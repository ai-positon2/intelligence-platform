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

var DIMS = [
  {k:"day",l:"Day",time:1},{k:"week",l:"Week",time:1},{k:"month",l:"Month",time:1},
  {k:"dow",l:"Day of week"},{k:"hour",l:"Hour of day",ex:1},
  {k:"campaign",l:"Campaign",rank:1},{k:"type",l:"Campaign type",rank:1},
  {k:"device",l:"Device",ex:1,rank:1},{k:"network",l:"Network",ex:1,rank:1}
];
var DIM = {}; DIMS.forEach(function(d){DIM[d.k]=d;});
var SPLITS = ["cost","impressions","clicks","conversions","value"];

var S = {
  accounts:[], acct:null, preset:"last30", range:null, compare:"prev", cmpRange:null,
  cur:null, cmp:null, sis:null, extras:null,
  filter:[],
  ex:{metrics:["cost","conversions"],dim:"day",type:"area",compare:true,accounts:[],index:false},
  split:"cost", heat:"conversions", scat:{x:"cost",y:"conversions",size:"clicks"},
  table:{sort:"cost",dir:-1,q:"",cols:null,trend:"cost",all:false},
  board:[], seq:0, other:{}, memo:{}, cd:null
};
var explorer = null, boardCards = [], charts = {}, picker = null;

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
    }).join("")+'</div>';
    var q=$(".ps-pop-q",m);
    if(q){q.oninput=function(){var t=q.value.trim().toLowerCase();$$(".ps-pop-i",m).forEach(function(b){b.hidden=!!t&&b.textContent.toLowerCase().indexOf(t)<0;});};}
  }
  paint();
  m.addEventListener("click",function(e){
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

/* ── Notices and states ───────────────────────────────────────── */
function notice(kind,title,html){
  $("#notice").innerHTML=title?'<div class="ps-notice '+kind+'"><b>'+esc(title)+'</b><p>'+html+'</p></div>':"";
}
function showError(err){
  $("#dash").hidden=true;
  $("#liveText").textContent="Google Ads is not reachable";
  $("#live").classList.add("off");
  if(err.status===409) notice("setup","Google Ads is not connected yet",
    esc(err.message)+' An admin sets these on Railway (they are named <code>GOOGLE_ADS_*</code>), then reloads this page.');
  else if(err.status===429) notice("warn","Google Ads is busy",esc(err.message));
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
  if(DIM[h.g]) S.ex.dim=h.g;
  if(["line","area","bar"].indexOf(h.t)>=0) S.ex.type=h.t;
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
    var ids={}; S.cur.campaigns.forEach(function(c){ids[c.id]=1;});
    S.filter=S.filter.filter(function(i){return ids[i];});
    $("#live").classList.remove("off");
    $("#liveText").textContent="Live from Google Ads · "+S.accounts.length+" accounts · "+S.acct.name+" in "+S.acct.currency;
    $("#dash").hidden=false;
    paintAll();
    loadExtras(seq);
  }).catch(function(err){if(seq===S.seq) showError(err);});
}
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
    boardCards.forEach(function(c){c.destroy();}); boardCards=[];
    ["#explorer","#splits","#heat","#scatter","#camps","#board"].forEach(function(s){$(s).innerHTML="";});
    $("#splits")._built=0; ["spDevice","spNetwork","spType","scatter"].forEach(destroyChart);
    paintSplitSeg(); paintFilter(); writeHash(); return;
  }
  notice("");
  paintFilter(); paintKpis(); paintExplorer(); paintInsights(); paintSplitSeg(); paintSplits(); paintHeat(); paintScatter(); paintTable(); paintBoard();
  writeHash();
}
/* A filter or pin changed: repaint everything that depends on it, without asking Google again. */
function refresh(){
  if(!S.cur) return;
  paintFilter(); paintKpis(); if(explorer) explorer.update(); paintInsights(); paintSplits(); paintHeat(); paintScatter(); paintTable();
  boardCards.forEach(function(c){c.update();}); writeHash();
}
function paintExtras(){
  if(explorer) explorer.update(); boardCards.forEach(function(c){c.update();}); paintSplits(); paintHeat();
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
var KPI_KEYS = ["cost","impressions","clicks","ctr","cpc","conversions","cvr","cpa","value","roas"];
function paintKpis(){
  var set=setCur(), cset=setCmp(), tc=P.total(S.cur,set), tp=S.cmp?P.total(S.cmp,cset):null, days=P.daily(S.cur,set), val=hasValue(), h="";
  KPI_KEYS.forEach(function(k){
    if(!val&&(k==="value"||k==="roas")) return;
    var m=P.METRICS[k], v=m.get(tc), pv=tp?m.get(tp):null, ch=tp?P.change(k,v,pv):null, on=S.ex.metrics.indexOf(k)>=0, col=mcol(k);
    h+='<button type="button" class="ps-kpi'+(on?" on":"")+'" data-m="'+k+'" style="--kc:'+col+'" title="'+esc(m.hint)+'">'+
      '<span class="k">'+esc(m.label)+'</span><span class="v" data-v>'+fm(k,v,true)+'</span>'+
      (ch?'<span class="d '+ch.tone+'">'+(ch.dir>0?'<i>▲</i>':ch.dir<0?'<i>▼</i>':'')+esc(ch.text)+'<em>vs '+esc(fm(k,pv,true))+'</em></span>':'<span class="d flat"><em>'+esc(m.hint)+'</em></span>')+
      sparkSvg(days.map(function(t){return m.get(t);}),col)+'</button>';
  });
  if(S.sis&&S.sis.account!=null&&!S.filter.length){
    h+='<div class="ps-kpi ps-kpi-static" style="--kc:#38bdf8" title="How often your Search ads showed, out of the times they could have"><span class="k">Search impr. share</span>'+
      '<span class="v">'+P.fmt("pct",S.sis.account)+'</span><span class="d flat"><em>of the searches you could match</em></span>'+
      '<span class="ps-meter"><i style="width:'+Math.round(S.sis.account*100)+'%"></i></span></div>';
  }
  var old={}; $$("#kpis .ps-kpi[data-m] [data-v]").forEach(function(e){old[e.parentNode.dataset.m]=e._n;});
  $("#kpis").innerHTML=h;
  $$("#kpis .ps-kpi[data-m]").forEach(function(b,i){
    var k=b.dataset.m, e=$("[data-v]",b), to=P.METRICS[k].get(tc); e._n=to;
    countUp(e,old[k]==null?0:old[k],to,k);
    if(!reduceMotion&&!document.hidden) b.animate([{opacity:0,transform:"translateY(8px)"},{opacity:1,transform:"none"}],{duration:380,delay:i*35,easing:"cubic-bezier(.16,1,.3,1)",fill:"backwards"});
  });
}
$("#kpis").addEventListener("click",function(e){
  var b=e.target.closest(".ps-kpi[data-m]"); if(!b) return;
  var k=b.dataset.m, ms=S.ex.metrics.slice();
  if(e.shiftKey||e.metaKey||e.ctrlKey){
    var i=ms.indexOf(k);
    if(i>=0&&ms.length>1) ms.splice(i,1); else if(i<0){ms.push(k); if(ms.length>4) ms.shift();}
  }else ms=[k];
  S.ex.metrics=ms; explorer.spec.metrics=ms; explorer.update(); paintKpiState();
  $("#explorer").scrollIntoView({behavior:document.hidden||reduceMotion?"auto":"smooth",block:"nearest"}); writeHash();
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
    titleColor:"#eef1fb",bodyColor:"#c4cbe4",titleFont:{weight:"700",size:12.5},bodyFont:{size:12},usePointStyle:true,boxPadding:5,caretSize:6,displayColors:true};
}
var HOURLBL=function(h){return h===0?"12a":h<12?h+"a":h===12?"12p":(h-12)+"p";};

/* What a chart shows, from the loaded data: labels, a total per label, and the comparison's total per label. */
function buildPlan(spec){
  var dim=spec.dim, d=S.cur, set=setCur(), cd=S.cmp, cset=setCmp(), pl={dim:dim,mode:"v",labels:[],cur:[],cmp:null,keys:null,ids:null,note:""};
  var wantCmp=!!(cd&&spec.compare);
  if(spec.accounts&&spec.accounts.length&&DIM[dim].time) return accountsPlan(spec);
  if(DIM[dim].time){
    var b=P.bucket(d.days,P.daily(d,set),dim); pl.labels=b.labels; pl.keys=b.keys; pl.cur=b.totals;
    if(wantCmp){var cb=P.bucket(cd.days,P.daily(cd,cset),dim); pl.cmp=pl.labels.map(function(_,i){return cb.totals[i]||null;}); pl.cmpKeys=cb.keys;}
    return pl;
  }
  if(dim==="dow"){
    pl.labels=P.DOW.slice(); pl.cur=P.byWeekday(d.days,P.daily(d,set));
    if(wantCmp) pl.cmp=P.byWeekday(cd.days,P.daily(cd,cset));
    return pl;
  }
  if(DIM[dim].ex){
    if(!S.extras) return {pending:true};
    if(S.extras.error) return {error:"Google Ads would not give the "+DIM[dim].l.toLowerCase()+" split: "+S.extras.error};
    if(S.filter.length) pl.note="This split is for the whole account; it cannot be narrowed to campaigns.";
    if(dim==="hour"){
      var g=P.hourGrid(S.extras.dowHour); pl.cur=P.byHour(g); for(var h=0;h<24;h++) pl.labels.push(HOURLBL(h));
      return pl;
    }
  }
  /* ranked groupings */
  pl.mode="h"; var m0=spec.metrics[0], items=[];
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
    (S.extras[dim]||[]).forEach(function(r){items.push({label:P.label(map,r[0]),t:r.slice(1,6),c:null});});
  }
  items.sort(function(a,b){var x=P.mval(m0,a.t),y=P.mval(m0,b.t);return (y==null?-Infinity:y)-(x==null?-Infinity:x);});
  if(dim==="campaign"&&items.length>12){pl.note="Top 12 of "+items.length+" campaigns, ranked by "+P.METRICS[m0].label+".";items=items.slice(0,12);}
  pl.labels=items.map(function(i){return i.label;}); pl.cur=items.map(function(i){return i.t;});
  pl.cmp=wantCmp&&dim!=="device"&&dim!=="network"?items.map(function(i){return i.c;}):null;
  pl.ids=items.map(function(i){return i.ids||null;});
  return pl;
}
function otherKey(id){return id+"|"+S.range.start+"|"+S.range.end;}
function accountsPlan(spec){
  var m=spec.metrics[0], pl={dim:spec.dim,mode:"v",accounts:[],labels:[],keys:null,note:""}, cs=[];
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
  var ms=spec.metrics.map(function(m){return P.METRICS[m].label;});
  var t=ms.length>2?ms.slice(0,-1).join(", ")+" & "+ms[ms.length-1]:ms.join(" & ");
  if(spec.accounts&&spec.accounts.length&&DIM[spec.dim].time) return ms[0]+", account by account";
  return t+" by "+DIM[spec.dim].l.toLowerCase();
}

function seriesConfig(spec,pl,th,wrapId){
  var C=cur(), ms=spec.metrics.slice(0,4), ds=[], scales={}, many=ms.length>1;
  var tickCount=pl.labels.length;
  scales.x={offset:true,grid:{display:false},border:{color:th.line},ticks:{color:th.muted,maxRotation:0,autoSkip:true,maxTicksLimit:tickCount>16?10:16,padding:6}};
  ms.forEach(function(m,i){
    var def=P.METRICS[m], col=mcol(m), isBar=spec.type==="bar"&&i===0, fill=spec.type==="area";
    ds.push({type:isBar?"bar":"line",label:def.label,data:pl.cur.map(function(t){return def.get(t);}),yAxisID:"y"+i,borderColor:col,
      backgroundColor:isBar?grad(col,.95,.4):fill?grad(col,.34,0):col,borderWidth:isBar?0:2.4,borderRadius:isBar?7:0,borderSkipped:false,
      fill:fill,cubicInterpolationMode:"monotone",pointRadius:0,pointHoverRadius:5.5,pointHoverBorderWidth:2.5,pointHoverBackgroundColor:th.panel,pointHoverBorderColor:col,
      order:i+10,_m:m,maxBarThickness:36});
    if(pl.cmp) ds.push({type:"line",label:def.label+" (comparison)",data:pl.cmp.map(function(t){return t?def.get(t):null;}),yAxisID:"y"+i,
      borderColor:alpha(col,.55),borderDash:[5,4],borderWidth:1.6,pointRadius:0,pointHoverRadius:0,cubicInterpolationMode:"monotone",fill:false,order:i,_m:m,_cmp:true});
    scales["y"+i]={position:i%2?"right":"left",display:i<2,beginAtZero:true,grace:"6%",grid:{display:i===0,color:th.line,drawTicks:false},border:{display:false},
      ticks:{color:many?col:th.muted,padding:8,maxTicksLimit:6,callback:function(v){return P.fmt(def.kind,v,C,true);}}};
  });
  var tip=tipBase(th);
  tip.filter=function(it){return !it.dataset._cmp;};
  tip.callbacks={
    title:function(items){
      var i=items[0].dataIndex, k=pl.keys&&pl.keys[i];
      if(k&&spec.dim==="day") return P.DOW[P.dowOf(k)]+", "+items[0].label;
      if(k&&spec.dim==="week") return "Week of "+P.dayLabel(k);
      return items[0].label;
    },
    label:function(c){
      var m=c.dataset._m, v=c.raw, s=c.dataset.label+":  "+P.fmtMetric(m,v,C);
      if(pl.cmp&&pl.cmp[c.dataIndex]){var cv=P.METRICS[m].get(pl.cmp[c.dataIndex]); s+="   (was "+P.fmtMetric(m,cv,C,true)+", "+P.change(m,v,cv).text+")";}
      return s;
    }
  };
  return {type:"bar",data:{labels:pl.labels,datasets:ds},plugins:[crosshair],
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},scales:scales,
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
  tip.callbacks={title:function(items){var k=pl.keys&&pl.keys[items[0].dataIndex]; return k&&spec.dim==="day"?P.DOW[P.dowOf(k)]+", "+items[0].label:items[0].label;},
    label:function(c){return c.dataset.label+":  "+(spec.index?c.raw==null?"–":c.raw.toFixed(0)+" (index)":P.fmt(def.kind,c.raw,c.dataset._cur));}};
  return {type:"line",data:{labels:pl.labels,datasets:ds},plugins:[crosshair],
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      scales:{x:{offset:true,grid:{display:false},border:{color:th.line},ticks:{color:th.muted,maxRotation:0,autoSkip:true,maxTicksLimit:10,padding:6}},
        y:{beginAtZero:!spec.index,grace:"6%",grid:{color:th.line,drawTicks:false},border:{display:false},ticks:{color:th.muted,padding:8,maxTicksLimit:6,
          callback:function(v){return spec.index?v.toFixed(0):P.fmt(def.kind,v,C,true);}}}},
      plugins:{legend:{display:true,position:"bottom",labels:{color:th.ink2,usePointStyle:true,boxWidth:8,padding:16}},tooltip:tip}}};
}
function rankedConfig(spec,pl,th,onPick){
  var ms=spec.metrics.slice(0,4), m=ms[0], def=P.METRICS[m], col=mcol(m), C=cur(), ds=[];
  ds.push({label:def.label,data:pl.cur.map(function(t){return def.get(t);}),backgroundColor:alpha(col,.88),hoverBackgroundColor:col,borderRadius:8,borderSkipped:false,_m:m,barPercentage:.78,categoryPercentage:.86});
  if(pl.cmp) ds.push({label:def.label+" (comparison)",data:pl.cmp.map(function(t){return t?def.get(t):null;}),backgroundColor:alpha(col,.26),borderRadius:8,borderSkipped:false,_cmp:true,barPercentage:.78,categoryPercentage:.86});
  var tip=tipBase(th);
  tip.filter=function(it){return !it.dataset._cmp;};
  tip.callbacks={label:function(c){
    var t=pl.cur[c.dataIndex], out=[];
    ms.forEach(function(k,j){
      var s=P.METRICS[k].label+":  "+P.fmtMetric(k,P.METRICS[k].get(t),C);
      if(j===0&&pl.cmp&&pl.cmp[c.dataIndex]){var pv=P.METRICS[k].get(pl.cmp[c.dataIndex]); s+="   (was "+P.fmtMetric(k,pv,C,true)+", "+P.change(k,P.METRICS[k].get(t),pv).text+")";}
      out.push(s);
    });
    return out;
  }};
  var clickable=!!(pl.ids&&pl.ids[0]);
  return {type:"bar",data:{labels:pl.labels,datasets:ds},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,interaction:{mode:"nearest",axis:"y",intersect:false},
      onClick:function(e,els){if(clickable&&els.length&&pl.ids[els[0].index]) onPick(pl.ids[els[0].index]);},
      onHover:function(e,els){e.native.target.style.cursor=clickable&&els.length?"pointer":"default";},
      scales:{x:{beginAtZero:true,position:"bottom",grid:{color:th.line,drawTicks:false},border:{display:false},ticks:{color:th.muted,padding:6,maxTicksLimit:6,callback:function(v){return P.fmt(def.kind,v,C,true);}}},
        y:{grid:{display:false},border:{display:false},ticks:{color:th.ink2,padding:6,callback:function(v){var l=this.getLabelForValue(v); return l.length>30?l.slice(0,29)+"…":l;}}}},
      plugins:{legend:{display:false},tooltip:tip}}};
}

var cardSeq=0;
function ChartCard(host,spec,o){
  var id="cc"+(++cardSeq), el=document.createElement("section");
  el.className="pm-panel ps-card ps-chart"+(o.main?" ps-chart-main":"");
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title"></span><span class="ps-sub"></span></div>'+
    '<div class="ps-card-actions">'+(o.main?'<button type="button" class="ps-ico" data-act="pin" title="Keep this chart in Your charts"><svg viewBox="0 0 24 24"><path d="M12 17v5M9 3h6l-1 6 4 4H6l4-4z"/></svg><span>Pin</span></button>':'')+
    '<button type="button" class="ps-ico" data-act="csv" title="Download this chart’s numbers"><svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg><span>CSV</span></button>'+
    '<button type="button" class="ps-ico" data-act="png" title="Download as an image"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-8 9"/></svg><span>PNG</span></button>'+
    (o.onRemove?'<button type="button" class="ps-ico danger" data-act="rm" title="Remove this chart"><svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></button>':'')+'</div></div>'+
    '<div class="ps-ctrls"><div class="ps-mchips" data-metrics></div><div class="ps-ctrl-row">'+
    '<button type="button" class="ps-sel" data-act="dim"><span class="k">Group by</span><b></b><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>'+
    '<div class="ps-seg ps-seg-s" data-type role="radiogroup" aria-label="Chart type">'+
      '<button type="button" role="radio" data-t="line">Line</button><button type="button" role="radio" data-t="area">Area</button><button type="button" role="radio" data-t="bar">Bars</button></div>'+
    '<label class="ps-switch" data-cmp><input type="checkbox"><i></i><span></span></label>'+
    (o.main?'<button type="button" class="ps-sel" data-act="accts"><span class="k">Compare accounts</span><b></b><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>'+
      '<label class="ps-switch" data-index hidden><input type="checkbox"><i></i><span>Index to 100</span></label>':'')+
    '</div></div><div class="ps-wrap"><canvas role="img"></canvas><div class="ps-msg" hidden></div></div><p class="ps-note"></p>';
  host.appendChild(el);
  var canvas=$("canvas",el), wrap=$(".ps-wrap",el), msg=$(".ps-msg",el), plan=null;
  var api={el:el,spec:spec,update:update,destroy:function(){destroyChart(id);el.remove();},canvas:canvas,plan:function(){return plan;}};

  function changed(){if(o.onSpec) o.onSpec(spec); update();}
  function metricsHtml(){
    var h=spec.metrics.map(function(m,i){
      var dim=o.main&&spec.accounts.length&&i>0;
      return '<span class="ps-mchip'+(dim?" dim":"")+'" style="--c:'+mcol(m)+'"><i></i>'+esc(P.METRICS[m].label)+
        (spec.metrics.length>1?'<button type="button" data-rm="'+m+'" aria-label="Remove '+esc(P.METRICS[m].label)+'">×</button>':'')+'</span>';
    }).join("");
    return h+'<button type="button" class="ps-mchip add" data-act="addm"'+(spec.metrics.length>=4?' title="Up to four metrics at once"':'')+'><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>Metric</button>';
  }
  function controls(){
    $("[data-metrics]",el).innerHTML=metricsHtml();
    $(".ps-sel[data-act=dim] b",el).textContent=DIM[spec.dim].l;
    var ranked=DIM[spec.dim].rank, ts=$("[data-type]",el);
    $$("button",ts).forEach(function(b){b.setAttribute("aria-checked",String(b.dataset.t===spec.type));b.disabled=!!ranked;});
    ts.title=ranked?"Rankings are always drawn as bars":"";
    var cmpLabel=S.compare==="yoy"?"vs last year":"vs previous period", cs=$("[data-cmp]",el), ci=$("input",cs);
    var cmpOk=!!(S.compare!=="none"&&S.cmp)&&!(spec.accounts&&spec.accounts.length)&&spec.dim!=="hour"&&spec.dim!=="device"&&spec.dim!=="network";
    ci.checked=!!(spec.compare&&cmpOk); ci.disabled=!cmpOk; $("span",cs).textContent=cmpLabel; cs.classList.toggle("off",!cmpOk);
    cs.title=cmpOk?"":(S.compare==="none"?"Choose a comparison in the bar above":"This grouping has no comparison");
    if(o.main){
      var a=spec.accounts.length; $(".ps-sel[data-act=accts] b",el).textContent=a?"+"+a:"None";
      var ix=$("[data-index]",el); ix.hidden=!a; $("input",ix).checked=!!spec.index;
    }
  }
  function say(t,sub){msg.hidden=false;msg.innerHTML='<b>'+esc(t)+'</b>'+(sub?'<span>'+esc(sub)+'</span>':'');}
  function update(){
    if(!S.cur) return;
    controls();
    $(".ps-title",el).textContent=titleFor(spec);
    $(".ps-sub",el).textContent=S.acct?S.acct.name:"";
    var th=theme();
    function draw(){
      plan=buildPlan(spec);
      wrap.style.height=plan&&plan.mode==="h"?Math.max(280,Math.min(560,plan.labels.length*38+64))+"px":"";
      if(plan.pending){destroyChart(id);say("Loading…",DIM[spec.dim].ex?"Reading the "+DIM[spec.dim].l.toLowerCase()+" split from Google Ads":"Reading the other accounts");$(".ps-note",el).textContent="";return;}
      if(plan.error){destroyChart(id);say("Not available",plan.error);return;}
      var has=plan.accounts?plan.accounts.some(function(a){return a.totals.some(function(t){return t[0]||t[1]||t[2]||t[3];});}):plan.cur.some(function(t){return t[0]||t[1]||t[2]||t[3];});
      if(!has){destroyChart(id);say("Nothing to chart","There is no activity for this selection in the range.");$(".ps-note",el).textContent="";return;}
      msg.hidden=true;
      var cfg=plan.accounts?accountsConfig(spec,plan,th):plan.mode==="h"?rankedConfig(spec,plan,th,function(ids){toggleIds(ids);}):seriesConfig(spec,plan,th,id);
      mkChart(id,canvas,cfg);
      canvas.setAttribute("aria-label",titleFor(spec)+" for "+(S.acct?S.acct.name:""));
      var notes=[];
      if(plan.note) notes.push(plan.note);
      if(!plan.accounts&&plan.mode==="v"&&spec.metrics.length>1) notes.push("Each metric uses its own scale, so the lines show shape, not size.");
      if(plan.cmp) notes.push((plan.mode==="h"?"Fainter bar: ":"Dashed line: ")+(S.compare==="yoy"?"the same days last year":"the previous period")+".");
      if(!plan.accounts&&DIM[spec.dim].time&&spec.metrics.some(function(m){return m==="conversions"||m==="cpa"||m==="cvr"||m==="value"||m==="roas"||m==="aov";})&&P.diffDays(S.range.end,P.localToday())<=3)
        notes.push("The last few days can still gain conversions, because Google credits them up to a few days later.");
      if(plan.mode==="h"&&spec.metrics.length>1) notes.push("Ranked by "+P.METRICS[spec.metrics[0]].label+"; hover for the rest.");
      if(plan.mode==="h"&&plan.ids&&plan.ids[0]) notes.push("Click a bar to focus the whole page on it.");
      $(".ps-note",el).textContent=notes.join(" ");
    }
    if(spec.accounts&&spec.accounts.length&&DIM[spec.dim].time&&o.main){
      var need=spec.accounts.some(function(i){return !S.other[otherKey(i)];});
      if(need){plan={pending:true};destroyChart(id);say("Loading…","Reading the other accounts");ensureOthers(spec,draw);return;}
    }
    draw();
  }

  el.addEventListener("click",function(e){
    var rm=e.target.closest("[data-rm]"), act=e.target.closest("[data-act]"), ty=e.target.closest("[data-t]");
    if(rm){spec.metrics=spec.metrics.filter(function(m){return m!==rm.dataset.rm;}); if(o.main) S.ex.metrics=spec.metrics; changed(); if(o.main) paintKpiState(); return;}
    if(ty&&!ty.disabled){spec.type=ty.dataset.t; changed(); return;}
    if(!act) return;
    var a=act.dataset.act;
    if(a==="addm") popMenu(act,{multi:true,items:function(){
      var items=[], last=null;
      P.METRIC_ORDER.forEach(function(k){
        if(!hasValue()&&(k==="value"||k==="roas"||k==="aov")) return;
        var def=P.METRICS[k]; if(def.group!==last){items.push({head:def.group});last=def.group;}
        items.push({key:k,label:def.label,sub:def.hint,color:mcol(k),checked:spec.metrics.indexOf(k)>=0,disabled:spec.metrics.indexOf(k)<0&&spec.metrics.length>=4});
      });
      return items;},
      onPick:function(k){
        var i=spec.metrics.indexOf(k);
        if(i>=0){if(spec.metrics.length>1) spec.metrics.splice(i,1);} else if(spec.metrics.length<4) spec.metrics.push(k);
        if(o.main){S.ex.metrics=spec.metrics;paintKpiState();} changed();}});
    else if(a==="dim") popMenu(act,{items:function(){
      return [{head:"Over time"}].concat(DIMS.filter(function(d){return d.time;}).map(function(d){return {key:d.k,label:d.l,checked:spec.dim===d.k};}),
        [{head:"Cycles"}],DIMS.filter(function(d){return d.k==="dow"||d.k==="hour";}).map(function(d){return {key:d.k,label:d.l,checked:spec.dim===d.k};}),
        [{head:"Rank by"}],DIMS.filter(function(d){return d.rank;}).map(function(d){return {key:d.k,label:d.l,checked:spec.dim===d.k};}));},
      onPick:function(k){spec.dim=k; if(o.main) S.ex.dim=k; changed();}});
    else if(a==="accts") popMenu(act,{multi:true,search:"Find an account…",items:function(){
      return S.accounts.filter(function(x){return x.id!==S.acct.id;}).map(function(x){
        var on=spec.accounts.indexOf(x.id)>=0;
        return {key:x.id,label:x.name,sub:P.fmtId(x.id)+" · "+x.currency,checked:on,disabled:!on&&spec.accounts.length>=3};});},
      onPick:function(k){var i=spec.accounts.indexOf(k); if(i>=0) spec.accounts.splice(i,1); else if(spec.accounts.length<3) spec.accounts.push(k);
        S.ex.accounts=spec.accounts; if(spec.accounts.length&&!DIM[spec.dim].time){spec.dim="day";S.ex.dim="day";} changed();}});
    else if(a==="pin"&&o.onPin) o.onPin(spec);
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
  var rows=[]; var C=cur();
  if(pl.accounts){
    var m=spec.metrics[0];
    rows.push([spec.dim].concat(pl.accounts.map(function(a){return a.name+" ("+P.METRICS[m].label+")";})));
    pl.labels.forEach(function(l,i){rows.push([pl.keys?pl.keys[i]:l].concat(pl.accounts.map(function(a){var v=P.METRICS[m].get(a.totals[i]);return v==null?"":v;})));});
  }else{
    rows.push([DIM[spec.dim].l].concat(spec.metrics.map(function(m){return P.METRICS[m].label;}),pl.cmp?spec.metrics.map(function(m){return P.METRICS[m].label+" (comparison)";}):[]));
    pl.labels.forEach(function(l,i){
      rows.push([pl.keys?pl.keys[i]:l].concat(spec.metrics.map(function(m){var v=P.METRICS[m].get(pl.cur[i]);return v==null?"":v;}),
        pl.cmp?spec.metrics.map(function(m){var v=pl.cmp[i]?P.METRICS[m].get(pl.cmp[i]):null;return v==null?"":v;}):[]));
    });
  }
  download("chart-"+slugify(S.acct.name)+"-"+spec.dim+"-"+S.range.start+"-"+S.range.end+".csv","text/csv;charset=utf-8",P.toCsv(rows)); void C;
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
  return (S.extras[k]||[]).map(function(r){return {label:P.label(map,r[0]),t:r.slice(1,6)};}).sort(function(a,b){return (P.METRICS[S.split].get(b.t)||0)-(P.METRICS[S.split].get(a.t)||0);});
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
    b.onclick=function(){popMenu(b,{items:function(){return P.METRIC_ORDER.filter(function(m){return hasValue()||(m!=="value"&&m!=="roas"&&m!=="aov");}).map(function(m){return {key:m,label:P.METRICS[m].label,sub:P.METRICS[m].hint,color:mcol(m),checked:sc[k]===m};});},
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
function validSpec(s){return s&&Array.isArray(s.metrics)&&s.metrics.length&&s.metrics.every(function(m){return P.METRICS[m];})&&DIM[s.dim]&&["line","area","bar"].indexOf(s.type)>=0;}
function saveBoard(){try{localStorage.setItem(BKEY,JSON.stringify(S.board));}catch(e){}}
function cleanSpec(s){return {metrics:s.metrics.slice(0,4),dim:s.dim,type:s.type,compare:!!s.compare,accounts:[],index:false};}
function pinChart(spec){
  if(S.board.length>=12){toast("Your charts are full (12). Remove one first.");return;}
  var c=cleanSpec(spec); if(spec.accounts&&spec.accounts.length){c.dim=DIM[c.dim].time?c.dim:"day";}
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
  S.board.push({metrics:["conversions","cost"],dim:"campaign",type:"bar",compare:true,accounts:[],index:false}); saveBoard(); paintBoard();
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
  S.board=loadBoard();
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
