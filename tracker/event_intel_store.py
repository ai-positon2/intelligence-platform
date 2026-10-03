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
  evi_profiles      one locked client profile: the Step 0 classification and
                    the Step 1 intake that the scoring rubric reads. A
                    recommend run without one is refused, not defaulted.
  evi_candidates    one row per scored event, carrying all three sub-scores,
                    the discovery category it came from, the famous-event
                    audit verdict, and the matchmaking evidence. Sub-scores
                    are stored separately from the total because the
                    breakdown IS the audit trail.

That last table is the point of the whole schema. An event roster assembled
from four of seven published pages, with three silently dropped, looks
identical to a complete one: shorter. Recording every attempt with its
outcome is what lets the report say "3 sources could not be read" instead of
quietly understating an event. Cf. the standing lesson that an empty result
must read as a fact about the request, not a fact about the world.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
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
# A page that could not be fetched directly, whose list was reconstructed by
# searching instead. Deliberately its own status rather than folded into `ok`:
# a page we parsed is evidence of a different grade from a list a model
# assembled out of search results, and a report that shows one number for both
# has thrown away the distinction that makes the roster trustworthy.
SOURCE_RECOVERED = "recovered"

# How a participant row came to exist. Stored per row, because a roster can
# legitimately mix the two and the report has to be able to say which is which.
VIA_PAGE = "page"
VIA_SEARCH = "search"
PROVENANCE = (VIA_PAGE, VIA_SEARCH)
PROVENANCE_LABELS = {
    VIA_PAGE: "Read from the event's own page",
    VIA_SEARCH: "Recovered by search: the page itself could not be read",
}


def _pg_conn():
    """One-off Postgres connection. None if DATABASE_URL is unset or the
    connection fails; callers treat that as 'not available'."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(database_url, connect_timeout=8)
        from .event_intel_jobs import CURRENT
        job = CURRENT.get()
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('evi.worker_token', %s, false)", (job['token'] if job else '',))
        return conn
    except Exception as e:
        logger.warning("event_intel_store: Postgres connection failed: %s", e)
        return None


def _ensure_tables(conn) -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('evi-schema-v2'))")
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
                provenance VARCHAR(12) NOT NULL DEFAULT 'page',
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
        # The locked client profile. Its whole reason to exist is that the
        # rubric refuses to run without one: the B2B/B2C classification decides
        # which side of the trade-show floor every sub-score measures, and a
        # default would silently score the wrong crowd.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_profiles (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                client_name TEXT NOT NULL,
                website TEXT,
                classification VARCHAR(32) NOT NULL,
                orientation VARCHAR(16) NOT NULL,
                buyer_roles TEXT,
                verticals TEXT,
                acv_band TEXT,
                sales_cycle TEXT,
                geo_scope TEXT,
                window_months INTEGER NOT NULL DEFAULT 12,
                force_include TEXT,
                force_exclude TEXT,
                max_events INTEGER NOT NULL DEFAULT 15,
                budget_note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evi_profiles_email
            ON evi_profiles (email, updated_at DESC)
        """)
        # Scored candidates. Every sub-score keeps its own column AND its own
        # note: the skill requires the three-part breakdown to be shown, not
        # just the total, because the breakdown is what makes a score
        # auditable rather than asserted.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_candidates (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES evi_runs(id),
                event_id INTEGER REFERENCES evi_events(id),
                name TEXT NOT NULL,
                edition TEXT,
                website TEXT,
                organizer TEXT,
                starts_on DATE,
                ends_on DATE,
                country TEXT,
                city TEXT,
                quarter TEXT,
                days INTEGER,
                industry TEXT,
                attendees TEXT,
                booths TEXT,
                format VARCHAR(16),
                category VARCHAR(32) NOT NULL,
                famous BOOLEAN NOT NULL DEFAULT FALSE,
                committed BOOLEAN NOT NULL DEFAULT FALSE,
                audit_verdict VARCHAR(16),
                audit_note TEXT,
                relevance INTEGER,
                relevance_note TEXT,
                dm_access INTEGER,
                dm_access_note TEXT,
                engagement INTEGER,
                engagement_note TEXT,
                matchmaking INTEGER NOT NULL DEFAULT 0,
                matchmaking_evidence TEXT,
                matchmaking_reason TEXT,
                total INTEGER,
                tier VARCHAR(4),
                description TEXT,
                client_line TEXT,
                cost_note TEXT,
                confidence VARCHAR(10),
                gaps JSONB,
                sources JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evi_candidates_run
            ON evi_candidates (run_id, total DESC)
        """)
        # One row per company the work-the-room play produced a draft for.
        # `draft_status` and `draft_reason` are columns rather than a report
        # field because a rewritten draft has to stay rewritten: the record of
        # why an opener was thrown away is the audit trail for the claim that
        # this agent does not fabricate conversations.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_outreach (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES evi_runs(id),
                source_run_id INTEGER REFERENCES evi_runs(id),
                event_name TEXT,
                event_class VARCHAR(16) NOT NULL,
                org_name TEXT NOT NULL,
                org_domain TEXT,
                role VARCHAR(24),
                person_name TEXT,
                person_title TEXT,
                fit INTEGER,
                fit_note TEXT,
                angle TEXT,
                opener TEXT,
                booth_note TEXT,
                draft_status VARCHAR(32) NOT NULL DEFAULT 'ok',
                draft_reason TEXT,
                draft_flagged JSONB,
                account_note TEXT,
                unqualified BOOLEAN NOT NULL DEFAULT FALSE,
                qualify_note TEXT,
                source_url TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evi_outreach_run
            ON evi_outreach (run_id, fit DESC)
        """)
        # evi_runs predates the recommend mode and already exists in
        # production, so this column arrives by ALTER rather than by the
        # CREATE above, which is a no-op on an existing table.
        # What actually happened. The source skill's "tighten over time" step
        # asks the operator to read reply-rate data out of a sequencer after
        # three to five events and drop what did not work. There is no
        # sequencer here, so the loop is closed with the one fact this
        # platform can hold honestly: what the user decided, in their own
        # words, keyed on the event rather than on the run, so a decision
        # survives into every later run that surfaces the same event.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_outcomes (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                event_key TEXT NOT NULL,
                event_name TEXT NOT NULL,
                decision VARCHAR(16) NOT NULL,
                note TEXT,
                run_id INTEGER REFERENCES evi_runs(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (email, event_key)
            )
        """)
        cur.execute("ALTER TABLE evi_runs ADD COLUMN IF NOT EXISTS "
                    "profile_id INTEGER REFERENCES evi_profiles(id)")
        cur.execute("ALTER TABLE evi_runs ADD COLUMN IF NOT EXISTS "
                    "source_run_id INTEGER REFERENCES evi_runs(id)")
        # evi_participants predates the search-recovery read path, so existing
        # rows get the default: they were all read from a page.
        cur.execute("ALTER TABLE evi_participants ADD COLUMN IF NOT EXISTS "
                    "provenance VARCHAR(12) NOT NULL DEFAULT 'page'")
        cur.execute("ALTER TABLE evi_candidates ADD COLUMN IF NOT EXISTS "
                    "committed BOOLEAN NOT NULL DEFAULT FALSE")
        # Discovery has always read whether an event is in person, virtual or
        # hybrid, and the column it needed did not exist, so every read of it
        # was thrown away at write time. Rows stored before this get NULL,
        # which renders as nothing rather than as "in person".
        cur.execute("ALTER TABLE evi_candidates ADD COLUMN IF NOT EXISTS "
                    "format VARCHAR(16)")
        # The same normalisation evi_outcomes.event_key already computes at
        # write time (name_key(name)), stored on the candidate row too so a
        # client's outcome history can be joined to what CATEGORY/FORMAT the
        # event they decided on actually was, without a second copy of the
        # matching logic re-implemented in SQL. Existing rows get NULL and
        # are excluded from the join rather than backfilled: a name_key
        # computed today from a name written months ago is a fresh read of
        # old data, not a fact this migration should assert on its own.
        cur.execute("ALTER TABLE evi_candidates ADD COLUMN IF NOT EXISTS "
                    "name_key VARCHAR(300)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evi_candidates_name_key "
                    "ON evi_candidates (name_key)")
        # Which locked client profile an outcome was decided under. One email
        # can hold several profiles (an agency login managing several
        # clients' calendars); without this, a preference learned for one
        # profile would leak into another profile's scoring the moment they
        # share a login. Nullable: existing rows predate profile-scoped
        # history and are excluded from outcome_pattern() rather than guessed.
        cur.execute("ALTER TABLE evi_outcomes ADD COLUMN IF NOT EXISTS "
                    "profile_id INTEGER REFERENCES evi_profiles(id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evi_runs_profile "
                    "ON evi_runs (profile_id)")
        # A client this account manages under an arrangement where even the
        # AGGREGATE fact of their interest in an event must not be shared.
        # Defaults FALSE, so this ships at zero behaviour change until it is
        # deliberately set: see cross_client_interest(), which excludes a
        # confidential profile from ever CONTRIBUTING to another client's
        # count, and event_intel_pipeline, which skips ever showing the
        # signal TO a confidential profile in the first place.
        cur.execute("ALTER TABLE evi_profiles ADD COLUMN IF NOT EXISTS "
                    "confidential BOOLEAN NOT NULL DEFAULT FALSE")
        # Legacy outcomes remain untouched: their client/edition ownership is
        # ambiguous. New decisions are explicitly scoped and never backfilled.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_decisions (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                profile_id INTEGER NOT NULL REFERENCES evi_profiles(id),
                event_identity TEXT NOT NULL,
                event_name TEXT NOT NULL,
                decision VARCHAR(16) NOT NULL CHECK (decision IN ('going','skipped','went')),
                note TEXT,
                run_id INTEGER NOT NULL REFERENCES evi_runs(id),
                category TEXT,
                format TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (profile_id, event_identity)
            )
        """)
        # One row per run: the last Apollo company match and what it spent.
        # The resolve route is the only billed route in this agent, and
        # without a record of a finished match every repeat press (or a
        # cross-site replay) billed the whole roster again.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_resolutions (
                run_id INTEGER PRIMARY KEY REFERENCES evi_runs(id),
                email TEXT NOT NULL,
                titles_key TEXT NOT NULL DEFAULT '',
                state VARCHAR(16) NOT NULL,
                result JSONB,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at TIMESTAMPTZ
            )
        """)
        # Paid work this agent does outside any run: the typeahead company
        # search (Apollo credits) and profile drafting (Claude calls). Neither
        # has a run to hang a ledger row on, and both used to be unrecorded.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_account_usage (
                id BIGSERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                kind VARCHAR(32) NOT NULL,
                calls INTEGER NOT NULL DEFAULT 0,
                credits INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # List-rate estimate of the model calls a draft made, which run
        # outside any job and so never reach the run ledger.
        cur.execute("ALTER TABLE evi_account_usage ADD COLUMN IF NOT EXISTS "
                    "usd NUMERIC(12,4) NOT NULL DEFAULT 0")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evi_account_usage "
                    "ON evi_account_usage (email, created_at)")
        # The people found at an event, one row per person per event, with
        # every piece of proof kept beside them (event_intel_attendees). Kept
        # out of evi_participants on purpose: that table is the companies the
        # event published, and its counts, roster and CSV all read it.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_attendees (
                id BIGSERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES evi_runs(id) ON DELETE CASCADE,
                event_id INTEGER,
                name TEXT NOT NULL,
                title TEXT,
                company TEXT,
                company_domain TEXT,
                linkedin TEXT,
                basis VARCHAR(16) NOT NULL,
                status VARCHAR(16),
                edition VARCHAR(16),
                evidence JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evi_attendees_run "
                    "ON evi_attendees (run_id, event_id)")
        # One row per run: the last attendee search and what it found. Like
        # evi_resolutions, it is what makes a second press while one search
        # is still going a no-op rather than a second search.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evi_attendee_scans (
                run_id INTEGER PRIMARY KEY REFERENCES evi_runs(id) ON DELETE CASCADE,
                email TEXT NOT NULL,
                state VARCHAR(16) NOT NULL,
                result JSONB,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at TIMESTAMPTZ
            )
        """)
        from .event_intel_evidence import schema
        schema(cur)
        from .event_intel_jobs import schema as jobs_schema
        jobs_schema(cur)
        from .event_intel_planning import schema as planning_schema
        planning_schema(cur)
        from .event_intel_cache import schema as cache_schema
        cache_schema(cur)
    conn.commit()
    _TABLES_READY = True


def _ts(d: dict, *keys: str) -> None:
    """ISO-format timestamp/date columns in place so jsonify never chokes."""
    for k in keys:
        v = d.get(k)
        if v is not None and hasattr(v, "isoformat"):
            d[k] = v.isoformat()


# ── runs ──────────────────────────────────────────────────────────────────

def save_run(email: str, mode: str, query: str, icp_note: str | None = None,
             profile_id: int | None = None,
             source_run_id: int | None = None) -> int | None:
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evi_runs (email, mode, query, icp_note, profile_id, "
                "source_run_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (email, mode, query, icp_note, profile_id, source_run_id))
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
                "summary, credits_spent, profile_id, source_run_id, "
                "created_at, updated_at "
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
                "  (SELECT e.name FROM evi_events e "
                "   WHERE e.run_id = COALESCE(r.source_run_id, r.id) "
                "   ORDER BY e.id LIMIT 1) AS event_name, "
                "  r.source_run_id "
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
                 "location", "country", "city", "availability", "availability_source", "venue", "format", "audience_note", "stated_size",
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
                "location, country, city, availability, availability_source, venue, format, audience_note, stated_size, confidence, "
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
                # An unrecognised provenance falls back to the WEAKER grade,
                # not the stronger one. Mislabelling a search-recovered row as
                # page-read overstates the evidence; the reverse only
                # understates it, and understating is the safe direction.
                (r.get("provenance") if r.get("provenance") in PROVENANCE
                 else VIA_SEARCH),
                r.get("resolution") or "unresolved",
                json.dumps(r["apollo"]) if r.get("apollo") else None,
                r.get("icp_score"), json.dumps(dict(r.get("evidence") or {}, source_text_sha256=r.get("source_text_sha256")))))
        if not payload:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO evi_participants (run_id, event_id, org_name, org_domain, "
                "role, tier, person_name, person_title, booth, note, source_url, "
                "fetched_at, provenance, resolution, apollo, icp_score, evidence) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)", payload)
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
               "person_title, booth, note, source_url, fetched_at, provenance, "
               "resolution, apollo, icp_score, evidence FROM evi_participants WHERE run_id = %s")
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


def update_participant_websites(updates: list[tuple]) -> int:
    """(participant id, website or None, profile lookup record[, profile
    detail]) per row.

    The website is only ever written onto a row that has none, so a pass over
    profile pages can never replace a link the listing itself published, and
    the records go into evidence beside whatever is already there. Returns
    how many rows were written."""
    if not updates:
        return 0
    conn = _pg_conn()
    if conn is None:
        return 0
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            payload = []
            for u in updates:
                pid, domain, record = u[0], u[1], u[2]
                patch = {"profile_lookup": record or {}}
                if len(u) > 3 and u[3] is not None:
                    patch["profile_detail"] = u[3]
                payload.append((domain or None, json.dumps(patch), pid))
            cur.executemany(
                "UPDATE evi_participants SET org_domain = COALESCE(org_domain, %s), "
                "evidence = COALESCE(evidence, '{}'::jsonb) || %s::jsonb WHERE id = %s",
                payload)
        conn.commit()
        return len(updates)
    except Exception as e:
        logger.warning("event_intel_store.update_participant_websites failed: %s", e)
        return 0
    finally:
        conn.close()


# ── attendees ─────────────────────────────────────────────────────────────

ATTENDEE_SCAN_STALE_MINUTES = 30
_ATTENDEE_COLS = ("id", "event_id", "name", "title", "company", "company_domain",
                  "linkedin", "basis", "status", "edition", "evidence")


def save_attendees(run_id: int, event_id: int | None, rows: list[dict]) -> int:
    """Replace one event's attendee rows for a run with `rows`.

    Replaced, not appended: a search run again finds the same people again,
    and appending listed each of them once per press."""
    conn = _pg_conn()
    if conn is None:
        return 0
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM evi_attendees WHERE run_id = %s AND "
                        "event_id IS NOT DISTINCT FROM %s", (run_id, event_id))
            cur.executemany(
                "INSERT INTO evi_attendees (run_id, event_id, name, title, company, "
                "company_domain, linkedin, basis, status, edition, evidence) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                [(run_id, event_id, str(r.get("name") or "")[:200],
                  (r.get("title") or None) and str(r["title"])[:300],
                  (r.get("company") or None) and str(r["company"])[:200],
                  r.get("company_domain") or None,
                  (r.get("linkedin") or None) and str(r["linkedin"])[:300],
                  r.get("basis"), r.get("status") or None, r.get("edition") or None,
                  json.dumps(r.get("evidence") or {}))
                 for r in rows if r.get("name") and r.get("basis")])
        conn.commit()
        return len(rows)
    except Exception as e:
        logger.warning("event_intel_store.save_attendees failed: %s", e)
        return 0
    finally:
        conn.close()


def get_attendees(run_id: int) -> list[dict]:
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT " + ", ".join(_ATTENDEE_COLS) + " FROM evi_attendees "
                        "WHERE run_id = %s ORDER BY id", (run_id,))
            return [dict(zip(_ATTENDEE_COLS, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("event_intel_store.get_attendees failed: %s", e)
        return []
    finally:
        conn.close()


def begin_attendee_scan(run_id: int, email: str):
    """Take the run's attendee-search slot. "go" when this call may search,
    "busy" when a search younger than ATTENDEE_SCAN_STALE_MINUTES is still
    going, None when storage is unavailable. A search older than that is
    one a restarted server never finished, and may be started again."""
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO evi_attendee_scans (run_id, email, state) "
                        "VALUES (%s, %s, 'new') ON CONFLICT (run_id) DO NOTHING",
                        (run_id, email))
            cur.execute("SELECT state, started_at < now() - %s * interval '1 minute' "
                        "FROM evi_attendee_scans WHERE run_id = %s FOR UPDATE",
                        (ATTENDEE_SCAN_STALE_MINUTES, run_id))
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return None
            if row[0] == "running" and not row[1]:
                conn.commit()
                return "busy"
            cur.execute("UPDATE evi_attendee_scans SET state = 'running', email = %s, "
                        "started_at = now(), finished_at = NULL WHERE run_id = %s",
                        (email, run_id))
        conn.commit()
        return "go"
    except Exception as e:
        logger.warning("event_intel_store.begin_attendee_scan failed: %s", e)
        return None
    finally:
        conn.close()


def finish_attendee_scan(run_id: int, state: str, result: dict | None) -> None:
    conn = _pg_conn()
    if conn is None:
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO evi_attendee_scans (run_id, email, state) "
                        "SELECT id, email, %s FROM evi_runs WHERE id = %s "
                        "ON CONFLICT (run_id) DO NOTHING", (state, run_id))
            cur.execute("UPDATE evi_attendee_scans SET state = %s, result = %s::jsonb, "
                        "finished_at = now() WHERE run_id = %s",
                        (state, json.dumps(result) if result is not None else None, run_id))
        conn.commit()
    except Exception as e:
        logger.warning("event_intel_store.finish_attendee_scan failed: %s", e)
    finally:
        conn.close()


def get_attendee_scan(run_id: int) -> dict | None:
    """The run's last attendee search: state, result, and whether a running
    one has gone stale (a server restart ends it without a word)."""
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT state, result, started_at, finished_at, "
                        "started_at < now() - %s * interval '1 minute' "
                        "FROM evi_attendee_scans WHERE run_id = %s",
                        (ATTENDEE_SCAN_STALE_MINUTES, run_id))
            row = cur.fetchone()
        if not row:
            return None
        out = {"state": row[0], "result": row[1], "started_at": row[2],
               "finished_at": row[3]}
        if row[0] == "running" and row[4]:
            out["state"] = "stale"
        _ts(out, "started_at", "finished_at")
        return out
    except Exception as e:
        logger.warning("event_intel_store.get_attendee_scan failed: %s", e)
        return None
    finally:
        conn.close()


# ── company resolution record, and account-level usage ───────────────────

RESOLVE_STALE_MINUTES = 10


def begin_resolution(run_id: int, email: str, titles_key: str):
    """Decide whether a company match may spend credits, atomically.

    Returns ("cached", result) when this run was already matched with the
    same titles, ("busy", None) when a match is in progress (one younger than
    RESOLVE_STALE_MINUTES, which is far past gunicorn's request timeout), or
    ("go", fresh) after taking the run's slot, where `fresh` is True only when
    this call created the run's record. (None, None) if storage is
    unavailable. The row lock makes two simultaneous presses one match."""
    conn = _pg_conn()
    if conn is None:
        return None, None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO evi_resolutions (run_id, email, titles_key, state) "
                        "VALUES (%s, %s, %s, 'new') ON CONFLICT (run_id) DO NOTHING",
                        (run_id, email, titles_key))
            fresh = cur.rowcount == 1
            cur.execute("SELECT state, titles_key, result, "
                        "started_at < now() - %s * interval '1 minute' "
                        "FROM evi_resolutions WHERE run_id = %s AND email = %s FOR UPDATE",
                        (RESOLVE_STALE_MINUTES, run_id, email))
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return None, None
            state, key, result, stale = row
            if state == "done" and key == titles_key:
                conn.commit()
                return "cached", result
            if state == "running" and not stale:
                conn.commit()
                return "busy", None
            cur.execute("UPDATE evi_resolutions SET state = 'running', titles_key = %s, "
                        "started_at = now(), finished_at = NULL WHERE run_id = %s",
                        (titles_key, run_id))
        conn.commit()
        return "go", fresh
    except Exception as e:
        logger.warning("event_intel_store.begin_resolution failed: %s", e)
        return None, None
    finally:
        conn.close()


def finish_resolution(run_id: int, state: str, result: dict | None,
                      titles_key: str | None = None) -> None:
    """Record how a match ended. `done` makes a repeat request free; `failed`
    lets the next request try again."""
    conn = _pg_conn()
    if conn is None:
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE evi_resolutions SET state = %s, result = %s, "
                        "titles_key = COALESCE(%s, titles_key), finished_at = now() "
                        "WHERE run_id = %s",
                        (state, json.dumps(result) if result is not None else None,
                         titles_key, run_id))
        conn.commit()
    except Exception as e:
        logger.warning("event_intel_store.finish_resolution failed: %s", e)
    finally:
        conn.close()


def record_account_usage(email: str, kind: str, calls: int = 0, credits: int = 0,
                         usd: float = 0.0) -> None:
    if not (calls or credits or usd):
        return
    conn = _pg_conn()
    if conn is None:
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO evi_account_usage (email, kind, calls, credits, usd) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (email, kind, int(calls), int(credits), round(float(usd or 0), 4)))
        conn.commit()
    except Exception as e:
        logger.warning("event_intel_store.record_account_usage failed: %s", e)
    finally:
        conn.close()


def account_usage(email: str, hours: int = 24) -> dict:
    """{kind: {"calls": n, "credits": n, "usd": x}} over the last `hours`, plus the
    run ledger's provider calls under "run_calls"."""
    conn = _pg_conn()
    if conn is None:
        return {}
    try:
        _ensure_tables(conn)
        out = {}
        with conn.cursor() as cur:
            cur.execute("SELECT kind, COALESCE(sum(calls), 0), COALESCE(sum(credits), 0), "
                        "COALESCE(sum(usd), 0) "
                        "FROM evi_account_usage WHERE email = %s "
                        "AND created_at >= now() - %s * interval '1 hour' GROUP BY kind",
                        (email, hours))
            for kind, calls, credits, usd in cur.fetchall():
                out[kind] = {"calls": int(calls), "credits": int(credits), "usd": float(usd)}
            cur.execute("SELECT count(*) FROM evi_provider_calls WHERE email = %s "
                        "AND created_at >= now() - %s * interval '1 hour'", (email, hours))
            out["run_calls"] = {"calls": int(cur.fetchone()[0]), "credits": 0}
        return out
    except Exception as e:
        logger.warning("event_intel_store.account_usage failed: %s", e)
        return {}
    finally:
        conn.close()


# ── sources (the "what we could not read" ledger) ─────────────────────────

def save_source(run_id: int, event_id: int | None, url: str, kind: str,
                status: str, http_status: int | None = None,
                rows_found: int = 0, note: str = "", metadata: dict | None = None) -> None:
    conn = _pg_conn()
    if conn is None:
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evi_sources (run_id, event_id, url, kind, status, "
                "http_status, rows_found, note, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (run_id, event_id, url, kind, status, http_status, rows_found,
                 (note or "")[:500], json.dumps(metadata or {}, default=str)))
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
                "note, metadata, fetched_at FROM evi_sources WHERE run_id = %s ORDER BY id",
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


# ── profiles (Step 0 + Step 1, locked before anything is scored) ──────────
#
# The source skill puts a HARD STOP between intake and discovery: nothing is
# discovered or scored until the classification and the intake are confirmed.
# Here that stop is a foreign key. A recommend run carries a profile_id or it
# does not start, so the run can always answer "which crowd did you score, and
# against whose ICP?" from stored data rather than from a prompt nobody kept.

_PROFILE_TEXT_FIELDS = ("client_name", "website", "buyer_roles", "verticals",
                        "acv_band", "sales_cycle", "geo_scope", "force_include",
                        "force_exclude", "budget_note", "what_they_sell", "selected_product", "firmographics")


def normalise_profile(payload: dict) -> dict:
    """Validate and shape an intake payload. Pure, so it is tested without a
    database.

    Raises ValueError on an unusable classification rather than substituting
    one. Everything else is clamped: a profile with a silly window or an empty
    vertical list is still a usable profile, but a profile pointed at the
    wrong side of the trade-show floor is not.
    """
    from . import event_intel_rubric as rubric

    p = dict(payload or {})
    classification = str(p.get("classification") or "").strip()
    # Raises on anything not in the skill's four rows. Deliberately not caught
    # here: the caller turns it into a 400 so the user sees the real reason.
    orientation = rubric.orientation_for(classification)

    name = str(p.get("client_name") or "").strip()
    if not name:
        raise ValueError("A client name is required: every list is scored "
                         "against one client's ICP, and a list that would fit "
                         "two different clients is too generic to be useful.")

    out = {"classification": classification, "orientation": orientation}
    for f in _PROFILE_TEXT_FIELDS:
        v = str(p.get(f) or "").strip()
        out[f] = (v[:4000] if f in ("force_include", "force_exclude", "budget_note", "what_they_sell", "selected_product", "firmographics")
                  else v[:400]) or None
    out["client_name"] = name[:200]

    def _int(key, default, lo, hi):
        try:
            n = int(p.get(key) if p.get(key) not in (None, "") else default)
        except (TypeError, ValueError):
            n = default
        return max(lo, min(hi, n))

    # 12 months is the skill's default window; the cap of 15 is its default
    # maximum returned list.
    out["window_months"] = _int("window_months", 12, 1, 36)
    out["max_events"] = _int("max_events", rubric.DEFAULT_CAP, 1, 25)
    # No settings-page toggle exists yet for this (deliberately deferred --
    # the copy and any contractual meaning of "confidential" is a real
    # product/legal surface of its own), so every save today sends nothing
    # for this key and it defaults to False: zero behaviour change until a
    # caller deliberately sets it. See cross_client_interest() for what it
    # actually gates.
    if "confidential" in p and not isinstance(p["confidential"], bool):
        raise ValueError("Confidential must be true or false.")
    out["confidential"] = p.get("confidential", False)
    return out


def save_profile(email: str, payload: dict) -> int | None:
    """Insert a locked profile. Raises ValueError for a bad intake (the caller
    renders that); returns None only when storage itself is unavailable."""
    clean = normalise_profile(payload)
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        cols = ["email"] + list(clean)
        vals = [email] + [clean[k] for k in clean]
        with conn.cursor() as cur:
            cur.execute("INSERT INTO evi_profiles (%s) VALUES (%s) RETURNING id"
                        % (", ".join(cols), ", ".join(["%s"] * len(cols))), vals)
            pid = cur.fetchone()[0]
        conn.commit()
        return pid
    except Exception as e:
        logger.warning("event_intel_store.save_profile failed: %s", e)
        return None
    finally:
        conn.close()


_PROFILE_COLS = ("id, email, client_name, website, classification, orientation, "
                 "buyer_roles, verticals, acv_band, sales_cycle, geo_scope, "
                 "window_months, force_include, force_exclude, max_events, "
                 "budget_note, confidential, what_they_sell, selected_product, firmographics, created_at, updated_at")


def get_profile(profile_id: int, email: str) -> dict | None:
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT %s FROM evi_profiles WHERE id = %%s AND email = %%s"
                        % _PROFILE_COLS, (profile_id, email))
            row = cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
        out = dict(zip(cols, row))
        _ts(out, "created_at", "updated_at")
        return out
    except Exception as e:
        logger.warning("event_intel_store.get_profile failed: %s", e)
        return None
    finally:
        conn.close()


def search_known_profiles(email: str, query: str, limit: int = 8) -> list[dict]:
    """Clients this user has already set up, matching `query` by name -- the
    fallback for when the company-search vendor behind the client-name
    autocomplete (see app.py's search route) is unavailable. Shaped like an
    Apollo company dict (sci_company_search._to_company) so the same picker
    row renderer works for either source; `from_history: True` marks a row as
    an existing profile rather than a live vendor result. No `logo`: nothing
    here comes from Apollo, so the picker falls back to its initial-letter
    avatar for these rows, same as it already does for any company with no
    logo. [] on any failure."""
    conn = _pg_conn()
    q = (query or "").strip()
    if conn is None or not email or not q:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (client_name) client_name, website FROM evi_profiles "
                "WHERE email = %s AND client_name ILIKE %s "
                "ORDER BY client_name, updated_at DESC LIMIT %s",
                (email, "%" + q + "%", limit))
            rows = cur.fetchall()
        return [{"id": "", "name": name, "logo": None, "industry": None,
                "location": None, "description": None, "summary": None,
                "followers_count": None, "profile_url": None,
                "website": website, "from_history": True}
               for name, website in rows if name]
    except Exception as e:
        logger.warning("event_intel_store.search_known_profiles failed: %s", e)
        return []
    finally:
        conn.close()


def list_profiles(email: str, limit: int = 40) -> list[dict]:
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT %s FROM evi_profiles WHERE email = %%s "
                        "ORDER BY updated_at DESC LIMIT %%s"
                        % _PROFILE_COLS, (email, limit))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            _ts(r, "created_at", "updated_at")
        return rows
    except Exception as e:
        logger.warning("event_intel_store.list_profiles failed: %s", e)
        return []
    finally:
        conn.close()


def update_profile(profile_id: int, email: str, payload: dict) -> bool:
    """Re-lock an existing profile. Same validation as creating one: an edit
    that blanks the classification must fail the same way a bad create does."""
    clean = normalise_profile(payload)
    if "confidential" not in payload:
        clean.pop("confidential", None)
    conn = _pg_conn()
    if conn is None:
        return False
    try:
        _ensure_tables(conn)
        sets = ", ".join("%s = %%s" % k for k in clean)
        with conn.cursor() as cur:
            cur.execute("UPDATE evi_profiles SET %s, updated_at = now() "
                        "WHERE id = %%s AND email = %%s" % sets,
                        (*[clean[k] for k in clean], profile_id, email))
            changed = cur.rowcount
        conn.commit()
        return bool(changed)
    except Exception as e:
        logger.warning("event_intel_store.update_profile failed: %s", e)
        return False
    finally:
        conn.close()


# ── candidates (scored events) ────────────────────────────────────────────

_CANDIDATE_FIELDS = (
    "event_id", "name", "name_key", "edition", "website", "organizer", "starts_on", "ends_on",
    "country", "city", "quarter", "days", "industry", "attendees", "booths",
    "format", "category", "famous", "audit_verdict", "audit_note",
    "relevance", "relevance_note", "dm_access", "dm_access_note",
    "engagement", "engagement_note", "matchmaking", "matchmaking_evidence",
    "matchmaking_reason", "total", "tier", "description", "client_line",
    "cost_note", "availability", "availability_source", "confidence", "committed", "gaps", "sources")


_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")


def iso_date_or_none(value) -> str | None:
    """A real calendar date as YYYY-MM-DD, or None.

    Dates reach this module as whatever a language model wrote, and they land
    in a DATE column. Postgres answers "Q2 2026" by aborting the statement,
    which in a batch insert costs every other row in it. Parsing here means an
    unusable date costs its own field instead of the run it arrived in.

    Deliberately strict. A date is only accepted in the ISO order the prompt
    asks for, because "04/11/2026" is the 4th of November to the organiser who
    published it and the 11th of April to the reader, and a booking made on the
    wrong one of those is worse than no date at all. Anything rejected here is
    kept as text by the caller rather than discarded.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    m = _ISO_DATE.match(str(value).strip())
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)),
                             int(m.group(3))).isoformat()
    except ValueError:
        # A well-formed but impossible date (2026-13-45). Same treatment as
        # unparseable: the reader keeps the raw text, the DATE column gets NULL.
        return None


EVENT_FORMATS = ("in_person", "virtual", "hybrid")


def _fmt(value) -> str | None:
    """One of the three formats, or nothing.

    A total function over a closed set, in the same spirit as the rubric's
    orientation lookup: an unrecognised word is dropped rather than stored and
    rendered as if it meant something. "TBC" on a card reads as a fact about
    the event.
    """
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in EVENT_FORMATS else None


def normalise_candidate(raw: dict) -> dict | None:
    """Shape one scored candidate, recomputing the total from its sub-scores.

    The total a model returns is DISCARDED and recomputed here from the three
    sub-scores and the matchmaking gate. That is the whole point: a model that
    scores 30/25/12 and then writes "Total: 84" produces a row where the
    breakdown and the headline disagree, and the headline is the one people
    read. Recomputing makes the two incapable of disagreeing.

    Returns None for a row with no name or an unknown discovery category,
    rather than filing it under a guessed one.
    """
    from . import event_intel_rubric as rubric

    r = dict(raw or {})
    name = str(r.get("name") or "").strip()
    category = str(r.get("category") or "").strip().lower()
    if not name or category not in rubric.CATEGORIES:
        logger.info("event_intel_store: dropped a candidate (name=%r, category=%r)",
                    name[:60], category)
        return None

    scored = rubric.score(
        r.get("relevance"), r.get("dm_access"), r.get("engagement"),
        organizer_run=bool(r.get("organizer_run")),
        matchmaking_evidence=str(r.get("matchmaking_evidence") or ""))

    website = str(r.get("website") or "").strip()
    if website and not website.lower().startswith(("http://", "https://")):
        website = ""

    def _txt(key, cap=600):
        v = str(r.get(key) or "").strip()
        return v[:cap] or None

    def _num(key, lo, hi):
        try:
            return max(lo, min(hi, int(r.get(key))))
        except (TypeError, ValueError):
            return None

    # Normalised BEFORE gaps_for reads it, so a format the closed set rejects
    # is reported as unknown rather than passing the gap check on the strength
    # of the raw string and then rendering as nothing.
    r["format"] = _fmt(r.get("format"))
    starts_on = iso_date_or_none(r.get("starts_on"))
    ends_on = iso_date_or_none(r.get("ends_on"))
    # An event that ends before it starts is two unrelated readings, not a
    # range. The start is the one a reader plans around, so the end is the one
    # dropped, and the row still says when the event begins.
    if starts_on and ends_on and ends_on < starts_on:
        ends_on = None
    # A date that would not parse is still an answer to "when": "Q2 2026" and
    # "October 2026" are what the organiser has actually announced this early.
    # It cannot go in a DATE column, so it is kept as the quarter text rather
    # than thrown away, and the row reads as scheduled-but-undated instead of
    # as having no timing at all.
    quarter = _txt("quarter", 12)
    if not quarter and not starts_on:
        raw_when = str(r.get("starts_on") or "").strip()
        quarter = raw_when[:12] or None

    # Computed here, once, at write time -- the same idiom evi_outcomes'
    # own event_key already uses (see save_outcome below). A client's future
    # outcome history is joined back to this row by this column, in
    # outcome_pattern(), rather than re-deriving name_key's normalisation a
    # second time in SQL.
    from .event_intel_discover import name_key
    out = {
        "event_id": r.get("event_id"),
        "name": name[:250],
        "name_key": name_key(name) or None,
        "edition": _txt("edition", 80),
        "website": website or None,
        "organizer": _txt("organizer", 200),
        "starts_on": starts_on,
        "ends_on": ends_on,
        "country": _txt("country", 100),
        "city": _txt("city", 120),
        "quarter": quarter,
        "days": _num("days", 1, 30),
        "industry": _txt("industry", 160),
        # Held as TEXT, quoted as the event states it. Never normalised to an
        # integer, because "12,000+" and "we expect 12,000" are different
        # claims and turning either into 12000 invents a precision the event
        # never published.
        "attendees": _txt("attendees", 80),
        "booths": _txt("booths", 80),
        # in_person / virtual / hybrid. Decision-relevant on its own: a
        # virtual event scoring 82 and an in-person one scoring 82 are not the
        # same proposition, because one of them costs flights.
        "format": r["format"],
        "category": category,
        "famous": bool(r.get("famous")),
        "audit_verdict": _txt("audit_verdict", 16),
        "audit_note": _txt("audit_note", 1200),
        "relevance": scored["sub_scores"][rubric.DIM_RELEVANCE],
        "relevance_note": _txt("relevance_note", 800),
        "dm_access": scored["sub_scores"][rubric.DIM_DM_ACCESS],
        "dm_access_note": _txt("dm_access_note", 800),
        "engagement": scored["sub_scores"][rubric.DIM_ENGAGEMENT],
        "engagement_note": _txt("engagement_note", 800),
        "matchmaking": scored["matchmaking"],
        "matchmaking_evidence": _txt("matchmaking_evidence", 800),
        "matchmaking_reason": scored["matchmaking_reason"][:600],
        "total": scored["total"],
        "tier": scored["tier"],
        "description": _txt("description", 900),
        "client_line": _txt("client_line", 600),
        # Budget rides along as a note and is never read by the rubric. The
        # rubric's score() has no parameter that could accept it.
        "cost_note": _txt("cost_note", 400),
        "availability": r.get("availability") if r.get("availability") in ("open","sold_out","cancelled") else "unknown",
        "availability_source": _txt("availability_source", 1000),
        # Carried through so ranking can read it back. Set in code from the
        # profile at discovery, never taken from a model's own claim.
        "committed": bool(r.get("committed")),
        "confidence": (str(r.get("confidence") or "medium").strip().lower()[:10]),
        "gaps": rubric.gaps_for(r),
        "sources": [u for u in (r.get("sources") or [])
                    if isinstance(u, str) and u.lower().startswith(("http://", "https://"))][:12],
    }
    return out


def save_candidates(run_id: int, rows: list[dict]) -> int:
    """Bulk insert scored candidates. Returns how many landed."""
    if not rows:
        return 0
    conn = _pg_conn()
    if conn is None:
        return 0
    try:
        _ensure_tables(conn)
        payload = []
        for r in rows:
            clean = normalise_candidate(r)
            if clean is None:
                continue
            # run_id is prepended here rather than carried on the row, the same
            # way save_event and save_participants do it, so the column can
            # never depend on a caller remembering to set it.
            payload.append((run_id,) + tuple(
                json.dumps(clean[f]) if f in ("gaps", "sources") else clean[f]
                for f in _CANDIDATE_FIELDS))
        if not payload:
            return 0
        cols = ("run_id",) + _CANDIDATE_FIELDS
        sql = ("INSERT INTO evi_candidates (%s) VALUES (%s)"
               % (", ".join(cols), ", ".join(["%s"] * len(cols))))
        try:
            with conn.cursor() as cur:
                cur.executemany(sql, payload)
            conn.commit()
            return len(payload)
        except Exception as e:
            # A batch insert is all-or-nothing, so one unwritable value would
            # otherwise empty a report that discovery had already filled. Retry
            # per row: a bad row costs itself and is named in the log, and the
            # other fourteen events still reach the reader.
            conn.rollback()
            logger.warning("event_intel_store.save_candidates: batch insert "
                           "failed (%s); retrying row by row", e)
            written = 0
            for row in payload:
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql, row)
                    conn.commit()
                    written += 1
                except Exception as row_error:
                    conn.rollback()
                    logger.warning("event_intel_store.save_candidates: dropped "
                                   "%r (%s)", row[2], row_error)
            return written
    except Exception as e:
        logger.warning("event_intel_store.save_candidates failed: %s", e)
        return 0
    finally:
        conn.close()


def get_candidates(run_id: int) -> list[dict]:
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, %s FROM evi_candidates WHERE run_id = %%s "
                "ORDER BY total DESC NULLS LAST, name"
                % ", ".join(_CANDIDATE_FIELDS), (run_id,))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            _ts(r, "starts_on", "ends_on")
        return rows
    except Exception as e:
        logger.warning("event_intel_store.get_candidates failed: %s", e)
        return []
    finally:
        conn.close()


def prior_candidate_names(email: str, exclude_run_id: int | None = None,
                          limit_runs: int = 12) -> list[dict]:
    """Every event this user has been recommended before, grouped by run.

    This is what makes the source skill's cross-client pattern check real.
    The skill asks a model to imagine whether the same list would appear for a
    different client; here the previous lists are on disk, so the overlap is
    measured against what was actually produced rather than recalled.
    """
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        sql = ("SELECT r.id, r.query, p.client_name, p.classification, "
               "       array_agg(c.name) AS names "
               "FROM evi_runs r "
               "JOIN evi_candidates c ON c.run_id = r.id "
               "LEFT JOIN evi_profiles p ON p.id = r.profile_id "
               "WHERE r.email = %s AND r.mode = 'recommend' AND r.status = 'complete'")
        args: list[Any] = [email]
        if exclude_run_id:
            sql += " AND r.id <> %s"
            args.append(exclude_run_id)
        sql += (" GROUP BY r.id, r.query, p.client_name, p.classification "
                "ORDER BY r.id DESC LIMIT %s")
        args.append(limit_runs)
        with conn.cursor() as cur:
            cur.execute(sql, args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("event_intel_store.prior_candidate_names failed: %s", e)
        return []
    finally:
        conn.close()


# ── Cross-client intelligence, k-anonymity gated ───────────────────────────
#
# Every query above this line filters WHERE email = %s. This is the first
# one in this file that crosses email on purpose. genericness() (in
# event_intel_audit.py) reads like a cross-client check but is not one: it
# compares different client_name PROFILES under the SAME shared login. The
# two functions below intentionally cross real tenant boundaries, so they are
# held to a stricter rule than anything else here: the RETURN VALUE itself
# must never carry another client's email, run_id or client_name -- not
# withheld only in whatever text later gets rendered from it. genericness()'s
# own `worst`/`comparisons` dict still carries the other client's real name
# today, masked only by its rendered `advice` string; that is a data-layer
# leak this pair is deliberately built not to repeat.
#
# The k-anonymity floor itself (how many distinct clients before a count is
# safe to say out loud at all) lives in event_intel_audit.cross_client_signal,
# next to genericness() -- these two functions only ever return raw counts,
# never a yes/no verdict, so nobody downstream can mistake an unfiltered
# query result for a cleared privacy check.
#
# CURRENTLY UNREFERENCED: event_intel_pipeline._run_recommend calls
# event_intel_report.disabled_cross_client_check() instead, pending
# unambiguous client identity/consent/confidential-profile isolation across
# staff logins. Both functions below are kept fully implemented and tested as
# the foundation to wire back in once that exists, not as an oversight.

def cross_client_interest(name_keys: list[str], classification: str | None,
                          window_days: int, exclude_email: str) -> dict:
    """{name_key: {"name": str, "distinct_clients": int}} across OTHER
    clients' completed recommend runs, for events those clients themselves
    KEPT (score cleared rubric.RANK_FLOOR -- "also considered or kept", never
    "was searched and discarded").

    `classification` narrows to profiles with a comparable buyer-access shape
    (pass None to skip that filter). `window_days` counts from when the
    OTHER client's run happened (evi_runs.created_at), not the event's own
    date: the question is whether multiple clients are independently being
    pointed at this right now, which the event's own start date (often
    months out) does not answer, and evi_candidates.quarter is a free-text
    fallback only populated when no real date parsed -- not reliable enough
    to filter on.

    A confidential profile's candidates never contribute a count here (see
    evi_profiles.confidential); the calling client is always excluded from
    its own count via `exclude_email`.
    """
    if not name_keys:
        return {}
    from . import event_intel_rubric as rubric
    conn = _pg_conn()
    if conn is None:
        return {}
    try:
        _ensure_tables(conn)
        sql = (
            "SELECT c.name_key, MIN(c.name) AS name, "
            # A client is a profile, not a login: two people at one agency
            # researching the same client are one client, and one person
            # researching three clients is three. A run with no profile
            # falls back to its login.
            "       COUNT(DISTINCT COALESCE('p' || r.profile_id::text, 'e' || r.email)) AS distinct_clients "
            "FROM evi_candidates c "
            "JOIN evi_runs r ON r.id = c.run_id "
            "LEFT JOIN evi_profiles p ON p.id = r.profile_id "
            "WHERE c.name_key = ANY(%s) "
            "AND r.mode = 'recommend' AND r.status = 'complete' "
            # Kept means what rank() keeps: over the bar, aimed at that
            # client's buyers, and not an edition that was already over.
            "AND c.total >= %s AND c.relevance >= %s "
            "AND (c.ends_on IS NULL OR c.ends_on >= r.created_at::date) "
            "AND r.created_at >= now() - (%s || ' days')::interval "
            "AND r.email <> %s "
            "AND NOT COALESCE(p.confidential, false)")
        args: list[Any] = [name_keys, rubric.RANK_FLOOR, rubric.RELEVANCE_GATE,
                           window_days, exclude_email]
        if classification:
            sql += " AND p.classification = %s"
            args.append(classification)
        sql += " GROUP BY c.name_key"
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return {row[0]: {"name": row[1], "distinct_clients": row[2]}
                    for row in cur.fetchall()}
    except Exception as e:
        logger.warning("event_intel_store.cross_client_interest failed: %s", e)
        return {}
    finally:
        conn.close()


def classification_population(classification: str | None, window_days: int,
                              exclude_email: str) -> int:
    """How many DISTINCT clients (this one excluded) have a completed
    recommend run in this classification within the window.

    The population cross_client_signal's second k-anonymity gate needs: a raw
    "3 or more other clients" floor is close to meaningless, and arguably
    re-identifying by elimination, in a classification bucket that only has
    4 or 5 clients ever -- see CROSS_CLIENT_MIN_POPULATION.
    """
    conn = _pg_conn()
    if conn is None:
        return 0
    try:
        _ensure_tables(conn)
        sql = (
            "SELECT COUNT(DISTINCT r.email) "
            "FROM evi_runs r LEFT JOIN evi_profiles p ON p.id = r.profile_id "
            "WHERE r.mode = 'recommend' AND r.status = 'complete' "
            "AND r.created_at >= now() - (%s || ' days')::interval "
            "AND r.email <> %s "
            "AND NOT COALESCE(p.confidential, false)")
        args: list[Any] = [window_days, exclude_email]
        if classification:
            sql += " AND p.classification = %s"
            args.append(classification)
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchone()[0] or 0
    except Exception as e:
        logger.warning("event_intel_store.classification_population failed: %s", e)
        return 0
    finally:
        conn.close()


# ── Work-the-room storage ─────────────────────────────────────────────────

_OUTREACH_FIELDS = (
    "run_id", "source_run_id", "event_name", "event_class", "org_name",
    "org_domain", "role", "person_name", "person_title", "fit", "fit_note",
    "angle", "opener", "booth_note", "draft_status", "draft_reason",
    "draft_flagged", "account_note", "unqualified", "qualify_note",
    "source_url")


def normalise_outreach(raw: dict, run_id: int, source_run_id: int | None,
                       event_name: str | None, event_class: str) -> dict | None:
    """One enforced draft, ready to store.

    The enforcement pass in event_intel_workroom decides `draft_status`, and
    this deliberately does not second-guess it, with one exception: a row
    whose status says a draft was rewritten but which carries no reason is
    downgraded to an unexplained rewrite with wording that says so. A rewrite
    the user cannot see the reason for is a silent edit, and a silent edit to
    a message they are about to send under their own name is the thing this
    whole play exists to prevent.
    """
    from . import event_intel_workroom as wr
    if not isinstance(raw, dict):
        return None
    org = str(raw.get("org_name") or "").strip()
    if not org:
        return None
    if event_class not in wr.EVENT_CLASSES:
        raise ValueError(
            "Unknown event class %r; it must be one of: %s"
            % (event_class, ", ".join(wr.EVENT_CLASSES)))

    def _txt(key, limit):
        v = str(raw.get(key) or "").strip()
        return v[:limit] or None

    status = str(raw.get("draft_status") or wr.DRAFT_REVIEW).strip()
    if status not in (wr.DRAFT_OK, wr.DRAFT_REVIEW, wr.DRAFT_NO_EVIDENCE, wr.DRAFT_AGGRESSIVE,
                      wr.DRAFT_ACCOUNT):
        status = wr.DRAFT_REVIEW
    reason = _txt("draft_reason", 1200)
    if status in (wr.DRAFT_NO_EVIDENCE, wr.DRAFT_AGGRESSIVE) and not reason:
        reason = ("This draft was rewritten by the safety pass, but the reason "
                  "was lost before it could be stored. Treat the opener as "
                  "unverified and read it before using it.")

    fit = raw.get("fit")
    try:
        fit = max(0, min(100, int(fit)))
    except (TypeError, ValueError):
        fit = None

    role = str(raw.get("role") or "").strip()[:24] or None
    if role and role not in ROLES:
        role = None
    return {
        "run_id": run_id, "source_run_id": source_run_id,
        "event_name": (str(event_name or "").strip()[:300] or None),
        "event_class": event_class, "org_name": org[:300],
        "org_domain": _txt("org_domain", 200), "role": role,
        "person_name": _txt("person_name", 200),
        "person_title": _txt("person_title", 300),
        "fit": fit, "fit_note": _txt("fit_note", 800),
        "angle": _txt("angle", 800), "opener": _txt("opener", 1500),
        "booth_note": _txt("booth_note", 1500),
        "draft_status": status, "draft_reason": reason,
        "draft_flagged": [str(x)[:120] for x in (raw.get("draft_flagged") or [])][:12],
        "account_note": _txt("account_note", 800),
        "unqualified": bool(raw.get("unqualified")),
        "qualify_note": _txt("qualify_note", 600),
        "source_url": _txt("source_url", 800),
    }


def save_outreach(run_id: int, source_run_id: int | None, event_name: str | None,
                  event_class: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = _pg_conn()
    if conn is None:
        return 0
    try:
        _ensure_tables(conn)
        payload = []
        for r in rows:
            clean = normalise_outreach(r, run_id, source_run_id, event_name,
                                       event_class)
            if clean is None:
                continue
            payload.append(tuple(
                json.dumps(clean[f]) if f == "draft_flagged" else clean[f]
                for f in _OUTREACH_FIELDS))
        if not payload:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO evi_outreach (%s) VALUES (%s)"
                % (", ".join(_OUTREACH_FIELDS),
                   ", ".join(["%s"] * len(_OUTREACH_FIELDS))), payload)
        conn.commit()
        return len(payload)
    except Exception as e:
        logger.warning("event_intel_store.save_outreach failed: %s", e)
        return 0
    finally:
        conn.close()


def get_outreach(run_id: int) -> list[dict]:
    conn = _pg_conn()
    if conn is None:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, %s FROM evi_outreach WHERE run_id = %%s "
                "ORDER BY fit DESC NULLS LAST, org_name"
                % ", ".join(_OUTREACH_FIELDS), (run_id,))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("event_intel_store.get_outreach failed: %s", e)
        return []
    finally:
        conn.close()


def prior_participant_events(email: str, exclude_run_id: int | None = None,
                             limit: int = 4000) -> dict | None:
    """{org_key: [event names]} across this user's earlier roster runs.

    The substitute for event-radar's CRM lookup, built from the only prior
    context this deployment actually holds. Keyed by the same org_key the
    workroom module uses so "Acme Ltd" on one floor and "Acme" on another are
    one company rather than two.

    None, not {}, when the history could not be read: {} is "this account has
    no earlier rosters", and a database error was printed as exactly that
    (workroom audit, 2026-10-01). Every run of the excluded run's own event
    is left out too, so a second lookup of the same event is not "another
    event" this company was seen at.
    """
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _ensure_tables(conn)
        sql = ("SELECT p.org_name, COALESCE(e.name, r.query) AS event_name "
               "FROM evi_participants p "
               "JOIN evi_runs r ON r.id = p.run_id "
               "LEFT JOIN evi_events e ON e.id = p.event_id "
               "WHERE r.email = %s")
        args: list[Any] = [email]
        if exclude_run_id:
            sql += (" AND r.id <> %s AND lower(COALESCE(e.name, r.query)) NOT IN ("
                    "SELECT lower(COALESCE(e2.name, r2.query)) FROM evi_runs r2 "
                    "LEFT JOIN evi_events e2 ON e2.run_id = r2.id WHERE r2.id = %s)")
            args.extend([exclude_run_id, exclude_run_id])
        sql += " ORDER BY r.created_at DESC, p.id LIMIT %s"
        args.append(limit)
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
    except Exception as e:
        logger.warning("event_intel_store.prior_participant_events failed: %s", e)
        return None
    finally:
        conn.close()

    from .event_intel_workroom import org_key
    out: dict[str, set] = {}
    for org_name, event_name in rows:
        key = org_key(org_name or "")
        if not key or not event_name:
            continue
        out.setdefault(key, set()).add(str(event_name))
    return {k: sorted(v) for k, v in out.items()}


# ── Outcomes: the "tighten over time" loop ────────────────────────────────

DECISION_GOING = "going"
DECISION_SKIPPED = "skipped"
DECISION_WENT = "went"
DECISIONS = (DECISION_GOING, DECISION_SKIPPED, DECISION_WENT)
DECISION_LABELS = {
    DECISION_GOING: "Decided to go",
    DECISION_SKIPPED: "Decided to skip",
    DECISION_WENT: "Went, and here is what happened",
}


def save_outcome(email: str, event_name: str, decision: str,
                 note: str | None = None, run_id: int | None = None,
                 profile_id: int | None = None, event_identity: str | None = None) -> bool:
    """Write one client/edition decision; do not reuse ambiguous legacy history."""
    from .event_intel_identity import event_key, strict_name
    if not (event_name or '').strip():
        raise ValueError('An event name is required to record an outcome.')
    if decision not in DECISIONS:
        raise ValueError('Unknown decision %r.' % decision)
    if not run_id or not profile_id:
        raise ValueError('A client profile and source run are required.')
    run = get_run(run_id, email)
    if not run or run.get('profile_id') != profile_id or not get_profile(profile_id, email):
        raise ValueError('That event run does not belong to this client profile.')
    rows = get_candidates(run_id)
    hits = [c for c in rows if (event_key(c) == event_identity if event_identity
            else strict_name(c.get('name')) == strict_name(event_name))]
    if len(hits) != 1:
        raise ValueError('Choose an unambiguous event edition from this report.')
    row = hits[0]
    conn = _pg_conn()
    if conn is None:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO evi_decisions
                (email, profile_id, event_identity, event_name, decision, note, run_id, category, format)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (profile_id, event_identity) DO UPDATE SET
                decision=EXCLUDED.decision, note=EXCLUDED.note, run_id=EXCLUDED.run_id,
                email=EXCLUDED.email, event_name=EXCLUDED.event_name, updated_at=now()""",
                (email, profile_id, event_key(row), row['name'], decision,
                 str(note or '').strip()[:2000] or None, run_id, row.get('category'), row.get('format')))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        logger.exception('event decision could not be saved')
        return False
    finally:
        conn.close()


def get_outcomes(email: str, profile_id: int | None = None) -> dict:
    """Only explicit client/edition decisions are eligible for display."""
    if not profile_id:
        return {}
    conn = _pg_conn()
    if conn is None:
        return {}
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT d.event_identity, d.event_name, d.decision, d.note, d.updated_at
                FROM evi_decisions d JOIN evi_profiles p ON p.id=d.profile_id
                WHERE d.profile_id=%s AND d.email=%s AND p.email=%s""", (profile_id,email,email))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols,r)) for r in cur.fetchall()]
        out = {}
        for row in rows:
            _ts(row,'updated_at')
            out[row.pop('event_identity')] = row
        return out
    except Exception:
        logger.exception('event decisions could not be read')
        return {}
    finally:
        conn.close()


def outcome_pattern(email: str, profile_id: int | None,
                    exclude_run_id: int | None = None) -> dict:
    """One observation per client/edition, irrespective of repeated discoveries."""
    out = {'by_category': {}, 'by_format': {}}
    if not profile_id:
        return out
    conn = _pg_conn()
    if conn is None:
        return out
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            sql = """SELECT d.category, d.format, d.decision FROM evi_decisions d
                JOIN evi_profiles p ON p.id=d.profile_id
                WHERE d.profile_id=%s AND d.email=%s AND p.email=%s"""
            args = [profile_id,email,email]
            if exclude_run_id:
                sql += ' AND d.run_id <> %s'
                args.append(exclude_run_id)
            cur.execute(sql,args)
            rows = cur.fetchall()
        for category, fmt, decision in rows:
            for group, key in [('by_category',category),('by_format',fmt)]:
                if key:
                    b = out[group].setdefault(key,dict(decisions=0,skipped=0,went_or_going=0))
                    b['decisions'] += 1
                    b['skipped' if decision == 'skipped' else 'went_or_going'] += 1
        return out
    except Exception:
        logger.exception('event history could not be read')
        return out
    finally:
        conn.close()
