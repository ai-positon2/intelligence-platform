"""Keyword Research: the page script run in Node against a stub page and the real result the server builds (tests/keyword_fakes.py).

The server researches, matches, judges and checks (tests/test_paid_keywords_*.py); the page only draws and filters. Here the whole script runs for real: it loads the
options and the accounts, shows the research cards, sends the form, polls a build, and draws the finished result tab by tab, with the keyword table filtered, sorted
and paged. The HTML it writes is read back.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from tests import keyword_fakes as K, strategy_fakes as F
from tests.test_campaign_book_js import HARNESS as BASE_HARNESS
from tracker import paid_keywords as pk, paid_keywords_job as job, paid_strategy as ps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "keyword_research.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

HARNESS = (BASE_HARNESS.replace("/p2/paid-marketing/campaign-book", "/p2/paid-marketing/keyword-research")
           .replace("console, Promise, isNaN", "navigator: { clipboard: { writeText: t => { (global.copied = global.copied || []).push(t); return sc.noclipboard ? Promise.reject(new Error('no')) : Promise.resolve(); } } }, console, Promise, isNaN")
           .replace("const out = { calls,", "const out = { copied: global.copied || [], calls,")
           .replace("JSON.parse(process.argv[2])", "JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))"))      # a long list is a scenario too big for one argument on Linux
assert "navigator" in HARNESS and "copied" in HARNESS and "keyword-research" in HARNESS and "readFileSync(process.argv[2]" in HARNESS

OPTIONS = {"cards": pk.CARDS, "goals": ps.GOALS, "minSearches": [10, 50, 100, 500], "defaultMin": 10, "buildsPerDay": 12, "modelReady": True}
ACCOUNTS = {"accounts": [{"id": "1234567890", "name": "Acme Dental", "currency": "USD"}, {"id": "2345678901", "name": "Northwind <b>Dental</b>", "currency": "USD"}], "groups": []}
API = "/p2/paid-marketing/api/keywords"


@pytest.fixture(scope="module")
def rec():
    store = F.FakeStrategyStore()
    inp = K.clean()
    r = store.create(by="ana@position2.com", inputs=inp, account_name="Acme Dental", steps=pk.steps_for(inp))
    store.begin(r["id"])
    job.run_build(r["id"], store=store, deps=K.deps())
    return json.loads(json.dumps(ps.public(store.get(r["id"]))))


def run(scenario):
    base = [{"match": "/keywords/options", "body": OPTIONS}, {"match": "/segments/accounts", "body": ACCOUNTS}, {"match": "/keywords/list", "body": {"builds": []}}]
    scenario = dict(scenario)
    scenario["responses"] = scenario.get("responses", []) + base
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(scenario, f)
    try:
        out = subprocess.run(["node", "-e", HARNESS, JS, f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def click(sel_map, **kw):
    return {"fire": ["click", dict({"closest": sel_map}, **kw)]}


def press(id_):
    return {"fire": ["click", {"id": id_}]}


def tab(name):
    return click({"#tabs [data-t]": {"data-t": name}})


def view(name):
    return click({".kr-views [data-kv]": {"data-kv": name}})


def sort(key):
    return click({"#kwTable [data-sort]": {"data-sort": key}})


def typed(sel, value, event="input"):
    return [{"set": [sel, value]}, {"fire": [event, {"id": sel[1:]}]}]


def opened(r, extra=None):
    return {"search": "?build=" + r["id"], "responses": [{"match": "/status", "body": {"status": "done", "progress": r["progress"]}}, {"match": "/keywords/" + r["id"], "method": "GET", "body": r}] + (extra or [])}


def page(r, name, steps=None):
    return run(opened(r) | {"steps": ([tab(name)] if name != "overview" else []) + (steps or [])})["el"]["#tabBody"]["html"]


def table(r, steps=None, view_="add"):
    out = run(opened(r) | {"steps": [tab("keywords")] + ([view(view_)] if view_ != "add" else []) + (steps or [])})
    return out["el"]["#kwTable"]["html"], out["el"]["#kwCount"]["text"], out["el"]["#tabBody"]["html"]


def kws(html):
    return re.findall(r'<span class="kr-kw">([^<]*)</span>', html)


def tabs_of(out):
    return [t.split("</button>")[0].split(">")[-1] for t in out["el"]["#tabs"]["html"].split("<button")[1:]]


# ── the form ────────────────────────────────────────────────────────────────

def test_the_form_shows_the_goals_the_cards_the_floors_and_the_accounts():
    out = run({})
    h = out["el"]
    assert h["#goals"]["html"].count("data-g=") == 5 and 'data-g="leads"' in h["#goals"]["html"] and 'aria-checked="true"' in h["#goals"]["html"]
    cards = h["#cards"]["html"]
    assert cards.count('<button type="button" class="cb-card') == 4 and "Business Research of this client" in cards and "Account keywords and search terms" in cards and "Uses paid data" not in cards
    assert "Needs a Google Ads account" in cards and "Needs a website" in cards
    assert h["#fMin"]["html"].count("<option") == 4 and '<option value="10" selected>10 a month</option>' in h["#fMin"]["html"] and "500 a month" in h["#fMin"]["html"]
    assert [i["label"] for i in out["picker"][-1][0]] == ["No account", "Acme Dental", "Northwind <b>Dental</b>"]


def test_a_card_that_cannot_work_cannot_be_turned_on_until_what_it_needs_is_given():
    out = run({"steps": [click({"#cards [data-k]": {"data-k": "account"}})]})
    account = out["el"]["#cards"]["html"].split('data-k="account"')[1].split("</button>")[0]
    assert account.startswith(' aria-pressed="false" aria-disabled="true"') and "Needs a Google Ads account" in account
    typed_ = run({"values": {"#fSite": "acme.com"}, "steps": [{"fire": ["input", {"id": "fSite"}]}]})
    assert "Needs a website" not in typed_["el"]["#cards"]["html"] and typed_["el"]["#cards"]["html"].count('aria-pressed="true"') == 3 and out["el"]["#cards"]["html"].count('aria-pressed="true"') == 2


def test_the_form_is_sent_as_it_was_filled_in():
    vals = {"#fName": "Acme Dental", "#fSite": "acme.com", "#fIndustry": "Dental", "#fPlaces": "Austin, TX", "#fServices": "implants", "#fBrief": "A brief", "#fAvoid": "dentures", "#fMin": "100",
            "#fComp": "smile.com\n\n  Another  \n"}
    out = run({"values": vals, "responses": [{"match": "/keywords/start", "method": "POST", "status": 201, "body": {"id": "a" * 32}}, {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}],
               "steps": [{"fire": ["input", {"id": "fSite"}]}, click({"#cards [data-k]": {"data-k": "business"}}), click({"#goals [data-g]": {"data-g": "calls"}}), press("goBtn")]})
    b = next(c for c in out["calls"] if c["url"].endswith("/keywords/start"))["body"]
    assert b["name"] == "Acme Dental" and b["website"] == "acme.com" and b["goal"] == "calls" and b["account"] == "" and b["locations"] == "Austin, TX" and b["services"] == "implants" and b["avoid"] == "dentures"
    assert b["minSearches"] == 100 and b["competitors"] == ["smile.com", "Another"] and b["research"] == {"business": False, "website": True, "account": False, "competitors": True}
    assert "budget" not in b and "campaignTypes" not in b and "launch" not in b
    assert out["hist"][-1] == "/p2/paid-marketing/keyword-research?build=" + "a" * 32 and out["el"]["#builder"]["hidden"] is True and out["el"]["#run"]["hidden"] is False


def test_the_smallest_search_volume_falls_back_to_the_default_when_the_field_is_empty():
    out = run({"values": {"#fName": "Acme"}, "responses": [{"match": "/keywords/start", "method": "POST", "status": 201, "body": {"id": "a" * 32}}, {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}], "steps": [press("goBtn")]})
    assert next(c for c in out["calls"] if c["url"].endswith("/keywords/start"))["body"]["minSearches"] == 10


def test_a_missing_name_is_refused_before_anything_is_sent_and_a_refusal_is_shown():
    out = run({"values": {"#fName": " "}, "steps": [press("goBtn")]})
    assert not any(c["url"].endswith("/start") for c in out["calls"]) and "Give the client a name" in out["el"]["#msg"]["html"]
    out = run({"values": {"#fName": "Acme"}, "responses": [{"match": "/keywords/start", "method": "POST", "status": 429, "body": {"error": "That is 12 builds <b>in a day</b>"}}], "steps": [press("goBtn")]})
    assert "12 builds &lt;b&gt;in a day&lt;/b&gt;" in out["el"]["#msg"]["html"] and out["el"]["#goBtn"]["disabled"] is False


def test_the_page_says_when_the_ai_is_not_available_here():
    out = run({"responses": [{"match": "/keywords/options", "body": dict(OPTIONS, modelReady=False)}]})
    assert "no Anthropic key is set" in out["el"]["#goNote"]["html"]


# ── a build in progress ─────────────────────────────────────────────────────

def test_progress_is_polled_and_drawn_step_by_step_until_it_is_done(rec):
    running = {"status": "running", "started": "2026-10-17T10:00:00+00:00", "progress": {"phase": "Writing", "steps": [
        {"key": "r:website", "label": "Reading the client's website", "state": "done", "note": "Read 7 pages"}, {"key": "r:planner", "label": "Keyword Planner", "state": "running", "note": ""},
        {"key": "a:classify", "label": "The <b>judging</b>", "state": "running", "note": "2 of 4 groups judged"}, {"key": "r:account", "label": "Account", "state": "failed", "note": "no"}]}}
    out = run({"search": "?build=" + rec["id"], "responses": [{"match": "/status", "sequence": [running, running, {"status": "done", "progress": rec["progress"]}]},
                                                               {"match": "/keywords/" + rec["id"], "method": "GET", "body": rec}], "steps": [{"timers": 1}, {"timers": 1}]})
    assert len([c for c in out["calls"] if c["url"].endswith("/status")]) == 3 and out["el"]["#result"]["hidden"] is False and out["el"]["#run"]["hidden"] is True
    first = run({"search": "?build=" + rec["id"], "responses": [{"match": "/status", "body": running}]})["el"]
    assert "Read 7 pages" in first["#steps"]["html"] and "2 of 4 groups judged" in first["#steps"]["html"] and "cb-step running" in first["#steps"]["html"] and "cb-step failed" in first["#steps"]["html"]
    assert "The &lt;b&gt;judging&lt;/b&gt;" in first["#steps"]["html"] and "<b>judging</b>" not in first["#steps"]["html"] and first["#runTitle"]["text"] == "Researching"


def test_a_build_that_was_interrupted_offers_to_finish_what_is_missing():
    stale = {"status": "running", "stale": True, "progress": {"steps": []}}
    out = run({"search": "?build=" + "c" * 32, "responses": [{"match": "/status", "body": stale}, {"match": "/rebuild", "method": "POST", "body": {}}], "steps": [press("carryBtn")]})
    assert "The research was interrupted" in out["el"]["#runTitle"]["text"] and next(c for c in out["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": True}


def test_a_build_that_is_gone_sends_the_person_back_to_the_form():
    out = run({"search": "?build=" + "b" * 32, "responses": [{"match": "/status", "status": 404, "body": {"error": "x"}}]})
    assert out["el"]["#builder"]["hidden"] is False and "no longer there" in out["el"]["#msg"]["html"]


# ── the finished result ─────────────────────────────────────────────────────

def test_the_result_has_its_header_downloads_and_tabs(rec):
    out = run(opened(rec))
    h = out["el"]
    assert h["#rName"]["text"] == "Acme Dental" and "acmedental.com" in h["#rMeta"]["html"] and "by ana" in h["#rMeta"]["html"]
    btns = h["#rBtns"]["html"]
    base = API + "/" + rec["id"] + "/download?file="
    for kind in ("xlsx", "pdf", "csv", "zip"):
        assert base + kind in btns
    assert "Keywords to add, for Google Ads Editor (CSV)" in btns and "Research again from the start" in btns and "New research" in btns and "Remove" in btns
    assert tabs_of(out) == ["Overview", "Themes (6)", "Keywords (23)", "What the account has", "Competitors", "Through the year", "Negatives (3)", "Plan", "Questions for the client (2)", "Checks"]


def test_without_anything_to_add_the_editor_download_is_not_offered(rec):
    r = json.loads(json.dumps(rec))
    r["plan"]["editor"] = []
    btns = run(opened(r))["el"]["#rBtns"]["html"]
    assert "Google Ads Editor" not in btns and "file=zip" in btns


def test_the_overview_says_what_was_found_and_what_was_read(rec):
    html = page(rec, "overview")
    c = rec["plan"]["coverage"]
    assert "Keywords judged" in html and "%d a fit" % c["yes"] in html and "To add to the account" in html and "Searches not yet covered" in html and "%d%%" % c["poolPct"] in html and "of the searches for ideas that share a word with what it sells" in html and "Negatives to add" in html
    assert "The market is mostly implants and Invisalign" in html and "Add the converting search term all on 4 implants austin tx as exact" in html
    assert "What was read" in html and "Business Research of this client" in html and "Used the Business Research of 2026-10-10." in html and html.count("cb-pill ok") >= 5
    assert "What Keyword Planner was asked" in html and "dental implants" in html and "rival: smileaustin.com" in html and "26 different keywords came back; 23 were judged" in html


def test_the_themes_tab_shows_each_theme_with_its_totals_and_campaign(rec):
    html = page(rec, "themes")
    assert "Covered (of those judged)" in html and "Dental Implants" in html and "People ready to replace missing teeth" in html and "P2_Search_Implants_20250101" in html and "already in the account" in html and "P2_Search_EmergencyDentist" in html
    assert "Peaks in May" in html and "Searches a month (fit)" in html and "kr-num" in html and html.count("cb-pill") >= 6 and "emergency dentist near me · 9,900" in html


def test_the_keywords_tab_starts_on_the_ones_to_add(rec):
    html, count, body = table(rec)
    assert count == "4 keywords" and sorted(kws(html)) == ["acme dental austin", "all on 4 implants austin tx", "emergency dentist near me", "implant dentist near me"]
    chips = re.findall(r'<button type="button" role="radio" data-kv="(\w+)" class="([^"]*)" aria-checked="(\w+)">([^<]*)</button>', body)
    assert chips == [("add", "on", "true", "To add (4)"), ("have", "", "false", "A fit, already covered (%d)" % sum(1 for r in rec["plan"]["keywords"] if r["fit"] == "yes" and r["status"] != "new")),
                     ("all", "", "false", "Everything judged (23)")]
    assert "Already a keyword" not in html and 'cb-pill ok">New' in html and "a search term that converted 4 times" in html and '<span class="cb-c kw phrase">phrase</span>' in html and "kr-num" in html


def test_the_views_switch_between_to_add_covered_and_everything(rec):
    have, count, _ = table(rec, view_="have")
    assert "dental implants austin" in kws(have) and "cost of dental implants" in kws(have) and "emergency dentist near me" not in kws(have) and "Already a keyword" in have and "Already matched" in have
    assert count == "%d keywords" % len(kws(have))
    every, count, _ = table(rec, view_="all")
    assert count == "23 keywords" and len(kws(every)) == 23 and "dentist dallas" in kws(every) and "blocked in some campaigns by \"dallas\"" in every and "Not a fit" in every and "Maybe" in every


def test_the_keywords_can_be_searched_by_part_of_a_word(rec):
    html, count, _ = table(rec, typed("#kwQ", "IMPLANT"), view_="all")
    assert count == "%d keywords" % len(kws(html)) and kws(html) and all("implant" in k for k in kws(html)) and "invisalign cost" not in kws(html)
    none, count, _ = table(rec, typed("#kwQ", "zzz"), view_="all")
    assert count == "0 keywords" and "No keyword matches." in none and kws(none) == []


def test_the_keywords_can_be_filtered_by_theme_intent_and_priority(rec):
    html, count, _ = table(rec, typed("#kwTheme", "emergency", "change"), view_="all")
    assert set(kws(html)) == {"emergency dentist near me", "emergency dentist austin"}
    html, _, _ = table(rec, typed("#kwIntent", "competitor", "change"), view_="all")
    assert set(kws(html)) == {"smile austin dentistry", "smile austin veneers"}
    html, _, _ = table(rec, typed("#kwPri", "high", "change"), view_="all")
    assert kws(html) and "emergency dentist austin" in kws(html) and "invisalign cost" not in kws(html)
    both, _, _ = table(rec, typed("#kwTheme", "implants", "change") + typed("#kwPri", "high", "change") + typed("#kwQ", "austin"), view_="all")
    assert set(kws(both)) <= {"dental implants austin", "dental implants cost austin", "all on 4 dental implants austin", "all on 4 implants austin tx"} and "dental implants austin" in kws(both)


def test_the_keywords_are_sorted_by_searches_and_the_sort_can_be_turned_round(rec):
    html, _, _ = table(rec, view_="all")
    ks = kws(html)
    by = {r["kw"]: r["avg"] or 0 for r in rec["plan"]["keywords"]}
    assert [by[k] for k in ks] == sorted((by[k] for k in ks), reverse=True)
    up, _, _ = table(rec, [sort("avg")], view_="all")
    assert [by[k] for k in kws(up)] == sorted(by[k] for k in kws(up))
    az, _, _ = table(rec, [sort("kw")], view_="all")
    assert kws(az) == sorted(kws(az))
    za, _, _ = table(rec, [sort("kw"), sort("kw")], view_="all")
    assert kws(za) == sorted(kws(za), reverse=True)
    pri, _, _ = table(rec, [sort("priority")], view_="all")
    order = {r["kw"]: {"high": 3, "medium": 2, "low": 1}[r["priority"]] for r in rec["plan"]["keywords"]}
    assert [order[k] for k in kws(pri)] == sorted((order[k] for k in kws(pri)), reverse=True)
    bid, _, _ = table(rec, [sort("lo")], view_="all")
    lo = {r["kw"]: r["lo"] if r["lo"] is not None else -1 for r in rec["plan"]["keywords"]}
    assert [lo[k] for k in kws(bid)] == sorted((lo[k] for k in kws(bid)), reverse=True)


def test_a_long_list_is_shown_a_hundred_at_a_time(rec):
    big = json.loads(json.dumps(rec))
    base = next(r for r in big["plan"]["keywords"] if r["include"])
    big["plan"]["keywords"] = [dict(base, kw="added keyword %03d" % i, avg=1000 - i) for i in range(250)]
    html, count, _ = table(big)
    assert count == "250 keywords, the first 100 shown" and len(kws(html)) == 100 and "Show 100 more" in html and kws(html)[0] == "added keyword 000"
    more, count, _ = table(big, [press("kwMore")])
    assert count == "250 keywords, the first 200 shown" and len(kws(more)) == 200
    last, count, _ = table(big, [press("kwMore"), press("kwMore")])
    assert count == "250 keywords" and len(kws(last)) == 250 and "kwMore" not in last
    refilter, count, _ = table(big, [press("kwMore")] + typed("#kwQ", "added keyword 00"))
    assert count == "10 keywords" and len(kws(refilter)) == 10
    again, count, _ = table(big, [press("kwMore")] + typed("#kwQ", "added"))                      # a new filter starts from the first page again, however many were shown
    assert count == "250 keywords, the first 100 shown" and len(kws(again)) == 100
    other, count, _ = table(big, [press("kwMore")] + typed("#kwTheme", "implants", "change"))
    assert len(kws(other)) <= 100


def test_a_keyword_with_a_close_variant_a_rival_and_a_search_term_says_so(rec):
    r = json.loads(json.dumps(rec))
    k = next(x for x in r["plan"]["keywords"] if x["include"])
    k.update(vars=3, rival="Smile Austin", conv={"conv": 2.5, "clicks": 9, "cost": 10, "campaign": "C"})
    html, _, _ = table(r)
    assert "and 2 close variants" in html and "names a competitor: Smile Austin" in html and "a search term that converted 2.5 times" in html


def test_the_account_tab_says_what_the_account_has(rec):
    html = page(rec, "account")
    assert "Keywords" in html and "Negative keywords" in html and "Shared negative lists" in html and "P2_Search_Implants_20250101" in html
    assert "Search terms that converted and are not keywords yet" in html and "all on 4 implants austin tx" in html and "Search terms that cost money and did not convert" in html and "dentist jobs austin" in html
    assert "The Search Query Analysis agent judges these in full" in html


def test_the_competitors_tab_lists_the_sites_and_the_keywords_that_name_a_rival(rec):
    html = page(rec, "compete")
    assert "Websites read" in html and "smileaustin.com" in html and "Keywords that name a competitor" in html and "smile austin dentistry" in html and "client&#39;s decision" in html


def test_the_year_tab_shades_each_month_by_its_size(rec):
    html = page(rec, "season")
    assert "Searches a month, by theme" in html and "Jan 2026" in html and "Dec 2026" in html and "Invisalign" in html and "Peaks in May" in html
    cells = re.findall(r'<td class="kr-m" style="background:rgba\(56,189,248,([0-9.]+)\)">([\d,]+)</td>', html)
    assert cells and all(0.05 <= float(a) <= 0.56 for a, _ in cells) and "kr-heat" in html
    row = [(float(a), int(n.replace(",", ""))) for a, n in cells[:12]]
    assert max(row)[0] == max(a for a, _ in row) and (max(row, key=lambda x: x[1])[0] == max(a for a, _ in row))


def test_the_negatives_tab_lists_them_and_copies_them_the_way_google_ads_writes_them(rec):
    html = page(rec, "negatives")
    assert "dental assistant" in html and "Not dental" in html and "Suggested and left out" in html and "It would block the keyword" in html and "Copy as Google Ads negatives" in html
    out = run(opened(rec) | {"steps": [tab("negatives"), press("copyNeg")]})
    assert out["copied"] == ['"dental assistant"\n"how long"\n[gem]']


def test_the_plan_tab_has_the_first_moves_tests_seasons_and_risks(rec):
    html = page(rec, "plan")
    assert "Exact match on implants lowers cost per lead" in html and "Raise the Invisalign budget before May" in html and "Implant terms are broad" in html and "Start with exact match" in html and "Each one is a guess to test" in html


def test_the_questions_copy_as_plain_text_grouped_by_area(rec):
    out = run(opened(rec) | {"steps": [tab("questions"), press("copyQ")]})
    assert out["copied"] == ["Brand terms and competitors\n1. May we bid on competitor names?\n\nGeneral\n1. Which places do you serve?"]
    html = page(rec, "questions")
    assert "Brand terms and competitors" in html and "cb-pill failed" in html and html.count("<h4>") == 2 and "Copy the questions" in html


def test_the_checks_tab_says_what_was_left_out_and_what_it_cost(rec):
    html = page(rec, "checks")
    assert "What was left out before judging" in html and "Ideas blocked in every campaign by the account&#39;s negatives" in html and "Ideas merged with a close variant" in html and "Blocked in some campaigns" in html
    assert "Assumptions" in html and "Implants earn the most per patient" in html
    r = json.loads(json.dumps(rec))
    r["plan"]["checks"].update(unjudged=2, convertingNotFit=["cheap tooth pulling"], emptySeeds=["insulin pump"])
    r["cost"] = {"stages": [{}], "model": 0.4, "research": 0.1, "usd": 0.5, "calls": 5}
    h = page(r, "checks")
    assert "Keywords not judged" in h and "2 keywords could not be judged" in h and "Converting search terms judged not a fit" in h and "cheap tooth pulling" in h and "Keyword Planner had no ideas for" in h and "insulin pump" in h
    assert "Judging: $0.4 in 5 calls. Paid research: $0.1. Total: $0.5." in h


def test_a_tab_with_nothing_to_show_is_not_offered():
    r = {"id": "d" * 32, "name": "Bare", "status": "done", "inputs": {"website": ""}, "progress": {"steps": []}, "research": {}, "createdBy": "x@position2.com",
         "plan": {"keywords": [], "themes": [], "coverage": {}, "plan": {}, "checks": {}, "editor": [], "generatedAt": "2026-10-17T00:00:00+00:00"}, "cost": {}}
    out = run(opened(r))
    assert tabs_of(out) == ["Overview", "Checks"] and "Google Ads Editor" not in out["el"]["#rBtns"]["html"]
    assert "n/a" in out["el"]["#tabBody"]["html"]


# ── what goes wrong, and what must never run ────────────────────────────────

def test_a_build_that_stopped_says_why_and_offers_to_carry_on(rec):
    r = json.loads(json.dumps(rec))
    r.update(status="error", error="the model is <i>down</i>", plan={})
    out = run(opened(r) | {"responses": opened(r)["responses"] + [{"match": "/rebuild", "method": "POST", "body": {"id": r["id"]}}], "steps": [press("carryBtn")]})
    note = out["el"]["#rNote"]["html"]
    assert "The research stopped." in note and "the model is &lt;i&gt;down&lt;/i&gt;" in note and "<i>down</i>" not in note and "Finish what is missing" in note
    assert next(c for c in out["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": True} and "file=xlsx" not in out["el"]["#rBtns"]["html"]


def test_researching_again_and_removing_ask_first(rec):
    no = run(opened(rec) | {"confirm": False, "steps": [press("rebuildBtn"), press("delBtn")]})
    assert not any(c["method"] in ("POST", "DELETE") and "/keywords/" + rec["id"] in c["url"] for c in no["calls"])
    yes = run(opened(rec) | {"responses": opened(rec)["responses"] + [{"match": "/rebuild", "method": "POST", "body": {}}], "steps": [press("rebuildBtn")]})
    assert next(c for c in yes["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": False}
    gone = run(opened(rec) | {"responses": opened(rec)["responses"] + [{"match": "/keywords/" + rec["id"], "method": "DELETE", "body": {"ok": True}}], "steps": [press("delBtn")]})
    assert any(c["method"] == "DELETE" for c in gone["calls"]) and gone["el"]["#builder"]["hidden"] is False and gone["el"]["#result"]["hidden"] is True
    cooling = run(opened(rec) | {"responses": opened(rec)["responses"] + [{"match": "/rebuild", "method": "POST", "status": 429, "body": {"error": "Try again in 20 seconds."}}], "steps": [press("rebuildBtn")]})
    assert "Try again in 20 seconds." in cooling["el"]["#msg"]["html"]


def test_change_the_details_puts_the_goal_back_too(rec):
    r = json.loads(json.dumps(rec))
    r["inputs"]["goal"] = "calls"
    out = run(opened(r) | {"steps": [press("reuseBtn")]})
    assert 'data-g="calls" aria-checked="true"' in out["el"]["#goals"]["html"] and 'data-g="leads" aria-checked="false"' in out["el"]["#goals"]["html"]


def test_change_the_details_puts_them_back_in_the_form(rec):
    out = run(opened(rec) | {"steps": [press("reuseBtn")]})
    e = out["el"]
    assert e["#fName"]["value"] == "Acme Dental" and e["#fSite"]["value"] == "acmedental.com" and e["#fIndustry"]["value"] == "Dental" and e["#fPlaces"]["value"] == "Austin, TX" and e["#fMin"]["value"] == "10"
    assert e["#fComp"]["value"] == "https://smileaustin.com/" and e["#builder"]["hidden"] is False and e["#result"]["hidden"] is True and out["picker"][-1][1] == "1234567890" and 'data-g="leads" aria-checked="true"' in e["#goals"]["html"]


def test_nothing_the_model_or_planner_or_a_website_wrote_can_run_as_html(rec):
    r = json.loads(json.dumps(rec))
    evil = "<img src=x onerror=alert(1)>"
    r["name"] = evil
    for k in r["plan"]["keywords"][:8]:
        k.update(kw=evil, note=evil, pattern=evil, rival=evil, comp=evil, intent="<b>x</b>", by={"kw": evil, "match": evil, "campaign": evil}, conv={"conv": 1, "clicks": 1, "cost": 1, "campaign": evil})
    for t in r["plan"]["themes"]:
        t.update(name=evil, why=evil, campaign=evil, pattern=evil, top=[{"kw": evil, "avg": 1}])
    w = r["plan"]["plan"]
    w["summary"][0] = evil
    w["quickWins"][0]["what"] = evil
    w["negatives"][0].update(kw=evil, why=evil)
    w["questions"][0].update(question=evil, area=evil)
    w["risks"][0]["risk"] = evil
    r["plan"]["checks"].update(emptySeeds=[evil], convertingNotFit=[evil], missing=[evil], assumptions=[evil], removedNegatives=[{"kw": evil, "match": evil, "reason": evil}])
    r["plan"]["seasonal"]["rows"][0][0] = evil
    r["research"]["account"]["data"]["campaigns"][0]["name"] = evil
    r["research"]["competitors"]["data"]["competitors"][0].update(name=evil, url="javascript:alert(2)")
    for name in ("overview", "themes", "account", "compete", "season", "negatives", "plan", "questions", "checks"):
        html = run(opened(r) | {"steps": [tab(name)] if name != "overview" else []})["el"]["#tabBody"]["html"]
        assert "<img" not in html and 'href="javascript' not in html, name
    html, _, _ = table(r, view_="all")
    assert "<img" not in html and "<b>x</b>" not in html
    assert "<img" not in run(opened(r))["el"]["#rName"]["html"]


def test_the_page_script_has_no_dash_and_no_leftover_wording_from_other_agents():
    src = open(JS, encoding="utf-8").read()
    assert chr(0x2014) not in src and chr(0x2013) not in src
    for word in ("campaign book", "strategist", "api/strategy", "api/business", "reputation"):
        assert word not in src.replace("Strategy & Campaign Book's", ""), word


def test_polling_stops_when_the_build_has_gone_quiet():
    stale = {"status": "running", "stale": True, "progress": {"steps": []}}
    out = run({"search": "?build=" + "c" * 32, "responses": [{"match": "/status", "body": stale}], "steps": [{"timers": 1}, {"timers": 1}]})
    assert len([c for c in out["calls"] if c["url"].endswith("/status")]) == 1
    live = {"status": "running", "progress": {"steps": []}}
    out = run({"search": "?build=" + "c" * 32, "responses": [{"match": "/status", "body": live}], "steps": [{"timers": 1}, {"timers": 1}]})
    assert len([c for c in out["calls"] if c["url"].endswith("/status")]) == 3


def test_a_result_that_stopped_halfway_offers_no_downloads(rec):
    r = json.loads(json.dumps(rec))
    r["status"] = "error"
    r["error"] = "stopped"
    del r["plan"]["generatedAt"]
    btns = run(opened(r))["el"]["#rBtns"]["html"]
    assert "download?file=" not in btns and "Research again from the start" in btns


def test_opening_another_build_starts_the_keyword_table_afresh(rec):
    other = json.loads(json.dumps(rec))
    other["id"] = "e" * 32
    out = run({"search": "?build=" + rec["id"], "responses": [{"match": "/status", "body": {"status": "done", "progress": rec["progress"]}}, {"match": "/keywords/" + rec["id"], "method": "GET", "body": rec},
                                                               {"match": "/keywords/" + other["id"], "method": "GET", "body": other}, {"match": "/keywords/list", "body": {"builds": [{"id": other["id"], "name": "Other", "status": "done", "createdBy": "a@b.c", "createdAt": None}]}}],
               "steps": [tab("keywords"), view("all"), press("backBtn"), click({".cb-open": {"data-id": other["id"]}}), tab("keywords")]})
    assert "To add (4)" in out["el"]["#tabBody"]["html"] and out["el"]["#kwCount"]["text"] == "4 keywords"


# ── what the independent review found ───────────────────────────────────────

def test_the_overview_says_where_searches_were_counted(rec):
    html = page(rec, "overview")
    assert '<p class="cb-fine">Searches are counted in Austin,Texas,United States.</p>' in html and rec["research"]["planner"]["data"]["geoNote"] in html


def test_the_checks_tab_says_what_was_withheld_what_was_not_available_and_every_number_to_check(rec):
    r = json.loads(json.dumps(rec))
    r["plan"]["checks"].update(editorWithheld="The account's keywords could not be <b>read</b>.", accountWarnings=["The account has more keywords than were read."], caveats=["Account-level negatives are not available."],
                               numbers=[{"where": "In short", "text": "That brings 450 leads", "number": "450"}])
    html = page(r, "checks")
    assert "Where searches were counted" in html and "The Google Ads Editor file was not made" in html and "could not be &lt;b&gt;read&lt;/b&gt;" in html and "<b>read</b>" not in html
    assert "The account has more keywords than were read." in html and "Not available from Google Ads" in html and "Account-level negatives are not available." in html
    assert "Numbers in the write-up that are not in the research" in html and "That brings 450 leads" in html and "450" in html and "The AI was not asked for any number" in html


def test_a_keyword_blocked_in_the_campaign_it_would_go_in_says_so(rec):
    r = json.loads(json.dumps(rec))
    k = next(x for x in r["plan"]["keywords"] if x["kw"] == "dentist dallas")
    k["blockedInTarget"] = True
    html, _, _ = table(r, view_="all")
    assert 'blocked in some campaigns by "dallas", including the one it would go in' in html
