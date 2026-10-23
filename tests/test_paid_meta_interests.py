"""Meta's interests and behaviors (tracker/paid_meta_interests.py): the requests, the reading of Meta's answers, the check that an interest is one Meta has, and which interests an
account's ad sets already name. Meta's answers are in the shapes the Graph API documents."""
import json

from tracker import paid_meta_audiences as ma
from tracker import paid_meta_interests as mi


def opt(oid, name, low=1_000_000, high=1_200_000, path=("Interests", "Additional interests"), topic="Health and wellness"):
    return {"id": oid, "name": name, "audience_size_lower_bound": low, "audience_size_upper_bound": high, "path": list(path), "topic": topic}


CATALOGUE = {
    "insulin pump": [opt("1", "Insulin pump"), opt("2", "Insulin", 5_000_000, 6_000_000)],
    "diabetes": [opt("3", "Diabetes", 20_000_000, 24_000_000), opt("4", "Diabetic diet", 900_000, 1_100_000), opt("1", "Insulin pump")],
    "type 1": [],
    "insulin pump users": [opt("2", "Insulin", 5_000_000, 6_000_000)],            # Meta answers a longer phrase with a shorter interest: not the interest asked for
}


def getter(calls=None, fail=()):
    def get(path, params):
        if calls is not None:
            calls.append((path, dict(params)))
        if params.get("type") == "adinterest":
            if params["q"] in fail:
                raise RuntimeError("Meta answered 500")
            return CATALOGUE.get(params["q"].lower(), [])
        if params.get("type") == "adinterestsuggestion":
            return [opt("5", "Continuous glucose monitor", 400_000, 500_000), opt("1", "Insulin pump")]
        if params.get("class") == "behaviors":
            return [opt("90", "Frequent travellers", 9_000_000, 10_000_000, ("Behaviors", "Travel")), opt("91", "Diabetes care givers", 30_000, 40_000, ("Behaviors", "Health"))]
        raise AssertionError(params)
    return get


def test_the_three_requests_are_the_ones_the_graph_api_documents():
    path, p = mi.search_request("insulin pump")
    assert path == "/search" and p["type"] == "adinterest" and p["q"] == "insulin pump" and p["limit"] == mi.SEARCH_LIMIT
    path, p = mi.suggest_request(["Insulin pump", "Diabetes"] + ["x%d" % i for i in range(20)])
    assert p["type"] == "adinterestsuggestion" and len(json.loads(p["interest_list"])) == mi.MAX_SEEDS_FOR_SUGGESTIONS and json.loads(p["interest_list"])[0] == "Insulin pump"
    assert mi.behaviors_request()[1]["type"] == "adTargetingCategory" and mi.behaviors_request()[1]["class"] == "behaviors"


def test_options_are_read_with_their_size_range_path_and_topic_and_a_row_without_an_id_or_name_is_dropped():
    got = mi.parse_options([opt("1", "Insulin pump"), {"name": "no id"}, {"id": "7"}, opt("1", "Insulin pump again"), "junk", opt("8", "  Spaced   out ")])
    assert [o["id"] for o in got] == ["1", "8"] and got[1]["name"] == "Spaced out"
    assert got[0]["low"] == 1_000_000 and got[0]["high"] == 1_200_000 and got[0]["size"] == 1_100_000 and got[0]["sizeText"] == "1.0M to 1.2M"
    assert got[0]["path"] == ["Interests", "Additional interests"] and got[0]["topic"] == "Health and wellness" and got[0]["kind"] == "interest"


def test_a_size_meta_will_not_say_is_empty_not_zero_and_the_old_single_size_field_is_still_read():
    got = mi.parse_options([{"id": "1", "name": "A", "audience_size_lower_bound": -1, "audience_size_upper_bound": -1}, {"id": "2", "name": "B", "audience_size": 750_000}])
    assert got[0]["size"] is None and got[0]["sizeText"] == ""
    assert got[1]["size"] == 750_000 and got[1]["sizeText"] == "750K"


def test_find_asks_once_per_phrase_and_a_phrase_meta_does_not_answer_is_said_and_does_not_lose_the_others():
    calls = []
    found, notes = mi.find(["insulin pump", "Diabetes", "insulin pump", "", "boom"], getter(calls, fail=("boom",)))
    assert sorted(found) == ["Diabetes", "boom", "insulin pump"] and len(calls) == 3
    assert [o["id"] for o in found["insulin pump"]] == ["1", "2"] and found["boom"] == []
    assert len(notes) == 1 and "boom" in notes[0] and "500" in notes[0]


def test_find_never_asks_about_more_than_the_limit_of_phrases():
    calls = []
    mi.find(["p%d" % i for i in range(60)], getter(calls))
    assert len(calls) == mi.MAX_QUERIES


def test_validate_keeps_only_an_interest_of_exactly_that_name_ignoring_case_and_punctuation():
    got, notes = mi.validate(["Insulin Pump", "diabetes", "Type 1 diabetes pump enthusiasts", "Insulin pump users"], getter())
    assert got["Insulin Pump"]["id"] == "1" and got["diabetes"]["id"] == "3" and got["Type 1 diabetes pump enthusiasts"] is None and notes == []
    assert got["Insulin pump users"] is None
    again, notes = mi.validate(["boom"], getter(fail=("boom",)))
    assert again["boom"] is None and notes                                  # unverified is None and is said


def test_suggestions_and_behaviors_are_read_and_a_failure_is_said():
    got, notes = mi.suggestions(["Insulin pump"], getter())
    assert [o["id"] for o in got] == ["5", "1"] and notes == []
    assert mi.suggestions([], getter()) == ([], [])
    beh, notes = mi.behaviors(getter())
    assert [o["kind"] for o in beh] == ["behavior", "behavior"]

    def broken(path, params):
        raise RuntimeError("no permission")
    got, notes = mi.suggestions(["x"], broken)
    assert got == [] and "no permission" in notes[0]


def adsets_and_stats():
    rows = [{"id": "s1", "name": "Pump users", "campaign_id": "c1", "effective_status": "ACTIVE",
             "targeting": {"flexible_spec": [{"interests": [{"id": "1", "name": "Insulin pump"}, {"id": "3", "name": "Diabetes"}], "behaviors": [{"id": "91", "name": "Diabetes care givers"}]}]}},
            {"id": "s2", "name": "Broad", "campaign_id": "c1", "effective_status": "PAUSED", "targeting": {"flexible_spec": [{"interests": [{"id": "1", "name": "Insulin pump"}]}],
                                                                                                       "custom_audiences": [{"id": "ca1", "name": "Site visitors"}]}}]
    ins = [{"adset_id": "s1", "campaign_name": "C", "campaign_id": "c1", "objective": "OUTCOME_LEADS", "spend": "300", "impressions": "9000", "inline_link_clicks": "200", "actions": [{"action_type": "lead", "value": "10"}]},
           {"adset_id": "s2", "campaign_name": "C", "campaign_id": "c1", "objective": "OUTCOME_LEADS", "spend": "100", "impressions": "4000", "inline_link_clicks": "50", "actions": [{"action_type": "lead", "value": "0"}]}]
    return ma.parse_adsets(rows), ma.parse_insights(ins)


def test_in_use_counts_the_ad_sets_naming_each_interest_and_what_they_spent_and_brought_and_skips_audiences():
    adsets, stats = adsets_and_stats()
    used = mi.in_use(adsets, stats)
    assert set(used) == {"1", "3", "91"}                                    # the custom audience is not an interest
    assert used["1"] == {"name": "Insulin pump", "kind": "interest", "adsets": 2, "cost": 400.0, "conv": 10.0, "cpa": 40.0}
    assert used["3"]["adsets"] == 1 and used["3"]["cost"] == 300.0 and used["3"]["cpa"] == 30.0
    assert used["91"]["kind"] == "behavior"
    assert mi.in_use([], {}) == {} and mi.in_use(adsets, {})["1"]["cost"] == 0.0 and mi.in_use(adsets, {})["1"]["cpa"] is None


def test_candidates_are_one_per_interest_carry_where_they_came_from_and_which_are_already_aimed_at():
    adsets, stats = adsets_and_stats()
    used = mi.in_use(adsets, stats)
    found, _ = mi.find(["insulin pump", "diabetes"], getter())
    sugg, _ = mi.suggestions(["Insulin pump"], getter())
    beh, _ = mi.behaviors(getter())
    got = mi.candidates(found, sugg, beh, used, phrases_for_behaviors=["diabetes"])
    by = {c["id"]: c for c in got}
    assert sorted(by) == ["1", "2", "3", "4", "5", "91"] and len(got) == 6                  # id 1 came three ways and is listed once; the travel behavior has nothing to do with it
    assert by["1"]["from"] == "search: insulin pump" and by["1"]["inUse"]["adsets"] == 2 and by["2"]["inUse"] is None
    assert by["5"]["from"] == "suggested by Meta" and by["91"]["from"] == "Meta behavior" and by["91"]["small"] is True and by["3"]["small"] is False


def test_candidates_leave_out_the_words_to_avoid_and_keep_no_behavior_when_none_is_asked_for():
    found, _ = mi.find(["insulin pump", "diabetes"], getter())
    got = mi.candidates(found, [], [], {}, avoid=["diet"])
    assert "4" not in {c["id"] for c in got}
    beh, _ = mi.behaviors(getter())
    assert mi.candidates({}, [], beh, {}) == []


def test_the_adsets_request_asks_for_the_targeting_of_live_and_paused_ad_sets():
    path, p = mi.adsets_request("act_9")
    assert path == "/act_9/adsets" and "targeting" in p["fields"] and json.loads(p["effective_status"]) == ["ACTIVE", "PAUSED"]
