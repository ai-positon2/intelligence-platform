/* Test & Experiment Tracker (Paid Marketing Agents).
 *
 * Pick a Google Ads account. The server reads every experiment and every ad group that runs two or more ads, compares each test with a two-proportion z-test and
 * decides a verdict and a next step for each (tracker/paid_exp.py and the modules beside it). This page draws the report: what needs a decision, each experiment with
 * its two arms side by side and a chart of how the gap grew, and each ad group with its ads held against the leader. It never reads an account, runs a test or decides
 * a verdict itself; how a verdict is labelled and which tests a view shows are in exp_data.js.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/exp", BASE = "/p2/paid-marketing/api/gads";
var D = window.AuditData, X = window.ExpData, PL = window.PMPlatform;
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var enc = encodeURIComponent;
/* small line icons, drawn inline so they take the colour of the text around them */
var IC = {
  up:'<path d="M5 15l7-7 7 7"/><path d="M12 8v12"/>',
  down:'<path d="M5 9l7 7 7-7"/><path d="M12 16V4"/>',
  equal:'<path d="M6 9h12M6 15h12"/>',
  clock:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  help:'<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1.1.9-1.1 1.7M12 16.9v.2"/>',
  check:'<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  flag:'<path d="M6 21V4"/><path d="M6 5h11l-2 4 2 4H6"/>',
  stop:'<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
  pause:'<path d="M9 6v12M15 6v12"/>',
  crown:'<path d="M4 18h16M4.5 8l4.5 4 3-6 3 6 4.5-4-1.5 10h-12z"/>',
  bolt:'<path d="M13 2.5L4.5 13.5h6.5l-1 8 8.5-11H12z"/>',
  flask:'<path d="M9 3h6"/><path d="M10 3v6.2L4.8 18a1.6 1.6 0 0 0 1.4 2.4h11.6a1.6 1.6 0 0 0 1.4-2.4L14 9.2V3"/><path d="M7.5 14.5h9"/>',
  ads:'<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M7 10h10M7 14h6"/>',
  arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>',
  copy:'<rect x="9" y="9" width="11" height="11" rx="2.2"/><path d="M5.5 15V6.5A2 2 0 0 1 7.5 4.5H15"/>',
  alert:'<path d="M12 8.5v4.5M12 16.6v.4"/><path d="M10.3 4.3L2.9 17.4A2 2 0 0 0 4.6 20.4h14.8a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z"/>'
};
function ic(name, cls){return '<svg class="ex-ic' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (IC[name] || "") + '</svg>';}
var S = {acct:null, report:null, seq:0, expView:"all", adView:"all", adText:"", shown:10, open:{}};
var PAGE = 10;                                           // ad tests drawn before "Show more"
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
function writeHash(){if(S.acct){try{history.replaceState(null, "", "#a=" + enc(S.acct.id));}catch(e){}}}
function lastAccount(){try{return localStorage.getItem("ex_account") || "";}catch(e){return "";}}
function cur(){return (S.report && S.report.account && S.report.account.currency) || "USD";}

/* ── Loading ──────────────────────────────────────────────── */
function run(refresh){
  if(!S.acct){show("empty"); setMsg(""); return;}
  var seq = ++S.seq;
  writeHash(); setMsg(""); show("skel");
  getJson(API + "/report?account=" + enc(S.acct.id) + (refresh === true ? "&refresh=1" : "")).then(function(r){
    if(seq !== S.seq) return;
    if(!r.ok){
      show(S.report ? "out" : "empty");
      if(r.j && r.j.kind === "cooldown"){setMsg(esc(r.j.error), "pm-note"); return;}
      var hint = r.j && r.j.kind === "quota" ? " Google limits how fast this app can read, so this clears on its own." : "";
      setMsg(esc((r.j && r.j.error) || ("The report could not be made (HTTP " + r.status + ")."))+hint);
      return;
    }
    S.report = r.j; S.expView = X.defaultExpView(r.j); S.adView = X.defaultAdView(r.j.adTests); S.adText = ""; S.shown = PAGE; S.open = {};
    $("#adFind").value = "";
    setMsg(""); show("out"); render();
  }).catch(function(){
    if(seq !== S.seq) return;
    show(S.report ? "out" : "empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Small pieces ─────────────────────────────────────────── */
function badge(v, kind){
  var m = X.verdictInfo(v, kind);
  return '<span class="ex-badge ex-t-' + m.tone + '">' + ic(m.icon) + esc(m.label) + '</span>';
}
function stateChip(e){
  return '<span class="ex-state ex-s-' + esc(String(e.state).replace(/\s+/g, "-")) + '">' + esc(X.stateInfo(e.state)) + '</span>';
}
function actionBox(a, tone){
  var k = X.kindInfo(a.kind);
  return '<div class="ex-do ex-t-' + (tone || k.tone) + '">' + ic(k.icon) + '<div><em>' + esc(k.label === "Look" ? "What to do" : k.label) + '</em><p>' + esc(a.text) + '</p></div></div>';
}
function ruler(c){
  var r = X.ruler(c); if(!r) return "";
  return '<div class="ex-ruler' + (r.passed ? " sure" : "") + '" title="How sure the data is that the gap is not chance. A verdict needs 95%.">' +
    '<div class="ex-ruler-bar"><i style="width:' + r.at + '%"></i><span class="ex-ruler-line" style="left:' + r.line + '%"></span></div>' +
    '<div class="ex-ruler-l"><span>coin toss</span><b>' + esc(X.conf(c)) + '</b><span>95% needed</span></div></div>';
}

/* ── The top ──────────────────────────────────────────────── */
function paintTop(rep){
  $("#lead").innerHTML = '<div class="ex-lead-ic">' + ic("flask") + '</div><p>' + esc(X.leadLine(rep)) + '</p>';
  $("#tiles").innerHTML = X.tiles(rep.summary, cur()).map(function(t){
    return '<div class="pd-kpi au-tile ' + t.tone + '"><span>' + esc(t.label) + '</span><b>' + esc(t.value) + '</b><i>' + esc(t.hint) + '</i></div>';
  }).join("");
  var w = $("#warn"), list = (rep.warnings || []);
  w.hidden = !list.length;
  w.innerHTML = list.map(function(x){return ic("alert") + '<span>' + esc(x) + '</span>';}).join("");
}

/* ── Needs a decision ─────────────────────────────────────── */
function paintNeeds(rep){
  var n = rep.needs || [];
  $("#needsCard").hidden = !n.length;
  $("#needsNote").textContent = n.length ? "Biggest decision first, then what the money in each is" : "";
  $("#needs").innerHTML = n.map(function(x, i){
    var k = X.kindInfo(x.kind), what = x.ref.type === "adTest" ? "Ad test" : "Experiment";
    return '<li class="ex-nd ex-t-' + k.tone + '" style="--i:' + i + '">' +
      '<div class="ex-nd-h"><span class="ex-nd-k">' + ic(k.icon) + esc(k.label) + '</span><span class="ex-what">' + esc(what) + '</span>' +
      '<b class="ex-nd-t">' + esc(x.title) + '</b><span class="ex-nd-m">' + esc(X.money(x.spend, cur())) + '</span></div>' +
      '<p>' + esc(x.text) + '</p>' +
      '<button type="button" class="ex-go" data-jump="' + esc(x.ref.type) + ':' + esc(x.ref.id) + '">Open it' + ic("arrow") + '</button></li>';
  }).join("");
}

/* ── Experiments ──────────────────────────────────────────── */
function timeline(e){
  if(!e.start) return "";
  var total = 0, done = 0;
  if(e.end){
    total = Math.max(1, Math.round((Date.parse(e.end) - Date.parse(e.start)) / 86400000));
    done = Math.max(0, Math.min(total, e.daysRunning));
  }
  var pct = total ? Math.round(100 * done / total) : 0;
  var left = e.state !== "running" ? "" : e.overdue ? "past its end" : e.daysLeft == null ? "no end date" : e.daysLeft === 0 ? "ends today" : D.plural(e.daysLeft, "day") + " left";
  return '<div class="ex-time' + (e.overdue ? " late" : "") + '"><div class="ex-time-bar" aria-hidden="true"><i style="width:' + (e.end ? (e.overdue ? 100 : pct) : 0) + '%"></i></div>' +
    '<div class="ex-time-l"><span>' + esc(X.span(e.start, e.end)) + '</span><b>' + esc(e.state === "running" || e.state === "setting up" ? left : "") + '</b></div></div>';
}
function armRow(a, metric, cmp){
  var t = a.total;
  if(!t) return '<tr><th>' + esc(a.name) + '</th><td colspan="7" class="ex-muted">not read</td></tr>';
  return '<tr class="' + (a.control ? "ctl" : "tri") + '"><th><span class="ex-dot"></span>' + esc(a.name) + '<em>' + (a.control ? "control" : "trial") + (a.split != null ? " · " + a.split + "% of traffic" : "") + '</em></th>' +
    '<td>' + esc(X.num(t.impressions)) + '</td><td>' + esc(X.num(t.clicks)) + '</td><td>' + esc(X.pct(t.ctr)) + '</td><td>' + esc(X.num(t.conversions)) + '</td>' +
    '<td>' + esc(X.pct(t.convRate)) + '</td><td>' + esc(X.money(t.cost, cur())) + '</td><td>' + esc(t.cpa == null ? "n/a" : X.money(t.cpa, cur())) + '</td></tr>';
}
function chartSvg(e){
  var c = X.chart(e, 640, 220, 34);
  if(!c.series.length) return '<p class="ex-muted ex-nochart">The chart appears once each arm has enough ' + (c.metric === "ctr" ? "impressions" : "clicks") + ' to draw a rate that is not noise.</p>';
  var g = c.yTicks.map(function(t){
    return '<line class="ex-grid" x1="' + c.pad + '" x2="' + (c.w - c.pad) + '" y1="' + t.y + '" y2="' + t.y + '"/><text class="ex-ax" x="' + (c.pad - 6) + '" y="' + (t.y + 3.5) + '" text-anchor="end">' + esc(X.pct(t.value)) + '</text>';
  }).join("");
  var xs = c.xTicks.map(function(t){return '<text class="ex-ax" x="' + t.x + '" y="' + (c.h - c.pad + 18) + '" text-anchor="middle">' + esc(t.label) + '</text>';}).join("");
  var lines = c.series.map(function(s){
    var cls = s.control ? "ctl" : "tri", end = s.last ? '<circle class="ex-end ' + cls + '" cx="' + s.last.x + '" cy="' + s.last.y + '" r="4.5"/>' : "";
    var area = s.points.length > 1 ? '<path class="ex-area ' + cls + '" d="' + s.d + 'L' + s.points[s.points.length - 1].x + ' ' + (c.h - c.pad) + 'L' + s.points[0].x + ' ' + (c.h - c.pad) + 'Z"/>' : "";
    return area + '<path class="ex-line ' + cls + '" d="' + s.d + '"/>' + end;
  }).join("");
  var legend = c.series.map(function(s){
    return '<span class="ex-key ' + (s.control ? "ctl" : "tri") + '"><i></i>' + esc(s.name) + (s.last ? ' <b>' + esc(X.pct(s.last.rate)) + '</b>' : "") + '</span>';
  }).join("");
  return '<figure class="ex-chart"><figcaption><b>' + esc(c.label) + ' since the start</b><span>each line is the rate the arm had earned by that day</span></figcaption>' +
    '<svg viewBox="0 0 ' + c.w + ' ' + c.h + '" role="img" aria-label="' + esc(c.label + " of each arm by day") + '">' + g + xs + lines + '</svg><div class="ex-legend">' + legend + '</div></figure>';
}
function compareBlock(e){
  var cmp = e.comparison; if(!cmp) return "";
  var b = X.bars(cmp), label = cmp.metricLabel || "rate";
  var rows = "";
  if(b){
    rows = '<div class="ex-vs"><div class="ex-vs-h"><b>' + esc(label.charAt(0).toUpperCase() + label.slice(1)) + '</b>' +
      (cmp.relative != null ? '<span class="ex-rel ex-t-' + (cmp.verdict === "winner" ? "good" : cmp.verdict === "behind" ? "bad" : "neutral") + '">' + esc(X.rel(cmp.relative)) + ' for the trial</span>' : "") + '</div>' +
      '<div class="ex-bar ctl"><span>Control</span><div><i style="width:' + b.control + '%"></i></div><b>' + esc(X.pct(cmp.control)) + '</b></div>' +
      '<div class="ex-bar tri"><span>Trial</span><div><i style="width:' + b.trial + '%"></i></div><b>' + esc(X.pct(cmp.trial)) + '</b></div></div>';
  }
  var extra = "";
  if(cmp.verdict === "winner" || cmp.verdict === "behind" || cmp.verdict === "no_difference") extra = ruler(cmp.confidence);
  if(cmp.verdict === "no_difference" && cmp.detectable) extra += '<p class="ex-fine">With this much data a gap smaller than ' + esc(X.pct(cmp.detectable)) + ' (relative) could not have been told apart from chance.</p>';
  if(cmp.verdict === "too_early" && cmp.wait){
    var w = X.waitText(cmp.wait);
    if(w) extra += '<p class="ex-fine"><b>Still needs</b> ' + esc(w) + (cmp.wait.eta ? ' (about ' + D.plural(cmp.wait.eta, "day") + ' at the current pace)' : '') + '.</p>';
  }
  if(cmp.note) extra += '<p class="ex-fine">' + esc(cmp.note) + '</p>';
  return rows + extra;
}
function experimentCard(e){
  var open = e.id in S.open ? S.open[e.id] : (e.state === "running" || e.state === "setting up");
  var cmp = e.comparison, tone = X.verdictInfo(e.verdict, "trial").tone;
  var arms = e.arms.length ? '<table class="ex-t"><thead><tr><th>Arm</th><th>Impr.</th><th>Clicks</th><th>Click rate</th><th>Conv.</th><th>Conv. rate</th><th>Spend</th><th>Cost / conv.</th></tr></thead><tbody>' +
    e.arms.map(function(a){return armRow(a, cmp && cmp.metric, cmp);}).join("") + '</tbody></table>' : "";
  var camps = e.arms.map(function(a){return a.campaigns.length ? '<li><b>' + esc(a.name) + '</b> runs ' + a.campaigns.map(function(c){return esc(c.name);}).join(", ") + '</li>' : "";}).join("");
  var body = '<div class="ex-xb">' +
    '<div class="ex-xgrid"><div class="ex-xl">' + compareBlock(e) + '</div><div class="ex-xr">' + (e.measured ? chartSvg(e) : "") + '</div></div>' +
    actionBox(e.action, e.measured ? undefined : "unk") + (e.splitNote ? '<p class="ex-split">' + ic("alert") + '<span>' + esc(e.splitNote) + '</span></p>' : "") +
    (e.description ? '<p class="ex-desc">' + esc(e.description) + '</p>' : "") + arms + (camps ? '<ul class="ex-camps">' + camps + '</ul>' : "") +
    (e.clipped ? '<p class="ex-fine">Only the last 365 days are read.</p>' : "") + '</div>';
  return '<li class="ex-x ex-t-' + tone + (open ? " open" : "") + '" data-x="' + esc(e.id) + '">' +
    '<button type="button" class="ex-xh" data-open="x:' + esc(e.id) + '" aria-expanded="' + open + '">' +
    '<span class="ex-xh-main"><b>' + esc(e.name) + '</b><em>' + stateChip(e) + '<span>' + esc(e.typeLabel) + '</span>' + (e.daysRead ? '<span>' + esc(D.plural(e.daysRead, "day")) + ' of results</span>' : '') + '</em></span>' +
    badge(e.verdict, "trial") + '<span class="au-chev" aria-hidden="true"></span></button>' + timeline(e) + (open ? body : "") + '</li>';
}
function paintExperiments(rep){
  var all = rep.experiments || [], c = X.expCounts(rep);
  $("#expCard").hidden = !all.length;
  $("#expNote").textContent = all.length ? D.plural(all.length, "experiment") + " in this account" : "";
  $("#expChips").innerHTML = X.EXP_VIEWS.map(function(v){
    if(!c[v.key] && v.key !== "all" && v.key !== S.expView) return "";
    return '<button type="button" class="pm-chip' + (S.expView === v.key ? " on" : "") + '" role="radio" aria-checked="' + (S.expView === v.key) + '" data-eview="' + v.key + '">' + esc(v.label) + ' · ' + c[v.key] + '</button>';
  }).join("");
  var list = X.filterExperiments(rep, S.expView);
  $("#expList").innerHTML = list.length ? list.map(experimentCard).join("") : '<li class="ex-none">No experiment in this view.</li>';
}

/* ── Ad tests ─────────────────────────────────────────────── */
function adRow(a, t, top){
  var w = a.rate != null && top > 0 ? Math.max(3, Math.round(100 * a.rate / top)) : 0;
  var lead = a.id === t.leader;
  var n = a.numbers || {};
  var rate = a.rate == null ? "n/a" : X.pct(a.rate);
  var vs = a.relative != null && !lead ? '<span class="ex-rel ex-t-' + (a.verdict === "behind" ? "bad" : "neutral") + '">' + esc(X.rel(a.relative)) + ' vs the leader</span>' : "";
  var text = (a.headlines || []).concat(a.descriptions || []);
  var url = /^https?:\/\//i.test(a.url || "") ? a.url : "";
  return '<li class="ex-ad ex-t-' + X.verdictInfo(a.verdict, "ad").tone + (lead ? " lead" : "") + '">' +
    '<div class="ex-ad-h"><div class="ex-ad-t">' + (lead ? '<span class="ex-crown" title="The ad with the best ' + esc(t.metricLabel || "rate") + '">' + ic("crown") + '</span>' : "") + '<b>' + esc(a.label) + '</b></div>' + badge(a.verdict, "ad") + '</div>' +
    '<div class="ex-ad-bar"><div class="ex-bar-track"><i style="width:' + w + '%"></i></div><b>' + esc(rate) + '</b><em>' + esc(t.metricLabel || "") + '</em>' + vs +
    (a.confidence != null && !lead ? '<span class="ex-conf">' + esc(X.conf(a.confidence)) + '</span>' : "") + '</div>' +
    '<div class="ex-ad-n"><span><b>' + esc(X.num(n.impressions)) + '</b> impr.</span><span><b>' + esc(X.num(n.clicks)) + '</b> clicks</span><span><b>' + esc(X.num(n.conversions)) + '</b> conv.</span><span><b>' + esc(X.money(n.cost, cur())) + '</b> spent</span>' +
    (a.strength ? '<span class="ex-str">ad strength: ' + esc(a.strength) + '</span>' : "") + '</div>' +
    (text.length ? '<details class="ex-txt"><summary>Show the ad</summary><div><ul>' + text.map(function(x){return '<li>' + esc(x) + '</li>';}).join("") + '</ul>' + (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(url.replace(/^https?:\/\//, "")) + '</a>' : "") + '</div></details>' : "") + '</li>';
}
function adCard(t){
  var open = !!S.open["a:" + t.id], tone = X.verdictInfo(t.verdict, "ad").tone;
  var top = 0; t.ads.forEach(function(a){if(a.rate != null && a.rate > top) top = a.rate;});
  var body = '<div class="ex-ab">' + actionBox(t.action) +
    (t.note ? '<p class="ex-fine">' + esc(t.note) + '</p>' : "") +
    (t.bar ? '<p class="ex-fine">' + esc(t.eligible) + ' ads had enough to be compared. Each is held against the best one at ' + esc(Math.round((1 - t.bar) * 1000) / 10) + '% confidence' + (t.eligible > 2 ? ' (stricter than 95% because the best of several is not a fair pick)' : '') + '.</p>' : "") +
    '<ul class="ex-ads">' + t.ads.map(function(a){return adRow(a, t, top);}).join("") + '</ul>' +
    (t.adCount > t.shown ? '<p class="ex-fine">' + esc(D.plural(t.adCount, "ad")) + ' are live in this ad group. The ' + t.shown + ' that spent most are shown.</p>' : "") + '</div>';
  return '<li class="ex-g ex-t-' + tone + (open ? " open" : "") + '" data-g="' + esc(t.id) + '">' +
    '<button type="button" class="ex-gh" data-open="a:' + esc(t.id) + '" aria-expanded="' + open + '">' +
    '<span class="ex-gh-main"><b>' + esc(t.name) + '</b><em>' + esc(t.campaign) + '</em></span>' +
    '<span class="ex-gh-n"><b>' + esc(D.plural(t.adCount, "ad")) + '</b><em>' + esc(X.money(t.spend, cur())) + ' in 30 days</em></span>' + badge(t.verdict, "ad") + '<span class="au-chev" aria-hidden="true"></span></button>' + (open ? body : "") + '</li>';
}
function paintAds(rep){
  var all = rep.adTests || [], c = X.adCounts(all);
  $("#adCard").hidden = !all.length;
  var more = (rep.summary || {}).adTests || 0;
  $("#adNote").textContent = all.length ? (more > all.length ? "The " + all.length + " of " + more + " that spent most, last 30 days" : D.plural(all.length, "ad group") + ", last 30 days") : "";
  $("#adChips").innerHTML = X.AD_VIEWS.map(function(v){
    if(!c[v.key] && v.key !== "all" && v.key !== S.adView) return "";
    return '<button type="button" class="pm-chip' + (S.adView === v.key ? " on" : "") + '" role="radio" aria-checked="' + (S.adView === v.key) + '" data-aview="' + v.key + '">' + esc(v.label) + ' · ' + c[v.key] + '</button>';
  }).join("");
  var list = X.filterAdTests(all, S.adView, S.adText), shown = list.slice(0, S.shown);
  $("#adList").innerHTML = shown.length ? shown.map(adCard).join("") : '<li class="ex-none">' + (all.length ? "No ad test matches." : "") + '</li>';
  var btn = $("#moreBtn"), left = list.length - shown.length;
  btn.hidden = left <= 0; btn.textContent = left > 0 ? "Show " + Math.min(PAGE, left) + " more (" + left + " left)" : "";
}

function paintNotes(rep){
  var items = (rep.warnings || []).map(function(w){return "<b>Not read:</b> " + esc(w);}).concat((rep.notes || []).map(esc));
  $("#notesCard").hidden = !items.length;
  $("#notes").innerHTML = items.map(function(x){return "<li>" + x + "</li>";}).join("");
}
function render(){
  var rep = S.report;
  $("#fresh").innerHTML = '<span class="live"></span><b>' + esc((rep.account.name || rep.account.id) + " (" + rep.account.id + ")") + '</b>&nbsp;· read ' + esc(D.ago(rep.readAt)) + ' · made ' + esc(D.ago(rep.generatedAt)) + ' · ' + esc(rep.window || "");
  var st = $("#stale");
  if(rep.stale && rep.stale.length){
    st.hidden = false;
    st.innerHTML = rep.stale.map(function(x){return esc("Google Ads is not answering now, so this report is built from the copy saved " + D.ago(x.savedAt) + ".");}).join(" ");
  } else {st.hidden = true; st.innerHTML = "";}
  paintTop(rep); paintNeeds(rep); paintExperiments(rep); paintAds(rep); paintNotes(rep);
  $("#foot").textContent = "Source: Google Ads (read only), saved for an hour. A verdict is a two-sided two-proportion z-test at 95 percent confidence, made only when a test has 7 days and enough clicks, impressions or conversions to count. " +
    "Conversions are Google's own and may be counted late. Nothing is changed in Google Ads: apply, end or pause a test there.";
}

/* ── Events ───────────────────────────────────────────────── */
function jump(ref){
  var p = ref.split(":"), type = p[0], id = p.slice(1).join(":"), el;
  if(type === "experiment"){
    var inView = X.filterExperiments(S.report, S.expView).some(function(e){return e.id === id;});
    if(!inView) S.expView = "all";
    S.open[id] = true; paintExperiments(S.report);
    el = document.querySelector('.ex-x[data-x="' + id.replace(/"/g, "") + '"]');
  } else {
    S.adView = "all"; S.adText = ""; $("#adFind").value = "";
    var list = X.filterAdTests(S.report.adTests, "all", ""), idx = -1;
    list.forEach(function(t, i){if(t.id === id) idx = i;});
    if(idx >= S.shown) S.shown = idx + 1;
    S.open["a:" + id] = true; paintAds(S.report);
    el = document.querySelector('.ex-g[data-g="' + id.replace(/"/g, "") + '"]');
  }
  if(el){el.classList.add("flash"); try{el.scrollIntoView({behavior:"smooth", block:"start"});}catch(e){el.scrollIntoView();}}
}
document.addEventListener("click", function(e){
  var t = e.target && e.target.closest ? e.target : null; if(!t || !S.report) return;
  var b = t.closest("[data-open]");
  if(b){
    var key = b.getAttribute("data-open"), kind = key.charAt(0), id = key.slice(2);
    if(kind === "x"){
      var exp = (S.report.experiments || []).filter(function(x){return x.id === id;})[0];
      var was = id in S.open ? S.open[id] : !!(exp && (exp.state === "running" || exp.state === "setting up"));
      S.open[id] = !was; paintExperiments(S.report);
    } else {S.open[key] = !S.open[key]; paintAds(S.report);}
    return;
  }
  b = t.closest("[data-jump]"); if(b){jump(b.getAttribute("data-jump")); return;}
  b = t.closest("[data-eview]"); if(b){S.expView = b.getAttribute("data-eview"); paintExperiments(S.report); return;}
  b = t.closest("[data-aview]"); if(b){S.adView = b.getAttribute("data-aview"); S.shown = PAGE; paintAds(S.report); return;}
  if(t.closest("#moreBtn")){S.shown += PAGE; paintAds(S.report);}
});
$("#adFind").addEventListener("input", function(e){S.adText = e.target.value; S.shown = PAGE; if(S.report) paintAds(S.report);});
$("#refreshBtn").addEventListener("click", function(){run(true);});

/* ── Boot ─────────────────────────────────────────────────── */
function boot(){
  var h = readHash(), first = true;
  accts = PL.accounts($("#acctPick"), {url: BASE + "/accounts", allow: ["google"], platform: "google", account: h.a || lastAccount(),
    onChange: function(a){
      if(!a){S.acct = null; S.report = null; show("empty"); setMsg("There are no Google Ads accounts to show.", "pm-note"); return;}
      if(S.acct && a.id === S.acct.id && !first) return;
      first = false; S.acct = a; S.report = null;
      try{localStorage.setItem("ex_account", a.id);}catch(e){}
      run();
    },
    onError: function(err){show("empty"); setMsg(esc((err && err.message) || err || "The accounts could not be loaded."));}});
}
boot();
})();
