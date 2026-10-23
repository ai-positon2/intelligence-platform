"""Keyword Research for a Meta ad account (tracker/paid_keywords_meta.py): what the ad sets aim at, the interests Meta has for the phrases chosen, and the arithmetic on the judged interests."""
import datetime as dt

from tests.test_paid_meta_interests import CATALOGUE, getter, opt
from tracker import paid_keywords_meta as km

TODAY = dt.date(2026, 10, 21)
ACCT = {"id": "act_1234567", "name": "Acme Meta", "currency": "USD"}


def adset(sid, name, interests=(), behaviors=(), audiences=(), advantage=False):
    flex = {}
    if interests:
        flex["interests"] = [{"id": i, "name": n} for i, n in interests]
    if behaviors:
        flex["behaviors"] = [{"id": i, "name": n} for i, n in behaviors]
    t = {"flexible_spec": [flex] if flex else [], "custom_audiences": [{"id": "ca" + a, "name": a} for a in audiences]}
    if advantage:
        t["targeting_automation"] = {"advantage_audience": 1}
    return {"id": sid, "name": name, "campaign_id": "c1", "effective_status": "ACTIVE", "targeting": t}


def insight(sid, spend, leads, campaign="Leads"):
    return {"adset_id": sid, "adset_name": sid, "campaign_id": "c1", "campaign_name": campaign, "objective": "OUTCOME_LEADS", "spend": str(spend), "impressions": "9000",
            "inline_link_clicks": "100", "actions": [{"action_type": "lead", "value": str(leads)}]}


ADSETS = [adset("s1", "Pump users", [("1", "Insulin pump"), ("3", "Diabetes")]), adset("s2", "Travel", [("90", "Frequent travellers")], advantage=True), adset("s3", "Retarget", audiences=["Site visitors"])]
INSIGHTS = [insight("s1", 600, 20), insight("s2", 400, 0), insight("s3", 100, 5)]


def pages(adsets=ADSETS, insights=INSIGHTS, fail=()):
    def fn(path, params):
        if path.endswith("/adsets"):
            if "adsets" in fail:
                raise RuntimeError("Meta answered 400")
            return adsets
        if path.endswith("/insights"):
            if "insights" in fail:
                raise RuntimeError("Meta answered 500")
            return insights
        raise AssertionError(path)
    return fn


def test_the_account_is_read_as_ad_sets_with_what_they_aim_at_and_what_each_spent_and_brought():
    pc = km.read_meta_account(ACCT, pages_fn=pages(), today=TODAY)
    d = pc["data"]
    assert pc["state"] == "ok" and d["window"] == {"start": "2026-07-23", "end": "2026-10-20"} and d["adsetCount"] == 3
    s1, s2, s3 = d["adsets"]
    assert s1["interests"] == [{"id": "1", "name": "Insulin pump"}, {"id": "3", "name": "Diabetes"}] and s1["cost"] == 600.0 and s1["conv"] == 20.0 and s1["cpa"] == 30.0 and s1["campaign"] == "Leads"
    assert s2["advantage"] is True and s2["cpa"] is None and s3["audiences"] == ["Site visitors"] and s3["interests"] == []
    assert d["totals"] == {"cost": 1100.0, "conv": 25.0, "cpa": 44.0} and d["advantageAdsets"] == 1
    assert "3 ad sets" in pc["note"] and "3 interests and behaviors in use" in pc["note"]


def test_each_interest_in_use_is_judged_against_the_accounts_own_cost_per_result():
    d = km.read_meta_account(ACCT, pages_fn=pages(), today=TODAY)["data"]
    by = {u["id"]: u for u in d["inUse"]}
    assert [u["id"] for u in d["inUse"]][0] in ("1", "3")                                 # biggest spend first: s1's interests are 600 each, the travel behavior 400
    assert by["1"]["verdict"] == "works" and "30.00" in by["1"]["why"] and "44.00" in by["1"]["why"]
    assert by["90"]["verdict"] == "weak" and "400" in by["90"]["why"] and "no results" in by["90"]["why"]


def test_a_verdict_needs_enough_to_say_so_and_never_blames_a_small_spend():
    v = km.use_verdict
    assert v({"cost": 0, "conv": 0, "cpa": None}, 40)[0] == "idle"
    assert v({"cost": 30, "conv": 0, "cpa": None}, 10)[0] == "watch"                       # under the floor of 50
    assert v({"cost": 50, "conv": 0, "cpa": None}, 10)[0] == "weak"
    assert v({"cost": 70, "conv": 0, "cpa": None}, 40)[0] == "watch"                       # 70 is over 50 but under twice the usual 40 (80)
    assert v({"cost": 90, "conv": 0, "cpa": None}, 40)[0] == "weak"
    assert v({"cost": 100, "conv": 2, "cpa": 50}, 100)[0] == "watch"                       # two results are too few to compare
    assert v({"cost": 300, "conv": 3, "cpa": 100}, 100)[0] == "watch" and "usual" in v({"cost": 300, "conv": 3, "cpa": 100}, 100)[1]
    assert v({"cost": 240, "conv": 3, "cpa": 80}, 100)[0] == "works"                       # exactly 0.8 of the account's
    assert v({"cost": 450, "conv": 3, "cpa": 150}, 100)[0] == "weak"                        # exactly 1.5 times
    assert v({"cost": 300, "conv": 5, "cpa": 60}, None)[0] == "watch"                       # no account average: no comparison


def test_results_that_cannot_be_read_leave_the_targeting_and_say_so_and_ad_sets_that_cannot_be_read_fail_the_piece():
    pc = km.read_meta_account(ACCT, pages_fn=pages(fail=("insights",)), today=TODAY)
    assert pc["state"] == "partial" and "without results" in pc["note"] and all(u["verdict"] == "unknown" for u in pc["data"]["inUse"]) and pc["data"]["adsets"][0]["cost"] == 0.0
    pc = km.read_meta_account(ACCT, pages_fn=pages(fail=("adsets",)), today=TODAY)
    assert pc["state"] == "failed" and "ad sets" in pc["note"] and "400" in pc["note"]
    assert km.read_meta_account(ACCT, pages_fn=pages([], []), today=TODAY)["note"] == "The account has no ad sets."


SEEDS = {"phrases": ["insulin pump", "diabetes", "type 1"], "behaviorWords": [], "avoid": []}


def research():
    return {"metaaccount": km.read_meta_account(ACCT, pages_fn=pages(), today=TODAY)}


def test_the_interests_are_the_ones_meta_has_for_the_phrases_marked_with_those_already_in_use():
    pc = km.read_interests(SEEDS, research(), get_fn=getter())
    d = pc["data"]
    ids = [c["id"] for c in d["candidates"]]
    assert pc["state"] == "ok" and sorted(ids) == ["1", "2", "3", "4", "5"] and d["emptyPhrases"] == ["type 1"] and d["found"] == 5 and d["cut"] is False
    by = {c["id"]: c for c in d["candidates"]}
    assert by["1"]["inUse"]["adsets"] == 1 and by["2"]["inUse"] is None and by["5"]["from"] == "suggested by Meta"
    assert ids[:2] == ["1", "3"]                                                          # the ones in use come first


def test_meta_is_asked_for_suggestions_from_the_interests_in_use_and_the_top_result_of_each_phrase_and_for_behaviors_only_when_asked():
    calls = []
    km.read_interests(SEEDS, {}, get_fn=getter(calls))
    sugg = [p for _path, p in calls if p.get("type") == "adinterestsuggestion"]
    assert len(sugg) == 1 and __import__("json").loads(sugg[0]["interest_list"]) == ["Insulin pump", "Diabetes"]          # the first result of each phrase that found one
    assert not [p for _path, p in calls if p.get("class") == "behaviors"]
    calls.clear()
    km.read_interests(dict(SEEDS, behaviorWords=["diabetes"]), research(), get_fn=getter(calls))
    assert len([p for _path, p in calls if p.get("class") == "behaviors"]) == 1
    sugg = [p for _path, p in calls if p.get("type") == "adinterestsuggestion"]
    assert __import__("json").loads(sugg[0]["interest_list"])[:2] == ["Insulin pump", "Diabetes"]                        # the interests in use, biggest spend first


def test_behaviors_are_added_only_when_words_for_them_are_given():
    seeds = dict(SEEDS, behaviorWords=["diabetes"])
    d = km.read_interests(seeds, research(), get_fn=getter())["data"]
    assert "91" in {c["id"] for c in d["candidates"]} and "90" not in {c["id"] for c in d["candidates"]} and d["behaviors"] == 2


def test_no_interest_at_all_fails_the_piece_and_says_meta_keeps_none_for_some_topics():
    pc = km.read_interests({"phrases": ["type 1"]}, {}, get_fn=getter())
    assert pc["state"] == "failed" and '"type 1"' in pc["note"] and "health conditions" in pc["note"]
    assert km.read_interests({"phrases": []}, {}, get_fn=getter())["state"] == "failed"


def test_a_phrase_meta_does_not_answer_is_said_and_the_rest_are_kept():
    pc = km.read_interests(SEEDS, research(), get_fn=getter(fail=("diabetes",)))
    assert pc["state"] == "partial" and "diabetes" in pc["note"] and {c["id"] for c in pc["data"]["candidates"]} >= {"1", "2"}


def test_more_candidates_than_the_limit_are_cut_with_the_ones_in_use_kept():
    big = {"p%d" % i: [opt(str(1000 + i), "Interest %d" % i)] for i in range(20)}
    big["insulin pump"] = [opt("1", "Insulin pump")] + [opt(str(5000 + i), "Extra %d" % i) for i in range(120)]

    def get(path, params):
        if params.get("type") == "adinterest":
            return big.get(params["q"], [])
        return []
    pc = km.read_interests({"phrases": ["insulin pump"] + ["p%d" % i for i in range(20)]}, research(), get_fn=get)
    d = pc["data"]
    assert len(d["candidates"]) == km.MAX_CANDIDATES and d["cut"] is True and d["found"] == 141 and d["candidates"][0]["id"] == "1" and "the first 100 of 141" in pc["note"]


def test_the_judged_interests_become_rows_and_an_unjudged_one_says_so():
    cands = km.read_interests(SEEDS, research(), get_fn=getter())["data"]["candidates"]
    rows = km.assemble(cands, {"1": {"fit": "yes", "intent": "core", "theme": "Pump users", "note": "the product"}, "3": {"fit": "no", "intent": "broad", "theme": "", "note": "too wide"}})
    by = {r["id"]: r for r in rows}
    assert by["1"]["fit"] == "yes" and by["1"]["theme"] == "Pump users" and by["3"]["fit"] == "no" and by["2"]["fit"] == "unjudged" and by["2"]["note"] == ""


def test_coverage_counts_what_fits_what_is_new_and_the_money_going_to_interests_judged_not_a_fit():
    r = research()
    cands = km.read_interests(SEEDS, r, get_fn=getter())["data"]["candidates"]
    rows = km.assemble(cands, {"1": {"fit": "yes"}, "2": {"fit": "yes"}, "3": {"fit": "no"}, "4": {"fit": "maybe"}})
    cov = km.coverage(rows, r["metaaccount"]["data"]["inUse"])
    assert cov["judged"] == 4 and cov["unjudged"] == 1 and cov["yes"] == 2 and cov["new"] == 1 and cov["inUse"] == 3 and cov["inUseFit"] == 1
    assert cov["misfit"] == ["3"] and cov["misfitSpend"] == 600.0                          # Diabetes, in use in s1, judged not a fit
    assert cov["works"] == ["1", "3"] and cov["weak"] == ["90"]
