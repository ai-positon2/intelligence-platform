"""Cross-Channel & Attribution Analysis: the routes, the page and where the agent is listed (app.py, templates)."""
import io
import os
import zipfile

import pytest

import app as app_module
from tests import test_paid_xchannel as t
from tracker import paid_gads as base
from tracker import paid_gads_segments as gads
from tracker import paid_xchannel as xc

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/xchannel"
PAGE = "/p2/paid-marketing/cross-channel"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def report(monkeypatch):
    monkeypatch.setattr(t.paid_segments, "_meta_target", lambda act, store: dict(t.META_ACCT, id=act))
    t.paid_gads.forget()
    r = t.run(**t.settings_fns(), actual_conv="100")
    t.paid_gads.forget()
    return r


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch, report):
    seen = []
    monkeypatch.setattr(xc, "report_for", lambda *x, **k: seen.append((x, k)) or report)
    r = client_as(STAFF).get(API + "/report?account=1112223334%2Bact_5550001&range=90&measure=all&actualConv=1%2C200&actualValue=50000&refresh=1")
    assert r.status_code == 200 and r.get_json()["platforms"] == ["google", "meta"] and r.headers["Cache-Control"] == "private, no-store"
    assert seen[0] == (("1112223334+act_5550001",), {"preset": "90", "measure": "all", "actual_conv": "1,200", "actual_value": "50000", "force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1112223334")
    client_as(STAFF).get(API + "/report?account=1112223334&refresh=0")
    assert seen == [(("1112223334",), {"preset": "30", "measure": "primary", "actual_conv": None, "actual_value": None, "force": False})] * 2


@pytest.mark.parametrize("err, status, key", [
    (gads.GadsError("not_configured", "not set"), 503, "kind"), (gads.GadsError("quota", "slow down"), 429, "kind"), (gads.GadsError("auth", "revoked"), 502, "kind"),
    (xc.BadRequest("range should be one of: 30"), 400, "error"), (base.AdsError("Google answered 500"), 502, "kind")])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, err, status, key):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(xc, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1112223334")
    assert r.status_code == status and str(err) in r.get_json()["error"] and key in r.get_json()


def test_a_refresh_that_is_too_soon_and_a_spent_allowance_say_when_to_come_back(monkeypatch):
    def soon(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 45)
    monkeypatch.setattr(xc, "report_for", soon)
    r = client_as(STAFF).get(API + "/report?account=1112223334&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 45

    def spent(*x, **k):
        raise base.OverQuota("Google's allowance is spent.", until=1790000000)
    monkeypatch.setattr(xc, "report_for", spent)
    r = client_as(STAFF).get(API + "/report?account=1112223334")
    assert r.status_code == 429 and r.get_json()["blockedUntil"] == 1790000000 and r.get_json()["kind"] == "quota"


def test_the_api_is_staff_only(monkeypatch, report):
    monkeypatch.setattr(xc, "report_for", lambda *x, **k: report)
    for path in ("/report?account=1112223334", "/download?account=1112223334&file=pdf"):
        assert client_as(None).get(API + path).status_code == 302 and client_as("someone@gmail.com").get(API + path).status_code == 302


def test_a_bad_account_range_or_number_is_a_400_through_the_real_checks():
    r = client_as(STAFF).get(API + "/report?account=nonsense")
    assert r.status_code == 400 and "10-digit Google Ads customer id" in r.get_json()["error"]
    assert client_as(STAFF).get(API + "/report").status_code == 400
    assert client_as(STAFF).get(API + "/report?account=1112223334%2B1112223334").status_code == 400


@pytest.mark.parametrize("kind, magic, ctype, ext", [("xlsx", b"PK", "spreadsheetml", ".xlsx"), ("pdf", b"%PDF", "application/pdf", ".pdf"), ("zip", b"PK", "application/zip", ".zip")])
def test_each_download_is_a_file_with_a_name_made_from_the_account(monkeypatch, report, kind, magic, ctype, ext):
    seen = []
    monkeypatch.setattr(xc, "report_for", lambda *x, **k: seen.append(k) or report)
    r = client_as(STAFF).get(API + "/download?account=1112223334%2Bact_5550001&range=60&file=" + kind)
    assert r.status_code == 200 and r.data[:len(magic)] == magic and ctype in r.headers["Content-Type"]
    assert r.headers["Content-Disposition"] == 'attachment; filename="Acme-Dental-Acme-Dental-Meta-cross-channel-and-attribution%s"' % ext and r.headers["Cache-Control"] == "private, no-store"
    assert seen[0]["preset"] == "60"


def test_the_zip_download_holds_the_tables(monkeypatch, report):
    monkeypatch.setattr(xc, "report_for", lambda *x, **k: report)
    r = client_as(STAFF).get(API + "/download?account=1112223334&file=zip")
    assert "platforms.csv" in zipfile.ZipFile(io.BytesIO(r.data)).namelist()


def test_a_download_of_an_unknown_kind_is_refused_and_a_report_that_fails_is_the_pages_error(monkeypatch):
    assert client_as(STAFF).get(API + "/download?account=1112223334&file=exe").status_code == 400
    assert client_as(STAFF).get(API + "/download?account=1112223334").status_code == 400

    def boom(*x, **k):
        raise gads.GadsError("quota", "slow down")
    monkeypatch.setattr(xc, "report_for", boom)
    r = client_as(STAFF).get(API + "/download?account=1112223334&file=pdf")
    assert r.status_code == 429 and r.get_json()["error"] == "slow down"


def test_a_file_that_cannot_be_made_is_said_in_words(monkeypatch, report):
    from tracker import paid_xchannel_export as export
    monkeypatch.setattr(xc, "report_for", lambda *x, **k: report)

    def broken(r):
        raise RuntimeError("disk")
    monkeypatch.setattr(export, "pdf", broken)
    r = client_as(STAFF).get(API + "/download?account=1112223334&file=pdf")
    assert r.status_code == 500 and r.get_json()["error"] == "The file could not be made (RuntimeError)."


def test_the_assistant_knows_about_the_agent():
    src = open(os.path.join(ROOT, "app.py")).read()
    assert "Cross-Channel & Attribution Analysis (/p2/paid-marketing/cross-channel)" in src


def test_the_page_renders_for_staff_and_is_closed_to_everyone_else():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "<title>Cross-Channel &amp; Attribution" in html and "cross_channel.js" in html and "cross_channel.css" in html and "xch_data.js" in html and "pm_platform.js" in html
    for el in ('id="acctPick"', 'id="ranges"', 'id="measures"', 'id="fConv"', 'id="fValue"', 'id="finds"', 'id="channels"', 'id="types"', 'id="chartCost"', 'id="rec"', 'id="dlXlsx"'):
        assert el in html, el
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302


def test_every_address_the_page_script_uses_is_a_route():
    js = open(os.path.join(ROOT, "static", "js", "cross_channel.js"), encoding="utf-8").read()
    rules = {r.rule for r in app_module.app.url_map.iter_rules()}
    for path in ("/p2/paid-marketing/api/xchannel/report", "/p2/paid-marketing/api/xchannel/download", "/p2/paid-marketing/api/gads/accounts", PAGE):
        assert path in rules, path
    assert 'API = "/p2/paid-marketing/api/xchannel"' in js and 'BASE = "/p2/paid-marketing/api/gads"' in js and '"/report?"' in js and '"/download?"' in js and 'BASE + "/accounts"' in js


def test_the_agent_is_on_the_paid_marketing_page_the_palette_and_the_hub_counts_it():
    import re
    root = os.path.join(ROOT, "templates")
    for name in ("paid_marketing.html", "_paid_kpal.html"):
        assert "/p2/paid-marketing/cross-channel" in open(os.path.join(root, name), encoding="utf-8").read(), name
    page = open(os.path.join(root, "paid_marketing.html"), encoding="utf-8").read()
    assert "c-xch" in page and "Cross-Channel &amp; Attribution Analysis" in page
    assert ".c-xch" in open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    hub = re.sub(r"<!--.*?-->", "", open(os.path.join(root, "hub.html"), encoding="utf-8").read(), flags=re.S)
    assert 'data-lxn="48"' in hub and "<span>21</span> dashboards" in hub and "Google and Meta compared side by side" in hub


def test_the_stylesheet_and_the_scripts_are_served():
    c = client_as(STAFF)
    for path in ("/static/css/cross_channel.css", "/static/js/xch_data.js", "/static/js/cross_channel.js"):
        assert c.get(path).status_code == 200, path
