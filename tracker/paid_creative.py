"""Paid Marketing Agents: Creative & Asset Health, the reading and the report.

For a Google Ads account (or every account of an MCC) or a Meta ad account this reads what shows whether the account's creatives are wearing out and whether its campaigns hold the assets they
need, and the rules (tracker/paid_creative_math.py) judge it. Nothing is changed in an account.

Google: every live ad's clicks and impressions by whole week (Monday to Sunday), the same for every Performance Max asset group, the first headline of each responsive search ad (to name it), the
assets Google itself rates Low (an ad's assets and an asset group's), and the account's asset coverage (Asset Coverage's own read and rules, tracker/paid_assets.py, so the two pages agree and
share one saved read). Meta: the live ads and Meta's quality rankings (the read Ad Strength and Asset Coverage share, tracker/paid_meta_creative.py), the ads' numbers by week (spend, impressions,
reach, link clicks), and the ad sets' coverage (tracker/paid_meta_creative_analyse.py).

The weekly numbers of the ads are the point (a failure there is raised); every other part is soft: a part that could not be read leaves its check out, says so, and is named in `unread` so a
monitor never takes the silence for "fixed". The read is cached like the other agents (tracker/paid_geo.py _cached). The field names of the Google queries and the Meta one are the platforms'
documented ones; the ones new here are checked by the admin live query check (tracker/paid_gaql_check.py), not by a test.
"""
from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

from . import paid_assets as assets
from . import paid_creative_math as math
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
AGENT = "creative"

_LIVE = "ad_group_ad.status = ENABLED AND ad_group.status = ENABLED AND campaign.status = ENABLED"
_DURING = "segments.date BETWEEN '%s' AND '%s'"
GAQL_ADS = ("SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.ad.name, segments.week, metrics.impressions, metrics.clicks, "
            "metrics.cost_micros FROM ad_group_ad WHERE " + _LIVE + " AND " + _DURING)
GAQL_GROUPS = ("SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, segments.week, metrics.impressions, metrics.clicks, metrics.cost_micros FROM asset_group "
               "WHERE asset_group.status = ENABLED AND campaign.status = ENABLED AND " + _DURING)
GAQL_HEADLINES = ("SELECT ad_group_ad.ad.id, ad_group_ad.ad.responsive_search_ad.headlines FROM ad_group_ad "
                  "WHERE ad_group_ad.ad.type = RESPONSIVE_SEARCH_AD AND " + _LIVE)
GAQL_WEAK_ADS = ("SELECT campaign.id, campaign.name, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad_asset_view.field_type, ad_group_ad_asset_view.performance_label, "
                 "asset.type, asset.name, asset.text_asset.text FROM ad_group_ad_asset_view WHERE ad_group_ad_asset_view.performance_label = LOW AND ad_group_ad_asset_view.enabled = TRUE AND "
                 + _LIVE)
GAQL_WEAK_GROUPS = ("SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, asset_group_asset.field_type, asset_group_asset.performance_label, asset.type, asset.name, "
                    "asset.text_asset.text FROM asset_group_asset WHERE asset_group_asset.performance_label = LOW AND asset_group_asset.status = ENABLED AND asset_group.status = ENABLED "
                    "AND campaign.status = ENABLED")

META_WEEK_FIELDS = "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,impressions,reach,inline_link_clicks"

AD_TYPES = {"RESPONSIVE_SEARCH_AD": "Responsive search ad", "EXPANDED_TEXT_AD": "Expanded text ad", "RESPONSIVE_DISPLAY_AD": "Responsive display ad", "CALL_AD": "Call ad",
            "DEMAND_GEN_MULTI_ASSET_AD": "Demand Gen ad", "DEMAND_GEN_VIDEO_RESPONSIVE_AD": "Demand Gen video ad", "IMAGE_AD": "Image ad", "VIDEO_RESPONSIVE_AD": "Video ad",
            "APP_AD": "App ad", "SHOPPING_PRODUCT_AD": "Shopping ad"}
GROUP_KIND = "Performance Max asset group"
WORKERS = 6


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x else 0.0


def _short(s, n=60):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


# ── The weeks ───────────────────────────────────────────────────────────────

def week_starts(today):
    """The Mondays of the last WEEKS whole weeks before `today`, oldest first (the week `today` is in is never counted: it is not whole)."""
    last = today - dt.timedelta(days=today.weekday() + 7)
    return [last - dt.timedelta(days=7 * i) for i in range(math.WEEKS - 1, -1, -1)]


def _empty_weeks():
    return [{"impr": 0.0, "clicks": 0.0, "cost": 0.0, "reach": 0.0} for _ in range(math.WEEKS)]


def _add(week, m):
    week["impr"] += _num(m.get("impressions"))
    week["clicks"] += _num(m.get("clicks"))
    week["cost"] = round(week["cost"] + _num(m.get("costMicros")) / 1e6, 4)


# ── Reading Google's rows ───────────────────────────────────────────────────

def headlines_of(rows):
    """{ad id: its first headline}."""
    out = {}
    for r in rows:
        ad = _get(r, "adGroupAd", "ad", default={})
        heads = _get(ad, "responsiveSearchAd", "headlines", default=[]) or []
        first = next((h.get("text") for h in heads if isinstance(h, dict) and h.get("text")), "")
        if ad.get("id") and first:
            out[str(ad["id"])] = first
    return out


def ad_name(ad_id, ad_type, ad_group, headlines, own_name=""):
    """What a person calls an ad: its first headline (or the name it was given) and the ad group it is in, so two ads of one ad group read differently."""
    kind = AD_TYPES.get(ad_type) or (str(ad_type or "Ad").replace("_", " ").capitalize())
    head = headlines.get(str(ad_id)) or own_name
    return "%s (%s)" % (_short(head, 50), ad_group) if head and ad_group else ("%s in %s" % (kind, ad_group) if ad_group else "%s %s" % (kind, ad_id))


def read_ads(rows, starts, headlines):
    """[creative]: the live ads with their weeks. A row for a week outside the window or with no ad id is dropped."""
    idx = {d.isoformat(): i for i, d in enumerate(starts)}
    by = {}
    for r in rows:
        ad = _get(r, "adGroupAd", "ad", default={})
        aid, week = str(ad.get("id") or ""), str(_get(r, "segments", "week", default="") or "")
        if not aid or week not in idx:
            continue
        c = by.get(aid)
        if c is None:
            kind = AD_TYPES.get(ad.get("type")) or str(ad.get("type") or "Ad").replace("_", " ").capitalize()
            group = _get(r, "adGroup", "name", default="") or ""
            c = by[aid] = {"id": aid, "name": ad_name(aid, ad.get("type"), group, headlines, ad.get("name") or ""), "kind": kind, "campaign": _get(r, "campaign", "name", default="") or "",
                           "campaignId": str(_get(r, "campaign", "id", default="") or ""), "group": group, "weeks": _empty_weeks(), "rank": None}
        _add(c["weeks"][idx[week]], r.get("metrics") or {})
    return list(by.values())


def read_groups(rows, starts):
    """[creative]: the live Performance Max asset groups with their weeks."""
    idx = {d.isoformat(): i for i, d in enumerate(starts)}
    by = {}
    for r in rows:
        g = r.get("assetGroup") or {}
        gid, week = str(g.get("id") or ""), str(_get(r, "segments", "week", default="") or "")
        if not gid or week not in idx:
            continue
        c = by.setdefault("g" + gid, {"id": "g" + gid, "name": g.get("name") or "Asset group %s" % gid, "kind": GROUP_KIND, "campaign": _get(r, "campaign", "name", default="") or "",
                                      "campaignId": str(_get(r, "campaign", "id", default="") or ""), "group": g.get("name") or "", "weeks": _empty_weeks(), "rank": None})
        _add(c["weeks"][idx[week]], r.get("metrics") or {})
    return list(by.values())


def _asset_text(r):
    a = r.get("asset") or {}
    text = _get(a, "textAsset", "text", default="") or ""
    return text or ("an image" if a.get("type") in ("IMAGE", "MEDIA_BUNDLE") else (a.get("name") or "").strip() or str(a.get("type") or "").lower().replace("_", " "))


def read_weak(ad_rows, group_rows, headlines):
    """[{creativeId, name, campaign, group, field, text, label, kind}]: the assets Google rates Low, an ad's and an asset group's."""
    out = []
    for r in ad_rows:
        ad = _get(r, "adGroupAd", "ad", default={})
        aid = str(ad.get("id") or "")
        v = r.get("adGroupAdAssetView") or {}
        if not aid or v.get("performanceLabel") != "LOW":
            continue
        group = _get(r, "adGroup", "name", default="") or ""
        out.append({"creativeId": aid, "name": ad_name(aid, ad.get("type"), group, headlines), "campaign": _get(r, "campaign", "name", default="") or "", "group": group,
                    "field": v.get("fieldType") or "ASSET", "text": _asset_text(r), "label": "LOW", "kind": AD_TYPES.get(ad.get("type")) or "Ad"})
    for r in group_rows:
        g = r.get("assetGroup") or {}
        gid = str(g.get("id") or "")
        v = r.get("assetGroupAsset") or {}
        if not gid or v.get("performanceLabel") != "LOW":
            continue
        out.append({"creativeId": "g" + gid, "name": g.get("name") or "Asset group %s" % gid, "campaign": _get(r, "campaign", "name", default="") or "", "group": g.get("name") or "",
                    "field": v.get("fieldType") or "ASSET", "text": _asset_text(r), "label": "LOW", "kind": GROUP_KIND})
    return out


def gaps_of_google(report):
    """[gap]: the shortfalls Asset Coverage found, one per campaign (or asset group, or ad) and kind of asset."""
    out = []
    for c in report.get("campaigns") or []:
        for g in c.get("gaps") or []:
            out.append({"key": g["key"], "scope": g["scope"], "campaign": c["name"], "campaignId": c["id"], "label": g["label"], "severity": g["severity"], "have": g["have"], "target": g["target"],
                        "add": g["add"], "message": g["message"]})
    return out


def _soft(warnings, what, fn, default=None):
    try:
        return fn()
    except gads.GadsError as e:
        warnings.append("%s could not be read (%s), so that check was skipped." % (what, e))
        return default


def read_account(member, search, *, today, report_fn):
    """{creatives, weak, gaps, weeksRead, today, warnings, unread}: one Google Ads account. The ads' weeks are raised on failure; the rest is soft. `report_fn(member)` is Asset Coverage's report."""
    starts = week_starts(today)
    first, last = starts[0].isoformat(), (starts[-1] + dt.timedelta(days=6)).isoformat()
    cid = member["id"]
    ad_rows = search(cid, GAQL_ADS % (first, last))
    warnings, unread = [], []
    group_rows = _soft(warnings, "The Performance Max asset groups' numbers", lambda: search(cid, GAQL_GROUPS % (first, last)), None)
    head_rows = _soft(warnings, "The ads' headlines", lambda: search(cid, GAQL_HEADLINES), None)
    headlines = headlines_of(head_rows or [])
    creatives = read_ads(ad_rows, starts, headlines) + (read_groups(group_rows, starts) if group_rows else [])
    weak_ads = _soft(warnings, "Google's ratings of the ads' assets", lambda: search(cid, GAQL_WEAK_ADS), None)
    weak_groups = _soft(warnings, "Google's ratings of the asset groups' assets", lambda: search(cid, GAQL_WEAK_GROUPS), None)
    weak = read_weak(weak_ads, weak_groups or [], headlines) if weak_ads is not None and weak_groups is not None else None
    if weak is None:
        unread.append("low_rating")
    gaps = _soft(warnings, "The asset coverage", lambda: gaps_of_google(report_fn(member)), None)
    if gaps is None:
        unread.append("missing_asset")
    return {"creatives": creatives, "weak": weak, "gaps": gaps, "weeksRead": True, "today": today.isoformat(), "warnings": warnings, "unread": unread}


# ── Meta ────────────────────────────────────────────────────────────────────

def meta_weeks_request(act, today):
    """(path, params) of the live ads' numbers in 7-day buckets, the last WEEKS of them ending yesterday, one row per ad and bucket."""
    last = today - dt.timedelta(days=1)
    first = last - dt.timedelta(days=7 * math.WEEKS - 1)
    return "/%s/insights" % act, {"level": "ad", "time_increment": 7, "time_range": json.dumps({"since": first.isoformat(), "until": last.isoformat()}), "fields": META_WEEK_FIELDS, "limit": 500}


def read_meta_weeks(rows, today):
    """{ad id: [weeks]}: Meta's 7-day rows put into the WEEKS buckets (a bucket outside the window is dropped, a missing one is zeros)."""
    first = today - dt.timedelta(days=7 * math.WEEKS)
    out = {}
    for r in rows:
        aid = str(r.get("ad_id") or "")
        try:
            start = dt.date.fromisoformat(str(r.get("date_start") or "")[:10])
        except ValueError:
            continue
        i = (start - first).days // 7
        if not aid or not 0 <= i < math.WEEKS:
            continue
        w = out.setdefault(aid, _empty_weeks())[i]
        w["impr"] += _num(r.get("impressions"))
        w["clicks"] += _num(r.get("inline_link_clicks"))
        w["cost"] = round(w["cost"] + _num(r.get("spend")), 4)
        w["reach"] += _num(r.get("reach"))
    return out


FORMAT_LABEL = {"video": "Video ad", "carousel": "Carousel ad", "image": "Image ad", "other": "Ad"}


def meta_creatives(raw, weeks):
    """[creative]: Meta's live ads with their weeks and rankings (the read Ad Strength shares). An ad Meta stopped or limited is not a creative to judge here."""
    out = []
    for ad in raw.get("ads") or []:
        if ad.get("status") != "ACTIVE":
            continue
        rank = ((raw.get("insights") or {}).get(ad["id"]) or {}).get("rank")
        out.append({"id": ad["id"], "name": ad["name"], "kind": FORMAT_LABEL.get(ad.get("format"), "Ad"), "campaign": ad.get("campaign") or "", "campaignId": ad.get("campaignId") or "",
                    "group": ad.get("adset") or "", "weeks": weeks.get(ad["id"]) or _empty_weeks(), "rank": rank})
    return out


def gaps_of_meta(coverage_report):
    """[gap]: the ad sets that hold too few ads (Meta's coverage view), the high and medium ones."""
    from . import paid_meta_creative_analyse as an
    out = []
    for r in coverage_report.get("adsets") or []:
        for f in r.get("flags") or []:
            if f["severity"] in ("high", "medium"):
                out.append({"key": f["code"].upper(), "scope": "Ad set: %s" % (r.get("name") or r.get("id")), "campaign": r.get("campaign") or "", "campaignId": r.get("campaignId") or "",
                            "label": "Live ads", "severity": f["severity"], "have": r.get("ads", 0), "target": an.TARGET_ADS, "add": max(0, an.TARGET_ADS - r.get("ads", 0)), "message": f["text"]})
    return out


def read_meta_account(act, pages, *, today, store=None, raw_fn=None, coverage_fn=None):
    """The same shape as read_account, for a Meta ad account. `raw_fn()` is the shared Meta creative read, `coverage_fn(raw)` the coverage view of it."""
    from . import paid_meta
    from . import paid_meta_creative as mc
    from . import paid_meta_creative_analyse as an
    raw = raw_fn() if raw_fn else mc.raw_for(act, store=store, today=today, pages_fn=pages, account_fn=lambda a: {})[0]      # the account's name is already known to the caller
    warnings, unread = list(raw.get("warnings") or []), []
    weeks = None
    try:
        weeks = read_meta_weeks(pages(*meta_weeks_request(act, today)), today)
    except (paid_meta.AdsError, gads.GadsError) as e:
        warnings.append("The ads' numbers by week could not be read (%s), so no ad was judged for wearing out." % str(e)[:160])
    creatives = meta_creatives(raw, weeks or {})
    ranked = bool(raw.get("insightsRead", True))
    if not ranked:
        unread.append("low_rating")
    cover = coverage_fn(raw) if coverage_fn else an.coverage(raw, {"id": act, "name": act, "currency": "USD", "platform": "meta"}, today)
    return {"creatives": creatives, "weak": None, "gaps": gaps_of_meta(cover), "weeksRead": weeks is not None, "today": today.isoformat(), "warnings": warnings,
            "unread": unread + (["tired"] if weeks is None else [])}


# ── Raw reads ───────────────────────────────────────────────────────────────

def google_raw(account, *, search_fn=None, report_fn=None, workers=WORKERS, today=None, store=None, now=None):
    """{accounts: [{id, name, currency, platform, ...the read}], warnings, members}: one account or an MCC's accounts. An account whose ads cannot be read is left out and said so; every one
    failing is raised. `report_fn(member)` is Asset Coverage's report for one account."""
    search = search_fn or gads.search
    members = account.get("members") or [account]
    day = today or dt.date.today()
    report = report_fn or (lambda m: assets.report_for(m["id"], accounts_fn=lambda: [m], groups_fn=lambda: [], search_fn=search_fn, store=store, today=day, now=now))

    def run(member):
        try:
            return member, read_account(member, search, today=day, report_fn=report), None
        except gads.GadsError as e:
            return member, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as pool:
        done = list(pool.map(run, members))
    ok = [(mem, r) for mem, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    many = bool(account.get("members"))
    return {"accounts": [dict(r, id=mem["id"], name=mem["name"], currency=mem.get("currency") or account.get("currency") or "USD", platform="google") for mem, r in ok],
            "warnings": ["%s could not be read: %s" % (mem["name"], e) for mem, r, e in done if e] + [("%s: %s" % (mem["name"], w)) if many else w for mem, r in ok for w in r["warnings"]],
            "members": [mem["name"] for mem in members] if many else []}


def meta_raw(act, *, pages_fn, account_fn, today=None, store=None, raw_fn=None):
    acct = account_fn(act) or {}
    got = read_meta_account(act, pages_fn, today=today or dt.date.today(), store=store, raw_fn=raw_fn)
    return {"accounts": [dict(got, id=act, name=acct.get("name") or act, currency=acct.get("currency") or "USD", platform="meta")], "warnings": list(got["warnings"]), "members": []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"fix": 0, "check": 1, "clean": 2}


def assemble(raw, account, *, now=None):
    """The report from the raw read."""
    now = now or dt.datetime.now(dt.timezone.utc)
    accounts, warnings = [], list(raw.get("warnings") or [])
    for a in raw["accounts"]:
        platform = a.get("platform") or "google"
        res = math.analyse(a, platform=platform)
        accounts.append(dict(res, id=a["id"], name=a["name"], currency=a.get("currency") or "USD", platform=platform, today=a.get("today") or "", unread=list(a.get("unread") or [])))
    accounts.sort(key=lambda a: (_RANK[a["status"]], -a["counts"]["high"], -a["counts"]["medium"], -a["counts"]["low"], a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": account.get("platform") or "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "generatedAt": now.isoformat(timespec="seconds"), "warnings": warnings, "accounts": accounts,
        "rules": [{"rule": k, "label": math.RULES[k]["label"], "level": math.RULES[k]["level"], "area": math.RULES[k]["area"], "fix": math.RULES[k]["fix"]} for k in math.ORDER],
        "notMeasured": math.NOT_MEASURED,
        "thresholds": {"recentWeeks": math.RECENT, "baseWeeks": math.BASE, "ctrFallPercent": int(math.CTR_FALL * 100), "ctrFallHighPercent": int(math.CTR_FALL_HIGH * 100),
                       "minRecentImpressions": math.MIN_RECENT_IMPR, "minBaseImpressions": math.MIN_BASE_IMPR, "frequency": math.FREQ_SIGNAL, "cpmRisePercent": int((math.CPM_RISE - 1) * 100)},
        "summary": {"counts": counts, "accounts": len(accounts), "high": sum(a["counts"]["high"] for a in accounts), "medium": sum(a["counts"]["medium"] for a in accounts),
                    "low": sum(a["counts"]["low"] for a in accounts), "findings": sum(a["findingCount"] for a in accounts),
                    "tired": sum(a["headline"]["tired"] for a in accounts), "judged": sum(a["headline"]["judged"] for a in accounts),
                    "creatives": sum(a["headline"]["creatives"] for a in accounts)},
    }


def load_raw(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None, meta_pages_fn=None, meta_account_fn=None, report_fn=None):
    """(raw, stale, account): the saved read of an account, read from the platform when there is none (or when `force`)."""
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        account = {"id": act, "name": (acct_fn(act) or {}).get("name") or act, "currency": (acct_fn(act) or {}).get("currency") or "USD", "platform": "meta"}
        raw, stale = paid_geo._cached("creative", act, "weeks", day, day, lambda: meta_raw(act, pages_fn=pages, account_fn=acct_fn, today=day, store=store), force=force, store=store, now=now)
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = paid_geo._cached("creative", account["id"], "weeks", day, day, lambda: google_raw(account, search_fn=search_fn, report_fn=report_fn, today=day, store=store, now=None),
                                      force=force, store=store, now=now)
    return raw, stale, account


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, meta_pages_fn=None, meta_account_fn=None,
               report_fn=None):
    """The Creative & Asset Health report for a Google Ads account or an MCC the root MCC holds, or for a Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account."""
    raw, stale, account = load_raw(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, now=now, force=force, store=store, today=today, meta_pages_fn=meta_pages_fn,
                                   meta_account_fn=meta_account_fn, report_fn=report_fn)
    report = assemble(raw, account, now=at)
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
