/* QA Agent (Paid Marketing Agents): the Campaign Launch QC, run on a live campaign.
 *
 * Pick an account (the Performance Summarizer's account list), then one of its
 * live campaigns: the QC starts on the shared checklist, no builder. Each check is
 * a row:
 *
 *   [tick] [x]   right or wrong (the cross needs a reason)
 *   what the agent found, printed (for the checks Google Ads can answer)
 *   a comment box beside it
 *
 * The agent ticks or crosses a box itself only when the answer is objective; a
 * person's click always wins, and clicking it again returns the box to the agent's
 * reading. Every change is saved at once and the server answers with the new
 * verdict, which is what the banner shows: this page never works out "ready" for
 * itself. The rules are tracker/paid_qa.py, the reading tracker/paid_qa_auto.py.
 */
(function(){
"use strict";

var BASE = "/p2/paid-marketing", API = BASE + "/api/qa";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,_-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function who(by){return by==="QA Agent"?"the QA Agent":(String(by||"").split("@")[0]||"someone");}
function rel(iso){
  var s=(Date.now()-new Date(iso).getTime())/1000; if(!isFinite(s)) return "";
  if(s<60) return "just now"; if(s<3600) return Math.round(s/60)+" min ago";
  if(s<86400) return Math.round(s/3600)+" h ago"; return Math.round(s/86400)+" d ago";
}
var VNAME = {ready:"Ready",incomplete:"Incomplete",not_ready:"Not ready"};

var root = $("#qa");
var S = {list:null, items:{}, accounts:[], account:null, campaigns:[], campaignId:"", launchId:"", launch:null,
         checks:{}, summary:null, needs:null, fStatus:"all", fSev:"all", closed:{}, text:{}, pend:{}, rowMsg:{},
         inflight:{}, queued:{}, timers:{}, seq:0, manual:false, running:false};

/* ── Requests ─────────────────────────────────────────────── */
function call(method, url, body){
  var o={method:method,credentials:"same-origin"};
  if(method!=="GET"){o.headers={"Content-Type":"application/json"};o.body=JSON.stringify(body||{});}
  return fetch(url,o).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function say(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function view(which,title,text){
  $("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#work").hidden=which!=="work";
  if(which==="empty"){ $("#emptyT").textContent=title||"Choose an account to begin";
    $("#emptyP").textContent=text||"Then pick one of its live campaigns. The checklist is the PPC team's Campaign Launch QC: 155 checks across account setup, tracking, campaign settings, ads, assets, policy and the final pre-launch review."; }
}
function fail(r,fallback){return esc((r.j&&r.j.error)||fallback||("Something went wrong (HTTP "+r.status+")."));}

/* ── Account, then campaign ───────────────────────────────── */
var acctPicker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){pickAccount(v);}});
var campPicker = window.PMPicker($("#campPick"),{label:"Live campaign",search:true,onChange:function(v){pickCampaign(v);}});
function setAccounts(list){
  S.accounts=list;
  var items=[{value:"",label:"Choose an account",sub:list.length+" accounts",avatar:"?",hue:200}];
  list.forEach(function(a){items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),sub:dashed(a.id)+(a.currency?" · "+a.currency:"")});});
  acctPicker.set(items,S.account?S.account.id:"");
}
function campaignsOff(label,sub){campPicker.set([{value:"",label:label,sub:sub||"",avatar:"…",hue:200}],"",true);}
function pickAccount(id){
  var a=S.accounts.filter(function(x){return x.id===id;})[0];
  S.campaignId=""; S.launchId=""; S.launch=null; S.campaigns=[];
  if(!a){S.account=null; campaignsOff("Choose an account first"); view("empty"); sync(); return;}
  S.account={id:a.id,name:a.name}; try{localStorage.setItem("qa_account",a.id);}catch(e){}
  loadCampaigns();
}
function badgeFor(q){
  return q?'<span class="qa-dot '+q.verdict.code+'" title="'+esc(VNAME[q.verdict.code]||"")+'"></span>':"";
}
function loadCampaigns(keep){
  var seq=++S.seq; say(""); view("skel"); campaignsOff("Loading live campaigns…");
  call("GET",API+"/campaigns?account="+S.account.id).then(function(r){
    if(seq!==S.seq) return;
    if(!r.ok){
      view("empty","The campaigns could not be loaded",(r.j&&r.j.error)||"Google Ads could not be read for this account.");
      campaignsOff("Campaigns unavailable","Google Ads could not be read");
      return say(fail(r,"The campaigns could not be loaded.")+" You can still run a QC: <a href=\"#\" id=\"goManual\">type the campaign in</a>.");
    }
    S.campaigns=r.j.campaigns||[]; paintCampaigns(); sync();
    if(keep&&S.campaignId) return;
    if(!S.campaigns.length) return view("empty","No live campaigns","This account has no enabled campaign to QC. Enable the campaign you are launching, or pick another account.");
    view("empty","Choose a campaign","The QC starts as soon as you pick one.");
    if(S.wantCampaign&&S.campaigns.some(function(c){return c.id===S.wantCampaign;})){var w=S.wantCampaign; S.wantCampaign=""; pickCampaign(w);}
  }).catch(function(){ if(seq===S.seq){view("empty");say("The server could not be reached. Try again.");} });
}
function paintCampaigns(){
  var items=[{value:"",label:"Choose a campaign",sub:S.campaigns.length+" live",avatar:"?",hue:200}];
  S.campaigns.forEach(function(c){
    items.push({value:c.id,label:c.name,avatar:inits(c.name),hue:hueOf(c.name),badge:badgeFor(c.qc),
      sub:(c.type||"Campaign")+(c.qc?" · QC "+c.qc.percent+"% · "+(VNAME[c.qc.verdict.code]||""):"")});
  });
  campPicker.set(items,S.campaignId);
}
function pickCampaign(id){
  var c=S.campaigns.filter(function(x){return x.id===id;})[0];
  if(!c){S.campaignId="";S.launchId="";S.launch=null;view("empty","Choose a campaign","The QC starts as soon as you pick one.");sync();return;}
  S.campaignId=c.id; start({campaignId:c.id,campaignName:c.name,channel:c.channel});
}
function start(c){
  var seq=++S.seq; say(""); view("skel");
  call("POST",API+"/start",{accountId:S.account.id,accountName:S.account.name,campaignId:c.campaignId,campaignName:c.campaignName,channel:c.channel||""}).then(function(r){
    if(seq!==S.seq) return;
    if(!r.ok){view("empty");return say(fail(r,"The QC could not be started."));}
    load(r.j); view("work"); render(); sync(); refreshCampaignList();
  }).catch(function(){ if(seq===S.seq){view("empty");say("The server could not be reached. Try again.");} });
}
function refreshCampaignList(){
  if(S.manual||!S.account) return;
  call("GET",API+"/campaigns?account="+S.account.id).then(function(r){ if(r.ok){S.campaigns=r.j.campaigns||[];paintCampaigns();} });
}
function load(j){
  S.launch=j.launch; S.launchId=j.launch.id; S.campaignId=j.launch.campaignId; S.checks=j.checks||{}; S.summary=j.summary; S.needs=j.needsClientType;
  S.text={}; S.pend={}; S.rowMsg={}; S.fStatus="all"; S.fSev="all";
  S.readError=j.readError||null;
}
function sync(){
  try{var q=S.account?"?account="+S.account.id+(S.campaignId?"&campaign="+S.campaignId:""):"";history.replaceState(null,"",location.pathname+q);}catch(e){}
}
/* The account list is read from Google Ads, which may be rate limited or not connected. */
function manualMode(why){
  S.manual=true; $("#pickers").hidden=true; $("#manual").hidden=false;
  say(esc(why||"The account list could not be loaded from Google Ads.")+" You can still run a QC: type the account and campaign in, and every check can be done by hand.","pm-note");
}
$("#mGo").addEventListener("click",function(){
  var id=$("#mId").value.replace(/\D/g,""), name=$("#mName").value.trim(), cid=$("#mCampId").value.replace(/\D/g,""), cname=$("#mCamp").value.trim();
  if(id.length!==10) return say("The Google Ads customer ID should be 10 digits.");
  if(!name) return say("Add the account name.");
  if(!cid) return say("Add the campaign ID (digits only).");
  if(!cname) return say("Add the campaign name.");
  S.account={id:id,name:name}; start({campaignId:cid,campaignName:cname,channel:$("#mType").value});
});
document.addEventListener("click",function(e){ if(e.target&&e.target.id==="goManual"){e.preventDefault();manualMode("Google Ads could not be read.");
  if(S.account){$("#mId").value=S.account.id;$("#mName").value=S.account.name;} } });

/* ── The QC ───────────────────────────────────────────────── */
var VERDICT_SUB = {
  ready:function(s){return "Every Critical check is right or N/A."+(s.pending?" "+s.pending+" Major or Minor check"+(s.pending===1?" is":"s are")+" still open: fix Major ones before launch, or within 24 hours with a note.":"");},
  incomplete:function(s){return s.pendingCritical.length+" Critical check"+(s.pendingCritical.length===1?" is":"s are")+" still to do. Launch only when they are all right.";},
  not_ready:function(s){return s.failedCritical.length+" Critical check"+(s.failedCritical.length===1?" is":"s are")+" wrong ("+s.failedCritical.join(", ")+"). Do not launch until "+(s.failedCritical.length===1?"it is":"they are")+" fixed.";}
};
function rowState(id){
  // A wrong mark with no reason yet ("draft") wins over a saved right or N/A: it is what the reviewer is doing now,
  // and the saved answer stands until the reason is written. A saved wrong stays wrong while its text is edited.
  var c=S.checks[id];
  if(S.pend[id]&&(!c||c.status!=="fail")) return "draft";
  return c&&c.status?c.status:"todo";
}
function noteOf(id){var c=S.checks[id]; return S.text.hasOwnProperty(id)?S.text[id]:(c&&c.source==="person"?c.note:"");}
function paintHead(){
  var L=S.launch, bits=[];
  bits.push(esc(L.accountName)+" · "+dashed(L.accountId));
  if(L.campaignTypes.length) bits.push(esc(L.campaignTypes.join(", ")));
  if(L.readAt) bits.push("read from Google Ads "+esc(rel(L.readAt)));
  $("#wName").textContent=L.campaignName; $("#wMeta").innerHTML=bits.join('<span class="sep">·</span>');
  $("#readMsg").innerHTML=!L.readAt?'<div class="pm-note">'+(S.readError?esc(S.readError.error)+" ":"The campaign has not been read from Google Ads yet. ")+
    'Every check can still be done by hand. <a href="#" id="tryRead">Read it now</a>.</div>':"";
}
function paintVerdict(){
  var s=S.summary, v=s.verdict, sev=s.bySeverity;
  var stat=function(name){var b=sev[name];return '<span class="qa-stat"><b>'+(b.pass+b.na)+'/'+b.total+'</b> '+name+' right</span>';};
  $("#verdict").className="qa-verdict "+v.code;
  $("#verdict").innerHTML=
    '<div class="qa-v-main"><span class="qa-v-ic">'+(v.code==="ready"?'<svg viewBox="0 0 24 24"><path d="M5 12l4 4 10-10"/></svg>':v.code==="not_ready"?'<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>':'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>')+'</span>'+
    '<div><div class="qa-v-t">'+esc(v.text)+'</div><div class="qa-v-s">'+esc(VERDICT_SUB[v.code](s))+'</div></div></div>'+
    '<div class="qa-v-bar"><i style="width:'+s.percent+'%"></i></div>'+
    '<div class="qa-v-stats"><span class="qa-stat big"><b>'+s.checked+'/'+s.total+'</b> checked ('+s.percent+'%)</span>'+stat("Critical")+stat("Major")+stat("Minor")+
    (s.failed.length?'<span class="qa-stat bad"><b>'+s.failed.length+'</b> wrong</span>':"")+'</div>';
}
function paintFilters(){
  var ids=S.summary.applicable, n={all:ids.length,todo:0,pass:0,fail:0,na:0};
  ids.forEach(function(id){var st=rowState(id); if(st==="todo"||st==="draft"){n.todo++;} if(st==="draft"){n.fail++;} if(n[st]!=null&&st!=="todo")n[st]++;});
  var st=[["all","All"],["todo","To do"],["pass","Right"],["fail","Wrong"],["na","N/A"]];
  $("#fStatus").innerHTML=st.map(function(x){return '<button type="button" class="pm-chip'+(S.fStatus===x[0]?" on":"")+'" data-f="'+x[0]+'" aria-pressed="'+(S.fStatus===x[0])+'">'+x[1]+' <span class="n">'+n[x[0]]+'</span></button>';}).join("");
  var sv=[["all","All"],["Critical","Critical"],["Major","Major"],["Minor","Minor"]];
  $("#fSev").innerHTML=sv.map(function(x){return '<button type="button" class="pm-chip'+(S.fSev===x[0]?" on":"")+'" data-s="'+x[0]+'" aria-pressed="'+(S.fSev===x[0])+'">'+x[1]+'</button>';}).join("");
  var have=S.launch.clientTypes;
  $("#fClient").innerHTML=S.list.clientTypes.filter(function(t){return t!=="All";}).map(function(t){
    var on=have.indexOf(t)>=0; return '<button type="button" class="pm-chip'+(on?" on":"")+'" data-c="'+esc(t)+'" aria-pressed="'+on+'">'+esc(t)+'</button>';}).join("");
  var nc=S.needs, el=$("#clientNote");
  el.hidden=!(nc&&nc.count);
  if(nc&&nc.count) el.innerHTML='<b>'+nc.count+' more check'+(nc.count===1?"":"s")+'</b> ('+nc.critical+' Critical) only apply to a type of client: '+esc(nc.types.join(", "))+
    '. Pick the client type above to add '+(nc.count===1?"it":"them")+'. Until then they are not counted in the verdict.';
}
$("#fStatus").addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;S.fStatus=b.dataset.f;paintFilters();paintSections();});
$("#fSev").addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;S.fSev=b.dataset.s;paintFilters();paintSections();});
$("#fClient").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return;
  var t=b.dataset.c, cur=S.launch.clientTypes.slice(), i=cur.indexOf(t); if(i>=0) cur.splice(i,1); else cur.push(t);
  call("PUT",API+"/launches/"+S.launchId,{clientTypes:cur}).then(function(r){
    if(!r.ok) return say(fail(r,"That could not be saved."));
    say(""); S.launch=r.j.launch; S.checks=r.j.checks||{}; S.summary=r.j.summary; S.needs=r.j.needsClientType; render(); refreshCampaignList();
  });
});

function tag(t,cls){return '<span class="qa-tag '+(cls||"")+'">'+esc(t)+'</span>';}
function rowHtml(it){
  var id=it.id, c=S.checks[id], st=rowState(id);
  var pass=st==="pass", isFail=st==="fail"||st==="draft", na=st==="na";
  var note=noteOf(id), found=c&&c.found?c.found:"";
  var tags="";
  if(it.campaignTypes[0]!=="All") tags+=tag(it.campaignTypes.join(" · "));
  if(it.clientTypes[0]!=="All") tags+=tag(it.clientTypes.join(" · "),"cl");
  if(it.condition) tags+=tag("If "+it.condition,"cond");
  var msg=S.rowMsg[id]||"";
  var by=c&&c.status?(c.source==="agent"?"Ticked by the QA Agent":(isFail?"Wrong":na?"N/A":"Right")+" · "+esc(who(c.by)))+" · "+esc(rel(c.at)):"";
  if(c&&c.source==="agent"&&c.status==="fail") by="Marked wrong by the QA Agent · "+esc(rel(c.at));
  var ph=isFail?"Why is this wrong? What needs fixing, and where?":(c&&c.source==="agent"?"Add a comment":"Comment");
  return '<div class="qa-row st-'+(st==="draft"?"fail draft":st)+'" data-id="'+esc(id)+'" id="chk-'+esc(id)+'">'+
    '<div class="qa-ctl">'+
      '<button type="button" class="qa-box" role="checkbox" aria-checked="'+pass+'" data-act="pass" title="'+(pass?(c&&c.source==="agent"?"The agent found this right. Click to confirm it yourself":"Right. Click to undo"):"Right")+'" aria-label="Right: '+esc(it.check)+'"><svg viewBox="0 0 24 24"><path d="M5 12l4 4 10-10"/></svg></button>'+
      '<button type="button" class="qa-x" aria-pressed="'+isFail+'" data-act="fail" title="'+(isFail?"Wrong. Click to undo":"Wrong")+'" aria-label="Wrong: '+esc(it.check)+'"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>'+
    '</div>'+
    '<div class="qa-main">'+
      '<div class="qa-line"><span class="qa-id">'+esc(id)+'</span><b class="qa-check">'+esc(it.check)+'</b>'+
        '<span class="qa-sev '+it.severity.toLowerCase()+'">'+it.severity+'</span>'+tags+'</div>'+
      (found?'<div class="qa-found'+(c.status==="fail"&&c.source==="agent"?" bad":"")+'"><span class="qa-found-k">Found</span><span class="qa-found-v">'+esc(found)+'</span></div>':"")+
      '<p class="qa-verify">'+esc(it.verify)+'</p>'+
      '<div class="qa-foot"><span class="qa-st">'+esc(msg)+'</span>'+(by?'<span class="qa-by" title="'+esc(c&&c.by||"")+'">'+by+'</span>':"")+
        '<button type="button" class="qa-na" aria-pressed="'+na+'" data-act="na" title="Does not apply to this campaign">N/A</button></div>'+
    '</div>'+
    '<div class="qa-why"><textarea rows="3" maxlength="2000" data-why="'+esc(id)+'" placeholder="'+esc(ph)+'" aria-label="Comment on '+esc(it.check)+'">'+esc(note)+'</textarea></div>'+
  '</div>';
}
function paintSections(){
  var s=S.summary, ok={}; s.applicable.forEach(function(id){ok[id]=1;});
  var by={}; s.bySection.forEach(function(b){by[b.section]=b;});
  var html="";
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
    if(!rows.length) return;
    var done=b.total-b.pending, closed=!!S.closed[sec], pct=b.total?Math.round(100*done/b.total):0;
    html+='<section class="pm-panel qa-sec'+(closed?" closed":"")+'" data-sec="'+esc(sec)+'">'+
      '<button type="button" class="qa-sec-h" data-toggle="'+esc(sec)+'" aria-expanded="'+(!closed)+'">'+
        '<span class="qa-chev"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span><span class="qa-sec-t">'+esc(sec)+'</span>'+
        (b.fail?'<span class="qa-pill bad">'+b.fail+' wrong</span>':"")+
        '<span class="qa-sec-n"><b>'+done+'</b> of '+b.total+' checked</span><span class="qa-mini"><i style="width:'+pct+'%"></i></span></button>'+
      '<div class="qa-rows">'+rows.map(rowHtml).join("")+'</div></section>';
  });
  $("#sections").innerHTML=html||'<div class="pm-panel"><p class="pm-empty">'+(S.fStatus==="all"&&S.fSev==="all"?"No checks apply to this campaign.":"Nothing matches this filter.")+'</p></div>';
}
function render(){
  paintHead(); paintVerdict(); paintFilters(); paintSections();
  $("#foot").innerHTML="Source: the PPC team's Campaign Launch QC Checklist. <b>Critical</b> checks are launch blockers; <b>Major</b> should be fixed before launch, "+
    "or within 24 hours with a note; <b>Minor</b> is best practice. The agent ticks a box only when Google Ads gives an exact answer; otherwise it prints what it found and leaves the box to you. "+
    "Your click always wins. Each answer is saved as you make it, with who made it and when.";
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
    var pill=h.querySelector(".qa-pill"), t=b.fail?b.fail+" wrong":"";
    if(t&&!pill){pill=document.createElement("span");pill.className="qa-pill bad";h.insertBefore(pill,h.querySelector(".qa-sec-n"));}
    if(pill){ if(t) pill.textContent=t; else pill.remove(); }
  });
}

/* ── Answering ────────────────────────────────────────────── */
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
    if(!S.text.hasOwnProperty(id)||S.text[id].trim()===(note||"")) delete S.text[id];       // keep anything typed since
    delete S.pend[id];
    if(q){ refresh(id); return save(id,q.status,q.note); }
    refresh(id); paintCampaignBadge();
    setTimeout(function(){ if(S.rowMsg[id]==="Saved"){S.rowMsg[id]="";setMsg(id);} },1800);
  }).catch(function(){
    S.inflight[id]=false; S.rowMsg[id]="The server could not be reached. Try again."; setMsg(id,true);
  });
}
function setMsg(id,bad){
  var el=document.querySelector("#chk-"+CSS.escape(id)+" .qa-st"); if(!el) return;
  el.textContent=S.rowMsg[id]||""; el.classList.toggle("bad",!!bad);
}
function paintCampaignBadge(){
  var c=S.campaigns.filter(function(x){return x.id===S.campaignId;})[0]; if(!c) return;
  c.qc={id:S.launchId,verdict:S.summary.verdict,percent:S.summary.percent,checked:S.summary.checked,total:S.summary.total,
        failedCount:S.summary.failed.length,failedCritical:S.summary.failedCritical.length};
  paintCampaigns();
}
function commit(id){
  // Save what the row now says: its answer and its comment.
  var st=rowState(id), note=noteOf(id).trim();
  if(st==="fail"||st==="draft"){
    if(note.length<3){ S.rowMsg[id]="Add the reason to record this as wrong."; setMsg(id,true); return; }
    return save(id,"fail",note);
  }
  if(st==="pass"||st==="na") return save(id,st,note);
  S.rowMsg[id]="Choose right or wrong and the comment is saved with it."; setMsg(id);
}
$("#sections").addEventListener("click",function(e){
  var head=e.target.closest("[data-toggle]");
  if(head){var k=head.dataset.toggle; S.closed[k]=!S.closed[k]; return paintSections();}
  var b=e.target.closest("[data-act]"); if(!b) return;
  var id=b.closest(".qa-row").dataset.id, st=rowState(id), act=b.dataset.act, c=S.checks[id], mine=c&&c.source==="person", note=noteOf(id).trim();
  if(act==="pass"){
    delete S.pend[id];
    if(st==="pass"&&mine){ delete S.text[id]; save(id,null,""); }              // undo my tick: back to the agent's reading
    else save(id,"pass",note);                                                  // tick it (or confirm the agent's tick)
  }else if(act==="na"){
    delete S.pend[id];
    if(st==="na"&&mine){ delete S.text[id]; save(id,null,""); } else save(id,"na",note);
  }else if(act==="fail"){
    if(st==="fail"&&mine){ delete S.pend[id]; delete S.text[id]; save(id,null,""); }
    else if(st==="draft"){ delete S.pend[id]; refresh(id); }
    else if(note.length>=3) save(id,"fail",note);                              // a reason is already written
    else{ S.pend[id]=true; S.rowMsg[id]=""; refresh(id);
      var ta=document.querySelector('textarea[data-why="'+CSS.escape(id)+'"]'); if(ta) ta.focus(); }
  }
});
$("#sections").addEventListener("input",function(e){
  var ta=e.target.closest("textarea[data-why]"); if(!ta) return; var id=ta.dataset.why;
  S.text[id]=ta.value; S.rowMsg[id]=""; setMsg(id);
  clearTimeout(S.timers[id]); S.timers[id]=setTimeout(function(){ commit(id); },900);
});
$("#sections").addEventListener("focusout",function(e){
  var ta=e.target.closest("textarea[data-why]"); if(!ta) return; var id=ta.dataset.why;
  clearTimeout(S.timers[id]);
  var c=S.checks[id], saved=c&&c.source==="person"&&c.note===ta.value.trim();
  if(!saved&&S.text.hasOwnProperty(id)) commit(id);
});

/* ── Header actions ───────────────────────────────────────── */
function run(){
  if(S.running||!S.launchId) return;
  S.running=true; var lab=$("#runBtn span"); lab.textContent="Reading Google Ads…"; $("#runBtn").disabled=true; say("");
  call("POST",API+"/launches/"+S.launchId+"/run",{}).then(function(r){
    S.running=false; $("#runBtn").disabled=false; lab.textContent="Re-run checks";
    if(!r.ok) return say(fail(r,"The campaign could not be read."));
    var keepText=S.text, keepPend=S.pend; load(r.j); S.text=keepText; S.pend=keepPend; render(); refreshCampaignList();
  }).catch(function(){S.running=false;$("#runBtn").disabled=false;lab.textContent="Re-run checks";say("The server could not be reached. Try again.");});
}
$("#runBtn").addEventListener("click",run);
document.addEventListener("click",function(e){ if(e.target&&e.target.id==="tryRead"){e.preventDefault();run();} });
$("#removeBtn").addEventListener("click",function(){
  if(!S.launch||!confirm('Remove the QC for "'+S.launch.campaignName+'"? It disappears from the list; its answers are kept in the records.')) return;
  call("DELETE",API+"/launches/"+S.launchId,{}).then(function(r){
    if(!r.ok) return say(fail(r,"That could not be removed."));
    S.launchId=""; S.launch=null; S.campaignId=""; view("empty","Choose a campaign","The QC starts as soon as you pick one."); loadCampaigns();
  });
});
$("#copyBtn").addEventListener("click",function(){
  var s=S.summary, L=S.launch, lines=["Launch QC: "+L.campaignName+" | "+L.accountName+" ("+dashed(L.accountId)+")","Verdict: "+s.verdict.text,
    "Checked: "+s.checked+"/"+s.total+" checks | Critical right "+(s.bySeverity.Critical.pass+s.bySeverity.Critical.na)+"/"+s.bySeverity.Critical.total];
  if(s.failed.length){lines.push("","Wrong:"); s.failed.forEach(function(id){var it=S.items[id],c=S.checks[id]||{};
    lines.push("- "+id+" "+it.check+" ("+it.severity+"): "+(c.note||c.found||""));});}
  if(s.pendingCritical.length){lines.push("","Critical checks still to do: "+s.pendingCritical.join(", "));}
  var text=lines.join("\n"), label=$("#copyBtn span");
  var done=function(){label.textContent="Copied";setTimeout(function(){label.textContent="Copy summary";},1600);};
  if(navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done,function(){say("Copying was blocked by the browser.");});
  else{var t=document.createElement("textarea");t.value=text;document.body.appendChild(t);t.select();try{document.execCommand("copy");done();}catch(e){say("Copying was blocked by the browser.");}t.remove();}
});

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search), want=p.get("account")||"";
  S.wantCampaign=p.get("campaign")||"";
  if(!want){try{want=localStorage.getItem("qa_account")||"";}catch(e){}}
  view("empty"); acctPicker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true); campaignsOff("Choose an account first");
  Promise.all([call("GET",API+"/checklist"),call("GET",BASE+"/api/gads/accounts")]).then(function(rs){
    if(!rs[0].ok){return say(fail(rs[0],"The checklist could not be loaded."));}
    S.list=rs[0].j; S.list.items.forEach(function(it){S.items[it.id]=it;});
    var a=rs[1];
    if(a.ok&&a.j.accounts&&a.j.accounts.length){
      S.accounts=a.j.accounts;
      if(want){var hit=S.accounts.filter(function(x){return x.id===want;})[0]; if(hit){S.account={id:hit.id,name:hit.name};}}
      setAccounts(S.accounts);
      if(S.account) loadCampaigns();
    }else{
      acctPicker.set([{value:"",label:"Accounts unavailable",sub:"type it in instead",avatar:"!",hue:20}],"",true);
      manualMode((a.j&&a.j.error)||"The account list could not be loaded from Google Ads.");
      if(want&&/^\d{10}$/.test(want)){ $("#mId").value=want; }
    }
  }).catch(function(){say("The server could not be reached. Reload to try again.");});
})();
})();
