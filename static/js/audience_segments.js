/* Audience Segment Analysis (Paid Marketing Agents).
 *
 * Pick a Google Ads account (or an MCC), a Meta ad account, or one of each (Google | Meta | Both), and a date range. The server reads how every audience the account uses performs
 * (tracker/paid_audiences.py), judges each against the account's own average for the same campaign type and says what to do given how
 * it is attached and how its campaigns bid (paid_audiences_insights.py). This page draws it. It never works out a verdict, an index or
 * a suggestion for itself: the server's are what is shown.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/audiences";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}

var VERDICTS = {star:["Strong","star"],good:["Above average","good"],par:["On par","par"],below:["Below average","below"],weak:["Weak","weak"],
                leak:["Leaking spend","leak"],thin:["Too little data","thin"],none:["Not judged","thin"]};
var MODES = {observation:"Observation",targeting:"Targeting",mixed:"Mixed"};
/* On Meta an ad set on Advantage+ audience treats its audiences as suggestions, not limits. */
function modeText(mode,platform){return platform==="meta"&&mode==="observation"?"Suggestion":MODES[mode];}
function platName(p){return p==="meta"?"Meta Ads":p==="both"?"Google Ads and Meta Ads":"Google Ads";}
function showBid(){return !!(S.report&&S.report.account.platform!=="meta");}
var PL = window.PMPlatform;
var S = {accounts:[], groups:[], metaAccounts:[], metaProblem:"", platform:"google", g:"", m:"", pickNote:"", account:"", currency:"USD", preset:"30", from:"", to:"", report:null, seq:0, type:"All",
         tbl:{sort:{col:"cost",dir:-1}, filter:"", verdict:"all", kind:"all", limit:50, open:{}},
         lst:{q:"", kind:"all", serve:"all", use:"all", origin:"all"}};

/* ── Formats ──────────────────────────────────────────────── */
var nf={};
function money(v,dec){
  if(v==null) return "Na";
  var k=S.currency+dec;
  if(!nf[k]){try{nf[k]=new Intl.NumberFormat("en-US",{style:"currency",currency:S.currency,minimumFractionDigits:dec,maximumFractionDigits:dec});}
             catch(e){nf[k]={format:function(x){return "$"+x.toFixed(dec);}};}}
  return nf[k].format(v);
}
function int(v){return v==null?"Na":Math.round(v).toLocaleString("en-US");}
function pc(v,d){return v==null?"Na":(v*100).toFixed(d==null?1:d)+"%";}
function conv(v){return v==null?"Na":(Math.abs(v-Math.round(v))<0.05?Math.round(v).toLocaleString("en-US"):v.toLocaleString("en-US",{minimumFractionDigits:1,maximumFractionDigits:1}));}
function x2(v){return v==null?"Na":v.toFixed(2)+"x";}
function signed(a){return a==null?"":a===0?"Hold":(a>0?"+":"")+Math.round(a*100)+"%";}
function bidText(r){return r.bid!=null?signed(r.bid):r.bidVaries?"Varies":"None";}
function longDate(s){return new Date(s+"T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"});}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}

/* ── Dates ────────────────────────────────────────────────── */
function ymd(d){var z=d.getTimezoneOffset()*60000;return new Date(d-z).toISOString().slice(0,10);}
function rangeFor(p){
  var t=new Date(), y=new Date(t.getFullYear(),t.getMonth(),t.getDate()-1);
  if(p==="7"||p==="30"){var s=new Date(y);s.setDate(s.getDate()-(+p-1));return [ymd(s),ymd(y)];}
  if(p==="mtd") return [ymd(new Date(t.getFullYear(),t.getMonth(),1)),ymd(t)];
  if(p==="last") return [ymd(new Date(t.getFullYear(),t.getMonth()-1,1)),ymd(new Date(t.getFullYear(),t.getMonth(),0))];
  return [S.from,S.to];
}
var presetsEl=$("#presets");
function paintPresets(){
  var on=null;
  presetsEl.querySelectorAll("button").forEach(function(b){var y=b.dataset.p===S.preset;b.setAttribute("aria-checked",String(y));if(y)on=b;});
  var gl=presetsEl.querySelector(".cf-seg-glow");
  if(on){gl.style.width=on.offsetWidth+"px";gl.style.transform="translateX("+(on.offsetLeft-4)+"px)";}
  $("#custom").hidden=S.preset!=="custom";
}
presetsEl.addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return;
  S.preset=b.dataset.p;
  if(S.preset!=="custom"){var r=rangeFor(S.preset);S.from=r[0];S.to=r[1];}
  $("#from").value=S.from;$("#to").value=S.to;
  paintPresets(); if(S.preset!=="custom") run();
});
window.addEventListener("resize",paintPresets);
$("#from").addEventListener("change",function(){S.from=this.value;});
$("#to").addEventListener("change",function(){S.to=this.value;});
$("#go").addEventListener("click",function(){run();});

/* ── Platform and accounts (the same pickers as Geo Performance) ─── */
var picker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){
  S.g=v; if(S.platform==="google") S.m=""; if(S.platform==="both"&&v&&!S.m) autoMeta(); recompute();
}});
var mpicker = window.PMPicker($("#metaPick"),{label:"Meta ad account",search:true,onChange:function(v){
  S.m=v; if(S.platform==="meta") S.g=""; recompute();
}});
var switcher = PL.switcher($("#platPick"),{allow:["google","meta","both"],value:"google",onChange:function(p){
  S.platform=p; if(p!=="meta"&&!S.g&&S.m) autoGoogle(); if(p!=="google"&&!S.m&&S.g) autoMeta(); paintPickers(); recompute();
}});
function googleAcct(id){return S.accounts.filter(function(a){return a.id===id;})[0]||null;}
function metaAcct(id){return S.metaAccounts.filter(function(a){return a.id===id;})[0]||null;}
function autoMeta(){var g=googleAcct(S.g), c=g?PL.counterpart(g,S.metaAccounts):null; if(c) S.m=c.id;}
function autoGoogle(){var m=metaAcct(S.m), c=m?PL.counterpart(m,S.accounts):null; if(c) S.g=c.id;}
function chosenAccount(){
  var p=S.platform;
  if(p==="google") return S.g;
  if(p==="meta") return S.m;
  if(S.g&&S.m&&googleAcct(S.g)&&metaAcct(S.m)&&googleAcct(S.g).currency===metaAcct(S.m).currency) return S.g+"+"+S.m;
  return S.g||S.m;
}
function recompute(){
  S.account=chosenAccount()||"";
  try{localStorage.setItem("au_account",S.account);}catch(e){}
  var note="";
  if(S.platform==="both"&&S.g&&S.m&&googleAcct(S.g)&&metaAcct(S.m)&&googleAcct(S.g).currency!==metaAcct(S.m).currency)
    note="These two accounts bill in different currencies, so they cannot be added together. Showing the Google account.";
  if(S.platform!=="google"&&S.metaProblem) note=S.metaProblem+(S.platform==="both"?" Showing Google only.":"");
  S.pickNote=note;
  run();
}
function paintPickers(){
  $("#acctPick").hidden=S.platform==="meta"; $("#metaPick").hidden=S.platform==="google";
  var both=S.platform==="both";
  var items=[{value:"",label:both?"None":"Choose an account",sub:both?"Leave Google out":S.metaAccounts.length+" accounts",avatar:both?"–":"?",hue:200}];
  S.metaAccounts.forEach(function(a){items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),sub:a.id+" · "+a.currency});});
  if(!metaAcct(S.m)) S.m="";
  mpicker.set(items,S.m,!S.metaAccounts.length);
  setAccounts(S.accounts,S.groups);
}
function groupOf(id){return S.groups.filter(function(g){return g.id===id;})[0]||null;}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function setAccounts(list, groups){
  S.accounts=list; S.groups=groups||[];
  var both=S.platform==="both";
  var items=[{value:"",label:both?"None":"Choose an account",sub:both?"Leave Meta out":list.length+" accounts"+(S.groups.length?" · "+S.groups.length+" MCCs to combine":""),avatar:both?"–":"?",hue:200}], seen={};
  (both?[]:S.groups).forEach(function(g){
    var head=g.name+" MCC";
    items.push({value:g.id,label:"All of "+g.name,avatar:inits(g.name),hue:hueOf(g.name),group:head,badge:MCC_BADGE,sub:g.accountIds.length+" accounts combined · "+g.currency});
    list.filter(function(a){return a.group===g.id;}).forEach(function(a){
      seen[a.id]=1; items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:head,sub:dashed(a.id)+" · "+a.currency});
    });
  });
  list.filter(function(a){return !seen[a.id];}).forEach(function(a){
    items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:S.groups.length?"Other accounts":"",sub:dashed(a.id)+" · "+a.currency});
  });
  if(!list.some(function(a){return a.id===S.g;})&&!groupOf(S.g)) S.g="";
  picker.set(items,S.g);
}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}

function run(refresh){
  if(!S.account){show("empty");setMsg(esc(S.pickNote||""),"pm-note");return;}
  if(!S.from||!S.to){setMsg("Pick a start and an end date.");return;}
  if(S.from>S.to){setMsg("The start date is after the end date.");return;}
  var seq=++S.seq, g=groupOf(S.account), q="?account="+encodeURIComponent(S.account)+"&from="+S.from+"&to="+S.to;
  try{history.replaceState(null,"",location.pathname+q);}catch(e){}
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 30 seconds.":esc(S.pickNote||""),"pm-note");
  show("skel");
  getJson(API+"/report"+q+(refresh===true?"&refresh=1":"")).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg(esc(S.pickNote||""),"pm-note");
    if(!r.ok){
      show(S.report?"out":"empty");
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j; S.currency=r.j.account.currency||"USD";
    if(S.type!=="All"&&r.j.types.indexOf(S.type)<0) S.type="All";
    S.tbl.filter=""; S.tbl.verdict="all"; S.tbl.kind="all"; S.tbl.limit=50; S.tbl.open={}; $("#filter").value="";
    S.lst={q:"", kind:"all", serve:"all", use:"all", origin:"all"}; $("#lfilter").value="";
    show("out"); render();
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
function view(){return S.report&&S.report.views[S.type];}
function render(){
  var rep=S.report, warn=rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"", pn=platName(rep.account.platform);
  setMsg(warn||esc(S.pickNote||""),"pm-note");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts combined</span>':"")+'&nbsp;· '+esc(longDate(rep.range.from))+
    ' to '+esc(longDate(rep.range.to))+' ('+rep.range.days+' day'+(rep.range.days===1?"":"s")+') · read from '+pn+' '+esc(ago(rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">'+pn+' is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintKinds(); paintTypes(); paintInsights(); paintKindChips(); paintVerdicts(); paintTable(); paintLists(); paintExcl();
  var meta=rep.account.platform==="meta", both=rep.account.platform==="both";
  $("#foot").innerHTML="Source: "+pn+", saved so the same report opens instantly. An audience is judged against this account's own average for the same kind of campaign: "+
    "the <b>efficiency index</b> is "+(view()&&view().basis==="roas"?"its return on ad spend":"the account's cost per conversion")+" compared with the account's, and 100 is average. "+
    "Nothing is judged under 5 conversions. "+
    (meta?"Meta reports results per ad set, not per audience, so an audience's numbers are those of the ad sets aimed at it, and an ad set aimed at two audiences counts under both. Meta has no audience bid adjustment, so the advice is an ad set of its own, a budget, or the targeting. "
         :"A <b>suggested bid adjustment</b> is 80% of the gap in steps of 5%, shown only where the campaign takes one: Smart Bidding, Demand Gen and Performance Max "+
          "ignore audience bid adjustments. "+(both?"On Meta there is no such adjustment, and an audience's numbers are those of the ad sets aimed at it. ":""))+
    "One click can belong to several audiences, so the shares do not add to 100%. "+(meta?"":"Performance Max audience signals report no results in Google's API, so they are not here. ")+
    "Nothing is changed in "+(meta?"Meta Ads":both?"Google Ads or Meta Ads":"Google Ads")+".";
}
function paintKpis(){
  var v=S.report.views.All, judged=v.rows.filter(function(r){return r.verdict!=="thin"&&r.verdict!=="none";}).length;
  var up=v.rows.filter(function(r){return r.verdict==="star"||r.verdict==="good";}).length, down=v.rows.filter(function(r){return r.verdict==="weak"||r.verdict==="below"||r.verdict==="leak";}).length;
  var bad=S.report.lists.filter(function(l){return l.used&&!l.serves;}).length;
  var items=[["Audiences in use",int(v.rows.length),"with results in this range"],["Judged",int(judged),"of "+int(v.rows.length)+" have 5 or more conversions"],
    ["Above average",int(up),up?"good":"none"],["Below average",int(down),down?"warn":"none"],["Lists that cannot serve",int(bad),bad?"warn":"good"]];
  $("#kpis").innerHTML=items.map(function(c){
    var cls=c[2]==="good"||c[2]==="warn"?c[2]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div>'+(cls||c[2]==="none"?'':'<div class="s">'+esc(c[2])+'</div>')+'</div>';
  }).join("");
}
function paintKinds(){
  var kt=S.report.kindTotals||[];
  $("#kinds").innerHTML=kt.length?kt.map(function(k){
    var w=k.index==null?0:Math.min(100,Math.round(k.index/2)), cls=k.index==null?"thin":k.index>=110?"good":k.index<90?"bad":"par";
    return '<button type="button" class="au-kind'+(S.tbl.kind===k.kind?" on":"")+'" data-k="'+k.kind+'"><b>'+esc(k.label)+'</b><span class="n">'+k.count+' audience'+(k.count===1?"":"s")+'</span>'+
      '<span class="au-idx '+cls+'">'+(k.index==null?"Too little data":"Index "+k.index)+'</span><i class="au-meter"><u class="'+cls+'" style="width:'+w+'%"></u><s></s></i>'+
      '<span class="n">'+money(k.cost,0)+' · '+conv(k.conversions)+' conv.'+(k.roas!=null?' · '+x2(k.roas):k.cpa!=null?' · '+money(k.cpa,2)+' each':"")+'</span></button>';
  }).join(""):'<p class="pm-empty">No audience has results in this range.</p>';
}
$("#kinds").addEventListener("click",function(e){
  var b=e.target.closest("[data-k]"); if(!b) return;
  S.tbl.kind=S.tbl.kind===b.dataset.k?"all":b.dataset.k; S.tbl.limit=50; paintKinds(); paintKindChips(); paintTable();
  $("#tableCard").scrollIntoView({behavior:"smooth",block:"start"});
});
function paintTypes(){
  var rep=S.report, chips=["All"].concat(rep.types);
  $("#types").innerHTML=chips.map(function(t){
    return '<button type="button" class="pm-chip'+(t===S.type?" on":"")+'" data-t="'+esc(t)+'" aria-pressed="'+(t===S.type)+'">'+esc(t)+'</button>';
  }).join("");
}
$("#types").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.type=b.dataset.t; S.tbl.verdict="all"; S.tbl.limit=50; S.tbl.open={}; render();
});
var GROUPS=[["action","Do this"],["watch","Keep an eye on"],["info","Good to know"]];
function paintInsights(){
  var items=(view()&&view().insights)||[], html="";
  $("#insNote").textContent=items.length?"Every one is computed from the tables below":"";
  if(!items.length) html='<p class="pm-empty">Nothing stands out in this view.</p>';
  GROUPS.forEach(function(g){
    var list=items.filter(function(i){return i.severity===g[0];});
    if(!list.length) return;
    html+='<h3 class="sg-gh">'+g[1]+'</h3><div class="sg-cards">'+list.map(function(i){
      var link=i.keys&&i.keys.length?' data-key="'+esc(i.keys[0])+'" tabindex="0" role="link"':"";
      return '<article class="sg-card '+i.severity+(link?" link":"")+'"'+link+'><div class="sg-card-top"><span class="sg-tag">'+esc({push:"Raise",trim:"Lower",exclude:"Exclude",kinds:"Kinds",
        smart:"Smart Bidding",meta:"Meta Ads",thin:"Thin data",lists:"Lists",unused:"Unused lists",exclusions:"Exclusions",pmax:"Not included",nodata:"No data"}[i.kind]||i.kind)+'</span>'+
        (i.severity==="info"?"":'<span class="sg-conf '+(i.confidence==="high"?"high":"")+'">'+(i.confidence==="high"?"High confidence":i.confidence==="medium"?"Medium confidence":"Directional")+'</span>')+'</div>'+
        '<h4>'+esc(i.headline)+'</h4><p class="ev">'+esc(i.evidence)+'</p><p class="ac"><b>'+(i.severity==="info"?"Note":"Do")+'</b> '+esc(i.action)+'</p></article>';
    }).join("")+'</div>';
  });
  $("#insList").innerHTML=html;
}
function goTo(key){
  var r=(view().rows||[]).filter(function(x){return x.key===key;})[0]; if(!r) return;
  S.tbl.filter=r.label; S.tbl.verdict="all"; S.tbl.kind="all"; S.tbl.open[key]=true; $("#filter").value=r.label; paintKinds(); paintKindChips(); paintVerdicts(); paintTable();
  $("#tableCard").scrollIntoView({behavior:"smooth",block:"start"});
}
$("#insList").addEventListener("click",function(e){var c=e.target.closest("[data-key]"); if(c) goTo(c.dataset.key);});
$("#insList").addEventListener("keydown",function(e){ if(e.key==="Enter"||e.key===" "){var c=e.target.closest("[data-key]"); if(c){e.preventDefault();c.click();}} });

/* ── The audience table ───────────────────────────────────── */
var COLS=[["label","Audience"],["cost","Spend"],["pctCost","% spend"],["clicks","Clicks"],["ctr","CTR"],["conversions","Conv."],["cvr","Conv. rate"],["cpa","CPA"],["roas","ROAS"],
          ["index","Index"],["verdict","Verdict"],["mode","Mode"],["bid","Bid now"],["adjustment","Suggested"]];
function paintKindChips(){
  var v=view(); if(!v) return;
  var by={}; v.rows.forEach(function(r){by[r.kind]=(by[r.kind]||0)+1;});
  var kinds=S.report.kinds.filter(function(k){return by[k.key];});
  $("#kindChips").innerHTML=[["all","All kinds",v.rows.length]].concat(kinds.map(function(k){return [k.key,k.label,by[k.key]];})).map(function(c){
    return '<button type="button" class="pm-chip'+(S.tbl.kind===c[0]?" on":"")+'" data-kc="'+c[0]+'" aria-pressed="'+(S.tbl.kind===c[0])+'">'+esc(c[1])+' <span class="n">'+c[2]+'</span></button>';
  }).join("");
}
$("#kindChips").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.tbl.kind=S.tbl.kind===b.dataset.kc?"all":b.dataset.kc; S.tbl.limit=50; paintKinds(); paintKindChips(); paintTable();
});
function paintVerdicts(){
  var v=view(); if(!v) return;
  var counts={}; v.rows.forEach(function(r){counts[r.verdict]=(counts[r.verdict]||0)+1;});
  var chips=[["all","Any verdict",v.rows.length]].concat(Object.keys(VERDICTS).filter(function(k){return counts[k];}).map(function(k){return [k,VERDICTS[k][0],counts[k]];}));
  $("#verdicts").innerHTML=chips.map(function(c){
    return '<button type="button" class="pm-chip'+(S.tbl.verdict===c[0]?" on":"")+'" data-v="'+c[0]+'" aria-pressed="'+(S.tbl.verdict===c[0])+'">'+esc(c[1])+' <span class="n">'+c[2]+'</span></button>';
  }).join("");
}
$("#verdicts").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.tbl.verdict=S.tbl.verdict===b.dataset.v?"all":b.dataset.v; S.tbl.limit=50; paintVerdicts(); paintTable();
});
$("#filter").addEventListener("input",function(){S.tbl.filter=this.value; S.tbl.limit=50; paintTable();});
function cell(c,r){
  switch(c){
    case "cost": return money(r.cost,0); case "pctCost": return pc(r.pctCost,1); case "clicks": return int(r.clicks); case "ctr": return pc(r.ctr,2);
    case "conversions": return conv(r.conversions); case "cvr": return pc(r.cvr,2); case "cpa": return money(r.cpa,2); case "roas": return x2(r.roas);
    case "index": return r.index==null?"":String(r.index);
  }
  return "";
}
function sorted(rows){
  var st=S.tbl.sort; if(!st) return rows;
  return rows.slice().sort(function(a,b){
    var x=a[st.col], y=b[st.col];
    if(x==null&&y==null) return 0; if(x==null) return 1; if(y==null) return -1;
    if(typeof x==="string") return st.dir*x.localeCompare(y);
    return st.dir*(x-y);
  });
}
function paintTable(){
  var v=view(); if(!v) return;
  var showRoas=v.total.value>0, term=S.tbl.filter.trim().toLowerCase();
  var rows=sorted(v.rows.filter(function(r){
    if(S.tbl.verdict!=="all"&&r.verdict!==S.tbl.verdict) return false;
    if(S.tbl.kind!=="all"&&r.kind!==S.tbl.kind) return false;
    return !term||(r.label+" "+r.kindLabel+" "+(r.account||"")).toLowerCase().indexOf(term)>=0;
  }));
  var cols=COLS.filter(function(c){return (c[0]!=="roas"||showRoas)&&((c[0]!=="bid"&&c[0]!=="adjustment")||showBid());}), st=S.tbl.sort;
  $("#tblNote").textContent=S.type==="All"?"all campaign types":S.type+" campaigns";
  var head=cols.map(function(c){
    var arw=st&&st.col===c[0]?'<span class="arw">'+(st.dir>0?"▲":"▼")+'</span>':"";
    return '<th class="sortable'+(c[0]!=="label"?" r":"")+'" data-col="'+c[0]+'">'+esc(c[1])+arw+'</th>';
  }).join("");
  var maxShare=v.rows.reduce(function(m,r){return Math.max(m,r.pctCost||0);},0)||1;
  var body=rows.slice(0,S.tbl.limit).map(function(r){
    var open=!!S.tbl.open[r.key];
    var tr='<tr class="au-r '+r.verdict+(open?" open":"")+'" data-key="'+esc(r.key)+'" tabindex="0"><td class="sg-lab"><span class="au-chev"></span>'+esc(r.label)+
      '<span class="gp-sub">'+esc(r.detail)+(r.account?" · "+esc(r.account):"")+'</span></td>'+cols.slice(1).map(function(c){
        if(c[0]==="pctCost") return '<td class="r sg-pc"><i style="width:'+Math.min(100,(r.pctCost||0)/maxShare*100).toFixed(1)+'%"></i><span>'+pc(r.pctCost,1)+'</span></td>';
        if(c[0]==="verdict") return '<td class="r"><span class="gp-v '+VERDICTS[r.verdict][1]+'" title="'+esc(r.why||"")+'">'+esc(r.verdictLabel)+'</span></td>';
        if(c[0]==="mode") return '<td class="r"><span class="au-mode '+r.mode+'">'+modeText(r.mode,r.platform)+'</span></td>';
        if(c[0]==="bid") return '<td class="r num">'+esc(r.platform==="meta"?"–":bidText(r))+'</td>';
        if(c[0]==="adjustment") return '<td class="r"><span class="gp-adj '+(r.adjustment>0?"up":r.adjustment<0?"down":"")+(r.adjustable?"":" off")+'" title="'+
          (r.adjustable?esc(r.confidence||"")+" confidence":r.platform==="meta"?"Meta has no audience bid adjustment":"These campaigns take no audience bid adjustment")+'">'+esc(r.adjustment==null||r.platform==="meta"?"":signed(r.adjustment))+'</span></td>';
        return '<td class="r num">'+esc(cell(c[0],r))+'</td>';
      }).join("")+'</tr>';
    return tr+(open?'<tr class="au-detail"><td colspan="'+cols.length+'">'+detail(r)+'</td></tr>':"");
  }).join("");
  var t=v.total;
  var foot='<tr class="sg-total"><td>Total shown</td>'+cols.slice(1).map(function(c){
    if(["verdict","mode","bid","adjustment","index","pctCost"].indexOf(c[0])>=0) return '<td></td>';
    return '<td class="r num">'+esc(cell(c[0],t))+'</td>';}).join("")+'</tr>';
  $("#tbl").innerHTML='<thead><tr>'+head+'</tr></thead><tbody>'+(body||'<tr><td colspan="'+cols.length+'" class="pm-empty">No audience matches.</td></tr>')+'</tbody><tfoot>'+foot+'</tfoot>';
  var more=$("#more"); more.hidden=rows.length<=S.tbl.limit; more.textContent="Show all "+rows.length+" audiences";
}
function detail(r){
  var what=r.action&&r.action.text?'<p class="au-do '+r.action.kind+'"><b>'+({raise:"Raise",lower:"Lower",exclude:"Exclude",hold:"Hold"}[r.action.kind]||"")+'</b> '+esc(r.action.text)+'</p>':"";
  var why=r.why?'<p class="au-why">'+esc(r.why)+'</p>':"";
  if(r.members&&r.members.length) why='<p class="au-why"><b>'+r.members.length+' audiences always used together:</b> '+r.members.map(esc).join(", ")+
    '. Meta reports results per ad set, and the same ad sets use every one of them, so it cannot tell them apart: they share these numbers.</p>'+why;
  var live=r.uses.filter(function(u){return u.m.impressions>0||u.m.cost>0;}), silent=r.uses.length-live.length;
  return what+why+'<table class="as-tx au-uses"><thead><tr><th>Where it is used</th><th>Mode</th>'+(showBid()?'<th class="r">Bid now</th>':'')+'<th class="r">Spend</th><th class="r">Clicks</th><th class="r">Conv.</th></tr></thead><tbody>'+
    live.map(function(u){
      return '<tr><td>'+esc(u.campaign)+(u.adGroup?' <span class="gp-sub">'+esc(u.adGroup)+(u.platform==="meta"?" (ad set)":"")+'</span>':' <span class="gp-sub">campaign level</span>')+'</td><td><span class="au-mode '+u.mode+'">'+modeText(u.mode,u.platform)+'</span></td>'+
        (showBid()?'<td class="r">'+(u.platform==="meta"?"–":u.bid==null?"None":signed(u.bid))+'</td>':'')+'<td class="r">'+money(u.m.cost,0)+'</td><td class="r">'+int(u.m.clicks)+'</td><td class="r">'+conv(u.m.conversions)+'</td></tr>';
    }).join("")+'</tbody></table>'+(silent?'<p class="au-why">'+silent+' more place'+(silent===1?" it is":"s it is")+' attached had no traffic in this range.</p>':"");
}
$("#tbl").addEventListener("click",function(e){
  var th=e.target.closest("th[data-col]");
  if(th){ var c=th.dataset.col, cur=S.tbl.sort; S.tbl.sort=cur&&cur.col===c?(cur.dir===-1?{col:c,dir:1}:null):{col:c,dir:c==="label"||c==="mode"||c==="verdict"?1:-1}; paintTable(); return; }
  var tr=e.target.closest("tr.au-r"); if(tr){ var k=tr.dataset.key; S.tbl.open[k]=!S.tbl.open[k]; paintTable(); }
});
$("#tbl").addEventListener("keydown",function(e){
  if(e.key!=="Enter"&&e.key!==" ") return; var tr=e.target.closest("tr.au-r"); if(tr){e.preventDefault(); tr.click();}
});
$("#more").addEventListener("click",function(){S.tbl.limit=100000; paintTable();});

/* ── Lists and exclusions ─────────────────────────────────── */
function listMatches(l){
  var f=S.lst;
  if(f.kind!=="all"&&l.kind!==f.kind) return false;
  if(f.serve==="yes"&&!l.serves) return false;
  if(f.serve==="no"&&l.serves) return false;
  if(f.use==="yes"&&!l.used) return false;
  if(f.use==="no"&&l.used) return false;
  if(f.origin==="google"&&!l.readOnly) return false;
  if(f.origin==="own"&&l.readOnly) return false;
  var t=f.q.trim().toLowerCase();
  return !t||[l.label,l.detail,l.account||"",l.status==="CLOSED"?"closed":"open",l.readOnly?"google":"",l.used?"used":"not used"].join(" ").toLowerCase().indexOf(t)>=0;
}
function chipRow(sel,key,defs,counts){
  $(sel).innerHTML=defs.map(function(d){
    return '<button type="button" class="pm-chip'+(S.lst[key]===d[0]?" on":"")+'" data-lf="'+key+'" data-v="'+d[0]+'" aria-pressed="'+(S.lst[key]===d[0])+'">'+esc(d[1])+' <span class="n">'+counts(d[0])+'</span></button>';
  }).join("");
}
function paintListFilters(){
  var ls=S.report.lists||[], n=function(fn){return ls.filter(fn).length;};
  var kinds=S.report.kinds.filter(function(k){return n(function(l){return l.kind===k.key;});});
  chipRow("#lKinds","kind",[["all","All kinds"]].concat(kinds.map(function(k){return [k.key,k.label];})),function(v){return v==="all"?ls.length:n(function(l){return l.kind===v;});});
  chipRow("#lServe","serve",[["all","Any size"],["yes","Can serve"],["no","Cannot serve"]],function(v){return v==="all"?ls.length:n(function(l){return v==="yes"?l.serves:!l.serves;});});
  chipRow("#lUse","use",[["all","Any use"],["yes","Used"],["no","Not used"]],function(v){return v==="all"?ls.length:n(function(l){return v==="yes"?l.used:!l.used;});});
  chipRow("#lOrigin","origin",[["all","Any source"],["own","Yours"],["google","Created by Google"]],function(v){return v==="all"?ls.length:n(function(l){return v==="google"?l.readOnly:!l.readOnly;});});
}
$("#listsCard").addEventListener("click",function(e){
  var b=e.target.closest("[data-lf]"); if(!b) return;
  S.lst[b.dataset.lf]=S.lst[b.dataset.lf]===b.dataset.v?"all":b.dataset.v;                 // a second click on the chosen chip clears it
  paintLists();
});
$("#lfilter").addEventListener("input",function(){S.lst.q=this.value; paintLists();});
function paintLists(){
  if(!S.report) return;
  var all=S.report.lists||[], multi=S.report.account.isGroup, ls=all.filter(listMatches);
  paintListFilters();
  $("#listsNote").textContent=all.length?(ls.length===all.length?all.length+" list"+(all.length===1?"":"s"):ls.length+" of "+all.length+" lists")+" · "+all.filter(function(l){return !l.serves;}).length+" cannot serve properly":"none";
  $("#lists").innerHTML=all.length?'<thead><tr><th>List</th><th>Kind</th><th class="r">'+(S.report.account.platform==="meta"?"People (Meta's estimate)":S.report.account.platform==="both"?"Search · Meta people":"Search")+'</th>'+(S.report.account.platform==="meta"?"":'<th class="r">Display and YouTube</th>')+'<th>Status</th><th>Used</th><th>What is wrong</th></tr></thead><tbody>'+
    (ls.length?ls.map(function(l){
      return '<tr><td class="sg-lab">'+esc(l.label)+(multi?'<span class="gp-sub">'+esc(l.account)+'</span>':"")+(l.readOnly?'<span class="gp-sub">created by Google</span>':"")+'</td><td>'+esc(l.detail)+'</td><td class="r num">'+(l.sizeSearch==null?"Not shown":int(l.sizeSearch))+'</td>'+
        (S.report.account.platform==="meta"?"":'<td class="r num">'+(l.platform==="meta"?"–":int(l.sizeDisplay))+'</td>')+'<td>'+esc(l.status==="OPEN"?"Open":l.status==="CLOSED"?"Closed":l.status==="NOT_READY"?"Not ready":l.status||"")+'</td><td>'+(l.used?"Yes":'<span class="au-no">Not used</span>')+
        '</td><td class="au-prob">'+(l.problems.length?l.problems.map(esc).join("<br>"):"Nothing")+'</td></tr>';
    }).join(""):'<tr><td colspan="7" class="pm-empty">No list matches.</td></tr>')+'</tbody>':'<tbody><tr><td class="pm-empty">The account has no audience lists.</td></tr></tbody>';
}
function paintExcl(){
  var ex=S.report.exclusions||[], multi=S.report.account.isGroup;
  $("#exclCard").hidden=!ex.length;
  $("#excl").innerHTML=ex.length?'<thead><tr><th>Audience</th><th>Kind</th><th>Excluded from</th></tr></thead><tbody>'+ex.map(function(e){
    return '<tr><td class="sg-lab">'+esc(e.label)+'</td><td>'+esc(e.detail)+'</td><td>'+esc(e.campaign)+(e.adGroup?' <span class="gp-sub">'+esc(e.adGroup)+'</span>':' <span class="gp-sub">whole campaign</span>')+(multi?' <span class="gp-sub">'+esc(e.account)+'</span>':"")+'</td></tr>';
  }).join("")+'</tbody>':"";
}

/* ── CSV and Refresh ──────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report, v=view(); if(!rep||!v) return;
  var lines=[["Account",rep.account.name],["Date range",rep.range.from+" to "+rep.range.to],["Campaign type",S.type],[],
    ["Audience","Platform","Kind","Detail","Impressions","Clicks","Cost","% cost","CTR","Conversions","Conv. rate","CPA","Conversion value","ROAS","Efficiency index","Verdict","Mode","Bid adjustment now","Suggested bid adjustment","What to do"]];
  v.rows.forEach(function(r){
    lines.push([r.label,r.platform==="meta"?"Meta Ads":"Google Ads",r.kindLabel,r.detail,r.impressions,r.clicks,r.cost.toFixed(2),r.pctCost==null?"":(r.pctCost*100).toFixed(2)+"%",r.ctr==null?"":(r.ctr*100).toFixed(2)+"%",r.conversions,
      r.cvr==null?"":(r.cvr*100).toFixed(2)+"%",r.cpa==null?"":r.cpa.toFixed(2),r.value,r.roas==null?"":r.roas.toFixed(3),r.index==null?"":r.index,r.verdictLabel,modeText(r.mode,r.platform),r.platform==="meta"?"":bidText(r),
      r.adjustment==null||!r.adjustable?"":Math.round(r.adjustment*100)+"%",(r.action&&r.action.text)||""]);
  });
  var blob=new Blob(["﻿"+lines.map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="audiences-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.range.from+"-"+rep.range.to+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search), saved="";
  try{saved=localStorage.getItem("au_account")||"";}catch(e){}
  var want=p.get("account")||saved;
  var pf=p.get("platform");
  if(!PL.validPlatform(pf)) pf=want?PL.platformOfId(want):(switcher.saved()||"google");
  S.platform=pf; switcher.set(pf);
  PL.partsOf(want||"").forEach(function(id){if(PL.isMeta(id)) S.m=id; else if(id) S.g=id;});
  if(p.get("from")&&p.get("to")){S.preset="custom";S.from=p.get("from");S.to=p.get("to");}
  else{var r=rangeFor(S.preset);S.from=r[0];S.to=r[1];}
  $("#from").value=S.from; $("#to").value=S.to; paintPresets();
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  mpicker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  var metaReq=getJson("/p2/paid-marketing/api/gads/accounts?platform=meta").then(function(r){
    if(r.ok) S.metaAccounts=(r.j.accounts||[]); else S.metaProblem=(r.j&&r.j.error)||"Meta Ads could not be read.";
  }).catch(function(){S.metaProblem="Meta Ads could not be reached.";});
  getJson("/p2/paid-marketing/api/segments/accounts").then(function(r){
    if(!r.ok){
      picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:20}],"",true);
      if(S.platform==="google") setMsg(esc((r.j&&r.j.error)||"The account list could not be loaded."));
      return metaReq;
    }
    S.accounts=r.j.accounts||[]; S.groups=r.j.groups||[];
    return metaReq;
  }).then(function(){
    if(S.platform!=="meta"&&!S.g&&S.m) autoGoogle();
    if(S.platform!=="google"&&!S.m&&S.g) autoMeta();
    if(S.platform==="meta"&&!S.m&&S.g) autoMeta();
    paintPickers(); recompute();
  }).catch(function(){
    picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:20}],"",true);
    setMsg("The server could not be reached. Reload to try again.");
  });
})();
})();
