"""Market Radar: one run from a URL to a stored competitor list, in the
background.

    run_id = start(url, owner_email)          # returns at once
    status(run_id, owner_email)               # poll

A run is profile (Phase 1) then competitor discovery (Phase 2). It takes one
to three minutes, longer than a web request may last (gunicorn's timeout is
120 s), so it runs on a daemon thread and reports through its mr_runs row:
`stage` while it works, `summary` when it ends. A thread that dies with the
process (a deploy) leaves the run "running"; status() says how long ago it
last moved so a stale run is visible as such, and the ledger keeps counting
its unfinished calls at their reservations.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback

logger = logging.getLogger(__name__)

STALE_AFTER_S = 600


def start(url, owner_email, *, reuse_profile=False, spawn=None):
    """Create the run and start it. Returns the run id. Raises ValueError for
    an unusable URL and store.StoreUnavailable when the database is down
    (nothing is spent in either case)."""
    from . import market_radar_store as store
    domain = store.normalize_domain(url)
    entity = store.upsert_entity(domain)
    client_id = store.upsert_client(owner_email, entity)
    run_id = store.create_run(client_id, owner_email, "baseline")
    store.update_run(run_id, status="running", stage="queued")
    args = (run_id, url, owner_email, client_id, entity, reuse_profile)
    if spawn is None:
        threading.Thread(target=job, args=args, name="mr-run-%s" % run_id, daemon=True).start()
    else:
        spawn(job, args)
    return run_id


def job(run_id, url, owner_email, client_id, entity_id, reuse_profile, *, client=None,
        discover=None, build_profile=None):
    from . import market_radar_ledger as ledger
    from . import market_radar_store as store
    from . import market_radar_profile as prof
    from . import market_radar_rivals as rivals
    discover = discover or rivals.discover
    build_profile = build_profile or prof.build_profile
    started = time.monotonic()

    def stage(name):
        try:
            store.update_run(run_id, stage=name)
        except Exception:
            logger.warning("market_radar_run %s: could not record stage %s", run_id, name)

    try:
        profile, profile_note = None, None
        if reuse_profile:
            profile = (store.get_entity(entity_id) or {}).get("profile")
            profile_note = "reused the stored profile" if profile else "no stored profile; built one"
        if not profile:
            stage("profile")
            built = build_profile(url, run_id=run_id, client=client)
            if built["status"] != "ok":
                store.update_run(run_id, status="failed", stage="profile",
                                 error="profile %s: %s" % (built["status"], built.get("error")),
                                 summary={"profile_status": built["status"],
                                          "profile_error": built.get("error"),
                                          "pages": built.get("pages")})
                return
            profile = built["profile"]
        # The user's corrections and radius, laid over the site's reading.
        from . import market_radar_views as views
        mine = store.get_client(client_id, owner_email) or {}
        profile = views.effective_profile(profile, mine.get("settings"))
        radius = mine.get("radius_km")
        result = discover(profile, run_id=run_id, client=client, progress=stage,
                          radius_km=float(radius) if radius is not None else None)
        result["run_id"] = run_id
        saved = []
        if result.get("competitors"):
            stage("save")
            saved = rivals.save(result, client_id=client_id, owner_email=owner_email)
        summary = {
            "profile": {k: profile.get(k) for k in ("name", "archetype", "hq", "hq_point",
                                                    "one_liner", "location_count",
                                                    "edited_fields")},
            "profile_note": profile_note,
            "result": result,
            "saved_entities": saved,
            "seconds": round(time.monotonic() - started, 1),
        }
        failed = result.get("status") == "failed"
        store.update_run(run_id, status="failed" if failed else "complete", stage="done",
                         error=str(result.get("error"))[:500] if failed else None,
                         summary=summary, coverage=result.get("coverage"))
    except Exception as e:
        logger.exception("market_radar_run %s failed", run_id)
        try:
            store.update_run(run_id, status="failed",
                             error="%s: %s" % (type(e).__name__, str(e)[:300]),
                             summary={"trace": traceback.format_exc()[-1500:]})
        except Exception:
            pass
    finally:
        try:
            ledger.abandon_open(run_id)
        except Exception:
            pass


def start_collect(client_id, owner_email, *, spawn=None):
    """Collect what this client's competitors did (Phase 3,
    tracker/market_radar_collect), in the background. Free: no model and no
    paid search. Returns the run id; raises PermissionError when the client
    is not this person's."""
    from . import market_radar_store as store
    if not store.get_client(client_id, owner_email):
        raise PermissionError("client %s is not yours or does not exist" % client_id)
    run_id = store.create_run(client_id, owner_email, "collect")
    store.update_run(run_id, status="running", stage="queued")
    args = (run_id, client_id, owner_email)
    if spawn is None:
        threading.Thread(target=collect_job, args=args, name="mr-collect-%s" % run_id,
                         daemon=True).start()
    else:
        spawn(collect_job, args)
    return run_id


def collect_job(run_id, client_id, owner_email, *, collect=None):
    from . import market_radar_store as store
    from . import market_radar_collect as mc
    collect = collect or mc.collect_client

    def stage(name):
        try:
            store.update_run(run_id, stage=name)
        except Exception:
            logger.warning("market_radar_run %s: could not record stage %s", run_id, name)

    try:
        result = collect(client_id, owner_email, run_id=run_id, progress=stage)
        store.update_run(run_id, status="complete", stage="done", summary=result,
                         coverage={"lines": result.get("coverage")})
    except Exception as e:
        logger.exception("market_radar_run collect %s failed", run_id)
        try:
            store.update_run(run_id, status="failed",
                             error="%s: %s" % (type(e).__name__, str(e)[:300]),
                             summary={"trace": traceback.format_exc()[-1500:]})
        except Exception:
            pass


def status(run_id, owner_email):
    from . import market_radar_ledger as ledger
    from . import market_radar_store as store
    run = store.get_run(run_id, owner_email)
    if not run:
        return None
    out = {k: run[k] for k in ("id", "status", "stage", "error", "summary")}
    out["ledger"] = ledger.summary(run_id)
    if run["status"] == "running":
        try:
            with store._tx() as cur:
                cur.execute("SELECT EXTRACT(EPOCH FROM now() - updated_at) FROM mr_runs WHERE id=%s",
                            (run_id,))
                idle = float(cur.fetchone()[0])
            out["idle_seconds"] = round(idle)
            if idle > STALE_AFTER_S:
                out["stale"] = "no progress for %d minutes; the worker probably restarted" % (idle // 60)
        except Exception:
            pass
    return out
