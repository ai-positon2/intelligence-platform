"""Brand IS Monitor: static/js/brand_is_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads Google Ads, does every sum and ranks every finding (tests/test_paid_brandis*.py). The page only draws, so how a share, a change and a status read, the
shape of the weekly bars and the CSV come from brand_is_data.js (executed here, not read), and brand_is.js is run for real: it loads the accounts, sends the threshold and the
campaigns the person marks, draws the report the server built and says what went wrong in words. The HTML it writes is read back.
"""
import json
import os
import shutil
import subprocess

import pytest

from tests import test_paid_brandis as T
from tracker import paid_brandis as b
from tracker import paid_brandis_math as m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "brand_is_data.js"), os.path.join(JS, "brand_is.js")
TEMPLATE = os.path.join(ROOT, "templates", "brand_is.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [T.ACCT, T.ACCT2, {"id": "3333333333", "name": "Gamma <b>&</b> Co", "currency": "USD"}, {"id": "4444444444", "name": "Delta", "currency": "USD"},
            {"id": "5555555555", "name": "Epsilon", "currency": "USD"}]


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
    rows = {"1234567890": [T.api(share=0.97), T.api(week=T.BEFORE, share=0.97)],
            "2222222222": [T.api(cid="22", name="Brand Core", share=0.62, lb=0.30, lr=0.08), T.api(cid="23", name="Brand Exact", impr="400", share=0.9, lb=0.0, lr=0.1),
                           T.api(cid="22", name="Brand Core", week=T.BEFORE, share=0.95), T.api(cid="23", name="Brand Exact", impr="400", week=T.BEFORE, share=0.9),
                           T.api(cid="24", name="NonBrand Core", impr="9000", share=0.4)],
            "3333333333": [T.api(cid="33", name="Core", share=0.9)],
            "4444444444": [T.api(cid="44", impr="10", share=0.9)],
            "5555555555": [T.api(cid="55", name="Brand", share=None, lb=None, lr=None, top=None, abs_top=None)]}

    def go(account, **kw):
        paid_geo.clear_cache()
        T.base.forget("")
        return b.report_for(account, accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [{"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in ACCOUNTS]}],
                            search_fn=T.searcher(rows), today=T.TODAY, **kw)
    out = {"mcc": json.loads(json.dumps(go(GROUP))), "one": json.loads(json.dumps(go("2222222222"))), "ok": json.loads(json.dumps(go("1234567890"))),
           "marked": json.loads(json.dumps(go("2222222222", overrides={"24": True, "22": False})))}
    mp.undo()
    paid_geo.clear_cache()
    T.base.forget("")
    return out


# ── words and shapes ────────────────────────────────────────────────────────

def test_every_status_and_level_the_server_can_send_has_a_label_and_a_tone():
    for s in ("below", "ok", "thin", "unmeasured", "none"):
        assert run("X.statusInfo(%s)" % json.dumps(s))["tone"] != "unk" or s in ("thin", "unmeasured", "none")
    assert run('X.statusInfo("below")') == {"label": "Below threshold", "tone": "over"} and run('X.statusInfo("ok")')["tone"] == "ok"
    assert run('X.statusInfo("zzz")') == {"label": "zzz", "tone": "unk"} and run('X.levelInfo("zzz")') == {"label": "zzz", "tone": "unk"}
    for level in ("high", "medium", "low", "info"):
        assert run("X.levelInfo(%s)" % json.dumps(level))["tone"] != "unk" or level == "info"
    assert [run("X.causeText(%s)" % json.dumps(c)) for c in ("budget", "rank", "both", None)] == ["Lost to budget", "Lost to rank", "Budget and rank", ""]


def test_the_server_statuses_and_levels_are_all_known_to_the_page(reports):
    seen = {a["status"] for a in reports["mcc"]["accounts"]}
    assert seen == {"below", "ok", "thin", "unmeasured", "none"}
    for s in seen:
        assert run("X.STATUS[%s]" % json.dumps(s)) is not None
    for a in reports["mcc"]["accounts"]:
        for f in a["findings"]:
            assert run("X.LEVEL[%s]" % json.dumps(f["level"])) is not None


@pytest.mark.parametrize("v,want", [(0.9, "90%"), (0.8999, "90%"), (0.0999, "10%"), (None, "n/a"), (1, "100%"), (0, "0%")])
def test_shares_read_as_whole_percentages(v, want):
    assert run("X.pct(%s)" % json.dumps(v)) == want


def test_a_change_reads_plainly_with_a_good_direction():
    assert run("X.delta(null)") == {"text": "No week before to compare", "tone": "none"}
    assert run("X.delta(0)")["tone"] == "flat" and run("X.delta(0.004)")["tone"] == "flat" and run("X.delta(-0.004)")["text"] == "Level with the week before"
    assert run("X.delta(-0.05)") == {"text": "Down 5 points on the week before", "tone": "bad"}
    assert run("X.delta(0.01)") == {"text": "Up 1 point on the week before", "tone": "good"}


def test_the_meter_clamps_the_bar_and_marks_the_threshold(reports):
    below = next(a for a in reports["mcc"]["accounts"] if a["status"] == "below")
    mt = run("X.meter(A)", A=below)
    assert mt["tone"] == "over" and 0 < mt["share"] < 90 and mt["threshold"] == 90
    assert run("X.meter(A)", A={"status": "none", "threshold": 0.9, "brand": {"share": None}}) == {"share": None, "threshold": 90, "tone": "unk"}
    assert run("X.meter(A)", A={"status": "ok", "threshold": 0.9, "brand": {"share": 1.2}})["share"] == 100


def test_the_weekly_bars_are_the_eight_weeks_with_flat_marked_weeks_where_nothing_can_be_judged(reports):
    two = next(a for a in reports["mcc"]["accounts"] if a["name"] == "Beta Clinic")
    bars = run("X.trendBars(A)", A=two)
    assert len(bars) == 8 and bars[-1]["has"] and bars[-1]["tone"] == "low" and bars[-2]["tone"] == "ok" and bars[0]["tone"] == "none" and bars[0]["height"] == 0
    assert bars[-1]["height"] == round(two["trend"][-1]["share"] * 100) and "to 2026-10-18" in bars[-1]["title"] and "eligible searches" in bars[-1]["title"]
    assert "no brand impressions" in bars[0]["title"]
    thin = run("X.trendBars(A)", A={"threshold": 0.9, "trend": [{"from": "a", "to": "b", "share": 0.9, "eligible": 50, "measured": 45, "unmeasured": 0}]})
    assert thin[0]["has"] is False and "too few searches" in thin[0]["title"]


def test_a_week_exactly_at_the_threshold_is_not_drawn_as_short_and_a_tiny_share_still_shows_a_bar():
    A = {"threshold": 0.9, "trend": [{"from": "a", "to": "b", "share": 0.9, "eligible": 500, "measured": 450, "unmeasured": 0},
                                     {"from": "c", "to": "d", "share": 0.01, "eligible": 500, "measured": 5, "unmeasured": 0},
                                     {"from": "e", "to": "f", "share": 0.8999, "eligible": 100, "measured": 90, "unmeasured": 0}]}
    bars = run("X.trendBars(A)", A=A)
    assert [b["tone"] for b in bars] == ["ok", "low", "low"] and [b["height"] for b in bars] == [90, 2, 90]


def test_the_tiles_are_the_servers_numbers_in_words(reports):
    t = run("X.tiles(R)", R=reports["mcc"])
    assert [x[0] for x in t] == ["Accounts read", "Under 90%", "Brand share, all together", "Fell on the week before", "Not judged"]
    assert t[0][1] == "5" and "2026-10-12 to 2026-10-18" in t[0][2]
    assert t[1][1] == "1" and t[1][3] == "warn" and "by 10 points or more" in t[1][2]
    assert t[2][1] == run("X.pct(S)", S=reports["mcc"]["summary"]["overall"]["share"]) and "across 2 accounts" in t[2][2]
    assert t[3][1] == "1" and t[4][1] == "3" and "too few searches" in t[4][2]
    quiet = run("X.tiles(R)", R=reports["ok"])
    assert quiet[1][1] == "0" and quiet[1][3] == "good" and "every judged account is on target" in quiet[1][2] and quiet[4][1] == "0"


def test_the_views_filter_the_accounts_and_open_on_the_first_with_something_in_it(reports):
    r = reports["mcc"]
    assert run("X.viewCount(R,'all')", R=r) == 5 and run("X.viewCount(R,'below')", R=r) == 1
    assert run("X.viewCount(R,'attention')", R=r) == 2                                    # the one below and the one with no share reported
    assert [a["name"] for a in run("X.shown(R,'attention')", R=r)] == ["Beta Clinic", "Epsilon"] and run("X.firstView(R)", R=r) == "attention"
    assert run("X.firstView(R)", R=reports["ok"]) == "all" and run("X.shown(R,'all')", R=reports["ok"])[0]["status"] == "ok"
    assert run("X.needsLook(A)", A={"status": "ok", "findings": [{"kind": "dropped"}]}) is True and run("X.needsLook(A)", A={"status": "ok", "findings": []}) is False


def test_the_address_carries_the_account_the_threshold_the_marks_and_refresh():
    assert run("X.query('1234567890','90',{},false)") == "account=1234567890&threshold=90"
    assert run("X.query('mcc-1','85',{'5':true,'7':true,'9':false},true)") == "account=mcc-1&threshold=85&brand=5,7&notbrand=9&refresh=1"
    assert run("X.query('a b&c','90',{},false)").startswith("account=a%20b%26c")


def test_marking_a_campaign_adds_the_mark_and_drops_it_when_it_only_restates_the_name():
    c = {"id": "7", "byName": False}
    assert run("X.mark({}, C, true)", C=c) == {"7": True}
    assert run("X.mark({'7':true}, C, false)", C=c) == {}                    # back to what the name says
    brand = {"id": "8", "byName": True}
    assert run("X.mark({}, C, false)", C=brand) == {"8": False} and run("X.mark({'8':false,'1':true}, C, true)", C=brand) == {"1": True}


def test_the_csv_has_a_line_per_brand_campaign_and_a_line_for_an_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    assert rows[0] == ["Brand IS Monitor", "Group"] and rows[2] == ["Threshold", "90%"] and rows[4] == run("X.HEAD")
    body = rows[5:]
    names = [r[4] for r in body]
    assert "Brand Core" in names and "Brand Exact" in names and "NonBrand Core" not in names and names.count("(no brand campaign)") == 1
    core = next(r for r in body if r[4] == "Brand Core")
    assert core[0] == "Beta Clinic" and core[1] == "Below threshold" and core[5] == "62%" and core[7] == "30%" and core[8] == "8%" and core[11] == "Lost to budget"
    assert all(len(r) == len(run("X.HEAD")) for r in body)


# ── the page script ─────────────────────────────────────────────────────────

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const paths = JSON.parse(process.argv[1]), sc = JSON.parse(process.argv[2]);
const els = {}, handlers = {}, calls = [], hist = [], store = {}, downloads = [], held = [];
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
  const body = Array.isArray(r.sequence) ? r.sequence[i] : r.body;
  const answer = { status: (r.statuses ? r.statuses[i] : r.status) || 200, body };
  if (r.hold && !r.released) return new Promise(res => held.push(() => { r.released = true; res(answer); }));
  return answer;
}
let picker = null; const sets = [];
const win = { PMPicker: (host, o) => { picker = { onChange: o.onChange, set(items, v, dis) { sets.push({ items, v, dis }); } }; return picker; } };
const ctx = { window: win, document: doc, history: { replaceState(a, b, u) { hist.push(u); } }, location: { search: sc.search || "", pathname: "/p2/paid-marketing/brand-is" },
  localStorage: { getItem: k => (k in store ? store[k] : (sc.stored || {})[k] || null), setItem: (k, v) => { store[k] = v; } },
  URLSearchParams, URL: { createObjectURL: b => { downloads.blob = b; return "blob:x"; }, revokeObjectURL() {} }, Blob: class { constructor(p) { this.text = p.join(""); } },
  setTimeout: fn => fn(), Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, console, isNaN, parseInt, parseFloat,
  fetch: url => (sc.reject && url.includes(sc.reject) ? (calls.push(url), Promise.reject(new Error("down"))) :
    Promise.resolve(respond(url)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) }))) };
ctx.window.document = doc;
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
    if (step.set) el(step.set[0]).value = step.set[1];
    if (step.change) el(step.change).handlers.change.call(el(step.change));
    if (step.click) el(step.click).handlers.click({});
    if (step.release) held.splice(0).forEach(f => f());
    if (step.fire) { const [sel, closest] = step.fire; el(sel).handlers.click({ target: target({ closest }) }); }
    for (let i = 0; i < 6; i++) await tick();
  }
  const out = { calls, hist, store, sets, downloads: downloads.map(d => ({ name: d.name })), blob: downloads.blob ? downloads.blob.text : "", el: {} };
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
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandis/report", "body": report}], "steps": [{"pick": "2222222222"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    import re
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("brand_is_data.js") < html.index("brand_is.js")


def test_the_accounts_are_loaded_into_the_picker_with_the_mcc_first_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and items[1]["sub"] == "5 accounts checked together" and "MCC" in items[1]["badge"] and len(items) == 7
    assert not any("/brandis/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_with_the_threshold_and_remembers_the_account(reports):
    out = opened(reports["one"])
    assert [c for c in out["calls"] if "/brandis/report" in c] == ["/p2/paid-marketing/api/brandis/report?account=2222222222&threshold=90"]
    assert out["store"]["bi_account"] == "2222222222" and out["hist"][-1] == "/p2/paid-marketing/brand-is?account=2222222222" and out["el"]["#out"]["hidden"] is False


def test_the_whole_report_is_drawn_with_the_worst_account_first_and_markup_escaped(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}])
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Accounts read", "Under 90%", "Brand share, all together", "Fell on the week before", "Not judged"):
        assert text in kp, text
    assert lst.index("Beta Clinic") < lst.index("Epsilon") and "Gamma" not in lst and "Acme Dental" not in lst            # "Needs a look" holds the two that need one
    assert "bi-pill over" in lst and "Below threshold" in lst and "68%" in lst and 'aria-label="68% against a threshold of 90%"' in lst
    assert "Lost to budget" in lst and "Down 25 points on the week before" in lst
    views = out["el"]["#views"]["html"]
    assert "Needs a look <span class=\"n\">2</span>" in views and "All <span class=\"n\">5</span>" in views and 'class="pm-chip on" data-v="attention"' in views
    assert "MCC · 5 accounts checked together" in out["el"]["#fresh"]["html"] and "<b>Group</b>" in out["el"]["#fresh"]["html"]


def test_all_accounts_shows_every_one_and_names_with_markup_are_escaped(reports):
    out = opened(reports["mcc"], [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])
    lst = out["el"]["#list"]["html"]
    assert "Gamma &lt;b&gt;&amp;&lt;/b&gt; Co" in lst and "<b>&</b>" not in lst
    for name in ("Acme Dental", "Beta Clinic", "Delta", "Epsilon"):
        assert name in lst
    for label in ("On target", "Too few searches", "No share reported", "No brand campaign"):
        assert label in lst, label
    assert "5 accounts, worst first" in out["el"]["#listNote"]["text"]


def test_a_single_account_opens_on_its_trend_findings_and_campaigns(reports):
    out = opened(reports["one"])
    lst = out["el"]["#list"]["html"]
    assert 'class="bi-row below open"' in lst and "Brand impression share by week" in lst and lst.count('class="bi-w ') == 8
    assert "Brand impression share is 68%, under 90%" in lst and "The brand campaigns run out of budget" in lst and "Brand Exact is at" not in lst
    assert "Brand Core" in lst and "Brand Exact" in lst and "NonBrand Core" in lst and "Do not count" in lst and "Count as brand" in lst
    assert "maximize clicks" in lst and "Daily budget" in lst


def test_a_row_opens_and_closes_on_a_click(reports):
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "2222222222"}}}]}]
    out = opened(reports["mcc"], steps)
    assert 'class="bi-row below open"' in out["el"]["#list"]["html"]
    out = opened(reports["mcc"], steps + steps[1:])
    assert 'class="bi-row below"' in out["el"]["#list"]["html"]


def test_marking_a_campaign_asks_again_with_the_mark_and_remembers_it(reports):
    out = opened(reports["one"], [{"fire": ["#list", {"[data-mark]": {"dataset": {"mark": "24", "to": "brand", "acct": "2222222222"}}}]}])
    reads = [c for c in out["calls"] if "/brandis/report" in c]
    assert reads[-1].endswith("account=2222222222&threshold=90&brand=24") and json.loads(out["store"]["bi_over"]) == {"2222222222": {"24": True}}
    out = opened(reports["marked"], [{"fire": ["#list", {"[data-mark]": {"dataset": {"mark": "22", "to": "brand", "acct": "2222222222"}}}]}])
    assert "marked by you" in out["el"]["#list"]["html"]


def test_the_marks_kept_for_an_account_are_sent_with_that_account_only(reports):
    stored = {"bi_over": json.dumps({"1234567890": {"5": False}})}
    mine = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandis/report", "body": reports["ok"]}], "stored": stored, "steps": [{"pick": "1234567890"}]})
    assert [c for c in mine["calls"] if "/brandis/report" in c] == ["/p2/paid-marketing/api/brandis/report?account=1234567890&threshold=90&notbrand=5"]
    other = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandis/report", "body": reports["one"]}], "stored": stored, "steps": [{"pick": "2222222222"}]})
    assert [c for c in other["calls"] if "/brandis/report" in c] == ["/p2/paid-marketing/api/brandis/report?account=2222222222&threshold=90"]


def test_a_threshold_is_sent_and_a_bad_one_is_refused_in_words(reports):
    out = opened(reports["one"], [{"set": ["#threshold", "85"]}, {"change": "#threshold"}])
    assert [c for c in out["calls"] if "/brandis/report" in c][-1].endswith("threshold=85") and out["store"]["bi_threshold"] == "85"
    out = opened(reports["one"], [{"set": ["#threshold", "20"]}, {"change": "#threshold"}])
    assert len([c for c in out["calls"] if "/brandis/report" in c]) == 1 and "whole percentage from 50 to 99" in out["el"]["#msg"]["html"] and out["el"]["#threshold"]["value"] == "90"
    out = opened(reports["one"], [{"set": ["#threshold", "abc"]}, {"change": "#threshold"}])
    assert "whole percentage" in out["el"]["#msg"]["html"]


def test_a_saved_threshold_and_marks_are_used_on_the_first_read(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandis/report", "body": reports["one"]}], "search": "?account=2222222222",
                "stored": {"bi_threshold": "80", "bi_over": json.dumps({"2222222222": {"5": False}})}})
    assert [c for c in out["calls"] if "/brandis/report" in c] == ["/p2/paid-marketing/api/brandis/report?account=2222222222&threshold=80&notbrand=5"]
    assert out["el"]["#threshold"]["value"] == "80"


def test_refresh_asks_again_with_refresh_and_the_csv_download_is_named_and_safe(reports):
    out = opened(reports["one"], [{"click": "#refreshBtn"}, {"click": "#exportBtn"}])
    assert [c for c in out["calls"] if "/brandis/report" in c][-1].endswith("&refresh=1")
    assert out["downloads"][0]["name"] == "brand-is-Beta-Clinic-2026-10-18.csv" and out["blob"].startswith("\ufeffBrand IS Monitor,Beta Clinic") and "Brand Core" in out["blob"]
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["brandCampaigns"][0]["name"] = "=HYPERLINK(1)"
    out = opened(risky, [{"click": "#exportBtn"}])
    assert "'=HYPERLINK(1)" in out["blob"]


def test_a_failure_is_said_in_words_with_a_hint_for_a_spent_allowance_and_a_cooldown_is_a_note(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandis/report", "status": 429, "body": {"error": "The daily limit is used up.", "kind": "quota"}}],
                "steps": [{"pick": "2222222222"}]})
    assert "The daily limit is used up." in out["el"]["#msg"]["html"] and "clears on its own" in out["el"]["#msg"]["html"] and out["el"]["#empty"]["hidden"] is False
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandis/report", "status": 429, "body": {"error": "Refreshed a moment ago.", "kind": "cooldown"}}],
                "steps": [{"pick": "2222222222"}]})
    assert "pm-note" in out["el"]["#msg"]["html"] and "Refreshed a moment ago." in out["el"]["#msg"]["html"]
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "reject": "/brandis/report", "steps": [{"pick": "2222222222"}]})
    assert "The server could not be reached" in out["el"]["#msg"]["html"]


def test_a_failure_after_a_report_keeps_the_report_on_screen(reports):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/brandis/report", "sequence": [reports["one"], {"error": "Google is down"}], "statuses": [200, 502]}],
          "steps": [{"pick": "2222222222"}, {"click": "#refreshBtn"}]}
    out = page(sc)
    assert "Google is down" in out["el"]["#msg"]["html"] and out["el"]["#out"]["hidden"] is False and "Beta Clinic" in out["el"]["#fresh"]["html"]


def test_an_answer_that_arrives_late_for_another_account_is_dropped(reports):
    sc = {"responses": [{"match": "account=2222222222", "body": reports["one"], "hold": True}, {"match": "account=1234567890", "body": reports["ok"]}, {"match": "/segments/accounts", "body": ACCTS}],
          "steps": [{"pick": "2222222222"}, {"pick": "1234567890"}, {"release": True}]}
    out = page(sc)
    assert "Acme Dental" in out["el"]["#fresh"]["html"] and "Beta Clinic" not in out["el"]["#fresh"]["html"]


def test_warnings_and_a_saved_read_are_said(reports):
    warned = dict(reports["one"], warnings=["Delta could not be read: Google answered 500"])
    assert "Delta could not be read" in opened(warned)["el"]["#msg"]["html"]
    old = dict(reports["one"], stale="2026-10-19T10:00:00+00:00")
    assert "Google Ads is not answering, so this is the copy saved" in opened(old)["el"]["#fresh"]["html"]


def test_no_account_shows_the_empty_state():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    assert out["el"]["#empty"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True and out["el"]["#out"]["hidden"] is True
    bad = page({"responses": [{"match": "/segments/accounts", "status": 503, "body": {"error": "Google Ads is not connected."}}]})
    assert "Google Ads is not connected." in bad["el"]["#msg"]["html"]
