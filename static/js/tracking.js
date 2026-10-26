/* Tracking Data Health (Paid Marketing Agents).
 *
 * Pick a Google Ads account, an MCC or a Meta ad account. The server reads its last five weeks and judges whether the conversion numbers can be trusted (tracker/paid_tracking*.py). This page
 * draws it and never decides what is broken for itself. The wording and the shapes are tracking_data.js.
 */
(function(){
"use strict";

var D = window.THData, API = "/p2/paid-marketing/api/tracking";
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

var S = {account:"", report:null, seq:0, view:"attention", open:{}};
var scope = null;
function groupOf(id){return scope?scope.groupOf(id):null;}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function postJson(url,body){ return getJson(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); }
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
    if(!keep){ S.open={}; S.view=D.firstView(r.j); if(r.j.accounts.length===1) S.open[r.j.accounts[0].id]=true; }
    show("out");
    try{render();}catch(e){setMsg("The report was read but could not be drawn: "+esc(String(e&&e.message||e)));}
  }).catch(function(){
    if(seq!==S.seq) return;
    show(S.report?"out":"empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Rendering ────────────────────────────────────────────── */
function render(){
  var rep=S.report;
  setMsg(rep.warnings&&rep.warnings.length?esc(rep.warnings.join(" · ")):"");
  $("#fresh").innerHTML='<span class="live"></span><b>'+esc(rep.account.name)+'</b>'+
    (rep.account.isGroup?' <span class="sg-mcc">'+(rep.account.platform==="both"?"Google + Meta":"MCC")+' · '+rep.account.accountCount+' accounts checked together</span>':"")+
    ' · read '+esc(ago(rep.readAt||rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">The platform is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  $("#exportBtn").hidden=window.PMScope.partsOf(S.account).length>1;
  paintKpis(); paintInsights(); paintViews(); paintList();
  var m=rep.minimums||{};
  $("#foot").innerHTML="Source: Google Ads (the account's clicks, cost and conversions by day, each conversion action's conversions, auto-tagging and the remarketing lists' sizes for Search) and Meta (the campaigns' results by day, the pixels and the custom audiences). "+
    "Yesterday is held against the <b>"+(m.baseDays||28)+" days before</b>, without the days the team expected a drop; under "+(m.minBaseDays||7)+" such days nothing is judged. "+
    "Conversions at zero are critical when the account usually has at least "+(m.stopDaily||2)+" a day and clicks held; a fall of "+(m.drop||60)+"% is high; a conversion rate down "+(m.cvrDrop||40)+"% over 3 days is medium. "+
    "A list is flagged when it falls under "+D.int(m.listMin||1000)+" or shrinks "+(m.listDrop||25)+"% in "+(m.listDays||14)+" days, and a pixel when it has not fired for "+(m.pixelHours||24)+" hours. "+
    "An acknowledgement holds for "+(m.ackDays||14)+" days. Nothing is changed in an ad account.";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.report).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
}
function paintInsights(){
  var list=D.insights(S.report), host=$("#insights");
  host.innerHTML=list.length?'<section class="pm-panel da-ins"><div class="pm-panel-h"><span class="pm-panel-t">Worth knowing</span></div><ul>'+list.map(function(i){return '<li class="'+i.kind+'">'+esc(i.text)+'</li>';}).join("")+'</ul></section>':"";
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

function paintList(){
  var rows=D.shown(S.report,S.view);
  $("#listNote").textContent=S.report.accounts.length+(S.report.accounts.length===1?" account":" accounts")+", the least healthy first";
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Every account's tracking looks healthy.")+'</p>';
}
function row(a){
  var open=!!S.open[a.id], st=D.statusInfo(a.status), top=a.findings[0];
  return '<article class="bi-row '+(a.status==="broken"?"stopped":a.status==="healthy"?"clear":"limited")+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'"><span class="bi-pill '+st.tone+'">'+esc(st.label)+'</span>'+
      '<span class="bi-name"><b>'+esc(a.name)+(a.platform==="meta"?' <span class="bi-mk">Meta</span>':"")+'</b><span class="bi-sub">'+(a.owner?"Owner: "+esc(a.owner)+" · ":"")+
        D.plural(a.yesterday.conv,"conversion")+' and '+D.plural(a.yesterday.clicks,"click")+' on '+esc(D.day(a.yesterday.date))+'</span></span>'+
      '<div class="bs-fig th-score '+D.scoreTone(a.score)+'"><span class="bi-n">'+a.score+'</span><span class="bs-sub">health score</span></div>'+
      '<span class="bi-delta bad">'+(top?esc(top.title):"")+'</span><span class="bi-why">'+(a.thin?"too little history":"")+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function chartHtml(a){
  var w=700,h=120,c=D.chart(a.days,w,h);
  if(!c.bars.length) return "";
  return '<h4>The last '+c.bars.length+' days <span class="dim">(bars: clicks, up to '+D.int(c.maxClicks)+' a day; dots: conversions, up to '+D.dec(c.maxConv)+'; shaded: an expected drop)</span></h4>'+
    '<svg class="th-chart" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" role="img" aria-label="Clicks and conversions by day">'+
    c.bars.map(function(b){return '<rect class="th-bar'+(b.expected?" exp":"")+'" x="'+b.x+'" y="'+b.y+'" width="'+b.w+'" height="'+b.h+'"><title>'+esc(D.day(b.date))+': '+D.int(b.clicks)+' clicks, '+D.dec(b.conv)+' conversions</title></rect>';}).join("")+
    c.dots.map(function(p){return '<circle class="th-dot'+(p.zero?" zero":"")+'" cx="'+p.cx+'" cy="'+p.cy+'" r="2.6"></circle>';}).join("")+'</svg>';
}
function findingsHtml(a){
  if(!a.findings.length) return '<p class="pm-empty">Nothing wrong found: conversions, the conversion actions, the lists and the pixels look as they usually do.</p>';
  return '<h4>What was found</h4><ul class="gc-finds">'+a.findings.map(function(f){
    var v=D.findingView(f);
    return '<li><span class="bi-lv '+v.tone+'">'+esc(v.label)+'</span>'+(v.was?' <span class="bi-sub">(was '+esc(v.was)+')</span>':"")+' <b>'+esc(f.title)+'</b>'+
      '<p class="gc-detail">'+esc(f.detail)+'</p>'+(f.evidence&&f.evidence.length?'<ul class="kp-evid">'+f.evidence.map(function(e){return '<li>'+esc(e)+'</li>';}).join("")+'</ul>':"")+
      '<p class="bi-sub">Start with: '+esc(f.fix)+'</p>'+(v.tag?'<p class="th-tag">'+esc(v.tag)+'</p>':"")+
      (v.canAck?'<form class="cc-set th-ack" data-kind="ack" data-account="'+esc(a.id)+'" data-key="'+esc(f.key)+'"><label class="cc-f"><span>Known, being fixed: what is being done</span><input data-k="note" maxlength="200" placeholder="tag fix going out today"></label><div class="cc-set-b"><button class="pm-btn-ghost" type="submit">Acknowledge</button><span class="bi-sub" data-out></span></div></form>':"")+
      (v.canUnack?'<button type="button" class="cc-ok th-unack" data-account="'+esc(a.id)+'" data-key="'+esc(f.key)+'">Withdraw the acknowledgement</button>':"")+'</li>';
  }).join("")+'</ul>';
}
function tableHtml(a,title,rows,cols){
  if(!rows.length) return "";
  return '<h4>'+title+'</h4><div class="bi-tbl"><table><thead><tr>'+cols.map(function(c){return '<th'+(c[2]?' class="r"':"")+'>'+esc(c[0])+'</th>';}).join("")+'<th>State</th><th></th></tr></thead><tbody>'+rows.map(function(r){
    return '<tr>'+cols.map(function(c){return '<td'+(c[2]?' class="r"':"")+'>'+esc(r[c[1]])+'</td>';}).join("")+
      '<td><span class="bi-lv '+r.tone+'">'+esc(r.label)+'</span>'+(r.why?'<br><span class="bi-sub">'+esc(r.why)+'</span>':"")+'</td>'+
      '<td>'+(r.state==="muted"?'<button type="button" class="cc-ok th-unmute" data-account="'+esc(a.id)+'" data-key="'+esc(r.key)+'">Put back</button>':'<button type="button" class="cc-ok th-mute" data-account="'+esc(a.id)+'" data-key="'+esc(r.key)+'" data-name="'+esc(r.name)+'">Leave out</button>')+'</td></tr>';
  }).join("")+'</tbody></table></div>';
}
function partsHtml(a){
  var rows=D.actionRows(a), cols=[["Action","name"],["28 days before","base",1],["Recently","recent",1]], judged=rows.filter(function(r){return r.judged;}), rest=rows.filter(function(r){return !r.judged;});
  var actions=a.actions==null?(a.platform==="meta"?"":'<p class="bi-sub">The conversion actions were not read.</p>'):
    tableHtml(a,"Conversion actions",judged,cols)+(rest.length?'<details class="gc-quiet"><summary>'+D.plural(rest.length,"conversion action")+' not judged (secondary, too few conversions, or left out)</summary>'+tableHtml(a,"",rest,cols)+'</details>':"");
  var lists=a.lists==null?'<p class="bi-sub">The lists were not read.</p>':tableHtml(a,a.platform==="meta"?"Custom audiences":"Remarketing lists",D.listRows(a),[["List","name"],["Now","size",1],["14 days ago","before",1],["Change","change",1]]);
  var pixels=a.pixels==null?"":tableHtml(a,"Pixels",D.pixelRows(a),[["Pixel","name"],["Last fired","last"]]);
  var tag=a.autoTagging===true?'<p class="bi-sub">Auto-tagging is on.</p>':a.autoTagging===false?"":"";
  return actions+lists+pixels+tag;
}
function settingsHtml(a){
  var wins=(a.windows||[]).map(function(w){
    return '<li><b>'+esc(w.from)+' to '+esc(w.to)+'</b> <span class="bi-sub">'+esc(w.why)+'</span> <button type="button" class="cc-ok th-unwin" data-account="'+esc(a.id)+'" data-from="'+esc(w.from)+'" data-to="'+esc(w.to)+'">Remove</button></li>';
  }).join("");
  return '<h4>For this account</h4>'+
    '<form class="cc-set th-set" data-kind="owner" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'"><label class="cc-f"><span>Who looks after its tracking</span><input data-k="owner" maxlength="120" value="'+esc(a.owner||"")+'" placeholder="a name or an email"></label>'+
      '<div class="cc-set-b"><button class="pm-btn" type="submit">Save owner</button><span class="bi-sub" data-out></span></div></form>'+
    '<form class="cc-set th-set" data-kind="window" data-account="'+esc(a.id)+'" data-name="'+esc(a.name)+'"><label class="cc-f"><span>A drop is expected from</span><input type="date" data-k="from"></label><label class="cc-f"><span>to</span><input type="date" data-k="to"></label>'+
      '<label class="cc-f"><span>because</span><input data-k="why" maxlength="200" placeholder="site migration"></label><div class="cc-set-b"><button class="pm-btn-ghost" type="submit">Add the window</button><span class="bi-sub" data-out></span></div></form>'+
    (wins?'<ul class="kp-out">'+wins+'</ul>':"");
}
function body(a){
  return '<div class="bi-body" data-account="'+esc(a.id)+'">'+chartHtml(a)+findingsHtml(a)+(a.notRun&&a.notRun.length?'<p class="bi-sub">'+esc(a.notRun.join(" "))+'</p>':"")+partsHtml(a)+settingsHtml(a)+'</div>';
}

/* ── What the team saves ──────────────────────────────────── */
function save(action,body,out,btn){
  if(out) out.textContent="Saving…";
  return postJson(API+"/settings/"+action,body).then(function(r){
    if(!r.ok){ if(btn) btn.disabled=false; var m=(r.j&&r.j.error)||"That could not be saved."; if(out) out.textContent=m; else setMsg(esc(m)); return; }
    if(out) out.textContent="Saved. Checking again…";
    run(false,true);
  }).catch(function(){ if(btn) btn.disabled=false; if(out) out.textContent="The server could not be reached. Try again."; else setMsg("The server could not be reached. Try again."); });
}
function fields(f){ var o={}; [].forEach.call(f.querySelectorAll("input"),function(t){ o[t.dataset.k]=t.value; }); return o; }
$("#list").addEventListener("click",function(e){
  var t=e.target, b;
  if((b=t.closest(".th-mute"))){
    var why=window.prompt?window.prompt("Leave "+b.dataset.name+" out of the checks because:"):"";
    if(why) { b.disabled=true; save("mute",{account:b.dataset.account,key:b.dataset.key,itemName:b.dataset.name,why:why},null,b); }
    return;
  }
  if((b=t.closest(".th-unmute"))){ b.disabled=true; save("unmute",{account:b.dataset.account,key:b.dataset.key},null,b); return; }
  if((b=t.closest(".th-unack"))){ b.disabled=true; save("unack",{account:b.dataset.account,key:b.dataset.key},null,b); return; }
  if((b=t.closest(".th-unwin"))){ b.disabled=true; save("window-remove",{account:b.dataset.account,from:b.dataset.from,to:b.dataset.to},null,b); return; }
  var head=t.closest(".bi-head"); if(head){ var id=head.closest(".bi-row").dataset.id; S.open[id]=!S.open[id]; paintList(); }
});
$("#list").addEventListener("submit",function(e){
  var f=e.target.closest(".th-set,.th-ack"); if(!f) return; e.preventDefault();
  var v=fields(f), out=f.querySelector("[data-out]");
  if(f.dataset.kind==="ack") save("ack",{account:f.dataset.account,key:f.dataset.key,note:v.note},out);
  else if(f.dataset.kind==="owner") save("owner",{account:f.dataset.account,name:f.dataset.name,owner:v.owner},out);
  else save("window",{account:f.dataset.account,name:f.dataset.name,from:v.from,to:v.to,why:v.why},out);
});

/* ── Refresh and download ─────────────────────────────────── */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){ if(S.account) location.href=API+"/export?"+D.query(S.account,false); });

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("th_account")||"";
  if(window.MonitorPanel&&$("#monPanel")) window.MonitorPanel.mount($("#monPanel"),{agent:"tracking",label:"Tracking Data Health"});
  scope=window.PMScope.mount($("#acctPick"),{account:want,onChange:function(a){ S.account=a; store("th_account",a); run(); },
    onError:function(e){setMsg(esc((e&&e.message)||"The account list could not be read."));}});
})();
})();
