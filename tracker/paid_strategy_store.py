"""Paid Marketing Agents: Strategy & Campaign Book, what is saved.

One table in the platform's Postgres, prefixed pm_ like the rest of Paid Marketing's:

  pm_strategies   one build: who asked, the inputs, the steps and where they are, what the research read, the plan written so far, what it cost

A build is saved a piece at a time (each research card as it finishes, each stage of the writing as it finishes), so a build that is
interrupted by a deploy keeps what it has and can pick up where it stopped. The rules for what a build may hold are
tracker/paid_strategy.py and tracker/paid_strategy_check.py; this file only reads and writes. It borrows the connection from
tracker/paid_store.py and raises its StoreUnavailable, so the routes handle it as every other Paid Marketing store.
"""
from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager

from . import paid_store, paid_strategy as ps

StoreUnavailable = paid_store.StoreUnavailable

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_strategies (
  id            text PRIMARY KEY,
  created_by    text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  name          text NOT NULL DEFAULT '',
  account_id    text NOT NULL DEFAULT '',
  account_name  text NOT NULL DEFAULT '',
  inputs        jsonb NOT NULL DEFAULT '{}'::jsonb,
  status        text NOT NULL DEFAULT 'queued',
  progress      jsonb NOT NULL DEFAULT '{}'::jsonb,
  research      jsonb NOT NULL DEFAULT '{}'::jsonb,
  plan          jsonb NOT NULL DEFAULT '{}'::jsonb,
  cost          jsonb NOT NULL DEFAULT '{}'::jsonb,
  note          text NOT NULL DEFAULT '',
  error         text NOT NULL DEFAULT '',
  started       timestamptz,
  archived      boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS pm_strategies_recent ON pm_strategies (created_at DESC) WHERE NOT archived;
CREATE TABLE IF NOT EXISTS pm_strategy_runs (
  id     bigserial PRIMARY KEY,
  email  text NOT NULL,
  at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pm_strategy_runs_by ON pm_strategy_runs (email, at DESC);
"""

COLS = ("id, created_by, created_at, updated_at, name, account_id, account_name, inputs, status, progress, research, plan, cost, note, error, started, archived, "
        "EXTRACT(EPOCH FROM (now() - updated_at))")
LIGHT = "id, status, progress, error, note, updated_at, started, EXTRACT(EPOCH FROM (now() - updated_at)), COALESCE(inputs->>'kind', 'strategy')"
LISTED = ("id, created_by, created_at, updated_at, name, account_id, account_name, inputs, status, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, cost, note, error, started, archived, "
          "EXTRACT(EPOCH FROM (now() - updated_at)), COALESCE(jsonb_array_length(CASE WHEN jsonb_typeof(plan->'campaigns') = 'array' THEN plan->'campaigns' END), 0)")
_READY = False
_READY_LOCK = threading.Lock()


def ensure_tables():
    global _READY
    if _READY:
        return
    with _READY_LOCK:
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


@contextmanager
def _tx():
    ensure_tables()
    conn = paid_store._connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _iso(v):
    return v.isoformat() if v else None


def _stale(status, age):
    """A build that says it is running but has not saved a thing, not even its heartbeat, for STALE_AFTER_S."""
    return status == "running" and age is not None and float(age) > ps.STALE_AFTER_S


def _rec(r):
    out = {"id": r[0], "created_by": r[1], "created_at": _iso(r[2]), "updated_at": _iso(r[3]), "name": r[4], "account_id": r[5], "account_name": r[6],
           "inputs": r[7] or {}, "status": r[8], "progress": r[9] or {}, "research": r[10] or {}, "plan": r[11] or {}, "cost": r[12] or {},
           "note": r[13] or "", "error": r[14] or "", "started": _iso(r[15]), "archived": bool(r[16]), "stale": _stale(r[8], r[17])}
    if len(r) > 18:
        out["campaign_count"] = int(r[18] or 0)
    return out


def _j(value):
    """JSON for jsonb: control characters out (Postgres refuses a NUL in jsonb), anything odd written as text."""
    return json.dumps(ps.scrub(value), default=str)


def _t(text):
    return ps.scrub(str(text or ""))


def create(*, by, inputs, account_name="", steps=None):
    """A new build, queued. Returns the record. `steps` are the build's steps when it is not a Strategy & Campaign Book (Business Research has its own)."""
    sid = uuid.uuid4().hex
    with _tx() as cur:
        cur.execute("INSERT INTO pm_strategies (id, created_by, name, account_id, account_name, inputs, progress) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING " + COLS,
                    (sid, by, inputs["name"], inputs.get("account") or "", account_name or "", _j(inputs),
                     _j({"steps": steps if steps is not None else ps.steps_for(inputs), "phase": "Queued"})))
        return _rec(cur.fetchone())


def get(sid):
    with _tx() as cur:
        cur.execute("SELECT " + COLS + " FROM pm_strategies WHERE id = %s", (sid,))
        row = cur.fetchone()
    return _rec(row) if row else None


def get_light(sid):
    """Just the status and progress (and which agent the build belongs to), for the page's polling (the research and the plan can be large)."""
    with _tx() as cur:
        cur.execute("SELECT " + LIGHT + " FROM pm_strategies WHERE id = %s AND NOT archived", (sid,))
        r = cur.fetchone()
    return {"id": r[0], "status": r[1], "progress": r[2] or {}, "error": r[3] or "", "note": r[4] or "", "updatedAt": _iso(r[5]), "started": _iso(r[6]),
            "stale": _stale(r[1], r[7]), "kind": r[8]} if r else None


def list_recent(limit=100, kind="strategy"):
    """The saved builds of one agent for the list ("strategy" is Strategy & Campaign Book, a build with no kind; "business" is Business Research): everything but
    the research and the plan (tens of megabytes for a hundred builds), with the campaigns counted."""
    with _tx() as cur:
        cur.execute("SELECT " + LISTED + " FROM pm_strategies WHERE NOT archived AND COALESCE(inputs->>'kind', 'strategy') = %s ORDER BY created_at DESC LIMIT %s", (kind, limit))
        return [_rec(r) for r in cur.fetchall()]


def begin(sid, *, resume=False):
    """Claim the right to run this build. True if this caller now owns a run; False if one is already going and has not gone stale (a run
    whose heartbeat has been silent for STALE_AFTER_S is taken to have died with its process), so two people pressing the button do not both start the model. A fresh run clears the old research, plan and cost; a resume keeps them."""
    keep = "" if resume else "research = '{}'::jsonb, plan = '{}'::jsonb, cost = '{}'::jsonb, "
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET status = 'running', error = '', note = '', " + keep + "started = now(), updated_at = now() "
                    "WHERE id = %s AND NOT archived AND (status <> 'running' OR updated_at < now() - make_interval(secs => %s)) RETURNING id",
                    (sid, ps.STALE_AFTER_S))
        return cur.fetchone() is not None


def touch(sid):
    """The heartbeat: a running build says it is alive."""
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET updated_at = now() WHERE id = %s AND status = 'running'", (sid,))


def record_run(email):
    with _tx() as cur:
        cur.execute("INSERT INTO pm_strategy_runs (email) VALUES (%s)", (email,))


def runs_today(email):
    """How many builds this person has started (or started again) in the last 24 hours, whichever server answered."""
    with _tx() as cur:
        cur.execute("SELECT count(*) FROM pm_strategy_runs WHERE email = %s AND at > now() - interval '24 hours'", (email,))
        return int(cur.fetchone()[0])


def set_progress(sid, progress):
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET progress = %s, updated_at = now() WHERE id = %s", (_j(progress), sid))


def set_steps(sid, steps, phase=None):
    with _tx() as cur:
        cur.execute("SELECT progress FROM pm_strategies WHERE id = %s FOR UPDATE", (sid,))
        row = cur.fetchone()
        prog = dict((row[0] if row else None) or {})
        prog["steps"] = steps
        if phase:
            prog["phase"] = phase
        cur.execute("UPDATE pm_strategies SET progress = %s, updated_at = now() WHERE id = %s", (_j(prog), sid))


def set_step(sid, key, state, note="", phase=None):
    """Change one step's state and note. Read, change and write under a row lock, because research steps finish on several threads at once."""
    with _tx() as cur:
        cur.execute("SELECT progress FROM pm_strategies WHERE id = %s FOR UPDATE", (sid,))
        row = cur.fetchone()
        if not row:
            return
        prog = dict(row[0] or {})
        steps = [dict(s) for s in prog.get("steps") or []]
        for s in steps:
            if s["key"] == key:
                s["state"], s["note"] = state, note[:400]
        prog["steps"] = steps
        if phase:
            prog["phase"] = phase
        cur.execute("UPDATE pm_strategies SET progress = %s, updated_at = now() WHERE id = %s", (_j(prog), sid))


def save_research(sid, key, piece):
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET research = jsonb_set(research, ARRAY[%s], %s::jsonb, true), updated_at = now() WHERE id = %s",
                    (key, _j(piece), sid))


def save_plan_part(sid, key, value):
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET plan = jsonb_set(plan, ARRAY[%s], %s::jsonb, true), updated_at = now() WHERE id = %s",
                    (key, _j(value), sid))


def save_plan(sid, plan):
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET plan = %s, updated_at = now() WHERE id = %s", (_j(plan), sid))


def finish(sid, *, cost, note=""):
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET status = 'done', error = '', note = %s, cost = %s, updated_at = now() WHERE id = %s",
                    (_t(note)[:600], _j(cost), sid))


def fail(sid, error, cost=None):
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET status = 'error', error = %s, cost = COALESCE(%s::jsonb, cost), updated_at = now() WHERE id = %s",
                    (_t(error)[:600], _j(cost) if cost is not None else None, sid))


def archive(sid):
    with _tx() as cur:
        cur.execute("UPDATE pm_strategies SET archived = true, updated_at = now() WHERE id = %s", (sid,))
