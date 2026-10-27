"""Creative & Asset Health: the routes and the page (app.py, templates/creative_health.html). No network."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_creative as TT  # noqa: E402
from tracker import paid_assets  # noqa: E402
from tracker import paid_creative as t  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/creative"
PAGE = "/p2/paid-marketing/creative-health"


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
    assert "Creative &amp; Asset Health" in html and 'id="acctPick"' in html and "js/creative_health_data.js" in html and "js/creative_health.js" in html and 'id="monPanel"' in html
    assert 'id="blind"' in html and "pm_scope.js" in html and html.index("pm_scope.js") < html.index("creative_health.js")
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    assert client_as(None).get(API + "/report?account=1234567890").status_code == 302 and client_as("someone@gmail.com").get(API + "/report?account=1234567890").status_code == 302


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch, store):
    seen = []
    monkeypatch.setattr(t, "report_for", lambda *x, **kw: seen.append((x, kw)) or {"accounts": []})
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"accounts": []} and seen[0] == (("1234567890",), {"force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=act_1234567")
    client_as(STAFF).get(API + "/report?account=1234567890&refresh=0")
    assert seen == [(("act_1234567",), {}), (("1234567890",), {})]                                  # no refresh asked: no force


@pytest.mark.parametrize("err, status, key", [
    (gads.GadsError("not_configured", "not set"), 503, "kind"), (gads.GadsError("quota", "slow down"), 429, "kind"), (gads.GadsError("auth", "revoked"), 502, "kind"),
    (t.BadRequest("That is not one of Position2's accounts."), 400, "error")])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, store, err, status, key):
    def boom(*x, **kw):
        raise err
    monkeypatch.setattr(t, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == status and str(err) in r.get_json()["error"] and key in r.get_json()


def test_a_meta_error_and_a_refresh_that_is_too_soon_are_said(monkeypatch, store):
    def meta(*x, **kw):
        raise base.AdsError("Meta says the token expired.")
    monkeypatch.setattr(t, "report_for", meta)
    r = client_as(STAFF).get(API + "/report?account=act_1234567")
    assert r.status_code == 502 and r.get_json()["kind"] == "meta" and "token expired" in r.get_json()["error"]

    def soon(*x, **kw):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 90)
    monkeypatch.setattr(t, "report_for", soon)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 90


def test_a_bad_account_is_refused_by_the_real_report(monkeypatch, store):
    monkeypatch.setattr(gads, "list_accounts", lambda: [TT.ACCT])
    r = client_as(STAFF).get(API + "/report?account=9999999999")
    assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]


def test_the_real_report_is_built_from_the_read_and_asset_coverage(monkeypatch, store):
    from tracker import paid_geo
    import datetime as dt
    monkeypatch.setattr(gads, "list_accounts", lambda: [TT.ACCT])
    monkeypatch.setattr(dt, "date", type("D", (dt.date,), {"today": classmethod(lambda cls: TT.TODAY)}))
    monkeypatch.setattr(gads, "search", TT.searcher(ads=TT.ad_weeks(base_ctr=0.05, recent_ctr=0.02), weak_ads=[TT.weak_ad_row()]), raising=True)
    cover = {"campaigns": [{"id": "9", "name": "Search Crowns", "gaps": [{"key": "SITELINK", "scope": "Campaign", "label": "Sitelinks", "severity": "high", "have": 0, "target": 4, "add": 4, "message": "None."}]}]}
    monkeypatch.setattr(paid_assets, "report_for", lambda *a, **k: cover)
    paid_geo.clear_cache()
    base.reset_state()
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == 200
    j = r.get_json()
    a = j["accounts"][0]
    assert j["account"]["name"] == "Acme Dental" and a["status"] == "fix" and a["counts"] == {"high": 2, "medium": 1, "low": 0}
    assert sorted(f["rule"] for f in a["findings"]) == ["low_rating", "missing_asset", "tired"] and len(j["notMeasured"]) >= 4 and j["thresholds"]["recentWeeks"] == 2
    paid_geo.clear_cache()


def test_the_monitor_is_known_to_the_alert_panel_and_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    assert pm.AGENTS["creative"] == {"label": "Creative & Asset Health", "page": "/p2/paid-marketing/creative-health"}
    assert client_as(STAFF).get("/p2/paid-marketing/api/monitors/creative/state").get_json()["label"] == "Creative & Asset Health"
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/creative-health"' in paid and "c-ch" in paid and "Creative &amp; Asset Health" in paid
    assert "u:'/p2/paid-marketing/creative-health'" in paid and "u:'/p2/paid-marketing/creative-health'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-ch{" in css
    v = {re.search(r"paid_marketing\.css'\) \}\}\?v=(\d+)", open(os.path.join(ROOT, "templates", x), encoding="utf-8").read()).group(1) for x in ("_paid_base.html", "paid_marketing.html")}
    assert len(v) == 1, v                                                                       # the stylesheet is re-versioned in the two templates that load it, together
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "tired creatives and missing assets caught before results slip" in hub
    kn = app_module._VIMI_PLATFORM_KNOWLEDGE
    mine = kn.split("Creative & Asset Health (", 1)[1].split("\n", 1)[0]
    assert "Creative & Asset Health (/p2/paid-marketing/creative-health)" in kn and "not blamed on one creative" in mine and "—" not in mine and "–" not in mine
    card = paid.split('href="/p2/paid-marketing/creative-health" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card


def test_the_daily_workflow_asks_for_no_monitor_by_name_so_this_one_runs_in_it_every_day():
    flow = open(os.path.join(ROOT, ".github", "workflows", "paid-monitors.yml"), encoding="utf-8").read()
    assert """-d '{}'""" in flow and "—" not in flow
    assert not pm.AGENTS["creative"].get("frequent") and not pm.AGENTS["creative"].get("weekdays")
