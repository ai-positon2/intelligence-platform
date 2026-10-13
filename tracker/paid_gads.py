"""Paid Marketing Agents: live Google Ads data.

Reads Position2's manager (MCC) account through the Google Ads REST API with
one shared sign-in, and hands the Performance Summarizer compact, ready-to-
chart rows. Read-only: only `googleAds:search` and the account list are ever
called, nothing here can change an ad account.

Environment (Railway):

  GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET   the OAuth client
  GOOGLE_ADS_DEVELOPER_TOKEN                        the API developer token
  GOOGLE_ADS_REFRESH_TOKEN                          one sign-in that can see the MCC
  GOOGLE_ADS_LOGIN_CUSTOMER_ID                      the manager account (default below)

The developer token has Explorer access, which allows roughly 2,880 requests
a day. Two things keep a busy team inside that: every result is cached in
Postgres (15 minutes for a range that includes today, 6 hours for one that
ended earlier), and a daily counter refuses new requests past DAILY_LIMIT
with a message instead of letting Google start rejecting them.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger(__name__)

API_VERSION = "v24"
API = "https://googleads.googleapis.com/" + API_VERSION
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_LOGIN = "9923720096"
DAILY_LIMIT = 2400            # Explorer allows ~2,880; keep a margin
MAX_RANGE_DAYS = 800
MAX_ROWS = 250_000
TTL_OPEN = 15 * 60            # a range that includes today
TTL_CLOSED = 6 * 3600         # a range that has ended
TTL_ACCOUNTS = 3600

ID_RE = re.compile(r"^\d{10}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOW = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


class AdsError(RuntimeError):
    """A Google Ads call failed; the message is safe to show staff."""


class NotConfigured(AdsError):
    pass


class OverQuota(AdsError):
    pass


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def login_customer():
    v = re.sub(r"\D", "", _env("GOOGLE_ADS_LOGIN_CUSTOMER_ID", DEFAULT_LOGIN))
    return v or DEFAULT_LOGIN


def config_problem():
    """None when the API can be called, else what is missing."""
    missing = [n for n in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
                           "GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_REFRESH_TOKEN")
               if not _env(n)]
    if missing:
        return "Google Ads is not connected yet: %s not set." % ", ".join(missing)
    return None


# ── Access token ────────────────────────────────────────────────────────────

_TOKEN = {"value": None, "exp": 0.0}
_TOKEN_LOCK = threading.Lock()


def _access_token(post=None):
    with _TOKEN_LOCK:
        if _TOKEN["value"] and _TOKEN["exp"] > time.time() + 60:
            return _TOKEN["value"]
        import requests
        post = post or requests.post
        try:
            r = post(TOKEN_URL, data={
                "client_id": _env("GOOGLE_ADS_CLIENT_ID"), "client_secret": _env("GOOGLE_ADS_CLIENT_SECRET"),
                "refresh_token": _env("GOOGLE_ADS_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=20)
        except Exception as e:  # network
            raise AdsError("Could not reach Google to sign in (%s)." % type(e).__name__) from e
        if r.status_code != 200:
            try:
                err = r.json().get("error", "")
            except ValueError:
                err = ""
            if err in ("invalid_grant", "invalid_client", "unauthorized_client"):
                raise AdsError("The Google Ads sign-in was rejected (%s). An admin needs to refresh "
                               "GOOGLE_ADS_REFRESH_TOKEN." % err)
            raise AdsError("Google would not sign in (HTTP %s)." % r.status_code)
        d = r.json()
        _TOKEN.update(value=d["access_token"], exp=time.time() + float(d.get("expires_in", 3000)))
        return _TOKEN["value"]


def reset_token_cache():
    with _TOKEN_LOCK:
        _TOKEN.update(value=None, exp=0.0)


# ── Calls ───────────────────────────────────────────────────────────────────

def _pacific_day():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:
        return dt.datetime.utcnow().date().isoformat()


def _count_call(store):
    """Add one request to today's tally; raise OverQuota past the limit.
    A store outage must not stop reads, so it is logged and skipped."""
    try:
        n = store.bump_gads_calls(_pacific_day())
    except Exception as e:
        log.warning("gads: could not count a request (%s)", e)
        return
    if n > DAILY_LIMIT:
        raise OverQuota("Today's Google Ads request budget (%d) is used up. It resets at midnight "
                        "Pacific time; cached views still work." % DAILY_LIMIT)


def _google_message(r):
    try:
        e = r.json()
        e = e[0] if isinstance(e, list) and e else e
        err = (e or {}).get("error", {})
        details = err.get("details") or []
        for d in details:
            for x in d.get("errors", []) or []:
                if x.get("message"):
                    return x["message"][:300]
        return (err.get("message") or "")[:300]
    except Exception:
        return ""


def _post(path, body, store, *, http=None):
    """One Google Ads request. Counted, retried once on a rate limit."""
    import requests
    http = http or requests.post
    for attempt in (0, 1):
        _count_call(store)
        headers = {"Authorization": "Bearer " + _access_token(),
                   "developer-token": _env("GOOGLE_ADS_DEVELOPER_TOKEN"),
                   "login-customer-id": login_customer(), "Content-Type": "application/json"}
        try:
            r = http(API + path, headers=headers, data=json.dumps(body), timeout=90)
        except Exception as e:
            raise AdsError("Could not reach Google Ads (%s)." % type(e).__name__) from e
        if r.status_code == 200:
            return r.json()
        if r.status_code == 401 and attempt == 0:
            reset_token_cache()
            continue
        if r.status_code in (429, 503) and attempt == 0:
            time.sleep(2.0)
            continue
        msg = _google_message(r)
        if r.status_code == 429:
            raise OverQuota("Google Ads is rate limiting this app right now. Try again in a minute.")
        raise AdsError("Google Ads answered %s%s" % (r.status_code, (": " + msg) if msg else "."))
    raise AdsError("Google Ads did not answer.")


def search(customer_id, gaql, store, *, http=None):
    """Every row of one GAQL query (follows pages)."""
    rows, token = [], None
    while True:
        body = {"query": gaql}          # v24 refuses a pageSize: pages are a fixed 10,000 rows
        if token:
            body["pageToken"] = token
        d = _post("/customers/%s/googleAds:search" % customer_id, body, store, http=http)
        rows.extend(d.get("results") or [])
        token = d.get("nextPageToken")
        if not token:
            return rows
        if len(rows) > MAX_ROWS:
            raise AdsError("That range is too large to chart (over %d rows). Pick a shorter range." % MAX_ROWS)


# ── Queries ─────────────────────────────────────────────────────────────────

def q_accounts():
    return ("SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code, "
            "customer_client.time_zone, customer_client.manager, customer_client.level, "
            "customer_client.status, customer_client.test_account FROM customer_client")


def q_campaign_days(start, end):
    return ("SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
            "campaign_budget.amount_micros, segments.date, metrics.impressions, metrics.clicks, "
            "metrics.cost_micros, metrics.conversions, metrics.conversions_value "
            "FROM campaign WHERE segments.date BETWEEN '%s' AND '%s'" % (start, end))


def q_campaign_sis(start, end):
    return ("SELECT campaign.id, metrics.search_impression_share FROM campaign "
            "WHERE segments.date BETWEEN '%s' AND '%s' AND campaign.advertising_channel_type = 'SEARCH'"
            % (start, end))


def q_account_sis(start, end):
    return ("SELECT metrics.search_impression_share FROM customer "
            "WHERE segments.date BETWEEN '%s' AND '%s'" % (start, end))


_M = "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value"


def q_device(start, end):
    return "SELECT segments.device, %s FROM customer WHERE segments.date BETWEEN '%s' AND '%s'" % (_M, start, end)


def q_network(start, end):
    return ("SELECT segments.ad_network_type, %s FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
            % (_M, start, end))


def q_dow_hour(start, end):
    return ("SELECT segments.day_of_week, segments.hour, %s FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
            % (_M, start, end))


# ── Parsing ─────────────────────────────────────────────────────────────────

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _base(m):
    """[impressions, clicks, cost, conversions, conversion value] from a metrics block."""
    m = m or {}
    return [int(_f(m.get("impressions"))), int(_f(m.get("clicks"))),
            round(_f(m.get("costMicros")) / 1e6, 4), round(_f(m.get("conversions")), 4),
            round(_f(m.get("conversionsValue")), 4)]


def parse_accounts(rows):
    """Enabled, non-manager, non-test client accounts under the MCC, name order."""
    out, seen = [], set()
    for r in rows:
        c = r.get("customerClient") or {}
        cid = str(c.get("id") or "")
        if not ID_RE.match(cid) or cid in seen or c.get("manager") or c.get("testAccount"):
            continue
        if c.get("status") != "ENABLED":
            continue
        seen.add(cid)
        out.append({"id": cid, "name": (c.get("descriptiveName") or "Account " + cid).strip(),
                    "currency": c.get("currencyCode") or "USD", "tz": c.get("timeZone") or "",
                    "level": int(c.get("level") or 1)})
    out.sort(key=lambda a: a["name"].lower())
    return out


def parse_campaign_days(rows, start, end):
    """The compact shape the page charts from.

    campaigns: [{id, name, type, status, budget}], days: ['YYYY-MM-DD', ...] every day
    in range (so a day with no spend still plots as zero), rows: [[campaign index,
    day index, impressions, clicks, cost, conversions, value], ...]."""
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    didx = {d: i for i, d in enumerate(days)}
    camps, cidx, out = [], {}, []
    for r in rows:
        c = r.get("campaign") or {}
        cid = str(c.get("id") or "")
        d = (r.get("segments") or {}).get("date")
        if not cid or d not in didx:
            continue
        if cid not in cidx:
            cidx[cid] = len(camps)
            camps.append({"id": cid, "name": c.get("name") or cid, "status": c.get("status") or "",
                          "type": c.get("advertisingChannelType") or "UNKNOWN",
                          "budget": round(_f((r.get("campaignBudget") or {}).get("amountMicros")) / 1e6, 2)})
        out.append([cidx[cid], didx[d]] + _base(r.get("metrics")))
    return {"start": start, "end": end, "days": days, "campaigns": camps, "rows": out}


def parse_sis(campaign_rows, account_rows):
    camp = {}
    for r in campaign_rows:
        v = (r.get("metrics") or {}).get("searchImpressionShare")
        cid = str((r.get("campaign") or {}).get("id") or "")
        if cid and v not in (None, ""):
            camp[cid] = round(_f(v), 4)
    acct = None
    for r in account_rows:
        v = (r.get("metrics") or {}).get("searchImpressionShare")
        if v not in (None, ""):
            acct = round(_f(v), 4)
    return {"account": acct, "campaigns": camp}


def parse_extras(device_rows, network_rows, dow_hour_rows):
    def seg(rows, key):
        acc = {}
        for r in rows:
            k = (r.get("segments") or {}).get(key) or "UNKNOWN"
            b = _base(r.get("metrics"))
            a = acc.setdefault(k, [0, 0, 0.0, 0.0, 0.0])
            for i in range(5):
                a[i] += b[i]
        return [[k] + [round(x, 4) if isinstance(x, float) else x for x in v] for k, v in sorted(acc.items())]
    heat = {}
    for r in dow_hour_rows:
        s = r.get("segments") or {}
        if s.get("dayOfWeek") not in DOW or s.get("hour") is None:
            continue
        key = (DOW.index(s["dayOfWeek"]), int(s["hour"]))
        b = _base(r.get("metrics"))
        a = heat.setdefault(key, [0, 0, 0.0, 0.0, 0.0])
        for i in range(5):
            a[i] += b[i]
    return {"device": seg(device_rows, "device"), "network": seg(network_rows, "adNetworkType"),
            "dowHour": [[d, h] + [round(x, 4) if isinstance(x, float) else x for x in v]
                        for (d, h), v in sorted(heat.items())]}


# ── Validation ──────────────────────────────────────────────────────────────

def check_range(start, end, label="range"):
    """(start, end) as date strings, or raises ValueError saying what is wrong."""
    if not (isinstance(start, str) and isinstance(end, str) and DATE_RE.match(start) and DATE_RE.match(end)):
        raise ValueError("%s should be two YYYY-MM-DD dates" % label)
    try:
        a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError:
        raise ValueError("%s has a date that does not exist" % label)
    if a > b:
        raise ValueError("%s starts after it ends" % label)
    if (b - a).days + 1 > MAX_RANGE_DAYS:
        raise ValueError("%s is longer than %d days" % (label, MAX_RANGE_DAYS))
    if b > dt.date.today() + dt.timedelta(days=1):
        raise ValueError("%s ends in the future" % label)
    return start, end


def _ttl(end):
    return TTL_OPEN if end >= (dt.date.today() - dt.timedelta(days=1)).isoformat() else TTL_CLOSED


# ── Cached fetches ──────────────────────────────────────────────────────────

def _par(fns):
    """Run independent reads at once (each is a network wait) and return their
    results in order; the first failure is raised."""
    if len(fns) == 1:
        return [fns[0]()]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        futs = [ex.submit(f) for f in fns]
        return [f.result() for f in futs]


def _cached(store, key, ttl, make):
    try:
        hit = store.get_gads_cache(key, ttl)
    except Exception as e:
        log.warning("gads: cache read failed (%s)", e)
        hit = None
    if hit is not None:
        return hit
    value = make()
    try:
        store.put_gads_cache(key, value)
    except Exception as e:
        log.warning("gads: cache write failed (%s)", e)
    return value


def list_accounts(store, *, http=None):
    def make():
        return parse_accounts(search(login_customer(), q_accounts(), store, http=http))
    return _cached(store, "accounts", TTL_ACCOUNTS, make)


def account_ids(store, *, http=None):
    return {a["id"] for a in list_accounts(store, http=http)}


def performance(store, customer_id, start, end, *, with_sis=True, http=None):
    """One range of campaign-by-day rows (and, for the main range, the period's
    search impression share)."""
    def make_days():
        return parse_campaign_days(search(customer_id, q_campaign_days(start, end), store, http=http), start, end)

    def days():
        return _cached(store, "days:%s:%s:%s" % (customer_id, start, end), _ttl(end), make_days)

    def make_sis():
        try:
            c, a = _par([lambda: search(customer_id, q_campaign_sis(start, end), store, http=http),
                         lambda: search(customer_id, q_account_sis(start, end), store, http=http)])
            return parse_sis(c, a)
        except OverQuota:
            raise
        except AdsError as e:       # share is a nicety; the range still charts without it
            log.warning("gads: impression share unavailable for %s (%s)", customer_id, e)
            return {"account": None, "campaigns": {}}

    def sis():
        return _cached(store, "sis:%s:%s:%s" % (customer_id, start, end), _ttl(end), make_sis)

    if not with_sis:
        return days()
    d, sh = _par([days, sis])
    return dict(d, sis=sh)


def performance_pair(store, customer_id, cur, cmp, *, with_sis=True, http=None):
    """The main range and (if given) its comparison range, fetched together."""
    jobs = [lambda: performance(store, customer_id, cur[0], cur[1], with_sis=with_sis, http=http)]
    if cmp:
        jobs.append(lambda: performance(store, customer_id, cmp[0], cmp[1], with_sis=False, http=http))
    res = _par(jobs)
    return res[0], (res[1] if cmp else None)


def extras(store, customer_id, start, end, *, http=None):
    def make():
        return parse_extras(*_par([lambda: search(customer_id, q_device(start, end), store, http=http),
                                   lambda: search(customer_id, q_network(start, end), store, http=http),
                                   lambda: search(customer_id, q_dow_hour(start, end), store, http=http)]))
    return _cached(store, "extras:%s:%s:%s" % (customer_id, start, end), _ttl(end), make)
