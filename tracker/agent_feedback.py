"""Postgres-backed storage for the thumbs up/down feedback control on
Strategic Agents' generated reports (Contact Finder, LinkedIn Strategy
Researcher, 42 North Dental Slot Checker, Social Media Intelligence, Event &
Conference Intelligence).

One shared table rather than one per agent: every agent's report is a
different shape (a tabbed drawer, a chat reply, a weekly briefing), but the
feedback itself is the same three facts every time -- who, which section of
which agent's output, and whether it helped -- so a single, agent-agnostic
table is the honest model rather than five copies of the same four columns.
`run_id` is stored as TEXT (not a foreign key into any agent's own run table)
because the five agents don't share an id space or even an id TYPE: LPS/SCI/
Event Intelligence use integer run ids, the Slot Checker's closest analogue is
a `generated_at` snapshot stamp, and Contact Finder's assistant chat has no
persisted run at all. This module knows nothing about any of those tables and
never joins into them -- it is deliberately the one place in the app that
does NOT need to understand five different report schemas to record an
opinion about one of them.

Follows the same connection/table-readiness pattern as
tracker/linkedin_playbook_store.py (own psycopg2 connection, no shared pool,
CREATE TABLE IF NOT EXISTS once per process) rather than reusing app.py's
_pg_conn to avoid importing app.py from tracker/, matching every other module
in this package.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TABLES_READY = False

RATINGS = ("up", "down")

# The only agents this control is wired into -- each one has an actual
# generated analysis worth rating, unlike the other five agents under
# Strategic Agents (raw Slack facts, a separate React app, a raw Sheet
# mirror, mock data, or a hidden external iframe). Kept here, not just in
# app.py, so a row can never be written for a slug nobody meant to enable.
AGENT_LABELS = {
    "company-people-intelligence": "Contact Finder",
    "linkedin-strategy-researcher": "LinkedIn Strategy Researcher",
    "42-north-dental-slot-checker": "42 North Dental Slot Checker",
    "social-media-intelligence": "Social Media Intelligence",
    "event-conference-intelligence": "Event & Conference Intelligence",
}


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
        logger.warning("agent_feedback: Postgres connection failed: %s", e)
        return None


def _ensure_tables(conn) -> None:
    """CREATE TABLE IF NOT EXISTS, once per process. Concurrent gunicorn
    workers racing this on cold start is safe -- Postgres serializes the DDL."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_feedback (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                agent_slug TEXT NOT NULL,
                run_id TEXT,
                section_key TEXT NOT NULL,
                section_label TEXT,
                rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
                reason TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_feedback_agent_created
            ON agent_feedback (agent_slug, created_at DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_feedback_agent_section
            ON agent_feedback (agent_slug, section_key, rating)
        """)
    conn.commit()
    _TABLES_READY = True


def save(*, email: str, agent_slug: str, run_id: str | None, section_key: str,
         section_label: str | None, rating: str, reason: str | None) -> int | None:
    """Insert one feedback row. Returns the new row's id, or None on any
    failure (no DATABASE_URL, bad rating, connection error) -- callers should
    treat that as 'not recorded' and fail silently/best-effort, same as every
    other logging path in this app (Sheets, Slack, agent_run_history)."""
    if rating not in RATINGS:
        return None
    conn = _pg_conn()
    if not conn:
        return None
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_feedback "
                "(email, agent_slug, run_id, section_key, section_label, rating, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (email.lower(), agent_slug, run_id, section_key, section_label or None,
                 rating, reason or None),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        logger.warning("agent_feedback: save failed: %s", e)
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


def update_reason(feedback_id: int, email: str, reason: str) -> bool:
    """Attach a reason typed AFTER the thumbs-down already submitted (the
    control posts the tap immediately, then lets someone add why without
    blocking on it -- see static/js/agent_feedback.js). Ownership-scoped in
    the query itself (WHERE id = %s AND email = %s), the same direct-fix
    pattern linkedin_playbook_store.py uses for its own prior IDOR, rather
    than fetching the row and checking in Python. Returns whether a row was
    actually updated, so a stale/foreign id can be told apart from a real
    write."""
    conn = _pg_conn()
    if not conn:
        return False
    try:
        _ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_feedback SET reason = %s "
                "WHERE id = %s AND email = %s AND rating = 'down'",
                (reason or None, feedback_id, email.lower()),
            )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        logger.warning("agent_feedback: update_reason failed: %s", e)
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


def list_recent(agent_slug: str | None = None, rating: str | None = None,
                 limit: int = 200) -> list[dict[str, Any]]:
    """Most recent feedback rows, newest first, for the admin review page.
    [] on any failure or if Postgres isn't configured -- the page just
    renders empty rather than erroring, same as every other admin list in
    this app."""
    conn = _pg_conn()
    if not conn:
        return []
    try:
        _ensure_tables(conn)
        clauses, params = [], []
        if agent_slug:
            clauses.append("agent_slug = %s")
            params.append(agent_slug)
        if rating in RATINGS:
            clauses.append("rating = %s")
            params.append(rating)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, agent_slug, run_id, section_key, section_label, "
                "rating, reason, created_at FROM agent_feedback " + where +
                " ORDER BY created_at DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
        return [{
            "id": r[0], "email": r[1], "agent_slug": r[2], "run_id": r[3],
            "section_key": r[4], "section_label": r[5], "rating": r[6],
            "reason": r[7], "created_at": r[8].isoformat(),
        } for r in rows]
    except Exception as e:
        logger.warning("agent_feedback: list_recent failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def summary(days: int = 30, agent_slug: str | None = None) -> list[dict[str, Any]]:
    """One row per (agent_slug, section_key) over the trailing window: up/down
    counts and the down share, so a reviewer can spot which section of which
    agent's report is actually being rejected rather than reading hundreds of
    individual taps. [] on any failure or if Postgres isn't configured."""
    conn = _pg_conn()
    if not conn:
        return []
    try:
        _ensure_tables(conn)
        where = "WHERE created_at > now() - (%s || ' days')::interval"
        params: list[Any] = [days]
        if agent_slug:
            where += " AND agent_slug = %s"
            params.append(agent_slug)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT agent_slug, section_key,
                       MAX(section_label) FILTER (WHERE section_label IS NOT NULL) AS section_label,
                       COUNT(*) FILTER (WHERE rating = 'up') AS up,
                       COUNT(*) FILTER (WHERE rating = 'down') AS down
                FROM agent_feedback
                """ + where + """
                GROUP BY agent_slug, section_key
                ORDER BY down DESC, up DESC
            """, params)
            rows = cur.fetchall()
        out = []
        for agent_slug, section_key, section_label, up, down in rows:
            total = up + down
            out.append({
                "agent_slug": agent_slug, "section_key": section_key,
                "section_label": section_label, "up": up, "down": down,
                "total": total,
                "down_pct": round(100 * down / total, 1) if total else 0.0,
            })
        return out
    except Exception as e:
        logger.warning("agent_feedback: summary failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
