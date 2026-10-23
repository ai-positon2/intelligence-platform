/* Strategy & Campaign Book (Paid Marketing Agents).
 *
 * The form on this page collects a client and the research to run; the server does the reading and the writing in the background
 * (tracker/paid_strategy_job.py) and this page polls it, then draws the finished plan: the strategy in tabs, every campaign opened out
 * (ad groups, keywords, ads, assets) and each campaign's Campaign Book in the QA Agent's layout. It never writes, counts or judges
 * anything for itself: every number and sentence it shows came from the server.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/strategy";
var $ = function(s,r){return (r||document).querySelector(s)};
var $$ = function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return String(n).replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function num(v,nd){return v==null||v===""||isNaN(v)?"n/a":Number(v).toLocaleString("en-US",{maximumFractionDigits:nd==null?0:nd});}
function money(v,cur){return v==null||v===""||isNaN(v)?"n/a":Number(v).toLocaleString("en-US",{maximumFractionDigits:v>=100?0:2})+(cur?" "+cur:"");}
function safeHttp(u){u=String(u||"").trim();return /^https?:\/\/[^\s"'<>]+$/i.test(u)?u:"";}
function link(u,label){var s=safeHttp(u);return s?'<a href="'+esc(s)+'" target="_blank" rel="noopener noreferrer">'+esc(label||s)+'</a>':esc(label||u||"");}
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

var S = {opts:null, accounts:[], account:"", accountObj:null, goal:"leads", on:{}, kinds:[], id:"", rec:null, tab:"overview", camp:"", openC:{}, seq:0, timer:null, filter:""};
var picker=null;

/* ── The form ─────────────────────────────────────────────── */
var ICON = {
  website:'<path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/><circle cx="12" cy="12" r="9"/>',
  competitors:'<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>',
  social:'<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 10.8 15.8 7.2M8.2 13.2l7.6 3.6"/>',
  history:'<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v4l3 2"/>',
  keywords:'<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  seasonality:'<path d="M3 15c3 0 3-8 6-8s3 10 6 10 3-6 6-6"/>',
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
function kindsHtml(){
  var auto=!S.kinds.length, out='<button type="button" class="pm-chip'+(auto?" on":"")+'" data-kind="" aria-pressed="'+auto+'">Let the strategist choose</button>';
  Object.keys(S.opts.kinds).forEach(function(k){
    var on=S.kinds.indexOf(k)>=0;
    out+='<button type="button" class="pm-chip'+(on?" on":"")+'" data-kind="'+esc(k)+'" aria-pressed="'+on+'">'+esc(S.opts.kinds[k])+'</button>';
  });
  return out;
}
function paintForm(){
  $("#goals").innerHTML=goalsHtml(); $("#cards").innerHTML=cardsHtml(); $("#kinds").innerHTML=kindsHtml();
  $("#curTag").textContent=S.accountObj&&S.accountObj.currency?"("+S.accountObj.currency+")":"";
  var warn=$("#goNote");
  if(!S.opts.modelReady) warn.innerHTML='<b>The AI is not available on this server</b> (no Anthropic key is set), so a strategy cannot be written here yet.';
}
function setMsg(html,cls){var m=$("#msg"); m.className=cls||""; m.innerHTML=html?'<div class="'+(cls==="pm-note"?"pm-note":"pm-err")+'" role="alert">'+html+'</div>':"";}

function payload(){
  var research={}; S.opts.cards.forEach(function(c){research[c.key]=!blocked(c)&&!!S.on[c.key];});
  return {name:$("#fName").value, website:$("#fSite").value, account:S.account, industry:$("#fIndustry").value, goal:S.goal,
    budget:$("#fBudget").value, launch:$("#fLaunch").value, locations:$("#fPlaces").value, services:$("#fServices").value, brief:$("#fBrief").value,
    avoid:$("#fAvoid").value, competitors:$("#fComp").value.split(/\n+/).map(function(s){return s.trim();}).filter(Boolean),
    campaignTypes:S.kinds.slice(), research:research};
}
function start(){
  setMsg("");
  var p=payload();
  if(p.name.trim().length<2){setMsg("Give the client a name.");$("#fName").focus();return;}
  $("#goBtn").disabled=true;
  sendJson(API+"/start","POST",p).then(function(r){
    $("#goBtn").disabled=false;
    if(!r.ok){setMsg(esc((r.j&&r.j.error)||("The build could not be started (HTTP "+r.status+").")));return;}
    watch(r.j.id);
  }).catch(function(){$("#goBtn").disabled=false;setMsg("The server could not be reached. Try again.");});
}

/* ── A build in progress ──────────────────────────────────── */
var STEP = {pending:["○","Waiting"],running:["◐","Working"],done:["✓","Done"],skipped:["\u00b7","Skipped"],failed:["✕","Could not be done"]};
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
  $("#runTitle").textContent=light.stale?"The build was interrupted":light.status==="error"?"The build stopped":"Building";
  $("#runSub").textContent=light.stale?"The server restarted while it was working. What was already done is kept.":(prog.phase||"")+(light.started?" \u00b7 started "+ago(light.started):"");
  $("#runAct").innerHTML=light.stale?'<button type="button" class="pm-btn" id="carryBtn">Finish what is missing</button>':"";
  $("#steps").innerHTML=stepsHtml(prog.steps);
  if(!S.listed){S.listed=true; loadRecent();}                                // the build that has just started joins the saved list
}
function stopPoll(){if(S.timer){clearTimeout(S.timer);S.timer=null;}}
function watch(id){
  stopPoll(); S.id=id; S.rec=null; S.camp=""; S.openC={}; S.tab="overview";
  try{history.replaceState(null,"",location.pathname+"?build="+encodeURIComponent(id));}catch(e){}
  var seq=++S.seq;
  function tick(){
    getJson(API+"/"+id+"/status").then(function(r){
      if(seq!==S.seq) return;
      if(!r.ok){
        if(r.status===404){leave();setMsg("That build is no longer there.");return;}
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
    if(!r.ok){setMsg(esc((r.j&&r.j.error)||"That build could not be opened."));leave();return;}
    S.rec=r.j; paintResult();
  });
}
function leave(){
  stopPoll(); S.id=""; S.rec=null; S.seq++; S.listed=false;
  $("#builder").hidden=false; $("#run").hidden=true; $("#result").hidden=true;
  try{history.replaceState(null,"",location.pathname);}catch(e){}
  loadRecent();
}

/* ── The finished plan ────────────────────────────────────── */
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
function chips(items,cls){return (items||[]).length?'<div class="cb-chipset">'+items.map(function(x){return '<span class="cb-c'+(cls?" "+cls:"")+'">'+esc(x)+'</span>';}).join("")+'</div>':"";}
function kpi(label,value,sub){return '<div class="cb-kpi"><span>'+esc(label)+'</span><b>'+esc(value)+'</b>'+(sub?'<em>'+esc(sub)+'</em>':"")+'</div>';}
function pill(text,cls){return '<span class="cb-pill'+(cls?" "+cls:"")+'">'+esc(text)+'</span>';}

var CARD_TITLE = {};
function cardTitle(k){return CARD_TITLE[k]||k;}

function paintResult(){
  var rec=S.rec, plan=rec.plan||{}, inp=rec.inputs||{};
  $("#builder").hidden=true; $("#run").hidden=true; $("#result").hidden=false;
  $("#rName").textContent=rec.name;
  var meta=[];
  if(inp.website) meta.push(esc(inp.website.replace(/^https?:\/\//,"").replace(/\/$/,"")));
  if(rec.accountName) meta.push("account "+esc(rec.accountName));
  if(inp.budget) meta.push(esc(money(inp.budget,inp.currency))+" a month");
  if(inp.launch) meta.push("launch "+esc(inp.launch));
  meta.push("by "+esc((rec.createdBy||"").split("@")[0]||"someone")+" "+esc(ago(rec.createdAt)));
  if(rec.cost&&rec.cost.usd!=null) meta.push("cost to build $"+esc(num(rec.cost.usd,2)));
  $("#rMeta").innerHTML=meta.join(" · ");
  var camps=plan.campaigns||[], ready=camps.length>0, missingBuild=camps.filter(function(c){return !c.build||!Object.keys(c.build).length;}).length;
  var b='';
  if(ready){
    b+='<a class="pm-btn" href="'+API+'/'+rec.id+'/download?file=xlsx" download>Excel</a>'+
       '<a class="pm-btn" href="'+API+'/'+rec.id+'/download?file=csv" download>CSV</a>'+
       '<a class="pm-btn" href="'+API+'/'+rec.id+'/download?file=pdf" download>PDF</a>'+
       '<details class="cb-more"><summary class="pm-btn-ghost">More downloads</summary><div class="cb-menu">'+
       '<a href="'+API+'/'+rec.id+'/download?file=zip" download>Every table as CSV (zip)</a>'+
       camps.filter(function(c){return c.book&&c.book.length;}).map(function(c){
         return '<span class="cb-menu-h">'+esc(c.name)+' (for the QA Agent)</span><a href="'+API+'/'+rec.id+'/download?file=book-xlsx&campaign='+encodeURIComponent(c.key)+'" download>Campaign Book, Excel</a>'+
           '<a href="'+API+'/'+rec.id+'/download?file=book-csv&campaign='+encodeURIComponent(c.key)+'" download>Campaign Book, CSV</a>';}).join("")+
       '</div></details>';
  }
  if(missingBuild&&rec.status!=="running") b+='<button type="button" class="pm-btn-ghost" id="resumeBtn">Write the '+missingBuild+' campaign'+(missingBuild===1?"":"s")+' that '+(missingBuild===1?"is":"are")+' missing</button>';
  b+='<button type="button" class="pm-btn-ghost" id="rebuildBtn">Build again from the start</button><button type="button" class="pm-btn-ghost" id="reuseBtn" title="Open the form with this client\'s details filled in">Change the details</button><button type="button" class="pm-btn-ghost" id="backBtn">New build</button><button type="button" class="pm-btn-ghost danger" id="delBtn">Remove</button>';
  $("#rBtns").innerHTML=b;
  var note="";
  if(rec.status==="error") note='<div class="pm-err" role="alert"><b>The build stopped.</b> '+esc(rec.error||"")+' What was already read is kept: '+(Object.keys(rec.research||{}).length?"open the tabs below, or ":"")+'press <b>Finish what is missing</b> to carry on from where it stopped.'+
      '<div class="cb-go"><button type="button" class="pm-btn" id="carryBtn">Finish what is missing</button></div></div>';
  else if(rec.note) note='<div class="pm-note warn">'+esc(rec.note)+'</div>';
  $("#rNote").innerHTML=note;
  paintTabs();
}

function tabsList(){
  var rec=S.rec, plan=rec.plan||{}, r=rec.research||{}, out=[["overview","Overview"]];
  if(plan.profile) out.push(["business","The business"]);
  if(((plan.competitive||{}).competitors||[]).length||((r.competitors||{}).data||{}).competitors&&r.competitors.data.competitors.length) out.push(["competitors","Competitors"]);
  if(r.social&&(r.social.data||{}).platforms||(plan.strategy||{}).social&&(plan.strategy.social||[]).length) out.push(["social","Social media"]);
  if(r.history&&(r.history.data||{}).campaigns) out.push(["account","Google Ads account"]);
  if(r.keywords&&((r.keywords.data||{}).ideas||[]).length) out.push(["keywords","Keywords"]);
  if(r.market&&r.market.state==="ok"||r.seasonality&&r.seasonality.state==="ok") out.push(["market","Market and season"]);
  if(plan.strategy) out.push(["strategy","Strategy"]);
  if((plan.campaigns||[]).length) out.push(["campaigns","Campaigns ("+plan.campaigns.length+")"],["book","Campaign Book"]);
  out.push(["checks","Checks"+((plan.claims||[]).length?" ("+plan.claims.length+" to confirm)":"")]);
  return out;
}
function paintTabs(){
  var list=tabsList();
  if(!list.some(function(t){return t[0]===S.tab;})) S.tab="overview";
  $("#tabs").innerHTML=list.map(function(t){return '<button type="button" role="tab" class="cb-tab'+(t[0]===S.tab?" on":"")+'" data-t="'+t[0]+'" aria-selected="'+(t[0]===S.tab)+'">'+esc(t[1])+'</button>';}).join("");
  var fn={overview:tabOverview,business:tabBusiness,competitors:tabCompetitors,social:tabSocial,account:tabAccount,keywords:tabKeywords,market:tabMarket,strategy:tabStrategy,campaigns:tabCampaigns,book:tabBook,checks:tabChecks}[S.tab];
  $("#tabBody").innerHTML=fn();
}

function tabOverview(){
  var rec=S.rec, plan=rec.plan||{}, st=plan.strategy||{}, inp=rec.inputs||{}, cur=inp.currency, fc=plan.forecast, res=rec.research||{};
  var k='<div class="cb-kpis">'+kpi("Monthly budget",inp.budget?money(inp.budget,cur):"Not given")+kpi("Campaigns",(plan.campaigns||[]).length)+
    (fc?kpi("Expected conversions",num((fc.scenarios[1]||{}).conversions,0)+" a month","about "+money((fc.scenarios[1]||{}).cpa,cur)+" each"):kpi("Forecast","None","Needs a budget and a cost per click"))+
    kpi("Claims to confirm",(plan.claims||[]).length,"with the client")+kpi("Facts on a page",((plan.profile||{}).facts||[]).filter(function(f){return f.verified;}).length+" of "+((plan.profile||{}).facts||[]).length)+'</div>';
  var out=k+sec("The plan in short",ul(st.summary));
  if(fc) out+=sec("What the budget buys",tbl(["Scenario","Cost per click","Conversion rate","Clicks","Conversions","Cost per conversion"],fc.scenarios.map(function(s){
      return [s.scenario,money(s.cpc),num(s.cvr,1)+"%",num(s.clicks),num(s.conversions,1),money(s.cpa)];}))+
    '<p class="cb-fine">Cost per click: '+esc(fc.cpcFrom)+'.<br>Conversion rate: '+esc(fc.cvrFrom)+'.<br>'+esc(fc.note)+'</p>',"Worked out in code, not guessed by the model");
  var alloc=(st.budget||{}).allocation||[];
  if(alloc.length) out+=sec("Where the money goes",tbl(["Campaign","Share","A day","A month","Why"],alloc.map(function(a){return [a.campaign,num(a.share,1)+"%",money(a.daily,cur),money(a.monthly,cur),a.note];})));
  if((st.objectives||[]).length) out+=sec("Objectives",tbl(["Objective","KPI","Target","Basis"],st.objectives.map(function(o){return [o.objective,o.kpi,o.target,o.basis];})));
  var order=["website","competitors","social","history","keywords","seasonality","market"], rows=order.filter(function(x){return res[x];}).map(function(x){
    var p=res[x]; return [cardTitle(x),H(pill({ok:"Read",partial:"Partly",failed:"Failed",skipped:"Skipped"}[p.state]||p.state,p.state)),p.note||""];});
  out+=sec("What was read",tbl(["Research","Result","What it found"],rows),"Anything that failed is named in the strategy as missing");
  return out;
}
function tabBusiness(){
  var p=S.rec.plan.profile||{}, pos=p.positioning||{};
  return sec("The business",para(p.summary))+
    sec("What it sells",tbl(["Offering","What it is","Priority for paid search"],(p.offerings||[]).map(function(o){return [o.name,o.description,H(pill(o.priority,o.priority))];})))+
    sec("Who buys",tbl(["Audience","Who","Need","Intent"],(p.audiences||[]).map(function(a){return [a.name,a.who,a.need,a.intent];})))+
    sec("Positioning",para(pos.promise)+ul(pos.differentiators)+(pos.tone?'<p class="cb-fine">Voice: '+esc(pos.tone)+'</p>':""))+
    sec("Proof the site can back",tbl(["Proof","Where"],(p.proof||[]).map(function(x){return [x.point,H(link(x.source))];})))+
    sec("How a visitor becomes a lead",tbl(["Action","Where","Note"],(p.conversionPaths||[]).map(function(x){return [x.action,x.where,x.note];})))+
    sec("Rules that touch the ads",tbl(["Topic","Rule","What it means here"],(p.compliance||[]).map(function(x){return [x.topic,x.rule,x.impact];})))+
    sec("What on the site will waste paid traffic",tbl(["Issue","Why it matters","Fix"],(p.siteGaps||[]).map(function(x){return [x.issue,x.why,x.fix];})))+
    sec("Facts the ads may state",tbl(["Fact","Quoted from the page","Check"],(p.facts||[]).map(function(f){
      return [f.fact,'"'+f.quote+'"',H(f.verified?pill("On "+(f.source?"the page":"a page"),"ok"):pill("Not found on a page: confirm with the client","failed"))];})),
      "A fact the pages do not show is never used as proof without the client's word")+
    sec("Assumptions",ul(p.assumptions))+sec("Questions for the client",ul(p.questions));
}
function tabCompetitors(){
  var c=S.rec.plan.competitive||{}, read=((S.rec.research.competitors||{}).data||{}).competitors||[];
  var out=sec("The landscape",para(c.landscape)+(c.competitors||[]).map(function(x){
    return '<div class="cb-comp"><h4>'+esc(x.name)+' <span>'+link(x.url,x.url.replace(/^https?:\/\//,"").replace(/\/$/,""))+'</span></h4>'+para(x.positioning)+
      '<div class="cb-cols">'+['offers:Offers','messages:What it leads with','ctas:How it asks for the sale','strengths:Strengths','weaknesses:Weaknesses','themes:Search themes'].map(function(s){
        var k=s.split(":");return (x[k[0]]||[]).length?'<div><h5>'+k[1]+'</h5>'+ul(x[k[0]])+'</div>':"";}).join("")+'</div></div>';}).join(""));
  out+=sec("Open space",tbl(["Opportunity","Why it is open"],(c.whiteSpace||[]).map(function(x){return [x.opportunity,x.why];})))+
    sec("How to stand apart",ul(c.differentiation))+sec("Watch out for",ul(c.watchOuts));
  out+=sec("Websites read",tbl(["Competitor","Found","Pages read","Result"],read.map(function(r){
    return [H(link(r.url,r.name)),r.via||"",(r.pages||[]).length,r.ok?"Read":(r.reason||"Could not be read")];})));
  return out||'<p class="cb-fine">No competitor was read.</p>';
}
function tabSocial(){
  var soc=(S.rec.research.social||{}), d=soc.data||{}, st=(S.rec.plan.strategy||{}).social||[], out="";
  if(soc.note) out+='<div class="pm-note">'+esc(soc.note)+'</div>';
  out+=sec("How to use it",tbl(["What the brand's own posts show","How to use it in the ads"],st.map(function(x){return [x.insight,x.use];})));
  out+=(d.platforms||[]).filter(function(p){return p.posts;}).map(function(p){
    return sec(p.platform+(p.handle?" · "+p.handle:""),'<p class="cb-fine">'+num(p.posts)+' posts'+(p.profileUrl?' · '+link(p.profileUrl,"profile"):"")+'</p>'+ul(p.summary)+(p.messaging.length?'<h5 class="cb-h5">Messaging and strategy</h5>'+ul(p.messaging):""));}).join("");
  var cp=d.crossPlatform||{};
  out+=sec("Across platforms",ul(cp.summary)+(cp.messaging&&cp.messaging.length?'<h5 class="cb-h5">Messaging and strategy</h5>'+ul(cp.messaging):""));
  out+=sec("Most engaging posts",tbl(["Platform","Post","Engagement"],(d.topPosts||[]).map(function(p){return [p.platform+(p.type?" "+p.type:""),H(p.url?link(p.url,p.caption||"Open the post"):esc(p.caption)),num(p.engagement)];})));
  return out||'<p class="cb-fine">No social findings were available.</p>';
}
function tabAccount(){
  var h=(S.rec.research.history||{}).data||{}, t=h.totals||{}, cur=(h.account||{}).currency, tm=h.terms||{};
  var out='<div class="cb-kpis">'+kpi("Spent",money(t.cost,cur),(h.window?h.window.start+" to "+h.window.end:""))+kpi("Conversions",num(t.conv,0),t.cpa?money(t.cpa,cur)+" each":"")+kpi("Conversion rate",t.cvr==null?"n/a":num(t.cvr,1)+"%")+kpi("Cost per click",money(t.cpc,cur))+kpi("Click through rate",t.ctr==null?"n/a":num(t.ctr,1)+"%")+'</div>';
  out+=sec("Campaigns",tbl(["Campaign","Type","Status","Bidding","Daily","Clicks","Cost","Conv.","Per conv."],(h.campaigns||[]).map(function(c){
    return [c.name,c.type,c.status.toLowerCase(),c.bid,money(c.daily),num(c.clicks),money(c.cost),num(c.conv,1),money(c.cpa)];})));
  out+=sec("Conversion actions",tbl(["Name","Type","Category","Primary"],(h.actions||[]).map(function(a){return [a.name,a.type,a.category,a.primary?"Yes":"No"];})));
  out+=sec("Keywords that converted",tbl(["Keyword","Match","Campaign","Clicks","Cost","Conv."],(h.keywordsBest||[]).map(function(k){return [k.text,k.match,k.campaign,num(k.clicks),money(k.cost),num(k.conv,1)];})));
  out+=sec("Keywords that spent and did not convert",tbl(["Keyword","Match","Campaign","Clicks","Cost"],(h.keywordsWasted||[]).map(function(k){return [k.text,k.match,k.campaign,num(k.clicks),money(k.cost)];})));
  out+=sec("Search terms that converted",tbl(["Search term","Campaign","Clicks","Cost","Conv."],(tm.converting||[]).map(function(k){return [k.term,k.campaign,num(k.clicks),money(k.cost),num(k.conv,1)];})));
  out+=sec("Search terms that spent and did not convert",tbl(["Search term","Campaign","Clicks","Cost"],(tm.wasteful||[]).map(function(k){return [k.term,k.campaign,num(k.clicks),money(k.cost)];})));
  out+=sec("Devices",tbl(["Device","Clicks","Cost","Conv.","Per conv."],(h.devices||[]).map(function(d){return [d.device,num(d.clicks),money(d.cost),num(d.conv,1),money(d.cpa)];})));
  out+=sec("Places",tbl(["Place","Clicks","Cost","Conv.","Per conv."],(h.regions||[]).map(function(d){return [d.name,num(d.clicks),money(d.cost),num(d.conv,1),money(d.cpa)];})));
  out+=sec("The live ads",(h.ads||[]).map(function(a){return '<div class="cb-oldad"><b>'+esc(a.campaign)+'</b> · '+num(a.clicks)+' clicks, '+num(a.conv,1)+' conversions'+ul(a.headlines.slice(0,8))+'</div>';}).join(""));
  return out;
}
function tabKeywords(){
  var d=(S.rec.research.keywords||{}).data||{}, q=S.filter.trim().toLowerCase();
  var ideas=(d.ideas||[]).filter(function(i){return !q||i.text.indexOf(q)>=0;}).slice(0,150);
  return '<div class="cb-kwtools"><label><span class="pd-dd-k">Find a keyword</span><input type="text" id="kwFind" value="'+esc(S.filter)+'" placeholder="Filter the ideas…" autocomplete="off"></label>'+
    '<p class="cb-fine">'+num(d.total)+' ideas from Google Keyword Planner'+((d.places||[]).length?' for '+esc(d.places.map(function(p){return p.canonical;}).join("; ")):"")+'. Bids are the range advertisers pay for the top of the page.</p></div>'+
    tbl(["Keyword","Searches a month","Competition","Top of page bid","Peak","Lowest","From"],ideas.map(function(i){
      return [i.text,num(i.avg),i.competition||"n/a",(i.lowBid||i.highBid)?money(i.lowBid)+" to "+money(i.highBid):"n/a",i.peak||"",i.low||"",i.from];}),"cb-kwt");
}
function tabMarket(){
  var m=(S.rec.research.market||{}).data||{}, s=(S.rec.research.seasonality||{}).data||{}, out="";
  out+=sec("What drives demand",ul(m.demand))+sec("How people choose",ul(m.decisionFactors))+sec("What changed lately",ul(m.trends))+
    sec("Advertising rules to follow",tbl(["Rule","Applies to"],(m.adPolicy||[]).map(function(x){return [x.rule,x.appliesTo];})))+sec("What it costs",ul(m.costSignals))+
    sec("Sources",ul((m.sources||[]).map(function(u){return H(link(u));})));
  if(s.months&&s.months.length) out+=sec("The account's year",tbl(["Month","Conversions index","Spend index"],s.months.map(function(x){return [x.month,x.conv==null?"n/a":x.conv,x.cost==null?"n/a":x.cost];}))+
    ul(s.headlines),"100 is an average month for this account");
  else if(s.note) out+=sec("The account's year",'<p class="cb-fine">'+esc(s.note)+'</p>');
  return out||'<p class="cb-fine">No market or seasonality research was available.</p>';
}
function tabStrategy(){
  var s=S.rec.plan.strategy||{}, t=s.targeting||{}, m=s.messaging||{}, ms=s.measurement||{}, b=s.budget||{};
  return sec("Funnel",tbl(["Stage","Role","Message","Channels"],(s.funnel||[]).map(function(x){return [x.stage,x.role,x.message,x.channels];})))+
    sec("Channels",tbl(["Channel","Share","Priority","Phase","Why"],(s.channels||[]).map(function(x){return [x.channel,x.share==null?"":num(x.share,0)+"%",x.priority,x.phase,x.why];})))+
    sec("Budget",para(b.pacing)+(b.scaleRule?'<p class="cb-fine">Raise it when: '+esc(b.scaleRule)+'</p>':""))+
    sec("Bidding",tbl(["Phase","Strategy","Move on when","Note"],(s.bidding||[]).map(function(x){return [x.phase,x.strategy,x.trigger,x.note];})))+
    sec("Targeting",tbl(["Places","Why"],(t.locations||[]).map(function(x){return [x.name,x.why];}))+'<p class="cb-fine">Devices: '+esc(t.devices||"n/a")+'<br>Schedule: '+esc(t.schedule||"n/a")+'<br>Demographics: '+esc(t.demographics||"n/a")+'</p>'+chips(t.audiences))+
    sec("Messaging",tbl(["Pillar","Proof","Example"],(m.pillars||[]).map(function(x){return [x.pillar,x.proof,x.example];}))+(m.tone?'<p class="cb-fine">Tone: '+esc(m.tone)+'</p>':"")+
      (m.offers&&m.offers.length?'<h5 class="cb-h5">Offers to use</h5>'+chips(m.offers,"ok"):"")+(m.avoid&&m.avoid.length?'<h5 class="cb-h5">Avoid</h5>'+chips(m.avoid,"bad"):""))+
    sec("Seasonality",tbl(["Window","What to do"],(s.seasonality||[]).map(function(x){return [x.window,x.action];})))+
    sec("Measurement",tbl(["Conversion","Type","Primary","How"],(ms.conversions||[]).map(function(x){return [x.name,x.type,x.primary?"Yes":"No",x.how];}))+tbl(["Tracking","Detail"],(ms.tracking||[]).map(function(x){return [x.item,x.detail];}))+(ms.reporting?'<p class="cb-fine">Reporting: '+esc(ms.reporting)+'</p>':""))+
    sec("Tests to run",tbl(["Test","Hypothesis","Metric","When"],(s.tests||[]).map(function(x){return [x.test,x.hypothesis,x.metric,x.when];})))+
    sec("Roadmap",(s.roadmap||[]).map(function(r){return '<h5 class="cb-h5">'+esc(r.phase)+'</h5>'+ul(r.actions);}).join(""))+
    sec("Risks",tbl(["Risk","What to do"],(s.risks||[]).map(function(x){return [x.risk,x.mitigation];})))+
    sec("Assumptions",ul(s.assumptions))+sec("Needed from the client before launch",ul(s.clientNeeds));
}

/* campaigns */
function kwLabel(k){return k.match==="exact"?"["+k.text+"]":k.match==="phrase"?'"'+k.text+'"':k.text;}
function hostPath(url,p1,p2){
  var h=String(url||"").replace(/^https?:\/\//,"").split("/")[0];
  return (h||"yourwebsite.com")+(p1?"/"+p1:"")+(p2?"/"+p2:"");
}
function adPreview(ad,url){
  var heads=(ad.headlines||[]).slice(0,3).join(" | "), d=(ad.descriptions||[]).slice(0,2).join(" ");
  return '<div class="cb-adprev"><span class="cb-adtag">Ad</span><span class="cb-adurl">'+esc(hostPath(url,ad.path1,ad.path2))+'</span><div class="cb-adh">'+esc(heads)+'</div><div class="cb-add">'+esc(d)+'</div></div>';
}
function textList(items,limit){
  return '<ol class="cb-tl">'+(items||[]).map(function(t){return '<li><span>'+esc(t)+'</span><i class="'+(t.length>limit?"over":"")+'">'+t.length+'/'+limit+'</i></li>';}).join("")+'</ol>';
}
function assetsHtml(a){
  if(!a) return "";
  var out="";
  if((a.sitelinks||[]).length) out+='<h5 class="cb-h5">Sitelinks</h5>'+tbl(["Text","Line 1","Line 2","Page"],a.sitelinks.map(function(s){return [s.text,s.desc1,s.desc2,H(link(s.url,s.url.replace(/^https?:\/\/[^/]+/,"")||"/")+(s.verified?"":' <span class="cb-flag" title="This page was not among the pages read">not seen on the site</span>'))];}));
  if((a.callouts||[]).length) out+='<h5 class="cb-h5">Callouts</h5>'+chips(a.callouts);
  if((a.snippets||[]).length) out+='<h5 class="cb-h5">Structured snippets</h5>'+a.snippets.map(function(s){return '<p class="cb-fine"><b>'+esc(s.header)+':</b> '+esc(s.values.join(", "))+'</p>';}).join("");
  if((a.call||{}).use) out+='<h5 class="cb-h5">Call asset</h5><p class="cb-fine">'+esc(a.call.note||"Use a call asset.")+'</p>';
  if((a.promotions||[]).length) out+='<h5 class="cb-h5">Promotions</h5>'+tbl(["Promotion","Detail"],a.promotions.map(function(p){return [p.text,p.detail];}));
  return out;
}
function campaignBody(c){
  var b=c.build||{}, st=c.settings||{}, out="";
  out+=para(c.purpose)+(c.notes?'<p class="cb-fine">'+esc(c.notes)+'</p>':"");
  out+='<h5 class="cb-h5">Settings</h5>'+tbl(["Setting","Value","Why"],[["Bid strategy","bidStrategy"],["Locations","locations"],["Devices","devices"],["Genders","genders"],["Household income","income"],["Ages","ages"],["Ad schedule","schedule"],["Language","language"]].map(function(x){
    var s=st[x[1]]||{}; return [x[0],s.value||"",s.why||""];}));
  if(!Object.keys(b).length) return out+'<div class="pm-err">This campaign could not be written: '+esc(c.buildError||"no reason was given")+'</div>';
  (c.kind==="search"?(b.adGroups||[]):[]).forEach(function(g){
    out+='<div class="cb-ag"><h4>'+esc(g.name)+(g.intent?" "+pill(g.intent):"")+'</h4>'+(g.theme?'<p class="cb-fine">'+esc(g.theme)+'</p>':"")+
      (g.landingPage?'<p class="cb-fine">Landing page: '+link(g.landingPage,g.landingPage.replace(/^https?:\/\/[^/]+/,"")||"/")+(g.landingVerified?"":' <span class="cb-flag">not seen on the site</span>')+'</p>':"");
    if((g.keywords||[]).length) out+='<h5 class="cb-h5">Keywords ('+g.keywords.length+')</h5><div class="cb-chipset">'+g.keywords.map(function(k){return '<span class="cb-c kw '+esc(k.match)+'" title="'+esc((k.intent||"")+(k.note?" · "+k.note:""))+'">'+esc(kwLabel(k))+'</span>';}).join("")+'</div>';
    (g.ads||[]).forEach(function(ad,i){
      out+='<h5 class="cb-h5">Responsive search ad '+(i+1)+'</h5>'+adPreview(ad,g.landingPage)+
        '<div class="cb-two"><div><h6>Headlines ('+ad.headlines.length+')</h6>'+textList(ad.headlines,30)+'</div><div><h6>Descriptions ('+ad.descriptions.length+')</h6>'+textList(ad.descriptions,90)+'</div></div>'+
        ((ad.pins||[]).length?'<p class="cb-fine">Pinned: '+esc(ad.pins.map(function(p){return p.text+" to position "+p.position;}).join("; "))+'</p>':"");
    });
    out+='</div>';
  });
  (b.assetGroups||[]).forEach(function(g){
    var s=g.signals||{};
    out+='<div class="cb-ag"><h4>'+esc(g.name)+'</h4>'+(g.theme?'<p class="cb-fine">'+esc(g.theme)+'</p>':"")+(g.finalUrl?'<p class="cb-fine">Final URL: '+link(g.finalUrl)+(g.landingVerified?"":' <span class="cb-flag">not seen on the site</span>')+'</p>':"")+
      '<div class="cb-two"><div><h6>Headlines ('+g.headlines.length+')</h6>'+textList(g.headlines,30)+'</div><div><h6>Long headlines ('+g.longHeadlines.length+')</h6>'+textList(g.longHeadlines,90)+
      '<h6>Descriptions ('+g.descriptions.length+')</h6>'+textList(g.descriptions,90)+'</div></div>'+
      '<p class="cb-fine">Business name: '+esc(g.businessName||"to be set")+' · Call to action: '+esc(g.cta||"automatic")+'</p>'+
      ((g.images||[]).length?'<h5 class="cb-h5">Image briefs</h5>'+tbl(["Ratio","Concept"],g.images.map(function(i){return [i.ratio,i.concept];})):"")+
      ((g.videos||[]).length?'<h5 class="cb-h5">Video briefs</h5>'+ul(g.videos.map(function(v){return v.concept;})):"")+
      '<h5 class="cb-h5">Audience signals</h5>'+chips([].concat((s.customSegments||[]).map(function(x){return "Custom segment: "+x;}),(s.interests||[]).map(function(x){return "Interest: "+x;}),(s.yourData||[]).map(function(x){return "Your data: "+x;}),s.demographics?["Demographics: "+s.demographics]:[]))+
      ((g.searchThemes||[]).length?'<h5 class="cb-h5">Search themes ('+g.searchThemes.length+')</h5>'+chips(g.searchThemes):"")+'</div>';
  });
  if(c.kind!=="search"&&c.kind!=="pmax"){
    (b.adGroups||[]).forEach(function(g){
      out+='<div class="cb-ag"><h4>'+esc(g.name)+'</h4>'+((g.audiences||[]).length?'<h5 class="cb-h5">Audiences</h5>'+chips(g.audiences):"")+((g.placements||[]).length?'<h5 class="cb-h5">Placements and topics</h5>'+chips(g.placements):"")+
        (g.ads||[]).map(function(a,i){return '<div class="cb-oldad"><b>'+esc(a.format||"Ad "+(i+1))+'</b>'+(a.longHeadline?'<p class="cb-fine">'+esc(a.longHeadline)+'</p>':"")+
          '<div class="cb-two"><div><h6>Headlines</h6>'+textList(a.headlines,c.kind==="demandgen"?40:30)+'</div><div><h6>Descriptions</h6>'+textList(a.descriptions,90)+'</div></div>'+
          (a.cta?'<p class="cb-fine">Call to action: '+esc(a.cta)+'</p>':"")+(a.concept?'<p class="cb-p"><b>Creative:</b> '+esc(a.concept)+'</p>':"")+(a.script?'<p class="cb-p"><b>Script:</b> '+esc(a.script)+'</p>':"")+'</div>';}).join("")+'</div>';
    });
  }
  var n=b.negatives||{};
  if((n.campaign||[]).length||(n.lists||[]).length){
    out+='<h5 class="cb-h5">Negative keywords</h5>'+((n.campaign||[]).length?tbl(["Negative","Match","Why"],n.campaign.map(function(x){return [x.text,x.match,x.reason];})):"")+
      (n.lists||[]).map(function(l){return '<p class="cb-fine"><b>List: '+esc(l.name)+'</b> ('+l.keywords.length+') '+esc(l.keywords.slice(0,30).join(", "))+(l.reason?' · '+esc(l.reason):"")+'</p>';}).join("");
    if((n.removed||[]).length) out+='<p class="cb-fine">Left out because it would block the campaign\'s own keywords: '+esc(n.removed.map(function(r){return r.negative+" ["+r.match+"] blocks "+r.blocked;}).join("; "))+'</p>';
  }
  out+=assetsHtml(b.assets);
  if((c.claims||[]).length) out+='<h5 class="cb-h5">Claims to confirm with the client</h5>'+tbl(["Where","Text","Claim"],c.claims.map(function(x){return [x.where,x.text,x.claim];}));
  if((b.shortfalls||[]).length) out+='<div class="pm-note warn">'+esc(b.shortfalls.join(" "))+'</div>';
  return out;
}
function campSummary(c){
  var b=c.build||{}, g=(b.adGroups||[]), n=g.length||(b.assetGroups||[]).length, kw=g.reduce(function(a,x){return a+(x.keywords||[]).length;},0), ads=g.reduce(function(a,x){return a+(x.ads||[]).length;},0);
  return c.kind==="pmax"?n+" asset group"+(n===1?"":"s"):c.kind==="search"?n+" ad groups · "+kw+" keywords · "+ads+" ads":n+" ad groups";
}
function tabCampaigns(){
  var inp=S.rec.inputs||{};
  return (S.rec.plan.campaigns||[]).map(function(c){
    return '<details class="cb-camp" data-key="'+esc(c.key)+'"'+(S.openC[c.key]?" open":"")+'><summary><span class="cb-ct"><b>'+esc(c.name)+'</b><em>'+esc(c.kindLabel)+' · '+esc(campSummary(c))+'</em></span>'+
      '<span class="cb-cn"><b>'+num(c.share,0)+'%</b><em>'+(c.daily?money(c.daily,inp.currency)+" a day":"no budget")+'</em></span></summary><div class="cb-cbody">'+(S.openC[c.key]?campaignBody(c):"")+'</div></details>';
  }).join("")||'<p class="cb-fine">No campaigns were planned.</p>';
}
function tabBook(){
  var camps=(S.rec.plan.campaigns||[]).filter(function(c){return c.book&&c.book.length;});
  if(!camps.length) return '<p class="cb-fine">No campaign book was written.</p>';
  var cur=camps.filter(function(c){return c.key===S.camp;})[0]||camps[0]; S.camp=cur.key;
  var rows=[], last=null;
  cur.book.forEach(function(r){
    if(r.section!==last){rows.push([H('<span class="cb-bsec">'+esc(r.section)+'</span>'),"",""]); last=r.section;}
    rows.push([r.field,H('<span class="'+(r.source==="plan"?"cb-fromplan":"")+'">'+esc(r.value)+'</span>'),r.other]);
  });
  return '<div class="cb-chipset cb-bchips" role="tablist" aria-label="Campaign">'+camps.map(function(c){return '<button type="button" class="pm-chip'+(c.key===cur.key?" on":"")+'" data-camp="'+esc(c.key)+'">'+esc(c.name)+'</button>';}).join("")+'</div>'+
    '<p class="cb-fine">The layout the QA Agent reads: Field, Value, Other things to check. Values in colour come from this campaign\'s plan; the rest are the team\'s standing answers. Add the downloaded file to the QA Agent when the campaign is built.</p>'+
    '<div class="cb-go"><a class="pm-btn" download href="'+API+'/'+S.rec.id+'/download?file=book-xlsx&campaign='+encodeURIComponent(cur.key)+'">Download this book (Excel)</a>'+
    '<a class="pm-btn-ghost" download href="'+API+'/'+S.rec.id+'/download?file=book-csv&campaign='+encodeURIComponent(cur.key)+'">CSV</a></div>'+
    tbl(["Field","Value","Other things to check"],rows,"cb-book");
}
function tabChecks(){
  var plan=S.rec.plan||{}, c=plan.checks||{}, out="";
  out+=sec("Claims to confirm with the client",tbl(["Campaign","Where","Text","Claim not found on the client's pages"],(plan.claims||[]).map(function(x){return [x.campaign,x.where,x.text,x.claim];})),
    "In the ad text, but not on the client's pages or in the brief");
  var d=Object.keys(c.dropped||{}).map(function(k){return c.dropped[k]+" "+k+" over a limit or outside the client's site were left out";});
  out+=sec("What was held back",ul(d.concat((c.removedNegatives||[]).map(function(r){return r.campaign+": the negative "+r.negative+" ["+r.match+"] would block the keyword "+r.blocked+", so it was removed";}))));
  out+=sec("What the writing came up short on",ul(c.shortfalls));
  out+=sec("What was not available",ul(c.missing),"The strategy says where it is working without these");
  if(c.unconfirmedFacts) out+=sec("Facts not found on a page",'<p class="cb-p">'+c.unconfirmedFacts+' fact'+(c.unconfirmedFacts===1?"":"s")+' the strategist listed could not be found word for word on the pages that were read. They are marked in The business tab.</p>');
  var cost=S.rec.cost||{};
  if(cost.stages) out+=sec("What the build cost",'<p class="cb-fine">Writing: $'+num(cost.model,2)+' in '+cost.calls+' calls. Paid research: $'+num(cost.research,2)+'. Total: $'+num(cost.usd,2)+'.</p>');
  return out||'<p class="cb-fine">Nothing to check.</p>';
}

/* The details of a finished build, put back in the form so they can be changed and built again as a new build. */
function reuse(rec){
  var i=(rec&&rec.inputs)||{};
  leave();
  var set=function(sel,v){$(sel).value=v==null?"":String(v);};
  set("#fName",i.name); set("#fSite",(i.website||"").replace(/^https?:\/\//,"").replace(/\/$/,"")); set("#fIndustry",i.industry); set("#fBudget",i.budget); set("#fLaunch",i.launch);
  set("#fPlaces",i.locations); set("#fServices",i.services); set("#fBrief",i.brief); set("#fAvoid",i.avoid);
  set("#fComp",(i.competitors||[]).map(function(c){return c.url||c.name;}).join("\n"));
  S.goal=i.goal||"leads"; S.kinds=(i.campaignTypes||[]).slice();
  S.account=i.account||""; S.accountObj=S.accounts.filter(function(a){return a.id===S.account;})[0]||null;
  picker.set(S.pickerItems,S.account);
  S.opts.cards.forEach(function(c){S.on[c.key]=i.research?!!i.research[c.key]:!!c.default;});
  paintForm(); window.scrollTo&&window.scrollTo(0,0);
}

/* ── Saved builds ─────────────────────────────────────────── */
var STATE_PILL={done:["Ready","ok"],running:["Building","run"],queued:["Waiting","run"],error:["Stopped","failed"]};
function loadRecent(){
  getJson(API+"/list").then(function(r){
    var box=$("#recent");
    if(!r.ok){box.innerHTML='<p class="cb-fine">'+esc((r.j&&r.j.error)||"The saved builds could not be listed.")+'</p>';return;}
    var rows=r.j.builds||[];
    if(!rows.length){box.innerHTML='<p class="cb-fine">Nothing has been built yet.</p>';return;}
    box.innerHTML=rows.map(function(b){
      var p=b.stale?["Interrupted","failed"]:(STATE_PILL[b.status]||[b.status,""]);
      return '<div class="cb-rec"><button type="button" class="cb-open" data-id="'+esc(b.id)+'"><span class="sq-av" style="--h:'+hueOf(b.name)+'">'+esc(inits(b.name))+'</span>'+
        '<span class="cb-rn"><b>'+esc(b.name)+'</b><em>'+esc(b.website.replace(/^https?:\/\//,"").replace(/\/$/,"")||b.accountName||"no website")+' · '+esc((b.createdBy||"").split("@")[0])+' · '+esc(ago(b.createdAt))+'</em></span>'+
        '<span class="cb-rs">'+pill(p[0],p[1])+(b.campaigns?'<em>'+b.campaigns+' campaigns</em>':"")+(b.cost!=null?'<em>$'+num(b.cost,2)+'</em>':"")+'</span></button></div>';
    }).join("");
  }).catch(function(){$("#recent").innerHTML='<p class="cb-fine">The saved builds could not be listed.</p>';});
}

/* ── Events ───────────────────────────────────────────────── */
document.addEventListener("click",function(e){
  var t=e.target;
  var chip=t.closest&&t.closest("#goals [data-g]"); if(chip){S.goal=chip.getAttribute("data-g"); paintForm(); return;}
  var card=t.closest&&t.closest("#cards [data-k]");
  if(card){var key=card.getAttribute("data-k"), c=S.opts.cards.filter(function(x){return x.key===key;})[0]; if(c&&!blocked(c)){S.on[key]=!S.on[key]; paintForm();} return;}
  var kind=t.closest&&t.closest("#kinds [data-kind]");
  if(kind){var k=kind.getAttribute("data-kind"); if(!k) S.kinds=[]; else {var i=S.kinds.indexOf(k); if(i>=0) S.kinds.splice(i,1); else S.kinds.push(k);} paintForm(); return;}
  var tab=t.closest&&t.closest("#tabs [data-t]"); if(tab){S.tab=tab.getAttribute("data-t"); paintTabs(); return;}
  var cp=t.closest&&t.closest("[data-camp]"); if(cp){S.camp=cp.getAttribute("data-camp"); paintTabs(); return;}
  var op=t.closest&&t.closest(".cb-open"); if(op){watch(op.getAttribute("data-id")); return;}
  if(t.id==="goBtn"){start(); return;}
  if(t.id==="runBack"||t.id==="backBtn"){leave(); return;}
  if(t.id==="reuseBtn"){reuse(S.rec); return;}
  if(t.id==="delBtn"){
    if(!window.confirm("Remove this build from the list? The files you downloaded are not affected.")) return;
    sendJson(API+"/"+S.id,"DELETE").then(function(r){ if(!r.ok){setMsg(esc((r.j&&r.j.error)||"It could not be removed."));return;} leave(); });
    return;
  }
  if(t.id==="rebuildBtn"||t.id==="resumeBtn"||t.id==="carryBtn"){
    var resume=t.id!=="rebuildBtn";
    if(!resume&&!window.confirm("Build this again from the start? The research and the writing are redone.")) return;
    sendJson(API+"/"+S.id+"/rebuild","POST",{resume:resume}).then(function(r){
      if(!r.ok){setMsg(esc((r.j&&r.j.error)||"It could not be started."),r.status===429||r.status===409?"pm-note":"");return;}
      setMsg(""); watch(S.id);
    });
    return;
  }
});
document.addEventListener("toggle",function(e){
  var d=e.target; if(!d||!d.classList||!d.classList.contains("cb-camp")) return;
  var key=d.getAttribute("data-key"); S.openC[key]=d.open;
  if(d.open){var c=((S.rec||{}).plan||{}).campaigns||[]; c=c.filter(function(x){return x.key===key;})[0]; var body=d.querySelector(".cb-cbody"); if(c&&body&&!body.innerHTML) body.innerHTML=campaignBody(c);}
},true);
document.addEventListener("input",function(e){
  if(e.target.id==="fSite") paintForm();
  if(e.target.id==="kwFind"){
    S.filter=e.target.value; var pos=e.target.selectionStart; $("#tabBody").innerHTML=tabKeywords(); var f=$("#kwFind"); if(f){f.focus(); try{f.setSelectionRange(pos,pos);}catch(x){}}
  }
});
document.addEventListener("click",function(e){var d=document.querySelector(".cb-more[open]"); if(d&&!d.contains(e.target)) d.removeAttribute("open");});

/* ── Start ────────────────────────────────────────────────── */
function dashDate(days){var d=new Date(); d.setDate(d.getDate()+days); return d.getFullYear()+"-"+("0"+(d.getMonth()+1)).slice(-2)+"-"+("0"+d.getDate()).slice(-2);}
function init(){
  picker=window.PMPicker($("#acctPick"),{label:"Google Ads account (optional)",search:true,onChange:function(v){
    S.account=v; S.accountObj=S.accounts.filter(function(a){return a.id===v;})[0]||null;
    if(S.accountObj&&!$("#fName").value.trim()) $("#fName").value=S.accountObj.name;
    paintForm();
  }});
  picker.set([{value:"",label:"No account",sub:"Website and brief only",avatar:"?",hue:200}],"",true);
  $("#fLaunch").value=dashDate(14); $("#fLaunch").min=dashDate(0);
  Promise.all([getJson(API+"/options"),getJson("/p2/paid-marketing/api/segments/accounts")]).then(function(rs){
    var o=rs[0], a=rs[1];
    if(!o.ok){setMsg(esc((o.j&&o.j.error)||"The page could not load its options."));return;}
    S.opts=o.j; S.opts.cards.forEach(function(c){S.on[c.key]=!!c.default; CARD_TITLE[c.key]=c.title;});
    S.accounts=(a.ok&&a.j.accounts)||[];
    var items=[{value:"",label:"No account",sub:"Website and brief only",avatar:"?",hue:200}];
    S.accounts.forEach(function(x){items.push({value:x.id,label:x.name,avatar:inits(x.name),hue:hueOf(x.name),sub:dashed(x.id)});});
    S.pickerItems=items; picker.set(items,"");
    if(!a.ok) $("#acctHint").textContent="The Google Ads accounts could not be listed, so this build can use the website and the brief only.";
    paintForm(); loadRecent();
    var want=(location.search.match(/[?&]build=([0-9a-f]{32})/)||[])[1]; if(want) watch(want);
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
}
if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",init); else init();

/* A Google-only agent: the same three choices as every agent; Meta says why it has nothing here and where to look, Both keeps Google and says so. */
if (window.PMPlatform && document.querySelector("#platSw")) window.PMPlatform.googleOnly(document.querySelector("#platSw"), {name:"Strategy & Campaign Book",why:"It writes Google Ads campaigns: keywords and match types, ad and extension text held to Google's limits, budgets and bid strategies. A Meta plan (objectives, ad sets, audiences, placements, creative) is not written by this agent yet.",closest:[{label:"Business Research",href:"/p2/paid-marketing/business-research"},{label:"Ad Copy & Creative Generator",href:"/p2/paid-marketing/ad-copy"},{label:"Account Audit",href:"/p2/paid-marketing/account-audit"}],body:"#gBody"});
})();
