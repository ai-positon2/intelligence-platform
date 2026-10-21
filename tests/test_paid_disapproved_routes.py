"""Disapproved Ads Tracker: the routes and the page (app.py, templates/disapproved_ads.html)."""
import os
import re

import pytest

import app as app_module
from tracker import paid_disapproved as d
from tracker import paid_gads as base
from tracker import paid_gads_segments as gads
from tests import test_paid_disapproved as T

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/disapproved"
PAGE = "/p2/paid-marketing/disapproved-ads"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def test_the_page_is_for_signed_in_staff_only():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Disapproved Ads Tracker" in html and 'id="acctPick"' in html and "js/disapproved_data.js" in html and "js/disapproved.js" in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    assert client_as(None).get(API + "/report?account=1234567890").status_code == 302 and client_as("someone@gmail.com").get(API + "/report?account=1234567890").status_code == 302


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch):
    seen = []
    monkeypatch.setattr(d, "report_for", lambda *x, **k: seen.append((x, {a: v for a, v in k.items() if a != "mon"})) or {"accounts": []})   # the monitors' store is passed as `mon`
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"accounts": []} and seen[0] == (("1234567890",), {"force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1234567890")
    client_as(STAFF).get(API + "/report?account=1234567890&refresh=0")
    assert seen == [(("1234567890",), {})] * 2                                         # no refresh asked: no force


@pytest.mark.parametrize("err, status, key", [
    (gads.GadsError("not_configured", "not set"), 503, "kind"), (gads.GadsError("quota", "slow down"), 429, "kind"), (gads.GadsError("auth", "revoked"), 502, "kind"),
    (d.BadRequest("That is not one of Position2's accounts."), 400, "error")])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, err, status, key):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(d, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == status and str(err) in r.get_json()["error"] and key in r.get_json()


def test_a_refresh_that_is_too_soon_says_when_to_come_back(monkeypatch):
    def soon(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 90)
    monkeypatch.setattr(d, "report_for", soon)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 90


def test_a_bad_account_is_refused_by_the_real_report(monkeypatch):
    monkeypatch.setattr(gads, "list_accounts", lambda: [T.ACCT])
    r = client_as(STAFF).get(API + "/report?account=9999999999")
    assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]


def test_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it():
    from tests import hidden_paid
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/disapproved-ads"' in paid and "c-da" in paid and "Disapproved Ads Tracker" in paid
    assert "u:'/p2/paid-marketing/disapproved-ads'" in paid and "u:'/p2/paid-marketing/disapproved-ads'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-da{" in css and ".pm.pm-da" in css
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "disapproved ads found before a client does" in hub
    assert "Disapproved Ads Tracker (/p2/paid-marketing/disapproved-ads)" in app_module._VIMI_PLATFORM_KNOWLEDGE and "Policy manager" in app_module._VIMI_PLATFORM_KNOWLEDGE
    card = paid.split('href="/p2/paid-marketing/disapproved-ads" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card
