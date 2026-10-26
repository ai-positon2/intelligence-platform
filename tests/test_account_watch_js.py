"""Account Watch: static/js/account_watch_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads each account and works out what changed (tests/test_paid_watch*.py). The page only draws, so how a status and a level read, which accounts a view shows and the CSV come from
account_watch_data.js (executed here, not read), and account_watch.js is run for real: it loads the accounts, draws the report the server built, refreshes and exports, and says what went wrong
in words. The HTML it writes is read back.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests import test_paid_watch as TW  # noqa: E402
from tests.test_copycheck_js import HARNESS  # noqa: E402
from tracker import paid_watch as w  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "account_watch_data.js"), os.path.join(JS, "account_watch.js")
TEMPLATE = os.path.join(ROOT, "templates", "account_watch.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [dict(TW.ACCT, name="Acme <b>Dental</b>"), TW.ACCT2]


def run(expr, **data):
    pre = "".join("const %s=%s;" % (key, json.dumps(v)) for key, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def build(accounts, **kw):
    search = TW.searcher(**kw)
    if len(accounts) > 1:
        group = {"id": GROUP, "name": "Group", "currency": "USD", "members": accounts}
        raw = w.google_raw(group, search_fn=search, now=TW.AT)
        return w.assemble(raw, group, now=TW.AT)
    raw = w.google_raw(accounts[0], search_fn=search, now=TW.AT)
    return w.assemble(raw, accounts[0], now=TW.AT)


@pytest.fixture(scope="module")
def reports():
    A, B = ACCOUNTS
    two = [TW.client_row(A["id"], A["name"], "SUSPENDED"), TW.client_row(B["id"], B["name"])]
    one = build([A], clients=two, changes=[TW.change_row(user="jim@client.com", at="2026-10-25 14:03:11")], orders=[TW.order_row(served="880000000")])
    one["readAt"] = "2026-10-26T07:30:00+00:00"
    clean = build([B], clients=two)
    mcc = build([A, B], clients=two + [TW.client_row("3333333333", "Gamma <i>Co</i>", "SUSPENDED")])
    medium = build([B], clients=two, changes=[TW.change_row(at="2026-10-25 14:03:11", old="50000000", new="80000000")])
    TG, pg, _ = TW.meta_world(status={"account_status": 3, "disable_reason": 0, "spend_cap": "100000", "amount_spent": "99000", "currency": "USD"})
    meta = w.assemble(w.meta_raw("act_1234567", pages_fn=pg, account_fn=lambda act: {"name": "Meta Co", "currency": "USD"}, get_fn=TG, now=TW.AT), {"id": "act_1234567", "name": "Meta Co", "currency": "USD", "platform": "meta"}, now=TW.AT)
    return {"one": one, "clean": clean, "mcc": mcc, "medium": medium, "meta": meta}


# ── the wording and the shapes ──────────────────────────────────────────────

def test_a_status_a_level_and_a_check_read_as_words_and_an_unknown_one_is_shown_as_it_came():
    assert run("X.statusInfo('fix')") == {"label": "Needs a fix", "tone": "bad"} and run("X.statusInfo('clean')") == {"label": "Nothing unusual", "tone": "ok"}
    assert run("X.statusInfo('odd')") == {"label": "odd", "tone": "unk"} and run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('zzz')") == {"label": "zzz", "tone": "unk"}
    assert run("X.checkInfo('unknown')") == {"label": "Not judged", "tone": "unk"} and run("X.checkInfo('problem')")["tone"] == "bad" and run("X.checkInfo('ok')")["label"] == "Fine"
    assert run("X.plural(1, 'check')") == "1 check" and run("X.plural(2, 'check')") == "2 checks" and run("X.int(null)") == "n/a"


def test_the_tiles_say_what_was_read_what_is_not_active_and_what_could_not_be_judged(reports):
    tiles = run("X.tiles(R)", R=reports["mcc"])
    assert [x[0] for x in tiles] == ["Accounts read", "Not active", "To fix", "Accounts to fix", "Checks not judged"]
    assert tiles[0][1:3] == ["3", "1 unremarkable, 2 to fix"] and tiles[1][1:] == ["2", "suspended, closed or gone from the manager account", "warn"] and tiles[2][1] == "2"
    good = run("X.tiles(R)", R=reports["clean"])
    assert good[1][1:] == ["0", "every account is active", "good"] and good[2][1:] == ["0", "nothing unusual was found", "good"] and good[3][3] == "good"
    assert run("X.unjudged(R)", R=reports["clean"]) == int(good[4][1]) and good[4][2] == "no earlier reading yet, or the platform did not show it"
    med = run("X.tiles(R)", R=reports["medium"])
    assert med[2][1:] == ["1", "0 high, 1 medium", "warn"]


def test_a_view_shows_the_accounts_that_need_a_look_and_opens_on_all_when_none_does(reports):
    assert run("X.viewCount(R, 'attention')", R=reports["mcc"]) == 2 and run("X.viewCount(R, 'all')", R=reports["mcc"]) == 3
    assert [a["name"] for a in run("X.shown(R, 'attention')", R=reports["mcc"])][:1] in (["Acme <b>Dental</b>"], ["Gamma <i>Co</i>"]) and run("X.firstView(R)", R=reports["mcc"]) == "attention"
    assert run("X.firstView(R)", R=reports["clean"]) == "all" and run("X.shown(R, 'attention')", R=reports["clean"]) == []
    assert run("X.query('1234567890')") == "account=1234567890" and run("X.query('1234567890+act_1', true)") == "account=1234567890%2Bact_1&refresh=1"


def test_an_account_reads_in_one_line_with_what_failed_and_what_was_not_judged(reports):
    assert run("X.accountSub(A)", A=reports["one"]["accounts"][0]) == "Google Ads, 4 checks failing, 2 not judged"
    assert run("X.accountSub(A)", A=reports["meta"]["accounts"][0]) == "Meta Ads, 1 check failing, 1 not judged"
    assert run("X.accountSub(A)", A=reports["clean"]["accounts"][0]) == "Google Ads, nothing unusual was found, 2 not judged"


def test_what_shows_only_across_the_accounts_is_said_in_sentences_and_a_not_active_account_adds_none(reports):
    one = run("X.insights(R)", R=reports["clean"])
    assert {i["kind"] for i in one} == {"unjudged"} and any(i["text"].startswith("Conversion goals are as they were: ") and "compares from tomorrow" in i["text"] for i in one)
    group = run("X.insights(R)", R=reports["mcc"])
    assert any(i["text"].endswith("could not be judged yet (open an account to see why).") for i in group) and not any("Gamma" in i["text"] for i in group)


def test_the_csv_has_one_line_per_finding_and_one_for_an_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    assert rows[0] == ["Account Watch", "Group"] and rows[3] == run("X.HEAD") and len(rows[3]) == 8
    body = {r[0]: r for r in rows[4:]}
    assert body["Acme <b>Dental</b>"][2] == "High" and body["Acme <b>Dental</b>"][3] == "Account" and body["Acme <b>Dental</b>"][4] == "The account is suspended" and "Open the account" in body["Acme <b>Dental</b>"][7]
    assert body["Beta Clinic"] == ["Beta Clinic", "Google Ads", "", "", "(nothing found)", "", "", ""] and body["Gamma <i>Co</i>"][4] == "The account is suspended"
    assert all(r[1] == "Meta" for r in run("X.csvRows(R)", R=reports["meta"])[4:])


# ── the page script ─────────────────────────────────────────────────────────

SCOPE = os.path.join(JS, "pm_scope.js")
SCRIPTS = [SCOPE, DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}
HARNESS_PAGE = (HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/account-watch")
                .replace("JSON.parse(process.argv[2])", "JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))"))


def page(scenario):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(scenario, f)
    try:
        out = subprocess.run(["node", "-e", HARNESS_PAGE, json.dumps(SCRIPTS), f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(reports, key, steps=None, **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/watch/report", "body": reports[key]}], "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("pm_platform.js") < html.index("pm_scope.js") < html.index("monitor_panel.js") < html.index("account_watch_data.js") < html.index("account_watch.js")
    assert "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_the_accounts_are_loaded_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and "MCC" in items[1]["badge"] and out["panels"] == [{"agent": "watch", "label": "Account Watch"}]
    assert not any("/watch/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports, "one")
    assert [c for c in out["calls"] if "/watch/report" in c] == ["/p2/paid-marketing/api/watch/report?account=1234567890"]
    assert out["store"]["aw_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/account-watch?account=1234567890" and out["el"]["#out"]["hidden"] is False


def test_the_report_is_drawn_with_the_findings_the_checks_and_markup_escaped(reports):
    out = opened(reports, "one")
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Accounts read", "Not active", "To fix", "Accounts to fix", "Checks not judged"):
        assert text in kp, text
    assert 'class="bi-row stopped open"' in lst and "bi-pill bad" in lst and "Needs a fix" in lst and "What was found" in lst and "What was checked" in lst
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in lst and "<b>Dental</b>" not in lst and "Acme <b>" not in lst
    assert "The account is suspended" in lst and "What to do." in lst and "Open the account in Google Ads or Meta Business Support" in lst and 'class="bi-lv bad">High' in lst
    assert "jim@client.com outside Position2 made 1 change" in lst and "Budget change:" in lst and "88 percent of the account budget order is used" in lst and "The account is active" in lst and "Suspended." in lst
    assert "headline" not in lst and "conversions</b> in the last 7 days" not in lst
    assert "read " in out["el"]["#fresh"]["html"] and "the ones to fix first" in out["el"]["#listNote"]["text"]
    assert "What this page cannot see" in out["el"]["#blind"]["html"] and "A card that was declined" in out["el"]["#blind"]["html"]
    foot = out["el"]["#foot"]["html"]
    assert "manager account's list of every client" in foot and "30 percent or more" in foot and "85 and 90 percent" in foot and "position2.com" in foot and "judged from the second day" in foot


def test_a_clean_account_says_so_and_a_group_shows_the_inactive_accounts_with_what_is_known_of_them(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/watch/report", "body": reports["clean"]}], "steps": [{"pick": "2222222222"}]})
    lst = out["el"]["#list"]["html"]
    assert "Nothing unusual" in lst and "nothing unusual" in lst and "Nothing to flag: nothing unusual was found." in lst
    mcc = opened(reports, "mcc", [{"pick": GROUP}])
    html = mcc["el"]["#list"]["html"]
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in html and "Gamma &lt;i&gt;Co&lt;/i&gt;" in html and "Beta Clinic" not in html and "Needs a look" in mcc["el"]["#views"]["html"] and "MCC" in mcc["el"]["#fresh"]["html"]
    assert "<i>Co</i>" not in html and "Only this account's status is known here" not in html                                  # closed rows say nothing yet
    gamma = opened(reports, "mcc", [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "3333333333"}}}]}])["el"]["#list"]["html"]
    assert "Only this account's status is known here" in gamma and "The account is suspended" in gamma and gamma.count("<td><b>") == 1                                # the one check it has: its status
    allv = opened(reports, "mcc", [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])
    assert "Beta Clinic" in allv["el"]["#list"]["html"]


def test_a_meta_account_shows_its_status_checks_and_a_row_opens_and_closes(reports):
    out = page({"responses": [{"match": "/gads/accounts", "body": {"accounts": [{"id": "act_1234567", "name": "Meta Co", "currency": "USD", "platform": "meta"}]}}, {"match": "/segments/accounts", "body": ACCTS},
                              {"match": "/watch/report", "body": reports["meta"]}], "steps": [{"platform": "meta"}, {"pickMeta": "act_1234567"}]})
    lst = out["el"]["#list"]["html"]
    assert "Meta Co" in lst and 'class="bi-mk">Meta' in lst and "The ad account is unsettled (a payment failed)" in lst and "The Pages are still connected" in lst and "99 percent of the spend cap is used" in lst
    closed = opened(reports, "one", [{"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}])
    assert "What was found" not in closed["el"]["#list"]["html"] and 'aria-expanded="false"' in closed["el"]["#list"]["html"]


def test_refresh_reads_again_with_the_refresh_flag_and_a_too_soon_refresh_says_when(reports):
    out = opened(reports, "one", [{"click": "#refreshBtn"}])
    asked = [c for c in out["calls"] if "/watch/report" in c]
    assert asked == ["/p2/paid-marketing/api/watch/report?account=1234567890", "/p2/paid-marketing/api/watch/report?account=1234567890&refresh=1"]
    soon = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/watch/report", "sequence": [reports["one"], {"error": "Refreshed a moment ago.", "kind": "cooldown", "retryIn": 90}], "statuses": [200, 429]}],
                 "steps": [{"pick": "1234567890"}, {"click": "#refreshBtn"}]})
    assert "Refreshed a moment ago." in soon["el"]["#msg"]["html"] and "pm-note" in soon["el"]["#msg"]["html"] and soon["el"]["#out"]["hidden"] is False


def test_a_report_that_could_not_be_built_is_said_in_words_for_each_kind_of_failure(reports):
    def failing(status, body):
        return page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/watch/report", "status": status, "body": body}], "steps": [{"pick": "1234567890"}]})
    quota = failing(429, {"error": "The daily limit is used up.", "kind": "quota"})["el"]["#msg"]["html"]
    assert "The daily limit is used up." in quota and "clears on its own" in quota
    assert "That is not one of Position2" in failing(400, {"error": "That is not one of Position2's accounts."})["el"]["#msg"]["html"]
    assert "The report could not be built (HTTP 500)." in failing(500, {})["el"]["#msg"]["html"]
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "reject": "/watch/report", "steps": [{"pick": "1234567890"}]})
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"] and down["el"]["#empty"]["hidden"] is False
    warned = dict(reports["one"], warnings=["Acme <b>Dental</b>: The change history could not be read."])
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/watch/report", "body": warned}], "steps": [{"pick": "1234567890"}]})
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;: The change history could not be read." in out["el"]["#msg"]["html"]


def test_the_csv_is_downloaded_and_a_cell_that_starts_like_a_formula_is_defused(reports):
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["name"] = "=HYPERLINK(\"x\")"
    out = opened({"one": risky}, "one", [{"click": "#exportBtn"}])
    assert out["blob"].startswith("﻿Account Watch,Acme") and "'=HYPERLINK" in out["blob"] and "The account is suspended" in out["blob"] and out["downloads"][0]["name"].startswith("account-watch-")


def test_a_check_that_failed_is_not_also_listed_as_one_that_could_not_be_judged(reports):
    texts = [i["text"] for i in run("X.insights(R)", R=reports["one"])]
    assert texts and not any("The account is active" in x or "Budgets and bids were not changed a lot" in x or "Only Position2 edited the account" in x for x in texts)
    assert not any("Gamma" in i["text"] for i in run("X.insights(R)", R=reports["mcc"]))
