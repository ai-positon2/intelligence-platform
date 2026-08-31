"""Postgres storage for Event & Conference Intelligence runs.

Same shape as tracker/sci_store.py and tracker/linkedin_playbook_store.py: a
standalone _pg_conn() (Railway gives this app no persistent disk, so a run a
user just started must survive the next deploy), lazy CREATE TABLE IF NOT
EXISTS, and every single-row read ownership-scoped in the SQL itself
(`WHERE id = %s AND email = %s`) rather than fetched-then-checked in Python.

Four tables:

  evi_runs          one row per request (lookup or discover).
  evi_events        one row per event a run resolved. `discover` mode
                    resolves many; `lookup` resolves one. Splitting this off
                    evi_runs is what lets both modes share one harvest path.
  evi_participants  one row per published participant, carrying the ROLE it
                    was published under and the URL it came from.
  evi_sources       one row per page the harvester tried, INCLUDING the ones
                    it could not read.

That last table is the point of the whole schema. An event roster assembled
from four of seven published pages, with three silently dropped, looks
identical to a complete one: shorter. Recording every attempt with its
outcome is what lets the report say "3 sources could not be read" instead of
quietly understating an event. Cf. the standing lesson that an empty result
must read as a fact about the request, not a fact about the world.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TABLES_READY = False

# Roles a participant row can carry. Deliberately explicit rather than free
# text, because the entire honesty contract of this agent rests on never
# letting an exhibitor be rendered under the word "attendee".
ROLE_EXHIBITOR = "exhibitor"
ROLE_SPONSOR = "sponsor"
ROLE_SPEAKER = "speaker"
ROLE_PARTNER = "partner"
ROLE_MEDIA = "media"
ROLE_ATTENDEE_DECLARED = "attendee_declared"
ROLES = (ROLE_EXHIBITOR, ROLE_SPONSOR, ROLE_SPEAKER, ROLE_PARTNER,
         ROLE_MEDIA, ROLE_ATTENDEE_DECLARED)

# Human wording per role, used by the report and by the export. "Attendee"
# appears for exactly one role, and that role only ever comes from a person
# publicly saying they are going.
ROLE_LABELS = {
    ROLE_EXHIBITOR: "Exhibitor",
    ROLE_SPONSOR: "Sponsor",
    ROLE_SPEAKER: "Speaker",
    ROLE_PARTNER: "Partner",
    ROLE_MEDIA: "Media",
    ROLE_ATTENDEE_DECLARED: "Publicly said they are attending",
}

SOURCE_OK = "ok"
SOURCE_BLOCKED = "blocked"
SOURCE_NOT_FOUND = "not_found"
SOURCE_ERROR = "error"


def _pg_conn():
    """One-off Postgres connection. None if DATABASE_URL is unset or the
    connection fails; callers treat that as 'not available'."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        return None
    try:
        import psycopg2
        return psycopg2.connect(database_url, connect_timeout=8)
    except Exception as e:
        logger.warning("event_intel_store: Postgres connection failed: %s", e)
        return None


def _ensure_tables(conn) -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_runs (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                mode VARCHAR(16) NOT NULL DEFAULT 'lookup',
                query TEXT NOT NULL,
                icp_note TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                stage VARCHAR(32),
                error TEXT,
                summary JSONB,
                credits_spent INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evi_runs_email
            ON evi_runs (email, created_at DESC)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_events (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES evi_runs(id),
                name TEXT NOT NULL,
                edition TEXT,
                website TEXT,
                organizer TEXT,
                starts_on DATE,
                ends_on DATE,
                location TEXT,
                venue TEXT,
                format VARCHAR(16),
                audience_note TEXT,
                stated_size TEXT,
                confidence VARCHAR(10),
                reasoning TEXT,
                fit_score INTEGER,
                fit_reasoning TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evi_events_run ON evi_events (run_id)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_participants (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES evi_runs(id),
                event_id INTEGER REFERENCES evi_events(id),
                org_name TEXT NOT NULL,
                org_domain TEXT,
                role VARCHAR(24) NOT NULL,
                tier TEXT,
                person_name TEXT,
                person_title TEXT,
                booth TEXT,
                note TEXT,
                source_url TEXT NOT NULL,
                fetched_at TIMESTAMPTZ,
                resolution VARCHAR(24) NOT NULL DEFAULT 'unresolved',
                apollo JSONB,
                icp_score INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evi_participants_run
            ON evi_participants (run_id, role)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_sources (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES evi_runs(id),
                event_id INTEGER REFERENCES evi_events(id),
                url TEXT NOT NULL,
                kind VARCHAR(24),
                status VARCHAR(16) NOT NULL,
                http_status INTEGER,
                rows_found INTEGER NOT NULL DEFAULT 0,
                note TEXT,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evi_sources_run ON evi_sources (run_id)
        """)
    conn.commit()
    _TABLES_READY = True


def _ts(d: dict, *keys: str) -> None:
    """ISO-format timestamp/date columns in place so jsonify never chokes."""
    for k in keys:
        v = d.get(k)
        if v is not None and hasattr(v, "isoformat"):
            d[k] = v.isoformat()


# ── runs ──────────────────────────────────────────────────────────────────

def save_run(email: str, mode: str, query: str, icp_note: str | None = None) -> int | None:
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evi_runs (email, mode, query, icp_note) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (email, mode, query, icp_note))
            run_id = cur.fetchone()[0]
        conn.commit()
        return run_id
    except Exception as e:
        logger.warning("event_intel_store.save_run failed: %s", e)
        return None
    finally:
        conn.close()


def update_run(run_id: int, **fields: Any) -> None:
    """Patch a run. `summary` is JSON-encoded here so callers pass a dict."""
    if not fields:
        return
    conn = _pg_conn()
    if conn is None:
        return
    allowed = ("status", "stage", "error", "summary", "credits_spent")
    sets, vals = [], []
    for k in allowed:
        if k in fields:
            v = fields[k]
            if k == "summary" and v is not None and not isinstance(v, str):
                v = json.dumps(v)
            sets.append("%s = %%s" % k)
            vals.append(v)
    if not sets:
        conn.close()
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE evi_runs SET %s, updated_at = now() WHERE id = %%s"
                % ", ".join(sets), (*vals, run_id))
        conn.commit()
    except Exception as e:
        logger.warning("event_intel_store.update_run failed: %s", e)
    finally:
        conn.close()


def add_credits(run_id: int, n: int) -> None:
    """Accumulate Apollo credits against a run. Separate from update_run so a
    concurrent stage cannot clobber another's spend with a stale read."""
    if not n:
        return
    conn = _pg_conn()
    if conn is None:
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE evi_runs SET credits_spent = credits_spent + %s, "
                        "updated_at = now() WHERE id = %s", (n, run_id))
        conn.commit()
    except Exception as e:
        logger.warning("event_intel_store.add_credits failed: %s", e)
    finally:
        conn.close()


def get_run(run_id: int, email: str) -> dict | None:
    """Ownership scoped in the SQL, never fetched-then-checked."""
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, mode, query, icp_note, status, stage, error, "
                "summary, credits_spent, created_at, updated_at "
                "FROM evi_runs WHERE id = %s AND email = %s", (run_id, email))
            row = cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
        out = dict(zip(cols, row))
        _ts(out, "created_at", "updated_at")
        return out
    except Exception as e:
        logger.warning("event_intel_store.get_run failed: %s", e)
        return None
    finally:
        conn.close()


def list_runs(email: str, limit: int = 60) -> list[dict]:
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT r.id, r.mode, r.query, r.status, r.created_at, r.credits_spent, "
                "  (SELECT count(*) FROM evi_participants p WHERE p.run_id = r.id) AS participant_count, "
                "  (SELECT e.name FROM evi_events e WHERE e.run_id = r.id ORDER BY e.id LIMIT 1) AS event_name "
                "FROM evi_runs r WHERE r.email = %s "
                "ORDER BY r.created_at DESC LIMIT %s", (email, limit))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            _ts(r, "created_at")
        return rows
    except Exception as e:
        logger.warning("event_intel_store.list_runs failed: %s", e)
        return []
    finally:
        conn.close()


# ── events ────────────────────────────────────────────────────────────────

_EVENT_FIELDS = ("name", "edition", "website", "organizer", "starts_on", "ends_on",
                 "location", "venue", "format", "audience_note", "stated_size",
                 "confidence", "reasoning", "fit_score", "fit_reasoning")


def save_event(run_id: int, event: dict) -> int | None:
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        cols = ["run_id"]
        vals: list[Any] = [run_id]
        for f in _EVENT_FIELDS:
            if event.get(f) not in (None, ""):
                cols.append(f)
                vals.append(event[f])
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evi_events (%s) VALUES (%s) RETURNING id"
                % (", ".join(cols), ", ".join(["%s"] * len(cols))), vals)
            event_id = cur.fetchone()[0]
        conn.commit()
        return event_id
    except Exception as e:
        logger.warning("event_intel_store.save_event failed: %s", e)
        return None
    finally:
        conn.close()


def get_events(run_id: int) -> list[dict]:
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, edition, website, organizer, starts_on, ends_on, "
                "location, venue, format, audience_note, stated_size, confidence, "
                "reasoning, fit_score, fit_reasoning FROM evi_events "
                "WHERE run_id = %s ORDER BY fit_score DESC NULLS LAST, id", (run_id,))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            _ts(r, "starts_on", "ends_on")
        return rows
    except Exception as e:
        logger.warning("event_intel_store.get_events failed: %s", e)
        return []
    finally:
        conn.close()


# ── participants ──────────────────────────────────────────────────────────

def save_participants(run_id: int, event_id: int | None, rows: list[dict]) -> int:
    """Bulk insert. Returns how many landed. Rows carrying an unknown role are
    dropped rather than coerced: a participant whose role we cannot name
    cannot be rendered honestly, and guessing 'exhibitor' would be exactly the
    fabrication this agent exists to avoid."""
    if not rows:
        return 0
    conn = _pg_conn()
    if conn is None:
        return 0
    try:
        _ensure_tables(conn)
        payload = []
        for r in rows:
            role = (r.get("role") or "").strip().lower()
            name = (r.get("org_name") or "").strip()
            src = (r.get("source_url") or "").strip()
            if role not in ROLES or not name or not src:
                logger.info("event_intel_store: dropped a participant row "
                            "(role=%r, org=%r, src=%r)", role, name[:60], src[:80])
                continue
            payload.append((
                run_id, event_id, name, (r.get("org_domain") or None), role,
                (r.get("tier") or None), (r.get("person_name") or None),
                (r.get("person_title") or None), (r.get("booth") or None),
                (r.get("note") or None), src, r.get("fetched_at"),
                r.get("resolution") or "unresolved",
                json.dumps(r["apollo"]) if r.get("apollo") else None,
                r.get("icp_score")))
        if not payload:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO evi_participants (run_id, event_id, org_name, org_domain, "
                "role, tier, person_name, person_title, booth, note, source_url, "
                "fetched_at, resolution, apollo, icp_score) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", payload)
        conn.commit()
        return len(payload)
    except Exception as e:
        logger.warning("event_intel_store.save_participants failed: %s", e)
        return 0
    finally:
        conn.close()


def get_participants(run_id: int, role: str | None = None) -> list[dict]:
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        sql = ("SELECT id, event_id, org_name, org_domain, role, tier, person_name, "
               "person_title, booth, note, source_url, fetched_at, resolution, "
               "apollo, icp_score FROM evi_participants WHERE run_id = %s")
        args: list[Any] = [run_id]
        if role:
            sql += " AND role = %s"
            args.append(role)
        sql += " ORDER BY icp_score DESC NULLS LAST, org_name"
        with conn.cursor() as cur:
            cur.execute(sql, args)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            _ts(r, "fetched_at")
        return rows
    except Exception as e:
        logger.warning("event_intel_store.get_participants failed: %s", e)
        return []
    finally:
        conn.close()


def update_participant_resolution(participant_ids: list[int], domain: str | None,
                                  apollo: dict | None, resolution: str,
                                  icp_score: int | None = None) -> None:
    """Attach an Apollo match to every participant row sharing one company.
    Takes a list because the same exhibitor commonly appears under several
    roles (exhibitor AND sponsor AND a speaker's employer) and all of them
    should carry the same firmographics from one resolution."""
    if not participant_ids:
        return
    conn = _pg_conn()
    if conn is None:
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE evi_participants SET org_domain = COALESCE(%s, org_domain), "
                "apollo = %s, resolution = %s, icp_score = COALESCE(%s, icp_score) "
                "WHERE id = ANY(%s)",
                (domain, json.dumps(apollo) if apollo else None, resolution,
                 icp_score, list(participant_ids)))
        conn.commit()
    except Exception as e:
        logger.warning("event_intel_store.update_participant_resolution failed: %s", e)
    finally:
        conn.close()


# ── sources (the "what we could not read" ledger) ─────────────────────────

def save_source(run_id: int, event_id: int | None, url: str, kind: str,
                status: str, http_status: int | None = None,
                rows_found: int = 0, note: str = "") -> None:
    conn = _pg_conn()
    if conn is None:
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evi_sources (run_id, event_id, url, kind, status, "
                "http_status, rows_found, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, event_id, url, kind, status, http_status, rows_found,
                 (note or "")[:500]))
        conn.commit()
    except Exception as e:
        logger.warning("event_intel_store.save_source failed: %s", e)
    finally:
        conn.close()


def get_sources(run_id: int) -> list[dict]:
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, event_id, url, kind, status, http_status, rows_found, "
                "note, fetched_at FROM evi_sources WHERE run_id = %s ORDER BY id",
                (run_id,))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            _ts(r, "fetched_at")
        return rows
    except Exception as e:
        logger.warning("event_intel_store.get_sources failed: %s", e)
        return []
    finally:
        conn.close()
