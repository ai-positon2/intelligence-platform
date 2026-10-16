/* Seasonality Analysis (Paid Marketing Agents).
 *
 * When in the year an account earns, and what to do about it. The server (tracker/paid_seasonality.py) reads two or three
 * years of the account's daily numbers and does all the analysis; sa_data.js turns what it sends into the figures shown, so a
 * number here is a number that is tested there. This file only draws.
 *
 * Reading a long history is done slice by slice (one year a request, newest first) so no request is long and a failure keeps
 * what was already read; the analysis is then asked for and answers from the saved days.
 *
 * The view is the URL (#pf=&a=account&m=months&yk=metric ...), so a link reproduces exactly what you were looking at.
 */
(function(){
"use strict";

var P = window.PSData, D = window.SAData, PL = window.PMPlatform, BASE = "/p2/paid-marketing/api/gads";
var $ = function(s,r){return (r||document).querySelector(s);};
var $$ = function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s));};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});};
var reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

var YEAR_COLORS = ["#ff7a59","#fbbf24","#38bdf8","#8b94be"];     // newest year first
var S = {accounts:[], acct:null, months:36, data:null, yk:"conv", yg:"month", ik:"conv", dk:"conv", seq:0, memo:{}, fetchedAt:null};
var charts = {}, accts = null, hashTimer = null;

/* ── Helpers ─────────────────────────────────────────────────── */
function alpha(hex,a){var n=parseInt(hex.slice(1),16);return "rgba("+(n>>16&255)+","+(n>>8&255)+","+(n&255)+","+a+")";}
function light(){return document.documentElement.getAttribute("data-theme")==="light";}
function shade(hex,k){var n=parseInt(hex.slice(1),16);function c(v){return Math.round(v*k).toString(16).padStart(2,"0");}return "#"+c(n>>16&255)+c(n>>8&255)+c(n&255);}
function col(hex){return light()?shade(hex,.72):hex;}
function theme(){
  var cs=getComputedStyle($("#sa")), g=function(n,d){return (cs.getPropertyValue(n)||"").trim()||d;};
  return {ink:g("--pm-ink","#e8ecf8"),muted:g("--pm-muted","#8b94be"),faint:g("--pm-faint","#5d6688"),
    line:g("--pm-line","rgba(255,255,255,.07)"),line2:g("--pm-line2","rgba(255,255,255,.12)")};
}
function cur(){return S.acct?S.acct.currency:"USD";}
function fmt(key,v,small){return D.fmt(key,v,cur(),small);}
function toast(msg){
  var t=$("#psToast"); if(!t){t=document.createElement("div");t.id="psToast";t.className="ps-toast";document.body.appendChild(t);}
  t.textContent=msg; t.classList.add("on"); clearTimeout(t._t); t._t=setTimeout(function(){t.classList.remove("on");},2200);
}
function download(name,mime,text){
  var a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([text],{type:mime})); a.download=name;
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
}
function slugify(s){return String(s).toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-+|-+$/g,"").slice(0,40)||"account";}
function destroyChart(id){if(charts[id]){charts[id].destroy();delete charts[id];}}
function mkChart(id,canvas,cfg){destroyChart(id);charts[id]=new Chart(canvas,cfg);return charts[id];}
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
  el.title=iso?"The oldest day on screen was last read from "+plat()+" "+new Date(iso).toLocaleString():"";
}
setInterval(function(){if(S.fetchedAt) paintUpdated(S.fetchedAt);},30000);

Chart.defaults.font.family="'Space Grotesk',system-ui,sans-serif";
Chart.defaults.font.size=11.5;
Chart.defaults.animation.duration=reduceMotion?0:650;
function tip(){return {backgroundColor:"rgba(9,12,24,.97)",titleColor:"#eef1fb",bodyColor:"#c4cbe4",borderColor:"rgba(255,255,255,.14)",borderWidth:1,padding:11,cornerRadius:12,boxPadding:5};}
function axes(o){
  var t=theme(); o=o||{};
  return {
    x:{grid:{display:false},border:{color:t.line2},ticks:{color:t.muted,maxRotation:0,autoSkip:true,maxTicksLimit:o.xTicks||12}},
    y:{beginAtZero:o.beginAtZero!==false,grid:{color:t.line},border:{display:false},ticks:{color:t.muted,callback:o.yFmt||function(v){return v;}},
       title:o.yTitle?{display:true,text:o.yTitle,color:t.faint}:undefined}
  };
}

/* ── Requests ────────────────────────────────────────────────── */
function getJson(url){
  return fetch(url,{credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){
      if(!r.ok){
        var e=new Error(j.error||("The server answered "+r.status));
        e.status=r.status; e.config=j.configProblem; e.cooldown=!!j.cooldown; e.retryIn=j.retryIn; e.blockedUntil=j.blockedUntil;
        throw e;
      }
      return j;
    });
  });
}
function q(extra){return "account="+encodeURIComponent(S.acct.id)+"&months="+S.months+(extra?"&"+extra:"");}

/* ── Notices ─────────────────────────────────────────────────── */
function notice(kind,title,html){
  $("#notice").innerHTML=title?'<div class="ps-notice '+kind+'"><b>'+esc(title)+'</b><p>'+html+'</p></div>':"";
}
function paintStale(stale,blockedUntil,warnings){
  var el=$("#staleBar"), t=function(x){return new Date(x).toLocaleString([], {weekday:"short",hour:"numeric",minute:"2-digit"});};
  var h="";
  (warnings||[]).forEach(function(w){h+=(h?" ":"")+'<b>Showing part of this account.</b> '+esc(w);});
  if(stale||blockedUntil) h+=(h?" ":"")+'<b>'+(stale?"Showing the copy saved "+esc(t(stale)):"Saved views still open")+'.</b> The platform has limited this app’s reads'+
    (blockedUntil?', so fresh numbers come back around <b>'+esc(t(blockedUntil*1000))+'</b>':'')+'.';
  el.hidden=!h; el.innerHTML=h;
}
function showError(err){
  var n=plat(), meta=n==="Meta Ads";
  $("#dash").hidden=true; $("#warm").hidden=true;
  $("#liveText").textContent=n+" is not reachable"; $("#live").classList.add("off");
  if(err.status===409) notice("setup",n+" is not connected yet",
    esc(err.message)+' An admin sets these on Railway (they are named <code>'+(meta?"META_ADS_*":"GOOGLE_ADS_*")+'</code>), then reloads this page.');
  else if(err.status===429) notice("warn",n+" is not answering right now",esc(err.message));
  else notice("bad","Could not load "+n,esc(err.message));
}

/* ── Hash state: the URL is the view ─────────────────────────── */
function writeHash(){
  clearTimeout(hashTimer);
  hashTimer=setTimeout(function(){
    if(!S.acct) return;
    var h=[["pf",accts?accts.platform():"google"],["a",S.acct.id],["m",S.months],["yk",S.yk],["yg",S.yg],["ik",S.ik],["dk",S.dk]];
    try{history.replaceState(null,"","#"+h.map(function(x){return x[0]+"="+encodeURIComponent(x[1]);}).join("&"));}catch(e){}
  },250);
}
function readHash(){
  var h={}; location.hash.replace(/^#/,"").split("&").forEach(function(p){
    var i=p.indexOf("="); if(i>0) h[p.slice(0,i)]=decodeURIComponent(p.slice(i+1));
  });
  return h;
}
function applyHash(h){
  if(h.m==="24"||h.m==="36") S.months=+h.m;
  ["yk","ik","dk"].forEach(function(k){if(D.BY[h[k]]) S[k]=h[k];});
  if(h.yg==="month"||h.yg==="week") S.yg=h.yg;
}

/* ── Command bar ─────────────────────────────────────────────── */
function paintMonths(){
  $$("#monthsSeg button").forEach(function(b){b.setAttribute("aria-checked",String(+b.dataset.m===S.months));});
}
$("#monthsSeg").addEventListener("click",function(e){
  var b=e.target.closest("[data-m]"); if(!b||+b.dataset.m===S.months) return;
  S.months=+b.dataset.m; paintMonths(); writeHash(); if(S.acct) load();
});
$("#shareBtn").addEventListener("click",function(){
  writeHash();
  setTimeout(function(){
    (navigator.clipboard?navigator.clipboard.writeText(location.href):Promise.reject()).then(function(){toast("Link to this exact view copied");},
      function(){toast("Copy the address bar to share this view");});
  },300);
});
$("#exportBtn").addEventListener("click",function(){
  if(!S.data||S.data.empty){toast("Nothing to export yet");return;}
  download("seasonality-"+slugify(S.acct.name)+".csv","text/csv;charset=utf-8",D.csv(S.data));
  toast("Downloaded the months and indexes");
});
$("#refreshBtn").addEventListener("click",function(){
  var btn=this; if(!S.acct||btn.classList.contains("busy")) return;
  btn.classList.add("busy"); btn.disabled=true;
  getJson(BASE+"/seasonality/warm?"+q("refresh=1")).then(function(){
    delete S.memo[memoKey()]; btn.classList.remove("busy"); btn.disabled=false; analyse(++S.seq,true); toast("Read from "+plat()+" just now");
  }).catch(function(err){
    btn.classList.remove("busy"); btn.disabled=false;
    toast(err.cooldown?err.message:"Could not refresh: "+err.message);
  });
});

/* ── Loading: read the history in slices, then ask for the analysis ── */
function memoKey(){return S.acct.id+"|"+S.months;}
function warmPanel(on,title,sub,pct){
  var el=$("#warm"); el.hidden=!on; if(!on) return;
  $("#warmT").textContent=title; $("#warmS").textContent=sub||""; $("#warmBar").style.width=Math.max(4,Math.round((pct||0)*100))+"%";
}
function dateRange(a,b){return D.windowText(a,b)+", "+b.slice(0,4);}
function load(){
  var seq=++S.seq;
  notice(""); paintStale(null,null,[]);
  $("#dash").hidden=true;
  $("#liveText").textContent="Reading "+S.acct.name+" from "+plat()+"…";
  if(S.memo[memoKey()]){analyse(seq,false);return;}
  var parts=null, i=0, problems=[];
  warmPanel(true,"Reading the history…","",0);
  (function next(){
    getJson(BASE+"/seasonality/warm?"+q("part="+i)).then(function(j){
      if(seq!==S.seq) return;
      parts=j.parts; (j.problems||[]).forEach(function(p){if(problems.indexOf(p)<0) problems.push(p);});
      i++;
      if(i<parts){
        warmPanel(true,"Reading year "+(i+1)+" of "+parts,dateRange(j.start,j.end)+" read. Next: an earlier year.",i/parts);
        next();
      }else{
        warmPanel(true,"Working out the shape of the year…","",1);
        analyse(seq,false);
      }
    }).catch(function(err){if(seq===S.seq) showError(err);});
  })();
}
function analyse(seq,quiet){
  getJson(BASE+"/seasonality?"+q()).then(function(j){
    if(seq!==S.seq) return;
    S.memo[memoKey()]=j; S.data=j;
    warmPanel(false);
    paintStale(j.stale,j.blockedUntil,j.warnings); paintUpdated(j.fetchedAt);
    $("#live").classList.remove("off");
    $("#liveText").textContent="Live from "+plat()+" · "+S.accounts.length+" accounts · "+S.acct.name+" in "+S.acct.currency;
    paintAll();
  }).catch(function(err){if(seq===S.seq) showError(err);});
}

/* ── Painting ────────────────────────────────────────────────── */
function paintAll(){
  var d=S.data;
  if(!d) return;
  if(d.empty){
    $("#dash").hidden=true;
    notice("warn","No spend to analyse yet","This account has no spend in the "+S.months/12+" years read, so there is no pattern to find. Pick another account, or come back once it has been running a while.");
    return;
  }
  notice("");
  $("#dash").hidden=false;
  paintHeads(); paintKpis(); paintAhead(); paintYoy(); paintIdx(); paintGap(); paintDow(); paintEvents(); paintTable();
  writeHash();
}

function paintHeads(){
  var d=S.data, c=d.coverage, el=$("#heads"), h="";
  $("#covHint").textContent="History read: "+D.monthsText(d.start,d.end)+" ("+c.monthsInARow+" months in a row with spend).";
  h+='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">In plain words</span><span class="ps-sub">Worked out from the numbers, not guessed</span></div></div>';
  if(d.headlines.length) h+='<ul class="sa-heads">'+d.headlines.map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ul>';
  else h+='<p class="ps-msg-inline">Nothing stands out yet.</p>';
  if(c.note) h+='<p class="sa-note">'+esc(c.note)+'</p>';
  el.innerHTML=h;
}

function paintKpis(){
  var tr=D.trailing(S.data.monthly), el=$("#kpis");
  if(!tr){el.innerHTML="";return;}
  var c=tr.cur, p=tr.prev, hasV=c.value>0, cards=[];
  function dl(key,now,was,low){
    var ch=p?D.change(now,was):null;
    if(ch==null) return '<span class="d flat"><em>'+(p?"No earlier figure":"No earlier year to compare")+'</em></span>';
    var good=low?ch<0:ch>0, tone=Math.abs(ch)<.02?"flat":good?"good":"bad";
    return '<span class="d '+tone+'">'+D.pct(ch)+'<em>vs the year before</em></span>';
  }
  cards.push({k:"Spend",c:"#fbbf24",v:fmt("cost",c.cost,true),d:dl("cost",c.cost,p&&p.cost,false)});
  cards.push({k:"Conversions",c:"#a3e635",v:fmt("conv",c.conv,true),d:dl("conv",c.conv,p&&p.conv,false)});
  var cpa=c.conv>0?c.cost/c.conv:null, pcpa=p&&p.conv>0?p.cost/p.conv:null;
  cards.push({k:"Cost / conv.",c:"#fb7185",v:fmt("cpa",cpa),d:dl("cpa",cpa,pcpa,true)});
  if(hasV){
    var ro=c.cost>0?c.value/c.cost:null, pro=p&&p.cost>0?p.value/p.cost:null;
    cards.push({k:"ROAS",c:"#38bdf8",v:fmt("roas",ro),d:dl("roas",ro,pro,false)});
  }
  el.style.setProperty("--cols",cards.length>3?2:1);
  el.innerHTML='<div class="sa-kpis-h">The last '+tr.months+' whole months</div>'+cards.map(function(x){
    return '<div class="ps-kpi ps-kpi-static" style="--kc:'+x.c+'"><div class="ps-kpi-h"><span class="k">'+esc(x.k)+'</span></div><span class="v">'+x.v+'</span>'+x.d+'<div class="ps-kpi-f"></div></div>';
  }).join("");
}

function paintAhead(){
  var u=S.data.upcoming||[], sec=$("#aheadSec"), el=$("#ahead");
  if(!u.length){
    sec.hidden=!S.data.coverage.yearly&&true;
    el.innerHTML='<div class="pm-panel ps-card"><p class="ps-msg-inline">A forecast of the next months needs at least '+S.data.coverage.needed+' months in a row of history. This account has '+S.data.coverage.monthsInARow+'.</p></div>';
    sec.hidden=false; return;
  }
  sec.hidden=false;
  el.innerHTML=u.map(function(m,i){
    var v=D.VERDICT[m.verdict]||D.VERDICT.unknown;
    var ev=(m.events||[]).map(function(e){
      var l=e.convLift==null?"no history yet":D.pct(e.convLift)+" conversions in past years";
      return '<li><b>'+esc(e.name)+'</b><span>'+esc(D.windowText(e.start,e.end))+' · '+esc(l)+'</span></li>';
    }).join("");
    return '<article class="pm-panel sa-mo" style="--vc:'+v.color+';--i:'+i+'">'+
      '<div class="sa-mo-h"><div><span class="sa-mo-n">'+esc(m.name)+'</span><span class="sa-mo-y">'+m.y+'</span></div><span class="sa-badge">'+esc(v.label)+'</span></div>'+
      '<div class="sa-idx">'+
        idxTile("Conversions",m.conv,"conv")+idxTile("Cost per conversion",m.cpa,"cpa")+idxTile("Spend",m.cost,"cost")+
      '</div>'+
      '<p class="sa-adv">'+esc(m.advice)+'</p>'+
      '<p class="sa-bud"><b>'+esc(D.budgetText(m.budget))+'</b></p>'+
      (m.n<=1&&m.conv!=null?'<p class="sa-small">One year of history behind this month, so treat it as a lead.</p>':'')+
      (ev?'<ul class="sa-ev">'+ev+'</ul>':'')+
    '</article>';
  }).join("");
}
function idxTile(label,v,key){
  var t=D.tone(v,S.data.thresholds), c=D.toneColor(t,key);
  return '<div class="sa-it"><span class="k">'+esc(label)+'</span><b style="color:'+c+'">'+(v==null?"–":Math.round(v))+'</b></div>';
}

/* Year over year: a line for each year across the months (or weeks). */
function paintYoy(){
  var d=S.data, el=$("#yoyCard"), ms=D.available(d.monthly), key=D.BY[S.yk]&&ms.some(function(m){return m.key===S.yk;})?S.yk:"conv";
  S.yk=key;
  var ov=D.overlay(d.monthly,d.weekly,key,S.yg), t=theme(), m=D.BY[key];
  $("#yoyHint").textContent=ov.years.length>1?"Each line is one calendar year. Read across to see whether the same months repeat.":"Only one year of history, so there is nothing to lay it against yet.";
  var opts=ms.map(function(x){return '<option value="'+x.key+'"'+(x.key===key?" selected":"")+'>'+esc(x.label)+(x.unit?" ("+x.unit+")":"")+'</option>';}).join("");
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+esc(m.label)+', year against year</span><span class="ps-sub">'+(S.yg==="week"?"Each week of the year (ISO weeks)":"Each calendar month")+'. Partial months are left out.</span></div>'+
    '<div class="ps-card-actions"><select class="ps-sel" id="yoyKey" aria-label="Metric">'+opts+'</select>'+
    '<div class="ps-seg ps-seg-s" id="yoyGrain" role="radiogroup" aria-label="Grain"><button type="button" role="radio" data-g="month" aria-checked="'+(S.yg==="month")+'">Months</button><button type="button" role="radio" data-g="week" aria-checked="'+(S.yg==="week")+'">Weeks</button></div></div></div>'+
    '<div class="ps-wrap"><canvas id="cvYoy" aria-label="'+esc(m.label)+' by '+(S.yg==="week"?"week":"month")+', one line per year"></canvas></div>';
  $("#yoyKey").onchange=function(){S.yk=this.value;paintYoy();writeHash();};
  $$("#yoyGrain button").forEach(function(b){b.onclick=function(){S.yg=b.dataset.g;paintYoy();writeHash();};});
  var n=ov.years.length, ds=ov.years.map(function(y,i){
    var rank=n-1-i, c=col(YEAR_COLORS[Math.min(rank,YEAR_COLORS.length-1)]);
    return {label:String(y.y),data:y.data,borderColor:c,backgroundColor:alpha(c,.08),borderWidth:rank===0?2.8:1.8,pointRadius:S.yg==="week"?0:rank===0?3.2:2,pointHoverRadius:5,tension:.3,spanGaps:false,fill:false};
  });
  mkChart("yoy",$("#cvYoy"),{type:"line",data:{labels:ov.labels,datasets:ds},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
    plugins:{legend:{display:true,position:"bottom",labels:{color:t.muted,usePointStyle:true,boxWidth:8,padding:14}},
      tooltip:Object.assign(tip(),{callbacks:{label:function(i){return i.dataset.label+": "+fmt(key,i.parsed.y);}}})},
    scales:{x:axes({xTicks:S.yg==="week"?14:12}).x,y:Object.assign(axes({yFmt:function(v){return fmt(key,v,true);}}).y,{beginAtZero:m.type==="sum"})}}});
}

/* The seasonal index of each calendar month. */
function paintIdx(){
  var d=S.data, sec=$("#idxSec"), idx=d.index;
  if(!idx){sec.hidden=true;return;}
  sec.hidden=false;
  var ms=D.available(d.monthly).filter(function(m){return m.type==="sum"||m.key!=="ctr";}), key=ms.some(function(m){return m.key===S.ik;})?S.ik:"conv";
  S.ik=key;
  var el=$("#idxCard"), rows=idx[key], t=theme(), m=D.BY[key], th=d.thresholds;
  var opts=ms.map(function(x){return '<option value="'+x.key+'"'+(x.key===key?" selected":"")+'>'+esc(x.label)+'</option>';}).join("");
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+esc(m.label)+' by month of the year</span><span class="ps-sub">'+
    (m.good==="down"?"A high bar is a dear month: here higher means more costly.":"A high bar is a strong month.")+'</span></div>'+
    '<div class="ps-card-actions"><select class="ps-sel" id="idxKey" aria-label="Metric">'+opts+'</select></div></div>'+
    '<div class="ps-wrap"><canvas id="cvIdx" aria-label="Seasonal index of '+esc(m.label)+' for each month"></canvas></div>'+
    '<p class="sa-small" id="idxRead"></p>';
  $("#idxKey").onchange=function(){S.ik=this.value;paintIdx();writeHash();};
  var vals=rows.map(function(r){return r.index;}), colors=rows.map(function(r){return alpha(col(D.toneColor(D.tone(r.index,th),key)),r.index==null?0:.8);});
  mkChart("idx",$("#cvIdx"),{type:"bar",data:{labels:D.MON,datasets:[{label:"Index",data:vals,backgroundColor:colors,borderRadius:6,borderWidth:0}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},
      tooltip:Object.assign(tip(),{callbacks:{title:function(i){return D.MONTHS[i[0].dataIndex];},label:function(i){
        var r=rows[i.dataIndex]; return r.index==null?"No history for this month":"Index "+Math.round(r.index)+" ("+r.n+(r.n===1?" year":" years")+")";},
        afterLabel:function(i){var r=rows[i.dataIndex]; return r.index==null?"":D.confidence(r);}}}),
      annotation:undefined},
    scales:{x:axes().x,y:Object.assign(axes({yFmt:function(v){return v;}}).y,{suggestedMin:0,suggestedMax:Math.max(130,Math.max.apply(null,vals.filter(function(v){return v!=null;}))+10)})}},
    plugins:[{id:"hundred",afterDraw:function(ch){
      var y=ch.scales.y.getPixelForValue(100), a=ch.chartArea, c=ch.ctx; c.save(); c.strokeStyle=t.faint; c.setLineDash([5,5]); c.lineWidth=1.2;
      c.beginPath(); c.moveTo(a.left,y); c.lineTo(a.right,y); c.stroke(); c.restore();}}]});
  var good=rows.filter(function(r){return r.index!=null;});
  $("#idxRead").textContent=good.length?(good.some(function(r){return r.n<=1;})?"Some months have one year behind them. ":"")+"The dashed line is 100, an average month. Hover a bar to see how many years back it.":"";
}

/* Does spend follow demand: the spend index against the conversions index. */
function paintGap(){
  var d=S.data, el=$("#gapCard"), idx=d.index;
  if(!idx){el.innerHTML="";destroyChart("gap");return;}
  var t=theme(), sp=idx.cost.map(function(r){return r.index;}), cv=idx.conv.map(function(r){return r.index;});
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">Does spend follow demand?</span><span class="ps-sub">Where the two lines part, budget did not follow what the account was earning.</span></div></div>'+
    '<div class="ps-wrap"><canvas id="cvGap" aria-label="Spend index against conversions index by month"></canvas></div>';
  mkChart("gap",$("#cvGap"),{type:"line",data:{labels:D.MON,datasets:[
    {label:"Conversions",data:cv,borderColor:col("#34d399"),backgroundColor:alpha("#34d399",.12),borderWidth:2.6,pointRadius:3,tension:.3,fill:"+1"},
    {label:"Spend",data:sp,borderColor:col("#fbbf24"),backgroundColor:"transparent",borderWidth:2.2,pointRadius:3,tension:.3}]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      plugins:{legend:{display:true,position:"bottom",labels:{color:t.muted,usePointStyle:true,boxWidth:8,padding:14}},
        tooltip:Object.assign(tip(),{callbacks:{label:function(i){return i.dataset.label+" index: "+(i.parsed.y==null?"–":Math.round(i.parsed.y));}}})},
      scales:{x:axes().x,y:Object.assign(axes().y,{suggestedMin:50})}}});
}

/* The days of the week. */
function paintDow(){
  var d=S.data, el=$("#dowCard"), dw=d.dow;
  if(!dw){
    $("#dowHint").textContent="";
    el.innerHTML='<p class="ps-msg-inline">The days of the week need at least 8 whole weeks with spend. This account does not have them yet.</p>';
    destroyChart("dow"); return;
  }
  var ms=D.available(d.monthly).filter(function(m){return m.key!=="impr";}), key=ms.some(function(m){return m.key===S.dk;})?S.dk:"conv";
  S.dk=key;
  var m=D.BY[key], vals=dw.idx[key], t=theme(), th=d.thresholds;
  $("#dowHint").textContent="The latest "+dw.weeks+" whole weeks, each day against its own week's average (100).";
  var opts=ms.map(function(x){return '<option value="'+x.key+'"'+(x.key===key?" selected":"")+'>'+esc(x.label)+'</option>';}).join("");
  el.innerHTML='<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+esc(m.label)+' by day of the week</span><span class="ps-sub">'+
    (m.good==="down"?"Higher means a dearer day.":"Higher means a stronger day.")+'</span></div><div class="ps-card-actions"><select class="ps-sel" id="dowKey" aria-label="Metric">'+opts+'</select></div></div>'+
    '<div class="ps-wrap sa-short"><canvas id="cvDow" aria-label="'+esc(m.label)+' index by day of the week"></canvas></div>';
  $("#dowKey").onchange=function(){S.dk=this.value;paintDow();writeHash();};
  var colors=vals.map(function(v){return alpha(col(D.toneColor(D.tone(v,th),key)),v==null?0:.8);});
  mkChart("dow",$("#cvDow"),{type:"bar",data:{labels:D.DAYS,datasets:[{label:"Index",data:vals,backgroundColor:colors,borderRadius:6,borderWidth:0}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:Object.assign(tip(),{callbacks:{label:function(i){return "Index "+(i.parsed.y==null?"–":Math.round(i.parsed.y));}}})},
      scales:{x:axes().x,y:Object.assign(axes().y,{suggestedMin:0})}},
    plugins:[{id:"hundred",afterDraw:function(ch){
      var y=ch.scales.y.getPixelForValue(100), a=ch.chartArea, c=ch.ctx; c.save(); c.strokeStyle=t.faint; c.setLineDash([5,5]); c.lineWidth=1.2;
      c.beginPath(); c.moveTo(a.left,y); c.lineTo(a.right,y); c.stroke(); c.restore();}}]});
}

/* Holidays. */
function liftCell(v,lowGood){
  if(v==null) return '<td class="num">–</td>';
  var good=lowGood?v<0:v>0, tone=Math.abs(v)<.03?"flat":good?"good":"bad";
  return '<td class="num"><span class="sa-pill '+tone+'">'+D.pct(v)+'</span></td>';
}
function paintEvents(){
  var d=S.data, sec=$("#evSec"), el=$("#events"), ev=d.events||[];
  if(!ev.length){sec.hidden=true;return;}
  sec.hidden=false;
  el.innerHTML='<div class="ps-tscroll"><table class="ps-table sa-table"><thead><tr><th>Holiday</th><th>When it fell</th><th class="num">Conversions</th><th class="num">Spend</th><th class="num">Cost per conversion</th><th class="num">Years measured</th></tr></thead><tbody>'+
    ev.map(function(e){
      var when=e.years.map(function(y){return D.windowText(y.start,y.end)+" "+y.y;}).slice(-2).join(", ");
      return '<tr><td><b>'+esc(e.name)+'</b></td><td class="sa-when">'+esc(when)+'</td>'+liftCell(e.convLift,false)+liftCell(e.spendLift,false)+liftCell(e.cpaChange,true)+'<td class="num">'+e.n+'</td></tr>';
    }).join("")+'</tbody></table></div>';
}

/* Every month. */
function paintTable(){
  var d=S.data, el=$("#tbl"), rows=d.monthly.slice().reverse();
  $("#tblHint").textContent=rows.length+" months. A partial month is marked and is not used for the index.";
  el.innerHTML='<div class="ps-tscroll"><table class="ps-table sa-table"><thead><tr><th>Month</th><th class="num">Spend</th><th class="num">Conversions</th><th class="num">Cost per conversion</th>'+
    (d.monthly.some(function(r){return r.value>0;})?'<th class="num">Value</th><th class="num">ROAS</th>':'')+'</tr></thead><tbody>'+
    rows.map(function(r){
      var hasV=d.monthly.some(function(x){return x.value>0;});
      return '<tr class="'+(r.usable?"":"sa-dim")+'"><td><b>'+D.MONTHS[r.m-1]+' '+r.y+'</b>'+(r.full?"":' <span class="sa-tag">partial</span>')+(r.full&&!r.usable?' <span class="sa-tag">mostly paused</span>':'')+'</td>'+
        '<td class="num">'+fmt("cost",r.cost)+'</td><td class="num">'+fmt("conv",r.conv)+'</td><td class="num">'+fmt("cpa",D.valueOf("cpa",r))+'</td>'+
        (hasV?'<td class="num">'+fmt("value",r.value)+'</td><td class="num">'+fmt("roas",D.valueOf("roas",r))+'</td>':'')+'</tr>';
    }).join("")+'</tbody></table></div>';
}

/* ── Boot ────────────────────────────────────────────────────── */
new MutationObserver(function(){if(S.data&&!S.data.empty) paintAll();}).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
function lastAccount(){try{return localStorage.getItem("ps_account")||"";}catch(e){return "";}}
function boot(){
  var h=readHash(); applyHash(h); paintMonths();
  var firstLoad=true;
  accts=PL.accounts($("#acctPick"),{url:BASE+"/accounts",allow:["google","meta","both"],platform:h.pf,account:h.a||lastAccount(),
    onLoaded:function(){S.accounts=accts.accounts();},
    onChange:function(a,platform){
      S.accounts=accts.accounts();
      if(!a){
        S.acct=null; S.data=null; $("#dash").hidden=true; $("#liveText").textContent="No account chosen";
        notice("warn","No account chosen",(platform==="meta"?"There are no Meta ad accounts this token can read yet.":"There are no accounts to show for this platform.")+" Pick another platform or account above."); return;
      }
      if(S.acct&&a.id===S.acct.id&&!firstLoad) return;
      firstLoad=false;
      S.acct=a; S.data=null;
      try{localStorage.setItem("ps_account",a.id);}catch(e){}
      load();
    },
    onError:showError});
}
boot();
})();
