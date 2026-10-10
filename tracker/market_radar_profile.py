"""Market Radar, Phase 1: from a company URL to a typed company profile.

    profile = build_profile("https://aspendental.com", run_id=run)

1. tracker/market_radar_site.read_site fetches the homepage and up to nine of
   the site's own pages and pulls out everything stated in machine-readable
   form (structured data, platform, jobs board, sitemap make-up, country
   signals). No model is involved.
2. One model call (Sonnet 5.5, no tools, structured output) reads those facts
   and the page text and returns a profile in a fixed schema, quoting the
   page for every key claim. It cannot search: a model reading pages already
   in hand must not complete the picture from elsewhere.
3. The profile is checked against the facts. A disagreement (the model says
   "online shop" and there is no shop platform and no product page) is kept
   and shown, never silently resolved either way.
4. It is stored on the company's shared record (mr_entities.profile).

Every model call is booked in the cost ledger when a run is given.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

from . import market_radar_llm as llm
from . import market_radar_site as site_reader

logger = logging.getLogger(__name__)

MODEL = os.environ.get("MR_PROFILE_MODEL", "claude-sonnet-5-5")
MAX_OUTPUT_TOKENS = 8000
HOME_CHARS = 14_000
PAGE_CHARS = 7_000
CORPUS_CHARS = 60_000
ARCHETYPES = ("local_single", "multi_location", "ecommerce", "b2b_services", "b2b_product",
              "manufacturer", "other")
CONFIDENCE = {"type": "string", "enum": ["high", "medium", "low"]}

PROFILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "one_liner", "offerings", "customer_type", "archetype",
                 "archetype_reason", "business_model", "sells_online", "location_count",
                 "location_count_basis", "hq", "markets", "service_area", "price_positioning",
                 "industry", "competitors_named", "languages", "evidence", "confidence",
                 "unknowns"],
    "properties": {
        "name": {"type": "string"},
        "one_liner": {"type": "string"},
        "offerings": {"type": "array", "items": {"type": "string"}},
        "customer_type": {"type": "string", "enum": ["B2C", "B2B", "both"]},
        "archetype": {"type": "string", "enum": list(ARCHETYPES)},
        "archetype_reason": {"type": "string"},
        "business_model": {"type": "string"},
        "sells_online": {"type": "boolean"},
        "location_count": {"type": "integer"},
        "location_count_basis": {"type": "string"},
        "hq": {"type": "object", "additionalProperties": False,
               "required": ["street", "postal_code", "city", "region", "country_code"],
               "properties": {"street": {"type": "string"}, "postal_code": {"type": "string"},
                              "city": {"type": "string"}, "region": {"type": "string"},
                              "country_code": {"type": "string"}}},
        "markets": {"type": "array", "items": {"type": "string"}},
        "service_area": {"type": "string"},
        "price_positioning": {"type": "string",
                              "enum": ["budget", "mid", "premium", "luxury", "unknown"]},
        "industry": {"type": "object", "additionalProperties": False,
                     "required": ["plain_label", "naics_code", "naics_title", "keywords"],
                     "properties": {"plain_label": {"type": "string"},
                                    "naics_code": {"type": "string"},
                                    "naics_title": {"type": "string"},
                                    "keywords": {"type": "array", "items": {"type": "string"}}}},
        "competitors_named": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "url"],
            "properties": {"name": {"type": "string"}, "url": {"type": "string"}}}},
        "languages": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["field", "quote", "url"],
            "properties": {"field": {"type": "string"}, "quote": {"type": "string"},
                           "url": {"type": "string"}}}},
        "confidence": {"type": "object", "additionalProperties": False,
                       "required": ["archetype", "industry", "hq", "location_count"],
                       "properties": {"archetype": CONFIDENCE, "industry": CONFIDENCE,
                                      "hq": CONFIDENCE, "location_count": CONFIDENCE}},
        "unknowns": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = """You profile a company from its own website, for a market-intelligence report that tracks its competitors and its industry.

You are given (1) FACTS a program read from the site's code and sitemap, and (2) the TEXT of the pages it read, each headed by its URL. Use only these. You have no search tool, and you must not fill gaps from general knowledge about a company with the same name: two firms often share a name, and this report is about the one at this website.

Archetypes, pick one:
- local_single: one physical location (or a few in one town) serving people nearby, e.g. a dental practice, a restaurant, a gym.
- multi_location: a chain or franchise with many physical locations in several towns.
- ecommerce: sells its own products online to consumers as its main channel (it may also have stores or stockists).
- b2b_services: sells services to businesses (agency, consultancy, IT services).
- b2b_product: sells software or products to businesses.
- manufacturer: makes physical goods, mostly sold through others.
- other: none of these fits; say why in archetype_reason.

Rules:
- location_count: the number of physical locations the pages or the facts support. Use the sitemap's location-page count only as a rough guide and say so in location_count_basis. 0 for an online-only business, -1 when nothing supports a number.
- hq.country_code: ISO 3166-1 alpha-2. Prefer a stated address (structured data, legal or contact page) over any other signal. A "shopify_store_info" fact is the registered address of the store's Shopify account, which is often a warehouse or an office other than the head office: use it only when the pages state no address, and say so in evidence or unknowns.
- Pages marked as read from the Wayback Machine are dated copies of the site, because the live site refused our server. Treat them as the site's content as of that date. If the facts note that the site chose a version for our location, do not treat that version's country as the company's.
- hq.street and hq.postal_code: the street address and postcode of the head office (for a local business, its premises), as the pages state them (contact page, Impressum, footer, structured data). Empty when not stated.
- markets: ISO alpha-2 codes of the countries the company actually serves or ships to, per the pages.
- industry.naics_code: the best-matching 6-digit NAICS 2022 code, and its title.
- competitors_named: only companies the site itself presents as competitors or alternatives (comparison pages, "vs" pages, "switch from"). Not partners, clients, insurers, suppliers or press outlets. Usually empty.
- evidence: for name, archetype, hq, location_count and industry, a short verbatim quote (under 25 words) from the page text, with that page's URL. Never invent or paraphrase a quote.
- unknowns: what a reader would expect to know that these pages did not say.
- Write in English, plainly. Do not use em dashes or en dashes. Use an empty string or empty list when something is unknown; never guess to fill a field."""


# == building the model's input ==================================================

def _strip_shared_lines(texts):
    """Per URL, the page text without lines repeated on most pages (menus,
    footers). The homepage keeps everything: its menu is content."""
    urls = list(texts)
    if len(urls) < 3:
        return dict(texts)
    from collections import Counter
    counts = Counter()
    for u in urls:
        counts.update({ln.strip() for ln in texts[u].splitlines() if ln.strip()})
    cut = max(2, round(len(urls) * 0.6))
    shared = {ln for ln, n in counts.items() if n >= cut}
    out = {urls[0]: texts[urls[0]]}
    for u in urls[1:]:
        kept = [ln for ln in texts[u].splitlines() if ln.strip() and ln.strip() not in shared]
        out[u] = "\n".join(kept) if kept else texts[u]
    return out


def build_corpus(site):
    bodies = _strip_shared_lines(site.get("texts") or {})
    parts, total = [], 0
    for i, (url, body) in enumerate(bodies.items()):
        body = body[:HOME_CHARS if i == 0 else PAGE_CHARS]
        if not body.strip():
            continue
        block = "=== %s ===\n%s" % (url, body)
        if total + len(block) > CORPUS_CHARS:
            block = block[:max(0, CORPUS_CHARS - total)]
        parts.append(block)
        total += len(block)
        if total >= CORPUS_CHARS:
            break
    return "\n\n".join(parts)


def facts_for_model(site):
    s = site.get("signals") or {}
    sm = s.get("sitemap") or {}
    return {
        "website": site.get("home_url"),
        "how_the_homepage_was_chosen": site.get("home_notes") or [],
        "page_title": s.get("title"),
        "meta_description": s.get("description"),
        "site_name": s.get("site_name"),
        "html_language": s.get("html_lang"),
        "structured_data_businesses": s.get("organizations", [])[:6],
        "phone_links": s.get("phones", [])[:5],
        "social_profiles": s.get("socials", {}),
        "shop_or_cms_platforms": [p["name"] for p in s.get("platforms", [])],
        "jobs_boards": s.get("ats", []),
        "store_locator": s.get("locator_vendors", []),
        "currencies_seen": s.get("currencies", []),
        "country_signals": site.get("country"),
        "sitemap": {k: sm.get(k) for k in ("urls", "top_sections", "location_like", "product_like")}
        if sm else "no readable sitemap",
        "pages_read": [{k: p.get(k) for k in ("kind", "url", "status", "words", "note", "via")}
                       for p in site.get("pages", [])],
        "program_hints": site.get("hints", []),
        "shopify_store_info": site.get("shopify_store") or "not a Shopify store, or not readable",
        "read_from_archive": bool(site.get("via_archive")),
    }


# == the model call ==============================================================

ProfileError = llm.ModelError     # kinds: not_configured, refused, truncated, bad_json, api_error, budget


def _client():
    return llm.default_client()


def ask_model(facts, corpus, *, run_id=None, stage="profile", client=None):
    """One structured-output call (tracker/market_radar_llm). Returns
    (parsed_profile, meta). Raises ProfileError."""
    user = ("FACTS (read by a program from the site's code and sitemap):\n"
            + json.dumps(facts, ensure_ascii=False, indent=1)
            + "\n\nTEXT OF THE PAGES READ:\n" + corpus)
    return llm.call_json(SYSTEM, user, PROFILE_SCHEMA, model=MODEL, max_tokens=MAX_OUTPUT_TOKENS,
                         run_id=run_id, stage=stage, client=client or _client())


# == checking the model against the facts ======================================

_DASH = re.compile(r"\s*[\u2013\u2014]\s*")


def _clean(value):
    """The model's own prose, house style (no em or en dashes)."""
    return _DASH.sub(", ", value).strip() if isinstance(value, str) else value


def checks(site, p):
    """Where the model and the facts disagree. Kept and shown, never resolved."""
    out = []
    s = site.get("signals") or {}
    sm = s.get("sitemap") or {}
    shop = [x["name"] for x in s.get("platforms", []) if x["name"] in site_reader.SHOP_PLATFORMS]
    products = sm.get("product_like", 0) >= 20 or s.get("product_schema")
    if p["archetype"] == "ecommerce" and not shop and not products:
        out.append("Profiled as an online shop, but no shop platform or product pages were found.")
    if p["archetype"] == "local_single" and sm.get("location_like", 0) >= 20:
        out.append("Profiled as a single location, but the sitemap lists %d location-like pages."
                   % sm["location_like"])
    if p["archetype"] in ("local_single", "multi_location") and shop and products:
        out.append("Profiled as a physical business, but the site is a shop with product pages.")
    vote = site.get("country") or {}
    hq = (p.get("hq") or {}).get("country_code") or ""
    if vote.get("code") and hq and hq.upper() != vote["code"] and vote.get("confidence", 0) >= 0.8:
        out.append("Headquarters given as %s, but the site's own signals point to %s (%d%%)."
                   % (hq.upper(), vote["code"], round(vote["confidence"] * 100)))
    lc = p.get("location_count", -1)
    if p["archetype"] == "multi_location" and 0 <= lc < 5 and sm.get("location_like", 0) >= 20:
        out.append("Location count given as %d, but the sitemap lists %d location-like pages."
                   % (lc, sm["location_like"]))
    texts = {_u(u): t for u, t in (site.get("texts") or {}).items()}
    page_urls = {_u(pg["url"]) for pg in site.get("pages", [])} | set(texts)
    unsourced = [e for e in p.get("evidence", []) if _u(e.get("url")) not in page_urls]
    if unsourced:
        out.append("%d evidence quote(s) cite a page that was not read." % len(unsourced))
    # Structured data is on the page too, just not in its visible text: the
    # model sees it among the facts and quotes it (drcjagadeesh.com's address
    # is "688 9th A Main Road" in its structured data and "687 9th A Main Rd"
    # in its visible text, the site's own inconsistency).
    structured = _norm(json.dumps((site.get("signals") or {}).get("organizations", []),
                                  ensure_ascii=False).replace("\\n", " "))
    unfound = [e for e in p.get("evidence", []) if _u(e.get("url")) in texts
               and _norm(e.get("quote")) and not _quote_on_page(e["quote"], texts[_u(e["url"])])
               and _norm(e["quote"]) not in structured]
    if unfound:
        out.append("%d evidence quote(s) do not appear on the page they cite." % len(unfound))
    return out


def _u(url):
    return (url or "").strip().rstrip("/").lower()


def _quote_on_page(quote, text):
    """Is the quote on the page? Matched with the reader's link addresses
    removed (a quote can run across a link's label) and kept (the model may
    quote a link itself: gymshark.com's "uk.linkedin.com/company/gymshark")."""
    q = _norm(quote)
    return q in _norm(text) or q in _norm(text, keep_links=True)


def _norm(text, keep_links=False):
    """Text for quote matching: link targets the reader appended ("[https://...]")
    removed, punctuation and case ignored."""
    text = text or ""
    if not keep_links:
        text = re.sub(r"\[(?:https?:|mailto:|tel:)[^\]]*\]", " ", text)
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def assemble(site, parsed, meta):
    """The stored profile: the model's reading, the facts, and the checks."""
    p = dict(parsed)
    for key in ("one_liner", "archetype_reason", "business_model", "location_count_basis",
                "service_area"):
        p[key] = _clean(p.get(key))
    p["offerings"] = [_clean(x) for x in p.get("offerings", [])][:10]
    p["unknowns"] = [_clean(x) for x in p.get("unknowns", [])][:10]
    p["hq"] = {k: (v.upper() if k == "country_code" else v).strip()
               for k, v in (p.get("hq") or {}).items()}
    # The pages may never state where the company is (gymshark.com does
    # not). Then a strong vote of the site's own signals stands in, labelled
    # as such, rather than leaving the country blank.
    vote = site.get("country") or {}
    p["hq_country_source"] = "pages" if p["hq"].get("country_code") else None
    if not p["hq"].get("country_code") and vote.get("code") and vote.get("confidence", 0) >= 0.8:
        p["hq"]["country_code"] = vote["code"]
        p["hq_country_source"] = "site signals (%s, %d%%)" % (
            ", ".join(sorted({e["signal"] for e in vote.get("evidence", [])})),
            round(vote["confidence"] * 100))
    p["markets"] = sorted({m.strip().upper() for m in p.get("markets", []) if len(m.strip()) == 2})
    s = site.get("signals") or {}
    p["facts"] = {
        "website": site.get("home_url"),
        "domain": site.get("domain"),
        "country_signals": site.get("country"),
        "platforms": [x["name"] for x in s.get("platforms", [])],
        "jobs_boards": s.get("ats", []),
        "socials": s.get("socials", {}),
        "phones": s.get("phones", [])[:5],
        "emails": s.get("emails", [])[:5],
        "store_locator": s.get("locator_vendors", []),
        "sitemap": s.get("sitemap"),
        "structured_locations": [o for o in s.get("organizations", []) if o.get("address")][:20],
        "hints": site.get("hints", []),
        "shopify_store": site.get("shopify_store"),
        "read_from_archive": bool(site.get("via_archive")),
        "read_in_browser": bool(site.get("via_browser")),
        "read_from_apollo": bool(site.get("via_apollo")),
    }
    p["checks"] = checks(site, p)
    p["coverage"] = {
        "pages": [{k: pg.get(k) for k in ("kind", "url", "status", "note", "via")} for pg in site.get("pages", [])],
        "home_notes": site.get("home_notes", []),
        "needs_browser": site.get("needs_browser", False),
        "robots": site.get("robots"),
    }
    p["model"] = {k: meta.get(k) for k in ("model_requested", "model_served", "stop_reason",
                                           "elapsed_ms", "cost_usd", "fallbacks")
                  if meta.get(k) is not None}
    p["read_at"] = site.get("read_at")
    return p


# == geocoding ==================================================================

NOMINATIM = "https://nominatim.openstreetmap.org/search"
_last_nominatim = [0.0]


def geocode(query, *, conn=None):
    """(lat, lon) for a place name, via OpenStreetMap's Nominatim, cached
    forever in mr_geocodes and spaced at least a second apart, as its usage
    policy asks. None when nothing matched or the service failed."""
    from . import market_radar_store as store
    query = " ".join((query or "").split())
    if not query:
        return None
    try:
        cached = store.cached_geocode(query, conn=conn)
    except store.StoreUnavailable:
        cached = None
    if cached is not None:
        return cached.get("point")
    wait = 1.1 - (time.monotonic() - _last_nominatim[0])
    if wait > 0:
        time.sleep(wait)
    _last_nominatim[0] = time.monotonic()
    try:
        r = requests.get(NOMINATIM, params={"q": query, "format": "json", "limit": 1},
                         headers={"User-Agent": site_reader.UA}, timeout=15)
        r.raise_for_status()
        hits = r.json()
    except Exception as e:
        logger.warning("market_radar_profile: geocode failed for %r: %s", query, e)
        return None
    point = {"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]),
             "label": hits[0].get("display_name", "")[:200]} if hits else None
    try:
        store.save_geocode(query, {"point": point}, conn=conn)
    except store.StoreUnavailable:
        pass
    return point


def _join(*parts):
    return ", ".join(" ".join(str(x).split()) for x in parts if x and str(x).strip())


def locate_hq(profile, *, conn=None):
    """The headquarters' coordinates, most precise source first, each labelled
    with its precision:

      exact    the site's own geo tag (structured data), for one place;
      street   a geocode of the stated street address (structured data for a
               single-site business, else the profile's hq.street);
      postcode a geocode of postcode and city, when the street did not match;
      city     a geocode of the city: its centre, which for a local business
               can be the wrong neighbourhood. kottident.de is in Kreuzberg,
               and "Berlin" geocoded 4 km away in Charlottenburg (2026-10-09),
               so the map search looked for competitors in the wrong area."""
    facts = profile.get("facts") or {}
    located = [o for o in facts.get("structured_locations", []) if o.get("geo")]
    single = profile.get("archetype") == "local_single"
    # One place only: a chain's structured data lists branches, and its first
    # branch is not its headquarters.
    if located and (single or len({(o["geo"]["lat"], o["geo"]["lon"]) for o in located}) == 1):
        return dict(located[0]["geo"], source="structured data", precision="exact")
    hq = profile.get("hq") or {}
    tries = []
    addressed = [o["address"] for o in facts.get("structured_locations", [])
                 if (o.get("address") or {}).get("streetAddress")]
    if single and len({a["streetAddress"] for a in addressed}) == 1:
        a = addressed[0]
        tries.append((_join(a.get("streetAddress"), a.get("postalCode"),
                            a.get("addressLocality") or hq.get("city"),
                            a.get("country") or hq.get("country_code")),
                      "geocoded street address (structured data)", "street"))
    if hq.get("street"):
        tries.append((_join(hq.get("street"), hq.get("postal_code"), hq.get("city"),
                            hq.get("country_code")), "geocoded street address", "street"))
    if hq.get("postal_code") and hq.get("city"):
        tries.append((_join(hq.get("postal_code"), hq.get("city"), hq.get("country_code")),
                      "geocoded postcode", "postcode"))
    if hq.get("city"):
        tries.append((_join(hq.get("city"), hq.get("region"), hq.get("country_code")),
                      "geocoded", "city"))
    seen = set()
    for query, source, precision in tries:
        if query in seen:
            continue
        seen.add(query)
        point = geocode(query, conn=conn)
        if point:
            return dict(point, source=source, precision=precision)
    return None


# == the whole step =============================================================

APOLLO_LABEL = "Apollo company record (the website refused our reader)"


def apollo_reading(site, *, run_id=None, enrich=None):
    """When a company's own site refuses us (lincolnelectric.com, weg.net and
    personio.com answered 403 or 429 even to a browser from a home address,
    2026-10-10), Apollo's record of it stands in: what it does, its
    industry, keywords, head office and LinkedIn page. One Apollo credit,
    booked in the ledger. Returns a site reading built from it, or None."""
    key = os.environ.get("APOLLO_API_KEY", "")
    if enrich is None:
        if not key:
            return None
        from . import apollo_client

        def enrich(domain):
            return apollo_client._post("organizations/enrich", {"domain": domain}, key)
    domain = site.get("domain")
    if not domain:
        return None
    try:
        if run_id:
            from . import market_radar_ledger as ledger
            with ledger.track(run_id, "profile", "apollo", units=1) as call:
                data = enrich(domain)
                call.record(units=1)
        else:
            data = enrich(domain)
    except Exception:
        return None
    org = (data or {}).get("organization") or {}
    if not org.get("name") or not (org.get("short_description") or org.get("industry")):
        return None
    lines = ["Name: %s" % org["name"]]
    for label, k in (("What it does", "short_description"), ("Industry", "industry"),
                     ("Founded", "founded_year"), ("Employees (estimate)", "estimated_num_employees"),
                     ("Street", "street_address"), ("City", "city"), ("Region", "state"),
                     ("Postal code", "postal_code"), ("Country", "country"),
                     ("Phone", "phone"), ("LinkedIn", "linkedin_url")):
        if org.get(k):
            lines.append("%s: %s" % (label, org[k]))
    if org.get("keywords"):
        lines.append("Keywords: " + ", ".join(str(k) for k in org["keywords"][:30]))
    text = "\n".join(lines)
    socials = dict((site.get("signals") or {}).get("socials") or {})
    li = re.search(r"linkedin\.com/(?:company|school|showcase)/([^/?#\s]+)", org.get("linkedin_url") or "")
    if li:
        socials["linkedin"] = li.group(1)
    return dict(site, status="ok", via_apollo=True, texts={APOLLO_LABEL: text},
                signals=dict(site.get("signals") or {}, socials=socials, title=org["name"],
                             description=org.get("short_description")),
                home_notes=list(site.get("home_notes") or []) +
                ["the website refused this server, so the profile was read from Apollo's "
                 "company record instead"])


BROWSER_PAGES = 4


def build_profile(url, *, run_id=None, client=None, save=True, conn=None, apollo_enrich=None,
                  browser=None):
    """Read the site, ask the model, check, locate, store. Returns
    {"status": "ok"|"unreadable"|"failed", "profile", "entity_id", "error"}."""
    from . import market_radar_store as store
    if browser is None:
        from . import market_radar_browser
        browser = market_radar_browser.for_run(run_id)
    # The profile is worth its other pages: up to BROWSER_PAGES more in the
    # same paid browser run when the site refuses this server.
    site = (site_reader.read_site(url, browser=browser, browser_pages=BROWSER_PAGES)
            if browser else site_reader.read_site(url))
    if site["status"] != "ok" or not site.get("texts"):
        stand_in = apollo_reading(site, run_id=run_id, enrich=apollo_enrich)
        if stand_in is None:
            return {"status": "unreadable", "profile": None, "entity_id": None,
                    "error": "the homepage could not be read", "pages": site.get("pages")}
        site = stand_in
    try:
        parsed, meta = ask_model(facts_for_model(site), build_corpus(site), run_id=run_id,
                                 client=client)
    except ProfileError as e:
        return {"status": "failed", "profile": None, "entity_id": None,
                "error": {"kind": e.kind, "detail": e.detail}, "pages": site.get("pages")}
    profile = assemble(site, parsed, meta)
    profile["hq_point"] = locate_hq(profile, conn=conn)
    entity_id = None
    if save:
        try:
            entity_id = store.upsert_entity(site["domain"], name=profile["name"] or None,
                                            country=profile["hq"].get("country_code") or None,
                                            archetype=profile["archetype"], conn=conn)
            store.set_profile(entity_id, profile, conn=conn)
        except store.StoreUnavailable as e:
            profile.setdefault("coverage", {})["saved"] = "not saved: %s" % e
    return {"status": "ok", "profile": profile, "entity_id": entity_id, "error": None}
