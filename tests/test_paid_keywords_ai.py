"""Keyword Research: the judging (tracker/paid_keywords_ai.py): what the model is asked, what it is shown, and that its answers go through the checks."""
import json

from tests import keyword_fakes as K
from tracker import paid_keywords_ai as kai, paid_keywords_check as kck, paid_keywords_data as kd, paid_strategy_ai as ai


def walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def prepared():
    inputs = K.clean()
    research = {"planner": {"data": {"ideas": [dict(kd.parse_idea(K.mk(k)), **{"from": ["seed: dental implants"]}) for k in K.MARKET]}},
                "account": {"data": {"keywords": K.ACCOUNT_KEYWORDS, "negatives": K.ACCOUNT_NEGATIVES, "lists": K.ACCOUNT_LISTS, "campaigns": [{"name": "P2_Search_Implants_20250101", "keywords": 2, "adGroups": 2}, {"name": "P2_Search_Invisalign_20250101", "keywords": 1, "adGroups": 1}],
                                     "keywordCount": 5, "negativeCount": 2, "terms": {"converting": [{"term": "all on 4 implants austin tx", "clicks": 44, "cost": 380.0, "conv": 4.0, "campaign": "C"}], "wasteful": [{"term": "dentist jobs austin"}]}}},
                "business": {"data": K.BUSINESS_PLAN and {"summary": "s", "offerings": [{"name": "Dental implants", "priority": "high"}], "searches": ["dental implants austin"], "vocabulary": [], "avoidWords": [], "compliance": [{"topic": "t", "rule": "r"}], "seasonality": []}},
                "website": {"data": {"pages": [{"url": "https://acmedental.com/", "title": "Acme Dental", "h1": ["Your Austin Family Dentist"], "h2": ["Implants"]}]}}}
    cands, _ = kd.candidates(inputs, research)
    return inputs, research, cands


class Spy:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, system, user, schema, *, max_tokens, stage):
        self.calls.append({"system": system, "user": json.loads(user), "schema": schema, "max_tokens": max_tokens, "stage": stage})
        return self.reply(stage, json.loads(user)) if callable(self.reply) else self.reply


# ── the schemas ─────────────────────────────────────────────────────────────

def test_the_schemas_are_strings_and_lists_only_so_the_api_never_finds_them_too_large():
    for schema in (kai.THEMES, kai.CLASSIFY, kai.PLAN):
        nodes = list(walk(schema))
        assert not any(isinstance(n, dict) and ("enum" in n or "const" in n or "anyOf" in n or "pattern" in n) for n in nodes)
        assert len(json.dumps(schema)) < 3000
        assert all(n.get("additionalProperties") is False and n["required"] == list(n["properties"]) for n in nodes if isinstance(n, dict) and n.get("type") == "object")


def test_the_schemas_ask_for_what_the_checks_read():
    keys = lambda schema: set(schema["properties"])
    assert keys(kai.THEMES) == {"themes", "assumptions"} and set(kai.THEMES["properties"]["themes"]["items"]["properties"]) == {"key", "name", "intent", "why", "existingCampaign", "priority"}
    assert set(kai.CLASSIFY["properties"]["rows"]["items"]["properties"]) == {"kw", "fit", "intent", "theme", "match", "priority", "note"}
    assert keys(kai.PLAN) == {"summary", "quickWins", "negatives", "tests", "questions", "risks", "seasonal"}


def test_the_prompts_say_the_house_rules_and_that_the_numbers_are_not_the_models():
    for system in (kai.THEMES_SYSTEM, kai.CLASSIFY_SYSTEM, kai.PLAN_SYSTEM):
        assert ai.HOUSE in system and "Never write a number of your own" in system and "do not estimate clicks, cost, conversions or revenue" in system
        assert "—" not in system and "–" not in system
    assert "Never promise a result" in kai.PLAN_SYSTEM and "smallest word or short phrase" in kai.PLAN_SYSTEM and "never the whole search" in kai.PLAN_SYSTEM
    assert "bidding on a rival is the client's decision" in kai.CLASSIFY_SYSTEM and "`kw` copied exactly as given" in kai.CLASSIFY_SYSTEM
    assert "Never invent a campaign name" in kai.THEMES_SYSTEM


# ── what the model is shown ─────────────────────────────────────────────────

def test_a_keyword_is_shown_as_an_object_with_its_searches_competition_and_what_the_account_has():
    row = kai.keyword_row({"kw": "cost of dental implants", "avg": 8100, "comp": "Medium", "status": "covered", "by": {"kw": "dental implants", "match": "Phrase"}, "from": ["seed: x"]})
    assert row == {"kw": "cost of dental implants", "searchesAMonth": 8100, "competition": "Medium", "inTheAccount": "already matched by dental implants (Phrase)", "foundFrom": "seed: x"}
    assert kai.keyword_row({"kw": "a", "avg": 1, "comp": "", "status": "bidding"})["inTheAccount"] == "the account bids on it" and kai.keyword_row({"kw": "a", "avg": None, "comp": "", "status": "new"})["inTheAccount"] == "new to the account"
    unknown = kai.keyword_row({"kw": "a", "avg": None, "comp": "", "status": "new"})
    assert unknown["searchesAMonth"] is None and unknown["competition"] is None and "convertedAsASearchTerm" not in unknown and "foundFrom" not in unknown
    extra = kai.keyword_row({"kw": "a", "avg": 1, "comp": "", "status": "new", "conv": {"conv": 4.0}, "brand": True, "rival": "Smile"})
    assert extra["convertedAsASearchTerm"] == 4.0 and extra["holdsTheClientsOwnName"] is True and extra["namesACompetitor"] == "Smile"


def test_the_themes_question_shows_the_client_the_business_the_campaigns_and_a_sample_of_the_biggest_ideas():
    inputs, research, cands = prepared()
    spy = Spy(K.THEMES)
    out = kai.write_themes(inputs, research, cands, spy)
    c = spy.calls[0]
    assert c["stage"] == "themes" and c["schema"] is kai.THEMES and c["max_tokens"] == 4000 and c["system"] is kai.THEMES_SYSTEM
    u = c["user"]
    assert u["client"]["name"] == "Acme Dental" and u["business"]["offerings"][0]["name"] == "Dental implants" and u["account"]["campaigns"][0]["name"] == "P2_Search_Implants_20250101"
    assert u["siteHeadings"][0]["h1"] == ["Your Austin Family Dentist"] and 0 < len(u["keywordIdeaSample"]) <= 120
    sample = [x["searches"] or 0 for x in u["keywordIdeaSample"]]
    assert sample == sorted(sample, reverse=True) and set(u["keywordIdeaSample"][0]) == {"kw", "searches", "status"}
    assert [t["key"] for t in out["themes"]] == ["implants", "invisalign", "emergency", "family", "brand"] and out["themes"][1]["campaign"] == "P2_Search_Invisalign_20250101"


def test_the_account_evidence_does_not_carry_the_thousands_of_keywords():
    _, research, _ = prepared()
    ev = kai.account_evidence(research)
    assert set(ev) == {"campaigns", "keywordCount", "negativeCount", "convertingSearchTerms", "wastefulSearchTerms"} and kai.account_evidence({}) is None
    assert kai.business_evidence({}) is None and kai.site_headings({}) is None


def test_one_chunk_is_asked_with_its_keywords_the_themes_the_avoid_list_and_the_rules_of_the_business():
    inputs, research, cands = prepared()
    inputs = dict(inputs, avoid="dentures")
    themes = kck.themes(K.THEMES, [])["themes"]
    spy = Spy(lambda stage, user: {"rows": [dict(K.judge(k["kw"]), kw=k["kw"]) for k in user["keywords"]]})
    judged, dropped = kai.write_classify(inputs, research, themes, cands[:10], spy)
    c = spy.calls[0]
    assert c["stage"] == "classify" and c["max_tokens"] == 9000 and c["schema"] is kai.CLASSIFY
    u = c["user"]
    assert [t["key"] for t in u["themes"]] == ["implants", "invisalign", "emergency", "family", "brand"] and u["avoid"] == ["dentures"] and u["competitorsNamed"] == ["smileaustin.com"]
    assert u["offerings"] == [{"name": "Dental implants", "priority": "high"}] and u["rulesThatTouchAds"] == [{"topic": "t", "rule": "r"}] and len(u["keywords"]) == 10
    assert len(judged) == 10 and dropped == 0 and set(judged) == {x["kw"] for x in cands[:10]}


def test_what_the_model_judges_goes_through_the_checks():
    inputs, research, cands = prepared()
    themes = kck.themes(K.THEMES, [])["themes"]
    first = cands[0]["kw"]
    spy = Spy({"rows": [dict(K.judge(first), kw=first, fit="superb", theme="Dental Implants"), dict(K.judge("x"), kw="a keyword that was never asked")]})
    judged, dropped = kai.write_classify(inputs, research, themes, cands[:5], spy)
    assert list(judged) == [first] and judged[first]["fit"] == "maybe" and judged[first]["theme"] == "implants" and dropped == 1


def test_the_plan_question_carries_the_numbers_the_judgement_and_what_the_account_already_has():
    inputs, research, cands = prepared()
    themes = kck.themes(K.THEMES, ["P2_Search_Implants_20250101"])["themes"]
    a = kd.assemble(cands, {c["kw"]: dict(K.judge(c["kw"]), theme=kck.theme_key(K.judge(c["kw"])["theme"], themes)) for c in cands}, themes)
    spy = Spy(K.PLAN)
    out = kai.write_plan(inputs, research, a, spy)
    c = spy.calls[0]
    u = c["user"]
    assert c["stage"] == "plan" and c["max_tokens"] == 9000 and u["coverage"]["keywordsThatFit"] == a["coverage"]["yes"] and u["coverage"]["percentOfTheKeywordsJudgedAFitTheAccountAlreadyCovers"] == a["coverage"]["pct"]
    assert u["coverage"]["percentOfTheMarketsSearchesTheAccountAlreadyCovers"] == a["coverage"].get("poolPct") and u["coverage"]["newKeywordsThatFit"] == a["coverage"]["new"]
    assert u["themes"][0]["name"] and "newSearches" in u["themes"][0] and "pattern" in u["themes"][0]
    assert 0 < len(u["topNewKeywords"]) <= 30 and all(k["searches"] is None or isinstance(k["searches"], int) for k in u["topNewKeywords"])
    assert u["convertingTermsToAdd"] and u["judgedNo"] and u["existingNegatives"] == ["jobs", "dallas"]
    assert u["competitorKeywords"] == [r["kw"] for r in a["keywords"] if r["intent"] == "competitor"]
    assert [n["kw"] for n in out["negatives"]] == ["dental assistant", "how long", "gem"] and out["questions"][0]["urgency"] == "Before launch"


def test_the_plan_is_asked_about_new_keywords_only_and_biggest_first():
    inputs, research, cands = prepared()
    themes = kck.themes(K.THEMES, [])["themes"]
    a = kd.assemble(cands, {c["kw"]: dict(K.judge(c["kw"]), theme=kck.theme_key(K.judge(c["kw"])["theme"], themes)) for c in cands}, themes)
    spy = Spy(K.PLAN)
    kai.write_plan(inputs, research, a, spy)
    top = spy.calls[0]["user"]["topNewKeywords"]
    by = {r["kw"]: r for r in a["keywords"]}
    assert top and all(by[t["kw"]]["status"] == "new" and by[t["kw"]]["fit"] == "yes" for t in top)
    assert [t["searches"] or 0 for t in top] == sorted([t["searches"] or 0 for t in top], reverse=True)


# ── what the mutation tests of this file found missing ─────────────────────

def test_the_biggest_ideas_are_shown_for_the_themes_in_any_order_they_arrive_and_all_of_a_small_list():
    inputs, research, cands = prepared()
    spy = Spy(K.THEMES)
    kai.write_themes(inputs, research, list(reversed(cands)), spy)
    sample = spy.calls[0]["user"]["keywordIdeaSample"]
    assert len(sample) == len(cands) and [s["searches"] or 0 for s in sample] == sorted((c.get("avg") or 0 for c in cands), reverse=True)
    many = [dict(cands[0], kw="keyword number %d" % i, avg=1000 + i) for i in range(200)]
    spy = Spy(K.THEMES)
    kai.write_themes(inputs, research, many, spy)
    sample = spy.calls[0]["user"]["keywordIdeaSample"]
    assert len(sample) == 120 and sample[0]["searches"] == 1199 and sample[-1]["searches"] == 1080


def test_the_plan_is_shown_the_biggest_new_keywords_the_biggest_not_a_fit_and_the_first_negatives_the_account_has():
    inputs, research, cands = prepared()
    research["account"]["data"]["negatives"] = [{"t": "neg%d" % i, "m": "PHRASE", "c": ["C"]} for i in range(100)]
    themes = kck.themes(K.THEMES, [])["themes"]
    base = kd.assemble(cands, {c["kw"]: dict(K.judge(c["kw"]), theme=kck.theme_key(K.judge(c["kw"])["theme"], themes)) for c in cands}, themes)
    template = next(r for r in base["keywords"] if r["fit"] == "yes" and r["status"] == "new")
    rows = [dict(template, kw="new keyword %03d" % i, avg=2000 - i) for i in range(50)] + [dict(template, kw="bad keyword %03d" % i, fit="no", avg=900 - i, note="No") for i in range(60)]
    spy = Spy(K.PLAN)
    kai.write_plan(inputs, research, dict(base, keywords=rows), spy)
    u = spy.calls[0]["user"]
    assert [k["kw"] for k in u["topNewKeywords"]] == ["new keyword %03d" % i for i in range(30)]
    assert [k["kw"] for k in u["judgedNo"]] == ["bad keyword %03d" % i for i in range(40)] and u["existingNegatives"] == ["neg%d" % i for i in range(60)]
    assert all(k["searches"] is not None for k in u["topNewKeywords"])
