"""Tracking Data Health: the routes, the page and its registration (app.py, templates/tracking_health.html, the hub). No network."""
import json

import pytest

import app as app_module
from tests.monitor_fakes import MemStore
from tracker import paid_gads_segments as gads
from tracker import paid_tracking as tr
from tracker import paid_tracking_settings as st

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/tracking"
PAGE = "/p2/paid-marketing/tracking-health"
REPORT = {"account": {"id": "1234567890", "name": "Acme <Dental>", "platform": "google"}, "generatedAt": "2026-10-26T09:00:00+00:00", "accounts": [], "warnings": []}


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


def test_the_page_and_every_api_are_for_signed_in_staff_only():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Tracking Data Health" in html and 'id="acctPick"' in html and "js/tracking_data.js" in html and "js/tracking.js" in html and 'id="monPanel"' in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    for method, url in (("get", "/report?account=1234567890"), ("get", "/export?account=1234567890"), ("get", "/settings"), ("post", "/settings/owner"), ("post", "/settings/ack")):
        assert getattr(client_as(None), method)(API + url).status_code == 302 and getattr(client_as("someone@gmail.com"), method)(API + url).status_code == 302, url


def test_the_report_route_passes_the_account_the_refresh_and_the_monitor_store(monkeypatch, store):
    seen = []
    monkeypatch.setattr(tr, "report_for", lambda *x, **kw: seen.append((x, {a: v for a, v in kw.items() if a != "mon"}, kw["mon"] is store)) or dict(REPORT))
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json()["account"]["id"] == "1234567890" and seen == [(("1234567890",), {"force": True}, True)]


def test_errors_become_answers_the_page_can_say(monkeypatch, store):
    def bad(*a, **k):
        raise tr.BadRequest("Pick an account.")
    monkeypatch.setattr(tr, "report_for", bad)
    assert client_as(STAFF).get(API + "/report?account=x").status_code == 400

    def quota(*a, **k):
        raise gads.GadsError("quota", "Google Ads says to slow down.")
    monkeypatch.setattr(tr, "report_for", quota)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code >= 400 and r.get_json()["kind"] == "quota"


def test_the_download_is_the_whole_report_as_a_json_file_named_for_the_account(monkeypatch, store):
    monkeypatch.setattr(tr, "report_for", lambda *x, **kw: dict(REPORT))
    r = client_as(STAFF).get(API + "/export?account=1234567890")
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("application/json")
    assert r.headers["Content-Disposition"] == 'attachment; filename="tracking-health-Acme-Dental-2026-10-26.json"'
    body = json.loads(r.get_data(as_text=True))
    assert body["account"]["name"] == "Acme <Dental>" and body["exportedBy"] == STAFF and body["exportedAt"]


def test_settings_are_saved_with_who_saved_them_and_refused_with_why(store):
    c = client_as(STAFF)
    r = c.post(API + "/settings/owner", json={"account": "1234567890", "name": "Acme", "owner": "Ana"})
    assert r.status_code == 200 and r.get_json()["account"]["owner"] == "Ana" and st.view(store)["changes"][0]["by"] == STAFF
    r = c.post(API + "/settings/window", json={"account": "1234567890", "from": "2026-11-01", "to": "2026-11-02", "why": "migration"})
    assert r.get_json()["account"]["windows"][0]["why"] == "migration"
    assert c.post(API + "/settings/ack", json={"account": "1234567890", "key": "conv_stopped", "note": "on it"}).get_json()["account"]["acks"]["conv_stopped"]["by"] == STAFF
    assert c.post(API + "/settings/mute", json={"account": "1234567890", "key": "list:9", "itemName": "Visitors", "why": "retired"}).get_json()["account"]["muted"] == {"list:9": "retired"}
    for action, body in (("unack", {"key": "conv_stopped"}), ("unmute", {"key": "list:9"}), ("window-remove", {"from": "2026-11-01", "to": "2026-11-02"})):
        assert c.post(API + "/settings/" + action, json=dict(body, account="1234567890")).status_code == 200, action
    bad = c.post(API + "/settings/window", json={"account": "1234567890", "from": "2026-11-02", "to": "2026-11-01", "why": "x"})
    assert bad.status_code == 400 and "before the first" in bad.get_json()["error"]
    assert c.post(API + "/settings/nonsense", json={}).status_code == 404
    assert c.get(API + "/settings").get_json()["accounts"]["1234567890"]["owner"] == "Ana"


def test_a_store_that_is_down_is_said_so(monkeypatch, store):
    def down(*a, **k):
        raise store.StoreUnavailable("DATABASE_URL is not set.")
    monkeypatch.setattr(store, "state_get", down)
    assert client_as(STAFF).get(API + "/settings").status_code == 503


def test_it_is_a_monitor_with_an_alert_panel(store):
    r = client_as(STAFF).get("/p2/paid-marketing/api/monitors/tracking/state")
    assert r.status_code == 200 and r.get_json()["label"] == "Tracking Data Health" and r.get_json()["critical"] == 0


def test_it_is_on_the_paid_marketing_page_in_both_search_lists_and_in_the_assistant_text():
    page = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/tracking-health" class="dash-card active c-th"' in page and "u:'/p2/paid-marketing/tracking-health'" in page
    assert "u:'/p2/paid-marketing/tracking-health'" in open(app_module.os.path.join(app_module.os.path.dirname(app_module.__file__), "templates", "_paid_kpal.html"), encoding="utf-8").read()
    assert "Tracking Data Health (/p2/paid-marketing/tracking-health):" in app_module._VIMI_PLATFORM_KNOWLEDGE and app_module.PAID_AGENT_TOTAL == 23
