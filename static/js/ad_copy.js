/* Ad Copy and Creative Briefs (Paid Marketing Agents).
 *
 * The form on this page collects what the ads are for, the formats, the angles and what the copy may say; the server reads, writes and checks in the background
 * (tracker/paid_adcopy_job.py) and this page polls it, then draws the finished result in tabs: the overview, one tab for each ad format with every line, its length against
 * the limit, its angle and whether it is ready or needs the client to confirm a claim, the creative briefs, what to confirm and the checks. It never counts, limits or judges
 * anything for itself: every length, every status and every claim it shows came from the server. The layout is Strategy & Campaign Book's (static/js/campaign_book.js).
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/adcopy";
var $ = function(s,r){return (r||document).querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return String(n).replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function num(v,nd){return v==null||v===""||isNaN(v)?"n/a":Number(v).toLocaleString("en-US",{maximumFractionDigits:nd==null?0:nd});}
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

var S = {opts:null, accounts:[], account:"", accountObj:null, goal:"leads", formats:[], angles:[], on:{}, id:"", rec:null, tab:"overview", seq:0, timer:null, plat:"both", platSw:null};
var picker=null;

/* ── The form ─────────────────────────────────────────────── */
var ICON = {
  website:'<path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/><circle cx="12" cy="12" r="9"/>',
  account:'<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v4l3 2"/>',
  google:'<circle cx="11" cy="11" r="6"/><path d="M20 20l-4-4"/>',
  meta:'<path d="M4 15c0-5 3-9 5.5-9S13 11 14.5 13 18 18 20 15"/>'
};
function goalsHtml(){
  return Object.keys(S.opts.goals).map(function(k){
    return '<button type="button" role="radio" class="pm-chip'+(k===S.goal?" on":"")+'" data-g="'+esc(k)+'" aria-checked="'+(k===S.goal)+'">'+esc(S.opts.goals[k])+'</button>';
  }).join("");
}
function anglesHtml(){
  return Object.keys(S.opts.angles).map(function(k){
    var on=S.angles.indexOf(k)>=0;
    return '<button type="button" class="pm-chip'+(on?" on":"")+'" data-a="'+esc(k)+'" aria-pressed="'+on+'">'+esc(k.charAt(0).toUpperCase()+k.slice(1))+'<small>'+esc(S.opts.angles[k])+'</small></button>';
  }).join("");
}
/* The Platform switch narrows the ad formats to Google's, Meta's, or all of them. Changing it keeps the formats still on offer; if none is left it picks that
   platform's defaults, and Both makes sure at least one format of each platform is on. */
function formatPlatform(key){var f=S.opts.formats.filter(function(x){return x.key===key;})[0]; return f?f.platform:"";}
function platFormats(){return S.opts.formats.filter(function(f){return S.plat==="both"||f.platform===S.plat;});}
function setPlatform(p){
  S.plat=p;
  var keep=function(k){return p==="both"||formatPlatform(k)===p;};
  S.formats=S.formats.filter(keep);
  if(!S.formats.length) S.formats=(S.opts.defaultFormats||[]).filter(keep);
  if(!S.formats.length){var f=platFormats()[0]; if(f) S.formats=[f.key];}
  if(p==="both") ["google","meta"].forEach(function(pl){
    if(S.formats.some(function(k){return formatPlatform(k)===pl;})) return;
    var d=(S.opts.defaultFormats||[]).filter(function(k){return formatPlatform(k)===pl;})[0]||(S.opts.formats.filter(function(f){return f.platform===pl;})[0]||{}).key;
    if(d) S.formats.push(d);
  });
  paintForm();
}
function platformOfFormats(keys){
  var g=keys.some(function(k){return formatPlatform(k)==="google";}), m=keys.some(function(k){return formatPlatform(k)==="meta";});
  return g&&!m?"google":m&&!g?"meta":"both";
}
function formatsHtml(){
  return platFormats().map(function(f){
    var on=S.formats.indexOf(f.key)>=0;
    return '<button type="button" class="cb-card'+(on?" on":"")+'" data-f="'+esc(f.key)+'" aria-pressed="'+on+'">'+
      '<span class="cb-card-top"><span class="cb-card-ic"><svg viewBox="0 0 24 24" aria-hidden="true">'+(ICON[f.platform]||"")+'</svg></span><span class="cb-card-sw" aria-hidden="true"></span></span>'+
      '<span class="cb-card-t">'+esc(f.label)+'</span><span class="cb-card-s">'+esc(f.about)+'</span>'+
      '<span class="cb-card-tags">'+(on?'<span class="cb-tag ok">On</span>':'<span class="cb-tag">Off</span>')+(f.brief?'<span class="cb-tag paid">Creative brief</span>':"")+'</span></button>';
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
      '<span class="cb-card-tags">'+(why?'<span class="cb-tag warn">'+esc(why)+'</span>':on?'<span class="cb-tag ok">On</span>':'<span class="cb-tag">Off</span>')+'</span></button>';
  }).join("");
}
function tonesHtml(){
  return Object.keys(S.opts.tones).map(function(k){return '<option value="'+esc(k)+'"'+(k===S.opts.defaultTone?" selected":"")+'>'+esc(S.opts.tones[k])+'</option>';}).join("");
}
function paintForm(){
  $("#goals").innerHTML=goalsHtml(); $("#formats").innerHTML=formatsHtml(); $("#angles").innerHTML=anglesHtml(); $("#cards").innerHTML=cardsHtml();
  if(!S.tonePainted){S.tonePainted=true; $("#fTone").innerHTML=tonesHtml();}
  if(!S.opts.modelReady) $("#goNote").innerHTML='<b>The AI is not available on this server</b> (no Anthropic key is set), so copy cannot be written here yet.';
}
function setMsg(html,cls){var m=$("#msg"); m.className=cls||""; m.innerHTML=html?'<div class="'+(cls==="pm-note"?"pm-note":"pm-err")+'" role="alert">'+html+'</div>':"";}

function payload(){
  var research={}; S.opts.cards.forEach(function(c){research[c.key]=!blocked(c)&&!!S.on[c.key];});
  return {name:$("#fName").value, website:$("#fSite").value, account:S.account, industry:$("#fIndustry").value, goal:S.goal, product:$("#fProduct").value,
    audience:$("#fAudience").value, locations:$("#fPlaces").value, tone:$("#fTone").value||S.opts.defaultTone, language:$("#fLang").value, formats:S.formats.slice(), angles:S.angles.slice(),
    briefs:$("#fBriefs").checked!==false, facts:$("#fFacts").value, keywords:$("#fKeywords").value, avoid:$("#fAvoid").value, competitors:$("#fComp").value,
    campaignName:$("#fCampaign").value, adGroupName:$("#fAdGroup").value, research:research};
}
function start(){
  setMsg("");
  var p=payload();
  if(p.name.trim().length<2){setMsg("Give the client a name.");$("#fName").focus();return;}
  if(p.product.trim().length<3){setMsg("Say what the ads are for.");$("#fProduct").focus();return;}
  if(!p.formats.length){setMsg("Pick at least one ad format.");return;}
  if(!p.angles.length){setMsg("Pick at least one angle.");return;}
  $("#goBtn").disabled=true;
  sendJson(API+"/start","POST",p).then(function(r){
    $("#goBtn").disabled=false;
    if(!r.ok){setMsg(esc((r.j&&r.j.error)||("The copy could not be started (HTTP "+r.status+").")));return;}
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
  $("#runTitle").textContent=light.stale?"The writing was interrupted":light.status==="error"?"The writing stopped":"Writing";
  $("#runSub").textContent=light.stale?"The server restarted while it was working. What was already written is kept.":(prog.phase||"")+(light.started?" · started "+ago(light.started):"");
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
        if(r.status===404){leave();setMsg("That copy is no longer there.");return;}
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
    if(!r.ok){setMsg(esc((r.j&&r.j.error)||"That copy could not be opened."));leave();return;}
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
  return '<div class="cb-tw"><table class="cb-t'+(cls?" "+cls:"")+'"><thead><tr>'+head.map(function(h){return "<th>"+cell(h)+"</th>";}).join("")+"</tr></thead><tbody>"+
    rows.map(function(r){return "<tr>"+r.map(function(c){return "<td"+(c&&typeof c==="object"&&c.cls?' class="'+esc(c.cls)+'"':"")+">"+cell(c)+"</td>";}).join("")+"</tr>";}).join("")+"</tbody></table></div>";
}
function ul(items){
  items=(items||[]).filter(Boolean); if(!items.length) return "";
  return '<ul class="cb-ul">'+items.map(function(x){return "<li>"+cell(x)+"</li>";}).join("")+"</ul>";
}
function sec(title,body,note){
  if(!body) return "";
  return '<section class="pm-panel cb-sec"><div class="pm-panel-h"><span class="pm-panel-t">'+esc(title)+'</span>'+(note?'<span class="pm-panel-s">'+esc(note)+'</span>':"")+'</div>'+body+'</section>';
}
function para(t){return t?'<p class="cb-p">'+esc(t)+'</p>':"";}
function fine(t){return t?'<p class="cb-fine">'+esc(t)+'</p>':"";}
function kpi(label,value,sub){return '<div class="cb-kpi"><span>'+esc(label)+'</span><b>'+esc(value)+'</b>'+(sub?'<em>'+esc(sub)+'</em>':"")+'</div>';}
function pill(text,cls){return '<span class="cb-pill'+(cls?" "+cls:"")+'">'+esc(text)+'</span>';}
function plan(){return (S.rec&&S.rec.plan)||{};}
function checks(){return plan().checks||{};}
function results(){var p=plan(), f=p.formats||{}; return (p.order||[]).filter(function(k){return f[k];}).map(function(k){return f[k];});}
function resultOf(key){return (plan().formats||{})[key]||null;}

/* the pieces the tabs and the copy buttons share: plain functions of a result, so a line is copied exactly as the page shows it */
function meter(chars,limit){
  var pct=limit?Math.min(100,Math.round(chars/limit*100)):0;
  return '<span class="ac-meter'+(limit&&chars>=limit?" full":"")+'"><span>'+esc(chars)+(limit?" / "+esc(limit):"")+'</span><i><b style="width:'+pct+'%"></b></i></span>';
}
function fieldCopy(res,fi,readyOnly){
  var f=res.fields[fi]; if(!f) return "";
  return f.items.filter(function(i){return !readyOnly||i.status==="ready";}).map(function(i){return i.text;}).join("\n");
}
function briefText(res){
  var b=res.brief; if(!b) return "";
  var out=[res.label,"Concept: "+b.concept,"Hook: "+b.hook,"Visual direction: "+b.visual];
  (b.script||[]).forEach(function(s,n){out.push("Beat "+(n+1)+": "+[s.beat,s.onScreen?"On screen: "+s.onScreen:"",s.voiceover?"Said: "+s.voiceover:""].filter(Boolean).join(" | "));});
  [["Ask the client for","assets"],["Do","dos"],["Do not","donts"]].forEach(function(p){(b[p[1]]||[]).forEach(function(x){out.push(p[0]+": "+x);});});
  (res.specs||[]).forEach(function(s){out.push("Size, "+s.asset+": "+s.size+". "+s.note);});
  return out.join("\n");
}
function questionsText(){
  var rows=checks().claims||[];
  return "Please confirm these before the ads run. Each is a claim the website and the facts we were given do not state:\n"+rows.map(function(c){return "- \""+c.text+"\" ("+c.format+"): "+c.claims.join(", ");}).join("\n");
}
function statusCell(i){
  if(i.status==="ready") return H(pill("Ready","ok")+(i.flags&&i.flags.length?'<span class="ac-claims">'+esc(i.flags.join(" "))+'</span>':""));
  return H(pill("Confirm","medium")+'<span class="ac-claims">'+esc("Not on the pages or in your facts: "+(i.claims||[]).join(", "))+'</span>');
}
function fieldBlock(res,fi){
  var f=res.fields[fi], ready=f.items.filter(function(i){return i.status==="ready";}).length;
  var head='<div class="ac-fh"><h4>'+esc(f.label)+'<em>'+esc(f.items.length)+" kept, up to "+esc(f.max)+", at most "+esc(f.limit)+" characters"+(f.items.length<f.min?"; "+esc(f.min)+" needed":"")+'</em></h4>'+
    (f.items.length?'<button type="button" class="ac-copy" data-copyall="'+esc(res.key+"|"+fi)+'">Copy the '+esc(ready)+' ready line'+(ready===1?"":"s")+'</button>':"")+'</div>';
  if(!f.items.length) return '<div class="ac-field">'+head+'<p class="cb-fine">Nothing usable came back for this field.</p></div>';
  var rows=f.items.map(function(i,n){
    return [{cls:"ac-c1",h:'<span class="ac-t">'+esc(i.text)+'</span>'},{cls:"ac-nw",h:meter(i.chars,f.limit)},{cls:"ac-nw",h:i.angle?pill(i.angle,i.angle==="other"?"skipped":""):""},statusCell(i),
      {cls:"ac-nw",h:'<button type="button" class="ac-copy" data-copy="'+esc(res.key+"|"+fi+"|"+n)+'">Copy</button>'}];
  });
  return '<div class="ac-field">'+head+tbl(["Text","Length","Angle","Status",""],rows)+'</div>';
}
function formatTab(res){
  var out="";
  if(res.shortfalls&&res.shortfalls.length) out+='<div class="pm-note warn">This format came out short: '+esc(res.shortfalls.join("; "))+'. The model was asked a second time. Write it again to try once more.</div>';
  res.fields.forEach(function(f,fi){out+=fieldBlock(res,fi);});
  var meta="";
  if(res.paths&&(res.paths.path1||res.paths.path2)) meta+='<span>Display path <b>/'+esc(res.paths.path1)+(res.paths.path2?"/"+esc(res.paths.path2):"")+'</b></span>';
  if(res.cta) meta+='<span>Button <b>'+esc(res.cta)+'</b>'+(res.ctaNote?" ("+esc(res.ctaNote)+")":"")+'</span>';
  if(meta) out+='<div class="ac-meta">'+meta+'</div>';
  if(res.brief) out+=fine("This format has a creative brief: see the Creative briefs tab.");
  return sec(res.label,out);
}
function briefCard(res){
  var b=res.brief; if(!b) return "";
  var kv='<dl class="ac-kv">'+[["Concept",b.concept],["Hook",b.hook],["Visual direction",b.visual]].filter(function(x){return x[1];}).map(function(x){return "<dt>"+esc(x[0])+"</dt><dd>"+esc(x[1])+"</dd>";}).join("")+"</dl>";
  var script=tbl(["Beat","On screen","Said"],(b.script||[]).map(function(s,n){return [{h:'<b>'+(n+1)+'.</b> '+esc(s.beat)},s.onScreen||"",s.voiceover||""];}));
  var lists=[["Ask the client for","assets"],["Do","dos"],["Do not","donts"]].map(function(p){return (b[p[1]]||[]).length?"<h5 class=\"cb-h5\">"+esc(p[0])+"</h5>"+ul(b[p[1]]):"";}).join("");
  var specs=tbl(["Asset","Size","Note"],(res.specs||[]).map(function(s){return [s.asset,s.size,s.note];}));
  var claims=(b.claims||[]).length?'<div class="pm-note warn">The brief states something the evidence does not hold: '+esc(b.claims.map(function(c){return '"'+c.text+'" ('+c.claims.join(", ")+")";}).join("; "))+'</div>':"";
  return '<div class="ac-brief-card"><div class="ac-fh"><h4>'+esc(res.label)+'</h4><button type="button" class="ac-copy" data-copybrief="'+esc(res.key)+'">Copy the brief</button></div>'+claims+kv+
    (script?"<h5 class=\"cb-h5\">Script</h5>"+script:"")+lists+(specs?"<h5 class=\"cb-h5\">Sizes</h5>"+specs:"")+'</div>';
}

function paintResult(){
  var rec=S.rec, p=plan(), inp=rec.inputs||{};
  $("#builder").hidden=true; $("#run").hidden=true; $("#result").hidden=false;
  $("#rName").textContent=rec.name;
  var meta=[];
  if(inp.website) meta.push(esc(shortUrl(inp.website)));
  if(rec.accountName) meta.push("account "+esc(rec.accountName));
  meta.push("by "+esc((rec.createdBy||"").split("@")[0]||"someone")+" "+esc(ago(rec.createdAt)));
  if(rec.cost&&rec.cost.usd!=null) meta.push("cost to write $"+esc(num(rec.cost.usd,2)));
  $("#rMeta").innerHTML=meta.join(" · ");
  var b='';
  if(p.generatedAt){
    var hasRsa=!!resultOf("google_rsa");
    b+='<a class="pm-btn" href="'+API+'/'+rec.id+'/download?file=xlsx" download>Excel</a>'+
       '<a class="pm-btn" href="'+API+'/'+rec.id+'/download?file=pdf" download>PDF</a>'+
       '<details class="cb-more"><summary class="pm-btn-ghost">More downloads</summary><div class="cb-menu">'+
       (hasRsa?'<a href="'+API+'/'+rec.id+'/download?file=csv" download>The search ad, for Google Ads Editor (CSV)</a>':"")+
       '<a href="'+API+'/'+rec.id+'/download?file=zip" download>Every table as CSV (zip)</a></div></details>';
  }
  b+='<button type="button" class="pm-btn-ghost" id="rebuildBtn">Write again from the start</button><button type="button" class="pm-btn-ghost" id="reuseBtn" title="Open the form with these details filled in">Change the details</button>'+
     '<button type="button" class="pm-btn-ghost danger" id="delBtn">Remove</button><button type="button" class="pm-btn-ghost" id="backBtn">New copy</button>';
  $("#rBtns").innerHTML=b;
  var note="";
  if(rec.status==="error") note='<div class="pm-err" role="alert"><b>The writing stopped.</b> '+esc(rec.error||"")+' What was already written is kept: press <b>Finish what is missing</b> to carry on from where it stopped.'+
      '<div class="cb-go"><button type="button" class="pm-btn" id="carryBtn">Finish what is missing</button></div></div>';
  else if(rec.note) note='<div class="pm-note warn">'+esc(rec.note)+'</div>';
  $("#rNote").innerHTML=note;
  paintTabs();
}
function tabsList(){
  var out=[["overview","Overview"]], c=checks(), n=(c.claims||[]).length;
  results().forEach(function(r){out.push([r.key,r.short]);});
  if(results().some(function(r){return r.brief;})) out.push(["briefs","Creative briefs"]);
  if(n) out.push(["confirm","To confirm ("+n+")"]);
  out.push(["checks","Checks"]);
  return out;
}
function paintTabs(){
  var list=tabsList();
  if(!list.some(function(t){return t[0]===S.tab;})) S.tab="overview";
  $("#tabs").innerHTML=list.map(function(t){return '<button type="button" role="tab" class="cb-tab'+(t[0]===S.tab?" on":"")+'" data-t="'+esc(t[0])+'" aria-selected="'+(t[0]===S.tab)+'">'+esc(t[1])+'</button>';}).join("");
  var fixed={overview:tabOverview,briefs:tabBriefs,confirm:tabConfirm,checks:tabChecks};
  $("#tabBody").innerHTML=fixed[S.tab]?fixed[S.tab]():formatTab(resultOf(S.tab)||{label:"",fields:[]});
}
function tabOverview(){
  var c=checks(), t=c.totals||{}, inp=(S.rec&&S.rec.inputs)||{}, r=(S.rec&&S.rec.research)||{};
  var out='<div class="cb-kpis">'+kpi("Lines ready",num(t.ready),"checked and allowed to run")+kpi("To confirm",num(t.confirm),"state a claim the client has not confirmed")+kpi("Formats",results().length)+
    kpi("Angles",(inp.angles||[]).length)+'</div>';
  var rows=results().map(function(x){
    var n=0,rd=0; x.fields.forEach(function(f){f.items.forEach(function(i){n++; if(i.status==="ready") rd++;});});
    return [x.label,{h:esc(n),cls:"ac-nw"},{h:esc(rd),cls:"ac-nw"},H(x.shortfalls&&x.shortfalls.length?pill("Short","medium"):pill("Complete","ok")),x.brief?"Yes":""];
  });
  out+=sec("What was written",tbl(["Format","Lines kept","Ready","Result","Creative brief"],rows));
  if((c.claims||[]).length) out+=sec("Before this runs",'<p class="cb-p">'+esc((c.claims||[]).length)+' line'+((c.claims||[]).length===1?"":"s")+' state'+((c.claims||[]).length===1?"s":"")+' something the client\'s website and your facts do not hold. They are marked Confirm and left out of the file for Google Ads Editor. Open <b>To confirm</b>.</p>');
  var read=["website","account"].filter(function(k){return r[k];}).map(function(k){var q=r[k]; return [k==="website"?"Client website":"Google Ads account",H(pill({ok:"Read",partial:"Partly",failed:"Failed",skipped:"Skipped"}[q.state]||q.state,q.state)),q.note||""];});
  out+=sec("What was read",tbl(["Research","Result","What it found"],read),"Anything that failed is named in the checks");
  return out;
}
function tabBriefs(){
  var cards=results().map(briefCard).join("");
  return sec("Creative briefs",cards||'<p class="cb-fine">No format here has a brief.</p>',"For a designer or a video editor. Sizes are the ones the platforms publish: check them before the brief goes out.");
}
function tabConfirm(){
  var rows=(checks().claims||[]).map(function(c){return [c.format,c.field,{h:'<span class="ac-t">'+esc(c.text)+'</span>',cls:"ac-c1"},H(pill(c.claims.join(", "),"medium"))];});
  return sec("To confirm with the client",'<p class="cb-p">Each line below says something the website and the facts you gave do not state. Get the client\'s answer, add it to the facts, and write again, or leave the line out.</p>'+
    tbl(["Format","Field","Line","Claim"],rows)+'<div class="cb-go"><button type="button" class="pm-btn-ghost" id="copyQ">Copy as a message to the client</button></div>');
}
function tabChecks(){
  var c=checks(), out="", why=(S.opts&&S.opts.reasons)||{};
  var drops=Object.keys(c.dropped||{}).map(function(k){return [(why[k]?"A line "+why[k]:k),{h:esc(num(c.dropped[k])),cls:"ac-nw"}];});
  out+=sec("Lines dropped",tbl(["Why","How many"],drops),"Every one was checked by a rule, not by the model");
  out+=sec("Short",ul(c.shortfalls));
  var kw=c.keywords;
  if(kw) out+=sec("Keywords",'<p class="cb-p"><b>In a Google headline:</b> '+esc((kw.covered||[]).join(", ")||"none")+'</p>'+((kw.missing||[]).length?'<p class="cb-p"><b>In no Google headline:</b> '+esc(kw.missing.join(", "))+'</p>':""));
  var f=c.factsUsed||{}, facts=[];
  (f.confirmed||[]).forEach(function(x){facts.push(["Confirmed by you",x]);});
  (f.siteOffers||[]).forEach(function(x){facts.push(["Offer on the website",x]);});
  if(f.siteRating) facts.push(["Rating on the website",(f.siteRating.value||"")+" from "+(f.siteRating.count||"")+" reviews"]);
  out+=sec("The facts the copy was allowed to use",tbl(["Where from","Fact"],facts),f.pagesRead!=null?num(f.pagesRead)+" page"+(f.pagesRead===1?"":"s")+" of the website read":"");
  out+=sec("Not available",ul(c.missing));
  if(c.unreadAccount) out+=sec("The account",'<p class="cb-p">A Google Ads account was chosen but could not be read, so the copy was written without its keywords and ads.</p>');
  var cost=(S.rec&&S.rec.cost)||{};
  if(cost.stages) out+=sec("What it cost",'<p class="cb-fine">Writing: $'+num(cost.model,2)+' in '+cost.calls+' calls. Total: $'+num(cost.usd,2)+'.</p>');
  return out||'<p class="cb-fine">Nothing to check.</p>';
}

/* The details of a finished build, put back in the form so they can be changed and written again as a new one. */
function reuse(rec){
  var i=(rec&&rec.inputs)||{};
  leave();
  var set=function(sel,v){$(sel).value=v==null?"":String(v);};
  set("#fName",i.name); set("#fSite",shortUrl(i.website)); set("#fIndustry",i.industry); set("#fProduct",i.product); set("#fAudience",i.audience); set("#fPlaces",i.locations);
  set("#fTone",i.tone||S.opts.defaultTone); set("#fLang",i.language||"English"); set("#fFacts",i.facts); set("#fKeywords",(i.keywords||[]).join(", ")); set("#fAvoid",i.avoid);
  set("#fComp",(i.competitors||[]).map(function(c){return c.name;}).join("\n")); set("#fCampaign",i.campaignName); set("#fAdGroup",i.adGroupName);
  $("#fBriefs").checked=i.briefs!==false;
  S.goal=i.goal||"leads"; S.formats=(i.formats||[]).slice(); S.angles=(i.angles||[]).slice();
  S.plat=platformOfFormats(S.formats); if(S.platSw) S.platSw.set(S.plat);
  S.account=i.account||""; S.accountObj=S.accounts.filter(function(a){return a.id===S.account;})[0]||null;
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
function copied(btn){return function(ok){btn.textContent=ok?"Copied":"Could not copy: select the text and copy it";};}

/* ── Saved copy ───────────────────────────────────────────── */
var STATE_PILL={done:["Ready","ok"],running:["Working","run"],queued:["Waiting","run"],error:["Stopped","failed"]};
function loadRecent(){
  getJson(API+"/list").then(function(r){
    var box=$("#recent");
    if(!r.ok){box.innerHTML='<p class="cb-fine">'+esc((r.j&&r.j.error)||"The saved copy could not be listed.")+'</p>';return;}
    var rows=r.j.builds||[];
    if(!rows.length){box.innerHTML='<p class="cb-fine">Nothing has been written yet.</p>';return;}
    box.innerHTML=rows.map(function(b){
      var p=b.stale?["Interrupted","failed"]:(STATE_PILL[b.status]||[b.status,""]);
      return '<div class="cb-rec"><button type="button" class="cb-open" data-id="'+esc(b.id)+'"><span class="sq-av" style="--h:'+hueOf(b.name)+'">'+esc(inits(b.name))+'</span>'+
        '<span class="cb-rn"><b>'+esc(b.name)+'</b><em>'+esc((b.formats||[]).join(", ")||"no formats")+' · '+esc((b.createdBy||"").split("@")[0])+' · '+esc(ago(b.createdAt))+'</em></span>'+
        '<span class="cb-rs">'+pill(p[0],p[1])+(b.cost!=null?'<em>$'+num(b.cost,2)+'</em>':"")+'</span></button></div>';
    }).join("");
  }).catch(function(){$("#recent").innerHTML='<p class="cb-fine">The saved copy could not be listed.</p>';});
}

/* ── Events ───────────────────────────────────────────────── */
document.addEventListener("input",function(e){ if(e.target.id==="fSite") paintForm(); });
document.addEventListener("click",function(e){
  var t=e.target;
  var chip=t.closest&&t.closest("#goals [data-g]"); if(chip){S.goal=chip.getAttribute("data-g"); paintForm(); return;}
  var fmt=t.closest&&t.closest("#formats [data-f]");
  if(fmt){var fk=fmt.getAttribute("data-f"), at=S.formats.indexOf(fk); if(at>=0) S.formats.splice(at,1); else S.formats.push(fk); paintForm(); return;}
  var ang=t.closest&&t.closest("#angles [data-a]");
  if(ang){
    var ak=ang.getAttribute("data-a"), ai=S.angles.indexOf(ak);
    if(ai>=0) S.angles.splice(ai,1);
    else if(S.angles.length>=S.opts.maxAngles){setMsg("Pick at most "+S.opts.maxAngles+" angles.","pm-note"); return;}
    else S.angles.push(ak);
    setMsg(""); paintForm(); return;
  }
  var card=t.closest&&t.closest("#cards [data-k]");
  if(card){var key=card.getAttribute("data-k"), c=S.opts.cards.filter(function(x){return x.key===key;})[0]; if(c&&!blocked(c)){S.on[key]=!S.on[key]; paintForm();} return;}
  var tab=t.closest&&t.closest("#tabs [data-t]"); if(tab){S.tab=tab.getAttribute("data-t"); paintTabs(); return;}
  var op=t.closest&&t.closest(".cb-open"); if(op){watch(op.getAttribute("data-id")); return;}
  var cp=t.closest&&t.closest("[data-copy]");
  if(cp){var a=cp.getAttribute("data-copy").split("|"), res=resultOf(a[0]), it=res&&res.fields[+a[1]]&&res.fields[+a[1]].items[+a[2]]; if(it) copyText(it.text,copied(cp)); return;}
  var ca=t.closest&&t.closest("[data-copyall]");
  if(ca){var b=ca.getAttribute("data-copyall").split("|"), r2=resultOf(b[0]); if(r2) copyText(fieldCopy(r2,+b[1],true),copied(ca)); return;}
  var cb=t.closest&&t.closest("[data-copybrief]");
  if(cb){var r3=resultOf(cb.getAttribute("data-copybrief")); if(r3) copyText(briefText(r3),copied(cb)); return;}
  if(t.id==="goBtn"){start(); return;}
  if(t.id==="runBack"||t.id==="backBtn"){leave(); return;}
  if(t.id==="reuseBtn"){reuse(S.rec); return;}
  if(t.id==="copyQ"){copyText(questionsText(),copied(t)); return;}
  if(t.id==="delBtn"){
    if(!window.confirm("Remove this copy from the list? The files you downloaded are not affected.")) return;
    sendJson(API+"/"+S.id,"DELETE").then(function(r){ if(!r.ok){setMsg(esc((r.j&&r.j.error)||"It could not be removed."));return;} leave(); });
    return;
  }
  if(t.id==="rebuildBtn"||t.id==="carryBtn"){
    var resume=t.id!=="rebuildBtn";
    if(!resume&&!window.confirm("Write this again from the start? Everything is written and checked again.")) return;
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
  picker.set([{value:"",label:"No account",sub:"Website and the facts you give only",avatar:"?",hue:200}],"",true);
  Promise.all([getJson(API+"/options"),getJson("/p2/paid-marketing/api/segments/accounts")]).then(function(rs){
    var o=rs[0], a=rs[1];
    if(!o.ok){setMsg(esc((o.j&&o.j.error)||"The page could not load its options."));return;}
    S.opts=o.j; S.formats=(S.opts.defaultFormats||[]).slice(); S.angles=(S.opts.defaultAngles||[]).slice();
    if(window.PMPlatform){
      S.platSw=window.PMPlatform.switcher($("#platSw"),{value:window.PMPlatform.saved()||"both",onChange:function(p){setPlatform(p);}});
      if(S.platSw.value()!=="both") setPlatform(S.platSw.value());
    }
    S.opts.cards.forEach(function(c){S.on[c.key]=!!c.default;});
    S.accounts=(a.ok&&a.j.accounts)||[];
    var items=[{value:"",label:"No account",sub:"Website and the facts you give only",avatar:"?",hue:200}];
    S.accounts.forEach(function(x){items.push({value:x.id,label:x.name,avatar:inits(x.name),hue:hueOf(x.name),sub:dashed(x.id)});});
    S.pickerItems=items; picker.set(items,"");
    if(!a.ok) $("#acctHint").textContent="The Google Ads accounts could not be listed, so this can use the website and your facts only.";
    paintForm(); loadRecent();
    var want=(location.search.match(/[?&]build=([0-9a-f]{32})/)||[])[1]; if(want) watch(want);
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
}
if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",init); else init();
})();
