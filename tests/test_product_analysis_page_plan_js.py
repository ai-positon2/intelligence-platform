"""Product Analysis: the plan panel on the page, run in Node.

pa_plan.js is tested on its own; this checks the page uses it where it should: the group the table shows gets its plan above the table, a product's drawer
gets the plans for what it falls under (two at most), a group that is not a recommendation gets none, and nothing a campaign or product is called can break the
markup. The page script is an IIFE that needs a browser, so the functions are cut out of it by name and run against the real pa_data.js and pa_plan.js.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

NP = 11
HARNESS = r"""
const fs=require('fs'), A=require(%(pa_data)s), Q=require(%(pa_plan)s);
const src=fs.readFileSync(%(page)s,'utf8');
const a=src.indexOf('/* What to do about a group of products'), b=src.indexOf('/* The 80/20 curve */');
const c=src.indexOf('/* What to do about this one product'), d=src.indexOf('function openDrawer(it){');
if(a<0||b<a||c<0||d<c) throw new Error('page functions not found');
const esc=function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});};
const S=%(state)s;
const cur=function(){return S.currency||"USD";};
const body=src.slice(a,b)+src.slice(c,d)+';return {groupPlan:groupPlan,drawerPlans:drawerPlans,planHtml:planHtml};';
const page=new Function('A','Q','S','esc','cur',body)(A,Q,S,esc,cur);
S.items=%(items)s; S.cls=A.classify(S.items,A.totals({total:S.items.reduce((t,i)=>{i.n.forEach((v,k)=>t[k]+=v);return t;},Array(%(np)d).fill(0))}),{});
const out=(%(expr)s);
console.log(JSON.stringify(out));
"""


def prod(pid, impr=1000, clicks=100, cost=100.0, conv=0.0, val=0.0, title=None):
    return {"id": pid, "title": title or "Product " + pid, "brand": "", "type": "", "t1": "", "t2": "", "other": False, "n": [impr, clicks, cost, conv, val] + [0] * (NP - 5)}


def run(expr, items, seg="all", mix=None, oos=None, currency=None):
    state = {"seg": seg, "oos": oos, "data": {"mix": mix}, "currency": currency}
    code = HARNESS % {"pa_data": json.dumps(os.path.join(JS, "pa_data.js")), "pa_plan": json.dumps(os.path.join(JS, "pa_plan.js")), "page": json.dumps(os.path.join(JS, "product_analysis.js")),
                      "state": json.dumps(state), "items": json.dumps(items), "expr": expr, "np": NP}
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


ITEMS = [prod("w1", cost=200.0), prod("w2", cost=80.0),                              # spent, never sold
         prod("s1", cost=100.0, conv=20, val=800.0),                                  # a steady seller at the account's rate
         prod("star", impr=3000, clicks=300, cost=50.0, conv=40, val=1000.0)]
CAMPS = [["Shop <Main>", "Shopping", "Manual CPC", "adjustable", 380.0]]
MIX = {"campaigns": CAMPS, "by": {i["id"]: [i["n"][2], 0, 0, 0, 0] for i in ITEMS}, "top": {i["id"]: 0 for i in ITEMS}}


def test_the_group_the_table_shows_gets_its_plan_above_the_table():
    html = run("page.groupPlan()", ITEMS, seg="waste", mix=MIX)
    assert html.startswith('<div class="pa-plan">') and "Stop paying for products that do not sell" in html and "What to do" in html
    assert "2 products spent $280 without a sale" in html and "<ol><li>" in html and "Worth" in html


def test_a_group_that_is_not_a_recommendation_gets_no_plan():
    for seg in ("all", "star", "steady", "thin", "nonsense", "constructor", "__proto__", "toString"):
        assert run("page.groupPlan()", ITEMS, seg=seg, mix=MIX) == "", seg


def test_a_recommendation_with_no_products_in_it_gets_no_plan():
    assert run("page.groupPlan()", [prod("s1", cost=100.0, conv=20, val=800.0)], seg="waste", mix=MIX) == ""
    assert run("page.groupPlan()", ITEMS, seg="oos", mix=MIX, oos=None) == ""                  # stock could not be read: nothing is out of stock as far as we know


def test_the_out_of_stock_group_gets_its_plan_from_the_stock_list():
    html = run("page.groupPlan()", ITEMS, seg="oos", mix=MIX, oos=["w1"])
    assert "Out of stock, still being advertised" in html and "1 product still got $200 of spend" in html
    assert "w2" not in html and run("page.groupPlan()", ITEMS, seg="oos", mix=MIX, oos=["nothing"]) == ""


def test_the_money_is_in_the_accounts_currency():
    html = run("page.groupPlan()", ITEMS, seg="waste", mix=MIX, currency="EUR")
    assert "\u20ac280" in html and "$280" not in html


def test_a_campaign_name_cannot_break_the_markup():
    html = run("page.groupPlan()", ITEMS, seg="waste", mix=MIX)
    assert "Shop &lt;Main&gt;" in html and "<Main>" not in html


def test_without_the_mix_the_plan_still_shows_and_says_it_could_not_be_read():
    html = run("page.groupPlan()", ITEMS, seg="waste", mix=None)
    assert "could not be read" in html and "On Performance Max" in html and "On Meta" in html          # every kind of campaign is covered
    gem = run("page.groupPlan()", ITEMS, seg="gem", mix=None)
    assert "Which kind of campaign these products spend in could not be read" in gem and "On Smart Bidding Shopping campaigns" in gem


def test_a_product_drawer_gets_the_plans_for_what_it_falls_under_and_never_more_than_two():
    one = run("page.drawerPlans(S.items[0])", ITEMS, mix=MIX)
    assert one.startswith('<details class="pa-dr-plan"><summary>What to do</summary>') and "Stop paying for products that do not sell" in one and one.count('class="pa-plan"') == 1
    assert run("page.drawerPlans(S.items[2])", ITEMS, mix=MIX) == ""                         # a steady seller needs nothing
    both = run("page.drawerPlans(S.items[3])", ITEMS, mix=MIX)
    assert "Give your best earners more room" in both
    stock = run("page.drawerPlans(S.items[0])", ITEMS, mix=MIX, oos=["w1"])
    assert stock.index("Out of stock, still being advertised") < stock.index("Stop paying for products that do not sell")      # the most urgent first
    assert stock.count('class="pa-plan"') == 2
    three = ITEMS + [prod("tri", impr=20000, clicks=30, cost=150.0)]                            # out of stock, spent and sold nothing, and nobody clicks it
    mix3 = dict(MIX, by=dict(MIX["by"], tri=[150.0, 0, 0, 0, 0]), top=dict(MIX["top"], tri=0))
    html = run("page.drawerPlans(S.items[4])", three, mix=mix3, oos=["tri"])
    assert html.count('class="pa-plan"') == 2 and "Make the listing worth a click" not in html          # three apply: the two most urgent are shown
    assert "Make the listing worth a click" in run("page.groupPlan()", three, seg="window", mix=mix3)       # and the third is still there as its own group


def test_the_table_paints_the_plan_above_its_tools_and_the_drawer_paints_its_own():
    src = open(os.path.join(JS, "product_analysis.js"), encoding="utf-8").read()
    assert """el.innerHTML=groupPlan()+'<div class="ps-tools">""" in src               # the plan comes first in the table card
    assert src.count("drawerPlans(it)+") == 1 and "function drawerPlans(it){" in src          # defined once, and the drawer's markup calls it
    assert src.index("whyLine(it)?") < src.index("drawerPlans(it)+")                         # below the one-line reason, above the numbers
