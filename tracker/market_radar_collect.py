"""Market Radar, Phase 3: collect what every competitor of a client did.

    result = collect_client(client_id, owner_email, run_id=run_id)

For each competitor (confirmed first, then the agent's suggestions; removed
ones never), the company's website is read once (market_radar_site, no
model) and that one read feeds the detectors in market_radar_detectors,
market_radar_jobs and market_radar_news. Each detector's payload is stored
as a snapshot (market_radar_store.save_snapshot: an unchanged payload adds
no row), compared with the last good one, and the differences are stored as
events (record_event: one event per company and key, merged across
sources).

Costs nothing in model or search fees: every read is our own HTTP request
to a public page, feed or job board.

Snapshots belong to the COMPANY, not the client: when another client
collected the same competitor in the last REUSE_HOURS, its reads are reused
instead of fetched again, so the tenth client tracking Aspen Dental adds no
load on Aspen's site and no requests to Google.

The coverage that comes back says, per detector, what was read for how many
competitors and why the rest were not, in the words the report will use:
"Hiring read for 6 of 9 competitors; 3 have no public jobs board."
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from datetime import datetime, timedelta, timezone

from . import market_radar_b2b as b2b
from . import market_radar_detectors as det
from . import market_radar_jobs as jobs
from . import market_radar_linkedin as li
from . import market_radar_news as news

logger = logging.getLogger(__name__)

MAX_COMPETITORS = 12
PARALLEL = 3
REUSE_HOURS = 12
BUDGET_S = 25 * 60

# (name, read, compare, baseline, what it measures). Order matters: reviews
# reads the catalog's result to choose its product pages.
DETECTORS = [
    ("news", news.read_news, None, None, "News"),
    ("locations", det.read_locations, det.compare_locations, None, "Locations"),
    ("catalog", det.read_catalog, det.compare_catalog, det.baseline_catalog, "Products and prices"),
    ("reviews", det.read_reviews, det.compare_reviews, None, "Review counts"),
    ("promotions", det.read_promotions, det.compare_promotions, None, "Homepage offers"),
    ("pages", det.read_pages, det.compare_pages, None, "Key page changes"),
    ("newsroom", det.read_newsroom, det.compare_newsroom, det.baseline_newsroom, "Own news and blog"),
    ("jobs", jobs.read_jobs, jobs.compare_jobs, None, "Hiring"),
    # Phase 10: B2B companies and manufacturers (tracker/market_radar_b2b,
    # tracker/market_radar_linkedin).
    ("filings", b2b.read_filings, b2b.compare_filings, b2b.baseline_filings, "Stock-market filings"),
    ("registry", b2b.read_registry, b2b.compare_registry, b2b.baseline_registry,
     "UK company register"),
    ("subdomains", b2b.read_subdomains, b2b.compare_subdomains, b2b.baseline_subdomains,
     "New subdomains"),
    ("headcount", b2b.read_headcount, b2b.compare_headcount, b2b.baseline_headcount, "Headcount"),
    ("linkedin", li.read_linkedin, li.compare_linkedin, li.baseline_linkedin, "LinkedIn posts"),
]
NAMES = [d[0] for d in DETECTORS]
B2B = {"b2b_services", "b2b_product"}
# Which kinds of client each specialist detector runs for (the plan's
# module table); a detector not listed runs for every client. A dentist's
# rivals have no certificate-log story to tell, and every Apollo read or
# LinkedIn read spends a credit or a person's session.
FOR_ARCHETYPES = {
    "subdomains": B2B | {"ecommerce"},
    "headcount": B2B | {"manufacturer"},
    "linkedin": B2B | {"manufacturer"},
}
# How long a read stays good, in hours, where 12 is too often: headcount
# costs a credit and moves slowly, certificate logs take a minute to query.
REUSE = {"subdomains": 7 * 24, "headcount": 30 * 24, "linkedin": 7 * 24}


HOOKS = ("crt_connect", "apollo_enrich", "linkedin_collect", "linkedin_available")


def applies(name, archetype):
    return name not in FOR_ARCHETYPES or archetype in FOR_ARCHETYPES[name]
# Detectors that read the live page text: an archived copy of a page is not
# this week's page, so these are skipped when the site refuses us.
LIVE_TEXT = {"promotions", "pages"}
# Detectors that do not need the company's own website to answer. The
# registry and LinkedIn take an identifier from it, but remember it.
SITELESS = {"news", "filings", "subdomains", "headcount", "linkedin", "registry"}


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def _fresh(snap, now, name=None):
    seen = (snap or {}).get("last_seen_at")
    return bool(seen and now - seen < timedelta(hours=REUSE.get(name, REUSE_HOURS)))


def _site_note(rs):
    first = (rs.get("pages") or [{}])[0]
    return first.get("note") or rs.get("status") or "the website could not be read"


def collect_entity(entity, *, run_id=None, client_country=None, breaker=None, deadline=None,
                   now=None, only=None, io=None, store=None, archetype=None):
    """Run every detector on one company. `io` replaces the network
    (read_site, get, get_json, fetch) and `store` the database, for tests.
    Never raises for a detector's failure: it becomes that detector's row."""
    from . import market_radar_http as http
    from . import market_radar_site as site
    if store is None:
        from . import market_radar_store as store
    io = dict({"read_site": site.read_site, "get": http.get, "get_json": http.get_json,
               "fetch": site.fetch}, **(io or {}))
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    wanted = [d for d in DETECTORS if (only is None or d[0] in only) and applies(d[0], archetype)]
    latest = {d[0]: store.latest_snapshot(entity["id"], d[0]) for d in wanted}
    stale = [d for d in wanted if not _fresh(latest[d[0]], now, d[0])]
    ctx = {"entity": entity, "get": io["get"], "get_json": io["get_json"], "fetch": io["fetch"],
           "now": now, "client_country": client_country, "run_id": run_id,
           # Phase 10's outside services, replaceable in tests like the network.
           "hooks": {k: v for k, v in io.items() if k in HOOKS},
           "news_breaker": breaker or news.Breaker(),
           "prev": {k: (v or {}).get("payload") for k, v in latest.items()},
           "results": {}, "site": None}
    out = {"entity_id": entity["id"], "domain": entity["domain"], "name": entity.get("name"),
           "site": None, "rows": [], "events": []}
    if any(d[0] not in SITELESS for d in stale):
        rs = io["read_site"]("https://" + entity["domain"])
        ctx["site"] = rs
        out["site"] = {"status": rs.get("status"), "via_archive": bool(rs.get("via_archive")),
                       "note": None if rs.get("status") == "ok" else _site_note(rs),
                       "pages": sum(1 for p in rs.get("pages") or [] if p["status"] == "ok")}
        # Phase 2 stores a competitor by name only. Its own site says which
        # country it is in, which picks the news edition; kept when the
        # signals clearly agree, so the next collection need not guess.
        vote = rs.get("country") or {}
        if not entity.get("country") and vote.get("code") and vote.get("confidence", 0) >= 0.6:
            entity = dict(entity, country=vote["code"])
            ctx["entity"] = entity
            try:
                store.upsert_entity(entity["domain"], country=vote["code"])
            except Exception:
                logger.warning("market_radar_collect: could not store %s's country",
                               entity["domain"])
    for name, read, compare, baseline, _label in wanted:
        t0 = time.monotonic()
        row = {"detector": name, "status": None, "note": "", "items": 0, "snapshot": None,
               "events": 0, "reused": False}
        if not any(d[0] == name for d in stale):
            snap = latest[name]
            payload = snap["payload"] or {}
            # A reused read says what that read found: "none" stays none, so
            # coverage never counts "no jobs board" as hiring read.
            found = "none" if "absent" in payload else \
                ("empty" if not snap.get("item_count") else "ok")
            ctx["results"][name] = {"status": found, "payload": None if found == "none" else payload}
            row.update(status=found, reused=True, items=snap.get("item_count") or 0,
                       note="%s (read %s ago; not fetched again)" % (
                           payload.get("absent") or "%d items" % (snap.get("item_count") or 0),
                           _ago(now - snap["last_seen_at"])))
            out["rows"].append(row)
            continue
        if deadline is not None and time.monotonic() > deadline:
            row.update(status="skipped", note="the collection ran out of time")
            out["rows"].append(row)
            continue
        rs = ctx["site"]
        if name not in SITELESS and (rs is None or rs.get("status") != "ok"):
            row.update(status="failed", note="the website could not be read: " + _site_note(rs or {}))
            out["rows"].append(row)
            continue
        if name in LIVE_TEXT and rs.get("via_archive"):
            row.update(status="skipped", note="the site refuses this server; an archived copy "
                       "is not this week's page")
            out["rows"].append(row)
            continue
        try:
            result = read(ctx)
        except Exception as e:
            logger.exception("market_radar_collect: %s failed on %s", name, entity["domain"])
            result = {"status": "failed", "note": "the detector broke (%s)" % type(e).__name__,
                      "payload": None, "items": 0}
        ctx["results"][name] = result
        row.update(status=result["status"], note=result["note"], items=result.get("items") or 0)
        if result["status"] == "none":
            # "Checked: there is none" is a fact worth keeping (a shop that
            # appears later is news, and the company is not re-read for it
            # within the reuse window). A failure is not: it stores nothing.
            result = dict(result, payload={"absent": result["note"]})
        if result.get("payload") is not None and result["status"] in ("ok", "empty", "none"):
            try:
                events = _store(store, entity, name, result, compare, baseline, run_id, now,
                                ctx, row)
                out["events"].extend(events)
            except Exception as e:
                logger.exception("market_radar_collect: saving %s for %s failed", name,
                                 entity["domain"])
                row["note"] += "; saving failed (%s)" % type(e).__name__
        row["seconds"] = round(time.monotonic() - t0, 1)
        out["rows"].append(row)
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


def _store(store, entity, name, result, compare, baseline, run_id, now, ctx, row):
    payload = result["payload"]
    snap = store.save_snapshot(entity["id"], name, payload, item_count=result.get("items"),
                               run_id=run_id)
    previous_absent = "absent" in (snap.get("previous") or {})
    if "absent" in payload:
        row["snapshot"] = "first" if snap["first"] else ("changed" if snap["changed"] else "same")
        events = []
    elif snap["first"] or previous_absent:
        # Nothing to compare with: a first read, or the first read since
        # there was none (a shop or jobs board that has just appeared).
        row["snapshot"] = "first"
        events = baseline(payload, now) if baseline else []
    elif snap["changed"]:
        row["snapshot"] = "changed"
        events = compare(snap["previous"], payload, now) if compare else []
        if name == "news":
            fresh = news.new_articles(snap["previous"], payload)
            row["note"] += "; %d new since the last read" % len(fresh)
    else:
        row["snapshot"] = "same"
        events = []
    home = (ctx["site"] or {}).get("home_url") or "https://" + entity["domain"]
    saved = []
    for ev in events:
        event_id, created = store.record_event(
            entity["id"], "%s:%s" % (name, ev["key"]), type=ev["type"], title=ev["title"],
            source={"url": ev.get("url") or home, "detector": name, "seen_at": _iso(now)},
            status=ev.get("status") or "unknown", event_date=ev.get("date"),
            summary=ev.get("summary"), location=ev.get("location"))
        saved.append(dict(ev, event_id=event_id, created=created, detector=name))
    row["events"] = sum(1 for e in saved if e["created"])
    return saved


def _ago(delta):
    minutes = int(delta.total_seconds() // 60)
    return "%d min" % minutes if minutes < 90 else "%d h" % (minutes // 60)


# == a client's competitors ===========================================================

def targets(client_id, owner_email, *, store=None, limit=MAX_COMPETITORS):
    """The companies to collect for a client: confirmed competitors first,
    then suggestions by confidence; removed ones never."""
    if store is None:
        from . import market_radar_store as store
    rows = store.competitors(client_id, owner_email)
    out = []
    for r in rows[:limit]:
        e = store.get_entity(r["entity_id"]) or {}
        out.append({"id": r["entity_id"], "domain": r["domain"], "name": r.get("name") or
                    e.get("name"), "country": e.get("country"), "status": r["status"],
                    "kind": r["kind"]})
    return out, max(0, len(rows) - limit)


def collect_client(client_id, owner_email, *, run_id=None, progress=None, store=None, io=None,
                   now=None, budget_s=BUDGET_S, parallel=PARALLEL):
    if store is None:
        from . import market_radar_store as store
    client = store.get_client(client_id, owner_email)
    if not client:
        raise PermissionError("client %s is not yours or does not exist" % client_id)
    client_country = client.get("country") or "US"
    from . import market_radar_views as views
    archetype = views.effective_profile(client.get("profile") or {}, client.get("settings")
                                        ).get("archetype") or client.get("archetype")
    companies, left_out = targets(client_id, owner_email, store=store)
    deadline = time.monotonic() + budget_s
    breaker = news.Breaker()
    done, results = 0, []
    started = time.monotonic()

    def one(entity):
        return collect_entity(entity, run_id=run_id, client_country=client_country,
                              breaker=breaker, deadline=deadline, now=now, io=io, store=store,
                              archetype=archetype)

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(one, e): e for e in companies}
        for f in concurrent.futures.as_completed(futures):
            e = futures[f]
            try:
                r = f.result()
            except Exception as ex:   # collect_entity guards each detector; this is the database
                logger.exception("market_radar_collect: %s failed", e["domain"])
                r = {"entity_id": e["id"], "domain": e["domain"], "name": e.get("name"),
                     "site": None, "events": [], "error": "%s: %s" % (type(ex).__name__,
                                                                       str(ex)[:200]),
                     "rows": [{"detector": d, "status": "failed", "note": "not run: the "
                               "collection for this company broke", "items": 0, "events": 0}
                              for d in NAMES if applies(d, archetype)]}
            r["status"], r["kind"] = e["status"], e["kind"]
            results.append(r)
            done += 1
            if progress:
                progress("collect %d/%d" % (done, len(companies)))
    order = {e["id"]: i for i, e in enumerate(companies)}
    results.sort(key=lambda r: order.get(r["entity_id"], 99))
    return {"status": "ok" if companies else "empty",
            "companies": [_public(r) for r in results],
            "coverage": coverage(results),
            "left_out": left_out,
            "news_requests": breaker.requests, "news_breaker_open": breaker.open,
            "new_events": sum(1 for r in results for e in r["events"] if e.get("created")),
            "seconds": round(time.monotonic() - started, 1)}


def _public(r):
    return {"entity_id": r["entity_id"], "domain": r["domain"], "name": r.get("name"),
            "status": r.get("status"), "kind": r.get("kind"), "site": r.get("site"),
            "error": r.get("error"), "seconds": r.get("seconds"),
            "rows": r["rows"],
            "events": [{k: e.get(k) for k in ("event_id", "created", "detector", "type",
                                              "title", "date", "url", "summary")}
                       for e in r["events"]][:80]}


# Why a detector found nothing, in a reader's words, by row status.
WHY = {"none": "have none", "empty": "had nothing listed", "failed": "could not be read",
       "skipped": "were skipped", "blocked": "refused us"}
NONE_WORDS = {"news": "no articles", "locations": "no location pages or addresses",
              "catalog": "no shop", "reviews": "no public review count",
              "promotions": "no offer on the homepage", "pages": "no readable key page",
              "newsroom": "no news page or feed", "jobs": "no public jobs board",
              "filings": "no US stock-market listing", "registry": "no UK company number on its site",
              "subdomains": "no certificates logged", "headcount": "no Apollo employee estimate",
              "linkedin": "no LinkedIn page linked from its site"}


def coverage(results):
    """One line per detector: read for how many, and why not the rest. No
    lines when there were no competitors ("read for 0 of 0" six times said
    nothing, clovedental.in, 2026-10-10)."""
    total = len(results)
    if not total:
        return []
    lines = []
    for name, _r, _c, _b, label in DETECTORS:
        # A detector this client's kind of business does not run gets no line.
        if not any(x["detector"] == name for r in results for x in r["rows"]):
            continue
        counts = {}
        for r in results:
            row = next((x for x in r["rows"] if x["detector"] == name), None)
            st = (row or {}).get("status") or "failed"
            counts[st] = counts.get(st, 0) + 1
        read = counts.get("ok", 0) + counts.get("empty", 0)
        parts = []
        if counts.get("empty"):
            parts.append("%d %s" % (counts["empty"], {"news": "had no articles",
                                                     "promotions": "showed no offer",
                                                     "jobs": "had no open roles"}.get(
                                                         name, WHY["empty"])))
        if counts.get("none"):
            parts.append("%d %s %s" % (counts["none"], "has" if counts["none"] == 1 else "have",
                                       NONE_WORDS[name]))
        for st in ("failed", "skipped"):
            if counts.get(st):
                why = "was skipped" if st == "skipped" and counts[st] == 1 else WHY[st]
                parts.append("%d %s" % (counts[st], why))
        text = "%s read for %d of %d competitors" % (label, read, total)
        if parts:
            text += "; " + ", ".join(parts)
        lines.append({"detector": name, "label": label, "read": read, "total": total,
                      "counts": counts, "text": text + "."})
    return lines
