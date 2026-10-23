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
    assert pc["state"] == "ok" and sorted(ids) == ["1", "2", "3", "4", "5", "90"] and d["emptyPhrases"] == ["type 1"] and d["found"] == 6 and d["cut"] is False       # 90 is in use and no search found it
    by = {c["id"]: c for c in d["candidates"]}
    assert by["1"]["inUse"]["adsets"] == 1 and by["2"]["inUse"] is None and by["5"]["from"] == "suggested by Meta"
    assert sorted(ids[:3]) == ["1", "3", "90"]                                              # the ones in use come first


def test_meta_is_asked_for_suggestions_from_the_interests_in_use_and_the_top_result_of_each_phrase_and_for_behaviors_only_when_asked():
    calls = []
    km.read_interests(SEEDS, {}, get_fn=getter(calls))
    sugg = [p for _path, p in calls if p.get("type") == "adinterestsuggestion"]
    assert len(sugg) == 1 and __import__("json").loads(sugg[0]["interest_list"]) == ["Insulin pump", "Diabetes"]          # the first result of each phrase that found one
    assert not [p for _path, p in calls if p.get("limit_type") == "behaviors"]
    calls.clear()
    km.read_interests(dict(SEEDS, behaviorWords=["diabetes"]), research(), get_fn=getter(calls), act="act_9")
    assert [path for path, p in calls if p.get("limit_type") == "behaviors"] == ["/act_9/targetingbrowse"]
    sugg = [p for _path, p in calls if p.get("type") == "adinterestsuggestion"]
    assert __import__("json").loads(sugg[0]["interest_list"])[:2] == ["Insulin pump", "Diabetes"]                        # the interests in use, biggest spend first


def test_behaviors_are_added_only_when_words_for_them_are_given():
    seeds = dict(SEEDS, behaviorWords=["diabetes"])
    d = km.read_interests(seeds, research(), get_fn=getter(), act="act_9")["data"]
    assert "91" in {c["id"] for c in d["candidates"]} and d["behaviors"] == 2
    assert [c for c in d["candidates"] if c["id"] == "90"][0]["from"] == "in use by an ad set"                     # the travel behavior is not looked at for this client, but an ad set aims at it


def test_with_no_ad_account_no_behavior_is_asked_for_and_the_note_says_why():
    calls = []
    pc = km.read_interests(dict(SEEDS, behaviorWords=["diabetes"]), research(), get_fn=getter(calls))
    assert not [p for _path, p in calls if p.get("limit_type") == "behaviors"] and "behavior" in pc["note"].lower() + " ".join(pc["data"].get("notes") or [])


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
    assert len(d["candidates"]) == km.MAX_CANDIDATES and d["cut"] is True and d["found"] == 143 and d["candidates"][0]["inUse"] and {c["id"] for c in d["candidates"][:3]} == {"1", "3", "90"} and "the first 100 of 143" in pc["note"]


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
    assert cov["judged"] == 4 and cov["unjudged"] == 2 and cov["yes"] == 2 and cov["new"] == 1 and cov["inUse"] == 3 and cov["inUseFit"] == 1
    assert cov["misfit"] == ["3"] and cov["misfitSpend"] == 600.0                          # Diabetes, in use in s1, judged not a fit
    assert cov["works"] == ["1", "3"] and cov["weak"] == ["90"]


def judged_rows():
    r = research()
    cands = km.read_interests(SEEDS, r, get_fn=getter())["data"]["candidates"]
    rows = km.assemble(cands, {"1": {"fit": "yes"}, "2": {"fit": "yes"}, "3": {"fit": "no"}, "4": {"fit": "maybe"}, "5": {"fit": "yes"}})
    in_use = r["metaaccount"]["data"]["inUse"]
    return r, rows, in_use, km.coverage(rows, in_use)


def test_the_plans_say_how_to_act_in_ads_manager_with_only_the_figures_that_are_known():
    r, rows, in_use, cov = judged_rows()
    aud = [{"name": "Pump users", "interests": [{"id": "2"}, {"id": "5"}]}, {"name": "Wider", "interests": [{"id": "4"}]}]
    exc = [{"id": "4", "name": "Diabetic diet"}]
    p = km.plans(rows, cov, in_use, aud, exc, r["metaaccount"]["data"]["adsets"], [{"seed": "Form fills", "why": "x"}])
    assert p["start"]["worth"].startswith("2 audiences to test (\"Pump users\", \"Wider\"), built from 2 interests that fit and are aimed at by no ad set.")
    assert "Detailed targeting" in p["start"]["steps"][1] and "Advantage+" in p["start"]["steps"][1]
    assert "\"Frequent travellers\"" in p["remove"]["worth"] and "\"Diabetes\"" in p["remove"]["worth"] and "$1,000.00" in p["remove"]["worth"]        # the weak one (400) and the misfit (600)
    assert "Pump users" in p["remove"]["steps"][0] and "Travel" in p["remove"]["steps"][0] and "Retarget" not in p["remove"]["steps"][0]      # only the ad sets that name them
    assert "\"Insulin pump\"" in p["scale"]["worth"] and "20 percent" in p["scale"]["steps"][1]
    assert p["exclude"]["worth"] == "1 interest: \"Diabetic diet\"." and p["lookalike"]["worth"] == "1 source: \"Form fills\"." and "100 people" in p["lookalike"]["steps"][1]


def test_a_plan_with_nothing_to_say_is_none_and_a_misfit_that_is_also_weak_is_not_counted_twice():
    r, rows, in_use, cov = judged_rows()
    assert all(v is None for v in km.plans([], {}, [], [], [], [], []).values())
    assert km.plans(rows, dict(cov, weak=["90"], misfit=["90"]), in_use, [], [], r["metaaccount"]["data"]["adsets"], [])["remove"]["worth"].startswith('"Frequent travellers": the ad sets that name it spent $400.00')


def test_a_long_list_of_names_is_shortened_and_says_how_many_more():
    assert km._names(["a", "b", "c", "d", "e"]) == '"a", "b", "c" and 2 more' and km._names(["a", "b"]) == '"a", "b"'


def test_an_interest_in_use_that_no_search_returned_is_still_listed_so_it_is_judged():
    r = research()
    only = {"phrases": ["type 1"]}                                           # a phrase that finds nothing
    pc = km.read_interests(only, r, get_fn=getter())
    ids = {c["id"]: c for c in pc["data"]["candidates"]}
    assert pc["state"] == "ok" and set(ids) >= {"1", "3", "90"} and ids["90"]["from"] == "in use by an ad set" and ids["90"]["sizeText"] == "" and ids["90"]["inUse"]["adsets"] == 1


def test_the_headlines_say_what_the_numbers_show_from_code_alone():
    r, rows, in_use, cov = judged_rows()
    h = km.headlines(cov, in_use, rows, 3, 1)
    assert h[0].startswith("1 of the 3 ad sets use Advantage+ audience")
    assert any(x.startswith("$600.00 of ad set spend names an interest judged not a fit (\"Diabetes\")") for x in h)
    assert any(x.startswith("1 interest in use spent without paying back: \"Frequent travellers\"") for x in h)
    assert any("2 interests that fit are aimed at by no ad set; the biggest is \"Insulin\" (5.0M to 6.0M people)" in x for x in h) or any("are aimed at by no ad set" in x for x in h)
    assert any(x.startswith("\"Insulin pump\"") and "less than the account's usual cost" in x for x in h)
    assert km.headlines({"misfit": [], "weak": [], "works": []}, [], [], 0, 0) == []
