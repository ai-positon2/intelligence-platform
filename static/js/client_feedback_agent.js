/* Client Feedback Agent (Paid Marketing Agents).
 *
 * Ported from Agent Holmes' landing/client_feedback_agent.html. A question
 * starts a background job (POST .../chat); this page polls the job and shows
 * each tool call as the server records it, then the answer.
 *
 * The context bar aims questions: a client (from the latest Pacing Desk run,
 * or a built-in list before the first push) and a timeframe. Both travel
 * with the question as a short context line the model reads; the bubble
 * shows the question as typed, with the context as chips.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/feedback";
var root = document.getElementById("cfa");
var IS_ADMIN = root.dataset.admin === "yes";
var stream = document.getElementById("stream"), welcome = document.getElementById("welcome");
var input = document.getElementById("input"), sendBtn = document.getElementById("sendBtn");
var turns = [], busy = false, client = "", when = "any";

// The Pacing Desk's clients, used until its first run is pushed.
var FALLBACK_CLIENTS = ["BD Diesel","Beta Bionics","Brian Tooley Racing","D&J Precision Machine","Eventgroove",
  "GD & Affiliates","Inspire Aesthetics","McHale Landscaping","Monster Transmission","OIA","Phoenix","Riccobene"];

// Clients the team asks about that have no Budget Tracker row, so the Pacing Desk run never lists them.
// Without this they are missing from the picker whenever the run's own client list loads.
var EXTRA_CLIENTS = ["Soteri Skin","Tealium"];
function withExtraClients(list){
  var out=list.slice();
  EXTRA_CLIENTS.forEach(function(n){ if(!out.some(function(c){return c.name===n})) out.push({name:n}); });
  return out;
}

function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function hueOf(name){var h=0;for(var i=0;i<name.length;i++)h=(h*31+name.charCodeAt(i))%360;return h;}
function inits(name){return name.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
var ALL='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"><circle cx="9" cy="8" r="3.2"/><circle cx="17" cy="9.5" r="2.4"/><path d="M3.5 19c.8-3.2 3-5 5.5-5s4.7 1.8 5.5 5M14.5 15.2c.7-.5 1.6-.8 2.5-.8 2 0 3.5 1.4 4 3.6"/></svg>';

/* ── Timeframe ─────────────────────────────────────────────── */
var WHEN = {
  week:{label:"This week", phrase:"this week", days:7},
  month:{label:"Last 30 days", phrase:"in the last 30 days", days:30},
  any:{label:"Any time", phrase:"recently", days:0}
};
function iso(d){return d.toISOString().slice(0,10);}
var seg=document.getElementById("when");
function paintWhen(){
  var on=null;
  seg.querySelectorAll("button").forEach(function(b){var y=b.dataset.when===when;b.setAttribute("aria-checked",String(y));if(y)on=b;});
  var g=seg.querySelector(".cf-seg-glow");
  if(on){g.style.width=on.offsetWidth+"px";g.style.transform="translateX("+(on.offsetLeft-4)+"px)";}
}
seg.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;when=b.dataset.when;paintWhen();paintCtx();paintStarters();});
window.addEventListener("resize",paintWhen);
var plat="both";
if(window.PMPlatform){
  var sw=window.PMPlatform.switcher(document.getElementById("platSw"),{value:window.PMPlatform.saved()||"both",onChange:function(p){plat=p;paintCtx();}});
  plat=sw.value();
}

/* ── Client picker ─────────────────────────────────────────── */
var picker = window.PMPicker(document.getElementById("clientPick"), {label:"Asking about", search:true, onChange:function(v){
  client=v; try{localStorage.setItem("cfa_client",v)}catch(e){}
  paintCtx(); paintStarters(); input.focus();
}});
var lastList=[];
function setClients(list){
  lastList=list;
  var items=[{value:"",label:"Any client",sub:"The agent works out who you mean",icon:ALL,hue:300}];
  list.forEach(function(c){
    items.push({value:c.name,label:c.name,avatar:inits(c.name),hue:hueOf(c.name),
      sub:c.accounts!=null?c.accounts+" account"+(c.accounts===1?"":"s")+" on the Pacing Desk":"Client",
      badge:c.offTarget?'<span class="badge pm-pill over">'+c.offTarget+' off target</span>':''});
  });
  if(!items.some(function(i){return i.value===client})) client="";
  picker.set(items,client);
  paintCtx(); paintStarters();
}


/* ── Context chips + the line the model reads ─────────────── */
/* Platform: Google, Meta or Both (the same switch as the other agents). One platform narrows what the model is told to look for; Both adds nothing. */
var PLAT_TEXT={google:"Google Ads only (leave out Meta and Facebook/Instagram ads)",meta:"Meta Ads only (Facebook and Instagram ads; leave out Google Ads)"};
var PLAT_CHIP={google:"Google Ads",meta:"Meta Ads"};
function contextLine(){
  var parts=[];
  if(client) parts.push("client: "+client);
  if(plat!=="both") parts.push("platform: "+PLAT_TEXT[plat]);
  if(when!=="any"){
    var end=new Date(), start=new Date(Date.now()-WHEN[when].days*86400000);
    parts.push("timeframe: "+WHEN[when].label.toLowerCase()+" ("+iso(start)+" to "+iso(end)+")");
  }
  return parts.length?"[Context for this question: "+parts.join("; ")+"]":"";
}
function chipsHtml(c,w,p){
  var out="";
  if(p&&p!=="both"&&PLAT_CHIP[p]) out+='<span class="cf-chip">'+esc(PLAT_CHIP[p])+'</span>';
  if(c) out+='<span class="cf-chip"><span class="av" style="--h:'+hueOf(c)+'">'+esc(inits(c))+'</span>'+esc(c)+'</span>';
  if(w&&w!=="any") out+='<span class="cf-chip">◷ '+esc(WHEN[w].label)+'</span>';
  return out;
}
function paintCtx(){
  var el=document.getElementById("ctx"), h=chipsHtml(client,when,plat);
  el.hidden=!h;
  el.innerHTML=h?'<span class="lbl">Asking about</span>'+h+'<button type="button" class="x" title="Clear the context" aria-label="Clear the context">×</button>':"";
  input.placeholder=client?"Ask anything about "+client+"…":"Ask about a client, a meeting, a budget…";
}
document.getElementById("ctx").addEventListener("click",function(e){
  if(!e.target.closest(".x")) return;
  client=""; when="any"; try{localStorage.removeItem("cfa_client")}catch(x){}
  setClients(lastList); paintWhen();
});

/* ── Starters, rewritten for the chosen client and timeframe ─ */
var SRC_IC={meet:["drive","▶","Meetings"],budget:["sheet","$","Budget"],slack:["slack","#","Slack"],mail:["gmail","@","Email"]};
function starterList(){
  var w=WHEN[when].phrase;
  if(client) return [
    ["meet","Summarise the last "+client+" meeting: decisions, asks and open items"],
    ["budget","What's "+client+"'s current budget and target?"],
    ["slack","What's been said about "+client+" in Slack "+w+"?"],
    ["mail","Any open email threads with "+client+" "+w+"?"]];
  return [
    ["meet","Give me a summary of the last Riccobene meeting"],
    ["budget","What's the latest budget for Eventgroove?"],
    ["slack","What's been said in Slack about Beta Bionics "+w+"?"],
    ["mail","Has OIA emailed us about anything open "+w+"?"]];
}
function paintStarters(){
  document.getElementById("helloTitle").textContent=client?"What do you want to know about "+client+"?":"Ask about a client, a meeting, a budget.";
  document.getElementById("starters").innerHTML=starterList().map(function(s,i){
    var ic=SRC_IC[s[0]];
    return '<button type="button" class="cf-starter" style="animation-delay:'+(i*60)+'ms"><span class="src"><span class="ico cf-'+ic[0]+'">'+ic[1]+'</span>'+ic[2]+
      '</span><span class="q">'+esc(s[1])+'</span><span class="go">Ask →</span></button>';
  }).join("");
}
document.getElementById("starters").addEventListener("click",function(e){
  var b=e.target.closest(".cf-starter"); if(b) send(b.querySelector(".q").textContent);
});

/* ── Markdown subset: escape first, then **bold**, `code`, bullets, headings ── */
function md(text){
  var lines=esc(text).split("\n"), out=[], list=false, para=[];
  function inline(s){return s.replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>").replace(/`([^`]+)`/g,"<code>$1</code>")}
  function flush(){ if(para.length){out.push("<p>"+inline(para.join(" "))+"</p>");para=[];} }
  lines.forEach(function(l){
    var t=l.trim(), b=/^[-*•]\s+(.*)$/.exec(t), h=/^#{1,4}\s+(.*)$/.exec(t), n=/^\d+[.)]\s+(.*)$/.exec(t);
    if(b||n){ flush(); if(!list){out.push("<ul>");list=true;} out.push("<li>"+inline((b||n)[1])+"</li>"); return; }
    if(list){out.push("</ul>");list=false;}
    if(h){ flush(); out.push("<h4>"+inline(h[1])+"</h4>"); return; }
    if(!t){ flush(); return; }
    para.push(t);
  });
  flush(); if(list) out.push("</ul>");
  return out.join("");
}

function scrollEnd(){ stream.scrollTop = stream.scrollHeight; }

function addUser(text, c, w, p){
  welcome.style.display="none";
  var row=document.createElement("div"); row.className="cf-msg user";
  row.innerHTML='<div class="cf-ucol"><div class="cf-bubble"></div>'+(chipsHtml(c,w,p)?'<div class="cf-uctx">'+chipsHtml(c,w,p)+'</div>':'')+'</div>';
  row.querySelector(".cf-bubble").textContent=text;
  stream.appendChild(row); scrollEnd();
}

var ICON={search_slack:["slack","#"],read_slack_channel:["slack","#"],search_email:["gmail","@"],read_email:["gmail","@"],
  get_meeting_transcript:["drive","▶"],get_budget_snapshot:["sheet","$"]};

function startTurn(){
  var wrap=document.createElement("div");
  wrap.innerHTML='<div class="cf-trace"></div><div class="cf-thinking"></div>';
  stream.appendChild(wrap); scrollEnd();
  var thinking=wrap.querySelector(".cf-thinking"), trace=wrap.querySelector(".cf-trace"), orb=null;
  thinking.innerHTML='<span class="pm-panel-s">Thinking…</span>';
  import("/static/js/thinking-orb.js").then(function(m){
    if(thinking.isConnected) orb=m.mountThinkingOrb(thinking,{state:"searching",size:20,layout:"row",
      labels:["Reading the question…","Deciding where to look…"]});
  }).catch(function(){});
  var shown=0;
  return {
    steps:function(steps){
      for(;shown<steps.length;shown++){
        var s=steps[shown], ic=ICON[s.tool]||["note","·"], kind=s.kind==="warn"?"warn":(s.kind==="note"?"note":ic[0]);
        var el=document.createElement("div"); el.className="cf-step "+kind;
        el.innerHTML='<span class="ico">'+(s.kind==="warn"?"!":esc(ic[1]))+'</span><span>'+esc(s.label)+'</span>';
        trace.appendChild(el);
        if(orb&&s.kind==="tool") orb.setState(s.tool.indexOf("read")===0?"weaving":"searching", s.label+"…");
      }
      scrollEnd();
    },
    done:function(answer, tools, secs){
      if(orb){orb.destroy();orb=null;}
      thinking.remove();
      trace.querySelectorAll(".cf-step:not(.warn)").forEach(function(e){e.insertAdjacentHTML("beforeend",'<span class="tick">✓</span>')});
      var row=document.createElement("div"); row.className="cf-msg bot";
      row.innerHTML='<div class="cf-av">CF</div><div class="cf-bubble">'+md(answer||"(No answer came back.)")+
        '<div class="cf-actions"><button type="button" class="cf-copy">Copy</button>'+(secs?'<span>Answered in '+secs+'s'+(tools&&tools.length?' · '+tools.length+' source'+(tools.length===1?'':'s'):'')+'</span>':'')+'</div></div>';
      row.querySelector(".cf-copy").addEventListener("click",function(ev){
        var b=ev.currentTarget;
        (navigator.clipboard?navigator.clipboard.writeText(answer||""):Promise.reject()).then(function(){b.textContent="Copied";setTimeout(function(){b.textContent="Copy"},1400)}).catch(function(){});
      });
      wrap.appendChild(row);
      scrollEnd();
    },
    fail:function(msg){
      if(orb){orb.destroy();orb=null;}
      thinking.remove();
      var e=document.createElement("div"); e.className="cf-error"; e.textContent=msg;
      wrap.appendChild(e); scrollEnd();
    }
  };
}

function api(path, opts){
  return fetch(API+path, Object.assign({credentials:"same-origin"}, opts||{})).then(function(r){
    return r.json().catch(function(){return {}}).then(function(j){
      if(!r.ok) throw new Error(j.error || ("The server answered "+r.status));
      return j;
    });
  });
}

function send(text){
  text=(text||"").trim();
  if(!text||busy) return;
  busy=true; sendBtn.disabled=true;
  addUser(text, client, when, plat);
  var ctx=contextLine(), message=ctx?ctx+"\n\n"+text:text;
  var history=turns.slice();
  input.value=""; grow();
  var turn=startTurn(), t0=Date.now();
  api("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:message,history:history})})
  .then(function(j){
    return new Promise(function(resolve,reject){
      var delay=1200, fails=0;
      (function poll(){
        api("/jobs/"+j.job_id).then(function(job){
          fails=0;
          turn.steps(job.steps||[]);
          if(job.status==="running"){ setTimeout(poll, delay); delay=Math.min(2500,delay+200); return; }
          if(job.status==="failed") return reject(new Error(job.error||"The question failed."));
          resolve(job);
        }).catch(function(e){
          console.warn("Client Feedback Agent: poll", e);
          if(++fails>=6) return reject(new Error("Lost touch with the server while the question was running. Reload the page and ask again."));    // not for ever: a 404 or a lapsed sign-in never gets better
          setTimeout(poll, 4000);
        });
      })();
    });
  })
  .then(function(job){
    turns.push({role:"user",content:message},{role:"assistant",content:job.answer||""});
    turn.done(job.answer, job.tools, Math.round((Date.now()-t0)/1000));
  })
  .catch(function(e){ turn.fail(e.message||"Something went wrong answering that."); })
  .then(function(){ busy=false; sendBtn.disabled=false; input.focus(); });
}

function grow(){ input.style.height="auto"; input.style.height=Math.min(input.scrollHeight,160)+"px"; }
input.addEventListener("input",grow);
input.addEventListener("keydown",function(e){ if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send(input.value);} });
sendBtn.addEventListener("click",function(){send(input.value)});

/* ── Sources: chips in the bar, details + connect in a popover ── */
var LABEL={slack:["Slack","S","slack"],google:["Google","G","google"],zoom:["Zoom","Z","zoom"]};
var WHAT={slack:"Search and channel history",google:"Gmail and Meeting Notes",zoom:"Cloud meeting transcripts"};
var STATE=null;
function stateOf(p){
  var s=(STATE&&STATE.sources[p])||{};
  if(s.state==="connected"||s.state==="configured") return ["on",s.state==="connected"?"Connected":"Configured"];
  if(s.state==="not_connected") return ["off","Not connected"];
  if(s.state==="unavailable") return ["bad","Unavailable"];
  return ["off",p==="zoom"?"Optional":"Not set up"];
}
function paintSources(st){
  STATE=st;
  document.getElementById("sources").innerHTML=["slack","google","zoom"].map(function(p){
    var s=stateOf(p);
    return '<button type="button" class="cf-src-chip '+s[0]+'" data-src="'+p+'" title="'+esc(LABEL[p][0]+": "+s[1])+'"><span class="b"></span>'+LABEL[p][0]+'</button>';
  }).join("");
  var pop=document.getElementById("srcPop");
  if(!pop.hidden) paintPop();
}
function paintPop(){
  var html=["slack","google","zoom"].map(function(p){
    var s=(STATE.sources[p])||{}, l=LABEL[p], st=stateOf(p), line, act="";
    if(s.state==="connected"){
      line='<span class="st ok">Connected'+(s.team?' · '+esc(s.team):'')+(s.connectedBy?' · by '+esc(s.connectedBy.split("@")[0]):'')+'</span>';
      if(IS_ADMIN) act='<button class="pm-btn-ghost" data-disconnect="'+p+'" type="button">Disconnect</button>';
    } else if(s.state==="configured"){ line='<span class="st ok">Configured on the server</span>'; }
    else if(s.state==="not_connected"){
      line='<span class="st">Not connected yet'+(IS_ADMIN?'':'. Ask an admin to connect it.')+'</span>';
      if(IS_ADMIN) act='<a class="pm-btn-ghost" href="/p2/paid-marketing/auth/'+p+'/start">Connect</a>';
    } else if(s.state==="unavailable"){ line='<span class="st">Store unavailable</span>'; }
    else { line='<span class="st">'+(p==="zoom"?'Optional, not set up. Drive notes are used instead.':'Not set up on the server yet')+'</span>'; }
    return '<div class="cf-src"><span class="lg '+l[2]+'">'+l[1]+'</span><span><span class="nm">'+l[0]+'</span><br><span class="st">'+WHAT[p]+'</span><br>'+line+'</span><span class="act">'+act+'</span></div>';
  }).join("");
  document.getElementById("srcPop").innerHTML=html+(STATE.configured&&!STATE.configured.anthropic?'<p class="cf-pop-note">The model key is missing on the server.</p>':'')+
    '<p class="cf-pop-note">One shared Slack and Google account, connected by an admin. Up to 10 rounds of searching per question.</p>';
}
document.getElementById("sources").addEventListener("click",function(e){
  if(!e.target.closest(".cf-src-chip")||!STATE) return;
  var pop=document.getElementById("srcPop"); pop.hidden=!pop.hidden; if(!pop.hidden) paintPop();
});
document.addEventListener("click",function(e){
  var pop=document.getElementById("srcPop");
  if(!pop.hidden&&!e.target.closest(".cf-srcs-wrap")) pop.hidden=true;
});
document.getElementById("srcPop").addEventListener("click",function(e){
  var b=e.target.closest("[data-disconnect]"); if(!b) return;
  var p=b.dataset.disconnect;
  if(!confirm("Disconnect "+LABEL[p][0]+"? Every user's questions stop reading it until an admin connects it again.")) return;
  api("/disconnect/"+p,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}).then(loadStatus)
    .catch(function(err){alert("Could not disconnect: "+err.message)});
});
function loadStatus(){
  return api("/status").then(paintSources).catch(function(e){
    document.getElementById("sources").innerHTML='<span class="cf-src-chip bad"><span class="b"></span>Could not check: '+esc(e.message)+'</span>';
  });
}

import("/static/js/thinking-orb.js").then(function(m){
  var h=document.getElementById("helloOrb"); if(h&&welcome.style.display!=="none") m.mountThinkingOrb(h,{state:"breathing",size:64,layout:"column"});
}).catch(function(){});
loadStatus();
try{client=localStorage.getItem("cfa_client")||"";}catch(e){}
var qClient=new URLSearchParams(location.search).get("client");
if(qClient){
  client=qClient;
  // A client the built-in list doesn't have still gets a row, so a link never loses its aim.
  if(FALLBACK_CLIENTS.indexOf(qClient)<0) FALLBACK_CLIENTS.push(qClient);
}
setClients(withExtraClients(FALLBACK_CLIENTS.map(function(n){return {name:n}})));
fetch("/p2/paid-marketing/api/pacing/clients",{credentials:"same-origin"}).then(function(r){return r.ok?r.json():null})
  .then(function(j){ if(j&&j.clients&&j.clients.length){ var l=j.clients; if(qClient&&!l.some(function(c){return c.name===qClient})) l=l.concat([{name:qClient}]); setClients(withExtraClients(l)); } }).catch(function(){});
paintWhen();
if(location.search) history.replaceState(null,"",location.pathname);
})();
