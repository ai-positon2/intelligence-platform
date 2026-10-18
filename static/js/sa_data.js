/* Seasonality Analysis: the numbers the page shows, with no page code.
 *
 * The server (tracker/paid_seasonality.py) does the analysis: the seasonal index of each month, the days of the week, the
 * holidays, this year against last, the next three months. What is left for the browser is to choose a metric and draw it:
 * a month's or week's figure from its totals, the rows of the year-over-year chart, the headline totals, the wording that
 * says how far to trust an index, and the CSV. That is here so it can be tested on its own (tests/test_seasonality_js.py
 * runs this file in Node), and so a rate on this page can never disagree with the same rate on the other agents.
 *
 * A month or week row is what the server sends: {y, m|w, days, impr, clicks, cost, conv, value}.
 */
(function(root){
"use strict";

var P = root.PSData || (typeof require!=="undefined"?require("./ps_data.js"):null);

var MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
var MON = MONTHS.map(function(m){return m.slice(0,3);});
var DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];

/* Every metric the page can draw. kind is the PSData formatter; "sum" ones are shown per day (a month of 31 days and one of
   28 are then comparable), "ratio" ones are one total over another. good says which way is better ("up", "down", or "" when
   neither is: more spend is not good or bad in itself). */
var METRICS = [
  {key:"cost",  label:"Spend",               kind:"money",  type:"sum",   good:"",     unit:"per day"},
  {key:"impr",  label:"Impressions",         kind:"int",    type:"sum",   good:"",     unit:"per day"},
  {key:"clicks",label:"Clicks",              kind:"int",    type:"sum",   good:"",     unit:"per day"},
  {key:"conv",  label:"Conversions",         kind:"num",    type:"sum",   good:"up",   unit:"per day"},
  {key:"value", label:"Conversion value",    kind:"money",  type:"sum",   good:"up",   unit:"per day"},
  {key:"ctr",   label:"Click-through rate",  kind:"pct",    type:"ratio", good:"up",   num:"clicks", den:"impr"},
  {key:"cvr",   label:"Conversion rate",     kind:"pct",    type:"ratio", good:"up",   num:"conv",   den:"clicks"},
  {key:"cpc",   label:"Cost per click",      kind:"money2", type:"ratio", good:"down", num:"cost",   den:"clicks"},
  {key:"cpa",   label:"Cost per conversion", kind:"money2", type:"ratio", good:"down", num:"cost",   den:"conv"},
  {key:"roas",  label:"Return on ad spend",  kind:"x",      type:"ratio", good:"up",   num:"value",  den:"cost"}
];
var BY = {}; METRICS.forEach(function(m){BY[m.key]=m;});

/* The metrics to offer for an account: conversion value and the return on it only when the account reports any. */
function available(rows){
  var hasValue=(rows||[]).some(function(r){return r.value>0;});
  return METRICS.filter(function(m){return hasValue||(m.key!=="value"&&m.key!=="roas");});
}

/* One metric of one row (a month, a week, or any {days, impr, clicks, cost, conv, value}); null when it cannot be told. */
function valueOf(key,row,days){
  var m=BY[key]; if(!m||!row) return null;
  if(m.type==="ratio"){var d=row[m.den]; return d>0?row[m.num]/d:null;}
  var n=days||row.days||1; return n>0?row[key]/n:null;
}
function fmt(key,v,cur,small){
  var m=BY[key]; if(v==null||!isFinite(v)) return "–";
  return P?P.fmt(m.kind,v,cur,small):String(v);
}

/* The rows of the year-over-year chart: one line per year, a point per calendar month (or ISO week). A month that is not whole in the data
   is left out (the first and the current one are partial: 17 days of October laid against all of last October would look like a collapse), and so is
   a week the server did not send. */
function overlay(monthly,weekly,key,grain){
  var rows=grain==="week"?weekly:monthly, n=grain==="week"?53:12, byYear={};
  (rows||[]).forEach(function(r){
    if(grain!=="week"&&!r.full) return;
    var y=r.y, at=(grain==="week"?r.w:r.m)-1;
    if(at<0||at>=n) return;
    (byYear[y]=byYear[y]||new Array(n).fill(null))[at]=valueOf(key,r,grain==="week"?7:r.days);
  });
  var years=Object.keys(byYear).map(Number).sort(function(a,b){return a-b;});
  var labels=[]; for(var i=0;i<n;i++) labels.push(grain==="week"?String(i+1):MON[i]);
  return {labels:labels,years:years.map(function(y){return {y:y,data:byYear[y]};})};
}

/* Headline totals: the last twelve whole months, and the twelve before them when they are in the data. */
function trailing(monthly){
  var full=(monthly||[]).filter(function(r){return r.full;});
  if(full.length<3) return null;
  function sum(rs){var t={days:0,impr:0,clicks:0,cost:0,conv:0,value:0}; rs.forEach(function(r){Object.keys(t).forEach(function(k){t[k]+=r[k];});}); return t;}
  var cur=full.slice(-12), prev=full.length>=24?full.slice(-24,-12):null;
  return {cur:sum(cur),prev:prev?sum(prev):null,months:cur.length,
    from:cur[0].y+"-"+String(cur[0].m).padStart(2,"0"),to:cur[cur.length-1].y+"-"+String(cur[cur.length-1].m).padStart(2,"0")};
}
function change(cur,prev){return cur==null||prev==null||!prev?null:cur/prev-1;}
function pct(x,d){
  if(x==null||!isFinite(x)) return "–";
  var v=x*100, s=Math.abs(v)>=10?Math.round(v):Math.round(v*10)/10;
  return (Math.abs(s)>0?(v>0?"+":"-"):"")+Math.abs(s)+"%";
}

/* How far to trust one month's index, in words, from the years behind it. */
function confidence(e){
  if(!e||e.index==null) return "";
  if(e.n<=1) return "One year of history behind this, so take it as a lead, not a rule.";
  if(e.agree===e.n) return "The same direction in all "+e.n+" years.";
  return e.agree+" of "+e.n+" years agree, so the pattern is mixed.";
}
/* peak, trough or ordinary for an index (thresholds come from the server so the two never disagree). */
function tone(index,th){
  th=th||{peak:110,trough:90};
  if(index==null) return "none";
  return index>=th.peak?"peak":index<=th.trough?"trough":"ordinary";
}
/* For a metric where lower is better (a cost) a peak is bad news: swap the colours. */
function toneColor(t,key){
  var m=BY[key], low=m&&m.good==="down";
  var good="#34d399", bad="#fb7185", mid="#8b94be";
  if(m&&m.good==="") return t==="none"?mid:"#fbbf24";          // more spend or more impressions is neither good nor bad
  if(t==="peak") return low?bad:good;
  if(t==="trough") return low?good:bad;
  return mid;
}
var VERDICT = {
  peak:        {label:"Raise budgets",    color:"#34d399", hint:"Demand is up and conversions are not dearer"},
  peak_costly: {label:"Raise with care",  color:"#fbbf24", hint:"Demand is up, and so is the price of a conversion"},
  typical:     {label:"Keep as is",       color:"#8b94be", hint:"An ordinary month"},
  quiet:       {label:"Hold the line",    color:"#38bdf8", hint:"Quieter, at a normal price"},
  quiet_costly:{label:"Trim budgets",     color:"#fb7185", hint:"Quieter and dearer"},
  unknown:     {label:"Not enough history", color:"#8b94be", hint:"Needs a year of history"}
};
function budgetText(step){
  if(!step) return "No change";
  return (step>0?"+":"")+step+"% on an average month's daily budget";
}
function windowText(a,b){
  var x=a.split("-"), y=b.split("-"), mm=function(s){return MON[+s[1]-1]+" "+(+s[2]);};
  return a===b?mm(x):mm(x)+" to "+mm(y);
}

/* "Oct 2023 to Oct 2026" for two ISO dates. */
function monthsText(a,b){
  var x=a.split("-"), y=b.split("-");
  return MON[+x[1]-1]+" "+x[0]+(a.slice(0,7)===b.slice(0,7)?"":" to "+MON[+y[1]-1]+" "+y[0]);
}

/* Month by month, as CSV: the months in the data, then the seasonal index of each calendar month. */
function csvCell(v){if(v==null)v="";var num=typeof v==="number";var s=String(v);if(!num&&/^[=+\-@\t\r]/.test(s))s="'"+s;return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}
function csv(rep){
  var out=[["Month","Days","Spend","Impressions","Clicks","Conversions","Conversion value","Cost per conversion","Return on ad spend"]];
  (rep.monthly||[]).forEach(function(r){
    out.push([r.y+"-"+String(r.m).padStart(2,"0"),r.days,r.cost,r.impr,r.clicks,r.conv,r.value,
      r.conv>0?Math.round(r.cost/r.conv*100)/100:"",r.cost>0&&r.value>0?Math.round(r.value/r.cost*100)/100:""]);
  });
  if(rep.index){
    out.push([]);
    out.push(["Calendar month","Conversions index","Spend index","Cost per conversion index","Conversion rate index","Years behind it"]);
    for(var i=0;i<12;i++){
      var g=function(k){var e=rep.index[k]&&rep.index[k][i]; return e&&e.index!=null?e.index:"";};
      out.push([MONTHS[i],g("conv"),g("cost"),g("cpa"),g("cvr"),rep.index.conv[i].n]);
    }
  }
  return out.map(function(r){return r.map(csvCell).join(",");}).join("\r\n");
}

var api = {MONTHS:MONTHS,MON:MON,DAYS:DAYS,METRICS:METRICS,BY:BY,available:available,valueOf:valueOf,fmt:fmt,overlay:overlay,
  trailing:trailing,change:change,pct:pct,confidence:confidence,tone:tone,toneColor:toneColor,VERDICT:VERDICT,budgetText:budgetText,
  windowText:windowText,monthsText:monthsText,csv:csv};
root.SAData = api;
if(typeof module!=="undefined"&&module.exports) module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
