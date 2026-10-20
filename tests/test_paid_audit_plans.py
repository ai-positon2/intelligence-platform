"""Account Audit: the fix plans of the budget and bidding checks (tracker/paid_audit_plans.py and how the checks carry them)."""
import copy

import pytest

from tests import audit_fakes as A
from tests.test_paid_audit_google_checks import camps, one
from tracker import paid_audit_google_checks as c
from tracker import paid_audit_model as model
from tracker import paid_audit_plans as p


def money(x):
    return "USD %s" % format(round(x), ",")


def camp(name="Search A", budget=50.0, cost=900.0, clicks=300, conv=30.0, lost=25.0, rank=10.0, share=50.0, bidding="MAXIMIZE_CONVERSIONS", kind="", target=None, shared=False):
    return {"name": name, "type": "SEARCH", "bidding": bidding, "targetKind": kind, "target": target, "budget": budget, "budgetShared": shared,
            "metrics": {"impressions": 10000, "clicks": clicks, "cost": cost, "conversions": conv, "budgetLost": lost, "rankLost": rank, "share": share}}


def step(steps, sid):
    return next(s for s in steps if s["id"] == sid)


def text(s):
    return " ".join([s["title"], s["why"], s["effect"] or ""] + s["how"])


# ── Budget ──────────────────────────────────────────────────────────────────

def test_a_capped_converting_campaign_gets_a_raise_sized_from_its_spend_and_lost_share():
    x = camp(cost=900.0, lost=25.0, share=50.0, budget=50.0, conv=30.0)       # 30 a day; 25 / 50 = half as much volume again: 15 more a day
    assert p.extra_per_day(x) == pytest.approx(15.0)
    s = step(p.budget_plan([(x, 25.0)], money), "budget")
    assert s["title"] == "Raise the budget of the converting campaigns that run out" and s["campaigns"] == ["Search A"]
    assert "Search A: from USD 50 to about USD 65 a day (USD 15 more)" in text(s) and "recover about 25% of the impressions" in text(s) and "roughly 15 more conversions" in text(s)
    assert s["effect"].startswith("About USD 15 more a day (USD 450 over 30 days), for roughly 15 more conversions: an estimate")
    assert "converted last month and still ran out of budget (Search A lost 25% of its impressions to budget)" in s["why"]


def test_several_campaigns_add_up_and_a_campaign_with_no_impression_share_is_not_sized_but_still_named():
    a, b = camp("Search A"), camp("Search B", cost=300.0, lost=10.0, share=20.0, budget=10.0, conv=5.0)
    none = camp("Search C", share=None)
    s = step(p.budget_plan([(a, 25.0), (b, 10.0), (none, 25.0)], money), "budget")
    assert "Search B: from USD 10 to about USD 15 a day" in text(s) and "Search C: raise the daily budget from USD 50. Google gave no impression share" in text(s)
    assert s["effect"].startswith("About USD 20 more a day")                 # 15 + 5; the unsized one adds nothing
    assert p.extra_per_day(none) is None and p.extra_per_day(camp(lost=None)) is None


def test_a_shared_budget_and_a_target_are_warned_about_and_more_than_eight_campaigns_say_so():
    s = step(p.budget_plan([(camp("Shared", shared=True, kind="cpa", target=40.0), 25.0)], money), "budget")
    h = " ".join(s["how"])
    assert "Shared uses a shared budget" in h and "Create a new budget" in h and "bids to a target CPA" in h
    many = [(camp("C%d" % i), 25.0) for i in range(10)]
    s = step(p.budget_plan(many, money), "budget")
    assert sum(1 for h in s["how"] if h.startswith("C") and ": from" in h) == p.MAX_LINES and "2 more campaigns are capped as well" in " ".join(s["how"])


def test_the_money_comes_from_campaigns_that_spend_without_converting_or_else_from_the_weakest():
    dead = [camp("Dead A", cost=600.0, conv=0.0, clicks=80), camp("Dead B", cost=300.0, conv=0.0, clicks=60)]
    steps = p.budget_plan([(camp(), 25.0)], money, dead)
    f = step(steps, "fund")
    assert f["title"] == "Pay for it from campaigns that spend and do not convert" and "spent USD 900 in 30 days" in f["why"] and f["effect"] == "Moving all of it would free about USD 30 a day." and f["campaigns"] == ["Dead A", "Dead B"]
    assert "Check their conversion tracking" in f["how"][0]
    weak = step(p.budget_plan([(camp(), 25.0)], money, []), "fund")
    assert weak["title"] == "Fund it from the campaigns that earn least" and "Cost / conv." in weak["how"][0]
    assert [s["id"] for s in p.budget_plan([(camp(share=None), 25.0)], money, [])] == ["budget"]          # nothing sized: no funding step to size


# ── Rank, partners, manual bidding, thin data, no conversions ───────────────

def test_the_rank_plan_names_each_campaign_and_branches_on_the_bidding_in_use():
    big = [camp("Big", rank=50.0, cost=1000.0, clicks=500, share=40.0, bidding="TARGET_CPA", kind="cpa", target=40.0), camp("Manual", rank=45.0, bidding="MANUAL_CPC")]
    s = step(p.rank_plan(big, money), "rank")
    t = text(s)
    assert "Big: 50% lost to rank, average CPC USD 2; bidding is target cpa (target CPA USD 40)." in t and "Winning it back for Big would take at least USD 1,250 more over 30 days" in t
    assert "raise it by about 20% to 30%" in t and "Raise the bids on the keywords that convert" in t and "Auction Insights" in t and "no maximize" not in t.lower()
    smart = text(step(p.rank_plan([camp(rank=50.0, bidding="MAXIMIZE_CONVERSIONS")], money), "rank"))
    assert "add the missing conversion signals" in smart and "raise it by about 20%" not in smart
    roas = text(step(p.rank_plan([camp(rank=50.0, bidding="TARGET_ROAS", kind="roas", target=4.0)], money), "rank"))
    assert "(target ROAS 4.00)" in roas
    assert "Quality Score" in t and s["campaigns"] == ["Big", "Manual"]


def test_partners_say_what_turning_them_off_saves_and_loses():
    s = step(p.partner_plan(200.0, 0.0, None, 30.0, money), "partners")
    assert s["effect"] == "Turning it off would save up to USD 200 a month, and lose the no conversions it brought." and "recorded no conversions" in s["why"] and "Networks" in " ".join(s["how"])
    s = step(p.partner_plan(200.0, 2.0, 100.0, 30.0, money), "partners")
    assert "cost USD 100 per conversion against USD 30 on Google Search" in s["why"] and "lose the 2 conversions it brought" in s["effect"]


def test_manual_bidding_moves_one_campaign_at_a_time_and_quotes_what_each_costs_per_conversion():
    s = step(p.manual_plan([camp("Manual", cost=600.0, conv=20.0, bidding="MANUAL_CPC")], money), "smart")
    assert "Manual: 20 conversions in 30 days at USD 30 each; bidding is manual cpc." in text(s) and "choose Maximize conversions" in text(s) and "at about what it costs now" in text(s)
    assert "No gain is promised" in s["effect"]


def test_thin_smart_bidding_says_how_many_conversions_each_is_short():
    s = step(p.thin_plan([camp("Thin", conv=10.0, bidding="TARGET_CPA", kind="cpa", target=40.0)], lambda x: 30, money), "thin")
    assert "Thin: 10 conversions in 30 days, about 30 suggested, 20 short." in text(s) and "portfolio strategy" in text(s) and "Merge campaigns" in text(s)


def test_spend_without_conversions_checks_tracking_first_and_sizes_what_pausing_would_save():
    s = step(p.dead_plan([camp("Dead", cost=600.0, conv=0.0, clicks=80)], 3000.0, money), "dead")
    assert "spent USD 600 in 30 days (20% of the account) with no conversion" in s["why"] and s["how"][1].startswith("First check the tracking") and "Recording conversions" in s["how"][1]
    assert "CRM" in text(s) and s["effect"] == "Pausing all of them would save about USD 600 a month, so settle the tracking question first."
    assert "0% of the account" in p.dead_plan([camp(cost=0.0)], 0, money)[0]["why"]


# ── As the checks carry them ────────────────────────────────────────────────

def rows_and_metrics():
    rows = [A.campaign("10", "Limited", primary="LIMITED", reasons=("BUDGET_CONSTRAINED",), budget="50000000"), A.campaign("12", "Dead")]
    m = [A.campaign_metrics("10", cost=900.0, conversions=30.0, budget_lost=0.25, share=0.5), A.campaign_metrics("12", clicks=80, cost=300.0, conversions=0.0)]
    return rows, m


def test_the_budget_check_carries_the_plan_with_the_funding_step_from_the_dead_campaign():
    rows, m = rows_and_metrics()
    f = one("G-STR-04", camps(*rows, metrics=m))
    ids = [s["id"] for s in f["plan"]]
    assert f["result"] == "warn" and ids == ["budget", "fund"]
    assert "Limited: from USD 50 to about USD 65 a day" in text(f["plan"][0]) and f["plan"][1]["campaigns"] == ["Dead"]


def test_a_passing_or_unknown_check_carries_no_plan_and_a_plan_does_not_change_the_score_or_the_fixed_text():
    assert "plan" not in one("G-STR-04") and "plan" not in one("G-STR-04", campaigns=None)
    rows, m = rows_and_metrics()
    f = one("G-STR-04", camps(*rows, metrics=m))
    assert f["fix"] == model.CATALOG["G-STR-04"]["fix"] and f["why"] == model.CATALOG["G-STR-04"]["why"]
    assert "plan" not in model.make("G-STR-04", "pass", "ok", plan=[{"id": "x"}]) and model.make("G-STR-04", "warn", "x", plan=[{"id": "x"}])["plan"] == [{"id": "x"}]
    assert "plan" not in model.make("G-STR-04", "warn", "x") and "plan" not in model.make("G-STR-04", "fail", "x", plan=[])


def test_the_other_checks_carry_their_plans():
    net = {"cost": {"SEARCH": 800.0, "SEARCH_PARTNERS": 200.0}, "total": 1000.0, "conversions": {"SEARCH": 30.0, "SEARCH_PARTNERS": 0.0}}
    assert [s["id"] for s in one("G-STR-03", network=net)["plan"]] == ["partners"]
    rank = camps(A.campaign("10", "Rank"), metrics=[A.campaign_metrics("10", cost=900.0, rank_lost=0.5, share=0.4)])
    f = one("G-STR-10", rank)
    assert f["result"] == "warn" and [s["id"] for s in f["plan"]] == ["rank"] and "Rank: 50% lost to rank" in text(f["plan"][0])
    dead = camps(A.campaign("10", "Dead"), metrics=[A.campaign_metrics("10", clicks=80, cost=900.0, conversions=0.0)])
    f = one("G-STR-09", dead)
    assert f["result"] in ("warn", "fail") and [s["id"] for s in f["plan"]] == ["dead"]
    manual = camps(A.campaign("10", "Man", bidding="MANUAL_CPC"), metrics=[A.campaign_metrics("10", cost=600.0, conversions=20.0)])
    assert [s["id"] for s in one("G-STR-08", manual)["plan"]] == ["smart"]
    thin = camps(A.campaign("10", "Thin", bidding="TARGET_CPA", target_cpa="40000000"), metrics=[A.campaign_metrics("10", cost=600.0, conversions=5.0)])
    assert [s["id"] for s in one("G-STR-07", thin)["plan"]] == ["thin"]


def test_no_plan_text_has_a_dash_or_a_stray_format_mark():
    rows, m = rows_and_metrics()
    f = one("G-STR-04", camps(*rows, metrics=m))
    steps = f["plan"] + p.rank_plan([camp(rank=50.0, bidding="MANUAL_CPC"), camp("T", rank=50.0, bidding="TARGET_ROAS", kind="roas", target=4.0)], money) + \
        p.partner_plan(10.0, 0.0, None, 5.0, money) + p.manual_plan([camp()], money) + p.thin_plan([camp()], lambda x: 30, money) + p.dead_plan([camp(conv=0.0)], 1000.0, money)
    for s in steps:
        t = text(s)
        assert chr(0x2014) not in t and chr(0x2013) not in t and "%s" not in t and "%d" not in t and "%%" not in t, s["id"]


def test_each_thin_campaign_is_measured_against_its_own_need():
    a, b = camp("Cpa", conv=10.0, bidding="TARGET_CPA"), camp("Roas", conv=10.0, bidding="TARGET_ROAS")
    s = step(p.thin_plan([a, b], lambda x: 30 if x["bidding"] == "TARGET_CPA" else 50, money), "thin")
    assert "Cpa: 10 conversions in 30 days, about 30 suggested, 20 short." in text(s) and "Roas: 10 conversions in 30 days, about 50 suggested, 40 short." in text(s)


# ── On the page ─────────────────────────────────────────────────────────────

import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

from tests import test_paid_audit as T  # noqa: E402
from tracker import paid_audit_google_read as gread  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = [os.path.join(ROOT, "static", "js", n) for n in ("au_data.js", "account_audit.js")]

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const paths = JSON.parse(process.argv[1]), report = JSON.parse(process.argv[2]);
const els = {}, handlers = {};
function el(sel){
  if(!els[sel]) els[sel] = {sel, innerHTML:"", textContent:"", value:"", hidden:false, style:{}, classList:{add(){},remove(){},toggle(){}}, children:[], dataset:{},
    addEventListener(ev, fn){ this["on_"+ev] = fn; }, setAttribute(){}, getAttribute(){return null;}, appendChild(){}, focus(){}, closest(){return null;}, querySelector:()=>el("q"+sel)};
  return els[sel];
}
const doc = {querySelector:el, getElementById:el, createElement:()=>el("created"), addEventListener(ev, fn){ handlers[ev] = fn; }, activeElement:null, body:{appendChild(){}}};
const win = {PMPlatform:{accounts(host, o){ setTimeout(()=>o.onChange({id:"1112223334", platform:"google", name:"Acme", currency:"USD"}, "google"), 0); return {}; }}};
const ctx = {window:win, document:doc, location:{hash:"", pathname:"/p"}, history:{replaceState(){}}, localStorage:{getItem:()=>null, setItem(){}}, setTimeout, clearTimeout, Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, decodeURIComponent, console,
  fetch:(url)=>Promise.resolve({ok:true, status:200, json:()=>Promise.resolve(url.includes("/report") ? report : url.includes("/history") ? {audits:[]} : {})})};
ctx.window.document = doc;
vm.createContext(ctx);
for(const p of paths) vm.runInContext(fs.readFileSync(p,"utf8"), ctx);
const tick = () => new Promise(r => setImmediate(r));
(async()=>{
  await new Promise(r => setTimeout(r, 30));          // the account picker answers on a timer
  for(let i=0;i<10;i++) await tick();
  const out = {};
  for(const k of Object.keys(els)) out[k] = els[k].innerHTML;
  process.stdout.write(JSON.stringify(out));
})();
"""


def drawn(report):
    out = subprocess.run(["node", "-e", HARNESS, json.dumps(JS), json.dumps(report)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return "".join(json.loads(out.stdout).values())


@pytest.fixture
def audit_report():
    from tests.test_paid_audit import GOOGLE_ID, run as run_audit
    T.fresh()
    rows = {gread.CAMPAIGNS_GAQL: [A.campaign("10", "Limited <b>", primary="LIMITED", reasons=("BUDGET_CONSTRAINED",), budget="50000000"), A.campaign("12", "Dead")],
            gread.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", cost=900.0, conversions=30.0, budget_lost=0.25, share=0.5), A.campaign_metrics("12", clicks=80, cost=300.0, conversions=0.0)]}
    base = T.google_search()

    def search(cid, q):
        return rows[q] if q in rows else base(cid, q)
    rep = run_audit(search_fn=search)
    return json.loads(json.dumps(rep))


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_audit_page_draws_the_steps_for_a_failed_check_and_escapes_them(audit_report):
    f = next(x for s in audit_report["sections"] for x in s["findings"] if x["id"] == "G-STR-04")
    assert f["result"] == "warn" and [s["id"] for s in f["plan"]] == ["budget", "fund"]
    html = drawn(audit_report)
    assert "Steps for this account" in html and "Raise the budget of the converting campaigns that run out" in html and 'class="au-plan"' in html
    assert "Limited &lt;b&gt;: from USD 50 to about USD 65 a day" in html and "<b>: from" not in html
    assert "Pay for it from campaigns that spend and do not convert" in html


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_check_that_could_not_be_read_shows_no_advice_and_a_passing_one_no_steps(audit_report):
    unknown = json.loads(json.dumps(audit_report))
    for s in unknown["sections"]:
        if any(f["id"] == "G-STR-04" for f in s["findings"]):
            for f in s["findings"]:                     # a section opens on what needs attention, then on what could not be read: leave only the unread one
                if f["id"] == "G-STR-04":
                    f.update(result="unknown", found="Could not be checked: the campaigns could not be read", fix="MARKER-FIX-TEXT")
                    f.pop("plan", None)
                else:
                    f["result"] = "pass"
            s["tally"] = {k: sum(1 for f in s["findings"] if f["result"] == k) for k in ("fail", "warn", "unknown", "pass", "na")}
            s["tally"]["total"] = len(s["findings"])
    html = drawn(unknown)
    assert "MARKER-FIX-TEXT" not in html and "This could not be checked, so there is nothing to fix yet. Read the account again." in html
    passing = json.loads(json.dumps(audit_report))
    for s in passing["sections"]:
        for f in s["findings"]:
            if f["id"] == "G-STR-04":
                f["result"] = "pass"
    assert "Raise the budget of the converting campaigns that run out" not in drawn(passing)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_step_title_with_markup_is_escaped_and_a_finding_without_a_plan_draws_no_steps(audit_report):
    risky = json.loads(json.dumps(audit_report))
    for s in risky["sections"]:
        for f in s["findings"]:
            if f["id"] == "G-STR-04":
                f["plan"][0]["title"] = "<img src=x onerror=1>"
                f["plan"][0]["how"][0] = "<b>open</b> it"
    html = drawn(risky)
    assert "&lt;img src=x onerror=1&gt;" in html and "<img" not in html and "&lt;b&gt;open&lt;/b&gt; it" in html
    bare = json.loads(json.dumps(audit_report))
    for s in bare["sections"]:
        for f in s["findings"]:
            f.pop("plan", None)
    html = drawn(bare)
    assert "Steps for this account" not in html and "What to do" in html
