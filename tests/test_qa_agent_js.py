"""The QA Agent page script (static/js/qa_agent.js) run in Node against a stub page and the real payloads the server builds: Google, Meta and Both from the shared control, the campaigns of each
account in one list, a QC started on the right account, and each QC drawn with its own platform's checklist."""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

from tests.test_paid_qa import API, SAME, STAFF, ACCOUNT, CAMPAIGN, NEW as GOOGLE_NEW, client_as, mem  # noqa: F401  (the fixture is the module's own)
from tests.test_paid_qa_meta import ACT, CAMP
from tests.test_paid_qa_meta_routes import NEW as META_NEW, meta  # noqa: F401
from tracker import paid_qa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "qa_agent.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8"), sc = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const els = {}, handlers = {}, calls = [], hist = [], store = {};
function mk(sel) {
  const kids = {};
  return { sel, innerHTML: "", textContent: "", value: "", hidden: false, disabled: false, className: "", attrs: {}, dataset: {}, children: [],
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; }, focus() {}, appendChild(c) { this.children.push(c); },
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector(s) { return kids[s] = kids[s] || mk(sel + " " + s); }, contains: () => false, closest: () => null,
    addEventListener(ev, fn) { (handlers[sel + "|" + ev] = handlers[sel + "|" + ev] || []).push(fn); } };
}
function el(sel) { return els[sel] = els[sel] || mk(sel); }
const doc = { readyState: "complete", querySelector: el, querySelectorAll: () => [], getElementById: id => el("#" + id), createElement: () => mk("created"), body: { appendChild() {} },
  addEventListener(ev, fn) { (handlers["document|" + ev] = handlers["document|" + ev] || []).push(fn); } };
const responses = (sc.responses || []).map(r => Object.assign({ n: 0 }, r));
function respond(url, opts) {
  const method = (opts && opts.method) || "GET";
  calls.push({ url, method, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const hits = responses.filter(r => url.includes(r.match) && (!r.method || r.method === method));
  if (!hits.length) return { status: 404, body: { error: "no stub for " + url } };
  return { status: hits[0].status || 200, body: hits[0].body };
}
const scope = { o: null };
const PMScope = { isMeta: id => /^act_\d+$/.test(id || ""), partsOf: id => String(id || "").split("+"),
  mount: (host, o) => { scope.o = o; scope.opts = { noGroups: !!o.noGroups, optional: !!o.optional };
    Promise.all([ctx.fetch("/p2/paid-marketing/api/segments/accounts").then(r => r.json()), ctx.fetch("/p2/paid-marketing/api/gads/accounts?platform=meta").then(r => r.json()).catch(() => ({}))]).then(([g, m]) => {
      o.onLoaded && o.onLoaded({ accounts: g.accounts || [], groups: g.groups || [], meta: (m.accounts || []).filter(a => a.platform === "meta") });
      o.onChange(o.account || "", /\+/.test(o.account || "") ? "both" : /^act_/.test(o.account || "") ? "meta" : "google"); }).catch(e => o.onError && o.onError(e));
    return { select: id => o.onChange(id, /\+/.test(id) ? "both" : /^act_/.test(id) ? "meta" : "google") }; } };
const ctx = { window: { PMScope }, document: doc, history: { replaceState(a, b, u) { hist.push(u); } }, location: { pathname: "/p2/paid-marketing/qa-agent", search: sc.search || "" },
  localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } }, navigator: {}, URLSearchParams,
  setTimeout: (fn, ms) => 1, clearTimeout() {}, Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, console, isNaN, parseInt, parseFloat,
  fetch: (url, opts) => Promise.resolve(respond(url, opts)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) })) };
ctx.window.document = doc;
vm.createContext(ctx);
vm.runInContext(src, ctx);
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  for (let i = 0; i < 12; i++) await tick();
  for (const step of sc.steps || []) {
    if (step.scope) scope.o.onChange(step.scope[0], step.scope[1]);
    if (step.fire) { const [sel, ev, spec] = step.fire; for (const fn of handlers[sel + "|" + ev] || []) fn({ target: Object.assign({ closest: () => null }, spec || {}) }); }
    for (let i = 0; i < 12; i++) await tick();
  }
  const out = { calls, hist, scopeOpts: scope.opts || null, store, el: {} };
  for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, value: els[k].value, disabled: els[k].disabled };
  process.stdout.write(JSON.stringify(out));
})();
"""

GOOGLE_ACCOUNTS = {"accounts": [{"id": ACCOUNT, "name": "Example Co", "currency": "USD"}], "groups": []}
META_ACCOUNTS = {"accounts": [{"id": ACT, "name": "Acme Meta", "currency": "USD", "platform": "meta"}]}


def checklists():
    return [{"match": "/qa/checklist?platform=meta", "body": paid_qa.checklist("meta")}, {"match": "/qa/checklist", "body": paid_qa.checklist("google")}]


def run(scenario):
    base = checklists() + [{"match": "/segments/accounts", "body": GOOGLE_ACCOUNTS}, {"match": "/gads/accounts", "body": META_ACCOUNTS}]
    scenario = dict(scenario, responses=scenario.get("responses", []) + base)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(scenario, f)
    try:
        out = subprocess.run(["node", "-e", HARNESS, JS, f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def started(c, new):
    return c.post(API + "/start", json=new, headers=SAME).get_json()


def campaigns(*items):
    return {"campaigns": list(items)}


def test_the_shared_control_is_mounted_for_one_account_per_platform_and_both_checklists_are_loaded():
    out = run({})
    assert out["scopeOpts"] == {"noGroups": True, "optional": False}
    urls = [c["url"] for c in out["calls"]]
    assert API + "/checklist" in urls and API + "/checklist?platform=meta" in urls


def test_a_meta_account_lists_its_campaigns_with_their_type_and_starts_the_qc_on_the_meta_account(meta):
    c = client_as(STAFF)
    payload = started(c, META_NEW)
    out = run({"search": "?account=%s&campaign=%s" % (ACT, CAMP["id"]), "responses": [
        {"match": "/qa/campaigns?account=" + ACT, "body": campaigns({"id": CAMP["id"], "name": CAMP["name"], "channel": "META_LEADS", "type": "Meta Leads", "qc": None})},
        {"match": "/qa/start", "method": "POST", "status": 201, "body": payload}]})
    req = next(x for x in out["calls"] if x["url"].endswith("/qa/start"))["body"]
    assert req == {"accountId": ACT, "accountName": "Acme Meta", "campaignId": CAMP["id"], "campaignName": CAMP["name"], "channel": "META_LEADS"}
    assert out["store"]["qa_account"] == ACT and out["hist"][-1] == "/p2/paid-marketing/qa-agent?account=%s&campaign=%s" % (ACT, CAMP["id"])
    sections = out["el"]["#sections"]["html"]
    assert "Campaign Objective" in sections and "Conversion Location" in sections and "1. Campaign Setup" in sections and "AI Max" not in sections
    assert "Position2 Meta launch checks" in out["el"]["#foot"]["html"] or "Meta launch checks" in out["el"]["#foot"]["html"]
    assert out["el"]["#wName"]["text"] == CAMP["name"] and "Acme Meta" in out["el"]["#wMeta"]["html"] and "Meta Leads" in out["el"]["#wMeta"]["html"]


def test_a_google_account_still_uses_the_campaign_book_checks_and_the_google_wording(mem):
    c = client_as(STAFF)
    payload = started(c, GOOGLE_NEW)
    out = run({"search": "?account=%s&campaign=%s" % (ACCOUNT, CAMPAIGN), "responses": [
        {"match": "/qa/campaigns?account=" + ACCOUNT, "body": campaigns({"id": CAMPAIGN, "name": "P2_Search_20261014", "channel": "SEARCH", "type": "Search", "qc": None})},
        {"match": "/qa/start", "method": "POST", "status": 201, "body": payload}]})
    sections = out["el"]["#sections"]["html"]
    assert "Ad copies" in sections and "Campaign Objective" not in sections and ">Google Ads</span>" in sections and "Campaign Book checks" in out["el"]["#foot"]["html"] and "Google Ads" in out["el"]["#foot"]["html"]


def test_both_lists_the_campaigns_of_the_two_accounts_together_and_each_is_started_on_its_own_account(meta):
    c = client_as(STAFF)
    payload = started(c, META_NEW)
    out = run({"search": "?account=%s%%2B%s&campaign=%s" % (ACCOUNT, ACT, CAMP["id"]), "responses": [
        {"match": "/qa/campaigns?account=" + ACCOUNT, "body": campaigns({"id": CAMPAIGN, "name": "P2_Search_Brand", "channel": "SEARCH", "type": "Search", "qc": None})},
        {"match": "/qa/campaigns?account=" + ACT, "body": campaigns({"id": CAMP["id"], "name": CAMP["name"], "channel": "META_LEADS", "type": "Meta Leads", "qc": None})},
        {"match": "/qa/start", "method": "POST", "status": 201, "body": payload}]})
    asked = [x["url"] for x in out["calls"] if "/qa/campaigns" in x["url"]]
    assert set(asked) == {API + "/campaigns?account=" + ACCOUNT, API + "/campaigns?account=" + ACT}
    assert next(x for x in out["calls"] if x["url"].endswith("/qa/start"))["body"]["accountId"] == ACT
    assert out["hist"][-1].replace("%2B", "+").startswith("/p2/paid-marketing/qa-agent?account=%s+%s" % (ACCOUNT, ACT))


def test_one_account_failing_under_both_is_said_and_the_other_is_still_listed(meta):
    out = run({"search": "?account=%s%%2B%s" % (ACCOUNT, ACT), "responses": [
        {"match": "/qa/campaigns?account=" + ACCOUNT, "body": campaigns({"id": CAMPAIGN, "name": "P2_Search_Brand", "channel": "SEARCH", "type": "Search", "qc": None})},
        {"match": "/qa/campaigns?account=" + ACT, "status": 502, "body": {"error": "Meta rejected the access token", "kind": "meta"}}]})
    assert "Acme Meta: Meta rejected the access token" in out["el"]["#msg"]["html"] and "The campaigns of the other account are shown" in out["el"]["#msg"]["html"]
    assert out["el"]["#empty"]["hidden"] is False and "Choose campaigns" in out["el"]["#emptyT"]["text"]


def test_every_account_failing_says_why_and_a_meta_account_does_not_offer_to_type_the_campaign_in(meta):
    out = run({"search": "?account=" + ACT, "responses": [{"match": "/qa/campaigns?account=" + ACT, "status": 502, "body": {"error": "Meta rejected the access token", "kind": "meta"}}]})
    assert "Meta rejected the access token" in out["el"]["#msg"]["html"] and "goManual" not in out["el"]["#msg"]["html"] and out["el"]["#emptyT"]["text"] == "The campaigns could not be loaded"
    g = run({"search": "?account=" + ACCOUNT, "responses": [{"match": "/qa/campaigns?account=" + ACCOUNT, "status": 502, "body": {"error": "Google Ads answered 403"}}]})
    assert "Google Ads answered 403" in g["el"]["#msg"]["html"] and "goManual" in g["el"]["#msg"]["html"]


def test_the_campaign_picker_is_labelled_for_both_platforms_and_a_meta_qc_says_meta_where_it_says_where_the_findings_came_from(meta):
    c = client_as(STAFF)
    payload = started(c, META_NEW)
    out = run({"search": "?account=%s&campaign=%s" % (ACT, CAMP["id"]), "responses": [
        {"match": "/qa/campaigns?account=" + ACT, "body": campaigns({"id": CAMP["id"], "name": CAMP["name"], "channel": "META_LEADS", "type": "Meta Leads", "qc": None})},
        {"match": "/qa/start", "method": "POST", "status": 201, "body": payload}]})
    html = out["el"]["#sections"]["html"]
    assert ">Meta</span>" in html and ">Google Ads</span>" not in html and "AI Max" not in html
