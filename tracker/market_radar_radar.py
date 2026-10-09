"""Market Radar, Phase 4: is a business like the client's opening nearby, or
a new brand entering its category?

Two scans, chosen by what the client is:

LOCAL (a business serving customers at a place). Overture publishes every
place it knows each month. A place in the client's categories, inside its
radius, that was not in the previous month's release is a CANDIDATE. It is
not yet news: measured around Austin City Dental (2026-10-09), 2,422 places
appeared between the August and September releases and most were existing
businesses newly added from a data source (three new dental clinics had
domains registered in 2003, 2007 and 2021). So a candidate is only
reported when something says it is actually new:

    * its name says so ("Coming Soon - Chipotle Mexican Grill"),
    * its website's domain was registered in the last 18 months (RDAP,
      the registries' own free record),
    * its own homepage says it is opening or has just opened,
    * a local news headline about an opening names it,
    * a chain's own store locator lists it (Overture's AllThePlaces source)
      AND one of the above.

Local news is read too, so an opening the map has not caught yet still
appears, as a headline.

NEW ENTRANTS (a business selling online). There is no "nearby", so the
question becomes "which new brands are entering the category?": launch
headlines for the client's product words in each market's news edition,
and shop domains registered in the last day (the free whoisds sample).
Each candidate's homepage is read and judged against the client by the
same Haiku check Phase 2 uses for competitors, and its domain age decides
whether it is a NEW brand or an established one launching something.

Costs: Overture, RDAP, Google News and the domain list are free. A few
Haiku calls and at most MAX_BRAND_SEARCHES paid searches (about $0.003
each) are booked against the run's ledger.
"""
from __future__ import annotations

import concurrent.futures
import io
import json
import logging
import re
import unicodedata
import zipfile
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import market_radar_news as news

logger = logging.getLogger(__name__)

NEW_DOMAIN_MONTHS = 18
MAX_CANDIDATES = 40            # nearest new listings checked for evidence
MAX_SITE_READS = 15
SAME_PLACE_KM = 0.3
NEWS_DAYS = 90
MAX_BRAND_SEARCHES = 5
MAX_ENTRANTS_JUDGED = 24
NRD_URL = "https://www.whoisds.com//whois-database/newly-registered-domains/%s/nrd"

NAME_SAYS = re.compile(r"\b(coming soon|opening soon|now open|grand opening|new location|"
                       r"opening (in )?(spring|summer|fall|autumn|winter|20\d\d)|"
                       r"neueröffnung|eröffnet bald|em breve|inaugura|próximamente|"
                       r"bientôt|prochainement)\b", re.I)
SITE_SAYS = re.compile(r"\b(now open|grand opening|coming soon|opening soon|we('| a)re open|"
                       r"newly opened|just opened|opening (in|this) (spring|summer|fall|autumn|"
                       r"winter|january|february|march|april|may|june|july|august|september|"
                       r"october|november|december)|now accepting new patients at our new|"
                       r"neueröffnung|jetzt geöffnet|inauguração|inauguramos|recién inaugurad|"
                       r"nouvelle ouverture|ouverture prochaine)\b", re.I)
OPENING_WORDS = {
    "en": '(opening OR "now open" OR "coming soon" OR "grand opening" OR opens OR "ribbon cutting")',
    "de": "(eröffnet OR Eröffnung OR Neueröffnung)",
    "pt": "(inaugura OR inauguração OR abre OR \"nova unidade\")",
    "es": "(inaugura OR abre OR apertura OR \"nueva sede\")",
    "fr": "(ouvre OR ouverture OR inauguration)",
    "it": "(apre OR apertura OR inaugura)",
    "nl": "(opent OR opening)",
    "ja": "(開院 OR オープン OR 開業)",
}
OPENING_HEADLINE = re.compile(r"open|coming soon|ribbon|eröffn|inaugur|abre|apertur|ouvr|"
                              r"ouverture|apre|開院|オープン|開業", re.I)
# "<noun> brand launch" and "new <noun> brand" find new brands; an exact
# phrase with OR-ed launch words found none for "sustainable sneakers", and
# "sneakers" with launch words found Nike collaborations (2026-10-09).
LAUNCH_QUERIES = ("%s brand launch", "new %s brand")
LAUNCH_HEADLINE = re.compile(r"launch|debut|new brand|startup|start-up|unveil|introduc", re.I)
LEGAL = re.compile(r"\b(llc|inc|ltd|pllc|dds|dmd|pa|pc|gmbh|ltda|limited)\b\.?", re.I)


def _now():
    return datetime.now(timezone.utc)


def fold(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", LEGAL.sub(" ", t)).split())


def domain_of(url):
    host = (urlsplit(url if "://" in (url or "") else "https://%s" % url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# == domain age (RDAP) =================================================================

def registered_on(domain, *, get, cache=None):
    """When a domain was registered, as YYYY-MM-DD, from RDAP (rdap.org sends
    the request to the registry that runs the domain). None when the
    registry does not say or did not answer. Cached: a registration date
    does not change."""
    key = "rdap:" + domain
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit.get("registered")
    read = get("https://rdap.org/domain/%s" % domain, accept="application/rdap+json", timeout=12)
    day = None
    if read["status"] == "ok":
        try:
            for e in json.loads(read["body"]).get("events") or []:
                if e.get("eventAction") == "registration" and e.get("eventDate"):
                    day = e["eventDate"][:10]
        except ValueError:
            pass
        if cache is not None:
            cache.put(key, {"registered": day})
    return day


def months_old(day, now):
    try:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (now - d).days / 30.4


class GeocodeCache:
    """RDAP answers kept in the mr_geocodes table, a key-value cache that
    already exists for exactly this: public lookups that never change."""

    def __init__(self, store):
        self.store = store

    def get(self, key):
        try:
            return self.store.cached_geocode(key)
        except Exception:
            return None

    def put(self, key, value):
        try:
            self.store.save_geocode(key, value)
        except Exception:
            pass


# == LOCAL: the map, month on month ===================================================

def _place_site(p):
    for w in p.get("websites") or []:
        d = domain_of(w)
        if d and not re.search(r"facebook|instagram|yelp|google|linktr|wix(site)?\.com|"
                               r"square\.site|business\.site|tiktok", d):
            return d, w
    return None, None


def _page(p):
    """A place's website as host and path, without scheme, www or a trailing slash."""
    _d, w = _place_site(p)
    if not w:
        return None
    u = urlsplit(w if "://" in w else "https://" + w)
    return domain_of(w) + (u.path or "/").rstrip("/").lower()


def new_listings(cur, prev, *, own_domain=None):
    """Places in `cur` with no counterpart in `prev`: not the same Overture
    id, not the same name within SAME_PLACE_KM, not the same website."""
    from .market_radar_places import distance_km
    ids = {p.get("id") for p in prev if p.get("id")}
    # The website's full address, not its domain: every Chipotle branch is
    # on locations.chipotle.com, so a domain match hid "Coming Soon -
    # Chipotle Mexican Grill" in Cedar Park behind an older branch.
    sites = {_page(p) for p in prev} - {None}
    by_name = {}
    for p in prev:
        by_name.setdefault(fold(p.get("name")), []).append(p)
    out = []
    for p in cur:
        if (p.get("status") or "open") == "permanently_closed" or p.get("id") in ids:
            continue
        d = _place_site(p)[0]
        if d == own_domain or (_page(p) and _page(p) in sites):
            continue
        twins = by_name.get(fold(p.get("name")), [])
        if any(distance_km(p["lat"], p["lon"], t["lat"], t["lon"]) <= SAME_PLACE_KM for t in twins):
            continue
        out.append(p)
    return out


def category_stems(categories, terms, words):
    """Word stems a headline about this kind of business would contain:
    "dent" from the search's terms, "dental" from dental_clinic, the
    industry's own words ("zahnarzt")."""
    stems = []
    for x in list(terms) + [p for c in categories for p in c.split("_")] + \
            [p for w in words for p in str(w).split()]:
        x = fold(x).replace(" ", "")
        if len(x) >= 4 and x not in stems and x not in {"clinic", "service", "store", "shop",
                                                        "office", "center", "centre", "general"}:
            stems.append(x)
    return stems


def local_news(entity_country, city, words, lang, *, get, breaker, stems=(), names=()):
    """Opening headlines naming the client's city and kind of business. A
    headline must itself name the kind of business: Google matches the
    words anywhere in an article, and "Austin dentist opening" also
    returned a highway, an R&D centre and a petrol station (2026-10-09)."""
    opening = OPENING_WORDS.get((lang or "en")[:2], OPENING_WORDS["en"])
    items, failures = [], []
    for w in words[:2]:
        q = '"%s" %s %s when:%dd' % (city, '"%s"' % w if " " in w else w, opening, NEWS_DAYS)
        if breaker.open:
            failures.append("Google News stopped answering")
            break
        breaker.wait_turn()
        read = get(news.feed_url(q, entity_country), limit=3_000_000, timeout=12)
        ok = read["status"] == "ok" and "<rss" in read["body"][:500]
        breaker.record(ok)
        if not ok:
            failures.append(read["note"] or read["status"])
            continue
        for it in news.parse_items(read["body"]):
            text = fold(it["title"])
            # Or it names a business of this kind on the map: "Luigi's Pizza
            # opens in Cedar Park" never says "restaurant".
            if OPENING_HEADLINE.search(it["title"]) and \
                    fold(city) in fold(it["title"] + " " + it.get("publisher", "")) and \
                    (not stems or any(st in text.replace(" ", "") for st in stems)
                     or any(_mentions(it["title"], n, city) for n in names)):
                items.append(it)
    seen, out = set(), []
    for it in sorted(items, key=lambda i: i.get("date") or "", reverse=True):
        k = fold(it["title"])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out[:25], failures


def _mentions(headline, name, city=""):
    """Does a headline name this business? Its distinctive words, all of
    them. The city's own words are not distinctive: a place called just
    "Cedar Park" would otherwise be named by every Cedar Park headline."""
    skip = {"the", "and", "dental", "dentist", "clinic", "care", "family", "center", "centre",
            "studio", "gym", "fitness", "restaurant", "cafe", "shop", "store"} | \
        set(fold(city).split())
    words = [w for w in fold(name).split() if len(w) > 2 and w not in skip]
    return bool(words) and all(re.search(r"\b%s\b" % re.escape(w), fold(headline)) for w in words)


def _site_evidence(url, fetch):
    page = fetch(url)
    if page.get("status") != "ok":
        return None
    m = SITE_SAYS.search(page.get("text") or "")
    if not m:
        return None
    text = page["text"]
    start = max(0, m.start() - 60)
    return " ".join(text[start:m.end() + 60].split())


def scan_local(*, point, categories, terms, radius_km, city, country, lang, own_domain,
               places, get, fetch, cache, breaker, now=None, industry_words=()):
    """The local scan. Returns {"status", "note", "findings", "news", "coverage"}."""
    now = now or _now()
    out = {"status": "ok", "findings": [], "news": [], "coverage": {}}
    try:
        cur_rel = places.latest_release()
        prev_rel = places.previous_release(cur_rel, places.releases())
    except Exception as e:
        return dict(out, status="failed", note="Overture's release list did not answer (%s)" %
                    type(e).__name__)
    if not prev_rel:
        return dict(out, status="failed", note="no earlier Overture release to compare with")
    try:
        cur = places.query(point["lat"], point["lon"], radius_km, categories=categories,
                           terms=terms, release=cur_rel)
        prev = places.query(point["lat"], point["lon"], radius_km, categories=categories,
                            terms=terms, release=prev_rel)
    except Exception as e:
        return dict(out, status="failed", note="the map could not be read (%s: %s)" % (
            type(e).__name__, str(e)[:160]))
    fresh = new_listings(cur, prev, own_domain=own_domain)
    # An earlier release that came back much smaller than this one would make
    # old places look new; the evidence checks still apply, but say so.
    short_read = len(prev) < 0.7 * len(cur) and len(cur) >= 20
    fresh.sort(key=lambda p: p["distance_km"])
    checked = fresh[:MAX_CANDIDATES]
    words = list(industry_words) or [c.replace("_", " ") for c in categories][:2]
    headlines, news_fail = local_news(country, city, words, lang, get=get, breaker=breaker,
                                      stems=category_stems(categories, terms, words),
                                      names=[p["name"] for p in cur if p.get("name")]) \
        if city else ([], [])
    out["news"] = headlines

    def evidence_for(p):
        ev, status, dated = [], "unknown", None
        if NAME_SAYS.search(p.get("name") or ""):
            ev.append("its listing is named \"%s\"" % p["name"])
            status = "planned" if re.search(r"coming|soon|bald|breve|próxim|bientôt|prochain",
                                            p["name"], re.I) else "opened"
        domain, site = _place_site(p)
        if domain:
            reg = registered_on(domain, get=get, cache=cache)
            age = months_old(reg, now)
            if age is not None and age <= NEW_DOMAIN_MONTHS:
                ev.append("its website %s was registered on %s" % (domain, reg))
                dated = dated or reg
                if status == "unknown":
                    status = "opened"
        for h in headlines:
            if _mentions(h["title"], p.get("name") or "", city or ""):
                ev.append("local news: \"%s\" (%s, %s)" % (h["title"], h.get("publisher"),
                                                           h.get("date")))
                dated = dated or h.get("date")
                break
        return ev, status, dated, domain, site

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        judged = list(pool.map(lambda p: (p, evidence_for(p)), checked))
    site_reads = 0
    for p, (ev, status, dated, domain, site) in judged:
        # The homepage is read only for the few that already look new or come
        # from a chain's own locator: a read is slow, and an old practice's
        # site rarely says anything about opening.
        if site and site_reads < MAX_SITE_READS and (ev or p.get("source") == "AllThePlaces"):
            site_reads += 1
            said = _site_evidence(site, fetch)
            if said:
                ev.append("its website says: \"%s\"" % said[:200])
                if status == "unknown":
                    status = "planned" if re.search(r"coming soon|opening soon|opening (in|this)",
                                                    said, re.I) else "opened"
        if p.get("source") == "AllThePlaces" and ev:
            ev.append("listed on the chain's own store locator")
        if not ev:
            continue
        # Only a young domain says "maybe": a practice that has been open for
        # years may simply have a new website (qdental.co.uk, London).
        strong = any(not e.startswith("its website ") or "says" in e for e in ev)
        out["findings"].append({
            "key": p.get("id") or domain or fold(p.get("name")), "name": p.get("name"),
            "category": p.get("taxonomy") or p.get("category"), "distance_km": p["distance_km"],
            "lat": p["lat"], "lon": p["lon"], "address": ", ".join(
                x for x in (p.get("street"), p.get("city")) if x),
            "website": site, "domain": domain, "status": status if strong else "unknown",
            "certain": strong, "evidence": ev,
            "date": dated or _date(p.get("source_updated")), "brand": p.get("brand"),
            "source": p.get("source")})
    out["coverage"] = {"release": cur_rel, "compared_with": prev_rel, "radius_km": radius_km,
                       "places_now": len(cur), "places_before": len(prev),
                       "new_listings": len(fresh), "checked": len(checked),
                       "confirmed_new": len(out["findings"]), "site_reads": site_reads,
                       "news_headlines": len(headlines), "news_failures": news_fail,
                       "earlier_release_short": short_read}
    unconfirmed = len(checked) - len(out["findings"])
    out["note"] = ("%d places like this within %.1f km; %d appeared on the map since %s; %d of "
                   "the nearest %d show a sign of being new%s; %d local opening headlines" % (
                       len(cur), radius_km, len(fresh), prev_rel[:10], len(out["findings"]),
                       len(checked), " (the other %d are most likely existing businesses newly "
                       "added to the map)" % unconfirmed if unconfirmed else "", len(headlines)))
    if short_read:
        out["note"] += ("; the earlier release returned far fewer places (%d against %d), so "
                        "many \"new\" listings may only be missing from it" % (len(prev), len(cur)))
    return out


def _date(value):
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", str(value or ""))
    return m.group(1) if m else None


# == NEW ENTRANTS: launches and new shop domains ======================================

def category_words(profile):
    """The client's product words, for launch headlines and domain matching."""
    words = []
    for w in (profile.get("industry") or {}).get("keywords") or []:
        w = " ".join(str(w).split()).lower()
        if 3 <= len(w) <= 40 and w not in words:
            words.append(w)
    for w in profile.get("offerings") or []:
        w = " ".join(str(w).split()).lower()
        if 3 <= len(w) <= 30 and w not in words:
            words.append(w)
    return words[:6]


GENERIC = {"online", "products", "product", "services", "service", "quality", "premium",
           "natural", "store", "shop", "brand", "goods", "items", "wear"}


def _singular(w):
    if w.endswith("ies") and len(w) > 5:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 4:
        return w[:-1]
    return w


def head_nouns(words):
    """The product in each phrase: its last word, singular ("sustainable
    sneakers" -> "sneaker", "wool shoes" -> "shoe"). The words before it
    describe; on their own they match anything ("running" found a physio
    and "sustainable" a solar company among new domains, 2026-10-09)."""
    out = []
    for w in words:
        parts = re.sub(r"[^a-z ]", " ", fold(w)).split()
        if parts:
            n = _singular(parts[-1])
            if len(n) >= 4 and n not in GENERIC and parts[-1] not in GENERIC and n not in out:
                out.append(n)
    return out


def domain_stems(words):
    """What a new shop's domain might contain: a product noun ("sneaker")
    or a whole phrase run together ("woolshoe")."""
    stems = []
    for n in head_nouns(words):
        if len(n) >= 5:
            stems.append(n)
    for w in words:
        parts = re.sub(r"[^a-z ]", " ", fold(w)).split()
        if len(parts) >= 2:
            joined = "".join(parts[:-1]) + _singular(parts[-1])
            if joined not in stems:
                stems.append(joined)
    return stems[:10]


def new_domains(get, now, *, days=1):
    """Domains registered in the last day, from whoisds' free daily sample
    (70,000 of each day's registrations, 2026-10-09). [] when it fails."""
    out, notes = [], []
    for back in range(1, days + 1):
        day = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        token = b64encode(("%s.zip" % day).encode()).decode()
        read = get(NRD_URL % token, accept="application/zip", limit=20_000_000, timeout=30,
                   raw=True)
        if read["status"] != "ok":
            notes.append("%s: %s" % (day, read["note"] or read["status"]))
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(read["raw"])) as z:
                name = z.namelist()[0]
                out.extend(l.strip().lower() for l in z.read(name).decode(
                    "utf-8", "replace").splitlines() if l.strip())
        except Exception as e:
            notes.append("%s: the file would not open (%s)" % (day, type(e).__name__))
    return out, notes


def launch_news(words, markets, *, get, breaker):
    items, failures = [], []
    queries = [form % n for n in head_nouns(words)[:2] for form in LAUNCH_QUERIES]
    for country in (markets or ["US"])[:2]:
        for qq in queries:
            if breaker.open:
                failures.append("Google News stopped answering")
                break
            breaker.wait_turn()
            q = "%s when:%dd" % (qq, NEWS_DAYS)
            read = get(news.feed_url(q, country), limit=3_000_000, timeout=12)
            ok = read["status"] == "ok" and "<rss" in read["body"][:500]
            breaker.record(ok)
            if not ok:
                failures.append(read["note"] or read["status"])
                continue
            items += [dict(it, market=country) for it in news.parse_items(read["body"])
                      if LAUNCH_HEADLINE.search(it["title"])]
    seen, out = set(), []
    # Headlines that call the BRAND new first: a week of a giant's launches
    # (eight Nike stories, 2026-10-09) must not push out "New running shoe
    # brand January launches".
    ranked = sorted(items, key=lambda i: i.get("date") or "", reverse=True)
    ranked.sort(key=lambda i: 0 if NEW_BRAND.search(i["title"]) else 1)
    for it in ranked:
        k = fold(it["title"])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out[:50], failures


NEW_BRAND = re.compile(r"new \w+( \w+)? brand|new brand|start-?up|debuts?|founded|seed|"
                       r"raises|launches with|first (shoe|product|collection)", re.I)

BRANDS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["brands"],
    "properties": {"brands": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "headline", "is_new_brand"],
        "properties": {"name": {"type": "string"}, "headline": {"type": "integer"},
                       "is_new_brand": {"type": "boolean"}}}}}}
BRANDS_SYSTEM = """You read news headlines about launches in a product category and list the BRANDS each headline is about, using only the headline text.

- name: the brand or company name exactly as the headline writes it.
- headline: the number of the headline it comes from.
- is_new_brand: true when the headline presents the brand itself as new (a new brand, a startup, a debut), false when an established company is launching a product.

Leave out the client itself, retailers and marketplaces (Amazon, Target, Walmart), publishers, and anything that is not a brand. Return an empty list when no headline names a brand."""


def brands_from_headlines(headlines, profile, *, run_id=None, client=None, llm=None):
    if not headlines:
        return []
    if llm is None:
        from . import market_radar_llm as llm
    user = "CLIENT: %s (%s)\n\nHEADLINES:\n%s" % (
        profile.get("name"), profile.get("one_liner") or "",
        "\n".join("%d. %s" % (i, h["title"]) for i, h in enumerate(headlines)))
    data, _meta = llm.call_json(BRANDS_SYSTEM, user, BRANDS_SCHEMA, model="claude-haiku-5-5",
                                max_tokens=1500, run_id=run_id, stage="radar_brands",
                                client=client)
    out, seen = [], set()
    for b in data.get("brands") or []:
        name = " ".join((b.get("name") or "").split())
        i = b.get("headline")
        if not name or fold(name) in seen or fold(name) == fold(profile.get("name")) or \
                not isinstance(i, int) or not 0 <= i < len(headlines):
            continue
        seen.add(fold(name))
        out.append({"name": name, "headline": headlines[i], "is_new_brand": bool(b["is_new_brand"])})
    return out


def resolve_brands(brands, *, country, token, run_id=None, search=None):
    """Each brand's own website, from one search batch (at most
    MAX_BRAND_SEARCHES queries, about $0.003 each): the first result that
    is not a marketplace, social network or publisher."""
    if not brands or not token:
        return {}, ("no search token" if brands else None)
    if search is None:
        from .market_radar_search import search
    from .market_radar_rivals import skip_domain
    picked = brands[:MAX_BRAND_SEARCHES]
    queries = ['"%s" official site' % b["name"] for b in picked]
    res = search(queries, token=token, country=(country or "us").lower(), run_id=run_id,
                 stage="radar_search")
    found = {}
    for b, q in zip(picked, queries):
        rows = sorted((r for r in res.get("results") or [] if r.get("query") == q),
                      key=lambda r: r.get("position") or 99)
        words = [w for w in fold(b["name"]).split() if len(w) > 2]
        for r in rows:
            d = r.get("domain") or ""
            # The brand's own site carries its name in the domain.
            if d and not skip_domain(d) and words and any(w in d.replace("-", "") for w in words):
                found[b["name"]] = d
                break
    return found, res.get("error")


def scan_entrants(profile, *, own_domain, competitors, get, fetch_home, now=None, token=None,
                  run_id=None, client=None, breaker, llm=None, search=None, verify=None):
    """The new-entrant scan for a business selling online. Returns
    {"status", "note", "findings", "news", "coverage"}."""
    from . import market_radar_rivals as rivals
    now = now or _now()
    verify = verify or rivals.verify
    words = category_words(profile)
    if not words:
        return {"status": "skipped", "note": "the profile names no products to look for",
                "findings": [], "news": [], "coverage": {}}
    markets = [m for m in (profile.get("markets") or []) if isinstance(m, str)] or \
        [((profile.get("hq") or {}).get("country_code") or "US")]
    headlines, news_fail = launch_news(words, markets, get=get, breaker=breaker)
    notes, cov = [], {"words": words, "headlines": len(headlines), "news_failures": news_fail}
    try:
        brands = brands_from_headlines(headlines, profile, run_id=run_id, client=client, llm=llm)
    except Exception as e:
        brands = []
        notes.append("reading the headlines failed (%s)" % type(e).__name__)
    known = set(competitors) | {own_domain}
    sites, err = resolve_brands([b for b in brands if b["is_new_brand"]] +
                                [b for b in brands if not b["is_new_brand"]],
                                country=markets[0], token=token, run_id=run_id, search=search)
    if err:
        notes.append("finding brand websites: %s" % err)
    cands = {}
    for b in brands:
        d = sites.get(b["name"])
        if d and d not in known:
            cands[d] = {"domain": d, "name": b["name"], "headline": b["headline"],
                        "news_says_new": b["is_new_brand"],
                        "via": [{"kind": "radar", "note": "named in a launch headline: %s"
                                 % b["headline"]["title"]}]}
    stems = domain_stems(words)
    fresh, nrd_notes = new_domains(get, now) if stems else ([], [])
    notes += nrd_notes
    matched = [d for d in fresh if any(s in d for s in stems)][:MAX_ENTRANTS_JUDGED]
    for d in matched:
        if d not in known and d not in cands:
            cands[d] = {"domain": d, "name": None, "headline": None, "news_says_new": False,
                        "registered": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
                        "via": [{"kind": "radar", "note": "domain registered yesterday"}]}
    cov.update(brands_named=len(brands), brand_sites=len(sites), new_domains_read=len(fresh),
               new_domains_matching=len(matched), candidates=len(cands))
    items = list(cands.values())[:MAX_ENTRANTS_JUDGED]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        briefs = dict(zip([i["domain"] for i in items], pool.map(fetch_home, items)))
    readable = [i for i in items if (briefs.get(i["domain"]) or {}).get("status") == "ok"]
    cov["readable"] = len(readable)
    verdicts, errors = verify(readable, briefs, profile, run_id=run_id, client=client) \
        if readable else ({}, [])
    if errors:
        notes.append("%d check batches failed" % len(errors))
    findings = []
    for i in readable:
        v = verdicts.get(i["domain"])
        if not v or not rivals.passes(v):
            continue
        reg = i.get("registered") or registered_on(i["domain"], get=get)
        age = months_old(reg, now)
        young = age is not None and age <= 24
        if not (young or i["news_says_new"]):
            continue        # an established brand: a competitor, not a new entrant
        ev = []
        if i.get("headline"):
            h = i["headline"]
            ev.append("launch news: \"%s\" (%s, %s)" % (h["title"], h.get("publisher"), h.get("date")))
        if young:
            ev.append("its domain was registered on %s" % reg)
        ev.append("it sells %s" % (v.get("sells") or "something similar"))
        findings.append({"key": i["domain"], "name": v.get("name") or i.get("name") or i["domain"],
                         "domain": i["domain"], "website": "https://%s/" % i["domain"],
                         "status": "opened", "evidence": ev, "reason": v.get("reason"),
                         "date": (i.get("headline") or {}).get("date") or reg,
                         "location": v.get("location")})
    cov["new_entrants"] = len(findings)
    note = "%d launch headlines for %s; %d brands named, %d new shop domains matched; %d new " \
           "entrants confirmed" % (len(headlines), ", ".join(words[:3]), len(brands), len(matched),
                                   len(findings))
    if notes:
        note += "; " + "; ".join(notes)
    return {"status": "ok", "note": note, "findings": findings, "news": headlines,
            "coverage": cov}


# == one client ========================================================================

REUSE_HOURS = 24
LOCAL_ARCHETYPES = {"local_single", "multi_location"}
ENTRANT_ARCHETYPES = {"ecommerce"}


def _search_settings(store, client_id, owner_email):
    """The map categories, terms and radius the latest competitor search
    used: the radar looks for the same kind of business."""
    run = store.latest_run(client_id, owner_email)
    places = (((run or {}).get("summary") or {}).get("result") or {}).get("coverage", {}) \
        .get("places") or {}
    if not (places.get("categories") or places.get("terms")):
        return None
    return {"categories": places.get("categories") or [], "terms": places.get("terms") or [],
            "radius_km": float(places.get("radius_km") or 5)}


def run_for_client(client_id, owner_email, *, run_id=None, store=None, io=None, now=None,
                   places=None, llm=None, search=None, verify=None, client=None, breaker=None):
    """Run the scans that fit this client, store what they found, and return
    a summary for the run: {"local": ..., "entrants": ...}, each with its
    findings, headlines and a note, or the reason it did not run."""
    if store is None:
        from . import market_radar_store as store
    from . import market_radar_views as views
    io = io or {}
    if "get" not in io or "fetch" not in io or "fetch_home" not in io:
        from . import market_radar_http as http
        from . import market_radar_rivals as rivals
        from . import market_radar_site as site
        io = dict({"get": http.get, "fetch": site.fetch, "fetch_home": rivals.read_home}, **io)
    if places is None:
        from . import market_radar_places as places
    now = now or _now()
    breaker = breaker or news.Breaker()
    c = store.get_client(client_id, owner_email)
    if not c:
        raise PermissionError("client %s is not yours or does not exist" % client_id)
    profile = views.effective_profile(c["profile"] or {}, c["settings"])
    archetype = profile.get("archetype") or c.get("archetype")
    out = {}
    cache = GeocodeCache(store)

    def stored(detector, run):
        snap = store.latest_snapshot(c["entity_id"], detector)
        seen = (snap or {}).get("last_seen_at")
        if seen and now - seen < timedelta(hours=REUSE_HOURS) and "scan" in (snap["payload"] or {}):
            return dict(snap["payload"]["scan"], reused=True)
        result = run()
        if result.get("status") == "ok":
            payload = {"findings": sorted(result["findings"], key=lambda f: str(f["key"])),
                       "news": [{k: h.get(k) for k in ("title", "publisher", "date", "link")}
                                for h in result["news"]], "scan": result}
            store.save_snapshot(c["entity_id"], detector, payload,
                                item_count=len(result["findings"]), run_id=run_id)
            result["new_events"] = _record(store, c["entity_id"], detector, result, now)
        return result

    if archetype in LOCAL_ARCHETYPES:
        point = profile.get("hq_point") or {}
        settings = _search_settings(store, client_id, owner_email)
        if point.get("lat") is None:
            out["local"] = {"status": "skipped", "note": "the business's location is not known"}
        elif not settings:
            out["local"] = {"status": "skipped", "note": "run a competitor search first: it "
                            "decides which kinds of business count as like this one"}
        else:
            hq = profile.get("hq") or {}
            radius = float(c.get("radius_km") or settings["radius_km"])
            competitors = {r["domain"] for r in store.competitors(client_id, owner_email)}
            out["local"] = stored("radar_local", lambda: scan_local(
                point=point, categories=settings["categories"], terms=settings["terms"],
                radius_km=radius, city=hq.get("city"), country=hq.get("country_code") or "US",
                lang=profile.get("language") or "en", own_domain=c["domain"], places=places,
                get=io["get"], fetch=io["fetch"], cache=cache, breaker=breaker, now=now,
                industry_words=[w for w in ((profile.get("industry") or {}).get("keywords")
                                            or [])][:2]))
            for f in out["local"].get("findings") or []:
                f["is_competitor"] = f.get("domain") in competitors
    if archetype in ENTRANT_ARCHETYPES:
        import os
        competitors = [r["domain"] for r in store.competitors(client_id, owner_email,
                                                              include_removed=True)]
        out["entrants"] = stored("radar_entrants", lambda: scan_entrants(
            profile, own_domain=c["domain"], competitors=competitors, get=io["get"],
            fetch_home=io["fetch_home"], now=now, token=os.environ.get("APIFY_API_TOKEN", ""),
            run_id=run_id, client=client, breaker=breaker, llm=llm, search=search,
            verify=verify))
    if not out:
        out["skipped"] = "the radar covers local businesses and online shops; this is %s" % (
            archetype or "an unknown type")
    return out


def _record(store, entity_id, detector, result, now):
    created = 0
    for f in result["findings"]:
        if detector == "radar_local":
            kind = "nearby_opening"
            title = "%s: %s (%s, %.1f km away)" % (
                "Possibly new nearby" if not f.get("certain", True) else
                {"planned": "Coming soon nearby", "opened": "New nearby"}.get(f["status"],
                                                                              "New nearby"),
                f["name"], (f.get("category") or "").replace("_", " "), f["distance_km"])
            location = {k: f.get(k) for k in ("lat", "lon", "distance_km", "address")}
            location["label"] = f["name"]
        else:
            kind, title, location = "new_entrant", "New brand in your category: %s" % f["name"], \
                ({"label": f["location"]} if f.get("location") else None)
        status = {"planned": "planned", "opened": "opened"}.get(f["status"], "unknown")
        url = f.get("website") or next((e for e in f["evidence"] if e.startswith("http")), None) \
            or "https://overturemaps.org/"
        _id, new = store.record_event(
            entity_id, "%s:%s" % (detector, f["key"]), type=kind, title=title,
            source={"url": url, "detector": detector, "seen_at": now.isoformat(timespec="seconds")},
            status=status, event_date=f.get("date"), summary="; ".join(f["evidence"])[:1500],
            location=location)
        created += 1 if new else 0
    return created
