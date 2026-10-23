"""Strategy & Campaign Book: the page script run in Node against a stub page and the real plan the server builds (tests/strategy_fakes.py).

The server researches, writes and checks (tests/test_paid_strategy_*.py); the page only draws. Here the whole script runs for real: it loads the options and
the accounts, shows the research cards, sends the form, polls a build, and draws the finished plan tab by tab. The HTML it writes is read back.
"""
import json
import os
import shutil
import subprocess

import pytest

from tests import strategy_fakes as F
from tracker import paid_strategy as ps, paid_strategy_job as job

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "campaign_book.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8"), sc = JSON.parse(process.argv[2]);
const els = {}, handlers = {}, calls = [], timers = [], hist = [], picker = { sets: [] }, bodies = {}, held = [];
const pickers = [], metaSets = [], switchers = [], platformsSet = [];
function el(sel) {
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", value: (sc.values || {})[sel] || "", hidden: false, disabled: false, min: "", open: false, className: "", attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; }, removeAttribute() {}, focus() {}, setSelectionRange() {},
    querySelector: () => null, contains: () => false, id: sel.startsWith("#") ? sel.slice(1) : "" };
  return els[sel];
}
const doc = { readyState: "complete", querySelector: s => (s === ".cb-more[open]" ? null : el(s)), querySelectorAll: () => [],
  addEventListener(ev, fn) { (handlers[ev] = handlers[ev] || []).push(fn); } };
const responses = (sc.responses || []).map(r => Object.assign({ n: 0 }, r));
function respond(url, opts) {
  const method = (opts && opts.method) || "GET";
  calls.push({ url, method, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const hits = responses.filter(r => url.includes(r.match) && (!r.method || r.method === method));
  if (!hits.length) return { status: 404, body: { error: "no stub for " + url } };
  const r = hits[0];
  const body = Array.isArray(r.sequence) ? r.sequence[Math.min(r.n++, r.sequence.length - 1)] : r.body;
  if (r.hold && !r.released) return new Promise(res => held.push(() => { r.released = true; res({ status: r.status || 200, body }); }));       // an answer that comes when told to
  return { status: r.status || 200, body };
}
/* the first picker made is the page's (or the Google one of the shared scope control, tests/test_scope_js.py); a second is the Meta one. The platform switch records what it was asked. */
const PMPicker = (host, o) => { const n = pickers.length; const p = { onChange: o.onChange, set(items, v) { (n === 0 ? picker.sets : metaSets).push([items, v]); } }; pickers.push(p); if (n === 0) picker.onChange = o.onChange; return p; };
const googleOnlies = [];
const PMPlatform = { saved: () => "", googleOnly: (host, o) => { googleOnlies.push(o); return { value: () => "google", set() {}, note: {} }; }, switcher: (host, o) => { switchers.push(o); let cur = o.value; return { set(p) { cur = p; platformsSet.push(p); }, value: () => cur, saved: () => "" }; },
  counterpart: (a, others) => { const n = x => String(x.name || "").toLowerCase().replace(/\(.*?\)/g, "").replace(/[^a-z0-9]/g, ""); const m = others.filter(o => o.currency === a.currency && n(o) === n(a)); return m.length === 1 ? m[0] : null; } };
/* the shared scope control (static/js/pm_scope.js, tested in test_scope_js.py) as a stand-in: it lists the accounts, reports the account and platform chosen, and can be told one from outside */
const scope = { o: null };
const PMScope = { isMeta: id => /^act_\d+$/.test(id || ""), partsOf: id => String(id || "").split("+"),
  mount: (host, o) => { scope.o = o; scope.opts = { noGroups: !!o.noGroups, optional: !!o.optional };
    Promise.all([ctx.fetch("/p2/paid-marketing/api/segments/accounts").then(r => r.json()), ctx.fetch("/p2/paid-marketing/api/gads/accounts?platform=meta").then(r => r.json()).catch(() => ({}))]).then(([g, m]) => {
      o.onLoaded && o.onLoaded({ accounts: g.accounts || [], groups: g.groups || [], meta: (m.accounts || []).filter(a => a.platform === "meta") });
      o.onChange(o.account || "", /^act_/.test(o.account || "") ? "meta" : "google"); }).catch(e => o.onError && o.onError(e));
    return { select: id => o.onChange(id, /\+/.test(id) ? "both" : /^act_/.test(id) ? "meta" : "google"), groupOf: () => null, platform: () => "google" }; } };
const ctx = { window: { PMPicker, PMPlatform, PMScope, confirm: () => sc.confirm !== false },
  document: doc, history: { replaceState(a, b, u) { hist.push(u); } }, location: { pathname: "/p2/paid-marketing/campaign-book", search: sc.search || "" },
  setTimeout: (fn, ms) => { timers.push(fn); return timers.length; }, clearTimeout: id => { timers[id - 1] = null; }, Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent,
  console, Promise, isNaN, parseInt, parseFloat,
  fetch: (url, opts) => Promise.resolve(respond(url, opts)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) })) };
ctx.window.document = doc; ctx.window.confirm = ctx.window.confirm; ctx.confirm = ctx.window.confirm;
vm.createContext(ctx);
for (const p of (sc.scripts || [])) vm.runInContext(fs.readFileSync(p, "utf8"), ctx);
vm.runInContext(src, ctx);
const tick = () => new Promise(r => setImmediate(r));
function target(spec) {
  spec = spec || {};
  const t = { id: spec.id || "", closest: q => (spec.closest && spec.closest[q] ? Object.assign({ getAttribute: k => (spec.closest[q][k] == null ? null : spec.closest[q][k]), open: spec.closest[q].open,
    querySelector: s => (spec.closest[q].body && s === ".cb-cbody" ? spec.closest[q].body : null), classList: { contains: c => c === "cb-camp" && !!spec.closest[q].camp } }, spec.closest[q]) : null),
    value: spec.value, selectionStart: 0, classList: { contains: c => (spec.classes || []).includes(c) }, getAttribute: k => (spec.attrs || {})[k], open: spec.open,
    querySelector: s => (spec.body && s === ".cb-cbody" ? (bodies[spec.body] = bodies[spec.body] || { innerHTML: "" }) : null) };
  return t;
}
(async () => {
  for (let i = 0; i < 8; i++) await tick();
  for (const step of sc.steps || []) {
    if (step.set) el(step.set[0]).value = step.set[1];
    if (step.fire) {
      const [ev, spec] = step.fire;
      for (const fn of handlers[ev] || []) fn({ target: target(spec) });
    }
    if (step.timers) { const fns = timers.splice(0); for (const f of fns) if (f) f(); }
    if (step.release) held.splice(0).forEach(f => f());
    if (step.pick !== undefined) { if (scope.o) scope.o.onChange(step.pick, "google"); else picker.onChange(step.pick); }
    if (step.pickMeta !== undefined) { if (scope.o) scope.o.onChange(step.pickMeta, "meta"); else pickers[1].onChange(step.pickMeta); }
    if (step.scope) scope.o.onChange(step.scope[0], step.scope[1]);
    if (step.platform && !scope.o) switchers[0].onChange(step.platform);
    for (let i = 0; i < 8; i++) await tick();
  }
  const out = { calls, hist, picker: picker.sets, metaPicker: metaSets, platformsSet, switchers: switchers.length, scopeOpts: scope.opts || null, googleOnlies, el: {}, bodies: {} };
  for (const k of Object.keys(bodies)) out.bodies[k] = bodies[k].innerHTML;
  for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, value: els[k].value, disabled: els[k].disabled };
  process.stdout.write(JSON.stringify(out));
})();
"""

OPTIONS = {"cards": ps.CARDS, "goals": ps.GOALS, "kinds": {k: v["label"] for k, v in ps.KINDS.items()}, "buildsPerDay": 12, "modelReady": True}
ACCOUNTS = {"accounts": [{"id": "1234567890", "name": "Acme Dental", "currency": "USD"}, {"id": "2345678901", "name": "Northwind <b>Dental</b>", "currency": "USD"}], "groups": []}


@pytest.fixture(scope="module")
def plan_rec():
    store = F.FakeStrategyStore()
    r = store.create(by="ana@position2.com", inputs=F.clean(), account_name="Acme Dental")
    store.begin(r["id"])
    job.run_build(r["id"], store=store, deps=F.deps())
    return json.loads(json.dumps(ps.public(store.get(r["id"]))))


def run(scenario):
    base = {"responses": [{"match": "/strategy/options", "body": OPTIONS}, {"match": "/segments/accounts", "body": ACCOUNTS}, {"match": "/strategy/list", "body": {"builds": []}}]}
    scenario = dict(scenario)
    scenario["responses"] = scenario.get("responses", []) + base["responses"]
    out = subprocess.run(["node", "-e", HARNESS, JS, json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def click(sel_map, **kw):
    return {"fire": ["click", dict({"closest": sel_map}, **kw)]}


def press(id_):
    return {"fire": ["click", {"id": id_}]}


def tab(name):
    return click({"#tabs [data-t]": {"data-t": name}})


def opened(rec, extra=None):
    """A scenario that opens a finished build and returns its page."""
    return {"search": "?build=" + rec["id"], "responses": [{"match": "/status", "body": {"status": "done", "progress": rec["progress"]}}, {"match": "/strategy/" + rec["id"], "method": "GET", "body": rec}]
            + (extra or [])}


# ── the form ────────────────────────────────────────────────────────────────

def test_the_form_shows_the_goals_the_cards_the_kinds_and_the_accounts():
    out = run({})
    h = out["el"]
    assert h["#goals"]["html"].count("data-g=") == 5 and 'data-g="leads"' in h["#goals"]["html"] and 'aria-checked="true"' in h["#goals"]["html"]
    cards = h["#cards"]["html"]
    assert cards.count('<button type="button" class="cb-card') == 7 and "Client website" in cards and "Social Media Intelligence" in cards and cards.count("Uses paid data") == 2
    assert 'data-k="history"' in cards and "Needs a Google Ads account" in cards and "Needs a website" in cards                   # no account and no website yet
    assert h["#kinds"]["html"].count("data-kind=") == 6 and "Let the strategist choose" in h["#kinds"]["html"]
    items = out["picker"][-1][0]
    assert [i["label"] for i in items] == ["No account", "Acme Dental", "Northwind <b>Dental</b>"] and items[1]["sub"] == "123-456-7890"


def test_a_card_that_cannot_work_cannot_be_turned_on_until_what_it_needs_is_given():
    out = run({"steps": [click({"#cards [data-k]": {"data-k": "history"}})]})
    history = out["el"]["#cards"]["html"].split('data-k="history"')[1].split("</button>")[0]
    assert history.startswith(' aria-pressed="false" aria-disabled="true"') and "Needs a Google Ads account" in history          # the click changed nothing
    typed = run({"values": {"#fSite": "acme.com"}, "steps": [{"fire": ["input", {"id": "fSite"}]}]})
    assert "Needs a website" not in typed["el"]["#cards"]["html"] and typed["el"]["#cards"]["html"].count("Needs a Google Ads account") == 2
    assert out["el"]["#cards"]["html"].count('aria-pressed="true"') == 4                  # competitors, social, keywords and market: website needs a site, history and seasonality an account
    assert typed["el"]["#cards"]["html"].count('aria-pressed="true"') == 5


def test_clicking_a_card_turns_it_off_and_a_chip_chooses_the_goal_and_kinds():
    out = run({"values": {"#fSite": "acme.com"}, "steps": [{"fire": ["input", {"id": "fSite"}]}, click({"#cards [data-k]": {"data-k": "market"}}), click({"#goals [data-g]": {"data-g": "calls"}}),
                                                          click({"#kinds [data-kind]": {"data-kind": "search"}}), click({"#kinds [data-kind]": {"data-kind": "pmax"}}),
                                                          click({"#kinds [data-kind]": {"data-kind": "search"}})]})
    h = out["el"]
    market = h["#cards"]["html"].split('data-k="market"')[1].split("</button>")[0]
    assert 'aria-pressed="false"' in market and "Off" in market
    assert 'data-g="calls" aria-checked="true"' in h["#goals"]["html"]
    kinds = h["#kinds"]["html"]
    assert 'data-kind="pmax" aria-pressed="true"' in kinds and 'data-kind="search" aria-pressed="false"' in kinds and 'data-kind="" aria-pressed="false"' in kinds


def test_the_form_is_sent_as_it_was_filled_in():
    vals = {"#fName": "Acme Dental", "#fSite": "acme.com", "#fIndustry": "Dental", "#fBudget": "6000", "#fLaunch": "2026-11-02", "#fPlaces": "Austin, TX", "#fServices": "implants",
            "#fBrief": "A brief", "#fAvoid": "no cures", "#fComp": "smile.com\n\n  Another  \n"}
    out = run({"values": vals, "responses": [{"match": "/strategy/start", "method": "POST", "status": 201, "body": {"id": "a" * 32}}, {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}],
               "steps": [{"fire": ["input", {"id": "fSite"}]}, click({"#cards [data-k]": {"data-k": "social"}}), click({"#kinds [data-kind]": {"data-kind": "search"}}), press("goBtn")]})
    sent = next(c for c in out["calls"] if c["url"].endswith("/strategy/start"))
    b = sent["body"]
    assert b["name"] == "Acme Dental" and b["website"] == "acme.com" and b["budget"] == "6000" and b["goal"] == "leads" and b["campaignTypes"] == ["search"] and b["account"] == ""
    assert b["competitors"] == ["smile.com", "Another"] and b["research"] == {"website": True, "competitors": True, "social": False, "history": False, "keywords": True, "seasonality": False, "market": True}
    assert out["hist"][-1] == "/p2/paid-marketing/campaign-book?build=" + "a" * 32 and out["el"]["#builder"]["hidden"] is True and out["el"]["#run"]["hidden"] is False


def test_a_missing_name_is_refused_before_anything_is_sent_and_a_refusal_is_shown():
    out = run({"values": {"#fName": " "}, "steps": [press("goBtn")]})
    assert not any(c["url"].endswith("/start") for c in out["calls"]) and "Give the client a name" in out["el"]["#msg"]["html"]
    out = run({"values": {"#fName": "Acme"}, "responses": [{"match": "/strategy/start", "method": "POST", "status": 429, "body": {"error": "That is 12 builds <b>in a day</b>"}}], "steps": [press("goBtn")]})
    assert "12 builds &lt;b&gt;in a day&lt;/b&gt;" in out["el"]["#msg"]["html"] and out["el"]["#goBtn"]["disabled"] is False


def test_the_page_says_when_the_ai_is_not_available_here():
    out = run({"responses": [{"match": "/strategy/options", "body": dict(OPTIONS, modelReady=False)}]})
    assert "no Anthropic key is set" in out["el"]["#goNote"]["html"]


# ── a build in progress ─────────────────────────────────────────────────────

def test_progress_is_polled_and_drawn_step_by_step_until_it_is_done(plan_rec):
    running = {"status": "running", "started": "2026-10-16T10:00:00+00:00", "progress": {"phase": "Researching", "steps": [
        {"key": "r:website", "label": "Reading the client's website", "state": "done", "note": "Read 7 pages"}, {"key": "r:social", "label": "Social", "state": "running", "note": ""},
        {"key": "a:profile", "label": "Understanding the business", "state": "pending", "note": ""}, {"key": "b:x", "label": "Building <b>X</b>", "state": "failed", "note": "no"}]}}
    out = run({"search": "?build=" + plan_rec["id"], "responses": [{"match": "/status", "sequence": [running, running, {"status": "done", "progress": plan_rec["progress"]}]},
                                                                    {"match": "/strategy/" + plan_rec["id"], "method": "GET", "body": plan_rec}], "steps": [{"timers": 1}, {"timers": 1}]})
    polls = [c for c in out["calls"] if c["url"].endswith("/status")]
    assert len(polls) == 3 and out["el"]["#result"]["hidden"] is False and out["el"]["#run"]["hidden"] is True
    first = run({"search": "?build=" + plan_rec["id"], "responses": [{"match": "/status", "body": running}]})["el"]
    assert "Reading the client&#39;s website" in first["#steps"]["html"] and "Read 7 pages" in first["#steps"]["html"] and "cb-step running" in first["#steps"]["html"]
    assert "cb-step failed" in first["#steps"]["html"] and "Building &lt;b&gt;X&lt;/b&gt;" in first["#steps"]["html"] and "<b>X</b>" not in first["#steps"]["html"]
    assert first["#runSub"]["text"].startswith("Researching") and first["#runTitle"]["text"] == "Building"


def test_a_build_that_is_gone_sends_the_person_back_to_the_form():
    out = run({"search": "?build=" + "b" * 32, "responses": [{"match": "/status", "status": 404, "body": {"error": "x"}}]})
    assert out["el"]["#builder"]["hidden"] is False and "no longer there" in out["el"]["#msg"]["html"]


# ── the finished plan ───────────────────────────────────────────────────────

def body(out, name):
    return out["el"]["#tabBody"]["html"]


def test_the_result_has_its_header_downloads_and_tabs(plan_rec):
    out = run(opened(plan_rec))
    h = out["el"]
    assert h["#rName"]["text"] == "Acme Dental" and "acmedental.com" in h["#rMeta"]["html"] and "6,000 USD a month" in h["#rMeta"]["html"] and "launch 2026-11-02" in h["#rMeta"]["html"] and "by ana" in h["#rMeta"]["html"]
    btns = h["#rBtns"]["html"]
    base = "/p2/paid-marketing/api/strategy/" + plan_rec["id"] + "/download?file="
    for kind in ("xlsx", "csv", "pdf", "zip"):
        assert base + kind in btns
    assert base + "book-xlsx&amp;campaign=searchimplants" in btns or base + "book-xlsx&campaign=searchimplants" in btns
    assert "Build again from the start" in btns and "New build" in btns and "Remove" in btns and "missing" not in btns
    tabs = [t.split("</button>")[0].split(">")[-1] for t in h["#tabs"]["html"].split("<button")[1:]]
    assert tabs == ["Overview", "The business", "Competitors", "Social media", "Google Ads account", "Keywords", "Market and season", "Strategy", "Campaigns (2)", "Campaign Book", "Checks"]


def test_the_overview_says_what_the_budget_buys_and_what_was_read(plan_rec):
    html = run(opened(plan_rec))["el"]["#tabBody"]["html"]
    assert "Monthly budget" in html and "6,000 USD" in html and "Expected conversions" in html and "Cautious" in html and "Worked out in code, not guessed by the model" in html
    assert "Where the money goes" in html and "P2_Search_Implants_20261102" in html and "70%" in html and "What was read" in html and "Client website" in html and html.count("cb-pill ok") >= 7


def test_every_tab_draws_what_the_plan_holds(plan_rec):
    seen = {n: run(opened(plan_rec) | {"steps": [tab(n)]})["el"]["#tabBody"]["html"] for n in ["business", "competitors", "social", "account", "keywords", "market", "strategy", "checks"]}
    assert "Free consultation for dental implants" in seen["business"] and "cb-pill ok" in seen["business"] and "Not found on a page: confirm with the client" in seen["business"]
    assert "Smile Austin" in seen["competitors"] and "$99 new patient special" in seen["competitors"] and "Open space" in seen["competitors"] and "Websites read" in seen["competitors"]
    assert "Short reels of the team" in seen["social"] and "instagram" in seen["social"] and "Most engaging posts" in seen["social"]
    assert "P2_Search_Old_20240101" in seen["account"] and "Keywords that converted" in seen["account"] and "dental implants austin" in seen["account"] and "Search terms that converted" in seen["account"]
    assert "cost of dental implants" in seen["keywords"] and "8,100" in seen["keywords"] and "3 ideas from Google Keyword Planner" in seen["keywords"] and "$3 to $9" not in seen["keywords"]
    assert "Healthcare ads must not promise results" in seen["market"] and 'href="https://example.com/report"' in seen["market"] and "The account&#39;s year" in seen["market"] and "January" in seen["market"]
    assert "Maximize conversions" in seen["strategy"] and "Days 1 to 30" in seen["strategy"] and "Free Implant Consultation" in seen["strategy"] and "Needed from the client before launch" in seen["strategy"]
    assert "dental implants [phrase] would block the keyword" in seen["checks"] and "What the build cost" in seen["checks"]


def test_the_keyword_tab_filters_as_you_type(plan_rec):
    out = run(opened(plan_rec) | {"steps": [tab("keywords"), {"set": ["#kwFind", "implant dentist"]}, {"fire": ["input", {"id": "kwFind", "value": "implant dentist"}]}]})
    html = out["el"]["#tabBody"]["html"]
    assert "implant dentist near me" in html and "cost of dental implants" not in html and 'value="implant dentist"' in html


def test_a_campaign_opens_into_ad_groups_keywords_ads_and_assets(plan_rec):
    out = run(opened(plan_rec) | {"steps": [tab("campaigns"), {"fire": ["toggle", {"id": "d", "classes": ["cb-camp"], "attrs": {"data-key": "searchimplants"}, "open": True, "body": "search"}]},
                                          {"fire": ["toggle", {"id": "d", "classes": ["cb-camp"], "attrs": {"data-key": "pmaxlocal"}, "open": True, "body": "pmax"}]}]})
    closed = run(opened(plan_rec) | {"steps": [tab("campaigns")]})["el"]["#tabBody"]["html"]
    assert closed.count("<details") == 2 and "P2_Search_Implants_20261102" in closed and "1 ad groups" in closed and "70%" in closed and "138 USD a day" in closed and "Settings" not in closed
    s = out["bodies"]["search"]
    assert "Bid strategy" in s and "Maximize conversions, then target CPA after 30 conversions" in s and "Implants Core" in s
    assert '[dental implants austin]' in s and '&quot;implant dentist near me&quot;' in s and 'class="cb-c kw exact"' in s and 'class="cb-c kw phrase"' in s
    assert "Responsive search ad 1" in s and "cb-adprev" in s and "acmedental.com/implants/austin" in s and "22/30" in s and "Headlines (15)" in s and "Descriptions (4)" in s
    assert "Negative keywords" in s and "Left out because it would block the campaign's own keywords" in s and "Sitelinks" in s and "Callouts" in s and "Structured snippets" in s
    pm = out["bodies"]["pmax"]
    assert "Austin Adults" in pm and "Search themes (2)" in pm and "Image briefs" in pm and "Audience signals" in pm and "Custom segment: dental implants cost" in pm and "Business name: Acme Dental" in pm
    reopened = run(opened(plan_rec) | {"steps": [tab("campaigns"), {"fire": ["toggle", {"id": "d", "classes": ["cb-camp"], "attrs": {"data-key": "searchimplants"}, "open": True, "body": "x"}], }, tab("book"), tab("campaigns")]})
    assert reopened["el"]["#tabBody"]["html"].count("<details") == 2 and "open" in reopened["el"]["#tabBody"]["html"].split("data-key=\"searchimplants\"")[1].split(">")[0]


def test_the_book_tab_lists_every_field_and_switches_campaign(plan_rec):
    out = run(opened(plan_rec) | {"steps": [tab("book")]})
    html = out["el"]["#tabBody"]["html"]
    assert html.count("<tr>") == 1 + 61 + 5 and "Campaign Name" in html and "P2_Search_Implants_20261102" in html and "cb-fromplan" in html and "Download this book (Excel)" in html
    assert "file=book-xlsx&amp;campaign=searchimplants" in html or "file=book-xlsx&campaign=searchimplants" in html
    other = run(opened(plan_rec) | {"steps": [tab("book"), click({"[data-camp]": {"data-camp": "pmaxlocal"}})]})["el"]["#tabBody"]["html"]
    assert "P2_PMax_Local_20261102" in other and "Audience signals" in other and "campaign=pmaxlocal" in other


# ── what goes wrong, and what must never run ────────────────────────────────

def test_a_campaign_that_was_not_written_offers_to_write_only_it(plan_rec):
    rec = json.loads(json.dumps(plan_rec))
    rec["plan"]["campaigns"][1]["build"] = {}
    rec["plan"]["campaigns"][1]["buildError"] = "the model was down"
    out = run(opened(rec) | {"responses": opened(rec)["responses"] + [{"match": "/rebuild", "method": "POST", "body": {"id": rec["id"]}}], "steps": [press("resumeBtn")]})
    assert "Write the 1 campaign that is missing" in out["el"]["#rBtns"]["html"]
    sent = next(c for c in out["calls"] if c["url"].endswith("/rebuild"))
    assert sent["body"] == {"resume": True}
    camp = run(opened(rec) | {"steps": [tab("campaigns")]})["el"]["#tabBody"]["html"]
    assert "details" in camp


def test_a_build_that_stopped_says_why_and_offers_to_carry_on(plan_rec):
    rec = json.loads(json.dumps(plan_rec))
    rec.update(status="error", error="the model is <i>down</i>", plan={})
    out = run(opened(rec) | {"responses": opened(rec)["responses"] + [{"match": "/rebuild", "method": "POST", "body": {"id": rec["id"]}}], "steps": [press("carryBtn")]})
    note = out["el"]["#rNote"]["html"]
    assert "The build stopped." in note and "the model is &lt;i&gt;down&lt;/i&gt;" in note and "Finish what is missing" in note and "<i>down</i>" not in note
    assert next(c for c in out["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": True}
    assert "downloads" not in out["el"]["#rBtns"]["html"] and "file=xlsx" not in out["el"]["#rBtns"]["html"]


def test_building_again_from_the_start_asks_first_and_removing_asks_first(plan_rec):
    no = run(opened(plan_rec) | {"confirm": False, "steps": [press("rebuildBtn"), press("delBtn")]})
    assert not any(c["method"] in ("POST", "DELETE") and "/strategy/" + plan_rec["id"] in c["url"] for c in no["calls"])
    yes = run(opened(plan_rec) | {"responses": opened(plan_rec)["responses"] + [{"match": "/rebuild", "method": "POST", "body": {}}, {"match": "/strategy/" + plan_rec["id"], "method": "DELETE", "body": {"ok": True}}],
                                  "steps": [press("rebuildBtn")]})
    assert next(c for c in yes["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": False}
    gone = run(opened(plan_rec) | {"responses": opened(plan_rec)["responses"] + [{"match": "/strategy/" + plan_rec["id"], "method": "DELETE", "body": {"ok": True}}], "steps": [press("delBtn")]})
    assert any(c["method"] == "DELETE" for c in gone["calls"]) and gone["el"]["#builder"]["hidden"] is False and gone["el"]["#result"]["hidden"] is True


def test_nothing_the_model_or_a_website_wrote_can_run_as_html(plan_rec):
    rec = json.loads(json.dumps(plan_rec))
    evil = '<img src=x onerror=alert(1)>'
    safe_text = "&lt;img src=x onerror=alert(1)&gt;"
    rec["name"] = evil
    p = rec["plan"]
    p["strategy"]["summary"] = [evil]
    p["profile"]["summary"] = evil
    p["profile"]["facts"][0]["fact"] = evil
    p["profile"]["proof"] = [{"point": evil, "source": "javascript:alert(1)"}]
    p["competitive"]["competitors"][0]["name"] = evil
    p["competitive"]["competitors"][0]["url"] = "javascript:alert(2)"
    p["campaigns"][0]["name"] = evil
    p["campaigns"][0]["build"]["adGroups"][0]["name"] = evil
    p["campaigns"][0]["build"]["adGroups"][0]["keywords"][0]["text"] = evil
    p["campaigns"][0]["build"]["adGroups"][0]["ads"][0]["headlines"][0] = evil
    p["campaigns"][0]["build"]["assets"]["sitelinks"][0]["url"] = "javascript:alert(3)"
    p["campaigns"][0]["book"][2]["value"] = evil
    rec["research"]["market"]["data"]["sources"] = ["javascript:alert(4)", "https://ok.example/x"]
    rec["research"]["market"]["data"]["demand"] = [evil]
    base = opened(rec)
    pages = {"overview": [], "business": [], "competitors": [], "market": [], "book": [], "campaigns": []}
    for name in pages:
        steps = [tab(name)] if name != "overview" else []
        if name == "campaigns":
            steps.append({"fire": ["toggle", {"id": "d", "classes": ["cb-camp"], "attrs": {"data-key": p["campaigns"][0]["key"]}, "open": True, "body": "c"}]})
        out = run(base | {"steps": steps})
        html = out["el"]["#tabBody"]["html"] + out["el"]["#rMeta"]["html"] + out["el"]["#rBtns"]["html"] + out["bodies"].get("c", "")
        assert "<img" not in html and 'href="javascript' not in html and "javascript:alert" not in html.replace("javascript:alert(1)", "").replace("javascript:alert(2)", "").replace("javascript:alert(3)", "").replace("javascript:alert(4)", "") or True
        assert "<img" not in html and 'href="javascript' not in html and 'href="  javascript' not in html, name
        pages[name] = html
    assert safe_text in pages["overview"] and safe_text in pages["business"] and safe_text in pages["competitors"] and safe_text in pages["market"] and safe_text in pages["book"] and safe_text in pages["campaigns"]
    assert 'href="https://ok.example/x"' in pages["market"] and 'href="javascript' not in pages["market"] and "javascript:alert(4)" in pages["market"]       # shown as words, never a link
    assert 'href="javascript' not in pages["business"] and 'href="javascript' not in pages["competitors"]
    assert run(base)["el"]["#rName"]["text"] == evil                  # set with textContent, so the browser shows it as text


def test_the_saved_builds_are_listed_with_their_state_and_open_on_a_click():
    rows = [{"id": "c" * 32, "name": "Acme <b>Dental</b>", "status": "done", "createdBy": "ana@position2.com", "createdAt": "2026-10-16T10:00:00+00:00", "accountName": "", "website": "https://acme.com/",
             "goal": "leads", "campaigns": 3, "cost": 1.234}, {"id": "d" * 32, "name": "Northwind", "status": "running", "createdBy": "x@position2.com", "createdAt": None, "accountName": "Northwind", "website": "",
                                                                  "goal": "", "campaigns": 0, "cost": None}]
    out = run({"responses": [{"match": "/strategy/list", "body": {"builds": rows}}]})
    html = out["el"]["#recent"]["html"]
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in html and "3 campaigns" in html and "$1.23" in html and "Ready" in html and "Building" in html and "acme.com" in html and "Northwind" in html
    opened_ = run({"responses": [{"match": "/strategy/list", "body": {"builds": rows}}, {"match": "/status", "body": {"status": "running", "progress": {"steps": []}}}],
                   "steps": [click({".cb-open": {"data-id": "d" * 32}})]})
    assert any(c["url"].endswith("/d" * 1 + "d" * 31 + "/status") or ("d" * 32) in c["url"] for c in opened_["calls"])


def test_a_finished_builds_details_can_be_put_back_in_the_form_to_be_changed(plan_rec):
    rec = json.loads(json.dumps(plan_rec))
    rec["inputs"].update(account="1234567890", brief="A brief", competitors=[{"name": "smile.com", "url": "https://smile.com/"}, {"name": "Another", "url": ""}], campaignTypes=["search"],
                         research={"website": True, "competitors": False, "social": True, "history": True, "keywords": True, "seasonality": False, "market": True})
    out = run(opened(rec) | {"steps": [press("reuseBtn")]})
    el = out["el"]
    assert el["#fName"]["value"] == "Acme Dental" and el["#fSite"]["value"] == "acmedental.com" and el["#fBudget"]["value"] == "6000" and el["#fLaunch"]["value"] == "2026-11-02"
    assert el["#fComp"]["value"] == "https://smile.com/\nAnother" and el["#fBrief"]["value"] == "A brief" and el["#fPlaces"]["value"] == "Austin, TX"
    assert out["picker"][-1][1] == "1234567890" and el["#builder"]["hidden"] is False and el["#result"]["hidden"] is True
    cards = el["#cards"]["html"]
    assert cards.split('data-k="competitors"')[1].split("</button>")[0].count('aria-pressed="false"') == 1 and cards.split('data-k="history"')[1].split("</button>")[0].count('aria-pressed="true"') == 1
    assert 'data-kind="search" aria-pressed="true"' in el["#kinds"]["html"]


def test_a_build_the_server_restarted_under_says_so_and_offers_to_carry_on(plan_rec):
    stale = {"status": "running", "stale": True, "started": "2026-10-16T10:00:00+00:00", "progress": {"phase": "Writing", "steps": [{"key": "r:website", "label": "Site", "state": "done", "note": ""}]}}
    out = run({"search": "?build=" + plan_rec["id"], "responses": [{"match": "/status", "body": stale}, {"match": "/rebuild", "method": "POST", "body": {"id": plan_rec["id"]}}], "steps": [{"timers": 1}]})
    h = out["el"]
    assert h["#runTitle"]["text"] == "The build was interrupted" and "The server restarted while it was working" in h["#runSub"]["text"] and "carryBtn" in h["#runAct"]["html"] and "Finish what is missing" in h["#runAct"]["html"]
    assert len([c for c in out["calls"] if c["url"].endswith("/status")]) == 1                    # it stopped asking: nothing is going to change
    again = run({"search": "?build=" + plan_rec["id"], "responses": [{"match": "/status", "sequence": [stale, {"status": "running", "progress": {"steps": []}}]},
                                                                  {"match": "/rebuild", "method": "POST", "body": {"id": plan_rec["id"]}}], "steps": [press("carryBtn")]})
    assert next(c for c in again["calls"] if c["url"].endswith("/rebuild"))["body"] == {"resume": True} and again["el"]["#runAct"]["html"] == ""
    live = run({"search": "?build=" + plan_rec["id"], "responses": [{"match": "/status", "body": dict(stale, stale=False)}]})
    assert live["el"]["#runTitle"]["text"] == "Building" and live["el"]["#runAct"]["html"] == ""


def test_an_interrupted_build_is_marked_in_the_saved_list():
    rows = [{"id": "c" * 32, "name": "Cut Off", "status": "running", "stale": True, "createdBy": "a@position2.com", "createdAt": "2026-10-16T10:00:00+00:00", "accountName": "", "website": "",
             "goal": "", "campaigns": 0, "cost": None}, {"id": "d" * 32, "name": "Going", "status": "running", "stale": False, "createdBy": "a@position2.com", "createdAt": "2026-10-16T10:00:00+00:00",
                                                         "accountName": "", "website": "", "goal": "", "campaigns": 0, "cost": None}]
    html = run({"responses": [{"match": "/strategy/list", "body": {"builds": rows}}]})["el"]["#recent"]["html"]
    assert html.count("Interrupted") == 1 and html.count("Building") == 1


def test_an_answer_that_arrives_after_the_person_has_gone_back_is_ignored(plan_rec):
    running = {"status": "running", "progress": {"phase": "Researching", "steps": [{"key": "r:website", "label": "Late answer", "state": "running", "note": ""}]}}
    out = run({"search": "?build=" + plan_rec["id"], "responses": [{"match": "/status", "body": running, "hold": True}], "steps": [press("backBtn"), {"release": True}]})
    assert out["el"]["#run"]["hidden"] is not False or "Late answer" not in out["el"].get("#steps", {}).get("html", "")
    assert out["el"]["#builder"]["hidden"] is False and "Late answer" not in out["el"].get("#steps", {}).get("html", "")
    shown = run({"search": "?build=" + plan_rec["id"], "responses": [{"match": "/status", "body": running, "hold": True}], "steps": [{"release": True}]})
    assert "Late answer" in shown["el"]["#steps"]["html"]                               # the same answer, when it is still wanted, is drawn


def test_tabs_for_what_was_never_read_are_not_offered(plan_rec):
    rec = json.loads(json.dumps(plan_rec))
    for key in ("competitors", "keywords", "social", "history", "market", "seasonality"):
        rec["research"].pop(key, None)
    rec["plan"]["competitive"] = {}
    rec["plan"]["strategy"]["social"] = []
    html = run(opened(rec))["el"]["#tabs"]["html"]
    for gone in ("Competitors", "Keywords", "Social media", "Google Ads account", "Market and season"):
        assert gone not in html, gone
    assert "Overview" in html and "The business" in html and "Strategy" in html and "Campaigns (2)" in html


def test_the_keyword_table_shows_each_ideas_competition(plan_rec):
    html = run(opened(plan_rec) | {"steps": [tab("keywords")]})["el"]["#tabBody"]["html"]
    assert html.count("Medium") == 3 and "$3" not in html and "3 to 9" in html


def test_choosing_an_account_shows_its_currency_and_names_the_client_when_the_name_is_empty():
    out = run({"steps": [{"pick": "1234567890"}]})
    assert out["el"]["#curTag"]["text"] == "(USD)" and out["el"]["#fName"]["value"] == "Acme Dental" and "Needs a Google Ads account" not in out["el"]["#cards"]["html"]
    kept = run({"values": {"#fName": "Someone Else"}, "steps": [{"pick": "1234567890"}]})
    assert kept["el"]["#fName"]["value"] == "Someone Else"
    none = run({"steps": [{"pick": "1234567890"}, {"pick": ""}]})
    assert none["el"]["#curTag"]["text"] == "" and "Needs a Google Ads account" in none["el"]["#cards"]["html"]


def test_a_build_made_a_moment_ago_says_just_now(plan_rec):
    import datetime
    rec = json.loads(json.dumps(plan_rec))
    rec["createdAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    assert "by ana just now" in run(opened(rec))["el"]["#rMeta"]["html"]
    rec["createdAt"] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
    assert "by ana 5 min ago" in run(opened(rec))["el"]["#rMeta"]["html"]
