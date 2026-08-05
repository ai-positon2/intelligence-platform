/* Company & People Intelligence: search, enrich modal, chat. */
(function(){
"use strict";

var SEARCH_URL = window.__CPI_SEARCH_URL__;
var ENRICH_URL = window.__CPI_ENRICH_URL__;
var CHAT_URL   = window.__CPI_CHAT_URL__;

var STATE = { entity: "people", page: 1, results: [] };
var CHAT_HISTORY = [];
/* The last real question the user typed, replayed verbatim when they pick a
   company from a disambiguation list so the original role/title is not lost. */
var LAST_QUESTION = "";
/* The company resolved on a previous turn. Sent back on each subsequent turn so
   a follow-up ("and their CFO?") does not re-run the same ambiguous name and ask
   the user to choose all over again, once per turn, at a credit a time. */
var ACTIVE_COMPANY = null;

function esc(s){ return String(s==null?"":s).replace(/[&<>"'`]/g, function(c){ return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;","`":"&#96;"}[c]; }); }
/* Apollo-supplied URLs go into href/src. esc() stops attribute breakout but not
   a "javascript:" scheme, so anything linkable is allowlisted to http(s) first. */
function safeUrl(u){ u=String(u==null?"":u).trim(); return /^https?:\/\//i.test(u) ? u : ""; }
function initials(n){ n=(n||"").trim(); if(!n) return "?"; var p=n.split(/\s+/); return ((p[0][0]||"")+(p[1]?p[1][0]:"")).toUpperCase(); }
function pmNum(n){ if(n===null||n===undefined||n==="") return ""; if(isNaN(+n)) return String(n); n=+n||0; if(n>=1e9) return (n/1e9).toFixed(1).replace(/\.0$/,"")+"B"; if(n>=1e6) return (n/1e6).toFixed(1).replace(/\.0$/,"")+"M"; if(n>=1e3) return (n/1e3).toFixed(n>=1e4?0:1).replace(/\.0$/,"")+"K"; return String(n); }
function pmMon(s){ var m=String(s||"").match(/^(\d{4})-(\d{2})/); if(!m) return String(s||""); return ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m[2]-1]+" "+m[1]; }

var SVG_LI='<svg viewBox="0 0 24 24"><path d="M4.98 3.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM2 21h6V9H2v12zm7.5 0h5.7v-6.4c0-3.4 4.3-3.7 4.3 0V21H22v-7.7c0-6.1-6.6-5.9-8.8-2.9V9H9.5v12z"/></svg>';
var SVG_WEB='<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 100 20 10 10 0 000-20zm6.9 6h-2.9a15 15 0 00-1.3-3.6A8 8 0 0118.9 8zM12 4.1c.6.9 1.3 2.2 1.7 3.9h-3.4c.4-1.7 1.1-3 1.7-3.9zM4.3 14A8 8 0 014 12c0-.7.1-1.4.3-2h3.3a17 17 0 000 4H4.3zm.8 2H8a15 15 0 001.3 3.6A8 8 0 015.1 16zm2.9-8H5.1a8 8 0 014.2-3.6A15 15 0 008 8zm4 11.9c-.6-.9-1.3-2.2-1.7-3.9h3.4c-.4 1.7-1.1 3-1.7 3.9zm2.1-5.9H9.9a15 15 0 010-4h4.2a15 15 0 010 4zm.6 5.6A15 15 0 0016 16h2.9a8 8 0 01-4.2 3.6zm1.7-5.6a17 17 0 000-4h3.3c.2.6.3 1.3.3 2s-.1 1.4-.3 2h-3.3z"/></svg>';
var SVG_MAIL='<svg viewBox="0 0 24 24"><path d="M3 5h18a1 1 0 011 1v12a1 1 0 01-1 1H3a1 1 0 01-1-1V6a1 1 0 011-1zm9 7.1L4.3 7H19.7L12 12.1zM4 9.2V17h16V9.2l-8 5.3-8-5.3z"/></svg>';
var SVG_PH='<svg viewBox="0 0 24 24"><path d="M6.6 2h3.1c.5 0 .9.3 1 .8l1 4a1 1 0 01-.3 1l-1.9 1.6a13 13 0 005.1 5.1l1.6-1.9a1 1 0 011-.3l4 1c.5.1.8.5.8 1v3.1c0 1.4-1.2 2.6-2.6 2.6C11.5 20 4 12.5 4 4.6 4 3.2 5.2 2 6.6 2z"/></svg>';
var SVG_CP='<svg viewBox="0 0 24 24"><path d="M9 2h9a1 1 0 011 1v13h-2V4H9V2zM5 6h9a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1zm1 2v12h7V8H6z"/></svg>';
var SVG_OK='<svg viewBox="0 0 24 24"><path d="M9.6 17.2l-4.8-4.8 1.7-1.7 3.1 3.1 7.9-7.9 1.7 1.7-9.6 9.6z"/></svg>';

/* ── Entity toggle + filters ── */
window.cpiSetEntity = function(entity){
  STATE.entity = entity;
  document.querySelectorAll("#cpiEntityToggle button").forEach(function(b){
    b.classList.toggle("on", b.getAttribute("data-entity")===entity);
  });
  document.getElementById("cpiFiltersPeople").style.display = entity==="people" ? "" : "none";
  document.getElementById("cpiFiltersCompanies").style.display = entity==="companies" ? "" : "none";
};

window.cpiToggleChip = function(el){ el.classList.toggle("on"); };

window.cpiClearFilters = function(){
  ["fpTitles","fpCompanyDomain","fpLocation","fpKeywords","fcName","fcDomain","fcLocation","fcIndustry"].forEach(function(id){
    var el=document.getElementById(id); if(el) el.value="";
  });
  ["fpEmpRange","fcEmpRange"].forEach(function(id){ var el=document.getElementById(id); if(el) el.value=""; });
  document.querySelectorAll("#fpSeniority .cpi-chip.on").forEach(function(c){ c.classList.remove("on"); });
};

function splitCsv(v){ return (v||"").split(",").map(function(s){return s.trim();}).filter(Boolean); }

function gatherFilters(){
  if(STATE.entity==="people"){
    var seniorities=[];
    document.querySelectorAll("#fpSeniority .cpi-chip.on").forEach(function(c){ seniorities.push(c.getAttribute("data-val")); });
    var emp=(document.getElementById("fpEmpRange").value||"").split(",");
    var f={ titles: splitCsv(document.getElementById("fpTitles").value) };
    if(seniorities.length) f.seniorities=seniorities;
    var dom=document.getElementById("fpCompanyDomain").value.trim();
    if(dom) f.company_domains=[dom];
    var loc=document.getElementById("fpLocation").value.trim();
    if(loc) f.person_locations=[loc];
    var kw=document.getElementById("fpKeywords").value.trim();
    if(kw) f.keywords=kw;
    if(emp[0]){ f.employee_min=+emp[0]; f.employee_max=emp[1]?+emp[1]:999999999; }
    return f;
  }
  var emp2=(document.getElementById("fcEmpRange").value||"").split(",");
  var f2={};
  var name=document.getElementById("fcName").value.trim(); if(name) f2.name=name;
  var domain=document.getElementById("fcDomain").value.trim(); if(domain) f2.domains=[domain];
  var loc2=document.getElementById("fcLocation").value.trim(); if(loc2) f2.locations=[loc2];
  var ind=document.getElementById("fcIndustry").value.trim(); if(ind) f2.industries=splitCsv(ind);
  if(emp2[0]){ f2.employee_min=+emp2[0]; f2.employee_max=emp2[1]?+emp2[1]:999999999; }
  return f2;
}

window.cpiRunSearch = function(reset){
  if(reset){ STATE.page=1; STATE.results=[]; }
  var wrap=document.getElementById("cpiResultsWrap");
  var btn=document.getElementById(STATE.entity==="people"?"cpiSearchBtn":"cpiSearchBtnCo");
  if(btn){ btn.disabled=true; btn.textContent="Searching…"; }
  if(reset){
    wrap.innerHTML='<div class="cpi-loading"><div class="sp"></div><span>Asking Apollo…</span></div>';
  }
  fetch(SEARCH_URL, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ entity: STATE.entity, filters: gatherFilters(), page: STATE.page })
  }).then(function(r){ return r.json(); }).then(function(d){
    if(btn){ btn.disabled=false; btn.textContent="Search"; }
    var items=(d&&d.results)||[];
    /* Advance only when a page actually came back, so Load more fetches the NEXT
       page instead of re-fetching page 1 and appending duplicate cards (which on
       the Companies tab also spent a fresh Apollo credit per click). */
    if(items.length){ STATE.page = (STATE.page||1) + 1; }
    STATE.results = reset ? items : STATE.results.concat(items);
    renderResults();
    document.getElementById("cpiLoadMore").style.display=(d&&d.has_more)?"":"none";
  }).catch(function(){
    if(btn){ btn.disabled=false; btn.textContent="Search"; }
    wrap.innerHTML='<div class="cpi-empty"><span>Search failed. Try again in a moment.</span></div>';
  });
};

function renderResults(){
  var wrap=document.getElementById("cpiResultsWrap");
  if(!STATE.results.length){
    wrap.innerHTML='<div class="cpi-empty"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/></svg><span>No matches. Try widening the filters.</span></div>';
    return;
  }
  var html='<div class="cpi-grid">'+STATE.results.map(function(r,i){
    return STATE.entity==="people" ? personCard(r,i) : companyCard(r,i);
  }).join("")+"</div>";
  wrap.innerHTML=html;
}

function personCard(p,i){
  var loc=[p.city,p.state,p.country].filter(Boolean).join(", ");
  return '<div class="cpi-card" data-spotlight>'+
    '<div class="cpi-card-top">'+
      '<div class="cpi-avatar">'+esc(initials(p.full_name))+'</div>'+
      '<div style="min-width:0"><div class="cpi-card-name">'+esc(p.full_name||"Unknown")+'</div>'+
      '<div class="cpi-card-sub">'+esc(p.title||"")+'</div></div>'+
    '</div>'+
    '<div class="cpi-card-meta">'+
      (p.organization_name?'<span class="cpi-meta-chip">'+esc(p.organization_name)+'</span>':'')+
      (p.seniority?'<span class="cpi-meta-chip">'+esc(p.seniority)+'</span>':'')+
      (loc?'<span class="cpi-meta-chip">'+esc(loc)+'</span>':'')+
    '</div>'+
    '<div class="cpi-card-footer">'+
      (p.linkedin_url?'<a class="cpi-card-link" href="'+esc(safeUrl(p.linkedin_url))+'" target="_blank" rel="noopener noreferrer" title="LinkedIn">'+SVG_LI+'</a>':'')+
      '<button class="cpi-enrich-btn" onclick=\'cpiOpenEnrich("person",'+i+')\'>Enrich &rarr;</button>'+
    '</div></div>';
}

function companyCard(c,i){
  var loc=[c.city,c.state,c.country].filter(Boolean).join(", ");
  var logo=c.logo_url?('<img src="'+esc(safeUrl(c.logo_url))+'" alt="" onerror="this.style.display=\'none\'">'):esc(initials(c.name));
  return '<div class="cpi-card" data-spotlight>'+
    '<div class="cpi-card-top">'+
      '<div class="cpi-avatar co">'+logo+'</div>'+
      '<div style="min-width:0"><div class="cpi-card-name">'+esc(c.name||"Unknown")+'</div>'+
      '<div class="cpi-card-sub">'+esc(c.primary_domain||"")+'</div></div>'+
    '</div>'+
    '<div class="cpi-card-meta">'+
      (c.estimated_num_employees?'<span class="cpi-meta-chip">'+pmNum(c.estimated_num_employees)+' employees</span>':'')+
      (c.industry?'<span class="cpi-meta-chip">'+esc(c.industry)+'</span>':'')+
      (loc?'<span class="cpi-meta-chip">'+esc(loc)+'</span>':'')+
    '</div>'+
    '<div class="cpi-card-footer">'+
      (c.website_url?'<a class="cpi-card-link" href="'+esc(safeUrl(c.website_url))+'" target="_blank" rel="noopener noreferrer" title="Website">'+SVG_WEB+'</a>':'')+
      '<button class="cpi-enrich-btn" onclick=\'cpiOpenEnrich("company",'+i+')\'>Enrich &rarr;</button>'+
    '</div></div>';
}

/* ── Enrich modal ── */
function pmKV(label,val,isLink){
  if(val===null||val===undefined||val==="") return "";
  var v=isLink?('<a href="'+esc(val)+'" target="_blank" rel="noopener noreferrer">'+esc(String(val).replace(/^https?:\/\/(www\.)?/,""))+'</a>'):esc(val);
  return '<div class="pm-kv-i"><span>'+esc(label)+'</span><b>'+v+'</b></div>';
}
function pmSo(url,svg,label,isCo,tip){
  if(!url) return "";
  var txt=isCo?('<em>'+esc(label)+'</em>'):esc(label);
  return '<a class="pm-so'+(isCo?' co':'')+'" href="'+esc(url)+'" target="_blank" rel="noopener noreferrer" title="'+esc(tip||label)+'">'+svg+'<span>'+txt+'</span></a>';
}
function pmCt(icon,label,value,href,badge){
  if(!value) return "";
  var v=href?('<a href="'+esc(href)+'">'+esc(value)+'</a>'):esc(value);
  return '<div class="pm-ct-i"><div class="pm-ct-ic">'+icon+'</div><div class="pm-ct-b">'+
    '<div class="pm-ct-l"><span>'+esc(label)+'</span>'+(badge||"")+'</div>'+
    '<div class="pm-ct-v">'+v+'</div></div>'+
    '<button class="pm-cp" data-v="'+esc(value)+'" onclick="cpiCopy(this)" title="Copy" aria-label="Copy '+esc(label)+'">'+SVG_CP+'</button></div>';
}
window.cpiCopy = function(btn){
  var v=btn.getAttribute("data-v")||"";
  var done=function(){ btn.classList.add("ok"); btn.innerHTML=SVG_OK; setTimeout(function(){ btn.classList.remove("ok"); btn.innerHTML=SVG_CP; },1400); };
  if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(v).then(done).catch(function(){}); }
};
function pmCompanyCard(c,tag){
  var logo=c.logo?('<img src="'+esc(safeUrl(c.logo))+'" alt="" onerror="this.style.display=\'none\';this.parentNode.textContent=\''+esc((c.name[0]||"?").toUpperCase())+'\'">'):esc((c.name[0]||"?").toUpperCase());
  return '<div class="pm-sec"><h4 class="pm-h4">Company<s>'+esc(tag||c.domain||"")+'</s></h4>'+
    '<div class="pm-cocard"><div class="pm-cotop"><div class="pm-colg">'+logo+'</div>'+
      '<div style="min-width:0"><div class="pm-coname">'+esc(c.name)+'</div>'+
      (c.industry?'<div class="pm-coind">'+esc(c.industry)+'</div>':"")+"</div></div>"+
      (c.description?'<div style="font-size:12.3px;color:#98a3c2;line-height:1.6;margin-top:12px">'+esc(c.description)+'</div>':"")+
    "</div>"+
    '<div class="pm-kv">'+
      pmKV("Employees",c.employees?pmNum(c.employees):"")+
      pmKV("Revenue",c.revenue?("$"+c.revenue):"")+
      pmKV("Founded",c.founded)+
      pmKV("HQ",c.hq)+
      pmKV("Website",c.website,true)+
    "</div>"+
    ((c.keywords&&c.keywords.length)?'<div class="pm-tags">'+c.keywords.map(function(k){return '<span class="pm-tag">'+esc(k)+'</span>';}).join("")+"</div>":"")+
  "</div>";
}

function personHero(p){
  var photo=p.photo?('<img src="'+esc(safeUrl(p.photo))+'" alt="" referrerpolicy="no-referrer" onerror="this.parentNode.textContent=\''+esc(initials(p.name))+'\'">'):esc(initials(p.name));
  var role="";
  if(p.title||(p.company&&p.company.name)){
    role='<div class="pm-role">'+esc(p.title||"")+((p.title&&p.company&&p.company.name)?" at ":"")+((p.company&&p.company.name)?"<b>"+esc(p.company.name)+"</b>":"")+"</div>";
  }
  var chips="";
  if(p.seniority) chips+='<span class="pm-chip ac">'+esc(p.seniority)+"</span>";
  if(p.location) chips+='<span class="pm-chip">&#128205; '+esc(p.location)+"</span>";
  if(p.company&&p.company.employees) chips+='<span class="pm-chip">&#128101; '+pmNum(p.company.employees)+" employees</span>";
  var co=p.company||{};
  var so=pmSo(p.linkedin,SVG_LI,"LinkedIn")+pmSo(co.linkedin,SVG_LI,"Company LinkedIn",true,co.name)+pmSo(co.website,SVG_WEB,"Company site",true,co.domain);
  return '<div class="pm-hero-in"><div class="pm-avw"><div class="pm-av">'+photo+"</div></div>"+
    '<div class="pm-id"><h3 class="pm-name">'+esc(p.name||"Unknown")+"</h3>"+role+
    (p.headline?'<div class="pm-head">'+esc(p.headline)+"</div>":"")+
    (chips?'<div class="pm-chips">'+chips+"</div>":"")+
    (so?'<div class="pm-socials">'+so+"</div>":"")+
    "</div></div>";
}

function personBody(p){
  if(!p||!p.matched){
    return '<div class="pm-sec"><h4 class="pm-h4">Profile<s>apollo</s></h4><div class="pm-empty"><b>No match found</b>Apollo has no full profile for this person beyond what the search already returned.</div></div>';
  }
  var emails=(p.emails&&p.emails.length)?p.emails:[];
  var phones=p.phones||[];
  var ct=emails.map(function(e){
    var badge=e.verified?'<span class="pm-vf">verified</span>':(e.status?'<span class="pm-vf" style="color:#9aa5c6;border-color:rgba(255,255,255,.16);background:rgba(255,255,255,.05)">'+esc(e.status)+"</span>":"");
    return pmCt(SVG_MAIL,e.primary?"Email":"Other email",e.email,"mailto:"+e.email,badge);
  }).join("")+phones.map(function(ph){
    var lbl=(ph.owner==="company")?"Company phone":(ph.label||"Phone");
    return pmCt(SVG_PH,lbl,ph.number,"tel:"+String(ph.number).replace(/[^\d+]/g,""),"");
  }).join("");
  var out="";
  if(ct){
    out+='<div class="pm-sec"><h4 class="pm-h4">Contact<s>'+(emails.length+phones.length)+"</s></h4>"+'<div class="pm-ct">'+ct+"</div></div>";
  } else {
    out+='<div class="pm-sec"><h4 class="pm-h4">Contact<s>apollo</s></h4><div class="pm-ct-no"><b>No contact details on file</b>Apollo only hands back verified emails/phones for people already in the connected CRM.</div></div>';
  }
  out+='<div class="pm-sec"><h4 class="pm-h4">Professional profile<s>apollo</s></h4><div class="pm-kv">'+
    pmKV("Title",p.title)+pmKV("Seniority",p.seniority)+
    pmKV("Departments",(p.departments||[]).join(", "))+pmKV("Functions",(p.functions||[]).join(", "))+
    pmKV("Location",p.location)+pmKV("Time zone",p.time_zone)+
  "</div></div>";
  var c=p.company||{};
  if(c.name) out+=pmCompanyCard(c,c.domain);
  if(p.history&&p.history.length){
    out+='<div class="pm-sec"><h4 class="pm-h4">Career history<s>'+p.history.length+(p.history.length===1?" role":" roles")+"</s></h4>"+
      p.history.map(function(h){
        var when=h.current?((h.start?pmMon(h.start):"")+" &rarr; now"):((h.start?pmMon(h.start):"?")+(h.end?(" &rarr; "+pmMon(h.end)):""));
        return '<div class="pm-job'+(h.current?" cur":"")+'"><div class="pm-job-d"></div><div>'+
          '<div class="pm-job-t">'+esc(h.title||"Role not specified")+"</div>"+
          (h.org?'<div class="pm-job-o">'+esc(h.org)+"</div>":"")+
          '<div class="pm-job-w">'+esc(when)+"</div></div></div>";
      }).join("")+"</div>";
  }
  return out;
}

function companyHero(c){
  var logo=c.logo?('<img src="'+esc(safeUrl(c.logo))+'" alt="" onerror="this.parentNode.textContent=\''+esc(initials(c.name))+'\'">'):esc(initials(c.name));
  var chips="";
  if(c.employees) chips+='<span class="pm-chip ac">'+pmNum(c.employees)+" employees</span>";
  if(c.hq) chips+='<span class="pm-chip">&#128205; '+esc(c.hq)+"</span>";
  if(c.revenue) chips+='<span class="pm-chip">$'+esc(c.revenue)+" revenue</span>";
  var so=pmSo(c.linkedin,SVG_LI,"LinkedIn")+pmSo(c.website,SVG_WEB,"Website");
  return '<div class="pm-hero-in"><div class="pm-avw"><div class="pm-av" style="border-radius:16px;background:#fff;color:#0b1020">'+logo+"</div></div>"+
    '<div class="pm-id"><h3 class="pm-name">'+esc(c.name||"Unknown")+"</h3>"+
    (c.industry?'<div class="pm-role">'+esc(c.industry)+"</div>":"")+
    (c.description?'<div class="pm-head">'+esc(c.description)+"</div>":"")+
    (chips?'<div class="pm-chips">'+chips+"</div>":"")+
    (so?'<div class="pm-socials">'+so+"</div>":"")+
    "</div></div>";
}

function companyBody(c){
  if(!c||!c.matched){
    return '<div class="pm-sec"><h4 class="pm-h4">Profile<s>apollo</s></h4><div class="pm-empty"><b>No match found</b>Apollo has no organization record for this company.</div></div>';
  }
  var out='<div class="pm-sec"><h4 class="pm-h4">Firmographics<s>apollo</s></h4><div class="pm-kv">'+
    pmKV("Founded",c.founded)+pmKV("Phone",c.phone)+pmKV("HQ",c.hq)+pmKV("Website",c.website,true)+
  "</div>"+
  ((c.keywords&&c.keywords.length)?'<div class="pm-tags">'+c.keywords.map(function(k){return '<span class="pm-tag">'+esc(k)+"</span>";}).join("")+"</div>":"")+
  "</div>";
  if(c.leadership&&c.leadership.length){
    out+='<div class="pm-sec"><h4 class="pm-h4">Key people<s>'+c.leadership.length+"</s></h4>"+
      c.leadership.map(function(p){
        return '<div class="pm-ct-i"><div class="pm-ct-ic">'+esc(initials(p.full_name))+"</div>"+
          '<div class="pm-ct-b"><div class="pm-ct-l"><span>'+esc(p.title||"")+"</span></div>"+
          '<div class="pm-ct-v">'+esc(p.full_name||"")+"</div></div>"+
          (p.linkedin_url?'<a class="pm-cp" href="'+esc(safeUrl(p.linkedin_url))+'" target="_blank" rel="noopener noreferrer" title="LinkedIn">'+SVG_LI+"</a>":"")+
        "</div>";
      }).join("")+"</div>";
  }
  return out;
}

window.cpiOpenEnrich = function(type, idx){
  var item=STATE.results[idx]; if(!item) return;
  document.getElementById("pmOvl").classList.add("on");
  document.getElementById("pmWrap").classList.add("on");
  document.body.style.overflow="hidden";
  document.getElementById("pmHero").innerHTML = type==="person" ? personHero({name:item.full_name,title:item.title}) : companyHero({name:item.name,logo:item.logo_url});
  document.getElementById("pmBody").innerHTML = '<div class="pm-sk" style="width:40%;height:11px;margin-bottom:14px"></div><div class="pm-sk" style="width:100%;height:52px;margin-bottom:9px"></div><div class="pm-sk" style="width:100%;height:52px"></div>';

  var body = type==="person"
    ? { type:"person", name: item.full_name, domain: item.organization_domain, apollo_id: item.id }
    : { type:"company", domain: item.primary_domain, apollo_id: item.id };

  fetch(ENRICH_URL, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body) })
    .then(function(r){ return r.json(); })
    .then(function(d){
      var p=(d&&d.profile)||{};
      document.getElementById("pmHero").innerHTML = type==="person" ? personHero(p) : companyHero(p);
      document.getElementById("pmBody").innerHTML = type==="person" ? personBody(p) : companyBody(p);
    })
    .catch(function(){
      document.getElementById("pmBody").innerHTML='<div class="pm-empty"><b>Enrichment failed</b>Could not reach Apollo just now. Try again in a moment.</div>';
    });
};
window.cpiCloseModal = function(){
  document.getElementById("pmOvl").classList.remove("on");
  document.getElementById("pmWrap").classList.remove("on");
  document.body.style.overflow="";
};
document.addEventListener("keydown", function(e){ if(e.key==="Escape") window.cpiCloseModal(); });

/* ── Chat ── */
function chatScroll(){ var b=document.getElementById("cpiChatBody"); b.scrollTop=b.scrollHeight; }
function addUserMsg(text){
  CHAT_HISTORY.push({role:"user", content:text});
  var b=document.getElementById("cpiChatBody");
  b.insertAdjacentHTML("beforeend", '<div class="cpi-msg user"><div class="cpi-msg-av">'+esc(initials((window.__CPI_USER_NAME__||"U")))+'</div><div class="cpi-bub"><p>'+esc(text)+"</p></div></div>");
  chatScroll();
}
function addTyping(){
  var b=document.getElementById("cpiChatBody");
  b.insertAdjacentHTML("beforeend", '<div class="cpi-msg assistant" id="cpiTyping"><div class="cpi-msg-av">AI</div><div class="cpi-bub"><div class="cpi-typing"><i></i><i></i><i></i></div></div></div>');
  chatScroll();
}
function removeTyping(){ var t=document.getElementById("cpiTyping"); if(t) t.remove(); }
function addAssistantMsg(answer, choices){
  CHAT_HISTORY.push({role:"assistant", content:answer||""});
  var b=document.getElementById("cpiChatBody");
  var choicesHtml="";
  if(choices&&choices.length){
    choicesHtml='<div class="cpi-choices">'+choices.map(function(c,i){
      var logo=c.logo?('<img src="'+esc(safeUrl(c.logo))+'" alt="">'):esc(initials(c.name));
      /* Values ride in data-* attributes and are wired up by a real event
         listener below, rather than being interpolated into an inline onclick
         string, so a company name containing a quote or apostrophe cannot
         break the handler. */
      return '<button class="cpi-choice" data-pick-name="'+esc(c.name||"")+'" data-pick-domain="'+esc(c.domain||"")+'" data-pick-org-id="'+esc(c.id||"")+'" data-pick-question="'+esc(LAST_QUESTION||"")+'">'+
        '<div class="cpi-choice-logo">'+logo+"</div>"+
        '<div class="cpi-choice-t"><b>'+esc(c.name)+"</b><span>"+esc([c.domain,c.hq].filter(Boolean).join(" · "))+"</span></div>"+
      "</button>";
    }).join("")+"</div>";
  }
  b.insertAdjacentHTML("beforeend", '<div class="cpi-msg assistant"><div class="cpi-msg-av">AI</div><div class="cpi-bub"><p>'+esc(answer||"I could not find an answer for that.").replace(/\n/g,"<br>")+"</p>"+choicesHtml+"</div></div>");
  var justAdded=b.lastElementChild;
  if(justAdded){
    justAdded.querySelectorAll(".cpi-choice").forEach(function(btn){
      btn.addEventListener("click", function(){
        /* The question is read off the button, not off LAST_QUESTION, so
           clicking a choice from an older message still answers the question
           that produced THAT list rather than whatever was typed since. */
        window.cpiPickChoice(btn.getAttribute("data-pick-name")||"",
                             btn.getAttribute("data-pick-domain")||"",
                             btn.getAttribute("data-pick-question")||"",
                             btn.getAttribute("data-pick-org-id")||"");
      });
    });
  }
  chatScroll();
}
/* Picking a company from a disambiguation list re-asks the ORIGINAL question
   (so the role/title being asked about is preserved verbatim) and passes the
   chosen company as a structured domain. Sending "I mean Acme (acme.com)" as
   free text instead would go back through the intent parser as a company NAME
   containing a domain, which resolves to nothing. */
window.cpiPickChoice = function(name, domain, question, orgId){
  var q = question || LAST_QUESTION || ("Tell me about " + name);
  sendChat(q, domain, name, orgId);
};

window.cpiSendChat = function(){
  var input=document.getElementById("cpiChatInput");
  var text=(input.value||"").trim();
  if(!text) return;
  input.value="";
  sendChat(text, "", "", "");
};

function sendChat(text, selectedDomain, selectedName, selectedOrgId){
  var sendBtn=document.getElementById("cpiChatSend");
  if(sendBtn.disabled) return;          /* a request is already in flight */
  sendBtn.disabled=true;
  /* Retire every on-screen choice button for the duration of the request, so
     double-clicking two different companies cannot fire two lookups (two
     credits, two contradictory answers appended to one transcript). */
  document.querySelectorAll(".cpi-choice").forEach(function(b){ b.disabled=true; });
  var isPick = !!(selectedDomain || selectedOrgId);
  if(!isPick){ LAST_QUESTION = text; }
  /* Show the company they picked, not the replayed question, so the transcript
     reads the way the conversation actually went. Name AND domain, since the
     whole point of the pick was to tell two same-named companies apart. */
  addUserMsg(isPick
    ? (selectedName ? (selectedDomain ? selectedName + " (" + selectedDomain + ")" : selectedName)
                    : selectedDomain)
    : text);
  addTyping();
  fetch(CHAT_URL, { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({
      message: text,
      selected_domain: selectedDomain || "",
      selected_org_id: selectedOrgId || "",
      selected_name: selectedName || "",
      context_org_id: (ACTIVE_COMPANY && ACTIVE_COMPANY.org_id) || "",
      context_domain: (ACTIVE_COMPANY && ACTIVE_COMPANY.domain) || "",
      context_name: (ACTIVE_COMPANY && ACTIVE_COMPANY.name) || "",
      history: CHAT_HISTORY.slice(0,-1)
    }) })
    .then(function(r){ return r.json(); })
    .then(function(d){
      removeTyping(); sendBtn.disabled=false;
      /* Pin whatever company the server actually resolved, so the next turn
         inherits it instead of re-disambiguating. */
      if(d && d.context && d.context.org_id){ ACTIVE_COMPANY = d.context; }
      addAssistantMsg(d&&d.answer, d&&d.choices);
    })
    .catch(function(){
      removeTyping(); sendBtn.disabled=false;
      addAssistantMsg("Something went wrong reaching the assistant. Try again.");
    });
}

})();
