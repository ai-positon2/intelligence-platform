/* Landing Page & Relevancy Check (Paid Marketing Agents).
 *
 * Pick a Google Ads account. The server reads what each landing page took, the ads, keywords and search terms behind it, fetches the busiest pages and
 * holds each one against a written checklist (tracker/paid_lp.py and the modules beside it). This page draws the report: the account's score, the checks
 * to fix first, and every page with its checks, its ad groups (and whether the page carries each keyword) and what the page was seen to say. It never reads
 * an account, fetches a page, runs a check or scores anything itself; how a finding is worded and which pages a view shows are in lp_data.js.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/lp", BASE = "/p2/paid-marketing/api/gads";
var D = window.AuditData, L = window.LpData, PL = window.PMPlatform;
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var enc = encodeURIComponent;
var S = {acct:null, report:null, seq:0, view:"all", sort:"spend", text:"", shown:12, open:{}, tab:{}, passed:{}, reads:{}};
var PAGE = 12;                                           // pages drawn before "Show more"
var TABS = [["checks", "Checks"], ["ads", "Ads and keywords"], ["seen", "What we saw"], ["numbers", "Numbers"]];
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
  try{history.replaceState(null, "", "#a=" + enc(S.acct.id));}catch(e){}
}
function lastAccount(){try{return localStorage.getItem("lp_account") || "";}catch(e){return "";}}
function safeUrl(u){return /^https?:\/\//i.test(String(u || "")) ? u : "";}
function cur(){return (S.report && S.report.account && S.report.account.currency) || "USD";}

/* ── Loading ──────────────────────────────────────────────── */
function run(refresh){
  if(!S.acct){show("empty"); setMsg(""); return;}
  var seq = ++S.seq;
  writeHash();
  setMsg("");
  show("skel");
  getJson(API + "/report?account=" + enc(S.acct.id) + (refresh === true ? "&refresh=1" : "")).then(function(r){
    if(seq !== S.seq) return;
    if(!r.ok){
      show(S.report ? "out" : "empty");
      if(r.j && r.j.kind === "cooldown"){setMsg(esc(r.j.error), "pm-note"); return;}
      var hint = r.j && r.j.kind === "quota" ? " Google limits how fast this app can read, so this clears on its own." : "";
      setMsg(esc((r.j && r.j.error) || ("The report could not be made (HTTP " + r.status + ")."))+hint);
      return;
    }
    S.report = r.j; S.view = L.defaultView(r.j.pages); S.sort = "spend"; S.text = ""; S.shown = PAGE; S.open = {}; S.tab = {}; S.passed = {}; S.reads = {};
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
function gradeBadge(p){
  if(!p.rated) return '<span class="au-grade au-tone-none lp-nv" title="Nothing about this page could be judged well enough to score">Not verified</span>';
  return '<span class="au-grade au-tone-' + D.gradeTone(p.grade) + '">' + esc(D.scoreText(p.score)) + (p.grade ? " · " + esc(p.grade) : "") + '</span>';
}
function render(){
  var rep = S.report, s = rep.summary || {};
  $("#fresh").innerHTML = '<span class="live"></span><b>' + esc(L.readNote(rep)) + '</b>&nbsp;· read ' + esc(D.ago(rep.readAt)) + ' · made ' + esc(D.ago(rep.generatedAt)) + ' · ' + esc(rep.window || "");
  var st = $("#stale");
  if(rep.stale && rep.stale.length){
    st.hidden = false;
    st.innerHTML = rep.stale.map(function(x){return esc("Google Ads is not answering now, so this report is built from the copy saved " + D.ago(x.savedAt) + ".");}).join(" ");
  } else {st.hidden = true; st.innerHTML = "";}
  var q = "account=" + enc(S.acct.id);
  $("#dlXlsx").href = API + "/download?" + q + "&file=xlsx";
  $("#dlPdf").href = API + "/download?" + q + "&file=pdf";
  $("#dlZip").href = API + "/download?" + q + "&file=zip";
  paintScore(s, rep); paintTiles(s); paintSecs(rep); paintFix(rep); paintPageControls(); paintPages(); paintNotes(rep);
  $("#foot").textContent = "Source: Google Ads (read only) and the pages themselves, saved for an hour. A score is the share of the checks' weight that passed (critical checks weigh most, a warning counts half); checks that could not be judged are left out and counted. A page that does not open scores 0. The account's score weights each page by what it spent. Nothing is changed in Google Ads: make the changes there and on the site.";
}
function paintScore(s, rep){
  var tone = D.gradeTone(s.grade), r = D.ring(s.score, 56);
  var held = (s.capReasons || []).length ? " The grade is held at C or below because " + s.capReasons.join(", and ") + "." : "";
  var t = s.tally || {}, nothing = !(rep.pages || []).length;
  var sentence = nothing ? "No landing page took money in the last 30 days, so there is nothing to check." :
    s.score == null ? "No page could be judged: none could be read well enough to score." :
    s.critical ? D.plural(s.critical, "critical check") + " failed across the pages." :
    s.high ? "No critical check failed. " + D.plural(s.high, "high check") + " failed across the pages." :
    (t.warn ? "No critical or high check failed. " + D.plural(t.warn, "check") + " " + (t.warn === 1 ? "is a warning." : "are warnings.") : "No check failed and nothing is a warning.");
  $("#score").innerHTML = '<div class="au-ring au-tone-'+tone+'"><svg viewBox="0 0 132 132" aria-hidden="true"><circle class="bg" cx="66" cy="66" r="56"></circle>' +
    '<circle class="fg" cx="66" cy="66" r="56" stroke-dasharray="'+r.dash.toFixed(1)+' '+r.gap.toFixed(1)+'"></circle></svg>' +
    '<div class="au-ring-n"><b>' + esc(D.scoreText(s.score)) + '</b><span>' + (s.grade ? "GRADE " + esc(s.grade) : "") + '</span></div></div>' +
    '<div class="au-score-t"><h2>' + esc(L.readNote(rep)) + '</h2><p>' + esc(sentence + held) + '</p>' +
    '<p>' + esc(D.plural(s.rated || 0, "page") + " scored out of " + (s.checked || 0) + " checked, from " + (s.pagesOfSpend || 0) + " that took money.") + '</p></div>';
}
function paintTiles(s){
  $("#tiles").innerHTML = L.tiles(s, cur()).map(function(t){
    return '<div class="pd-kpi au-tile ' + t.tone + '"><span>' + esc(t.label) + '</span><b>' + esc(t.value) + '</b><i>' + esc(t.hint) + '</i></div>';
  }).join("");
}
function paintSecs(rep){
  var secs = (rep.sections || []).filter(function(x){return x.score != null;});
  $("#sectionsCard").hidden = !secs.length;
  $("#secs").innerHTML = secs.map(function(x){
    return '<div class="lp-sec au-tone-' + D.gradeTone(x.grade) + '"><div class="lp-sec-h"><b>' + esc(x.label) + '</b><span class="au-grade au-tone-' + D.gradeTone(x.grade) + '">' + esc(D.scoreText(x.score)) + ' · ' + esc(x.grade || "") + '</span></div>' +
      '<div class="lp-meter" aria-hidden="true"><i style="width:' + Math.max(2, Math.min(100, x.score)) + '%"></i></div></div>';
  }).join("");
}
function sectionLabel(rep, key){
  var f = (rep.sections || []).filter(function(x){return x.key === key;})[0];
  return f ? f.label : "";
}
function paintFix(rep){
  var fix = rep.fixFirst || [], cat = rep.catalog || {};
  $("#fixCard").hidden = false;
  $("#fixNote").textContent = fix.length ? "One row per check, the worst first: the same problem on twenty pages is one thing to fix. Click a page to open it below." : "";
  $("#fix").innerHTML = fix.length ? fix.map(function(r){
    var c = cat[r.id] || {}, n = r.failed + r.warned;
    var links = (r.pages || []).map(function(x){
      return '<button type="button" class="lp-link" data-page="' + esc(x.pageId) + '" title="' + esc(x.found) + '">' + esc(L.shortPage(x.pageName, 56)) + '</button>';
    }).join("");
    var more = n > (r.pages || []).length ? ' <span class="lp-muted">and ' + (n - r.pages.length) + ' more</span>' : "";
    return '<li><div class="au-fix-h">' + chip("res", r.result) + chip("sev", r.severity) + '<b class="lp-fix-t">' + esc(c.title || r.id) + '</b>' +
      '<span class="au-fix-sec">' + esc(sectionLabel(rep, c.section)) + '</span></div>' +
      '<p class="lp-fix-meta"><b>' + esc(D.plural(n, "page")) + '</b>' + (r.failed ? ' · ' + r.failed + ' failing' : '') + (r.warned ? ' · ' + r.warned + ' with a warning' : '') +
      ' · <b>' + esc(L.moneyText(r.spend, cur())) + '</b> spent on them in 30 days</p>' +
      '<p>For example: ' + esc(r.found) + '</p>' +
      '<p class="lp-fix-pages">' + links + more + '</p>' +
      '<p class="do"><b>What to do:</b> ' + esc(c.fix || "") + '</p></li>';
  }).join("") : '<li><p>Nothing failed and nothing is a warning.</p></li>';
}
function paintPageControls(){
  var pages = S.report.pages || [], c = L.viewCounts(pages);
  $("#pagesCard").hidden = !pages.length;
  $("#viewChips").innerHTML = L.VIEWS.map(function(v){
    if(!c[v.key] && v.key !== "all" && v.key !== S.view) return "";
    return '<button type="button" class="pm-chip' + (S.view === v.key ? " on" : "") + '" role="radio" aria-checked="' + (S.view === v.key) + '" data-view="' + v.key + '">' + esc(v.label) + ' · ' + c[v.key] + '</button>';
  }).join("");
  $("#sortSel").innerHTML = L.SORTS.map(function(x){return '<option value="' + x.key + '"' + (S.sort === x.key ? " selected" : "") + '>' + esc(x.label) + '</option>';}).join("");
}
function dots(p){
  return (p.sections || []).map(function(sec){
    return '<i class="lp-dot au-tone-' + D.gradeTone(sec.grade) + '" title="' + esc(sec.label + ": " + D.scoreText(sec.score)) + '"></i>';
  }).join("");
}
function pageRow(p, open){
  var seen = p.seen || {}, t = (p.summary && p.summary.tally) || {};
  var dev = L.deviceRates(p.devices);
  var sub = seen.title || (seen.h1 && seen.h1[0]) || L.statusText(p.seen);
  var counts = [];
  if(p.rated && p.summary.critical) counts.push('<span class="au-res bad">' + p.summary.critical + ' critical</span>');
  if(t.fail) counts.push('<span class="au-res bad">' + t.fail + ' fail</span>');
  if(t.warn) counts.push('<span class="au-res warn">' + t.warn + ' warn</span>');
  if(!counts.length && p.rated) counts.push('<span class="au-res good">no problems</span>');
  return '<li class="lp-p ' + L.pageState(p) + (open ? " open" : "") + '" data-p="' + esc(p.id) + '"><button type="button" class="lp-ph" aria-expanded="' + !!open + '" data-open="' + esc(p.id) + '">' +
    gradeBadge(p) +
    '<span class="lp-pt"><b>' + esc(L.shortPage(p.id, 70)) + '</b><em>' + esc(sub) + '</em></span>' +
    '<span class="lp-pn"><b>' + esc(L.moneyText(p.spend, cur())) + '</b><em>' + esc(L.numText(p.clicks)) + ' clicks · ' + esc(L.rateText(p.convRate)) + ' convert</em></span>' +
    '<span class="lp-dots" aria-hidden="true">' + dots(p) + '</span>' +
    '<span class="lp-pc">' + counts.join("") + '</span><i class="au-chev"></i></button>' +
    '<div class="lp-pb"' + (open ? "" : " hidden") + '>' + (open ? pageBody(p) : "") + '</div></li>';
}
function paintPages(){
  var pages = S.report.pages || [];
  var all = L.sortPages(L.filterPages(pages, S.view, S.text), S.sort), list = all.slice(0, S.shown);
  $("#pagesNote").textContent = all.length === pages.length ? D.plural(pages.length, "page") + " checked" : all.length + " of " + pages.length + " pages";
  $("#pageList").innerHTML = list.length ? list.map(function(p){return pageRow(p, !!S.open[p.id]);}).join("") : '<li class="au-none">' + (S.text ? "No page matches that." : "No page here.") + '</li>';
  var more = $("#moreBtn");
  more.hidden = all.length <= list.length;
  more.textContent = "Show " + Math.min(PAGE, all.length - list.length) + " more (" + (all.length - list.length) + " left)";
}
function pageById(id){return (S.report.pages || []).filter(function(p){return p.id === id;})[0];}

/* ── One page, opened ─────────────────────────────────────── */
function pageBody(p){
  var tab = S.tab[p.id] || "checks";
  var link = safeUrl(p.url);
  var head = '<div class="lp-pb-h"><div class="pm-chips" role="tablist">' + TABS.map(function(t){
    return '<button type="button" class="pm-chip' + (tab === t[0] ? " on" : "") + '" role="tab" aria-selected="' + (tab === t[0]) + '" data-tab="' + t[0] + '" data-pid="' + esc(p.id) + '">' + esc(t[1]) + '</button>';
  }).join("") + '</div>' + (link ? '<a class="lp-ext" href="' + esc(link) + '" target="_blank" rel="noopener noreferrer">Open the page</a>' : '') + '</div>';
  var body = tab === "ads" ? tabAds(p) : tab === "seen" ? tabSeen(p) : tab === "numbers" ? tabNumbers(p) : tabChecks(p);
  return head + '<div class="lp-tab">' + body + '</div>';
}
function findingHtml(f){
  var tone = (D.RESULT[f.result] || {}).tone || "";
  var aff = f.items || [];
  return '<li class="au-f lp-f ' + tone + '"><div class="lp-fh">' + chip("res", f.result) + chip("sev", f.severity) +
    '<span class="au-ft"><b>' + esc(f.title) + '</b></span></div><p class="lp-found">' + esc(f.found) + '</p>' +
    (aff.length ? '<p class="lp-aff"><b>Affected (' + esc(f.count) + '):</b> ' + esc(D.affectedText(f)) + '</p>' : '') +
    ((f.result === "fail" || f.result === "warn") ? '<p class="lp-do"><b>What to do:</b> ' + esc(f.fix) + '</p>' : '') +
    '<details class="lp-why"><summary>Why it matters</summary><p>' + esc(f.why) + '</p></details></li>';
}
function readList(title, items){
  return (items && items.length) ? '<h4>' + esc(title) + '</h4><ul class="lp-rl">' + items.map(function(x){return '<li>' + esc(x) + '</li>';}).join("") + '</ul>' : '';
}
function readBlock(p){
  if(!(p.adGroups || []).length) return "";
  var r = S.reads[p.id];
  if(!r){
    return '<div class="lp-read"><h4>Read it like a person</h4><p class="lp-muted">Claude reads this page the way someone who clicked the ad would, and says whether it keeps the promise the ad made, what is missing and what to change. ' +
      'It is shown only the ad copy, the keywords, the search terms and the page\'s own visible text, and it never changes a score.</p>' +
      '<button type="button" class="pm-btn-ghost lp-readbtn" data-read="' + esc(p.id) + '">Ask Claude to read this page</button></div>';
  }
  var v = L.verdictInfo(r.verdict);
  return '<div class="lp-read"><h4>Read it like a person' + (v ? ' <span class="lp-on ' + v.tone + '">' + esc(v.label) + '</span>' : '') + '</h4>' +
    (r.promiseKept ? '<p class="lp-rp">' + esc(r.promiseKept) + '</p>' : '') +
    readList(r.source === "model" ? "What the page does not answer" : "What the checks found", r.gaps) + readList(r.source === "model" ? "Change on the page" : "What to do", r.pageEdits) +
    readList("Headlines that use the keyword's words", r.headlineIdeas) + readList("If the page is right, change the ad", r.adEdits) +
    '<p class="lp-muted">' + (r.source === "model" ? "Written by Claude from the ad copy and the page's text. Every number in it is one it was shown; it changes no score." :
      "A model was not available" + (r.unavailable ? " (" + esc(r.unavailable) + ")" : "") + ", so these are the checks' own words for this page.") + '</p></div>';
}
function tabChecks(p){
  var cat = S.report.catalog || {}, all = L.pageFindings(p, cat);
  var showPassed = !!S.passed[p.id];
  var passed = all.filter(function(f){return f.result === "pass" || f.result === "na";}).length;
  var secs = (p.sections || []).map(function(sec){
    var list = sec.findings.map(function(f){return L.full(f, cat);}).filter(function(f){return showPassed || (f.result !== "pass" && f.result !== "na");});
    if(!list.length) return "";
    return '<div class="lp-csec"><h4><span class="au-grade au-tone-' + D.gradeTone(sec.grade) + '">' + esc(D.scoreText(sec.score)) + '</span>' + esc(sec.label) + '</h4><ul class="au-list">' + list.map(findingHtml).join("") + '</ul></div>';
  }).join("");
  var nothing = !secs ? '<p class="au-none">Nothing needs attention on this page.</p>' : "";
  var why = (p.summary && p.summary.capReasons && p.summary.capReasons.length) ? '<p class="lp-note">The grade is held at C or below because ' + esc(p.summary.capReasons.join(", and ")) + '.</p>' : '';
  if(p.summary && p.summary.opens === false) why = '<p class="lp-note">This page does not open, so it scores 0 whatever else is true of it.</p>' + why;
  return readBlock(p) + why + secs + nothing + '<button type="button" class="pm-btn-ghost lp-toggle" data-passed="' + esc(p.id) + '">' + (showPassed ? "Hide" : "Show") + ' the ' + passed + ' checks that passed or do not apply</button>';
}
function onChip(on){var i = L.onInfo(on); return '<span class="lp-on ' + i.tone + '">' + esc(i.label) + '</span>';}
function ratingChip(r){var i = L.ratingInfo(r); return '<span class="lp-rate ' + i.tone + '">' + esc(i.label) + '</span>';}
function tabAds(p){
  var groups = p.adGroups || [];
  if(!groups.length) return '<p class="au-none">No Search ad group sends people to this page. It is reached by Shopping or Performance Max, which have no keywords or ad text to hold a page against, or the ads could not be read.</p>';
  return groups.map(function(g){
    var echo = g.echo == null ? '<span class="lp-on unk">Not checked</span>' :
      g.echo >= 0.6 ? '<span class="lp-on good">The page\'s headings echo "' + esc(g.bestHeadline) + '"</span>' :
      '<span class="lp-on bad">The page\'s headings carry only ' + Math.round(g.echo * 100) + '% of "' + esc(g.bestHeadline) + '"</span>';
    return '<div class="lp-g"><div class="lp-g-h"><b>' + esc(g.name) + '</b><span>' + esc(g.campaign) + ' · ' + esc(L.moneyText(g.cost, cur())) + ' · ' + esc(L.numText(g.clicks)) + ' clicks</span></div>' +
      '<div class="lp-g-e">' + echo + '</div>' +
      (g.headlines && g.headlines.length ? '<p class="lp-heads"><b>Headlines:</b> ' + g.headlines.map(esc).join(" | ") + '</p>' : '') +
      ((g.offers || []).length ? '<p class="lp-offers"><b>Promises in the ads:</b> ' + g.offers.map(function(o){return esc(o.text) + ' ' + onChip(o.on);}).join(" ") + '</p>' : '') +
      (g.keywords.length ? '<table class="lp-t"><thead><tr><th>Keyword</th><th>Spend</th><th>Quality score</th><th>Landing page</th><th>Ad relevance</th><th>On the page</th></tr></thead><tbody>' +
        g.keywords.map(function(k){
          return '<tr><td>' + esc(k.text) + (k.own ? ' <em class="lp-muted">(own address)</em>' : '') + '<em class="lp-muted"> ' + esc(k.match) + '</em></td><td>' + esc(L.moneyText(k.cost, cur())) + '</td><td>' + esc(k.qs == null ? "n/a" : k.qs + "/10") +
            '</td><td>' + ratingChip(k.landingPage) + '</td><td>' + ratingChip(k.adRelevance) + '</td><td>' + onChip(k.on) + '</td></tr>';
        }).join("") + '</tbody></table>' : '<p class="au-none">No keyword with spend was read for this ad group.</p>') +
      (g.terms.length ? '<table class="lp-t"><thead><tr><th>What people searched</th><th>Clicks</th><th>Spend</th><th>On the page</th></tr></thead><tbody>' +
        g.terms.map(function(t){return '<tr><td>' + esc(t.text) + '</td><td>' + esc(L.numText(t.clicks)) + '</td><td>' + esc(L.moneyText(t.cost, cur())) + '</td><td>' + onChip(t.on) + '</td></tr>';}).join("") + '</tbody></table>' : '') +
      '</div>';
  }).join("") + (p.groupCount > groups.length ? '<p class="lp-muted">and ' + (p.groupCount - groups.length) + ' more ad groups. The Excel file lists the keywords and search terms the read kept.</p>' : '');
}
function tabSeen(p){
  var rows = L.seenRows(p.seen);
  if(!rows.length) return '<p class="au-none">This page was not fetched.</p>';
  return '<dl class="lp-dl">' + rows.map(function(r){return '<dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd>';}).join("") + '</dl>' +
    '<p class="lp-muted">Seen in the page\'s HTML as a browser first receives it. Whatever a script adds after the page loads, or a tag a consent banner holds back, is not seen here.</p>';
}
function labBlock(p){
  var rep = S.report, conf = !!(rep.lab && rep.lab.configured), lab = p.lab;
  var btn = function(text){return '<button type="button" class="pm-btn-ghost lp-labbtn" data-lab="' + esc(p.id) + '">' + esc(text) + '</button>';};
  if(!lab){
    return '<div class="lp-lab"><h4>Lab speed test</h4>' + (conf ?
      '<p class="lp-muted">Google loads the page like a mid-range phone on a mobile connection and times what a visitor sees. It takes up to half a minute.</p>' + btn("Run the lab speed test") :
      '<p class="lp-muted">The lab speed test is not connected. An admin sets PAGESPEED_API_KEY on the server (a Google Cloud API key with the PageSpeed Insights API enabled). The checks above use the server time, the page weight and Google\'s own speed score meanwhile.</p>') + '</div>';
  }
  var tone = L.labTone(lab.score);
  return '<div class="lp-lab"><h4>Lab speed test <span class="lp-on ' + tone + '">' + esc(lab.score == null ? "not scored" : lab.score + " / 100") + '</span></h4>' +
    '<dl class="lp-dl">' + L.labRows(lab).map(function(r){return '<dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd>';}).join("") + '</dl>' +
    ((lab.savings || []).length ? '<h4>Biggest savings Lighthouse names</h4><ul class="lp-sav">' + lab.savings.map(function(x){return '<li>' + esc(x.title) + ' <em class="lp-muted">about ' + esc(L.secText(x.ms)) + '</em></li>';}).join("") + '</ul>' : '') +
    '<p class="lp-muted">Run ' + esc(D.ago(lab.ranAt)) + '.</p>' + (conf ? btn("Run it again") : '') + '</div>';
}
function tabNumbers(p){
  var d = L.deviceRates(p.devices), rows = [];
  [["mobile", "Phones"], ["desktop", "Desktops"], ["tablet", "Tablets"]].forEach(function(k){
    var v = d[k[0]]; if(v) rows.push('<tr><td>' + k[1] + '</td><td>' + esc(L.numText(v.clicks)) + '</td><td>' + esc(L.moneyText(v.cost, cur())) + '</td><td>' + esc(L.numText(v.conversions)) + '</td><td>' + esc(L.rateText(v.rate)) + '</td></tr>');
  });
  return '<div class="lp-nums"><div><span>Spend</span><b>' + esc(L.moneyText(p.spend, cur())) + '</b></div><div><span>Clicks</span><b>' + esc(L.numText(p.clicks)) + '</b></div>' +
    '<div><span>Conversions</span><b>' + esc(L.numText(p.conversions)) + '</b></div><div><span>Conversion rate</span><b>' + esc(L.rateText(p.convRate)) + '</b></div>' +
    '<div><span>Cost per conversion</span><b>' + esc(p.cpa == null ? "n/a" : L.moneyText(p.cpa, cur())) + '</b></div>' +
    '<div><span>Google mobile speed</span><b>' + esc(p.speed == null ? "no score" : p.speed + " / 10") + '</b></div></div>' +
    (rows.length ? '<table class="lp-t"><thead><tr><th>Device</th><th>Clicks</th><th>Spend</th><th>Conversions</th><th>Conversion rate</th></tr></thead><tbody>' + rows.join("") + '</tbody></table>' : '') +
    (d.gap != null ? '<p class="lp-note">Phones convert at ' + esc(Math.round(d.gap * 100)) + '% of the desktop rate on this page.</p>' : '') + labBlock(p);
}
function paintNotes(rep){
  var items = (rep.warnings || []).map(function(w){return "<b>Not read:</b> " + esc(w);}).concat((rep.notes || []).map(esc));
  $("#notesCard").hidden = !items.length;
  $("#notes").innerHTML = items.map(function(x){return "<li>" + x + "</li>";}).join("");
}

/* ── The lab speed test ───────────────────────────────────── */
function runLab(id, btn){
  if(!S.acct || btn.disabled) return;
  btn.disabled = true; var label = btn.textContent; btn.textContent = "Running the test. This takes up to half a minute...";
  var seq = S.seq;
  fetch(API + "/lab", {method:"POST", credentials:"same-origin", headers:{"Content-Type":"application/json"}, body:JSON.stringify({account:S.acct.id, page:id})}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok, status:r.status, j:j};});
  }).then(function(r){
    if(seq !== S.seq) return;
    if(!r.ok){btn.disabled = false; btn.textContent = label; setMsg(esc((r.j && r.j.error) || ("The test could not be run (HTTP " + r.status + ").")), "pm-err"); return;}
    setMsg("");
    return getJson(API + "/report?account=" + enc(S.acct.id)).then(function(rep){       // the saved read: the page's score now counts the test
      if(seq !== S.seq || !rep.ok) return;
      S.report = rep.j; render();
    });
  }).catch(function(){btn.disabled = false; btn.textContent = label; setMsg("The server could not be reached. Try again.");});
}

/* ── Read it like a person ────────────────────────────────── */
function readPage(id, btn){
  if(!S.acct || btn.disabled) return;
  btn.disabled = true; var label = btn.textContent; btn.textContent = "Reading the page. This takes a few seconds...";
  var seq = S.seq;
  fetch(API + "/read", {method:"POST", credentials:"same-origin", headers:{"Content-Type":"application/json"}, body:JSON.stringify({account:S.acct.id, page:id})}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok, status:r.status, j:j};});
  }).then(function(r){
    if(seq !== S.seq) return;
    if(!r.ok){btn.disabled = false; btn.textContent = label; setMsg(esc((r.j && r.j.error) || ("The page could not be read (HTTP " + r.status + ").")), "pm-err"); return;}
    setMsg(""); S.reads[id] = r.j.read; repaintPage(id);
  }).catch(function(){btn.disabled = false; btn.textContent = label; setMsg("The server could not be reached. Try again.");});
}

/* ── Events ───────────────────────────────────────────────── */
function repaintPage(id){
  var p = pageById(id); if(!p) return;
  var li = document.querySelector('.lp-p[data-p="' + id.replace(/"/g, "") + '"]'); if(!li) return;
  var tmp = document.createElement("div"); tmp.innerHTML = pageRow(p, !!S.open[id]);
  li.parentNode.replaceChild(tmp.firstChild, li);
}
function openPage(id, scroll){
  var p = pageById(id); if(!p) return;
  if(!all_visible(id)){S.view = "all"; S.text = ""; $("#pageFind").value = ""; var idx = L.sortPages(L.filterPages(S.report.pages, "all", ""), S.sort).indexOf(p); if(idx >= S.shown) S.shown = idx + 1; paintPageControls();}
  S.open[id] = true; paintPages();
  var li = document.querySelector('.lp-p[data-p="' + id.replace(/"/g, "") + '"]');
  if(li && scroll){li.classList.add("flash"); try{li.scrollIntoView({behavior: "smooth", block: "start"});}catch(e){li.scrollIntoView();}}
}
function all_visible(id){
  var list = L.sortPages(L.filterPages(S.report.pages, S.view, S.text), S.sort).slice(0, S.shown);
  return list.some(function(p){return p.id === id;});
}
document.addEventListener("click", function(e){
  var t = e.target && e.target.closest ? e.target : null; if(!t) return;
  var b = t.closest("[data-open]"); if(b){var id = b.getAttribute("data-open"); S.open[id] = !S.open[id]; repaintPage(id); return;}
  b = t.closest("[data-page]"); if(b){var pid = b.getAttribute("data-page"); if(pid) openPage(pid, true); return;}
  b = t.closest("[data-tab]"); if(b){var q = b.getAttribute("data-pid"); S.tab[q] = b.getAttribute("data-tab"); repaintPage(q); return;}
  b = t.closest("[data-passed]"); if(b){var r = b.getAttribute("data-passed"); S.passed[r] = !S.passed[r]; repaintPage(r); return;}
  b = t.closest("[data-view]"); if(b){S.view = b.getAttribute("data-view"); S.shown = PAGE; paintPageControls(); paintPages(); return;}
  b = t.closest("[data-lab]"); if(b){runLab(b.getAttribute("data-lab"), b); return;}
  b = t.closest("[data-read]"); if(b){readPage(b.getAttribute("data-read"), b); return;}
  if(t.closest("#moreBtn")){S.shown += PAGE; paintPages(); return;}
});
$("#sortSel").addEventListener("change", function(e){S.sort = e.target.value; S.shown = PAGE; paintPages();});
$("#pageFind").addEventListener("input", function(e){S.text = e.target.value; S.shown = PAGE; paintPages();});
$("#refreshBtn").addEventListener("click", function(){run(true);});

/* ── Boot ─────────────────────────────────────────────────── */
function boot(){
  var h = readHash(), first = true;
  accts = PL.accounts($("#acctPick"), {url: BASE + "/accounts", allow: ["google"], platform: "google", account: h.a || lastAccount(),
    onChange: function(a){
      if(!a){S.acct = null; S.report = null; show("empty"); setMsg("There are no Google Ads accounts to show.", "pm-note"); return;}
      if(S.acct && a.id === S.acct.id && !first) return;
      first = false; S.acct = a; S.report = null;
      try{localStorage.setItem("lp_account", a.id);}catch(e){}
      run();
    },
    onError: function(err){show("empty"); setMsg(esc((err && err.message) || err || "The accounts could not be loaded."));}});
}
boot();
})();
