"""Ad Copy and Creative Briefs: the page script run in Node against a stub page and the real result the server builds (tests/adcopy_fakes.py).

The server writes, limits, rules and checks (tests/test_paid_adcopy_*.py); the page only draws and copies. Here the whole script runs for real: it loads the options and the
accounts, shows the formats, angles and research cards, sends the form, polls a build, and draws the finished result tab by tab. The HTML it writes is read back, and the
text it copies to the clipboard is compared with the lines the server kept.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

from tests import adcopy_fakes as A, strategy_fakes as F
from tests.test_campaign_book_js import HARNESS as BASE_HARNESS
from tracker import paid_adcopy as pa, paid_adcopy_check as ck, paid_adcopy_job as job, paid_strategy as ps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "ad_copy.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

HARNESS = (BASE_HARNESS.replace("/p2/paid-marketing/campaign-book", "/p2/paid-marketing/ad-copy")
           .replace("console, Promise, isNaN", "navigator: { clipboard: { writeText: t => { (global.copied = global.copied || []).push(t); return sc.noclipboard ? Promise.reject(new Error('no')) : Promise.resolve(); } } }, console, Promise, isNaN")
           .replace("const out = { calls,", "const out = { copied: global.copied || [], calls,"))
HARNESS = HARNESS.replace('value: (sc.values || {})[sel] || "",', 'value: (sc.values || {})[sel] || "", checked: (sc.checked || {})[sel],')
assert "navigator" in HARNESS and "copied" in HARNESS and "ad-copy" in HARNESS and "sc.checked" in HARNESS

FORMATS = [{"key": k, "label": f["label"], "short": f["short"], "platform": f["platform"], "about": f["about"], "brief": f["brief"], "paths": f["paths"], "cta": f["cta"],
            "fields": [{x: fld[x] for x in ("key", "label", "limit", "min", "max", "optional")} for fld in f["fields"]]} for k, f in pa.FORMATS.items()]
OPTIONS = {"formats": FORMATS, "defaultFormats": pa.DEFAULT_FORMATS, "angles": pa.ANGLES, "defaultAngles": pa.DEFAULT_ANGLES, "maxAngles": 5, "tones": pa.TONES, "defaultTone": "plain",
           "cards": pa.CARDS, "goals": ps.GOALS, "reasons": ck.REASONS, "buildsPerDay": 12, "modelReady": True}
ACCOUNTS = {"accounts": [{"id": "1234567890", "name": "Acme Dental", "currency": "USD"}, {"id": "2345678901", "name": "Northwind <b>Dental</b>", "currency": "USD"}], "groups": []}
API = "/p2/paid-marketing/api/adcopy"


def build(make=None, model=None):
    store = F.FakeStrategyStore()
    inp = A.clean(**(make or {}))
    r = store.create(by="ana@position2.com", inputs=inp, account_name="Acme Dental", steps=pa.steps_for(inp))
    store.begin(r["id"])
    job.run_build(r["id"], store=store, deps=A.deps(model or A.Model()))
    return json.loads(json.dumps(ps.public(store.get(r["id"]))))


@pytest.fixture(scope="module")
def rec():
    over = A.answer("google_rsa")
    over["headlines"][3] = {"text": "Free Whitening Today", "angle": "offer"}
    return build({"formats": ["google_rsa", "google_pmax", "meta_feed"]}, A.Model({"google_rsa": over}))


def run(scenario):
    base = [{"match": "/adcopy/options", "body": OPTIONS}, {"match": "/segments/accounts", "body": ACCOUNTS}, {"match": "/adcopy/list", "body": {"builds": []}}]
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
    return {"search": "?build=" + r["id"], "responses": [{"match": "/status", "body": {"status": "done", "progress": r["progress"]}}, {"match": "/adcopy/" + r["id"], "method": "GET", "body": r}] + (extra or [])}


def page(r, name, steps=None):
    return run(opened(r) | {"steps": ([tab(name)] if name != "overview" else []) + (steps or [])})["el"]["#tabBody"]["html"]


# ── the form ────────────────────────────────────────────────────────────────

def test_the_form_shows_the_goals_formats_angles_tones_cards_and_accounts():
    out = run({})
    h = out["el"]
    assert h["#goals"]["html"].count("data-g=") == 5 and 'aria-checked="true"' in h["#goals"]["html"]
    formats = h["#formats"]["html"]
    assert formats.count('<button type="button" class="cb-card') == 6 and formats.count('cb-card on') == 2 and formats.count("Creative brief") == 4
    assert "Google Search ad (Responsive Search Ad)" in formats and "Meta Stories and Reels ad" in formats
    angles = h["#angles"]["html"]
    assert angles.count("data-a=") == 8 and angles.count('aria-pressed="true"') == 4 and "What the customer gets" in angles
    assert h["#fTone"]["html"].count("<option") == 5 and 'value="plain" selected' in h["#fTone"]["html"]
    cards = h["#cards"]["html"]
    assert cards.count('<button type="button" class="cb-card') == 2 and "Needs a Google Ads account" in cards and "Needs a website" in cards
    assert [i["value"] for i in out["picker"][-1][0]] == ["", "1234567890", "2345678901"]


def test_a_website_and_an_account_turn_the_cards_on():
    out = run({"values": {"#fSite": "acmedental.com"}, "steps": [{"fire": ["input", {"id": "fSite"}]}, {"pick": "1234567890"}]})
    cards = out["el"]["#cards"]["html"]
    assert "Needs a website" not in cards and "Needs a Google Ads account" not in cards and cards.count("cb-tag ok") == 2


def test_without_the_ai_the_form_says_so():
    out = run({"responses": [{"match": "/adcopy/options", "body": dict(OPTIONS, modelReady=False)}]})
    assert "AI is not available" in out["el"]["#goNote"]["html"]


def test_the_form_is_sent_with_the_formats_and_angles_chosen():
    values = {"#fName": "Acme Dental", "#fSite": "acmedental.com", "#fProduct": "dental implants", "#fTone": "friendly", "#fFacts": "Free consultation", "#fKeywords": "dental implants, invisalign",
              "#fAvoid": "cheap", "#fComp": "Smile Austin", "#fCampaign": "P2_Search", "#fAdGroup": "Implants", "#fLang": "English"}
    steps = [{"fire": ["input", {"id": "fSite"}]}, click({"#formats [data-f]": {"data-f": "meta_feed"}}), click({"#formats [data-f]": {"data-f": "google_pmax"}}),
             click({"#angles [data-a]": {"data-a": "proof"}}), click({"#angles [data-a]": {"data-a": "offer"}}), click({"#goals [data-g]": {"data-g": "calls"}}), {"pick": "1234567890"}, press("goBtn")]
    out = run({"values": values, "steps": steps, "responses": [{"match": "/adcopy/start", "method": "POST", "status": 201, "body": {"id": "a" * 32, "started": True}},
                                                              {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}]})
    sent = next(c for c in out["calls"] if c["url"].endswith("/adcopy/start"))["body"]
    assert sent["formats"] == ["google_rsa", "google_pmax"] and sent["angles"] == ["benefit", "problem", "action", "offer"] and sent["goal"] == "calls" and sent["tone"] == "friendly"
    assert sent["account"] == "1234567890" and sent["product"] == "dental implants" and sent["facts"] == "Free consultation" and sent["briefs"] is True
    assert sent["research"] == {"website": True, "account": True} and sent["campaignName"] == "P2_Search" and sent["competitors"] == "Smile Austin"


def test_the_brief_box_is_sent_as_it_is_ticked():
    values = {"#fName": "Acme", "#fProduct": "implants"}
    for checked, want in ((None, True), (True, True), (False, False)):
        scenario = {"values": values, "steps": [press("goBtn")], "responses": [{"match": "/adcopy/start", "method": "POST", "status": 201, "body": {"id": "a" * 32}}, {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}]}
        if checked is not None:
            scenario["checked"] = {"#fBriefs": checked}
        out = run(scenario)
        assert next(c for c in out["calls"] if c["url"].endswith("/adcopy/start"))["body"]["briefs"] is want


def test_a_form_that_is_not_complete_is_not_sent():
    def sent(values, steps=None):
        out = run({"values": values, "steps": (steps or []) + [press("goBtn")]})
        return [c for c in out["calls"] if c["url"].endswith("/adcopy/start")], out["el"]["#msg"]["html"]
    calls, msg = sent({"#fProduct": "implants"})
    assert calls == [] and "name" in msg
    calls, msg = sent({"#fName": "Acme"})
    assert calls == [] and "what the ads are for" in msg
    calls, msg = sent({"#fName": "Acme", "#fProduct": "implants"}, [click({"#formats [data-f]": {"data-f": "google_rsa"}}), click({"#formats [data-f]": {"data-f": "meta_feed"}})])
    assert calls == [] and "at least one ad format" in msg
    calls, msg = sent({"#fName": "Acme", "#fProduct": "implants"}, [click({"#angles [data-a]": {"data-a": a}}) for a in pa.DEFAULT_ANGLES])
    assert calls == [] and "at least one angle" in msg


def test_no_more_than_five_angles_can_be_picked():
    steps = [click({"#angles [data-a]": {"data-a": "offer"}}), click({"#angles [data-a]": {"data-a": "urgency"}}), click({"#angles [data-a]": {"data-a": "question"}})]
    out = run({"steps": steps})
    assert out["el"]["#angles"]["html"].count('aria-pressed="true"') == 5 and "at most 5 angles" in out["el"]["#msg"]["html"]


# ── a build in progress ─────────────────────────────────────────────────────

def test_a_running_build_shows_its_steps_and_a_stale_one_offers_to_finish(rec):
    steps = [{"key": "r:website", "label": "Reading the client's website", "state": "done", "note": "Read 7 pages"}, {"key": "a:google_rsa", "label": "Writing the Google Search copy", "state": "running", "note": ""}]
    out = run({"search": "?build=" + rec["id"], "responses": [{"match": "/status", "body": {"status": "running", "progress": {"steps": steps, "phase": "Writing"}, "started": "2026-10-18T10:00:00+00:00"}}]})
    assert out["el"]["#steps"]["html"].count("cb-step") == 2 and "Read 7 pages" in out["el"]["#steps"]["html"] and out["el"]["#runTitle"]["text"] == "Writing"
    stale = run({"search": "?build=" + rec["id"], "responses": [{"match": "/status", "body": {"status": "running", "stale": True, "progress": {"steps": steps}}}]})
    assert "Finish what is missing" in stale["el"]["#runAct"]["html"] and "interrupted" in stale["el"]["#runTitle"]["text"]


# ── the finished result ─────────────────────────────────────────────────────

def test_the_result_has_a_tab_for_each_format_the_briefs_what_to_confirm_and_the_checks(rec):
    out = run(opened(rec))
    tabs = re.findall(r'data-t="([^"]+)"', out["el"]["#tabs"]["html"])
    assert tabs == ["overview", "google_rsa", "google_pmax", "meta_feed", "briefs", "confirm", "checks"]
    assert "To confirm (1)" in out["el"]["#tabs"]["html"]
    btns = out["el"]["#rBtns"]["html"]
    assert btns.count("download?file=") == 4 and "file=csv" in btns and "Google Ads Editor" in btns
    assert out["el"]["#rName"]["text"] == "Acme Dental" and "cost to write" in out["el"]["#rMeta"]["html"]


def test_the_editor_download_is_offered_only_with_a_search_ad():
    only_meta = build({"formats": ["meta_feed"]})
    assert "file=csv" not in run(opened(only_meta))["el"]["#rBtns"]["html"]


def test_the_overview_counts_lines_and_says_what_is_to_confirm(rec):
    html = page(rec, "overview")
    assert "Lines ready" in html and "To confirm" in html and "Before this runs" in html and "left out of the file for Google Ads Editor" in html
    assert html.count("Complete") == 3 and "Client website" in html and "Google Ads account" in html


def test_a_format_tab_lists_every_line_with_its_length_angle_and_status(rec):
    html = page(rec, "google_rsa")
    lines = re.findall(r'<span class="ac-t">([^<]*)</span>', html)
    assert lines[:3] == ["Dental Implants Austin", "Invisalign Cost Guide", "Rated 4.9 Stars"] and len(lines) == 15 + 4
    assert "22 / 30" in html and "Display path <b>/Dental-Implants/Austin</b>" in html
    assert html.count("Ready") == 18 and html.count("Confirm</span>") == 1 and "Not on the pages or in your facts: " in html
    assert "Copy the 14 ready lines" in html and "Copy the 4 ready lines" in html
    assert 'data-copyall="google_rsa|0"' in html and 'data-copy="google_rsa|0|3"' in html


def test_a_format_that_came_out_short_says_so_and_an_empty_field_says_so():
    short = build({"formats": ["google_rsa"]}, A.Model({"google_rsa": {"headlines": A.rows(A.HEADS[:2]), "descriptions": [], "paths": {"path1": "a", "path2": "b"}}}))
    html = page(short, "google_rsa")
    assert "This format came out short" in html and "Headlines: 2 kept, at least 3 needed" in html and "Nothing usable came back for this field." in html and "2 kept, up to 15" in html


def test_the_button_and_notes_of_a_format_show(rec):
    html = page(rec, "google_pmax")
    assert "Button <b>Book now</b>" in html and "Short description" in html and "up to 1, at most 60 characters" in html


def test_a_line_is_escaped_everywhere_it_is_shown():
    r = build({"formats": ["meta_feed"], "name": "Acme Dental"})
    meta = r["plan"]["formats"]["meta_feed"]
    meta["fields"][1]["items"][0]["text"] = "<img src=x onerror=alert(1)>"
    meta["fields"][0]["items"][0]["claims"] = ["<b>free</b>"]
    meta["fields"][0]["items"][0]["status"] = "confirm"
    meta["brief"]["concept"] = "<script>alert(2)</script>"
    meta["brief"]["script"][0]["onScreen"] = "<svg onload=alert(3)>"
    r["name"] = "Acme <b>Dental</b>"
    for name in ("meta_feed", "briefs", "overview"):
        html = page(r, name)
        assert "<img src=x" not in html and "<script>alert" not in html and "<svg onload" not in html and "<b>free</b>" not in html, name
    assert "&lt;img src=x onerror=alert(1)&gt;" in page(r, "meta_feed") and "&lt;b&gt;free&lt;/b&gt;" in page(r, "meta_feed")
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in page(r, "briefs") and "&lt;svg onload=alert(3)&gt;" in page(r, "briefs")
    assert "Acme <b>Dental</b>" not in run(opened(r))["el"]["#rName"]["html"]


def test_copy_buttons_copy_exactly_what_the_server_kept(rec):
    line = run(opened(rec) | {"steps": [tab("google_rsa"), click({"[data-copy]": {"data-copy": "google_rsa|0|1"}})]})
    assert line["copied"] == ["Invisalign Cost Guide"]
    everything = run(opened(rec) | {"steps": [tab("google_rsa"), click({"[data-copyall]": {"data-copyall": "google_rsa|0"}})]})["copied"][0].split("\n")
    assert len(everything) == 14 and "Free Whitening Today" not in everything and everything[0] == "Dental Implants Austin"                  # only the ready lines
    unready = run(opened(rec) | {"steps": [tab("google_rsa"), click({"[data-copy]": {"data-copy": "google_rsa|0|3"}})]})["copied"]
    assert unready == ["Free Whitening Today"]                                                                                      # one line may be copied on purpose
    nothing = run(opened(rec) | {"steps": [tab("google_rsa"), click({"[data-copy]": {"data-copy": "google_rsa|9|9"}})]})["copied"]
    assert nothing == []


def test_a_brief_card_shows_the_script_the_lists_and_the_sizes_and_copies_as_text(rec):
    html = page(rec, "briefs")
    assert html.count("ac-brief-card") == 1 and "Meta feed ad (Facebook and Instagram)" in html and "A day with the team at Acme Dental" in html
    assert "Meet Acme Dental" in html and "Photo of the team" in html and "No stock photos" in html and "1080 x 1350" in html and "Copy the brief" in html
    got = run(opened(rec) | {"steps": [tab("briefs"), click({"[data-copybrief]": {"data-copybrief": "meta_feed"}})]})["copied"][0]
    assert got.startswith("Meta feed ad (Facebook and Instagram)\nConcept: A day with the team at Acme Dental\nHook: Meet the people who will plan your implants") and "Beat 1: The team at the door | On screen: Meet Acme Dental | Said: Meet the team." in got
    assert "Ask the client for: Photo of the team" in got and "Size, Image: 1080 x 1080" in got


def test_the_confirm_tab_lists_the_claims_and_copies_a_message_for_the_client(rec):
    html = page(rec, "confirm")
    assert "Free Whitening Today" in html and "Google Search" in html and "Copy as a message to the client" in html
    got = run(opened(rec) | {"steps": [tab("confirm"), press("copyQ")]})["copied"][0]
    assert got.startswith("Please confirm these before the ads run.") and '- "Free Whitening Today" (Google Search): ' in got


def test_the_checks_tab_names_what_was_dropped_and_the_facts_used():
    over = A.answer("google_rsa")
    over["headlines"] += [{"text": "X" * 40, "angle": "benefit"}, {"text": "Cheap Dental Implants", "angle": "offer"}]
    r = build({"formats": ["google_rsa"]}, A.Model({"google_rsa": over}))
    html = page(r, "checks")
    assert "Lines dropped" in html and "A line held a word on the avoid list" in html and "Rated 4.9 stars from 312 reviews." in html
    assert "In a Google headline" in html and "dental implants austin" in html and re.search(r"\d+ pages? of the website read", html)


def test_a_build_that_stopped_says_so_and_offers_to_carry_on(rec):
    stopped = dict(rec, status="error", error="1 of 2 formats could not be written (Meta feed: declined).")
    html = run(opened(stopped))["el"]["#rNote"]["html"]
    assert "The writing stopped." in html and "Meta feed: declined" in html and "Finish what is missing" in html
    noted = dict(rec, note="1 line states a claim the client has not confirmed.")
    assert "claim the client has not confirmed" in run(opened(noted))["el"]["#rNote"]["html"]


def test_build_again_resume_and_remove_call_the_right_routes(rec):
    out = run(opened(rec, [{"match": "/rebuild", "method": "POST", "body": {"id": rec["id"], "started": True}}]) | {"steps": [press("rebuildBtn")]})
    call = next(c for c in out["calls"] if c["url"].endswith("/rebuild"))
    assert call["body"] == {"resume": False}
    out = run(opened(rec, [{"match": "/rebuild", "method": "POST", "body": {"id": rec["id"], "started": True}}]) | {"steps": [press("carryBtn")]})
    assert next(c for c in out["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": True}
    declined = run(opened(rec) | {"confirm": False, "steps": [press("rebuildBtn")]})
    assert not [c for c in declined["calls"] if c["url"].endswith("/rebuild")]
    gone = run(opened(rec, [{"match": "/adcopy/" + rec["id"], "method": "DELETE", "body": {"ok": True}}]) | {"steps": [press("delBtn")]})
    assert any(c["method"] == "DELETE" for c in gone["calls"]) and gone["el"]["#builder"]["hidden"] is False


def test_change_the_details_puts_the_form_back(rec):
    out = run(opened(rec) | {"steps": [press("reuseBtn")]})
    el = out["el"]
    assert el["#fName"]["value"] == "Acme Dental" and el["#fProduct"]["value"] == "dental implants and invisalign" and el["#fKeywords"]["value"] == "dental implants austin, invisalign cost"
    assert el["#fComp"]["value"] == "Smile Austin" and el["#fFacts"]["value"] == "Rated 4.9 stars from 312 reviews." and el["#fTone"]["value"] == "plain"
    assert el["#formats"]["html"].count("cb-card on") == 3 and el["#builder"]["hidden"] is False


def test_the_saved_list_shows_formats_state_and_cost():
    builds = [{"id": "a" * 32, "name": "Acme <b>Dental</b>", "status": "done", "createdBy": "ana@position2.com", "createdAt": "2026-10-18T10:00:00+00:00", "formats": ["Google Search", "Meta feed"], "cost": 0.42},
              {"id": "b" * 32, "name": "Other", "status": "error", "createdBy": "bo@position2.com", "createdAt": "2026-10-18T10:00:00+00:00", "formats": [], "stale": True}]
    html = run({"responses": [{"match": "/adcopy/list", "body": {"builds": builds}}]})["el"]["#recent"]["html"]
    assert "Google Search, Meta feed" in html and "Ready" in html and "$0.42" in html and "Interrupted" in html and "no formats" in html and "Acme <b>Dental</b>" not in html


def test_there_is_no_briefs_tab_when_no_format_has_a_brief():
    r = build({"formats": ["google_rsa", "google_pmax"]})
    tabs = re.findall(r'data-t="([^"]+)"', run(opened(r))["el"]["#tabs"]["html"])
    assert tabs == ["overview", "google_rsa", "google_pmax", "checks"]                      # and no "to confirm" tab when nothing needs confirming


def test_what_the_server_flags_on_a_ready_line_and_a_path_and_a_brief_claim_are_shown_escaped():
    r = build({"formats": ["meta_feed", "google_rsa"]})
    meta = r["plan"]["formats"]["meta_feed"]
    meta["fields"][0]["items"][0]["flags"] = ["It touches a sensitive subject. <b>Read it</b>."]
    meta["brief"]["claims"] = [{"where": "hook", "text": "<i>Best</i> care", "claims": ["best"]}]
    r["plan"]["formats"]["google_rsa"]["paths"]["path1"] = "<u>x</u>"
    assert "It touches a sensitive subject. &lt;b&gt;Read it&lt;/b&gt;." in page(r, "meta_feed")
    briefs = page(r, "briefs")
    assert "The brief states something the evidence does not hold" in briefs and "&quot;&lt;i&gt;Best&lt;/i&gt; care&quot; (best)" in briefs and "<i>Best</i>" not in briefs
    assert "Display path <b>/&lt;u&gt;x&lt;/u&gt;/Austin</b>" in page(r, "google_rsa")


def test_change_the_details_restores_which_cards_were_on():
    r = build({"formats": ["google_rsa"]})
    r["inputs"]["research"] = {"website": False, "account": True}
    out = run(opened(r) | {"steps": [press("reuseBtn")]})
    cards = out["el"]["#cards"]["html"]
    assert re.search(r'data-k="website" aria-pressed="false"', cards) and re.search(r'data-k="account" aria-pressed="true"', cards)


# ── the Platform switch ──────────────────────────────────────────────────────

def _fn(src, name):
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


def plat(expr, formats, plat_="both"):
    """The switch's own functions from the page script, executed against the real format list."""
    src = open(JS, encoding="utf-8").read()
    prelude = "".join(_fn(src, n) + "\n" for n in ("formatPlatform", "platFormats", "setPlatform", "platformOfFormats"))
    setup = "var S={opts:%s,formats:%s,plat:%s};var paintForm=function(){};" % (json.dumps(OPTIONS), json.dumps(formats), json.dumps(plat_))
    r = subprocess.run(["node", "-e", "%s%s%sconsole.log(JSON.stringify(%s))" % (setup, prelude, "", expr)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_meta_offers_only_the_meta_formats_and_google_only_the_google_ones():
    assert plat("(S.plat='meta',platFormats().map(f=>f.key))", ["google_rsa"]) == ["meta_feed", "meta_stories_reels"]
    assert plat("(S.plat='google',platFormats().map(f=>f.key))", []) == ["google_rsa", "google_pmax", "google_demand_gen", "google_display"]
    assert len(plat("platFormats()", [], "both")) == 6


def test_choosing_meta_drops_the_google_formats_and_falls_back_to_the_metas_default_when_none_is_left():
    assert plat("(setPlatform('meta'),S.formats)", ["google_rsa", "meta_stories_reels"]) == ["meta_stories_reels"]
    assert plat("(setPlatform('meta'),S.formats)", ["google_rsa", "google_pmax"]) == ["meta_feed"]
    assert plat("(setPlatform('google'),S.formats)", ["meta_feed"]) == ["google_rsa"]


def test_choosing_both_makes_sure_each_platform_has_a_format_and_keeps_what_was_picked():
    assert plat("(setPlatform('both'),S.formats)", ["google_pmax"]) == ["google_pmax", "meta_feed"]
    assert plat("(setPlatform('both'),S.formats)", ["meta_stories_reels"]) == ["meta_stories_reels", "google_rsa"]
    assert plat("(setPlatform('both'),S.formats)", ["google_rsa", "meta_feed"]) == ["google_rsa", "meta_feed"]


def test_an_opened_build_puts_the_switch_where_its_formats_were():
    assert plat("platformOfFormats(['google_rsa','google_pmax'])", []) == "google" and plat("platformOfFormats(['meta_feed'])", []) == "meta"
    assert plat("platformOfFormats(['google_rsa','meta_feed'])", []) == "both" and plat("platformOfFormats([])", []) == "both"


def test_the_page_has_the_switch():
    page = open(JS, encoding="utf-8").read()
    tpl = open(os.path.join(ROOT, "templates", "ad_copy.html"), encoding="utf-8").read()
    assert "PMPlatform.switcher" in page and 'id="platSw"' in tpl and "pm_platform.js" in tpl and "S.platSw.set(S.plat)" in page
