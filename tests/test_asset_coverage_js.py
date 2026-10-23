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
TILES = os.path.join(ROOT, "static", "js", "asset_tiles.js")
META = os.path.join(ROOT, "static", "js", "meta_creative.js")
TEMPLATE = os.path.join(ROOT, "templates", "asset_coverage.html")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const pageSrc = fs.readFileSync(process.argv[1], "utf8"), tilesSrc = fs.readFileSync(process.argv[2], "utf8"), scenario = JSON.parse(process.argv[3]), mcSrc = fs.readFileSync(process.argv[4], "utf8");
const els = {}, handlers = {}, downloads = [], log = { scrolled: [], focused: [], classes: [], moved: [] };
function el(sel){
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", hidden: ["#acDrawer", "#acZoom", "#acdMore", "#acdExport"].includes(sel), value: "", src: "", dataset: {}, parentNode: null, open: false,
    addEventListener(ev, fn){ handlers[sel + ":" + ev] = fn; }, classList: { add(){}, remove(){}, toggle(){} }, closest: () => null, click(){}, remove(){}, appendChild(){},
    focus(){ log.focused.push(sel); }, scrollIntoView(){ log.scrolled.push(sel); }, setSelectionRange(){} };
  return els[sel];
}
const doc = { querySelector: el, createElement: () => { const e = el("created" + downloads.length); e.click = () => downloads.push({ href: e.href, name: e.download }); return e; },
  body: { appendChild(n){ log.moved.push(n.sel); } }, addEventListener(ev, fn){ handlers["document:" + ev] = fn; },
  documentElement: { classList: { add(c){ log.classes.push("+" + c); }, remove(c){ log.classes.push("-" + c); } } }, activeElement: { sel: "opener", focus(){ log.focused.push("opener"); } } };
const calls = [], held = [];
const picker = { sets: [], set(items, v){ this.sets.push([items, v]); } };
/* the shared scope control (static/js/pm_scope.js, tested in test_scope_js.py): it reports the account and platform chosen, and knows the MCC groups of the account list */
const scope = { o: null };
const PMScope = { isMeta: id => /^act_\d+$/.test(id || ""), partsOf: id => String(id || "").split("+"),
  mount: (host, o) => { scope.o = o; const groups = (((scenario.accounts || {}).body || {}).groups) || [];
    const acc = scenario.accounts || {};
    if ((acc.status || 200) >= 400) Promise.resolve().then(() => o.onError(new Error((acc.body || {}).error)));
    else Promise.resolve().then(() => o.onChange(o.account || "", /\+/.test(o.account || "") ? "both" : /^act_/.test(o.account || "") ? "meta" : "google"));
    return { groupOf: id => groups.filter(g => g.id === id)[0] || null }; } };
function respond(url){
  calls.push(url);
  if (scenario.hang && url.includes(scenario.hang)) return new Promise(() => {});       // an answer that never comes
  if (scenario.hold && url.includes(scenario.hold) && !held.released) return new Promise(res => held.push(() => res(answer(url))));      // an answer that comes when told to
  return answer(url);
}
function answer(url){
  for (const r of scenario.responses || []) if (url.includes(r.match)) return r.body === undefined ? r : { status: r.status || 200, body: r.body };
  if (url.includes("/segments/accounts")) return scenario.accounts;
  return scenario.reportBody;
}
const ctx = { window: { PMPicker: (host, o) => { picker.onChange = o.onChange; return picker; }, PMScope }, document: doc, history: { replaceState(){} }, location: { pathname: "/p", search: scenario.search || "" },
  localStorage: { getItem: () => "", setItem(){} }, URLSearchParams, URL: { createObjectURL: b => { downloads.blob = b; return "blob:x"; }, revokeObjectURL(){} },
  Blob: class { constructor(parts){ this.text = parts.join(""); } }, setTimeout: fn => fn(), Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, console,
  fetch: url => (scenario.reject && url.includes(scenario.reject) ? (calls.push(url), Promise.reject(new Error("down"))) :
    Promise.resolve(respond(url)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) }))) };
ctx.window.document = doc;
vm.createContext(ctx);
vm.runInContext(tilesSrc, ctx);
vm.runInContext(mcSrc, ctx);
vm.runInContext(pageSrc, ctx);
const tick = () => new Promise(r => setImmediate(r));
// a fake click target: closest(selector) answers with an element carrying that selector's dataset (or null)
function target(map){
  const make = spec => Object.assign({ dataset: {}, closest: q => (map[q] ? make(map[q]) : null) }, spec);
  return make(map["self"] || {});
}
(async () => {
  for (let i = 0; i < 6; i++) await tick();
  const out = {};
  for (const step of scenario.steps || []) {
    if (step.scope) scope.o.onChange(step.scope[0], step.scope[1]);
    if (step.click) { const [sel, attr, val] = step.click; const b = { dataset: { [attr]: val }, closest: q => q === "button" ? b : null }; handlers[sel + ":click"]({ target: b }); }
    if (step.type) { el("#filter").value = step.type; handlers["#filter:input"].call(el("#filter")); }
    if (step.open) { const head = { closest: q => q === ".ac-head" ? head : q === ".ac-row" ? { dataset: { key: step.open } } : null }; handlers["#list:click"]({ target: head }); }
    if (step.button) handlers[step.button + ":click"]();
    if (step.release) { held.released = true; held.splice(0).forEach(f => f()); }
    if (step.fire) {
      const [sel, ev, map, extra] = step.fire;
      const e = Object.assign({ target: target(map || {}), preventDefault(){ log.prevented = (log.prevented || 0) + 1; }, key: "" }, extra || {});
      if (map && map.self && map.self.id !== undefined) e.target.id = map.self.id;
      handlers[sel + ":" + ev].call(e.target, e);
    }
    for (let i = 0; i < 6; i++) await tick();
  }
  const keys = ["#metaOut", "#metaMsg", "#kpis", "#cov", "#sevs", "#kinds", "#list", "#acctAssets", "#foot", "#fresh", "#msg", "#listNote", "#more"];
  for (const k of keys) out[k] = els[k] ? els[k].innerHTML || els[k].textContent : "";
  out.hidden = { out: els["#out"] && els["#out"].hidden, empty: els["#empty"] && els["#empty"].hidden, acctCard: els["#acctCard"] && els["#acctCard"].hidden, more: els["#more"] && els["#more"].hidden };
  out.el = {}; for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, src: els[k].src, value: els[k].value };
  out.log = log; out.handlers = Object.keys(handlers);
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
CAMPAIGNS[0]["gaps"][0]["plan"] = {"steps": ["Open Google Ads, then Campaigns, click \"Hudson_Broad\".", "Click the +, choose <Sitelink>."], "worth": "The campaign had 2,217 impressions in the last 30 days with none live."}
CAMPAIGNS[0]["gaps"][1]["plan"] = {"steps": [], "worth": "Never shown: a plan with no steps draws nothing."}
CAMPAIGNS[1]["groups"][0]["slots"][0]["bad"] = [{"label": "Tour", "state": "disapproved", "why": [{"t": "HEALTH_IN_PERSONALIZED_ADS", "ty": "PROHIBITED"}, {"t": "PUNCTUATION_AND_SYMBOLS", "ty": "LIMITED"}]}]
CAMPAIGNS[1]["groups"][0]["slots"][0]["fix"] = {"steps": ["Open Google Ads, then Tools, then Policy manager.", "Edit the <text>."]}
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
    out = subprocess.run(["node", "-e", HARNESS, JS, TILES, json.dumps(scenario), META], capture_output=True, text=True, timeout=60)
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
    assert "Asset groups (1)" in h and "Crowns" in h and "Tour (disapproved: Health in personalized ads, Punctuation and symbols)" in h and "2 not shown in 30 days" in h


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
    assert lines[0] == "Account,Acme Dental" and lines[1] == "Window,2026-09-17 to 2026-10-16" and lines[3].startswith("Account,Campaign,Kind of campaign,Where,Asset,Priority") and lines[3].endswith(",How to add them")
    assert lines[4] == 'Acme,Hudson_Broad,Search,Campaign,Sitelinks,Fix first,0,4,4,No live sitelinks. Add 4. 1 past their end date.,2217,90,200.5,"Open Google Ads, then Campaigns, click ""Hudson_Broad"". Click the +, choose <Sitelink>."'
    assert lines[5].endswith(",2217,90,200.5,")                                                                  # a gap with no plan leaves the cell empty
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
    ids = set(re.findall(r'\$\("#([A-Za-z]+)"\)', js)) - {"acdFind"}                      # the find box is drawn with the drawer's tools
    assert ids and all('id="%s"' % i in html for i in ids), [i for i in ids if 'id="%s"' % i not in html]
    assert {"acDrawer", "acdT", "acdS", "acdTools", "acdBody", "acdMore", "acdExport", "acdClose", "acZoom", "acZimg", "acZcap", "acFloat", "listCard"} <= ids


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


# ═══ Everything opens: the cards, the bars, the kinds of asset ═══════════════════════════════════════════════════════════════════════════════
# The detail the page asks for is built here by the server's own code from fake Google rows, so the page is tested against the real shape.

import sys  # noqa: E402

sys.path.insert(0, ROOT)
from tests.test_paid_assets import TODAY, camp_row, link_row, searcher  # noqa: E402
from tracker import paid_assets as A  # noqa: E402
from tracker import paid_assets_detail as D  # noqa: E402

GOOGLE_IMG = "https://tpc.googlesyndication.com/simgad/2833249052621513497"
PICTURE = {"type": "IMAGE", "id": "i1", "name": "Hero.png", "imageAsset": {"fullSize": {"url": GOOGLE_IMG, "widthPixels": "896", "heightPixels": "640"}, "fileSize": "24273"}}
MEMBER = {"id": "1", "name": "Acme Dental"}
CAMP = "/p2/paid-marketing/api/assets/campaign?account=1234567890&campaign="
LIB = "/p2/paid-marketing/api/assets/library?account=1234567890"


def sl(cid, n, words, **kw):
    return link_row(cid, "SITELINK", "sl%s" % n, asset={"sitelinkAsset": {"linkText": words, "description1": "Line one"}, "finalUrls": ["https://x.com/%s" % n]}, **kw)


def detail():
    """What the server saves for the fixture's account: campaign 11 (Search), 12 (Performance Max with one group, 700) and 14 (Display, ad 9)."""
    s = D.Collector(MEMBER, TODAY)
    ad = {"campaign": {"id": "14"}, "adGroup": {"id": "70", "name": "Level 1"}, "adGroupAd": {"ad": {"id": "9", "type": "RESPONSIVE_DISPLAY_AD", "responsiveDisplayAd": {
        "marketingImages": [{"asset": "customers/1/assets/i1"}], "headlines": [{"text": "Hello"}], "businessName": "Acme"}}}}
    A.read_account(MEMBER, today=TODAY, sink=s, search_fn=searcher(
        campaigns=[camp_row("11", "Hudson_Broad"), camp_row("12", "PMax <Spring>", "PERFORMANCE_MAX"), camp_row("14", "Display lookalikes", "DISPLAY")],
        campaign_assets=[sl("11", 1, "Book now"), sl("11", 2, "Our team"), sl("11", 3, "Gone", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"]),
                         link_row("11", "AD_IMAGE", "i1", asset=PICTURE), link_row("11", "CALLOUT", "co1", asset={"calloutAsset": {"calloutText": "Open late"}})],
        groups=[{"campaign": {"id": "12"}, "assetGroup": {"id": "700", "name": "Crowns"}}],
        group_assets=[{"campaign": {"id": "12"}, "assetGroup": {"id": "700"}, "assetGroupAsset": {"fieldType": "HEADLINE", "status": "ENABLED", "primaryStatus": "ELIGIBLE"},
                       "asset": {"id": "h1", "type": "TEXT", "textAsset": {"text": "Crowns today"}}}],
        ads={"RESPONSIVE_DISPLAY_AD": [ad]}, asset_by_id=[{"asset": PICTURE}]))
    return s.to_dict()


DET = detail()
CAMP_11, CAMP_12, CAMP_14 = (D.campaign_detail(DET, c) for c in ("11", "12", "14"))
SITELINKS = D.library([DET], "SITELINK", "live")


def responses(*extra):
    return list(extra) + [{"match": CAMP + "1%3A11", "body": CAMP_11}, {"match": CAMP + "1%3A12", "body": CAMP_12}, {"match": CAMP + "1%3A14", "body": CAMP_14},
                          {"match": LIB + "&kind=SITELINK&state=live", "body": SITELINKS}]


def fire(sel, ev, map_=None, **extra):
    return {"fire": [sel, ev, map_ or {}, extra]}


def card(act):
    return fire("#kpis", "click", {"[data-act]": {"dataset": {"act": act}}})


def lib_body(n=3, total=None, state="live", words="Callout %02d"):
    items = [{"t": "CALLOUT", "x": words % i, "st": "live", "id": "c%d" % i, "fl": ["Callouts"], "nc": 2, "ng": 0, "camps": [{"key": "1:11", "name": "Hudson_Broad"}]} for i in range(n)]
    return {"kind": "CALLOUT", "state": state, "label": "Callouts", "total": total or n, "assetCount": total or n, "counts": {"all": n, "live": n, "problem": 0, "disapproved": 0, "expired": 0, "paused": 0, "pending": 0}, "assets": items}


def html(out, sel):
    return out["el"].get(sel, {}).get("html", "")


def test_the_cards_are_buttons_and_the_ones_about_campaigns_filter_the_list():
    out = run({})
    for act in ("all", "high", "low", "disapproved", "expired"):
        assert 'data-act="%s"' % act in out["#kpis"], act
    assert out["#kpis"].count('role="button" tabindex="0"') == 6
    out = run({"steps": [card("low")]})
    assert 'class="pm-chip on" data-s="low"' in out["#sevs"] and "Display lookalikes" in out["#list"] and "#listCard" in out["log"]["scrolled"]
    out = run({"steps": [card("all")]})
    assert 'class="pm-chip on" data-s="all"' in out["#sevs"] and "All campaigns <span class=\"n\">4</span>" in out["#sevs"] and "Complete" in out["#list"] and "Hudson_Broad" in out["#list"]
    out = run({"steps": [card("all"), card("high")]})
    assert "Hudson_Broad" in out["#list"] and "Complete" not in out["#list"] and "PMax" not in out["#list"]
    out = run({"steps": [{"click": ["#kinds", "k", "DISPLAY"]}, {"type": "zzz"}, card("high")]})
    assert out["el"]["#filter"]["value"] == "" and 'class="pm-chip on" data-k="all"' in out["#kinds"] and "Hudson_Broad" in out["#list"]       # the filters are cleared first


def test_a_card_answers_the_keyboard_too():
    out = run({"steps": [fire("#kpis", "keydown", {"[data-act]": {"dataset": {"act": "low"}}}, key="Enter")]})
    assert 'class="pm-chip on" data-s="low"' in out["#sevs"] and out["log"]["prevented"] == 1
    out = run({"steps": [fire("#kpis", "keydown", {"[data-act]": {"dataset": {"act": "low"}}}, key=" ")]})
    assert 'class="pm-chip on" data-s="low"' in out["#sevs"]
    out = run({"steps": [fire("#kpis", "keydown", {"[data-act]": {"dataset": {"act": "low"}}}, key="a")]})
    assert 'class="pm-chip on" data-s="high"' in out["#sevs"] and "prevented" not in out["log"]


def test_the_disapproved_card_opens_those_assets_with_who_uses_them():
    body = D.library([DET], None, "disapproved")
    out = run({"responses": [{"match": LIB + "&kind=&state=disapproved", "body": body}], "steps": [card("disapproved")]})
    assert LIB + "&kind=&state=disapproved" in out["calls"]
    d = out["el"]
    assert d["#acDrawer"]["hidden"] is False and d["#acdT"]["text"] == "Disapproved assets" and d["#acdS"]["text"] == "1 asset"
    assert "Gone" in d["#acdBody"]["html"] and "Disapproved by Google" in d["#acdBody"]["html"] and "Used by 1 campaign" in d["#acdBody"]["html"] and 'data-camp="1:11"' in d["#acdBody"]["html"]
    assert d["#acdExport"]["hidden"] is False and d["#acdMore"]["hidden"] is True and "data-state" not in d["#acdTools"]["html"] and 'id="acdFind"' in d["#acdTools"]["html"]
    assert "+ac-lock" in out["log"]["classes"] and "#acdClose" in out["log"]["focused"] and "#acFloat" in out["log"]["moved"]


def test_the_end_date_card_opens_the_assets_past_it_and_counts_assets_not_links():
    rep = json.loads(json.dumps(REPORT))
    rep["summary"].update(expiredAssets=2, expired=9)
    out = run({"report": lambda u: {"status": 200, "body": rep}, "responses": [{"match": LIB + "&kind=&state=expired", "body": D.library([DET], None, "expired")}], "steps": [card("expired")]})
    assert ">2<" in out["#kpis"] and "9" not in out["#kpis"].split("Past their end date")[1].split("</div></div>")[0] and "assets ended but still switched on" in out["#kpis"]
    assert LIB + "&kind=&state=expired" in out["calls"] and out["el"]["#acdT"]["text"] == "Past their end date" and out["el"]["#acdBody"]["html"].count("ac-tile") == 0
    assert "None." in out["el"]["#acdBody"]["html"]                                                    # none of the fixture's sitelinks is past its date
    old = json.loads(json.dumps(REPORT))                                                              # a report from before the server counted assets still shows its link count
    assert ">1<" in run({"report": lambda u: {"status": 200, "body": old}})["#kpis"]


def test_a_coverage_bar_opens_every_asset_of_its_kind_with_filters_and_the_campaigns_short_of_it():
    out = run({"responses": responses(), "steps": [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}})]})
    assert LIB + "&kind=SITELINK&state=live" in out["calls"]
    d = out["el"]
    assert d["#acdT"]["text"] == "Sitelinks" and "different asset" in d["#acdS"]["text"] and "1 with a problem" in d["#acdS"]["text"]
    tools = d["#acdTools"]["html"]
    assert 'data-tab="assets"' in tools and "Campaigns (1 short)" in tools and 'data-state="live"' in tools and 'data-state="problem"' in tools and 'data-state="all"' in tools
    assert 'role="radio" class="pm-chip on" data-state="live"' in tools and 'role="radio" class="pm-chip on" data-tab="assets"' in tools
    body = d["#acdBody"]["html"]
    assert "Book now" in body and "Our team" in body and "Line one" in body and "ac-tgrid" in body and "Gone" not in body                       # live ones only


def test_a_kinds_problem_filter_and_campaign_tab_reach_the_server_and_the_report():
    steps = [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}}), fire("#acDrawer", "click", {"[data-state]": {"dataset": {"state": "problem"}}})]
    out = run({"responses": responses({"match": LIB + "&kind=SITELINK&state=problem", "body": D.library([DET], "SITELINK", "problem")}), "steps": steps})
    assert LIB + "&kind=SITELINK&state=problem" in out["calls"] and "Gone" in html(out, "#acdBody") and "Book now" not in html(out, "#acdBody")
    steps.append(fire("#acDrawer", "click", {"[data-tab]": {"dataset": {"tab": "campaigns"}}}))
    out = run({"responses": responses({"match": LIB + "&kind=SITELINK&state=problem", "body": D.library([DET], "SITELINK", "problem")}), "steps": steps})
    body = html(out, "#acdBody")
    assert body.index("Hudson_Broad") < body.index("PMax &lt;Spring&gt;") and "None live" in body and "At target" in body and 'data-camp="1:11"' in body
    assert "0</b> live of 4" in body and "6</b> live of 4" in body                                                      # the report's own counts, campaign by campaign
    assert 'id="acdFind"' not in html(out, "#acdTools")                                                                 # nothing to search on that tab


def test_choosing_a_campaign_in_the_drawer_closes_it_and_opens_that_campaign_in_the_list():
    steps = [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}}), fire("#acDrawer", "click", {"[data-tab]": {"dataset": {"tab": "campaigns"}}}),
             fire("#acDrawer", "click", {"[data-camp]": {"dataset": {"camp": "1:11"}}})]
    out = run({"responses": responses(), "steps": steps})
    assert out["el"]["#acDrawer"]["hidden"] is True and "-ac-lock" in out["log"]["classes"]
    assert out["el"]["#filter"]["value"] == "Hudson_Broad" and 'class="pm-chip on" data-s="all"' in out["#sevs"] and "ac-row open" in out["#list"] and "#listCard" in out["log"]["scrolled"]
    assert CAMP + "1%3A11" in out["calls"] and "PMax" not in out["#list"]
    gone = run({"responses": responses(), "steps": [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}}), fire("#acDrawer", "click", {"[data-camp]": {"dataset": {"camp": "1:99"}}})]})
    assert "not in this report any more" in gone["#msg"] and gone["el"]["#acDrawer"]["hidden"] is True


def test_an_account_wide_row_opens_only_what_is_linked_to_the_account():
    assert 'class="ac-ar" data-kind="CALLOUT"' in run({})["#acctAssets"] and 'role="button"' in run({})["#acctAssets"]
    out = run({"responses": [{"match": LIB + "&kind=CALLOUT&state=live&level=account", "body": lib_body(2)}], "steps": [fire("#acctAssets", "click", {".ac-ar": {"dataset": {"kind": "CALLOUT"}}})]})
    assert LIB + "&kind=CALLOUT&state=live&level=account" in out["calls"] and out["el"]["#acdT"]["text"] == "Callouts linked to the account" and html(out, "#acdBody").count("<article") == 2
    out = run({"responses": [{"match": LIB + "&kind=CALLOUT&state=live&level=account", "body": lib_body(1)}], "steps": [fire("#acctAssets", "keydown", {".ac-ar": {"dataset": {"kind": "CALLOUT"}}}, key="Enter")]})
    assert out["el"]["#acDrawer"]["hidden"] is False and out["log"]["prevented"] == 1


def test_a_drawer_shows_the_first_cards_then_more_and_says_when_the_server_cut_it_short():
    steps = [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}})]
    big = {"match": LIB + "&kind=SITELINK&state=live", "body": dict(lib_body(30), total=500)}
    out = run({"responses": [big] + responses(), "steps": steps})
    body = html(out, "#acdBody")
    assert body.count("<article") == 24 and "Showing the first 30 of 500" in body and out["el"]["#acdMore"]["hidden"] is False and "Show more (6 left)" in out["el"]["#acdMore"]["text"]
    out = run({"responses": [big] + responses(), "steps": steps + [{"button": "#acdMore"}]})
    assert html(out, "#acdBody").count("<article") == 30 and out["el"]["#acdMore"]["hidden"] is True
    small = run({"responses": responses(), "steps": steps})
    assert "Showing the first" not in html(small, "#acdBody") and small["el"]["#acdMore"]["hidden"] is True


def test_the_find_box_narrows_the_cards_by_what_they_say_and_who_uses_them():
    steps = [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}}), fire("#acDrawer", "input", {"self": {"id": "acdFind", "value": "callout 05"}})]
    out = run({"responses": [{"match": LIB + "&kind=SITELINK&state=live", "body": lib_body(30)}] + responses(), "steps": steps})
    assert html(out, "#acdBody").count("<article") == 1 and "Callout 05" in html(out, "#acdBody")
    steps[-1] = fire("#acDrawer", "input", {"self": {"id": "acdFind", "value": "hudson"}})
    out = run({"responses": [{"match": LIB + "&kind=SITELINK&state=live", "body": lib_body(30)}] + responses(), "steps": steps})
    assert html(out, "#acdBody").count("<article") == 24                                                           # matched on the campaign it names
    steps[-1] = fire("#acDrawer", "input", {"self": {"id": "acdFind", "value": "zzz"}})
    assert "Nothing matches." in html(run({"responses": [{"match": LIB + "&kind=SITELINK&state=live", "body": lib_body(30)}] + responses(), "steps": steps}), "#acdBody")
    steps[-1] = fire("#acDrawer", "input", {"self": {"id": "somethingelse", "value": "zzz"}})
    assert html(run({"responses": responses(), "steps": steps}), "#acdBody").count("<article") == 2                 # another input is not ours


def test_a_drawer_that_cannot_load_says_so_and_can_be_tried_again():
    steps = [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}})]
    out = run({"responses": [{"match": LIB, "status": 502, "body": {"error": "Google Ads did not answer."}}], "steps": steps})
    assert "Google Ads did not answer." in html(out, "#acdBody") and 'data-reload="1"' in html(out, "#acdBody")
    out = run({"responses": [{"match": LIB, "status": 502, "body": {"error": "Google Ads did not answer."}}], "steps": steps + [fire("#acDrawer", "click", {"[data-reload]": {}})]})
    assert sum(LIB in c for c in out["calls"]) == 2
    out = run({"reject": "/api/assets/library", "steps": steps})
    assert "The server could not be reached." in html(out, "#acdBody")
    out = run({"responses": [{"match": LIB, "status": 500, "body": {}}], "steps": steps})
    assert "(HTTP 500)" in html(out, "#acdBody")
    out = run({"responses": [{"match": LIB + "&kind=SITELINK", "body": lib_body(0)}], "steps": steps})
    assert "No assets of this kind in this view." in html(out, "#acdBody") and out["el"]["#acdExport"]["hidden"] is True
    out = run({"responses": [{"match": LIB, "body": {"unexpected": True}}], "steps": steps})                          # a good status with the wrong body is an error, not a crash
    assert "does not understand" in html(out, "#acdBody") and 'data-reload="1"' in html(out, "#acdBody")


def test_the_drawer_can_be_closed_and_escape_closes_a_picture_before_the_drawer():
    steps = [card("disapproved")]
    body = {"match": LIB + "&kind=&state=disapproved", "body": D.library([DET], None, "disapproved")}
    out = run({"responses": [body], "steps": steps + [fire("#acDrawer", "click", {"[data-close]": {}})]})
    assert out["el"]["#acDrawer"]["hidden"] is True and "-ac-lock" in out["log"]["classes"] and "opener" in out["log"]["focused"]
    out = run({"responses": [body], "steps": steps + [fire("document", "keydown", key="Escape")]})
    assert out["el"]["#acDrawer"]["hidden"] is True
    out = run({"responses": [body], "steps": [fire("document", "keydown", key="Escape")]})
    assert out["el"]["#acDrawer"]["hidden"] is True and out["el"]["#acZoom"]["hidden"] is True and "-ac-lock" not in out["log"]["classes"]      # nothing open: nothing happens
    zoom = fire("document", "click", {"[data-zoom]": {"dataset": {"zoom": GOOGLE_IMG, "cap": "Hero.png"}}})
    out = run({"responses": [body], "steps": steps + [zoom]})
    assert out["el"]["#acZoom"]["hidden"] is False and out["el"]["#acZimg"]["src"] == GOOGLE_IMG and out["el"]["#acZcap"]["text"] == "Hero.png" and out["log"]["prevented"] == 1
    out = run({"responses": [body], "steps": steps + [zoom, fire("document", "keydown", key="Escape")]})
    assert out["el"]["#acZoom"]["hidden"] is True and out["el"]["#acDrawer"]["hidden"] is False and out["el"]["#acZimg"]["src"] == ""        # the picture first
    out = run({"responses": [body], "steps": steps + [zoom, fire("#acZoom", "click", {}), ]})
    assert out["el"]["#acZoom"]["hidden"] is True
    out = run({"responses": [body], "steps": steps + [fire("document", "click", {"[data-zoom]": {"dataset": {"zoom": "https://evil.example.com/x.png", "cap": "x"}}})]})
    assert out["el"].get("#acZoom", {}).get("hidden") is not False and out["el"].get("#acZimg", {}).get("src", "") == ""                       # a foreign picture is never opened


def test_the_drawers_assets_can_be_downloaded_as_they_are_filtered():
    steps = [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}}), {"button": "#acdExport"}]
    out = run({"responses": responses(), "steps": steps})
    lines = out["csv"].lstrip("﻿").split("\r\n")
    assert lines[0] == "Account,Kind,Text or name,Lines,Link,Picture,State,Why,Used by campaigns,Asset groups,Impressions (30 days),Clicks"
    assert len(lines) == 3 and lines[1].startswith("Acme Dental,Sitelinks,Book now,Line one,https://x.com/1,,live,,1,0,") and out["download"]["name"] == "assets-SITELINK-Acme-Dental-2026-10-16.csv"
    narrowed = run({"responses": responses(), "steps": steps[:1] + [fire("#acDrawer", "input", {"self": {"id": "acdFind", "value": "our team"}}), steps[1]]})
    assert len(narrowed["csv"].lstrip("﻿").split("\r\n")) == 2
    commas = D.library([DET], "SITELINK", "live")
    commas["assets"][0]["x"] = 'Book, "now"'
    quoted = run({"responses": [{"match": LIB + "&kind=SITELINK&state=live", "body": commas}] + responses(), "steps": steps})
    assert '"Book, ""now"""' in quoted["csv"]


def test_words_from_google_are_escaped_in_the_drawer_and_the_campaign_detail():
    evil = "<img src=x onerror=alert(1)>"
    bad = D.library([DET], "SITELINK", "live")
    bad["assets"][0].update(x=evil, camps=[{"key": "1:11", "name": evil}], account=evil)
    out = run({"responses": [{"match": LIB + "&kind=SITELINK&state=live", "body": bad}], "steps": [fire("#cov", "click", {"[data-kind]": {"dataset": {"kind": "SITELINK"}}})]})
    assert "<img src=x" not in html(out, "#acdBody") and "&lt;img src=x" in html(out, "#acdBody")
    sec = json.loads(json.dumps(CAMP_11))
    sec["sections"][0]["tiles"][0]["x"] = evil
    sec["sections"][0]["label"] = evil
    out = run({"responses": [{"match": CAMP + "1%3A11", "body": sec}], "steps": [{"open": "1:11"}]})
    assert "<img src=x" not in out["#list"]


def test_opening_a_campaign_asks_for_its_assets_once_and_says_so_while_it_waits():
    out = run({"hang": "/api/assets/campaign", "steps": [{"open": "1:11"}]})
    assert "Loading what is linked…" in out["#list"] and out["calls"].count(CAMP + "1%3A11") == 1
    out = run({"responses": responses(), "steps": [{"open": "1:11"}, {"open": "1:11"}, {"open": "1:11"}]})
    assert out["calls"].count(CAMP + "1%3A11") == 1 and "Loading" not in out["#list"]                          # closed and opened again: not asked again
    out = run({"responses": responses(), "steps": [{"open": "1:11"}]})
    assert "ac-row open" in out["#list"] and "Loading" not in out["#list"]


def test_each_kind_of_asset_shows_what_is_linked_and_opens_onto_the_assets_themselves():
    out = run({"responses": responses(), "steps": [{"open": "1:11"}]})
    h = out["#list"]
    assert h.count("ac-pvb") >= 3 and "Book now" in h and "Our team" in h and 'class="ac-chip live img"' in h and "Open late" in h
    assert 'aria-expanded="false"' in h and "Show all 3" in h and "ac-tgrid" not in h and "Gone" in h
    out = run({"responses": responses(), "steps": [{"open": "1:11"}, fire("#list", "click", {".ac-pvb": {"dataset": {"kid": "c:1:11|SITELINK"}}})]})
    h = out["#list"]
    assert "ac-tgrid" in h and "Line one" in h and 'href="https://x.com/1"' in h and "Hide" in h and "Disapproved by Google" in h
    assert h.count("ac-tile ") == 3
    out = run({"responses": responses(), "steps": [{"open": "1:11"}, fire("#list", "click", {".ac-pvb": {"dataset": {"kid": "c:1:11|AD_IMAGE"}}})]})
    assert 'src="%s"' % GOOGLE_IMG in out["#list"] and 'data-zoom="%s"' % GOOGLE_IMG in out["#list"] and "896 × 640 px" in out["#list"]
    kid = {".ac-pvb": {"dataset": {"kid": "c:1:11|SITELINK"}}}
    out = run({"responses": responses(), "steps": [{"open": "1:11"}, fire("#list", "click", kid), fire("#list", "click", kid)]})
    assert "ac-tgrid" not in out["#list"]                                                                          # open, then shut
    other = {".ac-pvb": {"dataset": {"kid": "c:1:11|CALLOUT"}}}
    out = run({"responses": responses(), "steps": [{"open": "1:11"}, fire("#list", "click", kid), fire("#list", "click", other)]})
    assert out["#list"].count("ac-tgrid") == 2                                                                       # opening one leaves the other open


def test_an_asset_the_audit_does_not_judge_is_listed_after_the_judged_ones_marked_as_such():
    rep = json.loads(json.dumps(REPORT))
    det = json.loads(json.dumps(CAMP_11))
    det["sections"].append({"key": "LANDSCAPE_LOGO", "label": "Landscape logo", "level": "campaign", "live": 1, "tiles": [dict(D.content(PICTURE), st="live", lv="campaign", id="ll")]})
    out = run({"report": lambda u: {"status": 200, "body": rep}, "responses": [{"match": CAMP + "1%3A11", "body": det}], "steps": [{"open": "1:11"}]})
    assert "Landscape logo" in out["#list"] and "not judged" in out["#list"]


def test_a_performance_max_groups_and_a_display_ads_assets_open_too():
    head = {".ac-pvb": {"dataset": {"kid": "g:1:12:700|HEADLINE"}}}
    opened = fire("#list", "toggle", {"self": {"dataset": {"d": "g:1:12:700"}, "open": True}})
    steps = [{"click": ["#sevs", "s", "medium"]}, {"open": "1:12"}, opened, fire("#list", "click", head)]
    out = run({"responses": responses(), "steps": steps})
    h = out["#list"]
    assert 'data-d="g:1:12:700" open' in h and "Crowns" in h                                                         # a group left open stays open when the list is drawn again
    assert "Crowns today" in h and "12 characters" in h
    shut = fire("#list", "toggle", {"self": {"dataset": {"d": "g:1:12:700"}, "open": False}})
    out = run({"responses": responses(), "steps": steps[:3] + [shut, fire("#list", "click", head)]})
    assert 'data-d="g:1:12:700" open' not in out["#list"] and 'data-d="g:1:12:700"' in out["#list"]
    steps = [{"click": ["#sevs", "s", "low"]}, {"open": "1:14"}, fire("#list", "click", {".ac-pvb": {"dataset": {"kid": "a:1:14:9|HEADLINE"}}})]
    out = run({"responses": responses(), "steps": steps})
    assert "Hello" in out["#list"] and "Level 1" in out["#list"]
    unknown = run({"responses": responses(), "steps": [{"click": ["#sevs", "s", "medium"]}, {"open": "1:12"}, fire("#list", "toggle", {"self": {"dataset": {}, "open": True}})]})
    assert "Crowns" in unknown["#list"]                                                                              # a toggle that is not ours is ignored


def test_a_campaign_whose_assets_cannot_be_read_says_why_and_can_be_asked_again():
    err = {"match": "/api/assets/campaign", "status": 400, "body": {"error": "That campaign is not live in this account any more."}}
    out = run({"responses": [err], "steps": [{"open": "1:11"}]})
    assert "That campaign is not live in this account any more." in out["#list"] and 'data-retry="1"' in out["#list"] and "What to add" in out["#list"]       # the gaps still show
    retry = fire("#list", "click", {"[data-retry]": {}, ".ac-row": {"dataset": {"key": "1:11"}}})
    out = run({"responses": [err], "steps": [{"open": "1:11"}, retry]})
    assert out["calls"].count(CAMP + "1%3A11") == 2
    out = run({"responses": [{"match": "/api/assets/campaign", "status": 500, "body": {}}], "steps": [{"open": "1:11"}]})
    assert "(HTTP 500)" in out["#list"]
    out = run({"reject": "/api/assets/campaign", "steps": [{"open": "1:11"}]})
    assert "The server could not be reached." in out["#list"]
    assert "ac-row open" in out["#list"]


def test_a_fresh_report_forgets_what_was_open_and_closes_the_drawer():
    out = run({"responses": responses(), "steps": [{"open": "1:11"}, card("disapproved"), {"button": "#refreshBtn"}, {"open": "1:11"}]})
    assert out["el"]["#acDrawer"]["hidden"] is True and out["calls"].count(CAMP + "1%3A11") == 2


def test_the_scripts_are_loaded_in_the_right_order_and_the_page_has_its_drawer():
    html_ = open(TEMPLATE, encoding="utf-8").read()
    assert html_.index("asset_tiles.js") < html_.index("asset_coverage.js")
    assert 'id="acFloat"' in html_ and html_.index('id="acFloat"') < html_.index('id="acDrawer"') < html_.index('id="acZoom"')
    assert 'role="dialog"' in html_ and 'aria-modal="true"' in html_ and 'aria-labelledby="acdT"' in html_


def test_a_slow_answer_that_arrives_after_a_fresh_report_is_thrown_away():
    out = run({"responses": responses(), "hold": "/api/assets/campaign", "steps": [{"open": "1:11"}, {"button": "#refreshBtn"}, {"release": True}, {"open": "1:11"}]})
    assert out["calls"].count(CAMP + "1%3A11") == 2 and "Loading" not in out["#list"] and "Book now" in out["#list"]       # the old answer did not stand in for the new read


def test_a_second_click_while_the_assets_are_loading_does_not_ask_again():
    out = run({"hang": "/api/assets/campaign", "steps": [{"open": "1:11"}, {"open": "1:11"}, {"open": "1:11"}]})
    assert out["calls"].count(CAMP + "1%3A11") == 1


def test_the_assets_drawer_counts_assets_and_how_many_of_them_are_different():
    body = lib_body(2, state="expired")
    body.update(total=2, assetCount=5)
    out = run({"responses": [{"match": LIB + "&kind=&state=expired", "body": body}], "steps": [card("expired")]})
    assert out["el"]["#acdS"]["text"] == "5 assets, 2 different"
    body.update(total=2, assetCount=2)
    out = run({"responses": [{"match": LIB + "&kind=&state=expired", "body": body}], "steps": [card("expired")]})
    assert out["el"]["#acdS"]["text"] == "2 assets"
    body.update(total=1, assetCount=1)
    assert run({"responses": [{"match": LIB + "&kind=&state=expired", "body": body}], "steps": [card("expired")]})["el"]["#acdS"]["text"] == "1 asset"


def test_a_gap_carries_how_to_add_it_and_a_problem_asset_how_to_fix_it_folded_away_and_escaped():
    h = run({"steps": [{"open": "1:11"}]})["#list"]
    assert '<details class="ac-plan"><summary>How to add them</summary><ol><li>Open Google Ads, then Campaigns, click &quot;Hudson_Broad&quot;.</li><li>Click the +, choose &lt;Sitelink&gt;.</li></ol>' in h
    assert '<p class="ac-worth"><b>Worth</b> The campaign had 2,217 impressions in the last 30 days with none live.</p></details>' in h
    assert h.count("How to add them") == 1 and "Never shown" not in h                                               # the second gap has a plan with no steps: no empty box
    g = run({"steps": [{"click": ["#sevs", "s", "medium"]}, {"open": "1:12"}]})["#list"]
    assert '<summary>How to fix</summary><ol><li>Open Google Ads, then Tools, then Policy manager.</li><li>Edit the &lt;text&gt;.</li></ol></details>' in g and "<text>" not in g


def test_a_topic_google_gave_is_shown_as_words_and_an_odd_one_cannot_break_the_row():
    evil = json.loads(json.dumps(REPORT))
    evil["campaigns"][1]["groups"][0]["slots"][0]["bad"][0]["why"] = [{"t": "<b>BAD_TOPIC</b>"}]
    h = run({"report": lambda u: {"status": 200, "body": evil}, "steps": [{"click": ["#sevs", "s", "medium"]}, {"open": "1:12"}]})["#list"]
    assert "Tour (disapproved: &lt;b&gt;bad topic&lt;/b&gt;)" in h and "<b>bad" not in h


# ── The Platform switch: Meta audits the ads of each ad set, Both shows the two ───────────────────────────────────────────────────────────

def _meta_coverage():
    from tests import test_paid_meta_creative as M
    from tracker import paid_meta_creative_analyse as an
    r = M.raw([M.ad("1", adset="11", name="Lone <b>ad</b>"), M.ad("2", adset="12"), M.ad("3", adset="12"), M.ad("4", adset="12"), M.ad("5", status="DISAPPROVED")], [M.ins("1", spend="400", adset="11")])
    return json.loads(json.dumps(an.coverage(r, {"id": "act_1234567", "name": "Acme <b>Meta</b>", "currency": "USD", "platform": "meta"}, M.TODAY)))


def _reads(out):
    return [c for c in out["calls"] if "/assets/report" in c]


G_STUB = {"match": "/assets/report?account=1234567890", "body": REPORT}
M_STUB = lambda: {"match": "/assets/report?account=act_1234567", "body": _meta_coverage()}                                # noqa: E731


def test_meta_alone_reads_only_the_meta_account_and_draws_the_creative_coverage_view():
    out = run({"search": "?account=act_1234567", "responses": [G_STUB, M_STUB()]})
    assert _reads(out) == ["/p2/paid-marketing/api/assets/report?account=act_1234567"]
    html = out["#metaOut"]
    assert out["el"]["#metaOut"]["hidden"] is False and out["hidden"]["out"] is True and out["el"]["#skel"]["hidden"] is True and "mc-title" not in html
    assert "ad sets have 3 or more live ads" in html and "Acme &lt;b&gt;Meta&lt;/b&gt;" in html and "<b>Meta</b>" not in html and "Set 11" in html and "Disapproved by Meta" in html and "What to do:" in html


def test_google_alone_is_unchanged_and_nothing_is_asked_of_meta():
    out = run({"responses": [G_STUB, M_STUB()]})
    assert _reads(out) == ["/p2/paid-marketing/api/assets/report?account=1234567890"] and out["hidden"]["out"] is False and out["el"]["#metaOut"]["hidden"] is True


def test_both_asks_each_platform_and_draws_the_two_one_under_the_other():
    out = run({"search": "?account=1234567890%2Bact_1234567", "responses": [G_STUB, M_STUB()]})
    assert sorted(_reads(out)) == ["/p2/paid-marketing/api/assets/report?account=1234567890", "/p2/paid-marketing/api/assets/report?account=act_1234567"]
    assert out["hidden"]["out"] is False and "Hudson_Broad" in out["#list"] and out["el"]["#metaOut"]["hidden"] is False and '<h2 class="mc-title">Meta Ads</h2>' in out["#metaOut"]


def test_a_meta_failure_under_both_is_said_and_google_is_still_shown():
    meta = {"match": "/assets/report?account=act_1234567", "status": 502, "body": {"error": "Meta is rate limiting this app right now.", "kind": "meta"}}
    out = run({"search": "?account=1234567890%2Bact_1234567", "responses": [G_STUB, meta]})
    assert "Hudson_Broad" in out["#list"] and out["hidden"]["out"] is False and "Meta Ads could not be read" in out["#metaMsg"] and "rate limiting" in out["#metaMsg"] and out["el"]["#metaOut"]["hidden"] is True


def test_a_google_failure_under_both_is_said_and_meta_is_still_shown():
    google = {"match": "/assets/report?account=1234567890", "status": 502, "body": {"error": "Google Ads answered 403"}}
    out = run({"search": "?account=1234567890%2Bact_1234567", "responses": [google, M_STUB()]})
    assert "Google Ads could not be read" in out["#msg"] and "403" in out["#msg"] and out["el"]["#metaOut"]["hidden"] is False and "Set 11" in out["#metaOut"]


def test_switching_from_meta_to_google_hides_the_meta_view():
    out = run({"search": "?account=act_1234567", "responses": [G_STUB, M_STUB()], "steps": [{"scope": ["1234567890", "google"]}]})
    assert out["el"]["#metaOut"]["hidden"] is True and out["hidden"]["out"] is False and "Hudson_Broad" in out["#list"]


def test_the_template_has_the_meta_panels_and_loads_the_scripts_in_order():
    html = open(os.path.join(ROOT, "templates", "asset_coverage.html"), encoding="utf-8").read()
    assert 'id="metaOut"' in html and 'id="metaMsg"' in html
    assert html.index("pm_picker.js") < html.index("pm_platform.js") < html.index("pm_scope.js") < html.index("meta_creative.js") < html.index("asset_coverage.js")
