/* Unknown Domains Alert (Paid Marketing Agents).
 *
 * The server reads every ad account, judges each address against the domain database, keeps the findings and sends the alerts
 * (tracker/paid_domains*.py). This page shows what it found, lets the team approve or dismiss a finding, manages the database
 * and the alert recipients, and starts a scan. It never decides what is a finding: the server's verdict is what is shown.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/domains";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var PAGE = 50, STALE_MS = 35 * 60 * 1000;
var STATUS = [["open","Open"],["approved","Approved"],["dismissed","Dismissed"],["gone","Gone"]];
var SEVERITY = [["critical","Critical"],["high","High"],["medium","Medium"]];
var SEV_TEXT = {critical:"Critical",high:"High",medium:"Wrong account"};
var KIND_TEXT = {unknown:"Unknown domain", wrong_account:"Wrong account"};
var S = {data:null, st:"open", sv:"all", q:"", limit:PAGE, dbq:"", dbLimit:PAGE, useq:"", timer:null};

function dashed(id){return /^\d{10}$/.test(id)?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(!iso||isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleDateString("en-US",{month:"short",day:"numeric"})+", "+t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function api(method,path,body){
  var o={method:method,headers:{"Accept":"application/json"},credentials:"same-origin"};
  if(method!=="GET"){o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body||{});}
  return fetch(API+path,o).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){
      if(!r.ok) throw new Error(j.error||("The server answered "+r.status+"."));
      return j;
    });
  });
}
function names(){
  var m={"*":"Everyone"};
  (S.data.groups||[]).forEach(function(g){m[g.id]=g.name+" (client)";});
  (S.data.accounts||[]).forEach(function(a){m[a.id]=a.name+" ("+(a.platform==="meta"?"Meta":"Google")+")";});
  return m;
}
function acct(id){return (S.data.accounts||[]).filter(function(a){return a.id===id;})[0];}
function plat(p){return '<span class="gp-pl '+(p==="meta"?"meta":"google")+'">'+(p==="meta"?"Meta":"Google")+'</span>';}
function chips(sel,defs,cur,key,counts){
  $(sel).innerHTML=defs.map(function(d){
    return '<button type="button" class="pm-chip'+(cur===d[0]?" on":"")+'" data-c="'+key+'" data-v="'+d[0]+'" aria-pressed="'+(cur===d[0])+'">'+esc(d[1])+
      (counts?' <span class="n">'+counts(d[0])+'</span>':"")+'</button>';
  }).join("");
}
function lastRun(){return (S.data.runs||[])[0]||null;}
function running(){
  var p=S.data.progress||{};
  return !!(p.running && p.startedAt && Date.now()-new Date(p.startedAt).getTime()<STALE_MS);
}

/* ── Strip, scan, problems ──────────────────────────────── */
function openTakeovers(){
  var by={};
  S.data.findings.forEach(function(f){
    if(f.status==="open"&&f.kind==="unknown"){(by[f.accountId]=by[f.accountId]||{}) [f.domain]=1;}
  });
  return Object.keys(by).filter(function(k){return Object.keys(by[k]).length>=3;}).length;
}
function paintKpis(){
  var d=S.data, c=d.counts, run=lastRun(), take=openTakeovers();
  var cards=[
    ['Open findings', c.open, c.open?"warn":"good", c.open?"waiting for a decision":"nothing to review"],
    ['Possible takeovers', take, take?"warn":"good", take?"accounts with 3 or more unknown domains":"none"],
    ['Domains approved', d.known.length, "", "in the database"],
    ['Accounts read', run&&run.read!=null?(run.read+" of "+run.accounts):"Na", run&&run.unreadableCount?"warn":"", run&&run.unreadableCount?(run.unreadableCount+" could not be read"):"at the last scan"],
    ['Alerts', d.baseline?"On":"Off", d.baseline?"good":"warn", d.baseline?"set by "+esc((d.baseline.by||"").split("@")[0]):"set the baseline first"]
  ];
  $("#kpis").innerHTML=cards.map(function(k){
    return '<div class="pd-kpi '+k[2]+'"><div class="k">'+k[0]+'</div><div class="v">'+esc(k[1])+'</div><div class="s">'+k[3]+'</div></div>';
  }).join("");
}
function paintScan(){
  var run=lastRun(), busy=running(), p=S.data.progress||{};
  var f=$("#fresh");
  f.className="pd-fresh"+(run&&run.status==="done"?"":" stale");
  f.innerHTML=run?('<span class="live"></span><span>Last scan <b>'+esc(ago(run.finishedAt))+'</b>'+(run.status==="done"?
    ', '+run.sightings+' addresses in '+run.read+' accounts, '+run.new+' new finding'+(run.new===1?"":"s"):', it failed')+'</span>'):
    '<span class="live"></span><span>No scan has run yet. Scan now, or wait for the next scan (every 4 hours).</span>';
  $("#scanBtn").disabled=busy;
  $("#scanBtn").textContent=busy?"Scanning…":"Scan now";
  var pr=$("#progress");
  pr.hidden=!busy;
  if(busy){
    var pct=p.total?Math.round(100*p.done/p.total):4;
    pr.querySelector("i").style.width=Math.max(4,pct)+"%";
    pr.querySelector("span").textContent=p.total?("Reading account "+p.done+" of "+p.total):"Listing the accounts…";
  }
  var out=[];
  (S.data.problems||[]).forEach(function(t){out.push('<div class="pm-note warn">'+esc(t)+'</div>');});
  if(run&&run.status==="failed") out.push('<div class="pm-note warn"><b>The last scan failed.</b> '+esc(run.error||"")+'</div>');
  if(run&&run.unreadableCount){
    out.push('<details class="pm-note warn"><summary><b>'+run.unreadableCount+' account'+(run.unreadableCount===1?"":"s")+' could not be read at the last scan.</b> Their ads were not checked.</summary><ul class="ud-list">'+
      run.unreadable.map(function(u){return '<li>'+esc(u.name)+' '+plat(u.platform)+' <span class="dim">'+esc(u.error||"")+'</span></li>';}).join("")+
      (run.unreadableCount>run.unreadable.length?'<li class="dim">and '+(run.unreadableCount-run.unreadable.length)+' more</li>':"")+'</ul></details>');
  }
  var sn=S.data.senders||{}, miss=[];
  if(!sn.email) miss.push("email (no sender is set up on the server)");
  if(!sn.slack) miss.push("Slack (SLACK_BOT_TOKEN is not set on the server)");
  if(miss.length) out.push('<div class="pm-note warn">These alerts cannot be sent yet: '+miss.join(" and ")+'.</div>');
  $("#problems").innerHTML=out.join("");
}

/* ── Baseline ───────────────────────────────────────────── */
function paintBase(){
  var show=!S.data.baseline;
  $("#baseCard").hidden=!show;
  if(!show) return;
  var rows=S.data.inUse||[], q=S.useq.toLowerCase();
  var shown=rows.filter(function(a){return !q||a.name.toLowerCase().indexOf(q)>=0||a.domains.some(function(d){return d.domain.indexOf(q)>=0;});});
  $("#trustAll").disabled=$("#trustNone").disabled=!rows.length;
  if(!rows.length){$("#useTbl").innerHTML='<tbody><tr><td class="dim">Run a scan first. It shows here what each account points at today.</td></tr></tbody>';return;}
  $("#useTbl").innerHTML='<thead><tr><th>Account</th><th>Domains its ads point at today</th><th></th></tr></thead><tbody>'+shown.slice(0,60).map(function(a){
    return '<tr><td>'+esc(a.name)+' '+plat(a.platform)+'</td><td>'+(a.domains.length?a.domains.map(function(d){
      return '<span class="ud-dom'+(d.known?"":" bad")+'" title="'+d.ads+' address'+(d.ads===1?"":"es")+'">'+esc(d.domain)+'</span>';}).join(" "):'<span class="dim">none</span>')+
      '</td><td><button type="button" class="pm-btn-ghost" data-trust="'+esc(a.accountId)+'">Trust this account\'s</button></td></tr>';
  }).join("")+(shown.length>60?'<tr><td colspan="3" class="dim">Showing 60 of '+shown.length+'. Search to narrow.</td></tr>':"")+'</tbody>';
}

/* ── Findings ───────────────────────────────────────────── */
function alertCell(f){
  if(f.alertedAt) return '<span class="dim">alerted '+esc(ago(f.alertedAt))+'</span>';
  if(f.status!=="open") return '<span class="dim">-</span>';
  if(!S.data.baseline) return '<span class="dim">held until the baseline is set</span>';
  return '<span class="ud-wait">waiting to be sent</span>';
}
function actionCell(f){
  if(f.status==="open"){
    var a=acct(f.accountId), hasGroup=!!(a&&a.group);
    return '<select class="ud-appr" data-fp="'+f.fingerprint+'" aria-label="Approve this domain for"><option value="">Approve for…</option>'+
      '<option value="account">This account</option>'+(hasGroup?'<option value="group">Its client</option>':"")+'<option value="everyone">Everyone</option></select> '+
      '<button type="button" class="pm-btn-ghost" data-act="dismiss" data-fp="'+f.fingerprint+'">Dismiss</button>';
  }
  if(f.status==="dismissed") return '<button type="button" class="pm-btn-ghost" data-act="reopen" data-fp="'+f.fingerprint+'">Reopen</button>';
  if(f.status==="approved") return '<span class="dim">approved by '+esc((f.decidedBy||"").split("@")[0])+'</span>';
  return '<span class="dim">fixed in the account</span>';
}
function paintFindings(){
  var all=S.data.findings, q=S.q.toLowerCase();
  var base=all.filter(function(f){
    return !q||[f.accountName,f.domain,f.host,f.campaign,f.adGroup,f.objectName,f.url,f.where].join(" ").toLowerCase().indexOf(q)>=0;});
  var stCount=function(v){return base.filter(function(f){return f.status===v;}).length;};
  chips("#stChips",STATUS,S.st,"st",stCount);
  var inSt=base.filter(function(f){return S.st==="all"||f.status===S.st;});
  chips("#svChips",SEVERITY,S.sv,"sv",function(v){return inSt.filter(function(f){return f.severity===v;}).length;});
  var rows=inSt.filter(function(f){return S.sv==="all"||f.severity===S.sv;});
  $("#findNote").textContent=all.length?(rows.length+" shown of "+all.length):"";
  if(!all.length){
    $("#findTbl").innerHTML='<tbody><tr><td class="dim">'+(lastRun()?"Nothing found. Every address the ads point at is in the database.":"Nothing yet. Run a scan.")+'</td></tr></tbody>';
    $("#findMore").hidden=true;return;
  }
  if(!rows.length){$("#findTbl").innerHTML='<tbody><tr><td class="dim">No finding matches. Click the chosen button again to clear it.</td></tr></tbody>';$("#findMore").hidden=true;return;}
  $("#findTbl").innerHTML='<thead><tr><th>Severity</th><th>Account</th><th>Where</th><th>Address</th><th>Why</th><th>First seen</th><th>Alert</th><th></th></tr></thead><tbody>'+
    rows.slice(0,S.limit).map(function(f){
      var place=[f.campaign,f.adGroup].filter(Boolean).join(" / ");
      return '<tr class="ud-'+f.status+'"><td><span class="ud-sev '+f.severity+'" title="'+esc(KIND_TEXT[f.kind]||f.kind)+'">'+esc(SEV_TEXT[f.severity]||f.severity)+'</span>'+
        (f.redirect?' <span class="ud-sev redirect">Redirect</span>':"")+'</td>'+
        '<td>'+esc(f.accountName)+' '+plat(f.platform)+'<span class="gp-sub">'+esc(f.platform==="meta"?f.accountId:dashed(f.accountId))+'</span></td>'+
        '<td>'+esc(f.where)+(f.objectName?'<span class="gp-sub">'+esc(f.objectName)+'</span>':"")+(place?'<span class="gp-sub">'+esc(place)+'</span>':"")+'</td>'+
        '<td><b>'+esc(f.host)+'</b><span class="gp-sub ud-url" title="'+esc(f.url)+'">'+esc(f.url)+'</span></td>'+
        '<td class="ud-why">'+esc(f.why)+'</td><td>'+esc(ago(f.firstSeen))+'</td><td>'+alertCell(f)+'</td><td class="ud-act">'+actionCell(f)+'</td></tr>';
    }).join("")+'</tbody>';
  var more=rows.length-S.limit;
  $("#findMore").hidden=more<=0;
  $("#findMore").textContent="Show "+Math.min(PAGE,more)+" more ("+more+" left)";
}

/* ── Domain database ────────────────────────────────────── */
function scopeOptions(){
  var d=S.data, h='<option value="*">Everyone</option>';
  if((d.groups||[]).length) h+='<optgroup label="A client (all its accounts)">'+d.groups.map(function(g){return '<option value="'+esc(g.id)+'">'+esc(g.name)+'</option>';}).join("")+'</optgroup>';
  [["google","Google Ads account"],["meta","Meta Ads account"]].forEach(function(p){
    var l=(d.accounts||[]).filter(function(a){return a.platform===p[0];});
    if(l.length) h+='<optgroup label="'+p[1]+'">'+l.map(function(a){return '<option value="'+esc(a.id)+'">'+esc(a.name)+' ('+esc(dashed(a.id))+')</option>';}).join("")+'</optgroup>';
  });
  return h;
}
function paintDb(){
  var d=S.data, nm=names(), q=S.dbq.toLowerCase();
  var cur=$("#addScope").value;
  if(!$("#addScope").dataset.n||$("#addScope").dataset.n!==String((d.accounts||[]).length+"/"+(d.groups||[]).length)){
    $("#addScope").innerHTML=scopeOptions();$("#addScope").dataset.n=(d.accounts||[]).length+"/"+(d.groups||[]).length;
    if(cur) $("#addScope").value=cur;
  }
  var rows=d.known.filter(function(k){return !q||(k.domain+" "+(nm[k.scope]||k.scope)+" "+k.note).toLowerCase().indexOf(q)>=0;});
  $("#dbNote").textContent=d.known.length+" domain"+(d.known.length===1?"":"s")+" approved";
  if(!d.known.length){$("#dbTbl").innerHTML='<tbody><tr><td class="dim">The database is empty. Add a domain above, import a list, or set the baseline from what the accounts use.</td></tr></tbody>';$("#dbMore").hidden=true;return;}
  $("#dbTbl").innerHTML='<thead><tr><th>Domain</th><th>For</th><th>Note</th><th>Added</th><th></th></tr></thead><tbody>'+rows.slice(0,S.dbLimit).map(function(k){
    return '<tr><td><b>'+esc(k.domain)+'</b></td><td>'+esc(nm[k.scope]||k.scope)+'</td><td>'+esc(k.note)+'</td><td class="dim">'+esc((k.added_by||"").split("@")[0])+(k.added_at?", "+esc(new Date(k.added_at).toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"})):"")+
      '</td><td><button type="button" class="pm-btn-ghost" data-rm="'+k.id+'" aria-label="Remove '+esc(k.domain)+'">Remove</button></td></tr>';
  }).join("")+(rows.length?"":'<tr><td colspan="5" class="dim">No entry matches.</td></tr>')+'</tbody>';
  var more=rows.length-S.dbLimit;
  $("#dbMore").hidden=more<=0;
  $("#dbMore").textContent="Show "+Math.min(PAGE,more)+" more ("+more+" left)";
}

/* ── Alerts and scans ───────────────────────────────────── */
function chan(r){
  if(!r) return '<span class="dim">-</span>';
  return r.status==="sent"?'<span class="ud-ok">sent</span>':r.status==="not_configured"?'<span class="dim" title="'+esc(r.error||"")+'">not set</span>':'<span class="ud-bad" title="'+esc(r.error||"")+'">failed</span>';
}
function paintAlerts(){
  var s=S.data.settings, sn=S.data.senders||{};
  if(document.activeElement!==$("#setEmails")) $("#setEmails").value=(s.emails||[]).join(", ");
  if(document.activeElement!==$("#setSlack")) $("#setSlack").value=s.slackChannel||"";
  $("#sendNote").textContent=(s.emails.length?s.emails.length+" email"+(s.emails.length===1?"":"s"):"no email")+" · "+(s.slackChannel?s.slackChannel:"no Slack channel");
  var runs=S.data.runs||[];
  if(!runs.length){$("#runsTbl").innerHTML='<tbody><tr><td class="dim">No scan yet.</td></tr></tbody>';return;}
  $("#runsTbl").innerHTML='<thead><tr><th>When</th><th>Started by</th><th>Accounts read</th><th>New</th><th>Alerted</th><th>Email</th><th>Slack</th><th>Notes</th></tr></thead><tbody>'+runs.map(function(r){
    var notes=[];
    if(r.status==="failed") notes.push('<span class="ud-bad">failed: '+esc(r.error||"")+'</span>');
    if(r.takeover&&r.takeover.length) notes.push('<span class="ud-bad">possible takeover: '+r.takeover.map(function(t){return esc(t.accountName);}).join(", ")+'</span>');
    if(r.unreadableCount) notes.push(r.unreadableCount+' unreadable');
    if(r.status==="done"&&!r.baseline) notes.push('<span class="dim">no baseline, nothing sent</span>');
    return '<tr><td>'+esc(ago(r.finishedAt))+'</td><td>'+esc(r.trigger==="schedule"?"the schedule":String(r.trigger||"").replace(/^page by /,"").split("@")[0])+'</td><td>'+(r.read!=null?r.read+" of "+r.accounts:"-")+
      '</td><td>'+(r.new!=null?r.new+(r.reopened?" (+"+r.reopened+" back)":""):"-")+'</td><td>'+(r.alerted||0)+'</td><td>'+chan(r.email)+'</td><td>'+chan(r.slack)+'</td><td>'+(notes.join("; ")||'<span class="dim">-</span>')+'</td></tr>';
  }).join("")+'</tbody>';
}

function render(){
  paintKpis();paintScan();paintBase();paintFindings();paintDb();paintAlerts();
  $("#skel").hidden=true;$("#out").hidden=false;
}
function load(){
  return api("GET","/state").then(function(d){
    S.data=d;setMsg("");render();
    if(running()&&!S.timer) S.timer=setInterval(load,3000);
    if(!running()&&S.timer){clearInterval(S.timer);S.timer=null;}
  }).catch(function(e){
    $("#skel").hidden=true;
    setMsg(esc(e.message));
  });
}
function act(p,msg){
  return p.then(function(r){setMsg("");return load().then(function(){return r;});}).catch(function(e){setMsg(esc(e.message));});
}

/* ── Events ─────────────────────────────────────────────── */
document.addEventListener("click",function(ev){
  var t=ev.target.closest("button,summary,a");if(!t) return;
  if(t.dataset.c){
    var k=t.dataset.c, v=t.dataset.v;
    S[k]=S[k]===v?"all":v;S.limit=PAGE;paintFindings();return;
  }
  if(t.id==="scanBtn"){
    t.disabled=true;
    api("POST","/scan").then(function(){S.data.progress={running:true,startedAt:new Date().toISOString(),done:0,total:0};render();if(!S.timer) S.timer=setInterval(load,3000);})
      .catch(function(e){setMsg(esc(e.message));load();});return;
  }
  if(t.dataset.act){act(api("POST","/findings/"+t.dataset.fp+"/"+t.dataset.act));return;}
  if(t.dataset.rm){
    if(confirm("Remove this domain from the database? Ads that use it become findings at the next scan.")) act(api("DELETE","/known/"+t.dataset.rm));return;
  }
  if(t.id==="trustAll"){
    if(confirm("Add every domain listed to the database, for the account that uses it, and start alerting?")) act(api("POST","/baseline",{trust:"all"}));return;
  }
  if(t.id==="trustNone"){
    if(confirm("Start alerting now without adding anything? Every address not in the database will be reported as new.")) act(api("POST","/baseline",{trust:[]}));return;
  }
  if(t.dataset.trust){act(api("POST","/baseline",{trust:[t.dataset.trust]}));return;}
  if(t.id==="findMore"){S.limit+=PAGE;paintFindings();return;}
  if(t.id==="dbMore"){S.dbLimit+=PAGE;paintDb();return;}
  if(t.id==="impGo"){doImport({text:$("#impText").value});return;}
  if(t.id==="impSheetGo"){doImport({sheetUrl:$("#impSheet").value});return;}
  if(t.id==="testBtn"){
    t.disabled=true;$("#setRes").innerHTML='<div class="pm-note">Sending…</div>';
    api("POST","/test-alert").then(function(r){
      var w=function(n,c){return n+": "+(c.status==="sent"?"sent":c.status==="not_configured"?"not set up ("+(c.error||"")+")":"failed ("+(c.error||"")+")");};
      $("#setRes").innerHTML='<div class="pm-note '+(r.sent?"":"warn")+'">'+esc(w("Email",r.email))+'<br>'+esc(w("Slack",r.slack))+'</div>';
    }).catch(function(e){$("#setRes").innerHTML='<div class="pm-err">'+esc(e.message)+'</div>';}).then(function(){t.disabled=false;});
  }
});
document.addEventListener("change",function(ev){
  var t=ev.target;
  if(t.classList.contains("ud-appr")&&t.value){
    var who=t.value;t.disabled=true;
    api("POST","/findings/"+t.dataset.fp+"/approve",{who:who}).then(function(r){
      setMsg(esc(r.approved+" finding"+(r.approved===1?"":"s")+" approved and the domain added to the database."),"pm-note");return load();
    }).catch(function(e){t.disabled=false;t.value="";setMsg(esc(e.message));});
  }
  if(t.id==="impFile"&&t.files&&t.files[0]){
    var rd=new FileReader();rd.onload=function(){doImport({text:String(rd.result||"")});t.value="";};rd.readAsText(t.files[0]);
  }
});
document.addEventListener("input",function(ev){
  var id=ev.target.id;
  if(id==="findFilter"){S.q=ev.target.value;S.limit=PAGE;paintFindings();}
  else if(id==="dbFilter"){S.dbq=ev.target.value;S.dbLimit=PAGE;paintDb();}
  else if(id==="useFilter"){S.useq=ev.target.value;paintBase();}
});
$("#addForm").addEventListener("submit",function(ev){
  ev.preventDefault();
  api("POST","/known",{domain:$("#addDomain").value,scope:$("#addScope").value,note:$("#addNote").value}).then(function(r){
    $("#addDomain").value="";$("#addNote").value="";
    setMsg(r.created?"Added.":"That domain was already in the database for them.","pm-note");return load();
  }).catch(function(e){setMsg(esc(e.message));});
});
$("#setForm").addEventListener("submit",function(ev){
  ev.preventDefault();
  api("POST","/settings",{emails:$("#setEmails").value,slackChannel:$("#setSlack").value}).then(function(){
    $("#setRes").innerHTML='<div class="pm-note">Saved.</div>';return load();
  }).catch(function(e){$("#setRes").innerHTML='<div class="pm-err">'+esc(e.message)+'</div>';});
});
function doImport(body){
  $("#impRes").innerHTML='<div class="pm-note">Importing…</div>';
  api("POST","/known/import",body).then(function(r){
    var h='Added '+r.added+', already there '+r.existing+(r.skippedCount?', skipped '+r.skippedCount:"")+'.';
    if(r.skipped&&r.skipped.length) h+='<ul class="ud-list">'+r.skipped.map(function(s){return '<li>Row '+s.row+': '+esc(s.text)+' ('+esc(s.problem)+')</li>';}).join("")+'</ul>';
    $("#impRes").innerHTML='<div class="pm-note '+(r.skippedCount?"warn":"")+'">'+h+'</div>';
    return load();
  }).catch(function(e){$("#impRes").innerHTML='<div class="pm-err">'+esc(e.message)+'</div>';});
}
load();
})();
