"""The Alert Hub: static/js/alert_hub_data.js run in Node, and the page script run against a stub page and the real answers the server gives.

The server decides (tests/test_paid_alerthub*.py). The page only words, filters and draws it, so the shapes come from alert_hub_data.js (executed here, not read), the state is the server's own
`view` of a store with real issues in it, and alert_hub.js is run for real against a stub page.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from test_master_agent_js import HARNESS  # noqa: E402
from tracker import paid_alerthub as hub  # noqa: E402
from tracker import paid_master as m  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "alert_hub_data.js"), os.path.join(JS, "alert_hub.js")
TEMPLATE = os.path.join(ROOT, "templates", "alert_hub.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
NOW = dt.datetime(2026, 10, 26, 7, 30, tzinfo=dt.timezone.utc)
G, G2 = "1234567890", "2222222222"


def run(expr, **data):
    pre = "".join("const %s=%s;" % (key, json.dumps(v)) for key, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def issue(agent, kind, level, account=G, name="Acme <b>Dental</b>", platform="google"):
    r = mon.make_issue(agent, "%s:%s:%s" % (agent, account, kind), {"id": account, "name": name, "platform": platform}, kind, level, "%s %s" % (pm.AGENTS[agent]["label"], kind), "detail", "Start with: x")
    r.update(status="open", firstSeen="2026-10-20T07:00:00+00:00", lastSeen="2026-10-26T07:00:00+00:00", resolvedAt=None, reopened=0, alertedAt=None, alertedLevel=None, alert={})
    return r


def real_state(clients=True, route=True, log=True):
    s = MemStore()
    if clients:
        cid = m.save_client(s, "", {"name": "Acme <i>Dental</i>", "accounts": [{"id": G}], "shared": {}}, "ana@position2.com")["id"]
        if route:
            hub.save_route(s, cid, {"slackChannel": "#acme-paid", "emails": "a@b.co", "floor": "P1"}, "ana@position2.com", NOW, {cid: "Acme <i>Dental</i>"})
    s.state_set("alerts", {"emails": [], "slackChannel": "#paid-alerts"})
    s.issues_put([issue("watch", "account_status", "high"), issue("kpi", "cpa", "medium"), issue("creative", "tired", "low", account=G2, name="Beta Clinic"), issue("spend", "spike", "medium", account=G2, name="Beta Clinic")])
    if log:
        s.state_set(hub.LOG_KEY, [{"at": "2026-10-26T07:31:00+00:00", "trigger": "schedule", "agents": ["KPI Monitoring", "Account Watch"], "held": 1, "belowFloor": 0,
                                   "sends": [{"route": "default", "name": "", "channel": "#paid-alerts", "emails": 0, "issues": 2, "p1": 1, "p2": 1, "floor": "P2", "sent": True, "email": {"status": "not_configured"}, "slack": {"status": "sent"}}]}])
    return json.loads(json.dumps(hub.view(s, Deliver(), NOW, pm.AGENTS)))


# ── the wording and the shapes ──────────────────────────────────────────────

def test_the_tiles_count_by_level_say_what_is_held_back_and_how_many_destinations_are_set():
    t = run("X.tiles(S)", S=real_state())
    assert [x[0] for x in t] == ["P1 open", "P2 open", "P3 open", "Held back", "Destinations"]
    assert t[0][1:] == ["1", "alerted as they turn up, and again after a week", "bad"] and t[1][1] == "2" and t[1][3] == "warn" and t[2][1] == "1" and t[3][1] == "1" and "already says it" in t[3][2]
    assert t[4][1] == "2" and t[4][2] == "the default and 1 client with a channel of their own" and t[4][3] == "good"
    clean = run("X.tiles(S)", S={"counts": {"P1": 0, "P2": 0, "P3": 0}, "held": 0, "default": {"emails": [], "slackChannel": ""}, "clients": []})
    assert clean[0][3] == "good" and clean[0][2] == "none open" and clean[3][2] == "no issue is covered by another" and clean[4][1] == "0" and clean[4][3] == "warn" and clean[4][2] == "the default"


def test_a_destination_row_says_where_it_sends_and_a_client_without_a_route_uses_the_default():
    st = real_state()
    rows = run("X.destinations(S)", S=st)
    assert [r["key"] for r in rows] == ["default", st["clients"][0]["id"]] and rows[0]["ready"] is True and rows[1]["own"] is True and rows[1]["floor"] == "P1" and rows[1]["slack"] == "#acme-paid"
    assert run("X.destText(R, D)", R=rows[1], D=st["default"]) == "Slack #acme-paid and 1 email, P1 only" and run("X.destText(R, D)", R=rows[0], D=st["default"]) == "Slack #paid-alerts, P1 and P2"
    bare = run("X.destinations(S)", S=real_state(route=False))
    assert bare[1]["own"] is False and bare[1]["ready"] is True and bare[1]["floor"] == "P2" and run("X.destText(R, D)", R=bare[1], D=st["default"]) == "Uses the default destination, P1 and P2"
    assert run("X.destText(R, D)", R=bare[1], D={"emails": [], "slackChannel": ""}) == "Uses the default destination (none set), P1 and P2"
    none = run("X.destinations(S)", S={"default": {}, "clients": [{"id": "c-a", "name": "A", "accounts": 1, "route": None}]})
    assert none[0]["ready"] is False and none[1]["ready"] is False


def test_what_the_page_sends_for_a_destination_is_what_the_server_reads():
    b = run("X.routeBody('c-acme', ' #acme ', 'a@b.co, c@d.co', 'P1')")
    assert b == {"clientId": "c-acme", "slackChannel": "#acme", "emails": "a@b.co, c@d.co", "floor": "P1"}
    assert run("X.routeBody('c', '', null, 'P9')") == {"clientId": "c", "slackChannel": "", "emails": "", "floor": "P2"}
    s = MemStore()
    r = hub.save_route(s, "c-acme", b, "ana", NOW, {"c-acme": "Acme"})
    assert r["slackChannel"] == "#acme" and r["emails"] == ["a@b.co", "c@d.co"] and r["floor"] == "P1" and run("X.emailsText(['a@b.co','c@d.co'])") == "a@b.co, c@d.co"


def test_the_open_issues_are_filtered_by_level_client_monitor_and_whether_they_are_held_back():
    rows = real_state()["open"]
    cid = real_state()["clients"][0]["id"]
    assert len(rows) == 4 and [r["p"] for r in rows] == ["P1", "P2", "P2", "P3"]
    assert sorted(r["agent"] for r in run("X.filterOpen(R, {p: 'P2'})", R=rows)) == ["kpi", "spend"]
    assert {r["agent"] for r in run("X.filterOpen(R, {client: C})", R=rows, C=cid)} == {"watch", "kpi"}
    assert {r["agent"] for r in run("X.filterOpen(R, {client: 'none'})", R=rows)} == {"creative", "spend"}
    assert {r["agent"] for r in run("X.filterOpen(R, {agent: 'kpi'})", R=rows)} == {"kpi"}
    assert {r["agent"] for r in run("X.filterOpen(R, {held: 'hide'})", R=rows)} == {"watch", "creative", "spend"} and len(run("X.filterOpen(R, {held: 'show'})", R=rows)) == 4
    assert len(run("X.filterOpen(R, {p: 'all', client: 'all', agent: 'all'})", R=rows)) == 4 and len(run("X.filterOpen(R, undefined)", R=rows)) == 4 and run("X.filterOpen(null, {})") == []
    assert run("X.filterOpen(R, {p: 'P1', agent: 'kpi'})", R=rows) == []


def test_the_filter_choices_count_what_each_has_and_end_with_no_client():
    f = run("X.facets(R)", R=real_state()["open"])
    assert [(c["name"], c["count"]) for c in f["clients"]] == [("Acme <i>Dental</i>", 2), ("No client", 2)] and f["clients"][-1]["id"] == "none"
    assert [(a["name"], a["count"]) for a in f["agents"]] == [("Account Watch", 1), ("Creative & Asset Health", 1), ("KPI Monitoring", 1), ("Spend Anomaly Alert", 1)]
    assert run("X.facets([])") == {"clients": [], "agents": []}


def test_an_open_issue_says_how_long_whether_it_was_alerted_and_what_holds_it_back():
    rows = {r["agent"]: r for r in real_state()["open"]}
    assert run("X.ageText(R)", R=rows["kpi"]) == "6 days" and run("X.ageText({ageDays: 0})") == "since today" and run("X.ageText({ageDays: 1})") == "1 day" and run("X.ageText({})") == ""
    assert run("X.alertedText(R)", R=rows["kpi"]) == "held back" and run("X.alertedText(R)", R=rows["watch"]) == "not yet" and run("X.alertedText(R)", R=rows["creative"]) == "not alerted (P3)"
    assert run("X.alertedText({p: 'P2', alertedAt: '2026-10-25T07:00:00+00:00', coveredBy: null})") == "2026-10-25"
    c = run("X.coveredText(R)", R=rows["kpi"])
    assert c.startswith("The account itself is the problem") and "Account Watch: Account Watch account_status" in c and run("X.coveredText(R)", R=rows["watch"]) == ""


def test_a_send_and_a_log_entry_say_what_happened_in_words():
    ok = {"route": "default", "name": "", "channel": "#paid-alerts", "emails": 0, "issues": 2, "p1": 1, "p2": 1, "sent": True, "email": {"status": "not_configured"}, "slack": {"status": "sent"}}
    assert run("X.sendLine(S)", S=ok) == {"text": "Default (#paid-alerts): 2 issues (1 P1, 1 P2), sent", "tone": "ok"}
    bad = dict(ok, name="Acme", channel="#acme", emails=2, issues=1, p1=0, sent=False, slack={"status": "failed", "error": "Slack refused: not_in_channel"})
    assert run("X.sendLine(S)", S=bad) == {"text": "Acme (#acme, 2 emails): 1 issue (1 P2), not delivered: Slack refused: not_in_channel, tried again at the next run", "tone": "bad"}
    none = dict(ok, sent=False, slack={"status": "not_configured"}, channel="")
    assert run("X.sendLine(S)", S=none)["text"] == "Default (): 2 issues (1 P1, 1 P2), not sent: no Slack channel or email is set"
    assert run("X.sendStatus(S)", S=dict(ok, sent=False, slack={"status": "sent"}, email={"status": "failed", "error": "smtp down"})) == "not delivered: smtp down, tried again at the next run"
    e = {"at": "2026-10-26T07:31:00+00:00", "trigger": "schedule", "agents": ["KPI Monitoring", "Account Watch"], "held": 1, "belowFloor": 2, "sends": [ok]}
    assert run("X.logLine(E)", E=e) == {"when": "2026-10-26 07:31 UTC", "trigger": "schedule", "text": "KPI Monitoring and Account Watch · sent to 1 destination · 1 held back · 2 under a floor", "tone": "ok", "sends": [run("X.sendLine(S)", S=ok)]}
    assert run("X.logLine(E)", E=dict(e, sends=[ok, bad], held=0, belowFloor=0, trigger="page by ana@position2.com", agents=["a", "b", "c", "d"]))["text"] == "4 monitors · 1 of 2 destinations took it"
    assert run("X.logLine(E)", E=dict(e, sends=[ok, bad]))["tone"] == "warn" and run("X.logLine(E)", E=dict(e, sends=[], held=0, belowFloor=0))["text"] == "KPI Monitoring and Account Watch · nothing sent"
    assert run("X.logLine(E)", E=dict(e, trigger="page by ana@position2.com"))["trigger"] == "from the page"


def test_the_rules_are_worded_from_what_the_server_says_and_every_kind_has_a_name():
    st = real_state()
    lines = run("X.ruleLines(S)", S=st)
    assert lines[0].startswith("When Account Watch finds an account that is gone or an account that is not serving, what follows from it is held back while it is open: ")
    assert "KPI Monitoring (every finding)" in lines[0] and "Spend Anomaly Alert (a drop in spend)" in lines[0] and "Tracking & Data Health (conversions dropped and conversions stopped)" in lines[0]
    assert any("(cannot show or deliver)" in l and "Disapproved Ads Tracker, an ad group that cannot show an ad; then KPI Monitoring, a campaign not delivering; then Spend Anomaly Alert, a drop in spend" in l for l in lines)
    assert any("(stopped converting)" in l and "Tracking & Data Health, conversions stopped; then KPI Monitoring, a campaign that stopped converting" in l for l in lines) and "never hidden behind a P2" in lines[-1]
    kinds = set(st["rules"]["root"]) | {k for ks in st["rules"]["families"].values() for k in ks} | {"%s.%s" % (a, k) for a, ks in st["rules"]["symptoms"].items() for k in (ks or [])}
    assert kinds and all(run("X.kindText(K) !== K", K=k) for k in kinds), [k for k in kinds if not run("X.kindText(K) !== K", K=k)]
    assert run("X.kindText('zz.unknown')") == "zz.unknown"


def test_the_csv_has_one_line_per_open_issue():
    st = real_state()
    rows = run("X.csvRows(S)", S=st)
    assert rows[0] == ["Alert Hub"] and rows[2] == run("X.HEAD") and len(rows) == 3 + 4
    body = rows[3:]
    assert ["P1", "Acme <i>Dental</i>", "Acme <b>Dental</b>", "Google Ads", "Account Watch", "Account Watch account_status", 6, "", ""] in body
    held = [r for r in body if r[4] == "KPI Monitoring"][0]
    assert held[8] == "Account Watch: Account Watch account_status" and [r for r in body if r[4] == "Creative & Asset Health"][0][1] == "(no client)"


# ── the page script, run against a stub page ────────────────────────────────

def page(scenario, tmp_path):
    f = tmp_path / "scenario.json"
    f.write_text(json.dumps(scenario))
    out = subprocess.run(["node", "-e", HARNESS, json.dumps([DATA, PAGE]), str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def stubs(st, **kw):
    return [{"match": "/alerthub/state", "body": st}] + kw.get("more", [])


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js)) | set(re.findall(r'"(#f[A-Za-z]+)"', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("alert_hub_data.js") < html.index("alert_hub.js") and "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_the_page_shows_the_tiles_the_destinations_the_open_issues_the_log_and_the_rules(tmp_path):
    st = real_state()
    out = page({"responses": stubs(st)}, tmp_path)
    assert out["calls"] == ["GET /p2/paid-marketing/api/alerthub/state"] and out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True
    kp = out["el"]["#kpis"]["html"]
    assert "P1 open" in kp and "Held back" in kp and "Destinations" in kp and 'class="pd-kpi bad"' in kp
    assert "4 open issues" in out["el"]["#fresh"]["html"] and "in 10 monitors" in out["el"]["#fresh"]["html"] and "last send" in out["el"]["#fresh"]["html"]
    assert out["el"]["#dSlack"]["value"] == "#paid-alerts" and out["el"]["#dEmails"]["value"] == ""
    cid = st["clients"][0]["id"]
    d = out["el"]["#destClients"]["html"]
    assert 'id="rs-%s" value="#acme-paid"' % cid in d and 'id="re-%s" value="a@b.co"' % cid in d and '<option value="P1" selected>' in d and "Acme &lt;i&gt;Dental&lt;/i&gt;" in d and "<i>Dental</i>" not in d
    assert 'class="pm-btn ah-save" data-id="%s"' % cid in d and 'class="pm-btn-ghost ah-test"' in d and "Slack #acme-paid and 1 email, P1 only" in d
    assert d.count("<tr><td>") == 1 and "<b>Default</b>" not in d and 'id="rs-default"' not in d                          # the default has its own boxes above, the table is the clients
    o = out["el"]["#open"]["html"]
    assert o.count("<tr") == 5 and 'class="ah-held"' in o and "Acme &lt;b&gt;Dental&lt;/b&gt;" in o and "<b>Dental</b>" not in o and "The account itself is the problem" in o and 'href="/p2/paid-marketing/kpi-monitoring"' in o
    assert '<span class="bi-lv bad">P1</span>' in o and '<span class="bi-lv wait">P3</span>' in o and "4 issues" in out["el"]["#openNote"]["text"]
    assert "Default (#paid-alerts): 2 issues (1 P1, 1 P2), sent" in out["el"]["#log"]["html"] and "1 held back" in out["el"]["#log"]["html"]
    r = out["el"]["#rules"]["html"]
    assert "P1</b> is a high issue" in r and "Not done, on purpose" in r and "When Account Watch finds" in r
    assert "Email is set up" in out["el"]["#foot"]["html"]


def test_a_long_list_shows_the_first_two_hundred_and_says_how_many_there_are(tmp_path):
    st = real_state()
    base = st["open"][1]
    st["open"] = [dict(base, fingerprint="kpi:%d" % n, accountName="Account %03d" % n) for n in range(250)]
    st["openTotal"] = 300
    out = page({"responses": stubs(st)}, tmp_path)
    assert out["el"]["#open"]["html"].count("<tr") == 201 and "The first 200 of 250." in out["el"]["#open"]["html"] and "250 issues of 300 (the page lists the first 250)" in out["el"]["#openNote"]["text"]


def test_filters_show_what_matches_and_say_when_nothing_does(tmp_path):
    st = real_state()
    steps = [{"set": ["#fP", "P3"]}, {"fire": ["#fP", "change", None]}]
    out = page({"responses": stubs(st), "steps": steps}, tmp_path)
    assert out["el"]["#open"]["html"].count("<tr") == 2 and "Creative &amp; Asset Health" in out["el"]["#open"]["html"] and "1 issue of 4" in out["el"]["#openNote"]["text"]
    none = page({"responses": stubs(st), "steps": [{"set": ["#fP", "P1"]}, {"set": ["#fAgent", "kpi"]}, {"fire": ["#fP", "change", None]}]}, tmp_path)
    assert "Nothing matches the filters." in none["el"]["#open"]["html"] and "0 issues of 4" in none["el"]["#openNote"]["text"]
    hid = page({"responses": stubs(st), "steps": [{"set": ["#fHeld", "hide"]}, {"fire": ["#fHeld", "change", None]}]}, tmp_path)
    assert 'class="ah-held"' not in hid["el"]["#open"]["html"] and hid["el"]["#open"]["html"].count("<tr") == 4


def test_the_filter_menus_list_the_clients_and_monitors_with_counts(tmp_path):
    out = page({"responses": stubs(real_state())}, tmp_path)
    assert "All clients" in out["el"]["#fClient"]["html"] and "Acme &lt;i&gt;Dental&lt;/i&gt; (2)" in out["el"]["#fClient"]["html"] and "No client (2)" in out["el"]["#fClient"]["html"]
    assert "All monitors" in out["el"]["#fAgent"]["html"] and "KPI Monitoring (1)" in out["el"]["#fAgent"]["html"]


def test_with_no_client_the_page_points_to_the_master_agent_and_with_nothing_open_it_says_so(tmp_path):
    out = page({"responses": stubs(real_state(clients=False, log=False))}, tmp_path)
    assert "Master Agent" in out["el"]["#destClients"]["html"] and "No client in the Master Agent yet" in out["el"]["#destNote"]["text"] and "Nothing has been sent through the hub yet" in out["el"]["#log"]["html"]
    s = MemStore()
    empty = json.loads(json.dumps(hub.view(s, Deliver(), NOW, pm.AGENTS)))
    out = page({"responses": stubs(empty)}, tmp_path)
    assert "Nothing is open in any monitor." in out["el"]["#open"]["html"] and "0 open issues" in out["el"]["#fresh"]["html"] and out["el"]["#changesCard"]["hidden"] is True


def test_saving_the_default_destination_posts_what_was_typed_and_reloads(tmp_path):
    st = real_state()
    steps = [{"set": ["#dEmails", "a@b.co, c@d.co"]}, {"set": ["#dSlack", "#new"]}, {"click": "#dSave"}]
    out = page({"responses": stubs(st, more=[{"match": "/api/monitors/alerts", "method": "POST", "body": {"alerts": {}}}]), "steps": steps}, tmp_path)
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/monitors/alerts", "body": {"emails": "a@b.co, c@d.co", "slackChannel": "#new"}}]
    assert out["calls"].count("GET /p2/paid-marketing/api/alerthub/state") == 2 and "Saved the default destination" in out["el"]["#msg"]["html"] and "pm-note" in out["el"]["#msg"]["html"]
    bad = page({"responses": stubs(st, more=[{"match": "/api/monitors/alerts", "method": "POST", "status": 400, "body": {"error": "nope is not an email address."}}]), "steps": steps}, tmp_path)
    assert "nope is not an email address." in bad["el"]["#msg"]["html"] and bad["calls"].count("GET /p2/paid-marketing/api/alerthub/state") == 1
    down = page({"responses": stubs(st), "reject": "/api/monitors/alerts", "steps": steps}, tmp_path)
    assert "Nothing was saved." in down["el"]["#msg"]["html"]


def test_saving_a_clients_destination_posts_its_boxes_and_a_refusal_is_said(tmp_path):
    st = real_state()
    cid = st["clients"][0]["id"]
    steps = [{"set": ["#rs-" + cid, "#acme-new"]}, {"set": ["#re-" + cid, "x@y.co"]}, {"set": ["#rf-" + cid, "P2"]}, {"fire": ["#destClients", "click", {".ah-save": {"dataset": {"id": cid}}}]}]
    out = page({"responses": stubs(st, more=[{"match": "/alerthub/route", "method": "POST", "body": {"route": {}}}]), "steps": steps}, tmp_path)
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/alerthub/route", "body": {"clientId": cid, "slackChannel": "#acme-new", "emails": "x@y.co", "floor": "P2"}}]
    assert "Saved the destination of Acme &lt;i&gt;Dental&lt;/i&gt;." in out["el"]["#msg"]["html"] and out["calls"].count("GET /p2/paid-marketing/api/alerthub/state") == 2
    bad = page({"responses": stubs(st, more=[{"match": "/alerthub/route", "method": "POST", "status": 400, "body": {"error": "x is not a Slack channel name or id."}}]), "steps": steps}, tmp_path)
    assert "x is not a Slack channel name or id." in bad["el"]["#msg"]["html"] and bad["calls"].count("GET /p2/paid-marketing/api/alerthub/state") == 1


def test_a_test_send_says_where_it_went_and_whether_it_arrived(tmp_path):
    st = real_state()
    cid = st["clients"][0]["id"]
    ok = {"match": "/alerthub/test", "method": "POST", "body": {"sent": True, "name": "Acme", "slack": {"status": "sent"}, "email": {"status": "not_configured"}}}
    out = page({"responses": stubs(st, more=[ok]), "steps": [{"fire": ["#destClients", "click", {".ah-test": {"dataset": {"id": cid}}}]}]}, tmp_path)
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/alerthub/test", "body": {"route": cid}}] and "Test sent (Slack: sent) to Acme." in out["el"]["#msg"]["html"] and "pm-note" in out["el"]["#msg"]["html"]
    d = page({"responses": stubs(st, more=[ok]), "steps": [{"click": "#dTest"}]}, tmp_path)
    assert d["posts"][0]["body"] == {"route": "default"}
    refused = {"match": "/alerthub/test", "method": "POST", "body": {"sent": False, "name": "", "slack": {"status": "failed", "error": "Slack refused: not_in_channel"}, "email": {"status": "not_configured"}}}
    r = page({"responses": stubs(st, more=[refused]), "steps": [{"click": "#dTest"}]}, tmp_path)
    assert "The test was not delivered: Slack: failed (Slack refused: not_in_channel)." in r["el"]["#msg"]["html"] and "pm-err" in r["el"]["#msg"]["html"]
    nowhere = {"match": "/alerthub/test", "method": "POST", "body": {"sent": False, "name": "Acme", "slack": {"status": "not_configured"}, "email": {"status": "not_configured"}}}
    n = page({"responses": stubs(st, more=[nowhere]), "steps": [{"click": "#dTest"}]}, tmp_path)
    assert "Nothing was sent: no Slack channel or email is set for Acme." in n["el"]["#msg"]["html"]
    err = page({"responses": stubs(st, more=[{"match": "/alerthub/test", "method": "POST", "status": 400, "body": {"error": "That client does not exist."}}]), "steps": [{"click": "#dTest"}]}, tmp_path)
    assert "That client does not exist." in err["el"]["#msg"]["html"]
    down = page({"responses": stubs(st), "reject": "/alerthub/test", "steps": [{"click": "#dTest"}]}, tmp_path)
    assert "Nothing was sent." in down["el"]["#msg"]["html"]


def test_a_state_that_cannot_be_read_is_said_in_words(tmp_path):
    out = page({"responses": [{"match": "/alerthub/state", "status": 503, "body": {"error": "The database is down."}}]}, tmp_path)
    assert "The database is down." in out["el"]["#msg"]["html"] and out["el"]["#out"]["hidden"] is True and out["el"]["#skel"]["hidden"] is True
    down = page({"responses": [], "reject": "/alerthub/state"}, tmp_path)
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"]


def test_the_csv_is_downloaded_and_a_cell_that_starts_like_a_formula_is_defused(tmp_path):
    st = real_state()
    st["open"][0]["title"] = "=HYPERLINK(\"x\")"
    out = page({"responses": stubs(st), "steps": [{"click": "#exportBtn"}]}, tmp_path)
    assert out["blob"].startswith("﻿Alert Hub") and "'=HYPERLINK" in out["blob"] and "Severity,Client,Account" in out["blob"] and out["downloads"][0]["name"].startswith("alert-hub-")
