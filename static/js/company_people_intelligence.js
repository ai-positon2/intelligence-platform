/* Contact Finder: search, enrich modal, chat. */
(function(){
"use strict";

var SEARCH_URL = window.__CPI_SEARCH_URL__;
var ENRICH_URL = window.__CPI_ENRICH_URL__;
var CHAT_URL   = window.__CPI_CHAT_URL__;

/* selected is keyed by Apollo id (not grid index) so a tick survives Load more,
   re-renders after a bulk enrich, and reopening a saved search. */
/* historyId is the drawer entry this search already owns, so Load more grows it
   instead of writing a second near-identical row. */
/* pinnedOrgId/pinnedOrgName: set when the user picks a company from the filter
   bar's own disambiguation list (see renderCompanyChoicePicker) for a
   candidate Apollo has no domain on file for, so there is no plain-domain
   value to put in the field instead. gatherFilters() applies the pin only
   while the field's text still matches pinnedOrgName, so it can never bleed
   into an unrelated later search once the user types something else. */
/* firmo: what the last fetch did to describe these people's employers (see
   firmoNote). Per-fetch, not cumulative. */
var STATE = { entity: "people", page: 1, results: [], selected: {},
              total: null, lastFilters: {}, historyId: null,
              pinnedOrgId: null, pinnedOrgName: null, firmo: null,
              companyDetail: undefined, industryDropped: 0, industryWanted: [] };
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
  if(changed){ STATE.selected={}; STATE.firmo=null; updateBulk(); }
  syncLoadMoreLabel();
};

/* Default on, and treated as on if the control is missing, so the richer card is
   what an untouched page produces. */
function companyDetailOn(){
  var el=document.getElementById("fpCompanyDetail");
  return el ? !!el.checked : true;
}

/* Each Companies page is a fresh mixed_companies/search call, which Apollo bills
   a credit for. A People page is free to search; describing employers the cache
   has not already seen costs one credit for the whole page, so with the toggle on
   the label says "up to" -- the only claim true both when the next page is more
   people at companies already described and when it is not. With it off, a People
   page really is free, and the label has to say so or the toggle looks decorative. */
function syncLoadMoreLabel(){
  var btn=document.getElementById("cpiLoadMore");
  if(!btn) return;
  btn.innerHTML = STATE.entity==="companies"
    ? 'Load more <s>&middot; 1 Apollo credit</s>'
    : (companyDetailOn() ? 'Load more <s>&middot; up to 1 Apollo credit</s>'
                         : 'Load more <s>&middot; free</s>');
}

/* Keeps everything that quotes a price in step with the toggle: the Load more
   button, and the toggle's own dimmed state. */
window.cpiSyncCostLabels = function(){
  var lbl=document.querySelector(".cpi-check-cost");
  if(lbl) lbl.classList.toggle("off", !companyDetailOn());
  syncLoadMoreLabel();
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
  /* Clear means back to the defaults, and the default is on. */
  var det=document.getElementById("fpCompanyDetail"); if(det) det.checked=true;
  window.cpiSyncCostLabels();
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
    /* Travels inside `filters` rather than beside them so it is saved, restored
       and exported with the rest of the search: reopening a saved entry then
       reproduces the same rows, not a differently-detailed version of them. The
       server pops it before anything reaches Apollo. */
    f.company_detail = companyDetailOn();
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
    var coEl=document.getElementById("fpCompanyDomain");
    if(STATE.pinnedOrgId && coEl && coEl.value===STATE.pinnedOrgName){
      f.organization_ids=[STATE.pinnedOrgId];
      delete f.company_domains;
    }
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
  var typedCompany=(filters.company_domains && filters.company_domains[0]) || "";
  if(reset){ wrap.innerHTML=skeletonGrid(6); }
  fetch(SEARCH_URL, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ entity: STATE.entity, filters: filters, page: STATE.page })
  }).then(function(r){ return r.json(); }).then(function(d){
    if(btn){ btn.disabled=false; btn.textContent="Search"; }
    if(d && d.error){ toast(d.error, "err"); }
    /* The name matched more than one distinct company: show the list instead
       of guessing one or searching across all of them at once. Nothing below
       this runs -- there is no page to render or "load more" until the user
       picks. */
    else if(d && d.needs_company_choice){
      STATE.results=[]; STATE.selected={};
      renderCompanyChoicePicker(typedCompany, d.choices||[]);
      document.getElementById("cpiToolbar").style.display="none";
      document.getElementById("cpiLoadMore").style.display="none";
      if(d.credits) toast('Looked up "'+typedCompany+'" ('+d.credits+" Apollo credit)", "ok");
      return;
    }
    /* The "at company" field also accepts a plain name now (resolved server-side
       to the real company/companies), so a fresh search discloses what it
       actually matched -- and any Apollo credit that resolution spent -- rather
       than silently substituting a different filter than the one typed. Only
       on `reset`: Load more re-hits the same cached resolution, so repeating
       this on every page would look like a new credit is spent each time. */
    else if(reset && d && d.resolved_company && d.resolved_company.length){
      var names=d.resolved_company.slice(0,3).join(", ");
      if(d.resolved_company.length>3) names += " +"+(d.resolved_company.length-3)+" more";
      var note='Matched "'+typedCompany+'" to '+names;
      if(d.credits) note += " ("+d.credits+" Apollo credit)";
      toast(note, "ok");
    }
    /* How the company detail on these rows was obtained. Kept per page rather
       than accumulated, because it describes what the last fetch did. */
    STATE.firmo=(d&&d.companies_described)||null;
    /* Read back from the response, not from the checkbox: the checkbox is what
       the user will ask for NEXT, and after they flip it the label would
       otherwise describe rows that were fetched under the old setting. */
    STATE.companyDetail=(d&&d.company_detail!==undefined)?!!d.company_detail:undefined;
    STATE.industryDropped=(d&&d.industry_dropped)||0;
    STATE.industryWanted=(d&&d.industry_wanted)||[];
    /* Apollo has no industry filter, so an industry search needs the company
       lookup to verify what it found. Saying so beats appearing to ignore the
       toggle. */
    if(reset && d && d.industry_forced_company_detail){
      toast("Company details were needed to check the industry, so they were fetched for this page.", "ok");
    }
    var items=(d&&d.results)||[];
    /* Advance only when a page actually came back, so Load more fetches the NEXT
       page instead of re-fetching page 1 and appending duplicate cards (which on
       the Companies tab also spent a fresh Apollo credit per click). */
    if(items.length){ STATE.page = (STATE.page||1) + 1; }
    if(d && d.total!==undefined && d.total!==null) STATE.total=d.total;
    STATE.results = reset ? items : STATE.results.concat(items);
    renderResults();
    /* A title search scoped to one company that came back empty gets a real
       explanation instead of the bare "No matches" renderResults() just drew --
       overwrite it with the researched note. */
    if(!items.length && d && d.ai_note) renderAiNote(d.ai_note);
    syncLoadMoreLabel();
    document.getElementById("cpiLoadMore").style.display=(d&&d.has_more)?"":"none";
    if(items.length) saveHistory(reset);
  }).catch(function(){
    if(btn){ btn.disabled=false; btn.textContent="Search"; }
    wrap.innerHTML='<div class="cpi-empty"><span>Search failed. Try again in a moment.</span></div>';
  });
};

/* Reuses chat's own .cpi-choices/.cpi-choice markup and {name,domain,id,logo,
   hq} shape, so a disambiguation list looks and behaves the same wherever it
   shows up in this page. */
function renderCompanyChoicePicker(query, choices){
  var wrap=document.getElementById("cpiResultsWrap");
  var rows=(choices||[]).map(function(c){
    var logo=c.logo?('<img src="'+esc(safeUrl(c.logo))+'" alt="">'):esc(initials(c.name));
    return '<button class="cpi-choice" data-pick-name="'+esc(c.name||"")+'" data-pick-domain="'+esc(c.domain||"")+'" data-pick-org-id="'+esc(c.id||"")+'">'+
      '<div class="cpi-choice-logo">'+logo+"</div>"+
      '<div class="cpi-choice-t"><b>'+esc(c.name)+"</b><span>"+esc([c.domain,c.hq].filter(Boolean).join(" · "))+"</span></div>"+
    "</button>";
  }).join("");
  wrap.innerHTML =
    '<div class="cpi-empty" style="align-items:stretch;text-align:left;max-width:560px;margin:10px auto;padding:26px 24px">'+
      '<div style="text-align:center;color:var(--tx2);font-size:13px;margin-bottom:14px">'+
        'Multiple companies match "'+esc(query)+'". Pick one to search:'+
      "</div>"+
      '<div class="cpi-choices">'+rows+"</div>"+
    "</div>";
  wrap.querySelectorAll(".cpi-choice").forEach(function(btn){
    btn.addEventListener("click", function(){
      window.cpiPickCompanyChoice(btn.getAttribute("data-pick-name")||"",
                                  btn.getAttribute("data-pick-domain")||"",
                                  btn.getAttribute("data-pick-org-id")||"");
    });
  });
}

/* A picked company either has a domain -- put it in the field and let the
   normal, free, strictly domain-filtered search path handle it, no second
   credit spent -- or does not, in which case the id is pinned (see STATE and
   gatherFilters) since there is nothing domain-shaped to type instead. */
window.cpiPickCompanyChoice = function(name, domain, orgId){
  var el=document.getElementById("fpCompanyDomain");
  if(!el) return;
  if(domain){ el.value=domain; STATE.pinnedOrgId=null; STATE.pinnedOrgName=null; }
  else { el.value=name; STATE.pinnedOrgId=orgId; STATE.pinnedOrgName=name; }
  window.cpiRunSearch(true);
};

/* fmtAnswer/.cpi-bub-cost are chat's own answer-formatting pieces, reused here
   so a search's AI explanation reads the same way an equivalent chat answer
   would -- same bullet/paragraph formatting, same "how was this answered"
   footer -- rather than inventing a second visual language for it. */
function renderAiNote(note){
  var wrap=document.getElementById("cpiResultsWrap");
  var bits=[];
  if(note.web_search) bits.push("live web research");
  else if(note.researched) bits.push("background knowledge, no live web");
  var costHtml=bits.length?('<div class="cpi-bub-cost">'+esc(bits.join(" · "))+"</div>"):"";
  wrap.innerHTML =
    '<div class="cpi-empty" style="align-items:stretch;text-align:left;max-width:620px;margin:10px auto;padding:24px 26px">'+
      '<div style="font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--tx3);margin-bottom:10px;font-family:monospace">No exact match &middot; AI-assisted</div>'+
      fmtAnswer(note.answer||"")+
      costHtml+
    "</div>";
}

/* The company detail on a people page is bought once for the whole page and
   cached for 30 days, so whether this page cost anything is worth stating: it is
   the difference between a search that was free and one that spent a credit, and
   nobody should have to guess which they just ran. */
function firmoNote(){
  if(STATE.entity!=="people") return "";
  var f=STATE.firmo;
  if(!f||!f.orgs){
    /* Says why the cards are thin, and where to change it. Without this, turning
       the toggle off looks like the page lost the data rather than being asked
       not to fetch it. */
    return STATE.companyDetail===false
      ? ' <s>&middot; company details off</s>' : "";
  }
  var what=f.orgs===1?"employer":(pmNum(f.orgs)+" employers");
  var how=f.fetched
    ? "described &middot; 1 credit"
    : "described <s>from cache, free</s>";
  return ' <s>&middot;</s> '+what+" "+how;
}

/* Apollo's industry input is a relevance match over company names and tags, so it
   hands back companies that merely mention an industry. Those are removed after
   the fact, which makes a page of 24 arrive as 18: unexplained, that looks like
   Apollo is thin on matches rather than like the filter doing its job. */
function industryNote(){
  var n=STATE.industryDropped;
  if(!n) return "";
  var want=(STATE.industryWanted||[]).slice(0,2).join(", ");
  return ' <s>&middot; '+pmNum(n)+" outside "+esc(want||"the industry")+" removed</s>";
}

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
    cnt.innerHTML = (STATE.total && STATE.total>shown
      ? "Showing <b>"+pmNum(shown)+"</b> of <b>"+pmNum(STATE.total)+"</b> <s>matches in Apollo</s>"
      : "<b>"+pmNum(shown)+"</b> <s>"+(STATE.entity==="people"?"people":"companies")+"</s>")
      + firmoNote() + industryNote();
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
var IC_STACK='<svg viewBox="0 0 24 24"><path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/></svg>';
var IC_PH='<svg viewBox="0 0 24 24"><path d="M22 16.9v3a2 2 0 01-2.2 2 19.8 19.8 0 01-8.6-3.1 19.5 19.5 0 01-6-6A19.8 19.8 0 012.1 4.2 2 2 0 014.1 2h3a2 2 0 012 1.7c.1.9.3 1.8.6 2.6a2 2 0 01-.5 2.1L8.1 9.6a16 16 0 006 6l1.2-1.1a2 2 0 012.1-.5c.8.3 1.7.5 2.6.6a2 2 0 011.7 2z"/></svg>';

/* Both the card and the free details view need the same money/growth phrasing,
   so neither can drift into describing the same figure differently. */
function pmMoney(n, printed){
  /* Apollo's organization_revenue_printed is a bare figure ("62B"), so it needs
     the currency symbol the numeric path already adds. Guarded, because some
     records do print one and "$$62B" is worse than either. */
  if(printed){
    printed=String(printed).trim();
    return /^[$€£¥]/.test(printed) ? printed : ("$"+printed);
  }
  return (n||n===0) ? ("$"+pmNum(n)) : "";
}
function pmGrowth(pct){
  if(pct===null||pct===undefined||pct==="") return "";
  var n=+pct; if(isNaN(n)) return "";
  /* Apollo reports this as a fraction on some records and as whole percent on
     others. Treating 0.14 as "0.1%" understates a real 14% growth by two orders
     of magnitude, so a value inside ±1 is read as the fraction it almost
     certainly is. */
  if(Math.abs(n)<=1) n=n*100;
  return (n>0?"+":"")+n.toFixed(n%1?1:0)+"%";
}
/* City, state and country joined with the repeats removed. Apollo stores the
   city-states and single-province capitals with the same name in two fields, so a
   plain join produced "Beijing, Beijing, China" and "Singapore, Singapore,
   Singapore". Case-insensitive, since the two fields are not always capitalised
   the same way. */
function placeLine(){
  var out=[], seen={};
  Array.prototype.slice.call(arguments).forEach(function(part){
    part=String(part==null?"":part).trim();
    if(!part) return;
    var key=part.toLowerCase();
    if(seen[key]) return;
    seen[key]=1; out.push(part);
  });
  return out.join(", ");
}
/* Whether two Apollo URL-ish values point at the same host, ignoring scheme, www
   and any path. Used to suppress a row that would repeat its neighbour verbatim. */
function sameHost(a, b){
  var norm=function(v){ return String(v==null?"":v).trim().toLowerCase()
    .replace(/^https?:\/\//,"").replace(/^www\./,"").replace(/\/.*$/,""); };
  a=norm(a); b=norm(b);
  return !!a && a===b;
}
function coHq(p, prefix){
  prefix = prefix===undefined ? "organization_" : prefix;
  return placeLine(p[prefix+"city"],p[prefix+"state"],p[prefix+"country"]);
}

function personCard(p,i){
  var loc=placeLine(p.city,p.state,p.country);
  var sel=STATE.selected[p.id]?" sel":"";
  var photo=safeUrl(p.photo_url);
  var av = photo
    ? '<div class="cpi-avatar ph"><img src="'+esc(photo)+'" alt="" loading="lazy" onerror="this.parentNode.textContent=\''+esc(initials(p.full_name))+'\'"></div>'
    : '<div class="cpi-avatar">'+esc(initials(p.full_name))+'</div>';

  var rows=[];
  if(p.organization_name){
    var lg=safeUrl(p.organization_logo)||logoFor(p.organization_domain);
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
  /* The person's own location when a credit has revealed it, the employer's HQ
     when it has not. Labelled either way, because "London" meaning "this person
     is in London" and "London" meaning "their head office is" are different
     facts and a sales rep acts differently on each. */
  if(loc) rows.push(row(IC_PIN, esc(loc)));
  else if(coHq(p)) rows.push(row(IC_PIN, esc(coHq(p))+' <s>HQ</s>'));
  var money=[];
  if(pmMoney(p.organization_revenue,p.organization_revenue_printed)) money.push(esc(pmMoney(p.organization_revenue,p.organization_revenue_printed))+" revenue");
  if(p.organization_funding) money.push("$"+pmNum(p.organization_funding)+" raised");
  if(pmGrowth(p.organization_growth12)) money.push(esc(pmGrowth(p.organization_growth12))+" headcount <s>12mo</s>");
  if(money.length) rows.push(row(IC_HIST, money.join(" · ")));
  var tags=[];
  if(p.seniority) tags.push(esc(String(p.seniority).replace(/_/g," ")));
  (p.departments||[]).slice(0,2).forEach(function(d){ tags.push(esc(String(d).replace(/_/g," "))); });
  /* Read off the title rather than returned by Apollo, so it says so. Only when
     Apollo's own seniority is absent, which on the free tier is always. */
  if(!tags.length){
    var derived=[];
    if(p.seniority_from_title) derived.push(esc(p.seniority_from_title));
    (p.functions_from_title||[]).slice(0,2).forEach(function(f){ derived.push(esc(f)); });
    if(derived.length) tags.push(derived.join(" · ")+' <s>from title</s>');
  }
  if(tags.length) rows.push(row(IC_TAG, tags.join(" · ")));
  if((p.organization_technologies||[]).length){
    rows.push(row(IC_STACK, esc(p.organization_technologies.slice(0,4).join(", "))));
  }
  if(p.email) rows.push(row(IC_ML,'<b>'+esc(p.email)+'</b>'+(p.email_status?' <span class="cpi-badge '+(p.email_status==="verified"?"ok":"dim")+'">'+esc(p.email_status.replace(/_/g," "))+'</span>':"")));
  if(p.title_start_date) rows.push(row(IC_CLK,"In role since "+esc(pmMon(p.title_start_date))));
  if((p.past_companies||[]).length) rows.push(row(IC_HIST,"Previously "+esc(p.past_companies.filter(Boolean).join(", "))));
  /* Apollo's own freshness stamp is free and is the one extra fact available on
     every row, so an otherwise-thin card still says something verifiable. */
  if(p.last_refreshed_at) rows.push(row(IC_CLK,"Apollo data refreshed <s>"+esc(pmMon(p.last_refreshed_at))+"</s>"));
  /* Names the missing fields rather than leaving dead space, and says what the
     click costs, so nobody spends a credit without knowing. Short enough to fit
     one card-width line: the row ellipsises, and a truncated price is no price at
     all. The full list lives in the Details view. */
  if(!p.enriched && !p.email){
    rows.push('<div class="cpi-row hint">'+IC_ML+'<span>Enrich for email, phone &amp; history <s>&middot; 1 credit</s></span></div>');
  }

  var socials="";
  if(p.linkedin_url) socials+='<a class="cpi-card-link" href="'+esc(safeUrl(p.linkedin_url))+'" target="_blank" rel="noopener noreferrer" title="LinkedIn">'+SVG_LI+'</a>';
  if(p.organization_domain) socials+='<a class="cpi-card-link" href="'+esc(safeUrl("https://"+String(p.organization_domain).replace(/^https?:\/\//i,"")))+'" target="_blank" rel="noopener noreferrer" title="Company website">'+SVG_WEB+'</a>';
  if(p.organization_linkedin) socials+='<a class="cpi-card-link" href="'+esc(safeUrl(p.organization_linkedin))+'" target="_blank" rel="noopener noreferrer" title="Company LinkedIn">'+SVG_LI+'</a>';

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
      '<button class="cpi-ghost-btn" onclick="cpiOpenDetails('+i+')">Details</button>'+
      '<button class="cpi-enrich-btn" onclick=\'cpiOpenEnrich("person",'+i+')\'>Enrich &rarr;</button>'+
    '</div></div>';
}

function companyCard(c,i){
  var loc=placeLine(c.city,c.state,c.country);
  var sel=STATE.selected[c.id]?" sel":"";
  var src=safeUrl(c.logo_url)||logoFor(c.primary_domain);
  var logo=src?('<img src="'+esc(src)+'" alt="" loading="lazy" onerror="this.parentNode.textContent=\''+esc(initials(c.name))+'\'">'):esc(initials(c.name));

  var rows=[];
  var firmo=[];
  if(c.estimated_num_employees) firmo.push('<b>'+pmNum(c.estimated_num_employees)+'</b> employees');
  if(pmGrowth(c.growth12)) firmo.push(esc(pmGrowth(c.growth12))+' <s>12mo</s>');
  if(c.industry) firmo.push(esc(c.industry));
  if(c.founded_year) firmo.push("est. "+esc(c.founded_year));
  if(firmo.length) rows.push(row(IC_BLD, firmo.join(" · ")));
  if(loc) rows.push(row(IC_PIN, esc(c.raw_address||loc)));
  var money=[];
  if(pmMoney(c.annual_revenue,c.revenue_printed)) money.push('<b>'+esc(pmMoney(c.annual_revenue,c.revenue_printed))+'</b> revenue');
  if(c.total_funding) money.push('$'+pmNum(c.total_funding)+' raised');
  if(c.publicly_traded_symbol) money.push(esc(c.publicly_traded_symbol));
  if(money.length) rows.push(row(IC_HIST, money.join(" · ")));
  if((c.technologies||[]).length) rows.push(row(IC_STACK, esc(c.technologies.slice(0,4).join(", "))));
  if((c.keywords||[]).length) rows.push(row(IC_TAG, esc(c.keywords.slice(0,4).join(", "))));
  if(c.phone) rows.push(row(IC_PH, esc(c.phone)));
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
      '<button class="cpi-ghost-btn" onclick="cpiOpenDetails('+i+')">Details</button>'+
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

/* Shared by the toolbar's Download menu and a direct export off a history
   entry, so both paths hit the same JSON contract and get the same
   "Search details" sheet on .xlsx -- filters/meta are optional, so a plain
   selection export (no known filters) still works exactly as before. */
function doCpiDownload(entity, rows, fmt, filters, meta){
  if(!rows.length){
    toast("Nothing to export.", "err");
    return;
  }
  var payload={ entity: entity, format: fmt, rows: rows, filters: filters||{}, meta: meta||{} };
  fetch(window.__CPI_EXPORT_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  }).then(function(r){
    if(!r.ok) throw new Error("export failed");
    var name=(r.headers.get("Content-Disposition")||"").match(/filename="([^"]+)"/);
    return r.blob().then(function(b){ return { blob:b, name:name?name[1]:("apollo-"+entity+"."+fmt) }; });
  }).then(function(o){
    var url=URL.createObjectURL(o.blob);
    var a=document.createElement("a");
    a.href=url; a.download=o.name; document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(url); }, 4000);
    toast("Downloaded "+rows.length+" row"+(rows.length===1?"":"s")+" as ."+fmt, "ok");
  }).catch(function(){ toast("Download failed. Try again in a moment.", "err"); });
}

window.cpiExport = function(fmt, onlySelected){
  document.querySelectorAll(".cpi-menu.on").forEach(function(m){ m.classList.remove("on"); });
  var rows = onlySelected ? selectedRows() : STATE.results;
  if(!rows.length){
    toast(onlySelected?"Select at least one row first.":"Run a search first.", "err");
    return;
  }
  /* A selection export omits the filters -- a hand-picked subset of rows is not
     "the results of this search" any more, so labelling it with the search's
     filters would overstate what it actually contains. */
  var filters = onlySelected ? {} : (STATE.lastFilters||{});
  var meta = onlySelected ? {} : { total: STATE.total };
  doCpiDownload(STATE.entity, rows, fmt, filters, meta);
};

/* ── History ── */
/* isNewSearch distinguishes "Search" from "Load more". A new search starts a new
   entry; paging grows the entry the server already gave us an id for, so one
   search is one row in the drawer no matter how deep it is paged. */
function saveHistory(isNewSearch){
  if(!STATE.results.length) return;
  if(isNewSearch) STATE.historyId = null;
  fetch(window.__CPI_HISTORY_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ entity: STATE.entity, filters: STATE.lastFilters||{},
                           total: STATE.total, rows: STATE.results,
                           replace_id: STATE.historyId||0 })
  }).then(function(r){ return r.json(); }).then(function(d){
    if(d && d.id) STATE.historyId = d.id;
  }).catch(function(){ /* history is best-effort, never blocks a search */ });
}

/* Reverse of gatherFilters: put a saved search's filters back on screen. Without
   this, reopening an entry showed its rows while the panel still held whatever
   was last typed, so pressing Search ran a different query than the one on
   screen. Uses the same specs, so a new filter stays a one-line change. */
function applyFiltersToForm(f){
  f=f||{};
  window.cpiClearFilters();
  (STATE.entity==="people"?PEOPLE_FIELDS:COMPANY_FIELDS).forEach(function(spec){
    var el=document.getElementById(spec[0]); if(!el) return;
    var v=f[spec[1]];
    if(v===undefined||v===null||v==="") return;
    el.value = Array.isArray(v) ? v.join(", ") : String(v);
  });
  [["#fpSeniority .cpi-chip", f.seniorities], ["#fpEmailStatus .cpi-chip", f.email_status]]
    .forEach(function(pair){
      var want=pair[1]||[];
      document.querySelectorAll(pair[0]).forEach(function(c){
        c.classList.toggle("on", want.indexOf(c.getAttribute("data-val"))>=0);
      });
    });
  /* The employee filter is a <select> of "min,max" buckets, so match the option
     back by value rather than trying to set the numbers directly. */
  var empId=STATE.entity==="people"?"fpEmpRange":"fcEmpRange";
  var emp=document.getElementById(empId);
  if(emp && f.employee_min!==undefined && f.employee_min!==null){
    var want=String(f.employee_min)+","+
      ((f.employee_max===undefined||f.employee_max===null||f.employee_max>=999999999)?"":String(f.employee_max));
    for(var i=0;i<emp.options.length;i++){
      if(emp.options[i].value===want){ emp.value=want; break; }
    }
  }
  var dc=f.department_counts||{};
  var dcName=Object.keys(dc)[0];
  if(dcName){
    var pre=STATE.entity==="people"?"fp":"fc";
    var n=document.getElementById(pre+"DeptName"); if(n) n.value=dcName;
    var lo=document.getElementById(pre+"DeptMin"); if(lo) lo.value=(dc[dcName].min!==undefined?dc[dcName].min:"");
    var hi=document.getElementById(pre+"DeptMax"); if(hi) hi.value=(dc[dcName].max!==undefined?dc[dcName].max:"");
  }
  /* Stored in Apollo's days, shown in the months the UI collects. */
  var tMin=document.getElementById("fpTenureMin"), tMax=document.getElementById("fpTenureMax");
  if(tMin && f.days_in_title_min) tMin.value=Math.round(f.days_in_title_min/30);
  if(tMax && f.days_in_title_max) tMax.value=Math.round(f.days_in_title_max/30);
  var sim=document.getElementById("fpSimilarTitles");
  if(sim && f.include_similar_titles!==undefined) sim.checked=!!f.include_similar_titles;
  var unk=document.getElementById("fcUnknownFounded");
  if(unk) unk.checked=!!f.include_unknown_founded_year;
  /* Restored from the entry, but only when it was actually recorded: entries saved
     before this toggle existed have no value, and reading `undefined` as "off"
     would silently reopen an old search with less detail than it was run with. */
  var det=document.getElementById("fpCompanyDetail");
  if(det) det.checked = (f.company_detail===undefined) ? true : !!f.company_detail;
  window.cpiSyncCostLabels();
}

var IC_PERSON='<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.6"/><path d="M5 20c0-3.9 3.1-7 7-7s7 3.1 7 7"/></svg>';
var IC_CHAT='<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 01-8 8H8l-5 3 1.4-4.2A8 8 0 1121 12z"/></svg>';
/* Single-line clamp for drawer metadata: a full answer is far too long to sit
   under an entry title, and CSS truncation would still ship the whole string. */
function trimTo(s, n){
  s=String(s||"").replace(/\s+/g," ").trim();
  return s.length>n ? s.slice(0,n-1)+"…" : s;
}
var IC_DL='<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>';

/* Buckets a history entry's timestamp into the same relative-date groups any
   mail/notes app uses, so a growing list of saved searches reads as a
   timeline instead of one long undifferentiated stack. */
function histBucket(iso){
  if(!iso) return "Earlier";
  var d=new Date(iso), now=new Date();
  var startOf=function(dt){ return new Date(dt.getFullYear(),dt.getMonth(),dt.getDate()).getTime(); };
  var days=Math.round((startOf(now)-startOf(d))/86400000);
  if(days<=0) return "Today";
  if(days===1) return "Yesterday";
  if(days<7) return "This week";
  if(days<30) return "This month";
  return "Earlier";
}

window.cpiOpenHistory = function(){
  document.getElementById("cpiDrawerOvl").classList.add("on");
  document.getElementById("cpiDrawer").classList.add("on");
  var body=document.getElementById("cpiDrawerBody");
  body.innerHTML='<div class="cpi-loading"><div class="sp"></div><span>Loading history…</span></div>';
  fetch(window.__CPI_HISTORY_URL__).then(function(r){ return r.json(); }).then(function(d){
    if(!d || d.available===false){
      body.innerHTML='<div class="cpi-empty"><span>History needs a database on this environment, so nothing is being stored yet.</span></div>';
      var clr=document.getElementById("cpiHistClearAll"); if(clr) clr.style.display="none";
      return;
    }
    var entries=d.entries||[];
    var clr=document.getElementById("cpiHistClearAll");
    if(clr) clr.style.display = entries.length ? "" : "none";
    if(!entries.length){
      body.innerHTML='<div class="cpi-empty"><span>Nothing saved yet. Searches you run, questions you ask the assistant, and contacts you enrich all show up here.</span></div>';
      return;
    }
    var groups={}, order=["Today","Yesterday","This week","This month","Earlier"];
    entries.forEach(function(e){ var b=histBucket(e.created_at); (groups[b]=groups[b]||[]).push(e); });
    var idx=0, html="";
    order.forEach(function(b){
      var list=groups[b]; if(!list||!list.length) return;
      html += '<div class="cpi-hist-date">'+esc(b)+'</div>';
      html += list.map(function(e){
        var when=e.created_at?new Date(e.created_at).toLocaleString(undefined,
          {month:"short",day:"numeric",hour:"numeric",minute:"2-digit"}):"";
        var co=e.entity==="companies";
        var isChat=e.entity==="chat";
        var isContact=e.entity==="contact"||e.entity==="company_profile";
        var style='style="animation-delay:'+Math.min(idx++,10)*28+'ms"';
        /* Three kinds of entry now share this drawer, so each says what it is
           and what reopening it will do, rather than all reading "N rows". */
        var ic, cls, meta;
        if(isChat){
          ic=IC_CHAT; cls="ch";
          meta=(e.preview? trimTo(e.preview,90)+" · " : "")+when;
        }else if(isContact){
          ic=IC_PERSON; cls="ct";
          meta="Enriched contact · already paid for · "+when;
        }else{
          ic=co?IC_BLD:IC_PERSON; cls=co?"co":"pp";
          meta=String(e.count||0)+" row"+(e.count===1?"":"s")+
            (e.total&&e.total>e.count?" of "+pmNum(e.total):"")+" · "+when;
        }
        /* Export builds a spreadsheet from search-row shape, which a chat answer
           and an enriched profile do not have -- offering the button would hand
           back a broken file, so it is only on the entries it works for. */
        var exportBtn = (isChat||isContact) ? "" :
          '<button class="cpi-hist-act exp" onclick="event.stopPropagation();cpiExportHistoryEntry('+e.id+',this)" aria-label="Export" title="Export this search">'+IC_DL+'</button>';
        var opener = isChat ? "cpiReopenChat" : (isContact ? "cpiReopenContact" : "cpiRestoreHistory");
        return '<div class="cpi-hist" '+style+' onclick="'+opener+'('+e.id+')">'+
          '<div class="cpi-hist-ic '+cls+'">'+ic+'</div>'+
          '<div class="cpi-hist-b"><div class="cpi-hist-l">'+esc(e.label||"Saved search")+'</div>'+
          '<div class="cpi-hist-m">'+esc(meta)+
            (e.credits?' &middot; '+esc(String(e.credits))+" credit"+(e.credits===1?"":"s"):"")+
          '</div></div>'+
          '<div class="cpi-hist-actions">'+exportBtn+
            '<button class="cpi-hist-act del" onclick="event.stopPropagation();cpiDeleteHistory('+e.id+')" aria-label="Delete" title="Delete">&#10005;</button>'+
          '</div>'+
        '</div>';
      }).join("");
    });
    body.innerHTML=html;
  }).catch(function(){
    body.innerHTML='<div class="cpi-empty"><span>Could not load history.</span></div>';
  });
};
/* Exports a saved search straight from the drawer -- no need to reopen it into
   the main grid first. Always .xlsx, since that is the format that carries the
   "Search details" sheet (the filters that produced these rows), which is the
   whole point of exporting from history rather than just rerunning it. */
window.cpiExportHistoryEntry = function(id, btn){
  if(btn){ btn.disabled=true; }
  fetch(window.__CPI_HISTORY_URL__+"/"+id).then(function(r){ return r.json(); }).then(function(d){
    if(btn){ btn.disabled=false; }
    if(!d || d.error){ toast("Could not export that search.", "err"); return; }
    var entity = d.entity==="companies" ? "companies" : "people";
    doCpiDownload(entity, d.rows||[], "xlsx", d.filters||{}, { total: d.total, label: d.label });
  }).catch(function(){
    if(btn){ btn.disabled=false; }
    toast("Could not export that search.", "err");
  });
};
window.cpiClearAllHistory = function(){
  var body=document.getElementById("cpiDrawerBody");
  var ids=Array.prototype.map.call(body.querySelectorAll(".cpi-hist"), function(el){
    return el.getAttribute("onclick").match(/\d+/)[0];
  });
  if(!ids.length) return;
  /* The drawer now also holds saved answers and enriched contacts, so the
     confirmation says "entries" rather than promising only searches will go. */
  if(!window.confirm("Delete all "+ids.length+" saved "+(ids.length===1?"entry":"entries")+
                     " (searches, answers and enriched contacts)? This cannot be undone.")) return;
  Promise.all(ids.map(function(id){
    return fetch(window.__CPI_HISTORY_URL__+"/"+id, { method:"DELETE" }).catch(function(){});
  })).then(function(){ window.cpiOpenHistory(); toast("Cleared history.", "ok"); });
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
    /* Put the filters back on screen and keep them as lastFilters, so what the
       panel shows, what a re-run would query, and what the entry is labelled
       with all stay the same thing. */
    STATE.lastFilters = d.filters||{};
    applyFiltersToForm(STATE.lastFilters);
    /* Continuing this reopened search grows its own entry rather than forking a
       near-duplicate in the drawer. */
    STATE.historyId = d.id||null;
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
/* Puts a saved exchange back into the chat panel, question and answer, exactly
   as it was answered. Deliberately a REPLAY and not a re-ask: re-running the
   question would spend credits again and could answer differently, which is not
   what "open my history" should mean. Any Enrich buttons that came with it are
   restored too, so a contact the answer named is still one click away. */
window.cpiReopenChat = function(id){
  fetch(window.__CPI_HISTORY_URL__+"/"+id).then(function(r){ return r.json(); }).then(function(d){
    if(!d || d.error){ toast("Could not reopen that answer.", "err"); return; }
    var q=(d.filters&&d.filters.question)||d.label||"";
    if(q) addUserMsg(q);
    /* Credits are passed as 0 because reopening spends nothing; the provenance
       flags are replayed as recorded so the footer describes how the answer was
       ORIGINALLY produced rather than asserting something that was never true. */
    addAssistantMsg(d.answer||"(no answer was recorded for this one)", null,
                    0, !!(d.filters&&d.filters.researched),
                    !!(d.filters&&d.filters.web_search),
                    (d.rows||[]).map(function(p){
                      return {type:"person", name:p.name, title:p.title,
                              domain:p.domain, apollo_id:p.apollo_id};
                    }));
    window.cpiCloseHistory();
    toast("Reopened a saved answer (no credits spent)", "ok");
  }).catch(function(){ toast("Could not reopen that answer.", "err"); });
};
/* Reopens an enriched profile straight from storage. The credit for this was
   already spent when it was first enriched, so this must NOT go back to
   /enrich -- it renders the saved record into the same modal instead. */
window.cpiReopenContact = function(id){
  fetch(window.__CPI_HISTORY_URL__+"/"+id).then(function(r){ return r.json(); }).then(function(d){
    var p=(d&&d.rows&&d.rows[0])||null;
    if(!p){ toast("Could not reopen that contact.", "err"); return; }
    var isCo = d.entity==="company_profile";
    document.getElementById("pmOvl").classList.add("on");
    document.getElementById("pmWrap").classList.add("on");
    document.body.style.overflow="hidden";
    document.getElementById("pmHero").innerHTML = isCo ? companyHero(p) : personHero(p);
    document.getElementById("pmBody").innerHTML = isCo ? companyBody(p) : personBody(p);
    window.cpiCloseHistory();
    toast("Reopened a saved contact (no credits spent)", "ok");
  }).catch(function(){ toast("Could not reopen that contact.", "err"); });
};

/* ── Enrich modal ── */
/* safeUrl is applied HERE rather than at the call sites: these two helpers are
   the only places Apollo-supplied URLs become an href, and a caller that forgot
   would ship a clickable "javascript:" link that esc() cannot defuse (it stops
   attribute breakout, not the scheme). Sanitizing at the sink means a new caller
   cannot reintroduce it. */
function pmKV(label,val,isLink){
  if(val===null||val===undefined||val==="") return "";
  var href=isLink?safeUrl(val):"";
  /* An unlinkable value still shows as text: dropping the row would hide a real
     fact, and rendering a dead <a> would invite the click anyway. */
  var v=href?('<a href="'+esc(href)+'" target="_blank" rel="noopener noreferrer">'+esc(String(href).replace(/^https?:\/\/(www\.)?/,""))+'</a>'):esc(val);
  return '<div class="pm-kv-i"><span>'+esc(label)+'</span><b>'+v+'</b></div>';
}
function pmSo(url,svg,label,isCo,tip){
  url=safeUrl(url);
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

/* Shared by every "Enrich" entry point (results grid, chat panel): opens the
   profile modal, shows a skeleton, then fetches and renders the real profile.
   Pulled out so a chat-originated person -- which has no index into
   STATE.results -- can drive the exact same modal as the grid's own button. */
function cpiRunEnrich(type, heroSeed, body){
  pmOpenModal();
  document.getElementById("pmHero").innerHTML = type==="person" ? personHero(heroSeed) : companyHero(heroSeed);
  document.getElementById("pmBody").innerHTML = '<div class="pm-sk" style="width:40%;height:11px;margin-bottom:14px"></div><div class="pm-sk" style="width:100%;height:52px;margin-bottom:9px"></div><div class="pm-sk" style="width:100%;height:52px"></div>';

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
}
/* ── Free details view ── */
/* Apollo's own UI shows a full profile panel the moment you click a search
   result. This is that panel, built entirely from fields already in hand: no
   request, no credit, instant. It exists because the previous card was the only
   view of a row, so everything that did not fit in six lines was invisible even
   though it had already been fetched and paid for.
   Same pm-* chrome as the paid modal on purpose -- one visual language, and the
   Enrich button inside it is the upgrade path rather than a competing entry
   point. */
function pmOpenModal(){
  document.getElementById("pmOvl").classList.add("on");
  document.getElementById("pmWrap").classList.add("on");
  document.body.style.overflow="hidden";
}

/* pmKV drops empty values, so a section whose every field was blank would render
   as a bare heading over nothing. Checking the assembled html rather than the
   inputs means one guard covers every field type. */
function pmSection(title, tag, inner){
  if(!inner) return "";
  return '<div class="pm-sec"><h4 class="pm-h4">'+esc(title)+(tag?"<s>"+esc(tag)+"</s>":"")+"</h4>"+inner+"</div>";
}
function pmKvBlock(){
  var html=Array.prototype.slice.call(arguments).join("");
  return html ? '<div class="pm-kv">'+html+"</div>" : "";
}
function pmTagBlock(list, cap){
  list=(list||[]).slice(0,cap||14);
  if(!list.length) return "";
  return '<div class="pm-tags">'+list.map(function(t){ return '<span class="pm-tag">'+esc(t)+"</span>"; }).join("")+"</div>";
}

/* The employer, described as fully as the row allows. Shared by the person view
   and the company view so a company never looks better as a search result than it
   does under the person who works there. */
function detailEmployer(r, pre){
  pre = pre===undefined ? "organization_" : pre;
  var g=function(k){ return r[pre+k]; };
  var name=pre?g("name"):r.name;
  var domain=pre?g("domain"):r.primary_domain;
  var logo=safeUrl(pre?g("logo"):r.logo_url)||logoFor(domain);
  var industry=pre?g("industry"):r.industry;
  var desc=pre?g("description"):r.short_description;
  var head=logo?('<img src="'+esc(logo)+'" alt="" onerror="this.style.display=\'none\'">'):esc(initials(name));
  var card = name ? ('<div class="pm-cocard"><div class="pm-cotop"><div class="pm-colg">'+head+"</div>"+
    '<div style="min-width:0"><div class="pm-coname">'+esc(name)+"</div>"+
    (industry?'<div class="pm-coind">'+esc(industry)+"</div>":"")+"</div></div>"+
    (desc?'<div style="font-size:12.3px;color:#98a3c2;line-height:1.6;margin-top:12px">'+esc(desc)+"</div>":"")+
    "</div>") : "";
  var website=pre?g("website"):r.website_url;
  var hq=pre?coHq(r,pre):placeLine(r.city,r.state,r.country);
  var kv=pmKvBlock(
    pmKV("Employees", (pre?g("employees"):r.estimated_num_employees) ? pmNum(pre?g("employees"):r.estimated_num_employees) : ""),
    pmKV("Headcount growth 6mo", pmGrowth(pre?g("growth6"):r.growth6)),
    pmKV("Headcount growth 12mo", pmGrowth(pre?g("growth12"):r.growth12)),
    pmKV("Revenue", pmMoney(pre?g("revenue"):r.annual_revenue, pre?g("revenue_printed"):r.revenue_printed)),
    pmKV("Total funding", (pre?g("funding"):r.total_funding) ? "$"+pmNum(pre?g("funding"):r.total_funding) : ""),
    pmKV("Latest round", pmMon(pre?g("funding_date"):r.latest_funding_round_date) || ""),
    pmKV("Founded", pre?g("founded"):r.founded_year),
    pmKV("Ticker", pre?g("ticker"):r.publicly_traded_symbol),
    pmKV("HQ", hq),
    pmKV("Address", pre?g("address"):r.raw_address),
    pmKV("Phone", pre?g("phone"):r.phone),
    /* pmKV renders a URL stripped of its scheme and www, so a website of
       "https://lenovo.com" and a domain of "lenovo.com" printed as two identical
       tiles. Only worth its own row when it says something the website does not,
       and the section heading already carries the domain either way. */
    pmKV("Domain", sameHost(domain, website) ? "" : domain),
    pmKV("Website", website, true),
    pmKV("LinkedIn", pre?g("linkedin"):r.linkedin_url, true),
    pmKV("Apollo org ID", pre?r.organization_id:r.id)
  );
  var tech=pmTagBlock(pre?g("technologies"):r.technologies, 12);
  var kws=pmTagBlock(pre?g("keywords"):r.keywords, 14);
  var inner=card+kv+
    (tech?'<div class="pm-kv-h">Technologies</div>'+tech:"")+
    (kws?'<div class="pm-kv-h">Keywords</div>'+kws:"");
  return pmSection(pre?"Employer":"Firmographics", domain||"apollo", inner);
}

function personDetailsBody(r, idx){
  var loc=placeLine(r.city,r.state,r.country);
  var out=pmSection("Role", "apollo search", pmKvBlock(
    pmKV("Title", r.title),
    pmKV("Headline", r.headline),
    pmKV("Seniority", r.seniority ? String(r.seniority).replace(/_/g," ") : ""),
    /* Two visibly different labels for the same idea, because they have
       different warranties: one is Apollo's, one is ours. */
    pmKV("Seniority (read from title)", r.seniority?"":r.seniority_from_title),
    pmKV("Function (read from title)", (r.functions_from_title||[]).join(", ")),
    pmKV("Departments", (r.departments||[]).map(function(d){ return String(d).replace(/_/g," "); }).join(", ")),
    pmKV("In role since", r.title_start_date?pmMon(r.title_start_date):""),
    pmKV("Previously", (r.past_companies||[]).filter(Boolean).join(", ")),
    pmKV("Location", loc),
    pmKV("LinkedIn", r.linkedin_url, true),
    pmKV("X / Twitter", r.twitter_url, true),
    pmKV("Apollo record refreshed", r.last_refreshed_at?pmMon(r.last_refreshed_at):""),
    pmKV("Apollo ID", r.id)
  ));

  var ct=(r.email?pmCt(SVG_MAIL,"Email",r.email,"mailto:"+r.email,
            r.email_status?('<span class="pm-vf"'+(r.email_status==="verified"?"":' style="color:#9aa5c6;border-color:rgba(255,255,255,.16);background:rgba(255,255,255,.05)"')+">"+esc(String(r.email_status).replace(/_/g," "))+"</span>"):""):"")+
    (r.phones||[]).map(function(n){ return pmCt(SVG_PH,"Phone",n,"tel:"+String(n).replace(/[^\d+]/g,""),""); }).join("");
  if(ct){
    out+=pmSection("Contact", "revealed", '<div class="pm-ct">'+ct+"</div>");
  } else {
    /* Names exactly what a credit buys and what is already here, so the choice to
       spend one is informed rather than hopeful. */
    out+=pmSection("Contact", "not revealed yet",
      '<div class="pm-ct-no"><b>Nothing revealed on this person yet</b>'+
      "Apollo's free search returns identity and role only. Enriching adds their verified email and status, direct and mobile phone numbers, their own city and country, full career history and photo"+
      (r.name_masked?", and reveals the surname Apollo is masking here":"")+
      ". That costs 1 credit, and is cached afterwards so reopening this person is free.</div>"+
      '<div style="margin-top:12px"><button class="cpi-enrich-btn" style="margin-left:0" onclick=\'cpiCloseModal();cpiOpenEnrich("person",'+idx+')\'>Enrich this person &middot; 1 credit</button></div>');
  }
  out+=detailEmployer(r);
  return out;
}

function companyDetailsBody(r, idx){
  var out=detailEmployer(r, "");
  out+=pmSection("Go deeper", "1 credit",
    '<div class="pm-ct-no"><b>Everything above is already paid for</b>'+
    "Enriching this company re-reads it from Apollo's organization record, which adds its head-office phone and address where the search did not carry them, plus the leadership contacts Apollo holds.</div>"+
    '<div style="margin-top:12px"><button class="cpi-enrich-btn" style="margin-left:0" onclick=\'cpiCloseModal();cpiOpenEnrich("company",'+idx+')\'>Enrich this company &middot; 1 credit</button></div>');
  return out;
}

window.cpiOpenDetails = function(idx){
  var r=STATE.results[idx];
  if(!r) return;
  var isPerson = STATE.entity==="people";
  pmOpenModal();
  var hero = isPerson
    ? personHero({name:r.full_name, title:r.title, headline:r.headline, photo:r.photo_url,
                  seniority:(r.seniority?String(r.seniority).replace(/_/g," "):r.seniority_from_title),
                  location:placeLine(r.city,r.state,r.country)||coHq(r),
                  linkedin:r.linkedin_url,
                  company:{name:r.organization_name, employees:r.organization_employees,
                           linkedin:r.organization_linkedin, website:r.organization_website,
                           domain:r.organization_domain}})
    : companyHero({name:r.name, logo:r.logo_url||logoFor(r.primary_domain), industry:r.industry,
                   description:r.short_description, employees:r.estimated_num_employees,
                   hq:placeLine(r.city,r.state,r.country),
                   revenue:(r.revenue_printed||(r.annual_revenue?pmNum(r.annual_revenue):"")),
                   linkedin:r.linkedin_url, website:r.website_url});
  document.getElementById("pmHero").innerHTML=hero;
  document.getElementById("pmBody").innerHTML=isPerson?personDetailsBody(r,idx):companyDetailsBody(r,idx);
};

window.cpiOpenEnrich = function(type, idx){
  var item=STATE.results[idx]; if(!item) return;
  var heroSeed = type==="person" ? {name:item.full_name,title:item.title} : {name:item.name,logo:item.logo_url};
  var body = type==="person"
    ? { type:"person", name: item.full_name, domain: item.organization_domain, apollo_id: item.id }
    : { type:"company", domain: item.primary_domain, apollo_id: item.id };
  cpiRunEnrich(type, heroSeed, body);
};
/* The chat panel's own "Enrich" button: a person named in a chat answer has no
   row in STATE.results to index into, so its identifying fields ride in
   data-* attributes on the button itself instead (same reasoning as the
   disambiguation choice buttons above -- no value gets built into a handler
   string, so a name containing a quote cannot break anything). */
window.cpiEnrichChatPerson = function(btn){
  var name=btn.getAttribute("data-name")||"", domain=btn.getAttribute("data-domain")||"",
      title=btn.getAttribute("data-title")||"", apolloId=btn.getAttribute("data-apollo-id")||"";
  btn.disabled=true; btn.textContent="Enriching…";
  cpiRunEnrich("person", {name:name, title:title},
    {type:"person", name:name, domain:domain, apollo_id:apolloId});
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
/* Answers now arrive as a lead paragraph plus bullets, so a little structure gets
   rendered. esc() runs FIRST and every tag introduced below is one we generate
   ourselves, so nothing in the model's output can inject markup. */
/* A citation URL (the answer prompt requires one whenever it names a publicly
   sourced person) would otherwise sit in the paragraph at full body-text size,
   wrapping mid-URL and dominating the message -- the actual complaint behind
   an answer that "needs its alignment fixed". Rendered small, muted and
   monospace instead, matching the quieter treatment .cpi-bub .src already
   gets. Runs on the ESCAPED string, so `url` here is already HTML-safe and
   must not be escaped again (that would turn a real "&" into "&amp;amp;"). */
function linkifySources(safeHtml){
  return safeHtml.replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]])/g, function(url){
    return safeUrl(url) ?
      '<a class="src-link" href="'+url+'" target="_blank" rel="noopener noreferrer">'+url+"</a>" :
      url;
  });
}
function fmtAnswer(text){
  /* Data can carry literal asterisks: Apollo masks withheld surnames as
     "Sh***a". Three in a row make the bold matcher pair the WRONG asterisks --
     "Vivek Sh***a, Meghana Ka***i" bolded "a, Meghana Ka" -- so any run that is
     not exactly a two-asterisk delimiter is neutralised before the bold pass.
     The server already abbreviates those names to "Vivek Sh." (see
     _cpi_display_name); this is the second line of defence, for asterisks that
     arrive from anywhere else, such as quoted web-research text. */
  var raw=String(text==null?"":text).replace(/\*{3,}/g,"…");
  var safe=esc(raw).replace(/\*\*([^*\n]+)\*\*/g,"<b>$1</b>");
  safe=linkifySources(safe);
  var out=[], list=null;
  safe.split(/\n/).forEach(function(ln){
    var m=ln.match(/^\s*(?:[-*•])\s+(.*)$/);
    if(m){ (list=list||[]).push("<li>"+m[1]+"</li>"); return; }
    if(list){ out.push('<ul class="cpi-bub-ul">'+list.join("")+"</ul>"); list=null; }
    if(ln.trim()) out.push("<p>"+ln+"</p>");
  });
  if(list) out.push('<ul class="cpi-bub-ul">'+list.join("")+"</ul>");
  return out.join("") || "<p>"+safe+"</p>";
}

function addAssistantMsg(answer, choices, credits, researched, webSearch, enrich){
  CHAT_HISTORY.push({role:"assistant", content:answer||""});
  var b=document.getElementById("cpiChatBody");
  /* Answers can spend Apollo credits from a pool the whole team shares, so each
     one says what it cost. Silence here is what let a single question quietly
     spend twenty.
     The research half is reported HONESTLY rather than as a flat "includes web
     research": if the key has no web-search tool, _cpi_research falls back to the
     model's background knowledge, which has no citations and a training cutoff.
     Saying so is both fair to the reader and the only way anyone can tell whether
     live web search is actually working in production. */
  var bits=[];
  var n=+credits||0;
  if(n>0) bits.push(n+" Apollo credit"+(n===1?"":"s")+" used");
  if(webSearch) bits.push("live web research");
  else if(researched) bits.push("background knowledge, no live web");
  var costHtml=bits.length?('<div class="cpi-bub-cost">'+esc(bits.join(" · "))+"</div>"):"";
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
  /* Present whenever a person was named but not enriched (see cpi_chat: no
     contact info was asked for, so the paid lookup was skipped by design) --
     one click spends the credit and opens the same profile modal the results
     grid uses, rather than spending it automatically on every question. */
  /* One entry or several: a list answer names more than one person and each of
     them is separately worth a credit, so this takes either shape. */
  var enrichList = (Array.isArray(enrich) ? enrich : (enrich ? [enrich] : []))
    .filter(function(e){ return e && e.type==="person"; });
  var enrichHtml = enrichList.length
    ? '<div class="cpi-enrich-row">'+enrichList.map(function(e){
        /* `label` is the printable form of a masked name ("Vivek Sh." for
           "Vivek Sh***a"); `name` stays raw because it is what gets sent to
           Apollo's people/match. */
        return '<button class="cpi-enrich-chip" data-name="'+esc(e.name||"")+'" data-domain="'+esc(e.domain||"")+'" data-title="'+esc(e.title||"")+'" data-apollo-id="'+esc(e.apollo_id||"")+'">'+SVG_LI+" Enrich "+esc(e.label||e.name||"this person")+"</button>";
      }).join("")+"</div>"
    : "";
  b.insertAdjacentHTML("beforeend", '<div class="cpi-msg assistant"><div class="cpi-msg-av">'+ARENA_AV+'</div><div class="cpi-bub">'+fmtAnswer(answer||"I could not find an answer for that.")+choicesHtml+enrichHtml+costHtml+"</div></div>");
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
    justAdded.querySelectorAll(".cpi-enrich-chip").forEach(function(btn){
      btn.addEventListener("click", function(){ window.cpiEnrichChatPerson(btn); });
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
      addAssistantMsg(d&&d.answer, d&&d.choices, d&&d.credits, d&&d.researched,
                      d&&d.web_search, d&&d.enrich);
    })
    .catch(function(){
      removeTyping(); sendBtn.disabled=false;
      addAssistantMsg("Something went wrong reaching the assistant. Try again.");
    });
}

})();
