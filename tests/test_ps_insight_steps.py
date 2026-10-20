"""Performance Summarizer: what to do about what stands out (static/js/ps_data.js insights, run in Node), and how the page draws it."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "static", "js", "ps_data.js")
PAGE = os.path.join(ROOT, "static", "js", "performance_summarizer.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr):
    code = "const P=require(%s);const out=(%s);console.log(JSON.stringify(out));" % (json.dumps(SCRIPT), expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def one(insights, id_):
    return next(i for i in insights if i["id"] == id_)


WASTE = """(()=>{
  const d={days:Array.from({length:3},(_,i)=>'2026-10-0'+(i+1)),
    campaigns:[{id:'1',name:'Good',type:'SEARCH'},{id:'2',name:'Dud',type:'DISPLAY'}],
    rows:[[0,0,1000,100,500,30,3000],[1,0,1000,100,200,0,0]]};
  return P.insights({cur:d,cmp:null,set:null,currency:'USD'});})()"""


def test_spend_without_a_conversion_says_what_to_check_in_order_and_what_stopping_it_would_save():
    w = one(run(WASTE), "waste")
    assert len(w["steps"]) == 4 and w["steps"][0].startswith("Check the conversion tracking first") and "Recording conversions" in w["steps"][0]
    assert "open Dud, then Insights and reports, then Search terms" in w["steps"][1] and "Search Query Analysis" in w["steps"][1]
    assert "Landing Page Check" in w["steps"][2] and w["steps"][3].startswith("If the tracking is fine")
    assert w["worth"].startswith("Pausing all of them would save about $2,000 a month at this rate ($200 over 3 days).")            # 200 over 3 days
    assert "a campaign that converts but is not measured looks the same" in w["worth"]


def test_the_campaign_named_in_the_search_terms_step_is_the_biggest_spender():
    out = run("""(()=>{const d={days:['2026-10-01'],campaigns:[{id:'1',name:'Good',type:'SEARCH'},{id:'2',name:'Small',type:'SEARCH'},{id:'3',name:'Big',type:'SEARCH'}],
      rows:[[0,0,1000,100,500,30,3000],[1,0,100,10,100,0,0],[2,0,500,50,300,0,0]]};
      return P.insights({cur:d,cmp:null,set:null,currency:'USD'});})()""")
    assert "open Big, then Insights and reports" in one(out, "waste")["steps"][1]


def sis(shares, account=0.5, cost=700):
    return run("""(()=>{const d={days:Array.from({length:3},(_,i)=>'2026-10-0'+(i+1)),campaigns:[{id:'1',name:'A',type:'SEARCH'}],rows:[[0,0,1000,100,%s,10,0]]};
      return P.insights({cur:d,cmp:null,set:null,currency:'USD',sis:{account:%s,campaigns:{}},shares:%s});})()""" % (cost, account, json.dumps(shares)))


def test_room_to_grow_splits_the_loss_into_budget_and_rank_and_sizes_the_budget_part():
    s = one(sis({"search_budget_lost_impression_share": 0.30, "search_rank_lost_impression_share": 0.20}, 0.5, 700), "sis")
    assert "Of the searches lost, 30% went to budget and 20% to rank." in s["text"] and len(s["steps"]) == 2
    assert s["steps"][0].startswith("Budget: 30% of the searches were lost to budget") and "about $140 more a day in all at this rate" in s["steps"][0]      # 233.33 a day * 0.3 / 0.5
    assert s["steps"][1].startswith("Rank: 20% were lost to rank") and "Auction Insights" in s["steps"][1]
    assert s["worth"] == "Winning back the budget loss would take about $140 more a day ($4,200 a month): an estimate that assumes the extra searches cost and convert like the rest."


def test_a_loss_under_five_points_gets_no_step_for_that_side_and_an_unknown_split_says_how_to_find_it():
    only_rank = one(sis({"search_budget_lost_impression_share": 0.04, "search_rank_lost_impression_share": 0.30}), "sis")
    assert len(only_rank["steps"]) == 1 and only_rank["steps"][0].startswith("Rank:") and only_rank["worth"] is None
    exactly = one(sis({"search_budget_lost_impression_share": 0.05, "search_rank_lost_impression_share": 0.05}), "sis")
    assert [s[:7] for s in exactly["steps"]] == ["Budget:", "Rank: 5"]
    none = one(sis({"search_budget_lost_impression_share": None, "search_rank_lost_impression_share": None}), "sis")
    assert len(none["steps"]) == 1 and "did not report how much was lost to budget" in none["steps"][0] and "Of the searches lost" not in none["text"] and none["worth"] is None
    nosh = one(run("""(()=>{const d={days:['2026-10-01'],campaigns:[{id:'1',name:'A',type:'SEARCH'}],rows:[[0,0,1000,100,500,10,0]]};
      return P.insights({cur:d,cmp:null,set:null,currency:'USD',sis:{account:0.5,campaigns:{}}});})()"""), "sis")
    assert len(nosh["steps"]) == 1 and nosh["steps"][0].startswith("This account did not report")


def test_a_share_at_sixty_percent_or_more_is_not_room_to_grow():
    assert "sis" not in [i["id"] for i in sis({"search_budget_lost_impression_share": 0.3, "search_rank_lost_impression_share": 0.1}, 0.6)]


WEEK = """(()=>{
  const days=Array.from({length:21},(_,i)=>{const d=new Date(Date.UTC(2026,8,7+i));return d.toISOString().slice(0,10);});   // 2026-09-07 is a Monday
  const rows=[]; days.forEach((_,i)=>{const dow=i%%7; const conv=dow===3?6:2; rows.push([0,i,1000,100,%s,conv,0]);});
  return P.insights({cur:{days:days,campaigns:[{id:'1',name:'A',type:'SEARCH'}],rows:rows},cmp:null,set:null,currency:'USD'});})()"""


def test_the_cheapest_and_dearest_day_say_how_to_use_it_on_either_kind_of_bidding():
    w = one(run(WEEK % "100"), "weekday")
    assert "Thu is your cheapest day" in w["text"] and len(w["steps"]) == 3
    assert "an ad schedule that leaves Sun out, or make sure the budget lasts through Thu" in w["steps"][1] and "set a positive adjustment for Thu and a negative one for Sun" in w["steps"][0] and "mostly ignored" in w["steps"][1] and "at least four weeks" in w["steps"][2] and "Segment Analysis" in w["steps"][2]


def test_a_spike_says_where_to_look_for_the_cause():
    out = run("""(()=>{const days=Array.from({length:10},(_,i)=>'2026-10-'+String(i+1).padStart(2,'0')); const rows=days.map((_,i)=>[0,i,1000,100,i===6?1500:100,2,0]);
      return P.insights({cur:{days:days,campaigns:[{id:'1',name:'A',type:'SEARCH'}],rows:rows},cmp:null,set:null,currency:'USD'});})()""")
    s = one(out, "spike")
    assert len(s["steps"]) == 3 and s["steps"][0].startswith("Open Tools, then Change history") and "twice a campaign's daily budget" in s["steps"][1] and "negatives" in s["steps"][2]


def test_the_findings_without_steps_have_none_and_no_step_has_a_dash_or_a_stray_mark():
    headline = one(run(WASTE), "headline")
    assert "steps" not in headline and "worth" not in headline
    for ins in (run(WASTE), sis({"search_budget_lost_impression_share": 0.3, "search_rank_lost_impression_share": 0.2}), run(WEEK % "100")):
        for i in ins:
            for text in (i.get("steps") or []) + [i.get("worth") or ""]:
                assert chr(0x2014) not in text and chr(0x2013) not in text and "%s" not in text and "undefined" not in text and "NaN" not in text, text


def test_the_page_passes_the_whole_account_shares_and_draws_the_steps_and_what_it_is_worth_escaped():
    src = open(PAGE, encoding="utf-8").read()
    assert "shares:S.filter.length?null:S.shares.cur" in src
    a = src.index("function paintInsights(){")
    b = src.index("/* ── Splits: device, network, campaign type")
    item = {"id": "waste", "tone": "warn", "title": "T <i>", "text": "x", "steps": ["Open <b>a</b>", "Then b"], "worth": "$5 <u>", "action": {"kind": "filter", "ids": ["1"]}}
    code = ("const out={}; const IC={good:'',warn:'',info:''}; const P={insights:()=>%s}; const S={cur:{},cmp:null,sis:null,shares:{cur:null},filter:[]};"
            "const setCur=()=>null, cur=()=>'USD'; const $=s=>({set innerHTML(v){out[s]=v;}}); const $$=()=>[];"
            "const esc=s=>String(s==null?'':s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
            "%s;paintInsights();console.log(JSON.stringify(out));") % (json.dumps([item, {"id": "h", "tone": "info", "title": "H", "text": "t"}]), src[a:b])
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    html = json.loads(r.stdout)["#insights"]
    assert '<details class="ps-do"><summary>What to do</summary><ol><li>Open &lt;b&gt;a&lt;/b&gt;</li><li>Then b</li></ol><p class="ps-worth"><b>Worth</b> $5 &lt;u&gt;</p></details>' in html
    assert html.count("<details") == 1 and "T &lt;i&gt;" in html
