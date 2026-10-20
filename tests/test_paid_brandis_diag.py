"""Brand IS Monitor: reading what the brand campaigns have, and rebuilding the plan from it (tracker/paid_brandis_diag.py, paid_brandis_plan.refine). No network."""
import pytest

from tests.test_paid_brandis_plan import account, ids, row, step, text
from tracker import paid_brandis_diag as d
from tracker import paid_brandis_plan as p
from tracker import paid_gads_segments as gads


# ── The answers ─────────────────────────────────────────────────────────────

def camp_row(cid="1", name="P2_Brand", status="ELIGIBLE", reasons=(), strategy="TARGET_IMPRESSION_SHARE", portfolio=False, cpa=None, roas=None, budget="500000000", shared=False):
    c = {"id": cid, "name": name, "primaryStatus": status, "primaryStatusReasons": list(reasons), "servingStatus": "SERVING", "biddingStrategyType": strategy,
         "biddingStrategySystemStatus": "ENABLED"}
    if portfolio:
        c["biddingStrategy"] = "customers/1/biddingStrategies/9"
    if cpa:
        c["targetCpa"] = {"targetCpaMicros": cpa}
    if roas:
        c["targetRoas"] = {"targetRoas": roas}
    return {"campaign": c, "campaignBudget": {"amountMicros": budget, "explicitlyShared": shared}}


def set_row(cid="1", location="ANYWHERE_ON_PAGE", fraction="600000", ceiling="1500000", spend=None, ecpc=None):
    c = {"id": cid, "targetImpressionShare": {"location": location, "locationFractionMicros": fraction, "cpcBidCeilingMicros": ceiling}}
    if spend:
        c["targetSpend"] = {"cpcBidCeilingMicros": spend}
    if ecpc is not None:
        c["manualCpc"] = {"enhancedCpcEnabled": ecpc}
    return {"campaign": c}


def kw_row(cid="1", ag="10", crit="100", text="acme dental", match="EXACT", qs=8, ctr="AVERAGE", rel="AVERAGE", lp="AVERAGE", serving="ELIGIBLE", approval="APPROVED"):
    k = {"criterionId": crit, "keyword": {"text": text, "matchType": match}, "status": "ENABLED", "approvalStatus": approval, "systemServingStatus": serving,
         "qualityInfo": {"searchPredictedCtr": ctr, "creativeQualityScore": rel, "postClickQualityScore": lp}}
    if qs:
        k["qualityInfo"]["qualityScore"] = qs
    return {"campaign": {"id": cid}, "adGroup": {"id": ag}, "adGroupCriterion": k}


def num_row(cid="1", ag="10", crit="100", impr="500"):
    return {"campaign": {"id": cid}, "adGroup": {"id": ag}, "adGroupCriterion": {"criterionId": crit}, "metrics": {"impressions": impr}}


def ad_row(cid="1", kind="RESPONSIVE_SEARCH_AD", strength="GOOD", approval="APPROVED", topics=()):
    return {"campaign": {"id": cid}, "adGroupAd": {"status": "ENABLED", "adStrength": strength, "ad": {"id": "5", "type": kind},
                                                   "policySummary": {"approvalStatus": approval, "policyTopicEntries": [{"topic": t} for t in topics]}}}


def test_a_campaign_is_read_with_its_status_bidding_target_and_budget():
    out = d.parse_campaigns([camp_row(status="LIMITED", reasons=["BUDGET_CONSTRAINED"], strategy="TARGET_CPA", cpa="40000000", portfolio=True, shared=True)])["1"]
    assert out == {"name": "P2_Brand", "primaryStatus": "LIMITED", "reasons": ["BUDGET_CONSTRAINED"], "serving": "SERVING", "bidding": "TARGET_CPA", "biddingStatus": "ENABLED",
                   "portfolio": True, "targetKind": "cpa", "target": 40.0, "budget": 500.0, "budgetShared": True}
    roas = d.parse_campaigns([camp_row(strategy="TARGET_ROAS", roas=4.256)])["1"]
    assert roas["targetKind"] == "roas" and roas["target"] == 4.26 and roas["portfolio"] is False
    mc = d.parse_campaigns([dict(camp_row(strategy="MAXIMIZE_CONVERSIONS"), campaign=dict(camp_row(strategy="MAXIMIZE_CONVERSIONS")["campaign"], maximizeConversions={"targetCpaMicros": "25000000"}))])["1"]
    assert mc["targetKind"] == "cpa" and mc["target"] == 25.0
    assert d.parse_campaigns([camp_row(strategy="MAXIMIZE_CONVERSIONS")])["1"]["targetKind"] == "" and d.parse_campaigns([{"campaign": {}}]) == {}


def test_the_bid_settings_are_read_as_shares_and_money_and_a_missing_one_is_none():
    s = d.parse_settings([set_row(spend="2000000", ecpc=True)])["1"]
    assert s == {"tisLocation": "ANYWHERE_ON_PAGE", "tisTarget": 0.6, "tisCeiling": 1.5, "spendCeiling": 2.0, "enhancedCpc": True}
    none = d.parse_settings([{"campaign": {"id": "2"}}])["2"]
    assert none == {"tisLocation": None, "tisTarget": None, "tisCeiling": None, "spendCeiling": None, "enhancedCpc": None}


def test_keywords_weak_when_the_score_is_six_or_less_or_a_part_is_below_average_busiest_first_and_rarely_served_named():
    rows = [kw_row(crit="1", text="acme", qs=7), kw_row(crit="2", text="acme dental", qs=6), kw_row(crit="3", text="acme dentist", qs=9, ctr="BELOW_AVERAGE"),
            kw_row(crit="4", text="acme x", qs=None, serving="RARELY_SERVED", approval="DISAPPROVED")]
    nums = [num_row(crit="1", impr="900"), num_row(crit="2", impr="100"), num_row(crit="3", impr="300")]
    k = d.parse_keywords(rows, nums)["1"]
    assert k["enabled"] == 4 and k["scored"] == 3 and k["rarelyServed"] == ["acme x"] and k["disapproved"] == ["acme x"]
    assert [w["text"] for w in k["weak"]] == ["acme dentist", "acme dental"] or [w["text"] for w in k["weak"]][0] == "acme dentist"
    assert k["weak"][0]["expectedCtr"] == "BELOW_AVERAGE" and k["weak"][0]["impressions"] == 300 and k["weak"][0]["qs"] == 9
    assert "acme" not in [w["text"] for w in k["weak"]]                                                  # a score of 7 with nothing below average is fine
    assert d.parse_keywords([kw_row(qs=6)], None)["1"]["weak"][0]["impressions"] == 0                    # the numbers could not be read: still named


def test_only_the_busiest_keywords_are_kept():
    rows = [kw_row(crit=str(i), text="k%d" % i, qs=3) for i in range(10)]
    nums = [num_row(crit=str(i), impr=str(100 + i)) for i in range(10)]
    k = d.parse_keywords(rows, nums)["1"]
    assert len(k["weak"]) == d.KEYWORDS_SHOWN and k["weak"][0]["text"] == "k9"


def test_ads_are_counted_by_strength_with_disapproved_and_limited_and_their_topics():
    rows = [ad_row(strength="GOOD"), ad_row(strength="POOR"), ad_row(strength="POOR", approval="DISAPPROVED", topics=["TRADEMARKS"]), ad_row(kind="EXPANDED_TEXT_AD"),
            ad_row(approval="APPROVED_LIMITED"), dict(ad_row(), adGroupAd={"status": "PAUSED", "ad": {"type": "RESPONSIVE_SEARCH_AD"}})]
    a = d.parse_ads(rows[:5] + [{"campaign": {"id": "1"}, "adGroupAd": {"status": "ENABLED", "ad": {"type": "RESPONSIVE_SEARCH_AD"}}}])["1"]
    assert a["ads"] == 6 and a["rsa"] == 5 and a["strength"] == {"GOOD": 2, "POOR": 2, "UNSPECIFIED": 1} and a["disapproved"] == 1 and a["limited"] == 1 and a["topics"] == ["TRADEMARKS"]


# ── The read ────────────────────────────────────────────────────────────────

def searcher(log=None, fail=()):
    def search(cid, q):
        if log is not None:
            log.append((cid, q))
        for key in fail:
            if key in q:
                raise gads.GadsError("api", "no %s" % key)
        if "FROM keyword_view" in q:
            return [num_row()]
        if "FROM ad_group_criterion" in q:
            return [kw_row(qs=4)]
        if "FROM ad_group_ad" in q:
            return [ad_row()]
        if "target_impression_share" in q:
            return [set_row()]
        return [camp_row()]
    return search


def test_the_read_asks_for_the_brand_campaigns_only_and_puts_the_facts_together():
    log = []
    out = d.read("1234567890", ["1", "3", "abc", " 2 "], search_fn=searcher(log))
    assert len(log) == 5 and all(c == "1234567890" and "campaign.id IN (1, 2, 3)" in q for c, q in log)
    f = out["campaigns"]["1"]
    assert out["warnings"] == [] and f["bidding"] == "TARGET_IMPRESSION_SHARE" and f["settings"]["tisTarget"] == 0.6 and f["keywords"]["weak"][0]["text"] == "acme dental"
    assert f["adFacts"]["rsa"] == 1


def test_no_campaign_is_not_a_read():
    with pytest.raises(ValueError):
        d.read("1234567890", ["x", ""], search_fn=searcher())


def test_a_part_that_fails_is_a_warning_that_leaves_it_out_and_the_campaign_settings_failing_is_raised():
    out = d.read("1234567890", ["1"], search_fn=searcher(fail=("target_impression_share", "FROM ad_group_ad")))
    f = out["campaigns"]["1"]
    assert f["settings"] is None and f["adFacts"] is None and f["keywords"]["enabled"] == 1
    assert sorted(w.split(" could")[0] for w in out["warnings"]) == ["The bid settings", "The brand ads"]
    with pytest.raises(gads.GadsError):
        d.read("1234567890", ["1"], search_fn=searcher(fail=("FROM campaign WHERE",)))


def test_a_campaign_with_no_keywords_or_ads_in_the_answer_reads_as_zero_not_unknown():
    def search(cid, q):
        return [camp_row()] if "FROM campaign WHERE campaign.id IN" in q and "target_impression_share" not in q else []
    f = d.read("1234567890", ["1"], search_fn=search)["campaigns"]["1"]
    assert f["keywords"]["enabled"] == 0 and f["adFacts"]["ads"] == 0 and f["settings"] is None or f["settings"] == {}


# ── The plan rebuilt from them ──────────────────────────────────────────────

def facts(**kw):
    base = {"name": "P2_Brand", "primaryStatus": "ELIGIBLE", "reasons": [], "serving": "SERVING", "bidding": "TARGET_IMPRESSION_SHARE", "biddingStatus": "ENABLED", "portfolio": False,
            "targetKind": "", "target": None, "budget": 500.0, "budgetShared": False,
            "settings": {"tisLocation": "ABSOLUTE_TOP_OF_PAGE", "tisTarget": 0.95, "tisCeiling": 8.0, "spendCeiling": None, "enhancedCpc": None},
            "keywords": {"enabled": 5, "scored": 5, "rarelyServed": [], "disapproved": [], "weak": []},
            "adFacts": {"ads": 3, "rsa": 3, "strength": {"GOOD": 3}, "disapproved": 0, "limited": 0, "topics": []}}
    base.update(kw)
    return {"1": base}


def rank_account(strategy="TARGET_IMPRESSION_SHARE", **kw):
    return account([row(share=0.60, lb=0.0, lr=0.30, strategy=strategy, clicks=1000, cost=3500.0, **kw)])        # $3.50 a click


def test_a_target_impression_share_set_too_low_anywhere_on_the_page_with_a_tight_limit_is_named_for_what_it_is():
    a = rank_account()
    r = p.refine(a, facts(settings={"tisLocation": "ANYWHERE_ON_PAGE", "tisTarget": 0.6, "tisCeiling": 3.5, "spendCeiling": None, "enhancedCpc": None}), "USD")
    s = step(r, "bids")
    assert r["diagnosed"] is True and "target impression share 60%, anywhere on page, bid limit $3.50" in s["why"]
    assert s["how"][0] == "This is what is wrong in these campaigns:" and "raise the target from 60% to 90% to 95%." in s["how"][1]
    assert any("counts any place on the page" in h for h in s["how"]) and any("raise it to about $7.00" in h for h in s["how"]) and "Then the general steps:" in s["how"]
    assert len(s["found"]) == 3


def test_a_target_impression_share_that_looks_right_says_so_and_points_at_quality_and_competition():
    s = step(p.refine(rank_account(), facts(), "USD"), "bids")
    assert s["how"][0].startswith("The target, the location and the bid limit look right") and s["level"] == "medium" and "found" not in s
    assert "What Google Ads shows: P2_Brand: target impression share 95%, absolute top of page, bid limit $8.00." in s["why"]


def test_a_limit_exactly_one_point_two_times_the_cpc_is_not_called_tight_and_just_under_is():
    ok = step(p.refine(rank_account(), facts(settings={"tisLocation": "TOP_OF_PAGE", "tisTarget": 0.9, "tisCeiling": 4.2, "spendCeiling": None, "enhancedCpc": None}), "USD"), "bids")
    assert "found" not in ok
    tight = step(p.refine(rank_account(), facts(settings={"tisLocation": "TOP_OF_PAGE", "tisTarget": 0.9, "tisCeiling": 4.19, "spendCeiling": None, "enhancedCpc": None}), "USD"), "bids")
    assert len(tight["found"]) == 1
    low = step(p.refine(rank_account(), facts(settings={"tisLocation": "TOP_OF_PAGE", "tisTarget": 0.8999, "tisCeiling": None, "spendCeiling": None, "enhancedCpc": None}), "USD"), "bids")
    assert "from 90% to" in low["found"][0]


def test_smart_bidding_targets_are_quoted_and_a_shared_strategy_is_warned_about():
    cpa = step(p.refine(rank_account("TARGET_CPA"), facts(bidding="TARGET_CPA", targetKind="cpa", target=40.0, portfolio=True), "USD"), "bids")
    assert "the target CPA is $40.00. Raise it by about 30% (to about $52.00)" in " ".join(cpa["how"]) and "shared (portfolio) bid strategy" in " ".join(cpa["how"])
    roas = step(p.refine(rank_account("TARGET_ROAS"), facts(bidding="TARGET_ROAS", targetKind="roas", target=5.0), "USD"), "bids")
    assert "target ROAS is 5.00. Lower it by about 30% (to about 3.50)" in " ".join(roas["how"])
    plain = step(p.refine(rank_account("MAXIMIZE_CONVERSIONS"), facts(bidding="MAXIMIZE_CONVERSIONS"), "USD"), "bids")
    assert "no target" in plain["why"] and "found" not in plain


def test_maximize_clicks_and_manual_cpc_say_what_they_have():
    spend = step(p.refine(rank_account("TARGET_SPEND"), facts(bidding="TARGET_SPEND", settings={"spendCeiling": 3.5, "tisTarget": None}), "USD"), "bids")
    assert "maximum CPC limit ($3.50) is close to last week's average CPC ($3.50)" in " ".join(spend["how"])
    none = step(p.refine(rank_account("TARGET_SPEND"), facts(bidding="TARGET_SPEND", settings={"spendCeiling": None}), "USD"), "bids")
    assert "no maximum CPC limit" in " ".join(none["how"]) and "bid limit none set" in none["why"]
    manual = step(p.refine(rank_account("MANUAL_CPC"), facts(bidding="MANUAL_CPC", settings={"enhancedCpc": True}), "USD"), "bids")
    assert "manual CPC, enhanced CPC on" in manual["why"]


def test_the_status_google_reports_becomes_the_first_step_with_a_fix_for_each_reason():
    a = rank_account()
    r = p.refine(a, facts(primaryStatus="LIMITED", reasons=["BIDDING_STRATEGY_LEARNING", "HAS_ADS_DISAPPROVED"]), "USD")
    first = r["plan"]["steps"][0]
    assert first["id"] == "status" and first["n"] == 1 and first["level"] == "high" and [s["n"] for s in r["plan"]["steps"]] == list(range(1, len(r["plan"]["steps"]) + 1))
    assert "limited (the bidding strategy is still learning after a recent change; some ads are disapproved)" in first["why"]
    h = " ".join(first["how"])
    assert "Status column" in h and "one to two weeks" in h and "fix or appeal each disapproved ad" in h and first["campaigns"] == ["P2_Brand"]
    learning = p.refine(a, facts(primaryStatus="LEARNING", reasons=["BIDDING_STRATEGY_LEARNING"]), "USD")["plan"]["steps"][0]
    assert learning["level"] == "medium"
    unknown = p.refine(a, facts(primaryStatus="NOT_ELIGIBLE", reasons=["SOMETHING_NEW"]), "USD")["plan"]["steps"][0]
    assert "something new" in unknown["why"].lower() and "follow the message under its status" in " ".join(unknown["how"])
    assert p.refine(a, facts(), "USD")["plan"]["steps"][0]["id"] != "status"


def test_an_eligible_campaign_adds_no_status_step_and_neither_does_an_account_on_target():
    ok = account([row(share=0.97, lb=0.0, lr=0.0)])
    r = p.refine(ok, facts(primaryStatus="LIMITED", reasons=["BUDGET_CONSTRAINED"]), "USD")
    assert r["plan"]["steps"] == [] and r["diagnosed"] is True


def test_a_shared_budget_is_named_and_a_budget_known_not_to_be_shared_drops_the_advice_about_it():
    a = account([row(share=0.70, lb=0.25, lr=0.0)])
    shared = step(p.refine(a, facts(budgetShared=True), "USD"), "budget")
    assert "use" in shared["why"] and "uses a shared budget" in shared["why"] and "Give P2_Brand a budget of its own" in shared["how"][1]
    alone = step(p.refine(a, facts(), "USD"), "budget")
    assert not any("shared budget" in h for h in alone["how"]) and "shared budget" not in alone["why"]


def test_the_quality_step_names_the_weak_keywords_and_ads_and_says_when_quality_is_not_the_cause():
    weak = [{"text": "acme dental", "match": "EXACT", "qs": 4, "expectedCtr": "BELOW_AVERAGE", "adRelevance": "AVERAGE", "landingPage": "BELOW_AVERAGE", "impressions": 900}]
    r = p.refine(rank_account(), facts(keywords={"enabled": 5, "scored": 5, "rarelyServed": ["acme x"], "disapproved": [], "weak": weak},
                                       adFacts={"ads": 3, "rsa": 3, "strength": {"POOR": 1, "AVERAGE": 1, "GOOD": 1}, "disapproved": 0, "limited": 0, "topics": []}), "USD")
    s = step(r, "relevance")
    assert '"acme dental" (Quality Score 4, below average: expected CTR, landing page)' in s["why"] and "1 rarely served" in s["why"] and "2 of 3 responsive search ads rated Poor or Average" in s["why"]
    assert s["how"][0].startswith("Start with the keywords and ads named above") and s["level"] == "medium"
    fine = step(p.refine(rank_account(), facts(), "USD"), "relevance")
    assert fine["how"][0].startswith("Quality does not look like the cause") and fine["level"] == "low" and "none with a weak Quality Score" in fine["why"]


def test_unread_parts_leave_the_step_as_it_was_and_the_facts_describe_each_campaign():
    a = rank_account("MANUAL_CPC")
    base = step(a, "relevance")
    r = p.refine(a, facts(bidding="MANUAL_CPC", keywords=None, adFacts=None), "USD")
    assert step(r, "relevance") == base
    lines = r["facts"][0]["lines"]
    assert r["facts"][0]["name"] == "P2_Brand" and lines[0] == "Status: eligible" and lines[1].startswith("Bidding: manual cpc") and lines[2] == "Budget: $500 a day"
    full = p.refine(rank_account(), facts(primaryStatus="LIMITED", reasons=["BUDGET_CONSTRAINED"], budgetShared=True, portfolio=True,
                                          keywords={"enabled": 5, "scored": 5, "rarelyServed": ["x"], "disapproved": [], "weak": [{"text": "a", "qs": 3, "impressions": 1}]},
                                          adFacts={"ads": 3, "rsa": 3, "strength": {"GOOD": 2, "POOR": 1}, "disapproved": 1, "limited": 1, "topics": []}), "USD")["facts"][0]["lines"]
    assert full[0] == "Status: limited (google marks it limited by budget)" and "shared bid strategy" in full[1] and "target 95% absolute top of page, bid limit $8.00" in full[1]
    assert full[2] == "Budget: $500 a day, shared with other campaigns" and full[3] == "Keywords: 5 enabled, 1 weak, 1 rarely served" and full[4] == "Ads: 3 live (2 good, 1 poor), 1 disapproved, 1 limited"


def test_nothing_in_a_refined_plan_has_a_dash_or_a_stray_format_mark():
    for strategy in ("TARGET_IMPRESSION_SHARE", "TARGET_CPA", "TARGET_SPEND", "MANUAL_CPC"):
        r = p.refine(rank_account(strategy), facts(bidding=strategy, primaryStatus="LIMITED", reasons=["BUDGET_CONSTRAINED", "HAS_ADS_LIMITED_BY_POLICY"], budgetShared=True,
                                                   settings={"tisLocation": "ANYWHERE_ON_PAGE", "tisTarget": 0.5, "tisCeiling": 1.0, "spendCeiling": 1.0, "enhancedCpc": False}), "USD")
        for s in r["plan"]["steps"]:
            assert "—" not in text(s) and "–" not in text(s) and "%s" not in text(s) and "%%" not in text(s) and "%d" not in text(s), s["id"]
        assert all("—" not in line for f in r["facts"] for line in f["lines"])


def test_a_status_with_no_reason_is_still_a_step_and_an_eligible_one_with_a_reason_is_too():
    a = rank_account()
    plain = p.refine(a, facts(primaryStatus="LIMITED", reasons=[]), "USD")["plan"]["steps"][0]
    assert plain["id"] == "status" and "P2_Brand: limited." in plain["why"]
    odd = p.refine(a, facts(primaryStatus="ELIGIBLE", reasons=["BUDGET_CONSTRAINED"]), "USD")["plan"]["steps"][0]
    assert odd["id"] == "status" and "limited by budget" in odd["why"]


def test_rarely_served_keywords_alone_mean_quality_is_not_ruled_out():
    k = {"enabled": 5, "scored": 5, "rarelyServed": ["acme x"], "disapproved": [], "weak": []}
    s = step(p.refine(rank_account(), facts(keywords=k), "USD"), "relevance")
    assert not s["how"][0].startswith("Quality does not look like the cause") and s["how"][0].startswith("Start with") and s["level"] == "medium"


def test_keywords_alone_or_ads_alone_are_enough_to_say_something_and_the_three_busiest_weak_keywords_are_named():
    weak = [{"text": "kw%d" % i, "match": "EXACT", "qs": 3, "expectedCtr": None, "adRelevance": None, "landingPage": None, "impressions": 100 - i} for i in range(5)]
    kw_only = step(p.refine(rank_account(), facts(keywords={"enabled": 5, "scored": 5, "rarelyServed": [], "disapproved": [], "weak": weak}, adFacts=None), "USD"), "relevance")
    assert '"kw0" (Quality Score 3)' in kw_only["why"] and '"kw2"' in kw_only["why"] and '"kw3"' not in kw_only["why"] and "5 weak" in kw_only["why"] and "live ads" not in kw_only["why"]
    ads_only = step(p.refine(rank_account(), facts(keywords=None, adFacts={"ads": 2, "rsa": 2, "strength": {"POOR": 2}, "disapproved": 0, "limited": 0, "topics": []}), "USD"), "relevance")
    assert "2 live ads, 2 of 2 responsive search ads rated Poor or Average" in ads_only["why"] and "brand keywords" not in ads_only["why"]


def test_facts_for_other_campaigns_leave_the_bid_and_budget_steps_as_they_were():
    a = account([row(share=0.50, lb=0.25, lr=0.25)])
    before = {s["id"]: s for s in a["plan"]["steps"]}
    r = p.refine(a, {"999": facts()["1"]}, "USD")
    after = {s["id"]: s for s in r["plan"]["steps"]}
    assert after["budget"] == before["budget"] and after["bids"] == before["bids"] and r["facts"] == []
