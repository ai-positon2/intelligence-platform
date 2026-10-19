"""Paid Marketing Agents: Disapproved Ads Agent, what is saved.

One table of its own, `pm_da_findings` (a disapproved ad, asset group or asset in one account: where, why, its status, when it was first and last seen and when
it was alerted). Its small named values (the alert settings, the scan's progress and history) and its scan lock live in the Unknown Domains Alert's
`pm_dom_state` table under keys that begin with "da-" or "da_", so this file borrows that module's connection, its StoreUnavailable and its state functions.
The scan and the routes take any object with these functions, so tests run on a dict.
"""
from __future__ import annotations

import json
import threading

from . import paid_domains_store as base
from .paid_domains_store import StoreUnavailable, _iso, _ts, _tx, claim, release, state_get, state_set  # noqa: F401 (re-exported: the scan uses them as this store's)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_da_findings (
    fingerprint  TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    account_name TEXT NOT NULL DEFAULT '',
    place        TEXT NOT NULL DEFAULT '',
    object_id    TEXT NOT NULL DEFAULT '',
    object_name  TEXT NOT NULL DEFAULT '',
    ad_type      TEXT NOT NULL DEFAULT '',
    campaign     TEXT NOT NULL DEFAULT '',
    ad_group     TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    severity     TEXT NOT NULL DEFAULT 'high',
    reason       TEXT NOT NULL DEFAULT '',
    topics       JSONB NOT NULL DEFAULT '[]'::jsonb,
    status       TEXT NOT NULL DEFAULT 'open',
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    alerted_at   TIMESTAMPTZ,
    alert        JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS pm_da_findings_status ON pm_da_findings (status, last_seen DESC);
"""

COLS = ("fingerprint", "platform", "account_id", "account_name", "place", "object_id", "object_name", "ad_type", "campaign", "ad_group", "url", "severity", "reason",
        "topics", "status", "first_seen", "last_seen", "alerted_at", "alert", "decided_by", "decided_at")
KEYS = ("fingerprint", "platform", "accountId", "accountName", "where", "objectId", "objectName", "adType", "campaign", "adGroup", "url", "severity", "reason",
        "topics", "status", "firstSeen", "lastSeen", "alertedAt", "alert", "decidedBy", "decidedAt")
_TIMES = ("firstSeen", "lastSeen", "alertedAt", "decidedAt")
_READY = False
_LOCK = threading.Lock()


def ensure_tables():
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        base.ensure_tables()
        conn = base.paid_store._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _READY = True


def _row(r):
    d = dict(zip(KEYS, r))
    for k in _TIMES:
        d[k] = _iso(d[k])
    d["topics"], d["alert"] = d["topics"] or [], d["alert"] or {}
    return d


def findings_all():
    ensure_tables()
    with _tx() as cur:
        cur.execute("SELECT " + ", ".join(COLS) + " FROM pm_da_findings ORDER BY last_seen DESC")
        return [_row(r) for r in cur.fetchall()]


def finding_get(fingerprint):
    ensure_tables()
    with _tx() as cur:
        cur.execute("SELECT " + ", ".join(COLS) + " FROM pm_da_findings WHERE fingerprint = %s", (fingerprint,))
        row = cur.fetchone()
    return _row(row) if row else None


def findings_put(rows):
    if not rows:
        return
    ensure_tables()
    with _tx() as cur:
        for f in rows:
            vals = []
            for k in KEYS:
                v = f.get(k)
                if k == "topics":
                    v = json.dumps(v or [])
                elif k == "alert":
                    v = json.dumps(v or {})
                elif k in _TIMES:
                    v = _ts(v)
                vals.append(v)
            cur.execute("INSERT INTO pm_da_findings (" + ", ".join(COLS) + ") VALUES (" + ", ".join(["%s"] * len(COLS)) + ") "
                        "ON CONFLICT (fingerprint) DO UPDATE SET " + ", ".join("%s = EXCLUDED.%s" % (c, c) for c in COLS[1:]), vals)
