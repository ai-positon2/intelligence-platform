"""Paid Marketing Agents: QA Agent, what the agent reads from Google Ads.

Pinned here: the one rule that matters (the agent ticks or crosses a box only when the
answer is objective, and otherwise prints what it found and leaves the box to the
reviewer), each rule on rows made up for the purpose (nothing here is a real client's),
that only the reads a campaign's type can answer are made, that one refused read
cannot sink the rest while a spent quota or a sign-in failure stops the run, and that a
campaign id can never become anything but digits in a query.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_qa as qa  # noqa: E402
from tracker import paid_qa_auto as auto  # noqa: E402


def rows(**named):
    """gather()-shaped data from {name: rows}; everything not given is an empty answer."""
    names = ("customer", "billing", "recs", "campaign", "criteria", "shared", "labels", "conv", "camp_conv", "days",
             "camp_assets", "cust_assets", "adgroups", "ads", "keywords", "asset_groups", "asset_group_assets",
             "asset_group_signals")
    d = {n: [] for n in names}
    d.update(named)
    d.setdefault("_errors", {})
    d.setdefault("_channel", "SEARCH")
    d.setdefault("_geo", {})
    d.setdefault("_lang", {})
    return d


def campaign(**k):
    base = {"id": "1", "name": "Example_Search", "status": "ENABLED", "servingStatus": "SERVING", "advertisingChannelType": "SEARCH",
            "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False, "targetContentNetwork": False},
            "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE", "negativeGeoTargetType": "PRESENCE"}}
    base.update(k)
    return [{"campaign": base, "campaignBudget": {"amountMicros": "30000000", "explicitlyShared": False}}]


def customer(**k):
    return [{"customer": dict({"autoTaggingEnabled": True, "currencyCode": "USD", "timeZone": "America/New_York"}, **k)}]


def find(check, **data):
    return auto.evaluate(rows(**data))[check]


# ── The rules are the checklist's ───────────────────────────────────────────

def test_every_rule_belongs_to_a_real_check():
    ids = {i["id"] for i in qa.items()}
    assert set(auto.RULES) <= ids
    assert len(auto.RULES) >= 60


def test_every_rule_survives_an_empty_answer_and_says_so_instead_of_ticking():
    out = auto.evaluate({"_errors": {"customer": "refused"}, "_channel": "SEARCH"})
    assert set(out) == set(auto.RULES)
    for cid, f in out.items():
        assert f["result"] is None and f["found"], cid
    assert out["1.6"] == {"result": None, "found": "Could not read this from Google Ads (refused)."}      # never a tick on nothing


def test_a_rule_that_breaks_cannot_sink_the_others(monkeypatch):
    monkeypatch.setitem(auto.RULES, "1.6", lambda c: 1 / 0)
    out = auto.evaluate(rows(customer=customer(), campaign=campaign()))
    assert out["1.6"]["result"] is None and "could not be read automatically" in out["1.6"]["found"]
    assert out["1.4"]["found"].startswith("Time zone")


def test_the_agent_never_decides_what_depends_on_the_plan():
    out = auto.evaluate(rows(customer=customer(), campaign=campaign(biddingStrategyType="TARGET_CPA",
                                                                    targetCpa={"targetCpaMicros": "40000000"}),
                             criteria=[{"campaignCriterion": {"type": "LOCATION", "location": {"geoTargetConstant": "geoTargetConstants/1"}}}]))
    for cid in ("1.4", "3.1", "3.2", "3.4", "3.6", "3.7", "3.8", "3.11", "3.12", "3.18", "5.4"):
        assert out[cid]["result"] is None, cid
    assert "30.00 USD" in out["3.6"]["found"] and "target CPA 40.00 USD" in out["3.7"]["found"]


def test_nothing_printed_uses_an_em_dash():
    out = auto.evaluate(rows(customer=customer(), campaign=campaign()))
    assert not any("—" in f["found"] for f in out.values())


# ── Account and tracking ────────────────────────────────────────────────────

def test_auto_tagging_is_a_tick_when_on_and_a_cross_when_off():
    assert find("1.6", customer=customer())["result"] == "pass"
    f = find("1.6", customer=customer(autoTaggingEnabled=False))
    assert f["result"] == "fail" and "OFF" in f["found"]


def test_url_parameters_are_printed_and_ticked_when_set_at_either_level():
    f = find("1.7", customer=customer(trackingUrlTemplate="{lpurl}?utm_source=google"), campaign=campaign())
    assert f["result"] == "pass" and "utm_source=google" in f["found"]
    f = find("1.7", customer=customer(), campaign=campaign(finalUrlSuffix="utm_medium=cpc&gclid={gclid}",
                                                           urlCustomParameters=[{"key": "campaignname", "value": "Brand"}]))
    assert f["result"] == "pass" and "utm_medium=cpc" in f["found"] and "{_campaignname}=Brand" in f["found"]
    f = find("1.7", customer=customer(), campaign=campaign())
    assert f["result"] == "fail" and "none" in f["found"]                                   # missing at both levels


def test_campaign_level_tracking_needs_the_campaign_to_have_its_own_otherwise_it_only_prints():
    own = find("3.16", customer=customer(), campaign=campaign(trackingUrlTemplate="{lpurl}?utm_source=google"))
    assert own["result"] == "pass" and "Test in Google Ads" in own["found"]
    inherited = find("3.16", customer=customer(trackingUrlTemplate="{lpurl}?a=b"), campaign=campaign())
    assert inherited["result"] is None and "account's template applies" in inherited["found"]
    nothing = find("3.16", customer=customer(), campaign=campaign())
    assert nothing["result"] is None and "no account template either" in nothing["found"]


def test_auto_apply_is_off_only_when_no_recommendation_type_is_enabled():
    assert find("1.5")["result"] == "pass"
    on = find("1.5", recs=[{"recommendationSubscription": {"type": "KEYWORD", "status": "ENABLED"}},
                           {"recommendationSubscription": {"type": "SITELINK_ASSET", "status": "PAUSED"}}])
    assert on["result"] == "fail" and "KEYWORD" in on["found"] and "SITELINK" not in on["found"]


def test_billing_is_never_ticked_because_the_balance_cannot_be_read():
    ok = find("1.2", billing=[{"billingSetup": {"status": "APPROVED", "paymentsAccountInfo": {"paymentsAccountName": "Example Co"}}}])
    assert ok["result"] is None and "Balance or credit" in ok["found"]
    assert find("1.2", billing=[{"billingSetup": {"status": "CANCELLED"}}])["result"] == "fail"
    assert find("1.2")["result"] is None


def test_the_eu_political_ads_declaration_must_have_been_made():
    assert find("1.15", customer=customer(containsEuPoliticalAdvertising="DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"))["result"] == "pass"
    assert find("1.15", customer=customer(containsEuPoliticalAdvertising="UNSPECIFIED"))["result"] == "fail"
    assert find("1.15", customer=customer())["result"] == "fail"


def test_conversion_actions_are_listed_and_a_primary_one_that_is_off_is_a_cross():
    conv = [{"conversionAction": {"name": "Leads", "status": "ENABLED", "primaryForGoal": True, "countingType": "ONE_PER_CLICK"}},
            {"conversionAction": {"name": "Page view", "status": "ENABLED", "primaryForGoal": False}}]
    assert "1 primary conversion action: Leads" in find("2.2", conv=conv)["found"]
    assert find("2.3", conv=conv)["result"] is None and "1 enabled" in find("2.3", conv=conv)["found"]
    assert "Leads = one per click" in find("2.6", conv=conv)["found"]
    off = conv + [{"conversionAction": {"name": "Calls", "status": "HIDDEN", "primaryForGoal": True}}]
    assert find("2.3", conv=off)["result"] == "fail" and "Calls" in find("2.3", conv=off)["found"]


def test_live_conversions_pass_only_when_the_campaign_recorded_some():
    got = [{"segments": {"conversionActionName": "Leads"}, "metrics": {"conversions": 3}}]
    assert find("15.3", camp_conv=got)["result"] == "pass" and "Leads 3" in find("15.3", camp_conv=got)["found"]
    none = find("15.3", camp_conv=[])
    assert none["result"] is None and "No conversions" in none["found"]                      # a new campaign is not a failure


# ── Campaign settings ───────────────────────────────────────────────────────

def test_status_ticks_only_a_campaign_that_is_enabled_and_serving():
    assert find("3.3", campaign=campaign())["result"] == "pass"
    assert find("3.3", campaign=campaign(status="PAUSED"))["result"] == "fail"
    assert find("3.3", campaign=campaign(servingStatus="PENDING"))["result"] is None


def test_end_date_passes_with_none_and_is_printed_when_there_is_one():
    assert find("3.5", campaign=campaign())["result"] == "pass"
    assert find("3.5", campaign=campaign(endDateTime="2037-12-30 00:00:00"))["result"] == "pass"       # Google's "no end"
    f = find("3.5", campaign=campaign(endDateTime="2026-12-31 23:59:59"))
    assert f["result"] is None and "2026-12-31" in f["found"]


def test_the_location_options_are_exact_answers():
    assert find("3.9", campaign=campaign())["result"] == "pass" and find("3.10", campaign=campaign())["result"] == "pass"
    both = campaign(geoTargetTypeSetting={"positiveGeoTargetType": "PRESENCE_OR_INTEREST", "negativeGeoTargetType": "PRESENCE_OR_INTEREST"})
    assert find("3.9", campaign=both)["result"] == "fail" and find("3.10", campaign=both)["result"] == "fail"


def test_locations_are_named_and_the_exclusions_too():
    crit = [{"campaignCriterion": {"type": "LOCATION", "location": {"geoTargetConstant": "geoTargetConstants/10"}}},
            {"campaignCriterion": {"type": "LOCATION", "negative": True, "location": {"geoTargetConstant": "geoTargetConstants/11"}}}]
    d = rows(campaign=campaign(), criteria=crit)
    d["_geo"] = {"geoTargetConstants/10": {"name": "Annapolis", "canonical": "Annapolis,Maryland,United States"},
                 "geoTargetConstants/11": {"name": "Baltimore", "canonical": ""}}
    f = auto.evaluate(d)["3.8"]
    assert f["result"] is None and "1 location targeted: Annapolis,Maryland,United States" in f["found"] and "Excluded: Baltimore" in f["found"]


def test_languages_and_ad_schedule_are_printed():
    crit = [{"campaignCriterion": {"type": "LANGUAGE", "language": {"languageConstant": "languageConstants/1000"}}},
            {"campaignCriterion": {"type": "AD_SCHEDULE", "adSchedule": {"dayOfWeek": "MONDAY", "startHour": 8, "endHour": 18}}}]
    d = rows(campaign=campaign(), criteria=crit)
    d["_lang"] = {"1000": "English"}
    out = auto.evaluate(d)
    assert out["3.11"]["found"] == "Languages: English." and "Mon 8-18" in out["3.12"]["found"]
    assert "all languages" in find("3.11", campaign=campaign())["found"]


def test_search_campaign_network_and_ai_settings():
    assert find("4.1", campaign=campaign())["result"] == "pass" and find("4.2", campaign=campaign())["result"] == "pass"
    wide = campaign(networkSettings={"targetContentNetwork": True, "targetSearchNetwork": True})
    assert find("4.1", campaign=wide)["result"] == "fail" and find("4.2", campaign=wide)["result"] == "fail"
    assert find("4.3", campaign=campaign())["result"] == "pass"
    assert find("4.3", campaign=campaign(aiMaxSetting={"enableAiMax": True}))["result"] == "fail"


def test_automatically_created_assets_are_off_only_when_every_setting_is_opted_out():
    off = campaign(assetAutomationSettings=[{"assetAutomationType": "TEXT_ASSET_AUTOMATION", "assetAutomationStatus": "OPTED_OUT"},
                                            {"assetAutomationType": "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION", "assetAutomationStatus": "OPTED_OUT"}])
    assert find("4.4", campaign=off)["result"] == "pass" and find("8.1", campaign=off)["result"] == "pass"
    on = campaign(assetAutomationSettings=[{"assetAutomationType": "TEXT_ASSET_AUTOMATION", "assetAutomationStatus": "OPTED_IN"},
                                           {"assetAutomationType": "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION", "assetAutomationStatus": "OPTED_IN"}])
    a, b = find("4.4", campaign=on), find("8.1", campaign=on)
    assert a["result"] == "fail" and "text customization ON" in a["found"] and b["result"] == "fail" and "Final URL expansion is ON" in b["found"]
    assert find("4.4", campaign=campaign())["result"] is None                               # no setting returned: no tick


def test_negative_keywords_pass_with_a_list_or_campaign_negatives_and_fail_with_none():
    lst = [{"sharedSet": {"name": "Generic", "type": "NEGATIVE_KEYWORDS", "memberCount": "60"}, "campaignSharedSet": {"status": "ENABLED"}}]
    a = find("4.7", shared=lst)
    assert a["result"] == "pass" and "Generic (60)" in a["found"]
    own = [{"campaignCriterion": {"type": "KEYWORD", "negative": True}}]
    assert find("4.7", criteria=own)["result"] == "pass"
    assert find("4.7")["result"] == "fail" and find("8.4", shared=lst)["result"] == "pass"
    placement = [{"sharedSet": {"name": "Places", "type": "NEGATIVE_PLACEMENTS"}, "campaignSharedSet": {"status": "ENABLED"}}]
    assert find("4.7", shared=placement)["result"] == "fail"                                # a placement list is not a keyword list


# ── Ad groups, ads and keywords ─────────────────────────────────────────────

def ad_group(i="11", name="Brand", status="ENABLED", type_="SEARCH_STANDARD", **k):
    return {"adGroup": dict({"id": i, "name": name, "status": status, "type": type_}, **k)}


def rsa(group="11", headlines=15, descriptions=4, strength="GOOD", path1="a", path2="b", status="ENABLED", urls=("https://example.com/",),
        approval="APPROVED", pinned=0):
    heads = [{"text": "H%d" % i, **({"pinnedField": "HEADLINE_1"} if i < pinned else {})} for i in range(headlines)]
    r = {"headlines": heads, "descriptions": [{"text": "d"}] * descriptions}
    if path1:
        r["path1"] = path1
    if path2:
        r["path2"] = path2
    return {"adGroup": {"id": group}, "adGroupAd": {"status": status, "adStrength": strength, "policySummary": {"approvalStatus": approval},
                                                    "ad": {"type": "RESPONSIVE_SEARCH_AD", "finalUrls": list(urls), "responsiveSearchAd": r}}}


def kw(group="11", text="example", match="EXACT", status="ENABLED", negative=False, serving="ELIGIBLE", approval="APPROVED"):
    return {"adGroup": {"id": group}, "adGroupCriterion": {"status": status, "negative": negative, "keyword": {"text": text, "matchType": match},
                                                           "systemServingStatus": serving, "approvalStatus": approval}}


def test_every_ad_group_needs_an_enabled_ad_and_a_live_keyword():
    groups = [ad_group("11", "Brand"), ad_group("12", "Generic")]
    ok = auto.evaluate(rows(adgroups=groups, ads=[rsa("11"), rsa("12")], keywords=[kw("11"), kw("12")]))
    assert ok["5.1"]["result"] == "pass" and ok["5.2"]["result"] == "pass"
    bad = auto.evaluate(rows(adgroups=groups, ads=[rsa("11"), rsa("12", status="PAUSED")], keywords=[kw("11"), kw("12", status="PAUSED"), kw("12", negative=True)]))
    assert bad["5.1"]["result"] == "fail" and "Generic" in bad["5.1"]["found"]
    assert bad["5.2"]["result"] == "fail" and "Generic" in bad["5.2"]["found"]              # a paused or negative keyword is not coverage
    assert auto.evaluate(rows())["5.1"]["result"] == "fail"                                  # a campaign with no ad group at all


def test_dynamic_search_ad_groups_need_no_keywords():
    d = rows(adgroups=[ad_group("11", "DSA", type_="SEARCH_DYNAMIC_ADS")], ads=[rsa("11")], keywords=[])
    assert auto.evaluate(d)["5.2"]["result"] is None


def test_ad_copy_counts_pass_only_when_good_and_fail_below_the_minimum():
    g = [ad_group()]
    good = find("6.1", adgroups=g, ads=[rsa()])
    assert good["result"] == "pass" and "15 headlines, 4 descriptions, strength good" in good["found"]
    avg = find("6.1", adgroups=g, ads=[rsa(strength="AVERAGE")])
    assert avg["result"] is None and "below Good" in avg["found"]                            # printed, left to the reviewer
    thin = find("6.1", adgroups=g, ads=[rsa(headlines=2, descriptions=1)])
    assert thin["result"] == "fail" and "Below the minimum" in thin["found"]
    assert find("6.1", adgroups=g, ads=[])["result"] == "fail"


def test_final_urls_are_printed_and_http_is_a_cross():
    g = [ad_group()]
    ok = find("6.3", adgroups=g, ads=[rsa()])
    assert ok["result"] is None and "https://example.com/" in ok["found"] and "Open them" in ok["found"]   # loading is not checked
    assert find("6.3", adgroups=g, ads=[rsa(urls=("http://example.com/",))])["result"] == "fail"


def test_display_paths_and_pins():
    g = [ad_group()]
    assert find("6.5", adgroups=g, ads=[rsa()])["result"] == "pass"
    assert find("6.5", adgroups=g, ads=[rsa(path2="")])["result"] == "fail"
    assert "3 pinned assets across 1 enabled ads" in find("6.9", adgroups=g, ads=[rsa(pinned=3)])["found"]


def test_duplicate_keywords_across_ad_groups_are_a_cross_and_no_duplicates_is_not_a_tick():
    d = [kw("11", "Shoes", "EXACT"), kw("12", "shoes", "EXACT"), kw("12", "shoes", "PHRASE")]
    f = find("5.5", keywords=d)
    assert f["result"] == "fail" and "shoes [exact]" in f["found"] and "phrase" not in f["found"]
    clean = find("5.5", keywords=[kw("11", "a"), kw("12", "b")])
    assert clean["result"] is None and "Spelling" in clean["found"]


def test_keywords_rarely_shown_and_disapprovals():
    f = find("15.7", keywords=[kw(text="obscure term", serving="RARELY_SERVED"), kw(text="fine")])
    assert f["result"] == "fail" and "obscure term" in f["found"] and "fine" not in f["found"]
    assert find("15.7", keywords=[kw()])["result"] == "pass"
    dis = find("15.2", ads=[rsa(approval="DISAPPROVED")], keywords=[kw(approval="DISAPPROVED"), kw()])
    assert dis["result"] == "fail" and "1 ad and 1 keyword disapproved" in dis["found"]
    clean = find("15.2", ads=[rsa()], keywords=[kw()])
    assert clean["result"] is None and "Assets are not read" in clean["found"]               # only part of the check was seen


def test_ad_rotation_and_default_bids():
    assert find("4.6", adgroups=[ad_group(adRotationMode="OPTIMIZE")])["result"] == "pass"
    assert find("4.6", adgroups=[ad_group()])["result"] == "pass"                            # Google leaves the default out
    assert find("4.6", adgroups=[ad_group(adRotationMode="ROTATE_FOREVER")])["result"] == "fail"
    assert "automated" in find("5.6", campaign=campaign(biddingStrategyType="MAXIMIZE_CONVERSIONS"), adgroups=[ad_group()])["found"]
    manual = find("5.6", campaign=campaign(biddingStrategyType="MANUAL_CPC"), adgroups=[ad_group(cpcBidMicros="2000000")])
    assert "Brand 2.00" in manual["found"]


# ── Assets ──────────────────────────────────────────────────────────────────

def asset(level, field, status="ENABLED"):
    if level == "campaign":
        return {"campaignAsset": {"fieldType": field, "status": status}, "asset": {"type": field}}
    return {"customerAsset": {"fieldType": field, "status": status}, "asset": {"type": field}}


def test_sitelinks_callouts_and_snippets_are_counted_with_the_campaigns_overriding_the_accounts():
    camp = [asset("campaign", "SITELINK")] * 4
    acct = [asset("account", "SITELINK")] * 2 + [asset("account", "CALLOUT")] * 5 + [asset("account", "STRUCTURED_SNIPPET")]
    f = find("7.1", camp_assets=camp, cust_assets=acct)
    assert f["result"] == "pass" and "4 on the campaign, 2 on the account" in f["found"]
    assert find("7.1", camp_assets=[asset("campaign", "SITELINK")] * 3, cust_assets=acct)["result"] == "fail"     # 3 on the campaign overrides
    assert find("7.1", cust_assets=[asset("account", "SITELINK")] * 4)["result"] == "pass"                         # inherited from the account
    assert find("7.2", cust_assets=acct)["result"] == "pass" and find("7.3", cust_assets=acct)["result"] == "pass"
    assert find("7.1", camp_assets=[asset("campaign", "SITELINK", status="PAUSED")] * 4)["result"] == "fail"      # paused does not count


def test_business_name_and_logo_pass_only_with_both():
    both = [asset("campaign", "BUSINESS_NAME"), asset("campaign", "LOGO")]
    assert find("7.4", camp_assets=both)["result"] == "pass" and find("8.12", camp_assets=both)["result"] == "pass"
    one = find("7.4", camp_assets=[asset("campaign", "BUSINESS_NAME")])
    assert one["result"] is None and "Logo: not set" in one["found"]


def test_optional_assets_are_ticked_when_present_and_only_printed_when_not():
    assert find("7.6", camp_assets=[asset("campaign", "CALL")])["result"] == "pass"
    assert find("7.6")["result"] is None and find("7.9")["result"] is None and find("7.8")["result"] is None


# ── Performance Max ─────────────────────────────────────────────────────────

def group(i="g1", name="Design"):
    return {"assetGroup": {"id": i, "name": name, "status": "ENABLED", "finalUrls": ["https://example.com/design/"]}}


def group_assets(i="g1", headline=15, long=5, desc=5, land=5, square=5, video=5):
    out = []
    for field, n in (("HEADLINE", headline), ("LONG_HEADLINE", long), ("DESCRIPTION", desc), ("MARKETING_IMAGE", land),
                     ("SQUARE_MARKETING_IMAGE", square), ("YOUTUBE_VIDEO", video)):
        out += [{"assetGroup": {"id": i}, "assetGroupAsset": {"fieldType": field, "status": "ENABLED"}} for _ in range(n)]
    return out


def pmax(**data):
    return rows(_channel="PERFORMANCE_MAX", campaign=campaign(advertisingChannelType="PERFORMANCE_MAX"), **data)


def test_asset_group_minimums_video_count_and_audience_signals():
    logo = [asset("campaign", "LOGO")]
    out = auto.evaluate(pmax(asset_groups=[group()], asset_group_assets=group_assets(),
                             asset_group_signals=[{"assetGroup": {"id": "g1"}, "assetGroupSignal": {"audience": {"audience": "customers/1/audiences/2"}}}],
                             camp_assets=logo))
    assert out["8.7"]["result"] == "pass" and out["8.8"]["result"] == "pass" and out["8.9"]["result"] == "pass"
    thin = auto.evaluate(pmax(asset_groups=[group()], asset_group_assets=group_assets(headline=2, square=0, video=1), camp_assets=logo))
    assert thin["8.7"]["result"] == "fail" and "headlines 2 of 3" in thin["8.7"]["found"] and "square images 0 of 1" in thin["8.7"]["found"]
    assert thin["8.8"]["result"] == "fail" and thin["8.9"]["result"] == "fail"
    assert auto.evaluate(pmax(asset_groups=[group()], asset_group_assets=group_assets()))["8.7"]["result"] is None   # no logo found: not ticked


def test_a_removed_asset_groups_leftover_assets_are_ignored():
    # Google can still return asset rows of a removed group; only groups the campaign has count.
    leftovers = group_assets("gone", headline=30)
    out = auto.evaluate(pmax(asset_groups=[group()], asset_group_assets=group_assets() + leftovers, camp_assets=[asset("campaign", "LOGO")]))
    assert "30" not in out["8.7"]["found"] and out["8.7"]["result"] == "pass"
    themes = [{"assetGroup": {"id": "gone"}, "assetGroupSignal": {"searchTheme": {"text": "stale theme"}}},
              {"assetGroup": {"id": "g1"}, "assetGroupSignal": {"searchTheme": {"text": "fresh theme"}}}]
    f = auto.evaluate(pmax(asset_groups=[group()], asset_group_signals=themes))["8.10"]
    assert "fresh theme" in f["found"] and "stale theme" not in f["found"]


def test_a_campaign_with_no_asset_group_is_a_cross():
    assert auto.evaluate(pmax())["8.7"]["result"] == "fail"


def test_merchant_center_must_be_linked_on_a_shopping_style_campaign():
    linked = campaign(shoppingSetting={"merchantId": "12345", "feedLabel": "US"})
    f = find("8.5", campaign=linked)
    assert f["result"] == "pass" and "12345" in f["found"] and "feed label US" in f["found"]
    assert find("8.5", campaign=campaign())["result"] == "fail"


# ── Pacing ──────────────────────────────────────────────────────────────────

def test_spend_pacing_prints_yesterday_against_the_daily_budget():
    days = [{"segments": {"date": "2026-10-12"}, "metrics": {"costMicros": "1000000"}},
            {"segments": {"date": "2026-10-13"}, "metrics": {"costMicros": "7500000"}}]
    f = find("15.4", campaign=campaign(), days=days)
    assert f["result"] is None and "2026-10-13" in f["found"] and "7.50 " in f["found"] and "25%" in f["found"]


# ── Reading a campaign ──────────────────────────────────────────────────────

class Google:
    """search(customer_id, gaql) that records what it was asked and can refuse some reads."""
    def __init__(self, refuse=None, error=None):
        self.queries, self.refuse, self.error = [], refuse or {}, error

    def __call__(self, customer_id, gaql):
        self.queries.append(gaql)
        for needle, exc in self.refuse.items():
            if needle in gaql:
                raise exc
        if "advertising_channel_type FROM campaign WHERE campaign.id" in gaql:
            return [{"campaign": {"id": "1", "advertisingChannelType": self.error or "SEARCH"}}]
        return []


class Refused(RuntimeError):
    def __init__(self, kind, msg=""):
        self.kind = kind
        super().__init__(msg)


def tables(g):
    import re
    return {m.group(1) for q in g.queries for m in [re.search(r"\bFROM (\w+)", q)] if m}


def test_only_the_reads_a_campaign_type_can_answer_are_made():
    for channel, present, absent in (("SEARCH", {"ad_group", "ad_group_ad", "ad_group_criterion"}, {"asset_group"}),
                                     ("PERFORMANCE_MAX", {"asset_group", "asset_group_asset", "asset_group_signal"}, {"ad_group", "ad_group_ad"}),
                                     ("DEMAND_GEN", set(), {"ad_group", "asset_group", "ad_group_criterion"})):
        g = Google(error=channel)
        out = auto.run("2133861213", "1", g)
        t = tables(g)
        assert present <= t and not (absent & t), (channel, t)
        assert out["campaign"]["channel"] == channel
    assert len(Google().queries) == 0


def test_a_read_costs_about_eighteen_calls_for_a_search_campaign():
    g = Google()
    auto.run("2133861213", "1", g)
    assert 14 <= len(g.queries) <= 22


def test_one_refused_read_is_noted_and_the_rest_still_run():
    g = Google(refuse={"FROM billing_setup": Refused("", "400 unrecognized field")})
    out = auto.run("2133861213", "1", g)
    assert "billing" in out["errors"] and "400 unrecognized field" in out["errors"]["billing"]
    assert out["findings"]["1.2"]["found"].startswith("Could not read this from Google Ads (400")
    assert "1.4" in out["findings"]


@pytest.mark.parametrize("kind", ["quota", "auth", "not_configured", "permission"])
def test_a_spent_quota_or_a_sign_in_failure_stops_the_run(kind):
    g = Google(refuse={"FROM customer": Refused(kind, "stop")})
    with pytest.raises(Refused):
        auto.run("2133861213", "1", g)


def test_a_campaign_not_in_the_account_comes_back_empty_without_reading_more():
    class Empty(Google):
        def __call__(self, cid, gaql):
            self.queries.append(gaql)
            return []
    g = Empty()
    out = auto.run("2133861213", "1", g)
    assert out["campaign"]["id"] == "" and out["findings"] == {} and len(g.queries) == 1


@pytest.mark.parametrize("bad", ["1 OR 1=1", "1; DROP", "abc", "", "1'", "12345678901234567890123"])
def test_a_campaign_id_can_only_be_digits_in_a_query(bad):
    g = Google()
    with pytest.raises(ValueError):
        auto.run("2133861213", bad, g)
    with pytest.raises(ValueError):
        auto.gather("2133861213", bad, g)
    assert g.queries == []


def test_the_live_campaign_list_is_enabled_campaigns_by_name_with_their_type():
    class Camps(Google):
        def __call__(self, cid, gaql):
            self.queries.append(gaql)
            return [{"campaign": {"id": "2", "name": "b_pmax", "advertisingChannelType": "PERFORMANCE_MAX"}},
                    {"campaign": {"id": "1", "name": "A_brand", "advertisingChannelType": "SEARCH"}},
                    {"campaign": {"id": "3", "name": "x", "advertisingChannelType": "SMART"}}]
    g = Camps()
    out = auto.list_campaigns("2133861213", g)
    assert [c["name"] for c in out] == ["A_brand", "b_pmax", "x"] and [c["type"] for c in out] == ["Search", "PMax", ""]
    assert "campaign.status = 'ENABLED'" in g.queries[0] and "ORDER BY campaign.name" in g.queries[0]


def test_a_found_text_is_kept_to_a_readable_length():
    f = auto.Finding("pass", "word " * 1000)
    assert len(f["found"]) <= auto.MAX_FOUND and f["found"].endswith("...")
    assert auto.Finding(None, "a\n  b\t c")["found"] == "a b c"
