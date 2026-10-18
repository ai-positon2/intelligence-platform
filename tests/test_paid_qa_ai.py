"""Paid Marketing Agents: QA Agent, the AI reviewer's guard rails.

The model is never trusted on its own. Pinned here: it is told to use only the evidence, to say
cannot_verify rather than guess, and to compare with the campaign book where a check depends on it;
every pass or flag must quote evidence that is really in what it was shown, or it is turned into
cannot_verify; exact Google Ads rules outrank it; a section that fails (model down, refusal, junk)
leaves its checks for a person and never yields a pass; each section gets only its own checks and
evidence. Nothing here calls a model: `call` is a stand-in.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_qa as qa  # noqa: E402
from tracker import paid_qa_ai as ai  # noqa: E402
from tracker import paid_qa_auto as auto  # noqa: E402
from tests.test_paid_qa_auto import ad_group, book_of, campaign, customer, group, rows, rsa  # noqa: E402

EVIDENCE = json.dumps({"campaign": {"name": "P2_Search_20261014", "dailyBudget": "30.00 USD"},
                       "adGroups": [{"ads": [{"headlines": ["Free estimates today!!"]}]}]})


def answer(id_, verdict="pass", finding="The campaign is named P2_Search_20261014.", evidence="P2_Search_20261014", fix=""):
    return {"id": id_, "verdict": verdict, "finding": finding, "evidence": evidence, "fix": fix}


# ── The instructions ────────────────────────────────────────────────────────

def test_the_model_is_told_to_use_only_the_evidence_never_to_guess_and_to_use_the_book():
    s = ai.SYSTEM
    assert "Use only the evidence given" in s and "cannot_verify" in s and "Never guess a pass" in s
    assert "campaign book" in s and "fromBook" in s and "do not guess the plan" in s
    assert "does not show to the API" in s and "ruleFinding" in s
    assert "—" not in s


def test_the_answer_format_has_exactly_the_four_fields_and_three_verdicts():
    item = ai.SCHEMA["properties"]["results"]["items"]
    assert set(item["properties"]) == {"id", "verdict", "finding", "evidence", "fix"} and item["additionalProperties"] is False
    assert item["properties"]["verdict"]["enum"] == ["pass", "flag", "cannot_verify"]


# ── Quotes must be in the evidence ──────────────────────────────────────────

def test_a_quote_from_the_evidence_is_grounded_and_an_invented_one_is_not():
    assert ai.grounded("Free estimates today!!", EVIDENCE)
    assert ai.grounded("headline: free estimates today", EVIDENCE)                      # trimmed and reworded still passes
    assert not ai.grounded("Seasonal 50% off patio sale ends Friday", EVIDENCE)         # made up
    assert not ai.grounded("ok", EVIDENCE)                                              # too short to mean anything


# ── Turning the model's answer into findings ────────────────────────────────

def test_a_pass_with_grounded_evidence_is_a_pass_and_a_flag_keeps_its_fix():
    out = ai.validate([answer("1.1"), answer("1.4", "flag", "Headline 1 ends with two exclamation marks.", "Free estimates today!!",
                                             "Remove the exclamation marks from headline 1.")], ["1.1", "1.4"], EVIDENCE)
    assert out["1.1"] == {"result": "pass", "found": "The campaign is named P2_Search_20261014.", "evidence": "P2_Search_20261014",
                          "fix": "", "source": "ai", "unverified": False}
    assert out["1.4"]["result"] == "fail" and out["1.4"]["fix"].startswith("Remove the exclamation") and out["1.4"]["unverified"] is False


def test_a_pass_never_carries_a_fix():
    out = ai.validate([answer("1.1", fix="Do something")], ["1.1"], EVIDENCE)
    assert out["1.1"]["result"] == "pass" and out["1.1"]["fix"] == ""


def test_an_answer_that_cannot_be_tied_to_the_evidence_is_not_counted():
    out = ai.validate([answer("1.4", "flag", "Headline 2 promises a 50% discount that the page does not mention.", "Seasonal 50% off patio sale ends Friday")],
                      ["1.4"], EVIDENCE)
    assert out["1.4"]["result"] is None and out["1.4"]["unverified"] is True
    assert "could not be tied to the evidence" in out["1.4"]["found"] and "50% discount" in out["1.4"]["found"]


def test_a_pass_or_flag_without_evidence_or_without_a_finding_is_not_counted():
    out = ai.validate([answer("1.1", evidence=""), answer("1.4", "flag", finding="bad", evidence="Free estimates today!!")], ["1.1", "1.4"], EVIDENCE)
    assert out["1.1"]["unverified"] and out["1.4"]["unverified"] and out["1.1"]["result"] is None and out["1.4"]["result"] is None


def test_cannot_verify_passes_through_with_its_reason():
    out = ai.validate([answer("1.8", "cannot_verify", "The campaign book is needed for the bid strategy and none was added.", "")], ["1.8"], EVIDENCE)
    assert out["1.8"]["result"] is None and out["1.8"]["unverified"] and "campaign book is needed" in out["1.8"]["found"]


def test_a_skipped_check_an_unknown_id_and_a_bad_verdict_never_become_passes():
    out = ai.validate([answer("1.1"), answer("99.9"), {"id": "1.4", "verdict": "maybe", "finding": "x", "evidence": "", "fix": ""},
                       "junk", None, answer("1.1", "flag", "A second, conflicting answer for the same check.", "P2_Search_20261014")],
                      ["1.1", "1.4", "1.25"], EVIDENCE)
    assert set(out) == {"1.1", "1.4", "1.25"}                                           # exactly what was asked
    assert out["1.1"]["result"] == "pass"                                               # the first answer stands
    assert out["1.4"]["unverified"] and out["1.25"]["unverified"] and "did not answer" in out["1.25"]["found"]


def test_a_reply_that_is_not_a_list_leaves_everything_for_a_person():
    for bad in (None, {}, "text", 5):
        out = ai.validate(bad, ["1.1", "1.4"], EVIDENCE)
        assert all(f["unverified"] and f["result"] is None for f in out.values())


def test_em_dashes_are_cleaned_from_what_the_page_shows():
    out = ai.validate([answer("1.1", finding="Named P2_Search_20261014 — follows the convention.")], ["1.1"], EVIDENCE)
    assert "—" not in out["1.1"]["found"] and "P2_Search_20261014, follows" in out["1.1"]["found"]


# ── Exact rules outrank the AI ──────────────────────────────────────────────

def test_an_exact_rules_pass_or_fail_stands_whatever_the_ai_said():
    rules = {"1.5": {"result": "fail", "found": "AI Max for Search is ON."}, "1.3": {"result": "pass", "found": "No extra network is on."}}
    said = {"1.5": {"result": "pass", "found": "AI Max looks off.", "evidence": "x", "fix": "", "source": "ai", "unverified": False},
            "1.3": {"result": "fail", "found": "Search partners look on.", "evidence": "x", "fix": "Turn it off", "source": "ai", "unverified": False}}
    out = ai.combine(rules, said, ["1.5", "1.3"])
    assert out["1.5"]["result"] == "fail" and out["1.5"]["source"] == "rule" and out["1.5"]["found"].startswith("AI Max for Search is ON")
    assert out["1.3"]["result"] == "pass" and out["1.3"]["source"] == "rule"


def test_where_a_rule_only_printed_a_value_the_ai_decides_and_the_printed_value_is_kept():
    rules = {"1.10": {"result": None, "found": "Devices: desktop all traffic, tablet excluded (-100%)."}}
    said = {"1.10": {"result": "pass", "found": "Tablets are excluded as the book says.", "evidence": "tablet excluded", "fix": "", "source": "ai", "unverified": False}}
    out = ai.combine(rules, said, ["1.10"])
    assert out["1.10"]["result"] == "pass" and out["1.10"]["source"] == "ai" and out["1.10"]["facts"].startswith("Devices: desktop")


def test_with_no_ai_answer_a_printed_rule_is_unverified_and_nothing_at_all_is_unverified():
    out = ai.combine({"1.10": {"result": None, "found": "Devices: all."}}, {}, ["1.10", "3.4"])
    assert out["1.10"]["unverified"] and out["1.10"]["result"] is None and out["1.10"]["found"] == "Devices: all."
    assert out["3.4"]["unverified"] and out["3.4"]["result"] is None


# ── Asking section by section ───────────────────────────────────────────────

def checks(*ids):
    return [qa.item(i) for i in ids]


META = {"name": "P2_Search_20261014", "type": "Search", "today": "2026-10-15"}


class Model:
    def __init__(self, fn=None, boom=None):
        self.fn, self.boom, self.asked = fn, boom, []

    def __call__(self, system, user, *, max_tokens):
        if self.boom:
            raise self.boom
        req = json.loads(user)
        self.asked.append((req, max_tokens))
        return {"results": [self.fn(c) for c in req["checks"]]}


def ok(c):
    return answer(c["id"])


def test_each_section_is_asked_separately_with_only_its_own_checks_and_evidence():
    m = Model(ok)
    ev = {"account": {"a": 1}, "campaign": {"name": "P2_Search_20261014"}, "targeting": {}, "adGroups": [{"x": 1}], "assets": {},
          "automaticallyCreatedAssets": "none found", "accountSettingsNotVisibleToTheApi": ["x"], "autoApplyRecommendationsEnabled": "none",
          "campaignBook": "none uploaded"}
    findings, problems = ai.review(checks("1.1", "1.4", "2.1", "3.4", "4.1"), ev,
                                   {"1.4": {"result": None, "found": "1 enabled ad."}}, META, call=m)
    assert problems == {} and set(findings) == {"1.1", "1.4", "2.1", "3.4", "4.1"}
    by = {req["section"]: req for req, _ in m.asked}
    assert len(m.asked) == 4
    s1 = by["1. Campaign Setup"]
    assert [c["id"] for c in s1["checks"]] == ["1.1", "1.4"] and "adGroups" in s1["evidence"] and "automaticallyCreatedAssets" not in s1["evidence"]
    assert s1["checks"][1]["ruleFinding"] == "1 enabled ad." and "ruleFinding" not in s1["checks"][0]
    s4 = by["4. Auto-Apply Recommendations"]
    assert set(s4["evidence"]) == {"autoApplyRecommendationsEnabled", "campaignBook"} and s4["today"] == "2026-10-15"
    assert set(by["3. Automatically Created Assets"]["evidence"]) >= {"accountSettingsNotVisibleToTheApi", "automaticallyCreatedAssets"}


def test_every_check_row_carries_its_expected_value_what_else_to_look_at_and_whether_it_needs_the_book():
    m = Model(ok)
    ai.review(checks("1.6", "1.8"), {"campaign": {}}, {}, META, call=m)
    rows_ = {c["id"]: c for c in m.asked[0][0]["checks"]}
    assert rows_["1.6"]["expectedValue"] == "Added" and "callouts" in rows_["1.6"]["otherThingsToCheck"] and rows_["1.6"]["fromBook"] is False
    assert rows_["1.8"]["fromBook"] is True and rows_["1.8"]["expectedValue"] == "Depends on the campaign book"


def test_a_section_that_fails_leaves_its_checks_for_a_person_and_the_other_sections_still_run():
    class Flaky(Model):
        def __call__(self, system, user, *, max_tokens):
            if json.loads(user)["section"].startswith("3."):
                raise RuntimeError("boom")
            return super().__call__(system, user, max_tokens=max_tokens)
    findings, problems = ai.review(checks("1.1", "3.4", "3.5"), {"campaign": {"name": "P2_Search_20261014"}}, {}, META, call=Flaky(ok), workers=1)
    assert findings["1.1"]["result"] == "pass"
    assert findings["3.4"]["result"] is None and findings["3.4"]["unverified"] and "could not be completed" in findings["3.4"]["found"]
    assert findings["3.5"]["result"] is None and problems == {"RuntimeError": 1}


def test_a_model_that_is_down_or_not_configured_never_produces_a_pass():
    from tracker.market_radar_llm import ModelError
    for err in (ModelError("not_configured", "no key"), ModelError("refused", "declined"), ModelError("truncated", "cut off"),
                ModelError("bad_json", "not json")):
        findings, problems = ai.review(checks("1.1", "3.4"), {}, {}, META, call=Model(boom=err))
        assert all(f["result"] is None and f["unverified"] for f in findings.values())
        assert problems == {err.kind: 2}


def test_progress_is_reported_after_each_section():
    seen = []
    ai.review(checks("1.1", "2.1", "3.4"), {"campaign": {"name": "P2_Search_20261014"}}, {}, META, call=Model(ok),
              progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_the_reply_budget_grows_with_the_section_and_is_capped():
    m = Model(ok)
    ai.review(checks("1.1"), {}, {}, META, call=m)
    assert m.asked[0][1] == 380 * 1 + 600
    big = [i for i in qa.items() if i["id"].startswith("4.")] * 30
    got = []
    ai.review_section("4. Auto-Apply Recommendations", big, {}, {}, META, call=lambda s, u, max_tokens: (got.append(max_tokens) or {"results": []}))
    assert got == [9000]


# ── The evidence it is shown ────────────────────────────────────────────────

def sample_data(book=None):
    ag = [{"adGroup": {"name": "Brand"}, "adGroupCriterion": {"type": "AGE_RANGE", "status": "ENABLED", "ageRange": {"type": "AGE_RANGE_25_34"}}},
          {"adGroup": {"name": "Brand"}, "adGroupCriterion": {"type": "GENDER", "status": "ENABLED", "negative": True, "gender": {"type": "MALE"}}},
          {"adGroup": {"name": "Brand"}, "adGroupCriterion": {"type": "USER_LIST", "status": "ENABLED", "negative": True}}]
    d = rows(customer=customer(descriptiveName="Example Co", trackingUrlTemplate="{lpurl}?utm_source=google"),
             campaign=campaign(biddingStrategyType="MAXIMIZE_CONVERSIONS"),
             adgroups=[ad_group("11", "Brand")], ads=[rsa("11", headlines=3, descriptions=2)], ag_criteria=ag,
             recs=[{"recommendationSubscription": {"type": "OPTIMIZE_AD_ROTATION", "status": "ENABLED"}}],
             criteria=[{"campaignCriterion": {"type": "LOCATION", "location": {"geoTargetConstant": "geoTargetConstants/10"}}},
                       {"campaignCriterion": {"type": "DEVICE", "bidModifier": 0, "device": {"type": "TABLET"}}},
                       {"campaignCriterion": {"type": "AGE_RANGE", "negative": True, "ageRange": {"type": "AGE_RANGE_18_24"}}},
                       {"campaignCriterion": {"type": "KEYWORD", "negative": True, "keyword": {"text": "jobs", "matchType": "BROAD"}}}],
             camp_assets=[{"campaignAsset": {"fieldType": "SITELINK", "status": "ENABLED", "source": "AUTOMATICALLY_CREATED"},
                           "asset": {"type": "SITELINK", "finalUrls": ["https://example.com/a"], "sitelinkAsset": {"linkText": "Our work", "description1": "See it"}}}],
             goals=[{"campaignConversionGoal": {"category": "PURCHASE", "origin": "WEBSITE", "biddable": True}}],
             cust_goals=[{"customerConversionGoal": {"category": "PURCHASE", "origin": "WEBSITE", "biddable": True}}])
    d["_geo"] = {"geoTargetConstants/10": {"name": "Annapolis", "canonical": "Annapolis,Maryland,United States"}}
    d["_pages"] = [{"url": "https://example.com/", "text": "x" * 5000, "status": 200}]
    d["_book"] = book
    return d


def test_the_evidence_has_what_the_campaign_book_checks_need_to_be_judged():
    d = sample_data()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert ev["account"]["trackingTemplate"] == "{lpurl}?utm_source=google" and ev["campaign"]["name"] == "P2_Search_20261014"
    t = ev["targeting"]
    assert t["locationsTargeted"] == ["Annapolis,Maryland,United States"]
    assert t["devices"] == [{"device": "TABLET", "bidModifier": 0, "note": "0 means excluded"}]
    assert t["campaignLevelAges"] == [{"value": "AGE_RANGE_18_24", "excluded": True}]
    assert t["adGroupDemographics"]["Brand"] == [{"kind": "AGE_RANGE", "value": "AGE_RANGE_25_34", "excluded": False},
                                                {"kind": "GENDER", "value": "MALE", "excluded": True}]
    assert t["audiences"] == [{"level": "ad group Brand", "type": "USER_LIST", "excluded": True}]
    assert ev["negatives"]["campaignNegativeKeywords"] == ["jobs [broad]"]
    g = ev["adGroups"][0]
    assert g["name"] == "Brand" and g["ads"][0]["headlines"][0] == "Headline 0" and len(g["ads"][0]["headlines"]) == 3
    assert ev["assets"]["campaignLevel"][0]["text"] == "Our work" and ev["assets"]["campaignLevel"][0]["source"] == "AUTOMATICALLY_CREATED"
    assert ev["automaticallyCreatedAssets"] == [{"level": "campaign", "field": "SITELINK"}]
    assert ev["conversionGoals"] == {"campaign": ["PURCHASE/WEBSITE optimised"], "accountDefaults": ["PURCHASE/WEBSITE optimised"]}
    assert ev["autoApplyRecommendationsEnabled"] == ["OPTIMIZE_AD_ROTATION"]
    assert "automatically created assets switches" in ev["accountSettingsNotVisibleToTheApi"][0]
    assert ev["campaignBook"] == "none uploaded"


def test_the_campaign_book_is_part_of_the_evidence_when_one_was_added():
    b = {"filename": "Book.xlsx", "rows": [{"field": "Genders", "value": "Women", "other": ""}], "text": "Sheet1 | Genders | Women"}
    d = sample_data(book=b)
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert ev["campaignBook"] == {"filename": "Book.xlsx", "fields": b["rows"], "everythingOnItsSheets": "Sheet1 | Genders | Women"}
    assert "campaignBook" in ai.SECTION_EVIDENCE["1"] and all("campaignBook" in v for v in ai.SECTION_EVIDENCE.values())


def test_a_landing_pages_text_is_cut_so_the_evidence_stays_readable():
    d = sample_data()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert len(ev["landingPages"][0]["text"]) == 1800
    json.dumps(ev, default=str)                                                       # and it serialises


def test_pmax_asset_group_texts_are_in_the_evidence():
    d = rows(_channel="PERFORMANCE_MAX", campaign=campaign(advertisingChannelType="PERFORMANCE_MAX"), asset_groups=[group()],
             asset_group_assets=[{"assetGroup": {"id": "g1"}, "assetGroupAsset": {"fieldType": "HEADLINE", "status": "ENABLED"},
                                  "asset": {"textAsset": {"text": "Design your yard"}}},
                                 {"assetGroup": {"id": "g1"}, "assetGroupAsset": {"fieldType": "MARKETING_IMAGE", "status": "ENABLED"}, "asset": {}}])
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    g = ev["performanceMaxAssetGroups"][0]
    assert g["name"] == "Design" and g["texts"] == {"HEADLINE": ["Design your yard"]} and g["counts"] == {"HEADLINE": 1, "MARKETING_IMAGE": 1}


def test_every_section_has_an_evidence_list_that_names_real_evidence():
    d = sample_data()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    sections = {i["id"].split(".")[0] for i in qa.items()}
    assert sections == set(ai.SECTION_EVIDENCE)                                        # no section forgotten
    for key, wanted in ai.SECTION_EVIDENCE.items():
        assert set(wanted) <= set(ev), (key, set(wanted) - set(ev))


def test_an_empty_campaign_still_gives_evidence_without_errors():
    d = rows()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert ev["adGroups"] == [] and ev["landingPages"] == [] and ev["targeting"]["languages"] == "all languages"
    assert ev["autoApplyRecommendationsEnabled"].startswith("none")


# ── Campaigns the campaign book does not cover are judged on the spot ───────

SPOT_ITEM = {"id": "1.8", "check": "Bid Strategy Type", "expected": "Depends on the campaign book", "otherThingsToCheck": "", "fromBook": True,
             "onTheSpot": "There is no plan to compare with. Judge the bid strategy on its own."}


def test_a_check_that_would_use_the_book_is_given_its_on_the_spot_guidance_when_the_book_does_not_apply():
    withbook = ai._check_rows([SPOT_ITEM], {}, True)[0]
    assert withbook["fromBook"] is True and "judgeOnTheSpot" not in withbook
    without = ai._check_rows([SPOT_ITEM], {}, False)[0]
    assert without["fromBook"] is False and without["judgeOnTheSpot"].startswith("There is no plan")
    fixed = ai._check_rows([dict(SPOT_ITEM, fromBook=False, onTheSpot="")], {}, False)[0]
    assert fixed["fromBook"] is False and "judgeOnTheSpot" not in fixed


def test_the_prompt_tells_the_ai_how_to_treat_a_campaign_the_book_does_not_cover():
    assert "judgeOnTheSpot" in ai.SYSTEM and "Never say the campaign book is needed" in ai.SYSTEM
    assert "audience signals, search themes" in ai.SYSTEM and "—" not in ai.SYSTEM


def test_the_evidence_says_the_book_is_not_used_and_never_carries_it():
    d = sample_data(book={"filename": "b.xlsx", "rows": [{"field": "Bid Strategy Type", "value": "Manual CPC"}], "text": "Manual CPC"})
    d["_channel"], d["_book"], d["_bookNotUsed"] = "PERFORMANCE_MAX", None, "Performance Max"
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert ev["campaignBook"] == "not used for this campaign type (Performance Max): the checks that depend on it are judged on the spot"
    assert "Manual CPC" not in json.dumps(ev)


def test_performance_max_evidence_has_each_asset_groups_audience_signals_and_search_themes():
    d = sample_data()
    d.update(_channel="PERFORMANCE_MAX", _names={"customers/1/audiences/9": "P2_Customers"},
             asset_groups=[{"assetGroup": {"id": "7", "name": "Design Build", "adStrength": "GOOD", "finalUrls": ["https://example.com/"]}},
                           {"assetGroup": {"id": "8", "name": "Patios", "adStrength": "AVERAGE", "finalUrls": ["https://example.com/p"]}}],
             signals=[{"assetGroup": {"id": "7"}, "assetGroupSignal": {"audience": {"audience": "customers/1/audiences/9"}}},
                      {"assetGroup": {"id": "7"}, "assetGroupSignal": {"searchTheme": {"text": "patio builder"}}}])
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert ev["performanceMaxSignals"] == [{"assetGroup": "Design Build", "audienceSignals": ["P2_Customers"], "searchThemes": ["patio builder"]},
                                           {"assetGroup": "Patios", "audienceSignals": "none", "searchThemes": "none"}]
    assert ev["demandGenTargeting"] is None


def test_demand_gen_evidence_has_audiences_by_ad_group_exclusions_and_its_ads_text():
    d = sample_data()
    d.update(_channel="DEMAND_GEN", _names={"customers/1/userLists/5": "Past customers", "customers/1/userLists/7": "Employees"},
             campaign=campaign(advertisingChannelType="DEMAND_GEN", demandGenCampaignSettings={"upgradedTargeting": True}),
             adgroups=[ad_group("11", "Ads A")],
             ads=[{"adGroup": {"id": "11"}, "adGroupAd": {"status": "ENABLED", "ad": {"type": "DEMAND_GEN_MULTI_ASSET_AD", "finalUrls": ["https://example.com/"],
                                                                                  "demandGenMultiAssetAd": {"headlines": [{"text": "Patio Builders"}],
                                                                                                            "descriptions": [{"text": "Design and build."}]}}}}],
             ag_audiences=[{"adGroup": {"id": "11", "name": "Ads A"}, "adGroupCriterion": {"type": "USER_LIST", "negative": False, "userList": {"userList": "customers/1/userLists/5"}}},
                           {"adGroup": {"id": "11", "name": "Ads A"}, "adGroupCriterion": {"type": "USER_LIST", "negative": True, "userList": {"userList": "customers/1/userLists/7"}}}])
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert ev["demandGenTargeting"] == {"audiencesByAdGroup": {"Ads A": {"audiences": ["Past customers"], "excluded": ["Employees"]}}, "optimizedTargeting": True}
    ad = ev["adGroups"][0]["ads"][0]
    assert ad["type"] == "DEMAND_GEN_MULTI_ASSET_AD" and ad["headlines"] == ["Patio Builders"] and ad["descriptions"] == ["Design and build."]
    assert ev["performanceMaxSignals"] is None


def test_section_six_reads_the_signals_and_a_search_campaign_is_never_shown_empty_ones():
    d = sample_data()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert set(ai._slice(ev, "6")) <= set(ai.SECTION_EVIDENCE["6"]) and "performanceMaxSignals" not in ai._slice(ev, "6")
    assert "demandGenTargeting" not in ai._slice(ev, "1")


def test_a_list_cut_short_for_the_model_says_how_many_it_left_out():
    """With 40 ad groups the model saw 25 and nothing said so, and a pass on the ad copy rested on part of the evidence."""
    d = rows(customer=customer(), campaign=campaign(), adgroups=[ad_group(str(i), "Group %d" % i) for i in range(40)],
             ads=[rsa(str(i), headlines=3, descriptions=2) for i in range(40)])
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert len(ev["adGroups"]) == 26 and ev["adGroups"][-1] == {"more": 15} and ev["adGroups"][0]["name"] == "Group 0"
    few = rows(customer=customer(), campaign=campaign(), adgroups=[ad_group("1", "Only")], ads=[rsa("1", headlines=3, descriptions=2)])
    assert all("more" not in g for g in ai.evidence_bundle(few, auto.Ctx(few))["adGroups"])
