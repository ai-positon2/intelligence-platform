"""Spend Anomaly Alert: the routes and the page (app.py, templates/spend_anomaly.html). No network."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_anomaly as TA  # noqa: E402
from tracker import paid_anomaly as an  # noqa: E402
from tracker import paid_anomaly_settings as st  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/anomaly"
PAGE = "/p2/paid-marketing/spend-anomaly"


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


def test_the_page_is_for_signed_in_staff_only():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Spend Anomaly Alert" in html and 'id="acctPick"' in html and "js/anomaly_data.js" in html and "js/anomaly.js" in html and 'id="monPanel"' in html and 'id="sens"' in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    for method, url in (("get", "/report?account=1234567890"), ("get", "/settings"), ("post", "/settings/account"), ("post", "/settings/sensitivity")):
        assert getattr(client_as(None), method)(API + url).status_code == 302 and getattr(client_as("someone@gmail.com"), method)(API + url).status_code == 302, url


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch, store):
    seen = []
    monkeypatch.setattr(an, "report_for", lambda *x, **k: seen.append((x, {a: v for a, v in k.items() if a != "mon"}, k["mon"] is store)) or {"accounts": []})
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"accounts": []} and seen[0] == (("1234567890",), {"force": True}, True)
    seen.clear()
    client_as(STAFF).get(API + "/report?account=act_1234567")
    client_as(STAFF).get(API + "/report?account=1234567890&refresh=0")
    assert seen == [(("act_1234567",), {}, True), (("1234567890",), {}, True)]                      # no refresh asked: no force


@pytest.mark.parametrize("err, status, key", [
    (gads.GadsError("not_configured", "not set"), 503, "kind"), (gads.GadsError("quota", "slow down"), 429, "kind"), (gads.GadsError("auth", "revoked"), 502, "kind"),
    (an.BadRequest("That is not one of Position2's accounts."), 400, "error"), (base.AdsError("Meta answered 500."), 502, "error")])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, store, err, status, key):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(an, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == status and str(err) in r.get_json()["error"] and key in r.get_json()


def test_a_refresh_that_is_too_soon_says_when_to_come_back(monkeypatch, store):
    def soon(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 90)
    monkeypatch.setattr(an, "report_for", soon)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 90


def test_a_bad_account_is_refused_by_the_real_report(monkeypatch, store):
    monkeypatch.setattr(gads, "list_accounts", lambda: [TA.ACCT])
    r = client_as(STAFF).get(API + "/report?account=9999999999")
    assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]


def test_the_real_report_is_built_from_the_read_and_what_the_team_saved(monkeypatch, store):
    monkeypatch.setattr(gads, "list_accounts", lambda: [TA.ACCT])
    monkeypatch.setattr(gads, "search", TA.searcher(), raising=True)
    an.clear_cache()
    base.reset_state()
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == 200
    j = r.get_json()
    assert j["account"]["name"] == "Acme Dental" and j["accounts"][0]["id"] == "1234567890" and j["accounts"][0]["curve"] is not None and j["sensitivity"] == "normal" and j["saved"]["teamSettings"] is True
    an.clear_cache()


def test_what_the_team_saved_is_read_and_saved_for_the_whole_team(store):
    c = client_as(STAFF)
    v = c.get(API + "/settings").get_json()
    assert v["accounts"] == {} and v["sensitivity"] is None and v["defaultSensitivity"] == "normal" and v["ratios"]["high"] == [1.4, 2.0] and v["maxMuteDays"] == 45 and v["changes"] == []
    r = c.post(API + "/settings/account", json={"account": "1234567890", "usualDaily": "1,200", "maxDaily": "3000", "minSwing": 40, "mutedUntil": "", "note": "Spring push"})
    assert r.status_code == 200 and r.get_json() == {"account": {"usualDaily": 1200.0, "maxDaily": 3000.0, "minSwing": 40.0, "mutedUntil": None, "note": "Spring push"}}
    assert st.load(store)["accounts"]["1234567890"]["maxDaily"] == 3000.0 and st.load(store)["changes"][0]["by"] == STAFF
    r = c.post(API + "/settings/sensitivity", json={"sensitivity": "high"})
    assert r.get_json() == {"sensitivity": "high"} and c.get(API + "/settings").get_json()["sensitivity"] == "high"
    assert [x["by"] for x in c.get(API + "/settings").get_json()["changes"]] == [STAFF] * 2
    assert c.post(API + "/settings/sensitivity", json={"sensitivity": ""}).get_json() == {"sensitivity": None}


@pytest.mark.parametrize("url, body, fragment", [
    ("/settings/account", {"account": "mcc-9923720096-USD", "maxDaily": 5}, "Pick one account"),
    ("/settings/account", {"account": "1234567890", "maxDaily": "lots"}, "must be a number"),
    ("/settings/account", {"account": "1234567890", "usualDaily": 500, "maxDaily": 400}, "cannot be below"),
    ("/settings/account", {"account": "1234567890", "mutedUntil": "2099-01-01"}, "at most 45 days"),
    ("/settings/sensitivity", {"sensitivity": "wild"}, "low, normal or high")])
def test_a_figure_that_is_not_valid_is_refused_with_the_reason_and_saves_nothing(store, url, body, fragment):
    r = client_as(STAFF).post(API + url, json=body)
    assert r.status_code == 400 and fragment in r.get_json()["error"] and st.load(store) == {"sensitivity": None, "accounts": {}, "changes": []}


def test_an_unreadable_store_is_said_not_crashed(monkeypatch):
    class Down(MemStore):
        StoreUnavailable = type("StoreUnavailable", (Exception,), {})

        def state_get(self, key, default=None):
            raise Down.StoreUnavailable("database down")
    monkeypatch.setattr(app_module, "_mon_store", lambda: Down())
    monkeypatch.setattr(app_module, "_pm_store_error", lambda e: ({"error": "store: %s" % e}, 503))
    r = client_as(STAFF).get(API + "/settings")
    assert r.status_code == 503 and r.get_json() == {"error": "store: database down"}
    r = client_as(STAFF).post(API + "/settings/sensitivity", json={"sensitivity": "low"})
    assert r.status_code == 503


def test_the_monitor_is_known_to_the_alert_panel_and_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    assert pm.AGENTS["spend"]["label"] == "Spend Anomaly Alert" and client_as(STAFF).get("/p2/paid-marketing/api/monitors/spend/state").get_json()["label"] == "Spend Anomaly Alert"
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/spend-anomaly"' in paid and "c-sa" in paid and "Spend Anomaly Alert" in paid
    assert "u:'/p2/paid-marketing/spend-anomaly'" in paid and "u:'/p2/paid-marketing/spend-anomaly'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-sa{" in css and ".pm.pm-sa" in css and ".sa-chart" in css and ".sa-f{" in css
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "spend watched for a spike or a stop every two hours" in hub
    k = app_module._VIMI_PLATFORM_KNOWLEDGE
    assert "Spend Anomaly Alert (/p2/paid-marketing/spend-anomaly)" in k and "mute until a date (at most 45 days ahead" in k and "every two hours" in k and "—" not in k.split("Spend Anomaly Alert (", 1)[1].split("\n", 1)[0]
    card = paid.split('href="/p2/paid-marketing/spend-anomaly" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card
