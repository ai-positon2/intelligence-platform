/* Client Feedback Agent (Paid Marketing Agents).
 *
 * Ported from Agent Holmes' landing/client_feedback_agent.html. A question
 * starts a background job (POST .../chat); this page polls the job and shows
 * each tool call as the server records it, then the answer.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/feedback";
var root = document.getElementById("cfa");
var IS_ADMIN = root.dataset.admin === "yes";
var stream = document.getElementById("stream"), welcome = document.getElementById("welcome");
var input = document.getElementById("input"), sendBtn = document.getElementById("sendBtn");
var turns = [], busy = false;

function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}

/* A small, safe markdown subset: escape first, then **bold**, `code`,
   "- " bullets, "#" headings and paragraphs. */
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

function addUser(text){
  welcome.style.display="none";
  var row=document.createElement("div"); row.className="cf-msg user";
  row.innerHTML='<div class="cf-bubble"></div>'; row.firstChild.textContent=text;
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
      row.innerHTML='<div class="cf-av">CF</div><div class="cf-bubble">'+md(answer||"(No answer came back.)")+'</div>';
      wrap.appendChild(row);
      if(tools&&tools.length) wrap.insertAdjacentHTML("beforeend",'<div class="cf-tools">'+tools.map(function(t){return '<span>'+esc(t)+'</span>'}).join("")+'</div>');
      if(secs) wrap.insertAdjacentHTML("beforeend",'<div class="cf-meta">Answered in '+secs+'s</div>');
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
  addUser(text);
  var history=turns.slice();
  input.value=""; grow();
  var turn=startTurn(), t0=Date.now();
  api("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:text,history:history})})
  .then(function(j){
    return new Promise(function(resolve,reject){
      var delay=1200;
      (function poll(){
        api("/jobs/"+j.job_id).then(function(job){
          turn.steps(job.steps||[]);
          if(job.status==="running"){ setTimeout(poll, delay); delay=Math.min(2500,delay+200); return; }
          if(job.status==="failed") return reject(new Error(job.error||"The question failed."));
          resolve(job);
        }).catch(function(e){ setTimeout(poll, 4000); console.warn("Client Feedback Agent: poll", e); });
      })();
    });
  })
  .then(function(job){
    turns.push({role:"user",content:text},{role:"assistant",content:job.answer||""});
    turn.done(job.answer, job.tools, Math.round((Date.now()-t0)/1000));
  })
  .catch(function(e){ turn.fail(e.message||"Something went wrong answering that."); })
  .then(function(){ busy=false; sendBtn.disabled=false; input.focus(); });
}

function grow(){ input.style.height="auto"; input.style.height=Math.min(input.scrollHeight,160)+"px"; }
input.addEventListener("input",grow);
input.addEventListener("keydown",function(e){ if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send(input.value);} });
sendBtn.addEventListener("click",function(){send(input.value)});
document.getElementById("starters").addEventListener("click",function(e){
  var b=e.target.closest(".cf-starter"); if(b) send(b.querySelector(".q").textContent);
});

/* ── Sources panel ─────────────────────────────────────────── */
var LABEL={slack:["Slack","S","slack"],google:["Google","G","google"],zoom:["Zoom","Z","zoom"]};
var WHAT={slack:"Search and channel history",google:"Gmail, Meeting Notes, Budget Tracker",zoom:"Cloud meeting transcripts"};

function paintSources(st){
  var html=["slack","google","zoom"].map(function(p){
    var s=st.sources[p]||{}, l=LABEL[p], line, act="";
    if(s.state==="connected"){
      line='<span class="st ok">Connected'+(s.team?' · '+esc(s.team):'')+(s.connectedBy?' · by '+esc(s.connectedBy.split("@")[0]):'')+'</span>';
      if(IS_ADMIN) act='<button class="pm-btn-ghost" data-disconnect="'+p+'" type="button">Disconnect</button>';
    } else if(s.state==="configured"){
      line='<span class="st ok">Configured on the server</span>';
    } else if(s.state==="not_connected"){
      line='<span class="st">Not connected yet'+(IS_ADMIN?'':'. Ask an admin to connect it.')+'</span>';
      if(IS_ADMIN) act='<a class="pm-btn-ghost" href="/p2/paid-marketing/auth/'+p+'/start">Connect</a>';
    } else if(s.state==="unavailable"){
      line='<span class="st">Store unavailable</span>';
    } else {
      line='<span class="st">'+(p==="zoom"?'Optional, not set up. Drive notes are used instead.':'Not set up on the server yet')+'</span>';
    }
    return '<div class="cf-src"><span class="lg '+l[2]+'">'+l[1]+'</span><span><span class="nm">'+l[0]+'</span><br><span class="st">'+WHAT[p]+'</span><br>'+line+'</span><span class="act">'+act+'</span></div>';
  }).join("");
  document.getElementById("sources").innerHTML=html;
  var ready=st.sources.slack&&st.sources.slack.state==="connected"||st.sources.google&&st.sources.google.state==="connected";
  document.getElementById("srcNote").textContent=st.configured&&!st.configured.anthropic?"Model key missing":(ready?"Live":"Not connected");
}

document.getElementById("sources").addEventListener("click",function(e){
  var b=e.target.closest("[data-disconnect]"); if(!b) return;
  var p=b.dataset.disconnect;
  if(!confirm("Disconnect "+LABEL[p][0]+"? Every user's questions stop reading it until an admin connects it again.")) return;
  api("/disconnect/"+p,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}).then(loadStatus)
    .catch(function(err){alert("Could not disconnect: "+err.message)});
});

function loadStatus(){
  return api("/status").then(paintSources).catch(function(e){
    document.getElementById("sources").innerHTML='<div class="cf-src"><span class="st">Could not check the sources: '+esc(e.message)+'</span></div>';
  });
}
loadStatus();
if(location.search) history.replaceState(null,"",location.pathname);
})();
