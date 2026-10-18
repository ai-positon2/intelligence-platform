/* Performance Summarizer: the numbers, with no page code.
 *
 * Everything the dashboard computes from the rows the server sends lives here
 * so it can be tested on its own (tests/test_performance_summarizer_js.py runs
 * this file in Node): date ranges and their comparison range, the metric
 * catalog, totals, grouping by day / week / month / weekday / campaign, the
 * way a change is worded and coloured, and the "what stands out" insights.
 *
 * A total is NB numbers, one per column the server sends: [impressions, clicks,
 * cost, conversions, conversion value, all conversions, all conversion value,
 * view-through conversions, interactions, engagements, video views, cross-device
 * conversions, invalid clicks, phone calls, phone impressions, viewable
 * impressions, measurable impressions]. Every metric on the page is one function
 * of a total, so a metric can be charted at any grouping without the server
 * knowing about it. The auction-share metrics (impression share and its lost
 * parts) cannot be added up over days, so they are read once per range and are
 * flagged `period`: tiles can show them, charts cannot.
 */
(function(root){
"use strict";

var DAY = 864e5;
var IMPR = 0, CLK = 1, COST = 2, CONV = 3, VAL = 4, ALLC = 5, ALLV = 6, VTC = 7, INT = 8, ENG = 9, VID = 10, XDV = 11,
    INV = 12, PCALL = 13, PIMPR = 14, AVV = 15, AVM = 16;
var NB = 17;

/* ── Dates (all as YYYY-MM-DD strings, arithmetic in UTC so DST never shifts a day) ── */
function pad(n){return n<10?"0"+n:""+n;}
function parseD(s){var p=s.split("-");return Date.UTC(+p[0],+p[1]-1,+p[2]);}
function fmtD(ms){var d=new Date(ms);return d.getUTCFullYear()+"-"+pad(d.getUTCMonth()+1)+"-"+pad(d.getUTCDate());}
function addDays(s,n){return fmtD(parseD(s)+n*DAY);}
function diffDays(a,b){return Math.round((parseD(b)-parseD(a))/DAY);}
function localToday(){var d=new Date();return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate());}
function monthStart(s){return s.slice(0,8)+"01";}
function addMonths(s,n){
  var p=s.split("-"), y=+p[0], m=+p[1]-1+n;
  y+=Math.floor(m/12); m=((m%12)+12)%12;
  return y+"-"+pad(m+1)+"-01";
}
function shiftYear(s,n){
  var p=s.split("-"), y=+p[0]+n, m=+p[1], d=+p[2];
  var last=new Date(Date.UTC(y,m,0)).getUTCDate();
  return y+"-"+pad(m)+"-"+pad(Math.min(d,last));
}
function dowOf(s){return (new Date(parseD(s)).getUTCDay()+6)%7;}   // Monday = 0
function mondayOf(s){return addDays(s,-dowOf(s));}

var PRESETS = [
  {id:"last7",label:"Last 7 days"},{id:"last14",label:"Last 14 days"},{id:"last30",label:"Last 30 days"},
  {id:"last90",label:"Last 90 days"},{id:"mtd",label:"This month"},{id:"lastMonth",label:"Last month"}
];

/* The range a preset means on `today`. Data runs through yesterday, so
   "last 7 days" is the 7 complete days before today. */
function presetRange(id,today){
  today=today||localToday();
  var y=addDays(today,-1);
  switch(id){
    case "last7": return {start:addDays(y,-6),end:y};
    case "last14": return {start:addDays(y,-13),end:y};
    case "last30": return {start:addDays(y,-29),end:y};
    case "last90": return {start:addDays(y,-89),end:y};
    case "mtd":
      if(monthStart(y)<=y && monthStart(today)<=y) return {start:monthStart(today),end:y};
      return {start:addMonths(monthStart(today),-1),end:y};          // the 1st: nothing yet this month
    case "lastMonth":
      var s=addMonths(monthStart(today),-1);
      return {start:s,end:addDays(monthStart(today),-1)};
  }
  return presetRange("last30",today);
}

/* The range to compare against: the equal-length period just before, or the same dates a year earlier. */
function compareRange(range,mode){
  if(mode==="yoy") return {start:shiftYear(range.start,-1),end:shiftYear(range.end,-1)};
  if(mode==="prev"){
    var len=diffDays(range.start,range.end)+1, end=addDays(range.start,-1);
    return {start:addDays(end,-(len-1)),end:end};
  }
  return null;
}

/* ── Metric catalog ── */
function ratio(a,b,k){return b?a/b*(k||1):null;}
function col(i){return function(t){return t[i];};}
function M(label,kind,better,group,get,hint,extra){
  var o={label:label,kind:kind,better:better,group:group,get:get,hint:hint};
  if(extra) for(var k in extra) o[k]=extra[k];
  return o;
}
/* `need` lists the columns that must be above zero for the metric to mean anything (otherwise the account simply
   has no such data); `value` marks metrics that need the account to report conversion value. */
var METRICS = {
  cost:        M("Spend","money",0,"Cost",col(COST),"What you paid"),
  profit:      M("Net value","money",1,"Cost",function(t){return t[VAL]-t[COST];},"Conversion value minus spend",{value:true}),
  cpm:         M("CPM","money2",0,"Cost",function(t){return ratio(t[COST],t[IMPR],1000);},"Cost per 1,000 impressions"),
  impressions: M("Impressions","int",1,"Volume",col(IMPR),"Times an ad was shown"),
  clicks:      M("Clicks","int",1,"Volume",col(CLK),"Times an ad was clicked"),
  interactions:M("Interactions","int",1,"Volume",col(INT),"Clicks plus other ad actions, such as video views",{need:[INT]}),
  engagements: M("Engagements","int",1,"Volume",col(ENG),"Times people engaged with an expandable ad",{need:[ENG]}),
  invalidClicks:M("Invalid clicks","int",-1,"Volume",col(INV),"Clicks Google filtered out as not genuine"),
  conversions: M("Conversions","num",1,"Results",col(CONV),"Actions you counted as a win"),
  value:       M("Conv. value","money",1,"Results",col(VAL),"Value of those conversions",{value:true}),
  allConv:     M("All conversions","num",1,"Results",col(ALLC),"Every conversion action, including ones not counted in Conversions",{need:[ALLC]}),
  allValue:    M("All conv. value","money",1,"Results",col(ALLV),"Value of all conversions",{need:[ALLV],value:true}),
  viewThrough: M("View-through conv.","num",1,"Results",col(VTC),"Conversions after seeing, not clicking, an ad",{need:[VTC]}),
  crossDevice: M("Cross-device conv.","num",1,"Results",col(XDV),"Conversions that began on another device",{need:[XDV]}),
  ctr:         M("CTR","pct",1,"Efficiency",function(t){return ratio(t[CLK],t[IMPR]);},"Clicks per impression"),
  cpc:         M("Avg. CPC","money2",-1,"Efficiency",function(t){return ratio(t[COST],t[CLK]);},"Cost per click"),
  cvr:         M("Conv. rate","pct",1,"Efficiency",function(t){return ratio(t[CONV],t[CLK]);},"Conversions per click"),
  cpa:         M("Cost / conv.","money2",-1,"Efficiency",function(t){return ratio(t[COST],t[CONV]);},"What one conversion costs"),
  roas:        M("ROAS","x",1,"Efficiency",function(t){return ratio(t[VAL],t[COST]);},"Value returned per $1 spent",{value:true}),
  aov:         M("Value / conv.","money2",1,"Efficiency",function(t){return ratio(t[VAL],t[CONV]);},"Average value of a conversion",{value:true}),
  interactionRate:M("Interaction rate","pct",1,"Efficiency",function(t){return ratio(t[INT],t[IMPR]);},"Interactions per impression",{need:[INT]}),
  costPerInteraction:M("Avg. cost","money2",-1,"Efficiency",function(t){return ratio(t[COST],t[INT]);},"Cost per interaction",{need:[INT]}),
  allConvRate: M("All conv. rate","pct",1,"Efficiency",function(t){return ratio(t[ALLC],t[INT]);},"All conversions per interaction",{need:[ALLC,INT]}),
  costPerAllConv:M("Cost / all conv.","money2",-1,"Efficiency",function(t){return ratio(t[COST],t[ALLC]);},"What one conversion of any kind costs",{need:[ALLC]}),
  allRoas:     M("All conv. value / cost","x",1,"Efficiency",function(t){return ratio(t[ALLV],t[COST]);},"All-conversion value per $1 spent",{need:[ALLV],value:true}),
  valuePerAllConv:M("Value / all conv.","money2",1,"Efficiency",function(t){return ratio(t[ALLV],t[ALLC]);},"Average value of a conversion of any kind",{need:[ALLC,ALLV],value:true}),
  invalidClickRate:M("Invalid click rate","pct",-1,"Efficiency",function(t){return ratio(t[INV],t[CLK]);},"Share of clicks Google filtered out"),
  engagementRate:M("Engagement rate","pct",1,"Engagement",function(t){return ratio(t[ENG],t[IMPR]);},"Engagements per impression",{need:[ENG]}),
  videoViews:  M("Video views","int",1,"Video",col(VID),"Times a video ad was watched",{need:[VID]}),
  videoViewRate:M("Video view rate","pct",1,"Video",function(t){return ratio(t[VID],t[IMPR]);},"Views per impression",{need:[VID]}),
  cpv:         M("Avg. CPV","money2",-1,"Video",function(t){return ratio(t[COST],t[VID]);},"Cost per video view",{need:[VID]}),
  phoneCalls:  M("Phone calls","int",1,"Calls",col(PCALL),"Calls placed from your ads",{need:[PCALL]}),
  phoneImpr:   M("Phone impressions","int",1,"Calls",col(PIMPR),"Times a call button was shown",{need:[PIMPR]}),
  phoneThroughRate:M("Phone-through rate","pct",1,"Calls",function(t){return ratio(t[PCALL],t[PIMPR]);},"Calls per phone impression",{need:[PIMPR]}),
  avViewable:  M("Viewable impr.","int",1,"Viewability",col(AVV),"Impressions that could be seen",{need:[AVV]}),
  avMeasurable:M("Measurable impr.","int",1,"Viewability",col(AVM),"Impressions Google could measure for viewability",{need:[AVM]}),
  avViewRate:  M("Viewable rate","pct",1,"Viewability",function(t){return ratio(t[AVV],t[AVM]);},"Viewable per measurable impression",{need:[AVM]}),
  avMeasureRate:M("Measurable rate","pct",1,"Viewability",function(t){return ratio(t[AVM],t[IMPR]);},"Measurable per impression",{need:[AVM]}),
  avCpm:       M("Viewable CPM","money2",-1,"Viewability",function(t){return ratio(t[COST],t[AVV],1000);},"Cost per 1,000 viewable impressions",{need:[AVV]}),
  sis:         M("Search impr. share","pct",1,"Auction share",null,"Searches your ads showed for, out of those they could",{period:true,share:"search_impression_share"}),
  topSis:      M("Search top impr. share","pct",1,"Auction share",null,"Share of impressions shown above organic results",{period:true,share:"search_top_impression_share"}),
  absTopSis:   M("Search abs. top impr. share","pct",1,"Auction share",null,"Share of impressions shown in the very first position",{period:true,share:"search_absolute_top_impression_share"}),
  budgetLost:  M("Search lost IS (budget)","pct",-1,"Auction share",null,"Impression share lost because the budget ran out",{period:true,share:"search_budget_lost_impression_share"}),
  rankLost:    M("Search lost IS (rank)","pct",-1,"Auction share",null,"Impression share lost to a low ad rank",{period:true,share:"search_rank_lost_impression_share"}),
  budgetLostTop:M("Lost top IS (budget)","pct",-1,"Auction share",null,"Top impression share lost to budget",{period:true,share:"search_budget_lost_top_impression_share"}),
  rankLostTop: M("Lost top IS (rank)","pct",-1,"Auction share",null,"Top impression share lost to ad rank",{period:true,share:"search_rank_lost_top_impression_share"}),
  contentSis:  M("Display impr. share","pct",1,"Auction share",null,"Display impressions won, out of those you could",{period:true,share:"content_impression_share"}),
  contentBudgetLost:M("Display lost IS (budget)","pct",-1,"Auction share",null,"Display share lost to budget",{period:true,share:"content_budget_lost_impression_share"}),
  contentRankLost:M("Display lost IS (rank)","pct",-1,"Auction share",null,"Display share lost to ad rank",{period:true,share:"content_rank_lost_impression_share"}),
  topPct:      M("Top of page rate","pct",1,"Auction share",null,"Impressions shown above organic results",{period:true,share:"top_impression_percentage"}),
  absTopPct:   M("Abs. top of page rate","pct",1,"Auction share",null,"Impressions shown in the very first position",{period:true,share:"absolute_top_impression_percentage"}),
  clickShare:  M("Search click share","pct",1,"Auction share",null,"Clicks you got out of those you could have",{period:true,share:"search_click_share"}),
  exactSis:    M("Search exact-match IS","pct",1,"Auction share",null,"Impression share on exact-match searches",{period:true,share:"search_exact_match_impression_share"}),
  reach:       M("Reach","int",1,"Reach",null,"People who saw an ad at least once (Meta)",{period:true,share:"reach"}),
  frequency:   M("Frequency","num",0,"Reach",null,"Times the average person saw an ad (Meta)",{period:true,share:"frequency"})
};
var GROUPS = ["Cost","Volume","Results","Efficiency","Engagement","Video","Calls","Viewability","Auction share","Reach"];
/* All metrics, grouped; the chartable ones (everything that can be added up over days) are METRIC_ORDER. */
var METRIC_ALL = Object.keys(METRICS).sort(function(a,b){return GROUPS.indexOf(METRICS[a].group)-GROUPS.indexOf(METRICS[b].group);});
var METRIC_ORDER = METRIC_ALL.filter(function(k){return !METRICS[k].period;});
/* How many columns lay n cards out in even rows (5 across for ten, 4 for eight, 3 for nine ...). */
function kpiCols(n){return n<=5?Math.max(1,n):n%5===0?5:n%4===0?4:n%3===0?3:5;}
var KPI_DEFAULT = ["cost","impressions","clicks","ctr","cpc","conversions","cvr","cpa","value","roas"];
function hsl(h,s,l){
  s/=100; l/=100;
  var a=s*Math.min(l,1-l), f=function(n){var k=(n+h/30)%12, c=l-a*Math.max(-1,Math.min(k-3,9-k,1)); return Math.round(255*c).toString(16).padStart(2,"0");};
  return "#"+f(0)+f(8)+f(4);
}
var GROUP_HUE = {"Cost":42,"Volume":232,"Results":92,"Efficiency":190,"Engagement":322,"Video":2,"Calls":160,"Viewability":268,"Auction share":206,"Reach":300};
var METRIC_COLOR = {cost:"#fbbf24",impressions:"#818cf8",clicks:"#22d3ee",ctr:"#2dd4bf",cpc:"#fb923c",cpm:"#a78bfa",
  conversions:"#a3e635",cvr:"#e879f9",cpa:"#fb7185",value:"#34d399",roas:"#38bdf8",aov:"#f472b6"};
(function(){   // every other metric: its group's hue, shifted a little per metric so neighbours differ
  var seen={};
  METRIC_ALL.forEach(function(k){
    var g=METRICS[k].group, i=seen[g]=(seen[g]||0)+1;
    if(!METRIC_COLOR[k]) METRIC_COLOR[k]=hsl((GROUP_HUE[g]+i*23)%360,78,64);
  });
})();
var SERIES = ["#fbbf24","#22d3ee","#e879f9","#a3e635","#fb7185","#818cf8","#fb923c","#2dd4bf"];

function mval(key,t){var m=METRICS[key]; return m.get?m.get(t):null;}
/* Why a metric has nothing to show for this account and range, or "" when it does. `missing` are the columns Google
   would not return; `shares` the whole-period auction shares. */
function noData(key,tc,missing,shares){
  var m=METRICS[key];
  if(m.period) return shares&&shares[m.share]!=null?"":"This platform has no figure for this account and range";
  var miss=(m.need||[]).filter(function(i){return missing&&missing.indexOf(i)>=0;});
  if(miss.length) return "This platform does not report this for the account";
  if(m.need&&m.need.some(function(i){return !tc[i];})) return "No data in this range";
  if(m.value&&!tc[VAL]) return "This account does not report conversion value";
  return "";
}

/* ── Formatting ── */
var _cur = {};
function curFmt(cur,dec){
  var k=cur+dec;
  if(!_cur[k]){
    try{_cur[k]=new Intl.NumberFormat("en-US",{style:"currency",currency:cur||"USD",minimumFractionDigits:dec,maximumFractionDigits:dec});}
    catch(e){_cur[k]=new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",minimumFractionDigits:dec,maximumFractionDigits:dec});}
  }
  return _cur[k];
}
function sym(cur){
  var p=curFmt(cur,0).formatToParts(0), s="";
  p.forEach(function(x){if(x.type==="currency")s=x.value;});
  return s||"$";
}
function compact(v){
  var a=Math.abs(v), s=v<0?"-":"";
  if(a>=1e9) return s+(a/1e9).toFixed(a>=1e10?0:1).replace(/\.0$/,"")+"B";
  if(a>=1e6) return s+(a/1e6).toFixed(a>=1e7?0:1).replace(/\.0$/,"")+"M";
  if(a>=1e4) return s+(a/1e3).toFixed(a>=1e5?0:1).replace(/\.0$/,"")+"k";
  return null;
}
/* kind: money | money2 | int | num | pct | x.  small=true gives the short form used on axes and tiles. */
function fmt(kind,v,cur,small){
  if(v==null||!isFinite(v)) return "–";
  switch(kind){
    case "money":{
      if(small){var c=compact(v); if(c!==null) return sym(cur)+c; if(Math.abs(v)<100&&v%1) return curFmt(cur,2).format(v);}
      return curFmt(cur,0).format(v);
    }
    case "money2":
      if(small&&Math.abs(v)>=1000){var c2=compact(v); if(c2!==null) return sym(cur)+c2;}
      return curFmt(cur,2).format(v);
    case "int":{
      if(small){var c3=compact(v); if(c3!==null) return c3;}
      return Math.round(v).toLocaleString("en-US");
    }
    case "num":{
      if(small){var c4=compact(v); if(c4!==null) return c4;}
      var r=Math.round(v*10)/10;
      return r.toLocaleString("en-US",{maximumFractionDigits:1});
    }
    case "pct": {var p=v*100; return (Math.abs(p)>=10?p.toFixed(1):p.toFixed(2))+"%";}
    case "x": return v.toFixed(2)+"x";
  }
  return String(v);
}
function fmtMetric(key,v,cur,small){
  if(METRICS[key].period&&v!=null){            // Google reports a tiny share as 0.0999 ("< 10%") and a huge one as 0.9001 ("> 90%")
    if(Math.abs(v-0.0999)<0.00005) return "< 10%";
    if(Math.abs(v-0.9001)<0.00005) return "> 90%";
  }
  return fmt(METRICS[key].kind,v,cur,small);
}

/* ── Changes ── */
/* How a change from prev to cur reads, and whether it is good news for that metric. */
function change(key,cur,prev){
  var m=METRICS[key];
  if(cur==null||prev==null||!isFinite(cur)||!isFinite(prev)) return {text:"–",dir:0,tone:"flat",pct:null};
  var d=cur-prev, dir=Math.abs(d)<1e-12?0:(d>0?1:-1), text, pct=null;
  if(m.kind==="pct"){
    var pp=d*100;
    text=(dir>=0?"+":"−")+Math.abs(pp).toFixed(Math.abs(pp)>=10?1:2)+" pp";
    if(prev) pct=d/Math.abs(prev);
  }else{
    if(!prev){return {text:cur?"new":"–",dir:cur?1:0,tone:"flat",pct:null};}
    pct=d/Math.abs(prev);
    var a=Math.abs(pct)*100;
    text=(dir>=0?"+":"−")+(a>=1000?Math.round(a).toLocaleString("en-US"):a>=10?a.toFixed(0):a.toFixed(1))+"%";
  }
  if(dir===0) text=m.kind==="pct"?"0 pp":"0%";
  var tone="flat";
  if(dir!==0&&m.better!==0&&Math.abs(pct==null?1:pct)>=0.005) tone=(dir*m.better>0)?"good":"bad";
  return {text:text,dir:dir,tone:tone,pct:pct};
}

/* ── Totals ── */
function zero(){var a=new Array(NB);for(var i=0;i<NB;i++)a[i]=0;return a;}
function addTo(a,b){for(var i=0;i<NB;i++)a[i]+=b[i]||0;return a;}
function addRow(t,r){for(var i=0;i<NB;i++)t[i]+=r[2+i]||0;}      // a row is [campaign, day, ...one number per column]
function sel(set,idx){return !set||set.has(idx);}

/* The campaigns a filter keeps, as a Set of indexes into data.campaigns (null = all). */
function filterSet(data,ids){
  if(!ids||!ids.length) return null;
  var want={};ids.forEach(function(i){want[i]=1;});
  var s=new Set();
  data.campaigns.forEach(function(c,i){if(want[c.id])s.add(i);});
  return s;
}
function total(data,set){
  var t=zero();
  data.rows.forEach(function(r){if(sel(set,r[0]))addRow(t,r);});
  return t;
}
/* One total per day in the range. */
function daily(data,set){
  var out=data.days.map(zero);
  data.rows.forEach(function(r){if(sel(set,r[0]))addRow(out[r[1]],r);});
  return out;
}
/* One total per campaign, same order as data.campaigns. */
function perCampaign(data){
  var out=data.campaigns.map(zero);
  data.rows.forEach(function(r){addRow(out[r[0]],r);});
  return out;
}
/* Per campaign, per day (for row sparklines): index by campaign -> array over days of a total. */
function campaignDaily(data){
  var out=data.campaigns.map(function(){return data.days.map(zero);});
  data.rows.forEach(function(r){addRow(out[r[0]][r[1]],r);});
  return out;
}
function byType(data,set){
  var acc={};
  data.rows.forEach(function(r){
    if(!sel(set,r[0])) return;
    addRow(acc[data.campaigns[r[0]].type]||(acc[data.campaigns[r[0]].type]=zero()),r);
  });
  return Object.keys(acc).map(function(k){return {key:k,t:acc[k]};});
}

/* ── Grouping a daily series ── */
var DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function dayLabel(s){var p=s.split("-");return MON[+p[1]-1]+" "+(+p[2]);}
function monthLabel(k){var p=k.split("-");return MON[+p[1]-1]+" "+p[0];}

/* Bucket daily totals by day, week (Monday start) or month. Returns {keys, labels, totals}. */
function bucket(days,totals,grain){
  if(grain==="day") return {keys:days.slice(),labels:days.map(dayLabel),totals:totals.map(function(t){return t.slice();})};
  var keys=[],labels=[],out=[],at={};
  days.forEach(function(d,i){
    var k=grain==="week"?mondayOf(d):d.slice(0,7);
    if(!(k in at)){at[k]=keys.length;keys.push(k);labels.push(grain==="week"?"Wk "+dayLabel(k):monthLabel(k));out.push(zero());}
    addTo(out[at[k]],totals[i]);
  });
  return {keys:keys,labels:labels,totals:out};
}
/* Seven totals, Monday first. */
function byWeekday(days,totals){
  var out=[0,1,2,3,4,5,6].map(zero);
  days.forEach(function(d,i){addTo(out[dowOf(d)],totals[i]);});
  return out;
}
/* Per-hour totals (24) and a 7x24 grid from the server's weekday-hour rows. */
function hourGrid(rows){
  var grid=[];for(var d=0;d<7;d++){grid.push([]);for(var h=0;h<24;h++)grid[d].push(zero());}
  (rows||[]).forEach(function(r){if(grid[r[0]]&&grid[r[0]][r[1]]) addTo(grid[r[0]][r[1]],r.slice(2,2+NB));});
  return grid;
}
function byHour(grid){
  var out=[];for(var h=0;h<24;h++){var t=zero();for(var d=0;d<7;d++)addTo(t,grid[d][h]);out.push(t);}
  return out;
}

var TYPE_LABEL = {SEARCH:"Search",PERFORMANCE_MAX:"Performance Max",DISPLAY:"Display",SHOPPING:"Shopping",VIDEO:"Video",
  DEMAND_GEN:"Demand Gen",DISCOVERY:"Demand Gen",SMART:"Smart",LOCAL:"Local",LOCAL_SERVICES:"Local Services",
  MULTI_CHANNEL:"App",TRAVEL:"Travel",HOTEL:"Hotel",UNKNOWN:"Other",UNSPECIFIED:"Other",
  META_SALES:"Meta · Sales",META_LEADS:"Meta · Leads",META_TRAFFIC:"Meta · Traffic",META_AWARENESS:"Meta · Awareness",
  META_ENGAGEMENT:"Meta · Engagement",META_APP:"Meta · App promotion",META_OTHER:"Meta · Other"};
var DEVICE_LABEL = {MOBILE:"Mobile",DESKTOP:"Desktop",TABLET:"Tablet",CONNECTED_TV:"Connected TV",OTHER:"Other",UNKNOWN:"Other",UNSPECIFIED:"Other"};
var NETWORK_LABEL = {SEARCH:"Google Search",SEARCH_PARTNERS:"Search partners",CONTENT:"Display network",YOUTUBE:"YouTube",
  YOUTUBE_SEARCH:"YouTube search",YOUTUBE_WATCH:"YouTube videos",MIXED:"Cross-network",GOOGLE_TV:"Google TV",
  GOOGLE_OWNED_CHANNELS:"Google-owned",UNKNOWN:"Other",UNSPECIFIED:"Other",
  FACEBOOK:"Facebook",INSTAGRAM:"Instagram",AUDIENCE_NETWORK:"Meta Audience Network",MESSENGER:"Messenger",THREADS:"Threads"};
function label(map,k){return map[k]||String(k||"Other").toLowerCase().replace(/_/g," ").replace(/^./,function(c){return c.toUpperCase();});}

/* ── Insights ──
   ctx: {cur, cmp, set, sis, cur$ (currency)}. Returns up to 7 of
   {id, tone: good|warn|info, title, text, action}. An action is
   {kind:"filter", ids:[campaign id]} or {kind:"explore", metrics:[], dim:""}. */
var RESULTLESS = {META_TRAFFIC:1,META_AWARENESS:1,META_ENGAGEMENT:1};
function insights(ctx){
  var cur=ctx.cur, cmp=ctx.cmp, set=ctx.set, C=ctx.currency||"USD", out=[];
  var tc=total(cur,set); if(!tc[COST]&&!tc[IMPR]) return out;
  var money=function(v){return fmt("money",v,C,true);}, money2=function(v){return fmt("money2",v,C);};
  var per=perCampaign(cur), camps=cur.campaigns;
  var pick=function(){var r=[];per.forEach(function(t,i){if(sel(set,i)) r.push({i:i,t:t,c:camps[i]});});return r;}();

  /* 1. The headline */
  var cpa=mval("cpa",tc);
  if(cmp){
    var tp=total(cmp,set);
    var dCost=change("cost",tc[COST],tp[COST]), dConv=change("conversions",tc[CONV],tp[CONV]), dCpa=change("cpa",cpa,mval("cpa",tp));
    var txt="You spent "+money(tc[COST])+" ("+dCost.text.replace("−","-")+" vs the comparison period)";
    if(tc[CONV]||tp[CONV]){
      txt+=" and got "+fmt("num",tc[CONV],C)+" conversions ("+dConv.text.replace("−","-")+")";
      if(cpa!=null&&mval("cpa",tp)!=null) txt+=", so each one cost "+money2(cpa)+(dCpa.dir<0?", down ":dCpa.dir>0?", up ":", flat ")+(dCpa.pct==null?"":Math.abs(dCpa.pct*100).toFixed(0)+"%");
    }
    out.push({id:"headline",tone:dCpa.tone==="good"?"good":dCpa.tone==="bad"?"warn":"info",title:"The headline",text:txt+".",
      action:{kind:"explore",metrics:["cost","conversions"],dim:"day"}});
  }else{
    out.push({id:"headline",tone:"info",title:"The headline",
      text:"You spent "+money(tc[COST])+(tc[CONV]?" and got "+fmt("num",tc[CONV],C)+" conversions at "+money2(cpa)+" each":" with "+fmt("int",tc[CLK],C)+" clicks")+".",
      action:{kind:"explore",metrics:["cost","conversions"],dim:"day"}});
  }

  /* Meta's Traffic, Awareness and Engagement campaigns have no conversion to report (their result is a click, a view or a reaction), so a zero there is
     not waste and they must not be set against the campaigns that do report conversions. */
  var counts=function(p){return !RESULTLESS[p.c.type];};
  var noResCost=pick.reduce(function(a,p){return counts(p)?a:a+p.t[COST];},0);
  if(noResCost>0&&tc[CONV]>0){
    var hl=out[out.length-1];
    hl.text=hl.text.replace(/\.$/,", counting "+money(noResCost)+" spent on Meta Traffic, Awareness or Engagement campaigns, which cannot report conversions.");
  }

  /* 2. Spend with nothing to show for it */
  if(tc[CONV]>0){
    var floor=Math.max(50,tc[COST]*0.02);
    var dead=pick.filter(function(p){return counts(p)&&p.t[CONV]===0&&p.t[COST]>=floor;});
    if(dead.length){
      var dc=dead.reduce(function(a,p){return a+p.t[COST];},0);
      out.push({id:"waste",tone:"warn",title:"Spend without a conversion",
        text:dead.length+(dead.length===1?" campaign spent ":" campaigns spent ")+money(dc)+" ("+Math.round(dc/tc[COST]*100)+"% of spend) with no conversions: "+
          dead.slice().sort(function(a,b){return b.t[COST]-a.t[COST];}).slice(0,2).map(function(p){return p.c.name;}).join(", ")+(dead.length>2?" and more":"")+".",
        action:{kind:"filter",ids:dead.map(function(p){return p.c.id;})}});
    }
  }

  /* 3. The campaign doing the most (or least) for its share of spend */
  var rCost=tc[COST]-noResCost;                                  // the spend that could have brought a conversion
  if(tc[CONV]>=10&&rCost>0){
    var best=null;
    pick.filter(counts).sort(function(a,b){return b.t[COST]-a.t[COST];}).slice(0,6).forEach(function(p){
      var cs=p.t[COST]/rCost, vs=p.t[CONV]/tc[CONV];
      if(cs<0.08) return;
      var r=vs/cs, dev=Math.abs(Math.log(r||0.0001));
      if(!best||dev>best.dev) best={p:p,cs:cs,vs:vs,r:r,dev:dev};
    });
    if(best&&(best.r>=1.3||best.r<=0.6)){
      var good=best.r>=1.3;
      out.push({id:"concentration",tone:good?"good":"warn",title:good?"Your engine":"A heavy spender",
        text:best.p.c.name+(good?" delivers ":" takes ")+(good?Math.round(best.vs*100)+"% of conversions from "+Math.round(best.cs*100)+"% of spend.":
          Math.round(best.cs*100)+"% of spend but delivers "+Math.round(best.vs*100)+"% of conversions."),
        action:{kind:"filter",ids:[best.p.c.id]}});
    }
  }

  /* 4. The biggest mover against the comparison period */
  if(cmp){
    var pc=perCampaign(cmp), pcIdx={};
    cmp.campaigns.forEach(function(c,i){pcIdx[c.id]=i;});
    var mv=null;
    pick.forEach(function(p){
      var j=pcIdx[p.c.id], before=j==null?0:pc[j][COST], d=p.t[COST]-before;
      if(Math.abs(d)>=Math.max(50,tc[COST]*0.03)&&(!mv||Math.abs(d)>Math.abs(mv.d))) mv={p:p,d:d,before:before};
    });
    if(mv){
      var up=mv.d>0;
      out.push({id:"mover",tone:"info",title:"Biggest mover",
        text:mv.p.c.name+(mv.before?" spend "+(up?"rose ":"fell ")+money(Math.abs(mv.d))+" ("+(up?"+":"-")+Math.abs(mv.d/mv.before*100).toFixed(0)+"%)":" is new, "+money(mv.p.t[COST])+" so far")+
          (mv.p.t[CONV]?" and it brought "+fmt("num",mv.p.t[CONV],C)+" conversions.":"."),
        action:{kind:"filter",ids:[mv.p.c.id]}});
    }
  }

  /* 5. Cheapest and dearest day of the week */
  var dayTotals=daily(cur,set), wk=byWeekday(cur.days,dayTotals);
  if(tc[CONV]>=20&&cur.days.length>=14){
    var rows=wk.map(function(t,i){return {i:i,t:t,cpa:mval("cpa",t)};}).filter(function(r){return r.cpa!=null&&r.t[CONV]>=3;});
    if(rows.length>=4){
      rows.sort(function(a,b){return a.cpa-b.cpa;});
      var lo=rows[0], hi=rows[rows.length-1];
      if(hi.cpa>=lo.cpa*1.25) out.push({id:"weekday",tone:"info",title:"Day of week",
        text:DOW[lo.i]+" is your cheapest day to convert ("+money2(lo.cpa)+" each) and "+DOW[hi.i]+" the dearest ("+money2(hi.cpa)+").",
        action:{kind:"explore",metrics:["cpa","conversions"],dim:"dow"}});
    }
  }

  /* 6. A day that stands out */
  if(dayTotals.length>=7){
    var costs=dayTotals.map(function(t){return t[COST];}), mean=costs.reduce(function(a,b){return a+b;},0)/costs.length;
    var sd=Math.sqrt(costs.reduce(function(a,b){return a+(b-mean)*(b-mean);},0)/costs.length), mx=0;
    costs.forEach(function(v,i){if(v>costs[mx])mx=i;});
    if(sd>0&&costs[mx]>mean+2*sd&&costs[mx]>=mean*1.5) out.push({id:"spike",tone:"warn",title:"A day that stands out",
      text:"Spend peaked on "+dayLabel(cur.days[mx])+" at "+money(costs[mx])+", "+(costs[mx]/mean).toFixed(1)+"x the daily average.",
      action:{kind:"explore",metrics:["cost"],dim:"day"}});
  }

  /* 7. Search impression share */
  if(ctx.sis&&ctx.sis.account!=null&&ctx.sis.account<0.6&&tc[COST]>0){
    out.push({id:"sis",tone:"info",title:"Room to grow",
      text:"Your ads show for "+Math.round(ctx.sis.account*100)+"% of the Google searches they could match, so there is demand you are not capturing yet.",
      action:{kind:"explore",metrics:["impressions","clicks"],dim:"day"}});
  }
  return out.slice(0,7);
}


/* ── Charts a reader can build ──
   What each grouping is, which chart kinds can draw it, and what a kind needs
   from the metrics. The page greys out what cannot work and says why, and
   switches to a kind that can when a grouping is changed under it. */
var DIMS = [
  {k:"day",l:"Day",time:1},{k:"week",l:"Week",time:1},{k:"month",l:"Month",time:1},
  {k:"dow",l:"Day of week"},{k:"hour",l:"Hour of day",ex:1},
  {k:"campaign",l:"Campaign",rank:1},{k:"type",l:"Campaign type",rank:1},
  {k:"device",l:"Device",ex:1,rank:1},{k:"network",l:"Network",ex:1,rank:1}
];
var DIM = {}; DIMS.forEach(function(d){DIM[d.k]=d;});
var TYPES = [
  {k:"line",l:"Line",hint:"Trends over time"},
  {k:"area",l:"Area",hint:"Trends, with weight"},
  {k:"bar",l:"Bars",hint:"Compare sizes side by side"},
  {k:"hbar",l:"Horizontal bars",hint:"Rankings, and long names"},
  {k:"combo",l:"Bars + lines",hint:"The first metric as bars, the rest as lines"},
  {k:"stacked",l:"Stacked bars",hint:"Parts that add up (use Break down by)"},
  {k:"stackedarea",l:"Stacked area",hint:"Parts of the whole over time (use Break down by)"},
  {k:"pie",l:"Pie",hint:"Share of a total"},
  {k:"donut",l:"Donut",hint:"Share of a total, with the total inside"},
  {k:"radar",l:"Radar",hint:"Shape across many categories"},
  {k:"scatter",l:"Scatter",hint:"One metric against another"}
];
var TYPE = {}; TYPES.forEach(function(t){TYPE[t.k]=t;});
var ADDITIVE = {cost:1,impressions:1,clicks:1,conversions:1,value:1};     // totals that can be summed, shared out or stacked
var SERIES_TYPES = {line:1,area:1,bar:1,stacked:1,stackedarea:1};         // kinds that can draw one line or bar per campaign / type
var SERIES_KINDS = [{k:"",l:"Nothing"},{k:"type",l:"Campaign type"},{k:"campaign",l:"Campaign (top 8)"}];

/* Why this kind of chart cannot draw this grouping, or "" when it can. */
function typeBlock(dim,type,hasAccounts){
  var d=DIM[dim]||{};
  if(hasAccounts) return type==="line"?"":"Comparing accounts draws lines";
  switch(type){
    case "line": case "area": case "combo": return d.rank?"Needs days, weeks, months, weekdays or hours":"";
    case "stacked": case "stackedarea": return (d.time||dim==="dow")?"":"Stacking needs days, weeks, months or weekdays";
    case "radar": return dim==="day"?"Too many days for a radar. Try weeks or weekdays":"";
  }
  return "";
}
function defaultType(dim){return (DIM[dim]&&DIM[dim].rank)?"hbar":"line";}
/* The kind to use after the grouping changed: the same one if it still works, else the natural one. */
function fixType(dim,type,hasAccounts){return typeBlock(dim,type,hasAccounts)?(hasAccounts?"line":defaultType(dim)):type;}
/* What the chosen metrics must be for this kind of chart, or "" when they are fine. */
function typeNeeds(type,metrics){
  if((type==="pie"||type==="donut")&&!ADDITIVE[metrics[0]]) return "A pie shows a share of a total, so start with spend, impressions, clicks, conversions or value.";
  if((type==="stacked"||type==="stackedarea")&&!ADDITIVE[metrics[0]]) return "Stacking adds parts together, so start with spend, impressions, clicks, conversions or value.";
  if(type==="scatter"&&metrics.length<2) return "Pick at least two metrics. The first goes across, the second up, and an optional third sets the size.";
  return "";
}
/* Choose a kind of chart. A stack needs something to stack, so it starts by splitting on campaign type. */
function withType(spec,type){
  spec.type=type;
  if((type==="stacked"||type==="stackedarea")&&!spec.series) spec.series="type";
  return spec;
}
function seriesAllowed(dim,type,hasAccounts){
  return !hasAccounts&&!!SERIES_TYPES[type]&&!!((DIM[dim]||{}).time||dim==="dow");
}
/* Each value as a percentage of the series' own peak, so metrics of different sizes can share one axis. */
function relative(vals){
  var mx=0; vals.forEach(function(v){if(v!=null&&v>mx)mx=v;});
  return vals.map(function(v){return v==null?null:(mx?v/mx*100:0);});
}
/* The n biggest items by value(), the rest folded into one "Other (k)". Items are {label, t, ids}. */
function foldTop(items,n,value){
  var s=items.slice().sort(function(a,b){return (value(b)||0)-(value(a)||0);});
  if(s.length<=n+1) return s;
  var rest=zero(), ids=[];
  s.slice(n).forEach(function(i){addTo(rest,i.t); if(i.ids) ids=ids.concat(i.ids);});
  return s.slice(0,n).concat([{label:"Other ("+(s.length-n)+")",t:rest,ids:ids,rest:true}]);
}
/* The groups of campaigns a "break down by" draws: one per campaign type, or the top campaigns by spend and the rest as "Other". */
function seriesGroups(data,set,kind,top){
  var per=perCampaign(data), idx=[];
  data.campaigns.forEach(function(c,i){if(sel(set,i)&&(per[i][COST]||per[i][IMPR]))idx.push(i);});
  var out=[];
  if(kind==="type"){
    var by={}, order=[];
    idx.forEach(function(i){var k=data.campaigns[i].type; if(!by[k]){by[k]=[];order.push(k);} by[k].push(i);});
    order.sort(function(a,b){return sumCost(per,by[b])-sumCost(per,by[a]);});
    order.forEach(function(k){out.push({name:label(TYPE_LABEL,k),idx:new Set(by[k])});});
  }else if(kind==="campaign"){
    idx.sort(function(a,b){return per[b][COST]-per[a][COST];});
    idx.slice(0,top||8).forEach(function(i){out.push({name:data.campaigns[i].name,idx:new Set([i])});});
    if(idx.length>(top||8)) out.push({name:"Other ("+(idx.length-(top||8))+")",idx:new Set(idx.slice(top||8))});
  }
  return out;
}
function sumCost(per,idx){return idx.reduce(function(a,i){return a+per[i][COST];},0);}

/* ── Misc ── */
function csvCell(v){
  if(v==null) return "";
  v=String(v);
  if(/^[=+\-@\t\r]/.test(v)) v="'"+v;                  // a cell must never run as a formula in Excel
  return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;
}
function toCsv(rows){return rows.map(function(r){return r.map(csvCell).join(",");}).join("\r\n");}
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function initials(n){
  return n.replace(/\(.*?\)/g,"").split(/[\s&.,\-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w);}).slice(0,2).map(function(w){return w[0];}).join("").toUpperCase()||"·";
}
function fmtId(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
/* Index a series to 100 at its first non-zero value (to compare accounts of different size). */
function indexTo100(vals){
  var base=null;for(var i=0;i<vals.length;i++){if(vals[i]){base=vals[i];break;}}
  return vals.map(function(v){return base&&v!=null?v/base*100:null;});
}

var api = {
  IMPR:IMPR,CLK:CLK,COST:COST,CONV:CONV,VAL:VAL,ALLC:ALLC,ALLV:ALLV,VTC:VTC,INT:INT,ENG:ENG,VID:VID,XDV:XDV,INV:INV,PCALL:PCALL,PIMPR:PIMPR,AVV:AVV,AVM:AVM,NB:NB,
  parseD:parseD,fmtD:fmtD,addDays:addDays,diffDays:diffDays,localToday:localToday,dowOf:dowOf,mondayOf:mondayOf,
  monthStart:monthStart,addMonths:addMonths,shiftYear:shiftYear,dayLabel:dayLabel,monthLabel:monthLabel,
  PRESETS:PRESETS,presetRange:presetRange,compareRange:compareRange,
  METRICS:METRICS,METRIC_ORDER:METRIC_ORDER,METRIC_ALL:METRIC_ALL,GROUPS:GROUPS,KPI_DEFAULT:KPI_DEFAULT,kpiCols:kpiCols,METRIC_COLOR:METRIC_COLOR,SERIES:SERIES,mval:mval,noData:noData,
  fmt:fmt,fmtMetric:fmtMetric,sym:sym,compact:compact,change:change,
  zero:zero,addTo:addTo,filterSet:filterSet,total:total,daily:daily,perCampaign:perCampaign,campaignDaily:campaignDaily,byType:byType,
  bucket:bucket,byWeekday:byWeekday,hourGrid:hourGrid,byHour:byHour,DOW:DOW,
  TYPE_LABEL:TYPE_LABEL,DEVICE_LABEL:DEVICE_LABEL,NETWORK_LABEL:NETWORK_LABEL,label:label,
  DIMS:DIMS,DIM:DIM,TYPES:TYPES,TYPE:TYPE,ADDITIVE:ADDITIVE,SERIES_TYPES:SERIES_TYPES,SERIES_KINDS:SERIES_KINDS,
  typeBlock:typeBlock,withType:withType,defaultType:defaultType,fixType:fixType,typeNeeds:typeNeeds,seriesAllowed:seriesAllowed,relative:relative,foldTop:foldTop,seriesGroups:seriesGroups,
  insights:insights,toCsv:toCsv,csvCell:csvCell,hueOf:hueOf,initials:initials,fmtId:fmtId,indexTo100:indexTo100
};
root.PSData = api;
if(typeof module!=="undefined"&&module.exports) module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
