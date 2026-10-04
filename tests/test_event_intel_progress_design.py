"""The run-in-progress panel (2026-10-04): one panel, in the form and in the
drawer, kept current by the same code.

The stepper's stages are checked against the stages the pipeline actually
writes, because a step the backend never reports is a step that never lights
up, and a stage with no label printed its raw key ("finding_attendees") on
every roster run.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

import app as appmod

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_charts import _cand, _lookup, _recommend, _render, _workroom, _part, _src, _out  # noqa: E402
from test_event_intel_event_view import page_script  # noqa: E402,F401  (fixture)

ROOT = os.path.join(os.path.dirname(__file__), "..")
TEMPLATE = os.path.join(ROOT, "templates", "event_conference_intelligence.html")
CSS = os.path.join(ROOT, "static", "css", "event_intel_intake.css")

node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page script")


def _script():
    html = open(TEMPLATE).read()
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)[0]


def _block(src, start, end):
    return src[src.index(start):src.index(end, src.index(start))]


def _progress_js():
    """The stage tables and the progress functions, as the page has them."""
    src = _script()
    esc = src[src.index("    function esc(s){"):src.index("\n    }\n", src.index("    function esc(s){")) + 7]
    tables = _block(src, "    var STAGES = {", "    /* The run in progress, drawn")
    funcs = _block(src, "    /* The run in progress, drawn", "    function watch(runId, mode){")
    return "var window = {};\n" + esc + tables + funcs


def _node(code):
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _pipeline_stages():
    found = set()
    for f in os.listdir(os.path.join(ROOT, "tracker")):
        if f.startswith("event_intel_") and f.endswith(".py"):
            text = open(os.path.join(ROOT, "tracker", f)).read()
            found |= set(re.findall(r"""stage=["']([a-z_]+)["']""", text))
    return found


@node
def test_every_step_is_a_stage_the_pipeline_reports_and_has_a_label():
    got = _node(_progress_js() + "\nprocess.stdout.write(JSON.stringify({order: STAGE_ORDER, short: SHORT, stages: STAGES}));")
    written = _pipeline_stages()
    for mode, order in got["order"].items():
        for st in order:
            assert st in written, (mode, st)
            assert got["short"].get(st), (mode, st)
            assert got["stages"].get(st), (mode, st)


@node
@pytest.mark.parametrize("mode,stage,pct,kick", [
    ("recommend", None, 3, "Starting"),
    ("recommend", "queued", 3, "Queued"),
    ("recommend", "discovering_categories", 13, "Step 1 of 4"),
    ("recommend", "ranking", 88, "Step 4 of 4"),
    ("lookup", "finding_attendees", 83, "Step 3 of 3"),
    ("workroom", "done", 100, "Finishing"),
])
def test_the_bar_and_the_kicker_say_where_the_run_is(mode, stage, pct, kick):
    got = _node(_progress_js() + "\nprocess.stdout.write(JSON.stringify(progressState(%s, %s)));"
                % (json.dumps(mode), json.dumps(stage)))
    assert (got["pct"], got["kick"]) == (pct, kick)


@node
def test_the_steps_mark_off_done_now_and_left():
    got = _node(_progress_js() + "\nprocess.stdout.write(JSON.stringify(stepsHtml('lookup', 'harvesting')));")
    states = re.findall(r'<li class="(\w+)">', got)
    assert states == ["done", "now", "pending"]
    assert re.findall(r'class="tx">([^<]+)', got) == ["Identify the event", "Read the published pages",
                                                       "Find the people going"]
    assert got.count("In progress") == 1


@node
@pytest.mark.parametrize("ms,text", [(0, "0:00"), (754000, "12:34"), (3723000, "1:02:03"), (-5000, "0:00")])
def test_the_clock_reads_like_a_clock(ms, text):
    assert _node(_progress_js() + "\nprocess.stdout.write(JSON.stringify(fmtElapsed(%d)));" % ms) == text


def test_the_status_route_says_when_the_run_started(monkeypatch):
    from tracker import event_intel_store as ST
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(ST, "get_run", lambda rid, email: {
        "id": rid, "status": "running", "stage": "scoring", "credits_spent": 0,
        "created_at": "2026-10-04T10:00:00+00:00"})
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["google_user"] = {"email": "harness@position2.com", "name": "T"}
    r = c.get("/p2/strategic-agents/event-conference-intelligence/runs/9/status")
    assert r.status_code == 200
    assert r.get_json()["started_at"] == "2026-10-04T10:00:00+00:00"


def _running(run, stage):
    run = dict(run, status="running", stage=stage, created_at="2026-10-04T10:00:00+00:00")
    return run


@pytest.mark.parametrize("make,stage,kick,now", [
    (lambda: _recommend([_cand("A", 80, "P1")]), "auditing", "Scoring the calendar · Step 2 of 4",
     "Audit the famous names"),
    (lambda: _lookup([_part("Acme", "exhibitor")], [_src("https://e.example/x", "ok")]), "harvesting",
     "Building the roster · Step 2 of 3", "Read the published pages"),
    (lambda: _workroom([_out("Acme", 80)]), "qualifying", "Working the room · Step 2 of 4",
     "Qualify and draft"),
])
def test_a_running_run_opened_in_the_drawer_shows_the_same_panel(page_script, make, stage, kick, now):
    body = _render(page_script, _running(make(), stage))
    assert 'class="evi-prog in-drawer"' in body
    assert re.search(r'id="drawerLiveKick">%s<' % re.escape(kick), body)
    assert re.search(r'<li class="now"><span class="dot">2</span><span class="tx">%s<' % re.escape(now), body)
    assert 'id="drawerLiveClock" data-since="2026-10-04T10:00:00+00:00"' in body


def test_the_drawer_watch_repaints_every_play_not_only_the_roster():
    src = _script()
    watch = _block(src, "    function watchDrawer(runId, mode){", "    window.watchDrawer")
    assert "paintProgress('drawerLive', mode, s.stage, s.started_at)" in watch
    # The three running branches all draw the shared panel.
    assert src.count("progressShell('drawerLive',") == 3


def test_the_form_gives_way_to_the_run_and_comes_back_on_failure():
    src = _script()
    run = _block(src, "    function startRun(e){", "    window.startRun = startRun;")
    assert "showRunning(true)" in run and "showRunning(false)" in run
    css = open(CSS).read()
    assert "#formCard.is-running .evi-form { display: none; }" in css
    lost = _block(src, "'Lost contact with the run.", "return;")
    assert "classList.remove('is-running')" in lost


def test_the_progress_panel_is_themed_both_ways():
    css = open(CSS).read()
    dark = re.search(r"^\.evi-prog \{\s*(--g-ink:.*?)\}", css, re.S | re.M).group(1)
    light = re.search(r':root\[data-theme="light"\] \.evi-prog \{\s*(--g-ink:.*?)\}', css, re.S).group(1)
    assert set(re.findall(r"--[\w-]+", dark)) == set(re.findall(r"--[\w-]+", light))
    for v in set(re.findall(r"var\((--g-[\w-]+)", css)):
        assert v in dark, v
