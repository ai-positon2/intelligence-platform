/* Segment Analysis (Paid Marketing Agents).
 *
 * Two filters, an account and a date range, drive one request:
 *   GET  /p2/paid-marketing/api/segments/report?account=&from=&to=
 *        -> the seven segment tables (each cut by campaign type), the totals
 *           and the insights computed from them (tracker/paid_segments.py,
 *           tracker/paid_segment_insights.py), read live from Google Ads.
 *   POST /p2/paid-marketing/api/segments/summary {account, from, to}
 *        -> a short Claude-written briefing built from those insights only.
 *
 * The tables use the PPC team's sheet columns: Impressions, Clicks, Cost,
 * % Cost, CTR, CPC, Conversions, CPL ("Na" when there are no conversions).
 * A row an insight is about is marked in its table, and an insight links to
 * its table.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/segments";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}

var COLS = [["label",""],["impressions","Impressions"],["clicks","Clicks"],["cost","Cost"],["pctCost","% Cost"],
  ["ctr","CTR"],["cpc","CPC"],["conversions","Conversions"],["cpl","CPL"]];
var CITY_ROWS = 15;
var SEG_NAV = {account:"Account",day:"Day",hour:"Hour",age:"Age",gender:"Gender",city:"City",device:"Device",network:"Network"};

var S = {accounts:[], groups:[], account:"", preset:"30", from:"", to:"", type:"All", report:null,
         seq:0, sort:{}, open:{}, currency:"USD"};

/* ── Number formats (the sheet's) ─────────────────────────── */
var nfCache = {};
function money(v, dec){
  if(v==null) return "Na";
  var k=S.currency+dec;
  if(!nfCache[k]){
    try{nfCache[k]=new Intl.NumberFormat("en-US",{style:"currency",currency:S.currency,minimumFractionDigits:dec,maximumFractionDigits:dec});}
    catch(e){nfCache[k]={format:function(x){return "$"+x.toFixed(dec);}};}
  }
  return nfCache[k].format(v);
}
function int(v){return v==null?"Na":Math.round(v).toLocaleString("en-US");}
function pc2(v){return v==null?"Na":(v*100).toFixed(2)+"%";}
function conv(v){return v==null?"Na":(Math.abs(v-Math.round(v))<0.05?Math.round(v).toLocaleString("en-US"):v.toLocaleString("en-US",{minimumFractionDigits:1,maximumFractionDigits:1}));}
function share(v){return v==null?"":(v*100>=10?(v*100).toFixed(0):(v*100).toFixed(1))+"%";}
function cell(col, row, total){
  switch(col){
    case "impressions": return int(row.impressions);
    case "clicks": return int(row.clicks);
    case "cost": return money(row.cost,0);
    case "ctr": return pc2(row.ctr);
    case "cpc": return money(row.cpc,2);
    case "conversions": return conv(row.conversions);
    case "cpl": return money(row.cpl,2);
  }
  return "";
}

/* ── Dates ────────────────────────────────────────────────── */
function ymd(d){var z=d.getTimezoneOffset()*60000;return new Date(d-z).toISOString().slice(0,10);}
function rangeFor(p){
  var t=new Date(), y=new Date(t.getFullYear(),t.getMonth(),t.getDate()-1);
  if(p==="7"||p==="30"){var s=new Date(y);s.setDate(s.getDate()-(+p-1));return [ymd(s),ymd(y)];}
  if(p==="mtd") return [ymd(new Date(t.getFullYear(),t.getMonth(),1)),ymd(t)];
  if(p==="last") return [ymd(new Date(t.getFullYear(),t.getMonth()-1,1)),ymd(new Date(t.getFullYear(),t.getMonth(),0))];
  return [S.from,S.to];
}
function longDate(s){return new Date(s+"T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"});}

/* ── Filters ──────────────────────────────────────────────── */
var presetsEl=$("#presets");
function paintPresets(){
  var on=null;
  presetsEl.querySelectorAll("button").forEach(function(b){var y=b.dataset.p===S.preset;b.setAttribute("aria-checked",String(y));if(y)on=b;});
  var g=presetsEl.querySelector(".cf-seg-glow");
  if(on){g.style.width=on.offsetWidth+"px";g.style.transform="translateX("+(on.offsetLeft-4)+"px)";}
  $("#custom").hidden=S.preset!=="custom";
}
presetsEl.addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return;
  S.preset=b.dataset.p;
  if(S.preset!=="custom"){var r=rangeFor(S.preset);S.from=r[0];S.to=r[1];}
  $("#from").value=S.from;$("#to").value=S.to;
  paintPresets(); if(S.preset!=="custom") run();
});
window.addEventListener("resize",paintPresets);
$("#from").addEventListener("change",function(){S.from=this.value;});
$("#to").addEventListener("change",function(){S.to=this.value;});
$("#go").addEventListener("click",run);

var picker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){
  S.account=v; try{localStorage.setItem("sg_account",v)}catch(e){} run();
}});
/* An MCC with two or more accounts in one currency is offered whole ("All of OIA"),
   followed by its own accounts; everything else is listed under "Other accounts". */
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function setAccounts(list, groups){
  S.accounts=list; S.groups=groups||[];
  var items=[{value:"",label:"Choose an account",sub:list.length+" accounts"+(S.groups.length?" · "+S.groups.length+" MCCs to combine":""),avatar:"?",hue:200}];
  var seen={};
  S.groups.forEach(function(g){
    var head=g.name+" MCC";
    items.push({value:g.id,label:"All of "+g.name,avatar:inits(g.name),hue:hueOf(g.name),group:head,badge:MCC_BADGE,
      sub:g.accountIds.length+" accounts combined · "+g.currency});
    list.filter(function(a){return a.group===g.id;}).forEach(function(a){
      seen[a.id]=1;
      items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:head,sub:dashed(a.id)+" · "+a.currency});
    });
  });
  list.filter(function(a){return !seen[a.id];}).forEach(function(a){
    items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:S.groups.length?"Other accounts":"",
      sub:dashed(a.id)+" · "+a.currency});
  });
  if(!list.some(function(a){return a.id===S.account;})&&!S.groups.some(function(g){return g.id===S.account;})) S.account="";
  picker.set(items,S.account);
}
function groupOf(id){return S.groups.filter(function(g){return g.id===id;})[0]||null;}

/* ── Loading the report ───────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function show(which){
  $("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";
}

function run(){
  if(!S.account){show("empty");setMsg("");return;}
  if(!S.from||!S.to){setMsg("Pick a start and an end date.");return;}
  if(S.from>S.to){setMsg("The start date is after the end date.");return;}
  var seq=++S.seq, q="?account="+encodeURIComponent(S.account)+"&from="+S.from+"&to="+S.to;
  try{history.replaceState(null,"",location.pathname+q);}catch(e){}
  var g=groupOf(S.account);
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 20 seconds.":"",g?"pm-note":"");
  show("skel");
  getJson(API+"/report"+q).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg("");
    if(!r.ok){
      show(S.report?"out":"empty");
      var hint=r.j&&r.j.kind==="quota"?" The daily Google Ads allowance is shared, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j; S.currency=r.j.account.currency||"USD";
    if(r.j.types.indexOf(S.type)<0 && S.type!=="All") S.type="All";
    S.sort={}; S.open={};
    show("out"); render(); loadSummary(seq);
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
function marks(rep){
  // {segKey: {label: "best"|"bad"}} from the insights that are about table rows.
  var m={};
  (rep.insights||[]).forEach(function(i){
    var kind=i.kind==="best"?"best":(i.kind==="waste"||i.kind==="costly")?"bad":null;
    if(!kind||!SEG_NAV[i.dimension]) return;
    (m[i.dimension]=m[i.dimension]||{});
    i.labels.forEach(function(l){ if(!m[i.dimension][l]) m[i.dimension][l]=kind; });
  });
  return m;
}

function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · "))+" The totals below are short by what those accounts spent.":"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts combined</span>':"")+'&nbsp;· '+esc(longDate(rep.range.from))+
    ' to '+esc(longDate(rep.range.to))+' ('+rep.range.days+' day'+(rep.range.days===1?"":"s")+') · read from Google Ads '+
    esc(new Date(rep.generatedAt).toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"}));
  paintKpis(); paintTypes(); paintInsights(); paintTables();
  $("#foot").innerHTML="Source: Google Ads, read live and kept for up to 15 minutes. CPL is cost divided by conversions, and <b>Na</b> means "+
    "there were none. Google Ads does not report every segment for every campaign type (Performance Max has no age or gender, for "+
    "example), so each table shows its <b>coverage</b>: the share of the account's spend it accounts for. Do not compare totals across tables "+
    "that cover different amounts. Insights always use all campaign types."+
    (rep.account.isGroup?" This is an MCC view: the "+rep.account.accountCount+" accounts are read one by one and their rows added together, "+
    "and the Account table shows each one's share.":"");
}

function typeTotal(){
  var day=S.report.segments.filter(function(s){return s.key==="day"})[0];
  var v=day&&day.views&&day.views[S.type];
  return v?v.total:S.report.total;
}
function paintKpis(){
  var t=typeTotal();
  var items=[["Spend",money(t.cost,0),""],["Clicks",int(t.clicks),""],["CTR",pc2(t.ctr),""],["CPC",money(t.cpc,2),""],
             ["Conversions",conv(t.conversions),t.conversions?"good":"warn"],["Cost per conversion",money(t.cpl,2),t.conversions?"info":"warn"]];
  $("#kpis").innerHTML=items.map(function(c){
    return '<div class="pd-kpi '+c[2]+'"><div class="k">'+c[0]+'</div><div class="v">'+esc(c[1])+'</div>'+
      (c[0]==="Spend"?'<div class="s">'+(S.type==="All"?"all campaign types":esc(S.type))+'</div>':"")+'</div>';
  }).join("");
}

function paintTypes(){
  var rep=S.report, day=rep.segments.filter(function(s){return s.key==="day"})[0];
  var chips=['All'].concat(rep.types);
  $("#types").innerHTML=chips.map(function(t){
    var v=day&&day.views&&day.views[t], n=(t!=="All"&&v&&rep.total.cost)?' <span class="n">'+share(v.total.cost/rep.total.cost)+'</span>':"";
    return '<button type="button" class="pm-chip'+(t===S.type?" on":"")+'" data-t="'+esc(t)+'" aria-pressed="'+(t===S.type)+'">'+esc(t)+n+'</button>';
  }).join("");
  $("#jump").innerHTML=rep.segments.map(function(s){
    return '<a href="#seg-'+s.key+'">'+esc(SEG_NAV[s.key]||s.label)+'</a>';
  }).join("");
}
$("#types").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return;
  S.type=b.dataset.t; paintKpis(); paintTypes(); paintTables();
});

/* Insights */
var GROUPS=[["action","Do this"],["watch","Keep an eye on"],["info","How far to trust this"]];
function paintInsights(){
  var items=S.report.insights||[], html="";
  $("#insNote").textContent=items.length?"Every one is computed from the tables below":"";
  if(!items.length) html='<p class="pm-empty">Nothing stands out in this range.</p>';
  GROUPS.forEach(function(g){
    var list=items.filter(function(i){return i.severity===g[0];});
    if(!list.length) return;
    html+='<h3 class="sg-gh">'+g[1]+'</h3><div class="sg-cards">'+list.map(function(i){
      var link=SEG_NAV[i.dimension]?' data-go="seg-'+i.dimension+'" tabindex="0" role="link"':"";
      return '<article class="sg-card '+i.severity+(link?" link":"")+'" '+link.trim()+'>'+
        '<div class="sg-card-top"><span class="sg-tag">'+esc(i.dimensionLabel)+'</span>'+
        (i.severity==="info"?"":'<span class="sg-conf '+i.confidence+'">'+(i.confidence==="high"?"High confidence":"Directional")+'</span>')+'</div>'+
        '<h4>'+esc(i.headline)+'</h4><p class="ev">'+esc(i.evidence)+'</p>'+
        '<p class="ac"><b>'+(i.severity==="info"?"Note":"Do")+'</b> '+esc(i.action)+'</p></article>';
    }).join("")+'</div>';
  });
  $("#insList").innerHTML=html;
}
$("#insList").addEventListener("click",function(e){
  var c=e.target.closest("[data-go]"); if(!c) return;
  var t=document.getElementById(c.dataset.go); if(!t) return;
  t.scrollIntoView({behavior:"smooth",block:"start"}); t.classList.remove("sg-flash"); void t.offsetWidth; t.classList.add("sg-flash");
});
$("#insList").addEventListener("keydown",function(e){ if(e.key==="Enter"||e.key===" "){var c=e.target.closest("[data-go]"); if(c){e.preventDefault();c.click();}} });

function loadSummary(seq){
  var box=$("#sum");
  box.innerHTML='<div class="sg-sum-load"><span></span><span></span><span></span> Writing the summary…</div>';
  getJson(API+"/summary",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({account:S.account,from:S.from,to:S.to})}).then(function(r){
    if(seq!==S.seq) return;
    if(!r.ok||!r.j.summary){
      box.innerHTML='<p class="sg-sum-note">The summary could not be written'+(r.j&&r.j.error?" ("+esc(r.j.error)+")":"")+'. The findings below stand on their own.</p>';
      return;
    }
    var j=r.j;
    box.innerHTML='<p class="sg-sum-t">'+esc(j.summary)+'</p>'+
      (j.topActions&&j.topActions.length?'<ol class="sg-sum-a">'+j.topActions.map(function(a){return '<li>'+esc(a)+'</li>';}).join("")+'</ol>':"")+
      '<p class="sg-sum-note">'+(j.source==="model"?"Written by Claude from the findings below, using only their numbers.":
        "Summarised from the findings below. Claude could not be reached, so this is the rules' own wording.")+'</p>';
  }).catch(function(){
    if(seq!==S.seq) return;
    box.innerHTML='<p class="sg-sum-note">The summary could not be written. The findings below stand on their own.</p>';
  });
}

/* Tables */
function sortRows(rows,key){
  var st=S.sort[key]; if(!st) return rows;
  var c=st.col, d=st.dir;
  return rows.slice().sort(function(a,b){
    var x=a[c], y=b[c];
    if(x==null&&y==null) return 0; if(x==null) return 1; if(y==null) return -1;
    if(typeof x==="string") return d*x.localeCompare(y);
    return d*(x-y);
  });
}
function paintTables(){
  var rep=S.report, mk=marks(rep);
  $("#tables").innerHTML=rep.segments.map(function(seg){
    var head='<div class="pm-panel-h"><span class="pm-panel-t">'+esc(seg.label)+'</span>';
    var v=seg.views&&seg.views[S.type];
    if(seg.error) return '<section class="pm-panel sg-tbl" id="seg-'+seg.key+'">'+head+'</div><div class="pm-err">'+esc(seg.error)+'</div></section>';
    if(!v||!v.rows.length) return '<section class="pm-panel sg-tbl" id="seg-'+seg.key+'">'+head+'</div><p class="pm-empty">No '+esc(seg.label.toLowerCase())+
      ' data for '+(S.type==="All"?"this range":esc(S.type))+'.</p></section>';
    var cov=v.coverage, covHtml=cov==null?"":'<span class="sg-cov '+(cov<0.7?"low":"")+'" title="This table\'s spend as a share of the '+(S.type==="All"?"account":esc(S.type))+'\'s spend">'+
      (cov>=0.995?"Covers all spend":"Covers "+share(cov)+" of spend")+'</span>';
    var maxShare=v.rows.reduce(function(m,r){return Math.max(m,r.pctCost||0)},0)||1;
    var rows=sortRows(v.rows,seg.key), cap=seg.key==="city"&&!S.open[seg.key]&&rows.length>CITY_ROWS;
    var shown=cap?rows.slice(0,CITY_ROWS):rows, m=mk[seg.key]||{};
    var st=S.sort[seg.key];
    var th=COLS.map(function(c){
      var arw=st&&st.col===c[0]?'<span class="arw">'+(st.dir>0?"▲":"▼")+'</span>':"";
      return '<th class="sortable'+(c[0]!=="label"?" r":"")+'" data-col="'+c[0]+'" data-seg="'+seg.key+'">'+(c[1]||esc(seg.key==="city"?"City":seg.label))+arw+'</th>';
    }).join("");
    var body=shown.map(function(r){
      var k=m[r.label];
      return '<tr class="'+(k?"mk-"+k:"")+'"><td class="sg-lab">'+esc(r.label)+
        (k?'<span class="sg-mk '+k+'">'+(k==="best"?"Best":"Review")+'</span>':"")+'</td>'+
        COLS.slice(1).map(function(c){
          if(c[0]==="pctCost") return '<td class="r sg-pc"><i style="width:'+Math.min(100,(r.pctCost||0)/maxShare*100).toFixed(1)+'%"></i><span>'+pc2(r.pctCost)+'</span></td>';
          return '<td class="r num">'+esc(cell(c[0],r))+'</td>';
        }).join("")+'</tr>';
    }).join("");
    var t=v.total;
    var foot='<tr class="sg-total"><td>Grand Total</td>'+COLS.slice(1).map(function(c){
      if(c[0]==="pctCost") return '<td class="r num">'+pc2(t.pctCost)+'</td>';
      return '<td class="r num">'+esc(cell(c[0],t))+'</td>';
    }).join("")+'</tr>';
    return '<section class="pm-panel sg-tbl" id="seg-'+seg.key+'">'+head+covHtml+'</div>'+
      (seg.note?'<p class="pm-note warn">'+esc(seg.note)+'</p>':"")+
      '<div class="pm-scroll"><table><thead><tr>'+th+'</tr></thead><tbody>'+body+'</tbody><tfoot>'+foot+'</tfoot></table></div>'+
      (cap?'<button type="button" class="pm-btn-ghost sg-more" data-more="'+seg.key+'">Show all '+rows.length+' cities</button>':"")+'</section>';
  }).join("");
}
$("#tables").addEventListener("click",function(e){
  var th=e.target.closest("th[data-col]");
  if(th){
    var k=th.dataset.seg, c=th.dataset.col, cur=S.sort[k];
    S.sort[k]=cur&&cur.col===c?(cur.dir===-1?{col:c,dir:1}:null):{col:c,dir:c==="label"?1:-1};
    if(!S.sort[k]) delete S.sort[k];
    paintTables(); return;
  }
  var more=e.target.closest("[data-more]");
  if(more){S.open[more.dataset.more]=true;paintTables();}
});

/* ── CSV ──────────────────────────────────────────────────── */
function csv(v){v=v==null?"":String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var lines=[["Account",rep.account.name],["Date range",rep.range.from+" to "+rep.range.to],["Campaign type",S.type],[]];
  rep.segments.forEach(function(seg){
    var v=seg.views&&seg.views[S.type]; if(!v) return;
    lines.push([seg.label,"Impressions","Clicks","Cost","% Cost","CTR","CPC","Conversions","CPL"]);
    function row(label,r){return [label,r.impressions,r.clicks,r.cost.toFixed(2),r.pctCost==null?"":(r.pctCost*100).toFixed(2)+"%",
      r.ctr==null?"":(r.ctr*100).toFixed(2)+"%",r.cpc==null?"":r.cpc.toFixed(2),r.conversions,r.cpl==null?"Na":r.cpl.toFixed(2)];}
    v.rows.forEach(function(r){lines.push(row(r.label,r));});
    lines.push(row("Grand Total",v.total)); lines.push([]);
  });
  var blob=new Blob(["﻿"+lines.map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="segments-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.range.from+"-"+rep.range.to+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search);
  try{S.account=localStorage.getItem("sg_account")||"";}catch(e){}
  if(p.get("account")) S.account=p.get("account");
  if(p.get("from")&&p.get("to")){S.preset="custom";S.from=p.get("from");S.to=p.get("to");}
  else{var r=rangeFor(S.preset);S.from=r[0];S.to=r[1];}
  $("#from").value=S.from; $("#to").value=S.to; paintPresets();
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  getJson(API+"/accounts").then(function(r){
    if(!r.ok){
      picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:20}],"",true);
      setMsg(esc((r.j&&r.j.error)||"The account list could not be loaded."));
      return;
    }
    var list=r.j.accounts||[];
    var groups=r.j.groups||[], want=(p.get("client")||"").toLowerCase();
    if(want&&!p.get("account")){
      // A client that is a whole MCC opens all of its accounts together.
      var hit=groups.filter(function(g){return g.name.toLowerCase()===want;})[0]||
        list.filter(function(a){return a.name.toLowerCase().indexOf(want)>=0||want.indexOf(a.name.toLowerCase())>=0;})[0];
      if(hit) S.account=hit.id;
    }
    setAccounts(list,groups); run();
  }).catch(function(){
    picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:20}],"",true);
    setMsg("The server could not be reached. Reload to try again.");
  });
})();
})();
