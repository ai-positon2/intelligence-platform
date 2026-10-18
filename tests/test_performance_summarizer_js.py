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


ORIGINAL = ["cost", "impressions", "clicks", "ctr", "cpc", "cpm", "conversions", "cvr", "cpa", "value", "roas", "aov"]


def test_a_metric_is_a_function_of_a_total_and_never_divides_by_zero():
    t = "[1000, 50, 200, 10, 900]"           # impressions, clicks, cost, conversions, value
    got = run("Object.fromEntries(%s.map(k=>[k,P.mval(k,%s)]))" % (json.dumps(ORIGINAL), t))
    assert got == {"cost": 200, "impressions": 1000, "clicks": 50, "ctr": 0.05, "cpc": 4, "cpm": 200, "conversions": 10,
                   "cvr": 0.2, "cpa": 20, "value": 900, "roas": 4.5, "aov": 90}
    nothing = run("Object.fromEntries(P.METRIC_ORDER.map(k=>[k,P.mval(k,P.zero())]))")
    assert nothing["cost"] == 0 and all(nothing[k] is None for k in ("ctr", "cpc", "cpm", "cvr", "cpa", "roas", "aov"))
    assert not [k for k, v in nothing.items() if v is not None and v != 0], "a metric of an empty total must be nothing or zero"


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
    assert run("P.total(%s,null).slice(0,5)" % DATA) == [700, 70, 350, 4, 200]
    assert run("P.total(%s,P.filterSet(%s,['2'])).slice(0,5)" % (DATA, DATA)) == [500, 50, 250, 0, 0]
    assert run("P.daily(%s,null).map(t=>t[2])" % DATA) == [50, 50, 100, 150]
    assert run("P.perCampaign(%s).map(t=>t[2])" % DATA) == [100, 250]
    assert run("P.filterSet(%s,[])" % DATA) is None and run("P.filterSet(%s,null)" % DATA) is None    # no filter = the whole account
    assert run("P.total(%s,P.filterSet(%s,[])).slice(0,5)" % (DATA, DATA)) == [700, 70, 350, 4, 200]
    # a filter that matches nothing keeps nothing (not the whole account)
    assert run("P.total(%s,P.filterSet(%s,['9'])).every(v=>v===0)" % (DATA, DATA)) is True
    sums = run("(()=>{const d=P.daily(%s,null);return [P.bucket(%s.days,d,'week').totals,P.byWeekday(%s.days,d)].map(a=>a.reduce((s,t)=>s+t[2],0))})()" % (DATA, DATA, DATA))
    assert sums == [350, 350]                                    # regrouping never creates or loses spend
    assert run("P.bucket(%s.days,P.daily(%s,null),'week').labels" % (DATA, DATA)) == ["Wk Sep 28"]      # Oct 1 2026 is a Thursday
    assert run("P.byType(%s,null).map(x=>[x.key,x.t[2]])" % DATA) == [["SEARCH", 100], ["PERFORMANCE_MAX", 250]]


def test_weeks_start_on_monday_and_months_are_labelled():
    assert run("[P.dowOf('2026-10-12'),P.dowOf('2026-10-18'),P.mondayOf('2026-10-18')]") == [0, 6, "2026-10-12"]
    assert run("P.bucket(['2026-09-30','2026-10-01'],[[0,0,1,0,0],[0,0,2,0,0]],'month').labels") == ["Sep 2026", "Oct 2026"]


def test_the_hour_grid_totals_every_weekday_hour():
    r = run("(()=>{const g=P.hourGrid([[0,9,10,2,5,1,3],[2,9,1,1,1,0,0]]);const h=P.byHour(g);return [g[0][9],h[9],h[10]].map(t=>t.slice(0,5))})()")
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


def test_a_negative_number_in_a_csv_stays_a_number():
    """It was written as the text '-2, which Excel cannot add up: every negative change in an export became text."""
    assert run("P.toCsv([[-2, -0.5, 7, '-2']])") == "-2,-0.5,7,'-2"


def test_index_to_100_starts_at_the_first_real_value():
    assert run("P.indexTo100([0,0,50,100,25])") == [0, 0, 100, 200, 50]
    assert run("P.indexTo100([0,0,0])") == [None, None, None]
    assert run("[P.initials('Gentle Dental'),P.initials('1369614 B.C. LTD. (BD Engine Brake)'),P.fmtId('2497090182')]") == ["GD", "1B", "249-709-0182"]


# ── Charts a reader can build ──────────────────────────────────────────────

def test_every_kind_of_chart_says_why_it_cannot_draw_a_grouping():
    got = run("Object.fromEntries(['line','stacked','radar','pie','hbar'].map(t=>[t,['day','dow','hour','campaign'].map(d=>P.typeBlock(d,t,false)?1:0)]))")
    assert got == {"line": [0, 0, 0, 1],            # a line over campaigns means nothing
                   "stacked": [0, 0, 1, 1],         # stacking needs a time or weekday axis
                   "radar": [1, 0, 0, 0],           # thirty days make an unreadable radar
                   "pie": [0, 0, 0, 0], "hbar": [0, 0, 0, 0]}
    assert "weeks" in run("P.typeBlock('campaign','area',false)") and "lines" in run("P.typeBlock('day','pie',true)")
    assert run("[P.typeBlock('day','line',true),P.typeBlock('day','stacked',true)]")[0] == ""


def test_changing_the_grouping_keeps_the_kind_when_it_still_works_else_picks_a_natural_one():
    assert run("[P.fixType('week','area',false),P.fixType('campaign','area',false),P.fixType('campaign','pie',false),"
               "P.fixType('device','combo',false),P.fixType('day','radar',false),P.fixType('dow','radar',false)]") \
        == ["area", "hbar", "pie", "hbar", "line", "radar"]
    assert run("P.fixType('campaign','pie',true)") == "line"                  # comparing accounts only draws lines


def test_metrics_that_cannot_fill_a_chart_are_told_so_before_it_draws_nothing():
    assert run("P.typeNeeds('pie',['cpa'])").startswith("A pie shows a share of a total")
    assert run("[P.typeNeeds('donut',['clicks']),P.typeNeeds('pie',['conversions','cpa']),P.typeNeeds('line',['cpa'])]") == ["", "", ""]
    assert run("P.typeNeeds('stacked',['roas'])").startswith("Stacking adds parts")
    assert run("[P.typeNeeds('scatter',['cost']),P.typeNeeds('scatter',['cost','clicks'])]")[1] == ""
    assert "at least two metrics" in run("P.typeNeeds('scatter',['cost'])")


def test_break_down_by_only_applies_to_kinds_and_groupings_that_can_show_it():
    assert run("[P.seriesAllowed('day','stacked',false),P.seriesAllowed('dow','line',false),P.seriesAllowed('campaign','bar',false),"
               "P.seriesAllowed('day','pie',false),P.seriesAllowed('day','line',true),P.seriesAllowed('hour','area',false)]") \
        == [True, True, False, False, False, False]


def test_metrics_of_different_sizes_share_an_axis_as_a_share_of_their_own_peak():
    assert run("P.relative([0,25,50,null])") == [0, 50, 100, None]
    assert run("P.relative([0,0])") == [0, 0] and run("P.relative([])") == []


def test_a_pie_folds_the_small_slices_into_other_but_not_a_single_one():
    out = run("(()=>{const it=n=>Array.from({length:n},(_,i)=>({label:'c'+i,t:[0,0,10-i,0,0],ids:['id'+i]}));"
              "return [P.foldTop(it(3),8,i=>i.t[2]).map(i=>i.label),P.foldTop(it(9),8,i=>i.t[2]).map(i=>i.label),"
              "P.foldTop(it(11),8,i=>i.t[2]).map(i=>[i.label,i.t[2],i.ids&&i.ids.length])]})()")
    assert out[0] == ["c0", "c1", "c2"] and out[1] == ["c%d" % i for i in range(9)]       # 9 is within 8 + 1: nothing to fold
    assert out[2][-1] == ["Other (3)", 2 + 1 + 0, 3]                          # c8, c9 and c10 folded together, ids kept for the click
    assert [r[0] for r in out[2][:8]] == ["c%d" % i for i in range(8)]


def test_break_down_groups_campaigns_by_type_or_by_top_spenders():
    d = ("{days:['2026-10-01'],campaigns:[{id:'1',name:'A',type:'SEARCH'},{id:'2',name:'B',type:'SEARCH'},"
         "{id:'3',name:'C',type:'DISPLAY'},{id:'4',name:'D',type:'SEARCH'}],"
         "rows:[[0,0,10,1,50,0,0],[1,0,10,1,30,0,0],[2,0,10,1,90,0,0],[3,0,0,0,0,0,0]]}")
    by_type = run("P.seriesGroups(%s,null,'type').map(g=>[g.name,[...g.idx]])" % d)
    assert by_type == [["Display", [2]], ["Search", [0, 1]]]            # biggest spender first; D has no activity and is left out
    top = run("P.seriesGroups(%s,null,'campaign',2).map(g=>[g.name,[...g.idx]])" % d)
    assert top == [["C", [2]], ["A", [0]], ["Other (1)", [1]]]
    assert run("P.seriesGroups(%s,new Set([0,2]),'type').map(g=>g.name)" % d) == ["Display", "Search"]      # honours the campaign filter
    assert run("P.seriesGroups(%s,null,'').length" % d) == 0


def test_choosing_a_stack_starts_with_something_to_stack_and_keeps_a_choice_already_made():
    assert run("P.withType({type:'line',series:''},'stacked')") == {"type": "stacked", "series": "type"}
    assert run("P.withType({type:'line',series:'campaign'},'stackedarea')") == {"type": "stackedarea", "series": "campaign"}
    assert run("P.withType({type:'line',series:''},'pie')") == {"type": "pie", "series": ""}


# ── The full list of Google metrics and the cards ──────────────────────────

FULL = "[1000, 50, 200, 10, 900, 12, 1080, 3, 80, 25, 400, 1, 2, 6, 300, 600, 800]"       # one value per column, in server order


def test_every_metric_is_described_and_the_chartable_ones_are_exactly_those_that_can_be_added_up():
    got = run("(()=>{const o=[];for(const k of P.METRIC_ALL){const m=P.METRICS[k];"
              "o.push([k,!!m.label,['money','money2','int','num','pct','x'].includes(m.kind),P.GROUPS.includes(m.group),!!m.hint,!!P.METRIC_COLOR[k],!!m.period,typeof m.get]);}return o;})()")
    assert len(got) >= 50
    assert all(r[1] and r[2] and r[3] and r[4] and r[5] for r in got), [r[0] for r in got if not all(r[1:6])]
    chartable = run("P.METRIC_ORDER")
    assert set(chartable) == {r[0] for r in got if not r[6]} and not {r[0] for r in got if r[6]} & set(chartable)   # shares never chart
    assert all(r[7] == "function" for r in got if not r[6]) and all(r[7] == "object" for r in got if r[6])


def test_the_new_metrics_work_out_what_google_defines():
    got = run("Object.fromEntries(['allConv','interactionRate','costPerInteraction','allConvRate','costPerAllConv','allRoas','valuePerAllConv','invalidClickRate',"
              "'engagementRate','videoViewRate','cpv','phoneThroughRate','avViewRate','avMeasureRate','avCpm','profit','crossDevice','viewThrough']"
              ".map(k=>[k,P.mval(k,%s)]))" % FULL)
    assert got == {"allConv": 12, "interactionRate": 0.08, "costPerInteraction": 2.5, "allConvRate": 0.15, "costPerAllConv": 200 / 12,
                   "allRoas": 5.4, "valuePerAllConv": 90, "invalidClickRate": 0.04, "engagementRate": 0.025, "videoViewRate": 0.4, "cpv": 0.5,
                   "phoneThroughRate": 0.02, "avViewRate": 800 / 600 if False else 600 / 800, "avMeasureRate": 0.8, "avCpm": 200 / 600 * 1000,
                   "profit": 700, "crossDevice": 1, "viewThrough": 3}


def test_a_metric_says_why_an_account_has_nothing_for_it():
    base = "[1000, 50, 200, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]"          # no value, no video, no interactions
    assert run("[P.noData('clicks',%s,[],null),P.noData('ctr',%s,[],null)]" % (base, base)) == ["", ""]
    assert run("P.noData('videoViews',%s,[],null)" % base) == "No data in this range"
    assert run("P.noData('roas',%s,[],null)" % base) == "This account does not report conversion value"
    assert run("P.noData('allConv',%s,[5],null)" % FULL) == "This platform does not report this for the account"     # pruned columns outrank the data
    assert run("[P.noData('sis',P.zero(),[],null),P.noData('sis',P.zero(),[],{search_impression_share:0.2}),P.noData('sis',P.zero(),[],{search_impression_share:null})]") \
        == ["This platform has no figure for this account and range", "", "This platform has no figure for this account and range"]


def test_a_tiny_or_huge_share_reads_the_way_google_writes_it():
    assert run("[P.fmtMetric('sis',0.0999,'USD'),P.fmtMetric('sis',0.9001,'USD'),P.fmtMetric('sis',0.4321,'USD'),P.fmtMetric('rankLost',0.0999,'USD'),P.fmtMetric('ctr',0.0999,'USD')]") \
        == ["< 10%", "> 90%", "43.2%", "< 10%", "9.99%"]               # only a share is "< 10%": a CTR of 9.99% is a number


def test_cards_are_laid_out_in_even_rows():
    assert run("[1,2,3,4,5,6,7,8,9,10,12,15,16,20].map(P.kpiCols)") == [1, 2, 3, 4, 5, 3, 5, 4, 3, 5, 4, 5, 4, 5]
    assert run("P.KPI_DEFAULT.length") == 10 and run("P.KPI_DEFAULT.every(k=>P.METRICS[k]&&!P.METRICS[k].period)") is True


def test_a_row_with_fewer_columns_than_the_page_expects_still_adds_up():
    """Ranges saved before the extra metrics existed have seven numbers per row; they must read as zero, not NaN."""
    got = run("(()=>{const d={days:['2026-10-01'],campaigns:[{id:'1',name:'A',type:'SEARCH'}],rows:[[0,0,100,10,50,2,100]]};"
              "const t=P.total(d,null);return [t.length,t.some(v=>Number.isNaN(v)),P.mval('cpa',t),P.mval('interactionRate',t),P.noData('interactions',t,[],null)]})()")
    assert got == [17, False, 25, 0, "No data in this range"]      # the number is 0; it is noData that tells the card to show a dash instead


def test_every_column_a_row_carries_is_added_up_not_just_the_first_five():
    got = run("(()=>{const row=[0,0,"+FULL[1:-1]+"];const d={days:['2026-10-01','2026-10-02'],campaigns:[{id:'1',name:'A',type:'VIDEO'}],rows:[row,[0,1,"+FULL[1:-1]+"]]};"
              "return [P.total(d,null),P.daily(d,null)[1],P.perCampaign(d)[0],P.byType(d,null)[0].t,P.campaignDaily(d)[0][0]]})()")
    assert got[0][8] == 160 and got[1][16] == 800 and got[2][10] == 800 and got[3][15] == 1200 and got[4][5] == 12        # interactions, measurable, video, viewable, all conversions (two days summed where it says total)


# ── Meta ────────────────────────────────────────────────────────────────────

def test_reach_and_frequency_are_whole_period_tiles_not_chartable_over_days():
    assert run("[P.METRICS.reach.period,P.METRICS.reach.share,P.METRICS.frequency.period,P.METRICS.frequency.share]") == [True, "reach", True, "frequency"]
    assert run("P.METRIC_ORDER.indexOf('reach')") == -1 and run("P.METRIC_ORDER.indexOf('frequency')") == -1
    assert run("P.METRIC_ALL.indexOf('reach')>=0") is True and run("P.GROUPS.indexOf('Reach')>=0") is True
    assert run("P.noData('reach',[0],[],{reach:5000})") == ""
    assert "no figure" in run("P.noData('reach',[0],[],{reach:null})")


def test_meta_campaign_types_and_placements_read_as_words():
    assert run("P.label(P.TYPE_LABEL,'META_SALES')") == "Meta · Sales"
    assert run("P.label(P.TYPE_LABEL,'META_APP')") == "Meta · App promotion"
    assert run("P.label(P.NETWORK_LABEL,'INSTAGRAM')") == "Instagram" and run("P.label(P.NETWORK_LABEL,'AUDIENCE_NETWORK')") == "Meta Audience Network"
    assert run("P.label(P.TYPE_LABEL,'SEARCH')") == "Search"                    # Google's are untouched


def test_the_reason_a_metric_is_empty_does_not_blame_google_for_a_meta_account():
    why = run("P.noData('allConv',[0,0,0,0,0,5,0,0,0,0,0,0,0,0,0,0,0],[5],null)")
    assert "Google" not in why and "platform" in why


def _meta(*campaigns):
    """insights for one-day Meta campaigns given as (type, cost, conversions)."""
    cs = ",".join("{id:'%d',name:'C%d',type:'%s'}" % (i + 1, i + 1, c[0]) for i, c in enumerate(campaigns))
    rows = ",".join("[%d,0,1000,100,%s,%s,0]" % (i, c[1], c[2]) for i, c in enumerate(campaigns))
    return run("P.insights({cur:{days:['2026-10-01'],campaigns:[%s],rows:[%s]},cmp:null,set:null,currency:'USD'}).map(i=>[i.id,i.text])" % (cs, rows))


@pytest.mark.parametrize("kind", ["META_TRAFFIC", "META_AWARENESS", "META_ENGAGEMENT"])
def test_a_meta_campaign_that_cannot_report_conversions_is_not_called_wasted_spend(kind):
    out = _meta(("META_LEADS", 1400, 70), (kind, 1400, 0))
    ids = [i[0] for i in out]
    assert "waste" not in ids and "concentration" not in ids
    assert out[0][1] == "You spent $2,800 and got 70 conversions at $40.00 each, counting $1,400 spent on Meta Traffic, Awareness or Engagement campaigns, which cannot report conversions."


def test_a_meta_leads_campaign_with_no_leads_is_still_wasted_spend():
    out = _meta(("META_LEADS", 1400, 70), ("META_LEADS", 1400, 0))
    assert [i[0] for i in out].count("waste") == 1 and "counting" not in out[0][1]


def test_the_share_of_spend_is_against_the_spend_that_can_bring_a_conversion():
    # Leads $1,000 of the $4,000 spent brings every conversion; the $3,000 of awareness could not have brought any
    out = dict(_meta(("META_LEADS", 1000, 40), ("META_AWARENESS", 3000, 0)))
    assert "concentration" not in out                              # 100% of conversions from 100% of the spend that can bring one: nothing stands out


def test_the_hour_rows_hold_googles_weekday_hours_and_metas_hours_alike():
    # Meta sends [hour, ...], Google [weekday, hour, ...]: a total by hour counts both, and Meta alone is not an empty chart
    assert run("P.hourRows({dowHour:[[2,9,5,1,1,1,1]],hour:[[9,7,2,2,2,2]]})") == [[2, 9, 5, 1, 1, 1, 1], [0, 9, 7, 2, 2, 2, 2]]
    assert run("P.hourRows({dowHour:[],hour:[[9,7,2,2,2,2]]})") == [[0, 9, 7, 2, 2, 2, 2]]
    assert run("P.hourRows({dowHour:[[2,9,5,1,1,1,1]]})") == [[2, 9, 5, 1, 1, 1, 1]] and run("P.hourRows(null)") == []
    r = run("(()=>{const e={dowHour:[[2,9,10,1,1,1,1]],hour:[[9,7,2,2,2,2]]};return P.byHour(P.hourGrid(P.hourRows(e)))[9].slice(0,4)})()")
    assert r == [17, 3, 3, 3]


def test_the_hour_of_day_chart_reads_the_hours_of_both_platforms():
    page = open(os.path.join(ROOT, "static", "js", "performance_summarizer.js"), encoding="utf-8").read()
    chart = page[page.index('if(dim==="hour"){'):][:200]            # the Explorer's hour chart
    assert "P.hourGrid(P.hourRows(S.extras))" in chart and "S.extras.dowHour" not in chart
