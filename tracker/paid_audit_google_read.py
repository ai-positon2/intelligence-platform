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
    """The account record. The Google tag is reduced to its id (AW-...); the snippet itself is not kept."""
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
            "optimizationScore": None if score is None else round(_f(score) * 100), "googleTagId": tag.group(0) if tag else ""}


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
    when it is still collecting members; `empty` and `tooSmallForSearch` count open lists only (a closed list is on purpose)."""
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
            examples.append({"name": u.get("name") or "", "type": u.get("type") or "", "size": 0})
        elif search < SEARCH_LIST_MIN:
            small += 1
            examples.append({"name": u.get("name") or "", "type": u.get("type") or "", "size": search})
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
                m[dst] = round(_f(x[src]) * 100)
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
                    "biddingStatus": c.get("biddingStrategySystemStatus") or "", "portfolio": bool(c.get("biddingStrategy")),
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
