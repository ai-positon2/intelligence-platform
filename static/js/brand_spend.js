/* Brand Spend Alert (Paid Marketing Agents).
 *
 * Pick a Google Ads account (or an MCC). The server reads what the brand campaigns spent, day by day (tracker/paid_brandspend.py), holds the last whole week against a usual
 * week from the account's own history, and works out the cause, the findings and the fix plan (paid_brandspend_math.py, paid_brandspend_plan.py). This page draws it, sends the
 * limit and the campaigns the person marks as brand, and never works a sum, a cause or a finding out for itself. The wording and the shapes are brand_spend_data.js.
 */
(function(){
"use strict";

var D = window.BSData, API = "/p2/paid-marketing/api/brandspend";
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

var S = {accounts:[], groups:[], account:"", report:null, seq:0, view:"attention", open:{}, over:{}, limit:"30"};

var picker = window.PMPicker($("#acctPick"),{label:"Google Ads account",search:true,onChange:function(v){
  S.account=v; store("bs_account",v); S.over=loadOver(); run();
}});
function loadOver(){try{return (JSON.parse(store("bs_over")||"{}")||{})[S.account]||{};}catch(e){return {};}}
function saveOver(){try{var all=JSON.parse(store("bs_over")||"{}")||{}; all[S.account]=S.over; store("bs_over",JSON.stringify(all));}catch(e){}}
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
  getJson(API+"/report?"+D.query(S.account,S.limit,S.over,refresh===true)).then(function(r){
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
  $("#foot").innerHTML="Source: Google Ads, saved so the same report opens instantly. <b>What is expected is the account's own history</b>: the middle of the weekly brand spend of the up to 7 weeks before "+
    "the last one (at least 4 of them with spend), with a band of "+esc(D.pct(rep.limit))+" either way. Nothing here is a budget the client set. A move smaller than 25 in the account's currency is not flagged. "+
    "Which campaigns count as brand is decided by their name (the word brand, and not non-brand, generic or competitor); mark any campaign the other way below. Brand keywords inside campaigns "+
    "that are not brand campaigns are not counted. A campaign that never spent has no row in Google's answer, so it cannot be seen. <b>More brand spend is not always a fault</b>: a launch, an event or press "+
    "brings more brand searches. Nothing is changed in Google Ads.";
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
  $("#listNote").textContent=S.report.accounts.length+(S.report.accounts.length===1?" account":" accounts")+", furthest from usual first";
  $("#list").innerHTML=rows.length?rows.map(row).join(""):'<p class="pm-empty">'+(S.view==="all"?"No account here.":"Nothing needs a look: every account that could be judged is inside its usual band.")+'</p>';
}
function figHtml(a){
  var cur=S.report.account.currency, c=D.change(a);
  if(a.status==="none"||a.status==="idle") return '<div class="bs-fig none"><span class="bi-n">n/a</span></div>';
  return '<div class="bs-fig '+a.status+'"><span class="bi-n">'+esc(D.money(a.brand.cost,cur))+'</span><span class="bs-sub">'+(a.expected?'usual '+esc(D.money(a.expected.cost,cur))+' · '+esc(c.text.replace(" on a usual week","")):"no usual week yet")+'</span></div>';
}
function row(a){
  var open=!!S.open[a.id], why=D.causeText(a.status,a.cause), pt=D.partialText(a);
  return '<article class="bi-row '+a.status+(open?" open":"")+'" data-id="'+esc(a.id)+'">'+
    '<button type="button" class="bi-head" aria-expanded="'+open+'">'+pill(a)+
      '<span class="bi-name"><b>'+esc(a.name)+'</b><span class="bi-sub">'+(a.campaigns.length?a.campaigns.length+" brand campaign"+(a.campaigns.length===1?"":"s"):"no brand campaign")+' · '+D.int(a.brand.clicks)+' clicks last week</span></span>'+
      figHtml(a)+
      '<span class="bi-delta bad">'+esc(why)+'</span>'+
      '<span class="bi-why">'+esc(pt)+'</span><span class="bi-chev" aria-hidden="true"></span></button>'+
    (open?body(a):"")+'</article>';
}
function trendHtml(a){
  var t=D.trendBars(a), cur=S.report.account.currency;
  return '<div class="bi-trend" role="img" aria-label="Brand spend, the last '+t.bars.length+' weeks">'+
    (t.band?'<span class="bs-band" style="bottom:calc(22px + (100% - 22px) * '+(t.band.low/100)+');height:calc((100% - 22px) * '+((t.band.high-t.band.low)/100)+')" title="The usual band"></span>':"")+
    t.bars.map(function(w){
      return '<span class="bi-w '+w.tone+'" title="'+esc(w.title)+'"><i style="height:'+w.height+'%"></i><em>'+(w.cost>0?esc(D.money(w.cost,cur).replace(/\.00$/,"")):"-")+'</em><small>'+esc(w.from.slice(5))+'</small></span>';
    }).join("")+'</div>'+(t.band?'<p class="bs-legend"><span class="bs-key"></span> the shaded strip is the usual band, '+esc(D.money(a.expected.low,cur))+' to '+esc(D.money(a.expected.high,cur))+' a week</p>':"");
}
function findingsHtml(a){
  if(!a.findings.length) return '<p class="pm-empty">Nothing to flag: the brand spend is inside its usual band.</p>';
  return '<ul class="bi-finds">'+a.findings.map(function(f){
    var l=D.levelInfo(f.level);
    return '<li><span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span><div><b>'+esc(f.title)+'</b><p>'+esc(f.detail)+'</p><p class="bi-next">'+esc(f.nextStep)+'</p></div></li>';
  }).join("")+'</ul>';
}
function campaignsHtml(a){
  var cur=S.report.account.currency, h="";
  if(a.campaigns.length){
    h+='<h4>Brand campaigns</h4><div class="bi-tbl"><table><thead><tr><th>Campaign</th><th class="r">Last week</th><th class="r">Usual week</th><th class="r">Change</th><th class="r">Clicks</th><th class="r">Cost per click</th><th>Status</th><th>Bidding</th><th class="r">Daily budget</th><th></th></tr></thead><tbody>'+
      a.campaigns.map(function(c){
        return '<tr><td>'+esc(c.name)+(c.byOverride?' <span class="bi-mk">marked by you</span>':"")+(c.state==="new"?' <span class="bi-mk">new</span>':"")+'</td><td class="r">'+esc(D.money(c.cost,cur))+'</td><td class="r">'+(c.usual==null?"n/a":esc(D.money(c.usual,cur)))+
          '</td><td class="r">'+(c.diff==null?"n/a":esc((c.diff>0?"+":c.diff<0?"-":"")+D.money(Math.abs(c.diff),cur)))+'</td><td class="r">'+D.int(c.clicks)+'</td><td class="r">'+(c.cpc==null?"n/a":esc(D.money(c.cpc,cur)))+
          '</td><td>'+esc((c.status||"").toLowerCase())+'</td><td>'+esc((c.strategy||"").replace(/_/g," ").toLowerCase())+'</td><td class="r">'+(c.budget==null?"n/a":esc(D.money(c.budget,cur)))+
          '</td><td class="r"><button type="button" class="pm-btn-ghost bi-mark" data-mark="'+esc(c.id)+'" data-to="not" data-acct="'+esc(a.id)+'">Do not count</button></td></tr>';
      }).join("")+'</tbody></table></div>';
  }
  if(a.otherCampaigns.length){
    h+='<h4>Other Search campaigns that spent <span class="dim">('+a.otherCampaigns.length+')</span></h4><div class="bi-tbl"><table><tbody>'+a.otherCampaigns.map(function(c){
      return '<tr><td>'+esc(c.name)+(c.byOverride?' <span class="bi-mk">marked by you</span>':"")+'</td><td class="r"><button type="button" class="pm-btn-ghost bi-mark" data-mark="'+esc(c.id)+'" data-to="brand" data-acct="'+esc(a.id)+'">Count as brand</button></td></tr>';
    }).join("")+'</tbody></table></div>';
  }
  return h;
}
function planHtml(a){
  var p=a.plan||{}, steps=p.steps||[];
  if(!steps.length) return "";
  return '<div class="bi-plan"><h4>Fix plan</h4>'+(p.note?'<p class="bi-plan-note">'+esc(p.note)+'</p>':"")+
    '<ol class="bi-steps">'+steps.map(function(s,i){
      var l=D.levelInfo(s.level);
      return '<li><details'+(i===0?' open':'')+'><summary><span class="bi-sn">'+s.n+'</span><span class="bi-st"><b>'+esc(s.title)+'</b></span>'+
        '<span class="bi-lv '+l.tone+'">'+esc(l.label)+'</span></summary>'+
        '<div class="bi-sb"><p class="bi-why-t"><b>Why this applies.</b> '+esc(s.why)+'</p><p class="bi-how-h">What to do</p><ol class="bi-how">'+s.how.map(function(h){return '<li>'+esc(h)+'</li>';}).join("")+'</ol>'+
        (s.worth?'<p class="bs-worth"><b>Worth.</b> '+esc(s.worth)+'</p>':"")+
        (s.campaigns&&s.campaigns.length?'<p class="bi-on">Applies to: '+s.campaigns.map(function(c){return '<span class="bi-chip">'+esc(c)+'</span>';}).join(" ")+'</p>':"")+'</div></details></li>';
    }).join("")+'</ol></div>';
}
function body(a){
  return '<div class="bi-body"><div class="bi-cols"><div><h4>Brand spend by week</h4>'+trendHtml(a)+'</div><div><h4>What it means</h4>'+findingsHtml(a)+'</div></div>'+planHtml(a)+campaignsHtml(a)+'</div>';
}
$("#list").addEventListener("click",function(e){
  var mk=e.target.closest("[data-mark]");
  if(mk){
    var a=S.report.accounts.filter(function(x){return x.id===mk.dataset.acct;})[0]; if(!a) return;
    var c=a.campaigns.concat(a.otherCampaigns).filter(function(x){return x.id===mk.dataset.mark;})[0]; if(!c) return;
    S.over=D.mark(S.over,c,mk.dataset.to==="brand"); saveOver(); run(); return;
  }
  var head=e.target.closest(".bi-head"); if(head){
    var art=head.closest(".bi-row"), id=art.dataset.id; S.open[id]=!S.open[id]; paintList();
  }
});

/* ── The limit, Refresh and CSV ───────────────────────────── */
var limEl=$("#limit"), saved=store("bs_limit");
if(saved&&/^\d+$/.test(saved)&&+saved>=10&&+saved<=90){S.limit=saved; limEl.value=saved;}
limEl.addEventListener("change",function(){
  var v=String(this.value||"").trim();
  if(!/^\d+$/.test(v)||+v<10||+v>90){setMsg("The limit is a whole percentage from 10 to 90.");this.value=S.limit;return;}
  S.limit=v; store("bs_limit",v); run();
});
function csv(v){if(v==null)v="";var num=typeof v==="number";v=String(v);if(!num&&/^[=+\-@\t\r]/.test(v))v="'"+v;return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}   /* a text cell must never run as a formula in Excel; a number is left alone */
$("#refreshBtn").addEventListener("click",function(){run(true);});
$("#exportBtn").addEventListener("click",function(){
  var rep=S.report; if(!rep) return;
  var blob=new Blob(["﻿"+D.csvRows(rep).map(function(l){return l.map(csv).join(",");}).join("\r\n")],{type:"text/csv;charset=utf-8"});
  var a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="brand-spend-"+rep.account.name.replace(/[^A-Za-z0-9]+/g,"-").replace(/^-|-$/g,"")+"-"+rep.window.latestTo+".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);
});

/* ── Start ────────────────────────────────────────────────── */
(function start(){
  var want=new URLSearchParams(location.search).get("account")||store("bs_account")||"";
  S.account=want;
  picker.set([{value:"",label:"Loading accounts…",sub:"",avatar:"…",hue:200}],"",true);
  getJson("/p2/paid-marketing/api/segments/accounts").then(function(r){
    if(!r.ok){picker.set([{value:"",label:"Accounts unavailable",sub:"",avatar:"!",hue:0}],"",true);setMsg(esc((r.j&&r.j.error)||"The account list could not be read."));return;}
    S.over=loadOver(); setAccounts(r.j.accounts||[],r.j.groups||[]); run();
  }).catch(function(){setMsg("The server could not be reached. Try again.");});
})();
})();
