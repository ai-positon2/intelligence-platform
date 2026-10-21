"""Brand Spend Alert: static/js/brand_spend_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads Google Ads, does every sum and ranks every finding (tests/test_paid_brandspend*.py). The page only draws, so how a sum of money, a change and a status read, the
shape of the weekly bars with the usual band behind them and the CSV come from brand_spend_data.js (executed here, not read), and brand_spend.js is run for real: it loads the
accounts, sends the limit and the campaigns the person marks, draws the report the server built and says what went wrong in words. The HTML it writes is read back.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess

import pytest

from tests import test_paid_brandspend as T
from tracker import paid_brandspend as b
from tracker import paid_brandspend_math as m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "brand_spend_data.js"), os.path.join(JS, "brand_spend.js")
TEMPLATE = os.path.join(ROOT, "templates", "brand_spend.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [T.ACCT, T.ACCT2, {"id": "3333333333", "name": "Gamma <b>&</b> Co", "currency": "USD"}, {"id": "4444444444", "name": "Delta", "currency": "USD"},
            {"id": "5555555555", "name": "Epsilon", "currency": "USD"}, {"id": "6666666666", "name": "Zeta", "currency": "USD"}]
THU = dt.date(2026, 10, 22)


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
    U = T.USUAL
    rows = {"1234567890": T.weeks_of(weekly=U + [203]),                                                       # as usual
            "2222222222": T.weeks_of(cid="22", name="Brand Core", weekly=[98] * 7 + [196]) + T.weeks_of(cid="23", name="Brand Exact", weekly=[105] * 8) +
            T.weeks_of(cid="24", name="NonBrand Core", weekly=[300] * 8),                                     # over: Core doubled
            "3333333333": T.weeks_of(cid="33", name="Core", weekly=U + [301]),                                # no brand campaign
            "4444444444": T.weeks_of(cid="44", weekly=U + [None]),                                            # stopped
            "5555555555": T.weeks_of(cid="55", weekly=U + [98], clicks=35),                                   # under
            "6666666666": T.weeks_of(cid="66", weekly=[None] * 5 + [100, 100, 100])}                          # new

    def go(account, today=T.TODAY, search=None, **kw):
        paid_geo.clear_cache()
        T.base.forget("")
        return b.report_for(account, accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [{"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in ACCOUNTS]}],
                            search_fn=search or T.searcher(rows), today=today, **kw)
    part = [T.api(date=(dt.date(2026, 10, 19) + dt.timedelta(days=d)).isoformat(), cost="87000000", clicks="10") for d in range(3)]
    from tests.monitor_fakes import MemStore
    from tracker import paid_brandspend_settings as st
    mon = MemStore()
    st.save_account(mon, "1234567890", {"weeklyMax": 250, "monthlyBudget": 800, "brandTerms": "acme"}, "ana@position2.com")
    mon.snaps_put("brandspend", "2026-10-19", {"1234567890": {"status": "over", "cost": 301}})
    mon.snaps_put("brandspend", "2026-10-20", {"1234567890": {"status": "over", "cost": 301}})
    rich_rows = T.weeks_of(weekly=T.USUAL + [301])
    spike = next(r for r in rich_rows if r["segments"]["date"] == "2026-10-07")
    spike["metrics"].update(costMicros="150000000", clicks="40", conversions="0")
    terms = {"1234567890": [T.term_row("12", "NonBrand Implants", "acme dental near me", "300000000", "60", "2")]}
    rich = go("1234567890", search=T.searcher({"1234567890": rich_rows}, terms=terms), mon=mon)
    out = {"mcc": json.loads(json.dumps(go(GROUP))), "one": json.loads(json.dumps(go("2222222222"))), "ok": json.loads(json.dumps(go("1234567890"))),
           "marked": json.loads(json.dumps(go("2222222222", overrides={"24": True, "22": False}))),
           "rich": json.loads(json.dumps(rich)), "part": json.loads(json.dumps(go("1234567890", today=THU, search=T.searcher({"1234567890": T.weeks_of(weekly=T.USUAL + [203]) + part}))))}
    mp.undo()
    paid_geo.clear_cache()
    T.base.forget("")
    return out


# ── words and shapes ────────────────────────────────────────────────────────

def test_every_status_the_server_can_send_has_a_label_and_a_tone_and_every_level_too(reports):
    seen = {a["status"] for a in reports["mcc"]["accounts"]}
    assert seen == {"ok", "over", "under", "stopped", "new", "none"}
    for s in seen | {"idle"}:
        assert run("X.STATUS[%s]" % json.dumps(s)) is not None
    assert run('X.statusInfo("over")') == {"label": "Over the usual", "tone": "over"} and run('X.statusInfo("stopped")')["tone"] == "bad" and run('X.statusInfo("zzz")') == {"label": "zzz", "tone": "unk"}
    for a in reports["mcc"]["accounts"]:
        for f in a["findings"]:
            assert run("X.LEVEL[%s]" % json.dumps(f["level"])) is not None
        for s in a["plan"]["steps"]:
            assert run("X.LEVEL[%s]" % json.dumps(s["level"])) is not None
    assert run('X.levelInfo("zzz")') == {"label": "zzz", "tone": "unk"}


def test_the_cause_reads_in_the_direction_of_the_move():
    assert [run("X.causeText(%s,%s)" % (json.dumps(s), json.dumps(c))) for s, c in (("over", "clicks"), ("over", "cpc"), ("under", "clicks"), ("under", "cpc"), ("over", "both"), ("under", None), ("ok", "clicks"))] == [
        "More clicks", "Dearer clicks", "Fewer clicks", "Cheaper clicks", "Clicks and their price", "", ""]


@pytest.mark.parametrize("v,cur", [(0, "USD"), (5, "USD"), (12.5, "USD"), (99.999, "USD"), (100, "USD"), (203, "USD"), (1234.5, "USD"), (98765.4, "EUR"), (301, "CHF"), (7.25, "GBP"), (55, "INR"), (4.5, "CAD"), (300, "AUD")])
def test_money_reads_exactly_as_the_server_writes_it(v, cur):
    assert run("X.money(%s,%s)" % (json.dumps(v), json.dumps(cur))) == m.money(v, cur)


def test_a_missing_sum_is_n_a_and_counts_have_thousands():
    assert run("X.money(null,'USD')") == "n/a" and run("X.int(1234567)") == "1,234,567" and run("X.int(null)") == "n/a" and run("X.pct(0.3)") == "30%" and run("X.pct(null)") == "n/a"


def test_a_change_reads_plainly_and_a_move_inside_the_band_is_not_called_bad(reports):
    assert run("X.change(A)", A={"ratio": None, "status": "new"}) == {"text": "No usual week to compare", "tone": "none"}
    assert run("X.change(A)", A={"ratio": 1.0, "status": "ok"}) == {"text": "Level with a usual week", "tone": "flat"}
    assert run("X.change(A)", A={"ratio": 1.004, "status": "ok"})["tone"] == "flat"
    assert run("X.change(A)", A={"ratio": 1.4828, "status": "over"}) == {"text": "Up 48% on a usual week", "tone": "bad"}
    assert run("X.change(A)", A={"ratio": 0.48, "status": "under"}) == {"text": "Down 52% on a usual week", "tone": "bad"}
    assert run("X.change(A)", A={"ratio": 1.1, "status": "ok"}) == {"text": "Up 10% on a usual week", "tone": "flat"}


def test_this_week_so_far_is_said_only_when_it_is_off_pace(reports):
    assert run("X.partialText(A)", A={"partial": {"status": "over"}}) == "This week so far is ahead of the usual pace"
    assert run("X.partialText(A)", A={"partial": {"status": "under"}}) == "This week so far is behind the usual pace"
    assert run("X.partialText(A)", A={"partial": {"status": "ok"}}) == "" and run("X.partialText(A)", A={"partial": None}) == "" and run("X.partialText(A)", A={}) == ""


def test_the_weekly_bars_share_a_scale_with_the_usual_band_and_are_marked_over_under_or_in_band(reports):
    over = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Beta Clinic")
    t = run("X.trendBars(A)", A=over)
    ex = over["expected"]
    top = max([w["cost"] for w in over["trend"]] + [ex["high"]]) * 1.12
    assert len(t["bars"]) == 8 and t["bars"][-1]["tone"] == "low" and t["bars"][-2]["tone"] == "ok" and t["bars"][0]["height"] == round(over["trend"][0]["cost"] / top * 100)
    assert t["bars"][-1]["height"] == round(over["trend"][-1]["cost"] / top * 100) and "to 2026-10-18" in t["bars"][-1]["title"]
    assert t["band"] == {"low": round(ex["low"] / top * 100), "high": round(ex["high"] / top * 100), "usual": round(ex["cost"] / top * 100)} and t["band"]["low"] < t["band"]["usual"] < t["band"]["high"]
    under = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Epsilon")
    assert run("X.trendBars(A)", A=under)["bars"][-1]["tone"] == "under"
    stopped = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Delta")
    sb = run("X.trendBars(A)", A=stopped)["bars"]
    assert sb[-1]["tone"] == "none" and sb[-1]["height"] == 0 and "no spend" in sb[-1]["title"]
    new = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Zeta")
    nt = run("X.trendBars(A)", A=new)
    assert nt["band"] is None and nt["bars"][-1]["tone"] == "ok" and nt["bars"][0]["tone"] == "none"


def test_a_tiny_spend_still_shows_a_bar_and_the_scale_never_divides_by_nothing():
    A = {"expected": None, "trend": [{"from": "a", "to": "b", "cost": 0.01, "clicks": 1}, {"from": "c", "to": "d", "cost": 0, "clicks": 0}]}
    t = run("X.trendBars(A)", A=A)
    assert t["bars"][0]["height"] == 2 and t["bars"][1]["height"] == 0 and t["band"] is None
    assert run("X.trendBars(A)", A={"expected": None, "trend": []}) == {"bars": [], "band": None}


def test_the_tiles_are_the_servers_numbers_in_words(reports):
    t = run("X.tiles(R)", R=reports["mcc"])
    assert [x[0] for x in t] == ["Accounts read", "Outside the usual", "Brand spend last week", "This week off pace", "Not judged"]
    assert t[0][1] == "6" and "2026-10-12 to 2026-10-18" in t[0][2]
    assert t[1][1] == "3" and t[1][3] == "warn" and "by twice the limit or more" in t[1][2]
    o = reports["mcc"]["summary"]["overall"]
    assert t[2][1] == m.money(o["cost"], "USD") and ("usual " + m.money(o["expected"], "USD")) in t[2][2] and "across 4 accounts" in t[2][2]
    assert t[3][1] == "0" and t[4][1] == "2" and "too new, no spend or no brand campaign" in t[4][2]
    quiet = run("X.tiles(R)", R=reports["ok"])
    assert quiet[1][1] == "0" and quiet[1][3] == "good" and "every account is inside its usual band" in quiet[1][2] and quiet[4][1] == "0" and "across 1 account" in quiet[2][2] and "up 0%" in quiet[2][2]
    none = run("X.tiles(R)", R={"account": {"currency": "USD"}, "window": {"latestFrom": "a", "latestTo": "b"}, "summary": {"accounts": 1, "needsLook": 0, "high": 0, "thisWeek": 0, "counts": {"none": 1}, "overall": {"cost": None, "expected": None, "accounts": 0}}})
    assert none[2][1] == "n/a" and "no account had a usual week" in none[2][2]


def test_the_views_filter_the_accounts_and_open_on_the_first_with_something_in_it(reports):
    r = reports["mcc"]
    assert run("X.viewCount(R,'all')", R=r) == 6 and run("X.viewCount(R,'outside')", R=r) == 3 and run("X.viewCount(R,'attention')", R=r) == 3
    assert [a["name"] for a in run("X.shown(R,'attention')", R=r)] == ["Delta", "Beta Clinic", "Epsilon"] and run("X.firstView(R)", R=r) == "attention"
    assert run("X.firstView(R)", R=reports["ok"]) == "all"
    assert run("X.needsLook(A)", A={"status": "ok", "findings": [{"level": "medium"}]}) is True and run("X.needsLook(A)", A={"status": "new", "findings": [{"level": "high"}]}) is True
    assert run("X.needsLook(A)", A={"status": "none", "findings": [{"level": "info"}]}) is False and run("X.needsLook(A)", A={"status": "ok", "findings": [{"level": "low"}]}) is False
    assert run("X.needsLook(A)", A={"status": "ok", "findings": []}) is False and run("X.needsLook(A)", A={"status": "over", "findings": []}) is True and run("X.needsLook(A)", A={"status": "ok"}) is False
    p = reports["part"]
    assert run("X.viewCount(R,'attention')", R=p) == 1 and run("X.viewCount(R,'outside')", R=p) == 0 and run("X.firstView(R)", R=p) == "attention"


def test_the_address_carries_only_the_account_and_refresh_because_the_server_applies_what_the_team_saved():
    assert run("X.query('1234567890',false)") == "account=1234567890" and run("X.query('mcc-1',true)") == "account=mcc-1&refresh=1" and run("X.query('a b&c',false)").startswith("account=a%20b%26c")


def test_marking_a_campaign_sends_the_value_and_nothing_when_it_only_restates_the_name():
    c = {"id": "7", "byName": False}
    assert run("X.markBody(C, true)", C=c) == {"campaign": "7", "value": True} and run("X.markBody(C, false)", C=c) == {"campaign": "7", "value": None}
    brand = {"id": "8", "byName": True}
    assert run("X.markBody(C, false)", C=brand) == {"campaign": "8", "value": False} and run("X.markBody(C, true)", C=brand) == {"campaign": "8", "value": None}


def test_the_limits_form_sends_the_account_and_the_typed_text_unchanged():
    assert run("X.limitsBody('111', T)", T={"weeklyMin": "150", "weeklyMax": "", "monthlyBudget": "5,000", "brandTerms": "acme, acme dental", "other": 1}) == {
        "account": "111", "weeklyMin": "150", "weeklyMax": "", "monthlyBudget": "5,000", "brandTerms": "acme, acme dental"}


def test_the_month_is_a_bar_of_spent_projected_and_budget_on_one_scale_and_has_no_bar_without_a_budget():
    mo = {"spent": 570, "projected": 930, "budget": 800, "status": "pace-over"}
    assert run("X.monthBar(A)", A={"month": mo}) == {"spent": 61, "projected": 100, "budget": 86, "tone": "warn"}
    assert run("X.monthBar(A)", A={"month": dict(mo, budget=1300, status="pace-under")}) == {"spent": 44, "projected": 72, "budget": 100, "tone": "low"}
    assert run("X.monthBar(A)", A={"month": dict(mo, budget=500, status="over")})["tone"] == "bad" and run("X.monthBar(A)", A={"month": dict(mo, budget=930, status="ok")})["tone"] == "ok"
    assert run("X.monthBar(A)", A={"month": dict(mo, budget=None)}) is None and run("X.monthBar(A)", A={"month": None}) is None and run("X.monthBar(A)", A={}) is None


def test_the_history_is_a_row_of_days_coloured_by_how_they_stood():
    a = {"history": {"statuses": [{"day": "2026-10-19", "status": "ok", "cost": 203}, {"day": "2026-10-20", "status": "over", "cost": 301}, {"day": "2026-10-21", "status": "stopped", "cost": 0}, {"day": "2026-10-22", "status": "new", "cost": None}, {"day": "2026-10-23", "status": "weird", "cost": 5}]}}
    cells = run("X.historyCells(A)", A=a)
    assert [c["tone"] for c in cells] == ["ok", "over", "bad", "unk", "unk"] and cells[1]["title"] == "2026-10-20: Over the usual, 301 spent" and cells[3]["title"] == "2026-10-22: Too new to judge" and cells[4]["title"] == "2026-10-23: weird, 5 spent"
    assert run("X.historyCells(A)", A={"history": None}) == [] and run("X.historyCells(A)", A={}) == []


def test_the_csv_has_a_line_per_brand_campaign_and_a_line_for_an_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    head = run("X.HEAD")
    assert rows[0] == ["Brand Spend Alert", "Group"] and rows[1] == ["Week", "2026-10-12 to 2026-10-18"] and rows[2] == ["Limit", "plus or minus 30% of a usual week"] and rows[4] == head
    body = rows[5:]
    names = [r[4] for r in body]
    assert "Brand Core" in names and "Brand Exact" in names and "NonBrand Core" not in names and names.count("(no brand campaign)") == 1
    core = next(r for r in body if r[4] == "Brand Core")
    assert core[:2] == ["Beta Clinic", "Over the usual"] and core[5] == 196 and core[6] == 98 and core[7] == 98 and core[8] == 70 and core[10] == "ENABLED" and core[11] == "MAXIMIZE_CLICKS" and core[12] == 40
    assert core[13] == "Dearer clicks"
    assert all(len(r) == len(head) for r in body) and head[-1] == "Fix plan"
    beta = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Beta Clinic")
    assert core[-1].startswith("1. ") and " | 2. " in core[-1] and core[-1] == run("X.planTitles(A)", A=beta)
    assert run("X.planTitles(A)", A={"plan": None}) == "" and run("X.planTitles(A)", A={}) == ""
    stopped = next(r for r in body if r[0] == "Delta")
    assert stopped[1] == "Spend stopped" and stopped[5] == 0 and stopped[7] == -203


# ── the page script ─────────────────────────────────────────────────────────

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const paths = JSON.parse(process.argv[1]), sc = JSON.parse(process.argv[2]);
const els = {}, calls = [], hist = [], store = {}, downloads = [], held = [], bodies = [], typed = {}, panels = [];
function el(sel) {
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", value: (sc.values || {})[sel] || "", hidden: false, href: "", handlers: {},
    addEventListener(ev, fn) { this.handlers[ev] = fn; }, setAttribute() {}, getAttribute() { return null; } };
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
let picker = null; const sets = [];
const win = { MonitorPanel: { mount: (host, o) => panels.push(o) }, PMPicker: (host, o) => { picker = { onChange: o.onChange, set(items, v, dis) { sets.push({ items, v, dis }); } }; return picker; } };
const ctx = { window: win, document: doc, history: { replaceState(a, b, u) { hist.push(u); } }, location: { search: sc.search || "", pathname: "/p2/paid-marketing/brand-spend" },
  localStorage: { getItem: k => (k in store ? store[k] : (sc.stored || {})[k] || null), setItem: (k, v) => { store[k] = v; } },
  URLSearchParams, URL: { createObjectURL: b => { downloads.blob = b; return "blob:x"; }, revokeObjectURL() {} }, Blob: class { constructor(p) { this.text = p.join(""); } },
  setTimeout: fn => fn(), Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, console, isNaN, parseInt, parseFloat,
  fetch: (url, o) => { if (o && o.method) bodies.push({ url, method: o.method, body: JSON.parse(o.body || "null") });
    return sc.reject && url.includes(sc.reject) ? (calls.push(url), Promise.reject(new Error("down"))) :
      Promise.resolve(respond(url)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) })); } };
ctx.window.document = doc;
vm.createContext(ctx);
for (const p of paths) vm.runInContext(fs.readFileSync(p, "utf8"), ctx);
const tick = () => new Promise(r => setImmediate(r));
function target(spec) {
  spec = spec || {};
  const make = s => ({ dataset: s.dataset || {}, closest: q => (spec.closest && spec.closest[q] ? make(spec.closest[q]) : null),
    querySelector: q => { const k = "q:" + q; typed[k] = typed[k] || { value: (s.fields || {})[q] || "" }; return typed[k]; } });
  return make(spec);
}
(async () => {
  for (let i = 0; i < 6; i++) await tick();
  for (const step of sc.steps || []) {
    if (step.pick) picker.onChange(step.pick);
    if (step.set) el(step.set[0]).value = step.set[1];
    if (step.change) el(step.change).handlers.change.call(el(step.change));
    if (step.click) el(step.click).handlers.click({});
    if (step.release) held.splice(0).forEach(f => f());
    if (step.fire) { const [sel, closest] = step.fire; el(sel).handlers.click({ target: target({ closest }) }); }
    for (let i = 0; i < 6; i++) await tick();
  }
  const out = { calls, bodies, panels, typed: Object.fromEntries(Object.entries(typed).map(([k, v]) => [k, v.value])), hist, store, sets, downloads: downloads.map(d => ({ name: d.name })), blob: downloads.blob ? downloads.blob.text : "", el: {} };
  for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, value: els[k].value };
  process.stdout.write(JSON.stringify(out));
})();
"""
SCRIPTS = [DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}


def page(scenario):
    out = subprocess.run(["node", "-e", HARNESS, json.dumps(SCRIPTS), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(report, steps=None, **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": report}], "steps": [{"pick": "2222222222"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("brand_spend_data.js") < html.index("brand_spend.js")
    assert "—" not in html and "–" not in html


def test_the_accounts_are_loaded_into_the_picker_with_the_mcc_first_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and items[1]["sub"] == "6 accounts checked together" and "MCC" in items[1]["badge"] and len(items) == 8
    assert not any("/brandspend/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account_and_the_alert_panel_is_mounted(reports):
    out = opened(reports["one"])
    assert [c for c in out["calls"] if "/brandspend/report" in c] == ["/p2/paid-marketing/api/brandspend/report?account=2222222222"] and out["panels"] == [{"agent": "brandspend", "label": "Brand Spend Alert"}]
    assert out["store"]["bs_account"] == "2222222222" and out["hist"][-1] == "/p2/paid-marketing/brand-spend?account=2222222222" and out["el"]["#out"]["hidden"] is False


def test_the_whole_report_is_drawn_with_the_worst_account_first_and_markup_escaped(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}])
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Accounts read", "Outside the usual", "Brand spend last week", "This week off pace", "Not judged"):
        assert text in kp, text
    assert lst.index("Delta") < lst.index("Beta Clinic") < lst.index("Epsilon") and "Gamma" not in lst and "Acme Dental" not in lst      # "Needs a look" holds the three that need one
    assert "bi-pill bad" in lst and "Spend stopped" in lst and "bi-pill over" in lst and "Over the usual" in lst and "bi-pill under" in lst and "Under the usual" in lst
    assert "$301" in lst and "usual $203 \u00b7 Up 48%" in lst and "bs-fig over" in lst and "Dearer clicks" in lst
    views = out["el"]["#views"]["html"]
    assert "Needs a look <span class=\"n\">3</span>" in views and "All <span class=\"n\">6</span>" in views and 'class="pm-chip on" data-v="attention"' in views
    assert "MCC · 6 accounts checked together" in out["el"]["#fresh"]["html"] and "<b>Group</b>" in out["el"]["#fresh"]["html"]
    assert "furthest from usual first" in out["el"]["#listNote"]["text"]


def test_all_accounts_shows_every_one_and_names_with_markup_are_escaped(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])
    lst = out["el"]["#list"]["html"]
    assert "Gamma &lt;b&gt;&amp;&lt;/b&gt; Co" in lst and "<b>&</b>" not in lst
    for name in ("Acme Dental", "Beta Clinic", "Delta", "Epsilon", "Zeta"):
        assert name in lst
    for label in ("As usual", "Too new to judge", "No brand campaign", "Spend stopped"):
        assert label in lst, label
    assert "6 accounts, furthest from usual first" in out["el"]["#listNote"]["text"]


def test_a_single_account_opens_on_its_bars_findings_plan_and_campaigns(reports):
    out = opened(reports["one"])
    lst = out["el"]["#list"]["html"]
    assert 'class="bi-row over open"' in lst and "Brand spend by week" in lst and lst.count('class="bi-w ') == 8 and 'class="bs-band"' in lst and "the shaded strip is the usual band" in lst
    f = reports["one"]["accounts"][0]["findings"][0]
    assert esc(f["title"]) in lst and esc(f["nextStep"]) in lst
    assert "Brand Core" in lst and "Brand Exact" in lst and "NonBrand Core" in lst and "Do not count" in lst and "Count as brand" in lst and "maximize clicks" in lst
    assert "Last week</th>" in lst and "Usual week</th>" in lst and "Cost per click</th>" in lst and "+$98.00" in lst
    plan = reports["one"]["accounts"][0]["plan"]
    assert lst.count("<details") == len(plan["steps"]) and lst.count("<details open>") == 1 and "Why this applies." in lst and "What to do" in lst and "bs-worth" in lst and "<b>Worth.</b>" in lst
    for s in plan["steps"]:
        assert esc(s["title"]) in lst and ('<span class="bi-sn">%d</span>' % s["n"]) in lst
        for h in s["how"]:
            assert esc(h) in lst
        if s["worth"]:
            assert esc(s["worth"]) in lst
    assert "Applies to:" in lst and "check-the-settings" not in lst and "data-diag" not in lst


def test_a_step_that_holds_markup_is_escaped_and_an_account_with_no_plan_draws_none(reports):
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["plan"]["steps"][0]["title"] = "<img src=x onerror=1>"
    risky["accounts"][0]["plan"]["steps"][0]["how"][0] = "<b>open</b> it"
    risky["accounts"][0]["plan"]["steps"][0]["worth"] = "worth <3"
    risky["accounts"][0]["plan"]["note"] = "It has been low for <3 weeks"
    lst = opened(risky)["el"]["#list"]["html"]
    assert "&lt;img src=x onerror=1&gt;" in lst and "<img" not in lst and "&lt;b&gt;open&lt;/b&gt; it" in lst and "low for &lt;3 weeks" in lst and "worth &lt;3" in lst
    quiet = json.loads(json.dumps(reports["one"]))
    quiet["accounts"][0]["plan"] = {"note": None, "steps": []}
    assert "bi-plan" not in opened(quiet)["el"]["#list"]["html"]
    assert "bi-plan" not in opened(dict(quiet, accounts=[dict(quiet["accounts"][0], plan=None)]))["el"]["#list"]["html"]


def test_an_account_on_its_usual_and_one_with_no_usual_week_draw_without_a_band_or_a_plan(reports):
    ok = opened(reports["ok"])["el"]["#list"]["html"]
    assert "As usual" in ok and "Nothing to flag: the brand spend is inside its usual band." in ok and "bi-plan" not in ok and 'class="bs-band"' in ok
    new = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Zeta")
    lst = opened(dict(reports["mcc"], accounts=[new]), [{"pick": GROUP}])["el"]["#list"]["html"]
    assert "Too new to judge" in lst and "no usual week yet" in lst and 'class="bs-band"' not in lst


def test_a_part_week_off_pace_shows_on_the_row_of_an_account_that_was_fine(reports):
    out = opened(reports["part"])
    lst = out["el"]["#list"]["html"]
    assert "This week so far is ahead of the usual pace" in lst and "ahead of the usual pace" in out["el"]["#list"]["html"]
    assert "This week off pace" in out["el"]["#kpis"]["html"]


def test_a_row_opens_and_closes_on_a_click(reports):
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "2222222222"}}}]}]
    out = opened(reports["mcc"], steps)
    assert 'class="bi-row over open"' in out["el"]["#list"]["html"]
    closed = opened(reports["mcc"], steps + steps[1:])
    assert 'class="bi-row over open"' not in closed["el"]["#list"]["html"]


def test_marking_a_campaign_saves_it_for_the_team_and_reads_again(reports):
    steps = [{"fire": ["#list", {"[data-mark]": {"dataset": {"mark": "24", "to": "brand", "acct": "2222222222"}}}]}]
    out = opened(reports["one"], steps, responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": reports["one"]}, {"match": "/settings/mark", "body": {"marks": {"24": True}}}])
    assert out["bodies"] == [{"url": "/p2/paid-marketing/api/brandspend/settings/mark", "method": "POST", "body": {"campaign": "24", "value": True}}] and len([c for c in out["calls"] if "/brandspend/report" in c]) == 2
    assert "bs_over" not in out["store"]
    back = opened(reports["marked"], [{"fire": ["#list", {"[data-mark]": {"dataset": {"mark": "24", "to": "not", "acct": "2222222222"}}}]}],
                  responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": reports["marked"]}, {"match": "/settings/mark", "body": {"marks": {}}}])
    assert back["bodies"][0]["body"] == {"campaign": "24", "value": None}                                          # 24 is a non-brand campaign by its name: counting it and then not counting it is the name again


def test_a_refused_save_is_said_and_nothing_is_read_again(reports):
    steps = [{"fire": ["#list", {"[data-mark]": {"dataset": {"mark": "24", "to": "brand", "acct": "2222222222"}}}]}]
    out = opened(reports["one"], steps, responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": reports["one"]}, {"match": "/settings/mark", "status": 503, "body": {"error": "The Paid Marketing store is unavailable: x"}}])
    assert "The Paid Marketing store is unavailable: x" in out["el"]["#msg"]["html"] and len([c for c in out["calls"] if "/brandspend/report" in c]) == 1
    down = opened(reports["one"], steps, reject="/settings/mark")
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"]


def test_the_limit_shown_is_the_one_the_server_used_and_a_changed_one_is_checked_saved_for_the_team_and_read_again(reports):
    out = opened(dict(reports["one"], limit=0.25))
    assert out["el"]["#limit"]["value"] == "25"
    ok = {"match": "/settings/limit", "body": {"limit": 0.4}}
    ch = opened(reports["one"], [{"set": ["#limit", "40"]}, {"change": "#limit"}], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": reports["one"]}, ok])
    assert ch["bodies"] == [{"url": "/p2/paid-marketing/api/brandspend/settings/limit", "method": "POST", "body": {"limit": "40"}}] and len([c for c in ch["calls"] if "/brandspend/report" in c]) == 2
    for bad in ("9", "91", "abc", "", "12.5", "-3"):
        r = opened(reports["one"], [{"set": ["#limit", bad]}, {"change": "#limit"}], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": reports["one"]}, ok])
        assert "The limit is a whole percentage from 10 to 90." in r["el"]["#msg"]["html"] and r["el"]["#limit"]["value"] == "30" and r["bodies"] == []


def test_refresh_reads_again_and_a_cooldown_is_said_with_the_servers_words(reports):
    out = opened(reports["one"], [{"click": "#refreshBtn"}])
    assert [c for c in out["calls"] if "/brandspend/report" in c][-1].endswith("account=2222222222&refresh=1")
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "sequence": [reports["one"], {"error": "Refreshed a moment ago. Try again in 90 seconds.", "kind": "cooldown", "retryIn": 90}], "statuses": [200, 429]}],
          "steps": [{"pick": "2222222222"}, {"click": "#refreshBtn"}]}
    c = page(sc)
    assert "Refreshed a moment ago. Try again in 90 seconds." in c["el"]["#msg"]["html"] and "pm-note" in c["el"]["#msg"]["html"] and c["el"]["#out"]["hidden"] is False


def test_failures_are_said_in_words(reports):
    quota = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "status": 429, "body": {"error": "The daily limit is used up.", "kind": "quota"}}], "steps": [{"pick": "2222222222"}]})
    assert "The daily limit is used up. The platform limits how fast" in quota["el"]["#msg"]["html"] and quota["el"]["#empty"]["hidden"] is False
    bad = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "status": 400, "body": {"error": "The limit must be between 10% and 90%."}}], "steps": [{"pick": "2222222222"}]})
    assert "The limit must be between 10% and 90%." in bad["el"]["#msg"]["html"]
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "reject": "/brandspend/report", "steps": [{"pick": "2222222222"}]})
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"]
    noacc = page({"responses": [{"match": "/segments/accounts", "status": 502, "body": {"error": "Google Ads is not set up."}}]})
    assert "Google Ads is not set up." in noacc["el"]["#msg"]["html"]


def test_a_copy_saved_while_google_was_not_answering_says_so_and_warnings_are_shown(reports):
    stale = dict(reports["one"], stale="2026-10-19T10:00:00+00:00", warnings=["Beta Clinic could not be read: no"])
    out = opened(stale)
    assert "Google Ads is not answering, so this is the copy saved" in out["el"]["#fresh"]["html"] and "Beta Clinic could not be read: no" in out["el"]["#msg"]["html"]


def test_the_footnote_says_where_the_usual_comes_from_with_the_limit_of_the_read(reports):
    foot = opened(reports["one"])["el"]["#foot"]["html"]
    assert "the account's own history" in foot and "band of 30% either way" in foot and "Nothing here is a budget the client set" in foot and "saved for everyone" in foot and "only in accounts whose brand words are saved" in foot
    assert "—" not in foot and "–" not in foot


def test_the_csv_downloads_with_the_account_and_week_in_its_name_and_a_formula_cell_is_defused(reports):
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["campaigns"][0]["name"] = "=HYPERLINK(1)"
    out = opened(risky, [{"click": "#exportBtn"}])
    assert out["downloads"] == [{"name": "brand-spend-Beta-Clinic-2026-10-18.csv"}] and out["blob"].startswith("﻿")
    lines = out["blob"].lstrip("﻿").split("\r\n")
    assert lines[0] == "Brand Spend Alert,Beta Clinic" and "'=HYPERLINK(1)" in out["blob"] and lines[4].startswith("Account,Status,Week from")


def test_nothing_the_page_writes_has_a_dash_or_an_unfilled_slot(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}] + [
        {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": a["id"]}}}]} for a in reports["mcc"]["accounts"]])
    text = re.sub(r"<[^>]+>", " ", out["el"]["#list"]["html"] + out["el"]["#kpis"]["html"] + out["el"]["#foot"]["html"])
    assert "—" not in text and "–" not in text and "undefined" not in text and "NaN" not in text and "[object" not in text and "null" not in text.replace("nullable", "")


# ── What else the readings show, and the form for what the team agreed ──────

def rich_open(reports, steps=None, **kw):
    return page(dict({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": reports["rich"]}, {"match": "/settings/", "body": {}}],
                      "steps": [{"pick": "1234567890"}] + (steps or [])}, **kw))


def test_an_account_with_readings_draws_the_facts_the_month_the_leak_the_spikes_and_the_days(reports):
    a = reports["rich"]["accounts"][0]
    lst = rich_open(reports)["el"]["#list"]["html"]
    e, sh = a["efficiency"], a["share"]
    assert "What else the figures show" in lst and "<b>Conversions</b> %s last week against a usual %s" % (int(e["conv"]), int(e["usualConv"])) in lst
    assert "<b>Impression share</b> 90% against a usual 90%" in lst and "<b>Agreed weekly limit</b> up to $250 · last week $301 (over by $51.00)" in lst
    mo = a["month"]
    assert mo["status"] == "pace-over" and mo["budget"] == 800.0
    assert "<h4>The month</h4>" in lst and ("heading for $%s against the budget of $800" % format(round(mo["projected"]), ",")) in lst and ("the budget runs out on %s" % mo["runsOutOn"]) in lst
    assert 'class="bs-mbar warn"' in lst and 'style="left:' in lst and ("%d days (" % mo["days"]) in lst
    assert "<h4>Brand searches other campaigns paid for" in lst and "NonBrand Implants" in lst and "acme dental near me" in lst and ("$300 in all at $5.00 a click against %s in the brand campaigns, about %s more than the same clicks cost there" % (m.money(a["leak"]["brandCpc"], "USD"), m.money(a["leak"]["overpay"], "USD"))) in lst
    assert "<h4>Days far above the usual</h4>" in lst and "<b>Wednesday 2026-10-07</b> $150 against a usual $29.00 · 40 clicks (usually 10) · 0 conversions" in lst and "clicks that did not convert" in lst
    assert "<h4>Day by day" in lst and lst.count('<i class="over"') == 2 and "Outside its band for 2 days in a row, since 2026-10-19." in lst


def test_the_form_shows_what_the_team_saved_and_saves_the_typed_figures_for_the_account(reports):
    steps = [{"fire": ["#list", {"[data-save]": {"dataset": {"save": "1234567890"}}, ".bi-row": {"fields": {'[data-f="weeklyMin"]': "100", '[data-f="weeklyMax"]': "260", '[data-f="monthlyBudget"]': "900", '[data-f="brandTerms"]': "acme, acme dental"}}}]}]
    out = rich_open(reports, steps)
    lst = out["el"]["#list"]["html"]
    assert "Agreed limits and brand words" in lst and "(saved for the whole team)" in lst and 'data-f="weeklyMax" value="250"' in lst and 'data-f="monthlyBudget" value="800"' in lst and 'data-f="brandTerms" value="acme"' in lst and 'data-f="weeklyMin" value=""' in lst
    assert 'data-save="1234567890">Save for the team' in lst and "disabled" not in lst.split("data-save=")[1].split(">")[0]
    assert out["bodies"] == [{"url": "/p2/paid-marketing/api/brandspend/settings/account", "method": "POST",
                              "body": {"account": "1234567890", "weeklyMin": "100", "weeklyMax": "260", "monthlyBudget": "900", "brandTerms": "acme, acme dental"}}] and len([c for c in out["calls"] if "/brandspend/report" in c]) == 2


def test_brand_words_the_campaign_names_suggest_are_offered_and_a_click_adds_one_without_repeating_it(reports):
    one = reports["one"]
    assert one["accounts"][0]["suggestedTerms"] == []                              # "Brand Core" and "Brand Exact" say no word that is a name
    r = json.loads(json.dumps(reports["one"]))
    r["accounts"][0]["suggestedTerms"] = ["northwind", "acme"]
    lst = opened(r)["el"]["#list"]["html"]
    assert "The campaign names suggest:" in lst and 'data-term="northwind">northwind</button>' in lst and 'data-term="acme">acme</button>' in lst
    click = lambda term, have: opened(r, [{"fire": ["#list", {"[data-term]": {"dataset": {"term": term}}, ".bi-row": {"fields": {'[data-f="brandTerms"]': have}}}]}])["typed"]['q:[data-f="brandTerms"]']
    assert click("northwind", "") == "northwind" and click("acme", "northwind") == "northwind, acme" and click("acme", "northwind, acme") == "northwind, acme"


def test_when_the_teams_settings_cannot_be_read_the_form_cannot_save_and_says_why(reports):
    r = json.loads(json.dumps(reports["one"]))
    r["saved"]["teamSettings"] = False
    lst = opened(r)["el"]["#list"]["html"]
    assert 'data-save="2222222222" disabled' in lst and "The team settings are not available, so nothing can be saved now." in lst


def test_an_account_with_no_extra_readings_draws_none_of_them_and_a_brand_word_search_that_found_nothing_says_so(reports):
    lst = opened(reports["ok"], [], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": reports["ok"]}])["el"]["#list"]["html"]
    for absent in ("Brand searches other campaigns paid for", "Days far above the usual", "Day by day", "Agreed weekly limit", 'class="bs-mbar'):
        assert absent not in lst, absent
    assert "Agreed limits and brand words" in lst and "Save a monthly budget below to judge it." in lst
    r = json.loads(json.dumps(reports["ok"]))
    r["accounts"][0].update(leakRead="read", leak=None, terms=["acme"])
    assert 'None found in the last 30 days for "acme".' in opened(r, [], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": r}])["el"]["#list"]["html"]
    r["accounts"][0].update(leakRead="not read")
    assert "The search terms could not be read, so this was not looked for." in opened(r, [], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandspend/report", "body": r}])["el"]["#list"]["html"]


def test_the_csv_names_the_other_findings_after_the_first(reports):
    rows = run("X.csvRows(R)", R=reports["rich"])
    head = run("X.HEAD")
    assert head[-2] == "Other findings" and head[-1] == "Fix plan" and all(len(r) == len(head) for r in rows[5:])
    first = rows[5]
    titles = [f["title"] for f in reports["rich"]["accounts"][0]["findings"]]
    assert first[14] == titles[0] and first[-2] == " | ".join(titles[1:]) and len(titles) >= 4


def test_the_alert_panel_has_a_place_on_the_page():
    html = open(TEMPLATE, encoding="utf-8").read()
    assert html.index('id="monPanel"') > html.index('id="out"') and html.index("monitor_panel.js") < html.index("brand_spend_data.js")
