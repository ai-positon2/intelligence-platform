"""Paid Marketing Agents: Unknown Domains Alert, what is saved.

Three tables in the platform's Postgres, prefixed pm_dom_ like the rest of Paid Marketing's:

  pm_dom_known      the domain database: a domain and who may send people to it (one account, an MCC group, or everyone ("*")), who added it and when
  pm_dom_findings   every address the scans found that is not approved: where it is, why it is flagged, its status (open, approved, dismissed, gone), when it
                    was first and last seen and when it was alerted
  pm_dom_state      small named values: the alert settings, the last scan's progress and the domains in use (for the baseline), the list of recent scans,
                    and the lock that keeps two scans from running at once

The rules (what a finding is, when one is new, what the alert says) are tracker/paid_domains.py and tracker/paid_domains_scan.py; this file only reads and
writes. It borrows the connection from tracker/paid_store.py and raises its StoreUnavailable, so the routes handle both the same way. The scan and the routes
take any object with these functions, so tests run on a dict.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime

from . import paid_store

StoreUnavailable = paid_store.StoreUnavailable

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_dom_known (
    id        BIGSERIAL PRIMARY KEY,
    domain    TEXT NOT NULL,
    scope     TEXT NOT NULL DEFAULT '*',
    note      TEXT NOT NULL DEFAULT '',
    added_by  TEXT,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain, scope)
);
CREATE TABLE IF NOT EXISTS pm_dom_findings (
    fingerprint  TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    severity     TEXT NOT NULL,
    platform     TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    account_name TEXT NOT NULL DEFAULT '',
    place        TEXT NOT NULL DEFAULT '',
    object_id    TEXT NOT NULL DEFAULT '',
    object_name  TEXT NOT NULL DEFAULT '',
    campaign     TEXT NOT NULL DEFAULT '',
    ad_group     TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    host         TEXT NOT NULL DEFAULT '',
    domain       TEXT NOT NULL DEFAULT '',
    redirect     BOOLEAN NOT NULL DEFAULT false,
    lookalike    TEXT,
    owners       JSONB NOT NULL DEFAULT '[]'::jsonb,
    why          TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open',
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    alerted_at   TIMESTAMPTZ,
    alert        JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS pm_dom_findings_status ON pm_dom_findings (status, last_seen DESC);
CREATE TABLE IF NOT EXISTS pm_dom_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_COLS = ("fingerprint", "kind", "severity", "platform", "account_id", "account_name", "place", "object_id", "object_name", "campaign", "ad_group", "url", "host",
         "domain", "redirect", "lookalike", "owners", "why", "status", "first_seen", "last_seen", "alerted_at", "alert", "decided_by", "decided_at")
# The same columns as the keys a finding has in tracker/paid_domains.py (camelCase there, snake_case here).
_KEYS = ("fingerprint", "kind", "severity", "platform", "accountId", "accountName", "where", "objectId", "objectName", "campaign", "adGroup", "url", "host",
         "domain", "redirect", "lookalike", "owners", "why", "status", "firstSeen", "lastSeen", "alertedAt", "alert", "decidedBy", "decidedAt")
_TIMES = ("firstSeen", "lastSeen", "alertedAt", "decidedAt")
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


def _ts(v):
    """A stored timestamp from an ISO string (or None)."""
    return datetime.fromisoformat(v) if isinstance(v, str) and v else None


# ── The domain database ─────────────────────────────────────────────────────

def _known(r):
    return {"id": r[0], "domain": r[1], "scope": r[2], "note": r[3] or "", "added_by": r[4], "added_at": _iso(r[5])}


def known_list():
    with _tx() as cur:
        cur.execute("SELECT id, domain, scope, note, added_by, added_at FROM pm_dom_known ORDER BY domain, scope")
        return [_known(r) for r in cur.fetchall()]


def known_add(domain, scope, note, by):
    """(row, created). Adding what is already there leaves it as it is."""
    with _tx() as cur:
        cur.execute("INSERT INTO pm_dom_known (domain, scope, note, added_by) VALUES (%s, %s, %s, %s) ON CONFLICT (domain, scope) DO NOTHING "
                    "RETURNING id, domain, scope, note, added_by, added_at", (domain, scope, note, by))
        row = cur.fetchone()
        if row:
            return _known(row), True
        cur.execute("SELECT id, domain, scope, note, added_by, added_at FROM pm_dom_known WHERE domain = %s AND scope = %s", (domain, scope))
        return _known(cur.fetchone()), False


def known_bulk(rows, by):
    """{added, existing} for [{domain, scope, note}]."""
    added = existing = 0
    with _tx() as cur:
        for r in rows:
            cur.execute("INSERT INTO pm_dom_known (domain, scope, note, added_by) VALUES (%s, %s, %s, %s) ON CONFLICT (domain, scope) DO NOTHING RETURNING id",
                        (r["domain"], r["scope"], r.get("note") or "", by))
            if cur.fetchone():
                added += 1
            else:
                existing += 1
    return {"added": added, "existing": existing}


def known_remove(entry_id):
    with _tx() as cur:
        cur.execute("DELETE FROM pm_dom_known WHERE id = %s RETURNING id", (entry_id,))
        return cur.fetchone() is not None


# ── Findings ────────────────────────────────────────────────────────────────

def _finding(r):
    d = dict(zip(_KEYS, r))
    for k in _TIMES:
        d[k] = _iso(d[k])
    d["owners"], d["alert"] = d["owners"] or [], d["alert"] or {}
    return d


def findings_all():
    with _tx() as cur:
        cur.execute("SELECT " + ", ".join(_COLS) + " FROM pm_dom_findings ORDER BY last_seen DESC")
        return [_finding(r) for r in cur.fetchall()]


def finding_get(fingerprint):
    with _tx() as cur:
        cur.execute("SELECT " + ", ".join(_COLS) + " FROM pm_dom_findings WHERE fingerprint = %s", (fingerprint,))
        row = cur.fetchone()
    return _finding(row) if row else None


def findings_put(rows):
    """Save findings, replacing a row with the same fingerprint."""
    if not rows:
        return
    with _tx() as cur:
        for f in rows:
            vals = []
            for k in _KEYS:
                v = f.get(k)
                if k == "owners":
                    v = json.dumps(v or [])
                elif k == "alert":
                    v = json.dumps(v or {})
                elif k in _TIMES:
                    v = _ts(v)
                vals.append(v)
            cur.execute("INSERT INTO pm_dom_findings (" + ", ".join(_COLS) + ") VALUES (" + ", ".join(["%s"] * len(_COLS)) + ") "
                        "ON CONFLICT (fingerprint) DO UPDATE SET " + ", ".join("%s = EXCLUDED.%s" % (c, c) for c in _COLS[1:]), vals)


# ── Named values and the scan lock ──────────────────────────────────────────

def state_get(key, default=None):
    with _tx() as cur:
        cur.execute("SELECT value FROM pm_dom_state WHERE key = %s", (key,))
        row = cur.fetchone()
    return default if row is None else row[0]


def state_set(key, value):
    with _tx() as cur:
        cur.execute("INSERT INTO pm_dom_state (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                    (key, json.dumps(value)))


def claim(key, ttl_s):
    """True when this caller now holds the lock `key`: nobody does, or the holder's claim is older than ttl_s (it died with its process)."""
    with _tx() as cur:
        cur.execute("INSERT INTO pm_dom_state (key, value) VALUES (%s, '{\"locked\": true}'::jsonb) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now() "
                    "WHERE pm_dom_state.updated_at < now() - make_interval(secs => %s) OR NOT COALESCE((pm_dom_state.value->>'locked')::boolean, false) RETURNING key",
                    (key, ttl_s))
        return cur.fetchone() is not None


def release(key):
    with _tx() as cur:
        cur.execute("UPDATE pm_dom_state SET value = '{\"locked\": false}'::jsonb, updated_at = now() WHERE key = %s", (key,))
