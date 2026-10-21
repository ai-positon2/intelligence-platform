"""The monitors' routes: the alert panel, the recipients, the test alert, Run now, the scheduled address and what the team saved for the Brand Spend Alert (app.py). No network."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from monitor_fakes import Deliver, MemStore  # noqa: E402
from tracker import paid_brandspend as bs  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api"


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def world(monkeypatch):
    class W:
        store, deliver, spawned, started = MemStore(), Deliver(), [], []
    w = W()
    monkeypatch.setattr(app_module, "_mon_store", lambda: w.store)
    monkeypatch.setattr(app_module, "_mon_deliver", lambda: w.deliver)
    monkeypatch.setattr(app_module, "_mon_inventory", lambda: ([{"id": "1", "name": "Acme", "platform": "google", "group": ""}], [], []))
    monkeypatch.setattr(app_module, "_mon_spawn", lambda fn: w.spawned.append(fn))
    monkeypatch.setattr(pm, "run_agent", lambda agent, store, **k: w.started.append((agent, k["trigger"], k["base_link"], len(k["accounts"]))) or {"status": "done"})
    monkeypatch.setattr(pm, "run_scheduled", lambda store, **k: w.started.append(("scheduled", k["base_link"], k["inventory_fn"]())) or {})
    return w


def test_every_monitor_route_is_for_signed_in_staff_only():
    for method, url in (("get", "/monitors/brandspend/state"), ("post", "/monitors/alerts"), ("post", "/monitors/brandspend/test-alert"), ("post", "/monitors/brandspend/run"),
                        ("get", "/brandspend/settings"), ("post", "/brandspend/settings/account"), ("post", "/brandspend/settings/mark"), ("post", "/brandspend/settings/limit")):
        assert getattr(client_as(None), method)(API + url).status_code == 302 and getattr(client_as("someone@gmail.com"), method)(API + url).status_code == 302, url


def test_the_alert_panel_is_the_monitors_state(world):
    world.store.state_set("alerts", {"emails": ["a@b.co"], "slackChannel": "#x"})
    r = client_as(STAFF).get(API + "/monitors/disapproved/state")
    j = r.get_json()
    assert r.status_code == 200 and j["label"] == "Disapproved Ads Tracker" and j["alerts"] == {"emails": ["a@b.co"], "slackChannel": "#x"} and j["senders"] == {"email": True, "slack": True} and j["openTotal"] == 0
    bad = client_as(STAFF).get(API + "/monitors/other/state")
    assert bad.status_code == 400 and "must be one of" in bad.get_json()["error"]


def test_the_recipients_are_saved_and_a_bad_address_is_refused_with_the_reason(world):
    r = client_as(STAFF).post(API + "/monitors/alerts", json={"emails": "A@x.co, b@y.co", "slackChannel": "#ppc"})
    assert r.status_code == 200 and r.get_json() == {"alerts": {"emails": ["a@x.co", "b@y.co"], "slackChannel": "#ppc"}} and world.store.state_get("alerts")["slackChannel"] == "#ppc"
    bad = client_as(STAFF).post(API + "/monitors/alerts", json={"emails": "nope"})
    assert bad.status_code == 400 and bad.get_json()["error"] == "nope is not an email address."
    assert client_as(STAFF).post(API + "/monitors/alerts", json={}).get_json() == {"alerts": {"emails": [], "slackChannel": ""}}


def test_a_test_alert_goes_through_the_senders_and_names_the_monitor(world):
    world.store.state_set("alerts", {"emails": ["a@b.co"], "slackChannel": "#x"})
    r = client_as(STAFF).post(API + "/monitors/brandspend/test-alert", json={})
    assert r.status_code == 200 and r.get_json()["sent"] is True and world.deliver.emails[0]["subject"] == "TEST: HIGH: Brand Spend Alert: 1 new (1 account)" and "/p2/paid-marketing/brand-spend" in world.deliver.emails[0]["text"]
    assert client_as(STAFF).post(API + "/monitors/other/test-alert", json={}).status_code == 400


def test_run_now_starts_the_monitor_in_the_background_with_who_asked_and_refuses_a_second_run(world):
    r = client_as(STAFF).post(API + "/monitors/brandspend/run", json={})
    assert r.status_code == 202 and r.get_json() == {"started": True} and len(world.spawned) == 1
    world.spawned[0]()
    assert world.started == [("brandspend", "page by ana@position2.com", "https://localhost", 1)]
    world.store.state_set("lock:brandspend", {"locked": True})
    busy = client_as(STAFF).post(API + "/monitors/brandspend/run", json={})
    assert busy.status_code == 409 and busy.get_json()["error"] == "A run is already going." and len(world.spawned) == 1
    assert client_as(STAFF).post(API + "/monitors/other/run", json={}).status_code == 400


def test_the_scheduled_address_needs_the_secret_and_no_session(world, monkeypatch):
    c = client_as(None)
    monkeypatch.delenv("MONITOR_ALERT_SECRET", raising=False)
    assert c.post(API + "/monitors/scheduled", json={}).status_code == 403 and c.post(API + "/monitors/scheduled", json={}, headers={"X-Monitor-Secret": ""}).status_code == 403          # unset: every call is refused
    monkeypatch.setenv("MONITOR_ALERT_SECRET", "s3cret")
    assert c.post(API + "/monitors/scheduled", json={}).status_code == 403 and c.post(API + "/monitors/scheduled", json={}, headers={"X-Monitor-Secret": "wrong"}).status_code == 403 and not world.spawned
    ok = c.post(API + "/monitors/scheduled", json={}, headers={"X-Monitor-Secret": "s3cret"})
    assert ok.status_code == 202 and ok.get_json() == {"started": True} and len(world.spawned) == 1
    world.spawned[0]()
    assert world.started[0][0] == "scheduled" and world.started[0][1] == "https://localhost" and world.started[0][2][0][0]["name"] == "Acme"


def test_a_store_that_is_down_is_said_as_a_503(monkeypatch):
    class Down(MemStore):
        def state_get(self, key, default=None):
            raise self.StoreUnavailable("DATABASE_URL is not set")
    down = Down()
    monkeypatch.setattr(app_module, "_mon_store", lambda: down)
    monkeypatch.setattr(app_module, "_mon_deliver", lambda: Deliver())
    r = client_as(STAFF).get(API + "/monitors/brandspend/state")
    assert r.status_code == 503 and "store is unavailable" in r.get_json()["error"] and client_as(STAFF).get(API + "/brandspend/settings").status_code == 503


# ── What the team saved for the Brand Spend Alert ───────────────────────────

def test_the_saved_settings_are_read_and_an_accounts_limits_marks_and_limit_are_saved_by_the_person_who_signed_in(world):
    c = client_as(STAFF)
    assert c.get(API + "/brandspend/settings").get_json() == {"limit": None, "defaultLimit": 0.3, "marks": {}, "accounts": {}, "changes": []}
    a = c.post(API + "/brandspend/settings/account", json={"account": "1234567890", "weeklyMax": "500", "monthlyBudget": 2000, "brandTerms": "Acme, acme dental"})
    assert a.status_code == 200 and a.get_json() == {"account": {"weeklyMin": None, "weeklyMax": 500.0, "monthlyBudget": 2000.0, "brandTerms": ["acme", "acme dental"]}}
    assert c.post(API + "/brandspend/settings/mark", json={"campaign": "24", "value": True}).get_json() == {"marks": {"24": True}}
    assert c.post(API + "/brandspend/settings/mark", json={"campaign": "22", "value": False}).get_json() == {"marks": {"24": True, "22": False}}
    assert c.post(API + "/brandspend/settings/mark", json={"campaign": "24", "value": None}).get_json() == {"marks": {"22": False}}
    assert c.post(API + "/brandspend/settings/limit", json={"limit": "25"}).get_json() == {"limit": 0.25}
    v = c.get(API + "/brandspend/settings").get_json()
    assert v["limit"] == 0.25 and v["marks"] == {"22": False} and v["accounts"]["1234567890"]["weeklyMax"] == 500.0
    assert {ch["by"] for ch in v["changes"]} == {STAFF} and len(v["changes"]) == 5


def test_a_saved_setting_that_makes_no_sense_is_refused_with_the_reason(world):
    c = client_as(STAFF)
    assert c.post(API + "/brandspend/settings/account", json={"account": "mcc-1", "weeklyMax": 1}).get_json()["error"].startswith("Pick one account")
    assert c.post(API + "/brandspend/settings/account", json={"account": "1234567890", "weeklyMax": "x"}).status_code == 400
    assert c.post(API + "/brandspend/settings/mark", json={"campaign": "abc", "value": True}).get_json()["error"] == "That is not a campaign id."
    r = c.post(API + "/brandspend/settings/limit", json={"limit": "5"})
    assert r.status_code == 400 and "between 10% and 90%" in r.get_json()["error"]
    assert world.store.state_get("settings:brandspend") is None


def test_the_report_routes_hand_the_monitors_store_to_the_report(world, monkeypatch):
    seen = []
    monkeypatch.setattr(bs, "report_for", lambda *x, **k: seen.append(k.get("mon")) or {"accounts": []})
    client_as(STAFF).get(API + "/brandspend/report?account=1234567890")
    from tracker import paid_disapproved as d
    monkeypatch.setattr(d, "report_for", lambda *x, **k: seen.append(k.get("mon")) or {"accounts": []})
    client_as(STAFF).get(API + "/disapproved/report?account=1234567890")
    assert seen == [world.store, world.store]


def test_the_daily_workflow_calls_the_scheduled_address_with_the_secret_and_the_secret_is_checked_by_the_route():
    text = open(os.path.join(ROOT, ".github", "workflows", "paid-monitors.yml"), encoding="utf-8").read()
    assert "/p2/paid-marketing/api/monitors/scheduled" in text and "X-Monitor-Secret: $SECRET" in text and "secrets.MONITOR_ALERT_SECRET" in text and "cron: '41 5 * * *'" in text
    assert "workflow_dispatch" in text and "cancel-in-progress: false" in text and "PLATFORM_BASE_URL" in text and "—" not in text and "–" not in text
    code = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert 'os.environ.get("MONITOR_ALERT_SECRET", "")' in code and 'request.headers.get("X-Monitor-Secret", "")' in code
