"""Spend Anomaly Alert: static/js/anomaly_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads the spend and works out the usual and the findings (tests/test_paid_anomaly*.py). The page only draws, so how a status and a level read, which accounts a view shows, where the
lines and bars of a chart go and the CSV come from anomaly_data.js (executed here, not read), and anomaly.js is run for real: it loads the accounts and what the team saved, draws the report the
server built, saves figures and a sensitivity and says what went wrong in words. The HTML it writes is read back.
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

from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_anomaly as TA  # noqa: E402
from tests.test_copycheck_js import HARNESS  # noqa: E402
from tracker import paid_anomaly as an  # noqa: E402
from tracker import paid_anomaly_settings as st  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "anomaly_data.js"), os.path.join(JS, "anomaly.js")
TEMPLATE = os.path.join(ROOT, "templates", "spend_anomaly.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
GAMMA = {"id": "3333333333", "name": "Gamma <b>&</b> Co", "currency": "USD"}
DELTA = {"id": "4444444444", "name": "Delta", "currency": "USD"}
ACCOUNTS = [TA.ACCT, TA.ACCT2, GAMMA, DELTA]
SPIKE = {9: 60, 10: 60, 11: 60}


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def reports():
    mp = pytest.MonkeyPatch()
    from tracker import paid_gads as base, paid_store
    saved = {}
    mp.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    mp.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    mp.setattr(paid_store, "get_gads_entry", lambda key, ttl: None, raising=False)
    an.clear_cache()
    base.reset_state()
    changes = [TA.change("2026-10-22 08:30:00", "zed@y.com", old={"campaignBudget": {"amountMicros": "50000000"}}, new={"campaignBudget": {"amountMicros": "400000000"}}), TA.change("2026-10-12 08:30:00", "ann@x.com")]
    extra = {"campaigns": {TA.ACCT["id"]: TA.camps()}, "budgets": {TA.ACCT["id"]: TA.budgets()}, "changes": {TA.ACCT["id"]: changes}}
    rows = {TA.ACCT["id"]: TA.hourly_rows(boost=SPIKE), TA.ACCT2["id"]: TA.hourly_rows(), GAMMA["id"]: TA.hourly_rows(days=3), DELTA["id"]: []}
    s = TA.searcher(rows, extra)
    mon = MemStore()
    st.save_account(mon, TA.ACCT["id"], {"maxDaily": 3000, "note": "Spring push", "mutedUntil": ""}, "a@b.co", today=TA.TODAY)
    group = {"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in ACCOUNTS]}
    kw = dict(accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [group], search_fn=s, now_utc=TA.NOW, clock=1000.0)
    out = {"one": an.report_for(TA.ACCT["id"], mon=mon, **kw)}
    an.clear_cache()
    out["mcc"] = an.report_for(GROUP, mon=mon, **kw)
    an.clear_cache()
    out["clean"] = an.report_for(TA.ACCT2["id"], mon=mon, **kw)
    an.clear_cache()
    out["flat"] = an.report_for(TA.ACCT2["id"], accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [group], search_fn=TA.searcher(rows, refuse_hours=True), now_utc=TA.NOW, clock=1000.0)
    an.clear_cache()
    out["meta"] = an.report_for("act_5555", search_fn=s, meta_pages_fn=TA.meta_pages(boost=SPIKE), meta_account_fn=lambda act: dict(TA.META), now_utc=TA.NOW, clock=1000.0)
    an.clear_cache()
    muted = MemStore()
    st.save_account(muted, TA.ACCT["id"], {"mutedUntil": "2026-10-22", "note": "Spring push"}, "a@b.co", today=TA.TODAY)
    out["muted"] = an.report_for(TA.ACCT["id"], mon=muted, **kw)
    an.clear_cache()
    yield out
    mp.undo()


# ── the data helpers ────────────────────────────────────────────────────────

def test_a_status_and_a_level_read_in_words_and_an_unknown_one_reads_as_itself():
    assert run("X.statusInfo('over')") == {"label": "Above usual", "tone": "over"} and run("X.statusInfo('under')")["label"] == "Below usual" and run("X.statusInfo('ok')")["tone"] == "ok"
    assert run("X.statusInfo('new')")["label"] == "Too new to judge" and run("X.statusInfo('none')")["label"] == "No spend" and run("X.statusInfo('zz')") == {"label": "zz", "tone": "unk"}
    assert run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('info')") == {"label": "Expected", "tone": "unk"} and run("X.levelInfo('q')") == {"label": "q", "tone": "unk"}
    assert [run("X.kindText(%s)" % json.dumps(k)) for k in ("spike", "surge", "ceiling", "budget", "drop", "zz", None)] == ["Spike", "Surge", "Ceiling", "Budget", "Drop", "zz", ""]


def test_money_is_written_the_way_the_server_writes_it():
    assert [run("X.money(%s, %s)" % (json.dumps(v), json.dumps(c))) for v, c in ((5, "USD"), (99.5, "USD"), (100, "USD"), (1234.5, "USD"), (1234.49, "USD"), (0, "USD"), (-250.5, "USD"), (12, "EUR"), (12, "XYZ"), (12, None))] == [
        "$5.00", "$99.50", "$100", "$1,235", "$1,234", "$0.00", "-$251", "€12.00", "XYZ 12.00", "12.00"]
    assert run("X.money(null, 'USD')") == "n/a" and run("X.money(NaN, 'USD')") == "n/a"


def test_a_ratio_and_an_hour_read_plainly():
    assert [run("X.ratioText(%s)" % json.dumps(v)) for v in (1.0, 2.25, 9.94, 10, 12.4, None)] == ["1.0x", "2.3x", "9.9x", "10x", "12x", "n/a"]
    assert [run("X.hh(%d)" % h) for h in (0, 5, 12, 23)] == ["00:00", "05:00", "12:00", "23:00"]


def test_the_tiles_say_how_many_need_a_look_and_what_has_been_spent_so_far(reports):
    t = run("X.tiles(R)", R=reports["mcc"])
    assert [x[0] for x in t] == ["Accounts read", "Need a look", "High", "Spent so far today", "Too new to judge"]
    assert t[0] == ["Accounts read", "4", "1 normal", ""] and t[1] == ["Need a look", "1", "1 above usual, 0 below", "warn"] and t[2][0] == "High" and t[4] == ["Too new to judge", "1", "fewer than 4 days of spend", ""]
    o = reports["mcc"]["summary"]["overall"]
    assert t[3][1] == "$%d" % round(o["cost"]) and "in 2 accounts" in t[3][2] and t[3][3] == "warn"
    c = run("X.tiles(R)", R=reports["clean"])
    assert c[1] == ["Need a look", "0", "all spending as usual", "good"] and c[2] == ["High", "0", "none at the highest level", "good"] and c[4][2] == "every account has a usual" and c[3][3] == ""
    many = json.loads(json.dumps(reports["mcc"]))
    many["summary"]["overall"] = None
    assert run("X.tiles(R)", R=many)[3] == ["Spent so far today", "n/a", "the accounts are in different currencies", ""]


def test_the_views_hold_the_accounts_that_need_a_look(reports):
    assert [a["name"] for a in run("X.shown(R, 'attention')", R=reports["mcc"])] == ["Acme Dental"] and len(run("X.shown(R, 'all')", R=reports["mcc"])) == 4
    assert run("X.firstView(R)", R=reports["mcc"]) == "attention" and run("X.firstView(R)", R=reports["clean"]) == "all" and run("X.viewCount(R, 'all')", R=reports["mcc"]) == 4
    assert run("X.needsLook({status: 'under'})") is True and run("X.needsLook({status: 'new'})") is False


def test_the_headline_is_the_worst_finding_or_why_there_is_none(reports):
    mcc = {a["name"]: a for a in reports["mcc"]["accounts"]}
    assert run("X.headline(A)", A=mcc["Acme Dental"]) == mcc["Acme Dental"]["findings"][0]["title"] and run("X.headline(A)", A=mcc["Beta Clinic"]) == ""
    assert run("X.headline(A)", A=mcc["Delta"]) == "No spend in the last 15 days" and run("X.headline(A)", A=mcc["Gamma <b>&</b> Co"]) == "Fewer than 4 days of spend are known"
    assert run("X.headline(A)", A={"findings": [{"level": "info", "title": "Expected"}], "status": "ok"}) == ""


def test_the_pace_says_what_has_been_spent_against_the_usual_by_now(reports):
    acme = reports["one"]["accounts"][0]
    p = run("X.paceText(A)", A=acme)
    assert p["tone"] == "over" and p["text"].startswith("$270 by 12:00, usual $120 (2.3x)")
    beta = reports["clean"]["accounts"][0]
    assert run("X.paceText(A)", A=beta) == {"text": "$120 by 12:00, usual $120 (1.0x)", "tone": ""}
    flat = reports["flat"]["accounts"][0]
    assert run("X.paceText(A)", A=flat) == {"text": "$120 so far today, a usual day is $240", "tone": ""}
    assert run("X.paceText(A)", A={"today": {"cost": 50, "usualByNow": 100, "through": 14, "ratio": 0.1}, "currency": "EUR"}) == {"text": "€50.00 by 14:00, usual €100 (0.1x)", "tone": "under"}
    assert run("X.paceText(A)", A={"today": {"cost": 50, "usualByNow": 100, "through": 14, "ratio": 1.75}, "currency": "USD"})["tone"] == "over" and run("X.paceText(A)", A={"today": {"cost": 50, "usualByNow": 100, "through": 14, "ratio": 1.74}, "currency": "USD"})["tone"] == ""
    assert run("X.paceText(A)", A={"today": {"cost": 50, "usualByNow": 100, "through": 14, "ratio": 0.2}, "currency": "USD"})["tone"] == "under" and run("X.paceText(A)", A={"today": {"cost": 50, "usualByNow": 100, "through": 14, "ratio": 0.21}, "currency": "USD"})["tone"] == ""


def test_a_sentence_across_accounts_for_whole_days_a_muted_push_and_a_new_account(reports):
    got = run("X.insights(R)", R=reports["flat"])
    assert [g["kind"] for g in got] == ["hours"] and got[0]["text"].startswith("This account gave no spend by hour, so it is judged by whole days only (Beta Clinic).")
    got = run("X.insights(R)", R=reports["muted"])
    assert [g["kind"] for g in got] == ["mute"] and got[0]["text"] == "A planned push is muted until 2026-10-22 (Spring push). Spikes up to that day are shown as expected and are not alerted."
    got = run("X.insights(R)", R=reports["mcc"])
    assert [g["kind"] for g in got] == ["new"] and got[0]["text"].startswith("Gamma <b>&</b> Co: Fewer than 4 days of spend are known") and run("X.insights(R)", R=reports["clean"]) == []
    ended = json.loads(json.dumps(reports["muted"]))
    ended["accounts"][0]["saved"]["mutedUntil"] = "2026-10-21"
    assert run("X.insights(R)", R=ended) == []


def test_the_curve_is_drawn_from_the_usual_the_highest_and_today(reports):
    c = reports["one"]["accounts"][0]["curve"]
    g = run("X.curveGeometry(C, 640, 210)", C=c)
    assert g["W"] == 640 and g["H"] == 210 and g["usual"].startswith("M44 ") and g["usual"].count("L") == 24 and g["upper"].count("L") == 24 and g["today"].count("L") == 12 and g["today"].startswith("M44 ")
    assert [t["h"] for t in g["ticks"]] == [0, 6, 12, 18, 24] and [t["label"] for t in g["ticks"]] == ["00", "06", "12", "18", "24"] and g["ticks"][0]["x"] == 44 and g["ticks"][-1]["x"] == 626
    assert g["nowX"] == g["ticks"][2]["x"] == 335 and g["dot"]["x"] == 335 and g["base"] == 184 and g["dot"]["y"] < g["base"]
    ys = [t["y"] for t in g["yticks"]]
    assert ys[0] == 184 and ys == sorted(ys, reverse=True)
    assert run("X.curveGeometry(null, 640, 210)") is None


def test_the_curve_scales_to_the_highest_value_it_holds():
    c = {"hour": 3, "today": [0, 10, 20, 300], "usual": [0, 10, 20, 30] + [30] * 21, "upper": [0, 12, 24, 36] + [40] * 21}
    g = run("X.curveGeometry(C, 600, 200, {l: 40, r: 10, t: 10, b: 20})", C=c)
    assert g["pad"] == {"l": 40, "r": 10, "t": 10, "b": 20} and g["dot"]["y"] == pytest.approx(10 + 170 * (1 - 300 / (300 * 1.08)), abs=0.1) and g["dot"]["x"] == pytest.approx(40 + 550 * 3 / 24, abs=0.1) and g["base"] == 180


def test_the_bars_are_one_a_day_with_the_usual_as_a_line(reports):
    a = reports["one"]["accounts"][0]
    g = run("X.barsGeometry(D, U, 640, 170)", D=a["days"], U=a["usualDay"])
    assert len(g["bars"]) == 16 and g["bars"][-1]["partial"] is True and g["bars"][-1]["date"] == "2026-10-22" and g["bars"][0]["label"] == "07/10" and g["bars"][-1]["label"] == "22/10"
    assert all(b["h"] >= 0 and b["y"] + b["h"] == pytest.approx(g["base"], abs=0.2) for b in g["bars"]) and g["usualY"] is not None and g["usualY"] < g["base"]
    full = [b for b in g["bars"][1:-1]]
    assert len({b["h"] for b in full}) == 1 and g["bars"][-1]["h"] > full[0]["h"] > 0                       # the spike makes today's bar taller than a usual day's
    assert all(g["bars"][i]["x"] < g["bars"][i + 1]["x"] for i in range(15))
    assert run("X.barsGeometry(D, 0, 640, 170)", D=[{"date": "2026-10-21", "cost": 0, "partial": False}])["usualY"] is None


def test_the_csv_has_a_line_per_finding_and_one_for_an_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    head = run("X.HEAD")
    assert rows[0] == ["Spend Anomaly Alert", "Group"] and rows[3] == head and head[3] == "Level" and head[-1] == "Next step" and len(head) == 15
    body = rows[4:]
    assert all(len(r) == len(head) for r in body) and len(body) == len(reports["mcc"]["accounts"][0]["findings"]) + 3
    acme = [r for r in body if r[0] == "Acme Dental"]
    assert [r[4] for r in acme] == [f["kind"].capitalize() for f in reports["mcc"]["accounts"][0]["findings"]] and acme[0][1] == "Google Ads" and acme[0][2] == "Above usual" and acme[0][3] == "High" and acme[0][5].startswith("2026-10-22")
    assert acme[0][11] == 270 and acme[0][12] == 120 and acme[0][13] == 240
    delta = [r for r in body if r[0] == "Delta"][0]
    assert delta[2] == "No spend" and delta[6] == "No spend in the last 15 days" and delta[3] == "" and delta[11] == 0 and delta[12] == ""
    assert [r for r in body if r[0] == "Beta Clinic"][0][6] == "Nothing unusual"


def test_the_team_figures_are_shown_as_text_and_an_ended_push_is_said_to_have_ended():
    v = run("X.savedView(S, '2026-10-22')", S={"usualDaily": 1200.5, "maxDaily": 3000, "minSwing": None, "mutedUntil": "2026-10-21", "note": "Spring"})
    assert v == {"usualDaily": "1200.5", "maxDaily": "3000", "minSwing": "", "mutedUntil": "2026-10-21", "note": "Spring", "ended": True}
    assert run("X.savedView(S, '2026-10-21')", S={"mutedUntil": "2026-10-21"})["ended"] is False and run("X.savedView(null, null)") == {"usualDaily": "", "maxDaily": "", "minSwing": "", "mutedUntil": "", "note": "", "ended": False}


def test_the_query_asks_for_the_account_and_a_refresh():
    assert run("X.query('act_55')") == "account=act_55" and run("X.query('a b', true)") == "account=a%20b&refresh=1"


# ── the page script ─────────────────────────────────────────────────────────

SCOPE = os.path.join(JS, "pm_scope.js")
SCRIPTS = [SCOPE, DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}
SETTINGS = {"accounts": {}, "sensitivity": None, "ratios": {"low": [2.5, 4.0], "normal": [1.75, 2.75], "high": [1.4, 2.0]}, "defaultSensitivity": "normal", "changes": []}


def page(scenario):
    out = subprocess.run(["node", "-e", HARNESS, json.dumps(SCRIPTS), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(report, steps=None, **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "body": report}, {"match": "/anomaly/settings", "body": SETTINGS}],
          "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("monitor_panel.js") < html.index("anomaly_data.js") < html.index("anomaly.js")
    for text in (html, js, open(DATA, encoding="utf-8").read()):
        assert "—" not in text and "–" not in text


def test_the_accounts_and_what_the_team_saved_are_loaded_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/settings", "body": SETTINGS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and "MCC" in items[1]["badge"] and len(items) == 6 and out["panels"] == [{"agent": "spend", "label": "Spend Anomaly Alert"}]
    assert any(c.endswith("/anomaly/settings") for c in out["calls"]) and not any("/anomaly/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports["one"])
    assert [c for c in out["calls"] if "/anomaly/report" in c] == ["/p2/paid-marketing/api/anomaly/report?account=1234567890"]
    assert out["store"]["an_account"] == "1234567890" and out["hist"][-1].endswith("?account=1234567890") and out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True


def test_the_report_is_drawn_with_the_finding_its_cause_the_plan_and_the_two_charts(reports):
    out = opened(reports["one"])
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Accounts read", "Need a look", "High", "Spent so far today", "Too new to judge"):
        assert text in kp, text
    assert 'class="bi-row stopped open"' in lst and "bi-pill over" in lst and "Above usual" in lst and "America/Los_Angeles" in lst and "2026-10-22 12:00" in lst and "$270 by 12:00, usual $120 (2.3x)" in lst
    assert lst.count('class="sa-f lv-') == len(reports["one"]["accounts"][0]["findings"]) and "What is unusual" in lst and "Campaign that moved" in lst and "<b>Generic</b>" in lst and "Share of the excess" in lst
    assert "a budget raised from $50.00 to $400 by zed@y.com (made no change in the days before)" in lst and "What to check" in lst and "<details open>" in lst and "Check again later today" in lst
    assert lst.count("<svg") == 2 and 'class="sa-today"' in lst and 'class="sa-usual"' in lst and 'class="sa-upper"' in lst and lst.count('class="sa-bar') == 16 and 'class="sa-bar partial"' in lst and "<title>2026-10-22: $270</title>" in lst
    assert "Today through the day" in lst and "The last days" in lst and "Smallest swing flagged" in lst and 'Daily budgets added up</div><div class="v">$250</div>' in lst
    assert 'class="sa-set" data-account="1234567890"' in lst and 'data-k="maxDaily" type="text" value="3000"' in lst and 'data-k="mutedUntil" type="date"' in lst and 'value="Spring push"' in lst
    assert "read " in out["el"]["#fresh"]["html"] and "Acme Dental" in out["el"]["#fresh"]["html"] and "the ones to look at first" in out["el"]["#listNote"]["text"]
    assert 'aria-checked="true"' in out["el"]["#sens"]["html"] and "Normal" in out["el"]["#sens"]["html"]
    assert out["el"]["#sens"]["html"].count('aria-checked="true"') == 1 and 'class="pm-chip on" data-s="normal"' in out["el"]["#sens"]["html"]
    assert out["el"]["#sensText"]["text"].startswith("How far above its usual an account must spend before it is flagged: 1.75x a usual pace is medium, 2.75x is high")


def test_a_group_opens_to_the_accounts_that_need_a_look_and_names_escape_their_markup(reports):
    mcc = opened(reports["mcc"], [{"pick": GROUP}])
    lst = mcc["el"]["#list"]["html"]
    assert "Acme Dental" in lst and "Beta Clinic" not in lst and "Gamma" not in lst and "Needs a look <span class=\"n\">1</span>" in mcc["el"]["#views"]["html"] and "MCC · 4 accounts watched together" in mcc["el"]["#fresh"]["html"]
    allv = opened(reports["mcc"], [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])["el"]["#list"]["html"]
    assert "Gamma &lt;b&gt;&amp;&lt;/b&gt; Co" in allv and "<b>&</b>" not in allv and "Delta" in allv and "bi-pill ok" in allv and "bi-pill unk" in allv and "Too new to judge" in allv and "No spend" in allv


def test_an_account_opens_and_closes_when_its_row_is_pressed(reports):
    steps = [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}]
    closed = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/settings", "body": SETTINGS}, {"match": "/anomaly/report", "body": reports["mcc"]}], "steps": steps})["el"]["#list"]["html"]
    assert 'class="bi-row clear"' in closed and "<svg" not in closed
    opened_ = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/settings", "body": SETTINGS}, {"match": "/anomaly/report", "body": reports["mcc"]}],
                    "steps": steps + [{"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "2222222222"}}}]}]})["el"]["#list"]["html"]
    assert 'class="bi-row clear open"' in opened_ and "Nothing unusual: today so far and yesterday are in line" in opened_ and opened_.count("<svg") == 2


def test_an_account_with_no_spend_or_too_few_days_says_so_in_the_body(reports):
    steps = [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "4444444444"}}}]},
             {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "3333333333"}}}]}]
    lst = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/settings", "body": SETTINGS}, {"match": "/anomaly/report", "body": reports["mcc"]}], "steps": steps})["el"]["#list"]["html"]
    assert "This account spent nothing in the last 15 days." in lst and "Fewer than 4 days of spend are known, so a usual day cannot be worked out yet." in lst


def test_a_meta_account_is_marked_and_an_account_without_hours_says_so_instead_of_drawing_the_day(reports):
    meta = opened(reports["meta"])["el"]["#list"]["html"]
    assert 'class="bi-mk">Meta</span>' in meta and "€" in meta and "Above usual" in meta
    flat = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "body": reports["flat"]}, {"match": "/anomaly/settings", "body": SETTINGS}], "steps": [{"pick": "2222222222"}]})
    lst = flat["el"]["#list"]["html"]
    assert "whole days only" in lst and "There is no spend by hour for this account, so only whole days are shown." in lst and lst.count("<svg") == 1
    assert "gave no spend by hour" in flat["el"]["#insights"]["html"]


def test_a_muted_push_is_shown_as_expected_with_the_note(reports):
    out = opened(reports["muted"])
    lst = out["el"]["#list"]["html"]
    assert "Expected" in lst and "Expected (muted until 2026-10-22): " in lst and "Spring push" in out["el"]["#insights"]["html"] and "bi-pill ok" in lst


def test_the_figures_the_team_saves_for_an_account_are_sent_and_the_report_is_read_again(reports):
    areas = [{"k": "usualDaily", "value": "1,200"}, {"k": "maxDaily", "value": "3000"}, {"k": "minSwing", "value": ""}, {"k": "mutedUntil", "value": "2026-11-30"}, {"k": "note", "value": "Black Friday"}]
    saved = {"usualDaily": 1200.0, "maxDaily": 3000.0, "minSwing": None, "mutedUntil": "2026-11-30", "note": "Black Friday"}
    out = opened(reports["one"], [{"submit": ["#list", {".sa-set": {"dataset": {"account": "1234567890"}, "areas": areas}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "body": reports["one"]}, {"match": "/anomaly/settings", "body": SETTINGS},
                            {"match": "/anomaly/settings/account", "method": "POST", "body": {"account": saved}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/anomaly/settings/account", "body": {"account": "1234567890", "usualDaily": "1,200", "maxDaily": "3000", "minSwing": "", "mutedUntil": "2026-11-30", "note": "Black Friday"}}]
    assert out["outs"][0]["[data-out]"] == "Saved. Judging again…" and len([c for c in out["calls"] if "/anomaly/report" in c]) == 2


def test_a_figure_the_server_refuses_says_why_and_nothing_is_read_again(reports):
    out = opened(reports["one"], [{"submit": ["#list", {".sa-set": {"dataset": {"account": "1234567890"}, "areas": [{"k": "maxDaily", "value": "lots"}]}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "body": reports["one"]}, {"match": "/anomaly/settings", "body": SETTINGS},
                            {"match": "/anomaly/settings/account", "method": "POST", "status": 400, "body": {"error": "The daily ceiling must be a number."}}])
    assert out["outs"][0]["[data-out]"] == "The daily ceiling must be a number." and len([c for c in out["calls"] if "/anomaly/report" in c]) == 1
    down = opened(reports["one"], [{"submit": ["#list", {".sa-set": {"dataset": {"account": "1234567890"}, "areas": []}}]}], reject="/anomaly/settings/account")
    assert down["outs"][0]["[data-out]"] == "The server could not be reached. Try again."


def test_the_sensitivity_is_saved_for_everyone_and_the_report_is_judged_again(reports):
    out = opened(reports["one"], [{"fire": ["#sens", {"button": {"dataset": {"s": "high"}}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "body": reports["one"]}, {"match": "/anomaly/settings", "body": SETTINGS},
                            {"match": "/anomaly/settings/sensitivity", "method": "POST", "body": {"sensitivity": "high"}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/anomaly/settings/sensitivity", "body": {"sensitivity": "high"}}] and out["el"]["#sensOut"]["text"] == "Saved. Judging again…"
    assert len([c for c in out["calls"] if "/anomaly/report" in c]) == 2
    bad = opened(reports["one"], [{"fire": ["#sens", {"button": {"dataset": {"s": "wild"}}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "body": reports["one"]}, {"match": "/anomaly/settings", "body": SETTINGS},
                            {"match": "/anomaly/settings/sensitivity", "method": "POST", "status": 400, "body": {"error": "The sensitivity must be low, normal or high."}}])
    assert bad["el"]["#sensOut"]["text"] == "The sensitivity must be low, normal or high." and len([c for c in bad["calls"] if "/anomaly/report" in c]) == 1
    nothing = opened(reports["one"], [{"fire": ["#sens", {"button": {"dataset": {}}}]}])
    assert nothing["posts"] == []


def test_a_refresh_asks_for_a_fresh_read_and_a_cooldown_a_spent_quota_and_a_dead_server_each_say_so(reports):
    out = opened(reports["one"], [{"click": "#refreshBtn"}])
    assert [c for c in out["calls"] if "/anomaly/report" in c][-1].endswith("account=1234567890&refresh=1")
    cool = opened({}, [], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "status": 429, "body": {"error": "Refreshed a moment ago.", "kind": "cooldown"}}])
    assert "Refreshed a moment ago." in cool["el"]["#msg"]["html"] and "pm-note" in cool["el"]["#msg"]["html"]
    quota = opened({}, [], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "status": 429, "body": {"error": "The daily limit is used up.", "kind": "quota"}}])
    assert "The daily limit is used up." in quota["el"]["#msg"]["html"] and "clears on its own" in quota["el"]["#msg"]["html"]
    assert "could not be reached" in opened({}, [], reject="/anomaly/report")["el"]["#msg"]["html"]


def test_an_mcc_says_it_takes_a_little_longer_while_it_reads_and_the_warnings_are_shown(reports):
    held = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/settings", "body": SETTINGS}, {"match": "/anomaly/report", "body": reports["mcc"], "hold": True}], "steps": [{"pick": GROUP}]})
    assert "Reading all 4 accounts of Group together" in held["el"]["#msg"]["html"] and held["el"]["#skel"]["hidden"] is False
    done = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/settings", "body": SETTINGS}, {"match": "/anomaly/report", "body": reports["mcc"], "hold": True}], "steps": [{"pick": GROUP}, {"release": True}]})
    assert done["el"]["#out"]["hidden"] is False and "Delta: The account&#39;s time zone is not known" in done["el"]["#msg"]["html"]


def test_the_csv_download_is_one_line_a_finding_with_a_byte_order_mark_and_no_formula_runs(reports):
    out = opened(reports["one"], [{"click": "#exportBtn"}])
    assert out["downloads"][0]["name"].startswith("spend-anomaly-Acme-Dental-2026-10-22") and out["downloads"][0]["name"].endswith(".csv") and out["blob"].startswith("﻿")
    lines = out["blob"][1:].split("\r\n")
    assert lines[0] == "Spend Anomaly Alert,Acme Dental" and lines[3].startswith("Account,Platform,Status,Level,Kind,Day,Problem,Detail,Spent,Usual,Ratio") and any(l.startswith("Acme Dental,Google Ads,Above usual,High,Surge,2026-10-22,") for l in lines)
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["findings"][0]["title"] = "=HYPERLINK(1)"
    assert "'=HYPERLINK(1)" in opened(risky, [{"click": "#exportBtn"}])["blob"]


def test_a_second_choice_ignores_an_answer_that_comes_back_late(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/settings", "body": SETTINGS},
                              {"match": "/anomaly/report?account=1234567890", "body": reports["one"], "hold": True}, {"match": "/anomaly/report?account=2222222222", "body": reports["clean"]}],
                "steps": [{"pick": "1234567890"}, {"pick": "2222222222"}, {"release": True}]})
    assert "Beta Clinic" in out["el"]["#list"]["html"] and "Acme Dental" not in out["el"]["#list"]["html"]


# ── the edges of the helpers ────────────────────────────────────────────────

def _summary(**counts):
    c = dict({"over": 0, "under": 0, "ok": 0, "new": 0, "none": 0}, **counts)
    return {"summary": {"counts": c, "accounts": sum(c.values()), "high": 0, "overall": None}}


def test_an_account_below_usual_counts_as_needing_a_look_in_the_tile():
    t = run("X.tiles(R)", R=_summary(over=1, under=2))
    assert t[1] == ["Need a look", "3", "1 above usual, 2 below", "warn"]
    assert run("X.tiles(R)", R=_summary(under=1))[1] == ["Need a look", "1", "0 above usual, 1 below", "warn"]


def test_the_spent_tile_turns_warn_only_when_a_quarter_over_the_usual():
    def tile(cost):
        r = _summary(ok=1)
        r["summary"]["overall"] = {"cost": cost, "usual": 100, "accounts": 1, "currency": "USD"}
        return run("X.tiles(R)", R=r)[3]
    assert tile(125)[3] == "" and tile(125.01)[3] == "warn" and tile(110)[3] == "" and tile(300)[2] == "a usual $100 by now in 1 account"


def test_a_new_account_with_a_saved_usual_is_not_told_to_save_one():
    base = {"account": {"isGroup": False}, "accounts": [{"name": "A", "status": "new", "hourly": True, "saved": {}, "today": {"date": "2026-10-22"}}]}
    assert [i["kind"] for i in run("X.insights(R)", R=base)] == ["new"]
    base["accounts"][0]["saved"] = {"usualDaily": 100}
    assert run("X.insights(R)", R=base) == []


def test_a_bar_is_two_thirds_of_its_slot_and_the_scale_holds_the_usual_day():
    days = [{"date": "2026-10-%02d" % (7 + i), "cost": 10.0, "partial": i == 15} for i in range(16)]
    g = run("X.barsGeometry(D, 100, 640, 170)", D=days)
    assert g["bars"][0]["w"] == 24.7 and g["bars"][0]["x"] == 49.8 and g["bars"][1]["x"] == 86.2
    assert g["top"] == pytest.approx(110.0) and g["bars"][0]["h"] == pytest.approx(132 * 10 / 110, abs=0.1) and g["usualY"] == pytest.approx(12 + 132 * (1 - 100 / 110), abs=0.1) and g["usualY"] > 12


def test_a_page_with_no_hour_data_draws_no_curve_and_every_account_row_has_a_figure(reports):
    lst = opened(reports["mcc"], [{"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])["el"]["#list"]["html"]
    assert lst.count("bs-fig") == 4 and lst.count('class="bi-n"') == 4 and 'class="bi-n">2.3x</span>' in lst


def test_an_open_account_stays_open_and_the_report_stays_on_screen_while_the_figures_are_judged_again(reports):
    areas = [{"k": "maxDaily", "value": "3000"}]
    responses = [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/settings", "body": SETTINGS}, {"match": "/anomaly/report", "body": reports["mcc"], "holdFrom": 2},
                 {"match": "/anomaly/settings/account", "method": "POST", "body": {"account": {"maxDaily": 3000.0}}}]
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}, {"submit": ["#list", {".sa-set": {"dataset": {"account": "1234567890"}, "areas": areas}}]}]
    out = page({"responses": responses, "steps": steps})
    assert 'class="bi-row stopped open"' in out["el"]["#list"]["html"] and out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True and len([c for c in out["calls"] if "/anomaly/report" in c]) == 2
    released = page({"responses": responses, "steps": steps + [{"release": True}]})
    assert 'class="bi-row stopped open"' in released["el"]["#list"]["html"]


def test_pressing_an_open_row_closes_it(reports):
    row = {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}
    closed = opened(reports["one"], [{"fire": ["#list", row]}])["el"]["#list"]["html"]
    assert 'class="bi-row stopped"' in closed and "<svg" not in closed
    again = opened(reports["one"], [{"fire": ["#list", row]}, {"fire": ["#list", row]}])["el"]["#list"]["html"]
    assert 'class="bi-row stopped open"' in again and again.count("<svg") == 2


def test_a_healthy_account_has_no_plan_and_no_budget_fact(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "body": reports["clean"]}, {"match": "/anomaly/settings", "body": SETTINGS}], "steps": [{"pick": "2222222222"}]})
    lst = out["el"]["#list"]["html"]
    assert "What to check" not in lst and "Daily budgets added up" not in lst and "Nothing unusual: today so far and yesterday are in line" in lst and "What is unusual" not in lst and lst.count("<svg") == 2
    acme = opened(reports["one"])["el"]["#list"]["html"]
    assert acme.count("Daily budgets added up") == 1 and "What to check" in acme


def test_a_text_that_would_run_as_a_formula_gets_a_quote_and_a_comma_or_a_line_break_is_quoted(reports):
    risky = json.loads(json.dumps(reports["one"]))
    f = risky["accounts"][0]["findings"]
    f[0]["title"], f[1]["title"] = "-2+3", "a, b\nc \"d\""
    blob = opened(risky, [{"click": "#exportBtn"}])["blob"]
    assert ",'-2+3," in blob and ',"a, b\nc ""d""",' in blob


def test_the_charts_label_every_other_day_and_mark_the_usual_only_when_there_is_one(reports):
    lst = opened(reports["one"])["el"]["#list"]["html"]
    bars = lst.split('aria-label="Spend each day against the usual day"', 1)[1].split("</svg>", 1)[0]
    assert bars.count('class="sa-lbl"') == 9 and ">22/10<" in bars and ">07/10<" not in bars and ">08/10<" in bars and 'class="sa-usual-l"' in bars and ">usual<" in bars
    clean = json.loads(json.dumps(reports["clean"]))
    clean["accounts"][0]["usualDay"] = 0
    bars = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/anomaly/report", "body": clean}, {"match": "/anomaly/settings", "body": SETTINGS}],
                 "steps": [{"pick": "2222222222"}]})["el"]["#list"]["html"].split('aria-label="Spend each day against the usual day"', 1)[1].split("</svg>", 1)[0]
    assert 'class="sa-usual-l"' not in bars
