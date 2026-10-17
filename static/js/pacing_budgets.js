/* Pacing Desk: Allocations (Paid Marketing Agents).
 *
 * Google Ads says what an account has spent and what each campaign's daily budget is set to. It cannot say what the CLIENT agreed to
 * spend this month, and every pacing verdict is measured against that figure. It lives here, one row per client and account per month
 * (api/pacing/budgets, tracker/paid_pacing_budgets.py):
 *
 *   - a month nobody has saved shows last month's rows, marked as carried over until someone saves them;
 *   - rows can be pasted from the Budget Tracker (cells copied from the sheet; nothing here connects to it) and are read for review
 *     before anything is changed;
 *   - a save names the version it started from, so two people editing at once cannot overwrite each other.
 *
 * The page's own script (pacing_desk.js) calls PDAllocations.init(); after a save the next run uses the new numbers.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/pacing/budgets";
var $ = function(s, r){return (r||document).querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
var monthName = function(m){return m ? MONTHS[Number(m.slice(5,7))-1]+" "+m.slice(0,4) : ""};
var when = function(iso){
  var d = new Date(iso); if(isNaN(d)) return "";
  return d.toLocaleString([], {dateStyle:"medium", timeStyle:"short"});
};

var S = {month:"", rows:[], source:null, version:"", lastSaved:null, months:[], accounts:[], dirty:false, busy:false, loaded:false};
var root = null, onSaved = function(){};

function getJson(url, opts){
  opts = opts || {}; opts.credentials = "same-origin";
  return fetch(url, opts).then(function(r){
    return r.json().catch(function(){return {}}).then(function(j){
      if(!r.ok){ var e = new Error(j.error || (url+" answered "+r.status)); e.status = r.status; e.kind = j.kind; throw e; }
      return j;
    });
  });
}
function sendJson(url, method, body){
  return getJson(url, {method:method, headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
}

/* ── Reading the table back from the inputs ──────────────────── */
function num(v){
  var s = String(v==null?"":v).replace(/[,$\s]/g,"");
  return s==="" ? null : Number(s);
}
function readRows(){
  var rows = [];
  root.querySelectorAll("tbody tr[data-i]").forEach(function(tr){
    var g = function(f){var el = tr.querySelector('[data-f="'+f+'"]'); return el ? el.value : ""};
    rows.push({client:g("client").trim(), account:g("account").trim(), allocated:g("allocated"), targetCpa:g("targetCpa"),
      daysRunning:g("daysRunning").trim(), status:g("status"), am:g("am").trim()});
  });
  return rows;
}
function syncFromDom(){ if(root && root.querySelector("tbody")) S.rows = readRows(); }

/* ── Painting ────────────────────────────────────────────────── */
function bannerHtml(){
  var s = S.source || {kind:"none"}, m = monthName(S.month);
  if(s.kind === "saved"){
    return '<p class="pd-al-banner ok">Saved'+(S.lastSaved ? ' by <b>'+esc(S.lastSaved.by||"someone")+'</b>, '+esc(when(S.lastSaved.at)) : '')+
      '. The next run uses these for '+esc(m)+'.</p>';
  }
  if(s.kind === "carried"){
    return '<p class="pd-al-banner warn"><b>Not saved for '+esc(m)+' yet.</b> These are '+esc(monthName(s.from))+'&rsquo;s allocations, carried over. '+
      'The run uses them as they are; change what differs and save to confirm.</p>';
  }
  if(s.kind === "seeded"){
    return '<p class="pd-al-banner warn"><b>Not saved for '+esc(m)+' yet.</b> These are the accounts and allocations of the run of '+esc(s.from||"an earlier day")+
      '. Check them and save to confirm.</p>';
  }
  return '<p class="pd-al-banner warn"><b>No allocations for '+esc(m)+'.</b> Add each account&rsquo;s budget for the month, or paste it from the Budget Tracker. '+
    'Until then a run cannot judge pacing.</p>';
}

function rowHtml(r, i){
  var inp = function(f, v, cls, ph, label, extra){
    return '<input class="pd-al-in '+cls+'" data-f="'+f+'" value="'+esc(v==null?"":v)+'" placeholder="'+esc(ph||"")+'" aria-label="'+esc(label+' for row '+(i+1))+'" '+(extra||"")+' autocomplete="off">';
  };
  return '<tr data-i="'+i+'">'+
    '<td>'+inp("client", r.client, "w-client", "Client", "Client")+'</td>'+
    '<td>'+inp("account", r.account, "w-account", "Account", "Account", 'list="alAccounts"')+'</td>'+
    '<td>'+inp("allocated", r.allocated, "w-num", "0", "Allocated budget", 'inputmode="decimal"')+'</td>'+
    '<td>'+inp("targetCpa", r.targetCpa, "w-num", "optional", "Target CPA", 'inputmode="decimal"')+'</td>'+
    '<td>'+inp("daysRunning", r.daysRunning, "w-days", "every day", "Days running")+'</td>'+
    '<td><select class="pd-al-in w-status" data-f="status" aria-label="Status for row '+(i+1)+'">'+
      ["","Active","Paused"].map(function(o){return '<option value="'+o+'"'+((r.status||"")===o?" selected":"")+'>'+(o||"Not set")+'</option>'}).join("")+'</select></td>'+
    '<td>'+inp("am", r.am, "w-am", "AM", "Account manager")+'</td>'+
    '<td><button type="button" class="pd-al-x" data-del="'+i+'" aria-label="Remove row '+(i+1)+'" title="Remove this account">&times;</button></td></tr>';
}

function totalOf(rows){
  return rows.reduce(function(t, r){var n = num(r.allocated); return t + (isFinite(n) && n ? n : 0)}, 0);
}

function paint(){
  var months = S.months.slice();
  if(months.indexOf(S.month) < 0) months.push(S.month);
  months.sort();
  root.innerHTML =
    '<div class="pm-panel-h"><span class="pm-panel-t">Allocations</span>'+
      '<span class="pm-panel-s">What each account is meant to spend this month. Google Ads cannot know this, so it is kept here.</span></div>'+
    '<div class="pd-al-bar"><label class="pd-al-month">Month <select id="alMonth" class="pd-al-in">'+
      months.map(function(m){return '<option value="'+m+'"'+(m===S.month?" selected":"")+'>'+esc(monthName(m))+'</option>'}).join("")+'</select></label>'+
      '<span class="pd-al-total" id="alTotal"></span></div>'+
    '<div id="alBanner">'+bannerHtml()+'</div>'+
    '<div class="pd-al-msg" id="alMsg" hidden role="status"></div>'+
    '<div class="pd-al-wrap"><table class="pd-al-table"><thead><tr><th>Client</th><th>Account</th><th>Allocated ($)</th><th>Target CPA ($)</th>'+
      '<th>Days running</th><th>Status</th><th>AM</th><th><span class="sr-only">Remove</span></th></tr></thead><tbody>'+
      (S.rows.length ? S.rows.map(rowHtml).join("") : '<tr><td colspan="8" class="pd-al-empty">No accounts yet.</td></tr>')+
    '</tbody></table></div>'+
    '<datalist id="alAccounts">'+S.accounts.map(function(a){return '<option value="'+esc(a)+'">'}).join("")+'</datalist>'+
    '<div class="pd-al-actions">'+
      '<button type="button" class="pm-btn-ghost" id="alAdd">+ Add account</button>'+
      '<button type="button" class="pm-btn-ghost" id="alPasteBtn" aria-expanded="false">Paste from the Budget Tracker</button>'+
      '<span class="pd-al-sp"></span>'+
      '<button type="button" class="pm-btn-ghost" id="alReload" title="Throw away unsaved changes">Reload</button>'+
      '<button type="button" class="pd-al-save" id="alSave">Save allocations</button></div>'+
    '<div class="pd-al-paste" id="alPaste" hidden>'+
      '<p class="pm-panel-s">In the Budget Tracker, select the rows for the month (with the header row), copy, and paste here. '+
        'Only Google rows for '+esc(monthName(S.month))+' are read. Nothing is changed until you choose how to use them.</p>'+
      '<textarea id="alText" class="pd-al-text" rows="6" spellcheck="false" aria-label="Pasted Budget Tracker rows"></textarea>'+
      '<div class="pd-al-actions"><button type="button" class="pm-btn-ghost" id="alRead">Read the pasted rows</button></div>'+
      '<div id="alPreview"></div></div>';
  setTotal();
  setBusy(S.busy);
}

function setTotal(){
  var t = $("#alTotal"); if(!t) return;
  var n = S.rows.length;
  t.innerHTML = n ? '<b>'+n+'</b> account'+(n===1?"":"s")+' &middot; <b>$'+Math.round(totalOf(S.rows)).toLocaleString("en-US")+'</b> allocated'+(S.dirty?' &middot; <span class="pd-al-dirty">unsaved changes</span>':'') : '';
}
function msg(kind, html){
  var m = $("#alMsg"); if(!m) return;
  if(!html){ m.hidden = true; m.innerHTML = ""; return; }
  m.hidden = false; m.className = "pd-al-msg "+kind; m.innerHTML = html;
}
function setBusy(b){
  S.busy = b;
  ["alSave","alAdd","alReload","alRead","alPasteBtn"].forEach(function(id){var el = $("#"+id); if(el) el.disabled = b;});
}
function markDirty(){ S.dirty = true; setTotal(); }

/* ── Loading and saving ──────────────────────────────────────── */
function load(month){
  setBusy(true);
  return getJson(API + (month ? "?month="+encodeURIComponent(month) : "")).then(function(j){
    S.month = j.month; S.rows = j.rows || []; S.source = j.source; S.version = j.version || ""; S.lastSaved = j.lastSaved;
    S.months = j.months || []; S.accounts = j.accounts || []; S.dirty = false; S.loaded = true; S.busy = false;
    paint();
  }).catch(function(e){
    S.busy = false;
    root.innerHTML = '<div class="pm-err">Could not load the allocations: '+esc(e.message)+'</div>';
  });
}

function save(){
  syncFromDom();
  setBusy(true); msg("", "");
  return sendJson(API+"?month="+encodeURIComponent(S.month), "PUT", {rows:S.rows, version:S.version}).then(function(j){
    S.rows = j.rows; S.source = j.source; S.version = j.version || ""; S.lastSaved = j.lastSaved; S.months = j.months || S.months; S.dirty = false; S.busy = false;
    paint();
    msg("ok", '<b>Saved.</b> <button type="button" class="pd-al-link" id="alRun">Update the run with these</button> (it uses the latest Google Ads read; nothing is read again).');
    onSaved(S.month);
  }).catch(function(e){
    setBusy(false);
    if(e.kind === "conflict"){
      msg("bad", '<b>Not saved.</b> '+esc(e.message)+' <button type="button" class="pd-al-link" id="alReloadNow">Reload</button>');
    } else msg("bad", '<b>Not saved.</b> '+esc(e.message));
  });
}

/* ── Pasting from the Budget Tracker ─────────────────────────── */
var PASTED = null;
function key(c, a){return (String(c).trim()+"|"+String(a).trim()).toLowerCase().replace(/\s+/g," ");}

function readPasted(){
  var text = $("#alText").value;
  setBusy(true); $("#alPreview").innerHTML = "";
  return sendJson(API+"/parse", "POST", {month:S.month, text:text}).then(function(j){
    setBusy(false); PASTED = j;
    var skipped = (j.skipped||[]).map(function(s){return '<li>'+(s.row?'<b>'+esc(s.row)+'</b>: ':'')+esc(s.reason)+'</li>'}).join("");
    $("#alPreview").innerHTML =
      '<p class="pd-al-banner ok"><b>'+j.rows.length+' row'+(j.rows.length===1?"":"s")+'</b> for '+esc(monthName(S.month))+' read'+
        (j.ignored ? ', '+j.ignored+' for other months left out' : '')+'.</p>'+
      (skipped ? '<details class="pd-al-skipped"><summary>'+(j.skipped.length)+' not used</summary><ul>'+skipped+'</ul></details>' : '')+
      '<div class="pd-al-actions"><button type="button" class="pd-al-save" id="alUseAll">Replace the table with these</button>'+
        '<button type="button" class="pm-btn-ghost" id="alUseMerge">Add these, updating matching accounts</button></div>';
  }).catch(function(e){
    setBusy(false); PASTED = null;
    $("#alPreview").innerHTML = '<p class="pd-al-banner bad"><b>Could not read that.</b> '+esc(e.message)+'</p>';
  });
}

/* The table after pasting: the pasted rows alone (replace) or the table with matching client+account rows updated and the rest added. */
function merge(current, pasted, replace){
  var rows = pasted.map(function(r){return {client:r.client, account:r.account, allocated:r.allocated, targetCpa:r.targetCpa==null?"":r.targetCpa,
    daysRunning:r.daysRunning||"", status:r.status||"", am:r.am||""}});
  if(replace) return rows;
  var out = current.slice(), at = {};
  out.forEach(function(r, i){at[key(r.client, r.account)] = i});
  rows.forEach(function(r){var k = key(r.client, r.account); if(k in at) out[at[k]] = r; else { at[k] = out.length; out.push(r); }});
  return out;
}

function usePasted(replace){
  if(!PASTED) return;
  syncFromDom();
  S.rows = merge(S.rows, PASTED.rows, replace);
  PASTED = null; S.dirty = true; paint();
  msg("warn", 'The pasted rows are in the table but <b>not saved</b>. Look them over, then press Save allocations.');
}

/* ── Events ──────────────────────────────────────────────────── */
function wire(){
  root.addEventListener("input", function(e){
    if(e.target.matches(".pd-al-table .pd-al-in")){ syncFromDom(); markDirty(); }
  });
  root.addEventListener("change", function(e){
    if(e.target.id === "alMonth"){
      if(S.dirty && !confirm("You have unsaved changes. Switch month and lose them?")){ $("#alMonth").value = S.month; return; }
      load(e.target.value);
    } else if(e.target.matches(".pd-al-table select")){ syncFromDom(); markDirty(); }
  });
  root.addEventListener("click", function(e){
    var t = e.target.closest("button"); if(!t) return;
    if(t.dataset.del != null){ syncFromDom(); S.rows.splice(Number(t.dataset.del), 1); S.dirty = true; var open = !$("#alPaste").hidden; paint(); if(open) togglePaste(true); return; }
    var id = t.id;
    if(id === "alAdd"){ syncFromDom(); S.rows.push({client:S.rows.length?S.rows[S.rows.length-1].client:"", account:"", allocated:"", targetCpa:"", daysRunning:"", status:"", am:""}); S.dirty = true;
      var wasOpen = !$("#alPaste").hidden; paint(); if(wasOpen) togglePaste(true);
      var last = root.querySelectorAll("tbody tr[data-i]"); if(last.length){ var f = last[last.length-1].querySelector('[data-f="account"]'); if(f) f.focus(); } }
    else if(id === "alSave") save();
    else if(id === "alReload" || id === "alReloadNow"){ load(S.month); }
    else if(id === "alPasteBtn") togglePaste();
    else if(id === "alRead") readPasted();
    else if(id === "alUseAll") usePasted(true);
    else if(id === "alUseMerge") usePasted(false);
    else if(id === "alRun" && window.PDRecompute) window.PDRecompute();
  });
}
function togglePaste(forceOpen){
  var p = $("#alPaste"), b = $("#alPasteBtn"), open = forceOpen === true ? true : p.hidden;
  p.hidden = !open; b.setAttribute("aria-expanded", String(open));
  if(open) $("#alText").focus();
}

if(typeof module !== "undefined" && module.exports) module.exports = {monthName:monthName, num:num, key:key, merge:merge, totalOf:totalOf};
if(typeof window === "undefined") return;
window.PDAllocations = {
  /* init(panelElement, {onSaved(month)}): the panel is filled the first time it is opened. */
  init: function(el, opts){ root = el; onSaved = (opts && opts.onSaved) || onSaved; wire(); },
  open: function(){ if(!S.loaded && !S.busy) return load(""); return Promise.resolve(); },
  isDirty: function(){ return S.dirty; }
};
})();
