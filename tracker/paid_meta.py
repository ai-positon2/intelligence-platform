"""Paid Marketing Agents: live Meta (Facebook and Instagram) Ads data.

Reads Position2's ad accounts through the Meta Marketing API with one shared
access token and hands the Paid Marketing pages the same compact rows the
Google Ads side does, so the Performance Summarizer's charts, tiles and
break-downs work on a Meta account without knowing which platform it is. Read-
only: only GET requests are ever sent (the account list, campaigns, insights),
nothing here can change an ad account.

Environment (Railway):

  META_ADS_ACCESS_TOKEN   a System User token with ads_read (the one that never expires)
  META_BUSINESS_ID        optional: also list the ad accounts this business owns or is a client of
  META_API_VERSION        optional, like "v23.0"

The token is sent in an Authorization header, never in a URL, and is never
logged or returned to a page.

How Meta's numbers become the page's columns (the same 17 the Google side
sends, in the same order, see tracker/paid_gads.py BASE):

  impressions   impressions
  clicks        LINK clicks (inline_link_clicks), the click Ads Manager calls "Link clicks"
  cost          spend, in the account's currency
  conversions   the campaign objective's results: purchases for Sales, leads for Leads,
                app installs for App promotion; none for Traffic, Awareness, Engagement
  value         purchase value (Sales campaigns)
  all conv.     every conversion event (purchase, lead, registration, subscribe, trial,
                application, schedule, contact, donate), whatever the objective
  interactions  post engagement, video views = 3-second video plays
  not in Meta   view-through, engagements, cross-device, invalid clicks, phone calls,
                viewability: sent as missing so the page says so instead of showing 0

Reach and frequency cannot be added over days, so they are read once per range
for the whole account (the way Google's impression-share numbers are).

Storage and refresh are the Google side's: days are kept one at a time in
Postgres (pm_gads_days, under the account id "act_..."), every other result is
saved whole (pm_gads_cache), a minute is held in memory, a stale copy is served
with its date when Meta cannot answer, and Refresh asks Meta again at most
every REFRESH_COOLDOWN_S per account. Meta limits each ad account by its own
usage measure; when it says to wait, no request is sent until that passes.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time

from . import paid_gads as g
from .paid_gads import AdsError, OverQuota, RefreshTooSoon, NotConfigured  # noqa: F401  (the page handles one set of errors)

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/"
DEFAULT_VERSION = "v23.0"
ACT_RE = re.compile(r"^act_\d{4,20}$")
CHUNK_DAYS = 31                  # one insights query covers at most this many days, so no answer is huge
PAGE_LIMIT = 500
MAX_ROWS = 250_000
TTL_ACCOUNTS = 3600
TTL_CAMPAIGNS = 3600
WAIT_BLOCK_S = 300               # a wait longer than this is "come back later", not a hiccup
NB = g.NB

# Meta has no such numbers; the page is told so instead of being shown zeros.
MISSING = [7, 9, 11, 12, 13, 14, 15, 16]
OBJECTIVE_LABEL = {
    "OUTCOME_SALES": "SALES", "OUTCOME_LEADS": "LEADS", "OUTCOME_TRAFFIC": "TRAFFIC", "OUTCOME_AWARENESS": "AWARENESS",
    "OUTCOME_ENGAGEMENT": "ENGAGEMENT", "OUTCOME_APP_PROMOTION": "APP"}
# Campaigns made before the "outcome" objectives existed.
LEGACY = {"CONVERSIONS": "SALES", "PRODUCT_CATALOG_SALES": "SALES", "LEAD_GENERATION": "LEADS", "LINK_CLICKS": "TRAFFIC",
          "BRAND_AWARENESS": "AWARENESS", "REACH": "AWARENESS", "POST_ENGAGEMENT": "ENGAGEMENT", "PAGE_LIKES": "ENGAGEMENT",
          "VIDEO_VIEWS": "ENGAGEMENT", "EVENT_RESPONSES": "ENGAGEMENT", "MESSAGES": "ENGAGEMENT", "APP_INSTALLS": "APP",
          "STORE_VISITS": "AWARENESS", "OUTCOME_ENGAGEMENT": "ENGAGEMENT"}
STATUS = {"ACTIVE": "ENABLED", "PAUSED": "PAUSED", "CAMPAIGN_PAUSED": "PAUSED", "ADSET_PAUSED": "PAUSED",
          "IN_PROCESS": "ENABLED", "WITH_ISSUES": "ENABLED", "PENDING_REVIEW": "ENABLED", "DISAPPROVED": "PAUSED",
          "PENDING_BILLING_INFO": "PAUSED", "ARCHIVED": "REMOVED", "DELETED": "REMOVED"}
ACCOUNT_STATUS = {1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_REVIEW", 8: "IN_GRACE_PERIOD", 9: "IN_GRACE_PERIOD",
                  100: "PENDING_CLOSURE", 101: "CLOSED", 201: "ANY_ACTIVE", 202: "ANY_CLOSED"}
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "PYG", "XAF", "XOF", "XPF", "RWF", "KMF", "GNF", "DJF", "BIF", "VUV"}
# The conversion events counted as "all conversions" (each is read from its own first-present action type, so the
# same event is never counted twice under two names).
ALL_EVENTS = [("omni_purchase", "purchase"), ("lead", "onsite_conversion.lead_grouped"), ("omni_complete_registration", "complete_registration"),
              ("subscribe",), ("start_trial",), ("submit_application",), ("schedule",), ("contact",), ("donate",)]
PURCHASE = ("omni_purchase", "purchase")
LEAD = ("lead", "onsite_conversion.lead_grouped")
INSTALL = ("omni_app_install", "mobile_app_install", "app_install")

INSIGHT_FIELDS = ["campaign_id", "campaign_name", "objective", "spend", "impressions", "inline_link_clicks",
                  "inline_post_engagement", "actions", "action_values", "video_play_actions"]


# ── Configuration ───────────────────────────────────────────────────────────

def token():
    return g._env("META_ADS_ACCESS_TOKEN")


def version():
    v = g._env("META_API_VERSION", DEFAULT_VERSION)
    return v if re.match(r"^v\d+\.\d+$", v) else DEFAULT_VERSION


def business_id():
    return re.sub(r"\D", "", g._env("META_BUSINESS_ID"))


def config_problem():
    """None when the API can be called, else what is missing."""
    if not token():
        return "Meta Ads is not connected yet: META_ADS_ACCESS_TOKEN is not set on the server."
    return None


def is_meta(account_id):
    return bool(ACT_RE.match(str(account_id or "")))


def norm_id(value):
    """act_<digits> from "act_123", "123" or " ACT_123 ", else ''."""
    v = str(value or "").strip().lower()
    if v.startswith("act_"):
        v = v[4:]
    return "act_" + v if re.fullmatch(r"\d{4,20}", v) else ""


# ── The request ─────────────────────────────────────────────────────────────

_BLOCK_KEY = "rl:meta-wait"
_BLOCK = {"until": 0.0, "checked": 0.0}


def reset_state():
    """For tests: forget what was learned about Meta's rate limits."""
    _BLOCK.update(until=0.0, checked=0.0)
    g.forget(g.CACHE_V + "meta:")


def _wait_message(until):
    mins = max(1, int((until - time.time()) / 60 + 0.5))
    return ("Meta is limiting how fast this app can read ad accounts. Live numbers return in about %d minute%s; "
            "views already saved still open." % (mins, "" if mins == 1 else "s"))


def blocked_until(store=None):
    now = time.time()
    if _BLOCK["until"] > now:
        return _BLOCK["until"]
    if store is not None and now - _BLOCK["checked"] > 30 and hasattr(store, "get_gads_entry"):
        _BLOCK["checked"] = now
        try:
            got = store.get_gads_entry(_BLOCK_KEY, 24 * 3600)
            u = float((got[0] or {}).get("until") or 0) if got else 0.0
        except Exception:
            u = 0.0
        if u > now:
            _BLOCK["until"] = u
            return u
    return 0.0


def _set_block(store, until):
    _BLOCK["until"] = until
    try:
        store.put_gads_cache(_BLOCK_KEY, {"until": until})
    except Exception as e:
        log.warning("meta: could not remember the wait (%s)", e)


def _error_of(r):
    try:
        e = (r.json() or {}).get("error") or {}
    except Exception:
        e = {}
    return e if isinstance(e, dict) else {}


def _wait_seconds(r):
    """How long Meta says to wait, from the usage headers it sends with a rate-limit answer, or None."""
    try:
        h = r.headers or {}
        raw = h.get("X-Business-Use-Case-Usage") or h.get("x-business-use-case-usage")
        best = 0.0
        if raw:
            for _acct, items in (json.loads(raw) or {}).items():
                for it in items or []:
                    best = max(best, float(it.get("estimated_time_to_regain_access") or 0) * 60)
        raw = h.get("X-Ad-Account-Usage") or h.get("x-ad-account-usage")
        if raw:
            best = max(best, float((json.loads(raw) or {}).get("estimated_time_to_regain_access") or 0) * 60)
        return best or None
    except Exception:
        return None


RATE_CODES = {4, 17, 32, 341, 613} | set(range(80000, 80015))
TOKEN_CODES = {190, 102}
PERMISSION_CODES = {10, 200, 278, 283}


def _get(path, params, store, *, http=None):
    """One Graph API GET. Never sent while Meta has said to wait; a short rate limit or a hiccup is retried once."""
    import requests
    http = http or requests.get
    url = path if path.startswith("http") else GRAPH + version() + "/" + path.lstrip("/")
    for attempt in (0, 1):
        until = blocked_until(store)
        if until:
            raise OverQuota(_wait_message(until), until=until)
        try:
            r = http(url, headers={"Authorization": "Bearer " + token()}, params=params or {}, timeout=90)
        except Exception as e:
            raise AdsError("Could not reach Meta (%s)." % type(e).__name__) from e
        if r.status_code == 200:
            return r.json()
        err = _error_of(r)
        code, msg = err.get("code"), str(err.get("message") or "")[:300]
        if code in RATE_CODES or r.status_code == 429:
            wait = _wait_seconds(r)
            if wait and wait > WAIT_BLOCK_S:
                until = time.time() + wait
                _set_block(store, until)
                raise OverQuota(_wait_message(until), until=until)
            if attempt == 0:
                time.sleep(min(wait or 3.0, 8.0))
                continue
            raise OverQuota("Meta is rate limiting this app right now. Try again in a minute.")
        if code in TOKEN_CODES:
            raise AdsError("Meta rejected the access token (expired, revoked or lacking access). A new META_ADS_ACCESS_TOKEN is needed.",
                           status=r.status_code, detail=msg)
        if code in PERMISSION_CODES:
            raise AdsError("The Meta token cannot read this ad account. Assign the system user to it in Business Settings.",
                           status=r.status_code, detail=msg)
        if (code in (1, 2) or r.status_code in (500, 502, 503)) and attempt == 0:
            time.sleep(2.0)
            continue
        raise AdsError("Meta answered %s%s" % (r.status_code, (": " + msg) if msg else "."), status=r.status_code, detail=msg)
    raise AdsError("Meta did not answer.")


def _pages(path, params, store, *, http=None, cap=MAX_ROWS):
    """Every row of a Graph list (follows the paging links)."""
    rows, nxt = [], None
    while True:
        d = _get(nxt, None, store, http=http) if nxt else _get(path, params, store, http=http)
        rows.extend(d.get("data") or [])
        nxt = (d.get("paging") or {}).get("next")
        if not nxt:
            return rows
        if len(rows) > cap:
            raise AdsError("That range is too large to chart (over %d rows). Pick a shorter range." % cap)


# ── Accounts and campaigns ──────────────────────────────────────────────────

def parse_accounts(rows):
    out, seen = [], set()
    for a in rows:
        aid = norm_id(a.get("account_id") or a.get("id"))
        if not aid or aid in seen:
            continue
        seen.add(aid)
        status = ACCOUNT_STATUS.get(a.get("account_status"), "")
        name = (a.get("name") or "Account " + aid[4:]).strip()
        if status and status != "ACTIVE":
            name += " (%s)" % status.lower().replace("_", " ")
        out.append({"id": aid, "name": name, "currency": a.get("currency") or "USD", "tz": a.get("timezone_name") or "",
                    "level": 1, "platform": "meta", "status": status or "UNKNOWN"})
    out.sort(key=lambda a: a["name"].lower())
    return out


def list_accounts(store, *, http=None, force=False):
    fields = "account_id,name,currency,timezone_name,account_status"

    def make():
        rows = _pages("me/adaccounts", {"fields": fields, "limit": 200}, store, http=http)
        biz = business_id()
        if biz:
            for edge in ("owned_ad_accounts", "client_ad_accounts"):
                try:
                    rows += _pages("%s/%s" % (biz, edge), {"fields": fields, "limit": 200}, store, http=http)
                except OverQuota:
                    raise
                except AdsError as e:       # the business edges are a bonus; /me/adaccounts already answered
                    log.warning("meta: could not read %s of business %s (%s)", edge, biz, e)
        return parse_accounts(rows)
    return g._cached(store, g.CACHE_V + "meta:accounts", TTL_ACCOUNTS, make, force=force)


def account_by_id(store, act, *, http=None):
    return next((a for a in list_accounts(store, http=http) if a["id"] == act), None)


def _budget(c, currency):
    raw = c.get("daily_budget") or c.get("lifetime_budget")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return round(v if currency in ZERO_DECIMAL else v / 100.0, 2)


def campaign_meta(store, act, currency="USD", *, http=None, force=False):
    """{campaign id: {status, budget}} for the account, from the campaigns edge (insights rows carry neither)."""
    def make():
        rows = _pages(act + "/campaigns", {"fields": "id,effective_status,daily_budget,lifetime_budget", "limit": 500},
                      store, http=http)
        return {str(c.get("id")): {"status": STATUS.get(c.get("effective_status"), "ENABLED"), "budget": _budget(c, currency)}
                for c in rows if c.get("id")}
    return g._cached(store, g.CACHE_V + "meta:camps:" + act, TTL_CAMPAIGNS, make, force=force)


# ── Reading insights rows ───────────────────────────────────────────────────

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _first(items, types):
    """The value of the first of `types` that appears in a list of {action_type, value} (0 if none does)."""
    by = {i.get("action_type"): i.get("value") for i in (items or []) if isinstance(i, dict)}
    for t in types:
        if t in by:
            return _f(by[t])
    return 0.0


def objective_kind(objective):
    o = str(objective or "").upper()
    return OBJECTIVE_LABEL.get(o) or LEGACY.get(o) or "OTHER"


def campaign_type(objective):
    return "META_" + objective_kind(objective)


def result_of(kind, actions, values):
    """(count, value) of what this campaign objective counts as a result."""
    if kind == "SALES":
        return _first(actions, PURCHASE), _first(values, PURCHASE)
    if kind == "LEADS":
        return _first(actions, LEAD), _first(values, LEAD)
    if kind == "APP":
        return _first(actions, INSTALL), 0.0
    if kind == "OTHER":                                  # an objective we do not know: purchases if any, else leads
        p = _first(actions, PURCHASE)
        return (p, _first(values, PURCHASE)) if p else (_first(actions, LEAD), _first(values, LEAD))
    return 0.0, 0.0


def numbers(row):
    """One value per column of the page (see the module docstring), from one insights row."""
    actions, values = row.get("actions"), row.get("action_values")
    kind = objective_kind(row.get("objective"))
    conv, val = result_of(kind, actions, values)
    allc = sum(_first(actions, t) for t in ALL_EVENTS)
    allv = sum(_first(values, t) for t in ALL_EVENTS)
    video = _first(row.get("video_play_actions"), ("video_view",))
    out = [int(_f(row.get("impressions"))), int(_f(row.get("inline_link_clicks"))), round(_f(row.get("spend")), 4),
           round(conv, 4), round(val, 4), round(allc, 4), round(allv, 4), 0, int(_f(row.get("inline_post_engagement"))),
           0, int(video), 0, 0, 0, 0, 0, 0]
    return out


def parse_campaign_days(rows, start, end, meta=None):
    """The compact shape the page charts from (see paid_gads.parse_campaign_days), from insights rows with one row per
    campaign per day."""
    meta = meta or {}
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    didx = {d: i for i, d in enumerate(days)}
    camps, cidx, out = [], {}, []
    for r in rows:
        cid = str(r.get("campaign_id") or "")
        d = r.get("date_start")
        if not cid or d not in didx:
            continue
        if cid not in cidx:
            m = meta.get(cid) or {}
            cidx[cid] = len(camps)
            camps.append({"id": cid, "name": r.get("campaign_name") or cid, "status": m.get("status") or "ENABLED",
                          "type": campaign_type(r.get("objective")), "budget": m.get("budget", 0.0)})
        out.append([cidx[cid], didx[d]] + numbers(r))
    return {"start": start, "end": end, "days": days, "campaigns": camps, "rows": out, "missing": list(MISSING)}


def q_insights(start, end):
    return {"level": "campaign", "time_increment": 1, "time_range": json.dumps({"since": start, "until": end}),
            "fields": ",".join(INSIGHT_FIELDS), "limit": PAGE_LIMIT}


def fetch_days(store, act, first, last, *, http=None, meta=None):
    """Insights rows for first..last, in chunks of CHUNK_DAYS so no single answer is large."""
    rows = []
    for a, b in _chunks(first, last):
        rows.extend(_pages(act + "/insights", q_insights(a, b), store, http=http))
    return rows


def _chunks(first, last):
    out, a = [], dt.date.fromisoformat(first)
    end = dt.date.fromisoformat(last)
    while a <= end:
        b = min(a + dt.timedelta(days=CHUNK_DAYS - 1), end)
        out.append((a.isoformat(), b.isoformat()))
        a = b + dt.timedelta(days=1)
    return out


# ── Campaign numbers, one day at a time (the Google side's day store) ───────

def campaign_days(store, act, start, end, *, currency="USD", force=False, notes=None, stamps=None, http=None):
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    try:
        stored = store.get_gads_days(act, start, end)
    except Exception as e:
        log.warning("meta: stored days could not be read (%s)", e)
        stored = {}
    today = dt.date.today()
    need = [d for d in days if force or d not in stored or g._age_s(stored[d][1]) > g._day_ttl(d, today)]
    per_day = {d: stored[d][0] for d in days if d in stored}
    stamp = {d: stored[d][1] for d in days if d in stored}
    meta = None
    for first, last in g._runs(need):
        try:
            if meta is None:
                try:
                    meta = campaign_meta(store, act, currency, http=http, force=force)
                except OverQuota:
                    raise
                except AdsError as e:         # status and budget are a nicety; the numbers still chart without them
                    if g._can_serve_stale(e):
                        raise
                    log.warning("meta: campaigns of %s unavailable (%s)", act, e)
                    meta = {}
            rows = fetch_days(store, act, first, last, http=http)
        except AdsError as e:
            if not g._can_serve_stale(e):
                raise
            lost = [d for d in g._span(first, last) if d not in per_day]
            if lost:
                raise
            if notes is not None:
                notes.append(g.oldest([stamp[d] for d in g._span(first, last)]))
            log.warning("meta: serving stored days %s to %s (%s)", first, last, e)
            continue
        fresh = g.split_days(parse_campaign_days(rows, first, last, meta))
        try:
            store.put_gads_days(act, fresh)
        except Exception as e:
            log.warning("meta: could not store days (%s)", e)
        now = g._now_iso()
        for d, entry in fresh.items():
            per_day[d], stamp[d] = entry, now
    if stamps is not None:
        stamps.append(g.oldest(list(stamp.values())))
    return g.assemble_days(days, per_day)


# ── Reach and frequency (whole-period, not additive) ────────────────────────

SHARE_KEYS = ["reach", "frequency"]


def make_shares(store, act, start, end, *, http=None):
    rows = _pages(act + "/insights", {"level": "account", "time_range": json.dumps({"since": start, "until": end}),
                                        "fields": "reach,frequency", "limit": 5}, store, http=http)
    out = {k: None for k in SHARE_KEYS}
    if rows:
        out["reach"] = int(_f(rows[0].get("reach")))
        out["frequency"] = round(_f(rows[0].get("frequency")), 4)
    return out


def performance(store, act, start, end, *, currency="USD", with_shares=True, notes=None, force=False, stamps=None, http=None):
    def days():
        return campaign_days(store, act, start, end, currency=currency, force=force, notes=notes, stamps=stamps, http=http)

    def shares():
        def run():
            try:
                return g._cached(store, g.CACHE_V + "meta:shares:%s:%s:%s" % (act, start, end), g._ttl(end),
                                 lambda: make_shares(store, act, start, end, http=http), notes, force=force, stamps=stamps)
            except AdsError as e:
                log.warning("meta: reach unavailable for %s (%s)", act, e)
                return {k: None for k in SHARE_KEYS}
        return run

    jobs = [days] + ([shares()] if with_shares else [])
    res = g._par(jobs)
    out = dict(res[0])
    out["sis"] = {"account": None, "campaigns": {}}
    if with_shares:
        out["shares"] = res[1]
    return out


def performance_pair(store, act, cur, cmp, *, currency="USD", with_shares=True, force=False, stamps=None, http=None):
    notes = []
    jobs = [lambda: performance(store, act, cur[0], cur[1], currency=currency, with_shares=with_shares, notes=notes,
                                force=force, stamps=stamps, http=http)]
    if cmp:
        jobs.append(lambda: performance(store, act, cmp[0], cmp[1], currency=currency, with_shares=with_shares, notes=notes,
                                        force=force, stamps=stamps, http=http))
    res = g._par(jobs)
    return res[0], (res[1] if cmp else None), notes


# ── Device, placement and hour splits ───────────────────────────────────────

DEVICE_KEYS = {"mobile_app": "MOBILE", "mobile_web": "MOBILE", "desktop": "DESKTOP", "tablet": "TABLET",
               "connected_tv": "CONNECTED_TV", "unknown": "UNKNOWN"}
PLACEMENT_KEYS = {"facebook": "FACEBOOK", "instagram": "INSTAGRAM", "audience_network": "AUDIENCE_NETWORK",
                  "messenger": "MESSENGER", "threads": "THREADS", "unknown": "UNKNOWN"}


def q_split(breakdown, start, end):
    return {"level": "campaign", "breakdowns": breakdown, "time_range": json.dumps({"since": start, "until": end}),
            "fields": ",".join(INSIGHT_FIELDS), "limit": PAGE_LIMIT}


def _fold(rows, key_of):
    acc = {}
    for r in rows:
        k = key_of(r)
        if k is None:
            continue
        b = numbers(r)
        a = acc.setdefault(k, [0] * NB)
        for i in range(NB):
            a[i] += b[i]
    return acc


def _rounded(vals):
    return [round(x, 4) if isinstance(x, float) else x for x in vals]


def parse_extras(device_rows, placement_rows, hour_rows):
    dev = _fold(device_rows, lambda r: DEVICE_KEYS.get(str(r.get("device_platform") or "unknown").lower(), "OTHER"))
    net = _fold(placement_rows, lambda r: PLACEMENT_KEYS.get(str(r.get("publisher_platform") or "unknown").lower(), "OTHER"))

    def hour(r):
        h = str(r.get("hourly_stats_aggregated_by_advertiser_time_zone") or "")[:2]
        return int(h) if h.isdigit() else None
    hrs = _fold(hour_rows, hour)
    return {"device": [[k] + _rounded(v) for k, v in sorted(dev.items())],
            "network": [[k] + _rounded(v) for k, v in sorted(net.items())],
            "dowHour": [], "hour": [[h] + _rounded(v) for h, v in sorted(hrs.items())], "missing": list(MISSING)}


def extras(store, act, start, end, *, notes=None, force=False, stamps=None, http=None):
    def read(b):
        return _pages(act + "/insights", q_split(b, start, end), store, http=http)

    def make():
        d, p, h = g._par([lambda: read("device_platform"), lambda: read("publisher_platform"),
                          lambda: read("hourly_stats_aggregated_by_advertiser_time_zone")])
        return parse_extras(d, p, h)
    return g._cached(store, g.CACHE_V + "meta:extras:%s:%s:%s" % (act, start, end), g._ttl(end), make, notes, force=force, stamps=stamps)


# ── A short report for the admin check ──────────────────────────────────────

def status(store, *, http=None):
    """What an admin needs to see that the connection works: the accounts it reads and one small real read."""
    problem = config_problem()
    out = {"configured": problem is None, "problem": problem, "version": version(), "business": business_id() or None}
    if problem:
        return out
    accounts = list_accounts(store, http=http, force=True)
    out["accounts"] = len(accounts)
    out["byStatus"] = {}
    for a in accounts:
        out["byStatus"][a["status"]] = out["byStatus"].get(a["status"], 0) + 1
    out["currencies"] = sorted({a["currency"] for a in accounts})
    out["sample"] = [{"id": a["id"], "name": a["name"], "currency": a["currency"]} for a in accounts[:8]]
    live = next((a for a in accounts if a["status"] == "ACTIVE"), None)
    if live:
        end = dt.date.today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=6)
        rows = _pages(live["id"] + "/insights", q_insights(start.isoformat(), end.isoformat()), store, http=http)
        tot = [0] * NB
        for r in rows:
            for i, v in enumerate(numbers(r)):
                tot[i] += v
        out["read"] = {"account": live["name"], "from": start.isoformat(), "to": end.isoformat(), "rows": len(rows),
                       "impressions": tot[0], "linkClicks": tot[1], "spend": round(tot[2], 2), "results": round(tot[3], 2)}
    return out


def probe(store, act, *, days=30, http=None):
    """What an account's insights actually report, for the admin check: every action type (with its count and value) summed
    over the last `days` days and over the account's campaigns, per objective. This is how to see which action type a
    campaign's results really are when the page shows no conversions for money that was clearly spent."""
    act = norm_id(act)
    if not act:
        raise ValueError("that is not a Meta ad account id")
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    rows = _pages(act + "/insights", {"level": "campaign", "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
                                      "fields": "campaign_name,objective,spend,impressions,inline_link_clicks,actions,action_values", "limit": PAGE_LIMIT},
                  store, http=http)
    by = {}
    for r in rows:
        o = by.setdefault(str(r.get("objective") or ""), {"campaigns": [], "spend": 0.0, "actions": {}, "values": {}})
        o["campaigns"].append(r.get("campaign_name"))
        o["spend"] = round(o["spend"] + _f(r.get("spend")), 2)
        for k, bucket in (("actions", "actions"), ("action_values", "values")):
            for a in r.get(k) or []:
                t = a.get("action_type")
                o[bucket][t] = round(o[bucket].get(t, 0) + _f(a.get("value")), 2)
    for o in by.values():
        o["campaigns"] = sorted(set(o["campaigns"]))[:6]
        for b in ("actions", "values"):
            o[b] = dict(sorted(o[b].items(), key=lambda kv: -kv[1])[:25])
    return {"account": act, "from": start.isoformat(), "to": end.isoformat(), "objectives": by}
