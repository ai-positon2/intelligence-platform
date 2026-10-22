/* Paid Marketing: the platform switch (Google | Meta | Both) and the account pickers of the agents that check one account, an MCC or a Meta ad account (Disapproved Ads, Ad Copy Checker,
 * Spend Anomaly, Location Conflicts, KPI Monitoring).
 *
 *   var scope = PMScope.mount(hostEl, {
 *     account: "<id from the URL or the last visit>",
 *     onChange: function(account, platform){...},   // an account was (re)chosen: a Google id, an "mcc-..." group, an act_ id, or "<google or mcc>+<act_>" for Both; "" when none
 *     onLoaded: function(info){...},                // the lists arrived: info = {accounts, groups, meta, metaProblem}
 *     onError: function(err){...}
 *   });
 *   scope.groupOf(id)    the MCC group for an id, or null
 *   scope.select(id)     the account was changed from outside
 *
 * The page asks the server for each half of a "Both" account separately and puts the answers together with PMScope.fetchReport and mergeReports: the accounts of the two reports in one list,
 * their summaries added up, the warnings joined. When one platform cannot be read the other is shown and the page says which is missing.
 * Everything that does not touch the page is a plain function, so tests/test_scope_js.py runs it in Node.
 */
(function(root){
"use strict";

var PLATFORMS = [["google","Google"],["meta","Meta"],["both","Both"]];
var PKEY = "pm_platform";
var NAME = {google:"Google Ads", meta:"Meta Ads"};

function isMeta(id){return /^act_\d+$/.test(String(id||""));}
function isCombo(id){return /^(\d{10}|mcc-[0-9]+(?:-[A-Z]{3})?)\+act_\d+$/.test(String(id||""));}
function partsOf(id){return isCombo(id)?String(id).split("+"):[String(id||"")];}
function comboId(g,m){return g+"+"+m;}
function platformOfId(id){return isCombo(id)?"both":isMeta(id)?"meta":"google";}
function validPlatform(v){return v==="google"||v==="meta"||v==="both";}
function savedPlatform(){try{var v=root.localStorage.getItem(PKEY); return validPlatform(v)?v:"";}catch(e){return "";}}
function savePlatform(v){try{root.localStorage.setItem(PKEY,v);}catch(e){}}

/* ── Putting the two halves of a Both report together ─────────────────────── */
function isNum(v){return typeof v==="number"&&v===v;}
/* Two summaries as one: a number is added, an object of numbers is added key by key, anything else is the first one's. */
function sumSummary(a,b){
  if(!a) return b; if(!b) return a;
  var out={};
  Object.keys(a).concat(Object.keys(b)).forEach(function(k){
    if(k in out) return;
    var x=a[k], y=b[k];
    if(isNum(x)&&isNum(y)) out[k]=Math.round((x+y)*10000)/10000;
    else if(x&&y&&typeof x==="object"&&typeof y==="object"&&!Array.isArray(x)) out[k]=sumSummary(x,y);
    else out[k]=x!==undefined&&x!==null?x:y;
  });
  return out;
}
function oldest(a,b){return !a?b:!b?a:(new Date(a)<=new Date(b)?a:b);}
function mergeReports(g,m){
  var ga=g.account||{}, ma=m.account||{};
  var accounts=(g.accounts||[]).concat(m.accounts||[]);
  var out={};
  Object.keys(m).forEach(function(k){out[k]=m[k];});
  Object.keys(g).forEach(function(k){out[k]=g[k];});                                  // what only one half has (rules, saved settings, patterns) is kept, Google's first
  out.account={id:comboId(ga.id,ma.id),name:ga.name===ma.name?ga.name:(ga.name||"")+" + "+(ma.name||""),currency:ga.currency||ma.currency||"USD",platform:"both",isGroup:true,accountCount:accounts.length};
  out.accounts=accounts;
  out.summary=sumSummary(g.summary,m.summary);
  out.warnings=(g.warnings||[]).concat(m.warnings||[]);
  if(ga.currency&&ma.currency&&ga.currency!==ma.currency) out.warnings.push("The two platforms bill in different currencies ("+ga.currency+" and "+ma.currency+"), so money figures added up across them are not exact.");
  out.readAt=oldest(g.readAt,m.readAt)||undefined;
  out.generatedAt=oldest(g.generatedAt,m.generatedAt);
  var stale=oldest(g.stale,m.stale); if(stale) out.stale=stale; else delete out.stale;
  return out;
}

/* ── One request, or two for a Both account ───────────────────────────────── */
function getJson(url){
  return root.fetch(url,{credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
/* fetchReport(base, account, refresh): the same answer a single request gives ({ok, status, j}); a Both account is asked for once per platform and the answers merged. */
function fetchReport(base,account,refresh){
  function one(id){return getJson(base+"?account="+encodeURIComponent(id)+(refresh?"&refresh=1":""));}
  if(!isCombo(account)) return one(account);
  var ps=partsOf(account);
  return Promise.all(ps.map(one)).then(function(r){
    var ok=r.filter(function(x){return x.ok;}), bad=r.filter(function(x){return !x.ok;});
    if(!ok.length) return bad[0];
    if(ok.length===2) return {ok:true,status:200,j:mergeReports(r[0].j,r[1].j)};
    var j=Object.assign({},ok[0].j), who=r[0].ok?NAME.meta:NAME.google, why=(bad[0].j&&bad[0].j.error)||("HTTP "+bad[0].status);
    j.warnings=(j.warnings||[]).concat([who+" could not be read, so this leaves it out: "+why]);
    return {ok:true,status:200,j:j};
  });
}

/* ── The control ──────────────────────────────────────────────────────────── */
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
function hueOf(n){var h=0;n=String(n||"");for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return String(n||"").replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w);}).slice(0,2).map(function(w){return w[0];}).join("").toUpperCase()||"·";}
function dashed(id){id=String(id||"");return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
var NONE={value:"",label:"None",sub:"Leave this platform out",avatar:"·",hue:220};

function mount(host,o){
  var st={platform:"google",google:[],groups:[],meta:[],gSel:"",mSel:"",ready:false,metaProblem:""};
  var wrap=document.createElement("div"); wrap.className="pm-plat";
  var sw=root.PMPlatform.switcher(wrap,{allow:["google","meta","both"],value:savedPlatform()||"google",onChange:function(p){st.platform=p; if(st.ready) preselect(); settle(); paint(); emit();}});
  var pickers=document.createElement("div"); pickers.className="pm-plat-pick";
  var hostG=document.createElement("div"), hostM=document.createElement("div");
  pickers.appendChild(hostG); pickers.appendChild(hostM);
  var note=document.createElement("p"); note.className="pm-plat-note"; note.hidden=true;
  wrap.appendChild(pickers); wrap.appendChild(note); host.appendChild(wrap);
  st.platform=sw.value();

  var pg=root.PMPicker(hostG,{label:"Google Ads account",search:true,onChange:function(v){st.gSel=v; emit();}});
  var pm=root.PMPicker(hostM,{label:"Meta ad account",search:true,onChange:function(v){st.mSel=v; emit();}});

  function groupOf(id){return st.groups.filter(function(g){return g.id===id;})[0]||null;}
  function metaOf(id){return st.meta.filter(function(a){return a.id===id;})[0]||null;}
  function googleOf(id){return st.google.filter(function(a){return a.id===id;})[0]||null;}
  function gItems(both){
    var list=st.google, items=[both?NONE:{value:"",label:"Choose an account",sub:list.length+" accounts"+(st.groups.length?" · "+st.groups.length+" MCCs to check together":""),avatar:"?",hue:200}], seen={};
    st.groups.forEach(function(g){
      var head=g.name+" MCC";
      items.push({value:g.id,label:"All of "+g.name,avatar:inits(g.name),hue:hueOf(g.name),group:head,badge:MCC_BADGE,sub:g.accountIds.length+" accounts checked together"});
      list.filter(function(a){return a.group===g.id;}).forEach(function(a){seen[a.id]=1; items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:head,sub:dashed(a.id)});});
    });
    list.filter(function(a){return !seen[a.id];}).forEach(function(a){items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:st.groups.length?"Other accounts":"",sub:dashed(a.id)});});
    return items;
  }
  function mItems(both){
    var items=[both?NONE:{value:"",label:"Choose an account",sub:st.meta.length+" accounts",avatar:"?",hue:200}];
    st.meta.forEach(function(a){items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),sub:a.id+" · "+(a.currency||"")});});
    return items;
  }
  function paint(){
    var p=st.platform;
    hostG.hidden=p==="meta"; hostM.hidden=p==="google";
    pg.set(gItems(p==="both"),st.gSel,!st.google.length);
    pm.set(mItems(p==="both"),st.mSel,!st.meta.length);
    sw.set(p);
    note.hidden=!(st.metaProblem&&p!=="google"); note.textContent=st.metaProblem&&p!=="google"?st.metaProblem:"";
  }
  /* the account the choice means */
  function chosen(){
    var p=st.platform, g=st.gSel, m=st.mSel;
    if(p==="google") return g; if(p==="meta") return m;
    return g&&m?comboId(g,m):(g||m);
  }
  function emit(){
    var a=chosen();
    try{root.localStorage.setItem(PKEY,st.platform);}catch(e){}
    o.onChange&&o.onChange(a,st.platform);
  }
  /* switching to a platform that has nothing chosen: the same client on it, when one account of that name is plain to see */
  function preselect(){
    var P=root.PMPlatform;
    if(st.platform!=="google"&&!st.mSel&&st.gSel&&googleOf(st.gSel)){var c=P.counterpart(googleOf(st.gSel),st.meta); if(c) st.mSel=c.id;}
    if(st.platform!=="meta"&&!st.gSel&&st.mSel&&metaOf(st.mSel)){var c2=P.counterpart(metaOf(st.mSel),st.google); if(c2) st.gSel=c2.id;}
  }
  /* keep what is chosen when it still exists on the platform shown, else forget it */
  function settle(){
    if(st.gSel&&!googleOf(st.gSel)&&!groupOf(st.gSel)) st.gSel="";
    if(st.mSel&&!metaOf(st.mSel)) st.mSel="";
  }
  function take(id){
    if(!id) return;
    if(isCombo(id)){var ps=partsOf(id); st.gSel=ps[0]; st.mSel=ps[1]; st.platform="both";}
    else if(isMeta(id)){st.mSel=id; if(st.platform==="google") st.platform="meta";}
    else {st.gSel=id; if(st.platform==="meta") st.platform="google";}
  }
  paint();
  var q=[getJson("/p2/paid-marketing/api/segments/accounts"),getJson("/p2/paid-marketing/api/gads/accounts?platform=meta").catch(function(){return {ok:false,j:{}};})];
  Promise.all(q).then(function(rs){
    var g=rs[0], m=rs[1];
    if(!g.ok){o.onError&&o.onError(new Error((g.j&&g.j.error)||"The account list could not be read."),g); return;}
    st.google=g.j.accounts||[]; st.groups=g.j.groups||[];
    st.meta=m.ok?(m.j.accounts||[]).filter(function(a){return a.platform==="meta"||isMeta(a.id);}):[];
    st.metaProblem=m.ok?((m.j&&m.j.metaProblem)||""):"Meta ad accounts could not be listed, so only Google Ads can be checked.";
    st.ready=true;
    take(o.account); settle(); paint();
    o.onLoaded&&o.onLoaded({accounts:st.google,groups:st.groups,meta:st.meta,metaProblem:st.metaProblem});
    emit();
  }).catch(function(e){o.onError&&o.onError(e);});
  return {groupOf:groupOf, platform:function(){return st.platform;}, account:chosen,
    select:function(id){take(id); settle(); paint(); emit();}};
}

var api={PLATFORMS:PLATFORMS,PKEY:PKEY,isMeta:isMeta,isCombo:isCombo,partsOf:partsOf,comboId:comboId,platformOfId:platformOfId,sumSummary:sumSummary,mergeReports:mergeReports,
  fetchReport:fetchReport,mount:mount};
if(typeof module!=="undefined"&&module.exports) module.exports=api; else root.PMScope=api;
})(typeof window!=="undefined"?window:globalThis);
