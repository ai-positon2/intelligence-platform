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

from .news_client import _decode_google_news_url, _is_article_fresh

logger = logging.getLogger(__name__)

MAX_JOB_AGE_DAYS = 90

# Creative / 3D role terms we care about (lowercased, matched as substrings).
CREATIVE_ROLE_KEYWORDS = [
    "3d artist", "3d animator", "3d designer", "3d modeler", "3d modeller",
    "3d generalist", "animator", "animation", "motion designer",
    "motion graphics", "vfx", "cgi", "rigging", "rigger", "texture artist",
    "lighting artist", "render", "cinematic artist", "art director",
    "creative director", "visual designer", "graphic designer", "ux designer",
    "ui designer", "game artist", "character artist", "environment artist",
    "concept artist", "designer",
]
_ROLE_RE = [re.compile(re.escape(k)) for k in CREATIVE_ROLE_KEYWORDS]

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
    return any(r.search(t) for r in _ROLE_RE)


def _looks_like_hiring(text: str) -> bool:
    t = _norm(text)
    return any(r.search(t) for r in _HIRING_RE)


def _build_query(company_name: str) -> str:
    roles = '("3D artist" OR animator OR animation OR "motion designer" OR VFX OR CGI OR "art director")'
    hiring = '(hiring OR jobs OR careers OR "job opening")'
    return urllib.parse.quote_plus(f'"{company_name}" {roles} {hiring}')


def get_job_postings(
    company_name: str,
    max_results: int = 5,
    max_age_days: int = MAX_JOB_AGE_DAYS,
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
            feed = feedparser.parse(url)
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
    return results
