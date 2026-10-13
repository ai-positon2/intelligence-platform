"""Paid Marketing Agents: Segment Analysis, the numbers.

The PPC team's segment report (one tab each for day of the week, hour of the
day, age, gender, city, device and network, every tab with the same columns)
used to be built by hand from Supermetrics pulls and pivot tables. This builds
the same report from one Google Ads account and one date range:

    Segment | Impressions | Clicks | Cost | % Cost | CTR | CPC | Conversions | CPL

and, like the sheet, a second cut by campaign type (Brand, Non Brand,
Performance Max, Demand Gen, ...). Metric rules are the sheet's:

    CTR = clicks / impressions     CPC = cost / clicks     CPL = cost / conversions
    % Cost = the row's cost / the table's total cost       CPL is None ("Na") at 0 conversions

One thing the sheet never said out loud and this does: Google Ads does not
report every segment for every campaign type (Performance Max has no age or
gender, for instance), so the age, gender and city tables cover less spend than
the device table. Each table carries its `coverage` (its spend over the
account's spend) so nobody compares totals that are not comparable.

This module only builds the report. The rules that read it for insights are in
tracker/paid_segment_insights.py.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads as gads

MAX_DAYS = 400
MIN_DATE = dt.date(2015, 1, 1)
CACHE_TTL_S = 15 * 60
CACHE_MAX = 64
ALL = "All"


class BadRequest(ValueError):
    pass


# ── Reading the API's rows ──────────────────────────────────────────────────

def _get(row, *path):
    for p in path:
        row = (row or {}).get(p) if isinstance(row, dict) else None
    return row


def _title(enum):
    return str(enum or "").replace("_", " ").title().strip() or "Unknown"


DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
AGES = ["AGE_RANGE_18_24", "AGE_RANGE_25_34", "AGE_RANGE_35_44", "AGE_RANGE_45_54",
        "AGE_RANGE_55_64", "AGE_RANGE_65_UP", "AGE_RANGE_UNDETERMINED"]
AGE_LABEL = {"AGE_RANGE_65_UP": "65+", "AGE_RANGE_UNDETERMINED": "Undetermined"}
GENDERS = ["FEMALE", "MALE", "UNDETERMINED"]
# The names Google Ads' own UI (and so the team's sheets) use.
DEVICE_LABEL = {
    "DESKTOP": "Computers", "MOBILE": "Mobile devices with full browsers",
    "TABLET": "Tablets with full browsers", "CONNECTED_TV": "Devices streaming video content to TV screens",
    "OTHER": "Other", "UNKNOWN": "Unknown"}
NETWORK_LABEL = {
    "SEARCH": "Search Network", "SEARCH_PARTNERS": "Search Partners", "CONTENT": "Display Network",
    "MIXED": "Cross-network", "YOUTUBE": "YouTube", "YOUTUBE_SEARCH": "YouTube Search",
    "YOUTUBE_WATCH": "YouTube Videos", "GOOGLE_TV": "Google TV", "DISCOVER": "Discover",
    "GMAIL": "Gmail", "UNKNOWN": "Unknown"}


def hour_label(h):
    h = int(h) % 24
    return "%d %s" % (h % 12 or 12, "AM" if h < 12 else "PM")


def _x_day(row):
    v = _get(row, "segments", "dayOfWeek")
    return (DAYS.index(v) if v in DAYS else 99, v.title() if v in DAYS else _title(v), "")


def _x_hour(row):
    v = _get(row, "segments", "hour")
    if v is None:
        return (99, "Unknown", "")
    return (int(v), hour_label(v), "")


def _x_age(row):
    v = _get(row, "adGroupCriterion", "ageRange", "type")
    label = AGE_LABEL.get(v) or (v or "").replace("AGE_RANGE_", "").replace("_", "-") or "Unknown"
    return (AGES.index(v) if v in AGES else 99, label, "")


def _x_gender(row):
    v = _get(row, "adGroupCriterion", "gender", "type")
    return (GENDERS.index(v) if v in GENDERS else 99, _title(v), "")


def _x_city(row):
    # The key is the geo resource name; the label is resolved afterwards.
    v = _get(row, "segments", "geoTargetCity") or ""
    return (v, v, "")


def _x_device(row):
    v = _get(row, "segments", "device")
    return (v or "", DEVICE_LABEL.get(v) or _title(v), "")


def _x_network(row):
    v = _get(row, "segments", "adNetworkType")
    return (v or "", NETWORK_LABEL.get(v) or _title(v), "")


_METRICS = "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions"
_CAMPAIGN = "campaign.name, campaign.advertising_channel_type"

# key, label, FROM, the segment fields to select, row reader, row order
# ("natural": the reader's sort key, "cost": biggest spender first)
DIMENSIONS = [
    {"key": "day", "label": "Day of the week", "from": "campaign",
     "select": "segments.day_of_week", "read": _x_day, "order": "natural"},
    {"key": "hour", "label": "Hour of the day", "from": "campaign",
     "select": "segments.hour", "read": _x_hour, "order": "natural"},
    {"key": "age", "label": "Age", "from": "age_range_view",
     "select": "ad_group_criterion.age_range.type", "read": _x_age, "order": "natural"},
    {"key": "gender", "label": "Gender", "from": "gender_view",
     "select": "ad_group_criterion.gender.type", "read": _x_gender, "order": "natural"},
    {"key": "city", "label": "Location (city)", "from": "user_location_view",
     "select": "segments.geo_target_city", "read": _x_city, "order": "cost"},
    {"key": "device", "label": "Device", "from": "campaign",
     "select": "segments.device", "read": _x_device, "order": "cost"},
    {"key": "network", "label": "Network", "from": "campaign",
     "select": "segments.ad_network_type", "read": _x_network, "order": "cost"},
]


def _x_account(row):
    name = row.get("_account") or "Unknown account"
    return (name, name, "")


# Only an MCC view has this table: the same campaign-level rows as the day table,
# cut by which of the MCC's accounts they came from.
ACCOUNT_DIM = {"key": "account", "label": "Account", "from": "campaign", "select": "campaign.name",
               "read": _x_account, "order": "cost"}
DIM_BY_KEY = dict({d["key"]: d for d in DIMENSIONS}, account=ACCOUNT_DIM)
MAX_MEMBERS = 40


def gaql(dim, start, end):
    return "SELECT %s, %s, %s FROM %s WHERE segments.date BETWEEN '%s' AND '%s'" % (
        _CAMPAIGN, dim["select"], _METRICS, dim["from"], start.isoformat(), end.isoformat())


# ── Campaign type ───────────────────────────────────────────────────────────
# The sheet had a lookup tab (campaign name -> type) kept by hand. Google Ads
# already knows the channel; only a Search campaign needs the name, to tell
# Brand from Non Brand.

_NON_BRAND = re.compile(r"non[\s_\-]*brand", re.I)
_BRAND = re.compile(r"brand", re.I)
_CHANNEL_TYPE = {
    "PERFORMANCE_MAX": "Performance Max", "DEMAND_GEN": "Demand Gen", "DISCOVERY": "Demand Gen",
    "DISPLAY": "Display", "VIDEO": "Video", "SHOPPING": "Shopping", "LOCAL": "Local",
    "SMART": "Smart", "MULTI_CHANNEL": "App", "LOCAL_SERVICES": "Local Services",
    "TRAVEL": "Travel", "HOTEL": "Hotel",
}


def campaign_type(name, channel):
    channel = str(channel or "")
    if channel == "SEARCH":
        name = str(name or "")
        if _NON_BRAND.search(name):
            return "Non Brand"
        return "Brand" if _BRAND.search(name) else "Non Brand"
    return _CHANNEL_TYPE.get(channel) or _title(channel)


# ── Metrics ─────────────────────────────────────────────────────────────────

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _zero():
    return {"imp": 0.0, "clk": 0.0, "cost": 0.0, "conv": 0.0}


def _add(into, other):
    for k in into:
        into[k] += other[k]


def _is_empty(m):
    return not (m["imp"] or m["clk"] or m["cost"] or m["conv"])


def _div(a, b):
    return a / b if b else None


def derive(m, total_cost):
    """The sheet's columns for one set of sums."""
    cost = round(m["cost"], 2)
    conv = round(m["conv"], 2)
    return {
        "impressions": int(round(m["imp"])), "clicks": int(round(m["clk"])), "cost": cost,
        "pctCost": _div(m["cost"], total_cost), "ctr": _div(m["clk"], m["imp"]),
        "cpc": None if not m["clk"] else round(m["cost"] / m["clk"], 4),
        "conversions": conv, "cpl": None if m["conv"] <= 0 else round(m["cost"] / m["conv"], 2),
    }


def _read(rows, dim):
    """{seg_key: {sort, label, sub, all, types}} from one query's rows. A row
    nothing happened on (an ad group's untouched age bucket) is dropped."""
    segs = {}
    for row in rows:
        m = _get(row, "metrics") or {}
        vals = {"imp": _num(m.get("impressions")), "clk": _num(m.get("clicks")),
                "cost": _num(m.get("costMicros")) / 1e6, "conv": _num(m.get("conversions"))}
        if _is_empty(vals):
            continue
        sort, label, sub = dim["read"](row)
        ctype = campaign_type(_get(row, "campaign", "name"), _get(row, "campaign", "advertisingChannelType"))
        seg = segs.setdefault(sort if dim["key"] == "city" else label,
                              {"sort": sort, "label": label, "sub": sub, "all": _zero(), "types": {}})
        _add(seg["all"], vals)
        _add(seg["types"].setdefault(ctype, _zero()), vals)
    return segs


def _name_cities(segs, geo_fn):
    """Replace each city's geo id with its name; two places that share a name
    (Springfield) are told apart by their state."""
    names = geo_fn([k for k in segs if k])
    for key, seg in segs.items():
        info = names.get(key) or {}
        seg["label"] = info.get("name") or ("Not city-level" if not key else "City " + key.split("/")[-1])
        seg["sub"] = info.get("canonical") or ""
    count = {}
    for seg in segs.values():
        count[seg["label"]] = count.get(seg["label"], 0) + 1
    for seg in segs.values():
        if count[seg["label"]] > 1 and seg["sub"]:
            parts = [p.strip() for p in seg["sub"].split(",")]
            seg["label"] = "%s, %s" % (seg["label"], parts[1] if len(parts) > 1 else parts[0])


def _view(segs, dim, ctype, account_cost):
    """One table: every segment's row for `ctype` (ALL for no filter)."""
    picked = []
    for seg in segs.values():
        m = seg["all"] if ctype == ALL else seg["types"].get(ctype)
        if m and not _is_empty(m):
            picked.append((seg, m))
    total = _zero()
    for _seg, m in picked:
        _add(total, m)
    if dim["order"] == "cost":
        picked.sort(key=lambda p: (-p[1]["cost"], p[0]["label"]))
    else:
        picked.sort(key=lambda p: (p[0]["sort"], p[0]["label"]))
    rows = []
    for seg, m in picked:
        row = derive(m, total["cost"])
        row.update(key=str(seg["sort"]), label=seg["label"], sub=seg["sub"])
        rows.append(row)
    return {"rows": rows, "total": derive(total, total["cost"]),
            "coverage": _div(total["cost"], account_cost)}


def parse_range(start, end):
    try:
        s, e = dt.date.fromisoformat(str(start)), dt.date.fromisoformat(str(end))
    except ValueError:
        raise BadRequest("The dates should be YYYY-MM-DD.")
    if s > e:
        raise BadRequest("The start date is after the end date.")
    if s < MIN_DATE:
        raise BadRequest("That start date is too far back.")
    if (e - s).days + 1 > MAX_DAYS:
        raise BadRequest("That range is over %d days." % MAX_DAYS)
    return s, e


def build_report(account, start, end, *, search_fn=None, geo_fn=None, workers=12):
    """The whole report for one account, or for an MCC's accounts together.
    `account` is {id, name, currency} and, for an MCC, `members`: the accounts
    [{id, name, timeZone}] whose rows are added up. A segment whose query fails
    is reported with its error and the rest still come back; if the day query
    fails everywhere, or every one does, the failure is raised: there is
    nothing to show. When only some of an MCC's accounts fail, the report says
    which, because its totals are then short."""
    search_fn = search_fn or gads.search
    geo_fn = geo_fn or gads.geo_names
    members = account.get("members") or [{"id": account["id"], "name": account["name"]}]
    is_group = bool(account.get("members"))

    def run(task):
        member, dim = task
        try:
            rows = search_fn(member["id"], gaql(dim, start, end))
            return member, dim, [dict(r, _account=member["name"]) for r in rows], None
        except gads.GadsError as e:
            return member, dim, None, e

    tasks = [(m, d) for m in members for d in DIMENSIONS]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks)))) as ex:
        done = list(ex.map(run, tasks))
    rows_by_dim, errors_by_dim, failed = {}, {}, {}
    for member, dim, rows, err in done:
        if err:
            errors_by_dim.setdefault(dim["key"], []).append(err)
            failed.setdefault(member["name"], str(err))
        else:
            rows_by_dim.setdefault(dim["key"], []).extend(rows)
    if not rows_by_dim:
        raise next(iter(e for _m, _d, _r, e in done if e))
    if "day" not in rows_by_dim:
        raise errors_by_dim["day"][0]
    read = {k: _read(rows, DIM_BY_KEY[k]) for k, rows in rows_by_dim.items()}
    base = read["day"]

    # The day table is a campaign-level cut, so it holds the whole spend: it is
    # what every other table's coverage is measured against.
    acct = {ALL: _zero()}
    for sg in base.values():
        _add(acct[ALL], sg["all"])
        for t, m in sg["types"].items():
            _add(acct.setdefault(t, _zero()), m)
    types = [t for t, _m in sorted(((t, m) for t, m in acct.items() if t != ALL and not _is_empty(m)),
                                   key=lambda p: -p[1]["cost"])]

    def entry_for(dim, segs, err):
        entry = {"key": dim["key"], "label": dim["label"], "error": None, "views": {}}
        if segs is None:
            entry["error"] = str(err)
            return entry
        if dim["key"] == "city":
            try:
                _name_cities(segs, geo_fn)
            except gads.GadsError as e:
                for key, sg in segs.items():
                    sg["label"] = "Not city-level" if not key else "City " + key.split("/")[-1]
                entry["note"] = "City names could not be looked up (%s)." % e
        for t in [ALL] + types:
            entry["views"][t] = _view(segs, dim, t, acct[t]["cost"])
        if err is not None:
            entry["note"] = "Some accounts could not be read for this table, so it is short."
        return entry

    segments = []
    if is_group:
        segments.append(entry_for(ACCOUNT_DIM, _read(rows_by_dim["day"], ACCOUNT_DIM), None))
    for dim in DIMENSIONS:
        errs = errors_by_dim.get(dim["key"])
        segments.append(entry_for(dim, read.get(dim["key"]), errs[0] if errs else None))

    days = (end - start).days + 1
    zones = sorted({m.get("timeZone") for m in members if m.get("timeZone")})
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD",
                    "isGroup": is_group, "accountCount": len(members),
                    "accounts": [m["name"] for m in members] if is_group else []},
        "range": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "types": types,
        "total": derive(acct[ALL], acct[ALL]["cost"]),
        "segments": segments,
        "warnings": ["%s could not be read: %s" % (n, why) for n, why in sorted(failed.items())],
        "timeZones": zones,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


# ── Cache + the entry point the route uses ──────────────────────────────────
# Explorer access is 2,880 operations a day and one report is about eight, so
# the same account and range is fetched once per CACHE_TTL_S, however many
# people ask.

_cache = {}
_cache_lock = threading.Lock()
_inflight = {}


def clear_cache():
    with _cache_lock:
        _cache.clear()
        _inflight.clear()


def resolve_target(target, *, accounts_fn=None, groups_fn=None):
    """{id, name, currency[, members]} for a 10-digit account id or a group id
    ("mcc-<id>" or "mcc-<id>-<CUR>") the root MCC actually holds."""
    target = str(target or "").strip()
    if gads.GROUP_ID_RE.match(target):
        group = next((g for g in (groups_fn or gads.list_groups)() if g["id"] == target), None)
        if not group:
            raise BadRequest("That MCC is not one of Position2's Google Ads MCCs.")
        by_id = {a["id"]: a for a in (accounts_fn or gads.list_accounts)()}
        members = [by_id[i] for i in group["accountIds"] if i in by_id]
        if len(members) > MAX_MEMBERS:
            raise BadRequest("That MCC has %d accounts, more than the %d this reads at once." % (len(members), MAX_MEMBERS))
        return {"id": group["id"], "name": group["name"], "currency": group["currency"], "members": members}
    digits = re.sub(r"\D", "", target)
    account = next((a for a in (accounts_fn or gads.list_accounts)() if a["id"] == digits), None)
    if not account:
        raise BadRequest("That account is not one of Position2's Google Ads accounts.")
    return account


def report_for(account_id, start, end, *, accounts_fn=None, groups_fn=None, search_fn=None, geo_fn=None, now=None):
    """build_report for an account or MCC the root MCC actually holds, cached."""
    s, e = parse_range(start, end)
    account = resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
    key = (account["id"], s, e)
    clock = time.time() if now is None else now
    with _cache_lock:
        hit = _cache.get(key)
        if hit and clock - hit[0] < CACHE_TTL_S:
            return hit[1]
        lock = _inflight.setdefault(key, threading.Lock())
    with lock:                       # two people asking at once share one fetch
        with _cache_lock:
            hit = _cache.get(key)
            if hit and clock - hit[0] < CACHE_TTL_S:
                return hit[1]
        report = build_report(account, s, e, search_fn=search_fn, geo_fn=geo_fn)
        with _cache_lock:
            _cache[key] = (clock, report)
            while len(_cache) > CACHE_MAX:
                _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
            _inflight.pop(key, None)
    return report
