"""Cross-Channel & Attribution Analysis: static/js/xch_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads both platforms, does every sum and ranks every finding (tests/test_paid_xchannel*.py). The page only draws, so how a figure reads and the shape of the charts come from
xch_data.js (executed here, not read), and cross_channel.js is run for real: it loads the picker, sends the range, measure and typed numbers, draws every section of a finished report
from the real server output, and says what went wrong in words. The HTML it writes is read back.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

from tests import test_paid_xchannel as T
from tracker import paid_xchannel_findings as fd
from tracker import paid_xchannel_math as m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "xch_data.js"), os.path.join(JS, "cross_channel.js")
TEMPLATE = os.path.join(ROOT, "templates", "cross_channel.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def reports():
    mp = pytest.MonkeyPatch()
    mp.setattr(T.paid_segments, "_meta_target", lambda act, store: dict(T.META_ACCT, id=act))
    T.paid_gads.forget()
    both = T.run(preset="90", **T.settings_fns(), actual_conv="300", actual_value="20000")
    T.paid_gads.forget()                                           # the saved settings are shared in memory between reads of the same account and range
    one = T.run(account=T.GID, preset="90")
    T.paid_gads.forget()
    plain = T.run(preset="30")
    T.paid_gads.forget()
    mp.undo()
    return {"both": json.loads(json.dumps(both)), "one": json.loads(json.dumps(one)), "plain": json.loads(json.dumps(plain))}


# ── words ───────────────────────────────────────────────────────────────────

def test_every_level_and_role_the_server_can_send_has_a_label_and_a_tone():
    for level in fd.LEVELS:
        assert run("X.levelInfo(%s)" % json.dumps(level))["tone"] != "unk"
    for role in m.ROLE:
        assert run("X.roleInfo(%s)" % json.dumps(role))["label"] == m.ROLE[role]
    assert run('X.levelInfo("zzz")') == {"label": "zzz", "tone": "unk"} and run('X.roleInfo("zzz")')["label"] == "Mixed"


def test_the_ranges_and_measures_offered_are_the_ones_the_server_accepts():
    assert [p[0] for p in run("X.PRESETS")] == list(m.PRESETS)
    from tracker import paid_xchannel as xc
    assert [p[0] for p in run("X.MEASURES")] == list(xc.MEASURES)


def test_changes_read_plainly_and_the_good_direction_depends_on_the_figure():
    assert run("[X.change(12.4), X.change(-3.6), X.change(1.9), X.change(null), X.change(NaN)]") == ["+12%", "-4%", "no change", "", ""]
    assert run('[X.changeTone("cpa", 10), X.changeTone("cpa", -10), X.changeTone("conv", 10), X.changeTone("conv", -10), X.changeTone("cost", 50), X.changeTone("cpa", 1), X.changeTone("cpa", null)]') == [
        "bad", "good", "good", "bad", "neutral", "neutral", "neutral"]


def test_a_platform_name_takes_an_apostrophe_alone():
    assert run('[X.poss("meta"), X.poss("google"), X.poss("tiktok")]') == ["Meta Ads'", "Google Ads'", "tiktok's"]


def test_ratios_drop_decimals_from_a_hundred():
    assert run("[X.ratio(4.4567), X.ratio(123.4), X.ratio(null), X.ratio(NaN)]") == ["4.46", "123", "n/a", "n/a"]


# ── the report, drawn ───────────────────────────────────────────────────────

def test_the_tiles_are_the_blended_figures_with_their_change_and_tone(reports):
    tiles = run("X.tiles(R)", R=reports["both"])
    assert [t["label"] for t in tiles] == ["Spend", "Conversions claimed", "Cost per conversion", "Return on ad spend", "Findings"]
    assert tiles[0]["value"] == "$5,400" or tiles[0]["value"].startswith("$")
    assert tiles[4]["value"] == str(reports["both"]["summary"]["findings"]) and tiles[4]["tone"] == "bad"
    one = run("X.tiles(R)", R=reports["one"])
    assert "own claim" in one[1]["hint"] and "Google Ads" in one[1]["hint"]


def test_the_return_tile_says_when_no_value_is_tracked():
    rep = {"blended": {"cur": {"cost": 100, "conv": 5, "cpa": 20, "roas": None, "value": 0}, "delta": {"cost": None, "cpa": None, "roas": None}}, "account": {"currency": "USD"}, "platforms": ["meta"],
           "range": {"label": "the last 30 days"}, "summary": {"findings": 0, "high": 0}}
    t = run("X.tiles(R)", R=rep)
    assert t[3]["value"] == "n/a" and t[3]["hint"] == "no conversion value tracked, or too small to be a value" and t[4]["tone"] == "good"


def test_the_lead_line_names_the_platforms_the_spend_and_the_claim(reports):
    s = run("X.leadLine(R)", R=reports["both"])
    assert s.startswith("Google Ads and Meta Ads spent $") and "between them claim" in s and "Your own system recorded 300, so some are counted twice." in s
    assert run("X.leadLine(R)", R=reports["one"]).startswith("Google Ads spent $") and " claims " in run("X.leadLine(R)", R=reports["one"])
    quiet = {"blended": {"cur": {"cost": 0}}, "account": {"currency": "USD"}, "platforms": ["google"], "range": {"label": "Last month"}}
    assert run("X.leadLine(R)", R=quiet) == "Google Ads had no spend in last month."


def test_the_share_bars_split_each_total_between_the_platforms_and_skip_a_total_nobody_has(reports):
    bars = run("X.shareBars(R)", R=reports["both"])
    assert [b["key"] for b in bars] == ["cost", "conv", "value"]
    for b in bars:
        assert abs(sum(s["pct"] for s in b["segments"]) - 100) < 0.2
    rep = json.loads(json.dumps(reports["both"]))
    for p in rep["platforms"]:
        rep["channels"][p]["share"]["value"] = None
    assert [b["key"] for b in run("X.shareBars(R)", R=rep)] == ["cost", "conv"]


def test_the_waterfall_rows_are_signed_scaled_to_the_biggest_and_say_which_way_is_good(reports):
    rep = json.loads(json.dumps(reports["both"]))
    rep["decomposition"] = {"ok": True, "from": 20.0, "to": 22.5, "change": 2.5, "mix": -0.5, "rate": {"google": 1.0, "meta": 2.0}}
    w = run('X.waterfall(R, "USD")', R=rep)
    assert [r["width"] for r in w["rows"]] == [25, 50, 100] and [r["tone"] for r in w["rows"]] == ["good", "bad", "bad"]
    assert w["rows"][0]["text"] == "-$0.50" and w["changeText"] == "+$2.50" and w["fromText"] == "$20.00" and w["rows"][1]["label"] == "Google Ads' own cost per conversion"
    rep["decomposition"] = {"ok": False, "why": "no"}
    assert run('X.waterfall(R, "USD")', R=rep) is None


def test_the_weekly_chart_has_a_line_per_platform_inside_its_frame_and_nice_ticks(reports):
    c = run("X.weeklyChart(R, 'cost', 520, 190)", R=reports["both"])
    assert [s["platform"] for s in c["series"]] == ["google", "meta"] and all(len(s["points"]) == len(reports["both"]["weeks"]) for s in c["series"])
    for s in c["series"]:
        for x, y in s["points"]:
            assert c["pad"]["l"] <= x <= 520 - c["pad"]["r"] + 0.1 and c["pad"]["t"] - 0.1 <= y <= c["baseline"] + 0.1
    assert c["max"] in (500, 1000, 2000, 5000, 200, 100, 50) or c["max"] > 0
    assert len(c["yTicks"]) == 3 and len(c["xTicks"]) <= 7 and c["path" if False else "series"]
    assert run("X.niceMax(137)") == 200 and run("X.niceMax(0.3)") == 0.5 and run("X.niceMax(4200)") == 5000 and run("X.niceMax(9)") == 10 and run("X.niceMax(1)") == 1


def test_there_is_no_chart_for_one_week_or_for_nothing():
    rep = {"weeks": [{"start": "2026-09-07", "google": {"cost": 5, "conv": 1}}], "platforms": ["google"], "account": {"currency": "USD"}}
    assert run("X.weeklyChart(R, 'cost', 520, 190)", R=rep) is None
    rep = {"weeks": [{"start": "2026-09-07", "google": {"cost": 0, "conv": 0}}, {"start": "2026-09-14", "google": {"cost": 0, "conv": 0}}], "platforms": ["google"], "account": {"currency": "USD"}}
    assert run("X.weeklyChart(R, 'cost', 520, 190)", R=rep) is None


def test_a_platform_missing_a_week_is_skipped_not_drawn_at_zero():
    rep = {"weeks": [{"start": "2026-09-07", "google": {"cost": 5, "conv": 1}, "meta": None}, {"start": "2026-09-14", "google": {"cost": 6, "conv": 1}, "meta": {"cost": 3, "conv": 1}},
                     {"start": "2026-09-21", "google": {"cost": 7, "conv": 1}, "meta": {"cost": 4, "conv": 1}}], "platforms": ["google", "meta"], "account": {"currency": "USD"}}
    c = run("X.weeklyChart(R, 'cost', 520, 190)", R=rep)
    assert [len(s["points"]) for s in c["series"]] == [3, 2]


def test_a_link_test_reads_in_words_for_each_outcome():
    base = {"spend": "meta", "conversions": "google", "lag": 0, "n": 13, "r": 0.8, "p": 0.001, "enough": True}
    assert run("X.linkText(T)", T=dict(base, reads="together")) == {"text": "When Meta Ads spend rose, Google Ads conversions rose in the same week (correlation 0.8 over 13 weeks).", "tone": "good"}
    assert run("X.linkText(T)", T=dict(base, reads="opposite", lag=1, r=-0.7))["text"].startswith("When Meta Ads spend rose, Google Ads conversions fell a week later") and run("X.linkText(T)", T=dict(base, reads="opposite"))["tone"] == "warn"
    assert run("X.linkText(T)", T=dict(base, reads="none", r=0.123))["text"] == "Meta Ads spend and Google Ads conversions in the same week: no clear link (correlation 0.12 over 13 weeks)."
    assert run("X.linkText(T)", T=dict(base, reads="none", enough=False))["text"].startswith("Not enough weeks to test")


def test_the_claims_against_the_persons_numbers_read_as_rows(reports):
    rec = reports["both"]["reconciliation"]
    assert rec["conversions"]["over"] and rec["value"]["over"]
    rows = {r["label"]: r for r in run('X.claimRows(R, "USD")', R=rec)}
    assert "Conversions your system recorded" in rows and "Spend per recorded conversion" in rows and "Return on the revenue recorded" in rows
    assert "Google Ads cost per conversion, scaled to what was recorded" in rows and "Meta Ads return, scaled to what was recorded" in rows
    assert rows["Claimed against recorded"]["tone"] == "bad" and rows["Claimed against recorded"]["value"] == "1.35x"
    assert run('X.claimRows(null, "USD")') == []


def test_claims_below_what_was_recorded_are_not_called_scaled_and_are_not_drawn_as_a_problem():
    under = {"spend": 1000, "blendedCpa": 10.0, "blendedRoas": 5.0,
             "conversions": {"claimed": 60, "actual": 100, "ratio": 0.6, "over": False, "perPlatform": {"google": {"cpa": 20.0}, "meta": {"cpa": None}}},
             "value": {"claimed": 3000, "actual": 5000, "over": False, "perPlatform": {"google": {"roas": 3.0}, "meta": {"roas": None}}}}
    rows = {r["label"]: r for r in run('X.claimRows(R, "USD")', R=under)}
    assert rows["Claimed against recorded"]["tone"] == "neutral" and rows["Google Ads cost per conversion"]["value"] == "$20.00" and rows["Meta Ads cost per conversion"]["value"] == "n/a"
    assert rows["Google Ads return"]["value"] == "3x" and rows["Meta Ads return"]["value"] == "n/a" and not any("scaled" in label for label in rows)


def test_the_attribution_settings_read_as_rows_per_platform(reports):
    t = run("X.attributionText(A)", A=reports["both"]["attribution"])
    assert t["google"][0]["name"] == "Booked" and "clicks counted for 30 days" in t["google"][0]["detail"] and "views for 1 day," in t["google"][0]["detail"]
    assert t["meta"][0]["name"] == "7 day click, 1 day view" and t["meta"][0]["detail"].startswith("100% of spend in 1 ad set")
    assert run("X.attributionText(A)", A={"google": None, "meta": None}) == {"google": [], "meta": []}


def test_the_type_rows_are_biggest_spend_first_and_name_the_platform(reports):
    rows = run("X.typeRows(R)", R=reports["both"])
    assert [r["t"]["cost"] for r in rows] == sorted((r["t"]["cost"] for r in rows), reverse=True) and {r["platformName"] for r in rows} == {"Google Ads", "Meta Ads"}


# ── the page script ─────────────────────────────────────────────────────────

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const paths = JSON.parse(process.argv[1]), sc = JSON.parse(process.argv[2]);
const els = {}, handlers = {}, calls = [], hist = [], store = {}, picks = [], held = [];
function el(sel) {
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", value: (sc.values || {})[sel] || "", hidden: false, href: "", handlers: {},
    addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }, setAttribute() {}, getAttribute() { return null; } };
  return els[sel];
}
const doc = { readyState: "complete", querySelector: s => el(s), querySelectorAll: () => [], addEventListener(ev, fn) { (handlers[ev] = handlers[ev] || []).push(fn); } };
const responses = (sc.responses || []).map(r => Object.assign({ n: 0 }, r));
function respond(url) {
  calls.push(url);
  const hits = responses.filter(r => url.includes(r.match));
  if (!hits.length) return { status: 404, body: { error: "no stub for " + url } };
  const r = hits[0];
  const i = Array.isArray(r.sequence) ? Math.min(r.n++, r.sequence.length - 1) : 0;
  const body = Array.isArray(r.sequence) ? r.sequence[i] : r.body;
  const answer = { status: (r.statuses ? r.statuses[i] : r.status) || 200, body };
  if (r.hold && !r.released) return new Promise(res => held.push(() => { r.released = true; res(answer); }));       // an answer that comes when told to
  return answer;
}
const win = { PMPlatform: { accounts: (host, o) => { picks.push(o); return {}; } } };
const ctx = { window: win, document: doc, history: { replaceState(a, b, u) { hist.push(u); } }, location: { hash: sc.hash || "", pathname: "/p2/paid-marketing/cross-channel" },
  localStorage: { getItem: k => (k in store ? store[k] : sc.stored || null), setItem: (k, v) => { store[k] = v; } },
  setTimeout, clearTimeout, Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, decodeURIComponent, console, isNaN, parseInt, parseFloat,
  fetch: url => Promise.resolve(respond(url)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) })) };
ctx.window.document = doc; ctx.window.location = ctx.location;
vm.createContext(ctx);
for (const p of paths) vm.runInContext(fs.readFileSync(p, "utf8"), ctx);
const tick = () => new Promise(r => setImmediate(r));
function target(spec) {
  spec = spec || {};
  return { closest: q => (spec.closest && spec.closest[q] ? { getAttribute: k => (spec.closest[q][k] == null ? null : spec.closest[q][k]) } : null) };
}
(async () => {
  for (let i = 0; i < 6; i++) await tick();
  for (const step of sc.steps || []) {
    if (step.pick) picks[picks.length - 1].onChange(step.pick[0], step.pick[1] || "both");
    if (step.set) el(step.set[0]).value = step.set[1];
    if (step.click) for (const fn of el(step.click).handlers.click || []) fn({});
    if (step.release) held.splice(0).forEach(f => f());
    if (step.fire) for (const fn of handlers.click || []) fn({ target: target({ closest: step.fire }) });
    for (let i = 0; i < 6; i++) await tick();
  }
  const out = { calls, hist, store, picker: picks.map(p => ({ url: p.url, allow: p.allow, platform: p.platform, account: p.account })), el: {} };
  for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, href: els[k].href, value: els[k].value };
  process.stdout.write(JSON.stringify(out));
})();
"""
SCRIPTS = [os.path.join(JS, n) for n in ("au_data.js", "exp_data.js", "xch_data.js", "cross_channel.js")]
ACCT = {"id": T.GID + "+" + T.ACT, "name": "Acme", "platform": "both"}


def page(scenario):
    out = subprocess.run(["node", "-e", HARNESS, json.dumps(SCRIPTS), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(report, steps=None, **kw):
    sc = {"responses": [{"match": "/xchannel/report", "body": report}], "steps": [{"pick": [ACCT]}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def count(html, needle):
    return html.count(needle)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    tpl = open(TEMPLATE).read()
    ids = set(re.findall(r'id="([^"]+)"', tpl))
    wanted = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', open(PAGE).read()))
    assert wanted - ids == set(), wanted - ids
    for name in ("pm_picker.js", "pm_platform.js", "au_data.js", "exp_data.js", "xch_data.js", "cross_channel.js"):
        assert name in tpl
    assert tpl.index("xch_data.js") < tpl.index("cross_channel.js") and tpl.index("exp_data.js") < tpl.index("xch_data.js")


def test_the_picker_offers_google_meta_and_both_and_the_controls_are_drawn():
    out = page({})
    assert out["picker"][0]["url"] == "/p2/paid-marketing/api/gads/accounts" and out["picker"][0]["allow"] == ["google", "meta", "both"] and out["picker"][0]["platform"] == "both"
    ranges = out["el"]["#ranges"]["html"]
    assert count(ranges, "data-r=") == 5 and ranges.count('aria-checked="true"') == 1 and 'data-r="30"' in ranges and "Last 30 days" in ranges and "This month so far" in ranges
    assert count(out["el"]["#measures"]["html"], "data-m=") == 2 and "bids on" in out["el"]["#measureHint"]["text"]


def test_a_saved_range_measure_and_account_are_restored_from_the_address():
    out = page({"hash": "#pf=meta&a=act_5550001&r=60&m=all", "stored": "1112223334"})
    assert out["picker"][0]["platform"] == "meta" and out["picker"][0]["account"] == "act_5550001"
    assert 'data-r="60" aria-checked="true"' in out["el"]["#ranges"]["html"].replace('class="pm-chip on" ', "").replace('class="pm-chip" ', "") or 'aria-checked="true" data-r="60"' in out["el"]["#ranges"]["html"]
    assert 'aria-checked="true" data-m="all"' in out["el"]["#measures"]["html"]
    junk = page({"hash": "#r=999&m=nonsense"})
    assert 'aria-checked="true" data-r="30"' in junk["el"]["#ranges"]["html"] and 'aria-checked="true" data-m="primary"' in junk["el"]["#measures"]["html"]


def test_choosing_an_account_reads_the_report_with_the_range_and_measure_and_remembers_it(reports):
    out = opened(reports["both"])
    assert out["calls"][0] == "/p2/paid-marketing/api/xchannel/report?account=1112223334%2Bact_5550001&range=30&measure=primary"
    assert out["store"]["xc_account"] == ACCT["id"] and out["hist"][-1] == "#pf=both&a=1112223334%2Bact_5550001&r=30&m=primary"
    assert out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True and out["el"]["#empty"]["hidden"] is True


def test_the_whole_report_is_drawn(reports):
    h = opened(reports["both"])["el"]
    assert count(h["#tiles"]["html"], "au-tile") == 5 and "Cost per conversion" in h["#tiles"]["html"]
    assert h["#lead"]["html"].startswith("<p>Google Ads and Meta Ads spent")
    finds = h["#finds"]["html"]
    assert count(finds, '<li class="xc-f') == len(reports["both"]["findings"]) and "xc-t-bad" in finds and "What to do" in finds and "<em>" in finds
    cols = h["#channels"]["html"]
    assert count(cols, 'class="xc-col') == 3 and "Blended" in cols and "none tracked" in cols and "$" in cols
    assert count(h["#shares"]["html"], 'class="xc-share"') == 3
    assert h["#wfCard"]["hidden"] is False and "xc-wf" in h["#wf"]["html"] and "add up to the whole change" in h["#wf"]["html"]
    types = h["#types"]["html"]
    assert count(types, "<tr>") == 5 and "Captures demand" in types and "Creates demand" in types and "none" in types and "xc-l-meta" in types
    assert "<svg" in h["#chartCost"]["html"] and "<svg" in h["#chartConv"]["html"] and count(h["#legend"]["html"], "xc-dot") == 2
    assert count(h["#links"]["html"], "<li") == 4 and "four were run" in h["#linkWhy"]["text"]
    assert h["#attrCard"]["hidden"] is False and count(h["#attr"]["html"], 'class="xc-col') == 2 and "Booked" in h["#attr"]["html"]
    assert h["#recCard"]["hidden"] is False and "Conversions your system recorded" in h["#rec"]["html"]
    assert count(h["#notes"]["html"], "<li>") == len(reports["both"]["notes"])


def test_a_report_without_a_second_platform_has_no_blended_column_and_says_why_there_is_no_split(reports):
    h = opened(reports["one"])["el"]
    assert count(h["#channels"]["html"], 'class="xc-col') == 1 and "Blended" not in h["#channels"]["html"]
    assert h["#wfCard"]["hidden"] is True
    assert "needs both of them" in h["#linkWhy"]["text"] and h["#links"]["html"] == ""
    assert h["#recCard"]["hidden"] is True and h["#attrCard"]["hidden"] is True
    assert count(h["#types"]["html"], "<th>Platform</th>") == 0


def test_too_few_weeks_say_how_many_are_needed(reports):
    h = opened(reports["plain"])["el"]
    assert "11 whole weeks" in h["#linkWhy"]["text"] and h["#links"]["html"] == ""
    assert "no clear link" not in h["#links"]["html"]


def test_the_downloads_carry_the_same_query_as_the_page(reports):
    h = opened(reports["both"])["el"]
    q = "account=1112223334%2Bact_5550001&range=30&measure=primary"
    assert h["#dlXlsx"]["href"] == "/p2/paid-marketing/api/xchannel/download?" + q + "&file=xlsx" and h["#dlPdf"]["href"].endswith(q + "&file=pdf") and h["#dlZip"]["href"].endswith(q + "&file=zip")


def test_the_range_and_the_measure_re_read_the_report_and_the_chips_move(reports):
    out = opened(reports["both"], [{"fire": {"[data-r]": {"data-r": "90"}}}, {"fire": {"[data-m]": {"data-m": "all"}}}])
    assert out["calls"][1].endswith("range=90&measure=primary") and out["calls"][2].endswith("range=90&measure=all")
    assert 'aria-checked="true" data-r="90"' in out["el"]["#ranges"]["html"] and 'aria-checked="true" data-m="all"' in out["el"]["#measures"]["html"]
    assert out["hist"][-1].endswith("&r=90&m=all")


def test_the_persons_own_numbers_are_sent_when_checked_and_dropped_when_cleared(reports):
    steps = [{"set": ["#fConv", " 1,200 "]}, {"set": ["#fValue", "50000"]}, {"click": "#ownGo"}, {"click": "#ownClear"}]
    out = opened(reports["both"], steps)
    assert out["calls"][1].endswith("&actualConv=1%2C200&actualValue=50000") and out["calls"][2].endswith("measure=primary") and "actualConv" not in out["calls"][2]
    assert out["el"]["#fConv"]["value"] == "" and out["el"]["#fValue"]["value"] == ""
    again = opened(reports["both"], [{"click": "#ownClear"}])
    assert len(again["calls"]) == 1                                  # clearing nothing reads nothing


def test_refresh_asks_again_with_refresh_and_a_download_never_does(reports):
    out = opened(reports["both"], [{"click": "#refreshBtn"}])
    assert out["calls"][1].endswith("&refresh=1") and "refresh" not in out["el"]["#dlXlsx"]["href"]


def test_a_failure_is_said_in_words_with_a_hint_for_a_spent_allowance(reports):
    def fail(status, body):
        return page({"responses": [{"match": "/xchannel/report", "status": status, "body": body}], "steps": [{"pick": [ACCT]}]})["el"]
    h = fail(400, {"error": "range should be one of: 30"})
    assert "range should be one of: 30" in h["#msg"]["html"] and "pm-err" in h["#msg"]["html"] and h["#empty"]["hidden"] is False
    h = fail(429, {"error": "Google's allowance is spent.", "kind": "quota"})
    assert "allowance is spent" in h["#msg"]["html"] and "clears on its own" in h["#msg"]["html"]
    h = fail(429, {"error": "Refreshed a moment ago.", "kind": "cooldown"})
    assert "Refreshed a moment ago." in h["#msg"]["html"] and "pm-note" in h["#msg"]["html"] and "clears on its own" not in h["#msg"]["html"]
    h = fail(500, {})
    assert "HTTP 500" in h["#msg"]["html"]


def test_a_failure_after_a_report_keeps_the_report_on_screen(reports):
    out = page({"responses": [{"match": "/xchannel/report", "sequence": [reports["both"], {"error": "down"}], "statuses": [200, 502]}], "steps": [{"pick": [ACCT]}, {"fire": {"[data-r]": {"data-r": "90"}}}]})
    assert "down" in out["el"]["#msg"]["html"] and out["el"]["#out"]["hidden"] is False


def test_no_account_shows_the_empty_state_with_a_hint_per_platform():
    out = page({"steps": [{"pick": [None, "meta"]}]})
    assert "no Meta ad accounts" in out["el"]["#msg"]["html"] and out["el"]["#empty"]["hidden"] is False
    assert "no accounts to show for this platform" in page({"steps": [{"pick": [None, "google"]}]})["el"]["#msg"]["html"]


def test_names_that_hold_markup_are_escaped_everywhere(reports):
    rep = json.loads(json.dumps(reports["both"]))
    evil = "<img src=x onerror=alert(1)>"
    rep["channels"]["meta"]["types"][0]["label"] = evil
    rep["findings"][0]["title"] = evil
    rep["findings"][0]["evidence"] = [evil]
    rep["attribution"]["google"]["counted"][0]["name"] = evil
    rep["notes"] = [evil]
    rep["channels"]["google"]["account"]["name"] = evil
    h = opened(rep)["el"]
    for key in ("#finds", "#types", "#attr", "#notes", "#channels"):
        assert "<img" not in h[key]["html"], key
    assert "&lt;img" in h["#finds"]["html"]


def test_a_saved_read_is_said_to_be_old(reports):
    rep = dict(reports["both"], stale="2026-10-17T08:00:00+00:00")
    h = opened(rep)["el"]
    assert h["#stale"]["hidden"] is False and "saved" in h["#stale"]["text"]
    assert opened(reports["both"])["el"]["#stale"]["hidden"] is True


def test_a_report_the_page_cannot_draw_says_so_instead_of_saying_the_server_is_down(reports):
    broken = dict(reports["both"], platforms=None)
    out = opened(broken)
    assert "could not be drawn" in out["el"]["#msg"]["html"] and "could not be reached" not in out["el"]["#msg"]["html"]


def test_an_answer_that_arrives_late_does_not_replace_a_newer_one(reports):
    out = page({"responses": [{"match": "range=30", "body": reports["plain"], "hold": True}, {"match": "range=90", "body": reports["both"]}],
                "steps": [{"pick": [ACCT]}, {"fire": {"[data-r]": {"data-r": "90"}}}, {"release": True}]})
    lead = out["el"]["#lead"]["html"]
    assert "in the last 90 days" in lead and "in the last 30 days" not in lead and out["el"]["#out"]["hidden"] is False


def test_picking_the_same_account_again_does_not_read_it_again(reports):
    out = opened(reports["both"], [{"pick": [ACCT]}, {"pick": [dict(ACCT)]}])
    assert len(out["calls"]) == 1
    other = opened(reports["both"], [{"pick": [dict(ACCT, id=T.GID)]}])
    assert len(other["calls"]) == 2 and other["calls"][1].startswith("/p2/paid-marketing/api/xchannel/report?account=1112223334&")


def test_when_there_is_no_split_the_card_says_why_instead_of_showing_a_blank(reports):
    rep = json.loads(json.dumps(reports["both"]))
    rep["decomposition"] = {"ok": False, "why": "Meta Ads recorded no conversions in one of the two periods."}
    h = opened(rep)["el"]
    assert h["#wfCard"]["hidden"] is False and "recorded no conversions in one of the two periods" in h["#wf"]["html"] and h["#wfNote"]["text"] == ""


def test_each_share_bar_is_described_for_a_screen_reader_and_labels_only_the_wide_segments(reports):
    rep = json.loads(json.dumps(reports["both"]))
    rep["channels"]["google"]["share"] = {"cost": 90.0, "conv": 55.0, "value": 100.0}
    rep["channels"]["meta"]["share"] = {"cost": 10.0, "conv": 45.0, "value": None}
    html = opened(rep)["el"]["#shares"]["html"]
    assert 'aria-label="Spend: Google Ads 90%, Meta Ads 10%"' in html
    spend = html.split('class="xc-share"')[1]
    assert ">90%</i>" in spend and ">10%</i>" not in spend and 'style="width:10%"></i>' in spend
    assert 'aria-label="Conversion value: Google Ads 100%"' in html


def test_a_share_outside_zero_to_a_hundred_cannot_break_the_bar(reports):
    rep = json.loads(json.dumps(reports["both"]))
    rep["channels"]["google"]["share"]["cost"] = 130.0
    rep["channels"]["meta"]["share"]["cost"] = -5.0
    html = opened(rep)["el"]["#shares"]["html"]
    assert "width:100%" in html and "width:0%" in html and "width:130%" not in html and "width:-5%" not in html


def test_the_script_is_valid_javascript():
    r = subprocess.run(["node", "--check", PAGE], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _plan_html(expr, **data):
    """planHtml cut out of the page script (an IIFE that needs a browser) and run with the page's own escaping."""
    code = ("const fs=require('fs');const src=fs.readFileSync(%s,'utf8');"
            "const a=src.indexOf('/* How to act on a finding'), b=src.indexOf('function paintFindings(rep){');"
            "if(a<0||b<a) throw new Error('function not found');"
            "const esc=function(s){return String(s==null?'':s).replace(/[&<>\"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c];});};"
            "const f=new Function('esc',src.slice(a,b)+';return {planHtml:planHtml};')(esc);" % json.dumps(PAGE))
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    r = subprocess.run(["node", "-e", code + pre + "console.log(JSON.stringify(" + expr + "));"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_findings_plan_is_folded_under_it_escaped_with_its_worth_and_note():
    p = {"steps": ["Open <Goals>.", "Set it."], "worth": "About 40%.", "note": "Not sized."}
    html = _plan_html("f.planHtml(P)", P=p)
    assert html == ('<details class="xc-plan"><summary>How to do it</summary><ol><li>Open &lt;Goals&gt;.</li><li>Set it.</li></ol>'
                    '<p class="xc-worth"><b>Worth</b> About 40%.</p><p class="xc-worth">Not sized.</p></details>')
    assert _plan_html("f.planHtml(P)", P={"steps": ["One."]}).count("xc-worth") == 0
    assert _plan_html("f.planHtml(P)", P=None) == "" and _plan_html("f.planHtml(P)", P={"steps": []}) == ""
    assert "planHtml(x.plan) + '</li>'" in open(PAGE, encoding="utf-8").read()                      # drawn after the action, in the finding's own item
