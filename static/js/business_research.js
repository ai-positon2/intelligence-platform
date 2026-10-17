/* Business Research (Paid Marketing Agents).
 *
 * The form on this page collects a client and the research to run; the server does the reading and the writing in the background
 * (tracker/paid_business_job.py) and this page polls it, then draws the finished report in tabs: the business, who buys and why, what customers say,
 * what the site measures, the competitors and social media, what it means for paid media, and the questions for the client. It never writes, counts or
 * judges anything for itself: every number and sentence it shows came from the server. The layout is Strategy & Campaign Book's (static/js/campaign_book.js).
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/business";
var $ = function(s,r){return (r||document).querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return String(n).replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function num(v,nd){return v==null||v===""||isNaN(v)?"n/a":Number(v).toLocaleString("en-US",{maximumFractionDigits:nd==null?0:nd});}
function money(v,cur){return v==null||v===""||isNaN(v)?"n/a":Number(v).toLocaleString("en-US",{maximumFractionDigits:v>=100?0:2})+(cur?" "+cur:"");}
function safeHttp(u){u=String(u||"").trim();return /^https?:\/\/[^\s"'<>]+$/i.test(u)?u:"";}
function link(u,label){var s=safeHttp(u);return s?'<a href="'+esc(s)+'" target="_blank" rel="noopener noreferrer">'+esc(label||s)+'</a>':esc(label||u||"");}
function shortUrl(u){return String(u||"").replace(/^https?:\/\//,"").replace(/\/$/,"");}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(!iso||isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000));
  return m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function sendJson(url,method,body){
  return getJson(url,{method:method,headers:{"Content-Type":"application/json"},body:body==null?undefined:JSON.stringify(body)});
}
function H(html){return {h:html};}
function cell(c){return c&&typeof c==="object"&&"h" in c?c.h:esc(c);}

var S = {opts:null, accounts:[], account:"", accountObj:null, goal:"leads", on:{}, id:"", rec:null, tab:"overview", seq:0, timer:null};
var picker=null;

/* ── The form ─────────────────────────────────────────────── */
var ICON = {
  website:'<path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/><circle cx="12" cy="12" r="9"/>',
  reputation:'<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  social:'<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 10.8 15.8 7.2M8.2 13.2l7.6 3.6"/>',
  history:'<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v4l3 2"/>',
  competitors:'<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>',
  market:'<path d="M4 20V8l8-5 8 5v12zM9 20v-6h6v6"/>'
};
function goalsHtml(){
  return Object.keys(S.opts.goals).map(function(k){
    return '<button type="button" role="radio" class="pm-chip'+(k===S.goal?" on":"")+'" data-g="'+esc(k)+'" aria-checked="'+(k===S.goal)+'">'+esc(S.opts.goals[k])+'</button>';
  }).join("");
}
function blocked(card){
  if(card.needs==="account"&&!S.account) return "Needs a Google Ads account";
  if(card.needs==="website"&&!$("#fSite").value.trim()) return "Needs a website";
  return "";
}
function cardsHtml(){
  return S.opts.cards.map(function(c){
    var why=blocked(c), on=!why&&S.on[c.key];
    return '<button type="button" class="cb-card'+(on?" on":"")+(why?" off":"")+'" data-k="'+esc(c.key)+'" aria-pressed="'+on+'"'+(why?' aria-disabled="true"':"")+'>'+
      '<span class="cb-card-top"><span class="cb-card-ic"><svg viewBox="0 0 24 24" aria-hidden="true">'+(ICON[c.key]||"")+'</svg></span>'+
      '<span class="cb-card-sw" aria-hidden="true"></span></span>'+
      '<span class="cb-card-t">'+esc(c.title)+'</span><span class="cb-card-s">'+esc(c.blurb)+'</span>'+
      '<span class="cb-card-tags">'+(why?'<span class="cb-tag warn">'+esc(why)+'</span>':on?'<span class="cb-tag ok">On</span>':'<span class="cb-tag">Off</span>')+
      (c.paid?'<span class="cb-tag paid">Uses paid data</span>':'')+'</span></button>';
  }).join("");
}
function paintForm(){
  $("#goals").innerHTML=goalsHtml(); $("#cards").innerHTML=cardsHtml();
  if(!S.opts.modelReady) $("#goNote").innerHTML='<b>The AI is not available on this server</b> (no Anthropic key is set), so the business cannot be written up here yet.';
}
function setMsg(html,cls){var m=$("#msg"); m.className=cls||""; m.innerHTML=html?'<div class="'+(cls==="pm-note"?"pm-note":"pm-err")+'" role="alert">'+html+'</div>':"";}

function payload(){
  var research={}; S.opts.cards.forEach(function(c){research[c.key]=!blocked(c)&&!!S.on[c.key];});
  return {name:$("#fName").value, website:$("#fSite").value, account:S.account, industry:$("#fIndustry").value, goal:S.goal,
    locations:$("#fPlaces").value, services:$("#fServices").value, brief:$("#fBrief").value, avoid:$("#fAvoid").value,
    competitors:$("#fComp").value.split(/\n+/).map(function(s){return s.trim();}).filter(Boolean), research:research};
}
function start(){
  setMsg("");
  var p=payload();
  if(p.name.trim().length<2){setMsg("Give the client a name.");$("#fName").focus();return;}
  $("#goBtn").disabled=true;
  sendJson(API+"/start","POST",p).then(function(r){
    $("#goBtn").disabled=false;
    if(!r.ok){setMsg(esc((r.j&&r.j.error)||("The research could not be started (HTTP "+r.status+").")));return;}
    watch(r.j.id);
  }).catch(function(){$("#goBtn").disabled=false;setMsg("The server could not be reached. Try again.");});
}

/* ── A build in progress ──────────────────────────────────── */
var STEP = {pending:["○","Waiting"],running:["◐","Working"],done:["✓","Done"],skipped:["·","Skipped"],failed:["✕","Could not be done"]};
function stepsHtml(steps){
  return (steps||[]).map(function(s){
    var st=STEP[s.state]||STEP.pending;
    return '<li class="cb-step '+esc(s.state)+'"><span class="cb-st" title="'+esc(st[1])+'">'+st[0]+'</span><span class="cb-sl">'+esc(s.label)+
      (s.note?'<span class="cb-sn">'+esc(s.note)+'</span>':"")+'</span></li>';
  }).join("");
}
function showRun(light){
  $("#builder").hidden=true; $("#result").hidden=true; $("#run").hidden=false; $("#recentCard").hidden=false;
  var prog=light.progress||{};
  $("#runTitle").textContent=light.stale?"The research was interrupted":light.status==="error"?"The research stopped":"Researching";
  $("#runSub").textContent=light.stale?"The server restarted while it was working. What was already done is kept.":(prog.phase||"")+(light.started?" · started "+ago(light.started):"");
  $("#runAct").innerHTML=light.stale?'<button type="button" class="pm-btn" id="carryBtn">Finish what is missing</button>':"";
  $("#steps").innerHTML=stepsHtml(prog.steps);
  if(!S.listed){S.listed=true; loadRecent();}
}
function stopPoll(){if(S.timer){clearTimeout(S.timer);S.timer=null;}}
function watch(id){
  stopPoll(); S.id=id; S.rec=null; S.tab="overview";
  try{history.replaceState(null,"",location.pathname+"?build="+encodeURIComponent(id));}catch(e){}
  var seq=++S.seq;
  function tick(){
    getJson(API+"/"+id+"/status").then(function(r){
      if(seq!==S.seq) return;
      if(!r.ok){
        if(r.status===404){leave();setMsg("That research is no longer there.");return;}
        S.timer=setTimeout(tick,5000);return;
      }
      var st=r.j.status;
      if(st==="running"||st==="queued"){showRun(r.j); if(!r.j.stale) S.timer=setTimeout(tick,2500); return;}
      load(id,seq);
    }).catch(function(){ if(seq===S.seq) S.timer=setTimeout(tick,5000); });
  }
  tick();
}
function load(id,seq){
  getJson(API+"/"+id).then(function(r){
    if(seq!==S.seq) return;
    S.listed=false; loadRecent();
    if(!r.ok){setMsg(esc((r.j&&r.j.error)||"That research could not be opened."));leave();return;}
    S.rec=r.j; paintResult();
  });
}
function leave(){
  stopPoll(); S.id=""; S.rec=null; S.seq++; S.listed=false;
  $("#builder").hidden=false; $("#run").hidden=true; $("#result").hidden=true;
  try{history.replaceState(null,"",location.pathname);}catch(e){}
  loadRecent();
}

/* ── The finished report ──────────────────────────────────── */
function tbl(head,rows,cls){
  if(!rows||!rows.length) return "";
  return '<div class="cb-tw"><table class="cb-t'+(cls?" "+cls:"")+'"><thead><tr>'+head.map(function(h){return "<th>"+esc(h)+"</th>";}).join("")+"</tr></thead><tbody>"+
    rows.map(function(r){return "<tr>"+r.map(function(c){return "<td>"+cell(c)+"</td>";}).join("")+"</tr>";}).join("")+"</tbody></table></div>";
}
function ul(items,cls){
  items=(items||[]).filter(Boolean); if(!items.length) return "";
  return '<ul class="cb-ul'+(cls?" "+cls:"")+'">'+items.map(function(x){return "<li>"+cell(x)+"</li>";}).join("")+"</ul>";
}
function sec(title,body,note){
  if(!body) return "";
  return '<section class="pm-panel cb-sec"><div class="pm-panel-h"><span class="pm-panel-t">'+esc(title)+'</span>'+(note?'<span class="pm-panel-s">'+esc(note)+'</span>':"")+'</div>'+body+'</section>';
}
function para(t){return t?'<p class="cb-p">'+esc(t)+'</p>':"";}
function fine(t){return t?'<p class="cb-fine">'+esc(t)+'</p>':"";}
function chips(items,cls){return (items||[]).length?'<div class="cb-chipset">'+items.map(function(x){return '<span class="cb-c'+(cls?" "+cls:"")+'">'+esc(x)+'</span>';}).join("")+'</div>':"";}
function kpi(label,value,sub){return '<div class="cb-kpi"><span>'+esc(label)+'</span><b>'+esc(value)+'</b>'+(sub?'<em>'+esc(sub)+'</em>':"")+'</div>';}
function pill(text,cls){return '<span class="cb-pill'+(cls?" "+cls:"")+'">'+esc(text)+'</span>';}
function h5(t){return '<h5 class="cb-h5">'+esc(t)+'</h5>';}
function seenPill(ok,yes,no){return H(ok?pill(yes,"ok"):pill(no,"failed"));}

var CARD_TITLE = {};
function cardTitle(k){return CARD_TITLE[k]||k;}
function plan(){return (S.rec&&S.rec.plan)||{};}
function core(){return plan().core||{};}
function aud(){return plan().audience||{};}
function imp(){return plan().implications||{};}
function res(){return (S.rec&&S.rec.research)||{};}

function paintResult(){
  var rec=S.rec, p=plan(), inp=rec.inputs||{};
  $("#builder").hidden=true; $("#run").hidden=true; $("#result").hidden=false;
  $("#rName").textContent=rec.name;
  var meta=[];
  if(inp.website) meta.push(esc(shortUrl(inp.website)));
  if(rec.accountName) meta.push("account "+esc(rec.accountName));
  if(inp.industry) meta.push(esc(inp.industry));
  meta.push("by "+esc((rec.createdBy||"").split("@")[0]||"someone")+" "+esc(ago(rec.createdAt)));
  if(rec.cost&&rec.cost.usd!=null) meta.push("cost to research $"+esc(num(rec.cost.usd,2)));
  $("#rMeta").innerHTML=meta.join(" · ");
  var b='';
  if(p.generatedAt){
    b+='<a class="pm-btn" href="'+API+'/'+rec.id+'/download?file=xlsx" download>Excel</a>'+
       '<a class="pm-btn" href="'+API+'/'+rec.id+'/download?file=pdf" download>PDF</a>'+
       '<details class="cb-more"><summary class="pm-btn-ghost">More downloads</summary><div class="cb-menu">'+
       ((imp().questions||[]).length?'<a href="'+API+'/'+rec.id+'/download?file=csv" download>Questions for the client (CSV)</a>':"")+
       '<a href="'+API+'/'+rec.id+'/download?file=zip" download>Every table as CSV (zip)</a></div></details>';
  }
  b+='<button type="button" class="pm-btn-ghost" id="rebuildBtn">Research again from the start</button><button type="button" class="pm-btn-ghost" id="reuseBtn" title="Open the form with this client\'s details filled in">Change the details</button><button type="button" class="pm-btn-ghost" id="backBtn">New research</button><button type="button" class="pm-btn-ghost danger" id="delBtn">Remove</button>';
  $("#rBtns").innerHTML=b;
  var note="";
  if(rec.status==="error") note='<div class="pm-err" role="alert"><b>The research stopped.</b> '+esc(rec.error||"")+' What was already read is kept: press <b>Finish what is missing</b> to carry on from where it stopped.'+
      '<div class="cb-go"><button type="button" class="pm-btn" id="carryBtn">Finish what is missing</button></div></div>';
  else if(rec.note) note='<div class="pm-note warn">'+esc(rec.note)+'</div>';
  $("#rNote").innerHTML=note;
  paintTabs();
}

function tabsList(){
  var p=plan(), r=res(), out=[["overview","Overview"]], rivals=((r.competitors||{}).data||{}).competitors||[];
  if(p.core) out.push(["business","The business"]);
  if(p.audience) out.push(["audience","Who buys and why"]);
  if((r.reputation&&r.reputation.state==="ok")||(imp().reputation||{}).summary) out.push(["reputation","Reputation"]);
  if(r.website&&((r.website.data||{}).pages||[]).length) out.push(["site","Site and tracking"]);
  if(rivals.some(function(c){return c.ok;})||(r.social&&(r.social.data||{}).platforms)||(imp().social||[]).length) out.push(["compete","Competitors and social"]);
  if(p.implications) out.push(["paid","What it means for paid media"]);
  if((imp().questions||[]).length) out.push(["questions","Questions for the client ("+imp().questions.length+")"]);
  out.push(["checks","Checks"+((p.claims||[]).length?" ("+p.claims.length+" to confirm)":"")]);
  return out;
}
function paintTabs(){
  var list=tabsList();
  if(!list.some(function(t){return t[0]===S.tab;})) S.tab="overview";
  $("#tabs").innerHTML=list.map(function(t){return '<button type="button" role="tab" class="cb-tab'+(t[0]===S.tab?" on":"")+'" data-t="'+t[0]+'" aria-selected="'+(t[0]===S.tab)+'">'+esc(t[1])+'</button>';}).join("");
  var fn={overview:tabOverview,business:tabBusiness,audience:tabAudience,reputation:tabReputation,site:tabSite,compete:tabCompete,paid:tabPaid,questions:tabQuestions,checks:tabChecks}[S.tab];
  $("#tabBody").innerHTML=fn();
}
var PRI={high:"high",medium:"medium",low:"low"};
function priPill(x){return H(pill(x||"medium",PRI[x]||"medium"));}

function tabOverview(){
  var c=core(), a=aud(), i=imp(), p=plan(), r=res(), facts=c.facts||[];
  var high=(c.offerings||[]).filter(function(o){return o.priority==="high";}).length;
  var k='<div class="cb-kpis">'+kpi("What it sells",(c.offerings||[]).length+" offerings",high+" high priority for paid search")+kpi("Audiences",(a.audiences||[]).length)+
    kpi("Facts on a page",facts.filter(function(f){return f.verified;}).length+" of "+facts.length)+kpi("Claims to confirm",(p.claims||[]).length,"with the client")+kpi("Questions for the client",(i.questions||[]).length)+'</div>';
  var out=k+sec("In short",ul(i.summary));
  out+=sec("Advertise first",tbl(["What","Why","Page"],(i.advertiseFirst||[]).map(function(x){return [x.what,x.why,H(x.page?link(x.page,shortUrl(x.page)):"")];})));
  var order=["website","reputation","social","history","competitors","market"], rows=order.filter(function(x){return r[x];}).map(function(x){
    var q=r[x]; return [cardTitle(x),H(pill({ok:"Read",partial:"Partly",failed:"Failed",skipped:"Skipped"}[q.state]||q.state,q.state)),q.note||""];});
  out+=sec("What was read",tbl(["Research","Result","What it found"],rows),"Anything that failed is named in the write-up as missing");
  return out;
}
function tabBusiness(){
  var c=core(), pos=c.positioning||{};
  return sec("The business",para(c.summary)+tbl(["Item","Detail"],(c.snapshot||[]).map(function(x){return [x.item,x.detail];})))+
    sec("What it sells",tbl(["Offering","What it is","Who it is for","Price on the page","Page","Priority for paid search","Why"],(c.offerings||[]).map(function(o){
      return [o.name,o.description,o.audience,o.pricing,H(o.page?link(o.page,shortUrl(o.page).replace(/^[^/]+/,"")||"/")+(o.pageSeen?"":' <span class="cb-flag">not among the pages read</span>'):""),priPill(o.priority),o.why];})))+
    sec("What sets it apart",tbl(["Point of difference","Why it matters","Strength","Evidence"],(c.usps||[]).map(function(u){
      var q=u.evidence?'<div class="br-quote">"'+esc(u.evidence)+'"</div>'+(u.verified?"":u.viaSearch?'<span class="cb-flag">only in what the web search found: check its source</span>':'<span class="cb-flag">quote not found on a page</span>'):"";
      return [u.usp,u.why_it_matters,H(pill(u.strength,u.strength==="proven"?"ok":u.strength==="claimed"?"medium":"low")),H(q+(u.source?'<div class="cb-fine">'+link(u.source,shortUrl(u.source))+'</div>':""))];})),
      "Proven means a page of the client's, or what the web search found, backs it word for word")+
    sec("Proof",tbl(["Proof","Where"],(c.proof||[]).map(function(x){return [x.point,H(link(x.source,shortUrl(x.source)))];})))+
    sec("Voice",(pos.promise?'<p class="cb-p"><b>Promise:</b> '+esc(pos.promise)+'</p>':"")+(pos.tone?fine("Tone: "+pos.tone):"")+
      ((pos.wordsUsed||[]).length?h5("Words its pages use")+chips(pos.wordsUsed):"")+((pos.wordsToAvoid||[]).length?h5("Words to avoid")+chips(pos.wordsToAvoid,"bad"):""))+
    sec("Rules that touch the ads",tbl(["Topic","Rule","What it means here"],(c.compliance||[]).map(function(x){return [x.topic,x.rule,x.impact];})))+
    sec("Facts the ads may state",tbl(["Fact","Quoted from the page","Check"],(c.facts||[]).map(function(f){
      return [f.fact,'"'+f.quote+'"',seenPill(f.verified,"On a page","Not found on a page: confirm with the client")];})),
      "A fact the pages do not show is never used as proof without the client's word")+
    sec("What on the site will waste paid traffic",tbl(["Issue","Why it matters","Fix"],(c.siteGaps||[]).map(function(x){return [x.issue,x.why,x.fix];})))+
    sec("Assumptions",ul(c.assumptions));
}
function tabAudience(){
  var a=aud(), v=a.voice||{}, g=a.geography||{};
  return sec("Who buys",tbl(["Audience","Who","Their situation","What they want","What worries them","What they search","Priority"],(a.audiences||[]).map(function(x){
      return [x.name,x.who,x.situation,x.goal,x.worries,x.searches,priPill(x.priority)];})))+
    sec("From search to customer",tbl(["Step","What happens","Friction","The chance"],(a.journey||[]).map(function(x){return [x.stage,x.what_happens,x.friction,x.opportunity];})))+
    sec("What makes someone start looking",ul(a.triggers))+
    sec("What stops them, and the answer",tbl(["Objection","Answer","Proof"],(a.objections||[]).map(function(x){return [x.objection,x.answer,x.proof];})))+
    sec("How to write to them",para(v.style)+((v.vocabulary||[]).length?h5("Words customers use")+chips(v.vocabulary):"")+((v.avoid||[]).length?h5("Keep out")+chips(v.avoid,"bad"):""))+
    sec("Where customers come from",para(g.summary)+tbl(["Area","Note"],(g.areas||[]).map(function(x){return [x.area,x.note];})))+
    sec("Seasons and events",tbl(["When","Why"],(a.seasonality||[]).map(function(x){return [x.window,x.why];})));
}
function tabReputation(){
  var d=(res().reputation||{}).data||{}, r=imp().reputation||{}, co=d.company||{}, keys=Object.keys(co);
  return sec("What the reputation says",para(r.summary)+((r.strengths||[]).length?h5("Lean on")+ul(r.strengths):"")+((r.risks||[]).length?h5("Could hurt")+ul(r.risks,"bad"):"")+((r.actions||[]).length?h5("What to do")+ul(r.actions):""))+
    sec("Ratings",tbl(["Where","Rating","Reviews","Page"],(d.ratings||[]).map(function(x){return [x.platform,H('<span class="br-stars">'+esc(x.rating||"n/a")+'</span>'),x.count||"n/a",H((x.url?link(x.url,shortUrl(x.url)):"")+(x.confirmed?"":' <span class="cb-flag">no page the search read confirms this</span>'))];})),
      "As the web search reported them")+
    sec("Customers praise",ul(d.praise))+sec("Customers complain about",ul(d.complaints))+sec("Customers ask",ul(d.questions))+
    sec("In the press",tbl(["Headline","Where","When"],(d.press||[]).map(function(x){return [x.headline,x.source,x.when];})))+
    sec("Awards",ul(d.awards))+sec("Who is behind it",tbl(["Name","Role"],(d.people||[]).map(function(x){return [x.name,x.role];}))+
      (keys.length?tbl(["About the company",""],keys.map(function(k){return [k.charAt(0).toUpperCase()+k.slice(1),co[k]];})):""))+
    sec("Sources",ul((d.sources||[]).map(function(u){return H(link(u));})))||'<p class="cb-fine">No reputation research was available.</p>';
}
function tabSite(){
  var w=(res().website||{}).data||{}, t=w.tech||{}, pages=w.pages||[], i=imp(), out="";
  out+=sec("What the pages carry",((t.tools||[]).length?'<div class="br-tools">'+t.tools.map(function(x){
      return '<div class="br-tool"><b>'+esc(x.name)+'</b><span>'+esc(x.category)+' · '+x.pages+' page'+(x.pages===1?"":"s")+'</span>'+((x.ids||[]).length?'<span>'+esc(x.ids.join(", "))+'</span>':"")+'</div>';}).join("")+'</div>':
      '<p class="cb-fine">No analytics, advertising, call tracking or booking tags were seen in the page scripts.</p>')+fine(t.note));
  out+=sec("Not seen",ul(t.gaps),"Said as 'not seen', never as 'missing': a tag can load later");
  out+=sec("To check with the client",tbl(["Item","What was seen","What to do"],(i.tracking||[]).map(function(x){return [x.item,x.seen,x.action];})));
  out+=sec("Pages read",tbl(["Page","Title","How it asks for the sale","Forms","Phone"],pages.map(function(pg){return [H(link(pg.url,shortUrl(pg.url))),pg.title||"",(pg.ctas||[]).join(", "),(pg.forms||[]).length,(pg.phones||[]).join(", ")];})));
  return out||'<p class="cb-fine">The website was not read.</p>';
}
function tabCompete(){
  var i=imp(), c=i.competitive||{}, read=((res().competitors||{}).data||{}).competitors||[], soc=res().social||{}, d=soc.data||{}, out="";
  out+=sec("How the client compares",para(c.summary)+((c.edge||[]).length?h5("Where it stands apart")+ul(c.edge):"")+((c.exposed||[]).length?h5("Where it is exposed")+ul(c.exposed,"bad"):""));
  out+=sec("Websites read",tbl(["Competitor","Found","Pages read","Result"],read.map(function(r){return [H(link(r.url,r.name)),r.via||"",(r.pages||[]).length,r.ok?"Read":(r.reason||"Could not be read")];})));
  out+=sec("What the brand's own posts show",tbl(["What the posts show","How to use it in the ads"],(i.social||[]).map(function(x){return [x.insight,x.use];})));
  out+=(d.platforms||[]).filter(function(p){return p.posts;}).map(function(p){
    return sec(p.platform+(p.handle?" · "+p.handle:""),fine(num(p.posts)+" posts")+ul(p.summary)+((p.messaging||[]).length?h5("Messaging and strategy")+ul(p.messaging):""));}).join("");
  return out||'<p class="cb-fine">No competitor or social findings were available.</p>';
}
function tabPaid(){
  var i=imp();
  return sec("Advertise first",tbl(["What","Why","Page","Evidence"],(i.advertiseFirst||[]).map(function(x){return [x.what,x.why,H(x.page?link(x.page,shortUrl(x.page)):""),x.evidence];})))+
    sec("Hold back",tbl(["What","Why"],(i.holdBack||[]).map(function(x){return [x.what,x.why];})))+
    sec("Angles to test",tbl(["Angle","For","Rests on","How it would sound"],(i.angles||[]).map(function(x){return [x.angle,x.audience,x.proof,x.example];})))+
    sec("Tests to run early",tbl(["Hypothesis","How","Metric"],(i.tests||[]).map(function(x){return [x.hypothesis,x.how,x.metric];})),"Each one is a guess to test, not a promise")+
    sec("Risks",tbl(["Risk","What to do"],(i.risks||[]).map(function(x){return [x.risk,x.mitigation];})))+
    sec("Next steps",tbl(["Step","Who","When"],(i.nextSteps||[]).map(function(x){return [x.step,x.owner,x.when];})));
}
function questionsByArea(){
  var order=[], by={};
  (imp().questions||[]).forEach(function(q){ if(!by[q.area]){by[q.area]=[];order.push(q.area);} by[q.area].push(q); });
  return order.map(function(a){return {area:a,items:by[a]};});
}
function questionsText(){
  return questionsByArea().map(function(g){return g.area+"\n"+g.items.map(function(q,i){return (i+1)+". "+q.question;}).join("\n");}).join("\n\n");
}
function tabQuestions(){
  var out='<div class="cb-go"><button type="button" class="pm-btn" id="copyQ">Copy the questions</button><a class="pm-btn-ghost" download href="'+API+'/'+S.rec.id+'/download?file=csv">CSV</a></div>'+
    fine("Plain text, grouped by area, ready to paste into an email. Each question says why it matters and when it is needed.");
  return out+questionsByArea().map(function(g){
    return '<div class="br-qgroup"><h4>'+esc(g.area)+'</h4>'+tbl(["Question","Why it matters","When"],g.items.map(function(q){return [q.question,q.why,H(pill(q.urgency,q.urgency==="Before launch"?"failed":q.urgency==="First month"?"medium":"low"))];}))+'</div>';
  }).join("");
}
function tabChecks(){
  var p=plan(), c=p.checks||{}, out="";
  out+=sec("Claims to confirm with the client",tbl(["Where","Text","Claim not found on the client's pages or in what the search found"],(p.claims||[]).map(function(x){return [x.where,x.text,x.claim];})),
    "In the write-up, but not on the client's pages or in the brief");
  out+=sec("What was not available",ul(c.missing),"The write-up says where it is working without these");
  if(c.unconfirmedFacts) out+=sec("Facts not found on a page",'<p class="cb-p">'+c.unconfirmedFacts+' fact'+(c.unconfirmedFacts===1?"":"s")+' could not be found word for word on the pages that were read. They are marked in The business tab.</p>');
  if(c.unconfirmedUsps) out+=sec("Points of difference without a quote",'<p class="cb-p">'+c.unconfirmedUsps+' point'+(c.unconfirmedUsps===1?"":"s")+' of difference could not be backed with a quote from a page, so '+(c.unconfirmedUsps===1?"it is":"they are")+' shown as claimed.</p>');
  var cost=S.rec.cost||{};
  if(cost.stages) out+=sec("What it cost",'<p class="cb-fine">Writing: $'+num(cost.model,2)+' in '+cost.calls+' calls. Paid research: $'+num(cost.research,2)+'. Total: $'+num(cost.usd,2)+'.</p>');
  return out||'<p class="cb-fine">Nothing to check.</p>';
}

/* The details of a finished build, put back in the form so they can be changed and run again as a new one. */
function reuse(rec){
  var i=(rec&&rec.inputs)||{};
  leave();
  var set=function(sel,v){$(sel).value=v==null?"":String(v);};
  set("#fName",i.name); set("#fSite",shortUrl(i.website)); set("#fIndustry",i.industry); set("#fPlaces",i.locations); set("#fServices",i.services); set("#fBrief",i.brief); set("#fAvoid",i.avoid);
  set("#fComp",(i.competitors||[]).map(function(c){return c.url||c.name;}).join("\n"));
  S.goal=i.goal||"leads"; S.account=i.account||""; S.accountObj=S.accounts.filter(function(a){return a.id===S.account;})[0]||null;
  picker.set(S.pickerItems,S.account);
  S.opts.cards.forEach(function(c){S.on[c.key]=i.research?!!i.research[c.key]:!!c.default;});
  paintForm(); window.scrollTo&&window.scrollTo(0,0);
}
function copyText(text,done){
  function fallback(){
    var t=document.createElement("textarea"); t.value=text; t.setAttribute("readonly",""); t.style.position="fixed"; t.style.opacity="0"; document.body.appendChild(t); t.select();
    var ok=false; try{ok=document.execCommand("copy");}catch(e){} document.body.removeChild(t); done(ok);
  }
  if(navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function(){done(true);},fallback); else fallback();
}

/* ── Saved research ───────────────────────────────────────── */
var STATE_PILL={done:["Ready","ok"],running:["Working","run"],queued:["Waiting","run"],error:["Stopped","failed"]};
function loadRecent(){
  getJson(API+"/list").then(function(r){
    var box=$("#recent");
    if(!r.ok){box.innerHTML='<p class="cb-fine">'+esc((r.j&&r.j.error)||"The saved research could not be listed.")+'</p>';return;}
    var rows=r.j.builds||[];
    if(!rows.length){box.innerHTML='<p class="cb-fine">Nothing has been researched yet.</p>';return;}
    box.innerHTML=rows.map(function(b){
      var p=b.stale?["Interrupted","failed"]:(STATE_PILL[b.status]||[b.status,""]);
      return '<div class="cb-rec"><button type="button" class="cb-open" data-id="'+esc(b.id)+'"><span class="sq-av" style="--h:'+hueOf(b.name)+'">'+esc(inits(b.name))+'</span>'+
        '<span class="cb-rn"><b>'+esc(b.name)+'</b><em>'+esc(shortUrl(b.website)||b.accountName||"no website")+' · '+esc((b.createdBy||"").split("@")[0])+' · '+esc(ago(b.createdAt))+'</em></span>'+
        '<span class="cb-rs">'+pill(p[0],p[1])+(b.cost!=null?'<em>$'+num(b.cost,2)+'</em>':"")+'</span></button></div>';
    }).join("");
  }).catch(function(){$("#recent").innerHTML='<p class="cb-fine">The saved research could not be listed.</p>';});
}

/* ── Events ───────────────────────────────────────────────── */
document.addEventListener("click",function(e){
  var t=e.target;
  var chip=t.closest&&t.closest("#goals [data-g]"); if(chip){S.goal=chip.getAttribute("data-g"); paintForm(); return;}
  var card=t.closest&&t.closest("#cards [data-k]");
  if(card){var key=card.getAttribute("data-k"), c=S.opts.cards.filter(function(x){return x.key===key;})[0]; if(c&&!blocked(c)){S.on[key]=!S.on[key]; paintForm();} return;}
  var tab=t.closest&&t.closest("#tabs [data-t]"); if(tab){S.tab=tab.getAttribute("data-t"); paintTabs(); return;}
  var op=t.closest&&t.closest(".cb-open"); if(op){watch(op.getAttribute("data-id")); return;}
  if(t.id==="goBtn"){start(); return;}
  if(t.id==="runBack"||t.id==="backBtn"){leave(); return;}
  if(t.id==="reuseBtn"){reuse(S.rec); return;}
  if(t.id==="copyQ"){copyText(questionsText(),function(ok){t.textContent=ok?"Copied":"Could not copy: select the text and copy it";});return;}
  if(t.id==="delBtn"){
    if(!window.confirm("Remove this research from the list? The files you downloaded are not affected.")) return;
    sendJson(API+"/"+S.id,"DELETE").then(function(r){ if(!r.ok){setMsg(esc((r.j&&r.j.error)||"It could not be removed."));return;} leave(); });
    return;
  }
  if(t.id==="rebuildBtn"||t.id==="carryBtn"){
    var resume=t.id!=="rebuildBtn";
    if(!resume&&!window.confirm("Research this again from the start? The reading and the writing are redone.")) return;
    sendJson(API+"/"+S.id+"/rebuild","POST",{resume:resume}).then(function(r){
      if(!r.ok){setMsg(esc((r.j&&r.j.error)||"It could not be started."),r.status===429||r.status===409?"pm-note":"");return;}
      setMsg(""); watch(S.id);
    });
    return;
  }
});
document.addEventListener("input",function(e){ if(e.target.id==="fSite") paintForm(); });
document.addEventListener("click",function(e){var d=document.querySelector(".cb-more[open]"); if(d&&!d.contains(e.target)) d.removeAttribute("open");});

/* ── Start ────────────────────────────────────────────────── */
function init(){
  picker=window.PMPicker($("#acctPick"),{label:"Google Ads account (optional)",search:true,onChange:function(v){
    S.account=v; S.accountObj=S.accounts.filter(function(a){return a.id===v;})[0]||null;
    if(S.accountObj&&!$("#fName").value.trim()) $("#fName").value=S.accountObj.name;
    paintForm();
  }});
  picker.set([{value:"",label:"No account",sub:"Website and brief only",avatar:"?",hue:200}],"",true);
  Promise.all([getJson(API+"/options"),getJson("/p2/paid-marketing/api/segments/accounts")]).then(function(rs){
    var o=rs[0], a=rs[1];
    if(!o.ok){setMsg(esc((o.j&&o.j.error)||"The page could not load its options."));return;}
    S.opts=o.j; S.opts.cards.forEach(function(c){S.on[c.key]=!!c.default; CARD_TITLE[c.key]=c.title;});
    S.accounts=(a.ok&&a.j.accounts)||[];
    var items=[{value:"",label:"No account",sub:"Website and brief only",avatar:"?",hue:200}];
    S.accounts.forEach(function(x){items.push({value:x.id,label:x.name,avatar:inits(x.name),hue:hueOf(x.name),sub:dashed(x.id)});});
    S.pickerItems=items; picker.set(items,"");
    if(!a.ok) $("#acctHint").textContent="The Google Ads accounts could not be listed, so this research can use the website and the brief only.";
    paintForm(); loadRecent();
    var want=(location.search.match(/[?&]build=([0-9a-f]{32})/)||[])[1]; if(want) watch(want);
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
}
if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",init); else init();
})();
