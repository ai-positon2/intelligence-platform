"""Test & Experiment Tracker: the routes, the page and where the agent is listed (app.py, templates)."""
import os
import re

import pytest

import app as app_module
from tracker import paid_exp as ex
from tracker import paid_gads as base
from tracker import paid_gads_segments as gads

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/exp"
PAGE = "/p2/paid-marketing/experiment-tracker"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


REPORT = {"account": {"id": "1112223334", "name": "Acme", "currency": "USD"}, "summary": {"needsAction": 2}, "experiments": [], "adTests": [], "needs": [], "warnings": [], "unread": [],
          "notes": [], "readAt": "2026-10-18T12:00:00+00:00", "generatedAt": "2026-10-18T12:00:00+00:00"}


def test_the_report_route_returns_the_report_and_passes_refresh_on(monkeypatch):
    seen = []
    monkeypatch.setattr(ex, "report_for", lambda *x, **k: seen.append((x, k)) or REPORT)
    r = client_as(STAFF).get(API + "/report?account=1112223334&refresh=1")
    assert r.status_code == 200 and r.get_json()["summary"]["needsAction"] == 2 and seen[0] == (("1112223334",), {"force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1112223334")
    client_as(STAFF).get(API + "/report?account=1112223334&refresh=0")
    assert seen == [(("1112223334",), {}), (("1112223334",), {})]


@pytest.mark.parametrize("err, status", [
    (gads.GadsError("not_configured", "not set"), 503), (gads.GadsError("quota", "slow down"), 429), (gads.GadsError("auth", "revoked"), 502),
    (gads.GadsError("api", "google broke"), 502), (ex.BadRequest("That account is not one of Position2's Google Ads accounts."), 400)])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, err, status):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(ex, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1112223334")
    assert r.status_code == status and str(err) in r.get_json()["error"]


def test_a_refresh_that_is_too_soon_says_when_to_try_again_and_the_api_is_staff_only(monkeypatch):
    def boom(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 45)
    monkeypatch.setattr(ex, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1112223334&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 45
    assert client_as(None).get(API + "/report?account=1").status_code == 302 and client_as("someone@gmail.com").get(API + "/report?account=1").status_code == 302


def test_a_bad_account_is_a_400_through_the_real_parser():
    r = client_as(STAFF).get(API + "/report?account=nonsense")
    assert r.status_code == 400 and "10-digit Google Ads customer id" in r.get_json()["error"]
    assert client_as(STAFF).get(API + "/report").status_code == 400
    assert client_as(STAFF).get(API + "/report?account=1112223334%2Bact_1234567890").status_code == 400


def test_the_page_opens_for_staff_and_is_closed_to_everyone_else():
    r = client_as(STAFF).get(PAGE)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Test &amp; Experiment Tracker" in html and 'id="acctPick"' in html and "js/exp_data.js" in html and "js/experiment_tracker.js" in html and "css/experiment_tracker.css" in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302


def _read(name):
    return open(os.path.join(ROOT, name), encoding="utf-8").read()


def _cards(html):
    body = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return [c for c in re.findall(r'class="dash-card ([^"]*)"', body)]


def test_the_card_is_on_the_paid_marketing_page_and_the_command_palette_lists_it():
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/experiment-tracker"' in paid and "c-ex" in paid
    for name in ("templates/_paid_kpal.html", "templates/paid_marketing.html"):
        assert "/p2/paid-marketing/experiment-tracker" in _read(name), name
    assert ".c-ex{" in _read("static/css/paid_marketing.css")


def test_the_hub_counts_every_live_paid_marketing_card_and_the_whole_platform():
    paid_live = sum(1 for c in _cards(_read("templates/paid_marketing.html")) if c.split()[0] == "active")
    strategic_live = sum(1 for c in _cards(_read("templates/b2b_agents.html")) if c.split()[0] == "active")
    hub = _read("templates/hub.html")
    assert '<div class="card-stat"><span>%d</span> dashboards</div>' % paid_live in hub and '<div class="card-stat"><span>%d</span> live</div>' % paid_live in hub
    assert 'data-lxn="%d"' % (strategic_live + paid_live + 16) in hub                           # 16 SEO and AEO tools beside the two workspaces


def test_the_assistant_knows_the_agent():
    src = open(app_module.__file__, encoding="utf-8").read()
    assert "Test & Experiment Tracker (/p2/paid-marketing/experiment-tracker)" in src and "two-sided two-proportion z-test" in src and "no model is asked whether a test won" in src


def test_the_agent_only_reads():
    src = open(app_module.__file__, encoding="utf-8").read()
    block = src[src.index("# Test & Experiment Tracker ---"):src.index("# Strategy & Campaign Book ---")]
    assert block.count("@app.route") == 2 and 'methods=' not in block
