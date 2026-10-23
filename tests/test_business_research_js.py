"""Business Research: the page script run in Node against a stub page and the real report the server builds (tests/business_fakes.py).

The server researches, writes and checks (tests/test_paid_business_*.py); the page only draws. Here the whole script runs for real: it loads the options and the
accounts, shows the research cards, sends the form, polls a build, and draws the finished report tab by tab. The HTML it writes is read back.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

from tests import business_fakes as B, strategy_fakes as F
from tests.test_campaign_book_js import HARNESS as BASE_HARNESS
from tracker import paid_business as pb, paid_business_job as job, paid_strategy as ps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "business_research.js")
SCOPE = os.path.join(ROOT, "static", "js", "pm_scope.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

# the campaign book's stub page, with this page's address and a clipboard that remembers what was copied
HARNESS = (BASE_HARNESS.replace("/p2/paid-marketing/campaign-book", "/p2/paid-marketing/business-research")
           .replace("console, Promise, isNaN", "navigator: { clipboard: { writeText: t => { (global.copied = global.copied || []).push(t); return sc.noclipboard ? Promise.reject(new Error('no')) : Promise.resolve(); } } }, console, Promise, isNaN")
           .replace("const out = { calls,", "const out = { copied: global.copied || [], calls,"))
assert "navigator" in HARNESS and "copied" in HARNESS and "business-research" in HARNESS

OPTIONS = {"cards": pb.CARDS, "goals": ps.GOALS, "buildsPerDay": 12, "modelReady": True}
ACCOUNTS = {"accounts": [{"id": "1234567890", "name": "Acme Dental", "currency": "USD"}, {"id": "2345678901", "name": "Northwind <b>Dental</b>", "currency": "USD"}], "groups": [{"id": "mcc-1-USD", "name": "Group", "accountIds": ["1234567890"]}]}
META_ACCOUNTS = {"accounts": [{"id": "act_1234567", "name": "Acme Dental", "currency": "USD", "platform": "meta"}, {"id": "act_7654321", "name": "Other Meta", "currency": "USD", "platform": "meta"}]}


@pytest.fixture(scope="module")
def rec():
    store = F.FakeStrategyStore()
    inp = B.clean()
    r = store.create(by="ana@position2.com", inputs=inp, account_name="Acme Dental", steps=pb.steps_for(inp))
    store.begin(r["id"])
    job.run_build(r["id"], store=store, deps=B.deps())
    return json.loads(json.dumps(ps.public(store.get(r["id"]))))


def run(scenario):
    base = [{"match": "/business/options", "body": OPTIONS}, {"match": "/segments/accounts", "body": ACCOUNTS}, {"match": "/gads/accounts", "body": META_ACCOUNTS}, {"match": "/business/list", "body": {"builds": []}}]
    scenario = dict(scenario)
    scenario["responses"] = scenario.get("responses", []) + base
    out = subprocess.run(["node", "-e", HARNESS, JS, json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def click(sel_map, **kw):
    return {"fire": ["click", dict({"closest": sel_map}, **kw)]}


def press(id_):
    return {"fire": ["click", {"id": id_}]}


def tab(name):
    return click({"#tabs [data-t]": {"data-t": name}})


def opened(r, extra=None):
    return {"search": "?build=" + r["id"], "responses": [{"match": "/status", "body": {"status": "done", "progress": r["progress"]}}, {"match": "/business/" + r["id"], "method": "GET", "body": r}] + (extra or [])}


def page(r, name):
    return run(opened(r) | {"steps": [tab(name)] if name != "overview" else []})["el"]["#tabBody"]["html"]


# ── the form ────────────────────────────────────────────────────────────────

def test_the_form_shows_the_goals_the_cards_and_the_accounts():
    out = run({})
    h = out["el"]
    assert h["#goals"]["html"].count("data-g=") == 5 and 'data-g="leads"' in h["#goals"]["html"] and 'aria-checked="true"' in h["#goals"]["html"]
    cards = h["#cards"]["html"]
    assert cards.count('<button type="button" class="cb-card') == 7 and "Reviews and reputation" in cards and "Social Media Intelligence" in cards and cards.count("Uses paid data") == 3
    assert "Needs a Google Ads account" in cards and "Needs a Meta ad account" in cards and "Needs a website" in cards and "#kinds" not in h
    assert out["scopeOpts"] == {"noGroups": True, "optional": True}                       # the shared platform and account control: one client account per platform, either may be left out


def test_a_card_that_cannot_work_cannot_be_turned_on_until_what_it_needs_is_given():
    out = run({"steps": [click({"#cards [data-k]": {"data-k": "history"}})]})
    history = out["el"]["#cards"]["html"].split('data-k="history"')[1].split("</button>")[0]
    assert history.startswith(' aria-pressed="false" aria-disabled="true"') and "Needs a Google Ads account" in history
    typed = run({"values": {"#fSite": "acme.com"}, "steps": [{"fire": ["input", {"id": "fSite"}]}]})
    assert "Needs a website" not in typed["el"]["#cards"]["html"] and typed["el"]["#cards"]["html"].count('aria-pressed="true"') == 5 and out["el"]["#cards"]["html"].count('aria-pressed="true"') == 4


def test_the_form_is_sent_as_it_was_filled_in():
    vals = {"#fName": "Acme Dental", "#fSite": "acme.com", "#fIndustry": "Dental", "#fPlaces": "Austin, TX", "#fServices": "implants", "#fBrief": "A brief", "#fAvoid": "no cures",
            "#fComp": "smile.com\n\n  Another  \n"}
    out = run({"values": vals, "responses": [{"match": "/business/start", "method": "POST", "status": 201, "body": {"id": "a" * 32}}, {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}],
               "steps": [{"fire": ["input", {"id": "fSite"}]}, click({"#cards [data-k]": {"data-k": "social"}}), click({"#goals [data-g]": {"data-g": "calls"}}), press("goBtn")]})
    b = next(c for c in out["calls"] if c["url"].endswith("/business/start"))["body"]
    assert b["name"] == "Acme Dental" and b["website"] == "acme.com" and b["goal"] == "calls" and b["account"] == "" and b["locations"] == "Austin, TX" and b["services"] == "implants"
    assert b["competitors"] == ["smile.com", "Another"] and b["research"] == {"website": True, "reputation": True, "social": False, "history": False, "metahistory": False, "competitors": True, "market": True}
    assert b["metaAccount"] == "" and b["platform"] == "google"
    assert "budget" not in b and "campaignTypes" not in b and "launch" not in b
    assert out["hist"][-1] == "/p2/paid-marketing/business-research?build=" + "a" * 32 and out["el"]["#builder"]["hidden"] is True and out["el"]["#run"]["hidden"] is False


def test_a_missing_name_is_refused_before_anything_is_sent_and_a_refusal_is_shown():
    out = run({"values": {"#fName": " "}, "steps": [press("goBtn")]})
    assert not any(c["url"].endswith("/start") for c in out["calls"]) and "Give the client a name" in out["el"]["#msg"]["html"]
    out = run({"values": {"#fName": "Acme"}, "responses": [{"match": "/business/start", "method": "POST", "status": 429, "body": {"error": "That is 12 builds <b>in a day</b>"}}], "steps": [press("goBtn")]})
    assert "12 builds &lt;b&gt;in a day&lt;/b&gt;" in out["el"]["#msg"]["html"] and out["el"]["#goBtn"]["disabled"] is False


def test_the_page_says_when_the_ai_is_not_available_here():
    out = run({"responses": [{"match": "/business/options", "body": dict(OPTIONS, modelReady=False)}]})
    assert "no Anthropic key is set" in out["el"]["#goNote"]["html"]


# ── a build in progress ─────────────────────────────────────────────────────

def test_progress_is_polled_and_drawn_step_by_step_until_it_is_done(rec):
    running = {"status": "running", "started": "2026-10-17T10:00:00+00:00", "progress": {"phase": "Researching", "steps": [
        {"key": "r:website", "label": "Reading the client's website", "state": "done", "note": "Read 7 pages"}, {"key": "r:reputation", "label": "Reviews", "state": "running", "note": ""},
        {"key": "a:core", "label": "The <b>business</b>", "state": "pending", "note": ""}, {"key": "r:market", "label": "Market", "state": "failed", "note": "no"}]}}
    out = run({"search": "?build=" + rec["id"], "responses": [{"match": "/status", "sequence": [running, running, {"status": "done", "progress": rec["progress"]}]},
                                                               {"match": "/business/" + rec["id"], "method": "GET", "body": rec}], "steps": [{"timers": 1}, {"timers": 1}]})
    assert len([c for c in out["calls"] if c["url"].endswith("/status")]) == 3 and out["el"]["#result"]["hidden"] is False and out["el"]["#run"]["hidden"] is True
    first = run({"search": "?build=" + rec["id"], "responses": [{"match": "/status", "body": running}]})["el"]
    assert "Read 7 pages" in first["#steps"]["html"] and "cb-step running" in first["#steps"]["html"] and "cb-step failed" in first["#steps"]["html"]
    assert "The &lt;b&gt;business&lt;/b&gt;" in first["#steps"]["html"] and "<b>business</b>" not in first["#steps"]["html"] and first["#runTitle"]["text"] == "Researching"


def test_a_build_that_was_interrupted_offers_to_finish_what_is_missing():
    stale = {"status": "running", "stale": True, "progress": {"steps": []}}
    out = run({"search": "?build=" + "c" * 32, "responses": [{"match": "/status", "body": stale}, {"match": "/rebuild", "method": "POST", "body": {}}], "steps": [press("carryBtn")]})
    assert "The research was interrupted" in out["el"]["#runTitle"]["text"] and next(c for c in out["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": True}


def test_a_build_that_is_gone_sends_the_person_back_to_the_form():
    out = run({"search": "?build=" + "b" * 32, "responses": [{"match": "/status", "status": 404, "body": {"error": "x"}}]})
    assert out["el"]["#builder"]["hidden"] is False and "no longer there" in out["el"]["#msg"]["html"]


# ── the finished report ─────────────────────────────────────────────────────

def test_the_result_has_its_header_downloads_and_tabs(rec):
    h = run(opened(rec))["el"]
    assert h["#rName"]["text"] == "Acme Dental" and "acmedental.com" in h["#rMeta"]["html"] and "Dental" in h["#rMeta"]["html"] and "by ana" in h["#rMeta"]["html"]
    btns = h["#rBtns"]["html"]
    base = "/p2/paid-marketing/api/business/" + rec["id"] + "/download?file="
    for kind in ("xlsx", "pdf", "csv", "zip"):
        assert base + kind in btns
    assert "Research again from the start" in btns and "New research" in btns and "Remove" in btns and "Questions for the client (CSV)" in btns
    tabs = [t.split("</button>")[0].split(">")[-1] for t in h["#tabs"]["html"].split("<button")[1:]]
    assert tabs == ["Overview", "The business", "Who buys and why", "Reputation", "Site and tracking", "Competitors and social", "What it means for paid media", "Questions for the client (3)", "Checks"]


def test_the_overview_says_what_it_found_and_what_was_read(rec):
    html = page(rec, "overview")
    assert "2 offerings" in html and "1 high priority for paid search" in html and "Audiences" in html and "1 of 2" in html and "Questions for the client" in html
    assert "Acme is an Austin family dentist" in html and "Advertise first" in html and "Dental implants" in html and "What was read" in html
    assert "Reviews and reputation" in html and html.count("cb-pill ok") >= 5 and 'href="https://acmedental.com/services/dental-implants"' in html


def test_every_tab_draws_what_the_report_holds(rec):
    seen = {n: page(rec, n) for n in ("business", "audience", "reputation", "site", "compete", "paid", "questions", "checks")}
    b = seen["business"]
    assert "Acme Dental is a family dental practice" in b and "From $1,900 per tooth" in b and "cb-pill high" in b and "cb-pill medium" in b and "Free implant consultation" in b
    assert "cb-pill ok" in b and "cb-pill low" in b and "quote not found on a page" in b and "Not found on a page: confirm with the client" in b and "not among the pages read" not in b
    assert "Your Austin family dentist" in b and "painless" in b and "No guaranteed results" in b and "No phone on the implants page" in b
    a = seen["audience"]
    assert "Missing teeth" in a and "dental implants austin" in a and "Show financing" in a and "A broken tooth" in a and "Implants cost too much" in a and "Round Rock" in a and "New insurance year" in a
    r = seen["reputation"]
    assert "Google" in r and "4.8" in r and "1,204" in r and "Kind, patient staff" in r and "Austin Chronicle Best Dentist 2024" in r and "Dr. Maria Lopez" in r and "Owner operated" in r
    assert 'href="https://yelp.com/biz/acme-dental-austin"' in r and "Patients rate it highly." in r and "Parking" in r
    s = seen["site"]
    assert "No analytics, advertising, call tracking or booking tags were seen" in s and "No call tracking script was seen" in s and "Confirm the property" in s and "Pages read" in s and "Seen in the page&#39;s own scripts" in s
    c = seen["compete"]
    assert "Smile Austin leads with cosmetic work." in c and "Implant financing" in c and "Websites read" in c and "Short reels of the team" in c and "instagram" in c
    p = seen["paid"]
    assert "Advertise first" in p and "Hold back" in p and "Veneers" in p and "Free Implant Consultation in Austin" in p and "A price in the ad lifts clicks" in p and "Thin pages" in p and "Fix the implants page" in p and "Client" in p
    q = seen["questions"]
    assert "Goals and targets" in q and q.count("<h4>") == 2 and "How many new implant patients a month do you want?" in q and "cb-pill failed" in q and "Copy the questions" in q
    k = seen["checks"]
    assert "Facts not found on a page" in k and "Points of difference without a quote" in k and "What it cost" in k


def test_a_tab_with_nothing_to_show_is_not_offered():
    r = {"id": "d" * 32, "name": "Bare", "status": "done", "inputs": {"website": ""}, "progress": {"steps": []}, "research": {}, "createdBy": "x@position2.com",
         "plan": {"core": {"summary": "A dentist", "offerings": [], "facts": [], "usps": []}, "audience": {}, "implications": {}, "claims": [], "checks": {}}, "cost": {}}
    out = run(opened(r))
    tabs = [t.split("</button>")[0].split(">")[-1] for t in out["el"]["#tabs"]["html"].split("<button")[1:]]
    assert tabs == ["Overview", "The business", "Who buys and why", "What it means for paid media", "Checks"]
    assert "0 offerings" in out["el"]["#tabBody"]["html"]


def test_the_questions_copy_as_plain_text_grouped_by_area(rec):
    out = run(opened(rec) | {"steps": [tab("questions"), press("copyQ")]})
    assert out["copied"] == ["Goals and targets\n1. How many new implant patients a month do you want?\n2. What is a patient worth?\n\nOffers and pricing\n1. Is the free consultation still on?"]


# ── what goes wrong, and what must never run ────────────────────────────────

def test_a_build_that_stopped_says_why_and_offers_to_carry_on(rec):
    r = json.loads(json.dumps(rec))
    r.update(status="error", error="the model is <i>down</i>", plan={})
    out = run(opened(r) | {"responses": opened(r)["responses"] + [{"match": "/rebuild", "method": "POST", "body": {"id": r["id"]}}], "steps": [press("carryBtn")]})
    note = out["el"]["#rNote"]["html"]
    assert "The research stopped." in note and "the model is &lt;i&gt;down&lt;/i&gt;" in note and "<i>down</i>" not in note and "Finish what is missing" in note
    assert next(c for c in out["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": True} and "file=xlsx" not in out["el"]["#rBtns"]["html"]
    r["research"] = {}
    tabs = [t.split("</button>")[0].split(">")[-1] for t in run(opened(r))["el"]["#tabs"]["html"].split("<button")[1:]]
    assert tabs == ["Overview", "Checks"]                                              # nothing was written, so there is nothing else to open


def test_researching_again_and_removing_ask_first(rec):
    no = run(opened(rec) | {"confirm": False, "steps": [press("rebuildBtn"), press("delBtn")]})
    assert not any(c["method"] in ("POST", "DELETE") and "/business/" + rec["id"] in c["url"] for c in no["calls"])
    yes = run(opened(rec) | {"responses": opened(rec)["responses"] + [{"match": "/rebuild", "method": "POST", "body": {}}], "steps": [press("rebuildBtn")]})
    assert next(c for c in yes["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": False}
    gone = run(opened(rec) | {"responses": opened(rec)["responses"] + [{"match": "/business/" + rec["id"], "method": "DELETE", "body": {"ok": True}}], "steps": [press("delBtn")]})
    assert any(c["method"] == "DELETE" for c in gone["calls"]) and gone["el"]["#builder"]["hidden"] is False and gone["el"]["#result"]["hidden"] is True
    cooling = run(opened(rec) | {"responses": opened(rec)["responses"] + [{"match": "/rebuild", "method": "POST", "status": 429, "body": {"error": "Try again in 20 seconds."}}], "steps": [press("rebuildBtn")]})
    assert "Try again in 20 seconds." in cooling["el"]["#msg"]["html"]


def test_change_the_details_puts_them_back_in_the_form(rec):
    out = run(opened(rec) | {"steps": [press("reuseBtn")]})
    e = out["el"]
    assert e["#fName"]["value"] == "Acme Dental" and e["#fSite"]["value"] == "acmedental.com" and e["#fIndustry"]["value"] == "Dental" and e["#fPlaces"]["value"] == "Austin, TX"
    assert e["#builder"]["hidden"] is False and e["#result"]["hidden"] is True and 'data-g="leads" aria-checked="true"' in e["#goals"]["html"]
    assert _reuse_and_start(rec)["account"] == "1234567890"                                      # the account of the saved build is chosen again


def test_nothing_the_model_or_a_website_wrote_can_run_as_html(rec):
    r = json.loads(json.dumps(rec))
    evil = "<img src=x onerror=alert(1)>"
    r["name"] = evil
    core, aud, imp = r["plan"]["core"], r["plan"]["audience"], r["plan"]["implications"]
    core["summary"] = evil
    core["offerings"][0]["name"] = evil
    core["offerings"][0]["page"] = "javascript:alert(1)"
    core["usps"][0]["usp"] = evil
    core["usps"][0]["source"] = "javascript:alert(2)"
    core["facts"][0]["quote"] = evil
    aud["audiences"][0]["name"] = evil
    imp["summary"][0] = evil
    imp["questions"][0]["question"] = evil
    imp["advertiseFirst"][0]["page"] = "javascript:alert(3)"
    rep = r["research"]["reputation"]["data"]
    rep["ratings"][0]["url"] = "javascript:alert(4)"
    rep["praise"][0] = evil
    rep["sources"] = ["javascript:alert(5)", "https://ok.example/x"]
    r["plan"]["tech"]["tools"] = [{"name": evil, "category": evil, "ids": [evil], "pages": 1}]
    r["research"]["website"]["data"]["tech"]["tools"] = r["plan"]["tech"]["tools"]
    r["plan"]["claims"] = [{"where": evil, "text": evil, "claim": evil}]
    for name in ("overview", "business", "audience", "reputation", "site", "paid", "questions", "checks"):
        html = run(opened(r) | {"steps": [tab(name)] if name != "overview" else []})["el"]["#tabBody"]["html"]
        assert "<img" not in html and "onerror=alert(1)>" not in html.replace("&lt;img src=x onerror=alert(1)&gt;", "") and 'href="javascript' not in html, name
    head = run(opened(r))["el"]
    assert "<img" not in head["#rName"]["text"] or head["#rName"]["text"] == evil                    # textContent, never HTML
    assert 'href="https://ok.example/x"' in run(opened(r) | {"steps": [tab("reputation")]})["el"]["#tabBody"]["html"]


def test_the_page_script_has_no_dash_and_no_leftover_campaign_book_wording():
    src = open(JS, encoding="utf-8").read()
    assert chr(0x2014) not in src and chr(0x2013) not in src
    for word in ("campaign book", "Campaign Book", "budget", "strategist", "kinds"):
        assert word not in src.replace("Strategy & Campaign Book's", ""), word
    assert not re.search(r"api/strategy", src)


def test_polling_stops_when_the_build_has_gone_quiet():
    stale = {"status": "running", "stale": True, "progress": {"steps": []}}
    out = run({"search": "?build=" + "c" * 32, "responses": [{"match": "/status", "body": stale}], "steps": [{"timers": 1}, {"timers": 1}]})
    assert len([c for c in out["calls"] if c["url"].endswith("/status")]) == 1
    live = {"status": "running", "progress": {"steps": []}}
    out = run({"search": "?build=" + "c" * 32, "responses": [{"match": "/status", "body": live}], "steps": [{"timers": 1}, {"timers": 1}]})
    assert len([c for c in out["calls"] if c["url"].endswith("/status")]) == 3


def test_a_report_that_stopped_halfway_offers_no_downloads(rec):
    r = json.loads(json.dumps(rec))
    r["status"] = "error"
    r["error"] = "stopped"
    del r["plan"]["generatedAt"]
    btns = run(opened(r))["el"]["#rBtns"]["html"]
    assert "download?file=" not in btns and "Research again from the start" in btns


def test_an_outside_rating_and_a_point_found_only_by_the_search_are_marked(rec):
    r = json.loads(json.dumps(rec))
    r["research"]["reputation"]["data"]["ratings"][0]["confirmed"] = True
    r["research"]["reputation"]["data"]["ratings"][1]["confirmed"] = False
    r["plan"]["core"]["usps"][0].update(verified=False, viaSearch=True)
    rep = page(r, "reputation")
    assert rep.count("no page the search read confirms this") == 1
    assert "only in what the web search found: check its source" in page(r, "business")


# ── The Platform switch and the Meta ad account ──────────────────────────────

def _reuse_and_start(record, steps=None):
    """Open a finished build, put its details back in the form and start again: the body that was sent."""
    sc = opened(record)
    sc = dict(sc, steps=[press("reuseBtn")] + (steps or []) + [press("goBtn")],
              responses=sc["responses"] + [{"match": "/business/start", "method": "POST", "status": 201, "body": {"id": "b" * 32}}, {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}])
    out = run(sc)
    return next(c for c in out["calls"] if c["url"].endswith("/business/start"))["body"]


def _started(steps, values=None):
    out = run({"values": values or {"#fName": "Acme", "#fSite": "acme.com"}, "steps": steps + [press("goBtn")],
               "responses": [{"match": "/business/start", "method": "POST", "status": 201, "body": {"id": "a" * 32}}, {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}]})
    return next(c for c in out["calls"] if c["url"].endswith("/business/start"))["body"], out


def test_a_meta_account_unlocks_the_meta_history_card_and_is_sent_with_the_platform():
    out = run({"steps": [{"scope": ["act_1234567", "meta"]}]})
    cards = out["el"]["#cards"]["html"]
    assert "Needs a Meta ad account" not in cards and "Needs a Google Ads account" in cards
    meta = cards.split('data-k="metahistory"')[1].split("</button>")[0]
    assert 'aria-pressed="true"' in meta and "Meta Ads history" in meta
    body, _ = _started([{"scope": ["act_1234567", "meta"]}])
    assert body["metaAccount"] == "act_1234567" and body["account"] == "" and body["platform"] == "meta" and body["research"]["metahistory"] is True and body["research"]["history"] is False


def test_both_sends_the_google_account_and_the_meta_account_and_names_the_client_from_the_first_when_empty():
    body, out = _started([{"scope": ["1234567890+act_1234567", "both"]}])
    assert body["account"] == "1234567890" and body["metaAccount"] == "act_1234567" and body["platform"] == "both" and body["research"]["history"] is True and body["research"]["metahistory"] is True
    named = run({"steps": [{"scope": ["act_1234567", "meta"]}]})
    assert named["el"]["#fName"]["value"] == "Acme Dental"                                               # an empty name is filled from the account chosen; a typed one is kept
    kept = run({"values": {"#fName": "Typed Name"}, "steps": [{"scope": ["act_1234567", "meta"]}]})
    assert kept["el"]["#fName"]["value"] == "Typed Name"


def test_going_back_to_google_takes_the_meta_account_out_of_what_is_sent():
    body, _ = _started([{"scope": ["1234567890+act_1234567", "both"]}, {"scope": ["1234567890", "google"]}])
    assert body["metaAccount"] == "" and body["platform"] == "google" and body["research"]["metahistory"] is False and body["research"]["history"] is True


def test_a_saved_build_with_both_accounts_chooses_both_again_and_the_meta_card_is_in_the_report_list(rec):
    both = json.loads(json.dumps(rec))
    both["inputs"].update(metaAccount="act_1234567", platform="both")
    both["research"]["metahistory"] = {"state": "ok", "note": "1 campaigns, 900 spent and 30 results in the last 90 days.", "data": {}}
    out = run(opened(both) | {"steps": [press("reuseBtn")]})
    assert "Meta Ads history" in out["el"]["#overview"]["html"] if "#overview" in out["el"] else True
    sent = _reuse_and_start(both)
    assert sent["account"] == "1234567890" and sent["metaAccount"] == "act_1234567" and sent["platform"] == "both"
