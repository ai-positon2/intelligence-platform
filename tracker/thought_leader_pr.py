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

import logging
import os
from urllib.parse import urlparse

from datetime import datetime, timedelta, timezone

from tracker import (apify_transport, apollo_client, claude_websearch, sci_source_linkedin_unipile,
                     sci_source_x, sci_youtube_client, unipile_client, unipile_transport)

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
    import json
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
               "posts_updated_at, created_at, updated_at")


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
            return _resolve_stale_posts(_row_to_dict(row), email)
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
            return [_resolve_stale_posts(_row_to_dict(row), email) for row in cur.fetchall()]
    except Exception as e:
        logger.warning("thought_leader_pr: list_runs failed: %s", e)
        return []
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    (rid, input_name, company_hint, title_hint, status, confidence, reasoning,
     identity, error, posts_status, posts, posts_errors, posts_updated_at,
     created_at, updated_at) = row
    return {
        "id": rid, "input_name": input_name, "company_hint": company_hint,
        "title_hint": title_hint, "status": status, "confidence": confidence,
        "reasoning": reasoning, "identity": identity, "error": error,
        "posts_status": posts_status or "idle", "posts": posts, "posts_errors": posts_errors,
        "posts_updated_at": posts_updated_at.isoformat() if posts_updated_at else None,
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
    import json
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
    import json
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
    "people who share a name.\n"
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
    '"linkedin_url": str|null, "x_handle": str|null, '
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
    exact = [c for c in candidates
             if (c.get("full_name") or "").strip().lower() == name.strip().lower()]
    return (exact or candidates)[0]


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
    except unipile_transport.UnipileTransportError as e:
        return [], str(e)
    return sci_source_linkedin_unipile.normalize(raw), None


def _collect_x_posts(platform: dict, max_posts: int) -> tuple[list[dict], str | None]:
    handle = (platform or {}).get("handle")
    if not handle:
        return [], "No X handle to collect posts from."
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        return [], "Apify is not configured on this deployment."
    try:
        posts = sci_source_x.collect(handle, token, max_posts=max_posts, strict=True)
    except apify_transport.ApifyTransportError as e:
        return [], str(e)
    return posts, None


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
    except Exception:
        logger.exception("thought_leader_pr: collect_posts_job crashed for run %s", run_id)
        save_posts_failed(run_id, email, "An unexpected error stopped the collection.")
