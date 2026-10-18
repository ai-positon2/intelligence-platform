/* Product Analysis (Paid Marketing Agents).
 *
 * Which products earn their ad spend. The server sends one row per product for
 * the range (tracker/paid_products.py); every figure, every group a product
 * falls into and every recommendation is worked out in pa_data.js, so a number
 * here is a number that is tested there. This file only draws.
 *
 * The view is the URL (#a=account&p=range&seg=group&q=search ...), so a link
 * reproduces exactly what you were looking at. The rules (target ROAS and the
 * two thresholds) are kept in this browser.
 */
(function(){
"use strict";

var P = window.PSData, A = window.PAData, BASE = "/p2/paid-marketing/api/gads";
var $ = function(s,r){return (r||document).querySelector(s);};
var $$ = function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s));};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});};
var reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
var COST=A.COST, VAL=A.VAL, CONV=A.CONV, CLK=A.CLK, IMPR=A.IMPR;

var SEG_COLOR = {star:"#34d399",steady:"#38bdf8",drag:"#fbbf24",waste:"#fb7185",thin:"#8b94be"};
var TONE_COLOR = {good:"#34d399",info:"#38bdf8",warn:"#fbbf24",bad:"#fb7185",mute:"#8b94be",flat:"#8b94be"};
var PAGE = 25;
var RKEY = "pa_rules_v1";

var S = {
  accounts:[], acct:null, preset:"last30", range:null, compare:"prev", cmpRange:null,
  data:null, cmpData:null, items:[], cmpItems:[], total:null, cmpTotal:null, cls:null, cmpCls:null, oos:null, oosKnown:false,
  rules:{target:null,minClicks:A.DEFAULTS.minClicks,minSpend:null},
  seg:"all", q:"", sort:"cost", dir:-1, shown:PAGE, by:"brand", rm:"cost", log:null, open:"", pm:"value", pn:5, fetchedAt:null,
  seq:0, memo:{}
};
var charts = {}, accts = null, hashTimer = null;
var PL = window.PMPlatform;

/* ── Small helpers ───────────────────────────────────────────── */
/* One request; a combined account (`account=<google id>+<act_ id>`, Both) is two, merged by pm_platform.js: products are matched
   on the item id, which a shared Merchant Center feed keeps the same on Meta. */
function fetchJson(url,refresh){
  return PL.fetchJson(url,refresh,{acct:S.acct,merge:function(u,g,m){return PL.mergeProductsResponse(S.acct,g,m);}});
}
function getJson(url){
  if(S.memo[url]) return S.memo[url];
  var p=fetchJson(url,false);
  S.memo[url] = p;
  p.catch(function(){delete S.memo[url];});
  return p;
}
function alpha(hex,a){var n=parseInt(hex.slice(1),16);return "rgba("+(n>>16&255)+","+(n>>8&255)+","+(n&255)+","+a+")";}
function light(){return document.documentElement.getAttribute("data-theme")==="light";}
function shade(hex,k){var n=parseInt(hex.slice(1),16);function c(v){return Math.round(v*k).toString(16).padStart(2,"0");}return "#"+c(n>>16&255)+c(n>>8&255)+c(n&255);}
function col(hex){return light()?shade(hex,.72):hex;}
function theme(){
  var cs=getComputedStyle($("#pa")), g=function(n,d){return (cs.getPropertyValue(n)||"").trim()||d;};
  return {ink:g("--pm-ink","#e8ecf8"),ink2:g("--pm-ink2","#c4cbe4"),muted:g("--pm-muted","#8b94be"),faint:g("--pm-faint","#5d6688"),
    line:g("--pm-line","rgba(255,255,255,.07)"),line2:g("--pm-line2","rgba(255,255,255,.12)"),panel:light()?"#ffffff":"#0c0f1c",accent:g("--pm-a","#ff7a59")};
}
function cur(){return S.acct?S.acct.currency:"USD";}
function money(v,small){return P.fmt("money",v,cur(),small);}
function pctStr(v,d){return v==null?"–":(v*100).toFixed(d==null?0:d)+"%";}
function fmtK(kind,v,small){return P.fmt(kind,v,cur(),small);}
function toast(msg){
  var t=$("#psToast"); if(!t){t=document.createElement("div");t.id="psToast";t.className="ps-toast";document.body.appendChild(t);}
  t.textContent=msg; t.classList.add("on"); clearTimeout(t._t); t._t=setTimeout(function(){t.classList.remove("on");},2200);
}
function fmtRange(r){
  var a=r.start.split("-"), b=r.end.split("-"), M=P.dayLabel;
  return M(r.start)+(a[0]!==b[0]?", "+a[0]:"")+" to "+M(r.end)+", "+b[0];
}
function download(name,mime,text){
  var a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([text],{type:mime})); a.download=name;
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
}
function slugify(s){return String(s).toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-+|-+$/g,"").slice(0,40)||"account";}
function destroyChart(id){if(charts[id]){charts[id].destroy();delete charts[id];}}
function mkChart(id,canvas,cfg){destroyChart(id);charts[id]=new Chart(canvas,cfg);return charts[id];}
function trunc(s,n){s=String(s||"");return s.length>n?s.slice(0,n-1)+"…":s;}

Chart.defaults.font.family="'Space Grotesk',system-ui,sans-serif";
Chart.defaults.font.size=11.5;
Chart.defaults.animation.duration=reduceMotion?0:650;
function tip(){
  var t=theme();
  return {backgroundColor:"rgba(9,12,24,.97)",titleColor:"#eef1fb",bodyColor:"#c4cbe4",borderColor:"rgba(255,255,255,.14)",borderWidth:1,padding:11,cornerRadius:12,boxPadding:5};
}
function axes(o){
  var t=theme(); o=o||{};
  return {
    x:{grid:{display:false},border:{color:t.line2},ticks:{color:t.muted,maxRotation:0,autoSkip:true,maxTicksLimit:o.xTicks||10},title:o.xTitle?{display:true,text:o.xTitle,color:t.faint}:undefined,
       type:o.xType||"category"},
    y:{beginAtZero:o.beginAtZero!==false,grid:{color:t.line},border:{display:false},ticks:{color:t.muted,callback:o.yFmt||function(v){return v;}},
       title:o.yTitle?{display:true,text:o.yTitle,color:t.faint}:undefined}
  };
}


function relTime(iso){
  var t=new Date(iso).getTime(); if(isNaN(t)) return "";
  var m=Math.max(0,Math.round((Date.now()-t)/60000));
  return m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
}
function plat(){
  var p=S.acct?S.acct.platform:(accts?accts.platform():"google");
  return p==="meta"?"Meta Ads":p==="both"?"Google Ads and Meta Ads":"Google Ads";
}
function paintUpdated(iso){
  S.fetchedAt=iso||null;
  var el=$("#upd"); if(!el) return;
  el.textContent=iso&&relTime(iso)?" · Updated "+relTime(iso):"";
  el.title=iso?"Last read from "+plat()+" "+new Date(iso).toLocaleString():"";
}
setInterval(function(){if(S.fetchedAt) paintUpdated(S.fetchedAt);},30000);
/* The same request as getJson, but asking Google again (the server allows it once every two minutes per account); the answer
   replaces the saved one in S.memo so the next load() paints it. */
function refreshJson(url){
  var p=fetchJson(url,true);
  S.memo[url]=p; p.catch(function(){delete S.memo[url];});
  return p;
}
function doRefresh(urls){
  var btn=$("#refreshBtn"); if(!S.acct||btn.classList.contains("busy")) return;
  btn.classList.add("busy"); btn.disabled=true;
  var main=refreshJson(urls[0]), rest=urls.slice(1).map(function(u){return refreshJson(u).catch(function(){return null;});});
  Promise.all([main].concat(rest)).then(function(){
    btn.classList.remove("busy"); btn.disabled=false; load(); toast("Read from "+plat()+" just now");
  }).catch(function(err){
    btn.classList.remove("busy"); btn.disabled=false;
    toast(err.cooldown?err.message:"Could not refresh: "+err.message);
    if(!err.cooldown) load();
  });
}


/* ── Notices and states ───────────────────────────────────────── */
function notice(kind,title,html){
  $("#notice").innerHTML=title?'<div class="ps-notice '+kind+'"><b>'+esc(title)+'</b><p>'+html+'</p></div>':"";
}
function paintStale(stale,blockedUntil,partial,note){
  var el=$("#staleBar"), t=function(x){return new Date(x).toLocaleString([], {weekday:"short",hour:"numeric",minute:"2-digit"});};
  if(!stale&&!blockedUntil&&!partial&&!note){el.hidden=true;el.innerHTML="";return;}
  el.hidden=false;
  var h="";
  if(partial) h+='<b>Showing part of this account.</b> '+esc(partial);
  if(note) h+=(h?' ':'')+'<b>Good to know.</b> '+esc(note);
  if(stale||blockedUntil) h+=(h?' ':'')+'<b>'+(stale?"Showing the copy saved "+esc(t(stale)):"Saved views still open")+'.</b> The platform has limited this app’s reads'+
    (blockedUntil?', so fresh numbers come back around <b>'+esc(t(blockedUntil*1000))+'</b>':'')+'.';
  el.innerHTML=h;
}
function showError(err){
  var n=plat();
  $("#dash").hidden=true;
  $("#liveText").textContent=n+" is not reachable";
  $("#live").classList.add("off");
  var meta=n==="Meta Ads";
  if(err.status===409) notice("setup",n+" is not connected yet",
    esc(err.message)+' An admin sets these on Railway (they are named <code>'+(meta?"META_ADS_*":"GOOGLE_ADS_*")+'</code>), then reloads this page.');
  else if(err.status===429) notice("warn",n+" is not answering right now",esc(err.message));
  else notice("bad","Could not load "+n,esc(err.message));
}

/* ── Rules (kept in this browser) ─────────────────────────────── */
function loadRules(){
  try{
    var r=JSON.parse(localStorage.getItem(RKEY)||"null");
    if(r&&typeof r==="object") S.rules=cleanRules(r);
  }catch(e){}
}
function saveRules(){try{localStorage.setItem(RKEY,JSON.stringify(S.rules));}catch(e){}}
function cleanRules(r){
  var t=+r.target, c=+r.minClicks, s=r.minSpend==null||r.minSpend===""?null:+r.minSpend;
  return {target:t>0&&t<1000?t:null, minClicks:c>=0&&c<=100000?Math.round(c):A.DEFAULTS.minClicks, minSpend:s!=null&&s>=0&&isFinite(s)?s:null};
}

/* ── Hash state: the URL is the view ──────────────────────────── */
function writeHash(){
  clearTimeout(hashTimer);
  hashTimer=setTimeout(function(){
    if(!S.acct) return;
    var q=[["pf",accts?accts.platform():"google"],["a",S.acct.id],["p",S.preset]];
    if(S.preset==="custom") q.push(["s",S.range.start],["e",S.range.end]);
    q.push(["c",S.compare]);
    if(S.seg!=="all") q.push(["seg",S.seg]);
    if(S.q) q.push(["q",S.q]);
    q.push(["so",S.sort],["sd",S.dir<0?"d":"a"],["by",S.by],["rm",S.rm],["pm",S.pm],["pn",S.pn]);
    if(S.log!=null) q.push(["lg",S.log?1:0]);
    if(S.rules.target!=null) q.push(["tr",S.rules.target]);
    if(S.rules.minClicks!==A.DEFAULTS.minClicks) q.push(["mc",S.rules.minClicks]);
    if(S.rules.minSpend!=null) q.push(["ms",S.rules.minSpend]);
    try{history.replaceState(null,"","#"+q.map(function(x){return x[0]+"="+encodeURIComponent(x[1]);}).join("&"));}catch(e){}
  },250);
}
function readHash(){
  var h={}; location.hash.replace(/^#/,"").split("&").forEach(function(p){
    var i=p.indexOf("="); if(i>0) h[p.slice(0,i)]=decodeURIComponent(p.slice(i+1));
  });
  return h;
}
var SORTS = A.COLS.map(function(c){return c.key;});
function applyHash(h){
  if(h.p&&(P.PRESETS.some(function(x){return x.id===h.p;})||h.p==="custom")) S.preset=h.p;
  if(S.preset==="custom"&&/^\d{4}-\d{2}-\d{2}$/.test(h.s||"")&&/^\d{4}-\d{2}-\d{2}$/.test(h.e||"")&&h.s<=h.e) S.range={start:h.s,end:h.e};
  else if(S.preset==="custom") S.preset="last30";
  if(["prev","yoy","none"].indexOf(h.c)>=0) S.compare=h.c;
  if(h.seg==="all"||A.FILTERS[h.seg]) S.seg=h.seg||"all";
  if(h.q) S.q=h.q.slice(0,120);
  if(SORTS.indexOf(h.so)>=0) S.sort=h.so;
  if(h.sd==="a") S.dir=1;
  if(A.BY[h.by]) S.by=h.by;
  if(["cost","value","roas","conv"].indexOf(h.rm)>=0) S.rm=h.rm;
  if(h.lg==="1") S.log=true; else if(h.lg==="0") S.log=false;
  if(A.PERF[h.pm]) S.pm=h.pm;
  if(["5","10","20"].indexOf(h.pn)>=0) S.pn=+h.pn;
  if(h.tr||h.mc||h.ms) S.rules=cleanRules({target:h.tr||S.rules.target,minClicks:h.mc!=null?h.mc:S.rules.minClicks,minSpend:h.ms!=null?h.ms:S.rules.minSpend});
}

/* ── Command bar ──────────────────────────────────────────────── */
function paintRanges(){
  var h="";
  P.PRESETS.forEach(function(p){h+='<button type="button" class="pm-chip" data-p="'+p.id+'" aria-pressed="'+(S.preset===p.id)+'">'+p.label+'</button>';});
  h+='<button type="button" class="pm-chip" data-p="custom" aria-pressed="'+(S.preset==="custom")+'">Custom</button>';
  $("#ranges").innerHTML=h;
  $("#custom").hidden=S.preset!=="custom"&&!$("#custom")._open;
  $("#cStart").value=S.range.start; $("#cEnd").value=S.range.end; $("#cEnd").max=P.localToday(); $("#cStart").max=P.localToday();
  $$("#cmpSeg button").forEach(function(b){b.setAttribute("aria-checked",String(b.dataset.c===S.compare));});
}
function computeRanges(){
  if(S.preset!=="custom") S.range=P.presetRange(S.preset);
  S.cmpRange=P.compareRange(S.range,S.compare);
  var n=P.diffDays(S.range.start,S.range.end)+1;
  $("#rangeNote").innerHTML='<b>'+esc(fmtRange(S.range))+'</b> ('+n+(n===1?" day":" days")+')'+
    (S.cmpRange?' compared with <b>'+esc(fmtRange(S.cmpRange))+'</b>':' with no comparison')+'.';
}
function onRange(p){
  if(p==="custom"){S.preset="custom";$("#custom")._open=true;paintRanges();return;}
  S.preset=p; $("#custom")._open=false; paintRanges(); computeRanges(); load();
}
$("#ranges").addEventListener("click",function(e){var b=e.target.closest("[data-p]"); if(b) onRange(b.dataset.p);});
$("#cApply").addEventListener("click",function(){
  var s=$("#cStart").value, e=$("#cEnd").value;
  if(!s||!e||s>e){toast("Pick a start date on or before the end date");return;}
  if(P.diffDays(s,e)+1>800){toast("That is longer than 800 days");return;}
  S.preset="custom"; S.range={start:s,end:e}; paintRanges(); computeRanges(); load();
});
$("#cmpSeg").addEventListener("click",function(e){
  var b=e.target.closest("[data-c]"); if(!b||b.dataset.c===S.compare) return;
  S.compare=b.dataset.c; paintRanges(); computeRanges(); load();
});
$("#shareBtn").addEventListener("click",function(){
  writeHash();
  setTimeout(function(){
    (navigator.clipboard?navigator.clipboard.writeText(location.href):Promise.reject()).then(function(){toast("Link to this exact view copied");},
      function(){toast("Copy the address bar to share this view");});
  },300);
});
$("#refreshBtn").addEventListener("click",function(){
  if(!S.acct||!S.range) return;
  var q="account="+S.acct.id+"&start="+S.range.start+"&end="+S.range.end;
  if(S.cmpRange) q+="&compare_start="+S.cmpRange.start+"&compare_end="+S.cmpRange.end;
  doRefresh([BASE+"/products?"+q]);
});
$("#exportBtn").addEventListener("click",function(){
  if(!S.items.length){toast("Nothing to export yet");return;}
  var list=tableRows();
  download("products-"+slugify(S.acct.name)+"-"+S.range.start+"-"+S.range.end+".csv","text/csv;charset=utf-8",A.csv(list,cur(),S.cls.ctx));
  toast("Downloaded "+list.length.toLocaleString("en-US")+" products");
});

/* ── The rules panel ──────────────────────────────────────────── */
function paintTune(){
  var el=$("#tune"), c=S.cls?S.cls.ctx:null, r=S.rules;
  var autoT=c&&c.hasValue&&c.acctRoas?c.acctRoas.toFixed(2)+"x":"";
  var autoS=c&&c.acctCpa?money(c.acctCpa):"";
  var noVal=c&&!c.hasValue;
  el.innerHTML='<div class="pa-tune-g">'+
    '<label class="pa-fld"><span>Target ROAS</span><input id="rTarget" inputmode="decimal" placeholder="'+(autoT?esc(autoT)+' (this account)':'Auto')+'" value="'+(r.target!=null?esc(r.target):"")+'"'+(noVal?" disabled":"")+'>'+
      '<small>'+(noVal?'This account reports no conversion value, so products are judged on cost per conversion.':'A product at 1.25x this or better is a Star. Leave blank to use the account’s own ROAS.')+'</small></label>'+
    '<label class="pa-fld"><span>Clicks needed to judge</span><input id="rClicks" inputmode="numeric" value="'+esc(r.minClicks)+'">'+
      '<small>Products with fewer clicks and no sale are “Not enough data”.</small></label>'+
    '<label class="pa-fld"><span>Spend that counts as wasted</span><input id="rSpend" inputmode="decimal" placeholder="'+(autoS?esc(autoS)+' (one typical sale)':'Auto')+'" value="'+(r.minSpend!=null?esc(r.minSpend):"")+'">'+
      '<small>A product that spent this much and sold nothing is “Spend, no sales”.</small></label>'+
    '</div><div class="pa-tune-f"><button type="button" class="pm-chip on" id="rApply">Apply</button><button type="button" class="pm-chip" id="rReset">Back to automatic</button></div>';
  $("#rApply").onclick=function(){
    S.rules=cleanRules({target:$("#rTarget").value.trim(),minClicks:$("#rClicks").value.trim()===""?A.DEFAULTS.minClicks:$("#rClicks").value,minSpend:$("#rSpend").value.trim()});
    saveRules(); recompute(); paintTune(); toast("Rules applied");
  };
  $("#rReset").onclick=function(){S.rules={target:null,minClicks:A.DEFAULTS.minClicks,minSpend:null};saveRules();recompute();paintTune();toast("Back to automatic");};
  el.onkeydown=function(e){if(e.key==="Enter"&&e.target.tagName==="INPUT"){e.preventDefault();$("#rApply").click();}};
}
$("#tuneBtn").addEventListener("click",function(){
  var el=$("#tune"), open=el.hidden;
  el.hidden=!open; this.setAttribute("aria-expanded",String(open));
  if(open){paintTune();var f=$("input:not([disabled])",el); if(f) f.focus();}
});

/* ── Loading ──────────────────────────────────────────────────── */
function skeleton(){
  $("#dash").hidden=false;
  $("#kpis").innerHTML=new Array(9).join('<div class="ps-skel ps-skel-kpi"></div>');
  $("#actions").innerHTML='<div class="ps-skel" style="height:150px"></div><div class="ps-skel" style="height:150px"></div><div class="ps-skel" style="height:150px"></div>';
}
function load(){
  var seq=++S.seq, q="account="+S.acct.id+"&start="+S.range.start+"&end="+S.range.end;
  if(S.cmpRange) q+="&compare_start="+S.cmpRange.start+"&compare_end="+S.cmpRange.end;
  notice("");
  skeleton();
  $("#liveText").textContent="Reading "+S.acct.name+" products from "+plat()+"…";
  getJson(BASE+"/products?"+q).then(function(j){
    if(seq!==S.seq) return;
    S.data=j.current; S.cmpData=j.compare;
    paintStale(j.stale,j.blockedUntil,j.partial,j.current&&j.current.note); paintUpdated(j.fetchedAt);
    $("#live").classList.remove("off");
    $("#liveText").textContent="Live from "+plat()+" · "+S.accounts.length+" accounts · "+S.acct.name+" in "+S.acct.currency;
    $("#dash").hidden=false;
    S.shown=PAGE; S.open="";
    recompute();
  }).catch(function(err){if(seq===S.seq) showError(err);});
}
/* The rules or the data changed: group every product again and repaint, without asking Google. */
function recompute(){
  if(!S.data) return;
  S.items=A.items(S.data); S.total=A.totals(S.data);
  S.cls=A.classify(S.items,S.total,S.rules);
  S.cmpItems=S.cmpData?A.items(S.cmpData):[]; S.cmpTotal=S.cmpData?A.totals(S.cmpData):null;
  S.cmpCls=S.cmpData?A.classify(S.cmpItems,S.cmpTotal,S.rules):null;
  S.oosKnown=Array.isArray(S.data.outOfStock); S.oos=S.oosKnown?S.data.outOfStock:null;
  paintAll();
}

/* ── Painting ─────────────────────────────────────────────────── */
function empty(){return !S.total[COST]&&!S.total[IMPR]&&!S.total[CLK];}
function paintAll(){
  var none=empty();
  $("#dash").classList.toggle("is-empty",none);
  if(none){
    notice("info","No product activity in this range","<b>"+esc(S.acct.name)+"</b> had no "+(S.acct.platform==="meta"?"catalog sales (product) ad":S.acct.platform==="both"?"Shopping, Performance Max or catalog sales ad":"Shopping or Performance Max product")+" impressions from "+esc(fmtRange(S.range))+
      ". Try a longer range, or another account. "+(S.acct.platform==="meta"?"Meta reports products only for catalog sales ads (Advantage+ catalog), so accounts without them will always be empty here.":"Accounts without a product feed will always be empty here."));
    ["pareto","perf","groups","bubble","rollup","trend"].forEach(destroyChart);
    writeHash(); return;
  }
  notice("");
  paintKpis(); paintActions(); paintPareto(); paintInsights(); paintPerf(); paintGroups(); paintBubble(); paintRollup(); paintTrend(); paintMovers(); paintTable();
  if(!$("#tune").hidden) paintTune();
  writeHash();
}

/* Key numbers */
var IC = {
  spend:'<path d="M12 3v18"/><path d="M16.5 7.5C16.5 5.8 14.5 4.5 12 4.5S7.5 5.8 7.5 7.5 9.5 10 12 10.5s4.5 1.3 4.5 3S14.5 19.5 12 19.5 7.5 18.2 7.5 16.5"/>',
  revenue:'<path d="M4 17l5-5 4 4 7-8"/><path d="M15 8h5v5"/>',
  roas:'<path d="M4 17a8 8 0 1 1 16 0"/><path d="m12 17 4-6"/>',
  conv:'<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".8"/>',
  cpa:'<path d="M5 12h14"/><path d="M12 5v14"/><circle cx="12" cy="12" r="9"/>',
  sell:'<path d="M5 7h14l-1.5 9a2 2 0 0 1-2 1.7H8.5a2 2 0 0 1-2-1.7z"/><path d="M9 7a3 3 0 0 1 6 0"/>',
  waste:'<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/>',
  top:'<path d="M12 3l2.6 5.5 6 .8-4.4 4.2 1.1 6-5.3-2.9-5.3 2.9 1.1-6L3.4 9.3l6-.8z"/>'
};
function delta(key,v,pv,fmtPrev){
  if(pv==null||v==null) return '<span class="d flat"><em>&nbsp;</em></span>';
  var ch=P.change(key,v,pv);
  return '<span class="d '+ch.tone+'">'+(ch.dir>0?'<i>▲</i>':ch.dir<0?'<i>▼</i>':'')+esc(ch.text)+'<em>vs '+esc(fmtPrev)+'</em></span>';
}
function paintKpis(){
  var c=S.cls.ctx, t=S.total, pt=S.cmpTotal, k=A.kpis(t,pt,S.cls), pk=S.cmpCls?A.kpis(pt,null,S.cmpCls):null, pa=A.pareto(S.items,c);
  var hasV=c.hasValue, cards=[];
  cards.push({k:"Spend",c:"#fbbf24",i:IC.spend,v:money(t[COST],true),d:delta("cost",t[COST],pt?pt[COST]:null,pt?money(pt[COST],true):"")});
  cards.push(hasV?{k:"Revenue",c:"#34d399",i:IC.revenue,v:money(t[VAL],true),d:delta("value",t[VAL],pt?pt[VAL]:null,pt?money(pt[VAL],true):"")}
    :{k:"Revenue",c:"#34d399",i:IC.revenue,v:"–",d:'<span class="d flat"><em>No conversion value reported</em></span>',nodata:true});
  cards.push(hasV?{k:"ROAS",c:"#38bdf8",i:IC.roas,v:k.roas==null?"–":k.roas.toFixed(2)+"x",d:delta("roas",k.roas,pt?(pt[COST]?pt[VAL]/pt[COST]:null):null,pt&&pt[COST]?(pt[VAL]/pt[COST]).toFixed(2)+"x":"")}
    :{k:"ROAS",c:"#38bdf8",i:IC.roas,v:"–",d:'<span class="d flat"><em>No conversion value reported</em></span>',nodata:true});
  cards.push({k:"Conversions",c:"#a3e635",i:IC.conv,v:fmtK("num",t[CONV],true),d:delta("conversions",t[CONV],pt?pt[CONV]:null,pt?fmtK("num",pt[CONV],true):"")});
  cards.push({k:"Cost / conv.",c:"#fb7185",i:IC.cpa,v:k.cpa==null?"–":fmtK("money2",k.cpa),d:delta("cpa",k.cpa,pk?pk.cpa:null,pk&&pk.cpa!=null?fmtK("money2",pk.cpa):"")});
  cards.push({k:"Products selling",c:"#818cf8",i:IC.sell,v:k.selling.toLocaleString("en-US")+'<small> of '+k.products.toLocaleString("en-US")+'</small>',
    d:delta("conversions",k.selling,pk?pk.selling:null,pk?pk.selling.toLocaleString("en-US"):"")});
  cards.push({k:"Spend, no sales",c:"#f97316",i:IC.waste,v:money(k.waste,true),
    d:k.wastePct==null?'<span class="d flat"><em>&nbsp;</em></span>':'<span class="d '+(k.wastePct>=0.2?"bad":k.wastePct>=0.1?"flat":"good")+'">'+pctStr(k.wastePct,1)+'<em>of spend'+(pk&&pk.waste!=null&&pt?", was "+pctStr(pk.wastePct,1):"")+'</em></span>'});
  cards.push(pa.count>=2?{k:"Top five share",c:"#e879f9",i:IC.top,v:pctStr(pa.top5,0),d:'<span class="d flat"><em>of '+(hasV?"revenue":"conversions")+' from 5 products</em></span>'}
    :{k:"Top five share",c:"#e879f9",i:IC.top,v:"–",d:'<span class="d flat"><em>Too few products with sales</em></span>',nodata:true});
  $("#kpis").style.setProperty("--cols",P.kpiCols(cards.length));
  $("#kpis").innerHTML=cards.map(function(x,i){
    return '<div class="ps-kpi ps-kpi-static'+(x.nodata?" nodata":"")+'" style="--kc:'+x.c+'"><div class="ps-kpi-h"><span class="ic"><svg viewBox="0 0 24 24" aria-hidden="true">'+x.i+'</svg></span><span class="k">'+esc(x.k)+'</span></div>'+
      '<span class="v">'+x.v+'</span>'+x.d+'<div class="ps-kpi-f"></div></div>';
  }).join("");
  if(!reduceMotion&&!document.hidden) $$("#kpis .ps-kpi").forEach(function(b,i){
    b.animate([{opacity:0,transform:"translateY(8px)"},{opacity:1,transform:"none"}],{duration:380,delay:i*35,easing:"cubic-bezier(.16,1,.3,1)",fill:"backwards"});
  });
  var c2=S.cls.ctx, bits=[];
  bits.push(c2.hasValue?"Judged on ROAS against "+(c2.autoTarget?"this account's own "+(c2.acctRoas?c2.acctRoas.toFixed(2)+"x":""):"your "+c2.target.toFixed(2)+"x target"):"Judged on cost per conversion, because no conversion value is reported");
  $("#kpiHint").textContent=bits.join(". ")+". Change this under Rules.";
}

/* What to do */
function paintActions(){
  var acts=A.actions(S.items,S.cls,S.oos), el=$("#actions");
  var stockNote=!S.oosKnown?'<p class="pa-stocknote">Stock status could not be read, so out-of-stock products are not checked. This needs a Merchant Center link on the Google Ads account, and Meta does not report stock.</p>':"";
  if(!acts.length){
    el.innerHTML='<div class="pa-all-good"><b>Nothing urgent.</b><p>No product is wasting money, none are out of stock and still advertised, and no clear gaps stand out. Try a longer date range or tighten the rules.</p></div>'+stockNote;
    return;
  }
  el.style.setProperty("--cols",acts.length<=4?acts.length:acts.length%4===0||acts.length===7?4:3);
  el.innerHTML=acts.map(function(a,i){
    var big=a.amountKind==="value"?money(a.value,true):money(a.cost,true), lab=a.amountKind==="value"?"revenue they bring":"spend on them";
    return '<button type="button" class="pa-act '+a.tone+'" data-f="'+a.key+'" style="--i:'+i+';--ac:'+TONE_COLOR[a.tone]+'">'+
      '<span class="n"><b>'+a.count.toLocaleString("en-US")+'</b> '+(a.count===1?"product":"products")+'</span>'+
      '<span class="t">'+esc(a.title)+'</span><span class="h">'+esc(a.how)+'</span>'+
      '<span class="m"><b>'+esc(big)+'</b> '+esc(lab)+'<span class="go">See them <i>→</i></span></span></button>';
  }).join("")+stockNote;
  $$(".pa-act",el).forEach(function(b){b.onclick=function(){setSeg(b.dataset.f,true);};});
}
function setSeg(key,scroll){
  S.seg=key; S.shown=PAGE; paintTable(); writeHash();
  if(scroll) $("#prods").scrollIntoView({behavior:document.hidden||reduceMotion?"auto":"smooth",block:"start"});
}

/* The 80/20 curve */
function paintPareto(){
  var c=S.cls.ctx, pa=A.pareto(S.items,c), el=$("#paretoCard"), unit=c.hasValue?"revenue":"conversions", t=theme();
  if(!pa.count){
    destroyChart("pareto");
    el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">A few products carry the account</span><span class="ps-sub">Share of '+unit+' by product, best first</span></div></div>'+
      '<p class="ps-msg-inline">No product in this range recorded '+(c.hasValue?"revenue":"a conversion")+'.</p>';
    return;
  }
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">A few products carry the account</span>'+
    '<span class="ps-sub"><b>'+pa.at80.toLocaleString("en-US")+'</b> of '+pa.all.toLocaleString("en-US")+' products ('+pctStr(pa.pct80,0)+') make 80% of '+unit+'</span></div></div>'+
    '<div class="ps-wrap"><canvas id="cvPareto" aria-label="Cumulative share of '+unit+' by product"></canvas></div>'+
    '<p class="ps-note">Read left to right: each step adds the next best product. The curve crosses the dashed line where 80% of '+unit+' is reached.</p>';
  var a=col("#ff7a59");
  mkChart("pareto",$("#cvPareto"),{type:"line",data:{labels:pa.points.map(function(p){return p.k;}),datasets:[
    {label:"Cumulative share",data:pa.points.map(function(p){return +(p.share*100).toFixed(2);}),borderColor:a,borderWidth:2.4,pointRadius:0,pointHoverRadius:5,tension:.25,fill:true,
      backgroundColor:function(cx){var ch=cx.chart,ar=ch.chartArea; if(!ar) return alpha("#ff7a59",.2); var g=ch.ctx.createLinearGradient(0,ar.top,0,ar.bottom); g.addColorStop(0,alpha("#ff7a59",.38)); g.addColorStop(1,alpha("#ff7a59",0)); return g;}},
    {label:"80%",data:pa.points.map(function(){return 80;}),borderColor:t.faint,borderDash:[5,5],borderWidth:1.2,pointRadius:0,fill:false}]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      plugins:{legend:{display:false},tooltip:Object.assign(tip(),{callbacks:{title:function(i){return "Top "+i[0].label+" products";},label:function(i){return i.datasetIndex?"80% line":i.parsed.y.toFixed(1)+"% of "+unit;}}})},
      scales:{x:Object.assign(axes({xTitle:"Products, best first",xTicks:8}).x,{}),y:Object.assign(axes({yFmt:function(v){return v+"%";}}).y,{max:100})}}});
}

/* What stands out */
var TIC = {good:'<path d="m5 12 5 5L20 7"/>',warn:'<path d="M12 4 3 20h18z"/><path d="M12 10v5M12 18v.5"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  bad:'<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',mute:'<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',flat:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>'};
function paintInsights(){
  var mv=A.movers(S.items,S.cmpItems,S.cls.ctx), pa=A.pareto(S.items,S.cls.ctx);
  var list=A.insights(S.items,S.cls,pa,mv,S.oos,cur());
  $("#insights").innerHTML='<div class="pm-panel ps-card"><div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">What stands out</span><span class="ps-sub">Worked out from the numbers, not guessed</span></div></div><div class="ps-ins">'+
    (list.length?list.map(function(x,i){
      var tone=x.tone==="bad"?"bad":x.tone==="mute"||x.tone==="flat"?"info":x.tone;
      return '<div class="ps-in '+tone+'" style="--i:'+i+'"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true">'+(TIC[x.tone]||TIC.info)+'</svg><div><p>'+esc(x.text)+'</p></div></div>';}).join("")
     :'<p class="ps-msg-inline">Nothing stands out yet.</p>')+'</div></div>';
}


/* How each product is performing: the products ranked by one measure, with each top spender's days drawn as a line */
var PERF_COLORS=["#ff7a59","#38bdf8","#a3e635","#e879f9","#fbbf24","#2dd4bf","#fb7185","#818cf8","#f97316","#22d3ee"];
function pcol(i){
  if(i<PERF_COLORS.length) return col(PERF_COLORS[i]);
  return "hsl("+((i*53+20)%360)+",70%,"+(light()?"42%":"62%")+")";
}
function sparkSvg(vals,color){
  var v=vals.filter(function(x){return x!=null&&isFinite(x);});
  if(v.length<2||Math.max.apply(null,v)===Math.min.apply(null,v)&&!v[0]) return '<span class="pa-nospark">no daily data</span>';
  var mn=Math.min.apply(null,v), mx=Math.max.apply(null,v), w=130, h=34, pad=3, n=vals.length, pts=[];
  vals.forEach(function(x,i){
    if(x==null||!isFinite(x)) return;
    pts.push([n===1?0:i/(n-1)*w, mx===mn?h/2:h-pad-(x-mn)/(mx-mn)*(h-pad*2)]);
  });
  var d="M"+pts.map(function(p){return p[0].toFixed(1)+","+p[1].toFixed(1);}).join("L");
  return '<svg class="pa-spark" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" aria-hidden="true"><path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>';
}
function pmOptions(){
  var c=S.cls.ctx, o=[];
  o.push(c.hasValue?["value","Revenue"]:["conversions","Conversions"]);
  o.push(["cost","Spend"]);
  if(c.hasValue){o.push(["roas","ROAS"]);o.push(["conversions","Conversions"]);}
  o.push(["clicks","Clicks"],["impressions","Impressions"],["ctr","CTR"],["cpc","Avg. CPC"]);
  return o;
}
function pFmt(key,v,small){var m=P.METRICS[key]; return fmtK(m.kind,v,small&&(m.kind==="money"||m.kind==="int"));}
function paintPerf(){
  var el=$("#perf"), opts=pmOptions(), t=theme(), ser=S.data.series;
  if(!opts.some(function(o){return o[0]===S.pm;})) S.pm=opts[0][0];
  var lb=A.leaderboard(S.items,S.cmpItems,S.pm,20), have=lb.rows.filter(function(r){return A.seriesValues(ser,r.it.id,S.pm);});
  var nLines=Math.min(S.pn,have.length);
  $("#perfHint").textContent=ser?"Pick a measure. The lines are each of the "+(ser.ids.length)+" products that spent the most, day by day; the list ranks every product."
    :"Daily lines are not available for this view, so only the ranking is shown.";
  var meta=A.PERF[S.pm];
  var top='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+esc(meta.label)+' by product</span>'+
    '<span class="ps-sub">'+(ser&&nLines?"Top "+nLines+" of those that spent the most, day by day":"Ranked across all "+S.items.filter(function(i){return !i.other;}).length.toLocaleString("en-US")+" products")+'</span></div>'+
    '<div class="ps-card-actions"><div class="ps-seg ps-seg-s" id="pmSeg" role="radiogroup" aria-label="Measure">'+opts.map(function(o){return '<button type="button" role="radio" data-m="'+o[0]+'" aria-checked="'+(S.pm===o[0])+'">'+o[1]+'</button>';}).join("")+'</div>'+
    (ser&&have.length>5?'<div class="ps-seg ps-seg-s" id="pnSeg" role="radiogroup" aria-label="Lines">'+[5,10,20].filter(function(n){return n<=Math.max(5,have.length);}).map(function(n){return '<button type="button" role="radio" data-n="'+n+'" aria-checked="'+(S.pn===n)+'">Top '+n+'</button>';}).join("")+'</div>':"")+'</div></div>';
  var chart=ser&&nLines?'<div class="ps-wrap"><canvas id="cvPerf" aria-label="'+esc(meta.label)+' by product, day by day"></canvas></div>':"";
  var maxV=lb.rows.length?lb.rows[0].v:0;
  var body='<div class="ps-tscroll"><table class="ps-table pa-table pa-lb"><thead><tr><th class="num">#</th><th class="name">Product</th><th class="trend">Daily</th>'+
    '<th class="num">'+esc(meta.label)+'</th><th class="num">'+(lb.additive?"Share":"Against best")+'</th><th class="num">Vs before</th><th class="grp">Group</th></tr></thead><tbody>'+
    (lb.rows.length?lb.rows.map(function(r,i){
      var it=r.it, vals=A.seriesValues(ser,it.id,S.pm), seg=it.seg?A.SEG[it.seg]:null, share=lb.additive?r.share:(maxV?r.v/maxV:0);
      var ch=r.change?'<span class="d '+r.change.tone+'">'+(r.change.dir>0?'<i>▲</i>':r.change.dir<0?'<i>▼</i>':'')+esc(r.change.text)+'</span>':'<span class="d flat">–</span>';
      var li=have.indexOf(r);
      return '<tr data-id="'+esc(it.id)+'"><td class="num pa-rank">'+r.rank+'</td><td class="name"><span class="cn">'+esc(trunc(it.title,80))+'</span><span class="cm">'+esc(it.id)+(it.brand?' · '+esc(it.brand):"")+'</span></td>'+
        '<td class="trend">'+(vals?sparkSvg(vals,li>=0&&li<nLines?pcol(li):col("#8b94be")):'<span class="pa-nospark">daily not read</span>')+'</td>'+
        '<td class="num"><b>'+esc(pFmt(S.pm,r.v,false))+'</b></td>'+
        '<td class="num"><span class="sbar pa-sbar"><s style="width:'+Math.min(100,share*100).toFixed(1)+'%"></s></span>'+(lb.additive?'<small class="d flat">'+pctStr(r.share,1)+'</small>':'')+'</td>'+
        '<td class="num">'+ch+'</td>'+
        '<td class="grp">'+(seg?'<span class="pa-badge" style="--cc:'+col(SEG_COLOR[seg.key])+'">'+esc(seg.label)+(it.gem?" ★":"")+'</span>':"")+'</td></tr>';
    }).join(""):'<tr><td colspan="7" class="empty">No product has any '+esc(meta.label.toLowerCase())+' in this range.</td></tr>')+'</tbody></table></div>';
  el.innerHTML=top+chart+body;
  destroyChart("perf");
  if(chart){
    var rows=have.slice(0,nLines), days=ser.days.map(P.dayLabel), kind=P.METRICS[S.pm].kind;
    var ds=rows.map(function(r,i){
      return {label:trunc(r.it.title,34),data:A.seriesValues(ser,r.it.id,S.pm),borderColor:pcol(i),backgroundColor:pcol(i),borderWidth:2,pointRadius:0,pointHoverRadius:4,tension:.28,spanGaps:true};});
    mkChart("perf",$("#cvPerf"),{type:"line",data:{labels:days,datasets:ds},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"nearest",intersect:false},
      onClick:function(e,els,ch){if(els.length){var r=rows[els[0].datasetIndex]; if(r) openDrawer(r.it);}},
      plugins:{legend:{display:true,position:"bottom",labels:{color:t.muted,usePointStyle:true,boxWidth:8,padding:12}},
        tooltip:Object.assign(tip(),{callbacks:{title:function(i){return i[0].dataset.label;},label:function(i){return i.label+": "+pFmt(S.pm,i.parsed.y,false);}}})},
      scales:{x:axes({xTicks:9}).x,y:Object.assign(axes({yFmt:function(v){return pFmt(S.pm,v,true);}}).y,{beginAtZero:kind!=="x"&&kind!=="pct"})}}});
  }
  $("#pmSeg").onclick=function(e){var b=e.target.closest("[data-m]"); if(b&&b.dataset.m!==S.pm){S.pm=b.dataset.m;paintPerf();writeHash();}};
  var pn=$("#pnSeg"); if(pn) pn.onclick=function(e){var b=e.target.closest("[data-n]"); if(b){S.pn=+b.dataset.n;paintPerf();writeHash();}};
  $$("#perf tbody tr[data-id]").forEach(function(r){r.onclick=function(){var it=S.items.filter(function(i){return i.id===r.dataset.id&&!i.other;})[0]; if(it) openDrawer(it);};});
}

/* Groups: where the money goes and what comes back */
function paintGroups(){
  var el=$("#groups"), st=A.segTotals(S.items), t=theme(), c=S.cls.ctx;
  var costSum=0, retSum=0; A.SEGS.forEach(function(s){costSum+=st[s.key].n[COST];retSum+=c.hasValue?st[s.key].n[VAL]:st[s.key].n[CONV];});
  var rows=A.SEGS.map(function(s){
    var n=st[s.key], r=c.hasValue?n.n[VAL]:n.n[CONV];
    return '<li class="click" data-f="'+s.key+'"><i style="background:'+col(SEG_COLOR[s.key])+'"></i><span class="n">'+esc(s.label)+'</span><span class="p">'+n.count.toLocaleString("en-US")+'</span>'+
      '<span class="v">'+esc(money(n.n[COST],true))+'</span><span class="bar"><s style="width:'+(costSum?(n.n[COST]/costSum*100).toFixed(1):0)+'%;background:'+col(SEG_COLOR[s.key])+'"></s></span>'+
      '<small>'+esc(s.hint)+(retSum?' · brings '+pctStr(r/retSum,0)+' of '+(c.hasValue?"revenue":"conversions"):'')+'</small></li>';
  }).join("");
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">Spend against return, by group</span><span class="ps-sub">Both bars add to 100%. A group that is wide under Spend but thin under '+(c.hasValue?"Revenue":"Conversions")+' costs more than it returns.</span></div></div>'+
    '<div class="ps-wrap ps-wrap-sm pa-gw"><canvas id="cvGroups" aria-label="Share of spend and of '+(c.hasValue?"revenue":"conversions")+' by group"></canvas></div>'+
    '<ul class="ps-legend pa-legend">'+rows+'</ul>';
  var labels=["Spend",c.hasValue?"Revenue":"Conversions"];
  mkChart("groups",$("#cvGroups"),{type:"bar",data:{labels:labels,datasets:A.SEGS.map(function(s){
    var n=st[s.key];
    return {label:s.label,backgroundColor:col(SEG_COLOR[s.key]),borderWidth:0,borderRadius:4,barPercentage:.7,
      data:[costSum?n.n[COST]/costSum*100:0,retSum?(c.hasValue?n.n[VAL]:n.n[CONV])/retSum*100:0],raw:[n.n[COST],c.hasValue?n.n[VAL]:n.n[CONV]]};})},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:Object.assign(tip(),{callbacks:{label:function(i){
        var raw=i.dataset.raw[i.dataIndex], isMoney=i.dataIndex===0||c.hasValue;
        return i.dataset.label+": "+i.parsed.x.toFixed(1)+"% ("+(isMoney?money(raw):Math.round(raw).toLocaleString("en-US"))+")";}}})},
      scales:{x:{stacked:true,max:100,grid:{color:t.line},border:{display:false},ticks:{color:t.muted,callback:function(v){return v+"%";}}},
              y:{stacked:true,grid:{display:false},border:{display:false},ticks:{color:t.ink2,font:{weight:600}}}}}});
  $$("#groups li[data-f]").forEach(function(li){li.onclick=function(){setSeg(li.dataset.f,true);};});
}

/* Bubble map: spend against return */
function paintBubble(){
  var el=$("#bubble"), c=S.cls.ctx, t=theme(), hasV=c.hasValue, yLabel=hasV?"ROAS":"Conversions";
  var pts=S.items.filter(function(i){return !i.other&&i.n[COST]>0;}).sort(function(a,b){return b.n[COST]-a.n[COST];}).slice(0,500);
  if(pts.length<2){el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">Every product, mapped</span></div></div><p class="ps-msg-inline">Not enough products with spend to map.</p>';destroyChart("bubble");return;}
  var maxV=Math.max.apply(null,pts.map(function(i){return hasV?i.n[VAL]:i.n[CONV];}))||1;
  /* A feed where a few products spend 30 times what a typical one does piles everything at the left edge on a straight scale. */
  var spends=pts.map(function(i){return i.n[COST];}).sort(function(a,b){return a-b;}), useLog=S.log!=null?S.log:spends[spends.length-1]/(spends[Math.floor(spends.length/2)]||1)>=30;
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">Every product, mapped</span>'+
    '<span class="ps-sub">Spend across, '+(hasV?"ROAS":"conversions")+' up. Bigger bubbles earn more. Click one to open it.</span></div>'+
    '<div class="ps-card-actions"><label class="ps-switch"><input type="checkbox" id="bLog"'+(useLog?" checked":"")+'><i></i>Log spend scale</label></div></div>'+
    '<div class="ps-wrap"><canvas id="cvBubble" aria-label="Spend against '+yLabel+' for each product"></canvas></div>'+
    '<p class="ps-note">Top '+pts.length+' products by spend. The dashed line is '+(hasV&&c.target?'the target, '+c.target.toFixed(2)+'x':'the account average')+'.</p>';
  var byseg={}; A.SEGS.forEach(function(s){byseg[s.key]=[];});
  pts.forEach(function(i){
    var yv=hasV?(i.n[COST]?i.n[VAL]/i.n[COST]:0):i.n[CONV], r=4+Math.sqrt((hasV?i.n[VAL]:i.n[CONV])/maxV)*17;
    byseg[i.seg].push({x:i.n[COST],y:yv,r:r,it:i});
  });
  var line=hasV&&c.target?c.target:null, maxX=Math.max.apply(null,pts.map(function(i){return i.n[COST];}));
  var guide={id:"paGuide",afterDatasetsDraw:function(ch){
    if(!line) return; var y=ch.scales.y.getPixelForValue(line), ar=ch.chartArea; if(y<ar.top||y>ar.bottom) return;
    var cx=ch.ctx; cx.save(); cx.strokeStyle=theme().faint; cx.setLineDash([5,5]); cx.lineWidth=1.2; cx.beginPath(); cx.moveTo(ar.left,y); cx.lineTo(ar.right,y); cx.stroke(); cx.restore();
  }};
  var ch=mkChart("bubble",$("#cvBubble"),{type:"bubble",plugins:[guide],data:{datasets:A.SEGS.filter(function(s){return byseg[s.key].length;}).map(function(s){
    return {label:s.label,data:byseg[s.key],backgroundColor:alpha(SEG_COLOR[s.key],.55),borderColor:col(SEG_COLOR[s.key]),borderWidth:1.2,hoverBorderWidth:2.5};})},
    options:{responsive:true,maintainAspectRatio:false,onClick:function(e,els){if(els.length){var d=ch.data.datasets[els[0].datasetIndex].data[els[0].index]; openDrawer(d.it);}},
      onHover:function(e,els){e.native.target.style.cursor=els.length?"pointer":"default";},
      plugins:{legend:{display:true,position:"bottom",labels:{color:t.muted,usePointStyle:true,boxWidth:8,padding:14}},
        tooltip:Object.assign(tip(),{callbacks:{title:function(i){return trunc(i[0].raw.it.title,60);},
          label:function(i){var it=i.raw.it, n=it.n; return [money(n[COST])+" spent, "+(hasV?money(n[VAL])+" revenue":Math.round(n[CONV])+" conversions"),
            hasV?(n[COST]?(n[VAL]/n[COST]).toFixed(2)+"x ROAS":""):""].filter(Boolean);}}})},
      scales:{x:Object.assign(axes({xTitle:"Spend",xType:useLog?"logarithmic":"linear",yTitle:yLabel,yFmt:function(v){return hasV?v+"x":v;}}).x,{ticks:{color:t.muted,callback:function(v){return money(v,true);},maxTicksLimit:7}}),
              y:axes({yTitle:yLabel,yFmt:function(v){return hasV?v+"x":v;}}).y}}});
  void maxX;
  $("#bLog").onchange=function(){S.log=this.checked;paintBubble();writeHash();};
}

/* Brands and product types */
function paintRollup(){
  var el=$("#rollup"), c=S.cls.ctx, t=theme(), list=A.rollup(S.items,S.by,10);
  var rms=[["cost","Spend"]].concat(c.hasValue?[["value","Revenue"],["roas","ROAS"]]:[["conv","Conversions"]]);
  if(!rms.some(function(r){return r[0]===S.rm;})) S.rm="cost";
  var haveType=S.items.some(function(i){return i.t1;}), haveBrand=S.items.some(function(i){return i.brand;});
  var bys=Object.keys(A.BY).filter(function(k){return k==="brand"?haveBrand:haveType;});
  if(bys.length&&bys.indexOf(S.by)<0){S.by=bys[0];list=A.rollup(S.items,S.by,10);}
  if(!bys.length){destroyChart("rollup");el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">By brand and product type</span></div></div><p class="ps-msg-inline">The feed has no brand or product type set, so there is nothing to group by. Add them in Merchant Center to see this.</p>';return;}
  function val(a){var n=a.n; return S.rm==="value"?n[VAL]:S.rm==="roas"?(n[COST]?n[VAL]/n[COST]:0):S.rm==="conv"?n[CONV]:n[COST];}
  var sorted=list.slice().sort(function(a,b){return (a.rest?1:0)-(b.rest?1:0)||val(b)-val(a);});
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">By '+esc(A.BY[S.by].toLowerCase())+'</span><span class="ps-sub">Top 10 by spend, the rest folded together</span></div>'+
    '<div class="ps-card-actions"><div class="ps-seg ps-seg-s" id="rbBy" role="radiogroup" aria-label="Group by">'+bys.map(function(k){return '<button type="button" role="radio" data-by="'+k+'" aria-checked="'+(S.by===k)+'">'+esc(A.BY[k])+'</button>';}).join("")+'</div>'+
    '<div class="ps-seg ps-seg-s" id="rbM" role="radiogroup" aria-label="Measure">'+rms.map(function(r){return '<button type="button" role="radio" data-m="'+r[0]+'" aria-checked="'+(S.rm===r[0])+'">'+r[1]+'</button>';}).join("")+'</div></div></div>'+
    '<div class="ps-wrap pa-rw" style="height:'+Math.max(260,sorted.length*34+50)+'px"><canvas id="cvRollup" aria-label="Brands or product types"></canvas></div>';
  var color=S.rm==="cost"?"#fbbf24":S.rm==="value"?"#34d399":S.rm==="conv"?"#a3e635":"#38bdf8";
  var tgt=S.rm==="roas"&&c.target?c.target:null;
  var guide={id:"paG2",afterDatasetsDraw:function(ch){
    if(!tgt) return; var x=ch.scales.x.getPixelForValue(tgt), ar=ch.chartArea; if(x<ar.left||x>ar.right) return;
    var cx=ch.ctx; cx.save(); cx.strokeStyle=theme().faint; cx.setLineDash([5,5]); cx.beginPath(); cx.moveTo(x,ar.top); cx.lineTo(x,ar.bottom); cx.stroke(); cx.restore();
  }};
  mkChart("rollup",$("#cvRollup"),{type:"bar",plugins:[guide],data:{labels:sorted.map(function(a){return trunc(a.key,26);}),datasets:[{data:sorted.map(val),
    backgroundColor:sorted.map(function(a){return a.rest?alpha("#8b94be",.55):alpha(color,.8);}),borderRadius:6,borderWidth:0,barPercentage:.78}]},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:Object.assign(tip(),{callbacks:{title:function(i){return sorted[i[0].dataIndex].key;},
      label:function(i){var a=sorted[i.dataIndex], n=a.n; return [money(n[COST])+" spent"+(c.hasValue?", "+money(n[VAL])+" revenue":", "+Math.round(n[CONV])+" conversions"),
        (c.hasValue&&n[COST]?(n[VAL]/n[COST]).toFixed(2)+"x ROAS, ":"")+a.count.toLocaleString("en-US")+" products"];}}})},
      scales:{x:{grid:{color:t.line},border:{display:false},ticks:{color:t.muted,callback:function(v){return S.rm==="roas"?v+"x":S.rm==="conv"?v:money(v,true);}}},
              y:{grid:{display:false},border:{display:false},ticks:{color:t.ink2}}}}});
  $("#rbBy").onclick=function(e){var b=e.target.closest("[data-by]"); if(b){S.by=b.dataset.by;paintRollup();writeHash();}};
  $("#rbM").onclick=function(e){var b=e.target.closest("[data-m]"); if(b){S.rm=b.dataset.m;paintRollup();writeHash();}};
}

/* Daily trend */
function paintTrend(){
  var el=$("#trend"), c=S.cls.ctx, t=theme(), d=S.data.daily;
  if(!d||!d.days||!d.days.length){el.innerHTML="";destroyChart("trend");return;}
  var rows=d.rows.map(A.asTotal), grain=d.days.length>120?"month":d.days.length>45?"week":"day";
  var b=P.bucket(d.days,rows,grain), cb=null;
  if(S.cmpData&&S.cmpData.daily&&S.cmpData.daily.days.length){cb=P.bucket(S.cmpData.daily.days,S.cmpData.daily.rows.map(A.asTotal),grain);}
  var key=c.hasValue?"value":"conversions", main=b.totals.map(function(x){return P.mval(key,x);}), cost=b.totals.map(function(x){return x[A.COST];});
  var prev=cb?cb.totals.map(function(x){return P.mval(key,x);}):null;
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">Spend and '+(c.hasValue?"revenue":"conversions")+' over time</span>'+
    '<span class="ps-sub">'+(grain==="day"?"Each day":grain==="week"?"Each week":"Each month")+(S.acct&&S.acct.platform==="meta"?', all Sales campaigns together (Meta cannot split a trend by product, so this is more than the catalog products below)':
      S.acct&&S.acct.platform==="both"?', all Google products and Meta Sales campaigns together (the Meta part is every Sales campaign, not only catalog products)':', all products together')+(cb?'. The dashed line is the comparison period.':'')+'</span></div></div>'+
    '<div class="ps-wrap"><canvas id="cvTrend" aria-label="Spend and '+(c.hasValue?"revenue":"conversions")+' over time"></canvas></div>';
  var ds=[{type:"bar",label:"Spend",data:cost,backgroundColor:alpha("#fbbf24",.55),borderRadius:4,borderWidth:0,yAxisID:"y",order:3},
    {type:"line",label:c.hasValue?"Revenue":"Conversions",data:main,borderColor:col("#34d399"),backgroundColor:"transparent",borderWidth:2.4,pointRadius:0,pointHoverRadius:5,tension:.3,yAxisID:"y2",order:1}];
  if(prev) ds.push({type:"line",label:(c.hasValue?"Revenue":"Conversions")+", before",data:main.map(function(_,i){return i<prev.length?prev[i]:null;}),borderColor:alpha("#34d399",.6),borderDash:[5,5],borderWidth:1.8,pointRadius:0,tension:.3,yAxisID:"y2",order:2});
  mkChart("trend",$("#cvTrend"),{data:{labels:b.labels,datasets:ds},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
    plugins:{legend:{display:true,position:"bottom",labels:{color:t.muted,usePointStyle:true,boxWidth:8,padding:14}},tooltip:Object.assign(tip(),{callbacks:{label:function(i){
      var v=i.parsed.y; return i.dataset.label+": "+(i.dataset.yAxisID==="y2"&&!c.hasValue?Math.round(v*10)/10:money(v));}}})},
    scales:{x:axes({xTicks:9}).x,y:Object.assign(axes({yFmt:function(v){return money(v,true);}}).y,{position:"left"}),
      y2:{position:"right",beginAtZero:true,grid:{display:false},border:{display:false},ticks:{color:t.muted,callback:function(v){return c.hasValue?money(v,true):v;}}}}}});
}

/* What moved */
function paintMovers(){
  var sec=$("#moversSec"), el=$("#movers"), c=S.cls.ctx, mv=A.movers(S.items,S.cmpItems,c,8);
  if(!mv){sec.hidden=true;return;}
  sec.hidden=false;
  $("#moversHint").textContent=(mv.newCount?mv.newCount+" new "+(mv.newCount===1?"product":"products")+" with spend. ":"")+(mv.goneCount?mv.goneCount+" stopped "+(mv.goneCount===1?"showing":"showing")+". ":"")+"Change in "+(c.hasValue?"revenue":"conversions")+" against the comparison period.";
  function list(title,sub,rows,tone){
    return '<section class="pm-panel ps-card"><div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+title+'</span><span class="ps-sub">'+sub+'</span></div></div>'+
      (rows.length?'<ol class="pa-mv">'+rows.map(function(r){
        var dv=c.hasValue?money(Math.abs(r.delta)):Math.round(Math.abs(r.delta))+" conv.", sign=r.delta>0?"+":"−";
        var sub=(r.isNew?"New this period":r.gone?"Not shown this period":"was "+(c.hasValue?money(r.prev[VAL]):Math.round(r.prev[CONV]))+", now "+(c.hasValue?money(r.cur[VAL]):Math.round(r.cur[CONV])));
        return '<li data-id="'+esc(r.id)+'"><span class="t"><b>'+esc(trunc(r.title,70))+'</b><small>'+esc(sub)+'</small></span><span class="v '+tone+'">'+sign+esc(dv)+'</span></li>';}).join("")+'</ol>'
       :'<p class="ps-msg-inline">None.</p>')+'</section>';
  }
  el.innerHTML=list("Biggest gains","Products earning more than before",mv.up,"good")+list("Biggest drops","Products earning less than before",mv.down,"bad");
  $$("#movers li[data-id]").forEach(function(li){li.onclick=function(){var it=S.items.filter(function(i){return i.id===li.dataset.id;})[0]; if(it) openDrawer(it); else toast("That product had no spend in this range");};});
}

/* The product table */
function tableRows(){
  var f=A.filterItems(S.items,S.seg,S.cls.ctx,S.oos?new Set(S.oos):null);
  f=A.search(f,S.q);
  return A.sortItems(f,S.sort,S.dir);
}
function segChips(){
  var st=A.segTotals(S.items), all=S.items.filter(function(i){return !i.other;}).length;
  var chips=[{k:"all",l:"All",n:all,c:null}].concat(A.SEGS.map(function(s){return {k:s.key,l:s.label,n:st[s.key].count,c:SEG_COLOR[s.key]};}));
  chips.push({k:"gem",l:"Underfunded stars",n:A.filterItems(S.items,"gem",S.cls.ctx).length,c:"#34d399"});
  if(S.oosKnown) chips.push({k:"oos",l:"Out of stock",n:A.filterItems(S.items,"oos",S.cls.ctx,new Set(S.oos)).length,c:"#fb7185"});
  return chips;
}
function paintTable(){
  var el=$("#prods"), c=S.cls.ctx, rows=tableRows(), shown=rows.slice(0,S.shown), tot=A.zero();
  rows.forEach(function(r){A.addTo(tot,r.n);});
  var chips=segChips().map(function(x){
    return '<button type="button" class="pa-chip'+(S.seg===x.k?" on":"")+'" data-f="'+x.k+'"'+(x.c?' style="--cc:'+col(x.c)+'"':"")+'>'+(x.c?'<i></i>':"")+esc(x.l)+'<b>'+x.n.toLocaleString("en-US")+'</b></button>';}).join("");
  var cols=A.COLS.filter(function(k){return k.key!=="title";});
  var th='<th class="name sortable'+(S.sort==="title"?" sorted":"")+'" data-sort="title">Product'+(S.sort==="title"?'<i>'+(S.dir>0?"▲":"▼")+'</i>':"")+'</th><th class="grp">Group</th>'+cols.map(function(k){
    if(k.key==="value"&&!c.hasValue) return "";
    if(k.key==="roas"&&!c.hasValue) return "";
    return '<th class="num sortable'+(S.sort===k.key?" sorted":"")+'" data-sort="'+k.key+'">'+esc(k.label)+(S.sort===k.key?'<i>'+(S.dir>0?"▲":"▼")+'</i>':"")+'</th>';}).join("");
  function cell(r,k){
    var t=A.asTotal(r.n), v=k.key==="value"?r.n[VAL]:P.mval(k.key,t);
    return '<td class="num">'+esc(fmtK(k.kind,v,k.key==="cost"||k.key==="value"||k.key==="impressions"||k.key==="clicks"))+'</td>';
  }
  var body=shown.map(function(r){
    var seg=r.seg?A.SEG[r.seg]:null, oos=S.oos&&S.oos.indexOf(r.id)>=0;
    return '<tr data-id="'+esc(r.id)+'"'+(r.other?' class="other"':"")+'><td class="name"><span class="cn">'+esc(trunc(r.title,90))+'</span>'+
      '<span class="cm">'+(r.id?esc(r.id):"")+(r.brand?' · '+esc(r.brand):"")+(r.type?' · '+esc(trunc(r.type,40)):"")+(oos?' · <em class="oos">out of stock</em>':"")+'</span></td>'+
      '<td class="grp">'+(seg?'<span class="pa-badge" style="--cc:'+col(SEG_COLOR[seg.key])+'">'+esc(seg.label)+(r.gem?" ★":"")+'</span>':"")+'</td>'+
      cols.map(function(k){return (k.key==="value"||k.key==="roas")&&!c.hasValue?"":cell(r,k);}).join("")+'</tr>';
  }).join("");
  var totRow='<tr class="tot"><td class="name"><span class="cn">'+(S.seg!=="all"||S.q?"Matching products":"All products")+' ('+rows.length.toLocaleString("en-US")+')</span></td><td></td>'+
    cols.map(function(k){return (k.key==="value"||k.key==="roas")&&!c.hasValue?"":cell({n:tot},k);}).join("")+'</tr>';
  el.innerHTML='<div class="ps-tools"><label class="ps-search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m20 20-4-4"/></svg><input id="pq" placeholder="Find a product, brand or item id…" value="'+esc(S.q)+'" autocomplete="off"></label>'+
    '<div class="pa-chips" role="group" aria-label="Show">'+chips+'</div></div>'+
    '<div class="ps-tscroll"><table class="ps-table pa-table"><thead><tr>'+th+'</tr></thead><tbody>'+
    (body||'<tr><td colspan="12" class="empty">No products match.</td></tr>')+'</tbody><tfoot>'+totRow+'</tfoot></table></div>'+
    (rows.length>S.shown?'<button type="button" class="ps-more" id="moreRows">Show '+Math.min(PAGE*2,rows.length-S.shown)+' more of '+(rows.length-S.shown).toLocaleString("en-US")+' left</button>':'');
  var folded=S.items.filter(function(i){return i.other;})[0];
  $("#prodHint").textContent=(folded?"Top "+(S.items.length-1).toLocaleString("en-US")+" by spend shown one by one; "+folded.count.toLocaleString("en-US")+" smaller products are folded into the last row. ":"")+"Click a product for its detail.";
  $("#pq").oninput=function(){S.q=this.value;S.shown=PAGE;var pos=this.selectionStart;paintTable();var n=$("#pq");n.focus();n.setSelectionRange(pos,pos);writeHash();};
  $$(".pa-chip",el).forEach(function(b){b.onclick=function(){setSeg(b.dataset.f,false);};});
  var more=$("#moreRows"); if(more) more.onclick=function(){S.shown+=PAGE*2;paintTable();};
  $$("th[data-sort]",el).forEach(function(h){h.onclick=function(){var k=h.dataset.sort; if(S.sort===k) S.dir*=-1; else{S.sort=k;S.dir=k==="title"?1:-1;} S.shown=PAGE; paintTable(); writeHash();};});
  $$("tbody tr[data-id]",el).forEach(function(r){r.onclick=function(){
    var it=S.items.filter(function(i){return i.id===r.dataset.id&&!i.other;})[0]; if(it) openDrawer(it);};});
}

/* One product, in a side panel */
function whyLine(it){
  var c=S.cls.ctx, n=it.n, seg=it.seg, e=A.efficiency(n,c);
  if(seg==="waste") return "Spent "+money(n[COST])+" over "+Math.round(n[CLK]).toLocaleString("en-US")+" clicks and sold nothing. One typical sale on this account costs "+(c.acctCpa?money(c.acctCpa):"more than that")+".";
  if(seg==="thin") return "Only "+Math.round(n[CLK])+" clicks, under the "+c.minClicks+" needed to judge it. Give it more time or a longer range.";
  if(seg==="steady"||seg==="star"||seg==="drag"){
    if(c.hasValue&&c.target&&e!=null) return "Returns "+(n[COST]?(n[VAL]/n[COST]).toFixed(2):"0")+"x, "+(e>=1?pctStr(e-1,0)+" above":pctStr(1-e,0)+" below")+" the "+c.target.toFixed(2)+"x target."+(it.gem?" It earns twice its share of the budget, so it is a candidate for more.":"");
    if(e!=null) return "Each conversion costs "+money(n[COST]/n[CONV])+" against "+money(c.acctCpa)+" for the account.";
  }
  return "";
}
function openDrawer(it){
  var el=$("#drawer"), c=S.cls.ctx, n=it.n, pm=S.cmpItems.filter(function(i){return i.id===it.id&&!i.other;})[0], pn=pm?pm.n:null, seg=A.SEG[it.seg];
  S.open=it.id;
  var tiles=[["Spend","cost","money"],["Revenue","value","money"],["ROAS","roas","x"],["Conversions","conversions","num"],["Cost / conv.","cpa","money2"],["Clicks","clicks","int"],
    ["Impressions","impressions","int"],["CTR","ctr","pct"],["Conv. rate","cvr","pct"],["Avg. CPC","cpc","money2"]].filter(function(x){return c.hasValue||(x[1]!=="value"&&x[1]!=="roas");});
  var oos=S.oos&&S.oos.indexOf(it.id)>=0;
  el.innerHTML='<div class="pa-dr-bg" data-close></div><aside class="pa-dr" role="dialog" aria-modal="true" aria-label="Product detail"><button type="button" class="pa-dr-x" data-close aria-label="Close">×</button>'+
    '<span class="pa-badge" style="--cc:'+col(SEG_COLOR[it.seg]||"#8b94be")+'">'+esc(seg?seg.label:"")+(it.gem?" ★ underfunded":"")+'</span>'+(oos?' <span class="pa-badge" style="--cc:'+col("#fb7185")+'">Out of stock</span>':"")+
    '<h3>'+esc(it.title)+'</h3><p class="pa-dr-m">'+esc(it.id)+(it.brand?" · "+esc(it.brand):"")+(it.type?" · "+esc(it.type):"")+'</p>'+
    (whyLine(it)?'<p class="pa-dr-why">'+esc(whyLine(it))+'</p>':"")+
    '<div class="pa-dr-g">'+tiles.map(function(x){
      var t=A.asTotal(n), v=x[1]==="value"?n[VAL]:P.mval(x[1],t), pv=pn?(x[1]==="value"?pn[VAL]:P.mval(x[1],A.asTotal(pn))):null;
      var ch=pn&&v!=null&&pv!=null?P.change(x[1],v,pv):null;
      return '<div class="pa-dr-t"><span>'+esc(x[0])+'</span><b>'+esc(fmtK(x[2],v,x[1]==="cost"||x[1]==="value"))+'</b>'+(ch?'<small class="d '+ch.tone+'">'+esc(ch.text)+'</small>':'<small>&nbsp;</small>')+'</div>';}).join("")+'</div>'+
    (A.seriesValues(S.data.series,it.id,"cost")?'<div class="pa-dr-ch"><span class="pa-dr-h">Day by day</span><div class="pa-dr-cw"><canvas id="cvDr" aria-label="Spend and '+(c.hasValue?"revenue":"conversions")+' by day for this product"></canvas></div></div>':
      '<p class="pa-dr-f">Daily numbers are read for the 20 products that spend the most.</p>')+
    '<p class="pa-dr-f">'+(pn?"Changes are against the comparison period.":"No comparison data for this product.")+'</p>'+
    '<button type="button" class="pm-btn-ghost" id="drCopy"><span>Copy item id</span></button></aside>';
  el.hidden=false; el.setAttribute("aria-hidden","false"); document.body.classList.add("pa-lock");
  $$("[data-close]",el).forEach(function(b){b.onclick=closeDrawer;});
  $("#drCopy").onclick=function(){(navigator.clipboard?navigator.clipboard.writeText(it.id):Promise.reject()).then(function(){toast("Item id copied");},function(){toast(it.id);});};
  var x=$(".pa-dr-x",el); if(x) x.focus();
  destroyChart("drawer");
  var dc=$("#cvDr"), sp=A.seriesValues(S.data.series,it.id,"cost");
  if(dc&&sp){
    var rv=A.seriesValues(S.data.series,it.id,c.hasValue?"value":"conversions"), t=theme();
    mkChart("drawer",dc,{data:{labels:S.data.series.days.map(P.dayLabel),datasets:[
      {type:"bar",label:"Spend",data:sp,backgroundColor:alpha("#fbbf24",.6),borderRadius:3,borderWidth:0,yAxisID:"y"},
      {type:"line",label:c.hasValue?"Revenue":"Conversions",data:rv,borderColor:col("#34d399"),backgroundColor:"transparent",borderWidth:2,pointRadius:0,tension:.3,yAxisID:"y2"}]},
      options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
        plugins:{legend:{display:true,position:"bottom",labels:{color:t.muted,usePointStyle:true,boxWidth:8,padding:10}},tooltip:tip()},
        scales:{x:axes({xTicks:6}).x,y:Object.assign(axes({yFmt:function(v){return money(v,true);}}).y,{position:"left"}),
          y2:{position:"right",beginAtZero:true,grid:{display:false},border:{display:false},ticks:{color:t.muted,callback:function(v){return c.hasValue?money(v,true):v;}}}}}});
  }
}
function closeDrawer(){destroyChart("drawer"); var el=$("#drawer"); el.hidden=true; el.setAttribute("aria-hidden","true"); el.innerHTML=""; document.body.classList.remove("pa-lock"); S.open="";}
document.addEventListener("keydown",function(e){if(e.key==="Escape"&&!$("#drawer").hidden) closeDrawer();});

/* ── Boot ─────────────────────────────────────────────────────── */
new MutationObserver(function(){if(S.data&&!empty()) paintAll();}).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});

function lastAccount(){try{return localStorage.getItem("ps_account")||"";}catch(e){return "";}}
function boot(){
  loadRules();
  var h=readHash(); applyHash(h);
  var firstLoad=true;
  accts=PL.accounts($("#acctPick"),{url:BASE+"/accounts",allow:["google","meta","both"],platform:h.pf,account:h.a||lastAccount(),
    onLoaded:function(list,j){S.accounts=accts.accounts(); paintRanges(); computeRanges();},
    onChange:function(a,platform){
      S.accounts=accts.accounts();
      if(!a){
        S.acct=null; $("#dash").hidden=true; $("#liveText").textContent="No account chosen";
        notice("warn","No account chosen",(platform==="meta"?"There are no Meta ad accounts this token can read yet.":"There are no accounts to show for this platform.")+" Pick another platform or account above."); return;
      }
      if(S.acct&&a.id===S.acct.id&&!firstLoad) return;
      firstLoad=false;
      S.acct=a; S.data=S.cmpData=null;
      try{localStorage.setItem("ps_account",a.id);}catch(e){}
      load();
    },
    onError:showError});
  paintRanges();
}
S.range=P.presetRange(S.preset);
boot();
})();
