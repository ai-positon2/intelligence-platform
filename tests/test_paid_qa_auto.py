"""Paid Marketing Agents: QA Agent, what Google Ads can answer exactly.

Pinned here: the one rule that matters (a rule ticks or crosses a box only when the answer is
exact, and otherwise prints what it found), each rule on rows made up for the purpose (nothing
here is a real client's), what the campaign book changes (and that a placeholder is never an
answer), which switches the API cannot show and so are never ticked, that only the reads a
campaign's type can answer are made, that one refused read cannot sink the rest while a spent
quota or a sign-in failure stops the run, and that a campaign id can never become anything but
digits in a query.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_qa as qa  # noqa: E402
from tracker import paid_qa_auto as auto  # noqa: E402
from tracker import paid_qa_book  # noqa: E402

NAMES = ("customer", "recs", "campaign", "criteria", "shared", "shared_all", "goals", "cust_goals", "camp_assets", "cust_assets",
         "adgroups", "ads", "ag_criteria", "asset_groups", "asset_group_assets")


def rows(**named):
    """gather()-shaped data from {name: rows}; everything not given is an empty answer."""
    d = {n: [] for n in NAMES}
    d.update(named)
    d.setdefault("_errors", {})
    d.setdefault("_channel", "SEARCH")
    d.setdefault("_geo", {})
    d.setdefault("_lang", {})
    d.setdefault("_pages", [])
    d.setdefault("_book", None)
    return d


def campaign(**k):
    base = {"id": "1", "name": "P2_Search_20261014", "status": "ENABLED", "servingStatus": "SERVING", "advertisingChannelType": "SEARCH",
            "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False, "targetContentNetwork": False,
                                "targetYoutube": False, "targetGoogleTvNetwork": False},
            "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE", "negativeGeoTargetType": "PRESENCE"},
            "biddingStrategyType": "MAXIMIZE_CONVERSIONS", "startDateTime": "2026-10-14 09:00:00"}
    base.update(k)
    return [{"campaign": base, "campaignBudget": {"amountMicros": "30000000", "explicitlyShared": False}}]


def customer(**k):
    return [{"customer": dict({"autoTaggingEnabled": True, "currencyCode": "USD", "timeZone": "America/New_York",
                               "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"}, **k)}]


def find(check, **data):
    return auto.evaluate(rows(**data))[check]


def book_of(**fields):
    return {"filename": "book.xlsx", "rows": [{"field": k.replace("_", " "), "value": v, "other": ""} for k, v in fields.items()], "text": ""}


# ── The rules are the checklist's ───────────────────────────────────────────

def test_every_check_in_the_campaign_book_has_a_rule_and_every_rule_a_check():
    ids = {i["id"] for i in qa.items()}
    assert set(auto.RULES) == ids and len(ids) == 65


def test_every_rule_survives_an_empty_answer_and_says_so_instead_of_ticking():
    out = auto.evaluate({"_errors": {"customer": "refused"}, "_channel": "SEARCH"})
    assert set(out) == set(auto.RULES) - {"6.1", "6.2", "6.3", "6.4"}                       # the on-the-spot checks are not a Search campaign's
    for cid, f in out.items():
        assert f["found"], cid
    assert out["1.20"] == {"result": None, "found": "Could not read this from Google Ads (refused)."}      # never a tick on nothing


def test_a_rule_that_breaks_cannot_sink_the_others(monkeypatch):
    monkeypatch.setitem(auto.RULES, "1.5", lambda c: 1 / 0)
    out = auto.evaluate(rows(customer=customer(), campaign=campaign()))
    assert out["1.5"]["result"] is None and "could not be read automatically" in out["1.5"]["found"]
    assert out["1.21"]["result"] == "pass"


def test_the_agent_never_decides_what_depends_on_the_campaign_book():
    out = auto.evaluate(rows(customer=customer(), campaign=campaign(),
                             criteria=[{"campaignCriterion": {"type": "LOCATION", "location": {"geoTargetConstant": "geoTargetConstants/1"}}}]))
    for cid in ("1.2", "1.8", "1.10", "1.11", "1.12", "1.14", "1.16", "1.24"):
        assert out[cid]["result"] is None, cid
    assert "campaign book names no bid strategy" in out["1.8"]["found"]


def test_nothing_printed_uses_an_em_dash():
    out = auto.evaluate(rows(customer=customer(), campaign=campaign()))
    assert not any("—" in f["found"] for f in out.values())


# ── 1. Campaign setup ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name,channel,result", [
    ("P2_Search_20261014", "SEARCH", "pass"), ("P2_PMax_2026-10-14", "PERFORMANCE_MAX", "pass"), ("p2_pmax_14102026", "PERFORMANCE_MAX", "pass"),
    ("P2_Brand_McH_Set-A", "SEARCH", "fail"), ("Search_20261014", "SEARCH", "fail"), ("P2_PMax_20261014", "SEARCH", "fail"),
    ("P2_Search_20261014", "PERFORMANCE_MAX", "fail"), ("P2_Search_", "SEARCH", "fail"), ("P2_Search_launch", "SEARCH", "fail")])
def test_the_campaign_name_must_be_p2_type_and_a_launch_date(name, channel, result):
    f = find("1.1", campaign=campaign(name=name, advertisingChannelType=channel), _channel=channel)
    assert f["result"] == result and name in f["found"]


def test_campaign_type_is_compared_with_the_book_only_when_the_book_names_one():
    assert find("1.2", campaign=campaign())["result"] is None
    assert find("1.2", campaign=campaign(), _book=book_of(campaign_type="Search"))["result"] == "pass"
    assert find("1.2", campaign=campaign(), _book=book_of(campaign_type="Performance Max"))["result"] == "fail"
    pm = campaign(advertisingChannelType="PERFORMANCE_MAX")
    assert find("1.2", campaign=pm, _channel="PERFORMANCE_MAX", _book=book_of(campaign_type="PMax"))["result"] == "pass"
    assert find("1.2", campaign=campaign(), _book=book_of(campaign_type="Depends on the campaign book"))["result"] is None


def test_every_extra_network_must_be_off():
    assert find("1.3", campaign=campaign())["result"] == "pass"
    wide = campaign(networkSettings={"targetSearchNetwork": True, "targetContentNetwork": True, "targetYoutube": True, "targetGoogleTvNetwork": True})
    f = find("1.3", campaign=wide)
    assert f["result"] == "fail" and all(x in f["found"] for x in ("Search partners", "Display network", "YouTube", "Google TV"))
    assert find("1.3", campaign=campaign(networkSettings={"targetContentNetwork": True}))["found"].count("Display") == 1


def test_a_search_campaign_needs_an_enabled_ad_and_a_pmax_one_an_asset_group():
    assert find("1.4")["result"] == "fail" and "No enabled ad" in find("1.4")["found"]
    assert find("1.4", ads=[rsa()], adgroups=[ad_group()])["result"] is None                  # spelling and wording are the AI's
    assert find("1.4", ads=[rsa(status="PAUSED")], adgroups=[ad_group()])["result"] == "fail"
    assert find("1.4", _channel="PERFORMANCE_MAX", asset_groups=[group()])["result"] is None
    assert find("1.4", _channel="PERFORMANCE_MAX")["result"] == "fail"


def test_ai_max_must_be_off():
    assert find("1.5", campaign=campaign())["result"] == "pass"
    assert find("1.5", campaign=campaign(aiMaxSetting={"enableAiMax": False}))["result"] == "pass"
    assert find("1.5", campaign=campaign(aiMaxSetting={"enableAiMax": True}))["result"] == "fail"


def goal(cat, origin, biddable, kind):
    return {kind: {"category": cat, "origin": origin, "biddable": biddable}}


def goals(*pairs, kind="campaignConversionGoal"):
    return [goal(c, o, b, kind) for c, o, b in pairs]


def test_the_campaign_must_use_the_account_level_default_goals():
    same = [("PURCHASE", "WEBSITE", True), ("PAGE_VIEW", "WEBSITE", False)]
    ok = find("1.7", goals=goals(*same), cust_goals=goals(*same, kind="customerConversionGoal"))
    assert ok["result"] == "pass" and "PURCHASE/WEBSITE" in ok["found"] and "PAGE_VIEW" not in ok["found"]
    changed = [("PURCHASE", "WEBSITE", False), ("PAGE_VIEW", "WEBSITE", False)]
    bad = find("1.7", goals=goals(*changed), cust_goals=goals(*same, kind="customerConversionGoal"))
    assert bad["result"] == "fail" and "PURCHASE/WEBSITE is optimised on the account but not optimised on the campaign" in bad["found"]
    assert find("1.7")["result"] is None                                                       # no goals came back: no tick


def test_optimization_goal_is_conversions_on_the_default_goals():
    same = [("PURCHASE", "WEBSITE", True)]
    kw = dict(goals=goals(*same), cust_goals=goals(*same, kind="customerConversionGoal"))
    assert find("1.9", campaign=campaign(biddingStrategyType="MAXIMIZE_CONVERSIONS"), **kw)["result"] == "pass"
    assert find("1.9", campaign=campaign(biddingStrategyType="TARGET_CPA"), **kw)["result"] == "pass"
    assert find("1.9", campaign=campaign(biddingStrategyType="TARGET_SPEND"), **kw)["result"] == "fail"       # maximize clicks
    assert find("1.9", campaign=campaign(biddingStrategyType="MANUAL_CPC"), **kw)["result"] == "fail"
    other = find("1.9", campaign=campaign(), goals=goals(("PURCHASE", "WEBSITE", False)), cust_goals=goals(*same, kind="customerConversionGoal"))
    assert other["result"] is None                                                             # conversion-based, but goals differ: 1.7 says so


@pytest.mark.parametrize("book,strategy,extra,result", [
    ("Maximize conversions", "MAXIMIZE_CONVERSIONS", {}, "pass"), ("maximise conversion value", "MAXIMIZE_CONVERSION_VALUE", {}, "pass"),
    ("Target CPA", "TARGET_CPA", {}, "pass"), ("tCPA", "MAXIMIZE_CONVERSIONS", {"maximizeConversions": {"targetCpaMicros": "40000000"}}, "pass"),
    ("Target ROAS", "MAXIMIZE_CONVERSION_VALUE", {"maximizeConversionValue": {"targetRoas": 4.0}}, "pass"),
    ("Maximize clicks", "TARGET_SPEND", {}, "pass"), ("Manual CPC", "MANUAL_CPC", {}, "pass"),
    ("Target CPA", "MAXIMIZE_CONVERSIONS", {}, "fail"), ("Maximize conversion value", "MAXIMIZE_CONVERSIONS", {}, "fail"),
    ("Maximize clicks", "MAXIMIZE_CONVERSIONS", {}, "fail"), ("Maximize conversions with a target CPA", "MAXIMIZE_CONVERSIONS", {}, "fail"),
    ("Maximize conversions with a target CPA", "MAXIMIZE_CONVERSIONS", {"maximizeConversions": {"targetCpaMicros": "9000000"}}, "pass")])
def test_the_bid_strategy_is_compared_with_the_book_in_the_books_own_words(book, strategy, extra, result):
    f = find("1.8", campaign=campaign(biddingStrategyType=strategy, **extra), _book=book_of(bid_strategy_type=book))
    assert f["result"] == result and book in f["found"]


def test_devices_and_demographics_are_printed_for_the_ai_and_the_book():
    crit = [{"campaignCriterion": {"type": "DEVICE", "device": {"type": "DESKTOP"}}},
            {"campaignCriterion": {"type": "DEVICE", "device": {"type": "TABLET"}, "bidModifier": 0}},
            {"campaignCriterion": {"type": "DEVICE", "device": {"type": "MOBILE"}, "bidModifier": 0.8}},
            {"campaignCriterion": {"type": "GENDER", "gender": {"type": "FEMALE"}}},
            {"campaignCriterion": {"type": "AGE_RANGE", "negative": True, "ageRange": {"type": "AGE_RANGE_18_24"}}}]
    ag = [{"adGroup": {"name": "Brand"}, "adGroupCriterion": {"type": "AGE_RANGE", "status": "ENABLED", "ageRange": {"type": "AGE_RANGE_25_34"}}},
          {"adGroup": {"name": "Brand"}, "adGroupCriterion": {"type": "INCOME_RANGE", "status": "ENABLED", "negative": True, "incomeRange": {"type": "INCOME_RANGE_0_50"}}}]
    out = auto.evaluate(rows(campaign=campaign(), criteria=crit, ag_criteria=ag))
    assert out["1.10"]["found"] == "Devices: desktop all traffic, tablet excluded (-100%), mobile bid -20%."
    assert out["1.11"]["found"] == "Genders: female; excluded: none."
    assert out["1.16"]["found"] == "Ages targeted: 25-34; excluded: 18-24."
    assert out["1.12"]["found"] == "Household income: no income setting, so all; excluded: 0-50."
    for cid in ("1.10", "1.11", "1.12", "1.16"):
        assert out[cid]["result"] is None


def test_no_audience_may_be_excluded_on_the_campaign_or_its_ad_groups():
    assert find("1.13", ag_criteria=[])["result"] == "pass"
    camp = [{"campaignCriterion": {"type": "USER_LIST", "negative": True}}]
    ag = [{"adGroup": {"name": "Brand"}, "adGroupCriterion": {"type": "USER_INTEREST", "negative": True}}]
    f = find("1.13", criteria=camp, ag_criteria=ag)
    assert f["result"] == "fail" and "2 audience exclusions" in f["found"] and "Brand" in f["found"] and "user list on campaign" in f["found"]
    kept = [{"campaignCriterion": {"type": "USER_LIST", "negative": False}}]
    assert find("1.13", criteria=kept)["result"] == "pass"                                     # an included audience is not an exclusion
    unread = rows(ag_criteria=None)
    assert auto.evaluate(unread)["1.13"]["result"] is None                                     # ad groups not read: no tick


def lang(*ids):
    return [{"campaignCriterion": {"type": "LANGUAGE", "language": {"languageConstant": "languageConstants/%s" % i}}} for i in ids]


def test_language_targeting_must_be_english_only():
    d = rows(campaign=campaign(), criteria=lang("1000"))
    d["_lang"] = {"1000": "English"}
    assert auto.evaluate(d)["1.15"]["result"] == "pass"
    d = rows(campaign=campaign(), criteria=lang("1000", "1003"))
    d["_lang"] = {"1000": "English", "1003": "Spanish"}
    f = auto.evaluate(d)["1.15"]
    assert f["result"] == "fail" and "English, Spanish" in f["found"]
    d = rows(campaign=campaign(), criteria=lang("1003"))
    d["_lang"] = {"1003": "Spanish"}
    assert auto.evaluate(d)["1.15"]["result"] == "fail"
    none = find("1.15", campaign=campaign())
    assert none["result"] == "fail" and "all languages" in none["found"]
    d = rows(campaign=campaign(), criteria=lang("1000"))
    d["_lang"] = {}                                              # the language names could not be looked up: English is still English
    assert auto.evaluate(d)["1.15"]["result"] == "pass"


def test_dynamic_keyword_insertion_is_found_in_headlines_descriptions_and_paths():
    g = [ad_group()]
    ok = find("1.17", adgroups=g, ads=[rsa()])
    assert ok["result"] == "pass" and "No dynamic keyword insertion" in ok["found"]
    for where in ("headline", "description", "path"):
        ad = rsa(inject=(where, "Buy {KeyWord:Shoes} today"))
        f = find("1.17", adgroups=g, ads=[ad])
        assert f["result"] == "fail" and "{KeyWord:Shoes}" in f["found"], where
    assert find("1.17", adgroups=g, ads=[rsa(inject=("headline", "Great {keyword: shoes}"))])["result"] == "fail"
    assert find("1.17", _channel="PERFORMANCE_MAX")["result"] == "pass"
    assert find("1.17", adgroups=g, ads=[])["result"] is None                                  # no ad text to scan


def test_a_negative_keyword_list_must_be_on_the_campaign():
    lst = [{"sharedSet": {"name": "Generic", "type": "NEGATIVE_KEYWORDS", "memberCount": "60"}, "campaignSharedSet": {"status": "ENABLED"}}]
    acct = [{"sharedSet": {"name": "Generic", "type": "NEGATIVE_KEYWORDS", "memberCount": "60"}},
            {"sharedSet": {"name": "Competitors", "type": "NEGATIVE_KEYWORDS", "memberCount": "300"}},
            {"sharedSet": {"name": "Places", "type": "NEGATIVE_PLACEMENTS", "memberCount": "5"}}]
    f = find("1.18", shared=lst, shared_all=acct)
    assert f["result"] == "pass" and "Generic (60 terms)" in f["found"] and "not on this campaign: Competitors" in f["found"]
    assert find("1.18", shared_all=acct)["result"] == "fail"                                   # lists exist but none is on the campaign
    own = [{"campaignCriterion": {"type": "KEYWORD", "negative": True}}]
    assert find("1.18", criteria=own)["result"] == "fail"                                      # the book wants a list, not loose negatives
    placement = [{"sharedSet": {"name": "Places", "type": "NEGATIVE_PLACEMENTS"}, "campaignSharedSet": {"status": "ENABLED"}}]
    assert find("1.18", shared=placement)["result"] == "fail"                                  # a placement list is not a keyword list
    paused = [{"sharedSet": {"name": "Generic", "type": "NEGATIVE_KEYWORDS"}, "campaignSharedSet": {"status": "REMOVED"}}]
    assert find("1.18", shared=paused)["result"] == "fail"
    acct_level = [{"sharedSet": {"name": "Account list", "type": "ACCOUNT_LEVEL_NEGATIVE_KEYWORDS", "memberCount": "40"}, "campaignSharedSet": {"status": "ENABLED"}}]
    assert find("1.18", shared=acct_level)["result"] == "pass"


def test_eu_political_ads_must_be_declared_as_off():
    assert find("1.19", customer=customer())["result"] == "pass"
    assert find("1.19", customer=customer(containsEuPoliticalAdvertising="CONTAINS_EU_POLITICAL_ADVERTISING"))["result"] == "fail"
    for v in ("UNSPECIFIED", None):
        f = find("1.19", customer=customer(containsEuPoliticalAdvertising=v))
        assert f["result"] == "fail" and "not been made" in f["found"]


def test_url_parameters_are_printed_and_ticked_when_set_at_either_level():
    f = find("1.20", customer=customer(trackingUrlTemplate="{lpurl}?utm_source=google"), campaign=campaign())
    assert f["result"] == "pass" and "utm_source=google" in f["found"]
    f = find("1.20", customer=customer(), campaign=campaign(finalUrlSuffix="utm_medium=cpc&gclid={gclid}",
                                                           urlCustomParameters=[{"key": "campaignname", "value": "Brand"}]))
    assert f["result"] == "pass" and "utm_medium=cpc" in f["found"] and "{_campaignname}=Brand" in f["found"]
    f = find("1.20", customer=customer(), campaign=campaign())
    assert f["result"] == "fail" and "none" in f["found"]                                      # missing at both levels


def test_the_location_option_must_be_presence():
    assert find("1.21", campaign=campaign())["result"] == "pass"
    both = campaign(geoTargetTypeSetting={"positiveGeoTargetType": "PRESENCE_OR_INTEREST", "negativeGeoTargetType": "PRESENCE"})
    f = find("1.21", campaign=both)
    assert f["result"] == "fail" and "Presence or interest" in f["found"]


def test_there_must_be_no_end_date_and_the_start_date_is_printed():
    f = find("1.22", campaign=campaign())
    assert f["result"] is None and "Start date 2026-10-14; no end date" in f["found"]
    assert find("1.22", campaign=campaign(endDateTime="2037-12-30 00:00:00"))["result"] is None          # Google's "no end"
    ends = find("1.22", campaign=campaign(endDateTime="2026-12-31 23:59:59"))
    assert ends["result"] == "fail" and "2026-12-31" in ends["found"]


def test_ad_rotation_must_be_optimize_and_the_schedule_is_printed():
    assert find("1.23", adgroups=[ad_group(adRotationMode="OPTIMIZE")])["result"] == "pass"
    assert find("1.23", adgroups=[ad_group()])["result"] == "pass"                            # Google leaves the default out
    assert find("1.23", adgroups=[ad_group(adRotationMode="ROTATE_FOREVER")])["result"] == "fail"
    sched = [{"campaignCriterion": {"type": "AD_SCHEDULE", "adSchedule": {"dayOfWeek": "MONDAY", "startHour": 8, "endHour": 18}}}]
    f = find("1.24", criteria=sched)
    assert f["result"] is None and "Mon 8-18" in f["found"]
    assert "none (runs at all hours)" in find("1.24")["found"]


def test_final_urls_must_be_https_and_load():
    g = dict(adgroups=[ad_group()], ads=[rsa()])
    ok = find("1.25", _pages=[page()], **g)
    assert ok["result"] == "pass" and "All HTTPS and all load" in ok["found"]
    assert find("1.25", _pages=[page(status=404)], **g)["result"] == "fail"
    assert find("1.25", _pages=[page(status=500)], **g)["result"] == "fail" and find("1.25", _pages=[page(status=410)], **g)["result"] == "fail"
    for refused in (page(status=403), page(status=429), page(status=503), page(status=401), page(error="TimeoutError: slow", status=None)):
        f = find("1.25", _pages=[refused], **g)                  # our visit was refused or timed out: that says nothing about the people the ad sends
        assert f["result"] is None and "could not be confirmed to load" in f["found"], refused
    assert find("1.25", _pages=[page(status=403), page(status=404)], **g)["result"] == "fail"       # one page that is really gone still fails the box
    assert find("1.25", _pages=[], adgroups=[ad_group()], ads=[rsa(urls=("http://example.com/",))])["result"] == "fail"
    none = find("1.25", **g)
    assert none["result"] is None and "could not be opened" in none["found"]                  # no page evidence: not ticked
    few = find("1.25", _pages=[page()], adgroups=[ad_group()], ads=[rsa(urls=("https://a.com/", "https://b.com/"))])
    assert few["result"] is None and "1 of 2" in few["found"]
    assert find("1.25", _channel="PERFORMANCE_MAX", asset_groups=[group()], _pages=[page()])["result"] == "pass"


# ── 2. Automation ───────────────────────────────────────────────────────────

def automation(**k):
    return campaign(assetAutomationSettings=[{"assetAutomationType": t, "assetAutomationStatus": s} for t, s in k.items()])


def test_final_url_expansion_and_asset_automation_must_be_off():
    off = automation(TEXT_ASSET_AUTOMATION="OPTED_OUT", FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION="OPTED_OUT")
    assert find("2.1", campaign=off)["result"] == "pass" and find("2.2", campaign=off)["result"] == "pass"
    on = automation(TEXT_ASSET_AUTOMATION="OPTED_IN", FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION="OPTED_IN")
    a, b = find("2.1", campaign=on), find("2.2", campaign=on)
    assert a["result"] == "fail" and "Final URL expansion is ON" in a["found"]
    assert b["result"] == "fail" and "text customization ON" in b["found"]
    assert find("2.2", campaign=campaign())["result"] is None                                  # no setting returned: no tick


def test_final_url_expansion_is_not_in_use_on_a_search_campaign_with_ai_max_off():
    f = find("2.1", campaign=campaign(aiMaxSetting={"enableAiMax": False}))
    assert f["result"] == "pass" and "AI Max is off" in f["found"]
    assert find("2.1", campaign=campaign(aiMaxSetting={"enableAiMax": True}))["result"] is None
    assert find("2.1", _channel="PERFORMANCE_MAX", campaign=campaign(advertisingChannelType="PERFORMANCE_MAX"))["result"] is None


# ── 3. Automatically created assets: the switches are not in the API ────────

def made(field, level="campaign", source="AUTOMATICALLY_CREATED"):
    if level == "campaign":
        return {"campaignAsset": {"fieldType": field, "status": "ENABLED", "source": source}, "asset": {"type": field}}
    return {"customerAsset": {"fieldType": field, "status": "ENABLED", "source": source}, "asset": {"type": field}}


def test_an_automatically_created_asset_proves_its_switch_is_on_and_its_absence_proves_nothing():
    cases = {"3.1": "MOBILE_APP", "3.2": "LOCATION", "3.3": "PROMOTION", "3.6": "CALLOUT", "3.7": "AD_IMAGE", "3.8": "SITELINK",
             "3.9": "STRUCTURED_SNIPPET", "3.11": "SELLER_RATING"}
    for cid, field in cases.items():
        none = find(cid, campaign=campaign())
        assert none["result"] is None and "does not show this switch to the API" in none["found"] and "does not prove it" in none["found"], cid
        f = find(cid, campaign=campaign(), camp_assets=[made(field)])
        assert f["result"] == "fail" and "automatically created" in f["found"] and "should be off" in f["found"], cid
        assert find(cid, campaign=campaign(), cust_assets=[made(field, "account")])["result"] == "fail", cid
        assert find(cid, campaign=campaign(), camp_assets=[made(field, source="ADVERTISER")])["result"] is None, cid   # the team's own asset is fine


def test_the_switches_that_should_be_on_or_have_no_asset_are_never_ticked_or_crossed():
    for cid in ("3.4", "3.5", "3.10"):
        f = find(cid, campaign=campaign(), camp_assets=[made("BUSINESS_LOGO"), made("BUSINESS_NAME")])
        assert f["result"] is None and "does not show this switch to the API" in f["found"], cid
    assert "does not prove it" not in find("3.4", campaign=campaign())["found"]


def test_automatically_generated_assets_and_auto_migration():
    assert find("5.1", campaign=campaign())["result"] is None
    f = find("5.1", campaign=campaign(), camp_assets=[made("SITELINK"), made("CALLOUT")])
    assert f["result"] == "fail" and "2 automatically created assets" in f["found"]
    g = find("5.1", campaign=automation(TEXT_ASSET_AUTOMATION="OPTED_IN"))
    assert g["result"] == "fail" and "text customization automation ON" in g["found"]
    m = find("5.2")
    assert m["result"] is None and "does not show" in m["found"]


# ── 4. Auto-apply recommendations ───────────────────────────────────────────

def sub(type_, status="ENABLED"):
    return {"recommendationSubscription": {"type": type_, "status": status}}


def test_with_no_auto_apply_subscription_every_recommendation_is_off():
    for i in range(1, 22):
        f = find("4.%d" % i)
        assert f["result"] == "pass" and "none is subscribed" in f["found"], i
    paused = [sub("OPTIMIZE_AD_ROTATION", "PAUSED")]
    assert find("4.1", recs=paused)["result"] == "pass"                                       # paused is off


def test_an_enabled_subscription_fails_the_matching_check_and_leaves_the_rest_for_a_person():
    recs = [sub("OPTIMIZE_AD_ROTATION"), sub("USE_BROAD_MATCH_KEYWORD"), sub("MAXIMIZE_CONVERSIONS_OPT_IN"), sub("DISPLAY_EXPANSION_OPT_IN")]
    assert find("4.1", recs=recs)["result"] == "fail" and "OPTIMIZE_AD_ROTATION" in find("4.1", recs=recs)["found"]
    assert find("4.9", recs=recs)["result"] == "fail"                                          # add broad match keywords
    assert find("4.10", recs=recs)["result"] == "fail"                                         # display expansion
    assert find("4.13", recs=recs)["result"] == "fail"                                         # maximize conversions
    assert find("4.14", recs=recs)["result"] is None                                           # maximize conversion VALUE is not maximize conversions
    assert find("4.8", recs=recs)["result"] is None                                            # add new keywords is not broad match
    other = find("4.21", recs=recs)
    assert other["result"] is None and "none of which clearly matches" in other["found"]


def test_an_unread_subscription_list_is_never_a_pass():
    d = rows()
    d["recs"] = None
    d["_errors"] = {"recs": "403 not allowed"}
    assert auto.evaluate(d)["4.1"]["result"] is None and "403 not allowed" in auto.evaluate(d)["4.1"]["found"]


# ── Landing pages and the helpers ───────────────────────────────────────────

def ad_group(i="11", name="Brand", status="ENABLED", type_="SEARCH_STANDARD", **k):
    return {"adGroup": dict({"id": i, "name": name, "status": status, "type": type_}, **k)}


def rsa(group="11", headlines=15, descriptions=4, status="ENABLED", urls=("https://example.com/",), inject=None):
    heads = [{"text": "Headline %d" % i} for i in range(headlines)]
    descs = [{"text": "Description %d" % i} for i in range(descriptions)]
    r = {"headlines": heads, "descriptions": descs, "path1": "a", "path2": "b"}
    if inject:
        where, text = inject
        if where == "headline":
            heads[0]["text"] = text
        elif where == "description":
            descs[0]["text"] = text
        else:
            r["path1"] = text
    return {"adGroup": {"id": group}, "adGroupAd": {"status": status, "adStrength": "GOOD", "policySummary": {"approvalStatus": "APPROVED"},
                                                    "ad": {"type": "RESPONSIVE_SEARCH_AD", "finalUrls": list(urls), "responsiveSearchAd": r}}}


def group(i="g1", name="Design"):
    return {"assetGroup": {"id": i, "name": name, "status": "ENABLED", "finalUrls": ["https://example.com/design/"]}}


def page(**k):
    base = {"url": "https://example.com/", "https": True, "status": 200, "finalUrl": "https://example.com/", "loadMs": 300}
    base.update(k)
    return base


def test_other_assets_are_counted_for_the_ai():
    camp = [{"campaignAsset": {"fieldType": "SITELINK", "status": "ENABLED"}, "asset": {}}] * 4
    acct = [{"customerAsset": {"fieldType": "CALLOUT", "status": "ENABLED"}, "asset": {}}] * 7
    f = find("1.6", camp_assets=camp, cust_assets=acct)
    assert f["result"] is None and "sitelinks 4 on the campaign, 0 on the account" in f["found"] and "callouts 0 on the campaign, 7 on the account" in f["found"]


# ── Reading a campaign ──────────────────────────────────────────────────────

class Google:
    """search(customer_id, gaql) that records what it was asked and can refuse some reads."""
    def __init__(self, refuse=None, channel="SEARCH"):
        self.queries, self.refuse, self.channel = [], refuse or {}, channel

    def __call__(self, customer_id, gaql):
        self.queries.append(gaql)
        for needle, exc in self.refuse.items():
            if needle in gaql:
                raise exc
        if "advertising_channel_type FROM campaign WHERE campaign.id" in gaql:
            return [{"campaign": {"id": "1", "advertisingChannelType": self.channel}}]
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
                                     ("PERFORMANCE_MAX", {"asset_group", "asset_group_asset", "asset_group_signal"}, {"ad_group", "ad_group_ad", "ad_group_criterion"}),
                                     ("DEMAND_GEN", {"ad_group", "ad_group_ad", "ad_group_criterion"}, {"asset_group", "asset_group_signal"})):
        g = Google(channel=channel)
        out = auto.run("2133861213", "1", g)
        t = tables(g)
        assert present <= t and not (absent & t), (channel, t)
        assert out["campaign"]["channel"] == channel
    assert len(Google().queries) == 0


def test_a_read_costs_about_sixteen_calls_for_a_search_campaign():
    g = Google()
    auto.run("2133861213", "1", g)
    assert 14 <= len(g.queries) <= 20


def test_the_book_travels_with_the_read_to_the_rules():
    b = book_of(bid_strategy_type="Target CPA")
    out = auto.run("2133861213", "1", Google(), book=b)
    assert out["data"]["_book"] is b


def test_one_refused_read_is_noted_and_the_rest_still_run():
    g = Google(refuse={"FROM recommendation_subscription": Refused("", "400 unrecognized field")})
    out = auto.run("2133861213", "1", g)
    assert "recs" in out["errors"] and "400 unrecognized field" in out["errors"]["recs"]
    assert out["findings"]["4.1"]["found"].startswith("Could not read this from Google Ads (400")
    assert "1.5" in out["findings"]


@pytest.mark.parametrize("kind", ["quota", "auth", "not_configured", "permission"])
def test_a_spent_quota_or_a_sign_in_failure_stops_the_run(kind):
    g = Google(refuse={"FROM customer WHERE": Refused(kind, "stop"), "FROM customer\n": Refused(kind, "stop"), "customer.descriptive_name": Refused(kind, "stop")})
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


# ── The campaign book is Search only; Performance Max and Demand Gen are judged on the spot ─────────────

def test_the_book_is_dropped_for_a_campaign_it_does_not_cover_and_kept_for_search():
    b = book_of(bid_strategy_type="Target CPA")
    pm = auto.run("2133861213", "1", Google(channel="PERFORMANCE_MAX"), book=b)
    assert pm["data"]["_book"] is None and pm["data"]["_bookNotUsed"] == "Performance Max"
    dg = auto.run("2133861213", "1", Google(channel="DEMAND_GEN"), book=b)
    assert dg["data"]["_book"] is None and dg["data"]["_bookNotUsed"] == "Demand Gen"
    se = auto.run("2133861213", "1", Google(channel="SEARCH"), book=b)
    assert se["data"]["_book"] is b and se["data"]["_bookNotUsed"] == ""


def test_no_rule_compares_a_performance_max_campaign_with_a_book_even_when_one_is_offered():
    d = rows(_channel="PERFORMANCE_MAX", _book=None, campaign=campaign(advertisingChannelType="PERFORMANCE_MAX", biddingStrategyType="MAXIMIZE_CONVERSIONS"))
    f = auto.evaluate(d)["1.8"]
    assert f["result"] is None and "book" not in f["found"].lower().replace("campaign book", "")


def pmax_data(signals=(), groups=(("1", "Design Build", "GOOD"), ("2", "Patios", "AVERAGE")), names=None):
    sig = []
    for gid, kind, value in signals:
        s = {"audience": {"audience": value}} if kind == "audience" else {"searchTheme": {"text": value}}
        sig.append({"assetGroup": {"id": gid, "name": dict((g[0], g[1]) for g in groups)[gid]}, "assetGroupSignal": s})
    d = rows(_channel="PERFORMANCE_MAX", campaign=campaign(advertisingChannelType="PERFORMANCE_MAX"), signals=sig,
             asset_groups=[{"assetGroup": {"id": g[0], "name": g[1], "adStrength": g[2], "finalUrls": ["https://example.com/"]}} for g in groups])
    d["_names"] = names or {}
    return d


def test_an_asset_group_with_no_audience_signal_is_a_fail_naming_it():
    d = pmax_data(signals=[("1", "audience", "customers/1/audiences/9"), ("1", "theme", "patio builder")], names={"customers/1/audiences/9": "P2_Customers"})
    f = auto.evaluate(d)["6.1"]
    assert f["result"] == "fail" and "Patios" in f["found"] and "Design Build: P2_Customers" in f["found"]


def test_audience_signals_on_every_asset_group_are_printed_for_the_ai_to_judge_not_ticked():
    d = pmax_data(signals=[("1", "audience", "customers/1/audiences/9"), ("2", "audience", "customers/1/audiences/9")], names={"customers/1/audiences/9": "P2_Customers"})
    f = auto.evaluate(d)["6.1"]
    assert f["result"] is None and "Design Build: P2_Customers" in f["found"] and "Patios: P2_Customers" in f["found"] and "landing page" in f["found"]


def test_an_asset_group_with_no_search_theme_is_a_fail_and_themes_are_printed_otherwise():
    d = pmax_data(signals=[("1", "theme", "patio builder"), ("1", "theme", "outdoor kitchen")])
    f = auto.evaluate(d)["6.2"]
    assert f["result"] == "fail" and "No search theme on Patios" in f["found"] and "2 themes: patio builder, outdoor kitchen" in f["found"]
    d = pmax_data(signals=[("1", "theme", "patio builder"), ("2", "theme", "pergola design")])
    f = auto.evaluate(d)["6.2"]
    assert f["result"] is None and "pergola design" in f["found"]


def test_an_asset_group_rated_below_good_by_google_fails_and_all_good_passes():
    f = auto.evaluate(pmax_data())["6.3"]
    assert f["result"] == "fail" and "Patios (average)" in f["found"] and "Design Build: good" in f["found"]
    ok = auto.evaluate(pmax_data(groups=(("1", "Design Build", "GOOD"), ("2", "Patios", "EXCELLENT"))))["6.3"]
    assert ok["result"] == "pass"
    unrated = auto.evaluate(pmax_data(groups=(("1", "Design Build", "GOOD"), ("2", "Patios", "PENDING"))))["6.3"]
    assert unrated["result"] is None and "not rated yet" in unrated["found"]


def dg_data(audiences=(), excluded=(), names=None, ads=True):
    ag = [{"adGroup": {"id": "11", "name": "Ads A", "status": "ENABLED"}}, {"adGroup": {"id": "12", "name": "Ads B", "status": "ENABLED"}}]
    crit = []
    for gid, ref in audiences:
        crit.append({"adGroup": {"id": gid, "name": "Ads A" if gid == "11" else "Ads B"}, "adGroupCriterion": {"type": "USER_LIST", "negative": False, "userList": {"userList": ref}}})
    for gid, ref in excluded:
        crit.append({"adGroup": {"id": gid, "name": "Ads A"}, "adGroupCriterion": {"type": "USER_LIST", "negative": True, "userList": {"userList": ref}}})
    live = [{"adGroup": {"id": "11"}, "adGroupAd": {"status": "ENABLED", "ad": {"type": "DEMAND_GEN_MULTI_ASSET_AD", "finalUrls": ["https://example.com/"],
                                                                                 "demandGenMultiAssetAd": {"headlines": [{"text": "Patio Builders"}],
                                                                                                           "descriptions": [{"text": "Design and build."}]}}}}] if ads else []
    d = rows(_channel="DEMAND_GEN", campaign=campaign(advertisingChannelType="DEMAND_GEN", demandGenCampaignSettings={"upgradedTargeting": True}),
             adgroups=ag, ag_audiences=crit, ads=live)
    d["_names"] = names or {}
    return d


def test_a_demand_gen_ad_group_with_no_audience_fails_and_optimized_targeting_is_said():
    d = dg_data(audiences=[("11", "customers/1/userLists/5")], names={"customers/1/userLists/5": "Past customers"})
    f = auto.evaluate(d)["6.4"]
    assert f["result"] == "fail" and "No audience on Ads B" in f["found"] and "Ads A: Past customers" in f["found"] and "Optimized targeting is ON" in f["found"]


def test_demand_gen_audiences_on_every_ad_group_are_left_to_the_ai_and_exclusions_do_not_count():
    d = dg_data(audiences=[("11", "customers/1/userLists/5"), ("12", "customers/1/userLists/6")], excluded=[("12", "customers/1/userLists/7")],
                names={"customers/1/userLists/5": "Past customers", "customers/1/userLists/6": "Site visitors", "customers/1/userLists/7": "Employees"})
    f = auto.evaluate(d)["6.4"]
    assert f["result"] is None and "Ads B: Site visitors" in f["found"] and "Employees" not in f["found"]
    only_excluded = auto.evaluate(dg_data(audiences=[("11", "customers/1/userLists/5")], excluded=[("12", "customers/1/userLists/7")]))["6.4"]
    assert only_excluded["result"] == "fail" and "Ads B" in only_excluded["found"]


def test_the_on_the_spot_rules_say_nothing_for_a_campaign_type_that_is_not_theirs():
    out = auto.evaluate(pmax_data())
    assert "6.4" not in out and {"6.1", "6.2", "6.3"} <= set(out)
    out = auto.evaluate(dg_data())
    assert "6.4" in out and not ({"6.1", "6.2", "6.3"} & set(out))


def test_demand_gen_ads_count_as_ads_and_their_text_is_scanned_for_keyword_insertion():
    d = dg_data(audiences=[("11", "customers/1/userLists/5")])
    out = auto.evaluate(d)
    assert out["1.4"]["result"] is None and "1 enabled ad" in out["1.4"]["found"]
    assert out["1.17"]["result"] == "pass" and "2 headlines, descriptions" in out["1.17"]["found"]
    assert auto.evaluate(dg_data(ads=False))["1.4"]["result"] == "fail"
    d["ads"][0]["adGroupAd"]["ad"]["demandGenMultiAssetAd"]["headlines"][0]["text"] = "Best {KeyWord:Patios}"
    assert auto.evaluate(d)["1.17"]["result"] == "fail"


def test_the_names_of_audiences_are_looked_up_for_performance_max_and_demand_gen_only():
    class Names(Google):
        def __call__(self, cid, gaql):
            self.queries.append(gaql)
            if "advertising_channel_type FROM campaign WHERE campaign.id" in gaql:
                return [{"campaign": {"id": "1", "advertisingChannelType": self.channel}}]
            if "FROM user_list" in gaql:
                return [{"userList": {"resourceName": "customers/1/userLists/5", "id": "5", "name": "Past customers"}}]
            if "FROM ad_group_criterion" in gaql and "'USER_LIST'" in gaql and self.channel == "DEMAND_GEN":
                return [{"adGroup": {"id": "11", "name": "A"}, "adGroupCriterion": {"type": "USER_INTEREST", "userInterest": {"userInterestCategory": "customers/1/userInterests/80483"}}}]
            if "FROM user_interest" in gaql:
                return [{"userInterest": {"resourceName": "customers/1/userInterests/80483", "name": "Home & Garden", "taxonomyType": "IN_MARKET"}}]
            return []
    dg = auto.run("2133861213", "1", Names(channel="DEMAND_GEN"))
    names = dg["data"]["_names"]
    assert names["customers/1/userLists/5"] == "Past customers" and names["customers/1/userInterests/80483"] == "Home & Garden (in market)"
    se = Names(channel="SEARCH")
    auto.run("2133861213", "1", se)
    assert not any("FROM user_list" in q for q in se.queries)


def test_a_printed_fact_does_not_ask_for_a_book_the_campaign_type_does_not_have():
    pm = rows(_channel="PERFORMANCE_MAX", campaign=campaign(advertisingChannelType="PERFORMANCE_MAX", biddingStrategyType="MAXIMIZE_CONVERSIONS"))
    pm["_bookNotUsed"] = "Performance Max"
    f = auto.evaluate(pm)
    assert f["1.8"]["found"].endswith("There is no campaign book for a Performance Max campaign, so it is judged on the spot.")
    assert "judged on the spot" in f["1.2"]["found"]
    se = auto.evaluate(rows(campaign=campaign(biddingStrategyType="MAXIMIZE_CONVERSIONS")))
    assert se["1.8"]["found"].endswith("The campaign book names no bid strategy to compare with.")


def test_5_1_does_not_fail_a_campaign_for_the_business_name_and_logo_that_3_4_and_3_5_want_on():
    """A correctly set campaign (dynamic logo and name on) was marked NOT READY by 5.1, which counted those assets as generated assets that should be off."""
    ok = find("5.1", campaign=campaign(), camp_assets=[made("BUSINESS_NAME"), made("BUSINESS_LOGO"), made("LOGO", "campaign")], cust_assets=[made("BUSINESS_NAME", "account")])
    assert ok["result"] is None
    bad = find("5.1", campaign=campaign(), camp_assets=[made("BUSINESS_NAME"), made("SITELINK")])
    assert bad["result"] == "fail" and "1 automatically created asset " in bad["found"] and "SITELINK" in bad["found"] and "BUSINESS_NAME" not in bad["found"]


DG = dict(_channel="DEMAND_GEN")


def test_a_demand_gen_campaign_with_an_excluded_audience_does_not_pass_check_1_13():
    """Demand Gen audiences are read on their own; the check looked only at the Search read and passed with an excluded list sitting there."""
    excl = [{"adGroup": {"id": "11", "name": "Leads"}, "adGroupCriterion": {"type": "USER_LIST", "negative": True}}]
    f = find("1.13", campaign=campaign(), ag_audiences=excl, **DG)
    assert f["result"] == "fail" and "1 audience exclusion found" in f["found"] and "user list on Leads" in f["found"]
    custom = [{"adGroup": {"id": "11", "name": "Leads"}, "adGroupCriterion": {"type": "CUSTOM_INTENT", "negative": True}}]
    assert find("1.13", campaign=campaign(), ag_audiences=custom, **DG)["result"] == "fail"
    assert find("1.13", campaign=campaign(), ag_audiences=[], **DG)["result"] == "pass"
    unread = find("1.13", campaign=campaign(), ag_audiences=None, _errors={"ag_audiences": "Google would not answer"}, **DG)
    assert unread["result"] is None                                                       # an audience read that failed is never a pass


def test_demographics_are_not_reported_as_unset_for_demand_gen_whose_ad_group_settings_are_not_read():
    for check in ("1.11", "1.12", "1.16"):
        f = find(check, campaign=campaign(), **DG)
        assert f["result"] is None and "Demand Gen sets this on its ad groups" in f["found"], check
        s = find(check, campaign=campaign())
        assert "Demand Gen" not in s["found"], check                                       # Search still says what it reads
