"""The Alert Hub: the routes and the page (app.py, templates/alert_hub.html). No network."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from monitor_fakes import Deliver, MemStore  # noqa: E402
from tracker import paid_alerthub as hub  # noqa: E402
from tracker import paid_master as m  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/alerthub"
PAGE = "/p2/paid-marketing/alert-hub"
G = "1234567890"


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


@pytest.fixture
def dv(monkeypatch):
    d = Deliver()
    monkeypatch.setattr(app_module, "_mon_deliver", lambda: d)
    return d


def acme(store):
    return m.save_client(store, "", {"name": "Acme Dental", "accounts": [{"id": G}], "shared": {}}, STAFF)["id"]


def issue(agent, kind, level, account=G, status="open"):
    r = mon.make_issue(agent, "%s:%s:%s" % (agent, account, kind), {"id": account, "name": "Acme Dental", "platform": "google"}, kind, level, "%s %s" % (agent, kind), "detail", "Start with: x")
    r.update(status=status, firstSeen="2026-10-20T07:00:00+00:00", lastSeen="2026-10-26T07:00:00+00:00", resolvedAt=None, reopened=0, alertedAt=None, alertedLevel=None, alert={})
    return r


def test_the_page_is_for_signed_in_staff_only():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Alert Hub" in html and 'id="destClients"' in html and 'id="open"' in html and "js/alert_hub_data.js" in html and "js/alert_hub.js" in html
    assert html.index("alert_hub_data.js") < html.index("alert_hub.js")
    for url in (PAGE, API + "/state"):
        assert client_as(None).get(url).status_code == 302 and client_as("someone@gmail.com").get(url).status_code == 302, url
    for url in ("/route", "/test"):
        assert client_as(None).post(API + url, json={}).status_code == 302 and client_as("someone@gmail.com").post(API + url, json={}).status_code == 302, url


def test_the_state_lists_the_clients_the_open_issues_and_what_covers_them(store, dv):
    cid = acme(store)
    store.issues_put([issue("watch", "account_status", "high"), issue("kpi", "cpa", "medium")])
    v = client_as(STAFF).get(API + "/state").get_json()
    assert v["openTotal"] == 2 and v["counts"] == {"P1": 1, "P2": 1, "P3": 0} and v["held"] == 1 and v["hasClients"] is True and v["clients"][0]["id"] == cid
    assert [i["p"] for i in v["open"]] == ["P1", "P2"] and v["open"][1]["coveredBy"]["agentLabel"] == "Account Watch" and v["open"][0]["clientName"] == "Acme Dental"
    assert v["senders"] == {"email": True, "slack": True} and v["default"] == {"emails": [], "slackChannel": ""} and [a["agent"] for a in v["agents"]][0] == "brandspend"


def test_a_clients_destination_is_saved_with_who_and_shown_in_the_state(store, dv):
    cid = acme(store)
    c = client_as(STAFF)
    r = c.post(API + "/route", json={"clientId": cid, "slackChannel": "#acme-paid", "emails": "a@b.co", "floor": "P1"})
    assert r.status_code == 200 and r.get_json()["route"]["slackChannel"] == "#acme-paid" and r.get_json()["route"]["by"] == STAFF
    v = c.get(API + "/state").get_json()
    assert v["clients"][0]["route"]["floor"] == "P1" and v["clients"][0]["route"]["emails"] == ["a@b.co"] and v["changes"][0]["by"] == STAFF
    r = c.post(API + "/route", json={"clientId": cid, "slackChannel": "", "emails": "", "floor": "P2"})
    assert r.status_code == 200 and r.get_json()["route"] is None and c.get(API + "/state").get_json()["clients"][0]["route"] is None


@pytest.mark.parametrize("body,msg", [({"slackChannel": "bad channel!"}, "not a Slack channel"), ({"emails": "nope"}, "not an email address"), ({"floor": "P3"}, "must be P1 or P2"),
                                      ({"clientId": "c-ghost", "slackChannel": "#x"}, "does not exist"), ({"clientId": "", "slackChannel": "#x"}, "does not exist")])
def test_a_destination_that_is_not_valid_is_refused_in_words(store, dv, body, msg):
    cid = acme(store)
    r = client_as(STAFF).post(API + "/route", json=dict({"clientId": cid}, **body))
    assert r.status_code == 400 and msg in r.get_json()["error"] and store.state_get(hub.KEY) is None


def test_a_test_goes_to_the_destination_it_is_for(store, dv):
    cid = acme(store)
    store.state_set("alerts", {"emails": [], "slackChannel": "#all"})
    c = client_as(STAFF)
    c.post(API + "/route", json={"clientId": cid, "slackChannel": "#acme-paid", "floor": "P2"})
    r = c.post(API + "/test", json={"route": cid})
    assert r.status_code == 200 and r.get_json()["sent"] is True and r.get_json()["name"] == "Acme Dental" and dv.slacks[-1]["channel"] == "#acme-paid" and dv.slacks[-1]["text"].startswith("TEST ")
    r = c.post(API + "/test", json={"route": "default"})
    assert r.get_json()["sent"] is True and r.get_json()["name"] == "" and dv.slacks[-1]["channel"] == "#all"
    assert "BASE" not in repr(dv.slacks[-1]["blocks"]) and "/p2/paid-marketing/alert-hub" in repr(dv.slacks[-1]["blocks"])
    assert c.post(API + "/test", json={"route": "c-ghost"}).status_code == 400 and c.post(API + "/test", json={}).status_code == 400


def test_a_test_with_nowhere_to_go_says_so_and_a_refusing_channel_is_reported(store, dv):
    r = client_as(STAFF).post(API + "/test", json={"route": "default"})
    assert r.status_code == 200 and r.get_json()["sent"] is False and r.get_json()["slack"]["status"] == "not_configured" and not dv.slacks
    store.state_set("alerts", {"emails": [], "slackChannel": "#all"})
    dv.fail = {"slack"}
    r = client_as(STAFF).post(API + "/test", json={"route": "default"})
    assert r.get_json()["sent"] is False and r.get_json()["slack"]["status"] == "failed" and "not_in_channel" in r.get_json()["slack"]["error"]


def test_a_store_that_is_down_is_said_in_words(store, dv, monkeypatch):
    class Down(Exception):
        pass
    store.StoreUnavailable = Down

    def boom(key, default=None):
        raise Down("The database is down.")
    monkeypatch.setattr(store, "state_get", boom)
    for r in (client_as(STAFF).get(API + "/state"), client_as(STAFF).post(API + "/route", json={"clientId": "c-x"}), client_as(STAFF).post(API + "/test", json={"route": "default"})):
        assert r.status_code in (502, 503) and "database is down" in r.get_json()["error"]


def test_the_default_destination_is_saved_by_the_address_every_monitor_page_uses(store, dv):
    r = client_as(STAFF).post("/p2/paid-marketing/api/monitors/alerts", json={"emails": "a@b.co", "slackChannel": "#paid-alerts"})
    assert r.status_code == 200 and client_as(STAFF).get(API + "/state").get_json()["default"] == {"emails": ["a@b.co"], "slackChannel": "#paid-alerts"}


def test_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/alert-hub"' in paid and "c-ah" in paid and "Alert Hub" in paid
    assert "u:'/p2/paid-marketing/alert-hub'" in paid and "u:'/p2/paid-marketing/alert-hub'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-ah{" in css and ".ah-held{" in css
    v = {re.search(r"paid_marketing\.css'\) \}\}\?v=(\d+)", open(os.path.join(ROOT, "templates", x), encoding="utf-8").read()).group(1) for x in ("_paid_base.html", "paid_marketing.html")}
    assert len(v) == 1, v
    cards = len(re.findall(r'class="dash-card', paid))
    hub_html = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub_html and ("<span>%d</span> live" % cards) in hub_html and "through one layer with a channel per client" in hub_html
    kn = app_module._VIMI_PLATFORM_KNOWLEDGE
    mine = kn.split("Alert Hub (", 1)[1].split("\n", 1)[0]
    assert "Alert Hub (/p2/paid-marketing/alert-hub)" in kn and "held back" in mine and "P1 (high)" in mine and "—" not in mine and "–" not in mine
    card = paid.split('href="/p2/paid-marketing/alert-hub" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card
    assert "—" not in open(os.path.join(ROOT, "templates", "alert_hub.html"), encoding="utf-8").read()
    entry = [l for l in open(os.path.join(ROOT, "templates", "_paid_kpal.html"), encoding="utf-8").read().splitlines() if "alert-hub" in l]
    assert len(entry) == 1 and "—" not in entry[0] and "–" not in entry[0]
