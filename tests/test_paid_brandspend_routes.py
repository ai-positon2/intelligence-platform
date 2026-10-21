"""Brand Spend Alert: the routes and the page (app.py, templates/brand_spend.html)."""
import os
import re

import pytest

import app as app_module
from tracker import paid_brandspend as b
from tracker import paid_gads as base
from tracker import paid_gads_segments as gads
from tests import test_paid_brandspend as T

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/brandspend"
PAGE = "/p2/paid-marketing/brand-spend"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def test_the_page_is_for_signed_in_staff_only():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Brand Spend Alert" in html and 'id="acctPick"' in html and 'id="limit"' in html and "js/brand_spend_data.js" in html and "js/brand_spend.js" in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    assert client_as(None).get(API + "/report?account=1234567890").status_code == 302 and client_as("someone@gmail.com").get(API + "/report?account=1234567890").status_code == 302


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch):
    seen = []
    monkeypatch.setattr(b, "report_for", lambda *x, **k: seen.append((x, {a: v for a, v in k.items() if a != "mon"})) or {"accounts": []})   # the monitors' store is passed as `mon`
    r = client_as(STAFF).get(API + "/report?account=1234567890&limit=25&brand=1,2&notbrand=3&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"accounts": []}
    assert seen[0] == (("1234567890",), {"limit": "25", "overrides": {"1": True, "2": True, "3": False}, "force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1234567890")
    client_as(STAFF).get(API + "/report?account=1234567890&refresh=0")
    assert seen == [(("1234567890",), {"limit": None, "overrides": {}})] * 2          # no refresh asked: no force


@pytest.mark.parametrize("err, status, key", [
    (gads.GadsError("not_configured", "not set"), 503, "kind"), (gads.GadsError("quota", "slow down"), 429, "kind"), (gads.GadsError("auth", "revoked"), 502, "kind"),
    (b.BadRequest("The limit must be between 10% and 90%."), 400, "error")])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, err, status, key):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(b, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == status and str(err) in r.get_json()["error"] and key in r.get_json()


def test_a_refresh_that_is_too_soon_says_when_to_come_back(monkeypatch):
    def soon(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 90)
    monkeypatch.setattr(b, "report_for", soon)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 90


def test_a_bad_limit_and_a_bad_account_are_refused_by_the_real_report(monkeypatch):
    monkeypatch.setattr(gads, "list_accounts", lambda: [T.ACCT])
    r = client_as(STAFF).get(API + "/report?account=1234567890&limit=5")
    assert r.status_code == 400 and "between 10% and 90%" in r.get_json()["error"]
    r = client_as(STAFF).get(API + "/report?account=9999999999")
    assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]


def test_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it():
    from tests import hidden_paid
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/brand-spend"' in paid and "c-bs" in paid and "Brand Spend Alert" in paid
    assert "u:'/p2/paid-marketing/brand-spend'" in paid and "u:'/p2/paid-marketing/brand-spend'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-bs{" in css and ".pm.pm-bs" in css and ".bs-band{" in css
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "brand spend watched against its usual week" in hub
    assert "Brand Spend Alert (/p2/paid-marketing/brand-spend)" in app_module._VIMI_PLATFORM_KNOWLEDGE and "the account's own history" in app_module._VIMI_PLATFORM_KNOWLEDGE
    card = paid.split('href="/p2/paid-marketing/brand-spend" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "\u2014" not in card and "\u2013" not in card
