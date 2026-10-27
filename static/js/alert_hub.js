/* Alert Hub (Paid Marketing Agents).
 *
 * The one layer every monitor's alert goes through. The server decides (tracker/paid_alerthub.py); this page shows where alerts go (the default destination and each client's own), every open issue
 * of every monitor with its severity and what covers it, what was sent, and the rules. The wording and the shapes are alert_hub_data.js. It reads and changes nothing in an ad account.
 */
(function(){
"use strict";

var D = window.AHData, API = "/p2/paid-marketing/api/alerthub", SHOWN = 200;
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleDateString("en-US",{month:"short",day:"numeric"})+" ("+rel+")";
}

var S = {state:null, f:{p:"all",client:"all",agent:"all",held:"show"}};

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
function show(which){$("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}

/* ── Loading ──────────────────────────────────────────────── */
function load(keep){
  if(!keep) show("skel");
  return getJson(API+"/state").then(function(r){
    if(!r.ok){show(keep?"out":"none"); setMsg(esc((r.j&&r.j.error)||("The alerts could not be read (HTTP "+r.status+").")));return;}
    S.state=r.j; show("out");
    try{render();}catch(e){setMsg("The alerts were read but could not be drawn: "+esc(String(e&&e.message||e)));}
  }).catch(function(){show(keep?"out":"none"); setMsg("The server could not be reached. Try again.");});
}

/* ── Rendering ────────────────────────────────────────────── */
function render(){
  var st=S.state;
  $("#fresh").innerHTML='<span class="live"></span><b>'+D.plural(st.openTotal,"open issue")+'</b> in '+D.plural((st.agents||[]).length,"monitor")+((st.log||[])[0]?' · last send '+esc(ago(st.log[0].at)):"");
  paintKpis(); paintDest(); paintFilters(); paintOpen(); paintLog(); paintRules(); paintChanges();
  $("#foot").innerHTML="Every monitor still finds and keeps its own issues; what changed is where their alerts go. "+esc(senders(st.senders))+". Nothing is changed in an ad account.";
}
function senders(s){
  s=s||{};
  return "Email "+(s.email?"is set up":"is not set up on this server")+" · Slack "+(s.slack?"is set up":"is not set up on this server");
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.state).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"||c[3]==="bad"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintDest(){
  var st=S.state, rows=D.destinations(st), d=rows[0];
  $("#dEmails").value=D.emailsText(d.emails); $("#dSlack").value=d.slack;
  $("#destNote").textContent=st.hasClients?"A client with no channel of its own uses the default":"No client in the Master Agent yet, so every account uses the default";
  $("#destClients").innerHTML=st.hasClients?'<div class="bi-tbl"><table><thead><tr><th>Client</th><th>Slack channel</th><th>Emails</th><th>Alerts from</th><th></th></tr></thead><tbody>'+rows.slice(1).map(function(r){
    return '<tr><td><b>'+esc(r.name)+'</b><span class="bi-sub"> '+D.plural(r.accounts,"account")+'</span><div class="bi-sub">'+esc(D.destText(r,st.default))+'</div></td>'+
      '<td><input type="text" id="rs-'+esc(r.key)+'" value="'+esc(r.slack)+'" placeholder="default channel" spellcheck="false" autocomplete="off"></td>'+
      '<td><input type="text" id="re-'+esc(r.key)+'" value="'+esc(D.emailsText(r.emails))+'" placeholder="default emails" spellcheck="false" autocomplete="off"></td>'+
      '<td><select id="rf-'+esc(r.key)+'"><option value="P2"'+(r.floor==="P2"?" selected":"")+'>P1 and P2</option><option value="P1"'+(r.floor==="P1"?" selected":"")+'>P1 only</option></select></td>'+
      '<td class="ah-act"><button type="button" class="pm-btn ah-save" data-id="'+esc(r.key)+'">Save</button> <button type="button" class="pm-btn-ghost ah-test" data-id="'+esc(r.key)+'">Send a test</button></td></tr>';
  }).join("")+'</tbody></table></div>':'<p class="pm-empty">Clients are written down in the <a href="/p2/paid-marketing/master-agent">Master Agent</a>. Each one can then have a Slack channel of its own here.</p>';
}
function paintFilters(){
  var fc=D.facets(S.state.open), f=S.f;
  function opts(list,cur,all){return '<option value="all">'+esc(all)+'</option>'+list.map(function(o){return '<option value="'+esc(o.id)+'"'+(cur===o.id?" selected":"")+'>'+esc(o.name)+' ('+o.count+')</option>';}).join("");}
  $("#fClient").innerHTML=opts(fc.clients,f.client,"All clients"); $("#fAgent").innerHTML=opts(fc.agents,f.agent,"All monitors");
  $("#fP").value=f.p; $("#fHeld").value=f.held;
}
function paintOpen(){
  var st=S.state, rows=D.filterOpen(st.open,S.f), shown=rows.slice(0,SHOWN);
  $("#openNote").textContent=D.plural(rows.length,"issue")+(rows.length<st.openTotal?" of "+st.openTotal:"")+(st.openTotal>st.open.length?" (the page lists the first "+st.open.length+")":"");
  $("#open").innerHTML=shown.length?'<div class="bi-tbl"><table><thead><tr><th>Level</th><th>Client</th><th>Account</th><th>Monitor</th><th>Issue</th><th>Open for</th><th>Alerted</th></tr></thead><tbody>'+shown.map(function(r){
    return '<tr'+(r.coveredBy?' class="ah-held"':"")+'><td><span class="bi-lv '+D.TONE[r.p]+'">'+esc(r.p)+'</span></td><td>'+esc(r.clientName||"")+'</td><td>'+esc(r.accountName)+(r.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+'</td>'+
      '<td><a href="'+esc(r.page)+'">'+esc(r.agentLabel)+'</a></td><td>'+esc(r.title)+(r.coveredBy?'<div class="bi-sub">'+esc(D.coveredText(r))+'</div>':"")+'</td><td>'+esc(D.ageText(r))+'</td><td>'+esc(D.alertedText(r))+'</td></tr>';
  }).join("")+'</tbody></table></div>'+(rows.length>SHOWN?'<p class="mp-note">The first '+SHOWN+' of '+rows.length+'. Narrow it with the filters, or export the CSV for all of them.</p>':""):'<p class="pm-empty">'+(st.openTotal?"Nothing matches the filters.":"Nothing is open in any monitor.")+'</p>';
}
function paintLog(){
  var log=(S.state.log||[]).map(D.logLine);
  $("#log").innerHTML=log.length?'<ul class="mp-runs">'+log.map(function(l){
    return '<li class="'+l.tone+'"><span>'+esc(l.when)+'</span> '+esc(l.trigger)+' · '+esc(l.text)+(l.sends.length?'<ul>'+l.sends.map(function(s){return '<li class="'+s.tone+'">'+esc(s.text)+'</li>';}).join("")+'</ul>':"")+'</li>';
  }).join("")+'</ul>':'<p class="pm-empty">Nothing has been sent through the hub yet. The first scheduled run after a monitor has set its baseline will.</p>';
}
function paintRules(){
  $("#rules").innerHTML='<ul class="th-blind-l"><li><b>P1</b> is a high issue, <b>P2</b> a medium one, <b>P3</b> a low one. P1 and P2 are alerted; P3 stays on its page and in the Monday summary.</li>'+
    '<li>One scheduled run sends one message per destination for every monitor, not one per monitor. A client with its own channel gets its own message; the rest go to the default.</li>'+
    D.ruleLines(S.state).map(function(l){return '<li>'+esc(l)+'</li>';}).join("")+
    '<li>Not done, on purpose: quiet hours, snoozing, throttling an issue that keeps coming back, and paging a person.</li></ul>';
}
function paintChanges(){
  var ch=S.state.changes||[];
  $("#changesCard").hidden=!ch.length;
  $("#changes").innerHTML=ch.map(function(c){return '<li>'+esc(ago(c.at))+' · '+esc(c.by)+' '+esc(c.what)+'</li>';}).join("");
}

/* ── Saving and testing a destination ─────────────────────── */
function note(html,kind){setMsg(html,kind);}
function testSend(route){
  note("Sending a test…","pm-note");
  return postJson(API+"/test",{route:route}).then(function(r){
    if(!r.ok){note(esc((r.j&&r.j.error)||("The test could not be sent (HTTP "+r.status+").")));return;}
    var j=r.j, bits=[]; if(j.slack&&j.slack.status!=="not_configured") bits.push("Slack: "+(j.slack.status==="sent"?"sent":"failed ("+esc(j.slack.error||"")+")"));
    if(j.email&&j.email.status!=="not_configured") bits.push("email: "+(j.email.status==="sent"?"sent":"failed ("+esc(j.email.error||"")+")"));
    note(j.sent?"Test sent ("+bits.join(", ")+") to "+esc(j.name||"the default destination")+".":(bits.length?"The test was not delivered: "+bits.join(", ")+".":"Nothing was sent: no Slack channel or email is set for "+esc(j.name||"the default destination")+"."),j.sent?"pm-note":"pm-err");
  }).catch(function(){note("The server could not be reached. Nothing was sent.");});
}
$("#dSave").addEventListener("click",function(){
  postJson("/p2/paid-marketing/api/monitors/alerts",{emails:$("#dEmails").value,slackChannel:$("#dSlack").value}).then(function(r){
    if(!r.ok){note(esc((r.j&&r.j.error)||"The default destination could not be saved."));return;}
    return load(true).then(function(){note("Saved the default destination. Every monitor page shows the same one.","pm-note");});
  }).catch(function(){note("The server could not be reached. Nothing was saved.");});
});
$("#dTest").addEventListener("click",function(){testSend("default");});
$("#destClients").addEventListener("click",function(e){
  var s=e.target.closest(".ah-save"), t=e.target.closest(".ah-test");
  if(t){testSend(t.dataset.id);return;}
  if(!s) return;
  var id=s.dataset.id, b=D.routeBody(id,$("#rs-"+id).value,$("#re-"+id).value,$("#rf-"+id).value);
  postJson(API+"/route",b).then(function(r){
    if(!r.ok){note(esc((r.j&&r.j.error)||"The destination could not be saved."));return;}
    var c=(S.state.clients||[]).filter(function(x){return x.id===id;})[0];
    return load(true).then(function(){note("Saved the destination of "+esc(c?c.name:"the client")+". It applies from the next run.","pm-note");});
  }).catch(function(){note("The server could not be reached. Nothing was saved.");});
});

/* ── The filters ──────────────────────────────────────────── */
["#fP","#fClient","#fAgent","#fHeld"].forEach(function(sel){
  $(sel).addEventListener("change",function(){
    S.f={p:$("#fP").value,client:$("#fClient").value,agent:$("#fAgent").value,held:$("#fHeld").value};
    paintOpen();
  });
});

/* ── CSV ──────────────────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel */
$("#exportBtn").addEventListener("click",function(){
  var blob=new Blob(["﻿"+D.csvRows(S.state).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="alert-hub-"+new Date().toISOString().slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

load(false);
})();
