"""Account Audit: static/js/au_data.js run in Node, and the page script checked against its template.

The server reads, checks and scores (tests/test_paid_audit*.py). The page only draws and filters, so which findings a view shows, how many of each result there are and
how a ring is drawn come from au_data.js: the file is executed here, not read. The page script is syntax-checked and every element it reaches for must exist in the template.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "static", "js", "au_data.js")
PAGE = os.path.join(ROOT, "static", "js", "account_audit.js")
TEMPLATE = os.path.join(ROOT, "templates", "account_audit.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const D=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def f(result, cid="G-X-01", title="A check", found="Found it.", items=(), count=None, severity="high", fix="Do it.", why="Because."):
    return {"id": cid, "title": title, "found": found, "items": list(items), "count": len(items) if count is None else count, "severity": severity, "result": result, "fix": fix, "why": why}


FINDINGS = [f("fail", "A-1", "Auto-tagging", "Auto-tagging is off."), f("warn", "A-2", "Quality Score", "31% low", items=["dentist (QS 3)"]), f("unknown", "A-3", "GA4 link", "Could not be checked"),
            f("pass", "A-4", "Account active", "Enabled."), f("pass", "A-5", "Tag", "Has a tag"), f("na", "A-6", "Calls", "No calls")]


# ── Counting and filtering ──────────────────────────────────────────────────

def test_a_tally_counts_each_result_and_the_total():
    assert run("D.tally(F)", F=FINDINGS) == {"fail": 1, "warn": 1, "pass": 2, "na": 1, "unknown": 1, "total": 6}
    assert run("D.tally([])") == {"fail": 0, "warn": 0, "pass": 0, "na": 0, "unknown": 0, "total": 0} and run("D.tally(null)")["total"] == 0


def test_each_view_shows_its_results_and_the_counts_match():
    assert [x["id"] for x in run('D.filterFindings(F, "attention", "")', F=FINDINGS)] == ["A-1", "A-2"]
    assert [x["id"] for x in run('D.filterFindings(F, "unknown", "")', F=FINDINGS)] == ["A-3"]
    assert [x["id"] for x in run('D.filterFindings(F, "pass", "")', F=FINDINGS)] == ["A-4", "A-5"]
    assert [x["id"] for x in run('D.filterFindings(F, "all", "")', F=FINDINGS)] == ["A-1", "A-2", "A-3", "A-4", "A-5", "A-6"]
    assert [run('D.viewCount(F, k)', F=FINDINGS, k=k) for k in ("attention", "unknown", "pass", "all", "nope")] == [2, 1, 2, 6, 0]
    assert run('D.filterFindings(F, "nope", "")', F=FINDINGS) == run('D.filterFindings(F, "all", "")', F=FINDINGS)       # an unknown view shows everything


def test_the_text_filter_looks_in_the_id_title_found_why_fix_and_affected_things():
    assert [x["id"] for x in run('D.filterFindings(F, "all", "dentist")', F=FINDINGS)] == ["A-2"]
    assert [x["id"] for x in run('D.filterFindings(F, "all", "  AUTO-TAG ")', F=FINDINGS)] == ["A-1"]
    assert [x["id"] for x in run('D.filterFindings(F, "all", "a-5")', F=FINDINGS)] == ["A-5"]
    assert [x["id"] for x in run('D.filterFindings(F, "attention", "enabled")', F=FINDINGS)] == []                     # a passing check is outside the view
    assert len(run('D.filterFindings(F, "all", "because")', F=FINDINGS)) == 6 and len(run('D.filterFindings(F, "all", "do it")', F=FINDINGS)) == 6
    assert run('D.filterFindings(null, "all", "x")') == []


def test_a_section_opens_on_what_needs_attention_then_what_could_not_be_checked_then_everything():
    assert run("D.defaultView(F)", F=FINDINGS) == "attention"
    assert run("D.defaultView(F)", F=[f("unknown"), f("pass")]) == "unknown"
    assert run("D.defaultView(F)", F=[f("pass"), f("na")]) == "all" and run("D.defaultView([])") == "all"


# ── Words and numbers ───────────────────────────────────────────────────────

def test_affected_things_are_joined_and_say_how_many_more():
    assert run("D.affectedText(x)", x=f("fail", items=["a", "b"])) == "a; b"
    assert run("D.affectedText(x)", x=f("fail", items=["a", "b"], count=5)) == "a; b (and 3 more)"
    assert run("D.affectedText(x)", x=f("fail")) == ""


def test_grades_have_a_tone_and_a_missing_one_has_none():
    assert [run("D.gradeTone(g)", g=g) for g in ("A", "B", "C", "D", "F", None, "")] == ["good", "ok", "warn", "bad", "bad", "none", "none"]
    assert run("D.scoreText(87.6)") == "88" and run("D.scoreText(0)") == "0" and run("D.scoreText(null)") == "n/a"
    assert run("D.percentText(12.4)") == "12%" and run("D.percentText(null)") == "0%"


def test_a_ring_is_a_stroke_of_the_score_over_the_circle():
    r = run("D.ring(25, 10)")
    assert r["circumference"] == pytest.approx(62.8318, rel=1e-4) and r["dash"] == pytest.approx(15.7079, rel=1e-4) and r["gap"] == pytest.approx(47.1238, rel=1e-4)
    assert run("D.ring(null, 10)")["dash"] == 0 and run("D.ring(150, 10)")["gap"] == pytest.approx(0, abs=1e-9) and run("D.ring(-5, 10)")["dash"] == 0


def test_time_since_a_read_is_said_in_words():
    now = 1_000_000_000_000
    iso = lambda minutes: __import__("datetime").datetime.fromtimestamp((now - minutes * 60_000) / 1000, __import__("datetime").timezone.utc).isoformat()
    assert [run("D.ago(i, n)", i=iso(m), n=now) for m in (0, 1, 59, 60, 125, 1440, 3000)] == ["just now", "1 minutes ago", "59 minutes ago", "1 hour ago", "2 hours ago", "1 day ago", "2 days ago"]
    assert run('D.ago("", 1)') == "" and run('D.ago("not a date", 1)') == "" and run("D.plural(1, 'day') + '|' + D.plural(0, 'day')") == "1 day|0 days"


def test_a_signed_change_reads_plus_minus_or_nothing():
    assert [run("D.signed(n)", n=n) for n in (12, -3, 0, None)] == ["+12", "-3", "0", ""]


def test_the_change_tiles_say_what_moved_since_a_saved_audit():
    c = {"scoreChange": 12, "scoreWas": 60, "gradeWas": "C", "scoreNow": 72, "gradeNow": "B", "counts": {"fixed": 2, "improved": 1, "worse": 0, "newProblems": 2}, "stillFailing": 1, "stillWarning": 1}
    t = {x["key"]: x for x in run("D.changeTiles(c)", c=c)}
    assert t["score"]["value"] == "+12" and t["score"]["tone"] == "good" and t["score"]["hint"] == "was 60 (C), now 72 (B)"
    assert t["fixed"]["value"] == 2 and t["fixed"]["tone"] == "good" and t["fixed"]["hint"] == "1 more got better"
    assert t["newProblems"]["value"] == 2 and t["newProblems"]["tone"] == "bad" and t["worse"]["value"] == 0 and t["worse"]["tone"] == "good" and t["worse"]["hint"] == "none"
    assert t["still"]["value"] == 1 and t["still"]["tone"] == "warn" and t["still"]["hint"] == "1 still warning"
    worse = {x["key"]: x for x in run("D.changeTiles(c)", c=dict(c, scoreChange=-5, counts={"fixed": 0, "improved": 0, "worse": 1, "newProblems": 0}, stillFailing=0, gradeNow=None, scoreWas=None))}
    assert worse["score"]["tone"] == "bad" and worse["score"]["value"] == "-5" and worse["score"]["hint"] == "no score then, now 72" and worse["worse"]["tone"] == "bad"
    assert worse["fixed"]["tone"] == "unk" and worse["fixed"]["hint"] == "checks that now pass" and worse["still"]["tone"] == "good"
    none = {x["key"]: x for x in run("D.changeTiles(c)", c={"scoreChange": None, "scoreNow": None})}
    assert none["score"]["value"] == "n/a" and none["score"]["tone"] == "unk" and none["score"]["hint"] == "no score then, now n/a"
    same = {x["key"]: x for x in run("D.changeTiles(c)", c=dict(c, scoreChange=0))}
    assert same["score"]["value"] == "0" and same["score"]["tone"] == "unk"


def test_the_changed_checks_are_listed_worst_news_first_and_empty_groups_are_left_out():
    c = {"newProblems": [{"id": "A"}], "worse": [], "fixed": [{"id": "B"}, {"id": "C"}], "improved": [{"id": "D"}]}
    assert run("D.changeGroups(c).map(g=>[g[0], g[1].length, g[2]])", c=c) == [["New problems", 1, "bad"], ["Put right", 2, "good"], ["Got better", 1, "good"]]
    assert run("D.changeGroups(c)", c={}) == []


def test_a_date_reads_in_words_and_a_bad_one_reads_as_nothing():
    assert run('D.dateText("2026-07-17T10:00:00+00:00")') in ("Jul 17, 2026", "Jul 16, 2026") and run('D.dateText("")') == "" and run('D.dateText("nope")') == ""


def test_the_headline_tiles_say_what_matters_and_stay_calm_when_nothing_does():
    s = {"critical": 2, "high": 1, "judged": 40, "unknownShare": 8, "tally": {"fail": 3, "warn": 5, "pass": 30, "na": 4, "unknown": 3}}
    t = {x["key"]: x for x in run("D.tiles(s)", s=s)}
    assert t["critical"]["value"] == 2 and t["critical"]["tone"] == "bad" and "untrustworthy" in t["critical"]["hint"]
    assert t["high"]["tone"] == "bad" and t["warn"]["value"] == 5 and t["warn"]["tone"] == "warn" and t["pass"]["hint"] == "of 40 judged"
    assert t["unknown"]["value"] == 3 and t["unknown"]["tone"] == "unk" and t["unknown"]["hint"] == "8% of the checks"
    calm = {x["key"]: x for x in run("D.tiles(s)", s={"critical": 0, "high": 0, "judged": 10, "unknownShare": 0, "tally": {"fail": 0, "warn": 0, "pass": 10, "na": 0, "unknown": 0}})}
    assert all(calm[k]["tone"] == "good" for k in ("critical", "high", "warn", "pass", "unknown")) and calm["unknown"]["hint"] == "Everything was read" and calm["critical"]["hint"] == "None"


def test_the_accounts_read_are_named_with_their_platform():
    assert run("D.readNote(r)", r={"accounts": [{"name": "Acme", "platform": "google"}, {"name": "Acme Meta", "platform": "meta"}]}) == "Acme (Google Ads) and Acme Meta (Meta Ads)"
    assert run("D.readNote(r)", r={"accounts": []}) == ""


def test_every_result_and_severity_the_server_can_send_has_a_label_and_the_order_is_worst_first():
    for r in ("fail", "warn", "unknown", "pass", "na"):
        assert run("D.RESULT[r].label", r=r)
    for s in ("critical", "high", "medium", "low"):
        assert run("D.SEVERITY[s].label", s=s)
    assert run("Object.keys(D.RESULT).sort((a,b)=>D.RESULT[a].rank-D.RESULT[b].rank)") == ["fail", "warn", "unknown", "pass", "na"]
    assert run("Object.keys(D.SEVERITY).sort((a,b)=>D.SEVERITY[a].rank-D.SEVERITY[b].rank)") == ["critical", "high", "medium", "low"]


def test_the_server_and_the_page_agree_on_the_results_and_severities():
    from tracker import paid_audit_model as model
    import tracker.paid_audit_export as ex
    assert set(run("Object.keys(D.RESULT)")) == set(model.RESULTS) and set(run("Object.keys(D.SEVERITY)")) == set(model.SEVERITIES)
    assert {k: run("D.RESULT[k].label", k=k) for k in model.RESULTS} == ex.RESULT_TEXT                       # the files and the page say the same words
    assert {k: run("D.SEVERITY[k].label", k=k) for k in model.SEVERITIES} == ex.SEVERITY_TEXT and run("D.PLATFORM") == ex.PLATFORM_TEXT


# ── The page against its template ───────────────────────────────────────────

def test_the_page_script_is_valid_javascript():
    r = subprocess.run(["node", "--check", PAGE], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["node", "--check", DATA], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def test_every_element_the_page_script_reaches_for_is_in_the_template():
    js, html = open(PAGE, encoding="utf-8").read(), open(TEMPLATE, encoding="utf-8").read()
    ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))
    assert ids >= {"acctPick", "msg", "empty", "skel", "out", "fresh", "stale", "score", "tiles", "fix", "fixNote", "sections", "notes", "notesCard", "foot", "refreshBtn", "dlXlsx", "dlPdf", "dlZip",
                   "saveBtn", "saveNote", "sinceCard", "sinceNote", "sinceTiles", "sinceLists", "histCard", "hist", "briefBtn", "briefOut"}
    for i in sorted(ids - {"sec-"}):
        assert 'id="%s"' % i in html, i
    for attr in ("data-toggle", "data-jump", "data-view", "data-more", "data-find", "data-compare", "data-remove"):
        assert attr in js, attr


def test_the_page_loads_its_scripts_in_order_and_its_stylesheet():
    html = open(TEMPLATE, encoding="utf-8").read()
    order = [html.index(s) for s in ("js/pm_picker.js", "js/pm_platform.js", "js/au_data.js", "js/account_audit.js")]
    assert order == sorted(order) and "css/account_audit.css" in html
    assert "root.AuditData=api" in open(DATA, encoding="utf-8").read() and "window.AuditData" in open(PAGE, encoding="utf-8").read()


def test_there_is_no_em_or_en_dash_in_any_of_the_page():
    for p in (DATA, PAGE, TEMPLATE, os.path.join(ROOT, "static", "css", "account_audit.css")):
        text = open(p, encoding="utf-8").read()
        assert "—" not in text and "–" not in text, p
