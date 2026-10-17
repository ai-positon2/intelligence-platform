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
