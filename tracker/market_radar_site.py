"""Market Radar, Phase 1: read a company's own website. No model here.

Everything a website states in machine-readable form is taken as it is
written, before any model sees the site: schema.org JSON-LD (name, address,
phone, social profiles), the shop platform it runs on, the jobs board its
careers page links to, the store locator it embeds, its phone prefixes,
currency, language and country signals, and what its sitemap is made of
(1,042 location pages says "chain"; 3,000 product pages says "shop").
tracker/market_radar_profile.py then hands these facts and the page text to
a model, which only has to judge what the facts cannot settle.

Rules this module keeps:

* It fetches the pages; a model never browses (the lesson of
  event_intel_intake.py: 15 seconds instead of up to 450).
* Every page it tried is listed with its outcome, including the ones that
  failed, so a thin profile says WHY it is thin.
* robots.txt is respected for every page after the homepage.
* No Accept-Language header is sent: a site should answer with its own
  default language, not one the server guesses for us. A site that still
  redirects by the visitor's location (found live: orangetheory.com sent a
  visitor in India to /en-in) is flagged, so the report does not mistake our
  server's location for the company's home market.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests

from .event_intel_harvest import (_charset, _read_body, client_render_marker,
                                  html_to_linked_text)
from .event_intel_http import public_get

UA = "Mozilla/5.0 (compatible; Position2-MarketRadar/1.0; +https://intelligence.position2.com)"
TIMEOUT = 20
MAX_EXTRA_PAGES = 9
MIN_WORDS = 150               # fewer readable words on the homepage: built in the browser
MAX_SITEMAP_FILES = 6         # one index plus five of its sitemaps
MAX_SITEMAP_URLS = 50_000

# -- page kinds we follow, in many languages ------------------------------------
# (kind, max pages of that kind, words matched against link text and path)
PAGE_KINDS = [
    ("about", 1, ("about", "über uns", "ueber-uns", "uber-uns", "unternehmen", "wir sind",
                  "sobre", "quem somos", "quienes somos", "a-propos", "à propos", "chi siamo",
                  "our story", "who we are", "company", "会社概要", "企業情報")),
    ("legal", 1, ("impressum", "imprint", "legal notice", "mentions légales", "mentions-legales",
                  "aviso legal", "company details")),
    ("locations", 2, ("locations", "location", "find a", "find us", "store locator", "stores",
                      "our clinics", "clinics", "centres", "centers", "studios", "branches",
                      "offices", "standorte", "filialen", "unidades", "lojas", "nossas lojas",
                      "tiendas", "店舗")),
    ("offerings", 2, ("services", "treatments", "what we do", "solutions", "products", "shop",
                      "collections", "leistungen", "behandlungen", "produkte", "servicos",
                      "serviços", "tratamentos", "produtos", "servicios", "tratamientos")),
    ("pricing", 1, ("pricing", "prices", "price list", "fees", "membership", "plans", "preise",
                    "kosten", "honorar", "precos", "preços", "precios", "tarifs")),
    ("contact", 1, ("contact", "kontakt", "contato", "contacto", "fale conosco", "お問い合わせ")),
    ("careers", 1, ("careers", "jobs", "join us", "join our team", "we're hiring", "karriere",
                    "stellenangebote", "carreiras", "trabalhe conosco", "vagas", "empleo",
                    "trabaja con nosotros", "採用")),
    ("press", 1, ("press", "newsroom", "news", "media", "presse", "imprensa", "notícias",
                  "noticias")),
]
SKIP_PATH = re.compile(r"(\.(pdf|jpe?g|png|gif|svg|webp|zip|mp4|docx?|xlsx?)$)|"
                       r"(/(cart|checkout|account|login|signin|sign-in|register|wp-admin|cdn-cgi)(/|$))",
                       re.I)

# -- vendor fingerprints -----------------------------------------------------------
PLATFORMS = [   # (name, kind, pattern) ; kind: commerce | cms
    ("shopify", "commerce", r"cdn\.shopify\.com|Shopify\.theme|\.myshopify\.com"),
    ("woocommerce", "commerce", r"wp-content/plugins/woocommerce|woocommerce-"),
    ("bigcommerce", "commerce", r"cdn\d*\.bigcommerce\.com|data-bc-"),
    ("magento", "commerce", r"Magento_|mage/cookies|text/x-magento-init"),
    ("salesforce_commerce", "commerce", r"demandware\.(static|net)|dwvar_|/on/demandware"),
    ("vtex", "commerce", r"vtexassets\.com|vteximg\.com|\.vtex\.(com|app)"),
    ("nuvemshop", "commerce", r"nuvemshop|tiendanube|d26lpennugtm8s\.cloudfront\.net"),
    ("shopware", "commerce", r"shopware"),
    ("prestashop", "commerce", r"prestashop"),
    ("wix", "cms", r"static\.wixstatic\.com|wix-bolt|_wixCssImports"),
    ("squarespace", "cms", r"static1\.squarespace\.com|squarespace-cdn"),
    ("webflow", "cms", r"webflow\.com/|data-wf-site"),
    ("wordpress", "cms", r"/wp-content/|/wp-includes/"),
    ("hubspot_cms", "cms", r"hs-scripts\.com|hubspot\.net/hub"),
]
LOCATOR_VENDORS = [
    ("yext", r"yextapis\.com|sites\.yext|yext-static|\.yextpages\."),
    ("brandify", r"where2getit|brandify"),
    ("rio_seo", r"rioseo|riolocal"),
    ("soci", r"soci\.ai|meetsoci"),
    ("uberall", r"uberall\.com"),
    ("storepoint", r"storepoint\.co"),
    ("stockist", r"stockist\.co"),
    ("storerocket", r"storerocket\.io"),
    ("locally", r"locally\.com"),
]
# A Google map on a contact page is not a store locator: two single-practice
# dentists were reported as having one until this was split out.
MAP_EMBED = r"google\.com/maps/embed|maps\.googleapis\.com/maps/api/js"
# Platforms that ARE a shop. WooCommerce is a WordPress plugin that sites
# install and never sell through (two dental practices, 2026-10-09), so it
# only counts with product pages to back it.
SHOP_PLATFORMS = {"shopify", "bigcommerce", "magento", "salesforce_commerce", "vtex",
                  "nuvemshop", "shopware", "prestashop"}
ATS_VENDORS = [   # (vendor, pattern capturing the board slug where there is one)
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("lever", r"jobs\.(?:eu\.)?lever\.co/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
    ("workday", r"([\w-]+)\.wd\d+\.myworkdayjobs\.com"),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
    ("recruitee", r"([\w-]+)\.recruitee\.com"),
    ("bamboohr", r"([\w-]+)\.bamboohr\.com/careers"),
    ("teamtailor", r"([\w-]+)\.teamtailor\.com"),
    ("personio", r"([\w-]+)\.jobs\.personio\.(?:de|com)"),
    ("jobvite", r"jobs\.jobvite\.com/([\w-]+)"),
    ("icims", r"([\w-]+)\.icims\.com"),
    ("breezy", r"([\w-]+)\.breezy\.hr"),
    ("jazzhr", r"([\w-]+)\.applytojob\.com"),
    ("darwinbox", r"([\w-]+)\.darwinbox\.in"),
    ("keka", r"([\w-]+)\.keka\.com/careers"),
]
SOCIAL = [   # (network, pattern capturing the handle)
    ("linkedin", r"linkedin\.com/(?:company|school|showcase)/([^/?#\s\"']+)"),
    ("instagram", r"instagram\.com/(?!p/|reel/|explore/|accounts/)([A-Za-z0-9_.]+)"),
    ("facebook", r"facebook\.com/(?!sharer|share\.php|dialog/|plugins/|tr\?)(?:pg/)?([^/?#\s\"']+)"),
    ("tiktok", r"tiktok\.com/@([A-Za-z0-9_.]+)"),
    ("youtube", r"youtube\.com/((?:channel/|c/|user/|@)[^/?#\s\"']+)"),
    ("x", r"(?:twitter|x)\.com/(?!intent/|share|home)([A-Za-z0-9_]{1,15})(?:[/?#\"'\s]|$)"),
    ("pinterest", r"pinterest\.[a-z.]+/(?!pin/)([A-Za-z0-9_]+)"),
]

# -- country signals ----------------------------------------------------------------
CC_TLD = {
    "uk": "GB", "de": "DE", "in": "IN", "br": "BR", "jp": "JP", "fr": "FR", "es": "ES",
    "it": "IT", "nl": "NL", "au": "AU", "ca": "CA", "ie": "IE", "nz": "NZ", "sg": "SG",
    "ae": "AE", "mx": "MX", "ch": "CH", "at": "AT", "se": "SE", "dk": "DK", "no": "NO",
    "fi": "FI", "pl": "PL", "pt": "PT", "be": "BE", "za": "ZA", "ar": "AR", "cl": "CL",
    "co": None,   # .co is used as a generic TLD far more than for Colombia
}
PHONE_PREFIX = [  # longest first
    ("+971", "AE"), ("+358", "FI"), ("+353", "IE"), ("+351", "PT"), ("+91", "IN"),
    ("+81", "JP"), ("+65", "SG"), ("+64", "NZ"), ("+61", "AU"), ("+55", "BR"), ("+54", "AR"),
    ("+52", "MX"), ("+49", "DE"), ("+48", "PL"), ("+47", "NO"), ("+46", "SE"), ("+45", "DK"),
    ("+44", "GB"), ("+43", "AT"), ("+41", "CH"), ("+39", "IT"), ("+34", "ES"), ("+33", "FR"),
    ("+32", "BE"), ("+31", "NL"), ("+27", "ZA"), ("+1", "US"),
]
CURRENCY_COUNTRY = {"GBP": "GB", "INR": "IN", "BRL": "BR", "JPY": "JP", "AUD": "AU",
                    "CAD": "CA", "USD": "US", "CHF": "CH", "MXN": "MX", "AED": "AE",
                    "SGD": "SG", "NZD": "NZ", "ZAR": "ZA", "SEK": "SE", "DKK": "DK",
                    "NOK": "NO", "PLN": "PL"}
COUNTRY_NAMES = {"united states": "US", "usa": "US", "united states of america": "US",
                 "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB",
                 "germany": "DE", "deutschland": "DE", "india": "IN", "brazil": "BR",
                 "brasil": "BR", "japan": "JP", "france": "FR", "spain": "ES", "españa": "ES",
                 "italy": "IT", "netherlands": "NL", "australia": "AU", "canada": "CA",
                 "ireland": "IE", "mexico": "MX", "méxico": "MX", "switzerland": "CH",
                 "austria": "AT", "österreich": "AT", "singapore": "SG",
                 "united arab emirates": "AE", "uae": "AE"}
WEIGHTS = {"jsonld_address": 3.0, "cctld": 3.0, "phone": 2.0, "phone_text": 1.0,
           "shop_registration": 2.0,
           "currency": 1.0, "lang_region": 1.0, "lang_default_region": 0.25, "lang_only": 0.5,
           "impressum": 0.5}
# "en-US" is WordPress's default locale and is left on sites everywhere
# (a London and two Indian practices, 2026-10-09), so it barely counts.
DEFAULT_LOCALES = {"en-us", "en_us"}
# A bare language that points at one country far more than any other.
LANG_ONLY = {"de": "DE", "ja": "JP", "it": "IT", "nl": "NL", "pl": "PL", "sv": "SE",
             "da": "DK", "fi": "FI", "nb": "NO", "no": "NO", "ko": "KR", "tr": "TR"}
TEXT_PHONE = re.compile(r"(?<![\w+])(\+\d{1,3})[\s.\-()]*\d[\d\s.\-()]{6,}\d")
CURRENCY_SYMBOLS = [("R$", "BRL"), ("₹", "INR"), ("£", "GBP"), ("€", "EUR"), ("¥", "JPY"),
                    ("A$", "AUD"), ("C$", "CAD")]

ORG_TYPES = re.compile(r"(Organization|Corporation|Business|Store|Shop|Dentist|Clinic|"
                       r"Physician|Restaurant|Club|Gym|Hotel|Agency|Office|Center|Centre|"
                       r"Practice|Brand|Company|NGO|School|Hospital)$")
LOCATION_PATH = re.compile(r"/(locations?|stores?|store-locator|clinics?|centres?|centers?|"
                           r"branches|offices|studios?|standorte?|filialen|unidades|lojas|"
                           r"tiendas|find-a-[\w-]+|dentists?|gyms?|practices?|[\w-]*near-me)(/|$)",
                           re.I)
PRODUCT_PATH = re.compile(r"/(products?|p|produtos?|produkte?|productos?|item|shop/p)/[^/]+",
                          re.I)


# == HTML parsing ===================================================================

class _Collector(HTMLParser):
    """One pass over a page: what the signals need, nothing rendered."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.title = ""
        self.meta = {}
        self.hreflang = []
        self.feeds = []          # (href, title) of RSS and Atom feeds the page announces
        self.canonical = None
        self.scripts_src = []
        self.jsonld_raw = []
        self.links = []          # (href, text)
        self._in_title = False
        self._in_jsonld = False
        self._buf = []
        self._a = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang") and not self.lang:
            self.lang = a["lang"].strip()
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "alternate" in rel and a.get("hreflang"):
                self.hreflang.append((a["hreflang"], a.get("href", "")))
            elif "alternate" in rel and a.get("href") and re.search(
                    r"(rss|atom)\+xml", a.get("type", ""), re.I):
                self.feeds.append((a["href"], a.get("title", "")))
            elif rel == "canonical" and a.get("href"):
                self.canonical = a["href"]
        elif tag == "script":
            if a.get("src"):
                self.scripts_src.append(a["src"])
            if "ld+json" in a.get("type", "").lower():
                self._in_jsonld, self._buf = True, []
        elif tag == "a" and a.get("href"):
            self._a = [a["href"], []]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            self.jsonld_raw.append("".join(self._buf))
            self._in_jsonld = False
        elif tag == "a" and self._a is not None:
            self.links.append((self._a[0], " ".join("".join(self._a[1]).split())))
            self._a = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_jsonld:
            self._buf.append(data)
        if self._a is not None:
            self._a[1].append(data)


def parse_html(markup):
    c = _Collector()
    try:
        c.feed(markup)
        c.close()
    except Exception:
        pass   # a broken page still gives whatever was collected before the break
    return c


# == JSON-LD ========================================================================

def _jsonld_nodes(raw_blocks):
    """Every object in every JSON-LD block, @graph and nested lists flattened.
    A block that does not parse is skipped, not fatal."""
    nodes = []

    def walk(x, depth=0):
        if depth > 6:
            return
        if isinstance(x, list):
            for i in x:
                walk(i, depth + 1)
        elif isinstance(x, dict):
            nodes.append(x)
            for key in ("@graph", "mainEntity", "subOrganization", "department", "location",
                        "brand", "publisher", "provider", "seller", "parentOrganization"):
                if key in x:
                    walk(x[key], depth + 1)

    for raw in raw_blocks:
        text = re.sub(r"^\s*(<!--|//<!\[CDATA\[)|(-->|//\]\]>)\s*$", "", raw.strip())
        try:
            walk(json.loads(text))
        except ValueError:
            continue
    return nodes


def _types(node):
    t = node.get("@type")
    return [t] if isinstance(t, str) else [x for x in t if isinstance(x, str)] if isinstance(t, list) else []


def _country_code(value):
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id") or ""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if len(v) == 2 and v.isalpha():
        return "GB" if v.upper() == "UK" else v.upper()
    return COUNTRY_NAMES.get(v.lower())


def _address(addr):
    if isinstance(addr, list):
        addr = addr[0] if addr else None
    if isinstance(addr, str):
        return {"text": addr[:200]}
    if not isinstance(addr, dict):
        return None
    out = {k: addr.get(k) for k in ("streetAddress", "addressLocality", "addressRegion",
                                    "postalCode") if isinstance(addr.get(k), str)}
    cc = _country_code(addr.get("addressCountry"))
    if cc:
        out["country"] = cc
    return out or None


def organizations(nodes):
    """Business-like JSON-LD nodes, reduced to the fields a profile uses."""
    out = []
    for n in nodes:
        types = _types(n)
        if not any(ORG_TYPES.search(t) for t in types):
            continue
        same_as = n.get("sameAs")
        org = {
            "types": types[:4],
            "name": n.get("name") if isinstance(n.get("name"), str) else None,
            "url": n.get("url") if isinstance(n.get("url"), str) else None,
            "telephone": n.get("telephone") if isinstance(n.get("telephone"), str) else None,
            "address": _address(n.get("address")),
            "price_range": n.get("priceRange") if isinstance(n.get("priceRange"), str) else None,
            "same_as": [s for s in (same_as if isinstance(same_as, list) else [same_as])
                        if isinstance(s, str)][:12],
        }
        geo = n.get("geo")
        if isinstance(geo, dict) and geo.get("latitude") is not None:
            try:
                org["geo"] = {"lat": float(geo["latitude"]), "lon": float(geo["longitude"])}
            except (TypeError, ValueError, KeyError):
                pass
        if org["name"] or org["address"] or org["telephone"]:
            out.append(org)
    return out


def _number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def ratings(nodes, page_url):
    """Every public review score stated in structured data on one page, as
    {"url", "item", "rating", "count"}. A count is the only part tracked
    over time: review volume growing is the proxy for sales growing."""
    out = []
    for n in nodes:
        agg = n.get("aggregateRating")
        if isinstance(agg, list):
            agg = agg[0] if agg else None
        if not isinstance(agg, dict):
            continue
        count = _number(agg.get("reviewCount") or agg.get("ratingCount"))
        if count is None:
            continue
        name = n.get("name") if isinstance(n.get("name"), str) else ""
        out.append({"url": page_url, "item": (name or ",".join(_types(n)))[:120],
                    "rating": _number(agg.get("ratingValue")), "count": int(count)})
    return out


def ratings_from_html(markup, page_url):
    return ratings(_jsonld_nodes(parse_html(markup).jsonld_raw), page_url)


# == signals from a page ===============================================================

def _host(url):
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _same_site(url, home_host):
    h = _host(url)
    return h == home_host or h.endswith("." + home_host)


CAREERS_LINK = re.compile(r"career|jobs?\b|/jobs|karriere|stellen|carreiras|vagas|trabalhe|"
                          r"empleo|trabaja|join-?us|join-our-team|work-with-us|recrut|採用", re.I)
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"}


def site_root(host):
    """The registrable part of a host, near enough: aspendental.com for
    careers.aspendental.com, mydentist.co.uk for www.mydentist.co.uk."""
    labels = (host or "").lower().split(".")
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def careers_links(links, home_url):
    """Links to the company's own careers pages, including a careers host of
    its own (careers.aspendental.com, which runs on Phenom), best first."""
    root = site_root(_host(home_url))
    out = []
    for href, label in links:
        url = urljoin(home_url, href).split("#", 1)[0]
        h = _host(url)
        if not url.startswith("http") or not (h == root or h.endswith("." + root)):
            continue
        if CAREERS_LINK.search(urlsplit(url).path or "") or CAREERS_LINK.search(h.split(".")[0]) \
                or CAREERS_LINK.search(label or ""):
            if url not in out:
                out.append(url)
    # A separate careers host first: it is the jobs site, not a page about it.
    out.sort(key=lambda u: 0 if _host(u) not in (root, "www." + root) else 1)
    return out[:4]


def detect(pattern_list, haystack):
    found = []
    for item in pattern_list:
        name, pattern = item[0], item[-1]
        if re.search(pattern, haystack, re.I):
            found.append(name if len(item) == 2 else (name, item[1]))
    return found


def ats_boards(haystack):
    boards = []
    for vendor, pattern in ATS_VENDORS:
        for m in re.finditer(pattern, haystack, re.I):
            slug = m.group(1) if m.groups() else None
            if any(b["vendor"] == vendor and b["board"] == slug for b in boards):
                continue
            # The matched address too: a Workday board is reached through its
            # site path (tenant.wd5.myworkdayjobs.com/<site>), not the tenant.
            boards.append({"vendor": vendor, "board": slug,
                           "url": haystack[m.start():m.start() + 200].split()[0]})
    return boards[:6]


def social_profiles(hrefs):
    out = {}
    for href in hrefs:
        for network, pattern in SOCIAL:
            if network in out:
                continue
            m = re.search(pattern, href, re.I)
            if m and m.group(1).lower() not in ("share", "sharer", "home", "intent", "watch"):
                out[network] = m.group(1).rstrip(".")
    return out


def phone_countries(phones):
    found = []
    for p in phones:
        digits = re.sub(r"[^\d+]", "", p)
        if not digits.startswith("+"):
            if digits.startswith("00"):
                digits = "+" + digits[2:]
            else:
                continue
        for prefix, cc in PHONE_PREFIX:
            if digits.startswith(prefix):
                found.append(cc)
                break
    return found


def currencies(markup, text):
    """Currencies seen in prices and structured data, most frequent first."""
    counts = Counter()
    for m in re.finditer(r'"(?:priceCurrency|currency|currencyCode)"\s*:\s*"([A-Z]{3})"', markup):
        counts[m.group(1)] += 3
    for m in re.finditer(r'(?:product:price:currency|og:price:currency)"\s+content="([A-Z]{3})"', markup):
        counts[m.group(1)] += 3
    for m in re.finditer(r"\b(USD|GBP|EUR|INR|BRL|JPY|AUD|CAD|CHF|MXN|AED|SGD)\s?\d", text):
        counts[m.group(1)] += 1
    for symbol, code in CURRENCY_SYMBOLS:
        counts[code] += len(re.findall(re.escape(symbol) + r"\s?\d", text))
    return [c for c, n in counts.most_common() if n > 0][:4]


def country_vote(domain, orgs, phones, currency_list, lang, og_locale, *, text_phones=(),
                 has_impressum=False, geo_redirected=False, shop_country=None):
    """Where the company is, from independent signals, with the evidence.
    Confidence is the winner's share of all the weight cast. Language and
    locale are ignored when the site redirected us by location: then they
    describe our server, not the company."""
    votes, evidence = Counter(), []

    def cast(cc, kind, detail):
        if cc:
            votes[cc] += WEIGHTS[kind]
            evidence.append({"signal": kind, "country": cc, "detail": detail})

    labels = domain.split(".")
    tld = labels[-1]
    if len(labels) >= 2 and labels[-2] in ("co", "com", "org", "net") and tld in CC_TLD:
        cast(CC_TLD[tld], "cctld", "." + ".".join(labels[-2:]))
    elif tld in CC_TLD:
        cast(CC_TLD[tld], "cctld", "." + tld)
    seen_addr = set()
    for org in orgs:
        cc = (org.get("address") or {}).get("country")
        if cc and cc not in seen_addr:
            seen_addr.add(cc)
            cast(cc, "jsonld_address", org.get("name") or "structured address")
    for cc, n in Counter(phone_countries(phones)).most_common(2):
        cast(cc, "phone", "%d phone link(s)" % n)
    if not phones:
        for cc, n in Counter(phone_countries(text_phones)).most_common(1):
            cast(cc, "phone_text", "%d phone number(s) in the text" % n)
    if currency_list and currency_list[0] in CURRENCY_COUNTRY:
        cast(CURRENCY_COUNTRY[currency_list[0]], "currency", currency_list[0])
    if has_impressum:
        cast("DE", "impressum", "has an Impressum (German legal notice) page")
    if shop_country:
        cast(_country_code(shop_country), "shop_registration", "Shopify store's registered country")
    if not geo_redirected:
        for tag in (og_locale, lang):
            tag = (tag or "").strip()
            m = re.match(r"^[a-z]{2,3}[-_]([A-Za-z]{2})$", tag)
            if m:
                kind = "lang_default_region" if tag.lower() in DEFAULT_LOCALES else "lang_region"
                cast(m.group(1).upper(), kind, tag)
                break
            if tag.lower() in LANG_ONLY:
                cast(LANG_ONLY[tag.lower()], "lang_only", tag)
                break
    if not votes:
        return {"code": None, "confidence": 0.0, "evidence": []}
    code, top = votes.most_common(1)[0]
    return {"code": code, "confidence": round(top / sum(votes.values()), 2),
            "evidence": evidence}


GEO_PATH = re.compile(r"^/([a-z]{2}[-_][a-z]{2})(/|$)", re.I)


def geo_redirect(requested, final):
    """Did the site send us somewhere chosen by our location? A locale path
    the request did not ask for, or a different host (a country subdomain)."""
    req, fin = urlsplit(requested), urlsplit(final)
    m = GEO_PATH.match(fin.path or "")
    if m and not GEO_PATH.match(req.path or ""):
        return "locale path /%s" % m.group(1)
    if _host(requested) != _host(final):
        return "host %s" % _host(final)
    return None


# == fetching ======================================================================

_TLS = []


def tls_context():
    """The TLS settings `requests` uses: urllib3's context with certifi's
    certificates. Measured from Railway (2026-10-09), with the same address,
    user agent and headers: three Shopify stores answered 429 to a connection
    made with ssl.create_default_context() and 200 to one made with these
    settings, so their edge tells the two handshakes apart. Built once; an
    SSLContext is safe to share between connections."""
    if not _TLS:
        import certifi
        from urllib3.util.ssl_ import create_urllib3_context
        ctx = create_urllib3_context()
        ctx.load_verify_locations(certifi.where())
        _TLS.append(ctx)
    return _TLS[0]


def fetch(url):
    """One page, raw HTML and readable text, classified. Never raises."""
    out = {"url": url, "final_url": url, "status": "error", "http_status": None,
           "html": "", "text": "", "note": "", "truncated": False}
    try:
        # Our own user agent, always; only the Accept headers are generic.
        # From Railway, three Shopify stores answered this crawler 429 when
        # it sent "Accept: text/html,application/xhtml+xml" and 200 when it
        # sent "*/*" with gzip (allbirds.com, snocks.com, insiderstore.com.br,
        # 2026-10-09). The Content-Type check below still refuses non-pages.
        r = public_get(url, timeout=TIMEOUT, stream=True, ssl_context=tls_context(),
                       headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip, deflate"})
    except requests.Timeout:
        out["note"] = "timed out after %ss" % TIMEOUT
        return out
    except ValueError as e:
        # The safe fetcher refuses private and reserved addresses: the bare
        # pacificdentalservices.com resolved to 192.0.2.1 (2026-10-09).
        out["note"] = "the domain points to an address we do not fetch (%s)" % str(e)[:80]
        return out
    except Exception as e:
        out["note"] = "could not be reached (%s)" % type(e).__name__
        return out
    try:
        out["http_status"] = r.status_code
        out["final_url"] = str(getattr(r, "url", "") or url)
        if r.status_code == 404:
            out["status"], out["note"] = "not_found", "page not found (404)"
            return out
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            out["status"], out["note"] = "blocked", "server refused (HTTP %s)" % r.status_code
            return out
        if r.status_code != 200:
            out["note"] = "unexpected HTTP %s" % r.status_code
            return out
        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype and "html" not in ctype and "xml" not in ctype:
            out["status"], out["note"] = "not_html", "not a web page (%s)" % ctype[:60]
            return out
        raw = _read_body(r, out)
        markup = raw.decode(_charset(r, raw), errors="replace")
    except Exception as e:
        out["note"] = "reading the page failed (%s)" % type(e).__name__
        return out
    finally:
        r.close()
    out["html"] = markup
    out["text"] = html_to_linked_text(markup, out["final_url"])
    out["status"] = "ok"
    if out["truncated"]:
        out["note"] = "page was larger than 3 MB and was cut"
    return out


def fetch_text(url, limit=3_000_000):
    """A non-HTML resource (robots.txt, a sitemap) as text, or None."""
    try:
        # Shopify's edge answers 403 to a request with no Accept header
        # (allbirds.com, snocks.com: robots.txt and sitemap.xml, 2026-10-09).
        r = public_get(url, timeout=TIMEOUT, stream=True, ssl_context=tls_context(),
                       headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"})
    except Exception:
        return None
    try:
        if r.status_code != 200:
            return None
        chunks, total = [], 0
        for chunk in r.iter_content(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                break
        raw = b"".join(chunks)
        if raw[:2] == b"\x1f\x8b":
            import gzip
            try:
                raw = gzip.decompress(raw)
            except Exception:
                return None
        return raw.decode("utf-8", errors="replace")
    finally:
        r.close()


def fetch_json(url):
    """A small JSON document (Shopify's /meta.json), or None."""
    text = fetch_text(url, limit=300_000)
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# == the Wayback Machine, for sites that refuse this server ==========================
#
# clovedental.in answered 403 to every request style from Railway, even
# full browser headers (2026-10-09): it refuses datacenter addresses, not a
# header. Its public copy in the Internet Archive is the honest way to read
# it. A page read this way is labelled with the capture date everywhere it
# is shown. No proxies, no disguised browsers.

WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
MAX_ARCHIVE_PAGES = 4


WAYBACK_AVAILABLE = "https://archive.org/wayback/available"
MAX_ARCHIVE_AGE_DAYS = 400


def _now():
    return datetime.now(timezone.utc)


def _recent(stamp):
    """A capture older than about a year describes a different company: the
    nearest 200 capture of lululemon.com is from 2011, because every recent
    capture of the bare domain is a redirect (2026-10-09)."""
    try:
        when = datetime.strptime(stamp[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return (_now() - when).days <= MAX_ARCHIVE_AGE_DAYS


def wayback_latest(url):
    """The newest capture of this URL that the archive saw answer 200, as
    (timestamp, original_url), or None. The CDX search is asked first; when
    it fails (it answered 503 for two days running, 2026-10-08 and 09), the
    simpler availability API is asked for the capture nearest today."""
    parts = urlsplit(url)
    query = parts.netloc + (parts.path or "/")
    text = fetch_text("%s?url=%s&output=json&limit=-1&filter=statuscode:200&fl=timestamp,original"
                      % (WAYBACK_CDX, requests.utils.quote(query, safe="/:")), limit=100_000)
    try:
        rows = json.loads(text or "")
    except ValueError:
        rows = None
    if rows is not None:
        if len(rows) < 2 or len(rows[-1]) < 2 or not _recent(rows[-1][0]):
            return None
        return rows[-1][0], rows[-1][1]
    # The availability API answers {} for "host/" but finds "host", and the
    # capture nearest today of a bare domain is often its redirect to www
    # (planetfitness.com), so the www form is asked too.
    path = parts.path if parts.path not in ("", "/") else ""
    host = parts.netloc
    hosts = [host, host[4:] if host.startswith("www.") else "www." + host]
    for h in hosts:
        data = fetch_json("%s?url=%s&timestamp=%s" % (WAYBACK_AVAILABLE,
                                                     requests.utils.quote(h + path, safe="/:"),
                                                     _now().strftime("%Y%m%d")))
        snap = ((data or {}).get("archived_snapshots") or {}).get("closest") or {}
        stamp = str(snap.get("timestamp") or "")
        if not snap.get("available") or str(snap.get("status")) != "200" or not _recent(stamp):
            continue
        m = re.match(r"https?://web\.archive\.org/web/\d+/(.+)$", snap.get("url") or "")
        if m:
            return stamp, m.group(1)
    return None


def fetch_archived(url):
    """The page as the Wayback Machine last saw it, shaped like fetch()'s
    result with final_url set to the ORIGINAL address (so its links resolve
    against the real site) and `via` naming the capture. Never raises."""
    found = wayback_latest(url)
    if not found:
        return dict(fetch_failed(url), note="the Wayback Machine has no readable copy")
    stamp, original = found
    page = fetch("https://web.archive.org/web/%sid_/%s" % (stamp, original))
    if page["status"] != "ok":
        return dict(page, final_url=url, note="the Wayback Machine copy could not be read (%s)"
                    % (page["note"] or page["status"]))
    when = "%s-%s-%s" % (stamp[:4], stamp[4:6], stamp[6:8])
    page.update(final_url=url, via="wayback:" + stamp,
                note="read from the Wayback Machine's copy of %s: the site refuses this server" % when)
    page["text"] = html_to_linked_text(page["html"], url)
    return page


def fetch_failed(url):
    return {"url": url, "final_url": url, "status": "error", "http_status": None, "html": "",
            "text": "", "note": "", "truncated": False}


def refuses_us(page):
    return page["status"] == "blocked" and page.get("http_status") in (401, 403, 429)


# == robots.txt =====================================================================

def parse_robots(text, agent_token="position2"):
    """(disallowed path prefixes that apply to us, sitemap URLs). The group
    naming our crawler wins over '*', as robots.txt specifies."""
    groups, sitemaps, current, agents = {}, [], None, []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = [x.strip() for x in line.split(":", 1)]
        key = key.lower()
        if key == "sitemap":
            sitemaps.append(value)
        elif key == "user-agent":
            if current is not None:
                agents, current = [], None
            agents.append(value.lower())
        elif key in ("disallow", "allow"):
            if current is None:
                current = {"disallow": [], "allow": []}
                for a in agents:
                    groups[a] = current
            if value:
                current[key].append(value)
    chosen = next((g for a, g in groups.items() if a != "*" and a in agent_token), None) \
        or groups.get("*") or {"disallow": [], "allow": []}
    return chosen, sitemaps


def allowed(path, rules):
    """Longest matching rule wins; Allow wins a tie (the robots.txt standard)."""
    best, verdict = -1, True
    for kind in ("disallow", "allow"):
        for rule in rules.get(kind, []):
            pattern = re.escape(rule).replace(r"\*", ".*")
            if pattern.endswith(r"\$"):
                pattern = pattern[:-2] + "$"
            if re.match(pattern, path) and (len(rule) > best or (len(rule) == best and kind == "allow")):
                best, verdict = len(rule), kind == "allow"
    return verdict


# == sitemap ========================================================================

def read_sitemap(home_url, sitemap_urls):
    """What the site's sitemap is made of: URL count, the commonest first path
    segments, and how many look like locations or products. Reads at most
    MAX_SITEMAP_FILES files. Returns None when there is no readable sitemap."""
    queue = list(dict.fromkeys(sitemap_urls or [urljoin(home_url, "/sitemap.xml")]))
    seen, urls, files_read = set(), [], 0
    while queue and files_read < MAX_SITEMAP_FILES and len(urls) < MAX_SITEMAP_URLS:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        body = fetch_text(sm)
        files_read += 1
        if not body:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
        if "<sitemapindex" in body[:2000]:
            # Prefer the sitemaps that describe places and products.
            locs.sort(key=lambda u: 0 if re.search(r"location|store|product|clinic|office|"
                                                   r"dentist|branch|loja|unidade", u, re.I) else 1)
            queue.extend(locs[:20])
        else:
            urls.extend(locs[:MAX_SITEMAP_URLS - len(urls)])
    if not urls:
        return None
    first = Counter()
    for u in urls:
        seg = [s for s in (urlsplit(u).path or "/").split("/") if s]
        first["/" + seg[0] if seg else "/"] += 1
    return {"files_read": files_read, "urls": len(urls), "capped": len(urls) >= MAX_SITEMAP_URLS,
            "top_sections": first.most_common(8),
            "location_like": sum(1 for u in urls if LOCATION_PATH.search(urlsplit(u).path or "")),
            "product_like": sum(1 for u in urls if PRODUCT_PATH.search(urlsplit(u).path or ""))}


# == choosing pages ================================================================

def pick_pages(links, home_url, rules):
    """The site's own pages worth reading, by kind, best first."""
    home_host = _host(home_url)
    chosen, taken, seen = [], Counter(), set()
    for kind, limit, words in PAGE_KINDS:
        for href, label in links:
            if taken[kind] >= limit or len(chosen) >= MAX_EXTRA_PAGES:
                break
            url = urljoin(home_url, href).split("#", 1)[0]
            if not url.startswith("http") or not _same_site(url, home_host):
                continue
            path = urlsplit(url).path or "/"
            if path in ("", "/") or SKIP_PATH.search(path) or path.rstrip("/") in seen:
                continue
            hay = (label or "").lower() + " " + path.lower().replace("-", " ").replace("_", " ")
            if any(w in hay for w in words):
                if not allowed(path, rules):
                    continue
                seen.add(path.rstrip("/"))
                taken[kind] += 1
                chosen.append((kind, url))
    return chosen


# == the whole site ==================================================================

def _words(page):
    return len(page["text"].split()) if page["status"] == "ok" else -1


def choose_home(url):
    """The page that best stands for the company's homepage, and notes on how
    it was chosen.

    Two live traps, both from the 2026-10-09 test set:
    * a bare domain can redirect somewhere useless (gymshark.com led to a
      checkout subdomain with 3 words of text; www.gymshark.com is the real
      store), so the www form is tried whenever the first read is poor;
    * a site can pick a version by the visitor's location (orangetheory.com
      sent a visitor in India to /en-in). When it did, and the page names
      its own default version (hreflang x-default), that version is read
      instead, so the profile describes the company and not our server."""
    notes = []
    home = fetch(url)
    if home["status"] != "ok" and url.startswith("https://"):
        retry = fetch("http://" + url[len("https://"):])
        if retry["status"] == "ok":
            home = retry
    redirect = geo_redirect(url, home["final_url"])
    host = _host(url)
    if (home["status"] != "ok" or _words(home) < MIN_WORDS or redirect) and \
            not (urlsplit(url).hostname or "").startswith("www.") and host.count(".") >= 1:
        alt = fetch("https://www." + host + (urlsplit(url).path or "/"))
        alt_redirect = geo_redirect("https://www." + host, alt["final_url"])
        better = _words(alt) > _words(home) and not (alt_redirect or "").startswith("host")
        if alt["status"] == "ok" and (better or (redirect and not alt_redirect)):
            notes.append("read www.%s: %s gave %s" % (
                host, host, redirect or "%d readable words" % max(_words(home), 0)))
            home, redirect = alt, alt_redirect
    if redirect and home["status"] == "ok":
        doc = parse_html(home["html"])
        default = next((urljoin(home["final_url"], h) for code, h in doc.hreflang
                        if code.lower() == "x-default" and h), None)
        if default and default.rstrip("/") != home["final_url"].rstrip("/"):
            again = fetch(default)
            if again["status"] == "ok":
                notes.append("the site chose %s for our location; read its default version %s"
                             % (redirect, default))
                home = again
                redirect = None
            else:
                notes.append("the site chose %s for our location; its default version %s "
                             "could not be read" % (redirect, default))
    home["geo_redirect"] = redirect
    return home, notes


def page_head(doc):
    """A page's <title> and meta description, as lines heading its text.
    Both are real page content the model quotes (Gymshark's name came from
    its title, its tagline from its description), and a quote check that
    cannot see them reports a true quote as invented."""
    lines = []
    title = " ".join(doc.title.split())
    if title:
        lines.append("TITLE: " + title)
    desc = " ".join((doc.meta.get("description") or doc.meta.get("og:description") or "").split())
    if desc:
        lines.append("DESCRIPTION: " + desc)
    return "".join(line + "\n" for line in lines)


def read_site(url):
    """Read a company's homepage and up to MAX_EXTRA_PAGES of its own pages.
    Returns {"home_url", "domain", "pages", "signals", "country", ...}.
    Never raises; an unreadable site comes back with status and reasons."""
    started = datetime.now(timezone.utc)
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    if not urlsplit(url).path:
        url += "/"                     # one spelling of the homepage, so it is fetched once
    home, notes = choose_home(url)
    home_url = home["final_url"]
    domain = _host(home_url) or _host(url)
    out = {"input_url": url, "home_url": home_url, "domain": domain,
           "read_at": started.isoformat(timespec="seconds"),
           "pages": [], "texts": {}, "signals": {}, "country": None,
           "needs_browser": False, "geo_redirect": home.get("geo_redirect"),
           "home_notes": notes, "robots": None, "status": home["status"]}
    refused = refuses_us(home)
    if refused:
        archived = fetch_archived(home_url)
        notes.append("the site refused this server (HTTP %s)" % home["http_status"])
        if archived["status"] == "ok":
            home = archived
            out["status"] = "ok"
        else:
            # Say we tried: "refused" alone reads as if no fallback existed.
            home["note"] = "; ".join(x for x in (home["note"], archived["note"]) if x)
    out["via_archive"] = bool(home.get("via"))
    out["pages"].append({"kind": "home", "url": home_url, "status": home["status"],
                         "http_status": home["http_status"], "note": home["note"],
                         "words": len(home["text"].split()), "via": home.get("via")})
    if home["status"] != "ok":
        if refused:
            # Shopify's store record often answers even when the pages do not.
            meta = fetch_json(urljoin(home_url, "/meta.json"))
            if shopify_store(meta):
                out["shopify_store"] = shopify_store(meta)
        return out

    robots_text = fetch_text(urljoin(home_url, "/robots.txt"), limit=500_000)
    rules, sitemaps = parse_robots(robots_text)
    out["robots"] = {"found": robots_text is not None, "sitemaps": sitemaps[:5],
                     "disallow_count": len(rules.get("disallow", []))}

    home_doc = parse_html(home["html"])
    words = len(home["text"].split())
    if words < MIN_WORDS:
        out["needs_browser"] = True
        out["pages"][0]["note"] = (home["note"] + "; " if home["note"] else "") + \
            "only %d readable words: the site is built in the browser (%s)" % (
                words, client_render_marker(home["html"]) or "no framework marker")

    extra = pick_pages(home_doc.links, home_url, rules)
    if refused:
        # The live site already refused us; its other pages come from the
        # archive too, a few only, since each archive read is slow.
        extra = extra[:MAX_ARCHIVE_PAGES]
    docs = [("home", home, home_doc)]
    reader = fetch_archived if refused else fetch
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda kv: (kv[0], reader(kv[1])), extra))
    for kind, page in results:
        out["pages"].append({"kind": kind, "url": page["final_url"], "status": page["status"],
                             "http_status": page["http_status"], "note": page["note"],
                             "words": len(page["text"].split()), "via": page.get("via")})
        if page["status"] == "ok":
            docs.append((kind, page, parse_html(page["html"])))

    out["texts"] = {p["final_url"]: page_head(d) + p["text"] for _, p, d in docs}
    out["signals"] = signals_from(docs, home_url)
    sitemap = None if refused else read_sitemap(home_url, sitemaps)
    out["signals"]["sitemap"] = sitemap
    s = out["signals"]
    if refused or any(p["name"] == "shopify" for p in s["platforms"]):
        store = shopify_store(fetch_json(urljoin(home_url, "/meta.json")))
        if store:
            out["shopify_store"] = store
            if not any(p["name"] == "shopify" for p in s["platforms"]):
                s["platforms"].append({"name": "shopify", "kind": "commerce"})
    out["country"] = country_vote(domain, s["organizations"], s["phones"], s["currencies"],
                                  s["html_lang"], s["og_locale"], text_phones=s["text_phones"],
                                  has_impressum=s["has_impressum"],
                                  geo_redirected=bool(out["geo_redirect"]),
                                  shop_country=(out.get("shopify_store") or {}).get("country"))
    out["hints"] = archetype_hints(s)
    return out


def shopify_store(meta):
    """The public record a Shopify store serves at /meta.json, reduced to what
    a profile uses. Its address is the store ACCOUNT's registered address,
    which can differ from the head office (allbirds.com's says Beverly Hills;
    the company is in San Francisco)."""
    if not isinstance(meta, dict) or not meta.get("myshopify_domain"):
        return None
    ships = meta.get("ships_to_countries") or []
    return {"name": meta.get("name"), "city": meta.get("city"), "province": meta.get("province"),
            "country": meta.get("country"), "currency": meta.get("currency"),
            "ships_to_countries": ships[:60] if isinstance(ships, list) else [],
            "products": meta.get("published_products_count"),
            "collections": meta.get("published_collections_count"),
            "description": (meta.get("description") or "")[:400]}


def signals_from(docs, home_url):
    home_host = _host(home_url)
    all_html = "\n".join(p["html"] for _, p, _ in docs)
    all_text = "\n".join(p["text"] for _, p, _ in docs)
    hrefs = [urljoin(p["final_url"], h) for _, p, d in docs for h, _ in d.links]
    script_srcs = " ".join(s for _, _, d in docs for s in d.scripts_src)
    nodes = []
    for _, _, d in docs:
        nodes.extend(_jsonld_nodes(d.jsonld_raw))
    home_doc = docs[0][2]
    orgs = organizations(nodes)
    phones = list(dict.fromkeys(
        [h[4:] for h in hrefs if h.lower().startswith("tel:")]
        + [o["telephone"] for o in orgs if o.get("telephone")]))[:10]
    emails = list(dict.fromkeys(
        h[7:].split("?")[0].lower() for h in hrefs if h.lower().startswith("mailto:")))[:6]
    internal = [h for h in hrefs if h.startswith("http") and _same_site(h, home_host)]
    location_links = sorted({h.split("#")[0] for h in internal
                             if LOCATION_PATH.search(urlsplit(h).path or "")})
    jsonld_types = Counter(t for n in nodes for t in _types(n))
    platform_hay = all_html[:4_000_000] + " " + script_srcs
    return {
        "title": " ".join(home_doc.title.split())[:200],
        "description": (home_doc.meta.get("description") or home_doc.meta.get("og:description") or "")[:400],
        "site_name": home_doc.meta.get("og:site_name"),
        "html_lang": home_doc.lang,
        "og_locale": home_doc.meta.get("og:locale"),
        "hreflang": sorted({h for h, _ in home_doc.hreflang})[:30],
        "jsonld_types": dict(jsonld_types.most_common(12)),
        "organizations": orgs[:12],
        "phones": phones,
        "emails": emails,
        "socials": social_profiles(hrefs),
        "ats": ats_boards(" ".join(hrefs) + " " + script_srcs),
        "platforms": [{"name": n, "kind": k} for n, k in detect(PLATFORMS, platform_hay)],
        "locator_vendors": detect(LOCATOR_VENDORS, platform_hay),
        "map_embed": bool(re.search(MAP_EMBED, platform_hay, re.I)),
        "text_phones": list(dict.fromkeys(m.group(0) for m in TEXT_PHONE.finditer(all_text)))[:10],
        "has_impressum": any(kind == "legal" and "impressum" in (p["final_url"] + " " + p["text"][:300]).lower()
                             for kind, p, _ in docs),
        "currencies": currencies(all_html[:2_000_000], all_text),
        "location_links": {"count": len(location_links), "sample": location_links[:8]},
        "has_cart": any(re.search(r"/cart(/|$|\?)", urlsplit(h).path or "") for h in internal),
        "product_schema": any(t in jsonld_types for t in ("Product", "ProductGroup", "Offer")),
        "careers_links": careers_links([l for _, _, d in docs for l in d.links], home_url),
        "feeds": list(dict.fromkeys(urljoin(p["final_url"], h) for _, p, d in docs
                                    for h, _ in d.feeds))[:6],
        "ratings": [r for _, p, d in docs for r in ratings(_jsonld_nodes(d.jsonld_raw),
                                                             p["final_url"])][:12],
    }


def _address_key(addr):
    """Town and postcode: the same place written with and without its street
    or country must count once (austincitydental.com lists one office twice)."""
    a = addr or {}
    return ((a.get("addressLocality") or "").strip().lower(), (a.get("postalCode") or "").strip())


def archetype_hints(s):
    """Facts that point at a business type. Hints only: the model decides,
    and these are shown to it as evidence, not as an answer."""
    hints = []
    sm = s.get("sitemap") or {}
    platforms = [p["name"] for p in s["platforms"] if p["kind"] == "commerce"]
    shop = [p for p in platforms if p in SHOP_PLATFORMS]
    has_products = sm.get("product_like", 0) >= 20 or s["product_schema"]
    if shop:
        hints.append("runs on a shop platform: %s" % ", ".join(shop))
    elif platforms and has_products:
        hints.append("has a shop plugin with products: %s" % ", ".join(platforms))
    elif platforms:
        hints.append("has a shop plugin installed (%s) but no product pages were found"
                     % ", ".join(platforms))
    if sm.get("product_like", 0) >= 20:
        hints.append("sitemap lists %d product pages" % sm["product_like"])
    if s["product_schema"]:
        hints.append("pages carry product structured data")
    if sm.get("location_like", 0) >= 5:
        hints.append("sitemap lists %d location-like pages" % sm["location_like"])
    if s["location_links"]["count"] >= 5:
        hints.append("links to %d location-like pages" % s["location_links"]["count"])
    if s["locator_vendors"]:
        hints.append("embeds a store locator: %s" % ", ".join(s["locator_vendors"]))
    addresses = {_address_key(o["address"]) for o in s["organizations"]
                 if o.get("address") and any(_address_key(o["address"]))}
    if len(addresses) == 1:
        hints.append("structured data gives one business address")
    elif len(addresses) > 1:
        hints.append("structured data gives %d different business addresses" % len(addresses))
    return hints
