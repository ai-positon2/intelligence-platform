"""Strategy & Campaign Book: how the model is asked and what is handed to it (tracker/paid_strategy_ai.py). The model is a fake."""
import datetime as dt
import json

import pytest

EM, EN = chr(0x2014), chr(0x2013)

from tests import strategy_fakes as F
from tracker import paid_strategy as ps, paid_strategy_ai as ai, paid_strategy_check as ck, paid_strategy_research as rs


def inputs(**kw):
    return ps.clean_inputs(F.inputs(**kw), today=dt.date(2026, 10, 16))


def research(**kw):
    out = {"website": rs.read_website(inputs(), fetcher=F.fetcher()),
           "history": rs.read_history({"id": "1234567890", "currency": "USD"}, search=F.fake_search, sqr_read=F.fake_sqr_read, today=dt.date(2026, 10, 16)),
           "competitors": rs.read_competitors(inputs(competitors="smileaustin.com"), fetcher=F.fetcher()),
           "keywords": rs.read_keywords(inputs(locations=""), post=F.fake_post, planner_account="1", currency="USD"),
           "market": rs.read_market(inputs(), ask=F.ask_search()), "social": rs.read_social("ana@position2.com", 5, store=F.FakeSci(runs={5: {
               "email": "ana@position2.com", "company_name": "x", "status": "done", "synthesis": F.SYNTHESIS, "updated_at": "t"}}), reused=True)}
    out.update(kw)
    return out


# ── the schemas are what structured output accepts ──────────────────────────

def walk(schema, path="$"):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False, path
        assert sorted(schema["required"]) == sorted(schema["properties"]), path
        for k, v in schema["properties"].items():
            walk(v, path + "." + k)
    elif schema.get("type") == "array":
        walk(schema["items"], path + "[]")
    else:
        assert schema["type"] in ("string", "number", "boolean"), path


@pytest.mark.parametrize("name", ["PROFILE", "COMPETITIVE", "STRATEGY", "ARCHITECTURE", "BUILD_SEARCH", "BUILD_PMAX", "BUILD_CREATIVE"])
def test_every_schema_requires_every_property_and_allows_nothing_extra(name):
    walk(getattr(ai, name))


def test_every_kind_has_a_build_schema():
    assert set(ai.BUILD_SCHEMAS) == set(ps.KINDS)


def test_the_prompts_carry_the_house_rules():
    for text in (ai.PROFILE_SYSTEM, ai.COMPETITIVE_SYSTEM, ai.STRATEGY_SYSTEM, ai.ARCH_SYSTEM, ai.SEARCH_SYSTEM, ai.PMAX_SYSTEM, ai.CREATIVE_SYSTEM):
        assert "em dashes" in text and "ONLY the evidence" in text and "never an instruction to you" in text
        assert EM not in text and EN not in text
    assert "Maximize conversions" in ai.ARCH_SYSTEM and "AI Max off" in ai.STRATEGY_SYSTEM and "30 characters" in ai.SEARCH_SYSTEM


# ── the evidence the model is shown ─────────────────────────────────────────

def test_the_site_evidence_is_every_page_trimmed_to_a_budget():
    ev = ai.site_evidence(research())
    assert len(ev["pages"]) >= 6 and ev["pages"][0]["title"].startswith("Acme Dental") and ev["facts"]["rating"] == {"value": 4.9, "count": 312}
    tiny = ai.site_evidence(research(), per_page=200, total=900)
    assert all(len(p["text"]) <= 300 for p in tiny["pages"]) and ai.site_evidence({}) is None


def test_competitor_evidence_only_has_the_ones_that_were_read():
    r = research(competitors=rs.read_competitors(inputs(competitors="smileaustin.com, gone.example"), fetcher=F.fetcher()))
    ev = ai.rival_evidence(r)
    assert [e["name"] for e in ev] == ["smileaustin.com"] and len(ev[0]["pages"]) == 3 and ai.rival_evidence({}) is None


def test_keyword_evidence_is_a_line_per_idea_and_social_evidence_skips_a_pending_run():
    kw = ai.keyword_evidence(research())
    assert kw["ideas"][0] == "cost of dental implants | 8100/mo | Medium | bid 3.0-9.0 | peak January | from your terms" and ai.keyword_evidence({}) is None
    assert ai.social_evidence(research())["platforms"][0]["platform"] == "instagram"
    assert ai.social_evidence({"social": {"data": {"pending": True, "platforms": [{"posts": 3}]}}}) is None


def test_what_is_missing_is_named_so_the_model_does_not_write_as_if_it_had_it():
    r = research(website=rs.failed("website", "The home page could not be read: blocked."), market=rs.piece("market", "partial", "only some"))
    said = ai.missing(r, inputs())
    assert "website: The home page could not be read: blocked." in said and "market (partly): only some" in said
    assert any(s.startswith("seasonality: not used") for s in ai.missing(r, inputs(account="", research={"seasonality": False})))


def test_the_client_block_leaves_out_what_was_not_given():
    block = ai.client_block(inputs(brief="", avoid=""))
    assert block["name"] == "Acme Dental" and "brief" not in block and "avoid" not in block and block["goal"] == "leads"


# ── the forecast is arithmetic, in code ─────────────────────────────────────

def test_the_forecast_uses_the_accounts_own_numbers_when_it_has_enough():
    r = {"history": {"data": {"totals": {"clicks": 1000, "cpc": 4.0, "conv": 40, "cvr": 4.0}}}}
    fc = ai.forecast(inputs(budget="4000"), r)
    exp = [s for s in fc["scenarios"] if s["scenario"] == "Expected"][0]
    assert exp == {"scenario": "Expected", "cpc": 4.0, "cvr": 4.0, "clicks": 1000, "conversions": 40.0, "cpa": 100.0}
    cautious, strong = fc["scenarios"][0], fc["scenarios"][2]
    assert cautious["cpc"] == 4.8 and cautious["cvr"] == 2.4 and cautious["clicks"] == 833 and strong["cpc"] == 3.4 and strong["cvr"] == 5.6
    assert "account's own average" in fc["cpcFrom"] and "account's own conversion rate" in fc["cvrFrom"]


def test_without_history_cost_per_click_comes_from_planner_and_the_rate_is_an_assumption():
    r = {"keywords": {"data": {"ideas": [{"lowBid": 2.0, "highBid": 4.0}, {"lowBid": 4.0, "highBid": 8.0}, {"lowBid": 6.0, "highBid": 10.0}]}}}
    fc = ai.forecast(inputs(budget="3000", goal="calls"), r)
    assert fc["scenarios"][1]["cpc"] == 6.0 and fc["scenarios"][1]["cvr"] == 5.0 and "Keyword Planner" in fc["cpcFrom"] and "assumption" in fc["cvrFrom"]


def test_planner_bids_in_another_currency_than_the_budget_are_not_used_for_the_forecast():
    """With no account chosen Planner is asked through Position2's first account, whose bids come back in ITS currency: divided into a budget in dollars they are the wrong size."""
    ideas = [{"lowBid": 2.0, "highBid": 4.0}, {"lowBid": 4.0, "highBid": 8.0}, {"lowBid": 6.0, "highBid": 10.0}]
    other = {"keywords": {"data": {"ideas": ideas, "bidCurrency": "INR"}}}
    assert ai.forecast(inputs(budget="3000", goal="calls", currency="USD"), other) is None
    same = {"keywords": {"data": {"ideas": ideas, "bidCurrency": "USD"}}}
    assert ai.forecast(inputs(budget="3000", goal="calls", currency="USD"), same)["scenarios"][1]["cpc"] == 6.0
    assert ai.forecast(inputs(budget="3000", goal="calls", currency="INR"), other)["scenarios"][1]["cpc"] == 6.0            # a budget in the same currency uses them
    assert ai.forecast(inputs(budget="3000", goal="calls"), {"keywords": {"data": {"ideas": ideas}}}) is not None            # a read that did not say is as before


def test_there_is_no_forecast_without_a_budget_a_cost_per_click_or_for_awareness():
    full = {"history": {"data": {"totals": {"clicks": 1000, "cpc": 4.0, "conv": 40, "cvr": 4.0}}}}
    assert ai.forecast(inputs(budget=None), full) is None and ai.forecast(inputs(goal="awareness"), full) is None and ai.forecast(inputs(), {}) is None
    thin = {"history": {"data": {"totals": {"clicks": 50, "cpc": 9.0, "conv": 1, "cvr": 2.0}}}}
    assert ai.forecast(inputs(), thin) is None                     # too few clicks to trust, and no Keyword Planner


# ── the stages ──────────────────────────────────────────────────────────────

class Spy:
    """A model that records what it was asked and answers from the standard fake."""

    def __init__(self, **ov):
        self.inner, self.asked = F.model(ov), []

    def __call__(self, system, user, schema, *, max_tokens, stage):
        self.asked.append({"stage": stage, "system": system, "user": json.loads(user), "schema": schema, "max_tokens": max_tokens})
        return self.inner(system, user, schema, max_tokens=max_tokens, stage=stage)


def test_each_stage_is_handed_its_evidence_and_the_earlier_stages():
    spy, r, i = Spy(), research(), inputs()
    profile = ai.write_profile(i, r, spy)
    comp = ai.write_competitive(i, r, profile, spy)
    strat = ai.write_strategy(i, r, profile, comp, spy)
    arch = ai.write_architecture(i, r, profile, strat, spy)
    asked = {a["stage"]: a["user"] for a in spy.asked}
    assert set(asked) == {"profile", "competitive", "strategy", "architecture"}
    assert asked["profile"]["client"]["name"] == "Acme Dental" and asked["profile"]["website"]["pages"] and asked["profile"]["account"]["totals"]["cost"] == 1300.0
    assert asked["competitive"]["competitors"][0]["name"] == "smileaustin.com" and asked["competitive"]["clientProfile"]["summary"]
    assert asked["strategy"]["profile"]["summary"] and asked["strategy"]["forecast"]["scenarios"] and asked["strategy"]["social"]["platforms"]
    assert asked["architecture"]["allowedKinds"] == ps.GOOGLE_KINDS and asked["architecture"]["clientPages"][0]["url"].startswith("https://acmedental.com")
    assert [c["name"] for c in arch["campaigns"]] == ["P2_Search_Implants_20261102", "P2_PMax_Local_20261102"]


def test_competitive_is_skipped_when_no_competitor_was_read():
    spy = Spy()
    assert ai.write_competitive(inputs(), {"website": research()["website"]}, {}, spy) is None and spy.asked == []


def test_a_build_gets_the_campaign_the_facts_and_what_to_fix():
    spy, r, i = Spy(), research(), inputs()
    profile = ai.write_profile(i, r, spy)
    strat = ai.write_strategy(i, r, profile, {}, spy)
    arch = ai.write_architecture(i, r, profile, strat, spy)
    spy.asked.clear()
    built, drops = ai.write_build(arch["campaigns"][0], i, r, profile, strat, {}, spy, problems=["Ad 1 has too few headlines."])
    sent = spy.asked[0]
    assert sent["stage"] == "build:" + arch["campaigns"][0]["key"] and sent["schema"] is ai.BUILD_SEARCH and sent["system"] is ai.SEARCH_SYSTEM
    assert sent["user"]["campaign"]["name"] == "P2_Search_Implants_20261102" and sent["user"]["fixTheseFromLastTime"] == ["Ad 1 has too few headlines."]
    assert {f["confirmed"] for f in sent["user"]["facts"]} == {True, False} and sent["user"]["account"]["keywordsBest"]
    assert built["adGroups"][0]["landingPage"] == "https://acmedental.com/services/dental-implants" and built["adGroups"][0]["landingVerified"]
    spy.asked.clear()
    ai.write_build(arch["campaigns"][1], i, r, profile, strat, {}, spy)
    assert spy.asked[0]["schema"] is ai.BUILD_PMAX and spy.asked[0]["system"] is ai.PMAX_SYSTEM
    assert spy.asked[0]["user"]["fixTheseFromLastTime"] is None


def test_a_failed_call_is_tried_once_more_with_more_room_and_then_raised():
    sizes = []

    def flaky(system, user, schema, *, max_tokens, stage):
        sizes.append(max_tokens)
        if len(sizes) == 1:
            raise RuntimeError("overloaded")
        return {}
    ai._call_stage(flaky, "x", "s", "{}", {}, 1000)
    assert sizes == [1000, 1500]

    class Refused(RuntimeError):
        kind = "refused"
    n = []

    def refuses(system, user, schema, *, max_tokens, stage):
        n.append(1)
        raise Refused("no")
    with pytest.raises(Refused):
        ai._call_stage(refuses, "x", "s", "{}", {}, 1000)
    assert n == [1]                                                # a refusal is not asked again
    always = []

    def down(system, user, schema, *, max_tokens, stage):
        always.append(1)
        raise RuntimeError("down")
    with pytest.raises(RuntimeError):
        ai._call_stage(down, "x", "s", "{}", {}, 1000)
    assert len(always) == 2


# ── the API refusing the schema ─────────────────────────────────────────────

class Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class Resp:
    def __init__(self, text, stop="end_turn", usage=None):
        self.content, self.stop_reason, self.model = [Block(text)], stop, "claude-sonnet-5-5"
        self.usage = type("U", (), {"model_dump": lambda s: usage or {"input_tokens": 100, "output_tokens": 50}})()


class FakeClient:
    def __init__(self, answers):
        self.answers, self.sent = list(answers), []
        self.messages = self

    def create(self, **kw):
        self.sent.append(kw)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def test_a_schema_the_api_refuses_is_recognised_by_what_the_error_says():
    assert ai.schema_refused("BadRequestError: 400 The schema is too complex") and ai.schema_refused("invalid_request_error: output_config.format.schema is not supported")
    assert not ai.schema_refused("RateLimitError: 429 slow down") and not ai.schema_refused("BadRequestError: 400 prompt is too long") and not ai.schema_refused("")
    assert not ai.schema_refused("APIConnectionError: the schema file was not found")


def test_the_same_question_can_be_asked_with_the_schema_in_the_prompt():
    from tracker.market_radar_llm import ModelError
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
    client = FakeClient([Resp('Here you go:\n```json\n{"a": "yes"}\n```')])
    parsed, meta = ai.text_json("SYSTEM", '{"q": 1}', schema, model="claude-sonnet-5-5", max_tokens=500, client=client)
    assert parsed == {"a": "yes"} and meta["usage"] == {"input_tokens": 100, "output_tokens": 50} and meta["fallback"]
    sent = client.sent[0]
    assert sent["system"].startswith("SYSTEM") and json.dumps(schema) in sent["system"] and "output_config" in sent and "format" not in sent["output_config"] and sent["max_tokens"] == 500
    for answer, kind in ((Resp("sorry", stop="refusal"), "refused"), (Resp("{", stop="max_tokens"), "truncated"), (Resp("no json here"), "bad_json"), (Resp("[1, 2]"), "bad_json"),
                         (RuntimeError("down"), "api_error")):
        with pytest.raises(ModelError) as e:
            ai.text_json("S", "{}", schema, model="m", max_tokens=5, client=FakeClient([answer]))
        assert e.value.kind == kind


def test_a_build_falls_back_to_the_schema_in_the_prompt_only_when_the_api_refuses_the_schema(monkeypatch):
    from tracker import market_radar_llm as llm, paid_strategy_job as job
    cost = job.Cost()
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    seen = []

    def refuses(system, user, schema_, **kw):
        seen.append("call_json")
        raise llm.ModelError("api_error", "BadRequestError: 400 the schema is too complex")
    monkeypatch.setattr(llm, "call_json", refuses)
    monkeypatch.setattr(ai, "text_json", lambda *a, **k: (seen.append("text_json") or {"ok": 1}, {"usage": {"input_tokens": 10, "output_tokens": 5}, "model_served": "claude-sonnet-5-5"}))
    got = job.default_call(cost, client=object())("s", "{}", schema, max_tokens=100, stage="strategy")
    assert got == {"ok": 1} and seen == ["call_json", "text_json"] and cost.total()["calls"] == 1
    seen.clear()
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: (_ for _ in ()).throw(llm.ModelError("api_error", "RateLimitError: 429")))
    with pytest.raises(llm.ModelError):
        job.default_call(cost, client=object())("s", "{}", schema, max_tokens=100, stage="strategy")
    assert seen == []
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: ({"fine": True}, {"usage": {"input_tokens": 1, "output_tokens": 1}, "model_served": "claude-sonnet-5-5"}))
    assert job.default_call(cost, client=object())("s", "{}", schema, max_tokens=100, stage="strategy") == {"fine": True}


def test_the_real_api_refusing_a_schema_as_too_large_falls_back_through_the_real_call_json():
    """Seen in production: call_json without a ledger run lets the SDK's BadRequestError out raw, and only a ModelError was caught, so the build died."""
    import anthropic, httpx
    from tracker import paid_strategy_job as job
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "The compiled grammar is too large, which would cause performance issues. Simplify your tool schemas or reduce the number of strict tools."}, "request_id": "req_x"}
    refused = anthropic.BadRequestError("Error code: 400 - %r" % body, response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), body=body)
    plain = FakeClient([Resp('{"ok": 1}')])
    client = type("C", (), {})()
    client.beta = type("B", (), {})()
    client.beta.messages = type("M", (), {"create": staticmethod(lambda **kw: (_ for _ in ()).throw(refused))})()
    client.messages = plain.messages
    cost = job.Cost()
    schema = {"type": "object", "properties": {"ok": {"type": "integer"}}, "required": ["ok"], "additionalProperties": False}
    assert job.default_call(cost, client=client)("s", "{}", schema, max_tokens=100, stage="architecture") == {"ok": 1}
    assert len(plain.sent) == 1 and "format" not in plain.sent[0]["output_config"] and cost.total()["calls"] == 1
    # a 400 that is not about the schema still stops the stage, as itself
    other = anthropic.BadRequestError("Error code: 400 - prompt is too long", response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), body={})
    client.beta.messages = type("M", (), {"create": staticmethod(lambda **kw: (_ for _ in ()).throw(other))})()
    with pytest.raises(anthropic.BadRequestError):
        job.default_call(cost, client=client)("s", "{}", schema, max_tokens=100, stage="architecture")
    assert len(plain.sent) == 1
    # an error that only MENTIONS the schema, but is not the API's 400, is not a refusal either
    server = anthropic.InternalServerError("Error code: 500 - invalid_request grammar 400 schema", response=httpx.Response(500, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), body={})
    client.beta.messages = type("M", (), {"create": staticmethod(lambda **kw: (_ for _ in ()).throw(server))})()
    with pytest.raises(anthropic.InternalServerError):
        job.default_call(cost, client=client)("s", "{}", schema, max_tokens=100, stage="architecture")
    assert len(plain.sent) == 1


def test_the_model_client_has_room_for_a_long_answer_and_is_made_once_and_only_with_a_key(monkeypatch):
    import anthropic
    from tracker import market_radar_llm as llm, paid_strategy_job as job
    made = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: made.append(kw) or object())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    seen = []
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: (seen.append(k["client"]) or {"a": 1}, {"usage": {}, "model_served": "claude-sonnet-5-5"}))
    call = job.default_call(job.Cost())
    call("s", "{}", {}, max_tokens=10, stage="x")
    call("s", "{}", {}, max_tokens=10, stage="y")
    assert made == [{"api_key": "key", "timeout": 600, "max_retries": 1}] and seen[0] is seen[1]
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(llm.ModelError) as e:
        job.long_client()
    assert e.value.kind == "not_configured"


def test_a_model_call_without_a_ledger_still_reports_what_it_used():
    """market_radar_llm.call_json used to drop the usage of a call with no run id, so a build could not book what it cost."""
    from tracker import market_radar_llm as llm
    client = FakeClient([Resp('{"a": 1}', usage={"input_tokens": 1234000, "output_tokens": 56000})])
    client.beta = client
    parsed, meta = llm.call_json("s", "u", {"type": "object"}, model="claude-sonnet-5-5", max_tokens=100, stage="x", client=client)
    assert parsed == {"a": 1} and meta["usage"] == {"input_tokens": 1234000, "output_tokens": 56000} and meta["model_served"] == "claude-sonnet-5-5"
    from tracker import paid_strategy_job as job
    cost = job.Cost()
    cost.add("x", meta)
    assert cost.total()["stages"][0]["inputTokens"] == 1234000 and cost.total()["usd"] == 3.03


def totals(clicks, conv, cvr):
    return {"history": {"data": {"totals": {"clicks": clicks, "cpc": 4.0, "conv": conv, "cvr": cvr}}}}


def test_the_accounts_own_conversion_rate_is_used_only_with_enough_clicks_and_conversions():
    assert "account's own conversion rate" in ai.forecast(inputs(), totals(200, 5, 2.5))["cvrFrom"]
    assert "assumption" in ai.forecast(inputs(), totals(199, 5, 2.5))["cvrFrom"]            # too few clicks to trust a rate
    assert "assumption" in ai.forecast(inputs(), totals(500, 4, 0.8))["cvrFrom"]            # too few conversions
    assert ai.forecast(inputs(), totals(500, 4, 0.8))["scenarios"][1]["cvr"] == 4.0          # the assumed rate for leads


def test_the_architecture_stage_is_told_which_campaign_types_were_asked_for():
    spy = Spy()
    ai.write_architecture(inputs(campaignTypes=["search", "video"]), research(), {}, {}, spy)
    assert spy.asked[0]["user"]["allowedKinds"] == ["search", "video"]
