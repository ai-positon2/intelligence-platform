"""Paid Marketing Agents: QA Agent, the checks the agent can run itself.

Pick an account and a live campaign and the QC starts on the shared checklist.
For the checks Google Ads can answer, this module reads the campaign and says
what it found, printed on the row ("Final URL suffix: utm_source=google...").

The one rule that matters: **the agent ticks or crosses a box only when the
answer is objective** (Display Network is off, auto-tagging is on). Where the
right answer depends on the media plan (the budget, the locations, the bid
target) it prints what is set and leaves the box to the reviewer. And where it
could only see part of what the check asks (it reads ads and keywords, not
every asset), it does not tick; it says what it saw. A false tick is worse than
an empty box.

Reading is `gather`, rules are `evaluate`, and neither touches a database. The
Google Ads transport is injected as `search(customer_id, gaql)`, so tests run
on fake rows, and the real one (tracker/paid_gads_segments.search) shares the
daily request budget with the other agents.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)


def _today():
    return dt.datetime.now(dt.timezone.utc).date()


ID_RE = re.compile(r"^\d{1,20}$")
MAX_FOUND = 1500

# The Google Ads channel type, as the checklist names it.
CAMPAIGN_TYPES = {
    "SEARCH": "Search", "PERFORMANCE_MAX": "PMax", "SHOPPING": "Shopping", "DISPLAY": "Display",
    "DEMAND_GEN": "Demand Gen", "VIDEO": "Video", "MULTI_CHANNEL": "App",
}


class Finding(dict):
    """result: 'pass', 'fail' or None (printed only); found: what was read."""

    def __init__(self, result, found):
        found = " ".join(str(found).split())
        if len(found) > MAX_FOUND:
            found = found[:MAX_FOUND - 3].rstrip() + "..."
        super().__init__(result=result, found=found)


F = Finding


# ── Which campaigns ─────────────────────────────────────────────────────────

_CAMPAIGNS_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type "
    "FROM campaign WHERE campaign.status = 'ENABLED' ORDER BY campaign.name")


def type_label(channel):
    return CAMPAIGN_TYPES.get(channel or "", "")


def list_campaigns(account_id, search):
    """The live (enabled) campaigns in an account, by name."""
    out = []
    for r in search(account_id, _CAMPAIGNS_GAQL):
        c = r.get("campaign") or {}
        if c.get("id"):
            out.append({"id": str(c["id"]), "name": c.get("name") or ("Campaign " + str(c["id"])),
                        "channel": c.get("advertisingChannelType") or "",
                        "type": type_label(c.get("advertisingChannelType"))})
    return sorted(out, key=lambda c: c["name"].lower())


# ── Reading one campaign ────────────────────────────────────────────────────

def _queries(cid, channel):
    """name -> GAQL. Only what this campaign's type can have is asked for."""
    w = "campaign.id = %s" % cid
    q = {
        "customer": "SELECT customer.descriptive_name, customer.auto_tagging_enabled, customer.tracking_url_template, "
                    "customer.final_url_suffix, customer.currency_code, customer.time_zone, "
                    "customer.conversion_tracking_setting.enhanced_conversions_for_leads_enabled, "
                    "customer.contains_eu_political_advertising FROM customer",
        "billing": "SELECT billing_setup.status, billing_setup.payments_account_info.payments_account_name FROM billing_setup",
        "recs": "SELECT recommendation_subscription.type, recommendation_subscription.status FROM recommendation_subscription",
        "campaign": "SELECT campaign.id, campaign.name, campaign.status, campaign.serving_status, campaign.primary_status, "
                    "campaign.advertising_channel_type, campaign.advertising_channel_sub_type, campaign.start_date_time, "
                    "campaign.end_date_time, campaign.tracking_url_template, campaign.final_url_suffix, "
                    "campaign.url_custom_parameters, campaign.bidding_strategy_type, "
                    "campaign.maximize_conversions.target_cpa_micros, campaign.maximize_conversion_value.target_roas, "
                    "campaign.target_cpa.target_cpa_micros, campaign.target_roas.target_roas, "
                    "campaign.network_settings.target_google_search, campaign.network_settings.target_search_network, "
                    "campaign.network_settings.target_content_network, "
                    "campaign.geo_target_type_setting.positive_geo_target_type, "
                    "campaign.geo_target_type_setting.negative_geo_target_type, "
                    "campaign_budget.amount_micros, campaign_budget.explicitly_shared, "
                    "campaign.asset_automation_settings, campaign.shopping_setting.merchant_id, "
                    "campaign.shopping_setting.feed_label, campaign.shopping_setting.campaign_priority, "
                    "campaign.dynamic_search_ads_setting.domain_name, campaign.ai_max_setting.enable_ai_max, "
                    "campaign.experiment_type, campaign.targeting_setting.target_restrictions "
                    "FROM campaign WHERE %s" % w,
        "criteria": "SELECT campaign_criterion.type, campaign_criterion.negative, "
                    "campaign_criterion.location.geo_target_constant, campaign_criterion.language.language_constant, "
                    "campaign_criterion.ad_schedule.day_of_week, campaign_criterion.ad_schedule.start_hour, "
                    "campaign_criterion.ad_schedule.end_hour, campaign_criterion.keyword.text, "
                    "campaign_criterion.keyword.match_type, campaign_criterion.proximity.radius, "
                    "campaign_criterion.proximity.radius_units, campaign_criterion.bid_modifier, campaign_criterion.device.type, "
                    "campaign_criterion.age_range.type, campaign_criterion.gender.type, campaign_criterion.income_range.type, "
                    "campaign_criterion.parental_status.type, campaign_criterion.user_list.user_list, "
                    "campaign_criterion.user_interest.user_interest_category, campaign_criterion.custom_audience.custom_audience, "
                    "campaign_criterion.placement.url FROM campaign_criterion WHERE %s" % w,
        "shared": "SELECT shared_set.name, shared_set.type, shared_set.member_count, campaign_shared_set.status "
                  "FROM campaign_shared_set WHERE %s" % w,
        "labels": "SELECT label.name FROM campaign_label WHERE %s" % w,
        "conv": "SELECT conversion_action.name, conversion_action.status, conversion_action.primary_for_goal, "
                "conversion_action.counting_type, conversion_action.type, conversion_action.include_in_conversions_metric, "
                "conversion_action.attribution_model_settings.attribution_model "
                "FROM conversion_action WHERE conversion_action.status != 'REMOVED'",
        "camp_conv": "SELECT segments.conversion_action_name, metrics.conversions FROM campaign WHERE %s "
                     "AND segments.date DURING LAST_30_DAYS" % w,
        "days": "SELECT segments.date, metrics.cost_micros, metrics.conversions FROM campaign WHERE %s "
                "AND segments.date DURING LAST_7_DAYS" % w,
        "camp_assets": "SELECT campaign.id, campaign_asset.field_type, campaign_asset.status, asset.type, asset.name, asset.final_urls, "
                       "asset.sitelink_asset.link_text, asset.sitelink_asset.description1, asset.sitelink_asset.description2, "
                       "asset.callout_asset.callout_text, asset.structured_snippet_asset.header, asset.structured_snippet_asset.values, "
                       "asset.call_asset.phone_number, asset.text_asset.text "
                       "FROM campaign_asset WHERE %s AND campaign_asset.status != 'REMOVED'" % w,
        "cust_assets": "SELECT customer_asset.field_type, customer_asset.status, asset.type, asset.final_urls, "
                       "asset.sitelink_asset.link_text, asset.sitelink_asset.description1, asset.callout_asset.callout_text, "
                       "asset.structured_snippet_asset.header, asset.structured_snippet_asset.values, "
                       "asset.call_asset.phone_number, asset.text_asset.text FROM customer_asset "
                       "WHERE customer_asset.status != 'REMOVED'",
        "search_terms": "SELECT search_term_view.search_term, search_term_view.status, metrics.clicks, metrics.impressions, "
                        "metrics.cost_micros, metrics.conversions FROM search_term_view WHERE %s "
                        "AND segments.date DURING LAST_30_DAYS ORDER BY metrics.cost_micros DESC LIMIT 50" % w,
        "changes": "SELECT change_event.change_date_time, change_event.user_email, change_event.change_resource_type, "
                   "change_event.resource_change_operation, change_event.campaign FROM change_event WHERE "
                   "change_event.change_date_time >= '%s' AND change_event.change_date_time <= '%s 23:59:59' "
                   "ORDER BY change_event.change_date_time DESC LIMIT 200" % (
                       (_today() - dt.timedelta(days=28)).isoformat(), _today().isoformat()),
        "shared_all": "SELECT shared_set.name, shared_set.type, shared_set.member_count FROM shared_set "
                      "WHERE shared_set.status = 'ENABLED'",
        "user_access": "SELECT customer_user_access.email_address, customer_user_access.access_role FROM customer_user_access",
        "user_lists": "SELECT user_list.name, user_list.type, user_list.size_for_search, user_list.size_for_display "
                      "FROM user_list WHERE user_list.membership_status = 'OPEN' LIMIT 100",
        "exclusions": "SELECT bidding_data_exclusion.name, bidding_data_exclusion.status, bidding_data_exclusion.start_date_time, "
                      "bidding_data_exclusion.end_date_time FROM bidding_data_exclusion",
        "seasonality": "SELECT bidding_seasonality_adjustment.name, bidding_seasonality_adjustment.status, "
                       "bidding_seasonality_adjustment.start_date_time, bidding_seasonality_adjustment.end_date_time "
                       "FROM bidding_seasonality_adjustment",
    }
    if channel in ("SEARCH", "SHOPPING"):
        q["adgroups"] = ("SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.type, ad_group.ad_rotation_mode, "
                         "ad_group.cpc_bid_micros, ad_group.targeting_setting.target_restrictions "
                         "FROM ad_group WHERE %s AND ad_group.status != 'REMOVED'" % w)
        q["ads"] = ("SELECT ad_group.id, ad_group_ad.status, ad_group_ad.ad.type, ad_group_ad.ad.final_urls, "
                    "ad_group_ad.ad_strength, ad_group_ad.policy_summary.approval_status, "
                    "ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
                    "ad_group_ad.ad.responsive_search_ad.path1, ad_group_ad.ad.responsive_search_ad.path2 "
                    "FROM ad_group_ad WHERE %s AND ad_group_ad.status != 'REMOVED'" % w)
    if channel == "SEARCH":
        q["keywords"] = ("SELECT ad_group.id, ad_group_criterion.status, ad_group_criterion.negative, "
                         "ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
                         "ad_group_criterion.system_serving_status, ad_group_criterion.approval_status "
                         "FROM ad_group_criterion WHERE %s AND ad_group_criterion.type = 'KEYWORD' "
                         "AND ad_group_criterion.status != 'REMOVED'" % w)
    if channel == "PERFORMANCE_MAX":
        q["asset_groups"] = ("SELECT asset_group.id, asset_group.name, asset_group.status, asset_group.final_urls, "
                             "asset_group.ad_strength FROM asset_group WHERE %s AND asset_group.status != 'REMOVED'" % w)
        q["asset_group_assets"] = ("SELECT asset_group.id, asset_group_asset.field_type, asset_group_asset.status, "
                                   "asset.text_asset.text, asset.name FROM asset_group_asset "
                                   "WHERE %s AND asset_group_asset.status != 'REMOVED'" % w)
        q["asset_group_signals"] = ("SELECT asset_group.id, asset_group_signal.audience.audience, "
                                    "asset_group_signal.search_theme.text FROM asset_group_signal WHERE %s" % w)
    return q


def gather(account_id, campaign_id, search, *, geo_fn=None, channel=None, web_fn=None):
    """Everything the rules read, as {name: rows}. A read Google refuses (400)
    is left out and noted in data['_errors'], so one odd account cannot sink the
    run; running out of quota or being signed out raises, because nothing after
    it would work either."""
    if not ID_RE.match(str(campaign_id)):
        raise ValueError("That is not a campaign id")
    queries = _queries(campaign_id, channel)

    def one(name):
        try:
            return name, search(account_id, queries[name]), None
        except Exception as e:                              # noqa: BLE001 (kinds below)
            kind = getattr(e, "kind", "")
            if kind in ("quota", "auth", "not_configured", "permission"):
                raise
            log.warning("qa auto: %s for %s failed: %s", name, account_id, str(e)[:200])
            return name, None, (str(e) or "could not be read")[:200]

    data, errors = {}, {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for name, rows, err in ex.map(one, list(queries)):
            data[name] = rows
            if err:
                errors[name] = err
    data["_errors"] = errors
    data["_channel"] = channel
    data["_pages"] = []
    if web_fn:
        urls = []
        for r in data.get("ads") or []:
            ad = (r.get("adGroupAd") or {})
            if ad.get("status") == "ENABLED":
                urls += (ad.get("ad") or {}).get("finalUrls") or []
        for r in data.get("asset_groups") or []:
            urls += (r.get("assetGroup") or {}).get("finalUrls") or []
        try:
            data["_pages"] = web_fn(urls) if urls else []
        except Exception as e:                              # noqa: BLE001 (the pages are evidence, never a reason to stop)
            log.warning("qa auto: landing pages failed: %s", str(e)[:200])
            errors["pages"] = "landing pages could not be read (%s)" % type(e).__name__
    data["_geo"], data["_lang"] = {}, {}
    crit = data.get("criteria") or []
    geo = [((r.get("campaignCriterion") or {}).get("location") or {}).get("geoTargetConstant") for r in crit]
    geo = [g for g in geo if g]
    if geo and geo_fn:
        try:
            data["_geo"] = geo_fn(geo)
        except Exception as e:                              # noqa: BLE001
            log.warning("qa auto: place names failed: %s", str(e)[:200])
    langs = sorted({re.sub(r"\D", "", ((r.get("campaignCriterion") or {}).get("language") or {}).get("languageConstant") or "")
                    for r in crit if (r.get("campaignCriterion") or {}).get("type") == "LANGUAGE"} - {""})
    if langs:
        try:
            rows = search(account_id, "SELECT language_constant.id, language_constant.name FROM language_constant "
                                      "WHERE language_constant.id IN (%s)" % ", ".join(langs))
            data["_lang"] = {str((r.get("languageConstant") or {}).get("id")): (r.get("languageConstant") or {}).get("name")
                             for r in rows}
        except Exception as e:                              # noqa: BLE001
            log.warning("qa auto: language names failed: %s", str(e)[:200])
    return data


# ── Reading the rows ────────────────────────────────────────────────────────

class Ctx:
    """The gathered rows with the small lookups the rules share."""

    def __init__(self, data):
        self.d = data
        self.errors = data.get("_errors") or {}
        self.customer = self._first("customer", "customer")
        row = (data.get("campaign") or [{}])[0] if data.get("campaign") else {}
        self.camp = row.get("campaign") or {}
        self.budget = row.get("campaignBudget") or {}
        self.channel = data.get("_channel") or self.camp.get("advertisingChannelType") or ""
        self.currency = self.customer.get("currencyCode") or ""

    def _first(self, name, key):
        rows = self.d.get(name) or []
        return (rows[0].get(key) or {}) if rows else {}

    def rows(self, name):
        return self.d.get(name) or []

    def read(self, name):
        """True when the read worked (an empty answer is an answer)."""
        return self.d.get(name) is not None

    def unread(self, *names):
        bad = [n for n in names if not self.read(n)]
        if not bad:
            return None
        return F(None, "Could not read this from Google Ads (%s)." % (self.errors.get(bad[0]) or "no answer"))


def _money(micros, cur=""):
    try:
        return ("%s %s" % (format(int(micros) / 1e6, ",.2f"), cur)).strip()
    except (TypeError, ValueError):
        return ""


def _day(stamp):
    return (stamp or "")[:10]


def _list(items, limit=6):
    items = [str(i) for i in items]
    return ", ".join(items[:limit]) + (" and %d more" % (len(items) - limit) if len(items) > limit else "")


def _ad_group_names(c):
    return {str((r.get("adGroup") or {}).get("id")): (r.get("adGroup") or {}).get("name") or "" for r in c.rows("adgroups")}


def _live_ad_groups(c):
    return [r["adGroup"] for r in c.rows("adgroups") if (r.get("adGroup") or {}).get("status") == "ENABLED"]


def _live_ads(c):
    return [r for r in c.rows("ads") if (r.get("adGroupAd") or {}).get("status") == "ENABLED"
            and ((r.get("adGroup") or {}).get("id"))]


def _rsas(c):
    return [r for r in _live_ads(c) if ((r["adGroupAd"].get("ad") or {}).get("type") == "RESPONSIVE_SEARCH_AD")]


def _asset_counts(c, field_types):
    """(campaign-level, account-level) counts of live assets of these field types."""
    camp = sum(1 for r in c.rows("camp_assets") if (r.get("campaignAsset") or {}).get("fieldType") in field_types
               and (r.get("campaignAsset") or {}).get("status") == "ENABLED")
    acct = sum(1 for r in c.rows("cust_assets") if (r.get("customerAsset") or {}).get("fieldType") in field_types
               and (r.get("customerAsset") or {}).get("status") == "ENABLED")
    return camp, acct


def _asset_line(c, label, field_types):
    camp, acct = _asset_counts(c, field_types)
    return camp, acct, "%s: %d on the campaign, %d on the account." % (label, camp, acct)


# ── The rules ───────────────────────────────────────────────────────────────

RULES = {}


def rule(*ids):
    def deco(fn):
        for i in ids:
            RULES[i] = fn
        return fn
    return deco


@rule("1.2")
def r_billing(c):
    if c.unread("billing"):
        return c.unread("billing")
    rows = c.rows("billing")
    if not rows:
        return F(None, "No billing setup is readable on this account (it may bill through the MCC). Balance and credit are not available from the API.")
    st = [(r.get("billingSetup") or {}).get("status") for r in rows]
    names = _list(sorted({((r.get("billingSetup") or {}).get("paymentsAccountInfo") or {}).get("paymentsAccountName") or "" for r in rows} - {""}), 3)
    text = "Billing setup %s%s. Balance or credit cover for the flight is not available from the API." % (
        _list(sorted(set(st))), (" (payments account %s)" % names) if names else "")
    return F("fail" if "APPROVED" not in st else None, text)


@rule("1.4")
def r_zone(c):
    if not c.customer:
        return c.unread("customer") or F(None, "Not available.")
    return F(None, "Time zone %s, currency %s." % (c.customer.get("timeZone") or "?", c.currency or "?"))


@rule("1.5")
def r_auto_apply(c):
    if c.unread("recs"):
        return c.unread("recs")
    on = [(r.get("recommendationSubscription") or {}) for r in c.rows("recs")]
    on = [s.get("type") for s in on if s.get("status") == "ENABLED"]
    if on:
        return F("fail", "Auto-apply is ON for: %s." % _list(on))
    return F("pass", "No auto-apply recommendation type is enabled.")


@rule("1.6")
def r_auto_tagging(c):
    if not c.customer:
        return c.unread("customer") or F(None, "Not available.")
    on = bool(c.customer.get("autoTaggingEnabled"))
    return F("pass" if on else "fail", "Auto-tagging is %s on the account." % ("ON" if on else "OFF"))


def _tracking_lines(c, campaign_only=False):
    cu, ca = c.customer, c.camp
    parts = []
    if not campaign_only:
        parts.append("Account tracking template: %s." % (cu.get("trackingUrlTemplate") or "none"))
        parts.append("Account final URL suffix: %s." % (cu.get("finalUrlSuffix") or "none"))
    parts.append("Campaign tracking template: %s." % (ca.get("trackingUrlTemplate") or "none"))
    parts.append("Campaign final URL suffix: %s." % (ca.get("finalUrlSuffix") or "none"))
    cp = ca.get("urlCustomParameters") or []
    parts.append("Custom parameters: %s." % (", ".join("{_%s}=%s" % (p.get("key"), p.get("value")) for p in cp) or "none"))
    return " ".join(parts)


@rule("1.7")
def r_tracking_any(c):
    if not c.customer or not c.camp:
        return c.unread("customer", "campaign") or F(None, "Not available.")
    has = bool(c.customer.get("trackingUrlTemplate") or c.customer.get("finalUrlSuffix")
               or c.camp.get("trackingUrlTemplate") or c.camp.get("finalUrlSuffix"))
    return F("pass" if has else "fail", _tracking_lines(c))


@rule("3.16")
def r_tracking_campaign(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    own = bool(c.camp.get("trackingUrlTemplate") or c.camp.get("finalUrlSuffix") or c.camp.get("urlCustomParameters"))
    text = _tracking_lines(c, campaign_only=True)
    if own:
        return F("pass", text + " Use Test in Google Ads to confirm the parameters resolve.")
    inherited = bool(c.customer.get("trackingUrlTemplate") or c.customer.get("finalUrlSuffix"))
    return F(None, "Nothing is set on the campaign itself; %s. %s" % (
        "the account's template applies" if inherited else "there is no account template either", text))


@rule("1.15")
def r_eu_political(c):
    if not c.customer:
        return c.unread("customer") or F(None, "Not available.")
    v = c.customer.get("containsEuPoliticalAdvertising")
    if v in ("CONTAINS_EU_POLITICAL_ADVERTISING", "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"):
        return F("pass", "EU political ads declaration: %s." % ("yes, it contains them" if v.startswith("CONTAINS") else "does not contain them"))
    return F("fail", "The EU political ads declaration has not been made.")


@rule("2.2")
def r_primary(c):
    if c.unread("conv"):
        return c.unread("conv")
    rows = [r["conversionAction"] for r in c.rows("conv") if (r.get("conversionAction") or {}).get("primaryForGoal")
            and r["conversionAction"].get("status") == "ENABLED"]
    names = sorted(r.get("name") or "?" for r in rows)
    return F(None, "%d primary conversion action%s: %s." % (len(names), "" if len(names) == 1 else "s", _list(names, 8) or "none"))


@rule("2.3")
def r_conv_status(c):
    if c.unread("conv"):
        return c.unread("conv")
    prim = [r["conversionAction"] for r in c.rows("conv") if (r.get("conversionAction") or {}).get("primaryForGoal")]
    bad = [a.get("name") for a in prim if a.get("status") != "ENABLED"]
    text = "%d primary action%s, %d enabled." % (len(prim), "" if len(prim) == 1 else "s", len(prim) - len(bad))
    if bad:
        return F("fail", text + " Not enabled: %s." % _list(bad))
    return F(None, text + " Whether each is recording needs the Conversions page.")


@rule("2.6")
def r_counting(c):
    if c.unread("conv"):
        return c.unread("conv")
    cnt = Counter((r["conversionAction"].get("name") or "?", r["conversionAction"].get("countingType") or "?")
                  for r in c.rows("conv") if (r.get("conversionAction") or {}).get("primaryForGoal")
                  and r["conversionAction"].get("status") == "ENABLED")
    if not cnt:
        return F(None, "No primary conversion action.")
    return F(None, "Count setting on primary actions: %s." % _list("%s = %s" % (n, t.replace("_", " ").lower()) for (n, t) in sorted(cnt)))


@rule("2.9")
def r_enhanced(c):
    if not c.customer:
        return c.unread("customer") or F(None, "Not available.")
    on = (c.customer.get("conversionTrackingSetting") or {}).get("enhancedConversionsForLeadsEnabled")
    return F(None, "Enhanced conversions for leads: %s. Enhanced conversions for web is set per action; check its diagnostics." % ("on" if on else "off"))


@rule("2.14")
def r_attribution(c):
    if c.unread("conv"):
        return c.unread("conv")
    m = Counter(((r["conversionAction"].get("attributionModelSettings") or {}).get("attributionModel") or "?").replace("_", " ").lower()
                for r in c.rows("conv") if (r.get("conversionAction") or {}).get("primaryForGoal")
                and r["conversionAction"].get("status") == "ENABLED")
    return F(None, "Attribution on primary actions: %s." % (_list("%s (%d)" % kv for kv in m.items()) or "none"))


@rule("3.1")
def r_type(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    sub = c.camp.get("advertisingChannelSubType")
    return F(None, "Type %s%s. Bidding %s." % (type_label(c.channel) or c.channel or "?",
                                              (", subtype " + sub.replace("_", " ").title()) if sub and sub != "UNSPECIFIED" else "",
                                              (c.camp.get("biddingStrategyType") or "?").replace("_", " ").lower()))


@rule("3.2")
def r_name(c):
    return F(None, "Campaign name: %s." % (c.camp.get("name") or "?")) if c.camp else (c.unread("campaign") or F(None, "Not available."))


@rule("3.3")
def r_status(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    st, serving = c.camp.get("status"), c.camp.get("servingStatus")
    text = "Status %s, serving status %s%s." % (st, serving or "?", (", " + c.camp["primaryStatus"].lower()) if c.camp.get("primaryStatus") else "")
    if st != "ENABLED":
        return F("fail", text)
    return F("pass" if serving == "SERVING" else None, text)


@rule("3.4")
def r_start(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    return F(None, "Start date %s." % (_day(c.camp.get("startDateTime")) or "not set"))


@rule("3.5")
def r_end(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    end = _day(c.camp.get("endDateTime"))
    if not end or end.startswith("2037"):
        return F("pass", "No end date.")
    return F(None, "Ends %s. Confirm that is the plan." % end)


@rule("3.6")
def r_budget(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    amt = c.budget.get("amountMicros")
    shared = c.budget.get("explicitlyShared")
    text = "Daily budget %s%s." % (_money(amt, c.currency) or "not readable", ", a SHARED budget" if shared else ", not shared")
    return F(None, text + " Compare with the plan (monthly budget / 30.4).")


@rule("3.7")
def r_bid(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    k = c.camp
    t = []
    for path, label in ((("maximizeConversions", "targetCpaMicros"), "target CPA"), (("targetCpa", "targetCpaMicros"), "target CPA")):
        v = (k.get(path[0]) or {}).get(path[1])
        if v:
            t.append("%s %s" % (label, _money(v, c.currency)))
    for key in ("maximizeConversionValue", "targetRoas"):
        v = (k.get(key) or {}).get("targetRoas")
        if v:
            t.append("target ROAS %s%%" % round(float(v) * 100))
    return F(None, "Bid strategy %s%s." % ((k.get("biddingStrategyType") or "?").replace("_", " ").lower(), (", " + ", ".join(t)) if t else ", no target set"))


@rule("3.8")
def r_locations(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    inc, exc, prox = [], [], []
    for r in c.rows("criteria"):
        cc = r.get("campaignCriterion") or {}
        if cc.get("type") == "LOCATION":
            g = (cc.get("location") or {}).get("geoTargetConstant") or ""
            nm = (c.d.get("_geo") or {}).get(g) or {}
            label = nm.get("canonical") or nm.get("name") or g.replace("geoTargetConstants/", "place ")
            (exc if cc.get("negative") else inc).append(label)
        elif cc.get("type") == "PROXIMITY":
            p = cc.get("proximity") or {}
            prox.append("%s %s radius" % (p.get("radius"), (p.get("radiusUnits") or "").lower()))
    text = "%d location%s targeted: %s." % (len(inc), "" if len(inc) == 1 else "s",
                                            _list(inc, 8) or "none set (the campaign has no location targeting)")
    if prox:
        text += " Radius: %s." % _list(prox)
    text += " Excluded: %s." % (_list(exc, 8) or "none")
    return F(None, text)


@rule("3.9")
def r_presence(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    v = (c.camp.get("geoTargetTypeSetting") or {}).get("positiveGeoTargetType")
    if not v:
        return F(None, "Location option not returned.")
    return F("pass" if v == "PRESENCE" else "fail", "Target option: %s." % ("Presence" if v == "PRESENCE" else "Presence or interest"))


@rule("3.10")
def r_presence_excl(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    v = (c.camp.get("geoTargetTypeSetting") or {}).get("negativeGeoTargetType")
    if not v:
        return F(None, "Exclusion option not returned.")
    return F("pass" if v == "PRESENCE" else "fail", "Exclude option: %s." % ("Presence" if v == "PRESENCE" else "Presence or interest"))


@rule("3.11")
def r_languages(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    ids = [re.sub(r"\D", "", ((r["campaignCriterion"].get("language") or {}).get("languageConstant")) or "")
           for r in c.rows("criteria") if (r.get("campaignCriterion") or {}).get("type") == "LANGUAGE"]
    if not ids:
        return F(None, "No language is set, so the campaign targets all languages.")
    return F(None, "Languages: %s." % _list((c.d.get("_lang") or {}).get(i) or "language " + i for i in ids))


@rule("3.12")
def r_schedule(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    sl = []
    for r in c.rows("criteria"):
        cc = r.get("campaignCriterion") or {}
        if cc.get("type") == "AD_SCHEDULE":
            a = cc.get("adSchedule") or {}
            sl.append("%s %s-%s" % ((a.get("dayOfWeek") or "?")[:3].title(), a.get("startHour", 0), a.get("endHour", 24)))
    return F(None, "Ad schedule: %s." % (_list(sl, 8) if sl else "none (runs at all hours)"))


@rule("3.18")
def r_labels(c):
    if c.unread("labels"):
        return c.unread("labels")
    names = [(r.get("label") or {}).get("name") for r in c.rows("labels")]
    return F(None, "Labels: %s." % (_list(names) or "none"))


@rule("4.1")
def r_display_off(c):
    n = c.camp.get("networkSettings") if c.camp else None
    if n is None:
        return c.unread("campaign") or F(None, "Network settings not returned.")
    on = bool(n.get("targetContentNetwork"))
    return F("fail" if on else "pass", "Google Display Network is %s." % ("ON" if on else "OFF"))


@rule("4.2")
def r_partners_off(c):
    n = c.camp.get("networkSettings") if c.camp else None
    if n is None:
        return c.unread("campaign") or F(None, "Network settings not returned.")
    on = bool(n.get("targetSearchNetwork"))
    return F("fail" if on else "pass", "Search partners are %s." % ("ON" if on else "OFF"))


@rule("4.3")
def r_ai_max(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    on = bool((c.camp.get("aiMaxSetting") or {}).get("enableAiMax"))
    return F("fail" if on else "pass", "AI Max for Search is %s." % ("ON" if on else "OFF"))


def _automation(c, wanted=None):
    """{type: status} of the campaign's asset automation settings."""
    s = {a.get("assetAutomationType"): a.get("assetAutomationStatus") for a in (c.camp.get("assetAutomationSettings") or [])}
    return {k: v for k, v in s.items() if wanted is None or k in wanted}


_AUTOMATION_NAMES = {
    "TEXT_ASSET_AUTOMATION": "text customization", "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION": "final URL expansion",
    "GENERATE_IMAGE_EXTRACTION": "image extraction", "GENERATE_IMAGE_ENHANCEMENT": "image enhancement",
    "GENERATE_ENHANCED_YOUTUBE_VIDEOS": "video enhancement",
}


def _pretty(t):
    return _AUTOMATION_NAMES.get(t) or (t or "").replace("_", " ").lower()


@rule("4.4", "8.2")
def r_asset_automation(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    s = _automation(c)
    if not s:
        return F(None, "No asset automation setting was returned.")
    on = [k for k, v in s.items() if v == "OPTED_IN"]
    text = "Automatically created assets: %s." % _list("%s %s" % (_pretty(k), "ON" if v == "OPTED_IN" else "off") for k, v in sorted(s.items()))
    return F("fail" if on else "pass", text)


@rule("8.1")
def r_url_expansion(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    v = _automation(c).get("FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION")
    if not v:
        return F(None, "Final URL expansion setting was not returned.")
    return F("fail" if v == "OPTED_IN" else "pass",
             "Final URL expansion is %s.%s" % ("ON" if v == "OPTED_IN" else "OFF", " If approved, check the URL exclusions." if v == "OPTED_IN" else ""))


@rule("4.6")
def r_rotation(c):
    if c.unread("adgroups"):
        return c.unread("adgroups")
    modes = Counter(((r.get("adGroup") or {}).get("adRotationMode") or "OPTIMIZE") for r in c.rows("adgroups")
                    if (r.get("adGroup") or {}).get("status") == "ENABLED")
    if not modes:
        return F(None, "No enabled ad group.")
    bad = modes.get("ROTATE_FOREVER", 0)
    return F("fail" if bad else "pass", "Ad rotation: %s." % _list("%s on %d ad group%s" % (m.replace("_", " ").lower(), n, "" if n == 1 else "s") for m, n in modes.items()))


@rule("4.7")
def r_negatives(c):
    if c.unread("shared", "criteria"):
        return c.unread("shared", "criteria")
    lists = [r for r in c.rows("shared") if (r.get("sharedSet") or {}).get("type") == "NEGATIVE_KEYWORDS"
             and (r.get("campaignSharedSet") or {}).get("status") == "ENABLED"]
    own = [1 for r in c.rows("criteria") if (r.get("campaignCriterion") or {}).get("type") == "KEYWORD"
           and (r.get("campaignCriterion") or {}).get("negative")]
    names = ["%s (%s)" % ((r["sharedSet"].get("name") or "list"), r["sharedSet"].get("memberCount", "?")) for r in lists]
    text = "Negative keyword lists attached: %s. Campaign negatives: %d." % (_list(names) or "none", len(own))
    return F("pass" if (lists or own) else "fail", text)


@rule("8.4")
def r_negatives_pmax(c):
    return r_negatives(c)


@rule("5.1")
def r_ads_in_groups(c):
    if c.unread("adgroups", "ads"):
        return c.unread("adgroups", "ads")
    groups = _live_ad_groups(c)
    if not groups:
        return F("fail", "No enabled ad group in the campaign.")
    have = Counter(str((r.get("adGroup") or {}).get("id")) for r in _live_ads(c))
    empty = [g.get("name") for g in groups if not have.get(str(g.get("id")))]
    text = "%d enabled ad group%s; ads per group: %s." % (len(groups), "" if len(groups) == 1 else "s",
                                                           _list("%s %d" % (g.get("name"), have.get(str(g.get("id")), 0)) for g in groups))
    return F("fail" if empty else "pass", ("No enabled ad in: %s. " % _list(empty) if empty else "") + text)


@rule("5.2")
def r_kw_in_groups(c):
    if c.unread("adgroups", "keywords"):
        return c.unread("adgroups", "keywords")
    groups = [g for g in _live_ad_groups(c) if g.get("type") != "SEARCH_DYNAMIC_ADS"]
    if not groups:
        return F(None, "No keyword ad group to check (dynamic search ad groups need none).")
    live = Counter(str((r.get("adGroup") or {}).get("id")) for r in c.rows("keywords")
                   if not (r.get("adGroupCriterion") or {}).get("negative")
                   and (r.get("adGroupCriterion") or {}).get("status") == "ENABLED")
    empty = [g.get("name") for g in groups if not live.get(str(g.get("id")))]
    text = "Enabled keywords per ad group: %s." % _list("%s %d" % (g.get("name"), live.get(str(g.get("id")), 0)) for g in groups)
    return F("fail" if empty else "pass", ("No enabled keyword in: %s. " % _list(empty) if empty else "") + text)


@rule("5.4")
def r_match_types(c):
    if c.unread("keywords"):
        return c.unread("keywords")
    m = Counter((r["adGroupCriterion"].get("keyword") or {}).get("matchType") or "?" for r in c.rows("keywords")
                if not (r.get("adGroupCriterion") or {}).get("negative") and r["adGroupCriterion"].get("status") == "ENABLED")
    return F(None, "Match types: %s." % (_list("%s %d" % (k.lower(), v) for k, v in sorted(m.items())) or "no keywords"))


@rule("5.5")
def r_duplicates(c):
    if c.unread("keywords"):
        return c.unread("keywords")
    seen = defaultdict(set)
    for r in c.rows("keywords"):
        k = r.get("adGroupCriterion") or {}
        if k.get("negative") or k.get("status") != "ENABLED":
            continue
        kw = k.get("keyword") or {}
        seen[((kw.get("text") or "").lower().strip(), kw.get("matchType"))].add(str((r.get("adGroup") or {}).get("id")))
    dup = sorted("%s [%s]" % (t, m.lower()) for (t, m), g in seen.items() if len(g) > 1)
    if dup:
        return F("fail", "The same keyword and match type is in more than one ad group: %s." % _list(dup))
    return F(None, "No keyword is repeated across the campaign's ad groups. Spelling and clashes with negatives are still for you.")


@rule("5.6")
def r_default_bids(c):
    if c.unread("adgroups"):
        return c.unread("adgroups")
    if (c.camp.get("biddingStrategyType") or "") not in ("MANUAL_CPC", "ENHANCED_CPC"):
        return F(None, "Bidding is automated (%s), so ad group bids are not used." % (c.camp.get("biddingStrategyType") or "?").replace("_", " ").lower())
    return F(None, "Ad group default bids: %s." % _list("%s %s" % (g.get("name"), _money(g.get("cpcBidMicros"), c.currency) or "unset") for g in _live_ad_groups(c)))


def _rsa_counts(r):
    rsa = (r["adGroupAd"].get("ad") or {}).get("responsiveSearchAd") or {}
    return rsa, len(rsa.get("headlines") or []), len(rsa.get("descriptions") or [])


@rule("6.1")
def r_rsa_counts(c):
    if c.unread("ads"):
        return c.unread("ads")
    rsas = _rsas(c)
    if not rsas:
        return F("fail" if c.read("ads") else None, "No enabled responsive search ad.")
    names = _ad_group_names(c)
    bad, parts, weak = [], [], []
    for r in rsas:
        rsa, h, d = _rsa_counts(r)
        g = names.get(str((r.get("adGroup") or {}).get("id")), "?")
        strength = (r["adGroupAd"].get("adStrength") or "?").replace("_", " ").lower()
        parts.append("%s: %d headlines, %d descriptions, strength %s" % (g, h, d, strength))
        if h < 3 or d < 2:
            bad.append(g)
        if strength in ("poor", "average", "pending", "unspecified", "unknown", "?"):
            weak.append(g)
    text = _list(parts, 5) + "."
    if bad:
        return F("fail", "Below the minimum (3 headlines, 2 descriptions) in %s. %s" % (_list(bad), text))
    return F("pass" if not weak else None, text + (" Ad strength is below Good in %s." % _list(weak) if weak else ""))


@rule("6.3")
def r_final_urls(c):
    if c.unread("ads"):
        return c.unread("ads")
    urls = sorted({u for r in _live_ads(c) for u in ((r["adGroupAd"].get("ad") or {}).get("finalUrls") or [])})
    if not urls:
        return F(None, "No final URL on the enabled ads.")
    http = [u for u in urls if not u.lower().startswith("https://")]
    text = "%d final URL%s: %s." % (len(urls), "" if len(urls) == 1 else "s", _list(urls, 4))
    if http:
        return F("fail", "Not HTTPS: %s. %s" % (_list(http), text))
    pages = c.d.get("_pages") or []
    broken = ["%s (%s)" % (p["url"], p.get("status") or p.get("error", "unreadable")[:60]) for p in pages
              if p.get("error") or (p.get("status") or 0) >= 400]
    if broken:
        return F("fail", "These pages do not load: %s. %s" % (_list(broken, 4), text))
    if pages:
        return F(None, text + " All HTTPS and %d of them loaded." % len(pages))
    return F(None, text + " All HTTPS. The pages could not be opened to confirm they load.")


@rule("6.5")
def r_paths(c):
    if c.unread("ads"):
        return c.unread("ads")
    rsas = _rsas(c)
    if not rsas:
        return F(None, "No enabled responsive search ad.")
    names = _ad_group_names(c)
    miss = [names.get(str((r.get("adGroup") or {}).get("id")), "?") for r in rsas
            if not ((_rsa_counts(r)[0].get("path1")) and (_rsa_counts(r)[0].get("path2")))]
    if miss:
        return F("fail", "Display path 1 or 2 is empty in: %s." % _list(sorted(set(miss))))
    return F("pass", "Path 1 and path 2 are filled on all %d enabled ads." % len(rsas))


@rule("6.9")
def r_pins(c):
    if c.unread("ads"):
        return c.unread("ads")
    rsas = _rsas(c)
    if not rsas:
        return F(None, "No enabled responsive search ad.")
    pinned = 0
    for r in rsas:
        rsa = _rsa_counts(r)[0]
        pinned += sum(1 for a in (rsa.get("headlines") or []) + (rsa.get("descriptions") or []) if a.get("pinnedField"))
    return F(None, "%d pinned asset%s across %d enabled ads." % (pinned, "" if pinned == 1 else "s", len(rsas)))


def _asset_rule(label, types, minimum, ideal=None):
    def fn(c):
        if c.unread("camp_assets", "cust_assets"):
            return c.unread("camp_assets", "cust_assets")
        camp, acct, text = _asset_line(c, label, types)
        eff = camp or acct                                  # campaign assets override the account's
        if eff >= minimum:
            return F("pass", text + (" Minimum is %d." % minimum))
        return F("fail", text + " Minimum is %d." % minimum)
    return fn


RULES["7.1"] = _asset_rule("Sitelinks", {"SITELINK"}, 4)
RULES["7.2"] = _asset_rule("Callouts", {"CALLOUT"}, 4)
RULES["7.3"] = _asset_rule("Structured snippets", {"STRUCTURED_SNIPPET"}, 1)


def _present_rule(label, types):
    def fn(c):
        if c.unread("camp_assets", "cust_assets"):
            return c.unread("camp_assets", "cust_assets")
        camp, acct, text = _asset_line(c, label, types)
        return F("pass" if (camp or acct) else None, text)
    return fn


RULES["7.5"] = _present_rule("Image assets", {"AD_IMAGE", "MARKETING_IMAGE"})
RULES["7.6"] = _present_rule("Call asset", {"CALL"})
RULES["7.7"] = _present_rule("Location assets", {"LOCATION", "AFFILIATE_LOCATION"})
RULES["7.8"] = _present_rule("Price and promotion assets", {"PRICE", "PROMOTION"})
RULES["7.9"] = _present_rule("Lead form assets", {"LEAD_FORM"})


@rule("7.4", "8.12")
def r_business(c):
    if c.unread("camp_assets", "cust_assets"):
        return c.unread("camp_assets", "cust_assets")
    n = _asset_counts(c, {"BUSINESS_NAME"})
    lg = _asset_counts(c, {"LOGO", "BUSINESS_LOGO", "LANDSCAPE_LOGO"})
    text = "Business name: %s. Logo: %s." % ("set" if any(n) else "not set", "set" if any(lg) else "not set")
    return F("pass" if (any(n) and any(lg)) else None, text)


@rule("7.10")
def r_inherit(c):
    if c.unread("cust_assets"):
        return c.unread("cust_assets")
    m = Counter((r.get("customerAsset") or {}).get("fieldType") or "?" for r in c.rows("cust_assets")
                if (r.get("customerAsset") or {}).get("status") == "ENABLED")
    return F(None, "Account-level assets this campaign can inherit: %s. Confirm none belong to another brand or geo." %
             (_list("%s %d" % (k.replace("_", " ").lower(), v) for k, v in sorted(m.items())) or "none"))


@rule("8.5")
def r_merchant(c):
    if not c.camp:
        return c.unread("campaign") or F(None, "Not available.")
    s = c.camp.get("shoppingSetting") or {}
    if s.get("merchantId"):
        return F("pass", "Merchant Center ID %s%s." % (s["merchantId"], (", feed label " + s["feedLabel"]) if s.get("feedLabel") else ""))
    return F("fail", "No Merchant Center account is linked to this campaign.")


@rule("9.6")
def r_priority(c):
    s = (c.camp.get("shoppingSetting") or {}) if c.camp else {}
    return F(None, "Campaign priority: %s." % (str(s.get("campaignPriority", "not returned")))) if s else F(None, "Not a shopping campaign.")


def _group_names(c):
    return {str((r.get("assetGroup") or {}).get("id")): (r.get("assetGroup") or {}).get("name") or "?" for r in c.rows("asset_groups")}


def _group_counts(c):
    """Live asset counts per asset group. Rows of a removed group can linger in
    Google's answer, so only groups the campaign still has are counted."""
    live = _group_names(c)
    n = defaultdict(Counter)
    for r in c.rows("asset_group_assets"):
        gid = str((r.get("assetGroup") or {}).get("id"))
        if gid in live and (r.get("assetGroupAsset") or {}).get("status") == "ENABLED":
            n[gid][r["assetGroupAsset"].get("fieldType")] += 1
    return n


@rule("8.7")
def r_group_minimums(c):
    if c.unread("asset_groups", "asset_group_assets"):
        return c.unread("asset_groups", "asset_group_assets")
    names, counts = _group_names(c), _group_counts(c)
    if not names:
        return F("fail", "The campaign has no asset group.")
    need = (("HEADLINE", 3, "headlines"), ("LONG_HEADLINE", 1, "long headlines"), ("DESCRIPTION", 2, "descriptions"),
            ("MARKETING_IMAGE", 1, "landscape images"), ("SQUARE_MARKETING_IMAGE", 1, "square images"))
    short, parts = [], []
    for gid, gname in names.items():
        k = counts.get(gid, Counter())
        miss = ["%s %d of %d" % (lbl, k.get(ft, 0), mn) for ft, mn, lbl in need if k.get(ft, 0) < mn]
        parts.append("%s: %s" % (gname, ", ".join("%d %s" % (k.get(ft, 0), lbl) for ft, mn, lbl in need)))
        if miss:
            short.append("%s (%s)" % (gname, ", ".join(miss)))
    logos = _asset_counts(c, {"LOGO", "BUSINESS_LOGO", "LANDSCAPE_LOGO"})
    text = "; ".join(parts)
    if short:
        return F("fail", "Below the minimum: %s. Counts, %s." % (_list(short, 4), text))
    return F(None if not any(logos) else "pass", text + (". Logo set." if any(logos) else ". No logo found; add one."))


@rule("8.8")
def r_videos(c):
    if c.unread("asset_groups", "asset_group_assets"):
        return c.unread("asset_groups", "asset_group_assets")
    names, counts = _group_names(c), _group_counts(c)
    if not names:
        return F(None, "The campaign has no asset group.")
    per = {g: counts.get(g, Counter()).get("YOUTUBE_VIDEO", 0) for g in names}
    text = "Videos per asset group: %s." % _list("%s %d" % (names[g], n) for g, n in per.items())
    return F("pass" if all(n >= 5 for n in per.values()) else "fail", text + " Aim for at least 5 so Google does not make its own.")


@rule("8.9")
def r_signals(c):
    if c.unread("asset_groups", "asset_group_signals"):
        return c.unread("asset_groups", "asset_group_signals")
    names = _group_names(c)
    if not names:
        return F(None, "The campaign has no asset group.")
    aud = Counter(str((r.get("assetGroup") or {}).get("id")) for r in c.rows("asset_group_signals")
                  if ((r.get("assetGroupSignal") or {}).get("audience") or {}).get("audience")
                  and str((r.get("assetGroup") or {}).get("id")) in names)
    text = "Audience signals per asset group: %s." % _list("%s %d" % (n, aud.get(g, 0)) for g, n in names.items())
    return F("pass" if all(aud.get(g) for g in names) else "fail", text)


@rule("8.10")
def r_themes(c):
    if c.unread("asset_groups", "asset_group_signals"):
        return c.unread("asset_groups", "asset_group_signals")
    names = _group_names(c)
    by = defaultdict(list)
    for r in c.rows("asset_group_signals"):
        t = ((r.get("assetGroupSignal") or {}).get("searchTheme") or {}).get("text")
        if t and str((r.get("assetGroup") or {}).get("id")) in names:
            by[str((r.get("assetGroup") or {}).get("id"))].append(t)
    return F(None, "Search themes: %s." % (_list("%s: %s" % (names.get(g, "?"), _list(t, 3)) for g, t in by.items()) or "none"))


@rule("8.11")
def r_group_urls(c):
    if c.unread("asset_groups"):
        return c.unread("asset_groups")
    rows = [r.get("assetGroup") or {} for r in c.rows("asset_groups")]
    return F(None, "Asset groups: %s." % (_list("%s -> %s" % (g.get("name"), _list(g.get("finalUrls") or ["no URL"], 1)) for g in rows) or "none"))


@rule("15.2")
def r_disapprovals(c):
    if c.unread("ads"):
        return c.unread("ads")
    bad_ads = [r for r in c.rows("ads") if ((r.get("adGroupAd") or {}).get("policySummary") or {}).get("approvalStatus") in ("DISAPPROVED", "AREA_OF_INTEREST_ONLY")]
    bad_kw = [r for r in c.rows("keywords") if (r.get("adGroupCriterion") or {}).get("approvalStatus") == "DISAPPROVED"]
    seen = "%d ads and %d keywords" % (len(c.rows("ads")), len(c.rows("keywords")))
    if bad_ads or bad_kw:
        return F("fail", "%d ad%s and %d keyword%s disapproved or limited, of %s read." % (
            len(bad_ads), "" if len(bad_ads) == 1 else "s", len(bad_kw), "" if len(bad_kw) == 1 else "s", seen))
    return F(None, "None of the %s read is disapproved. Assets are not read here; check them in Google Ads." % seen)


@rule("15.3")
def r_live_conversions(c):
    if c.unread("camp_conv"):
        return c.unread("camp_conv")
    per = Counter()
    for r in c.rows("camp_conv"):
        per[(r.get("segments") or {}).get("conversionActionName") or "?"] += float((r.get("metrics") or {}).get("conversions") or 0)
    total = sum(per.values())
    if total > 0:
        return F("pass", "%s conversions in the last 30 days: %s." % (format(total, ",.1f").rstrip("0").rstrip("."), _list("%s %s" % (k, round(v, 1)) for k, v in per.most_common(4))))
    return F(None, "No conversions recorded for this campaign in the last 30 days.")


@rule("15.4")
def r_pacing(c):
    if c.unread("days"):
        return c.unread("days")
    days = sorted(c.rows("days"), key=lambda r: (r.get("segments") or {}).get("date") or "")
    if not days:
        return F(None, "No spend in the last 7 days.")
    last = days[-1]
    spent = int((last.get("metrics") or {}).get("costMicros") or 0)
    bud = int(c.budget.get("amountMicros") or 0)
    pct = " (%d%% of the daily budget)" % round(100.0 * spent / bud) if bud else ""
    return F(None, "On %s it spent %s%s; daily budget %s." % ((last.get("segments") or {}).get("date"), _money(spent, c.currency), pct, _money(bud, c.currency) or "unknown"))


@rule("15.7")
def r_kw_serving(c):
    if c.unread("keywords"):
        return c.unread("keywords")
    low = [(r["adGroupCriterion"].get("keyword") or {}).get("text") for r in c.rows("keywords")
           if (r.get("adGroupCriterion") or {}).get("systemServingStatus") == "RARELY_SERVED"
           and r["adGroupCriterion"].get("status") == "ENABLED" and not r["adGroupCriterion"].get("negative")]
    if low:
        return F("fail", "%d keyword%s rarely shown (low search volume): %s." % (len(low), "" if len(low) == 1 else "s", _list(low)))
    return F("pass", "No enabled keyword is marked rarely shown.")


def _pages(c):
    return c.d.get("_pages") or []


@rule("13.1")
def r_page_live(c):
    pages = _pages(c)
    if not pages:
        return c.unread("ads") or F(None, "No landing page was available to open.")
    bad, notes = [], []
    for p in pages:
        if p.get("error"):
            bad.append("%s could not be opened (%s)" % (p["url"], p["error"][:80]))
            continue
        if p.get("status", 0) >= 400:
            bad.append("%s answered %s" % (p["url"], p["status"]))
        if p.get("stagingHost"):
            bad.append("%s looks like a staging address" % p["url"])
        if (p.get("robots") or {}).get("adsbotAllowed") is False:
            bad.append("robots.txt blocks AdsBot on %s" % p["url"])
        notes.append("%s: %s, %d ms%s" % (p["url"], p.get("status"), p.get("loadMs", 0),
                                          ", marked noindex (fine for ads, it only keeps the page out of organic search)" if p.get("noindex") else ""))
    if bad:
        return F("fail", _list(bad, 4) + ".")
    return F("pass", "Open and not blocked to AdsBot: " + _list(notes, 4) + ".")


@rule("13.2")
def r_params_survive(c):
    pages = [p for p in _pages(c) if "paramsSurvive" in p]
    if not pages:
        return F(None, "No landing page could be tested for parameters.")
    lost = [p["url"] for p in pages if p["paramsSurvive"] is False]
    ok = [p["url"] for p in pages if p["paramsSurvive"]]
    if lost:
        return F("fail", "gclid or utm_source did not reach the final page after the redirect on: %s." % _list(lost, 4))
    if ok and len(ok) == len(pages):
        return F("pass", "A test gclid and UTM parameters were still on the final URL for: %s." % _list(ok, 4))
    return F(None, "Parameters could not be tested on every page.")


# ── Run ─────────────────────────────────────────────────────────────────────

def evaluate(data):
    """{check_id: {'result', 'found'}} for every rule that has something to say."""
    c = Ctx(data)
    out = {}
    for cid, fn in RULES.items():
        try:
            f = fn(c)
        except Exception as e:                              # noqa: BLE001 (one rule never sinks the rest)
            log.exception("qa auto: rule %s failed", cid)
            f = F(None, "This check could not be read automatically (%s)." % type(e).__name__)
        if f:
            out[cid] = f
    return out


def campaign_summary(data):
    """The campaign's own details, for the page's header."""
    c = Ctx(data)
    return {"id": str(c.camp.get("id") or ""), "name": c.camp.get("name") or "", "channel": c.channel,
            "type": type_label(c.channel), "dsa": bool((c.camp.get("dynamicSearchAdsSetting") or {}).get("domainName")),
            "status": c.camp.get("status") or ""}


def run(account_id, campaign_id, search, *, geo_fn=None, web_fn=None):
    """Read one campaign and run every rule on it. One small read first says
    whether the campaign is in this account and what type it is, so only the
    reads that type can answer are made."""
    if not ID_RE.match(str(campaign_id)):
        raise ValueError("That is not a campaign id")
    rows = search(account_id, "SELECT campaign.id, campaign.advertising_channel_type FROM campaign "
                              "WHERE campaign.id = %s" % campaign_id)
    if not rows:
        return {"findings": {}, "campaign": {"id": "", "name": "", "channel": "", "type": "", "dsa": False, "status": ""},
                "errors": {}}
    channel = (rows[0].get("campaign") or {}).get("advertisingChannelType") or ""
    data = gather(account_id, campaign_id, search, geo_fn=geo_fn, channel=channel, web_fn=web_fn)
    return {"data": data, "findings": evaluate(data), "campaign": campaign_summary(data), "errors": data.get("_errors") or {}}
