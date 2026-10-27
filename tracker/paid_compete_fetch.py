"""Paid Marketing Agents: Competitor Analysis, reading the competitors' ads (the network is passed in, so all of it is tested without one).

Neither platform offers a way to read other advertisers' ads through an account, so the two public libraries are read by Apify scrapers the platform already pays for (tracker/apify_transport.py):

  Meta    the Meta Ad Library, by the competitor's Facebook page: its active ads in every country (actor apify/facebook-ads-scraper)
  Google  the Ads Transparency Center, by the competitor's website (every ad that sends people to it) or its advertiser id, for one country, with the text of text ads read from the picture
          (actor silva95gustavo/google-ads-scraper). Both actors were run for real and their output is what the normalisers in paid_compete_math.py read.

A read costs money (about half a cent an ad on Meta, two tenths of a cent on Google), so it is never automatic: the team presses Refresh. What was read is kept per competitor in the monitors'
store for CACHE_DAYS and shared by every account that names the same competitor. Either actor can be replaced by setting CA_APIFY_META_ACTOR_ID or CA_APIFY_GOOGLE_ACTOR_ID, but a
replacement must give the same shape (the normalisers say what they read).

A read that fails is kept as a failure with its reason and never as "no ads": an empty answer from a platform that was reached is kept as an empty read, and the report says which it was.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import paid_compete_math as cm
from . import paid_compete_settings as cs

ACTORS = {"meta": ("CA_APIFY_META_ACTOR_ID", "apify/facebook-ads-scraper"), "google": ("CA_APIFY_GOOGLE_ACTOR_ID", "silva95gustavo/google-ads-scraper")}
LIMIT = {"meta": 30, "google": 30}                    # ads read per competitor and platform
USD_PER_AD = {"meta": 0.005, "google": 0.0016}        # what the actors charge at the time of writing (Apify's own tier, shown to the team as "about")
CACHE_DAYS = 7
TIMEOUT_S = 420
WORKERS = 3
JOB_KEEP = 8
LOCK_TTL_S = 25 * 60
PLATFORM_NAME = {"google": "Google", "meta": "Meta"}


def actor_id(platform):
    env, default = ACTORS[platform]
    return os.environ.get(env, "").strip() or default


def cache_key(platform, comp, region):
    """Where a read is kept: Meta by the Facebook page, Google by the country and the advertiser id or website."""
    if platform == "meta":
        return "compete:ads:meta:" + (comp.get("facebook") or "").lower()
    return "compete:ads:google:%s:%s" % (region, (comp.get("googleAdvertiser") or comp.get("domain") or "").lower())


def start_url(platform, comp, region):
    """The address the actor is given for a competitor on a platform, or None when the competitor gives nothing to look up there."""
    if platform == "meta":
        return comp.get("facebook") or None
    if comp.get("googleAdvertiser"):
        return "https://adstransparency.google.com/advertiser/%s?region=%s" % (comp["googleAdvertiser"], region)
    if comp.get("domain"):
        return "https://adstransparency.google.com/?region=%s&domain=%s" % (region, comp["domain"])
    return None


def actor_input(platform, url):
    if platform == "meta":
        return {"startUrls": [{"url": url}], "resultsLimit": LIMIT["meta"], "activeStatus": "active"}
    return {"startUrls": [{"url": url}], "resultsLimit": LIMIT["google"], "ocr": True}


def skipped_reason(platform, comp, region):
    if start_url(platform, comp, region):
        return ""
    if platform == "meta":
        return "No Facebook page is set, so Meta's Ad Library was not searched."
    return "No website or advertiser id is set, so Google's Ads Transparency Center was not searched."


def estimate(competitors, platforms, region):
    """{reads, ads, usd}: what a refresh of every competitor on `platforms` can cost at most (the number of ads asked for at the actors' price)."""
    reads = [(p, c) for c in competitors for p in platforms if start_url(p, c, region)]
    ads = sum(LIMIT[p] for p, c in reads)
    return {"reads": len(reads), "ads": ads, "usd": round(sum(LIMIT[p] * USD_PER_AD[p] for p, c in reads), 2)}


# ── One read ────────────────────────────────────────────────────────────────

def read_one(platform, comp, region, *, run_fn, token, now):
    """{ads, error, skipped, fetchedAt, items} for one competitor on one platform. `run_fn(actor_id, input, token, timeout)` returns the actor's rows and raises when the run fails."""
    stamp = now.isoformat(timespec="seconds")
    why = skipped_reason(platform, comp, region)
    if why:
        return {"ads": [], "error": "", "skipped": why, "fetchedAt": stamp, "items": 0}
    try:
        rows = run_fn(actor_id(platform), actor_input(platform, start_url(platform, comp, region)), token, TIMEOUT_S)
    except Exception as e:                                                           # noqa: BLE001 (a failed read is said with its reason, never read as "no ads")
        return {"ads": [], "error": "The %s read failed: %s" % (PLATFORM_NAME[platform], str(e)[:200]), "skipped": "", "fetchedAt": stamp, "items": 0}
    norm = cm.meta_ad if platform == "meta" else cm.google_ad
    ads = [a for a in (norm(r, asof=now.date()) for r in rows or [] if isinstance(r, dict)) if a]
    return {"ads": ads, "error": "", "skipped": "", "fetchedAt": stamp, "items": len(rows or [])}


def cached(store, platform, comp, region):
    return store.state_get(cache_key(platform, comp, region))


def fresh(entry, now):
    try:
        return bool(entry) and not entry.get("error") and datetime.fromisoformat(entry["fetchedAt"]) > now - timedelta(days=CACHE_DAYS)
    except (KeyError, ValueError):
        return False


def entries_for(store, competitors, platforms, region):
    """The competitors' ads as the analysis takes them: one entry per competitor and platform, from what was read. A competitor never read says so."""
    out = []
    for c in competitors:
        for p in platforms:
            e = cached(store, p, c, region)
            if e is None:
                why = skipped_reason(p, c, region)
                out.append({"name": c["name"], "platform": p, "ads": [], "fetchedAt": "", "error": "", "skipped": why or "Not read yet: press Refresh competitor ads."})
            else:
                out.append({"name": c["name"], "platform": p, "ads": e.get("ads") or [], "fetchedAt": e.get("fetchedAt") or "", "error": e.get("error") or "", "skipped": e.get("skipped") or ""})
    return out


# ── A refresh ───────────────────────────────────────────────────────────────

def job_of(store, account_key):
    return store.state_get("compete:job:" + account_key)


def refresh(store, account_key, competitors, platforms, region, *, run_fn, token, who, now_fn=None, force=False, only=None):
    """Read every competitor on `platforms` that has no read newer than CACHE_DAYS (all of them with `force`; only those named with `only`), a few at a time, keeping the job's progress in the
    store as it goes. Returns the job ({status: busy} when one is already running for the account)."""
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    lock = "lock:compete:" + account_key
    if not store.claim(lock, LOCK_TTL_S):
        return {"status": "busy"}
    started = now_fn()
    pairs = [(p, c) for c in competitors if not only or c["name"] in only for p in platforms if start_url(p, c, region)]          # a competitor with nothing to look up on a platform is not a read
    todo = [(p, c) for p, c in pairs if force or not fresh(cached(store, p, c, region), started)]
    job = {"status": "running", "startedAt": started.isoformat(timespec="seconds"), "by": who, "total": len(todo), "skippedFresh": len(pairs) - len(todo),
           "steps": [{"name": c["name"], "platform": p, "status": "waiting", "ads": 0, "error": ""} for p, c in todo]}
    store.state_set("compete:job:" + account_key, job)
    try:
        def one(i):
            p, c = todo[i]
            job["steps"][i]["status"] = "reading"
            store.state_set("compete:job:" + account_key, job)
            res = read_one(p, c, region, run_fn=run_fn, token=token, now=now_fn())
            if not res["error"] or not (cached(store, p, c, region) or {}).get("ads"):
                store.state_set(cache_key(p, c, region), dict(res, name=c["name"]))             # a failed read does not replace the last good one
            job["steps"][i].update(status="failed" if res["error"] else "done", ads=len(res["ads"]), error=res["error"])
            store.state_set("compete:job:" + account_key, job)
        with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(todo) or 1))) as pool:
            list(pool.map(one, range(len(todo))))
        job.update(status="done", finishedAt=now_fn().isoformat(timespec="seconds"))
    except Exception as e:                                                           # noqa: BLE001
        job.update(status="failed", finishedAt=now_fn().isoformat(timespec="seconds"), error=("%s: %s" % (type(e).__name__, e))[:300])
    finally:
        store.state_set("compete:job:" + account_key, job)
        store.release(lock)
    return job
