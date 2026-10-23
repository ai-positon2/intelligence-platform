"""The Meta launch checklist (tracker/qa_checklist_meta.json) and the QA module serving two checklists: a launch on a Meta ad account is checked against Meta's, anything else against the
Campaign Book's, and the verdict rule is the same for both."""
import json

import pytest

from tracker import paid_qa as qa

META = {"id": "m1", "account_id": "act_1234567", "client_types": [], "campaign_types": ["Meta Leads"]}
GOOGLE = {"id": "g1", "account_id": "1234567890", "client_types": [], "campaign_types": ["Search"]}
KEYS = {"id", "section", "check", "expected", "otherThingsToCheck", "verify", "fromBook", "campaignTypes", "clientTypes", "severity"}


def test_the_platform_of_a_launch_is_its_accounts():
    assert qa.platform_of(META) == "meta" and qa.platform_of(GOOGLE) == "google" and qa.platform_of("act_9") == "meta" and qa.platform_of("1234567890") == "google" and qa.platform_of(None) == "google"


def test_the_meta_checklist_is_a_starter_with_the_same_shape_as_the_campaign_book_checks():
    cl = qa.checklist("meta")
    assert cl["starter"] is True and "not written down yet" in cl["source"] and cl["campaignTypes"] == ["Meta Leads", "Meta Sales", "Meta Traffic", "Meta Awareness"]
    ids = [i["id"] for i in cl["items"]]
    assert len(ids) == len(set(ids)) == 37
    for it in cl["items"]:
        assert KEYS <= set(it), it["id"]
        assert it["section"] in cl["sections"] and it["severity"] in cl["severities"] and set(it["campaignTypes"]) <= set(cl["campaignTypes"]) | {"All"}, it["id"]
        assert it["verify"].startswith("Expected: ") and it["expected"] and it["check"], it["id"]
        if it["fromBook"]:
            assert it.get("onTheSpot"), "%s is read from the campaign book and says how to judge it without one" % it["id"]
    assert not any("—" in json.dumps(i, ensure_ascii=False) or "–" in json.dumps(i, ensure_ascii=False) for i in cl["items"])


def test_the_google_checklist_is_unchanged_and_is_the_default():
    assert len(qa.items()) == len(qa.items("google")) == 65 and qa.item("1.5")["check"] == "AI Max" and qa.item("1.5", "meta")["check"] == "Budget Type and Amount"
    assert qa.checklist() is qa.checklist("google") is qa.checklist("tiktok") and qa.checklist("meta") is not qa.checklist() and qa.item("1.5", "tiktok")["check"] == "AI Max"      # an unknown platform is Google
    assert [i["id"] for i in qa.applicable([], ["Search"])] == [i["id"] for i in qa.applicable([], ["Search"], "google")]


def test_which_meta_checks_show_depends_on_the_objective():
    leads = {i["id"] for i in qa.applicable([], ["Meta Leads"], "meta")}
    awareness = {i["id"] for i in qa.applicable([], ["Meta Awareness"], "meta")}
    traffic = {i["id"] for i in qa.applicable([], ["Meta Traffic"], "meta")}
    assert {"2.1", "2.2", "2.3", "2.4", "4.1", "4.2", "4.3", "4.4"} <= leads and not ({"2.1", "2.2", "2.3", "2.4", "4.1"} & awareness)
    assert {"2.1", "2.2"} <= traffic and not ({"2.3", "2.4", "4.1"} & traffic)                       # traffic has no conversion event and no attribution window to check
    assert {"1.1", "1.2", "3.2", "5.1"} <= awareness and awareness < leads
    assert qa.applicable([], [], "meta") and len(qa.applicable([], [], "meta")) == 37                # a type not known yet narrows nothing


def test_the_verdict_uses_the_checks_of_the_launchs_own_platform():
    s = qa.summarize(META, {})
    assert s["total"] == len(qa.applicable([], ["Meta Leads"], "meta")) and s["verdict"]["code"] == "incomplete" and set(s["applicable"]) == {i["id"] for i in qa.applicable([], ["Meta Leads"], "meta")}
    assert qa.summarize(GOOGLE, {})["total"] == len(qa.applicable([], ["Search"]))
    failed = qa.summarize(META, {"1.1": {"status": "fail"}})
    assert failed["verdict"]["code"] == "not_ready" and failed["failedBlocking"] == ["1.1"]
    done = qa.summarize(META, {i["id"]: {"status": "pass"} for i in qa.applicable([], ["Meta Leads"], "meta")})
    assert done["verdict"]["code"] == "ready" and done["percent"] == 100


def test_a_persons_answer_is_checked_against_the_launchs_own_checklist():
    assert qa.clean_check(META, "3.9", {"status": "pass"}) == ("pass", "")
    with pytest.raises(qa.BadPayload):
        qa.clean_check(META, "1.10", {"status": "pass"})                                           # a Campaign Book check that is not a Meta one
    assert qa.clean_check(GOOGLE, "1.10", {"status": "pass"}) == ("pass", "")
    with pytest.raises(qa.BadPayload):
        qa.clean_check(dict(META, campaign_types=["Meta Awareness"]), "2.1", {"status": "pass"})   # the conversion location does not apply to an Awareness campaign


def test_what_each_check_shows_follows_the_agents_reading_and_a_persons_answer_wins():
    got = qa.effective(META, {"1.1": {"status": "fail", "note": "wrong name", "by": "a@b.c", "at": "t"}}, {"1.1": {"result": "pass", "found": "P2_MetaLeads_X_20261101"}, "3.9": {"result": "pass", "found": "all active"}}, "now")
    assert got["1.1"]["status"] == "fail" and got["1.1"]["source"] == "person" and got["1.1"]["found"] == "P2_MetaLeads_X_20261101" and got["3.9"]["status"] == "pass" and got["3.9"]["by"] == "QA Agent"
    assert "1.25" not in qa.effective(META, {}, {"1.25": {"result": "pass", "found": "x"}}, "now")      # a Campaign Book check id (the final URL) that is not on the Meta list never shows
    assert "1.25" in qa.effective(GOOGLE, {}, {"1.25": {"result": "pass", "found": "x"}}, "now")
