"""Test & Experiment Tracker: static/js/exp_data.js run in Node, and the page script checked against its template.

The server reads, compares and decides every verdict (tests/test_paid_exp*.py). The page only draws, filters and words things, so how a verdict is labelled, which tests a view shows,
how rates and gaps read and the shape of the chart come from exp_data.js: the file is executed here, not read. The page script is syntax-checked and every element it reaches for must
exist in the template.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "static", "js", "exp_data.js")
PAGE = os.path.join(ROOT, "static", "js", "experiment_tracker.js")
TEMPLATE = os.path.join(ROOT, "templates", "experiment_tracker.html")
CSS = os.path.join(ROOT, "static", "css", "experiment_tracker.css")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ── words ───────────────────────────────────────────────────────────────────

def test_a_verdict_is_worded_for_what_it_is_about():
    assert run('X.verdictInfo("winner", "trial")') == {"label": "Trial is ahead", "tone": "good", "icon": "up"}
    assert run('X.verdictInfo("winner", "ad")')["label"] == "Clear leader"
    assert run('X.verdictInfo("partial", "ad")')["label"] == "Leads some"
    assert run('X.verdictInfo("behind", "trial")')["tone"] == "bad" and run('X.verdictInfo("too_early", "ad")')["tone"] == "wait" and run('X.verdictInfo("partial", "ad")')["tone"] == "good"
    assert run('X.verdictInfo("no_data", "ad")')["label"] == "Not shown" and run('X.verdictInfo("no_data", "trial")')["label"] == "No data"
    assert run('X.verdictInfo("not_measured")')["label"] == "Not measured"
    assert run('X.verdictInfo("nonsense", "ad")') == {"label": "Unknown", "tone": "unk", "icon": "help"}


def test_every_kind_of_decision_the_server_can_send_has_a_label():
    for kind in ("decide", "apply", "end", "pause", "stuck", "check"):
        assert run('X.kindInfo(%s).label' % json.dumps(kind)) not in ("Look", None)
    assert run('X.kindInfo("zzz")') == {"label": "Look", "tone": "unk", "icon": "help"}


def test_every_verdict_the_server_can_send_is_known_to_the_page():
    from tracker import paid_exp_stats as st
    for v in st.VERDICTS:
        assert run('X.verdictInfo(%s, "ad").label' % json.dumps(v)) != "Unknown" or v in ("not_measured",), v
        assert run('X.verdictInfo(%s, "trial").label' % json.dumps(v)) != "Unknown" or v in ("partial",), v


def test_states_read_in_words():
    assert run('X.stateInfo("running")') == "Running" and run('X.stateInfo("setting up")') == "Setting up" and run('X.stateInfo("something")') == "Something" and run('X.stateInfo("")') == "Unknown"


def test_rates_gaps_and_confidence_read_plainly():
    assert run("[X.pct(5), X.pct(5.04), X.pct(5.06), X.pct(null), X.pct(NaN)]") == ["5%", "5%", "5.1%", "n/a", "n/a"]
    assert run("[X.rel(80), X.rel(-50.04), X.rel(0), X.rel(null), X.rel(0.02)]") == ["+80%", "-50%", "0%", "", "0%"]
    assert run("[X.conf(97.25), X.conf(95), X.conf(99.9), X.conf(100), X.conf(null)]") == ["97.3% sure", "95% sure", "99.9% sure", "over 99.9% sure", ""]
    assert run("[X.num(12345.6), X.num(null)]") == ["12,346", "n/a"]


def test_money_uses_the_account_currency_and_drops_cents_from_a_hundred():
    assert run('[X.money(1234.5, "USD"), X.money(12.5, "USD"), X.money(null), X.money(99.5, "xx")]') == ["$1,235", "$12.50", "n/a", "$99.50"]
    assert run('X.money(1000, "EUR")') == "€1,000" and run('X.money(500.4, "USD")') == "$500" and run('X.money(99.99, "USD")') == "$99.99"


def test_dates_read_as_month_and_day_whatever_the_time_zone():
    assert run('X.dateText("2026-10-01")') == "Oct 1" and run('X.dateText("")') == "" and run('X.dateText("junk")') == "junk"
    assert run('X.span("2026-09-20", "2026-10-30")') == "Sep 20 to Oct 30" and run('X.span("2026-09-20", null)') == "Sep 20 onward" and run('X.span(null, null)') == ""


def test_what_a_test_is_waiting_for_reads_as_a_list():
    assert run('X.waitText({more: {clicks: 60, conversions: 23}, days: 2})') == "60 more clicks in each arm, 23 more conversions, 2 more days"
    assert run('X.waitText({more: {impressions: 1500}, days: 1})') == "1,500 more impressions in each arm, 1 more day"
    assert run('X.waitText(null)') == "" and run('X.waitText({more: {}, days: 0})') == ""


# ── views ───────────────────────────────────────────────────────────────────

REPORT = {
    "experiments": [{"id": "1", "state": "running"}, {"id": "2", "state": "running"}, {"id": "3", "state": "ended"}, {"id": "4", "state": "applied"}, {"id": "5", "state": "setting up"}],
    "needs": [{"kind": "apply", "ref": {"type": "experiment", "id": "2"}}, {"kind": "pause", "ref": {"type": "adTest", "id": "2"}}],
}


def test_experiments_are_counted_and_filtered_by_view():
    assert run("X.expCounts(R)", R=REPORT) == {"needs": 1, "running": 3, "finished": 2, "all": 5}
    ids = lambda v: [e["id"] for e in run("X.filterExperiments(R, %s)" % json.dumps(v), R=REPORT)]   # noqa: E731
    assert ids("needs") == ["2"] and ids("running") == ["1", "2", "5"] and ids("finished") == ["3", "4"] and ids("all") == ["1", "2", "3", "4", "5"]


def test_the_ad_test_that_needs_a_decision_does_not_count_as_an_experiment_that_does():
    assert run("X.expCounts(R).needs", R=dict(REPORT, needs=[{"kind": "pause", "ref": {"type": "adTest", "id": "1"}}])) == 0


def test_the_first_view_is_the_one_with_something_in_it():
    assert run("X.defaultExpView(R)", R=REPORT) == "needs"
    assert run("X.defaultExpView(R)", R=dict(REPORT, needs=[])) == "running"
    assert run("X.defaultExpView(R)", R={"experiments": [{"id": "9", "state": "ended"}], "needs": []}) == "all"
    assert run("X.defaultExpView(R)", R={}) == "all"


TESTS = [
    {"id": "1", "name": "Implants", "campaign": "Search Implants", "verdict": "winner", "ads": [{"headlines": ["Fix your teeth"], "descriptions": ["Call us"]}]},
    {"id": "2", "name": "Cleanings", "campaign": "Search Cleanings", "verdict": "partial", "ads": [{"headlines": ["Book a cleaning"], "descriptions": []}]},
    {"id": "3", "name": "Braces", "campaign": "Search Braces", "verdict": "no_difference", "ads": [{"headlines": ["Straight teeth"], "descriptions": ["Free consultation"]}]},
    {"id": "4", "name": "Whitening", "campaign": "Search Whitening", "verdict": "too_early", "ads": []},
    {"id": "5", "name": "Crowns", "campaign": "Search Crowns", "verdict": "no_data", "ads": []},
]


def test_ad_tests_are_counted_and_filtered():
    assert run("X.adCounts(T)", T=TESTS) == {"leader": 2, "flat": 1, "early": 2, "all": 5}
    ids = lambda v, t="": [x["id"] for x in run("X.filterAdTests(T, %s, %s)" % (json.dumps(v), json.dumps(t)), T=TESTS)]   # noqa: E731
    assert ids("leader") == ["1", "2"] and ids("flat") == ["3"] and ids("early") == ["4", "5"] and ids("all") == ["1", "2", "3", "4", "5"]


def test_the_ad_search_looks_in_the_names_and_the_ad_text_and_wants_every_word():
    ids = lambda t: [x["id"] for x in run("X.filterAdTests(T, 'all', %s)" % json.dumps(t), T=TESTS)]   # noqa: E731
    assert ids("implants") == ["1"] and ids("  CLEANING  ") == ["2"] and ids("free consultation") == ["3"] and ids("fix teeth") == ["1"] and ids("fix braces") == []
    assert ids("search") == ["1", "2", "3", "4", "5"] and ids("") == ["1", "2", "3", "4", "5"] and ids("zzz") == []


def test_the_search_is_applied_inside_the_view():
    assert [x["id"] for x in run("X.filterAdTests(T, 'flat', 'search')", T=TESTS)] == ["3"]
    assert run("X.filterAdTests(T, 'flat', 'implants')", T=TESTS) == []


def test_the_first_ad_view_is_the_leaders_when_there_are_any():
    assert run("X.defaultAdView(T)", T=TESTS) == "leader"
    assert run("X.defaultAdView(T)", T=TESTS[2:]) == "all"
    assert run("X.defaultAdView(null)") == "all"


# ── tiles and the lead sentence ─────────────────────────────────────────────

SUMMARY = {"experiments": 5, "running": 3, "ended": 1, "applied": 1, "winners": 1, "behind": 1, "noDifference": 1, "tooEarly": 0, "needsAction": 4, "adTests": 40, "adTestsShown": 12,
           "adConclusive": 3, "adNoDifference": 5, "adTooEarly": 4, "adSpend": 1000.0, "experimentSpend": 234.5}


def test_the_tiles_say_what_matters_first():
    t = {x["key"]: x for x in run('X.tiles(S, "USD")', S=SUMMARY)}
    assert [x["key"] for x in run('X.tiles(S, "USD")', S=SUMMARY)] == ["needs", "running", "result", "ads", "leaders", "spend"]
    assert t["needs"]["value"] == 4 and t["needs"]["tone"] == "bad" and t["running"]["value"] == 3 and "5 experiments in all: 1 ended, 1 applied" in t["running"]["hint"]
    assert t["result"]["value"] == 2 and "1 ahead, 1 behind, 1 no difference, 0 too early" in t["result"]["hint"]
    assert t["ads"]["value"] == 40 and "the 12 that spent most are shown" in t["ads"]["hint"]
    assert t["leaders"]["value"] == 3 and t["spend"]["value"] == "$1,235"


def test_the_tiles_of_an_empty_account_are_calm():
    t = {x["key"]: x for x in run("X.tiles({}, 'USD')")}
    assert t["needs"]["tone"] == "good" and t["needs"]["hint"] == "Nothing is waiting on a person" and t["running"]["hint"] == "No experiment in this account" and t["result"]["tone"] == "neutral"
    assert t["ads"]["hint"] == "two or more live ads in each"


def test_the_lead_sentence_counts_and_agrees_with_one_and_many():
    assert run("X.leadLine({summary: S})", S=SUMMARY) == "4 tests need a decision. 3 experiments running, 2 with a result and 40 ad groups testing copy, 3 with a clear leader."
    assert run("X.leadLine({summary: S})", S={"needsAction": 1, "experiments": 1, "running": 1, "adTests": 0}) == "1 test needs a decision. 1 experiment running."
    assert run("X.leadLine({summary: S})", S={"experiments": 2, "running": 0}) == "Nothing needs a decision right now. 0 experiments running."
    assert run("X.leadLine({summary: S})", S={"adTests": 3}) == "Nothing needs a decision right now. 3 ad groups testing copy."
    assert run("X.leadLine({summary: S})", S={}) == "This account has no test to track."
    assert run("X.leadLine(null)") == "This account has no test to track."


# ── the chart ───────────────────────────────────────────────────────────────

def arm(name, control, rows):
    return {"name": name, "control": control, "days": rows}


def day(d, impr, clicks, conv):
    return [d, impr, clicks, 1.0, conv, 0.0]


EXP = {"comparison": {"metric": "convRate"}, "arms": [
    arm("Base", True, [day("2026-10-01", 1000, 100, 5), day("2026-10-02", 1000, 100, 10), day("2026-10-04", 1000, 100, 5)]),
    arm("Trial", False, [day("2026-10-01", 1000, 100, 10), day("2026-10-02", 1000, 100, 10), day("2026-10-03", 1000, 100, 10), day("2026-10-04", 1000, 100, 20)])]}


def test_the_chart_draws_each_arms_running_conversion_rate_over_every_day():
    c = run("X.chart(E, 600, 200, 20)", E=EXP)
    assert c["metric"] == "convRate" and c["label"] == "Conversion rate" and c["days"] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    base, trial = c["series"]
    assert base["control"] and not trial["control"] and base["name"] == "Base"
    assert [p["rate"] for p in base["points"]] == [5.0, 7.5, 7.5, 6.67]             # 5/100, 15/200, the same on the day with no row, then 20/300
    assert [p["rate"] for p in trial["points"]] == [10.0, 10.0, 10.0, 12.5]
    assert c["yMax"] == 15 and c["series"][1]["last"]["rate"] == 12.5
    assert base["points"][0]["x"] == 20 and base["points"][-1]["x"] == 580 and trial["d"].startswith("M20 ")
    assert [t["value"] for t in c["yTicks"]] == [0, 5, 10, 15] and c["yTicks"][0] == {"value": 0, "y": 180} and c["yTicks"][3]["y"] == 20


def test_a_higher_rate_is_drawn_higher_up():
    c = run("X.chart(E, 600, 200, 20)", E=EXP)
    assert c["series"][1]["points"][3]["y"] < c["series"][0]["points"][3]["y"]


def test_a_rate_is_not_drawn_until_its_arm_has_enough_clicks():
    e = {"comparison": {"metric": "convRate"}, "arms": [arm("A", True, [day("2026-10-01", 500, 10, 5), day("2026-10-02", 500, 10, 0), day("2026-10-03", 500, 10, 0)]),
                                                        arm("B", False, [day("2026-10-01", 500, 10, 1), day("2026-10-02", 500, 10, 1), day("2026-10-03", 500, 10, 1)])]}
    c = run("X.chart(E, 600, 200, 20)", E=e)
    assert [p["date"] for p in c["series"][0]["points"]] == ["2026-10-02", "2026-10-03"] and c["series"][0]["points"][0]["rate"] == 25.0


def test_the_chart_follows_the_verdicts_metric_and_falls_back_to_click_rate_without_conversions():
    e = {"comparison": {"metric": "ctr"}, "arms": [arm("A", True, [day("2026-10-01", 1000, 50, 5), day("2026-10-02", 1000, 50, 5)]), arm("B", False, [day("2026-10-01", 1000, 100, 5), day("2026-10-02", 1000, 100, 5)])]}
    c = run("X.chart(E, 600, 200, 20)", E=e)
    assert c["metric"] == "ctr" and c["label"] == "Click rate" and c["series"][0]["points"][1]["rate"] == 5.0
    e2 = {"comparison": None, "arms": [arm("A", True, [day("2026-10-01", 1000, 50, 0), day("2026-10-02", 1000, 50, 0)]), arm("B", False, [day("2026-10-01", 1000, 100, 0), day("2026-10-02", 1000, 100, 0)])]}
    assert run("X.chart(E).metric", E=e2) == "ctr"
    e3 = {"comparison": None, "arms": [arm("A", True, [day("2026-10-01", 1000, 500, 50), day("2026-10-02", 1000, 500, 50)])]}
    assert run("X.chart(E).metric", E=e3) == "convRate"


def test_there_is_nothing_to_draw_for_one_day_or_no_rate():
    assert run("X.chart(E, 600, 200, 20).series", E={"arms": [arm("A", True, [day("2026-10-01", 1000, 100, 5)])]}) == []
    assert run("X.chart({arms: []}).series") == [] and run("X.chart(null).series") == []
    assert run("X.chart(E, 600, 200, 20).series", E={"comparison": {"metric": "convRate"}, "arms": [arm("A", True, [day("2026-10-01", 1000, 100, 0), day("2026-10-02", 1000, 100, 0)])]}) == []


def test_the_chart_labels_a_few_days_along_the_bottom():
    c = run("X.chart(E, 600, 200, 20)", E=EXP)
    assert c["xTicks"][0] == {"x": 20, "label": "Oct 1"} and len(c["xTicks"]) <= 6
    long = {"comparison": {"metric": "convRate"}, "arms": [arm("A", True, [day("2026-09-%02d" % d, 1000, 100, 5) for d in range(1, 31)]), arm("B", False, [day("2026-09-%02d" % d, 1000, 100, 6) for d in range(1, 31)])]}
    n = len(run("X.chart(E, 600, 200, 20).xTicks", E=long))
    assert 4 <= n <= 6


def test_the_scale_is_made_of_round_steps_and_never_more_than_five():
    got = run("[0, 0.7, 2.2, 7, 13.5, 430, -3].map(X.scale)")
    assert got == [{"top": 1, "step": 0.25}, {"top": 0.8, "step": 0.2}, {"top": 2.5, "step": 0.5}, {"top": 8, "step": 2}, {"top": 15, "step": 5}, {"top": 500, "step": 100}, {"top": 1, "step": 0.25}]
    assert run("[1.2, 11, 0.15].map(X.scale)") == [{"top": 1.25, "step": 0.25}, {"top": 12.5, "step": 2.5}, {"top": 0.15, "step": 0.05}]     # quarter steps, and no float noise at an exact multiple
    for m in (0.013, 0.9, 3.3, 9.9, 47, 123456):
        s = run("X.scale(%s)" % m)
        assert s["top"] >= m and round(s["top"] / s["step"]) <= 5


# ── bars and the ruler ──────────────────────────────────────────────────────

def test_the_bars_show_each_rate_as_a_share_of_the_longer():
    assert run("X.bars({control: 5, trial: 9})") == {"control": 56, "trial": 100}
    assert run("X.bars({control: 9, trial: 9})") == {"control": 100, "trial": 100} and run("X.bars({control: 0, trial: 0})") == {"control": 0, "trial": 0}
    assert run("X.bars({control: null, trial: 9})") is None and run("X.bars(null)") is None


def test_the_ruler_runs_from_a_coin_toss_to_certain_with_the_95_percent_line():
    assert run("X.ruler(50)") == {"at": 0, "line": 90, "passed": False} and run("X.ruler(95)") == {"at": 90, "line": 90, "passed": True}
    assert run("X.ruler(100)")["at"] == 100 and run("X.ruler(30)")["at"] == 0 and run("X.ruler(94.9)")["passed"] is False
    assert run("X.ruler(null)") is None


# ── the page script and its template ────────────────────────────────────────

def test_the_page_script_and_data_are_valid_javascript():
    for p in (PAGE, DATA):
        r = subprocess.run(["node", "--check", p], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_every_element_the_page_script_reaches_for_is_in_the_template():
    js, html = open(PAGE, encoding="utf-8").read(), open(TEMPLATE, encoding="utf-8").read()
    ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))
    assert ids >= {"acctPick", "msg", "empty", "skel", "out", "fresh", "stale", "warn", "lead", "tiles", "needsCard", "needsNote", "needs", "expCard", "expNote", "expChips", "expList", "adCard",
                   "adNote", "adChips", "adFind", "adList", "moreBtn", "notes", "notesCard", "foot", "refreshBtn", "dlXlsx", "dlPdf", "dlZip"}
    for i in sorted(ids):
        assert 'id="%s"' % i in html, i
    for attr in ("data-open", "data-jump", "data-eview", "data-aview"):
        assert attr in js, attr


def test_the_page_loads_its_scripts_in_order_and_both_stylesheets():
    html = open(TEMPLATE, encoding="utf-8").read()
    order = [html.index(s) for s in ("js/pm_picker.js", "js/pm_platform.js", "js/au_data.js", "js/exp_data.js", "js/experiment_tracker.js")]
    assert order == sorted(order) and "css/account_audit.css" in html and "css/experiment_tracker.css" in html
    assert "root.ExpData = api" in open(DATA, encoding="utf-8").read() and "window.ExpData" in open(PAGE, encoding="utf-8").read() and "window.AuditData" in open(PAGE, encoding="utf-8").read()


def test_every_class_the_page_script_draws_is_styled():
    js, css = open(PAGE, encoding="utf-8").read(), open(CSS, encoding="utf-8").read()
    used = set()
    for m in re.findall(r'class="([^"]*)"', js):
        for c in m.split():
            if c.startswith("ex-") and not re.search(r"[+\']", c):
                used.add(c)
    assert used
    missing = sorted(c for c in used if "." + c not in css)
    assert not missing, missing


def test_the_page_never_links_an_address_it_has_not_checked_and_escapes_what_it_draws():
    js = open(PAGE, encoding="utf-8").read()
    assert "https?:" in js and 'rel="noopener noreferrer"' in js
    assert "innerHTML" in js and "esc(" in js


def test_motion_is_switched_off_for_anyone_who_asks_for_less():
    css = open(CSS, encoding="utf-8").read()
    assert "prefers-reduced-motion:no-preference" in css and "prefers-reduced-motion:reduce" in css
    reduce = css.split("prefers-reduced-motion:reduce")[1].split("}")[0]
    assert ".ex-bar i" in reduce and ".ex-bar-track i" in reduce and "animation:none" in reduce


def test_no_em_or_en_dash_in_the_page_files():
    for p in (DATA, PAGE, TEMPLATE, CSS):
        text = open(p, encoding="utf-8").read()
        assert "\u2014" not in text and "\u2013" not in text, p


def test_a_click_rate_line_starts_at_two_hundred_impressions():
    e = {"comparison": {"metric": "ctr"}, "arms": [arm("A", True, [day("2026-10-01", 100, 10, 0), day("2026-10-02", 100, 10, 0), day("2026-10-03", 100, 10, 0)]),
                                                   arm("B", False, [day("2026-10-01", 100, 20, 0), day("2026-10-02", 100, 20, 0), day("2026-10-03", 100, 20, 0)])]}
    c = run("X.chart(E, 600, 200, 20)", E=e)
    assert [p["date"] for p in c["series"][0]["points"]] == ["2026-10-02", "2026-10-03"] and c["series"][0]["points"][0]["rate"] == 10.0
    assert [p["date"] for p in run("X.chart(E, 600, 200, 20)", E=dict(e, arms=[arm("A", True, [day("2026-10-01", 200, 20, 0), day("2026-10-02", 200, 20, 0)]),
                                                                                  arm("B", False, [day("2026-10-01", 200, 40, 0), day("2026-10-02", 200, 40, 0)])]))["series"][0]["points"]] == ["2026-10-01", "2026-10-02"]


# ── Meta ────────────────────────────────────────────────────────────────────

def test_a_report_that_does_not_say_which_platforms_were_read_is_a_google_report():
    assert run("X.platformsOf({})") == ["google"] and run("X.platformsOf(null)") == ["google"] and run("X.platformsOf({summary:{platforms:[]}})") == ["google"]
    assert run("X.platformsOf({summary:{platforms:['meta']}})") == ["meta"] and run("X.platformsOf({summary:{platforms:['google','meta']}})") == ["google", "meta"]
    assert run("X.platformNames(['google','meta'])") == "Google Ads and Meta Ads" and run("X.platformNames(['google','meta'], ' or ')") == "Google Ads or Meta Ads"
    assert run("X.platformNames(undefined)") == "Google Ads" and run("X.platformNames(['tiktok'])") == "tiktok"


def test_a_meta_test_is_known_by_its_id():
    assert run("[X.isMetaId('meta:900'), X.isMetaId('900'), X.isMetaId(''), X.isMetaId(null), X.isMetaId('xmeta:1')]") == [True, False, False, False, False]


def test_the_stale_note_names_the_platform_that_is_not_answering():
    assert run("X.staleText({platform:'meta'}, '3 hours ago')") == "Meta Ads is not answering now, so this report is built from the copy saved 3 hours ago."
    assert run("X.staleText({platform:'google'}, 'x')").startswith("Google Ads is not answering now") and run("X.staleText({}, 'x')").startswith("Google Ads is not")
    assert run("X.staleText({platform:'tiktok'}, 'x')").startswith("The platform is not answering now")


def test_the_account_line_names_both_ids_when_two_accounts_were_read():
    assert run("X.accountNote(r)", r={"account": {"name": "Acme", "id": "1112223334+act_1"}, "accounts": [{"id": "1112223334"}, {"id": "act_1"}]}) == "Acme (1112223334 and act_1)"
    assert run("X.accountNote(r)", r={"account": {"name": "Acme", "id": "1112223334"}, "accounts": [{"id": "1112223334"}]}) == "Acme (1112223334)"
    assert run("X.accountNote(r)", r={"account": {"id": "1112223334"}}) == "1112223334" and run("X.accountNote(null)") == ""


def test_the_source_line_names_the_platforms_and_the_conversions_each_counts():
    google = run("X.sourceText(['google'])")
    assert google.startswith("Source: Google Ads (read only), saved for an hour.") and "Conversions are Google's own and may be counted late." in google
    assert google.endswith("Nothing is changed in Google Ads: apply, end or pause a test there.") and run("X.sourceText(undefined)") == google
    meta = run("X.sourceText(['meta'])")
    assert meta.startswith("Source: Meta Ads (read only)") and "Conversions are what Meta counts for each campaign's objective" in meta and "Google" not in meta
    both = run("X.sourceText(['google','meta'])")
    assert both.startswith("Source: Google Ads and Meta Ads (read only)") and "Conversions are each platform's own" in both and both.endswith("Nothing is changed in Google Ads or Meta Ads: apply, end or pause a test there.")


def test_the_ad_tests_are_called_ad_groups_ad_sets_or_both_for_the_platforms_read():
    assert run("[X.adUnit(['google'], 1), X.adUnit(['google'], 3), X.adUnit(['meta'], 1), X.adUnit(['meta'], 3), X.adUnit(undefined, 2)]") == ["1 ad group", "3 ad groups", "1 ad set", "3 ad sets", "2 ad groups"]
    assert run("[X.adUnit(['google','meta'], 1), X.adUnit(['google','meta'], 4)]") == ["1 ad group or ad set", "4 ad groups and ad sets"]
    assert "2 ad sets testing copy" in run("X.leadLine(r)", r={"summary": {"adTests": 2, "platforms": ["meta"]}})
    assert "2 ad groups testing copy" in run("X.leadLine(r)", r={"summary": {"adTests": 2}})
    assert "4 ad groups and ad sets testing copy" in run("X.leadLine(r)", r={"summary": {"adTests": 4, "platforms": ["google", "meta"]}})


def test_the_page_offers_all_three_platforms_marks_each_test_and_styles_what_it_draws():
    page, css, tpl = (open(f, encoding="utf-8").read() for f in (PAGE, CSS, TEMPLATE))
    assert 'allow: ["google", "meta", "both"]' in page and "#pf=" in page and "platformTag(" in page
    assert ".ex-plat" in css and ".ex-plat.meta" in css and ".ex-plat.google" in css
    assert "Meta's A/B tests" in tpl and "ad set" in tpl
    assert "x.label || k.label" in page
