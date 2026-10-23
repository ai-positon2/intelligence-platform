/* Asset Coverage Audit (Paid Marketing Agents).
 *
 * Pick a Google Ads account (or an MCC). The server reads every asset the live campaigns carry and holds each campaign, Performance Max
 * asset group and Display or Demand Gen ad against what its kind should hold (tracker/paid_assets.py, paid_assets_rules.py). This page
 * draws the report. It never counts an asset, judges a shortfall or decides what to add for itself.
 *
 * Everything on it opens: a card or a coverage bar opens the assets behind it (a drawer, from /api/assets/library), and a campaign's
 * kinds of asset open onto the assets themselves (from /api/assets/campaign): the picture of an image, the text of a sitelink, a
 * video's thumbnail. How one asset is drawn is asset_tiles.js.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/assets";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function int(v){return v==null?"Na":Math.round(v).toLocaleString("en-US");}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}

var SEV = {high:["Fix first","high"], medium:["Add","medium"], low:["Nice to have","low"]};
var SEV_RANK = {high:3, medium:2, low:1};
var SOURCE = {campaign:"this campaign", account:"the account", "asset group":"the asset group", ad:"the ad"};
var T = window.ACTiles;
var S = {accounts:[], groups:[], account:"", meta:"", scopeAcct:"", platform:"google", mseq:0, metaReport:null, currency:"USD", report:null, seq:0, sev:"high", kind:"all", filter:"", limit:25, open:{}, det:{}, openK:{}, openD:{}};
var D = {on:false, mode:"", kind:"", state:"all", level:"", title:"", tab:"assets", filter:"", limit:24, data:null, loading:false, error:"", seq:0, opener:null};
var enc = encodeURIComponent;

var float_=$("#acFloat"); if(float_&&document.body&&float_.parentNode!==document.body) document.body.appendChild(float_);   // above the page's own header
/* The Platform switch and the account pickers are the shared control (static/js/pm_scope.js). Google audits Google's assets, Meta audits the ads of each ad set (Meta has no asset
   rules), Both shows the two one under the other: S.account is the Google part (an account or an MCC), S.meta the Meta part. */
var scope=null;
function groupOf(id){return scope?scope.groupOf(id):null;}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function applyScope(a,p){
  var ps=window.PMScope.partsOf(a).filter(Boolean);
  S.scopeAcct=a||""; S.platform=p;
  S.account=ps.filter(function(x){return !window.PMScope.isMeta(x);})[0]||"";
  S.meta=ps.filter(function(x){return window.PMScope.isMeta(x);})[0]||"";
  try{localStorage.setItem("ac_account",S.scopeAcct);}catch(e){}
}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}
function setMetaMsg(html,kind){$("#metaMsg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function hideMeta(){$("#metaOut").hidden=true; $("#metaOut").innerHTML=""; setMetaMsg("");}
function runMeta(refresh){
  var seq=++S.mseq;
  setMetaMsg(""); $("#metaOut").hidden=true;
  if(S.platform==="meta") show("skel");
  getJson(API+"/report?account="+encodeURIComponent(S.meta)+(refresh===true?"&refresh=1":"")).then(function(r){
    if(seq!==S.mseq) return;
    if(S.platform==="meta") show("none");
    if(!r.ok){
      if(r.j&&r.j.kind==="cooldown"){setMetaMsg(esc(r.j.error),"pm-note");return;}
      setMetaMsg('<b>Meta Ads could not be read.</b> '+esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+").")));
      return;
    }
    S.metaReport=r.j;
    $("#metaOut").innerHTML=(S.platform==="both"?'<h2 class="mc-title">Meta Ads</h2>':"")+window.MetaCreative.coverage(r.j);
    $("#metaOut").hidden=false;
  }).catch(function(){
    if(seq!==S.mseq) return;
    if(S.platform==="meta") show("none");
    setMetaMsg("The server could not be reached. Try again.");
  });
}

function run(refresh){
  var wantG=S.platform!=="meta"&&S.account, wantM=S.platform!=="google"&&S.meta;
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.scopeAcct));}catch(e){}
  if(!wantG&&!wantM){show("empty");setMsg("");hideMeta();return;}
  if(wantM) runMeta(refresh); else hideMeta();
  if(!wantG){setMsg(""); S.seq++; return;}
  var seq=++S.seq, g=groupOf(S.account);
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 15 to 30 seconds.":"",g?"pm-note":"");
  if(S.platform!=="both"||!S.report) show("skel");
  getJson(API+"/report?account="+encodeURIComponent(S.account)+(refresh===true?"&refresh=1":"")).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg("");
    if(!r.ok){
      show(S.report?"out":(wantM&&S.platform==="both"?"none":"empty"));
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg((S.platform==="both"?"<b>Google Ads could not be read.</b> ":"")+esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j; S.currency=r.j.account.currency||"USD"; S.open={}; S.det={}; S.openK={}; S.openD={}; S.limit=25; S.filter=""; S.kind="all"; $("#filter").value=""; closeDrawer(true);
    S.sev=defaultSev(r.j.campaigns);
    show("out"); render();
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Which campaigns ──────────────────────────────────────── */
function worstOf(it){return it.severity||"none";}
function atLeast(it,level){ // a campaign is shown at a level when its worst gap is that bad or worse
  if(level==="all") return true;
  if(level==="none") return !it.severity;
  return !!it.severity&&SEV_RANK[it.severity]>=SEV_RANK[level];
}
function sevCount(items,level){return items.filter(function(i){return atLeast(i,level);}).length;}
function defaultSev(items){
  return sevCount(items,"high")?"high":sevCount(items,"medium")?"medium":sevCount(items,"low")?"low":"none";
}
function matches(it){
  if(!atLeast(it,S.sev)) return false;
  if(S.kind!=="all"&&it.type!==S.kind) return false;
  var t=S.filter.trim().toLowerCase(); if(!t) return true;
  var hay=[it.name,it.account,it.typeLabel].concat(it.gaps.map(function(g){return g.label+" "+g.scope;})).join(" ").toLowerCase();
  return hay.indexOf(t)>=0;
}

/* ── Rendering ────────────────────────────────────────────── */
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts combined</span>':"")+
    '&nbsp;· read from Google Ads '+esc(ago(rep.generatedAt))+' · impressions are the last '+rep.window.days+' days'+
    (rep.stale?' <span class="sg-mcc">Google Ads is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintCoverage(); paintSevs(); paintKinds(); paintList(); paintAccount();
  $("#foot").innerHTML=footNote(rep);
}
function footNote(rep){
  var na=(rep.notAudited||[]).map(function(n){return n.campaigns+" "+n.label;}).join(", ");
  return "Source: Google Ads, saved so the same report opens instantly. <b>Minimums are what Google needs for the format to show</b> (two sitelinks, two callouts, three headlines in a Performance Max asset group and so on). "+
    "<b>Targets are Position2's rule of thumb</b>, not Google's, and the coverage bars count a campaign as covered at its target. An asset counts as live when it is switched on and not disapproved, paused or past its end date; one under review counts because it will serve. "+
    "Where an ad group holds its own assets of a kind they override the campaign's, so a campaign shown as short may still be covered inside those ad groups (the row says how many). "+
    "For Display and Demand Gen the ad's own asset counts are read, and Google does not report approval for each asset there. Problems in account-wide assets are listed once under Account-wide assets, not under every campaign. Click a card, a coverage bar or a kind of asset to see the assets themselves."+
    (na?" Not audited (Google manages or builds these): "+esc(na)+".":"")+" Nothing is changed in Google Ads: add the assets there.";
}
function paintKpis(){
  var s=S.report.summary, share=s.impressions?Math.round(s.impressionsFixFirst/s.impressions*100):0;
  var g=s.gaps, ended=s.expiredAssets!=null?s.expiredAssets:s.expired, items=[
    ["Campaigns audited",int(s.campaigns),(s.withGaps?int(s.withGaps)+" have a gap":"no gaps"),"all"],
    ["Fix first",int(s.fixFirst),s.fixFirst?"warn":"good","high"],
    ["Impressions at stake",int(s.impressionsFixFirst),s.fixFirst?share+"% of all impressions, on fix-first campaigns":"nothing urgent","high"],
    ["Gaps to close",int(g.high+g.medium+g.low),g.high+" fix first · "+g.medium+" add · "+g.low+" nice to have","low"],
    ["Disapproved assets",int(s.disapprovedAssets),s.disapprovedAssets?"warn":"good","disapproved"],
    ["Past their end date",int(ended),ended?"assets ended but still switched on":"none","expired"]];
  $("#kpis").innerHTML=items.map(function(c){
    var cls=c[2]==="good"||c[2]==="warn"?c[2]:"";
    return '<div class="pd-kpi ac-click '+cls+'" role="button" tabindex="0" data-act="'+c[3]+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div>'+(cls?'':'<div class="s">'+esc(c[2])+'</div>')+'</div>';
  }).join("");
}
function kpiAct(act){
  if(act==="disapproved"||act==="expired"){
    var s=S.report.summary, n=act==="expired"?(s.expiredAssets!=null?s.expiredAssets:s.expired):s.disapprovedAssets;
    openDrawer({mode:"state", state:act, title:act==="expired"?"Past their end date":"Disapproved assets", n:n});
    return;
  }
  S.sev=act; S.kind="all"; S.filter=""; S.limit=25; $("#filter").value=""; paintSevs(); paintKinds(); paintList();
  var c=$("#listCard"); if(c&&c.scrollIntoView) c.scrollIntoView({behavior:"smooth",block:"start"});
}
$("#kpis").addEventListener("click",function(e){var b=e.target.closest("[data-act]"); if(b) kpiAct(b.dataset.act);});
$("#kpis").addEventListener("keydown",function(e){
  if(e.key!=="Enter"&&e.key!==" ") return;
  var b=e.target.closest("[data-act]"); if(b){e.preventDefault(); kpiAct(b.dataset.act);}
});
function paintCoverage(){
  var cov=S.report.summary.coverage||[];
  $("#cov").innerHTML=cov.length?cov.map(function(c){
    var n=c.campaigns||1, seg=function(k,v){return v?'<i class="'+k+'" style="flex:'+v+'" title="'+v+' '+k+'"></i>':"";};
    var bits=[c.atTarget+" of "+c.campaigns+" at target"];
    if(c.below) bits.push(c.below+" below");
    if(c.none) bits.push(c.none+" with none");
    if(c.unknown) bits.push(c.unknown+" not readable");
    return '<button type="button" class="ac-cov ac-click'+(c.optional?" opt":"")+'" data-kind="'+esc(c.key)+'" aria-label="Show the '+esc(c.label.toLowerCase())+'"><span class="ac-cov-l">'+esc(c.label)+(c.optional?' <em>optional</em>':"")+'</span>'+
      '<div class="ac-bar" aria-hidden="true">'+seg("ok",c.atTarget)+seg("below",c.below)+seg("none",c.none)+seg("unk",c.unknown)+'</div>'+
      '<span class="ac-cov-n">'+esc(bits.join(" · "))+'<span class="ac-go" aria-hidden="true">See them</span></span></button>';
  }).join(""):'<p class="pm-empty">No Search or Performance Max campaigns to cover.</p>';
}
function paintSevs(){
  var items=S.report.campaigns, defs=[["high","Fix first"],["medium","Fix first and add"],["low","Including nice to have"],["all","All campaigns"],["none","No gaps"]];
  $("#sevs").innerHTML=defs.map(function(d){
    return '<button type="button" role="radio" class="pm-chip'+(d[0]===S.sev?" on":"")+'" data-s="'+d[0]+'" aria-checked="'+(d[0]===S.sev)+'">'+d[1]+' <span class="n">'+sevCount(items,d[0])+'</span></button>';
  }).join("");
}
function paintKinds(){
  var items=S.report.campaigns, types={}, order=[];
  items.forEach(function(i){ if(!types[i.type]){types[i.type]={label:i.typeLabel,n:0}; order.push(i.type);} types[i.type].n++; });
  var defs=[["all","All",items.length]].concat(order.map(function(t){return [t,types[t].label,types[t].n];}));
  $("#kinds").innerHTML=defs.map(function(d){
    return '<button type="button" role="radio" class="pm-chip'+(d[0]===S.kind?" on":"")+'" data-k="'+esc(d[0])+'" aria-checked="'+(d[0]===S.kind)+'">'+esc(d[1])+' <span class="n">'+d[2]+'</span></button>';
  }).join("");
}
$("#cov").addEventListener("click",function(e){
  var b=e.target.closest("[data-kind]"); if(!b) return;
  var c=(S.report.summary.coverage||[]).filter(function(x){return x.key===b.dataset.kind;})[0];
  openDrawer({mode:"kind", kind:b.dataset.kind, title:c?c.label:b.dataset.kind, state:"live", opener:b});
});
$("#sevs").addEventListener("click",function(e){var b=e.target.closest("button"); if(!b) return; S.sev=b.dataset.s; S.limit=25; paintSevs(); paintList();});
$("#kinds").addEventListener("click",function(e){var b=e.target.closest("button"); if(!b) return; S.kind=b.dataset.k; S.limit=25; paintKinds(); paintList();});
$("#filter").addEventListener("input",function(){S.filter=this.value; S.limit=25; paintList();});

function pill(sev){var d=SEV[sev]; return d?'<span class="ac-pill '+d[1]+'">'+d[0]+'</span>':'<span class="ac-pill none">No gaps</span>';}
function paintList(){
  var items=S.report.campaigns, rows=items.filter(matches), shown=rows.slice(0,S.limit);
  $("#listNote").textContent="Worst gap first, then the busiest campaign (last 30 days)";
  var narrowed=S.filter.trim()||S.kind!=="all";
  $("#list").innerHTML=shown.length?shown.map(row).join(""):'<p class="pm-empty">'+(!narrowed&&S.sev==="high"&&items.length?"No campaign has a gap that stops an asset showing.":"No campaign matches.")+'</p>';
  var more=$("#more"); more.hidden=rows.length<=S.limit; more.textContent="Show all "+rows.length+" campaigns";
}
$("#more").addEventListener("click",function(){S.limit=100000; paintList();});

function topGap(it){
  var g=it.gaps[0]; if(!g) return it.typeLabel==="Search"||it.typeLabel==="Performance Max"?"Every asset kind is at its target.":"Every asset is in place.";
  var more=it.gaps.length>1?" +"+(it.gaps.length-1)+" more":"";
  return g.label+": "+g.message+more;
}
function row(it){
  var open=!!S.open[it.key], multi=S.report.account.isGroup;
  return '<article class="ac-row'+(open?" open":"")+'" data-key="'+esc(it.key)+'">'+
    '<button type="button" class="ac-head" aria-expanded="'+open+'">'+pill(worstOf(it)==="none"?null:it.severity)+
      '<span class="ac-name"><b>'+esc(it.name||"(unnamed)")+'</b><span class="ac-sub">'+esc(it.typeLabel)+(multi?" · "+esc(it.account):"")+'</span></span>'+
      '<span class="ac-num"><b>'+int(it.impressions)+'</b> impressions<span>'+int(it.clicks)+' clicks</span></span>'+
      '<span class="ac-gap">'+esc(topGap(it))+'</span><span class="ac-chev" aria-hidden="true"></span></button>'+
    (open?body(it):"")+'</article>';
}
function noteBits(s){
  var bits=[];
  if(s.severity==="info"&&s.status!=="ok") bits.push('<span class="ac-t">optional</span>');
  if(s.disapproved) bits.push('<span class="ac-t bad">'+s.disapproved+' disapproved</span>');
  if(s.expired) bits.push('<span class="ac-t bad">'+s.expired+' past end date</span>');
  if(s.paused) bits.push('<span class="ac-t">'+s.paused+' paused</span>');
  if(s.pending) bits.push('<span class="ac-t">'+s.pending+' under review</span>');
  if(s.notShown) bits.push('<span class="ac-t">'+s.notShown+' not shown in 30 days</span>');
  if(s.adGroups) bits.push('<span class="ac-t">'+s.adGroups+' ad group'+(s.adGroups===1?"":"s")+' with their own</span>');
  return bits.join(" ");
}
/* Google's policy topic as words: HEALTH_IN_PERSONALIZED_ADS is "Health in personalized ads". */
function topicWords(t){var w=String(t||"").replace(/_/g," ").toLowerCase().trim(); return w.charAt(0).toUpperCase()+w.slice(1);}
function badNames(s){
  return (s.bad&&s.bad.length)?'<span class="ac-names">'+s.bad.map(function(b){
    var why=(b.why&&b.why.length)?": "+b.why.map(function(x){return topicWords(x.t);}).join(", "):"";
    return esc(b.label)+" ("+(b.state==="expired"?"past end date":"disapproved"+esc(why))+")";}).join(", ")+'</span>':"";
}
/* How to do it (tracker/paid_assets_plans.py works the steps out): folded away under the gap or the asset that needs it. */
function planBlock(p,title){
  if(!p||!p.steps||!p.steps.length) return "";
  return '<details class="ac-plan"><summary>'+esc(title)+'</summary><ol>'+p.steps.map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ol>'+
    (p.worth?'<p class="ac-worth"><b>Worth</b> '+esc(p.worth)+'</p>':"")+'</details>';
}
function sectionMap(secs){var m={}; (secs||[]).forEach(function(x){m[x.key]=x;}); return m;}
function strip(sec,id,open){
  var n=sec.tiles.length, ncol=sec.cols;
  return '<tr class="ac-pv"><td colspan="'+ncol+'"><button type="button" class="ac-pvb" data-kid="'+esc(id)+'" aria-expanded="'+open+'"><span class="ac-pvs">'+T.preview(sec.tiles,8)+'</span>'+
    '<span class="ac-pvt">'+(open?"Hide":"Show all "+n)+'<i aria-hidden="true"></i></span></button></td></tr>'+
    (open?'<tr class="ac-pg"><td colspan="'+ncol+'">'+T.tiles(sec.tiles)+'</td></tr>':"");
}
function slotTable(slots,fromCol,secs,ns){
  var by=sectionMap(secs), ncol=fromCol?5:4, known={};
  function extra(sec,id){
    var cls=sec.live?"ok":"unk";
    return '<tr class="'+cls+'"><td>'+esc(sec.label)+' <span class="ac-t">not judged</span></td><td class="r c">'+sec.live+'</td><td class="r c">·</td>'+(fromCol?'<td class="c">'+esc(SOURCE[sec.level]||sec.level)+'</td>':'')+'<td></td></tr>'+
      strip(Object.assign({cols:ncol},sec),id,!!S.openK[id]);
  }
  var rows=slots.map(function(s){
    known[s.key]=1;
    var have=s.have==null?"Na":s.have, cls=s.status==="ok"?"ok":s.status==="unknown"?"unk":(s.severity||"low"), sec=by[s.key], id=ns+"|"+s.key;
    return '<tr class="'+cls+'"><td>'+esc(s.label)+'</td><td class="r c">'+have+'</td><td class="r c">'+(s.min&&s.min!==s.target?s.min+" to "+s.target:s.target)+'</td>'+
      (fromCol?'<td class="c">'+esc(SOURCE[s.source]||s.source)+'</td>':'')+'<td>'+noteBits(s)+badNames(s)+planBlock(s.fix,"How to fix")+'</td></tr>'+
      (sec&&sec.tiles.length?strip(Object.assign({cols:ncol},sec),id,!!S.openK[id]):"");
  }).join("");
  var more=(secs||[]).filter(function(x){return !known[x.key]&&x.tiles.length;}).map(function(x){return extra(x,ns+"|"+x.key);}).join("");
  return '<div class="ac-txw"><table class="ac-tx"><thead><tr><th>Kind</th><th class="r">Live</th><th class="r">Target</th>'+(fromCol?'<th>From</th>':'')+'<th>Notes</th></tr></thead><tbody>'+rows+more+'</tbody></table></div>';
}
function detailNote(dt){
  if(!dt||dt.status==="loading") return '<p class="ac-load">Loading what is linked…</p>';
  if(dt.status==="error") return '<p class="ac-load bad">'+esc(dt.msg)+' <button type="button" class="ac-link" data-retry="1">Try again</button></p>';
  return "";
}
function gapList(gaps){
  return gaps.length?'<ul class="ac-gaps">'+gaps.map(function(g){
    var d=SEV[g.severity]||["",""];
    return '<li class="'+d[1]+'"><span class="ac-pill '+d[1]+'">'+d[0]+'</span><span class="ac-gl"><b>'+esc(g.label)+'</b> <span class="ac-sc">'+esc(g.scope)+'</span><br>'+esc(g.message)+planBlock(g.plan,"How to add them")+'</span>'+
      (g.add?'<span class="ac-add">+'+g.add+'</span>':"")+'</li>';
  }).join("")+'</ul>':'<p class="pm-empty">Nothing to add.</p>';
}
function det(open,id,html){return '<details class="ac-det" data-d="'+esc(id)+'"'+(open?" open":"")+'>'+html+'</details>';}
function byId(list,id){return (list||[]).filter(function(x){return x.id===id;})[0]||{};}
function body(it){
  var dt=S.det[it.key], data=dt&&dt.status==="ok"?dt.data:null, k=it.key;
  var out='<div class="ac-body"><h4>What to add</h4>'+gapList(it.gaps)+detailNote(dt);
  if(it.slots.length) out+='<h4>Campaign assets</h4>'+slotTable(it.slots,true,data&&data.sections,"c:"+k);
  if(it.groups.length) out+='<h4>Asset groups ('+it.groups.length+')</h4>'+it.groups.map(function(g){
    var id="g:"+k+":"+g.id;
    return det(!!S.openD[id],id,'<summary>'+pill(g.severity)+' <b>'+esc(g.name||g.id)+'</b></summary>'+slotTable(g.slots,true,byId(data&&data.groups,g.id).sections,id));}).join("");
  if(it.ads.length) out+='<h4>Ads ('+it.ads.length+')</h4>'+it.ads.map(function(a){
    var id="a:"+k+":"+a.id;
    return det(!!S.openD[id],id,'<summary>'+pill(a.severity)+' <b>'+esc(a.adGroup||"(ad group)")+'</b> <span class="ac-sub">'+esc(a.typeLabel)+'</span></summary>'+slotTable(a.slots,false,byId(data&&data.ads,a.id).sections,id));}).join("");
  return out+'</div>';
}
$("#list").addEventListener("click",function(e){
  var pv=e.target.closest(".ac-pvb");
  if(pv){var id=pv.dataset.kid; S.openK[id]=!S.openK[id]; paintList(); return;}
  var retry=e.target.closest("[data-retry]");
  if(retry){var rk=retry.closest(".ac-row").dataset.key; delete S.det[rk]; loadDetail(rk); paintList(); return;}
  var head=e.target.closest(".ac-head"); if(!head) return;
  var key=head.closest(".ac-row").dataset.key; S.open[key]=!S.open[key];
  if(S.open[key]) loadDetail(key);
  paintList();
});
$("#list").addEventListener("toggle",function(e){var d=e.target; if(d&&d.dataset&&d.dataset.d) S.openD[d.dataset.d]=!!d.open;},true);

/* what a campaign's assets are: asked for when it opens, from the copy the report's read saved */
function loadDetail(key){
  var cur=S.det[key]; if(cur&&(cur.status==="loading"||cur.status==="ok")) return;
  var seq=S.seq; S.det[key]={status:"loading"};
  getJson(API+"/campaign?account="+enc(S.account)+"&campaign="+enc(key)).then(function(r){
    if(seq!==S.seq) return;
    S.det[key]=r.ok?{status:"ok",data:r.j}:{status:"error",msg:(r.j&&r.j.error)||("The assets could not be read (HTTP "+r.status+").")};
    paintList();
  }).catch(function(){
    if(seq!==S.seq) return;
    S.det[key]={status:"error",msg:"The server could not be reached."}; paintList();
  });
}

function paintAccount(){
  var rows=S.report.accountAssets||[], any=rows.some(function(a){return a.slots.length;});
  $("#acctCard").hidden=!any;
  if(!any) return;
  var multi=rows.length>1;
  $("#acctAssets").innerHTML=rows.filter(function(a){return a.slots.length;}).map(function(a){
    return (multi?'<h4 class="ac-acct">'+esc(a.account)+'</h4>':"")+'<div class="ac-txw"><table class="ac-tx"><thead><tr><th>Kind</th><th class="r">Live</th><th>Notes</th></tr></thead><tbody>'+
      a.slots.map(function(s){return '<tr class="ac-ar" data-kind="'+esc(s.key)+'" tabindex="0" role="button" aria-label="Show the account-wide '+esc(s.label.toLowerCase())+'"><td>'+esc(s.label)+'</td><td class="r c">'+s.have+'</td><td>'+noteBits(s)+badNames(s)+'<span class="ac-go" aria-hidden="true">See them</span></td></tr>';}).join("")+
      '</tbody></table></div>'+(a.locations?'<p class="ac-loc">'+a.locations+' location set'+(a.locations===1?"":"s")+' linked to the account.</p>':"");
  }).join("");
}

function openAccountKind(row){
  var s=null; (S.report.accountAssets||[]).forEach(function(a){a.slots.forEach(function(x){if(x.key===row.dataset.kind) s=x;});});
  openDrawer({mode:"kind", kind:row.dataset.kind, title:(s?s.label:row.dataset.kind)+" linked to the account", state:"live", level:"account", opener:row});
}
$("#acctAssets").addEventListener("click",function(e){var r=e.target.closest(".ac-ar"); if(r) openAccountKind(r);});
$("#acctAssets").addEventListener("keydown",function(e){
  if(e.key!=="Enter"&&e.key!==" ") return;
  var r=e.target.closest(".ac-ar"); if(r){e.preventDefault(); openAccountKind(r);}
});

/* ── The drawer: every asset behind a card or a bar ───────── */
function openDrawer(spec){
  D.on=true; D.mode=spec.mode; D.kind=spec.kind||""; D.state=spec.state||"all"; D.level=spec.level||""; D.title=spec.title||""; D.n=spec.n;
  D.tab="assets"; D.filter=""; D.limit=24; D.data=null; D.error=""; D.opener=spec.opener||document.activeElement||null;
  var host=$("#acDrawer"); host.hidden=false; document.documentElement.classList.add("ac-lock");
  paintDrawer(); loadLibrary();
  var x=$("#acdClose"); if(x&&x.focus) x.focus();
}
function closeDrawer(quiet){
  if(!D.on) return;
  D.on=false; $("#acDrawer").hidden=true; document.documentElement.classList.remove("ac-lock");
  if(!quiet&&D.opener&&D.opener.focus) D.opener.focus();
}
function loadLibrary(){
  var seq=++D.seq; D.loading=true; D.error=""; paintDrawer();
  var url=API+"/library?account="+enc(S.account)+"&kind="+enc(D.kind)+"&state="+enc(D.state)+(D.level?"&level="+enc(D.level):"");
  getJson(url).then(function(r){
    if(seq!==D.seq) return;
    D.loading=false;
    if(r.ok&&r.j&&Array.isArray(r.j.assets)&&r.j.counts) D.data=r.j;
    else D.error=(r.j&&r.j.error)||(r.ok?"Google Ads answered with something this page does not understand.":"The assets could not be read (HTTP "+r.status+").");
    paintDrawer();
  }).catch(function(){
    if(seq!==D.seq) return;
    D.loading=false; D.error="The server could not be reached."; paintDrawer();
  });
}
function kindCampaigns(){ // the report's own verdict for this kind, campaign by campaign: those with none first
  var rank={missing:0,short:1,thin:2,unknown:3,ok:4}, out=[];
  S.report.campaigns.forEach(function(it){
    var sl=it.slots.filter(function(s){return s.key===D.kind;})[0];
    if(sl) out.push({it:it, s:sl});
  });
  out.sort(function(a,b){return (rank[a.s.status]-rank[b.s.status])||(b.it.impressions-a.it.impressions);});
  return out;
}
var STATUS_WORD={missing:"None live", short:"Below the minimum", thin:"Below target", ok:"At target", unknown:"Not readable"};
function paintDrawer(){
  if(!D.on) return;
  var d=D.data, multi=S.report&&S.report.account.isGroup;
  $("#acdT").textContent=D.title;
  var sub=[];
  if(d&&D.mode==="state"){
    sub.push(num(d.assetCount)+" asset"+(d.assetCount===1?"":"s")+(d.total!==d.assetCount?", "+num(d.total)+" different":""));
  } else if(d){
    sub.push(num(d.counts.all)+" different asset"+(d.counts.all===1?"":"s"));
    if(d.counts.live) sub.push(num(d.counts.live)+" live");
    if(d.counts.problem) sub.push(num(d.counts.problem)+" with a problem");
  } else if(D.n!=null) sub.push(num(D.n)+" asset"+(D.n===1?"":"s"));
  $("#acdS").textContent=sub.join(" · ");
  var tools="";
  if(D.mode==="kind"){
    var camps=kindCampaigns(), short=camps.filter(function(c){return c.s.status!=="ok";}).length;
    tools+='<div class="pm-chips" role="radiogroup" aria-label="What to show">'+[["assets","Assets"],["campaigns","Campaigns ("+short+" short)"]].map(function(t){
      return '<button type="button" role="radio" class="pm-chip'+(D.tab===t[0]?" on":"")+'" data-tab="'+t[0]+'" aria-checked="'+(D.tab===t[0])+'">'+esc(t[1])+'</button>';}).join("")+'</div>';
    if(D.tab==="assets"){
      var c=d?d.counts:{live:0,problem:0,all:0};
      tools+='<div class="pm-chips" role="radiogroup" aria-label="Which assets">'+[["live","Live",c.live],["problem","Problems",c.problem],["all","All",c.all]].map(function(t){
        return '<button type="button" role="radio" class="pm-chip'+(D.state===t[0]?" on":"")+'" data-state="'+t[0]+'" aria-checked="'+(D.state===t[0])+'">'+t[1]+(d?' <span class="n">'+num(t[2])+'</span>':"")+'</button>';}).join("")+'</div>';
    }
  }
  if(D.tab==="assets") tools+='<input type="text" id="acdFind" class="ac-dfind" placeholder="Find in these assets…" value="'+esc(D.filter)+'" aria-label="Find in these assets" autocomplete="off">';
  $("#acdTools").innerHTML=tools;
  var h="", more=false;
  if(D.mode==="kind"&&D.tab==="campaigns"){
    var rows=kindCampaigns();
    h=rows.length?'<div class="ac-clist">'+rows.slice(0,D.limit*4).map(function(c){
      var bad=c.s.status!=="ok";
      return '<button type="button" class="ac-crow'+(bad?" bad":"")+'" data-camp="'+esc(c.it.key)+'"><span class="ac-pill '+(c.s.severity||"none")+'">'+esc(STATUS_WORD[c.s.status]||c.s.status)+'</span>'+
        '<span class="ac-name"><b>'+esc(c.it.name)+'</b><span class="ac-sub">'+esc(c.it.typeLabel)+(multi?" · "+esc(c.it.account):"")+'</span></span>'+
        '<span class="ac-num"><b>'+(c.s.have==null?"Na":c.s.have)+'</b> live of '+c.s.target+'<span>'+int(c.it.impressions)+' impressions</span></span></button>';}).join("")+'</div>':'<p class="pm-empty">No campaign is held to this kind.</p>';
    more=rows.length>D.limit*4;
  } else if(D.loading&&!d){
    h='<p class="ac-load">Loading the assets…</p>';
  } else if(D.error){
    h='<p class="ac-load bad">'+esc(D.error)+' <button type="button" class="ac-link" data-reload="1">Try again</button></p>';
  } else if(d){
    var list=filteredAssets(), t=D.filter.trim();
    h=(d.total>d.assets.length?'<p class="ac-trunc">Showing the first '+num(d.assets.length)+' of '+num(d.total)+'. Pick Live or Problems to narrow it.</p>':"")+
      (list.length?T.tiles(list.slice(0,D.limit),{library:true}):'<p class="pm-empty">'+(t?"Nothing matches.":D.mode==="state"?"None.":"No assets of this kind in this view.")+'</p>');
    more=list.length>D.limit;
    $("#acdMore").textContent="Show more ("+num(list.length-D.limit)+" left)";
  }
  $("#acdBody").innerHTML=h; $("#acdMore").hidden=!more;
  $("#acdExport").hidden=!(d&&D.tab==="assets"&&d.assets.length);
}
function filteredAssets(){
  var t=D.filter.trim().toLowerCase(), a=D.data?D.data.assets:[];
  if(!t) return a;
  return a.filter(function(x){
    return [x.x,x.n,x.u,x.off,x.code,x.bn,x.vid,x.account].concat(x.d||[],x.v||[],x.why||[],(x.camps||[]).map(function(c){return c.name;})).join(" ").toLowerCase().indexOf(t)>=0;
  });
}
function num(v){return Math.round(Number(v)||0).toLocaleString("en-US");}
$("#acDrawer").addEventListener("click",function(e){
  if(e.target.closest("[data-close]")) return closeDrawer();
  var tab=e.target.closest("[data-tab]"); if(tab){D.tab=tab.dataset.tab; D.limit=24; paintDrawer(); return;}
  var st=e.target.closest("[data-state]"); if(st){D.state=st.dataset.state; D.limit=24; D.data=null; loadLibrary(); return;}
  if(e.target.closest("[data-reload]")){loadLibrary(); return;}
  var camp=e.target.closest("[data-camp]"); if(camp){goCampaign(camp.dataset.camp);}
});
$("#acdMore").addEventListener("click",function(){D.limit+=48; paintDrawer();});
$("#acDrawer").addEventListener("input",function(e){
  if(!e.target||e.target.id!=="acdFind") return;
  D.filter=e.target.value; D.limit=24; paintDrawer();
  var f=$("#acdFind"); if(f&&f.focus){f.focus(); try{f.setSelectionRange(D.filter.length,D.filter.length);}catch(x){}}
});
$("#acdExport").addEventListener("click",function(){
  var rows=[["Account","Kind","Text or name","Lines","Link","Picture","State","Why","Used by campaigns","Asset groups","Impressions (30 days)","Clicks"]];
  filteredAssets().forEach(function(a){
    rows.push([a.account||S.report.account.name,(a.fl||[]).join("; "),a.x||a.n||"",(a.d||a.v||[]).join(" | "),a.u||"",a.img||"",a.st,(a.why||[]).join("; "),a.nc||0,a.ng||0,a.i||0,a.k||0]);
  });
  download(rows,"assets-"+(D.kind||D.state||"all"));
});
/* go to a campaign: close the drawer and open its row in the list */
function goCampaign(key){
  var it=S.report.campaigns.filter(function(c){return c.key===key;})[0];
  closeDrawer(true);
  if(!it){setMsg("That campaign is not in this report any more. Refresh the report.","pm-note"); return;}
  S.sev="all"; S.kind="all"; S.filter=it.name; S.limit=25; $("#filter").value=it.name; S.open[key]=true; loadDetail(key);
  paintSevs(); paintKinds(); paintList();
  var c=$("#listCard"); if(c&&c.scrollIntoView) c.scrollIntoView({behavior:"smooth",block:"start"});
}
/* a picture, enlarged */
function zoom(src,cap){
  var z=$("#acZoom"), safe=T.safeImg(src); if(!safe) return;
  $("#acZimg").src=safe; $("#acZcap").textContent=cap||""; z.hidden=false; document.documentElement.classList.add("ac-lock");
  var x=$("#acZx"); if(x&&x.focus) x.focus();
}
function closeZoom(){var z=$("#acZoom"); if(z.hidden) return false; z.hidden=true; $("#acZimg").src=""; if(!D.on) document.documentElement.classList.remove("ac-lock"); return true;}
$("#acZoom").addEventListener("click",function(e){ if(e.target.closest("[data-close]")||e.target===this) closeZoom(); });
document.addEventListener("click",function(e){
  var z=e.target.closest&&e.target.closest("[data-zoom]"); if(z){e.preventDefault(); zoom(z.dataset.zoom,z.dataset.cap);}
});
document.addEventListener("keydown",function(e){ if(e.key==="Escape"&&!closeZoom()) closeDrawer(); });

/* ── CSV and Refresh ──────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var lines=[["Account",rep.account.name],["Window",rep.window.from+" to "+rep.window.to],[],
    ["Account","Campaign","Kind of campaign","Where","Asset","Priority","Live now","Target","To add","What is wrong","Impressions (30 days)","Clicks","Cost","How to add them"]];
  rep.campaigns.forEach(function(it){
    it.gaps.forEach(function(g){
      lines.push([it.account,it.name,it.typeLabel,g.scope,g.label,(SEV[g.severity]||[""])[0],g.have==null?"":g.have,g.target,g.add,g.message,it.impressions,it.clicks,it.cost,g.plan?g.plan.steps.join(" "):""]);
    });
  });
  download(lines,"asset-coverage");
});
function download(lines,stem){
  var rep=S.report, blob=new Blob(["﻿"+lines.map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download=stem+"-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.window.to+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
}

/* ── Start ────────────────────────────────────────────────── */
(function init(){
  var p=new URLSearchParams(location.search), saved="";
  try{saved=localStorage.getItem("ac_account")||"";}catch(e){}
  scope=window.PMScope.mount($("#acctPick"),{account:p.get("account")||saved,
    onChange:function(a,pf){applyScope(a,pf); run();},
    onError:function(e){setMsg(esc((e&&e.message)||"The account list could not be loaded."));}});
})();
})();
