"""Fetch news articles via Google News RSS (or SerpAPI when key is provided)."""

from __future__ import annotations

import base64
import email.utils
import logging
import re
import urllib.parse
import json
import time
import random
import threading
import urllib.request
import concurrent.futures
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Hard cap on every Google News RSS request so a single slow/throttled
# response can never stall the whole run (this was causing multi-hour runs).
_FEED_TIMEOUT = 8  # seconds
_FEED_UA = "Mozilla/5.0 (compatible; SignalTracker/1.0; +https://intelligence.position2.com)"

# Module-level cache so news can be pre-fetched in parallel (see warm_news_cache)
# and then read instantly by get_news_articles during the sequential company loop.
_NEWS_CACHE: dict[str, list[dict]] = {}


_FEED_RETRIES = 2        # attempts per URL on transient failures (503/429/timeout)
_FEED_BACKOFF = 1.5      # base seconds; grows exponentially with jitter

# ── Circuit breaker ─────────────────────────────────────────────────────────
# Google blocks CI/datacenter IPs outright (every request 503s/times out). When
# that happens, retrying 1,200+ companies wastes an hour for zero data. After
# this many failures with no intervening success, we "open" the circuit and
# every further fetch returns instantly-empty so the step finishes in seconds.
_CIRCUIT_THRESHOLD = 30
_circuit_lock = threading.Lock()
_circuit_fails = 0
_circuit_open = False


def _circuit_record(success: bool) -> None:
    global _circuit_fails, _circuit_open
    with _circuit_lock:
        if success:
            _circuit_fails = 0
        else:
            _circuit_fails += 1
            if _circuit_fails >= _CIRCUIT_THRESHOLD and not _circuit_open:
                _circuit_open = True
                logger.error("[NEWS] circuit OPEN after %d consecutive failures — "
                             "endpoint is blocking us (likely IP block). Skipping "
                             "remaining fetches this run.", _circuit_fails)


def _fetch_feed(url: str):
    """Download an RSS URL with a hard timeout + retry/backoff, then parse bytes.

    feedparser.parse(url) does its own network fetch with NO timeout, so a
    throttled endpoint can hang indefinitely. We fetch ourselves (bounded) and
    parse the bytes. Google News rate-limits bursts (HTTP 503/429), so we retry
    transient failures with exponential backoff + jitter before giving up.
    Returns a parsed feed (possibly with no entries).
    """
    import feedparser  # type: ignore
    # If the endpoint has already proven to be blocking us this run, don't waste
    # time — return empty immediately.
    if _circuit_open:
        return feedparser.parse(b"")
    # Small upfront jitter de-synchronises concurrent workers so we don't hit
    # the endpoint in tight lockstep (a common 503 trigger).
    time.sleep(random.uniform(0, 0.5))
    last_exc = None
    for attempt in range(_FEED_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _FEED_UA})
            with urllib.request.urlopen(req, timeout=_FEED_TIMEOUT) as resp:
                data = resp.read()
            _circuit_record(True)
            return feedparser.parse(data)
        except Exception as exc:
            last_exc = exc
            if attempt < _FEED_RETRIES - 1 and not _circuit_open:
                time.sleep(_FEED_BACKOFF * (2 ** attempt) + random.uniform(0, 0.75))
    _circuit_record(False)
    logger.warning("[NEWS] feed fetch failed after %d tries for %s: %s",
                   _FEED_RETRIES, url, last_exc)
    return feedparser.parse(b"")


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
    min_score: int = 2,
    _use_cache: bool = True,
) -> list[dict]:
    """Return business-relevant article dicts for a company.

    A larger candidate pool is fetched, then passed through the relevance filter
    (heuristic always; AI gate when ai_filter and ai_key are set) so only news
    about real business events is stored. Returns at most ``max_articles``.
    """
    if _use_cache and company_name in _NEWS_CACHE:
        return _NEWS_CACHE[company_name]

    pool = max(max_articles * 3, 12)
    # Source priority: SerpAPI (if key) -> GDELT. GDELT is free and works from
    # CI/datacenter IPs; Google News RSS is blocked there, so it is NOT used in
    # this path anymore (kept only for local/dev callers of _rss_articles).
    if serpapi_key:
        articles = _serpapi_articles(company_name, serpapi_key, pool, max_age_days)
        if not articles:
            articles = _gdelt_articles(company_name, pool, max_age_days)
    else:
        articles = _gdelt_articles(company_name, pool, max_age_days)

    try:
        from .news_relevance import filter_relevant_articles
        articles = filter_relevant_articles(
            company_name, articles,
            ai_key=ai_key if ai_filter else "", model=ai_model, min_score=min_score,
        )
    except Exception as exc:  # fail-open: never lose news because the filter broke
        logger.warning("[NEWS] relevance filter unavailable for %s: %s", company_name, exc)

    return articles[:max_articles]


_GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"


def _fetch_json(url: str, timeout: int = 12, retries: int = 2):
    """GET a JSON URL with a hard timeout + light retry. Returns dict or None.

    Independent of the RSS circuit breaker: GDELT is reachable from CI even when
    Google News RSS is IP-blocked, so RSS failures must not disable GDELT.
    """
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _FEED_UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", "ignore"))
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(1.0 + random.uniform(0, 0.5))
    logger.warning("[NEWS] GDELT fetch failed for %s: %s", url, last)
    return None


def _gdelt_seendate_to_iso(seendate: str) -> str:
    """GDELT 'YYYYMMDDTHHMMSSZ' -> ISO 8601 'YYYY-MM-DDTHH:MM:SSZ' (parser-friendly)."""
    s = (seendate or "").strip()
    if len(s) >= 15 and s[8] == "T":
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:{s[13:15]}Z"
    return ""


def _gdelt_articles(
    company_name: str,
    max_articles: int = 5,
    max_age_days: int = MAX_NEWS_AGE_DAYS,
) -> list[dict]:
    """Fetch recent news for a company via the free GDELT DOC API (CI-friendly)."""
    days = min(max(int(max_age_days), 1), 365)
    q = urllib.parse.quote(f'"{company_name}"')
    url = (f"{_GDELT_DOC}?query={q}&mode=ArtList"
           f"&maxrecords={max(max_articles * 3, 10)}&sort=DateDesc"
           f"&format=json&timespan={days}days")
    data = _fetch_json(url)
    arts = data.get("articles", []) if isinstance(data, dict) else []
    results: list[dict] = []
    seen: set[str] = set()
    for a in arts:
        title = (a.get("title", "") or "").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        pub = _gdelt_seendate_to_iso(a.get("seendate", ""))
        if not _is_article_fresh(pub, max_age_days):
            continue
        seen.add(key)
        results.append({
            "title": title,
            "url": a.get("url", ""),
            "summary": "",
            "source": a.get("domain", ""),
            "published": pub,
        })
        if len(results) >= max_articles:
            break
    return results


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
        feed = _fetch_feed(url)
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
        feed = _fetch_feed(url)
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


def warm_news_cache(
    company_names,
    serpapi_key: str = "",
    ai_key: str = "",
    ai_filter: bool = False,
    ai_model: str = "gpt-4o-mini",
    min_score: int = 2,
    max_articles: int = 5,
    max_age_days: int = MAX_NEWS_AGE_DAYS,
    max_workers: int = 6,
) -> None:
    """Pre-fetch news for many companies in parallel into _NEWS_CACHE.

    Turns the previously sequential ~1,200 Google News RSS calls into a bounded
    thread pool. Each call is already hard-capped by _FEED_TIMEOUT, so the whole
    warm-up finishes in minutes instead of hours. Results are read instantly by
    get_news_articles() during the main company loop.
    """
    names = [n for n in dict.fromkeys(company_names) if n]  # dedupe, keep order
    if not names:
        return

    def _work(nm: str):
        try:
            arts = get_news_articles(
                nm, serpapi_key, max_articles=max_articles, max_age_days=max_age_days,
                ai_key=ai_key, ai_filter=ai_filter,
                ai_model=ai_model, min_score=min_score, _use_cache=False,
            )
        except Exception as exc:
            logger.warning("[NEWS] warm fetch failed for %s: %s", nm, exc)
            arts = []
        return nm, arts

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for nm, arts in ex.map(_work, names):
            _NEWS_CACHE[nm] = arts
