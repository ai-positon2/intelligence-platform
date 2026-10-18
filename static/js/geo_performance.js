/* Geo Performance (Paid Marketing Agents): performance by location.
 *
 * Pick a platform (Google | Meta | Both) and an account, a date range and how finely to cut the map (country, region, metro,
 * city, county, postal code). The server reads the platform live (tracker/paid_geo.py), judges every place against the
 * account's own average and suggests a geo bid adjustment where there is enough data (tracker/paid_geo_insights.py); this page
 * draws it: the numbers, what to do, a choice of charts, and a sortable table. It never works out a verdict or an adjustment for
 * itself: the server's are what is shown.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/geo";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}

var PL = window.PMPlatform;
var LEVELS = {
  google:[["country","Country"],["region","Region"],["metro","Metro area"],["city","City"],["county","County"],["postal_code","Postal code"]],
  meta:[["country","Country"],["region","Region"],["dma","Metro area (DMA)"]],
  both:[["country","Country"],["region","Region"]]};
var VERDICTS = {star:["Strong","star"],good:["Above average","good"],par:["On par","par"],below:["Below average","below"],weak:["Weak","weak"],
                leak:["Leaking spend","leak"],thin:["Too little data","thin"],none:["Not judged","thin"]};
var VCOLOR = {star:"#34d399",good:"#86efac",par:"#94a3b8",below:"#fbbf24",weak:"#f87171",leak:"#ef4444",thin:"#64748b",none:"#64748b"};
var S = {accounts:[], groups:[], metaAccounts:[], metaProblem:"", platform:"google", g:"", m:"", account:"", pickNote:"",
         preset:"30", from:"", to:"", level:"region", type:"All", report:null, seq:0, currency:"USD",
         chart:{kind:"bars", metric:"cost", top:15, order:"desc"}, tbl:{sort:{col:"cost",dir:-1}, filter:"", verdict:"all", limit:50}};

/* ── Number formats ───────────────────────────────────────── */
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
function pc(v,d){return v==null?"Na":(v*100).toFixed(d==null?1:d)+"%";}
function conv(v){return v==null?"Na":(Math.abs(v-Math.round(v))<0.05?Math.round(v).toLocaleString("en-US"):v.toLocaleString("en-US",{minimumFractionDigits:1,maximumFractionDigits:1}));}
function x2(v){return v==null?"Na":v.toFixed(2)+"x";}
function adjText(a){return a==null?"":a===0?"Hold":(a>0?"+":"")+Math.round(a*100)+"%";}
/* Meta has no bid adjustment by place: the same suggestion is a change in the share of the budget there (on Google, and on the two added, it is the bid). */
function budgetWords(){return !!(S.report&&S.report.account.platform==="meta");}
function platName(p){return p==="meta"?"Meta Ads":p==="both"?"Google Ads and Meta Ads":"Google Ads";}
function longDate(s){return new Date(s+"T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"});}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}

/* ── The metrics a chart or the table can show ────────────── */
var METRICS = {
  cost:{label:"Spend",get:function(r){return r.cost;},fmt:function(v){return money(v,0);},add:true},
  conversions:{label:"Conversions",get:function(r){return r.conversions;},fmt:conv,add:true},
  clicks:{label:"Clicks",get:function(r){return r.clicks;},fmt:int,add:true},
  impressions:{label:"Impressions",get:function(r){return r.impressions;},fmt:int,add:true},
  value:{label:"Conversion value",get:function(r){return r.value;},fmt:function(v){return money(v,0);},add:true,needs:"value"},
  cpa:{label:"Cost per conversion",get:function(r){return r.cpa;},fmt:function(v){return money(v,2);}},
  roas:{label:"Return on ad spend",get:function(r){return r.roas;},fmt:x2,needs:"value"},
  cvr:{label:"Conversion rate",get:function(r){return r.cvr;},fmt:function(v){return pc(v,2);}},
  ctr:{label:"CTR",get:function(r){return r.ctr;},fmt:function(v){return pc(v,2);}},
  cpc:{label:"CPC",get:function(r){return r.cpc;},fmt:function(v){return money(v,2);}},
  index:{label:"Efficiency index",get:function(r){return r.index;},fmt:function(v){return v==null?"Na":String(Math.round(v));}},
  pctCost:{label:"Share of spend",get:function(r){return r.pctCost;},fmt:function(v){return pc(v,1);}},
  pctConv:{label:"Share of conversions",get:function(r){return r.pctConv;},fmt:function(v){return pc(v,1);}}
};
var CHARTS = [
  {key:"bars",label:"Ranked bars",metrics:"all",help:"Each bar is a place, longest first. Green places beat the account's average, red ones trail it. Hover for the numbers."},
  {key:"bubble",label:"Spend vs results",metrics:"y",help:"Spend runs across and the chosen result runs up. Bigger bubbles got more clicks. Places high and left earn a lot for little; low and right cost more than they return."},
  {key:"share",label:"Share of spend vs conversions",metrics:null,help:"For each of the biggest spenders, its share of the spend beside its share of the conversions. A bar that is longer for conversions than for spend is earning more than its share."},
  {key:"donut",label:"Share of a total",metrics:"add",help:"How the chosen total splits between the biggest places, the rest grouped as Other."},
  {key:"scatter",label:"Efficiency vs spend",metrics:null,help:"Each dot is a place judged against the account's average (100). Above the line earns more than average. Dots far right spend the most, so they matter most."}
];

/* ── Dates ────────────────────────────────────────────────── */
function ymd(d){var z=d.getTimezoneOffset()*60000;return new Date(d-z).toISOString().slice(0,10);}
function rangeFor(p){
  var t=new Date(), y=new Date(t.getFullYear(),t.getMonth(),t.getDate()-1);
  if(p==="7"||p==="30"){var s=new Date(y);s.setDate(s.getDate()-(+p-1));return [ymd(s),ymd(y)];}
  if(p==="mtd") return [ymd(new Date(t.getFullYear(),t.getMonth(),1)),ymd(t)];
  if(p==="last") return [ymd(new Date(t.getFullYear(),t.getMonth()-1,1)),ymd(new Date(t.getFullYear(),t.getMonth(),0))];
  return [S.from,S.to];
}
var presetsEl=$("#presets");
function paintPresets(){
  var on=null;
  presetsEl.querySelectorAll("button").forEach(function(b){var y=b.dataset.p===S.preset;b.setAttribute("aria-checked",String(y));if(y)on=b;});
  var gl=presetsEl.querySelector(".cf-seg-glow");
  if(on){gl.style.width=on.offsetWidth+"px";gl.style.transform="translateX("+(on.offsetLeft-4)+"px)";}
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
$("#go").addEventListener("click",function(){run();});

/* ── Platform and accounts (the same pickers as Segment Analysis) ─── */
var picker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){
  S.g=v; if(S.platform==="google") S.m=""; if(S.platform==="both"&&v&&!S.m) autoMeta(); recompute();
}});
var mpicker = window.PMPicker($("#metaPick"),{label:"Meta ad account",search:true,onChange:function(v){
  S.m=v; if(S.platform==="meta") S.g=""; recompute();
}});
var switcher = PL.switcher($("#platPick"),{allow:["google","meta","both"],value:"google",onChange:function(p){
  S.platform=p; if(p!=="meta"&&!S.g&&S.m) autoGoogle(); if(p!=="google"&&!S.m&&S.g) autoMeta(); paintPickers(); recompute();
}});
function googleAcct(id){return S.accounts.filter(function(a){return a.id===id;})[0]||null;}
function metaAcct(id){return S.metaAccounts.filter(function(a){return a.id===id;})[0]||null;}
function autoMeta(){var g=googleAcct(S.g), c=g?PL.counterpart(g,S.metaAccounts):null; if(c) S.m=c.id;}
function autoGoogle(){var m=metaAcct(S.m), c=m?PL.counterpart(m,S.accounts):null; if(c) S.g=c.id;}
function chosenAccount(){
  var p=S.platform;
  if(p==="google") return S.g;
  if(p==="meta") return S.m;
  if(S.g&&S.m&&googleAcct(S.g)&&metaAcct(S.m)&&googleAcct(S.g).currency===metaAcct(S.m).currency) return S.g+"+"+S.m;
  return S.g||S.m;
}
function effPlatform(){return S.account?PL.platformOfId(S.account):S.platform;}
function recompute(){
  S.account=chosenAccount()||"";
  try{localStorage.setItem("gp_account",S.account);}catch(e){}
  var note="";
  if(S.platform==="both"&&S.g&&S.m&&googleAcct(S.g)&&metaAcct(S.m)&&googleAcct(S.g).currency!==metaAcct(S.m).currency)
    note="These two accounts bill in different currencies, so they cannot be added together. Showing the Google account.";
  if(S.platform!=="google"&&S.metaProblem) note=S.metaProblem+(S.platform==="both"?" Showing Google only.":"");
  S.pickNote=note;
  fixLevel(); paintLevels(); run();
}
function paintPickers(){
  $("#acctPick").hidden=S.platform==="meta"; $("#metaPick").hidden=S.platform==="google";
  var both=S.platform==="both";
  var items=[{value:"",label:both?"None":"Choose an account",sub:both?"Leave Google out":S.metaAccounts.length+" accounts",avatar:both?"–":"?",hue:200}];
  S.metaAccounts.forEach(function(a){items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),sub:a.id+" · "+a.currency});});
  if(!metaAcct(S.m)) S.m="";
  mpicker.set(items,S.m,!S.metaAccounts.length);
  setAccounts(S.accounts,S.groups);
}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function setAccounts(list, groups){
  S.accounts=list; S.groups=groups||[];
  var both=S.platform==="both";
  var items=[{value:"",label:both?"None":"Choose an account",sub:both?"Leave Meta out":list.length+" accounts"+(S.groups.length?" · "+S.groups.length+" MCCs to combine":""),avatar:both?"–":"?",hue:200}];
  var seen={};
  (both?[]:S.groups).forEach(function(g){
    var head=g.name+" MCC";
    items.push({value:g.id,label:"All of "+g.name,avatar:inits(g.name),hue:hueOf(g.name),group:head,badge:MCC_BADGE,sub:g.accountIds.length+" accounts combined · "+g.currency});
    list.filter(function(a){return a.group===g.id;}).forEach(function(a){
      seen[a.id]=1; items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:head,sub:dashed(a.id)+" · "+a.currency});
    });
  });
  list.filter(function(a){return !seen[a.id];}).forEach(function(a){
    items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:S.groups.length?"Other accounts":"",sub:dashed(a.id)+" · "+a.currency});
  });
  if(!list.some(function(a){return a.id===S.g;})&&!S.groups.some(function(g){return g.id===S.g;})) S.g="";
  picker.set(items,S.g);
}
function groupOf(id){return S.groups.filter(function(g){return g.id===id;})[0]||null;}

/* ── Location level ───────────────────────────────────────── */
function levelsFor(){return LEVELS[effPlatform()]||LEVELS.google;}
function fixLevel(){ if(!levelsFor().some(function(l){return l[0]===S.level;})) S.level="region"; }
function paintLevels(){
  $("#levels").innerHTML=levelsFor().map(function(l){
    return '<button type="button" role="radio" class="pm-chip'+(l[0]===S.level?" on":"")+'" data-l="'+l[0]+'" aria-checked="'+(l[0]===S.level)+'">'+esc(l[1])+'</button>';
  }).join("");
}
$("#levels").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b||b.dataset.l===S.level) return;
  S.level=b.dataset.l; try{localStorage.setItem("gp_level",S.level);}catch(x){}
  paintLevels(); run();
});

/* ── Loading the report ───────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}

function run(refresh){
  var force=refresh===true;
  if(!S.account){show("empty");setMsg(esc(S.pickNote||""),"pm-note");return;}
  if(!S.from||!S.to){setMsg("Pick a start and an end date.");return;}
  if(S.from>S.to){setMsg("The start date is after the end date.");return;}
  var seq=++S.seq, q="?account="+encodeURIComponent(S.account)+"&level="+S.level+"&from="+S.from+"&to="+S.to;
  try{history.replaceState(null,"",location.pathname+q+"&platform="+effPlatform());}catch(e){}
  var g=groupOf(S.account);
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 20 seconds.":"",g?"pm-note":"");
  show("skel");
  getJson(API+"/report"+q+(force?"&refresh=1":"")).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg("");
    if(!r.ok){
      show(S.report?"out":"empty");
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j; S.currency=r.j.account.currency||"USD";
    if(S.type!=="All"&&r.j.types.indexOf(S.type)<0) S.type="All";
    S.tbl.filter=""; S.tbl.verdict="all"; S.tbl.limit=50; $("#filter").value="";
    show("out"); render(); loadSummary(seq);
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
function view(){return S.report&&S.report.views[S.type];}
function render(){
  var rep=S.report, plat=platName(rep.account.platform);
  var warn=rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"";
  setMsg(warn||esc(S.pickNote||""),warn?"":"pm-note");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts combined</span>':"")+'&nbsp;· '+esc(longDate(rep.range.from))+
    ' to '+esc(longDate(rep.range.to))+' ('+rep.range.days+' day'+(rep.range.days===1?"":"s")+') · by '+esc(levelLabel(rep.level).toLowerCase())+' · read from '+plat+' '+
    esc(ago(rep.generatedAt))+(rep.stale?' <span class="sg-mcc">'+plat+' is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintTypes(); paintInsights(); paintChartControls(); drawChart(); paintVerdicts(); paintTable();
  $("#foot").innerHTML="Source: "+plat+", saved so the same report opens instantly. Places are where people <b>were</b> when they saw the ad ("+(budgetWords()?"Meta's region report":"Google's user location report")+"), "+
    "so a place can appear that you do not target. A place is judged against this account's own average: the <b>efficiency index</b> is the account's "+
    (view()&&view().basis==="roas"?"return on ad spend":"cost per conversion")+" compared with the place's, and 100 is average. A <b>suggested "+(budgetWords()?"budget change":"bid adjustment")+"</b> is 80% of the gap, in steps of 5%, "+
    "only with at least 5 conversions behind it: a starting point to apply gradually and check, not an instruction. Spend, conversions and conversion value are "+
    "as the platform reports them."+((rep.account.platform==="meta"||rep.account.platform==="both")?" On Meta, conversions are the campaign objective's results and the metro level is Meta's DMA; Meta has no city breakdown.":"")+
    (rep.account.isGroup?" This is an MCC view: the "+rep.account.accountCount+" accounts are read one by one and their rows added together.":"");
}
function levelLabel(k){var l=(S.report&&S.report.levels||[]).filter(function(x){return x.key===k;})[0]; return l?l.label:k;}
function paintKpis(){
  var v=view(); if(!v) return; var t=v.total;
  var items=[["Spend",money(t.cost,0),S.type==="All"?"all campaign types":S.type],["Conversions",conv(t.conversions),t.conversions?"good":"warn"],
             [v.basis==="roas"?"Return on ad spend":"Cost per conversion",v.basis==="roas"?x2(t.roas):money(t.cpa,2),"info"],
             ["Places",int(v.rows.length),levelLabel(S.report.level).toLowerCase()+" level"],
             ["Coverage",v.coverage==null?"Na":pc(v.coverage,0),"of this account's spend"]];
  $("#kpis").innerHTML=items.map(function(c){
    var cls=c[2]==="good"||c[2]==="warn"||c[2]==="info"?c[2]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div>'+(cls?'':'<div class="s">'+esc(c[2])+'</div>')+'</div>';
  }).join("");
}
function paintTypes(){
  var rep=S.report, chips=["All"].concat(rep.types), all=rep.views.All.total.cost||0;
  $("#types").innerHTML=chips.map(function(t){
    var v=rep.views[t], n=(t!=="All"&&v&&all)?' <span class="n">'+pc(v.total.cost/all,0)+'</span>':"";
    return '<button type="button" class="pm-chip'+(t===S.type?" on":"")+'" data-t="'+esc(t)+'" aria-pressed="'+(t===S.type)+'">'+esc(t)+n+'</button>';
  }).join("");
}
$("#types").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return;
  S.type=b.dataset.t; S.tbl.verdict="all"; S.tbl.limit=50; render(); loadSummary(S.seq);
});

/* Insights and the Claude briefing (the same cards as Segment Analysis) */
var GROUPS=[["action","Do this"],["watch","Keep an eye on"],["info","How far to trust this"]];
function paintInsights(){
  var items=(view()&&view().insights)||[], html="";
  $("#insNote").textContent=items.length?"Every one is computed from the table below":"";
  if(!items.length) html='<p class="pm-empty">Nothing stands out in this view.</p>';
  GROUPS.forEach(function(g){
    var list=items.filter(function(i){return i.severity===g[0];});
    if(!list.length) return;
    html+='<h3 class="sg-gh">'+g[1]+'</h3><div class="sg-cards">'+list.map(function(i){
      var link=i.keys&&i.keys.length?' data-key="'+esc(i.keys[0])+'" tabindex="0" role="link"':"";
      return '<article class="sg-card '+i.severity+(link?" link":"")+'"'+link+'>'+
        '<div class="sg-card-top"><span class="sg-tag">'+esc({push:budgetWords()?"More budget":"Raise bids",trim:budgetWords()?"Less budget":"Lower bids",exclude:"Exclude",concentration:"Concentration",thin:"Thin data",coverage:"Coverage",nodata:"No data"}[i.kind]||i.kind)+'</span>'+
        (i.severity==="info"?"":'<span class="sg-conf '+(i.confidence==="high"?"high":"")+'">'+(i.confidence==="high"?"High confidence":i.confidence==="medium"?"Medium confidence":"Directional")+'</span>')+'</div>'+
        '<h4>'+esc(i.headline)+'</h4><p class="ev">'+esc(i.evidence)+'</p>'+
        '<p class="ac"><b>'+(i.severity==="info"?"Note":"Do")+'</b> '+esc(i.action)+'</p></article>';
    }).join("")+'</div>';
  });
  $("#insList").innerHTML=html;
}
function goToPlace(key){
  var r=(view().rows||[]).filter(function(x){return x.key===key;})[0]; if(!r) return;
  S.tbl.filter=r.label; S.tbl.verdict="all"; $("#filter").value=r.label; paintVerdicts(); paintTable();
  var t=$("#tableCard"); t.scrollIntoView({behavior:"smooth",block:"start"});
}
$("#insList").addEventListener("click",function(e){var c=e.target.closest("[data-key]"); if(c) goToPlace(c.dataset.key);});
$("#insList").addEventListener("keydown",function(e){ if(e.key==="Enter"||e.key===" "){var c=e.target.closest("[data-key]"); if(c){e.preventDefault();c.click();}} });
function loadSummary(seq){
  var box=$("#sum");
  box.innerHTML='<div class="sg-sum-load"><span></span><span></span><span></span> Writing the summary…</div>';
  getJson(API+"/summary",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({account:S.account,level:S.level,from:S.from,to:S.to,type:S.type})}).then(function(r){
    if(seq!==S.seq) return;
    if(!r.ok||!r.j.summary){
      box.innerHTML='<p class="sg-sum-note">The summary could not be written'+(r.j&&r.j.error?" ("+esc(r.j.error)+")":"")+'. The findings below stand on their own.</p>'; return;
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

/* ── Charts ───────────────────────────────────────────────── */
var chart=null;
var reduceMotion=window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function light(){return document.documentElement.getAttribute("data-theme")==="light";}
function theme(){
  var cs=getComputedStyle($("#gp")), g=function(n,d){return (cs.getPropertyValue(n)||"").trim()||d;};
  return {ink:g("--pm-ink","#e8ecf8"),muted:g("--pm-muted","#8b94be"),line:g("--pm-line","rgba(255,255,255,.07)"),line2:g("--pm-line2","rgba(255,255,255,.12)"),
          panel:light()?"#ffffff":"#0c0f1c",accent:g("--pm-a","#fb923c"),accent2:g("--pm-a2","#f472b6")};
}
function vcol(v){return VCOLOR[v]||VCOLOR.thin;}
function hasValue(){var v=view(); return !!(v&&v.total&&v.total.value>0);}
function metricList(kind){
  var def=CHARTS.filter(function(c){return c.key===kind;})[0], v=view();
  var keys=Object.keys(METRICS).filter(function(k){
    var m=METRICS[k];
    if(m.needs==="value"&&!hasValue()) return false;
    if(def.metrics==="add") return !!m.add;
    if(def.metrics==="y") return ["conversions","cpa","roas","cvr","ctr","cpc","index","value","clicks"].indexOf(k)>=0;
    return true;
  });
  if(!v||!v.basis) keys=keys.filter(function(k){return k!=="index";});
  return keys;
}
function paintChartControls(){
  $("#chartKind").innerHTML=CHARTS.map(function(c){
    return '<button type="button" role="radio" class="pm-chip'+(c.key===S.chart.kind?" on":"")+'" data-k="'+c.key+'" aria-checked="'+(c.key===S.chart.kind)+'">'+esc(c.label)+'</button>';
  }).join("");
  var def=CHARTS.filter(function(c){return c.key===S.chart.kind;})[0], keys=metricList(S.chart.kind);
  $("#metricWrap").hidden=!def.metrics; $("#orderWrap").hidden=S.chart.kind!=="bars";
  if(keys.indexOf(S.chart.metric)<0) S.chart.metric=keys.indexOf("cost")>=0&&def.metrics!=="y"?"cost":keys[0];
  if(def.metrics==="y"&&S.chart.metric==="cost") S.chart.metric=keys.indexOf("conversions")>=0?"conversions":keys[0];
  $("#metricLab").textContent=def.metrics==="y"?"Up axis":def.metrics==="add"?"Total":"Metric";
  $("#metric").innerHTML=keys.map(function(k){return '<option value="'+k+'"'+(k===S.chart.metric?" selected":"")+'>'+esc(METRICS[k].label)+'</option>';}).join("");
  $("#topN").value=String(S.chart.top); $("#order").value=S.chart.order;
  $("#chartHelp").textContent=def.help;
}
$("#chartKind").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.chart.kind=b.dataset.k; paintChartControls(); drawChart();
});
$("#metric").addEventListener("change",function(){S.chart.metric=this.value; drawChart();});
$("#topN").addEventListener("change",function(){S.chart.top=+this.value; drawChart();});
$("#order").addEventListener("change",function(){S.chart.order=this.value; drawChart();});

function placeLabel(r){return r.label;}
function tipLines(r){
  var l=["Spend "+money(r.cost,0)+" ("+pc(r.pctCost,1)+" of spend)","Conversions "+conv(r.conversions)+" ("+pc(r.pctConv,1)+")",
         "CPA "+money(r.cpa,2)+(r.roas!=null?" · ROAS "+x2(r.roas):"")];
  if(r.index!=null) l.push("Efficiency index "+r.index);
  l.push(r.verdictLabel+(r.adjustment!=null?" · suggested "+adjText(r.adjustment):""));
  return l;
}
function drawChart(){
  var v=view(); if(!v) return;
  var cv=$("#chart"), th=theme(), kind=S.chart.kind, rows=v.rows.slice(), M=METRICS[S.chart.metric]||METRICS.cost;
  if(chart){chart.destroy();chart=null;}
  var empty=!rows.length;
  $("#chartNote").textContent=empty?"":(kind==="bars"?"top "+Math.min(S.chart.top,rows.length)+" by "+M.label.toLowerCase():"");
  $("#legend").innerHTML="";
  if(empty){ var c=cv.getContext("2d"); c.clearRect(0,0,cv.width,cv.height); return; }
  Chart.defaults.font.family="'Space Grotesk',system-ui,sans-serif"; Chart.defaults.font.size=11.5;
  Chart.defaults.animation.duration=reduceMotion?0:500; Chart.defaults.color=th.muted;
  var tip={backgroundColor:"rgba(9,12,24,.96)",borderColor:"rgba(255,255,255,.14)",borderWidth:1,padding:12,cornerRadius:12,titleColor:"#eef1fb",bodyColor:"#c4cbe4",
           titleFont:{weight:"700",size:12.5},bodyFont:{size:12},displayColors:false};
  var grid={color:th.line}, ticks={color:th.muted};
  var cfg;
  if(kind==="bars"){
    var pool=rows.filter(function(r){return M.get(r)!=null;});
    pool.sort(function(a,b){return S.chart.order==="asc"?M.get(a)-M.get(b):M.get(b)-M.get(a);});
    pool=pool.slice(0,S.chart.top);
    sizeFor(pool.length*30+70);
    cfg={type:"bar",data:{labels:pool.map(placeLabel),datasets:[{data:pool.map(M.get),backgroundColor:pool.map(function(r){return vcol(r.verdict)+"cc";}),
        borderColor:pool.map(function(r){return vcol(r.verdict);}),borderWidth:1,borderRadius:6}]},
      options:{indexAxis:"y",maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:Object.assign({},tip,{callbacks:{
        title:function(i){return pool[i[0].dataIndex].label+(pool[i[0].dataIndex].sub?" · "+pool[i[0].dataIndex].sub:"");},
        label:function(i){return M.label+": "+M.fmt(i.raw);},afterBody:function(i){return tipLines(pool[i[0].dataIndex]);}}})},
        scales:{x:{grid:grid,ticks:Object.assign({},ticks,{callback:function(v){return M.fmt(v);}})},y:{grid:{display:false},ticks:ticks}}}};
    legend(pool);
  }else if(kind==="bubble"){
    var Y=M, pool2=rows.filter(function(r){return Y.get(r)!=null&&r.cost>0;}).sort(function(a,b){return b.cost-a.cost;}).slice(0,S.chart.top*2);
    var maxClk=pool2.reduce(function(m,r){return Math.max(m,r.clicks);},1);
    var groups={}; pool2.forEach(function(r){(groups[r.verdict]=groups[r.verdict]||[]).push(r);});
    sizeFor(460);
    cfg={type:"bubble",data:{datasets:Object.keys(groups).map(function(k){
      return {label:VERDICTS[k][0],data:groups[k].map(function(r){return {x:r.cost,y:Y.get(r),r:4+14*Math.sqrt(r.clicks/maxClk),row:r};}),
        backgroundColor:vcol(k)+"88",borderColor:vcol(k),borderWidth:1.5};})},
      options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:Object.assign({},tip,{callbacks:{
        title:function(i){return i[0].raw.row.label;},label:function(i){return Y.label+": "+Y.fmt(i.raw.y);},afterBody:function(i){return tipLines(i[0].raw.row);}}})},
        scales:{x:{title:{display:true,text:"Spend",color:th.muted},grid:grid,ticks:Object.assign({},ticks,{callback:function(v){return money(v,0);}})},
                y:{title:{display:true,text:Y.label,color:th.muted},grid:grid,ticks:Object.assign({},ticks,{callback:function(v){return Y.fmt(v);}})}}}};
    legend(pool2);
  }else if(kind==="share"){
    var pool3=rows.slice().sort(function(a,b){return b.cost-a.cost;}).slice(0,S.chart.top);
    sizeFor(pool3.length*44+80);
    cfg={type:"bar",data:{labels:pool3.map(placeLabel),datasets:[
      {label:"Share of spend",data:pool3.map(function(r){return (r.pctCost||0)*100;}),backgroundColor:th.accent+"cc",borderRadius:5},
      {label:"Share of conversions",data:pool3.map(function(r){return (r.pctConv||0)*100;}),backgroundColor:th.accent2+"cc",borderRadius:5}]},
      options:{indexAxis:"y",maintainAspectRatio:false,plugins:{legend:{display:true,labels:{color:th.ink,usePointStyle:true,boxWidth:8}},tooltip:Object.assign({},tip,{callbacks:{
        title:function(i){return pool3[i[0].dataIndex].label;},label:function(i){return i.dataset.label+": "+i.raw.toFixed(1)+"%";},
        afterBody:function(i){return tipLines(pool3[i[0].dataIndex]);}}})},
        scales:{x:{grid:grid,ticks:Object.assign({},ticks,{callback:function(v){return v+"%";}})},y:{grid:{display:false},ticks:ticks}}}};
  }else if(kind==="donut"){
    var tot=rows.reduce(function(s,r){return s+(M.get(r)||0);},0)||1;
    var pool4=rows.filter(function(r){return (M.get(r)||0)>0;}).sort(function(a,b){return (M.get(b)||0)-(M.get(a)||0);}), top=pool4.slice(0,8), rest=pool4.slice(8);
    var data=top.map(M.get), labels=top.map(placeLabel);
    if(rest.length){data.push(rest.reduce(function(s,r){return s+(M.get(r)||0);},0)); labels.push("Other ("+rest.length+")");}
    var pal=["#fb923c","#f472b6","#a78bfa","#60a5fa","#34d399","#fbbf24","#22d3ee","#f87171","#64748b"];
    sizeFor(430);
    cfg={type:"doughnut",data:{labels:labels,datasets:[{data:data,backgroundColor:pal.slice(0,data.length).map(function(c){return c+"dd";}),borderColor:th.panel,borderWidth:2}]},
      options:{maintainAspectRatio:false,cutout:"58%",plugins:{legend:{position:"right",labels:{color:th.ink,usePointStyle:true,boxWidth:8}},tooltip:Object.assign({},tip,{callbacks:{
        label:function(i){return i.label+": "+M.fmt(i.raw)+" ("+(i.raw/tot*100).toFixed(1)+"%)";}}})}}};
  }else{                                              // scatter: efficiency against spend
    var pool5=rows.filter(function(r){return r.index!=null;});
    var groups5={}; pool5.forEach(function(r){(groups5[r.verdict]=groups5[r.verdict]||[]).push(r);});
    sizeFor(460);
    var avg={id:"gpAvg",afterDraw:function(ch){
      var y=ch.scales.y.getPixelForValue(100), a=ch.chartArea, c=ch.ctx; if(y<a.top||y>a.bottom) return;
      c.save(); c.strokeStyle=th.line2; c.setLineDash([5,5]); c.beginPath(); c.moveTo(a.left,y); c.lineTo(a.right,y); c.stroke();
      c.fillStyle=th.muted; c.fillText("account average",a.left+6,y-6); c.restore();}};
    cfg={type:"scatter",data:{datasets:Object.keys(groups5).map(function(k){
      return {label:VERDICTS[k][0],data:groups5[k].map(function(r){return {x:(r.pctCost||0)*100,y:r.index,row:r};}),
        backgroundColor:vcol(k)+"aa",borderColor:vcol(k),pointRadius:7,pointHoverRadius:9};})},
      plugins:[avg,{id:"gpNone",afterDraw:function(ch){
        if(pool5.length) return;
        var a=ch.chartArea, c=ch.ctx; c.save(); c.fillStyle=th.muted; c.textAlign="center"; c.font="600 14px 'Space Grotesk',sans-serif";
        c.fillText("No place here has enough conversions to be judged",(a.left+a.right)/2,(a.top+a.bottom)/2); c.restore();}}],
      options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:Object.assign({},tip,{callbacks:{
        title:function(i){return i[0].raw.row.label;},label:function(i){return "Index "+i.raw.y+" · "+i.raw.x.toFixed(1)+"% of spend";},afterBody:function(i){return tipLines(i[0].raw.row);}}})},
        scales:{x:{min:0,title:{display:true,text:"Share of spend",color:th.muted},grid:grid,ticks:Object.assign({},ticks,{callback:function(v){return v+"%";}})},
                y:{title:{display:true,text:"Efficiency index (100 = account average)",color:th.muted},grid:grid,ticks:ticks}}}};
    $("#chartNote").textContent=pool5.length?pool5.length+" places with enough data to judge":"No place has enough data to judge";
    legend(pool5);
  }
  chart=new Chart(cv,cfg);
}
function sizeFor(px){var h=Math.max(300,Math.min(1500,px)); $(".gp-chart").style.height=h+"px";}
function legend(pool){
  var seen={}; pool.forEach(function(r){seen[r.verdict]=1;});
  $("#legend").innerHTML=Object.keys(VERDICTS).filter(function(k){return seen[k];}).map(function(k){
    return '<span class="gp-lg"><i style="background:'+vcol(k)+'"></i>'+esc(VERDICTS[k][0])+'</span>';}).join("");
}
$("#pngBtn").addEventListener("click",function(){
  if(!chart) return;
  var src=$("#chart"), c=document.createElement("canvas"); c.width=src.width; c.height=src.height;
  var x=c.getContext("2d"); x.fillStyle=theme().panel; x.fillRect(0,0,c.width,c.height); x.drawImage(src,0,0);
  var a=document.createElement("a"); a.href=c.toDataURL("image/png");
  a.download="geo-"+S.chart.kind+"-"+S.report.level+"-"+S.report.range.from+"-"+S.report.range.to+".png";
  document.body.appendChild(a); a.click(); a.remove();
});
new MutationObserver(function(){ if(S.report) drawChart(); }).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});

/* ── The table ────────────────────────────────────────────── */
var COLS = [["label","Place"],["cost","Spend"],["pctCost","% spend"],["clicks","Clicks"],["ctr","CTR"],["cpc","CPC"],["conversions","Conv."],
            ["cvr","Conv. rate"],["cpa","CPA"],["roas","ROAS"],["index","Index"],["verdict","Verdict"],["adjustment","Suggested bid"]];
function colLabel(c){return c[0]==="adjustment"&&budgetWords()?"Suggested budget":c[1];}
function paintVerdicts(){
  var v=view(); if(!v) return;
  var counts={}; v.rows.forEach(function(r){counts[r.verdict]=(counts[r.verdict]||0)+1;});
  var chips=[["all","All",v.rows.length]].concat(Object.keys(VERDICTS).filter(function(k){return counts[k];}).map(function(k){return [k,VERDICTS[k][0],counts[k]];}));
  $("#verdicts").innerHTML=chips.map(function(c){
    return '<button type="button" class="pm-chip'+(S.tbl.verdict===c[0]?" on":"")+'" data-v="'+c[0]+'" aria-pressed="'+(S.tbl.verdict===c[0])+'">'+esc(c[1])+' <span class="n">'+c[2]+'</span></button>';
  }).join("");
}
$("#verdicts").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.tbl.verdict=b.dataset.v; S.tbl.limit=50; paintVerdicts(); paintTable();
});
$("#filter").addEventListener("input",function(){S.tbl.filter=this.value; S.tbl.limit=50; paintTable();});
function cellText(c,r){
  switch(c){
    case "cost": return money(r.cost,0); case "pctCost": return pc(r.pctCost,1); case "clicks": return int(r.clicks); case "ctr": return pc(r.ctr,2);
    case "cpc": return money(r.cpc,2); case "conversions": return conv(r.conversions); case "cvr": return pc(r.cvr,2); case "cpa": return money(r.cpa,2);
    case "roas": return x2(r.roas); case "index": return r.index==null?"":String(r.index);
  }
  return "";
}
function sortedRows(rows){
  var st=S.tbl.sort; if(!st) return rows;
  return rows.slice().sort(function(a,b){
    var x=a[st.col], y=b[st.col];
    if(x==null&&y==null) return 0; if(x==null) return 1; if(y==null) return -1;
    if(typeof x==="string") return st.dir*x.localeCompare(y);
    return st.dir*(x-y);
  });
}
function paintTable(){
  var v=view(); if(!v) return;
  var showRoas=hasValue(), term=S.tbl.filter.trim().toLowerCase();
  var rows=v.rows.filter(function(r){
    if(S.tbl.verdict!=="all"&&r.verdict!==S.tbl.verdict) return false;
    return !term||(r.label+" "+(r.sub||"")).toLowerCase().indexOf(term)>=0;
  });
  rows=sortedRows(rows);
  var cols=COLS.filter(function(c){return c[0]!=="roas"||showRoas;}), st=S.tbl.sort;
  $("#tableT").textContent="All "+levelLabel(S.report.level).toLowerCase()+" places";
  var cov=v.coverage;
  $("#covNote").textContent=cov==null?"":(cov>=0.995?"Covers all spend":"Covers "+pc(cov,0)+" of spend");
  var head=cols.map(function(c){
    var arw=st&&st.col===c[0]?'<span class="arw">'+(st.dir>0?"▲":"▼")+'</span>':"";
    return '<th class="sortable'+(c[0]!=="label"?" r":"")+'" data-col="'+c[0]+'">'+esc(colLabel(c))+arw+'</th>';
  }).join("");
  var maxShare=v.rows.reduce(function(m,r){return Math.max(m,r.pctCost||0);},0)||1;
  var shown=rows.slice(0,S.tbl.limit);
  var body=shown.map(function(r){
    var plats=r.platforms?Object.keys(r.platforms).map(function(p){return '<span class="gp-pl '+p+'">'+(p==="meta"?"Meta":"Google")+'</span>';}).join(""):"";
    return '<tr class="gp-r '+r.verdict+'"><td class="sg-lab">'+esc(r.label)+(r.sub?'<span class="gp-sub">'+esc(r.sub)+'</span>':"")+plats+'</td>'+
      cols.slice(1).map(function(c){
        if(c[0]==="pctCost") return '<td class="r sg-pc"><i style="width:'+Math.min(100,(r.pctCost||0)/maxShare*100).toFixed(1)+'%"></i><span>'+pc(r.pctCost,1)+'</span></td>';
        if(c[0]==="verdict") return '<td class="r"><span class="gp-v '+VERDICTS[r.verdict][1]+'" title="'+esc(r.why||"")+'">'+esc(r.verdictLabel)+'</span></td>';
        if(c[0]==="adjustment") return '<td class="r"><span class="gp-adj '+(r.adjustment>0?"up":r.adjustment<0?"down":"")+'" title="'+(r.confidence?esc(r.confidence)+" confidence":"")+'">'+esc(adjText(r.adjustment))+'</span></td>';
        return '<td class="r num">'+esc(cellText(c[0],r))+'</td>';
      }).join("")+'</tr>';
  }).join("");
  var t=v.total;
  var foot='<tr class="sg-total"><td>Total shown</td>'+cols.slice(1).map(function(c){
    if(c[0]==="verdict"||c[0]==="adjustment"||c[0]==="index") return '<td></td>';
    return '<td class="r num">'+esc(cellText(c[0],t))+'</td>';}).join("")+'</tr>';
  $("#tbl").innerHTML='<thead><tr>'+head+'</tr></thead><tbody>'+(body||'<tr><td colspan="'+cols.length+'" class="pm-empty">No place matches.</td></tr>')+'</tbody><tfoot>'+foot+'</tfoot>';
  var more=$("#more"); more.hidden=rows.length<=S.tbl.limit; more.textContent="Show all "+rows.length+" places";
}
$("#tbl").addEventListener("click",function(e){
  var th=e.target.closest("th[data-col]"); if(!th) return;
  var c=th.dataset.col, cur=S.tbl.sort;
  S.tbl.sort=cur&&cur.col===c?(cur.dir===-1?{col:c,dir:1}:null):{col:c,dir:c==="label"?1:-1};
  paintTable();
});
$("#more").addEventListener("click",function(){S.tbl.limit=100000; paintTable();});

/* ── CSV ──────────────────────────────────────────────────── */
function csv(v){v=v==null?"":String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report, v=view(); if(!rep||!v) return;
  var lines=[["Account",rep.account.name],["Platform",platName(rep.account.platform)],["Date range",rep.range.from+" to "+rep.range.to],["Level",levelLabel(rep.level)],["Campaign type",S.type],[],
    ["Place","Parent","Impressions","Clicks","Cost","% cost","CTR","CPC","Conversions","Conv. rate","CPA","Conversion value","ROAS","Efficiency index","Verdict",budgetWords()?"Suggested budget change":"Suggested bid adjustment","Confidence"]];
  v.rows.forEach(function(r){
    lines.push([r.label,r.sub,r.impressions,r.clicks,r.cost.toFixed(2),r.pctCost==null?"":(r.pctCost*100).toFixed(2)+"%",r.ctr==null?"":(r.ctr*100).toFixed(2)+"%",
      r.cpc==null?"":r.cpc.toFixed(2),r.conversions,r.cvr==null?"":(r.cvr*100).toFixed(2)+"%",r.cpa==null?"":r.cpa.toFixed(2),r.value,r.roas==null?"":r.roas.toFixed(3),
      r.index==null?"":r.index,r.verdictLabel,r.adjustment==null?"":Math.round(r.adjustment*100)+"%",r.confidence||""]);
  });
  var blob=new Blob(["﻿"+lines.map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="geo-"+rep.level+"-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.range.from+"-"+rep.range.to+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search);
  var fromUrl=p.get("account"), saved="";
  try{saved=localStorage.getItem("gp_account")||"";}catch(e){}
  var want=fromUrl||saved;
  var lv=p.get("level"), savedLv="";
  try{savedLv=localStorage.getItem("gp_level")||"";}catch(e){}
  S.level=lv||savedLv||"region";
  var pf=p.get("platform");
  if(!PL.validPlatform(pf)) pf=want?PL.platformOfId(want):(switcher.saved()||"google");
  S.platform=pf; switcher.set(pf);
  PL.partsOf(want||"").forEach(function(id){if(PL.isMeta(id)) S.m=id; else if(id) S.g=id;});
  if(p.get("from")&&p.get("to")){S.preset="custom";S.from=p.get("from");S.to=p.get("to");}
  else{var r=rangeFor(S.preset);S.from=r[0];S.to=r[1];}
  $("#from").value=S.from; $("#to").value=S.to; paintPresets();
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  mpicker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  var metaReq=getJson("/p2/paid-marketing/api/gads/accounts?platform=meta").then(function(r){
    if(r.ok) S.metaAccounts=(r.j.accounts||[]); else S.metaProblem=(r.j&&r.j.error)||"Meta Ads could not be read.";
  }).catch(function(){S.metaProblem="Meta Ads could not be reached.";});
  getJson("/p2/paid-marketing/api/segments/accounts").then(function(r){
    if(!r.ok){
      picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:20}],"",true);
      if(S.platform==="google") setMsg(esc((r.j&&r.j.error)||"The account list could not be loaded."));
      return metaReq;
    }
    S.accounts=r.j.accounts||[]; S.groups=r.j.groups||[];
    return metaReq;
  }).then(function(){
    if(S.platform!=="meta"&&!S.g&&S.m) autoGoogle();
    if(S.platform!=="google"&&!S.m&&S.g) autoMeta();
    if(S.platform==="meta"&&!S.m&&S.g) autoMeta();
    paintPickers(); recompute();
  }).catch(function(){
    picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:20}],"",true);
    setMsg("The server could not be reached. Reload to try again.");
  });
  paintLevels();
})();
})();
