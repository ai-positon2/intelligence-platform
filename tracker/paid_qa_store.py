"""Paid Marketing Agents: QA Agent, what is saved.

Two tables, in the platform's Postgres, prefixed pm_ like the rest of Paid
Marketing's:

  pm_qc_launches   one launch QC: the account, what is launching, who started it
  pm_qc_checks     the ticks: one row per (launch, check), its status, the
                   reason for a Fail, and who set it and when

A check that has not been ticked has no row. Clearing a tick deletes its row.
The rules (what a row may hold, the verdict) are tracker/paid_qa.py; this file
only reads and writes. It borrows the connection from tracker/paid_store.py
and raises its StoreUnavailable, so the routes handle both the same way.
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
    name            TEXT NOT NULL,
    client_types    JSONB NOT NULL DEFAULT '[]'::jsonb,
    campaign_types  JSONB NOT NULL DEFAULT '[]'::jsonb,
    planned_date    TEXT NOT NULL DEFAULT '',
    qc_by           TEXT NOT NULL DEFAULT '',
    approved_by     TEXT NOT NULL DEFAULT '',
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived        BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS pm_qc_launches_account ON pm_qc_launches (account_id, created_at DESC);
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

_LAUNCH_COLS = ("id, account_id, account_name, name, client_types, campaign_types, planned_date, "
                "qc_by, approved_by, created_by, created_at, updated_at, archived")
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
    return {"id": r[0], "account_id": r[1], "account_name": r[2], "name": r[3],
            "client_types": r[4] or [], "campaign_types": r[5] or [], "planned_date": r[6] or "",
            "qc_by": r[7] or "", "approved_by": r[8] or "", "created_by": r[9],
            "created_at": _iso(r[10]), "updated_at": _iso(r[11]), "archived": bool(r[12])}


# ── Launches ────────────────────────────────────────────────────────────────

def create_launch(*, account_id, account_name, name, client_types, campaign_types, planned_date,
                  qc_by, approved_by, by):
    launch_id = uuid.uuid4().hex
    with _tx() as cur:
        cur.execute(
            "INSERT INTO pm_qc_launches (id, account_id, account_name, name, client_types, campaign_types, "
            "planned_date, qc_by, approved_by, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING " + _LAUNCH_COLS,
            (launch_id, account_id, account_name, name, json.dumps(client_types), json.dumps(campaign_types),
             planned_date, qc_by, approved_by, by))
        return _launch(cur.fetchone())


def get_launch(launch_id):
    with _tx() as cur:
        cur.execute("SELECT " + _LAUNCH_COLS + " FROM pm_qc_launches WHERE id = %s", (launch_id,))
        row = cur.fetchone()
    return _launch(row) if row else None


def list_launches(account_id, limit=40):
    """An account's launches, newest first, archived ones left out."""
    with _tx() as cur:
        cur.execute("SELECT " + _LAUNCH_COLS + " FROM pm_qc_launches WHERE account_id = %s AND NOT archived "
                    "ORDER BY created_at DESC LIMIT %s", (account_id, limit))
        return [_launch(r) for r in cur.fetchall()]


_UPDATABLE = {"name": None, "client_types": json.dumps, "campaign_types": json.dumps, "planned_date": None,
              "qc_by": None, "approved_by": None}


def update_launch(launch_id, **fields):
    """Change the launch's details. Only the fields given change; anything else
    is ignored. Returns the launch, or None if there is none."""
    sets, args = [], []
    for k, v in fields.items():
        if k in _UPDATABLE:
            sets.append("%s = %%s" % k)
            args.append(_UPDATABLE[k](v) if _UPDATABLE[k] else v)
    with _tx() as cur:
        cur.execute("UPDATE pm_qc_launches SET " + ", ".join(sets + ["updated_at = now()"]) +
                    " WHERE id = %s RETURNING " + _LAUNCH_COLS, tuple(args) + (launch_id,))
        row = cur.fetchone()
    return _launch(row) if row else None


def archive_launch(launch_id):
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
