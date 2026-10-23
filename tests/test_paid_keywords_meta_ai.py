"""Keyword Research for Meta: the judging (tracker/paid_keywords_meta_ai.py): what the model is asked, what it is shown, and that its answers go through the checks."""
import json

from tests.test_paid_keywords_meta import SEEDS, research
from tests.test_paid_meta_interests import getter
from tracker import paid_keywords_meta as km
from tracker import paid_keywords_meta_ai as mai
from tracker import paid_strategy_ai as ai


def walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


class Spy:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, system, user, schema, *, max_tokens, stage):
        self.calls.append({"system": system, "user": json.loads(user), "schema": schema, "max_tokens": max_tokens, "stage": stage})
        return self.reply(stage, json.loads(user)) if callable(self.reply) else self.reply


INPUTS = {"name": "Acme", "website": "https://acme.com/", "goal": "leads", "avoid": "jobs; free", "competitors": [{"name": "Omnipod", "url": ""}], "platform": "meta", "research": {}}


def candidates():
    r = research()
    return km.read_interests(SEEDS, r, get_fn=getter())["data"]["candidates"], r


def test_the_schemas_are_strings_and_lists_only_with_every_property_required():
    for schema in (mai.SEEDS, mai.AUDIENCES, mai.JUDGE_ONLY):
        for node in walk(schema):
            if isinstance(node, dict) and node.get("type") == "object":
                assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            assert not (isinstance(node, dict) and "enum" in node)


def test_no_prompt_has_a_dash_and_each_one_says_what_meta_will_not_allow():
    for text in (mai.SEEDS_SYSTEM, mai.AUDIENCES_SYSTEM, mai.JUDGE_SYSTEM):
        assert "—" not in text and "–" not in text and "Advantage+ audience" in text and "sensitive topics" in text
    assert "Never write a number of your own" in mai.AUDIENCES_SYSTEM and "Never write a number of your own" in mai.JUDGE_SYSTEM and "Then:" not in mai.JUDGE_SYSTEM


def test_seeds_keep_short_phrases_add_the_clients_own_words_to_avoid_and_never_repeat():
    got = mai.seeds({"phrases": ["Insulin pump", "insulin pump", "a very long sentence that is not an interest name at all", "diabetes", 5, ""], "behaviorWords": ["travel"] * 3 + ["x%d" % i for i in range(9)],
                     "avoid": ["Jobs", "careers"], "assumptions": ["a"]}, INPUTS)
    assert got["phrases"] == ["Insulin pump", "diabetes"] and len(got["behaviorWords"]) == 6 and got["behaviorWords"][0] == "travel"
    assert got["avoid"] == ["Jobs", "careers", "free"]                                       # the client's jobs is the model's Jobs; free is added
    assert mai.seeds(None, {})["phrases"] == []


def test_the_seed_question_shows_what_the_account_aims_at_and_the_rivals_and_the_clients_avoid_words():
    r = research()
    spy = Spy({"phrases": ["insulin pump"], "behaviorWords": [], "avoid": [], "assumptions": []})
    out = mai.write_seeds(INPUTS, r, spy)
    u = spy.calls[0]["user"]
    assert spy.calls[0]["stage"] == "seeds" and out["phrases"] == ["insulin pump"]
    assert [i["name"] for i in u["account"]["interestsInUse"]][:2] == ["Insulin pump", "Diabetes"] and u["competitorsNamed"] == ["Omnipod"] and u["avoidFromTheClient"] == ["jobs", " free"]
    assert u["account"]["adSets"][1]["advantagePlus"] is True


def test_judging_keeps_only_listed_ids_the_first_answer_about_each_and_a_word_from_the_lists():
    got, dropped = mai.judged({"rows": [
        {"id": "1", "fit": "Yes.", "intent": "core", "theme": "Pump users", "note": "the product"}, {"id": "1", "fit": "no", "intent": "other", "theme": "", "note": "second answer"},
        {"id": "99", "fit": "yes", "intent": "core", "theme": "", "note": "not listed"}, {"id": "2 | Insulin | 5M", "fit": "perhaps", "intent": "weird", "theme": "", "note": "echoed a line"}]}, {"1", "2"})
    assert got["1"] == {"fit": "yes", "intent": "core", "theme": "Pump users", "note": "the product"} and dropped == 2
    assert got["2"]["fit"] == "maybe" and got["2"]["intent"] == "other"                         # an unknown word is the default, and an echoed line is understood
    assert mai.judged(None, {"1"}) == ({}, 0)


def by_id():
    cands, _ = candidates()
    return {c["id"]: c for c in cands}


def test_an_audience_is_made_only_of_listed_interests_not_judged_a_fit_and_loses_the_rest():
    ids = by_id()
    judgements = {"4": {"fit": "no"}, "1": {"fit": "yes"}}
    raw = {"audiences": [{"name": "Pump users", "role": "Prospecting.", "interestIds": ["1", "2", "4", "99", "1"], "why": "w", "test": "t"},
                         {"name": "pump users", "role": "test", "interestIds": ["3"], "why": "", "test": ""},                    # same name again
                         {"name": "Only a misfit", "role": "x", "interestIds": ["4"], "why": "", "test": ""},                    # nothing left in it
                         {"name": "", "role": "x", "interestIds": ["3"], "why": "", "test": ""},                                   # no name
                         {"name": "Rivals", "role": "competitor", "interestIds": ["5"], "why": "", "test": ""}]}
    kept, dropped = mai.audiences(raw, ids, judgements)
    assert [a["name"] for a in kept] == ["Pump users", "Rivals"] and [i["id"] for i in kept[0]["interests"]] == ["1", "2"]
    assert kept[0]["role"] == "prospecting" and kept[1]["role"] == "competitor" and kept[0]["largest"] == 5_500_000 and kept[0]["interests"][0]["inUse"] is True and kept[0]["interests"][1]["inUse"] is False
    assert dropped == 7                                                                           # three ids (4, 99, the repeated 1), the repeated name (1), the one with nothing left (its id and itself: 2), the one with no name (1)


def test_exclusions_are_listed_interests_not_part_of_an_audience_and_without_repeats():
    ids = by_id()
    aud = [{"interests": [{"id": "1"}]}]
    kept, dropped = mai.exclusions({"exclusions": [{"id": "2", "why": "students"}, {"id": "1", "why": "in an audience"}, {"id": "77", "why": "not listed"}, {"id": "2", "why": "again"}]}, ids, aud)
    assert [e["id"] for e in kept] == ["2"] and kept[0]["name"] == "Insulin" and kept[0]["why"] == "students" and dropped == 3


def reply(stage, user):
    if stage == "audiences":
        rows = [{"id": c["id"], "fit": "yes", "intent": "core", "theme": "Pump users", "note": ""} for c in user["interests"][:3]]
        return {"rows": rows, "audiences": [{"name": "Pump users", "role": "prospecting", "interestIds": [c["id"] for c in user["interests"][:3]], "why": "People close to the product.", "test": "Cost per lead against the account."}],
                "exclusions": [], "lookalikes": [{"seed": "Form fills", "why": "Closest to buyers."}], "summary": ["The list is wide. About 4,242 people were asked."],
                "quickWins": [{"what": "Start Pump users", "why": "None of it is aimed at yet"}], "questions": [{"area": "Compliance", "question": "May we name the pump?", "why": "Rules", "urgency": "before launch"}],
                "risks": [], "assumptions": ["leads are the goal"]}
    return {"rows": [{"id": c["id"], "fit": "no", "intent": "other", "theme": "", "note": "left out the first time"} for c in user["interests"]]}


def test_the_audience_question_shows_sizes_and_results_and_asks_once_more_about_what_it_left_out():
    cands, r = candidates()
    spy = Spy(reply)
    out = mai.write_audiences(INPUTS, r, cands, {"avoid": ["jobs"]}, spy)
    first, again = spy.calls
    assert first["stage"] == "audiences" and first["max_tokens"] == 16000 and again["stage"] == "judge"
    shown = {i["id"]: i for i in first["user"]["interests"]}
    assert shown["1"]["audienceSize"] == "1.0M to 1.2M" and shown["1"]["alreadyAimedAt"] == {"adSets": 1, "spent": 600.0, "results": 20.0, "costPerResult": 30.0} and "alreadyAimedAt" not in shown["2"]
    assert len(again["user"]["interests"]) == len(cands) - 3 and again["user"]["avoid"] == ["jobs"]
    assert len(out["judged"]) == len(cands) and out["judged"][cands[-1]["id"]]["fit"] == "no" and out["judged"][cands[0]["id"]]["fit"] == "yes"
    assert [a["name"] for a in out["audiences"]] == ["Pump users"] and out["plan"]["lookalikes"][0]["seed"] == "Form fills"


def test_a_number_the_model_was_not_given_is_listed_for_a_person_to_check():
    cands, r = candidates()
    out = mai.write_audiences(INPUTS, r, cands, {}, Spy(reply))
    assert [n["number"] for n in out["plan"]["numbers"]] == ["4,242"] and out["plan"]["numbers"][0]["where"] == "In short"


def test_when_asking_again_fails_the_left_out_interests_stay_unjudged_and_the_rest_is_kept():
    cands, r = candidates()

    def flaky(stage, user):
        if stage == "judge":
            raise RuntimeError("model down")
        return reply(stage, user)
    out = mai.write_audiences(INPUTS, r, cands, {}, Spy(flaky))
    assert len(out["judged"]) == 3 and len(out["audiences"]) == 1
