/* Spend Anomaly Alert (Paid Marketing Agents).
 *
 * Pick a Google Ads account, an MCC or a Meta ad account. The server reads the account's spend by day and by hour, works out what is usual and holds today and yesterday against it
 * (tracker/paid_anomaly*.py). This page draws the answer and never decides what is unusual for itself: the status, the findings, their causes and the plan all come from the server.
 * The wording and the shapes of the charts are anomaly_data.js.
 */
(function(){
"use strict";

var D = window.AnData, API = "/p2/paid-marketing/api/anomaly";
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

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}, meta:[], settings:{accounts:{},sensitivity:null,ratios:{}}};

var scope = null;
function groupOf(id){return scope?scope.groupOf(id):null;}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function postJson(url,body){
  return getJson(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
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
    (rep.account.isGroup?' <span class="sg-mcc">'+(rep.account.platform==="both"?"Google + Meta":"MCC")+' · '+rep.account.accountCount+' accounts watched together</span>':"")+
    ' · read '+esc(ago(oldestRead(rep)||rep.generatedAt));
  paintKpis(); paintInsights(); paintViews(); paintList(); paintSens();
  $("#foot").innerHTML="Source: each account's spend by day and by hour (Google Ads: the customer's cost by date and hour; Meta: insights by day, and by hour when Meta gives it), in the account's own time zone, kept for ten minutes so the page opens at once; Refresh reads again. "+
    "<b>Usual</b> is the median of the account's last 14 days (weekdays with weekdays, weekend days with weekend days when there are four of that kind) and the median of what those days had spent by this hour. A swing under 25 units of the account's currency, or a tenth of a usual day, is never flagged. "+
    "The latest hours are left out of a drop because the platform reports them late. The campaign that spent the excess, the budgets and the change history are read only for an account that looks wrong. "+
    "The monitor reads every account every two hours and alerts on what is new or worse; its first run sets a baseline and sends nothing. Nothing is changed in an ad account.";
}
function oldestRead(rep){
  var ts=(rep.accounts||[]).map(function(a){return a.readAt;}).filter(Boolean).sort();
  return ts[0]||"";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.report).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintInsights(){
  var list=D.insights(S.report), host=$("#insights");
  host.innerHTML=list.length?'<section class="pm-panel da-ins"><div class="pm-panel-h"><span class="pm-panel-t">Worth knowing</span></div><ul>'+list.map(function(i){return '<li class="'+i.kind+'">'+esc(i.text)+'</li>';}).join("")+'</ul></section>':"";
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
  $("#listNote").textContent=S.report.accounts.length+(S.report.accounts.length===1?" account":" accounts")+", the ones to look at first";
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Nothing needs a look: every account is spending as it usually does.")+'</p>';
}
function row(a){
  var open=!!S.open[a.id], pace=D.paceText(a), cls=a.status==="over"?"stopped":a.status==="under"?"limited":"clear";
  return '<article class="bi-row '+cls+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+(a.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+'</b><span class="bi-sub">'+esc(a.local||"")+(a.tz?" · "+esc(a.tz):"")+'</span></span>'+
      '<div class="bs-fig '+esc(pace.tone||"ok")+'"><span class="bi-n">'+(a.today.ratio==null?esc(D.money(a.today.cost,a.currency)):esc(D.ratioText(a.today.ratio)))+'</span><span class="bs-sub">'+esc(pace.text)+'</span></div>'+
      '<span class="bi-delta bad">'+esc(D.headline(a))+'</span>'+
      '<span class="bi-why">'+(a.hourly?"":"whole days only")+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}

/* ── What is wrong, and why ───────────────────────────────── */
function findingHtml(a,f){
  var lv=D.levelInfo(f.level), c=f.cause, rows="";
  if(c&&c.movers&&c.movers.length) rows='<div class="bi-tbl"><table><thead><tr><th>Campaign that moved</th><th class="r">Spent</th><th class="r">Usual</th><th class="r">Share of the excess</th></tr></thead><tbody>'+c.movers.map(function(x){
    return '<tr><td><b>'+esc(x.name)+'</b>'+(x.startDate?'<br><span class="bi-sub">started '+esc(x.startDate)+'</span>':"")+'</td><td class="r">'+esc(D.money(x.cost,a.currency))+'</td><td class="r">'+esc(D.money(x.usual,a.currency))+'</td><td class="r">'+Math.round(x.share*100)+'%</td></tr>';
  }).join("")+'</tbody></table></div>';
  return '<li class="sa-f lv-'+esc(f.level)+'"><div class="sa-f-h"><span class="bi-lv '+lv.tone+'">'+esc(lv.label)+'</span><b>'+esc(f.title)+'</b><span class="bi-sub">'+esc(D.kindText(f.kind))+' · '+esc(f.day==="today"?"today":"yesterday")+' ('+esc(f.key)+')</span></div>'+
    '<p class="sa-f-d">'+esc(f.detail)+'</p>'+rows+'</li>';
}
function findingsHtml(a){
  var fs=a.findings||[];
  if(!fs.length){
    return '<p class="pm-empty">'+(a.status==="none"?"This account spent nothing in the last 15 days.":a.status==="new"?esc((a.notes||[])[0]||"There is not enough history to judge it yet."):"Nothing unusual: today so far and yesterday are in line with what this account usually spends.")+'</p>';
  }
  return '<h4>What is unusual</h4><ul class="sa-fs">'+fs.map(function(f){return findingHtml(a,f);}).join("")+'</ul>';
}
function planHtml(a){
  var steps=(a.plan&&a.plan.steps)||[];
  if(!steps.length) return "";
  return '<div class="bi-plan"><h4>What to check</h4><ol class="bi-steps">'+steps.map(function(s,i){
    var l=D.levelInfo(s.level);
    return '<li><details'+(i===0?' open':'')+'><summary><span class="bi-sn">'+(i+1)+'</span><span class="bi-st"><b>'+esc(s.title)+'</b></span><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></summary>'+
      '<div class="bi-sb">'+(s.evidence?'<p class="bi-on">'+esc(s.evidence)+'</p>':"")+'<ul class="sa-acts">'+s.actions.map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ul>'+
      (s.where&&s.where.length?'<p class="bi-on">Start in: '+s.where.map(function(c){return '<span class="bi-chip">'+esc(c)+'</span>';}).join(" ")+'</p>':"")+'</div></details></li>';
  }).join("")+'</ol></div>';
}

/* ── The charts ───────────────────────────────────────────── */
function curveSvg(a){
  var g=D.curveGeometry(a.curve,640,210);
  if(!g) return '<p class="bi-sub">There is no spend by hour for this account, so only whole days are shown.</p>';
  var s='<svg class="sa-chart" viewBox="0 0 '+g.W+' '+g.H+'" role="img" aria-label="Spend through the day against the usual">';
  g.yticks.forEach(function(t){ s+='<line class="sa-grid" x1="'+g.pad.l+'" x2="'+(g.W-g.pad.r)+'" y1="'+t.y+'" y2="'+t.y+'"/><text class="sa-lbl" x="'+(g.pad.l-6)+'" y="'+(t.y+3)+'" text-anchor="end">'+esc(D.money(t.v,a.currency).replace(/\.\d\d$/,""))+'</text>'; });
  g.ticks.forEach(function(t){ s+='<text class="sa-lbl" x="'+t.x+'" y="'+(g.H-8)+'" text-anchor="middle">'+esc(t.label)+'</text>'; });
  s+='<path class="sa-upper" d="'+g.upper+'" fill="none"/><path class="sa-usual" d="'+g.usual+'" fill="none"/>';
  s+='<line class="sa-now" x1="'+g.nowX+'" x2="'+g.nowX+'" y1="'+g.pad.t+'" y2="'+g.base+'"/>';
  s+='<path class="sa-today" d="'+g.today+'" fill="none"/><circle class="sa-dot" cx="'+g.dot.x+'" cy="'+g.dot.y+'" r="4"/></svg>';
  return s+'<div class="pd-trend-legend"><span><i class="sa-k-today"></i>Today so far</span><span><i class="sa-k-usual"></i>Usual</span><span><i class="sa-k-upper"></i>Highest of the last days</span></div>';
}
function barsSvg(a){
  var g=D.barsGeometry(a.days,a.usualDay,640,170);
  var s='<svg class="sa-chart" viewBox="0 0 '+g.W+' '+g.H+'" role="img" aria-label="Spend each day against the usual day">';
  g.bars.forEach(function(b,i){
    s+='<rect class="sa-bar'+(b.partial?" partial":"")+'" x="'+b.x+'" y="'+b.y+'" width="'+b.w+'" height="'+b.h+'" rx="2"><title>'+esc(b.date+": "+D.money(b.cost,a.currency))+'</title></rect>';
    if(i%2===(g.bars.length-1)%2) s+='<text class="sa-lbl" x="'+(b.x+b.w/2)+'" y="'+(g.H-8)+'" text-anchor="middle">'+esc(b.label)+'</text>';
  });
  if(g.usualY!=null) s+='<line class="sa-usual-l" x1="'+g.pad.l+'" x2="'+(g.W-g.pad.r)+'" y1="'+g.usualY+'" y2="'+g.usualY+'"/><text class="sa-lbl" x="'+(g.pad.l-6)+'" y="'+(g.usualY+3)+'" text-anchor="end">usual</text>';
  return s+'</svg>';
}
function factsHtml(a){
  var t=a.today, y=a.yesterday, cur=a.currency;
  function k(l,v){return '<div><div class="k">'+esc(l)+'</div><div class="v">'+esc(v)+'</div></div>';}
  return '<div class="pd-kgrid">'+k("Today so far",D.money(t.cost,cur)+(t.hourly?" (to "+D.hh(t.through)+")":""))+k("Usual by now",t.usualByNow==null?"n/a":D.money(t.usualByNow,cur))+k("A usual day",D.money(a.usualDay,cur))+
    k("Yesterday",D.money(y.cost,cur))+k("Compared with",a.refDays+" days: "+a.basis)+k("Smallest swing flagged",D.money(a.floor,cur))+(a.budget?k("Daily budgets added up",D.money(a.budget,cur)):"")+'</div>';
}

/* ── What the team knows ──────────────────────────────────── */
function settingsHtml(a){
  var v=D.savedView(a.saved,a.today&&a.today.date);
  function inp(k,label,hint,type){return '<label class="cc-f"><span>'+label+'</span><input data-k="'+k+'" type="'+(type||"text")+'" value="'+esc(v[k])+'" placeholder="'+esc(hint)+'" autocomplete="off"></label>';}
  return '<h4>What the team knows about this account</h4>'+(v.ended?'<p class="bi-sub">The planned push muted until '+esc(v.mutedUntil)+' has ended.</p>':"")+
    '<form class="sa-set" data-account="'+esc(a.id)+'">'+
    inp("usualDaily","Usual spend a day ("+esc(a.currency)+")","used while fewer than 4 days are known")+inp("maxDaily","Ceiling a day ("+esc(a.currency)+")","a day that reaches it is always high")+inp("minSwing","Smallest swing worth a word ("+esc(a.currency)+")","25 when blank")+
    inp("mutedUntil","Planned push: mute spikes until","2026-11-30","date")+inp("note","Why","Black Friday sale")+
    '<div class="cc-set-b"><button class="pm-btn" type="submit">Save</button><span class="bi-sub" data-out></span></div></form>';
}
function body(a){
  return '<div class="bi-body">'+findingsHtml(a)+planHtml(a)+'<h4>Today through the day</h4>'+curveSvg(a)+'<h4>The last days</h4>'+barsSvg(a)+factsHtml(a)+settingsHtml(a)+'</div>';
}
$("#list").addEventListener("click",function(e){
  var head=e.target.closest(".bi-head"); if(head){
    var art=head.closest(".bi-row"), id=art.dataset.id; S.open[id]=!S.open[id]; paintList();
  }
});
$("#list").addEventListener("submit",function(e){
  var f=e.target.closest(".sa-set"); if(!f) return; e.preventDefault();
  var body={account:f.dataset.account}, out=f.querySelector("[data-out]");
  [].forEach.call(f.querySelectorAll("input"),function(t){ body[t.dataset.k]=t.value; });
  out.textContent="Saving…";
  postJson(API+"/settings/account",body).then(function(r){
    if(!r.ok){ out.textContent=(r.j&&r.j.error)||"That could not be saved."; return; }
    out.textContent="Saved. Judging again…";
    run(false,true);
  }).catch(function(){ out.textContent="The server could not be reached. Try again."; });
});

/* ── Sensitivity ──────────────────────────────────────────── */
function paintSens(){
  var cur=S.report.sensitivity||"normal", r=S.settings.ratios||{};
  function ratios(k){return r[k]?r[k][0]+"x a usual pace is medium, "+r[k][1]+"x is high":"";}
  $("#sens").innerHTML=["low","normal","high"].map(function(k){
    return '<button type="button" role="radio" class="pm-chip'+(k===cur?" on":"")+'" data-s="'+k+'" aria-checked="'+(k===cur)+'" title="'+esc(ratios(k))+'">'+esc(D.SENS[k])+'</button>';
  }).join("");
  $("#sensText").textContent="How far above its usual an account must spend before it is flagged: "+ratios(cur)+" (a short surge needs a little more). It applies to every account and to the alerts.";
}
$("#sens").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b||!b.dataset.s) return;
  var out=$("#sensOut"); out.textContent="Saving…";
  postJson(API+"/settings/sensitivity",{sensitivity:b.dataset.s}).then(function(r){
    if(!r.ok){ out.textContent=(r.j&&r.j.error)||"That could not be saved."; return; }
    S.settings.sensitivity=r.j.sensitivity; out.textContent="Saved. Judging again…"; run(false,true);
  }).catch(function(){ out.textContent="The server could not be reached. Try again."; });
});
function loadSettings(){
  return getJson(API+"/settings").then(function(r){ if(r.ok) S.settings={accounts:r.j.accounts||{},sensitivity:r.j.sensitivity||null,ratios:r.j.ratios||{}}; }).catch(function(){});
}

/* ── Refresh and CSV ──────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var blob=new Blob(["﻿"+D.csvRows(rep).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="spend-anomaly-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+String(rep.generatedAt||"").slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("an_account")||"";
  if(window.MonitorPanel&&$("#monPanel")) window.MonitorPanel.mount($("#monPanel"),{agent:"spend",label:"Spend Anomaly Alert"});
  S.account="";
  (typeof loadSettings==="function"?loadSettings():Promise.resolve()).then(function(){
    scope=window.PMScope.mount($("#acctPick"),{account:want,onChange:function(a){ S.account=a; store("an_account",a); run(); },
      onError:function(e){setMsg(esc((e&&e.message)||"The account list could not be read."));}});
  });
})();
})();
