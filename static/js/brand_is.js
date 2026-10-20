/* Brand IS Monitor (Paid Marketing Agents).
 *
 * Pick a Google Ads account (or an MCC). The server reads the Search impression share of the brand campaigns, week by week (tracker/paid_brandis.py),
 * works out the shares, the cause of any shortfall and the findings (paid_brandis_math.py). This page draws it, sends the threshold and the campaigns the
 * person marks as brand, and never works a share, a cause or a finding out for itself. The wording and the shapes are brand_is_data.js.
 */
(function(){
"use strict";

var D = window.BIData, API = "/p2/paid-marketing/api/brandis";
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function hueOf(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function inits(n){return n.replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w)}).slice(0,2).map(function(w){return w[0]}).join("").toUpperCase()||"·";}
function dashed(id){return id.length===10?id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6):id;}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000)), rel=m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
  return t.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})+" ("+rel+")";
}
function store(k,v){try{if(v===undefined)return localStorage.getItem(k)||"";localStorage.setItem(k,v);}catch(e){return "";}}

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}, over:{}, threshold:"90"};

var picker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){
  S.account=v; store("bi_account",v); S.over=loadOver(); run();
}});
function loadOver(){try{return (JSON.parse(store("bi_over")||"{}")||{})[S.account]||{};}catch(e){return {};}}
function saveOver(){try{var all=JSON.parse(store("bi_over")||"{}")||{}; all[S.account]=S.over; store("bi_over",JSON.stringify(all));}catch(e){}}
function groupOf(id){return S.groups.filter(function(g){return g.id===id;})[0]||null;}
var MCC_BADGE='<span class="badge pm-pill ok">MCC</span>';
function setAccounts(list, groups){
  S.accounts=list; S.groups=groups||[];
  var items=[{value:"",label:"Choose an account",sub:list.length+" accounts"+(S.groups.length?" · "+S.groups.length+" MCCs to check together":""),avatar:"?",hue:200}], seen={};
  S.groups.forEach(function(g){
    var head=g.name+" MCC";
    items.push({value:g.id,label:"All of "+g.name,avatar:inits(g.name),hue:hueOf(g.name),group:head,badge:MCC_BADGE,sub:g.accountIds.length+" accounts checked together"});
    list.filter(function(a){return a.group===g.id;}).forEach(function(a){
      seen[a.id]=1; items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:head,sub:dashed(a.id)});
    });
  });
  list.filter(function(a){return !seen[a.id];}).forEach(function(a){
    items.push({value:a.id,label:a.name,avatar:inits(a.name),hue:hueOf(a.name),group:S.groups.length?"Other accounts":"",sub:dashed(a.id)});
  });
  if(!list.some(function(a){return a.id===S.account;})&&!groupOf(S.account)) S.account="";
  picker.set(items,S.account);
}

/* ── Loading ──────────────────────────────────────────────── */
function setMsg(html,kind){$("#msg").innerHTML=html?'<div class="'+(kind||"pm-err")+'">'+html+'</div>':"";}
function getJson(url,opts){
  return fetch(url,Object.assign({credentials:"same-origin"},opts||{})).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};});
  });
}
function show(which){$("#empty").hidden=which!=="empty"; $("#skel").hidden=which!=="skel"; $("#out").hidden=which!=="out";}

function run(refresh){
  if(!S.account){show("empty");setMsg("");return;}
  var seq=++S.seq, g=groupOf(S.account);
  try{history.replaceState(null,"",location.pathname+"?account="+encodeURIComponent(S.account));}catch(e){}
  setMsg(g?"Reading all "+g.accountIds.length+" accounts of "+esc(g.name)+" together. This takes a little longer, about 10 to 20 seconds.":"",g?"pm-note":"");
  show("skel");
  getJson(API+"/report?"+D.query(S.account,S.threshold,S.over,refresh===true)).then(function(r){
    if(seq!==S.seq) return;
    if(g) setMsg("");
    if(!r.ok){
      show(S.report?"out":"empty");
      if(r.j&&r.j.kind==="cooldown"){setMsg(esc(r.j.error),"pm-note");return;}
      var hint=r.j&&r.j.kind==="quota"?" The platform limits how fast this app can read, so this clears on its own.":"";
      setMsg(esc((r.j&&r.j.error)||("The report could not be built (HTTP "+r.status+")."))+hint);
      return;
    }
    S.report=r.j; S.open={}; S.view=D.firstView(r.j);
    if(r.j.accounts.length===1) S.open[r.j.accounts[0].id]=true;
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
    (rep.account.isGroup?' <span class="sg-mcc">MCC · '+rep.account.accountCount+' accounts checked together</span>':"")+
    ' · last whole week, '+esc(rep.window.latestFrom)+' to '+esc(rep.window.latestTo)+' · read from Google Ads '+esc(ago(rep.readAt||rep.generatedAt))+
    (rep.stale?' <span class="sg-mcc">Google Ads is not answering, so this is the copy saved '+esc(ago(rep.stale))+'</span>':"");
  paintKpis(); paintViews(); paintList();
  $("#foot").innerHTML="Source: Google Ads, saved so the same report opens instantly. <b>Impression share is Google's own figure</b>: the share of the searches the brand ads "+
    "were eligible for that they were actually shown on. Several campaigns are put together by the searches each was eligible for, not by a plain average. "+
    "Google reports a share under 10% as \"below 10%\". A week with fewer than 100 eligible searches is not judged. Which campaigns count as brand is decided by their name "+
    "(the word brand, and not non-brand, generic or competitor); mark any campaign the other way below. <b>Who else is bidding on the brand</b> is in Auction Insights in Google Ads, "+
    "which this agent cannot read. Nothing is changed in Google Ads.";
}
function paintKpis(){
  $("#kpis").innerHTML=D.tiles(S.report).map(function(c){
    var cls=c[3]==="good"||c[3]==="warn"?c[3]:"";
    return '<div class="pd-kpi '+cls+'"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1])+'</div><div class="s">'+esc(c[2])+'</div></div>';
  }).join("");
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
  $("#listNote").textContent=S.report.accounts.length+(S.report.accounts.length===1?" account":" accounts")+", worst first";
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="attention"?"Nothing needs a look: every account that could be judged is on target.":"No account here.")+'</p>';
}
function meterHtml(a){
  var m=D.meter(a);
  if(m.share==null) return '<div class="bi-meter none"><span class="bi-n">n/a</span></div>';
  return '<div class="bi-meter '+m.tone+'"><span class="bi-n">'+D.pct(a.brand.share)+'</span><span class="bi-bar" role="img" aria-label="'+D.pct(a.brand.share)+' against a threshold of '+m.threshold+'%">'+
    '<i style="width:'+m.share.toFixed(1)+'%"></i><b style="left:'+m.threshold+'%" title="Threshold '+m.threshold+'%"></b></span></div>';
}
function row(a){
  var open=!!S.open[a.id], d=D.delta(a.delta), why=D.causeText(a.cause);
  return '<article class="bi-row '+a.status+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+'</b><span class="bi-sub">'+(a.brandCampaigns.length?a.brandCampaigns.length+" brand campaign"+(a.brandCampaigns.length===1?"":"s"):"no brand campaign")+' · '+D.int(a.brand.eligible)+' eligible searches</span></span>'+
      meterHtml(a)+
      '<span class="bi-delta '+d.tone+'">'+esc(a.delta==null?"":d.text)+'</span>'+
      '<span class="bi-why">'+(why?esc(why):"")+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function trendHtml(a){
  var bars=D.trendBars(a);
  return '<div class="bi-trend" role="img" aria-label="Brand impression share, the last '+bars.length+' weeks">'+bars.map(function(w){
    return '<span class="bi-w '+w.tone+'" title="'+esc(w.title)+'"><i style="height:'+w.height+'%"></i><em>'+(w.has?D.pct(w.share):"-")+'</em><small>'+esc(w.from.slice(5))+'</small></span>';
  }).join("")+'<span class="bi-th-line" style="bottom:'+Math.round(a.threshold*100)+'%" title="Threshold"></span></div>';
}
function findingsHtml(a){
  if(!a.findings.length) return '<p class="pm-empty">Nothing to flag: the brand is shown on enough of its searches and has not fallen.</p>';
  return '<ul class="bi-finds">'+a.findings.map(function(f){
    var l=D.levelInfo(f.level);
    return '<li><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span><div><b>'+esc(f.title)+'</b><p>'+esc(f.detail)+'</p><p class="bi-next">'+esc(f.nextStep)+'</p></div></li>';
  }).join("")+'</ul>';
}
function campaignsHtml(a){
  var h="";
  if(a.brandCampaigns.length){
    h+='<h4>Brand campaigns</h4><div class="bi-tbl"><table><thead><tr><th>Campaign</th><th class="r">Share</th><th class="r">Eligible</th><th class="r">Lost to budget</th><th class="r">Lost to rank</th><th class="r">Top of page</th><th>Bidding</th><th class="r">Daily budget</th><th></th></tr></thead><tbody>'+
      a.brandCampaigns.map(function(c){
        return '<tr><td>'+esc(c.name)+(c.byOverride?' <span class="bi-mk">marked by you</span>':"")+'</td><td class="r">'+D.pct(c.share)+'</td><td class="r">'+D.int(c.eligible)+'</td><td class="r">'+D.pct(c.lostBudget)+'</td><td class="r">'+D.pct(c.lostRank)+
          '</td><td class="r">'+D.pct(c.top)+'</td><td>'+esc((c.strategy||"").replace(/_/g," ").toLowerCase())+'</td><td class="r">'+(c.budget==null?"n/a":D.int(c.budget))+
          '</td><td class="r"><button type="button" class="pm-btn-ghost bi-mark" data-mark="'+esc(c.id)+'" data-to="not" data-acct="'+esc(a.id)+'">Do not count</button></td></tr>';
      }).join("")+'</tbody></table></div>';
  }
  if(a.otherCampaigns.length){
    h+='<h4>Other Search campaigns <span class="dim">('+a.otherCampaigns.length+')</span></h4><div class="bi-tbl"><table><tbody>'+a.otherCampaigns.map(function(c){
      return '<tr><td>'+esc(c.name)+(c.byOverride?' <span class="bi-mk">marked by you</span>':"")+'</td><td class="r"><button type="button" class="pm-btn-ghost bi-mark" data-mark="'+esc(c.id)+'" data-to="brand" data-acct="'+esc(a.id)+'">Count as brand</button></td></tr>';
    }).join("")+'</tbody></table></div>';
  }
  return h;
}
function body(a){
  return '<div class="bi-body"><div class="bi-cols"><div><h4>Brand impression share by week</h4>'+trendHtml(a)+'</div><div><h4>What it means</h4>'+findingsHtml(a)+'</div></div>'+campaignsHtml(a)+'</div>';
}
$("#list").addEventListener("click",function(e){
  var mk=e.target.closest("[data-mark]");
  if(mk){
    var a=S.report.accounts.filter(function(x){return x.id===mk.dataset.acct;})[0]; if(!a) return;
    var c=a.brandCampaigns.concat(a.otherCampaigns).filter(function(x){return x.id===mk.dataset.mark;})[0]; if(!c) return;
    S.over=D.mark(S.over,c,mk.dataset.to==="brand"); saveOver(); run(); return;
  }
  var head=e.target.closest(".bi-head"); if(head){
    var art=head.closest(".bi-row"), id=art.dataset.id; S.open[id]=!S.open[id]; paintList();
  }
});

/* ── The threshold, Refresh and CSV ───────────────────────── */
var thEl=$("#threshold"), saved=store("bi_threshold");
if(saved&&/^\d+$/.test(saved)){S.threshold=saved; thEl.value=saved;}
thEl.addEventListener("change",function(){
  var v=String(this.value||"").trim();
  if(!/^\d+$/.test(v)||+v<50||+v>99){setMsg("The threshold is a whole percentage from 50 to 99.");this.value=S.threshold;return;}
  S.threshold=v; store("bi_threshold",v); run();
});
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var blob=new Blob(["﻿"+D.csvRows(rep).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="brand-is-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.window.latestTo+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("bi_account")||"";
  S.account=want;
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  getJson("/p2/paid-marketing/api/segments/accounts").then(function(r){
    if(!r.ok){picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:0}],"",true);setMsg(esc((r.j&&r.j.error)||"The account list could not be read."));return;}
    S.over=loadOver(); setAccounts(r.j.accounts||[],r.j.groups||[]); run();
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
})();
})();
