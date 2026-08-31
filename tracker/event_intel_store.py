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
                # An unrecognised provenance falls back to the WEAKER grade,
                # not the stronger one. Mislabelling a search-recovered row as
                # page-read overstates the evidence; the reverse only
                # understates it, and understating is the safe direction.
                (r.get("provenance") if r.get("provenance") in PROVENANCE
                 else VIA_SEARCH),
                r.get("resolution") or "unresolved",
                json.dumps(r["apollo"]) if r.get("apollo") else None,
                r.get("icp_score")))
        if not payload:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO evi_participants (run_id, event_id, org_name, org_domain, "
                "role, tier, person_name, person_title, booth, note, source_url, "
                "fetched_at, provenance, resolution, apollo, icp_score) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", payload)
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
               "resolution, apollo, icp_score FROM evi_participants WHERE run_id = %s")
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


# ── profiles (Step 0 + Step 1, locked before anything is scored) ──────────
#
# The source skill puts a HARD STOP between intake and discovery: nothing is
# discovered or scored until the classification and the intake are confirmed.
# Here that stop is a foreign key. A recommend run carries a profile_id or it
# does not start, so the run can always answer "which crowd did you score, and
# against whose ICP?" from stored data rather than from a prompt nobody kept.

_PROFILE_TEXT_FIELDS = ("client_name", "website", "buyer_roles", "verticals",
                        "acv_band", "sales_cycle", "geo_scope", "force_include",
                        "force_exclude", "budget_note")


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
        out[f] = (v[:4000] if f in ("force_include", "force_exclude", "budget_note")
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
                 "budget_note, created_at, updated_at")


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
    "event_id", "name", "edition", "website", "organizer", "starts_on", "ends_on",
    "country", "city", "quarter", "days", "industry", "attendees", "booths",
    "category", "famous", "audit_verdict", "audit_note",
    "relevance", "relevance_note", "dm_access", "dm_access_note",
    "engagement", "engagement_note", "matchmaking", "matchmaking_evidence",
    "matchmaking_reason", "total", "tier", "description", "client_line",
    "cost_note", "confidence", "committed", "gaps", "sources")


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

    out = {
        "event_id": r.get("event_id"),
        "name": name[:250],
        "edition": _txt("edition", 80),
        "website": website or None,
        "organizer": _txt("organizer", 200),
        "starts_on": r.get("starts_on") or None,
        "ends_on": r.get("ends_on") or None,
        "country": _txt("country", 100),
        "city": _txt("city", 120),
        "quarter": _txt("quarter", 12),
        "days": _num("days", 1, 30),
        "industry": _txt("industry", 160),
        # Held as TEXT, quoted as the event states it. Never normalised to an
        # integer, because "12,000+" and "we expect 12,000" are different
        # claims and turning either into 12000 invents a precision the event
        # never published.
        "attendees": _txt("attendees", 80),
        "booths": _txt("booths", 80),
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
            payload.append(tuple(
                json.dumps(clean[f]) if f in ("gaps", "sources") else clean[f]
                for f in _CANDIDATE_FIELDS))
        if not payload:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO evi_candidates (%s) VALUES (%s)"
                % (", ".join(_CANDIDATE_FIELDS),
                   ", ".join(["%s"] * len(_CANDIDATE_FIELDS))), payload)
        conn.commit()
        return len(payload)
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

    status = str(raw.get("draft_status") or wr.DRAFT_OK).strip()
    if status not in (wr.DRAFT_OK, wr.DRAFT_NO_EVIDENCE, wr.DRAFT_AGGRESSIVE,
                      wr.DRAFT_ACCOUNT):
        status = wr.DRAFT_OK
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
                             limit: int = 4000) -> dict:
    """{org_key: [event names]} across this user's earlier roster runs.

    The substitute for event-radar's CRM lookup, built from the only prior
    context this deployment actually holds. Keyed by the same org_key the
    workroom module uses so "Acme Ltd" on one floor and "Acme" on another are
    one company rather than two.
    """
    conn = _pg_conn()
    if conn is None:
        return {}
    try:
        _ensure_tables(conn)
        sql = ("SELECT p.org_name, COALESCE(e.name, r.query) AS event_name "
               "FROM evi_participants p "
               "JOIN evi_runs r ON r.id = p.run_id "
               "LEFT JOIN evi_events e ON e.id = p.event_id "
               "WHERE r.email = %s")
        args: list[Any] = [email]
        if exclude_run_id:
            sql += " AND r.id <> %s"
            args.append(exclude_run_id)
        sql += " LIMIT %s"
        args.append(limit)
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
    except Exception as e:
        logger.warning("event_intel_store.prior_participant_events failed: %s", e)
        return {}
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
                 note: str | None = None, run_id: int | None = None) -> bool:
    """Record what the user decided about one event.

    Keyed on the event, not the run, so a decision made on one recommendation
    shows up on every later one that surfaces the same event. That is the
    whole point: the second time a tool suggests something you already
    rejected, it should know.

    A decision this does not recognise is refused rather than stored, because
    the report renders DECISION_LABELS and an unknown key would print raw.
    """
    from .event_intel_discover import name_key
    name = (event_name or "").strip()
    if not name:
        raise ValueError("An event name is required to record an outcome.")
    if decision not in DECISIONS:
        raise ValueError("Unknown decision %r. It must be one of: %s"
                         % (decision, ", ".join(DECISIONS)))
    conn = _pg_conn()
    if conn is None:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evi_outcomes (email, event_key, event_name, decision, "
                "note, run_id) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (email, event_key) DO UPDATE SET "
                "decision = EXCLUDED.decision, note = EXCLUDED.note, "
                "event_name = EXCLUDED.event_name, updated_at = now()",
                (email, name_key(name), name[:300], decision,
                 (note or "").strip()[:2000] or None, run_id))
        conn.commit()
        return True
    except Exception as e:
        logger.warning("event_intel_store.save_outcome failed: %s", e)
        return False
    finally:
        conn.close()


def get_outcomes(email: str) -> dict:
    """{event_key: {decision, note, event_name, updated_at}} for this user."""
    conn = _pg_conn()
    if conn is None:
        return {}
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT event_key, event_name, decision, note, updated_at "
                "FROM evi_outcomes WHERE email = %s", (email,))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        out = {}
        for r in rows:
            _ts(r, "updated_at")
            out[r.pop("event_key")] = r
        return out
    except Exception as e:
        logger.warning("event_intel_store.get_outcomes failed: %s", e)
        return {}
    finally:
        conn.close()
