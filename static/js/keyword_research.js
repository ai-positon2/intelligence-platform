/* Keyword Research (Paid Marketing Agents).
 *
 * The form on this page collects a client and what to read first; the server does the reading, the arithmetic and the judging in the background
 * (tracker/paid_keywords_job.py) and this page polls it, then draws the finished result in tabs: the overview, the themes, the keywords (filterable), what the
 * account already has, the competitors, the year, the negatives, the plan, the questions for the client and the checks. It never counts, matches or judges anything
 * for itself: every number, every status and every judgement it shows came from the server. The layout is Strategy & Campaign Book's (static/js/campaign_book.js).
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/keywords";
var $ = function(s,r){return (r||document).querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return String(n).replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function num(v,nd){return v==null||v===""||isNaN(v)?"n/a":Number(v).toLocaleString("en-US",{maximumFractionDigits:nd==null?0:nd});}
function money(v){return v==null||v===""||isNaN(v)?"":Number(v).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});}
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

var S = {opts:null, accounts:[], metaAccounts:[], account:"", metaAccount:"", platform:"google", accountObj:null, metaObj:null, goal:"leads", on:{}, id:"", rec:null, tab:"overview", seq:0, timer:null,
  kw:{view:"add",q:"",theme:"",intent:"",priority:"",sort:"avg",dir:-1,shown:100}, mi:{view:"new",q:"",shown:100}};
var scope=null;
var PAGE=100;

/* The Platform switch and the account pickers are the shared control (static/js/pm_scope.js): Google reads the Google Ads account's keywords and asks Keyword Planner, Meta reads the
   Meta ad account's ad sets and looks the client up in Meta's interests, Both does the two in one build. S.account is the Google part, S.metaAccount the Meta part (never an MCC). */
function applyScope(a,p){
  var ps=window.PMScope.partsOf(a).filter(Boolean);
  S.platform=p;
  S.account=ps.filter(function(x){return !window.PMScope.isMeta(x);})[0]||"";
  S.metaAccount=ps.filter(function(x){return window.PMScope.isMeta(x);})[0]||"";
  S.accountObj=S.accounts.filter(function(x){return x.id===S.account;})[0]||null;
  S.metaObj=S.metaAccounts.filter(function(x){return x.id===S.metaAccount;})[0]||null;
  var first=S.accountObj||S.metaObj;
  if(first&&!$("#fName").value.trim()) $("#fName").value=first.name;
  if(S.opts) paintForm();
}
function googleOn(){return S.platform!=="meta";}
function metaOn(){return S.platform!=="google";}

/* ── The form ─────────────────────────────────────────────── */
var ICON = {
  business:'<path d="M4 21V8l8-5 8 5v13"/><path d="M9 21v-6h6v6M9 11h.01M15 11h.01"/>',
  website:'<path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/><circle cx="12" cy="12" r="9"/>',
  account:'<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v4l3 2"/>',
  metaaccount:'<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v4l3 2"/>',
  competitors:'<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>'
};
function goalsHtml(){
  return Object.keys(S.opts.goals).map(function(k){
    return '<button type="button" role="radio" class="pm-chip'+(k===S.goal?" on":"")+'" data-g="'+esc(k)+'" aria-checked="'+(k===S.goal)+'">'+esc(S.opts.goals[k])+'</button>';
  }).join("");
}
function blocked(card){
  if(card.key==="account"&&!googleOn()) return "Google only";
  if(card.key==="metaaccount"&&!metaOn()) return "Meta only";
  if(card.needs==="account"&&!S.account) return "Needs a Google Ads account";
  if(card.needs==="metaAccount"&&!S.metaAccount) return "Needs a Meta ad account";
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
function minHtml(){
  return (S.opts.minSearches||[]).map(function(n){return '<option value="'+n+'"'+(n===S.opts.defaultMin?" selected":"")+'>'+num(n)+' a month</option>';}).join("");
}
function paintForm(){
  $("#goals").innerHTML=goalsHtml(); $("#cards").innerHTML=cardsHtml();
  $("#goBtn").textContent=S.platform==="meta"?"Find audiences":S.platform==="both"?"Find keywords and audiences":"Find keywords";
  $("#fMinWrap").hidden=!googleOn();
  if(!S.minPainted){S.minPainted=true; $("#fMin").innerHTML=minHtml();}
  if(!S.opts.modelReady) $("#goNote").innerHTML='<b>The AI is not available on this server</b> (no Anthropic key is set), so the keywords cannot be judged here yet.';
}
function setMsg(html,cls){var m=$("#msg"); m.className=cls||""; m.innerHTML=html?'<div class="'+(cls==="pm-note"?"pm-note":"pm-err")+'" role="alert">'+html+'</div>':"";}

function payload(){
  var research={}; S.opts.cards.forEach(function(c){research[c.key]=!blocked(c)&&!!S.on[c.key];});
  return {name:$("#fName").value, website:$("#fSite").value, account:S.account, metaAccount:S.metaAccount, platform:S.platform, industry:$("#fIndustry").value, goal:S.goal,
    locations:$("#fPlaces").value, services:$("#fServices").value, brief:$("#fBrief").value, avoid:$("#fAvoid").value,
    minSearches:parseInt($("#fMin").value,10)||S.opts.defaultMin,
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
  stopPoll(); S.id=id; S.rec=null; S.tab="overview"; S.kw=resetKw(); S.mi={view:"new",q:"",shown:PAGE};
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
function resetKw(){return {view:"add",q:"",theme:"",intent:"",priority:"",sort:"avg",dir:-1,shown:PAGE};}
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

/* ── The finished result ──────────────────────────────────── */
function tbl(head,rows,cls){
  if(!rows||!rows.length) return "";
  return '<div class="cb-tw"><table class="cb-t'+(cls?" "+cls:"")+'"><thead><tr>'+head.map(function(h){return "<th>"+(h&&typeof h==="object"&&"h" in h?h.h:esc(h))+"</th>";}).join("")+"</tr></thead><tbody>"+
    rows.map(function(r){return "<tr>"+r.map(function(c){return "<td"+(c&&typeof c==="object"&&c.cls?' class="'+esc(c.cls)+'"':"")+(c&&typeof c==="object"&&c.style?' style="'+esc(c.style)+'"':"")+">"+cell(c)+"</td>";}).join("")+"</tr>";}).join("")+"</tbody></table></div>";
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

var CARD_TITLE = {planner:"Keyword Planner", interests:"Meta's interest list"};
function cardTitle(k){return CARD_TITLE[k]||k;}
function plan(){return (S.rec&&S.rec.plan)||{};}
function wr(){return plan().plan||{};}
function res(){return (S.rec&&S.rec.research)||{};}
function cov(){return plan().coverage||{};}
function themes(){return plan().themes||[];}
function themeName(key){var t=themes().filter(function(x){return x.key===key;})[0]; return t?t.name:"Other";}
function cur(){return plan().currency||"USD";}
function mt(){return plan().meta||null;}
function hasGoogle(){var p=plan(); return !!(p.keywords||[]).length||!!(p.themes||[]).length||!!p.coverage;}

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
       ((p.editor||[]).length?'<a href="'+API+'/'+rec.id+'/download?file=csv" download>Keywords to add, for Google Ads Editor (CSV)</a>':"")+
       '<a href="'+API+'/'+rec.id+'/download?file=zip" download>Every table as CSV (zip)</a></div></details>';
  }
  b+='<button type="button" class="pm-btn-ghost" id="rebuildBtn">Research again from the start</button><button type="button" class="pm-btn-ghost" id="reuseBtn" title="Open the form with this client\'s details filled in">Change the details</button><button type="button" class="pm-btn-ghost" id="backBtn">New research</button><button type="button" class="pm-btn-ghost danger" id="delBtn">Remove</button>';
  $("#rBtns").innerHTML=b;
  var note="";
  if(rec.status==="error") note='<div class="pm-err" role="alert"><b>The research stopped.</b> '+esc(rec.error||"")+' What was already read and judged is kept: press <b>Finish what is missing</b> to carry on from where it stopped.'+
      '<div class="cb-go"><button type="button" class="pm-btn" id="carryBtn">Finish what is missing</button></div></div>';
  else if(rec.note) note='<div class="pm-note warn">'+esc(rec.note)+'</div>';
  $("#rNote").innerHTML=note;
  paintTabs();
}

function tabsList(){
  var p=plan(), r=res(), out=[["overview","Overview"]], rivals=((r.competitors||{}).data||{}).competitors||[], seasonal=(p.seasonal||{}).rows||[], m=mt();
  var both=hasGoogle()&&!!m, G=both?"Google: ":"", M=both?"Meta: ":"";
  if(themes().length) out.push(["themes",G+"Themes ("+themes().length+")"]);
  if((p.keywords||[]).length) out.push(["keywords",G+"Keywords ("+p.keywords.length+")"]);
  if(r.account&&(r.account.state==="ok"||r.account.state==="partial")) out.push(["account",G+"What the account has"]);
  if(m&&(m.audiences||[]).length) out.push(["maud",M+"Audiences to test ("+m.audiences.length+")"]);
  if(m&&(m.interests||[]).length) out.push(["mint",M+"Interests ("+m.interests.length+")"]);
  if(m&&((m.inUse||[]).length||(m.adsets||[]).length)) out.push(["mset",M+"What the ad sets aim at"]);
  if(rivals.some(function(c){return c.ok;})||(p.keywords||[]).some(function(k){return k.intent==="competitor";})) out.push(["compete","Competitors"]);
  if(seasonal.length) out.push(["season","Through the year"]);
  if((wr().negatives||[]).length) out.push(["negatives",G+"Negatives ("+wr().negatives.length+")"]);
  if((wr().quickWins||[]).length||(wr().tests||[]).length||(wr().risks||[]).length||(wr().seasonal||[]).length) out.push(["plan",G+"Plan"]);
  if(m&&(Object.keys(m.plans||{}).some(function(k){return m.plans[k];})||((m.plan||{}).quickWins||[]).length||((m.plan||{}).risks||[]).length)) out.push(["mplan",M+"Plan"]);
  if((wr().questions||[]).length) out.push(["questions",G+"Questions for the client ("+wr().questions.length+")"]);
  if(m&&((m.plan||{}).questions||[]).length) out.push(["mquestions",M+"Questions for the client ("+m.plan.questions.length+")"]);
  out.push(["checks","Checks"]);
  return out;
}
function paintTabs(){
  var list=tabsList();
  if(!list.some(function(t){return t[0]===S.tab;})) S.tab="overview";
  $("#tabs").innerHTML=list.map(function(t){return '<button type="button" role="tab" class="cb-tab'+(t[0]===S.tab?" on":"")+'" data-t="'+t[0]+'" aria-selected="'+(t[0]===S.tab)+'">'+esc(t[1])+'</button>';}).join("");
  var fn={overview:tabOverview,themes:tabThemes,keywords:tabKeywords,account:tabAccount,compete:tabCompete,season:tabSeason,negatives:tabNegatives,plan:tabPlan,questions:tabQuestions,checks:tabChecks,
    maud:tabMAud,mint:tabMInt,mset:tabMSet,mplan:tabMPlan,mquestions:tabMQuestions}[S.tab];
  $("#tabBody").innerHTML=fn();
  if(S.tab==="keywords") paintKw();
  if(S.tab==="mint") paintMi();
}
var PRI={high:"high",medium:"medium",low:"low"};
function priPill(x){return {h:pill(x||"medium",PRI[x]||"medium"),cls:"kr-nw"};}
function fitPill(x){return {cls:"kr-nw",h:(pill({yes:"A fit",maybe:"Maybe",no:"Not a fit",unjudged:"Not judged"}[x]||x,x==="yes"?"ok":x==="maybe"?"medium":x==="no"?"failed":"skipped"))};}
function statusPill(r){
  var by=r.by||{};
  if(r.status==="bidding") return H(pill("Already a keyword","low")+'<span class="kr-small">'+esc((by.match||"").toLowerCase())+" in "+esc(by.campaign||"the account")+'</span>');
  if(r.status==="covered") return H(pill("Already matched","medium")+'<span class="kr-small">by "'+esc(by.kw)+'" ('+esc((by.match||"").toLowerCase())+')</span>');
  return H(pill("New","ok")+((r.neg||{}).scope?'<span class="kr-small">blocked in some campaigns by "'+esc(((r.neg.by||[{}])[0]||{}).kw)+'"'+(r.blockedInTarget?", including the one it would go in":"")+'</span>':""));
}
function bids(r){return r.lo!=null||r.hi!=null?money(r.lo)+" to "+money(r.hi):"";}

function failedParts(){
  var e=plan().errors||{}, k=Object.keys(e);
  return k.map(function(x){return '<div class="pm-note warn"><b>The '+esc(x)+' part could not be written.</b> '+esc(e[x])+' The other part is kept: press <b>Research again from the start</b> to try this one.</div>';}).join("");
}
function tabOverview(){
  var c=cov(), p=plan(), w=wr(), r=res(), st=p.stats||{}, m=mt(), out=failedParts();
  if(hasGoogle()){
    var k='<div class="cb-kpis">'+kpi("Keywords judged",num(c.judged),num(c.yes)+" a fit")+kpi("To add to the account",num(c.include),"fit, new, not a rival's name")+
      kpi("Searches not yet covered",num(c.newSearches),"a month, of the keywords that fit")+kpi("Already covered",c.poolPct==null?"n/a":c.poolPct+"%","of the searches for ideas that share a word with what it sells")+
      kpi("Themes",themes().length)+kpi("Negatives to add",(w.negatives||[]).length)+'</div>';
    out+=(m?h5("Google"):"")+k+sec("In short",ul(w.summary));
    out+=sec("First moves",tbl(["What","Why"],(w.quickWins||[]).map(function(x){return [x.what,x.why];})));
  }
  if(m){
    var mc=m.coverage||{}, tot=m.totals||{};
    out+=(hasGoogle()?h5("Meta"):"")+'<div class="cb-kpis">'+kpi("Interests judged",num(mc.judged),num(mc.yes)+" a fit")+kpi("Fit and aimed at by no ad set",num(mc["new"]),"worth an ad set")+
      kpi("Interests in use",num(mc.inUse),num(m.adsetCount)+" ad sets")+kpi("Spent on interests that do not fit",mmoney(mc.misfitSpend),"in the last 90 days")+
      kpi("Audiences to test",(m.audiences||[]).length)+kpi("Cost per result today",tot.cpa==null?"n/a":mmoney(tot.cpa),"across the ad sets")+'</div>';
    out+=sec("What the numbers say",ul(m.headlines),"Worked out from Meta's own figures, not written by the AI")+sec("In short",ul((m.plan||{}).summary));
    out+=sec("First moves",tbl(["What","Why"],((m.plan||{}).quickWins||[]).map(function(x){return [x.what,x.why];})));
  }
  var order=["business","website","account","metaaccount","competitors","planner","interests"], rows=order.filter(function(x){return r[x];}).map(function(x){
    var q=r[x]; return [cardTitle(x),H(pill({ok:"Read",partial:"Partly",failed:"Failed",skipped:"Skipped"}[q.state]||q.state,q.state)),q.note||""];});
  out+=sec("What was read",tbl(["Research","Result","What it found"],rows),"Anything that failed is named in the checks");
  var calls=(((r.planner||{}).data||{}).calls)||[];
  if(calls.length) out+=sec("What Keyword Planner was asked",fine(((r.planner||{}).data||{}).geoNote)+tbl(["Asked about","Ideas kept","Note"],calls.map(function(x){return [x.label,x.ideas,x.note||""];})),
    st.considered?num(st.considered)+" different keywords came back; "+num(st.judged)+" were judged":"");
  var seeds=((m||{}).seeds||{}).phrases||[];
  if(seeds.length) out+=sec("What was looked up in Meta's interests",chips(seeds),"Meta keeps broad, standard names for interests: these are the phrases the AI chose to look up");
  return out;
}
/* ── Meta ─────────────────────────────────────────────────── */
function mcur(){return (mt()||{}).currency||"USD";}
var SYM={USD:"$",EUR:"€",GBP:"£"};
function mmoney(v){return v==null||v===""||isNaN(v)?"":(SYM[mcur()]||mcur()+" ")+money(v);}
function fitMeta(x){return fitPill(x);}
var VERDICT={works:["Working","ok"],weak:["Weak","failed"],watch:["Watch","medium"],idle:["No spend","skipped"],unknown:["Results not read","skipped"]};
function verdictPill(v){var p=VERDICT[v]||[v,""]; return {cls:"kr-nw",h:pill(p[0],p[1])};}
function useCell(u){
  if(!u) return {cls:"kr-nw",h:'<span class="kr-small">no ad set</span>'};
  return H(esc(num(u.adsets))+" ad set"+(u.adsets===1?"":"s")+'<span class="kr-small">'+esc(mmoney(u.cost))+" spent, "+esc(num(u.conv,1))+" results"+(u.cpa!=null?", "+esc(mmoney(u.cpa))+" each":"")+'</span>');
}
function tabMAud(){
  var m=mt(), aud=m.audiences||[];
  var rows=aud.map(function(a){
    return [{cls:"kr-c4",h:'<b>'+esc(a.name)+'</b><span class="kr-small">'+esc(a.why||"")+'</span>'},{cls:"kr-nw",h:esc(a.role)},
      {cls:"kr-c2",h:(a.interests||[]).map(function(i){return '<span class="cb-c kw'+(i.inUse?" exact":"")+'">'+esc(i.name)+(i.sizeText?" · "+esc(i.sizeText):"")+'</span>';}).join(" ")},
      {cls:"kr-num",h:esc(a.largest?num(a.largest):"n/a")},{cls:"kr-c3",h:esc(a.test||"")}];
  });
  var ex=(m.exclusions||[]).map(function(e){return [H('<span class="kr-kw">'+esc(e.name)+'</span>'),{cls:"kr-num",h:esc(e.sizeText||"")},e.why,e.inUse?"Yes":"No"];});
  return planBox((m.plans||{}).start,"How to start these audiences in Ads Manager")+
    sec("Audiences to test",tbl(["Audience","Role","Interests","Largest interest (people)","How to test it"],rows),"Sizes are Meta's. The interests in an audience overlap, so their sizes do not add up. A highlighted interest is one an ad set already aims at.")+
    planBox((m.plans||{}).exclude,"How to exclude these interests")+
    sec("Interests to exclude from every ad set",tbl(["Interest","Audience size","Why","Aimed at by an ad set now"],ex))+
    sec("Lookalike sources",tbl(["Source","Why"],((m.plan||{}).lookalikes||[]).map(function(x){return [x.seed,x.why];})),"Built from the client's own data")+planBox((m.plans||{}).lookalike,"How to build the lookalikes");
}
function miViewRows(view){
  return ((mt()||{}).interests||[]).filter(function(r){
    if(view==="new") return r.fit==="yes"&&!r.inUse;
    if(view==="fit") return r.fit==="yes";
    if(view==="use") return !!r.inUse;
    return true;
  });
}
function miFiltered(){
  var q=(S.mi.q||"").trim().toLowerCase(), order={yes:0,maybe:1,no:2,unjudged:3};
  return miViewRows(S.mi.view).filter(function(r){return !q||r.name.toLowerCase().indexOf(q)>=0||(r.theme||"").toLowerCase().indexOf(q)>=0;}).sort(function(a,b){
    var fa=order[a.fit]==null?4:order[a.fit], fb=order[b.fit]==null?4:order[b.fit];
    return fa-fb||((b.size||0)-(a.size||0))||(a.name<b.name?-1:1);
  });
}
function tabMInt(){
  var views=[["new","A fit, aimed at by no ad set ("+miViewRows("new").length+")"],["fit","Everything that fits ("+miViewRows("fit").length+")"],["use","In use by an ad set ("+miViewRows("use").length+")"],["all","Everything judged ("+miViewRows("all").length+")"]];
  return '<div class="kr-views" role="radiogroup" aria-label="Which interests">'+views.map(function(v){
      return '<button type="button" role="radio" data-mv="'+v[0]+'" class="'+(S.mi.view===v[0]?"on":"")+'" aria-checked="'+(S.mi.view===v[0])+'">'+esc(v[1])+'</button>';}).join("")+'</div>'+
    '<div class="kr-filters"><label><span class="pd-dd-k">Search</span><input type="text" id="miQ" value="'+esc(S.mi.q)+'" placeholder="Part of an interest or an audience" autocomplete="off"></label></div>'+
    '<p class="cb-fine kr-count" id="miCount"></p><div id="miTable"></div>'+
    fine("Audience sizes are Meta's (a range, for the whole of Meta). Fit, intent and the audience an interest belongs to are Claude's judgement. How an interest in use is doing is worked out from the ad sets that name it: an ad set that names several interests counts under each.");
}
function paintMi(){
  var rows=miFiltered(), shown=rows.slice(0,S.mi.shown), box=$("#miTable"), count=$("#miCount");
  if(!box) return;
  count.textContent=num(rows.length)+" interest"+(rows.length===1?"":"s")+(rows.length>shown.length?", the first "+num(shown.length)+" shown":"");
  if(!rows.length){box.innerHTML='<p class="cb-fine">No interest matches.</p>'; return;}
  var body=shown.map(function(r){
    return [{cls:"kr-c1",h:'<span class="kr-kw">'+esc(r.name)+'</span>'+(r.kind==="behavior"?'<span class="kr-small">a Meta behavior</span>':"")+(r.small?'<span class="kr-small">small: Meta cannot deliver an ad set on an audience this size reliably</span>':"")},
      fitMeta(r.fit),{cls:"kr-nw",h:esc(r.intent||"")},{cls:"kr-c2",h:esc(r.theme||"")},{cls:"kr-num",h:esc(r.sizeText||"n/a")},useCell(r.inUse),{cls:"kr-c3",h:esc(r.note||"")},{cls:"kr-c3",h:esc(r.from||"")}];
  });
  box.innerHTML=tbl(["Interest","Fit","Intent","Audience","Audience size","Aimed at by","Note","Found from"],body)+
    (rows.length>shown.length?'<p class="kr-more"><button type="button" class="pm-btn-ghost" id="miMore">Show '+Math.min(PAGE,rows.length-shown.length)+' more</button></p>':"");
}
function tabMSet(){
  var m=mt(), t=m.totals||{}, cc=mcur();
  var k='<div class="cb-kpis">'+kpi("Ad sets",num(m.adsetCount))+kpi("Interests and behaviors in use",num((m.inUse||[]).length))+kpi("Spent in 90 days",mmoney(t.cost),cc)+kpi("Results",num(t.conv,1))+
    kpi("Cost per result",t.cpa==null?"n/a":mmoney(t.cpa),cc)+kpi("On Advantage+ audience",num(((m.checks||{}).advantageAdsets)||0),"interests are only suggestions there")+'</div>';
  var use=(m.inUse||[]).map(function(u){return [H('<span class="kr-kw">'+esc(u.name)+'</span>'+(u.kind==="behavior"?'<span class="kr-small">a Meta behavior</span>':"")),{cls:"kr-num",h:esc(num(u.adsets))},{cls:"kr-num",h:esc(mmoney(u.cost))},{cls:"kr-num",h:esc(num(u.conv,1))},
    {cls:"kr-num",h:esc(u.cpa==null?"":mmoney(u.cpa))},verdictPill(u.verdict),{cls:"kr-c3",h:esc(u.why)},fitMeta(u.fit)];});
  var sets=(m.adsets||[]).map(function(a){return [{cls:"kr-c1",h:'<b>'+esc(a.name)+'</b><span class="kr-small">'+esc(a.campaign||"")+(a.status?" · "+esc(String(a.status).toLowerCase()):"")+'</span>'},
    {cls:"kr-c3",h:esc((a.interests||[]).map(function(i){return i.name;}).concat((a.behaviors||[]).map(function(i){return i.name;})).join(", "))},{cls:"kr-c3",h:esc((a.audiences||[]).join(", "))},
    {cls:"kr-nw",h:a.advantage?pill("Advantage+","medium"):""},{cls:"kr-num",h:esc(mmoney(a.cost))},{cls:"kr-num",h:esc(num(a.conv,1))},{cls:"kr-num",h:esc(a.cpa==null?"":mmoney(a.cpa))}];});
  return k+planBox((m.plans||{}).remove,"How to take out the interests that cost money and do not fit")+planBox((m.plans||{}).scale,"How to give more to what works")+
    sec("Interests and behaviors the ad sets aim at ("+cc+")",tbl(["Interest","Ad sets","Spent","Results","Cost per result","How it is doing","Why","Judged"],use),
      "Judged against the account's own cost per result: working is at least 20 percent under it with at least 3 results; weak is spending twice the usual cost of a result with nothing to show, or costing 1.5 times as much")+
    sec("Ad sets ("+cc+")",tbl(["Ad set","Interests and behaviors","Audiences","Setting","Spent","Results","Cost per result"],sets),m.adsetCount>(m.adsets||[]).length?"The first "+(m.adsets||[]).length+" of "+m.adsetCount:"Last 90 days");
}
function tabMPlan(){
  var m=mt(), w=m.plan||{}, pl=m.plans||{};
  return ["start","remove","scale","exclude","lookalike"].map(function(k){return planBox(pl[k],(pl[k]||{}).title||"");}).join("")+
    sec("First moves",tbl(["What","Why"],(w.quickWins||[]).map(function(x){return [x.what,x.why];})))+sec("Risks",tbl(["Risk","What to do"],(w.risks||[]).map(function(x){return [x.risk,x.mitigation];})));
}
function mQuestionsByArea(){
  var order=[], by={};
  (((mt()||{}).plan||{}).questions||[]).forEach(function(q){ if(!by[q.area]){by[q.area]=[];order.push(q.area);} by[q.area].push(q); });
  return order.map(function(a){return {area:a,items:by[a]};});
}
function mQuestionsText(){
  return mQuestionsByArea().map(function(g){return g.area+"\n"+g.items.map(function(q,i){return (i+1)+". "+q.question;}).join("\n");}).join("\n\n");
}
function tabMQuestions(){
  return '<div class="cb-go"><button type="button" class="pm-btn" id="copyMQ">Copy the questions</button></div>'+fine("Plain text, grouped by area, ready to paste into an email.")+
    mQuestionsByArea().map(function(g){
      return '<div class="kr-qgroup"><h4>'+esc(g.area)+'</h4>'+tbl(["Question","Why it matters","When"],g.items.map(function(q){return [q.question,q.why,H(pill(q.urgency,q.urgency==="Before launch"?"failed":q.urgency==="First month"?"medium":"low"))];}))+'</div>';
    }).join("");
}

function tabThemes(){
  var rows=themes().map(function(t){
    return [{cls:"kr-c4",h:'<b>'+esc(t.name)+'</b><span class="kr-small">'+esc(t.why||"")+'</span>'+chips((t.top||[]).map(function(x){return x.kw+" · "+num(x.avg);}),"kw")},{h:esc(t.intent),cls:"kr-nw"},priPill(t.priority),
      {cls:"kr-nw",h:esc(t.campaign)+(t.existing?'<span class="kr-small">already in the account</span>':"")},
      {h:esc(num(t.yes))+' fit<span class="kr-small">'+esc(num(t["new"]))+' new, '+esc(num(t.maybe))+' maybe, '+esc(num(t.no))+' not</span>',cls:"kr-num"},
      {h:esc(num(t.searches)),cls:"kr-num"},{h:esc(num(t.newSearches)),cls:"kr-num"},{h:t.coverage==null?"":esc(t.coverage+"%"),cls:"kr-num"},
      bids({lo:t.avgLow,hi:t.avgHigh}),t.pattern||""];
  });
  return sec("Themes",tbl(["Theme","Intent","Priority","Campaign","Keywords","Searches a month (fit)","Not yet covered","Covered (of those judged)","Average bids ("+cur()+")","Through the year"],rows),
    "Searches are Keyword Planner's, added up for the keywords that fit; keywords that overlap are counted each time");
}

/* the keywords table: the filters and the sort are the page's, every row and figure is the server's */
function sortVal(r,key){
  if(key==="kw") return r.kw;
  if(key==="priority") return {high:3,medium:2,low:1}[r.priority]||0;
  var v=r[key]; return v==null?-1:v;
}
function viewRows(view){
  return (plan().keywords||[]).filter(function(r){
    if(view==="add") return !!r.include;
    if(view==="have") return r.fit==="yes"&&r.status!=="new";
    return true;
  });
}
function kwFiltered(){
  var k=S.kw, q=(k.q||"").trim().toLowerCase();
  var rows=viewRows(k.view).filter(function(r){
    if(k.theme&&r.theme!==k.theme) return false;
    if(k.intent&&r.intent!==k.intent) return false;
    if(k.priority&&r.priority!==k.priority) return false;
    if(q&&r.kw.indexOf(q)<0) return false;
    return true;
  });
  rows.sort(function(a,b){var x=sortVal(a,k.sort), y=sortVal(b,k.sort); return x<y?-k.dir:x>y?k.dir:(a.kw<b.kw?-1:1);});
  return rows;
}
function opts(items,cur_,all){return '<option value="">'+esc(all)+'</option>'+items.map(function(x){return '<option value="'+esc(x[0])+'"'+(x[0]===cur_?" selected":"")+'>'+esc(x[1])+'</option>';}).join("");}
function tabKeywords(){
  var k=S.kw, intents=[], seen={};
  (plan().keywords||[]).forEach(function(r){if(!seen[r.intent]){seen[r.intent]=1;intents.push([r.intent,r.intent.charAt(0).toUpperCase()+r.intent.slice(1)]);}});
  var views=[["add","To add ("+viewRows("add").length+")"],["have","A fit, already covered ("+viewRows("have").length+")"],["all","Everything judged ("+viewRows("all").length+")"]];
  return planBox((plan().plans||{}).add,"How to add the keywords to the account")+'<div class="kr-views" role="radiogroup" aria-label="Which keywords">'+views.map(function(v){
      return '<button type="button" role="radio" data-kv="'+v[0]+'" class="'+(k.view===v[0]?"on":"")+'" aria-checked="'+(k.view===v[0])+'">'+esc(v[1])+'</button>';}).join("")+'</div>'+
    '<div class="kr-filters"><label><span class="pd-dd-k">Search</span><input type="text" id="kwQ" value="'+esc(k.q)+'" placeholder="Part of a keyword" autocomplete="off"></label>'+
    '<label><span class="pd-dd-k">Theme</span><select id="kwTheme">'+opts(themes().map(function(t){return [t.key,t.name];}),k.theme,"All themes")+'</select></label>'+
    '<label><span class="pd-dd-k">Intent</span><select id="kwIntent">'+opts(intents,k.intent,"All intents")+'</select></label>'+
    '<label><span class="pd-dd-k">Priority</span><select id="kwPri">'+opts([["high","High"],["medium","Medium"],["low","Low"]],k.priority,"Any priority")+'</select></label></div>'+
    '<p class="cb-fine kr-count" id="kwCount"></p><div id="kwTable"></div>'+
    fine("Searches a month, competition and bids are Google Keyword Planner's estimates for the last twelve months, in "+cur()+" for the bids. Fit, intent, theme, match type and priority are Claude's judgement; whether a keyword is new to the account is worked out from the account's own keywords.");
}
function sortHead(label,key){
  var k=S.kw, mark=k.sort===key?(k.dir<0?" ↓":" ↑"):"";
  return H('<button type="button" class="kr-sort" data-sort="'+key+'" aria-label="Sort by '+esc(label)+'">'+esc(label)+mark+'</button>');
}
function paintKw(){
  var rows=kwFiltered(), shown=rows.slice(0,S.kw.shown), box=$("#kwTable"), count=$("#kwCount");
  if(!box) return;
  count.textContent=num(rows.length)+" keyword"+(rows.length===1?"":"s")+(rows.length>shown.length?", the first "+num(shown.length)+" shown":"");
  if(!rows.length){box.innerHTML='<p class="cb-fine">No keyword matches.</p>'; return;}
  var body=shown.map(function(r){
    return [{cls:"kr-c1",h:('<span class="kr-kw">'+esc(r.kw)+'</span>'+(r.vars>1?'<span class="kr-small">and '+(r.vars-1)+' close variant'+(r.vars===2?"":"s")+'</span>':"")+
        (r.rival?'<span class="kr-small">names a competitor: '+esc(r.rival)+'</span>':"")+(r.conv?'<span class="kr-small">a search term that converted '+esc(num(r.conv.conv,1))+' times</span>':""))},
      fitPill(r.fit),priPill(r.priority),{h:esc(themeName(r.theme)),cls:"kr-c2"},{h:esc(r.intent),cls:"kr-nw"},{h:'<span class="cb-c kw '+esc(r.match)+'">'+esc(r.match)+'</span>',cls:"kr-nw"},
      {h:esc(num(r.avg)),cls:"kr-num"},r.comp||"",{h:esc(bids(r)),cls:"kr-num"},statusPill(r),{h:esc(r.pattern||""),cls:"kr-c3"},{h:esc(r.note||""),cls:"kr-c3"}];
  });
  box.innerHTML=tbl([sortHead("Keyword","kw"),"Fit",sortHead("Priority","priority"),"Theme","Intent","Match",sortHead("Searches a month","avg"),"Competition",sortHead("Bid range ("+cur()+")","lo"),"In the account","Through the year","Note"],body)+
    (rows.length>shown.length?'<p class="kr-more"><button type="button" class="pm-btn-ghost" id="kwMore">Show '+Math.min(PAGE,rows.length-shown.length)+' more</button></p>':"");
}

function tabAccount(){
  var d=((res().account||{}).data)||{}, t=d.terms||{}, rows=(plan().keywords||[]).filter(function(r){return r.conv;});
  var k='<div class="cb-kpis">'+kpi("Keywords",num(d.keywordCount))+kpi("Campaigns",(d.campaigns||[]).length)+kpi("Negative keywords",num(d.negativeCount),"in campaigns")+
    kpi("Shared negative lists",num(d.listCount))+kpi("Search terms",num(t.total),"in the last 90 days")+'</div>';
  return k+sec("Warnings",ul(d.warnings),"What could not be compared")+
    sec("Campaigns and their keywords",tbl(["Campaign","Keywords","Ad groups"],(d.campaigns||[]).map(function(c){return [c.name,{h:esc(num(c.keywords)),cls:"kr-num"},{h:esc(num(c.adGroups)),cls:"kr-num"}];})))+
    sec("Search terms that converted and are not keywords yet",tbl(["Search term","Conversions","Clicks","Cost ("+cur()+")","Campaign","Searches a month","Judged"],rows.map(function(r){
      return [H('<span class="kr-kw">'+esc(r.kw)+'</span>'),{h:esc(num(r.conv.conv,1)),cls:"kr-num"},{h:esc(num(r.conv.clicks)),cls:"kr-num"},{h:esc(money(r.conv.cost)),cls:"kr-num"},r.conv.campaign||"",{h:esc(num(r.avg)),cls:"kr-num"},fitPill(r.fit)];})),
      "Add these as exact keywords if the client agrees they are real leads")+
    sec("Search terms that cost money and did not convert",tbl(["Search term","Clicks","Cost ("+cur()+")","Campaign"],(t.wasteful||[]).map(function(x){return [H('<span class="kr-kw">'+esc(x.term)+'</span>'),{h:esc(num(x.clicks)),cls:"kr-num"},{h:esc(money(x.cost)),cls:"kr-num"},x.campaign||""];})),
      "The Search Query Analysis agent judges these in full");
}
function tabCompete(){
  var read=((res().competitors||{}).data||{}).competitors||[], rows=(plan().keywords||[]).filter(function(r){return r.intent==="competitor";});
  return sec("Websites read",tbl(["Competitor","Found","Pages read","Result"],read.map(function(r){return [H(link(r.url,r.name)),r.via||"",(r.pages||[]).length,r.ok?"Read":(r.reason||"Could not be read")];})),
    hasGoogle()?"Keyword Planner was asked what each site that was read is found for":"The sites were read for how the client stands against them")+
    sec("Keywords that name a competitor",tbl(["Keyword","Competitor named","Searches a month","Competition"],rows.map(function(r){return [H('<span class="kr-kw">'+esc(r.kw)+'</span>'),r.rival||"",{h:esc(num(r.avg)),cls:"kr-num"},r.comp||""];})),
      "Whether to bid on a rival's name is the client's decision: these are not in the Google Ads Editor file")||'<p class="cb-fine">No competitor keywords were found.</p>';
}
function tabSeason(){
  var s=plan().seasonal||{}, head=s.head||[], rows=(s.rows||[]).map(function(r){
    var vals=r.slice(1,1+head.length), max=Math.max.apply(null,vals.concat([1]));
    return [r[0]].concat(vals.map(function(v){return {h:esc(num(v)),cls:"kr-m",style:"background:rgba(56,189,248,"+(0.06+0.5*(v/max)).toFixed(2)+")"};})).concat([r[1+head.length]||""]);
  });
  return sec("Searches a month, by theme",tbl(["Theme"].concat(head).concat(["How it moves"]),rows,"kr-heat"),"One year of Keyword Planner's monthly figures: a lead for planning budgets, not a rule")||
    '<p class="cb-fine">There is no year of figures to show.</p>';
}
/* How to do it and what it is worth (tracker/paid_keywords_plans.py works the steps and the figure out): folded away so the tables stay in front. */
function planBox(p,title){
  if(!p||!p.steps||!p.steps.length) return "";
  return '<details class="kr-plan"><summary>'+esc(title)+'</summary><ol>'+p.steps.map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ol>'+(p.worth?'<p class="kr-worth"><b>Worth</b> '+esc(p.worth)+'</p>':"")+'</details>';
}
function unblockHtml(list){
  return (list||[]).map(function(u){
    return planBox({steps:u.steps,worth:u.worth},'"'+u.negative+'" ('+String(u.match||"").toLowerCase()+') stops '+num(u.count)+' keyword'+(u.count===1?"":"s")+', about '+num(u.searches)+' searches a month');
  }).join("");
}
function negText(){
  return (wr().negatives||[]).map(function(n){return n.match==="exact"?"["+n.kw+"]":n.match==="phrase"?'"'+n.kw+'"':n.kw;}).join("\n");
}
function tabNegatives(){
  var n=wr().negatives||[], rem=(plan().checks||{}).removedNegatives||[];
  return '<div class="cb-go"><button type="button" class="pm-btn" id="copyNeg">Copy as Google Ads negatives</button></div>'+planBox((plan().plans||{}).negatives,"How to add these negatives")+
    fine("One per line, with the match type as Google Ads writes it: [exact], \"phrase\", or plain for broad. Add them at account level or in a shared list.")+
    sec("Negative keywords to add",tbl(["Negative keyword","Match","Why","Note"],n.map(function(x){return [H('<span class="kr-kw">'+esc(x.kw)+'</span>'),x.match,x.why,x.note||""];})))+
    sec("Suggested and left out",tbl(["Negative keyword","Match","Why it was left out"],rem.map(function(x){return [H('<span class="kr-kw">'+esc(x.kw)+'</span>'),x.match,x.reason];})),
      "A negative is left out when it would block a keyword the account bids on or this research recommends, or the account already blocks it");
}
function tabPlan(){
  var w=wr();
  return sec("First moves",tbl(["What","Why"],(w.quickWins||[]).map(function(x){return [x.what,x.why];})))+
    sec("Tests to run early",tbl(["Hypothesis","How","Metric"],(w.tests||[]).map(function(x){return [x.hypothesis,x.how,x.metric];})),"Each one is a guess to test, not a promise")+
    sec("Seasons",tbl(["When","What to do"],(w.seasonal||[]).map(function(x){return [x.window,x.action];})))+
    sec("Risks",tbl(["Risk","What to do"],(w.risks||[]).map(function(x){return [x.risk,x.mitigation];})));
}
function questionsByArea(){
  var order=[], by={};
  (wr().questions||[]).forEach(function(q){ if(!by[q.area]){by[q.area]=[];order.push(q.area);} by[q.area].push(q); });
  return order.map(function(a){return {area:a,items:by[a]};});
}
function questionsText(){
  return questionsByArea().map(function(g){return g.area+"\n"+g.items.map(function(q,i){return (i+1)+". "+q.question;}).join("\n");}).join("\n\n");
}
function tabQuestions(){
  var out='<div class="cb-go"><button type="button" class="pm-btn" id="copyQ">Copy the questions</button></div>'+
    fine("Plain text, grouped by area, ready to paste into an email. Each question says why it matters and when it is needed.");
  return out+questionsByArea().map(function(g){
    return '<div class="kr-qgroup"><h4>'+esc(g.area)+'</h4>'+tbl(["Question","Why it matters","When"],g.items.map(function(q){return [q.question,q.why,H(pill(q.urgency,q.urgency==="Before launch"?"failed":q.urgency==="First month"?"medium":"low"))];}))+'</div>';
  }).join("");
}
function tabChecks(){
  var p=plan(), c=p.checks||{}, st=p.stats||{}, out="";
  if(c.geography) out+=sec("Where searches were counted",'<p class="cb-p">'+esc(c.geography)+'</p>');
  if(c.editorWithheld) out+=sec("The Google Ads Editor file was not made",'<p class="cb-p">'+esc(c.editorWithheld)+'</p>');
  out+=sec("What was not available",ul((c.missing||[]).concat(c.accountWarnings||[])),"The result says where it is working without these");
  out+=sec("Not available from Google Ads",ul(c.caveats),"So a keyword the account blocks only that way can still be shown as new");
  if((c.numbers||[]).length) out+=sec("Numbers in the write-up that are not in the research",tbl(["Where","Text","Number"],c.numbers.map(function(x){return [x.where,x.text,x.number];})),"The AI was not asked for any number: check these before sharing the write-up");
  if((c.emptySeeds||[]).length) out+=sec("Keyword Planner had no ideas for",ul(c.emptySeeds),"Google returns nothing for some phrases: try the other number (insulin pumps for insulin pump) or a nearby phrase");
  if(c.unjudged) out+=sec("Keywords not judged",'<p class="cb-p">'+c.unjudged+' keyword'+(c.unjudged===1?"":"s")+' could not be judged and '+(c.unjudged===1?"is":"are")+' shown as not judged.</p>');
  if((c.convertingNotFit||[]).length) out+=sec("Converting search terms judged not a fit",ul(c.convertingNotFit.map(function(x){return H('<span class="kr-kw">'+esc(x)+'</span>');})),"They converted in the account: check with the client whether they were real leads");
  if(c.blockedInSomeCampaigns) out+=sec("Blocked in some campaigns",'<p class="cb-p">'+c.blockedInSomeCampaigns+' keyword'+(c.blockedInSomeCampaigns===1?" is":"s are")+' blocked by a negative keyword in only some of the account\'s campaigns. They are marked in the keywords table.</p>'+unblockHtml((plan().plans||{}).unblock),"Each negative in the way, with what to do about it");
  var left=[["Ideas below the smallest search volume",st.belowFloor],["Ideas on the avoid list",st.avoided],["Ideas blocked in every campaign by the account's negatives",st.blockedEverywhere],["Ideas merged with a close variant",st.merged]].filter(function(x){return x[1];});
  if(left.length) out+=sec("What was left out before judging",tbl(["Left out","How many"],left.map(function(x){return [x[0],{h:esc(num(x[1])),cls:"kr-num"}];})));
  out+=sec("Assumptions",ul(c.assumptions));
  var m=mt();
  if(m){
    var mc=m.checks||{};
    out+=sec("Meta: what was not available",ul((mc.missing||[]).concat(mc.accountNotes||[]).concat(mc.interestNotes||[])),"The result says where it is working without these");
    if((mc.emptyPhrases||[]).length) out+=sec("Meta had no interest for",ul(mc.emptyPhrases),"Meta keeps broad, standard names, and none at all for some topics (health conditions among them)");
    if(mc.cut) out+=sec("More interests than were judged",'<p class="cb-p">'+num(mc.found)+' interests and behaviors were found; the ones an ad set already aims at, then the first of the rest, were judged.</p>');
    if(mc.unjudged) out+=sec("Meta: interests not judged",'<p class="cb-p">'+mc.unjudged+' interest'+(mc.unjudged===1?"":"s")+' could not be judged and '+(mc.unjudged===1?"is":"are")+' shown as not judged.</p>');
    if(mc.droppedAnswers) out+=sec("Answers left out",'<p class="cb-p">'+mc.droppedAnswers+' answer'+(mc.droppedAnswers===1?" was":"s were")+' left out because '+(mc.droppedAnswers===1?"it":"they")+' named an interest that was not in the list Meta gave, or one that does not fit.</p>');
    if((mc.numbers||[]).length) out+=sec("Meta: numbers in the write-up that are not in the research",tbl(["Where","Text","Number"],mc.numbers.map(function(x){return [x.where,x.text,x.number];})),"The AI was not asked for any number: check these before sharing the write-up");
    out+=sec("Meta: assumptions",ul(mc.assumptions));
  }
  var cost=S.rec.cost||{};
  if(cost.stages) out+=sec("What it cost",'<p class="cb-fine">Judging: $'+num(cost.model,2)+' in '+cost.calls+' calls. Paid research: $'+num(cost.research,2)+'. Total: $'+num(cost.usd,2)+'.</p>');
  return out||'<p class="cb-fine">Nothing to check.</p>';
}

/* The details of a finished build, put back in the form so they can be changed and run again as a new one. */
function reuse(rec){
  var i=(rec&&rec.inputs)||{};
  leave();
  var set=function(sel,v){$(sel).value=v==null?"":String(v);};
  set("#fName",i.name); set("#fSite",shortUrl(i.website)); set("#fIndustry",i.industry); set("#fPlaces",i.locations); set("#fServices",i.services); set("#fBrief",i.brief); set("#fAvoid",i.avoid);
  set("#fMin",i.minSearches||S.opts.defaultMin);
  set("#fComp",(i.competitors||[]).map(function(c){return c.url||c.name;}).join("\n"));
  S.goal=i.goal||"leads";
  var want=i.account&&i.metaAccount?i.account+"+"+i.metaAccount:(i.metaAccount||i.account||"");
  if(scope&&want) scope.select(want); else applyScope("",i.platform||S.platform);
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
function kwChanged(fn){fn(); S.kw.shown=PAGE; paintKw();}
function miChanged(fn){fn(); S.mi.shown=PAGE; paintMi();}
document.addEventListener("input",function(e){
  if(e.target.id==="fSite") paintForm();
  if(e.target.id==="miQ") miChanged(function(){S.mi.q=$("#miQ").value;});
  if(e.target.id==="kwQ") kwChanged(function(){S.kw.q=$("#kwQ").value;});
});
document.addEventListener("change",function(e){
  var id=e.target.id;
  if(id==="kwTheme") kwChanged(function(){S.kw.theme=$("#kwTheme").value;});
  if(id==="kwIntent") kwChanged(function(){S.kw.intent=$("#kwIntent").value;});
  if(id==="kwPri") kwChanged(function(){S.kw.priority=$("#kwPri").value;});
});
document.addEventListener("click",function(e){
  var t=e.target;
  var chip=t.closest&&t.closest("#goals [data-g]"); if(chip){S.goal=chip.getAttribute("data-g"); paintForm(); return;}
  var card=t.closest&&t.closest("#cards [data-k]");
  if(card){var key=card.getAttribute("data-k"), c=S.opts.cards.filter(function(x){return x.key===key;})[0]; if(c&&!blocked(c)){S.on[key]=!S.on[key]; paintForm();} return;}
  var tab=t.closest&&t.closest("#tabs [data-t]"); if(tab){S.tab=tab.getAttribute("data-t"); paintTabs(); return;}
  var view=t.closest&&t.closest(".kr-views [data-kv]"); if(view){S.kw.view=view.getAttribute("data-kv"); S.kw.shown=PAGE; paintTabs(); return;}
  var srt=t.closest&&t.closest("#kwTable [data-sort]");
  if(srt){var key2=srt.getAttribute("data-sort"); if(S.kw.sort===key2) S.kw.dir=-S.kw.dir; else {S.kw.sort=key2; S.kw.dir=key2==="kw"?1:-1;} paintKw(); return;}
  var mview=t.closest&&t.closest(".kr-views [data-mv]"); if(mview){S.mi.view=mview.getAttribute("data-mv"); S.mi.shown=PAGE; paintTabs(); return;}
  var op=t.closest&&t.closest(".cb-open"); if(op){watch(op.getAttribute("data-id")); return;}
  if(t.id==="miMore"){S.mi.shown+=PAGE; paintMi(); return;}
  if(t.id==="copyMQ"){copyText(mQuestionsText(),function(ok){t.textContent=ok?"Copied":"Could not copy: select the text and copy it";});return;}
  if(t.id==="kwMore"){S.kw.shown+=PAGE; paintKw(); return;}
  if(t.id==="goBtn"){start(); return;}
  if(t.id==="runBack"||t.id==="backBtn"){leave(); return;}
  if(t.id==="reuseBtn"){reuse(S.rec); return;}
  if(t.id==="copyQ"){copyText(questionsText(),function(ok){t.textContent=ok?"Copied":"Could not copy: select the text and copy it";});return;}
  if(t.id==="copyNeg"){copyText(negText(),function(ok){t.textContent=ok?"Copied":"Could not copy: select the text and copy it";});return;}
  if(t.id==="delBtn"){
    if(!window.confirm("Remove this research from the list? The files you downloaded are not affected.")) return;
    sendJson(API+"/"+S.id,"DELETE").then(function(r){ if(!r.ok){setMsg(esc((r.j&&r.j.error)||"It could not be removed."));return;} leave(); });
    return;
  }
  if(t.id==="rebuildBtn"||t.id==="carryBtn"){
    var resume=t.id!=="rebuildBtn";
    if(!resume&&!window.confirm("Research this again from the start? The reading and the judging are redone.")) return;
    sendJson(API+"/"+S.id+"/rebuild","POST",{resume:resume}).then(function(r){
      if(!r.ok){setMsg(esc((r.j&&r.j.error)||"It could not be started."),r.status===429||r.status===409?"pm-note":"");return;}
      setMsg(""); watch(S.id);
    });
    return;
  }
});
document.addEventListener("click",function(e){var d=document.querySelector(".cb-more[open]"); if(d&&!d.contains(e.target)) d.removeAttribute("open");});

/* ── Start ────────────────────────────────────────────────── */
function init(){
  getJson(API+"/options").then(function(o){
    if(!o.ok){setMsg(esc((o.j&&o.j.error)||"The page could not load its options."));return;}
    S.opts=o.j; S.opts.cards.forEach(function(c){S.on[c.key]=!!c.default; CARD_TITLE[c.key]=c.title;});
    scope=window.PMScope.mount($("#acctPick"),{account:"",noGroups:true,optional:true,
      onLoaded:function(info){S.accounts=info.accounts||[]; S.metaAccounts=info.meta||[];},
      onChange:function(a,pf){applyScope(a,pf);},
      onError:function(e){$("#acctHint").textContent="The accounts could not be listed ("+((e&&e.message)||"unknown")+"), so this research can use the website and what the client sells only.";}});
    paintForm(); loadRecent();
    var want=(location.search.match(/[?&]build=([0-9a-f]{32})/)||[])[1]; if(want) watch(want);
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
}
if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",init); else init();

})();
