"""Paid Marketing Agents: a live check of every query the recent agents send (read only, one account, LIMIT 1 each).

The agents' tests run on fakes written from what their authors believed Google and Meta answer, so a field name that does not exist passes every test and fails on the first real read (the
first KPI Monitoring read failed on `campaign.start_date`). This sends each query of the Brand IS Monitor, the Brand Spend Alert, the Disapproved Ads Tracker, the Ad Copy Checker, the Spend
Anomaly Alert, Location Conflicts, KPI Monitoring, the Keyword Visibility Tracker, Tracking & Data Health and Account Watch to one real account with its rows cut to one, and says which ones Google refuses and why; the Meta reads
are asked for one row of one account in the same way. It is the check to run after adding or changing any query, and what an admin runs from /p2/paid-marketing/api/query-check.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads

WORKERS = 4
META_WORKERS = 4
_LIMIT = re.compile(r"\s+LIMIT\s+\d+\s*$", re.I)


def _one_row(q):
    return _LIMIT.sub("", q.strip()) + " LIMIT 1"


def google_queries(today=None):
    """[(agent, label, gaql)]: every Google query the recent agents send, built with sample dates and ids (the ids need not exist: a query is valid or not by its fields)."""
    from . import paid_anomaly as an, paid_brandis as bis, paid_brandis_diag as diag, paid_brandspend as bs, paid_copycheck as cc, paid_disapproved as dis, paid_disapproved_extra as dx
    from . import paid_geoconf as gc, paid_kpi as kp, paid_kvis as kv, paid_track as tr, paid_watch as wt
    today = today or dt.date.today()
    a, b = (today - dt.timedelta(days=28)).isoformat(), (today - dt.timedelta(days=1)).isoformat()
    ts = ((today - dt.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"), today.strftime("%Y-%m-%d %H:%M:%S"))
    ids = ["1"]
    qs = [
        ("brandis", "campaign weeks", bis.gaql(a, b)),
        ("brandis", "campaign status", diag.gaql_campaigns(ids)), ("brandis", "bid strategy settings", diag.gaql_settings(ids)), ("brandis", "keywords", diag.gaql_keywords(ids)),
        ("brandis", "keyword numbers", diag.gaql_keyword_numbers(ids)), ("brandis", "ads", diag.gaql_ads(ids)),
        ("brandspend", "campaigns", bs.GAQL_CAMPAIGNS), ("brandspend", "campaign days", bs.gaql(a, b)), ("brandspend", "search terms", bs.gaql_terms(a, b)),
        ("disapproved", "ads", dis.GAQL_ADS), ("disapproved", "ad cover", dis.gaql_cover(ids)), ("disapproved", "ad weeks", dis.gaql_weekly(ids, a, b)),
        ("disapproved", "asset group assets", dx.GAQL_ASSETS), ("disapproved", "keywords", dx.GAQL_KEYWORDS),
        ("disapproved", "asset cover", dx.gaql_asset_cover(ids)), ("disapproved", "asset weeks", dx.gaql_asset_weekly(ids, a, b)),
        ("copycheck", "search ads", cc.GAQL_SEARCH), ("copycheck", "search ads plain", cc.GAQL_SEARCH_PLAIN), ("copycheck", "display ads", cc.GAQL_DISPLAY), ("copycheck", "asset group text", cc.GAQL_PMAX),
        ("copycheck", "campaign assets", cc.GAQL_CAMPAIGN_ASSETS), ("copycheck", "account assets", cc.GAQL_CUSTOMER_ASSETS),
        ("anomaly", "hourly spend", an.GAQL_HOURLY % (a, b)), ("anomaly", "daily spend", an.GAQL_DAILY % (a, b)), ("anomaly", "campaign days", an.GAQL_CAMPAIGN_DAYS % (a, b)),
        ("anomaly", "budgets", an.GAQL_BUDGETS), ("anomaly", "change history", an.GAQL_CHANGES % ts),
        ("geoconf", "campaigns", gc.GAQL_CAMPAIGNS), ("geoconf", "locations", gc.GAQL_LOCATIONS), ("geoconf", "place names", gc.GAQL_NAMES % "'geoTargetConstants/1014221'"),
        ("geoconf", "schedules", gc.GAQL_SCHEDULES), ("geoconf", "devices", gc.GAQL_DEVICES), ("geoconf", "ad groups", gc.GAQL_GROUPS), ("geoconf", "keywords", gc.GAQL_KEYWORDS),
        ("kpi", "campaigns", kp.GAQL_CAMPAIGNS), ("kpi", "days", kp.GAQL_DAYS % (a, b)), ("kpi", "budget lost", kp.GAQL_LOST % (a, b)),
        ("kvis", "top keywords", kv.GAQL_TOP % (a, b)), ("kvis", "keyword weeks", kv.GAQL_WEEKS % (a, b, "1")), ("kvis", "quality score history", kv.GAQL_QS % (a, b, "1")), ("kvis", "auction insights", kv.GAQL_AUCTION % (a, b)),
        ("track", "days", tr.GAQL_DAYS % (a, b)), ("track", "conversion actions", tr.GAQL_ACTIONS), ("track", "conversion action days", tr.GAQL_ACTION_DAYS % (a, b)), ("track", "audience lists", tr.GAQL_LISTS),
        ("track", "customer data terms", tr.GAQL_TERMS), ("track", "offline upload log", tr.GAQL_UPLOADS),
        ("watch", "manager account clients", wt.GAQL_CLIENTS), ("watch", "account budget orders", wt.GAQL_ORDERS), ("watch", "change history", wt.GAQL_CHANGES % (ts[0], ts[1])),
        ("watch", "conversion actions", wt.GAQL_ACTIONS), ("watch", "who can open the account", wt.GAQL_USERS),
    ]
    return qs


def meta_requests(act):
    """[(agent, label, path, params)]: every Meta read the recent agents send, cut to one row."""
    from . import paid_anomaly as an, paid_copycheck as cc, paid_disapproved_extra as dx, paid_geoconf as gc, paid_kpi as kp, paid_meta
    today = dt.date.today()
    a, b = (today - dt.timedelta(days=14)).isoformat(), (today - dt.timedelta(days=1)).isoformat()
    out = []
    for agent, fn in (("geoconf", lambda: gc.meta_request(act)), ("copycheck", lambda: cc.meta_request(act)), ("kpi campaigns", lambda: kp.meta_campaigns_request(act)),
                      ("kpi adsets", lambda: kp.meta_adsets_request(act)), ("anomaly daily", lambda: an.meta_daily_request(act, a, b)), ("anomaly hourly", lambda: an.meta_daily_request(act, a, b, hourly=True))):
        try:
            path, params = fn()
            out.append((agent.split()[0], agent, path, dict(params, limit=1)))
        except Exception as e:                                                       # noqa: BLE001 (a request that cannot even be built is itself the finding)
            out.append((agent.split()[0], agent, None, {"_build_error": str(e)}))
    try:
        path, params = dx.meta_ads_request(act)
        out.append(("disapproved", "ads", path, dict(params, limit=1)))
    except Exception as e:                                                           # noqa: BLE001
        out.append(("disapproved", "ads", None, {"_build_error": str(e)}))
    try:
        out.append(("kpi", "insights", "/%s/insights" % act, dict(paid_meta.q_insights(a, b), limit=1)))
    except Exception as e:                                                           # noqa: BLE001
        out.append(("kpi", "insights", None, {"_build_error": str(e)}))
    from . import paid_strategy_research_meta as mh
    for name, (path, params) in mh.requests_for(act, a, b).items():
        out.append(("metahistory", name, path, dict(params, limit=1)))
    from . import paid_meta_creative as mc
    for label, fn in (("ads", lambda: mc.ads_request(act)), ("ad numbers and rankings", lambda: mc.insights_request(act, a, b))):
        try:
            path, params = fn()
            out.append(("metacreative", label, path, dict(params, limit=1)))
        except Exception as e:                                                       # noqa: BLE001
            out.append(("metacreative", label, None, {"_build_error": str(e)}))
    from . import paid_meta_interests as mi
    for label, fn in (("interest search", lambda: mi.search_request("insulin pump")), ("interest suggestions", lambda: mi.suggest_request(["Insulin pump"])),
                      ("behaviors", lambda: mi.behaviors_request(act)), ("ad sets with targeting", lambda: mi.adsets_request(act))):
        try:
            path, params = fn()
            out.append(("metainterests", label, path, dict(params, limit=1)))
        except Exception as e:                                                       # noqa: BLE001
            out.append(("metainterests", label, None, {"_build_error": str(e)}))
    from . import paid_track as tr
    for label, fn in (("pixels", lambda: tr.meta_pixels_request(act)), ("custom audiences", lambda: tr.meta_audiences_request(act))):
        try:
            path, params = fn()
            out.append(("track", label, path, dict(params, limit=1)))
        except Exception as e:                                                       # noqa: BLE001
            out.append(("track", label, None, {"_build_error": str(e)}))
    from . import paid_watch as wt
    for label, fn in (("ad account status", lambda: wt.meta_status_request(act)), ("pages", lambda: wt.meta_pages_request(act)), ("pages without the published flag", lambda: wt.meta_pages_request(act, plain=True)),
                      ("activity history", lambda: wt.meta_activity_request(act, a))):
        try:
            path, params = fn()
            out.append(("watch", label, path, dict(params, limit=1)))
        except Exception as e:                                                       # noqa: BLE001
            out.append(("watch", label, None, {"_build_error": str(e)}))
    from . import paid_qa_meta as qm
    for label, edge, fields in (("campaigns", "campaigns", qm.CAMPAIGN_FIELDS), ("ad sets", "adsets", qm.ADSET_FIELDS), ("ads", "ads", qm.AD_FIELDS)):
        out.append(("metaqa", label, "/%s/%s" % (act, edge), {"fields": fields, "limit": 1}))
    return out


def check_google(account_id, *, search_fn=None, queries=None, workers=WORKERS):
    """[{agent, label, ok, error}] for every query sent to one Google Ads account with its rows cut to one."""
    search = search_fn or gads.search
    qs = queries if queries is not None else google_queries()

    def run(item):
        agent, label, q = item
        t0 = time.monotonic()
        try:
            rows = search(account_id, _one_row(q))
            return {"agent": agent, "label": label, "ok": True, "error": "", "rows": len(rows), "ms": int((time.monotonic() - t0) * 1000)}
        except Exception as e:                                                       # noqa: BLE001 (every refusal is the answer here)
            return {"agent": agent, "label": label, "ok": False, "error": str(e)[:300], "rows": 0, "ms": int((time.monotonic() - t0) * 1000)}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(run, qs))


def check_meta(act, *, pages_fn, requests=None, workers=META_WORKERS):
    """[{agent, label, ok, error}] for every Meta read sent to one ad account, one row each, a few at a time (they are many, and slow one by one)."""
    reqs = list(requests if requests is not None else meta_requests(act))

    def one(item):
        agent, label, path, params = item
        if path is None:
            return {"agent": agent, "label": label, "ok": False, "error": "could not be built: %s" % params.get("_build_error"), "rows": 0, "ms": 0}
        t0 = time.monotonic()
        try:
            rows = pages_fn(path, params)
            return {"agent": agent, "label": label, "ok": True, "error": "", "rows": len(rows or []), "ms": int((time.monotonic() - t0) * 1000)}
        except Exception as e:                                                       # noqa: BLE001
            return {"agent": agent, "label": label, "ok": False, "error": str(e)[:300], "rows": 0, "ms": int((time.monotonic() - t0) * 1000)}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, reqs))
