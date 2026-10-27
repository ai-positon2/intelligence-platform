"""The Master Agent: static/js/master_agent_data.js run in Node, and the page script run against a stub page and the real answers the server gives.

The server keeps and checks the clients (tests/test_paid_master*.py). The page only lists, edits and words them, so the shapes come from master_agent_data.js (executed here, not read) and what a form
turns into is held against the server's own cleaning, and master_agent.js is run for real against a stub page.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import paid_master as m  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "master_agent_data.js"), os.path.join(JS, "master_agent.js")
TEMPLATE = os.path.join(ROOT, "templates", "master_agent.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

G, G2, MA = "1234567890", "2222222222", "act_1234567"
CLIENT = {"name": "Acme <b>Dental</b>", "accounts": [{"id": G, "budget": {"monthly": "12,000", "weeklyMin": 2000, "weeklyMax": "3500"}, "targets": {"cpa": "85", "cvr": "4"}, "tolerance": "25"}, {"id": MA}],
          "shared": {"phones": "617-555-0100\n617-555-0111", "locations": "Quincy, MA", "areas": "Greater Boston", "brandTerms": "acme dental, acme", "writtenAs": "AcmeDental", "allowWords": "invisalign, zoomwhitening",
                     "domains": "acmedental.com\nacmedental.net", "competitors": "Tandem Diabetes | tandemdiabetes.com | TandemDiabetes\nInsulet | omnipod.com | | AR111111111111", "offers": [{"text": "Free whitening | exam", "ends": "2026-12-31"}, {"text": "Spring special", "ends": ""}], "notes": "Closed Sundays."}}
ACCOUNTS = [{"id": G, "name": "Acme Dental", "platform": "google"}, {"id": G2, "name": "Beta Clinic", "platform": "google"}, {"id": MA, "name": "Acme Meta", "platform": "meta"}]


def saved_state():
    s = m.save_client(__import__("monitor_fakes").MemStore(), "", CLIENT, "ana@position2.com")
    return dict(s)


def run(expr, **data):
    pre = "".join("const %s=%s;" % (key, json.dumps(v)) for key, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def client():
    return saved_state()


def state_of(client, drift=()):
    return {"clients": [client], "drift": list(drift), "changes": [{"at": "2026-10-27T09:00:00+00:00", "by": "ana@position2.com", "what": "saved the client"}], "usedBy": m.USED_BY}


# ── the wording and the shapes ──────────────────────────────────────────────

def test_a_field_says_which_agents_read_it_and_one_nobody_reads_says_so():
    assert run("X.usedBy('phones', M)", M=m.USED_BY) == "Read by Ad Copy Checker" and run("X.usedBy('offers', M)", M=m.USED_BY) == "Not read by an agent yet"
    assert run("X.usedBy('x', {x: ['A', 'B', 'C']})") == "Read by A, B and C" and run("X.usedBy('x', {x: ['A', 'B']})") == "Read by A and B" and run("X.usedBy('zz', {})") == "Not read by an agent yet"
    assert run("X.fieldLabel('targets.cpa')") == "Cost per conversion" and run("X.fieldLabel('mystery')") == "mystery"


def test_a_client_reads_in_one_line(client):
    assert run("X.clientSummary(C)", C=client) == "2 accounts, 10 of 10 shared fields filled"
    assert run("X.clientSummary(C)", C={"accounts": {G: {}}, "shared": {"phones": ["617-555-0100"]}}) == "1 account, 1 of 10 shared fields filled"
    assert run("X.filled(C)", C={}) == [0, 10]


def test_the_accounts_no_client_owns_are_listed_google_first_by_name(client):
    s = state_of(client)
    assert run("X.ownersOf(S)", S=s) == {G: "Acme <b>Dental</b>", MA: "Acme <b>Dental</b>"}
    more = ACCOUNTS + [{"id": "3333333333", "name": "alpha Co", "platform": "google"}, {"id": "act_999999", "name": "Aaa Meta first alphabetically", "platform": "meta"}]
    assert [a["name"] for a in run("X.unassigned(S, A)", S=s, A=more)] == ["alpha Co", "Beta Clinic", "Aaa Meta first alphabetically"]


def test_the_tiles_say_what_is_covered_and_what_differs(client):
    t = run("X.tiles(S, A)", S=state_of(client), A=ACCOUNTS)
    assert [x[0] for x in t] == ["Clients", "Accounts covered", "Without a client", "Settings that differ"]
    assert t[0][1:] == ["1", "written down once", ""] and t[1][1:3] == ["2", "of 3 accounts the platform lists"] and t[2][1:] == ["1", "no master figures for these yet", "warn"] and t[3][1:] == ["0", "no agent disagrees with the master", "good"]
    all_in = run("X.tiles(S, A)", S=state_of(client), A=[a for a in ACCOUNTS if a["id"] in (G, MA)])
    assert all_in[1][3] == "good" and all_in[2][1:] == ["0", "every account has a client", "good"]
    empty = run("X.tiles(S, A)", S={"clients": [], "drift": []}, A=[])
    assert empty[0][2] == "none yet: add the first" and empty[2][3] == "good"
    d = run("X.tiles(S, A)", S=state_of(client, [{"account": G, "client": "x", "agent": "KPI Monitoring", "field": "targets.cpa", "master": 85, "saved": 70}]), A=ACCOUNTS)
    assert d[3][1:] == ["1", "an agent's own saved value wins: settle them", "warn"]


def test_what_the_form_turns_into_is_what_the_server_saved_so_nothing_is_lost_on_the_way(client):
    form = run("X.formOf(C)", C=client)
    assert form["name"] == "Acme <b>Dental</b>" and form["accounts"][G]["monthly"] == "12000" and form["accounts"][G]["tolerance"] == "25" and form["accounts"][MA]["monthly"] == ""
    assert form["shared"]["phones"] == "617-555-0100\n617-555-0111" and form["shared"]["brandTerms"] == "acme dental, acme" and form["offers"] == "Free whitening | exam | 2026-12-31\nSpring special"
    assert form["shared"]["competitors"] == "Tandem Diabetes | tandemdiabetes.com | https://www.facebook.com/TandemDiabetes\nInsulet | omnipod.com |  | AR111111111111"
    sent = run("X.payload(F, [%s, %s])" % (json.dumps(G), json.dumps(MA)), F=form)
    back = m.clean_client(sent)
    assert back == {k: client[k] for k in ("name", "accounts", "shared")}                         # the form read back is the client saved
    assert run("X.payload(X.formOf(null), [])") == {"name": "", "accounts": [], "shared": {"phones": "", "locations": "", "areas": "", "brandTerms": "", "writtenAs": "", "allowWords": "", "domains": "",
                                                                                       "competitors": "", "offers": [], "notes": ""}}


def test_an_offer_line_splits_at_the_last_bar():
    assert run("X.offersOf('Free exam | worth 50 | 2026-12-31\\n\\nSpring special\\n  ')") == [{"text": "Free exam | worth 50", "ends": "2026-12-31"}, {"text": "Spring special", "ends": ""}]


def test_a_difference_is_a_sentence_and_a_row():
    d = {"account": G, "client": "Acme", "agent": "KPI Monitoring", "field": "targets.cpa", "master": 85, "saved": 70}
    assert run("X.driftText(D)", D=d) == "KPI Monitoring has 70 for cost per conversion and the master says 85. The agent's own wins."
    assert run("X.driftRows({drift: [D]}, N)", D=d, N={G: "Acme Dental"}) == [{"account": "Acme Dental", "client": "Acme", "agent": "KPI Monitoring", "field": "Cost per conversion", "master": "85", "saved": "70"}]
    assert run("X.valueText([1, 'a'])") == "1, a" and run("X.valueText(null)") == "" and run("X.valueText(1234.5)") == "1,234.5"


def test_the_csv_has_one_line_per_figure_and_per_shared_list(client):
    rows = run("X.csvRows(S, N)", S=state_of(client), N={G: "Acme Dental", MA: "Acme Meta"})
    assert rows[0] == ["Master Agent"] and rows[2] == run("X.HEAD")
    body = rows[3:]
    assert ["Acme <b>Dental</b>", "Acme Dental", "Google Ads", "Monthly budget", "12000"] in body and ["Acme <b>Dental</b>", "Acme Dental", "Google Ads", "Tolerance", "25"] in body
    assert ["Acme <b>Dental</b>", "(whole client)", "", "Phone numbers", "617-555-0100, 617-555-0111"] in body and ["Acme <b>Dental</b>", "(whole client)", "", "Offer", "Free whitening | exam (ends 2026-12-31)"] in body
    assert ["Acme <b>Dental</b>", "(whole client)", "", "Competitors", "Tandem Diabetes, Insulet"] in body
    assert not [r for r in body if r[1] == "Acme Meta"]


# ── the page script, run against a stub page ────────────────────────────────

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const paths = JSON.parse(process.argv[1]), sc = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const els = {}, calls = [], posts = [], downloads = [];
function el(sel) {
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", value: (sc.values || {})[sel] || "", hidden: false, href: "", handlers: {}, dataset: {}, disabled: false,
    addEventListener(ev, fn) { this.handlers[ev] = fn; }, setAttribute() {}, appendChild() {}, className: "" };
  return els[sel];
}
const doc = { querySelector: s => el(s), createElement: () => { const e = el("a" + downloads.length); e.click = () => downloads.push({ name: e.download }); e.remove = () => {}; return e; }, body: { appendChild() {} } };
const responses = (sc.responses || []).map(r => Object.assign({ n: 0 }, r));
function respond(url, opts) {
  const method = (opts && opts.method) || "GET";
  calls.push(method + " " + url);
  if (method === "POST") posts.push({ url, body: JSON.parse(opts.body) });
  const hits = responses.filter(r => url.includes(r.match) && (r.method || "GET") === method);
  if (!hits.length) return { status: 404, body: { error: "no stub for " + url } };
  const r = hits[0], i = Array.isArray(r.sequence) ? Math.min(r.n++, r.sequence.length - 1) : 0;
  return { status: (r.statuses ? r.statuses[i] : r.status) || 200, body: JSON.parse(JSON.stringify(Array.isArray(r.sequence) ? r.sequence[i] : r.body)) };
}
const ctx = { window: {}, document: doc, URL: { createObjectURL: b => { downloads.blob = b; return "blob:x"; }, revokeObjectURL() {} }, Blob: class { constructor(p) { this.text = p.join(""); } },
  setTimeout: fn => fn(), Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, console, isNaN, parseInt, parseFloat,
  fetch: (url, opts) => (sc.reject && url.includes(sc.reject) ? (calls.push("GET " + url), Promise.reject(new Error("down"))) : Promise.resolve(respond(url, opts)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) }))) };
ctx.window.document = doc;
vm.createContext(ctx);
for (const p of paths) vm.runInContext(fs.readFileSync(p, "utf8"), ctx);
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  for (let i = 0; i < 8; i++) await tick();
  for (const step of sc.steps || []) {
    if (step.set) el(step.set[0]).value = step.set[1];
    if (step.click) el(step.click).handlers.click({ target: {} });
    if (step.fire) { const [sel, ev, closest] = step.fire; el(sel).handlers[ev]({ target: { closest: q => (closest && closest[q]) || null } }); }
    for (let i = 0; i < 8; i++) await tick();
  }
  const out = { calls, posts, downloads, blob: downloads.blob ? downloads.blob.text : "", el: {} };
  for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, value: els[k].value };
  process.stdout.write(JSON.stringify(out));
})();
"""


def page(scenario, tmp_path):
    f = tmp_path / "scenario.json"
    f.write_text(json.dumps(scenario))
    out = subprocess.run(["node", "-e", HARNESS, json.dumps([DATA, PAGE]), str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def stubs(client, drift=(), **kw):
    st = state_of(client, drift)
    r = [{"match": "/master/state", "body": st}, {"match": "/segments/accounts", "body": {"accounts": [dict(a, currency="USD") for a in ACCOUNTS if a["platform"] == "google"], "groups": []}},
         {"match": "/gads/accounts?platform=meta", "body": {"accounts": [a for a in ACCOUNTS if a["platform"] == "meta"]}}]
    return r + kw.get("more", [])


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("master_agent_data.js") < html.index("master_agent.js") and "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_the_clients_are_listed_with_the_tiles_and_nothing_is_escaped_wrongly(client, tmp_path):
    out = page({"responses": stubs(client)}, tmp_path)
    lst, kp = out["el"]["#list"]["html"], out["el"]["#kpis"]["html"]
    assert [c for c in out["calls"]] == ["GET /p2/paid-marketing/api/master/state", "GET /p2/paid-marketing/api/segments/accounts", "GET /p2/paid-marketing/api/gads/accounts?platform=meta"]
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in lst and "<b>Dental</b>" not in lst and "2 accounts, 10 of 10 shared fields filled" in lst and "Acme Dental, Acme Meta" in lst and 'class="pm-btn-ghost ma-edit" data-id="%s"' % client["id"] in lst
    assert "Clients" in kp and "Accounts covered" in kp and "Without a client" in kp and "Settings that differ" in kp
    assert out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True and "1 client" in out["el"]["#fresh"]["html"] and "last change" in out["el"]["#fresh"]["html"]
    assert "Beta Clinic" in out["el"]["#free"]["text"] and out["el"]["#freeCard"]["hidden"] is False and out["el"]["#driftCard"]["hidden"] is True and "ana@position2.com saved the client" in out["el"]["#changes"]["html"]


def test_an_empty_master_invites_the_first_client(tmp_path):
    out = page({"responses": [{"match": "/master/state", "body": {"clients": [], "drift": [], "changes": [], "usedBy": {}}}, {"match": "/segments/accounts", "body": {"accounts": []}},
                              {"match": "/gads/accounts", "body": {"accounts": []}}]}, tmp_path)
    assert "No client yet" in out["el"]["#list"]["html"] and out["el"]["#freeCard"]["hidden"] is True and out["el"]["#changesCard"]["hidden"] is True


def test_where_an_agents_own_value_differs_is_shown_in_a_table(client, tmp_path):
    d = {"account": G, "client": "Acme <i>x</i>", "agent": "KPI Monitoring", "field": "targets.cpa", "master": 85.0, "saved": 70.0}
    out = page({"responses": stubs(client, [d])}, tmp_path)
    t = out["el"]["#drift"]["html"]
    assert out["el"]["#driftCard"]["hidden"] is False and "Acme Dental" in t and "KPI Monitoring" in t and "<b>Cost per conversion</b>" in t and "<td>85</td><td>70</td>" in t and "Acme &lt;i&gt;x&lt;/i&gt;" in t


def test_adding_a_client_opens_a_blank_editor_with_every_account_and_the_fields_that_say_who_reads_them(client, tmp_path):
    out = page({"responses": stubs(client), "steps": [{"click": "#addBtn"}]}, tmp_path)
    assert out["el"]["#editor"]["hidden"] is False and out["el"]["#edTitle"]["text"] == "Add a client" and out["el"]["#removeBtn"]["hidden"] is True
    acc = out["el"]["#acctList"]["html"]
    assert acc.count("<input type=\"checkbox\"") == 3 and "Beta Clinic" in acc and 'class="bi-mk">Meta' in acc and "in Acme &lt;b&gt;Dental&lt;/b&gt;" in acc and acc.count(" disabled") == 2
    sh = out["el"]["#shared"]["html"]
    assert "Read by Ad Copy Checker" in sh and "Read by Location Conflicts" in sh and "Read by Unknown Domains" in sh and 'id="sh-phones"' in sh and "(0 chosen)" in out["el"]["#acctNote"]["text"]


def test_editing_a_client_prefills_the_form_and_the_accounts_it_owns(client, tmp_path):
    out = page({"responses": stubs(client), "steps": [{"fire": ["#list", "click", {".ma-edit": {"dataset": {"id": client["id"]}}}]}]}, tmp_path)
    assert out["el"]["#edTitle"]["text"] == "Edit Acme <b>Dental</b>" and out["el"]["#fName"]["value"] == "Acme <b>Dental</b>" and out["el"]["#removeBtn"]["hidden"] is False
    assert "617-555-0100\n617-555-0111" in out["el"]["#shared"]["html"] and out["el"]["#fOffers"]["value"] == "Free whitening | exam | 2026-12-31\nSpring special" and out["el"]["#fNotes"]["value"] == "Closed Sundays."
    blocks = out["el"]["#acctBlocks"]["html"]
    assert 'id="ac-1234567890-monthly" value="12000"' in blocks and 'id="ac-act_1234567-cpa" value=""' in blocks and blocks.count("<fieldset") == 2 and "(2 chosen)" in out["el"]["#acctNote"]["text"]
    assert out["calls"].count("GET /p2/paid-marketing/api/master/state") == 1


def test_choosing_accounts_keeps_what_was_typed_for_the_others(client, tmp_path):
    steps = [{"click": "#addBtn"}, {"fire": ["#acctList", "click", {"input": {"dataset": {"id": G2}}}]}, {"set": ["#ac-%s-monthly" % G2, "5000"]},
             {"fire": ["#acctList", "click", {"input": {"dataset": {"id": G}}}]}]
    out = page({"responses": stubs(client), "steps": steps}, tmp_path)
    blocks = out["el"]["#acctBlocks"]["html"]
    assert 'id="ac-2222222222-monthly" value="5000"' in blocks and 'id="ac-1234567890-monthly" value=""' in blocks and "(2 chosen)" in out["el"]["#acctNote"]["text"]
    off = page({"responses": stubs(client), "steps": steps + [{"fire": ["#acctList", "click", {"input": {"dataset": {"id": G}}}]}]}, tmp_path)
    assert "ac-1234567890-monthly" not in off["el"]["#acctBlocks"]["html"] and 'value="5000"' in off["el"]["#acctBlocks"]["html"]


def test_the_account_filter_narrows_the_list_but_never_hides_a_chosen_one(client, tmp_path):
    steps = [{"click": "#addBtn"}, {"fire": ["#acctList", "click", {"input": {"dataset": {"id": G2}}}]}, {"set": ["#acctFilter", "acme"]}, {"fire": ["#acctFilter", "input", None]}]
    out = page({"responses": stubs(client), "steps": steps}, tmp_path)
    acc = out["el"]["#acctList"]["html"]
    assert "Acme Dental" in acc and "Acme Meta" in acc and "Beta Clinic" in acc and acc.count("<input") == 3
    nope = page({"responses": stubs(client), "steps": [{"click": "#addBtn"}, {"set": ["#acctFilter", "zzzz"]}, {"fire": ["#acctFilter", "input", None]}]}, tmp_path)
    assert "No account matches." in nope["el"]["#acctList"]["html"]


def test_saving_posts_the_form_as_the_server_reads_it_and_reloads_the_clients(client, tmp_path):
    saved = dict(client)
    steps = [{"click": "#addBtn"}, {"set": ["#fName", "New Co"]}, {"fire": ["#acctList", "click", {"input": {"dataset": {"id": G2}}}]}, {"set": ["#ac-%s-monthly" % G2, "5000"]}, {"set": ["#ac-%s-cpa" % G2, "90"]},
             {"set": ["#sh-phones", "617-555-0100"]}, {"set": ["#sh-domains", "newco.com"]}, {"set": ["#fOffers", "Free exam | 2026-12-31"]}, {"click": "#saveBtn"}]
    out = page({"responses": stubs(client, more=[{"match": "/master/client", "method": "POST", "body": {"client": saved}}]), "steps": steps}, tmp_path)
    [post] = out["posts"]
    assert post["url"].endswith("/api/master/client") and post["body"]["id"] == "" and post["body"]["names"] == {G: "Acme Dental", G2: "Beta Clinic", MA: "Acme Meta"}
    c = post["body"]["client"]
    assert c["name"] == "New Co" and c["accounts"] == [{"id": G2, "budget": {"monthly": "5000", "weeklyMin": "", "weeklyMax": ""}, "targets": {"cpa": "90", "roas": "", "cvr": "", "ctr": ""}, "tolerance": ""}]
    assert c["shared"]["phones"] == "617-555-0100" and c["shared"]["domains"] == "newco.com" and c["shared"]["offers"] == [{"text": "Free exam", "ends": "2026-12-31"}] and c["shared"]["notes"] == ""
    assert out["el"]["#editor"]["hidden"] is True and "Saved Acme &lt;b&gt;Dental&lt;/b&gt;." in out["el"]["#msg"]["html"] and out["calls"].count("GET /p2/paid-marketing/api/master/state") == 2


def test_a_refused_save_says_why_in_the_editor_and_keeps_it_open(client, tmp_path):
    steps = [{"click": "#addBtn"}, {"set": ["#sh-phones", "123"]}, {"click": "#saveBtn"}]
    out = page({"responses": stubs(client, more=[{"match": "/master/client", "method": "POST", "status": 400, "body": {"error": "123 is not a ten digit US phone number."}}]), "steps": steps}, tmp_path)
    assert "123 is not a ten digit US phone number." in out["el"]["#edMsg"]["html"] and out["el"]["#editor"]["hidden"] is False and out["calls"].count("GET /p2/paid-marketing/api/master/state") == 1
    down = page({"responses": stubs(client), "reject": "/master/client", "steps": [{"click": "#addBtn"}, {"click": "#saveBtn"}]}, tmp_path)
    assert "Nothing was saved." in down["el"]["#edMsg"]["html"] and down["el"]["#editor"]["hidden"] is False


def test_removing_a_client_needs_a_second_click(client, tmp_path):
    steps = [{"fire": ["#list", "click", {".ma-edit": {"dataset": {"id": client["id"]}}}]}, {"click": "#removeBtn"}]
    one = page({"responses": stubs(client, more=[{"match": "/client/delete", "method": "POST", "body": {"removed": {"id": client["id"], "name": "Acme"}}}]), "steps": steps}, tmp_path)
    assert one["posts"] == [] and "Click again" in one["el"]["#removeBtn"]["text"]
    two = page({"responses": stubs(client, more=[{"match": "/client/delete", "method": "POST", "body": {"removed": {"id": client["id"], "name": "Acme"}}}]), "steps": steps + [{"click": "#removeBtn"}]}, tmp_path)
    assert two["posts"] == [{"url": "/p2/paid-marketing/api/master/client/delete", "body": {"id": client["id"]}}] and two["el"]["#editor"]["hidden"] is True and "Removed Acme." in two["el"]["#msg"]["html"]


def test_cancel_closes_the_editor_without_saving(client, tmp_path):
    out = page({"responses": stubs(client), "steps": [{"click": "#addBtn"}, {"click": "#cancelBtn"}]}, tmp_path)
    assert out["el"]["#editor"]["hidden"] is True and out["posts"] == []


def test_a_master_that_cannot_be_read_and_a_missing_account_list_are_said_in_words(client, tmp_path):
    out = page({"responses": [{"match": "/master/state", "status": 503, "body": {"error": "The database is down."}}, {"match": "/segments/accounts", "body": {"accounts": []}}, {"match": "/gads/accounts", "body": {"accounts": []}}]}, tmp_path)
    assert "The database is down." in out["el"]["#msg"]["html"] and out["el"]["#out"]["hidden"] is True and out["el"]["#skel"]["hidden"] is True
    down = page({"responses": [], "reject": "/master/state"}, tmp_path)
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"]
    nometa = page({"responses": [{"match": "/master/state", "body": state_of(client)}, {"match": "/segments/accounts", "body": {"accounts": [{"id": G, "name": "Acme Dental"}]}}], "reject": "platform=meta"}, tmp_path)
    assert "Meta ad accounts could not be listed" in nometa["el"]["#msg"]["html"] and "pm-note" in nometa["el"]["#msg"]["html"]
    noggl = page({"responses": [{"match": "/master/state", "body": state_of(client)}, {"match": "/segments/accounts", "status": 502, "body": {"error": "Google is not answering."}}, {"match": "/gads/accounts", "body": {"accounts": []}}]}, tmp_path)
    assert "Google is not answering." in noggl["el"]["#msg"]["html"]


def test_the_csv_is_downloaded_and_a_cell_that_starts_like_a_formula_is_defused(client, tmp_path):
    risky = dict(client, name="=HYPERLINK(\"x\")")
    out = page({"responses": stubs(risky), "steps": [{"click": "#exportBtn"}]}, tmp_path)
    assert out["blob"].startswith("﻿Master Agent") and "'=HYPERLINK" in out["blob"] and "Monthly budget,12000" in out["blob"] and out["downloads"][0]["name"].startswith("master-agent-")
