/* Paid Marketing Agents landing page: the live readouts on each agent card. */
(function(){
"use strict";
var BASE = "/p2/paid-marketing";
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function getJson(u){return fetch(u,{credentials:"same-origin"}).then(function(r){return r.ok?r.json():null}).catch(function(){return null});}

// Clients whose search terms have their own rules (static/js/sqr_core.js).
var SQR_RULES = [[/riccobene/i,"riccobene","Riccobene"],[/\boia\b|outpatient imaging/i,"oia","OIA"],[/eventgroove/i,"eventgroove","Eventgroove"],
  [/gentle dental|42 north|\bgd\b/i,"gentledental_42north","42 North (Gentle Dental)"],[/mchale/i,"mchale","McHale"],[/beta bionics/i,"beta_bionics","Beta Bionics (partial)"]];

/* ── Live status + card readouts ───────────────────────────── */
function paintStatus(snaps, latest, fb, ga){
  var meta=null;
  if(snaps&&snaps.snapshots&&snaps.snapshots.length) meta=snaps.snapshots.filter(function(s){return s.id===snaps.latest})[0]||snaps.snapshots[snaps.snapshots.length-1];
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

Promise.all([getJson(BASE+"/api/pacing/snapshots"), getJson(BASE+"/api/pacing/clients"), getJson(BASE+"/api/feedback/status"), getJson(BASE+"/api/gads/accounts")])
  .then(function(r){ paintStatus(r[0], r[1], r[2], r[3]); });
})();
