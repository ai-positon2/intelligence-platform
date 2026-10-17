"""Paid Marketing Agents: Keyword Research, what is read before anything is judged.

Each function returns the shape the other agents' research does (tracker/paid_strategy_research.py),

    {"key", "state": ok | partial | failed | skipped, "note": words for the page, "took": seconds, "usd": cost beyond the model, "data": {...}}

and never raises: a piece that cannot be read says so in its note and the research is judged from the rest, naming what it did not have. The website and the
competitors are read by the shared functions (paid_strategy_research.read_website, read_competitors); what is here is new:

  business   the latest finished Business Research for the same client, so what was learned about the business is not asked again
  account    every keyword and negative keyword the account has and 90 days of its search terms
  planner    Google Keyword Planner: ideas for each thing the client sells, for the site, for each competitor's site and for what already converts, with their volumes

The functions take what they read through (post, sqr_read, list_recent...), so tests run on fakes and nothing here needs a network to be checked.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import paid_keywords as pk, paid_keywords_data as kd, paid_strategy as ps, paid_strategy_research as rs
from .paid_strategy_research import failed, piece, skipped

log = logging.getLogger(__name__)

MAX_KEYWORD_ROWS = 20_000           # the account's keywords kept for comparing: more than this is a very large account, and the page says so
MAX_NEGATIVE_ROWS = 20_000
MAX_SERVICE_BATCHES = 6
MAX_OFFERING_BATCHES = 3
MAX_CALLS = 14
CALL_WORKERS = 3
PER_CALL_IDEAS = 700                # the biggest ideas of one request that are kept
MAX_IDEAS = 2500                    # the biggest ideas of all the requests that are kept
MAX_TERM_METRICS = 100


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


# ── Business Research ───────────────────────────────────────────────────────

def find_business(inputs, *, list_recent, get):
    """The newest finished Business Research for the same client (the same website host, or the same name), or None. `list_recent(kind=)` is the store's list of saved
    builds, newest first; `get(id)` one whole build."""
    host, name = ps.host_of(inputs.get("website")), _norm(inputs.get("name"))
    for row in list_recent(kind="business") or []:
        i = row.get("inputs") or {}
        if row.get("status") != "done" or row.get("stale"):
            continue
        if (host and ps.host_of(i.get("website")) == host) or (name and _norm(row.get("name")) == name):
            rec = get(row["id"])
            if rec and (rec.get("plan") or {}).get("generatedAt"):
                return rec
    return None


def read_business(inputs, *, find):
    """What a finished Business Research learned, in the few parts Keyword Research uses. `find(inputs)` returns the build or None."""
    started = time.monotonic()
    rec = find(inputs)
    if not rec:
        return skipped("business", "There is no finished Business Research for this client, so this starts from what was typed and read here.")
    plan = rec.get("plan") or {}
    core, aud = plan.get("core") or {}, plan.get("audience") or {}
    pos = core.get("positioning") or {}
    data = {"id": rec.get("id"), "builtAt": (plan.get("generatedAt") or "")[:10], "summary": core.get("summary") or "",
            "offerings": [{"name": o.get("name"), "description": o.get("description"), "priority": o.get("priority"), "page": o.get("page")} for o in (core.get("offerings") or [])[:14]],
            "searches": [a.get("searches") for a in (aud.get("audiences") or []) if a.get("searches")][:8],
            "vocabulary": (aud.get("voice") or {}).get("vocabulary") or pos.get("wordsUsed") or [], "avoidWords": pos.get("wordsToAvoid") or [],
            "compliance": [{"topic": c.get("topic"), "rule": c.get("rule")} for c in (core.get("compliance") or [])[:8]],
            "seasonality": [{"window": s.get("window"), "why": s.get("why")} for s in (aud.get("seasonality") or [])[:8]]}
    return piece("business", "ok", "Used the Business Research of %s." % (data["builtAt"] or "an earlier date"), data, time.monotonic() - started)


# ── The account ─────────────────────────────────────────────────────────────

def read_account(account, *, sqr_read, store=None, today=None):
    """The account's keywords, negative keywords and 90 days of search terms. `sqr_read(store, account, start, end)` is paid_sqr_data.read."""
    started = time.monotonic()
    cid = account["id"]
    start, end = rs.window(today)
    try:
        sq = sqr_read(store, {"id": cid, "name": account.get("name")}, start, end)
    except Exception as e:                                  # noqa: BLE001 (the research goes on without the account)
        return failed("account", "The account's keywords could not be read (%s)." % rs._clip(e, 140), time.monotonic() - started)
    kws = sq.get("keywords") or []
    warns = list(sq.get("warnings") or [])
    terms = sq.get("terms") or []
    converting = [{"term": t["t"], "clicks": t["k"], "cost": t["x"], "conv": t["v"], "campaign": t["c"]} for t in sorted((t for t in terms if t["v"] > 0), key=lambda t: (-t["v"], t["x"]))[:60]]
    wasteful = [{"term": t["t"], "clicks": t["k"], "cost": t["x"], "campaign": t["c"]} for t in sorted((t for t in terms if t["v"] == 0 and t["x"] > 0), key=lambda t: -t["x"])[:30]]
    by_camp = {}
    for k in kws:
        c = by_camp.setdefault(k.get("c") or "", {"name": k.get("c") or "", "keywords": 0, "groups": set()})
        c["keywords"] += 1
        c["groups"].add(k.get("g") or "")
    campaigns = sorted(({"name": c["name"], "keywords": c["keywords"], "adGroups": len(c["groups"])} for c in by_camp.values() if c["name"]), key=lambda c: -c["keywords"])
    negs = (sq.get("negatives") or [])[:MAX_NEGATIVE_ROWS]
    lists = sq.get("lists") or []
    truncated = len(kws) > MAX_KEYWORD_ROWS or bool(sq.get("keywordsCut"))
    negs_cut = len(sq.get("negatives") or []) > MAX_NEGATIVE_ROWS or bool(sq.get("negativesCut"))
    data = {"account": {"id": cid, "name": account.get("name") or "", "currency": account.get("currency") or "USD"}, "window": {"start": start, "end": end},
            "keywords": kws[:MAX_KEYWORD_ROWS], "keywordCount": len(kws), "keywordsTruncated": truncated, "negatives": negs, "negativeCount": len(sq.get("negatives") or []),
            "lists": lists, "listCount": len(lists), "campaigns": campaigns[:40], "terms": {"converting": converting, "wasteful": wasteful, "total": len(terms)}, "warnings": warns,
            "keywordsUnknown": bool(sq.get("keywordsFailed")), "negativesUnknown": bool(sq.get("negativesFailed")), "caveats": list(sq.get("notes") or [])}
    if truncated:
        warns.append("The account has more keywords than were read (more than %s); the rest were not compared, so a keyword it has may be shown as new." % format(MAX_KEYWORD_ROWS, ","))
    if negs_cut:
        warns.append("The account has more negative keywords than were read (more than %s); the rest were not compared, so a keyword they block may be shown as new." % format(MAX_NEGATIVE_ROWS, ","))
    if sq.get("keywordsFailed"):
        warns.append("The account's keyword list could not be read, so every idea is shown as new and the file for Google Ads Editor is not made.")
    state = "partial" if warns else "ok"
    note = "%s keywords in %d campaigns, %s negative keywords in campaigns and %d shared lists, %s search terms in 90 days." % (
        format(len(kws), ","), len(campaigns), format(data["negativeCount"], ","), len(lists), format(len(terms), ","))
    return piece("account", state, " ".join([note] + warns), data, time.monotonic() - started)


# ── Keyword Planner ─────────────────────────────────────────────────────────

def variant(phrase):
    """The same phrase in its other number: Keyword Planner sometimes answers a singular phrase with nothing and the plural with hundreds of ideas (insulin pump, insulin pumps)."""
    w = phrase.split()
    if not w or len(w[-1]) < 4 or w[-1].endswith("ss"):
        return ""
    last = w[-1]
    other = last[:-1] if last.endswith("s") else last + "s"
    return " ".join(w[:-1] + [other])


def _clean_seed(text):
    text = re.sub(r"\s+", " ", re.sub(r"[^\w&' ]|_", " ", str(text or ""))).strip().lower()
    return text if 3 <= len(text) <= 60 and len(text.split()) <= 6 else ""


def seeds_for(inputs, research, dropped=None):
    """What Keyword Planner is asked, as [{'label', 'keywords': [...]}] and [{'label', 'url'}]: one request for each thing the client sells (the phrase and its other
    number), each offering a finished Business Research found, what already converts, the client's page headings; one for the site and one for each competitor read.
    What is not asked about because there would be too many requests is added to the list `dropped`, when one is given."""
    dropped = dropped if dropped is not None else []
    research = research or {}
    batches, seen = [], set()

    def add(label, phrases):
        out = []
        for p in phrases:
            p = _clean_seed(p)
            if p and p not in out:
                out.append(p)
        key = tuple(sorted(out))
        if out and key not in seen:
            seen.add(key)
            batches.append({"label": label, "keywords": out[:10]})

    services = pk.phrases(inputs.get("services"))
    for p in services[:MAX_SERVICE_BATCHES]:
        p = _clean_seed(p)
        if p:
            add(p, [p, variant(p)])
    dropped += [p for p in services[MAX_SERVICE_BATCHES:] if _clean_seed(p)]
    offerings = [o for o in ((research.get("business") or {}).get("data") or {}).get("offerings") or [] if o.get("priority") in ("high", "medium")]
    for o in offerings[:MAX_OFFERING_BATCHES]:
        p = _clean_seed(o.get("name"))
        if p:
            add(p, [p, variant(p)])
    account = ((research.get("account") or {}).get("data")) or {}
    add("what already converts", [t["term"] for t in (account.get("terms") or {}).get("converting", [])[:10]])
    site = ((research.get("website") or {}).get("data")) or {}
    heads = []
    for pg in (site.get("pages") or [])[1:8]:
        heads += list(pg.get("h1") or []) + [re.split(r"[|:\u2013\u2014-]", pg.get("title") or "")[0]]
    add("the client's pages", heads)

    urls = []
    pages = site.get("pages") or []
    home = pages[0]["url"] if pages else inputs.get("website")
    if home:
        urls.append({"label": "site", "url": home})
    for c in (((research.get("competitors") or {}).get("data")) or {}).get("competitors", [])[:pk.RIVALS_READ]:
        if c.get("ok") and c.get("pages"):
            urls.append({"label": "rival: %s" % c.get("name"), "url": c["pages"][0]["url"]})
    room = max(0, MAX_CALLS - len(urls))
    dropped += [b["label"] for b in batches[room:]]
    return batches[:room], urls


COUNTRIES = {2840: "the United States", 2124: "Canada", 2826: "the United Kingdom", 2036: "Australia", 2554: "New Zealand", 2356: "India", 2702: "Singapore"}
DEFAULT_COUNTRY = 2840


def resolve_places(places, *, post):
    """([{'name', 'id', 'canonical', 'type'}] Google found, [the names it did not]). A place is looked for wherever it is: Toronto is not looked for in the United States."""
    found, missing = [], []
    for place in places:
        try:
            d = post("/geoTargetConstants:suggest", {"locale": "en", "locationNames": {"names": [place]}})
        except Exception as e:                              # noqa: BLE001
            log.info("keywords: place %r not resolved: %s", place, e)
            missing.append(place)
            continue
        g = ((((d or {}).get("geoTargetConstantSuggestions") or [{}])[0]) or {}).get("geoTargetConstant") or {}
        if g.get("id"):
            found.append({"name": place, "id": str(g["id"]), "canonical": g.get("canonicalName") or g.get("name") or place, "type": g.get("targetType") or ""})
        else:
            missing.append(place)
    return found, missing


def geography(inputs, *, post):
    """(geo target constants, places found, a sentence saying where searches are counted). The places the client serves, as Google finds them; if none is found, the
    country of the account's currency; failing that the United States, and the sentence says so."""
    found, missing = resolve_places(rs.split_places(inputs.get("locations")), post=post) if inputs.get("locations") else ([], [])
    if found:
        where = "Searches are counted in %s." % "; ".join(p["canonical"] for p in found)
        geo = ["geoTargetConstants/%s" % p["id"] for p in found]
    else:
        country = rs.COUNTRY_BY_CURRENCY.get(inputs.get("currency")) if inputs.get("account") else None
        country = country or DEFAULT_COUNTRY
        geo = ["geoTargetConstants/%d" % country]
        why = "No place was found" if missing else "No place was given"
        where = "%s, so searches are counted for all of %s%s." % (why, COUNTRIES.get(country, "that country"), "" if inputs.get("account") and rs.COUNTRY_BY_CURRENCY.get(inputs.get("currency")) else " (the default)")
    if missing:
        where += " Google did not find: %s." % ", ".join(missing)
    return geo, found, where


def read_planner(inputs, research, *, post, planner_account, currency=None, workers=CALL_WORKERS):
    """Keyword Planner's ideas for the client, with the volumes of the converting search terms. `post(path, body)` is Google Ads' API call; `planner_account` is any
    account that may ask. A request that fails is named in the note and the others go on; if none answers, the piece fails."""
    started = time.monotonic()
    if not planner_account:
        return skipped("planner", "There is no Google Ads account to ask Keyword Planner through.")
    floor = int(inputs.get("minSearches") or pk.DEFAULT_MIN)
    geo, places, where = geography(inputs, post=post)
    base = {"language": rs.LANGUAGE_ENGLISH, "geoTargetConstants": geo, "keywordPlanNetwork": "GOOGLE_SEARCH", "includeAdultKeywords": False}
    not_asked = []
    batches, urls = seeds_for(inputs, research, not_asked)
    calls = [(b["label"], dict(base, keywordSeed={"keywords": b["keywords"]}), "keywords") for b in batches] + [(u["label"], dict(base, urlSeed={"url": u["url"]}), "url") for u in urls]
    if not calls:
        return skipped("planner", "There was nothing to start Keyword Planner from: add what the client sells, a website or an account.")

    def ask(call):
        label, body, kind = call
        try:
            d = post("/customers/%s:generateKeywordIdeas" % planner_account, body)
        except Exception as e:                              # noqa: BLE001 (Keyword Planner has its own limits)
            return label, kind, None, "did not answer (%s)" % rs._clip(e, 110)
        ideas = [x for x in (kd.parse_idea(r) for r in (d or {}).get("results") or []) if x]
        return label, kind, ideas, ""

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        answers = list(ex.map(ask, calls))
    pool, log_, notes, empty, answered, below = {}, [], [], [], 0, set()
    for label, kind, ideas, err in answers:
        if ideas is None:
            notes.append("Keyword Planner %s for %s." % (err, label))
            log_.append({"label": label, "ideas": 0, "note": err})
            continue
        answered += 1
        if not ideas:
            empty.append(label)
            log_.append({"label": label, "ideas": 0, "note": "Google returned no ideas"})
            continue
        below |= {i["kw"] for i in ideas if i["avg"] < floor}
        ideas = sorted((i for i in ideas if i["avg"] >= floor), key=lambda i: -i["avg"])[:PER_CALL_IDEAS]
        log_.append({"label": label, "ideas": len(ideas), "note": ""})
        tag = label if kind == "url" else "seed: " + label
        for i in ideas:
            have = pool.get(i["kw"])
            if have is None:
                pool[i["kw"]] = dict(i, **{"from": [tag]})
            elif len(have["from"]) < 4:
                have["from"].append(tag)
    if empty:
        notes.append("Keyword Planner had no ideas for: %s." % ", ".join(empty[:6]))
    if not answered:
        return failed("planner", " ".join(notes) or "Keyword Planner did not answer.", time.monotonic() - started)
    ranked = sorted(pool.values(), key=lambda i: (-i["avg"], i["kw"]))
    metrics = _term_metrics(research, pool, post=post, account=planner_account, base=base, notes=notes)
    data = {"geo": geo, "places": places, "currency": currency or inputs.get("currency") or "USD", "floor": floor, "seeds": [{"label": b["label"], "keywords": b["keywords"]} for b in batches] +
            [{"label": u["label"], "url": u["url"]} for u in urls], "calls": log_, "ideas": ranked[:MAX_IDEAS], "total": len(ranked), "termMetrics": metrics, "emptySeeds": empty,
            "belowFloor": len(below), "geoNote": where, "notAsked": not_asked}
    if not_asked:
        notes.append("Not asked about, to keep the number of requests down: %s." % ", ".join(not_asked[:8]))
    if not ranked:
        return failed("planner", " ".join(["Keyword Planner returned no ideas at or above %d searches a month." % floor] + notes), time.monotonic() - started)
    state = "partial" if notes else "ok"
    return piece("planner", state, " ".join(["%s keyword ideas from %d request%s." % (format(len(ranked), ","), answered, "" if answered == 1 else "s"), where] + notes), data, time.monotonic() - started)


def _term_metrics(research, pool, *, post, account, base, notes):
    """Volumes for the converting search terms Planner did not suggest, from one historical-metrics request ({term: row}). Never raises."""
    terms = [t["term"] for t in ((((research or {}).get("account") or {}).get("data") or {}).get("terms") or {}).get("converting", [])]
    have = {kd.skey(k) for k in pool}
    want = []
    for t in terms:
        t = " ".join(str(t).lower().split())
        if t and kd.skey(t) not in have and t not in want and len(t.split()) <= 10 and len(t) <= 80:
            want.append(t)
    if not want:
        return {}
    try:
        d = post("/customers/%s:generateKeywordHistoricalMetrics" % account, dict(base, keywords=want[:MAX_TERM_METRICS]))
    except Exception as e:                                  # noqa: BLE001
        notes.append("The searches a month of the converting search terms could not be read (%s)." % rs._clip(e, 100))
        return {}
    out = {}
    for r in (d or {}).get("results") or []:
        i = kd.parse_idea(r)
        if i:
            out[i["kw"]] = i
    return out
