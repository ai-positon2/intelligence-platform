/* Master Agent (Paid Marketing Agents).
 *
 * The Client Master Config: a client is written down once and the other agents read it. The server keeps and checks the clients (tracker/paid_master.py); this page lists them, edits one and
 * says where an agent's own saved value differs. The wording and the shapes are master_agent_data.js. It reads and changes nothing in an ad account.
 */
(function(){
"use strict";

var D = window.MAData, API = "/p2/paid-marketing/api/master";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleDateString("en-US",{month:"short",day:"numeric"})+" ("+rel+")";
}

var S = {state:{clients:[],drift:[],changes:[],usedBy:{}}, accounts:[], metaProblem:"", form:null, sel:[], filter:"", confirm:false};

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
function nameOf(){var o={}; S.accounts.forEach(function(a){o[a.id]=a.name;}); return o;}

/* ── Loading ──────────────────────────────────────────────── */
function load(keep){
  if(!keep) show("skel");
  return Promise.all([getJson(API+"/state"),
    getJson("/p2/paid-marketing/api/segments/accounts"),
    getJson("/p2/paid-marketing/api/gads/accounts?platform=meta").catch(function(){return {ok:false,j:{}};})]).then(function(rs){
    var st=rs[0], g=rs[1], m=rs[2];
    if(!st.ok){show(keep?"out":"none"); setMsg(esc((st.j&&st.j.error)||("The clients could not be read (HTTP "+st.status+").")));return;}
    S.state=st.j;
    var google=(g.ok?(g.j.accounts||[]):[]).map(function(a){return {id:a.id,name:a.name,platform:"google"};});
    var meta=(m.ok?(m.j.accounts||[]).filter(function(a){return a.platform==="meta"||/^act_\d+$/.test(a.id);}):[]).map(function(a){return {id:a.id,name:a.name,platform:"meta"};});
    S.accounts=google.concat(meta);
    S.metaProblem=m.ok?"":"Meta ad accounts could not be listed, so only Google Ads accounts can be added.";
    setMsg(!g.ok?esc((g.j&&g.j.error)||"The Google Ads account list could not be read."):S.metaProblem?esc(S.metaProblem):"",!g.ok?"pm-err":"pm-note");
    show("out");
    try{render();}catch(e){setMsg("The clients were read but could not be drawn: "+esc(String(e&&e.message||e)));}
  }).catch(function(){show(keep?"out":"none"); setMsg("The server could not be reached. Try again.");});
}

/* ── Rendering ────────────────────────────────────────────── */
function render(){
  var st=S.state;
  $("#fresh").innerHTML='<span class="live"></span><b>'+D.plural((st.clients||[]).length,"client")+'</b>'+((st.changes||[])[0]?' · last change '+esc(ago(st.changes[0].at))+' by '+esc(st.changes[0].by):"");
  paintKpis(); paintList(); paintDrift(); paintFree(); paintChanges();
  $("#foot").innerHTML="Kept in the monitors' store. The master fills what an agent has not saved for itself: Brand Spend Alert (budget, brand terms), KPI Monitoring (targets, tolerance), the Ad Copy Checker (phone numbers, places, written name, allowed words), Location Conflicts (regions) and Unknown Domains (allowed domains). "+
    "Offers and notes are kept and no agent reads them yet. Nothing is changed in an ad account.";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.state,S.accounts).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintList(){
  var cs=S.state.clients||[], names=nameOf();
  $("#listNote").textContent=cs.length?"A client's figures are used by every agent that reads the master":"";
  $("#list").innerHTML=cs.length?cs.map(function(c){
    var ids=Object.keys(c.accounts||{}), shown=ids.slice(0,4).map(function(i){return esc(names[i]||i);}).join(", ")+(ids.length>4?" and "+(ids.length-4)+" more":"");
    return '<article class="bi-row clear" data-id="'+esc(c.id)+'"><div class="bi-head"><span class="bi-name"><b>'+esc(c.name)+'</b><span class="bi-sub">'+esc(D.clientSummary(c))+(ids.length?' · '+shown:"")+'</span></span>'+
      '<span class="bi-why"></span><button type="button" class="pm-btn-ghost ma-edit" data-id="'+esc(c.id)+'">Edit</button></div></article>';
  }).join(""):'<p class="pm-empty">No client yet. Add the first one: every agent will read it.</p>';
}
function paintDrift(){
  var rows=D.driftRows(S.state,nameOf());
  $("#driftCard").hidden=!rows.length;
  $("#drift").innerHTML=rows.length?'<div class="bi-tbl"><table><thead><tr><th>Client</th><th>Account</th><th>Agent</th><th>Field</th><th>Master</th><th>Agent saved</th></tr></thead><tbody>'+rows.map(function(r){
    return '<tr><td>'+esc(r.client)+'</td><td>'+esc(r.account)+'</td><td>'+esc(r.agent)+'</td><td><b>'+esc(r.field)+'</b></td><td>'+esc(r.master)+'</td><td>'+esc(r.saved)+'</td></tr>';
  }).join("")+'</tbody></table></div>':"";
}
function paintFree(){
  var free=D.unassigned(S.state,S.accounts);
  $("#freeCard").hidden=!free.length||!(S.state.clients||[]).length;
  $("#freeNote").textContent=D.plural(free.length,"account");
  $("#free").textContent=free.slice(0,40).map(function(a){return a.name+(a.platform==="meta"?" (Meta)":"");}).join(", ")+(free.length>40?" and "+(free.length-40)+" more":"");
}
function paintChanges(){
  var ch=S.state.changes||[];
  $("#changesCard").hidden=!ch.length;
  $("#changes").innerHTML=ch.map(function(c){return '<li>'+esc(ago(c.at))+' · '+esc(c.by)+' '+esc(c.what)+'</li>';}).join("");
}

/* ── The editor ───────────────────────────────────────────── */
function openEditor(client){
  S.form=D.formOf(client); S.sel=Object.keys((client&&client.accounts)||{}); S.filter=""; S.confirm=false;
  $("#editor").hidden=false; $("#edMsg").innerHTML=""; $("#edOut").textContent=""; $("#acctFilter").value="";
  $("#edTitle").textContent=client?"Edit "+client.name:"Add a client";
  $("#edSub").textContent=client?"saved changes apply to every agent that reads it":"";
  $("#fName").value=S.form.name; $("#fOffers").value=S.form.offers; $("#fNotes").value=S.form.notes;
  $("#removeBtn").hidden=!client; $("#removeBtn").textContent="Remove the client";
  paintShared(); paintAccounts();
}
function closeEditor(){S.form=null; S.sel=[]; $("#editor").hidden=true;}
function paintShared(){
  $("#shared").innerHTML=D.SHARED.map(function(f){
    return '<label class="cc-f"><span>'+esc(f.label)+' <em class="dim">('+esc(D.usedBy(f.key,S.state.usedBy))+')</em></span><textarea id="sh-'+f.key+'" rows="2" spellcheck="false" placeholder="'+esc(f.hint)+'">'+esc(S.form.shared[f.key])+'</textarea></label>';
  }).join("");
}
function paintAccounts(){
  var own=D.ownersOf(S.state), me=S.form&&S.form.id?(S.state.clients.filter(function(c){return c.id===S.form.id;})[0]||{}).name:"", q=S.filter.toLowerCase();
  var list=S.accounts.filter(function(a){return S.sel.indexOf(a.id)>=0||!q||a.name.toLowerCase().indexOf(q)>=0||a.id.indexOf(q)>=0;});
  $("#acctNote").textContent="("+S.sel.length+" chosen)";
  $("#acctList").innerHTML=list.length?list.map(function(a){
    var taken=own[a.id]&&own[a.id]!==me, on=S.sel.indexOf(a.id)>=0;
    return '<label class="ma-acct'+(taken?" taken":"")+'"><input type="checkbox" data-id="'+esc(a.id)+'"'+(on?" checked":"")+(taken?" disabled":"")+'> <span>'+esc(a.name)+'</span>'+(a.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+
      (taken?' <span class="bi-sub">in '+esc(own[a.id])+'</span>':"")+'</label>';
  }).join(""):'<p class="pm-empty">No account matches.</p>';
  var names=nameOf();
  $("#acctBlocks").innerHTML=S.sel.map(function(id){
    var a=S.form.accounts[id]||{}, p="ac-"+id+"-";
    function inp(k,label,w){return '<label class="cc-f ma-n"><span>'+esc(label)+'</span><input type="text" id="'+p+k+'" value="'+esc(a[k]||"")+'" spellcheck="false"></label>';}
    return '<fieldset class="ma-acctblock"><legend>'+esc(names[id]||id)+' <em class="dim">('+esc(D.usedBy("budget.monthly",S.state.usedBy))+'; targets: '+esc(D.usedBy("targets",S.state.usedBy)).toLowerCase()+')</em></legend>'+
      D.BUDGETS.map(function(b){return inp(b[0],b[1]);}).join("")+D.TARGETS.map(function(t){return inp(t[0],t[1]);}).join("")+inp("tolerance","Tolerance (% past a target before it is a finding)")+'</fieldset>';
  }).join("");
}
/* what is typed in the per-account boxes, kept before the boxes are drawn again */
function captureBlocks(){
  S.sel.forEach(function(id){
    var a=S.form.accounts[id]=S.form.accounts[id]||{};
    ["monthly","weeklyMin","weeklyMax","cpa","roas","cvr","ctr","tolerance"].forEach(function(k){var e=$("#ac-"+id+"-"+k); if(e) a[k]=e.value;});
  });
}
function readForm(){
  captureBlocks();
  S.form.name=$("#fName").value; S.form.offers=$("#fOffers").value; S.form.notes=$("#fNotes").value;
  D.SHARED.forEach(function(f){S.form.shared[f.key]=$("#sh-"+f.key).value;});
  return D.payload(S.form,S.sel);
}

$("#addBtn").addEventListener("click",function(){openEditor(null);});
$("#cancelBtn").addEventListener("click",closeEditor);
$("#list").addEventListener("click",function(e){
  var b=e.target.closest(".ma-edit"); if(!b) return;
  var c=(S.state.clients||[]).filter(function(x){return x.id===b.dataset.id;})[0];
  if(c) openEditor(c);
});
$("#acctList").addEventListener("click",function(e){
  var box=e.target.closest("input"); if(!box||!box.dataset.id) return;
  captureBlocks();
  var id=box.dataset.id, i=S.sel.indexOf(id);
  if(i>=0) S.sel.splice(i,1); else S.sel.push(id);
  paintAccounts();
});
$("#acctFilter").addEventListener("input",function(){captureBlocks(); S.filter=$("#acctFilter").value||""; paintAccounts();});

$("#saveBtn").addEventListener("click",function(){
  if(!S.form) return;
  var body={id:S.form.id,client:readForm(),names:nameOf()};
  $("#edMsg").innerHTML=""; $("#edOut").textContent="Saving…";
  postJson(API+"/client",body).then(function(r){
    if(!r.ok){$("#edOut").textContent=""; $("#edMsg").innerHTML='<div class="pm-err">'+esc((r.j&&r.j.error)||("The client could not be saved (HTTP "+r.status+")."))+'</div>'; return;}
    var name=r.j.client.name;
    closeEditor();
    return load(true).then(function(){setMsg("Saved "+esc(name)+". Every agent that reads the master uses it from its next read.","pm-note");});
  }).catch(function(){$("#edOut").textContent=""; $("#edMsg").innerHTML='<div class="pm-err">The server could not be reached. Nothing was saved.</div>';});
});
$("#removeBtn").addEventListener("click",function(){
  if(!S.form||!S.form.id) return;
  if(!S.confirm){S.confirm=true; $("#removeBtn").textContent="Click again to remove it for good"; return;}
  postJson(API+"/client/delete",{id:S.form.id}).then(function(r){
    if(!r.ok){$("#edMsg").innerHTML='<div class="pm-err">'+esc((r.j&&r.j.error)||"The client could not be removed.")+'</div>'; return;}
    var name=r.j.removed.name;
    closeEditor();
    return load(true).then(function(){setMsg("Removed "+esc(name)+". Its accounts are free again.","pm-note");});
  }).catch(function(){$("#edMsg").innerHTML='<div class="pm-err">The server could not be reached. Nothing was removed.</div>';});
});

/* ── CSV ──────────────────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel */
$("#exportBtn").addEventListener("click",function(){
  var blob=new Blob(["﻿"+D.csvRows(S.state,nameOf()).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="master-agent-"+new Date().toISOString().slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

load(false);
})();
