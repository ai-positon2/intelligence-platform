"""Paid Marketing Agents: Asset Coverage Audit, the reading and the report.

For a Google Ads account (or an MCC's accounts together) this reads every asset the live campaigns are built from or shown
with, and says what each campaign is missing:

    Search / Performance Max  the sitelinks, callouts, structured snippets, calls, images, business name, logo, promotions, prices
                              and lead forms linked to the campaign, to its ad groups and to the account (the lowest level
                              that holds a live one wins), and the location sets linked
    Performance Max           every asset group's headlines, descriptions, images, logos, business name and videos
    Display / Demand Gen      every ad's images, logos, videos, headlines, descriptions and business name

and for each kind of asset how many are live, disapproved, paused, past their end date or under review, and how many that
campaign has never shown in the last 30 days. What a campaign *should* hold is tracker/paid_assets_rules.py. This module reads
(one cheap query per kind of thing, compacted to counts so the saved read stays small), then holds each campaign against the rules.

Only enabled campaigns, ad groups and ads are read; removed links are never counted. Google Ads only (Meta has no asset
library of this kind). Reading is cached through the shared cache (tracker/paid_geo.py _cached): one read per account per
day-ish, Refresh limited, a spent quota served from the last saved read with its date. A part that cannot be read is a
warning and its slots say "unknown"; the audit never reports "missing" for something it could not look at.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_assets_rules as rules
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_MEMBERS = seg.MAX_MEMBERS
WINDOW_DAYS = 30
BAD_EXAMPLES = 6                      # problem assets named per kind, per place

_LIVE = "campaign.status = ENABLED"
_DURING = "segments.date DURING LAST_30_DAYS"
_ASSET_FIELDS = (
    "asset.id, asset.type, asset.name, asset.sitelink_asset.link_text, asset.sitelink_asset.end_date, asset.callout_asset.callout_text, "
    "asset.structured_snippet_asset.header, asset.call_asset.phone_number, asset.promotion_asset.promotion_target, "
    "asset.promotion_asset.end_date, asset.price_asset.type, asset.text_asset.text, asset.youtube_video_asset.youtube_video_title, "
    "asset.lead_form_asset.business_name")

CAMPAIGNS_GAQL = ("SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.advertising_channel_sub_type, campaign.status "
                  "FROM campaign WHERE " + _LIVE)
CAMPAIGN_METRICS_GAQL = ("SELECT campaign.id, campaign.status, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
                         "FROM campaign WHERE " + _LIVE + " AND " + _DURING)
CAMPAIGN_ASSETS_GAQL = ("SELECT campaign.id, campaign.status, campaign_asset.field_type, campaign_asset.status, campaign_asset.primary_status, "
                        "campaign_asset.primary_status_reasons, " + _ASSET_FIELDS + " FROM campaign_asset WHERE " + _LIVE +
                        " AND campaign_asset.status != REMOVED")
CAMPAIGN_ASSETS_SHOWN_GAQL = ("SELECT campaign.id, campaign.status, campaign_asset.field_type, campaign_asset.status, asset.id, metrics.impressions "
                              "FROM campaign_asset WHERE " + _LIVE + " AND campaign_asset.status = ENABLED AND " + _DURING +
                              " AND metrics.impressions > 0")
CUSTOMER_ASSETS_GAQL = ("SELECT customer_asset.field_type, customer_asset.status, customer_asset.primary_status, customer_asset.primary_status_reasons, "
                        + _ASSET_FIELDS + " FROM customer_asset WHERE customer_asset.status != REMOVED")
AD_GROUP_ASSETS_GAQL = ("SELECT campaign.id, campaign.status, ad_group.id, ad_group.status, ad_group_asset.field_type, ad_group_asset.status, "
                        "ad_group_asset.primary_status FROM ad_group_asset WHERE " + _LIVE + " AND ad_group.status = ENABLED AND ad_group_asset.status = ENABLED")
GROUPS_GAQL = ("SELECT campaign.id, campaign.status, asset_group.id, asset_group.name, asset_group.status FROM asset_group "
               "WHERE " + _LIVE + " AND asset_group.status = ENABLED")
GROUP_ASSETS_GAQL = ("SELECT campaign.id, campaign.status, asset_group.id, asset_group.status, asset_group_asset.field_type, asset_group_asset.status, "
                     "asset_group_asset.primary_status, asset_group_asset.primary_status_reasons, " + _ASSET_FIELDS + " FROM asset_group_asset "
                     "WHERE " + _LIVE + " AND asset_group.status = ENABLED AND asset_group_asset.status != REMOVED")
CAMPAIGN_SETS_GAQL = ("SELECT campaign.id, campaign.status, campaign_asset_set.status, asset_set.type, asset_set.status FROM campaign_asset_set "
                      "WHERE " + _LIVE + " AND campaign_asset_set.status = ENABLED AND asset_set.status = ENABLED")
CUSTOMER_SETS_GAQL = ("SELECT customer_asset_set.status, asset_set.type, asset_set.status FROM customer_asset_set "
                      "WHERE customer_asset_set.status = ENABLED AND asset_set.status = ENABLED")
_AD_WHERE = " WHERE ad_group_ad.ad.type = %s AND ad_group_ad.status = ENABLED AND ad_group.status = ENABLED AND " + _LIVE
_AD_HEAD = "SELECT campaign.id, campaign.status, ad_group.id, ad_group.name, ad_group.status, ad_group_ad.ad.id, ad_group_ad.status, ad_group_ad.ad.type, "
ADS_GAQL = {
    "RESPONSIVE_DISPLAY_AD": (_AD_HEAD + "ad_group_ad.ad.responsive_display_ad.marketing_images, ad_group_ad.ad.responsive_display_ad.square_marketing_images, "
                              "ad_group_ad.ad.responsive_display_ad.logo_images, ad_group_ad.ad.responsive_display_ad.square_logo_images, "
                              "ad_group_ad.ad.responsive_display_ad.headlines, ad_group_ad.ad.responsive_display_ad.long_headline, "
                              "ad_group_ad.ad.responsive_display_ad.descriptions, ad_group_ad.ad.responsive_display_ad.business_name, "
                              "ad_group_ad.ad.responsive_display_ad.youtube_videos FROM ad_group_ad" + _AD_WHERE % "RESPONSIVE_DISPLAY_AD"),
    "DEMAND_GEN_MULTI_ASSET_AD": (_AD_HEAD + "ad_group_ad.ad.demand_gen_multi_asset_ad.marketing_images, ad_group_ad.ad.demand_gen_multi_asset_ad.square_marketing_images, "
                                  "ad_group_ad.ad.demand_gen_multi_asset_ad.portrait_marketing_images, ad_group_ad.ad.demand_gen_multi_asset_ad.logo_images, "
                                  "ad_group_ad.ad.demand_gen_multi_asset_ad.headlines, ad_group_ad.ad.demand_gen_multi_asset_ad.descriptions, "
                                  "ad_group_ad.ad.demand_gen_multi_asset_ad.business_name FROM ad_group_ad" + _AD_WHERE % "DEMAND_GEN_MULTI_ASSET_AD"),
    "DEMAND_GEN_VIDEO_RESPONSIVE_AD": (_AD_HEAD + "ad_group_ad.ad.demand_gen_video_responsive_ad.videos, ad_group_ad.ad.demand_gen_video_responsive_ad.logo_images, "
                                       "ad_group_ad.ad.demand_gen_video_responsive_ad.headlines, ad_group_ad.ad.demand_gen_video_responsive_ad.long_headlines, "
                                       "ad_group_ad.ad.demand_gen_video_responsive_ad.descriptions, ad_group_ad.ad.demand_gen_video_responsive_ad.business_name "
                                       "FROM ad_group_ad" + _AD_WHERE % "DEMAND_GEN_VIDEO_RESPONSIVE_AD"),
}
# Where each ad format keeps each kind of asset (the Google Ads field, a list of assets or a single text) -> our field key.
AD_FIELDS = {
    "RESPONSIVE_DISPLAY_AD": ("responsiveDisplayAd", {"marketingImages": "MARKETING_IMAGE", "squareMarketingImages": "SQUARE_MARKETING_IMAGE",
                                                      "logoImages": "LOGO", "squareLogoImages": "SQUARE_LOGO", "headlines": "HEADLINE",
                                                      "longHeadline": "LONG_HEADLINE", "descriptions": "DESCRIPTION", "businessName": "BUSINESS_NAME",
                                                      "youtubeVideos": "YOUTUBE_VIDEO"}),
    "DEMAND_GEN_MULTI_ASSET_AD": ("demandGenMultiAssetAd", {"marketingImages": "MARKETING_IMAGE", "squareMarketingImages": "SQUARE_MARKETING_IMAGE",
                                                            "portraitMarketingImages": "PORTRAIT_MARKETING_IMAGE", "logoImages": "LOGO",
                                                            "headlines": "HEADLINE", "descriptions": "DESCRIPTION", "businessName": "BUSINESS_NAME"}),
    "DEMAND_GEN_VIDEO_RESPONSIVE_AD": ("demandGenVideoResponsiveAd", {"videos": "YOUTUBE_VIDEO", "logoImages": "LOGO", "headlines": "HEADLINE",
                                                                      "longHeadlines": "LONG_HEADLINE", "descriptions": "DESCRIPTION",
                                                                      "businessName": "BUSINESS_NAME"}),
}


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


def _label(row):
    """A name for the asset a row is about: its link text, callout, snippet header, phone number, promotion, text or video title."""
    a = row.get("asset") or {}
    for path in (("sitelinkAsset", "linkText"), ("calloutAsset", "calloutText"), ("structuredSnippetAsset", "header"), ("callAsset", "phoneNumber"),
                 ("promotionAsset", "promotionTarget"), ("textAsset", "text"), ("youtubeVideoAsset", "youtubeVideoTitle"),
                 ("leadFormAsset", "businessName"), ("priceAsset", "type")):
        v = _get(a, *path)
        if v:
            return str(v)[:60]
    return str(a.get("name") or "")[:60]


def _end(row):
    a = row.get("asset") or {}
    return str(_get(a, "sitelinkAsset", "endDate", default="") or _get(a, "promotionAsset", "endDate", default="") or "")[:10]


def _link(node, row):
    """One asset link as {f: field, st, ps, rs, a: asset id, l: label, e: end date}, from the row's *Asset node (campaignAsset...)."""
    return {"f": str(node.get("fieldType") or "").upper(), "st": str(node.get("status") or "ENABLED").upper(),
            "ps": str(node.get("primaryStatus") or "").upper(), "rs": [str(r).upper() for r in node.get("primaryStatusReasons") or []],
            "a": str(_get(row, "asset", "id", default="")), "l": _label(row), "e": _end(row)}


def bucket(links, today=None, shown=None, scope=None):
    """{live, pending, disapproved, paused, expired, notShown, bad: [{label, state}]} for a list of links of one kind. `shown` is a set of
    (scope, asset id) pairs that had impressions; a live link missing from it is "not shown"."""
    out = {"live": 0, "pending": 0, "disapproved": 0, "paused": 0, "expired": 0, "notShown": 0, "bad": [], "ids": []}
    for l in links:
        state = rules.link_state(l, today)
        out[state] += 1
        if state in ("disapproved", "expired") and l.get("a") and l["a"] not in out["ids"]:
            out["ids"].append(l["a"])
        if state in ("disapproved", "expired") and len(out["bad"]) < BAD_EXAMPLES:
            out["bad"].append({"label": l.get("l") or "(unnamed)", "state": state})
        if state in ("live", "pending") and shown is not None and l["f"] in rules.SHOWN_FIELDS and (scope, l.get("a")) not in shown:
            out["notShown"] += 1
    return out


def merge(*buckets):
    """The sum of buckets (several fields of one kind, or the same kind at two levels)."""
    out = {"live": 0, "pending": 0, "disapproved": 0, "paused": 0, "expired": 0, "notShown": 0, "bad": [], "ids": []}
    for b in buckets:
        if not b:
            continue
        for k in ("live", "pending", "disapproved", "paused", "expired", "notShown"):
            out[k] += b.get(k, 0)
        out["bad"] += [x for x in b.get("bad", []) if len(out["bad"]) < BAD_EXAMPLES]
        out["ids"] += [i for i in b.get("ids", []) if i not in out["ids"]]
    out["have"] = out["live"] + out["pending"]
    return out


def read_campaigns(rows):
    """{campaign id: {id, name, type, subType}} for the enabled campaigns."""
    out = {}
    for r in rows:
        cid = str(_get(r, "campaign", "id", default=""))
        if cid:
            out[cid] = {"id": cid, "name": _get(r, "campaign", "name", default="") or "",
                        "type": str(_get(r, "campaign", "advertisingChannelType", default="") or "").upper(),
                        "subType": str(_get(r, "campaign", "advertisingChannelSubType", default="") or "").upper()}
    return out


def read_metrics(rows):
    """{campaign id: {impressions, clicks, cost, conversions}} for the last 30 days."""
    out = {}
    for r in rows:
        cid = str(_get(r, "campaign", "id", default=""))
        if not cid:
            continue
        m = r.get("metrics") or {}
        slot = out.setdefault(cid, {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0})
        slot["impressions"] += _i(m.get("impressions"))
        slot["clicks"] += _i(m.get("clicks"))
        slot["cost"] += _i(m.get("costMicros")) / 1e6
        slot["conversions"] += float(m.get("conversions") or 0)
    return {k: {"impressions": v["impressions"], "clicks": v["clicks"], "cost": round(v["cost"], 2), "conversions": round(v["conversions"], 2)}
            for k, v in out.items()}


def read_shown(rows):
    """The set of (campaign id, asset id) pairs whose link had impressions in the last 30 days."""
    return {(str(_get(r, "campaign", "id", default="")), str(_get(r, "asset", "id", default=""))) for r in rows if _i(_get(r, "metrics", "impressions")) > 0}


def read_campaign_assets(rows, shown, today=None):
    """{campaign id: {field: bucket}} from the campaign_asset rows."""
    by = {}
    for r in rows:
        cid = str(_get(r, "campaign", "id", default=""))
        link = _link(r.get("campaignAsset") or {}, r)
        if cid and link["f"]:
            by.setdefault(cid, {}).setdefault(link["f"], []).append(link)
    return {cid: {f: bucket(links, today, shown, cid) for f, links in fields.items()} for cid, fields in by.items()}


def read_customer_assets(rows, today=None):
    """{field: bucket} for the account-wide links."""
    by = {}
    for r in rows:
        link = _link(r.get("customerAsset") or {}, r)
        if link["f"]:
            by.setdefault(link["f"], []).append(link)
    return {f: bucket(links, today) for f, links in by.items()}


def read_ad_group_assets(rows):
    """{campaign id: {field: [ad group ids]}}: where an ad group holds its own live assets of a kind (they override the campaign's)."""
    out = {}
    for r in rows:
        n = r.get("adGroupAsset") or {}
        if rules.link_state({"st": n.get("status"), "ps": n.get("primaryStatus"), "rs": n.get("primaryStatusReasons")}) not in ("live", "pending"):
            continue
        cid, gid, f = str(_get(r, "campaign", "id", default="")), str(_get(r, "adGroup", "id", default="")), str(n.get("fieldType") or "").upper()
        if cid and gid and f:
            ids = out.setdefault(cid, {}).setdefault(f, [])
            if gid not in ids:
                ids.append(gid)
    return out


def read_groups(group_rows, asset_rows, today=None):
    """[{id, name, campaignId, assets: {field: bucket}}] for the enabled Performance Max asset groups."""
    groups, by_id = [], {}
    for r in group_rows:
        gid = str(_get(r, "assetGroup", "id", default=""))
        if gid and gid not in by_id:
            g = {"id": gid, "name": _get(r, "assetGroup", "name", default="") or "", "campaignId": str(_get(r, "campaign", "id", default="")), "links": {}}
            groups.append(g)
            by_id[gid] = g
    for r in asset_rows:
        g = by_id.get(str(_get(r, "assetGroup", "id", default="")))
        link = _link(r.get("assetGroupAsset") or {}, r)
        if g and link["f"]:
            g["links"].setdefault(link["f"], []).append(link)
    for g in groups:
        g["assets"] = {f: bucket(links, today) for f, links in g.pop("links").items()}
    return groups


def read_sets(campaign_rows, customer_rows):
    """{account: n, campaigns: {campaign id: n}}: the location sets (Business Profile, static and dynamic location groups) linked."""
    def is_loc(r):
        return str(_get(r, "assetSet", "type", default="")).upper() in rules.LOCATION_SET_TYPES
    out = {"account": sum(1 for r in customer_rows if is_loc(r)), "campaigns": {}}
    for r in campaign_rows:
        if is_loc(r):
            cid = str(_get(r, "campaign", "id", default=""))
            if cid:
                out["campaigns"][cid] = out["campaigns"].get(cid, 0) + 1
    return out


def _count(v):
    if isinstance(v, list):
        return sum(1 for x in v if x)
    if isinstance(v, dict):
        return 1 if str(v.get("text") or "").strip() else 0
    return 1 if str(v or "").strip() else 0


def read_ads(ad_type, rows):
    """[{campaignId, adGroupId, adGroup, adId, type, counts: {field: n}}] for one ad format."""
    node, mapping = AD_FIELDS[ad_type]
    out = []
    for r in rows:
        ad = _get(r, "adGroupAd", "ad", default={}) or {}
        body = ad.get(node) or {}
        out.append({"campaignId": str(_get(r, "campaign", "id", default="")), "adGroupId": str(_get(r, "adGroup", "id", default="")),
                    "adGroup": _get(r, "adGroup", "name", default="") or "", "adId": str(ad.get("id") or ""), "type": ad_type,
                    "counts": {field: _count(body.get(key)) for key, field in mapping.items()}})
    return out


# ── One account ─────────────────────────────────────────────────────────────

def read_account(member, *, search_fn=None, today=None):
    """The compact read of one account. The campaign list is the point, so a failure there is raised; every other part is an extra
    whose failure is a warning and an `ok` flag of False (its slots then say unknown instead of missing)."""
    search = search_fn or gads.search
    cid, name = member["id"], member["name"]
    today = today or dt.date.today()
    campaigns = read_campaigns(search(cid, CAMPAIGNS_GAQL))
    warnings, ok = [], {}

    def part(key, what, fn, empty):
        try:
            ok[key] = True
            return fn()
        except gads.GadsError as e:
            ok[key] = False
            warnings.append("%s: %s could not be read (%s)." % (name, what, e))
            return empty
    metrics = part("metrics", "the last 30 days' numbers", lambda: read_metrics(search(cid, CAMPAIGN_METRICS_GAQL)), {})
    shown = part("shown", "which assets were shown", lambda: read_shown(search(cid, CAMPAIGN_ASSETS_SHOWN_GAQL)), None)
    campaign_assets = part("campaignAssets", "the campaign assets", lambda: read_campaign_assets(search(cid, CAMPAIGN_ASSETS_GAQL), shown, today), {})
    customer_assets = part("customerAssets", "the account-wide assets", lambda: read_customer_assets(search(cid, CUSTOMER_ASSETS_GAQL), today), {})
    ad_group_assets = part("adGroupAssets", "the ad group assets", lambda: read_ad_group_assets(search(cid, AD_GROUP_ASSETS_GAQL)), {})
    types = {c["type"] for c in campaigns.values()}
    groups = []
    if "PERFORMANCE_MAX" in types:
        groups = part("groups", "the Performance Max asset groups", lambda: read_groups(search(cid, GROUPS_GAQL), search(cid, GROUP_ASSETS_GAQL), today), [])
    ads = []
    for ad_type, channel in (("RESPONSIVE_DISPLAY_AD", "DISPLAY"), ("DEMAND_GEN_MULTI_ASSET_AD", "DEMAND_GEN"), ("DEMAND_GEN_VIDEO_RESPONSIVE_AD", "DEMAND_GEN")):
        if channel in types:
            ads += part("ads:" + ad_type, "the %s ads" % rules.AD_SPECS[ad_type][2].lower(), lambda t=ad_type: read_ads(t, search(cid, ADS_GAQL[t])), [])
    sets = part("sets", "the location sets", lambda: read_sets(search(cid, CAMPAIGN_SETS_GAQL), search(cid, CUSTOMER_SETS_GAQL)), {"account": 0, "campaigns": {}})
    return {"id": cid, "name": name, "campaigns": campaigns, "metrics": metrics, "campaignAssets": campaign_assets, "customerAssets": customer_assets,
            "adGroupAssets": ad_group_assets, "groups": groups, "ads": ads, "sets": sets, "ok": ok, "warnings": warnings}


def google_raw(account, *, search_fn=None, workers=6, today=None):
    """The raw read of one account or an MCC's accounts together: {accounts: [read_account...], warnings, members}. An account that
    cannot be read is left out and said so; every one failing is raised."""
    members = account.get("members") or [{"id": account["id"], "name": account["name"]}]

    def run(m):
        try:
            return m, read_account(m, search_fn=search_fn, today=today), None
        except gads.GadsError as e:
            return m, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as ex:
        done = list(ex.map(run, members))
    ok = [(m, r) for m, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    warnings = ["%s could not be read: %s" % (m["name"], e) for m, r, e in done if e]
    for _m, r in ok:
        warnings += r.pop("warnings")
    return {"accounts": [r for _m, r in ok], "warnings": warnings, "members": [m["name"] for m in members] if account.get("members") else []}


# ── Holding each campaign against the rules ─────────────────────────────────

def _unknown(key, spec, source):
    return {"key": key, "label": spec["label"], "have": None, "min": spec["min"], "target": spec["target"], "status": "unknown", "severity": None,
            "add": 0, "source": source, "disapproved": 0, "paused": 0, "pending": 0, "expired": 0, "notShown": 0, "bad": [],
            "message": "Google Ads could not be read for this, so it is not judged."}


def _ext_slot(key, spec, camp_b, acct_b, ad_groups, ok, impressions):
    """One kind of campaign asset: the campaign's own live links, or else the account's. Problems named are the campaign's own: the
    account's are reported once under the account's assets, not once per campaign."""
    if not (ok.get("campaignAssets") and ok.get("customerAssets")):
        return _unknown(key, spec, "campaign")
    fields = spec.get("fields") or (key,)
    cb, ub = merge(*[camp_b.get(f) for f in fields]), merge(*[acct_b.get(f) for f in fields])
    inherit = cb["have"] == 0 and ub["have"] > 0
    have, source = (ub["have"], "account") if inherit else (cb["have"], "campaign")
    own = sorted({g for f in fields for g in ad_groups.get(f, [])})
    s = rules.slot(key, spec, have, cb, source=source, extra={"adGroups": len(own), "bad": cb["bad"]})
    if source == "campaign" and impressions > 0:
        s["notShown"] = cb["notShown"]
    if own and s["status"] != "ok":
        s["message"] += " 1 ad group holds its own." if len(own) == 1 else " %d ad groups hold their own." % len(own)
    return s


def _location_slot(cid, sets, ok):
    if not ok.get("sets"):
        return _unknown("LOCATION", rules.LOCATION, "campaign")
    own = (sets.get("campaigns") or {}).get(cid, 0)
    have, source = (own, "campaign") if own else (sets.get("account", 0), "account")
    return rules.slot("LOCATION", rules.LOCATION, have, None, source=source)


def _group_slots(group, camp_b, acct_b, ok):
    slots = []
    for key in rules.PMAX_GROUP_ORDER:
        spec = rules.PMAX_GROUP[key]
        fields = spec.get("fields") or (key,)
        gb = merge(*[group["assets"].get(f) for f in fields])
        have, source = gb["have"], "asset group"
        if key in rules.PMAX_FROM_CAMPAIGN and have == 0:
            if not ok.get("campaignAssets") or not ok.get("customerAssets"):
                slots.append(_unknown(key, spec, "asset group"))
                continue
            cb = merge(*[camp_b.get(f) for f in rules.PMAX_FROM_CAMPAIGN[key]])
            ub = merge(*[acct_b.get(f) for f in (("BUSINESS_LOGO",) if key == "LOGO" else ())])
            if cb["have"]:
                have, source = cb["have"], "campaign"
            elif ub["have"]:
                have, source = ub["have"], "account"
        s = rules.slot(key, spec, have, gb, source=source, extra={"bad": gb["bad"]})
        slots.append(s)
    return slots


def _ad_slots(ad):
    spec_map, order, _label = rules.AD_SPECS[ad["type"]]
    slots = []
    for key in order:
        spec = spec_map[key]
        have = sum(ad["counts"].get(f, 0) for f in (spec.get("fields") or (key,)))
        slots.append(rules.slot(key, spec, have, None, source="ad"))
    if ad["type"] == "DEMAND_GEN_MULTI_ASSET_AD" and not any(ad["counts"].get(f, 0) for f in rules.DEMAND_GEN_ANY_IMAGE):
        spec = rules._s("Images", "image", 1, 1, rules.HIGH, rules.LOW)
        slots.insert(0, rules.slot("IMAGES", spec, 0, None, source="ad"))
    return slots


_ALWAYS = ("key", "label", "have", "min", "target", "status", "source")


def _slim(slots):
    """A slot without its zero counters, empty lists and empty words: a report for a big account has thousands of them, and the page
    treats a missing counter as zero."""
    return [{k: v for k, v in s.items() if k in _ALWAYS or v not in (0, [], "", None)} for s in slots]


def _gaps(scope, slots):
    """The shortfalls among these slots, each carrying its place in the rules' own order (`_n`) so equally bad ones read in that order."""
    return [{"scope": scope, "key": s["key"], "label": s["label"], "severity": s["severity"], "have": s["have"], "target": s["target"], "add": s["add"],
             "message": s["message"], "_n": n} for n, s in enumerate(slots) if s.get("severity") and s["severity"] != rules.INFO]


def _fold(gaps, noun):
    """One line for the same shortfall in several asset groups (or ads): '4 asset groups (Crowns, Dentures, General and 1 more)'. Gaps with
    the same kind, severity and words are folded; a single one keeps its own scope."""
    folded, order = {}, []
    for g in gaps:
        k = (g["key"], g["severity"], g["message"])
        if k not in folded:
            folded[k] = dict(g, names=[])
            order.append(k)
        folded[k]["names"].append(g["scope"].split(": ", 1)[-1])
    out = []
    for k in order:
        g = folded[k]
        names = g.pop("names")
        if len(names) > 1:
            g["scope"] = "%d %ss (%s)" % (len(names), noun, ", ".join(names[:3]) + (" and %d more" % (len(names) - 3) if len(names) > 3 else ""))
        out.append(g)
    return out


def analyse_account(a, today=None):
    """[campaign item] for the audited campaigns of one read account, plus the account-level view."""
    ok, camps = a.get("ok") or {}, a["campaigns"]
    items, not_audited, disapproved = [], {}, set()
    groups_by, ads_by = {}, {}
    for g in a.get("groups") or []:
        groups_by.setdefault(g["campaignId"], []).append(g)
    for ad in a.get("ads") or []:
        ads_by.setdefault(ad["campaignId"], []).append(ad)
    acct_b = a.get("customerAssets") or {}
    qualify = lambda ids: ["%s:%s" % (a["id"], i) for i in ids]      # noqa: E731  (an asset id is only unique inside its account)
    for b in acct_b.values():
        disapproved.update(qualify(b.get("ids", [])))
    for cid, c in camps.items():
        if c["type"] not in rules.CHANNELS:
            not_audited[c["type"]] = not_audited.get(c["type"], 0) + 1
            continue
        m = (a.get("metrics") or {}).get(cid) or {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0}
        camp_b = (a.get("campaignAssets") or {}).get(cid, {})
        for b in camp_b.values():
            disapproved.update(qualify(b.get("ids", [])))
        slots, groups, ads = [], [], []
        if c["type"] in ("SEARCH", "PERFORMANCE_MAX"):
            for key in rules.CAMPAIGN_ASSET_ORDER:
                spec = rules.CAMPAIGN_ASSETS[key]
                if c["type"] in spec.get("channels", ("SEARCH", "PERFORMANCE_MAX")):
                    slots.append(_ext_slot(key, spec, camp_b, acct_b, (a.get("adGroupAssets") or {}).get(cid, {}), ok, m["impressions"]))
            slots.append(_location_slot(cid, a.get("sets") or {}, ok))
        if c["type"] == "PERFORMANCE_MAX":
            for g in groups_by.get(cid, []):
                for b in g["assets"].values():
                    disapproved.update(qualify(b.get("ids", [])))
                gs = _group_slots(g, camp_b, acct_b, ok)
                groups.append({"id": g["id"], "name": g["name"], "slots": gs, "severity": rules.worst(gs)})
        for ad in ads_by.get(cid, []):
            sl = _ad_slots(ad)
            ads.append({"id": ad["adId"], "adGroup": ad["adGroup"], "type": ad["type"], "typeLabel": rules.AD_SPECS[ad["type"]][2], "slots": sl,
                        "severity": rules.worst(sl)})
        gaps = _gaps("Campaign", slots)
        gaps += _fold([x for g in groups for x in _gaps("Asset group: " + (g["name"] or g["id"]), g["slots"])], "asset group")
        gaps += _fold([x for ad in ads for x in _gaps("%s: %s" % (ad["typeLabel"], ad["adGroup"] or "an ad group"), ad["slots"])], "ad")
        gaps.sort(key=lambda x: (-rules.SEVERITY_RANK[x["severity"]], x["scope"] != "Campaign", x["_n"], x["scope"]))
        for g in gaps:
            del g["_n"]
        counts = {k: sum(1 for x in gaps if x["severity"] == k) for k in (rules.HIGH, rules.MEDIUM, rules.LOW)}
        problems = {k: sum(s.get(k, 0) for s in slots) + sum(s.get(k, 0) for g in groups for s in g["slots"]) for k in ("disapproved", "paused", "pending", "expired")}
        not_shown = sum(s.get("notShown", 0) for s in slots)
        slots = _slim(slots)
        for g in groups:
            g["slots"] = _slim(g["slots"])
        for ad in ads:
            ad["slots"] = _slim(ad["slots"])
        items.append({"key": "%s:%s" % (a["id"], cid), "id": cid, "name": c["name"], "type": c["type"], "typeLabel": rules.channel_label(c["type"]),
                      "subType": c["subType"], "account": a["name"], "accountId": a["id"], **m,
                      "slots": slots, "groups": groups, "ads": ads, "gaps": gaps, "counts": counts,
                      "severity": gaps[0]["severity"] if gaps else None,
                      "notShown": not_shown, "problems": problems})
    account_assets = []
    if ok.get("customerAssets"):
        for key in rules.CAMPAIGN_ASSET_ORDER:
            spec = rules.CAMPAIGN_ASSETS[key]
            b = merge(*[acct_b.get(f) for f in (spec.get("fields") or (key,))])
            if b["live"] + b["pending"] + b["disapproved"] + b["paused"] + b["expired"]:
                account_assets.append({"key": key, "label": spec["label"], "have": b["have"], "disapproved": b["disapproved"], "paused": b["paused"],
                                       "pending": b["pending"], "expired": b["expired"], "bad": b["bad"]})
    return {"items": items, "notAudited": not_audited, "disapproved": sorted(disapproved),
            "accountAssets": {"account": a["name"], "accountId": a["id"], "slots": account_assets, "locations": (a.get("sets") or {}).get("account", 0)}}


def _rank(item):
    return (-(rules.SEVERITY_RANK[item["severity"]] if item["severity"] else -1), -item["impressions"], item["name"].lower())


def coverage(items):
    """For each kind of campaign asset, across the Search and Performance Max campaigns it applies to: how many are at the target,
    below it, have none at all, or could not be judged."""
    out = []
    for key in rules.CAMPAIGN_ASSET_ORDER + ("LOCATION",):
        spec = rules.LOCATION if key == "LOCATION" else rules.CAMPAIGN_ASSETS[key]
        slots = [s for it in items for s in it["slots"] if s["key"] == key]
        if not slots:
            continue
        out.append({"key": key, "label": spec["label"], "campaigns": len(slots), "atTarget": sum(s["status"] == "ok" for s in slots),
                    "below": sum(s["status"] in ("thin", "short") for s in slots), "none": sum(s["status"] == "missing" for s in slots),
                    "unknown": sum(s["status"] == "unknown" for s in slots), "optional": spec["missing"] == rules.INFO})
    return out


def assemble(raw, account, today):
    """The page's report from a raw read."""
    per = [analyse_account(a, today) for a in raw["accounts"]]
    items = sorted((i for p in per for i in p["items"]), key=_rank)
    not_audited = {}
    for p in per:
        for k, n in p["notAudited"].items():
            not_audited[k] = not_audited.get(k, 0) + n
    disapproved = {i for p in per for i in p["disapproved"]}
    gaps = [g for i in items for g in i["gaps"]]
    imps = sum(i["impressions"] for i in items)
    fix_first = [i for i in items if i["severity"] == rules.HIGH]
    summary = {
        "campaigns": len(items), "withGaps": sum(1 for i in items if i["gaps"]), "fixFirst": len(fix_first),
        "gaps": {k: sum(1 for g in gaps if g["severity"] == k) for k in (rules.HIGH, rules.MEDIUM, rules.LOW)},
        "impressions": imps, "impressionsFixFirst": sum(i["impressions"] for i in fix_first),
        "disapprovedAssets": len(disapproved), "paused": sum(i["problems"]["paused"] for i in items),
        "expired": sum(i["problems"]["expired"] for i in items), "pending": sum(i["problems"]["pending"] for i in items),
        "notShown": sum(i["notShown"] for i in items), "coverage": coverage(items)}
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "window": {"days": WINDOW_DAYS, "to": today.isoformat(), "from": (today - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()},
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "warnings": list(raw.get("warnings") or []),
        "campaigns": items, "accountAssets": [p["accountAssets"] for p in per],
        "notAudited": [{"type": k, "label": rules.channel_label(k), "campaigns": n} for k, n in sorted(not_audited.items(), key=lambda kv: -kv[1])],
        "summary": summary,
        "targets": {"campaignAssets": {k: {"min": v["min"], "target": v["target"], "label": v["label"], "why": v["why"]} for k, v in rules.CAMPAIGN_ASSETS.items()}},
    }


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None):
    """The asset coverage report for a Google Ads account or an MCC the root MCC holds. `force` (Refresh) skips the saved read, at
    most once per paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_geo, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
    raw, stale = paid_geo._cached("assets", account["id"], "audit", day, day,
                                  lambda: google_raw(account, search_fn=search_fn, today=day), force=force, store=store, now=now)
    report = assemble(raw, account, day)
    return dict(report, stale=stale) if stale else report
