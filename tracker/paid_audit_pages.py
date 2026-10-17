"""Paid Marketing Agents: Account Audit, do the landing pages answer.

Fetches the distinct final URLs the account's live ads send people to (the busiest first) and records whether each answered, with
what status, how long it took and where it ended up after redirects. Read only: one plain GET per page, the body is never read.
Fetching goes through tracker/event_intel_http.public_get, which only reaches public addresses on ports 80 and 443 and re-checks every
redirect, so a final URL that points inside our network is refused rather than fetched.

Some sites refuse automated requests with 401, 403 or 429 while serving real visitors and Google's own crawler normally. Those pages are
marked `blocked`, not broken: the audit says it could not verify them instead of calling them down.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

MAX_CHECK = 25                                # pages fetched per audit
TIMEOUT_S = 8
WORKERS = 5
BLOCKED = {401, 403, 429}
UA = "Mozilla/5.0 (compatible; Position2AccountAudit/1.0; +https://intelligence.position2.com)"
TIMED_OUT = "timed out"
_TEMPLATE = re.compile(r"\{[^{}]*\}")


def clean(url):
    """The URL as a visitor would reach it: {ValueTrack} and {_custom} placeholders filled with a harmless value, fragment dropped."""
    u = _TEMPLATE.sub("x", str(url or "").strip())
    parts = urlsplit(u)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def page_key(url):
    """What makes a landing page distinct: host and path, not the query (the same page with different parameters is one page)."""
    parts = urlsplit(clean(url))
    return (parts.netloc.lower(), parts.path.rstrip("/") or "/")


def pick(url_rows, limit=MAX_CHECK):
    """From the read's [{url, ads}] (busiest first): one URL per distinct page, up to `limit`. Returns (urls, distinct page count).
    Only http and https addresses are kept."""
    seen, picked = set(), []
    for row in url_rows:
        u = clean(row["url"])
        if urlsplit(u).scheme not in ("http", "https") or not urlsplit(u).netloc:
            continue
        k = page_key(u)
        if k in seen:
            continue
        seen.add(k)
        picked.append(u)
    return picked[:limit], len(seen)


def check_one(url, fetcher=None):
    """{url, status, error, blocked, ms, finalUrl, redirected, retried}. Never raises. A request that times out is made once more (a slow page
    on a busy minute is not a broken one); the time recorded is the last attempt's."""
    if fetcher is None:
        from .event_intel_http import public_get as fetcher
    out = {"url": url, "status": 0, "error": "", "blocked": False, "ms": 0, "finalUrl": "", "redirected": False, "retried": False}
    for attempt in (0, 1):
        out.update(status=0, error="", finalUrl="")
        t0 = time.monotonic()
        try:
            resp = fetcher(url, timeout=TIMEOUT_S, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"})
            try:
                out["status"] = int(resp.status_code)
                out["finalUrl"] = str(getattr(resp, "url", "") or url)
            finally:
                resp.close()
        except Exception as e:                  # a private address, DNS, a timeout, TLS
            out["error"] = _say(e)
        out["ms"] = int((time.monotonic() - t0) * 1000)
        if out["error"] == TIMED_OUT and attempt == 0:
            out["retried"] = True
            continue
        break
    out["blocked"] = out["status"] in BLOCKED
    out["redirected"] = bool(out["finalUrl"]) and page_key(out["finalUrl"]) != page_key(url)
    return out


def _say(e):
    name = type(e).__name__
    text = str(e)
    if isinstance(e, ValueError):
        return "refused (%s)" % text[:80]
    if "timed out" in text.lower() or "timeout" in name.lower():
        return TIMED_OUT
    if "name or service" in text.lower() or "resolve" in text.lower() or "getaddrinfo" in text.lower():
        return "the address does not resolve"
    return "%s" % name


def check(url_rows, fetcher=None, workers=WORKERS, limit=MAX_CHECK):
    """{checked, of, results}: the pages checked (the busiest `limit`) out of every distinct page the ads use."""
    urls, distinct = pick(url_rows, limit)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = list(ex.map(lambda u: check_one(u, fetcher), urls))
    return {"checked": len(results), "of": distinct, "results": results}
