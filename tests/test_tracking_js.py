"""Tracking Data Health: static/js/tracking_data.js run in Node, and the page script run against a stub page and a real report the server code builds from made-up accounts.

The server judges the tracking (tests/test_paid_tracking*.py). The page only draws, so how a status, a level and the 35 days read come from tracking_data.js (executed here, not read), and
tracking.js is run for real through the harness the other agents' page tests share: it reads the report the server built, draws it, and saves what the team says. The HTML it writes is read back.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from tests.test_copycheck_js import HARNESS
from tests.tracking_fakes import Google, Meta
from tracker import paid_geo
from tracker import paid_gads as base
from tracker import paid_store
from tracker import paid_tracking as tr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "tracking_data.js"), os.path.join(JS, "tracking.js")
TEMPLATE = os.path.join(ROOT, "templates", "tracking_health.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

TODAY = dt.date(2026, 10, 26)
AT = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)
ACCT = {"id": "1234567890", "name": "Acme <b>Dental</b>", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}
GROUP = "mcc-9923720096-USD"


def run(expr, **data):
    pre = "".join("const %s=%s;" % (key, json.dumps(v)) for key, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def reports():
    saved = {}
    mp = pytest.MonkeyPatch()
    mp.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    mp.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    paid_geo.clear_cache()
    base.forget("")
    try:
        def one(accounts, target="1234567890", groups=()):
            paid_geo.clear_cache()
            saved.clear()
            base.forget("")
            return tr.report_for(target, accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: list(groups), search_fn=Google(accounts, lambda: AT), today=TODAY, at=AT)
        broken = one({ACCT["id"]: {"over": {0: (100, 0)}, "actions": [{"id": "31", "name": "Lead form", "perDay": 4, "stopFrom": -2}],
                                   "lists": [{"id": "9", "name": "Visitors", "size": 900}], "tagging": True}})
        group = {"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
        mcc = one({ACCT["id"]: {}, ACCT2["id"]: {"tagging": False}}, GROUP, [group])
        healthy = one({ACCT["id"]: {}})
        paid_geo.clear_cache()
        meta = tr.report_for("act_5550001", meta_pages_fn=Meta({"pixels": [{"id": "88", "name": "Main pixel", "hoursAgo": 30}]}, lambda: AT),
                             meta_account_fn=lambda act: {"name": "Acme Meta", "currency": "USD"}, today=TODAY, at=AT)
        return {"broken": json.loads(json.dumps(broken)), "mcc": json.loads(json.dumps(mcc)), "healthy": json.loads(json.dumps(healthy)), "meta": json.loads(json.dumps(meta))}
    finally:
        mp.undo()
        paid_geo.clear_cache()


# ── tracking_data.js ────────────────────────────────────────────────────────

def test_statuses_levels_and_scores_read_in_words():
    assert run("[X.statusInfo('broken'), X.statusInfo('healthy'), X.statusInfo('odd')]") == [{"label": "Tracking broken", "tone": "bad"}, {"label": "Healthy", "tone": "ok"}, {"label": "odd", "tone": "unk"}]
    assert run("X.levelInfo('critical')") == {"label": "Critical", "tone": "bad"} and run("[X.scoreTone(100), X.scoreTone(70), X.scoreTone(40)]") == ["ok", "warn", "bad"]
    assert run("[X.dec(2.25), X.dec(3), X.plural(1, 'click'), X.plural(2.5, 'conversion'), X.day('2026-10-05')]") == ["2.3", "3", "1 click", "2.5 conversions", "Oct 5"]


def test_the_tiles_count_broken_accounts_and_average_the_score(reports):
    t = run("X.tiles(R)", R=reports["broken"])
    assert t[0][:2] == ["Accounts read", "1"] and t[1][:2] == ["Tracking broken", "1"] and t[1][3] == "warn" and t[3][:2] == ["Health score", "60"]
    h = run("X.tiles(R)", R=reports["healthy"])
    assert h[1][2] == "no conversions or pixel stopped" and h[1][3] == "good" and h[2][1] == "0"


def test_the_views_open_on_what_needs_a_look(reports):
    assert run("[X.firstView(R), X.viewCount(R,'attention'), X.viewCount(R,'all')]", R=reports["mcc"]) == ["attention", 1, 2]
    assert run("X.firstView(R)", R=reports["healthy"]) == "all"


def test_the_chart_scales_clicks_to_the_box_and_marks_days_without_conversions(reports):
    c = run("X.chart(R.accounts[0].days, 700, 120)", R=reports["broken"])
    assert len(c["bars"]) == 35 and c["maxClicks"] == 100 and c["bars"][-1]["h"] == 116 and c["dots"][-1]["zero"] is True and c["dots"][0]["zero"] is False
    assert run("X.chart([], 700, 120).bars") == []


def test_actions_lists_and_pixels_are_described(reports):
    a = run("X.actionRows(R.accounts[0])", R=reports["broken"])
    assert a[0]["name"] == "Lead form" and a[0]["label"] == "Stopped" and a[0]["key"] == "action:31" and a[0]["recent"] == "0 in 3 days" and a[0]["judged"]
    lst = run("X.listRows(R.accounts[0])", R=reports["broken"])
    assert lst[0]["label"] == "No history yet" and lst[0]["size"] == "900"
    px = run("X.pixelRows(R.accounts[0])", R=reports["meta"])
    assert px[0]["label"] == "Silent" and px[0]["last"] == "30 hours ago"


def test_a_finding_says_what_lowered_it_and_which_button_it_offers():
    acked = {"level": "low", "baseLevel": "critical", "acked": {"by": "ana", "note": "on it"}, "expected": ""}
    assert run("X.findingView(F)", F=acked) == {"label": "Low", "tone": "wait", "was": "Critical", "tag": "Acknowledged by ana: on it", "canAck": False, "canUnack": True}
    plain = {"level": "high", "baseLevel": "high", "acked": None, "expected": ""}
    assert run("X.findingView(F)", F=plain)["canAck"] is True and run("X.findingView(F)", F=dict(plain, level="low", baseLevel="low"))["canAck"] is False


def test_the_insights_say_what_waits_for_history(reports):
    out = run("X.insights(R)", R=reports["broken"])
    assert [i["text"] for i in out] == ["List trends start once the daily check has kept the lists' sizes for a few days."]


# ── the page script ─────────────────────────────────────────────────────────

SCRIPTS = [os.path.join(JS, "pm_scope.js"), DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in (ACCT, ACCT2)], "groups": [{"id": GROUP, "name": "Group", "accountIds": [ACCT["id"], ACCT2["id"]]}]}
HARNESS_PAGE = (HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/tracking-health")
                .replace("JSON.parse(process.argv[2])", "JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))"))


def page(scenario):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(scenario, f)
    try:
        out = subprocess.run(["node", "-e", HARNESS_PAGE, json.dumps(SCRIPTS), f.name], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(report, steps=None, extra=()):
    return page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/tracking/report", "body": report}] + list(extra), "steps": [{"pick": "1234567890"}] + (steps or [])})


def test_every_element_the_script_reaches_for_is_in_the_template_and_there_is_no_dash():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_scope.js") < html.index("monitor_panel.js") < html.index("tracking_data.js") < html.index("tracking.js")
    assert not any(d in t for d in ("—", "–") for t in (html, js, open(DATA, encoding="utf-8").read()))


def test_nothing_is_read_until_an_account_is_chosen_and_the_alert_panel_is_mounted():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    assert out["panels"] == [{"agent": "tracking", "label": "Tracking Data Health"}] and not any("/tracking/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False


def test_a_broken_account_is_drawn_with_its_chart_finding_actions_and_escaped_name(reports):
    out = opened(reports["broken"])
    lst = out["el"]["#list"]["html"]
    assert out["store"]["th_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/tracking-health?account=1234567890"
    assert 'class="bi-row stopped open"' in lst and "Tracking broken" in lst and '<span class="bi-n">60</span>' in lst and "th-score bad" in lst
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in lst and "<b>Dental</b>" not in lst
    assert '<svg class="th-chart"' in lst and lst.count('<rect class="th-bar"') == 35 and "th-dot zero" in lst
    assert "Conversions stopped" in lst and "Lead form" in lst and "Acknowledge" in lst and 'data-key="conv_stopped"' in lst
    assert "Conversion actions" in lst and "Stopped" in lst and "Remarketing lists" in lst and "No history yet" in lst and "Auto-tagging is on." in lst
    assert "Who looks after its tracking" in lst and "A drop is expected from" in lst
    assert "Tracking broken" in out["el"]["#kpis"]["html"] and "28 days before" in out["el"]["#foot"]["html"]


def test_an_mcc_opens_on_the_account_that_needs_a_look(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/tracking/report", "body": reports["mcc"]}], "steps": [{"pick": GROUP}]})
    lst = out["el"]["#list"]["html"]
    assert "Auto-tagging is off" in lst and "Beta Clinic" in lst and "Acme" not in lst and 'Needs a look <span class="n">1</span>' in out["el"]["#views"]["html"]


def test_a_meta_account_shows_its_pixels_and_no_actions(reports):
    lst = opened(reports["meta"])["el"]["#list"]["html"]
    assert 'class="bi-mk">Meta</span>' in lst and "Pixels" in lst and "Main pixel" in lst and "Conversion actions" not in lst and "The conversion actions were not read" not in lst


def test_acknowledging_a_finding_sends_the_note_and_reads_the_report_again(reports):
    form = {".th-set,.th-ack": {"dataset": {"kind": "ack", "account": "1234567890", "key": "conv_stopped"}, "areas": [{"k": "note", "value": "tag fix going out"}]}}
    out = opened(reports["broken"], [{"submit": ["#list", form]}], [{"match": "/tracking/settings/ack", "method": "POST", "body": {"account": {}}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/tracking/settings/ack", "body": {"account": "1234567890", "key": "conv_stopped", "note": "tag fix going out"}}]
    assert out["outs"][0]["[data-out]"] == "Saved. Checking again…" and len([c for c in out["calls"] if "/tracking/report" in c]) == 2


def test_a_window_the_server_refuses_says_why_and_nothing_is_read_again(reports):
    form = {".th-set,.th-ack": {"dataset": {"kind": "window", "account": "1234567890", "name": "Acme"}, "areas": [{"k": "from", "value": "2026-11-02"}, {"k": "to", "value": "2026-11-01"}, {"k": "why", "value": "x"}]}}
    out = opened(reports["broken"], [{"submit": ["#list", form]}], [{"match": "/tracking/settings/window", "method": "POST", "status": 400, "body": {"error": "The last day is before the first."}}])
    assert out["outs"][0]["[data-out]"] == "The last day is before the first." and len([c for c in out["calls"] if "/tracking/report" in c]) == 1


def test_putting_a_list_back_posts_its_key_for_its_account(reports):
    btn = {".th-unmute": {"dataset": {"account": "1234567890", "key": "list:9"}}}
    out = opened(reports["broken"], [{"fire": ["#list", btn]}], [{"match": "/tracking/settings/unmute", "method": "POST", "body": {"account": {}}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/tracking/settings/unmute", "body": {"account": "1234567890", "key": "list:9"}}]


def test_a_failed_read_says_why(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/tracking/report", "status": 429, "body": {"error": "Wait a minute.", "kind": "cooldown"}}],
                "steps": [{"pick": "1234567890"}]})
    assert "Wait a minute." in out["el"]["#msg"]["html"] and out["el"]["#out"]["hidden"] is True


def test_a_group_waiting_for_list_history_says_so_once(reports):
    r = json.loads(json.dumps(reports["broken"]))
    r["account"]["isGroup"] = True
    r["accounts"] = [r["accounts"][0], dict(r["accounts"][0], name="Second")]
    assert [i["text"] for i in run("X.insights(R)", R=r)] == ["List trends start once the daily check has kept the lists' sizes for a few days (2 accounts waiting)."]
