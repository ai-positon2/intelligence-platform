"""Asset Coverage Audit: the page script run in Node against a stub page and a fake report.

The server counts and judges (tests/test_paid_assets.py); the page only draws. Here the whole script runs for real: it loads the accounts,
asks for the report, and the HTML it writes is read back: the numbers on the cards, the coverage bars, which campaigns each filter shows,
what an opened campaign says, the account-wide assets, the CSV it would download, and what it does with an error or a cooldown.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "asset_coverage.js")
TEMPLATE = os.path.join(ROOT, "templates", "asset_coverage.html")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8"), scenario = JSON.parse(process.argv[2]);
const els = {}, handlers = {}, downloads = [];
function el(sel){
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", hidden: false, value: "", dataset: {},
    addEventListener(ev, fn){ handlers[sel + ":" + ev] = fn; }, classList: { add(){}, remove(){}, toggle(){} }, closest: () => null, click(){}, remove(){}, appendChild(){} };
  return els[sel];
}
const doc = { querySelector: el, createElement: () => { const e = el("created" + downloads.length); e.click = () => downloads.push({ href: e.href, name: e.download }); return e; }, body: { appendChild(){} } };
const calls = [];
const picker = { sets: [], set(items, v){ this.sets.push([items, v]); } };
function respond(url){
  calls.push(url);
  if (url.includes("/segments/accounts")) return scenario.accounts;
  return scenario.reportBody;
}
const ctx = { window: { PMPicker: (host, o) => { picker.onChange = o.onChange; return picker; } }, document: doc, history: { replaceState(){} }, location: { pathname: "/p", search: scenario.search || "" },
  localStorage: { getItem: () => "", setItem(){} }, URLSearchParams, URL: { createObjectURL: b => { downloads.blob = b; return "blob:x"; }, revokeObjectURL(){} },
  Blob: class { constructor(parts){ this.text = parts.join(""); } }, setTimeout: fn => fn(), Intl, Date, Math, JSON, Object, Array, String, Number, Promise, console,
  fetch: url => Promise.resolve(respond(url)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) })) };
ctx.window.document = doc;
vm.createContext(ctx);
vm.runInContext(src, ctx);
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  for (let i = 0; i < 6; i++) await tick();
  const out = {};
  for (const step of scenario.steps || []) {
    if (step.click) { const [sel, attr, val] = step.click; const b = { dataset: { [attr]: val }, closest: q => q === "button" ? b : null }; handlers[sel + ":click"]({ target: b }); }
    if (step.type) { el("#filter").value = step.type; handlers["#filter:input"].call(el("#filter")); }
    if (step.open) { const head = { closest: q => q === ".ac-head" ? head : q === ".ac-row" ? { dataset: { key: step.open } } : null }; handlers["#list:click"]({ target: head }); }
    if (step.button) handlers[step.button + ":click"]();
    for (let i = 0; i < 6; i++) await tick();
  }
  const keys = ["#kpis", "#cov", "#sevs", "#kinds", "#list", "#acctAssets", "#foot", "#fresh", "#msg", "#listNote", "#more"];
  for (const k of keys) out[k] = els[k] ? els[k].innerHTML || els[k].textContent : "";
  out.hidden = { out: els["#out"] && els["#out"].hidden, empty: els["#empty"] && els["#empty"].hidden, acctCard: els["#acctCard"] && els["#acctCard"].hidden, more: els["#more"] && els["#more"].hidden };
  out.calls = calls; out.csv = downloads.blob ? downloads.blob.text : null; out.download = downloads[0] || null; out.pickerLast = picker.sets.length;
  process.stdout.write(JSON.stringify(out));
})();
"""


def slot(key, label, have, target, status="ok", severity=None, source="campaign", **kw):
    d = {"key": key, "label": label, "have": have, "min": 2, "target": target, "status": status, "source": source}
    if severity:
        d["severity"] = severity
    d.update(kw)
    return d


def gap(label, severity, scope, message, add, key="X"):
    return {"scope": scope, "key": key, "label": label, "severity": severity, "have": 0, "target": add, "add": add, "message": message}


CAMPAIGNS = [
    {"key": "1:11", "id": "11", "name": "Hudson_Broad", "type": "SEARCH", "typeLabel": "Search", "subType": "", "account": "Acme", "accountId": "1", "impressions": 2217, "clicks": 90,
     "cost": 200.5, "conversions": 4, "severity": "high", "counts": {"high": 1, "medium": 0, "low": 1}, "notShown": 0, "problems": {},
     "slots": [slot("SITELINK", "Sitelinks", 0, 4, "missing", "high", expired=1, bad=[{"label": "Free Teeth Whitening", "state": "expired"}], add=4,
                    message="No live sitelinks. Add 4. 1 past their end date."), slot("CALLOUT", "Callouts", 5, 4, source="account")],
     "groups": [], "ads": [],
     "gaps": [gap("Sitelinks", "high", "Campaign", "No live sitelinks. Add 4. 1 past their end date.", 4), gap("Business name", "low", "Campaign", "No live business names. Add 1.", 1)]},
    {"key": "1:12", "id": "12", "name": "PMax <Spring>", "type": "PERFORMANCE_MAX", "typeLabel": "Performance Max", "subType": "", "account": "Acme", "accountId": "1", "impressions": 6000,
     "clicks": 200, "cost": 900, "conversions": 10, "severity": "medium", "counts": {"high": 0, "medium": 1, "low": 0}, "notShown": 2, "problems": {},
     "slots": [slot("SITELINK", "Sitelinks", 4, 4, notShown=2)],
     "groups": [{"id": "700", "name": "Crowns", "severity": "medium", "slots": [slot("YOUTUBE_VIDEO", "Videos", 0, 1, "missing", "medium", add=1, message="No live videos. Add 1.", disapproved=1,
                                                                                    bad=[{"label": "Tour", "state": "disapproved"}])]}],
     "ads": [], "gaps": [gap("Videos", "medium", "Asset group: Crowns", "No live videos. Add 1.", 1)]},
    {"key": "1:13", "id": "13", "name": "Complete", "type": "SEARCH", "typeLabel": "Search", "subType": "", "account": "Acme", "accountId": "1", "impressions": 100, "clicks": 3, "cost": 5,
     "conversions": 0, "severity": None, "counts": {"high": 0, "medium": 0, "low": 0}, "notShown": 0, "problems": {}, "slots": [slot("SITELINK", "Sitelinks", 6, 4)], "groups": [], "ads": [], "gaps": []},
    {"key": "1:14", "id": "14", "name": "Display lookalikes", "type": "DISPLAY", "typeLabel": "Display", "subType": "", "account": "Acme", "accountId": "1", "impressions": 50, "clicks": 1, "cost": 2,
     "conversions": 0, "severity": "low", "counts": {"high": 0, "medium": 0, "low": 1}, "notShown": 0, "problems": {}, "slots": [], "groups": [],
     "ads": [{"id": "9", "adGroup": "Level 1", "type": "RESPONSIVE_DISPLAY_AD", "typeLabel": "Responsive display ad", "severity": "low", "slots": [slot("YOUTUBE_VIDEO", "Videos", 0, 1, "missing", "low", add=1)]}],
     "gaps": [gap("Videos", "low", "Responsive display ad: Level 1", "No live videos. Add 1.", 1)]},
]
REPORT = {"account": {"id": "1234567890", "name": "Acme Dental", "currency": "USD", "platform": "google", "isGroup": False, "accountCount": 0},
          "window": {"days": 30, "from": "2026-09-17", "to": "2026-10-16"}, "generatedAt": "2026-10-16T10:00:00+00:00", "warnings": [], "campaigns": CAMPAIGNS,
          "accountAssets": [{"account": "Acme Dental", "accountId": "1", "locations": 2, "slots": [{"key": "CALLOUT", "label": "Callouts", "have": 5, "disapproved": 1, "paused": 0, "pending": 0, "expired": 0,
                                                                                                     "bad": [{"label": "Free consult", "state": "disapproved"}]}]}],
          "notAudited": [{"type": "SHOPPING", "label": "Shopping (built from the product feed)", "campaigns": 2}],
          "summary": {"campaigns": 4, "withGaps": 3, "fixFirst": 1, "gaps": {"high": 1, "medium": 1, "low": 2}, "impressions": 8367, "impressionsFixFirst": 2217, "disapprovedAssets": 3,
                      "paused": 5, "expired": 1, "pending": 0, "notShown": 2,
                      "coverage": [{"key": "SITELINK", "label": "Sitelinks", "campaigns": 3, "atTarget": 2, "below": 0, "none": 1, "unknown": 0, "optional": False},
                                   {"key": "PROMOTION", "label": "Promotions", "campaigns": 3, "atTarget": 0, "below": 0, "none": 3, "unknown": 0, "optional": True}]}}
ACCOUNTS = {"status": 200, "body": {"accounts": [{"id": "1234567890", "name": "Acme Dental", "group": None}], "groups": []}}


def run(scenario):
    scenario = dict({"accounts": ACCOUNTS, "search": "?account=1234567890", "report": lambda url: {"status": 200, "body": REPORT}}, **scenario)
    scenario["reportBody"] = scenario.pop("report")("")
    out = subprocess.run(["node", "-e", HARNESS, JS, json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def test_the_cards_show_the_servers_numbers_and_the_busiest_urgent_campaign_opens_first():
    out = run({})
    kpis = out["#kpis"]
    for text in ("Campaigns audited", ">4<", "3 have a gap", "Fix first", ">1<", "Impressions at stake", "2,217", "26% of all impressions", "1 fix first · 1 add · 2 nice to have", "Disapproved assets", ">3<", "Past their end date", "ended but still switched on"):
        assert text in kpis, text
    assert out["hidden"]["out"] is False and out["hidden"]["empty"] is True and any("/api/assets/report?account=1234567890" in c for c in out["calls"])
    assert "Hudson_Broad" in out["#list"] and "PMax" not in out["#list"] and "Complete" not in out["#list"]          # the default is Fix first
    assert "Fix first <span class=\"n\">1</span>" in out["#sevs"] and "Including nice to have <span class=\"n\">3</span>" in out["#sevs"] and "No gaps <span class=\"n\">1</span>" in out["#sevs"]


def test_each_filter_shows_the_campaigns_worst_gap_puts_in_it_and_the_kind_and_text_filters_narrow_it():
    steps = [{"click": ["#sevs", "s", "medium"]}]
    out = run({"steps": steps})
    assert out["#list"].index("PMax &lt;Spring&gt;") > out["#list"].index("Hudson_Broad") and "Display lookalikes" not in out["#list"]       # busiest first only within a severity; high before medium
    out = run({"steps": [{"click": ["#sevs", "s", "low"]}]})
    assert all(n in out["#list"] for n in ("Hudson_Broad", "PMax &lt;Spring&gt;", "Display lookalikes")) and "Complete" not in out["#list"]
    out = run({"steps": [{"click": ["#sevs", "s", "none"]}]})
    assert "Complete" in out["#list"] and "Hudson_Broad" not in out["#list"] and "Every asset kind is at its target." in out["#list"]
    out = run({"steps": [{"click": ["#sevs", "s", "low"]}, {"click": ["#kinds", "k", "DISPLAY"]}]})
    assert "Display lookalikes" in out["#list"] and "Hudson_Broad" not in out["#list"]
    out = run({"steps": [{"click": ["#sevs", "s", "low"]}, {"type": "video"}]})
    assert "PMax" in out["#list"] and "Display lookalikes" in out["#list"] and "Hudson_Broad" not in out["#list"]           # matched on the kind of asset in the gap
    out = run({"steps": [{"type": "zzz"}]})
    assert "No campaign matches." in out["#list"]


def test_the_default_filter_falls_back_when_nothing_is_urgent():
    quiet = json.loads(json.dumps(REPORT))
    for c in quiet["campaigns"]:
        if c["severity"] == "high":
            c.update(severity=None, gaps=[], counts={"high": 0, "medium": 0, "low": 0})
    out = run({"report": lambda u: {"status": 200, "body": quiet}})
    assert "PMax &lt;Spring&gt;" in out["#list"] and "Hudson_Broad" not in out["#list"]                                       # no fix-first, so it opens on Fix first and add
    for c in quiet["campaigns"]:
        c.update(severity=None, gaps=[], counts={"high": 0, "medium": 0, "low": 0})
    out = run({"report": lambda u: {"status": 200, "body": quiet}})
    assert "Every asset kind is at its target." in out["#list"] and "Hudson_Broad" in out["#list"]


def test_an_opened_campaign_says_what_to_add_what_it_holds_and_what_is_wrong_with_what_it_has():
    out = run({"steps": [{"open": "1:11"}]})
    h = out["#list"]
    assert "What to add" in h and "No live sitelinks. Add 4. 1 past their end date." in h and "+4" in h and "Nice to have" in h
    assert "Campaign assets" in h and "1 past end date" in h and "Free Teeth Whitening (past end date)" in h and "the account" in h
    out = run({"steps": [{"click": ["#sevs", "s", "medium"]}, {"open": "1:12"}]})
    h = out["#list"]
    assert "Asset groups (1)" in h and "Crowns" in h and "Tour (disapproved)" in h and "2 not shown in 30 days" in h


def test_account_wide_assets_are_listed_once_with_their_problems():
    out = run({})
    assert out["hidden"]["acctCard"] is False
    assert "Callouts" in out["#acctAssets"] and "1 disapproved" in out["#acctAssets"] and "Free consult (disapproved)" in out["#acctAssets"] and "2 location sets linked" in out["#acctAssets"]
    assert "Shopping (built from the product feed)" in out["#foot"] and "Targets are Position2&#39;s rule of thumb" not in out["#foot"] and "Targets are Position2's rule of thumb" in out["#foot"]


def test_coverage_bars_show_each_kind_and_mark_optional_ones():
    cov = run({})["#cov"]
    assert "2 of 3 at target · 1 with none" in cov and "Sitelinks" in cov and "<em>optional</em>" in cov and "0 of 3 at target · 3 with none" in cov


def test_text_from_google_is_escaped_everywhere_it_is_drawn():
    evil = json.loads(json.dumps(REPORT))
    evil["campaigns"][0]["name"] = "<img src=x onerror=alert(1)>"
    evil["campaigns"][0]["slots"][0]["bad"] = [{"label": "<script>alert(2)</script>", "state": "disapproved"}]
    evil["accountAssets"][0]["slots"][0]["bad"] = [{"label": "<b>x</b>", "state": "expired"}]
    out = run({"report": lambda u: {"status": 200, "body": evil}, "steps": [{"open": "1:11"}]})
    assert "<img" not in out["#list"] and "<script" not in out["#list"] and "<b>x" not in out["#acctAssets"] and "&lt;img src=x" in out["#list"]


def test_the_csv_has_one_row_per_gap_with_the_numbers_and_quotes_what_needs_it():
    commas = json.loads(json.dumps(REPORT))
    commas["campaigns"][1]["name"] = 'PMax, "Spring"'
    quoted = run({"report": lambda u: {"status": 200, "body": commas}, "steps": [{"button": "#exportBtn"}]})["csv"]
    assert 'Acme,"PMax, ""Spring""",Performance Max' in quoted
    out = run({"steps": [{"button": "#exportBtn"}]})
    lines = out["csv"].lstrip("﻿").split("\r\n")
    assert lines[0] == "Account,Acme Dental" and lines[1] == "Window,2026-09-17 to 2026-10-16" and lines[3].startswith("Account,Campaign,Kind of campaign,Where,Asset,Priority")
    assert lines[4] == 'Acme,Hudson_Broad,Search,Campaign,Sitelinks,Fix first,0,4,4,No live sitelinks. Add 4. 1 past their end date.,2217,90,200.5'
    assert len(lines) == 4 + 4 and out["download"]["name"] == "asset-coverage-Acme-Dental-2026-10-16.csv"
    assert any(l.startswith("Acme,PMax <Spring>,Performance Max,Asset group: Crowns,Videos,Add,0,1,1,") for l in lines)


def test_an_error_a_cooldown_and_an_unreachable_server_are_said_plainly():
    out = run({"report": lambda u: {"status": 429, "body": {"error": "Too many reads.", "kind": "quota"}}})
    assert "Too many reads." in out["#msg"] and "clears on its own" in out["#msg"] and out["hidden"]["empty"] is False
    out = run({"report": lambda u: {"status": 429, "body": {"error": "Refreshed a moment ago.", "kind": "cooldown", "retryIn": 30}}})
    assert "Refreshed a moment ago." in out["#msg"] and "clears on its own" not in out["#msg"]
    out = run({"report": lambda u: {"status": 400, "body": {"error": "That account is not one of Position2's Google Ads accounts."}}})
    assert "not one of Position2" in out["#msg"].replace("&#39;", "'")
    out = run({"accounts": {"status": 503, "body": {"error": "Google Ads is not connected."}}})
    assert "Google Ads is not connected." in out["#msg"]


def test_the_warnings_and_a_stale_copy_are_shown_and_refresh_asks_for_a_fresh_read():
    stale = dict(REPORT, warnings=["Acme: the ad group assets could not be read (google broke)."], stale="2026-10-15T09:00:00+00:00")
    out = run({"report": lambda u: {"status": 200, "body": stale}, "steps": [{"button": "#refreshBtn"}]})
    assert "could not be read" in out["#msg"] and "Google Ads is not answering" in out["#fresh"]
    assert sum("/report" in c for c in out["calls"]) == 2 and out["calls"][-1].endswith("&refresh=1")


def test_the_template_has_every_element_the_script_reaches_for():
    html = open(TEMPLATE, encoding="utf-8").read()
    import re
    js = open(JS, encoding="utf-8").read()
    ids = set(re.findall(r'\$\("#([A-Za-z]+)"\)', js))
    assert ids and all('id="%s"' % i in html for i in ids), [i for i in ids if 'id="%s"' % i not in html]


def test_a_share_is_rounded_not_cut_and_an_mccs_rows_name_their_account_and_coverage_says_how_many_are_below():
    rep = json.loads(json.dumps(REPORT))
    rep["summary"]["impressions"], rep["summary"]["impressionsFixFirst"] = 3, 2                       # 66.67 percent
    rep["summary"]["coverage"][0].update(below=2, atTarget=0, none=1)
    rep["account"].update(isGroup=True, accountCount=2)
    out = run({"report": lambda u: {"status": 200, "body": rep}})
    assert "67% of all impressions" in out["#kpis"] and "Search · Acme" in out["#list"] and "0 of 3 at target · 2 below · 1 with none" in out["#cov"]
    single = run({})
    assert "Search · Acme" not in single["#list"]


def test_an_optional_kind_with_none_is_marked_optional_not_flagged():
    rep = json.loads(json.dumps(REPORT))
    rep["campaigns"][0]["slots"].append(slot("PROMOTION", "Promotions", 0, 1, "missing", "info"))
    rep["campaigns"][0]["slots"].append(slot("PRICE", "Prices", 2, 1))
    out = run({"report": lambda u: {"status": 200, "body": rep}, "steps": [{"open": "1:11"}]})
    row = [r for r in out["#list"].split("<tr") if "Promotions" in r][0]
    assert "optional" in row and "optional" not in [r for r in out["#list"].split("<tr") if "Prices" in r][0]
