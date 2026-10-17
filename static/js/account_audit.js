/* Account Audit (Paid Marketing Agents).
 *
 * Pick a Google Ads account, a Meta ad account or one of each. The server reads the account and holds it against a written checklist
 * (tracker/paid_audit.py and the checklists beside it). This page draws the report: a score, the checks to fix first, and every section's
 * findings with a filter. It never reads an account, runs a check or scores anything itself; how a finding is worded and which ones a
 * view shows are in au_data.js.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/audit", BASE = "/p2/paid-marketing/api/gads";
var D = window.AuditData, PL = window.PMPlatform;
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var enc = encodeURIComponent;
var S = {acct:null, report:null, seq:0, views:{}, filters:{}, open:{}, shown:{}};
var PAGE = 40;                                           // findings drawn per section before "Show more"
var accts = null;

function setMsg(html, kind){$("#msg").innerHTML = html ? '<div class="'+(kind||"pm-err")+'">'+html+'</div>' : "";}
function show(which){$("#empty").hidden = which !== "empty"; $("#skel").hidden = which !== "skel"; $("#out").hidden = which !== "out";}
function getJson(url){
  return fetch(url, {credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok, status:r.status, j:j};});
  });
}
function readHash(){
  var h = {}; location.hash.replace(/^#/, "").split("&").forEach(function(p){
    var i = p.indexOf("="); if(i > 0) h[p.slice(0, i)] = decodeURIComponent(p.slice(i + 1));
  });
  return h;
}
function writeHash(){
  if(!S.acct) return;
  try{history.replaceState(null, "", "#pf=" + enc(S.acct.platform) + "&a=" + enc(S.acct.id));}catch(e){}
}
function lastAccount(){try{return localStorage.getItem("au_account") || "";}catch(e){return "";}}

/* ── Loading ──────────────────────────────────────────────── */
function run(refresh){
  if(!S.acct){show("empty"); setMsg(""); return;}
  var seq = ++S.seq;
  writeHash();
  setMsg(S.acct.platform === "both" ? "Reading both accounts. The first read takes up to half a minute." : "", S.acct.platform === "both" ? "pm-note" : "");
  show("skel");
  getJson(API + "/report?account=" + enc(S.acct.id) + (refresh === true ? "&refresh=1" : "")).then(function(r){
    if(seq !== S.seq) return;
    if(!r.ok){
      show(S.report ? "out" : "empty");
      if(r.j && r.j.kind === "cooldown"){setMsg(esc(r.j.error), "pm-note"); return;}
      var hint = r.j && r.j.kind === "quota" ? " The platform limits how fast this app can read, so this clears on its own." : "";
      setMsg(esc((r.j && r.j.error) || ("The audit could not be made (HTTP " + r.status + ")."))+hint);
      return;
    }
    S.report = r.j; S.views = {}; S.filters = {}; S.open = {}; S.shown = {};
    setMsg("");
    show("out"); render();
  }).catch(function(){
    if(seq !== S.seq) return;
    show(S.report ? "out" : "empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Drawing ──────────────────────────────────────────────── */
function chip(kind, key){
  var m = (kind === "res" ? D.RESULT : D.SEVERITY)[key] || {label: key, tone: ""};
  return '<span class="au-'+kind+' '+(kind === "res" ? m.tone : key)+'">'+esc(m.label)+'</span>';
}
function render(){
  var rep = S.report, s = rep.summary || {};
  $("#fresh").innerHTML = '<span class="live"></span><b>' + esc(D.readNote(rep)) + '</b>&nbsp;· read ' + esc(D.ago(rep.readAt)) + ' · made ' + esc(D.ago(rep.generatedAt));
  var st = $("#stale");
  if(rep.stale && rep.stale.length){
    st.hidden = false;
    st.innerHTML = rep.stale.map(function(x){return esc((D.PLATFORM[x.platform] || x.platform) + " is not answering now, so its part of this audit is the copy saved " + D.ago(x.savedAt) + ".");}).join(" ");
  } else {st.hidden = true; st.innerHTML = "";}
  var q = "account=" + enc(S.acct.id);
  $("#dlXlsx").href = API + "/download?" + q + "&file=xlsx";
  $("#dlPdf").href = API + "/download?" + q + "&file=pdf";
  $("#dlZip").href = API + "/download?" + q + "&file=zip";
  paintScore(s, rep); paintTiles(s); paintFix(rep); paintSections(rep); paintNotes(rep);
  $("#foot").textContent = "Source: the platform's own API, read only and saved for an hour. A score is the share of the checks' weight that passed (critical checks weigh most, a warning counts half), and checks that could not be read are left out of it and counted. A failed critical check keeps the grade at C or below. Nothing is changed in either platform: make the changes there.";
}
function paintScore(s, rep){
  var tone = D.gradeTone(s.grade), r = D.ring(s.score, 56);
  var plats = Object.keys(s.platforms || {}).map(function(p){
    var v = s.platforms[p];
    return '<span class="au-plat au-tone-'+D.gradeTone(v.grade)+'">' + esc(D.PLATFORM[p] || p) + ' <b>' + esc(D.scoreText(v.score)) + (v.grade ? " (" + esc(v.grade) + ")" : "") + '</b></span>';
  }).join("");
  var judged = s.judged || 0;
  var fails = s.tally ? s.tally.fail : 0, warns = s.tally ? s.tally.warn : 0;
  var sentence = s.score == null ? "Nothing could be judged: every part the checks need could not be read." :
    s.critical ? D.plural(s.critical, "critical check") + " failed, so the grade is held at C or below." :
    fails ? "No critical check failed. " + D.plural(fails, "check") + " failed and " + D.plural(warns, "check") + (warns === 1 ? " is a warning." : " are warnings.") :
    warns ? "No check failed. " + D.plural(warns, "check") + (warns === 1 ? " is a warning." : " are warnings.") : "No check failed and nothing is a warning.";
  $("#score").innerHTML = '<div class="au-ring au-tone-'+tone+'"><svg viewBox="0 0 132 132" aria-hidden="true"><circle class="bg" cx="66" cy="66" r="56"></circle>' +
    '<circle class="fg" cx="66" cy="66" r="56" stroke-dasharray="'+r.dash.toFixed(1)+' '+r.gap.toFixed(1)+'"></circle></svg>' +
    '<div class="au-ring-n"><b>' + esc(D.scoreText(s.score)) + '</b><span>' + (s.grade ? "GRADE " + esc(s.grade) : "") + '</span></div></div>' +
    '<div class="au-score-t"><h2>' + esc(D.readNote(rep)) + '</h2><p>' + esc(sentence) + ' ' + esc(judged + " of the " + ((s.tally && s.tally.total) || judged) + " checks could be judged.") + '</p><div class="au-plats">' + plats + '</div></div>';
}
function paintTiles(s){
  $("#tiles").innerHTML = D.tiles(s).map(function(t){
    return '<div class="pd-kpi au-tile ' + t.tone + '"><span>' + esc(t.label) + '</span><b>' + esc(t.value) + '</b><i>' + esc(t.hint) + '</i></div>';
  }).join("");
}
function sectionLabel(rep, id){
  var f = null;
  (rep.sections || []).forEach(function(sec){sec.findings.forEach(function(x){if(x.id === id) f = sec;});});
  return f ? (D.PLATFORM[f.platform] || f.platform) + ": " + f.label : "";
}
function paintFix(rep){
  var fix = rep.fixFirst || [];
  $("#fixNote").textContent = fix.length ? "The worst failures and warnings, most severe first. Click one to open it below." : "";
  $("#fix").innerHTML = fix.length ? fix.map(function(f){
    var aff = D.affectedText(f);
    return '<li><div class="au-fix-h">' + chip("res", f.result) + chip("sev", f.severity) + '<button type="button" data-jump="' + esc(f.id) + '">' + esc(f.title) + '</button>' +
      '<span class="au-fix-sec">' + esc(sectionLabel(rep, f.id)) + '</span></div>' +
      '<p>' + esc(f.found) + '</p>' +
      (aff ? '<p class="do"><b>Affected:</b> ' + esc(aff.length > 260 ? aff.slice(0, 257) + "..." : aff) + '</p>' : '') +
      '<p class="do"><b>What to do:</b> ' + esc(f.fix) + '</p></li>';
  }).join("") : '<li><p>Nothing failed and nothing is a warning.</p></li>';
}
function viewChips(sec){
  var cur = S.views[sec.key] || D.defaultView(sec.findings);
  return D.VIEWS.map(function(v){
    var n = D.viewCount(sec.findings, v.key);
    if(!n && v.key !== "all" && v.key !== cur) return "";
    return '<button type="button" class="pm-chip' + (cur === v.key ? " on" : "") + '" role="radio" aria-checked="' + (cur === v.key) + '" data-sec="' + esc(sec.key) + '" data-view="' + v.key + '">' + esc(v.label) + ' · ' + n + '</button>';
  }).join("");
}
function findingHtml(f, open){
  var tone = (D.RESULT[f.result] || {}).tone || "";
  var aff = (f.items || []);
  var body = '<div class="au-fb"' + (open ? "" : " hidden") + '>' +
    '<h4>Why it matters</h4><p>' + esc(f.why) + '</p>' +
    ((f.result === "fail" || f.result === "warn" || f.result === "unknown") ? '<h4>What to do</h4><p>' + esc(f.fix) + '</p>' : '') +
    (aff.length ? '<h4>Affected (' + esc(f.count) + ')</h4><ul>' + aff.map(function(i){return '<li>' + esc(i) + '</li>';}).join("") + '</ul>' +
      ((f.count || aff.length) > aff.length ? '<p class="au-more">and ' + esc((f.count) - aff.length) + ' more. The Excel file lists everything the read kept.</p>' : '') : '') +
    '</div>';
  return '<li class="au-f ' + tone + (open ? " open" : "") + '" data-f="' + esc(f.id) + '"><button type="button" class="au-fh" aria-expanded="' + !!open + '" data-toggle="' + esc(f.id) + '">' +
    chip("res", f.result) + chip("sev", f.severity) + '<span class="au-ft"><b>' + esc(f.title) + '</b><em>' + esc(f.id) + '</em></span><i class="au-chev"></i>' +
    '<span class="au-found">' + esc(f.found) + '</span></button>' + body + '</li>';
}
function paintSection(sec){
  var view = S.views[sec.key] || D.defaultView(sec.findings), text = S.filters[sec.key] || "";
  var all = D.filterFindings(sec.findings, view, text), limit = S.shown[sec.key] || PAGE, list = all.slice(0, limit);
  var t = sec.tally, total = Math.max(1, t.fail + t.warn + t.unknown + t.pass), tone = D.gradeTone(sec.grade);
  var bar = [["bad", t.fail], ["warn", t.warn], ["unk", t.unknown], ["good", t.pass]].map(function(p){
    return p[1] ? '<i class="' + p[0] + '" style="flex:' + p[1] + '" title="' + p[1] + ' ' + p[0] + '"></i>' : "";
  }).join("");
  return '<section class="pm-panel au-sec" id="sec-' + esc(sec.key) + '" data-sec="' + esc(sec.key) + '">' +
    '<div class="au-sec-h"><span class="au-grade au-tone-' + tone + '">' + esc(D.scoreText(sec.score)) + (sec.grade ? " · " + esc(sec.grade) : "") + '</span>' +
    '<div class="au-sec-t"><b>' + esc(sec.label) + '</b><span>' + esc(D.PLATFORM[sec.platform] || sec.platform) + ' · ' + esc(D.plural(t.total, "check")) +
    (t.unknown ? ' · ' + esc(t.unknown) + ' could not be read' : '') + '</span></div><div class="au-bar" aria-hidden="true">' + bar + '</div></div>' +
    '<div class="au-tools"><div class="pm-chips" role="radiogroup" aria-label="Which checks">' + viewChips(sec) + '</div>' +
    '<label class="au-find"><input type="text" data-find="' + esc(sec.key) + '" value="' + esc(text) + '" placeholder="Find a check..." aria-label="Find a check in ' + esc(sec.label) + '" autocomplete="off"></label></div>' +
    (list.length ? '<ul class="au-list">' + list.map(function(f){return findingHtml(f, !!S.open[f.id]);}).join("") + '</ul>' : '<p class="au-none">' + (text ? "No check matches that." : "Nothing here.") + '</p>') +
    (all.length > list.length ? '<button type="button" class="pm-btn-ghost au-more-btn" data-more="' + esc(sec.key) + '">Show ' + Math.min(PAGE, all.length - list.length) + ' more (' + (all.length - list.length) + ' left)</button>' : '') + '</section>';
}
function paintSections(rep){
  $("#sections").innerHTML = (rep.sections || []).map(paintSection).join("");
}
function repaintSection(key){
  var sec = (S.report.sections || []).filter(function(x){return x.key === key;})[0]; if(!sec) return;
  var host = document.getElementById("sec-" + key), focusFind = document.activeElement && document.activeElement.getAttribute && document.activeElement.getAttribute("data-find") === key;
  var caret = focusFind ? document.activeElement.selectionStart : null;
  var tmp = document.createElement("div"); tmp.innerHTML = paintSection(sec);
  host.parentNode.replaceChild(tmp.firstChild, host);
  if(focusFind){var inp = document.querySelector('[data-find="' + key + '"]'); if(inp){inp.focus(); try{inp.setSelectionRange(caret, caret);}catch(e){}}}
}
function paintNotes(rep){
  var items = (rep.warnings || []).map(function(w){return "<b>Not read:</b> " + esc(w);}).concat((rep.notes || []).map(esc));
  $("#notesCard").hidden = !items.length;
  $("#notes").innerHTML = items.map(function(x){return "<li>" + x + "</li>";}).join("");
}

/* ── Events ───────────────────────────────────────────────── */
function toggle(id){
  S.open[id] = !S.open[id];
  var li = document.querySelector('.au-f[data-f="' + id.replace(/"/g, "") + '"]'); if(!li) return;
  li.classList.toggle("open", S.open[id]);
  li.querySelector(".au-fh").setAttribute("aria-expanded", String(!!S.open[id]));
  li.querySelector(".au-fb").hidden = !S.open[id];
}
function jump(id){
  var key = null, rep = S.report;
  (rep.sections || []).forEach(function(sec){sec.findings.forEach(function(f){if(f.id === id) key = sec.key;});});
  if(!key) return;
  var sec = rep.sections.filter(function(x){return x.key === key;})[0], f = sec.findings.filter(function(x){return x.id === id;})[0];
  S.views[key] = "all"; S.filters[key] = ""; S.open[id] = true;
  var idx = sec.findings.indexOf(f); if(idx >= (S.shown[key] || PAGE)) S.shown[key] = idx + 1;
  repaintSection(key);
  var li = document.querySelector('.au-f[data-f="' + id.replace(/"/g, "") + '"]');
  if(li){li.classList.add("flash"); try{li.scrollIntoView({behavior: "smooth", block: "center"});}catch(e){li.scrollIntoView();}}
}
document.addEventListener("click", function(e){
  var t = e.target && e.target.closest ? e.target : null; if(!t) return;
  var b = t.closest("[data-toggle]"); if(b){toggle(b.getAttribute("data-toggle")); return;}
  b = t.closest("[data-jump]"); if(b){jump(b.getAttribute("data-jump")); return;}
  b = t.closest("[data-view]"); if(b){S.views[b.getAttribute("data-sec")] = b.getAttribute("data-view"); S.shown[b.getAttribute("data-sec")] = 0; repaintSection(b.getAttribute("data-sec")); return;}
  b = t.closest("[data-more]"); if(b){var k = b.getAttribute("data-more"); S.shown[k] = (S.shown[k] || PAGE) + PAGE; repaintSection(k); return;}
});
document.addEventListener("input", function(e){
  var t = e.target; if(!t || !t.getAttribute || t.getAttribute("data-find") == null) return;
  var k = t.getAttribute("data-find"); S.filters[k] = t.value; S.shown[k] = 0; repaintSection(k);
});
$("#refreshBtn").addEventListener("click", function(){run(true);});

/* ── Boot ─────────────────────────────────────────────────── */
function boot(){
  var h = readHash(), first = true;
  accts = PL.accounts($("#acctPick"), {url: BASE + "/accounts", allow: ["google", "meta", "both"], platform: h.pf, account: h.a || lastAccount(),
    onChange: function(a, platform){
      if(!a){
        S.acct = null; S.report = null; show("empty");
        setMsg((platform === "meta" ? "There are no Meta ad accounts this token can read yet." : "There are no accounts to show for this platform.") + " Pick another platform or account above.", "pm-note");
        return;
      }
      if(S.acct && a.id === S.acct.id && !first) return;
      first = false; S.acct = a; S.report = null;
      try{localStorage.setItem("au_account", a.id);}catch(e){}
      run();
    },
    onError: function(err){show("empty"); setMsg(esc((err && err.message) || err || "The accounts could not be loaded."));}});
}
boot();
})();
