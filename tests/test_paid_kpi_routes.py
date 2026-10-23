"""KPI Monitoring: the routes and the page (app.py, templates/kpi_monitoring.html). No network."""
import datetime as dt
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_kpi as TK  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_kpi as k  # noqa: E402
from tracker import paid_kpi_settings as st  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/kpi"
PAGE = "/p2/paid-marketing/kpi-monitoring"


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
    assert "KPI Monitoring" in html and 'id="acctPick"' in html and "js/kpi_data.js" in html and "js/kpi.js" in html and 'id="monPanel"' in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    for method, url in (("get", "/report?account=1234567890"), ("get", "/settings"), ("post", "/settings/account"), ("post", "/settings/campaign"), ("post", "/settings/mute"), ("post", "/settings/unmute")):
        assert getattr(client_as(None), method)(API + url).status_code == 302 and getattr(client_as("someone@gmail.com"), method)(API + url).status_code == 302, url


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch, store):
    seen = []
    monkeypatch.setattr(k, "report_for", lambda *x, **kw: seen.append((x, {a: v for a, v in kw.items() if a != "mon"}, kw["mon"] is store)) or {"accounts": []})
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"accounts": []} and seen[0] == (("1234567890",), {"force": True}, True)
    seen.clear()
    client_as(STAFF).get(API + "/report?account=act_1234567")
    client_as(STAFF).get(API + "/report?account=1234567890&refresh=0")
    assert seen == [(("act_1234567",), {}, True), (("1234567890",), {}, True)]                      # no refresh asked: no force


@pytest.mark.parametrize("err, status, key", [
    (gads.GadsError("not_configured", "not set"), 503, "kind"), (gads.GadsError("quota", "slow down"), 429, "kind"), (gads.GadsError("auth", "revoked"), 502, "kind"),
    (k.BadRequest("That is not one of Position2's accounts."), 400, "error")])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, store, err, status, key):
    def boom(*x, **kw):
        raise err
    monkeypatch.setattr(k, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == status and str(err) in r.get_json()["error"] and key in r.get_json()


def test_a_refresh_that_is_too_soon_says_when_to_come_back(monkeypatch, store):
    def soon(*x, **kw):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 90)
    monkeypatch.setattr(k, "report_for", soon)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 90


def test_a_bad_account_is_refused_by_the_real_report(monkeypatch, store):
    monkeypatch.setattr(gads, "list_accounts", lambda: [TK.ACCT])
    r = client_as(STAFF).get(API + "/report?account=9999999999")
    assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]


def real_days():
    """Fourteen days of 100 spent and one conversion each, counted back from the real UTC date (the route reads the clock)."""
    today = dt.datetime.now(dt.timezone.utc).date()
    return [{"customer": {"timeZone": "UTC"}, "campaign": {"id": "5"}, "segments": {"date": (today - dt.timedelta(days=i)).isoformat()},
             "metrics": {"impressions": "2000", "clicks": "200", "costMicros": "100000000", "conversions": 1.0, "conversionsValue": 0.0}} for i in range(1, 15)]


def test_the_real_report_is_built_from_the_read_and_what_the_team_saved(monkeypatch, store):
    from tracker import paid_geo
    monkeypatch.setattr(gads, "list_accounts", lambda: [TK.ACCT])
    monkeypatch.setattr(gads, "search", TK.searcher(camps={TK.ACCT["id"]: [TK.camp_row()]}, days={TK.ACCT["id"]: real_days()}), raising=True)
    paid_geo.clear_cache()
    base.reset_state()
    st.save_account(store, TK.ACCT["id"], "Acme Dental", {"targets": {"cpa": 40}}, STAFF)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == 200
    j = r.get_json()
    a = j["accounts"][0]
    assert j["account"]["name"] == "Acme Dental" and a["id"] == "1234567890" and a["saved"]["targets"] == 1 and a["counts"] == {"high": 1, "medium": 0, "low": 0}
    assert a["items"][0]["findings"][0]["rule"] == "cpa" and a["items"][0]["metrics"][0]["target"] == 40.0
    st.mute(store, TK.ACCT["id"], "5", "Search Core", "a test", STAFF)                              # muting applies to the saved read: no second read
    j2 = client_as(STAFF).get(API + "/report?account=1234567890").get_json()
    assert j2["accounts"][0]["status"] == "clean" and j2["accounts"][0]["muted"] == 1
    paid_geo.clear_cache()


def test_what_the_team_saved_is_read_and_saved_for_the_whole_team(store):
    c = client_as(STAFF)
    assert c.get(API + "/settings").get_json()["accounts"] == {}
    r = c.post(API + "/settings/account", json={"account": "1234567890", "name": "Acme", "targets": {"cpa": "45", "roas": "", "cvr": "3", "ctr": ""}, "tolerance": "25"})
    got = r.get_json()["account"]
    assert r.status_code == 200 and got["targets"] == {"cpa": 45.0, "roas": None, "cvr": 3.0, "ctr": None} and got["tolerance"] == 25.0 and got["name"] == "Acme"
    assert st.load(store)["changes"][0]["by"] == STAFF
    r = c.post(API + "/settings/campaign", json={"account": "1234567890", "campaign": "5", "name": "Brand", "cpa": "20", "roas": "", "cvr": "", "ctr": ""})
    assert r.get_json()["account"]["campaigns"] == {"5": {"cpa": 20.0, "roas": None, "cvr": None, "ctr": None}} and r.get_json()["account"]["targets"]["cpa"] == 45.0
    r = c.post(API + "/settings/mute", json={"account": "1234567890", "campaign": "6", "name": "Test", "why": "a test campaign"})
    assert r.get_json()["account"]["muted"] == {"6": "a test campaign"}
    shown = c.get(API + "/settings").get_json()
    assert shown["accounts"]["1234567890"]["muted"] == {"6": "a test campaign"} and shown["accounts"]["1234567890"]["campaigns"]["5"]["cpa"] == 20.0                # the same shape as a save answers with
    r = c.post(API + "/settings/unmute", json={"account": "1234567890", "campaign": "6"})
    assert r.get_json()["account"]["muted"] == {}
    assert [x["by"] for x in c.get(API + "/settings").get_json()["changes"]] == [STAFF] * 4


@pytest.mark.parametrize("url, body, fragment", [
    ("/settings/account", {"account": "mcc-9923720096-USD", "targets": {"cpa": 5}}, "Pick one account"),
    ("/settings/account", {"account": "1234567890", "targets": {"cpa": "abc"}}, "must be a number"),
    ("/settings/account", {"account": "1234567890", "targets": {"cvr": "500"}}, "must be between"),
    ("/settings/account", {"account": "1234567890", "tolerance": "1"}, "between 5 and 100"),
    ("/settings/campaign", {"account": "1234567890", "campaign": "x", "cpa": 5}, "campaign id"),
    ("/settings/mute", {"account": "1234567890", "campaign": "5", "why": ""}, "Say why"),
    ("/settings/unmute", {"account": "mcc-1", "campaign": "5"}, "Pick one account")])
def test_input_that_is_not_valid_is_refused_with_the_reason_and_saves_nothing(store, url, body, fragment):
    r = client_as(STAFF).post(API + url, json=body)
    assert r.status_code == 400 and fragment in r.get_json()["error"] and st.load(store) == {"accounts": {}, "changes": []}


def test_an_unreadable_store_is_said_not_crashed(monkeypatch):
    class Down(MemStore):
        StoreUnavailable = type("StoreUnavailable", (Exception,), {})

        def state_get(self, key, default=None):
            raise Down.StoreUnavailable("database down")
    monkeypatch.setattr(app_module, "_mon_store", lambda: Down())
    monkeypatch.setattr(app_module, "_pm_store_error", lambda e: ({"error": "store: %s" % e}, 503))
    r = client_as(STAFF).get(API + "/settings")
    assert r.status_code == 503 and r.get_json() == {"error": "store: database down"}
    for url, body in (("/settings/account", {"account": "123"}), ("/settings/campaign", {"account": "1234567890", "campaign": "5", "cpa": 5}),
                      ("/settings/mute", {"account": "1234567890", "campaign": "5", "why": "x"}), ("/settings/unmute", {"account": "1234567890", "campaign": "5"})):
        assert client_as(STAFF).post(API + url, json=body).status_code == 503, url


def test_the_monitor_is_known_to_the_alert_panel_and_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    assert pm.AGENTS["kpi"]["label"] == "KPI Monitoring" and client_as(STAFF).get("/p2/paid-marketing/api/monitors/kpi/state").get_json()["label"] == "KPI Monitoring"
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/kpi-monitoring"' in paid and "c-kp" in paid and "KPI Monitoring" in paid
    assert "u:'/p2/paid-marketing/kpi-monitoring'" in paid and "u:'/p2/paid-marketing/kpi-monitoring'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-kp{" in css and ".kp-metrics{" in css and ".kp-evid{" in css
    for t in ("_paid_base.html", "paid_marketing.html"):
        v = {re.search(r"paid_marketing\.css'\) \}\}\?v=(\d+)", open(os.path.join(ROOT, "templates", x), encoding="utf-8").read()).group(1) for x in ("_paid_base.html", "paid_marketing.html")}
        assert len(v) == 1, v                                                                    # the stylesheet is re-versioned in the two templates that load it, together
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "campaign targets watched for drift, learning and delivery" in hub
    kn = app_module._VIMI_PLATFORM_KNOWLEDGE
    assert "KPI Monitoring (/p2/paid-marketing/kpi-monitoring)" in kn and "A figure with too little behind it" in kn and "—" not in kn.split("KPI Monitoring (", 1)[1].split("\n", 1)[0]
    card = paid.split('href="/p2/paid-marketing/kpi-monitoring" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card


def test_the_daily_workflow_asks_for_no_monitor_by_name_so_this_one_runs_in_it_every_day():
    flow = open(os.path.join(ROOT, ".github", "workflows", "paid-monitors.yml"), encoding="utf-8").read()
    assert """-d '{}'""" in flow and "KPI Monitoring" in flow and "—" not in flow
    assert not pm.AGENTS["kpi"].get("frequent") and not pm.AGENTS["kpi"].get("weekdays")
