/* Paid Marketing Agents landing page: the client launcher and live readouts.
 *
 * Pick a client and the five actions become deep links aimed at them:
 *   Performance Summarizer #a=<Google Ads account id> (matched to the client by name)
 *   Pacing Desk            ?client=<name>
 *   Client Feedback Agent  ?client=<name>
 *   Search Query Analysis  ?account=<ruleset id> (the client's own rules,
 *                          or the generic ruleset when it has none)
 *   Segment Analysis       ?client=<name> (opens the Google Ads account whose
 *                          name matches)
 * Each agent page reads its parameter on load.
 */
(function(){
"use strict";
var BASE = "/p2/paid-marketing";
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function money(v){return v>=1e6?"$"+(v/1e6).toFixed(1)+"M":v>=1e3?"$"+Math.round(v/1e3)+"k":"$"+Math.round(v);}
function getJson(u){return fetch(u,{credentials:"same-origin"}).then(function(r){return r.ok?r.json():null}).catch(function(){return null});}

var FALLBACK = ["BD Diesel","Beta Bionics","Brian Tooley Racing","D&J Precision Machine","Eventgroove","GD & Affiliates",
  "Inspire Aesthetics","McHale Landscaping","Monster Transmission","OIA","Phoenix","Riccobene"];
// Clients whose search terms have their own rules (static/js/sqr_agent.js).
var SQR_RULES = [[/riccobene/i,"riccobene","Riccobene"],[/\boia\b|outpatient imaging/i,"oia","OIA"],[/eventgroove/i,"eventgroove","Eventgroove"],
  [/gentle dental|42 north|\bgd\b/i,"gentledental_42north","42 North (Gentle Dental)"],[/mchale/i,"mchale","McHale"],[/beta bionics/i,"beta_bionics","Beta Bionics (partial)"]];
function rulesFor(name){for(var i=0;i<SQR_RULES.length;i++)if(SQR_RULES[i][0].test(name))return SQR_RULES[i];return null;}
var norm=function(n){return String(n||"").toLowerCase().replace(/\(.*?\)/g,"").replace(/[^a-z0-9]/g,"");};
var adsAccounts=[];
/* The Google Ads account for a Budget Tracker client name: same name, or one contains the other. */
function adsFor(name){
  var n=norm(name); if(n.length<3) return null;
  for(var i=0;i<adsAccounts.length;i++) if(norm(adsAccounts[i].name)===n) return adsAccounts[i];
  for(var j=0;j<adsAccounts.length;j++){var a=norm(adsAccounts[j].name); if(a.length>=4&&(a.indexOf(n)>=0||n.indexOf(a)>=0)) return adsAccounts[j];}
  return null;
}
var ALL='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></svg>';

var clients = FALLBACK.map(function(n){return {name:n}}), byName = {}, chosen = "";
var picker = window.PMPicker(document.getElementById("launchPick"), {label:"Jump to a client", search:true, onChange:function(v){
  chosen=v; try{localStorage.setItem("pm_launch_client",v)}catch(e){} paintActions(true);
}});

function paintPicker(){
  byName={}; clients.forEach(function(c){byName[c.name]=c});
  var items=[{value:"",label:"All clients",sub:clients.length+" clients across the three agents",icon:ALL,hue:36}];
  clients.forEach(function(c){
    var r=rulesFor(c.name);
    items.push({value:c.name,label:c.name,avatar:inits(c.name),hue:hueOf(c.name),
      sub:(c.accounts!=null?c.accounts+" account"+(c.accounts===1?"":"s"):"Client")+(r?" · own SQR rules":""),
      badge:c.offTarget?'<span class="badge pm-pill over">'+c.offTarget+' off target</span>':(c.accounts!=null?'<span class="badge pm-pill ok">on track</span>':'')});
  });
  if(!byName[chosen]) chosen="";
  picker.set(items,chosen);
  paintActions(false);
}

function paintActions(bump){
  var c=byName[chosen], r=chosen?rulesFor(chosen):null, q=chosen?"?client="+encodeURIComponent(chosen):"";
  var ads=chosen?adsFor(chosen):null;
  var acts={perf:BASE+"/performance-summarizer"+(ads?"#a="+ads.id:""),pacing:BASE+"/pacing-desk"+q, feedback:BASE+"/client-feedback-agent"+q,
    sqr:BASE+"/search-query-analysis"+(chosen?"?account="+(r?r[1]:"generic"):""),
    segments:BASE+"/segment-analysis"+q};
  var subs={
    perf:chosen?(ads?"Live: "+ads.name:"Pick an account inside"):"Live from Google Ads",
    pacing:chosen?(c&&c.accounts!=null?c.offTarget+" of "+c.accounts+" account"+(c.accounts===1?"":"s")+" off target":"Their accounts, today"):"Every account, today",
    feedback:chosen?"Aimed at "+chosen:"Slack, Gmail, meetings",
    sqr:chosen?(r?"With "+r[2]+" rules":"With the generic rules"):"Pick an account's rules",
    segments:chosen?"For "+chosen:"Any Google Ads account"};
  document.querySelectorAll("#launchActs .pl-act").forEach(function(a,i){
    var k=a.dataset.act; a.href=acts[k];
    a.querySelector('[data-s]').textContent=subs[k];
    a.classList.toggle("aimed",!!chosen);
    if(bump) a.animate([{transform:"translateY(6px)",opacity:.4},{transform:"none",opacity:1}],{duration:380,delay:i*70,easing:"cubic-bezier(.16,1,.3,1)",fill:"backwards"});
  });
}

/* ── Live status + card readouts ───────────────────────────── */
function chip(state,html){return '<span class="pl-st '+state+'"><span class="b"></span>'+html+'</span>';}
function paintStatus(snaps, latest, fb, ga){
  var out=[], meta=null;
  if(snaps&&snaps.snapshots&&snaps.snapshots.length){
    meta=snaps.snapshots.filter(function(s){return s.id===snaps.latest})[0]||snaps.snapshots[snaps.snapshots.length-1];
    var d=new Date(meta.asOf+"T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric"});
    var off=(meta.overCount||0)+(meta.underCount||0);
    out.push(chip("on","Pacing run <b>"+esc(d)+"</b>"));
    out.push(chip(off?"warn":"on","<b>"+off+"</b> of "+meta.accountCount+" accounts off target"));
    out.push(chip("on","Projects <b>"+money(meta.tPr)+"</b> of "+money(meta.tAll)));
  } else out.push(chip("off","No pacing run pushed yet"));
  if(fb){
    var live=["slack","google"].filter(function(p){return fb.sources[p]&&fb.sources[p].state==="connected"});
    out.push(chip(live.length?"on":"off",live.length?"Feedback reads <b>"+live.map(function(p){return p==="slack"?"Slack":"Google"}).join(" + ")+"</b>":"Feedback sources not connected"));
  }
  out.push(ga&&ga.accounts?chip("on","Google Ads live · <b>"+ga.accounts.length+"</b> accounts"):chip("off","Google Ads not connected yet"));
  out.push(chip("on","<b>"+SQR_RULES.length+"</b> accounts with their own SQR rules"));
  document.getElementById("launchStatus").innerHTML=out.join("");

  var lp=document.querySelector('[data-live="pacing"]');
  if(meta&&meta.tAll){
    var sp=meta.tSp/meta.tAll, pr=meta.tPr/meta.tAll;
    lp.innerHTML='<div class="pl-bar"><i class="pr" style="width:'+Math.min(100,pr*100).toFixed(1)+'%"></i><i class="sp" style="width:'+Math.min(100,sp*100).toFixed(1)+'%"></i></div>'+
      '<div class="pl-cap"><span>'+Math.round(sp*100)+'% spent</span><span>projects '+Math.round(pr*100)+'%</span></div>';
  } else lp.innerHTML='<div class="pl-cap"><span>Waiting for the first run</span></div>';
  var lf=document.querySelector('[data-live="feedback"]');
  if(fb) lf.innerHTML='<div class="pl-dots">'+["slack","google","zoom"].map(function(p){
    var s=fb.sources[p]||{}, on=s.state==="connected"||s.state==="configured";
    return '<span class="'+(on?"on":"")+'"><i></i>'+(p==="slack"?"Slack":p==="google"?"Google":"Zoom")+'</span>';
  }).join("")+'</div>';
  document.querySelector('[data-live="segments"]').innerHTML='<div class="pl-dots">'+["Day","Hour","Age","Gender","City","Device","Network"].map(function(n){
    return '<span class="on"><i></i>'+n+'</span>';}).join("")+'</div>';
  var lg=document.querySelector('[data-live="perf"]');
  if(lg) lg.innerHTML=ga&&ga.accounts?'<div class="pl-dots"><span class="on"><i></i>'+ga.accounts.length+' accounts</span><span class="on"><i></i>12 metrics</span><span class="on"><i></i>9 splits</span></div>'
    :'<div class="pl-cap"><span>Waiting for the Google Ads connection</span></div>';
  document.querySelector('[data-live="sqr"]').innerHTML='<div class="pl-dots">'+SQR_RULES.map(function(r){
    return '<span class="on"><i></i>'+esc(r[2].replace(/ \(.*\)/,""))+'</span>';}).join("")+'</div>';
}

try{chosen=localStorage.getItem("pm_launch_client")||"";}catch(e){}
paintPicker();
Promise.all([getJson(BASE+"/api/pacing/snapshots"), getJson(BASE+"/api/pacing/clients"), getJson(BASE+"/api/feedback/status"), getJson(BASE+"/api/gads/accounts")])
  .then(function(r){
    if(r[3]&&r[3].accounts) adsAccounts=r[3].accounts;
    if(r[1]&&r[1].clients&&r[1].clients.length){ clients=r[1].clients; paintPicker(); } else paintActions(false);
    paintStatus(r[0], r[1], r[2], r[3]);
  });
})();
