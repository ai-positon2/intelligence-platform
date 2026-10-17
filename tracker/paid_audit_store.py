"""Paid Marketing Agents: Account Audit, the saved audits.

One table in the platform's Postgres, prefixed pm_ like the rest of Paid Marketing's:

  pm_audit_history   an audit someone chose to keep: the account it was for, who saved it and when, the score and every check's result
                     (tracker/paid_audit_history.py snapshot), and an optional note ("Onboarding", "Q1 review")

Nothing is saved unless a person asks, so the history is the audits they meant to compare. This file only reads and writes; what a snapshot
holds and how two are compared is tracker/paid_audit_history.py. It borrows the connection from tracker/paid_store.py and raises its
StoreUnavailable, so the routes handle it the same way as the other Paid Marketing stores.
"""
from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager

from . import paid_audit_history as history
from . import paid_store

StoreUnavailable = paid_store.StoreUnavailable
NOTE_CHARS = 120
MAX_KEPT = 60                                  # saved audits kept per account; saving past it drops the oldest

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_audit_history (
    id           TEXT PRIMARY KEY,
    account_key  TEXT NOT NULL,
    account_name TEXT NOT NULL DEFAULT '',
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    score        INTEGER,
    grade        TEXT,
    tally        JSONB NOT NULL DEFAULT '{}'::jsonb,
    critical     INTEGER NOT NULL DEFAULT 0,
    high         INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT '',
    results      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS pm_audit_history_account ON pm_audit_history (account_key, created_at DESC);
"""

_COLS = "id, account_key, account_name, created_by, created_at, score, grade, tally, critical, high, note"
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


def _row(r, results=None):
    out = {"id": r[0], "accountKey": r[1], "accountName": r[2], "createdBy": r[3], "createdAt": _iso(r[4]), "score": r[5], "grade": r[6], "tally": r[7] or {},
           "critical": r[8] or 0, "high": r[9] or 0, "note": r[10] or ""}
    if results is not None:
        out["results"] = results or {}
    return out


def clean_note(note):
    return " ".join(str(note or "").split())[:NOTE_CHARS]


def save(account_key, account_name, by, report, note=""):
    """Keeps this audit. Returns the saved row (without its results). The oldest past MAX_KEPT for the account are dropped."""
    snap = history.snapshot(report)
    sid = uuid.uuid4().hex
    with _tx() as cur:
        cur.execute("INSERT INTO pm_audit_history (id, account_key, account_name, created_by, score, grade, tally, critical, high, note, results) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING " + _COLS,
                    (sid, account_key, str(account_name or "")[:200], by, snap["score"], snap["grade"], json.dumps(snap["tally"]), snap["critical"], snap["high"],
                     clean_note(note), json.dumps(snap["results"])))
        row = _row(cur.fetchone())
        cur.execute("DELETE FROM pm_audit_history WHERE account_key = %s AND id NOT IN "
                    "(SELECT id FROM pm_audit_history WHERE account_key = %s ORDER BY created_at DESC, id DESC LIMIT %s)", (account_key, account_key, MAX_KEPT))
    return row


def list_saved(account_key, limit=30):
    """An account's saved audits, newest first, without their results."""
    with _tx() as cur:
        cur.execute("SELECT " + _COLS + " FROM pm_audit_history WHERE account_key = %s ORDER BY created_at DESC, id DESC LIMIT %s", (account_key, limit))
        return [_row(r) for r in cur.fetchall()]


def get(sid, account_key=None):
    """One saved audit with its results, or None (also None when it belongs to another account than `account_key`)."""
    with _tx() as cur:
        cur.execute("SELECT " + _COLS + ", results FROM pm_audit_history WHERE id = %s", (sid,))
        r = cur.fetchone()
    if not r or (account_key is not None and r[1] != account_key):
        return None
    return _row(r[:11], r[11])


def latest(account_key):
    with _tx() as cur:
        cur.execute("SELECT " + _COLS + ", results FROM pm_audit_history WHERE account_key = %s ORDER BY created_at DESC, id DESC LIMIT 1", (account_key,))
        r = cur.fetchone()
    return _row(r[:11], r[11]) if r else None


def delete(sid, by, is_admin=False):
    """Removes a saved audit. Whoever saved it, or an admin, may. True when a row was removed."""
    with _tx() as cur:
        cur.execute("DELETE FROM pm_audit_history WHERE id = %s AND (%s OR created_by = %s) RETURNING id", (sid, bool(is_admin), by))
        return cur.fetchone() is not None
