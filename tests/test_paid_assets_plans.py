"""Asset Coverage Audit: how to fix each gap and each problem asset (tracker/paid_assets_plans.py, and the policy read in tracker/paid_assets.py).

What is pinned: that Google is asked why assets were disapproved (and only for the disapproved, a capped number, never failing the report), that the topics reach the
asset they belong to and the read that is kept is never changed by being analysed, which steps each kind and level of gap gets, and that the only thing a gap is set
against is what is known: the campaign's impressions.
"""
import copy

import pytest

from tests.test_paid_assets import (ACCT, FULL_SEARCH, TODAY, a, camp_row, link_row, many, metric_row, read, searcher, slot_of, sitelink)
from tracker import paid_assets_plans as pl

EM, EN = chr(0x2014), chr(0x2013)


def policy_row(aid, *topics):
    return {"asset": {"id": aid, "policySummary": {"approvalStatus": "DISAPPROVED", "policyTopicEntries": [{"topic": t, "type": ty} for t, ty in topics]}}}


BAD_CALLOUT = link_row("5", "CALLOUT", "c1", asset={"calloutAsset": {"calloutText": "Cures anxiety"}}, status="ENABLED", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"])


# ── The read ──────────────────────────────────────────────────────────────

def test_the_disapproved_ids_are_found_in_every_shape_the_read_keeps_them():
    camp = {"5": {"CALLOUT": {"ids": ["c1", "c2"]}, "SITELINK": {"ids": []}}}
    cust = {"CALL": {"ids": ["x9"]}}
    groups = [{"id": "g", "assets": {"HEADLINE": {"ids": ["h1"]}, "LOGO": {"ids": []}}}]
    assert a.disapproved_ids(camp, cust, groups) == ["c1", "c2", "h1", "x9"]
    assert a.disapproved_ids({}, None, []) == []


def test_policy_topics_are_deduplicated_ordered_by_seriousness_and_capped():
    rows = [policy_row("1", ("EDITORIAL", "LIMITED"), ("TRADEMARKS_IN_AD_TEXT", "PROHIBITED"), ("EDITORIAL", "LIMITED"), ("A", "LIMITED"), ("B", "LIMITED")),
            policy_row("2"), {"asset": {"id": "3"}}, {"asset": {"policySummary": {"policyTopicEntries": [{"topic": "X"}]}}}]
    out = a.read_policy(rows)
    assert out == {"1": [{"t": "TRADEMARKS_IN_AD_TEXT", "ty": "PROHIBITED"}, {"t": "EDITORIAL", "ty": "LIMITED"}, {"t": "A", "ty": "LIMITED"}]}


def test_google_is_asked_why_only_for_the_disapproved_assets():
    log = []
    raw = read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH + [BAD_CALLOUT], metrics=[metric_row("5")], asset_by_id=[policy_row("c1", ("HEALTH_IN_PERSONALIZED_ADS", "PROHIBITED"))], log=log)
    asks = [q for _c, q in log if "policy_summary" in q]
    assert len(asks) == 1 and "asset.id IN (c1)" in asks[0] and raw["policy"] == {"c1": [{"t": "HEALTH_IN_PERSONALIZED_ADS", "ty": "PROHIBITED"}]} and raw["ok"]["policy"]
    quiet = []
    clean = read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH, metrics=[metric_row("5")], log=quiet)
    assert not any("policy_summary" in q for _c, q in quiet) and clean["policy"] == {} and "policy" not in clean["ok"]            # nothing disapproved: nothing asked


def test_no_more_than_sixty_are_asked_for_and_a_failure_costs_only_the_reasons():
    rows = [link_row("5", "CALLOUT", "c%03d" % i, asset={"calloutAsset": {"calloutText": "t%d" % i}}, status="ENABLED", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"]) for i in range(80)]
    log = []
    read(campaigns=[camp_row("5")], campaign_assets=rows, log=log)
    asked = " ".join(q for _c, q in log if "policy_summary" in q)
    assert asked.count("c0") == a.POLICY_MAX
    raw = read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH + [BAD_CALLOUT], metrics=[metric_row("5")], fail=("asset_by_id",))
    assert raw["ok"]["policy"] is False and any("policy reasons for disapproved assets could not be read" in w for w in raw["warnings"]) and raw["policy"] == {}
    item = a.analyse_account(raw, TODAY)["items"][0]
    assert slot_of(item, "CALLOUT")["fix"]["steps"][0].startswith("Open Google Ads, then Tools, then Policy manager")             # the report still stands


# ── The topics reach the asset, and the read is left alone ────────────────

def raw_with_policy():
    return read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH + [BAD_CALLOUT], metrics=[metric_row("5")],
                asset_by_id=[policy_row("c1", ("HEALTH_IN_PERSONALIZED_ADS", "PROHIBITED"), ("PUNCTUATION_AND_SYMBOLS", "LIMITED"))])


def test_a_disapproved_asset_carries_the_topics_google_gave_and_the_slot_the_steps():
    item = a.analyse_account(raw_with_policy(), TODAY)["items"][0]
    s = slot_of(item, "CALLOUT")
    assert s["bad"] == [{"label": "Cures anxiety", "state": "disapproved", "why": [{"t": "HEALTH_IN_PERSONALIZED_ADS", "ty": "PROHIBITED"}, {"t": "PUNCTUATION_AND_SYMBOLS", "ty": "LIMITED"}]}]
    t = " ".join(s["fix"]["steps"])
    assert "Policy manager" in t and "Health in personalized ads:" in t and "Punctuation and symbols:" in t and "Appeal" in t
    assert "a" not in s["bad"][0]                                                                          # the id was only there to find the reasons


def test_analysing_the_kept_read_again_gives_the_same_answer():
    raw = raw_with_policy()
    before = copy.deepcopy(raw)
    first, second = a.analyse_account(raw, TODAY), a.analyse_account(raw, TODAY)
    assert first == second and raw == before                                                                 # the saved read is analysed on every view
    assert slot_of(second["items"][0], "CALLOUT")["bad"][0]["why"]


def test_account_assets_carry_their_reasons_too():
    cust = [link_row(None, "CALL", "x9", asset={"callAsset": {"phoneNumber": "555"}}, status="ENABLED", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"], level="customerAsset")]
    out = a.analyse_account(read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH, customer_assets=cust, asset_by_id=[policy_row("x9", ("PHONE_NUMBER_NOT_ALLOWED", "PROHIBITED"))]), TODAY)
    call = next(s for s in out["accountAssets"]["slots"] if s["key"] == "CALL")
    assert call["bad"][0]["why"][0]["t"] == "PHONE_NUMBER_NOT_ALLOWED" and "Phone number not allowed:" in " ".join(call["fix"]["steps"])


# ── What each problem needs ───────────────────────────────────────────────

def test_each_problem_gets_its_own_step_and_a_slot_with_none_gets_none():
    s = {"disapproved": 1, "expired": 2, "paused": 1, "pending": 1, "bad": []}
    t = pl.problem_fix(s)["steps"]
    assert "Policy manager" in t[0] and "did not name the policy" in t[1] and "Appeal" in t[2]
    assert "new end date" in t[3] and "choose Enable" in t[4] and "two business days" in t[5]
    assert pl.problem_fix({"disapproved": 0, "expired": 0, "paused": 0, "pending": 0}) is None
    only = pl.problem_fix({"expired": 1})["steps"]
    assert len(only) == 1 and "end date" in only[0]


@pytest.mark.parametrize("topic,word", [("DESTINATION_NOT_WORKING", "does not work"), ("TRADEMARKS_IN_AD_TEXT", "trademark"), ("PUNCTUATION_AND_SYMBOLS", "punctuation"),
                                        ("EXCESSIVE_CAPITALIZATION", "capital"), ("REPETITION", "repeats"), ("GIMMICKS", "gimmick"), ("HEALTH_CLAIMS", "Health claims"),
                                        ("PERSONALIZED_ADS_HEALTH", "personal"), ("MISLEADING_CONTENT", "mislead"), ("UNCLEAR_RELEVANCE", "cannot tell"),
                                        ("IMAGE_QUALITY", "image"), ("PHONE_NUMBER", "phone number"), ("SOMETHING_NEW", "Open the policy named")])
def test_a_policy_topic_is_met_with_what_usually_fixes_it(topic, word):
    assert word in pl.topic_hint(topic)


def test_topics_are_read_as_words_and_the_same_topic_is_said_once():
    assert pl.topic_name("HEALTH_IN_PERSONALIZED_ADS") == "Health in personalized ads"
    s = {"disapproved": 2, "bad": [{"state": "disapproved", "why": [{"t": "REPETITION"}]}, {"state": "disapproved", "why": [{"t": "REPETITION"}, {"t": "GIMMICKS"}]}]}
    t = pl.problem_fix(s)["steps"]
    assert sum(x.startswith("Repetition:") for x in t) == 1 and any(x.startswith("Gimmicks:") for x in t)


# ── What each gap needs ───────────────────────────────────────────────────

def gap(key, label, scope="Campaign", severity="high", have=0, add=4):
    return {"scope": scope, "key": key, "label": label, "severity": severity, "have": have, "target": 4, "add": add, "message": "x"}


CTX = {"name": "Search_Brand", "type": "SEARCH", "impressions": 12000, "share": 0.4}


def test_a_campaign_gap_says_where_to_click_what_to_write_and_where_to_attach_it():
    p = pl.gap_plan(gap("SITELINK", "Sitelinks"), CTX)
    t = " ".join(p["steps"])
    assert p["steps"][0] == 'Open Google Ads, then Campaigns, click "Search_Brand", and choose Assets in the left menu (under Ads & assets).'
    assert "choose Sitelink, and add 4." in t and "up to 25 characters" in t and "up to 35 each" in t and "choose this campaign; choose the account instead" in t and "reviewed first" in t
    more = pl.gap_plan(gap("SITELINK", "Sitelinks", have=1, add=3, severity="medium"), CTX)
    assert "add 3 more." in " ".join(more["steps"])


def test_every_kind_of_campaign_asset_has_a_how():
    from tracker import paid_assets_rules as rules
    for key in rules.CAMPAIGN_ASSET_ORDER + ("LOCATION",):
        p = pl.gap_plan(gap(key, key.title()), CTX)
        assert p and len(p["steps"]) == 3 and "Assets" in p["steps"][0], key
    for key in rules.PMAX_GROUP_ORDER:
        p = pl.gap_plan(gap(key, rules.PMAX_GROUP[key]["label"], scope="Asset group: Crowns"), CTX)
        assert p and "Asset groups" in p["steps"][0] and "pencil" in p["steps"][1], key
        assert pl.gap_plan(gap(key, "x", scope="Responsive display ad: Group"), CTX)["steps"][0].endswith("choose Ads in the left menu.")
    assert pl.gap_plan(gap("NOPE", "Nope"), CTX) is None and pl.gap_plan(gap("NOPE", "Nope", scope="Asset group: x"), CTX) is None


def test_the_level_of_a_gap_comes_from_its_scope():
    assert pl.level_of("Campaign") == "campaign" and pl.level_of("Asset group: Crowns") == "group" and pl.level_of("4 asset groups (A, B, C and 1 more)") == "group"
    assert pl.level_of("Responsive display ad: Implants") == "ad"


def test_a_performance_max_campaign_with_no_asset_group_is_told_to_build_one():
    p = pl.gap_plan(gap("ASSET_GROUPS", "Asset groups", have=0, add=1), dict(CTX, name="PMax"))
    assert p["steps"][1].startswith("Choose Asset groups in the left menu, then the +") and "at least 3 headlines" in p["steps"][1]


def test_a_gap_is_set_against_the_impressions_it_cost_and_never_a_made_up_gain():
    none = pl.gap_plan(gap("SITELINK", "Sitelinks"), CTX)["worth"]
    assert none == "The campaign had 12,000 impressions in the last 30 days (40% of what the audited campaigns had) with none live. Google does not publish what an asset adds, so there is no conversion estimate here."
    thin = pl.gap_plan(gap("SITELINK", "Sitelinks", have=2, add=2, severity="medium"), CTX)["worth"]
    assert "with fewer than the target" in thin
    assert pl.gap_plan(gap("PROMOTION", "Promotions", severity="low"), CTX)["worth"] is None
    assert "no impressions in the last 30 days, so settle why" in pl.gap_plan(gap("SITELINK", "Sitelinks"), dict(CTX, impressions=0))["worth"]
    assert "(" not in pl.gap_plan(gap("SITELINK", "Sitelinks"), dict(CTX, share=None))["worth"].split("impressions in the last 30 days")[1][:3]


# ── Through the report ────────────────────────────────────────────────────

def test_the_report_gives_every_gap_its_plan_with_the_campaigns_share_of_impressions():
    raw = read(campaigns=[camp_row("5", "Search_A"), camp_row("6", "Search_B")], metrics=[metric_row("5", 3000), metric_row("6", 1000)],
               campaign_assets=many("5", "SITELINK", 4) + many("6", "SITELINK", 1))
    rep = a.assemble({"accounts": [raw], "warnings": []}, ACCT, TODAY)
    b = next(i for i in rep["campaigns"] if i["name"] == "Search_B")
    g = next(x for x in b["gaps"] if x["key"] == "SITELINK")
    assert g["plan"]["steps"][0].startswith('Open Google Ads, then Campaigns, click "Search_B"') and "25% of what the audited campaigns had" in g["plan"]["worth"]
    assert all("plan" in x for i in rep["campaigns"] for x in i["gaps"] if x["key"] in pl.CAMPAIGN_KINDS)


def test_no_plan_uses_an_em_or_en_dash_or_leaves_a_placeholder():
    blobs = [repr(pl.gap_plan(gap(k, k), CTX)) for k in list(pl.CAMPAIGN_KINDS) + ["ASSET_GROUPS"]]
    blobs += [repr(pl.gap_plan(gap(k, k, scope=sc), CTX)) for k in pl.GROUP_KINDS for sc in ("Asset group: Crowns", "Responsive display ad: Group")]
    blobs += [repr(pl.problem_fix({"disapproved": 1, "expired": 1, "paused": 1, "pending": 1, "bad": [{"why": [{"t": k}]} for k, _ in pl.TOPIC_HINTS]}))]
    blobs += [repr(pl.TOPIC_HINTS)]
    for b in blobs:
        assert EM not in b and EN not in b and "None" not in b and "{" not in b.replace("{'", "").replace("'}", "") .replace("{\"", ""), b[:200]
