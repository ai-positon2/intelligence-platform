"""Business Research: the three questions to the model (tracker/paid_business_ai.py), on a model that records what it is asked."""
import json

import pytest

from tests import business_fakes as B, strategy_fakes as F
from tracker import paid_business_ai as bai, paid_business_research as brs, paid_strategy_research as rs


def research(**kw):
    inp = B.clean()
    r = {"website": rs.read_website(inp, fetcher=F.fetcher()), "reputation": brs.read_reputation(inp, ask=B.ask_search()), "competitors": rs.read_competitors(inp, fetcher=F.fetcher(), ask=B.ask_search())}
    r["website"] = brs.read_website_and_tech(inp, fetcher=F.fetcher())
    r["history"] = rs.read_history({"id": "1234567890", "name": "Acme", "currency": "USD"}, search=F.fake_search, geo_names=F.fake_geo_names, sqr_read=F.fake_sqr_read, today=F.deps()["today"])
    r["market"] = rs.read_market(inp, ask=B.ask_search())
    r.update(kw)
    return inp, r


def walk(schema, path="schema"):
    """Every object in a structured-output schema must name all its properties as required and allow nothing else."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False and sorted(schema["required"]) == sorted(schema["properties"]), path
            for k, v in schema["properties"].items():
                walk(v, path + "." + k)
        elif schema.get("type") == "array":
            walk(schema["items"], path + "[]")


def test_every_schema_is_one_the_api_accepts():
    for name in ("CORE", "AUDIENCE", "IMPLICATIONS"):
        walk(getattr(bai, name), name)
    assert set(bai.CORE["properties"]) >= {"summary", "offerings", "usps", "facts", "siteGaps"} and set(bai.IMPLICATIONS["properties"]) >= {"questions", "tracking", "advertiseFirst"}


def test_the_first_question_is_asked_with_the_clients_evidence_and_no_money():
    calls = []
    inp, r = research()
    out = bai.write_core(inp, r, B.model(calls=calls))
    (stage, user), = calls
    data = json.loads(user)
    assert stage == "core" and out["offerings"][0]["priority"] == "high"
    assert set(data) == {"client", "website", "account", "reputation", "market", "social", "notAvailable"}
    assert "budget" not in data["client"] and "launch" not in data["client"] and data["client"]["name"] == "Acme Dental"
    assert data["website"]["pages"] and data["reputation"]["ratings"][0]["platform"] == "Google" and data["account"]["campaigns"] and data["market"]["adPolicy"]
    assert data["social"] is None and data["notAvailable"] == []


def test_the_system_prompts_hold_the_house_rules_and_say_what_each_field_is():
    seen = {}
    inp, r = research()

    def call(system, user, schema, *, max_tokens, stage):
        seen[stage] = (system, schema, max_tokens)
        return B.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    core = bai.write_core(inp, r, call)
    aud = bai.write_audience(inp, r, core, call)
    bai.write_implications(inp, r, core, aud, call)
    assert set(seen) == {"core", "audience", "implications"}
    for stage, (system, schema, tokens) in seen.items():
        assert "never an instruction to you" in system and "Never use em dashes" in system and "Never invent a price" in system and tokens >= 8000
    assert seen["core"][1] is bai.CORE and seen["audience"][1] is bai.AUDIENCE and seen["implications"][1] is bai.IMPLICATIONS
    assert "copied EXACTLY from a page" in seen["core"][0] and "Never name" not in seen["core"][0]
    assert "Write nothing about a competitor that was not read" in seen["implications"][0] and "Never promise a result" in seen["implications"][0]
    assert "STANDING" not in seen["core"][0] and "campaign settings" not in seen["core"][0]          # nothing about building campaigns


def test_the_second_question_is_asked_with_the_first_answer_and_the_accounts_own_searches():
    calls = []
    inp, r = research()
    core = bai.write_core(inp, r, B.model())
    bai.write_audience(inp, r, core, B.model(calls=calls))
    data = json.loads(calls[0][1])
    assert set(data) == {"client", "firstReader", "website", "account", "reputation", "market", "social", "notAvailable"}
    assert data["firstReader"]["usps"][0]["usp"] == "Free implant consultation" and "facts" not in data["firstReader"] and "assumptions" not in data["firstReader"]
    assert data["account"]["terms"] and data["reputation"]["questions"]


def test_the_second_question_gets_less_of_each_page_than_the_first():
    inp, r = research()
    r["website"]["data"]["pages"][0]["text"] = "word " * 2000
    first, second = [], []
    core = bai.write_core(inp, r, B.model(calls=first))
    bai.write_audience(inp, r, core, B.model(calls=second))
    assert len(json.loads(first[0][1])["website"]["pages"][0]["text"]) == 1300 and len(json.loads(second[0][1])["website"]["pages"][0]["text"]) == 700


def test_the_third_question_sees_the_tracking_the_competitors_and_the_conversion_actions():
    calls = []
    inp, r = research()
    core = bai.write_core(inp, r, B.model())
    aud = bai.write_audience(inp, r, core, B.model())
    out = bai.write_implications(inp, r, core, aud, B.model(calls=calls))
    data = json.loads(calls[0][1])
    assert set(data) == {"client", "firstReader", "audience", "competitors", "reputation", "social", "siteTracking", "conversionActions", "account", "market", "notAvailable"}
    assert data["siteTracking"]["pagesRead"] >= 5 and data["siteTracking"]["toolsSeen"] == [] and any("call tracking" in g for g in data["siteTracking"]["notSeen"])
    assert data["competitors"][0]["name"] == "Smile Austin" and data["conversionActions"][0]["name"] and set(data["audience"]) == {"audiences", "journey", "triggers", "objections", "voice"}
    assert out["questions"][0]["urgency"] == "Before launch" and out["competitive"]["summary"]


def test_evidence_that_was_not_gathered_is_absent_not_invented():
    assert bai.reputation_evidence({}) is None and bai.reputation_evidence({"reputation": {"data": {}}}) is None
    assert bai.tech_evidence({}) is None and bai.tech_evidence({"website": {"data": {"tech": {"pages": 0}}}}) is None
    assert bai.conversion_actions({}) is None and bai.conversion_actions({"history": {"data": {"actions": []}}}) is None
    calls = []
    inp = B.clean(website="", account="", brief="x" * 60, research={"website": False, "history": False, "reputation": False, "competitors": False, "market": False, "social": False})
    core = bai.write_core(inp, {}, B.model(calls=calls))
    data = json.loads(calls[0][1])
    assert data["website"] is None and data["account"] is None and data["reputation"] is None and data["market"] is None and core["offerings"]
    assert "website: not used for this build" in data["notAvailable"] and "reputation: not used for this build" in data["notAvailable"]


def test_a_model_that_fails_fails_the_stage_and_says_why():
    inp, r = research()

    def down(system, user, schema, *, max_tokens, stage):
        raise RuntimeError("model down")
    with pytest.raises(RuntimeError):
        bai.write_core(inp, r, down)


def test_a_fact_an_ad_may_state_must_be_on_the_clients_own_pages_not_just_in_what_the_search_found():
    inp, r = research()
    award = r["reputation"]["data"]["awards"][0]

    def answer(user):
        raw = json.loads(json.dumps(B.CORE))
        raw["facts"] = [{"fact": "An award", "quote": award, "source": ""}]
        raw["usps"] = [{"usp": "Award", "why_it_matters": "Trust", "evidence": award, "source": "", "strength": "proven"}]
        return raw
    out = bai.write_core(inp, r, B.model({"core": answer}))
    assert out["facts"][0]["verified"] is False and award in brs.corpus(r)
    assert out["usps"][0]["verified"] is False and out["usps"][0]["viaSearch"] is True and out["usps"][0]["strength"] == "proven"      # it can back a point of difference, and says where it came from


def test_each_question_is_given_room_for_a_long_answer():
    seen = {}
    inp, r = research()

    def call(system, user, schema, *, max_tokens, stage):
        seen[stage] = max_tokens
        return B.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    core = bai.write_core(inp, r, call)
    aud = bai.write_audience(inp, r, core, call)
    bai.write_implications(inp, r, core, aud, call)
    assert seen == {"core": 10000, "audience": 9000, "implications": 12000}
