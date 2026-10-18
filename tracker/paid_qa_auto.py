"""Paid Marketing Agents: QA Agent, what Google Ads can answer exactly.

The PPC team's Campaign Book checks are about the campaign's own settings: its name,
networks, AI Max, goals, bid strategy, devices, demographics, locations, language, negative
keywords, the automation switches and the auto-apply recommendations. This reads the
campaign from Google Ads and runs a rule for every check whose answer Google Ads gives
exactly ("AI Max is off", "Display Network is on", "no end date").

The one rule that matters: **a rule ticks or crosses a box only when the answer is exact.**
Where the right answer depends on the campaign book (the bid strategy, devices, genders,
locations, age, ad schedule) it prints what is set, and compares it with the book only when
the book names an unambiguous value. Where Google Ads does not show a setting to the API
(the account's "automatically created assets" switches, auto migration), it says so and
never ticks; the AI reviewer and then a person take it from there (see paid_qa_ai.py).
A false tick is worse than an empty box.

Reading is `gather`, rules are `evaluate`, and neither touches a database. The Google Ads
transport is injected as `search(customer_id, gaql)`, so tests run on fake rows; the real
one (tracker/paid_gads_segments.search) shares the daily request budget with the other agents.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import paid_qa_book

log = logging.getLogger(__name__)

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
                    "customer.contains_eu_political_advertising FROM customer",
        "recs": "SELECT recommendation_subscription.type, recommendation_subscription.status FROM recommendation_subscription",
        "campaign": "SELECT campaign.id, campaign.name, campaign.status, campaign.serving_status, campaign.primary_status, "
                    "campaign.advertising_channel_type, campaign.advertising_channel_sub_type, campaign.start_date_time, "
                    "campaign.end_date_time, campaign.tracking_url_template, campaign.final_url_suffix, "
                    "campaign.url_custom_parameters, campaign.bidding_strategy_type, "
                    "campaign.maximize_conversions.target_cpa_micros, campaign.maximize_conversion_value.target_roas, "
                    "campaign.target_cpa.target_cpa_micros, campaign.target_roas.target_roas, "
                    "campaign.network_settings.target_google_search, campaign.network_settings.target_search_network, "
                    "campaign.network_settings.target_content_network, campaign.network_settings.target_youtube, "
                    "campaign.network_settings.target_google_tv_network, "
                    "campaign.geo_target_type_setting.positive_geo_target_type, "
                    "campaign.geo_target_type_setting.negative_geo_target_type, "
                    "campaign_budget.amount_micros, campaign_budget.explicitly_shared, "
                    "campaign.asset_automation_settings, campaign.dynamic_search_ads_setting.domain_name, "
                    "campaign.ai_max_setting.enable_ai_max, campaign.demand_gen_campaign_settings.upgraded_targeting "
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
        "shared_all": "SELECT shared_set.name, shared_set.type, shared_set.member_count FROM shared_set "
                      "WHERE shared_set.status = 'ENABLED'",
        "goals": "SELECT campaign_conversion_goal.category, campaign_conversion_goal.origin, campaign_conversion_goal.biddable "
                 "FROM campaign_conversion_goal WHERE %s" % w,
        "cust_goals": "SELECT customer_conversion_goal.category, customer_conversion_goal.origin, customer_conversion_goal.biddable "
                      "FROM customer_conversion_goal",
        "camp_assets": "SELECT campaign.id, campaign_asset.field_type, campaign_asset.status, campaign_asset.source, asset.type, asset.name, "
                       "asset.final_urls, asset.sitelink_asset.link_text, asset.sitelink_asset.description1, "
                       "asset.sitelink_asset.description2, asset.callout_asset.callout_text, asset.structured_snippet_asset.header, "
                       "asset.structured_snippet_asset.values, asset.call_asset.phone_number, asset.text_asset.text "
                       "FROM campaign_asset WHERE %s AND campaign_asset.status != 'REMOVED'" % w,
        "cust_assets": "SELECT customer_asset.field_type, customer_asset.status, customer_asset.source, asset.type, asset.final_urls, "
                       "asset.sitelink_asset.link_text, asset.sitelink_asset.description1, asset.callout_asset.callout_text, "
                       "asset.structured_snippet_asset.header, asset.structured_snippet_asset.values, "
                       "asset.call_asset.phone_number, asset.text_asset.text FROM customer_asset "
                       "WHERE customer_asset.status != 'REMOVED'",
    }
    if channel in ("SEARCH", "SHOPPING"):
        q["adgroups"] = ("SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.type, ad_group.ad_rotation_mode "
                         "FROM ad_group WHERE %s AND ad_group.status != 'REMOVED'" % w)
        q["ads"] = ("SELECT ad_group.id, ad_group_ad.status, ad_group_ad.ad.type, ad_group_ad.ad.final_urls, "
                    "ad_group_ad.ad_strength, ad_group_ad.policy_summary.approval_status, "
                    "ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
                    "ad_group_ad.ad.responsive_search_ad.path1, ad_group_ad.ad.responsive_search_ad.path2 "
                    "FROM ad_group_ad WHERE %s AND ad_group_ad.status != 'REMOVED'" % w)
        q["ag_criteria"] = ("SELECT ad_group.id, ad_group.name, ad_group_criterion.type, ad_group_criterion.negative, "
                            "ad_group_criterion.status, ad_group_criterion.age_range.type, ad_group_criterion.gender.type, "
                            "ad_group_criterion.income_range.type, ad_group_criterion.parental_status.type, "
                            "ad_group_criterion.user_list.user_list, ad_group_criterion.user_interest.user_interest_category, "
                            "ad_group_criterion.custom_audience.custom_audience, ad_group_criterion.combined_audience.combined_audience "
                            "FROM ad_group_criterion WHERE %s AND ad_group_criterion.type IN ('AGE_RANGE', 'GENDER', 'INCOME_RANGE', "
                            "'PARENTAL_STATUS', 'USER_LIST', 'USER_INTEREST', 'CUSTOM_AUDIENCE', 'COMBINED_AUDIENCE') "
                            "AND ad_group_criterion.status != 'REMOVED'" % w)
    if channel == "DEMAND_GEN":
        q["adgroups"] = ("SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.type FROM ad_group "
                         "WHERE %s AND ad_group.status != 'REMOVED'" % w)
        q["ads"] = ("SELECT ad_group.id, ad_group_ad.status, ad_group_ad.ad.type, ad_group_ad.ad.final_urls, "
                    "ad_group_ad.policy_summary.approval_status, ad_group_ad.ad.demand_gen_multi_asset_ad.headlines, "
                    "ad_group_ad.ad.demand_gen_multi_asset_ad.descriptions, ad_group_ad.ad.demand_gen_video_responsive_ad.headlines, "
                    "ad_group_ad.ad.demand_gen_video_responsive_ad.long_headlines, ad_group_ad.ad.demand_gen_video_responsive_ad.descriptions, "
                    "ad_group_ad.ad.demand_gen_carousel_ad.headline, ad_group_ad.ad.demand_gen_carousel_ad.description "
                    "FROM ad_group_ad WHERE %s AND ad_group_ad.status != 'REMOVED'" % w)
        q["ag_audiences"] = ("SELECT ad_group.id, ad_group.name, ad_group_criterion.type, ad_group_criterion.negative, ad_group_criterion.display_name, "
                             "ad_group_criterion.user_list.user_list, ad_group_criterion.user_interest.user_interest_category, "
                             "ad_group_criterion.custom_audience.custom_audience, ad_group_criterion.custom_intent.custom_intent, "
                             "ad_group_criterion.combined_audience.combined_audience, ad_group_criterion.audience.audience "
                             "FROM ad_group_criterion WHERE %s AND ad_group_criterion.type IN ('USER_LIST', 'USER_INTEREST', 'CUSTOM_AUDIENCE', "
                             "'CUSTOM_INTENT', 'COMBINED_AUDIENCE', 'AUDIENCE') AND ad_group_criterion.status != 'REMOVED'" % w)
    if channel == "PERFORMANCE_MAX":
        q["signals"] = ("SELECT asset_group.id, asset_group.name, asset_group_signal.audience.audience, asset_group_signal.search_theme.text "
                        "FROM asset_group_signal WHERE %s" % w)
        q["asset_groups"] = ("SELECT asset_group.id, asset_group.name, asset_group.status, asset_group.final_urls, "
                             "asset_group.ad_strength FROM asset_group WHERE %s AND asset_group.status != 'REMOVED'" % w)
        q["asset_group_assets"] = ("SELECT asset_group.id, asset_group_asset.field_type, asset_group_asset.status, "
                                   "asset.text_asset.text, asset.name FROM asset_group_asset "
                                   "WHERE %s AND asset_group_asset.status != 'REMOVED'" % w)
    return q


def gather(account_id, campaign_id, search, *, geo_fn=None, channel=None, web_fn=None, book=None):
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
    data["_book"] = book
    data["_names"] = {}
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
    if channel in ("PERFORMANCE_MAX", "DEMAND_GEN"):
        try:
            data["_names"] = _audience_names(account_id, search, data)
        except Exception as e:                              # noqa: BLE001 (the names are evidence, never a reason to stop)
            if getattr(e, "kind", "") in ("quota", "auth", "not_configured", "permission"):
                raise
            log.warning("qa auto: audience names failed: %s", str(e)[:200])
            errors["names"] = "audience names could not be read (%s)" % type(e).__name__
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


def _tail(resource):
    return str(resource or "").rsplit("/", 1)[-1]


def _audience_names(account_id, search, data):
    """{resource name: name} for the audiences a Performance Max signal or a Demand Gen ad group points at."""
    out = {}
    for gaql, key in (("SELECT audience.id, audience.name FROM audience", "audience"),
                      ("SELECT user_list.id, user_list.name FROM user_list", "userList"),
                      ("SELECT custom_audience.id, custom_audience.name FROM custom_audience", "customAudience"),
                      ("SELECT custom_interest.id, custom_interest.name FROM custom_interest", "customInterest")):
        for r in search(account_id, gaql):
            x = r.get(key) or {}
            if x.get("resourceName") and x.get("name"):
                out[x["resourceName"]] = x["name"]
    ids = sorted({_tail((((r.get("adGroupCriterion") or {}).get("userInterest")) or {}).get("userInterestCategory"))
                  for r in data.get("ag_audiences") or []} - {""})
    ids = [i for i in ids if i.isdigit()]
    if ids:
        for r in search(account_id, "SELECT user_interest.user_interest_id, user_interest.name, user_interest.taxonomy_type FROM user_interest "
                                    "WHERE user_interest.user_interest_id IN (%s)" % ", ".join(ids)):
            x = r.get("userInterest") or {}
            if x.get("resourceName") and x.get("name"):
                out[x["resourceName"]] = "%s (%s)" % (x["name"], str(x.get("taxonomyType") or "").replace("_", " ").lower() or "interest")
    return out


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
        self.book = data.get("_book")
        self.names = data.get("_names") or {}

    def label(self, resource):
        """The name of an audience a resource name points at, or what it is when Google would not name it."""
        return self.names.get(resource) or ("audience " + _tail(resource) if resource else "an audience")

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

    def no_book(self, what):
        """The sentence that closes a printed fact when there is nothing to compare it with."""
        if self.d.get("_bookNotUsed"):
            return " There is no campaign book for a %s campaign, so it is judged on the spot." % self.d["_bookNotUsed"]
        return " The campaign book names no %s to compare with." % what

    def from_book(self, *names):
        return paid_qa_book.lookup(self.book, *names)


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


def _live_ads(c):
    return [r for r in c.rows("ads") if (r.get("adGroupAd") or {}).get("status") == "ENABLED" and ((r.get("adGroup") or {}).get("id"))]


def _rsas(c):
    return [r for r in _live_ads(c) if ((r["adGroupAd"].get("ad") or {}).get("type") == "RESPONSIVE_SEARCH_AD")]


def _ad_texts(c):
    """Every headline, description and display path of the enabled ads, with where it is."""
    out = []
    for r in _rsas(c):
        rsa = (r["adGroupAd"].get("ad") or {}).get("responsiveSearchAd") or {}
        for kind in ("headlines", "descriptions"):
            for a in rsa.get(kind) or []:
                if a.get("text"):
                    out.append((kind[:-1], a["text"]))
        for p in ("path1", "path2"):
            if rsa.get(p):
                out.append(("path", rsa[p]))
    for r in c.rows("asset_group_assets"):
        t = ((r.get("asset") or {}).get("textAsset") or {}).get("text")
        if t and (r.get("assetGroupAsset") or {}).get("status") == "ENABLED":
            out.append((((r.get("assetGroupAsset") or {}).get("fieldType") or "text").lower(), t))
    for r in _live_ads(c):
        ad = r["adGroupAd"].get("ad") or {}
        for key in ("demandGenMultiAssetAd", "demandGenVideoResponsiveAd"):
            for kind in ("headlines", "longHeadlines", "descriptions"):
                for a in (ad.get(key) or {}).get(kind) or []:
                    if a.get("text"):
                        out.append((kind[:-1], a["text"]))
        for key, kind in (("headline", "headline"), ("description", "description")):
            t = (ad.get("demandGenCarouselAd") or {}).get(key)
            if t:
                out.append((kind, t))
    return out


def _asset_counts(c, field_types):
    """(campaign-level, account-level) counts of live assets of these field types."""
    camp = sum(1 for r in c.rows("camp_assets") if (r.get("campaignAsset") or {}).get("fieldType") in field_types
               and (r.get("campaignAsset") or {}).get("status") == "ENABLED")
    acct = sum(1 for r in c.rows("cust_assets") if (r.get("customerAsset") or {}).get("fieldType") in field_types
               and (r.get("customerAsset") or {}).get("status") == "ENABLED")
    return camp, acct


def _auto_created(c, field_types=None):
    """[(level, field type)] for assets Google made on its own, at the campaign and the account."""
    out = []
    for r in c.rows("camp_assets"):
        k = r.get("campaignAsset") or {}
        if k.get("source") == "AUTOMATICALLY_CREATED" and k.get("status") != "REMOVED" and (field_types is None or k.get("fieldType") in field_types):
            out.append(("campaign", k.get("fieldType")))
    for r in c.rows("cust_assets"):
        k = r.get("customerAsset") or {}
        if k.get("source") == "AUTOMATICALLY_CREATED" and k.get("status") != "REMOVED" and (field_types is None or k.get("fieldType") in field_types):
            out.append(("account", k.get("fieldType")))
    return out


# ── The rules ───────────────────────────────────────────────────────────────

RULES = {}


def rule(*ids):
    def deco(fn):
        for i in ids:
            RULES[i] = fn
        return fn
    return deco


def _need_campaign(c):
    return None if c.camp else (c.unread("campaign") or F(None, "Not available."))


# 1. Campaign setup ----------------------------------------------------------

NAME_RE = re.compile(r"^P2_(Search|PMax|Pmax|PMAX)_(.+)$", re.I)


@rule("1.1")
def r_name(c):
    if _need_campaign(c):
        return _need_campaign(c)
    name = c.camp.get("name") or ""
    m = NAME_RE.match(name)
    want = "P2_Search_<launch date> or P2_PMax_<launch date>"
    if not m:
        return F("fail", "The name is '%s'. It should follow P2_<Search or PMax>_<launch date>, e.g. %s." % (name, want))
    kind = type_label(c.channel)
    said = "PMax" if m.group(1).lower() == "pmax" else "Search"
    if kind in ("Search", "PMax") and said != kind:
        return F("fail", "The name '%s' says %s but the campaign is %s." % (name, said, kind))
    if not re.search(r"\d{2,4}", m.group(2)):
        return F("fail", "The name '%s' has no launch date after the campaign type." % name)
    return F("pass", "The name '%s' follows P2_<type>_<launch date>." % name)


@rule("1.2")
def r_type(c):
    if _need_campaign(c):
        return _need_campaign(c)
    actual = type_label(c.channel) or c.channel or "?"
    sub = c.camp.get("advertisingChannelSubType")
    text = "The campaign is %s%s." % (actual, (" (" + sub.replace("_", " ").title() + ")") if sub and sub != "UNSPECIFIED" else "")
    book = c.from_book("campaign type", "type")
    if not book:
        return F(None, text + c.no_book("type"))
    b = re.sub(r"[^a-z]", "", book.lower())
    ok = {"Search": ("search",), "PMax": ("pmax", "performancemax"), "Shopping": ("shopping",), "Display": ("display",),
          "Demand Gen": ("demandgen",), "Video": ("video",), "App": ("app",)}.get(actual, ())
    if any(k in b for k in ok):
        return F("pass", text + " The campaign book says %s." % book)
    return F("fail", text + " The campaign book says %s." % book)


@rule("1.3")
def r_networks(c):
    n = c.camp.get("networkSettings") if c.camp else None
    if n is None:
        return _need_campaign(c) or F(None, "Network settings not returned.")
    on = [label for key, label in (("targetSearchNetwork", "Search partners"), ("targetContentNetwork", "Display network"),
                                   ("targetYoutube", "YouTube"), ("targetGoogleTvNetwork", "Google TV")) if n.get(key)]
    if on:
        return F("fail", "These extra networks are ON: %s. The campaign book wants none." % ", ".join(on))
    return F("pass", "No extra network is on: Search partners, Display, YouTube and Google TV are all off.")


@rule("1.4")
def r_adcopies(c):
    if c.channel == "PERFORMANCE_MAX":
        if c.unread("asset_groups"):
            return c.unread("asset_groups")
        n = len(c.rows("asset_groups"))
        return F("fail" if not n else None, "%d asset group%s. Spelling, Start Case and brand wording are read from the ad text." % (n, "" if n == 1 else "s"))
    if c.unread("ads"):
        return c.unread("ads")
    live = _live_ads(c)
    if not live:
        return F("fail", "No enabled ad in the campaign. At least 1 is needed.")
    return F(None, "%d enabled ad%s. Spelling, Start Case and brand wording are read from the ad text." % (len(live), "" if len(live) == 1 else "s"))


@rule("1.5")
def r_ai_max(c):
    if _need_campaign(c):
        return _need_campaign(c)
    on = bool((c.camp.get("aiMaxSetting") or {}).get("enableAiMax"))
    return F("fail" if on else "pass", "AI Max for Search is %s." % ("ON" if on else "OFF"))


@rule("1.6")
def r_other_assets(c):
    if c.unread("camp_assets", "cust_assets"):
        return c.unread("camp_assets", "cust_assets")
    parts = []
    for label, types in (("sitelinks", {"SITELINK"}), ("callouts", {"CALLOUT"}), ("structured snippets", {"STRUCTURED_SNIPPET"}),
                         ("call", {"CALL"}), ("images", {"AD_IMAGE", "MARKETING_IMAGE"}), ("promotions", {"PROMOTION"}), ("prices", {"PRICE"})):
        a, b = _asset_counts(c, types)
        parts.append("%s %d on the campaign, %d on the account" % (label, a, b))
    return F(None, "; ".join(parts) + ".")


@rule("1.7")
def r_goal(c):
    if c.unread("goals", "cust_goals"):
        return c.unread("goals", "cust_goals")
    cust = {(r["customerConversionGoal"].get("category"), r["customerConversionGoal"].get("origin")): bool(r["customerConversionGoal"].get("biddable"))
            for r in c.rows("cust_goals") if r.get("customerConversionGoal")}
    camp = {(r["campaignConversionGoal"].get("category"), r["campaignConversionGoal"].get("origin")): bool(r["campaignConversionGoal"].get("biddable"))
            for r in c.rows("goals") if r.get("campaignConversionGoal")}
    if not cust or not camp:
        return F(None, "No conversion goal list came back to compare.")
    diff = ["%s/%s is %s on the account but %s on the campaign" % (k[0], k[1], "optimised" if cust.get(k) else "not optimised", "optimised" if camp.get(k) else "not optimised")
            for k in sorted(set(cust) | set(camp), key=str) if cust.get(k) != camp.get(k)]
    if diff:
        return F("fail", "The campaign does not use the account-level default goals: %s." % _list(diff, 5))
    on = sorted("%s/%s" % k for k, v in cust.items() if v)
    return F("pass", "The campaign uses the account-level default goals (%d goals optimised: %s)." % (len(on), _list(on, 5)))


_CONVERSION_FAMILY = ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS")


def _strategy_labels(camp):
    """Normalised phrases that describe the campaign's bid strategy, so a book can say it its own way."""
    t = camp.get("biddingStrategyType") or ""
    labels = {"MAXIMIZE_CONVERSIONS": ["maximize conversions", "max conversions"], "MAXIMIZE_CONVERSION_VALUE": ["maximize conversion value", "max conversion value"],
              "TARGET_CPA": ["target cpa", "tcpa"], "TARGET_ROAS": ["target roas", "troas"], "MANUAL_CPC": ["manual cpc", "enhanced cpc", "ecpc"],
              "TARGET_SPEND": ["maximize clicks", "max clicks"], "TARGET_IMPRESSION_SHARE": ["target impression share", "impression share"]}.get(t, [])
    if t == "MAXIMIZE_CONVERSIONS" and (camp.get("maximizeConversions") or {}).get("targetCpaMicros"):
        labels += ["target cpa", "tcpa"]
    if t == "MAXIMIZE_CONVERSION_VALUE" and (camp.get("maximizeConversionValue") or {}).get("targetRoas"):
        labels += ["target roas", "troas"]
    return labels


@rule("1.8")
def r_bid_strategy(c):
    if _need_campaign(c):
        return _need_campaign(c)
    t = (c.camp.get("biddingStrategyType") or "?")
    text = "Bid strategy is %s." % t.replace("_", " ").lower()
    book = c.from_book("bid strategy type", "bid strategy")
    if not book:
        return F(None, text + c.no_book("bid strategy"))
    b = " ".join(re.sub(r"[^a-z]+", " ", book.lower().replace("maximise", "maximize")).split())
    labels = _strategy_labels(c.camp)
    wants_target = [g for g in (("target cpa", "tcpa"), ("target roas", "troas")) if any(k in b for k in g)]
    if any(lbl in b for lbl in labels) and all(any(k in labels for k in g) for g in wants_target):
        return F("pass", text + " The campaign book says %s." % book)
    return F("fail", text + " The campaign book says %s." % book)


@rule("1.9")
def r_optimization_goal(c):
    if _need_campaign(c):
        return _need_campaign(c)
    t = c.camp.get("biddingStrategyType") or "?"
    if t not in _CONVERSION_FAMILY:
        return F("fail", "The bid strategy is %s, which does not optimise for conversions." % t.replace("_", " ").lower())
    g = r_goal(c)
    if g["result"] == "pass":
        return F("pass", "The campaign optimises for conversions (%s) on the account-level default goals." % t.replace("_", " ").lower())
    return F(None, "The bid strategy optimises for conversions (%s). %s" % (t.replace("_", " ").lower(), g["found"]))


def _devices(c):
    out = []
    for r in c.rows("criteria"):
        cc = r.get("campaignCriterion") or {}
        if cc.get("type") == "DEVICE":
            mod = cc.get("bidModifier")
            out.append("%s %s" % ((cc.get("device") or {}).get("type", "?").lower(),
                                  "excluded (-100%)" if mod == 0 else ("bid %+d%%" % round((mod - 1) * 100)) if mod not in (None, 1) else "all traffic"))
    return out


@rule("1.10")
def r_devices(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    return F(None, "Devices: %s." % (_list(_devices(c)) or "no device setting, so all devices"))


def _demographics(c, kind):
    """Included and excluded values of one demographic (AGE_RANGE, GENDER, INCOME_RANGE), from the campaign and its ad groups."""
    key = {"AGE_RANGE": "ageRange", "GENDER": "gender", "INCOME_RANGE": "incomeRange"}[kind]
    inc, exc = set(), set()
    for r in c.rows("criteria"):
        cc = r.get("campaignCriterion") or {}
        if cc.get("type") == kind:
            (exc if cc.get("negative") else inc).add((cc.get(key) or {}).get("type"))
    for r in c.rows("ag_criteria"):
        cc = r.get("adGroupCriterion") or {}
        if cc.get("type") == kind and cc.get("status") != "REMOVED":
            (exc if cc.get("negative") else inc).add((cc.get(key) or {}).get("type"))
    short = lambda s: (s or "?").replace(kind + "_", "").replace("AGE_RANGE_", "").replace("_", "-").lower()
    return sorted(short(x) for x in inc - {None}), sorted(short(x) for x in exc - {None})


DG_DEMOGRAPHICS = "Demand Gen sets this on its ad groups, which this check does not read yet. Look at it in Google Ads."


@rule("1.11")
def r_genders(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    if c.channel == "DEMAND_GEN":
        return F(None, DG_DEMOGRAPHICS)
    inc, exc = _demographics(c, "GENDER")
    return F(None, "Genders: %s; excluded: %s." % (_list(inc) or "no gender setting", _list(exc) or "none"))


@rule("1.12")
def r_income(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    if c.channel == "DEMAND_GEN":
        return F(None, DG_DEMOGRAPHICS)
    inc, exc = _demographics(c, "INCOME_RANGE")
    return F(None, "Household income: %s; excluded: %s." % (_list(inc) or "no income setting, so all", _list(exc) or "none"))


@rule("1.13")
def r_audience_exclusion(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    kinds = ("USER_LIST", "USER_INTEREST", "CUSTOM_AUDIENCE", "COMBINED_AUDIENCE")
    out = [("campaign", (r.get("campaignCriterion") or {}).get("type")) for r in c.rows("criteria")
           if (r.get("campaignCriterion") or {}).get("type") in kinds and (r.get("campaignCriterion") or {}).get("negative")]
    out += [(r.get("adGroup", {}).get("name") or "an ad group", (r.get("adGroupCriterion") or {}).get("type")) for r in c.rows("ag_criteria")
            if (r.get("adGroupCriterion") or {}).get("type") in kinds and (r.get("adGroupCriterion") or {}).get("negative")]
    dg_kinds = kinds + ("CUSTOM_INTENT", "AUDIENCE")              # Demand Gen keeps its ad group audiences in their own read
    out += [(r.get("adGroup", {}).get("name") or "an ad group", (r.get("adGroupCriterion") or {}).get("type")) for r in c.rows("ag_audiences")
            if (r.get("adGroupCriterion") or {}).get("type") in dg_kinds and (r.get("adGroupCriterion") or {}).get("negative")]
    if not out and c.channel == "DEMAND_GEN" and c.unread("ag_audiences"):
        return c.unread("ag_audiences")                          # not read: never a pass
    if out:
        return F("fail", "%d audience exclusion%s found: %s. The campaign book wants none." % (len(out), "" if len(out) == 1 else "s",
                                                                                       _list("%s on %s" % (k.replace("_", " ").lower(), where) for where, k in out)))
    if c.channel == "SEARCH" and not c.read("ag_criteria"):
        return F(None, "No campaign-level audience exclusion. Ad group audiences could not be read.")
    return F("pass", "No audience is excluded on the campaign or its ad groups.")


@rule("1.14")
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
    text = "%d location%s targeted: %s." % (len(inc), "" if len(inc) == 1 else "s", _list(inc, 10) or "none set (the campaign has no location targeting)")
    if prox:
        text += " Radius: %s." % _list(prox)
    return F(None, text + " Excluded: %s." % (_list(exc, 8) or "none"))


@rule("1.15")
def r_language(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    ids = [re.sub(r"\D", "", ((r["campaignCriterion"].get("language") or {}).get("languageConstant")) or "")
           for r in c.rows("criteria") if (r.get("campaignCriterion") or {}).get("type") == "LANGUAGE"]
    if not ids:
        return F("fail", "No language is set, so the campaign targets all languages. It should be English only.")
    names = [(c.d.get("_lang") or {}).get(i) or "language " + i for i in ids]
    if names == ["English"] or ids == ["1000"]:                 # 1000 is Google's id for English, so a failed name lookup does not turn it into a fail
        return F("pass", "Language targeting is English only.")
    return F("fail", "Language targeting is %s. It should be English only." % _list(names))


@rule("1.16")
def r_age(c):
    if c.unread("criteria"):
        return c.unread("criteria")
    if c.channel == "DEMAND_GEN":
        return F(None, DG_DEMOGRAPHICS)
    inc, exc = _demographics(c, "AGE_RANGE")
    return F(None, "Ages targeted: %s; excluded: %s." % (_list(inc) or "no age setting, so all ages", _list(exc) or "none"))


_DKI = re.compile(r"\{\s*(keyword|keywor|kw)\s*:", re.I)


@rule("1.17")
def r_dki(c):
    if c.channel == "PERFORMANCE_MAX":
        return F("pass", "Performance Max assets cannot use keyword insertion.")
    if c.channel == "DEMAND_GEN" and not c.rows("ads") and c.read("ads"):
        return F(None, "No enabled ad text to scan.")
    if c.unread("ads"):
        return c.unread("ads")
    texts = _ad_texts(c)
    if not texts:
        return F(None, "No enabled ad text to scan.")
    hits = [t for _, t in texts if _DKI.search(t)]
    if hits:
        return F("fail", "Dynamic keyword insertion is used in %d place%s: %s." % (len(hits), "" if len(hits) == 1 else "s", _list(hits, 4)))
    return F("pass", "No dynamic keyword insertion in the %d headlines, descriptions and paths of the enabled ads." % len(texts))


@rule("1.18")
def r_negatives(c):
    if c.unread("shared", "criteria"):
        return c.unread("shared", "criteria")
    lists = [r for r in c.rows("shared") if (r.get("sharedSet") or {}).get("type") in ("NEGATIVE_KEYWORDS", "ACCOUNT_LEVEL_NEGATIVE_KEYWORDS")
             and (r.get("campaignSharedSet") or {}).get("status") == "ENABLED"]
    own = [1 for r in c.rows("criteria") if (r.get("campaignCriterion") or {}).get("type") == "KEYWORD"
           and (r.get("campaignCriterion") or {}).get("negative")]
    acct = [r["sharedSet"] for r in c.rows("shared_all") if (r.get("sharedSet") or {}).get("type") in ("NEGATIVE_KEYWORDS", "ACCOUNT_LEVEL_NEGATIVE_KEYWORDS")]
    names = ["%s (%s terms)" % ((r["sharedSet"].get("name") or "list"), r["sharedSet"].get("memberCount", "?")) for r in lists]
    free = [s.get("name") for s in acct if s.get("name") not in {r["sharedSet"].get("name") for r in lists}]
    text = "Lists on the campaign: %s. Campaign negatives: %d. Negative lists in the account: %d%s." % (
        _list(names) or "none", len(own), len(acct), (", not on this campaign: " + _list(free, 4)) if free else "")
    return F("pass" if lists else "fail", text)


@rule("1.19")
def r_eu_political(c):
    if not c.customer:
        return c.unread("customer") or F(None, "Not available.")
    v = c.customer.get("containsEuPoliticalAdvertising")
    if v == "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING":
        return F("pass", "EU political ads is declared as: does not contain them (off).")
    if v == "CONTAINS_EU_POLITICAL_ADVERTISING":
        return F("fail", "EU political ads is declared as: contains them. It should be off.")
    return F("fail", "The EU political ads declaration has not been made. It should be answered 'no' (off).")


def _tracking_lines(c):
    cu, ca = c.customer, c.camp
    parts = ["Account tracking template: %s." % (cu.get("trackingUrlTemplate") or "none"),
             "Account final URL suffix: %s." % (cu.get("finalUrlSuffix") or "none"),
             "Campaign tracking template: %s." % (ca.get("trackingUrlTemplate") or "none"),
             "Campaign final URL suffix: %s." % (ca.get("finalUrlSuffix") or "none")]
    cp = ca.get("urlCustomParameters") or []
    parts.append("Custom parameters: %s." % (", ".join("{_%s}=%s" % (p.get("key"), p.get("value")) for p in cp) or "none"))
    return " ".join(parts)


@rule("1.20")
def r_campaign_url(c):
    if not c.customer or not c.camp:
        return c.unread("customer", "campaign") or F(None, "Not available.")
    has = bool(c.customer.get("trackingUrlTemplate") or c.customer.get("finalUrlSuffix")
               or c.camp.get("trackingUrlTemplate") or c.camp.get("finalUrlSuffix"))
    return F("pass" if has else "fail", _tracking_lines(c))


@rule("1.21")
def r_presence(c):
    if _need_campaign(c):
        return _need_campaign(c)
    v = (c.camp.get("geoTargetTypeSetting") or {}).get("positiveGeoTargetType")
    if not v:
        return F(None, "Location option not returned.")
    return F("pass" if v == "PRESENCE" else "fail", "Target option: %s." % ("Presence: people in or regularly in the included locations" if v == "PRESENCE" else "Presence or interest"))


@rule("1.22")
def r_dates(c):
    if _need_campaign(c):
        return _need_campaign(c)
    start, end = _day(c.camp.get("startDateTime")), _day(c.camp.get("endDateTime"))
    has_end = bool(end) and not end.startswith("2037")
    text = "Start date %s; %s." % (start or "not set", ("end date %s" % end) if has_end else "no end date")
    if has_end:
        return F("fail", text + " There should be no end date.")
    return F(None, text + " The start date should be the day it went live.")


@rule("1.23")
def r_rotation(c):
    if c.unread("adgroups"):
        return c.unread("adgroups")
    modes = Counter(((r.get("adGroup") or {}).get("adRotationMode") or "OPTIMIZE") for r in c.rows("adgroups")
                    if (r.get("adGroup") or {}).get("status") == "ENABLED")
    if not modes:
        return F(None, "No enabled ad group.")
    bad = modes.get("ROTATE_FOREVER", 0)
    return F("fail" if bad else "pass", "Ad rotation: %s." % _list("%s on %d ad group%s" % (m.replace("_", " ").lower(), n, "" if n == 1 else "s") for m, n in modes.items()))


@rule("1.24")
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


@rule("1.25")
def r_final_url(c):
    if c.channel == "PERFORMANCE_MAX":
        if c.unread("asset_groups"):
            return c.unread("asset_groups")
        urls = sorted({u for r in c.rows("asset_groups") for u in ((r.get("assetGroup") or {}).get("finalUrls") or [])})
    else:
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
    # A page is broken only when the site says so (not found, gone, a server error). A site that refuses our visit (401, 403, 429, 503) or a visit that
    # timed out from here says nothing about the visitors the ad sends: that is "could not confirm", never a crossed box.
    def gone(p):
        st = p.get("status") or 0
        return not p.get("error") and (st in (404, 410) or (500 <= st < 600 and st != 503) or (400 <= st < 500 and st not in (401, 403, 407, 408, 429)))
    broken = ["%s (%s)" % (p["url"], p.get("status")) for p in pages if gone(p)]
    if broken:
        return F("fail", "These pages do not load: %s. %s" % (_list(broken, 4), text))
    unsure = ["%s (%s)" % (p["url"], p.get("status") or (p.get("error") or "unreadable")[:60]) for p in pages if p.get("error") or (p.get("status") or 0) >= 400]
    if unsure:
        return F(None, "%s All HTTPS. These could not be confirmed to load, and the site may only be refusing automated visits: %s." % (text, _list(unsure, 4)))
    if pages and len(pages) >= len(urls):
        return F("pass", text + " All HTTPS and all load.")
    if pages:
        return F(None, text + " All HTTPS; %d of %d were opened and loaded." % (len(pages), len(urls)))
    return F(None, text + " All HTTPS. The pages could not be opened to confirm they load.")


# 2. Automation --------------------------------------------------------------

def _automation(c):
    return {a.get("assetAutomationType"): a.get("assetAutomationStatus") for a in (c.camp.get("assetAutomationSettings") or [])}


_AUTOMATION_NAMES = {
    "TEXT_ASSET_AUTOMATION": "text customization", "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION": "final URL expansion",
    "GENERATE_IMAGE_EXTRACTION": "image extraction", "GENERATE_IMAGE_ENHANCEMENT": "image enhancement",
    "GENERATE_ENHANCED_YOUTUBE_VIDEOS": "video enhancement",
}


@rule("2.1")
def r_url_expansion(c):
    if _need_campaign(c):
        return _need_campaign(c)
    v = _automation(c).get("FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION")
    if not v:
        if c.channel == "SEARCH" and not (c.camp.get("aiMaxSetting") or {}).get("enableAiMax"):
            return F("pass", "AI Max is off, so final URL expansion is not in use.")
        return F(None, "The final URL expansion setting was not returned.")
    return F("fail" if v == "OPTED_IN" else "pass", "Final URL expansion is %s." % ("ON" if v == "OPTED_IN" else "OFF"))


@rule("2.2")
def r_asset_automation(c):
    if _need_campaign(c):
        return _need_campaign(c)
    s = _automation(c)
    if not s:
        return F(None, "No asset automation setting was returned.")
    on = [k for k, v in s.items() if v == "OPTED_IN"]
    text = "Asset automation: %s." % _list("%s %s" % (_AUTOMATION_NAMES.get(k) or k.replace("_", " ").lower(), "ON" if v == "OPTED_IN" else "off") for k, v in sorted(s.items()))
    return F("fail" if on else "pass", text)


# 3. Automatically created assets (the switches are not in the API) ----------

NOT_IN_API = "Google Ads does not show this switch to the API, so it cannot be read here."


def _auto_asset_rule(label, types, expect_off=True):
    def fn(c):
        if c.unread("camp_assets", "cust_assets"):
            return c.unread("camp_assets", "cust_assets")
        made = _auto_created(c, types) if types else []
        if made and expect_off:
            return F("fail", "%d automatically created %s asset%s exist (%s), so this is ON. It should be off." % (
                len(made), label, "" if len(made) == 1 else "s", _list(sorted({lv for lv, _ in made}))))
        if not types:
            return F(None, NOT_IN_API)
        extra = " No automatically created %s asset exists, which is consistent with off but does not prove it." % label if expect_off else ""
        return F(None, NOT_IN_API + extra)
    return fn


RULES["3.1"] = _auto_asset_rule("app", {"MOBILE_APP"})
RULES["3.2"] = _auto_asset_rule("location", {"LOCATION", "AFFILIATE_LOCATION"})
RULES["3.3"] = _auto_asset_rule("promotion", {"PROMOTION"})
RULES["3.4"] = _auto_asset_rule("business logo", {"BUSINESS_LOGO", "LOGO"}, expect_off=False)
RULES["3.5"] = _auto_asset_rule("business name", {"BUSINESS_NAME"}, expect_off=False)
RULES["3.6"] = _auto_asset_rule("callout", {"CALLOUT"})
RULES["3.7"] = _auto_asset_rule("image", {"AD_IMAGE", "MARKETING_IMAGE"})
RULES["3.8"] = _auto_asset_rule("sitelink", {"SITELINK"})
RULES["3.9"] = _auto_asset_rule("structured snippet", {"STRUCTURED_SNIPPET"})
RULES["3.10"] = _auto_asset_rule("longer headline", None)
RULES["3.11"] = _auto_asset_rule("seller rating", {"SELLER_RATING"})


# 4. Auto-apply recommendations ----------------------------------------------

REC_TOKENS = {
    "4.1": ["AD_ROTATION"], "4.2": ["RESPONSIVE_SEARCH_AD", "TEXT_AD", "IMPROVE_AD_STRENGTH"], "4.3": ["REDUNDANT"],
    "4.4": ["NON_SERVING", "UNUSED_KEYWORD"], "4.5": ["CONFLICTING"], "4.6": ["OPTIMIZED_TARGETING", "TARGETING_OPT"],
    "4.7": ["CONVERSION_TRACKING", "GOOGLE_TAG", "ENHANCED_CONVERSION"], "4.8": ["KEYWORD"], "4.9": ["BROAD_MATCH"],
    "4.10": ["DISPLAY_EXPANSION"], "4.11": ["IMPRESSION_SHARE"], "4.12": ["MAXIMIZE_CLICKS"], "4.13": ["MAXIMIZE_CONVERSIONS"],
    "4.14": ["MAXIMIZE_CONVERSION_VALUE"], "4.15": ["TARGET_CPA_OPT_IN"], "4.16": ["TARGET_ROAS_OPT_IN"], "4.17": ["SET_TARGET_CPA"],
    "4.18": ["SET_TARGET_ROAS"], "4.19": ["TARGET_CPA"], "4.20": ["TARGET_ROAS"], "4.21": ["STORE_VISIT"],
}
REC_EXCLUDE = {"4.8": ["BROAD", "MATCH", "NON_SERVING", "UNUSED", "REDUNDANT", "CONFLICTING"], "4.13": ["VALUE"], "4.19": ["OPT_IN", "SET_"], "4.20": ["OPT_IN", "SET_"]}


def _enabled_recs(c):
    return sorted((r.get("recommendationSubscription") or {}).get("type") or "?" for r in c.rows("recs")
                  if (r.get("recommendationSubscription") or {}).get("status") == "ENABLED")


def _rec_rule(cid):
    def fn(c):
        if c.unread("recs"):
            return c.unread("recs")
        on = _enabled_recs(c)
        if not on:
            return F("pass", "Auto-apply is off for every recommendation type on the account (none is subscribed).")
        hit = [t for t in on if any(k in t for k in REC_TOKENS[cid]) and not any(x in t for x in REC_EXCLUDE.get(cid, []))]
        if hit:
            return F("fail", "Auto-apply is ON for: %s. It should be off." % _list(hit))
        return F(None, "Auto-apply is ON for other recommendation types (%s), none of which clearly matches this one." % _list(on))
    return fn


for _i in range(1, 22):
    RULES["4.%d" % _i] = _rec_rule("4.%d" % _i)


# 5. Account automation ------------------------------------------------------

WANTED_AUTO = {"BUSINESS_NAME", "BUSINESS_LOGO", "LOGO"}        # the automatically created assets the checklist wants switched on (3.4 business logo, 3.5 business name)


@rule("5.1")
def r_generated_assets(c):
    if c.unread("camp_assets", "cust_assets", "campaign"):
        return c.unread("camp_assets", "cust_assets", "campaign")
    made = [m for m in _auto_created(c) if m[1] not in WANTED_AUTO]          # 3.4 and 3.5 ask for these ON, so 5.1 must not count them against the campaign
    on = [k for k, v in _automation(c).items() if v == "OPTED_IN"]
    if made or on:
        bits = []
        if made:
            bits.append("%d automatically created asset%s (%s)" % (len(made), "" if len(made) == 1 else "s", _list(sorted({t for _, t in made}), 4)))
        bits += ["%s automation ON" % _AUTOMATION_NAMES.get(k, k.lower()) for k in on]
        return F("fail", "Automatically generated assets are on: %s." % _list(bits))
    return F(None, "No automatically created asset exists and no asset automation is on for the campaign. The account-level switch itself is not visible to the API.")


@rule("5.2")
def r_migration(c):
    return F(None, "Auto migration and upgrades is an account setting that Google Ads does not show to the API, so it cannot be read here.")


# ── Run ─────────────────────────────────────────────────────────────────────

# 6. On the spot (Performance Max and Demand Gen) ----------------------------
# The campaign book is for Search campaigns. These are the checks a Search campaign does not have: what Performance Max is told about
# its audience (signals, search themes) and how Google rates its asset groups, and who a Demand Gen ad group is aimed at. A rule
# settles what is exact (an asset group with no signal, a rating below Good); whether what is there fits the business is the AI's call
# on the landing page text.

def _pmax_groups(c):
    groups = {}
    for r in c.rows("asset_groups"):
        g = r.get("assetGroup") or {}
        groups[str(g.get("id"))] = {"name": g.get("name") or "an asset group", "strength": g.get("adStrength") or "", "audiences": [], "themes": []}
    for r in c.rows("signals"):
        g = groups.get(str((r.get("assetGroup") or {}).get("id")))
        s = r.get("assetGroupSignal") or {}
        if g is None:
            continue
        if (s.get("audience") or {}).get("audience"):
            g["audiences"].append(c.label(s["audience"]["audience"]))
        elif (s.get("searchTheme") or {}).get("text"):
            g["themes"].append(s["searchTheme"]["text"])
    return list(groups.values())


@rule("6.1")
def r_signals(c):
    if c.channel != "PERFORMANCE_MAX":
        return None
    if c.unread("asset_groups", "signals"):
        return c.unread("asset_groups", "signals")
    groups = _pmax_groups(c)
    if not groups:
        return F(None, "The campaign has no asset group to read audience signals from.")
    none = [g["name"] for g in groups if not g["audiences"]]
    lines = "; ".join("%s: %s" % (g["name"], _list(sorted(set(g["audiences"]))) or "none") for g in groups)
    if none:
        return F("fail", "No audience signal on %s. %s." % (_list(none), lines))
    return F(None, "Audience signals by asset group. %s. Whether they fit the business is read against the landing page." % lines)


@rule("6.2")
def r_search_themes(c):
    if c.channel != "PERFORMANCE_MAX":
        return None
    if c.unread("asset_groups", "signals"):
        return c.unread("asset_groups", "signals")
    groups = _pmax_groups(c)
    if not groups:
        return F(None, "The campaign has no asset group to read search themes from.")
    none = [g["name"] for g in groups if not g["themes"]]
    lines = "; ".join("%s: %s" % (g["name"], ("%d theme%s: %s" % (len(g["themes"]), "" if len(g["themes"]) == 1 else "s", _list(g["themes"], 25)))
                                  if g["themes"] else "none") for g in groups)
    if none:
        return F("fail", "No search theme on %s. %s." % (_list(none), lines))
    return F(None, "Search themes by asset group. %s. Whether they match the landing page is read against its text." % lines)


@rule("6.3")
def r_group_strength(c):
    if c.channel != "PERFORMANCE_MAX":
        return None
    if c.unread("asset_groups"):
        return c.unread("asset_groups")
    groups = _pmax_groups(c)
    if not groups:
        return F(None, "The campaign has no asset group.")
    weak = [g for g in groups if g["strength"] in ("POOR", "AVERAGE")]
    text = "; ".join("%s: %s" % (g["name"], (g["strength"] or "not rated").lower().replace("_", " ")) for g in groups)
    if weak:
        return F("fail", "Google rates %s below Good. All: %s." % (_list(["%s (%s)" % (g["name"], g["strength"].lower()) for g in weak]), text))
    if any(g["strength"] not in ("GOOD", "EXCELLENT") for g in groups):
        return F(None, "Some asset groups are not rated yet. %s." % text)
    return F("pass", "Every asset group is rated Good or Excellent. %s." % text)


@rule("6.4")
def r_dg_audiences(c):
    if c.channel != "DEMAND_GEN":
        return None
    if c.unread("adgroups", "ag_audiences"):
        return c.unread("adgroups", "ag_audiences")
    groups = {str((r.get("adGroup") or {}).get("id")): {"name": (r.get("adGroup") or {}).get("name") or "an ad group", "audiences": []}
              for r in c.rows("adgroups") if (r.get("adGroup") or {}).get("status") == "ENABLED"}
    for r in c.rows("ag_audiences"):
        cc = r.get("adGroupCriterion") or {}
        g = groups.get(str((r.get("adGroup") or {}).get("id")))
        if g is None or cc.get("negative"):
            continue
        ref = ((cc.get("userList") or {}).get("userList") or (cc.get("userInterest") or {}).get("userInterestCategory")
               or (cc.get("customAudience") or {}).get("customAudience") or (cc.get("customIntent") or {}).get("customIntent")
               or (cc.get("combinedAudience") or {}).get("combinedAudience") or (cc.get("audience") or {}).get("audience"))
        g["audiences"].append(c.names.get(ref) or cc.get("displayName") or c.label(ref))
    if not groups:
        return F(None, "The campaign has no enabled ad group.")
    none = [g["name"] for g in groups.values() if not g["audiences"]]
    lines = "; ".join("%s: %s" % (g["name"], _list(sorted(set(g["audiences"])), 8) or "none") for g in groups.values())
    targeting = (c.camp.get("demandGenCampaignSettings") or {}).get("upgradedTargeting")
    tail = " Optimized targeting is %s." % ("ON" if targeting else "off") if targeting is not None else ""
    if none:
        return F("fail", "No audience on %s. %s.%s" % (_list(none), lines, tail))
    return F(None, "Audiences by ad group. %s.%s Whether they fit the business is read against the landing page." % (lines, tail))


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


def run(account_id, campaign_id, search, *, geo_fn=None, web_fn=None, book=None):
    """Read one campaign and run every rule on it. One small read first says
    whether the campaign is in this account and what type it is, so only the
    reads that type can answer are made."""
    if not ID_RE.match(str(campaign_id)):
        raise ValueError("That is not a campaign id")
    rows = search(account_id, "SELECT campaign.id, campaign.advertising_channel_type FROM campaign "
                              "WHERE campaign.id = %s" % campaign_id)
    if not rows:
        return {"data": {}, "findings": {}, "campaign": {"id": "", "name": "", "channel": "", "type": "", "dsa": False, "status": ""},
                "errors": {}}
    channel = (rows[0].get("campaign") or {}).get("advertisingChannelType") or ""
    use_book = paid_qa_book.applies_to(channel)
    data = gather(account_id, campaign_id, search, geo_fn=geo_fn, channel=channel, web_fn=web_fn, book=book if use_book else None)
    data["_bookNotUsed"] = "" if use_book else paid_qa_book.TYPE_NAMES.get(channel) or channel
    return {"data": data, "findings": evaluate(data), "campaign": campaign_summary(data), "errors": data.get("_errors") or {}}
