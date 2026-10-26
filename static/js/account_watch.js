/* Account Watch (Paid Marketing Agents).
 *
 * Pick a Google Ads account, an MCC or a Meta ad account. The server reads each account and judges what changed (tracker/paid_watch*.py). This page draws it and never
 * decides what is broken for itself. The wording and the shapes are account_watch_data.js.
 */
(function(){
"use strict";

var D = window.AWData, API = "/p2/paid-marketing/api/watch";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}
function store(k,v){try{if(v===undefined)return localStorage.getItem(k)||"";localStorage.setItem(k,v);}catch(e){return "";}}

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}};

var scope = null;
function groupOf(id){return scope?scope.groupOf(id):null;}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}

function run(refresh,keep){
  if(!S.account){show("empty");setMsg("");return;}
  var seq=++S.seq, parts=window.PMScope.partsOf(S.account), g=groupOf(parts[0]), both=parts.length>1;
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.account));}catch(e){}
  setMsg(both?"Reading Google Ads and Meta Ads together. This takes a little longer.":g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 30 seconds.":"",(both||g)?"pm-note":"");
  if(!keep) show("skel");
  window.PMScope.fetchReport(API+"/report",S.account,refresh===true).then(function(r){
    if(seq!==S.seq) return;
    if(g||both) setMsg("");
    if(!r.ok){
      show(S.report?"out":"empty");
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j;
    if(!keep){ S.open={}; S.view=D.firstView(r.j); if(r.j.accounts.length===1) S.open[r.j.accounts[0].id]=true; }
    show("out");
    try{render();}catch(e){setMsg("The report was read but could not be drawn: "+esc(String(e&&e.message||e)));}
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">'+(rep.account.platform==="both"?"Google + Meta":"MCC")+' · '+rep.account.accountCount+' accounts checked together</span>':"")+
    ' · read '+esc(ago(rep.readAt||rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">The platform is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintInsights(); paintViews(); paintList(); paintBlind();
  var t=rep.thresholds||{};
  $("#foot").innerHTML="Source: Google Ads (the manager account's list of every client with its status, the account budget orders, the change history of the last "+(t.changeDays||3)+" days, the conversion actions and who can open each account) and Meta (the ad account's status and spend cap, the Pages and the activity history), saved so the same report opens instantly. "+
    "Who can open an account, the conversion goals and the Pages are held against the readings this monitor keeps each day, so they are judged from the second day. A budget is flagged when it moves by "+(t.budgetMovePercent||30)+" percent or more, an account budget order or a spend cap from "+(t.orderPercent||85)+" and "+(t.capPercent||90)+" percent used. A person whose address is not on "+((t.teamDomains||["position2.com"]).join(", "))+" is named with what they changed. "+
    "A card that was declined is not shown by either platform; a failed payment appears as a status that is not active. Nothing is changed in an ad account.";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.report).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintInsights(){
  var list=D.insights(S.report), host=$("#insights");
  host.innerHTML=list.length?'<section class="pm-panel da-ins"><div class="pm-panel-h"><span class="pm-panel-t">Across the accounts</span></div><ul>'+list.map(function(i){return '<li class="'+i.kind+'">'+esc(i.text)+'</li>';}).join("")+'</ul></section>':"";
}
function paintBlind(){
  var list=S.report.notMeasured||[];
  $("#blind").innerHTML=list.length?'<div class="pm-panel-h"><span class="pm-panel-t">What this page cannot see</span></div><ul class="th-blind-l">'+list.map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ul>':"";
}
function paintViews(){
  $("#views").innerHTML=D.VIEWS.map(function(v){
    var n=D.viewCount(S.report,v[0]);
    return '<button type="button" role="radio" class="pm-chip'+(v[0]===S.view?" on":"")+'" data-v="'+v[0]+'" aria-checked="'+(v[0]===S.view)+'">'+esc(v[1])+' <span class="n">'+n+'</span></button>';
  }).join("");
}
$("#views").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.view=b.dataset.v; paintViews(); paintList();
});

function pill(a){var s=D.statusInfo(a.status); return '<span class="bi-pill '+s.tone+'">'+esc(s.label)+'</span>';}
function paintList(){
  var rows=D.shown(S.report,S.view);
  $("#listNote").textContent=S.report.accounts.length+(S.report.accounts.length===1?" account":" accounts")+", the ones to fix first";
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Nothing needs a look: nothing unusual was found.")+'</p>';
}
function figHtml(a){
  var c=a.counts;
  if(a.status==="clean") return '<div class="bs-fig ok"><span class="bi-n">0</span><span class="bs-sub">nothing unusual</span></div>';
  if(a.status==="fix") return '<div class="bs-fig stopped"><span class="bi-n">'+D.int(c.high+c.medium)+'</span><span class="bs-sub">to fix'+(c.high?' · '+c.high+' high':"")+'</span></div>';
  return '<div class="bs-fig under"><span class="bi-n">'+D.int(c.low)+'</span><span class="bs-sub">small finding'+(c.low===1?"":"s")+'</span></div>';
}
function row(a){
  var open=!!S.open[a.id], top=a.byRule[0];
  return '<article class="bi-row '+(a.status==="fix"?"stopped":a.status==="check"?"limited":"clear")+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+(a.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+'</b><span class="bi-sub">'+esc(D.accountSub(a))+'</span></span>'+
      figHtml(a)+
      '<span class="bi-delta bad">'+(top?esc(top.label)+(top.findings>1?' · '+D.plural(top.findings,"finding"):""):"")+'</span>'+
      '<span class="bi-why"></span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function findingHtml(f){
  var l=D.levelInfo(f.level);
  return '<li><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span> <b>'+esc(f.title)+'</b> <span class="bi-sub">'+esc(f.area)+'</span><p class="gc-detail">'+esc(f.detail)+'</p>'+
    (f.evidence&&f.evidence.length?'<ul class="kp-evid">'+f.evidence.map(function(e){return '<li>'+esc(e)+'</li>';}).join("")+'</ul>':"")+
    '<p class="th-fix"><b>What to do.</b> '+esc(f.fix)+'</p></li>';
}
function checksHtml(a){
  return '<h4>What was checked</h4><div class="bi-tbl"><table><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>'+(a.checks||[]).map(function(c){
    var s=D.checkInfo(c.state);
    return '<tr><td><b>'+esc(c.label)+'</b></td><td><span class="bi-lv '+s.tone+'">'+esc(s.label)+'</span></td><td class="bi-sub">'+esc(c.note)+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function body(a){
  var finds=(a.findings||[]).length?'<h4>What was found <span class="dim">('+D.plural(a.findings.length,"finding")+', worst first)</span></h4><ul class="gc-finds th-finds">'+a.findings.map(findingHtml).join("")+'</ul>':
    '<p class="pm-empty">Nothing to flag: nothing unusual was found.</p>';
  return '<div class="bi-body">'+(a.inactive?'<p class="th-head">Only this account\'s status is known here: the platform does not let its other details be read.</p>':'')+finds+checksHtml(a)+'</div>';
}
$("#list").addEventListener("click",function(e){
  var head=e.target.closest(".bi-head"); if(head){
    var art=head.closest(".bi-row"), id=art.dataset.id; S.open[id]=!S.open[id]; paintList();
  }
});

/* ── Refresh and CSV ──────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var blob=new Blob(["﻿"+D.csvRows(rep).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="account-watch-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+String(rep.generatedAt||"").slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("aw_account")||"";
  if(window.MonitorPanel&&$("#monPanel")) window.MonitorPanel.mount($("#monPanel"),{agent:"watch",label:"Account Watch"});
  S.account="";
  scope=window.PMScope.mount($("#acctPick"),{account:want,onChange:function(a){ S.account=a; store("aw_account",a); run(); },
    onError:function(e){setMsg(esc((e&&e.message)||"The account list could not be read."));}});
})();
})();
