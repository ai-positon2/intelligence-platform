"""Market Radar, Phase 5: the industry pulse. What is happening in the
client's INDUSTRY in its country this month, grouped into themes.

    out = run_for_client(client_id, owner_email, run_id=run)

One pulse is about an industry in a country, not about a client: a dental
practice in Leeds and one in London read the same UK dental pulse, so it is
stored once (mr_pulses) and reused for a day by every client it fits.

How one pulse is read:

1. A query plan. Haiku writes four to six Google News queries for the
   industry in the country edition's language ("NHS dentistry" in the UK
   edition, "Zahnärzte" in the German one) and the words a headline about
   the industry contains. The plan never names a company.
2. Google News, last 30 days, every query. Measured 2026-10-10: a third of
   what comes back is market-research advertising ("Dental Implants Market
   Size, Share | Growth Forecast [2034]" from IndexBox, openPR, Fortune
   Business Insights) and some is off topic (a careers site's course
   pages). Both are dropped by rule, and counted, before any model sees
   them; a headline must name the industry in its own words.
3. Trade publications. A publisher that keeps covering the industry
   (DrBicuspid, Dentistry Today, dentistry.co.uk, World Footwear) is looked
   up once: its feed is found, read, and remembered in mr_industry_sources
   for the industry and country, with how much of it is about the
   industry. A feed mostly about the industry is read on every later
   pulse, so news reaches the report even when Google does not surface it.
   A publisher with no feed, or a general one, is remembered too, so it is
   not looked up again for FEED_RETRY_DAYS.
4. US only: the Federal Register's rules and proposed rules from the last
   90 days whose title or summary names the industry.
5. Themes. Sonnet groups the surviving headlines into three to eight
   themes. Every theme cites at least two of the headlines it was given by
   number (checked here; a theme that does not is dropped), and its "why it
   matters" is for a business of this kind in this country, not for one
   client: what this means for one client is the report's job (Phase 7).

Every step reports what it could not read: a query Google refused, a feed
that failed, a model call that broke. A pulse whose news could not be read
is "failed", never an empty industry.

Cost: Google News, feeds and the Federal Register are free. One Haiku call
(about $0.001) and one Sonnet call (about $0.05) per industry and country,
booked against the run's ledger.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urljoin, urlsplit

from . import market_radar_news as news

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
REGULATION_DAYS = 90
MAX_MARKETS = 2
MAX_QUERIES = 6
MAX_FOR_THEMES = 150
MAX_DISCOVERIES = 6            # publishers looked up for a feed in one pulse
MAX_FEEDS_READ = 8
FEED_RETRY_DAYS = 30
TRADE_SHARE = 0.3              # a feed this much about the industry is a trade feed
MIN_PUBLISHER_ITEMS = 2
REUSE_HOURS = 24
# Raised when the way a pulse is read changes, so a pulse read the old way
# is read again rather than reused for the rest of its day.
PULSE_VERSION = 2
PLAN_MODEL = os.environ.get("MR_PULSE_PLAN_MODEL", "claude-haiku-5-5")
THEME_MODEL = os.environ.get("MR_PULSE_THEME_MODEL", "claude-sonnet-5-5")
FEDREG_URL = "https://www.federalregister.gov/api/v1/documents.json"

# Publishers that sell market-research reports and publish their adverts as
# news. Every one of these was in the dental or footwear results on
# 2026-10-10, or is the same trade under another name.
RESEARCH_SITES = {
    "indexbox.io", "fortunebusinessinsights.com", "openpr.com", "precedenceresearch.com",
    "marketresearchfuture.com", "marketgrowthreports.com", "researchandmarkets.com",
    "alliedmarketresearch.com", "grandviewresearch.com", "mordorintelligence.com",
    "marketsandmarkets.com", "imarcgroup.com", "gminsights.com",
    "transparencymarketresearch.com", "coherentmarketinsights.com", "factmr.com",
    "futuremarketinsights.com", "persistencemarketresearch.com", "verifiedmarketresearch.com",
    "databridgemarketresearch.com", "expertmarketresearch.com", "towardshealthcare.com",
    "snsinsider.com", "straitsresearch.com", "skyquestt.com", "technavio.com",
    "businessresearchinsights.com", "cognitivemarketresearch.com", "zionmarketresearch.com",
    "emergenresearch.com", "polarismarketresearch.com", "maximizemarketresearch.com",
    "wiseguyreports.com", "htfmarketintelligence.com", "einpresswire.com",
    "marketresearch.com", "6wresearch.com", "giiresearch.com", "reportlinker.com",
    "shiksha.com",
}
# Headlines that are market-research adverts, stock tips or listicles,
# whoever publishes them. Not "market share" alone: "Hoka gains market
# share" is news.
NOISE_TITLE = re.compile(
    r"\bmarket size\b|\bsize,? share\b|\bshare,? (size|growth|trends)\b|\bcagr\b|\[20\d\d\]|"
    r"\b20[2-4]\d ?[-–/] ?20[2-4]\d\b|\bmarket (report|research|forecast|outlook)\b|"
    r"forecast (period|till|to|through) 20\d\d|\bstocks? to (watch|buy)\b|^\s*\d+ best\b|"
    r"\bbest .{0,50} (of|for|in) (20\d\d|january|february|march|april|may|june|july|"
    r"august|september|october|november|december)\b", re.I)
# Google's own placeholder in place of a headline ("META_TITLE_SECTORS",
# 2026-10-10). Case matters: "Fresh" is a headline.
PLACEHOLDER = re.compile(r"^[A-Z0-9]+(_[A-Z0-9]+)+$")
GUESSED_FEEDS = ("/feed/", "/rss.xml", "/feed", "/rss")


def _now():
    return datetime.now(timezone.utc)


def _host(url):
    host = (urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _site_of(host):
    """The registrable part of a host, roughly: "uk.news.yahoo.com" ->
    "yahoo.com". Used only to match RESEARCH_SITES."""
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_research(host):
    return host in RESEARCH_SITES or _site_of(host) in RESEARCH_SITES


def is_noise(item):
    title = (item.get("title") or "").strip()
    return bool(NOISE_TITLE.search(title) or PLACEHOLDER.match(title)) or \
        is_research(_host(item.get("publisher_site") or item.get("link")))


def term_pattern(terms):
    """A headline names the industry when one of its words BEGINS with a
    term ("dental" in "dentalcare", "zahnarzt" in "Zahnarztpraxis")."""
    folded = sorted({news.fold(t) for t in terms if len(news.fold(t)) >= 3}, key=len, reverse=True)
    if not folded:
        return None
    return re.compile(r"(?:^| )(?:%s)" % "|".join(re.escape(t) for t in folded))


def about_industry(title, pattern):
    return bool(pattern and pattern.search(news.fold(title)))


# == which industry, which countries ====================================================

def industry_key(profile):
    ind = (profile or {}).get("industry") or {}
    code = re.sub(r"\D", "", str(ind.get("naics_code") or ""))
    if len(code) == 6:
        return "naics:" + code
    label = news.fold(ind.get("plain_label") or "")
    return "label:" + label.replace(" ", "-") if label else None


def markets(profile):
    """The client's own country first. Other markets only for a business
    that serves a few countries: a brand shipping to thirty is read in its
    home market, not in the first two of thirty in alphabetical order."""
    hq = ((profile.get("hq") or {}).get("country_code") or "").upper()
    rest = [m for m in profile.get("markets") or [] if m != hq]
    out = [hq] if hq else []
    if len(profile.get("markets") or []) <= 3:
        out += rest
    out = [c for c in dict.fromkeys(out) if c]
    return out[:MAX_MARKETS] or ["US"]


def edition_language(country):
    hl = news.EDITIONS.get(country, news.EDITIONS["US"])[0]
    return hl.split("-")[0]


# == step 1: the query plan ===========================================================

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["queries", "match_terms", "regulator_terms"],
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"}},
        "match_terms": {"type": "array", "items": {"type": "string"}},
        "regulator_terms": {"type": "array", "items": {"type": "string"}}}}
PLAN_SYSTEM = """You plan news searches that follow an INDUSTRY in one country, for the owner of a business in that industry. The searches run in that country's Google News edition.

- queries: 4 to 6 searches, written in the edition's language. Keep each one short: 1 to 3 words, because every word must appear in a headline and a longer search returns nothing ("Zahnärzte Kassen Honorare OR Vergütung" found no article on 2026-10-10). Use OR only between alternatives for the same idea ("Zahnarztpraxis OR Zahnarztpraxen"). Together they cover the industry as a whole, its regulation or public policy, costs and prices (or reimbursement), staffing, customer demand, and deals or new entrants. Name the industry the way that country's press does (in the UK, dentistry news is mostly about the NHS).
- Never name a company or a product brand (not the business described, not a competitor, not a brand such as Invisalign).
- Avoid words that pull in market-research adverts ("market size", "forecast", "CAGR").
- match_terms: 6 to 20 lowercase words or word beginnings that a headline about this industry contains, in the edition's language AND in English (for dentists in Germany: zahnarzt, zahnärzt, zahnmedizin, dental, dentist). Each at least 4 letters. Never a generic word such as market, business, health, price, store, brand.
- regulator_terms: 1 to 3 plain English words a US federal rule about this industry would contain in its title (dental, dentist; footwear). Empty when no federal rule would name the industry."""


def fallback_plan(profile):
    ind = (profile.get("industry") or {})
    label = " ".join(str(ind.get("plain_label") or "").split())
    words = [w for w in news.fold(label).split() if len(w) >= 4]
    return {"queries": [label] if label else [], "match_terms": words,
            "regulator_terms": words[:2]}


def plan_queries(profile, country, *, run_id=None, client=None, llm=None):
    """(plan, note). A plan the model could not write falls back to the
    industry's own name, and the note says so."""
    if llm is None:
        from . import market_radar_llm as llm
    ind = profile.get("industry") or {}
    user = ("INDUSTRY: %s (NAICS %s, %s)\nKEYWORDS: %s\nWHAT THIS KIND OF BUSINESS SELLS: %s\n"
            "BUSINESS TYPE: %s\nCOUNTRY: %s\nEDITION LANGUAGE: %s") % (
        ind.get("plain_label") or "", ind.get("naics_code") or "", ind.get("naics_title") or "",
        ", ".join(ind.get("keywords") or []), ", ".join((profile.get("offerings") or [])[:6]),
        profile.get("archetype") or "", country, edition_language(country))
    try:
        data, _meta = llm.call_json(PLAN_SYSTEM, user, PLAN_SCHEMA, model=PLAN_MODEL,
                                    max_tokens=1500, run_id=run_id, stage="pulse_plan",
                                    client=client)
    except Exception as e:
        kind = getattr(e, "kind", type(e).__name__)
        return fallback_plan(profile), "the search plan could not be written (%s); " \
            "searched for the industry's name only" % kind
    queries = []
    for q in data.get("queries") or []:
        q = " ".join(str(q).split())
        if 2 <= len(q) <= 80 and q.lower() not in (x.lower() for x in queries):
            queries.append(q)
    terms = []
    for t in data.get("match_terms") or []:
        t = " ".join(str(t).split()).lower()
        if len(news.fold(t)) >= 4 and t not in terms:
            terms.append(t)
    reg = [" ".join(str(t).split()).lower() for t in data.get("regulator_terms") or []
           if 3 <= len(str(t).strip()) <= 30][:3]
    plan = {"queries": queries[:MAX_QUERIES], "match_terms": terms[:20], "regulator_terms": reg}
    if not plan["queries"] or not plan["match_terms"]:
        fb = fallback_plan(profile)
        plan = {k: plan[k] or fb[k] for k in plan}
        return plan, "the search plan came back incomplete; filled in from the industry's name"
    return plan, None


# == step 2: Google News ==============================================================

def read_google(plan, country, *, get, breaker, now=None):
    """(items, per_query, failures). Items are unique stories that name the
    industry, newest first; per_query says what each query returned."""
    pattern = term_pattern(plan["match_terms"])
    stories, per_query, failures = {}, [], []
    noise = off_topic = 0
    for q in plan["queries"]:
        row = {"query": q, "items": 0, "kept": 0, "status": "ok"}
        per_query.append(row)
        if breaker.open:
            row["status"], row["note"] = "not_read", "Google News stopped answering"
            failures.append("%s: not read, Google News stopped answering" % q)
            continue
        breaker.wait_turn()
        read = get(news.feed_url("%s when:%dd" % (q, WINDOW_DAYS), country), limit=3_000_000,
                   timeout=12)
        ok = read["status"] == "ok" and "<rss" in read["body"][:500]
        breaker.record(ok)
        if not ok:
            row["status"], row["note"] = "failed", read["note"] or read["status"]
            failures.append("%s: %s" % (q, row["note"]))
            continue
        items = news.parse_items(read["body"])
        row["items"] = len(items)
        for it in items:
            if not it["title"]:
                continue
            if is_noise(it):
                noise += 1
                continue
            if not about_industry(it["title"], pattern):
                off_topic += 1
                continue
            key = news._story_key(it["title"])
            if key in stories:
                stories[key]["copies"] += 1
                continue
            row["kept"] += 1
            stories[key] = dict(it, id=key, copies=1, source="google_news", query=q)
    items = sorted(stories.values(), key=lambda s: s.get("date") or "", reverse=True)
    return items, per_query, failures, {"noise": noise, "off_topic": off_topic}


# == step 3: trade publications =======================================================

def find_feed(site_url, *, fetch, get):
    """(feed_url, items, note): the publisher's own feed, from the feeds its
    homepage announces, or the usual WordPress-style addresses."""
    from . import market_radar_detectors as det
    from . import market_radar_site as site
    tried, notes = [], []
    page = fetch(site_url)
    if page["status"] == "ok":
        doc = site.parse_html(page["html"])
        tried += [urljoin(page["final_url"], h) for h, _ in doc.feeds
                  if not re.search(r"comment", h, re.I)]
    else:
        notes.append("homepage: %s" % (page.get("note") or page["status"]))
    root = "https://%s" % urlsplit(site_url).hostname
    tried += [root + g for g in GUESSED_FEEDS]
    for f in list(dict.fromkeys(tried))[:6]:
        read = get(f, limit=2_000_000, timeout=12)
        items = det.parse_feed(read["body"]) if read["status"] == "ok" else []
        if items:
            return f, items, None
        if read["status"] in ("blocked", "error"):
            notes.append("%s: %s" % (urlsplit(f).path or "/", read["note"] or read["status"]))
    return None, [], "; ".join(notes[:3]) or "no feed found"


def _feed_items(items, *, publisher, site_domain, feed_url, now, pattern, trade):
    """A feed's items from the last WINDOW_DAYS, as headlines. A trade
    feed's items are all about the industry; a feed's undated items are
    kept only when it is a trade feed (it is newest first)."""
    since = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    out = []
    for it in items[:40]:
        day = it.get("date")
        if (day and day < since) or (not day and not trade):
            continue
        if not trade and not about_industry(it["title"], pattern):
            continue
        if NOISE_TITLE.search(it["title"]):
            continue
        out.append({"title": it["title"], "publisher": publisher, "publisher_site":
                    "https://" + site_domain, "date": day, "link": it["url"],
                    "id": news._story_key(it["title"]), "copies": 1, "source": "feed",
                    "feed": feed_url})
    return out


def read_trade(key, country, google_items, plan, *, store, fetch, get, now):
    """(items, report). Reads the feeds already known to be about this
    industry, and looks up publishers that kept covering it in this read."""
    pattern = term_pattern(plan["match_terms"])
    known = store.industry_sources(key, country)
    by_site = {}
    for r in known:
        by_site.setdefault(r.get("site_domain"), []).append(r)
    report = {"read": [], "discovered": [], "failed": []}
    items = []

    # Publishers that covered the industry at least twice in this read.
    counts, names, homes = {}, {}, {}
    for it in google_items:
        host = _host(it.get("publisher_site"))
        if host and not is_research(host):
            counts[host] = counts.get(host, 0) + 1
            names[host] = it.get("publisher") or host
            homes[host] = it["publisher_site"].rstrip("/") + "/"
    retry_before = now - timedelta(days=FEED_RETRY_DAYS)

    def settled(rows):
        # A feed was found (trade or general), or the last look for one
        # was recent enough not to repeat.
        return any(r["kind"] in ("trade", "general") or
                   (r.get("created_at") or now) > retry_before for r in rows)

    fresh = [host for host, n in sorted(counts.items(), key=lambda kv: -kv[1])
             if n >= MIN_PUBLISHER_ITEMS and not settled(by_site.get(host) or [])]
    for host in fresh[:MAX_DISCOVERIES]:
        feed, raw, note = find_feed(homes[host], fetch=fetch, get=get)
        if not feed:
            store.save_industry_source(key, homes[host], country=country,
                                       site_domain=host, kind="none", discovered_from=names[host],
                                       ok=False, error=note[:300])
            report["failed"].append({"publisher": names[host], "note": note})
            continue
        share = sum(1 for i in raw if about_industry(i["title"], pattern)) / len(raw)
        trade = share >= TRADE_SHARE or about_industry(names[host], pattern)
        store.save_industry_source(key, feed, country=country, site_domain=host,
                                   kind="trade" if trade else "general",
                                   discovered_from=names[host], ok=True, item_count=len(raw))
        report["discovered"].append({"publisher": names[host], "feed": feed, "trade": trade,
                                     "share": round(share, 2), "items": len(raw)})
        if trade:
            got = _feed_items(raw, publisher=names[host], site_domain=host, feed_url=feed,
                              now=now, pattern=pattern, trade=True)
            items += got
            report["read"].append({"publisher": names[host], "feed": feed, "items": len(got),
                                   "new": True})

    # Trade feeds learned on earlier pulses.
    just_read = {r["feed"] for r in report["read"]}
    for r in [r for r in known if r["kind"] == "trade" and r["feed_url"] not in just_read]:
        if len(report["read"]) >= MAX_FEEDS_READ:
            break
        from . import market_radar_detectors as det
        read = get(r["feed_url"], limit=2_000_000, timeout=12)
        raw = det.parse_feed(read["body"]) if read["status"] == "ok" else []
        publisher = r.get("discovered_from") or r.get("site_domain")
        if not raw:
            note = read["note"] or ("no items" if read["status"] == "ok" else read["status"])
            store.save_industry_source(key, r["feed_url"], country=country, kind="trade",
                                       ok=False, error=note[:300])
            report["failed"].append({"publisher": publisher, "note": note})
            continue
        store.save_industry_source(key, r["feed_url"], country=country, kind="trade", ok=True,
                                   item_count=len(raw))
        got = _feed_items(raw, publisher=publisher, site_domain=r.get("site_domain") or "",
                          feed_url=r["feed_url"], now=now, pattern=pattern, trade=True)
        items += got
        report["read"].append({"publisher": publisher, "feed": r["feed_url"], "items": len(got),
                               "new": False})
    return items, report


# == step 4: US rules =================================================================

def federal_register(terms, match_terms, *, get_json, now):
    """(docs, note). Rules and proposed rules from the last REGULATION_DAYS
    whose title or summary names the industry. The API's own term search
    reads the whole text, so "dental" also returns an Exchange broker rule
    that mentions dental plans once (2026-10-10): the title or summary has
    to say it."""
    pattern = term_pattern(list(terms) + list(match_terms))
    since = (now - timedelta(days=REGULATION_DAYS)).strftime("%Y-%m-%d")
    docs, notes = {}, []
    for t in terms[:3]:
        params = [("conditions[term]", t), ("conditions[publication_date][gte]", since),
                  ("conditions[type][]", "RULE"), ("conditions[type][]", "PRORULE"),
                  ("order", "newest"), ("per_page", "40")]
        params += [("fields[]", f) for f in ("title", "publication_date", "type", "agencies",
                                              "html_url", "abstract")]
        data, read = get_json(FEDREG_URL + "?" + urlencode(params), timeout=20)
        if data is None:
            notes.append("%s: %s" % (t, read["note"] or read["status"]))
            continue
        for d in data.get("results") or []:
            text = "%s %s" % (d.get("title") or "", d.get("abstract") or "")
            if not about_industry(text, pattern) or d.get("html_url") in docs:
                continue
            docs[d["html_url"]] = {
                "title": " ".join((d.get("title") or "").split())[:300],
                "date": d.get("publication_date"),
                "type": {"Rule": "Final rule", "Proposed Rule": "Proposed rule"}.get(
                    d.get("type"), d.get("type")),
                "agency": ", ".join(a.get("name") or a.get("raw_name") or ""
                                    for a in d.get("agencies") or [])[:120],
                "link": d["html_url"]}
    out = sorted(docs.values(), key=lambda d: d.get("date") or "", reverse=True)
    if notes and not out and len(notes) >= len(terms[:3]):
        return None, "the Federal Register did not answer: " + "; ".join(notes)
    note = "%d rules and proposed rules in the last %d days name %s" % (
        len(out), REGULATION_DAYS, " or ".join(terms[:3]))
    if notes:
        note += "; " + "; ".join(notes)
    return out, note


# == step 5: themes ===================================================================

THEME_KINDS = ("regulation", "costs_and_prices", "workforce", "demand", "competition_and_deals",
               "technology", "supply", "other")
THEMES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["themes"],
    "properties": {"themes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["title", "summary", "why_it_matters", "kind", "items"],
        "properties": {"title": {"type": "string"}, "summary": {"type": "string"},
                       "why_it_matters": {"type": "string"},
                       "kind": {"type": "string", "enum": list(THEME_KINDS)},
                       "items": {"type": "array", "items": {"type": "integer"}}}}}}}
THEMES_SYSTEM = """You group recent news headlines about one INDUSTRY in one COUNTRY into the themes that a business owner in that industry should know about this month.

Use only the numbered headlines. Do not add facts, numbers or names that are not in them.

- title: the theme in at most 8 words, saying what is happening ("NHS dental contract reform stalls"), not a topic label ("Regulation").
- summary: one or two sentences saying what the headlines report. Use a number only if a headline states it.
- why_it_matters: one sentence on what this could mean for a business of the kind named, in that country. A consideration, not a prediction.
- kind: the closest of the listed kinds.
- items: the numbers of the headlines the theme rests on. At least 2. A headline belongs to at most one theme.

Leave out headlines that are market-research adverts, listicles and shopping guides ("the best leggings to buy", gift guides, product roundups), awards, stock tips and one company's share-price moves, celebrity, crime or human-interest stories, a single company's product news that says nothing about the industry, and news from other countries that does not affect this one. A company whose name contains the industry's word ("Gildan Activewear") is not industry news for that reason alone.

Return 3 to 8 themes, strongest first: more headlines, more recent, more consequence for the business. Fewer strong themes are better than more weak ones; return none if the headlines do not support any.

Write title, summary and why_it_matters in plain English even when the headlines are in another language: translate, and keep a local name (an agency, a law) in its own language only where it has no English form. Never use em dashes or en dashes."""


DASHES = re.compile(r"\s*[%s%s]\s*" % (chr(0x2014), chr(0x2013)))


def _clean(text, limit):
    text = DASHES.sub(", ", " ".join(str(text or "").split()))
    return text[:limit]


def themes(articles, regulation, *, label, country, run_id=None, client=None, llm=None):
    """(themes, note). Each theme lists its articles. A theme that cites
    fewer than two of the given headlines is dropped, and the note says how
    many were."""
    rows = list(articles[:MAX_FOR_THEMES]) + [
        dict(d, publisher="US Federal Register (%s)" % (d.get("agency") or "agency"),
             title="%s: %s" % (d["type"], d["title"])) for d in (regulation or [])[:20]]
    if len(rows) < 2:
        return [], "too few headlines to find themes"
    if llm is None:
        from . import market_radar_llm as llm
    user = "INDUSTRY: %s\nCOUNTRY: %s\n\nHEADLINES:\n%s" % (label, country, "\n".join(
        "%d. %s | %s | %s%s" % (i, r.get("date") or "undated", r.get("publisher") or "",
                               r["title"], " (%d outlets)" % r["copies"]
                               if (r.get("copies") or 1) > 1 else "")
        for i, r in enumerate(rows)))
    try:
        data, _meta = llm.call_json(THEMES_SYSTEM, user, THEMES_SCHEMA, model=THEME_MODEL,
                                    max_tokens=6000, run_id=run_id, stage="pulse_themes",
                                    client=client)
    except Exception as e:
        return None, "themes could not be written (%s: %s)" % (
            getattr(e, "kind", type(e).__name__), str(getattr(e, "detail", e))[:200])
    out, used, dropped = [], set(), 0
    for t in data.get("themes") or []:
        idx = []
        for i in t.get("items") or []:
            if isinstance(i, int) and 0 <= i < len(rows) and i not in used and i not in idx:
                idx.append(i)
        title = _clean(t.get("title"), 120)
        if len(idx) < 2 or not title:
            dropped += 1
            continue
        used.update(idx)
        cited = [rows[i] for i in idx]
        out.append({
            "title": title, "summary": _clean(t.get("summary"), 500),
            "why_it_matters": _clean(t.get("why_it_matters"), 400),
            "kind": t.get("kind") if t.get("kind") in THEME_KINDS else "other",
            "articles": [{k: r.get(k) for k in ("title", "publisher", "date", "link", "source")}
                         for r in sorted(cited, key=lambda r: r.get("date") or "", reverse=True)],
            "publishers": len({r.get("publisher") for r in cited}),
            "latest": max((r.get("date") or "" for r in cited), default="") or None})
    note = None
    if dropped:
        note = "%d theme%s cited fewer than two of the headlines and %s left out" % (
            dropped, "" if dropped == 1 else "s", "was" if dropped == 1 else "were")
    return out[:8], note


# == one pulse ========================================================================

LANGUAGES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish",
             "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "sv": "Swedish",
             "no": "Norwegian", "da": "Danish", "fi": "Finnish", "pl": "Polish",
             "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "hi": "Hindi", "ar": "Arabic",
             "tr": "Turkish", "id": "Indonesian", "he": "Hebrew", "cs": "Czech"}


def edition_words(edition):
    """A Google News edition code as words for the page: "GB:en" ->
    "English", "BR:pt-419" -> "Portuguese" (the country is the section's
    heading already)."""
    lang = (str(edition or "").split(":")[-1].split("-")[0] or "").lower()
    return LANGUAGES.get(lang, "the %s edition" % (edition or "default"))


def build_pulse(profile, key, country, *, store, get, get_json, fetch, breaker, now=None,
                run_id=None, client=None, llm=None):
    now = now or _now()
    label = (profile.get("industry") or {}).get("plain_label") or key
    edition = news.EDITIONS.get(country, news.EDITIONS["US"])[2]
    notes = []
    plan, plan_note = plan_queries(profile, country, run_id=run_id, client=client, llm=llm)
    if plan_note:
        notes.append(plan_note)
    google, per_query, failures, dropped = read_google(plan, country, get=get, breaker=breaker,
                                                       now=now)
    trade, feeds = read_trade(key, country, google, plan, store=store, fetch=fetch, get=get,
                              now=now)
    seen, articles = set(), []
    for it in sorted(google + trade, key=lambda s: s.get("date") or "", reverse=True):
        if it["id"] not in seen:
            seen.add(it["id"])
            articles.append(it)
    regulation, reg_note = None, None
    if country == "US" and plan["regulator_terms"]:
        regulation, reg_note = federal_register(plan["regulator_terms"], plan["match_terms"],
                                                get_json=get_json, now=now)
    read_ok = sum(1 for q in per_query if q["status"] == "ok")
    payload = {
        "version": PULSE_VERSION,
        "industry_key": key, "country": country, "edition": edition, "label": label,
        "plan": plan, "window_days": WINDOW_DAYS, "read_at": now.isoformat(timespec="seconds"),
        "articles": [{k: a.get(k) for k in ("id", "title", "publisher", "publisher_site", "date",
                                            "link", "copies", "source")} for a in articles[:200]],
        "regulation": regulation, "regulation_note": reg_note, "feeds": feeds,
        "coverage": {"queries": per_query, "noise_dropped": dropped["noise"],
                     "off_topic_dropped": dropped["off_topic"], "failures": failures},
    }
    if not read_ok and not trade:
        payload.update(status="failed", themes=[], note="no news could be read: " +
                       "; ".join(failures[:3] or ["no query was sent"]))
        return payload
    found, theme_note = themes(articles, regulation, label=label, country=country,
                               run_id=run_id, client=client, llm=llm)
    if theme_note:
        notes.append(theme_note)
    payload["themes"] = found or []
    payload["status"] = "ok" if found is not None else "partial"
    parts = ["%d headlines about %s from Google News in %s, %d of %d searches answered" % (
        len(articles), label, edition_words(edition), read_ok, len(per_query))]
    if feeds["read"]:
        parts.append("%d from %d trade publication feeds" % (
            sum(f["items"] for f in feeds["read"]), len(feeds["read"])))
    if dropped["noise"]:
        parts.append("%d market-research adverts and listicles left out" % dropped["noise"])
    if failures:
        parts.append("not read: " + "; ".join(failures[:3]))
    payload["note"] = "; ".join(parts + notes)
    return payload


def run_for_client(client_id, owner_email, *, run_id=None, store=None, io=None, now=None,
                   llm=None, client=None, breaker=None):
    """The pulse of the client's industry in each of its markets, read fresh
    or reused from the last REUSE_HOURS. {"pulses": [{country, industry_key,
    status, note, themes, reused}]} or {"skipped": reason}."""
    if store is None:
        from . import market_radar_store as store
    from . import market_radar_views as views
    io = io or {}
    if not all(k in io for k in ("get", "get_json", "fetch")):
        from . import market_radar_http as http
        from . import market_radar_site as site
        io = dict({"get": http.get, "get_json": http.get_json, "fetch": site.fetch}, **io)
    now = now or _now()
    breaker = breaker or news.Breaker()
    c = store.get_client(client_id, owner_email)
    if not c:
        raise PermissionError("client %s is not yours or does not exist" % client_id)
    profile = views.effective_profile(c["profile"] or {}, c["settings"])
    key = industry_key(profile)
    if not key:
        return {"skipped": "the industry is not known; set it on the profile first"}
    out = []
    for country in markets(profile):
        last = store.latest_pulse(key, country)
        if last and now - last["created_at"] < timedelta(hours=REUSE_HOURS) and \
                (last["payload"] or {}).get("status") == "ok" and \
                (last["payload"] or {}).get("version") == PULSE_VERSION:
            p = last["payload"]
            out.append({"country": country, "industry_key": key, "status": p["status"],
                        "note": p.get("note"), "themes": len(p.get("themes") or []),
                        "reused": True, "pulse_id": last["id"]})
            continue
        try:
            p = build_pulse(profile, key, country, store=store, get=io["get"],
                            get_json=io["get_json"], fetch=io["fetch"], breaker=breaker, now=now,
                            run_id=run_id, client=client, llm=llm)
        except Exception as e:
            logger.exception("market_radar_pulse %s %s failed", key, country)
            out.append({"country": country, "industry_key": key, "status": "failed",
                        "note": "%s: %s" % (type(e).__name__, str(e)[:300])})
            continue
        pulse_id = store.save_pulse(key, country, p, run_id=run_id)
        out.append({"country": country, "industry_key": key, "status": p["status"],
                    "note": p.get("note"), "themes": len(p.get("themes") or []),
                    "reused": False, "pulse_id": pulse_id})
    return {"pulses": out}
