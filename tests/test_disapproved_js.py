"""Disapproved Ads Tracker: static/js/disapproved_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads Google Ads, decides which ad groups can show nothing and ranks every finding (tests/test_paid_disapproved*.py). The page only draws, so how a status, a policy name
and a sum read, which accounts a view shows and the CSV come from disapproved_data.js (executed here, not read), and disapproved.js is run for real: it loads the accounts, draws the
report the server built and says what went wrong in words. The HTML it writes is read back.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess

import pytest

from tests import test_paid_disapproved as T
from tests import test_paid_disapproved_extra as X
from tracker import paid_assets_plans as ap
from tracker import paid_brandspend_math as bm
from tracker import paid_disapproved as d

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "disapproved_data.js"), os.path.join(JS, "disapproved.js")
TEMPLATE = os.path.join(ROOT, "templates", "disapproved_ads.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [T.ACCT, T.ACCT2, {"id": "3333333333", "name": "Gamma <b>&</b> Co", "currency": "USD"}, {"id": "4444444444", "name": "Delta", "currency": "USD"}]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def reports():
    mp = pytest.MonkeyPatch()
    from tracker import paid_geo, paid_store
    saved = {}
    mp.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    mp.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    paid_geo.clear_cache()
    T.base.reset_state()
    T.base.forget("")
    keys = [w[0].isoformat() for w in T.WEEKS]
    ads = {T.ACCT["id"]: [T.api(ad="1", group="10", camp="Core <Search>"), T.api(ad="2", group="10", camp="Core <Search>"),
                          T.api(ad="3", group="11", camp="Brand", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED", ["Acme Rival"]),))],
           T.ACCT2["id"]: [T.api(ad="4", group="20", review="UNDER_APPEAL", topics=(("HEALTH_IN_PERSONALIZED_ADS", "LIMITED", []),))],
           "3333333333": [T.api(ad="5", group="30", approval="APPROVED_LIMITED", topics=(("PUNCTUATION_AND_SYMBOLS", "LIMITED", ["Best!!!"]),))],
           "4444444444": []}
    cover = {T.ACCT["id"]: [{"adGroup": {"id": "11"}}], T.ACCT2["id"]: [{"adGroup": {"id": "20"}}], "3333333333": [{"adGroup": {"id": "30"}}]}
    weekly = {T.ACCT["id"]: [T.week_row("10", keys[i], 100, 50_000_000) for i in range(4)]}
    assets = {T.ACCT2["id"]: [X.arow(aid="900"), X.arow(aid="901")]}
    asset_cover = {T.ACCT2["id"]: [X.arow(aid="a"), X.arow(aid="b"), X.arow(aid="c")]}
    keywords = {T.ACCT2["id"]: [X.krow()]}
    from tests.monitor_fakes import MemStore
    mon = MemStore()
    mon.issues_put([{"agent": "disapproved", "fingerprint": "group:1234567890:10", "status": "open", "firstSeen": "2026-10-18T07:00:00+00:00", "lastSeen": "2026-10-21T07:00:00+00:00", "reopened": 1, "resolvedAt": None},
                    {"agent": "disapproved", "fingerprint": "topic:1234567890:DESTINATION_NOT_WORKING", "status": "open", "firstSeen": "2026-10-19T07:00:00+00:00", "lastSeen": "2026-10-21T07:00:00+00:00", "reopened": 0, "resolvedAt": None},
                    {"agent": "disapproved", "fingerprint": "old:1", "status": "resolved", "firstSeen": "2026-10-10T07:00:00+00:00", "lastSeen": "2026-10-12T07:00:00+00:00", "reopened": 0, "resolvedAt": "2026-10-12T07:00:00+00:00"}])
    asof = dt.datetime(2026, 10, 21, 7, 0, tzinfo=dt.timezone.utc)

    def go(account, **kw):
        paid_geo.clear_cache()
        T.base.forget("")
        return d.report_for(account, accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [{"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in ACCOUNTS]}],
                            search_fn=T.searcher(ads, cover=cover, weekly=weekly, assets=assets, asset_cover=asset_cover, keywords=keywords), today=T.TODAY, mon=mon, asof=asof, **kw)
    out = {"mcc": json.loads(json.dumps(go(GROUP))), "one": json.loads(json.dumps(go("1234567890"))), "clear": json.loads(json.dumps(go("4444444444")))}
    mp.undo()
    paid_geo.clear_cache()
    T.base.forget("")
    return out


# ── words and shapes ────────────────────────────────────────────────────────

def test_every_status_and_state_the_server_can_send_has_a_label_and_a_tone_and_the_page_knows_them_all(reports):
    seen = {a["status"] for a in reports["mcc"]["accounts"]}
    assert seen == {"stopped", "disapproved", "limited", "clear"}
    for s in seen:
        assert run("X.STATUS[%s]" % json.dumps(s)) is not None
    states = {i["state"] for a in reports["mcc"]["accounts"] for i in a["items"]}
    assert states == {"dead", "covered", "limited", "keyword"}
    for s in states | {"unchecked"}:
        assert run("X.STATE[%s]" % json.dumps(s)) is not None
    levels = {f["level"] for a in reports["mcc"]["accounts"] for f in a["findings"]} | {s["level"] for a in reports["mcc"]["accounts"] for s in a["plan"]["steps"]}
    for lv in levels:
        assert run("X.LEVEL[%s]" % json.dumps(lv)) is not None
    assert run('X.statusInfo("stopped")') == {"label": "Ad groups stopped", "tone": "bad"} and run('X.statusInfo("zzz")') == {"label": "zzz", "tone": "unk"}
    assert run('X.stateInfo("zzz")') == {"label": "zzz", "tone": "unk"} and run('X.levelInfo("zzz")') == {"label": "zzz", "tone": "unk"}


def test_approvals_and_reviews_read_in_words_and_unknown_ones_are_not_invented():
    assert [run("X.approvalText(%s)" % json.dumps(a)) for a in ("DISAPPROVED", "APPROVED_LIMITED", "AREA_OF_INTEREST_ONLY", "ZZZ", None)] == ["Disapproved", "Limited", "Area of interest only", "ZZZ", ""]
    assert [run("X.reviewText(%s)" % json.dumps(r)) for r in ("UNDER_APPEAL", "REVIEW_IN_PROGRESS", "REVIEWED", "")] == ["Under appeal", "Being reviewed", "", ""]


@pytest.mark.parametrize("t", ["DESTINATION_NOT_WORKING", "TRADEMARKS_IN_AD_TEXT", "HEALTH_IN_PERSONALIZED_ADS", "x", "A__B", " spaced_out ", "", None])
def test_a_policy_name_reads_exactly_as_the_server_writes_it(t):
    assert run("X.topicName(%s)" % json.dumps(t)) == ap.topic_name(t)


def test_a_policy_that_was_not_named_says_so():
    assert run("X.topicLabel('NOT_NAMED')") == "Reason not named" and run("X.topicLabel('PROHIBITED_X')") == "Prohibited x"


@pytest.mark.parametrize("v,cur", [(0, "USD"), (12.5, "USD"), (99.999, "USD"), (100, "USD"), (203, "USD"), (1234.5, "USD"), (301, "CHF"), (55, "INR")])
def test_money_reads_exactly_as_the_server_writes_it(v, cur):
    assert run("X.money(%s,%s)" % (json.dumps(v), json.dumps(cur))) == bm.money(v, cur)
    assert run("X.money(null,'USD')") == "n/a" and run("X.int(1234567)") == "1,234,567" and run("X.int(null)") == "n/a"


def test_the_tiles_are_the_servers_numbers_in_words(reports):
    t = run("X.tiles(R)", R=reports["mcc"])
    assert [x[0] for x in t] == ["Accounts read", "Need a look", "Ad groups stopped", "Ads disapproved", "Ads limited"]
    assert t[0][1] == "4" and "last 5 whole weeks" in t[0][2]
    assert t[1][1] == "2" and t[1][3] == "warn" and "1 with an ad group stopped" in t[1][2]
    assert t[2][1] == "1" and t[2][3] == "warn" and "about 100 clicks a week not coming" in t[2][2]
    assert t[3][1] == "6" and t[3][2] == "1 keyword too, 1 under appeal or in review" and t[4][1] == "1"
    quiet = run("X.tiles(R)", R=reports["clear"])
    assert quiet[1][1] == "0" and quiet[1][3] == "good" and "no ad is disapproved" in quiet[1][2] and quiet[2][3] == "good" and "none has all its ads disapproved" in quiet[2][2] and "none waiting on Google" in quiet[3][2]
    nolost = json.loads(json.dumps(reports["mcc"]))
    nolost["summary"]["lostClicks"] = 0
    assert run("X.tiles(R)", R=nolost)[2][2] == "every ad in them is disapproved"


def test_the_views_filter_the_accounts_and_open_on_the_first_with_something_in_it(reports):
    r = reports["mcc"]
    assert run("X.viewCount(R,'all')", R=r) == 4 and run("X.viewCount(R,'attention')", R=r) == 2 and run("X.firstView(R)", R=r) == "attention"
    assert [a["status"] for a in run("X.shown(R,'attention')", R=r)] == ["stopped", "disapproved"]
    assert run("X.firstView(R)", R=reports["clear"]) == "all" and run("X.needsLook(A)", A={"status": "limited"}) is False and run("X.needsLook(A)", A={"status": "clear"}) is False


def test_the_address_carries_the_account_and_refresh():
    assert run("X.query('1234567890',false)") == "account=1234567890" and run("X.query('mcc-1',true)") == "account=mcc-1&refresh=1" and run("X.query('a b&c',false)") == "account=a%20b%26c"


def test_the_csv_has_a_line_per_flagged_item_and_a_line_for_an_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    head = run("X.HEAD")
    assert rows[0] == ["Disapproved Ads Tracker", "Group"] and rows[1] == ["Weeks", "2026-09-14 to 2026-10-18"] and rows[3] == head and head[5] == "Kind" and head[16] == "Stopped for days"
    body = rows[4:]
    assert all(len(r) == len(head) for r in body) and head[-1] == "Fix plan" and len(body) == 9              # 3 + 1 + 1 ads, two assets, a keyword and one line for the clear account
    first = next(r for r in body if r[4] == "1")
    assert first[:4] == ["Acme Dental", "Ad groups stopped", "Core <Search>", "Group 10"] and first[5] == "Ad" and first[6] == "responsive search ad" and first[7] == "Disapproved" and first[8] == "" and first[9] == "No approved ad in the ad group"
    assert first[10] == "Destination not working" and first[11] == "Click here!!!" and first[12] == "Dentist near you | Book today" and first[13] == "https://example.com/a" and first[14] == 100 and first[15] == 0 and first[16] == 3
    assert next(r for r in body if r[4] == "4")[8] == "Under appeal" and next(r for r in body if r[4] == "5")[9] == "Limited, still shows" and next(r for r in body if r[4] == "3")[14] == "" and next(r for r in body if r[4] == "3")[16] == ""
    asset = next(r for r in body if r[4].startswith("asset:"))
    assert asset[5] == "Performance Max asset" and asset[3] == "Summer (headlines)" and asset[12] == "Free whitening!!!"
    kw = next(r for r in body if r[4].startswith("kw:"))
    assert kw[5] == "Keyword" and kw[12] == "[cheap implants]" and kw[9] == "Keyword cannot show"
    clear = next(r for r in body if r[0] == "Delta")
    assert clear[1] == "All clear" and clear[2] == "(no flagged ad)" and clear[-1] == "" and len(clear) == len(head)
    acme = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Acme Dental")
    assert first[-1].startswith("1. Get the ad groups that cannot show an ad running again | 2. ") and first[-1] == run("X.planTitles(A)", A=acme)
    assert run("X.planTitles(A)", A={"plan": None}) == "" and run("X.planTitles(A)", A={}) == ""


def test_what_shows_across_accounts_and_over_time_is_said_in_sentences():
    r = {"patterns": [{"topic": "T", "label": "Trademarks in ad text", "accounts": ["A", "B", "C", "D", "E", "F"], "ads": 9}, {"topic": "U", "label": "Destination mismatch", "accounts": ["A", "B"], "ads": 2}], "fixTimes": {"fixed": 1, "median": 1, "longest": 1}}
    got = run("X.insights(R)", R=r)
    assert [g["kind"] for g in got] == ["pattern", "pattern", "fixes"]
    assert got[0]["text"].startswith("Trademarks in ad text flags ads in 6 accounts (A, B, C, D and 2 more), 9 ads in all. The same policy in many accounts is usually one cause") and got[1]["text"].startswith("Destination mismatch flags ads in 2 accounts (A, B), 2 ads in all.")
    assert got[2]["text"] == "Of the 1 problem fixed since the monitor began, the middle one took 1 day and the longest 1."
    assert run("X.insights(R)", R={"patterns": [], "fixTimes": {"fixed": 3, "median": 2, "longest": 7}})[0]["text"] == "Of the 3 problems fixed since the monitor began, the middle one took 2 days and the longest 7."
    assert run("X.insights(R)", R={}) == [] and run("X.insights(R)", R={"patterns": [], "fixTimes": None}) == []


def test_five_accounts_say_one_more_and_four_say_no_more():
    five = run("X.insights(R)", R={"patterns": [{"label": "P", "accounts": list("ABCDE"), "ads": 5}]})[0]["text"]
    four = run("X.insights(R)", R={"patterns": [{"label": "P", "accounts": list("ABCD"), "ads": 4}]})[0]["text"]
    assert "(A, B, C, D and 1 more)" in five and "(A, B, C, D)" in four and "more)" not in four


def test_each_kind_of_flagged_thing_has_its_name():
    assert [run("X.kindText(%s)" % json.dumps(k)) for k in ("ad", "asset", "keyword", "meta", None, "zzz")] == ["Ad", "Performance Max asset", "Keyword", "Meta ad", "Ad", "Ad"]


# ── the page script ─────────────────────────────────────────────────────────

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const paths = JSON.parse(process.argv[1]), sc = JSON.parse(process.argv[2]);
const els = {}, calls = [], hist = [], store = {}, downloads = [], held = [], panels = [];
function el(sel) {
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", value: (sc.values || {})[sel] || "", hidden: false, href: "", handlers: {},
    addEventListener(ev, fn) { this.handlers[ev] = fn; }, setAttribute() {}, getAttribute() { return null; }, appendChild() {}, className: "" };
  return els[sel];
}
const doc = { querySelector: s => el(s), createElement: () => { const e = el("a" + downloads.length); e.click = () => downloads.push({ href: e.href, name: e.download }); e.remove = () => {}; return e; },
  body: { appendChild() {} }, addEventListener() {} };
const responses = (sc.responses || []).map(r => Object.assign({ n: 0 }, r));
function respond(url) {
  calls.push(url);
  const hits = responses.filter(r => url.includes(r.match));
  if (!hits.length) return { status: 404, body: { error: "no stub for " + url } };
  const r = hits[0], i = Array.isArray(r.sequence) ? Math.min(r.n++, r.sequence.length - 1) : 0;
  const body = JSON.parse(JSON.stringify(Array.isArray(r.sequence) ? r.sequence[i] : r.body));
  const answer = { status: (r.statuses ? r.statuses[i] : r.status) || 200, body };
  if (r.hold && !r.released) return new Promise(res => held.push(() => { r.released = true; res(answer); }));
  return answer;
}
let picker = null; const sets = [], setsMeta = [], pickers = [], switchers = [], platformsSet = [], googleOnlies = [];
const win = { MonitorPanel: { mount: (host, o) => panels.push(o) },
  PMPicker: (host, o) => { const n = pickers.length, p = { onChange: o.onChange, set(items, v, dis) { (n === 0 ? sets : setsMeta).push({ items, v, dis }); } }; pickers.push(p); if (n === 0) picker = p; return p; },
  PMPlatform: { googleOnly: (host, o) => { googleOnlies.push(o); return { value: () => "google", set() {}, note: {} }; }, switcher: (host, o) => { switchers.push(o); let cur = o.value; return { set(p) { cur = p; platformsSet.push(p); }, value: () => cur, saved: () => "" }; },
    counterpart: (a, others) => { const n = x => String(x.name || "").toLowerCase().replace(/\(.*?\)/g, "").replace(/[^a-z0-9]/g, ""); const m = others.filter(o => o.currency === a.currency && n(o) === n(a)); return m.length === 1 ? m[0] : null; } } };
const ctx = { window: win, document: doc, history: { replaceState(a, b, u) { hist.push(u); } }, location: { search: sc.search || "", pathname: "/p2/paid-marketing/disapproved-ads" },
  localStorage: { getItem: k => (k in store ? store[k] : (sc.stored || {})[k] || null), setItem: (k, v) => { store[k] = v; } },
  URLSearchParams, URL: { createObjectURL: b => { downloads.blob = b; return "blob:x"; }, revokeObjectURL() {} }, Blob: class { constructor(p) { this.text = p.join(""); } },
  setTimeout: fn => fn(), Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, console, isNaN, parseInt, parseFloat,
  fetch: url => (sc.reject && url.includes(sc.reject) ? (calls.push(url), Promise.reject(new Error("down"))) :
    Promise.resolve(respond(url)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) }))) };
ctx.window.document = doc; win.fetch = ctx.fetch; win.localStorage = ctx.localStorage;
vm.createContext(ctx);
for (const p of paths) vm.runInContext(fs.readFileSync(p, "utf8"), ctx);
const tick = () => new Promise(r => setImmediate(r));
function target(spec) {
  spec = spec || {};
  const make = s => ({ dataset: s.dataset || {}, closest: q => (spec.closest && spec.closest[q] ? make(spec.closest[q]) : null) });
  return make(spec);
}
(async () => {
  for (let i = 0; i < 6; i++) await tick();
  for (const step of sc.steps || []) {
    if (step.pick) picker.onChange(step.pick);
    if (step.pickMeta) pickers[1].onChange(step.pickMeta);
    if (step.platform) switchers[0].onChange(step.platform);
    if (step.click) el(step.click).handlers.click({});
    if (step.release) held.splice(0).forEach(f => f());
    if (step.fire) { const [sel, closest] = step.fire; el(sel).handlers.click({ target: target({ closest }) }); }
    for (let i = 0; i < 6; i++) await tick();
  }
  const out = { calls, panels, hist, store, sets, setsMeta, platformsSet, switchers: switchers.length, googleOnlies, downloads: downloads.map(d => ({ name: d.name })), blob: downloads.blob ? downloads.blob.text : "", el: {} };
  for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, value: els[k].value };
  process.stdout.write(JSON.stringify(out));
})();
"""
SCOPE = os.path.join(JS, "pm_scope.js")
SCRIPTS = [SCOPE, DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}


def page(scenario):
    out = subprocess.run(["node", "-e", HARNESS, json.dumps(SCRIPTS), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(report, steps=None, **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/disapproved/report", "body": report}], "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("disapproved_data.js") < html.index("disapproved.js")
    assert "—" not in html and "–" not in html


def test_the_accounts_are_loaded_into_the_picker_with_the_mcc_first_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and items[1]["sub"] == "4 accounts checked together" and "MCC" in items[1]["badge"] and len(items) == 6
    assert not any("/disapproved/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports["one"])
    assert [c for c in out["calls"] if "/disapproved/report" in c] == ["/p2/paid-marketing/api/disapproved/report?account=1234567890"]
    assert out["store"]["da_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/disapproved-ads?account=1234567890" and out["el"]["#out"]["hidden"] is False


def test_the_whole_report_is_drawn_most_serious_first_with_markup_escaped(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}])
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Accounts read", "Need a look", "Ad groups stopped", "Ads disapproved", "Ads limited"):
        assert text in kp, text
    assert lst.index("Acme Dental") < lst.index("Beta Clinic") and "Gamma" not in lst and "Delta" not in lst              # "Needs a look" holds the two that need one
    assert "bi-pill bad" in lst and "Ad groups stopped" in lst and "bi-pill over" in lst and "Ads disapproved" in lst and "Destination not working" in lst and "1 under appeal" in lst
    assert '<span class="bi-n">1</span><span class="bs-sub">ad group with nothing to show · the oldest for 3 days</span>' in lst and '<span class="bi-n">3</span><span class="bs-sub">ads disapproved, others cover</span>' in lst
    views = out["el"]["#views"]["html"]
    assert "Needs a look <span class=\"n\">2</span>" in views and "All <span class=\"n\">4</span>" in views and 'class="pm-chip on" data-v="attention"' in views
    assert "MCC · 4 accounts checked together" in out["el"]["#fresh"]["html"] and "most serious first" in out["el"]["#listNote"]["text"]


def test_all_accounts_shows_every_one_and_names_with_markup_are_escaped(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])
    lst = out["el"]["#list"]["html"]
    assert "Gamma &lt;b&gt;&amp;&lt;/b&gt; Co" in lst and "<b>&</b>" not in lst
    for name in ("Acme Dental", "Beta Clinic", "Delta"):
        assert name in lst
    for label in ("Ads limited", "All clear", "no flagged ad"):
        assert label in lst, label
    assert "4 accounts, most serious first" in out["el"]["#listNote"]["text"]


def test_a_single_account_opens_on_its_policies_findings_plan_stopped_groups_and_ads(reports):
    out = opened(reports["one"])
    lst = out["el"]["#list"]["html"]
    acme = reports["one"]["accounts"][0]
    assert 'class="bi-row stopped open"' in lst and "<h4>By policy</h4>" in lst and "Destination not working" in lst and "Trademarks in ad text" in lst and "prohibited" in lst
    f = acme["findings"][0]
    assert esc(f["title"]) in lst and esc(f["nextStep"]) in lst and "1 ad group cannot show any ad" in lst
    assert "<h4>Ad groups with nothing to show</h4>" in lst and "Group 10" in lst and "Core &lt;Search&gt;" in lst and "<td class=\"r\">100</td>" in lst
    assert "<h4>Flagged ads</h4>" in lst and "Dentist near you | Book today" in lst and "https://example.com/a" in lst and '&quot;Click here!!!&quot;' in lst and "Acme Rival" in lst
    assert "No approved ad in the ad group" in lst and "Another ad is showing" in lst
    plan = acme["plan"]
    assert lst.count("<details") == len(plan["steps"]) and lst.count("<details open>") == 1 and "Why this applies." in lst and "What to do" in lst and "<b>Worth.</b>" in lst
    for s in plan["steps"]:
        assert esc(s["title"]) in lst and ('<span class="bi-sn">%d</span>' % s["n"]) in lst
        for h in s["how"]:
            assert esc(h) in lst
    assert esc(plan["note"]) in lst and "Applies to:" in lst and "Core &lt;Search&gt;" in lst


def test_a_step_that_holds_markup_is_escaped_and_a_clear_account_draws_no_plan_and_says_so(reports):
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["plan"]["steps"][0]["title"] = "<img src=x onerror=1>"
    risky["accounts"][0]["plan"]["steps"][0]["how"][0] = "<b>open</b> it"
    risky["accounts"][0]["plan"]["steps"][0]["worth"] = "worth <3"
    risky["accounts"][0]["items"][0]["headlines"] = ["<script>x</script>"]
    lst = opened(risky)["el"]["#list"]["html"]
    assert "&lt;img src=x onerror=1&gt;" in lst and "<img" not in lst and "&lt;b&gt;open&lt;/b&gt; it" in lst and "worth &lt;3" in lst and "&lt;script&gt;x&lt;/script&gt;" in lst and "<script>" not in lst
    clear = opened(reports["clear"], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/disapproved/report", "body": reports["clear"]}])["el"]["#list"]["html"]
    assert "Nothing to flag: no enabled ad in this account is disapproved or limited." in clear and "bi-plan" not in clear and "<h4>Flagged ads" not in clear and "<h4>By policy" not in clear


def test_an_account_with_no_stopped_ad_group_draws_no_stopped_table(reports):
    beta = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Beta Clinic")
    lst = opened(dict(reports["mcc"], accounts=[beta]), [{"pick": GROUP}])["el"]["#list"]["html"]
    assert 'class="bi-row disapproved open"' in lst and "<h4>Flagged ads" in lst and "Ad groups with nothing to show</h4>" not in lst and "Under appeal" in lst


def test_a_long_list_of_flagged_ads_shows_the_first_forty_and_points_to_the_csv(reports):
    big = json.loads(json.dumps(reports["one"]))
    one = big["accounts"][0]["items"][0]
    big["accounts"][0]["items"] = [dict(one, id=str(1000 + i)) for i in range(55)]
    lst = opened(big)["el"]["#list"]["html"]
    assert "the first 40 of 55; the CSV has all" in lst and lst.count("<tr><td>Dentist near you") == 40


def test_an_ad_with_no_text_and_no_policy_named_still_draws(reports):
    r = json.loads(json.dumps(reports["one"]))
    ad = r["accounts"][0]["items"][0]
    ad.update(headlines=[], topics=[], url="", type="CALL_ONLY_AD", review="UNDER_APPEAL")
    lst = opened(r)["el"]["#list"]["html"]
    assert "call only ad" in lst and "not named" in lst and "Under appeal" in lst


def test_a_row_opens_and_closes_on_a_click(reports):
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}]
    out = opened(reports["mcc"], steps)
    assert 'class="bi-row stopped open"' in out["el"]["#list"]["html"]
    closed = opened(reports["mcc"], steps + steps[1:])
    assert 'class="bi-row stopped open"' not in closed["el"]["#list"]["html"]


def test_refresh_reads_again_and_a_cooldown_is_said_with_the_servers_words(reports):
    out = opened(reports["one"], [{"click": "#refreshBtn"}])
    assert [c for c in out["calls"] if "/disapproved/report" in c][-1].endswith("account=1234567890&refresh=1")
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/disapproved/report", "sequence": [reports["one"], {"error": "Refreshed a moment ago. Try again in 90 seconds.", "kind": "cooldown", "retryIn": 90}], "statuses": [200, 429]}],
          "steps": [{"pick": "1234567890"}, {"click": "#refreshBtn"}]}
    c = page(sc)
    assert "Refreshed a moment ago. Try again in 90 seconds." in c["el"]["#msg"]["html"] and "pm-note" in c["el"]["#msg"]["html"] and c["el"]["#out"]["hidden"] is False


def test_failures_are_said_in_words(reports):
    quota = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/disapproved/report", "status": 429, "body": {"error": "The daily limit is used up.", "kind": "quota"}}], "steps": [{"pick": "1234567890"}]})
    assert "The daily limit is used up. The platform limits how fast" in quota["el"]["#msg"]["html"] and quota["el"]["#empty"]["hidden"] is False
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "reject": "/disapproved/report", "steps": [{"pick": "1234567890"}]})
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"]
    noacc = page({"responses": [{"match": "/segments/accounts", "status": 502, "body": {"error": "Google Ads is not set up."}}]})
    assert "Google Ads is not set up." in noacc["el"]["#msg"]["html"]


def test_a_copy_saved_while_google_was_not_answering_says_so_and_warnings_are_shown(reports):
    stale = dict(reports["one"], stale="2026-10-20T10:00:00+00:00", warnings=["Whether other ads cover the flagged ones could not be read (no)."])
    out = opened(stale)
    assert "Google Ads is not answering, so this is the copy saved" in out["el"]["#fresh"]["html"] and "could not be read (no)." in out["el"]["#msg"]["html"]


def test_the_footnote_says_what_stopped_means_and_that_the_hints_are_not_a_ruling(reports):
    foot = opened(reports["one"])["el"]["#foot"]["html"]
    assert "stopped</b> when every enabled ad in it is disapproved" in foot and "not a ruling" in foot and "In Performance Max the unit is a field of an asset group" in foot and "Merchant Center" in foot and "no age" in foot
    assert "—" not in foot and "–" not in foot


def test_the_csv_downloads_with_the_account_and_end_week_in_its_name_and_a_formula_cell_is_defused(reports):
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["items"][0]["headlines"] = ["=HYPERLINK(1)"]
    out = opened(risky, [{"click": "#exportBtn"}])
    assert out["downloads"] == [{"name": "disapproved-ads-Acme-Dental-2026-10-18.csv"}] and out["blob"].startswith("﻿")
    lines = out["blob"].lstrip("﻿").split("\r\n")
    assert lines[0] == "Disapproved Ads Tracker,Acme Dental" and "'=HYPERLINK(1)" in out["blob"] and lines[3].startswith("Account,Status,Campaign")


def test_nothing_the_page_writes_has_a_dash_or_an_unfilled_slot(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}] + [
        {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": a["id"]}}}]} for a in reports["mcc"]["accounts"]])
    text = re.sub(r"<[^>]+>", " ", out["el"]["#list"]["html"] + out["el"]["#kpis"]["html"] + out["el"]["#foot"]["html"])
    assert "—" not in text and "–" not in text and "undefined" not in text and "NaN" not in text and "[object" not in text and "null" not in text


# ── Age, shared causes, keywords, assets, Meta and the panel ────────────────

def test_the_page_says_what_shows_across_accounts_and_over_time(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}])
    ins = out["el"]["#insights"]["html"]
    rep = reports["mcc"]
    assert rep["patterns"][0]["label"] == "Punctuation and symbols" and sorted(rep["patterns"][0]["accounts"]) == ["Beta Clinic", "Gamma <b>&</b> Co"]
    assert "Across the accounts" in ins and "Punctuation and symbols flags ads in 2 accounts (" in ins and "Gamma &lt;b&gt;&amp;&lt;/b&gt; Co" in ins and "<b>&</b>" not in ins
    assert "Of the 1 problem fixed since the monitor began, the middle one took 2 days and the longest 2." in ins
    quiet = opened(reports["clear"])["el"]["#insights"]["html"]
    assert "fixed since the monitor began" in quiet and "flags ads in" not in quiet                   # one account has no pattern across accounts, the fix times are the monitor's
    none = json.loads(json.dumps(reports["clear"]))
    none["fixTimes"] = None
    assert opened(none)["el"]["#insights"]["html"] == ""


def test_a_stopped_group_and_a_policy_show_how_long_they_have_been_open_and_that_they_came_back(reports):
    lst = opened(reports["one"])["el"]["#list"]["html"]
    assert "ad group with nothing to show · the oldest for 3 days" in lst
    assert '<span class="bi-mk">came back 1x</span>' in lst and "<th class=\"r\">Stopped for</th><th class=\"r\">Lost since</th>" in lst and ">3 days<" in lst and "about 43 clicks" in lst
    assert "<th class=\"r\">Flagged for</th>" in lst and ">2 days<" in lst
    assert "It has been flagged for 2 days." in lst and "The longest has been stopped for 3 days (about 43 clicks lost since)." in lst


def test_a_performance_max_asset_a_keyword_and_a_meta_badge_are_drawn(reports):
    beta = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Beta Clinic")
    out = opened(dict(reports["mcc"], accounts=[beta]), [{"pick": GROUP}])
    lst = out["el"]["#list"]["html"]
    assert 'class="bi-mk">Performance Max asset</span> Free whitening!!!' in lst and "<h4>Disapproved keywords <span class=\"dim\">(1)</span></h4>" in lst and "[cheap implants]" in lst and "Healthcare and medicines" in lst
    assert "3 ads and assets are disapproved" in lst and "Each has another approved ad or asset in its ad group, so the ad group still shows." in lst
    meta = json.loads(json.dumps(beta))
    meta["platform"] = "meta"
    assert 'Beta Clinic <span class="bi-mk">Meta</span>' in opened(dict(reports["mcc"], accounts=[meta]), [{"pick": GROUP}])["el"]["#list"]["html"]


def test_what_the_flagged_ads_share_is_drawn_as_a_table(reports):
    r = json.loads(json.dumps(reports["one"]))
    r["accounts"][0]["suspects"] = [{"kind": "headline", "text": "Book today <now>", "ads": 6, "of": 8}, {"kind": "url", "text": "https://example.com/a", "ads": 4, "of": 8}, {"kind": "evidence", "text": "Free!!!", "ads": 3, "of": 8}]
    lst = opened(r)["el"]["#list"]["html"]
    assert "What the flagged ads have in common" in lst and "<td>Headline</td><td>Book today &lt;now&gt;</td><td class=\"r\">6 of 8 flagged ads</td>" in lst and "<td>Page</td>" in lst and "<td>Text Google points at</td>" in lst
    plain = json.loads(json.dumps(reports["one"]))
    plain["accounts"][0]["suspects"] = []
    assert "What the flagged ads have in common" not in opened(plain)["el"]["#list"]["html"]


def test_meta_ad_accounts_are_in_their_own_picker_and_a_meta_list_that_fails_costs_nothing(reports):
    meta = {"accounts": [{"id": "act_1234567", "name": "Meta Co", "currency": "USD", "platform": "meta"}, {"id": "other", "name": "Not Meta", "platform": "google"}]}
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/gads/accounts", "body": meta}]})
    assert [i["label"] for i in out["sets"][-1]["items"]][-1] != "Meta Co" and len(out["sets"][-1]["items"]) == 6
    items = out["setsMeta"][-1]["items"]
    assert [(i["label"], i["value"]) for i in items] == [("Choose an account", ""), ("Meta Co", "act_1234567")] and out["switchers"] == 1
    assert [c for c in out["calls"] if "gads/accounts" in c] == ["/p2/paid-marketing/api/gads/accounts?platform=meta"]
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "reject": "/gads/accounts"})
    assert len(down["sets"][-1]["items"]) == 6 and [i["value"] for i in down["setsMeta"][-1]["items"]] == [""]
    bad = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/gads/accounts", "status": 409, "body": {"error": "Meta is not connected"}}]})
    assert len(bad["sets"][-1]["items"]) == 6 and [i["value"] for i in bad["setsMeta"][-1]["items"]] == [""]


def test_the_alert_panel_is_mounted_for_this_monitor_and_has_a_place_in_the_template(reports):
    assert opened(reports["one"])["panels"] == [{"agent": "disapproved", "label": "Disapproved Ads Tracker"}]
    html = open(TEMPLATE, encoding="utf-8").read()
    assert 'id="insights"' in html and html.index('id="monPanel"') > html.index('id="out"') and html.index("monitor_panel.js") < html.index("disapproved_data.js")


def test_a_policy_that_came_back_says_so_in_the_table_and_an_account_without_keywords_has_no_keyword_table(reports):
    r = json.loads(json.dumps(reports["one"]))
    r["accounts"][0]["topics"][0]["reopened"] = 2
    lst = opened(r)["el"]["#list"]["html"]
    assert '<span class="bi-mk">came back 2x</span>' in lst and "Disapproved keywords" not in lst


def test_a_keyword_is_in_the_keyword_table_and_not_among_the_flagged_ads(reports):
    beta = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Beta Clinic")
    lst = opened(dict(reports["mcc"], accounts=[beta]), [{"pick": GROUP}])["el"]["#list"]["html"]
    assert lst.count("<td>[cheap implants]</td>") == 1 and "Keyword</span> [cheap implants]" not in lst and "Disapproved keywords" in lst
