"""Market Radar, Phase 11: a real browser for sites that refuse this server.

Some companies' edges refuse a request from a datacenter address whatever
it looks like (ESAB, ABB, ServiceNow and Lincoln Electric all answered this
server 403 in Phase 10). Their pages are opened instead in a headless
Firefox on Apify (apify/website-content-crawler, the actor Event
Intelligence already uses), through Apify's proxy, and handed back shaped
like market_radar_site.fetch()'s result, so the site reader and every
detector read them as they read any page.

Measured 2026-10-10 on the six homepages that refused us: ESAB, ABB,
ServiceNow and Lincoln Electric came back whole within a minute; WEG served
an "Access Denied" page with HTTP 200 (so a wall is recognised by its words,
not its status), and Clove Dental hung until the run's time ran out. Hence
a short per-page timeout, no retries, and a run limit that keeps whatever
was loaded before it.

Cost: the actor is free; Apify charges compute units (1 GB of memory for an
hour) for the run. Every run is booked in the cost ledger at its worst case
(the run's memory for its whole time limit at the highest compute unit
price) before it starts, and the run's own usageTotalUsd replaces the
booking afterwards, as for searches (tracker/market_radar_search.py). A
collection opens at most PER_COLLECTION sites this way.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from decimal import Decimal

import requests

from . import market_radar_search as search

logger = logging.getLogger(__name__)

ACTOR = "apify/website-content-crawler"
MEMORY_MB = 4096
RUN_TIMEOUT_S = 120
PAGE_TIMEOUT_S = 30
MAX_PAGES = 5
# The highest compute unit price on Apify's plans (Free); a lower tier pays less.
CU_PRICE_MAX_USD = Decimal("0.40")
SETTLE_SECONDS = 3
# Each read is a paid browser run of up to two minutes: at most this many
# companies per collection, the highest-ranked first.
PER_COLLECTION = 3
MIN_WORDS = 40

# A page the browser was given instead of the site's own: a bot wall, a
# challenge, a consent wall. WEG answered "Access Denied" with HTTP 200.
WALL = re.compile(
    r"\b(access denied|just a moment|attention required|request unsuccessful|"
    r"pardon our interruption|are you a robot|verify you are (a )?human|"
    r"checking your browser|enable javascript and cookies|captcha|"
    r"you don't have permission to access|request blocked|bot detection)\b", re.I)


class Allowance:
    """How many sites a collection may still open in the browser, shared by
    its threads."""

    def __init__(self, n=None):
        self.left, self._lock = PER_COLLECTION if n is None else n, threading.Lock()

    def take(self):
        with self._lock:
            if self.left <= 0:
                return False
            self.left -= 1
            return True


def booking_usd():
    """The most one run can cost: its memory for its whole time limit (plus
    the start-up Apify does not cut off) at the highest price."""
    hours = Decimal(RUN_TIMEOUT_S + 30) / 3600
    return (Decimal(MEMORY_MB) / 1024 * hours * CU_PRICE_MAX_USD).quantize(Decimal("0.0001"))


def build_input(urls):
    n = len(urls)
    return {
        "startUrls": [{"url": u} for u in urls],
        "maxCrawlDepth": 0, "maxCrawlPages": n, "maxResults": n,
        "crawlerType": "playwright:firefox",
        "initialConcurrency": n, "maxConcurrency": n,
        "maxRequestRetries": 0, "requestTimeoutSecs": PAGE_TIMEOUT_S,
        "removeCookieWarnings": True, "blockMedia": True, "maxScrollHeightPixels": 2000,
        # Nothing clicked, nothing removed, nothing rewritten: the reader
        # needs the page's own markup (its links, its JSON-LD, its footer's
        # social links), which the actor's defaults strip out.
        "clickElementsCssSelector": "",
        "removeElementsCssSelector": "dummy_keep_everything",
        "htmlTransformer": "none",
        "saveHtml": True, "saveMarkdown": False,
        "proxyConfiguration": {"useApifyProxy": True},
    }


def _key(u):
    return re.sub(r"^https?://(www\.)?", "", str(u or "").strip().lower()).rstrip("/")


def page_from_item(item):
    """(fetch-shaped page, None) for a real page, or (None, reason)."""
    from . import market_radar_site as site
    crawl = item.get("crawl") if isinstance(item.get("crawl"), dict) else {}
    code = crawl.get("httpStatusCode")
    if code and code >= 400:
        return None, "the site refused the browser too (HTTP %s)" % code
    html = str(item.get("html") or "")
    final = crawl.get("loadedUrl") or item.get("url")
    text = site.html_to_linked_text(html, final) if html else str(item.get("text") or "")
    title = str((item.get("metadata") or {}).get("title") or "")
    head = title + "\n" + " ".join(text.split()[:80])
    if WALL.search(head):
        return None, "the site showed the browser a block page (%s)" % (title or "no title")[:60]
    if len(text.split()) < MIN_WORDS:
        return None, "the browser saw only %d words" % len(text.split())
    return {"url": item.get("url"), "final_url": final, "status": "ok", "http_status": code or 200,
            "html": html, "text": text, "truncated": False, "via": "browser",
            "note": "read in a real browser: the site refuses this server"}, None


def open_pages(urls, *, token=None, run_id=None, stage="browser", api=None, sleep=time.sleep):
    """Open `urls` (at most MAX_PAGES) in one browser run. Returns {"pages":
    {asked url: page}, "missed": {asked url: reason}, "error", "status",
    "charged_usd", "booked_usd"}. Never raises; the ledger records the run."""
    urls = list(dict.fromkeys(u for u in urls if u))[:MAX_PAGES]
    out = {"pages": {}, "missed": {}, "error": None, "status": None, "charged_usd": None,
           "booked_usd": float(booking_usd()), "apify_run_id": None, "start": None}
    if not urls:
        return out
    token = token if token is not None else os.environ.get("APIFY_API_TOKEN", "")
    if not token and api is None:
        out["error"] = "APIFY_API_TOKEN is not set"
        return out
    api = api or search.ApifyApi(token)

    def go():
        try:
            run = api.start(ACTOR, build_input(urls),
                            {"timeout": RUN_TIMEOUT_S, "memory": MEMORY_MB})
        except requests.HTTPError as e:
            out["error"], out["start"] = "Apify refused the run: %s" % str(e)[:300], "refused"
            return
        except Exception as e:
            out["error"] = "starting the run failed: %s: %s" % (type(e).__name__, str(e)[:300])
            out["start"] = "unknown"
            return
        rid = out["apify_run_id"] = run.get("id")
        out["start"] = "started"
        items = []
        try:
            run = search._poll(api, rid, RUN_TIMEOUT_S + 60, sleep)
            out["status"] = run.get("status")
            # A run cut off at its limit keeps every page it had loaded.
            if run.get("defaultDatasetId"):
                items = search._retrying(lambda: api.items(run["defaultDatasetId"]), sleep)
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
            try:
                api.abort(rid)
            except Exception:
                pass
        wanted = {_key(u): u for u in urls}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            crawl = item.get("crawl") if isinstance(item.get("crawl"), dict) else {}
            asked = next((wanted[_key(c)] for c in (item.get("url"), crawl.get("loadedUrl"))
                          if _key(c) in wanted), None)
            if not asked or asked in out["pages"]:
                continue
            page, why = page_from_item(item)
            if page:
                out["pages"][asked] = dict(page, url=asked)
                out["missed"].pop(asked, None)
            else:
                out["missed"][asked] = why
        for u in urls:
            if u not in out["pages"] and u not in out["missed"]:
                out["missed"][u] = ("the browser could not load it in time"
                                    if out["status"] in ("TIMED-OUT", "SUCCEEDED", None)
                                    else "the browser run ended %s" % out["status"])
        try:
            sleep(SETTLE_SECONDS)
            settled = search._retrying(lambda: api.run(rid), sleep)
            out["charged_usd"] = settled.get("usageTotalUsd")
        except Exception as e:
            out["error"] = out["error"] or "could not read the run's charge: %s" % str(e)[:200]

    if run_id is None:
        go()
        return out
    from . import market_radar_ledger as ledger
    with ledger.track(run_id, stage, "apify", reserved_usd=booking_usd()) as call:
        go()
        if out["charged_usd"] is not None:
            call.record(actual_usd=out["charged_usd"], units=len(out["pages"]), error=out["error"])
        elif out.get("start") == "refused":
            call.record(error=out["error"], billed=False)
        else:
            call.record(error=out["error"] or "Apify reported no charge for this run")
    return out


def for_run(run_id, allowance=None, *, token=None, opener=None):
    """The `browser` the site reader takes, bound to a run and an allowance:
    browser(urls) -> open_pages()'s result, or None when no browser may be
    used (no Apify token, or the collection's allowance is spent). None when
    no token is set at all."""
    token = token if token is not None else os.environ.get("APIFY_API_TOKEN", "")
    if not token and opener is None:
        return None
    opener = opener or (lambda urls: open_pages(urls, token=token, run_id=run_id))
    first = {"taken": False}
    lock = threading.Lock()

    def browser(urls, more=False):
        # The allowance counts companies, not runs: a company's other
        # pages (`more`) are part of the same read. One `browser` per
        # company.
        with lock:
            if not first["taken"]:
                if allowance is not None and not allowance.take():
                    return None
                first["taken"] = True
        try:
            return opener(urls)
        except Exception as e:      # open_pages never raises; a test opener might
            logger.warning("market_radar_browser: opening %s broke: %s", urls[:1], e)
            return {"pages": {}, "missed": {u: "the browser broke" for u in urls},
                    "error": str(e)[:200]}
    return browser
