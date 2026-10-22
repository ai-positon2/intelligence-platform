"""KPI Monitoring: static/js/kpi_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads each campaign's last two weeks and works out what is off target (tests/test_paid_kpi*.py). The page only draws, so how a figure, a status and a level read, which accounts a view
shows, how a campaign's four figures sit against their targets and the CSV come from kpi_data.js (executed here, not read), and kpi.js is run for real: it loads the accounts and what the team
saved, draws the report the server built, saves targets and leaves campaigns out, and says what went wrong in words. The HTML it writes is read back.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_kpi as TK  # noqa: E402
from tests.test_copycheck_js import HARNESS  # noqa: E402
from tracker import paid_kpi as k  # noqa: E402
from tracker import paid_kpi_settings as st  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "kpi_data.js"), os.path.join(JS, "kpi.js")
TEMPLATE = os.path.join(ROOT, "templates", "kpi_monitoring.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [TK.ACCT, TK.ACCT2]
STRATEGY = {"targetCpa": {"targetCpaMicros": "40000000"}}


def run(expr, **data):
    pre = "".join("const %s=%s;" % (key, json.dumps(v)) for key, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def days_of(cid, cost, conv, **kw):
    return TK.day_rows(cid, cost=cost, conv=conv, **kw)


def world():
    camps = {TK.ACCT["id"]: [TK.camp_row("5", "Search <b>Core</b>"), TK.camp_row("6", "Brand"), TK.camp_row("7", "Learner", system="LEARNING_NEW"), TK.camp_row("8", "Few"), TK.camp_row("9", "Old Test")],
             TK.ACCT2["id"]: [TK.camp_row("5", "Fine", **STRATEGY)]}
    days = {TK.ACCT["id"]: days_of("5", 100.0, 1.0) + days_of("6", 40.0, 1.0) + days_of("7", 40.0, 1.0) + days_of("8", 40.0, 0.2) + days_of("9", 40.0, 1.0),
            TK.ACCT2["id"]: days_of("5", 40.0, 1.0)}
    return camps, days


def mon_store():
    mon = MemStore()
    st.save_account(mon, TK.ACCT["id"], "Acme Dental", {"targets": {"cpa": "40", "roas": "", "cvr": "", "ctr": ""}, "tolerance": "20"}, "t")
    st.save_campaign(mon, TK.ACCT["id"], "6", "Brand", {"cpa": "80"}, "t")
    st.mute(mon, TK.ACCT["id"], "9", "Old Test", "a test campaign", "t")
    return mon


@pytest.fixture(scope="module")
def reports():
    mp = pytest.MonkeyPatch()
    from tracker import paid_gads as base, paid_geo, paid_store
    saved = {}
    mp.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    mp.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    mp.setattr(paid_store, "get_gads_entry", lambda key, ttl: None, raising=False)
    group = {"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in ACCOUNTS]}
    camps, days = world()
    mon = mon_store()

    def report(account, search, **kw):
        paid_geo.clear_cache()
        base.reset_state()
        base.forget("")
        return k.report_for(account, accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [group], search_fn=search, today=TK.TODAY, at=TK.AT, **kw)
    out = {"one": report(TK.ACCT["id"], TK.searcher(camps=camps, days=days), mon=mon),
           "mcc": report(GROUP, TK.searcher(camps=camps, days=days), mon=mon),
           "clean": report(TK.ACCT2["id"], TK.searcher(camps=camps, days=days), mon=mon),
           "notargets": report(TK.ACCT["id"], TK.searcher(camps=camps, days=days))}
    rows = [TK.meta_rows(day=(TK.TODAY - __import__("datetime").timedelta(days=i)).isoformat(), spend="100", leads="1") for i in range(1, 15)]
    meta_mon = MemStore()
    st.save_account(meta_mon, "act_1234567", "Beta Meta", {"targets": {"cpa": "40"}}, "t")
    out["meta"] = report("act_1234567", TK.searcher(), mon=meta_mon, meta_pages_fn=TK.meta_pages(insights=rows), meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR"})
    out["settings"] = st.view(mon)
    yield out
    mp.undo()


# ── the data helpers ────────────────────────────────────────────────────────

def test_a_status_a_level_and_a_state_read_in_words_and_an_unknown_one_reads_as_itself():
    assert run("X.statusInfo('fix')") == {"label": "Needs a fix", "tone": "bad"} and run("X.statusInfo('check')")["label"] == "Worth a look" and run("X.statusInfo('clean')") == {"label": "On track", "tone": "ok"}
    assert run("X.statusInfo('zz')") == {"label": "zz", "tone": "unk"}
    assert run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('low')")["tone"] == "wait" and run("X.levelInfo('q')") == {"label": "q", "tone": "unk"}
    assert [run("X.stateInfo(%s)" % json.dumps(s))["label"] for s in ("on", "off", "unknown", "none", "zz")] == ["On target", "Off target", "Not enough data", "No target", "zz"]
    assert [run("X.channelText(%s)" % json.dumps(c)) for c in ("SEARCH", "PERFORMANCE_MAX", "META_LEADS", "META_SALES", "", "ZZ")] == ["Search", "Performance Max", "Meta leads", "Meta sales", "Campaign", "ZZ"]


def test_money_a_multiple_and_a_rate_are_written_as_the_server_writes_them():
    assert [run("X.fmt('cpa', %s, 'USD')" % v) for v in ("12.5", "1234.5", "99.999", "100")] == ["$12.50", "$1,235", "$100.00", "$100"]
    assert run("X.fmt('cpa', 40, 'CHF')") == "40.00 CHF" and run("X.fmt('cpa', 40, 'EUR')") == "€40.00" and run("X.fmt('cpa', 4000, 'GBP')") == "£4,000"
    assert run("X.fmt('roas', 3.456)") == "3.46x" and run("X.fmt('roas', 4)") == "4.00x"
    assert run("X.fmt('cvr', 0.05)") == "5.0%" and run("X.fmt('ctr', 0.01234)") == "1.2%" and run("X.fmt('cvr', 0.0125)") == "1.3%" and run("X.fmt('ctr', 1)") == "100.0%"
    assert run("X.fmt('cpa', null, 'USD')") == "n/a" and run("X.fmt('roas', null)") == "n/a" and run("X.money(null, 'USD')") == "n/a"


def test_the_figures_match_what_the_server_writes_in_its_own_findings(reports):
    f = [f for i in reports["one"]["accounts"][0]["items"] for f in i["findings"] if f["rule"] == "cpa"][0]
    assert "$100.00" not in f["title"] and "$100 a conversion" in f["title"] and "$40.00" in f["title"]
    assert run("X.fmt('cpa', 100, 'USD')") == "$100" and run("X.fmt('cpa', 40, 'USD')") == "$40.00"


def test_the_tiles_say_what_to_fix_and_what_was_not_judged(reports):
    t = run("X.tiles(R)", R=reports["one"])
    assert [x[0] for x in t] == ["Campaigns read", "To fix", "Off target", "Not judged", "Accounts to fix"]
    s = reports["one"]["summary"]
    assert t[0] == ["Campaigns read", "5", "%d on target, %d off" % (s["onTarget"], s["offTarget"]), ""] and t[1][0:2] == ["To fix", str(s["high"] + s["medium"])] and t[1][3] == "warn"
    assert t[2] == ["Off target", "1", "of 3 campaigns that could be judged", "warn"] and t[3] == ["Not judged", "1", "0 with no target, 1 with too little data", ""] and t[4][1] == "1"
    c = run("X.tiles(R)", R=reports["clean"])
    assert c[1] == ["To fix", "0", "nothing is off its target or held back", "good"] and c[2][3] == "good" and c[4] == ["Accounts to fix", "0", "of 1 account read", "good"]
    low = json.loads(json.dumps(reports["clean"]))
    low["summary"]["counts"] = {"fix": 0, "check": 2, "clean": 0}
    assert run("X.tiles(R)", R=low)[4][2] == "2 worth a look only"


def test_the_views_hold_the_accounts_that_need_a_look(reports):
    assert [a["name"] for a in run("X.shown(R, 'attention')", R=reports["mcc"])] == ["Acme Dental"] and len(run("X.shown(R, 'all')", R=reports["mcc"])) == 2
    assert run("X.firstView(R)", R=reports["mcc"]) == "attention" and run("X.firstView(R)", R=reports["clean"]) == "all" and run("X.viewCount(R, 'all')", R=reports["mcc"]) == 2
    assert run("X.needsLook({status: 'check'})") is True and run("X.needsLook({status: 'clean'})") is False
    assert run("X.query('1234567890', true)") == "account=1234567890&refresh=1" and run("X.query('act_1', false)") == "account=act_1" and run("X.query('a b', false)") == "account=a%20b"


def test_the_campaigns_with_something_to_look_at_the_quiet_ones_and_the_left_out_are_told_apart(reports):
    a = reports["one"]["accounts"][0]
    assert [c["name"] for c in run("X.flagged(A)", A=a)] == ["Search <b>Core</b>", "Learner"]
    assert sorted(c["name"] for c in run("X.quiet(A)", A=a)) == ["Brand", "Few"] and [c["name"] for c in run("X.leftOut(A)", A=a)] == ["Old Test"]


def test_a_left_out_campaign_is_never_one_to_look_at_even_if_it_carried_findings():
    a = {"items": [{"id": "1", "name": "A", "muted": True, "findings": [{"rule": "cpa"}]}, {"id": "2", "name": "B", "muted": False, "findings": [{"rule": "cpa"}]}, {"id": "3", "name": "C", "muted": False, "findings": []}]}
    assert [c["name"] for c in run("X.flagged(A)", A=a)] == ["B"] and [c["name"] for c in run("X.quiet(A)", A=a)] == ["C"] and [c["name"] for c in run("X.leftOut(A)", A=a)] == ["A"]


def test_a_figure_that_only_the_week_before_had_is_still_shown():
    rows = run("X.metricRows(I, 'USD')", I={"metrics": [{"metric": "cpa", "value": None, "target": None, "prior": 30, "state": "none"}]})
    assert [(r["metric"], r["now"], r["before"], r["target"]) for r in rows] == [("cpa", "n/a", "$30.00", "none saved")]


def test_a_campaigns_figures_sit_against_their_targets_in_order_and_what_has_nothing_is_left_out(reports):
    a = reports["one"]["accounts"][0]
    core = [i for i in a["items"] if i["name"].startswith("Search")][0]
    rows = run("X.metricRows(I, 'USD')", I=core)
    assert [r["metric"] for r in rows] == ["cpa", "cvr", "ctr"][:len(rows)] or rows[0]["metric"] == "cpa"
    cpa = rows[0]
    assert cpa["label"] == "Cost per conversion" and cpa["now"] == "$100" and cpa["before"] == "$100" and cpa["target"] == "$40.00" and cpa["source"] == "saved for the account" and cpa["state"] == "off" and cpa["why"] == ""
    few = [i for i in a["items"] if i["name"] == "Few"][0]
    f = run("X.metricRows(I, 'USD')", I=few)[0]
    assert f["now"] == "n/a" and f["state"] == "unknown" and f["why"].startswith("only 1.4 conversions in the week") and f["target"] == "$40.00"
    brand = [i for i in a["items"] if i["name"] == "Brand"][0]
    assert run("X.metricRows(I, 'USD')", I=brand)[0]["target"] == "$80.00" and run("X.metricRows(I, 'USD')", I=brand)[0]["source"] == "saved for this campaign" and run("X.metricRows(I, 'USD')", I=brand)[0]["state"] == "on"
    assert run("X.metricRows(I, 'USD')", I={"metrics": [{"metric": "roas", "value": None, "target": None, "prior": None, "state": "none"}]}) == []
    order = run("X.metricRows(I, 'USD')", I={"metrics": [{"metric": "ctr", "value": 0.02, "target": None, "prior": None, "state": "none"}, {"metric": "cpa", "value": 5, "target": None, "prior": None, "state": "none"}]})
    assert [r["metric"] for r in order] == ["cpa", "ctr"] and order[0]["target"] == "none saved" and order[0]["source"] == ""
    strategy = run("X.metricRows(I, 'USD')", I={"metrics": [{"metric": "cpa", "value": 5, "target": 4, "source": "strategy", "prior": None, "state": "off"}]})
    assert strategy[0]["source"] == "the campaign's bid strategy" and strategy[0]["before"] == "n/a"


def test_the_account_as_one_shows_only_the_figures_that_have_one(reports):
    rows = run("X.headlineRows(A)", A=reports["one"]["accounts"][0])
    assert rows[0]["metric"] == "cpa" and rows[0]["target"] == "$40.00" and rows[0]["source"] == "saved for the account"
    assert run("X.headlineRows({})") == []


def test_what_shows_only_across_the_campaigns_is_said_in_sentences(reports):
    ins = run("X.insights(R)", R=reports["one"])
    assert [i["kind"] for i in ins] == ["data", "muted"]
    assert ins[0]["text"] == "1 campaign with a target had too little in the last 7 days to be judged (at least 5 conversions are needed for a cost or return)." and ins[1]["text"] == "1 campaign is left out of the checks by the team."
    none = run("X.insights(R)", R=reports["notargets"])
    assert none[0]["kind"] == "targets" and none[0]["text"].startswith("No target is saved here and no campaign states one in its bid strategy")
    assert run("X.insights(R)", R=reports["clean"]) == []
    some = json.loads(json.dumps(reports["one"]))
    some["accounts"][0].update(withoutTarget=2, notJudged=0, muted=0)
    assert run("X.insights(R)", R=some) == [{"kind": "targets", "text": "2 campaigns of 5 have no target, so only their delivery, learning and budget were checked."}]
    many = run("X.insights(R)", R=reports["mcc"])
    assert many[0]["text"].startswith("Acme Dental: ")
    assert run("X.insights(R)", R={"account": {"isGroup": False}, "accounts": [{"name": "A", "campaigns": 0}]}) == [{"kind": "none", "text": "No enabled campaign was found."}]
    both = json.loads(json.dumps(reports["one"]))
    both["accounts"][0].update(muted=2, notJudged=2)
    assert [i["text"] for i in run("X.insights(R)", R=both)] == ["2 campaigns with a target had too little in the last 7 days to be judged (at least 5 conversions are needed for a cost or return).",
                                                                   "2 campaigns are left out of the checks by the team."]


def test_the_csv_has_one_line_per_finding_and_one_per_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    assert rows[0] == ["KPI Monitoring", "Group"] and rows[3] == run("X.HEAD") and len(rows[3]) == 8
    body = rows[4:]
    found = sum(a["findingCount"] for a in reports["mcc"]["accounts"])
    assert found == 2 and len([r for r in body if r[2] != "(nothing found)"]) == found and [r for r in body if r[2] == "(nothing found)"][0][0] == "Beta Clinic"
    cpa = [r for r in body if r[4] == "Cost per conversion above target"][0]
    assert cpa[0] == "Acme Dental" and cpa[1] == "Google Ads" and cpa[3] == "High" and cpa[6].startswith("Last 7 days: $100 (target $40.00") and " | " in cpa[6] and cpa[7].startswith("Look at what spent")
    assert run("X.csvRows(R)[2]", R=reports["one"]) == [] and run("X.csvRows(R)[1]", R=reports["one"]) == ["Read", reports["one"]["readAt"]]
    assert all(r[1] == "Meta" for r in run("X.csvRows(R)", R=reports["meta"])[4:])


def test_what_the_team_saved_is_shown_as_the_forms_hold_it():
    saved = {"targets": {"cpa": 45.5, "roas": None, "cvr": 3.0, "ctr": None}, "tolerance": 25.0, "campaigns": {"5": {"cpa": 20.0, "roas": None, "cvr": None, "ctr": 8.0}}, "muted": {"6": "a test"}}
    assert run("X.savedView(S)", S=saved) == {"cpa": "45.5", "roas": "", "cvr": "3", "ctr": "", "tolerance": "25"}
    assert run("X.savedView(null)") == {"cpa": "", "roas": "", "cvr": "", "ctr": "", "tolerance": ""}
    assert run("X.campaignView(S, '5')", S=saved) == {"cpa": "20", "roas": "", "cvr": "", "ctr": "8"} and run("X.campaignView(S, '9')", S=saved) == {"cpa": "", "roas": "", "cvr": "", "ctr": ""}
    assert run("X.mutedWhy(S, '6')", S=saved) == "a test" and run("X.mutedWhy(S, '7')", S=saved) == "" and run("X.mutedWhy(null, '7')") == ""


# ── the page script ─────────────────────────────────────────────────────────

SCRIPTS = [DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}
HARNESS_PAGE = HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/kpi-monitoring")


def page(scenario):
    out = subprocess.run(["node", "-e", HARNESS_PAGE, json.dumps(SCRIPTS), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(reports, key, steps=None, **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kpi/report", "body": reports[key]}, {"match": "/kpi/settings", "body": reports["settings"]}],
          "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def reply(reports, key="one", extra=()):
    return [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kpi/report", "body": reports[key]}, {"match": "/kpi/settings", "body": reports["settings"]}] + list(extra)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("monitor_panel.js") < html.index("kpi_data.js") < html.index("kpi.js")
    assert "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_the_accounts_and_what_the_team_saved_are_loaded_and_nothing_is_read_until_one_is_chosen(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kpi/settings", "body": reports["settings"]}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and "MCC" in items[1]["badge"] and len(items) == 4 and out["panels"] == [{"agent": "kpi", "label": "KPI Monitoring"}]
    assert any(c.endswith("/kpi/settings") for c in out["calls"]) and not any("/kpi/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports, "one")
    assert [c for c in out["calls"] if "/kpi/report" in c] == ["/p2/paid-marketing/api/kpi/report?account=1234567890"]
    assert out["store"]["kp_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/kpi-monitoring?account=1234567890" and out["el"]["#out"]["hidden"] is False


def test_the_report_is_drawn_worst_first_with_the_figures_the_evidence_and_markup_escaped(reports):
    out = opened(reports, "one")
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Campaigns read", "To fix", "Off target", "Not judged", "Accounts to fix"):
        assert text in kp, text
    assert 'class="bi-row stopped open"' in lst and "bi-pill bad" in lst and "Needs a fix" in lst and "The account as one" in lst and "What was found" in lst and "Fix plan" in lst and "Check again the next day" in lst
    assert lst.index("Cost per conversion above target") < lst.index("Still learning")                                           # the table of what was found, worst first
    assert "Search &lt;b&gt;Core&lt;/b&gt;" in lst and "<b>Core</b>" not in lst and "Search <b>" not in lst
    assert "Last 7 days" in lst and "The 7 days before" in lst and "saved for the account" in lst and "$100" in lst and "$40.00" in lst and "Off target" in lst
    assert "Last 7 days: $100 (target $40.00, saved for the account)" in lst and "The 7 days before were off target too." in lst
    assert "2 campaigns with nothing wrong" in lst and '<details class="gc-quiet">' in lst and "only 1.4 conversions in the week" in lst
    assert "Left out of the checks" in lst and "Old Test" in lst and "a test campaign" in lst and 'class="cc-ok kp-unmute"' in lst
    assert "read " in out["el"]["#fresh"]["html"] and "Acme Dental" in out["el"]["#fresh"]["html"] and "the ones to fix first" in out["el"]["#listNote"]["text"]
    assert "Across the campaigns" in out["el"]["#insights"]["html"] and "left out of the checks by the team" in out["el"]["#insights"]["html"]
    assert "5 conversions for a cost or a return" in out["el"]["#foot"]["html"] and "after 14 days" in out["el"]["#foot"]["html"]


def test_the_forms_hold_what_is_saved(reports):
    lst = opened(reports, "one")["el"]["#list"]["html"]
    assert 'data-k="cpa" inputmode="decimal" value="40"' in lst and 'data-k="tolerance" inputmode="decimal" value="20"' in lst and 'data-kind="account"' in lst
    assert 'data-campaign="6" data-name="Brand"' in lst and 'data-k="cpa" inputmode="decimal" value="80"' in lst and 'data-kind="campaign"' in lst and 'data-kind="mute"' in lst
    empty = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kpi/report", "body": reports["notargets"]}, {"match": "/kpi/settings", "body": {"accounts": {}, "changes": []}}], "steps": [{"pick": "1234567890"}]})
    html = empty["el"]["#list"]["html"]
    assert 'data-k="cpa" inputmode="decimal" value=""' in html and "Save the targets of this account" in html and "no target saved" in html and "No target is saved here" in empty["el"]["#insights"]["html"]


def test_a_clean_account_says_so_and_a_group_opens_to_the_accounts_that_need_a_look(reports):
    out = opened(reports, "clean")
    assert "On track" in out["el"]["#list"]["html"] and "nothing wrong in 1 campaign" in out["el"]["#list"]["html"] and "Nothing to flag" in out["el"]["#list"]["html"]
    mcc = opened(reports, "mcc", [{"pick": GROUP}])
    lst = mcc["el"]["#list"]["html"]
    assert "Acme Dental" in lst and "Beta Clinic" not in lst and 'class="bi-row stopped"' in lst
    assert "Needs a look <span class=\"n\">1</span>" in mcc["el"]["#views"]["html"] and "MCC · 2 accounts checked together" in mcc["el"]["#fresh"]["html"]
    allv = opened(reports, "mcc", [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])["el"]["#list"]["html"]
    assert "Beta Clinic" in allv and "bi-pill ok" in allv and allv.index("Acme Dental") < allv.index("Beta Clinic")


def test_a_row_opens_and_closes_when_its_header_is_pressed(reports):
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}]
    out = opened(reports, "mcc", steps)
    assert 'class="bi-row stopped open"' in out["el"]["#list"]["html"]
    closed = opened(reports, "mcc", steps + steps[1:])
    assert 'class="bi-row stopped"' in closed["el"]["#list"]["html"] and "What was found" not in closed["el"]["#list"]["html"]


def test_a_meta_account_is_drawn_with_its_campaigns(reports):
    out = page({"responses": reply(reports, "meta"), "steps": [{"pick": "1234567890"}]})
    lst = out["el"]["#list"]["html"]
    assert 'class="bi-mk">Meta</span>' in lst and "Meta leads" in lst and "Leads" in lst and "€100" in lst and "Cost per conversion above target" in lst


def test_a_campaigns_targets_are_saved_for_it_and_the_report_is_read_again(reports):
    fields = [{"k": "cpa", "value": "20"}, {"k": "roas", "value": ""}, {"k": "cvr", "value": ""}, {"k": "ctr", "value": "8"}]
    out = opened(reports, "one", [{"submit": ["#list", {".kp-set,.kp-camp-set,.kp-mute": {"dataset": {"kind": "campaign", "account": "1234567890", "campaign": "5", "name": "Search Core"}, "areas": fields}}]}],
                 responses=reply(reports, "one", [{"match": "/kpi/settings/campaign", "method": "POST", "body": {"account": {"targets": {"cpa": 40}, "campaigns": {"5": {"cpa": 20, "ctr": 8}}, "muted": {}}}}]))
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/kpi/settings/campaign", "body": {"account": "1234567890", "campaign": "5", "name": "Search Core", "cpa": "20", "roas": "", "cvr": "", "ctr": "8"}}]
    assert out["outs"][0]["[data-out]"] == "Saved. Checking again…" and len([c for c in out["calls"] if "/kpi/report" in c]) == 2


def test_the_targets_of_an_account_are_saved_with_the_tolerance(reports):
    fields = [{"k": "cpa", "value": "45"}, {"k": "roas", "value": "4"}, {"k": "cvr", "value": "3"}, {"k": "ctr", "value": ""}, {"k": "tolerance", "value": "25"}]
    out = opened(reports, "one", [{"submit": ["#list", {".kp-set,.kp-camp-set,.kp-mute": {"dataset": {"kind": "account", "account": "1234567890", "name": "Acme Dental"}, "areas": fields}}]}],
                 responses=reply(reports, "one", [{"match": "/kpi/settings/account", "method": "POST", "body": {"account": {"targets": {"cpa": 45}, "campaigns": {}, "muted": {}}}}]))
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/kpi/settings/account", "body": {"account": "1234567890", "name": "Acme Dental", "targets": {"cpa": "45", "roas": "4", "cvr": "3", "ctr": ""}, "tolerance": "25"}}]
    assert out["outs"][0]["[data-out]"] == "Saved. Checking again…" and len([c for c in out["calls"] if "/kpi/report" in c]) == 2
    assert 'data-k="cpa" inputmode="decimal" value="45"' in out["el"]["#list"]["html"]                           # the form shows what was just saved


def test_a_campaign_is_left_out_with_a_reason_and_put_back(reports):
    out = opened(reports, "one", [{"submit": ["#list", {".kp-set,.kp-camp-set,.kp-mute": {"dataset": {"kind": "mute", "account": "1234567890", "campaign": "7", "name": "Learner"}, "areas": [{"k": "why", "value": "being replaced"}]}}]}],
                 responses=reply(reports, "one", [{"match": "/kpi/settings/mute", "method": "POST", "body": {"account": {"muted": {"7": "being replaced"}}}}]))
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/kpi/settings/mute", "body": {"account": "1234567890", "campaign": "7", "name": "Learner", "why": "being replaced"}}]
    assert len([c for c in out["calls"] if "/kpi/report" in c]) == 2
    back = opened(reports, "one", [{"fire": ["#list", {".kp-unmute": {"dataset": {"account": "1234567890", "campaign": "9"}}}]}],
                  responses=reply(reports, "one", [{"match": "/kpi/settings/unmute", "method": "POST", "body": {"account": {"muted": {}}}}]))
    assert back["posts"] == [{"url": "/p2/paid-marketing/api/kpi/settings/unmute", "body": {"account": "1234567890", "campaign": "9"}}] and len([c for c in back["calls"] if "/kpi/report" in c]) == 2


def test_a_save_the_server_refuses_says_why_and_nothing_is_read_again(reports):
    out = opened(reports, "one", [{"submit": ["#list", {".kp-set,.kp-camp-set,.kp-mute": {"dataset": {"kind": "account", "account": "1234567890", "name": "A"}, "areas": [{"k": "cpa", "value": "abc"}]}}]}],
                 responses=reply(reports, "one", [{"match": "/kpi/settings/account", "method": "POST", "status": 400, "body": {"error": "The cost per conversion must be a number."}}]))
    assert out["outs"][0]["[data-out]"] == "The cost per conversion must be a number." and len([c for c in out["calls"] if "/kpi/report" in c]) == 1
    mute = opened(reports, "one", [{"submit": ["#list", {".kp-set,.kp-camp-set,.kp-mute": {"dataset": {"kind": "mute", "account": "1234567890", "campaign": "7", "name": "L"}, "areas": [{"k": "why", "value": ""}]}}]}],
                  responses=reply(reports, "one", [{"match": "/kpi/settings/mute", "method": "POST", "status": 400, "body": {"error": "Say why the campaign is left out, so the next person knows."}}]))
    assert mute["outs"][0]["[data-out]"].startswith("Say why") and len([c for c in mute["calls"] if "/kpi/report" in c]) == 1
    unreachable = opened(reports, "one", [{"submit": ["#list", {".kp-set,.kp-camp-set,.kp-mute": {"dataset": {"kind": "account", "account": "1234567890", "name": "A"}, "areas": []}}]}], reject="/kpi/settings/account")
    assert unreachable["outs"][0]["[data-out]"] == "The server could not be reached. Try again."


def test_putting_a_campaign_back_that_the_server_refuses_says_why(reports):
    out = opened(reports, "one", [{"fire": ["#list", {".kp-unmute": {"dataset": {"account": "1234567890", "campaign": "9"}}}]}],
                 responses=reply(reports, "one", [{"match": "/kpi/settings/unmute", "method": "POST", "status": 400, "body": {"error": "The campaign id must be a number."}}]))
    assert "The campaign id must be a number." in out["el"]["#msg"]["html"] and len([c for c in out["calls"] if "/kpi/report" in c]) == 1


def campaigns_report(reports, n, quiet=0):
    rep = json.loads(json.dumps(reports["one"]))
    a = rep["accounts"][0]
    flagged = [i for i in a["items"] if i["findings"]][0]
    calm = [i for i in a["items"] if not i["findings"] and not i["muted"]][0]
    a["items"] = [dict(flagged, id=str(i), name="Camp %02d" % i) for i in range(n)] + [dict(calm, id="q%d" % i, name="Calm %02d" % i) for i in range(quiet)]
    return rep


def test_more_campaigns_are_shown_by_the_button_and_the_rest_are_said_to_be_in_the_csv(reports):
    rep = {"one": campaigns_report(reports, 23), "settings": reports["settings"]}
    out = opened(rep, "one")
    lst = out["el"]["#list"]["html"]
    assert "show 3 more" in lst and "Camp 19" in lst and "Camp 20" not in lst and "The first 20 of 23" in lst
    more = opened(rep, "one", [{"fire": ["#list", {".cc-more": {"dataset": {"account": "1234567890"}}}]}])["el"]["#list"]["html"]
    assert "Camp 22" in more and "show" not in more.split("Camp 22")[1].split("</ul>")[1]


def test_a_long_list_of_campaigns_is_shown_twenty_at_a_time(reports):
    rep = {"one": campaigns_report(reports, 45), "settings": reports["settings"]}
    more = opened(rep, "one", [{"fire": ["#list", {".cc-more": {"dataset": {"account": "1234567890"}}}]}])["el"]["#list"]["html"]
    assert "Camp 39" in more and "Camp 40" not in more and "The first 40 of 45" in more and "show 5 more" in more


def test_the_quiet_campaigns_are_shown_twenty_at_a_time_too(reports):
    rep = {"one": campaigns_report(reports, 1, quiet=25), "settings": reports["settings"]}
    lst = opened(rep, "one")["el"]["#list"]["html"]
    assert "25 campaigns with nothing wrong" in lst and "Calm 19" in lst and "Calm 20" not in lst and "The first 20 of 25" in lst and "show 5 more" in lst
    more = opened(rep, "one", [{"fire": ["#list", {".cc-more-q": {"dataset": {"account": "1234567890"}}}]}])["el"]["#list"]["html"]
    assert "Calm 24" in more and "The first" not in more.split("Calm 24")[1]


def test_meta_ad_accounts_are_offered_beside_the_google_ones(reports):
    meta = {"match": "/gads/accounts", "body": {"accounts": [{"id": "act_1234567", "name": "Beta Meta", "platform": "meta"}, {"id": "1", "name": "not meta", "platform": "google"}]}}
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, meta, {"match": "/kpi/settings", "body": reports["settings"]}]})
    items = out["sets"][-1]["items"]
    assert [i for i in items if i.get("group") == "Meta ad accounts"] == [{"value": "act_1234567", "label": "Beta Meta", "avatar": "BM", "hue": items[-1]["hue"], "group": "Meta ad accounts", "badge": '<span class="badge pm-pill">Meta</span>', "sub": "act_1234567"}]


def test_what_was_shown_of_the_quiet_campaigns_starts_again_with_another_account(reports):
    rep = {"one": campaigns_report(reports, 1, quiet=25), "settings": reports["settings"]}
    steps = [{"fire": ["#list", {".cc-more-q": {"dataset": {"account": "1234567890"}}}]}, {"pick": "1234567890"}]
    again = opened(rep, "one", steps)["el"]["#list"]["html"]
    assert "Calm 19" in again and "Calm 20" not in again and "The first 20 of 25" in again


def test_a_failed_read_says_why_and_a_cooldown_is_a_note(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kpi/report", "status": 429, "body": {"error": "The platform limits how fast this app can read.", "kind": "quota"}}, {"match": "/kpi/settings", "body": reports["settings"]}],
                "steps": [{"pick": "1234567890"}]})
    assert "limits how fast" in out["el"]["#msg"]["html"] and "this clears on its own" in out["el"]["#msg"]["html"] and out["el"]["#empty"]["hidden"] is False
    cool = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kpi/report", "status": 429, "body": {"error": "Refreshed a moment ago.", "kind": "cooldown"}}, {"match": "/kpi/settings", "body": reports["settings"]}],
                 "steps": [{"pick": "1234567890"}]})
    assert 'class="pm-note"' in cool["el"]["#msg"]["html"] and "Refreshed a moment ago." in cool["el"]["#msg"]["html"]
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kpi/settings", "body": reports["settings"]}], "steps": [{"pick": "1234567890"}], "reject": "/kpi/report"})
    assert "server could not be reached" in down["el"]["#msg"]["html"]


def test_refresh_reads_again_asking_for_a_fresh_read_and_the_csv_downloads(reports):
    out = opened(reports, "one", [{"click": "#refreshBtn"}, {"click": "#exportBtn"}])
    assert [c for c in out["calls"] if "/kpi/report" in c] == ["/p2/paid-marketing/api/kpi/report?account=1234567890", "/p2/paid-marketing/api/kpi/report?account=1234567890&refresh=1"]
    assert out["downloads"][0]["name"].startswith("kpis-Acme-Dental-") and out["downloads"][0]["name"].endswith(".csv")
    assert out["blob"].startswith("﻿KPI Monitoring,Acme Dental") and "Cost per conversion above target" in out["blob"] and "Last 7 days: $100 (target $40.00" in out["blob"]


def test_a_cell_that_starts_like_a_formula_is_quoted_in_the_csv(reports):
    rep = json.loads(json.dumps(reports["one"]))
    rep["accounts"][0]["items"][0]["findings"][0]["detail"] = "=HYPERLINK(\"x\")"
    out = opened({"one": rep, "settings": reports["settings"]}, "one", [{"click": "#exportBtn"}])
    assert "\"'=HYPERLINK(\"\"x\"\")\"" in out["blob"]


def test_pressing_a_button_keeps_the_open_rows_open(reports):
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}, {"fire": ["#list", {".kp-unmute": {"dataset": {"account": "1234567890", "campaign": "9"}}}]}]
    out = opened(reports, "mcc", steps, responses=reply(reports, "mcc", [{"match": "/kpi/settings/unmute", "method": "POST", "body": {"account": {"muted": {}}}}]))
    assert len([c for c in out["calls"] if "/kpi/report" in c]) == 3 and 'class="bi-row stopped open"' in out["el"]["#list"]["html"]


def test_a_read_that_arrives_after_a_newer_one_is_ignored(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kpi/report?account=1234567890", "hold": True, "body": reports["one"]},
                              {"match": "/kpi/report?account=mcc-", "body": reports["mcc"]}, {"match": "/kpi/settings", "body": reports["settings"]}],
                "steps": [{"pick": "1234567890"}, {"pick": GROUP}, {"release": True}]})
    assert "MCC · 2 accounts checked together" in out["el"]["#fresh"]["html"] and "Beta Clinic" not in out["el"]["#list"]["html"] and "1 account, the ones" not in out["el"]["#listNote"]["text"]


def test_the_list_note_counts_accounts_and_a_saved_copy_is_said(reports):
    one = opened(reports, "one")
    assert one["el"]["#listNote"]["text"] == "1 account, the ones to fix first" and "copy saved" not in one["el"]["#fresh"]["html"]
    mcc = opened(reports, "mcc", [{"pick": GROUP}])
    assert mcc["el"]["#listNote"]["text"] == "2 accounts, the ones to fix first"
    stale = json.loads(json.dumps(reports["one"]))
    stale["stale"] = "2026-10-21T09:00:00+00:00"
    assert "The platform is not answering, so this is the copy saved" in opened({"one": stale, "settings": reports["settings"]}, "one")["el"]["#fresh"]["html"]
