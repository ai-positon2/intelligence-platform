"""Ad Copy Checker: static/js/copycheck_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads the ads and runs the rules (tests/test_paid_copycheck*.py). The page only draws, so how a status and a level read, which accounts a view shows, how a text is cut into plain and
marked parts and the CSV come from copycheck_data.js (executed here, not read), and copycheck.js is run for real: it loads the accounts and what the team saved, draws the report the server
built, marks the characters a finding points at, saves a word or a list and says what went wrong in words. The HTML it writes is read back.
"""
import json
import os
import re
import shutil
import subprocess

import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_copycheck as T
from tracker import paid_copycheck as cc
from tracker import paid_copycheck_settings as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "copycheck_data.js"), os.path.join(JS, "copycheck.js")
TEMPLATE = os.path.join(ROOT, "templates", "ad_copy_checker.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [T.ACCT, T.ACCT2, {"id": "3333333333", "name": "Gamma <b>&</b> Co", "currency": "USD"}, {"id": "4444444444", "name": "Delta", "currency": "USD"}]


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
    bad = T.rsa(ad="1", heads=("Affordble <b>Dentst</b> & Co", "Quality Dental Care"), descs=("Call 617-555-0199 now!!", "We offer implants"), camp="Core <Search>", group="Implants")
    placeholder = T.rsa(ad="2", heads=("{Brand} Implants", "Quality Dental Care"), descs=("We offer implants.",), camp="Core <Search>", group="Cleanings")
    small = T.rsa(ad="3", heads=("Quality Dental Care", "Call Now For FREEBIES today"), descs=("We offer implants.",), camp="Brand", group="Offers")
    search = {T.ACCT["id"]: [bad, placeholder, small], T.ACCT2["id"]: [small], "3333333333": [T.rsa(ad="9")], "4444444444": []}
    s = T.searcher(search=search)
    mon = MemStore()
    st.save_account(mon, T.ACCT["id"], "Acme Dental", {"phones": "617-555-0100"}, "a@b.co")
    group = {"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in ACCOUNTS]}
    kw = dict(accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [group], search_fn=s, today=T.TODAY, speller=T.speller())
    out = {"one": cc.report_for(T.ACCT["id"], mon=mon, **kw), "mcc": cc.report_for(GROUP, mon=mon, **kw)}
    out["meta"] = cc.report_for("act_1234567", search_fn=s, meta_pages_fn=lambda p, q: [T.meta_ad("1", body="Affordble care now!!", title="Dentst")], meta_account_fn=lambda a: {"name": "Beta Meta", "currency": "EUR"},
                                today=T.TODAY, speller=T.speller())
    out["clean"] = cc.report_for("3333333333", **kw)
    yield out
    mp.undo()
    paid_geo.clear_cache()


# ── the data helpers ────────────────────────────────────────────────────────

def test_a_status_and_a_level_read_in_words_and_an_unknown_one_reads_as_itself():
    assert run("X.statusInfo('fix')") == {"label": "Fix these", "tone": "bad"} and run("X.statusInfo('clean')")["tone"] == "ok" and run("X.statusInfo('zz')") == {"label": "zz", "tone": "unk"}
    assert run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('q')") == {"label": "q", "tone": "unk"}
    assert [run("X.slotText(%s)" % json.dumps(k)) for k in ("headline", "primary", "path", "sitelinkdesc", "zz", None)] == ["Headline", "Primary text", "Display path", "Sitelink line", "zz", ""]
    assert [run("X.kindText(%s)" % json.dumps(k)) for k in ("rsa", "pmax", "meta", "zz", None)] == ["Responsive search ad", "Performance Max asset group", "Meta ad", "Ad", "Ad"]


def test_a_text_is_cut_into_plain_and_marked_parts_that_read_back_as_the_text():
    segs = run("X.segments(T, F)", T="Affordble Dentst near you", F=[{"start": 10, "end": 16}, {"start": 0, "end": 9}])
    assert segs == [{"t": "Affordble", "f": [1]}, {"t": " ", "f": []}, {"t": "Dentst", "f": [0]}, {"t": " near you", "f": []}] and "".join(s["t"] for s in segs) == "Affordble Dentst near you"
    assert run("X.segments(T, F)", T="abc", F=[]) == [{"t": "abc", "f": []}] and run("X.segments(T, F)", T="", F=[]) == [{"t": "", "f": []}]
    over = run("X.segments(T, F)", T="Dentst near", F=[{"start": 0, "end": 6}, {"start": 3, "end": 9}, {"start": 0, "end": 11}])
    assert [s["f"] for s in over] == [[2]] and over[0]["t"] == "Dentst near"                                                 # the widest one that starts first wins, the overlapping ones are left unmarked
    assert run("X.segments(T, F)", T="abc", F=[{"start": 2, "end": 9}, {"start": -1, "end": 2}, {"start": 1, "end": 1}]) == [{"t": "abc", "f": []}]      # out of range or empty: never marked
    whole = run("X.segments(T, F)", T="ALL CAPS", F=[{"start": 0, "end": 8}])
    assert whole == [{"t": "ALL CAPS", "f": [0]}]


def test_the_lines_of_an_ad_are_the_ones_with_findings_grouped_by_slot_and_text_in_the_order_they_first_come():
    item = {"findings": [{"slot": "description", "text": "A", "rule": "x"}, {"slot": "headline", "text": "A", "rule": "y"}, {"slot": "description", "text": "A", "rule": "z"}, {"slot": "headline", "text": "B", "rule": "w"}]}
    got = run("X.lines(I)", I=item)
    assert [(g["slot"], g["text"], [f["rule"] for f in g["findings"]]) for g in got] == [("description", "A", ["x", "z"]), ("headline", "A", ["y"]), ("headline", "B", ["w"])]
    assert run("X.lines(I)", I={}) == [] and run("X.lines(I)", I={"findings": []}) == []


def test_only_a_word_can_be_marked_right_and_the_word_is_lower_case_letters():
    assert [run("X.canAllow({rule: %s})" % json.dumps(r)) for r in ("spelling", "vocab", "odd_caps", "caps_word", "phone", "placeholder", "gap")] == [True, True, True, True, False, False, False]
    assert run("X.wordOf(F)", F={"excerpt": "DenTist's"}) == "dentist's" and run("X.wordOf(F)", F={"excerpt": "A1-b"}) == "a-b" and run("X.wordOf(F)", F={}) == ""


def test_how_long_an_ad_has_been_wrong_is_said_with_how_often_it_came_back():
    assert run("X.agePhrase({})") == "" and run("X.agePhrase({ageDays: 0})") == "first seen today" and run("X.agePhrase({ageDays: 1})") == "open 1 day"
    assert run("X.agePhrase({ageDays: 12, reopened: 2})") == "open 12 days, came back 2x" and run("X.agePhrase({ageDays: 0, reopened: 1})") == "first seen today, came back 1x"


def test_the_tiles_say_what_is_to_fix_and_the_views_hold_the_accounts_that_need_a_look(reports):
    t = run("X.tiles(R)", R=reports["mcc"])
    assert [x[0] for x in t] == ["Ads read", "Ads to fix", "Small things", "Findings", "Accounts to fix"] and t[0] == ["Ads read", "5", "1 with nothing wrong", ""] and t[1] == ["Ads to fix", "2", "2 high, 0 medium", "warn"] and t[3][1] == "8" and t[4] == ["Accounts to fix", "1", "1 with small things only", "warn"]
    clean = run("X.tiles(R)", R=reports["clean"])
    assert clean[1] == ["Ads to fix", "0", "none has a slip that shows", "good"] and clean[2][2] == "nothing to tidy" and clean[4][3] == "good"
    assert [a["name"] for a in run("X.shown(R, 'attention')", R=reports["mcc"])] == ["Acme Dental", "Beta Clinic"] and len(run("X.shown(R, 'all')", R=reports["mcc"])) == 4
    assert run("X.firstView(R)", R=reports["mcc"]) == "attention" and run("X.firstView(R)", R=reports["clean"]) == "all" and run("X.viewCount(R, 'all')", R=reports["mcc"]) == 4


def test_what_only_shows_across_the_ads_is_said_in_sentences():
    r = {"account": {"isGroup": True}, "accounts": [{"name": "A", "spellingChecked": False, "phones": [{"number": "617-555-0100", "ads": 2}, {"number": "617-555-0199", "ads": 1}], "phonesSaved": 0,
                                                      "vocab": [{"word": "dentst", "ads": 3}], "brandsRead": [{"brand": "SmileCo", "forms": {"SmileCo": 3, "Smile Co": 1}}]},
                                                     {"name": "B", "spellingChecked": True, "phones": [{"number": "1", "ads": 1}, {"number": "2", "ads": 1}], "phonesSaved": 1, "vocab": [], "brandsRead": [{"brand": "X", "forms": {"X": 2}}]}]}
    got = run("X.insights(R)", R=r)
    assert [g["kind"] for g in got] == ["spelling", "phones", "vocab", "brand"]
    assert got[0]["text"] == "A: Spelling was not checked: the dictionary is not installed on the server." and got[1]["text"].startswith("A: The ads carry 2 different phone numbers (617-555-0100 in 2 ads, 617-555-0199 in 1 ad).")
    assert got[2]["text"] == "A: Words the dictionary does not know that the ads use often: dentst (3 ads). If they are right, add them to the allowed words." and got[3]["text"] == "A: The brand is written 2 ways: \"SmileCo\" (3), \"Smile Co\" (1)."
    single = run("X.insights(R)", R={"account": {"isGroup": False}, "accounts": [dict(r["accounts"][0], spellingChecked=True, vocab=[], brandsRead=[])]})
    assert single[0]["text"].startswith("The ads carry 2 different phone numbers") and len(single) == 1


def test_the_csv_has_a_line_per_finding_and_one_for_an_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    head = run("X.HEAD")
    assert rows[0] == ["Ad Copy Checker", "Group"] and rows[3] == head and head[6] == "Level" and head[-1] == "Open for days"
    body = rows[4:]
    assert all(len(r) == len(head) for r in body) and len(body) == 10                   # Acme's 7 findings, Beta's 1, and one line each for Gamma and Delta, which have none
    first = next(r for r in body if r[0] == "Acme Dental" and r[7] == "Possible misspelling")
    assert body[0][6:8] == ["High", "Unfilled placeholder"] and [r[7] for r in body[:5]] == ["Unfilled placeholder", "Phone number", "Possible misspelling", "Possible misspelling", "Doubled punctuation"]
    assert first[1] == "Google Ads" and first[2] == "Core <Search>" and first[3] == "Group Implants" and first[4] == "1" and first[5] == "Responsive search ad" and first[6] == "Medium" and first[8] == "Headline"
    assert first[9] == "Affordble <b>Dentst</b> & Co" and first[10] == "Affordble" and first[12] == "affordable"
    assert [r for r in body if r[0] == "Delta"] == [["Delta", "Google Ads", "(nothing found)"] + [""] * 11] and len([r for r in body if r[2] == "(nothing found)"]) == 2
    assert run("X.ruleLabel(R, 'phone')", R=reports["mcc"]) == "Phone number" and run("X.ruleLabel(R, 'zz')", R=reports["mcc"]) == "zz"


# ── the page script ─────────────────────────────────────────────────────────

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const paths = JSON.parse(process.argv[1]), sc = JSON.parse(process.argv[2]);
const els = {}, calls = [], posts = [], hist = [], store = {}, downloads = [], held = [], panels = [];
function el(sel) {
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", value: (sc.values || {})[sel] || "", hidden: false, href: "", handlers: {}, disabled: false,
    addEventListener(ev, fn) { this.handlers[ev] = fn; }, setAttribute() {}, getAttribute() { return null; }, appendChild() {}, className: "" };
  return els[sel];
}
const doc = { querySelector: s => el(s), createElement: () => { const e = el("a" + downloads.length); e.click = () => downloads.push({ href: e.href, name: e.download }); e.remove = () => {}; return e; },
  body: { appendChild() {} }, addEventListener() {}, activeElement: null };
const responses = (sc.responses || []).map(r => Object.assign({ n: 0 }, r));
function respond(url, opts) {
  const method = (opts && opts.method) || "GET";
  calls.push(url);
  if (method === "POST") posts.push({ url, body: JSON.parse(opts.body) });
  const hits = responses.filter(r => url.includes(r.match) && (r.method || "GET") === method);
  if (!hits.length) return { status: 404, body: { error: "no stub for " + url } };
  const r = hits[0], i = Array.isArray(r.sequence) ? Math.min(r.n++, r.sequence.length - 1) : 0;
  const body = JSON.parse(JSON.stringify(Array.isArray(r.sequence) ? r.sequence[i] : r.body));
  const answer = { status: (r.statuses ? r.statuses[i] : r.status) || 200, body };
  r.count = (r.count || 0) + 1;
  if ((r.hold || (r.holdFrom && r.count >= r.holdFrom)) && !r.released) return new Promise(res => held.push(() => { r.released = true; res(answer); }));
  return answer;
}
let picker = null; const sets = [], setsMeta = [], pickers = [], switchers = [], platformsSet = [];
const win = { MonitorPanel: { mount: (host, o) => panels.push(o) },
  PMPicker: (host, o) => { const n = pickers.length, p = { onChange: o.onChange, set(items, v, dis) { (n === 0 ? sets : setsMeta).push({ items, v, dis }); } }; pickers.push(p); if (n === 0) picker = p; return p; },
  /* the platform switch of tracker pages: records what it was asked, and finds the same client on the other platform by name and currency */
  PMPlatform: { switcher: (host, o) => { switchers.push(o); let cur = o.value; return { set(p) { cur = p; platformsSet.push(p); }, value: () => cur, saved: () => "" }; },
    counterpart: (a, others) => { const n = x => String(x.name || "").toLowerCase().replace(/\(.*?\)/g, "").replace(/[^a-z0-9]/g, ""); const m = others.filter(o => o.currency === a.currency && n(o) === n(a)); return m.length === 1 ? m[0] : null; } } };
const ctx = { window: win, document: doc, history: { replaceState(a, b, u) { hist.push(u); } }, location: { search: sc.search || "", pathname: "/p2/paid-marketing/ad-copy-checker" },
  localStorage: { getItem: k => (k in store ? store[k] : (sc.stored || {})[k] || null), setItem: (k, v) => { store[k] = v; } },
  URLSearchParams, URL: { createObjectURL: b => { downloads.blob = b; return "blob:x"; }, revokeObjectURL() {} }, Blob: class { constructor(p) { this.text = p.join(""); } },
  setTimeout: fn => fn(), Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, console, isNaN, parseInt, parseFloat,
  fetch: (url, opts) => (sc.reject && url.includes(sc.reject) ? (calls.push(url), Promise.reject(new Error("down"))) :
    Promise.resolve(respond(url, opts)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) }))) };
ctx.window.document = doc; win.fetch = ctx.fetch; win.localStorage = ctx.localStorage;
vm.createContext(ctx);
for (const p of paths) vm.runInContext(fs.readFileSync(p, "utf8"), ctx);
const tick = () => new Promise(r => setImmediate(r));
const btns = [];
function target(spec) {
  spec = spec || {};
  const outs = {};
  const make = s => ({ dataset: s.dataset || {}, disabled: false, closest: q => (spec.closest && spec.closest[q] ? make(spec.closest[q]) : null),
    querySelectorAll: () => (s.areas || []).map(a => ({ dataset: { k: a.k }, value: a.value })), querySelector: q => (outs[q] = outs[q] || { textContent: "" }) });
  const t = make(spec); t.outs = outs; return t;
}
const targets = [];
(async () => {
  for (let i = 0; i < 6; i++) await tick();
  for (const step of sc.steps || []) {
    if (step.pick) picker.onChange(step.pick);
    if (step.pickMeta) pickers[1].onChange(step.pickMeta);
    if (step.platform) switchers[0].onChange(step.platform);
    if (step.set) el(step.set[0]).value = step.set[1];
    if (step.click) el(step.click).handlers.click({});
    if (step.release) held.splice(0).forEach(f => f());
    if (step.fire) { const [sel, closest] = step.fire; const t = target({ closest }); targets.push(t); el(sel).handlers.click({ target: t }); }
    if (step.submit) { const [sel, closest] = step.submit; const t = target({ closest }); targets.push(t); el(sel).handlers.submit({ target: t, preventDefault() {} }); }
    for (let i = 0; i < 6; i++) await tick();
  }
  const out = { calls, posts, panels, hist, store, sets, setsMeta, platformsSet, switchers: switchers.length, downloads: downloads.map(d => ({ name: d.name })), blob: downloads.blob ? downloads.blob.text : "", el: {},
    outs: targets.map(t => Object.fromEntries(Object.entries(t.outs).map(([k, v]) => [k, v.textContent]))), disabled: targets.map(t => Object.values(t.closest ? [] : []).length) };
  for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, value: els[k].value };
  process.stdout.write(JSON.stringify(out));
})();
"""
SCOPE = os.path.join(JS, "pm_scope.js")
SCRIPTS = [SCOPE, DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}
SETTINGS = {"accounts": {"1234567890": {"phones": ["617-555-0100"], "places": [], "brand": [], "allow": [], "name": "Acme Dental"}}, "allow": ["zoom"], "changes": []}


def page(scenario):
    out = subprocess.run(["node", "-e", HARNESS, json.dumps(SCRIPTS), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(report, steps=None, **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": report}, {"match": "/copycheck/settings", "body": SETTINGS}],
          "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("monitor_panel.js") < html.index("copycheck_data.js") < html.index("copycheck.js")
    assert "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_the_accounts_and_what_the_team_saved_are_loaded_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/settings", "body": SETTINGS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and "MCC" in items[1]["badge"] and len(items) == 6 and out["panels"] == [{"agent": "copycheck", "label": "Ad Copy Checker"}]
    assert any(c.endswith("/copycheck/settings") for c in out["calls"]) and not any("/copycheck/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True
    assert "#globalAllow" not in out["el"]                                                                           # drawn with the report, not before


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports["one"])
    assert [c for c in out["calls"] if "/copycheck/report" in c] == ["/p2/paid-marketing/api/copycheck/report?account=1234567890"]
    assert out["store"]["cc_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/ad-copy-checker?account=1234567890" and out["el"]["#out"]["hidden"] is False
    assert out["el"]["#globalAllow"]["value"] == "zoom"


def test_the_report_is_drawn_worst_first_with_the_marked_characters_and_markup_escaped(reports):
    out = opened(reports["one"])
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Ads read", "Ads to fix", "Small things", "Findings", "Accounts to fix"):
        assert text in kp, text
    assert 'class="bi-row stopped open"' in lst and "bi-pill bad" in lst and "Fix these" in lst and '<span class="bi-n">2</span><span class="bs-sub">ads to fix · 2 high</span>' in lst
    assert lst.index("Unfilled placeholder") < lst.index("Possible misspelling")                                          # the table of what was found, high first
    assert '<mark class="cc-hl lv-medium" title="&quot;Affordble&quot; is not a word I know. Did you mean &quot;affordable&quot;?">Affordble</mark>' in lst
    assert "&lt;b&gt;" in lst and "<b>Dentst" not in lst and "Core &lt;Search&gt; / Group Implants" in lst
    assert '<mark class="cc-hl lv-high"' in lst and "{Brand}" in lst and "Fill in or remove the placeholders in 2 ads" in lst         # a literal <b> in the copy is a placeholder too
    assert "Fix plan" in lst and "Check again the next day" in lst and '<details open>' in lst
    assert "read " in out["el"]["#fresh"]["html"] and "Acme Dental" in out["el"]["#fresh"]["html"] and "the ones to fix first" in out["el"]["#listNote"]["text"]


def test_the_phone_table_says_which_numbers_are_on_the_saved_list_and_a_form_holds_what_is_saved(reports):
    out = opened(reports["one"])
    lst = out["el"]["#list"]["html"]
    assert "Phone numbers in the ads" in lst and "<td>617-555-0199</td><td class=\"r\">1</td><td><b>no</b></td>" in lst
    assert '<form class="cc-set" data-account="1234567890" data-name="Acme Dental">' in lst and ">617-555-0100</textarea>" in lst and 'data-k="allow"' in lst and "Words that are right" in lst
    meta = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": reports["one"]}, {"match": "/copycheck/settings", "body": {"accounts": {}, "allow": []}}], "steps": [{"pick": "1234567890"}]})
    assert "no list saved" in meta["el"]["#list"]["html"]


def test_a_clean_account_says_so_and_a_group_opens_to_the_accounts_that_need_a_look(reports):
    out = opened(reports["clean"])
    assert "Clean" in out["el"]["#list"]["html"] and "nothing wrong in 1 ad" in out["el"]["#list"]["html"] and "Nothing to flag" in out["el"]["#list"]["html"]
    mcc = opened(reports["mcc"], [{"pick": GROUP}])
    lst = mcc["el"]["#list"]["html"]
    assert lst.index("Acme Dental") < lst.index("Beta Clinic") and "Gamma" not in lst and 'class="bi-row stopped"' in lst and 'class="bi-row limited"' in lst
    assert "Needs a look <span class=\"n\">2</span>" in mcc["el"]["#views"]["html"] and "MCC · 4 accounts checked together" in mcc["el"]["#fresh"]["html"]
    allv = opened(reports["mcc"], [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])["el"]["#list"]["html"]
    assert "Gamma &lt;b&gt;&amp;&lt;/b&gt; Co" in allv and "<b>&</b>" not in allv and "Delta" in allv and "bi-pill ok" in allv


def test_a_meta_account_is_drawn_with_its_own_slot_names(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": reports["meta"]}, {"match": "/copycheck/settings", "body": SETTINGS}], "steps": [{"pick": "1234567890"}]})
    lst = out["el"]["#list"]["html"]
    assert 'class="bi-mk">Meta</span>' in lst and "Primary text" in lst and "Meta ad" in lst and "Camp / Set" in lst and "Possible misspelling" in lst


def test_a_word_marked_right_is_saved_for_the_account_and_the_report_is_read_again_without_the_skeleton(reports):
    steps = [{"fire": ["#list", {".cc-ok": {"dataset": {"account": "1234567890", "name": "Acme Dental", "word": "affordble"}}}]}]
    out = opened(reports["one"], steps, responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": reports["one"]}, {"match": "/copycheck/settings", "body": SETTINGS},
                                                    {"match": "/copycheck/settings/allowed-word", "method": "POST", "body": {"account": {"phones": ["617-555-0100"], "places": [], "brand": [], "allow": ["affordble"]}}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/copycheck/settings/allowed-word", "body": {"account": "1234567890", "words": "affordble"}}]
    assert len([c for c in out["calls"] if "/copycheck/report" in c]) == 2 and out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True


def test_a_word_that_cannot_be_saved_says_why_and_a_blank_word_is_not_sent(reports):
    out = opened(reports["one"], [{"fire": ["#list", {".cc-ok": {"dataset": {"account": "1234567890", "name": "A", "word": "x1"}}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": reports["one"]}, {"match": "/copycheck/settings", "body": SETTINGS},
                            {"match": "/copycheck/settings/allowed-word", "method": "POST", "status": 400, "body": {"error": "x1 is not a word (letters, apostrophes and hyphens only)."}}])
    assert "x1 is not a word" in out["el"]["#msg"]["html"] and len([c for c in out["calls"] if "/copycheck/report" in c]) == 1
    blank = opened(reports["one"], [{"fire": ["#list", {".cc-ok": {"dataset": {"account": "1234567890", "name": "A", "word": ""}}}]}])
    assert blank["posts"] == []


def test_saving_the_lists_of_an_account_sends_what_was_typed_and_reads_the_report_again(reports):
    areas = [{"k": "phones", "value": "617-555-0100\n617-555-0199"}, {"k": "places", "value": "Quincy, MA"}, {"k": "brand", "value": "SmileCo"}, {"k": "allow", "value": "invisalign"}]
    out = opened(reports["one"], [{"submit": ["#list", {".cc-set": {"dataset": {"account": "1234567890", "name": "Acme Dental"}, "areas": areas}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": reports["one"]}, {"match": "/copycheck/settings", "body": SETTINGS},
                            {"match": "/copycheck/settings/account", "method": "POST", "body": {"account": {"phones": ["617-555-0100", "617-555-0199"], "places": ["Quincy, MA"], "brand": ["SmileCo"], "allow": ["invisalign"]}}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/copycheck/settings/account", "body": {"account": "1234567890", "name": "Acme Dental", "phones": "617-555-0100\n617-555-0199", "places": "Quincy, MA", "brand": "SmileCo", "allow": "invisalign"}}]
    assert out["outs"][0]["[data-out]"] == "Saved. Checking again…" and len([c for c in out["calls"] if "/copycheck/report" in c]) == 2


def test_a_list_the_server_refuses_says_why_and_nothing_is_read_again(reports):
    out = opened(reports["one"], [{"submit": ["#list", {".cc-set": {"dataset": {"account": "1234567890", "name": "A"}, "areas": [{"k": "phones", "value": "12"}]}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": reports["one"]}, {"match": "/copycheck/settings", "body": SETTINGS},
                            {"match": "/copycheck/settings/account", "method": "POST", "status": 400, "body": {"error": "12 is not a ten digit US phone number."}}])
    assert out["outs"][0]["[data-out]"] == "12 is not a ten digit US phone number." and len([c for c in out["calls"] if "/copycheck/report" in c]) == 1


def test_the_words_for_every_account_are_saved_from_the_box(reports):
    out = opened(reports["one"], [{"set": ["#globalAllow", "zoom, invisalign"]}, {"click": "#globalSave"}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": reports["one"]}, {"match": "/copycheck/settings", "body": SETTINGS},
                            {"match": "/copycheck/settings/allow", "method": "POST", "body": {"allow": ["zoom", "invisalign"]}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/copycheck/settings/allow", "body": {"words": "zoom, invisalign"}}]
    assert out["el"]["#globalOut"]["text"] == "Saved. Checking again…" and len([c for c in out["calls"] if "/copycheck/report" in c]) == 2


def test_more_ads_are_shown_by_the_button_and_the_rest_are_said_to_be_in_the_csv(reports):
    many = json.loads(json.dumps(reports["one"]))
    a = many["accounts"][0]
    a["items"] = [dict(a["items"][1], id=str(i)) for i in range(60)]
    out = opened(many)
    assert out["el"]["#list"]["html"].count('class="cc-ad"') == 25 and "The first 25 of 60" in out["el"]["#list"]["html"] and "show 25 more" in out["el"]["#list"]["html"]
    more = opened(many, [{"fire": ["#list", {".cc-more": {"dataset": {"account": "1234567890"}}}]}])
    assert more["el"]["#list"]["html"].count('class="cc-ad"') == 50 and "show 10 more" in more["el"]["#list"]["html"]


def test_a_cooldown_a_spent_quota_and_a_dead_server_each_say_so_in_words(reports):
    cool = opened({"error": "Refreshed a moment ago.", "kind": "cooldown", "retryIn": 90}, [], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "status": 429, "body": {"error": "Refreshed a moment ago.", "kind": "cooldown"}}])
    assert "Refreshed a moment ago." in cool["el"]["#msg"]["html"] and "pm-note" in cool["el"]["#msg"]["html"]
    quota = opened({}, [], responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "status": 429, "body": {"error": "The daily limit is used up.", "kind": "quota"}}])
    assert "The daily limit is used up." in quota["el"]["#msg"]["html"] and "clears on its own" in quota["el"]["#msg"]["html"]
    down = opened({}, [], reject="/copycheck/report")
    assert "could not be reached" in down["el"]["#msg"]["html"]


def test_an_mcc_says_it_takes_a_little_longer_while_it_reads(reports):
    held = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/settings", "body": SETTINGS}, {"match": "/copycheck/report", "body": reports["mcc"], "hold": True}], "steps": [{"pick": GROUP}]})
    assert "Reading all 4 accounts of Group together" in held["el"]["#msg"]["html"] and held["el"]["#skel"]["hidden"] is False
    done = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/settings", "body": SETTINGS}, {"match": "/copycheck/report", "body": reports["mcc"], "hold": True}], "steps": [{"pick": GROUP}, {"release": True}]})
    assert done["el"]["#msg"]["html"] == "" and done["el"]["#out"]["hidden"] is False


def test_the_csv_download_is_one_line_a_finding_with_a_byte_order_mark_and_no_formula_runs(reports):
    out = opened(reports["one"], [{"click": "#exportBtn"}])
    assert out["downloads"][0]["name"].startswith("ad-copy-Acme-Dental-") and out["downloads"][0]["name"].endswith(".csv") and out["blob"].startswith("﻿")
    lines = out["blob"][1:].split("\r\n")
    assert lines[0] == "Ad Copy Checker,Acme Dental" and lines[3].startswith("Account,Platform,Campaign,Ad group,Ad id") and any(l.startswith('Acme Dental,Google Ads,Core <Search>,Group Implants,1,Responsive search ad,Medium,Possible misspelling,Headline,Affordble <b>Dentst</b> & Co,Affordble,') for l in lines)
    risky = json.loads(json.dumps(reports["one"]))
    risky["accounts"][0]["items"][0]["campaign"] = "=HYPERLINK(1)"
    assert "'=HYPERLINK(1)" in opened(risky, [{"click": "#exportBtn"}])["blob"]


def test_a_second_choice_ignores_an_answer_that_comes_back_late(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/settings", "body": SETTINGS},
                              {"match": "/copycheck/report?account=1234567890", "body": reports["one"], "hold": True}, {"match": "/copycheck/report?account=3333333333", "body": reports["clean"]}],
                "steps": [{"pick": "1234567890"}, {"pick": "3333333333"}, {"release": True}]})
    assert "Clean" in out["el"]["#list"]["html"] and "Affordble" not in out["el"]["#list"]["html"]


def test_the_tiles_name_the_accounts_to_fix_and_the_ones_with_small_things_apart():
    r = {"summary": {"ads": 9, "clean": 3, "high": 1, "medium": 1, "low": 4, "findings": 12, "accounts": 5, "counts": {"fix": 2, "check": 1, "clean": 2}}}
    assert run("X.tiles(R)", R=r)[4] == ["Accounts to fix", "2", "1 with small things only", "warn"] and run("X.tiles(R)", R=r)[3] == ["Findings", "12", "in 6 ads", ""]
    assert run("X.tiles(R)", R={"summary": dict(r["summary"], counts={"fix": 0, "check": 0, "clean": 5})})[4] == ["Accounts to fix", "0", "of 5 accounts read", "good"]


def test_the_csv_has_how_long_an_ad_has_been_wrong_when_it_is_known_and_nothing_when_it_is_not(reports):
    r = json.loads(json.dumps(reports["one"]))
    r["accounts"][0]["items"][0]["ageDays"] = 12
    rows = run("X.csvRows(R)", R=r)[4:]
    first = [x for x in rows if x[4] == r["accounts"][0]["items"][0]["id"]]
    other = [x for x in rows if x[4] != r["accounts"][0]["items"][0]["id"]]
    assert first and all(x[-1] == 12 for x in first) and other and all(x[-1] == "" for x in other)


def test_the_plain_parts_of_a_line_are_escaped_and_only_a_word_gets_the_button_to_say_it_is_right(reports):
    lst = opened(reports["one"])["el"]["#list"]["html"]
    assert "</mark>&lt;/b&gt; &amp; Co" in lst and "</b> & Co" not in lst and ">&lt;b&gt;</mark>" in lst
    assert lst.count('class="cc-ok"') == 3 and lst.count('data-word="affordble"') == 1 and lst.count('data-word="dentst"') == 1 and lst.count('data-word="freebies"') == 1       # not on a placeholder, a phone number or a doubled mark


def test_marking_a_word_right_keeps_the_open_accounts_open_and_keeps_showing_the_report_while_it_reads_again(reports):
    responses = [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/settings", "body": SETTINGS}, {"match": "/copycheck/report", "body": reports["mcc"], "holdFrom": 2},
                 {"match": "/copycheck/settings/allowed-word", "method": "POST", "body": {"account": {"phones": [], "places": [], "brand": [], "allow": ["affordble"]}}}]
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]},
             {"fire": ["#list", {".cc-ok": {"dataset": {"account": "1234567890", "name": "Acme Dental", "word": "affordble"}}}]}]
    out = page({"responses": responses, "steps": steps})
    assert 'class="bi-row stopped open"' in out["el"]["#list"]["html"] and out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True and len(out["posts"]) == 1


def test_the_lists_just_saved_are_what_the_form_shows_next(reports):
    areas = [{"k": "phones", "value": "617-555-0100\n617-555-0199"}]
    saved = {"phones": ["617-555-0100", "617-555-0199"], "places": [], "brand": [], "allow": []}
    out = opened(reports["one"], [{"submit": ["#list", {".cc-set": {"dataset": {"account": "1234567890", "name": "Acme Dental"}, "areas": areas}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": reports["one"]}, {"match": "/copycheck/settings", "body": SETTINGS},
                            {"match": "/copycheck/settings/account", "method": "POST", "body": {"account": saved}}])
    assert "617-555-0100\n617-555-0199</textarea>" in out["el"]["#list"]["html"]


def test_an_account_that_was_open_stays_open_after_a_word_is_marked_right_and_the_report_is_read_again(reports):
    responses = [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/settings", "body": SETTINGS}, {"match": "/copycheck/report", "body": reports["mcc"]},
                 {"match": "/copycheck/settings/allowed-word", "method": "POST", "body": {"account": {"phones": [], "places": [], "brand": [], "allow": ["affordble"]}}}]
    opened_row = {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}
    before = page({"responses": responses, "steps": [{"pick": GROUP}]})["el"]["#list"]["html"]
    after = page({"responses": responses, "steps": [{"pick": GROUP}, {"fire": ["#list", opened_row]},
                                                    {"fire": ["#list", {".cc-ok": {"dataset": {"account": "1234567890", "name": "Acme Dental", "word": "affordble"}}}]}]})
    assert 'class="bi-row stopped open"' not in before and 'class="bi-row stopped open"' in after["el"]["#list"]["html"] and len([c for c in after["calls"] if "/copycheck/report" in c]) == 2


# ── Claude's read of the grammar ────────────────────────────────────────────

GRAMMAR = {"checked": 2, "of": 2, "account": {"id": "1234567890", "name": "Acme Dental"}, "issues": [
    {"text": "Their is a <b>clinic</b> near you", "slots": ["headline"], "ads": 3, "where": ["Core <Search>", "Brand"], "excerpt": "Their is", "fix": "There is", "start": 0, "end": 8, "why": "Their shows ownership & there is meant."}]}
GRAM_CLICK = {"fire": ["#list", {".cc-gram": {"dataset": {"account": "1234567890"}}}]}


def grammar_scene(body=None, steps=None, **kw):
    responses = [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": T_REPORT[0]}, {"match": "/copycheck/settings", "body": SETTINGS},
                 dict({"match": "/copycheck/grammar", "method": "POST", "body": body or GRAMMAR}, **kw)]
    return page({"responses": responses, "steps": [{"pick": "1234567890"}] + (steps or [])})


T_REPORT = []


@pytest.fixture(autouse=True)
def _grammar_report(reports):
    T_REPORT[:] = [reports["one"]]
    yield


def test_what_claude_found_is_said_in_a_sentence_and_what_it_could_not_do_is_said_too():
    assert run("X.grammarNote(G)", G={"checked": 12, "of": 12, "issues": []}) == "No error of grammar or wording found in 12 lines."
    assert run("X.grammarNote(G)", G={"checked": 1, "of": 1, "issues": []}) == "No error of grammar or wording found in 1 line."
    assert run("X.grammarNote(G)", G={"checked": 80, "of": 300, "issues": [{}, {}]}) == "2 errors of grammar or wording found in the first 80 of 300 lines."
    assert run("X.grammarNote(G)", G={"checked": 4, "of": 4, "issues": [{}]}) == "1 error of grammar or wording found in 4 lines."
    assert run("X.grammarNote(G)", G={"checked": 0, "of": 9, "issues": [], "unavailable": "not_configured"}) == "Claude could not be reached (not configured), so grammar was not read. The other checks stand."


def test_an_account_offers_the_grammar_read_and_nothing_is_asked_until_it_is_pressed():
    out = grammar_scene()
    assert "Read the grammar with Claude" in out["el"]["#list"]["html"] and 'data-account="1234567890"' in out["el"]["#list"]["html"] and out["posts"] == []


def test_pressing_the_button_asks_for_the_account_and_draws_the_errors_with_the_marked_words_and_markup_escaped():
    out = grammar_scene(steps=[GRAM_CLICK])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/copycheck/grammar", "body": {"account": "1234567890"}}]
    lst = out["el"]["#list"]["html"]
    assert "1 error of grammar or wording found in 2 lines." in lst and '<mark class="cc-hl lv-medium">Their is</mark> a &lt;b&gt;clinic&lt;/b&gt; near you' in lst and "There is" in lst and "Their shows ownership &amp; there is meant." in lst
    assert "Headline · Core &lt;Search&gt;, Brand" in lst and '<td class="r">3</td>' in lst and "Read the grammar with Claude" not in lst and "<b>clinic</b>" not in lst


def test_while_claude_reads_the_page_says_so_and_the_button_is_gone():
    out = grammar_scene(steps=[GRAM_CLICK], hold=True)
    lst = out["el"]["#list"]["html"]
    assert "Claude is reading the lines of Acme Dental" in lst and "Read the grammar with Claude" not in lst and "cc-gram" not in lst


def test_a_read_that_fails_says_why_and_offers_to_try_again():
    out = grammar_scene(steps=[GRAM_CLICK], status=429, body={"error": "That is 30 grammar reads in a day, the most this allows."})
    lst = out["el"]["#list"]["html"]
    assert "30 grammar reads in a day" in lst and "Try again" in lst and 'class="pm-btn-ghost cc-gram"' in lst
    nobody = grammar_scene(steps=[GRAM_CLICK], status=500, body={})
    assert "The grammar could not be read (HTTP 500)." in nobody["el"]["#list"]["html"]
    gone = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/copycheck/report", "body": T_REPORT[0]}, {"match": "/copycheck/settings", "body": SETTINGS}],
                 "reject": "/copycheck/grammar", "steps": [{"pick": "1234567890"}, GRAM_CLICK]})
    assert "The server could not be reached. Try again." in gone["el"]["#list"]["html"]


def test_a_model_that_could_not_be_reached_is_said_in_the_panel_and_a_refresh_forgets_what_was_read():
    out = grammar_scene(body={"checked": 0, "of": 5, "issues": [], "unavailable": "api_error"}, steps=[GRAM_CLICK])
    assert "Claude could not be reached (api error), so grammar was not read." in out["el"]["#list"]["html"]
    again = grammar_scene(steps=[GRAM_CLICK, {"click": "#refreshBtn"}])
    assert "Read the grammar with Claude" in again["el"]["#list"]["html"] and "There is" not in again["el"]["#list"]["html"]


def test_an_ad_says_how_many_of_its_other_lines_have_nothing_wrong_in_the_right_number(reports):
    lst = opened(reports["one"])["el"]["#list"]["html"]
    rests = [it["lineCount"] - len({(f["slot"], f["text"]) for f in it["findings"]}) for it in reports["one"]["accounts"][0]["items"]]
    assert rests == [4, 4, 4] and lst.count("4 other lines of this ad have nothing wrong.") == 3
    one = json.loads(json.dumps(reports["one"]))
    for it in one["accounts"][0]["items"]:
        it["lineCount"] = len({(f["slot"], f["text"]) for f in it["findings"]}) + 1
    lst = opened(one)["el"]["#list"]["html"]
    assert lst.count("1 other line of this ad has nothing wrong.") == 3 and "other lines" not in lst
    none = json.loads(json.dumps(reports["one"]))
    for it in none["accounts"][0]["items"]:
        it["lineCount"] = len({(f["slot"], f["text"]) for f in it["findings"]})
    assert "other line" not in opened(none)["el"]["#list"]["html"]
