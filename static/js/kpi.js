/* KPI Monitoring (Paid Marketing Agents).
 *
 * Pick a Google Ads account, an MCC or a Meta ad account. The server reads each campaign's last two weeks and holds the last 7 days against its target (tracker/paid_kpi*.py). This page draws
 * it and never decides what is off target for itself. The wording and the shapes are kpi_data.js.
 */
(function(){
"use strict";

var D = window.KPData, API = "/p2/paid-marketing/api/kpi";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}
function store(k,v){try{if(v===undefined)return localStorage.getItem(k)||"";localStorage.setItem(k,v);}catch(e){return "";}}

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}, meta:[], settings:{accounts:{}}, shown:{}, quietShown:{}};

var picker = window.PMPicker($("#acctPick"),{label:"Ad account",search:true,onChange:function(v){
  S.account=v; store("kp_account",v); run();
}});
function groupOf(id){return S.groups.filter(function(g){return g.id===id;})[0]||null;}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function setAccounts(list, groups){
  S.accounts=list; S.groups=groups||[];
  var items=[{value:"",label:"Choose an account",sub:list.length+" accounts"+(S.groups.length?" · "+S.groups.length+" MCCs to check together":""),avatar:"?",hue:200}], seen={};
  S.groups.forEach(function(g){
    var head=g.name+" MCC";
    items.push({value:g.id,label:"All of "+g.name,avatar:inits(g.name),hue:hueOf(g.name),group:head,badge:MCC_BADGE,sub:g.accountIds.length+" accounts checked together"});
    list.filter(function(a){return a.group===g.id;}).forEach(function(a){
      seen[a.id]=1; items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:head,sub:dashed(a.id)});
    });
  });
  list.filter(function(a){return !seen[a.id];}).forEach(function(a){
    items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:S.groups.length?"Other accounts":"",sub:dashed(a.id)});
  });
  S.meta.forEach(function(a){ items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:"Meta ad accounts",badge:'<span class="badge pm-pill">Meta</span>',sub:a.id}); });
  if(!list.some(function(a){return a.id===S.account;})&&!groupOf(S.account)&&!S.meta.some(function(a){return a.id===S.account;})) S.account="";
  picker.set(items,S.account);
}

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
  var seq=++S.seq, g=groupOf(S.account);
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.account));}catch(e){}
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 30 seconds.":"",g?"pm-note":"");
  if(!keep) show("skel");
  getJson(API+"/report?"+D.query(S.account,refresh===true)).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg("");
    if(!r.ok){
      show(S.report?"out":"empty");
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j;
    if(!keep){ S.open={}; S.shown={}; S.quietShown={}; S.view=D.firstView(r.j); if(r.j.accounts.length===1) S.open[r.j.accounts[0].id]=true; }
    show("out");
    try{render();}catch(e){setMsg("The report was read but could not be drawn: "+esc(String(e&&e.message||e)));}
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
var SHOWN_CAMPS = 20, MORE = 20, QUIET_ROWS = 20;
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts checked together</span>':"")+
    ' · read '+esc(ago(rep.readAt||rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">The platform is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintInsights(); paintViews(); paintList();
  var m=rep.minimums||{};
  $("#foot").innerHTML="Source: the live campaigns of Google Ads (their status, bid strategy and its own target, and their cost, clicks, impressions and conversions by day) and the active campaigns of Meta, saved so the same report opens instantly. "+
    "The <b>last 7 whole days</b> are held against the target and against the 7 days before. A figure is judged only with enough behind it: "+(m.conversions||5)+" conversions for a cost or a return, "+(m.clicks||100)+" clicks for a conversion rate, "+(m.impressions||1000)+" impressions for a click-through rate; "+
    "a campaign without that says so instead of being called on target. A target is the one saved for the campaign, then the one saved for the account, then (cost and return, Google) the campaign's own bid strategy target. "+
    "A campaign counts as stuck in learning after "+(m.stuckDays||14)+" days, counted from the first day the daily check saw it learning. Nothing is changed in an ad account.";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.report).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintInsights(){
  var list=D.insights(S.report), host=$("#insights");
  host.innerHTML=list.length?'<section class="pm-panel da-ins"><div class="pm-panel-h"><span class="pm-panel-t">Across the campaigns</span></div><ul>'+list.map(function(i){return '<li class="'+i.kind+'">'+esc(i.text)+'</li>';}).join("")+'</ul></section>':"";
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
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Nothing needs a look: no campaign is off its target or held back.")+'</p>';
}
function figHtml(a){
  var c=a.counts;
  if(a.status==="clean") return '<div class="bs-fig ok"><span class="bi-n">0</span><span class="bs-sub">nothing wrong in '+D.plural(a.campaigns,"campaign")+'</span></div>';
  if(a.status==="fix") return '<div class="bs-fig stopped"><span class="bi-n">'+D.int(c.high+c.medium)+'</span><span class="bs-sub">to fix'+(c.high?' · '+c.high+' high':"")+'</span></div>';
  return '<div class="bs-fig under"><span class="bi-n">'+D.int(c.low)+'</span><span class="bs-sub">small finding'+(c.low===1?"":"s")+'</span></div>';
}
function row(a){
  var open=!!S.open[a.id], top=a.byRule[0];
  return '<article class="bi-row '+(a.status==="fix"?"stopped":a.status==="check"?"limited":"clear")+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+(a.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+'</b><span class="bi-sub">'+D.plural(a.campaigns,"campaign")+' read'+(a.offTarget?', '+a.offTarget+' off target':"")+(a.findingCount?', '+D.plural(a.findingCount,"finding"):"")+'</span></span>'+
      figHtml(a)+
      '<span class="bi-delta bad">'+(top?esc(top.label)+' · '+D.plural(top.findings,"finding"):"")+'</span>'+
      '<span class="bi-why">'+(a.targetsSet?"":"no target saved")+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function metricsTable(rows){
  if(!rows.length) return "";
  return '<div class="bi-tbl kp-metrics"><table><thead><tr><th>Figure</th><th class="r">Last 7 days</th><th class="r">The 7 days before</th><th class="r">Target</th><th>Verdict</th></tr></thead><tbody>'+rows.map(function(m){
    var s=D.stateInfo(m.state);
    return '<tr><td>'+esc(m.label)+'</td><td class="r">'+esc(m.now)+'</td><td class="r">'+esc(m.before)+'</td><td class="r">'+esc(m.target)+(m.source?'<br><span class="bi-sub">'+esc(m.source)+'</span>':"")+'</td>'+
      '<td><span class="bi-lv '+s.tone+'">'+esc(s.label)+'</span>'+(m.why?'<br><span class="bi-sub">'+esc(m.why)+'</span>':"")+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function headlineHtml(a){
  var rows=D.headlineRows(a);
  if(!rows.length) return "";
  var r=a.headline.recent;
  return '<h4>The account as one <span class="dim">('+esc(D.money(r.cost,a.currency))+' spent and '+D.plural(Math.round(r.conv*10)/10,"conversion")+' in the last 7 days, against the targets saved for the account)</span></h4>'+metricsTable(rows);
}
function rulesHtml(a){
  if(!a.byRule.length) return '<p class="pm-empty">Nothing to flag: the '+D.plural(a.campaigns,"campaign")+' read are delivering and none is off its target'+(a.targetsSet?"":" (no target is saved, so cost and return were not judged)")+'.</p>';
  return '<h4>What was found</h4><div class="bi-tbl"><table><thead><tr><th>Problem</th><th>Level</th><th class="r">Findings</th></tr></thead><tbody>'+a.byRule.map(function(b){
    var l=D.levelInfo(b.level);
    return '<tr><td><b>'+esc(b.label)+'</b><br><span class="bi-sub">'+esc(b.fix)+'</span></td><td><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></td><td class="r">'+D.int(b.findings)+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function planHtml(a){
  var steps=(a.plan&&a.plan.steps)||[];
  if(!steps.length) return "";
  return '<div class="bi-plan"><h4>Fix plan</h4><ol class="bi-steps">'+steps.map(function(s,i){
    var l=D.levelInfo(s.level);
    return '<li><details'+(i===0?' open':'')+'><summary><span class="bi-sn">'+(i+1)+'</span><span class="bi-st"><b>'+esc(s.title)+'</b></span><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></summary>'+
      '<div class="bi-sb"><p class="bi-why-t">'+esc(s.how)+'</p>'+
      (s.where&&s.where.length?'<p class="bi-on">Start in: '+s.where.map(function(c){return '<span class="bi-chip">'+esc(c)+'</span>';}).join(" ")+'</p>':"")+
      (s.examples&&s.examples.length?'<ul class="gc-ex">'+s.examples.map(function(c){return '<li>'+esc(c)+'</li>';}).join("")+'</ul>':"")+'</div></details></li>';
  }).join("")+'</ol></div>';
}
function findingHtml(f){
  var l=D.levelInfo(f.level);
  return '<li><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span> <b>'+esc(f.title)+'</b><p class="gc-detail">'+esc(f.detail)+'</p>'+
    (f.evidence&&f.evidence.length?'<ul class="kp-evid">'+f.evidence.map(function(e){return '<li>'+esc(e)+'</li>';}).join("")+'</ul>':"")+'</li>';
}
function toolsHtml(a,c){
  var saved=S.settings.accounts[a.id]||{}, v=D.campaignView(saved,c.id);
  return '<details class="kp-tools"><summary>Targets for this campaign, or leave it out</summary>'+
    '<form class="cc-set kp-camp-set" data-kind="campaign" data-account="'+esc(a.id)+'" data-campaign="'+esc(c.id)+'" data-name="'+esc(c.name)+'">'+
      '<label class="cc-f"><span>Cost per conversion</span><input data-k="cpa" inputmode="decimal" value="'+esc(v.cpa)+'" placeholder="the account\'s"></label>'+
      '<label class="cc-f"><span>Return on ad spend</span><input data-k="roas" inputmode="decimal" value="'+esc(v.roas)+'" placeholder="the account\'s"></label>'+
      '<label class="cc-f"><span>Lowest conversion rate (%)</span><input data-k="cvr" inputmode="decimal" value="'+esc(v.cvr)+'" placeholder="the account\'s"></label>'+
      '<label class="cc-f"><span>Lowest click-through rate (%)</span><input data-k="ctr" inputmode="decimal" value="'+esc(v.ctr)+'" placeholder="the account\'s"></label>'+
      '<div class="cc-set-b"><button class="pm-btn" type="submit">Save targets</button><span class="bi-sub" data-out></span></div></form>'+
    '<form class="cc-set kp-mute" data-kind="mute" data-account="'+esc(a.id)+'" data-campaign="'+esc(c.id)+'" data-name="'+esc(c.name)+'">'+
      '<label class="cc-f"><span>Leave it out of the checks because</span><input data-k="why" maxlength="200" placeholder="a test campaign, being replaced"></label>'+
      '<div class="cc-set-b"><button class="pm-btn-ghost" type="submit">Leave out</button><span class="bi-sub" data-out></span></div></form></details>';
}
function campHtml(a,c){
  var top=c.findings.length?D.levelInfo(c.level):null;
  return '<li class="gc-camp kp-camp"><div class="gc-camp-h">'+(top?'<span class="bi-lv '+top.tone+'">'+esc(top.label)+'</span>':"")+'<b>'+esc(c.name)+'</b> <span class="bi-sub">'+esc(D.channelText(c.channel))+
      (c.strategy?' · '+esc(c.strategy.replace(/_/g," ").toLowerCase()):"")+' · '+esc(D.money(c.cost,a.currency))+' in 7 days</span></div>'+
    metricsTable(D.metricRows(c,a.currency))+
    (c.findings.length?'<ul class="gc-finds">'+c.findings.map(findingHtml).join("")+'</ul>':"")+toolsHtml(a,c)+'</li>';
}
function campsHtml(a){
  var list=D.flagged(a);
  if(!list.length) return "";
  var n=S.shown[a.id]||SHOWN_CAMPS;
  return '<h4>The campaigns <span class="dim">('+D.plural(list.length,"campaign")+' with something to look at, worst first)</span></h4><ul class="gc-camps">'+list.slice(0,n).map(function(c){return campHtml(a,c);}).join("")+'</ul>'+
    (list.length>n?'<p class="bs-note">The first '+n+' of '+list.length+'; <button type="button" class="cc-more" data-account="'+esc(a.id)+'">show '+Math.min(MORE,list.length-n)+' more</button>. The CSV has all.</p>':"");
}
function quietHtml(a){
  var list=D.quiet(a);
  if(!list.length) return "";
  var n=S.quietShown[a.id]||QUIET_ROWS;
  return '<details class="gc-quiet"><summary>'+D.plural(list.length,"campaign")+' with nothing wrong</summary><ul class="gc-camps">'+list.slice(0,n).map(function(c){return campHtml(a,c);}).join("")+'</ul>'+
    (list.length>n?'<p class="bs-note">The first '+n+' of '+list.length+'; <button type="button" class="cc-more-q" data-account="'+esc(a.id)+'">show '+Math.min(MORE,list.length-n)+' more</button>.</p>':"")+'</details>';
}
function leftOutHtml(a){
  var list=D.leftOut(a);
  if(!list.length) return "";
  var saved=S.settings.accounts[a.id]||{};
  return '<h4>Left out of the checks <span class="dim">('+D.plural(list.length,"campaign")+')</span></h4><ul class="kp-out">'+list.map(function(c){
    return '<li><b>'+esc(c.name)+'</b> <span class="bi-sub">'+esc(c.mutedWhy||D.mutedWhy(saved,c.id))+'</span> <button type="button" class="cc-ok kp-unmute" data-account="'+esc(a.id)+'" data-campaign="'+esc(c.id)+'">Put back</button></li>';
  }).join("")+'</ul>';
}
function settingsHtml(a){
  var v=D.savedView(S.settings.accounts[a.id]), cur=a.currency||"";
  return '<h4>The targets for this account</h4><form class="cc-set kp-set" data-kind="account" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'">'+
    '<label class="cc-f"><span>Cost per conversion or lead, in '+esc(cur)+'</span><input data-k="cpa" inputmode="decimal" value="'+esc(v.cpa)+'" placeholder="e.g. 45"></label>'+
    '<label class="cc-f"><span>Return on ad spend (4 means 400 percent)</span><input data-k="roas" inputmode="decimal" value="'+esc(v.roas)+'" placeholder="e.g. 4"></label>'+
    '<label class="cc-f"><span>Lowest conversion rate (%)</span><input data-k="cvr" inputmode="decimal" value="'+esc(v.cvr)+'" placeholder="e.g. 3"></label>'+
    '<label class="cc-f"><span>Lowest click-through rate (%)</span><input data-k="ctr" inputmode="decimal" value="'+esc(v.ctr)+'" placeholder="e.g. 5"></label>'+
    '<label class="cc-f"><span>How far past a target before it is flagged (%)</span><input data-k="tolerance" inputmode="decimal" value="'+esc(v.tolerance)+'" placeholder="20"></label>'+
    '<div class="cc-set-b"><button class="pm-btn" type="submit">Save</button><span class="bi-sub" data-out></span></div></form>';
}
function body(a){
  return '<div class="bi-body">'+headlineHtml(a)+rulesHtml(a)+planHtml(a)+campsHtml(a)+quietHtml(a)+leftOutHtml(a)+settingsHtml(a)+'</div>';
}
$("#list").addEventListener("click",function(e){
  var un=e.target.closest(".kp-unmute");
  if(un){ un.disabled=true; unmute(un.dataset.account,un.dataset.campaign,un); return; }
  var more=e.target.closest(".cc-more");
  if(more){ S.shown[more.dataset.account]=(S.shown[more.dataset.account]||SHOWN_CAMPS)+MORE; paintList(); return; }
  var moreq=e.target.closest(".cc-more-q");
  if(moreq){ S.quietShown[moreq.dataset.account]=(S.quietShown[moreq.dataset.account]||QUIET_ROWS)+MORE; paintList(); return; }
  var head=e.target.closest(".bi-head"); if(head){
    var art=head.closest(".bi-row"), id=art.dataset.id; S.open[id]=!S.open[id]; paintList();
  }
});

/* ── What the team saved ──────────────────────────────────── */
function loadSettings(){
  return getJson(API+"/settings").then(function(r){ if(r.ok) S.settings={accounts:r.j.accounts||{}}; }).catch(function(){});
}
function saved(account,out,r){
  if(!r.ok){ out.textContent=(r.j&&r.j.error)||"That could not be saved."; return false; }
  S.settings.accounts[account]=r.j.account;
  out.textContent="Saved. Checking again…";
  run(false,true);
  return true;
}
function failed(out){ out.textContent="The server could not be reached. Try again."; }
function unmute(account,campaign,btn){
  postJson(API+"/settings/unmute",{account:account,campaign:campaign}).then(function(r){
    if(!r.ok){ if(btn) btn.disabled=false; setMsg(esc((r.j&&r.j.error)||"That could not be saved.")); return; }
    S.settings.accounts[account]=r.j.account;
    run(false,true);
  }).catch(function(){ if(btn) btn.disabled=false; setMsg("The server could not be reached. Try again."); });
}
function fields(f){
  var out={};
  [].forEach.call(f.querySelectorAll("input,textarea"),function(t){ out[t.dataset.k]=t.value; });
  return out;
}
$("#list").addEventListener("submit",function(e){
  var f=e.target.closest(".kp-set,.kp-camp-set,.kp-mute"); if(!f) return; e.preventDefault();
  var out=f.querySelector("[data-out]"), v=fields(f), account=f.dataset.account;
  out.textContent="Saving…";
  if(f.dataset.kind==="account"){
    postJson(API+"/settings/account",{account:account,name:f.dataset.name,targets:{cpa:v.cpa,roas:v.roas,cvr:v.cvr,ctr:v.ctr},tolerance:v.tolerance}).then(function(r){ saved(account,out,r); }).catch(function(){ failed(out); });
  } else if(f.dataset.kind==="campaign"){
    postJson(API+"/settings/campaign",{account:account,campaign:f.dataset.campaign,name:f.dataset.name,cpa:v.cpa,roas:v.roas,cvr:v.cvr,ctr:v.ctr}).then(function(r){ saved(account,out,r); }).catch(function(){ failed(out); });
  } else {
    postJson(API+"/settings/mute",{account:account,campaign:f.dataset.campaign,name:f.dataset.name,why:v.why}).then(function(r){ saved(account,out,r); }).catch(function(){ failed(out); });
  }
});

/* ── Refresh and CSV ──────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var blob=new Blob(["﻿"+D.csvRows(rep).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="kpis-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+String(rep.generatedAt||"").slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("kp_account")||"";
  if(window.MonitorPanel&&$("#monPanel")) window.MonitorPanel.mount($("#monPanel"),{agent:"kpi",label:"KPI Monitoring"});
  S.account=want;
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  Promise.all([getJson("/p2/paid-marketing/api/segments/accounts"),getJson("/p2/paid-marketing/api/gads/accounts?platform=meta").catch(function(){return {ok:false,j:{}};}),loadSettings()]).then(function(rs){
    var r=rs[0], meta=rs[1];
    if(!r.ok){picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:0}],"",true);setMsg(esc((r.j&&r.j.error)||"The account list could not be read."));return;}
    S.meta=meta.ok?(meta.j.accounts||[]).filter(function(a){return a.platform==="meta"||/^act_\d+$/.test(a.id);}):[];
    setAccounts(r.j.accounts||[],r.j.groups||[]); run();
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
})();
})();
