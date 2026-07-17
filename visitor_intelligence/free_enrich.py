"""
Free-tier company enrichment: no Apollo credits, no third-party data purchase.

This is the DEFAULT enrichment path. It fetches the company's own homepage and
extracts what the company already publishes about itself:

  1. schema.org Organization JSON-LD  -- legalName, description, address,
     sameAs (social/LinkedIn links), foundingDate, when the site publishes it
  2. OpenGraph / meta tags            -- title, description, site_name
  3. Tech-stack fingerprinting        -- regex signatures over the fetched HTML
     for common analytics/CRM/framework/hosting tags (the same technique
     BuiltWith/Wappalyzer use, hand-rolled here so it costs nothing)
  4. Clearbit Logo API                -- https://logo.clearbit.com/{domain},
     free/no-auth, confirms the domain resolves to a real brand + gets a logo
  5. SEC EDGAR full-text search       -- free + authoritative for revenue/
     employee data, but ONLY for US public filers (most B2B visitors are
     private companies, so this frequently returns nothing; that is honest,
     not a bug)

What this deliberately does NOT do: scrape LinkedIn. Company people/leadership
lookups only use a company's own published "/about"/"/leadership"/"/team" page
plus (optionally, by the caller) a web search for public press mentions. No
LinkedIn profile is ever fetched or parsed here.

Coverage is real but partial: a company's own homepage might not carry
schema.org markup (many don't), private-company revenue/employee counts are
usually not public anywhere, and a small site might have no OG tags at all.
Every field is best-effort and simply omitted when not found, never guessed.
"""

from __future__ import annotations

import json
import re
import logging
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional
from urllib.parse import quote

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; visitor-intel-research/1.0; +free-tier enrichment)"
# SEC EDGAR's fair-access policy rejects generic User-Agents with a 403 and
# requires an identifiable requester (https://www.sec.gov/os/webmaster-faq#developers).
_SEC_UA = "Position2 Intelligence Platform %s" % \
    __import__("os").environ.get("SEC_EDGAR_CONTACT", "reporting@position2.com")
_TTL = 7 * 86400
_CACHE: Dict[str, tuple] = {}


def _fetch(url: str, timeout: float = 6.0, user_agent: Optional[str] = None) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent or _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def _fetch_json(url: str, timeout: float = 6.0) -> Optional[Any]:
    text = _fetch(url, timeout=timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# schema.org / OpenGraph / meta extraction
# --------------------------------------------------------------------------- #
def _extract_jsonld_org(html: str) -> Dict[str, Any]:
    """First Organization (or @graph entry of type Organization) in any
    application/ld+json block on the page."""
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I)
    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = []
        if isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                candidates.extend(data["@graph"])
            else:
                candidates.append(data)
        elif isinstance(data, list):
            candidates.extend(data)
        for c in candidates:
            if not isinstance(c, dict):
                continue
            t = c.get("@type")
            types = t if isinstance(t, list) else [t]
            if any((x or "").lower() == "organization" for x in types):
                return c
    return {}


def _extract_og(html: str) -> Dict[str, str]:
    out = {}
    for m in re.finditer(
        r'<meta[^>]+property=["\']og:([a-zA-Z:]+)["\'][^>]+content=["\']([^"\']*)["\']',
        html):
        out[m.group(1)] = m.group(2)
    # meta description as a fallback
    if "description" not in out:
        d = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', html)
        if d:
            out["description"] = d.group(1)
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    if t:
        out["title"] = re.sub(r"\s+", " ", t.group(1)).strip()
    return out


def _unescape(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return (s.replace("&#39;", "'").replace("&amp;", "&")
            .replace("&quot;", '"').replace("&#x27;", "'"))


# --------------------------------------------------------------------------- #
# Tech-stack fingerprinting (hand-rolled Wappalyzer-style signatures)
# --------------------------------------------------------------------------- #
_TECH_SIGNATURES: Dict[str, str] = {
    "React": r"\breact\b|__NEXT_DATA__|_next/static",
    "Vue.js": r"__vue__|vue\.js|/vue@",
    "Angular": r"ng-version|angular\.js",
    "WordPress": r"wp-content|wp-includes|/wp-json/",
    "Shopify": r"cdn\.shopify\.com|shopify\.com/s/files",
    "Webflow": r"webflow\.com|data-wf-site",
    "Squarespace": r"squarespace\.com|static1\.squarespace",
    "Google Analytics / GTM": r"googletagmanager\.com|gtag\(|google-analytics\.com",
    "Segment": r"cdn\.segment\.com|analytics\.js",
    "HubSpot": r"js\.hs-scripts\.com|hubspot|hs-analytics",
    "Marketo": r"munchkin\.marketo|marketo\.com",
    "Salesforce": r"force\.com|salesforce\.com",
    "Intercom": r"widget\.intercom\.io|intercomcdn",
    "Drift": r"js\.driftt\.com",
    "Zendesk": r"zdassets\.com|zendesk\.com",
    "Cloudflare": r"cloudflare\.com|cf-ray",
    "Fastly": r"fastly\.net|x-served-by.*fastly",
    "Akamai": r"akamai(?:hd|edge)?\.net",
    "AWS / CloudFront": r"cloudfront\.net|amazonaws\.com",
    "Stripe (payments)": r"js\.stripe\.com|stripe\.com/v3",
    "Optimizely": r"cdn\.optimizely\.com",
    "Amplitude": r"cdn\.amplitude\.com",
    "Mixpanel": r"cdn\.mxpnl\.com|mixpanel\.com",
    "Datadog RUM": r"datadoghq-browser-agent",
    "Sentry": r"sentry\.io|sentry-cdn\.com",
}


def detect_technologies(html: str, headers: Optional[Dict[str, str]] = None) -> List[str]:
    hay = html
    if headers:
        hay += " " + " ".join("%s: %s" % (k, v) for k, v in headers.items())
    found = []
    for name, pattern in _TECH_SIGNATURES.items():
        if re.search(pattern, hay, re.I):
            found.append(name)
    return found


# --------------------------------------------------------------------------- #
# Free public data sources
# --------------------------------------------------------------------------- #
def clearbit_logo_exists(domain: str, timeout: float = 4.0) -> Optional[str]:
    """Free, no-auth. Returns the logo URL if Clearbit has this domain indexed
    (a soft signal the domain is a real, known brand), else None."""
    url = "https://logo.clearbit.com/%s" % domain
    try:
        req = urllib.request.Request(url, method="HEAD",
                                    headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return url if resp.status == 200 else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None


def sec_edgar_lookup(company_name: str, timeout: float = 6.0) -> Dict[str, Any]:
    """Free, authoritative, but ONLY covers US public filers. Most B2B website
    visitors are private companies, so an empty result here is expected and
    honest, not a failure of the method. Company name + CIK live in the
    top-level <company-info> block of EDGAR's atom feed, NOT inside <entry>
    (those are the individual filings)."""
    if not company_name:
        return {}
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=%s"
        "&type=10-K&dateb=&owner=include&count=5&output=atom" % quote(company_name))
    text = _fetch(url, timeout=timeout, user_agent=_SEC_UA)
    if not text or "<company-info" not in text:
        return {}
    # A unique match puts one <company-info> at the top level; an ambiguous
    # name (matches several companies) nests a <company-info> inside each
    # <entry> instead. Take whichever appears first -- the best/first result.
    m = re.search(r"<company-info[^>]*>(.*?)</company-info>", text, re.S)
    if not m:
        return {}
    info = m.group(1)
    name_m = re.search(r"<conformed-name>(.*?)</conformed-name>", info)
    cik_m = re.search(r"<cik>(\d+)</cik>", info)
    sic_m = re.search(r"<assigned-sic-desc>(.*?)</assigned-sic-desc>", info)
    if not name_m:
        # A common/ambiguous name (e.g. "Apple") matches several filers, and
        # SEC's per-entry company-info in that case omits conformed-name
        # entirely. Rather than guess which company a bare CIK belongs to,
        # decline the match -- an honest empty result beats a wrong company.
        return {}
    out = {"sec_public_filer": True, "sec_company_name": name_m.group(1).strip()}
    if cik_m:
        out["sec_cik"] = cik_m.group(1)
    if sic_m:
        out["sec_industry"] = sic_m.group(1).strip().title()
    return out


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def enrich_company_free(domain: str, check_sec: bool = False) -> Dict[str, Any]:
    """Domain -> best-effort firmographic dict, entirely from free/public
    sources. Every field is omitted (not guessed) when not found."""
    if not domain:
        return {}
    key = domain.lower()
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    out: Dict[str, Any] = {"domain": domain, "source": "free"}
    html = _fetch("https://%s" % domain) or _fetch("http://%s" % domain)
    if html:
        org = _extract_jsonld_org(html)
        og = _extract_og(html)

        name = org.get("legalName") or org.get("name") or og.get("site_name")
        desc = org.get("description") or og.get("description")
        out["name"] = _unescape(name)
        out["description"] = _unescape(desc)[:400] if desc else None
        out["title_tag"] = _unescape(og.get("title"))

        addr = org.get("address")
        if isinstance(addr, dict):
            out["hq_city"] = addr.get("addressLocality")
            out["hq_country"] = addr.get("addressCountry")
        elif isinstance(addr, list) and addr and isinstance(addr[0], dict):
            out["hq_city"] = addr[0].get("addressLocality")
            out["hq_country"] = addr[0].get("addressCountry")

        same_as = org.get("sameAs") or []
        if isinstance(same_as, str):
            same_as = [same_as]
        out["linkedin_url"] = next(
            (u for u in same_as if "linkedin.com" in (u or "")), None)
        out["social_links"] = [u for u in same_as if u][:8]
        out["founded_year"] = (org.get("foundingDate") or "")[:4] or None

        out["technologies"] = detect_technologies(html)
    else:
        out["fetch_failed"] = True

    if clearbit_logo_exists(domain):
        out["logo_url"] = "https://logo.clearbit.com/%s" % domain
        out["known_brand"] = True

    if check_sec and out.get("name"):
        sec = sec_edgar_lookup(out["name"])
        out.update(sec)

    out = {k: v for k, v in out.items() if v not in (None, "", [])}
    _CACHE[key] = (time.time() + _TTL, out)
    return out


# A title must contain one of these to count as a person's role. Marketing
# copy picked up from a generic /team or /about page ("500M+ API requests",
# "What's happening") never matches this, which is the whole point: better to
# return nothing than to label marketing copy as a person.
_TITLE_KEYWORDS = re.compile(
    r"\b(CEO|CTO|CFO|COO|CMO|CISO|CPO|CRO|President|Founder|Co-?founder|"
    r"Chief\s+\w+\s+Officer|VP\b|Vice\s+President|Director|Head\s+of|"
    r"Manager|Lead\b|Engineer|Designer|Partner)\b", re.I)


def fetch_team_page(domain: str) -> List[Dict[str, str]]:
    """Best-effort: fetch the company's own /about, /leadership, /team, or
    /company/team page and pull plausible name+title pairs from simple card-
    style markup. Zero LinkedIn involvement. Coverage is limited and
    deliberately conservative: a candidate is only kept when its "title" line
    actually contains a real job-title keyword (CEO, VP, Director, ...) --
    without that filter, generic marketing headings on the same page ("500M+
    API requests", "What's happening") get mistaken for names, which is worse
    than returning nothing."""
    for path in ("/leadership", "/about/leadership", "/company/leadership",
                "/about-us/leadership", "/team", "/about/team", "/company/team",
                "/about"):
        html = _fetch("https://%s%s" % (domain, path))
        if not html:
            continue
        blocks = re.findall(
            r'<h[2-4][^>]*>([^<]{3,60})</h[2-4]>\s*(?:<p[^>]*>)?([^<]{3,80})?',
            html)
        people = []
        for name, title in blocks:
            name = _unescape(name).strip()
            title = _unescape(title or "").strip()
            if not (1 <= len(name.split()) <= 4 and title and len(title) < 80):
                continue
            if not _TITLE_KEYWORDS.search(title):
                continue
            people.append({"name": name, "title": title, "source_path": path})
        if people:
            return people[:15]
    return []
