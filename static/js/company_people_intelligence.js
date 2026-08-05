/* Company & People Intelligence: search, enrich modal, chat. */
(function(){
"use strict";

var SEARCH_URL = window.__CPI_SEARCH_URL__;
var ENRICH_URL = window.__CPI_ENRICH_URL__;
var CHAT_URL   = window.__CPI_CHAT_URL__;

/* selected is keyed by Apollo id (not grid index) so a tick survives Load more,
   re-renders after a bulk enrich, and reopening a saved search. */
var STATE = { entity: "people", page: 1, results: [], selected: {},
              total: null, lastFilters: {} };
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
  var changed = STATE.entity !== entity;
  STATE.entity = entity;
  document.querySelectorAll("#cpiEntityToggle button").forEach(function(b){
    b.classList.toggle("on", b.getAttribute("data-entity")===entity);
  });
  document.getElementById("cpiFiltersPeople").style.display = entity==="people" ? "" : "none";
  document.getElementById("cpiFiltersCompanies").style.display = entity==="companies" ? "" : "none";
  /* People and company rows have different shapes and export columns, so a
     selection cannot survive the switch. Clear it rather than mixing the two. */
  if(changed){ STATE.selected={}; updateBulk(); }
};

window.cpiToggleChip = function(el){ el.classList.toggle("on"); };

window.cpiToggleAdvanced = function(panelId, btnId){
  var panel=document.getElementById(panelId), btn=document.getElementById(btnId);
  var on=!panel.classList.contains("on");
  panel.classList.toggle("on", on);
  if(btn) btn.classList.toggle("on", on);
};

/* Declarative filter specs, so a new Apollo filter is one line here plus one
   input in the template rather than another branch in a growing if-chain.
   kind: "str" (trimmed string) | "csv" (comma list -> array)
         | "one" (single value -> one-element array) | "num" (number or omit) */
var PEOPLE_FIELDS = [
  ["fpTitles","titles","csv"], ["fpCompanyDomain","company_domains","one"],
  ["fpLocation","person_locations","one"], ["fpCompanyLocation","company_locations","one"],
  ["fpKeywords","keywords","str"], ["fpLinkedinUrls","linkedin_urls","csv"],
  ["fpIndustry","industries","csv"], ["fpSegments","market_segments","csv"],
  ["fpNaics","naics_codes","csv"], ["fpSic","sic_codes","csv"],
  ["fpTechnologies","technologies","csv"], ["fpTechnologiesAll","technologies_all","csv"],
  ["fpTechnologiesNot","exclude_technologies","csv"],
  ["fpJobTitles","job_titles","csv"], ["fpJobLocations","job_locations","csv"],
  ["fpJobPostedAfter","job_posted_after","str"],
  ["fpRevenueMin","revenue_min","num"], ["fpRevenueMax","revenue_max","num"],
  ["fpFoundedMin","founded_min","num"], ["fpFoundedMax","founded_max","num"],
  ["fpYoeMin","yoe_min","num"], ["fpYoeMax","yoe_max","num"],
  ["fpJobsMin","num_jobs_min","num"], ["fpJobsMax","num_jobs_max","num"],
  ["fpGrowthMin","headcount_growth_min","num"], ["fpGrowthMax","headcount_growth_max","num"],
  ["fpGrowthMonths","headcount_growth_months","num"]
];
var COMPANY_FIELDS = [
  ["fcName","name","str"], ["fcDomain","domains","one"],
  ["fcLocation","locations","one"], ["fcExcludeLocation","exclude_locations","one"],
  ["fcIndustry","industries","csv"], ["fcExcludeKeywords","exclude_keywords","csv"],
  ["fcSegments","market_segments","csv"],
  ["fcNaics","naics_codes","csv"], ["fcNaicsNot","exclude_naics_codes","csv"],
  ["fcSic","sic_codes","csv"], ["fcSicNot","exclude_sic_codes","csv"],
  ["fcTechnologies","technologies","csv"], ["fcTechnologiesAll","technologies_all","csv"],
  ["fcTechnologiesNot","exclude_technologies","csv"],
  ["fcJobTitles","job_titles","csv"], ["fcJobLocations","job_locations","csv"],
  ["fcJobPostedAfter","job_posted_after","str"],
  ["fcFundedAfter","funded_after","str"], ["fcFundedBefore","funded_before","str"],
  ["fcRevenueMin","revenue_min","num"], ["fcRevenueMax","revenue_max","num"],
  ["fcFoundedMin","founded_min","num"], ["fcFoundedMax","founded_max","num"],
  ["fcTotalFundMin","total_funding_min","num"], ["fcTotalFundMax","total_funding_max","num"],
  ["fcLastFundMin","latest_funding_min","num"], ["fcLastFundMax","latest_funding_max","num"],
  ["fcJobsMin","num_jobs_min","num"], ["fcJobsMax","num_jobs_max","num"],
  ["fcGrowthMin","headcount_growth_min","num"], ["fcGrowthMax","headcount_growth_max","num"],
  ["fcGrowthMonths","headcount_growth_months","num"]
];

window.cpiClearFilters = function(){
  PEOPLE_FIELDS.concat(COMPANY_FIELDS).forEach(function(spec){
    var el=document.getElementById(spec[0]); if(el) el.value="";
  });
  ["fpEmpRange","fcEmpRange","fpDeptName","fpDeptMin","fpDeptMax","fcDeptName","fcDeptMin",
   "fcDeptMax","fpTenureMin","fpTenureMax"].forEach(function(id){
    var el=document.getElementById(id); if(el) el.value="";
  });
  var sim=document.getElementById("fpSimilarTitles"); if(sim) sim.checked=true;
  var unk=document.getElementById("fcUnknownFounded"); if(unk) unk.checked=false;
  document.querySelectorAll("#fpSeniority .cpi-chip.on, #fpEmailStatus .cpi-chip.on").forEach(function(c){ c.classList.remove("on"); });
  ["fpAdvanced","fcAdvanced"].forEach(function(id){ var el=document.getElementById(id); if(el) el.classList.remove("on"); });
  ["fpMoreBtn","fcMoreBtn"].forEach(function(id){ var el=document.getElementById(id); if(el) el.classList.remove("on"); });
};

function splitCsv(v){ return (v||"").split(",").map(function(s){return s.trim();}).filter(Boolean); }
function numVal(id){ var el=document.getElementById(id); if(!el||el.value==="") return null; var n=+el.value; return isNaN(n)?null:n; }
function chipVals(sel){ var out=[]; document.querySelectorAll(sel).forEach(function(c){ out.push(c.getAttribute("data-val")); }); return out; }

function applySpecs(specs, f){
  specs.forEach(function(spec){
    var el=document.getElementById(spec[0]);
    if(!el) return;
    var key=spec[1], kind=spec[2];
    if(kind==="num"){ var n=numVal(spec[0]); if(n!==null) f[key]=n; return; }
    var v=(el.value||"").trim();
    if(!v) return;
    if(kind==="csv"){ var list=splitCsv(v); if(list.length) f[key]=list; }
    else if(kind==="one"){ f[key]=[v]; }
    else { f[key]=v; }
  });
}

/* Employee-range <select> carries "min,max" with an open-ended top bucket. */
function applyEmpRange(id, f){
  var el=document.getElementById(id); if(!el) return;
  var parts=(el.value||"").split(",");
  if(parts[0]){ f.employee_min=+parts[0]; f.employee_max=parts[1]?+parts[1]:999999999; }
}

/* Apollo wants {dept: {min, max}}; the UI collects one dept at a time. */
function applyDeptCounts(nameId, minId, maxId, f){
  var nameEl=document.getElementById(nameId);
  if(!nameEl||!nameEl.value) return;
  var lo=numVal(minId), hi=numVal(maxId);
  if(lo===null && hi===null) return;
  var range={};
  if(lo!==null) range.min=lo;
  if(hi!==null) range.max=hi;
  f.department_counts={};
  f.department_counts[nameEl.value]=range;
}

function gatherFilters(){
  var f={};
  if(STATE.entity==="people"){
    applySpecs(PEOPLE_FIELDS, f);
    f.include_similar_titles = !!(document.getElementById("fpSimilarTitles")||{}).checked;
    if(!f.titles) f.titles=[];
    var sen=chipVals("#fpSeniority .cpi-chip.on"); if(sen.length) f.seniorities=sen;
    var em=chipVals("#fpEmailStatus .cpi-chip.on"); if(em.length) f.email_status=em;
    applyEmpRange("fpEmpRange", f);
    applyDeptCounts("fpDeptName","fpDeptMin","fpDeptMax", f);
    /* The UI asks for months in role because that is how recruiters think about
       it; Apollo's filter is in days. 30-day months, matching its own docs. */
    var tMin=numVal("fpTenureMin"), tMax=numVal("fpTenureMax");
    if(tMin!==null) f.days_in_title_min=Math.round(tMin*30);
    if(tMax!==null) f.days_in_title_max=Math.round(tMax*30);
    return f;
  }
  applySpecs(COMPANY_FIELDS, f);
  applyEmpRange("fcEmpRange", f);
  applyDeptCounts("fcDeptName","fcDeptMin","fcDeptMax", f);
  if((document.getElementById("fcUnknownFounded")||{}).checked) f.include_unknown_founded_year=true;
  return f;
}

function skeletonGrid(n){
  var one='<div class="cpi-skel-card">'+
    '<div class="cpi-skel-row"><div class="cpi-sk-b cpi-sk-av"></div>'+
      '<div style="flex:1"><div class="cpi-sk-b" style="height:13px;width:62%"></div>'+
      '<div class="cpi-sk-b" style="height:10px;width:44%;margin-top:7px"></div></div></div>'+
    '<div class="cpi-sk-b" style="height:9px;width:88%;margin-top:16px"></div>'+
    '<div class="cpi-sk-b" style="height:9px;width:70%;margin-top:8px"></div>'+
    '<div class="cpi-sk-b" style="height:9px;width:54%;margin-top:8px"></div></div>';
  return '<div class="cpi-grid">'+new Array(n+1).join(one)+'</div>';
}

window.cpiRunSearch = function(reset){
  if(reset){ STATE.page=1; STATE.results=[]; STATE.selected={}; STATE.total=null; }
  var wrap=document.getElementById("cpiResultsWrap");
  var btn=document.getElementById(STATE.entity==="people"?"cpiSearchBtn":"cpiSearchBtnCo");
  if(btn){ btn.disabled=true; btn.textContent="Searching…"; }
  var filters=gatherFilters();
  STATE.lastFilters=filters;
  if(reset){ wrap.innerHTML=skeletonGrid(6); }
  fetch(SEARCH_URL, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ entity: STATE.entity, filters: filters, page: STATE.page })
  }).then(function(r){ return r.json(); }).then(function(d){
    if(btn){ btn.disabled=false; btn.textContent="Search"; }
    if(d && d.error){ toast(d.error, "err"); }
    var items=(d&&d.results)||[];
    /* Advance only when a page actually came back, so Load more fetches the NEXT
       page instead of re-fetching page 1 and appending duplicate cards (which on
       the Companies tab also spent a fresh Apollo credit per click). */
    if(items.length){ STATE.page = (STATE.page||1) + 1; }
    if(d && d.total!==undefined && d.total!==null) STATE.total=d.total;
    STATE.results = reset ? items : STATE.results.concat(items);
    renderResults();
    document.getElementById("cpiLoadMore").style.display=(d&&d.has_more)?"":"none";
    if(items.length) saveHistory();
  }).catch(function(){
    if(btn){ btn.disabled=false; btn.textContent="Search"; }
    wrap.innerHTML='<div class="cpi-empty"><span>Search failed. Try again in a moment.</span></div>';
  });
};

function renderResults(){
  var wrap=document.getElementById("cpiResultsWrap");
  var bar=document.getElementById("cpiToolbar");
  if(!STATE.results.length){
    wrap.innerHTML='<div class="cpi-empty"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/></svg><span>No matches. Try widening the filters.</span></div>';
    if(bar) bar.style.display="none";
    updateBulk();
    return;
  }
  if(bar) bar.style.display="";
  var shown=STATE.results.length;
  var cnt=document.getElementById("cpiCount");
  if(cnt){
    cnt.innerHTML = STATE.total && STATE.total>shown
      ? "Showing <b>"+pmNum(shown)+"</b> of <b>"+pmNum(STATE.total)+"</b> <s>matches in Apollo</s>"
      : "<b>"+pmNum(shown)+"</b> <s>"+(STATE.entity==="people"?"people":"companies")+"</s>";
  }
  wrap.innerHTML='<div class="cpi-grid">'+STATE.results.map(function(r,i){
    return STATE.entity==="people" ? personCard(r,i) : companyCard(r,i);
  }).join("")+"</div>";
  updateBulk();
  syncSelectAllLabel();
}

/* Company favicon by domain. Apollo's free people search returns no logo, and a
   real mark next to every row is most of what makes the grid feel finished, so
   this falls back to the public favicon service and hides itself if that 404s.
   Same class of third-party asset call the page already makes for webfonts. */
function logoFor(domain){
  domain=String(domain||"").replace(/^https?:\/\//i,"").replace(/\/.*$/,"").trim();
  return domain ? "https://www.google.com/s2/favicons?domain="+encodeURIComponent(domain)+"&sz=64" : "";
}
function row(svg, inner){ return '<div class="cpi-row">'+svg+'<span>'+inner+'</span></div>'; }

var IC_BLD='<svg viewBox="0 0 24 24"><path d="M3 21h18M5 21V7l7-4 7 4v14M9 9h2M9 13h2M9 17h2M15 9h0M15 13h0"/></svg>';
var IC_PIN='<svg viewBox="0 0 24 24"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0116 0z"/><circle cx="12" cy="10" r="3"/></svg>';
var IC_TAG='<svg viewBox="0 0 24 24"><path d="M20.6 13.4l-7.2 7.2a2 2 0 01-2.8 0l-7.2-7.2A2 2 0 013 12V5a2 2 0 012-2h7a2 2 0 011.4.6l7.2 7.2a2 2 0 010 2.6z"/><circle cx="7.5" cy="7.5" r="1.2"/></svg>';
var IC_CLK='<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>';
var IC_HIST='<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>';
var IC_ML='<svg viewBox="0 0 24 24"><rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>';

function personCard(p,i){
  var loc=[p.city,p.state,p.country].filter(Boolean).join(", ");
  var sel=STATE.selected[p.id]?" sel":"";
  var photo=safeUrl(p.photo_url);
  var av = photo
    ? '<div class="cpi-avatar ph"><img src="'+esc(photo)+'" alt="" loading="lazy" onerror="this.parentNode.textContent=\''+esc(initials(p.full_name))+'\'"></div>'
    : '<div class="cpi-avatar">'+esc(initials(p.full_name))+'</div>';

  var rows=[];
  if(p.organization_name){
    var lg=logoFor(p.organization_domain);
    var co=(lg?'<img class="cpi-row-logo" src="'+esc(lg)+'" alt="" loading="lazy" onerror="this.style.display=\'none\'"> ':"")
      +'<b>'+esc(p.organization_name)+'</b>';
    var extra=[];
    if(p.organization_industry) extra.push(esc(p.organization_industry));
    if(p.organization_employees) extra.push(pmNum(p.organization_employees)+" emp");
    /* Falls back to the domain when Apollo gave no firmographics, so the row
       still carries a second piece of information instead of just a name. */
    if(!extra.length && p.organization_domain) extra.push("<s>"+esc(p.organization_domain)+"</s>");
    rows.push('<div class="cpi-row">'+IC_BLD+'<span>'+co+(extra.length?" · "+extra.join(" · "):"")+'</span></div>');
  }
  if(loc) rows.push(row(IC_PIN, esc(loc)));
  var tags=[];
  if(p.seniority) tags.push(esc(String(p.seniority).replace(/_/g," ")));
  (p.departments||[]).slice(0,2).forEach(function(d){ tags.push(esc(String(d).replace(/_/g," "))); });
  if(tags.length) rows.push(row(IC_TAG, tags.join(" · ")));
  if(p.email) rows.push(row(IC_ML,'<b>'+esc(p.email)+'</b>'+(p.email_status?' <span class="cpi-badge '+(p.email_status==="verified"?"ok":"dim")+'">'+esc(p.email_status.replace(/_/g," "))+'</span>':"")));
  if(p.title_start_date) rows.push(row(IC_CLK,"In role since "+esc(pmMon(p.title_start_date))));
  if((p.past_companies||[]).length) rows.push(row(IC_HIST,"Previously "+esc(p.past_companies.filter(Boolean).join(", "))));
  /* Apollo's own freshness stamp is free and is the one extra fact available on
     every row, so an otherwise-thin card still says something verifiable. */
  if(p.last_refreshed_at) rows.push(row(IC_CLK,"Apollo data refreshed <s>"+esc(pmMon(p.last_refreshed_at))+"</s>"));
  /* Names the missing fields rather than leaving dead space, and says what the
     click costs, so nobody spends a credit without knowing. */
  if(!p.enriched && !loc && !p.email){
    rows.push('<div class="cpi-row hint">'+IC_ML+'<span>Enrich for email, phone, location &amp; seniority <s>&middot; 1 credit</s></span></div>');
  }

  var socials="";
  if(p.linkedin_url) socials+='<a class="cpi-card-link" href="'+esc(safeUrl(p.linkedin_url))+'" target="_blank" rel="noopener noreferrer" title="LinkedIn">'+SVG_LI+'</a>';
  if(p.organization_domain) socials+='<a class="cpi-card-link" href="'+esc(safeUrl("https://"+String(p.organization_domain).replace(/^https?:\/\//i,"")))+'" target="_blank" rel="noopener noreferrer" title="Company website">'+SVG_WEB+'</a>';

  return '<div class="cpi-card'+sel+(p.enriched?" enr":"")+'" data-spotlight>'+
    '<button class="cpi-card-check'+(sel?" on":"")+'" onclick="cpiToggleSelect('+i+')" aria-label="Select"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg></button>'+
    '<div class="cpi-card-top">'+av+
      '<div style="min-width:0">'+
        '<div class="cpi-name-row"><div class="cpi-card-name">'+esc(p.full_name||"Unknown")+'</div>'+
          (p.name_masked?'<span class="cpi-masked" title="Apollo masks this surname on the current plan. Enrich to reveal the real name.">masked</span>':'')+
          (p.enriched?'<span class="cpi-badge ok">enriched</span>':'')+
        '</div>'+
        '<div class="cpi-card-sub">'+esc(p.title||p.headline||"")+'</div>'+
      '</div>'+
    '</div>'+
    (rows.length?'<div class="cpi-rows">'+rows.join("")+'</div>':'')+
    '<div class="cpi-card-footer">'+socials+
      '<button class="cpi-enrich-btn" onclick=\'cpiOpenEnrich("person",'+i+')\'>Enrich &rarr;</button>'+
    '</div></div>';
}

function companyCard(c,i){
  var loc=[c.city,c.state,c.country].filter(Boolean).join(", ");
  var sel=STATE.selected[c.id]?" sel":"";
  var src=safeUrl(c.logo_url)||logoFor(c.primary_domain);
  var logo=src?('<img src="'+esc(src)+'" alt="" loading="lazy" onerror="this.parentNode.textContent=\''+esc(initials(c.name))+'\'">'):esc(initials(c.name));

  var rows=[];
  var firmo=[];
  if(c.estimated_num_employees) firmo.push('<b>'+pmNum(c.estimated_num_employees)+'</b> employees');
  if(c.industry) firmo.push(esc(c.industry));
  if(c.founded_year) firmo.push("est. "+esc(c.founded_year));
  if(firmo.length) rows.push(row(IC_BLD, firmo.join(" · ")));
  if(loc) rows.push(row(IC_PIN, esc(loc)));
  var money=[];
  if(c.annual_revenue) money.push('<b>$'+pmNum(c.annual_revenue)+'</b> revenue');
  if(c.total_funding) money.push('$'+pmNum(c.total_funding)+' raised');
  if(c.publicly_traded_symbol) money.push(esc(c.publicly_traded_symbol));
  if(money.length) rows.push(row(IC_HIST, money.join(" · ")));
  if((c.technologies||[]).length) rows.push(row(IC_TAG, esc(c.technologies.slice(0,4).join(", "))));
  if(c.latest_funding_round_date) rows.push(row(IC_CLK,"Last round "+esc(pmMon(c.latest_funding_round_date))));

  var socials="";
  if(c.website_url) socials+='<a class="cpi-card-link" href="'+esc(safeUrl(c.website_url))+'" target="_blank" rel="noopener noreferrer" title="Website">'+SVG_WEB+'</a>';
  if(c.linkedin_url) socials+='<a class="cpi-card-link" href="'+esc(safeUrl(c.linkedin_url))+'" target="_blank" rel="noopener noreferrer" title="LinkedIn">'+SVG_LI+'</a>';

  return '<div class="cpi-card'+sel+'" data-spotlight>'+
    '<button class="cpi-card-check'+(sel?" on":"")+'" onclick="cpiToggleSelect('+i+')" aria-label="Select"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg></button>'+
    '<div class="cpi-card-top">'+
      '<div class="cpi-avatar co">'+logo+'</div>'+
      '<div style="min-width:0"><div class="cpi-card-name">'+esc(c.name||"Unknown")+'</div>'+
      '<div class="cpi-card-sub">'+esc(c.primary_domain||"")+'</div></div>'+
    '</div>'+
    (rows.length?'<div class="cpi-rows">'+rows.join("")+'</div>':'')+
    '<div class="cpi-card-footer">'+socials+
      '<button class="cpi-enrich-btn" onclick=\'cpiOpenEnrich("company",'+i+')\'>Enrich &rarr;</button>'+
    '</div></div>';
}

/* ── Selection, bulk enrich, export ── */
function rowKey(r){ return r && r.id; }

window.cpiToggleSelect = function(i){
  var r=STATE.results[i]; if(!r||!rowKey(r)) return;
  if(STATE.selected[r.id]) delete STATE.selected[r.id]; else STATE.selected[r.id]=r;
  renderResults();
};
window.cpiClearSelection = function(){ STATE.selected={}; renderResults(); };
window.cpiToggleSelectAll = function(){
  var all=STATE.results.every(function(r){ return !rowKey(r) || STATE.selected[r.id]; });
  if(all){ STATE.selected={}; }
  else { STATE.results.forEach(function(r){ if(rowKey(r)) STATE.selected[r.id]=r; }); }
  renderResults();
};
function selectedRows(){ return Object.keys(STATE.selected).map(function(k){ return STATE.selected[k]; }); }
function syncSelectAllLabel(){
  var btn=document.getElementById("cpiSelectAll");
  if(!btn||!STATE.results.length) return;
  var all=STATE.results.every(function(r){ return !rowKey(r) || STATE.selected[r.id]; });
  btn.lastChild.textContent = all ? " Clear all" : " Select all";
}
function updateBulk(){
  var bar=document.getElementById("cpiBulk"), n=selectedRows().length;
  if(!bar) return;
  bar.classList.toggle("on", n>0);
  var lbl=document.getElementById("cpiBulkN");
  if(lbl) lbl.innerHTML="<b>"+n+"</b> selected";
  var enr=document.getElementById("cpiBulkEnrich");
  if(enr) enr.style.display = STATE.entity==="people" ? "" : "none";
}

window.cpiToggleMenu = function(id){
  var m=document.getElementById(id); if(!m) return;
  var on=!m.classList.contains("on");
  document.querySelectorAll(".cpi-menu.on").forEach(function(x){ x.classList.remove("on"); });
  m.classList.toggle("on", on);
};
document.addEventListener("click", function(e){
  if(!e.target.closest(".cpi-menu-wrap")){
    document.querySelectorAll(".cpi-menu.on").forEach(function(m){ m.classList.remove("on"); });
  }
});

var TOAST_T=null;
function toast(msg, kind){
  var el=document.getElementById("cpiToast"); if(!el) return;
  el.className="cpi-toast on"+(kind?" "+kind:"");
  el.textContent=msg;
  if(TOAST_T) clearTimeout(TOAST_T);
  TOAST_T=setTimeout(function(){ el.classList.remove("on"); }, 4200);
}

window.cpiEnrichSelected = function(){
  var rows=selectedRows().filter(function(r){ return r.id && !r.enriched; });
  if(!rows.length){ toast("Nothing new to enrich in the selection.", "err"); return; }
  var btn=document.getElementById("cpiBulkEnrich");
  if(btn){ btn.disabled=true; }
  toast("Enriching "+rows.length+" "+(rows.length===1?"person":"people")+"…");
  fetch(window.__CPI_ENRICH_BULK_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ ids: rows.map(function(r){ return r.id; }) })
  }).then(function(r){ return r.json(); }).then(function(d){
    if(btn) btn.disabled=false;
    if(d && d.error){ toast(d.error, "err"); return; }
    var profiles=(d&&d.profiles)||{};
    var n=0;
    /* Merge onto the existing row so free search fields survive where the
       enrichment came back blank, rather than blanking a populated card. */
    STATE.results = STATE.results.map(function(r){
      var pr=r&&r.id?profiles[r.id]:null;
      if(!pr) return r;
      n++;
      var merged=Object.assign({}, r);
      Object.keys(pr).forEach(function(k){
        var v=pr[k];
        if(v===null||v===undefined||v===""||(Array.isArray(v)&&!v.length)) return;
        merged[k]=v;
      });
      merged.enriched=true; merged.name_masked=false;
      if(STATE.selected[r.id]) STATE.selected[r.id]=merged;
      return merged;
    });
    renderResults();
    var bits=[];
    if(d.fetched) bits.push(d.fetched+" fetched from Apollo");
    if(d.cached) bits.push(d.cached+" from cache (free)");
    toast("Revealed "+n+" "+(n===1?"profile":"profiles")+(bits.length?" · "+bits.join(" · "):""), "ok");
    if(d.capped) toast("Only the first 50 were enriched. Select fewer to do the rest.", "err");
  }).catch(function(){
    if(btn) btn.disabled=false;
    toast("Enrichment failed. Try again in a moment.", "err");
  });
};

window.cpiExport = function(fmt, onlySelected){
  document.querySelectorAll(".cpi-menu.on").forEach(function(m){ m.classList.remove("on"); });
  var rows = onlySelected ? selectedRows() : STATE.results;
  if(!rows.length){
    toast(onlySelected?"Select at least one row first.":"Run a search first.", "err");
    return;
  }
  /* POSTed as JSON and downloaded from a blob, rather than by submitting a form,
     so the endpoint keeps a single JSON contract. The server's filename is
     honoured by reading it back off Content-Disposition. */
  var payload={ entity: STATE.entity, format: fmt, rows: rows };
  fetch(window.__CPI_EXPORT_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  }).then(function(r){
    if(!r.ok) throw new Error("export failed");
    var name=(r.headers.get("Content-Disposition")||"").match(/filename="([^"]+)"/);
    return r.blob().then(function(b){ return { blob:b, name:name?name[1]:("apollo-"+STATE.entity+"."+fmt) }; });
  }).then(function(o){
    var url=URL.createObjectURL(o.blob);
    var a=document.createElement("a");
    a.href=url; a.download=o.name; document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(url); }, 4000);
    toast("Downloaded "+rows.length+" row"+(rows.length===1?"":"s")+" as ."+fmt, "ok");
  }).catch(function(){ toast("Download failed. Try again in a moment.", "err"); });
};

/* ── History ── */
function saveHistory(){
  if(!STATE.results.length) return;
  fetch(window.__CPI_HISTORY_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ entity: STATE.entity, filters: STATE.lastFilters||{},
                           total: STATE.total, rows: STATE.results })
  }).catch(function(){ /* history is best-effort, never blocks a search */ });
}

window.cpiOpenHistory = function(){
  document.getElementById("cpiDrawerOvl").classList.add("on");
  document.getElementById("cpiDrawer").classList.add("on");
  var body=document.getElementById("cpiDrawerBody");
  body.innerHTML='<div class="cpi-loading"><div class="sp"></div><span>Loading history…</span></div>';
  fetch(window.__CPI_HISTORY_URL__).then(function(r){ return r.json(); }).then(function(d){
    if(!d || d.available===false){
      body.innerHTML='<div class="cpi-empty"><span>History needs a database on this environment, so nothing is being stored yet.</span></div>';
      return;
    }
    var entries=d.entries||[];
    if(!entries.length){
      body.innerHTML='<div class="cpi-empty"><span>No saved searches yet. Run a search and it will show up here.</span></div>';
      return;
    }
    body.innerHTML=entries.map(function(e){
      var when=e.created_at?new Date(e.created_at).toLocaleString():"";
      return '<div class="cpi-hist" onclick="cpiRestoreHistory('+e.id+')">'+
        '<div class="cpi-hist-ic">'+(e.entity==="companies"?"&#127970;":"&#128100;")+'</div>'+
        '<div class="cpi-hist-b"><div class="cpi-hist-l">'+esc(e.label||"Saved search")+'</div>'+
        '<div class="cpi-hist-m">'+esc(String(e.count||0))+' rows'+
          (e.total?" of "+pmNum(e.total):"")+' · '+esc(when)+'</div></div>'+
        '<button class="cpi-hist-del" onclick="event.stopPropagation();cpiDeleteHistory('+e.id+')" aria-label="Delete">&#10005;</button>'+
      '</div>';
    }).join("");
  }).catch(function(){
    body.innerHTML='<div class="cpi-empty"><span>Could not load history.</span></div>';
  });
};
window.cpiCloseHistory = function(){
  document.getElementById("cpiDrawerOvl").classList.remove("on");
  document.getElementById("cpiDrawer").classList.remove("on");
};
window.cpiRestoreHistory = function(id){
  fetch(window.__CPI_HISTORY_URL__+"/"+id).then(function(r){ return r.json(); }).then(function(d){
    if(!d || d.error){ toast("Could not reopen that search.", "err"); return; }
    STATE.entity = d.entity==="companies" ? "companies" : "people";
    window.cpiSetEntity(STATE.entity);
    STATE.results = d.rows||[];
    STATE.total = d.total;
    STATE.selected = {};
    STATE.page = 1;
    renderResults();
    document.getElementById("cpiLoadMore").style.display="none";
    window.cpiCloseHistory();
    toast("Reopened "+(STATE.results.length)+" saved rows (no credits spent)", "ok");
  }).catch(function(){ toast("Could not reopen that search.", "err"); });
};
window.cpiDeleteHistory = function(id){
  fetch(window.__CPI_HISTORY_URL__+"/"+id, { method:"DELETE" })
    .then(function(){ window.cpiOpenHistory(); })
    .catch(function(){ toast("Could not delete that entry.", "err"); });
};

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
/* The assistant's avatar is the Arena mark, matching the panel header. Kept as a
   constant so the markup stays identical everywhere it is injected. */
var ARENA_AV = '<img src="/static/logo-mark.svg?v=1" alt="Arena">';

function chatScroll(){ var b=document.getElementById("cpiChatBody"); b.scrollTop=b.scrollHeight; }
function addUserMsg(text){
  CHAT_HISTORY.push({role:"user", content:text});
  var b=document.getElementById("cpiChatBody");
  b.insertAdjacentHTML("beforeend", '<div class="cpi-msg user"><div class="cpi-msg-av">'+esc(initials((window.__CPI_USER_NAME__||"U")))+'</div><div class="cpi-bub"><p>'+esc(text)+"</p></div></div>");
  chatScroll();
}
function addTyping(){
  var b=document.getElementById("cpiChatBody");
  b.insertAdjacentHTML("beforeend", '<div class="cpi-msg assistant" id="cpiTyping"><div class="cpi-msg-av">'+ARENA_AV+'</div><div class="cpi-bub"><div class="cpi-typing"><i></i><i></i><i></i></div></div></div>');
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
  b.insertAdjacentHTML("beforeend", '<div class="cpi-msg assistant"><div class="cpi-msg-av">'+ARENA_AV+'</div><div class="cpi-bub"><p>'+esc(answer||"I could not find an answer for that.").replace(/\n/g,"<br>")+"</p>"+choicesHtml+"</div></div>");
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

/* Starter-prompt chips in the opening message. */
window.cpiAsk = function(text){
  text=String(text||"").trim();
  if(!text) return;
  var sendBtn=document.getElementById("cpiChatSend");
  if(sendBtn && sendBtn.disabled) return;   /* a request is already in flight */
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
