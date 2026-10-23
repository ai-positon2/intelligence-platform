/* QA Agent (Paid Marketing Agents): the Campaign Launch QC, done by the AI.
 *
 * Pick Google, Meta or Both and the account (a Google Ads account, a Meta ad account), then one or more of its live
 * campaigns (a multiple choice: each gets its own QC, shown as tabs). The AI reviews every check
 * on the shared checklist, reading each campaign in
 * Google Ads and its landing pages, and flags what is wrong with the fix. This page leads
 * with those flags, then the few checks the AI could not verify for itself ("needs a
 * human"), then what passed. A person can confirm or overrule any check with one click and
 * (a wrong mark is saved with the AI's finding as its reason); nothing has to be ticked for the review to be complete.
 *
 * The checks that "depend on the campaign book" are compared with the book the team adds here
 * as an Excel file or a Google Sheet link; adding it starts a fresh review against it.
 *
 * The review runs on the server in the background (about a minute); this page polls its
 * progress. The server also works out the verdict, which is what the banner shows: this
 * page never decides "ready" for itself. The rules are tracker/paid_qa.py, the evidence
 * tracker/paid_qa_auto.py and paid_qa_web.py, the reviewer tracker/paid_qa_ai.py.
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
function who(by){return by==="QA Agent"?"the AI":(String(by||"").split("@")[0]||"someone");}
function rel(iso){
  var s=(Date.now()-new Date(iso).getTime())/1000; if(!isFinite(s)) return "";
  if(s<60) return "just now"; if(s<3600) return Math.round(s/60)+" min ago";
  if(s<86400) return Math.round(s/3600)+" h ago"; return Math.round(s/86400)+" d ago";
}
var VNAME = {ready:"Ready",incomplete:"Incomplete",not_ready:"Not ready"};

var root = $("#qa");
var MAX_SEL = 10;
var S = {list:null, lists:{}, items:{}, accounts:[], accts:[], platform:"google", account:null, campaigns:[], meta:{}, sel:[], qcs:{}, campaignId:"", launchId:"", launch:null,
         checks:{}, summary:null, needs:null, review:null, fStatus:"all", fSev:"all", closed:{}, rowMsg:{},
         inflight:{}, queued:{}, seq:0, manual:false, poll:null, startError:"", bookAll:true, bookBusy:""};

/* A QC is checked against its platform's checklist (the Campaign Book checks for Google Ads, the Meta launch checklist for a Meta ad account): the ids are not the same checks. */
function isMetaId(id){return /^act_\d+$/.test(String(id||""));}
function useList(platform){
  var l=S.lists[platform]||S.lists.google; if(!l) return;
  S.list=l; S.items={}; l.items.forEach(function(it){S.items[it.id]=it;});
}
function pn(){return S.launch&&isMetaId(S.launch.accountId)?"Meta":"Google Ads";}

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
    $("#emptyP").textContent=text||"Then pick one or more of its live campaigns. The AI reviews the PPC team's Campaign Book checks: the campaign's own settings, the automation switches and the auto-apply recommendations."; }
}
function fail(r,fallback){return esc((r.j&&r.j.error)||fallback||("Something went wrong (HTTP "+r.status+")."));}
function stopPoll(){ if(S.poll){clearTimeout(S.poll);S.poll=null;} }

/* ── A multiple-choice campaign picker (same look as the shared searchable dropdown) ─── */
function MultiPick(host,opts){
  var wrap=document.createElement("div"); wrap.className="sq-dd pd-dd qa-ms";
  wrap.innerHTML='<span class="pd-dd-k">'+esc(opts.label)+'</span>'+
    '<button type="button" class="sq-dd-btn" aria-haspopup="listbox" aria-expanded="false" aria-label="'+esc(opts.label)+'"></button>'+
    '<div class="sq-dd-menu" hidden><input class="sq-dd-search" placeholder="Filter campaigns…" autocomplete="off">'+
    '<div class="qa-ms-bar"><button type="button" data-a="all">Select all</button><button type="button" data-a="none">Clear</button><span class="qa-ms-n"></span></div>'+
    '<div role="listbox" aria-multiselectable="true" class="pd-dd-list"></div>'+
    '<div class="qa-ms-foot"><button type="button" class="pm-btn qa-ms-go"></button></div></div>';
  host.appendChild(wrap);
  var btn=wrap.querySelector(".sq-dd-btn"), menu=wrap.querySelector(".sq-dd-menu"), list=wrap.querySelector(".pd-dd-list"),
      q=wrap.querySelector(".sq-dd-search"), nEl=wrap.querySelector(".qa-ms-n"), go=wrap.querySelector(".qa-ms-go");
  var items=[], applied=[], draft=[], disabled=false, offLabel=null, shown=[];
  function av(it){return '<span class="sq-av" style="--h:'+(it.hue==null?40:it.hue)+'">'+esc(it.avatar||"·")+'</span>';}
  function byId(id){return items.filter(function(i){return i.value===id;})[0];}
  function paintBtn(){
    var h;
    if(offLabel) h=av({avatar:"…",hue:200})+'<span class="sq-dd-txt"><span class="nm">'+esc(offLabel.label)+'</span><span class="ds">'+esc(offLabel.sub||"")+'</span></span>';
    else if(!applied.length) h=av({avatar:"?",hue:200})+'<span class="sq-dd-txt"><span class="nm">Choose campaigns</span><span class="ds">'+items.length+' live · pick one or more</span></span>';
    else if(applied.length===1){ var it=byId(applied[0])||{label:applied[0],sub:""}; h=av(it)+'<span class="sq-dd-txt"><span class="nm">'+esc(it.label)+'</span><span class="ds">'+esc(it.sub||"")+'</span></span>'+(it.badge||""); }
    else h=av({avatar:String(applied.length),hue:260})+'<span class="sq-dd-txt"><span class="nm">'+applied.length+' campaigns selected</span><span class="ds">'+
      esc(applied.slice(0,2).map(function(id){return (byId(id)||{label:id}).label;}).join(", ")+(applied.length>2?" and "+(applied.length-2)+" more":""))+'</span></span>';
    btn.innerHTML=h+'<span class="sq-chev"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span>'; btn.disabled=disabled;
  }
  function paintList(){
    var term=q.value.trim().toLowerCase();
    shown=items.filter(function(i){return !term||(i.label+" "+(i.sub||"")).toLowerCase().indexOf(term)>=0;});
    list.innerHTML=shown.map(function(it,i){
      var on=draft.indexOf(it.value)>=0;
      return '<button type="button" role="option" class="sq-opt" data-i="'+i+'" aria-selected="'+on+'">'+av(it)+'<span class="sq-dd-txt"><span class="nm" style="font-size:13.5px">'+esc(it.label)+
        '</span><span class="ds">'+esc(it.sub||"")+'</span></span>'+(it.badge||"")+'<span class="tick">✓</span></button>';
    }).join("")||'<div class="sq-none">Nothing matches “'+esc(term)+'”.</div>';
    nEl.textContent=draft.length+" selected"+(draft.length>=MAX_SEL?" (the most at once)":"");
    go.textContent=draft.length?"Run the QC on "+draft.length+" campaign"+(draft.length===1?"":"s"):"Pick a campaign"; go.disabled=!draft.length;
  }
  function open(){
    if(disabled) return;
    document.querySelectorAll(".pd-dd.open").forEach(function(o){if(o!==wrap&&o.__close)o.__close();});
    draft=applied.slice(); wrap.classList.add("open"); menu.hidden=false; btn.setAttribute("aria-expanded","true"); q.value=""; paintList(); setTimeout(function(){q.focus();},20);
  }
  function close(focus){wrap.classList.remove("open"); menu.hidden=true; btn.setAttribute("aria-expanded","false"); if(focus) btn.focus();}
  wrap.__close=close;
  btn.addEventListener("click",function(){menu.hidden?open():close();});
  list.addEventListener("click",function(e){
    var o=e.target.closest(".sq-opt"); if(!o) return; var id=shown[+o.dataset.i].value, i=draft.indexOf(id);
    if(i>=0) draft.splice(i,1); else if(draft.length<MAX_SEL) draft.push(id); paintList();
  });
  wrap.querySelector(".qa-ms-bar").addEventListener("click",function(e){
    var b=e.target.closest("button"); if(!b) return;
    if(b.dataset.a==="none") draft=[];
    else shown.forEach(function(it){ if(draft.indexOf(it.value)<0&&draft.length<MAX_SEL) draft.push(it.value); });
    paintList();
  });
  q.addEventListener("input",paintList);
  q.addEventListener("keydown",function(e){ if(e.key==="Escape"){e.preventDefault();close(true);} });
  go.addEventListener("click",function(){ if(!draft.length) return; applied=draft.slice(); paintBtn(); close(true); opts.onApply(applied.slice()); });
  document.addEventListener("click",function(e){ if(menu.hidden) return; var path=e.composedPath?e.composedPath():[]; if(path.indexOf(wrap)<0&&!wrap.contains(e.target)) close(); });   // the list is repainted by a click, so ask where the click began
  return {
    set:function(newItems,newApplied){items=newItems; applied=newApplied.slice(); offLabel=null; disabled=false; paintBtn(); if(!menu.hidden) paintList();},
    off:function(label,sub){items=[]; applied=[]; offLabel={label:label,sub:sub}; disabled=true; paintBtn();}
  };
}

/* ── Account, then campaigns ──────────────────────────────── */
var campPicker = MultiPick($("#campPick"),{label:"Live campaigns",onApply:function(ids){applySelection(ids);}});
var scope=null, G_LIST=[], M_LIST=[];
function acctOf(id){
  var g=G_LIST.filter(function(x){return x.id===id;})[0]; if(g) return {id:g.id,name:g.name,platform:"google"};
  var m=M_LIST.filter(function(x){return x.id===id;})[0]; return m?{id:m.id,name:m.name,platform:"meta"}:null;
}
function campaignsOff(label,sub){campPicker.off(label,sub);}
function resetQcs(){ stopPoll(); S.sel=[]; S.qcs={}; S.campaignId=""; S.launchId=""; S.launch=null; paintTabs(); }
/* The platform switch and the account picker are the shared control (static/js/pm_scope.js): Google, Meta or Both (a Google Ads account and a Meta ad account, their campaigns in one list). */
function applyScope(a,p){
  var list=window.PMScope.partsOf(a).filter(Boolean).map(acctOf).filter(Boolean);
  S.platform=p; resetQcs(); S.campaigns=[]; S.accts=list;
  if(!list.length){S.account=null; campaignsOff("Choose an account first"); view("empty"); sync(); return;}
  S.account={id:list[0].id,name:list[0].name};
  try{localStorage.setItem("qa_account",a);}catch(e){}
  loadCampaigns();
}
function badgeFor(q){
  return q?'<span class="qa-dot '+q.verdict.code+'" title="'+esc(VNAME[q.verdict.code]||"")+'"></span>':"";
}
var CHOOSE="The AI review starts as soon as you apply your choice. Pick one campaign or several.";
/* The live campaigns of every account chosen, in one list: each remembers its account. Resolves to {campaigns, errors}. */
function fetchCampaigns(){
  var accts=S.accts.length?S.accts:(S.account?[{id:S.account.id,name:S.account.name,platform:"google"}]:[]);
  return Promise.all(accts.map(function(a){
    return call("GET",API+"/campaigns?account="+encodeURIComponent(a.id)).then(function(r){
      if(!r.ok) return {acct:a,error:(r.j&&r.j.error)||("HTTP "+r.status)};
      return {acct:a,campaigns:(r.j.campaigns||[]).map(function(c){c.acct=a; return c;})};
    }).catch(function(){return {acct:a,error:"The server could not be reached."};});
  })).then(function(rs){
    return {campaigns:rs.reduce(function(all,x){return all.concat(x.campaigns||[]);},[]),
      errors:rs.filter(function(x){return x.error;}).map(function(x){return x.acct.name+": "+x.error;}), failedAll:rs.every(function(x){return x.error;})};
  });
}
function loadCampaigns(keep){
  var seq=++S.seq; say(""); if(!(keep&&S.sel.length)) view("skel"); campaignsOff("Loading live campaigns…");
  fetchCampaigns().then(function(got){
    if(seq!==S.seq) return;
    if(got.failedAll){
      var why=got.errors.join(" ")||"The account could not be read.";
      view("empty","The campaigns could not be loaded",why);
      campaignsOff("Campaigns unavailable","The account could not be read");
      return say(esc(why)+(S.accts.every(function(a){return a.platform==="google";})?" You can still run a QC: <a href=\"#\" id=\"goManual\">type the campaign in</a>.":""));
    }
    if(got.errors.length) say(got.errors.map(esc).join("<br>")+"<br>The campaigns of the other account are shown.","pm-note");
    S.campaigns=got.campaigns; S.campaigns.forEach(function(c){S.meta[c.id]={name:c.name,channel:c.channel,acct:c.acct};}); paintCampaigns(); sync();
    if(keep&&S.sel.length) return;
    if(!S.campaigns.length) return view("empty","No live campaigns","This account has no live campaign to QC. Turn on the campaign you are launching, or pick another account.");
    view("empty","Choose campaigns",CHOOSE);
    if(S.wantCampaign){
      var want=S.wantCampaign.split(",").filter(function(id){return S.campaigns.some(function(c){return c.id===id;});}); S.wantCampaign="";
      if(want.length) applySelection(want);
    }
  });
}
function paintCampaigns(){
  var items=S.campaigns.map(function(c){
    return {value:c.id,label:c.name,avatar:inits(c.name),hue:hueOf(c.name),badge:badgeFor(c.qc),
      sub:(c.type||"Campaign")+(c.qc?" · QC "+c.qc.percent+"% · "+(VNAME[c.qc.verdict.code]||""):"")};
  });
  campPicker.set(items,S.sel);
}
function startOne(id){
  var m=S.meta[id]||{name:id,channel:""}, a=m.acct||S.account;
  return call("POST",API+"/start",{accountId:a.id,accountName:a.name,campaignId:id,campaignName:m.name,channel:m.channel||""}).then(function(r){
    if(!r.ok) return {error:(r.j&&r.j.error)||("HTTP "+r.status)};
    return r.j;
  }).catch(function(){return {error:"The server could not be reached."};});
}
function applySelection(ids){
  stopPoll(); var seq=++S.seq;
  ids=ids.filter(function(id,i){return ids.indexOf(id)===i;}).slice(0,MAX_SEL);
  S.sel=ids; Object.keys(S.qcs).forEach(function(k){ if(ids.indexOf(k)<0) delete S.qcs[k]; });
  if(!ids.length){ S.campaignId=""; S.launchId=""; S.launch=null; paintTabs(); paintCampaigns(); view("empty","Choose campaigns",CHOOSE); sync(); return; }
  say(""); paintTabs(); paintCampaigns(); sync();
  var active=ids.indexOf(S.campaignId)>=0?S.campaignId:ids[0], shownActive=false;
  if(S.qcs[active]&&!S.qcs[active].error){ activate(active); shownActive=true; } else view("skel");
  var queue=ids.filter(function(id){return !S.qcs[id];}), errors=[];
  function worker(){
    var id=queue.shift(); if(!id) return Promise.resolve();
    return startOne(id).then(function(j){
      if(seq!==S.seq) return;
      S.qcs[id]=j;
      if(j.error) errors.push((S.meta[id]||{}).name+": "+j.error);
      else if(j.reviewError&&errors.indexOf(j.reviewError)<0) errors.push(j.reviewError);
      if(id===active&&!shownActive&&!j.error){ shownActive=true; activate(active); }
      paintTabs();
    }).then(worker);
  }
  Promise.all([worker(),worker(),worker()]).then(function(){
    if(seq!==S.seq) return;
    if(!shownActive){                                       // the active one failed: show the first that opened
      var ok=ids.filter(function(id){return S.qcs[id]&&!S.qcs[id].error;})[0];
      if(ok) activate(ok); else view("empty");
    }
    if(errors.length) say(errors.map(esc).join("<br>"), "pm-err");
    paintTabs(); pollAll(seq); refreshCampaignList(); sync();
  });
}
function activate(cid){
  var j=S.qcs[cid]; if(!j||j.error) return;
  S.campaignId=cid; S.startError=j.reviewError||""; load(j); view("work"); render(); paintTabs(); sync(); pollAll(S.seq);
}
function open(j){
  // a payload that came back from an action on the active QC (re-run, a book): keep it and show it
  S.qcs[j.launch.campaignId]=j; if(S.sel.indexOf(j.launch.campaignId)<0) S.sel.push(j.launch.campaignId);
  activate(j.launch.campaignId); pollAll(S.seq);
}
function isRunning(j){return j&&j.launch&&j.launch.review&&j.launch.review.status==="running";}
function pollAll(seq){
  stopPoll();
  var running=S.sel.filter(function(id){return isRunning(S.qcs[id]);});
  if(!running.length) return;
  S.poll=setTimeout(function(){
    if(seq!==S.seq) return;
    Promise.all(running.map(function(id){
      return call("GET",API+"/launches/"+S.qcs[id].launch.id).then(function(r){return {id:id,r:r};}).catch(function(){return {id:id,r:null};});
    })).then(function(rs){
      if(seq!==S.seq) return;
      var finished=false;
      rs.forEach(function(x){ if(x.r&&x.r.ok){ S.qcs[x.id]=x.r.j; if(!isRunning(x.r.j)) finished=true; } });
      if(running.indexOf(S.campaignId)>=0&&S.qcs[S.campaignId]){
        var was=S.review&&S.review.status==="running"; load(S.qcs[S.campaignId]);
        if(was&&!isRunning(S.qcs[S.campaignId])){ S.fStatus=defaultFilter(); render(); } else renderReview();
      }
      paintTabs(); if(finished) refreshCampaignList();
      pollAll(seq);
    });
  },2500);
}
/* ── Tabs: one per selected campaign ─────────────────────── */
function tabState(cid){
  var j=S.qcs[cid], m=S.meta[cid]||{name:cid};
  if(!j) return {name:m.name,state:"starting"};
  if(j.error) return {name:m.name,state:"error"};
  var act=cid===S.campaignId, rev=act?S.review:(j.launch.review||{}), sum=act?S.summary:j.summary;
  return {name:j.launch.campaignName||m.name, state:rev.status==="running"?"running":rev.status==="error"?"error":"done",
          verdict:sum.verdict.code, flagged:sum.failed.length, human:(sum.needsHuman||[]).length};
}
function paintTabs(){
  var el=$("#qcTabs"); el.hidden=S.sel.length<2; if(el.hidden){el.innerHTML="";return;}
  el.innerHTML=S.sel.map(function(cid){
    var t=tabState(cid), ic=(t.state==="running"||t.state==="starting")?'<span class="qa-spin sm"></span>':t.state==="error"?'<span class="qa-dot not_ready"></span>':'<span class="qa-dot '+t.verdict+'"></span>';
    var sub=t.state==="starting"?"Starting":t.state==="running"?"Reviewing":t.state==="error"?"Did not finish":(t.flagged?t.flagged+" flagged":"nothing flagged")+(t.human?" · "+t.human+" for a human":"");
    return '<button type="button" class="qa-tab'+(cid===S.campaignId?" on":"")+'" data-c="'+esc(cid)+'" aria-pressed="'+(cid===S.campaignId)+'">'+ic+
      '<span class="qa-tab-t"><span class="qa-tab-n">'+esc(t.name)+'</span><span class="qa-tab-s">'+esc(sub)+'</span></span></button>';
  }).join("");
}
$("#qcTabs").addEventListener("click",function(e){ var b=e.target.closest("button"); if(!b) return; if(b.dataset.c!==S.campaignId) activate(b.dataset.c); });
function refreshCampaignList(){
  if(S.manual||!S.account) return;
  fetchCampaigns().then(function(got){ if(!got.failedAll){S.campaigns=got.campaigns;paintCampaigns();} });
}
function load(j){
  var first=S.launchId!==j.launch.id;
  useList(isMetaId(j.launch.accountId)?"meta":"google");
  S.launch=j.launch; S.launchId=j.launch.id; S.campaignId=j.launch.campaignId; S.checks=j.checks||{}; S.summary=j.summary; S.needs=j.needsClientType;
  S.review=j.launch.review||{status:""};
  if(first){ S.rowMsg={}; S.fSev="all"; S.fStatus=defaultFilter(); }
}
function defaultFilter(){
  var s=S.summary; if(!s) return "all";
  if(s.failed.length) return "fail";
  if(s.needsHuman&&s.needsHuman.length) return "todo";
  return "all";
}
function sync(){
  try{var q=S.account?"?account="+S.accts.map(function(a){return a.id;}).concat(S.accts.length?[]:[S.account.id]).join("+")+(S.sel.length?"&campaign="+S.sel.join(","):""):"";history.replaceState(null,"",location.pathname+q);}catch(e){}
}
/* The account list is read from Google Ads, which may be rate limited or not connected. */
function manualMode(why){
  S.manual=true; $("#pickers").hidden=true; $("#manual").hidden=false;
  say(esc(why||"The account list could not be loaded from Google Ads.")+" You can still run a QC: type the account and campaign in. The AI needs Google Ads to read the campaign, so until it is back the checks stay open for a person.","pm-note");
}
$("#mGo").addEventListener("click",function(){
  var id=$("#mId").value.replace(/\D/g,""), name=$("#mName").value.trim(), cid=$("#mCampId").value.replace(/\D/g,""), cname=$("#mCamp").value.trim();
  if(id.length!==10) return say("The Google Ads customer ID should be 10 digits.");
  if(!name) return say("Add the account name.");
  if(!cid) return say("Add the campaign ID (digits only).");
  if(!cname) return say("Add the campaign name.");
  S.account={id:id,name:name}; S.meta[cid]={name:cname,channel:$("#mType").value}; applySelection([cid]);
});
document.addEventListener("click",function(e){ if(e.target&&e.target.id==="goManual"){e.preventDefault();manualMode("Google Ads could not be read.");
  if(S.account){$("#mId").value=S.account.id;$("#mName").value=S.account.name;} } });

/* ── The QC ───────────────────────────────────────────────── */
var VERDICT_SUB = {
  ready:function(s){return "The AI found nothing wrong, and every check is passed or confirmed.";},
  incomplete:function(s){return s.pendingBlocking.length+" check"+(s.pendingBlocking.length===1?"":"s")+" the AI could not verify from what it can see ("+s.pendingBlocking.join(", ")+"). A person has to look: confirm each below"+(S.launch&&!S.launch.book&&S.launch.bookApplies!==false?", or add the campaign book, which some of them need":"")+".";},
  not_ready:function(s){return "The AI flagged "+s.failedBlocking.length+" check"+(s.failedBlocking.length===1?"":"s")+" ("+s.failedBlocking.join(", ")+"). Do not launch until "+(s.failedBlocking.length===1?"it is":"they are")+" fixed.";}
};
function rowState(id){
  var c=S.checks[id];
  return c&&c.status?c.status:"todo";
}
function paintHead(){
  var L=S.launch, bits=[];
  bits.push(esc(L.accountName)+" · "+dashed(L.accountId));
  if(L.campaignTypes.length) bits.push(esc(L.campaignTypes.join(", ")));
  if(L.readAt) bits.push("reviewed by the AI "+esc(rel(L.readAt)));
  $("#wName").textContent=L.campaignName; $("#wMeta").innerHTML=bits.join('<span class="sep">·</span>');
}
function renderReview(){
  var r=S.review||{status:""}, box=$("#reviewBox"), running=r.status==="running";
  $("#runBtn").disabled=running; $("#runBtn span").textContent=running?"Reviewing…":(S.launch&&S.launch.readAt?"Re-run the AI review":"Run the AI review");
  var h="";
  if(running){
    var pct=r.total?Math.round(100*r.done/r.total):4;
    h='<section class="qa-review"><div class="qa-review-t"><span class="qa-spin"></span><b>The AI is doing the QC</b></div>'+
      '<div class="qa-review-s">'+esc(r.phase||"Starting")+(r.total?" ("+r.done+" of "+r.total+" sections)":"")+'. This takes about a minute: it reads the campaign in '+pn()+', opens the landing pages, then goes through the checklist.</div>'+
      '<div class="qa-v-bar"><i style="width:'+pct+'%"></i></div></section>';
  }else if(r.status==="error"){
    h='<div class="pm-err">The review did not finish: '+esc(r.error||"unknown error")+' <a href="#" id="tryRead">Run it again</a>.</div>';
  }else if(!r.status&&!(S.launch&&S.launch.readAt)){
    h='<div class="pm-note">'+(S.startError?esc(S.startError)+" ":"The AI has not reviewed this campaign yet. ")+'<a href="#" id="tryRead">Run the AI review</a>.</div>';
  }else if(r.note){
    h='<div class="pm-note">'+esc(r.note)+' <a href="#" id="tryRead">Run it again</a>.</div>';
  }
  box.innerHTML=h;
  $("#results").hidden=running;
}
function paintVerdict(){
  var s=S.summary, v=s.verdict, sev=s.bySeverity, human=(s.needsHuman||[]).length;
  var stats=function(){
    var names=Object.keys(sev);
    if(names.length>1) return names.map(function(n){var b=sev[n];return '<span class="qa-stat"><b>'+(b.pass+b.na)+'/'+b.total+'</b> '+n+' passed</span>';}).join("");
    var b=sev[names[0]]; return '<span class="qa-stat"><b>'+(b.pass+b.na)+'/'+b.total+'</b> passed</span>';
  };
  $("#verdict").className="qa-verdict "+v.code;
  $("#verdict").innerHTML=
    '<div class="qa-v-main"><span class="qa-v-ic">'+(v.code==="ready"?'<svg viewBox="0 0 24 24"><path d="M5 12l4 4 10-10"/></svg>':v.code==="not_ready"?'<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>':'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>')+'</span>'+
    '<div><div class="qa-v-t">'+esc(v.text)+'</div><div class="qa-v-s">'+esc(VERDICT_SUB[v.code](s))+'</div></div></div>'+
    '<div class="qa-v-bar"><i style="width:'+s.percent+'%"></i></div>'+
    '<div class="qa-v-stats"><span class="qa-stat big"><b>'+s.checked+'/'+s.total+'</b> checks answered ('+s.percent+'%)</span>'+
    (s.failed.length?'<span class="qa-stat bad"><b>'+s.failed.length+'</b> flagged</span>':'<span class="qa-stat"><b>0</b> flagged</span>')+
    (human?'<span class="qa-stat"><b>'+human+'</b> need a human</span>':"")+stats()+'</div>';
}
function paintFilters(){
  var ids=S.summary.applicable, n={all:ids.length,todo:0,pass:0,fail:0,na:0};
  ids.forEach(function(id){var st=rowState(id); n[st]++;});
  var st=[["fail","Flagged"],["todo","Needs a human"],["pass","Passed"],["na","N/A"],["all","All"]];
  $("#fStatus").innerHTML=st.map(function(x){return '<button type="button" class="pm-chip'+(S.fStatus===x[0]?" on":"")+(x[0]==="fail"&&n.fail?" warn":"")+'" data-f="'+x[0]+'" aria-pressed="'+(S.fStatus===x[0])+'">'+x[1]+' <span class="n">'+n[x[0]]+'</span></button>';}).join("");
  var several=S.list.severities.length>1;
  $("#grpSev").hidden=!several;
  if(several){
    var sv=[["all","All"]].concat(S.list.severities.map(function(x){return [x,x];}));
    $("#fSev").innerHTML=sv.map(function(x){return '<button type="button" class="pm-chip'+(S.fSev===x[0]?" on":"")+'" data-s="'+x[0]+'" aria-pressed="'+(S.fSev===x[0])+'">'+x[1]+'</button>';}).join("");
  }
  var types=S.list.clientTypes.filter(function(t){return t!=="All";});
  $("#grpClient").hidden=!types.length; $("#clientNote").hidden=true;
  if(types.length){
    var have=S.launch.clientTypes;
    $("#fClient").innerHTML=types.map(function(t){
      var on=have.indexOf(t)>=0; return '<button type="button" class="pm-chip'+(on?" on":"")+'" data-c="'+esc(t)+'" aria-pressed="'+on+'">'+esc(t)+'</button>';}).join("");
    var nc=S.needs, el=$("#clientNote");
    el.hidden=!(nc&&nc.count);
    if(nc&&nc.count) el.innerHTML='<b>'+nc.count+' more check'+(nc.count===1?"":"s")+'</b> only apply to a type of client: '+esc(nc.types.join(", "))+
      '. Pick the client type above and the AI\'s answers for '+(nc.count===1?"it":"them")+' count. Until then they are not in the verdict.';
  }
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
function byLine(c,st){
  if(!c||!c.status) return "";
  if(c.source==="person") return (st==="fail"?"Marked wrong":st==="na"?"N/A":"Confirmed right")+" by "+esc(who(c.by))+" · "+esc(rel(c.at));
  if(c.source==="rule") return (st==="fail"?"Flagged":"Passed")+" by an exact "+pn()+" rule · "+esc(rel(c.at));
  return (st==="fail"?"Flagged":"Passed")+" by the AI · "+esc(rel(c.at));
}
function rowHtml(it){
  var id=it.id, c=S.checks[id], st=rowState(id);
  var pass=st==="pass", isFail=st==="fail", na=st==="na";
  var found=c&&c.found?c.found:"", mine=c&&c.source==="person";
  var tags="";
  if(it.campaignTypes[0]!=="All") tags+=tag(it.campaignTypes.join(" · "));
  if(it.clientTypes[0]!=="All") tags+=tag(it.clientTypes.join(" · "),"cl");
  if(it.condition) tags+=tag("If "+it.condition,"cond");
  if(c&&c.status&&!mine&&c.source==="ai") tags+=tag("AI "+(c.status==="fail"?"flagged":"passed"),c.status==="fail"?"ai bad":"ai");
  if(c&&c.status&&!mine&&c.source==="rule") tags+=tag("Rule "+(c.status==="fail"?"flagged":"passed"),c.status==="fail"?"ai bad":"ai");
  if(st==="todo") tags+=tag("Needs a human","need");
  var msg=S.rowMsg[id]||"", by=byLine(c,st);
  var label=st==="todo"?"Could not verify":(isFail&&!mine?"AI found":"Found");
  if(c&&c.source==="rule") label=pn();
  return '<div class="qa-row st-'+st+'" data-id="'+esc(id)+'" id="chk-'+esc(id)+'">'+
    '<div class="qa-ctl">'+
      '<button type="button" class="qa-box" role="checkbox" aria-checked="'+pass+'" data-act="pass" title="'+(pass&&!mine?"The AI passed this. Click to confirm it yourself":pass?"Confirmed right. Click to undo":"Mark right")+'" aria-label="Right: '+esc(it.check)+'"><svg viewBox="0 0 24 24"><path d="M5 12l4 4 10-10"/></svg></button>'+
      '<button type="button" class="qa-x" aria-pressed="'+isFail+'" data-act="fail" title="'+(isFail&&!mine?"Flagged. Click if you agree and add why":isFail?"Marked wrong. Click to undo":"Mark wrong")+'" aria-label="Wrong: '+esc(it.check)+'"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>'+
    '</div>'+
    '<div class="qa-main">'+
      '<div class="qa-line"><span class="qa-id">'+esc(id)+'</span><b class="qa-check">'+esc(it.check)+'</b>'+
        (S.list.severities.length>1?'<span class="qa-sev '+it.severity.toLowerCase()+'">'+it.severity+'</span>':"")+tags+'</div>'+
      (found?'<div class="qa-found'+(c.status==="fail"&&c.source!=="person"?" bad":st==="todo"?" need":"")+'"><span class="qa-found-k">'+label+'</span><span class="qa-found-v">'+esc(found)+'</span></div>':"")+
      (c&&c.fix&&(c.status==="fail")?'<div class="qa-fix"><span class="qa-found-k">Fix</span><span>'+esc(c.fix)+'</span></div>':"")+
      (c&&c.evidence&&c.source==="ai"?'<div class="qa-ev"><span class="qa-found-k">Evidence</span><span>'+esc(c.evidence)+'</span></div>':"")+
      (c&&c.facts?'<div class="qa-ev"><span class="qa-found-k">'+pn()+'</span><span>'+esc(c.facts)+'</span></div>':"")+
      '<p class="qa-verify">'+esc(it.verify)+'</p>'+
      '<div class="qa-foot"><span class="qa-st">'+esc(msg)+'</span>'+(by?'<span class="qa-by" title="'+esc(c&&c.by||"")+'">'+by+'</span>':"")+
        '<button type="button" class="qa-na" aria-pressed="'+na+'" data-act="na" title="Does not apply to this campaign">N/A</button></div>'+
    '</div>'+
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
      if(S.fStatus!=="all") return st===S.fStatus;
      return true;
    });
    if(!rows.length) return;
    var done=b.total-b.pending, closed=!!S.closed[sec], pct=b.total?Math.round(100*done/b.total):0;
    html+='<section class="pm-panel qa-sec'+(closed?" closed":"")+'" data-sec="'+esc(sec)+'">'+
      '<button type="button" class="qa-sec-h" data-toggle="'+esc(sec)+'" aria-expanded="'+(!closed)+'">'+
        '<span class="qa-chev"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span><span class="qa-sec-t">'+esc(sec)+'</span>'+
        (b.fail?'<span class="qa-pill bad">'+b.fail+' flagged</span>':"")+
        '<span class="qa-sec-n"><b>'+done+'</b> of '+b.total+' answered</span><span class="qa-mini"><i style="width:'+pct+'%"></i></span></button>'+
      '<div class="qa-rows">'+rows.map(rowHtml).join("")+'</div></section>';
  });
  var none={fail:"The AI flagged nothing in this view.",todo:"Nothing here needs a human.",pass:"No check has passed yet.",na:"Nothing is marked N/A."}[S.fStatus];
  $("#sections").innerHTML=html||'<div class="pm-panel"><p class="pm-empty">'+(S.fSev==="all"&&none?none:"Nothing matches this filter.")+'</p></div>';
}
function render(){
  paintHead(); renderBook(); renderReview();
  if(S.review&&S.review.status==="running") return;
  paintVerdict(); paintFilters(); paintSections();
  $("#foot").innerHTML=isMetaId(S.launch&&S.launch.accountId)?
    "Source: Position2's Meta launch checks, a starter written from Meta's own campaign settings (the team's own Meta checks are not written down yet), reviewed by the AI. The AI answers only from what it can read in Meta, on the landing pages and in the campaign book you add; "+
    "Meta does not show whether the dataset received events, Conversions API, event match quality or domain verification to the API, so those stay for a person. Exact Meta rules outrank the AI, and your click outranks both.":
    "Source: the PPC team's Campaign Book checks, reviewed by the AI. The AI answers only from what it can read in Google Ads, on the landing pages and in the campaign book you add; "+
    "Google Ads does not show some account switches to the API, so those stay for a person. Exact Google Ads rules outrank the AI, and your click outranks both. "+
    "The campaign book is for Search campaigns: a Performance Max or Demand Gen campaign is not compared with one, and the checks that would use it are judged on the spot from Google Ads.";
}
function refresh(id){
  // After one check changes: repaint that row, the verdict, the section headers and the filter counts.
  var el=document.getElementById("chk-"+id), it=S.items[id];
  if(el&&it){var t=document.createElement("div");t.innerHTML=rowHtml(it);el.replaceWith(t.firstChild);}
  paintVerdict(); paintFilters(); paintHeads();
}
function paintHeads(){
  var by={}; S.summary.bySection.forEach(function(b){by[b.section]=b;});
  document.querySelectorAll(".qa-sec").forEach(function(sec){
    var b=by[sec.dataset.sec]; if(!b) return;
    var h=sec.querySelector(".qa-sec-h"), done=b.total-b.pending;
    h.querySelector(".qa-sec-n").innerHTML="<b>"+done+"</b> of "+b.total+" answered";
    h.querySelector(".qa-mini i").style.width=(b.total?Math.round(100*done/b.total):0)+"%";
    var pill=h.querySelector(".qa-pill"), t=b.fail?b.fail+" flagged":"";
    if(t&&!pill){pill=document.createElement("span");pill.className="qa-pill bad";h.insertBefore(pill,h.querySelector(".qa-sec-n"));}
    if(pill){ if(t) pill.textContent=t; else pill.remove(); }
  });
}

/* ── A person confirming or overruling ────────────────────── */
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
    if(S.qcs[S.campaignId]) S.qcs[S.campaignId].summary=S.summary;                     // the tabs and a later switch back read this
    if(q){ refresh(id); return save(id,q.status,q.note); }
    refresh(id); paintCampaignBadge(); paintTabs();
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
        failedCount:S.summary.failed.length,failedBlocking:S.summary.failedBlocking.length};
  paintCampaigns();
}
function wrongNote(id){
  // A wrong mark is saved with its reason: the AI's own finding where there is one.
  var c=S.checks[id], f=c&&c.found?c.found.replace(/^The AI's answer could not be tied to the evidence[^.]*\. It said: /,""):"";
  return ("Marked wrong"+(f?": "+f:"")).slice(0,400);
}
$("#sections").addEventListener("click",function(e){
  var head=e.target.closest("[data-toggle]");
  if(head){var k=head.dataset.toggle; S.closed[k]=!S.closed[k]; return paintSections();}
  var b=e.target.closest("[data-act]"); if(!b) return;
  var id=b.closest(".qa-row").dataset.id, st=rowState(id), act=b.dataset.act, c=S.checks[id], mine=c&&c.source==="person";
  if(act==="pass"){
    if(st==="pass"&&mine) save(id,null,"");                 // undo my answer: back to the AI's
    else save(id,"pass","");                                // confirm it right (or overrule a flag)
  }else if(act==="na"){
    if(st==="na"&&mine) save(id,null,""); else save(id,"na","");
  }else if(act==="fail"){
    if(st==="fail"&&mine) save(id,null,""); else save(id,"fail",wrongNote(id));
  }
});

/* ── The campaign book ────────────────────────────────────── */
function takesBook(cid){var q=S.qcs[cid]; return !(q&&q.launch&&q.launch.bookApplies===false);}       // the campaign book is for Search campaigns
function renderBook(){
  if(S.launch&&S.launch.bookApplies===false){
    var ign=S.launch.bookIgnored;
    $("#bookBox").innerHTML='<section class="pm-panel qa-book"><div class="qa-book-t"><b>Campaign book</b><span class="qa-book-s">Not used for this campaign type</span></div>'+
      '<p>'+esc(S.launch.bookNote)+'</p>'+
      (ign?'<p>A campaign book saved earlier on this QC is ignored for this campaign.</p><div class="qa-book-row"><button type="button" class="pm-btn-ghost qa-danger" id="bookRemove">Remove the saved book</button></div>':"")+'</section>';
    return;
  }
  var b=S.launch&&S.launch.book, busy=S.bookBusy;
  var status=busy?'<span class="qa-book-s">'+esc(busy)+'</span>':
    b?'<span class="qa-book-s ok"><b>'+esc(b.filename||"Campaign book")+'</b> · '+b.fields+' field'+(b.fields===1?"":"s")+' read'+(b.uploadedBy?' · added by '+esc(who(b.uploadedBy)):"")+(b.uploadedAt?' · '+esc(rel(b.uploadedAt)):"")+'</span>':
    '<span class="qa-book-s">Not added</span>';
  var n=S.sel.filter(takesBook).length, skipped=S.sel.length-n;
  var all=n>1?'<label class="qa-book-all"><input type="checkbox" id="bookAll"'+(S.bookAll?" checked":"")+(busy?" disabled":"")+'> Apply to all '+n+' selected campaigns'+
    (skipped?' ('+skipped+' other type'+(skipped===1?"":"s")+' do not use a book)':"")+'</label>':"";
  $("#bookBox").innerHTML='<section class="pm-panel qa-book"><div class="qa-book-t"><b>Campaign book</b>'+status+'</div>'+
    '<p>'+(b?'The AI compares the campaign with this book on bid strategy, devices, genders, household income, locations, age and ad schedule.':
      'Some checks depend on the client\'s plan: bid strategy, devices, genders, household income, locations, age and ad schedule. Add the campaign book as an Excel file or a Google Sheet link and the AI reviews the campaign against it.')+'</p>'+
    '<div class="qa-book-row"><button type="button" class="pm-btn-ghost" id="bookPick"'+(busy?" disabled":"")+'>'+(b?"Replace with a file":"Upload Excel or CSV")+'</button>'+
    '<input type="file" id="bookFile" accept=".xlsx,.csv" hidden>'+
    '<input type="text" id="bookLink" maxlength="400" placeholder="or paste a Google Sheet link (shared as anyone with the link can view)" autocomplete="off"'+(busy?" disabled":"")+'>'+
    '<button type="button" class="pm-btn-ghost" id="bookLinkGo"'+(busy?" disabled":"")+'>Add link</button>'+
    (b?'<button type="button" class="pm-btn-ghost qa-danger" id="bookRemove"'+(busy?" disabled":"")+'>Remove</button>':"")+'</div>'+all+'</section>';
}
function sendBook(body){
  var ids=(S.bookAll&&S.sel.length>1?S.sel.filter(takesBook):[S.campaignId]).map(function(c){return S.qcs[c]&&S.qcs[c].launch&&S.qcs[c].launch.id;}).filter(Boolean);
  if(!ids.length) ids=[S.launchId];
  S.bookBusy="Reading the book…"; renderBook(); say("");
  var notes=[], i=0;
  function next(){
    if(i>=ids.length) return Promise.resolve(true);
    return call("POST",API+"/launches/"+ids[i++]+"/book",body).then(function(r){
      if(!r.ok){ say(fail(r,"The book could not be added.")); return false; }
      S.qcs[r.j.launch.campaignId]=r.j; if(r.j.reviewError&&notes.indexOf(r.j.reviewError)<0) notes.push(r.j.reviewError);
      return next();
    });
  }
  next().then(function(){
    S.bookBusy=""; S.startError=notes[0]||""; if(notes.length) say(notes.map(esc).join("<br>"),"pm-note");
    var cur=S.qcs[S.campaignId]; if(cur&&cur.launch) activate(S.campaignId); else renderBook();
    paintTabs(); pollAll(S.seq);
  }).catch(function(){S.bookBusy=""; renderBook(); say("The server could not be reached. Try again.");});
}
document.addEventListener("click",function(e){
  var id=e.target&&e.target.id;
  if(id==="bookPick"){ $("#bookFile").click(); }
  else if(id==="bookLinkGo"){ var v=($("#bookLink").value||"").trim(); if(!v) return say("Paste the Google Sheet link first."); sendBook({sheetUrl:v}); }
  else if(id==="bookRemove"){
    if(!confirm("Remove the campaign book from this QC? The checks that depend on it go back to needing it.")) return;
    S.bookBusy="Removing…"; renderBook();
    call("DELETE",API+"/launches/"+S.launchId+"/book",{}).then(function(r){ S.bookBusy=""; if(!r.ok){renderBook();return say(fail(r,"That could not be removed."));} open(r.j); });
  }
});
document.addEventListener("change",function(e){
  if(e.target&&e.target.id==="bookAll"){ S.bookAll=e.target.checked; return; }
  if(!e.target||e.target.id!=="bookFile"||!e.target.files||!e.target.files[0]) return;
  var f=e.target.files[0];
  if(f.size>3000000) return say("That file is over 3 MB. Save a smaller copy, or add the Google Sheet link instead.");
  var rd=new FileReader();
  rd.onload=function(){ var m=String(rd.result).split(","); sendBook({filename:f.name,data:m.length>1?m[1]:""}); };
  rd.onerror=function(){ say("That file could not be read."); };
  rd.readAsDataURL(f);
});

/* ── Header actions ───────────────────────────────────────── */
function run(){
  if(!S.launchId||(S.review&&S.review.status==="running")) return;
  $("#runBtn").disabled=true; say("");
  call("POST",API+"/launches/"+S.launchId+"/run",{}).then(function(r){
    if(!r.ok){ $("#runBtn").disabled=false; return say(fail(r,"The review could not be started.")); }
    S.startError=""; open(r.j);
  }).catch(function(){$("#runBtn").disabled=false;say("The server could not be reached. Try again.");});
}
$("#runBtn").addEventListener("click",run);
document.addEventListener("click",function(e){ if(e.target&&e.target.id==="tryRead"){e.preventDefault();run();} });
$("#removeBtn").addEventListener("click",function(){
  if(!S.launch||!confirm('Remove the QC for "'+S.launch.campaignName+'"? It disappears from the list; its answers are kept in the records.')) return;
  var gone=S.campaignId;
  call("DELETE",API+"/launches/"+S.launchId,{}).then(function(r){
    if(!r.ok) return say(fail(r,"That could not be removed."));
    delete S.qcs[gone]; S.sel=S.sel.filter(function(id){return id!==gone;}); S.launchId=""; S.launch=null; S.campaignId="";
    stopPoll(); paintTabs(); paintCampaigns(); sync();
    var next=S.sel.filter(function(id){return S.qcs[id]&&!S.qcs[id].error;})[0];
    if(next){ activate(next); pollAll(S.seq); } else view("empty","Choose campaigns",CHOOSE);
    refreshCampaignList();
  });
});
$("#copyBtn").addEventListener("click",function(){
  var s=S.summary, L=S.launch, lines=["Launch QC (AI review): "+L.campaignName+" | "+L.accountName+" ("+dashed(L.accountId)+")","Verdict: "+s.verdict.text,
    "Answered: "+s.checked+"/"+s.total+" checks"];
  if(s.failed.length){lines.push("","Flagged:"); s.failed.forEach(function(id){var it=S.items[id],c=S.checks[id]||{};
    lines.push("- "+id+" "+it.check+" ("+it.severity+"): "+(c.note||c.found||"")+(c.fix?" Fix: "+c.fix:""));});}
  if(s.pendingBlocking.length){lines.push("","Checks the AI could not verify (a person must look): "+s.pendingBlocking.join(", "));}
  var text=lines.join("\n"), label=$("#copyBtn span");
  var done=function(){label.textContent="Copied";setTimeout(function(){label.textContent="Copy summary";},1600);};
  if(navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done,function(){say("Copying was blocked by the browser.");});
  else{var t=document.createElement("textarea");t.value=text;document.body.appendChild(t);t.select();try{document.execCommand("copy");done();}catch(e){say("Copying was blocked by the browser.");}t.remove();}
});

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search), want=(p.get("account")||"").replace(/[^A-Za-z0-9_+]/g,"");
  S.wantCampaign=(p.get("campaign")||"").replace(/[^0-9,]/g,"");
  if(!want){try{want=localStorage.getItem("qa_account")||"";}catch(e){}}
  view("empty"); campaignsOff("Choose an account first");
  Promise.all([call("GET",API+"/checklist"),call("GET",API+"/checklist?platform=meta")]).then(function(rs){
    if(!rs[0].ok){return say(fail(rs[0],"The checklist could not be loaded."));}
    S.lists.google=rs[0].j; if(rs[1].ok) S.lists.meta=rs[1].j; useList("google");
    scope=window.PMScope.mount($("#acctPick"),{account:want,noGroups:true,
      onLoaded:function(info){G_LIST=info.accounts||[]; M_LIST=info.meta||[]; S.accounts=G_LIST;},
      onChange:function(a,pf){applyScope(a,pf);},
      onError:function(e){
        manualMode((e&&e.message)||"The account list could not be loaded.");
        if(/^\d{10}$/.test(want)){ $("#mId").value=want; }
      }});
  }).catch(function(){say("The server could not be reached. Reload to try again.");});
})();

})();
