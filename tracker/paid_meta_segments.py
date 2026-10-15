"""Paid Marketing Agents: Segment Analysis for a Meta ad account, and for a Google and a Meta account together.

The report has exactly the shape tracker/paid_segments.build_report gives a Google account (seven tables, each cut by
campaign type, the sheet's columns, coverage, totals), so the page and tracker/paid_segment_insights.py read it without
knowing which platform it came from. Meta's cuts:

    day of the week   from the daily campaign rows (Meta has no weekday breakdown)
    hour              hourly_stats_aggregated_by_advertiser_time_zone
    age, gender       age, gender
    location          region (a state or region: Meta has no city breakdown)
    device            device_platform  (desktop is "Computers", as Google calls it, so the two add up)
    network           publisher_platform (Facebook, Instagram, Audience Network, Messenger)

and the campaign type is the campaign's objective ("Meta Sales", "Meta Leads", ...). Conversions are the objective's
results (see tracker/paid_meta.py), clicks are link clicks.

merge_reports(google_report, meta_report) adds two reports of the same range and currency: a segment that has the same
label on both platforms (Female, 25-34, Computers, 9 AM) is one row; a segment only one platform has stays a row. A
campaign type exists on one platform only, so each type table is that platform's.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads as base
from . import paid_meta as meta
from . import paid_segments as seg

log = logging.getLogger(__name__)

AGE_BUCKETS = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
GENDERS = ["female", "male", "unknown"]
DEVICE_LABEL = {"desktop": "Computers", "mobile_app": "Mobile app", "mobile_web": "Mobile web", "tablet": "Tablets",
                "connected_tv": "Connected TV", "unknown": "Unknown"}
PLACEMENT_LABEL = {"facebook": "Facebook", "instagram": "Instagram", "audience_network": "Audience Network",
                   "messenger": "Messenger", "threads": "Threads", "unknown": "Unknown"}
TYPE_NAME = {"SALES": "Meta Sales", "LEADS": "Meta Leads", "TRAFFIC": "Meta Traffic", "AWARENESS": "Meta Awareness",
             "ENGAGEMENT": "Meta Engagement", "APP": "Meta App promotion", "OTHER": "Meta Other"}


def _title(s):
    return str(s or "").replace("_", " ").title().strip() or "Unknown"


def _x_day(row):
    d = row.get("date_start") or ""
    try:
        i = dt.date.fromisoformat(d).weekday()
    except ValueError:
        return (99, "Unknown", "")
    return (i, seg.DAYS[i].title(), "")


def _x_hour(row):
    h = str(row.get("hourly_stats_aggregated_by_advertiser_time_zone") or "")[:2]
    if not h.isdigit():
        return (99, "Unknown", "")
    return (int(h), seg.hour_label(int(h)), "")


def _x_age(row):
    v = str(row.get("age") or "")
    if v in AGE_BUCKETS:
        return (AGE_BUCKETS.index(v), v, "")
    return (99, "Undetermined", "")


def _x_gender(row):
    v = str(row.get("gender") or "unknown").lower()
    label = "Undetermined" if v == "unknown" else _title(v)
    return (GENDERS.index(v) if v in GENDERS else 99, label, "")


def _x_region(row):
    v = str(row.get("region") or "").strip()
    return (v, v or "Not region-level", "")


def _x_device(row):
    v = str(row.get("device_platform") or "unknown").lower()
    return (v, DEVICE_LABEL.get(v) or _title(v), "")


def _x_network(row):
    v = str(row.get("publisher_platform") or "unknown").lower()
    return (v, PLACEMENT_LABEL.get(v) or _title(v), "")


# key, label, the Graph breakdown ("" = the daily rows), row reader, order
DIMENSIONS = [
    {"key": "day", "label": "Day of the week", "breakdown": "", "read": _x_day, "order": "natural"},
    {"key": "hour", "label": "Hour of the day", "breakdown": "hourly_stats_aggregated_by_advertiser_time_zone",
     "read": _x_hour, "order": "natural"},
    {"key": "age", "label": "Age", "breakdown": "age", "read": _x_age, "order": "natural"},
    {"key": "gender", "label": "Gender", "breakdown": "gender", "read": _x_gender, "order": "natural"},
    {"key": "city", "label": "Location (region)", "breakdown": "region", "read": _x_region, "order": "cost"},
    {"key": "device", "label": "Device", "breakdown": "device_platform", "read": _x_device, "order": "cost"},
    {"key": "network", "label": "Network", "breakdown": "publisher_platform", "read": _x_network, "order": "cost"},
]


def campaign_type(objective):
    return TYPE_NAME.get(meta.objective_kind(objective), "Meta Other")


def read_rows(rows, dim):
    """{segment label: {sort, label, sub, all, types}} (the shape paid_segments._read gives) from insights rows."""
    segs = {}
    for r in rows:
        n = meta.numbers(r)
        vals = {"imp": float(n[0]), "clk": float(n[1]), "cost": float(n[2]), "conv": float(n[3])}
        if seg._is_empty(vals):
            continue
        sort, label, sub = dim["read"](r)
        s = segs.setdefault(sort if dim["key"] == "city" else label, {"sort": sort, "label": label, "sub": sub, "all": seg._zero(), "types": {}})
        seg._add(s["all"], vals)
        seg._add(s["types"].setdefault(campaign_type(r.get("objective")), seg._zero()), vals)
    return segs


def query(dim, start, end):
    p = {"level": "campaign", "time_range": json.dumps({"since": start, "until": end}), "fields": ",".join(meta.INSIGHT_FIELDS),
         "limit": meta.PAGE_LIMIT}
    if dim["breakdown"]:
        p["breakdowns"] = dim["breakdown"]
    else:
        p["time_increment"] = 1
    return p


def build_report(account, start, end, *, fetch=None, store=None, workers=7):
    """The whole report for one Meta ad account {id, name, currency, tz}. A table whose query fails is reported with its
    error and the rest still come back; if the day table fails there is nothing to show and the failure is raised."""
    from . import paid_store
    store = store or paid_store

    def get(dim):
        try:
            if fetch:
                return dim, fetch(account["id"], dim, query(dim, start.isoformat(), end.isoformat())), None
            return dim, meta._pages(account["id"] + "/insights", query(dim, start.isoformat(), end.isoformat()), store), None
        except base.AdsError as e:
            return dim, None, e

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(DIMENSIONS)))) as ex:
        done = list(ex.map(get, DIMENSIONS))
    errors = {d["key"]: e for d, _r, e in done if e}
    if "day" in errors:
        raise errors["day"]
    read = {d["key"]: read_rows(rows, d) for d, rows, e in done if not e}
    day = read["day"]
    acct = {seg.ALL: seg._zero()}
    for sg in day.values():
        seg._add(acct[seg.ALL], sg["all"])
        for t, m in sg["types"].items():
            seg._add(acct.setdefault(t, seg._zero()), m)
    types = [t for t, _m in sorted(((t, m) for t, m in acct.items() if t != seg.ALL and not seg._is_empty(m)), key=lambda p: -p[1]["cost"])]

    segments = []
    for dim in DIMENSIONS:
        entry = {"key": dim["key"], "label": dim["label"], "error": None, "views": {}}
        if dim["key"] in errors:
            entry["error"] = str(errors[dim["key"]])
        else:
            for t in [seg.ALL] + types:
                entry["views"][t] = seg._view(read[dim["key"]], dim, t, acct[t]["cost"])
        segments.append(entry)
    days = (end - start).days + 1
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": "meta",
                    "isGroup": False, "accountCount": 1, "accounts": []},
        "range": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "types": types,
        "total": seg.derive(acct[seg.ALL], acct[seg.ALL]["cost"]),
        "segments": segments,
        "warnings": [],
        "timeZones": [account["tz"]] if account.get("tz") else [],
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


# ── A Google and a Meta report as one ───────────────────────────────────────

def _sums(rows):
    imp = sum(r["impressions"] for r in rows)
    clk = sum(r["clicks"] for r in rows)
    cost = sum(r["cost"] for r in rows)
    conv = sum(r["conversions"] for r in rows)
    return {"imp": float(imp), "clk": float(clk), "cost": float(cost), "conv": float(conv)}


def _merge_rows(a_rows, b_rows, order):
    by, first = {}, []
    for r in list(a_rows) + list(b_rows):
        k = r["label"]
        if k not in by:
            by[k] = {"label": k, "key": r.get("key", ""), "sub": r.get("sub", ""), "parts": []}
            first.append(k)
        by[k]["parts"].append(r)
    out = []
    for k in first:
        e = by[k]
        out.append((e, _sums(e["parts"])))
    if order == "cost":
        out.sort(key=lambda p: (-p[1]["cost"], p[0]["label"]))
    else:
        def nat(p):
            key = p[0]["key"]
            return (int(key) if str(key).lstrip("-").isdigit() else 999, p[0]["label"])
        out.sort(key=nat)
    return out


def _merge_view(av, bv, order, account_cost):
    rows_in = _merge_rows((av or {}).get("rows", []), (bv or {}).get("rows", []), order)
    total = seg._zero()
    for _e, m in rows_in:
        seg._add(total, m)
    rows = []
    for e, m in rows_in:
        row = seg.derive(m, total["cost"])
        row.update(key=e["key"], label=e["label"], sub=e["sub"])
        rows.append(row)
    return {"rows": rows, "total": seg.derive(total, total["cost"]), "coverage": seg._div(total["cost"], account_cost)}


def merge_reports(g, m, account):
    """One report for the Google report `g` and the Meta report `m` (same range, same currency)."""
    cost = g["total"]["cost"] + m["total"]["cost"]
    order = {d["key"]: d["order"] for d in DIMENSIONS}
    gseg, mseg = {s["key"]: s for s in g["segments"]}, {s["key"]: s for s in m["segments"]}
    types = sorted(set(g["types"]) | set(m["types"]),
                   key=lambda t: -(((gseg["day"]["views"].get(t) or {}).get("total") or {}).get("cost", 0) +
                                   ((mseg["day"]["views"].get(t) or {}).get("total") or {}).get("cost", 0)))
    segments, warnings = [], list(g.get("warnings") or []) + list(m.get("warnings") or [])
    for key in [s["key"] for s in g["segments"]]:
        gs, ms = gseg.get(key), mseg.get(key)
        entry = {"key": key, "label": (gs or ms)["label"], "error": None, "views": {}}
        if gs and gs.get("error") and ms and ms.get("error"):
            entry["error"] = gs["error"]
        elif (gs and gs.get("error")) or (ms and ms.get("error")):
            who = "Google Ads" if gs and gs.get("error") else "Meta Ads"
            entry["note"] = "%s could not be read for this table, so it covers the other platform only." % who
        for t in [seg.ALL] + types:
            gv = ((gs or {}).get("views") or {}).get(t)
            mv = ((ms or {}).get("views") or {}).get(t)
            acct_cost = cost if t == seg.ALL else _type_cost(gseg, mseg, t)
            if gv or mv:
                entry["views"][t] = _merge_view(gv, mv, order.get(key, "natural"), acct_cost)
        if key == "city":
            entry["note"] = (entry.get("note", "") + " Google reports cities and Meta reports regions, so rows are not merged." ).strip()
        segments.append(entry)
    total = {k: (g["total"][k] or 0) + (m["total"][k] or 0) for k in ("impressions", "clicks", "cost", "conversions")}
    zero = {"imp": total["impressions"], "clk": total["clicks"], "cost": total["cost"], "conv": total["conversions"]}
    stale = [x for x in (g.get("stale"), m.get("stale")) if x]
    out = {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or g["account"]["currency"],
                    "platform": "both", "isGroup": False, "accountCount": 2, "accounts": []},
        "range": g["range"], "types": types, "total": seg.derive(zero, zero["cost"]), "segments": segments, "warnings": warnings,
        "timeZones": sorted(set((g.get("timeZones") or []) + (m.get("timeZones") or []))),
        "generatedAt": min(g["generatedAt"], m["generatedAt"]),
    }
    if stale:
        out["stale"] = min(stale)
    return out


def _type_cost(gseg, mseg, t):
    """The campaign type's whole spend: its day table total (the day table is a campaign-level cut that holds all of it)."""
    tot = 0.0
    for s in (gseg, mseg):
        v = (s.get("day", {}).get("views") or {}).get(t)
        if v:
            tot += v["total"]["cost"]
    return tot
