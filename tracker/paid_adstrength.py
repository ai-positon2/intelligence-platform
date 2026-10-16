"""Paid Marketing Agents: Ad Strength Analysis, the reading.

Google rates every Responsive Search Ad (RSA) and every Performance Max asset group Poor, Average, Good or
Excellent ("ad strength"). This reads that rating for an account (or an MCC's accounts together) with everything
Google exposes about *why*:

    RSA   the rating, Google's own tips for the ad ("Try including more keywords in your headlines"), every
          headline and description with its pin and its own rating (Low, Good, Best...), the last 30 days'
          impressions, clicks and cost, the ad group's top keywords, and the new RSA text Google itself has
          recommended for the ad group where it has some (the `recommendation` resource)
    PMax  the asset group's rating, every asset it holds (text included) and the 30 days' numbers. The API does
          not carry tips or per-asset ratings for Performance Max, so what to fix is worked out from the asset
          counts (tracker/paid_adstrength_insights.py)

Only enabled campaigns, ad groups and ads are read. Google Ads only: Meta has no ad strength rating.

What the ratings mean for the work (which ads come first, what to change) is tracker/paid_adstrength_insights.py.
This module reads, folds the accounts of an MCC together and caches. The cache is the shared one
(tracker/paid_geo.py _cached): one read per account per day-ish, Refresh limited, a spent quota served from the
last saved read with its date.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_adstrength_insights as insights
from . import paid_gads as base
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_MEMBERS = seg.MAX_MEMBERS
WINDOW_DAYS = 30
KEYWORDS_PER_GROUP = 12
GROUP_CHUNK = 150                      # ad groups per keyword query
RECOMMENDATIONS = ("RESPONSIVE_SEARCH_AD", "RESPONSIVE_SEARCH_AD_ASSET", "RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH",
                   "IMPROVE_PERFORMANCE_MAX_AD_STRENGTH")

_LIVE = "campaign.status = ENABLED AND segments.date DURING LAST_30_DAYS"
_METRICS = "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions"

RSA_GAQL = (
    "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad_strength, ad_group_ad.action_items, "
    "ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
    "ad_group_ad.ad.responsive_search_ad.path1, ad_group_ad.ad.responsive_search_ad.path2, " + _METRICS + " FROM ad_group_ad "
    "WHERE ad_group_ad.ad.type = RESPONSIVE_SEARCH_AD AND ad_group_ad.status = ENABLED AND ad_group.status = ENABLED AND " + _LIVE)

GROUP_GAQL = (
    "SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, asset_group.ad_strength, asset_group.final_urls, " + _METRICS +
    " FROM asset_group WHERE asset_group.status = ENABLED AND " + _LIVE)

GROUP_ASSETS_GAQL = (
    "SELECT asset_group.id, asset_group_asset.field_type, asset.type, asset.text_asset.text FROM asset_group_asset "
    "WHERE asset_group_asset.status = ENABLED AND asset_group.status = ENABLED AND campaign.status = ENABLED")

RECS_GAQL = (
    "SELECT recommendation.type, recommendation.campaign, recommendation.ad_group, "
    "recommendation.responsive_search_ad_recommendation, recommendation.responsive_search_ad_asset_recommendation, "
    "recommendation.responsive_search_ad_improve_ad_strength_recommendation, recommendation.improve_performance_max_ad_strength_recommendation "
    "FROM recommendation WHERE recommendation.dismissed = FALSE AND recommendation.type IN (%s)" % ", ".join(RECOMMENDATIONS))


def keywords_gaql(ad_group_ids):
    return ("SELECT ad_group.id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, metrics.impressions FROM keyword_view "
            "WHERE ad_group_criterion.negative = FALSE AND ad_group_criterion.status = ENABLED AND ad_group.status = ENABLED AND "
            "ad_group.id IN (%s) AND %s" % (", ".join(str(int(i)) for i in ad_group_ids), _LIVE))


# ── Reading the API's rows ──────────────────────────────────────────────────

def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _numbers(row):
    m = row.get("metrics") or {}
    return {"impressions": _i(m.get("impressions")), "clicks": _i(m.get("clicks")),
            "cost": round(_i(m.get("costMicros")) / 1e6, 2), "conversions": round(float(m.get("conversions") or 0), 2)}


def _texts(items):
    out = []
    for h in items or []:
        t = str((h or {}).get("text") or "").strip()
        if t:
            out.append({"text": t, "label": str(h.get("assetPerformanceLabel") or "").upper() or None,
                        "pin": str(h.get("pinnedField") or "") or None})
    return out


def read_rsas(rows):
    """One dict per RSA: ids, names, rating, Google's tips, the headlines and descriptions, and the 30 days' numbers."""
    out = []
    for r in rows:
        ad = _get(r, "adGroupAd", "ad", default={})
        rsa = ad.get("responsiveSearchAd") or {}
        ad_id = str(ad.get("id") or "")
        if not ad_id:
            continue
        ag_id = str(_get(r, "adGroup", "id", default=""))
        out.append({
            "key": "rsa:%s:%s" % (ag_id, ad_id), "kind": "rsa", "adId": ad_id, "adGroupId": ag_id, "adGroup": _get(r, "adGroup", "name", default=""),
            "campaignId": str(_get(r, "campaign", "id", default="")), "campaign": _get(r, "campaign", "name", default=""),
            "strength": str(_get(r, "adGroupAd", "adStrength", default="UNSPECIFIED")).upper(),
            "tips": [str(t) for t in (_get(r, "adGroupAd", "actionItems", default=[]) or []) if str(t).strip()],
            "headlines": _texts(rsa.get("headlines")), "descriptions": _texts(rsa.get("descriptions")),
            "finalUrl": (ad.get("finalUrls") or [""])[0], "path": "/".join(p for p in (rsa.get("path1"), rsa.get("path2")) if p),
            **_numbers(r)})
    return out


def read_groups(group_rows, asset_rows):
    """One dict per Performance Max asset group: ids, names, rating, the 30 days' numbers, and every asset by type
    (the text of the text assets, a count for the rest)."""
    groups, by_id = [], {}
    for r in group_rows:
        gid = str(_get(r, "assetGroup", "id", default=""))
        if not gid or gid in by_id:
            continue
        g = {"key": "pmax:" + gid, "kind": "pmax", "assetGroupId": gid, "assetGroup": _get(r, "assetGroup", "name", default=""),
             "campaignId": str(_get(r, "campaign", "id", default="")), "campaign": _get(r, "campaign", "name", default=""),
             "strength": str(_get(r, "assetGroup", "adStrength", default="UNSPECIFIED")).upper(),
             "finalUrl": (_get(r, "assetGroup", "finalUrls", default=[""]) or [""])[0],
             "texts": {"HEADLINE": [], "LONG_HEADLINE": [], "DESCRIPTION": []}, "counts": {}, **_numbers(r)}
        groups.append(g)
        by_id[gid] = g
    for r in asset_rows:
        g = by_id.get(str(_get(r, "assetGroup", "id", default="")))
        if not g:
            continue
        ftype = str(_get(r, "assetGroupAsset", "fieldType", default="")).upper()
        if not ftype:
            continue
        g["counts"][ftype] = g["counts"].get(ftype, 0) + 1
        text = str(_get(r, "asset", "textAsset", "text", default="")).strip()
        if text and ftype in g["texts"]:
            g["texts"][ftype].append(text)
    return groups


def _walk_texts(node, key):
    """The texts of the first list called `key` (headlines or descriptions) found anywhere inside `node`."""
    if isinstance(node, dict):
        if isinstance(node.get(key), list):
            return [str(x.get("text") or "").strip() for x in node[key] if isinstance(x, dict) and str(x.get("text") or "").strip()]
        for v in node.values():
            found = _walk_texts(v, key)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _walk_texts(v, key)
            if found:
                return found
    return []


def read_recommendations(rows):
    """{adGroups: {ad group id: {headlines, descriptions, source}}, assetGroups: {asset group id: {adStrength}}}: the text Google
    itself recommends for an ad group's RSA (a whole new ad, extra assets for the ad, or a better version of it) and the
    Performance Max asset groups it says can be improved."""
    ad_groups, asset_groups = {}, {}
    for r in rows:
        rec = r.get("recommendation") or {}
        rtype = str(rec.get("type") or "")
        if rtype == "IMPROVE_PERFORMANCE_MAX_AD_STRENGTH":
            p = rec.get("improvePerformanceMaxAdStrengthRecommendation") or {}
            gid = str(p.get("assetGroup") or "").rsplit("/", 1)[-1]
            if gid:
                asset_groups[gid] = {"adStrength": str(p.get("adStrength") or "").upper() or None}
            continue
        ag = str(rec.get("adGroup") or "").rsplit("/", 1)[-1]
        if not ag:
            continue
        if rtype == "RESPONSIVE_SEARCH_AD":
            node = (rec.get("responsiveSearchAdRecommendation") or {}).get("ad") or {}
        elif rtype == "RESPONSIVE_SEARCH_AD_ASSET":
            p = rec.get("responsiveSearchAdAssetRecommendation") or {}
            node = p.get("recommendedAssets") or {k: v for k, v in p.items() if k != "currentAd"}
        elif rtype == "RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH":
            p = rec.get("responsiveSearchAdImproveAdStrengthRecommendation") or {}
            node = p.get("recommendedAd") or {k: v for k, v in p.items() if k != "currentAd"}
        else:
            continue
        h, d = _walk_texts(node, "headlines"), _walk_texts(node, "descriptions")
        if h or d:
            slot = ad_groups.setdefault(ag, {"headlines": [], "descriptions": [], "sources": []})
            slot["headlines"] += [t for t in h if t not in slot["headlines"]]
            slot["descriptions"] += [t for t in d if t not in slot["descriptions"]]
            if rtype not in slot["sources"]:
                slot["sources"].append(rtype)
    return {"adGroups": ad_groups, "assetGroups": asset_groups}


def read_keywords(rows, per_group=KEYWORDS_PER_GROUP):
    """{ad group id: [keyword text, ...]}: each ad group's keywords with the most impressions first, repeats (the same word
    in several match types) once."""
    seen = {}
    for r in rows:
        ag = str(_get(r, "adGroup", "id", default=""))
        text = str(_get(r, "adGroupCriterion", "keyword", "text", default="")).strip()
        if not ag or not text:
            continue
        slot = seen.setdefault(ag, {})
        total, first = slot.get(text.lower(), (0, text))
        slot[text.lower()] = (total + _i(_get(r, "metrics", "impressions")), first)
    return {ag: [t for _i_, t in sorted(v.values(), key=lambda x: (-x[0], x[1].lower()))[:per_group]] for ag, v in seen.items()}


# ── One account ─────────────────────────────────────────────────────────────

def read_account(member, *, search_fn=None):
    """{ads, groups, recs, warnings} for one Google account. The ratings are the point, so a failure there is raised; the
    keywords and Google's recommendations are extras, and a failure there is a warning."""
    search = search_fn or gads.search
    cid, name = member["id"], member["name"]
    ads = read_rsas(search(cid, RSA_GAQL))
    groups = read_groups(search(cid, GROUP_GAQL), search(cid, GROUP_ASSETS_GAQL))
    warnings = []
    try:
        recs = read_recommendations(search(cid, RECS_GAQL))
    except gads.GadsError as e:
        recs, warnings = {"adGroups": {}, "assetGroups": {}}, ["%s: Google's recommendations could not be read (%s)." % (name, e)]
    weak = sorted({a["adGroupId"] for a in ads if insights.needs_work(a["strength"])})
    keywords = {}
    try:
        for i in range(0, len(weak), GROUP_CHUNK):
            keywords.update(read_keywords(search(cid, keywords_gaql(weak[i:i + GROUP_CHUNK]))))
    except gads.GadsError as e:
        warnings.append("%s: the keywords could not be read (%s), so keyword advice is left out." % (name, e))
    for a in ads:
        a["account"], a["accountId"] = name, cid
        a["keywords"] = keywords.get(a["adGroupId"], [])
        a["google"] = recs["adGroups"].get(a["adGroupId"])
    for g in groups:
        g["account"], g["accountId"] = name, cid
        g["google"] = recs["assetGroups"].get(g["assetGroupId"])
    return {"ads": ads, "groups": groups, "warnings": warnings}


def google_raw(account, *, search_fn=None, workers=6):
    """The raw read of one account or an MCC's accounts together: {ads, groups, warnings, members}. An account that cannot be
    read is left out and said so; every one failing is raised."""
    members = account.get("members") or [{"id": account["id"], "name": account["name"]}]

    def run(m):
        try:
            return m, read_account(m, search_fn=search_fn), None
        except gads.GadsError as e:
            return m, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as ex:
        done = list(ex.map(run, members))
    ok = [(m, r) for m, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    warnings = ["%s could not be read: %s" % (m["name"], e) for m, r, e in done if e]
    out = {"ads": [], "groups": [], "warnings": warnings, "members": [m["name"] for m in members] if account.get("members") else []}
    for _m, r in ok:
        out["ads"] += r["ads"]
        out["groups"] += r["groups"]
        out["warnings"] += r["warnings"]
    return out


# ── The report ──────────────────────────────────────────────────────────────

def _view(items, analyse):
    rows = sorted((analyse(i) for i in items), key=lambda x: (-x["priority"], -x["impressions"], x["name"].lower()))
    counts = {}
    for r in rows:
        counts[r["strength"]] = counts.get(r["strength"], 0) + 1
    below = [r for r in rows if r["belowGood"]]
    imps = sum(r["impressions"] for r in rows)
    return {"items": rows, "total": len(rows), "counts": counts, "belowGood": len(below), "impressions": imps,
            "impressionsBelowGood": sum(r["impressions"] for r in below)}


def assemble(raw, account, today):
    """The page's report from a raw read: a view for Search (RSA) and one for Performance Max, each item analysed."""
    search = _view(raw["ads"], insights.analyse_rsa)
    pmax = _view(raw["groups"], insights.analyse_group)
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "window": {"days": WINDOW_DAYS, "to": today.isoformat(), "from": (today - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()},
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "warnings": list(raw.get("warnings") or []),
        "search": search, "pmax": pmax,
        "summary": insights.summarise(search, pmax),
    }


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None):
    """The ad strength report for a Google Ads account or an MCC the root MCC holds. `force` (Refresh) skips the saved read, at
    most once per paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_geo, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
    raw, stale = paid_geo._cached("adstrength", account["id"], "ads", day, day,
                                  lambda: google_raw(account, search_fn=search_fn), force=force, store=store, now=now)
    report = assemble(raw, account, day)
    return dict(report, stale=stale) if stale else report


def find_item(report, key):
    """The analysed item with this key (an RSA `rsa:<ad group>:<ad>` or an asset group `pmax:<id>`), or None."""
    for view in (report["search"], report["pmax"]):
        for it in view["items"]:
            if it["key"] == key:
                return it
    return None
