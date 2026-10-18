"""Paid Marketing Agents: Geo Performance, the numbers.

Performance by location for a Google Ads account (or an MCC's accounts together), a Meta ad
account, or a Google and a Meta account added together, at one location level at a time:

    Google   country, region (state), metro area, city, county, postal code
    Meta     country, region, metro area (DMA)
    Both     country, region (the two platforms' other levels do not line up)

Google's numbers come from the "user location" report (where the person was, the report Segment
Analysis already uses for cities) and carry conversion value, so ROAS exists when the account
tracks value. Each row has the metrics a PPC team reads (impressions, clicks, cost, conversions,
value, CTR, CPC, conversion rate, CPA, ROAS) and its share of the spend and of the conversions.

The report is cut by campaign type (Brand, Non Brand, Performance Max, Meta Sales...) the way
Segment Analysis is, and carries its *coverage*: the spend that has a location at this level over
the account's spend, because Google does not report a location for every campaign type and nobody
should read a table as the whole account when it is not.

What the numbers mean (efficiency against the account's own average, suggested bid adjustments,
what to do) is tracker/paid_geo_insights.py. This module reads, adds up and caches.

Reading is split from assembling on purpose. What is cached is the *raw* read of one platform
(places and their sums); the report is assembled from it every time, so a Google and a Meta half
that were saved separately can be added without asking either platform again.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads as base
from . import paid_gads_segments as gads
from . import paid_geo_insights as insights
from . import paid_segments as seg

ALL = seg.ALL
MAX_MEMBERS = seg.MAX_MEMBERS
CACHE_TTL_S = 60
CACHE_MAX = 64

GOOGLE_LEVELS = [("country", "Country"), ("region", "Region"), ("metro", "Metro area"), ("city", "City"),
                 ("county", "County"), ("postal_code", "Postal code")]
META_LEVELS = [("country", "Country"), ("region", "Region"), ("dma", "Metro area (DMA)")]
BOTH_LEVELS = [("country", "Country"), ("region", "Region")]
LEVELS = {"google": GOOGLE_LEVELS, "meta": META_LEVELS, "both": BOTH_LEVELS}
SEGMENT_FIELD = {"region": "geoTargetRegion", "metro": "geoTargetMetro", "city": "geoTargetCity",
                 "county": "geoTargetCounty", "postal_code": "geoTargetPostalCode"}
META_BREAKDOWN = {"country": "country", "region": "region", "dma": "dma"}
PLATFORM_NAME = {"google": "Google Ads", "meta": "Meta Ads"}


BadRequest = seg.BadRequest                 # one error for a bad range, a bad level or a bad account: the route answers 400


def levels_for(platform):
    return LEVELS.get(platform) or GOOGLE_LEVELS


def check_level(level, platform):
    if level not in [k for k, _ in levels_for(platform)]:
        raise BadRequest("%s cannot be cut by %s. Pick one of: %s." % (
            {"google": "Google Ads", "meta": "Meta Ads", "both": "Google Ads and Meta Ads together"}.get(platform, "That account"),
            str(level)[:30], ", ".join(k.replace("_", " ") for k, _ in levels_for(platform))))
    return level


# ── Sums ────────────────────────────────────────────────────────────────────

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def zero():
    return {"imp": 0.0, "clk": 0.0, "cost": 0.0, "conv": 0.0, "val": 0.0}


def add(into, other):
    for k in into:
        into[k] += other.get(k, 0.0)


def is_empty(m):
    return not (m["imp"] or m["clk"] or m["cost"] or m["conv"])


def _div(a, b):
    return a / b if b else None


def derive(m, total_cost, total_conv):
    """The columns for one set of sums."""
    cost, conv, val = round(m["cost"], 2), round(m["conv"], 2), round(m["val"], 2)
    return {
        "impressions": int(round(m["imp"])), "clicks": int(round(m["clk"])), "cost": cost, "conversions": conv, "value": val,
        "ctr": _div(m["clk"], m["imp"]), "cpc": None if not m["clk"] else round(m["cost"] / m["clk"], 4),
        "cvr": _div(m["conv"], m["clk"]), "cpa": None if m["conv"] <= 0 else round(m["cost"] / m["conv"], 2),
        "roas": None if not m["cost"] or m["val"] <= 0 else round(m["val"] / m["cost"], 3),
        "pctCost": _div(m["cost"], total_cost), "pctConv": _div(m["conv"], total_conv),
    }


# ── Google Ads ──────────────────────────────────────────────────────────────

_METRICS = "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value"
_CAMPAIGN = "campaign.name, campaign.advertising_channel_type"


def gaql(level, start, end):
    where = "WHERE segments.date BETWEEN '%s' AND '%s'" % (start.isoformat(), end.isoformat())
    if level == "country":
        return "SELECT %s, user_location_view.country_criterion_id, %s FROM user_location_view %s" % (_CAMPAIGN, _METRICS, where)
    field = "segments.geo_target_" + level
    return "SELECT %s, %s, %s FROM user_location_view %s" % (_CAMPAIGN, field, _METRICS, where)


def totals_gaql(start, end):
    return "SELECT %s, %s FROM campaign WHERE segments.date BETWEEN '%s' AND '%s'" % (
        _CAMPAIGN, _METRICS, start.isoformat(), end.isoformat())


def _g_vals(row):
    m = (row or {}).get("metrics") or {}
    return {"imp": _num(m.get("impressions")), "clk": _num(m.get("clicks")), "cost": _num(m.get("costMicros")) / 1e6,
            "conv": _num(m.get("conversions")), "val": _num(m.get("conversionsValue"))}


def _g_type(row):
    c = (row or {}).get("campaign") or {}
    return seg.campaign_type(c.get("name"), c.get("advertisingChannelType"))


def _g_key(row, level):
    if level == "country":
        cid = ((row.get("userLocationView") or {}).get("countryCriterionId"))
        return "geoTargetConstants/%s" % cid if cid else ""
    return (row.get("segments") or {}).get(SEGMENT_FIELD[level]) or ""


def read_google(rows, level):
    """{place key: {all, types}} from a location query's rows. A row nothing happened on is dropped."""
    segs = {}
    for row in rows:
        vals = _g_vals(row)
        if is_empty(vals):
            continue
        s = segs.setdefault(_g_key(row, level), {"all": zero(), "types": {}})
        add(s["all"], vals)
        add(s["types"].setdefault(_g_type(row), zero()), vals)
    return segs


def read_google_totals(rows):
    """{campaign type: sums} for the account, the spend every table's coverage is measured against."""
    out = {}
    for row in rows:
        vals = _g_vals(row)
        if not is_empty(vals):
            add(out.setdefault(_g_type(row), zero()), vals)
    return out


def _fold(into, other):
    """Add one read's places (and types) into another's."""
    for key, s in other.items():
        d = into.setdefault(key, {"all": zero(), "types": {}})
        add(d["all"], s["all"])
        for t, m in s["types"].items():
            add(d["types"].setdefault(t, zero()), m)


def _split_canonical(canonical):
    return [p.strip() for p in str(canonical or "").split(",") if p.strip()]


def name_places(segs, level, geo_fn):
    """Give each place its name. Returns {key: (label, sub)}: `sub` is the place's parents (state, country). Two places that
    share a name (Springfield, or a postal code in two states) are told apart by their first parent; a key Google would not
    name is shown as what it is."""
    keys = [k for k in segs if k]
    names = geo_fn(keys) if keys else {}
    info, count = {}, {}
    for key in segs:
        if not key:
            label, parents = "Not reported at this level", []
        else:
            n = names.get(key) or {}
            label = n.get("name") or ("Place " + key.split("/")[-1])
            parts = _split_canonical(n.get("canonical"))
            parents = parts[1:] if parts and parts[0] == label else parts
        info[key] = (label, parents)
        count[label] = count.get(label, 0) + 1
    out = {}
    for key, (label, parents) in info.items():
        shown = "%s, %s" % (label, parents[0]) if count[label] > 1 and parents else label
        out[key] = (shown, ", ".join(parents))
    return out


def google_raw(account, level, start, end, *, search_fn=None, geo_fn=None, workers=8):
    """The raw read of one Google account (or an MCC's accounts added together) at one level:
    {platform, level, places: {key: {label, sub, all, types}}, totals: {type: sums}, warnings, timeZones, members}.
    An account that cannot be read is left out and said so; every one failing is raised."""
    search_fn = search_fn or gads.search
    geo_fn = geo_fn or gads.geo_names
    members = account.get("members") or [{"id": account["id"], "name": account["name"], "timeZone": account.get("timeZone")}]

    def run(task):
        member, kind = task
        try:
            q = gaql(level, start, end) if kind == "places" else totals_gaql(start, end)
            return member, kind, search_fn(member["id"], q), None
        except gads.GadsError as e:
            return member, kind, None, e

    tasks = [(m, k) for m in members for k in ("places", "totals")]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks)))) as ex:
        done = list(ex.map(run, tasks))
    failed, segs, totals, ok = {}, {}, {}, 0
    for member, kind, rows, err in done:
        if err:
            failed.setdefault(member["name"], str(err))
            continue
        if kind == "places":
            _fold(segs, read_google(rows, level))
            ok += 1
        else:
            for t, m in read_google_totals(rows).items():
                add(totals.setdefault(t, zero()), m)
    if not ok:
        raise next(e for _m, _k, _r, e in done if e)
    try:
        labels = name_places(segs, level, geo_fn)
        note = ""
    except gads.GadsError as e:
        labels = {k: (("Not reported at this level" if not k else "Place " + k.split("/")[-1]), "") for k in segs}
        note = "Place names could not be looked up (%s)." % e
    places = {k: dict(v, label=labels[k][0], sub=labels[k][1]) for k, v in segs.items()}
    warnings = ["%s could not be read: %s" % (n, why) for n, why in sorted(failed.items())]
    if note:
        warnings.append(note)
    zones = sorted({m.get("timeZone") for m in members if m.get("timeZone")})
    return {"platform": "google", "level": level, "places": places, "totals": totals, "warnings": warnings, "timeZones": zones,
            "members": [m["name"] for m in members] if account.get("members") else []}


# ── Meta ────────────────────────────────────────────────────────────────────

COUNTRY_NAMES = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom", "AU": "Australia", "NZ": "New Zealand", "IE": "Ireland", "IN": "India",
    "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy", "NL": "Netherlands", "BE": "Belgium", "CH": "Switzerland", "AT": "Austria",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PL": "Poland", "PT": "Portugal", "CZ": "Czechia", "GR": "Greece",
    "TR": "Turkey", "IL": "Israel", "AE": "United Arab Emirates", "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait", "EG": "Egypt",
    "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya", "MX": "Mexico", "BR": "Brazil", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
    "PE": "Peru", "JP": "Japan", "KR": "South Korea", "CN": "China", "HK": "Hong Kong", "TW": "Taiwan", "SG": "Singapore", "MY": "Malaysia",
    "TH": "Thailand", "VN": "Vietnam", "PH": "Philippines", "ID": "Indonesia", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka",
    "RU": "Russia", "UA": "Ukraine", "RO": "Romania", "HU": "Hungary", "BG": "Bulgaria", "HR": "Croatia", "RS": "Serbia", "SK": "Slovakia",
    "SI": "Slovenia", "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia", "IS": "Iceland", "LU": "Luxembourg", "MT": "Malta", "CY": "Cyprus",
    "PR": "Puerto Rico", "DO": "Dominican Republic", "CR": "Costa Rica", "PA": "Panama", "EC": "Ecuador", "UY": "Uruguay", "VE": "Venezuela",
    "MA": "Morocco", "DZ": "Algeria", "TN": "Tunisia", "GH": "Ghana", "ET": "Ethiopia", "TZ": "Tanzania", "UG": "Uganda", "JO": "Jordan",
    "LB": "Lebanon", "OM": "Oman", "BH": "Bahrain", "NP": "Nepal", "KZ": "Kazakhstan",
}


def _m_label(row, level):
    v = str(row.get(META_BREAKDOWN[level]) or "").strip()
    if level == "country":
        return COUNTRY_NAMES.get(v.upper(), v or "Unknown"), ""
    return v or "Unknown", ""


def meta_query(level, start, end):
    from . import paid_meta
    return {"level": "campaign", "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
            "fields": ",".join(paid_meta.INSIGHT_FIELDS), "breakdowns": META_BREAKDOWN[level], "limit": paid_meta.PAGE_LIMIT}


def meta_raw(account, level, start, end, *, store=None, fetch=None):
    """The raw read of one Meta ad account at one level (same shape as google_raw). Meta's breakdown rows cover all the spend
    that has a location, so the account's totals are the sum of the rows."""
    from . import paid_meta, paid_meta_segments, paid_store
    store = store or paid_store
    try:
        rows = fetch(account["id"], level, meta_query(level, start, end)) if fetch else \
            paid_meta._pages(account["id"] + "/insights", meta_query(level, start, end), store)
    except base.AdsError as e:
        raise gads.translate(e) from e
    places, totals = {}, {}
    for r in rows:
        n = paid_meta.numbers(r)
        vals = {"imp": float(n[0]), "clk": float(n[1]), "cost": float(n[2]), "conv": float(n[3]), "val": float(n[4])}
        if is_empty(vals):
            continue
        label, sub = _m_label(r, level)
        key = "meta:" + label
        ctype = paid_meta_segments.campaign_type(r.get("objective"))
        s = places.setdefault(key, {"label": label, "sub": sub, "all": zero(), "types": {}})
        add(s["all"], vals)
        add(s["types"].setdefault(ctype, zero()), vals)
        add(totals.setdefault(ctype, zero()), vals)
    return {"platform": "meta", "level": level, "places": places, "totals": totals, "warnings": [],
            "timeZones": [account["tz"]] if account.get("tz") else [], "members": []}


# ── Adding a Google and a Meta read ─────────────────────────────────────────

def _place_norm(label):
    s = re.sub(r"\b(state|province|region|county|of)\b", " ", str(label or "").lower())
    return re.sub(r"[^a-z0-9]+", "", s)


def _copy_place(p):
    """A place with its own sums: a read comes out of the cache shared, so adding two reads must never change either."""
    return dict(p, all=dict(p["all"]), types={t: dict(v) for t, v in p["types"].items()})


def merge_raw(g, m):
    """One raw read from a Google and a Meta one of the same level and range: a place with the same name on both is one row,
    a place only one platform has stays a row. Each place keeps its sums per platform. Neither input is changed."""
    places, by_name = {}, {}
    for key, p in g["places"].items():
        d = _copy_place(p)
        d["plat"] = {"google": dict(p["all"])}
        places[key] = d
        by_name[_place_norm(p["label"])] = key
    for key, p in m["places"].items():
        hit = by_name.get(_place_norm(p["label"]))
        if hit:
            d = places[hit]
            add(d["all"], p["all"])
            for t, v in p["types"].items():
                add(d["types"].setdefault(t, zero()), v)
            d["plat"]["meta"] = dict(p["all"])
        else:
            d = _copy_place(p)
            d["plat"] = {"meta": dict(p["all"])}
            places["meta:" + key] = d
    totals = {}
    for src in (g["totals"], m["totals"]):
        for t, v in src.items():
            add(totals.setdefault(t, zero()), v)
    return {"platform": "both", "level": g["level"], "places": places, "totals": totals,
            "warnings": list(g["warnings"]) + list(m["warnings"]), "timeZones": sorted(set(g["timeZones"]) | set(m["timeZones"])),
            "members": g.get("members") or [], "readAt": min(filter(None, (g.get("readAt"), m.get("readAt"))), default=None)}


# ── Assembling a report from a raw read ─────────────────────────────────────

RESULT_TYPES = ("Meta Sales", "Meta Leads", "Meta App promotion", "Meta Other")      # the Meta campaign types whose "conversion" is a sale, a lead or an install


def _mixed_results(totals):
    """True when two or more kinds of result are added together: a sale, a lead and an install are not the same thing, so the places of the whole account
    cannot be ranked on them (Meta's traffic, awareness and engagement campaigns have no conversion and are not counted here)."""
    return sum(1 for t in RESULT_TYPES if (totals.get(t) or {}).get("cost", 0) > 0) >= 2


def _view(places, ctype, account_cost, account_conv):
    picked = []
    for key, p in places.items():
        m = p["all"] if ctype == ALL else p["types"].get(ctype)
        if m and not is_empty(m):
            picked.append((key, p, m))
    total = zero()
    for _k, _p, m in picked:
        add(total, m)
    picked.sort(key=lambda kpm: (-kpm[2]["cost"], kpm[1]["label"]))
    rows = []
    for key, p, m in picked:
        row = derive(m, total["cost"], total["conv"])
        row.update(key=key, label=p["label"], sub=p.get("sub") or "")
        if p.get("plat"):
            row["platforms"] = {pl: derive(v, total["cost"], total["conv"]) for pl, v in p["plat"].items()
                                if ctype == ALL and not is_empty(v)}
        rows.append(row)
    tot = derive(total, total["cost"], total["conv"])
    return {"rows": rows, "total": tot, "coverage": _div(total["cost"], account_cost)}


def assemble(raw, account, start, end):
    """The report the page reads, from one raw read: a table per campaign type, each judged against its own average."""
    totals = raw["totals"]
    acct = zero()
    for m in totals.values():
        add(acct, m)
    types = [t for t, m in sorted(totals.items(), key=lambda p: -p[1]["cost"]) if not is_empty(m)]
    # a type the places have but the totals do not (should not happen) is still offered
    seen = {t for p in raw["places"].values() for t in p["types"]}
    types += sorted(t for t in seen if t not in types)
    views = {}
    platform = raw["platform"]
    for t in [ALL] + types:
        cost = acct["cost"] if t == ALL else totals.get(t, zero())["cost"]
        conv = acct["conv"] if t == ALL else totals.get(t, zero())["conv"]
        v = _view(raw["places"], t, cost, conv)
        v["account"] = derive(acct if t == ALL else totals.get(t, zero()), cost or 0, conv or 0)
        views[t] = insights.analyse(v, level=raw["level"], currency=account.get("currency") or "USD", platform=platform,
                                    mixed=t == ALL and platform == "meta" and _mixed_results(totals))
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": platform,
                    "isGroup": bool(raw.get("members")), "accountCount": len(raw.get("members") or []) or 1, "accounts": raw.get("members") or []},
        "range": {"from": start.isoformat(), "to": end.isoformat(), "days": (end - start).days + 1},
        "level": raw["level"], "levels": [{"key": k, "label": l} for k, l in levels_for(platform)],
        "types": types, "views": views, "warnings": raw.get("warnings") or [], "timeZones": raw.get("timeZones") or [],
        "generatedAt": raw.get("readAt") or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),         # when the numbers were read, not when this page was built
    }


# ── The entry point the route uses ──────────────────────────────────────────
# One level of one platform is a few reads; it is saved in Postgres (shared by every worker, kept across restarts) and read from
# the platform again only when older than paid_gads._ttl(end) or someone presses Refresh. A minute in memory sits in front of
# that. When the platform cannot answer, the last saved read is served with its date in `stale`.

_cache = {}
_cache_lock = threading.Lock()
_inflight = {}


def clear_cache():
    with _cache_lock:
        _cache.clear()
        _inflight.clear()


def _cached(kind, key_id, level, s, e, build, *, force, store, now):
    key = (kind, key_id, level, s, e)
    pkey = base.CACHE_V + "geo:%s:%s:%s:%s:%s" % (kind, key_id, level, s.isoformat(), e.isoformat())
    ttl = base._ttl(e.isoformat())
    clock = time.time() if now is None else now
    if force:
        base.refresh_guard(store, "geo:%s:%s" % (kind, key_id))
        with _cache_lock:
            _cache.pop(key, None)
        base.forget(pkey)
    else:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and clock - hit[0] < CACHE_TTL_S:
                return hit[1], None
    lock = _inflight.setdefault(key, threading.Lock())
    with lock:                       # two people asking at once share one read
        if not force:
            with _cache_lock:
                hit = _cache.get(key)
                if hit and clock - hit[0] < CACHE_TTL_S:
                    return hit[1], None
            saved = base._entry(store, pkey, ttl)
            if saved is not None:
                with _cache_lock:
                    _cache[key] = (clock, saved[0])
                return saved[0], None
        try:
            raw = dict(build(), readAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))      # when the platform was asked, kept with the read
        except gads.GadsError as err:
            old = base._saved_copy(store, pkey) if err.kind == "quota" else None
            if old is None:
                raise
            return old[0], old[1]
        if not raw.get("warnings"):                       # a read that lost a part is shown, but not kept for hours: the next ask reads again
            try:
                store.put_gads_cache(pkey, raw)
            except Exception as err:                      # noqa: BLE001 (a failed save never costs the report)
                base.log.warning("geo: could not save the read (%s)", err)
            base._mem_put(pkey, raw, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        with _cache_lock:
            _cache[key] = (clock, raw)
            while len(_cache) > CACHE_MAX:
                _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
            _inflight.pop(key, None)
    return raw, None


def _google_target(account_id, accounts_fn, groups_fn):
    return seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)


def _raw_for(raw_id, level, s, e, *, force, store, now, accounts_fn, groups_fn, search_fn, geo_fn, meta_fetch):
    """(raw read, account, stale) for one Google account / MCC group or one Meta account."""
    from . import paid_meta
    if paid_meta.is_meta(raw_id):
        account = seg._meta_target(raw_id, store)
        raw, stale = _cached("meta", paid_meta.day_key(account["id"]), level, s, e,
                             lambda: meta_raw(account, level, s, e, store=store, fetch=meta_fetch), force=force, store=store, now=now)
    else:
        account = _google_target(raw_id, accounts_fn, groups_fn)
        raw, stale = _cached("google", account["id"], level, s, e,
                             lambda: google_raw(account, level, s, e, search_fn=search_fn, geo_fn=geo_fn), force=force, store=store, now=now)
    return raw, account, stale


def report_for(account_id, level, start, end, *, accounts_fn=None, groups_fn=None, search_fn=None, geo_fn=None, now=None,
               force=False, store=None, meta_fetch=None):
    """The report for an account or MCC the root MCC holds, a Meta ad account (act_...), or a Google and a Meta account together
    ("<google id>+<act_ id>"), at one location level. `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_meta, paid_store
    store = store or paid_store
    raw_id = str(account_id or "").strip()
    s, e = seg.parse_range(start, end)
    kw = dict(force=force, store=store, now=now, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, geo_fn=geo_fn,
              meta_fetch=meta_fetch)
    if "+" in raw_id:
        parts = raw_id.split("+")
        if len(parts) != 2 or not base.ID_RE.match(parts[0]) or not paid_meta.is_meta(parts[1]):
            raise BadRequest("A combined account is one Google Ads account and one Meta ad account.")
        check_level(level, "both")

        def half(rid):
            try:
                return _raw_for(rid, level, s, e, **kw), None
            except base.RefreshTooSoon:
                return _raw_for(rid, level, s, e, **dict(kw, force=False)), None        # just refreshed: it is current
            except gads.GadsError as err:
                return None, err
        (gres, gerr), (mres, merr) = half(parts[0]), half(parts[1])
        if gres is None and mres is None:
            raise gerr
        if gres is None or mres is None:
            ok, bad, who = (mres, gerr, "google") if gres is None else (gres, merr, "meta")
            raw, account, stale = ok
            raw = dict(raw, warnings=list(raw.get("warnings") or []) + ["%s could not be read: %s Showing the other platform only." % (
                PLATFORM_NAME[who], bad)])
            report = assemble(raw, account, s, e)
            return dict(report, stale=stale) if stale else report
        (graw, gacct, gstale), (mraw, macct, mstale) = gres, mres
        if (gacct.get("currency") or "USD") != (macct.get("currency") or "USD"):
            raise BadRequest("The two accounts bill in different currencies, so they cannot be added together.")
        account = {"id": raw_id, "name": gacct["name"] + " + " + macct["name"], "currency": gacct.get("currency")}
        report = assemble(merge_raw(graw, mraw), account, s, e)
        stale = gstale or mstale
        return dict(report, stale=stale) if stale else report
    platform = "meta" if paid_meta.is_meta(raw_id) else "google"
    check_level(level, platform)
    raw, account, stale = _raw_for(raw_id, level, s, e, **kw)
    report = assemble(raw, account, s, e)
    return dict(report, stale=stale) if stale else report
