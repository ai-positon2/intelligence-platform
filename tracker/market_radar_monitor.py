"""Market Radar, Phase 8: weekly updates on a schedule, inside the web app.

Each client with weekly updates switched on names a weekday and an hour
(UTC). Every TICK_S seconds the scheduler finds the clients whose latest
scheduled slot has not been started yet and starts ONE of them: a normal
collection that ends by writing the "what changed" update and sending it
(tracker/market_radar_digest).

Why it is safe to run in the web process:

  * Gunicorn runs two workers, each with this thread. A Postgres advisory
    lock lets one tick at a time act; the other skips.
  * A slot is marked as started BEFORE its collection starts, so a crash
    or a deploy mid-run never starts the same week twice. A run that dies
    leaves the week without an update, and the page says when the last one
    was sent.
  * Nothing starts while another collection is running, so a backlog after
    a deploy drains one client per tick instead of all at once.
  * MR_MONITOR_DISABLED=1 on the deployment stops it entirely, and nothing
    is ever scheduled for a client until someone switches it on.

Not a cron: a deploy at the slot's hour only delays that week's update to
the next tick after the app is back.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

TICK_S = 600
LOCK_KEY = 0x4D520008
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_started = False
_guard = threading.Lock()


def slot_for(now, weekday, hour):
    """The latest moment at or before `now` that falls on `weekday`
    (0 = Monday) at `hour`:00 UTC."""
    base = now.replace(minute=0, second=0, microsecond=0, hour=hour)
    back = (now.weekday() - weekday) % 7
    slot = base - timedelta(days=back)
    if slot > now:
        slot -= timedelta(days=7)
    return slot


def next_slot(now, weekday, hour):
    return slot_for(now, weekday, hour) + timedelta(days=7)


def due(settings, now):
    """The slot to start now, or None."""
    mon = (settings or {}).get("monitor") or {}
    if mon.get("enabled") is not True:
        return None
    slot = slot_for(now, int(mon.get("weekday", 0)), int(mon.get("hour", 7)))
    last = mon.get("last_slot")
    if last and last >= slot.isoformat():
        return None
    return slot


def tick(*, store=None, now=None, start=None):
    """One look at the schedule. Returns what it did, for the log and tests."""
    if store is None:
        from . import market_radar_store as store
    if start is None:
        from . import market_radar_run as mrun

        def start(client_id, owner):
            return mrun.start_collect(client_id, owner, monitor="send")
    now = now or datetime.now(timezone.utc)
    with store.advisory_lock(LOCK_KEY) as mine:
        if not mine:
            return {"skipped": "another worker holds the schedule"}
        if store.running_collections():
            return {"skipped": "a collection is running"}
        for c in store.monitored_clients():
            slot = due(c["settings"], now)
            if slot is None:
                continue
            store.set_monitor_state(c["client_id"], {"last_slot": slot.isoformat(),
                                                     "last_started_at": now.isoformat()})
            try:
                run_id = start(c["client_id"], c["owner_email"])
            except Exception as e:
                logger.exception("market_radar_monitor: could not start client %s", c["client_id"])
                store.set_monitor_state(c["client_id"], {"last_error": "%s: %s" % (
                    type(e).__name__, str(e)[:200])})
                return {"failed": c["client_id"]}
            store.set_monitor_state(c["client_id"], {"last_run_id": run_id, "last_error": None})
            return {"started": c["client_id"], "run_id": run_id, "slot": slot.isoformat()}
    return {"idle": True}


def _loop():
    time.sleep(60)            # let the app finish starting
    while True:
        try:
            out = tick()
            if out.get("started") or out.get("failed"):
                logger.info("market_radar_monitor: %s", out)
        except Exception:
            logger.exception("market_radar_monitor: tick failed")
        time.sleep(TICK_S)


def enabled():
    return os.environ.get("MR_MONITOR_DISABLED", "") not in ("1", "true", "yes") and \
        "pytest" not in sys.modules and bool(os.environ.get("DATABASE_URL"))


def start_scheduler():
    """Start the schedule thread once per process (no-op when disabled)."""
    global _started
    with _guard:
        if _started or not enabled():
            return False
        _started = True
    threading.Thread(target=_loop, name="mr-monitor", daemon=True).start()
    return True
