"""Market Radar, Phase 6: the signal engine. Raw items become events, and
events are scored for each client.

    run_for_client(client_id, owner_email, run_id=run)

TRIAGE (per company, shared by every client that tracks it). Phase 3 stores
the news that names a competitor, and its own blog posts, without deciding
what they say. Measured on 2026-10-10, most of it is not a move: Hoka's 128
articles were mostly shoe reviews and deal posts, Planet Fitness's were
crime reports at its gyms, and the moves that were there came as many
headlines each (Lululemon's C-suite overhaul: 13 different headlines from
13 outlets the same day; Ganni x Lululemon: 6). Matching titles cannot fold
those, so Haiku reads the company's new headlines in one call and returns:

  * events: each with a type from TYPES, a status (planned is not opened),
    an English title, the place if any, the event's own date when a
    headline states one, and the headlines that report it. A headline can
    also be added to an event already recorded, from the news or from the
    company's own site ("Aspen Dental opens new practice in Merced" joins
    the location detector's "New location page: Merced, CA"), which is how
    one event gets two independent sources;
  * left out: every other headline, with the reason (a review, a deal
    post, crime at a branch, stock chatter, an award, a namesake).

Only headlines not read before are sent, with the company's open events as
context, so a weekly collection costs a fraction of the first. Every
headline's verdict is remembered in a "signals" snapshot.

The model's answer is checked: headline numbers must be real and used
once, an existing event must be one we offered and of a compatible type,
a date must be a real date near now. A company whose triage failed keeps
its headlines for the next collection, and the coverage says so.

SCORING (per client). Every event of the client's competitors, and the
radar's finds on the client itself, gets a score:

    type weight x recency x distance x competitor tier x evidence
    x the client's own thumbs up or down on that type

and a severity. Many launches by one company in one window are worth less
each (a catalog of 100 new colours is one move, not 100). If more than
HIGH_SHARE of the events come out HIGH the scale is broken, so the lowest
are moved down to MEDIUM and the result says how many.
"""
from __future__ import annotations

import concurrent.futures
import logging
import math
import os
import re
from datetime import date, datetime, timedelta, timezone

from . import market_radar_news as news

logger = logging.getLogger(__name__)

MODEL = os.environ.get("MR_SIGNALS_MODEL", "claude-haiku-5-5")
MAX_ITEMS = 120              # headlines per company per collection
WINDOW_DAYS = 90
CONTEXT_EVENTS = 40          # open events offered to the model per company
HIGH_SHARE = 0.15
HIGH_MIN = 3
GEOCODE_MAX = 15             # news places geocoded per scoring
PARALLEL = 3

TYPES = {
    "new_location": "opens or plans a new branch, store, clinic, gym or office",
    "closed_location": "closes a branch",
    "relocation": "moves a branch",
    "acquisition": "buys, merges with or is bought by another company",
    "funding": "raises money, lists on a stock market, takes on financing",
    "leadership_change": "a chief executive or senior leader joins, leaves or changes role",
    "layoffs": "cuts jobs",
    "hiring_push": "a named hiring drive",
    "product_launch": "launches a product, service, collection or collaboration",
    "pricing_change": "raises or cuts prices, changes its pricing model",
    "promotion": "a sale, discount or offer campaign",
    "partnership": "a partnership or supply deal with another company",
    "new_market": "enters a new country, region or kind of customer",
    "legal_regulatory": "a lawsuit, fine, recall or regulator action against the company itself",
    "financial_results": "reports results, guidance or sales figures",
    "rebrand": "a new name, brand or positioning",
    "marketing_campaign": "an advertising campaign, a new ambassador or a sponsored athlete or star",
    "other_move": "another business decision by the company",
}
STATUSES = ("rumored", "announced", "planned", "opened", "completed", "closed", "unknown")
LEFT_OUT = {
    "not_about_company": "not about this company (a namesake, or only a passing mention)",
    "review_or_deal": "a product review, comparison, deal, discount-code or shopping post",
    "stock_chatter": "stock-price commentary",
    "incident": "a crime, accident or incident at a branch",
    "award_or_ranking": "an award, ranking, list or anniversary",
    "routine": "routine notice: an earnings date, a website refresh, one staff hire below senior leadership",
    "community": "charity, sponsorship or a community event",
    "opinion_or_profile": "an opinion piece, interview or profile",
    "other": "something else that is not a move",
}
# A headline may join an event recorded by another source only when the two
# types describe the same kind of thing.
COMPATIBLE = {
    "new_location": {"new_location", "new_job_location", "nearby_opening"},
    "closed_location": {"closed_location", "location_list_shrank"},
    "product_launch": {"product_launch"},
    "pricing_change": {"price_increase", "price_cut", "pricing_change"},
    "promotion": {"promotion", "sale_started"},
    "hiring_push": {"hiring_surge", "new_job_location"},
    "layoffs": {"hiring_slowdown"},
}

# == triage ===========================================================================

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["events", "left_out"],
    "properties": {
        "events": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["existing", "type", "status", "title", "place", "date", "items"],
            "properties": {
                "existing": {"type": "string"},
                "type": {"type": "string", "enum": list(TYPES)},
                "status": {"type": "string", "enum": list(STATUSES)},
                "title": {"type": "string"}, "place": {"type": "string"},
                "date": {"type": "string"},
                "items": {"type": "array", "items": {"type": "integer"}}}}},
        "left_out": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["item", "reason"],
            "properties": {"item": {"type": "integer"},
                           "reason": {"type": "string", "enum": list(LEFT_OUT)}}}}}}

SYSTEM = """You read recent headlines that mention one company and decide which report a MOVE by that company: something it did or will do as a business. A competitor of our client is watching.

Event types:
%s

For each move, return one event:
- items: the numbers of EVERY headline that reports this same move. Many outlets cover one move with different headlines (a new CEO's management shake-up reported 13 ways is one event).
- existing: if the move is one of the EXISTING EVENTS listed (already recorded from the news or from the company's own website), its id (E1, E2, ...). A news story about a new practice in Merced is the same move as "New location page: Merced, CA". Otherwise "".
- type, status: status is what the headlines say has happened: rumored, announced, planned (will open, coming soon), opened, completed (a deal closed), closed, or unknown. "Coming to" is planned, not opened.
- title: the move in at most 14 plain English words, naming the company, written by you from the headlines (translate if needed). Its tense matches the status: "plans to open" or "will open" for planned, "opens" only for opened.
- place: the town or region a location move is in, with its state or country ("Merced, CA"); "" otherwise.
- date: YYYY-MM-DD only when a headline states when the move happens or happened (an opening day). "" otherwise; the publication date is known already.

Every headline you do not put in an event goes in left_out with a reason:
%s

Be strict:
- A product review, a sale at a retailer, a discount-code page, a crime at one branch, a stock-price article, an award or an anniversary is not a move.
- A different company with a similar or partly shared name that sells something else is not this company ("Alo Drink" is not Alo Yoga).
- A company's own posts are mostly marketing. Exhibiting at a trade show, speaking at or hosting an event, a webinar or a customer workshop, publishing an article, a customer story or a how-to is routine, not a move. A post is a move only when it announces a decision: a launch, an opening, a deal, a hire at the top, a new market, a price change.
- When the company a headline names carries a word this company's name does not ("Spur Intelligence" for Spur, "Alo Drink" for Alo), it is a different company: leave it out as not_about_company, unless the headline also names this company's domain or one of its own products.
- acquisition only when ownership changes. A franchisor running a franchisee's sites for a while is other_move.
- leadership_change only for chief executives, board members and senior leaders, not one more dentist, trainer or manager.
- partnership only when a partnership begins. A team or athlete leaving the company is other_move.
- A product launch and the advertising campaign for it are one event, not two. Before answering, check that no two events describe the same move.
Use each headline once. Never use em dashes or en dashes."""


def _system():
    return SYSTEM % ("\n".join("- %s: %s" % kv for kv in TYPES.items()),
                     "\n".join("- %s: %s" % kv for kv in LEFT_OUT.items()))


def _clean(text, limit):
    text = re.sub(r"\s*[%s%s]\s*" % (chr(0x2014), chr(0x2013)), ", ", " ".join(
        str(text or "").split()))
    return text[:limit]


def _day(value, now):
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    today = now.date()
    return d.isoformat() if today - timedelta(days=400) <= d <= today + timedelta(days=400) else None


def items_to_read(news_items, posts, triaged, now):
    """The company's headlines (news) and own posts from the window that
    have no verdict yet, newest first, at most MAX_ITEMS. Returns (items,
    left_for_later)."""
    since = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    out = []
    for it in news_items or []:
        if it.get("id") and it["id"] not in triaged and (it.get("date") or since) >= since:
            out.append({"id": it["id"], "title": it["title"], "date": it.get("date"),
                        "publisher": it.get("publisher") or "", "link": it.get("link"),
                        "copies": it.get("copies") or 1, "detector": "news"})
    for p in posts or []:
        pid = "post:%d" % p["id"]
        if pid not in triaged and not p.get("hidden_reason"):
            det = next((s.get("detector") for s in p.get("sources") or [] if s.get("detector")),
                       "newsroom")
            out.append({"id": pid, "title": p["title"], "date": _iso_day(p.get("event_date")),
                        "publisher": "its LinkedIn page" if det == "linkedin" else "its own site",
                        "link": _source_url(p), "copies": 1,
                        "detector": "linkedin" if det == "linkedin" else "newsroom",
                        "event_id": p["id"]})
    out.sort(key=lambda i: i.get("date") or "", reverse=True)
    return out[:MAX_ITEMS], max(0, len(out) - MAX_ITEMS)


def _iso_day(v):
    return v.isoformat()[:10] if hasattr(v, "isoformat") else (str(v)[:10] if v else None)


def _source_url(e):
    return next((s.get("url") for s in e.get("sources") or [] if s.get("url")), None)


def triage(company, items, existing, *, now, run_id=None, client=None, llm=None):
    """{"events", "left_out", "unanswered", "note"}. `existing` is [{"key",
    "type", "title", "date"}]. Raises nothing: a failed call returns
    events None and the reason in note."""
    if llm is None:
        from . import market_radar_llm as llm
    ctx = [dict(e, ref="E%d" % (i + 1)) for i, e in enumerate(existing[:CONTEXT_EVENTS])]
    user = "COMPANY: %s (%s), %s. %s\n\nEXISTING EVENTS:\n%s\n\nHEADLINES:\n%s" % (
        company.get("name") or company["domain"], company["domain"], company.get("country") or "",
        ("What it sells: " + company["sells"]) if company.get("sells") else "",
        "\n".join("%s | %s | %s | %s" % (e["ref"], e.get("date") or "", e["type"], e["title"])
                  for e in ctx) or "(none)",
        "\n".join("%d | %s | %s%s | %s" % (i, it.get("date") or "undated", it["publisher"],
                                          " (%d outlets)" % it["copies"] if it["copies"] > 1
                                          else "", it["title"]) for i, it in enumerate(items)))
    try:
        data, _meta = llm.call_json(_system(), user, SCHEMA, model=MODEL,
                                    max_tokens=min(16000, 1500 + 90 * len(items)),
                                    run_id=run_id, stage="signals_triage", client=client)
    except Exception as e:
        return {"events": None, "left_out": None, "unanswered": len(items),
                "note": "the headlines could not be read (%s)" % getattr(e, "kind",
                                                                       type(e).__name__)}
    refs = {e["ref"]: e for e in ctx}
    used, events, rejected = set(), [], 0
    for ev in data.get("events") or []:
        idx = []
        for i in ev.get("items") or []:
            if isinstance(i, int) and 0 <= i < len(items) and i not in used and i not in idx:
                idx.append(i)
        if not idx or ev.get("type") not in TYPES:
            rejected += 1
            continue
        target = refs.get(ev.get("existing") or "")
        if target and target["type"] != ev["type"] and \
                target["type"] not in COMPATIBLE.get(ev["type"], set()):
            target = None            # not the same kind of thing: a new event
        used.update(idx)
        rows = sorted((items[i] for i in idx), key=lambda r: r.get("date") or "9999")
        events.append({
            "existing": target["key"] if target else None,
            "type": ev["type"], "status": ev.get("status") if ev.get("status") in STATUSES
            else "unknown",
            "title": _clean(ev.get("title"), 160) or _clean(rows[0]["title"], 160),
            "place": _clean(ev.get("place"), 80),
            "date": _day(ev.get("date"), now) or rows[0].get("date"),
            "items": rows})
    left = []
    for lo in data.get("left_out") or []:
        i = lo.get("item")
        if isinstance(i, int) and 0 <= i < len(items) and i not in used:
            used.add(i)
            left.append({"item": items[i], "reason": lo.get("reason") if lo.get("reason") in
                         LEFT_OUT else "other"})
    # A headline the model returned nowhere is not judged: it is read again
    # next time rather than silently dropped.
    unanswered = len(items) - len(used)
    notes = []
    if unanswered:
        notes.append("%d headlines got no verdict and will be read again" % unanswered)
    if rejected:
        notes.append("%d events were malformed and left out" % rejected)
    return {"events": events, "left_out": left, "unanswered": unanswered,
            "note": "; ".join(notes) or None}


def prior_presence(place, entity_id, event_day, *, store):
    """What the company's own location pages said about this place before
    the news: the difference between a new market and one more branch in a
    market it serves (Aspen Dental "opens" in Merced, 2026-10-01, where its
    sitemap already listed a Merced page)."""
    from . import market_radar_detectors as det
    city = news.fold((place or "").split(",")[0])
    if len(city) < 3:
        return None
    first = store.first_snapshot(entity_id, "locations")
    if not first or "places" not in (first["payload"] or {}):
        return None
    hits = [p for p in first["payload"]["places"]
            if city in news.fold(det.place_label(p))]
    seen = first["first_seen_at"].date()
    if hits and event_day and seen <= date.fromisoformat(event_day) - timedelta(days=14):
        return "Its website already listed %d %s location page%s on %s, before this news: " \
               "another site in a market it serves, not a new market." % (
                   len(hits), place.split(",")[0], "" if len(hits) == 1 else "s", seen.isoformat())
    return None


def triage_company(entity, *, store, now, run_id=None, client=None, llm=None, sells=None):
    """Read one company's new headlines and posts into events. Returns a
    summary row for coverage."""
    snap = store.latest_snapshot(entity["id"], "news")
    news_items = ((snap or {}).get("payload") or {}).get("items") or []
    recent = store.recent_events([entity["id"]], days=WINDOW_DAYS + 30)
    posts = [e for e in recent if e["type"] == "announcement"]
    memo = (store.latest_snapshot(entity["id"], "signals") or {}).get("payload") or {}
    triaged = dict(memo.get("triaged") or {})
    items, later = items_to_read(news_items, posts, triaged, now)
    row = {"entity_id": entity["id"], "domain": entity["domain"], "name": entity.get("name"),
           "read": 0, "events": 0, "new_events": 0, "left_out": 0, "later": later, "note": None}
    if not items:
        row["note"] = "no new headlines"
        return row
    existing = [{"key": e["dedupe_key"], "type": e["type"], "title": e["title"],
                 "date": _iso_day(e["event_date"]) or _iso_day(e["first_seen_at"])}
                for e in recent if e["type"] != "announcement" and not e.get("hidden_reason")]
    # The events a headline is most likely to join first: a catalog's
    # hundred new colours must not push a new branch out of the list.
    existing.sort(key=lambda e: 0 if e["type"] not in ("product_launch", "sold_out",
                                                        "product_removed") else 1)
    got = triage(dict(entity, sells=sells), items, existing, now=now, run_id=run_id,
                 client=client, llm=llm)
    if got["events"] is None:
        row.update(status="failed", note=got["note"])
        return row
    events, left = got["events"], got["left_out"]
    stamp = now.isoformat(timespec="seconds")
    for ev in events:
        first = ev["items"][0]
        key = ev["existing"] or ("news:" if first["detector"] == "news" else "post-typed:") + \
            first["id"].replace("post:", "")
        summary = None
        if ev["type"] == "new_location" and ev["place"]:
            summary = prior_presence(ev["place"], entity["id"], ev["date"], store=store)
        created_any = False
        for it in ev["items"]:
            event_id, created = store.record_event(
                entity["id"], key, type=ev["type"], title=ev["title"],
                source={"url": it.get("link") or "https://" + entity["domain"],
                        "detector": it["detector"], "seen_at": stamp,
                        "publisher": it["publisher"], "headline": it["title"][:300],
                        "date": it.get("date")},
                status=ev["status"], event_date=ev["date"], summary=summary,
                location={"label": ev["place"]} if ev["place"] else None)
            created_any = created_any or created
            triaged[it["id"]] = key
            if it.get("event_id"):
                store.hide_event(it["event_id"], "folded into: %s" % ev["title"])
        row["events"] += 1
        row["new_events"] += 1 if created_any else 0
    for lo in left:
        it = lo["item"]
        triaged[it["id"]] = "left_out:" + lo["reason"]
        if it.get("event_id"):
            store.hide_event(it["event_id"], "not a move: " + LEFT_OUT[lo["reason"]])
    # Remember verdicts only for headlines still in the window.
    keep = {i["id"] for i in news_items} | {"post:%d" % p["id"] for p in posts}
    store.save_snapshot(entity["id"], "signals",
                        {"triaged": {k: v for k, v in triaged.items() if k in keep}},
                        item_count=len(triaged), run_id=run_id)
    row.update(status="ok", read=len(items) - got["unanswered"], left_out=len(left),
               note=got["note"])
    return row


# == scoring ==========================================================================

WEIGHTS = {
    "acquisition": 10, "new_market": 9, "funding": 8, "nearby_opening": 9, "new_location": 8,
    "closed_location": 8, "layoffs": 8, "relocation": 6, "leadership_change": 7,
    "new_entrant": 7, "pricing_change": 6, "price_increase": 6, "price_cut": 6,
    "legal_regulatory": 6, "hiring_surge": 6, "hiring_slowdown": 6, "hiring_push": 5,
    "new_job_location": 6, "location_list_shrank": 5, "product_launch": 5, "partnership": 5,
    "financial_results": 5, "rebrand": 5, "senior_hire_search": 4, "promotion": 4,
    "sale_started": 4, "other_move": 3, "marketing_campaign": 3, "product_removed": 3,
    # A key page's wording changed: worth listing, rarely worth a report's
    # top five (McFarlane Dental's swapped blog link ranked second, 2026-10-10).
    "page_changed": 2,
    "promotion_ended": 2, "review_growth": 2, "sold_out": 2, "site_restructured": 2,
    "announcement": 2,
    # A host name first certified: a hint of a product or region to come.
    "new_subdomain": 3,
}
TIER = {"direct": 1.0, "local": 1.0, "indirect": 0.7, "aspirational": 0.5}
HIGH, MEDIUM = 6.0, 2.5
CERTAINTY = {"rumored": 0.6}
# For a chain or an online brand, one more branch of a rival chain is
# routine (Burn Boot Camp's one franchise opening ranked first for
# Orangetheory, 2026-10-10). For a business with one site, a branch is
# weighed by its distance instead.
BRANCH_TYPES = {"new_location", "closed_location", "relocation"}
BRANCH_WEIGHT_FOR_CHAINS = 0.6
REPEAT_DECAY = 0.7           # the k-th event of one type by one company is worth 0.7^(k-1)


def recency(day, now):
    """1 for today (or a planned date ahead), fading over about six weeks,
    never below a quarter: an acquisition three months ago still outranks
    this week's review count."""
    age = max(0, (now.date() - day).days) if day else 0
    return max(0.25, math.exp(-age / 45.0))


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def news_day(e):
    """The day freshness is measured from: when the move happened, or, for
    a move dated ahead ("grand opening October 17"), when it became news.
    A planned date ahead is not fresher than this week's news (a franchise
    opening dated a week ahead ranked first for Orangetheory, 2026-10-10)."""
    seen = _as_date(e.get("first_seen_at"))
    day = _as_date(e.get("event_date")) or seen
    if day and seen and day > seen:
        published = [d for d in (_as_date(s.get("date")) for s in e.get("sources") or []) if d]
        day = min(published) if published else seen
    return day


def distance_factor(km):
    if km is None:
        return 1.0
    for limit, f in ((5, 1.5), (25, 1.25), (100, 1.0), (500, 0.8)):
        if km <= limit:
            return f
    return 0.6


def evidence_factor(event):
    articles = len([s for s in event.get("sources") or [] if s.get("detector") == "news"])
    f = 1 + 0.25 * (max(1, event.get("evidence_count") or 1) - 1)
    if articles > 1:
        f += 0.1 * math.log2(articles)
    return min(f, 1.8)


def feedback_factor(etype, feedback_by_type):
    up, down = feedback_by_type.get(etype, (0, 0))
    if down >= 2 and down > up:
        return 0.6
    if up >= 2 and up > down:
        return 1.2
    return 1.0


def score_events(events, *, tiers, now, point=None, local=False, feedback=None, geocode=None,
                 chain=False):
    """[(event_id, score, severity, distance_km)] and the number of HIGH
    events moved down. `tiers` maps entity_id -> (kind, status)."""
    from .market_radar_places import distance_km as dist
    feedback = feedback or {}
    rows, geocoded = [], 0
    for e in events:
        if e.get("hidden_reason"):
            continue
        day = news_day(e)
        km = None
        loc = e.get("location") or {}
        if point and loc.get("lat") is not None:
            km = loc.get("distance_km") or dist(point["lat"], point["lon"], loc["lat"], loc["lon"])
        elif point and local and loc.get("label") and geocode and geocoded < GEOCODE_MAX and \
                e["type"] in ("new_location", "closed_location", "relocation"):
            geocoded += 1
            hit = geocode(loc["label"])
            if hit:
                km = dist(point["lat"], point["lon"], hit["lat"], hit["lon"])
        kind, status = tiers.get(e["entity_id"], ("direct", "confirmed"))
        tier = TIER.get(kind, 0.7) * (0.85 if status == "proposed" else 1.0)
        s = WEIGHTS.get(e["type"], 3) * recency(day, now) * distance_factor(km) * tier * \
            evidence_factor(e) * feedback_factor(e["type"], feedback) * \
            CERTAINTY.get(e.get("status"), 1.0) * \
            (BRANCH_WEIGHT_FOR_CHAINS if chain and e["type"] in BRANCH_TYPES else 1.0)
        rows.append({"id": e["id"], "entity_id": e["entity_id"], "type": e["type"], "score": s,
                     "km": round(km, 1) if km is not None else None})
    # One company's many events of one type: each further one counts less.
    rows.sort(key=lambda r: -r["score"])
    seen = {}
    for r in rows:
        k = (r["entity_id"], r["type"])
        r["score"] *= REPEAT_DECAY ** seen.get(k, 0)
        seen[k] = seen.get(k, 0) + 1
    rows.sort(key=lambda r: -r["score"])
    for r in rows:
        r["severity"] = "HIGH" if r["score"] >= HIGH else "MEDIUM" if r["score"] >= MEDIUM else "LOW"
    highs = [r for r in rows if r["severity"] == "HIGH"]
    cap = max(HIGH_MIN, int(len(rows) * HIGH_SHARE))
    for r in highs[cap:]:
        r["severity"] = "MEDIUM"
    return rows, max(0, len(highs) - cap)


def score_client(client_id, owner_email, *, store, now, run_id=None, geocode=None):
    from . import market_radar_views as views
    c = store.get_client(client_id, owner_email)
    profile = views.effective_profile(c["profile"] or {}, c["settings"])
    comps = store.competitors(client_id, owner_email)
    tiers = {r["entity_id"]: (r["kind"], r["status"]) for r in comps}
    tiers[c["entity_id"]] = ("direct", "confirmed")
    ids = [r["entity_id"] for r in comps] + [c["entity_id"]]
    events = [e for e in store.recent_events(ids, days=WINDOW_DAYS + 30, limit=1000)
              if e["entity_id"] != c["entity_id"] or e["type"] in ("nearby_opening",
                                                                    "new_entrant")]
    old = store.client_scores(client_id)
    fb = {}
    by_id = {e["id"]: e for e in events}
    for eid, v in old.items():
        if v.get("feedback") and eid in by_id:
            up, down = fb.get(by_id[eid]["type"], (0, 0))
            fb[by_id[eid]["type"]] = (up + (v["feedback"] == "up"), down + (v["feedback"] == "down"))
    # Distance means something only from the one place a local business
    # serves. A chain's head office is not where its customers are
    # (Orangetheory's HQ in Florida ranked a Florida franchise opening
    # first, 2026-10-10), so chains are scored without distance until the
    # report knows their branches.
    point = profile.get("hq_point") if profile.get("archetype") == "local_single" else None
    rows, demoted = score_events(events, tiers=tiers, now=now, point=point, local=True,
                                 feedback=fb, geocode=geocode,
                                 chain=profile.get("archetype") != "local_single")
    store.link_client_events(client_id, [(r["id"], round(r["score"], 3), r["severity"], r["km"])
                                         for r in rows], run_id=run_id)
    counts = {s: sum(1 for r in rows if r["severity"] == s) for s in ("HIGH", "MEDIUM", "LOW")}
    return {"scored": len(rows), "severity": counts, "demoted": demoted,
            "hidden": sum(1 for e in events if e.get("hidden_reason"))}


# == one client ========================================================================

def run_for_client(client_id, owner_email, *, run_id=None, store=None, now=None, llm=None,
                   client=None, geocode=None):
    if store is None:
        from . import market_radar_store as store
    if geocode is None:
        from .market_radar_profile import geocode
    now = now or datetime.now(timezone.utc)
    c = store.get_client(client_id, owner_email)
    if not c:
        raise PermissionError("client %s is not yours or does not exist" % client_id)
    from . import market_radar_collect as mc
    companies, _ = mc.targets(client_id, owner_email, store=store)
    sells = {r["entity_id"]: (r.get("details") or {}).get("sells")
             for r in store.competitors(client_id, owner_email)}
    def one(e):
        try:
            return triage_company(e, store=store, now=now, run_id=run_id, client=client,
                                  llm=llm, sells=sells.get(e["id"]))
        except Exception as ex:
            logger.exception("market_radar_signals: triage of %s failed", e["domain"])
            return {"entity_id": e["id"], "domain": e["domain"], "name": e.get("name"),
                    "status": "failed", "note": "%s: %s" % (type(ex).__name__, str(ex)[:200])}

    # A company with a hundred headlines takes Haiku about a minute; three
    # at a time keeps a twelve-competitor collection to a few minutes.
    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        rows = list(pool.map(one, companies))
    scored = score_client(client_id, owner_email, store=store, now=now, run_id=run_id,
                          geocode=geocode)
    failed = [r for r in rows if r.get("status") == "failed"]
    read = sum(r.get("read") or 0 for r in rows)
    note = "%d headlines and posts read for %d companies: %d events, %d left out as not moves" % (
        read, len(rows), sum(r.get("events") or 0 for r in rows),
        sum(r.get("left_out") or 0 for r in rows))
    if failed:
        note += "; not read for %d (%s)" % (len(failed), "; ".join(
            "%s: %s" % (r["domain"], r["note"]) for r in failed[:3]))
    later = sum(r.get("later") or 0 for r in rows)
    if later:
        note += "; %d older headlines wait for the next collection" % later
    return {"companies": rows, "scored": scored, "note": note}
