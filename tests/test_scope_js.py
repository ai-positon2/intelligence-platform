"""The platform switch of the recent agents (static/js/pm_scope.js): the pure parts run in Node, the pages' use of it runs against the shared page harness. No network."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_copycheck_js import HARNESS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
SCOPE = os.path.join(JS, "pm_scope.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(SCOPE), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ── the ids ─────────────────────────────────────────────────────────────────

def test_a_both_account_is_a_google_id_or_an_mcc_and_a_meta_id_joined_with_a_plus():
    for good in ("1234567890+act_1234567", "mcc-9923720096-USD+act_1", "mcc-5717638537+act_99"):
        assert run("X.isCombo(%s)" % json.dumps(good)) is True, good
    for bad in ("1234567890", "act_1234567", "1234567890+1234567890", "act_1+act_2", "+act_1", "1234567890+", "1234567890+act_", "123+act_1", "mcc-+act_1", "", None):
        assert run("X.isCombo(%s)" % json.dumps(bad)) is False, bad
    assert run("X.partsOf('mcc-1-USD+act_5')") == ["mcc-1-USD", "act_5"] and run("X.partsOf('1234567890')") == ["1234567890"] and run("X.partsOf(null)") == [""]
    assert run("X.comboId('1234567890','act_5')") == "1234567890+act_5"
    assert [run("X.platformOfId(%s)" % json.dumps(i)) for i in ("1234567890", "mcc-1-USD", "act_5", "1234567890+act_5")] == ["google", "google", "meta", "both"]
    assert run("X.isMeta('act_12')") is True and run("X.isMeta('act_')") is False and run("X.isMeta('12')") is False


# ── putting two reports together ────────────────────────────────────────────

def report(name, acc, platform="google", currency="USD", **kw):
    r = {"account": {"id": acc, "name": name, "currency": currency, "platform": platform, "isGroup": False, "accountCount": 0}, "generatedAt": "2026-10-22T10:00:00+00:00", "readAt": "2026-10-22T09:00:00+00:00",
         "warnings": [], "accounts": [{"id": acc, "name": name, "status": "fix", "platform": platform}], "rules": [{"rule": "r"}],
         "summary": {"accounts": 1, "campaigns": 4, "high": 1, "counts": {"fix": 1, "check": 0, "clean": 0}, "overall": {"cost": 10.5, "expected": 8}}}
    r.update(kw)
    return r


def test_the_summaries_are_added_number_by_number_and_what_is_not_a_number_is_kept():
    s = run("X.sumSummary(A, B)", A={"n": 1, "c": {"a": 1, "b": 2}, "t": "x", "l": [1], "only": 5}, B={"n": 2.5, "c": {"a": 3, "z": 4}, "t": "y", "l": [2], "late": 7})
    assert s == {"n": 3.5, "c": {"a": 4, "b": 2, "z": 4}, "t": "x", "l": [1], "only": 5, "late": 7}
    assert run("X.sumSummary(null, B)", B={"a": 1}) == {"a": 1} and run("X.sumSummary(A, undefined)", A={"a": 1}) == {"a": 1}
    assert run("X.sumSummary(A, B)", A={"x": 0.1}, B={"x": 0.2}) == {"x": 0.3}                              # no 0.30000000000000004


def test_two_reports_are_one_with_the_google_accounts_first_and_the_summaries_added():
    g, m = report("Acme", "1234567890", warnings=["Google warns"]), report("Acme Meta", "act_1", "meta", stale="2026-10-21T08:00:00+00:00", warnings=["Meta warns"])
    out = run("X.mergeReports(G, M)", G=g, M=m)
    assert out["account"] == {"id": "1234567890+act_1", "name": "Acme + Acme Meta", "currency": "USD", "platform": "both", "isGroup": True, "accountCount": 2}
    assert [a["id"] for a in out["accounts"]] == ["1234567890", "act_1"] and out["summary"]["campaigns"] == 8 and out["summary"]["counts"] == {"fix": 2, "check": 0, "clean": 0}
    assert out["summary"]["overall"] == {"cost": 21, "expected": 16} and out["warnings"] == ["Google warns", "Meta warns"] and out["rules"] == [{"rule": "r"}]
    assert out["stale"] == "2026-10-21T08:00:00+00:00" and out["readAt"] == "2026-10-22T09:00:00+00:00"
    same = run("X.mergeReports(G, M)", G=g, M=report("Acme", "act_1", "meta"))
    assert same["account"]["name"] == "Acme" and "stale" not in same


def test_the_older_read_is_the_one_a_merged_report_names_and_a_mixed_currency_is_said():
    g = report("A", "1234567890", readAt="2026-10-22T09:00:00+00:00", generatedAt="2026-10-22T09:05:00+00:00")
    m = report("B", "act_1", "meta", currency="EUR", readAt="2026-10-22T07:00:00+00:00", generatedAt="2026-10-22T11:00:00+00:00")
    out = run("X.mergeReports(G, M)", G=g, M=m)
    assert out["readAt"] == "2026-10-22T07:00:00+00:00" and out["generatedAt"] == "2026-10-22T09:05:00+00:00"
    assert out["warnings"] == ["The two platforms bill in different currencies (USD and EUR), so money figures added up across them are not exact."]
    assert out["account"]["currency"] == "USD"
    one = dict(g)
    del one["readAt"]
    assert run("X.mergeReports(G, M)", G=one, M={k: v for k, v in m.items() if k != "readAt"}).get("readAt") is None


def test_what_only_one_half_carries_is_kept_and_googles_wins_when_both_do():
    g = dict(report("A", "1234567890"), patterns=[{"p": "google"}], saved={"x": 1})
    m = dict(report("B", "act_1", "meta"), patterns=[{"p": "meta"}], sensitivity="high")
    out = run("X.mergeReports(G, M)", G=g, M=m)
    assert out["patterns"] == [{"p": "google"}] and out["saved"] == {"x": 1} and out["sensitivity"] == "high"


# ── asking once or twice ────────────────────────────────────────────────────

FETCH = r"""
const calls = [];
const answers = %s;
globalThis.fetch = (url) => { calls.push(url); const k = Object.keys(answers).find(k => url.includes(k)); const a = answers[k] || {status: 404, body: {error: "none"}};
  return Promise.resolve({ok: a.status < 400, status: a.status, json: () => Promise.resolve(a.body)}); };
const X = require(%s);
"""


def fetch_report(answers, account, refresh=False):
    code = FETCH % (json.dumps(answers), json.dumps(SCOPE)) + "X.fetchReport('/api/x/report', %s, %s).then(r => console.log(JSON.stringify({r, calls})));" % (json.dumps(account), "true" if refresh else "false")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_single_account_is_asked_for_once_and_a_refresh_says_so():
    got = fetch_report({"1234567890": {"status": 200, "body": report("A", "1234567890")}}, "1234567890", True)
    assert got["calls"] == ["/api/x/report?account=1234567890&refresh=1"] and got["r"]["ok"] is True and got["r"]["j"]["account"]["id"] == "1234567890"


def test_a_both_account_is_asked_for_once_per_platform_and_the_answers_are_merged():
    answers = {"account=1234567890": {"status": 200, "body": report("A", "1234567890")}, "account=act_1": {"status": 200, "body": report("B", "act_1", "meta")}}
    got = fetch_report(answers, "1234567890+act_1")
    assert got["calls"] == ["/api/x/report?account=1234567890", "/api/x/report?account=act_1"] and got["r"]["ok"] is True
    assert got["r"]["j"]["account"]["platform"] == "both" and got["r"]["j"]["summary"]["campaigns"] == 8
    with_mcc = fetch_report(dict(answers, **{"account=mcc-1-USD": {"status": 200, "body": report("G", "mcc-1-USD")}}), "mcc-1-USD+act_1", True)
    assert with_mcc["calls"] == ["/api/x/report?account=mcc-1-USD&refresh=1", "/api/x/report?account=act_1&refresh=1"]


def test_when_one_platform_cannot_be_read_the_other_is_shown_and_the_page_says_which_is_missing():
    meta_down = {"account=1234567890": {"status": 200, "body": report("A", "1234567890", warnings=["w"])}, "account=act_1": {"status": 502, "body": {"error": "Meta answered 400: bad field", "kind": "meta"}}}
    got = fetch_report(meta_down, "1234567890+act_1")
    assert got["r"]["ok"] is True and got["r"]["j"]["warnings"] == ["w", "Meta Ads could not be read, so this leaves it out: Meta answered 400: bad field"] and got["r"]["j"]["account"]["id"] == "1234567890"
    google_down = {"account=1234567890": {"status": 429, "body": {"error": "slow down", "kind": "quota"}}, "account=act_1": {"status": 200, "body": report("B", "act_1", "meta")}}
    got = fetch_report(google_down, "1234567890+act_1")
    assert got["r"]["ok"] is True and got["r"]["j"]["warnings"] == ["Google Ads could not be read, so this leaves it out: slow down"]
    nobody = fetch_report({"account=1234567890": {"status": 429, "body": {"error": "slow down", "kind": "quota"}}, "account=act_1": {"status": 502, "body": {"error": "x"}}}, "1234567890+act_1")
    assert nobody["r"]["ok"] is False and nobody["r"]["status"] == 429 and nobody["r"]["j"]["kind"] == "quota"
    empty = fetch_report({"account=1234567890": {"status": 500, "body": {}}, "account=act_1": {"status": 200, "body": report("B", "act_1", "meta")}}, "1234567890+act_1")
    assert empty["r"]["j"]["warnings"] == ["Google Ads could not be read, so this leaves it out: HTTP 500"]


# ── the pages ───────────────────────────────────────────────────────────────

PAGES = [("kpi", "kpi-monitoring", "kpi_data.js", "kpi.js", "/kpi/"), ("geoconf", "location-conflicts", "geoconf_data.js", "geoconf.js", "/geoconf/"),
         ("copycheck", "ad-copy-checker", "copycheck_data.js", "copycheck.js", "/copycheck/"), ("anomaly", "spend-anomaly", "anomaly_data.js", "anomaly.js", "/anomaly/"),
         ("disapproved", "disapproved-ads", "disapproved_data.js", "disapproved.js", "/disapproved/"), ("track", "tracking-health", "tracking_health_data.js", "tracking_health.js", "/track/")]
GROUP = "mcc-9923720096-USD"
ACCTS = {"accounts": [{"id": "1234567890", "name": "Acme Dental", "currency": "USD", "group": GROUP}, {"id": "2222222222", "name": "Beta Clinic", "currency": "USD", "group": GROUP}],
         "groups": [{"id": GROUP, "name": "Group", "accountIds": ["1234567890", "2222222222"]}]}
METAS = {"accounts": [{"id": "act_1234567", "name": "Acme Dental", "currency": "USD", "platform": "meta"}, {"id": "act_999", "name": "Other Co", "currency": "USD", "platform": "meta"}]}


def page(slug, scripts, scenario):
    harness = (HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/" + slug).replace("JSON.parse(process.argv[2])", "JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))"))
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(scenario, f)
    try:
        out = subprocess.run(["node", "-e", harness, json.dumps(scripts), f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def scenario(path, steps, **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/gads/accounts", "body": METAS}, {"match": "/settings", "body": {"accounts": {}, "changes": []}},
                        {"match": path + "report", "status": 404, "body": {"error": "no report in this test"}}], "steps": steps}
    sc.update(kw)
    return sc


@pytest.mark.parametrize("agent, slug, data, script, path", PAGES)
def test_every_page_has_the_switch_two_pickers_and_asks_the_right_account(agent, slug, data, script, path):
    scripts = [SCOPE, os.path.join(JS, data), os.path.join(JS, script)]
    out = page(slug, scripts, scenario(path, [{"pick": "1234567890"}, {"platform": "meta"}, {"pickMeta": "act_1234567"}, {"platform": "both"}, {"pickMeta": "act_999"}, {"platform": "google"}]))
    assert out["switchers"] == 1 and [i["value"] for i in out["setsMeta"][-1]["items"]] == ["", "act_1234567", "act_999"]
    assert [i["label"] for i in out["sets"][-1]["items"]][:2] == ["Choose an account", "All of Group"]
    asked = [c.split("?account=")[1] for c in out["calls"] if path + "report?account=" in c]
    # Google account; Meta platform (its counterpart by name is chosen for it, then chosen again); Both asks each half; another Meta account; back to Google
    assert asked == ["1234567890", "act_1234567", "act_1234567", "1234567890", "act_1234567", "1234567890", "act_999", "1234567890"]
    base = "/p2/paid-marketing/" + slug + "?account="
    assert out["hist"] == [base + "1234567890", base + "act_1234567", base + "act_1234567", base + "1234567890%2Bact_1234567", base + "1234567890%2Bact_999", base + "1234567890"]    # the link keeps the Both choice
    assert out["platformsSet"][-1] == "google"
    assert out["store"]["pm_platform"] == "google"


@pytest.mark.parametrize("agent, slug, data, script, path", PAGES)
def test_a_link_to_a_meta_account_or_a_both_account_opens_on_that_platform(agent, slug, data, script, path):
    scripts = [SCOPE, os.path.join(JS, data), os.path.join(JS, script)]
    meta = page(slug, scripts, scenario(path, [], search="?account=act_1234567"))
    assert [c for c in meta["calls"] if path + "report" in c] == ["/p2/paid-marketing/api" + path + "report?account=act_1234567"]
    assert meta["platformsSet"][-1] == "meta"
    both = page(slug, scripts, scenario(path, [], search="?account=" + "1234567890%2Bact_1234567"))
    asked = [c for c in both["calls"] if path + "report" in c]
    assert asked == ["/p2/paid-marketing/api" + path + "report?account=1234567890", "/p2/paid-marketing/api" + path + "report?account=act_1234567"] and both["platformsSet"][-1] == "both"


def test_the_pages_link_the_switch_and_the_google_only_pages_show_all_three_choices_and_say_why_meta_has_nothing():
    for t in ("disapproved_ads", "ad_copy_checker", "spend_anomaly", "location_conflicts", "kpi_monitoring", "tracking_health"):
        html = open(os.path.join(ROOT, "templates", t + ".html"), encoding="utf-8").read()
        assert html.index("pm_picker.js") < html.index("pm_platform.js") < html.index("pm_scope.js") < html.index("monitor_panel.js"), t
    for t, js in (("brand_is", "brand_is.js"), ("brand_spend", "brand_spend.js"), ("keyword_visibility", "kvis.js")):
        html = open(os.path.join(ROOT, "templates", t + ".html"), encoding="utf-8").read()
        src = open(os.path.join(JS, js), encoding="utf-8").read()
        assert 'id="platSw"' in html and 'id="gBody"' in html and "pm_platform.js" in html and "PMPlatform.googleOnly" in src and 'allow:["google"]' not in src, t
        assert "Meta" in src and src.count('body:"#gBody"') == 1, t                                                   # the card says why Meta has nothing here
    sq, sqh = open(os.path.join(JS, "sqr_agent.js"), encoding="utf-8").read(), open(os.path.join(ROOT, "templates", "sqr_agent.html"), encoding="utf-8").read()
    assert "PL.googleOnly" in sq and "noSwitch: true" in sq and 'id="platSw"' in sqh and 'id="gBody"' in sqh
    for js in ("disapproved", "copycheck", "anomaly", "geoconf", "kpi", "tracking_health"):
        src = open(os.path.join(JS, js + ".js"), encoding="utf-8").read()
        assert "PMScope.mount" in src and "PMPicker" not in src and "—" not in src and '"Google + Meta":"MCC"' in src, js
    assert "—" not in open(SCOPE, encoding="utf-8").read() and "–" not in open(SCOPE, encoding="utf-8").read()


# ── the control itself, on a small stand-in for the page ────────────────────

MOUNT = r"""
const els = [], sets = [[], []], changes = [], store = {};
const document = { createElement: () => { const e = {children: [], className: "", hidden: false, textContent: "", appendChild(c) { this.children.push(c); }}; els.push(e); return e; } };
const switcherOpts = [];
const picks = [];
const root = { document,
  localStorage: { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } },
  fetch: url => { const a = url.includes("segments") ? SEG : META; return Promise.resolve({ok: a !== null, status: a === null ? 500 : 200, json: () => Promise.resolve(a || {})}); },
  PMPlatform: { switcher: (host, o) => { switcherOpts.push(o); host.appendChild({}); return { set() {}, value: () => o.value }; },
    counterpart: (a, others) => others.filter(x => x.name === a.name && x.currency === a.currency)[0] || null },
  PMPicker: (host, o) => { const i = picks.length; picks.push(o); return { set(items, v, dis) { sets[i].push({items, v, dis}); } }; } };
globalThis.window = root; globalThis.document = document;
const X = require(%s);
const scope = X.mount({appendChild() {}}, %s);
"""


def mount(options, seg, meta, steps="", saved=None):
    pre = "const SEG = %s, META = %s;" % (json.dumps(seg), json.dumps(meta))
    code = pre + MOUNT % (json.dumps(SCOPE), "Object.assign(%s, {onChange: (a, p) => changes.push([a, p]), onLoaded: i => (globalThis.loaded = i), onError: e => (globalThis.err = e.message)})" % json.dumps(options)) + \
        "const tick = () => new Promise(r => setTimeout(r, 5));\n(async () => { for (let i = 0; i < 4; i++) await tick();\n" + steps + "\nfor (let i = 0; i < 4; i++) await tick();\n" \
        "console.log(JSON.stringify({changes, sets, hostG: els[2].hidden, hostM: els[3].hidden, note: els[4].hidden ? '' : els[4].textContent, noteHidden: els[4].hidden, store, loaded: globalThis.loaded || null, err: globalThis.err || null, account: scope.account(), platform: scope.platform(), group: scope.groupOf('%s')}));})();" % GROUP
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_two_pickers_show_for_their_platform_and_both_for_both():
    google = mount({}, ACCTS, METAS)
    assert (google["hostG"], google["hostM"]) == (False, True)
    meta = mount({"account": "act_1234567"}, ACCTS, METAS)
    assert (meta["hostG"], meta["hostM"]) == (True, False) and meta["platform"] == "meta"
    both = mount({"account": "1234567890+act_1234567"}, ACCTS, METAS)
    assert (both["hostG"], both["hostM"]) == (False, False) and both["platform"] == "both" and both["account"] == "1234567890+act_1234567"


def test_a_meta_list_that_could_not_be_read_is_said_when_meta_is_in_view_and_not_for_google():
    google = mount({}, ACCTS, None)
    assert google["noteHidden"] is True and google["loaded"]["meta"] == [] and google["loaded"]["metaProblem"].startswith("Meta ad accounts could not be listed")
    meta = mount({"account": "act_1234567"}, ACCTS, None)
    assert meta["noteHidden"] is False and meta["note"] == "Meta ad accounts could not be listed, so only Google Ads can be checked."
    ok = mount({"account": "act_1234567"}, ACCTS, dict(METAS, metaProblem="The Meta token has expired."))
    assert ok["note"] == "The Meta token has expired." and ok["loaded"]["metaProblem"] == "The Meta token has expired."


def test_choosing_a_platform_with_nothing_chosen_picks_the_same_client_there_when_the_name_is_plain():
    g = mount({"account": "1234567890"}, ACCTS, METAS, "switcherOpts[0].onChange('meta');")
    assert g["account"] == "act_1234567" and g["platform"] == "meta"
    m = mount({"account": "act_1234567"}, ACCTS, METAS, "switcherOpts[0].onChange('google');")
    assert m["account"] == "1234567890" and m["platform"] == "google"
    other = mount({"account": "2222222222"}, ACCTS, METAS, "switcherOpts[0].onChange('meta');")
    assert other["account"] == ""                                                                                # no Meta account called Beta Clinic: nothing is guessed
    both = mount({"account": "1234567890"}, ACCTS, METAS, "switcherOpts[0].onChange('both');")
    assert both["account"] == "1234567890+act_1234567"


def test_an_account_that_is_not_on_the_list_is_forgotten_and_the_platform_is_remembered():
    m = mount({"account": "9999999999"}, ACCTS, METAS)
    assert m["account"] == "" and m["changes"][-1] == ["", "google"]
    m = mount({"account": "act_999"}, ACCTS, METAS, "switcherOpts[0].onChange('both');")
    assert m["store"]["pm_platform"] == "both" and m["account"] == "act_999"                                      # only a Meta account chosen and no Google one of that name: that one is the account
    gone = mount({"account": "act_000"}, ACCTS, METAS)
    assert gone["account"] == ""


def test_the_account_list_failing_is_reported_and_an_mcc_is_known_as_one():
    bad = mount({}, None, METAS)
    assert bad["err"] == "The account list could not be read." and bad["changes"] == []
    m = mount({}, ACCTS, METAS)
    assert m["group"]["name"] == "Group" and m["sets"][0][-1]["items"][1]["badge"].count("MCC") == 1


def test_every_function_a_page_calls_on_the_scope_control_is_one_it_exports():
    import glob
    exported = set(json.loads(subprocess.run(["node", "-e", "global.window=undefined;console.log(JSON.stringify(Object.keys(require(%s))))" % json.dumps(SCOPE)], capture_output=True, text=True, timeout=30).stdout))
    used = {}
    for path in glob.glob(os.path.join(JS, "*.js")):
        if os.path.basename(path) == "pm_scope.js":
            continue
        for n in set(re.findall(r"\b(?:window\.)?PMScope\.(\w+)", open(path, encoding="utf-8").read())):
            used.setdefault(n, []).append(os.path.basename(path))
    assert used and {n: w for n, w in used.items() if n not in exported} == {}


def test_the_qa_agent_uses_the_shared_control_for_all_three_choices_and_loads_it_first():
    html = open(os.path.join(ROOT, "templates", "qa_agent.html"), encoding="utf-8").read()
    src = open(os.path.join(JS, "qa_agent.js"), encoding="utf-8").read()
    assert html.index("pm_picker.js") < html.index("pm_scope.js") < html.index("qa_agent.js") and html.count("</div>") == html.count("<div"), "qa_agent"
    assert "PMScope.mount" in src and "noGroups:true" in src and "googleOnly" not in src and "PMPicker(" not in src and "—" not in src
    assert 'id="platSw"' not in html and 'id="gBody"' not in html and "—" not in html
