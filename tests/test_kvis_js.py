"""Keyword Visibility Tracker: static/js/kvis_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads the highest-spend keywords and the auctions and works out what lost its place (tests/test_paid_kvis*.py). The page only draws, so how a share, a status and a level read, which
accounts a view shows, how a keyword's figures are laid out and the CSV come from kvis_data.js (executed here, not read), and kvis.js is run for real: it loads the accounts and what the team
saved, draws the report the server built, saves the settings and ignores a domain, and says what went wrong in words. The HTML it writes is read back.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_kvis as TK  # noqa: E402
from tests import test_paid_kvis_math as TM  # noqa: E402
from tests.test_copycheck_js import HARNESS  # noqa: E402
from tracker import paid_kvis as kv  # noqa: E402
from tracker import paid_kvis_settings as st  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "kvis_data.js"), os.path.join(JS, "kvis.js")
TEMPLATE = os.path.join(ROOT, "templates", "keyword_visibility.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [TK.ACCT, TK.ACCT2]


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def world():
    top = {TK.ACCT["id"]: [TK.top_row("9", "101", "dentist <b>quincy</b>", cost=400.0, qs=3), TK.top_row("9", "102", "implants dover", cost=300.0, qs=8), TK.top_row("9", "103", "thin one", cost=10.0, qs=7)],
           TK.ACCT2["id"]: [TK.top_row("9", "101", "braces", cost=200.0, qs=8)]}
    weeks = {TK.ACCT["id"]: [TK.week_row("9", "101", w, top=0.3 if w == TM.RECENT else 0.6) for w in TM.MONDAYS] + TK.all_weeks("9", "102", top=0.6)
             + [TK.week_row("9", "103", TM.RECENT, impressions=20)],
             TK.ACCT2["id"]: TK.all_weeks("9", "101", top=0.7)}
    qs = {TK.ACCT["id"]: [TK.qs_row("9", "101", TM.OLDEST, 6), TK.qs_row("9", "101", TM.RECENT, 3)]}
    auction = {TK.ACCT["id"]: [TK.auc_row(week=w, overlap=0.3 if w == TM.RECENT else 0.0) for w in TM.MONDAYS]}
    return top, weeks, qs, auction


@pytest.fixture(scope="module")
def reports():
    mp = pytest.MonkeyPatch()
    from tracker import paid_gads as base, paid_geo, paid_store
    saved = {}
    mp.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    mp.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    mp.setattr(paid_store, "get_gads_entry", lambda key, ttl: None, raising=False)
    group = {"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in ACCOUNTS]}
    top, weeks, qs, auction = world()
    mon = MemStore()
    st.save_account(mon, TK.ACCT["id"], "Acme Dental", {"topIs": "50", "topN": "25", "ignore": "sister.com"}, "t")

    def report(account, search, **kw):
        paid_geo.clear_cache()
        base.reset_state()
        base.forget("")
        return kv.report_for(account, accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [group], search_fn=search, today=TM.TODAY, at=TK.AT, **kw)
    out = {"one": report(TK.ACCT["id"], TK.searcher(top=top, weeks=weeks, qs=qs, auction=auction), mon=mon),
           "mcc": report(GROUP, TK.searcher(top=top, weeks=weeks, qs=qs, auction=auction), mon=mon),
           "clean": report(TK.ACCT2["id"], TK.searcher(top=top, weeks=weeks, qs=qs, auction=auction), mon=mon),
           "noauction": report(TK.ACCT["id"], TK.searcher(top=top, weeks=weeks, qs=qs, auction=auction, fail_kinds=("auction",)))}
    out["settings"] = st.view(mon)
    yield out
    mp.undo()


# ── the data helpers ────────────────────────────────────────────────────────

def test_a_status_and_a_level_read_in_words_and_an_unknown_one_reads_as_itself():
    assert run("X.statusInfo('fix')") == {"label": "Needs a fix", "tone": "bad"} and run("X.statusInfo('check')")["label"] == "Worth a look" and run("X.statusInfo('clean')") == {"label": "Holding", "tone": "ok"}
    assert run("X.statusInfo('zz')") == {"label": "zz", "tone": "unk"}
    assert run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('low')")["tone"] == "wait" and run("X.levelInfo('q')") == {"label": "q", "tone": "unk"}


def test_a_share_a_score_and_money_are_written_as_the_server_writes_them():
    assert [run("X.pct(%s)" % v) for v in ("0.3", "0.456", "0", "1")] == ["30%", "46%", "0%", "100%"] and run("X.pct(null)") == "n/a"
    assert [run("X.score(%s)" % v) for v in ("7", "3.5", "4.04")] == ["7", "3.5", "4"] and run("X.score(null)") == "n/a"
    assert [run("X.money(%s, 'USD')" % v) for v in ("12.5", "1234.5", "100")] == ["$12.50", "$1,235", "$100"] and run("X.money(40, 'CHF')") == "40.00 CHF" and run("X.money(null, 'USD')") == "n/a"


def test_the_tiles_say_what_to_fix_and_what_was_not_judged(reports):
    t = run("X.tiles(R)", R=reports["one"])
    assert [x[0] for x in t] == ["Keywords tracked", "To fix", "Not judged", "Competitors flagged", "Accounts to fix"]
    s = reports["one"]["summary"]
    assert t[0] == ["Keywords tracked", "3", "2 with enough searches to judge", ""] and t[1][1] == str(s["high"] + s["medium"]) and t[1][2] == "%d high, %d medium" % (s["high"], s["medium"]) and t[1][3] == "warn"
    assert t[2] == ["Not judged", "1", "too few searches last week", ""] and t[3] == ["Competitors flagged", "1", "new or overlapping more", "warn"] and t[4][1] == "1"
    c = run("X.tiles(R)", R=reports["clean"])
    assert c[1] == ["To fix", "0", "no keyword or competitor needs action", "good"] and c[2] == ["Not judged", "0", "every keyword had enough", ""] and c[3] == ["Competitors flagged", "0", "none new in the auctions", "good"]
    assert c[4] == ["Accounts to fix", "0", "of 1 account read", "good"]
    low = json.loads(json.dumps(reports["clean"]))
    low["summary"]["counts"] = {"fix": 0, "check": 2, "clean": 0}
    assert run("X.tiles(R)", R=low)[4][2] == "2 worth a look only"


def test_the_views_hold_the_accounts_that_need_a_look(reports):
    assert [a["name"] for a in run("X.shown(R, 'attention')", R=reports["mcc"])] == ["Acme Dental"] and len(run("X.shown(R, 'all')", R=reports["mcc"])) == 2
    assert run("X.firstView(R)", R=reports["mcc"]) == "attention" and run("X.firstView(R)", R=reports["clean"]) == "all" and run("X.viewCount(R, 'all')", R=reports["mcc"]) == 2
    assert run("X.needsLook({status: 'check'})") is True and run("X.needsLook({status: 'clean'})") is False
    assert run("X.query('1234567890', true)") == "account=1234567890&refresh=1" and run("X.query('a b', false)") == "account=a%20b"


def test_the_keywords_with_something_to_look_at_the_holding_ones_and_the_unjudged_are_told_apart(reports):
    a = reports["one"]["accounts"][0]
    assert [k["keyword"] for k in run("X.flagged(A)", A=a)] == ["dentist <b>quincy</b>"] and [k["keyword"] for k in run("X.quiet(A)", A=a)] == ["implants dover"]
    assert [k["keyword"] for k in run("X.notJudged(A)", A=a)] == ["thin one"]


def test_a_keywords_figures_sit_last_week_against_the_weeks_before(reports):
    k = [i for i in reports["one"]["accounts"][0]["items"] if i["keyword"].startswith("dentist")][0]
    assert run("X.figureRows(I)", I=k) == [{"label": "At the top of the page", "now": "30%", "before": "60%"}, {"label": "Lost to rank", "now": "10%", "before": "10%"},
                                           {"label": "Quality Score", "now": "3", "before": "6"}]
    assert run("X.matchText('EXACT')") == "exact" and run("X.matchText('')") == "" and run("X.matchText(null)") == ""


def test_only_a_competitor_finding_can_be_answered_with_ignore_this_domain():
    assert run("X.ignorable(F)", F={"rule": "comp_new", "facts": {"domain": "rival.com"}}) == "rival.com" and run("X.ignorable(F)", F={"rule": "brand_hijack", "facts": {"domain": "x.com"}}) == "x.com"
    assert run("X.ignorable(F)", F={"rule": "comp_up", "facts": {"domain": "y.com"}}) == "y.com"
    assert run("X.ignorable(F)", F={"rule": "top_is_low", "facts": {"domain": "x.com"}}) == "" and run("X.ignorable(F)", F={"rule": "comp_new", "facts": {}}) == "" and run("X.ignorable(F)", F={"rule": "comp_new"}) == ""


def test_what_shows_only_across_the_keywords_is_said_in_sentences(reports):
    ins = run("X.insights(R)", R=reports["one"])
    assert [i["kind"] for i in ins] == ["data"] and ins[0]["text"] == "1 keyword of 3 had too few searches last week to be judged (at least 100 eligible searches are needed)."
    assert run("X.insights(R)", R=reports["clean"]) == [{"kind": "auction", "text": "No auction insights came back, so competitors were not checked."}]
    none = run("X.insights(R)", R=reports["noauction"])
    assert [i["kind"] for i in none] == ["data", "auction"] and none[1]["text"] == "No auction insights came back, so competitors were not checked."
    assert run("X.insights(R)", R=reports["mcc"])[0]["text"].startswith("Acme Dental: ")
    assert run("X.insights(R)", R={"account": {"isGroup": False}, "accounts": [{"name": "A", "keywords": 0}]}) == [{"kind": "none", "text": "No Search keyword spent in the last 28 days, so there is nothing to track."}]
    cut = json.loads(json.dumps(reports["clean"]))
    cut["accounts"][0].update(read=40, tracked=25, auctionRead=True)
    exact = json.loads(json.dumps(cut))
    exact["accounts"][0].update(read=25, tracked=25)
    assert run("X.insights(R)", R=exact) == []                                                                         # every keyword read is tracked: nothing to say
    assert run("X.insights(R)", R=cut) == [{"kind": "tracked", "text": "The 25 highest-spend keywords of the 40 read are tracked; the number can be changed in the settings below."}]


def test_a_standing_limit_is_said_first_and_replaces_the_per_account_line_about_competitors(reports):
    limited = json.loads(json.dumps(reports["noauction"]))
    limited["limits"] = ["Auction insights are not available to this app: reason."]
    ins = run("X.insights(R)", R=limited)
    assert ins[0] == {"kind": "limit", "text": "Auction insights are not available to this app: reason."} and [i["kind"] for i in ins] == ["limit", "data"]
    assert run("X.insights(R)", R=reports["noauction"])[-1]["kind"] == "auction"


def test_the_csv_has_one_line_per_finding_competitors_too_and_one_per_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    assert rows[0] == ["Keyword Visibility Tracker", "Group"] and rows[3] == run("X.HEAD") and len(rows[3]) == 9
    body = rows[4:]
    found = sum(a["findingCount"] for a in reports["mcc"]["accounts"])
    assert found == 5 and len([r for r in body if r[1] != "(nothing found)"]) == found and [r for r in body if r[1] == "(nothing found)"][0][0] == "Beta Clinic"
    drop = [r for r in body if r[5] == "Top-of-page share fell"][0]
    assert drop[0] == "Acme Dental" and drop[1] == "dentist <b>quincy</b>" and drop[2] == "exact" and drop[3] == "Search Core" and drop[4] == "High" and " | " in drop[7] and drop[8].startswith("Look at what changed")
    comp = [r for r in body if r[5] == "A new competitor in the auctions"][0]
    assert comp[1] == "" and comp[3] == "Search Core" and comp[4] == "Medium" and comp[6] == "rival.com showed in 30% of your auctions last week and in almost none before."
    assert run("X.csvRows(R)[2]", R=reports["one"]) == [] and run("X.csvRows(R)[1]", R=reports["one"]) == ["Read", reports["one"]["readAt"]]


def test_what_the_team_saved_is_shown_as_the_form_holds_it():
    assert run("X.savedView(S)", S={"topIs": 35.5, "qsMin": 6, "topN": 40, "ignore": ["a.com", "b.org"]}) == {"topIs": "35.5", "qsMin": "6", "topN": "40", "ignore": "a.com, b.org"}
    assert run("X.savedView(null)") == {"topIs": "", "qsMin": "", "topN": "", "ignore": ""}


# ── the page script ─────────────────────────────────────────────────────────

SCRIPTS = [DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}
HARNESS_PAGE = (HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/keyword-visibility")
                .replace("JSON.parse(process.argv[2])", "JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))"))      # a long report is too big for one argument on Linux (128 KB)


def page(scenario):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(scenario, f)
    try:
        out = subprocess.run(["node", "-e", HARNESS_PAGE, json.dumps(SCRIPTS), f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def reply(reports, key="one", extra=()):
    return [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kvis/report", "body": reports[key]}, {"match": "/kvis/settings", "body": reports["settings"]}] + list(extra)


def opened(reports, key, steps=None, **kw):
    sc = {"responses": reply(reports, key), "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("monitor_panel.js") < html.index("kvis_data.js") < html.index("kvis.js")
    assert "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()
    assert "meta" not in js.lower() and "Meta" in html                              # Google only: the page does not ask for Meta accounts (the template says why there is none)


def test_the_accounts_and_what_the_team_saved_are_loaded_and_nothing_is_read_until_one_is_chosen(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kvis/settings", "body": reports["settings"]}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and "MCC" in items[1]["badge"] and len(items) == 4 and out["panels"] == [{"agent": "kvis", "label": "Keyword Visibility Tracker"}]
    assert any(c.endswith("/kvis/settings") for c in out["calls"]) and not any("/kvis/report" in c for c in out["calls"]) and not any("platform=meta" in c for c in out["calls"])
    assert out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports, "one")
    assert [c for c in out["calls"] if "/kvis/report" in c] == ["/p2/paid-marketing/api/kvis/report?account=1234567890"]
    assert out["store"]["kv_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/keyword-visibility?account=1234567890" and out["el"]["#out"]["hidden"] is False


def test_the_report_is_drawn_worst_first_with_the_figures_the_evidence_and_markup_escaped(reports):
    out = opened(reports, "one")
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Keywords tracked", "To fix", "Not judged", "Competitors flagged", "Accounts to fix"):
        assert text in kp, text
    assert 'class="bi-row stopped open"' in lst and "bi-pill bad" in lst and "Needs a fix" in lst and "What was found" in lst and "Fix plan" in lst and "Check again next week" in lst
    assert lst.index("A new competitor in the auctions") < lst.index("Top-of-page share fell") < lst.index("Low Quality Score")                           # the table of what was found, in the order of the rules
    assert "dentist &lt;b&gt;quincy&lt;/b&gt;" in lst and "<b>quincy</b>" not in lst and "dentist <b>" not in lst
    assert "At the top of the page" in lst and "Last week" in lst and "The 4 weeks before" in lst and "#1 by spend" in lst and "$400 in 28 days" in lst and "Quality Score" in lst
    assert "Last week: 30% at the top" in lst and "The four weeks before: 60%" in lst and "Competitors in the auctions" in lst and "rival.com is a new competitor" in lst
    assert 'Ignore rival.com (not a competitor)</button>' in lst and "Keywords holding their place (1)" in lst
    assert "implants dover" in lst and "Keywords not judged, too few searches (1)" in lst and "thin one" in lst and "only 25 searches it was eligible for last week" in lst
    assert "Competitors that overlap most (1)" in lst and "<td>rival.com</td><td class=\"r\">30%</td><td class=\"r\">0%</td><td class=\"r\">1</td>" in lst
    assert "read " in out["el"]["#fresh"]["html"] and "Acme Dental" in out["el"]["#fresh"]["html"] and "the ones to fix first" in out["el"]["#listNote"]["text"]
    assert "Across the keywords" in out["el"]["#insights"]["html"] and "too few searches last week" in out["el"]["#insights"]["html"]
    assert "100 searches it was eligible for" in out["el"]["#foot"]["html"] and "does not report a keyword's share lost to budget" in out["el"]["#foot"]["html"]


def test_the_form_holds_what_is_saved(reports):
    lst = opened(reports, "one")["el"]["#list"]["html"]
    assert 'data-k="topIs" inputmode="decimal" value="50"' in lst and 'data-k="topN" inputmode="decimal" value="25"' in lst and 'data-k="ignore" value="sister.com"' in lst and 'data-k="qsMin" inputmode="decimal" value=""' in lst
    empty = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kvis/report", "body": reports["one"]}, {"match": "/kvis/settings", "body": {"accounts": {}, "changes": []}}], "steps": [{"pick": "1234567890"}]})
    assert 'data-k="topIs" inputmode="decimal" value=""' in empty["el"]["#list"]["html"] and 'data-k="ignore" value=""' in empty["el"]["#list"]["html"]


def test_a_clean_account_says_so_and_a_group_opens_to_the_accounts_that_need_a_look(reports):
    out = opened(reports, "clean")
    assert "Holding" in out["el"]["#list"]["html"] and "nothing wrong in 1 keyword" in out["el"]["#list"]["html"] and "Nothing to flag" in out["el"]["#list"]["html"]
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


def test_an_account_with_no_auction_insights_says_so_in_the_row(reports):
    out = opened(reports, "noauction")
    assert "no auction insights" in out["el"]["#list"]["html"] and "competitors were not checked" in out["el"]["#insights"]["html"] and "auction insights could not be read" in out["el"]["#msg"]["html"]


def test_a_domain_is_ignored_for_the_account_and_the_report_is_read_again_without_the_skeleton(reports):
    steps = [{"fire": ["#list", {".kv-ign": {"dataset": {"account": "1234567890", "name": "Acme Dental", "domain": "rival.com"}}}]}]
    out = page({"responses": reply(reports, "one", [{"match": "/kvis/settings/ignore", "method": "POST", "body": {"account": {"topIs": 50, "ignore": ["sister.com", "rival.com"]}}}]),
                "steps": [{"pick": "1234567890"}] + steps})
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/kvis/settings/ignore", "body": {"account": "1234567890", "domains": "rival.com"}}]
    assert len([c for c in out["calls"] if "/kvis/report" in c]) == 2 and out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True
    assert 'data-k="ignore" value="sister.com, rival.com"' in out["el"]["#list"]["html"]


def test_a_domain_that_cannot_be_saved_says_why_and_a_blank_one_is_not_sent(reports):
    out = page({"responses": reply(reports, "one", [{"match": "/kvis/settings/ignore", "method": "POST", "status": 400, "body": {"error": "x is not a domain (like example.com)."}}]),
                "steps": [{"pick": "1234567890"}, {"fire": ["#list", {".kv-ign": {"dataset": {"account": "1234567890", "name": "A", "domain": "x"}}}]}]})
    assert "x is not a domain" in out["el"]["#msg"]["html"] and len([c for c in out["calls"] if "/kvis/report" in c]) == 1
    blank = opened(reports, "one", [{"fire": ["#list", {".kv-ign": {"dataset": {"account": "1234567890", "name": "A", "domain": ""}}}]}])
    assert blank["posts"] == []
    down = page({"responses": reply(reports, "one"), "steps": [{"pick": "1234567890"}, {"fire": ["#list", {".kv-ign": {"dataset": {"account": "1234567890", "name": "A", "domain": "x.com"}}}]}], "reject": "/kvis/settings/ignore"})
    assert "server could not be reached" in down["el"]["#msg"]["html"]


def test_saving_the_settings_of_an_account_sends_what_was_typed_and_reads_the_report_again(reports):
    fields = [{"k": "topIs", "value": "40"}, {"k": "qsMin", "value": "6"}, {"k": "topN", "value": "30"}, {"k": "ignore", "value": "a.com, b.org"}]
    out = page({"responses": reply(reports, "one", [{"match": "/kvis/settings/account", "method": "POST", "body": {"account": {"topIs": 40, "qsMin": 6, "topN": 30, "ignore": ["a.com", "b.org"]}}}]),
                "steps": [{"pick": "1234567890"}, {"submit": ["#list", {".kv-set": {"dataset": {"account": "1234567890", "name": "Acme Dental"}, "areas": fields}}]}]})
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/kvis/settings/account", "body": {"account": "1234567890", "name": "Acme Dental", "topIs": "40", "qsMin": "6", "topN": "30", "ignore": "a.com, b.org"}}]
    assert out["outs"][0]["[data-out]"] == "Saved. Checking again…" and len([c for c in out["calls"] if "/kvis/report" in c]) == 2
    assert 'data-k="topIs" inputmode="decimal" value="40"' in out["el"]["#list"]["html"]


def test_a_save_the_server_refuses_says_why_and_nothing_is_read_again(reports):
    out = page({"responses": reply(reports, "one", [{"match": "/kvis/settings/account", "method": "POST", "status": 400, "body": {"error": "The lowest Quality Score must be between 2 and 10."}}]),
                "steps": [{"pick": "1234567890"}, {"submit": ["#list", {".kv-set": {"dataset": {"account": "1234567890", "name": "A"}, "areas": [{"k": "qsMin", "value": "11"}]}}]}]})
    assert out["outs"][0]["[data-out]"] == "The lowest Quality Score must be between 2 and 10." and len([c for c in out["calls"] if "/kvis/report" in c]) == 1
    down = page({"responses": reply(reports, "one"), "steps": [{"pick": "1234567890"}, {"submit": ["#list", {".kv-set": {"dataset": {"account": "1234567890", "name": "A"}, "areas": []}}]}], "reject": "/kvis/settings/account"})
    assert down["outs"][0]["[data-out]"] == "The server could not be reached. Try again."


def many_keywords(reports, n):
    rep = json.loads(json.dumps(reports["one"]))
    a = rep["accounts"][0]
    flagged = [i for i in a["items"] if i["findings"]][0]
    a["items"] = [dict(flagged, id="9-%d" % i, keyword="Kw %02d" % i, rank=i + 1) for i in range(n)]
    return {"one": rep, "settings": reports["settings"]}


def test_more_keywords_are_shown_by_the_button_and_twenty_at_a_time(reports):
    rep = many_keywords(reports, 45)
    lst = opened(rep, "one")["el"]["#list"]["html"]
    assert "show 20 more" in lst and "Kw 19" in lst and "Kw 20" not in lst and "The first 20 of 45" in lst
    more = opened(rep, "one", [{"fire": ["#list", {".cc-more": {"dataset": {"account": "1234567890"}}}]}])["el"]["#list"]["html"]
    assert "Kw 39" in more and "Kw 40" not in more and "The first 40 of 45" in more and "show 5 more" in more
    short = opened(many_keywords(reports, 23), "one", [{"fire": ["#list", {".cc-more": {"dataset": {"account": "1234567890"}}}]}])["el"]["#list"]["html"]
    assert "Kw 22" in short and "The first" not in short.split("Kw 22")[1].split("</ul>")[1]


def test_what_was_shown_of_the_keywords_starts_again_with_another_account(reports):
    rep = many_keywords(reports, 45)
    again = opened(rep, "one", [{"fire": ["#list", {".cc-more": {"dataset": {"account": "1234567890"}}}]}, {"pick": "1234567890"}])["el"]["#list"]["html"]
    assert "Kw 19" in again and "Kw 20" not in again and "The first 20 of 45" in again


def test_the_holding_keywords_are_listed_twenty_and_the_rest_are_in_the_csv(reports):
    rep = json.loads(json.dumps(reports["one"]))
    a = rep["accounts"][0]
    calm = [i for i in a["items"] if not i["findings"] and i["judged"]][0]
    a["items"] = [dict(calm, id="9-%d" % i, keyword="Calm %02d" % i) for i in range(25)]
    lst = opened({"one": rep, "settings": reports["settings"]}, "one")["el"]["#list"]["html"]
    assert "Keywords holding their place (25)" in lst and "Calm 19" in lst and "Calm 20" not in lst and "The first 20 of 25. The CSV has all." in lst


def test_a_failed_read_says_why_and_a_cooldown_is_a_note(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kvis/report", "status": 429, "body": {"error": "The platform limits how fast this app can read.", "kind": "quota"}}, {"match": "/kvis/settings", "body": reports["settings"]}],
                "steps": [{"pick": "1234567890"}]})
    assert "limits how fast" in out["el"]["#msg"]["html"] and "this clears on its own" in out["el"]["#msg"]["html"] and out["el"]["#empty"]["hidden"] is False
    cool = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kvis/report", "status": 429, "body": {"error": "Refreshed a moment ago.", "kind": "cooldown"}}, {"match": "/kvis/settings", "body": reports["settings"]}],
                 "steps": [{"pick": "1234567890"}]})
    assert 'class="pm-note"' in cool["el"]["#msg"]["html"] and "Refreshed a moment ago." in cool["el"]["#msg"]["html"]
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kvis/settings", "body": reports["settings"]}], "steps": [{"pick": "1234567890"}], "reject": "/kvis/report"})
    assert "server could not be reached" in down["el"]["#msg"]["html"]


def test_refresh_reads_again_asking_for_a_fresh_read_and_the_csv_downloads(reports):
    out = opened(reports, "one", [{"click": "#refreshBtn"}, {"click": "#exportBtn"}])
    assert [c for c in out["calls"] if "/kvis/report" in c] == ["/p2/paid-marketing/api/kvis/report?account=1234567890", "/p2/paid-marketing/api/kvis/report?account=1234567890&refresh=1"]
    assert out["downloads"][0]["name"].startswith("keywords-Acme-Dental-") and out["downloads"][0]["name"].endswith(".csv")
    assert out["blob"].startswith("﻿Keyword Visibility Tracker,Acme Dental") and "Top-of-page share fell" in out["blob"] and "rival.com showed in 30% of your auctions" in out["blob"]


def test_a_cell_that_starts_like_a_formula_is_quoted_in_the_csv(reports):
    rep = json.loads(json.dumps(reports["one"]))
    rep["accounts"][0]["items"][0]["findings"][0]["detail"] = "=HYPERLINK(\"x\")"
    out = opened({"one": rep, "settings": reports["settings"]}, "one", [{"click": "#exportBtn"}])
    assert "\"'=HYPERLINK(\"\"x\"\")\"" in out["blob"]


def test_pressing_a_button_keeps_the_open_rows_open(reports):
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}, {"fire": ["#list", {".kv-ign": {"dataset": {"account": "1234567890", "name": "Acme Dental", "domain": "rival.com"}}}]}]
    out = page({"responses": reply(reports, "mcc", [{"match": "/kvis/settings/ignore", "method": "POST", "body": {"account": {"ignore": ["rival.com"]}}}]), "steps": [{"pick": "1234567890"}] + steps})
    assert len([c for c in out["calls"] if "/kvis/report" in c]) == 3 and 'class="bi-row stopped open"' in out["el"]["#list"]["html"]


def test_a_read_that_arrives_after_a_newer_one_is_ignored(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/kvis/report?account=1234567890", "hold": True, "body": reports["one"]},
                              {"match": "/kvis/report?account=mcc-", "body": reports["mcc"]}, {"match": "/kvis/settings", "body": reports["settings"]}],
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
