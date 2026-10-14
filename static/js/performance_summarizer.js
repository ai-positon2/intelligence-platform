/* Performance Summarizer (Paid Marketing Agents).
 *
 * Live Google Ads numbers for one account at a time, drawn so the reader
 * decides what to look at: any metric (or several) against any other, split
 * by day, week, month, weekday, hour, campaign, campaign type, device or
 * network, with the previous period or last year laid over it, and any
 * campaign selection filtering the whole page. The server only sends rows
 * (tracker/paid_gads.py); every figure is worked out in ps_data.js.
 *
 * The view is the URL (#a=account&p=range&m=metrics&g=grouping ...), so a
 * link reproduces exactly what you were looking at. Charts the reader pins
 * are kept in this browser only.
 */
(function(){
"use strict";

var P = window.PSData, BASE = "/p2/paid-marketing/api/gads";
var $ = function(s,r){return (r||document).querySelector(s);};
var $$ = function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s));};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});};
var reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

var SPLITS = ["cost","impressions","clicks","conversions","value"];

var S = {
  accounts:[], acct:null, preset:"last30", range:null, compare:"prev", cmpRange:null,
  cur:null, cmp:null, sis:null, extras:null, shares:{cur:null,cmp:null}, missing:[], kpis:P.KPI_DEFAULT.slice(),
  filter:[],
  ex:{metrics:["cost","conversions"],dim:"day",type:"area",compare:true,accounts:[],index:false,series:""},
  studio:{metrics:["conversions","cost"],dim:"campaign",type:"hbar",compare:false,accounts:[],index:false,series:""},
  split:"cost", heat:"conversions", scat:{x:"cost",y:"conversions",size:"clicks"},
  table:{sort:"cost",dir:-1,q:"",cols:null,trend:"cost",all:false},
  board:[], seq:0, other:{}, memo:{}, cd:null
};
var explorer = null, studioCard = null, boardCards = [], charts = {}, picker = null;

/* ── Small helpers ───────────────────────────────────────────── */
function getJson(url){
  if(S.memo[url]) return S.memo[url];
  var p = fetch(url,{credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){
      if(!r.ok){var e=new Error(j.error||("The server answered "+r.status)); e.status=r.status; e.config=j.configProblem; throw e;}
      return j;
    });
  });
  S.memo[url] = p;
  p.catch(function(){delete S.memo[url];});
  return p;
}
function alpha(hex,a){
  var n=parseInt(hex.slice(1),16);
  return "rgba("+(n>>16&255)+","+(n>>8&255)+","+(n&255)+","+a+")";
}
function light(){return document.documentElement.getAttribute("data-theme")==="light";}

/* On a white page the bright dark-theme colours wash out, so charts use a deeper shade of each. */
function shade(hex,k){var n=parseInt(hex.slice(1),16);function c(v){return Math.round(v*k).toString(16).padStart(2,"0");}return "#"+c(n>>16&255)+c(n>>8&255)+c(n&255);}
function mcol(m){var c=P.METRIC_COLOR[m]; return light()?shade(c,.72):c;}
function scol(i){var c=P.SERIES[i%P.SERIES.length]; return light()?shade(c,.72):c;}
function tcol(k){var c=TYPE_COL[k]||"#8b94be"; return light()?shade(c,.72):c;}
function theme(){
  var cs=getComputedStyle($("#ps")), g=function(n,d){return (cs.getPropertyValue(n)||"").trim()||d;};
  return {ink:g("--pm-ink","#e8ecf8"),ink2:g("--pm-ink2","#c4cbe4"),muted:g("--pm-muted","#8b94be"),faint:g("--pm-faint","#5d6688"),
    line:g("--pm-line","rgba(255,255,255,.07)"),line2:g("--pm-line2","rgba(255,255,255,.12)"),
    panel:light()?"#ffffff":"#0c0f1c",accent:g("--pm-a","#a3e635")};
}
function cur(){return S.acct?S.acct.currency:"USD";}
function fm(key,v,small){return P.fmtMetric(key,v,cur(),small);}
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
function setFilter(ids){S.filter=ids.slice();refresh();}
function toggleIds(ids){
  var on=ids.every(function(i){return S.filter.indexOf(i)>=0;});
  setFilter(on?S.filter.filter(function(i){return ids.indexOf(i)<0;}):S.filter.concat(ids.filter(function(i){return S.filter.indexOf(i)<0;})));
}
function hasValue(){return !!(S.cur&&P.total(S.cur,null)[P.VAL]>0);}
function setCur(){return P.filterSet(S.cur,S.filter);}
function setCmp(){return S.cmp?P.filterSet(S.cmp,S.filter):null;}

/* ── A small floating menu (metric picker, grouping, export ...) ── */
var menu = null;
function closeMenu(){if(menu){menu.remove();menu=null;document.removeEventListener("mousedown",onDocDown,true);document.removeEventListener("keydown",onMenuKey,true);}}
function onDocDown(e){if(menu&&!menu.contains(e.target)&&!(menu._anchor&&menu._anchor.contains(e.target)))closeMenu();}
function onMenuKey(e){if(e.key==="Escape"){var a=menu&&menu._anchor;closeMenu();if(a)a.focus();}}
/* items: [{key,label,sub,color,checked,head,disabled}]; o.multi keeps it open and asks o.items() again. */
function popMenu(anchor,o){
  var same=menu&&menu._anchor===anchor; closeMenu(); if(same) return;
  var m=document.createElement("div"); m.className="ps-pop"; m.setAttribute("role","menu"); m._anchor=anchor;
  function paint(){
    var items=o.items();
    m.innerHTML=(o.search?'<input class="ps-pop-q" placeholder="'+esc(o.search)+'" autocomplete="off">':'')+'<div class="ps-pop-list">'+items.map(function(it){
      if(it.head) return '<div class="ps-pop-h">'+esc(it.head)+'</div>';
      return '<button type="button" role="menuitem" class="ps-pop-i'+(it.checked?" on":"")+'" data-k="'+esc(it.key)+'"'+(it.disabled?" disabled":"")+'>'+
        (it.color?'<i class="sw" style="background:'+it.color+'"></i>':'<i class="ck"></i>')+
        '<span class="t"><b>'+esc(it.label)+'</b>'+(it.sub?'<small>'+esc(it.sub)+'</small>':'')+'</span>'+
        (it.checked?'<svg class="tick" viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>':'')+'</button>';
    }).join("")+'</div>'+(o.footer?'<div class="ps-pop-f">'+o.footer.map(function(f){return '<button type="button" class="ps-pop-fb" data-f="'+esc(f.key)+'">'+esc(f.label)+'</button>';}).join("")+'</div>':"");
    var q=$(".ps-pop-q",m);
    if(q){q.oninput=function(){var t=q.value.trim().toLowerCase();$$(".ps-pop-i",m).forEach(function(b){b.hidden=!!t&&b.textContent.toLowerCase().indexOf(t)<0;});};}
  }
  paint();
  m.addEventListener("click",function(e){
    var fb=e.target.closest(".ps-pop-fb");
    if(fb){o.onFooter(fb.dataset.f); if(o.multi){var y0=$(".ps-pop-list",m).scrollTop; paint(); $(".ps-pop-list",m).scrollTop=y0;} else closeMenu(); return;}
    var b=e.target.closest(".ps-pop-i"); if(!b||b.disabled) return;
    o.onPick(b.dataset.k);
    if(o.multi){var y=$(".ps-pop-list",m).scrollTop; paint(); $(".ps-pop-list",m).scrollTop=y;} else closeMenu();
  });
  document.body.appendChild(m); menu=m;
  var r=anchor.getBoundingClientRect(), w=m.offsetWidth, h=m.offsetHeight;
  m.style.left=Math.max(8,Math.min(r.left,innerWidth-w-8))+"px";
  var below=r.bottom+6; m.style.top=(below+h>innerHeight-8&&r.top>h+14?r.top-h-6:below)+"px";
  setTimeout(function(){document.addEventListener("mousedown",onDocDown,true);document.addEventListener("keydown",onMenuKey,true);},0);
  var first=$(".ps-pop-q",m)||$(".ps-pop-i",m); if(first&&o.focus!==false) first.focus();
}


/* ── Updated, and Refresh ─────────────────────────────────────── */
function relTime(iso){
  var t=new Date(iso).getTime(); if(isNaN(t)) return "";
  var m=Math.max(0,Math.round((Date.now()-t)/60000));
  return m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
}
function paintUpdated(iso){
  S.fetchedAt=iso||null;
  var el=$("#upd"); if(!el) return;
  el.textContent=iso&&relTime(iso)?" · Updated "+relTime(iso):"";
  el.title=iso?"Last read from Google Ads "+new Date(iso).toLocaleString():"";
}
setInterval(function(){if(S.fetchedAt) paintUpdated(S.fetchedAt);},30000);
/* The same request as getJson, but asking Google again (the server allows it once every two minutes per account); the answer
   replaces the saved one in S.memo so the next load() paints it. */
function refreshJson(url){
  var p=fetch(url+(url.indexOf("?")<0?"?":"&")+"refresh=1",{credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){
      if(!r.ok){var e=new Error(j.error||("The server answered "+r.status)); e.status=r.status; e.cooldown=!!j.cooldown; throw e;}
      return j;
    });
  });
  S.memo[url]=p; p.catch(function(){delete S.memo[url];});
  return p;
}
function doRefresh(urls){
  var btn=$("#refreshBtn"); if(!S.acct||btn.classList.contains("busy")) return;
  btn.classList.add("busy"); btn.disabled=true;
  var main=refreshJson(urls[0]), rest=urls.slice(1).map(function(u){return refreshJson(u).catch(function(){return null;});});
  Promise.all([main].concat(rest)).then(function(){
    btn.classList.remove("busy"); btn.disabled=false; load(); toast("Read from Google Ads just now");
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
/* Google's daily allowance spent: say when live numbers return, and when a saved copy stands in for them. */
function paintStale(stale,blockedUntil){
  var el=$("#staleBar"), t=function(x){return new Date(x).toLocaleString([], {weekday:"short",hour:"numeric",minute:"2-digit"});};
  if(!stale&&!blockedUntil){el.hidden=true;el.innerHTML="";return;}
  el.hidden=false;
  el.innerHTML='<b>'+(stale?"Showing the copy saved "+esc(t(stale)):"Saved views still open")+'.</b> Google has used up this app\u2019s daily Google Ads allowance'+
    (blockedUntil?', so fresh numbers come back around <b>'+esc(t(blockedUntil*1000))+'</b>':'')+'.';
}
function showError(err){
  $("#dash").hidden=true;
  $("#liveText").textContent="Google Ads is not reachable";
  $("#live").classList.add("off");
  if(err.status===409) notice("setup","Google Ads is not connected yet",
    esc(err.message)+' An admin sets these on Railway (they are named <code>GOOGLE_ADS_*</code>), then reloads this page.');
  else if(err.status===429) notice("warn","Google Ads is not answering right now",esc(err.message));
  else notice("bad","Could not load Google Ads",esc(err.message));
}

/* ── Hash state: the URL is the view ──────────────────────────── */
var hashTimer=null;
function writeHash(){
  clearTimeout(hashTimer);
  hashTimer=setTimeout(function(){
    if(!S.acct) return;
    var q=[["a",S.acct.id],["p",S.preset]];
    if(S.preset==="custom") q.push(["s",S.range.start],["e",S.range.end]);
    q.push(["c",S.compare],["m",S.ex.metrics.join(",")],["g",S.ex.dim],["t",S.ex.type],["cm",S.ex.compare?1:0]);
    if(S.ex.series) q.push(["sr",S.ex.series]);
    if(S.filter.length) q.push(["f",S.filter.join(",")]);
    if(S.ex.accounts.length) q.push(["ac",S.ex.accounts.join(",")],["ix",S.ex.index?1:0]);
    q.push(["sp",S.split],["hm",S.heat],["sx",S.scat.x],["sy",S.scat.y],["ss",S.scat.size]);
    try{history.replaceState(null,"","#"+q.map(function(x){return x[0]+"="+encodeURIComponent(x[1]);}).join("&"));}catch(e){}
  },250);
}
function readHash(){
  var h={}; location.hash.replace(/^#/,"").split("&").forEach(function(p){
    var i=p.indexOf("="); if(i>0) h[p.slice(0,i)]=decodeURIComponent(p.slice(i+1));
  });
  return h;
}
function applyHash(h){
  if(h.p&&(P.PRESETS.some(function(x){return x.id===h.p;})||h.p==="custom")) S.preset=h.p;
  if(S.preset==="custom"&&/^\d{4}-\d{2}-\d{2}$/.test(h.s||"")&&/^\d{4}-\d{2}-\d{2}$/.test(h.e||"")&&h.s<=h.e) S.range={start:h.s,end:h.e};
  else if(S.preset==="custom") S.preset="last30";
  if(["prev","yoy","none"].indexOf(h.c)>=0) S.compare=h.c;
  var ms=(h.m||"").split(",").filter(function(m){return P.METRICS[m];}).slice(0,4);
  if(ms.length) S.ex.metrics=ms;
  if(P.DIM[h.g]) S.ex.dim=h.g;
  if(P.TYPE[h.t]) S.ex.type=h.t;
  if(["type","campaign"].indexOf(h.sr)>=0) S.ex.series=h.sr;
  if(h.cm!=null) S.ex.compare=h.cm!=="0";
  if(h.f) S.filter=h.f.split(",").filter(function(x){return /^\d+$/.test(x);}).slice(0,200);
  if(h.ac) S.ex.accounts=h.ac.split(",").filter(function(x){return /^\d{10}$/.test(x);}).slice(0,3);
  if(h.ix==="1") S.ex.index=true;
  if(SPLITS.indexOf(h.sp)>=0) S.split=h.sp;
  if(P.METRICS[h.hm]) S.heat=h.hm;
  ["x","y","size"].forEach(function(k){var v=h["s"+(k==="size"?"s":k)]; if(P.METRICS[v]) S.scat[k]=v;});
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
$("#exportBtn").addEventListener("click",function(){
  if(!S.cur) return;
  popMenu(this,{items:function(){return [{key:"daily",label:"Daily numbers",sub:"One row per day, every metric (CSV)"},
    {key:"camps",label:"Campaigns",sub:"One row per campaign for this range (CSV)"}];},
    onPick:function(k){exportCsv(k);}});
});

/* ── Loading ──────────────────────────────────────────────────── */
function skeleton(){
  $("#dash").hidden=false;
  $("#kpis").innerHTML=new Array(8).join('<div class="ps-skel ps-skel-kpi"></div>');
  $("#insights").innerHTML='<div class="ps-skel" style="height:360px"></div>';
}
function load(){
  var seq=++S.seq, q="account="+S.acct.id+"&start="+S.range.start+"&end="+S.range.end;
  if(S.cmpRange) q+="&compare_start="+S.cmpRange.start+"&compare_end="+S.cmpRange.end;
  notice("");
  skeleton();
  $("#liveText").textContent="Reading "+S.acct.name+" from Google Ads…";
  getJson(BASE+"/performance?"+q).then(function(j){
    if(seq!==S.seq) return;
    S.cur=j.current; S.cmp=j.compare; S.sis=j.current.sis||null; S.extras=null; S.cd=null;
    S.shares={cur:j.current.shares||null,cmp:(j.compare&&j.compare.shares)||null}; S.missing=j.current.missing||[];
    paintStale(j.stale,j.blockedUntil); paintUpdated(j.fetchedAt);
    var ids={}; S.cur.campaigns.forEach(function(c){ids[c.id]=1;});
    S.filter=S.filter.filter(function(i){return ids[i];});
    $("#live").classList.remove("off");
    $("#liveText").textContent="Live from Google Ads · "+S.accounts.length+" accounts · "+S.acct.name+" in "+S.acct.currency;
    $("#dash").hidden=false;
    paintAll();
    loadExtras(seq);
  }).catch(function(err){if(seq===S.seq) showError(err);});
}
$("#refreshBtn").addEventListener("click",function(){
  if(!S.acct||!S.range) return;
  var q="account="+S.acct.id+"&start="+S.range.start+"&end="+S.range.end, pq=q;
  if(S.cmpRange) pq+="&compare_start="+S.cmpRange.start+"&compare_end="+S.cmpRange.end;
  doRefresh([BASE+"/performance?"+pq,BASE+"/extras?"+q]);
});
function loadExtras(seq){
  getJson(BASE+"/extras?account="+S.acct.id+"&start="+S.range.start+"&end="+S.range.end).then(function(x){
    if(seq!==S.seq) return; S.extras=x; paintExtras();
  }).catch(function(err){
    if(seq!==S.seq) return; S.extras={error:err.message}; paintExtras();
  });
}

/* ── Painting ─────────────────────────────────────────────────── */
function empty(){
  var t=P.total(S.cur,null);
  return !t[P.COST]&&!t[P.IMPR]&&!t[P.CLK];
}
function paintAll(){
  var none=empty();
  $("#dash").classList.toggle("is-empty",none);
  if(none){
    notice("info","No activity in this range","<b>"+esc(S.acct.name)+"</b> had no impressions or spend from "+esc(fmtRange(S.range))+". Try a longer range or another account.");
    $("#kpis").innerHTML=""; $("#insights").innerHTML="";
    if(explorer){explorer.destroy();explorer=null;}
    if(studioCard){studioCard.destroy();studioCard=null;}
    boardCards.forEach(function(c){c.destroy();}); boardCards=[];
    ["#explorer","#studio","#splits","#heat","#scatter","#camps","#board"].forEach(function(s){$(s).innerHTML="";});
    $("#splits")._built=0; ["spDevice","spNetwork","spType","scatter"].forEach(destroyChart);
    paintSplitSeg(); paintFilter(); writeHash(); return;
  }
  notice("");
  paintFilter(); paintKpis(); paintExplorer(); paintInsights(); paintStudio(); paintBoard(); paintSplitSeg(); paintSplits(); paintHeat(); paintScatter(); paintTable();
  writeHash();
}
/* A filter or pin changed: repaint everything that depends on it, without asking Google again. */
function refresh(){
  if(!S.cur) return;
  paintFilter(); paintKpis(); if(explorer) explorer.update(); if(studioCard) studioCard.update(); paintInsights(); paintSplits(); paintHeat(); paintScatter(); paintTable();
  boardCards.forEach(function(c){c.update();}); writeHash();
}
function paintExtras(){
  if(explorer) explorer.update(); if(studioCard) studioCard.update(); boardCards.forEach(function(c){c.update();}); paintSplits(); paintHeat();
}

function paintFilter(){
  var el=$("#filterbar"), idx={}; S.cur.campaigns.forEach(function(c){idx[c.id]=c;});
  var live=S.filter.filter(function(i){return idx[i];});
  if(live.length!==S.filter.length) S.filter=live;
  if(!live.length){el.hidden=true;el.innerHTML="";return;}
  var names=live.slice(0,3).map(function(i){return '<span class="ps-fchip">'+esc(idx[i].name)+'</span>';}).join("");
  el.hidden=false;
  el.innerHTML='<span class="ps-f-l"><svg viewBox="0 0 24 24"><path d="M4 5h16l-6 8v5l-4 2v-7z"/></svg>Showing '+live.length+(live.length===1?" campaign":" campaigns")+'</span>'+
    names+(live.length>3?'<span class="ps-fchip mute">+'+(live.length-3)+' more</span>':'')+
    '<button type="button" class="ps-f-x" id="clearFilter">Show the whole account</button>';
  $("#clearFilter").onclick=function(){setFilter([]);};
}

/* Key number tiles */
function sparkSvg(vals,color){
  var v=vals.filter(function(x){return x!=null;});
  if(v.length<2) return "";
  var mn=Math.min.apply(null,v), mx=Math.max.apply(null,v), w=120, h=34, pad=3, n=vals.length, pts=[];
  vals.forEach(function(x,i){
    if(x==null) return;
    var px=n===1?0:i/(n-1)*w, py=mx===mn?h/2:h-pad-(x-mn)/(mx-mn)*(h-pad*2);
    pts.push([px,py]);
  });
  var d="M"+pts.map(function(p){return p[0].toFixed(1)+","+p[1].toFixed(1);}).join("L");
  var id="sg"+Math.random().toString(36).slice(2,8);
  return '<svg class="ps-spark" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="'+id+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+color+'" stop-opacity=".38"/><stop offset="1" stop-color="'+color+'" stop-opacity="0"/></linearGradient></defs>'+
    '<path d="'+d+'L'+w+','+h+'L0,'+h+'Z" fill="url(#'+id+')"/><path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>';
}
function countUp(el,from,to,key){
  if(reduceMotion||document.hidden||to==null||from==null||from===to){el.textContent=fm(key,to,true);return;}
  var t0=performance.now(), dur=600;
  (function step(t){
    var k=Math.min(1,(t-t0)/dur), e=1-Math.pow(1-k,3);
    el.textContent=fm(key,from+(to-from)*e,true);
    if(k<1) requestAnimationFrame(step);
  })(t0);
}
/* ── Key number cards ─────────────────────────────────────────────
   Ten by default, any of the 50-odd metrics Google gives, in any order. The
   choice lives in this browser (ps_kpis_v1). A card is a metric: click it to
   chart it, shift-click to add it to the chart, the corner button changes
   which metric the card shows, and "Customize cards" lists them all. */
var KKEY="ps_kpis_v1", KMAX=20;
function loadKpis(){
  try{
    var v=JSON.parse(localStorage.getItem(KKEY)||"null");
    if(Array.isArray(v)){v=v.filter(function(k,i){return P.METRICS[k]&&v.indexOf(k)===i;}).slice(0,KMAX); if(v.length) S.kpis=v;}
  }catch(e){}
}
function saveKpis(){try{localStorage.setItem(KKEY,JSON.stringify(S.kpis));}catch(e){}}
function usable(k){return !P.METRICS[k].value||hasValue();}
var GROUP_ICON={
  "Cost":'<path d="M12 3v18"/><path d="M16.5 7.5C16.5 5.8 14.5 4.5 12 4.5S7.5 5.8 7.5 7.5 9.5 10 12 10.5s4.5 1.3 4.5 3S14.5 19.5 12 19.5 7.5 18.2 7.5 16.5"/>',
  "Volume":'<path d="M5 20v-8M12 20V5M19 20v-5" class="th"/>',
  "Results":'<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".8"/>',
  "Efficiency":'<path d="M4 17a8 8 0 1 1 16 0"/><path d="m12 17 4-6"/>',
  "Engagement":'<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  "Video":'<rect x="3" y="5" width="18" height="14" rx="3.5"/><path d="m10 9 5 3-5 3z"/>',
  "Calls":'<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
  "Viewability":'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  "Auction share":'<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M14.5 3.4A9 9 0 0 1 20.6 9.5H14.5z"/>'
};
/* Every metric as menu items, grouped, for the card pickers and the chart pickers. chart=true leaves out the
   whole-period shares, which cannot be drawn over time. */
function metricItems(opts){
  var tc=P.total(S.cur,null), sh=S.shares.cur, items=[], last=null;
  (opts.chart?P.METRIC_ORDER:P.METRIC_ALL).forEach(function(k){
    if(!usable(k)) return;
    var def=P.METRICS[k]; if(def.group!==last){items.push({head:def.group});last=def.group;}
    var why=P.noData(k,tc,S.missing,sh);
    items.push({key:k,label:def.label,sub:why?"No figures here: "+why.toLowerCase():def.hint,color:mcol(k),checked:opts.checked(k),disabled:opts.disabled?opts.disabled(k):false});
  });
  return items;
}
function paintKpis(){
  var set=setCur(), cset=setCmp(), tc=P.total(S.cur,set), tp=S.cmp?P.total(S.cmp,cset):null, days=P.daily(S.cur,set), h="", shown=0;
  var shCur=S.shares.cur, shCmp=S.shares.cmp, whole=P.total(S.cur,null);
  S.kpis.forEach(function(k,i){
    if(!P.METRICS[k]||!usable(k)) return;
    shown++;
    var m=P.METRICS[k], col=mcol(k), on=S.ex.metrics.indexOf(k)>=0, v, pv=null, ch=null, body="", why;
    if(m.period){
      v=shCur?shCur[m.share]:null; pv=shCmp?shCmp[m.share]:null; why=P.noData(k,whole,S.missing,shCur);
      if(S.filter.length) body='<span class="d flat"><em>Whole account</em></span>';
      else if(v!=null&&pv!=null) ch=P.change(k,v,pv);
      else body='<span class="d flat"><em>'+esc(v==null?why:m.hint)+'</em></span>';
    }else{
      v=m.get(tc); pv=tp?m.get(tp):null; why=P.noData(k,whole,S.missing,null);
      if(why) v=null;                               // an account with no such data reads "-", not a made-up zero
      if(tp&&!why) ch=P.change(k,v,pv);
      else body='<span class="d flat"><em>'+esc(why||m.hint)+'</em></span>';
    }
    var delta=ch?'<span class="d '+ch.tone+'">'+(ch.dir>0?'<i>▲</i>':ch.dir<0?'<i>▼</i>':'')+esc(ch.text)+'<em>vs '+esc(fm(k,pv,true))+'</em></span>':body;
    var foot=m.period?(v!=null?'<span class="ps-meter"><i style="width:'+Math.min(100,Math.round(v*100))+'%"></i></span>':''):sparkSvg(days.map(function(t){return m.get(t);}),col);
    h+='<div class="ps-kpi'+(on?" on":"")+(m.period?" ps-kpi-static":"")+(why&&!m.period?" nodata":"")+'" role="button" tabindex="0" data-m="'+k+'" data-i="'+i+'" style="--kc:'+col+'" title="'+esc(m.hint)+'">'+
      '<div class="ps-kpi-h"><span class="ic"><svg viewBox="0 0 24 24" aria-hidden="true">'+GROUP_ICON[m.group]+'</svg></span><span class="k">'+esc(m.label)+'</span>'+
      '<button type="button" class="sw" data-sw="'+i+'" aria-label="Change what this card shows" title="Change this card"><svg viewBox="0 0 24 24"><path d="M5 12h.01M12 12h.01M19 12h.01" class="th"/></svg></button></div>'+
      '<span class="v" data-v>'+esc(fm(k,v,true))+'</span>'+delta+'<div class="ps-kpi-f">'+foot+'</div></div>';
  });
  var old={}; $$("#kpis .ps-kpi[data-m] [data-v]").forEach(function(e){old[e.closest(".ps-kpi").dataset.m]=e._n;});
  var box=$("#kpis"); box.style.setProperty("--cols",P.kpiCols(shown)); box.innerHTML=h;
  $$("#kpis .ps-kpi[data-m]").forEach(function(b,i){
    var k=b.dataset.m, e=$("[data-v]",b), m=P.METRICS[k], to=m.period?(shCur?shCur[m.share]:null):(b.classList.contains("nodata")?null:m.get(tc)); e._n=to;
    if(!m.period) countUp(e,old[k]==null?0:old[k],to,k);
    if(!reduceMotion&&!document.hidden) b.animate([{opacity:0,transform:"translateY(8px)"},{opacity:1,transform:"none"}],{duration:380,delay:i*35,easing:"cubic-bezier(.16,1,.3,1)",fill:"backwards"});
  });
  $("#kpiCount").textContent=shown+(shown===1?" card":" cards");
}
function chartFromCard(card,shift){
  var k=card.dataset.m, m=P.METRICS[k];
  if(m.period){toast(m.label+" is a figure for the whole period, so it cannot be drawn over time");return;}
  var ms=S.ex.metrics.slice();
  if(shift){
    var i=ms.indexOf(k);
    if(i>=0&&ms.length>1) ms.splice(i,1); else if(i<0){ms.push(k); if(ms.length>4) ms.shift();}
  }else ms=[k];
  S.ex.metrics=ms; explorer.spec.metrics=ms; explorer.update(); paintKpiState();
  $("#explorer").scrollIntoView({behavior:document.hidden||reduceMotion?"auto":"smooth",block:"nearest"}); writeHash();
}
$("#kpis").addEventListener("click",function(e){
  var sw=e.target.closest("[data-sw]");
  if(sw){
    e.stopPropagation(); var idx=+sw.dataset.sw;
    popMenu(sw,{search:"Find a metric…",items:function(){return metricItems({checked:function(k){return S.kpis[idx]===k;}});},
      onPick:function(k){
        var at=S.kpis.indexOf(k);
        if(at>=0&&at!==idx){S.kpis[at]=S.kpis[idx];}     // already on another card: the two swap places
        S.kpis[idx]=k; saveKpis(); paintKpis();}});
    return;
  }
  var b=e.target.closest(".ps-kpi[data-m]"); if(b) chartFromCard(b,e.shiftKey||e.metaKey||e.ctrlKey);
});
$("#kpis").addEventListener("keydown",function(e){
  if(e.key!=="Enter"&&e.key!==" ") return;
  var b=e.target.closest(".ps-kpi[data-m]"); if(!b||e.target.closest("[data-sw]")) return;
  e.preventDefault(); chartFromCard(b,e.shiftKey);
});
$("#kpiCustom").addEventListener("click",function(){
  popMenu(this,{multi:true,search:"Find a metric…",
    items:function(){
      return [{head:S.kpis.length+" of "+KMAX+" cards. Tick to add, untick to remove."}].concat(metricItems({
        checked:function(k){return S.kpis.indexOf(k)>=0;},
        disabled:function(k){return S.kpis.indexOf(k)<0&&S.kpis.length>=KMAX;}}));},
    footer:[{key:"reset",label:"Back to the default ten"}],
    onFooter:function(){S.kpis=P.KPI_DEFAULT.slice(); saveKpis(); paintKpis();},
    onPick:function(k){
      var i=S.kpis.indexOf(k);
      if(i>=0){if(S.kpis.length>1) S.kpis.splice(i,1);} else if(S.kpis.length<KMAX) S.kpis.push(k);
      saveKpis(); paintKpis();}});
});
function paintKpiState(){$$("#kpis .ps-kpi[data-m]").forEach(function(b){b.classList.toggle("on",S.ex.metrics.indexOf(b.dataset.m)>=0);});}

/* ── The chart builder ────────────────────────────────────────── */
Chart.defaults.font.family="'Space Grotesk',system-ui,sans-serif";
Chart.defaults.font.size=11.5;
Chart.defaults.animation.duration=reduceMotion?0:650;
var crosshair={id:"psCross",afterDatasetsDraw:function(ch){
  var t=ch.tooltip, a=t&&t.getActiveElements?t.getActiveElements():[]; if(!a.length) return;
  var x=a[0].element.x, ar=ch.chartArea, c=ch.ctx;
  c.save(); c.strokeStyle=theme().line2; c.lineWidth=1; c.setLineDash([4,4]); c.beginPath(); c.moveTo(x,ar.top); c.lineTo(x,ar.bottom); c.stroke(); c.restore();
}};
function grad(hex,a0,a1){
  return function(c){
    var ch=c.chart, ar=ch.chartArea; if(!ar) return alpha(hex,a0);
    var g=ch.ctx.createLinearGradient(0,ar.top,0,ar.bottom); g.addColorStop(0,alpha(hex,a0)); g.addColorStop(1,alpha(hex,a1)); return g;
  };
}
function tipBase(th){
  return {enabled:true,backgroundColor:"rgba(9,12,24,.96)",borderColor:"rgba(255,255,255,.14)",borderWidth:1,padding:12,cornerRadius:12,
    titleColor:"#eef1fb",bodyColor:"#c4cbe4",footerColor:"#eef1fb",titleFont:{weight:"700",size:12.5},bodyFont:{size:12},usePointStyle:true,boxPadding:5,caretSize:6,displayColors:true};
}
var HOURLBL=function(h){return h===0?"12a":h<12?h+"a":h===12?"12p":(h-12)+"p";};

/* Picture for each kind of chart (drawn in the gallery). */
var TYPE_ICON={
  line:'<path d="M3 17l5-7 4 4 8-10"/><circle cx="8" cy="10" r="1.2"/><circle cx="12" cy="14" r="1.2"/>',
  area:'<path d="M3 19V13l5-6 4 4 8-7v15z" class="f"/>',
  bar:'<path d="M6 20V11M12 20V4M18 20v-6" class="th"/>',
  hbar:'<path d="M4 6h12M4 12h16M4 18h8" class="th"/>',
  combo:'<path d="M6 20v-6M12 20v-9M18 20v-4" class="th"/><path d="M3 9l5-4 5 3 7-5"/>',
  stacked:'<path d="M6 20v-4M6 14v-3M12 20v-7M12 11V6M18 20v-3M18 15v-2" class="th"/>',
  stackedarea:'<path d="M3 20v-6l6-4 5 3 7-5v12z" class="f"/><path d="M3 20l6-3 5 1 7-4"/>',
  pie:'<path d="M12 3a9 9 0 1 0 9 9h-9z" class="f"/><path d="M14.5 3.4A9 9 0 0 1 20.6 9.5H14.5z"/>',
  donut:'<path d="M12 4a8 8 0 1 0 8 8" class="th"/><path d="M15 4.8A8 8 0 0 1 19.2 9" class="th"/>',
  radar:'<path d="M12 3l8.5 6.2-3.2 10H6.7l-3.2-10z"/><path d="M12 8l4.6 3.4-1.8 5.6H9.2l-1.8-5.6z" class="f"/>',
  scatter:'<circle cx="6" cy="16" r="1.8" class="f"/><circle cx="11" cy="9" r="2.4" class="f"/><circle cx="17" cy="14" r="1.8" class="f"/><circle cx="19" cy="6" r="1.8" class="f"/>'
};

/* Is the previous period / last year drawable on this chart? */
function cmpOk(spec){
  return !!(S.compare!=="none"&&S.cmp)&&["line","area","bar","hbar","combo"].indexOf(spec.type)>=0&&!(spec.accounts&&spec.accounts.length)
    &&!(P.SERIES_TYPES[spec.type]&&spec.series&&P.seriesAllowed(spec.dim,spec.type,false))
    &&["hour","device","network"].indexOf(spec.dim)<0&&((spec.type!=="bar"&&spec.type!=="hbar")||spec.metrics.length===1);
}
function seriesOn(spec){return !!spec.series&&P.seriesAllowed(spec.dim,spec.type,!!(spec.accounts&&spec.accounts.length));}

/* What a chart shows, from the loaded data: labels, a total per label, the comparison's total per label,
   and (when broken down) one set of totals per campaign type or campaign. */
function buildPlan(spec){
  var dim=spec.dim, d=S.cur, set=setCur(), cd=S.cmp, cset=setCmp(), D=P.DIM[dim];
  var pl={dim:dim,labels:[],cur:[],cmp:null,keys:null,ids:null,note:"",series:null};
  var wantCmp=!!(cd&&spec.compare&&cmpOk(spec));
  if(spec.accounts&&spec.accounts.length&&D.time) return accountsPlan(spec);
  if(D.time){
    var b=P.bucket(d.days,P.daily(d,set),dim); pl.labels=b.labels; pl.keys=b.keys; pl.cur=b.totals;
    if(wantCmp){var cb=P.bucket(cd.days,P.daily(cd,cset),dim); pl.cmp=pl.labels.map(function(_,i){return cb.totals[i]||null;}); pl.cmpKeys=cb.keys;}
    if(seriesOn(spec)) pl.series=P.seriesGroups(d,set,spec.series,8).map(function(g){return {name:g.name,totals:P.bucket(d.days,P.daily(d,g.idx),dim).totals};});
    return pl;
  }
  if(dim==="dow"){
    pl.labels=P.DOW.slice(); pl.cur=P.byWeekday(d.days,P.daily(d,set));
    if(wantCmp) pl.cmp=P.byWeekday(cd.days,P.daily(cd,cset));
    if(seriesOn(spec)) pl.series=P.seriesGroups(d,set,spec.series,8).map(function(g){return {name:g.name,totals:P.byWeekday(d.days,P.daily(d,g.idx))};});
    return pl;
  }
  if(D.ex){
    if(!S.extras) return {pending:true};
    if(S.extras.error) return {error:"Google Ads would not give the "+D.l.toLowerCase()+" split: "+S.extras.error};
    if(S.filter.length) pl.note="This split is for the whole account; it cannot be narrowed to campaigns.";
    if(dim==="hour"){
      var g=P.hourGrid(S.extras.dowHour); pl.cur=P.byHour(g); for(var h=0;h<24;h++) pl.labels.push(HOURLBL(h));
      return pl;
    }
  }
  /* ranked groupings */
  var m0=spec.metrics[0], items=[];
  if(dim==="campaign"){
    var per=P.perCampaign(d), cper=cd?P.perCampaign(cd):null, cidx={};
    if(cd) cd.campaigns.forEach(function(c,i){cidx[c.id]=i;});
    d.campaigns.forEach(function(c,i){
      if(set&&!set.has(i)) return; if(!per[i][P.COST]&&!per[i][P.IMPR]) return;
      items.push({label:c.name,ids:[c.id],t:per[i],c:wantCmp&&cidx[c.id]!=null?cper[cidx[c.id]]:null});
    });
  }else if(dim==="type"){
    var tt=P.byType(d,set), pt={}; if(wantCmp) P.byType(cd,cset).forEach(function(x){pt[x.key]=x.t;});
    tt.forEach(function(x){
      items.push({label:P.label(P.TYPE_LABEL,x.key),ids:d.campaigns.filter(function(c){return c.type===x.key;}).map(function(c){return c.id;}),t:x.t,c:pt[x.key]||null});
    });
  }else{
    var map=dim==="device"?P.DEVICE_LABEL:P.NETWORK_LABEL;
    (S.extras[dim]||[]).forEach(function(r){items.push({label:P.label(map,r[0]),t:r.slice(1,1+P.NB),c:null});});
  }
  items.sort(function(a,b){var x=P.mval(m0,a.t),y=P.mval(m0,b.t);return (y==null?-Infinity:y)-(x==null?-Infinity:x);});
  var cap=spec.type==="radar"?8:spec.type==="scatter"?60:12;
  if(dim==="campaign"&&items.length>cap){pl.note="Top "+cap+" of "+items.length+" campaigns, ranked by "+P.METRICS[m0].label+".";items=items.slice(0,cap);}
  pl.labels=items.map(function(i){return i.label;}); pl.cur=items.map(function(i){return i.t;});
  pl.cmp=wantCmp&&dim!=="device"&&dim!=="network"?items.map(function(i){return i.c;}):null;
  pl.ids=items.map(function(i){return i.ids||null;});
  return pl;
}
function otherKey(id){return id+"|"+S.range.start+"|"+S.range.end;}
function accountsPlan(spec){
  var m=spec.metrics[0], pl={dim:spec.dim,accounts:[],labels:[],keys:null,note:""}, cs=[];
  var mine=P.bucket(S.cur.days,P.daily(S.cur,setCur()),spec.dim);
  pl.labels=mine.labels; pl.keys=mine.keys;
  pl.accounts.push({name:S.acct.name,id:S.acct.id,currency:S.acct.currency,totals:mine.totals});
  for(var i=0;i<spec.accounts.length;i++){
    var a=S.accounts.filter(function(x){return x.id===spec.accounts[i];})[0], data=S.other[otherKey(spec.accounts[i])];
    if(!a) continue; if(!data) return {pending:true};
    if(data.error) return {error:a.name+": "+data.error};
    pl.accounts.push({name:a.name,id:a.id,currency:a.currency,totals:P.bucket(data.days,P.daily(data,null),spec.dim).totals});
    cs.push(a.currency);
  }
  if(cs.some(function(c){return c!==S.acct.currency;})&&P.METRICS[m].kind.indexOf("money")===0&&!spec.index) pl.note="These accounts bill in different currencies. Turn on “Index to 100” to compare their shape instead.";
  return pl;
}
function ensureOthers(spec,done){
  var need=(spec.accounts||[]).filter(function(id){return !S.other[otherKey(id)];});
  if(!need.length) return done();
  var seq=S.seq;
  Promise.all(need.map(function(id){
    var key=otherKey(id);
    return getJson(BASE+"/performance?account="+id+"&start="+S.range.start+"&end="+S.range.end+"&sis=0").then(function(j){S.other[key]=j.current;},
      function(e){S.other[key]={error:e.message};});
  })).then(function(){if(seq===S.seq) done();});
}

function titleFor(spec){
  var ms=spec.metrics.map(function(m){return P.METRICS[m].label;}), dl=P.DIM[spec.dim].l.toLowerCase();
  var list=ms.length>2?ms.slice(0,-1).join(", ")+" & "+ms[ms.length-1]:ms.join(" & ");
  if(spec.accounts&&spec.accounts.length&&P.DIM[spec.dim].time) return ms[0]+", account by account";
  if(spec.type==="scatter"&&ms.length>1) return ms[0]+" vs "+ms[1]+(ms[2]?", sized by "+ms[2]:"")+" by "+dl;
  if(spec.type==="pie"||spec.type==="donut") return ms[0]+" share by "+dl;
  if(seriesOn(spec)) return ms[0]+" by "+dl+", split by "+(spec.series==="type"?"campaign type":"campaign");
  return list+" by "+dl;
}

/* One tooltip line for a metric: its value, and how it moved against the comparison when there is one. */
function tipLine(m,v,cmpTotal){
  var s=P.METRICS[m].label+":  "+P.fmtMetric(m,v,cur());
  if(cmpTotal){var cv=P.METRICS[m].get(cmpTotal); s+="   (was "+P.fmtMetric(m,cv,cur(),true)+", "+P.change(m,v,cv).text+")";}
  return s;
}
function dayTitle(spec,pl,items){
  var i=items[0].dataIndex, k=pl.keys&&pl.keys[i];
  if(k&&spec.dim==="day") return P.DOW[P.dowOf(k)]+", "+items[0].label;
  if(k&&spec.dim==="week") return "Week of "+P.dayLabel(k);
  return items[0].label;
}
function valueAxis(def,C,th,opts){
  return Object.assign({beginAtZero:true,grace:"6%",grid:{color:th.line,drawTicks:false},border:{display:false},
    ticks:{color:th.muted,padding:8,maxTicksLimit:6,callback:function(v){return P.fmt(def.kind,v,C,true);}}},opts||{});
}
function catAxis(th,labels,horiz){
  return {offset:true,grid:{display:false},border:{color:th.line},
    ticks:{color:horiz?th.ink2:th.muted,maxRotation:0,autoSkip:!horiz,maxTicksLimit:labels.length>16?10:16,padding:6,
      callback:function(v){var l=this.getLabelForValue(v); return horiz&&l.length>30?l.slice(0,29)+"…":l;}}};
}

/* Line, area, and bars + lines: each metric on its own scale. */
function seriesConfig(spec,pl,th){
  var C=cur(), ms=spec.metrics.slice(0,4), ds=[], scales={}, many=ms.length>1;
  scales.x=catAxis(th,pl.labels,false);
  ms.forEach(function(m,i){
    var def=P.METRICS[m], col=mcol(m), isBar=spec.type==="combo"&&i===0, fill=spec.type==="area";
    ds.push({type:isBar?"bar":"line",label:def.label,data:pl.cur.map(function(t){return def.get(t);}),yAxisID:"y"+i,borderColor:col,
      backgroundColor:isBar?grad(col,.95,.4):fill?grad(col,.34,0):col,borderWidth:isBar?0:2.4,borderRadius:isBar?7:0,borderSkipped:false,
      fill:fill,cubicInterpolationMode:"monotone",pointRadius:0,pointHoverRadius:5.5,pointHoverBorderWidth:2.5,pointHoverBackgroundColor:th.panel,pointHoverBorderColor:col,
      order:i+10,_m:m,maxBarThickness:36});
    if(pl.cmp) ds.push({type:"line",label:def.label+" (comparison)",data:pl.cmp.map(function(t){return t?def.get(t):null;}),yAxisID:"y"+i,
      borderColor:alpha(col,.55),borderDash:[5,4],borderWidth:1.6,pointRadius:0,pointHoverRadius:0,cubicInterpolationMode:"monotone",fill:false,order:i,_m:m,_cmp:true});
    scales["y"+i]=valueAxis(def,C,th,{position:i%2?"right":"left",display:i<2,grid:{display:i===0,color:th.line,drawTicks:false},
      ticks:{color:many?col:th.muted,padding:8,maxTicksLimit:6,callback:function(v){return P.fmt(def.kind,v,C,true);}}});
  });
  var tip=tipBase(th);
  tip.filter=function(it){return !it.dataset._cmp;};
  tip.callbacks={title:function(items){return dayTitle(spec,pl,items);},
    label:function(c){return tipLine(c.dataset._m,c.raw,pl.cmp&&pl.cmp[c.dataIndex]);}};
  return {type:"bar",data:{labels:pl.labels,datasets:ds},plugins:[crosshair],
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},scales:scales,plugins:{legend:{display:false},tooltip:tip}}};
}

/* One metric split by campaign type or campaign: lines, areas, grouped bars, or stacks that add up. */
function breakdownConfig(spec,pl,th){
  var C=cur(), m=spec.metrics[0], def=P.METRICS[m], t=spec.type, stacked=t==="stacked"||t==="stackedarea", isBar=t==="bar"||t==="stacked";
  var ds=pl.series.map(function(s,i){
    var col=scol(i), fill=t==="area"||t==="stackedarea";
    return {type:isBar?"bar":"line",label:s.name,data:s.totals.map(function(x){return def.get(x);}),borderColor:col,
      backgroundColor:isBar?alpha(col,.9):fill?alpha(col,stacked?.6:.22):col,borderWidth:isBar?0:2.2,borderRadius:isBar&&!stacked?5:0,borderSkipped:false,
      fill:fill?(t==="stackedarea"?(i===0?"origin":"-1"):"origin"):false,cubicInterpolationMode:"monotone",pointRadius:0,pointHoverRadius:4.5,
      pointHoverBackgroundColor:th.panel,pointHoverBorderColor:col,pointHoverBorderWidth:2.2,stack:stacked?"s":undefined,order:i,maxBarThickness:40};
  });
  var tip=tipBase(th);
  tip.callbacks={title:function(items){return dayTitle(spec,pl,items);},
    label:function(c){return c.dataset.label+":  "+P.fmt(def.kind,c.raw,C);},
    footer:function(items){if(!stacked) return ""; var s=0; items.forEach(function(i){s+=i.raw||0;}); return "Total:  "+P.fmt(def.kind,s,C);}};
  return {type:isBar?"bar":"line",data:{labels:pl.labels,datasets:ds},plugins:[crosshair],
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      scales:{x:Object.assign(catAxis(th,pl.labels,false),{stacked:stacked}),y:valueAxis(def,C,th,{stacked:stacked})},
      plugins:{legend:{display:true,position:"bottom",labels:{color:th.ink2,usePointStyle:true,boxWidth:8,padding:14}},tooltip:tip}}};
}

/* Bars, across or down. With several metrics each is drawn as a share of its own peak so they can sit side by side. */
function barsConfig(spec,pl,th,onPick){
  var C=cur(), ms=spec.metrics.slice(0,4), horiz=spec.type==="hbar", multi=ms.length>1, ds=[], def0=P.METRICS[ms[0]];
  var raws=ms.map(function(m){return pl.cur.map(function(t){return P.METRICS[m].get(t);});}), rels=multi?raws.map(P.relative):null;
  ms.forEach(function(m,i){
    var col=mcol(m);
    ds.push({label:P.METRICS[m].label,data:multi?rels[i]:raws[i],backgroundColor:alpha(col,.88),hoverBackgroundColor:col,borderRadius:7,borderSkipped:false,
      _m:m,_raw:raws[i],barPercentage:.8,categoryPercentage:.84,maxBarThickness:44});
  });
  if(!multi&&pl.cmp) ds.push({label:def0.label+" (comparison)",data:pl.cmp.map(function(t){return t?def0.get(t):null;}),backgroundColor:alpha(mcol(ms[0]),.26),
    borderRadius:7,borderSkipped:false,_cmp:true,barPercentage:.8,categoryPercentage:.84,maxBarThickness:44});
  var tip=tipBase(th);
  tip.filter=function(it){return !it.dataset._cmp;};
  tip.callbacks={title:function(items){return dayTitle(spec,pl,items);},
    label:function(c){
      var m=c.dataset._m, raw=c.dataset._raw[c.dataIndex], s=tipLine(m,raw,!multi&&pl.cmp?pl.cmp[c.dataIndex]:null);
      return multi&&c.raw!=null?s+"   ("+Math.round(c.raw)+"% of its peak)":s;
    }};
  var vAxis=valueAxis(def0,C,th,multi?{max:100,ticks:{color:th.muted,padding:8,maxTicksLimit:5,callback:function(v){return v+"%";}}}:{});
  var cAxis=catAxis(th,pl.labels,horiz), clickable=!!(pl.ids&&pl.ids[0]);
  return {type:"bar",data:{labels:pl.labels,datasets:ds},
    options:{indexAxis:horiz?"y":"x",responsive:true,maintainAspectRatio:false,
      interaction:horiz?{mode:"nearest",axis:"y",intersect:false}:{mode:"index",intersect:false},
      onClick:function(e,els){if(clickable&&els.length&&pl.ids[els[0].index]) onPick(pl.ids[els[0].index]);},
      onHover:function(e,els){e.native.target.style.cursor=clickable&&els.length?"pointer":"default";},
      scales:horiz?{x:vAxis,y:cAxis}:{x:cAxis,y:vAxis},plugins:{legend:{display:multi,position:"bottom",labels:{color:th.ink2,usePointStyle:true,boxWidth:8,padding:14}},tooltip:tip}}};
}

/* Pie and donut: the first metric as a share of its total; the smallest slices fold into "Other". */
function pieConfig(spec,pl,th,onPick){
  var C=cur(), m=spec.metrics[0], def=P.METRICS[m];
  var items=pl.labels.map(function(l,i){return {label:l,t:pl.cur[i],ids:pl.ids&&pl.ids[i]};}).filter(function(i){return (def.get(i.t)||0)>0;});
  items=P.foldTop(items,8,function(i){return def.get(i.t);});
  var vals=items.map(function(i){return def.get(i.t)||0;}), tot=vals.reduce(function(a,b){return a+b;},0), donut=spec.type==="donut", clickable=items.some(function(i){return i.ids;});
  var center={id:"psCenter",afterDraw:function(ch){
    if(!donut) return; var a=ch.chartArea, c=ch.ctx, x=(a.left+a.right)/2, y=(a.top+a.bottom)/2;
    c.save(); c.textAlign="center"; c.fillStyle=th.ink; c.font="700 24px 'Space Grotesk',sans-serif"; c.fillText(P.fmt(def.kind,tot,C,true),x,y+4);
    c.fillStyle=th.faint; c.font="700 10.5px 'Space Grotesk',sans-serif"; c.fillText(def.label.toUpperCase(),x,y+22); c.restore();}};
  var tip=tipBase(th); tip.callbacks={label:function(c){return " "+c.label+":  "+P.fmt(def.kind,c.raw,C)+"  ("+Math.round(c.raw/tot*100)+"%)";}};
  return {type:"doughnut",plugins:[center],data:{labels:items.map(function(i){return i.label;}),
      datasets:[{data:vals,backgroundColor:items.map(function(i,k){return i.rest?"#8b94be":scol(k);}),borderColor:th.panel,borderWidth:3,hoverOffset:8,borderRadius:donut?5:3}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:donut?"66%":0,layout:{padding:8},
      onClick:function(e,els){if(clickable&&els.length&&items[els[0].index].ids) onPick(items[els[0].index].ids);},
      onHover:function(e,els){e.native.target.style.cursor=clickable&&els.length?"pointer":"default";},
      plugins:{legend:{display:true,position:"bottom",labels:{color:th.ink2,usePointStyle:true,boxWidth:8,padding:12}},tooltip:tip}}};
}

/* Radar: one shape per metric, each as a share of its own peak. */
function radarConfig(spec,pl,th){
  var C=cur(), ms=spec.metrics.slice(0,4), n=Math.min(pl.labels.length,spec.dim==="campaign"||spec.dim==="type"?8:24);
  var labels=pl.labels.slice(0,n);
  var ds=ms.map(function(m){
    var col=mcol(m), raw=pl.cur.slice(0,n).map(function(t){return P.METRICS[m].get(t);}), rel=P.relative(raw);
    return {label:P.METRICS[m].label,data:rel,borderColor:col,backgroundColor:alpha(col,.16),borderWidth:2.2,pointRadius:3,pointBackgroundColor:col,pointHoverRadius:5,_m:m,_raw:raw};
  });
  var tip=tipBase(th); tip.callbacks={title:function(items){return items[0].label;},
    label:function(c){return c.dataset.label+":  "+P.fmtMetric(c.dataset._m,c.dataset._raw[c.dataIndex],C)+"   ("+Math.round(c.raw)+"% of its peak)";}};
  return {type:"radar",data:{labels:labels,datasets:ds},
    options:{responsive:true,maintainAspectRatio:false,
      scales:{r:{min:0,max:100,ticks:{display:false,stepSize:25},grid:{color:th.line},angleLines:{color:th.line},pointLabels:{color:th.ink2,font:{size:11.5}}}},
      plugins:{legend:{display:ms.length>1,position:"bottom",labels:{color:th.ink2,usePointStyle:true,boxWidth:8,padding:14}},tooltip:tip}}};
}

/* Scatter: the first metric across, the second up, an optional third as the size of each dot. */
function scatterConfig(spec,pl,th,onPick){
  var C=cur(), ms=spec.metrics, xd=P.METRICS[ms[0]], yd=P.METRICS[ms[1]], sd=ms[2]?P.METRICS[ms[2]]:null, pts=[];
  pl.labels.forEach(function(l,i){
    var x=xd.get(pl.cur[i]), y=yd.get(pl.cur[i]), s=sd?sd.get(pl.cur[i]):0;
    if(x!=null&&y!=null) pts.push({x:x,y:y,s:s||0,label:l,ids:pl.ids&&pl.ids[i],t:pl.cur[i]});
  });
  var mx=Math.max.apply(null,pts.map(function(p){return p.s;}).concat([1])), col=mcol(ms[1]), clickable=pts.some(function(p){return p.ids;});
  var tip=tipBase(th); tip.callbacks={title:function(it){return it[0].raw.label;},label:function(c){
    var t=c.raw.t, out=[tipLine(ms[0],xd.get(t)),tipLine(ms[1],yd.get(t))]; if(sd) out.push(tipLine(ms[2],sd.get(t))); return out;}};
  return {type:"bubble",data:{datasets:[{data:pts.map(function(p){return {x:p.x,y:p.y,r:sd?4+16*Math.sqrt(p.s/mx):7,label:p.label,t:p.t,ids:p.ids};}),
      backgroundColor:alpha(col,.55),borderColor:col,borderWidth:1.5,hoverBorderWidth:3}]},
    options:{responsive:true,maintainAspectRatio:false,layout:{padding:{top:14,right:16}},
      onClick:function(e,els){if(clickable&&els.length&&pts[els[0].index].ids) onPick(pts[els[0].index].ids);},
      onHover:function(e,els){e.native.target.style.cursor=clickable&&els.length?"pointer":"default";},
      scales:{x:valueAxis(xd,C,th,{title:{display:true,text:xd.label,color:th.muted}}),y:valueAxis(yd,C,th,{title:{display:true,text:yd.label,color:th.muted}})},
      plugins:{legend:{display:false},tooltip:tip}}};
}

function accountsConfig(spec,pl,th){
  var m=spec.metrics[0], def=P.METRICS[m], C=cur(), ds=[];
  pl.accounts.forEach(function(a,i){
    var vals=pl.accounts[i].totals.map(function(t){return def.get(t);});
    if(spec.index) vals=P.indexTo100(vals);
    var col=scol(i);
    ds.push({type:"line",label:a.name,data:vals,borderColor:col,backgroundColor:col,borderWidth:2.4,cubicInterpolationMode:"monotone",pointRadius:0,pointHoverRadius:5.5,
      pointHoverBackgroundColor:th.panel,pointHoverBorderColor:col,pointHoverBorderWidth:2.5,_cur:a.currency});
  });
  var tip=tipBase(th);
  tip.callbacks={title:function(items){return dayTitle(spec,pl,items);},
    label:function(c){return c.dataset.label+":  "+(spec.index?c.raw==null?"–":c.raw.toFixed(0)+" (index)":P.fmt(def.kind,c.raw,c.dataset._cur));}};
  return {type:"line",data:{labels:pl.labels,datasets:ds},plugins:[crosshair],
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      scales:{x:catAxis(th,pl.labels,false),y:valueAxis(def,C,th,{beginAtZero:!spec.index,ticks:{color:th.muted,padding:8,maxTicksLimit:6,
          callback:function(v){return spec.index?v.toFixed(0):P.fmt(def.kind,v,C,true);}}})},
      plugins:{legend:{display:true,position:"bottom",labels:{color:th.ink2,usePointStyle:true,boxWidth:8,padding:16}},tooltip:tip}}};
}

function chartConfig(spec,pl,th){
  var t=spec.type, pick=function(ids){toggleIds(ids);};
  if(pl.accounts) return accountsConfig(spec,pl,th);
  if(t==="pie"||t==="donut") return pieConfig(spec,pl,th,pick);
  if(t==="radar") return radarConfig(spec,pl,th);
  if(t==="scatter") return scatterConfig(spec,pl,th,pick);
  if(pl.series&&P.SERIES_TYPES[t]) return breakdownConfig(spec,pl,th);
  if(t==="bar"||t==="hbar") return barsConfig(spec,pl,th,pick);
  return seriesConfig(spec,pl,th);
}

function galleryHtml(spec){
  var acc=!!(spec.accounts&&spec.accounts.length);
  return P.TYPES.map(function(t){
    var why=P.typeBlock(spec.dim,t.k,acc);
    return '<button type="button" class="ps-tile'+(spec.type===t.k?" on":"")+'" data-gt="'+t.k+'" role="radio" aria-checked="'+(spec.type===t.k)+'"'+(why?' disabled title="'+esc(why)+'"':' title="'+esc(t.hint)+'"')+'>'+
      '<svg viewBox="0 0 24 24" aria-hidden="true">'+TYPE_ICON[t.k]+'</svg><span>'+esc(t.l)+'</span></button>';
  }).join("");
}
/* The same gallery in a floating panel, for the compact cards. */
function popGallery(anchor,spec,onPick){
  var same=menu&&menu._anchor===anchor; closeMenu(); if(same) return;
  var m=document.createElement("div"); m.className="ps-pop ps-pop-gal"; m._anchor=anchor;
  m.innerHTML='<div class="ps-gallery" role="radiogroup" aria-label="Chart type">'+galleryHtml(spec)+'</div>';
  m.addEventListener("click",function(e){var b=e.target.closest("[data-gt]"); if(!b||b.disabled) return; onPick(b.dataset.gt); closeMenu();});
  document.body.appendChild(m); menu=m;
  var r=anchor.getBoundingClientRect(), w=m.offsetWidth, h=m.offsetHeight;
  m.style.left=Math.max(8,Math.min(r.left,innerWidth-w-8))+"px";
  var below=r.bottom+6; m.style.top=(below+h>innerHeight-8&&r.top>h+14?r.top-h-6:below)+"px";
  setTimeout(function(){document.addEventListener("mousedown",onDocDown,true);document.addEventListener("keydown",onMenuKey,true);},0);
}

var cardSeq=0;
function ChartCard(host,spec,o){
  var id="cc"+(++cardSeq), el=document.createElement("section"), studio=!!o.studio;
  el.className="pm-panel ps-card ps-chart"+(o.main?" ps-chart-main":"")+(studio?" ps-chart-studio":"");
  var actions='<div class="ps-card-actions">'+(o.main?'<button type="button" class="ps-ico" data-act="pin" title="Keep this chart in Your charts"><svg viewBox="0 0 24 24"><path d="M12 17v5M9 3h6l-1 6 4 4H6l4-4z"/></svg><span>Pin</span></button>':'')+
    '<button type="button" class="ps-ico" data-act="csv" title="Download this chart’s numbers"><svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg><span>CSV</span></button>'+
    '<button type="button" class="ps-ico" data-act="png" title="Download as an image"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-8 9"/></svg><span>PNG</span></button>'+
    (o.onRemove?'<button type="button" class="ps-ico danger" data-act="rm" title="Remove this chart"><svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></button>':'')+'</div>';
  var caret='<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>';
  var groupBtn='<button type="button" class="ps-sel" data-act="dim"><span class="k">Group by</span><b></b>'+caret+'</button>';
  var seriesBtn='<button type="button" class="ps-sel" data-act="series"><span class="k">Break down by</span><b></b>'+caret+'</button>';
  var cmpSw='<label class="ps-switch" data-cmp><input type="checkbox"><i></i><span></span></label>';
  var head='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title"></span><span class="ps-sub"></span></div>'+actions+'</div>';
  var controls=studio
    ? '<div class="ps-steps"><div class="ps-step"><div class="ps-step-h"><i>1</i>Pick a chart</div><div class="ps-gallery" data-gallery role="radiogroup" aria-label="Chart type"></div></div>'+
      '<div class="ps-step"><div class="ps-step-h"><i>2</i>Choose your metrics<small>up to four</small></div><div class="ps-mchips" data-metrics></div></div>'+
      '<div class="ps-step"><div class="ps-step-h"><i>3</i>Slice it</div><div class="ps-ctrl-row">'+groupBtn+seriesBtn+cmpSw+'</div></div></div>'
    : '<div class="ps-ctrls"><div class="ps-mchips" data-metrics></div><div class="ps-ctrl-row">'+groupBtn+
      '<button type="button" class="ps-sel" data-act="type"><span class="k">Chart</span><svg class="tic" viewBox="0 0 24 24"></svg><b></b>'+caret+'</button>'+seriesBtn+cmpSw+
      (o.main?'<button type="button" class="ps-sel" data-act="accts"><span class="k">Compare accounts</span><b></b>'+caret+'</button>'+
        '<label class="ps-switch" data-index hidden><input type="checkbox"><i></i><span>Index to 100</span></label>':'')+'</div></div>';
  var foot=studio?'<div class="ps-studio-foot"><button type="button" class="pm-btn ps-addbtn" data-act="add">Add to Your charts</button>'+
    '<button type="button" class="pm-btn-ghost" data-act="reset">Start over</button><span class="ps-studio-tip">Add as many as you like. They are saved in this browser.</span></div>':'';
  el.innerHTML=head+controls+'<div class="ps-wrap"><canvas role="img"></canvas><div class="ps-msg" hidden></div></div><p class="ps-note"></p>'+foot;
  host.appendChild(el);
  var canvas=$("canvas",el), wrap=$(".ps-wrap",el), msg=$(".ps-msg",el), plan=null;
  var api={el:el,spec:spec,update:update,destroy:function(){destroyChart(id);el.remove();},canvas:canvas,plan:function(){return plan;}};

  function changed(){if(o.onSpec) o.onSpec(spec); update();}
  function accN(){return spec.accounts?spec.accounts.length:0;}
  function metricsHtml(){
    var h=spec.metrics.map(function(m,i){
      var dim=(o.main&&accN()&&i>0)||((spec.type==="pie"||spec.type==="donut"||(P.SERIES_TYPES[spec.type]&&seriesOn(spec)))&&i>0);
      return '<span class="ps-mchip'+(dim?" dim":"")+'" style="--c:'+mcol(m)+'" title="'+(dim?"This chart uses only the first metric":"")+'"><i></i>'+esc(P.METRICS[m].label)+
        (spec.metrics.length>1?'<button type="button" data-rm="'+m+'" aria-label="Remove '+esc(P.METRICS[m].label)+'">×</button>':'')+'</span>';
    }).join("");
    return h+'<button type="button" class="ps-mchip add" data-act="addm"'+(spec.metrics.length>=4?' title="Up to four metrics at once"':'')+'><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>Metric</button>';
  }
  function controlsPaint(){
    $("[data-metrics]",el).innerHTML=metricsHtml();
    $(".ps-sel[data-act=dim] b",el).textContent=P.DIM[spec.dim].l;
    if(studio) $("[data-gallery]",el).innerHTML=galleryHtml(spec);
    else{var tb=$(".ps-sel[data-act=type]",el); $("b",tb).textContent=P.TYPE[spec.type].l; $(".tic",tb).innerHTML=TYPE_ICON[spec.type];}
    var sb=$(".ps-sel[data-act=series]",el), sOk=P.seriesAllowed(spec.dim,spec.type,accN()>0);
    sb.disabled=!sOk; sb.classList.toggle("off",!sOk);
    $("b",sb).textContent=sOk&&spec.series?(spec.series==="type"?"Campaign type":"Campaign"):"Nothing";
    sb.title=sOk?"Draw one line or bar per campaign type or campaign":"Needs a line, area or bar chart over days, weeks, months or weekdays";
    var cs=$("[data-cmp]",el), ci=$("input",cs), ok=cmpOk(spec);
    ci.checked=!!(spec.compare&&ok); ci.disabled=!ok; $("span",cs).textContent=S.compare==="yoy"?"vs last year":"vs previous period"; cs.classList.toggle("off",!ok);
    cs.title=ok?"":(S.compare==="none"?"Choose a comparison in the bar above":"This chart has no comparison");
    if(o.main){
      var ix=$("[data-index]",el); $(".ps-sel[data-act=accts] b",el).textContent=accN()?"+"+accN():"None"; ix.hidden=!accN(); $("input",ix).checked=!!spec.index;
    }
  }
  function say(t,sub){msg.hidden=false;msg.innerHTML='<b>'+esc(t)+'</b>'+(sub?'<span>'+esc(sub)+'</span>':'');}
  function update(){
    if(!S.cur) return;
    controlsPaint();
    $(".ps-title",el).textContent=titleFor(spec);
    $(".ps-sub",el).textContent=S.acct?S.acct.name:"";
    var th=theme();
    function draw(){
      plan=buildPlan(spec);
      var horiz=spec.type==="hbar"&&plan&&plan.labels;
      wrap.style.height=horiz?Math.max(280,Math.min(620,plan.labels.length*(spec.metrics.length>1?(spec.metrics.length*14+22):38)+70))+"px":"";
      if(plan.pending){destroyChart(id);say("Loading…",P.DIM[spec.dim].ex?"Reading the "+P.DIM[spec.dim].l.toLowerCase()+" split from Google Ads":"Reading the other accounts");$(".ps-note",el).textContent="";return;}
      if(plan.error){destroyChart(id);say("Not available",plan.error);return;}
      var need=P.typeNeeds(spec.type,spec.metrics);
      if(need){destroyChart(id);say(P.TYPE[spec.type].l+" needs a different choice",need);$(".ps-note",el).textContent="";return;}
      if((spec.type==="stacked"||spec.type==="stackedarea")&&!plan.series){destroyChart(id);say("Choose what to stack","Set “Break down by” to campaign type or campaign.");$(".ps-note",el).textContent="";return;}
      var has=plan.accounts?plan.accounts.some(function(a){return a.totals.some(function(t){return t[0]||t[1]||t[2]||t[3];});}):plan.cur.some(function(t){return t[0]||t[1]||t[2]||t[3];});
      if(!has){destroyChart(id);say("Nothing to chart","There is no activity for this selection in the range.");$(".ps-note",el).textContent="";return;}
      msg.hidden=true;
      mkChart(id,canvas,chartConfig(spec,plan,th));
      canvas.setAttribute("aria-label",titleFor(spec)+" for "+(S.acct?S.acct.name:""));
      var notes=[], bars=spec.type==="bar"||spec.type==="hbar";
      if(plan.note) notes.push(plan.note);
      if(!plan.accounts&&(spec.type==="line"||spec.type==="area"||spec.type==="combo")&&spec.metrics.length>1) notes.push("Each metric uses its own scale, so the lines show shape, not size.");
      if(bars&&spec.metrics.length>1) notes.push("Each metric’s bars are scaled to its own peak (100%); hover for the real figures.");
      if((spec.type==="radar")&&spec.metrics.length>1||spec.type==="radar") notes.push("Each metric is scaled to its own peak, so the shapes compare patterns, not size.");
      if(spec.type==="scatter") notes.push("Click a dot to focus the whole page on it."); 
      if(plan.cmp) notes.push((bars?"Fainter bar: ":"Dashed line: ")+(S.compare==="yoy"?"the same days last year":"the previous period")+".");
      if(!plan.accounts&&P.DIM[spec.dim].time&&["conversions","cpa","cvr","value","roas","aov"].some(function(m){return spec.metrics.indexOf(m)>=0;})&&P.diffDays(S.range.end,P.localToday())<=3)
        notes.push("The last few days can still gain conversions, because Google credits them up to a few days later.");
      if(plan.ids&&plan.ids[0]&&(bars||spec.type==="pie"||spec.type==="donut")) notes.push("Click a "+(bars?"bar":"slice")+" to focus the whole page on it.");
      $(".ps-note",el).textContent=notes.join(" ");
    }
    if(spec.accounts&&spec.accounts.length&&P.DIM[spec.dim].time&&o.main){
      var need2=spec.accounts.some(function(i){return !S.other[otherKey(i)];});
      if(need2){plan={pending:true};destroyChart(id);say("Loading…","Reading the other accounts");ensureOthers(spec,draw);return;}
    }
    draw();
  }
  function setType(k){P.withType(spec,k); changed();}

  el.addEventListener("click",function(e){
    var rm=e.target.closest("[data-rm]"), act=e.target.closest("[data-act]"), gt=e.target.closest("[data-gt]");
    if(rm){spec.metrics=spec.metrics.filter(function(m){return m!==rm.dataset.rm;}); if(o.main) S.ex.metrics=spec.metrics; changed(); if(o.main) paintKpiState(); return;}
    if(gt&&!gt.disabled){setType(gt.dataset.gt); return;}
    if(!act) return;
    var a=act.dataset.act;
    if(a==="addm") popMenu(act,{multi:true,search:"Find a metric\u2026",items:function(){
      return metricItems({chart:true,checked:function(k){return spec.metrics.indexOf(k)>=0;},
        disabled:function(k){return spec.metrics.indexOf(k)<0&&spec.metrics.length>=4;}});},
      onPick:function(k){
        var i=spec.metrics.indexOf(k);
        if(i>=0){if(spec.metrics.length>1) spec.metrics.splice(i,1);} else if(spec.metrics.length<4) spec.metrics.push(k);
        if(o.main){S.ex.metrics=spec.metrics;paintKpiState();} changed();}});
    else if(a==="dim") popMenu(act,{items:function(){
      return [{head:"Over time"}].concat(P.DIMS.filter(function(d){return d.time;}).map(function(d){return {key:d.k,label:d.l,checked:spec.dim===d.k};}),
        [{head:"Cycles"}],P.DIMS.filter(function(d){return d.k==="dow"||d.k==="hour";}).map(function(d){return {key:d.k,label:d.l,checked:spec.dim===d.k};}),
        [{head:"Rank by"}],P.DIMS.filter(function(d){return d.rank;}).map(function(d){return {key:d.k,label:d.l,checked:spec.dim===d.k};}));},
      onPick:function(k){spec.dim=k; spec.type=P.fixType(k,spec.type,accN()>0); if(o.main) S.ex.dim=k; changed();}});
    else if(a==="type") popGallery(act,spec,setType);
    else if(a==="series") popMenu(act,{items:function(){return P.SERIES_KINDS.map(function(s){return {key:s.k||"none",label:s.l,checked:(spec.series||"")===s.k};});},
      onPick:function(k){spec.series=k==="none"?"":k; changed();}});
    else if(a==="accts") popMenu(act,{multi:true,search:"Find an account…",items:function(){
      return S.accounts.filter(function(x){return x.id!==S.acct.id;}).map(function(x){
        var on=spec.accounts.indexOf(x.id)>=0;
        return {key:x.id,label:x.name,sub:P.fmtId(x.id)+" · "+x.currency,checked:on,disabled:!on&&spec.accounts.length>=3};});},
      onPick:function(k){var i=spec.accounts.indexOf(k); if(i>=0) spec.accounts.splice(i,1); else if(spec.accounts.length<3) spec.accounts.push(k);
        S.ex.accounts=spec.accounts; if(spec.accounts.length&&!P.DIM[spec.dim].time){spec.dim="day";S.ex.dim="day";}
        spec.type=P.fixType(spec.dim,spec.type,spec.accounts.length>0); changed();}});
    else if(a==="pin"&&o.onPin) o.onPin(spec);
    else if(a==="add"&&o.onAdd) o.onAdd(spec);
    else if(a==="reset"&&o.onReset) o.onReset(api);
    else if(a==="rm"&&o.onRemove) o.onRemove(api);
    else if(a==="csv") cardCsv(api);
    else if(a==="png") cardPng(api);
  });
  $("[data-cmp] input",el).addEventListener("change",function(){spec.compare=this.checked; if(o.main) S.ex.compare=this.checked; changed();});
  if(o.main) $("[data-index] input",el).addEventListener("change",function(){spec.index=this.checked; S.ex.index=this.checked; changed();});
  return api;
}

function cardCsv(card){
  var pl=card.plan(), spec=card.spec; if(!pl||pl.pending||pl.error) return;
  var rows=[];
  if(pl.accounts){
    var m=spec.metrics[0];
    rows.push([spec.dim].concat(pl.accounts.map(function(a){return a.name+" ("+P.METRICS[m].label+")";})));
    pl.labels.forEach(function(l,i){rows.push([pl.keys?pl.keys[i]:l].concat(pl.accounts.map(function(a){var v=P.METRICS[m].get(a.totals[i]);return v==null?"":v;})));});
  }else if(pl.series&&P.SERIES_TYPES[spec.type]){
    var m1=spec.metrics[0];
    rows.push([P.DIM[spec.dim].l].concat(pl.series.map(function(s){return s.name+" ("+P.METRICS[m1].label+")";})));
    pl.labels.forEach(function(l,i){rows.push([pl.keys?pl.keys[i]:l].concat(pl.series.map(function(s){var v=P.METRICS[m1].get(s.totals[i]);return v==null?"":v;})));});
  }else{
    rows.push([P.DIM[spec.dim].l].concat(spec.metrics.map(function(m){return P.METRICS[m].label;}),pl.cmp?spec.metrics.map(function(m){return P.METRICS[m].label+" (comparison)";}):[]));
    pl.labels.forEach(function(l,i){
      rows.push([pl.keys?pl.keys[i]:l].concat(spec.metrics.map(function(m){var v=P.METRICS[m].get(pl.cur[i]);return v==null?"":v;}),
        pl.cmp?spec.metrics.map(function(m){var v=pl.cmp[i]?P.METRICS[m].get(pl.cmp[i]):null;return v==null?"":v;}):[]));
    });
  }
  download("chart-"+slugify(S.acct.name)+"-"+spec.dim+"-"+S.range.start+"-"+S.range.end+".csv","text/csv;charset=utf-8",P.toCsv(rows));
}
function cardPng(card){
  var src=card.canvas; if(!src.width) return;
  var c=document.createElement("canvas"), pad=24, head=64; c.width=src.width+pad*2; c.height=src.height+pad*2+head;
  var x=c.getContext("2d"), th=theme(); x.fillStyle=light()?"#ffffff":"#0b0e1c"; x.fillRect(0,0,c.width,c.height);
  x.fillStyle=th.ink; x.font="700 26px 'Space Grotesk',sans-serif"; x.fillText(titleFor(card.spec),pad,pad+26);
  x.fillStyle=th.muted; x.font="400 18px 'Space Grotesk',sans-serif"; x.fillText(S.acct.name+"  ·  "+fmtRange(S.range),pad,pad+54);
  x.drawImage(src,pad,pad+head);
  c.toBlob(function(b){var a=document.createElement("a");a.href=URL.createObjectURL(b);a.download="chart-"+slugify(S.acct.name)+"-"+card.spec.dim+".png";document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);});
}

function paintExplorer(){
  if(!explorer){
    $("#explorer").innerHTML="";
    explorer=ChartCard($("#explorer"),S.ex,{main:true,onSpec:function(){paintKpiState();writeHash();},onPin:function(spec){pinChart(spec);}});
  }
  explorer.update();
}

/* ── Chart studio: build a chart from scratch, then add it to Your charts ── */
var SKEY="ps_studio_v1";
function loadStudio(){try{var v=JSON.parse(localStorage.getItem(SKEY)||"null"); if(validSpec(v)){v.accounts=[];v.index=false;v.metrics=v.metrics.slice(0,4);S.studio=v;}}catch(e){}}
function saveStudio(){try{localStorage.setItem(SKEY,JSON.stringify(cleanSpec(S.studio)));}catch(e){}}
function paintStudio(){
  if(!studioCard){
    $("#studio").innerHTML="";
    studioCard=ChartCard($("#studio"),S.studio,{studio:true,onSpec:saveStudio,
      onAdd:function(spec){pinChart(spec);},
      onReset:function(){
        var fresh={metrics:["conversions","cost"],dim:"campaign",type:"hbar",compare:false,accounts:[],index:false,series:""};
        Object.keys(fresh).forEach(function(k){S.studio[k]=fresh[k];}); saveStudio(); studioCard.update();}});
  }
  studioCard.update();
}
$("#studioBtn").addEventListener("click",function(){
  var t=$("#studioSec"); if(t) t.scrollIntoView({behavior:document.hidden||reduceMotion?"auto":"smooth",block:"start"});
});

/* ── What stands out ──────────────────────────────────────────── */
var IC={good:'<path d="m5 12 5 5L20 7"/>',warn:'<path d="M12 4 3 20h18z"/><path d="M12 10v5M12 18v.5"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>'};
function paintInsights(){
  var list=P.insights({cur:S.cur,cmp:S.cmp,set:setCur(),sis:S.filter.length?null:S.sis,currency:cur()});
  $("#insights").innerHTML='<div class="pm-panel ps-card"><div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">What stands out</span><span class="ps-sub">Worked out from the numbers, not guessed</span></div></div><div class="ps-ins">'+
    list.map(function(x,i){
      return '<div class="ps-in '+x.tone+'" style="--i:'+i+'"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true">'+IC[x.tone]+'</svg><div><b>'+esc(x.title)+'</b><p>'+esc(x.text)+'</p>'+
        (x.action?'<button type="button" data-i="'+i+'">Show me <span>→</span></button>':'')+'</div></div>';}).join("")+'</div></div>';
  $$("#insights button[data-i]").forEach(function(b){
    b.onclick=function(){
      var a=list[+b.dataset.i].action;
      if(a.kind==="filter"){setFilter(a.ids);}
      else{S.ex.metrics=a.metrics.slice();S.ex.dim=a.dim;explorer.spec.metrics=S.ex.metrics;explorer.spec.dim=a.dim;explorer.update();paintKpiState();writeHash();}
      $("#explorer").scrollIntoView({behavior:document.hidden||reduceMotion?"auto":"smooth",block:"nearest"});
    };
  });
}

/* ── Splits: device, network, campaign type ───────────────────── */
function paintSplitSeg(){
  var seg=$("#splitSeg"), val=hasValue();
  if(!val&&S.split==="value") S.split="cost";
  seg.innerHTML=SPLITS.filter(function(k){return val||k!=="value";}).map(function(k){
    return '<button type="button" role="radio" data-s="'+k+'" aria-checked="'+(S.split===k)+'">'+esc(P.METRICS[k].label)+'</button>';}).join("");
}
$("#splitSeg").addEventListener("click",function(e){var b=e.target.closest("[data-s]"); if(!b) return; S.split=b.dataset.s; paintSplitSeg(); paintSplits(); writeHash();});

function donutCard(id,title,sub,rows,key,opts){
  var C=cur(), def=P.METRICS[key], tot=rows.reduce(function(a,r){return a+(def.get(r.t)||0);},0);
  var all=rows;                       // the legend shows the top five and folds the rest into one line
  if(rows.length>6){
    var rest=P.zero(); rows.slice(5).forEach(function(r){P.addTo(rest,r.t);});
    rows=rows.slice(0,5).concat([{label:"Other ("+(all.length-5)+")",t:rest,rest:true}]);
  }
  var h='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+esc(title)+'</span><span class="ps-sub">'+esc(sub)+'</span></div></div>';
  if(!rows.length||!tot) return h+'<p class="ps-msg-inline">No '+esc(def.label.toLowerCase())+' to split in this range.</p>';
  h+='<div class="ps-donut"><div class="ps-donut-c"><canvas></canvas><div class="ps-donut-mid"><b>'+esc(fm(key,tot,true))+'</b><span>'+esc(def.label)+'</span></div></div><ul class="ps-legend">'+
    rows.map(function(r,i){
      var v=def.get(r.t)||0, share=tot?v/tot:0, col=scol(i), cp=P.METRICS.cpa.get(r.t);
      return '<li class="'+(opts.click&&!r.rest?"click":"")+(r.rest?" rest":"")+'"'+(opts.click&&!r.rest?' data-i="'+i+'"':'')+'><i style="background:'+(r.rest?"#8b94be":col)+'"></i><span class="n">'+esc(r.label)+'</span><span class="p">'+Math.round(share*100)+'%</span>'+
        '<span class="v">'+esc(fm(key,v,true))+'</span><span class="bar"><s style="width:'+(share*100).toFixed(1)+'%;background:'+(r.rest?"#8b94be":col)+'"></s></span>'+
        (key!=="conversions"&&r.t[P.CONV]?'<small>'+esc(fm("cpa",cp,true))+' per conv.</small>':'')+'</li>';}).join("")+'</ul></div>';
  return h;
}
function paintSplits(){
  if(!S.cur) return;
  var key=S.split, def=P.METRICS[key], th=theme(), host=$("#splits");
  if(!host._built){host.innerHTML='<section class="pm-panel ps-card" id="spDevice"></section><section class="pm-panel ps-card" id="spNetwork"></section><section class="pm-panel ps-card" id="spType"></section>';host._built=1;}
  var types=P.byType(S.cur,setCur()).map(function(x){return {label:P.label(P.TYPE_LABEL,x.key),t:x.t,key:x.key};}).sort(function(a,b){return (def.get(b.t)||0)-(def.get(a.t)||0);});
  [["spType","Campaign type","What kind of campaigns spend it",types,true],
   ["spDevice","Device","Phones, desktops and tablets",extraRows("device",P.DEVICE_LABEL),false],
   ["spNetwork","Network","Where the ads were shown",extraRows("network",P.NETWORK_LABEL),false]].forEach(function(d){
    var el=$("#"+d[0]), rows=d[3], pend=!d[4]&&!S.extras, err=!d[4]&&S.extras&&S.extras.error;
    destroyChart(d[0]);
    if(pend){el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+esc(d[1])+'</span><span class="ps-sub">'+esc(d[2])+'</span></div></div><div class="ps-skel" style="height:190px"></div>';return;}
    if(err){el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+esc(d[1])+'</span></div></div><p class="ps-msg-inline">Not available right now: '+esc(S.extras.error)+'</p>';return;}
    rows=rows.filter(function(r){return (def.get(r.t)||0)>0;}).sort(function(a,b){return def.get(b.t)-def.get(a.t);});
    el.innerHTML=donutCard(d[0],d[1],d[4]&&S.filter.length?d[2]+" (filtered)":(!d[4]&&S.filter.length?d[2]+" (whole account)":d[2]),rows,key,{click:d[4]});
    var cv=$("canvas",el); if(!cv) return;
    mkChart(d[0],cv,{type:"doughnut",data:{labels:rows.map(function(r){return r.label;}),datasets:[{data:rows.map(function(r){return def.get(r.t)||0;}),
      backgroundColor:rows.map(function(r,i){return scol(i);}),borderColor:th.panel,borderWidth:3,hoverOffset:6,borderRadius:5}]},
      options:{responsive:true,maintainAspectRatio:false,cutout:"72%",plugins:{legend:{display:false},tooltip:Object.assign(tipBase(th),{callbacks:{label:function(c){
        var r=rows[c.dataIndex], tot=c.dataset.data.reduce(function(a,b){return a+b;},0);
        return " "+r.label+":  "+fm(key,c.raw)+"  ("+Math.round(c.raw/tot*100)+"%)";}}})}}});
    if(d[4]) $$("li.click",el).forEach(function(li){li.onclick=function(){var r=rows[+li.dataset.i]; if(r) toggleIds(S.cur.campaigns.filter(function(c){return c.type===r.key;}).map(function(c){return c.id;}));};});
  });
}
function extraRows(k,map){
  if(!S.extras||S.extras.error) return [];
  return (S.extras[k]||[]).map(function(r){return {label:P.label(map,r[0]),t:r.slice(1,1+P.NB)};}).sort(function(a,b){return (P.METRICS[S.split].get(b.t)||0)-(P.METRICS[S.split].get(a.t)||0);});
}

/* ── Heatmap: when it happens ─────────────────────────────────── */
var HEAT=["conversions","clicks","cost","impressions","cpa","cvr","ctr","cpc"];
function paintHeat(){
  var el=$("#heat"); if(!S.cur) return;
  var head='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">When it happens</span><span class="ps-sub">Day and hour, whole account</span></div>'+
    '<div class="ps-card-actions"><button type="button" class="ps-sel" data-act="hm"><span class="k">Show</span><b>'+esc(P.METRICS[S.heat].label)+'</b><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button></div></div>';
  if(!S.extras){el.innerHTML=head+'<div class="ps-skel" style="height:230px"></div>';bindHeat(el);return;}
  if(S.extras.error){el.innerHTML=head+'<p class="ps-msg-inline">Not available right now: '+esc(S.extras.error)+'</p>';bindHeat(el);return;}
  var g=P.hourGrid(S.extras.dowHour), def=P.METRICS[S.heat], vals=[], mn=Infinity, mx=-Infinity;
  for(var d=0;d<7;d++)for(var h=0;h<24;h++){
    var t=g[d][h], v=t[P.IMPR]||t[P.CLK]||t[P.COST]?def.get(t):null;
    if(def.kind==="money2"||def.kind==="pct"){ if(t[P.CLK]<3) v=null; if(S.heat==="cpa"&&t[P.CONV]<1) v=null; }
    vals.push(v); if(v!=null){mn=Math.min(mn,v);mx=Math.max(mx,v);}
  }
  var cells="", best=[], k=0;
  for(var dd=0;dd<7;dd++){
    cells+='<span class="hl">'+P.DOW[dd]+'</span>';
    for(var hh=0;hh<24;hh++){
      var v2=vals[k++], inten=0;
      if(v2!=null&&mx>0){inten=def.better===-1?(mx===mn?1:(mx-v2)/(mx-mn)):(mx===mn?1:v2/mx);}
      if(v2!=null) best.push({d:dd,h:hh,v:v2,inten:inten});
      cells+='<i class="hc'+(v2==null?" nil":"")+'" data-d="'+dd+'" data-h="'+hh+'" style="--q:'+inten.toFixed(3)+'"></i>';
    }
  }
  var axis='<span></span>'+[0,3,6,9,12,15,18,21].map(function(h){return '<span class="ha" style="grid-column:'+(h+2)+' / span 3">'+HOURLBL(h)+'</span>';}).join("");
  best.sort(function(a,b){return b.inten-a.inten||b.v-a.v;});
  var top=best.slice(0,3).map(function(b){return P.DOW[b.d]+" "+HOURLBL(b.h);}).join(", ");
  var dayT=P.DOW.map(function(_,d){var t=P.zero();for(var h2=0;h2<24;h2++)P.addTo(t,g[d][h2]);return t;}), hourT=P.byHour(g);
  function bestOf(arr){var bi=-1,bv=null;arr.forEach(function(t,i){var v=def.get(t); if(v==null||(def.kind!=="money"&&def.kind!=="int"&&def.kind!=="num"&&t[P.CLK]<10)) return;
    if(bi<0||(def.better===-1?v<bv:v>bv)){bi=i;bv=v;}}); return bi<0?null:{i:bi,v:bv};}
  var bd=bestOf(dayT), bh=bestOf(hourT), wkend=P.zero(), all=P.zero(); dayT.forEach(function(t,i){P.addTo(all,t); if(i>=5) P.addTo(wkend,t);});
  var bi={cost:P.COST,impressions:P.IMPR,clicks:P.CLK,conversions:P.CONV,value:P.VAL}[S.heat];      // only a plain total has a "share"
  var wShare=bi!=null&&all[bi]?wkend[bi]/all[bi]:null;
  var stats='<div class="ps-hstats"><div class="ps-hstat"><span>Best day</span><b>'+(bd?P.DOW[bd.i]:"\u2013")+'</b><small>'+(bd?esc(fm(S.heat,bd.v,true)):"")+'</small></div>'+
    '<div class="ps-hstat"><span>Best hour</span><b>'+(bh?HOURLBL(bh.i):"\u2013")+'</b><small>'+(bh?esc(fm(S.heat,bh.v,true)):"")+'</small></div>'+
    '<div class="ps-hstat"><span>Weekends</span><b>'+(wShare==null?"\u2013":Math.round(wShare*100)+"%")+'</b><small>'+(wShare==null?"not a total":"of the "+esc(def.label.toLowerCase()))+'</small></div></div>';
  el.innerHTML=head+'<div class="ps-heat"><div class="ps-hgrid">'+axis+cells+'</div><div class="ps-htip" hidden></div></div>'+stats+
    '<p class="ps-note">'+(top?'Strongest windows for '+esc(def.label.toLowerCase())+': <b>'+esc(top)+'</b>.':'Not enough activity to show a pattern.')+
    ' Times are in the account’s own time zone'+(S.acct.tz?' ('+esc(S.acct.tz)+')':'')+'.</p>';
  bindHeat(el,g,def);
}
function bindHeat(el,g,def){
  var b=$("[data-act=hm]",el);
  if(b) b.onclick=function(){popMenu(b,{items:function(){return HEAT.filter(function(k){return true;}).map(function(k){return {key:k,label:P.METRICS[k].label,sub:P.METRICS[k].hint,color:mcol(k),checked:S.heat===k};});},
    onPick:function(k){S.heat=k;paintHeat();writeHash();}});};
  var grid=$(".ps-hgrid",el), tip=$(".ps-htip",el); if(!grid||!tip||!g) return;
  grid.addEventListener("mousemove",function(e){
    var c=e.target.closest(".hc"); if(!c){tip.hidden=true;return;}
    var t=g[+c.dataset.d][+c.dataset.h], r=c.getBoundingClientRect(), pr=$(".ps-heat",el).getBoundingClientRect();
    tip.hidden=false;
    tip.innerHTML='<b>'+P.DOW[+c.dataset.d]+' '+HOURLBL(+c.dataset.h)+'</b>'+["conversions","clicks","cost","cpa"].map(function(k){return '<span>'+esc(P.METRICS[k].label)+'<em>'+esc(fm(k,P.METRICS[k].get(t)))+'</em></span>';}).join("");
    tip.style.left=Math.max(4,Math.min(r.left-pr.left-70,pr.width-170))+"px"; tip.style.top=(r.top-pr.top-tip.offsetHeight-8)+"px";
  });
  grid.addEventListener("mouseleave",function(){tip.hidden=true;});
}

/* ── Campaign map: any metric against any other ───────────────── */
var TYPE_COL={SEARCH:"#22d3ee",PERFORMANCE_MAX:"#a3e635",SHOPPING:"#fbbf24",DISPLAY:"#e879f9",VIDEO:"#fb7185",DEMAND_GEN:"#818cf8",SMART:"#fb923c",LOCAL:"#2dd4bf"};
function paintScatter(){
  var el=$("#scatter"); if(!S.cur) return;
  var sc=S.scat, th=theme(), C=cur();
  function sel(k,label){return '<button type="button" class="ps-sel" data-act="sc-'+k+'"><span class="k">'+label+'</span><b>'+esc(P.METRICS[sc[k]].label)+'</b><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>';}
  var per=P.perCampaign(S.cur), set=setCur(), pts=[];
  S.cur.campaigns.forEach(function(c,i){
    var t=per[i]; if(!t[P.COST]&&!t[P.IMPR]) return;
    var x=P.METRICS[sc.x].get(t), y=P.METRICS[sc.y].get(t), s=P.METRICS[sc.size].get(t);
    if(x==null||y==null) return;
    pts.push({x:x,y:y,s:s||0,c:c,on:!set||set.has(i)});
  });
  pts.sort(function(a,b){return b.s-a.s;}); pts=pts.slice(0,80);
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">Campaign map</span><span class="ps-sub">Every campaign, placed by any two metrics. Click a bubble to focus.</span></div></div>'+
    '<div class="ps-ctrl-row">'+sel("x","Across")+sel("y","Up")+sel("size","Size")+'</div><div class="ps-wrap ps-wrap-sm"><canvas role="img" aria-label="Campaign map"></canvas></div><div class="ps-tlegend"></div>';
  ["x","y","size"].forEach(function(k){
    var b=$("[data-act=sc-"+k+"]",el);
    b.onclick=function(){popMenu(b,{search:"Find a metric\u2026",items:function(){return metricItems({chart:true,checked:function(m){return sc[k]===m;}});},
      onPick:function(m){sc[k]=m;paintScatter();writeHash();}});};
  });
  if(!pts.length){$(".ps-wrap",el).innerHTML='<p class="ps-msg-inline">No campaigns to place for these metrics.</p>';return;}
  var maxS=Math.max.apply(null,pts.map(function(p){return p.s;}))||1, types={};
  pts.forEach(function(p){types[p.c.type]=1;});
  var tt=P.total(S.cur,set), ax=P.METRICS[sc.x].get(tt), ay=P.METRICS[sc.y].get(tt);
  var avg={id:"psAvg",afterDatasetsDraw:function(ch){
    var a=ch.chartArea, c=ch.ctx, sx=ch.scales.x, sy=ch.scales.y; c.save(); c.strokeStyle=th.line2; c.setLineDash([5,5]); c.lineWidth=1;
    if(ax!=null&&ax>=sx.min&&ax<=sx.max){var px=sx.getPixelForValue(ax); c.beginPath(); c.moveTo(px,a.top); c.lineTo(px,a.bottom); c.stroke();}
    if(ay!=null&&ay>=sy.min&&ay<=sy.max){var py=sy.getPixelForValue(ay); c.beginPath(); c.moveTo(a.left,py); c.lineTo(a.right,py); c.stroke();}
    c.restore();}};
  var tip=tipBase(th); tip.callbacks={title:function(it){return it[0].raw.c.name;},label:function(c){
    var t=per[S.cur.campaigns.indexOf(c.raw.c)];
    return [P.METRICS[sc.x].label+":  "+fm(sc.x,c.raw.x),P.METRICS[sc.y].label+":  "+fm(sc.y,c.raw.y),P.METRICS[sc.size].label+":  "+fm(sc.size,P.METRICS[sc.size].get(t)),
      P.label(P.TYPE_LABEL,c.raw.c.type)+(c.raw.c.status&&c.raw.c.status!=="ENABLED"?" · "+c.raw.c.status.toLowerCase():"")];}};
  mkChart("scatter",$("canvas",el),{type:"bubble",plugins:[avg],
    data:{datasets:[{data:pts.map(function(p){return {x:p.x,y:p.y,r:4+16*Math.sqrt(p.s/maxS),c:p.c};}),
      backgroundColor:pts.map(function(p){return alpha(tcol(p.c.type),p.on?(S.filter.length?.95:.62):.12);}),
      borderColor:pts.map(function(p){return tcol(p.c.type);}),borderWidth:pts.map(function(p){return p.on&&S.filter.length?2.5:1;}),hoverBorderWidth:3}]},
    options:{responsive:true,maintainAspectRatio:false,layout:{padding:{top:14,right:16}},
      onClick:function(e,els){if(els.length) toggleIds([pts[els[0].index].c.id]);},
      onHover:function(e,els){e.native.target.style.cursor=els.length?"pointer":"default";},
      scales:{x:{beginAtZero:true,grace:"5%",title:{display:true,text:P.METRICS[sc.x].label,color:th.muted},grid:{color:th.line,drawTicks:false},border:{display:false},
          ticks:{color:th.muted,padding:6,maxTicksLimit:6,callback:function(v){return P.fmt(P.METRICS[sc.x].kind,v,C,true);}}},
        y:{beginAtZero:true,grace:"8%",title:{display:true,text:P.METRICS[sc.y].label,color:th.muted},grid:{color:th.line,drawTicks:false},border:{display:false},
          ticks:{color:th.muted,padding:6,maxTicksLimit:6,callback:function(v){return P.fmt(P.METRICS[sc.y].kind,v,C,true);}}}},
      plugins:{legend:{display:false},tooltip:tip}}});
  $(".ps-tlegend",el).innerHTML=Object.keys(types).map(function(k){return '<span><i style="background:'+(tcol(k))+'"></i>'+esc(P.label(P.TYPE_LABEL,k))+'</span>';}).join("")+
    '<span class="avg">Dashed lines: account average</span>';
}

/* ── Campaign table ───────────────────────────────────────────── */
var COLS=[
  {k:"cost",l:"Spend",def:1},{k:"impressions",l:"Impr."},{k:"clicks",l:"Clicks",def:1},{k:"ctr",l:"CTR",def:1},{k:"cpc",l:"Avg. CPC",def:1},
  {k:"conversions",l:"Conv.",def:1},{k:"cvr",l:"Conv. rate"},{k:"cpa",l:"Cost / conv.",def:1},{k:"value",l:"Conv. value",val:1},{k:"roas",l:"ROAS",def:1,val:1},
  {k:"sis",l:"Search IS",def:1},{k:"trend",l:"Trend",def:1}
];
function tableCols(){
  if(!S.table.cols){S.table.cols={};COLS.forEach(function(c){S.table.cols[c.k]=!!c.def;});}
  return COLS.filter(function(c){return S.table.cols[c.k]&&(!c.val||hasValue());});
}
function paintTable(){
  var el=$("#camps"); if(!S.cur) return;
  if(!S.cd) S.cd=P.campaignDaily(S.cur);
  var per=P.perCampaign(S.cur), cmpPer=null, cidx={}, tb=S.table, cols=tableCols(), C=cur();
  if(S.cmp){cmpPer=P.perCampaign(S.cmp); S.cmp.campaigns.forEach(function(c,i){cidx[c.id]=i;});}
  var rows=S.cur.campaigns.map(function(c,i){return {i:i,c:c,t:per[i],p:cmpPer&&cidx[c.id]!=null?cmpPer[cidx[c.id]]:null};}).filter(function(r){return r.t[P.COST]||r.t[P.IMPR];});
  var q=tb.q.trim().toLowerCase();
  if(q) rows=rows.filter(function(r){return (r.c.name+" "+P.label(P.TYPE_LABEL,r.c.type)+" "+r.c.status).toLowerCase().indexOf(q)>=0;});
  var tc=P.total(S.cur,q?new Set(rows.map(function(r){return r.i;})):null), totalSpend=tc[P.COST]||1;
  function val(r,k){
    if(k==="sis") return S.sis&&S.sis.campaigns[r.c.id]!=null?S.sis.campaigns[r.c.id]:null;
    if(k==="trend") return null;
    return P.METRICS[k].get(r.t);
  }
  var sk=tb.sort==="name"?null:tb.sort;
  rows.sort(function(a,b){
    if(!sk) return tb.dir*a.c.name.localeCompare(b.c.name);
    var x=val(a,sk),y=val(b,sk); x=x==null?-Infinity:x; y=y==null?-Infinity:y; return tb.dir*(x-y)||b.t[P.COST]-a.t[P.COST];
  });
  var shown=tb.all||q?rows:rows.slice(0,15), sel={}; S.filter.forEach(function(i){sel[i]=1;});
  var th='<th class="chk"></th><th class="name sortable'+(tb.sort==="name"?" sorted":"")+'" data-sort="name">Campaign'+(tb.sort==="name"?'<i>'+(tb.dir>0?"▲":"▼")+'</i>':"")+'</th>'+cols.map(function(c){
    return '<th class="num'+(c.k!=="trend"?" sortable":"")+(tb.sort===c.k?" sorted":"")+'"'+(c.k!=="trend"?' data-sort="'+c.k+'"':"")+'>'+esc(c.l)+(tb.sort===c.k?'<i>'+(tb.dir>0?"▲":"▼")+'</i>':"")+'</th>';}).join("");
  var tbody=shown.map(function(r){
    var st=(r.c.status||"").toLowerCase();
    var cells=cols.map(function(c){
      if(c.k==="trend"){
        var v=S.cd[r.i].map(function(t){return P.METRICS[tb.trend].get(t);});
        return '<td class="num trend">'+sparkSvg(v,mcol(tb.trend))+'</td>';
      }
      var v2=val(r,c.k), txt=c.k==="sis"?(v2==null?"–":P.fmt("pct",v2)):fm(c.k,v2,c.k==="cost"||c.k==="impressions"||c.k==="value");
      var d="";
      if(r.p&&(c.k==="cost"||c.k==="conversions"||c.k==="cpa")){var ch=P.change(c.k,v2,P.METRICS[c.k].get(r.p)); d='<small class="d '+ch.tone+'">'+esc(ch.text)+'</small>';}
      var bar=c.k==="cost"?'<span class="sbar"><s style="width:'+Math.min(100,r.t[P.COST]/totalSpend*100).toFixed(1)+'%"></s></span>':"";
      return '<td class="num">'+esc(txt)+d+bar+'</td>';
    }).join("");
    return '<tr class="'+(sel[r.c.id]?"sel":"")+'" data-id="'+esc(r.c.id)+'"><td class="chk"><input type="checkbox" aria-label="Focus on '+esc(r.c.name)+'"'+(sel[r.c.id]?" checked":"")+'></td>'+
      '<td class="name"><span class="cn">'+esc(r.c.name)+'</span><span class="cm"><i class="st '+esc(st)+'"></i>'+esc(st||"")+' · '+esc(P.label(P.TYPE_LABEL,r.c.type))+(r.c.budget?' · '+esc(P.fmt("money",r.c.budget,C))+'/day':'')+'</span></td>'+cells+'</tr>';
  }).join("");
  var tot='<tr class="tot"><td></td><td class="name"><span class="cn">'+(q?"Matching campaigns":S.filter.length?"Whole account":"All campaigns")+'</span></td>'+cols.map(function(c){
    if(c.k==="trend") return "<td></td>"; if(c.k==="sis") return '<td class="num">'+(S.sis&&S.sis.account!=null?P.fmt("pct",S.sis.account):"–")+'</td>';
    return '<td class="num">'+esc(fm(c.k,P.METRICS[c.k].get(tc),c.k==="cost"||c.k==="impressions"||c.k==="value"))+'</td>';}).join("")+'</tr>';
  el.innerHTML='<div class="ps-tools"><label class="ps-search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m20 20-4-4"/></svg><input id="cq" placeholder="Find a campaign…" value="'+esc(tb.q)+'" autocomplete="off"></label>'+
    '<div class="ps-tools-r"><button type="button" class="ps-sel" data-act="trend"><span class="k">Trend</span><b>'+esc(P.METRICS[tb.trend].label)+'</b><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>'+
    '<button type="button" class="ps-sel" data-act="cols"><span class="k">Columns</span><b>'+cols.length+'</b><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button></div></div>'+
    '<div class="ps-tscroll"><table class="ps-table"><thead><tr>'+th+'</tr></thead><tbody>'+(tbody||'<tr><td colspan="'+(cols.length+2)+'" class="empty">No campaigns match.</td></tr>')+'</tbody><tfoot>'+tot+'</tfoot></table></div>'+
    (rows.length>15&&!q?'<button type="button" class="ps-more" id="moreRows">'+(tb.all?"Show the top 15":"Show all "+rows.length+" campaigns")+'</button>':'');
  $("#campHint").textContent="Tick campaigns to focus the whole page on them. "+rows.length+" with activity.";
  $("#cq").oninput=function(){tb.q=this.value;var pos=this.selectionStart;paintTable();var n=$("#cq");n.focus();n.setSelectionRange(pos,pos);};
  var more=$("#moreRows"); if(more) more.onclick=function(){tb.all=!tb.all;paintTable();};
  $$("th[data-sort]",el).forEach(function(h){h.onclick=function(){var k=h.dataset.sort; if(tb.sort===k) tb.dir*=-1; else{tb.sort=k;tb.dir=k==="name"?1:-1;} paintTable();};});
  $$("tbody tr[data-id]",el).forEach(function(r){r.querySelector("input").onchange=function(){toggleIds([r.dataset.id]);};});
  $("[data-act=trend]",el).onclick=function(){popMenu(this,{items:function(){return ["cost","clicks","conversions","cpa","ctr"].map(function(k){return {key:k,label:P.METRICS[k].label,color:mcol(k),checked:tb.trend===k};});},
    onPick:function(k){tb.trend=k;paintTable();}});};
  $("[data-act=cols]",el).onclick=function(){popMenu(this,{multi:true,items:function(){return COLS.filter(function(c){return !c.val||hasValue();}).map(function(c){return {key:c.k,label:c.l,checked:!!tb.cols[c.k]};});},
    onPick:function(k){tb.cols[k]=!tb.cols[k];paintTable();}});};
}

/* ── Your charts (kept in this browser) ───────────────────────── */
var BKEY="ps_board_v1";
function loadBoard(){try{var b=JSON.parse(localStorage.getItem(BKEY)||"[]"); return Array.isArray(b)?b.filter(validSpec).slice(0,12):[];}catch(e){return [];}}
function validSpec(s){return s&&Array.isArray(s.metrics)&&s.metrics.length&&s.metrics.every(function(m){return P.METRICS[m];})&&P.DIM[s.dim]&&P.TYPE[s.type]&&(!s.series||["type","campaign"].indexOf(s.series)>=0);}
function saveBoard(){try{localStorage.setItem(BKEY,JSON.stringify(S.board));}catch(e){}}
function cleanSpec(s){return {metrics:s.metrics.slice(0,4),dim:s.dim,type:s.type,compare:!!s.compare,accounts:[],index:false,series:s.series||""};}
function pinChart(spec){
  if(S.board.length>=12){toast("Your charts are full (12). Remove one first.");return;}
  var c=cleanSpec(spec); if(spec.accounts&&spec.accounts.length){c.dim=P.DIM[c.dim].time?c.dim:"day";c.type=P.fixType(c.dim,c.type,false);}
  S.board.push(c); saveBoard(); paintBoard(); toast("Pinned. It is under Your charts.");
  $("#board").lastElementChild&&$("#board").lastElementChild.scrollIntoView({behavior:document.hidden||reduceMotion?"auto":"smooth",block:"center"});
}
function paintBoard(){
  var host=$("#board"); boardCards.forEach(function(c){c.destroy();}); boardCards=[]; host.innerHTML="";
  $("#boardHint").textContent=S.board.length?"Saved in this browser, for every account you open.":"Pin the chart above, or start a new one. Each keeps its own metrics and grouping, so you can build the view you want and it will be here next time.";
  S.board.forEach(function(spec){
    var card=ChartCard(host,spec,{onSpec:function(){saveBoard();},onRemove:function(c){
      S.board.splice(boardCards.indexOf(c),1); boardCards.splice(boardCards.indexOf(c),1); c.destroy(); saveBoard(); paintBoard();}});
    boardCards.push(card); card.update();
  });
}
$("#addChart").addEventListener("click",function(){
  if(S.board.length>=12){toast("Your charts are full (12). Remove one first.");return;}
  S.board.push({metrics:["conversions","cost"],dim:"campaign",type:"hbar",compare:true,accounts:[],index:false,series:""}); saveBoard(); paintBoard();
  $("#board").lastElementChild.scrollIntoView({behavior:document.hidden||reduceMotion?"auto":"smooth",block:"center"});
});

/* ── Export ───────────────────────────────────────────────────── */
function exportCsv(kind){
  var set=setCur(), C=cur(), rows=[], name="performance-"+slugify(S.acct.name)+"-"+S.range.start+"-"+S.range.end;
  if(kind==="daily"){
    var d=P.daily(S.cur,set);
    rows.push(["Date"].concat(P.METRIC_ORDER.map(function(k){return P.METRICS[k].label;})));
    S.cur.days.forEach(function(day,i){rows.push([day].concat(P.METRIC_ORDER.map(function(k){var v=P.METRICS[k].get(d[i]);return v==null?"":v;})));});
    download(name+"-daily.csv","text/csv;charset=utf-8",P.toCsv(rows));
  }else{
    var per=P.perCampaign(S.cur);
    rows.push(["Campaign","Status","Type","Daily budget"].concat(P.METRIC_ORDER.map(function(k){return P.METRICS[k].label;}),["Search impression share"]));
    S.cur.campaigns.forEach(function(c,i){
      if(set&&!set.has(i)) return; if(!per[i][P.COST]&&!per[i][P.IMPR]) return;
      rows.push([c.name,c.status,P.label(P.TYPE_LABEL,c.type),c.budget||""].concat(P.METRIC_ORDER.map(function(k){var v=P.METRICS[k].get(per[i]);return v==null?"":v;}),
        [S.sis&&S.sis.campaigns[c.id]!=null?S.sis.campaigns[c.id]:""]));
    });
    download(name+"-campaigns.csv","text/csv;charset=utf-8",P.toCsv(rows));
  }
  void C; toast("Downloaded");
}

/* ── Boot ─────────────────────────────────────────────────────── */
new MutationObserver(function(){if(S.cur) refresh();}).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});

function boot(){
  var h=readHash(); applyHash(h);
  S.board=loadBoard(); loadStudio(); loadKpis();
  picker=window.PMPicker($("#acctPick"),{label:"Account",search:true,onChange:function(id){
    var a=S.accounts.filter(function(x){return x.id===id;})[0]; if(!a||(S.acct&&a.id===S.acct.id)) return;
    S.acct=a; S.filter=[]; S.ex.accounts=S.ex.accounts.filter(function(x){return x!==a.id;});
    try{localStorage.setItem("ps_account",a.id);}catch(e){}
    load();
  }});
  getJson(BASE+"/accounts").then(function(j){
    S.accounts=j.accounts||[];
    if(!S.accounts.length){notice("warn","No accounts found","The Google Ads manager account has no enabled client accounts this sign-in can read.");$("#liveText").textContent="No accounts";return;}
    var last=""; try{last=localStorage.getItem("ps_account")||"";}catch(e){}
    var want=h.a||last;
    S.acct=S.accounts.filter(function(a){return a.id===want;})[0]||S.accounts[0];
    picker.set(S.accounts.map(function(a){return {value:a.id,label:a.name,sub:P.fmtId(a.id)+" · "+a.currency,avatar:P.initials(a.name),hue:P.hueOf(a.name)};}),S.acct.id);
    paintRanges(); computeRanges();
    if(j.usage&&j.usage.calls!=null&&j.usage.calls>j.usage.limit*0.8) toast("Google Ads request budget is "+Math.round(j.usage.calls/j.usage.limit*100)+"% used today");
    load();
  }).catch(showError);
  paintRanges();
}
S.range=P.presetRange(S.preset);
boot();
})();
