/* Location Conflicts (Paid Marketing Agents).
 *
 * Pick a Google Ads account, an MCC or a Meta ad account. The server reads where each campaign is set to show and holds every campaign, ad group, ad and keyword that names a place against it
 * (tracker/paid_geoconf*.py). This page draws it and never decides what is a conflict for itself. The wording and the shapes are geoconf_data.js.
 */
(function(){
"use strict";

var D = window.GCData, API = "/p2/paid-marketing/api/geoconf";
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

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}, meta:[], settings:{accounts:{}}, shown:{}};

var picker = window.PMPicker($("#acctPick"),{label:"Ad account",search:true,onChange:function(v){
  S.account=v; store("gc_account",v); run();
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
  S.meta.forEach(function(a){ items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:"Meta ad accounts",badge:'<span class="badge pm-pill">Meta</span>',sub:a.id}); });
  if(!list.some(function(a){return a.id===S.account;})&&!groupOf(S.account)&&!S.meta.some(function(a){return a.id===S.account;})) S.account="";
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
var SHOWN_CAMPS = 20, MORE = 20, QUIET_ROWS = 60;
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts checked together</span>':"")+
    ' · read '+esc(ago(rep.readAt||rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">The platform is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintInsights(); paintViews(); paintList();
  $("#foot").innerHTML="Source: the live campaigns of Google Ads (their location option, the locations and radiuses they target or exclude, the ad schedule and device adjustments, the ad group names, the responsive Search ads and the keywords) and the ad sets of Meta, saved so the same report opens instantly. "+
    "A <b>place</b> is a name the account itself uses: the places its campaigns target or exclude (with their state and country), the places saved here and for the Ad Copy Checker, and the fifty states. A campaign called after a city that no campaign targets and nobody saved is not seen. "+
    "A name that is also an ordinary word can be ignored with the button on a finding. The schedule and device checks compare a campaign with the others of its type: with fewer than three there is nothing to compare. Nothing is changed in an ad account.";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.report).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintInsights(){
  var list=D.insights(S.report), host=$("#insights");
  host.innerHTML=list.length?'<section class="pm-panel da-ins"><div class="pm-panel-h"><span class="pm-panel-t">Across the campaigns</span></div><ul>'+list.map(function(i){return '<li class="'+i.kind+'">'+esc(i.text)+'</li>';}).join("")+'</ul></section>':"";
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
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Nothing needs a look: no campaign read is set to show where it should not.")+'</p>';
}
function figHtml(a){
  var c=a.counts;
  if(a.status==="clean") return '<div class="bs-fig ok"><span class="bi-n">0</span><span class="bs-sub">nothing wrong in '+D.plural(a.campaigns,"campaign")+'</span></div>';
  if(a.status==="fix") return '<div class="bs-fig stopped"><span class="bi-n">'+D.int(c.high+c.medium)+'</span><span class="bs-sub">to fix'+(c.high?' · '+c.high+' high':"")+'</span></div>';
  return '<div class="bs-fig under"><span class="bi-n">'+D.int(c.low)+'</span><span class="bs-sub">small setting'+(c.low===1?"":"s")+'</span></div>';
}
function row(a){
  var open=!!S.open[a.id], top=a.byRule[0];
  return '<article class="bi-row '+(a.status==="fix"?"stopped":a.status==="check"?"limited":"clear")+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+(a.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+'</b><span class="bi-sub">'+D.plural(a.campaigns,a.platform==="meta"?"ad set":"campaign")+' read'+(a.findingCount?', '+D.plural(a.findingCount,"finding"):"")+'</span></span>'+
      figHtml(a)+
      '<span class="bi-delta bad">'+(top?esc(top.label)+' · '+D.plural(top.findings,"finding"):"")+'</span>'+
      '<span class="bi-why">'+(a.namesRead?"":"place names not read")+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function rulesHtml(a){
  if(!a.byRule.length) return '<p class="pm-empty">Nothing to flag: every place named in the '+D.plural(a.campaigns,"campaign")+' read agrees with where it shows.</p>';
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
      (s.where&&s.where.length?'<p class="bi-on">Start in: '+s.where.map(function(c){return '<span class="bi-chip">'+esc(c)+'</span>';}).join(" ")+'</p>':"")+
      (s.examples&&s.examples.length?'<ul class="gc-ex">'+s.examples.map(function(c){return '<li>'+esc(c)+'</li>';}).join("")+'</ul>':"")+'</div></details></li>';
  }).join("")+'</ol></div>';
}
function findingHtml(a,f){
  var l=D.levelInfo(f.level), words=D.ignorable(f);
  return '<li><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span> <b>'+esc(f.title)+'</b><span class="gc-scope">'+esc(D.scopeText(f.scope))+'</span><p class="gc-detail">'+esc(f.detail)+'</p>'+
    (f.evidence&&f.evidence.length?'<ul class="gc-evid">'+f.evidence.map(function(e){return '<li><span class="cc-slot">'+esc(e.slot)+'</span><span class="cc-text">'+esc(e.text)+'</span></li>';}).join("")+(f.count&&f.count>f.evidence.length?'<li class="bs-note">and '+(f.count-f.evidence.length)+' more</li>':"")+'</ul>':"")+
    (words.length?'<p class="gc-act">'+words.map(function(w){return '<button type="button" class="cc-ok gc-ign" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'" data-word="'+esc(w)+'">Ignore "'+esc(w)+'" as a place</button>';}).join(" ")+'</p>':"")+'</li>';
}
function campHtml(a,c){
  var top=D.levelInfo(c.level), ex=D.excludedText(c);
  return '<li class="gc-camp"><div class="gc-camp-h"><span class="bi-lv '+top.tone+'">'+esc(top.label)+'</span><b>'+esc(c.name)+'</b> <span class="bi-sub">'+esc(D.channelText(c.channel))+'</span></div>'+
    '<p class="gc-where"><span>Shows in</span> '+esc(D.targetsText(c))+(ex?' <span class="gc-not">'+esc(ex)+'</span>':"")+'</p>'+
    '<p class="gc-where"><span>Shows to</span> '+esc(D.optionText(c.positive))+'</p>'+
    '<ul class="gc-finds">'+c.findings.map(function(f){return findingHtml(a,f);}).join("")+'</ul></li>';
}
function campsHtml(a){
  var list=D.flagged(a);
  if(!list.length) return "";
  var n=S.shown[a.id]||SHOWN_CAMPS;
  return '<h4>The campaigns <span class="dim">('+D.plural(list.length,"campaign")+' with something to look at, worst first)</span></h4><ul class="gc-camps">'+list.slice(0,n).map(function(c){return campHtml(a,c);}).join("")+'</ul>'+
    (list.length>n?'<p class="bs-note">The first '+n+' of '+list.length+'; <button type="button" class="cc-more" data-account="'+esc(a.id)+'">show '+Math.min(MORE,list.length-n)+' more</button>. The CSV has all.</p>':"");
}
function quietHtml(a){
  var list=D.quiet(a);
  if(!list.length) return "";
  return '<details class="gc-quiet"><summary>'+D.plural(list.length,"campaign")+' with nothing wrong</summary><div class="bi-tbl"><table><thead><tr><th>Campaign</th><th>Type</th><th>Shows in</th><th>Shows to</th></tr></thead><tbody>'+
    list.slice(0,QUIET_ROWS).map(function(c){
      return '<tr><td>'+esc(c.name)+'</td><td>'+esc(D.channelText(c.channel))+'</td><td>'+esc(D.targetsText(c))+'</td><td>'+esc(D.optionText(c.positive))+'</td></tr>';
    }).join("")+'</tbody></table></div>'+(list.length>QUIET_ROWS?'<p class="bs-note">The first '+QUIET_ROWS+' of '+list.length+'.</p>':"")+'</details>';
}
function settingsHtml(a){
  var v=D.savedView(S.settings.accounts[a.id]);
  return '<h4>What the team knows about this account</h4><form class="cc-set gc-set" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'">'+
    '<label class="cc-f"><span>Places it may name though no campaign targets them</span><textarea data-k="areas" rows="2" spellcheck="false" placeholder="Greater Boston, one per line">'+esc(v.areas)+'</textarea></label>'+
    '<label class="cc-f"><span>Words that look like a place and are not</span><textarea data-k="ignore" rows="2" spellcheck="false" placeholder="Orange, Reading">'+esc(v.ignore)+'</textarea></label>'+
    '<label class="gc-chk"><input type="checkbox" data-k="acceptPresence"'+(v.acceptPresence?" checked":"")+'> Showing to people interested in the places is wanted (the option is not a finding)</label>'+
    '<div class="cc-set-b"><button class="pm-btn" type="submit">Save</button><span class="bi-sub" data-out></span></div></form>';
}
function body(a){
  return '<div class="bi-body">'+rulesHtml(a)+planHtml(a)+campsHtml(a)+quietHtml(a)+settingsHtml(a)+'</div>';
}
$("#list").addEventListener("click",function(e){
  var ign=e.target.closest(".gc-ign");
  if(ign){ ign.disabled=true; ignoreWord(ign.dataset.account,ign.dataset.name,ign.dataset.word,ign); return; }
  var more=e.target.closest(".cc-more");
  if(more){ S.shown[more.dataset.account]=(S.shown[more.dataset.account]||SHOWN_CAMPS)+MORE; paintList(); return; }
  var head=e.target.closest(".bi-head"); if(head){
    var art=head.closest(".bi-row"), id=art.dataset.id; S.open[id]=!S.open[id]; paintList();
  }
});

/* ── What the team saved ──────────────────────────────────── */
function loadSettings(){
  return getJson(API+"/settings").then(function(r){ if(r.ok) S.settings={accounts:r.j.accounts||{}}; }).catch(function(){});
}
function ignoreWord(account,name,word,btn){
  if(!word){ if(btn) btn.disabled=false; return; }
  postJson(API+"/settings/ignore",{account:account,words:word}).then(function(r){
    if(!r.ok){ if(btn) btn.disabled=false; setMsg(esc((r.j&&r.j.error)||"That could not be saved.")); return; }
    S.settings.accounts[account]=Object.assign({},r.j.account,{name:name});
    run(false,true);
  }).catch(function(){ if(btn) btn.disabled=false; setMsg("The server could not be reached. Try again."); });
}
$("#list").addEventListener("submit",function(e){
  var f=e.target.closest(".gc-set"); if(!f) return; e.preventDefault();
  var body={account:f.dataset.account,name:f.dataset.name}, out=f.querySelector("[data-out]");
  [].forEach.call(f.querySelectorAll("textarea,input"),function(t){ body[t.dataset.k]=t.type==="checkbox"?!!t.checked:t.value; });
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
  a.download="locations-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+String(rep.generatedAt||"").slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("gc_account")||"";
  if(window.MonitorPanel&&$("#monPanel")) window.MonitorPanel.mount($("#monPanel"),{agent:"geoconf",label:"Location Conflicts"});
  S.account=want;
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  Promise.all([getJson("/p2/paid-marketing/api/segments/accounts"),getJson("/p2/paid-marketing/api/gads/accounts?platform=meta").catch(function(){return {ok:false,j:{}};}),loadSettings()]).then(function(rs){
    var r=rs[0], meta=rs[1];
    if(!r.ok){picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:0}],"",true);setMsg(esc((r.j&&r.j.error)||"The account list could not be read."));return;}
    S.meta=meta.ok?(meta.j.accounts||[]).filter(function(a){return a.platform==="meta"||/^act_\d+$/.test(a.id);}):[];
    setAccounts(r.j.accounts||[],r.j.groups||[]); run();
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
})();
})();
