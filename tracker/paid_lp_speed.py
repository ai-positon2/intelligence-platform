"""Paid Marketing Agents: Landing Page & Relevancy Check, the lab speed test.

Google's PageSpeed Insights loads a page like a mid-range phone on a mobile connection (Lighthouse) and times what a visitor sees and when the page becomes
usable. It is the one measure of speed in this agent that does not come from our servers' plain read of the HTML, so it is run only when someone asks, one page at a
time (a test takes up to half a minute), for a page that is in the account's own report, and it needs a key: PAGESPEED_API_KEY, a Google Cloud API key with the
PageSpeed Insights API enabled. Without the key the page says the test is not connected and the speed checks go on without it; nothing is invented.

What comes back for a page: the Lighthouse performance score (0 to 100), the time the largest content was shown (LCP), the time the page was blocked (TBT), layout shift
(CLS), the first paint, the biggest savings Lighthouse names, and, where Google has field data from real visitors (Chrome UX Report), what those visitors saw. A result
is kept three days per page in the shared cache (tracker/paid_store.py), all of an account's in one entry.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading

import requests

log = logging.getLogger(__name__)

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
TIMEOUT_S = 75
KEEP_S = 3 * 24 * 3600                              # how long a result is shown
MAX_SAVINGS = 5
MIN_SAVING_MS = 300                                 # a saving smaller than this is not worth naming
FIELD_KEYS = (("LARGEST_CONTENTFUL_PAINT_MS", "lcpMs"), ("INTERACTION_TO_NEXT_PAINT", "inpMs"), ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "cls"), ("FIRST_CONTENTFUL_PAINT_MS", "fcpMs"))
_LOCK = threading.Lock()


class LabError(RuntimeError):
    """kind: not_configured, quota, page (Google could not load the page), api."""

    def __init__(self, kind, detail):
        self.kind, self.detail = kind, detail
        super().__init__(detail)


def api_key():
    return (os.environ.get("PAGESPEED_API_KEY") or "").strip()


def configured():
    return bool(api_key())


def config_problem():
    return None if configured() else ("The lab speed test is not connected. An admin sets PAGESPEED_API_KEY (a Google Cloud API key with the PageSpeed Insights API enabled) "
                                      "on the server.")


def _num(v, digits=0):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return round(v, digits) if digits else int(round(v))


def parse(body):
    """The result of one test from PageSpeed Insights' answer: {score, lcpMs, tbtMs, cls, fcpMs, speedIndexMs, savings: [{title, ms}], field}. `score` is None when
    Lighthouse could not score the page. A malformed answer is a LabError."""
    if not isinstance(body, dict):
        raise LabError("api", "PageSpeed Insights gave an answer that could not be read")
    lh = body.get("lighthouseResult")
    if not isinstance(lh, dict):
        raise LabError("api", "PageSpeed Insights gave no test result")
    perf = ((lh.get("categories") or {}).get("performance") or {}).get("score")
    audits = lh.get("audits") or {}

    def audit(key, digits=0):
        return _num((audits.get(key) or {}).get("numericValue"), digits)
    savings = []
    for a in audits.values():
        d = (a or {}).get("details") or {}
        ms = d.get("overallSavingsMs")
        if d.get("type") == "opportunity" and isinstance(ms, (int, float)) and ms >= MIN_SAVING_MS and a.get("title"):
            savings.append({"title": str(a["title"])[:120], "ms": int(round(ms))})
    savings.sort(key=lambda s: (-s["ms"], s["title"]))
    return {"score": None if not isinstance(perf, (int, float)) else int(round(perf * 100)), "lcpMs": audit("largest-contentful-paint"), "tbtMs": audit("total-blocking-time"),
            "cls": audit("cumulative-layout-shift", 3), "fcpMs": audit("first-contentful-paint"), "speedIndexMs": audit("speed-index"), "savings": savings[:MAX_SAVINGS],
            "field": _field(body.get("loadingExperience"))}


def _field(exp):
    """What real visitors saw (Chrome UX Report), or None when Google has too little data for the page: {category, lcpMs, inpMs, cls, fcpMs}."""
    if not isinstance(exp, dict) or not isinstance(exp.get("metrics"), dict) or not exp["metrics"]:
        return None
    out = {"category": str(exp.get("overall_category") or "").lower() or None}
    for key, name in FIELD_KEYS:
        m = exp["metrics"].get(key) or {}
        v = _num(m.get("percentile"))
        out[name] = None if v is None else (round(v / 100.0, 2) if name == "cls" else v)         # Google gives layout shift multiplied by 100
    return out


def run(url, *, get=None, key=None):
    """One lab test of `url` on a mobile profile. Raises LabError. `get` is the HTTP call (requests.get unless a test gives one)."""
    key = key if key is not None else api_key()
    if not key:
        raise LabError("not_configured", config_problem())
    get = get or requests.get
    try:
        resp = get(ENDPOINT, params={"url": url, "strategy": "mobile", "category": "performance", "key": key}, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise LabError("api", "PageSpeed Insights could not be reached (%s)" % type(e).__name__) from e
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code != 200:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        message = str((err or {}).get("message") or "")[:200]
        if resp.status_code == 429:
            raise LabError("quota", "PageSpeed Insights is limiting how fast it can be asked. Try again in a minute.")
        if resp.status_code == 400 and ("FAILED_DOCUMENT_REQUEST" in message or "Lighthouse returned error" in message or "ERRORED_DOCUMENT_REQUEST" in message):
            raise LabError("page", "Google's test could not load the page (%s)" % (message or "no reason given"))
        if resp.status_code in (400, 403) and ("API key" in message or "not been used" in message or "disabled" in message or "PERMISSION" in message.upper()):
            raise LabError("not_configured", "The PageSpeed Insights key was refused (%s)" % message)
        raise LabError("api", "PageSpeed Insights answered %d%s" % (resp.status_code, (": " + message) if message else ""))
    return parse(body)


# ── Kept results ────────────────────────────────────────────────────────────

def cache_key(account_id):
    return "lplab1:%s" % account_id


def _age_s(iso, now):
    try:
        return (now - dt.datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return KEEP_S + 1


def saved(store, account_id, now=None):
    """{page id: result} for the account's pages, the ones from the last three days. Never raises: a store that fails is no results."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        data = store.get_gads_cache_any(cache_key(account_id))
    except Exception as e:                                  # noqa: BLE001
        log.warning("landing page lab: could not read saved results (%s)", e)
        return {}
    entries = (data[0] if data else None) or {}
    return {pid: r for pid, r in entries.items() if isinstance(r, dict) and _age_s(r.get("ranAt"), now) <= KEEP_S}


def remember(store, account_id, page_id, result, now=None):
    """Keeps `result` for the page (with when it ran), alongside the account's other results, dropping the ones that have aged out."""
    now = now or dt.datetime.now(dt.timezone.utc)
    with _LOCK:
        entries = saved(store, account_id, now)
        entries[page_id] = dict(result, ranAt=now.isoformat())
        try:
            store.put_gads_cache(cache_key(account_id), entries)
        except Exception as e:                              # noqa: BLE001 (a result that could not be kept is still shown this once)
            log.warning("landing page lab: could not save the result (%s)", e)
    return entries[page_id]
