"""Keyword Research: what the page may ask for, the cards and the steps (tracker/paid_keywords.py)."""
import pytest

from tests import keyword_fakes as K
from tracker import paid_keywords as pk, paid_strategy as ps


def test_a_good_form_is_normalised_and_marked_as_this_agents():
    i = K.clean(website="http://www.acmedental.com/x?y=1", account="123-456-7890", currency="cad", competitors="smileaustin.com\nAnother Dental, smileaustin.com")
    assert i["kind"] == "keywords" and i["website"] == "https://www.acmedental.com/x" and i["account"] == "1234567890" and i["currency"] == "CAD"
    assert i["competitors"] == [{"name": "smileaustin.com", "url": "https://smileaustin.com/"}, {"name": "Another Dental", "url": ""}]
    assert i["minSearches"] == 10 and i["budget"] is None and i["campaignTypes"] == [] and i["goal"] == "leads" and i["locations"] == "Austin, TX"


def test_a_name_and_one_thing_to_start_from_are_needed():
    for body, msg in (({"website": "x.com"}, "name"), (K.inputs(name="A"), "name"), (K.inputs(website="", account="", services=""), "what the client sells, a website or a Google Ads account"),
                      ("nope", "form"), (None, "form")):
        with pytest.raises(ps.BadPayload) as e:
            pk.clean_inputs(body)
        assert msg in str(e.value)
    assert pk.clean_inputs(K.inputs(website="", account="", services="implants"))["services"] == "implants"
    assert pk.clean_inputs(K.inputs(website="x.com", account="", services=""))["website"] == "https://x.com/"
    assert pk.clean_inputs(K.inputs(website="", account="1234567890", services=""))["account"] == "1234567890"


def test_a_brief_alone_is_not_enough_because_nothing_could_be_looked_up_from_it():
    with pytest.raises(ps.BadPayload):
        pk.clean_inputs({"name": "Acme", "brief": "A long brief about a business with lots of words to say what it does and for whom."})


def test_the_account_the_goal_the_floor_and_the_competitors_are_checked():
    for kw, msg in ((dict(account="12345"), "ten digits"), (dict(goal="fame"), "goal"), (dict(minSearches=7), "smallest monthly search volume"), (dict(minSearches="lots"), "smallest monthly search volume"),
                    (dict(competitors=["a.com", "b.com", "c.com", "d.com"]), "at most 3"), (dict(competitors=5), "list"), (dict(website="not a site"), "web address")):
        with pytest.raises(ps.BadPayload) as e:
            pk.clean_inputs(K.inputs(**kw))
        assert msg in str(e.value), kw


def test_the_smallest_search_volume_defaults_and_takes_the_values_offered():
    assert pk.clean_inputs(K.inputs(minSearches=None))["minSearches"] == 10 and pk.clean_inputs(K.inputs(minSearches=""))["minSearches"] == 10
    assert [pk.clean_inputs(K.inputs(minSearches=n))["minSearches"] for n in pk.MIN_SEARCHES] == list(pk.MIN_SEARCHES) and pk.clean_inputs(K.inputs(minSearches="100"))["minSearches"] == 100


def test_a_competitor_given_twice_by_address_or_by_name_counts_once():
    i = pk.clean_inputs(K.inputs(competitors=["https://www.smileaustin.com/", "smileaustin.com", "Rival", "rival"]))
    assert [c["name"] for c in i["competitors"]] == ["smileaustin.com", "Rival"]


def test_the_free_text_is_cut_to_what_the_agent_reads():
    i = pk.clean_inputs(K.inputs(services="x" * 2000, avoid="y" * 2000, brief="z" * 9000, locations="p" * 900, industry="q" * 300))
    assert len(i["services"]) == 800 and len(i["avoid"]) == 600 and len(i["brief"]) == 3000 and len(i["locations"]) == 500 and len(i["industry"]) == 120


def test_a_card_that_needs_a_website_or_an_account_is_off_without_one():
    i = K.clean(website="", account="")
    assert i["research"] == {"business": True, "website": False, "account": False, "competitors": True}
    j = K.clean(research={"business": False, "competitors": False})
    assert j["research"] == {"business": False, "website": True, "account": True, "competitors": False}


def test_the_cards_say_what_they_need_and_none_uses_paid_data():
    assert [c["key"] for c in pk.CARDS] == pk.CARD_KEYS == ["business", "website", "account", "competitors"]
    assert {c["key"]: c["needs"] for c in pk.CARDS} == {"business": None, "website": "website", "account": "account", "competitors": None}
    assert not any(c["paid"] for c in pk.CARDS) and all(c["blurb"] and c["title"] for c in pk.CARDS) and all(c["default"] for c in pk.CARDS)


def test_the_steps_follow_the_cards_that_are_on_and_keyword_planner_is_always_there():
    keys = lambda i: [s["key"] for s in pk.steps_for(i)]
    assert keys(K.clean()) == ["r:business", "r:website", "r:account", "r:competitors", "r:planner", "a:themes", "a:classify", "a:plan", "f:finish"]
    assert keys(K.clean(website="", account="", research={"business": False, "competitors": False})) == ["r:planner", "a:themes", "a:classify", "a:plan", "f:finish"]
    assert all(s["state"] == "pending" and s["label"] for s in pk.steps_for(K.clean()))


def test_phrases_are_split_cleaned_and_limited():
    assert pk.phrases("dental implants, invisalign;  emergency care\nImplants | invisalign ") == ["dental implants", "invisalign", "emergency care", "Implants"]
    assert pk.phrases("") == [] and pk.phrases(None) == [] and pk.phrases(" , ; ") == [] and len(pk.phrases(",".join("p%d" % i for i in range(40)))) == 20


def test_the_list_row_and_the_kind_test():
    rec = {"id": "a" * 32, "name": "Acme", "status": "done", "created_by": "x@position2.com", "created_at": "t", "account_name": "Acme Dental", "inputs": K.clean(), "stale": 0, "cost": {"usd": 0.5}}
    assert pk.brief(rec) == {"id": "a" * 32, "name": "Acme", "status": "done", "createdBy": "x@position2.com", "createdAt": "t", "accountName": "Acme Dental", "website": "https://acmedental.com/",
                             "industry": "Dental", "stale": False, "cost": 0.5}
    assert pk.is_keywords(rec) and not pk.is_keywords({"inputs": {"kind": "business"}}) and not pk.is_keywords({"inputs": {}}) and not pk.is_keywords(None)
    assert pk.brief(dict(rec, inputs={}, cost=None))["cost"] is None and pk.brief(dict(rec, stale=1))["stale"] is True


def test_it_shares_the_daily_limit_with_the_other_two_agents():
    from tracker import paid_business as pb
    assert pk.BUILDS_PER_DAY == ps.BUILDS_PER_DAY == pb.BUILDS_PER_DAY == 12 and pk.KIND == "keywords" and pk.MAX_COMPETITORS == pk.RIVALS_READ == 3
