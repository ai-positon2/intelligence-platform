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
/* entity is what the FILTER PANEL is set to, i.e. what the next search will look
   for. shownEntity is what the rows currently on screen actually are. They are
   two different facts and were one variable: flipping the toggle relabelled a
   grid of people as companies without refetching, so the next re-render drew them
   through the company card (every field blank, every name "Unknown"), an export
   built company columns out of person rows, and Load more appended companies to
   the bottom of a people list. Anything describing the rows must read
   shownEntity; only the search itself reads entity. */
var STATE = { entity: "people", shownEntity: null, page: 1, results: [],
              selected: {},
              total: null, lastFilters: {}, historyId: null,
              pinnedOrgId: null, pinnedOrgName: null, firmo: null,
              companyDetail: undefined, rejected: null, rejectedLabels: {},
              /* Layout is a working preference, not a property of a search, so
                 it is remembered across visits. Read defensively: localStorage
                 throws in some privacy modes, and an unreadable preference is a
                 reason to fall back to cards, not to break the page. */
              view: (function(){
                try{ return localStorage.getItem("cpi-view")==="table" ? "table" : "cards"; }
                catch(e){ return "cards"; }
              })(),
              sortKey: null, sortDir: "asc" };
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
  /* The rows on screen and any selection of them belong to the search that
     fetched them, so switching tabs leaves both alone. What it cannot leave alone
     is Load more: that continues the DISPLAYED search, and continuing a people
     search from the companies panel appended companies to a list of people. */
  if(changed && STATE.shownEntity && STATE.shownEntity!==entity){
    var more=document.getElementById("cpiLoadMore");
    if(more) more.style.display="none";
  }
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

/* ── Vocabulary comboboxes ── */
/* Several Apollo filters accept only values Apollo already knows, and say nothing
   when given anything else: the search just comes back empty, or quietly wider
   than asked for. Measured on this account, an invented technology uid returns 0
   people, an invented place returns 0, and a misspelled place returns a different
   set rather than an error. Industry is the worst of them, because Apollo's
   classification is the LinkedIn taxonomy in which nothing at all is spelled
   "healthcare": it is "hospital & health care", "medical practice",
   "pharmaceuticals" and six more.

   So every one of those filters is a picker rather than a text box, all driven by
   the same widget below. Suggestions come from the server, which merges a
   written-down copy of each vocabulary with every value Apollo has actually been
   observed to return, so the list improves as the tool is used and an unconfirmed
   value is visibly unconfirmed.

   Typing is never blocked: an unlisted value is still a legitimate search. The
   two code vocabularies are the exception, because Apollo enforces their shape and
   rejects anything else, so those are checked before a chip is made. */
var COMBO_SPECS = [
  /* [input id, Apollo filter key, vocabulary, what one entry is called] */
  ["fpIndustry",         "industries",           "industry",   "industry"],
  ["fpLocation",         "person_locations",     "location",   "location"],
  ["fpCompanyLocation",  "company_locations",    "location",   "location"],
  ["fpNaics",            "naics_codes",          "naics",      "NAICS code"],
  ["fpSic",              "sic_codes",            "sic",        "SIC code"],
  ["fpTechnologies",     "technologies",         "technology", "technology"],
  ["fpTechnologiesAll",  "technologies_all",     "technology", "technology"],
  ["fpTechnologiesNot",  "exclude_technologies", "technology", "technology"],
  ["fpJobLocations",     "job_locations",        "location",   "location"],
  ["fcIndustry",         "industries",           "industry",   "industry"],
  ["fcLocation",         "locations",            "location",   "location"],
  ["fcExcludeLocation",  "exclude_locations",    "location",   "location"],
  ["fcNaics",            "naics_codes",          "naics",      "NAICS code"],
  ["fcNaicsNot",         "exclude_naics_codes",  "naics",      "NAICS code"],
  ["fcSic",              "sic_codes",            "sic",        "SIC code"],
  ["fcSicNot",           "exclude_sic_codes",    "sic",        "SIC code"],
  ["fcTechnologies",     "technologies",         "technology", "technology"],
  ["fcTechnologiesAll",  "technologies_all",     "technology", "technology"],
  ["fcTechnologiesNot",  "exclude_technologies", "technology", "technology"],
  ["fcJobLocations",     "job_locations",        "location",   "location"]
];

/* Written out rather than derived from the singular: "industry" pluralizes to
   "industries", not "industrys", and there are only five nouns to cover. */
var COMBO_PLURALS = {
  "industry": "industries", "location": "locations", "technology": "technologies",
  "NAICS code": "NAICS codes", "SIC code": "SIC codes"
};
function comboPlural(noun){ return COMBO_PLURALS[noun] || (noun+"s"); }

/* The shapes Apollo enforces, and what to say when one is not met. Kept in step
   with tracker/apollo_vocab.py, which rejects the same values server-side: this
   copy exists to answer immediately, not to be the only guard. */
var COMBO_FORMATS = {
  naics: [/^[0-9]{2,5}$/, "NAICS codes are 2 to 5 digits here. Official codes are 6 digits, so drop the last one or two: 541511 becomes 54151."],
  sic:   [/^[0-9]{4}$/,   "SIC codes are exactly 4 digits."]
};

var COMBO = {};        /* input id -> {sel: [values]} */
var COMBO_CACHE = {};  /* vocabulary + query -> entries, so retyping is instant */
var COMBO_CUR = -1;    /* highlighted option in the one open list */

function comboSpec(key){
  for(var i=0;i<COMBO_SPECS.length;i++){ if(COMBO_SPECS[i][0]===key) return COMBO_SPECS[i]; }
  return null;
}
function comboState(key){
  if(!COMBO[key]) COMBO[key]={sel:[]};
  return COMBO[key];
}
/* The selected values of one combo, which is what a search is built from. */
function comboSel(key){ return comboState(key).sel; }

function renderComboChips(key){
  var wrap=document.getElementById(key+"Chips");
  if(!wrap) return;
  wrap.innerHTML=comboSel(key).map(function(v,i){
    return '<span class="cpi-chip-sel">'+esc(v)+
      '<button type="button" onclick="cpiComboRemove(\''+key+'\','+i+')" '+
      'aria-label="Remove '+esc(v)+'">&times;</button></span>';
  }).join("");
}

window.cpiComboRemove = function(key, i){
  comboSel(key).splice(i,1);
  renderComboChips(key);
};

function setComboValues(key, values){
  /* De-duplicated case-insensitively, because "Healthcare" and "healthcare" are
     one filter and two chips would only look like a mistake. */
  var seen={}, out=[];
  (values||[]).forEach(function(v){
    v=String(v==null?"":v).trim();
    if(!v) return;
    var k=v.toLowerCase();
    if(seen[k]) return;
    seen[k]=1; out.push(v);
  });
  comboState(key).sel=out;
  renderComboChips(key);
}

function addComboValue(key, value){
  var spec=comboSpec(key);
  var fmt=spec && COMBO_FORMATS[spec[2]];
  value=String(value==null?"":value).trim();
  if(fmt && !fmt[0].test(value)){
    /* Refused here rather than sent, because Apollo's own schema rejects it and
       the search would come back empty with nothing to explain why. */
    comboNote(key, fmt[1], value);
    return;
  }
  setComboValues(key, comboSel(key).concat([value]));
  var input=document.getElementById(key);
  if(input){ input.value=""; input.focus(); }
  closeCombo(key);
}

/* A refusal has to be visible in the list itself: the input is where the eye
   already is, and an alert for a mistyped code would be far too much.

   Stamped with the value it refused, because the request for that same text is
   usually still in flight and its response would otherwise land a moment later
   and replace the explanation with an ordinary list. */
function comboNote(key, message, forValue){
  var list=document.getElementById(key+"List");
  if(!list) return;
  COMBO_SPECS.forEach(function(other){ if(other[0]!==key) closeCombo(other[0]); });
  list.innerHTML='<div class="cpi-opt-none cpi-opt-warn">'+esc(message)+'</div>';
  list.classList.add("on");
  list.dataset.q=String(forValue==null?"":forValue);
  list.dataset.warn="1";
  liftComboGroup(key, true);
  placeComboList(key);
  COMBO_CUR=-1;
}

/* The labelled filter groups each carry a z-index, which makes every one of them
   its own stacking context. An open dropdown therefore cannot rise above the
   groups that come after it unless its own group is lifted for as long as it is
   open. */
function liftComboGroup(key, on){
  var list=document.getElementById(key+"List");
  var group=list && list.closest(".cpi-fset");
  if(group) group.classList.toggle("cpi-fset-lift", !!on);
}

/* A list that always opens downward at a fixed height runs off the bottom of the
   window whenever its field sits low on the page, which is the same "cannot see
   the options" problem in a different guise. Give it the room that is actually
   there, and open it upward when that side has more. */
function placeComboList(key){
  var list=document.getElementById(key+"List");
  var combo=document.getElementById(key+"Combo");
  if(!list||!combo) return;
  var GAP=5, EDGE=14, IDEAL=290, FLOOR=150;
  var r=combo.getBoundingClientRect();
  var below=window.innerHeight-r.bottom-GAP-EDGE;
  var above=r.top-GAP-EDGE;
  var up=below<Math.min(IDEAL, above);
  list.classList.toggle("up", up);
  list.style.maxHeight=Math.max(FLOOR, Math.min(IDEAL, up?above:below))+"px";
}

function closeCombo(key){
  var list=document.getElementById(key+"List");
  var input=document.getElementById(key);
  if(list){
    list.classList.remove("on");
    delete list.dataset.q;
    delete list.dataset.warn;
  }
  if(input) input.setAttribute("aria-expanded","false");
  liftComboGroup(key, false);
  COMBO_CUR=-1;
}

function closeAllCombos(){
  COMBO_SPECS.forEach(function(spec){ closeCombo(spec[0]); });
}

function renderComboList(key, entries, query, meta){
  var spec=comboSpec(key);
  var noun=spec?spec[3]:"value";
  var list=document.getElementById(key+"List");
  var input=document.getElementById(key);
  if(!list) return;
  /* Exactly one list open, enforced where a list actually opens rather than only
     on focus: these panels overlap each other, and two at once leaves one
     floating over the field being typed into. */
  COMBO_SPECS.forEach(function(other){ if(other[0]!==key) closeCombo(other[0]); });
  if(!entries.length){
    var fmt=spec && COMBO_FORMATS[spec[2]];
    if(fmt && query && !fmt[0].test(query.trim())){
      list.innerHTML='<div class="cpi-opt-none cpi-opt-warn">'+esc(fmt[1])+'</div>';
    } else {
      /* Never a dead end for a free-text vocabulary: an unlisted value is still a
         legitimate search, it just cannot be promised to match. */
      list.innerHTML='<div class="cpi-opt-none">No '+esc(noun)+' on file matches '+
        '<b>'+esc(query)+'</b>.'+(fmt?"":" Press Enter to filter on it anyway.")+'</div>';
    }
  } else {
    list.innerHTML=entries.map(function(e,i){
      var covers=e.covers||[];
      var tag=covers.length
        ? '<span class="cpi-opt-tag">'+covers.length+' industries</span>'
        : (e.confirmed?'<span class="cpi-opt-tag">seen in apollo</span>':"");
      /* A family says which industries it stands for; a code says what it means.
         Either way the second line is what stops the value being a guess. */
      var sub=e.note ? e.note : (covers.length ? covers.join(", ") : "");
      return '<button type="button" class="cpi-opt'+(i===0?" cur":"")+'" role="option" '+
        'data-value="'+esc(e.value)+'" data-combo="'+esc(key)+'">'+tag+
        '<b>'+esc(e.value)+'</b>'+(sub?'<s>'+esc(sub)+'</s>':"")+'</button>';
    }).join("")
    /* A capped list has to say so. Without this the list simply stops, which
       reads as the end of the vocabulary rather than the end of one page of it,
       and there is nothing on screen to suggest that typing would reach more. */
    + (meta && meta.truncated
        ? '<div class="cpi-opt-none cpi-opt-more">Showing the first '+
          esc(String(entries.length))+' of '+esc(String(meta.total))+
          ' '+esc(comboPlural(noun))+'. Keep typing to narrow the list.</div>'
        : "");
    COMBO_CUR=0;
  }
  list.classList.add("on");
  if(input) input.setAttribute("aria-expanded","true");
  /* Which query this list is the answer to. Enter reads it before trusting the
     highlighted option, because a list still showing the results of an earlier
     keystroke would otherwise commit a value the user never typed: entering the
     real 6-digit NAICS code 541511 added 5132, left highlighted from "software".
  */
  list.dataset.q=String(query==null?"":query);
  delete list.dataset.warn;
  liftComboGroup(key, true);
  placeComboList(key);
  /* Scrolled to the bottom of a previous query, the next one would open already
     scrolled past its first option. */
  list.scrollTop=0;
}

function comboUrl(vocab, q){
  /* Industry has its own endpoint and its own families; the rest share one. */
  if(vocab==="industry") return window.__CPI_INDUSTRIES_URL__+"?q="+encodeURIComponent(q);
  return window.__CPI_VOCAB_URL__+"?kind="+encodeURIComponent(vocab)+
    "&q="+encodeURIComponent(q);
}

function loadCombo(key){
  var spec=comboSpec(key);
  var input=document.getElementById(key);
  if(!spec||!input) return;
  var q=input.value.trim();
  var ck=spec[2]+"\n"+q.toLowerCase();
  /* Cached with its meta, not just its entries: a cached list that lost the
     "showing N of M" fact would silently claim to be the whole vocabulary the
     second time it was opened. */
  if(COMBO_CACHE[ck]){
    renderComboList(key, COMBO_CACHE[ck].entries, q, COMBO_CACHE[ck].meta);
    return;
  }
  fetch(comboUrl(spec[2], q))
    .then(function(r){ return r.json(); })
    .then(function(d){
      var entries=(d&&d.entries)||[];
      var meta={total:(d&&d.total), truncated:!!(d&&d.truncated)};
      COMBO_CACHE[ck]={entries:entries, meta:meta};
      var list=document.getElementById(key+"List");
      /* A refusal already shown for this exact text stands: the response is a
         list of near matches, but the value cannot be used whatever it resembles. */
      if(list && list.dataset.warn==="1" && list.dataset.q===q) return;
      /* Only render if the box still says what was asked for: a slow response to
         an earlier keystroke must not replace the list for a later one. */
      if(input.value.trim()===q) renderComboList(key, entries, q, meta);
    })
    .catch(function(){ /* the list is an aid; typing still works without it */ });
}

function moveComboCur(key, delta){
  var opts=document.querySelectorAll("#"+key+"List .cpi-opt");
  if(!opts.length) return;
  COMBO_CUR=(COMBO_CUR+delta+opts.length)%opts.length;
  opts.forEach(function(o,i){ o.classList.toggle("cur", i===COMBO_CUR); });
  opts[COMBO_CUR].scrollIntoView({block:"nearest"});
}

function initCombo(key){
  var input=document.getElementById(key);
  if(!input) return;
  var timer=null;
  input.addEventListener("input", function(){
    if(timer) clearTimeout(timer);
    timer=setTimeout(function(){ loadCombo(key); }, 120);
  });
  input.addEventListener("focus", function(){
    /* Only one list open at a time, or an earlier one stays floating over the
       field being typed into. */
    closeAllCombos();
    loadCombo(key);
  });
  input.addEventListener("keydown", function(e){
    if(e.key==="ArrowDown"){ e.preventDefault(); moveComboCur(key, 1); return; }
    if(e.key==="ArrowUp"){ e.preventDefault(); moveComboCur(key, -1); return; }
    if(e.key==="Escape"){ closeCombo(key); return; }
    if(e.key==="Enter"){
      e.preventDefault();
      var typed=input.value.trim();
      var list=document.getElementById(key+"List");
      /* Only a list rendered for exactly what is in the box may speak for it. */
      var fresh=list && list.dataset.q===typed && list.dataset.warn!=="1";
      var cur=fresh?document.querySelectorAll("#"+key+"List .cpi-opt")[COMBO_CUR]:null;
      if(cur){ addComboValue(key, cur.getAttribute("data-value")); }
      else if(typed){ addComboValue(key, typed); }
      return;
    }
    /* Backspace on an empty box removes the last chip, the convention every
       tag input follows. */
    if(e.key==="Backspace" && !input.value && comboSel(key).length){
      window.cpiComboRemove(key, comboSel(key).length-1);
    }
  });
  var list=document.getElementById(key+"List");
  if(list){
    /* Delegated, and mousedown rather than click, so choosing an option is not
       cancelled by the input losing focus first. */
    list.addEventListener("mousedown", function(e){
      var opt=e.target.closest(".cpi-opt");
      if(!opt) return;
      e.preventDefault();
      addComboValue(opt.getAttribute("data-combo"), opt.getAttribute("data-value"));
    });
  }
}

document.addEventListener("click", function(e){
  if(!e.target.closest(".cpi-combo")) closeAllCombos();
});
/* Room below the field changes as the page scrolls or the window resizes, so an
   already-open list is re-measured rather than left where it no longer fits. */
["scroll","resize"].forEach(function(evt){
  window.addEventListener(evt, function(){
    COMBO_SPECS.forEach(function(spec){
      var list=document.getElementById(spec[0]+"List");
      if(list && list.classList.contains("on")) placeComboList(spec[0]);
    });
  }, {passive:true});
});

/* Declarative filter specs, so a new Apollo filter is one line here plus one
   input in the template rather than another branch in a growing if-chain.
   kind: "str" (trimmed string) | "csv" (comma list -> array)
         | "one" (single value -> one-element array) | "num" (number or omit) */
var PEOPLE_FIELDS = [
  ["fpTitles","titles","csv"], ["fpCompanyDomain","company_domains","one"],
  ["fpKeywords","keywords","str"], ["fpLinkedinUrls","linkedin_urls","csv"],
  ["fpSegments","market_segments","csv"],
  ["fpJobTitles","job_titles","csv"],
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
  ["fcExcludeKeywords","exclude_keywords","csv"],
  ["fcSegments","market_segments","csv"],
  ["fcJobTitles","job_titles","csv"],
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
  COMBO_SPECS.forEach(function(spec){
    setComboValues(spec[0], []);
    var el=document.getElementById(spec[0]); if(el) el.value="";
    closeCombo(spec[0]);
  });
  window.cpiSyncCostLabels();
  document.querySelectorAll("#fpSeniority .cpi-chip.on, #fpEmailStatus .cpi-chip.on").forEach(function(c){ c.classList.remove("on"); });
  ["fpAdvanced","fcAdvanced"].forEach(function(id){ var el=document.getElementById(id); if(el) el.classList.remove("on"); });
  ["fpMoreBtn","fcMoreBtn"].forEach(function(id){ var el=document.getElementById(id); if(el) el.classList.remove("on"); });
};

function splitCsv(v){ return (v||"").split(",").map(function(s){return s.trim();}).filter(Boolean); }
function numVal(id){ var el=document.getElementById(id); if(!el||el.value==="") return null; var n=+el.value; return isNaN(n)?null:n; }
function chipVals(sel){ var out=[]; document.querySelectorAll(sel).forEach(function(c){ out.push(c.getAttribute("data-val")); }); return out; }

/* Every picker on the active tab, read from its chips. Two combos can name the
   same Apollo filter (industry exists on both tabs), which is safe because only
   one tab's prefix is ever asked for. */
function applyCombos(prefix, f){
  COMBO_SPECS.forEach(function(spec){
    if(spec[0].indexOf(prefix)!==0) return;
    var vals=comboSel(spec[0]);
    if(vals.length) f[spec[1]]=vals.slice();
  });
}

/* Reopening a saved search has to refill the pickers too, or the chips would show
   an empty filter bar above results that were produced by a full one. */
function restoreCombos(prefix, f){
  COMBO_SPECS.forEach(function(spec){
    if(spec[0].indexOf(prefix)!==0) return;
    setComboValues(spec[0], f[spec[1]]||[]);
    var el=document.getElementById(spec[0]); if(el) el.value="";
  });
}

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
    /* From the chips, not the input box: the box holds whatever is being typed
       right now, which is a half-finished word rather than a filter. */
    applyCombos("fp", f);
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
  applyCombos("fc", f);
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
    /* A search that failed is not a search that found nothing. This used to
       toast and then fall through, so renderResults() drew the "No matches"
       empty state over the top -- turning "Apollo never answered" into a claim
       that nobody matched, and on a reset wiping the rows already on screen to
       do it. Nothing below this runs: there is no page, no count and no
       history entry to write for a search that did not happen. */
    if(d && (d.error || d.search_failed)){
      toast(d.error || "Search failed.", "err");
      if(reset){
        STATE.results=[]; STATE.selected={}; STATE.total=null; STATE.rejected=null;
        STATE.companyUnconfirmed=0;
        wrap.innerHTML='<div class="cpi-empty"><svg viewBox="0 0 24 24" fill="none" '+
          'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+
          '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5v.01"/>'+
          "</svg><span>"+esc(d.error || "Search failed.")+"</span>"+
          '<button type="button" class="cpi-relax" onclick="cpiRunSearch(true)">Try again</button></div>';
        document.getElementById("cpiToolbar").style.display="none";
        document.getElementById("cpiLoadMore").style.display="none";
        updateBulk();
      }
      return;
    }
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
    STATE.rejected=(d&&d.rejected)||null;
    STATE.rejectedLabels=(d&&d.rejected_labels)||{};
    /* Rows Apollo returned with no employer domain to check against a
       company-domain search: kept (see search_people), each flagged on its own
       row, but the count belongs in the header too so the page does not read
       as having confirmed every row's employer when it could not. */
    STATE.companyUnconfirmed=(d&&d.company_unconfirmed)||0;
    /* Apollo has no industry filter, so an industry search needs the company
       lookup to verify what it found. Saying so beats appearing to ignore the
       toggle. */
    if(reset && d && d.industry_forced_company_detail){
      toast("Company details were needed to check the industry, so they were fetched for this page.", "ok");
    }
    /* Codes Apollo would have rejected were not sent. Said out loud, because the
       search that ran is not the search that was asked for, and an empty page
       would otherwise read as "no such companies". */
    if(d && d.invalid_codes){
      /* One toast, not one per vocabulary: a second would replace the first
         before it had been read. */
      var parts=[];
      Object.keys(d.invalid_codes).forEach(function(kind){
        var bad=d.invalid_codes[kind]||{};
        parts.push((bad.codes||[]).join(", ")+" was not used. "+(bad.hint||""));
      });
      toast(parts.join(" "), "err");
    }
    var items=(d&&d.results)||[];
    /* Advance only when a page actually came back, so Load more fetches the NEXT
       page instead of re-fetching page 1 and appending duplicate cards (which on
       the Companies tab also spent a fresh Apollo credit per click). */
    if(items.length){ STATE.page = (STATE.page||1) + 1; }
    if(d && d.total!==undefined && d.total!==null) STATE.total=d.total;
    /* Stamped here, where the rows arrive, so nothing downstream has to trust
       the toggle to know what they are. */
    if(items.length || reset) STATE.shownEntity = STATE.entity;
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
  if(STATE.shownEntity!=="people") return "";
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

/* Several of Apollo's filters are relevance hints rather than rules, so it returns
   rows that do not satisfy them. Those are removed after the fact, which makes a
   page of 24 arrive as 18: unexplained, that reads as Apollo being thin on matches
   rather than as the filters doing their job. Broken down per reason, so a filter
   quietly doing nothing is visible as a reason that never appears. */
/* One source of truth for what was removed and why, because two places now say
   it: the count line when some rows survived, and the empty state when none did.
   Returns null when nothing was removed. */
/* Which filter each rejection reason came from, so a reason can offer to relax
   the exact thing that caused it. A reason with no entry here is still shown,
   just not clickable: guessing at which control to clear would be worse than
   leaving it to the reader. */
var REJECT_FILTER = {
  company:"company_domains",
  industry:"industries", employees:"employee_min", revenue:"revenue_min",
  hq:"company_locations", technology:"technologies", title:"titles"
};

function rejectedReasons(){
  var r=STATE.rejected;
  if(!r) return null;
  var keys=Object.keys(r).filter(function(k){ return r[k]; });
  if(!keys.length) return null;
  var total=0;
  keys.forEach(function(k){ total+=r[k]; });
  keys.sort(function(a,b){ return r[b]-r[a]; });
  return { total: total, keys: keys, text: keys.map(function(k){
    return pmNum(r[k])+" "+(STATE.rejectedLabels[k]||k);
  }).join(", ") };
}

/* The same reasons, each one a button that drops the filter it blames and runs
   the search again. An explanation the reader has to act on by hand is only
   half of one, and the filter responsible is already known here. */
function rejectedActions(){
  var why=rejectedReasons();
  if(!why) return "";
  var r=STATE.rejected;
  var parts=why.keys.map(function(k){
    var label=pmNum(r[k])+" "+(STATE.rejectedLabels[k]||k);
    var filt=REJECT_FILTER[k];
    if(!filt) return '<span class="cpi-relax-dim">'+esc(label)+"</span>";
    return '<button type="button" class="cpi-relax" onclick="cpiRelax(\''+esc(k)+'\')" '+
      'title="Remove that filter and search again">'+esc(label)+"</button>";
  });
  return '<div class="cpi-relax-row"><span>Removed '+pmNum(why.total)+": </span>"+
    parts.join('<span class="cpi-relax-sep">·</span>')+"</div>";
}

/* Drop the filter a rejection reason blames, then re-run. Both halves of an
   employee or revenue band go together: clearing only the floor and leaving the
   ceiling would re-run a search that still excludes for the same reason. */
window.cpiRelax = function(reasonKey){
  var filt=REJECT_FILTER[reasonKey];
  if(!filt) return;
  window.cpiDropFilter(filt);
  if(filt==="employee_min") window.cpiDropFilter("employee_max");
  if(filt==="revenue_min") window.cpiDropFilter("revenue_max");
  if(filt==="technologies") window.cpiDropFilter("technologies_all");
  toast("Removed that filter. Searching again.", "ok");
  window.cpiRunSearch(true);
};

function rejectedNote(){
  var why=rejectedReasons();
  if(!why) return "";
  return ' <s>&middot; '+pmNum(why.total)+" removed: "+esc(why.text)+"</s>";
}

function unconfirmedNote(){
  var n=STATE.companyUnconfirmed;
  if(!n) return "";
  return ' <s>&middot; '+pmNum(n)+" with employer unconfirmed</s>";
}

/* "1 companies" is the kind of small wrongness that makes a page feel unfinished,
   and the singular case is common: a scoped search often returns exactly one. */
function noun(n){
  if(STATE.shownEntity==="people") return n===1 ? "person" : "people";
  return n===1 ? "company" : "companies";
}

function renderResults(){
  var wrap=document.getElementById("cpiResultsWrap");
  var bar=document.getElementById("cpiToolbar");
  if(!STATE.results.length){
    /* "No matches" is a claim about Apollo, and it is often not what happened:
       several of Apollo's filters are relevance hints, so rows come back that do
       not satisfy them and are removed afterwards. When that removes ALL of them
       the count and its breakdown were the thing that explained it -- and both
       live in the toolbar, which hides itself when there are no rows, so the
       explanation disappeared exactly when it was the whole story. */
    var why=rejectedReasons();
    wrap.innerHTML='<div class="cpi-empty"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/></svg><span>'+
      (why
        ? "Apollo returned "+pmNum(why.total)+" "+noun(why.total)+
          ", and on checking, none of them matched: "+esc(why.text)+
          ". Remove a filter to widen the search:"
        : "No matches. Try widening the filters.")+
      "</span>"+(why?rejectedActions():"")+"</div>";
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
      : "<b>"+pmNum(shown)+"</b> <s>"+noun(shown)+"</s>")
      + firmoNote() + rejectedNote() + unconfirmedNote();
  }
  wrap.innerHTML = STATE.view==="table"
    ? renderTable()
    : '<div class="cpi-grid">'+STATE.results.map(function(r,i){
        return STATE.shownEntity==="people" ? personCard(r,i) : companyCard(r,i);
      }).join("")+"</div>";
  updateBulk();
  syncSelectAllLabel();
}

/* ── Table view ──
   The cards read one person at a time, which is right for judging a single
   prospect and wrong for comparing twenty-four. Same rows, same selection, same
   actions: only the layout differs, so nothing here re-fetches or re-derives
   anything the cards did not already have.

   Every column is declared once with how to render it AND how to sort it, so a
   column cannot end up sorting by something other than what it displays. */
function tdTrunc(html, plain){
  return '<span class="cpi-td-t"'+(plain?' title="'+esc(plain)+'"':"")+'>'+html+'</span>';
}

function coCell(r){
  var name=r.organization_name||r.name;
  if(!name) return '<span class="cpi-td-dim">–</span>';
  var lg=safeUrl(r.organization_logo||r.logo_url);
  var dom=r.organization_domain||r.primary_domain;
  // Set only when this search was scoped to a domain and Apollo did not
  // return enough data on THIS row to confirm it against that domain -- not
  // a claim the person works elsewhere or that this is a different company,
  // which is why the row is on screen at all rather than dropped (see
  // search_people's employer_unconfirmed and search_companies'
  // domain_unconfirmed -- same shape, one on a person's employer, one on the
  // company row itself).
  var unconfirmedTitle=r.domain_unconfirmed
    ?"Searched by domain, but Apollo did not return a domain on this company's own record to confirm the match. Not ruled out, just unconfirmed."
    :"Searched by company domain, but Apollo did not return an employer domain for this specific person to confirm the match. Not ruled out, just unconfirmed.";
  var inner=(lg?'<img class="cpi-td-logo" src="'+esc(lg)+'" alt="" loading="lazy" onerror="this.style.display=\'none\'">':"")+
    '<span class="cpi-td-t">'+esc(name)+'</span>'+
    ((r.employer_unconfirmed||r.domain_unconfirmed)?'<span class="cpi-masked sm" title="'+esc(unconfirmedTitle)+'">unconfirmed</span>':"");
  return dom
    ? '<a class="cpi-td-co" href="'+esc(safeUrl("https://"+String(dom).replace(/^https?:\/\//i,"")))+
      '" target="_blank" rel="noopener noreferrer" title="'+esc(name+" · "+dom)+'">'+inner+"</a>"
    : '<span class="cpi-td-co" title="'+esc(name)+'">'+inner+"</span>";
}

var TABLE_COLS = {
  people: [
    {key:"full_name", label:"Name", cls:"w-name", sort:function(r){ return (r.full_name||"").toLowerCase(); },
     cell:function(r,i){
       var photo=safeUrl(r.photo_url);
       var av=photo
         ? '<span class="cpi-td-av ph"><img src="'+esc(photo)+'" alt="" loading="lazy" onerror="this.parentNode.textContent=\''+esc(initials(r.full_name))+'\'"></span>'
         : '<span class="cpi-td-av">'+esc(initials(r.full_name))+'</span>';
       var badges=(r.name_masked?'<span class="cpi-masked sm" title="Apollo masks this surname on the current plan. Enrich to reveal it.">masked</span>':"")+
                  (r.enriched?'<span class="cpi-badge ok sm">enriched</span>':"");
       var nm='<b>'+esc(r.full_name||"Unknown")+"</b>"+badges;
       return '<div class="cpi-td-person">'+av+
         (r.linkedin_url
            ? '<a class="cpi-td-nm" href="'+esc(safeUrl(r.linkedin_url))+'" target="_blank" rel="noopener noreferrer" title="'+esc(r.full_name||"")+'">'+nm+"</a>"
            : '<span class="cpi-td-nm" title="'+esc(r.full_name||"")+'">'+nm+"</span>")+"</div>";
     }},
    {key:"title", label:"Title", cls:"w-title", sort:function(r){ return cleanTitle(r.title||r.headline).toLowerCase(); },
     cell:function(r){ var t=cleanTitle(r.title||r.headline);
       return t?tdTrunc(esc(t), t):'<span class="cpi-td-dim">–</span>'; }},
    {key:"organization_name", label:"Company", cls:"w-co", sort:function(r){ return (r.organization_name||"").toLowerCase(); },
     cell:function(r){ return coCell(r); }},
    {key:"organization_industry", label:"Industry", cls:"w-ind", sort:function(r){ return (r.organization_industry||"").toLowerCase(); },
     cell:function(r){ return r.organization_industry?tdTrunc(esc(r.organization_industry), r.organization_industry):'<span class="cpi-td-dim">–</span>'; }},
    {key:"organization_employees", label:"Size", cls:"w-num", num:true, sort:function(r){ return +r.organization_employees||-1; },
     cell:function(r){ return r.organization_employees?'<span class="cpi-td-n">'+pmNum(r.organization_employees)+"</span>":'<span class="cpi-td-dim">–</span>'; }},
    {key:"location", label:"Location", cls:"w-loc", sort:function(r){ return (placeLine(r.city,r.state,r.country)||coHq(r)||"").toLowerCase(); },
     cell:function(r){
       var own=placeLine(r.city,r.state,r.country);
       if(own) return tdTrunc(esc(own), own);
       var hq=coHq(r);
       /* Labelled, because "their head office is in London" is a different fact
          from "this person is in London" and a rep acts differently on each. */
       return hq?tdTrunc(esc(hq)+' <s>HQ</s>', hq+" (company HQ)"):'<span class="cpi-td-dim">–</span>';
     }},
    {key:"email", label:"Email", cls:"w-mail", sort:function(r){ return (r.email||"~").toLowerCase(); },
     cell:function(r){
       if(r.email) return '<a class="cpi-td-mail" href="mailto:'+esc(r.email)+'" title="'+esc(r.email)+'">'+esc(r.email)+"</a>"+
         (r.email_status==="verified"?'<span class="cpi-badge ok sm">ok</span>':"");
       return '<span class="cpi-td-dim">Enrich <s>1 credit</s></span>';
     }}
  ],
  companies: [
    {key:"name", label:"Company", cls:"w-name", sort:function(r){ return (r.name||"").toLowerCase(); },
     cell:function(r){ return '<div class="cpi-td-person">'+coCell(r)+"</div>"; }},
    {key:"industry", label:"Industry", cls:"w-ind", sort:function(r){ return (r.industry||"").toLowerCase(); },
     cell:function(r){ return r.industry?tdTrunc(esc(r.industry), r.industry):'<span class="cpi-td-dim">–</span>'; }},
    {key:"estimated_num_employees", label:"Size", cls:"w-num", num:true, sort:function(r){ return +r.estimated_num_employees||-1; },
     cell:function(r){ return r.estimated_num_employees?'<span class="cpi-td-n">'+pmNum(r.estimated_num_employees)+"</span>":'<span class="cpi-td-dim">–</span>'; }},
    {key:"annual_revenue", label:"Revenue", cls:"w-num", num:true, sort:function(r){ return +r.annual_revenue||-1; },
     cell:function(r){ var m=pmMoney(r.annual_revenue,r.revenue_printed);
       return m?'<span class="cpi-td-n">'+esc(m)+"</span>":'<span class="cpi-td-dim">–</span>'; }},
    {key:"total_funding", label:"Funding", cls:"w-num", num:true, sort:function(r){ return +r.total_funding||-1; },
     cell:function(r){ return r.total_funding?'<span class="cpi-td-n">$'+pmNum(r.total_funding)+"</span>":'<span class="cpi-td-dim">–</span>'; }},
    {key:"hq", label:"HQ", cls:"w-loc", sort:function(r){ return (placeLine(r.city,r.state,r.country)||"").toLowerCase(); },
     cell:function(r){ var h=placeLine(r.city,r.state,r.country);
       return h?tdTrunc(esc(h), h):'<span class="cpi-td-dim">–</span>'; }},
    {key:"founded_year", label:"Founded", cls:"w-num", num:true, sort:function(r){ return +r.founded_year||-1; },
     cell:function(r){ return r.founded_year?'<span class="cpi-td-n">'+esc(String(r.founded_year))+"</span>":'<span class="cpi-td-dim">–</span>'; }}
  ]
};

/* Indices, not rows. Every action on this page addresses a row by its position
   in STATE.results (cpiToggleSelect, cpiOpenDetails, cpiOpenEnrich), so sorting
   a copy of the array would silently point every button at the wrong person.
   The display order is a permutation of indices and the original index travels
   with each rendered row. */
function tableOrder(cols){
  var order=STATE.results.map(function(_r,i){ return i; });
  if(!STATE.sortKey) return order;
  var col=null;
  cols.forEach(function(c){ if(c.key===STATE.sortKey) col=c; });
  if(!col) return order;
  var dir=STATE.sortDir==="desc"?-1:1;
  return order.slice().sort(function(a,b){
    var x=col.sort(STATE.results[a]), y=col.sort(STATE.results[b]);
    if(x<y) return -1*dir;
    if(x>y) return 1*dir;
    return a-b;                     /* stable: equal values keep Apollo's order */
  });
}

window.cpiSortBy = function(key){
  if(STATE.sortKey===key) STATE.sortDir = STATE.sortDir==="asc" ? "desc" : "asc";
  else { STATE.sortKey=key; STATE.sortDir="asc"; }
  renderResults();
};

function renderTable(){
  var cols=TABLE_COLS[STATE.shownEntity==="companies"?"companies":"people"];
  var order=tableOrder(cols);
  var head=cols.map(function(c){
    var on=STATE.sortKey===c.key;
    var arrow=on?(STATE.sortDir==="asc"?"↑":"↓"):"";
    return '<th class="'+c.cls+(c.num?" num":"")+(on?" sorted":"")+'">'+
      '<button type="button" onclick="cpiSortBy(\''+esc(c.key)+'\')" '+
      'aria-label="Sort by '+esc(c.label)+'">'+esc(c.label)+
      '<i class="cpi-th-ar">'+arrow+"</i></button></th>";
  }).join("");
  var body=order.map(function(i){
    var r=STATE.results[i];
    var sel=STATE.selected[r.id]?" sel":"";
    var cells=cols.map(function(c){
      return '<td class="'+c.cls+(c.num?" num":"")+'">'+c.cell(r,i)+"</td>";
    }).join("");
    return '<tr class="'+sel.trim()+(r.enriched?" enr":"")+'">'+
      '<td class="w-chk"><button class="cpi-card-check'+(sel?" on":"")+'" '+
        'onclick="cpiToggleSelect('+i+')" aria-label="Select row"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg></button></td>'+
      cells+
      '<td class="w-act"><div class="cpi-td-acts">'+
        '<button class="cpi-ghost-btn sm" onclick="cpiOpenDetails('+i+')">Details</button>'+
        (STATE.shownEntity==="people"
          ? '<button class="cpi-enrich-btn sm" onclick=\'cpiOpenEnrich("person",'+i+')\'>Enrich</button>'
          : '<button class="cpi-enrich-btn sm" onclick=\'cpiOpenEnrich("company",'+i+')\'>Enrich</button>')+
      "</div></td></tr>";
  }).join("");
  /* Sorting reorders the rows that are LOADED, which is not the same as the top
     of the whole result set. With more matches than rows on screen, saying so is
     the difference between "the biggest companies that matched" and "the biggest
     of the two dozen fetched so far".

     Guarded on a column that really exists in this tab's set: the two tabs have
     different columns, so a sort key left over from the other one sorts nothing,
     and a note claiming a sort that is not in effect is its own small lie. */
  var sortLive=cols.some(function(c){ return c.key===STATE.sortKey; });
  var note = (sortLive && STATE.total && STATE.total>STATE.results.length)
    ? '<div class="cpi-tbl-note">Sorted within the '+pmNum(STATE.results.length)+
      ' rows loaded so far, not across all '+pmNum(STATE.total)+
      ' matches. Load more to sort over more of them.</div>'
    : "";
  return '<div class="cpi-tbl-wrap"><table class="cpi-tbl"><thead><tr>'+
    '<th class="w-chk"></th>'+head+'<th class="w-act"></th>'+
    "</tr></thead><tbody>"+body+"</tbody></table></div>"+note;
}

window.cpiSetView = function(view){
  STATE.view = view==="table" ? "table" : "cards";
  try{ localStorage.setItem("cpi-view", STATE.view); }catch(e){}
  document.querySelectorAll("#cpiViewToggle button").forEach(function(b){
    b.classList.toggle("on", b.getAttribute("data-view")===STATE.view);
  });
  /* The table takes the whole width and the chat moves below it. Done here
     rather than in renderResults so the layout follows the CHOSEN view even
     before a search has run. */
  var lay=document.querySelector(".cpi-layout");
  if(lay) lay.classList.toggle("wide", STATE.view==="table");
  if(STATE.results.length) renderResults();
};

/* Apollo returns some titles with the same role stated twice, joined by a comma,
   an ampersand or a slash: "Marketing Director, Marketing Director" and
   "Director, Marketing & Director, Marketing" are both real values from one
   search. Only an EXACT repeat is collapsed, so "Director, Marketing & Sales"
   and "VP Sales / EMEA" survive untouched. The stored value is never changed:
   this is a display cleanup, and the Details view and every export still carry
   Apollo's string as Apollo sent it. */
function cleanTitle(t){
  var s=String(t==null?"":t).trim();
  if(!s) return "";
  /* Each separator is tried in turn and a split that does not come out all-equal
     simply falls through to the next, so the order below is readability only and
     does not change any result: "Director, Marketing & Director, Marketing"
     fails the comma split (three unequal parts) and is then caught by " & ".
     Splitting on ONE separator is what matters; which one is found first is not. */
  var seps=[/\s+&\s+/, /\s+\/\s+/, /\s+\|\s+/, /\s*,\s*/];
  for(var i=0;i<seps.length;i++){
    var parts=s.split(seps[i]);
    if(parts.length<2) continue;
    var first=parts[0].trim().toLowerCase();
    if(!first) continue;
    var same=parts.every(function(p){ return p.trim().toLowerCase()===first; });
    if(same) return parts[0].trim();
  }
  return s;
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
  /* Apollo returns this as a FRACTION: 0.19 is 19% growth. Every observed value
     in this repo's fixtures is one (0.08, 0.19), and the External Usage export has
     multiplied the same Apollo field by 100 unconditionally since long before this
     page existed.

     This used to scale only values inside ±1, on the belief that some records
     arrive as whole percent. The cost of that hedge was silent and one-directional:
     a company that grew 150% arrives as 1.5 and was printed as "+1.5%", so the
     fastest-growing employers on the page looked like the flattest. One
     convention, matching the rest of the app. */
  n = n * 100;
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
    /* Apollo's own logo, or none. This used to fall back to Google's public
       favicon service, which meant the browser announced every company on the
       page to a third party, one request per card, as the grid was scrolled:
       the prospect list being worked, leaving the building to buy a 16px image.
       Where Apollo has no logo the row shows the name alone, which is what it
       was always going to show if the favicon 404'd anyway. */
    var lg=safeUrl(p.organization_logo);
    var co=(lg?'<img class="cpi-row-logo" src="'+esc(lg)+'" alt="" loading="lazy" onerror="this.style.display=\'none\'"> ':"")
      +'<b>'+esc(p.organization_name)+'</b>'
      +(p.employer_unconfirmed?'<span class="cpi-masked sm" title="Searched by company domain, but Apollo did not return an employer domain for this specific person to confirm the match. Not ruled out, just unconfirmed.">unconfirmed</span>':"");
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
    rows.push('<div class="cpi-row hint">'+IC_ML+'<span>Enrich for email &amp; history <s>&middot; 1 credit</s></span></div>');
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
  /* Apollo returns a logo for companies on the search itself; no third-party
     favicon lookup, for the same reason as the person card above. */
  var src=safeUrl(c.logo_url);
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
      '<div style="min-width:0"><div class="cpi-card-name">'+esc(c.name||"Unknown")+
      (c.domain_unconfirmed?'<span class="cpi-masked sm" title="Searched by domain, but Apollo did not return a domain on this company\'s own record to confirm the match. Not ruled out, just unconfirmed.">unconfirmed</span>':"")+'</div>'+
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
/* Names the count, because the line right next to this button says "Showing 24 of
   79,421 matches in Apollo" and a button called "Select all" sitting beside that
   reads as selecting all 79,421. It selects the 24 that are loaded. */
function syncSelectAllLabel(){
  var btn=document.getElementById("cpiSelectAll");
  if(!btn||!STATE.results.length) return;
  var all=STATE.results.every(function(r){ return !rowKey(r) || STATE.selected[r.id]; });
  var n=STATE.results.length;
  btn.lastChild.textContent = all ? (" Clear "+n) : (" Select these "+n);
}
function updateBulk(){
  var bar=document.getElementById("cpiBulk"), n=selectedRows().length;
  if(!bar) return;
  bar.classList.toggle("on", n>0);
  var lbl=document.getElementById("cpiBulkN");
  if(lbl) lbl.innerHTML="<b>"+n+"</b> selected";
  var enr=document.getElementById("cpiBulkEnrich");
  if(enr) enr.style.display = STATE.shownEntity==="people" ? "" : "none";
  /* What the click will cost, on the button, before it is clicked. Only rows
     that are not already enriched can charge, which is the same set
     cpiEnrichSelected actually sends, so the quoted price is the real one.
     Says "up to" because a row Apollo has cached costs nothing. */
  if(enr && STATE.shownEntity==="people"){
    var pay=selectedRows().filter(function(r){ return r.id && !r.enriched; }).length;
    var lab=enr.querySelector(".cpi-bulk-cost");
    if(!lab){
      lab=document.createElement("s");
      lab.className="cpi-bulk-cost";
      enr.appendChild(lab);
    }
    lab.textContent = pay ? (" · up to "+pay+" credit"+(pay===1?"":"s")) : " · free";
  }
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
    var msg="Revealed "+n+" "+(n===1?"profile":"profiles")+(bits.length?" · "+bits.join(" · "):"");
    /* People Apollo never answered for are not people Apollo has nothing on,
       and the difference decides what the reader does next. Reported in the
       same toast rather than a second one, which would replace this before it
       had been read, and as an error, because "Revealed 40 profiles" on its own
       reads as the whole job done. They stay unenriched and unbilled, so the
       same selection can simply be sent again. */
    if(d.unreachable){
      toast(msg+" · Apollo did not answer for "+d.unreachable+
            ", so they were neither revealed nor ruled out. They cost nothing "+
            "and are still selected: try again in a moment.", "err");
    } else {
      toast(msg, "ok");
    }
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
  /* The rejection counts are part of what produced this page: without them the
     file reads as everything Apollo offered, when it is deliberately less. Left
     out of a hand-picked selection, which is not "the results of this search"
     any more and so cannot claim the search's bookkeeping either. */
  var meta = onlySelected ? {} : { total: STATE.total, rejected: STATE.rejected||{} };
  doCpiDownload(STATE.shownEntity||STATE.entity, rows, fmt, filters, meta);
};

/* ── History ── */
/* isNewSearch distinguishes "Search" from "Load more". A new search starts a new
   entry; paging grows the entry the server already gave us an id for, so one
   search is one row in the drawer no matter how deep it is paged. */
function saveHistory(isNewSearch){
  if(!STATE.results.length) return;
  if(isNewSearch){ STATE.historyId = null; STATE.historyTruncated = false; }
  fetch(window.__CPI_HISTORY_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ entity: STATE.shownEntity||STATE.entity,
                           filters: STATE.lastFilters||{},
                           total: STATE.total, rows: STATE.results,
                           replace_id: STATE.historyId||0 })
  }).then(function(r){ return r.json(); }).then(function(d){
    if(d && d.id) STATE.historyId = d.id;
    /* The server keeps only the first _CPI_HISTORY_MAX_ROWS of a paged
       search; past that, "Load more" keeps growing the grid but the saved
       entry stops growing with it. Told once per entry rather than on every
       page past the cap, since the fact does not change on the second page
       that trips it. */
    if(d && d.truncated && !STATE.historyTruncated){
      STATE.historyTruncated = true;
      toast("History keeps the first "+d.kept+" of this search's "+d.of+
            " rows; paging further won't add more to the saved entry.", "err");
    }
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
  restoreCombos(STATE.entity==="people"?"fp":"fc", f);
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
        /* A bulk reveal. Its rows are in search-row shape, so unlike a single
           enriched contact it reopens into the grid and exports like a search. */
        var isRevealed=e.entity==="revealed";
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
        }else if(isRevealed){
          ic=IC_PERSON; cls="ct";
          meta=String(e.count||0)+" contact"+(e.count===1?"":"s")+
            " · already paid for · "+when;
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
    var m=String(el.getAttribute("onclick")||"").match(/\d+/);
    return m?m[0]:null;
  }).filter(Boolean);
  if(!ids.length) return;
  /* The drawer now also holds saved answers and enriched contacts, so the
     confirmation says "entries" rather than promising only searches will go. */
  if(!window.confirm("Delete all "+ids.length+" saved "+(ids.length===1?"entry":"entries")+
                     " (searches, answers and enriched contacts)? This cannot be undone.")) return;
  /* Each delete is its own request, so some can fail while others succeed. It
     used to swallow every failure and then say "Cleared history." regardless,
     which told the user their contact data was gone when it was still there. */
  Promise.all(ids.map(function(id){
    return fetch(window.__CPI_HISTORY_URL__+"/"+id, { method:"DELETE" })
      .then(function(r){ return r.json(); })
      .then(function(d){ return !!(d && d.deleted); })
      .catch(function(){ return false; });
  })).then(function(results){
    var gone=results.filter(Boolean).length, left=results.length-gone;
    window.cpiOpenHistory();
    if(!left){ toast("Cleared history.", "ok"); return; }
    toast(gone?("Deleted "+gone+", but "+left+" could not be deleted. Try again.")
             :"Nothing could be deleted. Try again in a moment.", "err");
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
    /* These rows are of the kind the entry recorded, whatever the panel was
       showing a moment ago. */
    STATE.shownEntity = STATE.entity;
    STATE.results = d.rows||[];
    STATE.total = d.total;
    STATE.selected = {};
    STATE.page = 1;
    /* Describes what the LAST FETCH did, so it cannot survive into a set of rows
       it never applied to. Left standing, the export of a reopened entry printed
       the previous search's "Removed on checking" counts as if they were this
       one's, which is a wrong statement about the file rather than a missing
       one. */
    STATE.rejected = null; STATE.rejectedLabels = {}; STATE.firmo = null;
    STATE.companyUnconfirmed = 0;
    var revealed = d.entity==="revealed";
    if(revealed){
      /* Not a search: nobody typed filters to get these people, so the panel is
         left exactly as the user had it and no filters are claimed for the rows. */
      STATE.lastFilters = {};
      STATE.historyId = null;
    }else{
      /* Put the filters back on screen and keep them as lastFilters, so what the
         panel shows, what a re-run would query, and what the entry is labelled
         with all stay the same thing. */
      STATE.lastFilters = d.filters||{};
      applyFiltersToForm(STATE.lastFilters);
      /* Continuing this reopened search grows its own entry rather than forking a
         near-duplicate in the drawer. */
      STATE.historyId = d.id||null;
    }
    renderResults();
    document.getElementById("cpiLoadMore").style.display="none";
    window.cpiCloseHistory();
    toast("Reopened "+(STATE.results.length)+(revealed?" revealed contacts":" saved rows")+
          " (no credits spent)", "ok");
  }).catch(function(){ toast("Could not reopen that search.", "err"); });
};
window.cpiDeleteHistory = function(id){
  fetch(window.__CPI_HISTORY_URL__+"/"+id, { method:"DELETE" })
    .then(function(r){ return r.json(); })
    .then(function(d){
      window.cpiOpenHistory();
      /* Refreshing the list is not proof the row went: the route answers
         {deleted:false} for an id it did not remove, and reporting nothing left
         the user believing deleted contact data was gone. */
      if(!(d && d.deleted)) toast("Could not delete that entry.", "err");
    })
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
    /* A lookup that never reached Apollo says so. Telling a reader "Apollo has
       no profile for this person" on the strength of a failed request is the
       one thing this modal must not do: they act on it by giving up. */
    return p&&p.lookup_failed
      ? '<div class="pm-sec"><h4 class="pm-h4">Profile<s>apollo</s></h4><div class="pm-empty"><b>Lookup failed</b>Apollo did not answer, so this person was neither found nor ruled out. Try again in a moment.</div></div>'
      : '<div class="pm-sec"><h4 class="pm-h4">Profile<s>apollo</s></h4><div class="pm-empty"><b>No match found</b>Apollo has no full profile for this person beyond what the search already returned.</div></div>';
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
    return c&&c.lookup_failed
      ? '<div class="pm-sec"><h4 class="pm-h4">Profile<s>apollo</s></h4><div class="pm-empty"><b>Lookup failed</b>Apollo did not answer, so this company was neither found nor ruled out. Try again in a moment.</div></div>'
      : '<div class="pm-sec"><h4 class="pm-h4">Profile<s>apollo</s></h4><div class="pm-empty"><b>No match found</b>Apollo has no organization record for this company.</div></div>';
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
      /* What it actually cost, rather than the button's static price. A miss is
         free and a record already bought is free, and both used to be billed to
         the reader as one credit. */
      if(d && d.credits!==undefined){
        if(d.credits>0) toast(d.credits+" Apollo credit spent.", "ok");
        else if(p && p.matched) toast("Already enriched, so this cost nothing.", "ok");
        else toast("No match, so this cost nothing.", "ok");
      }
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
  var logo=safeUrl(pre?g("logo"):r.logo_url);
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
      "Apollo's free search returns identity and role only. Enriching adds their verified work email and its status, their own city and country, full career history and photo"+
      (r.name_masked?", and reveals the surname Apollo is masking here":"")+
      ". That costs 1 credit, and is cached afterwards so reopening this person is free. "+
      "A direct or mobile number comes back only for people already in the connected Apollo or CRM account: Apollo reveals any other number through a separate metered request this tool does not make, so treat a phone as a bonus rather than the reason to spend.</div>"+
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
    : companyHero({name:r.name, logo:r.logo_url, industry:r.industry,
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
      /* The question moved off the pinned company (it asked about an industry, a
         place, a size), so stop sending that company with the next turn. Without
         this the pin outlives the subject: the server drops it for one answer and
         the client hands it straight back on the following question. Cleared
         before the line below so a reply that carries a company of its own still
         wins. */
      if(d && d.clear_context){ ACTIVE_COMPANY = null; }
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

/* ── Saying the query back ── */
/* What a filter is called in the chip bar. Only keys that are really filters:
   include_similar_titles and company_detail are settings about HOW to search,
   not part of what is being asked for, and showing them as removable chips
   would invite someone to "remove" a checkbox. */
var QBAR_LABELS = {
  titles:"Title", seniorities:"Seniority", industries:"Industry",
  keywords:"Keywords", name:"Name", company_domains:"At company",
  domains:"Domain", person_locations:"Person in", company_locations:"HQ in",
  locations:"HQ in", exclude_locations:"Not HQ in", job_locations:"Hiring in",
  technologies:"Uses", technologies_all:"Uses all", exclude_technologies:"Not using",
  naics_codes:"NAICS", exclude_naics_codes:"Not NAICS",
  sic_codes:"SIC", exclude_sic_codes:"Not SIC",
  market_segments:"Segment", job_titles:"Hiring for", email_status:"Email",
  linkedin_urls:"LinkedIn", exclude_keywords:"Excluding",
  employee_min:"Employees ≥", employee_max:"Employees ≤",
  revenue_min:"Revenue ≥", revenue_max:"Revenue ≤",
  founded_min:"Founded ≥", founded_max:"Founded ≤",
  total_funding_min:"Funding ≥", total_funding_max:"Funding ≤",
  latest_funding_min:"Last round ≥", latest_funding_max:"Last round ≤",
  num_jobs_min:"Open jobs ≥", num_jobs_max:"Open jobs ≤",
  headcount_growth_min:"Growth ≥", headcount_growth_max:"Growth ≤",
  headcount_growth_months:"Growth window", yoe_min:"Experience ≥",
  yoe_max:"Experience ≤", days_in_title_min:"In role ≥",
  days_in_title_max:"In role ≤", job_posted_after:"Jobs posted after",
  job_posted_before:"Jobs posted before", funded_after:"Funded after",
  funded_before:"Funded before", department_counts:"Department"
};
/* Not filters: settings about how the search runs, or values already shown by
   their own control. */
var QBAR_SKIP = {include_similar_titles:1, company_detail:1,
                 include_unknown_founded_year:1, max_people:1, max_companies:1};

/* Chip filters carry Apollo's own value ("c_suite"), which is not what the chip
   says on screen ("C-Suite"). Read the label back off the chip rather than
   keeping a second copy of the mapping here: the group is the source of truth
   for its own wording, so adding a seniority cannot make the bar disagree. */
function chipLabels(groupSel, values){
  var byVal={};
  document.querySelectorAll(groupSel+" .cpi-chip").forEach(function(c){
    byVal[c.getAttribute("data-val")]=(c.textContent||"").trim();
  });
  return (values||[]).map(function(v){ return byVal[v]||String(v); });
}

function qbarValue(key, v){
  if(key==="days_in_title_min"||key==="days_in_title_max")
    return Math.round(v/30)+" mo";
  if(key==="employee_max" && +v>=999999999) return "";
  if(key==="seniorities") return chipLabels("#fpSeniority", v).join(", ");
  if(key==="email_status") return chipLabels("#fpEmailStatus", v).join(", ");
  if(Array.isArray(v)) return v.join(", ");
  if(v && typeof v==="object") return Object.keys(v).join(", ");
  return String(v);
}

/* Is anything actually being asked for? Same reading of gatherFilters() the bar
   uses, so "the bar is empty" and "there is nothing to count" are one fact. */
function queryHasFilters(){
  var f=gatherFilters();
  return Object.keys(f).some(function(k){
    if(QBAR_SKIP[k]) return false;
    var v=f[k];
    if(v===null||v===undefined||v==="") return false;
    if(Array.isArray(v)) return v.length>0;
    return true;
  });
}

/* Bands, so "Employees ≥51" and "Employees ≤200" read as one "Employees 51-200"
   rather than two chips saying half a thing each. Eight of these exist, which
   would otherwise be sixteen chips for one mental filter. */
var QBAR_BANDS = [
  ["employee_min","employee_max","Employees",""],
  ["revenue_min","revenue_max","Revenue","$"],
  ["founded_min","founded_max","Founded",""],
  ["total_funding_min","total_funding_max","Funding","$"],
  ["latest_funding_min","latest_funding_max","Last round","$"],
  ["num_jobs_min","num_jobs_max","Open jobs",""],
  ["headcount_growth_min","headcount_growth_max","Growth","%"],
  ["yoe_min","yoe_max","Experience"," yrs"],
  ["days_in_title_min","days_in_title_max","In role"," mo"]
];

function bandText(lo, hi, unit, key){
  var f=function(n){
    if(key==="days_in_title_min") n=Math.round(n/30);
    if(n>=999999999) return null;
    var s=(unit==="$") ? "$"+pmNum(n) : pmNum(n)+(unit==="$"?"":unit);
    return s;
  };
  var a=(lo===undefined||lo===null)?null:f(lo);
  var b=(hi===undefined||hi===null)?null:f(hi);
  if(a&&b) return a+" to "+b;
  if(a) return a+"+";
  if(b) return "up to "+b;
  return "";
}

/* The active filters, drawn from the same gatherFilters() the search uses, so
   the bar cannot describe a different query than the one that would run. */
function renderQueryBar(){
  var bar=document.getElementById("cpiQbar");
  if(!bar) return;
  var f=gatherFilters();
  var chips=[], done={};
  QBAR_BANDS.forEach(function(b){
    var lo=f[b[0]], hi=f[b[1]];
    var has=(lo!==undefined&&lo!==null&&lo!=="")||(hi!==undefined&&hi!==null&&hi!=="");
    if(!has) return;
    done[b[0]]=1; done[b[1]]=1;
    var txt=bandText(lo, hi, b[3], b[0]);
    if(!txt) return;
    chips.push('<span class="cpi-qchip"><i>'+esc(b[2])+'</i>'+esc(txt)+
      '<button type="button" onclick="cpiDropFilter(\''+esc(b[0])+'\');cpiDropFilter(\''+esc(b[1])+'\')" '+
      'aria-label="Remove the '+esc(b[2])+' filter">&times;</button></span>');
  });
  Object.keys(f).forEach(function(k){
    if(QBAR_SKIP[k] || done[k]) return;
    var v=f[k];
    if(v===null||v===undefined||v===""||(Array.isArray(v)&&!v.length)) return;
    var txt=qbarValue(k, v);
    if(!txt) return;
    chips.push('<span class="cpi-qchip"><i>'+esc(QBAR_LABELS[k]||k)+'</i>'+esc(txt)+
      '<button type="button" onclick="cpiDropFilter(\''+esc(k)+'\')" '+
      'aria-label="Remove '+esc(QBAR_LABELS[k]||k)+' filter">&times;</button></span>');
  });
  if(!chips.length){ bar.style.display="none"; bar.innerHTML=""; return; }
  bar.style.display="";
  bar.innerHTML='<span class="cpi-qbar-lbl">Searching for</span>'+chips.join("")+
    '<button type="button" class="cpi-qbar-clear" onclick="cpiClearFilters();cpiFiltersChanged()">Clear all</button>';
}

/* Remove one filter by its Apollo key, whichever control happens to hold it.
   Keyed off the same spec tables gatherFilters reads, so a filter added later
   becomes removable without touching this. */
window.cpiDropFilter = function(key){
  var pre = STATE.entity==="people" ? "fp" : "fc";
  (STATE.entity==="people"?PEOPLE_FIELDS:COMPANY_FIELDS).forEach(function(spec){
    if(spec[1]===key){ var el=document.getElementById(spec[0]); if(el) el.value=""; }
  });
  COMBO_SPECS.forEach(function(spec){
    if(spec[0].indexOf(pre)===0 && spec[1]===key) setComboValues(spec[0], []);
  });
  if(key==="company_domains"){ STATE.pinnedOrgId=null; STATE.pinnedOrgName=null; }
  if(key==="seniorities") document.querySelectorAll("#fpSeniority .cpi-chip.on").forEach(function(c){ c.classList.remove("on"); });
  if(key==="email_status") document.querySelectorAll("#fpEmailStatus .cpi-chip.on").forEach(function(c){ c.classList.remove("on"); });
  if(key==="employee_min"||key==="employee_max"){
    var emp=document.getElementById(pre+"EmpRange"); if(emp) emp.value="";
  }
  if(key==="department_counts"){
    ["DeptName","DeptMin","DeptMax"].forEach(function(s){
      var el=document.getElementById(pre+s); if(el) el.value="";
    });
  }
  cpiFiltersChanged();
};

/* ── The free match count ── */
var COUNT_T=null, COUNT_SEQ=0;

function countEl(){
  return document.getElementById(STATE.entity==="people" ? "cpiLiveCount" : "cpiLiveCountCo");
}

/* Debounced, and only for People: the Companies endpoint bills a credit per
   call, so there is deliberately no live count on that tab (the server refuses
   it too, rather than trusting this to be the only guard).

   Nothing is counted until at least one real filter is set. Counting an empty
   filter set means asking Apollo how many people it has, which is a number in
   the hundreds of millions that describes the database rather than the search:
   noise on load, and a request nobody asked for. */
function scheduleCount(){
  /* Both are cleared first, every time. A count belongs to one filter set on one
     tab, so leaving the People number in place while the Companies tab is open
     means switching back shows a figure for filters that have since changed, and
     a "counting…" that never resolves. Clearing both makes a stale number
     impossible rather than unlikely. */
  ["cpiLiveCount","cpiLiveCountCo"].forEach(function(id){
    var e=document.getElementById(id);
    if(e){ e.textContent=""; e.title=""; }
  });
  if(COUNT_T) clearTimeout(COUNT_T);
  var el=countEl();
  if(!el) return;
  if(STATE.entity!=="people") return;
  if(!queryHasFilters()) return;
  el.textContent="counting…";
  COUNT_T=setTimeout(runCount, 420);
}

function runCount(){
  var seq=++COUNT_SEQ;
  var filters=gatherFilters();
  fetch(window.__CPI_COUNT_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({entity: STATE.entity, filters: filters})
  }).then(function(r){ return r.json(); }).then(function(d){
    /* A slow answer to an earlier keystroke must not overwrite a later one. */
    if(seq!==COUNT_SEQ) return;
    var el=countEl();
    if(!el) return;
    if(!d || d.count===null || d.count===undefined){
      el.textContent = (d && d.reason) ? "" : "";
      el.title = (d && d.reason) || "";
      return;
    }
    /* "about", because Apollo's total counts what IT matched and the
       verification pass then drops rows that do not really qualify. The honest
       number is an upper bound, and saying so is the whole point. */
    el.innerHTML = (d.approx?"about ":"")+"<b>"+pmNum(d.count)+"</b> "+
      (d.count===1?"match":"matches");
    el.title = d.approx
      ? "Apollo's own count. Some of these are removed when the filters are "+
        "re-checked against each record, so the page will show this many or fewer."
      : "Apollo's own count for these filters.";
  }).catch(function(){
    var el=countEl(); if(el) el.textContent="";
  });
}

/* Anything that changes the query: refresh what the bar says and what the count
   says, together, so they can never disagree. */
window.cpiFiltersChanged = function(){
  renderQueryBar();
  syncMoreBadge();
  scheduleCount();
};

/* How many filters are set inside the collapsed "More filters" panel. Without
   this the long tail is genuinely invisible: it is collapsed by default, so a
   revenue floor set last week is still narrowing today's search with nothing on
   screen to say so. The chip bar shows WHICH; this shows there are some even
   when the bar is scrolled past. */
function syncMoreBadge(){
  ["fp","fc"].forEach(function(pre){
    var panel=document.getElementById(pre+"Advanced");
    var btn=document.getElementById(pre+"MoreBtn");
    if(!panel||!btn) return;
    var n=0;
    panel.querySelectorAll("input, select").forEach(function(el){
      if(el.type==="checkbox"){ if(el.checked && el.id!=="fpCompanyDetail") n++; }
      else if((el.value||"").trim()) n++;
    });
    /* The pickers keep their value in chips, not in the input. */
    COMBO_SPECS.forEach(function(spec){
      if(spec[0].indexOf(pre)!==0) return;
      var el=document.getElementById(spec[0]+"Combo");
      if(el && panel.contains(el) && comboSel(spec[0]).length) n++;
    });
    var base=btn.getAttribute("data-base") || btn.textContent.trim();
    btn.setAttribute("data-base", base);
    btn.innerHTML = n ? esc(base)+' <b class="cpi-more-n">'+n+"</b>"
                      : esc(base);
  });
}

/* Apollo can only filter headcount by fixed buckets, and the select offers
   exactly those. A sentence says "50 to 200 people", which is not one of them,
   so the requested span is snapped to the bucket it overlaps most. Without this
   the value matched no option, the select stayed empty, and the size quietly
   went missing from a search whose note had just claimed to set it.

   Read off the select's own options rather than hardcoded here, so the buckets
   have one definition. */
function snapEmployeeBucket(f, selId){
  var sel=document.getElementById(selId);
  if(!sel) return;
  var lo=f.employee_min, hi=f.employee_max;
  if(lo===undefined && hi===undefined) return;
  lo=(typeof lo==="number")?lo:0;
  hi=(typeof hi==="number" && hi<999999999)?hi:Infinity;
  var best=null, bestOverlap=0;
  for(var i=0;i<sel.options.length;i++){
    var v=sel.options[i].value;
    if(!v) continue;
    var p=v.split(",");
    var bl=+p[0], bh=p[1]?+p[1]:Infinity;
    var ov=Math.min(hi,bh)-Math.max(lo,bl);
    if(ov>bestOverlap){ bestOverlap=ov; best=v; }
  }
  if(best){
    var q=best.split(",");
    f.employee_min=+q[0];
    f.employee_max=q[1]?+q[1]:999999999;
  } else {
    /* Nothing Apollo can express: drop it rather than leave a value the form
       cannot show and the search would not honour. */
    delete f.employee_min; delete f.employee_max;
  }
}

/* ── One sentence to filters ── */
window.cpiParseQuery = function(){
  var input=document.getElementById("cpiAskInput");
  var btn=document.getElementById("cpiAskBtn");
  var note=document.getElementById("cpiAskNote");
  if(!input) return;
  var q=input.value.trim();
  if(!q){ input.focus(); return; }
  if(btn){ btn.disabled=true; btn.textContent="Reading…"; }
  fetch(window.__CPI_PARSE_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({q:q})
  }).then(function(r){ return r.json(); }).then(function(d){
    if(btn){ btn.disabled=false; btn.textContent="Fill filters"; }
    if(!d || d.error){ toast((d&&d.error)||"Could not read that query.", "err"); return; }
    var f=d.filters||{};
    if(!Object.keys(f).length){
      toast("Nothing recognisable to filter on in that. Try naming a role, an industry, a place or a size.", "err");
      return;
    }
    if(d.entity && d.entity!==STATE.entity) window.cpiSetEntity(d.entity);
    snapEmployeeBucket(f, STATE.entity==="people" ? "fpEmpRange" : "fcEmpRange");
    applyFiltersToForm(f);
    cpiFiltersChanged();
    /* Told, not just done. Counted off the chip bar rather than off what the
       parser returned, because those are not always the same number: a value
       Apollo cannot express (a headcount span that fits no bucket) is dropped
       on the way in, and claiming to have set it would be a statement the form
       does not support. */
    var landed=document.querySelectorAll("#cpiQbar .cpi-qchip").length;
    if(note){
      note.style.display="";
      note.innerHTML = landed
        ? ("Filled "+landed+" filter"+(landed===1?"":"s")+
           " from your description. Check them, then Search.")
        : "Nothing in that description mapped onto a filter. Try naming a role, an industry, a place or a size.";
    }
    if(!landed){ toast("Nothing recognisable to filter on in that.", "err"); return; }
    toast("Filters filled. Nothing searched or spent yet.", "ok");
  }).catch(function(){
    if(btn){ btn.disabled=false; btn.textContent="Fill filters"; }
    toast("Could not read that query.", "err");
  });
};

/* ── The working list ── */
var LIST_ROWS=null;

function listKeyOf(r){
  var id=String((r&&(r.id||r.apollo_id))||"").trim();
  if(id) return id;
  var parts = STATE.shownEntity==="companies"
    ? [r&&r.name, r&&r.primary_domain]
    : [(r&&(r.full_name||r.name)), (r&&(r.organization_name||r.organization_domain))];
  return parts.map(function(p){ return String(p==null?"":p).trim().toLowerCase(); })
              .join("|").replace(/^\|+|\|+$/g,"") || "?";
}

function setListCount(n){
  var b=document.getElementById("cpiListN");
  if(!b) return;
  b.textContent=String(n||0);
  b.style.display = n ? "" : "none";
}

window.cpiAddToList = function(){
  var rows=selectedRows();
  if(!rows.length) rows=STATE.results.slice();
  if(!rows.length){ toast("Nothing to add yet.", "err"); return; }
  fetch(window.__CPI_LIST_URL__, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({entity: STATE.shownEntity||STATE.entity, rows: rows})
  }).then(function(r){ return r.json(); }).then(function(d){
    if(!d || d.available===false){ toast("The list is not available on this environment.", "err"); return; }
    setListCount(d.count);
    LIST_ROWS=null;
    if(d.full){ toast("The list holds "+d.cap+" rows. Download and clear it to add more.", "err"); return; }
    var dup=rows.length-(d.added||0);
    toast("Added "+(d.added||0)+" to the list"+
          (dup>0?" ("+dup+" already there)":"")+".", "ok");
  }).catch(function(){ toast("Could not add to the list.", "err"); });
};

function loadList(){
  return fetch(window.__CPI_LIST_URL__).then(function(r){ return r.json(); })
    .then(function(d){
      LIST_ROWS = (d&&d.rows)||[];
      setListCount((d&&d.count)||0);
      return d;
    });
}

window.cpiOpenList = function(){
  var dw=document.getElementById("cpiListDrawer"), ov=document.getElementById("cpiListOvl");
  if(dw) dw.classList.add("on");
  if(ov) ov.classList.add("on");
  var body=document.getElementById("cpiListBody");
  if(body) body.innerHTML='<div class="cpi-empty"><span>Loading…</span></div>';
  loadList().then(function(){ renderList(); })
    .catch(function(){
      if(body) body.innerHTML='<div class="cpi-empty"><span>Could not load the list.</span></div>';
    });
};

window.cpiCloseList = function(){
  var dw=document.getElementById("cpiListDrawer"), ov=document.getElementById("cpiListOvl");
  if(dw) dw.classList.remove("on");
  if(ov) ov.classList.remove("on");
};

function renderList(){
  var body=document.getElementById("cpiListBody");
  var clear=document.getElementById("cpiListClearAll");
  var actions=document.getElementById("cpiListActions");
  var sub=document.getElementById("cpiListSub");
  if(!body) return;
  var rows=LIST_ROWS||[];
  if(clear) clear.style.display = rows.length ? "" : "none";
  if(actions) actions.style.display = rows.length ? "" : "none";
  if(sub){
    /* How many of these already cost money, so the list says what it is worth
       rather than only how long it is. */
    var enriched=rows.filter(function(r){ return r.enriched||r.email; }).length;
    sub.textContent = rows.length
      ? rows.length+" row"+(rows.length===1?"":"s")+
        (enriched?" · "+enriched+" already enriched":"")
      : "Rows kept across searches and tabs";
  }
  if(!rows.length){
    body.innerHTML='<div class="cpi-empty"><span>Nothing on the list yet. Select rows in the grid and press <b>Add to list</b> to keep them across searches.</span></div>';
    return;
  }
  body.innerHTML=rows.map(function(r){
    var isCo = r._entity==="companies";
    var name = esc(isCo ? (r.name||"Unnamed") : (r.full_name||r.name||"Unnamed"));
    var sub2 = isCo ? [r.primary_domain, r.industry] : [r.title, r.organization_name];
    var paid = (r.enriched||r.email) ? '<span class="cpi-dw-badge">enriched</span>' : "";
    return '<div class="cpi-dw-item"><div class="cpi-dw-item-main">'+
      '<div class="cpi-dw-title">'+name+paid+"</div>"+
      '<div class="cpi-dw-meta">'+esc(sub2.filter(Boolean).join(" · "))+"</div></div>"+
      '<button class="cpi-dw-x-sm" onclick="cpiRemoveFromList(\''+esc(r._key)+'\')" '+
      'aria-label="Remove '+name+' from the list">&times;</button></div>';
  }).join("");
}

window.cpiRemoveFromList = function(key){
  fetch(window.__CPI_LIST_URL__, {
    method:"DELETE", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({keys:[key]})
  }).then(function(r){ return r.json(); }).then(function(d){
    setListCount((d&&d.count)||0);
    LIST_ROWS=(LIST_ROWS||[]).filter(function(r){ return r._key!==key; });
    renderList();
  }).catch(function(){ toast("Could not remove that row.", "err"); });
};

window.cpiClearList = function(){
  var n=(LIST_ROWS||[]).length;
  if(!window.confirm("Clear all "+n+" row"+(n===1?"":"s")+" from the list? Enriched contacts stay in History.")) return;
  fetch(window.__CPI_LIST_URL__, {
    method:"DELETE", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({all:true})
  }).then(function(r){ return r.json(); }).then(function(d){
    LIST_ROWS=[]; setListCount(0); renderList();
    toast("List cleared.", "ok");
  }).catch(function(){ toast("Could not clear the list.", "err"); });
};

/* The list can hold both kinds of row, and the export columns differ, so each
   kind is downloaded as its own file rather than forcing one set of headers
   onto both. */
window.cpiExportList = function(fmt){
  var rows=LIST_ROWS||[];
  if(!rows.length){ toast("The list is empty.", "err"); return; }
  ["people","companies"].forEach(function(kind){
    var subset=rows.filter(function(r){ return (r._entity||"people")===kind; });
    if(!subset.length) return;
    doCpiDownload(kind, subset, fmt, {}, {});
  });
};

/* ── What this tool has spent ── */
function loadSpend(){
  var el=document.getElementById("cpiSpend");
  if(!el) return;
  fetch(window.__CPI_CREDITS_URL__).then(function(r){ return r.json(); })
    .then(function(d){
      if(!d || !d.available) return;      /* no Postgres: say nothing at all */
      el.style.display="";
      el.innerHTML='<b>'+pmNum(d.month)+'</b> credit'+(d.month===1?"":"s")+
        ' spent here this month'+(d.month_mine?' <s>'+pmNum(d.month_mine)+" by you</s>":"");
      el.title="What Contact Finder has spent from the shared Apollo pool this "+
        "month. Not the account balance: the same key also funds visitor "+
        "de-anonymisation and External Usage enrichment, and no endpoint "+
        "available here reports the pool's remaining total.";
    }).catch(function(){ /* the header is decoration; never block the page */ });
}

/* ── Wiring ── */
/* The script is deferred, so the DOM is parsed by the time this runs and there is
   no readyState to wait on. */
COMBO_SPECS.forEach(function(spec){ initCombo(spec[0]); });
window.cpiSyncCostLabels();

/* Every control that can change the query re-renders the bar and re-counts.
   Delegated at the document rather than bound per control, so the 60-odd inputs
   and the ones added later are covered by the same two lines. */
["input","change"].forEach(function(evt){
  document.addEventListener(evt, function(e){
    var t=e.target;
    if(!t || !t.closest) return;
    if(t.closest(".cpi-filters") || t.id==="fpCompanyDetail") cpiFiltersChanged();
  }, true);
});
document.addEventListener("click", function(e){
  var t=e.target;
  if(t && t.closest && t.closest(".cpi-chip-group")) setTimeout(cpiFiltersChanged, 0);
}, true);
/* Picking from a combo commits a chip rather than typing into a field, so it
   does not raise an input event on anything the listeners above watch. */
var _addComboValue = addComboValue;
addComboValue = function(key, value){ _addComboValue(key, value); cpiFiltersChanged(); };
var _cpiComboRemove = window.cpiComboRemove;
window.cpiComboRemove = function(key, i){ _cpiComboRemove(key, i); cpiFiltersChanged(); };
var _cpiSetEntity = window.cpiSetEntity;
window.cpiSetEntity = function(entity){ _cpiSetEntity(entity); cpiFiltersChanged(); };

renderQueryBar();
syncMoreBadge();
loadSpend();
loadList().catch(function(){ /* the badge is optional */ });
/* Reflect the remembered layout on the toggle before any results exist, so the
   control never disagrees with the view the first search will render in. */
window.cpiSetView(STATE.view);

})();
