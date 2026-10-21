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

var S = {opts:null, accounts:[], account:"", accountObj:null, goal:"leads", on:{}, id:"", rec:null, tab:"overview", seq:0, timer:null,
  kw:{view:"add",q:"",theme:"",intent:"",priority:"",sort:"avg",dir:-1,shown:100}};
var picker=null;
var PAGE=100;

/* ── The form ─────────────────────────────────────────────── */
var ICON = {
  business:'<path d="M4 21V8l8-5 8 5v13"/><path d="M9 21v-6h6v6M9 11h.01M15 11h.01"/>',
  website:'<path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/><circle cx="12" cy="12" r="9"/>',
  account:'<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v4l3 2"/>',
  competitors:'<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>'
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
function minHtml(){
  return (S.opts.minSearches||[]).map(function(n){return '<option value="'+n+'"'+(n===S.opts.defaultMin?" selected":"")+'>'+num(n)+' a month</option>';}).join("");
}
function paintForm(){
  $("#goals").innerHTML=goalsHtml(); $("#cards").innerHTML=cardsHtml();
  if(!S.minPainted){S.minPainted=true; $("#fMin").innerHTML=minHtml();}
  if(!S.opts.modelReady) $("#goNote").innerHTML='<b>The AI is not available on this server</b> (no Anthropic key is set), so the keywords cannot be judged here yet.';
}
function setMsg(html,cls){var m=$("#msg"); m.className=cls||""; m.innerHTML=html?'<div class="'+(cls==="pm-note"?"pm-note":"pm-err")+'" role="alert">'+html+'</div>':"";}

function payload(){
  var research={}; S.opts.cards.forEach(function(c){research[c.key]=!blocked(c)&&!!S.on[c.key];});
  return {name:$("#fName").value, website:$("#fSite").value, account:S.account, industry:$("#fIndustry").value, goal:S.goal,
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
  stopPoll(); S.id=id; S.rec=null; S.tab="overview"; S.kw=resetKw();
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

var CARD_TITLE = {planner:"Keyword Planner"};
function cardTitle(k){return CARD_TITLE[k]||k;}
function plan(){return (S.rec&&S.rec.plan)||{};}
function wr(){return plan().plan||{};}
function res(){return (S.rec&&S.rec.research)||{};}
function cov(){return plan().coverage||{};}
function themes(){return plan().themes||[];}
function themeName(key){var t=themes().filter(function(x){return x.key===key;})[0]; return t?t.name:"Other";}
function cur(){return plan().currency||"USD";}

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
  var p=plan(), r=res(), out=[["overview","Overview"]], rivals=((r.competitors||{}).data||{}).competitors||[], seasonal=(p.seasonal||{}).rows||[];
  if(themes().length) out.push(["themes","Themes ("+themes().length+")"]);
  if((p.keywords||[]).length) out.push(["keywords","Keywords ("+p.keywords.length+")"]);
  if(r.account&&(r.account.state==="ok"||r.account.state==="partial")) out.push(["account","What the account has"]);
  if(rivals.some(function(c){return c.ok;})||(p.keywords||[]).some(function(k){return k.intent==="competitor";})) out.push(["compete","Competitors"]);
  if(seasonal.length) out.push(["season","Through the year"]);
  if((wr().negatives||[]).length) out.push(["negatives","Negatives ("+wr().negatives.length+")"]);
  if((wr().quickWins||[]).length||(wr().tests||[]).length||(wr().risks||[]).length||(wr().seasonal||[]).length) out.push(["plan","Plan"]);
  if((wr().questions||[]).length) out.push(["questions","Questions for the client ("+wr().questions.length+")"]);
  out.push(["checks","Checks"]);
  return out;
}
function paintTabs(){
  var list=tabsList();
  if(!list.some(function(t){return t[0]===S.tab;})) S.tab="overview";
  $("#tabs").innerHTML=list.map(function(t){return '<button type="button" role="tab" class="cb-tab'+(t[0]===S.tab?" on":"")+'" data-t="'+t[0]+'" aria-selected="'+(t[0]===S.tab)+'">'+esc(t[1])+'</button>';}).join("");
  var fn={overview:tabOverview,themes:tabThemes,keywords:tabKeywords,account:tabAccount,compete:tabCompete,season:tabSeason,negatives:tabNegatives,plan:tabPlan,questions:tabQuestions,checks:tabChecks}[S.tab];
  $("#tabBody").innerHTML=fn();
  if(S.tab==="keywords") paintKw();
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

function tabOverview(){
  var c=cov(), p=plan(), w=wr(), r=res(), st=p.stats||{};
  var k='<div class="cb-kpis">'+kpi("Keywords judged",num(c.judged),num(c.yes)+" a fit")+kpi("To add to the account",num(c.include),"fit, new, not a rival's name")+
    kpi("Searches not yet covered",num(c.newSearches),"a month, of the keywords that fit")+kpi("Already covered",c.poolPct==null?"n/a":c.poolPct+"%","of the searches for ideas that share a word with what it sells")+
    kpi("Themes",themes().length)+kpi("Negatives to add",(w.negatives||[]).length)+'</div>';
  var out=k+sec("In short",ul(w.summary));
  out+=sec("First moves",tbl(["What","Why"],(w.quickWins||[]).map(function(x){return [x.what,x.why];})));
  var order=["business","website","account","competitors","planner"], rows=order.filter(function(x){return r[x];}).map(function(x){
    var q=r[x]; return [cardTitle(x),H(pill({ok:"Read",partial:"Partly",failed:"Failed",skipped:"Skipped"}[q.state]||q.state,q.state)),q.note||""];});
  out+=sec("What was read",tbl(["Research","Result","What it found"],rows),"Anything that failed is named in the checks");
  var calls=(((r.planner||{}).data||{}).calls)||[];
  out+=sec("What Keyword Planner was asked",fine(((r.planner||{}).data||{}).geoNote)+tbl(["Asked about","Ideas kept","Note"],calls.map(function(x){return [x.label,x.ideas,x.note||""];})),
    st.considered?num(st.considered)+" different keywords came back; "+num(st.judged)+" were judged":"");
  return out;
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
    "Keyword Planner was asked what each site that was read is found for")+
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
function kwChanged(fn){fn(); S.kw.shown=PAGE; paintKw();}
document.addEventListener("input",function(e){
  if(e.target.id==="fSite") paintForm();
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
  var op=t.closest&&t.closest(".cb-open"); if(op){watch(op.getAttribute("data-id")); return;}
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
  picker=window.PMPicker($("#acctPick"),{label:"Google Ads account (optional)",search:true,onChange:function(v){
    S.account=v; S.accountObj=S.accounts.filter(function(a){return a.id===v;})[0]||null;
    if(S.accountObj&&!$("#fName").value.trim()) $("#fName").value=S.accountObj.name;
    paintForm();
  }});
  picker.set([{value:"",label:"No account",sub:"Website and what it sells only",avatar:"?",hue:200}],"",true);
  Promise.all([getJson(API+"/options"),getJson("/p2/paid-marketing/api/segments/accounts")]).then(function(rs){
    var o=rs[0], a=rs[1];
    if(!o.ok){setMsg(esc((o.j&&o.j.error)||"The page could not load its options."));return;}
    S.opts=o.j; S.opts.cards.forEach(function(c){S.on[c.key]=!!c.default; CARD_TITLE[c.key]=c.title;});
    S.accounts=(a.ok&&a.j.accounts)||[];
    var items=[{value:"",label:"No account",sub:"Website and what it sells only",avatar:"?",hue:200}];
    S.accounts.forEach(function(x){items.push({value:x.id,label:x.name,avatar:inits(x.name),hue:hueOf(x.name),sub:dashed(x.id)});});
    S.pickerItems=items; picker.set(items,"");
    if(!a.ok) $("#acctHint").textContent="The Google Ads accounts could not be listed, so this research can use the website and what the client sells only.";
    paintForm(); loadRecent();
    var want=(location.search.match(/[?&]build=([0-9a-f]{32})/)||[])[1]; if(want) watch(want);
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
}
if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",init); else init();
})();
