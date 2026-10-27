"""Competitor Analysis: the routes and the page (app.py, templates/competitor_analysis.html). No network."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from monitor_fakes import MemStore  # noqa: E402
from test_paid_compete import Runner, own_raw_ads, raw_of  # noqa: E402
from test_paid_compete_math import pool_with_price  # noqa: E402
from tracker import paid_compete_fetch as cf  # noqa: E402
from tracker import paid_compete_settings as cs  # noqa: E402

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/compete"
PAGE = "/p2/paid-marketing/competitor-analysis"
G, MA = "1234567890", "act_1234567"


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
    monkeypatch.setattr(app_module, "_mon_spawn", lambda fn: fn())                                  # a refresh runs inline
    return s


@pytest.fixture(autouse=True)
def own(monkeypatch):
    from tracker import paid_copycheck
    monkeypatch.setattr(paid_copycheck, "load_raw", lambda account_id, **kw: (raw_of("meta" if account_id.startswith("act_") else "google", own_raw_ads("meta" if account_id.startswith("act_") else "google")), False,
                                                                               {"id": account_id, "name": "Acme"}))


def save(c, account=G, text="Tandem | tandemdiabetes.com | TandemDiabetes", **kw):
    return c.post(API + "/settings", json=dict({"account": account, "competitors": text, "region": "us", "names": {G: "Acme", MA: "Acme Meta"}}, **kw))


def test_the_page_is_for_signed_in_staff_only():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Competitor Analysis" in html and 'id="fComp"' in html and 'id="refreshBtn"' in html and "js/competitor_analysis_data.js" in html and "js/competitor_analysis.js" in html
    for url in (PAGE, API + "/report?account=" + G, API + "/job?account=" + G):
        assert client_as(None).get(url).status_code == 302 and client_as("someone@gmail.com").get(url).status_code == 302, url
    for url in ("/settings", "/refresh"):
        assert client_as(None).post(API + url, json={}).status_code == 302 and client_as("someone@gmail.com").post(API + url, json={}).status_code == 302, url


def test_the_report_of_an_account_with_no_competitor_says_so_and_carries_the_box_and_the_senders(store, monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    r = client_as(STAFF).get(API + "/report?account=" + G)
    j = r.get_json()
    assert r.status_code == 200 and j["status"] == "no_competitors" and j["competitorsText"] == "" and j["senders"] == {"apify": False} and j["platforms"] == ["google"] and j["own"] == {"google": {"ads": 4}}
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    assert client_as(STAFF).get(API + "/report?account=" + G).get_json()["senders"] == {"apify": True}


def test_both_accounts_of_a_client_are_read_together_and_a_bad_account_is_refused(store):
    j = client_as(STAFF).get(API + "/report?account=%s%%2B%s" % (G, MA)).get_json()
    assert j["platforms"] == ["meta", "google"] and j["accounts"] == {"google": G, "meta": MA} and j["key"] == "google:%s+meta:%s" % (G, MA)
    for q, msg in (("", "Pick a Google Ads account"), ("?account=abc", "Pick one Google Ads account"), ("?account=act_x", "Pick one Meta ad account")):
        r = client_as(STAFF).get(API + "/report" + q)
        assert r.status_code == 400 and msg in r.get_json()["error"], q


def test_saving_writes_the_list_for_every_account_in_the_scope_with_who_and_it_comes_back_in_the_report(store):
    c = client_as(STAFF)
    r = save(c, account="%s+%s" % (G, MA))
    assert r.status_code == 200 and set(r.get_json()["saved"]) == {"google", "meta"} and r.get_json()["saved"]["meta"]["competitors"][0]["facebook"] == "https://www.facebook.com/TandemDiabetes"
    st = cs.load(store)
    assert set(st["accounts"]) == {G, MA} and st["accounts"][G]["name"] == "Acme" and st["accounts"][MA]["name"] == "Acme Meta" and st["changes"][0]["by"] == STAFF and st["accounts"][G]["region"] == "US"
    j = c.get(API + "/report?account=" + G).get_json()
    assert j["competitorsText"] == "Tandem | tandemdiabetes.com | https://www.facebook.com/TandemDiabetes" and j["competitors"][0]["name"] == "Tandem" and j["estimate"]["reads"] == 1
    assert save(c, text="").status_code == 200 and cs.load(store)["accounts"] == {MA: cs.load(store)["accounts"][MA]}


@pytest.mark.parametrize("body,msg", [({"text": "A"}, "nowhere to look"), ({"text": "A | a.com | https://example.com/x"}, "not a Facebook page"), ({"region": "USA"}, "two letters"), ({"account": ""}, "Pick a Google Ads account")])
def test_a_list_that_is_not_valid_is_refused_in_words_and_nothing_is_saved(store, body, msg):
    kw = {"competitors": body["text"]} if "text" in body else {k: v for k, v in body.items()}
    r = client_as(STAFF).post(API + "/settings", json=dict({"account": G, "competitors": "A | a.com", "region": "US"}, **kw))
    assert r.status_code == 400 and msg in r.get_json()["error"] and store.state_get(cs.KEY) is None


def test_a_refresh_needs_a_token_a_competitor_and_no_refresh_already_running(store, monkeypatch):
    c = client_as(STAFF)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    r = c.post(API + "/refresh", json={"account": G})
    assert r.status_code == 400 and "APIFY_API_TOKEN is not set" in r.get_json()["error"]
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    r = c.post(API + "/refresh", json={"account": G})
    assert r.status_code == 400 and r.get_json()["error"] == "Save at least one competitor first."
    save(c)
    store.state_set("lock:compete:google:%s" % G, {"locked": True})
    r = c.post(API + "/refresh", json={"account": G})
    assert r.status_code == 409 and "already running" in r.get_json()["error"]


def test_a_refresh_starts_the_read_in_the_background_and_the_job_and_report_show_what_it_read(store, monkeypatch):
    from tracker import apify_transport
    runner = Runner(meta=[], google=[__import__("test_paid_compete_math").GOOGLE_REAL])
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", lambda actor, run_input, token, timeout=300, poll_interval=5, strict=False: runner(actor, run_input, token, timeout))
    c = client_as(STAFF)
    save(c, text="Tandem | tandemdiabetes.com")
    r = c.post(API + "/refresh", json={"account": G})
    assert r.status_code == 202 and r.get_json()["started"] is True and r.get_json()["estimate"] == {"reads": 1, "ads": 30, "usd": 0.05}
    assert runner.calls[0]["actor"] == "silva95gustavo/google-ads-scraper" and runner.calls[0]["token"] == "tok" and runner.calls[0]["input"]["startUrls"][0]["url"].endswith("domain=tandemdiabetes.com")
    job = c.get(API + "/job?account=" + G).get_json()["job"]
    assert job["status"] == "done" and job["by"] == STAFF and job["steps"][0]["ads"] == 1
    j = c.get(API + "/report?account=" + G).get_json()
    assert j["status"] == "ok" and j["counts"]["competitorAds"] == 1 and j["job"]["status"] == "done"
    again = c.post(API + "/refresh", json={"account": G})
    assert again.status_code == 202 and len(runner.calls) == 1                                       # read lately: not read, not paid for again
    forced = c.post(API + "/refresh", json={"account": G, "force": True})
    assert forced.status_code == 202 and len(runner.calls) == 2


def test_a_refresh_can_name_the_competitors_to_read(store, monkeypatch):
    from tracker import apify_transport
    runner = Runner(google=[])
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", lambda actor, run_input, token, timeout=300, poll_interval=5, strict=False: runner(actor, run_input, token, timeout))
    c = client_as(STAFF)
    save(c, text="Tandem | tandemdiabetes.com\nOther | other.example")
    r = c.post(API + "/refresh", json={"account": G, "only": ["Other"]})
    assert r.get_json()["estimate"]["reads"] == 1 and len(runner.calls) == 1 and "domain=other.example" in runner.calls[0]["input"]["startUrls"][0]["url"]


def test_a_store_that_is_down_is_said_in_words(store, monkeypatch):
    class Down(Exception):
        pass
    store.StoreUnavailable = Down

    def boom(key, default=None):
        raise Down("The database is down.")
    monkeypatch.setattr(store, "state_get", boom)
    for r in (client_as(STAFF).get(API + "/report?account=" + G), client_as(STAFF).get(API + "/job?account=" + G), save(client_as(STAFF))):
        assert r.status_code in (502, 503) and "database is down" in r.get_json()["error"]


def test_a_failure_reading_our_ads_is_answered_in_words_not_raised(store, monkeypatch):
    from tracker import paid_copycheck, paid_gads_segments as paid_gads
    for kind, status in (("not_configured", 503), ("quota", 429), ("auth", 502)):
        monkeypatch.setattr(paid_copycheck, "load_raw", lambda *a, _k=kind, **k: (_ for _ in ()).throw(paid_gads.GadsError(_k, "Google said %s." % _k)))
        r = client_as(STAFF).get(API + "/report?account=" + G)
        assert r.status_code == status and r.get_json() == {"error": "Google said %s." % kind, "kind": kind}
    monkeypatch.setattr(paid_copycheck, "load_raw", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("something odd")))
    r = client_as(STAFF).get(API + "/report?account=" + G)
    assert r.status_code == 400 and r.get_json()["error"] == "The ads of 1234567890 could not be read (something odd)."


def test_the_agent_is_on_the_paid_marketing_page_the_palette_the_hub_counts_and_the_assistant_knows_it(store):
    from tests import hidden_paid
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/competitor-analysis"' in paid and "c-ca" in paid and "Competitor Analysis" in paid
    assert "u:'/p2/paid-marketing/competitor-analysis'" in paid and "u:'/p2/paid-marketing/competitor-analysis'" in client_as(STAFF).get("/p2/paid-marketing/pacing-desk").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-ca{" in css and ".ca-bar{" in css and ".ca-idea{" in css
    v = {re.search(r"paid_marketing\.css'\) \}\}\?v=(\d+)", open(os.path.join(ROOT, "templates", x), encoding="utf-8").read()).group(1) for x in ("_paid_base.html", "paid_marketing.html")}
    assert len(v) == 1, v
    cards = len(re.findall(r'class="dash-card', paid))
    hub_html = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub_html and ("<span>%d</span> live" % cards) in hub_html and "read against yours with ideas to test" in hub_html
    kn = app_module._VIMI_PLATFORM_KNOWLEDGE
    mine = kn.split("Competitor Analysis (", 1)[1].split("\n", 1)[0]
    assert "Competitor Analysis (/p2/paid-marketing/competitor-analysis)" in kn and "Ads Transparency Center" in mine and "never automatic" in mine and "—" not in mine and "–" not in mine
    card = paid.split('href="/p2/paid-marketing/competitor-analysis" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card
    assert "—" not in open(os.path.join(ROOT, "templates", "competitor_analysis.html"), encoding="utf-8").read()
    entry = [l for l in open(os.path.join(ROOT, "templates", "_paid_kpal.html"), encoding="utf-8").read().splitlines() if "competitor-analysis" in l]
    assert len(entry) == 1 and "—" not in entry[0] and "–" not in entry[0]
