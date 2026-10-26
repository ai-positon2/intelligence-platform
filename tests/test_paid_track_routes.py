"""Tracking & Data Health: the routes and the page (app.py, templates/tracking_health.html). No network."""
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
from tests import test_paid_track as TT  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402
from tracker import paid_track as t  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/track"
PAGE = "/p2/paid-marketing/tracking-health"


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
    assert "Tracking &amp; Data Health" in html and 'id="acctPick"' in html and "js/tracking_health_data.js" in html and "js/tracking_health.js" in html and 'id="monPanel"' in html
    assert 'id="blind"' in html and "pm_scope.js" in html and html.index("pm_scope.js") < html.index("tracking_health.js")
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    for url in ("/report?account=1234567890",):
        assert client_as(None).get(API + url).status_code == 302 and client_as("someone@gmail.com").get(API + url).status_code == 302, url


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch, store):
    seen = []
    monkeypatch.setattr(t, "report_for", lambda *x, **kw: seen.append((x, {a: v for a, v in kw.items() if a != "mon"}, kw["mon"] is store)) or {"accounts": []})
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"accounts": []} and seen[0] == (("1234567890",), {"force": True}, True)
    seen.clear()
    client_as(STAFF).get(API + "/report?account=act_1234567")
    client_as(STAFF).get(API + "/report?account=1234567890&refresh=0")
    assert seen == [(("act_1234567",), {}, True), (("1234567890",), {}, True)]                      # no refresh asked: no force


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


def real_days(stop_after=0):
    """Thirty days of 50 spent and 3 conversions each, counted back from the real UTC date (the route reads the clock); the last `stop_after` days record none."""
    today = dt.datetime.now(dt.timezone.utc).date()
    return [{"customer": {"timeZone": "UTC"}, "segments": {"date": (today - dt.timedelta(days=i)).isoformat()}, "metrics": {"costMicros": "50000000", "conversions": 0.0 if i <= stop_after else 3.0}}
            for i in range(1, 31)]


def test_the_real_report_is_built_from_the_read_and_the_monitors_own_readings(monkeypatch, store):
    from tracker import paid_geo
    monkeypatch.setattr(gads, "list_accounts", lambda: [TT.ACCT])
    monkeypatch.setattr(gads, "search", TT.searcher(days={TT.ACCT["id"]: real_days(stop_after=3)}, actions={}, action_days={}, lists={}), raising=True)
    paid_geo.clear_cache()
    base.reset_state()
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == 200
    j = r.get_json()
    a = j["accounts"][0]
    assert j["account"]["name"] == "Acme Dental" and a["id"] == "1234567890" and a["status"] == "fix" and a["counts"] == {"high": 1, "medium": 0, "low": 0}
    assert a["findings"][0]["rule"] == "no_conv" and "No conversions in the last 3 days" in a["findings"][0]["title"] and j["summary"]["dark"] == 1 and len(j["notMeasured"]) == 4
    paid_geo.clear_cache()


def test_the_monitor_is_known_to_the_alert_panel_and_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    assert pm.AGENTS["track"] == {"label": "Tracking & Data Health", "page": "/p2/paid-marketing/tracking-health"}
    assert client_as(STAFF).get("/p2/paid-marketing/api/monitors/track/state").get_json()["label"] == "Tracking & Data Health"
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/tracking-health"' in paid and "c-th" in paid and "Tracking &amp; Data Health" in paid
    assert "u:'/p2/paid-marketing/tracking-health'" in paid and "u:'/p2/paid-marketing/tracking-health'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-th{" in css and ".th-fix{" in css and ".th-blind-l{" in css
    v = {re.search(r"paid_marketing\.css'\) \}\}\?v=(\d+)", open(os.path.join(ROOT, "templates", x), encoding="utf-8").read()).group(1) for x in ("_paid_base.html", "paid_marketing.html")}
    assert len(v) == 1, v                                                                       # the stylesheet is re-versioned in the two templates that load it, together
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "tracking watched for conversions that stopped" in hub
    kn = app_module._VIMI_PLATFORM_KNOWLEDGE
    mine = kn.split("Tracking & Data Health (", 1)[1].split("\n", 1)[0]
    assert "Tracking & Data Health (/p2/paid-marketing/tracking-health)" in kn and "A figure with too little behind it" in mine and "—" not in mine and "–" not in mine
    card = paid.split('href="/p2/paid-marketing/tracking-health" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card


def test_the_daily_workflow_asks_for_no_monitor_by_name_so_this_one_runs_in_it_every_day():
    flow = open(os.path.join(ROOT, ".github", "workflows", "paid-monitors.yml"), encoding="utf-8").read()
    assert """-d '{}'""" in flow and "Tracking & Data Health" in flow and "—" not in flow
    assert not pm.AGENTS["track"].get("frequent") and not pm.AGENTS["track"].get("weekdays")
