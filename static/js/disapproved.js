/* Disapproved Ads Tracker (Paid Marketing Agents).
 *
 * Pick a Google Ads account (or an MCC). The server reads the enabled ads Google has disapproved or limited (tracker/paid_disapproved.py), works out which ad groups can show nothing,
 * groups the ads by policy and builds the findings and the fix plan (paid_disapproved_math.py, paid_disapproved_plan.py). This page draws it and never decides what is stopped, what a
 * policy means or what to do for itself. The wording and the shapes are disapproved_data.js.
 */
(function(){
"use strict";

var D = window.DAData, API = "/p2/paid-marketing/api/disapproved";
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

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}, meta:[]};

var picker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){
  S.account=v; store("da_account",v); run();
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
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}

function run(refresh){
  if(!S.account){show("empty");setMsg("");return;}
  var seq=++S.seq, g=groupOf(S.account);
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.account));}catch(e){}
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 20 seconds.":"",g?"pm-note":"");
  show("skel");
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
    S.report=r.j; S.open={}; S.view=D.firstView(r.j);
    if(r.j.accounts.length===1) S.open[r.j.accounts[0].id]=true;
    show("out");
    try{render();}catch(e){setMsg("The report was read but could not be drawn: "+esc(String(e&&e.message||e)));}
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}


/* ── Rendering ────────────────────────────────────────────── */
var SHOWN_ADS = 40;
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts checked together</span>':"")+
    ' · read from Google Ads '+esc(ago(rep.readAt||rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">Google Ads is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintInsights(); paintViews(); paintList();
  $("#foot").innerHTML="Source: Google Ads, saved so the same report opens instantly. An ad group is <b>stopped</b> when every enabled ad in it is disapproved: nothing in it can show, whatever its keywords. "+
    "A disapproved ad with another approved ad beside it is not stopped (the rotation is smaller and one fix clears it). <b>What an ad group used to bring</b> is the middle of its clicks in the four whole weeks before the last, "+
    "for the weeks that had any; it is an estimate of what is not coming, not a count. The policy hints are the usual fixes, not a ruling: the text in Policy manager is the ruling. "+
    "Only enabled ads in enabled ad groups and campaigns are read. In Performance Max the unit is a field of an asset group, stopped when it has fewer approved assets than Google needs to serve it (three headlines, two descriptions, a long headline, a landscape and a square image, a business name). "+
    "How long something has been open comes from the daily run: a problem seen for the first time today has no age. Shopping product feeds (Merchant Center) are not in the Google Ads API. Nothing is changed in Google Ads.";
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
  $("#listNote").textContent=S.report.accounts.length+(S.report.accounts.length===1?" account":" accounts")+", most serious first";
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Nothing needs a look: no enabled ad is disapproved in the accounts read.")+'</p>';
}
function figHtml(a){
  var c=a.counts;
  if(a.status==="clear") return '<div class="bs-fig ok"><span class="bi-n">0</span><span class="bs-sub">no flagged ad</span></div>';
  if(a.status==="stopped"){
    var ages=a.deadGroups.map(function(g){return g.ageDays;}).filter(function(x){return x!=null&&x>=1;});
    return '<div class="bs-fig stopped"><span class="bi-n">'+D.int(c.stoppedGroups)+'</span><span class="bs-sub">ad group'+(c.stoppedGroups===1?"":"s")+' with nothing to show'+(ages.length?' · the oldest for '+Math.max.apply(null,ages)+' days':"")+'</span></div>';
  }
  if(a.status==="disapproved") return '<div class="bs-fig over"><span class="bi-n">'+D.int(c.disapproved)+'</span><span class="bs-sub">ad'+(c.disapproved===1?"":"s")+' disapproved, others cover</span></div>';
  return '<div class="bs-fig under"><span class="bi-n">'+D.int(c.limited)+'</span><span class="bs-sub">ad'+(c.limited===1?"":"s")+' limited</span></div>';
}
function row(a){
  var open=!!S.open[a.id], top=a.topics[0], c=a.counts, wait=[];
  if(c.appeal) wait.push(c.appeal+" under appeal");
  if(c.reviewing) wait.push(c.reviewing+" in review");
  return '<article class="bi-row '+a.status+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+(a.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+'</b><span class="bi-sub">'+(a.items.length?a.items.length+" flagged ad"+(a.items.length===1?"":"s")+" in "+a.adGroups+" ad group"+(a.adGroups===1?"":"s")+" of "+a.campaigns+" campaign"+(a.campaigns===1?"":"s"):"no flagged ad")+'</span></span>'+
      figHtml(a)+
      '<span class="bi-delta bad">'+(top?esc(D.topicLabel(top.topic)):"")+'</span>'+
      '<span class="bi-why">'+esc(wait.join(", "))+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function topicsHtml(a){
  if(!a.topics.length) return "";
  return '<h4>By policy</h4><div class="bi-tbl"><table><thead><tr><th>Policy</th><th>Type</th><th class="r">Ads</th><th class="r">Ad groups</th><th class="r">Stopped</th><th class="r">Flagged for</th></tr></thead><tbody>'+
    a.topics.map(function(t){
      var age=t.ageDays==null?"":t.ageDays<1?"today":t.ageDays+" day"+(t.ageDays===1?"":"s");
      return '<tr><td>'+esc(t.label)+(t.reopened?' <span class="bi-mk">came back '+t.reopened+'x</span>':"")+'</td><td>'+esc(String(t.type||"").replace(/_/g," ").toLowerCase())+'</td><td class="r">'+D.int(t.ads)+'</td><td class="r">'+D.int(t.adGroups)+'</td><td class="r">'+(t.dead?D.int(t.dead):"")+'</td><td class="r">'+esc(age)+'</td></tr>';
    }).join("")+'</tbody></table></div>';
}
function findingsHtml(a){
  if(!a.findings.length) return '<p class="pm-empty">Nothing to flag: no enabled ad in this account is disapproved or limited.</p>';
  return '<ul class="bi-finds">'+a.findings.map(function(f){
    var l=D.levelInfo(f.level);
    return '<li><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span><div><b>'+esc(f.title)+'</b><p>'+esc(f.detail)+'</p><p class="bi-next">'+esc(f.nextStep)+'</p></div></li>';
  }).join("")+'</ul>';
}
function deadHtml(a){
  if(!a.deadGroups.length) return "";
  return '<h4>Ad groups with nothing to show</h4><div class="bi-tbl"><table><thead><tr><th>Ad group</th><th>Campaign</th><th class="r">Ads flagged</th><th class="r">Clicks a week before</th><th class="r">Last week</th><th class="r">Not coming</th><th class="r">Stopped for</th><th class="r">Lost since</th></tr></thead><tbody>'+
    a.deadGroups.map(function(g){
      var w=g.weight;
      return '<tr><td>'+esc(g.name)+(g.reopened?' <span class="bi-mk">came back '+g.reopened+'x</span>':"")+'</td><td>'+esc(g.campaign)+'</td><td class="r">'+D.int(g.ads)+'</td><td class="r">'+(w?D.int(w.usualClicks):"n/a")+'</td><td class="r">'+(w?D.int(w.lastClicks):"n/a")+'</td><td class="r">'+(w?D.int(w.lostClicks):"n/a")+'</td><td class="r">'+(g.ageDays==null?"n/a":g.ageDays<1?"today":g.ageDays+" day"+(g.ageDays===1?"":"s"))+'</td><td class="r">'+(g.lostSoFar==null?"n/a":"about "+D.int(g.lostSoFar)+" clicks")+'</td></tr>';
    }).join("")+'</tbody></table></div>';
}
function suspectsHtml(a){
  var sus=a.suspects||[];
  if(!sus.length) return "";
  var what={headline:"Headline",evidence:"Text Google points at",url:"Page"};
  return '<h4>What the flagged ads have in common</h4><div class="bi-tbl"><table><thead><tr><th>Kind</th><th>Text</th><th class="r">In</th></tr></thead><tbody>'+sus.map(function(x){
    return '<tr><td>'+esc(what[x.kind]||x.kind)+'</td><td>'+esc(x.text)+'</td><td class="r">'+x.ads+' of '+x.of+' flagged ads</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function keywordsHtml(a){
  var kws=a.items.filter(function(i){return i.kind==="keyword";});
  if(!kws.length) return "";
  return '<h4>Disapproved keywords <span class="dim">('+kws.length+')</span></h4><div class="bi-tbl"><table><thead><tr><th>Keyword</th><th>Campaign and ad group</th><th>Policy</th></tr></thead><tbody>'+kws.slice(0,SHOWN_ADS).map(function(i){
    return '<tr><td>'+esc((i.headlines||[])[0]||i.id)+'</td><td>'+esc(i.campaign)+'<br><span class="bi-sub">'+esc(i.adGroup)+'</span></td><td>'+(i.topics.length?i.topics.map(function(t){return esc(D.topicLabel(t.t));}).join(", "):'<span class="bi-sub">not named</span>')+'</td></tr>';
  }).join("")+'</tbody></table></div>'+(kws.length>SHOWN_ADS?'<p class="bs-note">The first '+SHOWN_ADS+' of '+kws.length+'; the CSV has all.</p>':"");
}
function adsHtml(a){
  var flagged=a.items.filter(function(i){return i.kind!=="keyword";});
  if(!flagged.length) return "";
  var list=flagged.slice(0,SHOWN_ADS);
  return '<h4>Flagged ads'+(flagged.length>SHOWN_ADS?' <span class="dim">(the first '+SHOWN_ADS+' of '+flagged.length+'; the CSV has all)</span>':"")+'</h4><div class="bi-tbl"><table><thead><tr><th>Ad</th><th>Campaign and ad group</th><th>Status</th><th>Policy</th></tr></thead><tbody>'+
    list.map(function(i){
      var st=D.stateInfo(i.state), rv=D.reviewText(i.review);
      return '<tr><td>'+(i.kind!=="ad"?'<span class="bi-mk">'+esc(D.kindText(i.kind))+'</span> ':"")+esc((i.headlines||[]).join(" | ")||String(i.type||"").replace(/_/g," ").toLowerCase()||("Ad "+i.id))+(i.url?'<br><span class="bi-sub">'+esc(i.url)+'</span>':"")+'</td>'+
        '<td>'+esc(i.campaign)+'<br><span class="bi-sub">'+esc(i.adGroup)+'</span></td>'+
        '<td><span class="bi-pill '+st.tone+'">'+esc(st.label)+'</span><br><span class="bi-sub">'+esc(D.approvalText(i.approval))+(rv?" · "+esc(rv):"")+'</span></td>'+
        '<td>'+(i.topics.length?i.topics.map(function(t){return esc(D.topicLabel(t.t))+(t.ev&&t.ev.length?'<br><span class="bi-sub">'+esc(t.ev.map(function(x){return '"'+x+'"';}).join(", "))+'</span>':"");}).join("<br>"):'<span class="bi-sub">not named</span>')+'</td></tr>';
    }).join("")+'</tbody></table></div>';
}
function planHtml(a){
  var p=a.plan||{}, steps=p.steps||[];
  if(!steps.length) return "";
  return '<div class="bi-plan"><h4>Fix plan</h4>'+(p.note?'<p class="bi-plan-note">'+esc(p.note)+'</p>':"")+
    '<ol class="bi-steps">'+steps.map(function(s,i){
      var l=D.levelInfo(s.level);
      return '<li><details'+(i===0?' open':'')+'><summary><span class="bi-sn">'+s.n+'</span><span class="bi-st"><b>'+esc(s.title)+'</b></span>'+
        '<span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></summary>'+
        '<div class="bi-sb"><p class="bi-why-t"><b>Why this applies.</b> '+esc(s.why)+'</p><p class="bi-how-h">What to do</p><ol class="bi-how">'+s.how.map(function(h){return '<li>'+esc(h)+'</li>';}).join("")+'</ol>'+
        (s.worth?'<p class="bs-worth"><b>Worth.</b> '+esc(s.worth)+'</p>':"")+
        (s.campaigns&&s.campaigns.length?'<p class="bi-on">Applies to: '+s.campaigns.map(function(c){return '<span class="bi-chip">'+esc(c)+'</span>';}).join(" ")+'</p>':"")+'</div></details></li>';
    }).join("")+'</ol></div>';
}
function body(a){
  return '<div class="bi-body"><h4>What it means</h4>'+findingsHtml(a)+planHtml(a)+topicsHtml(a)+suspectsHtml(a)+deadHtml(a)+keywordsHtml(a)+adsHtml(a)+'</div>';
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
  a.download="disapproved-ads-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.window.to+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("da_account")||"";
  if(window.MonitorPanel&&$("#monPanel")) window.MonitorPanel.mount($("#monPanel"),{agent:"disapproved",label:"Disapproved Ads Tracker"});
  S.account=want;
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  Promise.all([getJson("/p2/paid-marketing/api/segments/accounts"),getJson("/p2/paid-marketing/api/gads/accounts?platform=meta").catch(function(){return {ok:false,j:{}};})]).then(function(rs){
    var r=rs[0], meta=rs[1];
    if(!r.ok){picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:0}],"",true);setMsg(esc((r.j&&r.j.error)||"The account list could not be read."));return;}
    S.meta=meta.ok?(meta.j.accounts||[]).filter(function(a){return a.platform==="meta"||/^act_\d+$/.test(a.id);}):[];
    setAccounts(r.j.accounts||[],r.j.groups||[]); run();
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
})();
})();
