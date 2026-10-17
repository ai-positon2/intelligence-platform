/* Account Audit: the figures and wording the page draws, as plain functions (so tests/test_account_audit_js.py runs them in Node).
 *
 * The server reads the account, runs every check and scores it (tracker/paid_audit*.py). This file only decides how a finding is
 * worded, which findings a filter shows, how many of each result there are and how a ring is drawn. It never judges an account.
 */
(function(root){
"use strict";

var RESULT = {
  fail:   {label:"Fail",            rank:0, tone:"bad"},
  warn:   {label:"Warning",         rank:1, tone:"warn"},
  unknown:{label:"Could not check", rank:2, tone:"unk"},
  pass:   {label:"Pass",            rank:3, tone:"good"},
  na:     {label:"Not applicable",  rank:4, tone:"na"}
};
var SEVERITY = {
  critical:{label:"Critical", rank:0},
  high:    {label:"High",     rank:1},
  medium:  {label:"Medium",   rank:2},
  low:     {label:"Low",      rank:3}
};
var PLATFORM = {google:"Google Ads", meta:"Meta Ads"};
var VIEWS = [
  {key:"attention", label:"Needs attention", results:["fail","warn"]},
  {key:"unknown",   label:"Could not check", results:["unknown"]},
  {key:"pass",      label:"Passed",          results:["pass"]},
  {key:"all",       label:"Everything",      results:["fail","warn","unknown","pass","na"]}
];
var VIEW_BY = {}; VIEWS.forEach(function(v){VIEW_BY[v.key]=v;});

function tally(findings){
  var t={fail:0,warn:0,pass:0,na:0,unknown:0,total:0};
  (findings||[]).forEach(function(f){if(t.hasOwnProperty(f.result)) t[f.result]++; t.total++;});
  return t;
}
function viewCount(findings,view){
  var v=VIEW_BY[view]; if(!v) return 0;
  return (findings||[]).filter(function(f){return v.results.indexOf(f.result)>=0;}).length;
}
/* The view that opens first for a section: what needs attention, else what could not be checked, else everything. */
function defaultView(findings){
  return viewCount(findings,"attention")?"attention":viewCount(findings,"unknown")?"unknown":"all";
}
function haystack(f){
  return [f.id,f.title,f.found,f.why,f.fix].concat(f.items||[]).join(" ").toLowerCase();
}
/* The findings of one section under a view and a text filter, in the server's order (failures first, the most severe first). */
function filterFindings(findings,view,text){
  var v=VIEW_BY[view]||VIEW_BY.all, t=String(text||"").trim().toLowerCase();
  return (findings||[]).filter(function(f){
    return v.results.indexOf(f.result)>=0&&(!t||haystack(f).indexOf(t)>=0);
  });
}
function affectedText(f){
  var shown=(f.items||[]).slice(); if(!shown.length) return "";
  var more=(f.count||shown.length)-shown.length;
  return shown.join("; ")+(more>0?" (and "+more+" more)":"");
}
function gradeTone(grade){
  return grade==="A"?"good":grade==="B"?"ok":grade==="C"?"warn":grade==="D"||grade==="F"?"bad":"none";
}
function scoreText(score){return score==null?"n/a":String(Math.round(score));}
function percentText(n){return (Math.round(n)||0)+"%";}
/* A ring: the stroke length for a score out of 100 on a circle of radius r. */
function ring(score,r){
  var c=2*Math.PI*r, s=score==null?0:Math.max(0,Math.min(100,score));
  return {circumference:c, dash:c*s/100, gap:c-c*s/100};
}
function plural(n,word){return n+" "+(n===1?word:word+"s");}
function ago(iso,now){
  var t=new Date(iso).getTime(), n=(now==null?Date.now():now);
  if(!iso||isNaN(t)) return "";
  var m=Math.max(0,Math.round((n-t)/60000));
  return m<1?"just now":m<60?m+" minutes ago":m<1440?plural(Math.round(m/60),"hour")+" ago":plural(Math.round(m/1440),"day")+" ago";
}
/* The headline tiles of a report. `tone` says how the tile is drawn; a tile for what needs fixing is only warm when there is something. */
function tiles(summary){
  var t=(summary&&summary.tally)||{fail:0,warn:0,pass:0,na:0,unknown:0};
  return [
    {key:"critical", label:"Failed critical checks", value:summary.critical||0, tone:(summary.critical?"bad":"good"), hint:summary.critical?"These can make every other number untrustworthy":"None"},
    {key:"high",     label:"Failed high checks",     value:summary.high||0,     tone:(summary.high?"bad":"good"),     hint:summary.high?"Fix these next":"None"},
    {key:"warn",     label:"Warnings",               value:t.warn,              tone:(t.warn?"warn":"good"),           hint:t.warn?"Worth a look":"None"},
    {key:"pass",     label:"Passed",                 value:t.pass,              tone:"good",                          hint:"of "+((summary.judged)||0)+" judged"},
    {key:"unknown",  label:"Could not check",        value:t.unknown,           tone:(t.unknown?"unk":"good"),          hint:t.unknown?percentText(summary.unknownShare||0)+" of the checks":"Everything was read"}
  ];
}
function readNote(report){
  var parts=[];
  (report.accounts||[]).forEach(function(a){parts.push(a.name+" ("+(PLATFORM[a.platform]||a.platform)+")");});
  return parts.join(" and ");
}
function signed(n){return n==null?"":n>0?"+"+n:n<0?"-"+Math.abs(n):"0";}
/* What changed since a saved audit, as tiles. `c` is the server's comparison (tracker/paid_audit_history.py compare). */
function changeTiles(c){
  var k=c.counts||{fixed:0,improved:0,worse:0,newProblems:0};
  return [
    {key:"score",    label:"Score",          value:c.scoreChange==null?"n/a":signed(c.scoreChange), tone:(c.scoreChange>0?"good":c.scoreChange<0?"bad":"unk"), hint:(c.scoreWas==null?"no score then":"was "+c.scoreWas+(c.gradeWas?" ("+c.gradeWas+")":""))+", now "+(c.scoreNow==null?"n/a":c.scoreNow)+(c.gradeNow?" ("+c.gradeNow+")":"")},
    {key:"fixed",    label:"Put right",      value:k.fixed,       tone:(k.fixed?"good":"unk"),        hint:k.improved?k.improved+" more got better":"checks that now pass"},
    {key:"newProblems",label:"New problems", value:k.newProblems, tone:(k.newProblems?"bad":"good"),  hint:k.newProblems?"passed then, not now":"none"},
    {key:"worse",    label:"Got worse",      value:k.worse,       tone:(k.worse?"bad":"good"),        hint:k.worse?"a warning that became a failure":"none"},
    {key:"still",    label:"Still failing",  value:c.stillFailing||0, tone:((c.stillFailing||0)?"warn":"good"), hint:(c.stillWarning||0)+" still warning"}
  ];
}
/* The changed checks in the order they are listed: new problems and worse first, then what was put right. */
function changeGroups(c){
  return [["New problems",c.newProblems,"bad"],["Got worse",c.worse,"bad"],["Put right",c.fixed,"good"],["Got better",c.improved,"good"]].filter(function(g){return g[1]&&g[1].length;});
}
function dateText(iso){
  var d=new Date(iso); if(!iso||isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-US",{year:"numeric",month:"short",day:"numeric"});
}
var api={signed:signed,changeTiles:changeTiles,changeGroups:changeGroups,dateText:dateText,RESULT:RESULT,SEVERITY:SEVERITY,PLATFORM:PLATFORM,VIEWS:VIEWS,tally:tally,viewCount:viewCount,defaultView:defaultView,filterFindings:filterFindings,
  affectedText:affectedText,gradeTone:gradeTone,scoreText:scoreText,percentText:percentText,ring:ring,plural:plural,ago:ago,tiles:tiles,readNote:readNote};
if(typeof module!=="undefined"&&module.exports) module.exports=api; else root.AuditData=api;
})(typeof window!=="undefined"?window:globalThis);
