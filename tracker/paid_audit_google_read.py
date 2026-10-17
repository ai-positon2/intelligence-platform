"""Paid Marketing Agents: Account Audit, reading one Google Ads account.

One cheap query per part of the account, each compacted to what the checks need (counts, shares, a few named examples) so the saved
read stays small even for an account with thousands of keywords. Only the parsers in this module look at Google's answer; the checks
(tracker/paid_audit_google_checks.py) only look at what the parsers return. Every part is read on its own: a part that cannot be read is
a warning and its `ok` flag is False, so the checks say "unknown" for it. The audit never reports a pass or a failure for something it
could not look at.

Read only: nothing here changes the account.
"""
from __future__ import annotations

import re

from . import paid_gads_segments as gads

WINDOW = "segments.date DURING LAST_30_DAYS"
EXAMPLES = 8                                  # named examples kept per finding


# ── Reading the API's rows ──────────────────────────────────────────────────

def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _i(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _units(micros):
    return round(_f(micros) / 1e6, 2)


def _id_of(resource):
    """The id at the end of a resource name ("customers/1/conversionActions/22" gives "22")."""
    return str(resource or "").rsplit("/", 1)[-1]


# ── The account's own settings ──────────────────────────────────────────────

CUSTOMER_GAQL = (
    "SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone, customer.status, customer.test_account, "
    "customer.manager, customer.auto_tagging_enabled, customer.tracking_url_template, customer.final_url_suffix, "
    "customer.conversion_tracking_setting.conversion_tracking_status, customer.conversion_tracking_setting.enhanced_conversions_for_leads_enabled, "
    "customer.conversion_tracking_setting.accepted_customer_data_terms, customer.conversion_tracking_setting.cross_account_conversion_tracking_id, "
    "customer.call_reporting_setting.call_reporting_enabled, customer.call_reporting_setting.call_conversion_reporting_enabled, "
    "customer.optimization_score, customer.remarketing_setting.google_global_site_tag FROM customer")
_TAG_ID = re.compile(r"\bAW-\d{6,}\b")


def parse_customer(rows):
    """The account record. The Google tag is reduced to its id (AW-...); the snippet itself is not kept. Shares are kept to a tenth of a percent
    so a check decides a threshold on the real figure, not a rounded one."""
    c = (rows[0] if rows else {}).get("customer") or {}
    track = c.get("conversionTrackingSetting") or {}
    calls = c.get("callReportingSetting") or {}
    tag = _TAG_ID.search(_get(c, "remarketingSetting", "googleGlobalSiteTag", default="") or "")
    score = c.get("optimizationScore")
    return {"id": str(c.get("id") or ""), "name": c.get("descriptiveName") or "", "currency": c.get("currencyCode") or "",
            "timeZone": c.get("timeZone") or "", "status": c.get("status") or "UNKNOWN", "test": bool(c.get("testAccount")),
            "manager": bool(c.get("manager")), "autoTagging": bool(c.get("autoTaggingEnabled")),
            "trackingTemplate": c.get("trackingUrlTemplate") or "", "finalUrlSuffix": c.get("finalUrlSuffix") or "",
            "trackingStatus": track.get("conversionTrackingStatus") or "UNKNOWN",
            "crossAccountTrackingId": str(track.get("crossAccountConversionTrackingId") or ""),
            "enhancedLeads": bool(track.get("enhancedConversionsForLeadsEnabled")),
            "dataTerms": bool(track.get("acceptedCustomerDataTerms")),
            "callReporting": bool(calls.get("callReportingEnabled")), "callConversions": bool(calls.get("callConversionReportingEnabled")),
            "optimizationScore": None if score is None else round(_f(score) * 100, 1), "googleTagId": tag.group(0) if tag else ""}


# ── Conversion actions ──────────────────────────────────────────────────────

CONVERSIONS_GAQL = (
    "SELECT conversion_action.id, conversion_action.name, conversion_action.status, conversion_action.type, conversion_action.category, "
    "conversion_action.primary_for_goal, conversion_action.counting_type, conversion_action.attribution_model_settings.attribution_model, "
    "conversion_action.value_settings.default_value, conversion_action.value_settings.always_use_default_value, "
    "conversion_action.click_through_lookback_window_days, conversion_action.view_through_lookback_window_days, conversion_action.origin, "
    "conversion_action.include_in_conversions_metric, conversion_action.owner_customer FROM conversion_action "
    "WHERE conversion_action.status != REMOVED")
CONVERSION_VOLUME_GAQL = (
    "SELECT segments.conversion_action, metrics.conversions, metrics.all_conversions, metrics.conversions_value, metrics.all_conversions_value "
    "FROM customer WHERE " + WINDOW)


def parse_conversions(action_rows, volume_rows=None, own_id=""):
    """Every conversion action that is not removed, with the last 30 days' conversions: [{id, name, status, type, category, primary,
    counting, attribution, defaultValue, alwaysDefault, clickWindow, viewWindow, origin, included, mine, conversions, allConversions,
    value}]. `mine` is False for an action owned by another account (a manager's). `volume_rows` is None when the volumes could not be
    read (then the counts are None, never 0)."""
    volume = {}
    for r in volume_rows or []:
        k = _id_of(_get(r, "segments", "conversionAction"))
        v = volume.setdefault(k, {"conversions": 0.0, "allConversions": 0.0, "value": 0.0})
        v["conversions"] += _f(_get(r, "metrics", "conversions"))
        v["allConversions"] += _f(_get(r, "metrics", "allConversions"))
        v["value"] += _f(_get(r, "metrics", "allConversionsValue", default=_get(r, "metrics", "conversionsValue")))
    out = []
    for r in action_rows:
        a = r.get("conversionAction") or {}
        aid = str(a.get("id") or _id_of(a.get("resourceName")))
        vol = volume.get(aid, {"conversions": 0.0, "allConversions": 0.0, "value": 0.0}) if volume_rows is not None else None
        out.append({"id": aid, "name": a.get("name") or "", "status": a.get("status") or "UNKNOWN", "type": a.get("type") or "",
                    "category": a.get("category") or "", "primary": bool(a.get("primaryForGoal")), "counting": a.get("countingType") or "",
                    "attribution": _get(a, "attributionModelSettings", "attributionModel", default=""),
                    "defaultValue": _f(_get(a, "valueSettings", "defaultValue")), "alwaysDefault": bool(_get(a, "valueSettings", "alwaysUseDefaultValue")),
                    "clickWindow": _i(a.get("clickThroughLookbackWindowDays")), "viewWindow": _i(a.get("viewThroughLookbackWindowDays")),
                    "origin": a.get("origin") or "", "included": bool(a.get("includeInConversionsMetric")),
                    "mine": not own_id or not a.get("ownerCustomer") or _id_of(a.get("ownerCustomer")) == str(own_id),
                    "conversions": None if vol is None else round(vol["conversions"], 2),
                    "allConversions": None if vol is None else round(vol["allConversions"], 2),
                    "value": None if vol is None else round(vol["value"], 2)})
    out.sort(key=lambda x: (x["status"] != "ENABLED", not x["primary"], x["name"].lower()))
    return out


# ── Links and audience lists ────────────────────────────────────────────────
# Google's API exposes the product links (Merchant Center, a manager, a data partner, an advertising partner, Hotel Center) but not a
# Google Analytics 4 link, so a GA4 link can only be inferred from the conversion actions imported from it (the checks say so).

LINKS_GAQL = "SELECT product_link.type, product_link.resource_name FROM product_link"
MERCHANT_GAQL = ("SELECT product_link.type, product_link.merchant_center.merchant_center_id FROM product_link "
                 "WHERE product_link.type = MERCHANT_CENTER")
LISTS_GAQL = (
    "SELECT user_list.id, user_list.name, user_list.type, user_list.membership_status, user_list.size_for_search, user_list.size_for_display, "
    "user_list.eligible_for_search, user_list.eligible_for_display, user_list.closing_reason, user_list.read_only FROM user_list")
SEARCH_LIST_MIN = 1000                        # Google's minimum list size to serve on Search
DISPLAY_LIST_MIN = 100


def parse_links(link_rows, merchant_rows=None):
    """{counts: {link type: how many}, merchantCenter: [merchant ids]}. The ids are None when they could not be read."""
    counts = {}
    for r in link_rows:
        t = _get(r, "productLink", "type", default="UNKNOWN")
        counts[t] = counts.get(t, 0) + 1
    ids = None
    if merchant_rows is not None:
        ids = sorted({str(_get(r, "productLink", "merchantCenter", "merchantCenterId", default="")) for r in merchant_rows} - {""})
    return {"counts": counts, "merchantCenter": ids}


def parse_lists(rows):
    """The account's audience lists, compacted: {total, open, withMembers, byType, tooSmallForSearch, empty, examples}. A list is "open"
    when it is still collecting members; `empty` and `tooSmallForSearch` count open lists only (a closed list is on purpose). Each example
    says whether it is "empty" (no members at all) or "small" (members, but under the Search minimum, which includes a list that only
    serves Display)."""
    lists = [r.get("userList") or {} for r in rows]
    by_type, empty, small, examples = {}, 0, 0, []
    open_lists = [u for u in lists if u.get("membershipStatus") == "OPEN"]
    for u in lists:
        t = u.get("type") or "UNKNOWN"
        by_type[t] = by_type.get(t, 0) + 1
    members = 0
    for u in open_lists:
        search, display = _i(u.get("sizeForSearch")), _i(u.get("sizeForDisplay"))
        if search or display:
            members += 1
        if not search and not display:
            empty += 1
            examples.append({"name": u.get("name") or "", "type": u.get("type") or "", "size": 0, "kind": "empty"})
        elif search < SEARCH_LIST_MIN:
            small += 1
            examples.append({"name": u.get("name") or "", "type": u.get("type") or "", "size": search, "kind": "small"})
    return {"total": len(lists), "open": len(open_lists), "withMembers": members, "byType": by_type, "empty": empty,
            "tooSmallForSearch": small, "examples": examples[:EXAMPLES]}


# ── Campaigns: settings, status, bidding, budget, targeting ─────────────────

CAMPAIGNS_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.status, campaign.primary_status, campaign.primary_status_reasons, campaign.serving_status, "
    "campaign.advertising_channel_type, campaign.advertising_channel_sub_type, campaign.bidding_strategy_type, campaign.bidding_strategy_system_status, "
    "campaign.network_settings.target_google_search, campaign.network_settings.target_search_network, campaign.network_settings.target_content_network, "
    "campaign.geo_target_type_setting.positive_geo_target_type, "
    "campaign.geo_target_type_setting.negative_geo_target_type, campaign_budget.amount_micros, campaign_budget.explicitly_shared, "
    "campaign.target_cpa.target_cpa_micros, campaign.maximize_conversions.target_cpa_micros, campaign.target_roas.target_roas, "
    "campaign.maximize_conversion_value.target_roas, campaign.shopping_setting.merchant_id, campaign.bidding_strategy, "
    "campaign.start_date_time, campaign.end_date_time FROM campaign WHERE campaign.status != REMOVED")
CAMPAIGN_METRICS_GAQL = (
    "SELECT campaign.id, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.all_conversions, "
    "metrics.search_budget_lost_impression_share, metrics.search_rank_lost_impression_share, metrics.search_impression_share "
    "FROM campaign WHERE campaign.status = ENABLED AND " + WINDOW)
CAMPAIGN_CRITERIA_GAQL = (
    "SELECT campaign.id, campaign_criterion.type, campaign_criterion.negative FROM campaign_criterion "
    "WHERE campaign.status = ENABLED AND campaign_criterion.type IN (LOCATION, LANGUAGE, PROXIMITY)")
CAMPAIGN_NEGATIVES_GAQL = (
    "SELECT campaign.id, campaign_criterion.keyword.match_type FROM campaign_criterion "
    "WHERE campaign.status = ENABLED AND campaign_criterion.type = KEYWORD AND campaign_criterion.negative = TRUE")
CAMPAIGN_LISTS_GAQL = (
    "SELECT campaign.id, shared_set.id, shared_set.name, shared_set.member_count FROM campaign_shared_set "
    "WHERE shared_set.type = NEGATIVE_KEYWORDS AND shared_set.status = ENABLED AND campaign.status = ENABLED AND campaign_shared_set.status = ENABLED")
NETWORK_GAQL = (
    "SELECT campaign.id, segments.ad_network_type, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions FROM campaign "
    "WHERE campaign.advertising_channel_type = SEARCH AND campaign.status = ENABLED AND " + WINDOW)


def _bid_target(c):
    """The target a Smart Bidding strategy is set to: (kind, value) with kind "cpa" or "roas", or ("", 0)."""
    cpa = _get(c, "targetCpa", "targetCpaMicros", default=_get(c, "maximizeConversions", "targetCpaMicros"))
    if cpa:
        return "cpa", _units(cpa)
    roas = _get(c, "targetRoas", "targetRoas", default=_get(c, "maximizeConversionValue", "targetRoas"))
    if roas:
        return "roas", round(_f(roas), 2)
    return "", 0


def parse_campaigns(campaign_rows, metric_rows=None, criteria_rows=None, negative_rows=None, list_rows=None):
    """Every enabled campaign with its settings, last-30-day numbers, targeting and negatives, and the counts of the rest:
    {campaigns: [...], counts: {enabled, paused, byType}, lists: {id: {name, members}}, totals: {impressions, clicks, cost, conversions}}.
    A part that was not read (None) leaves its fields None on each campaign rather than 0, so a check can say unknown."""
    metrics = {}
    for r in metric_rows or []:
        cid = str(_get(r, "campaign", "id", default=""))
        m = metrics.setdefault(cid, {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0, "allConversions": 0.0,
                                     "budgetLost": None, "rankLost": None, "share": None})
        x = r.get("metrics") or {}
        m["impressions"] += _i(x.get("impressions"))
        m["clicks"] += _i(x.get("clicks"))
        m["cost"] += _f(x.get("costMicros")) / 1e6
        m["conversions"] += _f(x.get("conversions"))
        m["allConversions"] += _f(x.get("allConversions"))
        for src, dst in (("searchBudgetLostImpressionShare", "budgetLost"), ("searchRankLostImpressionShare", "rankLost"), ("searchImpressionShare", "share")):
            if x.get(src) is not None:
                m[dst] = round(_f(x[src]) * 100, 1)
    crit = {}
    for r in criteria_rows or []:
        cid = str(_get(r, "campaign", "id", default=""))
        c = crit.setdefault(cid, {"locations": 0, "excludedLocations": 0, "proximities": 0, "languages": 0})
        kind, neg = _get(r, "campaignCriterion", "type"), bool(_get(r, "campaignCriterion", "negative"))
        if kind == "LOCATION":
            c["excludedLocations" if neg else "locations"] += 1
        elif kind == "PROXIMITY":
            c["proximities"] += 1
        elif kind == "LANGUAGE":
            c["languages"] += 1
    negatives = {}
    for r in negative_rows or []:
        cid = str(_get(r, "campaign", "id", default=""))
        negatives[cid] = negatives.get(cid, 0) + 1
    attached, lists = {}, {}
    for r in list_rows or []:
        cid, s = str(_get(r, "campaign", "id", default="")), r.get("sharedSet") or {}
        sid = str(s.get("id") or "")
        attached.setdefault(cid, []).append(sid)
        lists[sid] = {"name": s.get("name") or "", "members": _i(s.get("memberCount"))}
    out, counts = [], {"enabled": 0, "paused": 0, "byType": {}}
    totals = {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0}
    for r in campaign_rows:
        c, b = r.get("campaign") or {}, r.get("campaignBudget") or {}
        status = c.get("status") or "UNKNOWN"
        if status == "PAUSED":
            counts["paused"] += 1
        if status != "ENABLED":
            continue
        cid, ctype = str(c.get("id") or ""), c.get("advertisingChannelType") or "UNKNOWN"
        counts["enabled"] += 1
        counts["byType"][ctype] = counts["byType"].get(ctype, 0) + 1
        net, geo = c.get("networkSettings") or {}, c.get("geoTargetTypeSetting") or {}
        target_kind, target = _bid_target(c)
        m = metrics.get(cid, {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0, "allConversions": 0.0,
                              "budgetLost": None, "rankLost": None, "share": None}) if metric_rows is not None else None
        k = crit.get(cid, {"locations": 0, "excludedLocations": 0, "proximities": 0, "languages": 0}) if criteria_rows is not None else None
        if m:
            totals["impressions"] += m["impressions"]
            totals["clicks"] += m["clicks"]
            totals["cost"] += m["cost"]
            totals["conversions"] += m["conversions"]
        out.append({"id": cid, "name": c.get("name") or "", "type": ctype, "subType": c.get("advertisingChannelSubType") or "",
                    "primaryStatus": c.get("primaryStatus") or "", "reasons": list(c.get("primaryStatusReasons") or []),
                    "serving": c.get("servingStatus") or "", "bidding": c.get("biddingStrategyType") or "UNKNOWN",
                    "biddingStatus": c.get("biddingStrategySystemStatus") or "", "portfolio": str(c.get("biddingStrategy") or ""),
                    "targetKind": target_kind, "target": target,
                    "googleSearch": bool(net.get("targetGoogleSearch")), "searchPartners": bool(net.get("targetSearchNetwork")),
                    "displayNetwork": bool(net.get("targetContentNetwork")),
                    "geoMode": geo.get("positiveGeoTargetType") or "", "budget": _units(b.get("amountMicros")), "budgetShared": bool(b.get("explicitlyShared")),
                    "merchantId": str(_get(c, "shoppingSetting", "merchantId", default="") or ""),
                    "metrics": m, "targeting": k,
                    "negatives": None if negative_rows is None else negatives.get(cid, 0),
                    "lists": None if list_rows is None else attached.get(cid, [])})
    totals["cost"] = round(totals["cost"], 2)
    totals["conversions"] = round(totals["conversions"], 2)
    out.sort(key=lambda x: (-(x["metrics"] or {}).get("cost", 0), x["name"].lower()))
    return {"campaigns": out, "counts": counts, "lists": lists, "totals": totals if metric_rows is not None else None}


def parse_network(rows):
    """Search campaigns' spend by where the ad ran: {cost: {network: spend}, total, conversions: {network: n}}."""
    cost, conv = {}, {}
    for r in rows:
        n = _get(r, "segments", "adNetworkType", default="UNKNOWN")
        cost[n] = cost.get(n, 0.0) + _f(_get(r, "metrics", "costMicros")) / 1e6
        conv[n] = conv.get(n, 0.0) + _f(_get(r, "metrics", "conversions"))
    return {"cost": {k: round(v, 2) for k, v in cost.items()}, "total": round(sum(cost.values()), 2),
            "conversions": {k: round(v, 2) for k, v in conv.items()}}


# ── Ad groups, keywords and ads ─────────────────────────────────────────────
# Three separate queries joined in code on the ad group id (a keyword or an ad row does not carry its ad group's name, and a keyword
# that had no impressions in the window is not returned by the metrics query, so the keyword list and its numbers are two reads).

AD_GROUPS_GAQL = (
    "SELECT campaign.id, ad_group.id, ad_group.name, ad_group.type, ad_group.primary_status FROM ad_group "
    "WHERE campaign.status = ENABLED AND ad_group.status = ENABLED")
KEYWORDS_GAQL = (
    "SELECT campaign.id, ad_group.id, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
    "ad_group_criterion.status, ad_group_criterion.approval_status, ad_group_criterion.system_serving_status, "
    "ad_group_criterion.quality_info.quality_score, ad_group_criterion.quality_info.creative_quality_score, "
    "ad_group_criterion.quality_info.post_click_quality_score, ad_group_criterion.quality_info.search_predicted_ctr FROM ad_group_criterion "
    "WHERE ad_group_criterion.type = KEYWORD AND ad_group_criterion.negative = FALSE AND ad_group_criterion.status != REMOVED "
    "AND campaign.status = ENABLED AND ad_group.status = ENABLED")
KEYWORD_METRICS_GAQL = (
    "SELECT ad_group.id, ad_group_criterion.criterion_id, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
    "FROM keyword_view WHERE " + WINDOW + " AND campaign.status = ENABLED AND ad_group.status = ENABLED "
    "AND ad_group_criterion.status != REMOVED AND metrics.impressions > 0")
ADS_GAQL = (
    "SELECT campaign.id, ad_group.id, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status, ad_group_ad.ad_strength, "
    "ad_group_ad.policy_summary.approval_status, ad_group_ad.policy_summary.policy_topic_entries, ad_group_ad.ad.final_urls, "
    "ad_group_ad.ad.responsive_search_ad.headlines FROM ad_group_ad "
    "WHERE campaign.status = ENABLED AND ad_group.status = ENABLED AND ad_group_ad.status != REMOVED")
WASTE_MIN_CLICKS = 15                         # a keyword with this many clicks in 30 days and no conversion has had a fair chance
LEGACY_ADS = {"EXPANDED_TEXT_AD", "TEXT_AD", "CALL_ONLY_AD", "EXPANDED_DYNAMIC_SEARCH_AD", "LEGACY_RESPONSIVE_DISPLAY_AD", "LEGACY_APP_INSTALL_AD"}
SEARCH_AD_GROUP = "SEARCH_STANDARD"
MAX_URLS = 40
MANY_KEYWORDS = 40                            # keywords in one ad group past which its ads cannot speak to each of them
BELOW = "BELOW_AVERAGE"


def parse_ad_groups(rows):
    """{groups: {id: {campaign, name, type}}, total}. Only enabled ad groups of enabled campaigns are read."""
    groups = {}
    for r in rows:
        g, c = r.get("adGroup") or {}, r.get("campaign") or {}
        gid = str(g.get("id") or "")
        if gid:
            groups[gid] = {"campaign": str(c.get("id") or ""), "name": g.get("name") or "", "type": g.get("type") or "UNKNOWN"}
    return {"groups": groups, "total": len(groups)}


def _norm(text):
    return " ".join(str(text or "").lower().split())


def _qs_band(score):
    return "low" if score <= 4 else "mid" if score <= 6 else "high"


def parse_keywords(rows, metric_rows, groups, campaign_names=None):
    """The keyword facts: counts by match type and status, Quality Score weighted by impressions, keywords that spent without ever
    converting, the same keyword in several ad groups of one campaign, disapproved and rarely served keywords. `metric_rows` None means
    the numbers could not be read: the facts that need them are None. `groups` is parse_ad_groups()["groups"]. The result also carries
    `_perGroup` (enabled keywords per ad group) for the structure join; compact() drops it before the read is saved."""
    names = campaign_names or {}
    metrics = None
    if metric_rows is not None:
        metrics = {}
        for r in metric_rows:
            k = (str(_get(r, "adGroup", "id", default="")), str(_get(r, "adGroupCriterion", "criterionId", default="")))
            m = metrics.setdefault(k, {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0})
            x = r.get("metrics") or {}
            m["impressions"] += _i(x.get("impressions"))
            m["clicks"] += _i(x.get("clicks"))
            m["cost"] += _f(x.get("costMicros")) / 1e6
            m["conversions"] += _f(x.get("conversions"))
    by_match, status = {}, {"ENABLED": 0, "PAUSED": 0}
    per_group, per_campaign = {}, {}
    qs = {"scored": 0, "keywords": {"low": 0, "mid": 0, "high": 0}, "impressions": {"low": 0, "mid": 0, "high": 0, "none": 0},
          "below": {"expectedCtr": 0, "adRelevance": 0, "landingPage": 0}}
    low_examples, waste_examples, dup, disapproved, rarely = [], [], {}, [], 0
    waste_cost = waste_n = 0
    scored_impressions = 0
    for r in rows:
        c, g, k = r.get("campaign") or {}, r.get("adGroup") or {}, r.get("adGroupCriterion") or {}
        cid, gid, crit = str(c.get("id") or ""), str(g.get("id") or ""), str(k.get("criterionId") or "")
        kw = k.get("keyword") or {}
        text, match = kw.get("text") or "", kw.get("matchType") or "UNKNOWN"
        st = k.get("status") or "UNKNOWN"
        status[st] = status.get(st, 0) + 1
        if st != "ENABLED":
            continue
        by_match[match] = by_match.get(match, 0) + 1
        per_group[gid] = per_group.get(gid, 0) + 1
        pc = per_campaign.setdefault(cid, {"keywords": 0, "broad": 0})
        pc["keywords"] += 1
        pc["broad"] += match == "BROAD"
        m = metrics.get((gid, crit), {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0}) if metrics is not None else None
        where = {"keyword": text, "match": match, "campaign": names.get(cid, ""), "adGroup": (groups.get(gid) or {}).get("name", "")}
        score = _get(k, "qualityInfo", "qualityScore")
        if score:
            band = _qs_band(_i(score))
            qs["scored"] += 1
            qs["keywords"][band] += 1
            if m is not None:
                qs["impressions"][band] += m["impressions"]
                scored_impressions += m["impressions"]
                for src, dst in (("searchPredictedCtr", "expectedCtr"), ("creativeQualityScore", "adRelevance"), ("postClickQualityScore", "landingPage")):
                    if _get(k, "qualityInfo", src) == BELOW:
                        qs["below"][dst] += m["impressions"]
                if band == "low" and m["impressions"]:
                    low_examples.append(dict(where, qs=_i(score), impressions=m["impressions"]))
        elif m is not None:
            qs["impressions"]["none"] += m["impressions"]
        if m is not None and m["clicks"] >= WASTE_MIN_CLICKS and m["conversions"] == 0:
            waste_cost += m["cost"]
            waste_n += 1
            waste_examples.append(dict(where, clicks=m["clicks"], cost=round(m["cost"], 2)))
        dup.setdefault((cid, _norm(text), match), set()).add(gid)
        if k.get("approvalStatus") == "DISAPPROVED":
            disapproved.append(where)
        if k.get("systemServingStatus") == "RARELY_SERVED":
            rarely += 1
    if metrics is None:
        qs["impressions"] = None
        qs["below"] = None
    else:
        qs["below"] = {k: (round(100.0 * v / scored_impressions, 1) if scored_impressions else 0) for k, v in qs["below"].items()}
    low_examples.sort(key=lambda e: (-e["impressions"], e["keyword"]))
    waste_examples.sort(key=lambda e: (-e["cost"], e["keyword"]))
    dups = sorted(((key, ags) for key, ags in dup.items() if len(ags) > 1), key=lambda kv: (-len(kv[1]), kv[0]))
    return {"total": sum(status.values()), "enabled": status["ENABLED"], "paused": status["PAUSED"], "byMatch": by_match, "qs": qs,
            "lowQualityExamples": low_examples[:EXAMPLES],
            "wasted": None if metrics is None else {"cost": round(waste_cost, 2), "keywords": waste_n, "minClicks": WASTE_MIN_CLICKS,
                                                    "examples": waste_examples[:EXAMPLES]},
            "duplicates": {"keywords": len(dups), "copies": sum(len(ags) - 1 for _k, ags in dups),
                           "examples": [{"keyword": key[1], "match": key[2], "campaign": names.get(key[0], ""), "adGroups": len(ags)} for key, ags in dups[:EXAMPLES]]},
            "disapproved": {"count": len(disapproved), "examples": disapproved[:EXAMPLES]}, "rarelyServed": rarely,
            "perCampaign": per_campaign, "_perGroup": per_group}


def parse_ads(rows, groups, campaign_names=None):
    """The ad facts: counts by type, responsive search ad strength, disapproved and limited ads with the policy topic, legacy formats,
    pinned headlines and the distinct landing pages. `_perGroup` (live ads per ad group) is for the structure join."""
    names = campaign_names or {}
    by_type, strength = {}, {}
    per_group, urls = {}, {}
    disapproved, limited, legacy, pinned_any, pinned_heavy, rsa = [], 0, 0, 0, 0, 0
    for r in rows:
        a, c, g = r.get("adGroupAd") or {}, r.get("campaign") or {}, r.get("adGroup") or {}
        if a.get("status") != "ENABLED":
            continue
        ad, gid, cid = a.get("ad") or {}, str(g.get("id") or ""), str(c.get("id") or "")
        t = ad.get("type") or "UNKNOWN"
        by_type[t] = by_type.get(t, 0) + 1
        pg = per_group.setdefault(gid, {"ads": 0, "rsa": 0})
        pg["ads"] += 1
        if t in LEGACY_ADS:
            legacy += 1
        if t == "RESPONSIVE_SEARCH_AD":
            pg["rsa"] += 1
            rsa += 1
            s = a.get("adStrength") or "UNSPECIFIED"
            strength[s] = strength.get(s, 0) + 1
            pins = sum(1 for h in _get(ad, "responsiveSearchAd", "headlines", default=[]) or [] if h.get("pinnedField"))
            pinned_any += pins > 0
            pinned_heavy += pins >= 3
        approval = _get(a, "policySummary", "approvalStatus", default="")
        if approval == "DISAPPROVED":
            topics = [str(e.get("topic") or "") for e in _get(a, "policySummary", "policyTopicEntries", default=[]) or []][:3]
            disapproved.append({"campaign": names.get(cid, ""), "adGroup": (groups.get(gid) or {}).get("name", ""), "type": t, "topics": [x for x in topics if x]})
        elif approval in ("APPROVED_LIMITED", "AREA_OF_INTEREST_ONLY"):
            limited += 1
        for u in ad.get("finalUrls") or []:
            urls[u] = urls.get(u, 0) + 1
    ranked = sorted(urls.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"total": sum(by_type.values()), "byType": by_type, "rsa": rsa, "strength": strength, "legacy": legacy,
            "disapproved": {"count": len(disapproved), "examples": disapproved[:EXAMPLES]}, "limited": limited,
            "pinned": {"any": pinned_any, "heavy": pinned_heavy},
            "urls": {"distinct": len(urls), "list": [{"url": u, "ads": n} for u, n in ranked[:MAX_URLS]]}, "_perGroup": per_group}


def join_structure(groups, kw, ads, campaign_names=None):
    """Facts about ad groups that need both the keywords and the ads: ad groups with no live ad, with one ad, with no keyword, with too
    many keywords. `kw` or `ads` None (not read) leaves the facts that need it None. Only Search ad groups are judged."""
    names = campaign_names or {}
    search = {gid: g for gid, g in groups.items() if g["type"] == SEARCH_AD_GROUP}

    def pick(pred):
        hits = [dict(adGroup=g["name"], campaign=names.get(g["campaign"], "")) for gid, g in search.items() if pred(gid)]
        hits.sort(key=lambda h: (h["campaign"], h["adGroup"]))
        return {"count": len(hits), "examples": hits[:EXAMPLES]}
    out = {"adGroups": len(groups), "searchAdGroups": len(search), "byType": {}}
    for g in groups.values():
        out["byType"][g["type"]] = out["byType"].get(g["type"], 0) + 1
    if ads is not None:
        pg = ads["_perGroup"]
        out["noLiveAd"] = pick(lambda gid: pg.get(gid, {}).get("rsa", 0) + pg.get(gid, {}).get("ads", 0) == 0)
        out["noResponsiveAd"] = pick(lambda gid: pg.get(gid, {}).get("rsa", 0) == 0)
        out["oneAd"] = pick(lambda gid: pg.get(gid, {}).get("ads", 0) == 1)
        out["manyAds"] = pick(lambda gid: pg.get(gid, {}).get("rsa", 0) > 3)
    if kw is not None:
        pk = kw["_perGroup"]
        out["noKeywords"] = pick(lambda gid: pk.get(gid, 0) == 0)
        out["manyKeywords"] = pick(lambda gid: pk.get(gid, 0) > MANY_KEYWORDS)
    return out



def compact(facts):
    """The facts without the join helpers, ready to be saved."""
    return {k: v for k, v in (facts or {}).items() if not k.startswith("_")} if facts is not None else None


# ── Search terms ────────────────────────────────────────────────────────────

TERMS_LIMIT = 1000
TERMS_GAQL = (
    "SELECT search_term_view.search_term, search_term_view.status, campaign.id, metrics.cost_micros, metrics.clicks, metrics.conversions "
    "FROM search_term_view WHERE " + WINDOW + " AND metrics.cost_micros > 0 ORDER BY metrics.cost_micros DESC LIMIT %d" % TERMS_LIMIT)


def parse_terms(rows, campaign_names=None):
    """The biggest search terms by spend and how much of that spend never converted: {rows, capped, cost, wastedCost, wastedTerms,
    examples}. The read is the TERMS_LIMIT most expensive terms, so `capped` says the cheaper ones are not in it."""
    names = campaign_names or {}
    total = wasted = 0.0
    n_wasted, examples = 0, []
    for r in rows:
        x = r.get("metrics") or {}
        cost = _f(x.get("costMicros")) / 1e6
        total += cost
        if _f(x.get("conversions")) == 0:
            wasted += cost
            n_wasted += 1
            examples.append({"term": _get(r, "searchTermView", "searchTerm", default=""), "cost": round(cost, 2), "clicks": _i(x.get("clicks")),
                             "campaign": names.get(str(_get(r, "campaign", "id", default="")), "")})
    examples.sort(key=lambda e: (-e["cost"], e["term"]))
    return {"rows": len(rows), "capped": len(rows) >= TERMS_LIMIT, "cost": round(total, 2), "wastedCost": round(wasted, 2),
            "wastedTerms": n_wasted, "examples": examples[:EXAMPLES]}


# ── Reading the whole account ───────────────────────────────────────────────

WORKERS = 4
READS = [                                      # (key, what it is, query), in the order they are reported when one fails
    ("customer", "the account settings", CUSTOMER_GAQL),
    ("conversionActions", "the conversion actions", CONVERSIONS_GAQL),
    ("conversionVolume", "the conversions of the last 30 days", CONVERSION_VOLUME_GAQL),
    ("links", "the product links", LINKS_GAQL),
    ("merchant", "the Merchant Center links", MERCHANT_GAQL),
    ("lists", "the audience lists", LISTS_GAQL),
    ("campaigns", "the campaigns", CAMPAIGNS_GAQL),
    ("campaignMetrics", "the campaigns' numbers for the last 30 days", CAMPAIGN_METRICS_GAQL),
    ("criteria", "the campaigns' locations and languages", CAMPAIGN_CRITERIA_GAQL),
    ("campaignNegatives", "the campaigns' negative keywords", CAMPAIGN_NEGATIVES_GAQL),
    ("campaignLists", "the negative keyword lists in use", CAMPAIGN_LISTS_GAQL),
    ("network", "where Search spend ran", NETWORK_GAQL),
    ("adGroups", "the ad groups", AD_GROUPS_GAQL),
    ("keywords", "the keywords", KEYWORDS_GAQL),
    ("keywordMetrics", "the keywords' numbers for the last 30 days", KEYWORD_METRICS_GAQL),
    ("ads", "the ads", ADS_GAQL),
    ("terms", "the search terms", TERMS_GAQL),
]


def read_account(account_id, *, search_fn=None, workers=WORKERS):
    """The compact read of one account: {customer, conversions, links, lists, campaigns, network, structure, keywords, ads, terms, ok,
    warnings}. The account record is the point, so a failure there is raised; every other part is read on its own, a failure is a warning,
    its `ok` flag is False and what depends on it is None (the checks then say unknown instead of guessing). Read only."""
    from concurrent.futures import ThreadPoolExecutor
    search = search_fn or gads.search
    rows, errors = {}, {}

    def fetch(read):
        key, _what, gaql = read
        try:
            return key, search(account_id, gaql), None
        except gads.GadsError as e:
            return key, None, e
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for key, got, err in ex.map(fetch, READS):
            if err is not None:
                errors[key] = err
            else:
                rows[key] = got
    if "customer" in errors:
        raise errors["customer"]
    what = {key: w for key, w, _q in READS}
    warnings = ["%s could not be read (%s)." % (what[k][0].upper() + what[k][1:], e) for k, e in errors.items()]
    ok = {key: key in rows for key, _w, _q in READS}

    def got(key):
        return rows.get(key)
    customer = parse_customer(rows["customer"])
    actions = None if got("conversionActions") is None else parse_conversions(rows["conversionActions"], got("conversionVolume"), own_id=account_id)
    links = None if got("links") is None else parse_links(rows["links"], got("merchant"))
    lists = None if got("lists") is None else parse_lists(rows["lists"])
    camp = None if got("campaigns") is None else parse_campaigns(rows["campaigns"], got("campaignMetrics"), got("criteria"), got("campaignNegatives"), got("campaignLists"))
    names = {c["id"]: c["name"] for c in camp["campaigns"]} if camp else {}
    network = None if got("network") is None else parse_network(rows["network"])
    ag = parse_ad_groups(rows["adGroups"]) if got("adGroups") is not None else None
    groups = ag["groups"] if ag else {}
    kw = None if got("keywords") is None else parse_keywords(rows["keywords"], got("keywordMetrics"), groups, names)
    ads = None if got("ads") is None else parse_ads(rows["ads"], groups, names)
    structure = None if ag is None else join_structure(groups, kw, ads, names)
    terms = None if got("terms") is None else parse_terms(rows["terms"], names)
    return {"customer": customer, "conversions": actions, "links": links, "lists": lists, "campaigns": camp, "network": network,
            "structure": structure, "keywords": compact(kw), "ads": compact(ads), "terms": terms, "ok": ok, "warnings": warnings}
