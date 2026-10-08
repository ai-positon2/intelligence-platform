"""Market Radar: Google web search through the platform's existing Apify
account (apify/google-search-scraper). No new search vendor, account or card.

Every search goes through the cost ledger when given a run:

  * the call is booked at its worst case (booking_usd) before it starts,
    against the run's cap (tracker/market_radar_ledger.py);
  * Apify is given a spending cap of its own (maxTotalChargeUsd), so the
    vendor stops charging too, not just our bookkeeping (see below for why
    the two limits differ);
  * afterwards the run's own `usageTotalUsd` (what the account actually
    pays, per Apify's API docs) replaces the booking. A run that reports no
    charge stays counted at its booking, never zero.

Pricing (Apify Store, checked 2026-10-08): pay per event, $0.0025 per results
page on the Bronze/Starter tier ($0.0045 on Free), plus an "Actor Start"
event of $0.00005 per GB of memory. One page holds about 10 results. The
actor saves each page's HTML to a key-value store by default; that is turned
off here, along with every paid add-on.

Two different limits, on purpose:

  * the LEDGER BOOKING is the real worst case for this many queries (every
    page at the highest tier's price, plus start events): about half a cent
    for one query. That is what counts against the run's $1.00 cap;
  * APIFY'S OWN CAP (maxTotalChargeUsd) is only a backstop, and Apify
    refuses (HTTP 400) any value below the actor's minimalMaxTotalChargeUsd,
    which is $0.50 for this actor. The first live test sent $0.05 and was
    refused for exactly that (2026-10-09, nothing charged). Booking $0.50 in
    the ledger instead would let two searches use up a whole run's budget.
"""
from __future__ import annotations

import time
from decimal import Decimal
from urllib.parse import urlsplit

import requests

from . import apify_transport

ACTOR = "apify/google-search-scraper"
MAX_QUERIES_PER_CALL = 50
MAX_QUERY_WORDS = 32                  # Google's own limit, per the actor's docs
APIFY_MIN_MAX_CHARGE_USD = Decimal("0.50")    # the actor's minimalMaxTotalChargeUsd
PRICE_PER_PAGE_MAX_USD = Decimal("0.0045")     # the highest tier's price (Free)
START_EVENTS_USD = Decimal("0.0002")           # 4 x $0.00005; the default run uses 1 GB
SETTLE_SECONDS = 3                    # re-read the run after it ends: the charge can land late


class SearchError(RuntimeError):
    pass


class ApifyApi:
    """The three Apify calls a search needs. Tests pass a fake instead."""

    def __init__(self, token):
        self.token = token
        self.base = apify_transport._BASE_URL

    def _h(self):
        return apify_transport._headers(self.token)

    def start(self, actor, run_input, params):
        url = "%s/acts/%s/runs" % (self.base, apify_transport._normalize_actor_id(actor))
        resp = requests.post(url, json=run_input, params=params, headers=self._h(), timeout=30)
        if resp.status_code >= 400:
            # Keep Apify's own explanation: raise_for_status() alone gave only
            # "400 Bad Request for url: ...", which hid why the run was refused.
            raise requests.HTTPError("HTTP %s: %s" % (resp.status_code, (resp.text or "")[:400]),
                                     response=resp)
        return resp.json()["data"]

    def run(self, run_id):
        resp = requests.get("%s/actor-runs/%s" % (self.base, run_id), headers=self._h(), timeout=30)
        resp.raise_for_status()
        return resp.json()["data"]

    def items(self, dataset_id):
        return apify_transport._fetch_dataset_items(dataset_id, self.token)

    def abort(self, run_id):
        requests.post("%s/actor-runs/%s/abort" % (self.base, run_id), headers=self._h(), timeout=30)


def build_input(queries, country="us", search_language=None):
    run_input = {
        "queries": "\n".join(queries),
        "maxPagesPerQuery": 1,
        "countryCode": country,
        "mobileResults": False,
        "includeUnfilteredResults": False,
        "saveHtml": False,
        "saveHtmlToKeyValueStore": False,   # the actor's default is True
        "focusOnPaidAds": False,
        "maximumLeadsEnrichmentRecords": 0,
    }
    if search_language:
        run_input["searchLanguage"] = search_language
    return run_input


def check_queries(queries):
    clean = [q.strip() for q in queries or [] if q and q.strip()]
    if not clean:
        raise ValueError("no queries given")
    if len(clean) > MAX_QUERIES_PER_CALL:
        raise ValueError("at most %d queries per call" % MAX_QUERIES_PER_CALL)
    too_long = [q for q in clean if len(q.split()) > MAX_QUERY_WORDS]
    if too_long:
        raise ValueError("queries over %d words: %r" % (MAX_QUERY_WORDS, too_long[:3]))
    return clean


def organic_results(pages):
    """One row per organic result: query, position, title, url, domain, snippet."""
    rows = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        query = (page.get("searchQuery") or {}).get("term")
        for r in page.get("organicResults") or []:
            url = r.get("url") or ""
            host = (urlsplit(url).hostname or "").lower()
            rows.append({"query": query, "position": r.get("position"), "title": r.get("title"),
                         "url": url, "domain": host[4:] if host.startswith("www.") else host,
                         "snippet": r.get("description")})
    return rows


def booking_usd(n_queries):
    """The most a search of n queries (one page each) can cost."""
    return n_queries * PRICE_PER_PAGE_MAX_USD + START_EVENTS_USD


def search(queries, *, token, country="us", search_language=None, run_id=None,
           stage="search", apify_cap_usd=APIFY_MIN_MAX_CHARGE_USD, timeout=180, api=None,
           sleep=time.sleep):
    """Run the queries (one results page each) and return what came back, what
    it cost, and what the pages contained. Never raises on a vendor failure:
    the error is in the result, and the ledger still records the charge."""
    queries = check_queries(queries)
    if not token:
        raise SearchError("APIFY_API_TOKEN is not set")
    api = api or ApifyApi(token)
    booked = booking_usd(len(queries))
    apify_cap_usd = max(Decimal(str(apify_cap_usd)), APIFY_MIN_MAX_CHARGE_USD, booked)
    started = time.monotonic()
    out = {"queries": queries, "status": None, "error": None, "apify_run_id": None,
           "charged_usd": None, "charged_events": None, "booked_usd": float(booked),
           "apify_cap_usd": float(apify_cap_usd),
           "pages": 0, "results": [], "fields_seen": [], "elapsed_ms": None, "start": None}

    def go():
        try:
            run = api.start(ACTOR, build_input(queries, country, search_language),
                            {"timeout": int(timeout), "maxTotalChargeUsd": str(apify_cap_usd)})
        except requests.HTTPError as e:
            # Apify answered and refused: no run exists, nothing was charged.
            out["error"] = "Apify refused the run: %s" % str(e)[:300]
            out["start"] = "refused"
            return
        except Exception as e:
            # No answer (timeout, dropped connection): a run may exist.
            out["error"] = "starting the run failed: %s: %s" % (type(e).__name__, str(e)[:300])
            out["start"] = "unknown"
            return
        rid = out["apify_run_id"] = run.get("id")
        out["start"] = "started"
        try:
            run = _poll(api, rid, timeout + 30, sleep)
            out["status"] = run.get("status")
            if run.get("defaultDatasetId"):
                pages = api.items(run["defaultDatasetId"])
                out["pages"] = len(pages)
                out["results"] = organic_results(pages)
                out["fields_seen"] = sorted({k for p in pages if isinstance(p, dict) for k in p})
            if out["status"] != "SUCCEEDED":
                out["error"] = "Apify run ended %s" % out["status"]
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
            try:
                api.abort(rid)        # a run we stopped waiting for must not keep charging
            except Exception:
                pass
        try:
            sleep(SETTLE_SECONDS)
            settled = api.run(rid)
            out["charged_usd"] = settled.get("usageTotalUsd")
            out["charged_events"] = settled.get("chargedEventCounts")
        except Exception as e:
            out["error"] = out["error"] or "could not read the run's charge: %s" % str(e)[:200]

    def timed():
        go()
        out["elapsed_ms"] = int((time.monotonic() - started) * 1000)

    if run_id is None:
        timed()
        return out

    from . import market_radar_ledger as ledger
    with ledger.track(run_id, stage, "apify", reserved_usd=booked) as call:
        timed()
        if out["charged_usd"] is not None:
            call.record(actual_usd=out["charged_usd"], units=out["pages"], error=out["error"])
        elif out.get("start") == "refused":
            call.record(error=out["error"], billed=False)
        else:
            call.record(error=out["error"] or "Apify reported no charge for this run")
    return out


def _poll(api, run_id, timeout, sleep):
    deadline = time.monotonic() + timeout
    while True:
        run = api.run(run_id)
        if run.get("status") in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return run
        if time.monotonic() >= deadline:
            raise SearchError("Apify run %s did not finish within %ss" % (run_id, timeout))
        sleep(3)
