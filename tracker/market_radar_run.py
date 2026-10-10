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
# A collection's stage after its competitors, shown by the moves card. Not a
# step of the competitor search's progress bar (STAGE_STEP in the page script).
RADAR_STAGE = "radar"
PULSE_STAGE = "pulse"
SIGNALS_STAGE = "signals"
REPORT_STAGE = "report"
DIGEST_STAGE = "digest"


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


def start_collect(client_id, owner_email, *, spawn=None, monitor=None):
    """Collect what this client's competitors did (Phase 3,
    tracker/market_radar_collect), in the background. Free: no model and no
    paid search. Returns the run id; raises PermissionError when the client
    is not this person's."""
    if monitor not in (None, "send", "preview"):
        raise ValueError("monitor must be None, 'send' or 'preview'")
    from . import market_radar_store as store
    if not store.get_client(client_id, owner_email):
        raise PermissionError("client %s is not yours or does not exist" % client_id)
    run_id = store.create_run(client_id, owner_email, "collect")
    store.update_run(run_id, status="running", stage="queued")
    args = (run_id, client_id, owner_email)
    if spawn is None:
        threading.Thread(target=collect_job, args=args, kwargs={"monitor": monitor},
                         name="mr-collect-%s" % run_id, daemon=True).start()
    else:
        spawn(collect_job, args) if monitor is None else spawn(
            lambda *a: collect_job(*a, monitor=monitor), args)
    return run_id


def _radar(client_id, owner_email, *, run_id=None):
    from . import market_radar_radar as mr
    return mr.run_for_client(client_id, owner_email, run_id=run_id)


def _pulse(client_id, owner_email, *, run_id=None):
    from . import market_radar_pulse as mp
    return mp.run_for_client(client_id, owner_email, run_id=run_id)


def _signals(client_id, owner_email, *, run_id=None):
    from . import market_radar_signals as ms
    return ms.run_for_client(client_id, owner_email, run_id=run_id)


def _report(client_id, owner_email, *, run_id=None, collection=None):
    from . import market_radar_report as mrep
    return mrep.write_report(client_id, owner_email, run_id=run_id, collection=collection)


def _digest(client_id, owner_email, *, run_id=None, send=True):
    """Phase 8: the "what changed" update, stored, and sent when `send`."""
    from . import market_radar_digest as md
    from . import market_radar_store as store
    digest_id, payload = md.make_digest(client_id, owner_email, run_id=run_id)
    out = {"digest_id": digest_id, "status": payload["status"],
           "items": len((payload.get("words") or {}).get("items") or [])}
    if send:
        settings = (store.get_client(client_id, owner_email) or {}).get("settings") or {}
        out["delivery"] = md.deliver(digest_id, client_id, payload, settings)
    return out


def collect_job(run_id, client_id, owner_email, *, collect=None, radar=None, pulse=None,
                signals=None, report=None, monitor=None, digest=None):
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
        # Phase 4: businesses like the client's opening nearby, or new brands
        # in its category. A radar that breaks does not lose the collection.
        stage(RADAR_STAGE)
        try:
            result["radar"] = (radar or _radar)(client_id, owner_email, run_id=run_id)
        except Exception as e:
            logger.exception("market_radar_run radar %s failed", run_id)
            result["radar"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}
        # Phase 5: the industry's news in the client's markets, grouped into
        # themes. Shared with every client in the same industry and country.
        stage(PULSE_STAGE)
        try:
            result["pulse"] = (pulse or _pulse)(client_id, owner_email, run_id=run_id)
        except Exception as e:
            logger.exception("market_radar_run pulse %s failed", run_id)
            result["pulse"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}
        # Phase 6: competitor headlines read into typed events, and every
        # event scored for this client. Last, so the radar's finds are scored.
        stage(SIGNALS_STAGE)
        try:
            result["signals"] = (signals or _signals)(client_id, owner_email, run_id=run_id)
        except Exception as e:
            logger.exception("market_radar_run signals %s failed", run_id)
            result["signals"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}
        # Phase 7: the written report, from everything above.
        stage(REPORT_STAGE)
        try:
            result["report"] = (report or _report)(client_id, owner_email, run_id=run_id,
                                                   collection=result)
        except Exception as e:
            logger.exception("market_radar_run report %s failed", run_id)
            result["report"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}
        if monitor:
            # Phase 8: a weekly (or "send now") run ends with the update.
            stage(DIGEST_STAGE)
            try:
                result["digest"] = (digest or _digest)(client_id, owner_email, run_id=run_id,
                                                       send=monitor == "send")
            except Exception as e:
                logger.exception("market_radar_run digest %s failed", run_id)
                result["digest"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}
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
