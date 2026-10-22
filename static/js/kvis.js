/* Keyword Visibility Tracker (Paid Marketing Agents).
 *
 * Pick a Google Ads account or an MCC. The server reads the highest-spend Search keywords and the auctions and holds the last whole week against the four before it (tracker/paid_kvis*.py). This page
 * draws it and never decides what lost its place for itself. The wording and the shapes are kvis_data.js.
 */
(function(){
"use strict";

var D = window.KVData, API = "/p2/paid-marketing/api/kvis";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}
function store(k,v){try{if(v===undefined)return localStorage.getItem(k)||"";localStorage.setItem(k,v);}catch(e){return "";}}

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}, settings:{accounts:{}}, shown:{}};

var picker = window.PMPicker($("#acctPick"),{label:"Ad account",search:true,onChange:function(v){
  S.account=v; store("kv_account",v); run();
}});
function groupOf(id){return S.groups.filter(function(g){return g.id===id;})[0]||null;}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function setAccounts(list, groups){
  S.accounts=list; S.groups=groups||[];
  var items=[{value:"",label:"Choose an account",sub:list.length+" accounts"+(S.groups.length?" · "+S.groups.length+" MCCs to check together":""),avatar:"?",hue:200}], seen={};
  S.groups.forEach(function(g){
    var head=g.name+" MCC";
    items.push({value:g.id,label:"All of "+g.name,avatar:inits(g.name),hue:hueOf(g.name),group:head,badge:MCC_BADGE,sub:g.accountIds.length+" accounts checked together"});
    list.filter(function(a){return a.group===g.id;}).forEach(function(a){
      seen[a.id]=1; items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:head,sub:dashed(a.id)});
    });
  });
  list.filter(function(a){return !seen[a.id];}).forEach(function(a){
    items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:S.groups.length?"Other accounts":"",sub:dashed(a.id)});
  });
  if(!list.some(function(a){return a.id===S.account;})&&!groupOf(S.account)) S.account="";
  picker.set(items,S.account);
}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function postJson(url,body){
  return getJson(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}

function run(refresh,keep){
  if(!S.account){show("empty");setMsg("");return;}
  var seq=++S.seq, g=groupOf(S.account);
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.account));}catch(e){}
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 30 seconds.":"",g?"pm-note":"");
  if(!keep) show("skel");
  getJson(API+"/report?"+D.query(S.account,refresh===true)).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg("");
    if(!r.ok){
      show(S.report?"out":"empty");
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j;
    if(!keep){ S.open={}; S.shown={}; S.view=D.firstView(r.j); if(r.j.accounts.length===1) S.open[r.j.accounts[0].id]=true; }
    show("out");
    try{render();}catch(e){setMsg("The report was read but could not be drawn: "+esc(String(e&&e.message||e)));}
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
var SHOWN_KW = 20, MORE = 20, QUIET_ROWS = 20;
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts checked together</span>':"")+
    ' · read '+esc(ago(rep.readAt||rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">The platform is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintInsights(); paintViews(); paintList();
  var m=rep.minimums||{};
  $("#foot").innerHTML="Source: the highest-spend Search keywords of Google Ads over the last 28 days, each one's last "+((m.weeks||4)+1)+" whole weeks (the shares at the top of the page and lost to rank, the Quality Score Google kept for the week) and the auction insights of the live Search campaigns, saved so the same report opens instantly. "+
    "The <b>last whole week</b> is held against the "+(m.weeks||4)+" weeks before it. A keyword is judged only with "+(m.eligible||100)+" searches it was eligible for in the week (its impressions divided by its share); one without that says so instead of being called fine. "+
    "Google no longer reports a true average position, so this uses the share of searches at the top of the page; it does not report a keyword's share lost to budget, so only the share lost to rank is shown. "+
    "A competitor is new when it overlaps your ads at least 10% in the last week and under 2% in each week before; on a brand campaign 5% is enough and it is called a possible brand hijack. Nothing is changed in an ad account.";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.report).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintInsights(){
  var list=D.insights(S.report), host=$("#insights");
  host.innerHTML=list.length?'<section class="pm-panel da-ins"><div class="pm-panel-h"><span class="pm-panel-t">Across the keywords</span></div><ul>'+list.map(function(i){return '<li class="'+i.kind+'">'+esc(i.text)+'</li>';}).join("")+'</ul></section>':"";
}
function paintViews(){
  $("#views").innerHTML=D.VIEWS.map(function(v){
    var n=D.viewCount(S.report,v[0]);
    return '<button type="button" role="radio" class="pm-chip'+(v[0]===S.view?" on":"")+'" data-v="'+v[0]+'" aria-checked="'+(v[0]===S.view)+'">'+esc(v[1])+' <span class="n">'+n+'</span></button>';
  }).join("");
}
$("#views").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.view=b.dataset.v; paintViews(); paintList();
});

function pill(a){var s=D.statusInfo(a.status); return '<span class="bi-pill '+s.tone+'">'+esc(s.label)+'</span>';}
function paintList(){
  var rows=D.shown(S.report,S.view);
  $("#listNote").textContent=S.report.accounts.length+(S.report.accounts.length===1?" account":" accounts")+", the ones to fix first";
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Nothing needs a look: no keyword lost its place and no new competitor turned up.")+'</p>';
}
function figHtml(a){
  var c=a.counts;
  if(a.status==="clean") return '<div class="bs-fig ok"><span class="bi-n">0</span><span class="bs-sub">nothing wrong in '+D.plural(a.keywords,"keyword")+'</span></div>';
  if(a.status==="fix") return '<div class="bs-fig stopped"><span class="bi-n">'+D.int(c.high+c.medium)+'</span><span class="bs-sub">to fix'+(c.high?' · '+c.high+' high':"")+'</span></div>';
  return '<div class="bs-fig under"><span class="bi-n">'+D.int(c.low)+'</span><span class="bs-sub">small finding'+(c.low===1?"":"s")+'</span></div>';
}
function row(a){
  var open=!!S.open[a.id], top=a.byRule[0];
  return '<article class="bi-row '+(a.status==="fix"?"stopped":a.status==="check"?"limited":"clear")+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+'</b><span class="bi-sub">'+D.plural(a.keywords,"keyword")+' tracked'+(a.findingCount?', '+D.plural(a.findingCount,"finding"):"")+'</span></span>'+
      figHtml(a)+
      '<span class="bi-delta bad">'+(top?esc(top.label)+' · '+D.plural(top.findings,"finding"):"")+'</span>'+
      '<span class="bi-why">'+(a.auctionRead?"":"no auction insights")+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function rulesHtml(a){
  if(!a.byRule.length) return '<p class="pm-empty">Nothing to flag: the '+D.plural(a.keywords,"keyword")+' tracked are holding their place'+(a.auctionRead?" and no new competitor turned up":"")+'.</p>';
  return '<h4>What was found</h4><div class="bi-tbl"><table><thead><tr><th>Problem</th><th>Level</th><th class="r">Findings</th></tr></thead><tbody>'+a.byRule.map(function(b){
    var l=D.levelInfo(b.level);
    return '<tr><td><b>'+esc(b.label)+'</b><br><span class="bi-sub">'+esc(b.fix)+'</span></td><td><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></td><td class="r">'+D.int(b.findings)+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function planHtml(a){
  var steps=(a.plan&&a.plan.steps)||[];
  if(!steps.length) return "";
  return '<div class="bi-plan"><h4>Fix plan</h4><ol class="bi-steps">'+steps.map(function(s,i){
    var l=D.levelInfo(s.level);
    return '<li><details'+(i===0?' open':'')+'><summary><span class="bi-sn">'+(i+1)+'</span><span class="bi-st"><b>'+esc(s.title)+'</b></span><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></summary>'+
      '<div class="bi-sb"><p class="bi-why-t">'+esc(s.how)+'</p>'+
      (s.where&&s.where.length?'<p class="bi-on">Start with: '+s.where.map(function(c){return '<span class="bi-chip">'+esc(c)+'</span>';}).join(" ")+'</p>':"")+
      (s.examples&&s.examples.length?'<ul class="gc-ex">'+s.examples.map(function(c){return '<li>'+esc(c)+'</li>';}).join("")+'</ul>':"")+'</div></details></li>';
  }).join("")+'</ol></div>';
}
function findingHtml(a,f){
  var l=D.levelInfo(f.level), dom=D.ignorable(f);
  return '<li><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span> <b>'+esc(f.title)+'</b><p class="gc-detail">'+esc(f.detail)+'</p>'+
    (f.evidence&&f.evidence.length?'<ul class="kp-evid">'+f.evidence.map(function(e){return '<li>'+esc(e)+'</li>';}).join("")+'</ul>':"")+
    (dom?'<p class="gc-act"><button type="button" class="cc-ok kv-ign" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'" data-domain="'+esc(dom)+'">Ignore '+esc(dom)+' (not a competitor)</button></p>':"")+'</li>';
}
function figuresHtml(item){
  return '<div class="bi-tbl kp-metrics"><table><thead><tr><th>Figure</th><th class="r">Last week</th><th class="r">The 4 weeks before</th></tr></thead><tbody>'+D.figureRows(item).map(function(r){
    return '<tr><td>'+esc(r.label)+'</td><td class="r">'+esc(r.now)+'</td><td class="r">'+esc(r.before)+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function kwHtml(a,k){
  var top=k.findings.length?D.levelInfo(k.level):null;
  return '<li class="gc-camp kp-camp"><div class="gc-camp-h">'+(top?'<span class="bi-lv '+top.tone+'">'+esc(top.label)+'</span>':"")+'<b>'+esc(k.keyword)+'</b> <span class="bi-sub">'+esc(D.matchText(k.match))+' · '+esc(k.campaign)+
      ' · #'+k.rank+' by spend · '+esc(D.money(k.cost,a.currency))+' in 28 days</span></div>'+
    (k.judged?figuresHtml(k):'<p class="bi-sub">Not judged: '+esc(k.why)+'.</p>')+
    (k.findings.length?'<ul class="gc-finds">'+k.findings.map(function(f){return findingHtml(a,f);}).join("")+'</ul>':"")+'</li>';
}
function kwsHtml(a){
  var list=D.flagged(a);
  if(!list.length) return "";
  var n=S.shown[a.id]||SHOWN_KW;
  return '<h4>The keywords <span class="dim">('+D.plural(list.length,"keyword")+' with something to look at, worst first)</span></h4><ul class="gc-camps">'+list.slice(0,n).map(function(k){return kwHtml(a,k);}).join("")+'</ul>'+
    (list.length>n?'<p class="bs-note">The first '+n+' of '+list.length+'; <button type="button" class="cc-more" data-account="'+esc(a.id)+'">show '+Math.min(MORE,list.length-n)+' more</button>. The CSV has all.</p>':"");
}
function competitorFindingsHtml(a){
  var list=a.competitorFindings||[];
  if(!list.length) return "";
  return '<h4>Competitors in the auctions</h4><ul class="gc-finds">'+list.map(function(f){return findingHtml(a,f);}).join("")+'</ul>';
}
function competitorsHtml(a){
  var list=a.competitors||[];
  if(!list.length) return "";
  return '<details class="gc-quiet"><summary>Competitors that overlap most ('+list.length+')</summary><div class="bi-tbl"><table><thead><tr><th>Domain</th><th class="r">Overlap last week</th><th class="r">The 4 weeks before</th><th class="r">Campaigns</th></tr></thead><tbody>'+
    list.map(function(c){return '<tr><td>'+esc(c.domain)+'</td><td class="r">'+esc(D.pct(c.overlap))+'</td><td class="r">'+esc(D.pct(c.before))+'</td><td class="r">'+D.int(c.campaigns)+'</td></tr>';}).join("")+'</tbody></table></div></details>';
}
function quietHtml(a){
  var list=D.quiet(a), thin=D.notJudged(a), out="";
  if(list.length) out+='<details class="gc-quiet"><summary>Keywords holding their place ('+list.length+')</summary><div class="bi-tbl"><table><thead><tr><th>Keyword</th><th class="r">Spend, 28 days</th><th class="r">Top of page</th><th class="r">Quality Score</th></tr></thead><tbody>'+
    list.slice(0,QUIET_ROWS).map(function(k){return '<tr><td>'+esc(k.keyword)+' <span class="bi-sub">'+esc(D.matchText(k.match))+' · '+esc(k.campaign)+'</span></td><td class="r">'+esc(D.money(k.cost,a.currency))+'</td><td class="r">'+esc(D.pct(k.recent.top))+'</td><td class="r">'+esc(D.score(k.qs))+'</td></tr>';}).join("")+
    '</tbody></table></div>'+(list.length>QUIET_ROWS?'<p class="bs-note">The first '+QUIET_ROWS+' of '+list.length+'. The CSV has all.</p>':"")+'</details>';
  if(thin.length) out+='<details class="gc-quiet"><summary>Keywords not judged, too few searches ('+thin.length+')</summary><ul class="kp-out">'+thin.slice(0,QUIET_ROWS).map(function(k){
    return '<li><b>'+esc(k.keyword)+'</b> <span class="bi-sub">'+esc(k.campaign)+' · '+esc(k.why)+'</span></li>';}).join("")+'</ul></details>';
  return out;
}
function settingsHtml(a){
  var v=D.savedView(S.settings.accounts[a.id]);
  return '<h4>What the team knows about this account</h4><form class="cc-set kv-set" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'">'+
    '<label class="cc-f"><span>Lowest wanted share at the top of the page (%)</span><input data-k="topIs" inputmode="decimal" value="'+esc(v.topIs)+'" placeholder="50"></label>'+
    '<label class="cc-f"><span>Lowest wanted Quality Score</span><input data-k="qsMin" inputmode="decimal" value="'+esc(v.qsMin)+'" placeholder="5"></label>'+
    '<label class="cc-f"><span>How many top-spend keywords to track</span><input data-k="topN" inputmode="decimal" value="'+esc(v.topN)+'" placeholder="25"></label>'+
    '<label class="cc-f"><span>Competitor domains to ignore (a sister site, a partner)</span><input data-k="ignore" value="'+esc(v.ignore)+'" placeholder="example.com, partner.org"></label>'+
    '<div class="cc-set-b"><button class="pm-btn" type="submit">Save</button><span class="bi-sub" data-out></span></div></form>';
}
function body(a){
  return '<div class="bi-body">'+rulesHtml(a)+planHtml(a)+kwsHtml(a)+competitorFindingsHtml(a)+competitorsHtml(a)+quietHtml(a)+settingsHtml(a)+'</div>';
}
$("#list").addEventListener("click",function(e){
  var ign=e.target.closest(".kv-ign");
  if(ign){ ign.disabled=true; ignoreDomain(ign.dataset.account,ign.dataset.name,ign.dataset.domain,ign); return; }
  var more=e.target.closest(".cc-more");
  if(more){ S.shown[more.dataset.account]=(S.shown[more.dataset.account]||SHOWN_KW)+MORE; paintList(); return; }
  var head=e.target.closest(".bi-head"); if(head){
    var art=head.closest(".bi-row"), id=art.dataset.id; S.open[id]=!S.open[id]; paintList();
  }
});

/* ── What the team saved ──────────────────────────────────── */
function loadSettings(){
  return getJson(API+"/settings").then(function(r){ if(r.ok) S.settings={accounts:r.j.accounts||{}}; }).catch(function(){});
}
function ignoreDomain(account,name,domain,btn){
  if(!domain){ if(btn) btn.disabled=false; return; }
  postJson(API+"/settings/ignore",{account:account,domains:domain}).then(function(r){
    if(!r.ok){ if(btn) btn.disabled=false; setMsg(esc((r.j&&r.j.error)||"That could not be saved.")); return; }
    S.settings.accounts[account]=Object.assign({},r.j.account,{name:name});
    run(false,true);
  }).catch(function(){ if(btn) btn.disabled=false; setMsg("The server could not be reached. Try again."); });
}
$("#list").addEventListener("submit",function(e){
  var f=e.target.closest(".kv-set"); if(!f) return; e.preventDefault();
  var body={account:f.dataset.account,name:f.dataset.name}, out=f.querySelector("[data-out]");
  [].forEach.call(f.querySelectorAll("input,textarea"),function(t){ body[t.dataset.k]=t.value; });
  out.textContent="Saving…";
  postJson(API+"/settings/account",body).then(function(r){
    if(!r.ok){ out.textContent=(r.j&&r.j.error)||"That could not be saved."; return; }
    S.settings.accounts[body.account]=r.j.account;
    out.textContent="Saved. Checking again…";
    run(false,true);
  }).catch(function(){ out.textContent="The server could not be reached. Try again."; });
});

/* ── Refresh and CSV ──────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var blob=new Blob(["﻿"+D.csvRows(rep).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="keywords-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+String(rep.generatedAt||"").slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("kv_account")||"";
  if(window.MonitorPanel&&$("#monPanel")) window.MonitorPanel.mount($("#monPanel"),{agent:"kvis",label:"Keyword Visibility Tracker"});
  S.account=want;
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  Promise.all([getJson("/p2/paid-marketing/api/segments/accounts"),loadSettings()]).then(function(rs){
    var r=rs[0];
    if(!r.ok){picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:0}],"",true);setMsg(esc((r.j&&r.j.error)||"The account list could not be read."));return;}
    setAccounts(r.j.accounts||[],r.j.groups||[]); run();
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
})();
})();
