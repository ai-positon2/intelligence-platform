/* Search Query Analysis (Paid Marketing Agents): the page.
 *
 * Pick a Google Ads account and a range; its search terms, keywords and negative keywords are read from Google Ads
 * (/p2/paid-marketing/api/sqr/data, tracker/paid_sqr_data.py). The rules, the arithmetic and the ranking are in sqr_core.js (run in Node by
 * tests/test_sqr_core_js.py); this file wires the controls and draws what it returns. Only the clicked terms no rule settles go to the model,
 * through /p2/paid-marketing/api/sqr/classify, which holds each account's remaining categories (tracker/paid_sqr.py). A CSV upload is
 * still offered for an account Google Ads can't be read for. The ruleset ids here and there must match: tests/test_paid_marketing.py checks.
 */
(function(){
"use strict";
const Plan = window.SQRPlan, Core = window.SQRCore, RULESETS = Core.RULESETS, P = window.PSData, PL = window.PMPlatform;
const BASE = "/p2/paid-marketing/api";

function esc(s){ return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
const $id = id => document.getElementById(id);
const fmtConv = Core.fmtConv;

// =====================================================================
// The model step: only the terms no rule settled (and that were clicked),
// in batches, to the platform's own route. The categories each account is
// judged against live server-side (tracker/paid_sqr.py).
// =====================================================================
const CLASSIFY_URL = BASE + "/sqr/classify";
const BATCH = 40, PARALLEL = 3;

async function classifyBatch(rulesetId, account, batch, profile){
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 90000);
  try {
    const res = await fetch(CLASSIFY_URL, { method: "POST", credentials: "same-origin", signal: ctl.signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ruleset: rulesetId, account: account, terms: batch, profile: profile || undefined }) });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || ("classify answered " + res.status));
    return j.results || {};
  } finally { clearTimeout(t); }
}

async function classifyPending(ruleset, account, terms, progress, profile){
  const out = {}, failures = [];
  const batches = [];
  for (let i = 0; i < terms.length; i += BATCH) batches.push(terms.slice(i, i + BATCH));
  let done = 0, next = 0;
  async function worker(){
    while (next < batches.length){
      const b = batches[next++];
      try { Object.assign(out, await classifyBatch(ruleset.id, account, b, profile)); }
      catch (e) { failures.push(e.message || String(e)); }
      done += b.length;
      progress(done, terms.length);
    }
  }
  await Promise.all(Array.from({ length: Math.min(PARALLEL, batches.length) }, worker));
  let fallback = 0;
  terms.forEach(t => {
    if (!out[t]) { out[t] = { status: "Not Relevant", subtype: "No Clear Intent", by: "fallback" }; fallback++; }
    else if (out[t].by === "fallback") fallback++;
  });
  return { results: out, fallback, failures };
}

// =====================================================================
// State
// =====================================================================
const S = { acct: null, preset: "last30", range: P.presetRange("last30"), data: null, loading: 0, rulesManual: false, currency: "USD", ran: null };
let current = "generic", orb = null, lastResult = null;
const files = { terms: null, keywords: null, negs: null }, meta = { terms: null, keywords: null, negs: null };
const PRESETS = [{id:"last7",label:"Last 7 days"},{id:"last14",label:"Last 14 days"},{id:"last30",label:"Last 30 days"},{id:"last90",label:"Last 90 days"}];
const MAX_DAYS = 92;
const ACCT_HINT = { riccobene: "Dental · 20 locations", oia: "Imaging · 12 facilities", eventgroove: "Event printing",
  gentledental_42north: "Dental", mchale: "Landscaping · DC metro", beta_bionics: "Medical device · type 1 diabetes, US only", generic: "Works for any account, learns it from its Google Ads data" };
const ACCT_HUE = { riccobene: 190, oia: 255, eventgroove: 320, gentledental_42north: 160, mchale: 120, beta_bionics: 280, generic: 220 };

function money(v, whole){
  v = Number(v) || 0;
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency: S.currency || "USD", maximumFractionDigits: whole === false ? 2 : (Math.abs(v) >= 100 ? 0 : 2) }).format(v); }
  catch (e) { return "$" + Math.round(v).toLocaleString(); }
}
const num = v => (Number(v) || 0).toLocaleString();
// conversions per click, so over 100% when one click converts more than once
const convRate = (conv, clicks) => clicks > 0 ? (Number(conv) / clicks * 100).toFixed(1) + "%" : "–";
const plural = (n, one, many) => n + " " + (n === 1 ? one : (many || one + "s"));
function toast(t){
  let el = $id("sqToast");
  if (!el) { el = document.createElement("div"); el.id = "sqToast"; el.className = "ps-toast"; document.body.appendChild(el); }
  el.textContent = t; el.classList.add("on"); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("on"), 2600);
}

// =====================================================================
// Rules dropdown
// =====================================================================
function initials(label){ return label.replace(/\(.*?\)/g, "").split(/\s+/).filter(w => /^[A-Za-z0-9]/.test(w)).slice(0, 2).map(w => w[0]).join("").toUpperCase(); }
function badge(id){
  return id === "generic" ? '<span class="badge pm-pill ok">Automatic</span>' : '<span class="badge pm-pill mute">Client rulebook</span>';
}
function avatar(rs){ return '<span class="sq-av" style="--h:' + (ACCT_HUE[rs.id] || 200) + '">' + esc(initials(rs.label)) + '</span>'; }
const dd = $id("dd"), ddBtn = $id("ddBtn"), ddMenu = $id("ddMenu"), ddList = $id("ddList"), ddSearch = $id("ddSearch");
let hl = 0, shown = [];

function paintButton(){
  const rs = RULESETS[current];
  ddBtn.innerHTML = avatar(rs) + '<span class="sq-dd-txt"><span class="nm">' + esc(rs.label) + '</span><span class="ds">' +
    esc(ACCT_HINT[rs.id] || "") + '</span></span>' + badge(rs.id) +
    '<span class="sq-chev"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span>';
  let note = rs.note;
  if (rs.id === "generic" && S.acct) {
    const ph = Core.brandPhrases(S.acct.name).filter(p => p.indexOf(" ") >= 0 || Core.brandPhrases(S.acct.name).length === 1);
    note += ph.length ? " <b>For " + esc(S.acct.name) + "</b>, a search containing " + ph.map(p => "\u2018" + esc(p) + "\u2019").join(" or ") + " counts as the account\'s own brand."
      : " <b>For " + esc(S.acct.name) + "</b> the name is too short or too common to treat as a brand word, so the model reads brand searches too.";
  }
  $id("rulesetNote").innerHTML = note;
}
function paintList(){
  const q = ddSearch.value.trim().toLowerCase();
  shown = Object.values(RULESETS).filter(rs => !q || (rs.label + " " + (ACCT_HINT[rs.id] || "")).toLowerCase().includes(q));
  if (hl >= shown.length) hl = Math.max(0, shown.length - 1);
  ddList.innerHTML = shown.length ? shown.map((rs, i) =>
    '<button type="button" role="option" class="sq-opt' + (i === hl ? " hl" : "") + '" data-id="' + rs.id + '" aria-selected="' + (rs.id === current) +
      '" style="animation-delay:' + (i * 25) + 'ms">' + avatar(rs) + '<span class="sq-dd-txt"><span class="nm" style="font-size:13.5px">' + esc(rs.label) +
      '</span><span class="ds">' + esc(ACCT_HINT[rs.id] || "") + '</span></span>' + badge(rs.id) + '<span class="tick">✓</span></button>').join("")
    : '<div class="sq-none">No rules match “' + esc(q) + '”.</div>';
}
function openMenu(){
  dd.classList.add("open"); ddMenu.hidden = false; ddBtn.setAttribute("aria-expanded", "true");
  ddSearch.value = ""; hl = Math.max(0, Object.keys(RULESETS).indexOf(current)); paintList();
  setTimeout(() => ddSearch.focus(), 20);
}
function closeMenu(focus){
  dd.classList.remove("open"); ddMenu.hidden = true; ddBtn.setAttribute("aria-expanded", "false");
  if (focus) ddBtn.focus();
}
function setRules(id, manual){
  if (!RULESETS[id]) return;
  current = id; if (manual) S.rulesManual = true;
  paintButton(); paintWhy();
}
function choose(id){
  setRules(id, true); closeMenu(true);
  ddBtn.animate([{ transform: "scale(.98)" }, { transform: "scale(1.01)" }, { transform: "none" }], { duration: 320, easing: "ease-out" });
}
function paintWhy(){
  const why = S.data && S.data.ruleset ? S.data.ruleset : null;
  const el = $id("rulesWhy");
  if (!why || S.rulesManual) { el.textContent = S.rulesManual ? "Chosen by you for this account." : ""; return; }
  el.textContent = why.id === "generic" ? "Automatic rules: " + why.why + "." : "Client rulebook picked from the account's name: " + why.why + ".";
}
ddBtn.addEventListener("click", () => ddMenu.hidden ? openMenu() : closeMenu());
ddList.addEventListener("click", e => { const o = e.target.closest(".sq-opt"); if (o) choose(o.dataset.id); });
ddSearch.addEventListener("input", () => { hl = 0; paintList(); });
ddSearch.addEventListener("keydown", e => {
  if (e.key === "ArrowDown") { e.preventDefault(); hl = Math.min(shown.length - 1, hl + 1); paintList(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); hl = Math.max(0, hl - 1); paintList(); }
  else if (e.key === "Enter") { e.preventDefault(); if (shown[hl]) choose(shown[hl].id); }
  else if (e.key === "Escape") { e.preventDefault(); closeMenu(true); }
});
ddBtn.addEventListener("keydown", e => { if (e.key === "ArrowDown" && ddMenu.hidden) { e.preventDefault(); openMenu(); } });
document.addEventListener("click", e => { if (!dd.contains(e.target) && !ddMenu.hidden) closeMenu(); });
$id("rulesBtn").addEventListener("click", () => {
  const n = $id("rulesetNote"), open = n.hidden;
  n.hidden = !open; $id("rulesBtn").setAttribute("aria-expanded", String(open));
});

// =====================================================================
// Reading Google Ads
// =====================================================================
function notice(kind, title, html){
  $id("notice").innerHTML = '<div class="ps-notice ' + kind + '"><b>' + esc(title) + '</b><p>' + html + '</p></div>';
}
function clearNotice(){ $id("notice").innerHTML = ""; }

function computeRange(){
  if (S.preset !== "custom") S.range = P.presetRange(S.preset);
  const n = P.diffDays(S.range.start, S.range.end) + 1;
  $id("rangeNote").innerHTML = '<b>' + esc(S.range.start) + ' to ' + esc(S.range.end) + '</b> (' + n + (n === 1 ? " day" : " days") + '). The ranges Google Ads can answer here are up to ' + MAX_DAYS + ' days.';
}
function paintRanges(){
  let h = "";
  PRESETS.forEach(p => { h += '<button type="button" class="pm-chip" data-p="' + p.id + '" aria-pressed="' + (S.preset === p.id) + '">' + p.label + '</button>'; });
  h += '<button type="button" class="pm-chip" data-p="custom" aria-pressed="' + (S.preset === "custom") + '">Custom</button>';
  $id("ranges").innerHTML = h;
  $id("custom").hidden = S.preset !== "custom" && !$id("custom")._open;
  $id("cStart").value = S.range.start; $id("cEnd").value = S.range.end; $id("cEnd").max = P.localToday(); $id("cStart").max = P.localToday();
}
$id("ranges").addEventListener("click", e => {
  const b = e.target.closest("[data-p]"); if (!b) return;
  if (b.dataset.p === "custom") { S.preset = "custom"; $id("custom")._open = true; paintRanges(); return; }
  S.preset = b.dataset.p; $id("custom")._open = false; paintRanges(); computeRange(); load(false);
});
$id("cApply").addEventListener("click", () => {
  const s = $id("cStart").value, e = $id("cEnd").value;
  if (!s || !e || s > e) { toast("Pick a start date on or before the end date"); return; }
  if (P.diffDays(s, e) + 1 > MAX_DAYS) { toast("Search terms are limited to " + MAX_DAYS + " days at a time"); return; }
  S.preset = "custom"; S.range = { start: s, end: e }; paintRanges(); computeRange(); load(false);
});
$id("refreshBtn").addEventListener("click", () => load(true));
$id("dataLine").addEventListener("click", e => { if (e.target.closest("#tryLonger")) { S.preset = "last90"; $id("custom")._open = false; paintRanges(); computeRange(); load(false); } });

function setLive(text, off){
  $id("liveText").textContent = text; $id("live").classList.toggle("off", !!off);
}
function paintData(){
  const d = S.data, el = $id("dataLine");
  if (!d) { el.innerHTML = ""; return; }
  const w = (d.warnings || []).map(x => '<li>' + esc(x) + '</li>').join("");
  const marks = d.terms.filter(t => t.s === "ADDED" || t.s === "ADDED_EXCLUDED").length;
  el.innerHTML = '<div class="sq-data-in"><b>Read from Google Ads:</b> ' + num(d.terms.length) + ' search term rows, ' + num(d.keywords.length) + ' keywords and ' +
    num(negativeCount(d)) + ' negative keywords (campaign and list) for <b>' + esc(S.acct.name) + '</b>, ' + esc(d.range[0]) + ' to ' + esc(d.range[1]) + '.' +
    (marks ? ' ' + num(marks) + ' of the terms are already keywords.' : '') + (w ? '<ul class="sq-warns">' + w + '</ul>' : '') +
    (d.notes || []).map(x => '<small class="sq-ex">' + esc(x) + '</small>').join("") +
    (d.terms.length ? "" : '<p class="sq-empty">Google Ads reports no searches for this account in these dates: it had no traffic, or its campaigns were paused.' +
      (S.preset === "last90" ? "" : ' <button type="button" class="pm-chip" id="tryLonger">Look at the last 90 days</button>') + '</p>') + '</div>';
  $id("readyText").textContent = d.terms.length ? num(d.terms.length) + " search term rows ready" + (S.acct ? " for " + S.acct.name : "") + "." : "That range has no search terms.";
}
function refreshReady(){
  const csv = !!files.terms;
  const have = csv || (S.data && S.data.terms && S.data.terms.length);
  $id("runBtn").disabled = !have || S.loading > 0;
  $id("runBtn").classList.toggle("ready", !!have && !S.loading);
  if (csv) {
    const extra = [];
    if (files.keywords) extra.push(meta.keywords.toLocaleString() + " keywords");
    if (files.negs) extra.push(meta.negs.toLocaleString() + " negatives");
    $id("readyText").textContent = meta.terms.toLocaleString() + " search terms from your file" + (extra.length ? ", with " + extra.join(" and ") : "") + ". The analysis will use the files, not Google Ads.";
  } else if (!S.acct) $id("readyText").textContent = "Pick an account to start.";
  else if (S.loading) $id("readyText").textContent = "Reading Google Ads…";
  else if (S.data) paintData();
}

let loadSeq = 0;
async function load(force){
  if (!S.acct) return;
  const seq = ++loadSeq, acct = S.acct; S.loading++; S.data = null; lastResult = null;
  $id("dlBtn").disabled = true; $id("results").innerHTML = ""; $id("dataLine").innerHTML = ""; $id("staleBar").hidden = true; clearNotice();
  setLive("Reading " + acct.name + " from Google Ads…"); refreshReady();
  const url = BASE + "/sqr/data?account=" + encodeURIComponent(acct.id) + "&start=" + S.range.start + "&end=" + S.range.end + (force ? "&refresh=1" : "");
  try {
    const r = await fetch(url, { credentials: "same-origin" });
    const j = await r.json().catch(() => ({}));
    if (seq !== loadSeq) return;
    if (!r.ok) { const e = new Error(j.error || ("The server answered " + r.status)); e.status = r.status; e.config = j.configProblem; throw e; }
    S.data = j; S.currency = acct.currency || "USD";
    if (!S.rulesManual && j.ruleset) setRules(j.ruleset.id, false); else { paintButton(); paintWhy(); }
    $id("upd").textContent = j.fetchedAt ? "· read " + ago(j.fetchedAt) : "";
    setLive("Live from Google Ads · " + acct.name);
    if (j.stale) { $id("staleBar").hidden = false; $id("staleBar").innerHTML = "<b>Showing the copy saved " + esc(new Date(j.stale).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })) + ".</b> Google Ads did not answer, so fresh numbers will come back when it does."; }
  } catch (err) {
    if (seq !== loadSeq) return;
    setLive("Google Ads is not reachable", true);
    if (err.status === 409) notice("setup", "Google Ads is not connected yet", esc(err.message));
    else if (err.status === 429 && /refreshed moments ago/.test(err.message)) { notice("warn", "Already current", esc(err.message)); setLive("Live from Google Ads · " + acct.name); }
    else if (err.status === 429) notice("warn", "Google Ads is not answering right now", esc(err.message));
    else notice("bad", "Could not read Google Ads", esc(err.message));
  } finally {
    if (seq === loadSeq) S.loading--; else S.loading = Math.max(0, S.loading - 1);
    refreshReady();
  }
}
function ago(iso){
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago";
}

// =====================================================================
// CSV fallback (drop zones)
// =====================================================================
function countRows(text, key){
  if (key === "terms") { const r = Core.rowsFromCsv(text), n = r.error ? 0 : Core.prepareRows(r.rows).length; return { ok: n > 0, n }; }
  const n = (key === "negs" ? Core.negativesFromCsv(text) : Core.keywordsFromCsv(text)).length;
  return { ok: n > 0, n };
}
function sparkle(el){
  if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const r = el.getBoundingClientRect(), colors = ["#22d3ee", "#818cf8", "#34d399", "#fbbf24"];
  for (let i = 0; i < 14; i++){
    const s = document.createElement("span"); s.className = "sq-spark";
    const a = Math.PI * 2 * i / 14, d = 60 + Math.random() * 60;
    s.style.left = (r.width / 2) + "px"; s.style.top = (r.height / 2) + "px";
    s.style.setProperty("--dx", Math.cos(a) * d + "px"); s.style.setProperty("--dy", Math.sin(a) * d + "px");
    s.style.setProperty("--c", colors[i % colors.length]);
    el.appendChild(s); setTimeout(() => s.remove(), 900);
  }
}
const UNIT = { terms: "search terms", keywords: "keywords", negs: "negatives" };
function wireDrop(key){
  const input = $id("file-" + key), drop = $id("drop-" + key), chip = $id("file-" + key + "-name");
  const take = f => {
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      const c = countRows(reader.result, key);
      drop.classList.remove("bad");
      if (!c.ok){
        files[key] = null; meta[key] = null;
        drop.classList.add("filled", "bad");
        chip.innerHTML = '<span class="fn">' + esc(f.name) + '</span><span class="fm">no ' + UNIT[key] + ' column found</span><button type="button" class="x" aria-label="Remove file">×</button>';
      } else {
        files[key] = reader.result; meta[key] = c.n;
        drop.classList.add("filled");
        chip.innerHTML = '<span class="fn">' + esc(f.name) + '</span><span class="fm">' + c.n.toLocaleString() + ' ' + UNIT[key] + '</span><button type="button" class="x" aria-label="Remove file">×</button>';
        sparkle(drop);
      }
      refreshReady();
    };
    reader.readAsText(f);
  };
  input.addEventListener("change", () => take(input.files[0]));
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", e => { if (!drop.contains(e.relatedTarget)) drop.classList.remove("drag"); });
  drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("drag"); document.body.classList.remove("sq-dragging"); take(e.dataTransfer.files[0]); });
  chip.addEventListener("click", e => {
    if (!e.target.closest(".x")) return;
    e.preventDefault(); e.stopPropagation();
    files[key] = null; meta[key] = null; input.value = ""; chip.innerHTML = "";
    drop.classList.remove("filled", "bad"); refreshReady();
  });
}
["terms", "keywords", "negs"].forEach(wireDrop);
let dragDepth = 0;
document.addEventListener("dragenter", e => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { dragDepth++; document.body.classList.add("sq-dragging"); } });
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("sq-dragging"); } });
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => { e.preventDefault(); dragDepth = 0; document.body.classList.remove("sq-dragging"); });

// =====================================================================
// Running it
// =====================================================================
function setStatus(t, frac){
  $id("statusLine").textContent = t; $id("ovStatus").textContent = t;
  if (frac != null) $id("ovBar").style.width = Math.round(Math.min(1, frac) * 100) + "%";
}
// A campaign's negatives arrive once per keyword with the campaigns that have it; a shared list once, with the campaigns it is attached to.
function expandNegatives(d){
  const out = d.negatives.map(n => ({ text: n.t, match: n.m, level: "campaign", campaigns: n.c }));
  (d.lists || []).forEach(l => l.k.forEach(k => out.push({ text: k[0], match: k[1], level: "list", campaigns: l.c })));
  return out;
}
function negativeCount(d){ return d.negatives.length + (d.lists || []).reduce((a, l) => a + l.k.length, 0); }
function inputs(){
  if (files.terms) {
    const t = Core.rowsFromCsv(files.terms);
    if (t.error) throw new Error(t.error);
    return { rows: t.rows, keywords: Core.keywordsFromCsv(files.keywords), negatives: Core.negativesFromCsv(files.negs), account: "", from: "your CSV files" };
  }
  const d = S.data;
  return { rows: d.terms.map(t => ({ term: t.t, campaign: t.c, adGroup: t.g, campaignType: t.ct, clicks: t.k, impressions: t.i, cost: t.x, conversions: t.v, status: t.s })),
    keywords: d.keywords.map(k => ({ text: k.t, match: k.m, campaign: k.c, adGroup: k.g })),
    negatives: expandNegatives(d), account: S.acct ? S.acct.name : "", from: "Google Ads" };
}
// What the account is, from its own Google Ads data: its campaigns, what it bids on, what converted. The model is told this so it can judge a
// search for any account, not only the ones with a written rulebook.
function accountProfile(inp){
  const cost = new Map(), conv = new Map(), camp = new Map();
  inp.rows.forEach(r => {
    const k = Core.normText(r.term); if (!k) return;
    cost.set(k, (cost.get(k) || 0) + (+r.cost || 0));
    if ((+r.conversions || 0) > 0) { const e = conv.get(k) || { term: r.term, conversions: 0, cost: 0 }; e.conversions += +r.conversions; e.cost += +r.cost || 0; conv.set(k, e); }
    if (r.campaign) camp.set(r.campaign, (camp.get(r.campaign) || 0) + (+r.cost || 0));
  });
  const seen = new Set(), kws = [];
  inp.keywords.forEach(k => { const t = Core.keyText(k.text); if (t && !seen.has(t)) { seen.add(t); kws.push(t); } });
  kws.sort((a, b) => (cost.get(b) || 0) - (cost.get(a) || 0));
  return {
    campaigns: Array.from(camp.entries()).sort((a, b) => b[1] - a[1]).slice(0, 25).map(e => e[0]),
    keywords: kws.slice(0, 40),
    converting: Array.from(conv.values()).sort((a, b) => (b.conversions - a.conversions) || (b.cost - a.cost)).slice(0, 30).map(e => e.term),
  };
}
async function runAnalysis(){
  const inp = inputs();
  const auto = current === "generic";
  const profile = accountProfile(inp);
  const ruleset = auto ? Core.autoRuleset({ name: inp.account, keywords: inp.keywords }) : RULESETS[current];
  S.ran = { from: inp.from, account: inp.account, range: files.terms ? null : S.data.range, warnings: files.terms ? [] : (S.data.warnings || []), notes: files.terms ? [] : (S.data.notes || []) };
  const classify = terms => classifyPending(ruleset, inp.account, terms, (d, n) => setStatus("Model: " + d.toLocaleString() + " / " + n.toLocaleString() + " terms the rules couldn't settle", 0.32 + 0.6 * d / n), profile);
  return Core.analyze(ruleset, inp.rows, { keywords: inp.keywords, negatives: inp.negatives, accountName: inp.account, classify, status: setStatus });
}
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !$id("runBtn").disabled && $id("overlay").hidden) { e.preventDefault(); $id("runBtn").click(); }
});
$id("runBtn").addEventListener("click", async () => {
  $id("runBtn").disabled = true; $id("dlBtn").disabled = true; $id("results").innerHTML = "";
  $id("overlay").hidden = false; setStatus("Starting…", 0);
  import("/static/js/thinking-orb.js").then(m => { if (!orb && !$id("overlay").hidden) orb = m.mountThinkingOrb($id("ovOrb"), { state: "solving", size: 64 }); }).catch(() => {});
  try {
    lastResult = await runAnalysis();
    setStatus("Done: " + lastResult.summary.totalRows.toLocaleString() + " term rows classified.", 1);
    renderResults(lastResult); $id("dlBtn").disabled = false;
    $id("results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    $id("results").innerHTML = '<div class="pm-err">Something went wrong: ' + esc(e && e.message ? e.message : e) + '</div>';
    setStatus("Failed.");
  } finally {
    $id("overlay").hidden = true; if (orb) { orb.destroy(); orb = null; }
    refreshReady();
  }
});

// =====================================================================
// Results
// =====================================================================
const pillClass = s => s === "Relevant" ? "ok" : s === "Competitor" ? "over" : s === "Client Review" ? "unclear" : "mute";
const rowClass = s => s === "Relevant" ? "sq-row-rel" : s === "Competitor" ? "sq-row-comp" : s === "Client Review" ? "sq-row-rev" : "";
const MIX = [["Relevant", "c-rel"], ["Competitor", "c-comp"], ["Not Relevant", "c-nr"], ["Client Review", "c-rev"], ["Not reviewed", "c-un"]];
const BY = { rule: "rule", model: "model", fallback: "fallback", skipped: "not read" };

function where(n){ return n.level === "Campaign" ? "Campaign: " + n.campaign : n.level; }
function source(n){ return n.implied ? "spent, no conversions" : n.by === "model" ? "model" : n.by === "mixed" ? "rules + model" : "rule"; }
function tbl(h, rows){ return '<div class="pm-scroll"><table><thead><tr>' + h.map(x => '<th' + (x[1] ? ' class="r"' : '') + '>' + x[0] + '</th>').join("") + '</tr></thead><tbody>' + rows + '</tbody></table></div>'; }
const none = t => '<p class="pm-empty">' + (t || "None found.") + '</p>';
const hint = t => '<p class="sq-hint">' + t + '</p>';

/* How to apply what was found and what it is worth (static/js/sqr_plan.js works both out): folded away under the table's own hint. */
function planHtml(p){
  if (!p || !p.steps || !p.steps.length) return "";
  return '<details class="ps-do"><summary>How to do it</summary><ol>' + p.steps.map(t => '<li>' + esc(t) + '</li>').join("") + '</ol>' +
    (p.worth ? '<p class="ps-worth"><b>Worth</b> ' + esc(p.worth) + '</p>' : "") + '</details>';
}
function renderResults(r){
  const s = r.summary, N = s.totalRows, ran = S.ran || {};
  const kpi = (v, l) => '<div class="sq-kpi"><div class="v">' + v + '</div><div class="l">' + l + '</div></div>';
  const tab = (k, l, n) => '<button class="sq-tab" data-k="' + k + '">' + l + '<span class="n">' + n + '</span></button>';
  let warn = "";
  if (s.failures.length) warn += '<div class="pm-err">' + s.failures.length + ' model batch' + (s.failures.length === 1 ? "" : "es") +
    ' failed: ' + esc(s.failures[0]) + ' Their ' + s.fallback.toLocaleString() + ' term rows (' + money(s.notJudgedSpend) + ' of spend) are marked Not Relevant, No Clear Intent, and say "fallback" in the table. They are not turned into negatives and are not counted as wasted spend. Run again to retry them.</div>';
  if (s.overLimit) warn += '<div class="sq-warn">' + plural(s.overLimit, "term row") + ' that cost money ' + (s.overLimit === 1 ? "was" : "were") + ' not read by the model: a run reads at most ' + Core.MODEL_CAP.toLocaleString() +
    ' terms, the costliest first (' + money(s.notReviewedSpend) + ' of spend is in the rows left unread, marked Not reviewed). A shorter range or one account at a time reads them all.</div>';
  (ran.warnings || []).forEach(w => { warn += '<div class="sq-warn">' + esc(w) + '</div>'; });
  const skippedBits = [];
  if (r.negativesSkipped) skippedBits.push(num(r.negativesSkipped) + " negative suggestions the account already has or that did not spend");
  const ks = r.keywordsSkipped || {};
  if (ks.existing) skippedBits.push(num(ks.existing) + " relevant terms it already bids on");
  if (ks.blocked) skippedBits.push(num(ks.blocked) + " that an existing negative blocks");
  if (ks.type) skippedBits.push(num(ks.type) + " in campaigns that take no keywords (Shopping, Display)");
  if (ks.weak) skippedBits.push(num(ks.weak) + " relevant terms with too little response to suggest");
  const reviewCount = r.reviewNegatives.length;
  $id("results").innerHTML = warn + '<div class="pm-panel">' +
    '<div class="pm-panel-h"><span class="pm-panel-t">Results · ' + esc(r.ruleset.label) + '</span>' +
    '<span class="pm-panel-s">' + (ran.account ? esc(ran.account) + ' · ' : '') + (ran.range ? esc(ran.range[0]) + ' to ' + esc(ran.range[1]) + ' · ' : '') + esc(ran.from || "") + ' · ' +
      s.byRule.toLocaleString() + ' rows settled by rules · ' + s.byModel.toLocaleString() + ' by the model' + (s.fallback ? ' · ' + s.fallback.toLocaleString() + ' fallback' : '') + '</span></div>' +
    '<div class="sq-mix">' + MIX.map(m => { const n = s.byStatus[m[0]] || 0; return n ? '<i class="' + m[1] + '" style="width:' + (100 * n / N).toFixed(2) + '%" title="' + m[0] + ': ' + n + '"></i>' : ""; }).join("") + '</div>' +
    '<div class="sq-legend">' + MIX.filter(m => s.byStatus[m[0]]).map(m => '<span><span class="sw ' + m[1] + '"></span>' + m[0] + ' <b>' + (s.byStatus[m[0]] || 0).toLocaleString() + '</b></span>').join("") + '</div>' +
    '<div class="sq-kpis">' + kpi(num(s.uniqueTerms), "distinct terms (" + num(N) + " rows)") + kpi(money(s.totalSpend), "spend") + kpi(fmtConv(s.totalConv).toLocaleString(), "conversions") +
      kpi(money(s.wastedSpend), "spent on not-relevant terms that never converted") + kpi(s.relevantSpendPct == null ? "–" : s.relevantSpendPct + "%", "of spend on relevant terms") +
      kpi(r.priorityNegatives.length, r.priorityNegatives.length === 1 ? "priority negative" : "priority negatives") + kpi(reviewCount, reviewCount === 1 ? "negative to check first" : "negatives to check first") + kpi(r.priorityKeywords.length, r.priorityKeywords.length === 1 ? "priority keyword" : "priority keywords") + '</div>' +
    (skippedBits.length ? hint("Left out: " + skippedBits.join("; ") + ".") : "") +
    '<div class="sq-tabs">' + tab("negs", "Priority negatives", r.priorityNegatives.length) + tab("check", "Check before blocking", reviewCount) + tab("kws", "Priority keywords", r.priorityKeywords.length) +
      tab("review", "Client review", r.clientReview.reduce((a, c) => a + c.count, 0)) + tab("all", "Every term", N) + '</div>' +
    '<div id="panes">' + paneNegs(r) + paneCheck(r) + paneKws(r) + paneReview(r) + paneAll(r) + '</div>' + howRanked() + '</div>';
  const tabs = $id("results").querySelectorAll(".sq-tab");
  const show = k => { tabs.forEach(t => t.classList.toggle("on", t.dataset.k === k)); $id("results").querySelectorAll(".pane").forEach(p => p.hidden = p.id !== "pane-" + k); };
  tabs.forEach(t => t.addEventListener("click", () => show(t.dataset.k)));
  show("negs");
}
function howRanked(){
  return '<details class="sq-ref"><summary>How these are decided and ranked</summary><ul class="sq-how">' +
    '<li><b>Every term</b> goes through the account\'s own rules first. Words are matched as whole words, so "student" is not "dent". A term the rules cannot settle is read by Claude only if it was clicked, costliest first, up to ' + Core.MODEL_CAP.toLocaleString() + ' terms a run. A service word does not settle a term that also hints at jobs, schools, DIY, price shopping, a general question, pets, products or charity: those go to Claude too.</li>' +
    '<li><b>Priority negatives</b> are ranked by the money spent on those terms that never converted. A competitor or not-relevant term that did convert, a negative that would also stop a term judged relevant, one that would stop a keyword the account bids on, one that names a competitor the account bids on, and an account-wide competitor negative in an account that runs a competitor campaign are held back under Check before blocking. Only a negative with money actually wasted is a priority. A negative the account already has (at account level, in that campaign, or in a list attached to it) is not suggested again. A not-relevant term with no rule-given negative gets an exact negative in its campaign only if it spent at least 1 and never converted.</li>' +
    '<li><b>Priority keywords</b> are relevant terms the account does not bid on, in campaigns that take keywords. The rows of a term in several ad groups are added together, and it is suggested for the ad group where it converted most. Tier 1 is at least one whole conversion; tier 2 is a partial conversion, or at least 3 clicks at a click rate of 5% or more. Ranked by tier, conversions, then clicks. Terms Google already marks as added or excluded are left out.</li>' +
    '<li><b>Client review</b> items are ordered by the money they cost.</li></ul></details>';
}
function negRow(n){
  return '<tr><td class="sq-term">' + esc(n.text) + (n.examples.length && n.examples[0] !== n.text ? '<small class="sq-ex">e.g. ' + esc(n.examples.slice(0, 2).join(", ")) + '</small>' : '') + '</td><td>' + esc(n.matchType) + '</td><td>' + esc(where(n)) +
    '</td><td>' + esc(n.reason || "") + '<span class="sq-by">' + esc(source(n)) + '</span></td><td class="r num">' + money(n.cost) + '</td><td class="r num">' + money(n.wasted) + '</td><td class="r num">' + num(n.clicks) + '</td><td class="r num">' + fmtConv(n.conversions) + '</td><td class="r num">' + num(n.terms) + '</td></tr>';
}
function paneNegs(r){
  const ref = r.allNegatives.slice(0, 1000).map(n => '<tr><td class="sq-term">' + esc(n.text) + '</td><td>' + esc(n.matchType) + '</td><td>' + esc(where(n)) + '</td><td class="r num">' + money(n.cost, false) + '</td><td class="r num">' + money(n.wasted, false) + '</td><td class="r num">' + fmtConv(n.conversions) + '</td><td>' + (n.risks.length ? esc(n.risks.join(", ")) : "") + '</td></tr>').join("");
  return '<div class="pane" id="pane-negs" hidden>' + planHtml(Plan.negativePlan(r, (S.ran || {}).range, v => money(v))) + hint("Ranked by the money spent on these terms that never converted. A negative whose terms did convert, that would also stop a relevant term or a keyword the account bids on, or that names a competitor the account bids on (or blocks competitors account-wide when the account runs a competitor campaign), is not here: it is under Check before blocking. A negative with no money wasted is in the full list below.") +
    (r.priorityNegatives.length ? tbl([["Negative"], ["Match"], ["Where"], ["Why"], ["Cost", 1], ["Wasted", 1], ["Clicks", 1], ["Conv", 1], ["Terms", 1]], r.priorityNegatives.map(negRow).join("")) : none()) +
    (ref ? '<details class="sq-ref"><summary>All negatives (' + r.allNegatives.length + ')</summary>' + tbl([["Negative"], ["Match"], ["Where"], ["Cost", 1], ["Wasted", 1], ["Conv", 1], ["Check"]], ref) +
      (r.allNegatives.length > 1000 ? '<p class="pm-panel-s" style="margin-top:10px">Showing the first 1,000. The workbook has every one.</p>' : "") + '</details>' : "") + '</div>';
}
const PRIORITY_N = Core.PRIORITY;
function paneCheck(r){
  const rows = r.reviewNegatives.map(n => '<tr><td class="sq-term">' + esc(n.text) + '</td><td>' + esc(n.matchType) + '</td><td>' + esc(where(n)) + '</td><td>' + esc(n.reason || "") +
    '</td><td>' + n.risks.map(x => '<span class="pm-pill unclear">' + esc(x) + '</span>').join(" ") +
    (n.collateral.n ? '<small class="sq-ex">would also stop ' + esc(n.collateral.ex.join(", ")) + (n.collateral.n > n.collateral.ex.length ? " and " + (n.collateral.n - n.collateral.ex.length) + " more" : "") + '</small>' : '') +
    (n.conflicts.length ? '<small class="sq-ex">stops keyword ' + esc(n.conflicts.join(", ")) + '</small>' : '') + '</td><td class="r num">' + money(n.cost, false) + '</td><td class="r num">' + fmtConv(n.conversions) + '</td></tr>').join("");
  return '<div class="pane" id="pane-check" hidden>' + planHtml(Plan.checkPlan(r)) + hint("These look like negatives, but a person should look first: the terms converted, or the negative would also stop terms judged relevant or a keyword the account bids on.") +
    (rows ? tbl([["Negative"], ["Match"], ["Where"], ["Why"], ["Check"], ["Cost", 1], ["Conv", 1]], rows) : none()) + '</div>';
}
function paneKws(r){
  const rows = r.priorityKeywords.map(k => '<tr><td class="sq-term">' + esc(k.term) + '</td><td>' + esc(k.assetType) + '</td><td>' + esc(k.matchType) + '</td><td>' + esc(k.whereToAdd) + '</td><td class="r num">' + fmtConv(k.conversions) +
    '</td><td class="r num">' + money(k.cost, false) + '</td><td class="r num">' + (k.cpa == null ? "–" : money(k.cpa, false)) + '</td><td class="r num">' + (k.ctr * 100).toFixed(1) + '%</td><td class="r num">' + num(k.clicks) + '</td><td class="r num">' + convRate(k.conversions, k.clicks) + '</td><td><span class="pm-pill ' + (k.tier === 1 ? "ok" : "under") + '">Tier ' + k.tier + '</span></td></tr>').join("");
  const ref = r.allKeywords.slice(PRIORITY_N).map(k => '<tr><td class="sq-term">' + esc(k.term) + '</td><td>' + esc(k.assetType) + '</td><td class="r num">' + fmtConv(k.conversions) + '</td><td class="r num">' + money(k.cost, false) + '</td><td class="r num">' + convRate(k.conversions, k.clicks) + '</td></tr>').join("");
  return '<div class="pane" id="pane-kws" hidden>' + planHtml(Plan.keywordPlan(r)) + hint("Relevant terms the account does not bid on yet. Tier 1 converted at least once; tier 2 drew clicks at a good click rate (or a partial conversion). Ranked by tier, conversions, then clicks.") +
    (rows ? tbl([["Term"], ["Type"], ["Match"], ["Where to add"], ["Conv", 1], ["Cost", 1], ["Cost / conv", 1], ["CTR", 1], ["Clicks", 1], ["Conv rate", 1], ["Tier"]], rows) : none()) +
    (ref ? '<details class="sq-ref"><summary>All eligible keywords (' + r.allKeywords.length + ')</summary>' + tbl([["Term"], ["Type"], ["Conv", 1], ["Cost", 1], ["Conv rate", 1]], ref) + '</details>' : "") + '</div>';
}
function paneReview(r){
  const rows = r.clientReview.map(c => '<tr><td>' + esc(c.subtype) + '</td><td class="r num">' + c.count + '</td><td class="r num">' + money(c.cost, false) + '</td><td class="r num">' + fmtConv(c.conversions) + '</td><td class="sq-term">' + esc(c.examples.join(", ")) + '</td></tr>').join("");
  return '<div class="pane" id="pane-review" hidden>' + (rows ? tbl([["Issue"], ["Rows", 1], ["Cost", 1], ["Conv", 1], ["Costliest examples"]], rows) : none()) + '</div>';
}
function paneAll(r){
  const sorted = r.rows.slice().sort((a, b) => b.cost - a.cost || b.clicks - a.clicks);
  const rows = sorted.slice(0, 500).map(x => '<tr class="' + rowClass(x._det.status) + '"><td class="sq-term">' + esc(x.term) + '</td><td>' + esc(x.campaign) + '</td><td class="r num">' + money(x.cost, false) + '</td><td class="r num">' + fmtConv(x.conversions) +
    '</td><td><span class="pm-pill ' + pillClass(x._det.status) + '">' + esc(x._det.status) + '</span></td><td>' + esc(x._det.subtype || "") + '<span class="sq-by">' + esc(BY[x._det.by] || x._det.by || "") + '</span></td></tr>').join("");
  return '<div class="pane" id="pane-all" hidden>' + tbl([["Term"], ["Campaign"], ["Cost", 1], ["Conv", 1], ["Status"], ["Why"]], rows) +
    (r.rows.length > 500 ? '<p class="pm-panel-s" style="margin-top:10px">Showing the 500 that cost the most of ' + r.rows.length.toLocaleString() + ' term rows. The workbook has every one.</p>' : "") + '</div>';
}

$id("dlBtn").addEventListener("click", () => {
  if (!lastResult || typeof XLSX === "undefined") { setStatus("The spreadsheet library didn't load. Refresh and try again."); return; }
  const r = lastResult, ran = S.ran || {}, wb = XLSX.utils.book_new();
  const add = (name, aoa) => XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), name);
  const w = n => n.campaign ? n.campaign : "";
  add("SQR Analysis", [["Search Term", "Campaign", "Ad Group", "Clicks", "Impr.", "Cost", "Conversions", "Classification", "Subtype", "Decided by"],
    ...r.rows.map(x => [x.term, x.campaign, x.adGroup, x.clicks, x.impressions, x.cost, fmtConv(x.conversions), x._det.status, x._det.subtype || "", BY[x._det.by] || x._det.by || ""])]);
  add("Summary", [["Metric", "Value"], ["Account", ran.account || ""], ["Range", ran.range ? ran.range[0] + " to " + ran.range[1] : ""], ["Data from", ran.from || ""], ["Currency", S.currency], ["Not compared", (ran.notes || []).join(" ")],
    ["Account ruleset", r.ruleset.label], ["Term rows", r.summary.totalRows], ["Distinct terms", r.summary.uniqueTerms], ["Total spend", r.summary.totalSpend.toFixed(2)],
    ["Total conversions", fmtConv(r.summary.totalConv)], ["Spend on not-relevant terms with no conversions", r.summary.wastedSpend.toFixed(2)], ["Spend on competitor terms", r.summary.competitorSpend.toFixed(2)],
    ["Share of spend on relevant terms %", r.summary.relevantSpendPct == null ? "" : r.summary.relevantSpendPct], ["Relevant rows %", r.summary.relevantPct],
    ["Rows settled by rules", r.summary.byRule], ["Rows settled by the model", r.summary.byModel], ["Rows the model failed on (fallback)", r.summary.fallback],
    ["Rows not read (no clicks, or over the review limit)", r.summary.skipped], ...(Plan.saving(r, ran.range, v => money(v)) ? [["What the priority negatives are worth", Plan.saving(r, ran.range, v => money(v))]] : []), ...Object.entries(r.summary.byStatus)]);
  const negHead = ["Negative Text", "Match Type", "Level", "Campaign", "Reason", "Decided by", "Wasted Spend", "Total Cost", "Clicks", "Conversions", "Distinct Terms"];
  const negRowX = n => [n.text, n.matchType, n.level, w(n), n.reason, source(n), n.wasted.toFixed(2), n.cost.toFixed(2), n.clicks, fmtConv(n.conversions), n.terms];
  add("Negatives to Add (Priority)", [negHead, ...r.priorityNegatives.map(negRowX)]);
  add("Negatives to Check First", [[...negHead, "Why check", "Would also stop", "Stops keyword"], ...r.reviewNegatives.map(n => [...negRowX(n), n.risks.join("; "), n.collateral.ex.join("; "), n.conflicts.join("; ")])]);
  add("New Keywords to Add (Priority)", [["Term", "Asset Type", "Suggested Match Type", "Add To Level", "Where To Add", "Tier", "Conversions", "Cost / Conv", "CTR", "Clicks", "Cost", "Conv Rate"],
    ...r.priorityKeywords.map(k => [k.term, k.assetType, k.matchType, k.addLevel, k.whereToAdd, k.tier, fmtConv(k.conversions), k.cpa == null ? "" : k.cpa.toFixed(2), (k.ctr * 100).toFixed(2) + "%", k.clicks, k.cost.toFixed(2), convRate(k.conversions, k.clicks)])]);
  add("Client Review Required", [["Issue Type", "Rows", "Cost", "Conversions", "Costliest Example Terms"], ...r.clientReview.map(c => [c.subtype, c.count, c.cost.toFixed(2), fmtConv(c.conversions), c.examples.join("; ")])]);
  add("All Negatives (Reference)", [[...negHead, "Check"], ...r.allNegatives.map(n => [...negRowX(n), n.risks.join("; ")])]);
  add("All Keywords (Reference)", [["Term", "Asset Type", "Match Type", "Where To Add", "Tier", "Conversions", "CTR", "Clicks", "Cost", "Conv Rate"], ...r.allKeywords.map(k => [k.term, k.assetType, k.matchType, k.whereToAdd, k.tier, fmtConv(k.conversions), (k.ctr * 100).toFixed(2) + "%", k.clicks, k.cost.toFixed(2), convRate(k.conversions, k.clicks)])]);
  const blob = new Blob([XLSX.write(wb, { bookType: "xlsx", type: "array" })], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const slug = String(ran.account || r.ruleset.id).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
  const filename = "SQR_Analysis_" + slug + "_" + new Date().toISOString().slice(0, 10) + ".xlsx";
  const url = URL.createObjectURL(blob), a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  setStatus("Saved " + filename);
});

// =====================================================================
// Boot: the account picker (Google only), the range, and the link's account
// =====================================================================
// An old link says ?account=<rules id>; a new one says ?account=<10-digit Google Ads id>.
const qAccount = new URLSearchParams(location.search).get("account");
if (qAccount && RULESETS[qAccount]) { current = qAccount; S.rulesManual = true; }
// (the old page kept a rules id under the same key, so only a 10-digit Google Ads id is taken from storage)
const ACCT_RE = /^(\d{10}|mcc-\d{10}(-[A-Z]{3})?)$/;                  // a Google Ads account or an MCC ("All of ...")
const wantAcct = qAccount && ACCT_RE.test(qAccount) ? qAccount : (function(){ try { const v = localStorage.getItem("sqr_account") || ""; return ACCT_RE.test(v) ? v : ""; } catch (e) { return ""; } })();
paintButton(); paintWhy(); paintRanges(); computeRange(); refreshReady();

let firstLoad = true;
/* A Google-only agent: the same three choices as every agent, Meta says why it has nothing here and where to look, Both keeps Google and says so. */
if (PL && $id("platSw")) PL.googleOnly($id("platSw"), { name: "Search Query Analysis", why: "Meta has no search terms: people are reached by audience and placement, not by what they typed.",
  closest: [{ label: "Segment Analysis", href: "/p2/paid-marketing/segment-analysis" }, { label: "Audience Segments", href: "/p2/paid-marketing/audience-segments" }], body: "#gBody" });
/* The account picker: every Google Ads account, and each MCC as "All of ..." to read all its accounts at once (the same list the other agents use). */
function chooseAccount(a){
  if (!a) { S.acct = null; setLive("No Google Ads account to read", true); refreshReady(); return; }
  if (S.acct && S.acct.id === a.id && !firstLoad) return;
  firstLoad = false;
  S.acct = a; S.rulesManual = qAccount && RULESETS[qAccount] ? true : false; S.data = null;
  try { localStorage.setItem("sqr_account", a.id); } catch (e) {}
  load(false);
}
const pickList = { accounts: [], groups: [] };
function hueOf(n){ let h = 0; for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) % 360; return h; }
function inits(n){ return n.replace(/\(.*?\)/g, "").split(/[\s&.,-]+/).filter(w => /^[A-Za-z0-9]/.test(w)).slice(0, 2).map(w => w[0]).join("").toUpperCase() || "·"; }
function dashed(id){ return id.length === 10 ? id.slice(0, 3) + "-" + id.slice(3, 6) + "-" + id.slice(6) : id; }
function accountOf(id){
  const g = pickList.groups.find(x => x.id === id);
  if (g) return { id: g.id, name: g.name, currency: g.currency, isGroup: true, accountCount: g.accountIds.length };
  return pickList.accounts.find(x => x.id === id) || null;
}
const picker = window.PMPicker($id("acctPick"), { label: "Ad account", search: true, onChange: v => chooseAccount(accountOf(v)) });
function pickItems(list, groups){
  const items = [{ value: "", label: "Choose an account", sub: list.length + " accounts" + (groups.length ? " · " + groups.length + " MCCs to read together" : ""), avatar: "?", hue: 200 }], seen = {};
  groups.forEach(g => {
    const head = g.name + " MCC";
    items.push({ value: g.id, label: "All of " + g.name, avatar: inits(g.name), hue: hueOf(g.name), group: head, badge: '<span class="badge pm-pill ok">MCC</span>', sub: g.accountIds.length + " accounts read together" });
    list.filter(a => a.group === g.id).forEach(a => { seen[a.id] = 1; items.push({ value: a.id, label: a.name, avatar: inits(a.name), hue: hueOf(a.name), group: head, sub: dashed(a.id) }); });
  });
  list.filter(a => !seen[a.id]).forEach(a => items.push({ value: a.id, label: a.name, avatar: inits(a.name), hue: hueOf(a.name), group: groups.length ? "Other accounts" : "", sub: dashed(a.id) }));
  return items;
}
picker.set([{ value: "", label: "Loading accounts…", sub: "", avatar: "…", hue: 200 }], "", true);
fetch(BASE + "/segments/accounts", { credentials: "same-origin" }).then(r => r.json().catch(() => ({})).then(j => ({ ok: r.ok, status: r.status, j }))).then(r => {
  if (!r.ok) {
    picker.set([{ value: "", label: "Accounts unavailable", sub: "", avatar: "!", hue: 0 }], "", true);
    setLive("Google Ads is not reachable", true);
    const msg = (r.j && r.j.error) || "The account list could not be read.";
    if (r.status === 409) notice("setup", "Google Ads is not connected yet", esc(msg) + " The CSV exports below still work.");
    else notice("bad", "Could not load the Google Ads accounts", esc(msg) + " The CSV exports below still work.");
    $id("csvFallback").open = true;
    return;
  }
  pickList.accounts = r.j.accounts || []; pickList.groups = r.j.groups || [];
  if (!pickList.accounts.length) { notice("warn", "No account chosen", "There are no Google Ads accounts to show. Use the CSV exports below instead."); }
  const want = accountOf(wantAcct) ? wantAcct : "";
  picker.set(pickItems(pickList.accounts, pickList.groups), want);
  if (want) chooseAccount(accountOf(want));
}).catch(() => { setLive("Google Ads is not reachable", true); notice("bad", "Could not load the Google Ads accounts", "The server could not be reached. The CSV exports below still work."); $id("csvFallback").open = true; });
})();
