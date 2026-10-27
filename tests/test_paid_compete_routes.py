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


def collect(runner):
    """The Apify transport's run_actor_collect (what a run saved, even if it was stopped) answering from a stand-in actor."""
    def run(actor, run_input, token, timeout=240, poll_interval=5, memory_mb=None):
        return {"items": runner(actor, run_input, token, timeout), "status": "SUCCEEDED", "error": None}
    return run


def save(c, account=G, text="Tandem | tandemdiabetes.com | TandemDiabetes", **kw):
    return c.post(API + "/settings", json=dict({"account": account, "competitors": text, "region": "us", "names": {G: "Acme", MA: "Acme Meta"}}, **kw))


def test_the_page_is_for_signed_in_staff_only():
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Competitor Analysis" in html and 'id="caQ"' in html and 'id="caSearch"' in html and 'id="refreshBtn"' in html and 'id="fComp"' not in html and "js/competitor_analysis_data.js" in html and "js/competitor_analysis.js" in html
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
    monkeypatch.setattr(apify_transport, "run_actor_collect", collect(runner))
    c = client_as(STAFF)
    save(c, text="Tandem | tandemdiabetes.com")
    r = c.post(API + "/refresh", json={"account": G})
    assert r.status_code == 202 and r.get_json()["started"] is True and r.get_json()["estimate"] == {"reads": 1, "ads": 150, "usd": 0.24, "minutes": 28}
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
    monkeypatch.setattr(apify_transport, "run_actor_collect", collect(runner))
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


# ── the picker: search by name, add one at a time, kept for each ad account ─────────────────────────────

def fake_companies(monkeypatch, *orgs):
    from tracker import paid_compete_search as se
    monkeypatch.setattr(se, "_apollo", lambda q, key: list(orgs))
    monkeypatch.setenv("APOLLO_API_KEY", "k")


TANDEM_ORG = {"name": "Tandem Diabetes Care", "primary_domain": "tandemdiabetes.com", "logo_url": "https://logo.example/t.png", "industry": "medical devices", "facebook_url": "https://www.facebook.com/TandemDiabetes"}
INSULET_ORG = {"name": "Insulet", "primary_domain": "insulet.com", "industry": "medical devices"}


def add(c, org, account=G, **kw):
    comp = {"name": org["name"], "domain": org.get("primary_domain", ""), "facebook": org.get("facebook_url", ""), "fbSource": "apollo" if org.get("facebook_url") else "", "logo": org.get("logo_url", ""), "industry": org.get("industry", "")}
    return c.post(API + "/competitors", json=dict({"account": account, "op": "add", "competitor": comp, "names": {G: "Acme", MA: "Acme Meta"}}, **kw))


def test_the_search_and_the_changes_are_for_signed_in_staff_only():
    for method, url in (("get", API + "/search?account=%s&q=tandem" % G), ("post", API + "/competitors"), ("post", API + "/find-facebook")):
        for who in (None, "someone@gmail.com"):
            r = getattr(client_as(who), method)(url, **({"json": {}} if method == "post" else {}))
            assert r.status_code == 302, (url, who)


def test_a_name_typed_gives_the_companies_with_that_name_and_marks_those_already_added(store, monkeypatch):
    fake_companies(monkeypatch, TANDEM_ORG, INSULET_ORG)
    c = client_as(STAFF)
    j = c.get(API + "/search?account=%s&q=tandem" % G).get_json()
    assert [x["name"] for x in j["companies"]] == ["Tandem Diabetes Care", "Insulet"] and j["error"] is None and not any(x["added"] for x in j["companies"])
    assert j["companies"][0]["facebook"] == "https://www.facebook.com/TandemDiabetes" and j["companies"][0]["domain"] == "tandemdiabetes.com"
    add(c, TANDEM_ORG)
    j = c.get(API + "/search?account=%s&q=tandem" % G).get_json()
    assert {x["name"]: x["added"] for x in j["companies"]} == {"Tandem Diabetes Care": True, "Insulet": False}
    other = c.get(API + "/search?account=%s&q=tandem" % "2345678901").get_json()
    assert not any(x["added"] for x in other["companies"])                                          # another ad account has its own competitors
    assert c.get(API + "/search?account=%s&q=t" % G).get_json() == {"companies": [], "error": None}
    assert c.get(API + "/search?q=tandem").status_code == 400


def test_a_search_that_fails_is_answered_with_the_reason_not_as_no_company(store, monkeypatch):
    import requests
    from tracker import paid_compete_search as se
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(se, "_apollo", lambda q, key: (_ for _ in ()).throw(requests.Timeout("slow")))
    r = client_as(STAFF).get(API + "/search?account=%s&q=tandem" % G)
    assert r.status_code == 200 and r.get_json()["companies"] == [] and r.get_json()["error"]["kind"] == "timeout" and r.get_json()["error"]["retryable"] is True


def test_competitors_are_added_one_by_one_and_each_is_saved_for_that_ad_account(store, monkeypatch):
    c = client_as(STAFF)
    r = add(c, TANDEM_ORG)
    assert r.status_code == 200 and [x["name"] for x in r.get_json()["competitors"]] == ["Tandem Diabetes Care"] and r.get_json()["region"] == "US"
    r = add(c, INSULET_ORG)
    assert [x["name"] for x in r.get_json()["competitors"]] == ["Tandem Diabetes Care", "Insulet"]
    saved = cs.load(store)
    assert [x["name"] for x in saved["accounts"][G]["competitors"]] == ["Tandem Diabetes Care", "Insulet"] and saved["accounts"][G]["name"] == "Acme" and saved["changes"][0]["by"] == STAFF
    assert saved["accounts"][G]["competitors"][0]["logo"] == "https://logo.example/t.png" and saved["accounts"][G]["competitors"][0]["fbSource"] == "apollo"
    j = c.get(API + "/report?account=" + G).get_json()
    assert [x["name"] for x in j["competitors"]] == ["Tandem Diabetes Care", "Insulet"] and j["estimate"]["reads"] == 2                  # one Google read each; only Tandem has a page for Meta


def test_the_list_of_one_ad_account_is_not_the_list_of_another(store):
    c = client_as(STAFF)
    add(c, TANDEM_ORG, account=G)
    add(c, INSULET_ORG, account="2345678901")
    add(c, INSULET_ORG, account=MA)
    names = lambda acct: [x["name"] for x in c.get(API + "/report?account=" + acct).get_json()["competitors"]]            # noqa: E731
    assert names(G) == ["Tandem Diabetes Care"] and names("2345678901") == ["Insulet"] and names(MA) == ["Insulet"] and names("3456789012") == []
    c.post(API + "/competitors", json={"account": G, "op": "remove", "competitor": {"name": "Tandem Diabetes Care"}})
    assert names(G) == [] and names("2345678901") == ["Insulet"]


def test_a_client_with_a_google_and_a_meta_account_gets_the_change_on_both_and_one_that_applies_to_one_is_passed_over(store):
    c = client_as(STAFF)
    both = "%s+%s" % (G, MA)
    add(c, INSULET_ORG, account=MA)
    r = add(c, INSULET_ORG, account=both)
    assert r.status_code == 200                                                                      # the Meta account already had it: passed over, the Google account got it
    assert [x["name"] for x in cs.load(store)["accounts"][G]["competitors"]] == ["Insulet"] and [x["name"] for x in cs.load(store)["accounts"][MA]["competitors"]] == ["Insulet"]
    r = add(c, INSULET_ORG, account=both)
    assert r.status_code == 400 and "already in this account" in r.get_json()["error"]               # on neither: refused
    r = c.post(API + "/competitors", json={"account": both, "op": "remove", "competitor": {"name": "Insulet"}})
    assert r.status_code == 200 and r.get_json()["competitors"] == [] and cs.load(store)["accounts"] == {}


@pytest.mark.parametrize("body,msg", [({"op": "add", "competitor": {"name": "A"}}, "nowhere to look"), ({"op": "add", "competitor": {"domain": "a.com"}}, "Give each competitor a name"), ({"op": "remove", "competitor": {"name": "Ghost"}}, "not one of this"),
                                      ({"op": "explode", "competitor": {"name": "A", "domain": "a.com"}}, "Unknown change"), ({"op": "region", "region": "USA"}, "two letters"), ({"op": "add", "account": "", "competitor": {"name": "A", "domain": "a.com"}}, "Pick a Google Ads account")])
def test_a_change_that_is_not_valid_is_refused_in_words_and_saves_nothing(store, body, msg):
    r = client_as(STAFF).post(API + "/competitors", json=dict({"account": G}, **body))
    assert r.status_code == 400 and msg in r.get_json()["error"] and store.state_get(cs.KEY) is None


def test_the_country_is_changed_for_the_account_and_comes_back_in_the_report(store):
    c = client_as(STAFF)
    add(c, TANDEM_ORG)
    r = c.post(API + "/competitors", json={"account": G, "op": "region", "region": "gb"})
    assert r.status_code == 200 and r.get_json()["region"] == "GB" and c.get(API + "/report?account=" + G).get_json()["region"] == "GB"


def test_a_change_made_while_another_is_being_made_waits_then_says_so(store, monkeypatch):
    from tracker import paid_compete
    monkeypatch.setattr(paid_compete.time, "sleep", lambda s: None)
    store.locks.add(paid_compete.SETTINGS_LOCK)
    r = add(client_as(STAFF), TANDEM_ORG)
    assert r.status_code == 400 and "being changed by someone else" in r.get_json()["error"] and store.state_get(cs.KEY) is None
    store.locks.discard(paid_compete.SETTINGS_LOCK)
    assert add(client_as(STAFF), TANDEM_ORG).status_code == 200 and paid_compete.SETTINGS_LOCK not in store.locks            # the lock is let go of


def test_the_facebook_page_is_looked_for_by_name_and_website_and_kept_when_the_answer_is_sure(store, monkeypatch):
    from tracker import paid_compete_search as se
    asked = []

    def ask(name, domain):
        asked.append((name, domain))
        return {"profile_url": "https://www.facebook.com/InsuletCorp", "page_name": "Insulet", "confidence": "high", "reasoning": "linked from insulet.com"}
    monkeypatch.setattr(se, "ask_claude", ask)
    c = client_as(STAFF)
    add(c, INSULET_ORG)
    r = c.post(API + "/find-facebook", json={"account": G, "name": "insulet"})
    j = r.get_json()
    assert r.status_code == 200 and j["kept"] is True and j["facebook"] == "https://www.facebook.com/InsuletCorp" and j["pageName"] == "Insulet" and asked == [("Insulet", "insulet.com")]
    mine = cs.load(store)["accounts"][G]["competitors"][0]
    assert mine["facebook"] == "https://www.facebook.com/InsuletCorp" and mine["fbSource"] == "search" and mine["domain"] == "insulet.com" and j["competitors"][0]["facebook"] == mine["facebook"]
    again = c.post(API + "/find-facebook", json={"account": G, "name": "Insulet"}).get_json()
    assert again["kept"] is False and again["reasoning"] == "A Facebook page is already set." and len(asked) == 1                    # a page already set is not searched for again


def test_a_page_the_answer_is_not_sure_of_is_not_kept_and_the_reason_is_given(store, monkeypatch):
    from tracker import paid_compete_search as se
    monkeypatch.setattr(se, "ask_claude", lambda n, d: {"profile_url": "https://www.facebook.com/FanPage", "confidence": "low", "reasoning": "only a fan page was found"})
    c = client_as(STAFF)
    add(c, INSULET_ORG)
    j = c.post(API + "/find-facebook", json={"account": G, "name": "Insulet"}).get_json()
    assert j["kept"] is False and j["facebook"] == "" and j["reasoning"] == "only a fan page was found" and cs.load(store)["accounts"][G]["competitors"][0]["facebook"] == ""
    assert c.post(API + "/find-facebook", json={"account": G, "name": "Nobody"}).status_code == 400


def test_a_page_typed_by_the_team_is_kept_as_typed_and_is_not_searched_over(store, monkeypatch):
    from tracker import paid_compete_search as se
    monkeypatch.setattr(se, "ask_claude", lambda n, d: (_ for _ in ()).throw(AssertionError("searched")))
    c = client_as(STAFF)
    add(c, INSULET_ORG)
    r = c.post(API + "/competitors", json={"account": G, "op": "update", "competitor": {"name": "Insulet", "domain": "insulet.com", "facebook": "Omnipod", "fbSource": "manual"}})
    assert r.status_code == 200 and r.get_json()["competitors"][0]["facebook"] == "https://www.facebook.com/Omnipod"
    assert c.post(API + "/find-facebook", json={"account": G, "name": "Insulet"}).get_json()["facebook"] == "https://www.facebook.com/Omnipod"
    assert cs.load(store)["accounts"][G]["competitors"][0]["fbSource"] == "manual"


def test_a_refresh_keeps_what_a_stopped_run_saved_and_says_so(store, monkeypatch):
    from tracker import apify_transport
    from test_paid_compete_math import GOOGLE_REAL
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_collect", lambda actor, run_input, token, timeout=240, poll_interval=5, memory_mb=None: {"items": [GOOGLE_REAL], "status": "ABORTED", "error": "Apify run r1 did not finish within 1500s"})
    c = client_as(STAFF)
    add(c, {"name": "Tandem", "primary_domain": "tandemdiabetes.com"})
    assert c.post(API + "/refresh", json={"account": G}).status_code == 202
    job = c.get(API + "/job?account=" + G).get_json()["job"]
    assert job["steps"][0]["ads"] == 1 and "stopped before it finished" in job["steps"][0]["partial"]
    j = c.get(API + "/report?account=" + G).get_json()
    assert j["analysis"]["google"]["competitors"][0]["partial"] and any("stopped before it finished" in x for x in j["caveats"])
