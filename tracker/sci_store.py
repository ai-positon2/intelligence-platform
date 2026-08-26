"""Postgres-backed storage for Social Creative Intelligence Analyst runs.

Mirrors tracker/linkedin_playbook_store.py's shape: a standalone _pg_conn()
(this Flask app runs on Railway with no persistent disk, so a run a user just
started must survive past the next deploy), lazy CREATE TABLE IF NOT EXISTS,
and every single-row read ownership-scoped in the query itself
(`WHERE id = %s AND email = %s`), never "fetch then check in Python".

Three tables: sci_runs (one row per analysis request), sci_platform_runs (one
row per platform per run -- its own status so one platform failing doesn't
blank out the others), sci_posts (one row per scraped post, with the Claude
creative-analysis result attached once it completes). sci_spend_log is
written by callers directly with plain INSERTs (see log_spend below) so cost
tracking exists from day one even though enforcement is a later phase.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TABLES_READY = False


def _pg_conn():
    """One-off Postgres connection. None if DATABASE_URL isn't configured or
    the connection fails -- callers treat that as 'not available'."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        return None
    try:
        import psycopg2
        return psycopg2.connect(database_url, connect_timeout=8)
    except Exception as e:
        logger.warning("sci_store: Postgres connection failed: %s", e)
        return None


def _ensure_tables(conn) -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sci_runs (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                company_name TEXT NOT NULL,
                company_url TEXT,
                company_logo TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                error TEXT,
                identify_result JSONB,
                synthesis JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_sci_runs_email
            ON sci_runs (email, created_at DESC)
        """)
        # Added after the table shipped (see linkedin_playbook_store's own
        # company_logo, always present from creation there): the picker's
        # candidates carry a real logo URL (tracker/sci_company_search.py,
        # Apollo-backed) that a run had no column to keep, so History fell
        # back to a plain monogram for every row.
        cur.execute("ALTER TABLE sci_runs ADD COLUMN IF NOT EXISTS company_logo TEXT")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sci_platform_runs (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES sci_runs(id),
                platform VARCHAR(20) NOT NULL,
                handle TEXT,
                handle_confidence VARCHAR(10),
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                status_detail TEXT,
                post_count INTEGER DEFAULT 0,
                last_post_at TIMESTAMPTZ,
                window_start DATE,
                window_end DATE,
                collected_at TIMESTAMPTZ,
                analyzed_at TIMESTAMPTZ,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (run_id, platform)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sci_posts (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES sci_runs(id),
                platform VARCHAR(20) NOT NULL,
                platform_post_id TEXT NOT NULL,
                post_url TEXT,
                post_type VARCHAR(20),
                caption TEXT,
                posted_at TIMESTAMPTZ,
                media_urls JSONB,
                metrics JSONB,
                raw JSONB,
                creative_analysis JSONB,
                creative_analysis_status VARCHAR(10) NOT NULL DEFAULT 'pending',
                creative_analysis_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (run_id, platform, platform_post_id)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_sci_posts_run_platform
            ON sci_posts (run_id, platform)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sci_spend_log (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES sci_runs(id),
                platform VARCHAR(20),
                vendor VARCHAR(20),
                operation VARCHAR(30),
                units NUMERIC,
                cost_usd NUMERIC,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
    conn.commit()
    _TABLES_READY = True


def _ts(d: dict, *keys: str) -> None:
    for k in keys:
        if d.get(k) is not None:
            d[k] = d[k].isoformat()


# ── Runs ───────────────────────────────────────────────────────────────────

_RUN_COLUMNS = ["id", "email", "company_name", "company_url", "company_logo", "status", "error",
                "identify_result", "synthesis", "created_at", "updated_at"]


def save_run(email: str, company_name: str, company_url: str | None = None,
            company_logo: str | None = None) -> int | None:
    """Create a new run row with status='running'. Returns the new row's id,
    or None on any failure. `company_logo` is the logo URL from whichever
    picker candidate (see tracker/sci_company_search.py) the user selected,
    if any -- carried through so History can show a real logo instead of a
    monogram for every run, same as linkedin_playbook_store.save_run."""
    conn = _pg_conn()
    if not conn:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sci_runs (email, company_name, company_url, company_logo) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (email.lower(), company_name, company_url, company_logo),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        logger.warning("sci_store: save_run failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_run_status(run_id: int, status: str, error: str | None = None,
                      identify_result: dict | None = None, synthesis: dict | None = None) -> bool:
    """Update a run's top-level status. Deliberately not ownership-scoped by
    email -- the background worker already knows run_id from having created
    the row itself, and never accepts a caller-supplied run_id."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        from psycopg2.extras import Json
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sci_runs SET status = %s, error = %s, "
                "identify_result = COALESCE(%s, identify_result), "
                "synthesis = COALESCE(%s, synthesis), updated_at = now() "
                "WHERE id = %s",
                (status, error, Json(identify_result) if identify_result is not None else None,
                 Json(synthesis) if synthesis is not None else None, run_id),
            )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("sci_store: update_run_status failed for run %s: %s", run_id, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_runs(email: str, limit: int = 100) -> list[dict]:
    """This user's own runs, newest first. [] on any failure."""
    conn = _pg_conn()
    if not conn or not email:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_RUN_COLUMNS)} FROM sci_runs "
                "WHERE email = %s ORDER BY created_at DESC LIMIT %s",
                (email.lower(), limit),
            )
            rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(zip(_RUN_COLUMNS, row))
            _ts(d, "created_at", "updated_at")
            out.append(d)
        return out
    except Exception as e:
        logger.warning("sci_store: list_runs failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def search_known_companies(email: str, query: str, limit: int = 8) -> list[dict]:
    """Companies this user has already analyzed, matching `query` by name --
    the fallback for when the Arena company-search vendor (used to
    disambiguate an ambiguous name like "apple" before a run starts, see
    app.py's search route) is unavailable. Ownership-scoped in the query
    itself, like every other read here. Shaped like an arena_client company
    dict (see arena_client._to_company) so the frontend can render either
    source with the same card renderer; `from_history: True` marks it as a
    past run rather than a live vendor result. [] on any failure."""
    conn = _pg_conn()
    q = (query or "").strip()
    if not conn or not email or not q:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            # DISTINCT ON collapses repeat analyses of the same company to
            # its most recent row, so re-analyzing Nike five times still
            # offers one Nike card.
            cur.execute(
                "SELECT DISTINCT ON (company_name) company_name, company_url, company_logo "
                "FROM sci_runs WHERE email = %s AND company_name ILIKE %s "
                "ORDER BY company_name, created_at DESC LIMIT %s",
                (email.lower(), "%" + q + "%", limit),
            )
            rows = cur.fetchall()
        out = []
        for row in rows:
            name, url, logo = row[0], row[1], row[2]
            if not name:
                continue
            out.append({"id": "", "name": name, "logo": logo, "industry": None,
                       "location": None, "description": None, "summary": None,
                       "followers_count": None, "profile_url": None,
                       "website": url, "from_history": True})
        return out
    except Exception as e:
        logger.warning("sci_store: search_known_companies failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_run(run_id: int, email: str) -> dict | None:
    """One run, ownership-scoped in the query itself. Returns None for a run
    that doesn't exist AND for one that belongs to a different email,
    identically -- callers must 404 either way, never reveal which."""
    conn = _pg_conn()
    if not conn or not email:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_RUN_COLUMNS)} FROM sci_runs "
                "WHERE id = %s AND email = %s",
                (run_id, email.lower()),
            )
            row = cur.fetchone()
        if not row:
            return None
        d = dict(zip(_RUN_COLUMNS, row))
        _ts(d, "created_at", "updated_at")
        return d
    except Exception as e:
        logger.warning("sci_store: get_run failed for run %s: %s", run_id, e)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Platform runs ─────────────────────────────────────────────────────────

_PLATFORM_RUN_COLUMNS = ["id", "run_id", "platform", "handle", "handle_confidence", "status",
                         "status_detail", "post_count", "last_post_at", "window_start",
                         "window_end", "collected_at", "analyzed_at", "error",
                         "created_at", "updated_at"]


def upsert_platform_run(run_id: int, platform: str, **fields: Any) -> int | None:
    """Create-or-update the one row for (run_id, platform). Not ownership-
    scoped -- the background worker already knows its own run_id. Accepts any
    subset of: handle, handle_confidence, status, status_detail, post_count,
    last_post_at, window_start, window_end, collected_at, analyzed_at, error."""
    allowed = {"handle", "handle_confidence", "status", "status_detail", "post_count",
              "last_post_at", "window_start", "window_end", "collected_at",
              "analyzed_at", "error"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    conn = _pg_conn()
    if not conn:
        return None
    try:
        _ensure_tables(conn)
        cols = ["run_id", "platform"] + list(fields.keys())
        vals = [run_id, platform] + list(fields.values())
        placeholders = ", ".join(["%s"] * len(vals))
        update_clause = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields.keys())
        update_clause = (update_clause + ", " if update_clause else "") + "updated_at = now()"
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO sci_platform_runs ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT (run_id, platform) DO UPDATE SET {update_clause} "
                "RETURNING id",
                vals,
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        logger.warning("sci_store: upsert_platform_run failed for run %s/%s: %s", run_id, platform, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_platform_runs(run_id: int) -> list[dict]:
    """Not ownership-scoped by itself -- callers must have already resolved
    run_id through get_run(run_id, email) before calling this, same as
    get_posts below."""
    conn = _pg_conn()
    if not conn:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_PLATFORM_RUN_COLUMNS)} FROM sci_platform_runs "
                "WHERE run_id = %s ORDER BY platform",
                (run_id,),
            )
            rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(zip(_PLATFORM_RUN_COLUMNS, row))
            _ts(d, "last_post_at", "window_start", "window_end", "collected_at",
                "analyzed_at", "created_at", "updated_at")
            out.append(d)
        return out
    except Exception as e:
        logger.warning("sci_store: get_platform_runs failed for run %s: %s", run_id, e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Posts ──────────────────────────────────────────────────────────────────

def upsert_posts(run_id: int, platform: str, posts: list[dict]) -> int:
    """Bulk-insert scraped posts, keyed on (run_id, platform, platform_post_id)
    so a re-scrape doesn't duplicate rows. Each post dict: platform_post_id,
    post_url, post_type, caption, posted_at (ISO string or None), media_urls
    (list), metrics (dict), raw (dict). Returns the number of rows written."""
    if not posts:
        return 0
    conn = _pg_conn()
    if not conn:
        return 0
    try:
        _ensure_tables(conn)
        from psycopg2.extras import Json
        written = 0
        with conn.cursor() as cur:
            for p in posts:
                pid = str(p.get("platform_post_id") or "").strip()
                if not pid:
                    continue
                cur.execute(
                    "INSERT INTO sci_posts (run_id, platform, platform_post_id, post_url, "
                    "post_type, caption, posted_at, media_urls, metrics, raw) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (run_id, platform, platform_post_id) DO UPDATE SET "
                    "post_url = EXCLUDED.post_url, metrics = EXCLUDED.metrics, "
                    "raw = EXCLUDED.raw, updated_at = now()",
                    (run_id, platform, pid, p.get("post_url"), p.get("post_type"),
                     p.get("caption"), p.get("posted_at"), Json(p.get("media_urls") or []),
                     Json(p.get("metrics") or {}), Json(p.get("raw") or {})),
                )
                written += 1
        conn.commit()
        return written
    except Exception as e:
        logger.warning("sci_store: upsert_posts failed for run %s/%s: %s", run_id, platform, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


_POST_COLUMNS = ["id", "run_id", "platform", "platform_post_id", "post_url", "post_type",
                 "caption", "posted_at", "media_urls", "metrics", "raw", "creative_analysis",
                 "creative_analysis_status", "creative_analysis_error", "created_at", "updated_at"]


def get_posts(run_id: int, platform: str | None = None) -> list[dict]:
    """Not ownership-scoped by itself -- callers resolve run_id through
    get_run(run_id, email) first."""
    conn = _pg_conn()
    if not conn:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            if platform:
                cur.execute(
                    f"SELECT {', '.join(_POST_COLUMNS)} FROM sci_posts "
                    "WHERE run_id = %s AND platform = %s ORDER BY posted_at DESC NULLS LAST",
                    (run_id, platform),
                )
            else:
                cur.execute(
                    f"SELECT {', '.join(_POST_COLUMNS)} FROM sci_posts "
                    "WHERE run_id = %s ORDER BY platform, posted_at DESC NULLS LAST",
                    (run_id,),
                )
            rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(zip(_POST_COLUMNS, row))
            _ts(d, "posted_at", "created_at", "updated_at")
            out.append(d)
        return out
    except Exception as e:
        logger.warning("sci_store: get_posts failed for run %s: %s", run_id, e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_post_creative_analysis(post_id: int, analysis: dict, status: str = "ok",
                                  error: str | None = None) -> bool:
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        from psycopg2.extras import Json
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sci_posts SET creative_analysis = %s, creative_analysis_status = %s, "
                "creative_analysis_error = %s, updated_at = now() WHERE id = %s",
                (Json(analysis) if analysis is not None else None, status, error, post_id),
            )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("sci_store: update_post_creative_analysis failed for post %s: %s", post_id, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Spend log ──────────────────────────────────────────────────────────────

def log_spend(run_id: int | None, platform: str | None, vendor: str, operation: str,
             units: float | None = None, cost_usd: float | None = None) -> None:
    """Best-effort spend instrumentation -- never raises, never blocks the
    pipeline. Enforcement (checking accumulated spend against a cap) is a
    later phase; this just makes sure the data exists to enforce against."""
    conn = _pg_conn()
    if not conn:
        return
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sci_spend_log (run_id, platform, vendor, operation, units, cost_usd) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (run_id, platform, vendor, operation, units, cost_usd),
            )
        conn.commit()
    except Exception as e:
        logger.warning("sci_store: log_spend failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
