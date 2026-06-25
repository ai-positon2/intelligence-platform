"""Detect creative / 3D hiring activity via free job-board + careers RSS.

Mirrors news_client: queries Google's RSS search scoped to creative-role +
hiring terms for a company, keeps fresh results whose title/summary actually
contain a creative-role keyword, and returns structured posting dicts. No paid
API, no LLM — just RSS over HTTP, so it runs for free in the weekly Action.

Public entry point: get_job_postings(company_name, ...).

The query is built to surface job-board / aggregator pages (LinkedIn Jobs,
Indeed, Greenhouse, Lever, BuiltIn, etc.) that Google indexes into its RSS.
A real per-company ATS feed URL can be plugged into ATS_FEEDS later for higher
precision without changing the fetcher.
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from .news_client import _decode_google_news_url, _is_article_fresh, _fetch_feed

logger = logging.getLogger(__name__)

MAX_JOB_AGE_DAYS = 90

# Creative / 3D role terms we care about (lowercased, matched as substrings).
# Marketing-creative content roles only — the kind that signal a company is
# investing in brand/ad/video/social output (a Position2 pitch hook).
CREATIVE_ROLE_KEYWORDS = [
    "3d artist", "3d animator", "3d generalist", "animator", "animation",
    "motion designer", "motion graphics", "vfx", "cgi", "video editor",
    "video producer", "videographer", "content creator", "content designer",
    "social media", "brand designer", "graphic designer", "art director",
    "creative director", "creative producer", "performance marketing",
    "growth marketer", "digital marketing", "campaign manager", "brand manager",
]
# Hard excludes: engineering / product / hardware / UX roles that merely contain
# the word "design" but are NOT marketing-creative — never a Position2 hook.
CREATIVE_ROLE_EXCLUDE = [
    "ux", "ui", "product designer", "industrial designer", "hardware",
    "mechanical", "pcb", "firmware", "embedded", "asic", "fpga", "chip",
    "electrical", "qa", "test engineer", "data engineer", "software engineer",
    "backend", "frontend", "devops", "sales", "accountant", "hr ",
]
_ROLE_RE = [re.compile(re.escape(k)) for k in CREATIVE_ROLE_KEYWORDS]
_ROLE_EXCLUDE_RE = [re.compile(re.escape(k)) for k in CREATIVE_ROLE_EXCLUDE]

# Hiring-context terms — at least one should be present to avoid pure news.
_HIRING_RE = [re.compile(re.escape(k)) for k in
              ("hiring", "job", "jobs", "career", "careers", "vacancy",
               "vacancies", "now hiring", "opening", "open role", "we're hiring",
               "join our", "apply")]

# Optional: explicit ATS / careers RSS feeds keyed by company name (lowercased).
# When present, used in addition to the RSS search for higher precision.
ATS_FEEDS: dict[str, str] = {}


def _norm(t: str) -> str:
    return (t or "").lower()


def is_creative_role(text: str) -> bool:
    t = _norm(text)
    if any(r.search(t) for r in _ROLE_EXCLUDE_RE):
        return False
    return any(r.search(t) for r in _ROLE_RE)


def _looks_like_hiring(text: str) -> bool:
    t = _norm(text)
    return any(r.search(t) for r in _HIRING_RE)


def _build_query(company_name: str) -> str:
    roles = '("3D artist" OR animator OR animation OR "motion designer" OR VFX OR CGI OR "art director")'
    hiring = '(hiring OR jobs OR careers OR "job opening")'
    return urllib.parse.quote_plus(f'"{company_name}" {roles} {hiring}')


CAREER_PATHS = [
    "/careers", "/career", "/jobs", "/en/careers", "/en/career",
    "/company/careers", "/about/careers", "/join-us", "/work-with-us",
]
CAREER_SUBDOMAINS = ["careers.", "jobs."]
_TAG_RE = __import__("re").compile(r"<[^>]+>")
_WS_RE = __import__("re").compile(r"\s+")


def _fetch_text(url: str, timeout: int = 10) -> str:
    """GET a URL and return de-tagged text (empty string on any failure)."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SignalTrackerBot/1.0)"})
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except Exception as exc:
        logger.debug("careers fetch failed %s: %s", url, exc)
        return ""
    text = _TAG_RE.sub(" ", raw)
    return _WS_RE.sub(" ", text)


def _candidate_career_urls(domain: str) -> list[str]:
    d = (domain or "").strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if not d:
        return []
    urls = [f"https://{d}{p}" for p in CAREER_PATHS]
    urls += [f"https://{sub}{d}" for sub in CAREER_SUBDOMAINS]
    return urls


import json as _json
import re as _re2

def _http_json(url, timeout=10):
    """GET a URL and parse JSON; return dict/list or None on any failure."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SignalTrackerBot/1.0)",
            "Accept": "application/json"})
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        return _json.loads(raw)
    except Exception as exc:
        logger.debug("ATS json fetch failed %s: %s", url, exc)
        return None

def _company_slugs(company_name, domain):
    """Candidate ATS slugs derived from domain root and company name."""
    slugs = []
    d = (domain or "").lower().replace("https://", "").replace("http://", "").strip("/")
    root = d.split("/")[0].split(".")[0] if d else ""
    if root:
        slugs.append(root)
    nm = _re2.sub(r"[^a-z0-9]", "", (company_name or "").lower())
    if nm and nm not in slugs:
        slugs.append(nm)
    nm2 = _re2.sub(r"[^a-z0-9]+", "-", (company_name or "").lower()).strip("-")
    if nm2 and nm2 not in slugs:
        slugs.append(nm2)
    return slugs[:3]

def get_ats_postings(company_name, domain):
    """Query common ATS public JSON APIs for the company's full job list.
    Returns [{title,url,location,source}] (may be empty). Structured + reliable."""
    out = []
    for slug in _company_slugs(company_name, domain):
        # Greenhouse
        d = _http_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if isinstance(d, dict) and isinstance(d.get("jobs"), list) and d["jobs"]:
            for j in d["jobs"]:
                out.append({"title": j.get("title", ""), "url": j.get("absolute_url", ""),
                            "location": (j.get("location") or {}).get("name", ""), "source": "Greenhouse"})
            return out
        # Lever
        d = _http_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if isinstance(d, list) and d:
            for j in d:
                out.append({"title": j.get("text", ""), "url": j.get("hostedUrl", ""),
                            "location": (j.get("categories") or {}).get("location", ""), "source": "Lever"})
            return out
        # Ashby
        d = _http_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if isinstance(d, dict) and isinstance(d.get("jobs"), list) and d["jobs"]:
            for j in d["jobs"]:
                out.append({"title": j.get("title", ""), "url": j.get("jobUrl", ""),
                            "location": j.get("location", ""), "source": "Ashby"})
            return out
        # SmartRecruiters
        d = _http_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings")
        if isinstance(d, dict) and isinstance(d.get("content"), list) and d["content"]:
            for j in d["content"]:
                loc = (j.get("location") or {})
                out.append({"title": j.get("name", ""), "url": (j.get("ref") or ""),
                            "location": loc.get("city", ""), "source": "SmartRecruiters"})
            return out
        # Recruitee
        d = _http_json(f"https://{slug}.recruitee.com/api/offers/")
        if isinstance(d, dict) and isinstance(d.get("offers"), list) and d["offers"]:
            for j in d["offers"]:
                out.append({"title": j.get("title", ""), "url": j.get("careers_url", ""),
                            "location": j.get("location", ""), "source": "Recruitee"})
            return out
    return out

def _discover_careers_links(domain):
    """Fetch the homepage and pull out any links pointing at careers/jobs pages."""
    d = (domain or "").lower().replace("https://", "").replace("http://", "").strip("/")
    if not d:
        return []
    import urllib.request
    try:
        req = urllib.request.Request(f"https://{d}", headers={"User-Agent": "Mozilla/5.0 (compatible; SignalTrackerBot/1.0)"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
    except Exception:
        return []
    links = []
    for m in _re2.finditer(r'href=["\']([^"\']+)["\']', html):
        href = m.group(1)
        if _re2.search(r'career|jobs|join-us|join_us|/join', href, _re2.I):
            if href.startswith("http"):
                links.append(href)
            elif href.startswith("/"):
                links.append(f"https://{d}{href}")
    seen=set(); uniq=[]
    for u in links:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq[:5]


def get_career_page_postings(company_name: str, domain: str, max_results: int = 5) -> list[dict]:
    """Scan a company's own careers page(s) for creative-role hiring.

    Tries common careers URLs/subdomains, de-tags the HTML, and if a marketing-
    creative role keyword appears in a hiring context, emits a posting pointing
    at that careers page. Heuristic but free and runs in the weekly Action.
    """
    results: list[dict] = []
    seen = set()

    # 1) Structured ATS APIs — real job titles, reliable. Keep creative roles only.
    for job in get_ats_postings(company_name, domain):
        title = (job.get("title") or "").strip()
        if not title or not is_creative_role(title):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        role = next((k for k in CREATIVE_ROLE_KEYWORDS if k in _norm(title)), "creative")
        loc = job.get("location", "")
        results.append({
            "title": f"{company_name} hiring: {title}" + (f" ({loc})" if loc else ""),
            "url": job.get("url", ""), "summary": "", "source": job.get("source", "Careers"),
            "published": "", "role": role,
        })
        if len(results) >= max_results:
            return results
    if results:
        return results

    # 2) Careers-page HTML fallback: discovered links first, then common paths.
    urls = _discover_careers_links(domain) + _candidate_career_urls(domain)
    seen_url = set()
    for url in urls:
        if url in seen_url:
            continue
        seen_url.add(url)
        text = _fetch_text(url)
        if not text:
            continue
        low = _norm(text)
        if not _looks_like_hiring(low):
            continue
        if any(r.search(low) for r in _ROLE_EXCLUDE_RE) and not any(r.search(low) for r in _ROLE_RE):
            continue
        roles = [k for k in CREATIVE_ROLE_KEYWORDS if k in low]
        if not roles:
            continue
        for role in roles:
            if role in seen:
                continue
            seen.add(role)
            results.append({
                "title": f"{company_name} careers page lists open role: {role.title()}",
                "url": url, "summary": "", "source": "Careers page", "published": "", "role": role,
            })
            if len(results) >= max_results:
                return results
        if results:
            return results
    return results


def get_job_postings(
    company_name: str,
    max_results: int = 5,
    max_age_days: int = MAX_JOB_AGE_DAYS,
    domain: str = "",
) -> list[dict]:
    """Return creative-hiring posting dicts for a company (may be empty).

    Each: {title, url, summary, source, published, role}. Only entries that
    are fresh AND mention a creative role are returned.
    """
    try:
        import feedparser  # type: ignore
    except ImportError:
        logger.error("feedparser not installed. Run: pip install feedparser")
        return []

    feeds = []
    ats = ATS_FEEDS.get(company_name.strip().lower())
    if ats:
        feeds.append(ats)
    q = _build_query(company_name)
    feeds.append(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")

    results: list[dict] = []
    seen_titles: set[str] = set()
    for url in feeds:
        try:
            feed = _fetch_feed(url)  # bounded timeout (was untimed)
        except Exception as exc:
            logger.warning("Jobs RSS fetch failed for '%s': %s", company_name, exc)
            continue
        for entry in feed.entries:
            title = entry.get("title", "") or ""
            summary = entry.get("summary", "")[:300] if entry.get("summary") else ""
            pub = entry.get("published", "")
            blob = title + " " + summary
            if not _is_article_fresh(pub, max_age_days):
                continue
            if not is_creative_role(blob):
                continue
            if not _looks_like_hiring(blob):
                continue
            key = title.strip().lower()
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            source = entry.get("source")
            role = next((k for k in CREATIVE_ROLE_KEYWORDS if k in _norm(blob)), "creative")
            results.append({
                "title": title,
                "url": _decode_google_news_url(entry.get("link", "")),
                "summary": summary,
                "source": source.get("title") if isinstance(source, dict) else str(source or ""),
                "published": pub,
                "role": role,
            })
            if len(results) >= max_results:
                return results
    # Fallback / augment: scan the company's own careers page(s).
    if domain and len(results) < max_results:
        for p in get_career_page_postings(company_name, domain, max_results=max_results - len(results)):
            key = (p.get("title") or "").strip().lower()
            if key and key not in seen_titles:
                seen_titles.add(key)
                results.append(p)
    return results
