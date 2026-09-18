"""Thought Leader Intelligence -- Phase 0: identity resolution.

Given a person's name (+ optional company/title hint, or a LinkedIn URL /
X handle the caller already knows), resolve to ONE real public figure before
any research is done about them. This is deliberately its own phase, and its
own file: profiling the wrong "John Smith" is a materially worse failure than
the wrong-event-edition problem tracker/event_intel_resolve.py exists for --
this agent researches a real named individual's reputation, not a
conference -- so identity resolution is refused rather than guessed at low
confidence, the same discipline event_intel_resolve applies to an ambiguous
event.

Three sources are combined, cheapest/most-structured first:
  1. Apollo (tracker/apollo_client.search_people) -- a business-database
     candidate: title, company, LinkedIn/X URLs, photo. Free-tier search,
     costs nothing to try.
  2. tracker/claude_websearch -- a real web-search grounding pass that
     confirms or corrects Apollo's candidate (or finds the person without
     one), and is the ONLY source allowed to set confidence. Follows
     event_intel_resolve.py's exact discipline: refuse a reply that ran no
     search (a recalled name, not a found one), require "high"/"medium"
     confidence to proceed, never invent a fact.
  3. Unipile + YouTube Data API -- best-effort platform verification of
     whatever LinkedIn URL / name the first two steps produced. Both are
     soft-fail: a platform that can't be verified is reported as
     unresolved, never as absent from the person's real life.

Later phases (own-platform posts, sentiment, earned media, synthesis) build
on `identity` once a run is confirmed; nothing here fetches a single post.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlparse

from datetime import datetime, timedelta, timezone

from tracker import (apify_transport, apify_x_replies, apollo_client, claude_websearch,
                     sci_name_match, sci_source_linkedin_unipile, sci_source_x, sci_youtube_client,
                     tlpr_facebook_pulse, tlpr_instagram_pulse, tlpr_linkedin_pulse,
                     tlpr_press, tlpr_reddit_pulse, tlpr_tiktok_pulse, tlpr_x_pulse,
                     unipile_client, unipile_transport)

logger = logging.getLogger(__name__)

_TABLES_READY = False

# Mirrors sci_store.py's STALE_RUN_MINUTES: this app runs on Railway with no
# persistent worker queue, so a background thread that died mid-resolve (or
# mid-collect, once Phase 1's own thread is running) leaves a "resolving" or
# "collecting" row behind forever unless something else says how old is too
# old to still be someone waiting on it.
STALE_RUN_MINUTES = 10

STATUSES = ("resolving", "needs_review", "confirmed", "failed")
POSTS_STATUSES = ("idle", "collecting", "ready", "failed")
_STALE_POSTS_ERROR = "The collection stalled and did not finish -- try collecting again."
_STALE_REACTION_ERROR = "The analysis stalled and did not finish -- try again."
_STALE_PRESS_ERROR = "The press search stalled and did not finish -- try again."
_STALE_SYNTHESIS_ERROR = "The report generation stalled and did not finish -- try again."


# ───────────────────────── Postgres store ─────────────────────────

def _pg_conn():
    """One-off Postgres connection. None if DATABASE_URL isn't configured or
    the connection fails -- callers treat that as 'not available', same as
    every other best-effort datastore in this app."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        return None
    try:
        import psycopg2
        return psycopg2.connect(database_url, connect_timeout=8)
    except Exception as e:
        logger.warning("thought_leader_pr: Postgres connection failed: %s", e)
        return None


def _ensure_tables(conn) -> None:
    """CREATE TABLE IF NOT EXISTS, once per process. Concurrent gunicorn
    workers racing this on cold start is safe -- Postgres serializes the DDL."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS thought_leader_pr_runs (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                input_name TEXT NOT NULL,
                company_hint TEXT,
                title_hint TEXT,
                linkedin_url_hint TEXT,
                x_handle_hint TEXT,
                status TEXT NOT NULL DEFAULT 'resolving'
                    CHECK (status IN ('resolving', 'needs_review', 'confirmed', 'failed')),
                confidence TEXT,
                reasoning TEXT,
                identity JSONB,
                error TEXT,
                posts_status TEXT NOT NULL DEFAULT 'idle',
                posts JSONB,
                posts_errors JSONB,
                posts_updated_at TIMESTAMPTZ,
                reaction_status TEXT NOT NULL DEFAULT 'idle',
                reaction JSONB,
                reaction_errors JSONB,
                reaction_updated_at TIMESTAMPTZ,
                press_status TEXT NOT NULL DEFAULT 'idle',
                press JSONB,
                press_errors JSONB,
                press_updated_at TIMESTAMPTZ,
                synthesis_status TEXT NOT NULL DEFAULT 'idle',
                synthesis JSONB,
                synthesis_errors JSONB,
                synthesis_updated_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_tlpr_runs_email_created
            ON thought_leader_pr_runs (email, created_at DESC)
        """)
        # Added for Phase 1 (owned-platform posts) after the table shipped
        # with Phase 0 only -- see sci_store.py's own company_logo/
        # reddit_pulse columns for the same "already in CREATE TABLE above,
        # ALSO added here" pattern, so this is correct whether Railway
        # already created the Phase-0-only table or not.
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS posts_status TEXT NOT NULL DEFAULT 'idle'")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS posts JSONB")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS posts_errors JSONB")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS posts_updated_at TIMESTAMPTZ")
        # Added for Phase 2 (audience reaction), same reasoning.
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS reaction_status TEXT NOT NULL DEFAULT 'idle'")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS reaction JSONB")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS reaction_errors JSONB")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS reaction_updated_at TIMESTAMPTZ")
        # Added for Phase 3 (earned media / press), same reasoning.
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS press_status TEXT NOT NULL DEFAULT 'idle'")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS press JSONB")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS press_errors JSONB")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS press_updated_at TIMESTAMPTZ")
        # Added for Phase 4 (synthesis report), same reasoning.
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS synthesis_status TEXT NOT NULL DEFAULT 'idle'")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS synthesis JSONB")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS synthesis_errors JSONB")
        cur.execute("ALTER TABLE thought_leader_pr_runs ADD COLUMN IF NOT EXISTS synthesis_updated_at TIMESTAMPTZ")
    conn.commit()
    _TABLES_READY = True


def create_run(*, email: str, input_name: str, company_hint: str | None = None,
               title_hint: str | None = None, linkedin_url_hint: str | None = None,
               x_handle_hint: str | None = None) -> int | None:
    """Insert a new run row in 'resolving' status. Returns its id, or None on
    any failure -- the caller can still run resolve_identity() and show the
    result even if it could not be persisted."""
    conn = _pg_conn()
    if not conn:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO thought_leader_pr_runs
                    (email, input_name, company_hint, title_hint, linkedin_url_hint, x_handle_hint)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (email, input_name, company_hint, title_hint, linkedin_url_hint, x_handle_hint))
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        logger.warning("thought_leader_pr: create_run failed: %s", e)
        return None
    finally:
        conn.close()


def save_result(run_id: int, email: str, result: dict) -> bool:
    """Write a resolve_identity() result onto its run row. Ownership-scoped
    in the SQL itself (WHERE id = %s AND email = %s), never fetched-then-
    checked in Python."""
    if not run_id:
        return False
    conn = _pg_conn()
    if not conn:
        return False
    status = "needs_review"
    if result.get("ok"):
        status = "needs_review"  # still awaits the user's explicit confirm
    elif result.get("error"):
        status = "failed"
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET status = %s, confidence = %s, reasoning = %s, identity = %s,
                    error = %s, updated_at = now()
                WHERE id = %s AND email = %s
            """, (status, result.get("confidence"), result.get("reasoning"),
                  json.dumps(result.get("identity")) if result.get("identity") else None,
                  (result.get("error") or {}).get("detail") if result.get("error") else None,
                  run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_result failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def confirm_run(run_id: int, email: str) -> bool:
    """Mark a run's identity as user-confirmed -- the gate later phases will
    check before spending any budget researching this person."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET status = 'confirmed', updated_at = now()
                WHERE id = %s AND email = %s AND status = 'needs_review'
            """, (run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: confirm_run failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


_RUN_COLUMNS = ("id, input_name, company_hint, title_hint, status, confidence, "
               "reasoning, identity, error, posts_status, posts, posts_errors, "
               "posts_updated_at, reaction_status, reaction, reaction_errors, "
               "reaction_updated_at, press_status, press, press_errors, "
               "press_updated_at, synthesis_status, synthesis, synthesis_errors, "
               "synthesis_updated_at, created_at, updated_at")


def get_run(run_id: int, email: str) -> dict | None:
    conn = _pg_conn()
    if not conn:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT {_RUN_COLUMNS}
                FROM thought_leader_pr_runs WHERE id = %s AND email = %s
            """, (run_id, email))
            row = cur.fetchone()
            if not row:
                return None
            run = _resolve_stale_posts(_row_to_dict(row), email)
            run = _resolve_stale_reaction(run, email)
            run = _resolve_stale_press(run, email)
            return _resolve_stale_synthesis(run, email)
    except Exception as e:
        logger.warning("thought_leader_pr: get_run failed for run %s: %s", run_id, e)
        return None
    finally:
        conn.close()


def list_runs(email: str, limit: int = 25) -> list[dict]:
    conn = _pg_conn()
    if not conn:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT {_RUN_COLUMNS}
                FROM thought_leader_pr_runs WHERE email = %s
                ORDER BY created_at DESC LIMIT %s
            """, (email, limit))
            runs = []
            for row in cur.fetchall():
                run = _resolve_stale_reaction(_resolve_stale_posts(_row_to_dict(row), email), email)
                run = _resolve_stale_press(run, email)
                runs.append(_resolve_stale_synthesis(run, email))
            return runs
    except Exception as e:
        logger.warning("thought_leader_pr: list_runs failed: %s", e)
        return []
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    (rid, input_name, company_hint, title_hint, status, confidence, reasoning,
     identity, error, posts_status, posts, posts_errors, posts_updated_at,
     reaction_status, reaction, reaction_errors, reaction_updated_at,
     press_status, press, press_errors, press_updated_at,
     synthesis_status, synthesis, synthesis_errors, synthesis_updated_at,
     created_at, updated_at) = row
    return {
        "id": rid, "input_name": input_name, "company_hint": company_hint,
        "title_hint": title_hint, "status": status, "confidence": confidence,
        "reasoning": reasoning, "identity": identity, "error": error,
        "posts_status": posts_status or "idle", "posts": posts, "posts_errors": posts_errors,
        "posts_updated_at": posts_updated_at.isoformat() if posts_updated_at else None,
        "reaction_status": reaction_status or "idle", "reaction": reaction,
        "reaction_errors": reaction_errors,
        "reaction_updated_at": reaction_updated_at.isoformat() if reaction_updated_at else None,
        "press_status": press_status or "idle", "press": press, "press_errors": press_errors,
        "press_updated_at": press_updated_at.isoformat() if press_updated_at else None,
        "synthesis_status": synthesis_status or "idle", "synthesis": synthesis,
        "synthesis_errors": synthesis_errors,
        "synthesis_updated_at": synthesis_updated_at.isoformat() if synthesis_updated_at else None,
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def _resolve_stale_posts(run: dict, email: str) -> dict:
    """A 'collecting' row gone quiet for STALE_RUN_MINUTES is a daemon thread
    a process restart killed with nothing left to ever mark it 'ready' or
    'failed' -- same fix, same place, as sci_store.resolve_stale_run: on
    read, not via a sweep this app has no worker infrastructure to run."""
    if run.get("posts_status") != "collecting":
        return run
    updated_at = run.get("updated_at")
    if not updated_at:
        return run
    last = datetime.fromisoformat(updated_at)
    if last >= datetime.now(timezone.utc) - timedelta(minutes=STALE_RUN_MINUTES):
        return run
    if save_posts_failed(run["id"], email, _STALE_POSTS_ERROR):
        run = dict(run, posts_status="failed", posts_errors={"_run": _STALE_POSTS_ERROR})
    return run


def _resolve_stale_reaction(run: dict, email: str) -> dict:
    """Same self-heal as _resolve_stale_posts, for Phase 2's own
    'collecting' status -- a separate column, so a stuck posts collection
    must never be able to also strand the reaction poll (or vice versa)."""
    if run.get("reaction_status") != "collecting":
        return run
    updated_at = run.get("updated_at")
    if not updated_at:
        return run
    last = datetime.fromisoformat(updated_at)
    if last >= datetime.now(timezone.utc) - timedelta(minutes=STALE_RUN_MINUTES):
        return run
    if save_reaction_failed(run["id"], email, _STALE_REACTION_ERROR):
        run = dict(run, reaction_status="failed", reaction_errors={"_run": _STALE_REACTION_ERROR})
    return run


def _resolve_stale_press(run: dict, email: str) -> dict:
    """Same self-heal as _resolve_stale_posts/_resolve_stale_reaction, for
    Phase 3's own 'collecting' status -- a separate column, so a stuck
    press search can't strand the posts or reaction polls, or vice versa."""
    if run.get("press_status") != "collecting":
        return run
    updated_at = run.get("updated_at")
    if not updated_at:
        return run
    last = datetime.fromisoformat(updated_at)
    if last >= datetime.now(timezone.utc) - timedelta(minutes=STALE_RUN_MINUTES):
        return run
    if save_press_failed(run["id"], email, _STALE_PRESS_ERROR):
        run = dict(run, press_status="failed", press_errors={"_run": _STALE_PRESS_ERROR})
    return run


def _resolve_stale_synthesis(run: dict, email: str) -> dict:
    """Same self-heal as the other three, for Phase 4's own 'collecting'
    status -- a fourth independent column, so a stuck report generation
    can't strand the posts, reaction, or press polls, or vice versa."""
    if run.get("synthesis_status") != "collecting":
        return run
    updated_at = run.get("updated_at")
    if not updated_at:
        return run
    last = datetime.fromisoformat(updated_at)
    if last >= datetime.now(timezone.utc) - timedelta(minutes=STALE_RUN_MINUTES):
        return run
    if save_synthesis_failed(run["id"], email, _STALE_SYNTHESIS_ERROR):
        run = dict(run, synthesis_status="failed", synthesis_errors={"_run": _STALE_SYNTHESIS_ERROR})
    return run


def start_collecting(run_id: int, email: str) -> bool:
    """Flip posts_status to 'collecting' before the background thread
    starts, so a poll immediately after the /collect request returns sees
    the real in-progress state rather than a stale 'idle'."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET posts_status = 'collecting', posts_errors = NULL, updated_at = now()
                WHERE id = %s AND email = %s AND status = 'confirmed'
            """, (run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: start_collecting failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def save_posts(run_id: int, email: str, posts: dict, errors: dict) -> bool:
    """posts_status becomes 'ready' even when every platform failed --
    per-platform failure is recorded in `errors`, not a top-level run
    failure. A partial result across 3 independent platforms is not the
    same defect class as the collection job itself crashing (see
    save_posts_failed); conflating them would hide which platforms actually
    worked behind one blanket 'failed' the moment any single one didn't."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET posts_status = 'ready', posts = %s, posts_errors = %s,
                    posts_updated_at = now(), updated_at = now()
                WHERE id = %s AND email = %s
            """, (json.dumps(posts), json.dumps(errors) if errors else None, run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_posts failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def save_posts_failed(run_id: int, email: str, message: str) -> bool:
    """The collection job itself crashed (an unexpected exception, or a
    staleness timeout) -- distinct from save_posts, which always means the
    job ran to completion, however partial the result."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET posts_status = 'failed', posts_errors = %s, posts_updated_at = now(),
                    updated_at = now()
                WHERE id = %s AND email = %s
            """, (json.dumps({"_run": message}), run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_posts_failed failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def start_reacting(run_id: int, email: str) -> bool:
    """Flip reaction_status to 'collecting' before Phase 2's background
    thread starts -- same reason start_collecting exists for posts_status,
    kept as a separate gate so Phase 2 can be re-run without needing
    posts_status to also be 'confirmed'-adjacent (it already requires an
    actual identity, checked in collect_reaction_job itself, not here)."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET reaction_status = 'collecting', reaction_errors = NULL, updated_at = now()
                WHERE id = %s AND email = %s AND status = 'confirmed'
            """, (run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: start_reacting failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def save_reaction(run_id: int, email: str, reaction: dict, errors: dict) -> bool:
    """reaction_status becomes 'ready' even when every source failed --
    same "partial is not a failure" rule save_posts follows, per-source
    failure lives in `errors`."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET reaction_status = 'ready', reaction = %s, reaction_errors = %s,
                    reaction_updated_at = now(), updated_at = now()
                WHERE id = %s AND email = %s
            """, (json.dumps(reaction), json.dumps(errors) if errors else None, run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_reaction failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def save_reaction_failed(run_id: int, email: str, message: str) -> bool:
    """The analysis job itself crashed -- distinct from save_reaction, which
    always means the job ran to completion, however partial the result."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET reaction_status = 'failed', reaction_errors = %s, reaction_updated_at = now(),
                    updated_at = now()
                WHERE id = %s AND email = %s
            """, (json.dumps({"_run": message}), run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_reaction_failed failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def start_press(run_id: int, email: str) -> bool:
    """Flip press_status to 'collecting' before Phase 3's background thread
    starts -- same gate as start_reacting, kept independent so press search
    can be run (or re-run) without depending on posts_status or
    reaction_status at all: GDELT/SerpAPI only need the confirmed identity,
    not Phase 1's collected posts."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET press_status = 'collecting', press_errors = NULL, updated_at = now()
                WHERE id = %s AND email = %s AND status = 'confirmed'
            """, (run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: start_press failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def save_press(run_id: int, email: str, press: dict, errors: dict) -> bool:
    """press_status becomes 'ready' even when a source came back empty or
    unconfigured -- same "partial is not a failure" rule save_posts and
    save_reaction follow, per-source failure lives in `errors`."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET press_status = 'ready', press = %s, press_errors = %s,
                    press_updated_at = now(), updated_at = now()
                WHERE id = %s AND email = %s
            """, (json.dumps(press), json.dumps(errors) if errors else None, run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_press failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def save_press_failed(run_id: int, email: str, message: str) -> bool:
    """The press search job itself crashed -- distinct from save_press,
    which always means the job ran to completion, however partial the
    result."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET press_status = 'failed', press_errors = %s, press_updated_at = now(),
                    updated_at = now()
                WHERE id = %s AND email = %s
            """, (json.dumps({"_run": message}), run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_press_failed failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def start_synthesizing(run_id: int, email: str) -> bool:
    """Flip synthesis_status to 'collecting' before Phase 4's background
    thread starts -- same gate as start_reacting/start_press, kept
    independent so the report can be generated (or regenerated) without
    depending on posts_status/reaction_status/press_status: synthesize_report
    reads whatever is already on the run row and writes plainly around
    whatever is missing, rather than requiring every earlier phase first."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET synthesis_status = 'collecting', synthesis_errors = NULL, updated_at = now()
                WHERE id = %s AND email = %s AND status = 'confirmed'
            """, (run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: start_synthesizing failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def save_synthesis(run_id: int, email: str, synthesis: dict, errors: dict) -> bool:
    """synthesis_status becomes 'ready' only once synthesize_report() has
    produced a real report -- unlike posts/reaction/press, this is a single
    Claude call with no independent sources to partially succeed, so a
    failure there goes through save_synthesis_failed instead of arriving
    here with an empty result."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET synthesis_status = 'ready', synthesis = %s, synthesis_errors = %s,
                    synthesis_updated_at = now(), updated_at = now()
                WHERE id = %s AND email = %s
            """, (json.dumps(synthesis), json.dumps(errors) if errors else None, run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_synthesis failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


def save_synthesis_failed(run_id: int, email: str, message: str) -> bool:
    """The report generation itself failed or crashed -- distinct from
    save_synthesis, which always means a real report was produced."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE thought_leader_pr_runs
                SET synthesis_status = 'failed', synthesis_errors = %s, synthesis_updated_at = now(),
                    updated_at = now()
                WHERE id = %s AND email = %s
            """, (json.dumps({"_run": message}), run_id, email))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("thought_leader_pr: save_synthesis_failed failed for run %s: %s", run_id, e)
        return False
    finally:
        conn.close()


# ───────────────────────── Identity resolution ─────────────────────────

_SYSTEM = (
    "You resolve a named person to ONE specific real individual, using web "
    "search, for a professional PR/reputation research tool used by a "
    "marketing agency. This tool profiles PUBLIC FIGURES ONLY -- executives, "
    "founders, authors, analysts, speakers, or creators with an existing "
    "public professional platform. It must never be used to profile a "
    "private individual, and you are the only safeguard against that.\n\n"
    "RULES.\n"
    "1. Resolve to ONE person. Common names collide -- if context (a company "
    "or title hint, or a candidate identity you were given to confirm) points "
    "to a specific person, verify THAT person rather than defaulting to the "
    "most famous bearer of the name. Do not silently merge two different "
    "people who share a name. A candidate identity you were given is an "
    "UNVERIFIED lead from a business database, not a conclusion -- if it "
    "conflicts with the other context you were given (a mismatched company, "
    "title, or field), or you cannot find independent evidence it is real, "
    "treat it as unrelated noise and identify the person the search and "
    "context actually point to instead of defaulting to it.\n"
    "2. VERIFY, do not pattern-match. Confirm this is a real, identifiable "
    "public figure with an actual public platform (LinkedIn, X, YouTube, "
    "press coverage, a book, a company they lead). Set confidence: \"high\" "
    "when multiple independent sources agree on the same person's identity "
    "and current role; \"medium\" when identified but a detail is "
    "unconfirmed or sources are thin; \"low\" when several different people "
    "plausibly match this name and you cannot choose between them; \"none\" "
    "when you cannot find a real public figure by this name at all. Return "
    "\"low\" or \"none\" rather than guessing -- everything downstream "
    "researches whoever you return here.\n"
    "3. Every entry in `disambiguating_facts` must be something you actually "
    "found while searching, stated plainly and factually (a role, a company, "
    "a widely known book or project) -- never invented, never a subjective "
    "characterization of the person.\n"
    "4. If this reads as a private individual with no real public platform, "
    "set `is_public_figure` to false and confidence to \"none\" -- say so in "
    "`reasoning`.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"confidence": "high"|"medium"|"low"|"none", "reasoning": str, '
    '"is_public_figure": bool, "full_name": str|null, "headline": str|null, '
    '"current_title": str|null, "current_company": str|null, '
    '"linkedin_url": str|null, "x_handle": str|null, "instagram_handle": str|null, '
    '"disambiguating_facts": [str]}'
)

_MIN_CONFIDENCE = ("high", "medium")


def _linkedin_slug(url: str | None) -> str | None:
    """The public identifier Unipile's GET /users/{id} wants ("satyanadella"
    from linkedin.com/in/satyanadella/), not the full URL."""
    if not url:
        return None
    try:
        parts = [p for p in urlparse(url).path.split("/") if p]
    except Exception:
        return None
    if len(parts) >= 2 and parts[0] == "in":
        return parts[1]
    return parts[-1] if parts else None


def _handle_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = [p for p in urlparse(url).path.split("/") if p]
    except Exception:
        return None
    return parts[-1].lstrip("@") if parts else None


MAX_NAME_CANDIDATES = 6


def _looks_like_same_person(name: str, candidate_full_name: str) -> bool:
    """Whether an Apollo row's OWN name plausibly matches the person being
    searched -- deliberately stricter than sci_name_match.plausible_match,
    which is right for a company name (a short, distinctive brand name
    being a SUBSET of a longer account title is expected and fine) but
    wrong for a person's full name, where a shared surname or given name
    alone is a weak signal: millions of unrelated people share one. Apollo
    is a B2B business-contact database with essentially no coverage of
    non-corporate public figures (politicians, authors, activists with no
    employer to list), and its free-text `keywords` search returns a
    "confident" top hit for almost any query regardless of real relevance
    -- the exact failure sci_name_match.py's own docstring describes
    against a different vendor. A real incident this caught: searching
    "Rahul Gandhi" (the Indian politician) returned an unrelated small-
    business owner named "Gandhi" as Apollo's top keyword hit purely
    because "Gandhi" appears in both the search and the row's company name
    ("Rahul Traders") -- handed to the identity-resolution model as "a
    business database suggests this may be [him]," it anchored on that
    wrong candidate instead of independently verifying the well-known
    person actually being searched.

    Requires every significant word of the SEARCHED name to appear in the
    candidate's own name (so "Rahul Gandhi" matches "Rahul Gandhi" or
    "Rahul K. Gandhi", but not a candidate whose name is only "Gandhi" or
    only shares an unrelated "Rahul")."""
    search_tokens = sci_name_match.name_tokens(name)
    candidate_tokens = sci_name_match.name_tokens(candidate_full_name)
    if not search_tokens or not candidate_tokens:
        return False
    return search_tokens <= candidate_tokens


def search_name_candidates(name: str, company_hint: str | None = None) -> tuple[list[dict], dict | None]:
    """A cheap, Apollo-only candidate list for a typed name, shown BEFORE
    resolve_identity's own websearch grounding call ever runs -- same
    cheap-before-expensive shape as Social Media Intelligence's own /search
    ahead of /analyze (tracker/sci_company_search.py), so a common name
    ("John Smith") can be disambiguated by a business-database match before
    a paid Claude+web_search call has to guess which one was meant.

    Deliberately a separate Apollo call from _best_apollo_candidate's, not a
    refactor of it: that function needs exactly one best-effort row to seed
    the grounding pass and already has its own tests; this one needs the
    other rows Apollo returned, that function throws away, surfaced as real
    choices instead. Never raises -- returns ([], error) so a down or
    unconfigured Apollo degrades to "type it yourself", never a 500."""
    name = (name or "").strip()
    if not name:
        return [], None
    api_key = os.environ.get("APOLLO_API_KEY", "")
    if not api_key:
        return [], {"code": "not_configured",
                    "message": "Apollo is not configured on this deployment."}
    filters = {"max_people": MAX_NAME_CANDIDATES,
              "keywords": ("%s %s" % (name, company_hint)).strip() if company_hint else name}
    try:
        people = apollo_client.search_people(filters, api_key, per_page=MAX_NAME_CANDIDATES)
    except Exception as e:
        logger.warning("thought_leader_pr: name candidate search failed for %r: %s", name, e)
        return [], {"code": "error", "message": "The candidate search could not be completed."}

    seen = set()
    candidates = []
    for p in people:
        full_name = (p.get("full_name") or "").strip()
        if not full_name:
            continue
        # Apollo's `keywords` filter is a fuzzy OR-search across name,
        # title, AND company/organization text, so a row surfaces here
        # whenever ANY of those fields loosely matches -- not necessarily
        # the person's own name. Require the row's own name to actually
        # match before showing it as a candidate for THIS person, or the
        # list fills up with unrelated people who merely work at a company
        # or hold a title that happens to share a word with the search.
        if not _looks_like_same_person(name, full_name):
            continue
        key = (full_name.lower(), (p.get("organization_name") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "full_name": full_name,
            "title": p.get("title"),
            "company": p.get("organization_name"),
            "location": ", ".join(b for b in (p.get("city"), p.get("state"), p.get("country")) if b) or None,
            "photo_url": p.get("photo_url"),
            "linkedin_url": p.get("linkedin_url"),
        })
    return candidates, None


def _best_apollo_candidate(name: str, company_hint: str | None,
                           linkedin_url: str | None, api_key: str) -> dict | None:
    """A business-database candidate to hand to the websearch grounding pass
    as a starting point -- never trusted on its own. An exact `linkedin_urls`
    match is preferred when the caller already gave us one; otherwise a
    free-text keyword search (Apollo has no "search by bare name" filter, so
    the name -- and company hint, folded in as more keywords rather than
    guessed as a domain -- goes through `keywords`)."""
    filters: dict = {"max_people": 5}
    if linkedin_url:
        filters["linkedin_urls"] = [linkedin_url]
    else:
        filters["keywords"] = f"{name} {company_hint}".strip() if company_hint else name
    try:
        candidates = apollo_client.search_people(filters, api_key, per_page=5)
    except Exception:
        logger.exception("thought_leader_pr: apollo search_people failed for %r", name)
        return None
    if not candidates:
        return None
    if linkedin_url:
        # An exact `linkedin_urls` lookup is authoritative on the URL the
        # caller themselves supplied -- trust it even if Apollo's own
        # `full_name` field differs (a nickname, a maiden name, a typo).
        return candidates[0]
    exact = [c for c in candidates
             if (c.get("full_name") or "").strip().lower() == name.strip().lower()]
    if exact:
        return exact[0]
    # No exact match: only fall back to a keyword-search hit when its OWN
    # name plausibly matches. Blindly returning candidates[0] here is
    # exactly what caused a real incident -- see _looks_like_same_person's
    # docstring for the full story. A missing Apollo hint is recoverable
    # (the websearch grounding step below still finds a well-known public
    # figure on its own); a wrong one silently anchors that step onto an
    # unrelated person.
    plausible = [c for c in candidates
                 if _looks_like_same_person(name, (c.get("full_name") or ""))]
    return plausible[0] if plausible else None


def _resolve_linkedin_platform(url: str | None) -> dict:
    """Best-effort verification of a LinkedIn profile via Unipile. Soft-fail
    throughout: no connected account, no match, or an API error all report
    as 'not verified', never as 'this person has no LinkedIn'."""
    out = {"url": url, "resolved": False, "provider_id": None,
           "headline": None, "photo_url": None, "note": None}
    slug = _linkedin_slug(url)
    if not slug:
        out["note"] = "No LinkedIn URL to verify."
        return out
    account_id = unipile_transport.account_for_platform("linkedin")
    if not account_id:
        out["note"] = "No connected LinkedIn account available to verify this profile."
        return out
    profile, err = unipile_client.get_user_profile(slug, account_id)
    if err is not None:
        out["note"] = unipile_client.describe_error(err)
        return out
    out["resolved"] = True
    out["provider_id"] = profile.get("provider_id")
    out["headline"] = profile.get("headline")
    out["photo_url"] = profile.get("profile_picture_url_large") or profile.get("profile_picture_url")
    return out


def _resolve_youtube_platform(name: str) -> dict:
    """Best-effort YouTube channel match. Reuses
    sci_youtube_client.resolve_company_channel for a person's name: the
    underlying lookup (an authoritative forHandle try, then a
    title-plausibility-checked search) has nothing company-specific about
    it -- only its docstring does."""
    out = {"url": None, "title": None, "channel_id": None, "resolved": False, "note": None}
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        out["note"] = "YouTube is not configured on this deployment."
        return out
    try:
        channel = sci_youtube_client.resolve_company_channel(name, api_key)
    except Exception:
        logger.exception("thought_leader_pr: youtube lookup failed for %r", name)
        channel = None
    if not channel:
        out["note"] = "No YouTube channel confidently matched this name."
        return out
    out["resolved"] = True
    out["channel_id"] = channel.get("channel_id")
    out["url"] = channel.get("profile_url")
    out["title"] = channel.get("title")
    return out


def _resolve_x_platform(handle: str | None) -> dict:
    """X/Twitter is Apify-collected (tracker/sci_source_x.py) starting in
    Phase 1 -- Phase 0 only records the handle if one was found, since
    scraping posts is Phase 1's job, not identity resolution's."""
    handle = (handle or "").lstrip("@") or None
    return {"handle": handle, "url": f"https://x.com/{handle}" if handle else None,
            "resolved": bool(handle)}


def _resolve_instagram_platform(handle: str | None) -> dict:
    """Instagram is never a Phase 1 owned platform (no own-post collection
    exists for it) -- this handle exists only so Phase 2's
    tracker/tlpr_instagram_pulse.py can read the person's own Mentions
    tab. Same soft-fail, no-separate-verification-call shape as
    _resolve_x_platform: a wrong or unconfirmed handle degrades to an
    empty mentions read, never a hard failure."""
    handle = (handle or "").lstrip("@") or None
    return {"handle": handle, "url": f"https://www.instagram.com/{handle}/" if handle else None,
            "resolved": bool(handle)}


def resolve_identity(name: str, *, company_hint: str | None = None,
                     title_hint: str | None = None, linkedin_url: str | None = None,
                     x_handle: str | None = None) -> dict:
    """Resolve one named person. Never raises.

    Returns {"ok": bool, "confidence": str, "reasoning": str,
             "identity": dict|None, "spend": dict, "error": {kind,detail}|None}.
    ok is True only at high/medium confidence for a confirmed public figure,
    matching event_intel_resolve.resolve_event's discipline: a low-confidence
    or private-individual result is refused, not softened into a partial one.
    """
    name = (name or "").strip()
    if not name:
        return {"ok": False, "confidence": "none", "reasoning": "No name was provided.",
                "identity": None, "spend": {}, "error": None}

    apollo_key = os.environ.get("APOLLO_API_KEY", "")
    apollo_candidate = None
    if apollo_key:
        apollo_candidate = _best_apollo_candidate(name, company_hint, linkedin_url, apollo_key)

    user = "Person: %s" % name
    context_bits = [b for b in (title_hint, company_hint) if b]
    if context_bits:
        user += "\nKnown context from the person searching: %s" % ", ".join(context_bits)
    if linkedin_url:
        user += "\nA LinkedIn URL was provided directly: %s" % linkedin_url
    if apollo_candidate:
        user += ("\nA business database suggests this may be: %s, %s at %s "
                 "(LinkedIn: %s). Confirm or correct this against what you find."
                 % (apollo_candidate.get("full_name") or name,
                    apollo_candidate.get("title") or "unknown title",
                    apollo_candidate.get("organization_name") or "an unknown company",
                    apollo_candidate.get("linkedin_url") or "none on file"))

    res = claude_websearch.ask(_SYSTEM, user, max_uses=8, max_tokens=3000)
    spend = claude_websearch.spend_of(res)

    if res.get("error"):
        err = res["error"]
        logger.warning("thought_leader_pr: identity lookup failed for %r (%s: %s)",
                       name, err["kind"], err["detail"])
        return {"ok": False, "confidence": "none",
                "reasoning": "The identity lookup could not run: %s."
                             % claude_websearch.reader_reason(err),
                "identity": None, "spend": spend, "error": err}

    if not res.get("search_count"):
        return {"ok": False, "confidence": "none",
                "reasoning": "The lookup was answered without a single search being run, "
                             "so this name was recalled rather than verified.",
                "identity": None, "spend": spend, "error": None}

    parsed = claude_websearch.extract_json(res.get("text") or "", require="confidence")
    if not isinstance(parsed, dict):
        logger.warning("thought_leader_pr: unparsable reply for %r (stop_reason=%s, chars=%s)",
                       name, res.get("stop_reason"), len(res.get("text") or ""))
        return {"ok": False, "confidence": "none",
                "reasoning": "The identity lookup returned an unreadable response.",
                "identity": None, "spend": spend,
                "error": {"kind": claude_websearch.ERR_UNPARSABLE, "detail": (res.get("text") or "")[:400]}}

    confidence = str(parsed.get("confidence") or "none").lower()
    if confidence not in ("high", "medium", "low", "none"):
        confidence = "none"
    reasoning = claude_websearch.strip_em_dash(str(parsed.get("reasoning") or ""))[:800]
    is_public_figure = bool(parsed.get("is_public_figure", True))

    if confidence not in _MIN_CONFIDENCE or not parsed.get("full_name") or not is_public_figure:
        return {"ok": False, "confidence": confidence,
                "reasoning": reasoning or "Could not confidently identify a public figure by this name.",
                "identity": None, "spend": spend, "error": None}

    _clean = claude_websearch.strip_em_dash
    full_name = _clean((parsed.get("full_name") or name).strip())
    facts = [_clean(str(f))[:240] for f in (parsed.get("disambiguating_facts") or [])
             if str(f).strip()][:4]

    resolved_linkedin_url = (linkedin_url or (apollo_candidate or {}).get("linkedin_url")
                             or (parsed.get("linkedin_url") or "").strip() or None)
    resolved_x_handle = (x_handle or _handle_from_url((apollo_candidate or {}).get("twitter_url"))
                         or (parsed.get("x_handle") or "").strip().lstrip("@") or None)
    resolved_instagram_handle = (parsed.get("instagram_handle") or "").strip().lstrip("@") or None

    linkedin_platform = _resolve_linkedin_platform(resolved_linkedin_url)
    identity = {
        "full_name": full_name,
        "headline": _clean((parsed.get("headline") or "").strip()) or linkedin_platform.get("headline"),
        "current_title": _clean((parsed.get("current_title") or "").strip())
                         or (apollo_candidate or {}).get("title"),
        "current_company": _clean((parsed.get("current_company") or "").strip())
                           or (apollo_candidate or {}).get("organization_name"),
        "photo_url": linkedin_platform.get("photo_url") or (apollo_candidate or {}).get("photo_url"),
        "confidence": confidence,
        "reasoning": reasoning,
        "disambiguating_facts": facts,
        "platforms": {
            "linkedin": linkedin_platform,
            "x": _resolve_x_platform(resolved_x_handle),
            "youtube": _resolve_youtube_platform(full_name),
            "instagram": _resolve_instagram_platform(resolved_instagram_handle),
        },
        "source": {"apollo_matched": bool(apollo_candidate), "websearch_count": res.get("search_count")},
    }
    return {"ok": True, "confidence": confidence, "reasoning": reasoning,
            "identity": identity, "spend": spend, "error": None}


# ───────────────────────── Phase 1: owned-platform posts ─────────────────────────
#
# Collects each platform independently and never lets one's failure block the
# others -- the same fault isolation Social Media Intelligence's own
# per-platform collectors rely on (tracker/sci_pipeline.py). A platform with
# nothing to show is recorded in `errors`, never silently merged into the
# empty-but-succeeded case: "no posts because there is no connected LinkedIn
# account" and "no posts because this person genuinely doesn't post there"
# are different facts a reader needs told apart.
#
# Reuses each platform's existing, already-tested SCI adapter rather than
# writing a fourth copy of "call a vendor, normalize the response": LinkedIn
# via unipile_transport.fetch_posts + sci_source_linkedin_unipile.normalize
# (is_company=False is the one branch nothing in SCI itself ever exercises),
# X via tracker/sci_source_x.py's collect() unmodified (it is already
# handle-agnostic -- see this feature's own plan file), and YouTube via
# sci_youtube_client.list_recent_videos, which already returns the exact
# same shared post shape as the other two.

MAX_POSTS_PER_PLATFORM = 20


def _collect_linkedin_posts(platform: dict, max_posts: int) -> tuple[list[dict], str | None]:
    provider_id = (platform or {}).get("provider_id")
    if not provider_id:
        return [], "No verified LinkedIn profile to collect posts from."
    account_id = unipile_transport.account_for_platform("linkedin")
    if not account_id:
        return [], "No connected LinkedIn account is available to collect posts."
    try:
        raw = unipile_transport.fetch_posts(provider_id, "linkedin", is_company=False,
                                            max_posts=max_posts, strict=True, account_id=account_id)
        return sci_source_linkedin_unipile.normalize(raw), None
    except unipile_transport.UnipileTransportError as e:
        return [], str(e)
    except Exception as e:
        # Deliberately as broad as unipile_transport.fetch_posts's own catch
        # (see its docstring): normalize() runs on whatever shape the vendor
        # actually sent, and this function's caller (collect_posts_job) must
        # never let one platform's malformed response take the other two
        # platforms down with it. Before this, an exception here escaped
        # past the narrow `except UnipileTransportError` above straight to
        # collect_posts_job's own catch-all, wiping X's and YouTube's
        # already-collected posts too and reporting a blanket "unexpected
        # error" with the real cause visible only in the server log.
        logger.exception("thought_leader_pr: linkedin posts normalize crashed")
        return [], "Could not read LinkedIn's response (%s)." % type(e).__name__


def _collect_x_posts(platform: dict, max_posts: int) -> tuple[list[dict], str | None]:
    handle = (platform or {}).get("handle")
    if not handle:
        return [], "No X handle to collect posts from."
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        return [], "Apify is not configured on this deployment."
    try:
        return sci_source_x.collect(handle, token, max_posts=max_posts, strict=True), None
    except apify_transport.ApifyTransportError as e:
        return [], str(e)
    except Exception as e:
        # Same reasoning as _collect_linkedin_posts above: sci_source_x.collect
        # normalizes AFTER run_actor_and_wait returns, so a malformed item in
        # an otherwise-successful scrape raised past the narrow
        # ApifyTransportError catch and crashed the whole run.
        logger.exception("thought_leader_pr: x posts normalize crashed")
        return [], "Could not read X's response (%s)." % type(e).__name__


def _collect_youtube_posts(platform: dict, max_posts: int) -> tuple[list[dict], str | None]:
    channel_id = (platform or {}).get("channel_id")
    if not channel_id:
        return [], "No YouTube channel to collect videos from."
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return [], "YouTube is not configured on this deployment."
    # list_recent_videos never raises (see its own docstring) -- an empty
    # result and a vendor failure are indistinguishable from here, which is
    # the same limit every other caller of this function already accepts.
    videos = sci_youtube_client.list_recent_videos(channel_id, api_key, max_results=max_posts, days=365)
    if not videos:
        return [], "No recent videos found in the last year."
    return videos, None


def collect_posts_job(run_id: int, email: str, max_posts: int = MAX_POSTS_PER_PLATFORM) -> None:
    """Runs on a background thread started by the /collect route, mirroring
    sci_pipeline's job-thread convention (this app has no persistent worker
    queue, so a request that fetched three vendors live would time out
    before a Railway response could return). Always leaves the run in a
    terminal posts_status ('ready' or 'failed') -- never raises past this
    function, so a run can never be left stuck 'collecting' by anything
    short of the process itself dying (which _resolve_stale_posts covers on
    the next read)."""
    try:
        run = get_run(run_id, email)
        if not run or not run.get("identity"):
            save_posts_failed(run_id, email, "This run has no confirmed identity to collect posts for.")
            return
        platforms = (run["identity"] or {}).get("platforms") or {}

        posts: dict[str, list] = {}
        errors: dict[str, str] = {}

        li_posts, li_err = _collect_linkedin_posts(platforms.get("linkedin"), max_posts)
        posts["linkedin"] = li_posts
        if li_err:
            errors["linkedin"] = li_err

        x_posts, x_err = _collect_x_posts(platforms.get("x"), max_posts)
        posts["x"] = x_posts
        if x_err:
            errors["x"] = x_err

        yt_posts, yt_err = _collect_youtube_posts(platforms.get("youtube"), max_posts)
        posts["youtube"] = yt_posts
        if yt_err:
            errors["youtube"] = yt_err

        save_posts(run_id, email, posts, errors)
    except Exception as e:
        logger.exception("thought_leader_pr: collect_posts_job crashed for run %s", run_id)
        save_posts_failed(run_id, email, "An unexpected error stopped the collection (%s)."
                          % (str(e)[:160] or type(e).__name__))


# ───────────────────────── Phase 2: audience reaction ─────────────────────────
#
# Seven independent sources, combined into one read of "how does the room
# react": (1) real comments/replies on the person's OWN posts collected in
# Phase 1 (LinkedIn/X/YouTube), then six sources of what OTHER people
# independently post ABOUT them, never their own voice: (2) X
# (tracker/tlpr_x_pulse.py, a real X-wide search), (3) Reddit
# (tracker/tlpr_reddit_pulse.py, mention-threads plus each thread's own
# comment section), (4) LinkedIn (tracker/tlpr_linkedin_pulse.py, LinkedIn's
# own post search via the same Unipile account Phase 1 already uses), (5)
# TikTok (tracker/tlpr_tiktok_pulse.py, TikTok's own video search -- TikTok
# is not a Phase 1 owned platform), (6) Instagram
# (tracker/tlpr_instagram_pulse.py, the person's own Mentions tab -- also
# not a Phase 1 owned platform, and only readable when Phase 0 found an
# Instagram handle), and (7) Facebook (tracker/tlpr_facebook_pulse.py,
# keyword search plus comment-section reading, the one source here built on
# two vendors NOT already integrated elsewhere in this codebase -- a
# deliberate, user-confirmed decision, see that module's docstring).
#
# Only the first needs Phase 1's posts to already exist (comments are
# fetched per already-collected post); the other six need only the resolved
# identity and run independently of whether Phase 1 ever ran.
#
# Same fault isolation as Phase 1: one platform's fetch failing never
# blocks the others, and a failure here is recorded in `errors`, never
# conflated with the analysis job itself crashing (reaction_status='failed'
# via save_reaction_failed).

MAX_POSTS_FOR_REACTION = 5
MAX_COMMENTS_PER_POST = 20
MAX_COMMENTS_DIGEST = 80

_REACTION_SYSTEM = (
    "You analyze how people react to a public figure's own social media "
    "posts, for a PR/reputation research tool used by a marketing agency. "
    "You are given real comments and replies left on their recent posts "
    "across platforms: which platform, an excerpt of the text, and its "
    "engagement. Report what is genuinely there, including criticism, "
    "rather than a flattering summary.\n\n"
    "Ground everything in the comments you were given. Never infer a fact "
    "this data doesn't support, and never soften a recurring criticism into "
    "a neutral observation. If the comments are mostly generic reactions "
    "(\"nice post!\", emoji-only) with no real substance, say that "
    "plainly -- that is a real finding, not a failure.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"comment_sentiment": {"<comment_id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|"neutral", '
    '"detail": str, "comment_ids": [str, ...]}], '
    '"notable_comments": [{"comment_id": str, "why": str}]}\n\n'
    "Rules: \"verdict\" is ONE sentence a comms lead could repeat in a "
    "meeting. \"comment_sentiment\" must label EVERY comment id you were "
    "given. \"themes\" is 2-5 recurring reactions, each with a concrete "
    "\"detail\" (specific to these comments, never generic advice) and 1-4 "
    "supporting comment_ids copied exactly from the data. \"notable_comments\" "
    "is 2-4 comments genuinely worth reading directly (a strong compliment, "
    "a sharp criticism, a widely-liked reply), each citing a real "
    "comment_id and a one-sentence \"why\"."
)

_COMMENT_SENTIMENTS = ("positive", "neutral", "negative", "mixed")


def _top_engaged_posts(posts: list[dict], n: int) -> list[dict]:
    def _engagement(p):
        m = p.get("metrics") or {}
        return sum(int(m.get(k) or 0) for k in ("likes", "comments", "shares", "views"))
    return sorted(posts or [], key=_engagement, reverse=True)[:n]


def _collect_linkedin_comments(posts: list[dict], max_posts: int,
                               max_comments: int) -> tuple[list[dict], str | None]:
    top = _top_engaged_posts(posts, max_posts)
    if not top:
        return [], "No LinkedIn posts to read comments from."
    account_id = unipile_transport.account_for_platform("linkedin")
    if not account_id:
        return [], "No connected LinkedIn account is available."
    out: list[dict] = []
    for post in top:
        pid = post.get("platform_post_id")
        if not pid:
            continue
        try:
            data, err = unipile_client.list_comments(pid, account_id, limit=max_comments)
        except Exception:
            logger.exception("thought_leader_pr: linkedin comments fetch crashed for post %s", pid)
            continue
        if err is not None or not isinstance(data, dict):
            continue
        for c in (data.get("items") or [])[:max_comments]:
            author_details = c.get("author_details") or {}
            out.append({
                "platform": "linkedin",
                "comment_id": c.get("id") or c.get("comment_id"),
                "text": c.get("text") or "",
                "author": author_details.get("name") or c.get("author"),
                "posted_at": c.get("date"), "likes": c.get("reaction_counter"),
            })
    if not out:
        return [], "No comments could be read from their recent LinkedIn posts."
    return out, None


def _collect_x_replies(posts: list[dict], max_posts: int,
                       max_comments: int) -> tuple[list[dict], str | None]:
    top = _top_engaged_posts(posts, max_posts)
    if not top:
        return [], "No X posts to read replies from."
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        return [], "Apify is not configured on this deployment."
    out: list[dict] = []
    for post in top:
        pid = post.get("platform_post_id")
        if not pid:
            continue
        try:
            replies = apify_x_replies.collect(pid, token, max_replies=max_comments, strict=True)
        except apify_transport.ApifyTransportError as e:
            logger.warning("thought_leader_pr: x replies fetch failed for tweet %s: %s", pid, e)
            continue
        except Exception:
            # apify_x_replies.collect() normalizes AFTER its transport call
            # returns, same shape as sci_source_x.collect() -- a malformed
            # reply must skip only THIS post's replies, never crash the
            # whole reaction job over one tweet.
            logger.exception("thought_leader_pr: x replies normalize crashed for tweet %s", pid)
            continue
        for r in replies:
            out.append({"platform": "x", "comment_id": r.get("comment_id"), "text": r.get("text") or "",
                       "author": r.get("author"), "posted_at": r.get("posted_at"), "likes": r.get("likes")})
    if not out:
        return [], "No replies could be read from their recent X posts."
    return out, None


def _collect_youtube_comments(posts: list[dict], max_posts: int,
                              max_comments: int) -> tuple[list[dict], str | None]:
    top = _top_engaged_posts(posts, max_posts)
    if not top:
        return [], "No YouTube videos to read comments from."
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return [], "YouTube is not configured on this deployment."
    out: list[dict] = []
    for post in top:
        vid = post.get("platform_post_id")
        if not vid:
            continue
        for c in sci_youtube_client.list_video_comments(vid, api_key, max_results=max_comments):
            out.append({"platform": "youtube", "comment_id": c.get("comment_id"),
                       "text": c.get("text") or "", "author": c.get("author"),
                       "posted_at": c.get("posted_at"), "likes": c.get("likes")})
    if not out:
        return [], "No comments could be read from their recent YouTube videos."
    return out, None


def _digest_comments(comments: list[dict], max_items: int = MAX_COMMENTS_DIGEST) -> list[dict]:
    """What Claude actually reads. Ordered by likes so a cap drops the
    comments nobody engaged with rather than an arbitrary slice. The id
    given to the model is platform-prefixed (never just the raw vendor id)
    because a LinkedIn comment id and an X comment id share no namespace and
    could otherwise collide."""
    ordered = sorted(comments, key=lambda c: int(c.get("likes") or 0), reverse=True)[:max_items]
    out = []
    for i, c in enumerate(ordered):
        raw_id = c.get("comment_id") or str(i)
        out.append({"id": "%s:%s" % (c.get("platform"), raw_id), "platform": c.get("platform"),
                   "text": (c.get("text") or "")[:400], "likes": c.get("likes")})
    return out


def _clean_reaction_analysis(parsed: dict, valid_ids: set[str]) -> dict:
    """Same discipline as tlpr_reddit_pulse._clean_analysis: strip every
    comment id the model was not actually given, drop any theme left with
    no real citation, compute sentiment counts from the labels rather than
    trust a self-reported tally, and strip_em_dash every free-text field the
    model wrote -- this dict's "detail"/"why"/"verdict" strings are exactly
    the kind of model-authored prose that shipped a dash to a live report
    before b00d931 unified this fix."""
    _clean = claude_websearch.strip_em_dash

    def _cited(entries, *, name_key):
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = [str(i) for i in (entry.get("comment_ids") or []) if str(i) in valid_ids]
            name = _clean(str(entry.get(name_key) or "").strip())
            if not ids or not name:
                continue
            cleaned = dict(entry, comment_ids=ids[:4], **{name_key: name})
            if "detail" in cleaned:
                cleaned["detail"] = _clean(str(cleaned["detail"] or ""))
            out.append(cleaned)
        return out

    sentiment_raw = parsed.get("comment_sentiment")
    labels = {}
    if isinstance(sentiment_raw, dict):
        for cid, label in sentiment_raw.items():
            if str(cid) in valid_ids and label in _COMMENT_SENTIMENTS:
                labels[str(cid)] = label
    from collections import Counter
    counts = Counter(labels.values())
    total = sum(counts.values())

    notable = []
    for entry in (parsed.get("notable_comments") or []):
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("comment_id") or "")
        why = _clean(str(entry.get("why") or "").strip())
        if cid in valid_ids and why:
            notable.append({"comment_id": cid, "why": why[:300]})

    return {
        "verdict": _clean(str(parsed.get("verdict") or "").strip()),
        "sentiment": {
            "counts": {s: counts.get(s, 0) for s in _COMMENT_SENTIMENTS},
            "labelled": total,
            "negative_share": round(counts.get("negative", 0) / total, 3) if total else None,
        },
        "themes": _cited(parsed.get("themes"), name_key="label")[:5],
        "notable_comments": notable[:4],
    }


def analyze_comment_sentiment(comments: list[dict]) -> dict:
    """The judgement half for Phase 2's own-post comments. Never raises --
    returns {"error": ...} so a failed analysis still leaves the raw
    comment count intact and rendered."""
    if not comments:
        return {"error": "No comments were collected to analyze."}
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    digest = _digest_comments(comments)
    valid_ids = {d["id"] for d in digest}
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key, timeout=120.0, max_retries=1)
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            # 16000, not the 3000 this shipped with: MAX_COMMENTS_DIGEST is 80
            # items, and the prompt requires a sentiment label for every one
            # of them plus themes/notable_comments -- 3000 was tight enough
            # that a real run hit stop_reason=max_tokens and came back as an
            # "unreadable response" with no indication why. Same headroom now
            # given to every tlpr_*_pulse.analyze() for the same reason.
            max_tokens=16000, system=_REACTION_SYSTEM,
            messages=[{"role": "user",
                      "content": json.dumps({"comments": digest}, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("thought_leader_pr: comment sentiment analysis failed: %s", e)
        return {"error": "The comment sentiment analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = claude_websearch.extract_json(raw, require="verdict")
    if not isinstance(parsed, dict):
        stop_reason = getattr(resp, "stop_reason", None)
        logger.warning("thought_leader_pr: unparsable comment sentiment analysis "
                       "(stop_reason=%s, chars=%d)", stop_reason, len(raw))
        if stop_reason == "max_tokens":
            return {"error": "The comment sentiment analysis ran out of output budget before "
                             "it finished (stop_reason=max_tokens). Raise max_tokens in "
                             "analyze_comment_sentiment() or reduce MAX_COMMENTS_DIGEST."}
        return {"error": "The comment sentiment analysis returned an unreadable response."}
    return _clean_reaction_analysis(parsed, valid_ids)


def collect_reaction_job(run_id: int, email: str, max_posts: int = MAX_POSTS_FOR_REACTION,
                         max_comments: int = MAX_COMMENTS_PER_POST) -> None:
    """Runs on a background thread started by the /collect-reaction route,
    same job-thread convention as collect_posts_job. Always leaves the run
    in a terminal reaction_status ('ready' or 'failed')."""
    try:
        run = get_run(run_id, email)
        if not run or not run.get("identity"):
            save_reaction_failed(run_id, email, "This run has no confirmed identity to analyze.")
            return
        identity = run["identity"] or {}
        full_name = identity.get("full_name") or run.get("input_name") or ""
        company_hint = identity.get("current_company") or run.get("company_hint")
        x_handle = (identity.get("platforms") or {}).get("x", {}).get("handle")
        li_provider_id = (identity.get("platforms") or {}).get("linkedin", {}).get("provider_id")
        instagram_handle = (identity.get("platforms") or {}).get("instagram", {}).get("handle")
        posts = run.get("posts") or {}

        comments: list[dict] = []
        errors: dict[str, str] = {}

        li_comments, li_err = _collect_linkedin_comments(posts.get("linkedin") or [], max_posts, max_comments)
        comments.extend(li_comments)
        if li_err:
            errors["linkedin"] = li_err

        x_comments, x_err = _collect_x_replies(posts.get("x") or [], max_posts, max_comments)
        comments.extend(x_comments)
        if x_err:
            errors["x"] = x_err

        yt_comments, yt_err = _collect_youtube_comments(posts.get("youtube") or [], max_posts, max_comments)
        comments.extend(yt_comments)
        if yt_err:
            errors["youtube"] = yt_err

        comment_sentiment = analyze_comment_sentiment(comments) if comments else None

        try:
            reddit = tlpr_reddit_pulse.build_pulse(full_name, company_hint)
        except Exception:
            logger.exception("thought_leader_pr: reddit pulse crashed for run %s", run_id)
            reddit = {"note": "The Reddit conversation read could not be completed.", "thread_count": 0}

        try:
            x_pulse = tlpr_x_pulse.build_pulse(full_name, x_handle)
        except Exception:
            logger.exception("thought_leader_pr: x pulse crashed for run %s", run_id)
            x_pulse = {"note": "The X conversation read could not be completed.", "tweet_count": 0}

        try:
            linkedin_pulse = tlpr_linkedin_pulse.build_pulse(full_name, li_provider_id)
        except Exception:
            logger.exception("thought_leader_pr: linkedin pulse crashed for run %s", run_id)
            linkedin_pulse = {"note": "The LinkedIn conversation read could not be completed.", "post_count": 0}

        try:
            tiktok_pulse = tlpr_tiktok_pulse.build_pulse(full_name)
        except Exception:
            logger.exception("thought_leader_pr: tiktok pulse crashed for run %s", run_id)
            tiktok_pulse = {"note": "The TikTok conversation read could not be completed.", "video_count": 0}

        try:
            instagram_pulse = tlpr_instagram_pulse.build_pulse(full_name, instagram_handle)
        except Exception:
            logger.exception("thought_leader_pr: instagram pulse crashed for run %s", run_id)
            instagram_pulse = {"note": "The Instagram conversation read could not be completed.",
                               "mention_count": 0}

        try:
            facebook_pulse = tlpr_facebook_pulse.build_pulse(full_name)
        except Exception:
            logger.exception("thought_leader_pr: facebook pulse crashed for run %s", run_id)
            facebook_pulse = {"note": "The Facebook conversation read could not be completed.", "post_count": 0}

        reaction = {
            "comments_analyzed": len(comments),
            "comment_sentiment": comment_sentiment,
            "reddit": reddit,
            "x_pulse": x_pulse,
            "linkedin_pulse": linkedin_pulse,
            "tiktok_pulse": tiktok_pulse,
            "instagram_pulse": instagram_pulse,
            "facebook_pulse": facebook_pulse,
        }
        save_reaction(run_id, email, reaction, errors)
    except Exception as e:
        logger.exception("thought_leader_pr: collect_reaction_job crashed for run %s", run_id)
        save_reaction_failed(run_id, email, "An unexpected error stopped the analysis (%s)."
                             % (str(e)[:160] or type(e).__name__))


# ───────────────────────── Phase 3: earned media / press ─────────────────────────
#
# What real news coverage exists about this person, via tracker/tlpr_press.py
# (GDELT + SerpAPI). Needs only the confirmed identity -- unlike Phase 2's
# own-post comment sentiment, this does not depend on Phase 1's posts ever
# having been collected, so it can run in parallel with either earlier phase.

def collect_press_job(run_id: int, email: str) -> None:
    """Runs on a background thread started by the /collect-press route, same
    job-thread convention as collect_posts_job and collect_reaction_job.
    Always leaves the run in a terminal press_status ('ready' or
    'failed')."""
    try:
        run = get_run(run_id, email)
        if not run or not run.get("identity"):
            save_press_failed(run_id, email, "This run has no confirmed identity to search press for.")
            return
        identity = run["identity"] or {}
        full_name = identity.get("full_name") or run.get("input_name") or ""
        company_hint = identity.get("current_company") or run.get("company_hint")

        press = tlpr_press.build_press(full_name, company_hint)
        errors = press.pop("errors", {}) or {}
        save_press(run_id, email, press, errors)
    except Exception as e:
        logger.exception("thought_leader_pr: collect_press_job crashed for run %s", run_id)
        save_press_failed(run_id, email, "An unexpected error stopped the press search (%s)."
                          % (str(e)[:160] or type(e).__name__))


# ───────────────────────── Phase 4: synthesis report ─────────────────────────
#
# The "so what": one Claude call that reads the ALREADY-ANALYZED findings
# from Phases 1-3 (never the raw posts/comments/articles again) and writes
# a single reputation verdict. This is deliberately kept inline here rather
# than spun into its own tlpr_*.py sibling module -- unlike Phase 2/3, there
# is no new vendor integration here, just recombining this run's own
# already-grounded conclusions, so it belongs with the rest of this run's
# orchestration rather than as a new "collector."
#
# Because every input claim was already grounded and citation-checked in its
# own phase (comment_ids/thread_ids/article_ids), this step's own output
# does not re-cite raw ids -- it tags each bullet with which of
# identity/posts/reaction/press it drew from instead. Mislabeling that tag
# is a cosmetic badge error, not a fabricated citation that would render as
# a link to something that doesn't exist, so it does not need the same
# valid_ids stripping every other Claude-authored citation in this codebase
# gets.

_SOURCE_TAGS = ("identity", "posts", "reaction", "press")

_SYNTHESIS_SYSTEM = (
    "You write the final reputation synthesis for a public figure, for a "
    "PR/reputation research tool used by a marketing agency. You are given "
    "the person's identity, their posting activity (a count and total "
    "engagement per platform -- NOT what they post about, that is not "
    "given to you, so never describe the content, tone, or style of their "
    "posts), and the ALREADY-ANALYZED findings from two separate reads: how "
    "people react to their own posts and the Reddit conversation about "
    "them, and what recent press coverage says. Each of those was already "
    "grounded in real data and independently verified; your job is to weave "
    "their conclusions into ONE readable synthesis, not to re-analyze raw "
    "evidence.\n\n"
    "Any input marked unavailable has no real data behind it -- write "
    "around it plainly (e.g. \"no press coverage was found\") rather than "
    "inventing a reading for it, and never imply a source was checked when "
    "it was unavailable.\n\n"
    "Be specific to this person, never generic advice that could apply to "
    "anyone. If reaction and press genuinely diverge (praised online but "
    "criticized in the press, or vice versa) or notably agree, say so "
    "plainly in \"alignment\" -- that tension or consistency is itself the "
    "most useful thing this report can surface.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"headline": str, "verdict": str, '
    '"strengths": [{"text": str, "sources": ["identity"|"posts"|"reaction"|"press", ...]}], '
    '"risks": [{"text": str, "sources": [...]}], '
    '"alignment": str}\n\n'
    "Rules: \"headline\" is a punchy 6-12 word label for their current PR "
    "position, not a full sentence. \"verdict\" is 2-3 sentences, the read "
    "a comms lead would actually want. \"strengths\" is 2-5 concrete "
    "positives, each tagged with which input(s) it genuinely comes from. "
    "\"risks\" is 0-5 concrete risks or watch items -- empty list if none "
    "are genuinely present, never invented to fill the field. \"alignment\" "
    "is 1-2 sentences comparing how they present themselves against how "
    "the room and the press actually receive them; say plainly if there "
    "isn't enough data yet to compare."
)


def _platform_post_stats(posts_list: list[dict] | None) -> dict:
    posts_list = posts_list or []
    engagement = sum(
        v for p in posts_list for v in (p.get("metrics") or {}).values()
        if isinstance(v, (int, float))
    )
    return {"count": len(posts_list), "total_engagement": engagement}


def _posts_summary(posts: dict | None) -> dict:
    posts = posts or {}
    by_platform = {pl: _platform_post_stats(posts.get(pl)) for pl in ("linkedin", "x", "youtube")}
    return {"available": any(s["count"] for s in by_platform.values()), "by_platform": by_platform}


def _reaction_summary(reaction: dict | None) -> dict:
    if not reaction:
        return {"available": False}
    cs = reaction.get("comment_sentiment") or {}
    reddit = reaction.get("reddit") or {}
    r_analysis = reddit.get("analysis") or {}
    out: dict = {"available": bool(reaction.get("comments_analyzed") or reddit.get("thread_count")),
                "own_post_comments": None, "reddit": None}
    if cs.get("verdict") and not cs.get("error"):
        out["own_post_comments"] = {
            "verdict": cs.get("verdict"),
            "sentiment_counts": (cs.get("sentiment") or {}).get("counts"),
            "themes": [t.get("label") for t in (cs.get("themes") or []) if t.get("label")],
        }
    if reddit.get("thread_count") and not r_analysis.get("error"):
        out["reddit"] = {
            "verdict": r_analysis.get("verdict"),
            "sentiment_counts": (r_analysis.get("sentiment") or {}).get("counts"),
            "themes": [t.get("label") for t in (r_analysis.get("themes") or []) if t.get("label")],
            "risk_flags": r_analysis.get("risk_flags") or [],
        }
    return out


def _press_summary(press: dict | None) -> dict:
    if not press or not press.get("article_count"):
        return {"available": False}
    a = press.get("analysis") or {}
    out: dict = {"available": True, "article_count": press.get("article_count"),
                "source_count": press.get("source_count")}
    if a.get("verdict") and not a.get("error"):
        out.update({
            "verdict": a.get("verdict"),
            "sentiment_counts": (a.get("sentiment") or {}).get("counts"),
            "themes": [t.get("label") for t in (a.get("themes") or []) if t.get("label")],
            "risk_flags": a.get("risk_flags") or [],
        })
    return out


def _extract_json_object(raw: str) -> str | None:
    """Same string-aware brace scan as tlpr_press._extract_json_object."""
    start = raw.find("{")
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None


def _clean_synthesis(parsed: dict) -> dict:
    """strip_em_dash every free-text field, same discipline as every other
    Claude-authored field in this codebase since b00d931, and drop any
    "sources" tag the model invented outside the fixed set -- see the
    module-level comment above for why this needs no id-level grounding."""
    _clean = claude_websearch.strip_em_dash

    def _bullets(items, cap):
        out = []
        for entry in items if isinstance(items, list) else []:
            if not isinstance(entry, dict):
                continue
            text = _clean(str(entry.get("text") or "").strip())
            if not text:
                continue
            sources = [s for s in (entry.get("sources") or []) if s in _SOURCE_TAGS]
            out.append({"text": text, "sources": sources})
        return out[:cap]

    return {
        "headline": _clean(str(parsed.get("headline") or "").strip())[:140],
        "verdict": _clean(str(parsed.get("verdict") or "").strip()),
        "strengths": _bullets(parsed.get("strengths"), 5),
        "risks": _bullets(parsed.get("risks"), 5),
        "alignment": _clean(str(parsed.get("alignment") or "").strip()),
    }


def synthesize_report(run: dict) -> dict:
    """The judgement half for Phase 4. Never raises -- returns
    {"error": ...} so collect_synthesis_job can tell a real failure apart
    from a real (however thin) report."""
    if not run or not run.get("identity"):
        return {"error": "This run has no confirmed identity to synthesize."}
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    identity = run["identity"] or {}
    payload = {
        "identity": {
            "full_name": identity.get("full_name"), "headline": identity.get("headline"),
            "current_title": identity.get("current_title"),
            "current_company": identity.get("current_company"),
        },
        "posts": _posts_summary(run.get("posts")),
        "reaction": _reaction_summary(run.get("reaction")),
        "press": _press_summary(run.get("press")),
    }
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key, timeout=120.0, max_retries=1)
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=2500, system=_SYNTHESIS_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("thought_leader_pr: synthesize_report failed: %s", e)
        return {"error": "The report generation could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    candidate = _extract_json_object(raw)
    if candidate is None:
        return {"error": "The report generation returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The report generation returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The report generation returned an unexpected shape."}
    return _clean_synthesis(parsed)


def collect_synthesis_job(run_id: int, email: str) -> None:
    """Runs on a background thread started by the /collect-synthesis route,
    same job-thread convention as the other three collect_*_job functions.
    Always leaves the run in a terminal synthesis_status ('ready' or
    'failed')."""
    try:
        run = get_run(run_id, email)
        if not run or not run.get("identity"):
            save_synthesis_failed(run_id, email, "This run has no confirmed identity to synthesize.")
            return
        report = synthesize_report(run)
        if report.get("error"):
            save_synthesis_failed(run_id, email, report["error"])
            return
        save_synthesis(run_id, email, report, {})
    except Exception as e:
        logger.exception("thought_leader_pr: collect_synthesis_job crashed for run %s", run_id)
        save_synthesis_failed(run_id, email, "An unexpected error stopped the report generation (%s)."
                              % (str(e)[:160] or type(e).__name__))
