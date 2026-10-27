"""The alert panel the monitors share (static/js/monitor_panel.js): its wording and shapes run in Node, and the panel itself against a stub page and the state the server builds."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "static", "js", "monitor_panel.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(PANEL), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_senders_and_the_recipients_are_said_in_words():
    assert run("X.sendersText(S)", S={"email": True, "slack": False}) == "Email is set up · Slack is not set up on this server"
    assert run("X.sendersText(S)", S={}) == "Email is not set up on this server · Slack is not set up on this server" and run("X.sendersText(null)").startswith("Email is not set up")
    assert run("X.recipientsText(A)", A={"emails": [], "slackChannel": ""}) == "Nobody is alerted yet: add an email address or a Slack channel."
    assert run("X.recipientsText(A)", A={"emails": ["a@b.co"], "slackChannel": ""}) == "Alerts go to 1 email address and no Slack channel."
    assert run("X.recipientsText(A)", A={"emails": ["a@b.co", "c@d.co"], "slackChannel": "#ppc"}) == "Alerts go to 2 email addresses and the Slack channel #ppc."
    assert run("X.recipientsText(A)", A={"emails": [], "slackChannel": "#ppc"}) == "Alerts go to no email address and the Slack channel #ppc."


def test_where_the_monitor_stands_is_one_line():
    assert run("X.standing(S)", S={"baseline": None, "runs": []}).startswith("It has not run yet. The first run sets the baseline")
    run1 = {"status": "done", "finishedAt": "2026-10-21T07:01:12+00:00", "trigger": "schedule"}
    s = {"baseline": {"at": "2026-10-20T07:00:00+00:00", "by": "schedule"}, "runs": [run1], "openTotal": 4, "counts": {"high": 1}, "fixed": [1, 2]}
    assert run("X.standing(S)", S=s) == "Baseline set 2026-10-20 · last run 2026-10-21 07:01 UTC (schedule) · 4 issues open (1 high), 2 fixed lately."
    s2 = dict(s, runs=[dict(run1, trigger="page by ana@position2.com")], openTotal=1, counts={"high": 0}, fixed=[1])
    assert run("X.standing(S)", S=s2) == "Baseline set 2026-10-20 · last run 2026-10-21 07:01 UTC (from the page) · 1 issue open, 1 fixed lately."
    assert run("X.standing(S)", S=dict(s, runs=[dict(run1, status="failed")])).find("the last run (2026-10-21 07:01 UTC) failed") > 0
    assert run("X.standing(S)", S={"baseline": None, "runs": [run1], "openTotal": 0, "counts": {}, "fixed": []}) == "last run 2026-10-21 07:01 UTC (schedule) · 0 issues open, 0 fixed lately."


def test_a_run_is_a_line_with_what_it_found_and_what_went_wrong():
    base = {"status": "done", "finishedAt": "2026-10-21T07:01:12+00:00", "accounts": 12, "read": 12, "new": 0, "worse": 0, "reminders": 0, "resolved": 0, "alerted": 0, "baseline": True, "unreadableCount": 0, "email": None, "slack": None}
    assert run("X.runLine(R)", R=base) == {"when": "2026-10-21 07:01 UTC", "tone": "ok", "text": "read 12 of 12 accounts · nothing new · none alerted"}
    busy = dict(base, new=2, worse=1, reminders=1, resolved=3, alerted=3, unreadableCount=1)
    assert run("X.runLine(R)", R=busy) == {"when": "2026-10-21 07:01 UTC", "tone": "warn", "text": "read 12 of 12 accounts · 2 new · 1 worse · 1 reminder · 3 fixed · 3 issues alerted · 1 not read"}
    assert run("X.runLine(R)", R=dict(base, new=1, alerted=0, email={"status": "failed"}))["tone"] == "warn"
    assert "nothing new" not in run("X.runLine(R)", R=dict(base, worse=1, alerted=1))["text"] and "nothing new" not in run("X.runLine(R)", R=dict(base, reminders=1, alerted=1))["text"] and "nothing new" not in run("X.runLine(R)", R=dict(base, reminders=1))["text"]
    assert "nothing new" not in run("X.runLine(R)", R=dict(base, alerted=1))["text"] and "nothing new" not in run("X.runLine(R)", R=dict(base, baseline=False))["text"]
    assert run("X.runLine(R)", R=dict(base, new=1, alerted=1))["text"] == "read 12 of 12 accounts · 1 new · 1 issue alerted"
    held = run("X.runLine(R)", R=dict(base, new=2, alerted=1, heldBack=1, belowFloor=0))["text"]
    assert held == "read 12 of 12 accounts · 2 new · 1 issue alerted · 1 held back (another issue covers it)"
    assert run("X.runLine(R)", R=dict(base, new=1, alerted=0, heldBack=0, belowFloor=1))["text"] == "read 12 of 12 accounts · 1 new · none alerted · 1 under a client's floor"
    assert "held back" not in run("X.runLine(R)", R=dict(base, new=1, alerted=1, heldBack=0, belowFloor=0))["text"]
    assert run("X.runLine(R)", R={"status": "failed", "finishedAt": "2026-10-21T07:01:12+00:00", "error": "RuntimeError: x"}) == {"when": "2026-10-21 07:01 UTC", "tone": "bad", "text": "Failed: RuntimeError: x"}
    assert run("X.runLine(R)", R={"status": "failed", "finishedAt": ""})["text"] == "Failed: no reason given"


def test_the_age_of_an_issue_and_how_often_it_came_back():
    assert [run("X.ageText(I)", I=i) for i in ({"ageDays": 0, "reopened": 0}, {"ageDays": 1, "reopened": 0}, {"ageDays": 9, "reopened": 2}, {"ageDays": None, "reopened": 1}, {"ageDays": None, "reopened": 0})] == [
        "since today", "1 day", "9 days, came back 2 times", "came back 1 time", ""]


def test_levels_and_dates():
    assert run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('zzz')") == {"label": "zzz", "tone": "unk"}
    assert run("[X.day('2026-10-21T07:01:12+00:00'), X.day(null), X.when(null), X.plural(1,'a'), X.plural(2,'a'), X.plural(2,'x','y')]") == ["2026-10-21", "", "", "1 a", "2 as", "2 y"]


# ── The panel ───────────────────────────────────────────────────────────────

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const sc = JSON.parse(process.argv[2]);
const els = {}, calls = [];
function el(sel) { if (!els[sel]) els[sel] = { sel, value: (sc.values || {})[sel] || "" }; return els[sel]; }
const host = { innerHTML: "", handlers: {}, addEventListener(ev, fn) { this.handlers[ev] = fn; } };
const doc = { querySelector: s => el(s) };
const answers = sc.answers || {};
function respond(url, o) {
  const key = (o && o.method || "GET") + " " + url.replace("/p2/paid-marketing/api/monitors", "");
  calls.push({ key, body: o && o.body ? JSON.parse(o.body) : null });
  const a = answers[key];
  const hit = Array.isArray(a) ? a[Math.min(calls.filter(c => c.key === key).length - 1, a.length - 1)] : a;
  return hit || { status: 404, body: { error: "no stub for " + key } };
}
const timers = [];
const ctx = { window: {}, document: doc, setTimeout: (fn, ms) => { timers.push(ms); }, console, JSON, Object, Array, String, Promise,
  fetch: (url, o) => Promise.resolve(respond(url, o)).then(r => ({ ok: (r.status || 200) < 400, status: r.status || 200, json: () => Promise.resolve(r.body) })) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  ctx.window.MonitorPanel.mount(host, { agent: "brandspend", label: "Brand Spend Alert" });
  for (let i = 0; i < 6; i++) await tick();
  const snaps = [host.innerHTML];
  for (const step of sc.steps || []) {
    if (step.set) el(step.set[0]).value = step.set[1];
    host.handlers.click({ target: { closest: () => ({ id: step.click }) } });
    for (let i = 0; i < 8; i++) await tick();
    snaps.push(host.innerHTML);
  }
  process.stdout.write(JSON.stringify({ html: host.innerHTML, snaps, calls, timers }));
})();
"""


def panel(**sc):
    r = subprocess.run(["node", "-e", HARNESS, PANEL, json.dumps(sc)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def state(**kw):
    s = {"agent": "brandspend", "label": "Brand Spend Alert", "alerts": {"emails": ["a@b.co"], "slackChannel": "#ppc"}, "senders": {"email": True, "slack": False}, "baseline": {"at": "2026-10-20T07:00:00+00:00", "by": "schedule"},
         "runs": [{"status": "done", "finishedAt": "2026-10-21T07:01:12+00:00", "trigger": "schedule", "accounts": 3, "read": 3, "new": 1, "worse": 0, "reminders": 0, "resolved": 0, "alerted": 1, "baseline": True, "unreadableCount": 0}],
         "counts": {"high": 1, "medium": 0, "low": 0}, "open": [{"level": "high", "accountName": "Acme <Dental>", "platform": "google", "title": "Brand spend stopped", "ageDays": 3, "reopened": 1, "alertedAt": "2026-10-19T07:00:00+00:00"},
                                                           {"level": "low", "accountName": "Beta", "platform": "meta", "title": "Small", "ageDays": 0, "reopened": 0, "alertedAt": None}],
         "openTotal": 2, "fixed": [{"accountName": "Gamma", "title": "Over", "resolvedAt": "2026-10-20T07:00:00+00:00", "ageDays": 4}], "digest": "2026-10-19", "running": False}
    s.update(kw)
    return s


def test_the_panel_draws_the_recipients_the_senders_the_open_issues_the_fixed_ones_and_the_runs():
    out = panel(answers={"GET /brandspend/state": {"body": state()}})
    h = out["html"]
    assert 'value="a@b.co"' in h and 'value="#ppc"' in h and "Alerts go to 1 email address and the Slack channel #ppc." in h and "Email is set up · Slack is not set up on this server" in h and "(last 2026-10-19)" in h
    assert "Acme &lt;Dental&gt;" in h and "<Dental>" not in h and "Brand spend stopped" in h and "3 days, came back 1 time" in h and ">2026-10-19<" in h and ">not yet<" in h and 'class="bi-mk">Meta' in h and "since today" in h
    assert "Open issues <span class=\"dim\">(2)</span>" in h and "Fixed lately" in h and "fixed 2026-10-20, open 4 days" in h and "read 3 of 3 accounts · 1 new · 1 issue alerted" in h and "Baseline set 2026-10-20" in h
    assert ">Run now<" in h and out["calls"] == [{"key": "GET /brandspend/state", "body": None}]


def test_a_quiet_monitor_says_nothing_is_open_and_one_that_has_not_run_says_so():
    h = panel(answers={"GET /brandspend/state": {"body": state(open=[], openTotal=0, fixed=[], runs=[], baseline=None, counts={})}})["html"]
    assert "Nothing is open." in h and "It has not run yet." in h and "Last runs" not in h and "Fixed lately" not in h
    many = panel(answers={"GET /brandspend/state": {"body": state(openTotal=55)}})["html"]
    assert "The first 2 of 55." in many


def test_the_panel_says_when_the_state_cannot_be_read():
    out = panel(answers={"GET /brandspend/state": {"status": 503, "body": {"error": "The Paid Marketing store is unavailable: x"}}})
    assert "The Paid Marketing store is unavailable: x" in out["html"] and "Alerts and history" in out["html"]
    assert "Run now" not in out["html"]


def test_saving_sends_what_was_typed_reloads_and_says_saved_or_why_not():
    answers = {"GET /brandspend/state": [{"body": state()}, {"body": state(alerts={"emails": ["x@y.co"], "slackChannel": ""})}], "POST /alerts": {"body": {"alerts": {}}}}
    out = panel(answers=answers, values={"#mpEmails": "x@y.co", "#mpSlack": ""}, steps=[{"click": "mpSave"}])
    assert out["calls"][1] == {"key": "POST /alerts", "body": {"emails": "x@y.co", "slackChannel": ""}} and out["calls"][2]["key"] == "GET /brandspend/state" and 'value="x@y.co"' in out["html"] and "Saved." in out["html"]
    bad = panel(answers={"GET /brandspend/state": {"body": state()}, "POST /alerts": {"status": 400, "body": {"error": "nope is not an email address."}}}, values={"#mpEmails": "nope"}, steps=[{"click": "mpSave"}])
    assert "nope is not an email address." in bad["html"] and len(bad["calls"]) == 2


def test_a_test_alert_says_what_each_channel_did_and_why_one_did_not():
    ok = panel(answers={"GET /brandspend/state": {"body": state()}, "POST /brandspend/test-alert": {"body": {"sent": True, "email": {"status": "sent"}, "slack": {"status": "not_configured"}}}}, steps=[{"click": "mpTest"}])
    assert "A test alert was sent (email: sent, Slack: not_configured)." in ok["html"]
    no = panel(answers={"GET /brandspend/state": {"body": state()}, "POST /brandspend/test-alert": {"body": {"sent": False, "email": {"status": "failed", "error": "smtp down"}, "slack": {"status": "not_configured", "error": "No Slack channel is set."}}}}, steps=[{"click": "mpTest"}])
    assert "Nothing was sent. Email: failed (smtp down). Slack: not_configured (No Slack channel is set.)." in no["html"]
    bad = panel(answers={"GET /brandspend/state": {"body": state()}, "POST /brandspend/test-alert": {"status": 500, "body": {"error": "boom"}}}, steps=[{"click": "mpTest"}])
    assert "boom" in bad["html"]


def test_run_now_starts_a_run_says_so_and_looks_again_shortly_and_a_run_that_is_going_is_followed():
    out = panel(answers={"GET /brandspend/state": {"body": state()}, "POST /brandspend/run": {"status": 202, "body": {"started": True}}}, steps=[{"click": "mpRun"}])
    assert "The run started. It takes a minute or two; this page updates when it is done." in out["html"] and out["timers"] == [3000]
    busy = panel(answers={"GET /brandspend/state": {"body": state()}, "POST /brandspend/run": {"status": 409, "body": {"error": "A run is already going."}}}, steps=[{"click": "mpRun"}])
    assert "A run is already going." in busy["html"] and busy["timers"] == []
    going = panel(answers={"GET /brandspend/state": {"body": state(running=True)}})
    assert "Running…" in going["html"] and 'id="mpRun" disabled' in going["html"] and going["timers"] == [4000]
