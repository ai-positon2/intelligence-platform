/* Ad Copy Checker (Paid Marketing Agents).
 *
 * Pick a Google Ads account, an MCC or a Meta ad account. The server reads the text of the live ads and runs the rules on every line (tracker/paid_copycheck*.py): misspellings, placeholders,
 * damaged characters, repeated words, spacing, punctuation, capitals, phone numbers and places against what the team saved, the brand written two ways. This page draws it and never decides
 * what is wrong with a line for itself. The wording and the shapes are copycheck_data.js.
 */
(function(){
"use strict";

var D = window.CCData, API = "/p2/paid-marketing/api/copycheck";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}
function store(k,v){try{if(v===undefined)return localStorage.getItem(k)||"";localStorage.setItem(k,v);}catch(e){return "";}}

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}, meta:[], settings:{accounts:{},allow:[]}, shown:{}, gram:{}};

var scope = null;
function groupOf(id){return scope?scope.groupOf(id):null;}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function postJson(url,body){
  return getJson(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}

function run(refresh,keep){
  if(!S.account){show("empty");setMsg("");return;}
  var seq=++S.seq, parts=window.PMScope.partsOf(S.account), g=groupOf(parts[0]), both=parts.length>1;
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.account));}catch(e){}
  setMsg(both?"Reading Google Ads and Meta Ads together. This takes a little longer.":g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 30 seconds.":"",(both||g)?"pm-note":"");
  if(!keep) show("skel");
  window.PMScope.fetchReport(API+"/report",S.account,refresh===true).then(function(r){
    if(seq!==S.seq) return;
    if(g||both) setMsg("");
    if(!r.ok){
      show(S.report?"out":"empty");
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j;
    if(!keep){ S.open={}; S.shown={}; S.gram={}; S.view=D.firstView(r.j); if(r.j.accounts.length===1) S.open[r.j.accounts[0].id]=true; }
    show("out");
    try{render();}catch(e){setMsg("The report was read but could not be drawn: "+esc(String(e&&e.message||e)));}
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
var SHOWN_ADS = 25, MORE = 25;
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">'+(rep.account.platform==="both"?"Google + Meta":"MCC")+' · '+rep.account.accountCount+' accounts checked together</span>':"")+
    ' · read '+esc(ago(rep.readAt||rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">The platform is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintInsights(); paintViews(); paintList(); paintGlobal();
  $("#foot").innerHTML="Source: the live ads of Google Ads (Search, Display, Performance Max asset groups, sitelinks, callouts and snippets) and Meta (primary text, headline and description of active ads), saved so the same report opens instantly. "+
    "Every finding points at the exact characters. A <b>misspelling</b> is a word the dictionary does not know that is one or two letters from one it does; words the account uses in three or more ads spelt the same way, the brand, the account's web address and the allowed words are not doubted. "+
    "<b>Phone numbers</b> and <b>places</b> are checked only against what the team saved here (the Client Master Config is not built yet). The dictionary is general English: a product or a person's name can look like a typo, and "+
    "\"This is right\" teaches it. The rules do not judge grammar or meaning: the button for Claude does, on request. How long something has been open comes from the weekly run: a problem seen for the first time today has no age. Nothing is changed in an ad account.";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.report).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintInsights(){
  var list=D.insights(S.report), host=$("#insights");
  host.innerHTML=list.length?'<section class="pm-panel da-ins"><div class="pm-panel-h"><span class="pm-panel-t">Across the ads</span></div><ul>'+list.map(function(i){return '<li class="'+i.kind+'">'+esc(i.text)+'</li>';}).join("")+'</ul></section>':"";
}
function paintViews(){
  $("#views").innerHTML=D.VIEWS.map(function(v){
    var n=D.viewCount(S.report,v[0]);
    return '<button type="button" role="radio" class="pm-chip'+(v[0]===S.view?" on":"")+'" data-v="'+v[0]+'" aria-checked="'+(v[0]===S.view)+'">'+esc(v[1])+' <span class="n">'+n+'</span></button>';
  }).join("");
}
$("#views").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return; S.view=b.dataset.v; paintViews(); paintList();
});

function pill(a){var s=D.statusInfo(a.status); return '<span class="bi-pill '+s.tone+'">'+esc(s.label)+'</span>';}
function paintList(){
  var rows=D.shown(S.report,S.view);
  $("#listNote").textContent=S.report.accounts.length+(S.report.accounts.length===1?" account":" accounts")+", the ones to fix first";
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Nothing needs a look: the copy of the ads read is clean.")+'</p>';
}
function figHtml(a){
  var c=a.counts;
  if(a.status==="clean") return '<div class="bs-fig ok"><span class="bi-n">0</span><span class="bs-sub">nothing wrong in '+D.plural(a.ads,"ad")+'</span></div>';
  if(a.status==="fix") return '<div class="bs-fig stopped"><span class="bi-n">'+D.int(c.high+c.medium)+'</span><span class="bs-sub">ad'+(c.high+c.medium===1?"":"s")+' to fix'+(c.high?' · '+c.high+' high':"")+'</span></div>';
  return '<div class="bs-fig under"><span class="bi-n">'+D.int(c.low)+'</span><span class="bs-sub">ad'+(c.low===1?"":"s")+' with small things</span></div>';
}
function row(a){
  var open=!!S.open[a.id], top=a.byRule[0];
  return '<article class="bi-row '+(a.status==="fix"?"stopped":a.status==="check"?"limited":"clear")+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+(a.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+'</b><span class="bi-sub">'+D.plural(a.ads,"ad")+' read'+(a.findingCount?', '+D.plural(a.findingCount,"finding"):"")+'</span></span>'+
      figHtml(a)+
      '<span class="bi-delta bad">'+(top?esc(top.label)+' · '+D.plural(top.ads,"ad"):"")+'</span>'+
      '<span class="bi-why">'+(a.spellingChecked?"":"spelling not checked")+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function rulesHtml(a){
  if(!a.byRule.length) return '<p class="pm-empty">Nothing to flag: no line of the '+D.plural(a.ads,"ad")+' read has a slip.</p>';
  return '<h4>What was found</h4><div class="bi-tbl"><table><thead><tr><th>Problem</th><th>Level</th><th class="r">Ads</th><th class="r">Findings</th><th>For example</th></tr></thead><tbody>'+a.byRule.map(function(b){
    var l=D.levelInfo(b.level);
    return '<tr><td><b>'+esc(b.label)+'</b><br><span class="bi-sub">'+esc(b.fix)+'</span></td><td><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></td><td class="r">'+D.int(b.ads)+'</td><td class="r">'+D.int(b.findings)+'</td><td>'+
      b.examples.map(function(x){return '<span class="cc-ex">'+esc(x.excerpt.length>40?x.excerpt.slice(0,38)+"…":x.excerpt)+'</span>';}).join(" ")+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function planHtml(a){
  var steps=(a.plan&&a.plan.steps)||[];
  if(!steps.length) return "";
  return '<div class="bi-plan"><h4>Fix plan</h4><ol class="bi-steps">'+steps.map(function(s,i){
    var l=D.levelInfo(s.level);
    return '<li><details'+(i===0?' open':'')+'><summary><span class="bi-sn">'+(i+1)+'</span><span class="bi-st"><b>'+esc(s.title)+'</b></span><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></summary>'+
      '<div class="bi-sb"><p class="bi-why-t">'+esc(s.how)+'</p>'+
      (s.where&&s.where.length?'<p class="bi-on">Start in: '+s.where.map(function(c){return '<span class="bi-chip">'+esc(c)+'</span>';}).join(" ")+'</p>':"")+
      (s.examples&&s.examples.length?'<p class="bi-on">For example: '+s.examples.map(function(c){return '<span class="cc-ex">'+esc(c)+'</span>';}).join(" ")+'</p>':"")+'</div></details></li>';
  }).join("")+'</ol></div>';
}
function textHtml(line){
  return D.segments(line.text,line.findings).map(function(p){
    if(!p.f.length) return esc(p.t);
    var f=line.findings[p.f[0]];
    return '<mark class="cc-hl lv-'+esc(f.level)+'" title="'+esc(f.message)+'">'+esc(p.t)+'</mark>';
  }).join("");
}
function adHtml(a,i){
  var lv=D.levelInfo(i.level), age=D.agePhrase(i), flagged=D.lines(i), rest=Math.max(0,(i.lineCount||0)-flagged.length);
  return '<li class="cc-ad"><div class="cc-ad-h"><span class="bi-lv '+lv.tone+'">'+esc(lv.label)+'</span><b>'+esc(D.kindText(i.kind))+'</b> <span class="bi-sub">'+esc(i.campaign||"Whole account")+(i.adGroup?" / "+esc(i.adGroup):"")+(age?" · "+esc(age):"")+'</span></div>'+
    '<ul class="cc-lines">'+flagged.map(function(l){
      return '<li><span class="cc-slot">'+esc(D.slotText(l.slot))+'</span><span class="cc-text">'+textHtml(l)+'</span><ul class="cc-why">'+l.findings.map(function(f){
        var fl=D.levelInfo(f.level);
        return '<li><span class="bi-lv '+fl.tone+'">'+esc(fl.label)+'</span> '+esc(f.message)+(D.canAllow(f)?' <button type="button" class="cc-ok" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'" data-word="'+esc(D.wordOf(f))+'">This is right</button>':"")+'</li>';
      }).join("")+'</ul></li>';
    }).join("")+'</ul>'+(rest?'<p class="bs-note">'+rest+' other line'+(rest===1?"":"s")+' of this ad '+(rest===1?"has":"have")+' nothing wrong.</p>':"")+'</li>';
}
function adsHtml(a){
  if(!a.items.length) return "";
  var n=S.shown[a.id]||SHOWN_ADS, list=a.items.slice(0,n);
  return '<h4>The ads <span class="dim">('+D.plural(a.items.length,"ad")+' with something to look at, worst first)</span></h4><ul class="cc-ads">'+list.map(function(i){return adHtml(a,i);}).join("")+'</ul>'+
    (a.items.length>n?'<p class="bs-note">The first '+n+' of '+a.items.length+'; <button type="button" class="cc-more" data-account="'+esc(a.id)+'">show '+Math.min(MORE,a.items.length-n)+' more</button>. The CSV has all.</p>':"");
}
function grammarHtml(a){
  var g=S.gram[a.id];
  var head='<h4>Grammar and wording <span class="dim">(read by Claude, on request)</span></h4>';
  if(!g) return head+'<p class="bi-sub">The checks above are plain rules. Claude can also read the lines for errors of grammar and wording (a wrong word, a missing word). It reads the account\'s distinct lines once and every error it names is checked against the line before it is shown.</p>'+
    '<p><button type="button" class="pm-btn-ghost cc-gram" data-account="'+esc(a.id)+'">Read the grammar with Claude</button></p>';
  if(g.loading) return head+'<p class="bi-sub">Claude is reading the lines of '+esc(a.name)+'. This takes about 20 to 60 seconds.</p>';
  if(g.error) return head+'<p class="pm-err">'+esc(g.error)+'</p><p><button type="button" class="pm-btn-ghost cc-gram" data-account="'+esc(a.id)+'">Try again</button></p>';
  return head+'<p class="bi-sub">'+esc(D.grammarNote(g))+'</p>'+(g.issues.length?'<div class="bi-tbl"><table><thead><tr><th>Line</th><th>Should be</th><th>Why</th><th class="r">Ads</th></tr></thead><tbody>'+g.issues.map(function(i){
    return '<tr><td>'+esc(i.text.slice(0,i.start))+'<mark class="cc-hl lv-medium">'+esc(i.excerpt)+'</mark>'+esc(i.text.slice(i.end))+'<br><span class="bi-sub">'+esc(i.slots.map(D.slotText).join(", "))+' · '+esc(i.where.join(", "))+'</span></td><td>'+esc(i.fix)+'</td><td>'+esc(i.why)+'</td><td class="r">'+D.int(i.ads)+'</td></tr>';
  }).join("")+'</tbody></table></div>':"");
}
function phonesHtml(a){
  if(!a.phones.length) return "";
  return '<h4>Phone numbers in the ads</h4><div class="bi-tbl"><table><thead><tr><th>Number</th><th class="r">Ads</th><th>On the saved list</th></tr></thead><tbody>'+a.phones.map(function(p){
    var saved=((S.settings.accounts[a.id]||{}).phones||[]).map(function(x){return x.replace(/\D/g,"");});
    return '<tr><td>'+esc(p.number)+'</td><td class="r">'+D.int(p.ads)+'</td><td>'+(saved.length?(saved.indexOf(p.digits)>=0?"yes":'<b>no</b>'):'<span class="bi-sub">no list saved</span>')+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function settingsHtml(a){
  var s=S.settings.accounts[a.id]||{phones:[],places:[],brand:[],allow:[]};
  function ta(k,label,hint,rows){return '<label class="cc-f"><span>'+label+'</span><textarea data-k="'+k+'" rows="'+(rows||2)+'" spellcheck="false" placeholder="'+esc(hint)+'">'+esc((s[k]||[]).join(k==="allow"?", ":"\n"))+'</textarea></label>';}
  return '<h4>What the team knows about this account</h4><form class="cc-set" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'">'+
    ta("phones","Phone numbers its ads may carry","617-555-0100, one per line")+ta("places","Places it serves","Quincy, MA, one per line")+ta("brand","The brand, written the way the client writes it","SmileCo, one per line")+
    ta("allow","Words that are right","invisalign, zoomwhitening",2)+
    '<div class="cc-set-b"><button class="pm-btn" type="submit">Save</button><span class="bi-sub" data-out></span></div></form>';
}
function body(a){
  return '<div class="bi-body">'+rulesHtml(a)+planHtml(a)+adsHtml(a)+grammarHtml(a)+phonesHtml(a)+settingsHtml(a)+'</div>';
}
function paintGlobal(){
  var el=$("#globalAllow"); if(!el) return;
  if(document.activeElement!==el) el.value=(S.settings.allow||[]).join(", ");
}
$("#list").addEventListener("click",function(e){
  var ok=e.target.closest(".cc-ok");
  if(ok){ ok.disabled=true; allowWord(ok.dataset.account,ok.dataset.name,ok.dataset.word,ok); return; }
  var gram=e.target.closest(".cc-gram");
  if(gram){ readGrammar(gram.dataset.account); return; }
  var more=e.target.closest(".cc-more");
  if(more){ S.shown[more.dataset.account]=(S.shown[more.dataset.account]||SHOWN_ADS)+MORE; paintList(); return; }
  var head=e.target.closest(".bi-head"); if(head){
    var art=head.closest(".bi-row"), id=art.dataset.id; S.open[id]=!S.open[id]; paintList();
  }
});

/* ── Claude's read of the grammar ────────────────────────── */
function readGrammar(account){
  S.gram[account]={loading:true}; paintList();
  postJson(API+"/grammar",{account:account}).then(function(r){
    S.gram[account]=r.ok?r.j:{error:(r.j&&r.j.error)||("The grammar could not be read (HTTP "+r.status+").")};
    paintList();
  }).catch(function(){ S.gram[account]={error:"The server could not be reached. Try again."}; paintList(); });
}

/* ── What the team saved ──────────────────────────────────── */
function loadSettings(){
  return getJson(API+"/settings").then(function(r){ if(r.ok) S.settings={accounts:r.j.accounts||{},allow:r.j.allow||[]}; }).catch(function(){});
}
function allowWord(account,name,word,btn){
  if(!word){ if(btn) btn.disabled=false; return; }
  postJson(API+"/settings/allowed-word",{account:account,words:word}).then(function(r){
    if(!r.ok){ if(btn) btn.disabled=false; setMsg(esc((r.j&&r.j.error)||"That could not be saved.")); return; }
    S.settings.accounts[account]=Object.assign({},r.j.account,{name:name});
    run(false,true);
  }).catch(function(){ if(btn) btn.disabled=false; setMsg("The server could not be reached. Try again."); });
}
$("#list").addEventListener("submit",function(e){
  var f=e.target.closest(".cc-set"); if(!f) return; e.preventDefault();
  var body={account:f.dataset.account,name:f.dataset.name}, out=f.querySelector("[data-out]");
  [].forEach.call(f.querySelectorAll("textarea"),function(t){ body[t.dataset.k]=t.value; });
  out.textContent="Saving…";
  postJson(API+"/settings/account",body).then(function(r){
    if(!r.ok){ out.textContent=(r.j&&r.j.error)||"That could not be saved."; return; }
    S.settings.accounts[body.account]=r.j.account;
    out.textContent="Saved. Checking again…";
    run(false,true);
  }).catch(function(){ out.textContent="The server could not be reached. Try again."; });
});
$("#globalSave").addEventListener("click",function(){
  var out=$("#globalOut"); out.textContent="Saving…";
  postJson(API+"/settings/allow",{words:$("#globalAllow").value}).then(function(r){
    if(!r.ok){ out.textContent=(r.j&&r.j.error)||"That could not be saved."; return; }
    S.settings.allow=r.j.allow; out.textContent="Saved. Checking again…"; run(false,true);
  }).catch(function(){ out.textContent="The server could not be reached. Try again."; });
});

/* ── Refresh and CSV ──────────────────────────────────────── */
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var blob=new Blob(["\ufeff"+D.csvRows(rep).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="ad-copy-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+String(rep.generatedAt||"").slice(0,10)+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("cc_account")||"";
  if(window.MonitorPanel&&$("#monPanel")) window.MonitorPanel.mount($("#monPanel"),{agent:"copycheck",label:"Ad Copy Checker"});
  S.account="";
  (typeof loadSettings==="function"?loadSettings():Promise.resolve()).then(function(){
    scope=window.PMScope.mount($("#acctPick"),{account:want,onChange:function(a){ S.account=a; store("cc_account",a); run(); },
      onError:function(e){setMsg(esc((e&&e.message)||"The account list could not be read."));}});
  });
})();
})();
