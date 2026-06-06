"""Fetch news articles via Google News RSS (or SerpAPI when key is provided)."""

from __future__ import annotations

import base64
import email.utils
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _decode_google_news_url(google_url: str) -> str:
    """Decode a Google News redirect URL to the actual article URL.

    Google News RSS wraps every article link in a redirect like:
        https://news.google.com/rss/articles/CBMi<base64>?...
    The base64 path encodes (among other things) the original article URL.
    This function extracts it without making any HTTP requests.
    Returns the original google_url unchanged on any failure.
    """
    if not google_url or "news.google.com" not in google_url:
        return google_url
    match = re.search(r"/articles/([A-Za-z0-9_=-]+)", google_url)
    if not match:
        return google_url
    encoded = match.group(1)
    # Restore standard base64 padding
    padding = (4 - len(encoded) % 4) % 4
    try:
        decoded_bytes = base64.urlsafe_b64decode(encoded + "=" * padding)
        decoded_text = decoded_bytes.decode("utf-8", errors="replace")
        # The real URL sits inside the decoded bytes — find the first non-Google http(s) URL
        url_match = re.search(
            r"https?://(?!news\.google\.com)[^\s\x00-\x1f\"'<>\x80-\xff]{10,}",
            decoded_text,
        )
        if url_match:
            real_url = url_match.group(0).rstrip(".,;)")
            return real_url
    except Exception:
        pass
    return google_url

MAX_NEWS_AGE_DAYS = 90

_EXEC_KEYWORDS = ["ceo", "cfo", "cto", "cmo", "coo", "president", "chief", "vice president"]


def _parse_article_date(date_str: str) -> datetime | None:
    """Parse RSS (RFC 2822) and ISO 8601 date strings into a UTC datetime."""
    if not date_str:
        return None
    # RFC 2822 — standard Google News RSS format ("Mon, 12 May 2026 10:00:00 GMT")
    try:
        return email.utils.parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        pass
    # ISO 8601 — fromisoformat handles YYYY-MM-DD, YYYY-MM-DDTHH:MM:SSZ, etc.
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    return None


def _is_article_fresh(date_str: str, max_age_days: int = MAX_NEWS_AGE_DAYS) -> bool:
    """Return True only if date_str is present, parseable, and within max_age_days."""
    dt = _parse_article_date(date_str)
    if dt is None:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


def _has_exec_keyword(text: str) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in _EXEC_KEYWORDS)


def _extract_name_title(text: str) -> tuple[str | None, str | None]:
    """Try to extract a (name, title) pair from a news headline or snippet."""
    _title_pat = r'(Chief\b[^,\n]{0,35}|C[EFILMOT]O\b[^,\n]{0,25}|President\b[^,\n]{0,25}|Vice\s+President\b[^,\n]{0,25}|VP\b[^,\n]{0,25})'
    _name_pat = r'([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})'

    # Pattern A: "Name joins/hired/named/appointed [as] Title"
    m = re.search(
        rf'\b{_name_pat}\b\s+'
        r'(?:joins?|was hired|was named|was appointed|named|hired|appointed)'
        rf'(?:\s+(?:as|to))?\s+(?:new\s+)?{_title_pat}',
        text,
    )
    if m and _has_exec_keyword(m.group(2)):
        return m.group(1), m.group(2).strip(" .")

    # Pattern B: "appoints/names/hires Name [as] [new] Title"
    m = re.search(
        rf'(?:appoints?|names?|hires?)\s+{_name_pat}\s+(?:as\s+)?(?:new\s+)?{_title_pat}',
        text,
    )
    if m and _has_exec_keyword(m.group(2)):
        return m.group(1), m.group(2).strip(" .")

    return None, None


def get_news_articles(
    company_name: str,
    serpapi_key: str = "",
    max_articles: int = 5,
    max_age_days: int = MAX_NEWS_AGE_DAYS,
    ai_key: str = "",
    ai_filter: bool = False,
    ai_model: str = "gpt-4o-mini",
) -> list[dict]:
    """Return business-relevant article dicts for a company.

    A larger candidate pool is fetched, then passed through the relevance filter
    (heuristic always; AI gate when ai_filter and ai_key are set) so only news
    about real business events is stored. Returns at most ``max_articles``.
    """
    pool = max(max_articles * 3, 12)
    if serpapi_key:
        articles = _serpapi_articles(company_name, serpapi_key, pool, max_age_days)
        if not articles:
            articles = _rss_articles(company_name, pool, max_age_days)
    else:
        articles = _rss_articles(company_name, pool, max_age_days)

    try:
        from .news_relevance import filter_relevant_articles
        articles = filter_relevant_articles(
            company_name, articles,
            ai_key=ai_key if ai_filter else "", model=ai_model,
        )
    except Exception as exc:  # fail-open: never lose news because the filter broke
        logger.warning("[NEWS] relevance filter unavailable for %s: %s", company_name, exc)

    return articles[:max_articles]


def _rss_articles(
    company_name: str,
    max_articles: int = 5,
    max_age_days: int = MAX_NEWS_AGE_DAYS,
) -> list[dict]:
    try:
        import feedparser  # type: ignore
    except ImportError:
        logger.error("feedparser not installed. Run: pip install feedparser")
        return []

    query = urllib.parse.quote_plus(f'"{company_name}"')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        feed = feedparser.parse(url)
        results = []
        discarded = 0
        for entry in feed.entries:
            pub = entry.get("published", "")
            if not _is_article_fresh(pub, max_age_days):
                discarded += 1
                continue
            source = entry.get("source")
            raw_url = entry.get("link", "")
            results.append({
                "title": entry.get("title", ""),
                "url": _decode_google_news_url(raw_url),
                "summary": entry.get("summary", "")[:300],
                "source": source.get("title") if isinstance(source, dict) else str(source or ""),
                "published": pub,
            })
            if len(results) >= max_articles:
                break
        if discarded:
            logger.info("[NEWS] Discarded %d articles older than %d days for %s", discarded, max_age_days, company_name)
        return results
    except Exception as exc:
        logger.warning("RSS fetch failed for '%s': %s", company_name, exc)
        return []


def get_leadership_from_news(
    company_name: str,
    max_results: int = 5,
    max_age_days: int = MAX_NEWS_AGE_DAYS,
) -> list[dict]:
    """Search Google News RSS for C-suite appointment news for a company.

    Returns list of {name, title, source_url, published_date}.
    """
    try:
        import feedparser  # type: ignore
    except ImportError:
        logger.error("feedparser not installed. Run: pip install feedparser")
        return []

    query = urllib.parse.quote_plus(
        f'"{company_name}" CEO OR CFO OR CTO OR CMO OR President appointed hired joins'
    )
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        feed = feedparser.parse(url)
        results = []
        discarded = 0
        for entry in feed.entries:
            pub = entry.get("published", "")
            if not _is_article_fresh(pub, max_age_days):
                discarded += 1
                continue
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            name, exec_title = _extract_name_title(f"{title} {summary}")
            if name and exec_title:
                results.append({
                    "name": name,
                    "title": exec_title,
                    "source_url": _decode_google_news_url(entry.get("link", "")),
                    "published_date": pub,
                })
            if len(results) >= max_results:
                break
        if discarded:
            logger.info("[NEWS] Discarded %d articles older than %d days for %s", discarded, max_age_days, company_name)
        return results
    except Exception as exc:
        logger.warning("Leadership news fetch failed for '%s': %s", company_name, exc)
        return []


def _serpapi_articles(
    company_name: str,
    serpapi_key: str,
    max_articles: int = 5,
    max_age_days: int = MAX_NEWS_AGE_DAYS,
) -> list[dict]:
    try:
        from serpapi import GoogleSearch  # type: ignore
    except ImportError:
        return []
    try:
        params = {"q": company_name, "tbm": "nws", "tbs": "qdr:w", "api_key": serpapi_key, "num": max_articles}
        raw_results = GoogleSearch(params).get_dict().get("news_results", [])
        results = []
        discarded = 0
        for r in raw_results:
            pub = r.get("date", "")
            if pub and not _is_article_fresh(pub, max_age_days):
                discarded += 1
                continue
            results.append({
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "summary": r.get("snippet", "")[:300],
                "source": r.get("source", ""),
                "published": pub,
            })
            if len(results) >= max_articles:
                break
        if discarded:
            logger.info("[NEWS] Discarded %d SerpAPI articles older than %d days for %s", discarded, max_age_days, company_name)
        return results
    except Exception as exc:
        logger.warning("SerpAPI fetch failed for '%s': %s", company_name, exc)
        return []
