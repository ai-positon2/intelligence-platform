"""Ad Copy Checker: the routes and the page (app.py, templates/ad_copy_checker.html). No network."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_copycheck as T  # noqa: E402
from tracker import paid_copycheck as cc  # noqa: E402
from tracker import paid_copycheck_settings as st  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/copycheck"
PAGE = "/p2/paid-marketing/ad-copy-checker"


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
    assert "Ad Copy Checker" in html and 'id="acctPick"' in html and "js/copycheck_data.js" in html and "js/copycheck.js" in html and 'id="monPanel"' in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302
    for method, url in (("get", "/report?account=1234567890"), ("get", "/settings"), ("post", "/settings/account"), ("post", "/settings/allow"), ("post", "/settings/allowed-word")):
        assert getattr(client_as(None), method)(API + url).status_code == 302 and getattr(client_as("someone@gmail.com"), method)(API + url).status_code == 302, url


def test_the_report_route_passes_what_the_page_sent_to_the_report(monkeypatch, store):
    seen = []
    monkeypatch.setattr(cc, "report_for", lambda *x, **k: seen.append((x, {a: v for a, v in k.items() if a != "mon"}, k["mon"] is store)) or {"accounts": []})
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"accounts": []} and seen[0] == (("1234567890",), {"force": True}, True)
    seen.clear()
    client_as(STAFF).get(API + "/report?account=act_1234567")
    client_as(STAFF).get(API + "/report?account=1234567890&refresh=0")
    assert seen == [(("act_1234567",), {}, True), (("1234567890",), {}, True)]                      # no refresh asked: no force


@pytest.mark.parametrize("err, status, key", [
    (gads.GadsError("not_configured", "not set"), 503, "kind"), (gads.GadsError("quota", "slow down"), 429, "kind"), (gads.GadsError("auth", "revoked"), 502, "kind"),
    (cc.BadRequest("That is not one of Position2's accounts."), 400, "error")])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, store, err, status, key):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(cc, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == status and str(err) in r.get_json()["error"] and key in r.get_json()


def test_a_refresh_that_is_too_soon_says_when_to_come_back(monkeypatch, store):
    def soon(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 90)
    monkeypatch.setattr(cc, "report_for", soon)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 90


def test_a_bad_account_is_refused_by_the_real_report(monkeypatch, store):
    monkeypatch.setattr(gads, "list_accounts", lambda: [T.ACCT])
    r = client_as(STAFF).get(API + "/report?account=9999999999")
    assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]


def test_what_the_team_saved_is_read_and_saved_for_the_whole_team(store):
    c = client_as(STAFF)
    assert c.get(API + "/settings").get_json() == {"accounts": {}, "allow": [], "changes": []}
    r = c.post(API + "/settings/account", json={"account": "1234567890", "name": "Acme Dental", "phones": "617-555-0100", "places": "Quincy, MA", "brand": "SmileCo", "allow": "zoom"})
    assert r.status_code == 200 and r.get_json() == {"account": {"phones": ["617-555-0100"], "places": ["Quincy, MA"], "brand": ["SmileCo"], "allow": ["zoom"], "name": "Acme Dental"}}
    assert st.load(store)["accounts"]["1234567890"]["phones"] == ["617-555-0100"] and st.load(store)["changes"][0]["by"] == STAFF
    r = c.post(API + "/settings/allowed-word", json={"account": "1234567890", "words": "invisalign"})
    assert r.get_json()["account"]["allow"] == ["zoom", "invisalign"] and r.get_json()["account"]["phones"] == ["617-555-0100"]
    r = c.post(API + "/settings/allow", json={"words": "Zoom, whitening"})
    assert r.get_json() == {"allow": ["zoom", "whitening"]} and c.get(API + "/settings").get_json()["allow"] == ["zoom", "whitening"]
    assert [x["by"] for x in c.get(API + "/settings").get_json()["changes"]] == [STAFF] * 3


@pytest.mark.parametrize("url, body, fragment", [
    ("/settings/account", {"account": "mcc-9923720096-USD", "phones": "617-555-0100"}, "Pick one account"),
    ("/settings/account", {"account": "1234567890", "phones": "617"}, "ten digit"),
    ("/settings/allowed-word", {"account": "1234567890", "words": ""}, "at least one word"),
    ("/settings/allow", {"words": "x1"}, "is not a word")])
def test_a_list_that_is_not_valid_is_refused_with_the_reason_and_saves_nothing(store, url, body, fragment):
    r = client_as(STAFF).post(API + url, json=body)
    assert r.status_code == 400 and fragment in r.get_json()["error"] and st.load(store) == st.empty()


def test_an_unreadable_store_is_said_not_crashed(monkeypatch):
    class Down(MemStore):
        StoreUnavailable = type("StoreUnavailable", (Exception,), {})

        def state_get(self, key, default=None):
            raise Down.StoreUnavailable("database down")
    monkeypatch.setattr(app_module, "_mon_store", lambda: Down())
    monkeypatch.setattr(app_module, "_pm_store_error", lambda e: ({"error": "store: %s" % e}, 503))
    r = client_as(STAFF).get(API + "/settings")
    assert r.status_code == 503 and r.get_json() == {"error": "store: database down"}
    r = client_as(STAFF).post(API + "/settings/allow", json={"words": "zoom"})
    assert r.status_code == 503


def test_the_monitor_is_known_to_the_alert_panel_and_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    assert pm.AGENTS["copycheck"]["label"] == "Ad Copy Checker" and client_as(STAFF).get("/p2/paid-marketing/api/monitors/copycheck/state").get_json()["label"] == "Ad Copy Checker"
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/ad-copy-checker"' in paid and "c-cc" in paid and "Ad Copy Checker" in paid
    assert "u:'/p2/paid-marketing/ad-copy-checker'" in paid and "u:'/p2/paid-marketing/ad-copy-checker'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-cc{" in css and ".pm.pm-cc" in css and ".cc-hl{" in css
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub and "ad copy proofread for typos and wrong numbers" in hub
    k = app_module._VIMI_PLATFORM_KNOWLEDGE
    assert "Ad Copy Checker (/p2/paid-marketing/ad-copy-checker)" in k and "This is right" in k and "Client Master Config is not built yet" in k
    card = paid.split('href="/p2/paid-marketing/ad-copy-checker" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card


# ── Claude's read of the grammar ────────────────────────────────────────────

@pytest.fixture
def grammar(monkeypatch, store):
    from tracker import paid_copycheck_ai as ai

    class G:
        asked, answers = [], []
    g = G()
    g.asked, g.answers = [], []
    raw = {"accounts": [{"id": "1234567890", "ads": [{"id": "1", "campaign": "Core", "texts": [{"slot": "headline", "text": "Their is a clinic near you"}]}]}], "readAt": "2026-10-22T09:00:00+00:00"}
    monkeypatch.setattr(cc, "load_raw", lambda account, **k: g.asked.append(account) or (raw, None, {"id": account, "name": "Acme Dental"}))
    monkeypatch.setattr(ai, "cached_proofread", lambda key, rows, **k: g.answers.append((key, [r["text"] for r in rows], k)) or {"checked": 1, "of": 1, "issues": []})
    monkeypatch.setattr(app_module, "_COPYCHECK_USES", {})
    return g


def test_the_grammar_read_is_for_staff_and_reads_the_lines_of_the_saved_ads_of_one_account(grammar):
    assert client_as(None).post(API + "/grammar", json={"account": "1234567890"}).status_code == 302 and client_as("someone@gmail.com").post(API + "/grammar", json={"account": "1234567890"}).status_code == 302
    r = client_as(STAFF).post(API + "/grammar", json={"account": "1234567890"})
    assert r.status_code == 200 and r.get_json() == {"checked": 1, "of": 1, "issues": [], "account": {"id": "1234567890", "name": "Acme Dental"}, "readAt": "2026-10-22T09:00:00+00:00"}
    assert grammar.asked == ["1234567890"] and grammar.answers == [("1234567890", ["Their is a clinic near you"], {"generated_at": "2026-10-22T09:00:00+00:00"})]


def test_an_mcc_is_refused_before_anything_is_read(grammar):
    r = client_as(STAFF).post(API + "/grammar", json={"account": "mcc-9923720096-USD"})
    assert r.status_code == 400 and "one account at a time" in r.get_json()["error"] and grammar.asked == [] and grammar.answers == []


def test_a_missing_or_foreign_account_is_refused_by_the_real_read(monkeypatch, store):
    monkeypatch.setattr(app_module, "_COPYCHECK_USES", {})
    monkeypatch.setattr(gads, "list_accounts", lambda: [T.ACCT])
    for body in ({}, {"account": "9999999999"}):
        r = client_as(STAFF).post(API + "/grammar", json=body)
        assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]


def test_a_user_gets_a_fixed_number_of_grammar_reads_a_day(grammar):
    for _ in range(app_module.COPYCHECK_GRAMMAR_DAILY_CAP):
        assert client_as(STAFF).post(API + "/grammar", json={"account": "1234567890"}).status_code == 200
    r = client_as(STAFF).post(API + "/grammar", json={"account": "1234567890"})
    assert r.status_code == 429 and "%d grammar reads" % app_module.COPYCHECK_GRAMMAR_DAILY_CAP in r.get_json()["error"]
    assert client_as("bo@position2.com").post(API + "/grammar", json={"account": "1234567890"}).status_code == 200                # the count is per person


def test_a_read_that_cannot_be_made_says_why_and_costs_no_turn(monkeypatch, store):
    from tracker import paid_copycheck_ai as ai
    monkeypatch.setattr(app_module, "_COPYCHECK_USES", {})
    monkeypatch.setattr(ai, "cached_proofread", lambda *a, **k: pytest.fail("the model must not be asked when the ads could not be read"))

    def bad(account, **k):
        raise cc.BadRequest("That account is not one of Position2's Google Ads accounts.")
    monkeypatch.setattr(cc, "load_raw", bad)
    r = client_as(STAFF).post(API + "/grammar", json={"account": "9999999999"})
    assert r.status_code == 400 and "not one of Position2" in r.get_json()["error"]

    def down(account, **k):
        raise gads.GadsError("quota", "slow down")
    monkeypatch.setattr(cc, "load_raw", down)
    r = client_as(STAFF).post(API + "/grammar", json={"account": "1234567890"})
    assert r.status_code == 429 and r.get_json()["kind"] == "quota" and app_module._COPYCHECK_USES == {}


def test_yesterdays_grammar_reads_do_not_count_today(grammar, monkeypatch):
    monkeypatch.setattr(app_module, "_COPYCHECK_USES", {STAFF: ("2000-01-01", 999)})
    assert client_as(STAFF).post(API + "/grammar", json={"account": "1234567890"}).status_code == 200
    day, used = app_module._COPYCHECK_USES[STAFF]
    assert day != "2000-01-01" and used == 1
