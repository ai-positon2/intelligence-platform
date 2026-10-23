"""What a Meta plan is held to (tracker/paid_strategy_meta_check.py): the campaigns, the ad text limits, the buttons, the addresses, the interests Meta has, and the learning bar."""
import pytest

from tracker import paid_strategy as ps
from tracker import paid_strategy_meta_check as mc

INPUTS = {"budget": 3000, "launch": "2026-11-02", "locations": "Austin, TX", "campaignTypes": []}
CTX = {"site": "https://acme.com/", "known": {"acme.com", "acme.com/implants"}}


def camp(kind="metaleads", theme="Implants", share=1, **kw):
    return dict({"kind": kind, "theme": theme, "purpose": "p", "share": share, "landingPage": "/implants", "audience": "adults", "notes": "n", "settings": {}}, **kw)


def test_a_campaign_is_named_for_its_objective_and_gets_meta_standard_answers_where_none_were_written():
    out = mc.architecture({"campaigns": [camp()]}, INPUTS)
    c = out["campaigns"][0]
    assert c["name"] == "P2_MetaLeads_Implants_20261102" and c["kind"] == "metaleads" and c["kindLabel"] == "Meta Leads" and c["objective"] == "Leads" and c["platform"] == "meta"
    st = c["settings"]
    assert set(st) == set(mc.SETTING_KEYS) and st["bidStrategy"]["value"] == "Highest volume" and st["attribution"]["value"] == "7-day click and 1-day view" and st["placements"]["value"] == "Advantage+ placements"
    assert st["optimization"]["value"] == "Maximize number of conversions (leads)" and st["conversionEvent"]["value"] == "Lead" and st["locations"]["value"] == "Austin, TX" and st["specialCategory"]["value"] == "None"
    assert all(v["why"] for v in st.values())
    assert c["share"] == 100.0 and c["daily"] == round(3000 / 30.4, 2) and c["monthly"] == round(c["daily"] * 30.4)


def test_the_settings_the_model_wrote_win_over_the_standard_answers():
    raw = camp(settings={"bidStrategy": {"value": "Cost per result goal of about 40", "why": "history"}, "ages": {"value": "25 to 64", "why": "the offer"}, "optimization": {"value": "Maximize reach", "why": "x"}})
    st = mc.architecture({"campaigns": [raw]}, INPUTS)["campaigns"][0]["settings"]
    assert st["bidStrategy"] == {"value": "Cost per result goal of about 40", "why": "history"} and st["ages"]["value"] == "25 to 64" and st["optimization"]["value"] == "Maximize reach"


def test_traffic_and_awareness_have_no_conversion_event_and_their_own_goal():
    t = mc.architecture({"campaigns": [camp("metatraffic", "Site"), camp("Meta Awareness", "Reach"), camp("sales", "Shop")]}, INPUTS)["campaigns"]
    assert [c["kind"] for c in t] == ["metatraffic", "metaawareness", "metasales"]
    assert t[0]["settings"]["optimization"]["value"] == "Maximize number of landing page views" and t[1]["settings"]["optimization"]["value"] == "Maximize reach" and t[2]["settings"]["conversionEvent"]["value"] == "Purchase"
    assert t[0]["settings"]["conversionEvent"]["value"].startswith("None") and "do not optimise" in t[0]["settings"]["conversionEvent"]["why"]


def test_only_the_kinds_asked_for_and_only_meta_kinds_are_planned():
    raw = {"campaigns": [camp("metaleads"), camp("search", "Brand"), camp("metasales", "Shop"), camp("nonsense", "x"), camp(None, "y")]}
    assert [c["kind"] for c in mc.architecture(raw, INPUTS)["campaigns"]] == ["metaleads", "metasales"]
    assert [c["kind"] for c in mc.architecture(raw, dict(INPUTS, campaignTypes=["metasales", "search"]))["campaigns"]] == ["metasales"]
    assert mc.architecture({"campaigns": [camp("search")]}, INPUTS) == {"campaigns": []} and mc.architecture(None, INPUTS) == {"campaigns": []}


def test_a_campaign_with_a_tiny_share_still_gets_a_dollar_a_day():
    out = mc.architecture({"campaigns": [camp(theme="A", share=99), camp(theme="B", share=1)]}, INPUTS, budget=61)["campaigns"]
    assert out[1]["daily"] >= 1.0 and out[0]["daily"] > 1.0


def test_two_campaigns_on_one_theme_get_different_keys_and_names_and_at_most_six_are_kept():
    out = mc.architecture({"campaigns": [camp(), camp(), camp(theme="Other")]}, INPUTS)["campaigns"]
    assert len({c["key"] for c in out}) == 3 and len({c["name"] for c in out}) == 3 and out[1]["name"].endswith("_2")
    many = mc.architecture({"campaigns": [camp(theme="T%d" % i) for i in range(10)]}, INPUTS)["campaigns"]
    assert len(many) == 6


def test_the_budget_given_is_split_by_share_and_the_share_is_normalised():
    out = mc.architecture({"campaigns": [camp(theme="A", share=60), camp(theme="B", share=20)]}, INPUTS, budget=1500)["campaigns"]
    assert [c["share"] for c in out] == [75.0, 25.0] and round(sum(c["daily"] for c in out), 2) == round(1500 / 30.4, 2) and out[0]["daily"] > out[1]["daily"]
    none = mc.architecture({"campaigns": [camp()]}, dict(INPUTS, budget=None))["campaigns"][0]
    assert none["daily"] is None and none["monthly"] is None


def ad(**kw):
    return dict({"name": "Ad", "format": "image", "primaryText": "Missing a tooth? Book a visit", "headline": "Dental Implants Austin", "description": "Free consult", "cta": "Book Now", "concept": "c", "script": "", "finalUrl": "/implants"}, **kw)


def adset(**kw):
    return dict({"name": "Missing teeth", "theme": "t", "audienceType": "interests", "interests": ["Dentistry"], "behaviors": [], "customAudiences": [], "lookalikes": [], "excludes": [], "budgetShare": 1,
                 "placements": "Advantage+", "landingPage": "/implants", "ads": [ad(), ad(name="B"), ad(name="C")]}, **kw)


def built_of(*sets):
    d = {}
    return mc.build({"adSets": list(sets)}, CTX, d), d


def test_ad_text_is_kept_only_when_it_fits_what_meta_shows_and_what_does_not_is_counted_never_cut():
    (b, d) = built_of(adset(ads=[ad(primaryText="x" * 125), ad(primaryText="x" * 126), ad(headline="h" * 40), ad(headline="h" * 41), ad(description="d" * 30), ad(description="d" * 31)]))
    kept = b["adSets"][0]["ads"]
    assert len(kept) == 4 and [a["primaryText"] == "x" * 125 for a in kept][0] and len(kept[0]["primaryText"]) == 125 and len(kept[1]["headline"]) == 40
    assert d["primary texts"] == 1 and d["headlines"] == 1 and d["descriptions"] == 1 and d["ads"] == 2          # an ad left with no primary text or headline is dropped with it
    assert kept[2]["description"] == "d" * 30 and kept[3]["description"] == ""                                       # a description that is too long is left off, the ad stays


def test_an_ad_needs_a_primary_text_and_a_headline_and_the_button_must_be_one_of_metas():
    (b, d) = built_of(adset(ads=[ad(primaryText=""), ad(headline=" "), ad(cta="book now"), ad(cta="Click Here"), ad(cta="")]))
    ads = b["adSets"][0]["ads"]
    assert len(ads) == 3 and [a["cta"] for a in ads] == ["Book Now", "", ""] and d["ads"] == 2 and d["buttons"] == 1


def test_the_final_address_must_be_the_clients_own_and_defaults_to_the_ad_sets_page():
    (b, d) = built_of(adset(landingPage="/implants", ads=[ad(finalUrl="https://evil.example/x"), ad(finalUrl=""), ad(finalUrl="/implants")]))
    a = b["adSets"][0]["ads"]
    assert a[0]["finalUrl"] == "" and a[1]["finalUrl"] == "https://acme.com/implants" and a[1]["landingVerified"] is True and a[2]["finalUrl"] == "https://acme.com/implants" and d["landing pages"] == 1
    (b, d) = built_of(adset(landingPage="https://other.example/"))
    assert b["adSets"][0]["landingPage"] == "" and d["landing pages"] >= 1


def test_ad_sets_are_cleaned_audience_type_is_one_of_the_four_and_budget_shares_add_up_to_100():
    (b, d) = built_of(adset(audienceType="Lookalike audience", budgetShare=3), adset(name="Two", audienceType="???", budgetShare=1), adset(name="", budgetShare=5), "junk")
    s = b["adSets"]
    assert [x["name"] for x in s] == ["Missing teeth", "Two"] and [x["audienceType"] for x in s] == ["lookalike", "interests"] and [x["budgetShare"] for x in s] == [75.0, 25.0]
    assert mc.build(None, CTX, {}) == {"adSets": [], "assets": {}} and len(mc.build({"adSets": [adset(name="S%d" % i) for i in range(10)]}, CTX, {})["adSets"]) == 6


def test_at_most_eight_ads_in_an_ad_set_and_markup_and_dashes_are_taken_out_of_text():
    (b, _d) = built_of(adset(ads=[ad(name="A%d" % i, headline="Headline %d" % i) for i in range(12)]))
    assert len(b["adSets"][0]["ads"]) == 8
    (b, _d) = built_of(adset(ads=[ad(primaryText="<b>Book</b> today — now", headline="Fast – Easy")]))
    a = b["adSets"][0]["ads"][0]
    assert a["primaryText"] == "Book today, now" and a["headline"] == "Fast, Easy"


def test_interests_are_checked_against_metas_list_and_one_meta_does_not_have_is_dropped_and_counted():
    (b, d) = built_of(adset(interests=["Dentistry", "Teeth whitening", "Made up interest"]))
    answers = {"Dentistry": {"id": "1", "name": "Dentistry", "sizeText": "9.0M to 10.0M"}, "Teeth whitening": {"id": "2", "name": "Teeth whitening", "sizeText": ""}, "Made up interest": None}
    out, dropped = mc.verify_interests(b, answers)
    s = out["adSets"][0]
    assert dropped == ["Made up interest"] and s["interests"] == ["Dentistry", "Teeth whitening"] and out["drops"]["interests"] == 1
    assert s["interestsChecked"] == [{"name": "Dentistry", "id": "1", "sizeText": "9.0M to 10.0M", "verified": True}, {"name": "Teeth whitening", "id": "2", "sizeText": "", "verified": True}]


def test_when_meta_did_not_answer_the_interests_are_kept_and_marked_unverified():
    (b, _d) = built_of(adset(interests=["Dentistry"]))
    out, dropped = mc.verify_interests(b, {"Dentistry": None}, unanswered=True)
    assert dropped == [] and out["adSets"][0]["interests"] == ["Dentistry"] and out["adSets"][0]["interestsChecked"] == [{"name": "Dentistry", "id": "", "sizeText": "", "verified": False}]
    assert "interests" not in out.get("drops", {})


def test_the_claim_scan_gets_every_primary_text_headline_and_description_with_where_it_is():
    (b, _d) = built_of(adset(ads=[ad(), ad(description="")]))
    texts = mc.ad_texts(b)
    assert texts[0] == ("Missing teeth, ad 1 primary text", "Missing a tooth? Book a visit") and ("Missing teeth, ad 1 description", "Free consult") in texts and len(texts) == 5


def test_what_a_meta_build_is_short_of_is_said():
    assert mc.shortfalls({}) == ["No ad sets were written."]
    (b, _d) = built_of(adset())
    assert mc.shortfalls(b) == []
    (b, _d) = built_of(adset(interests=[], audienceType="interests"), adset(name="Two", ads=[ad()]), adset(name="Three", interests=[], audienceType="broad"))
    out = mc.shortfalls(b)
    assert len(out) == 2 and "Ad set Missing teeth has no audience" in out[0] and "Ad set Two has 1 ad after" in out[1] and "at least 3" in out[1]
    (b, _d) = built_of(adset(ads=[ad(name="A%d" % i, headline="H %d" % i) for i in range(8)]))
    b["adSets"][0]["ads"] += b["adSets"][0]["ads"][:3]
    assert "11 ads: more than 10" in mc.shortfalls(b)[0]


def research(cpa=40.0, kind="LEADS"):
    return {"metahistory": {"data": {"campaigns": [{"objective": kind, "cost": 800.0, "conv": 800.0 / cpa, "cpa": cpa}, {"objective": "SALES", "cost": 500.0, "conv": 5.0, "cpa": 100.0}], "totals": {"cpa": 65.0}}}}


def planned(daily, **kw):
    (b, _d) = built_of(adset(budgetShare=3), adset(name="Two", budgetShare=1))
    return [dict({"name": "P2_MetaLeads_X_20261102", "platform": "meta", "kind": "metaleads", "objective": "Leads", "daily": daily, "build": b}, **kw)]


def test_an_ad_set_that_cannot_reach_about_fifty_results_a_week_is_named_with_the_numbers():
    out, unjudged = mc.learning_gaps(planned(100.0), research(40.0))
    assert unjudged == 0 and len(out) == 2                                  # 75 a day is about 13 results a week at 40, 25 a day about 4
    assert "ad set Missing teeth gets 75.00 a day, about 13 results a week at 40.00 each (the account's last 90 days on Leads campaigns)" in out[0] and "about 50 a week" in out[0]
    assert "ad set Two gets 25.00 a day, about 4 results a week" in out[1]
    ok, _ = mc.learning_gaps(planned(2000.0), research(40.0))
    assert ok == []                                                         # 1500 a day is 262 results a week


def one_set(daily):
    (b, _d) = built_of(adset(budgetShare=1))
    return [{"name": "P2_MetaLeads_X_20261102", "platform": "meta", "kind": "metaleads", "objective": "Leads", "daily": daily, "build": b}]


def test_the_bar_is_exactly_fifty_results_a_week():
    at, _ = mc.learning_gaps(one_set(100.0), research(14.0))                               # 100 a day for 7 days at 14 a result is exactly 50
    assert at == []
    under, _ = mc.learning_gaps(one_set(99.99), research(14.0))
    assert len(under) == 1 and "about 49 results a week" in under[0]                       # a result a week short of the bar is said


def test_the_cost_of_a_result_counts_the_campaigns_of_the_objective_that_brought_nothing_and_falls_back_to_the_overall_one_and_says_so():
    r = research(40.0)
    r["metahistory"]["data"]["campaigns"].append({"objective": "LEADS", "cost": 800.0, "conv": 0.0, "cpa": None})      # 1600 spent for 20 results is 80 a result, not 40
    out, _ = mc.learning_gaps(one_set(100.0), r)
    assert "at 80.00 each" in out[0] and "last 90 days on Leads campaigns" in out[0]
    other, _ = mc.learning_gaps(planned(100.0), research(40.0, kind="SALES"))              # no Leads history: the overall 65 is used and said
    assert "the account's last 90 days across every objective" in other[0] and "at 65.00 each" in other[0]


def test_nothing_is_judged_without_a_cost_per_result_and_traffic_and_awareness_are_not_judged():
    out, unjudged = mc.learning_gaps(planned(10.0), {})
    assert out == [] and unjudged == 1
    out, unjudged = mc.learning_gaps(planned(10.0, kind="metatraffic", objective="Traffic"), research())
    assert out == [] and unjudged == 1
    out, unjudged = mc.learning_gaps(planned(None), research())
    assert out == [] and unjudged == 0
    google = planned(10.0, platform="google")
    assert mc.learning_gaps(google, research()) == ([], 0)
