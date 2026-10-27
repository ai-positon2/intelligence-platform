"""Creative & Asset Health: static/js/creative_health_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads each account and judges its creatives (tests/test_paid_creative*.py). The page only draws, so how a status and a level read, which accounts a view shows and the CSV come from
creative_health_data.js (executed here, not read), and creative_health.js is run for real: it loads the accounts, draws the report the server built, refreshes and exports, and says what went
wrong in words. The HTML it writes is read back.
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

from tests import test_paid_creative as TC  # noqa: E402
from tests.test_copycheck_js import HARNESS  # noqa: E402
from tracker import paid_creative as c  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "creative_health_data.js"), os.path.join(JS, "creative_health.js")
TEMPLATE = os.path.join(ROOT, "templates", "creative_health.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [dict(TC.ACCT, name="Acme <b>Dental</b>"), TC.ACCT2]
GAP = {"key": "SITELINK", "scope": "Campaign", "label": "Sitelinks", "severity": "high", "have": 0, "target": 4, "add": 4, "message": "No sitelinks, so the ad is smaller than it could be."}


def run(expr, **data):
    pre = "".join("const %s=%s;" % (key, json.dumps(v)) for key, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def build(accounts, search, gaps=()):
    report = lambda m: {"campaigns": [{"id": "9", "name": "Search Crowns", "gaps": list(gaps.get(m["id"], []) if isinstance(gaps, dict) else gaps)}]}      # noqa: E731
    if len(accounts) > 1:
        group = {"id": GROUP, "name": "Group", "currency": "USD", "members": accounts}
        return c.assemble(c.google_raw(group, search_fn=search, report_fn=report, today=TC.TODAY), group, now=TC.AT)
    return c.assemble(c.google_raw(accounts[0], search_fn=search, report_fn=report, today=TC.TODAY), accounts[0], now=TC.AT)


@pytest.fixture(scope="module")
def reports():
    A, B = ACCOUNTS
    bad = TC.searcher(ads=TC.ad_weeks(base_ctr=0.05, recent_ctr=0.02), weak_ads=[TC.weak_ad_row()])
    fine = TC.searcher()

    def both(cid, q):
        return (bad if cid == A["id"] else fine)(cid, q)
    one = build([A], bad, [GAP])
    one["readAt"] = "2026-10-22T07:30:00+00:00"
    clean = build([B], fine)
    mcc = build([A, B], both, {A["id"]: [GAP]})
    rows = [TC.meta_row("a1", s, impr=5000, clicks=250 if i < 3 else 60) for i, s in enumerate(("2026-09-17", "2026-09-24", "2026-10-01", "2026-10-08", "2026-10-15"))]
    raw = TC.meta_raw_read()
    raw["insights"]["a1"]["rank"] = {"quality": "BELOW_AVERAGE_20", "engagement": "BELOW_AVERAGE_35", "conversion": None}
    meta = c.assemble(c.meta_raw("act_1234567", pages_fn=TC.meta_api(rows), account_fn=lambda act: {"name": "Meta Co", "currency": "USD"}, today=TC.TODAY, raw_fn=lambda: raw),
                      {"id": "act_1234567", "name": "Meta Co", "currency": "USD", "platform": "meta"}, now=TC.AT)
    return {"one": one, "clean": clean, "mcc": mcc, "meta": meta}


# ── the wording and the shapes ──────────────────────────────────────────────

def test_a_status_a_level_and_a_check_read_as_words_and_an_unknown_one_is_shown_as_it_came():
    assert run("X.statusInfo('fix')") == {"label": "Needs a fix", "tone": "bad"} and run("X.statusInfo('clean')") == {"label": "Nothing unusual", "tone": "ok"}
    assert run("X.statusInfo('odd')") == {"label": "odd", "tone": "unk"} and run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('zzz')") == {"label": "zzz", "tone": "unk"}
    assert run("X.checkInfo('unknown')") == {"label": "Not judged", "tone": "unk"} and run("X.checkInfo('problem')")["tone"] == "bad" and run("X.checkInfo('ok')")["label"] == "Fine"
    assert run("X.plural(1, 'creative')") == "1 creative" and run("X.plural(2, 'creative')") == "2 creatives" and run("X.int(null)") == "n/a"


def test_the_tiles_say_what_was_read_what_is_wearing_out_and_what_could_not_be_judged(reports):
    tiles = run("X.tiles(R)", R=reports["mcc"])
    assert [x[0] for x in tiles] == ["Creatives read", "Wearing out", "To fix", "Accounts to fix", "Not judged"]
    assert tiles[0][1:3] == ["2", "2 judged on their last weeks"] and tiles[1][1:] == ["1", "losing clicks, shown too often or getting dearer", "warn"] and tiles[2][1:] == ["3", "2 high, 1 medium", "warn"]
    good = run("X.tiles(R)", R=reports["clean"])
    assert good[1][1:] == ["0", "none found among those judged", "good"] and good[2][1:] == ["0", "nothing needs fixing", "good"] and good[3][3] == "good" and good[4][1:3] == ["0", "too little delivery in the last weeks to tell"]


def test_a_view_shows_the_accounts_that_need_a_look_and_opens_on_all_when_none_does(reports):
    assert run("X.viewCount(R, 'attention')", R=reports["mcc"]) == 1 and run("X.viewCount(R, 'all')", R=reports["mcc"]) == 2
    assert [a["name"] for a in run("X.shown(R, 'attention')", R=reports["mcc"])] == ["Acme <b>Dental</b>"] and run("X.firstView(R)", R=reports["mcc"]) == "attention"
    assert run("X.firstView(R)", R=reports["clean"]) == "all" and run("X.shown(R, 'attention')", R=reports["clean"]) == []
    assert run("X.query('1234567890')") == "account=1234567890" and run("X.query('1234567890+act_1', true)") == "account=1234567890%2Bact_1&refresh=1"


def test_an_account_reads_in_one_line_with_what_was_judged_and_what_is_wearing_out(reports):
    assert run("X.accountSub(A)", A=reports["one"]["accounts"][0]) == "Google Ads, 1 creative read, 1 judged, 1 wearing out"
    assert run("X.accountSub(A)", A=reports["clean"]["accounts"][0]) == "Google Ads, 1 creative read, 1 judged"
    assert run("X.accountSub(A)", A=reports["meta"]["accounts"][0]).startswith("Meta Ads, 2 creatives read, ")


def test_what_shows_only_across_the_accounts_is_said_in_sentences(reports):
    assert run("X.insights(R)", R=reports["clean"]) == [] and run("X.insights(R)", R=reports["one"]) == []                 # a check that failed is not also listed as one that could not be judged
    unk = json.loads(json.dumps(reports["clean"]))
    unk["accounts"][0]["checks"][1].update(state="unknown", note="Google's asset ratings could not be read.")
    assert run("X.insights(R)", R=unk) == [{"kind": "unjudged", "text": "Platform ratings of the assets: Google's asset ratings could not be read."}]
    unk["account"]["isGroup"] = True
    assert run("X.insights(R)", R=unk)[0]["text"] == "Platform ratings of the assets: 1 account could not be judged yet (open an account to see why)."
    tiny = json.loads(json.dumps(reports["clean"]))
    tiny["accounts"][0]["headline"]["notJudged"] = 3
    assert [i["text"] for i in run("X.insights(R)", R=tiny)] == ["3 creatives had too little delivery in the last weeks to judge, so they are not listed as fine."]


def test_the_csv_has_one_line_per_finding_and_one_for_an_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    assert rows[0] == ["Creative & Asset Health", "Group"] and rows[3] == run("X.HEAD") and len(rows[3]) == 10
    body = [r for r in rows[4:]]
    acme = [r for r in body if r[0] == "Acme <b>Dental</b>"]
    assert [r[2] for r in acme] == ["High", "High", "Medium"] and [r[3] for r in acme] == ["Tired creatives", "Missing assets", "Weak assets"] and acme[0][3] == "Tired creatives" and acme[0][4] == "Search Crowns" and "click-through rate down" in acme[0][6]
    assert [r for r in body if r[0] == "Beta Clinic"] == [["Beta Clinic", "Google Ads", "", "", "", "", "(nothing found)", "", "", ""]]
    assert all(r[1] == "Meta" for r in run("X.csvRows(R)", R=reports["meta"])[4:])


# ── the page script ─────────────────────────────────────────────────────────

SCOPE = os.path.join(JS, "pm_scope.js")
SCRIPTS = [SCOPE, DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}
HARNESS_PAGE = (HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/creative-health")
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
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/creative/report", "body": reports[key]}], "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("pm_platform.js") < html.index("pm_scope.js") < html.index("monitor_panel.js") < html.index("creative_health_data.js") < html.index("creative_health.js")
    assert "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_the_accounts_are_loaded_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and "MCC" in items[1]["badge"] and out["panels"] == [{"agent": "creative", "label": "Creative & Asset Health"}]
    assert not any("/creative/report" in x for x in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports, "one")
    assert [x for x in out["calls"] if "/creative/report" in x] == ["/p2/paid-marketing/api/creative/report?account=1234567890"]
    assert out["store"]["ch_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/creative-health?account=1234567890" and out["el"]["#out"]["hidden"] is False


def test_the_report_is_drawn_with_the_findings_the_checks_and_markup_escaped(reports):
    out = opened(reports, "one")
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Creatives read", "Wearing out", "To fix", "Accounts to fix", "Not judged"):
        assert text in kp, text
    assert 'class="bi-row stopped open"' in lst and "bi-pill bad" in lst and "Needs a fix" in lst and "What was found" in lst and "What was checked" in lst
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in lst and "<b>Dental</b>" not in lst and "Acme <b>" not in lst
    assert "click-through rate down 60%" in lst and "What to do." in lst and "Add a fresh variant next to it now" in lst and 'class="bi-lv bad">High' in lst and "Search Crowns" in lst
    assert "HEADLINE: Best crowns" in lst and "rated Low by Google" in lst and "No sitelinks, so the ad is smaller than it could be." in lst and "Creatives hold their click-through rate" in lst
    assert "read " in out["el"]["#fresh"]["html"] and "the ones to fix first" in out["el"]["#listNote"]["text"]
    assert "What this page cannot see" in out["el"]["#blind"]["html"] and "How old a creative is" in out["el"]["#blind"]["html"]
    foot = out["el"]["#foot"]["html"]
    assert "last 2 whole weeks against the 3 before" in foot and "1,000 impressions" in foot and "25 percent (40 is high)" in foot and "Nothing is changed in an ad account." in foot


def test_a_clean_account_says_so_and_a_group_shows_the_accounts_to_fix_first(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/creative/report", "body": reports["clean"]}], "steps": [{"pick": "2222222222"}]})
    lst = out["el"]["#list"]["html"]
    assert "Nothing unusual" in lst and "Nothing to flag: nothing unusual was found." in lst
    mcc = opened(reports, "mcc", [{"pick": GROUP}])
    html = mcc["el"]["#list"]["html"]
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in html and "Beta Clinic" not in html and "Needs a look" in mcc["el"]["#views"]["html"] and "MCC" in mcc["el"]["#fresh"]["html"]
    allv = opened(reports, "mcc", [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])
    assert "Beta Clinic" in allv["el"]["#list"]["html"]


def test_a_meta_account_shows_its_findings_and_a_row_opens_and_closes(reports):
    out = page({"responses": [{"match": "/gads/accounts", "body": {"accounts": [{"id": "act_1234567", "name": "Meta Co", "currency": "USD", "platform": "meta"}]}}, {"match": "/segments/accounts", "body": ACCTS},
                              {"match": "/creative/report", "body": reports["meta"]}], "steps": [{"platform": "meta"}, {"pickMeta": "act_1234567"}]})
    lst = out["el"]["#list"]["html"]
    assert "Meta Co" in lst and 'class="bi-mk">Meta' in lst and "Hero video" in lst and "Prospecting" in lst and "Meta ranks this ad below the ads competing for the same people" in lst
    closed = opened(reports, "one", [{"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}])
    assert "What was found" not in closed["el"]["#list"]["html"] and 'aria-expanded="false"' in closed["el"]["#list"]["html"]


def test_refresh_reads_again_with_the_refresh_flag_and_a_too_soon_refresh_says_when(reports):
    out = opened(reports, "one", [{"click": "#refreshBtn"}])
    asked = [x for x in out["calls"] if "/creative/report" in x]
    assert asked == ["/p2/paid-marketing/api/creative/report?account=1234567890", "/p2/paid-marketing/api/creative/report?account=1234567890&refresh=1"]
    soon = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/creative/report", "sequence": [reports["one"], {"error": "Refreshed a moment ago.", "kind": "cooldown", "retryIn": 90}], "statuses": [200, 429]}],
                 "steps": [{"pick": "1234567890"}, {"click": "#refreshBtn"}]})
    assert "Refreshed a moment ago." in soon["el"]["#msg"]["html"] and "pm-note" in soon["el"]["#msg"]["html"] and soon["el"]["#out"]["hidden"] is False


def test_a_report_that_could_not_be_built_is_said_in_words_for_each_kind_of_failure(reports):
    def failing(status, body):
        return page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/creative/report", "status": status, "body": body}], "steps": [{"pick": "1234567890"}]})
    quota = failing(429, {"error": "The daily limit is used up.", "kind": "quota"})["el"]["#msg"]["html"]
    assert "The daily limit is used up." in quota and "clears on its own" in quota
    assert "That is not one of Position2" in failing(400, {"error": "That is not one of Position2's accounts."})["el"]["#msg"]["html"]
    assert "The report could not be built (HTTP 500)." in failing(500, {})["el"]["#msg"]["html"]
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "reject": "/creative/report", "steps": [{"pick": "1234567890"}]})
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"] and down["el"]["#empty"]["hidden"] is False
    warned = dict(reports["one"], warnings=["Acme <b>Dental</b>: Google's ratings of the ads' assets could not be read."])
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/creative/report", "body": warned}], "steps": [{"pick": "1234567890"}]})
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;: Google&#39;s ratings of the ads&#39; assets could not be read." in out["el"]["#msg"]["html"]


def test_the_csv_is_downloaded_and_a_cell_that_starts_like_a_formula_is_defused(reports):
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["name"] = "=HYPERLINK(\"x\")"
    out = opened({"one": risky}, "one", [{"click": "#exportBtn"}])
    assert out["blob"].startswith("﻿Creative & Asset Health,Acme") and "'=HYPERLINK" in out["blob"] and "click-through rate down" in out["blob"] and out["downloads"][0]["name"].startswith("creative-health-")
