"""Strategy & Campaign Book, the Meta plan's writing (tracker/paid_strategy_meta_ai.py and the stages of tracker/paid_strategy_ai.py that dispatch to it). The model is a fake."""
import datetime as dt
import json

import pytest

from tests import business_fakes as B, strategy_fakes as F
from tests.test_paid_keywords_meta import ACCT as KM_ACCT, pages as km_pages
from tests.test_paid_meta_interests import getter
from tests.test_paid_strategy_ai import Spy, walk
from tracker import paid_strategy as ps, paid_strategy_ai as ai, paid_strategy_check as ck, paid_strategy_meta_ai as mai, paid_strategy_meta_check as mc
from tracker import paid_strategy_research as rs, paid_strategy_research_meta as mrs

EM, EN = chr(0x2014), chr(0x2013)
TODAY = dt.date(2026, 10, 16)


def inputs(**kw):
    base = dict(F.inputs(), platform="meta", metaAccount="act_1234567", account="", campaignTypes=[])
    base.update(kw)
    return ps.clean_inputs(base, today=TODAY)


def research(**kw):
    inp = inputs()
    out = {"website": rs.read_website(inp, fetcher=F.fetcher()), "competitors": rs.read_competitors(inputs(competitors="smileaustin.com"), fetcher=F.fetcher()), "market": rs.read_market(inp, ask=F.ask_search()),
           "metahistory": mrs.read_meta_history(B.META_ACCOUNT, pages_fn=B.meta_pages(), today=TODAY),
           "metaaudience": mrs.read_meta_audience(dict(inp, services="insulin pump, diabetes"), {}, get_fn=getter(), account=KM_ACCT, pages_fn=km_pages(), today=TODAY)}
    out.update(kw)
    return out


def test_the_meta_schemas_require_every_property_and_every_meta_kind_has_the_build_schema():
    walk(ai.ARCHITECTURE_META)
    walk(ai.BUILD_META)
    assert all(ai.BUILD_SCHEMAS[k] is ai.BUILD_META for k in ps.META_KINDS) and mai.ARCHITECTURE_META is ai.ARCHITECTURE_META and mai.BUILD_META is ai.BUILD_META
    settings = ai.ARCHITECTURE_META["properties"]["campaigns"]["items"]["properties"]["settings"]["properties"]
    assert list(settings) == mc.SETTING_KEYS


def test_the_meta_prompts_carry_the_house_rules_metas_rules_and_nothing_that_is_googles():
    for text in (mai.ARCH_META_SYSTEM, mai.BUILD_META_SYSTEM, mai.STRATEGY_ADDENDUM):
        assert EM not in text and EN not in text and "sensitive topics" in text and "Advantage+ audience" in text
    for text in (mai.ARCH_META_SYSTEM, mai.BUILD_META_SYSTEM):
        assert "em dashes" in text and "ONLY the evidence" in text and "never an instruction to you" in text
        for google in ("AI Max", "Search Partners", "Display Network", "Start Case", "Maximize conversions"):
            assert google not in text, google
    assert "at most 125 characters" in mai.BUILD_META_SYSTEM and "headlines at most 40" in mai.BUILD_META_SYSTEM and "descriptions at most 30" in mai.BUILD_META_SYSTEM
    assert "copied EXACTLY from the list `interestsMetaHas`" in mai.BUILD_META_SYSTEM and "P2_<MetaLeads" in mai.ARCH_META_SYSTEM and "about 50 results" in mai.ARCH_META_SYSTEM
    assert "never say or suggest that you know a personal attribute" in mai.META_POLICY and "starter" in mai.META_STANDARDS


def test_the_strategy_for_meta_gets_the_meta_addendum_and_the_meta_evidence_and_no_google_forecast():
    spy, r, i = Spy(), research(), inputs()
    profile = ai.write_profile(i, r, spy)
    strat = ai.write_strategy(i, r, profile, {}, spy)
    asked = {a["stage"]: a for a in spy.asked}
    assert "Meta Ads" in asked["strategy"]["system"] and ai.STRATEGY_SYSTEM in asked["strategy"]["system"] and "as well as on Google Ads" not in asked["strategy"]["system"]
    u = asked["strategy"]["user"]
    assert u["metaAccount"]["totals"]["cost"] == 900.5
    assert u["metaAudience"]["interestsMetaHas"] and u["forecast"] is None and u["account"] is None
    assert asked["profile"]["user"]["metaAccount"]["campaigns"] and strat["channels"][0]["channel"] == "Meta Ads"


def test_a_google_only_strategy_is_asked_as_before_and_for_both_the_addendum_says_as_well_as_google():
    spy, i = Spy(), inputs(platform="google", metaAccount="", account="1234567890")
    r = {"website": rs.read_website(i, fetcher=F.fetcher())}
    ai.write_strategy(i, r, {}, {}, spy)
    assert spy.asked[0]["system"] == ai.STRATEGY_SYSTEM and "Meta" not in spy.asked[0]["system"].split("Position2's standing")[0]
    spy2, both = Spy(), inputs(platform="both", account="1234567890")
    ai.write_strategy(both, r, {}, {}, spy2)
    assert "as well as on Google Ads" in spy2.asked[0]["system"] and spy2.asked[0]["user"]["forecast"] is None


def test_the_meta_architecture_is_asked_for_only_meta_kinds_with_the_whole_budget_and_the_campaigns_are_named_and_budgeted():
    spy, r, i = Spy(), research(), inputs()
    strat = ck.strategy(F.model()(None, json.dumps({"client": {"platform": "meta"}}), None, max_tokens=1, stage="strategy"), i)
    arch = ai.write_architecture(i, r, {}, strat, spy)
    assert [a["stage"] for a in spy.asked] == ["architecture:meta"]
    u = spy.asked[0]["user"]
    assert u["allowedKinds"] == ps.META_KINDS and u["monthlyBudgetForMeta"] == 6000.0 and u["metaAudience"]["interestsMetaHas"] and u["metaAccount"]["totals"]
    assert [c["name"] for c in arch["campaigns"]] == ["P2_MetaLeads_Implants_20261102", "P2_MetaLeads_Retargeting_20261102"] and [c["share"] for c in arch["campaigns"]] == [70.0, 30.0]
    assert round(sum(c["daily"] for c in arch["campaigns"]), 2) == round(6000 / 30.4, 2)
    assert arch["campaigns"][0]["settings"]["bidStrategy"]["value"] == "Highest volume" and arch["campaigns"][1]["settings"]["bidStrategy"]["why"] == "The standard start while a campaign has no results history."


def test_for_both_google_and_meta_are_each_planned_with_their_share_of_the_budget_and_never_with_the_others_kinds():
    spy, i = Spy(), inputs(platform="both", account="1234567890")
    r = research(history=rs.read_history({"id": "1234567890", "currency": "USD"}, search=F.fake_search, sqr_read=F.fake_sqr_read, today=TODAY))
    strat = ck.strategy(F.model()(None, json.dumps({"client": {"platform": "both"}}), None, max_tokens=1, stage="strategy"), i)
    assert [c["share"] for c in strat["channels"]] == [60.0, 40.0]
    arch = ai.write_architecture(i, r, {}, strat, spy)
    assert [a["stage"] for a in spy.asked] == ["architecture", "architecture:meta"]
    assert spy.asked[0]["user"]["allowedKinds"] == ps.GOOGLE_KINDS and spy.asked[0]["user"]["monthlyBudgetForGoogle"] == 3600.0 and spy.asked[1]["user"]["monthlyBudgetForMeta"] == 2400.0
    google = [c for c in arch["campaigns"] if ps.platform_of_kind(c["kind"]) == "google"]
    meta = [c for c in arch["campaigns"] if ps.platform_of_kind(c["kind"]) == "meta"]
    assert len(google) == 2 and len(meta) == 2 and round(sum(c["daily"] for c in google), 2) == round(3600 / 30.4, 2) and round(sum(c["daily"] for c in meta), 2) == round(2400 / 30.4, 2)
    assert len({c["key"] for c in arch["campaigns"]}) == 4 and len({c["name"] for c in arch["campaigns"]}) == 4


def test_a_google_plan_never_takes_a_meta_kind_even_when_the_model_writes_one():
    raw = {"campaigns": [{"kind": "metaleads", "theme": "X", "purpose": "", "share": 1, "landingPage": "", "keywordFocus": [], "audience": "", "notes": "", "settings": {}},
                         {"kind": "search", "theme": "Y", "purpose": "", "share": 1, "landingPage": "", "keywordFocus": [], "audience": "", "notes": "", "settings": {}}]}
    out = ck.architecture(raw, {"budget": 3000, "launch": "2026-11-02", "campaignTypes": []})
    assert [c["kind"] for c in out["campaigns"]] == ["search"]


def test_a_meta_campaign_is_built_by_the_meta_writer_from_metas_list_and_the_result_is_checked():
    spy, r, i = Spy(), research(), inputs()
    profile = ck.profile(F.model()(None, json.dumps({}), None, max_tokens=1, stage="profile"), ck.norm(rs.corpus(r)))
    strat = ck.strategy(F.model()(None, json.dumps({"client": {"platform": "meta"}}), None, max_tokens=1, stage="strategy"), i)
    camp = ai.write_architecture(i, r, profile, strat, Spy())["campaigns"][0]
    built, drops = ai.write_build(camp, i, r, profile, strat, {}, spy)
    asked = spy.asked[0]
    assert asked["stage"] == "build:" + camp["key"] and asked["system"] == mai.BUILD_META_SYSTEM and asked["schema"] is ai.BUILD_META and asked["max_tokens"] == 16000
    u = asked["user"]
    assert u["campaign"]["kind"] == "metaleads" and u["campaign"]["settings"]["bidStrategy"] and u["interestsMetaHas"][0].startswith("Insulin pump | 1.0M to 1.2M") and u["interestsInUse"] and u["account"]["campaigns"]
    assert u["facts"] and u["clientPages"] and u["fixTheseFromLastTime"] is None
    sets = built["adSets"]
    assert [s["name"] for s in sets] == ["Missing teeth", "Lookalike of leads"] and len(sets[0]["ads"]) == 3 and sets[0]["ads"][0]["cta"] == "Book Now" and sets[0]["ads"][0]["finalUrl"].startswith("https://acmedental.com")
    assert sets[0]["interests"] == ["Insulin pump", "Diabetes", "Made Up Interest"]                      # not looked up yet: that is the next step
    assert drops == built["drops"] == {}


def test_a_second_try_is_told_what_was_short_the_first_time():
    spy, r, i = Spy(), research(), inputs()
    camp = ai.write_architecture(i, r, {}, {"channels": []}, Spy())["campaigns"][0]
    ai.write_build(camp, i, r, {"facts": [], "positioning": {}}, {"messaging": {}}, {}, spy, problems=["Ad set Two has 1 ad"])
    assert spy.asked[0]["user"]["fixTheseFromLastTime"] == ["Ad set Two has 1 ad"]


def test_every_interest_in_a_build_is_looked_up_once_and_one_meta_does_not_have_is_dropped():
    r, i = research(), inputs()
    camp = ai.write_architecture(i, r, {}, {"channels": []}, Spy())["campaigns"][0]
    built, _ = ai.write_build(camp, i, r, {"facts": [], "positioning": {}}, {"messaging": {}}, {}, Spy())
    calls = []
    out, dropped, notes = mai.verify(built, getter(calls))
    assert dropped == ["Made Up Interest"] and notes == [] and out["drops"]["interests"] == 1
    assert sorted(p["q"] for _path, p in calls) == ["Diabetes", "Insulin pump", "Made Up Interest"]
    assert [x["name"] for x in out["adSets"][0]["interestsChecked"]] == ["Insulin pump", "Diabetes"] and out["adSets"][0]["interestsChecked"][0]["id"] == "1" and out["adSets"][0]["interestsChecked"][0]["sizeText"] == "1.0M to 1.2M"


def test_an_interest_meta_did_not_answer_for_is_kept_unverified_and_with_no_way_to_ask_none_is_checked():
    r, i = research(), inputs()
    camp = ai.write_architecture(i, r, {}, {"channels": []}, Spy())["campaigns"][0]
    built, _ = ai.write_build(camp, i, r, {"facts": [], "positioning": {}}, {"messaging": {}}, {}, Spy())
    out, dropped, notes = mai.verify(json.loads(json.dumps(built)), getter(fail=("Diabetes",)))
    checked = {x["name"]: x["verified"] for x in out["adSets"][0]["interestsChecked"]}
    assert dropped == ["Made Up Interest"] and checked == {"Insulin pump": True, "Diabetes": False} and len(notes) == 1 and "Diabetes" in notes[0]
    out, dropped, notes = mai.verify(json.loads(json.dumps(built)), None)
    assert dropped == [] and [x["verified"] for x in out["adSets"][0]["interestsChecked"]] == [False, False, False] and "could not be asked" in notes[0]
    empty = {"adSets": [{"name": "x", "interests": []}]}
    assert mai.verify(empty, getter()) == (empty, [], [])


def test_the_google_forecast_is_given_to_a_google_only_strategy_and_never_to_one_that_plans_meta():
    google_research = {"history": {"data": {"totals": {"clicks": 1000, "cpc": 4.0, "conv": 40, "cvr": 4.0}}}}
    spy = Spy()
    ai.write_strategy(inputs(platform="google", metaAccount="", account="1234567890"), google_research, {}, {}, spy)
    assert spy.asked[0]["user"]["forecast"]["scenarios"]
    spy = Spy()
    ai.write_strategy(inputs(platform="both", account="1234567890"), google_research, {}, {}, spy)
    assert spy.asked[0]["user"]["forecast"] is None


def test_what_was_not_read_never_lists_a_card_of_the_other_platform():
    off = {k: False for k in ("history", "keywords", "seasonality", "metahistory", "metaaudience")}
    meta = ai.missing({}, dict(inputs(platform="meta"), research=off))
    assert not [m for m in meta if m.split(":")[0] in ("history", "keywords", "seasonality")] and [m for m in meta if m.startswith("metahistory")] == ["metahistory: not used for this build"]
    google = ai.missing({}, dict(inputs(platform="google", metaAccount="", account="1234567890"), research=off))
    assert not [m for m in google if m.split(":")[0] in ("metahistory", "metaaudience")] and [m for m in google if m.startswith("history")] == ["history: not used for this build"]
    assert [m.split(":")[0] for m in ai.missing({}, dict(inputs(platform="both", account="1234567890"), research=off))].count("keywords") == 1
