/* Competitor Analysis (Paid Marketing Agents).
 *
 * Pick a Google Ads account and/or a Meta ad account, type a competitor's company name and pick it from the companies with a similar name (as many as you like; they are kept for that ad account),
 * press Refresh to read their ads, and read how they differ from ours and what to try. The server decides
 * (tracker/paid_compete*.py); this page draws it. The wording and the shapes are competitor_analysis_data.js. It reads and changes nothing in an ad account.
 */
(function(){
"use strict";

var D = window.CAData, API = "/p2/paid-marketing/api/compete", MAX_POLLS = 600;      /* a Google read of a big advertiser takes a quarter of an hour, so the page keeps looking for about 40 minutes */
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function store(k,v){try{if(v===undefined)return localStorage.getItem(k)||"";localStorage.setItem(k,v);}catch(e){return "";}}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleDateString("en-US",{month:"short",day:"numeric"})+" ("+rel+")";
}

var S = {account:"", report:null, seq:0, polls:0, results:null, finding:{}, query:""};
var scope = null;

function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url){
  return fetch(url,{credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function postJson(url,body){
  return fetch(url,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}
function q(extra){return "?account="+encodeURIComponent(S.account)+(extra||"");}

/* ── Loading ──────────────────────────────────────────────── */
function run(keep){
  if(!S.account){show("empty");setMsg("");return Promise.resolve();}
  var seq=++S.seq;
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.account));}catch(e){}
  if(!keep) show("skel");
  return getJson(API+"/report"+q()).then(function(r){
    if(seq!==S.seq) return;
    if(!r.ok){
      show(S.report?"out":"empty");
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j; show("out");
    try{render();}catch(e){setMsg("The report was read but could not be drawn: "+esc(String(e&&e.message||e)));}
    if(r.j.job&&r.j.job.status==="running") poll();
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  var names=Object.keys(rep.names||{}).map(function(p){return rep.names[p];}).join(" + ");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(names||"Account")+'</b> · '+rep.platforms.map(function(p){return D.PLAT[p];}).join(" + ")+' · Google ads read for '+esc(rep.region);
  $("#kpis").innerHTML=D.tiles(rep).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
  $("#standing").textContent=D.standing(rep);
  paintCompetitors(); paintIdeas(); paintGaps(); paintAds(); paintBlind();
  $("#foot").innerHTML="Competitor ads are read from Meta's Ad Library (the page's active ads) and Google's Ads Transparency Center by scrapers, only when you press Refresh, and kept for "+rep.cacheDays+" days. "+
    "Our ads are the Ad Copy Checker's read of the text of the ads running now. A message is a gap only when the competitors use it clearly more than we do, in at least three of their ads and, when two were read, in two competitors. Nothing is changed in an ad account.";
}
function paintCompetitors(){
  var rep=S.report;
  $("#fRegion").value=rep.region||"US";
  $("#compNote").textContent=rep.competitors.length?D.plural(rep.competitors.length,"competitor"):"none yet";
  $("#compFor").textContent=D.forText(rep);
  paintList(); paintResults();
  $("#estimate").textContent=D.estimateText(rep.estimate,rep.cacheDays);
  $("#refreshBtn").disabled=!rep.competitors.length||!(rep.senders&&rep.senders.apify)||!!(rep.job&&rep.job.status==="running");
  $("#refreshBtn").title=rep.senders&&rep.senders.apify?"":"APIFY_API_TOKEN is not set on this server";
  paintJob();
  $("#compTables").innerHTML=rep.platforms.map(function(p){
    var rows=D.competitorRows(rep,p);
    if(!rows.length) return "";
    return '<h4>'+esc(D.PLAT[p])+'</h4><div class="bi-tbl"><table><thead><tr><th>Competitor</th><th>Ads</th><th>Active</th><th>New in 30 days</th><th>Median days running</th><th>Longest</th><th>Formats</th><th>Calls to action</th><th>Landing pages</th></tr></thead><tbody>'+rows.map(function(r){
      var bad=r.status==="failed"||r.status==="skipped";
      return '<tr><td><b>'+esc(r.name)+'</b><div class="bi-sub'+(r.status==="failed"?" ca-bad":"")+'">'+esc(r.note)+'</div>'+(r.flag?'<div class="bi-sub ca-warn">'+esc(r.flag)+'</div>':"")+(r.noText?'<div class="bi-sub">'+esc(r.noText)+'</div>':"")+'</td>'+(bad?'<td colspan="8"></td>':
        '<td>'+r.ads+(r.capped?'+':"")+'</td><td>'+r.active+'</td><td>'+r.newest+'</td><td>'+esc(r.median)+'</td><td>'+(r.longest==null?"":esc(r.longest+" days"))+'</td><td>'+esc(r.formats)+'</td><td>'+esc(r.ctas)+'</td><td>'+esc(r.landing)+'</td>')+'</tr>';
    }).join("")+'</tbody></table></div>';
  }).join("");
}
function paintJob(){
  var job=S.report.job;
  $("#jobBox").innerHTML=job?'<p class="mp-note">'+esc(D.jobText(job))+'</p>'+((job.steps||[]).length&&job.status!=="done"?'<ul class="mp-runs">'+job.steps.map(function(s){var l=D.stepLine(s);return '<li class="'+(l.tone==="bad"?"bad":"")+'">'+esc(l.text)+'</li>';}).join("")+'</ul>':""):"";
}
function paintIdeas(){
  var cards=D.ideaCards(S.report);
  $("#ideasCard").hidden=!cards.length;
  $("#ideasNote").textContent=D.plural(cards.length,"idea");
  $("#ideas").innerHTML=cards.map(function(c){
    return '<article class="ca-idea"><div class="ca-idea-h"><span class="bi-lv '+c.tone+'">'+esc(c.level)+'</span><b>'+esc(c.title)+'</b></div><p>'+esc(c.why)+'</p><p><b>Try this:</b> '+esc(c.tryThis)+'</p>'+(c.example?'<p class="bi-sub">Example from them: '+esc(c.example)+'</p>':"")+'</article>';
  }).join("");
}
function bar(label,their,own){
  return '<div class="ca-bar"><span class="ca-bar-l">'+esc(label)+'</span><span class="ca-bar-t"><i class="their" style="width:'+Math.max(0,Math.min(100,their))+'%"></i></span><span class="ca-bar-t"><i class="own" style="width:'+Math.max(0,Math.min(100,own))+'%"></i></span><span class="ca-bar-n">'+Math.round(their)+'% / '+Math.round(own)+'%</span></div>';
}
function paintGaps(){
  var rep=S.report, html=rep.platforms.map(function(p){
    var g=D.gapRows(rep,p);
    if(!(rep.analysis||{})[p]) return "";
    if(!g.judged) return '<h4>'+esc(D.PLAT[p])+'</h4><p class="pm-empty">'+esc(g.why||"Nothing to compare yet.")+'</p>';
    var out='<h4>'+esc(D.PLAT[p])+'</h4><p class="bi-sub">Bars: them, then us.</p>';
    out+=g.gaps.length?g.gaps.map(function(x){return bar(x.label,x.their,x.own)+'<p class="bi-sub">'+esc(D.gapText({their:x.their,own:x.own,ads:x.ads,who:x.who}))+(x.example?' · '+esc(D.exampleText(x.example)):"")+'</p>';}).join(""):'<p class="pm-empty">No message the competitors use clearly more than we do.</p>';
    if(g.edges.length) out+='<h5>What we say more than they do</h5>'+g.edges.map(function(x){return bar(x.label,x.their,x.own);}).join("");
    return out;
  }).join("");
  $("#gapsCard").hidden=!html;
  $("#gaps").innerHTML=html;
}
function adTable(rows){
  return '<div class="bi-tbl"><table><thead><tr><th>Competitor</th><th>Headline</th><th>Text</th><th>Format</th><th>Running</th></tr></thead><tbody>'+rows.map(function(r){
    return '<tr><td>'+esc(r.who)+'</td><td>'+esc(r.headline)+'</td><td>'+esc(r.body)+'</td><td>'+esc(r.format)+(r.cta?' · '+esc(r.cta):"")+'</td><td>'+(r.days==null?"":esc(D.plural(r.days,"day")))+(r.since?'<div class="bi-sub">since '+esc(r.since)+'</div>':"")+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function paintAds(){
  var rep=S.report, html=rep.platforms.map(function(p){
    var offers=D.offerRows(rep,p), win=D.winnerRows(rep,p), news=D.newestRows(rep,p), out="";
    if(offers.length) out+='<h5>Offers they state</h5><div class="bi-tbl"><table><thead><tr><th>Offer</th><th>Lines</th><th>Competitors</th></tr></thead><tbody>'+offers.map(function(o){return '<tr><td>'+esc(o.text)+'</td><td>'+o.lines+'</td><td>'+esc(o.who)+'</td></tr>';}).join("")+'</tbody></table></div>';
    if(win.length) out+='<h5>Running longest</h5>'+adTable(win);
    if(news.length) out+='<h5>Newest</h5>'+adTable(news);
    return out?'<h4>'+esc(D.PLAT[p])+'</h4>'+out:"";
  }).join("");
  $("#adsCard").hidden=!html;
  $("#ads").innerHTML=html;
}
function paintBlind(){
  var rep=S.report;
  $("#blind").innerHTML=(rep.caveats||[]).map(function(c){return '<li><b>This time:</b> '+esc(c)+'</li>';}).join("")+(rep.notMeasured||[]).map(function(c){return '<li>'+esc(c)+'</li>';}).join("");
}

/* ── Finding and keeping competitors ──────────────────────── */
function names(){return S.report&&S.report.names?flip(S.report):{};}
function flip(rep){var o={}; Object.keys(rep.names||{}).forEach(function(p){o[rep.accounts[p]]=rep.names[p];}); return o;}
function reasonOf(r,fallback){return esc((r.j&&r.j.error)||(fallback+" (HTTP "+r.status+")."));}

function paintResults(){
  var res=S.results, box=$("#caResults");
  if(!res){box.innerHTML="";return;}
  var cards=D.resultCards(res);
  box.innerHTML='<p class="bi-sub">'+esc(D.searchNote(res,S.query))+'</p>'+cards.map(function(c,i){
    return '<div class="ca-co'+(c.added?" added":"")+'">'+(c.logo?'<img class="ca-logo" src="'+esc(c.logo)+'" alt="" loading="lazy" referrerpolicy="no-referrer">':'<span class="ca-logo ca-logo-x">'+esc(c.name.charAt(0).toUpperCase())+'</span>')+
      '<div class="ca-co-t"><b>'+esc(c.name)+'</b><div class="bi-sub">'+esc(c.sub||c.description)+'</div><div class="bi-sub">'+(c.website?esc(c.domain):'<em>no website known</em>')+(c.hasFacebook?' \u00b7 Facebook page known':"")+'</div></div>'+
      (c.added?'<span class="ca-added">Added</span>':'<button class="pm-btn-ghost" type="button" data-add="'+i+'">Add as competitor</button>')+'</div>';
  }).join("");
}
function paintList(){
  var rep=S.report, cards=D.competitorCards(rep,S.finding), box=$("#caList");
  box.innerHTML=cards.length?'<h4>This account\'s competitors</h4>'+cards.map(function(c,i){
    return '<div class="ca-co">'+(c.logo?'<img class="ca-logo" src="'+esc(c.logo)+'" alt="" loading="lazy" referrerpolicy="no-referrer">':'<span class="ca-logo ca-logo-x">'+esc(c.name.charAt(0).toUpperCase())+'</span>')+
      '<div class="ca-co-t"><b>'+esc(c.name)+'</b><div class="bi-sub">'+esc([c.industry,c.domain].filter(Boolean).join(" \u00b7 "))+'</div>'+
      '<div class="bi-sub ca-fb '+esc(c.fbTone)+'">'+esc(c.fbText)+'</div>'+
      '<label class="ca-fb-in"><span class="dim">Facebook page</span><input type="text" data-fb="'+i+'" value="'+esc(c.facebook)+'" placeholder="paste the page address if it is not found" spellcheck="false" autocomplete="off"'+(c.finding?" disabled":"")+'></label></div>'+
      '<div class="ca-co-b">'+(c.canFind?'<button class="pm-btn-ghost" type="button" data-find="'+i+'">Find its Facebook page</button>':"")+'<button class="pm-btn-ghost" type="button" data-rm="'+i+'" aria-label="Remove '+esc(c.name)+'">Remove</button></div></div>';
  }).join(""):'<p class="pm-empty">No competitors yet for this account. Type a company name above.</p>';
}
function applyChange(r){
  if(!r.ok){setMsg(reasonOf(r,"The change could not be saved"));return false;}
  S.report.competitors=r.j.competitors; S.report.region=r.j.region; return true;
}
function afterChange(){paintList(); paintResults(); $("#compNote").textContent=S.report.competitors.length?D.plural(S.report.competitors.length,"competitor"):"none yet"; return run(true);}

function findFacebook(name,force){
  if(S.finding[name]) return Promise.resolve();
  S.finding[name]=true; paintList(); var acct=S.account;
  return postJson(API+"/find-facebook",D.findBody(S.account,name,names(),force)).then(function(r){
    delete S.finding[name];
    if(acct!==S.account) return;
    if(!r.ok){paintList(); setMsg(reasonOf(r,"The Facebook page could not be looked for"));return;}
    S.report.competitors=r.j.competitors;
    var found=r.j.kept||r.j.facebook;
    return afterChange().then(function(){        /* the report is drawn again first, which clears the message line */
      setMsg(found?"":esc(name)+": "+esc(r.j.error||r.j.reasoning||"No Facebook page could be checked.")+" Paste its page address in the box if you know it, or Meta ads cannot be read for it.","pm-note");
    });
  }).catch(function(){delete S.finding[name]; paintList(); setMsg("The server could not be reached. The Facebook page was not looked for.");});
}
$("#caForm").addEventListener("submit",function(e){
  e.preventDefault();
  var q=$("#caQ").value.trim();
  if(!S.account||q.length<2){setMsg(S.account?"Type at least two letters of the company name.":"Pick an ad account first: competitors are saved for each ad account.");return;}
  setMsg(""); S.query=q; $("#caSearch").disabled=true; $("#caResults").innerHTML='<p class="bi-sub">Searching\u2026</p>';
  getJson(API+"/search?account="+encodeURIComponent(S.account)+"&q="+encodeURIComponent(q)).then(function(r){
    $("#caSearch").disabled=false;
    if(!r.ok){S.results=null; paintResults(); setMsg(reasonOf(r,"The search could not be made"));return;}
    S.results=r.j; paintResults();
  }).catch(function(){$("#caSearch").disabled=false; S.results=null; paintResults(); setMsg("The server could not be reached. Try again.");});
});
$("#caResults").addEventListener("click",function(e){
  var b=e.target.closest("[data-add]"); if(!b||!S.results) return;
  var card=D.resultCards(S.results)[+b.dataset.add]; if(!card) return;
  b.disabled=true; setMsg("");
  postJson(API+"/competitors",D.addBody(S.account,card.company,names())).then(function(r){
    if(!applyChange(r)){b.disabled=false;return;}
    (S.results.companies||[]).forEach(function(c){ if(c.name===card.name) c.added=true; });
    var need=!card.company.facebook;
    return afterChange().then(function(){ if(need) return findFacebook(card.name,false); });
  }).catch(function(){b.disabled=false; setMsg("The server could not be reached. Nothing was saved.");});
});
$("#caList").addEventListener("click",function(e){
  var rm=e.target.closest("[data-rm]"), fd=e.target.closest("[data-find]");
  var cards=D.competitorCards(S.report,S.finding);
  if(rm){
    var c=cards[+rm.dataset.rm]; if(!c) return;
    rm.disabled=true; setMsg("");
    postJson(API+"/competitors",D.removeBody(S.account,c.name,names())).then(function(r){
      if(!applyChange(r)){rm.disabled=false;return;}
      if(S.results) (S.results.companies||[]).forEach(function(x){ if(x.name===c.name) x.added=false; });
      return afterChange();
    }).catch(function(){rm.disabled=false; setMsg("The server could not be reached. Nothing was changed.");});
  }else if(fd){
    var f=cards[+fd.dataset.find]; if(f) findFacebook(f.name,false);
  }
});
$("#caList").addEventListener("change",function(e){
  var inp=e.target.closest("[data-fb]"); if(!inp) return;
  var c=S.report.competitors[+inp.dataset.fb]; if(!c) return;
  setMsg("");
  postJson(API+"/competitors",D.pageBody(S.account,c,inp.value,names())).then(function(r){
    if(!applyChange(r)){paintList();return;}
    return afterChange();
  }).catch(function(){setMsg("The server could not be reached. Nothing was saved.");});
});
$("#fRegion").addEventListener("change",function(){
  if(!S.report) return; setMsg("");
  postJson(API+"/competitors",D.regionBody(S.account,$("#fRegion").value,names())).then(function(r){
    if(!applyChange(r)){$("#fRegion").value=S.report.region||"US";return;}
    return afterChange();
  }).catch(function(){setMsg("The server could not be reached. Nothing was saved.");});
});

/* ── Refreshing and polling ───────────────────────────────── */
$("#refreshBtn").addEventListener("click",function(){
  if(!S.report) return;
  setMsg("");
  postJson(API+"/refresh",{account:S.account}).then(function(r){
    if(!r.ok){setMsg(esc((r.j&&r.j.error)||("The refresh could not start (HTTP "+r.status+").")));return;}
    S.polls=0; S.report.job={status:"running",steps:[],total:0}; paintJob(); $("#refreshBtn").disabled=true; poll();
  }).catch(function(){setMsg("The server could not be reached. Nothing was read.");});
});
function poll(){
  if(S.polls++>=MAX_POLLS) return;
  setTimeout(function(){
    getJson(API+"/job"+q()).then(function(r){
      if(!r.ok||!r.j.job){return;}
      S.report.job=r.j.job; paintJob();
      if(r.j.job.status==="running") poll(); else run(true);
    }).catch(function(){});
  },4000);
}

/* ── CSV ──────────────────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel */
$("#exportBtn").addEventListener("click",function(){
  if(!S.report) return;
  var blob=new Blob(["﻿"+D.csvRows(S.report).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="competitor-analysis-"+new Date().toISOString().slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function(){
  var want=(new URLSearchParams(location.search).get("account"))||store("ca_account")||"";
  scope=window.PMScope.mount($("#acctPick"),{account:want,noGroups:true,onChange:function(a){S.account=a; S.results=null; S.finding={}; store("ca_account",a); run();},
    onError:function(e){setMsg(esc((e&&e.message)||"The account list could not be read."));}});
})();
})();
