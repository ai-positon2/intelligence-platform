"""The QA Agent's routes for a Meta ad account: the campaign list, starting a QC, the review running on Meta's answers, a person's answer, and the Meta checklist."""
import pytest

from tests.test_paid_qa import API, SAME, STAFF, appmod, client_as, mem  # noqa: F401  (the fixture is the module's own)
from tests.test_paid_qa_meta import ACT, CAMP, world
from tracker import paid_meta, paid_qa


@pytest.fixture
def meta(mem, monkeypatch):
    get, pages = world()
    monkeypatch.setattr(paid_meta, "_get", lambda path, params, store, **kw: get(path, params))
    monkeypatch.setattr(paid_meta, "_pages", lambda path, params, store, **kw: pages(path, params))
    return mem


NEW = {"accountId": ACT, "accountName": "Acme Meta", "campaignId": CAMP["id"], "campaignName": CAMP["name"], "channel": "META_LEADS"}


def test_the_campaigns_of_a_meta_account_are_listed_with_their_qc_when_there_is_one(meta):
    c = client_as(STAFF)
    r = c.get(API + "/campaigns?account=" + ACT)
    assert r.status_code == 200 and [(x["name"], x["type"], x["qc"]) for x in r.get_json()["campaigns"]] == [("alpha", "", None), ("Beta", "Meta Sales", None)]
    assert c.get(API + "/campaigns?account=act_12").status_code == 400 and c.get(API + "/campaigns?account=nonsense").status_code == 400


def test_a_meta_account_that_cannot_be_read_is_a_502_that_says_it_is_meta(meta, monkeypatch):
    def boom(*a, **k):
        raise paid_meta.AdsError("Meta rejected the access token")
    monkeypatch.setattr(paid_meta, "_pages", boom)
    r = client_as(STAFF).get(API + "/campaigns?account=" + ACT)
    assert r.status_code == 502 and r.get_json()["kind"] == "meta" and "access token" in r.get_json()["error"]


def test_starting_a_qc_on_a_meta_campaign_reviews_it_from_meta_and_returns_the_meta_checks(meta):
    c = client_as(STAFF)
    r = c.post(API + "/start", json=NEW, headers=SAME)
    assert r.status_code == 201
    j = r.get_json()
    lid = j["launch"]["id"]
    assert j["launch"]["accountId"] == ACT and j["launch"]["channel"] == "META_LEADS" and j["launch"]["campaignTypes"] == ["Meta Leads"] and j["launch"]["bookApplies"] is True
    assert j["launch"]["review"]["status"] == "done" and meta.launches[lid]["auto"]["1.1"]["result"] == "pass"
    assert set(j["checks"]) <= {i["id"] for i in paid_qa.applicable([], ["Meta Leads"], "meta")} and j["checks"]["1.1"]["status"] == "pass" and j["checks"]["1.1"]["by"] == "QA Agent"
    assert j["summary"]["total"] == 37 and j["summary"]["verdict"]["code"] == "incomplete"


def test_a_person_answers_a_meta_check_and_a_google_check_id_is_refused(meta):
    c = client_as(STAFF)
    lid = c.post(API + "/start", json=NEW, headers=SAME).get_json()["launch"]["id"]
    ok = c.put("%s/launches/%s/checks/4.1" % (API, lid), json={"status": "pass", "note": "Events Manager shows Lead events"}, headers=SAME)
    assert ok.status_code == 200 and ok.get_json()["check"]["status"] == "pass" and ok.get_json()["check"]["source"] == "person" and ok.get_json()["summary"]["total"] == 37
    bad = c.put("%s/launches/%s/checks/1.10" % (API, lid), json={"status": "pass"}, headers=SAME)
    assert bad.status_code == 400 and "not a check on this checklist" in bad.get_json()["error"]


def test_the_review_runs_again_on_a_meta_qc_and_the_checklist_route_serves_either_list(meta):
    c = client_as(STAFF)
    lid = c.post(API + "/start", json=NEW, headers=SAME).get_json()["launch"]["id"]
    appmod._QC_LAST_RUN.clear()
    again = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    assert again.status_code == 200 and again.get_json()["launch"]["review"]["status"] == "done"
    meta_list = c.get(API + "/checklist?platform=meta").get_json()
    google_list = c.get(API + "/checklist").get_json()
    assert meta_list["starter"] is True and len(meta_list["items"]) == 37 and len(google_list["items"]) == 65 and "starter" not in google_list


def test_a_google_qc_is_unchanged(meta):
    from tests.test_paid_qa import NEW as GOOGLE_NEW
    c = client_as(STAFF)
    j = c.post(API + "/start", json=GOOGLE_NEW, headers=SAME).get_json()
    assert j["launch"]["campaignTypes"] == ["Search"] and j["summary"]["total"] == len(paid_qa.applicable([], ["Search"]))
