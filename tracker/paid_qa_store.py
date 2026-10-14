"""Paid Marketing Agents: QA Agent, what is saved.

Two tables, in the platform's Postgres, prefixed pm_ like the rest of Paid
Marketing's:

  pm_qc_launches   one QC: an account and one of its campaigns, the client type
                   if one was chosen, and what the agent read from Google Ads
                   (the `auto` findings and when). One live QC per campaign.
  pm_qc_checks     what people clicked: one row per (QC, check), its status,
                   the comment, and who set it and when

A check nobody has answered has no row; clearing an answer deletes its row, and
the check shows the agent's reading again. The rules (what a row may hold, how
the two combine, the verdict) are tracker/paid_qa.py; this file only reads and
writes. It borrows the connection from tracker/paid_store.py and raises its
StoreUnavailable, so the routes handle both the same way.
"""
from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager

from . import paid_store

StoreUnavailable = paid_store.StoreUnavailable

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_qc_launches (
    id              TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    account_name    TEXT NOT NULL,
    campaign_id     TEXT NOT NULL,
    campaign_name   TEXT NOT NULL,
    channel         TEXT NOT NULL DEFAULT '',
    client_types    JSONB NOT NULL DEFAULT '[]'::jsonb,
    campaign_types  JSONB NOT NULL DEFAULT '[]'::jsonb,
    auto            JSONB NOT NULL DEFAULT '{}'::jsonb,
    auto_at         TIMESTAMPTZ,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived        BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS pm_qc_launches_account ON pm_qc_launches (account_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS pm_qc_launches_one_live ON pm_qc_launches (account_id, campaign_id) WHERE NOT archived;
CREATE TABLE IF NOT EXISTS pm_qc_checks (
    launch_id   TEXT NOT NULL REFERENCES pm_qc_launches(id) ON DELETE CASCADE,
    check_id    TEXT NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    checked_by  TEXT,
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (launch_id, check_id)
);
"""

_LAUNCH_COLS = ("id, account_id, account_name, campaign_id, campaign_name, channel, client_types, "
                "campaign_types, auto, auto_at, created_by, created_at, updated_at, archived")
_READY = False
_READY_LOCK = threading.Lock()


def ensure_tables():
    global _READY
    if _READY:
        return
    with _READY_LOCK:
        if _READY:
            return
        conn = paid_store._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _READY = True


@contextmanager
def _tx():
    ensure_tables()
    conn = paid_store._connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _iso(v):
    return v.isoformat() if v else None


def _launch(r):
    return {"id": r[0], "account_id": r[1], "account_name": r[2], "campaign_id": r[3], "campaign_name": r[4],
            "channel": r[5] or "", "client_types": r[6] or [], "campaign_types": r[7] or [], "auto": r[8] or {},
            "auto_at": _iso(r[9]), "created_by": r[10], "created_at": _iso(r[11]), "updated_at": _iso(r[12]),
            "archived": bool(r[13])}


# ── QCs ─────────────────────────────────────────────────────────────────────

def start_launch(*, account_id, account_name, campaign_id, campaign_name, channel, campaign_types, by):
    """The live QC for this campaign, started if there is none. Returns
    (launch, created). Two people opening the same campaign at once get the one
    QC: the unique index decides, not a check-then-insert."""
    with _tx() as cur:
        cur.execute(
            "INSERT INTO pm_qc_launches (id, account_id, account_name, campaign_id, campaign_name, channel, "
            "campaign_types, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (account_id, campaign_id) WHERE NOT archived DO NOTHING RETURNING id",
            (uuid.uuid4().hex, account_id, account_name, campaign_id, campaign_name, channel,
             json.dumps(campaign_types), by))
        created = cur.fetchone() is not None
        cur.execute("SELECT " + _LAUNCH_COLS + " FROM pm_qc_launches WHERE account_id = %s AND campaign_id = %s "
                    "AND NOT archived", (account_id, campaign_id))
        return _launch(cur.fetchone()), created


def get_launch(launch_id):
    with _tx() as cur:
        cur.execute("SELECT " + _LAUNCH_COLS + " FROM pm_qc_launches WHERE id = %s", (launch_id,))
        row = cur.fetchone()
    return _launch(row) if row else None


def list_launches(account_id, limit=200):
    """An account's live QCs, newest first."""
    with _tx() as cur:
        cur.execute("SELECT " + _LAUNCH_COLS + " FROM pm_qc_launches WHERE account_id = %s AND NOT archived "
                    "ORDER BY created_at DESC LIMIT %s", (account_id, limit))
        return [_launch(r) for r in cur.fetchall()]


def set_client_types(launch_id, client_types):
    with _tx() as cur:
        cur.execute("UPDATE pm_qc_launches SET client_types = %s, updated_at = now() WHERE id = %s AND NOT archived "
                    "RETURNING " + _LAUNCH_COLS, (json.dumps(client_types), launch_id))
        row = cur.fetchone()
    return _launch(row) if row else None


def save_auto(launch_id, findings, *, campaign_name, channel, campaign_types):
    """Keep what the agent read: the findings, when, and the campaign's own
    name and type as Google Ads reports them now."""
    with _tx() as cur:
        cur.execute("UPDATE pm_qc_launches SET auto = %s, auto_at = now(), campaign_name = %s, channel = %s, "
                    "campaign_types = %s, updated_at = now() WHERE id = %s AND NOT archived RETURNING " + _LAUNCH_COLS,
                    (json.dumps(findings), campaign_name, channel, json.dumps(campaign_types), launch_id))
        row = cur.fetchone()
    return _launch(row) if row else None


def archive_launch(launch_id):
    """Remove a QC from the lists. Its answers are kept, and the campaign can
    start a fresh QC."""
    with _tx() as cur:
        cur.execute("UPDATE pm_qc_launches SET archived = true, updated_at = now() WHERE id = %s", (launch_id,))
        return cur.rowcount > 0


# ── Ticks ───────────────────────────────────────────────────────────────────

def _check(r):
    return {"status": r[0], "note": r[1] or "", "by": r[2], "at": _iso(r[3])}


def checks_for(launch_ids):
    """{launch id: {check id: {status, note, by, at}}} for each launch asked."""
    launch_ids = list(launch_ids)
    out = {i: {} for i in launch_ids}
    if not launch_ids:
        return out
    with _tx() as cur:
        cur.execute("SELECT launch_id, check_id, status, note, checked_by, checked_at FROM pm_qc_checks "
                    "WHERE launch_id = ANY(%s)", (launch_ids,))
        for r in cur.fetchall():
            out[r[0]][r[1]] = _check(r[2:])
    return out


def set_check(launch_id, check_id, status, note, by):
    """Tick a check (status 'pass', 'fail' or 'na', with its comment). Returns
    what was stored, or None if there is no such launch."""
    with _tx() as cur:
        cur.execute("SELECT 1 FROM pm_qc_launches WHERE id = %s AND NOT archived", (launch_id,))
        if not cur.fetchone():
            return None
        cur.execute(
            "INSERT INTO pm_qc_checks (launch_id, check_id, status, note, checked_by, checked_at) "
            "VALUES (%s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (launch_id, check_id) DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note, "
            "checked_by = EXCLUDED.checked_by, checked_at = now() "
            "RETURNING status, note, checked_by, checked_at", (launch_id, check_id, status, note, by))
        stored = _check(cur.fetchone())
        cur.execute("UPDATE pm_qc_launches SET updated_at = now() WHERE id = %s", (launch_id,))
    return stored


def clear_check(launch_id, check_id):
    """Un-tick a check. Returns False if there is no such launch."""
    with _tx() as cur:
        cur.execute("SELECT 1 FROM pm_qc_launches WHERE id = %s AND NOT archived", (launch_id,))
        if not cur.fetchone():
            return False
        cur.execute("DELETE FROM pm_qc_checks WHERE launch_id = %s AND check_id = %s", (launch_id, check_id))
        cur.execute("UPDATE pm_qc_launches SET updated_at = now() WHERE id = %s", (launch_id,))
    return True
