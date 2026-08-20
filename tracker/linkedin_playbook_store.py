"""Postgres-backed storage for LinkedIn Playbook Studio runs and playbooks.

Deliberately NOT sqlite3, unlike most tracker/*_store.py modules in this repo:
this Flask app runs on Railway with no persistent disk, and the sqlite pattern
those other stores use only works because their databases are periodically
rebuilt by a batch script and committed to git -- read-mostly caches, not live
per-request write targets. A user clicking "Analyze" and expecting that run to
still be there after the next deploy needs a real database. This reuses the
platform's own already-configured Postgres (DATABASE_URL / _pg_conn() in
app.py, used today by agent_run_history and cpi_search_history) rather than
adding a new one.

Every single-row read is ownership-scoped in the query itself
(`WHERE id = %s AND email = %s`), never "fetch then check in Python" -- this
is the direct fix for a prior standalone tool's IDOR, where its saved-run
lookup trusted a bare id with no ownership check at all.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TABLES_READY = False


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
        logger.warning("linkedin_playbook_store: Postgres connection failed: %s", e)
        return None


def _ensure_tables(conn) -> None:
    """CREATE TABLE IF NOT EXISTS, once per process. Concurrent gunicorn
    workers racing this on cold start is safe -- Postgres serializes the DDL."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lps_runs (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                parent_run_id INTEGER REFERENCES lps_runs(id),
                run_type TEXT NOT NULL,
                company_id TEXT NOT NULL,
                company_name TEXT NOT NULL,
                company_logo TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                error TEXT,
                summary TEXT,
                scorecard_score NUMERIC,
                output JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_lps_runs_email
            ON lps_runs (email, created_at DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_lps_runs_parent
            ON lps_runs (parent_run_id)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lps_playbooks (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES lps_runs(id),
                email TEXT NOT NULL,
                mode TEXT NOT NULL,
                content JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (run_id, mode)
            )
        """)
    conn.commit()
    _TABLES_READY = True


def _run_row_to_dict(row, columns: list[str]) -> dict:
    d = dict(zip(columns, row))
    for ts_key in ("created_at", "updated_at"):
        if d.get(ts_key) is not None:
            d[ts_key] = d[ts_key].isoformat()
    if d.get("scorecard_score") is not None:
        d["scorecard_score"] = float(d["scorecard_score"])
    return d


_THIN_COLUMNS = ["id", "email", "parent_run_id", "run_type", "company_id", "company_name",
                 "company_logo", "status", "error", "summary", "scorecard_score",
                 "created_at", "updated_at"]
_FULL_COLUMNS = _THIN_COLUMNS + ["output"]


def save_run(email: str, run_type: str, company_id: str, company_name: str,
            company_logo: str | None = None, parent_run_id: int | None = None) -> int | None:
    """Create a new run row with status='running'. Returns the new row's id,
    or None on any failure -- callers should treat that as 'could not start
    the analysis' and report a clear error, not silently proceed."""
    conn = _pg_conn()
    if not conn:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lps_runs (email, parent_run_id, run_type, company_id, company_name, company_logo) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (email.lower(), parent_run_id, run_type, company_id, company_name, company_logo),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        logger.warning("linkedin_playbook_store: save_run failed: %s", e)
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


def update_run_status(run_id: int, status: str, output: dict | None = None,
                      error: str | None = None, summary: str | None = None,
                      scorecard_score: float | None = None) -> bool:
    """Update a run's status once the background analysis job finishes (or
    fails). Deliberately not ownership-scoped by email -- the background
    worker that calls this already knows run_id from having created the row
    itself, and never accepts a caller-supplied run_id."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        from psycopg2.extras import Json
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE lps_runs SET status = %s, output = COALESCE(%s, output), "
                "error = %s, summary = COALESCE(%s, summary), "
                "scorecard_score = COALESCE(%s, scorecard_score), updated_at = now() "
                "WHERE id = %s",
                (status, Json(output) if output is not None else None, error, summary,
                 scorecard_score, run_id),
            )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("linkedin_playbook_store: update_run_status failed for run %s: %s", run_id, e)
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
    """This user's own runs, newest first, thin rows (no output blob -- the
    history list must not deserialize the full multi-agent result just to
    render a row). [] on any failure or if Postgres isn't configured."""
    conn = _pg_conn()
    if not conn or not email:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_THIN_COLUMNS)} FROM lps_runs "
                "WHERE email = %s ORDER BY created_at DESC LIMIT %s",
                (email.lower(), limit),
            )
            rows = cur.fetchall()
        return [_run_row_to_dict(r, _THIN_COLUMNS) for r in rows]
    except Exception as e:
        logger.warning("linkedin_playbook_store: list_runs failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_run(run_id: int, email: str) -> dict | None:
    """One run, full output included -- ownership-scoped in the query itself.
    Returns None for a run that doesn't exist AND for one that exists but
    belongs to a different email, identically -- callers must 404 either way,
    never reveal which."""
    conn = _pg_conn()
    if not conn or not email:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_FULL_COLUMNS)} FROM lps_runs "
                "WHERE id = %s AND email = %s",
                (run_id, email.lower()),
            )
            row = cur.fetchone()
        if not row:
            return None
        return _run_row_to_dict(row, _FULL_COLUMNS)
    except Exception as e:
        logger.warning("linkedin_playbook_store: get_run failed for run %s: %s", run_id, e)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_children(parent_run_id: int, email: str) -> list[dict]:
    """Competitor runs linked to one own-brand run, ownership-scoped the same
    way as get_run. [] on any failure."""
    conn = _pg_conn()
    if not conn or not email:
        return []
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_THIN_COLUMNS)} FROM lps_runs "
                "WHERE parent_run_id = %s AND email = %s ORDER BY created_at DESC",
                (parent_run_id, email.lower()),
            )
            rows = cur.fetchall()
        return [_run_row_to_dict(r, _THIN_COLUMNS) for r in rows]
    except Exception as e:
        logger.warning("linkedin_playbook_store: get_children failed for parent %s: %s", parent_run_id, e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_playbook(run_id: int, email: str, mode: str, content: dict) -> int | None:
    """Generate-or-regenerate a playbook for a run. Re-verifies the run
    belongs to `email` before writing -- never trusts that the caller already
    checked, since a playbook write is exactly the kind of side effect that
    must not happen against someone else's run. Upserts on (run_id, mode), so
    "regenerate" replaces rather than accumulates. Returns the playbook row's
    id, or None if the run isn't this user's or the write fails."""
    if get_run(run_id, email) is None:
        logger.warning("linkedin_playbook_store: save_playbook refused -- run %s not owned by caller", run_id)
        return None
    conn = _pg_conn()
    if not conn:
        return None
    try:
        _ensure_tables(conn)
        from psycopg2.extras import Json
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lps_playbooks (run_id, email, mode, content) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (run_id, mode) DO UPDATE SET "
                "content = EXCLUDED.content, created_at = now() "
                "RETURNING id",
                (run_id, email.lower(), mode, Json(content)),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        logger.warning("linkedin_playbook_store: save_playbook failed for run %s: %s", run_id, e)
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


def get_playbook(run_id: int, email: str, mode: str) -> dict | None:
    """A saved playbook for a run, ownership-scoped via the run itself (a
    playbook row's own `email` column is denormalized for this exact check,
    so no join back to lps_runs is needed for the common case, but the
    ownership guarantee comes from this WHERE clause, not from trusting the
    caller)."""
    conn = _pg_conn()
    if not conn or not email:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, run_id, mode, content, created_at FROM lps_playbooks "
                "WHERE run_id = %s AND email = %s AND mode = %s",
                (run_id, email.lower(), mode),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "run_id": row[1], "mode": row[2], "content": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
        }
    except Exception as e:
        logger.warning("linkedin_playbook_store: get_playbook failed for run %s: %s", run_id, e)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
