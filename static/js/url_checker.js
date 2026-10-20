/* URL Checker (Unknown Domains Alert page).
 *
 * The scan checks the final URL of every enabled ad and keeps a finding for each one that is broken or redirects (tracker/paid_links*.py).
 * This draws them and lets the team dismiss one or open it again; the sheet that is emailed can be downloaded here too. It never decides
 * what is broken: the server's findings are what is shown. unknown_domains.js calls UDLinks.paint(state) after every load.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/domains";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var PAGE = 50;
var STATUS = [["open","To fix"],["dismissed","Dismissed"],["fixed","Fixed"]];
var KINDS = [["broken","Broken"],["redirect","Redirects"],["redirect_domain","Redirects to another domain"]];
var KIND_PILL = {broken:["Broken","critical"], redirect:["Redirect","medium"], redirect_domain:["Redirect, other domain","high"]};
var S = {data:null, st:"open", kind:"all", q:"", limit:PAGE};

/* How to put a link right, from what the scan found: where the address is held decides where to edit it, and the kind of finding decides what to do about it. */
function fixSteps(f){
  var meta=f.platform==="meta", where=String(f.where||""), name=f.objectName?" ("+f.objectName+")":"", camp=f.campaign?" in "+f.campaign:"", steps=[];
  if(meta) steps.push("In Meta Ads Manager open the ad"+name+", then Edit, and change the Website URL under Destination.");
  else if(where==="ad") steps.push("In Google Ads open Ads, find the ad"+name+camp+", click the pencil, and change the Final URL. An edited ad goes back through review for about a day.");
  else if(where==="asset group") steps.push("In Google Ads open Campaigns, then"+(f.campaign?" "+f.campaign+", then":"")+" Asset groups, open the asset group"+name+", and change its Final URL.");
  else if(/^tracking template/.test(where)) steps.push("In Google Ads open the Settings of the level named in the finding ("+where.replace(/^tracking template \(|\)$/g,"")+"), then Tracking template, and correct it.");
  else steps.push("In Google Ads open Ads & assets, then Assets, find the "+where.replace(/ \(.*$/,"")+name+", and change its final URL. It is attached at "+((where.match(/\((\w+) level\)/)||[])[1]||"a")+" level, so the change applies everywhere it is used.");
  if(f.kind==="broken"){
    steps.push("Open "+f.url+" in a browser first to confirm: some sites refuse automated checks. If the page is really gone, replace the link with the page that now holds that content.");
    steps.push("While it is broken every click is paid for and goes nowhere, so pause the ad until it is fixed if that will take more than a day.");
  }else if(f.kind==="redirect_domain"){
    steps.push("The link ends on another domain ("+(f.target||"")+"). If the client moved to it, use that address as the link and update every ad still pointing at the old one; if it is not expected, find out why before more clicks are paid for.");
    steps.push("Expect the Unknown Domains check to flag the new domain until it is approved on this page.");
  }else{
    steps.push("The link redirects to "+(f.target||"another page")+". Use that address as the ad's link so each click skips the extra hop and the tracking parameters are not lost. Keep the old address only if the redirect is deliberate (a short link or a tracking step).");
  }
  return steps;
}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(!iso||isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleDateString("en-US",{month:"short",day:"numeric"})+" ("+rel+")";
}
function plat(p){return '<span class="gp-pl '+(p==="meta"?"meta":"google")+'">'+(p==="meta"?"Meta":"Google")+'</span>';}
function chips(sel,defs,cur,key,counts){
  $(sel).innerHTML=defs.map(function(d){
    return '<button type="button" class="pm-chip'+(cur===d[0]?" on":"")+'" data-lc="'+key+'" data-v="'+d[0]+'" aria-pressed="'+(cur===d[0])+'">'+esc(d[1])+' <span class="n">'+counts(d[0])+'</span></button>';
  }).join("");
}
function alertCell(f){
  if(f.alertedAt) return '<span class="dim">alerted '+esc(ago(f.alertedAt))+'</span>';
  if(f.status!=="open") return '<span class="dim">-</span>';
  return '<span class="ud-wait">waiting to be sent</span>';
}
function actionCell(f){
  if(f.status==="open") return '<button type="button" class="pm-btn-ghost" data-lk="dismiss" data-fp="'+f.fingerprint+'">Dismiss</button>';
  if(f.status==="dismissed") return '<button type="button" class="pm-btn-ghost" data-lk="reopen" data-fp="'+f.fingerprint+'">Reopen</button>';
  return '<span class="dim">works now</span>';
}

function paint(data){
  S.data=data;
  var all=data.links||[], counts=data.linkCounts||{open:0,dismissed:0,fixed:0}, q=S.q.toLowerCase();
  var run=(data.runs||[])[0], lk=run&&run.links;
  $("#linkRun").textContent=lk?(lk.error?"The last check failed: "+lk.error:("Last check: "+lk.urls+" addresses, "+lk.checked+" asked this time"+
    (lk.blocked?", "+lk.blocked+" refuse automated checks (not verified)":"")+(lk.skipped?", "+lk.skipped+" left for the next scan":"")+".")):"No check yet. It runs with every scan.";
  var base=all.filter(function(f){return !q||[f.accountName,f.campaign,f.adGroup,f.objectName,f.url,f.problem,f.target].join(" ").toLowerCase().indexOf(q)>=0;});
  chips("#lkStChips",STATUS,S.st,"st",function(v){return base.filter(function(f){return f.status===v;}).length;});
  var inSt=base.filter(function(f){return S.st==="all"||f.status===S.st;});
  chips("#lkKindChips",KINDS,S.kind,"kind",function(v){return inSt.filter(function(f){return f.kind===v;}).length;});
  var rows=inSt.filter(function(f){return S.kind==="all"||f.kind===S.kind;});
  $("#linkNote").textContent=counts.open+" to fix";
  $("#linkSheet").hidden=!counts.open;
  if(!all.length){
    $("#linkTbl").innerHTML='<tbody><tr><td class="dim">'+(lk?"Every address the ads point at works and goes where it says.":"Nothing yet. Run a scan.")+'</td></tr></tbody>';
    $("#linkMore").hidden=true;return;
  }
  if(!rows.length){$("#linkTbl").innerHTML='<tbody><tr><td class="dim">No link matches. Click the chosen button again to clear it.</td></tr></tbody>';$("#linkMore").hidden=true;return;}
  $("#linkTbl").innerHTML='<thead><tr><th>Problem</th><th>Account</th><th>Campaign / ad</th><th>Link in the ad</th><th>Since</th><th>Alert</th><th></th></tr></thead><tbody>'+
    rows.slice(0,S.limit).map(function(f){
      var pill=KIND_PILL[f.kind]||[f.kind,""], place=[f.campaign,f.adGroup].filter(Boolean).join(" / ");
      return '<tr class="ud-'+(f.status==="open"?"open":f.status)+'"><td><span class="ud-sev '+pill[1]+'">'+esc(pill[0])+'</span><span class="gp-sub ud-why">'+esc(f.problem)+'</span>'+(f.status==="open"?'<details class="pd-how"><summary>How to fix</summary><ol>'+fixSteps(f).map(function(s){return '<li>'+esc(s)+'</li>';}).join("")+'</ol></details>':"")+'</td>'+
        '<td>'+esc(f.accountName)+' '+plat(f.platform)+'</td>'+
        '<td>'+esc(f.where)+(f.objectName?'<span class="gp-sub">'+esc(f.objectName)+'</span>':"")+(place?'<span class="gp-sub">'+esc(place)+'</span>':"")+'</td>'+
        '<td><b class="ud-url" title="'+esc(f.url)+'">'+esc(f.url)+'</b>'+(f.target?'<span class="gp-sub ud-url" title="'+esc(f.target)+'">goes to '+esc(f.target)+'</span>':"")+'</td>'+
        '<td>'+esc(ago(f.firstSeen))+'</td><td>'+alertCell(f)+'</td><td class="ud-act">'+actionCell(f)+'</td></tr>';
    }).join("")+'</tbody>';
  var more=rows.length-S.limit;
  $("#linkMore").hidden=more<=0;
  $("#linkMore").textContent="Show "+Math.min(PAGE,more)+" more ("+more+" left)";
}

document.addEventListener("click",function(ev){
  var t=ev.target.closest("button");if(!t||!S.data) return;
  if(t.dataset.lc){var k=t.dataset.lc, v=t.dataset.v;S[k]=S[k]===v?"all":v;S.limit=PAGE;paint(S.data);return;}
  if(t.id==="linkMore"){S.limit+=PAGE;paint(S.data);return;}
  if(t.dataset.lk){
    t.disabled=true;
    fetch(API+"/links/"+t.dataset.fp+"/"+t.dataset.lk,{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},credentials:"same-origin",body:"{}"})
      .then(function(r){return r.json().catch(function(){return {};}).then(function(j){if(!r.ok) throw new Error(j.error||("The server answered "+r.status+"."));});})
      .then(function(){if(window.UDLoad) window.UDLoad();})
      .catch(function(e){t.disabled=false;alert(e.message);});
  }
});
document.addEventListener("input",function(ev){
  if(ev.target.id==="linkFilter"){S.q=ev.target.value;S.limit=PAGE;if(S.data) paint(S.data);}
});
window.UDLinks={paint:paint};
})();
