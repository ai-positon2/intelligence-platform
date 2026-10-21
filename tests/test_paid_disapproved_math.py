"""Disapproved Ads Tracker: which ad groups show nothing, the weight of what they lost, the policy topics, the findings and the fix plan (tracker/paid_disapproved_math.py, paid_disapproved_plan.py). No network."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_disapproved_math as m  # noqa: E402
from tracker import paid_disapproved_plan as plan  # noqa: E402


def ad(i, group="G1", camp="Core Search", approval="DISAPPROVED", topics=(("DESTINATION_NOT_WORKING", "PROHIBITED"),), review="REVIEWED", evidence=(), name=None):
    return {"id": str(i), "campaignId": "C-" + camp, "campaign": camp, "adGroupId": group, "adGroup": name or ("Group " + group), "type": "RESPONSIVE_SEARCH_AD", "approval": approval,
            "review": review, "topics": [{"t": t, "ty": ty, "ev": list(evidence)} for t, ty in topics], "headlines": ["Dentist near you"], "url": "https://example.com/a"}


def weeks(*clicks, cost_each=0.5):
    return [{"clicks": c, "cost": c * cost_each} for c in clicks]


# ── The weight of an ad group ───────────────────────────────────────────────

def test_the_usual_is_the_middle_of_the_earlier_weeks_with_clicks_and_a_stopped_group_has_lost_them():
    w = m.weight(weeks(100, 110, 90, 100, 0))
    assert w == {"usualClicks": 100, "usualCost": 50.0, "lastClicks": 0, "lastCost": 0.0, "lostClicks": 100}


def test_weeks_already_stopped_do_not_pull_the_usual_down():
    w = m.weight(weeks(100, 0, 0, 120, 0))                      # stopped for two weeks and then again: only the weeks with clicks say what is usual
    assert w["usualClicks"] == 110 and w["lostClicks"] == 110 and w["usualCost"] == 55.0


def test_a_group_that_still_makes_at_least_half_of_its_usual_has_lost_nothing():
    assert m.weight(weeks(100, 100, 100, 100, 50))["lostClicks"] == 0 and m.weight(weeks(100, 100, 100, 100, 49))["lostClicks"] == 51


def test_no_earlier_click_or_too_few_weeks_is_no_weight():
    assert m.weight(weeks(0, 0, 0, 0, 5)) is None and m.weight(weeks(10)) is None and m.weight(None) is None and m.weight([]) is None


# ── The items ───────────────────────────────────────────────────────────────

W = {"G1": weeks(100, 110, 90, 100, 0), "G2": weeks(40, 40, 40, 40, 40)}


def test_a_disapproved_ad_with_no_approved_ad_beside_it_is_stopped_and_one_with_cover_is_not():
    a = m.analyse([ad(1, "G1"), ad(2, "G1"), ad(3, "G2", "Brand")], {"G1": 0, "G2": 1}, W)
    st = {i["id"]: i["state"] for i in a["items"]}
    assert st == {"1": "dead", "2": "dead", "3": "covered"} and a["status"] == "stopped"
    assert a["counts"] == {"disapproved": 3, "limited": 0, "keywords": 0, "stoppedGroups": 1, "appeal": 0, "reviewing": 0, "assets": 0, "meta": 0} and a["campaigns"] == 2 and a["adGroups"] == 2
    g = a["deadGroups"][0]
    assert g["id"] == "G1" and g["ads"] == 2 and g["name"] == "Group G1" and g["topics"] == ["DESTINATION_NOT_WORKING"] and g["weight"]["lostClicks"] == 100


def test_a_group_missing_from_the_cover_has_no_approved_ad():
    assert m.analyse([ad(1, "G9")], {}, None)["items"][0]["state"] == "dead"


def test_when_the_cover_could_not_be_read_nothing_is_called_stopped():
    a = m.analyse([ad(1, "G1")], None, W)
    assert a["items"][0]["state"] == "unchecked" and a["items"][0]["covers"] is None and a["status"] == "disapproved" and a["deadGroups"] == [] and a["coverKnown"] is False
    assert "could not be read, so none is called serious" in a["findings"][0]["detail"]


def test_a_disapproved_ad_with_cover_beside_a_limited_one_is_disapproved_and_says_where_to_start():
    a = m.analyse([ad(1), ad(2, "G2", approval="APPROVED_LIMITED")], {"G1": 1, "G2": 1}, None)
    assert a["status"] == "disapproved" and [f["kind"] for f in a["findings"]] == ["disapproved", "limited"]
    assert a["findings"][0]["nextStep"].startswith("Start with: Fix the ads flagged for Destination not working (2 ads).")


def test_limited_and_area_of_interest_ads_are_limited_not_disapproved():
    a = m.analyse([ad(1, approval="APPROVED_LIMITED"), ad(2, "G2", approval="AREA_OF_INTEREST_ONLY")], {"G1": 2, "G2": 2}, None)
    assert [i["state"] for i in a["items"]] == ["limited", "limited"] and a["status"] == "limited" and a["counts"]["limited"] == 2 and a["counts"]["disapproved"] == 0
    assert [f["kind"] for f in a["findings"]] == ["limited"] and a["findings"][0]["level"] == "low"


def test_appeals_and_reviews_are_counted_and_stay_on_the_list():
    a = m.analyse([ad(1, review="UNDER_APPEAL"), ad(2, "G2", review="REVIEW_IN_PROGRESS"), ad(3, "G3")], {"G1": 1, "G2": 1, "G3": 1}, None)
    assert a["counts"]["appeal"] == 1 and a["counts"]["reviewing"] == 1 and len(a["items"]) == 3
    f = [x for x in a["findings"] if x["kind"] == "waiting"][0]
    assert f["level"] == "info" and f["title"] == "1 ad is under appeal and 1 ad is being reviewed" and "a pending appeal is not a fix" in f["detail"]


def test_nothing_flagged_is_clear_with_no_findings_and_no_plan():
    a = m.analyse([], {}, {})
    assert a["status"] == "clear" and a["findings"] == [] and a["plan"] == {"steps": [], "note": None} and a["items"] == [] and a["topics"] == []


def test_the_stopped_groups_are_biggest_first_and_the_items_stopped_first():
    ads = [ad(1, "G2", "Zeta"), ad(2, "G1"), ad(3, "G3", approval="APPROVED_LIMITED"), ad(4, "G4", "Brand")]
    a = m.analyse(ads, {"G1": 0, "G2": 0, "G3": 1, "G4": 1}, {"G1": weeks(10, 10, 10, 10, 0), "G2": weeks(100, 100, 100, 100, 0)})
    assert [g["id"] for g in a["deadGroups"]] == ["G2", "G1"]               # the one that made more clicks first, though its campaign comes later in the alphabet
    assert [i["state"] for i in a["items"]] == ["dead", "dead", "covered", "limited"]


# ── The topics ──────────────────────────────────────────────────────────────

def test_an_ad_with_two_policies_counts_in_both_and_the_most_serious_type_wins():
    ads = [ad(1, "G1", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED"), ("HEALTHCARE_AND_MEDICINES", "PROHIBITED"))), ad(2, "G2", topics=(("TRADEMARKS_IN_AD_TEXT", "PROHIBITED"),))]
    t = {x["topic"]: x for x in m.analyse(ads, {"G1": 1, "G2": 1}, None)["topics"]}
    assert t["TRADEMARKS_IN_AD_TEXT"]["ads"] == 2 and t["TRADEMARKS_IN_AD_TEXT"]["type"] == "PROHIBITED" and t["TRADEMARKS_IN_AD_TEXT"]["adGroups"] == 2
    assert t["HEALTHCARE_AND_MEDICINES"]["ads"] == 1 and t["HEALTHCARE_AND_MEDICINES"]["label"] == "Healthcare and medicines"


def test_topics_with_a_stopped_ad_group_come_first_then_the_most_ads():
    ads = [ad(1, "G1", topics=(("A_TOPIC", "LIMITED"),)), ad(2, "G2", topics=(("B_TOPIC", "LIMITED"),)), ad(3, "G3", topics=(("B_TOPIC", "LIMITED"),)), ad(4, "G4", topics=(("C_TOPIC", "LIMITED"),))]
    a = m.analyse(ads, {"G1": 0, "G2": 1, "G3": 1, "G4": 1}, None)
    assert [t["topic"] for t in a["topics"]] == ["A_TOPIC", "B_TOPIC", "C_TOPIC"] and [t["dead"] for t in a["topics"]] == [1, 0, 0] and [t["disapproved"] for t in a["topics"]] == [1, 2, 1]


def test_the_text_google_points_at_is_kept_three_at_most_without_repeats():
    ads = [ad(i, "G%d" % i, evidence=("Free!!!", "Best price")) for i in range(1, 4)] + [ad(8, "G8", evidence=("Cheapest",)), ad(9, "G9", evidence=("Lowest",))]
    t = m.analyse(ads, {}, None)["topics"][0]
    assert t["examples"] == ["Free!!!", "Best price", "Cheapest"]


def test_a_topic_that_disapproves_ads_comes_before_one_that_only_limits_them_whatever_their_counts():
    ads = [ad(1, "G1", approval="APPROVED_LIMITED", topics=(("L_TOPIC", "LIMITED"),)), ad(2, "G2", approval="APPROVED_LIMITED", topics=(("L_TOPIC", "LIMITED"),)), ad(3, "G3", topics=(("D_TOPIC", "LIMITED"),))]
    assert [t["topic"] for t in m.analyse(ads, {"G1": 1, "G2": 1, "G3": 1}, None)["topics"]] == ["D_TOPIC", "L_TOPIC"]


def test_an_ad_with_no_named_policy_is_a_topic_of_its_own_with_an_honest_hint():
    t = m.analyse([ad(1, topics=())], {"G1": 1}, None)["topics"][0]
    assert t["topic"] == "NOT_NAMED" and t["label"] == "Reason not named" and "did not name the policy" in t["hint"]


def test_the_hint_is_the_asset_coverage_library_for_the_words_in_the_topic():
    t = m.analyse([ad(1, topics=(("DESTINATION_NOT_WORKING", "PROHIBITED"),))], {"G1": 1}, None)["topics"][0]
    assert t["hint"].startswith("The page the asset sends people to does not work")


# ── The findings ────────────────────────────────────────────────────────────

def test_the_stopped_finding_names_the_groups_and_what_they_used_to_bring():
    a = m.analyse([ad(1, "G1"), ad(2, "G2", "Brand", name="Brand Exact")], {"G1": 0, "G2": 0}, {"G1": weeks(100, 110, 90, 100, 0), "G2": weeks(20, 20, 20, 20, 10)})
    f = a["findings"][0]
    assert f["level"] == "high" and f["kind"] == "stopped" and f["title"] == "2 ad groups cannot show any ad"
    assert "Every enabled ad in 2 ad groups is disapproved, so nothing in them can show" in f["detail"] and "Group G1 in Core Search" in f["detail"] and "Brand Exact in Brand" in f["detail"]
    assert "Before, they made about 120 clicks a week between them; last week they made 10." in f["detail"] and f["nextStep"].startswith("Start with: Get the ad groups that cannot show an ad running again.")


def test_a_stopped_group_with_no_history_says_nothing_about_clicks():
    f = m.analyse([ad(1)], {"G1": 0}, None)["findings"][0]
    assert f["title"] == "1 ad group cannot show any ad" and "Before, they made" not in f["detail"] and "Every enabled ad in 1 ad group is disapproved, so nothing in it can show" in f["detail"]


def test_a_covered_disapproval_is_medium_with_the_topics_and_says_the_ad_group_still_shows():
    a = m.analyse([ad(1, "G1", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED"),)), ad(2, "G2", "Brand", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED"),))], {"G1": 1, "G2": 3}, None)
    f = a["findings"][0]
    assert f["level"] == "medium" and f["title"] == "2 ads are disapproved" and "In 2 ad groups of 2 campaigns (Brand, Core Search)." in f["detail"] and "Flagged for trademarks in ad text." in f["detail"]
    assert "Each has another approved ad in its ad group, so the ad group still shows." in f["detail"]


def test_with_a_stopped_group_the_covered_finding_says_to_fix_that_first():
    a = m.analyse([ad(1, "G1"), ad(2, "G2", "Brand")], {"G1": 0, "G2": 2}, None)
    assert [f["kind"] for f in a["findings"]] == ["stopped", "disapproved"] and a["findings"][1]["nextStep"] == "Fix the ad groups that cannot show an ad first; these follow."


# ── The plan ────────────────────────────────────────────────────────────────

def test_the_plan_starts_with_the_stopped_groups_sizes_them_then_one_step_a_topic_then_the_check():
    ads = [ad(1, "G1", evidence=("Cheap!!!",)), ad(2, "G2", "Brand", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED"),))]
    a = m.analyse(ads, {"G1": 0, "G2": 2}, {"G1": weeks(100, 110, 90, 100, 0)})
    steps = a["plan"]["steps"]
    assert [s["id"] for s in steps] == ["cover", "topic-destination_not_working", "topic-trademarks_in_ad_text", "again"] and [s["n"] for s in steps] == [1, 2, 3, 4]
    cover = steps[0]
    assert cover["level"] == "high" and cover["campaigns"] == ["Core Search"] and '1 ad group ("Group G1") has no approved ad' in cover["why"]
    assert "Before, these ad groups made about 100 clicks a week and spent about $50.00; last week they made 0, so about 100 clicks a week are not coming." in cover["worth"]
    assert "an estimate from the last 5 whole weeks" in cover["worth"] and "Responsive search ad" in " ".join(cover["how"])
    dest = steps[1]
    assert dest["title"] == "Fix the ads flagged for Destination not working (1 ad)" and dest["level"] == "high" and dest["why"].startswith("1 ad is flagged for this (in 1 ad group with no approved ad). The page the asset sends")
    assert "prohibited use" in dest["why"] and 'Google pointed at: "Cheap!!!".' in dest["why"] and dest["how"][2].startswith("This is about the page, not the words")
    tm = steps[2]
    assert tm["level"] == "medium" and tm["why"].startswith("1 ad is flagged for this. ") and "Google limits this: the ad can run in some places" in tm["why"] and tm["how"][2].startswith("Edit that one ad")
    assert steps[3]["level"] == "low" and "Refresh" in " ".join(steps[3]["how"])


def test_a_topic_with_limited_ads_only_is_low_and_the_worth_says_when_the_weeks_could_not_be_read():
    a = m.analyse([ad(1, approval="APPROVED_LIMITED", topics=(("PERSONALIZED_ADVERTISING", "LIMITED"),)), ad(2, "G2", "Brand")], {"G1": 1, "G2": 0}, None)
    assert a["plan"]["steps"][0]["worth"] == "How many clicks these ad groups used to bring could not be read, so it is not sized here."
    topic = [s for s in a["plan"]["steps"] if s["id"] == "topic-personalized_advertising"][0]
    assert topic["level"] == "low"
    nodata = m.analyse([ad(1)], {"G1": 0}, {})
    assert nodata["plan"]["steps"][0]["worth"] == "None of these ad groups had a click in the weeks read, so there is nothing to size."


def test_only_the_four_biggest_topics_get_a_step_and_the_note_says_how_many_more_there_are():
    ads = [ad(i, "G%d" % i, topics=(("TOPIC_%d" % i, "LIMITED"),)) for i in range(1, 8)]
    a = m.analyse(ads, {"G%d" % i: 1 for i in range(1, 8)}, None)
    assert len([s for s in a["plan"]["steps"] if s["id"].startswith("topic-")]) == 4 and "3 more policy topics are in the table above." in a["plan"]["note"] and "the text in Policy manager is the ruling" in a["plan"]["note"]
    one = m.analyse(ads[:5], {"G%d" % i: 1 for i in range(1, 8)}, None)["plan"]["note"]
    assert "1 more policy topic is in the table above." in one


def test_an_unchecked_cover_says_so_in_the_plan_note():
    assert "could not be read, so no ad group is called stopped" in m.analyse([ad(1)], None, None)["plan"]["note"]
    assert "could not be read, so no ad group" not in m.analyse([ad(1)], {"G1": 1}, None)["plan"]["note"]


def test_a_plan_step_with_no_named_policy_asks_to_read_the_reason_in_policy_manager():
    s = m.analyse([ad(1, topics=())], {"G1": 1}, None)["plan"]["steps"][0]
    assert s["title"] == "Find out why 1 ad is flagged" and "did not name the policy" in s["why"]


def test_every_step_has_a_title_a_reason_and_how_and_nothing_has_a_dash_or_an_unfilled_slot():
    seen = []
    for a in (m.analyse([ad(1, evidence=("Free!!!",)), ad(2, "G2", "Brand", approval="APPROVED_LIMITED", topics=(("HEALTH_IN_PERSONALIZED_ADS", "LIMITED"),)), ad(3, "G3", topics=())], {"G1": 0, "G2": 1, "G3": 1},
                        {"G1": weeks(100, 110, 90, 100, 0)}), m.analyse([ad(1)], None, None), m.analyse([], {}, {})):
        for s in a["plan"]["steps"]:
            assert s["title"] and s["why"] and s["how"] and all(s["how"]) and s["level"] in ("high", "medium", "low")
            seen += [s["title"], s["why"], s["worth"] or ""] + s["how"]
        for f in a["findings"]:
            seen += [f["title"], f["detail"], f["nextStep"]]
        seen.append(a["plan"]["note"] or "")
    text = "\n".join(seen)
    assert text.strip() and "—" not in text and "–" not in text
    assert not re.search(r"%[sd]|\{|None|nan", text)


# ── Assets, keywords and Meta ───────────────────────────────────────────────

def asset(i, group="pmax:7:HEADLINE", name="Summer (headlines)", approval="DISAPPROVED", need=3, text="Free whitening!!!", topics=(("PUNCTUATION_AND_SYMBOLS", "LIMITED"),), camp="PMax All"):
    return dict(ad(i, group, camp, approval, topics, name=name), kind="asset", need=need, type="ASSET_HEADLINE", headlines=[text], url="")


def kw(i, group="G1", text="[cheap implants]", topics=(("HEALTHCARE_AND_MEDICINES", "LIMITED"),)):
    return dict(ad(i, group, "Core Search", "DISAPPROVED", topics), kind="keyword", type="KEYWORD", headlines=[text], url="")


def test_a_performance_max_field_with_fewer_approved_assets_than_it_needs_is_stopped():
    a = m.analyse([asset(1)], {"pmax:7:HEADLINE": 2}, None)                         # headlines need three approved, two are
    assert a["items"][0]["state"] == "dead" and a["items"][0]["kind"] == "asset" and a["status"] == "stopped" and a["deadGroups"][0]["name"] == "Summer (headlines)"
    assert a["findings"][0]["title"] == "1 ad group cannot show any ad" and "Summer (headlines) in PMax All" in a["findings"][0]["detail"]
    ok = m.analyse([asset(1)], {"pmax:7:HEADLINE": 3}, None)
    assert ok["items"][0]["state"] == "covered" and ok["status"] == "disapproved"
    assert "1 asset is disapproved" == ok["findings"][0]["title"] and "Each has another approved asset in its asset group, so the ad group still shows." in ok["findings"][0]["detail"]
    mixed = m.analyse([asset(1), ad(2, "G1")], {"pmax:7:HEADLINE": 3, "G1": 1}, None)
    assert mixed["findings"][0]["title"] == "2 ads and assets are disapproved" and mixed["counts"]["assets"] == 1


def test_a_field_that_needs_no_assets_is_never_stopped_by_losing_one():
    a = m.analyse([asset(1, need=0)], {"pmax:7:HEADLINE": 0}, None)
    assert a["items"][0]["state"] == "covered" and a["deadGroups"] == []


def test_a_disapproved_keyword_is_counted_apart_never_stops_an_ad_group_and_has_its_own_finding_and_step():
    a = m.analyse([kw(1), kw(2, "G2", "[bleaching kit]", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED"),)), ad(3, "G3")], {"G1": 0, "G2": 0, "G3": 1}, None)
    assert [i["state"] for i in a["items"]] == ["covered", "keyword", "keyword"] and a["deadGroups"] == [] and a["counts"]["keywords"] == 2 and a["counts"]["disapproved"] == 1
    assert [t["topic"] for t in a["topics"]] == ["DESTINATION_NOT_WORKING"] and a["keywordTopics"] == [{"topic": "HEALTHCARE_AND_MEDICINES", "label": "Healthcare and medicines", "keywords": 1},
                                                                                                       {"topic": "TRADEMARKS_IN_AD_TEXT", "label": "Trademarks in ad text", "keywords": 1}]
    f = [x for x in a["findings"] if x["kind"] == "keywords"][0]
    assert f["level"] == "medium" and f["title"] == "2 keywords are disapproved" and '[cheap implants]' in f["detail"] and f["nextStep"] == "Start with: Fix the disapproved keywords."
    s = [x for x in a["plan"]["steps"] if x["id"] == "keywords"][0]
    assert "healthcare and medicines (1), trademarks in ad text (1)" in s["why"] and "Appeal in Policy manager" in " ".join(s["how"]) and s["campaigns"] == ["Core Search"]


def test_an_account_with_only_disapproved_keywords_is_disapproved_and_not_stopped():
    a = m.analyse([kw(1)], {"G1": 0}, None)
    assert a["status"] == "disapproved" and a["counts"]["disapproved"] == 0 and a["counts"]["keywords"] == 1 and [f["kind"] for f in a["findings"]] == ["keywords"]


def test_a_meta_ad_is_an_ad_with_the_same_cover_rule():
    ma = dict(ad(1, "meta:55", "Spring leads", name="Ad set 55"), kind="meta", platform="meta", type="META_AD", url="")
    a = m.analyse([ma], {"meta:55": 0}, None)
    assert a["items"][0]["state"] == "dead" and a["items"][0]["platform"] == "meta" and a["counts"]["meta"] == 1


# ── Age, what they share and coming back ────────────────────────────────────

NOW = __import__("datetime").datetime(2026, 10, 21, 7, 0, tzinfo=__import__("datetime").timezone.utc)


def ages(**kw_):
    return {fp: {"firstSeen": since, "reopened": n} for fp, (since, n) in kw_.items()}


def test_a_stopped_group_and_a_policy_carry_how_long_they_have_been_open_and_the_clicks_lost_since():
    a = m.analyse([ad(1, "G1")], {"G1": 0}, W, account_id="111", ages={"group:111:G1": {"firstSeen": "2026-10-14T07:00:00+00:00", "reopened": 0}, "topic:111:DESTINATION_NOT_WORKING": {"firstSeen": "2026-10-17T07:00:00+00:00", "reopened": 2}}, now=NOW)
    g = a["deadGroups"][0]
    assert g["fp"] == "group:111:G1" and g["ageDays"] == 7 and g["lostSoFar"] == 100 and a["agesKnown"] is True          # 100 clicks a week, 7 days
    t = a["topics"][0]
    assert t["fp"] == "topic:111:DESTINATION_NOT_WORKING" and t["ageDays"] == 4 and t["reopened"] == 2
    assert "The longest has been stopped for 7 days (about 100 clicks lost since)." in a["findings"][0]["detail"]
    cover = a["plan"]["steps"][0]
    assert "The oldest has been stopped for 7 days: about 100 clicks lost since, at the usual pace." in cover["worth"]
    topic = [s for s in a["plan"]["steps"] if s["id"].startswith("topic-")][0]
    assert "It has been flagged for 4 days." in topic["why"] and "It has come back 2 times after being fixed" in topic["why"]
    rec = [f for f in a["findings"] if f["kind"] == "recurring"][0]
    assert rec["title"] == "Destination not working has come back 2 times after it was fixed" and rec["level"] == "medium"


def test_without_ages_nothing_is_said_about_age_and_a_limited_topic_has_its_own_fingerprint():
    a = m.analyse([ad(1, "G1")], {"G1": 0}, W, account_id="111")
    assert a["deadGroups"][0]["ageDays"] is None and a["deadGroups"][0]["lostSoFar"] is None and a["agesKnown"] is False and "stopped for" not in a["findings"][0]["detail"] and "lost since" not in a["plan"]["steps"][0]["worth"]
    lim = m.analyse([ad(1, approval="APPROVED_LIMITED")], {"G1": 1}, None, account_id="111")
    assert lim["topics"][0]["fp"] == "limited:111:DESTINATION_NOT_WORKING"
    new = m.analyse([ad(1, "G1")], {"G1": 0}, W, account_id="111", ages={}, now=NOW)
    assert new["deadGroups"][0]["ageDays"] is None and new["agesKnown"] is True


def test_an_issue_first_seen_today_has_no_age_to_say():
    a = m.analyse([ad(1, "G1")], {"G1": 0}, W, account_id="111", ages={"group:111:G1": {"firstSeen": "2026-10-21T06:00:00+00:00", "reopened": 0}}, now=NOW)
    assert a["deadGroups"][0]["ageDays"] == 0 and a["deadGroups"][0]["lostSoFar"] is None
    assert "stopped for" not in a["findings"][0]["detail"]


def flagged(n, headline="Book today", url="https://example.com/a", **kw_):
    return [dict(ad(i, "G%d" % i, **kw_), headlines=[headline, "Unique %d" % i], url=url) for i in range(1, n + 1)]


def test_a_headline_in_most_of_the_flagged_ads_is_the_one_cause_to_fix_once():
    a = m.analyse(flagged(6), {"G%d" % i: 1 for i in range(1, 7)}, None)
    assert a["suspects"][0] == {"kind": "headline", "text": "Book today", "ads": 6, "of": 6} and {"kind": "url", "text": "https://example.com/a", "ads": 6, "of": 6} in a["suspects"]
    f = [x for x in a["findings"] if x["kind"] == "common"][0]
    assert f["level"] == "medium" and f["title"] == 'The headline "Book today" is in 6 of the 6 flagged ads that have one' and f["nextStep"] == "Start with: Fix the shared cause once."
    s = [x for x in a["plan"]["steps"] if x["id"] == "common"][0]
    assert s["why"].startswith('Among the flagged ads, the headline "Book today" is in 6 of 6; the page https://example.com/a is in 6 of 6.') and "Find and replace" in " ".join(s["how"]) and s["worth"] == "Up to 6 of the 6 flagged ads could clear with this one change."


def test_a_text_in_too_few_ads_or_too_small_a_share_is_not_a_suspect_and_a_url_needs_three():
    few = flagged(2, headline="Book today", url="https://x.example/")
    assert [x["kind"] for x in m.analyse(few, {}, None)["suspects"]] == ["headline"]                          # two ads: a headline counts, a page needs three
    many = [dict(ad(i, "G%d" % i), headlines=["Head %d" % i], url="https://example.com/%d" % i) for i in range(1, 11)]
    many[0]["headlines"] = ["Shared"]
    many[1]["headlines"] = ["Shared"]
    assert m.analyse(many, {}, None)["suspects"] == []                                                         # 2 of 10 is under the share
    assert [f for f in m.analyse(flagged(2), {}, None)["findings"] if f["kind"] == "common"] == []              # and under three flagged ads nothing is said


def test_the_text_google_points_at_is_a_suspect_too_and_a_keyword_is_never_counted_in_them():
    ads = [ad(i, "G%d" % i, evidence=("Free!!!",)) for i in range(1, 5)] + [kw(9)]
    a = m.analyse(ads, {}, None)
    assert {"kind": "evidence", "text": "Free!!!", "ads": 4, "of": 4} in a["suspects"] and all(s["kind"] != "headline" or "cheap" not in s["text"] for s in a["suspects"])
    assert max(s["of"] for s in a["suspects"]) == 4


def test_at_most_four_suspects_the_most_shared_first():
    ads = [dict(ad(i, "G%d" % i, evidence=("E1", "E2")), headlines=["H1", "H2", "H3"], url="https://example.com/") for i in range(1, 5)]
    sus = m.analyse(ads, {}, None)["suspects"]
    assert len(sus) == 4 and all(s["ads"] == 4 for s in sus)


def test_how_often_it_came_back_is_known_even_when_the_time_of_the_read_is_not():
    a = m.analyse([ad(1, "G1")], {"G1": 0}, W, account_id="111", ages={"group:111:G1": {"firstSeen": "2026-10-14T07:00:00+00:00", "reopened": 3}}, now=None)
    g = a["deadGroups"][0]
    assert g["ageDays"] is None and g["reopened"] == 3 and a["agesKnown"] is False
    b = m.analyse([ad(1, "G1")], {"G1": 0}, W, account_id="111", ages={"group:111:G1": {"reopened": 1}}, now=NOW)
    assert b["deadGroups"][0]["ageDays"] is None and b["deadGroups"][0]["reopened"] == 1


def test_the_most_shared_suspect_comes_first_whatever_the_alphabet_says():
    ads = [dict(ad(i, "G%d" % i), headlines=(["Alpha"] if i <= 3 else []) + ["Zulu"], url="") for i in range(1, 6)]
    sus = m.analyse(ads, {}, None)["suspects"]
    assert [(s["text"], s["ads"]) for s in sus] == [("Zulu", 5), ("Alpha", 3)]


def test_a_shared_cause_in_four_ads_is_low_and_in_five_is_medium():
    low = m.analyse(flagged(4), {}, None)
    assert [f["level"] for f in low["findings"] if f["kind"] == "common"] == ["low"]
    assert [f["level"] for f in m.analyse(flagged(5), {}, None)["findings"] if f["kind"] == "common"] == ["medium"]


def test_a_stopped_group_that_came_back_is_named_and_the_one_that_came_back_most_is_the_one_said():
    a = m.analyse([ad(1, "G1"), ad(2, "G2", "Brand", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED"),))], {"G1": 0, "G2": 1}, W, account_id="111",
                  ages={"group:111:G1": {"firstSeen": "2026-10-14T07:00:00+00:00", "reopened": 1}, "topic:111:TRADEMARKS_IN_AD_TEXT": {"firstSeen": "2026-10-14T07:00:00+00:00", "reopened": 3},
                                                         "topic:111:DESTINATION_NOT_WORKING": {"firstSeen": "2026-10-14T07:00:00+00:00", "reopened": 2}}, now=NOW)
    rec = [f for f in a["findings"] if f["kind"] == "recurring"]
    assert len(rec) == 1 and rec[0]["title"] == "Trademarks in ad text has come back 3 times after it was fixed"            # the topics are listed first, so the first of them is not the worst
    only = m.analyse([ad(1, "G1")], {"G1": 0}, W, account_id="111", ages={"group:111:G1": {"firstSeen": "2026-10-14T07:00:00+00:00", "reopened": 1}}, now=NOW)
    assert [f["title"] for f in only["findings"] if f["kind"] == "recurring"] == ["Group G1 has come back 1 time after it was fixed"]
