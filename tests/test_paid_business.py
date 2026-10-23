"""Business Research: what the page may ask for and what a build's steps are (tracker/paid_business.py)."""
import pytest

from tracker import paid_business as pb, paid_strategy as ps
from tests import business_fakes as B


def test_the_cards_are_the_seven_the_page_offers_and_say_what_they_need():
    assert [c["key"] for c in pb.CARDS] == ["website", "reputation", "social", "history", "metahistory", "competitors", "market"]
    assert {c["key"]: c["needs"] for c in pb.CARDS if c["needs"]} == {"website": "website", "history": "account", "metahistory": "metaAccount"}
    assert {c["key"] for c in pb.CARDS if c["paid"]} == {"reputation", "social", "market"} and all(c["default"] for c in pb.CARDS)
    assert all(c["title"] and c["blurb"] for c in pb.CARDS) and set(pb.RESEARCH_LABELS) == set(pb.CARD_KEYS)


def test_a_client_is_cleaned_and_marked_as_a_business_research_build():
    i = B.clean(website="http://www.AcmeDental.com/x?y=1#z", account="123-456-7890", competitors="smileaustin.com\nAnother Dental, smileaustin.com")
    assert i["kind"] == "business" and i["website"] == "https://www.acmedental.com/x" and i["account"] == "1234567890" and i["budget"] is None and i["campaignTypes"] == []
    assert i["competitors"] == [{"name": "smileaustin.com", "url": "https://smileaustin.com/"}, {"name": "Another Dental", "url": ""}]
    assert i["research"] == {k: k != "metahistory" for k in pb.CARD_KEYS} and i["goal"] == "leads" and i["currency"] == "USD"          # no Meta account named: that card is off
    assert i["metaAccount"] == "" and i["platform"] == "google"


def test_cards_that_need_a_website_or_an_account_are_off_without_one():
    i = B.clean(website="", account="", brief="x" * 50)
    assert i["research"] == {"website": False, "reputation": True, "social": True, "history": False, "metahistory": False, "competitors": True, "market": True}
    assert B.clean(research={"reputation": False, "market": False})["research"]["reputation"] is False
    assert B.clean(research="nonsense")["research"]["website"] is True


@pytest.mark.parametrize("bad, words", [({"name": "A"}, "name"), ({"name": ""}, "name"), ({"website": "", "account": "", "brief": "short"}, "brief"),
                                        ({"website": "not a url"}, "web address"), ({"website": "https://acme.test:8080/"}, "ports"), ({"account": "12345"}, "ten digits"),
                                        ({"goal": "fame"}, "goal"), ({"competitors": {"a": 1}}, "list"), ({"competitors": ["a%d.com" % i for i in range(4)]}, "at most 3")])
def test_bad_details_are_refused_in_words(bad, words):
    with pytest.raises(ps.BadPayload) as e:
        B.clean(**bad)
    assert words in str(e.value)


def test_it_refuses_what_is_not_a_form():
    with pytest.raises(ps.BadPayload):
        pb.clean_inputs(["x"])
    with pytest.raises(ps.BadPayload):
        pb.clean_inputs(None)


def test_a_client_with_only_a_brief_is_enough():
    i = B.clean(website="", account="", brief="They sell dental implants to families in Austin and want more consultations.")
    assert i["website"] == "" and len(i["brief"]) > 40


def test_the_steps_follow_the_cards_that_are_on_then_the_three_writing_stages_then_the_checks():
    s = pb.steps_for(B.clean())
    assert [x["key"] for x in s] == ["r:website", "r:reputation", "r:social", "r:history", "r:competitors", "r:market", "a:core", "a:audience", "a:implications", "f:finish"]
    assert all(x["state"] == "pending" and x["label"] for x in s)
    off = pb.steps_for(B.clean(research={"website": False, "social": False, "history": False, "competitors": False, "market": False}))
    assert [x["key"] for x in off] == ["r:reputation", "a:core", "a:audience", "a:implications", "f:finish"]


def test_a_build_knows_it_is_business_research_and_the_list_row_is_small():
    assert pb.is_business({"inputs": {"kind": "business"}}) and not pb.is_business({"inputs": {}}) and not pb.is_business({"inputs": {"kind": "strategy"}}) and not pb.is_business(None)
    row = pb.brief({"id": "a" * 32, "name": "Acme", "status": "done", "created_by": "x@position2.com", "created_at": "2026-10-17", "account_name": "Acme",
                    "inputs": {"website": "https://acme.test/", "industry": "Dental"}, "stale": 1, "cost": {"usd": 0.41}, "plan": {"core": {"summary": "x" * 10000}}})
    assert row == {"id": "a" * 32, "name": "Acme", "status": "done", "createdBy": "x@position2.com", "createdAt": "2026-10-17", "accountName": "Acme", "website": "https://acme.test/",
                   "industry": "Dental", "stale": True, "cost": 0.41}


def test_a_meta_account_turns_its_card_on_and_picks_the_platform():
    i = B.clean(metaAccount=" act_1030478554590813 ", account="")
    assert i["metaAccount"] == "act_1030478554590813" and i["research"]["metahistory"] is True and i["research"]["history"] is False and i["platform"] == "meta"
    both = B.clean(metaAccount="act_1030478554590813")
    assert both["platform"] == "both" and both["research"]["history"] is True and both["research"]["metahistory"] is True
    assert B.clean(metaAccount="act_1030478554590813", platform="google")["platform"] == "google"
    assert B.clean(metaAccount="act_1030478554590813", research={"metahistory": False})["research"]["metahistory"] is False


def test_a_meta_account_alone_is_enough_to_work_from_and_a_bad_one_or_platform_is_refused():
    assert B.clean(website="", account="", brief="", metaAccount="act_1030478554590813")["metaAccount"] == "act_1030478554590813"
    for bad, words in (({"metaAccount": "1030478554590813"}, "act_"), ({"metaAccount": "act_"}, "act_"), ({"platform": "tiktok"}, "google, meta or both")):
        with pytest.raises(ps.BadPayload) as e:
            B.clean(**bad)
        assert words in str(e.value)


def test_a_card_of_a_platform_that_is_not_in_the_build_is_not_listed_as_missing_but_one_that_is_and_is_off_is():
    from tracker import paid_strategy_ai as ai
    assert ai.missing({}, {"platform": "google", "research": {"metahistory": False, "history": False}}) == ["history: not used for this build"]
    assert ai.missing({}, {"platform": "meta", "research": {"metahistory": False, "history": False}}) == ["metahistory: not used for this build"]
    assert ai.missing({}, {"platform": "both", "research": {"metahistory": False, "history": False}}) == ["metahistory: not used for this build", "history: not used for this build"] or \
        sorted(ai.missing({}, {"platform": "both", "research": {"metahistory": False, "history": False}})) == ["history: not used for this build", "metahistory: not used for this build"]
    assert ai.missing({}, {"research": {"metahistory": False}}) == []                 # an older build with no platform is a Google build
