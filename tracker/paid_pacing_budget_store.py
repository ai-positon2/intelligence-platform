"""Paid Marketing Agents: Pacing Desk, the saved monthly budgets.

One table in the platform's Postgres, prefixed pm_ like the rest of Paid Marketing's:

  pm_pacing_budgets   what each account is meant to spend in a month: one row per month, client and account (what the Budget Tracker's
                      DPR tab used to hold), with who last changed it and when

This file only reads and writes; checking a row and laying the month out for the engine is tracker/paid_pacing_budgets.py. It borrows
the connection from tracker/paid_store.py and raises its StoreUnavailable, so the routes handle it like the other Paid Marketing stores.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from . import paid_pacing_budgets as budgets
from . import paid_store

StoreUnavailable = paid_store.StoreUnavailable

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_pacing_budgets (
    month        TEXT NOT NULL,
    ckey         TEXT NOT NULL,
    client       TEXT NOT NULL,
    account      TEXT NOT NULL,
    allocated    NUMERIC NOT NULL,
    target_cpa   NUMERIC,
    days_running TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT '',
    am           TEXT NOT NULL DEFAULT '',
    channel      TEXT NOT NULL DEFAULT 'Google',
    updated_by   TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (month, ckey)
);
ALTER TABLE pm_pacing_budgets ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'Google';
"""

_COLS = "client, account, allocated, target_cpa, days_running, status, am, updated_by, updated_at, channel"
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


def _row(r):
    return {"client": r[0], "account": r[1], "allocated": float(r[2]), "targetCpa": None if r[3] is None else float(r[3]),
            "daysRunning": r[4] or "", "status": r[5] or "", "am": r[6] or "", "updatedBy": r[7], "updatedAt": r[8].isoformat() if r[8] else None,
            "channel": r[9] or "Google"}


def _params(month, row, by):
    channel = row.get("channel") or "Google"
    return (month, budgets.row_key(row["client"], row["account"], channel), row["client"], row["account"], row["allocated"], row.get("targetCpa"),
            row.get("daysRunning") or "", row.get("status") or "", row.get("am") or "", channel, by)


_INSERT = ("INSERT INTO pm_pacing_budgets (month, ckey, client, account, allocated, target_cpa, days_running, status, am, channel, updated_by) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")


def list_month(month):
    """The month's rows, by client then account."""
    with _tx() as cur:
        cur.execute("SELECT " + _COLS + " FROM pm_pacing_budgets WHERE month = %s ORDER BY lower(client), lower(account)", (month,))
        return [_row(r) for r in cur.fetchall()]


def months():
    """Every month that has rows, oldest first."""
    with _tx() as cur:
        cur.execute("SELECT DISTINCT month FROM pm_pacing_budgets ORDER BY month")
        return [r[0] for r in cur.fetchall()]


def replace_month(month, rows, by):
    """Makes `rows` (already checked) the whole of the month, in one transaction. Returns the stored rows."""
    with _tx() as cur:
        cur.execute("DELETE FROM pm_pacing_budgets WHERE month = %s", (month,))
        for r in rows:
            cur.execute(_INSERT, _params(month, r, by))
    return list_month(month)


def save_row(month, row, by, replaces=None):
    """Adds or changes one row (already checked). `replaces` is the (client, account[, channel]) the row had before, when the person renamed it, so
    the old row does not stay behind. Returns the stored row."""
    with _tx() as cur:
        if replaces:
            cur.execute("DELETE FROM pm_pacing_budgets WHERE month = %s AND ckey = %s", (month, budgets.row_key(*replaces)))
        cur.execute(_INSERT + " ON CONFLICT (month, ckey) DO UPDATE SET client = EXCLUDED.client, account = EXCLUDED.account, "
                    "allocated = EXCLUDED.allocated, target_cpa = EXCLUDED.target_cpa, days_running = EXCLUDED.days_running, "
                    "status = EXCLUDED.status, am = EXCLUDED.am, channel = EXCLUDED.channel, updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING " + _COLS,
                    _params(month, row, by))
        return _row(cur.fetchone())


def delete_row(month, client, account, channel="Google"):
    """Removes one row. True when it existed."""
    with _tx() as cur:
        cur.execute("DELETE FROM pm_pacing_budgets WHERE month = %s AND ckey = %s RETURNING 1", (month, budgets.row_key(client, account, channel)))
        return cur.fetchone() is not None
