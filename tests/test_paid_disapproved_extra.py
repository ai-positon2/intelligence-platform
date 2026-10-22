"""Disapproved Ads Tracker: reading Performance Max assets, disapproved keywords and Meta's rejected ads (tracker/paid_disapproved_extra.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_disapproved as d  # noqa: E402
from tracker import paid_disapproved_extra as ex  # noqa: E402


def arow(gid="7", gname="Summer", aid="900", field="HEADLINE", text="Free whitening!!!", approval="DISAPPROVED", review="REVIEWED", camp="PMax All",
         topics=(("PUNCTUATION_AND_SYMBOLS", "LIMITED", ["Free!!!"]),)):
    return {"campaign": {"id": "5", "name": camp}, "assetGroup": {"id": gid, "name": gname}, "asset": {"id": aid, "type": "TEXT", "textAsset": {"text": text}},
            "assetGroupAsset": {"fieldType": field, "policySummary": {"approvalStatus": approval, "reviewStatus": review,
                                                                      "policyTopicEntries": [{"topic": t, "type": ty, "evidences": [{"textList": {"texts": ev}}]} for t, ty, ev in topics]}}}


# ── Performance Max ─────────────────────────────────────────────────────────

def test_a_flagged_asset_is_an_item_whose_group_is_its_asset_group_and_field():
    [a] = ex.read_assets([arow()])
    assert a == {"id": "asset:7:900:HEADLINE", "campaignId": "5", "campaign": "PMax All", "adGroupId": "pmax:7:HEADLINE", "adGroup": "Summer (headlines)", "type": "ASSET_HEADLINE",
                 "approval": "DISAPPROVED", "review": "REVIEWED", "topics": [{"t": "PUNCTUATION_AND_SYMBOLS", "ty": "LIMITED", "ev": ["Free!!!"]}], "headlines": ["Free whitening!!!"], "url": "",
                 "kind": "asset", "platform": "google", "need": 3}


@pytest.mark.parametrize("field, need, label", [("HEADLINE", 3, "headlines"), ("LONG_HEADLINE", 1, "long headlines"), ("DESCRIPTION", 2, "descriptions"), ("MARKETING_IMAGE", 1, "landscape images"),
                                                ("SQUARE_MARKETING_IMAGE", 1, "square images"), ("BUSINESS_NAME", 1, "business name"), ("LOGO", 0, "logos"), ("SOMETHING_NEW", 0, "something new")])
def test_each_field_needs_what_google_needs_and_is_called_by_its_name(field, need, label):
    [a] = ex.read_assets([arow(field=field)])
    assert a["need"] == need and a["adGroup"] == "Summer (%s)" % label and a["adGroupId"] == "pmax:7:" + field


def test_an_asset_with_no_text_is_named_by_its_name_or_its_type_and_a_row_missing_a_piece_is_dropped():
    img = arow(aid="901", field="MARKETING_IMAGE")
    img["asset"] = {"id": "901", "type": "IMAGE", "name": "hero.jpg"}
    bare = arow(aid="902", field="MARKETING_IMAGE")
    bare["asset"] = {"id": "902", "type": "IMAGE"}
    got = ex.read_assets([img, bare, arow(gid=""), arow(aid=""), arow(field=""), {}])
    assert [a["headlines"] for a in got] == [["hero.jpg"], ["image asset 902"]]


def test_the_asset_queries_ask_for_enabled_flagged_assets_their_cover_and_their_weeks():
    q = ex.GAQL_ASSETS
    assert "FROM asset_group_asset" in q and "asset_group_asset.status = ENABLED" in q and "IN ('DISAPPROVED', 'APPROVED_LIMITED', 'AREA_OF_INTEREST_ONLY')" in q
    for f in ("asset_group_asset.field_type", "policy_summary.policy_topic_entries", "asset.text_asset.text", "asset_group.name", "policy_summary.review_status"):
        assert f in q
    c = ex.gaql_asset_cover(["7", "8"])
    assert "asset_group.id IN (7, 8)" in c and "IN ('APPROVED', 'APPROVED_LIMITED', 'AREA_OF_INTEREST_ONLY')" in c and "asset_group_asset.field_type" in c
    w = ex.gaql_asset_weekly(["7"], dt.date(2026, 9, 14), dt.date(2026, 10, 18))
    assert "FROM asset_group WHERE" in w and "BETWEEN '2026-09-14' AND '2026-10-18'" in w and "asset_group.id IN (7)" in w and "segments.week" in w


def test_the_cover_counts_approved_assets_per_asset_group_and_field():
    rows = [{"assetGroup": {"id": "7"}, "assetGroupAsset": {"fieldType": "HEADLINE"}}] * 2 + [{"assetGroup": {"id": "7"}, "assetGroupAsset": {"fieldType": "DESCRIPTION"}}, {"assetGroup": {}, "assetGroupAsset": {}}]
    assert ex.read_asset_cover(rows) == {"pmax:7:HEADLINE": 2, "pmax:7:DESCRIPTION": 1}


def test_an_asset_groups_weeks_are_given_to_each_of_its_fields():
    keys = [w[0].isoformat() for w in d.weeks(dt.date(2026, 10, 21))]
    rows = [{"assetGroup": {"id": "7"}, "segments": {"week": keys[0]}, "metrics": {"clicks": "50", "costMicros": "20000000"}}, {"assetGroup": {"id": "9"}, "segments": {"week": keys[1]}, "metrics": {"clicks": "5", "costMicros": "1000000"}}]
    got = ex.read_asset_weekly(rows, d.weeks(dt.date(2026, 10, 21)), {"7": ["pmax:7:HEADLINE", "pmax:7:DESCRIPTION"]})
    assert sorted(got) == ["pmax:7:DESCRIPTION", "pmax:7:HEADLINE"] and got["pmax:7:HEADLINE"][0] == {"clicks": 50.0, "cost": 20.0} and got["pmax:7:HEADLINE"] is got["pmax:7:DESCRIPTION"]


# ── Keywords ────────────────────────────────────────────────────────────────

def krow(cid="11", gid="3", text="cheap implants", mt="EXACT", camp="Core Search", entries=({"topic": "HEALTHCARE_AND_MEDICINES", "type": "LIMITED"},)):
    return {"campaign": {"id": "5", "name": camp}, "adGroup": {"id": gid, "name": "Implants"},
            "adGroupCriterion": {"criterionId": cid, "keyword": {"text": text, "matchType": mt}, "approvalStatus": "DISAPPROVED", "policySummary": {"policyTopicEntries": list(entries)}}}


def test_a_disapproved_keyword_is_an_item_shown_with_its_match_type():
    got = ex.read_keywords([krow(), krow("12", mt="PHRASE"), krow("13", mt="BROAD"), krow("", text="x"), krow("14", text=""), krow("15", gid="")])
    assert [a["headlines"] for a in got] == [["[cheap implants]"], ['"cheap implants"'], ["cheap implants"]]
    a = got[0]
    assert a["id"] == "kw:3:11" and a["adGroupId"] == "3" and a["adGroup"] == "Implants" and a["kind"] == "keyword" and a["approval"] == "DISAPPROVED" and a["need"] == 0 and a["url"] == ""
    assert a["topics"] == [{"t": "HEALTHCARE_AND_MEDICINES", "ty": "LIMITED", "ev": []}] and a["type"] == "KEYWORD"


def test_a_keyword_whose_policy_google_would_not_give_has_no_topics():
    row = krow()
    del row["adGroupCriterion"]["policySummary"]
    assert ex.read_keywords([row])[0]["topics"] == []


def test_the_keyword_query_asks_for_positive_enabled_disapproved_keywords_without_the_policy_topics_google_refuses():
    q = ex.GAQL_KEYWORDS
    assert "ad_group_criterion.approval_status = DISAPPROVED" in q and "ad_group_criterion.negative = FALSE" in q and "ad_group_criterion.status = ENABLED" in q and "FROM ad_group_criterion" in q
    assert "policy_summary" not in q                                                            # "Unrecognized field in the query" on the live API
    assert not hasattr(ex, "GAQL_KEYWORDS_PLAIN")


# ── Meta ────────────────────────────────────────────────────────────────────

def mad(i="1", status="DISAPPROVED", adset="55", feedback=None, issues=None, name="Spring promo"):
    return {"id": i, "name": name, "effective_status": status, "adset": {"id": adset, "name": "Ad set %s" % adset}, "campaign": {"id": "9", "name": "Spring leads"},
            "ad_review_feedback": {"global": feedback if feedback is not None else {"ADULT_CONTENT": "Shows too much skin"}}, "issues_info": issues if issues is not None else []}


def test_the_meta_requests_ask_for_rejected_and_flagged_ads_and_for_the_active_ones():
    path, params = ex.meta_ads_request("act_123")
    assert path == "/act_123/ads" and params["effective_status"] == '["DISAPPROVED", "WITH_ISSUES"]' and "ad_review_feedback" in params["fields"] and "issues_info" in params["fields"] and params["limit"] == 200
    p2, q2 = ex.meta_active_request("act_123")
    assert p2 == "/act_123/ads" and q2["effective_status"] == '["ACTIVE"]' and q2["fields"] == "id,adset_id"


def test_a_rejected_ad_is_disapproved_and_a_flagged_one_is_limited_with_the_policies_meta_names():
    got = ex.read_meta_ads([mad(), mad("2", "WITH_ISSUES", feedback={}, issues=[{"error_code": 1487, "error_summary": "Ad can't run", "error_message": "The ad has a problem"}, {"error_summary": "Other issue", "error_message": ""}]),
                            mad("3", "ACTIVE"), mad("4", adset=""), {"effective_status": "DISAPPROVED"}])
    assert [(a["id"], a["approval"]) for a in got] == [("meta:1", "DISAPPROVED"), ("meta:2", "APPROVED_LIMITED")]
    a, b = got
    assert a["topics"] == [{"t": "ADULT_CONTENT", "ty": "", "ev": ["Shows too much skin"]}] and a["adGroupId"] == "meta:55" and a["adGroup"] == "Ad set 55" and a["campaign"] == "Spring leads" and a["headlines"] == ["Spring promo"]
    assert b["topics"] == [{"t": "ISSUE_1487", "ty": "", "ev": ["The ad has a problem"]}, {"t": "Other issue", "ty": "", "ev": ["Other issue"]}] and a["kind"] == "meta" and a["platform"] == "meta" and a["need"] == 1


def test_the_meta_cover_counts_active_ads_per_ad_set():
    assert ex.read_meta_cover([{"id": "1", "adset_id": "55"}, {"id": "2", "adset_id": "55"}, {"id": "3", "adset_id": "56"}, {"id": "4"}]) == {"meta:55": 2, "meta:56": 1}
