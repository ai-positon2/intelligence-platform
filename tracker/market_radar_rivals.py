"""Market Radar, Phase 2: from a company profile to a ranked competitor list.

    result = discover(profile, run_id=run, client_id=c, owner_email=me)

No single source is trusted on its own. Candidates come from up to five
places, each keyed by website domain (two firms can share a name; they
cannot share a domain):

  1. the client's own site: companies it names as competitors or alternatives
     (read in Phase 1, profile["competitors_named"]);
  2. the model's own knowledge: one planning call (Sonnet 5.5, no tools)
     names the competitors a well-informed analyst would name, with their
     domains, and writes the web searches. Nothing it names is kept unless
     that domain answers and its homepage says what the model claimed;
  3. Google web results, through the platform's existing Apify account;
  4. "top 10" style articles found by those searches: the companies they
     link to (articles and directories are dropped as competitors, but what
     they list is often exactly the list wanted);
  5. for a local business, every place of the same category within the
     radius, from Overture Maps' open places data (free).

Every candidate's homepage is then read by this server and judged by Haiku
5.5, in batches: is it a business (not a directory, article, marketplace or
social page), does it sell the same thing, to the same people, where. A
candidate whose site could not be read is not silently kept or dropped: it
is reported as unread. Sonnet 5.5 then ranks the survivors and labels each:
direct, indirect, local, aspirational. The ranking can only choose from the
verified list; a domain it adds is dropped and reported.

Every paid call goes through the run's cost ledger ($1.00 cap by default).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from . import market_radar_llm as llm
from . import market_radar_site as site_reader

logger = logging.getLogger(__name__)

PLAN_MODEL = os.environ.get("MR_RIVALS_PLAN_MODEL", "claude-sonnet-5-5")
VERIFY_MODEL = os.environ.get("MR_RIVALS_VERIFY_MODEL", "claude-haiku-5-5")
RANK_MODEL = os.environ.get("MR_RIVALS_RANK_MODEL", "claude-sonnet-5-5")

MAX_QUERIES = 10
SEARCH_GROUP = 4           # queries per Apify run; the runs go in parallel
MAX_KNOWN = 15
MAX_PLACES = 25            # nearest same-category places with a website
MAX_VERIFY = 70            # candidates read and judged in the first round
MAX_ARTICLES = 4           # "top 10" pages whose links are read
MAX_FROM_ARTICLE = 15
MAX_VERIFY_ROUND2 = 30
MAX_ARCHIVED = 10          # refused homepages retried from the Wayback Machine
VERIFY_BATCH = 8
TEXT_CHARS = 1200
FETCH_WORKERS = 8
DEFAULT_RADIUS_KM = 5
MIN_PLACES = 8             # fewer than this nearby: widen the radius once
MIN_SCORE = 50             # a weaker match is listed as left out, not as a competitor
LIMITS = {"local_single": 15, "multi_location": 12, "ecommerce": 12}
DEFAULT_LIMIT = 10
SEARCH_LANGUAGES = {"ar", "bg", "ca", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "hr",
                    "hu", "id", "is", "it", "iw", "ja", "ko", "lt", "lv", "nl", "no", "pl", "pt",
                    "ro", "ru", "sk", "sl", "sr", "sv", "th", "tr", "uk", "zh-CN", "zh-TW"}

# Never competitors and never worth a fetch: social networks, marketplaces,
# search engines, encyclopedias, review and booking directories. A site that
# is a directory but is not listed here is still caught by the check.
SKIP_DOMAINS = (
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
    "tiktok.com", "pinterest.com", "reddit.com", "quora.com", "medium.com", "wikipedia.org",
    "wikimedia.org", "google.com", "goo.gl", "g.page", "apple.com", "bing.com", "yahoo.com",
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.in", "amazon.com.br", "ebay.com",
    "ebay.co.uk", "ebay.de", "etsy.com", "walmart.com", "aliexpress.com", "alibaba.com",
    "flipkart.com", "mercadolivre.com.br", "zalando.de", "zalando.co.uk", "asos.com",
    "yelp.com", "yelp.co.uk", "tripadvisor.com", "trustpilot.com", "bbb.org", "yellowpages.com",
    "yell.com", "glassdoor.com", "indeed.com", "crunchbase.com", "zoominfo.com",
    "zocdoc.com", "healthgrades.com", "nhs.uk", "practo.com", "justdial.com", "doctolib.de",
    "jameda.de", "sulekha.com", "mapquest.com", "foursquare.com", "nextdoor.com",
    "wa.me", "whatsapp.com", "linktr.ee", "bit.ly", "t.co",
    # affiliate and app-link redirectors, linked from "best of" articles
    "redirectingat.com", "viglink.com", "onelink.me", "app.link", "linksynergy.com",
    "awin1.com", "shareasale.com", "skimresources.com", "amzn.to", "pntrs.com", "sjv.io",
    "anrdoezrs.net", "dpbolvw.net", "jdoqocy.com", "kqzyfj.com", "tkqlhce.com", "howl.me",
)
_SKIP_SUFFIXES = tuple("." + d for d in SKIP_DOMAINS)


def skip_domain(domain):
    d = (domain or "").lower()
    return d in SKIP_DOMAINS or d.endswith(_SKIP_SUFFIXES)


def domain_of(url_or_host):
    from .market_radar_store import normalize_domain
    try:
        return normalize_domain(url_or_host)
    except ValueError:
        return None


_DASH = re.compile(r"\s*[–—]\s*")


def _clean(value):
    return _DASH.sub(", ", value).strip() if isinstance(value, str) else value


# == what the prompts are told about the client =====================================

def client_brief(profile):
    hq = profile.get("hq") or {}
    ind = profile.get("industry") or {}
    return {
        "name": profile.get("name"),
        "website": (profile.get("facts") or {}).get("website"),
        "domain": (profile.get("facts") or {}).get("domain"),
        "what_they_do": profile.get("one_liner"),
        "offerings": (profile.get("offerings") or [])[:8],
        "customers": profile.get("customer_type"),
        "business_type": profile.get("archetype"),
        "business_model": profile.get("business_model"),
        "locations": profile.get("location_count"),
        "headquarters": ", ".join(x for x in (hq.get("city"), hq.get("region"),
                                               hq.get("country_code")) if x),
        "markets": profile.get("markets") or [],
        "service_area": profile.get("service_area"),
        "price_positioning": profile.get("price_positioning"),
        "industry": ind.get("plain_label"),
        "industry_keywords": (ind.get("keywords") or [])[:10],
        "languages": profile.get("languages") or [],
    }


# == 1. the plan: searches to run, competitors already known =========================

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["queries", "search_country", "search_language", "known", "place_categories",
                 "place_terms", "radius_km", "notes"],
    "properties": {
        "queries": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["q", "purpose"],
            "properties": {"q": {"type": "string"}, "purpose": {"type": "string"}}}},
        "search_country": {"type": "string"},
        "search_language": {"type": "string"},
        "known": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "domain", "relation", "why"],
            "properties": {"name": {"type": "string"}, "domain": {"type": "string"},
                           "relation": {"type": "string",
                                        "enum": ["direct", "indirect", "aspirational"]},
                           "why": {"type": "string"}}}},
        "place_categories": {"type": "array", "items": {"type": "string"}},
        "place_terms": {"type": "array", "items": {"type": "string"}},
        "radius_km": {"type": "integer"},
        "notes": {"type": "string"},
    },
}

PLAN_SYSTEM = """You plan how to find a company's competitors, for a market-intelligence report. You are given the company's profile, read from its own website.

Return:
- queries: up to 10 Google web searches whose results would be competitors' OWN websites, or articles listing such companies ("best X in Y", "brands like Z", "alternatives to Z"). Write each in the language people in the company's main market search in. For a local business, name its city or neighbourhood. For a chain, search for the other chains in its country. For an online brand, search by product category and by "brands like / alternatives to" the brand. Do not search for the company's own name alone. Each under 12 words. purpose: one short phrase.
- search_country: ISO 3166-1 alpha-2 code (lowercase) of the country whose Google to search, normally the headquarters' country or main market.
- search_language: the language code of the queries (e.g. en, de, pt, es, ja).
- known: competitors you already know of with confidence, as an analyst of this industry would name them, each with its main website domain (e.g. "rothys.com"). Only real companies you are sure exist and compete with THIS company in its market; it is fine to return none, and for a small local business you normally should, because you cannot know its neighbours. Every domain will be checked by fetching it, so a guessed domain is wasted. relation: direct (same offering, same customers, same market), indirect (a substitute), aspirational (a much larger player the company is measured against). why: one short sentence.
- place_categories: for a business serving customers at a physical location, the Overture Maps place categories its competitors would be listed under, in Overture's snake_case English vocabulary (examples: dentist, dental_clinic, orthodontist, gym, fitness_trainer, yoga_studio, restaurant, pizza_restaurant, hair_salon, beauty_salon, shoe_store, clothing_store, pharmacy, veterinarian, physical_therapist, accountant, lawyer, real_estate_agent). Empty for online-only businesses.
- place_terms: 1 to 3 lowercase word stems that every such category contains (e.g. "dent", "orthodont"), used as a catch-all.
- radius_km: for a local business, how far its customers usually travel (a city dentist: 3 to 5; a rural one: 15 to 25). 0 when not local.
- notes: anything about this company that makes its competitors unusual, or "".
Do not use em dashes or en dashes."""


def plan_search(profile, *, run_id=None, client=None):
    user = "COMPANY PROFILE:\n" + json.dumps(client_brief(profile), ensure_ascii=False, indent=1)
    plan, meta = llm.call_json(PLAN_SYSTEM, user, PLAN_SCHEMA, model=PLAN_MODEL, max_tokens=4000,
                               run_id=run_id, stage="rivals_plan", client=client)
    queries, seen = [], set()
    for q in plan.get("queries") or []:
        text = " ".join((q.get("q") or "").split())
        if text and text.lower() not in seen and len(text.split()) <= 32:
            seen.add(text.lower())
            queries.append({"q": text, "purpose": _clean(q.get("purpose") or "")})
    plan["queries"] = queries[:MAX_QUERIES]
    plan["known"] = (plan.get("known") or [])[:MAX_KNOWN]
    country = (plan.get("search_country") or "").strip().lower()
    if len(country) != 2:
        country = ((profile.get("hq") or {}).get("country_code") or "us").lower()
    plan["search_country"] = "gb" if country == "uk" else country
    lang = (plan.get("search_language") or "").strip()
    plan["search_language"] = lang if lang in SEARCH_LANGUAGES else None
    plan["radius_km"] = max(0, min(int(plan.get("radius_km") or 0), 25))
    return plan, meta


# == candidates =========================================================================

class Pool:
    """Candidates keyed by domain, each remembering every way it was found."""

    def __init__(self, own_domain):
        self.own = own_domain
        self.items = {}
        self.skipped = {}           # domain -> why it was never fetched

    def add(self, url_or_domain, via, *, name=None, url=None, **extra):
        domain = domain_of(url_or_domain)
        if not domain:
            return None
        if domain == self.own or (self.own and (domain.endswith("." + self.own)
                                                or self.own.endswith("." + domain))):
            return None
        if skip_domain(domain):
            self.skipped.setdefault(domain, "a social network, marketplace or directory")
            return None
        item = self.items.setdefault(domain, {"domain": domain, "names": [], "via": [],
                                              "url": None})
        if name and name not in item["names"]:
            item["names"].append(name)
        if url and not item["url"]:
            item["url"] = url
        item["via"].append(dict(extra, kind=via))
        return item

    def ordered(self):
        """Most-evidenced first: several independent sources, then the site's
        own word, the nearest places, the model's knowledge, top search hits."""
        weight = {"site": 5, "places": 3, "model": 3, "article": 2, "search": 1}

        def score(item):
            kinds = {v["kind"] for v in item["via"]}
            best_pos = min([v.get("position") or 10 for v in item["via"] if v["kind"] == "search"]
                           or [10])
            near = min([v.get("distance_km", 99) for v in item["via"] if v["kind"] == "places"]
                       or [99])
            return (-(sum(weight.get(k, 1) for k in kinds) + len(item["via"]) * 0.2),
                    near, best_pos, item["domain"])
        return sorted(self.items.values(), key=score)


def from_site(pool, profile):
    n = 0
    for c in profile.get("competitors_named") or []:
        if pool.add(c.get("url") or "", "site", name=c.get("name")):
            n += 1
    return {"status": "ok", "found": n}


def from_plan(pool, plan):
    n = 0
    for k in plan.get("known") or []:
        if pool.add(k.get("domain") or "", "model", name=k.get("name"),
                    relation=k.get("relation"), why=_clean(k.get("why"))):
            n += 1
    return {"status": "ok", "found": n}


def from_search(pool, plan, *, run_id=None, token=None, search=None):
    """Organic results become candidates; every result page is kept so the
    "top 10" articles among them can be read later.

    The queries go to Apify in parallel groups of SEARCH_GROUP: the actor
    works through one run's queries one after another, and ten queries in a
    single run took 134 s on 2026-10-09 (austincitydental.com). Each extra
    run adds only a $0.00005 start event."""
    queries = [q["q"] for q in plan.get("queries") or []]
    if not queries:
        return {"status": "skipped", "note": "the plan had no searches"}, []
    if search is None:
        from .market_radar_search import search
    token = token if token is not None else os.environ.get("APIFY_API_TOKEN", "")
    groups = [queries[i:i + SEARCH_GROUP] for i in range(0, len(queries), SEARCH_GROUP)]

    def one(group):
        try:
            return search(group, token=token, country=plan["search_country"],
                          search_language=plan.get("search_language"), run_id=run_id,
                          stage="rivals_search")
        except Exception as e:
            return {"results": [], "error": "%s: %s" % (type(e).__name__, str(e)[:300])}

    with ThreadPoolExecutor(max_workers=len(groups)) as ex:
        outs = list(ex.map(one, groups))
    results = [r for out in outs for r in (out.get("results") or [])]
    for r in results:
        pool.add(r.get("url") or "", "search", name=None, url=None, query=r.get("query"),
                 position=r.get("position"), title=(r.get("title") or "")[:160],
                 result_url=r.get("url"))
    errors = [out["error"] for out in outs if out.get("error")]
    charged = [out.get("charged_usd") for out in outs]
    meta = {"status": "ok" if not errors else ("partial" if results else "failed"),
            "error": "; ".join(errors) or None, "queries": len(queries), "runs": len(groups),
            "results": len(results),
            "charged_usd": round(sum(c for c in charged if c), 6) if any(charged) else None,
            "elapsed_ms": max([out.get("elapsed_ms") or 0 for out in outs] or [0])}
    return meta, results


def _place_domain(place):
    for w in place.get("websites") or []:
        d = domain_of(w)
        if d:
            return d, w
    return None, None


def from_places(pool, profile, plan, *, places=None):
    """Same-category places near the client, nearest first, from Overture."""
    if places is None:
        from . import market_radar_places as places
    point = profile.get("hq_point")
    if not point or point.get("lat") is None:
        return {"status": "skipped", "note": "the client's location is not known"}
    radius = plan.get("radius_km") or DEFAULT_RADIUS_KM
    by_user = bool(plan.get("radius_set_by_user"))
    own = pool.own
    meta = {"status": "ok", "radius_km": radius, "located_by": point.get("source"),
            "location_precision": point.get("precision") or "unknown"}
    if point.get("precision") == "city":
        meta["warning"] = ("the client was located only by its city, so distances are from the "
                           "city centre and nearby rivals may be missed")
    try:
        # The client's own listing tells us exactly which category it is in.
        near_self = places.query(point["lat"], point["lon"], places.SELF_RADIUS_KM)
        mine = [p for p in near_self if _place_domain(p)[0] == own]
        categories = set(plan.get("place_categories") or [])
        if mine:
            meta["own_listing"] = {"name": mine[0]["name"], "category": mine[0]["category"],
                                   "taxonomy": mine[0]["taxonomy"]}
            categories.update(c for c in (mine[0]["category"], mine[0]["taxonomy"]) if c)
        terms = plan.get("place_terms") or []
        if not categories and not terms:
            return dict(meta, status="skipped", note="no place category to look for")
        meta["categories"], meta["terms"] = sorted(categories), terms
        found = places.query(point["lat"], point["lon"], radius, categories=categories,
                             terms=terms)
        if len(found) < MIN_PLACES and radius < 25 and not by_user:
            radius = min(25, radius * 3)
            meta["radius_km"] = radius
            meta["widened"] = "fewer than %d places nearby" % MIN_PLACES
            found = places.query(point["lat"], point["lon"], radius, categories=categories,
                                 terms=terms)
    except Exception as e:
        return {"status": "failed", "error": "%s: %s" % (type(e).__name__, str(e)[:300])}
    open_places = [p for p in found if (p.get("status") or "open") != "permanently_closed"]
    by_domain, no_site, social = {}, 0, 0
    for p in open_places:
        d, w = _place_domain(p)
        if not d:
            no_site += 1
            continue
        if skip_domain(d):
            social += 1
            continue
        if d == own:
            continue
        by_domain.setdefault(d, []).append((p, w))
    added = 0
    for d, rows in sorted(by_domain.items(), key=lambda kv: kv[1][0][0]["distance_km"]):
        if added >= MAX_PLACES:
            break
        p, w = rows[0]
        pool.add(w, "places", name=p.get("name"), url=w, distance_km=p["distance_km"],
                 category=p.get("taxonomy") or p.get("category"),
                 address=", ".join(x for x in (p.get("street"), p.get("city")) if x),
                 branches_nearby=len(rows))
        added += 1
    meta.update(found=len(found), closed=len(found) - len(open_places), without_website=no_site,
                website_is_social=social, companies=len(by_domain), added=added)
    return meta


# == reading a candidate's homepage =====================================================

_LINK = re.compile(r"\s*\[(?:https?://|/)[^\]]*\]")


def read_home(item):
    """What the check needs to know about a candidate, from its own homepage."""
    url = item.get("url") or "https://%s/" % item["domain"]
    try:
        page, notes = site_reader.choose_home(url)
    except Exception as e:
        return {"status": "error", "note": "%s" % type(e).__name__}
    if page["status"] != "ok":
        return {"status": page["status"], "note": page.get("note")}
    return _brief(page, notes)


def read_archived(item):
    """The Wayback Machine's latest copy of a homepage that refused this
    server (planetfitness.com and purebarre.com answered 403 from Railway,
    2026-10-09). Labelled with its capture date wherever it is shown."""
    url = "https://%s/" % item["domain"]
    try:
        page = site_reader.fetch_archived(url)
    except Exception as e:
        return {"status": "error", "note": "archive: %s" % type(e).__name__}
    if page.get("status") != "ok":
        return {"status": page.get("status") or "error", "note": page.get("note")}
    brief = _brief(page, [page.get("note") or ""])
    stamp = (page.get("via") or "").split(":", 1)[-1]
    brief["archived"] = "%s-%s-%s" % (stamp[:4], stamp[4:6], stamp[6:8]) if len(stamp) >= 8 else "unknown date"
    brief["final_domain"] = item["domain"]
    return brief


def _brief(page, notes):
    doc = site_reader.parse_html(page["html"])
    text = _LINK.sub("", page["text"])
    text = " ".join(text.split())[:TEXT_CHARS]
    orgs = site_reader.organizations(site_reader._jsonld_nodes(doc.jsonld_raw))
    countries = sorted({o["address"]["country"] for o in orgs
                        if o.get("address") and o["address"].get("country")})
    final = domain_of(page["final_url"])
    return {"status": "ok", "final_domain": final, "head": site_reader.page_head(doc).strip(),
            "text": text, "lang": doc.lang, "address_countries": countries,
            "words": len(page["text"].split()), "notes": notes}


def worth_archive(brief):
    """A site that refused, stalled or answered oddly (Gymshark's rivals on
    2026-10-09: alphaleteathletics.com and adidas.com 403, lululemon.com a
    read timeout, underarmour.com HTTP 418) may still be read from the
    archive. A domain that does not resolve or a page that is gone is not
    retried: an old copy of a dead company is not a competitor."""
    if brief.get("status") not in ("blocked", "error"):
        return False
    note = (brief.get("note") or "").lower()
    return not any(x in note for x in ("gaierror", "name or service", "nodename", "not found"))


def archive_unread(items, briefs, archive_reader=read_archived):
    """Second try, from the archive, for candidates whose site refused us and
    that the map does not already vouch for. Bounded: the archive is slow."""
    todo = [i for i in items if worth_archive(briefs.get(i["domain"]) or {})
            and not any(v["kind"] == "places" for v in i["via"])][:MAX_ARCHIVED]
    if not todo:
        return 0
    got = read_all(todo, reader=archive_reader)
    n = 0
    for d, b in got.items():
        if b.get("status") == "ok":
            briefs[d] = b
            n += 1
        else:
            briefs[d] = dict(briefs[d], note="%s; archive: %s" % (briefs[d].get("note"),
                                                                 b.get("note") or b.get("status")))
    return n


def read_all(items, reader=read_home):
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        return dict(zip([i["domain"] for i in items], pool.map(reader, items)))


# == 2. the check: one Haiku call per batch of candidates ============================

VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["domain", "site_type", "name", "sells", "same_offering", "same_customers",
                     "where", "location", "scale", "locations", "reason"],
        "properties": {
            "domain": {"type": "string"},
            "site_type": {"type": "string", "enum": [
                "business", "directory_or_marketplace", "article_or_publisher",
                "social_or_profile", "parked_or_empty", "other"]},
            "name": {"type": "string"},
            "sells": {"type": "string"},
            "same_offering": {"type": "string", "enum": ["yes", "partly", "no", "unclear"]},
            "same_customers": {"type": "string", "enum": ["yes", "partly", "no", "unclear"]},
            "where": {"type": "string", "enum": ["same_city", "same_region", "same_country",
                                                "other_country", "worldwide", "unclear"]},
            "location": {"type": "string"},
            "scale": {"type": "string", "enum": ["smaller", "similar", "larger", "much_larger",
                                                "unclear"]},
            "locations": {"type": "string", "enum": ["one", "few", "many", "online_only",
                                                    "unclear"]},
            "reason": {"type": "string"}}}}},
}

VERIFY_SYSTEM = """You check candidate competitors for a company, one website at a time, using only the homepage text given for each. You have no search tool and must not use outside knowledge about a company with the same name: two firms often share a name, and the candidate is the one at that domain.

For each candidate return:
Judge the business behind the site. A group, parent company or support organisation whose own site speaks to dentists, partners, investors or job seekers, but which runs or backs offices, clinics, stores or brands that serve the client's kind of customer, is a business that sells the same offering to the same customers (answer "partly" for same_customers). Heartland Dental and Smile Brands are examples: they back hundreds of dental offices.

- site_type: business (a company selling its own products or services), directory_or_marketplace (lists or sells many other businesses: review sites, booking platforms, retailers of many brands), article_or_publisher (news, blog, magazine, a "best of" list), social_or_profile, parked_or_empty (no real content, for sale, under construction), other.
- name: the business's name as the page gives it.
- sells: what it sells, in under 12 words.
- same_offering: does it sell what the client sells (yes), something overlapping or a substitute (partly), or something else (no)?
- same_customers: the same kind of customer (consumers vs businesses, similar segment)?
- where: where it operates relative to the client's headquarters.
- location: its city and country if the page says, else "".
- scale: its size compared with the client, judged from the page (number of locations, countries, range).
- locations: how many physical sites it has: one, few (2 to 9), many (10 or more), online_only, or unclear.
- reason: one plain sentence a client would accept, citing what the page says. No em dashes or en dashes.
Return one verdict per candidate, with the candidate's domain exactly as given."""


def _candidate_line(item, brief):
    via = []
    for v in item["via"][:4]:
        if v["kind"] == "places":
            via.append("listed on a map %.1f km from the client as %s" % (v["distance_km"],
                                                                         v.get("category")))
        elif v["kind"] == "search":
            via.append("Google result for %r" % v.get("query"))
        elif v["kind"] == "model":
            via.append("suggested as a known competitor")
        elif v["kind"] == "site":
            via.append("named on the client's own site")
        elif v["kind"] == "article":
            via.append("linked from an article at %s" % v.get("article"))
    if brief.get("archived"):
        via.append("homepage read from the Wayback Machine's copy of %s (the site refuses our "
                   "server)" % brief["archived"])
    return {"domain": item["domain"], "found_as": via, "homepage_title_and_description":
            brief.get("head", ""), "homepage_text": brief.get("text", ""),
            "addresses_in_structured_data": brief.get("address_countries", [])}


def verify(items, briefs, profile, *, run_id=None, client=None):
    """Haiku's verdict for every readable candidate, by domain, and the
    errors of any batch that failed (its candidates stay unjudged)."""
    readable = [i for i in items if (briefs.get(i["domain"]) or {}).get("status") == "ok"]
    batches = [readable[i:i + VERIFY_BATCH] for i in range(0, len(readable), VERIFY_BATCH)]
    head = "CLIENT:\n" + json.dumps(client_brief(profile), ensure_ascii=False) + "\n\nCANDIDATES:\n"

    def one(batch):
        user = head + json.dumps([_candidate_line(i, briefs[i["domain"]]) for i in batch],
                                 ensure_ascii=False, indent=1)
        try:
            parsed, meta = llm.call_json(VERIFY_SYSTEM, user, VERIFY_SCHEMA, model=VERIFY_MODEL,
                                         max_tokens=600 + 300 * len(batch), run_id=run_id,
                                         stage="rivals_verify", client=client)
            return parsed.get("verdicts") or [], None
        except llm.ModelError as e:
            return [], {"kind": e.kind, "detail": e.detail[:200],
                        "domains": [i["domain"] for i in batch]}

    verdicts, errors = {}, []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for got, err in ex.map(one, batches):
            if err:
                errors.append(err)
            for v in got:
                d = (v.get("domain") or "").lower()
                if d in briefs and d not in verdicts:
                    v["reason"] = _clean(v.get("reason"))
                    v["sells"] = _clean(v.get("sells"))
                    verdicts[d] = v
    return verdicts, errors


def too_small_for(verdict, archetype):
    """A single office is not a rival to a chain at the chain's level (Aspen
    Dental's list had single practices in Richardson and Rancho Cucamonga,
    2026-10-09). A chain's local rivals belong to the per-region radar."""
    return archetype == "multi_location" and verdict.get("locations") == "one"


def passes(verdict):
    return (verdict.get("site_type") == "business"
            and verdict.get("same_offering") in ("yes", "partly")
            and verdict.get("same_customers") != "no")


# == "top 10" articles: the companies they link to ======================================

_LISTY = re.compile(r"\b(best|top|alternatives?|vs\.?|versus|like|compared?|leading|biggest|"
                    r"largest|melhores|beste[nr]?|mejores|meilleur[se]?|migliori|alternativen)\b|"
                    r"\b\d{1,2}\b", re.I)


def articles_to_read(results, verdicts, briefs):
    """Search results on sites judged to be articles or directories whose title
    reads like a list, best-placed first."""
    seen, out = set(), []
    for r in sorted(results, key=lambda r: r.get("position") or 99):
        d = domain_of(r.get("url") or "")
        if not d or r["url"] in seen:
            continue
        if not _LISTY.search(r.get("title") or "") or skip_domain(d):
            continue
        site_type = (verdicts.get(d) or {}).get("site_type")
        # Judged an article or directory, or never judged (beyond the first
        # round's cap): either way its page may list the companies wanted.
        if site_type in ("article_or_publisher", "directory_or_marketplace") or d not in verdicts:
            seen.add(r["url"])
            out.append(r)
        if len(out) >= MAX_ARTICLES:
            break
    return out


def links_from_article(url, fetch=None):
    """(domain, anchor text, href) for each outside site the page links to,
    in page order, without repeats."""
    fetch = fetch or site_reader.fetch
    page = fetch(url)
    if page.get("status") != "ok":
        return None, page.get("note") or page.get("status")
    doc = site_reader.parse_html(page["html"])
    home = domain_of(page.get("final_url") or url)
    out, seen = [], set()
    for href, text in doc.links:
        absolute = urljoin(page.get("final_url") or url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        d = domain_of(absolute)
        if not d or d == home or (home and (d.endswith("." + home) or home.endswith("." + d))):
            continue
        if d in seen or skip_domain(d):
            continue
        seen.add(d)
        out.append((d, text[:80], absolute))
    return out, None


def from_articles(pool, articles, *, fetch=None):
    meta = {"read": [], "added": 0}
    before = set(pool.items)
    for r in articles:
        links, err = links_from_article(r["url"], fetch=fetch)
        src = domain_of(r["url"])
        if err:
            meta["read"].append({"url": r["url"], "error": err})
            continue
        kept = 0
        for d, text, href in links:
            if kept >= MAX_FROM_ARTICLE:
                break
            parts = urlsplit(href)
            if parts.path not in ("", "/") and len(parts.path.strip("/").split("/")) > 1:
                continue          # a deep link (a product, a post), not a company's site
            if pool.add(d, "article", name=text or None, article=src, article_url=r["url"]):
                kept += 1
        meta["read"].append({"url": r["url"], "links": len(links), "kept": kept})
    meta["added"] = len(set(pool.items) - before)
    return meta


def _expected(item):
    """An unread candidate a reader would ask about: one the site or the
    model named, or one found more than once. A single link from an article
    (Gymshark's run: nytimes.com, vogue.com.au, finanzen.net) is counted,
    not listed."""
    kinds = {v["kind"] for v in item["via"]}
    return bool(kinds & {"site", "model"}) or len(item["via"]) > 1


def merge_redirects(items, briefs, kept_by_final):
    """Two candidates whose homepages land on the same site are one company
    (centralaustindental.com redirects to koladentistry.com, both listed on
    the map, 2026-10-09). The first keeps the evidence of both; the other is
    marked as merged and not judged again."""
    for item in items:
        b = briefs.get(item["domain"]) or {}
        if b.get("status") != "ok":
            continue
        final = b.get("final_domain") or item["domain"]
        first = kept_by_final.get(final)
        if first is None:
            kept_by_final[final] = item
        elif first is not item:
            first["via"].extend(item["via"])
            for n in item["names"]:
                if n not in first["names"]:
                    first["names"].append(n)
            briefs[item["domain"]] = {"status": "merged", "into": first["domain"],
                                      "note": "same website as %s" % first["domain"]}


def map_only_verdict(item, plan):
    """A place the map lists in the client's category whose website refused
    this server is still a nearby competitor; it is kept, marked as known from
    the map only, rather than dropped for a reason that says nothing about it."""
    near = [v for v in item["via"] if v["kind"] == "places"]
    if not near:
        return None
    p = near[0]
    return {"domain": item["domain"], "site_type": "business",
            "name": (item["names"] or [item["domain"]])[0], "sells": p.get("category") or "",
            "same_offering": "yes", "same_customers": "unclear", "where": "same_city",
            "location": p.get("address") or "", "scale": "unclear", "locations": "unclear",
            "map_only": True,
            "reason": "Its website did not let us read it; the map lists it as %s, %.1f km "
                      "from the client." % ((p.get("category") or "the same category")
                                            .replace("_", " "), p["distance_km"])}


# == 3. the ranking =====================================================================

RANK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["competitors", "left_out", "gaps"],
    "properties": {
        "competitors": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["domain", "kind", "score", "reason"],
            "properties": {"domain": {"type": "string"},
                           "kind": {"type": "string",
                                    "enum": ["direct", "indirect", "local", "aspirational"]},
                           "score": {"type": "integer"},
                           "reason": {"type": "string"}}}},
        "left_out": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["domain", "why"],
            "properties": {"domain": {"type": "string"}, "why": {"type": "string"}}}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
}

RANK_SYSTEM = """You choose and rank a company's competitors for a market-intelligence report that will track what they do. You are given the company's profile and a list of candidates already checked against their own homepages, with how each was found.

Choose at most {limit} competitors, only from the candidate list, most important first. Labels:
- direct: same offering, same kind of customer, same market.
- indirect: a substitute or a partial overlap that customers really compare.
- local: a business of the same kind near the client (use only for candidates found on the map near the client, or verified to be in the same city).
- aspirational: a much larger player in the same market the client is measured against.
score: 0 to 100, how much the client should watch this company. A business near the client in the same category scores higher than a distant one; a national chain present in the client's city counts as local.
reason: one plain sentence a business owner would accept, saying why (what they sell, where, how they were found). No em dashes or en dashes, no internal jargon.
left_out: candidates you did not choose that a reader might expect, with a short why.
gaps: what this list probably misses (for example, competitors whose websites could not be read), or empty."""


def _rank_line(item, verdict, brief):
    near = [v for v in item["via"] if v["kind"] == "places"]
    return {"domain": item["domain"], "name": verdict.get("name") or (item["names"] or [""])[0],
            "sells": verdict.get("sells"), "same_offering": verdict.get("same_offering"),
            "same_customers": verdict.get("same_customers"), "where": verdict.get("where"),
            "location": verdict.get("location"), "scale": verdict.get("scale"),
            "distance_km": near[0]["distance_km"] if near else None,
            "branches_nearby": near[0].get("branches_nearby") if near else None,
            "found_by": sorted({v["kind"] for v in item["via"]}),
            "found_count": len(item["via"]), "check": verdict.get("reason"),
            "checked_on": "map listing only" if verdict.get("map_only") else (
                "an archived copy of its homepage (%s)" % brief["archived"] if brief.get("archived")
                else "its own homepage")}


def rank(profile, survivors, *, limit, run_id=None, client=None):
    user = ("CLIENT:\n" + json.dumps(client_brief(profile), ensure_ascii=False, indent=1)
            + "\n\nCANDIDATES (checked):\n" + json.dumps(survivors, ensure_ascii=False, indent=1))
    parsed, meta = llm.call_json(RANK_SYSTEM.replace("{limit}", str(limit)), user, RANK_SCHEMA,
                                 model=RANK_MODEL, max_tokens=6000, run_id=run_id,
                                 stage="rivals_rank", client=client)
    return parsed, meta


# == the whole step =====================================================================

def _via_label(v):
    k = v["kind"]
    if k == "places":
        return "on the map %.1f km away (%s)" % (v["distance_km"], v.get("category") or "same category")
    if k == "search":
        return "Google #%s for \"%s\"" % (v.get("position"), v.get("query"))
    if k == "article":
        return "listed in an article on %s" % v.get("article")
    if k == "model":
        return "known competitor (checked against its site)"
    return "named on the client's own site"


def discover(profile, *, run_id=None, client=None, token=None, progress=None, search=None,
             places=None, reader=read_home, fetch=None, archive_reader=read_archived,
             radius_km=None):
    """Returns {"status", "competitors", "unread", "rejected", "coverage", "plan", "gaps"}.
    Never raises for a failed source: each source reports its own status in
    coverage, and a failed source is never reported as "no competitors"."""
    started = time.monotonic()
    say = progress or (lambda stage: None)
    archetype = profile.get("archetype")
    own = domain_of((profile.get("facts") or {}).get("domain")
                    or (profile.get("facts") or {}).get("website") or "")
    pool = Pool(own)
    coverage = {}

    say("rivals_plan")
    try:
        plan, plan_meta = plan_search(profile, run_id=run_id, client=client)
    except llm.ModelError as e:
        return {"status": "failed", "error": {"stage": "plan", "kind": e.kind, "detail": e.detail},
                "competitors": [], "coverage": coverage}
    if radius_km:
        plan["radius_km"] = float(radius_km)       # the user's own radius wins
        plan["radius_set_by_user"] = True
    coverage["site"] = from_site(pool, profile)
    coverage["model"] = from_plan(pool, plan)

    say("rivals_search")
    local = archetype in ("local_single",)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_search = ex.submit(from_search, pool, plan, run_id=run_id, token=token, search=search)
        f_places = ex.submit(from_places, pool, profile, plan, places=places) if local else None
        search_meta, results = f_search.result()
        coverage["search"] = search_meta
        coverage["places"] = f_places.result() if f_places else {
            "status": "not_run", "note": "map search runs for single-site local businesses; "
                                         "chains get it per region in a later phase"}

    say("rivals_verify")
    first = pool.ordered()[:MAX_VERIFY]
    briefs = read_all(first, reader=reader)
    archived = archive_unread(first, briefs, archive_reader) if archive_reader else 0
    kept_by_final = {}
    merge_redirects(first, briefs, kept_by_final)
    verdicts, errors = verify(first, briefs, profile, run_id=run_id, client=client)

    articles = articles_to_read(results, verdicts, briefs)
    coverage["articles"] = from_articles(pool, articles, fetch=fetch) if articles else {
        "read": [], "added": 0}
    second = [i for i in pool.ordered() if i["domain"] not in briefs][:MAX_VERIFY_ROUND2]
    if second:
        briefs2 = read_all(second, reader=reader)
        briefs.update(briefs2)
        if archive_reader:
            archived += archive_unread(second, briefs, archive_reader)
        merge_redirects(second, briefs, kept_by_final)
        v2, e2 = verify(second, briefs, profile, run_id=run_id, client=client)
        verdicts.update(v2)
        errors += e2
    judged = first + second
    coverage["checked"] = {"candidates": len(pool.items), "read": sum(
        1 for i in judged if briefs[i["domain"]].get("status") == "ok"),
        "judged": len(verdicts), "not_fetched": max(0, len(pool.items) - len(judged)),
        "read_from_archive": archived,
        "check_errors": errors}

    # A candidate that redirects to the client's own site is the client.
    survivors, rejected, unread, merged = [], [], [], []
    unread_other = 0
    for item in judged:
        b = briefs[item["domain"]]
        if b.get("status") == "merged":
            merged.append({"domain": item["domain"], "into": b["into"]})
            continue
        if b.get("status") != "ok":
            fallback = map_only_verdict(item, plan)
            if fallback:
                verdicts[item["domain"]] = fallback
                survivors.append(item)
            elif _expected(item):
                unread.append({"domain": item["domain"], "why": b.get("note") or b.get("status"),
                               "found": [_via_label(v) for v in item["via"][:3]]})
            else:
                unread_other += 1
            continue
        if b.get("final_domain") == own:
            rejected.append({"domain": item["domain"], "why": "redirects to the client's own site"})
            continue
        if skip_domain(b.get("final_domain")):
            rejected.append({"domain": item["domain"],
                             "why": "redirects to %s, a social network, marketplace or directory"
                                    % b["final_domain"]})
            continue
        v = verdicts.get(item["domain"])
        if not v:
            unread.append({"domain": item["domain"], "why": "the check did not return a verdict",
                           "found": [_via_label(x) for x in item["via"][:3]]})
            continue
        if too_small_for(v, archetype):
            rejected.append({"domain": item["domain"], "site_type": v.get("site_type"),
                             "why": "a single site, not a rival to a chain at its level"})
        elif passes(v):
            survivors.append(item)
        else:
            rejected.append({"domain": item["domain"], "site_type": v.get("site_type"),
                             "why": v.get("reason")})

    limit = LIMITS.get(archetype, DEFAULT_LIMIT)
    lines = [_rank_line(i, verdicts[i["domain"]], briefs[i["domain"]]) for i in survivors]
    competitors, gaps, left_out = [], [], []
    if lines:
        say("rivals_rank")
        try:
            ranked, _ = rank(profile, lines, limit=limit, run_id=run_id, client=client)
        except llm.ModelError as e:
            return {"status": "failed", "error": {"stage": "rank", "kind": e.kind,
                                                   "detail": e.detail},
                    "competitors": [], "coverage": coverage, "plan": _plan_view(plan),
                    "survivors": lines}
        by_domain = {i["domain"]: i for i in survivors}
        line_by = {l["domain"]: l for l in lines}
        invented, weak = [], []
        for c in ranked.get("competitors") or []:
            d = (c.get("domain") or "").lower()
            item = by_domain.get(d)
            if not item or any(x["domain"] == d for x in competitors):
                if not item:
                    invented.append(d)
                continue
            score = max(0, min(100, int(c.get("score") or 0)))
            if score < MIN_SCORE:
                weak.append({"domain": d, "why": "weak match (score %d): %s" % (score, _clean(c.get("reason")))})
                continue
            kind = c.get("kind")
            near = [v for v in item["via"] if v["kind"] == "places"]
            if kind == "local" and not near and line_by[d].get("where") != "same_city":
                kind = "direct"     # "local" needs a map listing or a same-city address
            competitors.append({
                "domain": d, "name": line_by[d]["name"], "kind": kind,
                "score": score,
                "reason": _clean(c.get("reason")), "sells": line_by[d]["sells"],
                "location": line_by[d]["location"], "distance_km": line_by[d]["distance_km"],
                "branches_nearby": line_by[d]["branches_nearby"],
                "found": [_via_label(v) for v in item["via"][:4]],
                "found_by": line_by[d]["found_by"], "checked_on": line_by[d]["checked_on"]})
            if len(competitors) >= limit:
                break
        competitors.sort(key=lambda c: -c["score"])     # the model's order and scores can disagree
        gaps = [_clean(g) for g in ranked.get("gaps") or []]
        left_out = weak + [{"domain": x.get("domain"), "why": _clean(x.get("why"))}
                           for x in ranked.get("left_out") or []]
        if invented:
            coverage["rank_dropped"] = {"domains": invented,
                                        "why": "not among the checked candidates"}
    coverage["checked"]["unread_minor"] = unread_other
    coverage["seconds"] = round(time.monotonic() - started, 1)
    return {"status": "ok" if competitors else "none_found", "competitors": competitors, "left_out": left_out, "gaps": gaps,
            "unread": unread, "rejected": rejected[:60], "merged": merged, "coverage": coverage,
            "plan": _plan_view(plan)}


def _plan_view(plan):
    return {k: plan.get(k) for k in ("queries", "search_country", "search_language", "known",
                                     "place_categories", "place_terms", "radius_km",
                                     "radius_set_by_user", "notes")}


def save(result, *, client_id, owner_email, conn=None):
    """Store the list on the client: each competitor becomes (or refreshes) a
    shared company record and a proposed link. A competitor the user already
    confirmed or removed keeps that decision (market_radar_store). Earlier
    suggestions this full search no longer makes are dropped."""
    from . import market_radar_store as store
    saved = []
    for c in result.get("competitors") or []:
        entity = store.upsert_entity(c["domain"], name=c.get("name") or None, conn=conn)
        details = {k: c.get(k) for k in ("reason", "sells", "location", "distance_km",
                                         "branches_nearby", "checked_on", "found_by", "score")}
        details["run_id"] = result.get("run_id")
        store.propose_competitor(client_id, owner_email, entity, c["kind"],
                                 confidence=round(c["score"] / 100.0, 2),
                                 found_via=c.get("found"), details=details, conn=conn)
        saved.append(entity)
    # A full search replaces the agent's earlier suggestions; a search that
    # failed in part keeps them, since it may simply have missed them.
    search_ok = ((result.get("coverage") or {}).get("search") or {}).get("status") == "ok"
    if result.get("status") == "ok" and search_ok:
        result["retired_suggestions"] = store.retire_suggestions(client_id, owner_email, saved,
                                                                 conn=conn)
    return saved
