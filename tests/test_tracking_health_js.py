"""Tracking & Data Health: static/js/tracking_health_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads each account and works out what is broken (tests/test_paid_track*.py). The page only draws, so how a figure, a status and a level read, which accounts a view shows and the CSV
come from tracking_health_data.js (executed here, not read), and tracking_health.js is run for real: it loads the accounts, draws the report the server built, refreshes and exports, and says what
went wrong in words. The HTML it writes is read back.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests import test_paid_track as TT  # noqa: E402
from tests.test_copycheck_js import HARNESS  # noqa: E402
from tracker import paid_track as t  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "tracking_health_data.js"), os.path.join(JS, "tracking_health.js")
TEMPLATE = os.path.join(ROOT, "templates", "tracking_health.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [dict(TT.ACCT, name="Acme <b>Dental</b>"), TT.ACCT2]


def run(expr, **data):
    pre = "".join("const %s=%s;" % (key, json.dumps(v)) for key, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def build(accounts, days, **kw):
    """The report the server builds for `accounts` (a list of {id, name, currency}) whose days are `days` ({id: rows})."""
    search = TT.searcher(days=days, **kw)
    raw = t.google_raw({"id": "mcc", "name": "Group", "currency": "USD", "members": accounts}, search_fn=search, now=TT.AT) if len(accounts) > 1 else \
        t.google_raw(accounts[0], search_fn=search, now=TT.AT)
    return t.assemble(raw, {"id": "mcc-9923720096-USD" if len(accounts) > 1 else accounts[0]["id"], "name": "Group" if len(accounts) > 1 else accounts[0]["name"], "currency": "USD",
                            "members": accounts if len(accounts) > 1 else None}, now=TT.AT)


@pytest.fixture(scope="module")
def reports():
    A, B = ACCOUNTS
    one = build([A], {A["id"]: TT.dark()[A["id"]]}, terms=False)
    one["readAt"] = "2026-10-22T15:00:00+00:00"
    clean = build([B], {B["id"]: TT.day_rows()}, actions={B["id"]: []}, action_days={B["id"]: []}, lists={B["id"]: []})
    mcc = build([A, B], {A["id"]: TT.dark()[A["id"]], B["id"]: TT.day_rows()}, actions={}, action_days={}, lists={})
    quiet = build([B], {B["id"]: TT.day_rows(cost=0.0, conv=0.0)}, actions={B["id"]: []}, action_days={B["id"]: []}, lists={B["id"]: []})
    meta = t.assemble(t.meta_raw("act_1234567", pages_fn=TT.meta_pages(pixels=[{"id": "1", "name": "Site", "last_fired_time": "2026-10-10T10:00:00+0000"}]),
                                 account_fn=lambda act: {"name": "Meta Co", "currency": "GBP"}, now=TT.AT), {"id": "act_1234567", "name": "Meta Co", "currency": "GBP", "platform": "meta"}, now=TT.AT)
    small = build([B], {B["id"]: TT.day_rows()}, terms=False, actions={B["id"]: []}, action_days={B["id"]: []}, lists={B["id"]: []})                         # only a low finding
    medium = build([B], {B["id"]: TT.day_rows()}, terms=False, actions={B["id"]: []}, action_days={B["id"]: []}, lists={B["id"]: [TT.list_row("7", kind="CRM_BASED")]})        # a medium one
    return {"one": one, "clean": clean, "mcc": mcc, "quiet": quiet, "meta": meta, "small": small, "medium": medium}


# ── the wording and the shapes ──────────────────────────────────────────────

def test_a_status_a_level_and_a_check_read_as_words_and_an_unknown_one_is_shown_as_it_came():
    assert run("X.statusInfo('fix')") == {"label": "Needs a fix", "tone": "bad"} and run("X.statusInfo('clean')") == {"label": "Tracking is fine", "tone": "ok"}
    assert run("X.statusInfo('odd')") == {"label": "odd", "tone": "unk"} and run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('zzz')") == {"label": "zzz", "tone": "unk"}
    assert run("X.checkInfo('unknown')") == {"label": "Not judged", "tone": "unk"} and run("X.checkInfo('problem')")["tone"] == "bad" and run("X.checkInfo('ok')")["label"] == "Fine"


def test_money_and_counts_are_written_the_way_the_server_writes_them():
    assert run("X.money(1234.5, 'USD')") == "$1,235" and run("X.money(12.5, 'EUR')") == "€12.50" and run("X.money(50, 'XYZ')") == "50.00 XYZ" and run("X.money(null, 'USD')") == "n/a"
    assert run("X.plural(1, 'check')") == "1 check" and run("X.plural(2, 'check')") == "2 checks" and run("X.plural(3, 'list', 'lists')") == "3 lists" and run("X.int(null)") == "n/a"


def test_the_tiles_say_what_was_read_what_is_dark_and_what_could_not_be_judged(reports):
    tiles = run("X.tiles(R)", R=reports["one"])
    assert [x[0] for x in tiles] == ["Accounts read", "Conversions stopped", "To fix", "Accounts to fix", "Checks not judged"]
    assert tiles[0][1:3] == ["1", "0 fine, 1 to fix"] and tiles[1][1:] == ["1", "spending, and nothing recorded", "warn"] and tiles[2][1:] == ["1", "1 high, 0 medium", "warn"]
    good = run("X.tiles(R)", R=reports["clean"])
    assert good[1][1:] == ["0", "no account is spending with nothing recorded", "good"] and good[2][1:] == ["0", "no tracking problem was found", "good"] and good[3][3] == "good"
    assert run("X.unjudged(R)", R=reports["clean"]) == int(good[4][1]) and good[4][2] == "too little data, or the platform did not show it"


def test_a_view_shows_the_accounts_that_need_a_look_and_opens_on_all_when_none_does(reports):
    assert run("X.viewCount(R, 'attention')", R=reports["mcc"]) == 1 and run("X.viewCount(R, 'all')", R=reports["mcc"]) == 2
    assert [a["name"] for a in run("X.shown(R, 'attention')", R=reports["mcc"])] == ["Acme <b>Dental</b>"] and run("X.firstView(R)", R=reports["mcc"]) == "attention"
    assert run("X.firstView(R)", R=reports["clean"]) == "all" and run("X.shown(R, 'attention')", R=reports["clean"]) == []
    assert run("X.query('1234567890')") == "account=1234567890" and run("X.query('1234567890+act_1', true)") == "account=1234567890%2Bact_1&refresh=1"


def test_an_account_reads_in_one_line_with_what_failed_and_what_was_not_judged(reports):
    assert run("X.accountSub(A)", A=reports["one"]["accounts"][0]) == "Google Ads, 2 checks failing, 1 not judged"
    assert run("X.accountSub(A)", A=reports["meta"]["accounts"][0]).startswith("Meta Ads, 1 check failing")
    assert "every check that could be run passed" in run("X.accountSub(A)", A=reports["clean"]["accounts"][0])


def test_what_shows_only_across_the_accounts_is_said_in_sentences(reports):
    one = run("X.insights(R)", R=reports["clean"])
    assert {i["kind"] for i in one} == {"unjudged"} and any(i["text"].startswith("Each conversion action records: No conversion action records enough") for i in one)
    group = run("X.insights(R)", R=reports["mcc"])
    assert any(i["text"].endswith("could not be judged (open an account to see why).") for i in group)
    idle = run("X.insights(R)", R=reports["quiet"])
    assert any(i["kind"] == "idle" and "Nothing was spent in the last 7 days" in i["text"] for i in idle)


def test_the_csv_has_one_line_per_finding_and_one_for_an_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    assert rows[0] == ["Tracking & Data Health", "Group"] and rows[3] == run("X.HEAD") and len(rows[3]) == 8
    body = {r[0]: r for r in rows[4:]}
    assert body["Acme <b>Dental</b>"][2] == "High" and body["Acme <b>Dental</b>"][3] == "Conversions" and body["Acme <b>Dental</b>"][4].startswith("No conversions in the last 3 days") and "test conversion" in body["Acme <b>Dental</b>"][7]
    assert body["Beta Clinic"] == ["Beta Clinic", "Google Ads", "", "", "(nothing found)", "", "", ""]
    assert all(r[1] == "Meta" for r in run("X.csvRows(R)", R=reports["meta"])[4:])


# ── the page script ─────────────────────────────────────────────────────────

SCOPE = os.path.join(JS, "pm_scope.js")
SCRIPTS = [SCOPE, DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}
HARNESS_PAGE = (HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/tracking-health")
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
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/track/report", "body": reports[key]}], "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("pm_platform.js") < html.index("pm_scope.js") < html.index("monitor_panel.js") < html.index("tracking_health_data.js") < html.index("tracking_health.js")
    assert "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_the_accounts_are_loaded_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and "MCC" in items[1]["badge"] and out["panels"] == [{"agent": "track", "label": "Tracking & Data Health"}]
    assert not any("/track/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports, "one")
    assert [c for c in out["calls"] if "/track/report" in c] == ["/p2/paid-marketing/api/track/report?account=1234567890"]
    assert out["store"]["th_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/tracking-health?account=1234567890" and out["el"]["#out"]["hidden"] is False


def test_the_report_is_drawn_with_the_findings_the_checks_and_markup_escaped(reports):
    out = opened(reports, "one")
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Accounts read", "Conversions stopped", "To fix", "Accounts to fix", "Checks not judged"):
        assert text in kp, text
    assert 'class="bi-row stopped open"' in lst and "bi-pill bad" in lst and "Needs a fix" in lst and "What was found" in lst and "What was checked" in lst
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in lst and "<b>Dental</b>" not in lst and "Acme <b>" not in lst
    assert "No conversions in the last 3 days, usually about 3 a day" in lst and "What to do." in lst and "test conversion" in lst and 'class="bi-lv bad">High' in lst
    assert "Last 3 days: 0 conversions, $150 spent" in lst and "Conversions are being recorded" in lst and "Customer data terms accepted" in lst and "Not accepted." in lst
    assert "0 conversions</b> in the last 7 days" in lst.replace("<b>", "<b>") or "conversions</b> in the last 7 days (about" in lst
    assert "read " in out["el"]["#fresh"]["html"] and "the ones to fix first" in out["el"]["#listNote"]["text"]
    assert "What this page cannot see" in out["el"]["#blind"]["html"] and "Conversions API" in out["el"]["#blind"]["html"]
    foot = out["el"]["#foot"]["html"]
    assert "21 days before" in foot and "at least 5 conversions" in foot and "30 percent" in foot and "1,000 people" in foot and "14 days without an upload" in foot and "48 hours" in foot


def test_a_clean_account_says_so_and_a_group_opens_to_the_accounts_that_need_a_look(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/track/report", "body": reports["clean"]}], "steps": [{"pick": "2222222222"}]})
    lst = out["el"]["#list"]["html"]
    assert "Tracking is fine" in lst and "no problem found" in lst and "Nothing to flag: every check that could be run passed." in lst
    mcc = opened(reports, "mcc", [{"pick": GROUP}])
    html = mcc["el"]["#list"]["html"]
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in html and "Beta Clinic" not in html and "Needs a look" in mcc["el"]["#views"]["html"] and "MCC" in mcc["el"]["#fresh"]["html"]
    allv = opened(reports, "mcc", [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])
    assert "Beta Clinic" in allv["el"]["#list"]["html"] and "Acme &lt;b&gt;Dental&lt;/b&gt;" in allv["el"]["#list"]["html"]


def test_a_meta_account_shows_its_pixel_check_and_a_row_opens_and_closes(reports):
    out = page({"responses": [{"match": "/gads/accounts", "body": {"accounts": [{"id": "act_1234567", "name": "Meta Co", "currency": "GBP", "platform": "meta"}]}}, {"match": "/segments/accounts", "body": ACCTS},
                              {"match": "/track/report", "body": reports["meta"]}], "steps": [{"platform": "meta"}, {"pickMeta": "act_1234567"}]})
    lst = out["el"]["#list"]["html"]
    assert "Meta Co" in lst and 'class="bi-mk">Meta' in lst and "The pixel fires" in lst and "The pixel &quot;Site&quot; last fired" in lst
    closed = opened(reports, "one", [{"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}])
    assert "What was found" not in closed["el"]["#list"]["html"] and 'aria-expanded="false"' in closed["el"]["#list"]["html"]


def test_refresh_reads_again_with_the_refresh_flag_and_a_too_soon_refresh_says_when(reports):
    out = opened(reports, "one", [{"click": "#refreshBtn"}])
    asked = [c for c in out["calls"] if "/track/report" in c]
    assert asked == ["/p2/paid-marketing/api/track/report?account=1234567890", "/p2/paid-marketing/api/track/report?account=1234567890&refresh=1"]
    soon = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/track/report", "sequence": [reports["one"], {"error": "Refreshed a moment ago.", "kind": "cooldown", "retryIn": 90}], "statuses": [200, 429]}],
                 "steps": [{"pick": "1234567890"}, {"click": "#refreshBtn"}]})
    assert "Refreshed a moment ago." in soon["el"]["#msg"]["html"] and "pm-note" in soon["el"]["#msg"]["html"] and soon["el"]["#out"]["hidden"] is False


def test_a_report_that_could_not_be_built_is_said_in_words_for_each_kind_of_failure(reports):
    def failing(status, body):
        return page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/track/report", "status": status, "body": body}], "steps": [{"pick": "1234567890"}]})
    quota = failing(429, {"error": "The daily limit is used up.", "kind": "quota"})["el"]["#msg"]["html"]
    assert "The daily limit is used up." in quota and "clears on its own" in quota
    assert "That is not one of Position2" in failing(400, {"error": "That is not one of Position2's accounts."})["el"]["#msg"]["html"]
    assert "The report could not be built (HTTP 500)." in failing(500, {})["el"]["#msg"]["html"]
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "reject": "/track/report", "steps": [{"pick": "1234567890"}]})
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"] and down["el"]["#empty"]["hidden"] is False
    warned = dict(reports["one"], warnings=["Acme <b>Dental</b>: The audience lists could not be read."])
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/track/report", "body": warned}], "steps": [{"pick": "1234567890"}]})
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;: The audience lists could not be read." in out["el"]["#msg"]["html"]


def test_the_csv_is_downloaded_and_a_cell_that_starts_like_a_formula_is_defused(reports):
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["name"] = "=HYPERLINK(\"x\")"
    out = opened({"one": risky}, "one", [{"click": "#exportBtn"}])
    assert out["blob"].startswith("﻿Tracking & Data Health,Acme") and "'=HYPERLINK" in out["blob"] and "test conversion" in out["blob"] and out["downloads"][0]["name"].startswith("tracking-health-")


def test_an_account_with_only_a_low_finding_is_worth_a_look_and_a_medium_one_counts_as_to_fix(reports):
    small, medium = reports["small"], reports["medium"]
    assert small["accounts"][0]["status"] == "check" and run("X.viewCount(R, 'attention')", R=small) == 1 and run("X.firstView(R)", R=small) == "attention" and run("X.statusInfo('check')")["label"] == "Worth a look"
    tiles = run("X.tiles(R)", R=small)
    assert tiles[2][1:] == ["0", "no tracking problem was found", "good"] and tiles[3][1:3] == ["0", "1 worth a look only"]
    assert medium["accounts"][0]["counts"] == {"high": 0, "medium": 1, "low": 0} and run("X.tiles(R)", R=medium)[2][1:] == ["1", "0 high, 1 medium", "warn"]
    lst = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/track/report", "body": small}], "steps": [{"pick": "2222222222"}]})["el"]["#list"]["html"]
    assert "Worth a look" in lst and "bi-pill under" in lst and "small finding" in lst and "Customer data terms are not accepted" in lst


def test_a_check_that_failed_is_not_also_listed_as_one_that_could_not_be_judged(reports):
    texts = [i["text"] for i in run("X.insights(R)", R=reports["one"])]
    assert not any("Conversions are being recorded" in x or "Customer data terms accepted" in x for x in texts)
