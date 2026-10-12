/* Pacing Desk (Paid Marketing Agents).
 *
 * Ported from Agent Holmes' landing/pacing_desk.html. The engine's daily run
 * is pushed to /p2/paid-marketing/api/pacing/seed; this page reads it back,
 * lets anyone switch to an earlier day, and keeps a shared "applied" tick on
 * each recommended move (who ticked it is recorded server-side).
 *
 * Kept from the original on purpose (each was user feedback or a real bug):
 * client + account dropdowns rather than a search box; the top strip
 * recomputes for the current selection; the category table first with
 * campaigns collapsed under it; "Day (elapsed+1)" with parentheses; moves
 * keyed by (date, location, from, to) since category names repeat across
 * locations.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/pacing";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var m0 = function(v){return (v<0?"-$":"$")+Math.round(Math.abs(v)).toLocaleString("en-US")};
var pc = function(v,d){return (v*100).toFixed(d==null?0:d)+"%"};
var sum = function(arr,f){return arr.reduce(function(t,x){return t+f(x)},0)};
var slug = function(s){
  return String(s).toLowerCase().replace(/&/g,"and").replace(/[^a-z0-9]+/g,"-")
    .replace(/^-+|-+$/g,"").slice(0,120) || "x";
};

var PILL = {"WILL OVERSPEND":["over","Over"],"WILL UNDERSPEND":["under","Under"],
            "ON TARGET":["ok","On target"],"BUDGET UNCLEAR":["unclear","Budget unclear"]};
var HUE = {"WILL OVERSPEND":"h-over","WILL UNDERSPEND":"h-under","ON TARGET":"h-ok","BUDGET UNCLEAR":"h-unclear"};

var D = null, DERIVED = null, SOURCE = "live", LIVE_DATE = null, DATES = [], ROLLUPS = [], CURRENT_F = [];
var filter = "all", pickClient = "", pickAccount = "";
var campSort = {key:null, dir:1}, moveSort = {key:"amt", dir:-1};
var movesApplied = {}, pollTimer = null, openKeys = {};

function getJson(url){
  return fetch(url, {credentials:"same-origin"}).then(function(r){
    if(r.status===404) return null;
    return r.json().catch(function(){return {}}).then(function(j){
      if(!r.ok) throw new Error(j.error || (url+" answered "+r.status));
      return j;
    });
  });
}

function computeDerived(data){
  var A=[]; data.clients.forEach(function(c){c.accounts.forEach(function(a){a._client=c.name; A.push(a)})});
  return {A:A,
    over:A.filter(function(a){return a.landing==="WILL OVERSPEND"}),
    under:A.filter(function(a){return a.landing==="WILL UNDERSPEND"}),
    capped:A.filter(function(a){return a.capped}),
    amb:A.filter(function(a){return a.ambiguous})};
}

/* A move's id: the day, two short slugs and a hash of (location, from, to). */
function hashStr(s){var h=2166136261;for(var i=0;i<s.length;i++){h^=s.charCodeAt(i);h=(h*16777619)>>>0;}return h.toString(16);}
function moveId(date, loc, from, to){
  return date+"__"+slug(loc).slice(0,24)+"__"+slug(from).slice(0,24)+"__"+hashStr(loc+"|"+from+"|"+to);
}

function pollMoves(){
  if(pollTimer){clearInterval(pollTimer); pollTimer=null;}
  movesApplied = {};
  var date = D.asOf;
  function once(){
    getJson(API+"/moves?date="+encodeURIComponent(date)).then(function(j){
      if(!D || D.asOf!==date) return;
      var map={}; ((j&&j.moves)||[]).forEach(function(m){map[m.id]=m});
      var changed = JSON.stringify(map)!==JSON.stringify(movesApplied);
      movesApplied = map;
      if(changed) render();
    }).catch(function(e){console.warn("Pacing Desk: moves", e)});
  }
  once();
  pollTimer = setInterval(function(){ if(!document.hidden) once(); }, 15000);
}

/* ── CSV ─────────────────────────────────────────────────────── */
function csvCell(v){var s=String(v==null?"":v);if(/[",\n]/.test(s))s='"'+s.replace(/"/g,'""')+'"';return s;}
function downloadCsv(rows, filename){
  var blob=new Blob(["﻿"+rows.map(function(r){return r.map(csvCell).join(",")}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var url=URL.createObjectURL(blob), a=document.createElement("a");
  a.href=url; a.download=filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(function(){URL.revokeObjectURL(url)},1000);
}
function exportAccountsCsv(){
  var rows=[["Client","Account","Channels","Status","Capped","Budget unclear","Allocated","Spend",
             "Projected","Pacing %","Ideal %","Spend/day","Budget/day","Utilisation %","Moves"]];
  D.clients.forEach(function(c){ c.accounts.filter(keep).forEach(function(a){
    rows.push([c.name,a.name,a.channels||"Google",a.landing,a.capped?"Yes":"No",a.ambiguous?"Yes":"No",
      a.allocated,a.spend,a.ambiguous?"":a.projected,(a.pacing*100).toFixed(1),(a.ideal*100).toFixed(1),
      a.currentDaily,a.budgetDaily,(a.utilisation*100).toFixed(1),a.moves.length]);
  }); });
  downloadCsv(rows,"pacing-desk_"+D.asOf+"_accounts.csv");
}
function exportCampaignsCsv(key){
  var acct=findAccount(key); if(!acct) return;
  var rows=[["Location","Campaign","Type","Status","Spend","Conv","CPA","Daily cap","Used %","Search IS %","New daily","Pool"]];
  acct.campaigns.forEach(function(c){
    rows.push([c.loc||acct.name,c.name,c.type,c.status,c.spend,c.conv,c.cpa==null?"":c.cpa,c.budget,
      c.util==null?"":(c.util*100).toFixed(1),c.sis==null?"":(c.sis*100).toFixed(1),c.newDaily,c.pool||""]);
  });
  downloadCsv(rows,"pacing-desk_"+D.asOf+"_"+slug(acct.name)+"_campaigns.csv");
}
function findAccount(key){
  var p=key.split("||"), found=null;
  D.clients.forEach(function(c){ if(c.name===p[0]) c.accounts.forEach(function(a){ if(a.name===p[1]) found=a; }); });
  return found;
}

/* ── Hero ring: where the month stands ─────────────────────────── */
function paintRing(){
  var el=$("#ring"); el.hidden=false;
  var day=D.elapsed+1, frac=Math.min(1,D.elapsed/Math.max(1,D.daysInMonth));
  var r=46, C=2*Math.PI*r;
  el.innerHTML='<svg viewBox="0 0 108 108" aria-label="Day '+day+' of '+D.daysInMonth+'">'+
    '<defs><linearGradient id="pdRingGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fbbf24"/><stop offset="1" stop-color="#fb7185"/></linearGradient></defs>'+
    '<circle class="trk" cx="54" cy="54" r="'+r+'" fill="none" stroke-width="7"/>'+
    '<circle class="arc" cx="54" cy="54" r="'+r+'" fill="none" stroke-width="7" transform="rotate(-90 54 54)" '+
      'stroke-dasharray="'+C.toFixed(1)+'" stroke-dashoffset="'+C.toFixed(1)+'"/>'+
    '<text class="big" x="54" y="56" text-anchor="middle">'+day+'</text>'+
    '<text class="small" x="54" y="72" text-anchor="middle">of '+D.daysInMonth+'</text></svg>'+
    '<div class="pd-ring-meta"><span><b>'+esc(D.month)+'</b></span>'+
      '<span>Ideal pacing <b>'+pc(D.idealPacing,1)+'</b></span>'+
      '<span>Tolerance <b>+'+esc(D.tolerance)+'pp</b></span>'+
      '<span>Data through <b>'+esc(D.dataThrough)+'</b></span></div>';
  requestAnimationFrame(function(){requestAnimationFrame(function(){
    var a=el.querySelector(".arc"); if(a) a.setAttribute("stroke-dashoffset",(C*(1-frac)).toFixed(1));
  })});
}

function paintFresh(){
  var el=$("#fresh"), stale=SOURCE!=="live";
  el.className="pd-fresh"+(stale?" stale":"");
  var when = D.seededAt ? new Date(D.seededAt).toLocaleString([], {dateStyle:"medium", timeStyle:"short"}) : "";
  el.innerHTML='<span class="live"></span>'+(stale
    ? 'Viewing an earlier run, <b>'+esc(D.asOf)+'</b>. Pick the latest date in “As of” for today’s numbers.'
    : 'Live: the <b>'+esc(D.asOf)+'</b> run'+(when?', pushed '+esc(when):'')+'. Each morning’s run replaces this for everyone.');
}

function inScope(a){
  if(pickClient && a._client!==pickClient) return false;
  if(pickAccount && (a._client+"||"+a.name)!==pickAccount) return false;
  return true;
}

function paintStrip(){
  var A=DERIVED.A.filter(inScope), solid=A.filter(function(a){return !a.ambiguous});
  var tAll=sum(solid,function(a){return a.allocated}), tSp=sum(solid,function(a){return a.spend}),
      tPr=sum(solid,function(a){return a.projected}), tCv=sum(A,function(a){return a.conv}),
      tMv=sum(A,function(a){return a.moves.length});
  var capped=A.filter(function(a){return a.capped}),
      off=A.filter(function(a){return a.landing==="WILL OVERSPEND"||a.landing==="WILL UNDERSPEND"}).length;
  var atRisk=sum(capped,function(a){return a.allocated-a.projected});
  var nC={}; A.forEach(function(a){nC[a._client]=1}); nC=Object.keys(nC).length;
  var pl=function(n,w){return n+" "+w+(n===1?"":"s")};
  var cells=[
    ["","Allocated",m0(tAll),pl(nC,"client")+" · "+pl(A.length,"account"),null],
    ["","Spent to date",m0(tSp),tAll?pc(tSp/tAll,1)+" of budget":"–",tAll?tSp/tAll:null],
    [Math.abs((tPr/(tAll||1))-1)<=0.02?"good":"info","Projected month end",m0(tPr),tAll?pc(tPr/tAll)+" of budget":"–",tAll?tPr/tAll:null],
    ["","Conversions",Math.round(tCv).toLocaleString("en-US"),tCv?"CPA "+m0(tSp/tCv):"–",null],
    [off?"warn":"good","Moves suggested",String(tMv),pl(off,"account")+" off target",null],
    [atRisk>0?"warn":"good","At risk from caps",m0(atRisk),pl(capped.length,"account")+" can’t reach budget",null]
  ];
  $("#strip").innerHTML=cells.map(function(c){
    return '<div class="pd-kpi '+c[0]+'"><div class="k">'+c[1]+'</div><div class="v">'+c[2]+'</div>'+
      '<div class="s">'+esc(c[3])+'</div>'+(c[4]!=null?'<div class="mini"><i style="width:'+Math.min(100,c[4]*100).toFixed(1)+'%"></i></div>':'')+'</div>';
  }).join("");
  document.querySelectorAll("#strip .pd-kpi .v").forEach(countUp);
}

/* Numbers roll up to their value: "$801,327" counts from $0. */
function countUp(el){
  var txt=el.textContent, m=txt.match(/^(-?\$?)([\d,]+)(.*)$/);
  // A hidden tab pauses requestAnimationFrame: show the real figure then.
  if(!m||document.hidden||(window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches)) return;
  var target=parseInt(m[2].replace(/,/g,""),10), t0=null;
  if(!target) return;
  function step(ts){
    if(!t0) t0=ts;
    var p=Math.min(1,(ts-t0)/900); p=1-Math.pow(1-p,3);
    el.textContent=m[1]+Math.round(target*p).toLocaleString("en-US")+m[3];
    if(p<1) requestAnimationFrame(step); else el.textContent=txt;
  }
  requestAnimationFrame(step);
}

function paintTrend(){
  var el=$("#trend"), rolls=ROLLUPS.filter(function(r){return r.asOf && r.asOf.slice(0,7)===D.asOf.slice(0,7) && r.asOf<=D.asOf});
  if(!rolls.length){el.hidden=true;return;}
  el.hidden=false;
  var last=rolls[rolls.length-1], days=last.daysInMonth||30;
  var w=1080,h=190,pL=64,pR=20,pT=18,pB=30,iW=w-pL-pR,iH=h-pT-pB;
  var dayOf=function(r){return Number(r.asOf.slice(8,10))};
  var x=function(d){return pL+(d-1)/Math.max(1,days-1)*iW};
  var maxY=Math.max(last.tAll*1.08,(last.tPr||0)*1.04);
  rolls.forEach(function(r){if(r.tSp*1.06>maxY)maxY=r.tSp*1.06});
  var y=function(v){return pT+iH-(v/maxY)*iH};
  var s='<svg viewBox="0 0 '+w+' '+h+'" role="img" aria-label="Portfolio spend against plan">';
  s+='<defs><linearGradient id="pdArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fbbf24" stop-opacity=".28"/><stop offset="1" stop-color="#fbbf24" stop-opacity="0"/></linearGradient></defs>';
  [.25,.5,.75].forEach(function(f){var gy=y(last.tAll*f);s+='<line class="t-axis" x1="'+pL+'" x2="'+(w-pR)+'" y1="'+gy.toFixed(1)+'" y2="'+gy.toFixed(1)+'" stroke-dasharray="2 6" stroke-width="1"/>';});
  var yb=y(last.tAll);
  s+='<line class="t-budget" x1="'+pL+'" y1="'+yb.toFixed(1)+'" x2="'+(w-pR)+'" y2="'+yb.toFixed(1)+'" stroke-width="1.5"/>';
  s+='<text class="t-lbl hi" x="'+(w-pR)+'" y="'+(yb-7).toFixed(1)+'" text-anchor="end">'+m0(last.tAll)+' budget</text>';
  s+='<line class="t-ideal" x1="'+x(1).toFixed(1)+'" y1="'+y(0).toFixed(1)+'" x2="'+x(days).toFixed(1)+'" y2="'+yb.toFixed(1)+'" stroke-width="1.5" stroke-dasharray="4 5"/>';
  s+='<line class="t-axis" x1="'+pL+'" y1="'+(pT+iH)+'" x2="'+(w-pR)+'" y2="'+(pT+iH)+'" stroke-width="1"/>';
  [1,Math.round(days/2),days].forEach(function(d){s+='<text class="t-lbl" x="'+x(d).toFixed(1)+'" y="'+(h-8)+'" text-anchor="middle">Day '+d+'</text>'});
  s+='<text class="t-lbl" x="'+(pL-10)+'" y="'+(y(0)+4).toFixed(1)+'" text-anchor="end">$0</text>';
  var pts=rolls.map(function(r){return [x(dayOf(r)),y(r.tSp)]});
  if(pts.length>1){
    var line="M"+pts.map(function(p){return p[0].toFixed(1)+","+p[1].toFixed(1)}).join(" L");
    s+='<path d="'+line+' L'+pts[pts.length-1][0].toFixed(1)+','+(pT+iH)+' L'+pts[0][0].toFixed(1)+','+(pT+iH)+' Z" fill="url(#pdArea)"/>';
    s+='<path class="t-line" d="'+line+'" fill="none" stroke-width="2.5" stroke-linejoin="round"/>';
  }
  pts.forEach(function(p){s+='<circle class="t-dot" cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="3.2"/>'});
  var lp=pts[pts.length-1], px=x(days), py=y(last.tPr||last.tAll);
  s+='<line class="t-proj" x1="'+lp[0].toFixed(1)+'" y1="'+lp[1].toFixed(1)+'" x2="'+px.toFixed(1)+'" y2="'+py.toFixed(1)+'" stroke-width="2" stroke-dasharray="3 4"/>';
  s+='<circle class="t-pdot" cx="'+px.toFixed(1)+'" cy="'+py.toFixed(1)+'" r="4.5" stroke-width="2"/>';
  s+='<text class="t-lbl hi" x="'+(px-8).toFixed(1)+'" y="'+(py+(py<yb?-10:16)).toFixed(1)+'" text-anchor="end">projects '+m0(last.tPr)+'</text>';
  s+='</svg>';
  el.innerHTML='<div class="pm-panel-h"><span class="pm-panel-t">Spend against plan · '+esc(last.month||"")+'</span>'+
    '<span class="pm-panel-s">'+rolls.length+' of '+days+' days recorded</span></div>'+s+
    '<div class="pd-trend-legend"><span><i style="background:#fbbf24"></i>Spent to date</span>'+
    '<span><i style="background:var(--pm-faint)"></i>Ideal pacing</span>'+
    '<span><i style="background:#fb7185"></i>Projection to month end</span></div>'+
    (rolls.length<2?'<p class="pm-panel-s" style="margin-top:8px">The line fills in as each day’s run is pushed. One run recorded this month so far.</p>':'');
}

/* ── A searchable dropdown (same look as Search Query Analysis') ──
   items: [{value, label, sub, badge(html), avatar, hue, group}] */
function Picker(host, opts){
  var wrap=document.createElement("div"); wrap.className="sq-dd pd-dd";
  wrap.innerHTML='<span class="pd-dd-k">'+esc(opts.label)+'</span>'+
    '<button type="button" class="sq-dd-btn" aria-haspopup="listbox" aria-expanded="false" aria-label="'+esc(opts.label)+'"></button>'+
    '<div class="sq-dd-menu" hidden>'+(opts.search?'<input class="sq-dd-search" placeholder="Filter '+esc(opts.label.toLowerCase())+'s…" autocomplete="off">':'')+
    '<div role="listbox" class="pd-dd-list"></div></div>';
  host.appendChild(wrap);
  var btn=wrap.querySelector(".sq-dd-btn"), menu=wrap.querySelector(".sq-dd-menu"),
      list=wrap.querySelector(".pd-dd-list"), q=wrap.querySelector(".sq-dd-search");
  var items=[], value="", hl=0, shown=[], disabled=false;
  function av(it){
    if(it.icon) return '<span class="sq-av pd-av-ic" style="--h:'+(it.hue||40)+'">'+it.icon+'</span>';
    return '<span class="sq-av" style="--h:'+(it.hue==null?40:it.hue)+'">'+esc(it.avatar||"·")+'</span>';
  }
  function cur(){ return items.filter(function(i){return i.value===value})[0]||items[0]; }
  function paintBtn(){
    var it=cur(); if(!it){btn.innerHTML="";return;}
    btn.innerHTML=av(it)+'<span class="sq-dd-txt"><span class="nm">'+esc(it.label)+'</span><span class="ds">'+esc(it.sub||"")+'</span></span>'+
      (it.badge||"")+'<span class="sq-chev"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span>';
    btn.disabled=disabled;
  }
  function paintList(){
    var term=q?q.value.trim().toLowerCase():"";
    shown=items.filter(function(i){return !term||(i.label+" "+(i.sub||"")+" "+(i.group||"")).toLowerCase().indexOf(term)>=0});
    if(hl>=shown.length) hl=Math.max(0,shown.length-1);
    var html="", grp=null;
    shown.forEach(function(it,i){
      if(it.group&&it.group!==grp){grp=it.group; html+='<div class="pd-grp">'+esc(grp)+'</div>';}
      html+='<button type="button" role="option" class="sq-opt'+(i===hl?' hl':'')+'" data-i="'+i+'" aria-selected="'+(it.value===value)+
        '" style="animation-delay:'+Math.min(i*18,300)+'ms">'+av(it)+'<span class="sq-dd-txt"><span class="nm" style="font-size:13.5px">'+esc(it.label)+
        '</span><span class="ds">'+esc(it.sub||"")+'</span></span>'+(it.badge||"")+'<span class="tick">✓</span></button>';
    });
    list.innerHTML=html||'<div class="sq-none">Nothing matches “'+esc(term)+'”.</div>';
    var h=list.querySelector(".hl"); if(h) h.scrollIntoView({block:"nearest"});
  }
  function open(){
    if(disabled) return;
    document.querySelectorAll(".pd-dd.open").forEach(function(o){if(o!==wrap&&o.__close)o.__close()});
    wrap.classList.add("open"); menu.hidden=false; btn.setAttribute("aria-expanded","true");
    if(q) q.value="";
    hl=Math.max(0,items.map(function(i){return i.value}).indexOf(value)); paintList();
    setTimeout(function(){(q||btn).focus()},20);
  }
  function close(focus){ wrap.classList.remove("open"); menu.hidden=true; btn.setAttribute("aria-expanded","false"); if(focus) btn.focus(); }
  wrap.__close=close;
  function choose(it){
    close(true);
    if(it.value===value) return;
    value=it.value; paintBtn();
    btn.animate([{transform:"scale(.97)"},{transform:"scale(1.01)"},{transform:"none"}],{duration:300,easing:"ease-out"});
    opts.onChange(value);
  }
  btn.addEventListener("click",function(){menu.hidden?open():close()});
  btn.addEventListener("keydown",function(e){if(e.key==="ArrowDown"&&menu.hidden){e.preventDefault();open();}});
  list.addEventListener("click",function(e){var o=e.target.closest(".sq-opt"); if(o) choose(shown[+o.dataset.i]);});
  (q||menu).addEventListener("keydown",function(e){
    if(e.key==="ArrowDown"){e.preventDefault();hl=Math.min(shown.length-1,hl+1);paintList();}
    else if(e.key==="ArrowUp"){e.preventDefault();hl=Math.max(0,hl-1);paintList();}
    else if(e.key==="Enter"){e.preventDefault();if(shown[hl])choose(shown[hl]);}
    else if(e.key==="Escape"){e.preventDefault();close(true);}
  });
  if(q) q.addEventListener("input",function(){hl=0;paintList();});
  document.addEventListener("click",function(e){if(!wrap.contains(e.target)&&!menu.hidden)close();});
  return {
    set:function(newItems,newValue,isDisabled){items=newItems;value=newValue;disabled=!!isDisabled;paintBtn();if(!menu.hidden)paintList();},
    value:function(){return value;}
  };
}

var PK = {};
var STATUS_HUE = {"WILL OVERSPEND":24,"WILL UNDERSPEND":212,"ON TARGET":152,"BUDGET UNCLEAR":262};
function hueOf(name){var h=0;for(var i=0;i<name.length;i++)h=(h*31+name.charCodeAt(i))%360;return h;}
function inits(name){return name.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
var CAL='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>';
var ALL='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></svg>';

function buildPickers(){
  var el=$("#pickers"); el.hidden=false;
  el.innerHTML='<div class="pd-bar-grid" id="pickGrid"></div><button class="pd-clear" id="reset" hidden title="Clear the client, account and filter">'+
    '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>Clear</button>';
  var g=$("#pickGrid");
  PK.client=Picker(g,{label:"Client",search:true,onChange:function(v){pickClient=v;pickAccount="";fillAccounts();paintStrip();render();flashList();}});
  PK.account=Picker(g,{label:"Account",search:true,onChange:function(v){
    pickAccount=v;
    if(pickAccount){pickClient=pickAccount.split("||")[0];fillClients();}
    fillAccounts();paintStrip();render();
    if(pickAccount) focusAccount(pickAccount); else flashList();
  }});
  PK.asof=Picker(g,{label:"As of",search:false,onChange:function(v){switchDate(v)}});
  $("#reset").addEventListener("click",function(){pickClient="";pickAccount="";filter="all";buildControls();fillClients();fillAccounts();paintStrip();render();flashList();});
}

/* Picking an account takes you to it, open, with a glow. */
function focusAccount(key){
  var d=document.querySelector('.pd-acct[data-key="'+CSS.escape("acct:"+key)+'"]');
  if(!d) return;
  if(!d.open) d.open=true;
  setTimeout(function(){
    d.scrollIntoView({behavior:document.hidden?"auto":"smooth",block:"start"});
    d.classList.remove("pd-flash"); void d.offsetWidth; d.classList.add("pd-flash");
  },60);
}
function flashList(){ var l=$("#list"); l.classList.remove("pd-swap"); void l.offsetWidth; l.classList.add("pd-swap"); }

function buildControls(){
  var d=DERIVED;
  var F=[["all","All accounts",d.A.length,""],
    ["action","Needs action",d.over.length+d.under.length,""],
    ["over","Overspending",d.over.length,"var(--over)"],
    ["under","Underspending",d.under.length,"var(--under)"],
    ["capped","Can’t reach budget",d.capped.length,"var(--over)"],
    ["moves","Has moves",d.A.filter(function(a){return a.moves.length}).length,"var(--pm-a)"],
    ["unclear","Budget unclear",d.amb.length,"var(--unclear)"]].filter(function(f){return f[2]>0||f[0]==="all"});
  CURRENT_F=F;
  if(!F.some(function(f){return f[0]===filter})) filter="all";
  $("#controlsRow").hidden=false;
  $("#controls").innerHTML=F.map(function(f){
    return '<button class="pm-chip" data-f="'+f[0]+'" aria-pressed="'+(f[0]===filter)+'">'+
      (f[3]?'<span class="sw" style="background:'+f[3]+'"></span>':'')+esc(f[1])+'<span class="n">'+f[2]+'</span></button>';
  }).join("");
}

function offTarget(accts){return accts.filter(function(a){return a.landing==="WILL OVERSPEND"||a.landing==="WILL UNDERSPEND"}).length;}
function fillClients(){
  var items=[{value:"",label:"All clients",sub:D.clients.length+" clients · "+DERIVED.A.length+" accounts",icon:ALL,hue:36,
    badge:offTarget(DERIVED.A)?'<span class="badge pm-pill over">'+offTarget(DERIVED.A)+' off</span>':''}];
  D.clients.forEach(function(c){
    var off=offTarget(c.accounts);
    items.push({value:c.name,label:c.name,avatar:inits(c.name),hue:hueOf(c.name),
      sub:c.accounts.length+" account"+(c.accounts.length===1?"":"s")+" · "+m0(sum(c.accounts.filter(function(a){return !a.ambiguous}),function(a){return a.allocated}))+" budget",
      badge:off?'<span class="badge pm-pill over">'+off+' off</span>':'<span class="badge pm-pill ok">on track</span>'});
  });
  PK.client.set(items,pickClient);
}
function fillAccounts(){
  var pool=pickClient?D.clients.filter(function(c){return c.name===pickClient}):D.clients;
  var n=sum(pool,function(c){return c.accounts.length});
  var items=[{value:"",label:"All accounts",sub:n+" account"+(n===1?"":"s")+(pickClient?" at "+pickClient:""),icon:ALL,hue:36}];
  pool.forEach(function(c){
    c.accounts.slice().sort(function(x,y){return x.name.localeCompare(y.name)}).forEach(function(a){
      var p=PILL[a.landing]||["mute",a.landing];
      items.push({value:c.name+"||"+a.name,label:a.name,group:pickClient?null:c.name,avatar:inits(a.name),hue:STATUS_HUE[a.landing],
        sub:(a.ambiguous?"budget unconfirmed":"spent "+pc(a.pacing,0)+" · projects "+pc(a.projectedPct,0))+(a.moves.length?" · "+a.moves.length+" move"+(a.moves.length===1?"":"s"):""),
        badge:'<span class="badge pm-pill '+p[0]+'">'+p[1]+'</span>'});
    });
  });
  PK.account.set(items,pickAccount,n===0);
}
function fillAsOf(){
  var dates=DATES.length?DATES:[D.asOf];
  PK.asof.set(dates.map(function(dt){
    var d=new Date(dt+"T12:00:00");
    return {value:dt,label:d.toLocaleDateString("en-US",{weekday:"short",month:"short",day:"numeric"}),
      sub:dt===LIVE_DATE?"Latest run":dt,icon:CAL,hue:dt===LIVE_DATE?36:220,
      badge:dt===LIVE_DATE?'<span class="badge pm-pill ok">Live</span>':''};
  }),D.asOf,dates.length<2);
}

function switchDate(date){
  if(date===D.asOf) return;
  $("#list").classList.add("pd-loading");
  getJson(API+"/snapshots/"+encodeURIComponent(date)).then(function(snap){
    if(snap){ setD(snap, date===LIVE_DATE?"live":"history"); flashList(); }
  }).catch(function(e){showError("Could not load "+date+": "+e.message); fillAsOf();})
  .then(function(){$("#list").classList.remove("pd-loading")});
}

function crumb(){
  var b=$("#reset");
  if(!pickClient&&!pickAccount&&filter==="all"){$("#crumb").textContent="";b.hidden=true;return;}
  b.hidden=false;
  var parts=[pickAccount?"<b>"+esc(pickAccount.split("||")[1])+"</b>":pickClient?"<b>"+esc(pickClient)+"</b>":"All clients"];
  var f=CURRENT_F.filter(function(x){return x[0]===filter})[0];
  if(f&&filter!=="all") parts.push(esc(f[1]).toLowerCase());
  $("#crumb").innerHTML="Showing "+parts.join(" · ");
}

function keep(a){
  if(!inScope(a)) return false;
  switch(filter){
    case "action": return a.landing==="WILL OVERSPEND"||a.landing==="WILL UNDERSPEND";
    case "over": return a.landing==="WILL OVERSPEND";
    case "under": return a.landing==="WILL UNDERSPEND";
    case "capped": return a.capped;
    case "moves": return a.moves.length>0;
    case "unclear": return a.ambiguous;
  }
  return true;
}

/* The pacing bar: spend fill, ghost projection, dashed ideal tick, solid 100% line. */
function bar(a){
  var scale=Math.max(1,a.projectedPct,a.pacing)*1.04;
  var w=function(v){return Math.max(0,Math.min(100,(v/scale)*100))};
  return '<div><div class="pd-bar '+(HUE[a.landing]||"")+'">'+
    '<div class="proj" style="width:'+w(a.projectedPct)+'%"></div>'+
    '<div class="spent" style="width:'+w(a.pacing)+'%"></div>'+
    '<div class="ideal" style="left:'+w(a.ideal)+'%" title="Ideal pacing '+pc(a.ideal,1)+'"></div>'+
    '<div class="budget" style="left:'+w(1)+'%" title="100% of budget"></div></div>'+
    '<div class="pd-bar-l"><span>spent '+pc(a.pacing,1)+'</span><span class="dim">ideal '+pc(a.ideal,1)+'</span><span>proj '+pc(a.projectedPct)+'</span></div></div>';
}

function arw(state,key){return state.key===key?' <span class="arw">'+(state.dir>0?'▲':'▼')+'</span>':''}
function delta(active,d){
  if(!active||Math.abs(d)<1) return '<span class="dim">–</span>';
  return '<span class="'+(d>0?'pd-delta-up':'pd-delta-dn')+'">'+(d>0?'+':'')+m0(d)+'</span>';
}

function movesTable(a){
  if(!a.moves.length) return '<p class="pm-note">No campaign move clears the $5/day floor here: the account-level budget is the lever.</p>';
  var multi=a.moves.some(function(m){return m.loc&&m.loc!==a.name});
  var rows=a.moves.map(function(m){
    var loc=m.loc||a.name, id=moveId(D.asOf,loc,m.from,m.to), st=movesApplied[id];
    return {m:m,loc:loc,id:id,st:st&&st.applied?st:null};
  });
  rows.sort(function(x,y){return (x.m.amt-y.m.amt)*moveSort.dir});
  return '<div class="pm-scroll"><table><thead><tr>'+(multi?'<th>Location</th>':'')+
    '<th class="r sortable" data-msort="amt" tabindex="0">$/day'+arw(moveSort,"amt")+'</th><th>From</th><th></th><th>To</th><th>Why</th><th class="r">Applied</th></tr></thead><tbody>'+
    rows.map(function(r){var m=r.m;
      return '<tr class="'+(r.st?'pd-done':'')+'" data-move-id="'+esc(r.id)+'" data-loc="'+esc(r.loc)+'" data-from="'+esc(m.from)+'" data-to="'+esc(m.to)+'" data-amt="'+m.amt+'">'+
        (multi?'<td>'+esc(r.loc)+'</td>':'')+'<td class="r pd-amt">'+m0(m.amt)+'</td>'+
        '<td class="cn">'+esc(m.from)+'</td><td class="pd-arrow">→</td><td class="cn">'+esc(m.to)+'</td><td class="why">'+esc(m.why)+'</td>'+
        '<td class="r"><label class="pd-toggle" title="Mark this move applied for everyone"><input type="checkbox" aria-label="Mark move applied"'+(r.st?' checked':'')+'><i></i></label>'+
        (r.st&&r.st.appliedBy?'<span class="pd-by">'+esc(r.st.appliedBy.split("@")[0])+'</span>':'')+'</td></tr>';
    }).join("")+'</tbody></table></div>';
}

var CAMP_GET={name:function(r){return r.c.name.toLowerCase()},type:function(r){return (r.c.type||"").toLowerCase()},
  spend:function(r){return r.c.spend},conv:function(r){return r.c.conv||0},cpa:function(r){return r.c.cpa==null?Infinity:r.c.cpa},
  budget:function(r){return r.c.budget||0},util:function(r){return r.c.util||0},sis:function(r){return r.c.sis==null?-1:r.c.sis},
  delta:function(r){return r.delta}};

function campTable(a){
  var pools={}; a.campaigns.forEach(function(c){if(c.pool)pools[c.pool]=1});
  var multi=a.campaigns.some(function(c){return c.loc&&c.loc!==a.name});
  var rows=a.campaigns.map(function(c){return {c:c,delta:c.newDaily-c.budget}});
  if(campSort.key&&CAMP_GET[campSort.key]){var g=CAMP_GET[campSort.key];
    rows.sort(function(x,y){var vx=g(x),vy=g(y);return (typeof vx==="string"?vx.localeCompare(vy):vx-vy)*campSort.dir});}
  var th=function(k,l,r){return '<th class="sortable'+(r?' r':'')+'" data-sort="'+k+'" tabindex="0">'+esc(l)+arw(campSort,k)+'</th>'};
  return (Object.keys(pools).length?'<p class="pm-note">Shared budget pools: <b>'+Object.keys(pools).map(esc).join(", ")+
      '</b>. Campaigns on one pool cannot trade budget with each other, and the pool counts once in the account total.</p>':'')+
    '<div class="pd-h4"><span>Campaigns ('+a.campaigns.length+')</span><button class="pm-btn-ghost" data-camp-csv="'+esc(a._client)+'||'+esc(a.name)+'" type="button">'+
      '<svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>CSV</button></div>'+
    '<div class="pm-scroll"><table><thead><tr>'+(multi?'<th>Location</th>':'')+th("name","Campaign")+th("type","Type")+th("spend","Spend",1)+
      th("conv","Conv",1)+th("cpa","CPA",1)+th("budget","Daily cap",1)+th("util","Used",1)+th("sis","Search IS",1)+th("delta","Change",1)+
    '</tr></thead><tbody>'+rows.map(function(r){var c=r.c;
      return '<tr>'+(multi?'<td>'+esc(c.loc)+'</td>':'')+'<td class="cn">'+esc(c.name)+
        (c.pool?' <span class="pm-tag" title="Shared budget pool">pool: '+esc(c.pool)+'</span>':'')+
        (c.flags||[]).map(function(f){return '<span class="pd-flag">'+esc(f)+'</span>'}).join("")+'</td>'+
        '<td><span class="pm-tag">'+esc(c.type)+'</span></td><td class="r num">'+m0(c.spend)+'</td><td class="r num">'+(c.conv||0)+'</td>'+
        '<td class="r num">'+(c.cpa!=null?m0(c.cpa):'<span class="dim">–</span>')+'</td><td class="r num">'+(c.budget?m0(c.budget):'<span class="dim">–</span>')+'</td>'+
        '<td class="r num">'+(c.util?pc(c.util):'<span class="dim">–</span>')+'</td><td class="r num">'+(c.sis!=null?pc(c.sis):'<span class="dim">–</span>')+'</td>'+
        '<td class="r num">'+delta(c.active,r.delta)+'</td></tr>'}).join("")+'</tbody></table></div>';
}

function categoryTable(a){
  var cats=a.categories||[];
  if(!cats.length) return '<p class="pm-empty">No category rollup for this account.</p>';
  var multi=cats.some(function(c){return c.loc&&c.loc!==a.name});
  return '<div class="pm-scroll"><table><thead><tr>'+(multi?'<th>Location</th>':'')+
    '<th>Category</th><th class="r">Spend</th><th class="r">Conv</th><th class="r">CPL</th><th class="r">Daily cap</th><th class="r">Used</th><th class="r">Change</th></tr></thead><tbody>'+
    cats.map(function(c){
      return '<tr>'+(multi?'<td>'+esc(c.loc)+'</td>':'')+'<td class="cn">'+esc(c.name)+
        (c.pooled?' <span class="pm-tag" title="Shared budget pool, '+(c.members||[]).length+' campaigns">pool</span>':'')+'</td>'+
        '<td class="r num">'+m0(c.spend)+'</td><td class="r num">'+(c.conv||0)+'</td>'+
        '<td class="r num">'+(c.cpl!=null?m0(c.cpl):'<span class="dim">–</span>')+'</td><td class="r num">'+(c.budget?m0(c.budget):'<span class="dim">–</span>')+'</td>'+
        '<td class="r num">'+(c.util?pc(c.util):'<span class="dim">–</span>')+'</td><td class="r num">'+delta(c.active,c.newDaily-c.budget)+'</td></tr>';
    }).join("")+'</tbody></table></div>';
}

function detail(a){
  var k=[["Allocated",m0(a.allocated)],["Spent",m0(a.spend)]];
  if(!a.ambiguous){k.push(["Projected",m0(a.projected)]);k.push([a.gap<0?"Short by":"Over by",m0(Math.abs(a.gap))]);}
  k.push(["Spend / day",m0(a.currentDaily)]);
  if(!a.ambiguous) k.push(["Needs / day",m0(a.requiredDaily)]);
  k.push(["Budget / day",m0(a.budgetDaily)],["Used",pc(a.utilisation)],["Active days left",String(a.daysLeft)],
         ["Conversions",String(a.conv)],["CPA",a.cpa!=null?m0(a.cpa):"–"]);
  if(a.targetCpa) k.push(["Target CPA",m0(a.targetCpa)]);
  if(a.roas!=null) k.push(["ROAS",a.roas.toFixed(2)+"x"]);
  if(a.convValue) k.push(["Conv. value",m0(a.convValue)]);
  k.push(["Runs",esc(a.schedule)],["Channels",esc(a.channels||"Google")]);
  var notes=(a.notes||[]).map(function(n){
    var warn=/looks wrong|Cannot reach|not spending at all|Guardrails/i.test(n);
    return '<p class="pm-note'+(warn?' warn':'')+'">'+esc(n).replace(/\*(.+?)\*/g,"<b>$1</b>")+'</p>';
  }).join("");
  var done=a.moves.filter(function(m){var st=movesApplied[moveId(D.asOf,m.loc||a.name,m.from,m.to)];return st&&st.applied}).length;
  return '<div class="pd-body"><div class="pd-kgrid">'+k.map(function(x){return '<div><div class="k">'+x[0]+'</div><div class="v">'+x[1]+'</div></div>'}).join("")+'</div>'+notes+
    '<div class="pd-h4"><span>Recommended moves'+(a.moves.length?' · <span class="prog">'+done+'/'+a.moves.length+' applied</span>':'')+'</span></div>'+movesTable(a)+
    '<div class="pd-h4"><span>Categories ('+(a.categories?a.categories.length:0)+')</span></div>'+categoryTable(a)+
    '<details class="pd-camps"'+(openKeys["camps:"+a._client+"||"+a.name]?' open':'')+' data-key="camps:'+esc(a._client)+'||'+esc(a.name)+'"><summary><span class="caret"></span>Campaign detail</summary>'+campTable(a)+'</details></div>';
}

function render(){
  if(!D) return;
  // Re-rendering must not snap open rows shut (moves poll every 15s).
  document.querySelectorAll("#list details[data-key]").forEach(function(d){openKeys[d.dataset.key]=d.open});
  var html="";
  D.clients.forEach(function(c){
    var accts=c.accounts.filter(keep); if(!accts.length) return;
    var s=accts.filter(function(a){return !a.ambiguous});
    var al=sum(s,function(a){return a.allocated}), sp=sum(s,function(a){return a.spend}), pr=sum(s,function(a){return a.projected});
    var need=accts.filter(function(a){return a.landing!=="ON TARGET"}).length, mv=sum(accts,function(a){return a.moves.length});
    var ck="client:"+c.name, cOpen=openKeys[ck]===undefined?true:openKeys[ck];
    html+='<details class="pd-client"'+(cOpen?' open':'')+' data-key="'+esc(ck)+'"><summary><span class="caret"></span><span class="pd-cname">'+esc(c.name)+'</span>'+
      '<span class="pd-csum"><span>'+accts.length+' account'+(accts.length===1?'':'s')+(need?' · <span class="off">'+need+' off target</span>':'')+(mv?' · '+mv+' move'+(mv===1?'':'s'):'')+'</span>'+
      (al?'<span class="num">'+m0(sp)+' / '+m0(al)+'</span><span class="pd-cbar" title="Spent of budget"><i style="width:'+Math.min(100,sp/al*100).toFixed(1)+'%"></i></span>'+
          '<span>projects <span class="num">'+pc(pr/al)+'</span>'+(s.length<accts.length?' <span class="dim">(excl. '+(accts.length-s.length)+' unclear)</span>':'')+'</span>'
         :'<span class="dim">budget figures unconfirmed</span>')+'</span></summary>';
    accts.forEach(function(a){
      var p=PILL[a.landing]||["mute",a.landing], key="acct:"+a._client+"||"+a.name;
      var open=(pickAccount&&(a._client+"||"+a.name)===pickAccount)||openKeys[key];
      var allDone=a.moves.length&&a.moves.every(function(m){var st=movesApplied[moveId(D.asOf,m.loc||a.name,m.from,m.to)];return st&&st.applied});
      html+='<details class="pd-acct"'+(open?' open':'')+' data-key="'+esc(key)+'"><summary><span class="pd-aname"><span class="caret"></span><span class="t">'+esc(a.name)+'</span>'+
        '<span class="pm-pill '+p[0]+'">'+p[1]+'</span>'+
        (a.channels&&a.channels!=="Google"?'<span class="pm-tag" title="Channels in the Budget Tracker; this desk tracks Google spend only">'+esc(a.channels)+'</span>':'')+
        (a.capped?'<span class="pm-tag warn" title="Daily caps cannot reach the monthly budget">capped</span>':'')+
        (a.moves.length?(allDone?'<span class="pm-tag good">'+a.moves.length+' applied</span>':'<span class="pm-tag">'+a.moves.length+' move'+(a.moves.length===1?'':'s')+'</span>'):'')+'</span>'+
        (a.ambiguous?'<span class="pd-fig dim">budget figure unconfirmed</span>':bar(a))+
        '<span class="pd-fig"><span class="big">'+m0(a.spend)+'</span>of '+m0(a.allocated)+'</span>'+
        '<span class="pd-fig"><span class="big">'+m0(a.currentDaily)+'/day</span>'+(a.ambiguous?'spend rate':'needs '+m0(a.requiredDaily))+'</span>'+
        '</summary>'+(open?detail(a):'')+'</details>';
    });
    html+='</details>';
  });
  $("#list").innerHTML=html||'<p class="pm-empty">No account matches that combination. Try clearing the filter.</p>';
  crumb();
}

function paintOrphans(){
  var b=[];
  if(D.portfolioWarnings&&D.portfolioWarnings.length){
    var tot=D.portfolioWarnings.reduce(function(t,w){var m=w.match(/\$([\d,]+(?:\.\d+)?)/);return t+(m?parseFloat(m[1].replace(/,/g,"")):0)},0);
    b.push('<div class="pd-h4"><span>Spending outside the Budget Tracker</span></div><div class="pd-client"><p class="pm-note warn"><b>'+m0(tot)+
      '</b> spent month to date by accounts with no Google budget row. They are left out of every figure above.</p>'+
      D.portfolioWarnings.map(function(w){return '<p class="pm-note">'+esc(w)+'</p>'}).join("")+'</div>');
  }
  if(D.otherChannels&&D.otherChannels.length){
    b.push('<div class="pd-h4"><span>Other channels this desk doesn’t cover</span></div><div class="pd-client"><p class="pm-note">'+
      'This desk reads the Google Ads export only. The Budget Tracker also allocates these, which no figure here includes:</p>'+
      D.otherChannels.map(function(o){return '<p class="pm-note"><b>'+esc(o.client)+'</b>, '+esc(o.account)+' on <b>'+esc(o.channel)+'</b>: '+m0(o.allocated)+' allocated</p>'}).join("")+'</div>');
  }
  $("#orphans").innerHTML=b.join("");
}

function paintFoot(){
  $("#foot").textContent='Ideal pacing is (today − 1) ÷ days in month, with a +'+D.tolerance+'pp tolerance. '+
    'The month-end projection comes from actual spend per active day, never from the daily cap, because most campaigns never spend their full cap. '+
    'Budget moves stay inside a single location and net to zero. Daily caps, status and impression share come from the Google Ads export; '+
    'allocations from the Budget Tracker. Recommendations are a draft: check before applying.';
}

function setD(nd, source){
  D=nd; DERIVED=computeDerived(D); SOURCE=source;
  paintRing(); paintFresh(); paintStrip(); paintTrend(); paintOrphans(); paintFoot();
  buildControls(); fillClients(); fillAccounts(); fillAsOf();
  pollMoves(); render();
}

function showError(msg){
  $("#fresh").className="pd-fresh stale";
  $("#fresh").innerHTML='<span class="live"></span>'+esc(msg);
}

/* ── Interaction ─────────────────────────────────────────────── */
$("#list").addEventListener("toggle",function(e){
  var d=e.target; if(!d.classList||!d.classList.contains("pd-acct")) return;
  openKeys[d.dataset.key]=d.open;
  if(d.open && !d.querySelector(".pd-body")){
    var a=findAccount(d.dataset.key.slice(5)); if(a) d.insertAdjacentHTML("beforeend",detail(a));
  }
},true);

$("#list").addEventListener("change",function(e){
  var cb=e.target.closest(".pd-toggle input"); if(!cb) return;
  var tr=cb.closest("tr"), id=tr.dataset.moveId, next=cb.checked;
  cb.disabled=true;
  fetch(API+"/moves/"+encodeURIComponent(id),{method:"PUT",credentials:"same-origin",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({date:D.asOf,loc:tr.dataset.loc,from:tr.dataset.from,to:tr.dataset.to,amt:Number(tr.dataset.amt),applied:next})})
  .then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||r.status);return j})})
  .then(function(j){movesApplied[id]=j.move; render();})
  .catch(function(err){cb.checked=!next; cb.disabled=false; alert("Could not save that tick: "+err.message);});
});

$("#list").addEventListener("click",function(e){
  var csv=e.target.closest("[data-camp-csv]"); if(csv){exportCampaignsCsv(csv.dataset.campCsv);return;}
  var mth=e.target.closest("th[data-msort]");
  if(mth){moveSort.dir*=-1;render();return;}
  var th=e.target.closest("th.sortable[data-sort]");
  if(th){var k=th.dataset.sort; campSort.dir=campSort.key===k?campSort.dir*-1:(k==="name"||k==="type"?1:-1); campSort.key=k; render();}
});
$("#list").addEventListener("keydown",function(e){
  if(e.key!=="Enter"&&e.key!==" ") return;
  var th=e.target.closest("th.sortable"); if(th){e.preventDefault();th.click();}
});
$("#controls").addEventListener("click",function(e){
  var b=e.target.closest(".pm-chip"); if(!b) return;
  filter=b.dataset.f;
  document.querySelectorAll("#controls .pm-chip").forEach(function(c){c.setAttribute("aria-pressed",String(c===b))});
  render();
});
$("#exportAccounts").addEventListener("click",exportAccountsCsv);

/* ── Boot ────────────────────────────────────────────────────── */
getJson(API+"/snapshots").then(function(j){
  ROLLUPS=(j&&j.snapshots)||[];
  DATES=ROLLUPS.map(function(r){return r.id}).sort().reverse();
  LIVE_DATE=(j&&j.latest)||DATES[0]||null;
  if(!LIVE_DATE){
    $("#list").innerHTML='<div class="pm-panel"><p class="pm-empty" style="padding:26px 0">No run has been pushed yet. '+
      'The pacing engine sends each morning’s result here once its push is pointed at this page.</p></div>';
    showError("Waiting for the first run.");
    return;
  }
  return getJson(API+"/snapshots/"+encodeURIComponent(LIVE_DATE)).then(function(snap){
    if(!snap) throw new Error("the latest run ("+LIVE_DATE+") is missing");
    buildPickers(); setD(snap,"live");
  });
}).catch(function(e){
  $("#list").innerHTML='<div class="pm-err">Could not load the Pacing Desk: '+esc(e.message)+'</div>';
  showError("Not connected.");
});
})();
