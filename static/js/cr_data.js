/* Call Transcript Analyzer: the numbers the page shows, with no page code.
 *
 * The server (tracker/paid_calls.py and paid_calls_topics.py) counts the calls and reads the sample; what is left for the browser is
 * to say how a rate is written, which location's misses stand out, how this range compares with the last, what the sample covered
 * and the CSV. That is here so tests/test_call_analyzer_js.py can run it in Node. No call text ever reaches the browser, so none is
 * handled here.
 */
(function(root){
"use strict";

function num(n){return n==null||!isFinite(n)?"–":Math.round(n).toLocaleString("en-US");}
function pct(x,d){
  if(x==null||!isFinite(x)) return "–";
  var v=x*100, dd=d==null?(Math.abs(v)>=10?0:1):d;
  return v.toFixed(dd)+"%";
}
/* A length in seconds as m:ss. */
function dur(sec){
  if(sec==null||!isFinite(sec)) return "–";
  var s=Math.round(sec), m=Math.floor(s/60);
  return m+":"+String(s%60).padStart(2,"0");
}
function change(cur,prev){return prev==null||!prev||cur==null?null:cur/prev-1;}
function signed(x){
  if(x==null||!isFinite(x)) return "–";
  var v=x*100, s=Math.abs(v)>=10?Math.round(v):Math.round(v*10)/10;
  return (Math.abs(s)>0?(v>0?"+":"-"):"")+Math.abs(s)+"%";
}

/* Locations whose missed-call rate stands out: at least 1.5 times the whole account's, on at least 30 calls and 5 misses, so a small
   office with two missed calls is not shouted about. Returns {key: "high"}; the same for leads below half the account's rate on at
   least 50 calls ("low"). */
var MIN_CALLS=30, MIN_MISSES=5, LEAD_MIN_CALLS=50;
function flags(rows,totals){
  var out={}; if(!rows||!totals) return out;
  rows.forEach(function(r){
    if(totals.missRate!=null&&r.missRate!=null&&r.calls>=MIN_CALLS&&r.missed>=MIN_MISSES&&r.missRate>=1.5*totals.missRate) out[r.key]="miss";
    else if(totals.leadRate&&r.calls>=LEAD_MIN_CALLS&&r.leadRate<0.5*totals.leadRate) out[r.key]="lowLeads";
  });
  return out;
}

/* How much of the sample could be read, in words. */
function coverage(an){
  if(!an) return "";
  if(!an.read) return "None of the "+num(an.sampled)+" calls sampled had a transcript.";
  return "Read "+num(an.read)+" calls that had a transcript, from a sample of "+num(an.sampled)+" spread across the range ("+num(an.matching)+" calls match).";
}
/* Half of a share of calls is a guess when the sample is this small: say so. */
function thin(an){return !!an&&an.read>0&&an.read<30;}

var INTENT_COLOR={new_patient:"#34d399",emergency:"#fb7185",service:"#38bdf8",appointment:"#a78bfa",insurance:"#fbbf24",records:"#8b94be",
  not_a_patient:"#f97316",no_conversation:"#5d6688",other:"#475069"};
var REASON_COLOR={no_availability:"#fbbf24",insurance:"#fb7185",transfer:"#f97316",callback:"#38bdf8",price:"#a78bfa",other:"#8b94be"};

function csvCell(v){var s=String(v==null?"":v); return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}
function csv(ov,an){
  var out=[], head=["Name","Calls","Answered","Missed","Missed rate","First-time callers","CallRail leads","Lead rate","Average length (s)"];
  function table(title,rows){
    if(!rows||!rows.length) return;
    out.push([title]); out.push(head);
    rows.forEach(function(r){out.push([r.key,r.calls,r.answered,r.missed,r.missRate==null?"":Math.round(r.missRate*10000)/100+"%",r.firstTime,r.leads,
      r.leadRate==null?"":Math.round(r.leadRate*10000)/100+"%",r.avgDuration]);});
    out.push([]);
  }
  if(ov){table("Sources",ov.sources);table("Locations",ov.locations);table("Campaigns",ov.campaigns);table("Keywords",ov.keywords);}
  if(an&&an.intents){
    out.push(["What callers wanted (sampled calls with a transcript)"]); out.push(["Intent","Calls","Booked","Not booked","Lost"]);
    an.intents.forEach(function(i){out.push([i.label,i.n,i.booked,i.notBooked,i.lost]);}); out.push([]);
    out.push(["Topics"]); out.push(["Topic","Calls","Share of calls read"]);
    an.topics.forEach(function(t){out.push([t.label,t.n,Math.round(t.share*10000)/100+"%"]);});
  }
  return out.map(function(r){return r.map(csvCell).join(",");}).join("\r\n");
}

var api={num:num,pct:pct,dur:dur,change:change,signed:signed,flags:flags,coverage:coverage,thin:thin,INTENT_COLOR:INTENT_COLOR,REASON_COLOR:REASON_COLOR,
  MIN_CALLS:MIN_CALLS,MIN_MISSES:MIN_MISSES,LEAD_MIN_CALLS:LEAD_MIN_CALLS,csv:csv};
root.CRData=api;
if(typeof module!=="undefined"&&module.exports) module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
