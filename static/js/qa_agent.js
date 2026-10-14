/* QA Agent (Paid Marketing Agents): the Campaign Launch QC checklist.
 *
 * Pick an account (the Performance Summarizer's account list), then a launch QC
 * for it or a new one. A new one asks what is launching (client type, campaign
 * types) and builds the list of checks that apply. Each check has three controls:
 *
 *   [tick]  done, Pass
 *   [x]     an error: opens a comment box beside it and a reason is required
 *   [N/A]   does not apply to this launch
 *
 * Every change is saved at once (PUT .../checks/<id>) and the server answers with
 * the new verdict, which is what the banner shows: this page never works out
 * "ready" for itself. The rules are tracker/paid_qa.py.
 */
(function(){
"use strict";

var BASE = "/p2/paid-marketing", API = BASE + "/api/qa";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function who(email){return String(email||"").split("@")[0]||"someone";}
function rel(iso){
  var s=(Date.now()-new Date(iso).getTime())/1000; if(!isFinite(s)) return "";
  if(s<60) return "just now"; if(s<3600) return Math.round(s/60)+" min ago";
  if(s<86400) return Math.round(s/3600)+" h ago"; return Math.round(s/86400)+" d ago";
}
function longDate(s){return s?new Date(s+"T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"}):"";}

var root = $("#qa");
var S = {list:null, items:{}, accounts:[], account:null, launches:[], launchId:"", launch:null, checks:{}, summary:null,
         fStatus:"all", fSev:"all", closed:{}, drafts:{}, rowMsg:{}, inflight:{}, queued:{}, timers:{},
         formMode:"new", selClient:[], selCamp:[], seq:0};

/* ── Requests ─────────────────────────────────────────────── */
function call(method, url, body){
  var o={method:method,credentials:"same-origin"};
  if(method!=="GET"){o.headers={"Content-Type":"application/json"};o.body=JSON.stringify(body||{});}
  return fetch(url,o).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function say(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function view(which){
  $("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel";
  $("#form").hidden=which!=="form"; $("#work").hidden=which!=="work";
}
function fail(r,fallback){return esc((r.j&&r.j.error)||fallback||("Something went wrong (HTTP "+r.status+")."));}

/* ── Account ──────────────────────────────────────────────── */
var picker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){pickAccount(v);}});
function setAccounts(list){
  S.accounts=list;
  var items=[{value:"",label:"Choose an account",sub:list.length+" accounts",avatar:"?",hue:200}];
  list.forEach(function(a){items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),sub:dashed(a.id)+(a.currency?" · "+a.currency:"")});});
  picker.set(items,S.account?S.account.id:"");
}
function pickAccount(id){
  var a=S.accounts.filter(function(x){return x.id===id;})[0];
  if(!a){S.account=null;S.launchId="";S.launches=[];$("#launches").hidden=true;view("empty");sync();return;}
  S.account={id:a.id,name:a.name}; S.launchId=""; try{localStorage.setItem("qa_account",a.id);}catch(e){}
  loadLaunches(true);
}
$("#mGo").addEventListener("click",function(){
  var id=$("#mId").value.replace(/\D/g,""), name=$("#mName").value.trim();
  if(id.length!==10) return say("The Google Ads customer ID should be 10 digits.");
  if(!name) return say("Add the account name.");
  say(""); S.account={id:id,name:name}; S.launchId=""; loadLaunches(true);
});

/* ── Launches for the account ─────────────────────────────── */
function loadLaunches(openFirst){
  var seq=++S.seq; say(""); view("skel"); $("#launches").hidden=true;
  call("GET",API+"/launches?account="+S.account.id).then(function(r){
    if(seq!==S.seq) return;
    if(!r.ok){view("empty");return say(fail(r,"The launch QCs could not be loaded."));}
    S.launches=r.j.launches||[]; paintLaunchChips(); $("#launches").hidden=false;
    var want=S.launchId&&S.launches.some(function(l){return l.id===S.launchId;})?S.launchId:(openFirst&&S.launches[0]?S.launches[0].id:"");
    if(want) openLaunch(want); else{ view("empty"); showForm("new"); }
  }).catch(function(){ if(seq===S.seq){view("empty");say("The server could not be reached. Try again.");} });
}
function paintLaunchChips(){
  var h=S.launches.map(function(l){
    var v=l.summary.verdict.code;
    return '<button type="button" class="pm-chip'+(l.id===S.launchId?" on":"")+'" data-l="'+l.id+'" aria-pressed="'+(l.id===S.launchId)+'">'+
      '<span class="qa-dot '+v+'"></span>'+esc(l.name)+' <span class="n">'+l.summary.percent+'%</span></button>';
  }).join("");
  $("#launchChips").innerHTML=h+'<button type="button" class="pm-chip qa-new" data-new="1">+ New launch QC</button>';
}
$("#launchChips").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return;
  if(b.dataset.new) return showForm("new");
  openLaunch(b.dataset.l);
});

function openLaunch(id){
  var seq=++S.seq; S.launchId=id; paintLaunchChips(); say(""); view("skel");
  call("GET",API+"/launches/"+id).then(function(r){
    if(seq!==S.seq) return;
    if(!r.ok){view("empty");return say(fail(r,"That launch QC could not be opened."));}
    load(r.j); view("work"); render(); sync();
  }).catch(function(){ if(seq===S.seq){view("empty");say("The server could not be reached. Try again.");} });
}
function load(j){S.launch=j.launch;S.checks=j.checks||{};S.summary=j.summary;S.drafts={};S.rowMsg={};S.fStatus="all";S.fSev="all";}
function sync(){
  try{var q=S.account?"?account="+S.account.id+(S.launchId?"&launch="+S.launchId:""):"";history.replaceState(null,"",location.pathname+q);}catch(e){}
}

/* ── New launch / edit details ────────────────────────────── */
function chips(host, all, sel, onToggle){
  $(host).innerHTML=all.map(function(t){
    var on=sel.indexOf(t)>=0; return '<button type="button" class="pm-chip'+(on?" on":"")+'" data-t="'+esc(t)+'" aria-pressed="'+on+'">'+esc(t)+'</button>';
  }).join("");
}
function paintFormChips(){chips("#fClient",S.list.clientTypes,S.selClient);chips("#fCamp",S.list.campaignTypes,S.selCamp);}
function toggle(arr,v){var i=arr.indexOf(v); if(i>=0) arr.splice(i,1); else arr.push(v);}
$("#fClient").addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;toggle(S.selClient,b.dataset.t);paintFormChips();preview();});
$("#fCamp").addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;toggle(S.selCamp,b.dataset.t);paintFormChips();preview();});
var pvTimer=null;
function preview(){
  clearTimeout(pvTimer);
  pvTimer=setTimeout(function(){
    var q="?clientTypes="+encodeURIComponent(S.selClient.join(","))+"&campaignTypes="+encodeURIComponent(S.selCamp.join(","));
    call("GET",API+"/applicable"+q).then(function(r){
      var el=$("#fPreview"); if(!r.ok||!el) return;
      var p=r.j, none=!S.selClient.length&&!S.selCamp.length;
      el.innerHTML=(none?"Nothing picked yet, so all ":"This builds a checklist of ")+'<b>'+p.count+' checks</b>: '+
        p.bySeverity.Critical+' Critical, '+p.bySeverity.Major+' Major, '+p.bySeverity.Minor+' Minor.'+
        (none?" Pick the client type and campaign types to narrow it to this launch.":"");
    });
  },200);
}
function showForm(mode){
  S.formMode=mode; var L=S.launch, edit=mode==="edit"&&L;
  $("#formTitle").textContent=edit?"Edit launch details":"New launch QC";
  $("#formAcct").textContent=S.account.name+" · "+dashed(S.account.id);
  $("#fName").value=edit?L.name:""; $("#fDate").value=edit?L.plannedDate:"";
  $("#fQc").value=edit?L.qcBy:(root.dataset.user||root.dataset.email||""); $("#fAppr").value=edit?L.approvedBy:"";
  S.selClient=edit?L.clientTypes.slice():[]; S.selCamp=edit?L.campaignTypes.slice():[];
  $("#fSave").textContent=edit?"Save details":"Build the checklist"; $("#fWarn").hidden=!edit;
  $("#fCancel").hidden=!S.launch&&!S.launches.length; say("");
  paintFormChips(); view("form"); preview(); setTimeout(function(){$("#fName").focus();},30);
}
$("#fCancel").addEventListener("click",function(){ if(S.launch&&S.launchId) view("work"); else view("empty"); });
$("#fSave").addEventListener("click",function(){
  var body={name:$("#fName").value.trim(),clientTypes:S.selClient,campaignTypes:S.selCamp,plannedDate:$("#fDate").value,
            qcBy:$("#fQc").value.trim(),approvedBy:$("#fAppr").value.trim()};
  if(!body.name){say("Say which campaign(s) are launching.");return $("#fName").focus();}
  var btn=$("#fSave"); btn.disabled=true; say("");
  var req=S.formMode==="edit"&&S.launchId
    ? call("PUT",API+"/launches/"+S.launchId,body)
    : call("POST",API+"/launches",Object.assign({accountId:S.account.id,accountName:S.account.name},body));
  req.then(function(r){
    btn.disabled=false;
    if(!r.ok) return say(fail(r,"That could not be saved."));
    S.launchId=r.j.launch.id; load(r.j);
    if(S.formMode==="edit"){ S.fStatus="all"; }
    view("work"); render(); sync();
    call("GET",API+"/launches?account="+S.account.id).then(function(x){ if(x.ok){S.launches=x.j.launches;paintLaunchChips();$("#launches").hidden=false;} });
  }).catch(function(){btn.disabled=false;say("The server could not be reached. Try again.");});
});

/* ── The checklist ────────────────────────────────────────── */
var VERDICT_SUB = {
  ready:function(s){return "Every Critical check is passed or N/A."+(s.pending?" "+s.pending+" Major or Minor check"+(s.pending===1?" is":"s are")+" still open: fix Major ones before launch, or within 24 hours with a note.":"");},
  incomplete:function(s){return s.pendingCritical.length+" Critical check"+(s.pendingCritical.length===1?" is":"s are")+" still to do. Launch only when they are all passed.";},
  not_ready:function(s){return s.failedCritical.length+" Critical check"+(s.failedCritical.length===1?" has":"s have")+" failed ("+s.failedCritical.join(", ")+"). Do not launch until "+(s.failedCritical.length===1?"it is":"they are")+" fixed and passed.";}
};
function rowState(id){
  // An unsaved error ("draft") wins over a saved pass or N/A: it is what the reviewer is doing now,
  // and the saved tick stands until the reason is written. A saved error stays an error while its text is edited.
  var c=S.checks[id];
  if(S.drafts.hasOwnProperty(id)&&(!c||c.status!=="fail")) return "draft";
  return c?c.status:"todo";
}
function paintHead(){
  var L=S.launch, bits=[];
  bits.push(esc(S.account.name)+" · "+dashed(L.accountId));
  if(L.campaignTypes.length) bits.push(esc(L.campaignTypes.join(", ")));
  if(L.clientTypes.length) bits.push(esc(L.clientTypes.join(", ")));
  if(L.plannedDate) bits.push("launching "+esc(longDate(L.plannedDate)));
  if(L.qcBy) bits.push("QC by "+esc(L.qcBy));
  if(L.approvedBy) bits.push("approved by "+esc(L.approvedBy));
  $("#wName").textContent=L.name; $("#wMeta").innerHTML=bits.join('<span class="sep">·</span>');
}
function paintVerdict(){
  var s=S.summary, v=s.verdict, sev=s.bySeverity;
  var stat=function(name){var b=sev[name];return '<span class="qa-stat"><b>'+(b.pass+b.na)+'/'+b.total+'</b> '+name+' passed</span>';};
  $("#verdict").className="qa-verdict "+v.code;
  $("#verdict").innerHTML=
    '<div class="qa-v-main"><span class="qa-v-ic">'+(v.code==="ready"?'<svg viewBox="0 0 24 24"><path d="M5 12l4 4 10-10"/></svg>':v.code==="not_ready"?'<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>':'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>')+'</span>'+
    '<div><div class="qa-v-t">'+esc(v.text)+'</div><div class="qa-v-s">'+esc(VERDICT_SUB[v.code](s))+'</div></div></div>'+
    '<div class="qa-v-bar"><i style="width:'+s.percent+'%"></i></div>'+
    '<div class="qa-v-stats"><span class="qa-stat big"><b>'+s.checked+'/'+s.total+'</b> checked ('+s.percent+'%)</span>'+stat("Critical")+stat("Major")+stat("Minor")+
    (s.failed.length?'<span class="qa-stat bad"><b>'+s.failed.length+'</b> failed</span>':"")+'</div>';
}
function paintFilters(){
  var ids=S.summary.applicable, n={all:ids.length,todo:0,pass:0,fail:0,na:0};
  ids.forEach(function(id){var st=rowState(id); if(st==="todo"||st==="draft"){n.todo++;} if(st==="draft"){n.fail++;} if(n[st]!=null&&st!=="todo")n[st]++;});
  var st=[["all","All"],["todo","To do"],["pass","Passed"],["fail","Errors"],["na","N/A"]];
  $("#fStatus").innerHTML=st.map(function(x){return '<button type="button" class="pm-chip'+(S.fStatus===x[0]?" on":"")+'" data-f="'+x[0]+'" aria-pressed="'+(S.fStatus===x[0])+'">'+x[1]+' <span class="n">'+n[x[0]]+'</span></button>';}).join("");
  var sv=[["all","All"],["Critical","Critical"],["Major","Major"],["Minor","Minor"]];
  $("#fSev").innerHTML=sv.map(function(x){return '<button type="button" class="pm-chip'+(S.fSev===x[0]?" on":"")+'" data-s="'+x[0]+'" aria-pressed="'+(S.fSev===x[0])+'">'+x[1]+'</button>';}).join("");
}
$("#fStatus").addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;S.fStatus=b.dataset.f;paintFilters();paintSections();});
$("#fSev").addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;S.fSev=b.dataset.s;paintFilters();paintSections();});

function tag(t,cls){return '<span class="qa-tag '+(cls||"")+'">'+esc(t)+'</span>';}
function rowHtml(it){
  var id=it.id, c=S.checks[id], st=rowState(id), draft=S.drafts[id];
  var pass=st==="pass", isFail=st==="fail"||st==="draft", na=st==="na";
  var note=isFail?(draft!=null?draft:(c?c.note:"")):(c&&c.note?c.note:"");
  var tags="";
  if(it.campaignTypes[0]!=="All") tags+=tag(it.campaignTypes.join(" · "));
  if(it.clientTypes[0]!=="All") tags+=tag(it.clientTypes.join(" · "),"cl");
  if(it.condition) tags+=tag("If "+it.condition,"cond");
  var msg=S.rowMsg[id]||"";
  return '<div class="qa-row st-'+(st==="draft"?"fail draft":st)+'" data-id="'+esc(id)+'" id="chk-'+esc(id)+'">'+
    '<div class="qa-ctl">'+
      '<button type="button" class="qa-box" role="checkbox" aria-checked="'+pass+'" data-act="pass" title="'+(pass?"Done. Click to untick":"Tick when done")+'" aria-label="Done: '+esc(it.check)+'"><svg viewBox="0 0 24 24"><path d="M5 12l4 4 10-10"/></svg></button>'+
      '<button type="button" class="qa-x" aria-pressed="'+isFail+'" data-act="fail" title="Mark an error and say why" aria-label="Error: '+esc(it.check)+'"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>'+
      '<button type="button" class="qa-na" aria-pressed="'+na+'" data-act="na" title="Does not apply to this launch">N/A</button>'+
    '</div>'+
    '<div class="qa-main">'+
      '<div class="qa-line"><span class="qa-id">'+esc(id)+'</span><b class="qa-check">'+esc(it.check)+'</b>'+
        '<span class="qa-sev '+it.severity.toLowerCase()+'">'+it.severity+'</span>'+tags+'</div>'+
      '<p class="qa-verify">'+esc(it.verify)+'</p>'+
      (isFail?'<div class="qa-why"><textarea rows="2" maxlength="2000" data-why="'+esc(id)+'" placeholder="What failed, where (campaign / ad group), and what needs fixing?" aria-label="Reason for the error on '+esc(it.check)+'">'+esc(note)+'</textarea></div>':
        (c&&c.note?'<p class="qa-note">'+esc(c.note)+'</p>':""))+
      '<div class="qa-foot"><span class="qa-st">'+esc(msg)+'</span>'+
        (c?'<span class="qa-by" title="'+esc(c.by||"")+'">'+(st==="fail"?"Error":st==="na"?"N/A":"Done")+' by '+esc(who(c.by))+' · '+esc(rel(c.at))+'</span>':"")+'</div>'+
    '</div></div>';
}
function paintSections(){
  var s=S.summary, ok={}; s.applicable.forEach(function(id){ok[id]=1;});
  var by={}; s.bySection.forEach(function(b){by[b.section]=b;});
  var html="", shown=0;
  S.list.sections.forEach(function(sec){
    var b=by[sec]; if(!b) return;
    var rows=S.list.items.filter(function(it){
      if(it.section!==sec||!ok[it.id]) return false;
      if(S.fSev!=="all"&&it.severity!==S.fSev) return false;
      var st=rowState(it.id);
      if(S.fStatus==="todo") return st==="todo"||st==="draft";
      if(S.fStatus==="fail") return st==="fail"||st==="draft";
      if(S.fStatus!=="all") return st===S.fStatus;
      return true;
    });
    if(!rows.length) return; shown+=rows.length;
    var done=b.total-b.pending, closed=!!S.closed[sec], pct=b.total?Math.round(100*done/b.total):0;
    html+='<section class="pm-panel qa-sec'+(closed?" closed":"")+'" data-sec="'+esc(sec)+'">'+
      '<button type="button" class="qa-sec-h" data-toggle="'+esc(sec)+'" aria-expanded="'+(!closed)+'">'+
        '<span class="qa-chev"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span><span class="qa-sec-t">'+esc(sec)+'</span>'+
        (b.fail?'<span class="qa-pill bad">'+b.fail+' error'+(b.fail===1?"":"s")+'</span>':"")+
        '<span class="qa-sec-n"><b>'+done+'</b> of '+b.total+' checked</span><span class="qa-mini"><i style="width:'+pct+'%"></i></span></button>'+
      '<div class="qa-rows">'+rows.map(rowHtml).join("")+'</div></section>';
  });
  $("#sections").innerHTML=html||'<div class="pm-panel"><p class="pm-empty">'+(S.fStatus==="all"&&S.fSev==="all"?"No checks apply to this launch.":"Nothing matches this filter.")+'</p></div>';
}
function render(){
  paintHead(); paintVerdict(); paintFilters(); paintSections();
  $("#foot").innerHTML="Source: the PPC team's Campaign Launch QC Checklist. <b>Critical</b> checks are launch blockers; <b>Major</b> should be fixed before launch, "+
    "or within 24 hours with a note; <b>Minor</b> is best practice. Each tick is saved as you make it, with who made it and when.";
}
function refresh(id){
  // After one check changes: repaint that row, the verdict, the section headers and the filter counts.
  var el=document.getElementById("chk-"+id), it=S.items[id], ae=document.activeElement;
  var keep=ae&&ae.matches&&ae.matches("textarea[data-why]")&&ae.dataset.why===id?{s:ae.selectionStart,e:ae.selectionEnd}:null;
  if(el&&it){var t=document.createElement("div");t.innerHTML=rowHtml(it);el.replaceWith(t.firstChild);}
  if(keep){var ta=document.querySelector('textarea[data-why="'+CSS.escape(id)+'"]'); if(ta){ta.focus();try{ta.setSelectionRange(keep.s,keep.e);}catch(e){}}}
  paintVerdict(); paintFilters(); paintHeads();
}
function paintHeads(){
  var by={}; S.summary.bySection.forEach(function(b){by[b.section]=b;});
  document.querySelectorAll(".qa-sec").forEach(function(sec){
    var b=by[sec.dataset.sec]; if(!b) return;
    var h=sec.querySelector(".qa-sec-h"), done=b.total-b.pending;
    h.querySelector(".qa-sec-n").innerHTML="<b>"+done+"</b> of "+b.total+" checked";
    h.querySelector(".qa-mini i").style.width=(b.total?Math.round(100*done/b.total):0)+"%";
    var pill=h.querySelector(".qa-pill"), t=b.fail?b.fail+" error"+(b.fail===1?"":"s"):"";
    if(t&&!pill){pill=document.createElement("span");pill.className="qa-pill bad";h.insertBefore(pill,h.querySelector(".qa-sec-n"));}
    if(pill){ if(t) pill.textContent=t; else pill.remove(); }
  });
}

/* ── Ticking ──────────────────────────────────────────────── */
function save(id,status,note){
  if(S.inflight[id]){S.queued[id]={status:status,note:note};return;}      // latest wins, after the one in flight
  S.inflight[id]=true; S.rowMsg[id]="Saving…"; setMsg(id);
  call("PUT",API+"/launches/"+S.launchId+"/checks/"+encodeURIComponent(id),{status:status,note:note||""}).then(function(r){
    S.inflight[id]=false; var q=S.queued[id]; delete S.queued[id];
    if(!r.ok){
      S.rowMsg[id]=(r.j&&r.j.error)||"That could not be saved."; setMsg(id,true);
      if(q) save(id,q.status,q.note); return;
    }
    if(r.j.check){S.checks[id]=r.j.check;} else {delete S.checks[id];}
    S.summary=r.j.summary; S.rowMsg[id]="Saved";
    if(S.drafts[id]==null||S.drafts[id].trim()===(note||"")) delete S.drafts[id];       // keep anything typed since
    if(q){ refresh(id); return save(id,q.status,q.note); }
    refresh(id); paintLaunchPercent();
    setTimeout(function(){ if(S.rowMsg[id]==="Saved"){S.rowMsg[id]="";setMsg(id);} },1800);
  }).catch(function(){
    S.inflight[id]=false; S.rowMsg[id]="The server could not be reached. Try again."; setMsg(id,true);
  });
}
function setMsg(id,bad){
  var el=document.querySelector("#chk-"+CSS.escape(id)+" .qa-st"); if(!el) return;
  el.textContent=S.rowMsg[id]||""; el.classList.toggle("bad",!!bad);
}
function paintLaunchPercent(){
  var l=S.launches.filter(function(x){return x.id===S.launchId;})[0]; if(!l) return;
  l.summary={verdict:S.summary.verdict,percent:S.summary.percent,checked:S.summary.checked,total:S.summary.total,
             failedCount:S.summary.failed.length,failedCritical:S.summary.failedCritical.length};
  paintLaunchChips();
}
function saveFail(id){
  var note=(S.drafts[id]!=null?S.drafts[id]:(S.checks[id]?S.checks[id].note:"")).trim();
  if(note.length<3){ S.rowMsg[id]="Add the reason to record this error."; setMsg(id,true); return; }
  save(id,"fail",note);
}
$("#sections").addEventListener("click",function(e){
  var head=e.target.closest("[data-toggle]");
  if(head){var k=head.dataset.toggle; S.closed[k]=!S.closed[k]; return paintSections();}
  var b=e.target.closest("[data-act]"); if(!b) return;
  var id=b.closest(".qa-row").dataset.id, st=rowState(id), act=b.dataset.act;
  if(act==="pass"){ delete S.drafts[id]; save(id,st==="pass"?null:"pass",""); }
  else if(act==="na"){ delete S.drafts[id]; save(id,st==="na"?null:"na",""); }
  else if(act==="fail"){
    if(st==="fail"||st==="draft"){ delete S.drafts[id]; if(st==="fail") save(id,null,""); else refresh(id); }
    else{ S.drafts[id]=""; S.rowMsg[id]=""; refresh(id);
      var ta=document.querySelector('textarea[data-why="'+CSS.escape(id)+'"]'); if(ta) ta.focus(); }
  }
});
$("#sections").addEventListener("input",function(e){
  var ta=e.target.closest("textarea[data-why]"); if(!ta) return; var id=ta.dataset.why;
  S.drafts[id]=ta.value; S.rowMsg[id]=""; setMsg(id);
  clearTimeout(S.timers[id]); S.timers[id]=setTimeout(function(){ if(ta.value.trim().length>=3) saveFail(id); },900);
});
$("#sections").addEventListener("focusout",function(e){
  var ta=e.target.closest("textarea[data-why]"); if(!ta) return; var id=ta.dataset.why;
  clearTimeout(S.timers[id]);
  var saved=S.checks[id]&&S.checks[id].status==="fail"&&S.checks[id].note===ta.value.trim();
  if(!saved&&S.drafts[id]!=null) saveFail(id);
});

/* ── Header actions ───────────────────────────────────────── */
$("#editBtn").addEventListener("click",function(){showForm("edit");});
$("#removeBtn").addEventListener("click",function(){
  if(!S.launch||!confirm('Remove the launch QC "'+S.launch.name+'"? It disappears from this list; its ticks are kept in the records.')) return;
  call("DELETE",API+"/launches/"+S.launchId,{}).then(function(r){
    if(!r.ok) return say(fail(r,"That could not be removed."));
    S.launchId=""; S.launch=null; loadLaunches(true);
  });
});
$("#copyBtn").addEventListener("click",function(){
  var s=S.summary, L=S.launch, lines=["Launch QC: "+L.name+" | "+S.account.name+" ("+dashed(L.accountId)+")","Verdict: "+s.verdict.text,
    "Checked: "+s.checked+"/"+s.total+" checks | Critical passed "+(s.bySeverity.Critical.pass+s.bySeverity.Critical.na)+"/"+s.bySeverity.Critical.total];
  if(s.failed.length){lines.push("","Errors:"); s.failed.forEach(function(id){var it=S.items[id],c=S.checks[id]||{};
    lines.push("- "+id+" "+it.check+" ("+it.severity+"): "+(c.note||""));});}
  if(s.pendingCritical.length){lines.push("","Critical checks still to do: "+s.pendingCritical.join(", "));}
  var text=lines.join("\n"), label=$("#copyBtn span");
  var done=function(){label.textContent="Copied";setTimeout(function(){label.textContent="Copy summary";},1600);};
  if(navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done,function(){say("Copying was blocked by the browser.");});
  else{var t=document.createElement("textarea");t.value=text;document.body.appendChild(t);t.select();try{document.execCommand("copy");done();}catch(e){say("Copying was blocked by the browser.");}t.remove();}
});

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search), want=p.get("account")||"", launch=p.get("launch")||"";
  if(!want){try{want=localStorage.getItem("qa_account")||"";}catch(e){}}
  view("empty"); picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  Promise.all([call("GET",API+"/checklist"),call("GET",BASE+"/api/gads/accounts")]).then(function(rs){
    if(!rs[0].ok){return say(fail(rs[0],"The checklist could not be loaded."));}
    S.list=rs[0].j; S.list.items.forEach(function(it){S.items[it.id]=it;});
    var a=rs[1];
    if(a.ok&&a.j.accounts&&a.j.accounts.length){
      S.accounts=a.j.accounts;
      if(want){var hit=S.accounts.filter(function(x){return x.id===want;})[0]; if(hit){S.account={id:hit.id,name:hit.name};S.launchId=launch;}}
      setAccounts(S.accounts);
      if(S.account) loadLaunches(true);
    }else{
      // The account list is read from Google Ads, which may be rate limited or not connected:
      // the QC does not need it, so the account can be typed in.
      picker.set([{value:"",label:"Accounts unavailable",sub:"type it in instead",avatar:"!",hue:20}],"",true);
      $("#acctPick").hidden=true; $("#manual").hidden=false;
      say(esc((a.j&&a.j.error)||"The account list could not be loaded from Google Ads.")+" You can still run a QC: type the account name and its Google Ads customer ID below.","pm-note");
      if(want&&/^\d{10}$/.test(want)){ $("#mId").value=want; }
    }
  }).catch(function(){say("The server could not be reached. Reload to try again.");});
})();
})();
