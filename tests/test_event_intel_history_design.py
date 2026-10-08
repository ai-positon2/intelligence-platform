"""The Your runs rail (2026-10-08): what kind of run each row is, when it
happened, filtered by play, and a run started here joining the list at once.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

import app as appmod
from tracker import event_intel_store as ST

ROOT = os.path.join(os.path.dirname(__file__), "..")
TEMPLATE = os.path.join(ROOT, "templates", "event_conference_intelligence.html")
CSS = os.path.join(ROOT, "static", "css", "event_intel_intake.css")
_PAGE = "/p2/strategic-agents/event-conference-intelligence"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page script")


def _run(i, mode, status="complete", n=0, when="2026-10-04T10:00:00+00:00", name=None):
    return {"id": i, "mode": mode, "query": "q%d" % i, "status": status, "participant_count": n,
            "credits_spent": 0, "created_at": when, "event_name": name or "Run %d" % i}


RUNS = [_run(1, "recommend"), _run(2, "lookup", n=40), _run(3, "lookup", "running"),
        _run(4, "workroom"), _run(5, "discover"), _run(6, "lookup", "failed"),
        _run(7, "recommend", name="RSA Conference")]


def _page(monkeypatch, runs=RUNS):
    monkeypatch.setattr(ST, "list_runs", lambda e, limit=60: list(runs))
    monkeypatch.setattr(ST, "list_profiles", lambda e, limit=40: [])
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["google_user"] = {"email": "harness@position2.com", "name": "T"}
    r = c.get(_PAGE)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _script():
    html = open(TEMPLATE).read()
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)[0]


def _fn(src, name):
    start = src.index("    function %s(" % name)
    return src[start:src.index("\n    }\n", start) + 7]


def _node(code):
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── rendered ────────────────────────────────────────────────────────────────

def test_the_filters_are_the_counts_by_play(monkeypatch):
    html = _page(monkeypatch)
    got = dict((k, int(n)) for k, n in re.findall(
        r'class="hf[^"]*" data-filter="(\w+)"[^>]*><b>(\d+)</b>', html))
    assert got == {"all": 7, "recommend": 2, "lookup": 3, "workroom": 1}


def test_a_play_with_no_runs_cannot_be_filtered_to(monkeypatch):
    html = _page(monkeypatch, [_run(1, "lookup")])
    assert re.search(r'data-filter="recommend" onclick="[^"]+" disabled', html)
    assert not re.search(r'data-filter="lookup" onclick="[^"]+" disabled', html)


def test_search_appears_only_once_the_list_is_long(monkeypatch):
    assert 'id="runsSearch"' in _page(monkeypatch)
    assert 'id="runsSearch"' not in _page(monkeypatch, RUNS[:6])
    assert 'class="hfilter"' not in _page(monkeypatch, [])


def test_each_row_carries_its_play_icon_status_and_time(monkeypatch):
    html = _page(monkeypatch)
    rows = re.findall(r'<button class="evi-run m-(\w+)"(.*?)</button>', html, re.S)
    assert len(rows) == 7
    for mode, body in rows:
        assert 'data-mode="%s"' % mode in body
        assert re.search(r'data-status="(complete|running|failed)"', body)
        assert 'data-created="2026-10-04T10:00:00+00:00"' in body
        assert re.search(r'class="ri"[^>]*><svg[^>]*>.+?</svg>', body, re.S)
    rsa = [b for m, b in rows if "RSA Conference" in b][0]
    assert 'data-q="rsa conference q7"' in rsa


# ── when, in the reader's own time ──────────────────────────────────────────

@node
@pytest.mark.parametrize("when,now,group,time", [
    ("2026-10-08T09:50:00", "2026-10-08T10:00:00", "Today", "10 min ago"),
    ("2026-10-08T09:59:40", "2026-10-08T10:00:00", "Today", "just now"),
    ("2026-10-08T09:57:00", "2026-10-08T10:00:00", "Today", "3 min ago"),
    ("2026-10-08T07:05:00", "2026-10-08T10:00:00", "Today", "7:05"),
    ("2026-10-07T23:30:00", "2026-10-08T00:10:00", "Yesterday", "23:30"),
    ("2026-10-04T10:00:00", "2026-10-08T10:00:00", "This week", "Oct 4"),
    ("2026-09-13T10:00:00", "2026-10-08T10:00:00", "September", "Sep 13"),
    ("2025-12-30T10:00:00", "2026-01-08T10:00:00", "December 2025", "Dec 30"),
])
def test_runs_are_grouped_by_day_and_timed_like_a_person_would(when, now, group, time):
    src = _script()
    code = "var RUN_DAY = 86400000;\n" + _fn(src, "runGroup") + _fn(src, "runTime") + """
var w = new Date(%s), n = new Date(%s), g = runGroup(w, n);
process.stdout.write(JSON.stringify([g, runTime(w, g, n)]));""" % (json.dumps(when), json.dumps(now))
    assert _node(code) == [group, time]


# ── filtering ───────────────────────────────────────────────────────────────

_DOM = """
function el(cls, attrs){
  var a = attrs || {};
  return {hidden: false, _cls: cls, getAttribute: function(k){ return a[k] === undefined ? null : a[k]; },
          classList: {contains: function(c){ return cls.split(' ').indexOf(c) !== -1; },
                      toggle: function(){}}, setAttribute: function(){}};
}
var kids = [el('hg'), el('evi-run', {'data-mode': 'lookup', 'data-q': 'web summit'}),
            el('evi-run', {'data-mode': 'recommend', 'data-q': 'cvent'}),
            el('hg'), el('evi-run', {'data-mode': 'lookup', 'data-q': 'rsa conference'})];
var search = {value: SEARCH}, none = {hidden: true};
var list = {children: kids, querySelectorAll: function(){ return kids.filter(function(k){ return k._cls === 'evi-run'; }); }};
global.document = {getElementById: function(id){ return {runsList: list, runsSearch: search, runsNone: none}[id] || null; },
                   querySelectorAll: function(){ return []; }};
"""


@node
@pytest.mark.parametrize("kind,search,shown,headings,none", [
    ("all", "", [True, True, True], [True, True], True),
    ("lookup", "", [True, False, True], [True, True], True),
    ("recommend", "", [False, True, False], [True, False], True),
    ("all", "RSA", [False, False, True], [False, True], True),
    ("workroom", "", [False, False, False], [False, False], False),
])
def test_filtering_hides_rows_and_the_headings_left_empty(kind, search, shown, headings, none):
    src = _script()
    code = _DOM.replace("SEARCH", json.dumps(search)) + "var runFilter = 'all';\n" + _fn(src, "filterRuns") + """
filterRuns(%s);
process.stdout.write(JSON.stringify({rows: kids.filter(function(k){ return k._cls === 'evi-run'; }).map(function(k){ return !k.hidden; }),
  heads: kids.filter(function(k){ return k._cls === 'hg'; }).map(function(k){ return !k.hidden; }), none: none.hidden}));
""" % json.dumps(kind)
    got = _node(code)
    assert (got["rows"], got["heads"], got["none"]) == (shown, headings, none)


# ── a run started here ──────────────────────────────────────────────────────

@node
@pytest.mark.parametrize("mode,body,title", [
    ("lookup", {"query": "Web Summit"}, "Web Summit"),
    ("workroom", {}, "Money20/20 USA"),
    ("recommend", {}, "Cvent"),
])
def test_a_new_run_is_named_the_way_the_list_names_it(mode, body, title):
    src = _script()
    code = """
var opts = {sourceRun: 'Money20/20 USA \\u00b7 247 listed', profilePick: 'Cvent \\u00b7 B2B, selling to marketing'};
global.document = {getElementById: function(id){
  return opts[id] ? {selectedIndex: 0, options: [{textContent: opts[id]}]} : null; }};
""" + _fn(src, "runTitle") + "process.stdout.write(JSON.stringify(runTitle(%s, %s)));" % (
        json.dumps(mode), json.dumps(body))
    assert _node(code) == title


def test_a_started_run_joins_the_list_and_takes_its_final_status():
    src = _script()
    start = src[src.index("    function startRun(e){"):src.index("    window.startRun = startRun;")]
    assert "addRunRow(res.j.run_id, mode, runTitle(mode, body))" in start
    watch = src[src.index("    function watch(runId, mode){"):src.index("    function readingMode(on){")]
    done = watch[watch.index("if (s.status === 'complete' || s.status === 'failed') {"):]
    assert done.index("setRunRowStatus(runId, s.status, s.needs_pick)") < done.index("openRun(runId)")
    row = _fn(src, "addRunRow")
    for attr in ("data-run", "data-mode", "data-status", "data-created", "data-q"):
        assert "'%s'" % attr in row, attr
    assert "decorateRuns()" in row and "evi-runcount" in row
    # A new account has no count yet; the first run gives it one.
    assert "count = document.createElement('span')" in row


# ── the stylesheet ──────────────────────────────────────────────────────────

def test_every_play_has_its_colour_in_both_themes():
    css = open(CSS).read()
    for mode in ("recommend", "lookup", "workroom", "discover"):
        assert re.search(r"(?m)^\.evi-run\.m-%s\b[^{]*\{ --rc:" % mode, css), mode
        assert re.search(r'(?m)^:root\[data-theme="light"\] \.evi-run\.m-%s\b[^{]*\{ --rc:' % mode, css), mode
