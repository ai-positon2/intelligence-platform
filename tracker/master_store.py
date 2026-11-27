"""Master Agent chat: where conversations, turns and files are kept (Postgres, the same database as the rest of Paid Marketing).

  pm_master_convos    one conversation: who owns it, its title, and the code sandbox (container) it keeps between turns
  pm_master_turns     one question and its answer: the question, the files that came with it, the live steps while it runs,
                      the answer, the files it made, the sources it read, and the status (running, done, failed)
  pm_master_files     the bytes of every file: uploads ("in") and files the agent made ("out"), owned by one conversation

A turn outlasts a web request and the platform runs more than one worker, so the page polls the turn and whichever worker answers
reads it from here (the same shape as Vimi's jobs). Only the owner of a conversation (or an admin) can read it or its files.
"""
from __future__ import annotations

import json
import threading
import uuid

from . import paid_store
from .paid_store import StoreUnavailable  # noqa: F401  (callers catch it from here)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_master_convos (
    id            TEXT PRIMARY KEY,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    title         TEXT NOT NULL DEFAULT '',
    container_id  TEXT,
    archived      BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS pm_master_convos_by ON pm_master_convos (created_by, updated_at);
CREATE TABLE IF NOT EXISTS pm_master_turns (
    id            TEXT PRIMARY KEY,
    convo_id      TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL,
    question      TEXT NOT NULL DEFAULT '',
    files_in      JSONB NOT NULL DEFAULT '[]'::jsonb,
    steps         JSONB NOT NULL DEFAULT '[]'::jsonb,
    answer        TEXT,
    files_out     JSONB NOT NULL DEFAULT '[]'::jsonb,
    sources       JSONB NOT NULL DEFAULT '[]'::jsonb,
    usage         JSONB NOT NULL DEFAULT '{}'::jsonb,
    error         TEXT
);
ALTER TABLE pm_master_turns ADD COLUMN IF NOT EXISTS draft TEXT;
CREATE INDEX IF NOT EXISTS pm_master_turns_convo ON pm_master_turns (convo_id, created_at);
CREATE INDEX IF NOT EXISTS pm_master_turns_by ON pm_master_turns (created_by, created_at);
CREATE TABLE IF NOT EXISTS pm_master_files (
    id            TEXT PRIMARY KEY,
    convo_id      TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    direction     TEXT NOT NULL,
    name          TEXT NOT NULL,
    mime          TEXT NOT NULL DEFAULT 'application/octet-stream',
    size          INTEGER NOT NULL DEFAULT 0,
    data          BYTEA,
    anthropic_id  TEXT
);
CREATE INDEX IF NOT EXISTS pm_master_files_convo ON pm_master_files (convo_id, created_at);
"""

_READY = False
_LOCK = threading.Lock()


def ensure():
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        conn = paid_store._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _READY = True


def _tx():
    ensure()
    return paid_store._tx()


def _iso(v):
    return v.isoformat() if v else None


# ── conversations ──────────────────────────────────────────────────────────

def create_convo(by, title=""):
    cid = uuid.uuid4().hex
    with _tx() as cur:
        cur.execute("INSERT INTO pm_master_convos (id, created_by, title) VALUES (%s, %s, %s)", (cid, by, (title or "")[:140]))
    return cid


def _convo_row(r):
    return {"id": r[0], "createdBy": r[1], "createdAt": _iso(r[2]), "updatedAt": _iso(r[3]), "title": r[4], "containerId": r[5], "archived": r[6]}


def get_convo(cid):
    with _tx() as cur:
        cur.execute("SELECT id, created_by, created_at, updated_at, title, container_id, archived FROM pm_master_convos WHERE id = %s", (cid,))
        r = cur.fetchone()
    return _convo_row(r) if r else None


def list_convos(by, limit=60):
    with _tx() as cur:
        cur.execute("SELECT id, created_by, created_at, updated_at, title, container_id, archived FROM pm_master_convos "
                    "WHERE created_by = %s AND NOT archived ORDER BY updated_at DESC LIMIT %s", (by, int(limit)))
        return [_convo_row(r) for r in cur.fetchall()]


def rename_convo(cid, title):
    with _tx() as cur:
        cur.execute("UPDATE pm_master_convos SET title = %s WHERE id = %s", ((title or "")[:140], cid))


def archive_convo(cid):
    with _tx() as cur:
        cur.execute("UPDATE pm_master_convos SET archived = true WHERE id = %s", (cid,))


def set_container(cid, container_id):
    with _tx() as cur:
        cur.execute("UPDATE pm_master_convos SET container_id = %s, updated_at = now() WHERE id = %s", (container_id, cid))


def touch_convo(cid):
    with _tx() as cur:
        cur.execute("UPDATE pm_master_convos SET updated_at = now() WHERE id = %s", (cid,))


# ── turns ──────────────────────────────────────────────────────────────────

TURN_COLS = "id, convo_id, created_by, created_at, finished_at, status, question, files_in, steps, answer, files_out, sources, usage, error, draft"


def _turn_row(r):
    return {"id": r[0], "convoId": r[1], "createdBy": r[2], "createdAt": _iso(r[3]), "finishedAt": _iso(r[4]), "status": r[5], "question": r[6],
            "filesIn": r[7] or [], "steps": r[8] or [], "answer": r[9], "filesOut": r[10] or [], "sources": r[11] or [], "usage": r[12] or {}, "error": r[13],
            "draft": r[14] if len(r) > 14 else None}


def create_turn(cid, by, question, files_in):
    tid = uuid.uuid4().hex
    with _tx() as cur:
        cur.execute("INSERT INTO pm_master_turns (id, convo_id, created_by, status, question, files_in) VALUES (%s, %s, %s, 'running', %s, %s)",
                    (tid, cid, by, (question or "")[:20000], json.dumps(files_in or [])))
        cur.execute("UPDATE pm_master_convos SET updated_at = now() WHERE id = %s", (cid,))
    return tid


def add_step(tid, step):
    with _tx() as cur:
        cur.execute("UPDATE pm_master_turns SET steps = steps || %s::jsonb WHERE id = %s", (json.dumps([step], default=str), tid))


def set_draft(tid, text):
    """The answer as it is being written, for the page to show while the turn runs."""
    with _tx() as cur:
        cur.execute("UPDATE pm_master_turns SET draft = %s WHERE id = %s AND status = 'running'", ((text or "")[:60000], tid))


def finish_turn(tid, *, answer=None, files_out=None, sources=None, usage=None, error=None):
    """Close a running turn. A turn the person stopped is already closed and is left as it is."""
    with _tx() as cur:
        cur.execute("UPDATE pm_master_turns SET status = %s, answer = %s, files_out = %s, sources = %s, usage = %s, error = %s, draft = NULL, finished_at = now() "
                    "WHERE id = %s AND status = 'running'",
                    ("failed" if error else "done", answer, json.dumps(files_out or []), json.dumps(sources or [], default=str), json.dumps(usage or {}), error, tid))
        return cur.rowcount > 0


def cancel_turn(tid):
    with _tx() as cur:
        cur.execute("UPDATE pm_master_turns SET status = 'failed', error = 'Stopped.', finished_at = now() WHERE id = %s AND status = 'running'", (tid,))
        return cur.rowcount > 0


def get_turn(tid):
    with _tx() as cur:
        cur.execute("SELECT %s FROM pm_master_turns WHERE id = %%s" % TURN_COLS, (tid,))
        r = cur.fetchone()
    return _turn_row(r) if r else None


def list_turns(cid, limit=200):
    with _tx() as cur:
        cur.execute("SELECT %s FROM pm_master_turns WHERE convo_id = %%s ORDER BY created_at ASC LIMIT %%s" % TURN_COLS, (cid, int(limit)))
        return [_turn_row(r) for r in cur.fetchall()]


def turn_counts(by, stale_s):
    """(turns still running, turns started in the last 24 hours) for one person; a turn older than stale_s is not running: its worker is gone."""
    with _tx() as cur:
        cur.execute("SELECT count(*) FILTER (WHERE status = 'running' AND created_at > now() - %s * interval '1 second'), "
                    "count(*) FILTER (WHERE created_at > now() - interval '1 day') FROM pm_master_turns WHERE created_by = %s", (stale_s, by))
        running, today = cur.fetchone()
    return int(running or 0), int(today or 0)


# ── files ──────────────────────────────────────────────────────────────────

def save_file(cid, by, direction, name, mime, data, anthropic_id=None):
    fid = uuid.uuid4().hex
    with _tx() as cur:
        cur.execute("INSERT INTO pm_master_files (id, convo_id, created_by, direction, name, mime, size, data, anthropic_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (fid, cid, by, direction, name[:200], mime[:120], len(data or b""), paid_store_binary(data), anthropic_id))
    return fid


def paid_store_binary(data):
    import psycopg2
    return psycopg2.Binary(data or b"")


def set_anthropic_id(fid, anthropic_id):
    with _tx() as cur:
        cur.execute("UPDATE pm_master_files SET anthropic_id = %s WHERE id = %s", (anthropic_id, fid))


def get_file(fid, with_data=True):
    cols = "id, convo_id, created_by, created_at, direction, name, mime, size, anthropic_id" + (", data" if with_data else "")
    with _tx() as cur:
        cur.execute("SELECT %s FROM pm_master_files WHERE id = %%s" % cols, (fid,))
        r = cur.fetchone()
    if not r:
        return None
    out = {"id": r[0], "convoId": r[1], "createdBy": r[2], "createdAt": _iso(r[3]), "direction": r[4], "name": r[5], "mime": r[6], "size": r[7], "anthropicId": r[8]}
    if with_data:
        out["data"] = bytes(r[9]) if r[9] is not None else b""
    return out


def list_files(cid, direction=None):
    with _tx() as cur:
        if direction:
            cur.execute("SELECT id, name, mime, size, anthropic_id, direction FROM pm_master_files WHERE convo_id = %s AND direction = %s ORDER BY created_at", (cid, direction))
        else:
            cur.execute("SELECT id, name, mime, size, anthropic_id, direction FROM pm_master_files WHERE convo_id = %s ORDER BY created_at", (cid,))
        return [{"id": r[0], "name": r[1], "mime": r[2], "size": r[3], "anthropicId": r[4], "direction": r[5]} for r in cur.fetchall()]
