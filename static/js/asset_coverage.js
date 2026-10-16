/* Asset Coverage Audit (Paid Marketing Agents).
 *
 * Pick a Google Ads account (or an MCC). The server reads every asset the live campaigns carry and holds each campaign, Performance Max
 * asset group and Display or Demand Gen ad against what its kind should hold (tracker/paid_assets.py, paid_assets_rules.py). This page
 * draws the report. It never counts an asset, judges a shortfall or decides what to add for itself.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/assets";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function int(v){return v==null?"Na":Math.round(v).toLocaleString("en-US");}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}

var SEV = {high:["Fix first","high"], medium:["Add","medium"], low:["Nice to have","low"]};
var SEV_RANK = {high:3, medium:2, low:1};
var SOURCE = {campaign:"this campaign", account:"the account", "asset group":"the asset group", ad:"the ad"};
var S = {accounts:[], groups:[], account:"", currency:"USD", report:null, seq:0, sev:"high", kind:"all", filter:"", limit:25, open:{}};

var picker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){
  S.account=v; try{localStorage.setItem("ac_account",v);}catch(e){} run();
}});
function groupOf(id){return S.groups.filter(function(g){return g.id===id;})[0]||null;}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function setAccounts(list, groups){
  S.accounts=list; S.groups=groups||[];
  var items=[{value:"",label:"Choose an account",sub:list.length+" accounts"+(S.groups.length?" · "+S.groups.length+" MCCs to combine":""),avatar:"?",hue:200}], seen={};
  S.groups.forEach(function(g){
    var head=g.name+" MCC";
    items.push({value:g.id,label:"All of "+g.name,avatar:inits(g.name),hue:hueOf(g.name),group:head,badge:MCC_BADGE,sub:g.accountIds.length+" accounts combined"});
    list.filter(function(a){return a.group===g.id;}).forEach(function(a){
      seen[a.id]=1; items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:head,sub:dashed(a.id)});
    });
  });
  list.filter(function(a){return !seen[a.id];}).forEach(function(a){
    items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:S.groups.length?"Other accounts":"",sub:dashed(a.id)});
  });
  if(!list.some(function(a){return a.id===S.account;})&&!groupOf(S.account)) S.account="";
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
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 15 to 30 seconds.":"",g?"pm-note":"");
  show("skel");
  getJson(API+"/report?account="+encodeURIComponent(S.account)+(refresh===true?"&refresh=1":"")).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg("");
    if(!r.ok){
      show(S.report?"out":"empty");
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j; S.currency=r.j.account.currency||"USD"; S.open={}; S.limit=25; S.filter=""; S.kind="all"; $("#filter").value="";
    S.sev=defaultSev(r.j.campaigns);
    show("out"); render();
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Which campaigns ──────────────────────────────────────── */
function worstOf(it){return it.severity||"none";}
function atLeast(it,level){ // a campaign is shown at a level when its worst gap is that bad or worse
  if(level==="none") return !it.severity;
  return !!it.severity&&SEV_RANK[it.severity]>=SEV_RANK[level];
}
function sevCount(items,level){return items.filter(function(i){return atLeast(i,level);}).length;}
function defaultSev(items){
  return sevCount(items,"high")?"high":sevCount(items,"medium")?"medium":sevCount(items,"low")?"low":"none";
}
function matches(it){
  if(!atLeast(it,S.sev)) return false;
  if(S.kind!=="all"&&it.type!==S.kind) return false;
  var t=S.filter.trim().toLowerCase(); if(!t) return true;
  var hay=[it.name,it.account,it.typeLabel].concat(it.gaps.map(function(g){return g.label+" "+g.scope;})).join(" ").toLowerCase();
  return hay.indexOf(t)>=0;
}

/* ── Rendering ────────────────────────────────────────────── */
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts combined</span>':"")+
    '&nbsp;· read from Google Ads '+esc(ago(rep.generatedAt))+' · impressions are the last '+rep.window.days+' days'+
    (rep.stale?' <span class="sg-mcc">Google Ads is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintCoverage(); paintSevs(); paintKinds(); paintList(); paintAccount();
  $("#foot").innerHTML=footNote(rep);
}
function footNote(rep){
  var na=(rep.notAudited||[]).map(function(n){return n.campaigns+" "+n.label;}).join(", ");
  return "Source: Google Ads, saved so the same report opens instantly. <b>Minimums are what Google needs for the format to show</b> (two sitelinks, two callouts, three headlines in a Performance Max asset group and so on). "+
    "<b>Targets are Position2's rule of thumb</b>, not Google's, and the coverage bars count a campaign as covered at its target. An asset counts as live when it is switched on and not disapproved, paused or past its end date; one under review counts because it will serve. "+
    "Where an ad group holds its own assets of a kind they override the campaign's, so a campaign shown as short may still be covered inside those ad groups (the row says how many). "+
    "For Display and Demand Gen the ad's own asset counts are read, and Google does not report approval for each asset there. Problems in account-wide assets are listed once under Account-wide assets, not under every campaign."+
    (na?" Not audited (Google manages or builds these): "+esc(na)+".":"")+" Nothing is changed in Google Ads: add the assets there.";
}
function paintKpis(){
  var s=S.report.summary, share=s.impressions?Math.round(s.impressionsFixFirst/s.impressions*100):0;
  var g=s.gaps, items=[
    ["Campaigns audited",int(s.campaigns),(s.withGaps?int(s.withGaps)+" have a gap":"no gaps")],
    ["Fix first",int(s.fixFirst),s.fixFirst?"warn":"good"],
    ["Impressions at stake",int(s.impressionsFixFirst),s.fixFirst?share+"% of all impressions, on fix-first campaigns":"nothing urgent"],
    ["Gaps to close",int(g.high+g.medium+g.low),g.high+" fix first · "+g.medium+" add · "+g.low+" nice to have"],
    ["Disapproved assets",int(s.disapprovedAssets),s.disapprovedAssets?"warn":"good"],
    ["Past their end date",int(s.expired),s.expired?"ended but still switched on":"none"]];
  $("#kpis").innerHTML=items.map(function(c){
    var cls=c[2]==="good"||c[2]==="warn"?c[2]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div>'+(cls?'':'<div class="s">'+esc(c[2])+'</div>')+'</div>';
  }).join("");
}
function paintCoverage(){
  var cov=S.report.summary.coverage||[];
  $("#cov").innerHTML=cov.length?cov.map(function(c){
    var n=c.campaigns||1, seg=function(k,v){return v?'<i class="'+k+'" style="flex:'+v+'" title="'+v+' '+k+'"></i>':"";};
    var bits=[c.atTarget+" of "+c.campaigns+" at target"];
    if(c.below) bits.push(c.below+" below");
    if(c.none) bits.push(c.none+" with none");
    if(c.unknown) bits.push(c.unknown+" not readable");
    return '<div class="ac-cov'+(c.optional?" opt":"")+'"><span class="ac-cov-l">'+esc(c.label)+(c.optional?' <em>optional</em>':"")+'</span>'+
      '<div class="ac-bar" aria-hidden="true">'+seg("ok",c.atTarget)+seg("below",c.below)+seg("none",c.none)+seg("unk",c.unknown)+'</div>'+
      '<span class="ac-cov-n">'+esc(bits.join(" · "))+'</span></div>';
  }).join(""):'<p class="pm-empty">No Search or Performance Max campaigns to cover.</p>';
}
function paintSevs(){
  var items=S.report.campaigns, defs=[["high","Fix first"],["medium","Fix first and add"],["low","Including nice to have"],["none","No gaps"]];
  $("#sevs").innerHTML=defs.map(function(d){
    return '<button type="button" role="radio" class="pm-chip'+(d[0]===S.sev?" on":"")+'" data-s="'+d[0]+'" aria-checked="'+(d[0]===S.sev)+'">'+d[1]+' <span class="n">'+sevCount(items,d[0])+'</span></button>';
  }).join("");
}
function paintKinds(){
  var items=S.report.campaigns, types={}, order=[];
  items.forEach(function(i){ if(!types[i.type]){types[i.type]={label:i.typeLabel,n:0}; order.push(i.type);} types[i.type].n++; });
  var defs=[["all","All",items.length]].concat(order.map(function(t){return [t,types[t].label,types[t].n];}));
  $("#kinds").innerHTML=defs.map(function(d){
    return '<button type="button" role="radio" class="pm-chip'+(d[0]===S.kind?" on":"")+'" data-k="'+esc(d[0])+'" aria-checked="'+(d[0]===S.kind)+'">'+esc(d[1])+' <span class="n">'+d[2]+'</span></button>';
  }).join("");
}
$("#sevs").addEventListener("click",function(e){var b=e.target.closest("button"); if(!b) return; S.sev=b.dataset.s; S.limit=25; paintSevs(); paintList();});
$("#kinds").addEventListener("click",function(e){var b=e.target.closest("button"); if(!b) return; S.kind=b.dataset.k; S.limit=25; paintKinds(); paintList();});
$("#filter").addEventListener("input",function(){S.filter=this.value; S.limit=25; paintList();});

function pill(sev){var d=SEV[sev]; return d?'<span class="ac-pill '+d[1]+'">'+d[0]+'</span>':'<span class="ac-pill none">No gaps</span>';}
function paintList(){
  var items=S.report.campaigns, rows=items.filter(matches), shown=rows.slice(0,S.limit);
  $("#listNote").textContent="Worst gap first, then the busiest campaign (last 30 days)";
  var narrowed=S.filter.trim()||S.kind!=="all";
  $("#list").innerHTML=shown.length?shown.map(row).join(""):'<p class="pm-empty">'+(!narrowed&&S.sev==="high"&&items.length?"No campaign has a gap that stops an asset showing.":"No campaign matches.")+'</p>';
  var more=$("#more"); more.hidden=rows.length<=S.limit; more.textContent="Show all "+rows.length+" campaigns";
}
$("#more").addEventListener("click",function(){S.limit=100000; paintList();});

function topGap(it){
  var g=it.gaps[0]; if(!g) return it.typeLabel==="Search"||it.typeLabel==="Performance Max"?"Every asset kind is at its target.":"Every asset is in place.";
  var more=it.gaps.length>1?" +"+(it.gaps.length-1)+" more":"";
  return g.label+": "+g.message+more;
}
function row(it){
  var open=!!S.open[it.key], multi=S.report.account.isGroup;
  return '<article class="ac-row'+(open?" open":"")+'" data-key="'+esc(it.key)+'">'+
    '<button type="button" class="ac-head" aria-expanded="'+open+'">'+pill(worstOf(it)==="none"?null:it.severity)+
      '<span class="ac-name"><b>'+esc(it.name||"(unnamed)")+'</b><span class="ac-sub">'+esc(it.typeLabel)+(multi?" · "+esc(it.account):"")+'</span></span>'+
      '<span class="ac-num"><b>'+int(it.impressions)+'</b> impressions<span>'+int(it.clicks)+' clicks</span></span>'+
      '<span class="ac-gap">'+esc(topGap(it))+'</span><span class="ac-chev" aria-hidden="true"></span></button>'+
    (open?body(it):"")+'</article>';
}
function noteBits(s){
  var bits=[];
  if(s.severity==="info"&&s.status!=="ok") bits.push('<span class="ac-t">optional</span>');
  if(s.disapproved) bits.push('<span class="ac-t bad">'+s.disapproved+' disapproved</span>');
  if(s.expired) bits.push('<span class="ac-t bad">'+s.expired+' past end date</span>');
  if(s.paused) bits.push('<span class="ac-t">'+s.paused+' paused</span>');
  if(s.pending) bits.push('<span class="ac-t">'+s.pending+' under review</span>');
  if(s.notShown) bits.push('<span class="ac-t">'+s.notShown+' not shown in 30 days</span>');
  if(s.adGroups) bits.push('<span class="ac-t">'+s.adGroups+' ad group'+(s.adGroups===1?"":"s")+' with their own</span>');
  return bits.join(" ");
}
function badNames(s){
  return (s.bad&&s.bad.length)?'<span class="ac-names">'+s.bad.map(function(b){return esc(b.label)+" ("+(b.state==="expired"?"past end date":"disapproved")+")";}).join(", ")+'</span>':"";
}
function slotTable(slots,fromCol){
  return '<div class="ac-txw"><table class="ac-tx"><thead><tr><th>Kind</th><th class="r">Live</th><th class="r">Target</th>'+(fromCol?'<th>From</th>':'')+'<th>Notes</th></tr></thead><tbody>'+
    slots.map(function(s){
      var have=s.have==null?"Na":s.have, cls=s.status==="ok"?"ok":s.status==="unknown"?"unk":(s.severity||"low");
      return '<tr class="'+cls+'"><td>'+esc(s.label)+'</td><td class="r c">'+have+'</td><td class="r c">'+(s.min&&s.min!==s.target?s.min+" to "+s.target:s.target)+'</td>'+
        (fromCol?'<td class="c">'+esc(SOURCE[s.source]||s.source)+'</td>':'')+'<td>'+noteBits(s)+badNames(s)+'</td></tr>';
    }).join("")+'</tbody></table></div>';
}
function gapList(gaps){
  return gaps.length?'<ul class="ac-gaps">'+gaps.map(function(g){
    var d=SEV[g.severity]||["",""];
    return '<li class="'+d[1]+'"><span class="ac-pill '+d[1]+'">'+d[0]+'</span><span class="ac-gl"><b>'+esc(g.label)+'</b> <span class="ac-sc">'+esc(g.scope)+'</span><br>'+esc(g.message)+'</span>'+
      (g.add?'<span class="ac-add">+'+g.add+'</span>':"")+'</li>';
  }).join("")+'</ul>':'<p class="pm-empty">Nothing to add.</p>';
}
function body(it){
  var out='<div class="ac-body"><h4>What to add</h4>'+gapList(it.gaps);
  if(it.slots.length) out+='<h4>Campaign assets</h4>'+slotTable(it.slots,true);
  if(it.groups.length) out+='<h4>Asset groups ('+it.groups.length+')</h4>'+it.groups.map(function(g){
    return '<details class="ac-det"><summary>'+pill(g.severity)+' <b>'+esc(g.name||g.id)+'</b></summary>'+slotTable(g.slots,true)+'</details>';}).join("");
  if(it.ads.length) out+='<h4>Ads ('+it.ads.length+')</h4>'+it.ads.map(function(a){
    return '<details class="ac-det"><summary>'+pill(a.severity)+' <b>'+esc(a.adGroup||"(ad group)")+'</b> <span class="ac-sub">'+esc(a.typeLabel)+'</span></summary>'+slotTable(a.slots,false)+'</details>';}).join("");
  return out+'</div>';
}
$("#list").addEventListener("click",function(e){
  var head=e.target.closest(".ac-head"); if(!head) return;
  var key=head.closest(".ac-row").dataset.key; S.open[key]=!S.open[key]; paintList();
});

function paintAccount(){
  var rows=S.report.accountAssets||[], any=rows.some(function(a){return a.slots.length;});
  $("#acctCard").hidden=!any;
  if(!any) return;
  var multi=rows.length>1;
  $("#acctAssets").innerHTML=rows.filter(function(a){return a.slots.length;}).map(function(a){
    return (multi?'<h4 class="ac-acct">'+esc(a.account)+'</h4>':"")+'<div class="ac-txw"><table class="ac-tx"><thead><tr><th>Kind</th><th class="r">Live</th><th>Notes</th></tr></thead><tbody>'+
      a.slots.map(function(s){return '<tr><td>'+esc(s.label)+'</td><td class="r c">'+s.have+'</td><td>'+noteBits(s)+badNames(s)+'</td></tr>';}).join("")+
      '</tbody></table></div>'+(a.locations?'<p class="ac-loc">'+a.locations+' location set'+(a.locations===1?"":"s")+' linked to the account.</p>':"");
  }).join("");
}

/* ── CSV and Refresh ──────────────────────────────────────── */
function csv(v){v=v==null?"":String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var lines=[["Account",rep.account.name],["Window",rep.window.from+" to "+rep.window.to],[],
    ["Account","Campaign","Kind of campaign","Where","Asset","Priority","Live now","Target","To add","What is wrong","Impressions (30 days)","Clicks","Cost"]];
  rep.campaigns.forEach(function(it){
    it.gaps.forEach(function(g){
      lines.push([it.account,it.name,it.typeLabel,g.scope,g.label,(SEV[g.severity]||[""])[0],g.have==null?"":g.have,g.target,g.add,g.message,it.impressions,it.clicks,it.cost]);
    });
  });
  var blob=new Blob(["﻿"+lines.map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="asset-coverage-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.window.to+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search), saved="";
  try{saved=localStorage.getItem("ac_account")||"";}catch(e){}
  S.account=p.get("account")||saved;
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  getJson("/p2/paid-marketing/api/segments/accounts").then(function(r){
    if(!r.ok){
      picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:20}],"",true);
      setMsg(esc((r.j&&r.j.error)||"The account list could not be loaded.")); return;
    }
    setAccounts(r.j.accounts||[],r.j.groups||[]); run();
  }).catch(function(){
    picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:20}],"",true);
    setMsg("The server could not be reached. Reload to try again.");
  });
})();
})();
