/* Call Transcript Analyzer (Paid Marketing Agents).
 *
 * What the calls an ad account drives were about, from CallRail. The server counts every call of the range (overview) and reads a
 * sample of the transcribed ones (analysis); cr_data.js turns what it sends into the figures shown. This file only draws.
 *
 * Nothing from a call (name, number, summary, transcript) ever reaches this page: only counts, and for the calls that look like lost
 * opportunities their location, time, source, campaign, length, topics and CallRail's own lead score and link.
 *
 * The view is the URL (#a=account&loc=&src=&p=preset ...), so a link reproduces exactly what you were looking at.
 */
(function(){
"use strict";

var P = window.PSData, D = window.CRData, BASE = "/p2/paid-marketing/api/calls";
var $ = function(s,r){return (r||document).querySelector(s);};
var $$ = function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s));};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});};
var reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

var S = {accounts:[], acct:null, locations:[], loc:"", src:"", preset:"last30", range:null, ov:null, an:null, anErr:null, seq:0, fetchedAt:null};
var charts = {}, hashTimer = null;

/* ── Helpers ─────────────────────────────────────────────────── */
function alpha(hex,a){var n=parseInt(hex.slice(1),16);return "rgba("+(n>>16&255)+","+(n>>8&255)+","+(n&255)+","+a+")";}
function light(){return document.documentElement.getAttribute("data-theme")==="light";}
function shade(hex,k){var n=parseInt(hex.slice(1),16);function c(v){return Math.round(v*k).toString(16).padStart(2,"0");}return "#"+c(n>>16&255)+c(n>>8&255)+c(n&255);}
function col(hex){return light()?shade(hex,.72):hex;}
function theme(){
  var cs=getComputedStyle($("#ca")), g=function(n,d){return (cs.getPropertyValue(n)||"").trim()||d;};
  return {ink:g("--pm-ink","#e8ecf8"),muted:g("--pm-muted","#8b94be"),faint:g("--pm-faint","#5d6688"),
    line:g("--pm-line","rgba(255,255,255,.07)"),line2:g("--pm-line2","rgba(255,255,255,.12)")};
}
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
function paintUpdated(iso){
  S.fetchedAt=iso||null;
  var el=$("#upd"); if(!el) return;
  el.textContent=iso&&relTime(iso)?" · Updated "+relTime(iso):"";
}
setInterval(function(){if(S.fetchedAt) paintUpdated(S.fetchedAt);},30000);
function fmtRange(r){var M=P.dayLabel; return M(r.start)+", "+r.start.slice(0,4)+" to "+M(r.end)+", "+r.end.slice(0,4);}

Chart.defaults.font.family="'Space Grotesk',system-ui,sans-serif";
Chart.defaults.font.size=11.5;
Chart.defaults.animation.duration=reduceMotion?0:650;
function tip(){return {backgroundColor:"rgba(9,12,24,.97)",titleColor:"#eef1fb",bodyColor:"#c4cbe4",borderColor:"rgba(255,255,255,.14)",borderWidth:1,padding:11,cornerRadius:12,boxPadding:5};}

function getJson(url){
  return fetch(url,{credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){
      if(!r.ok){
        var e=new Error(j.error||("The server answered "+r.status));
        e.status=r.status; e.cooldown=!!j.cooldown; e.retryIn=j.retryIn; e.blockedUntil=j.blockedUntil;
        throw e;
      }
      return j;
    });
  });
}
function q(extra){
  var s="account="+encodeURIComponent(S.acct.id)+"&start="+S.range.start+"&end="+S.range.end;
  if(S.loc) s+="&company="+encodeURIComponent(S.loc);
  if(S.src) s+="&source="+encodeURIComponent(S.src);
  return s+(extra?"&"+extra:"");
}

/* ── Notices ─────────────────────────────────────────────────── */
function notice(kind,title,html){
  $("#notice").innerHTML=title?'<div class="ps-notice '+kind+'"><b>'+esc(title)+'</b><p>'+html+'</p></div>':"";
}
function paintStale(stale){
  var el=$("#staleBar");
  if(!stale){el.hidden=true;el.innerHTML="";return;}
  el.hidden=false;
  el.innerHTML='<b>Showing the copy saved '+esc(new Date(stale).toLocaleString([], {weekday:"short",hour:"numeric",minute:"2-digit"}))+'.</b> CallRail did not answer, so fresh numbers will come back when it does.';
}
function showError(err){
  $("#dash").hidden=true;
  $("#liveText").textContent="CallRail is not reachable"; $("#live").classList.add("off");
  if(err.status===409) notice("setup","CallRail is not connected yet",esc(err.message)+' An admin sets <code>CALLRAIL_API_KEY</code> on Railway, then reloads this page.');
  else if(err.status===429) notice("warn","CallRail is not answering right now",esc(err.message));
  else notice("bad","Could not load CallRail",esc(err.message));
}

/* ── Hash state ──────────────────────────────────────────────── */
function writeHash(){
  clearTimeout(hashTimer);
  hashTimer=setTimeout(function(){
    if(!S.acct) return;
    var h=[["a",S.acct.id],["p",S.preset]];
    if(S.preset==="custom") h.push(["s",S.range.start],["e",S.range.end]);
    if(S.loc) h.push(["loc",S.loc]);
    if(S.src) h.push(["src",S.src]);
    try{history.replaceState(null,"","#"+h.map(function(x){return x[0]+"="+encodeURIComponent(x[1]);}).join("&"));}catch(e){}
  },250);
}
function readHash(){
  var h={}; location.hash.replace(/^#/,"").split("&").forEach(function(p){
    var i=p.indexOf("="); if(i>0) h[p.slice(0,i)]=decodeURIComponent(p.slice(i+1));
  });
  return h;
}
var PRESETS=[{id:"last7",label:"Last 7 days"},{id:"last14",label:"Last 14 days"},{id:"last30",label:"Last 30 days"},{id:"last90",label:"Last 90 days"}];
function applyHash(h){
  if(PRESETS.some(function(x){return x.id===h.p;})||h.p==="custom") S.preset=h.p;
  if(S.preset==="custom"&&/^\d{4}-\d{2}-\d{2}$/.test(h.s||"")&&/^\d{4}-\d{2}-\d{2}$/.test(h.e||"")&&h.s<=h.e) S.range={start:h.s,end:h.e};
  else if(S.preset==="custom") S.preset="last30";
  if(h.loc&&/^COM[0-9a-f]{32}$/.test(h.loc)) S.loc=h.loc;
  if(h.src) S.src=h.src.slice(0,60);
}

/* ── Command bar ─────────────────────────────────────────────── */
function paintRanges(){
  var h="";
  PRESETS.forEach(function(p){h+='<button type="button" class="pm-chip" data-p="'+p.id+'" aria-pressed="'+(S.preset===p.id)+'">'+p.label+'</button>';});
  h+='<button type="button" class="pm-chip" data-p="custom" aria-pressed="'+(S.preset==="custom")+'">Custom</button>';
  $("#ranges").innerHTML=h;
  $("#custom").hidden=S.preset!=="custom"&&!$("#custom")._open;
  $("#cStart").value=S.range.start; $("#cEnd").value=S.range.end; $("#cEnd").max=P.localToday(); $("#cStart").max=P.localToday();
}
function computeRange(){
  if(S.preset!=="custom") S.range=P.presetRange(S.preset);
  var n=P.diffDays(S.range.start,S.range.end)+1;
  $("#rangeNote").innerHTML='<b>'+esc(fmtRange(S.range))+'</b> ('+n+(n===1?" day":" days")+'), compared with the '+n+' days before it.';
}
function onRange(p){
  if(p==="custom"){S.preset="custom";$("#custom")._open=true;paintRanges();return;}
  S.preset=p; $("#custom")._open=false; paintRanges(); computeRange(); load();
}
$("#ranges").addEventListener("click",function(e){var b=e.target.closest("[data-p]"); if(b) onRange(b.dataset.p);});
$("#cApply").addEventListener("click",function(){
  var s=$("#cStart").value, e=$("#cEnd").value;
  if(!s||!e||s>e){toast("Pick a start date on or before the end date");return;}
  if(P.diffDays(s,e)+1>92){toast("CallRail views are limited to 92 days");return;}
  S.preset="custom"; S.range={start:s,end:e}; paintRanges(); computeRange(); load();
});
$("#acctSel").addEventListener("change",function(){
  S.acct=S.accounts.filter(function(a){return a.id===$("#acctSel").value;})[0]; S.loc=""; S.src="";
  try{localStorage.setItem("ca_account",S.acct.id);}catch(e){}
  loadLocations().then(load);
});
$("#locSel").addEventListener("change",function(){S.loc=this.value; load();});
$("#srcSel").addEventListener("change",function(){S.src=this.value; load();});
$("#shareBtn").addEventListener("click",function(){
  writeHash();
  setTimeout(function(){
    (navigator.clipboard?navigator.clipboard.writeText(location.href):Promise.reject()).then(function(){toast("Link to this exact view copied");},
      function(){toast("Copy the address bar to share this view");});
  },300);
});
$("#exportBtn").addEventListener("click",function(){
  if(!S.ov){toast("Nothing to export yet");return;}
  download("calls-"+slugify(S.acct.name)+"-"+S.range.start+"-"+S.range.end+".csv","text/csv;charset=utf-8",D.csv(S.ov,S.an));
  toast("Downloaded the tables");
});
$("#refreshBtn").addEventListener("click",function(){
  var btn=this; if(!S.acct||btn.classList.contains("busy")) return;
  btn.classList.add("busy"); btn.disabled=true;
  Promise.all([getJson(BASE+"/overview?"+q("refresh=1")),getJson(BASE+"/analysis?"+q("refresh=1")).catch(function(){return null;})]).then(function(){
    btn.classList.remove("busy"); btn.disabled=false; toast("Read from CallRail just now"); load();
  }).catch(function(err){
    btn.classList.remove("busy"); btn.disabled=false; toast(err.cooldown?err.message:"Could not refresh: "+err.message);
  });
});

function fillSelect(sel,first,items,value){
  sel.innerHTML='<option value="">'+esc(first)+'</option>'+items.map(function(i){
    return '<option value="'+esc(i.value)+'"'+(i.value===value?" selected":"")+'>'+esc(i.label)+'</option>';}).join("");
}
function loadLocations(){
  return getJson(BASE+"/locations?account="+encodeURIComponent(S.acct.id)).then(function(j){
    S.locations=j.locations||[];
    fillSelect($("#locSel"),"All locations",S.locations.map(function(c){return {value:c.id,label:c.name+(c.active?"":" (disabled)")};}),S.loc);
  }).catch(function(){S.locations=[];});
}

/* ── Loading ─────────────────────────────────────────────────── */
function load(){
  var seq=++S.seq;
  notice(""); paintStale(null);
  S.ov=null; S.an=null; S.anErr=null;
  $("#dash").hidden=true;
  $("#liveText").textContent="Reading "+S.acct.name+" from CallRail…";
  getJson(BASE+"/overview?"+q()).then(function(j){
    if(seq!==S.seq) return;
    S.ov=j.overview;
    paintStale(j.stale); paintUpdated(j.fetchedAt);
    $("#live").classList.remove("off");
    $("#liveText").textContent="Live from CallRail · "+S.acct.name+(S.loc?" · one location":"")+(S.src?" · "+S.src:"");
    fillSelect($("#srcSel"),"All sources",S.ov.sources.map(function(s){return {value:s.key,label:s.key+(s.paid?" (paid)":"")+" · "+D.num(s.calls)};}),S.src);
    $("#dash").hidden=false;
    paintOverview(); paintAnalysis();
    writeHash();
  }).catch(function(err){if(seq===S.seq) showError(err);});
  getJson(BASE+"/analysis?"+q()).then(function(j){
    if(seq!==S.seq) return;
    S.an=j.analysis; paintAnalysis();
  }).catch(function(err){if(seq===S.seq){S.anErr=err; paintAnalysis();}});
}

/* ── The overview ────────────────────────────────────────────── */
function delta(now,was,low){
  var ch=D.change(now,was);
  if(ch==null) return '<span class="d flat"><em>No earlier figure</em></span>';
  var good=low?ch<0:ch>0, tone=Math.abs(ch)<.03?"flat":good?"good":"bad";
  return '<span class="d '+tone+'">'+D.signed(ch)+'<em>vs the '+(S.ov.previousRange?"days before":"period before")+'</em></span>';
}
function paintKpis(){
  var t=S.ov.totals, p=S.ov.previous, cards=[];
  var ansR=t.calls?t.answered/t.calls:null, pAnsR=p.calls?p.answered/p.calls:null;
  cards.push({k:"Calls",c:"#38bdf8",v:D.num(t.calls),d:delta(t.calls,p.calls,false)});
  cards.push({k:"Answered",c:"#34d399",v:D.pct(ansR),d:delta(ansR,pAnsR,false)});
  cards.push({k:"Missed",c:"#fb7185",v:D.num(t.missed),d:'<span class="d '+(t.missRate>0.1?"bad":"flat")+'">'+D.pct(t.missRate)+'<em>of calls</em></span>'});
  cards.push({k:"First-time callers",c:"#a78bfa",v:D.num(t.firstTime),d:'<span class="d flat">'+D.pct(t.calls?t.firstTime/t.calls:null)+'<em>of calls</em></span>'});
  cards.push({k:"CallRail leads",c:"#fbbf24",v:D.num(t.leads),d:'<span class="d flat">'+(t.leads===0&&t.calls>=100?'<em>none marked as a lead in CallRail</em>':D.pct(t.leadRate)+'<em>of calls</em>')+'</span>'});
  cards.push({k:"Average call",c:"#f97316",v:D.dur(t.avgDuration),d:delta(t.avgDuration,p.avgDuration,false)});
  $("#kpis").style.setProperty("--cols",cards.length);
  $("#kpis").innerHTML=cards.map(function(x){
    return '<div class="ps-kpi ps-kpi-static" style="--kc:'+x.c+'"><div class="ps-kpi-h"><span class="k">'+esc(x.k)+'</span></div><span class="v">'+x.v+'</span>'+x.d+'<div class="ps-kpi-f"></div></div>';
  }).join("");
  $("#kpiHint").textContent=(S.src?S.src+": ":"All sources: ")+"counted by CallRail for every call in the range. Leads are CallRail's own count.";
}
function table(rows,head,opts){
  opts=opts||{};
  if(!rows||!rows.length) return '<p class="ps-msg-inline">None in this range.</p>';
  var fl=opts.flags||{};
  return '<div class="ps-tscroll"><table class="ps-table sa-table ca-table"><thead><tr><th>'+esc(head)+'</th><th class="num">Calls</th><th class="num">Missed</th><th class="num">Leads</th><th class="num">Lead rate</th></tr></thead><tbody>'+
    rows.map(function(r){
      var f=fl[r.key];
      return '<tr'+(opts.click?' class="ca-click'+(opts.sel===r.key?" sel":"")+'" data-k="'+esc(r.key)+'" tabindex="0"':"")+'><td><b>'+esc(r.key)+'</b>'+(r.paid?' <span class="ca-paid">paid</span>':"")+
        (f==="miss"?' <span class="ca-flag bad">misses stand out</span>':f==="lowLeads"?' <span class="ca-flag warn">few leads</span>':"")+'</td>'+
        '<td class="num">'+D.num(r.calls)+'</td><td class="num">'+D.num(r.missed)+' <small>'+D.pct(r.missRate)+'</small></td><td class="num">'+D.num(r.leads)+'</td><td class="num">'+D.pct(r.leadRate)+'</td></tr>';
    }).join("")+'</tbody></table></div>';
}
function card(title,sub,body){
  return '<div class="ps-card-h"><div class="ps-card-t"><span class="ps-title">'+esc(title)+'</span>'+(sub?'<span class="ps-sub">'+esc(sub)+'</span>':'')+'</div></div>'+body;
}
function paintOverview(){
  var o=S.ov, t=theme();
  paintKpis();
  // sources
  var top=o.sources.slice(0,10), flagsS=D.flags(o.sources,o.totals);
  $("#srcChart").innerHTML=card("Calls by source","The ten busiest; paid sources in orange",'<div class="ps-wrap ca-short"><canvas id="cvSrc" aria-label="Calls by source"></canvas></div>');
  mkChart("src",$("#cvSrc"),{type:"bar",data:{labels:top.map(function(s){return s.key;}),datasets:[
    {label:"Answered",data:top.map(function(s){return s.answered;}),backgroundColor:top.map(function(s){return alpha(col(s.paid?"#f97316":"#38bdf8"),.8);}),borderRadius:4,stack:"a"},
    {label:"Missed",data:top.map(function(s){return s.missed;}),backgroundColor:alpha(col("#fb7185"),.85),borderRadius:4,stack:"a"}]},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:Object.assign(tip(),{callbacks:{label:function(i){return i.dataset.label+": "+D.num(i.parsed.x);}}})},
      scales:{x:{stacked:true,grid:{color:t.line},border:{display:false},ticks:{color:t.muted,callback:function(v){return D.num(v);}}},y:{stacked:true,grid:{display:false},ticks:{color:t.muted}}},
      onClick:function(e,els){if(els.length){S.src=top[els[0].index].key===S.src?"":top[els[0].index].key; $("#srcSel").value=S.src; load();}}}});
  $("#srcTable").innerHTML=table(o.sources,"Source",{click:true,sel:S.src,flags:flagsS});
  // campaigns, keywords
  $("#ckHint").textContent=S.src?"Calls CallRail finds for "+S.src+". A source whose name is part of another's brings both.":"Campaigns are the UTM campaign names CallRail captured; keywords are the Google Ads search terms.";
  $("#campTable").innerHTML=card("Campaigns","The most calls first",table(o.campaigns,"Campaign"));
  $("#kwTable").innerHTML=card("Keywords","The most calls first",table(o.keywords,"Keyword"));
  // locations
  var sec=$("#locSec");
  if(o.locations){
    sec.hidden=false;
    var fl=D.flags(o.locations,o.totals), nflag=Object.keys(fl).filter(function(k){return fl[k]==="miss";}).length;
    $("#locHint").textContent=nflag?nflag+" "+(nflag===1?"location misses":"locations miss")+" at least one and a half times the account's rate of missed calls.":"No location's missed calls stand out.";
    $("#locTable").innerHTML=table(o.locations,"Location",{flags:fl});
  }else sec.hidden=true;
  $$("#srcTable .ca-click").forEach(function(tr){
    var go=function(){S.src=tr.dataset.k===S.src?"":tr.dataset.k; $("#srcSel").value=S.src; load();};
    tr.onclick=go; tr.onkeydown=function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();go();}};
  });
}

/* ── The analysis ────────────────────────────────────────────── */
function bars(id,labels,vals,colors,fmtv){
  var t=theme();
  mkChart(id,$("#"+id),{type:"bar",data:{labels:labels,datasets:[{data:vals,backgroundColor:colors,borderRadius:5,borderWidth:0}]},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:Object.assign(tip(),{callbacks:{label:function(i){return (fmtv||D.num)(i.parsed.x);}}})},
      scales:{x:{grid:{color:t.line},border:{display:false},ticks:{color:t.muted}},y:{grid:{display:false},ticks:{color:t.muted}}}}});
}
function paintAnalysis(){
  var el=$("#anBody"), a=S.an;
  if(!S.ov) return;
  if(S.anErr){
    $("#anHint").textContent="";
    el.innerHTML='<section class="pm-panel ps-card"><p class="ps-msg-inline">The calls could not be read just now: '+esc(S.anErr.message)+'</p></section>'; return;
  }
  if(!a){
    $("#anHint").textContent="Reading the transcripts of a sample of calls. This takes up to a minute.";
    el.innerHTML='<section class="pm-panel ps-card"><div class="ps-skel" style="height:180px"></div></section>'; return;
  }
  $("#anHint").textContent=D.coverage(a);
  if(!a.read){
    ["intents","topics","reasons","lost"].forEach(destroyChart);
    el.innerHTML='<section class="pm-panel ps-card"><p class="ps-msg-inline">No transcripts to read here. Calls only have one when CallRail recorded and transcribed them, which '+
      'is set per location. Try another location or source, or a longer range.</p></section>'; return;
  }
  var t=theme(), h="";
  h+='<div class="ca-warnthin"'+(D.thin(a)?"":" hidden")+'><b>A small sample.</b> Fewer than 30 calls could be read, so treat the shares below as a rough guide.</div>';
  h+='<div class="ps-two"><section class="pm-panel ps-card" id="anIntents"></section><section class="pm-panel ps-card" id="anOutcome"></section></div>';
  h+='<div class="ps-two"><section class="pm-panel ps-card" id="anTopics"></section><section class="pm-panel ps-card" id="anMore"></section></div>';
  h+='<div class="ps-sec-h"><div><h2>Calls that looked like lost opportunities</h2><span class="ps-hint ps-hint-b" id="lostHint"></span></div></div>';
  h+='<section class="pm-panel ps-card ps-tablecard" id="anLost"></section>';
  h+='<p class="ca-method"><b>How it reads a call.</b> Plain keyword rules, not a model: what the caller said decides what they wanted, the practice\'s own tags (Booked Appointment, Not Booked) and what the agent said decide whether an '+
     'appointment was made. It is wrong sometimes, so it also shows how many calls it could not place ("Other"). A patient moving an appointment or asking about a bill is not a lead and is never counted as a lost one.</p>';
  el.innerHTML=h;
  // intents
  $("#anIntents").innerHTML=card("What they wanted","The "+D.num(a.read)+" calls read",'<div class="ps-wrap ca-short"><canvas id="cvIntents" aria-label="What callers wanted"></canvas></div>');
  bars("cvIntents",a.intents.map(function(i){return i.label;}),a.intents.map(function(i){return i.n;}),a.intents.map(function(i){return alpha(col(D.INTENT_COLOR[i.key]||"#8b94be"),.85);}));
  // outcome
  var rs=a.reasons;
  $("#anOutcome").innerHTML=card("Did they get an appointment?",a.wantsAppointment?"Of the "+D.num(a.wantsAppointment)+" calls from people looking for care (new patients, pain, treatments)":"No call here was from someone looking for care",
    (a.wantsAppointment?'<div class="ca-big"><b>'+D.pct(a.bookRate,0)+'</b><span>ended in a booked appointment ('+D.num(a.booked)+' of '+D.num(a.wantsAppointment)+')</span></div>'+
      (rs.length?'<div class="ps-wrap ca-short2"><canvas id="cvReasons" aria-label="Why the others did not book"></canvas></div><p class="ca-small">Why the others did not: the likeliest reason found in the call.</p>':'<p class="ca-small">Every one of them booked, or the call never said why not.</p>'):''));
  if(a.wantsAppointment&&rs.length) bars("cvReasons",rs.map(function(r){return r.label;}),rs.map(function(r){return r.n;}),rs.map(function(r){return alpha(col(D.REASON_COLOR[r.key]||"#8b94be"),.85);}));
  // topics
  $("#anTopics").innerHTML=card("What came up","Share of the calls read that touched each topic; a call can raise several",'<div class="ps-wrap ca-short"><canvas id="cvTopics" aria-label="Topics"></canvas></div>');
  bars("cvTopics",a.topics.map(function(x){return x.label;}),a.topics.map(function(x){return Math.round(x.share*1000)/10;}),a.topics.map(function(){return alpha(col("#38bdf8"),.8);}),function(v){return v+"%";});
  // score, sentiment, tags
  var mo='<div class="ca-facts">';
  if(a.score&&a.score.avg!=null) mo+='<div><span>CallRail lead score, average</span><b>'+a.score.avg+'</b><small>'+a.score.buckets.map(function(b){return D.num(b.n)+" "+b.label;}).join(" · ")+'</small></div>';
  if(a.sentiment.length) mo+='<div><span>Mood of the caller</span><b>'+esc(a.sentiment[0].label)+'</b><small>'+a.sentiment.map(function(s){return D.num(s.n)+" "+esc(s.label.toLowerCase());}).join(" · ")+'</small></div>';
  mo+='</div>';
  if(a.tags.length) mo+='<p class="ca-small"><b>The practice\'s own tags</b></p><div class="ca-tags">'+a.tags.map(function(x){return '<span class="ca-tag">'+esc(x.name)+' <b>'+D.num(x.n)+'</b></span>';}).join("")+'</div>';
  if(a.bySource.length>1) mo+='<p class="ca-small"><b>Book rate by source</b>, of the calls from people looking for care</p><ul class="ca-bs">'+a.bySource.filter(function(s){return s.wantsAppt>=3;}).slice(0,6).map(function(s){
    return '<li><span>'+esc(s.source)+'</span><b>'+(s.bookRate==null?"–":D.pct(s.bookRate,0))+'</b><small>'+D.num(s.booked)+' of '+D.num(s.wantsAppt)+'</small></li>';}).join("")+'</ul>';
  $("#anMore").innerHTML=card("How the calls felt","From CallRail's own scoring",mo);
  // lost
  $("#lostHint").textContent=a.lostTotal?(a.lostTotal>a.lost.length?"The "+a.lost.length+" with the highest CallRail lead score, of "+D.num(a.lostTotal)+".":D.num(a.lostTotal)+" in the sample, highest lead score first."):"None in this sample.";
  $("#anLost").innerHTML=a.lost.length?'<div class="ps-tscroll"><table class="ps-table sa-table ca-table"><thead><tr><th>When</th><th>Location</th><th>Source</th><th class="num">Length</th><th>Wanted</th><th>Likeliest reason</th><th class="num">Lead score</th><th></th></tr></thead><tbody>'+
    a.lost.map(function(l){
      return '<tr><td>'+esc((l.start||"").replace("T"," ").slice(0,16))+'</td><td>'+esc(l.company)+'</td><td>'+esc(l.source)+(l.campaign?' <small>'+esc(l.campaign)+'</small>':'')+'</td><td class="num">'+D.dur(l.duration)+'</td>'+
        '<td>'+esc(intentLabel(a,l.intent))+'</td><td>'+esc(reasonLabel(a,l.reason))+'</td><td class="num">'+(l.score==null?"–":Math.round(l.score))+'</td>'+
        '<td class="num">'+(l.url?'<a class="ca-open" href="'+esc(l.url)+'" target="_blank" rel="noopener noreferrer">Open in CallRail</a>':'')+'</td></tr>';
    }).join("")+'</tbody></table></div>':'<p class="ps-msg-inline">No lost opportunity found in the calls read.</p>';
}
function intentLabel(a,k){var i=a.intents.filter(function(x){return x.key===k;})[0]; return i?i.label:k;}
function reasonLabel(a,k){var i=a.reasons.filter(function(x){return x.key===k;})[0]; return i?i.label:"No clear reason";}

/* ── Boot ────────────────────────────────────────────────────── */
new MutationObserver(function(){if(S.ov){paintOverview();paintAnalysis();}}).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
function boot(){
  var h=readHash(); applyHash(h);
  S.range=P.presetRange(S.preset); paintRanges(); computeRange();
  getJson(BASE+"/accounts").then(function(j){
    S.accounts=j.accounts||[];
    if(!S.accounts.length){notice("warn","No CallRail accounts","The API key cannot read any account."); return;}
    var want=h.a||(function(){try{return localStorage.getItem("ca_account")||"";}catch(e){return "";}})();
    S.acct=S.accounts.filter(function(a){return a.id===want;})[0]||S.accounts[0];
    $("#acctSel").innerHTML=S.accounts.map(function(a){return '<option value="'+esc(a.id)+'"'+(a.id===S.acct.id?" selected":"")+'>'+esc(a.name)+'</option>';}).join("");
    return loadLocations().then(load);
  }).catch(showError);
}
boot();
})();
