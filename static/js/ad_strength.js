/* Ad Strength Analysis (Paid Marketing Agents).
 *
 * Pick a Google Ads account (or an MCC). The server reads Google's ad strength rating for every live Responsive Search Ad and Performance
 * Max asset group (tracker/paid_adstrength.py), sorts out what is worth fixing (paid_adstrength_insights.py) and, when asked for one ad,
 * has Claude suggest headline and description changes that the server has already held to Google's limits (paid_adstrength_ai.py).
 * This page draws it. It never works out a rating, a gap or a suggestion for itself.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/adstrength";
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

var RATINGS = [["below","Below Good"],["all","All"],["POOR","Poor"],["AVERAGE","Average"],["GOOD","Good"],["EXCELLENT","Excellent"],["unrated","Not rated"]];
var CLS = {POOR:"poor",AVERAGE:"avg",GOOD:"good",EXCELLENT:"exc"};
var S = {accounts:[], groups:[], account:"", meta:"", scopeAcct:"", platform:"google", currency:"USD", report:null, seq:0, mseq:0, metaReport:null, kind:"search", rating:"below", filter:"", limit:25, open:{}, sugg:{}};

/* The Platform switch and the account pickers are the shared control (static/js/pm_scope.js). Google reads Google's ad strength, Meta reads Meta's ad quality (its own rankings and
   frequency), Both shows the two one under the other: S.account is the Google part (an account or an MCC), S.meta the Meta part. */
var scope=null;
function groupOf(id){return scope?scope.groupOf(id):null;}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function applyScope(a,p){
  var ps=window.PMScope.partsOf(a).filter(Boolean);
  S.scopeAcct=a||""; S.platform=p;
  S.account=ps.filter(function(x){return !window.PMScope.isMeta(x);})[0]||"";
  S.meta=ps.filter(function(x){return window.PMScope.isMeta(x);})[0]||"";
  try{localStorage.setItem("as_account",S.scopeAcct);}catch(e){}
}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}
function setMetaMsg(html,kind){$("#metaMsg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function hideMeta(){$("#metaOut").hidden=true; $("#metaOut").innerHTML=""; setMetaMsg("");}
function runMeta(refresh){
  var seq=++S.mseq;
  setMetaMsg(""); $("#metaOut").hidden=true;
  if(S.platform==="meta") show("skel");
  getJson(API+"/report?account="+encodeURIComponent(S.meta)+(refresh===true?"&refresh=1":"")).then(function(r){
    if(seq!==S.mseq) return;
    if(S.platform==="meta") show("none");
    if(!r.ok){
      if(r.j&&r.j.kind==="cooldown"){setMetaMsg(esc(r.j.error),"pm-note");return;}
      setMetaMsg('<b>Meta Ads could not be read.</b> '+esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+").")));
      return;
    }
    S.metaReport=r.j;
    $("#metaOut").innerHTML=(S.platform==="both"?'<h2 class="mc-title">Meta Ads</h2>':"")+window.MetaCreative.quality(r.j);
    $("#metaOut").hidden=false;
  }).catch(function(){
    if(seq!==S.mseq) return;
    if(S.platform==="meta") show("none");
    setMetaMsg("The server could not be reached. Try again.");
  });
}

function run(refresh){
  var wantG=S.platform!=="meta"&&S.account, wantM=S.platform!=="google"&&S.meta;
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.scopeAcct));}catch(e){}
  if(!wantG&&!wantM){show("empty");setMsg("");hideMeta();return;}
  if(wantM) runMeta(refresh); else hideMeta();
  if(!wantG){setMsg(""); S.seq++; return;}
  var seq=++S.seq, g=groupOf(S.account);
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 20 seconds.":"",g?"pm-note":"");
  if(S.platform!=="both"||!S.report) show("skel");
  getJson(API+"/report?account="+encodeURIComponent(S.account)+(refresh===true?"&refresh=1":"")).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg("");
    if(!r.ok){
      show(S.report?"out":(wantM&&S.platform==="both"?"none":"empty"));
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg((S.platform==="both"?"<b>Google Ads could not be read.</b> ":"")+esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j; S.currency=r.j.account.currency||"USD"; S.open={}; S.sugg={}; S.limit=25; S.filter=""; $("#filter").value="";
    if(r.j.search.total===0&&r.j.pmax.total>0) S.kind="pmax"; else if(r.j.pmax.total===0) S.kind="search";
    show("out"); render();
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
function view(){return S.report&&S.report[S.kind];}
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts combined</span>':"")+
    '&nbsp;· read from Google Ads '+esc(ago(rep.generatedAt))+' · impressions are the last '+rep.window.days+' days'+
    (rep.stale?' <span class="sg-mcc">Google Ads is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintKinds(); paintRatings(); paintList();
  $("#foot").innerHTML="Source: Google Ads, saved so the same report opens instantly. <b>Ad strength is Google's own rating</b> and Google does not publish how it is scored, "+
    "so a suggested change is aimed at Google's tips and its ratings of each headline and description, and is not a promise of Good or Excellent. "+
    "The tips and the Low, Good and Best ratings of single headlines are Google's; Google's API carries neither for Performance Max, so those asset groups "+
    "are held against what Google accepts instead. Suggested text is checked in code against Google's character limits and against facts the ad already states "+
    "(a number or a claim like \"free\" is never added unless the ad already says it). Nothing is changed in Google Ads: copy the text into the ad.";
}
function paintKpis(){
  var s=S.report.summary, share=s.reviewed?Math.round((s.good+s.excellent)/s.reviewed*100):null;
  var items=[["Ads reviewed",int(s.reviewed),s.reviewed?"search ads and Performance Max":"none live"],
    ["Below Good",int(s.belowGood),s.belowGood?"warn":"good"],
    ["Impressions on those",int(s.impressionsBelowGood),s.impressions?Math.round(s.impressionsBelowGood/s.impressions*100)+"% of all impressions":"last 30 days"],
    ["Good or Excellent",share==null?"Na":share+"%",int(s.good+s.excellent)+" of "+int(s.reviewed)],
    ["Not rated yet",int(s.unrated),"too new or too little traffic"]];
  $("#kpis").innerHTML=items.map(function(c){
    var cls=c[2]==="good"||c[2]==="warn"?c[2]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div>'+(cls?'':'<div class="s">'+esc(c[2])+'</div>')+'</div>';
  }).join("");
}
function paintKinds(){
  var r=S.report, defs=[["search","Search ads (RSA)",r.search],["pmax","Performance Max",r.pmax]];
  $("#kinds").innerHTML=defs.map(function(d){
    return '<button type="button" role="radio" class="pm-chip'+(d[0]===S.kind?" on":"")+'" data-k="'+d[0]+'" aria-checked="'+(d[0]===S.kind)+'">'+d[1]+
      ' <span class="n">'+d[2].belowGood+' of '+d[2].total+'</span></button>';
  }).join("");
}
function count(v,key){
  if(key==="all") return v.total;
  if(key==="below") return v.belowGood;
  if(key==="unrated") return v.items.filter(function(i){return i.rank==null;}).length;
  return v.counts[key]||0;
}
function paintRatings(){
  var v=view();
  $("#ratings").innerHTML=RATINGS.filter(function(r){return r[0]==="below"||r[0]==="all"||count(v,r[0])>0;}).map(function(r){
    return '<button type="button" class="pm-chip'+(r[0]===S.rating?" on":"")+'" data-r="'+r[0]+'" aria-pressed="'+(r[0]===S.rating)+'">'+r[1]+' <span class="n">'+count(v,r[0])+'</span></button>';
  }).join("");
  var tot=v.total||1;
  $("#bar").innerHTML=v.total?["POOR","AVERAGE","GOOD","EXCELLENT"].map(function(k){
    var n=v.counts[k]||0; return n?'<i class="'+CLS[k]+'" style="flex:'+n+'" title="'+n+' '+k.toLowerCase()+'"></i>':"";
  }).join("")+(function(){var u=count(v,"unrated");return u?'<i class="unr" style="flex:'+u+'" title="'+u+' not rated yet"></i>':"";})():"";
}
$("#kinds").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.kind=b.dataset.k; S.limit=25; paintKinds(); paintRatings(); paintList();
});
$("#ratings").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.rating=b.dataset.r; S.limit=25; paintRatings(); paintList();
});
$("#filter").addEventListener("input",function(){S.filter=this.value; S.limit=25; paintList();});

function pill(it){
  var c=CLS[it.strength]||"unr";
  return '<span class="as-pill '+c+'">'+esc(it.strengthLabel)+'</span>';
}
function matches(it){
  var r=S.rating;
  if(r==="below"&&!it.belowGood) return false;
  if(r==="unrated"&&it.rank!=null) return false;
  if(r!=="below"&&r!=="all"&&r!=="unrated"&&it.strength!==r) return false;
  var t=S.filter.trim().toLowerCase(); if(!t) return true;
  var hay=[it.name,it.campaign,it.account||""].concat((it.headlines||[]).map(function(h){return h.text;}),(it.descriptions||[]).map(function(h){return h.text;})).join(" ").toLowerCase();
  return hay.indexOf(t)>=0;
}
function paintList(){
  var v=view(), rows=v.items.filter(matches), shown=rows.slice(0,S.limit);
  $("#listNote").textContent=S.kind==="search"?"Responsive Search Ads, worst and busiest first":"Performance Max asset groups, worst and busiest first";
  $("#list").innerHTML=shown.length?shown.map(row).join(""):'<p class="pm-empty">'+(S.rating==="below"&&v.total?"Nothing here is below Good.":"No ad matches.")+'</p>';
  var more=$("#more"); more.hidden=rows.length<=S.limit; more.textContent="Show all "+rows.length+" ads";
}
$("#more").addEventListener("click",function(){S.limit=100000; paintList();});

function row(it){
  var open=!!S.open[it.key], first=(it.gaps||[]).filter(function(g){return g.severity==="action";})[0]||(it.gaps||[])[0];
  return '<article class="as-row'+(open?" open":"")+'" data-key="'+esc(it.key)+'">'+
    '<button type="button" class="as-head" aria-expanded="'+open+'">'+pill(it)+
      '<span class="as-name"><b>'+esc(it.name||"(unnamed)")+'</b><span class="as-sub">'+esc(it.campaign)+(it.account&&S.report.account.isGroup?" · "+esc(it.account):"")+'</span></span>'+
      '<span class="as-num"><b>'+int(it.impressions)+'</b> impressions<span>'+int(it.clicks)+' clicks</span></span>'+
      '<span class="as-gap">'+esc(first?first.text:it.nextStep)+'</span><span class="as-chev" aria-hidden="true"></span></button>'+
    (open?body(it):"")+'</article>';
}
function textRows(list,max,fieldLabel){
  if(!list.length) return '<p class="pm-empty">None.</p>';
  return '<table class="as-tx"><tbody>'+list.map(function(h){
    var text=typeof h==="string"?h:h.text, label=typeof h==="string"?null:h.label, pin=typeof h==="string"?null:h.pin;
    var l=label&&label!=="PENDING"?'<span class="as-lab '+label.toLowerCase()+'">'+esc(label.charAt(0)+label.slice(1).toLowerCase())+'</span>':"";
    return '<tr><td>'+esc(text)+(pin?' <span class="as-pin" title="Pinned to '+esc(pin.replace("_"," ").toLowerCase())+'">pinned</span>':"")+'</td><td class="r">'+l+'</td><td class="r c'+(text.length>max?" over":"")+'">'+text.length+'/'+max+'</td></tr>';
  }).join("")+'</tbody></table>';
}
function body(it){
  var rsa=it.kind==="rsa", out='<div class="as-body">';
  out+='<p class="as-next"><b>'+esc(it.nextStep)+'</b></p><div class="as-cols"><div>';
  if(rsa){
    out+='<h4>Google\'s tips for this ad</h4>'+(it.tips.length?'<ul class="as-tips">'+it.tips.map(function(t){
      return '<li>'+esc(t.text)+' <span class="as-k '+(t.textFix?"txt":"ast")+'">'+(t.textFix?"text change":"extra asset")+'</span></li>';}).join("")+'</ul>':'<p class="pm-empty">Google shows no tips for this ad.</p>');
  }
  out+='<h4>What the rules see</h4>'+((it.gaps||[]).length?'<ul class="as-gaps">'+it.gaps.map(function(g){
    return '<li class="'+g.severity+'">'+esc(g.text)+'</li>';}).join("")+'</ul>':'<p class="pm-empty">Nothing stands out.</p>');
  if(it.keywords&&it.keywords.length) out+='<h4>The ad group\'s top keywords</h4><p class="as-kw">'+it.keywords.map(function(k){
    var hs=it.keywordCoverage.headlines.covered.indexOf(k)>=0; return '<span class="as-kwc'+(hs?" in":"")+'" title="'+(hs?"A headline carries this":"No headline carries this")+'">'+esc(k)+'</span>';}).join("")+'</p>';
  out+='</div><div>';
  if(rsa){
    out+='<h4>Headlines ('+it.headlines.length+' of 15)</h4>'+textRows(it.headlines,30)+'<h4>Descriptions ('+it.descriptions.length+' of 4)</h4>'+textRows(it.descriptions,90);
    if(it.google) out+='<h4>Text Google itself recommends for this ad group</h4>'+textRows(it.google.headlines,30)+textRows(it.google.descriptions,90);
  }else{
    out+='<h4>Assets</h4><table class="as-tx"><tbody>'+it.assets.map(function(a){
      return '<tr><td>'+esc(a.label.charAt(0).toUpperCase()+a.label.slice(1))+'</td><td class="r c'+(a.count<a.min?" over":"")+'">'+a.count+' of up to '+a.max+'</td></tr>';}).join("")+'</tbody></table>'+
      '<h4>Headlines</h4>'+textRows(it.texts.HEADLINE,30)+'<h4>Long headlines</h4>'+textRows(it.texts.LONG_HEADLINE,90)+'<h4>Descriptions</h4>'+textRows(it.texts.DESCRIPTION,90);
  }
  out+='</div></div><div class="as-sugg" data-sugg="'+esc(it.key)+'">'+suggHtml(it)+'</div></div>';
  return out;
}

/* ── Suggestions ──────────────────────────────────────────── */
var FIELD={headline:"Headline",long_headline:"Long headline",description:"Description"}, MAXC={headline:30,long_headline:90,description:90};
function suggHtml(it){
  var s=S.sugg[it.key];
  if(!s) return '<button type="button" class="pm-btn as-go" data-go="'+esc(it.key)+'">Suggest headline and description changes</button><span class="as-hint">Claude reads this ad, Google\'s tips and the keywords. Every suggestion is checked against Google\'s limits.</span>';
  if(s==="loading") return '<div class="sg-sum-load"><span></span><span></span><span></span> Writing suggestions…</div>';
  if(s.error) return '<p class="pm-err">'+esc(s.error)+'</p><button type="button" class="pm-btn-ghost as-go" data-go="'+esc(it.key)+'">Try again</button>';
  var src=s.source==="model"?"Written by Claude, then checked in code against Google's limits.":
          s.source==="google"?"Claude was not available, so this is Google's own recommended text and the ad group's keywords.":
          "Claude was not available, so this is built from the ad group's keywords.";
  var ch=s.changes||[];
  var h='<div class="as-sg"><div class="as-sg-h"><h4>Suggested changes</h4>'+(ch.length?'<button type="button" class="pm-btn-ghost" data-copyall="'+esc(it.key)+'">Copy all new text</button>':"")+'</div>'+
    (s.summary?'<p class="as-sg-sum">'+esc(s.summary)+'</p>':"");
  if(ch.length){
    h+='<table class="as-ch"><tbody>'+ch.map(function(c,i){
      return '<tr><td class="act"><span class="as-act '+c.action+'">'+(c.action==="replace"?"Replace":"Add")+'</span><span class="as-fld">'+FIELD[c.field]+'</span></td>'+
        '<td>'+(c.replaces?'<s>'+esc(c.replaces)+'</s><br>':"")+'<b>'+esc(c.text)+'</b><span class="as-why">'+esc(c.why)+'</span></td>'+
        '<td class="r c">'+c.text.length+'/'+MAXC[c.field]+'</td><td class="r"><button type="button" class="pm-btn-ghost as-cp" data-copy="'+esc(it.key)+':'+i+'">Copy</button></td></tr>';}).join("")+'</tbody></table>';
  }else h+='<p class="pm-empty">Nothing valid could be suggested.</p>';
  return h+'<p class="as-sg-note">'+esc(src)+' Pinned positions are not shown here, so check the pins when you edit the ad.</p></div><button type="button" class="pm-btn-ghost as-go" data-go="'+esc(it.key)+'">Suggest again</button>';
}
function itemOf(key){
  var r=S.report; return r.search.items.concat(r.pmax.items).filter(function(i){return i.key===key;})[0];
}
function suggest(key){
  var it=itemOf(key); if(!it) return;
  S.sugg[key]="loading"; repaintSugg(key);
  var seq=S.seq;
  getJson(API+"/suggest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({account:S.account,key:key})}).then(function(r){
    if(seq!==S.seq) return;
    S.sugg[key]=r.ok?r.j:{error:(r.j&&r.j.error)||"The suggestions could not be written."}; repaintSugg(key);
  }).catch(function(){ if(seq!==S.seq) return; S.sugg[key]={error:"The server could not be reached. Try again."}; repaintSugg(key); });
}
function repaintSugg(key){
  var box=document.querySelector('[data-sugg="'+key.replace(/"/g,'\\"')+'"]'), it=itemOf(key); if(box&&it) box.innerHTML=suggHtml(it);
}
function copy(text,btn){
  function done(){ if(btn){var t=btn.textContent; btn.textContent="Copied"; setTimeout(function(){btn.textContent=t;},1200);} }
  if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(text).then(done,function(){fallback();}); } else fallback();
  function fallback(){ var a=document.createElement("textarea"); a.value=text; document.body.appendChild(a); a.select(); try{document.execCommand("copy");}catch(e){} a.remove(); done(); }
}
$("#list").addEventListener("click",function(e){
  var go=e.target.closest("[data-go]"); if(go){ suggest(go.dataset.go); return; }
  var cp=e.target.closest("[data-copy]"); if(cp){
    var parts=cp.dataset.copy.split(":"), s=S.sugg[parts.slice(0,-1).join(":")]; if(s&&s.changes) copy(s.changes[+parts[parts.length-1]].text,cp); return;
  }
  var all=e.target.closest("[data-copyall]"); if(all){
    var s2=S.sugg[all.dataset.copyall]; if(s2&&s2.changes) copy(s2.changes.map(function(c){return FIELD[c.field]+": "+c.text;}).join("\n"),all); return;
  }
  var head=e.target.closest(".as-head"); if(head){
    var art=head.closest(".as-row"), key=art.dataset.key; S.open[key]=!S.open[key]; paintList();
  }
});

/* ── CSV and Refresh ──────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var lines=[["Account",rep.account.name],["Window",rep.window.from+" to "+rep.window.to],[],
    ["Kind","Account","Campaign","Ad group or asset group","Rating","Impressions","Clicks","Cost","Conversions","Google's tips","What the rules see","Next step"]];
  rep.search.items.concat(rep.pmax.items).forEach(function(i){
    lines.push([i.kind==="rsa"?"Responsive Search Ad":"Performance Max",i.account||rep.account.name,i.campaign,i.name,i.strengthLabel,i.impressions,i.clicks,i.cost,i.conversions,
      (i.tips||[]).map(function(t){return t.text;}).join("; "),(i.gaps||[]).map(function(g){return g.text;}).join(" | "),i.nextStep]);
  });
  var blob=new Blob(["﻿"+lines.map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="ad-strength-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.window.to+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search), saved="";
  try{saved=localStorage.getItem("as_account")||"";}catch(e){}
  scope=window.PMScope.mount($("#acctPick"),{account:p.get("account")||saved,
    onChange:function(a,pf){applyScope(a,pf); run();},
    onError:function(e){setMsg(esc((e&&e.message)||"The account list could not be loaded."));}});
})();
})();
