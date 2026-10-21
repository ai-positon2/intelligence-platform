"""Paid Marketing Agents: the monitors' shared store (Brand Spend Alert, Disapproved Ads Tracker, and the monitors to come).

Three tables in the platform's Postgres, prefixed pm_mon_ like the rest of Paid Marketing's:

  pm_mon_state   small named values: the alert recipients, each monitor's saved settings (the team's brand marks, agreed limits and brand words), the list of recent runs,
                 the baseline of each monitor and the lock that keeps two runs of one monitor from running at once
  pm_mon_issues  every problem a monitor has raised, one row per fingerprint: what and where it is, its level, when it was first and last seen, when it was resolved
                 (and how many times it came back), and when and how it was alerted. A row is kept after it is resolved: that is the memory the monitors' "new",
                 "how long open", "fixed in" and "keeps coming back" are made from
  pm_mon_snaps   one small row per monitor, account and day: the figures of that day's read (status, spend against usual, stopped ad groups...) for the history

The rules (what an issue is, when it is new, what an alert says) are tracker/paid_monitor.py and each monitor's own module; this file only reads and writes. It borrows the
connection from tracker/paid_store.py and raises its StoreUnavailable, so the routes handle it like the other Paid Marketing stores. The monitor engine takes any object
with these functions, so tests run on a dict (tests/monitor_fakes.py) and, with TEST_DATABASE_URL set, against a real Postgres.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import paid_store

StoreUnavailable = paid_store.StoreUnavailable

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_mon_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pm_mon_issues (
    agent        TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    account_name TEXT NOT NULL DEFAULT '',
    platform     TEXT NOT NULL DEFAULT 'google',
    kind         TEXT NOT NULL DEFAULT '',
    level        TEXT NOT NULL DEFAULT 'medium',
    title        TEXT NOT NULL DEFAULT '',
    detail       TEXT NOT NULL DEFAULT '',
    next_step    TEXT NOT NULL DEFAULT '',
    data         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status       TEXT NOT NULL DEFAULT 'open',
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ,
    reopened     INTEGER NOT NULL DEFAULT 0,
    alerted_at   TIMESTAMPTZ,
    alerted_level TEXT,
    alert        JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (agent, fingerprint)
);
CREATE INDEX IF NOT EXISTS pm_mon_issues_open ON pm_mon_issues (agent, status, last_seen DESC);
CREATE TABLE IF NOT EXISTS pm_mon_snaps (
    agent      TEXT NOT NULL,
    account_id TEXT NOT NULL,
    day        DATE NOT NULL,
    data       JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (agent, account_id, day)
);
"""

_COLS = ("agent", "fingerprint", "account_id", "account_name", "platform", "kind", "level", "title", "detail", "next_step", "data", "status", "first_seen", "last_seen",
         "resolved_at", "reopened", "alerted_at", "alerted_level", "alert")
# The same columns as the keys an issue has in tracker/paid_monitor.py.
_KEYS = ("agent", "fingerprint", "accountId", "accountName", "platform", "kind", "level", "title", "detail", "nextStep", "data", "status", "firstSeen", "lastSeen",
         "resolvedAt", "reopened", "alertedAt", "alertedLevel", "alert")
_TIMES = ("firstSeen", "lastSeen", "resolvedAt", "alertedAt")
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
    """A stored time as UTC ISO text, whatever time zone the database session is in."""
    return v.astimezone(timezone.utc).isoformat() if v else None


def _ts(v):
    """A stored timestamp from an ISO string (or None)."""
    return datetime.fromisoformat(v) if isinstance(v, str) and v else None


# ── Named values and the lock ───────────────────────────────────────────────

def state_get(key, default=None):
    with _tx() as cur:
        cur.execute("SELECT value FROM pm_mon_state WHERE key = %s", (key,))
        row = cur.fetchone()
    return default if row is None else row[0]


def state_set(key, value):
    with _tx() as cur:
        cur.execute("INSERT INTO pm_mon_state (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (key, json.dumps(value)))


def claim(key, ttl_s):
    """True when this caller now holds the lock `key`: nobody does, or the holder's claim is older than ttl_s (it died with its process)."""
    with _tx() as cur:
        cur.execute("INSERT INTO pm_mon_state (key, value) VALUES (%s, '{\"locked\": true}'::jsonb) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now() "
                    "WHERE pm_mon_state.updated_at < now() - make_interval(secs => %s) OR NOT COALESCE((pm_mon_state.value->>'locked')::boolean, false) RETURNING key",
                    (key, ttl_s))
        return cur.fetchone() is not None


def release(key):
    with _tx() as cur:
        cur.execute("UPDATE pm_mon_state SET value = '{\"locked\": false}'::jsonb, updated_at = now() WHERE key = %s", (key,))


# ── Issues ──────────────────────────────────────────────────────────────────

def _issue(r):
    d = dict(zip(_KEYS, r))
    for k in _TIMES:
        d[k] = _iso(d[k])
    d["data"], d["alert"] = d["data"] or {}, d["alert"] or {}
    return d


def issues_all(agent):
    with _tx() as cur:
        cur.execute("SELECT " + ", ".join(_COLS) + " FROM pm_mon_issues WHERE agent = %s ORDER BY last_seen DESC", (agent,))
        return [_issue(r) for r in cur.fetchall()]


def issues_put(rows):
    """Save issues, replacing a row with the same agent and fingerprint."""
    if not rows:
        return
    with _tx() as cur:
        for f in rows:
            vals = []
            for k in _KEYS:
                v = f.get(k)
                if k in ("data", "alert"):
                    v = json.dumps(v or {})
                elif k in _TIMES:
                    v = _ts(v)
                elif k == "reopened":
                    v = int(v or 0)
                vals.append(v)
            cur.execute("INSERT INTO pm_mon_issues (" + ", ".join(_COLS) + ") VALUES (" + ", ".join(["%s"] * len(_COLS)) + ") "
                        "ON CONFLICT (agent, fingerprint) DO UPDATE SET " + ", ".join("%s = EXCLUDED.%s" % (c, c) for c in _COLS[2:]), vals)


# ── Daily snapshots ─────────────────────────────────────────────────────────

def snaps_put(agent, day, rows):
    """Save one day's figures: rows is {account id: {...}}; a second run on the same day replaces the first."""
    if not rows:
        return
    with _tx() as cur:
        for aid, data in rows.items():
            cur.execute("INSERT INTO pm_mon_snaps (agent, account_id, day, data) VALUES (%s, %s, %s, %s) ON CONFLICT (agent, account_id, day) DO UPDATE SET data = EXCLUDED.data",
                        (agent, aid, day, json.dumps(data)))


def snaps_get(agent, account_id, since):
    """[{day (ISO date), ...the day's figures}] for an account from `since` (an ISO date) on, oldest first."""
    with _tx() as cur:
        cur.execute("SELECT day, data FROM pm_mon_snaps WHERE agent = %s AND account_id = %s AND day >= %s ORDER BY day", (agent, account_id, since))
        return [dict(r[1] or {}, day=r[0].isoformat()) for r in cur.fetchall()]


def snaps_prune(agent, before):
    """Forget the days before `before` (an ISO date)."""
    with _tx() as cur:
        cur.execute("DELETE FROM pm_mon_snaps WHERE agent = %s AND day < %s", (agent, before))
