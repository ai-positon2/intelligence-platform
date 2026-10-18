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

The developer token has Explorer access, a small daily allowance that Google
counts in its own way (the 429 says "Number of operations for explorer
access"). What keeps a busy team inside it:

  * campaign numbers are stored one day at a time in Postgres (pm_gads_days),
    so a range is built from days already read and only the days never read,
    or old enough to have changed, are asked for: last 7 days, last 30, last
    90 and the comparison range share the same stored days;
  * every other result (shares, device and hour splits, products, segment
    reports) is saved whole in Postgres (pm_gads_cache) and kept in memory
    for a minute, and when Google refuses (allowance spent, or Google
    unreachable) the last saved copy is served and the page says how old it is;
  * a Refresh button asks Google again for the view on screen (`force`), at
    most once every REFRESH_COOLDOWN_S per account, and says when the numbers
    were last read from Google;
  * Google's own "retry in N seconds" is remembered (in memory and in the
    database, so every worker knows) and no request is sent until it passes;
  * a daily tally refuses new requests past DAILY_LIMIT.

Metrics: five core ones (impressions, clicks, cost, conversions, conversion
value) are always fetched. A dozen more ("optional": all conversions,
interactions, video views, phone calls, viewability ...) ride along in the same
query, and if Google refuses one of them for any reason it is dropped and
remembered, so one unsupported metric can never take the page down. The
auction-share metrics (impression share and its lost-to-budget and
lost-to-rank parts) cannot be summed over days, so they are read once per
range for the whole account.
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
DAILY_LIMIT = 2400            # our own ceiling, below what we believe Google allows
MAX_RANGE_DAYS = 800
MAX_ROWS = 250_000
TTL_OPEN = 3600               # a range that includes today or yesterday
TTL_CLOSED = 24 * 3600        # a range that has ended
TTL_DAY_RECENT = 3600         # a stored day, yesterday or today
TTL_DAY_MONTH = 24 * 3600     # a stored day in the last 30 days (conversions still arrive late)
TTL_DAY_OLD = 7 * 24 * 3600   # anything older
REFRESH_COOLDOWN_S = 120      # the Refresh button, per account
MEM_TTL_S = 60                # the in-memory copy in front of Postgres
TTL_ACCOUNTS = 3600
DAILY_BLOCK_S = 300           # a retry delay longer than this is the daily allowance, not a hiccup
CACHE_V = "v2:"               # bump when the shape of a cached range changes

ID_RE = re.compile(r"^\d{10}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOW = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

# The numbers every row carries, in column order: (GAQL metric, scale to the unit shown).
CORE = [("impressions", 1), ("clicks", 1), ("cost_micros", 1e-6), ("conversions", 1), ("conversions_value", 1)]
OPTIONAL = [("all_conversions", 1), ("all_conversions_value", 1), ("view_through_conversions", 1), ("interactions", 1),
            ("engagements", 1), ("video_trueview_views", 1), ("cross_device_conversions", 1), ("invalid_clicks", 1),
            ("phone_calls", 1), ("phone_impressions", 1), ("active_view_impressions", 1),
            ("active_view_measurable_impressions", 1)]
BASE = CORE + OPTIONAL
NB = len(BASE)
# Whole-period shares: not additive, so read per range for the whole account.
SHARES = ["search_impression_share", "search_top_impression_share", "search_absolute_top_impression_share",
          "search_budget_lost_impression_share", "search_rank_lost_impression_share",
          "search_budget_lost_top_impression_share", "search_rank_lost_top_impression_share",
          "content_impression_share", "content_budget_lost_impression_share", "content_rank_lost_impression_share",
          "top_impression_percentage", "absolute_top_impression_percentage", "search_click_share",
          "search_exact_match_impression_share"]

_BAD = set()                  # optional metrics Google named in a refusal (per worker, until restart)
_BAD_SHARES = set()           # the same for the auction-share metrics
_BLOCK = {"until": 0.0, "checked": 0.0}


class AdsError(RuntimeError):
    """A Google Ads call failed; the message is safe to show staff."""

    def __init__(self, message, status=None, detail=""):
        super().__init__(message)
        self.status, self.detail = status, detail or message


class NotConfigured(AdsError):
    pass


class RefreshTooSoon(AdsError):
    """The Refresh button was pressed again before REFRESH_COOLDOWN_S passed."""

    def __init__(self, message, retry_in=0):
        super().__init__(message, status=429)
        self.retry_in = retry_in


class OverQuota(AdsError):
    def __init__(self, message, status=429, until=None):
        super().__init__(message, status=status)
        self.until = until            # epoch seconds when Google says it will answer again, if it said


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


def _block_message(until):
    hrs = max(1, int((until - time.time()) / 3600 + 0.5))
    return ("Google has used up this app's daily Google Ads allowance. Live numbers return in about %d hour%s; "
            "views already saved still open." % (hrs, "" if hrs == 1 else "s"))


def blocked_until(store=None):
    """Epoch seconds until which Google will not answer (0 if it will), asking the database at most once a minute
    so a worker that never saw the 429 still knows."""
    now = time.time()
    if _BLOCK["until"] > now:
        return _BLOCK["until"]
    if store is not None and now - _BLOCK["checked"] > 60:
        _BLOCK["checked"] = now
        try:
            u = float(store.get_gads_block() or 0)
        except Exception:
            u = 0.0
        if u > now:
            _BLOCK["until"] = u
            return u
    return 0.0


def _set_block(store, until):
    _BLOCK["until"] = until
    try:
        store.set_gads_block(until)
    except Exception as e:
        log.warning("gads: could not remember the block (%s)", e)


def reset_state():
    """For tests: forget what was learned about Google's refusals."""
    _BAD.clear()
    _BAD_SHARES.clear()
    _BLOCK.update(until=0.0, checked=0.0)
    forget()


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
                        "Pacific time; views already saved still open." % DAILY_LIMIT)


def _google_message(r, limit=300):
    try:
        e = r.json()
        e = e[0] if isinstance(e, list) and e else e
        err = (e or {}).get("error", {})
        for d in err.get("details") or []:
            for x in d.get("errors", []) or []:
                if x.get("message"):
                    return x["message"][:limit]
        return (err.get("message") or "")[:limit]
    except Exception:
        return ""


def _retry_seconds(r):
    """How long Google says to wait, from its quota error ("retryDelay": "61458s"), or None."""
    try:
        e = r.json()
        e = e[0] if isinstance(e, list) and e else e
        for d in ((e or {}).get("error", {}).get("details")) or []:
            for x in d.get("errors", []) or []:
                v = ((x.get("details") or {}).get("quotaErrorDetails") or {}).get("retryDelay")
                if v:
                    return float(str(v).rstrip("s"))
    except Exception:
        pass
    return None


def _post(path, body, store, *, http=None):
    """One Google Ads request. Counted, never sent while Google has said to wait, retried once on a short rate limit."""
    import requests
    http = http or requests.post
    for attempt in (0, 1):
        until = blocked_until(store)
        if until:
            raise OverQuota(_block_message(until), until=until)
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
        if r.status_code == 429:
            delay = _retry_seconds(r)
            if delay and delay > DAILY_BLOCK_S:
                until = time.time() + delay
                _set_block(store, until)
                raise OverQuota(_block_message(until), until=until)
            if attempt == 0:
                time.sleep(min(delay or 2.0, 8.0))
                continue
            raise OverQuota("Google Ads is rate limiting this app right now. Try again in a minute.")
        if r.status_code == 503 and attempt == 0:
            time.sleep(2.0)
            continue
        msg = _google_message(r)
        raise AdsError("Google Ads answered %s%s" % (r.status_code, (": " + msg) if msg else "."),
                       status=r.status_code, detail=_google_message(r, 1500))
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


def _names(text, fields):
    """Which of `fields` a Google error message names (whole words, so all_conversions is not all_conversions_value)."""
    return [f for f in fields if re.search(r"(?<![a-z_])%s(?![a-z_])" % re.escape(f), text or "")]


def search_pruned(customer_id, build, optional, bad, store, *, http=None):
    """Run build(fields) with every optional field not known to be refused. If Google answers 400 naming some of
    them, drop those (and remember them) and try again; if it names none, try once with none of them (and
    remember nothing, because the cause may be the range or the account). Returns (rows, fields_used)."""
    fields = [f for f in optional if f not in bad]
    for _ in range(4):
        try:
            return search(customer_id, build(fields), store, http=http), fields
        except OverQuota:
            raise
        except AdsError as e:
            if e.status != 400 or not fields:
                raise
            named = _names(e.detail, fields)
            if named:
                bad.update(named)
                log.warning("gads: Google refused %s (%s); dropping them", named, e.detail[:200])
                fields = [f for f in fields if f not in named]
            else:
                log.warning("gads: 400 naming no optional metric (%s); retrying with the core five", e.detail[:200])
                fields = []
    raise AdsError("Google Ads refused the metric list.")


# ── Queries ─────────────────────────────────────────────────────────────────

def q_accounts():
    return ("SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code, "
            "customer_client.time_zone, customer_client.manager, customer_client.level, "
            "customer_client.status, customer_client.test_account FROM customer_client")


def _metrics(fields):
    return ", ".join("metrics." + f for f in [c[0] for c in CORE] + list(fields))


def q_campaign_days(start, end, fields=()):
    return ("SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
            "campaign_budget.amount_micros, segments.date, %s "
            "FROM campaign WHERE segments.date BETWEEN '%s' AND '%s'" % (_metrics(fields), start, end))


def q_account_days(start, end, fields=()):
    """The whole account, one row per day: one request reads years of it, where q_campaign_days is a row per campaign per day."""
    return ("SELECT segments.date, %s FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
            % (_metrics(fields), start, end))


ACCOUNT_CAMPAIGN = {"id": "ACCOUNT", "name": "All campaigns", "advertisingChannelType": "ACCOUNT"}


def q_campaign_sis(start, end):
    return ("SELECT campaign.id, metrics.search_impression_share FROM campaign "
            "WHERE segments.date BETWEEN '%s' AND '%s' AND campaign.advertising_channel_type = 'SEARCH'"
            % (start, end))


def q_shares(start, end, fields):
    return ("SELECT %s FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
            % (", ".join("metrics." + f for f in fields), start, end))


def q_device(start, end, fields=()):
    return "SELECT segments.device, %s FROM customer WHERE segments.date BETWEEN '%s' AND '%s'" % (_metrics(fields), start, end)


def q_network(start, end, fields=()):
    return ("SELECT segments.ad_network_type, %s FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
            % (_metrics(fields), start, end))


def q_dow_hour(start, end, fields=()):
    return ("SELECT segments.day_of_week, segments.hour, %s FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
            % (_metrics(fields), start, end))


# ── Parsing ─────────────────────────────────────────────────────────────────

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _camel(field):
    head, *rest = field.split("_")
    return head + "".join(w.title() for w in rest)


_KEYS = [(_camel(f), k) for f, k in BASE]


def _base(m):
    """One value per column in BASE (impressions, clicks, cost, conversions, value, then the optional ones; 0 where
    Google sent nothing) from a metrics block."""
    m = m or {}
    out = []
    for i, (key, scale) in enumerate(_KEYS):
        v = _f(m.get(key)) * scale
        out.append(int(v) if i < 2 else round(v, 4))
    return out


def missing_columns(fields):
    """Indexes (into BASE) of the optional metrics that were NOT fetched, so the page can say so instead of showing 0."""
    return [len(CORE) + i for i, (f, _) in enumerate(OPTIONAL) if f not in fields]


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


def parse_campaign_days(rows, start, end, fields=None):
    """The compact shape the page charts from.

    campaigns: [{id, name, type, status, budget}], days: ['YYYY-MM-DD', ...] every day in range (so a day with no
    spend still plots as zero), rows: [[campaign index, day index, <one number per BASE column>], ...], and
    missing: the BASE columns Google would not give."""
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
    return {"start": start, "end": end, "days": days, "campaigns": camps, "rows": out,
            "missing": missing_columns([f for f, _ in OPTIONAL] if fields is None else fields)}


def parse_sis(campaign_rows):
    camp = {}
    for r in campaign_rows:
        v = (r.get("metrics") or {}).get("searchImpressionShare")
        cid = str((r.get("campaign") or {}).get("id") or "")
        if cid and v not in (None, ""):
            camp[cid] = round(_f(v), 4)
    return camp


def parse_shares(rows, fields):
    """{metric: share between 0 and 1, or None} for the whole account over the range. Google reports a share as
    "< 10%" when it is tiny; that comes back as the number 0.0999 and is kept as it is."""
    out = {f: None for f in fields}
    for r in rows:
        m = r.get("metrics") or {}
        for f in fields:
            v = m.get(_camel(f))
            if v not in (None, ""):
                try:
                    out[f] = round(float(v), 4)
                except (TypeError, ValueError):
                    pass
    return out


def parse_extras(device_rows, network_rows, dow_hour_rows, missing=()):
    def seg(rows, key):
        acc = {}
        for r in rows:
            k = (r.get("segments") or {}).get(key) or "UNKNOWN"
            b = _base(r.get("metrics"))
            a = acc.setdefault(k, [0] * NB)
            for i in range(NB):
                a[i] += b[i]
        return [[k] + [round(x, 4) if isinstance(x, float) else x for x in v] for k, v in sorted(acc.items())]
    heat = {}
    for r in dow_hour_rows:
        s = r.get("segments") or {}
        if s.get("dayOfWeek") not in DOW or s.get("hour") is None:
            continue
        key = (DOW.index(s["dayOfWeek"]), int(s["hour"]))
        b = _base(r.get("metrics"))
        a = heat.setdefault(key, [0] * NB)
        for i in range(NB):
            a[i] += b[i]
    return {"device": seg(device_rows, "device"), "network": seg(network_rows, "adNetworkType"),
            "dowHour": [[d, h] + [round(x, 4) if isinstance(x, float) else x for x in v]
                        for (d, h), v in sorted(heat.items())], "missing": sorted(set(missing))}


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


def _day_ttl(day, today=None):
    """How long a stored day stays good: yesterday and today change by the hour, the last month's conversions still
    arrive late, anything older has settled."""
    age = ((today or dt.date.today()) - dt.date.fromisoformat(day)).days
    return TTL_DAY_RECENT if age <= 1 else TTL_DAY_MONTH if age <= 30 else TTL_DAY_OLD


def _age_s(iso):
    try:
        t = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 1e12
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds()


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def oldest(stamps):
    """The oldest of the times data was read from Google (ISO text), or None."""
    st = [x for x in (stamps or []) if x]
    return min(st, key=lambda x: -_age_s(x)) if st else None      # compared as times: the database and this process write different UTC offsets


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


def _can_serve_stale(e):
    return isinstance(e, OverQuota) or e.status in (None, 429, 500, 502, 503)


def _saved_copy(store, key):
    """(value, saved-at) of the newest saved copy of `key`, or None. Before CACHE_V changed the stored shape the same
    ranges were saved without the prefix; their rows lack the newer columns, which the page reads as "no data", so
    they still beat showing nothing when Google cannot answer."""
    keys = [key]
    base = key[len(CACHE_V):] if key.startswith(CACHE_V) else ""
    if base.split(":")[0] in ("days", "extras", "accounts"):
        keys.append(base)
    for k in keys:
        try:
            old = store.get_gads_cache_any(k)
        except Exception:
            old = None
        if old is not None:
            return old
    return None


_MEM = {}                      # key -> (read at, value, saved-at): a minute in front of Postgres, per worker
_MEM_LOCK = threading.Lock()
_MEM_MAX = 400


def _mem_get(key, ttl):
    with _MEM_LOCK:
        hit = _MEM.get(key)
    if hit and time.time() - hit[0] < min(ttl, MEM_TTL_S):
        return hit
    return None


def _mem_put(key, value, saved):
    with _MEM_LOCK:
        _MEM[key] = (time.time(), value, saved)
        if len(_MEM) > _MEM_MAX:
            for k in sorted(_MEM, key=lambda x: _MEM[x][0])[:len(_MEM) - _MEM_MAX]:
                _MEM.pop(k, None)


def forget(prefix=""):
    """Drop the in-memory copies (all, or those whose key starts with `prefix`): the next read goes to Postgres."""
    with _MEM_LOCK:
        for k in [k for k in _MEM if k.startswith(prefix)]:
            _MEM.pop(k, None)


def _entry(store, key, ttl):
    """(value, saved-at or None) if a copy younger than ttl is saved, else None."""
    hit = _mem_get(key, ttl)
    if hit:
        return hit[1], hit[2]
    try:
        getter = getattr(store, "get_gads_entry", None)
        if getter:
            got = getter(key, ttl)
        else:
            v = store.get_gads_cache(key, ttl)
            got = (v, None) if v is not None else None
    except Exception as e:
        log.warning("gads: cache read failed (%s)", e)
        return None
    if got is not None:
        _mem_put(key, got[0], got[1])
    return got


def _cached(store, key, ttl, make, notes=None, force=False, stamps=None):
    """The saved copy if it is fresh; else ask Google. With `force` the saved copy is skipped and Google is asked
    again. If Google cannot answer (allowance spent, unreachable), the last saved copy of any age is served and its
    time added to `notes` so the page can say so. `stamps` collects when each result was read from Google."""
    if not force:
        hit = _entry(store, key, ttl)
        if hit is not None:
            if stamps is not None:
                stamps.append(hit[1])
            return hit[0]
    try:
        value = make()
    except AdsError as e:
        if not _can_serve_stale(e):
            raise
        old = _saved_copy(store, key)
        if old is None:
            raise
        if notes is not None:
            notes.append(old[1])
        if stamps is not None:
            stamps.append(old[1])
        log.warning("gads: serving the saved copy of %s (%s)", key, e)
        return old[0]
    saved = _now_iso()
    try:
        store.put_gads_cache(key, value)
    except Exception as e:
        log.warning("gads: cache write failed (%s)", e)
    _mem_put(key, value, saved)
    if stamps is not None:
        stamps.append(saved)
    return value


def refresh_guard(store, scope):
    """Called when someone presses Refresh: raises RefreshTooSoon if `scope` (an account, say) was refreshed in the
    last REFRESH_COOLDOWN_S seconds, else notes that it is being refreshed now. The Explorer allowance is shared by
    everyone, so a button that can be pressed over and over would spend it."""
    key = "rl:" + scope
    try:
        last = store.get_gads_entry(key, REFRESH_COOLDOWN_S) if hasattr(store, "get_gads_entry") else None
    except Exception:
        last = None
    if last is not None:
        left = max(1, int(REFRESH_COOLDOWN_S - _age_s(last[1])))
        raise RefreshTooSoon("This account was refreshed moments ago, so these numbers are current. You can refresh it "
                             "again in %d second%s." % (left, "" if left == 1 else "s"), retry_in=left)
    try:
        store.put_gads_cache(key, {"t": _now_iso()})
    except Exception as e:
        log.warning("gads: could not note a refresh (%s)", e)


def list_accounts(store, *, http=None, force=False):
    def make():
        return parse_accounts(search(login_customer(), q_accounts(), store, http=http))
    return _cached(store, CACHE_V + "accounts", TTL_ACCOUNTS, make, force=force)


def account_ids(store, *, http=None):
    return {a["id"] for a in list_accounts(store, http=http)}


# ── Campaign numbers, one day at a time ─────────────────────────────────────

def _runs(days):
    """Split a sorted list of YYYY-MM-DD days into runs of consecutive days: [(first, last), ...]."""
    out = []
    for d in days:
        if out and (dt.date.fromisoformat(d) - dt.date.fromisoformat(out[-1][1])).days == 1:
            out[-1][1] = d
        else:
            out.append([d, d])
    return [tuple(r) for r in out]


def split_days(parsed):
    """A parsed run (parse_campaign_days) as {day: {"m": columns Google would not give, "r": [[campaign id, name,
    status, type, budget, <one number per column>], ...]}}, one entry for every day of the run, quiet ones too."""
    out = {d: {"m": parsed["missing"], "r": []} for d in parsed["days"]}
    for ci, di, *nums in parsed["rows"]:
        c = parsed["campaigns"][ci]
        out[parsed["days"][di]]["r"].append([c["id"], c["name"], c["status"], c["type"], c["budget"]] + nums)
    return out


def assemble_days(days, per_day):
    """The shape the page charts from, built from stored days: campaigns in the order first seen, rows
    [campaign index, day index, <numbers>], and `missing`, the columns any of those days could not get from Google. A campaign's name, status
    and daily budget are the ones its newest stored day carries (a day read weeks ago must not say a paused campaign is enabled)."""
    camps, cidx, rows, missing = [], {}, [], set()
    for di, d in enumerate(days):
        entry = per_day[d]
        missing.update(entry.get("m") or [])
        for r in entry["r"]:
            cid = r[0]
            now = {"id": cid, "name": r[1], "status": r[2], "type": r[3], "budget": r[4]}
            if cid not in cidx:
                cidx[cid] = len(camps)
                camps.append(now)
            else:
                camps[cidx[cid]] = now             # the days run oldest first: the newest one a campaign appears in says what it is now
            rows.append([cidx[cid], di] + r[5:])
    return {"start": days[0], "end": days[-1], "days": list(days), "campaigns": camps, "rows": rows, "missing": sorted(missing)}


def campaign_days(store, customer_id, start, end, *, force=False, notes=None, stamps=None, http=None, account=False):
    """Campaign-by-day numbers for the range, from the days already stored; only days never read, or older than their
    time-to-live (or all of them, with `force`), are asked for, one query per run of consecutive days. If Google cannot
    answer, stored days of any age stand in (their times go to `notes`); a day that was never stored cannot.

    With `account`, the same thing for the whole account in one row a day (the five core numbers only, kept under their own
    key so they never mix with the campaign rows): years of history in one request, for pages that need no campaign split."""
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    key = customer_id + ":acct" if account else customer_id
    try:
        stored = store.get_gads_days(key, start, end)
    except Exception as e:
        log.warning("gads: stored days could not be read (%s)", e)
        stored = {}
    today = dt.date.today()
    need = [d for d in days if force or d not in stored or _age_s(stored[d][1]) > _day_ttl(d, today)]
    per_day = {d: stored[d][0] for d in days if d in stored}
    stamp = {d: stored[d][1] for d in days if d in stored}
    for first, last in _runs(need):
        try:
            if account:
                rows, used = search_pruned(customer_id, lambda f: q_account_days(first, last, f), [], _BAD, store, http=http)
                rows = [dict(r, campaign=ACCOUNT_CAMPAIGN) for r in rows]
            else:
                rows, used = search_pruned(customer_id, lambda f: q_campaign_days(first, last, f), [o[0] for o in OPTIONAL],
                                           _BAD, store, http=http)
        except AdsError as e:
            if not _can_serve_stale(e):
                raise
            lost = [d for d in _span(first, last) if d not in per_day]
            if lost:
                legacy = None if account else _legacy_range(store, customer_id, start, end)
                if legacy is not None:
                    if notes is not None:
                        notes.append(legacy[1])
                    if stamps is not None:
                        stamps.append(legacy[1])
                    return legacy[0]
                raise
            if notes is not None:
                notes.append(oldest([stamp[d] for d in _span(first, last)]))
            log.warning("gads: serving stored days %s to %s (%s)", first, last, e)
            continue
        fresh = split_days(parse_campaign_days(rows, first, last, used))
        try:
            store.put_gads_days(key, fresh)
        except Exception as e:
            log.warning("gads: could not store days (%s)", e)
        now = _now_iso()
        for d, entry in fresh.items():
            per_day[d], stamp[d] = entry, now
    if stamps is not None:
        stamps.append(oldest(list(stamp.values())))
    return assemble_days(days, per_day)


def _span(first, last):
    a, b = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def _legacy_range(store, customer_id, start, end):
    """A whole-range copy saved before days were stored one by one, if one exists: better than nothing when Google
    cannot answer and no day of the range was ever stored."""
    return _saved_copy(store, CACHE_V + "days:%s:%s:%s" % (customer_id, start, end))


def performance(store, customer_id, start, end, *, with_sis=True, with_shares=True, notes=None, force=False, stamps=None, http=None):
    """One range of campaign-by-day rows, plus (for the main range) each Search campaign's impression share and
    (for the main range and its comparison) the whole account's auction shares."""
    def days():
        return campaign_days(store, customer_id, start, end, force=force, notes=notes, stamps=stamps, http=http)

    def make_sis():
        try:
            return parse_sis(search(customer_id, q_campaign_sis(start, end), store, http=http))
        except OverQuota:
            raise
        except AdsError as e:       # share is a nicety; the range still charts without it
            if _can_serve_stale(e):
                raise
            log.warning("gads: campaign impression share unavailable for %s (%s)", customer_id, e)
            return {}

    def make_shares():
        out = {f: None for f in SHARES}
        try:
            rows, used = search_pruned(customer_id, lambda f: q_shares(start, end, f), SHARES, _BAD_SHARES, store, http=http)
            out.update(parse_shares(rows, used))
        except OverQuota:
            raise
        except AdsError as e:
            if _can_serve_stale(e):
                raise
            log.warning("gads: auction shares unavailable for %s (%s)", customer_id, e)
        return out

    def soft(fn, empty):
        """The shares are extras: if Google cannot give them (and no saved copy exists) the range still draws."""
        def run():
            try:
                return fn()
            except AdsError as e:
                log.warning("gads: leaving out an optional read for %s (%s)", customer_id, e)
                return empty
        return run

    jobs = [days]
    if with_sis:
        jobs.append(soft(lambda: _cached(store, CACHE_V + "sis:%s:%s:%s" % (customer_id, start, end), _ttl(end), make_sis, notes,
                                         force=force, stamps=stamps), {}))
    if with_shares:
        jobs.append(soft(lambda: _cached(store, CACHE_V + "shares:%s:%s:%s" % (customer_id, start, end), _ttl(end), make_shares, notes,
                                         force=force, stamps=stamps), {f: None for f in SHARES}))
    res = _par(jobs)
    out = dict(res[0])
    i = 1
    if with_sis:
        out["sis"] = {"account": None, "campaigns": res[i]}
        i += 1
    if with_shares:
        out["shares"] = res[i]
        if with_sis:
            out["sis"]["account"] = res[i].get("search_impression_share")
    return out


def performance_pair(store, customer_id, cur, cmp, *, with_sis=True, with_shares=True, force=False, stamps=None, http=None):
    """The main range and (if given) its comparison range, fetched together. Returns (current, compare, saved) where
    `saved` lists the times of any saved copies served because Google could not answer; `stamps`, if given, is filled
    with the times the numbers were read from Google (see oldest())."""
    notes = []
    jobs = [lambda: performance(store, customer_id, cur[0], cur[1], with_sis=with_sis, with_shares=with_shares, notes=notes,
                                force=force, stamps=stamps, http=http)]
    if cmp:
        jobs.append(lambda: performance(store, customer_id, cmp[0], cmp[1], with_sis=False, with_shares=with_shares, notes=notes,
                                        force=force, stamps=stamps, http=http))
    res = _par(jobs)
    return res[0], (res[1] if cmp else None), notes


def extras(store, customer_id, start, end, *, notes=None, force=False, stamps=None, http=None):
    used = []

    def one(build):
        rows, f = search_pruned(customer_id, build, [o[0] for o in OPTIONAL], _BAD, store, http=http)
        used.append(f)
        return rows

    def make():
        d, n, h = _par([lambda: one(lambda f: q_device(start, end, f)), lambda: one(lambda f: q_network(start, end, f)),
                        lambda: one(lambda f: q_dow_hour(start, end, f))])
        have = set(used[0]).intersection(*[set(u) for u in used[1:]]) if used else set()
        return parse_extras(d, n, h, missing_columns(have))
    return _cached(store, CACHE_V + "extras:%s:%s:%s" % (customer_id, start, end), _ttl(end), make, notes, force=force, stamps=stamps)
