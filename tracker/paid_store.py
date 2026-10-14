"""Paid Marketing Agents: everything the three agents keep in Postgres.

Ported from Agent Holmes (github.com/ai-positon2/Agent_Holmes, server/db.js),
which ran these agents as a separate Node service on Railway with no sign-in.
Here they sit behind the platform's Position2 login, and the tables carry a
pm_ prefix so they never collide with that service's own tables.

  pm_pacing_snapshots   one row per day's Pacing Desk run: its portfolio
                        rollup (meta), as pushed by the engine
  pm_pacing_clients     that day's per-client detail (accounts, campaigns,
                        categories, moves)
  pm_pacing_meta        key/value; 'latest' -> {"date": ...}, 'refresh' -> the
                        Refresh button's current or last run
  pm_pacing_moves       the shared "applied" tick on a recommended move, and
                        who ticked it
  pm_oauth_tokens       the ONE shared Slack grant and ONE shared Google grant
                        the Client Feedback Agent reads with, encrypted
  pm_gads_cache         Google Ads results the Performance Summarizer already
                        fetched (tracker/paid_gads.py), so a repeat view costs
                        no API request
  pm_gads_days          Google Ads campaign numbers one day at a time, kept
                        until Refresh replaces them, so any date range is
                        built from days already stored and only days never
                        read (or old enough to have changed) are asked for
  pm_gads_usage         Google Ads requests made per day, against the
                        developer token's daily allowance
  pm_feedback_jobs      one Client Feedback Agent question: its live tool
                        steps while it runs, then its answer
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


class StoreUnavailable(RuntimeError):
    """DATABASE_URL is unset or Postgres refused the connection."""


_READY = False
_READY_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_pacing_snapshots (
    date        TEXT PRIMARY KEY,
    meta        JSONB NOT NULL,
    seeded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pm_pacing_clients (
    date  TEXT NOT NULL REFERENCES pm_pacing_snapshots(date) ON DELETE CASCADE,
    slug  TEXT NOT NULL,
    data  JSONB NOT NULL,
    PRIMARY KEY (date, slug)
);
CREATE TABLE IF NOT EXISTS pm_pacing_meta (
    key    TEXT PRIMARY KEY,
    value  JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS pm_pacing_moves (
    id             TEXT PRIMARY KEY,
    date           TEXT NOT NULL,
    loc            TEXT,
    from_campaign  TEXT,
    to_campaign    TEXT,
    amt            NUMERIC,
    applied        BOOLEAN NOT NULL DEFAULT false,
    applied_at     TEXT,
    applied_by     TEXT
);
CREATE INDEX IF NOT EXISTS pm_pacing_moves_date ON pm_pacing_moves (date);
CREATE TABLE IF NOT EXISTS pm_oauth_tokens (
    provider       TEXT PRIMARY KEY,
    access_token   TEXT NOT NULL,
    refresh_token  TEXT,
    expires_at     TIMESTAMPTZ,
    meta           JSONB,
    connected_by   TEXT,
    connected_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pm_gads_cache (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pm_gads_days (
    customer_id  TEXT NOT NULL,
    day          DATE NOT NULL,
    data         JSONB NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, day)
);
CREATE TABLE IF NOT EXISTS pm_gads_usage (
    day    TEXT PRIMARY KEY,
    calls  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pm_feedback_jobs (
    id           TEXT PRIMARY KEY,
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT NOT NULL,
    question     TEXT,
    steps        JSONB NOT NULL DEFAULT '[]'::jsonb,
    answer       TEXT,
    tools        JSONB NOT NULL DEFAULT '[]'::jsonb,
    error        TEXT,
    finished_at  TIMESTAMPTZ
);
"""

JOB_STATUSES = ("running", "done", "failed")


def _connect():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise StoreUnavailable("DATABASE_URL is not set")
    import psycopg2
    try:
        return psycopg2.connect(url, connect_timeout=8)
    except Exception as e:
        raise StoreUnavailable("Postgres connection failed: %s" % e) from e


def ensure_tables():
    global _READY
    if _READY:
        return
    with _READY_LOCK:
        if _READY:
            return
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _READY = True


@contextmanager
def _tx():
    """A cursor on a fresh connection, committed on success, rolled back on
    error."""
    ensure_tables()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc)


# ── Pacing Desk ─────────────────────────────────────────────────────────────

def save_snapshot(meta, clients, latest_date):
    """Replace one day's snapshot and point 'latest' at `latest_date`, in one
    transaction (Holmes' seedDay). `clients` is [(slug, data), ...]."""
    with _tx() as cur:
        cur.execute(
            "INSERT INTO pm_pacing_snapshots (date, meta, seeded_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (date) DO UPDATE SET meta = EXCLUDED.meta, seeded_at = now()",
            (meta["asOf"], json.dumps(meta)))
        cur.execute("DELETE FROM pm_pacing_clients WHERE date = %s", (meta["asOf"],))
        for slug, data in clients:
            cur.execute("INSERT INTO pm_pacing_clients (date, slug, data) VALUES (%s, %s, %s)",
                        (meta["asOf"], slug, json.dumps(data)))
        cur.execute(
            "INSERT INTO pm_pacing_meta (key, value) VALUES ('latest', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (json.dumps({"date": latest_date}),))


def list_snapshots():
    """Every recorded day's rollup, oldest first, each with its date as id."""
    with _tx() as cur:
        cur.execute("SELECT date, meta, seeded_at FROM pm_pacing_snapshots ORDER BY date ASC")
        rows = cur.fetchall()
    out = []
    for date, meta, seeded_at in rows:
        row = dict(meta or {})
        row["id"] = date
        row["seededAt"] = seeded_at.isoformat() if seeded_at else None
        out.append(row)
    return out


def get_snapshot(date):
    """{meta..., clients: [...]} for one day, or None."""
    with _tx() as cur:
        cur.execute("SELECT meta, seeded_at FROM pm_pacing_snapshots WHERE date = %s", (date,))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("SELECT data FROM pm_pacing_clients WHERE date = %s ORDER BY slug ASC", (date,))
        clients = [r[0] for r in cur.fetchall()]
    out = dict(row[0] or {})
    out["seededAt"] = row[1].isoformat() if row[1] else None
    out["clients"] = clients
    return out


def latest_date():
    with _tx() as cur:
        cur.execute("SELECT value FROM pm_pacing_meta WHERE key = 'latest'")
        row = cur.fetchone()
    return (row[0] or {}).get("date") if row else None


def latest_clients():
    """(date, [{name, accounts, offTarget}]) from the latest run, without
    shipping its campaign detail. (None, []) before the first push."""
    date = latest_date()
    if not date:
        return None, []
    with _tx() as cur:
        cur.execute(
            "SELECT data->>'name', "
            "jsonb_array_length(COALESCE(data->'accounts','[]'::jsonb)), "
            "(SELECT count(*) FROM jsonb_array_elements(COALESCE(data->'accounts','[]'::jsonb)) a "
            " WHERE a->>'landing' IN ('WILL OVERSPEND','WILL UNDERSPEND')) "
            "FROM pm_pacing_clients WHERE date = %s ORDER BY data->>'name'", (date,))
        rows = cur.fetchall()
    return date, [{"name": r[0], "accounts": int(r[1] or 0), "offTarget": int(r[2] or 0)} for r in rows if r[0]]


def claim_refresh(by, stale_s):
    """Start a Refresh unless one is already running. True when this caller
    now owns it. A run older than stale_s does not block: its worker is gone.
    The row is locked for the check, so two clicks on two workers cannot
    both win."""
    state = {"state": "running", "by": by, "startedAt": _now().isoformat(), "step": "Starting"}
    with _tx() as cur:
        cur.execute("INSERT INTO pm_pacing_meta (key, value) VALUES ('refresh', '{}'::jsonb) "
                    "ON CONFLICT (key) DO NOTHING")
        cur.execute("SELECT value FROM pm_pacing_meta WHERE key = 'refresh' FOR UPDATE")
        cur_state = cur.fetchone()[0] or {}
        if cur_state.get("state") == "running":
            try:
                started = datetime.fromisoformat(cur_state.get("startedAt") or "")
            except ValueError:
                started = None
            if started and (_now() - started).total_seconds() < stale_s:
                return False
        cur.execute("UPDATE pm_pacing_meta SET value = %s WHERE key = 'refresh'", (json.dumps(state),))
    return True


def update_refresh(**fields):
    """Merge fields into the Refresh state."""
    with _tx() as cur:
        cur.execute("UPDATE pm_pacing_meta SET value = value || %s::jsonb WHERE key = 'refresh'",
                    (json.dumps(fields),))


def get_refresh():
    with _tx() as cur:
        cur.execute("SELECT value FROM pm_pacing_meta WHERE key = 'refresh'")
        row = cur.fetchone()
    return (row[0] or {}) if row else {}


def list_moves(date):
    with _tx() as cur:
        cur.execute(
            "SELECT id, date, loc, from_campaign, to_campaign, amt, applied, applied_at, applied_by "
            "FROM pm_pacing_moves WHERE date = %s", (date,))
        rows = cur.fetchall()
    return [{"id": r[0], "date": r[1], "loc": r[2], "from": r[3], "to": r[4],
             "amt": float(r[5]) if r[5] is not None else None, "applied": bool(r[6]),
             "appliedAt": r[7], "appliedBy": r[8]} for r in rows]


def set_move(move_id, *, date, loc, from_campaign, to_campaign, amt, applied, by):
    """Tick or untick one move. Returns the stored row."""
    applied_at = _now().isoformat() if applied else None
    with _tx() as cur:
        cur.execute(
            "INSERT INTO pm_pacing_moves (id, date, loc, from_campaign, to_campaign, amt, applied, "
            "applied_at, applied_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET date = EXCLUDED.date, loc = EXCLUDED.loc, "
            "from_campaign = EXCLUDED.from_campaign, to_campaign = EXCLUDED.to_campaign, "
            "amt = EXCLUDED.amt, applied = EXCLUDED.applied, applied_at = EXCLUDED.applied_at, "
            "applied_by = EXCLUDED.applied_by",
            (move_id, date, loc, from_campaign, to_campaign, amt, bool(applied), applied_at,
             by if applied else None))
    return {"id": move_id, "date": date, "loc": loc, "from": from_campaign, "to": to_campaign,
            "amt": amt, "applied": bool(applied), "appliedAt": applied_at,
            "appliedBy": by if applied else None}


# ── Google Ads cache and request tally (Performance Summarizer) ────────────

def get_gads_cache(key, ttl_s):
    """The cached value for `key` if it is younger than ttl_s seconds, else None."""
    with _tx() as cur:
        cur.execute("SELECT value FROM pm_gads_cache WHERE key = %s "
                    "AND fetched_at > now() - %s * interval '1 second'", (key, ttl_s))
        row = cur.fetchone()
    return row[0] if row else None


def get_gads_entry(key, ttl_s):
    """(value, saved-at as ISO text) for `key` if it is younger than ttl_s seconds, else None."""
    with _tx() as cur:
        cur.execute("SELECT value, fetched_at FROM pm_gads_cache WHERE key = %s "
                    "AND fetched_at > now() - %s * interval '1 second'", (key, ttl_s))
        row = cur.fetchone()
    return (row[0], row[1].isoformat()) if row else None


def get_gads_days(customer_id, start, end):
    """{day (YYYY-MM-DD): (data, saved-at as ISO text)} for every stored day of the account from start to end."""
    with _tx() as cur:
        cur.execute("SELECT day, data, fetched_at FROM pm_gads_days WHERE customer_id = %s AND day BETWEEN %s AND %s",
                    (customer_id, start, end))
        rows = cur.fetchall()
    return {r[0].isoformat(): (r[1], r[2].isoformat()) for r in rows}


def put_gads_days(customer_id, days):
    """Store {day: data}, replacing any copy of the same day."""
    if not days:
        return
    with _tx() as cur:
        for day, data in days.items():
            cur.execute("INSERT INTO pm_gads_days (customer_id, day, data, fetched_at) VALUES (%s, %s, %s, now()) "
                        "ON CONFLICT (customer_id, day) DO UPDATE SET data = EXCLUDED.data, fetched_at = now()",
                        (customer_id, day, json.dumps(data)))


def get_gads_cache_any(key):
    """(value, saved-at as ISO text) for `key` whatever its age, or None. Served when Google cannot answer."""
    with _tx() as cur:
        cur.execute("SELECT value, fetched_at FROM pm_gads_cache WHERE key = %s", (key,))
        row = cur.fetchone()
    return (row[0], row[1].isoformat()) if row else None


def get_gads_block():
    """Epoch seconds until which Google said not to ask again (0 if it did not)."""
    with _tx() as cur:
        cur.execute("SELECT value FROM pm_pacing_meta WHERE key = 'gads_block'")
        row = cur.fetchone()
    return float((row[0] or {}).get("until") or 0) if row else 0.0


def set_gads_block(until):
    with _tx() as cur:
        cur.execute("INSERT INTO pm_pacing_meta (key, value) VALUES ('gads_block', %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (json.dumps({"until": until}),))


def put_gads_cache(key, value):
    with _tx() as cur:
        cur.execute("INSERT INTO pm_gads_cache (key, value, fetched_at) VALUES (%s, %s, now()) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, fetched_at = now()",
                    (key, json.dumps(value)))
        # Old copies are only read when Google cannot answer, or when someone opens a range again.
        cur.execute("DELETE FROM pm_gads_cache WHERE fetched_at < now() - interval '90 days'")


def bump_gads_calls(day):
    """Add one request to `day`'s tally and return the new total."""
    with _tx() as cur:
        cur.execute("INSERT INTO pm_gads_usage (day, calls) VALUES (%s, 1) "
                    "ON CONFLICT (day) DO UPDATE SET calls = pm_gads_usage.calls + 1 RETURNING calls", (day,))
        return int(cur.fetchone()[0])


def gads_calls(day):
    with _tx() as cur:
        cur.execute("SELECT calls FROM pm_gads_usage WHERE day = %s", (day,))
        row = cur.fetchone()
    return int(row[0]) if row else 0


# ── The shared Slack / Google grant (encrypted at rest) ─────────────────────
#
# One row per provider. These tokens read a real person's Slack and Gmail, so
# they are never stored in the clear: Fernet, keyed from the platform's own
# SECRET_KEY. If SECRET_KEY is ever rotated the old rows simply stop
# decrypting, and the page asks an admin to connect again.

class TokenUnreadable(RuntimeError):
    pass


def _fernet():
    from cryptography.fernet import Fernet
    secret = os.environ.get("SECRET_KEY", "") or os.environ.get("FLASK_SECRET_KEY", "")
    if not secret:
        raise TokenUnreadable("SECRET_KEY is not set")
    key = hashlib.sha256(b"paid-marketing-oauth-tokens:" + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _seal(value):
    if value is None:
        return None
    return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def _open(value):
    if value is None:
        return None
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise TokenUnreadable("the stored token no longer decrypts (was SECRET_KEY changed?)") from e


def set_token(provider, *, access_token, refresh_token=None, expires_at=None, meta=None, by=None):
    """Store a grant. A refresh token the provider didn't resend keeps the
    one already stored (Google only sends it on first consent)."""
    with _tx() as cur:
        cur.execute(
            "INSERT INTO pm_oauth_tokens (provider, access_token, refresh_token, expires_at, meta, "
            "connected_by, connected_at) VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (provider) DO UPDATE SET access_token = EXCLUDED.access_token, "
            "refresh_token = COALESCE(EXCLUDED.refresh_token, pm_oauth_tokens.refresh_token), "
            "expires_at = EXCLUDED.expires_at, "
            "meta = COALESCE(EXCLUDED.meta, pm_oauth_tokens.meta), "
            "connected_by = COALESCE(EXCLUDED.connected_by, pm_oauth_tokens.connected_by), "
            "connected_at = CASE WHEN EXCLUDED.connected_by IS NULL "
            "THEN pm_oauth_tokens.connected_at ELSE now() END",
            (provider, _seal(access_token), _seal(refresh_token), expires_at,
             json.dumps(meta) if meta is not None else None, by))


def get_token(provider):
    """{access_token, refresh_token, expires_at, meta, connected_by,
    connected_at} or None. Raises TokenUnreadable if it no longer decrypts."""
    with _tx() as cur:
        cur.execute(
            "SELECT access_token, refresh_token, expires_at, meta, connected_by, connected_at "
            "FROM pm_oauth_tokens WHERE provider = %s", (provider,))
        row = cur.fetchone()
    if not row:
        return None
    return {"access_token": _open(row[0]), "refresh_token": _open(row[1]), "expires_at": row[2],
            "meta": row[3] or {}, "connected_by": row[4], "connected_at": row[5]}


def token_status(provider):
    """What the page shows, without decrypting anything: None, or
    {connected_by, connected_at, meta}."""
    with _tx() as cur:
        cur.execute("SELECT connected_by, connected_at, meta FROM pm_oauth_tokens WHERE provider = %s",
                    (provider,))
        row = cur.fetchone()
    if not row:
        return None
    return {"connected_by": row[0], "connected_at": row[1].isoformat() if row[1] else None,
            "meta": row[2] or {}}


def delete_token(provider):
    with _tx() as cur:
        cur.execute("DELETE FROM pm_oauth_tokens WHERE provider = %s", (provider,))


# ── Client Feedback Agent jobs ──────────────────────────────────────────────
#
# A question can take a minute or more (up to ten rounds of Slack / Drive /
# Gmail calls), longer than a web request may stay open, and the platform
# runs more than one worker process, so a question lives here: the worker
# that took it runs it in a thread and appends each step, and the page polls
# whichever worker answers.

def create_job(question, by):
    job_id = uuid.uuid4().hex
    with _tx() as cur:
        cur.execute("INSERT INTO pm_feedback_jobs (id, created_by, status, question) "
                    "VALUES (%s, %s, 'running', %s)", (job_id, by, question))
    return job_id


def add_step(job_id, step):
    with _tx() as cur:
        cur.execute("UPDATE pm_feedback_jobs SET steps = steps || %s::jsonb WHERE id = %s",
                    (json.dumps([step]), job_id))


def finish_job(job_id, *, answer=None, tools=None, error=None):
    status = "failed" if error else "done"
    with _tx() as cur:
        cur.execute("UPDATE pm_feedback_jobs SET status = %s, answer = %s, tools = %s, error = %s, "
                    "finished_at = now() WHERE id = %s",
                    (status, answer, json.dumps(tools or []), error, job_id))


def job_counts(by, stale_s):
    """(questions still running, questions asked in the last 24 hours) for
    one person. A job older than stale_s is not counted as running: its
    worker is gone."""
    with _tx() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE status = 'running' AND created_at > now() - %s * interval '1 second'), "
            "count(*) FILTER (WHERE created_at > now() - interval '1 day') "
            "FROM pm_feedback_jobs WHERE created_by = %s", (stale_s, by))
        running, today = cur.fetchone()
    return int(running or 0), int(today or 0)


def get_job(job_id):
    with _tx() as cur:
        cur.execute("SELECT id, created_by, created_at, status, question, steps, answer, tools, "
                    "error, finished_at FROM pm_feedback_jobs WHERE id = %s", (job_id,))
        r = cur.fetchone()
    if not r:
        return None
    return {"id": r[0], "createdBy": r[1], "createdAt": r[2].isoformat() if r[2] else None,
            "status": r[3], "question": r[4], "steps": r[5] or [], "answer": r[6],
            "tools": r[7] or [], "error": r[8],
            "finishedAt": r[9].isoformat() if r[9] else None}
