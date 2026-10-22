"""Keyword Visibility Tracker: the routes and the page (app.py, templates/keyword_visibility.html). No network."""
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
from tests import test_paid_kvis as TK  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_kvis as kv  # noqa: E402
from tracker import paid_kvis_math as m  # noqa: E402
from tracker import paid_kvis_settings as st  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/kvis"
PAGE = "/p2/paid-marketing/keyword-visibility"


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
    assert "Keyword Visibility Tracker" in html and 'id="acctPick"' in html and "js/kvis_data.js" in html and "js/kvis.js" in html and 'id="monPanel"' in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    for method, url in (("get", "/report?account=1234567890"), ("get", "/settings"), ("post", "/settings/account"), ("post", "/settings/ignore")):
        assert getattr(client_as(None), method)(API + url).status_code == 302 and getattr(client_as("someone@gmail.com"), method)(API + url).status_code == 302, url


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch, store):
    seen = []
    monkeypatch.setattr(kv, "report_for", lambda *x, **kw: seen.append((x, {a: v for a, v in kw.items() if a != "mon"}, kw["mon"] is store)) or {"accounts": []})
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"accounts": []} and seen[0] == (("1234567890",), {"force": True}, True)
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1234567890&refresh=0")
    assert seen == [(("1234567890",), {}, True)]                                                       # no refresh asked: no force


@pytest.mark.parametrize("err, status, key", [
    (gads.GadsError("not_configured", "not set"), 503, "kind"), (gads.GadsError("quota", "slow down"), 429, "kind"), (gads.GadsError("auth", "revoked"), 502, "kind"),
    (kv.BadRequest("That is not one of Position2's accounts."), 400, "error")])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, store, err, status, key):
    def boom(*x, **kw):
        raise err
    monkeypatch.setattr(kv, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == status and str(err) in r.get_json()["error"] and key in r.get_json()


def test_a_refresh_that_is_too_soon_says_when_to_come_back(monkeypatch, store):
    def soon(*x, **kw):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 90)
    monkeypatch.setattr(kv, "report_for", soon)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 90


def test_a_bad_account_and_a_meta_account_are_refused_by_the_real_report(monkeypatch, store):
    monkeypatch.setattr(gads, "list_accounts", lambda: [TK.ACCT])
    r = client_as(STAFF).get(API + "/report?account=9999999999")
    assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]
    r = client_as(STAFF).get(API + "/report?account=act_1234567")
    assert r.status_code == 400 and "Google Ads accounts only" in r.get_json()["error"]


def test_the_real_report_is_built_from_the_read_and_what_the_team_saved(monkeypatch, store):
    from tracker import paid_geo
    weeks = [a.isoformat() for a, _b in m.week_list(dt.date.today())]
    rows = [TK.week_row("9", "101", w, top=0.3) for w in weeks]
    monkeypatch.setattr(gads, "list_accounts", lambda: [TK.ACCT])
    monkeypatch.setattr(gads, "search", TK.searcher(weeks={TK.ACCT["id"]: rows}), raising=True)
    paid_geo.clear_cache()
    base.reset_state()
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == 200
    j = r.get_json()
    a = j["accounts"][0]
    assert j["account"]["name"] == "Acme Dental" and a["id"] == "1234567890" and a["week"] == weeks[-1] and a["counts"] == {"high": 0, "medium": 1, "low": 0}
    assert a["items"][0]["findings"][0]["rule"] == "top_is_low"
    st.save_account(store, TK.ACCT["id"], "Acme Dental", {"topIs": 25}, STAFF)                       # a lower wanted share applies to the saved read: no second read
    j2 = client_as(STAFF).get(API + "/report?account=1234567890").get_json()
    assert j2["accounts"][0]["status"] == "clean" and j2["accounts"][0]["saved"]["topIs"] == 25
    paid_geo.clear_cache()


def test_what_the_team_saved_is_read_and_saved_for_the_whole_team(store):
    c = client_as(STAFF)
    assert c.get(API + "/settings").get_json()["accounts"] == {}
    r = c.post(API + "/settings/account", json={"account": "1234567890", "name": "Acme", "topIs": "40", "qsMin": "6", "topN": "30", "ignore": "sister.com"})
    assert r.status_code == 200 and r.get_json() == {"account": {"topIs": 40.0, "qsMin": 6, "topN": 30, "ignore": ["sister.com"], "name": "Acme"}}
    assert st.load(store)["changes"][0]["by"] == STAFF
    r = c.post(API + "/settings/ignore", json={"account": "1234567890", "domains": "partner.org"})
    assert r.get_json()["account"]["ignore"] == ["sister.com", "partner.org"] and r.get_json()["account"]["topIs"] == 40.0
    shown = c.get(API + "/settings").get_json()
    assert shown["accounts"]["1234567890"]["ignore"] == ["sister.com", "partner.org"] and [x["by"] for x in shown["changes"]] == [STAFF] * 2


@pytest.mark.parametrize("url, body, fragment", [
    ("/settings/account", {"account": "mcc-9923720096-USD", "topIs": 5}, "Pick one Google Ads account"),
    ("/settings/account", {"account": "1234567890", "topIs": "abc"}, "must be a number"),
    ("/settings/account", {"account": "1234567890", "qsMin": "11"}, "must be between 2 and 10"),
    ("/settings/account", {"account": "1234567890", "topN": "3"}, "must be between 5 and 100"),
    ("/settings/account", {"account": "1234567890", "ignore": "not a domain"}, "is not a domain"),
    ("/settings/ignore", {"account": "1234567890", "domains": ""}, "at least one domain"),
    ("/settings/ignore", {"account": "act_1", "domains": "a.com"}, "Pick one Google Ads account")])
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
    for url, body in (("/settings/account", {"account": "1234567890"}), ("/settings/ignore", {"account": "1234567890", "domains": "a.com"})):
        assert client_as(STAFF).post(API + url, json=body).status_code == 503, url


def test_the_monitor_is_known_to_the_alert_panel_and_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    assert pm.AGENTS["kvis"]["label"] == "Keyword Visibility Tracker" and client_as(STAFF).get("/p2/paid-marketing/api/monitors/kvis/state").get_json()["label"] == "Keyword Visibility Tracker"
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/keyword-visibility"' in paid and "c-kv" in paid and "Keyword Visibility Tracker" in paid
    assert "u:'/p2/paid-marketing/keyword-visibility'" in paid and "u:'/p2/paid-marketing/keyword-visibility'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-kv{" in css and ".pm-kp .cc-f input{" in css
    for t in ("_paid_base.html", "paid_marketing.html"):
        assert "paid_marketing.css') }}?v=56" in open(os.path.join(ROOT, "templates", t), encoding="utf-8").read()
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "top-spend keywords followed week by week for the top of the page, Quality Score and new competitors" in hub
    kn = app_module._VIMI_PLATFORM_KNOWLEDGE
    assert "Keyword Visibility Tracker (/p2/paid-marketing/keyword-visibility)" in kn and "gives no keyword-level share lost to budget" in kn and "—" not in kn.split("Keyword Visibility Tracker (", 1)[1].split("\n", 1)[0]
    card = paid.split('href="/p2/paid-marketing/keyword-visibility" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card


def test_the_daily_workflow_asks_for_no_monitor_by_name_so_this_one_runs_in_it_on_tuesdays():
    flow = open(os.path.join(ROOT, ".github", "workflows", "paid-monitors.yml"), encoding="utf-8").read()
    assert """-d '{}'""" in flow and "Keyword Visibility Tracker" in flow and "—" not in flow
    assert not pm.AGENTS["kvis"].get("frequent") and pm.AGENTS["kvis"]["weekdays"] == (1,)
