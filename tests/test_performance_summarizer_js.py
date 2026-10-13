"""Performance Summarizer: the number-crunching in static/js/ps_data.js, run in Node.

The page script only draws; every figure it shows (a date range and the one
to compare it with, a metric from a total, how a change reads and whether it
is good news, a day / week / weekday grouping, the "what stands out" lines)
is computed here, so this is where a wrong number would come from. The file
is executed, not read: a typo that only breaks at run time fails here.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "static", "js", "ps_data.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr):
    code = "const P=require(%s);const out=(%s);console.log(JSON.stringify(out));" % (json.dumps(SCRIPT), expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_presets_mean_the_complete_days_before_today():
    assert run("P.presetRange('last7','2026-10-13')") == {"start": "2026-10-06", "end": "2026-10-12"}
    assert run("P.presetRange('last30','2026-03-01')") == {"start": "2026-01-30", "end": "2026-02-28"}     # across a short month
    assert run("P.presetRange('mtd','2026-10-13')") == {"start": "2026-10-01", "end": "2026-10-12"}
    assert run("P.presetRange('mtd','2026-10-01')") == {"start": "2026-09-01", "end": "2026-09-30"}       # nothing yet this month
    assert run("P.presetRange('lastMonth','2026-01-05')") == {"start": "2025-12-01", "end": "2025-12-31"}


def test_the_comparison_range_is_the_same_length_just_before_or_a_year_back():
    assert run("P.compareRange({start:'2026-10-01',end:'2026-10-07'},'prev')") == {"start": "2026-09-24", "end": "2026-09-30"}
    assert run("P.compareRange({start:'2026-10-01',end:'2026-10-31'},'prev')") == {"start": "2026-08-31", "end": "2026-09-30"}
    assert run("P.compareRange({start:'2024-02-29',end:'2024-03-05'},'yoy')") == {"start": "2023-02-28", "end": "2023-03-05"}
    assert run("P.compareRange({start:'2026-10-01',end:'2026-10-07'},'none')") is None


def test_a_metric_is_a_function_of_a_total_and_never_divides_by_zero():
    t = "[1000, 50, 200, 10, 900]"           # impressions, clicks, cost, conversions, value
    got = run("Object.fromEntries(P.METRIC_ORDER.map(k=>[k,P.mval(k,%s)]))" % t)
    assert got == {"cost": 200, "impressions": 1000, "clicks": 50, "ctr": 0.05, "cpc": 4, "cpm": 200, "conversions": 10,
                   "cvr": 0.2, "cpa": 20, "value": 900, "roas": 4.5, "aov": 90}
    nothing = run("Object.fromEntries(P.METRIC_ORDER.map(k=>[k,P.mval(k,[0,0,0,0,0])]))")
    assert nothing["cost"] == 0 and all(nothing[k] is None for k in ("ctr", "cpc", "cpm", "cvr", "cpa", "roas", "aov"))


def test_numbers_are_formatted_for_the_currency_and_never_show_nan():
    assert run("[P.fmt('money',12345.6,'USD',true),P.fmt('money',1234567,'USD',true),P.fmt('money',950,'USD'),P.fmt('money2',3.456,'USD')]") \
        == ["$12.3k", "$1.2M", "$950", "$3.46"]
    assert run("[P.fmt('pct',0.0345),P.fmt('pct',0.345),P.fmt('x',3.456),P.fmt('num',12.34),P.fmt('int',1234567)]") \
        == ["3.45%", "34.5%", "3.46x", "12.3", "1,234,567"]
    assert run("[P.fmt('money',null,'USD'),P.fmt('pct',NaN),P.fmt('int',Infinity)]") == ["–"] * 3
    assert run("P.fmt('money2',2.5,'GBP')") == "£2.50" and "CA$" in run("P.fmt('money',70700,'CAD')")


def test_a_change_reads_right_and_is_good_or_bad_for_that_metric():
    assert run("P.change('cpa',40,50)") == {"text": "−20%", "dir": -1, "tone": "good", "pct": -0.2}      # cheaper is good
    assert run("P.change('conversions',8,10)")["tone"] == "bad"
    assert run("P.change('cost',120,100)")["tone"] == "flat"                                                    # spend up is neither
    assert run("P.change('ctr',0.05,0.04)")["text"] == "+1.00 pp"                                              # a rate moves in points
    assert run("P.change('clicks',5,0)")["text"] == "new" and run("P.change('clicks',null,5)")["text"] == "–"
    assert run("P.change('clicks',100,100.2)")["tone"] == "flat"                                                # under half a percent is noise
    assert run("[P.change('conversions',10,10).text,P.change('ctr',0.05,0.05).text]") == ["0%", "0 pp"]          # no change never reads "+0.0%"


DATA = ("{days:['2026-10-01','2026-10-02','2026-10-03','2026-10-04'],"
        "campaigns:[{id:'1',name:'Brand',type:'SEARCH',status:'ENABLED'},{id:'2',name:'PMax',type:'PERFORMANCE_MAX',status:'ENABLED'}],"
        "rows:[[0,0,100,10,50,2,100],[0,1,100,10,50,2,100],[1,2,200,20,100,0,0],[1,3,300,30,150,0,0]]}")


def test_totals_filters_and_groupings_agree_with_each_other():
    assert run("P.total(%s,null)" % DATA) == [700, 70, 350, 4, 200]
    assert run("P.total(%s,P.filterSet(%s,['2']))" % (DATA, DATA)) == [500, 50, 250, 0, 0]
    assert run("P.daily(%s,null).map(t=>t[2])" % DATA) == [50, 50, 100, 150]
    assert run("P.perCampaign(%s).map(t=>t[2])" % DATA) == [100, 250]
    assert run("P.filterSet(%s,[])" % DATA) is None and run("P.filterSet(%s,null)" % DATA) is None    # no filter = the whole account
    assert run("P.total(%s,P.filterSet(%s,[]))" % (DATA, DATA)) == [700, 70, 350, 4, 200]
    # a filter that matches nothing keeps nothing (not the whole account)
    assert run("P.total(%s,P.filterSet(%s,['9']))" % (DATA, DATA)) == [0, 0, 0, 0, 0]
    sums = run("(()=>{const d=P.daily(%s,null);return [P.bucket(%s.days,d,'week').totals,P.byWeekday(%s.days,d)].map(a=>a.reduce((s,t)=>s+t[2],0))})()" % (DATA, DATA, DATA))
    assert sums == [350, 350]                                    # regrouping never creates or loses spend
    assert run("P.bucket(%s.days,P.daily(%s,null),'week').labels" % (DATA, DATA)) == ["Wk Sep 28"]      # Oct 1 2026 is a Thursday
    assert run("P.byType(%s,null).map(x=>[x.key,x.t[2]])" % DATA) == [["SEARCH", 100], ["PERFORMANCE_MAX", 250]]


def test_weeks_start_on_monday_and_months_are_labelled():
    assert run("[P.dowOf('2026-10-12'),P.dowOf('2026-10-18'),P.mondayOf('2026-10-18')]") == [0, 6, "2026-10-12"]
    assert run("P.bucket(['2026-09-30','2026-10-01'],[[0,0,1,0,0],[0,0,2,0,0]],'month').labels") == ["Sep 2026", "Oct 2026"]


def test_the_hour_grid_totals_every_weekday_hour():
    r = run("(()=>{const g=P.hourGrid([[0,9,10,2,5,1,3],[2,9,1,1,1,0,0]]);const h=P.byHour(g);return [g[0][9],h[9],h[10]]})()")
    assert r == [[10, 2, 5, 1, 3], [11, 3, 6, 1, 3], [0, 0, 0, 0, 0]]


def test_insights_name_the_waste_the_engine_and_the_comparison():
    waste = run("""(()=>{
      const d={days:Array.from({length:3},(_,i)=>'2026-10-0'+(i+1)),
        campaigns:[{id:'1',name:'Good',type:'SEARCH'},{id:'2',name:'Dud',type:'DISPLAY'}],
        rows:[[0,0,1000,100,500,30,3000],[1,0,1000,100,200,0,0]]};
      return P.insights({cur:d,cmp:null,set:null,currency:'USD'}).map(i=>[i.id,i.tone,i.action&&i.action.kind,i.action&&i.action.ids,i.text]);})()""")
    ids = [w[0] for w in waste]
    assert ids[0] == "headline" and "waste" in ids
    w = waste[ids.index("waste")]
    assert w[1] == "warn" and w[2] == "filter" and w[3] == ["2"] and "Dud" in w[4] and "29%" in w[4]       # $200 of $700
    assert "concentration" in ids                                    # Good: 100% of conversions from 71% of spend
    # small spends are not "waste": under $50, or under 2% of the account's spend
    small = run("(()=>{const d={days:['2026-10-01'],campaigns:[{id:'1',name:'Big',type:'SEARCH'},{id:'2',name:'Tiny',type:'SEARCH'},"
                "{id:'3',name:'Cheap',type:'SEARCH'}],rows:[[0,0,9000,900,9000,40,1],[1,0,10,1,30,0,0],[2,0,10,1,150,0,0]]};"
                "return P.insights({cur:d,cmp:null,set:null}).map(i=>i.id);})()")
    assert "waste" not in small                                    # $30 is under $50; $150 is 1.6% of $9,180
    low = run("(()=>{const d={days:['2026-10-01'],campaigns:[{id:'1',name:'Main',type:'SEARCH'},{id:'2',name:'Thirty',type:'SEARCH'},{id:'3',name:'Sixty',type:'SEARCH'}],"
              "rows:[[0,0,900,90,910,20,1],[1,0,10,1,30,0,0],[2,0,10,1,60,0,0]]};"
              "return P.insights({cur:d,cmp:null,set:null}).filter(i=>i.id==='waste').map(i=>i.action.ids);})()")
    assert low == [["3"]]                                          # on a small account $60 counts, $30 still does not
    # no conversions anywhere means no tracking, so nothing is called wasted
    none = run("""P.insights({cur:{days:['2026-10-01'],campaigns:[{id:'1',name:'A',type:'SEARCH'}],rows:[[0,0,100,10,500,0,0]]},cmp:null,set:null}).map(i=>i.id)""")
    assert none == ["headline"]
    cmp = run("""(()=>{const mk=(c)=>({days:['2026-10-01'],campaigns:[{id:'1',name:'A',type:'SEARCH'}],rows:[[0,0,1000,100,c,10,0]]});
      return P.insights({cur:mk(300),cmp:mk(200),set:null,currency:'USD'}).map(i=>[i.id,i.tone,i.text]);})()""")
    assert cmp[0] == ["headline", "warn", "You spent $300 (+50% vs the comparison period) and got 10 conversions (0%), "
                                          "so each one cost $30.00, up 50%."]       # dearer per conversion is bad news
    assert cmp[1][0] == "mover" and "A spend rose $100 (+50%)" in cmp[1][2]


def test_an_empty_range_has_no_insights():
    assert run("P.insights({cur:{days:['2026-10-01'],campaigns:[],rows:[]},cmp:null,set:null})") == []


def test_a_csv_cell_can_never_run_as_a_formula():
    assert run("P.toCsv([['=SUM(A1)','+1','-2','@x','a,b','say \"hi\"',null,5]])") == "'=SUM(A1),'+1,'-2,'@x,\"a,b\",\"say \"\"hi\"\"\",,5"


def test_index_to_100_starts_at_the_first_real_value():
    assert run("P.indexTo100([0,0,50,100,25])") == [0, 0, 100, 200, 50]
    assert run("P.indexTo100([0,0,0])") == [None, None, None]
    assert run("[P.initials('Gentle Dental'),P.initials('1369614 B.C. LTD. (BD Engine Brake)'),P.fmtId('2497090182')]") == ["GD", "1B", "249-709-0182"]
