"""The Master Agent: the routes and the page (app.py, templates/master_agent.html). No network."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_kpi_settings as ks  # noqa: E402
from tracker import paid_master as m  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/master"
PAGE = "/p2/paid-marketing/master-agent"
G, MA = "1234567890", "act_1234567"


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def store(monkeypatch):
    s = MemStore()
    s.StoreUnavailable = type("StoreUnavailable", (Exception,), {})
    monkeypatch.setattr(app_module, "_mon_store", lambda: s)
    return s


def body(**kw):
    d = {"name": "Acme Dental", "accounts": [{"id": G, "budget": {"monthly": "12000"}, "targets": {"cpa": "85"}}, {"id": MA}], "shared": {"phones": "617-555-0100", "domains": "acmedental.com"}}
    d.update(kw)
    return d


def test_the_page_is_for_signed_in_staff_only():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Master Agent" in html and 'id="editor"' in html and 'id="saveBtn"' in html and "js/master_agent_data.js" in html and "js/master_agent.js" in html
    assert html.index("master_agent_data.js") < html.index("master_agent.js")
    for url in (PAGE, API + "/state"):
        assert client_as(None).get(url).status_code == 302 and client_as("someone@gmail.com").get(url).status_code == 302, url
    for url in ("/client", "/client/delete"):
        assert client_as(None).post(API + url, json={}).status_code == 302 and client_as("someone@gmail.com").post(API + url, json={}).status_code == 302, url


def test_a_client_is_saved_listed_and_removed_with_who_did_it(store):
    c = client_as(STAFF)
    r = c.post(API + "/client", json={"client": body()})
    assert r.status_code == 200 and r.get_json()["client"]["id"] == "c-acme-dental" and r.get_json()["client"]["accounts"][G]["budget"] == {"monthly": 12000.0}
    state = c.get(API + "/state").get_json()
    assert [x["name"] for x in state["clients"]] == ["Acme Dental"] and state["changes"][0]["by"] == STAFF and state["drift"] == [] and state["usedBy"]["domains"] == ["Unknown Domains"]
    edited = c.post(API + "/client", json={"id": "c-acme-dental", "client": body(name="Acme Dental Group")})
    assert edited.status_code == 200 and edited.get_json()["client"]["id"] == "c-acme-dental"
    gone = c.post(API + "/client/delete", json={"id": "c-acme-dental"})
    assert gone.status_code == 200 and gone.get_json() == {"removed": {"id": "c-acme-dental", "name": "Acme Dental Group"}} and c.get(API + "/state").get_json()["clients"] == []


def test_what_is_wrong_is_a_400_in_words_and_nothing_is_saved(store):
    c = client_as(STAFF)
    for bad, text in ((body(name=""), "Give the client a name"), (body(shared={"phones": "123"}), "not a ten digit US phone number"), (body(accounts=[{"id": "x"}]), "not a Google Ads customer id")):
        r = c.post(API + "/client", json={"client": bad})
        assert r.status_code == 400 and text in r.get_json()["error"]
    assert store.state_get(m.KEY) is None
    assert c.post(API + "/client", json={"id": "c-nope", "client": body()}).status_code == 400
    assert c.post(API + "/client/delete", json={"id": "c-nope"}).status_code == 400


def test_an_account_already_in_another_client_is_refused_by_name(store):
    c = client_as(STAFF)
    c.post(API + "/client", json={"client": body()})
    r = c.post(API + "/client", json={"client": {"name": "Other", "accounts": [{"id": G}]}, "names": {G: "Acme Dental (Google)"}})
    assert r.status_code == 400 and r.get_json()["error"] == "Acme Dental (Google) is already in the client Acme Dental."


def test_the_state_lists_where_an_agents_own_value_differs(store):
    c = client_as(STAFF)
    c.post(API + "/client", json={"client": body()})
    ks.save_account(store, G, "Acme", {"targets": {"cpa": 70}}, "a@b.co")
    d = c.get(API + "/state").get_json()["drift"]
    assert d == [{"account": G, "client": "Acme Dental", "agent": "KPI Monitoring", "field": "targets.cpa", "master": 85.0, "saved": 70.0}]


def test_a_store_that_is_down_is_said_not_crashed(monkeypatch, store):
    class Down(Exception):
        pass
    store.StoreUnavailable = Down

    def boom(key):
        raise Down("The database is down.")
    monkeypatch.setattr(store, "state_get", boom)
    r = client_as(STAFF).get(API + "/state")
    assert r.status_code in (502, 503) and "database is down" in r.get_json()["error"]
    r = client_as(STAFF).post(API + "/client", json={"client": body()})
    assert r.status_code in (502, 503)


def test_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/master-agent"' in paid and "c-ma" in paid and "Master Agent" in paid
    assert "u:'/p2/paid-marketing/master-agent'" in paid and "u:'/p2/paid-marketing/master-agent'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-ma{" in css and ".ma-acct{" in css
    v = {re.search(r"paid_marketing\.css'\) \}\}\?v=(\d+)", open(os.path.join(ROOT, "templates", x), encoding="utf-8").read()).group(1) for x in ("_paid_base.html", "paid_marketing.html")}
    assert len(v) == 1, v
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "written down once for every agent" in hub
    kn = app_module._VIMI_PLATFORM_KNOWLEDGE
    mine = kn.split("Master Agent (", 1)[1].split("\n", 1)[0]
    assert "Master Agent (/p2/paid-marketing/master-agent)" in kn and "still wins" in mine and "—" not in mine and "–" not in mine
    card = paid.split('href="/p2/paid-marketing/master-agent" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card
