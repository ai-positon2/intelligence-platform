/* Disapproved Ads Agent (Paid Marketing Agents).
 *
 * The server reads every active ad account for the live ads Google or Meta has disapproved, keeps the findings and sends the alerts
 * (tracker/paid_disapproved*.py). This page shows what it found, lets the team dismiss one or open it again, saves the alert recipients
 * and starts a scan. It never decides what is disapproved: the server's findings are what is shown.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/disapproved";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var PAGE = 50, STALE_MS = 35 * 60 * 1000;
var STATUS = [["open","Disapproved now"],["dismissed","Dismissed"],["fixed","Fixed"]];
var WHAT = [["ad","Ads"],["asset group","Asset groups"],["asset","Assets"]];
var PLATFORMS = [["google","Google Ads"],["meta","Meta Ads"]];
var SEV = {high:["Disapproved","critical"], medium:["Asset disapproved","high"]};
var S = {data:null, st:"open", wh:"all", pl:"all", q:"", limit:PAGE, timer:null};

function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(!iso||isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleDateString("en-US",{month:"short",day:"numeric"})+", "+t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}
function plat(p){return '<span class="gp-pl '+(p==="meta"?"meta":"google")+'">'+(p==="meta"?"Meta":"Google")+'</span>';}
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
function chips(sel,defs,cur,key,counts){
  $(sel).innerHTML=defs.map(function(d){
    return '<button type="button" class="pm-chip'+(cur===d[0]?" on":"")+'" data-c="'+key+'" data-v="'+d[0]+'" aria-pressed="'+(cur===d[0])+'">'+esc(d[1])+' <span class="n">'+counts(d[0])+'</span></button>';
  }).join("");
}
function lastRun(){return (S.data.runs||[])[0]||null;}
function running(){
  var p=S.data.progress||{};
  return !!(p.running && p.startedAt && Date.now()-new Date(p.startedAt).getTime()<STALE_MS);
}

function paintKpis(){
  var d=S.data, c=d.counts, run=lastRun(), accounts={};
  d.findings.forEach(function(f){if(f.status==="open") accounts[f.accountId]=1;});
  var cards=[
    ['Disapproved now', c.open, c.open?"warn":"good", c.open?"live ads that are not running":"nothing disapproved"],
    ['Accounts affected', Object.keys(accounts).length, "", "with a disapproved ad"],
    ['Fixed', c.fixed, "", "approved, paused or removed since"],
    ['Accounts read', run&&run.read!=null?(run.read+" of "+run.accounts):"Na", run&&run.unreadableCount?"warn":"", run&&run.unreadableCount?(run.unreadableCount+" could not be read"):"at the last scan"]
  ];
  $("#kpis").innerHTML=cards.map(function(k){
    return '<div class="pd-kpi '+k[2]+'"><div class="k">'+k[0]+'</div><div class="v">'+esc(k[1])+'</div><div class="s">'+esc(k[3])+'</div></div>';
  }).join("");
}
function paintScan(){
  var run=lastRun(), busy=running(), p=S.data.progress||{};
  var f=$("#fresh");
  f.className="pd-fresh"+(run&&run.status==="done"?"":" stale");
  f.innerHTML=run?('<span class="live"></span><span>Last scan <b>'+esc(ago(run.finishedAt))+'</b>'+(run.status==="done"?
    ', '+run.read+' accounts read, '+run.disapproved+' disapproved, '+run.new+' new':', it failed')+'</span>'):
    '<span class="live"></span><span>No scan has run yet. Scan now, or wait for the daily scan.</span>';
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
  (run&&run.problems||[]).forEach(function(t){out.push('<div class="pm-note warn">'+esc(t)+'</div>');});
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
function alertCell(f){
  if(f.alertedAt) return '<span class="dim">alerted '+esc(ago(f.alertedAt))+'</span>';
  if(f.status!=="open") return '<span class="dim">-</span>';
  return '<span class="ud-wait">waiting to be sent</span>';
}
function actionCell(f){
  if(f.status==="open") return '<button type="button" class="pm-btn-ghost" data-act="dismiss" data-fp="'+f.fingerprint+'">Dismiss</button>';
  if(f.status==="dismissed") return '<button type="button" class="pm-btn-ghost" data-act="reopen" data-fp="'+f.fingerprint+'">Reopen</button>';
  return '<span class="dim">no longer disapproved</span>';
}
function paintFindings(){
  var all=S.data.findings, q=S.q.toLowerCase();
  var base=all.filter(function(f){return !q||[f.accountName,f.campaign,f.adGroup,f.objectName,f.reason,f.adType,f.url].join(" ").toLowerCase().indexOf(q)>=0;});
  chips("#stChips",STATUS,S.st,"st",function(v){return base.filter(function(f){return f.status===v;}).length;});
  var inSt=base.filter(function(f){return S.st==="all"||f.status===S.st;});
  chips("#whChips",WHAT,S.wh,"wh",function(v){return inSt.filter(function(f){return f.where===v;}).length;});
  chips("#plChips",PLATFORMS,S.pl,"pl",function(v){return inSt.filter(function(f){return f.platform===v;}).length;});
  var rows=inSt.filter(function(f){return (S.wh==="all"||f.where===S.wh)&&(S.pl==="all"||f.platform===S.pl);});
  $("#findNote").textContent=all.length?(rows.length+" shown of "+all.length):"";
  $("#sheetLink").hidden=!S.data.counts.open;
  if(!all.length){
    $("#daTbl").innerHTML='<tbody><tr><td class="dim">'+(lastRun()?"No live ad is disapproved.":"Nothing yet. Run a scan.")+'</td></tr></tbody>';
    $("#findMore").hidden=true;return;
  }
  if(!rows.length){$("#daTbl").innerHTML='<tbody><tr><td class="dim">No ad matches. Click the chosen button again to clear it.</td></tr></tbody>';$("#findMore").hidden=true;return;}
  $("#daTbl").innerHTML='<thead><tr><th>Status</th><th>Account</th><th>Campaign / ad</th><th>Why it was disapproved</th><th>Since</th><th>Alert</th><th></th></tr></thead><tbody>'+
    rows.slice(0,S.limit).map(function(f){
      var sev=SEV[f.severity]||[f.severity,""], place=[f.campaign,f.adGroup].filter(Boolean).join(" / ");
      return '<tr class="ud-'+(f.status==="open"?"open":f.status)+'"><td><span class="ud-sev '+sev[1]+'">'+esc(sev[0])+'</span><span class="gp-sub">'+esc(f.adType)+'</span></td>'+
        '<td>'+esc(f.accountName)+' '+plat(f.platform)+'</td>'+
        '<td>'+esc(f.where)+(f.objectName?'<span class="gp-sub">'+esc(f.objectName)+'</span>':"")+(place?'<span class="gp-sub">'+esc(place)+'</span>':"")+
        (f.url?'<span class="gp-sub ud-url" title="'+esc(f.url)+'">'+esc(f.url)+'</span>':"")+'</td>'+
        '<td class="ud-why">'+esc(f.reason)+'</td><td>'+esc(ago(f.firstSeen))+'</td><td>'+alertCell(f)+'</td><td class="ud-act">'+actionCell(f)+'</td></tr>';
    }).join("")+'</tbody>';
  var more=rows.length-S.limit;
  $("#findMore").hidden=more<=0;
  $("#findMore").textContent="Show "+Math.min(PAGE,more)+" more ("+more+" left)";
}
function chan(r){
  if(!r) return '<span class="dim">-</span>';
  return r.status==="sent"?'<span class="ud-ok">sent</span>':r.status==="not_configured"?'<span class="dim" title="'+esc(r.error||"")+'">not set</span>':'<span class="ud-bad" title="'+esc(r.error||"")+'">failed</span>';
}
function paintAlerts(){
  var s=S.data.settings;
  if(document.activeElement!==$("#setEmails")) $("#setEmails").value=(s.emails||[]).join(", ");
  if(document.activeElement!==$("#setSlack")) $("#setSlack").value=s.slackChannel||"";
  $("#sendNote").textContent=(s.emails.length?s.emails.length+" email"+(s.emails.length===1?"":"s"):"no email")+" · "+(s.slackChannel?s.slackChannel:"no Slack channel")+(s.own?"":" · same as the Unknown Domains Alert");
  var runs=S.data.runs||[];
  if(!runs.length){$("#runsTbl").innerHTML='<tbody><tr><td class="dim">No scan yet.</td></tr></tbody>';return;}
  $("#runsTbl").innerHTML='<thead><tr><th>When</th><th>Started by</th><th>Accounts read</th><th>Disapproved</th><th>New</th><th>Fixed</th><th>Email</th><th>Slack</th><th>Notes</th></tr></thead><tbody>'+runs.map(function(r){
    var notes=[];
    if(r.status==="failed") notes.push('<span class="ud-bad">failed: '+esc(r.error||"")+'</span>');
    if(r.unreadableCount) notes.push(r.unreadableCount+' unreadable');
    return '<tr><td>'+esc(ago(r.finishedAt))+'</td><td>'+esc(r.trigger==="schedule"?"the schedule":String(r.trigger||"").replace(/^page by /,"").split("@")[0])+'</td><td>'+(r.read!=null?r.read+" of "+r.accounts:"-")+
      '</td><td>'+(r.disapproved!=null?r.disapproved:"-")+'</td><td>'+(r.new!=null?r.new+(r.reopened?" (+"+r.reopened+" back)":""):"-")+'</td><td>'+(r.fixed||0)+'</td><td>'+chan(r.email)+'</td><td>'+chan(r.slack)+
      '</td><td>'+(notes.join("; ")||'<span class="dim">-</span>')+'</td></tr>';
  }).join("")+'</tbody>';
}

function render(){
  paintKpis();paintScan();paintFindings();paintAlerts();
  $("#skel").hidden=true;$("#out").hidden=false;
}
function load(){
  return api("GET","/state").then(function(d){
    S.data=d;setMsg("");render();
    if(running()&&!S.timer) S.timer=setInterval(load,3000);
    if(!running()&&S.timer){clearInterval(S.timer);S.timer=null;}
  }).catch(function(e){$("#skel").hidden=true;setMsg(esc(e.message));});
}

document.addEventListener("click",function(ev){
  var t=ev.target.closest("button");if(!t) return;
  if(t.dataset.c){var k=t.dataset.c, v=t.dataset.v;S[k]=S[k]===v?"all":v;S.limit=PAGE;paintFindings();return;}
  if(t.id==="findMore"){S.limit+=PAGE;paintFindings();return;}
  if(t.id==="scanBtn"){
    t.disabled=true;
    api("POST","/scan").then(function(){S.data.progress={running:true,startedAt:new Date().toISOString(),done:0,total:0};render();if(!S.timer) S.timer=setInterval(load,3000);})
      .catch(function(e){setMsg(esc(e.message));load();});return;
  }
  if(t.dataset.act){api("POST","/findings/"+t.dataset.fp+"/"+t.dataset.act).then(load).catch(function(e){setMsg(esc(e.message));});return;}
  if(t.id==="testBtn"){
    t.disabled=true;$("#setRes").innerHTML='<div class="pm-note">Sending…</div>';
    api("POST","/test-alert").then(function(r){
      var w=function(n,c){return n+": "+(c.status==="sent"?"sent":c.status==="not_configured"?"not set up ("+(c.error||"")+")":"failed ("+(c.error||"")+")");};
      $("#setRes").innerHTML='<div class="pm-note '+(r.sent?"":"warn")+'">'+esc(w("Email",r.email))+'<br>'+esc(w("Slack",r.slack))+'</div>';
    }).catch(function(e){$("#setRes").innerHTML='<div class="pm-err">'+esc(e.message)+'</div>';}).then(function(){t.disabled=false;});
  }
});
document.addEventListener("input",function(ev){
  if(ev.target.id==="findFilter"){S.q=ev.target.value;S.limit=PAGE;paintFindings();}
});
$("#setForm").addEventListener("submit",function(ev){
  ev.preventDefault();
  api("POST","/settings",{emails:$("#setEmails").value,slackChannel:$("#setSlack").value}).then(function(){
    $("#setRes").innerHTML='<div class="pm-note">Saved.</div>';return load();
  }).catch(function(e){$("#setRes").innerHTML='<div class="pm-err">'+esc(e.message)+'</div>';});
});
load();
})();
