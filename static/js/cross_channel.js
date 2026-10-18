/* Cross-Channel & Attribution Analysis (Paid Marketing Agents).
 *
 * Pick a Google Ads account, a Meta ad account or one of each, a range and a conversion measure, and optionally the conversions and revenue your own system recorded. The server reads
 * both platforms, does every sum and ranks every finding (tracker/paid_xchannel.py and the modules beside it). This page draws the report: what to look at, the platforms side by side,
 * what moved the blended cost per conversion, the campaign types, the weeks, how each platform counts a conversion and the platforms' claims against your own numbers. It never reads an
 * account or works out a figure itself; how a figure reads and the shape of the charts are in xch_data.js.
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/xchannel", BASE = "/p2/paid-marketing/api/gads";
var D = window.AuditData, X = window.XchData, PL = window.PMPlatform;
var $ = function(s){return document.querySelector(s)};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var enc = encodeURIComponent;
var S = {acct:null, range:"30", measure:"primary", conv:"", value:"", report:null, seq:0};
var METRICS = [["cost","Spend","money"],["clicks","Clicks","num"],["conv","Conversions","num"],["value","Conversion value","money"],["cpa","Cost per conversion","money"],
  ["roas","Return on ad spend","x"],["ctr","Click rate","pct1"],["cpc","Cost per click","money"],["cvr","Conversion rate","pct1"]];
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
function writeHash(){if(S.acct){try{history.replaceState(null, "", "#pf=" + enc(S.acct.platform) + "&a=" + enc(S.acct.id) + "&r=" + enc(S.range) + "&m=" + enc(S.measure));}catch(e){}}}
function lastAccount(){try{return localStorage.getItem("xc_account") || "";}catch(e){return "";}}
function cur(){return (S.report && S.report.account && S.report.account.currency) || "USD";}
function query(){
  var q = "account=" + enc(S.acct.id) + "&range=" + enc(S.range) + "&measure=" + enc(S.measure);
  if(S.conv) q += "&actualConv=" + enc(S.conv);
  if(S.value) q += "&actualValue=" + enc(S.value);
  return q;
}

/* ── Loading ──────────────────────────────────────────────── */
function run(refresh){
  if(!S.acct){show("empty"); setMsg(""); return;}
  var seq = ++S.seq, both = S.acct.platform === "both";
  writeHash(); setMsg(both ? "Reading both accounts. The first read takes up to a minute." : "", both ? "pm-note" : ""); show("skel");
  getJson(API + "/report?" + query() + (refresh === true ? "&refresh=1" : "")).then(function(r){
    if(seq !== S.seq) return;
    if(!r.ok){
      show(S.report ? "out" : "empty");
      if(r.j && r.j.kind === "cooldown"){setMsg(esc(r.j.error), "pm-note"); return;}
      var hint = r.j && r.j.kind === "quota" ? " The platform limits how fast this app can read, so this clears on its own." : "";
      setMsg(esc((r.j && r.j.error) || ("The report could not be made (HTTP " + r.status + ")."))+hint);
      return;
    }
    S.report = r.j; setMsg(""); show("out");
    try{render();}catch(e){setMsg("The report could not be drawn (" + esc(e && e.message || e) + ").");}
  }).catch(function(){
    if(seq !== S.seq) return;
    show(S.report ? "out" : "empty"); setMsg("The server could not be reached. Try again.");
  });
}

/* ── Controls ─────────────────────────────────────────────── */
function chips(el, list, on, attr){
  el.innerHTML = list.map(function(x){
    return '<button type="button" role="radio" class="pm-chip' + (x[0] === on ? " on" : "") + '" aria-checked="' + (x[0] === on) + '" ' + attr + '="' + esc(x[0]) + '">' + esc(x[1]) + '</button>';
  }).join("");
}
function paintControls(){
  chips($("#ranges"), X.PRESETS, S.range, "data-r");
  chips($("#measures"), X.MEASURES, S.measure, "data-m");
  $("#measureHint").textContent = X.MEASURE_HINT[S.measure] || "";
}

/* ── The top ──────────────────────────────────────────────── */
function paintTop(rep){
  $("#lead").innerHTML = '<p>' + esc(X.leadLine(rep)) + '</p>';
  $("#tiles").innerHTML = X.tiles(rep).map(function(t){
    return '<div class="pd-kpi au-tile ' + esc(t.tone) + '"><span>' + esc(t.label) + '</span><b>' + esc(t.value) + '</b><i>' + esc(t.hint) + '</i></div>';
  }).join("");
  var w = $("#warn"), list = rep.warnings || [];
  w.hidden = !list.length;
  w.innerHTML = list.map(function(x){return '<span>' + esc(x) + '</span>';}).join("");
}

/* ── Findings ─────────────────────────────────────────────── */
function paintFindings(rep){
  var f = rep.findings || [];
  $("#findCard").hidden = !f.length;
  $("#findNote").textContent = f.length ? "Most serious first" : "";
  $("#finds").innerHTML = f.map(function(x, i){
    var l = X.levelInfo(x.level);
    return '<li class="xc-f xc-t-' + esc(l.tone) + '" style="--i:' + i + '"><div class="xc-f-h"><span class="xc-badge">' + esc(l.label) + '</span><b>' + esc(x.title) + '</b></div>' +
      '<p>' + esc(x.text) + '</p>' +
      (x.evidence && x.evidence.length ? '<div class="xc-ev">' + x.evidence.map(function(e){return '<span>' + esc(e) + '</span>';}).join("") + '</div>' : "") +
      (x.action ? '<div class="xc-do"><em>What to do</em><p>' + esc(x.action) + '</p></div>' : "") + '</li>';
  }).join("");
}

/* ── Side by side ─────────────────────────────────────────── */
function fmt(kind, v){
  if(v == null) return "n/a";
  return kind === "money" ? X.money(v, cur()) : kind === "num" ? X.num(v) : kind === "x" ? X.ratio(v) + "x" : kind === "pct1" ? (Math.round(v * 10) / 10) + "%" : String(v);
}
function column(title, sub, d, delta, cls){
  return '<div class="xc-col ' + cls + '"><h3>' + esc(title) + '</h3><p class="xc-sub">' + esc(sub) + '</p><dl>' + METRICS.map(function(m){
    var tone = X.changeTone(m[0], delta ? delta[m[0]] : null), ch = delta ? X.change(delta[m[0]]) : "";
    var text = m[0] === "value" && !d.value && d.cost > 0 ? "none tracked" : fmt(m[2], d[m[0]]);
    return '<div><dt>' + esc(m[1]) + '</dt><dd>' + esc(text) + (ch ? ' <i class="xc-ch xc-t-' + tone + '">' + esc(ch) + '</i>' : "") + '</dd></div>';
  }).join("") + '</dl></div>';
}
function paintChannels(rep){
  var html = rep.platforms.map(function(p){
    var c = rep.channels[p];
    return column(c.name, c.account.name + (c.measure === "all" ? ", all conversions" : ""), c.cur, c.delta, "xc-" + p);
  }).join("");
  if(rep.platforms.length > 1) html += column("Blended", "the platforms' claims added", rep.blended.cur, rep.blended.delta, "xc-both");
  $("#channels").innerHTML = html;
  $("#chNote").textContent = "Each figure with its change on the period before (" + rep.previous.start + " to " + rep.previous.end + ")";
  $("#shares").innerHTML = X.shareBars(rep).map(function(b){
    return '<div class="xc-share"><span>' + esc(b.label) + '</span><div class="xc-bar" role="img" aria-label="' + esc(b.label + ": " + b.segments.map(function(s){return s.name + " " + X.pct(s.pct);}).join(", ")) + '">' +
      b.segments.map(function(s){return '<i class="xc-l-' + esc(s.platform) + '" style="width:' + Math.max(0, Math.min(100, s.pct)) + '%">' + (s.pct >= 12 ? esc(X.pct(s.pct)) : "") + '</i>';}).join("") + '</div></div>';
  }).join("");
}

/* ── What moved the blended cost ──────────────────────────── */
function paintWaterfall(rep){
  var w = X.waterfall(rep, cur()), card = $("#wfCard");
  card.hidden = !w && rep.platforms.length < 2;
  if(card.hidden) return;
  if(!w){
    $("#wfNote").textContent = ""; $("#wf").innerHTML = '<p class="xc-p">' + esc((rep.decomposition && rep.decomposition.why) || "Nothing to split.") + '</p>'; return;
  }
  $("#wfNote").textContent = w.fromText + " to " + w.toText + " (" + w.changeText + ")";
  $("#wf").innerHTML = '<ul class="xc-wf">' + w.rows.map(function(r){
    return '<li><span>' + esc(r.label) + '</span><div class="xc-wf-bar"><i class="xc-t-' + esc(r.tone) + '" style="width:' + r.width + '%"></i></div><b class="xc-t-' + esc(r.tone) + '">' + esc(r.text) + '</b></li>';
  }).join("") + '</ul><p class="xc-p">Together they add up to the whole change. The mix is what moving conversions between platforms did at last period\'s costs; each platform\'s own cost is what it did at this period\'s mix.</p>';
}

/* ── Campaign types ───────────────────────────────────────── */
function paintTypes(rep){
  var rows = X.typeRows(rep), multi = rep.platforms.length > 1;
  $("#types").innerHTML = '<thead><tr>' + (multi ? '<th>Platform</th>' : "") + '<th>Campaign type</th><th>Usually</th><th class="n">Spend</th><th class="n">Share of spend</th><th class="n">Conversions</th><th class="n">Cost per conversion</th><th class="n">Return</th></tr></thead><tbody>' +
    rows.map(function(r){
      var t = r.t, ro = X.roleInfo(t.role);
      return '<tr>' + (multi ? '<td><span class="xc-dot xc-l-' + esc(r.platform) + '"></span>' + esc(r.platformName) + '</td>' : "") + '<td>' + esc(t.label) + '</td><td><span class="xc-role xc-t-' + esc(ro.tone) + '">' + esc(ro.label) + '</span></td>' +
        '<td class="n">' + esc(X.money(t.cost, cur())) + '</td><td class="n">' + esc(t.shareOfAll == null ? "n/a" : X.pct(t.shareOfAll)) + '</td><td class="n">' + esc(X.num(t.conv)) + '</td>' +
        '<td class="n">' + esc(t.cpa == null ? (t.cost > 0 ? "none" : "n/a") : X.money(t.cpa, cur())) + '</td><td class="n">' + esc(t.roas == null ? "n/a" : X.ratio(t.roas) + "x") + '</td></tr>';
    }).join("") + '</tbody>';
}

/* ── Weeks and links ──────────────────────────────────────── */
function chart(rep, key){
  var c = X.weeklyChart(rep, key, 520, 190);
  if(!c) return '<p class="xc-p">Not enough weeks to draw.</p>';
  return '<svg viewBox="0 0 ' + c.w + ' ' + c.h + '" role="img" aria-label="Weekly ' + (key === "cost" ? "spend" : "conversions") + ' by platform" class="xc-svg">' +
    c.yTicks.map(function(t){return '<line x1="' + c.pad.l + '" x2="' + (c.w - c.pad.r) + '" y1="' + t.y + '" y2="' + t.y + '" class="xc-grid"/><text x="' + (c.pad.l - 6) + '" y="' + (t.y + 4) + '" class="xc-ax" text-anchor="end">' + esc(t.label) + '</text>';}).join("") +
    c.xTicks.map(function(t){return '<text x="' + t.x + '" y="' + (c.h - 6) + '" class="xc-ax" text-anchor="middle">' + esc(t.label) + '</text>';}).join("") +
    c.series.map(function(s){return '<path d="' + s.path + '" class="xc-line xc-l-' + esc(s.platform) + '"/>';}).join("") + '</svg>';
}
function paintWeekly(rep){
  $("#wkNote").textContent = (rep.weeks || []).length + " whole weeks, Monday to Sunday, over this range and the one before it";
  $("#chartCost").innerHTML = chart(rep, "cost");
  $("#chartConv").innerHTML = chart(rep, "conv");
  $("#legend").innerHTML = rep.platforms.map(function(p){return '<span><i class="xc-dot xc-l-' + esc(p) + '"></i>' + esc(X.name(p)) + '</span>';}).join("");
  var lk = rep.links || {}, why = $("#linkWhy"), list = $("#links");
  if(!lk.ok){
    why.textContent = lk.why || "No test was run."; list.innerHTML = ""; return;
  }
  why.textContent = "Four tests on week over week changes, each held to a strict bar because four were run. A link is a pattern in the numbers, never proof that one platform causes the other.";
  list.innerHTML = lk.tests.map(function(t){var x = X.linkText(t); return '<li class="xc-t-' + esc(x.tone) + '">' + esc(x.text) + '</li>';}).join("");
}

/* ── How each platform counts ─────────────────────────────── */
function paintAttribution(rep){
  var a = rep.attribution || {}, t = X.attributionText(a), have = t.google.length || t.meta.length || (a.notes || []).length;
  $("#attrCard").hidden = !have;
  if(!have) return;
  function col(title, rows, cls, empty){
    return '<div class="xc-col ' + cls + '"><h3>' + esc(title) + '</h3>' + (rows.length ? '<ul class="xc-attr">' + rows.map(function(r){return '<li><b>' + esc(r.name) + '</b><span>' + esc(r.detail) + '</span></li>';}).join("") + '</ul>' : '<p class="xc-p">' + esc(empty) + '</p>') + '</div>';
  }
  var html = "";
  if(rep.platforms.indexOf("google") >= 0) html += col("Google Ads counts", t.google, "xc-google", "Its conversion actions could not be read.");
  if(rep.platforms.indexOf("meta") >= 0) html += col("Meta Ads counts", t.meta, "xc-meta", "Its ad sets' attribution settings could not be read.");
  $("#attr").innerHTML = html;
  $("#attrNote").hidden = !(a.notes || []).length;
}

/* ── Against your own numbers ─────────────────────────────── */
function paintRecon(rep){
  var rows = X.claimRows(rep.reconciliation, cur());
  $("#recCard").hidden = !rows.length;
  if(!rows.length) return;
  var over = rep.reconciliation.conversions && rep.reconciliation.conversions.over;
  $("#recNote").textContent = over ? "The platforms claim more than happened" : "What the platforms claim against what happened";
  $("#rec").innerHTML = rows.map(function(r){return '<div class="xc-t-' + esc(r.tone || "neutral") + '"><dt>' + esc(r.label) + '</dt><dd>' + esc(r.value) + '</dd></div>';}).join("");
}

function paintNotes(rep){
  var items = (rep.notes || []).map(esc);
  $("#notesCard").hidden = !items.length;
  $("#notes").innerHTML = items.map(function(x){return "<li>" + x + "</li>";}).join("");
}

function render(){
  var rep = S.report;
  $("#fresh").innerHTML = '<span class="live"></span><b>Read ' + esc(D.ago(rep.readAt || rep.generatedAt)) + '</b>&nbsp;· ' + esc(rep.range.label) + ' (' + esc(rep.range.start) + ' to ' + esc(rep.range.end) + ')';
  var st = $("#stale");
  if(rep.stale){st.hidden = false; st.textContent = "Part of this read is the one saved " + D.ago(rep.stale) + ", because a platform would not answer now.";} else {st.hidden = true; st.textContent = "";}
  var q = query();
  $("#dlXlsx").href = API + "/download?" + q + "&file=xlsx";
  $("#dlPdf").href = API + "/download?" + q + "&file=pdf";
  $("#dlZip").href = API + "/download?" + q + "&file=zip";
  paintTop(rep); paintFindings(rep); paintChannels(rep); paintWaterfall(rep); paintTypes(rep); paintWeekly(rep); paintAttribution(rep); paintRecon(rep); paintNotes(rep);
  $("#foot").textContent = "Read only: from the " + rep.platforms.map(X.name).join(" and ") + " API. Every figure is arithmetic on what the platforms returned.";
}

/* ── Events ───────────────────────────────────────────────── */
document.addEventListener("click", function(e){
  var t = e.target && e.target.closest ? e.target : null; if(!t) return;
  var b = t.closest("[data-r]");
  if(b){S.range = b.getAttribute("data-r"); paintControls(); run(); return;}
  b = t.closest("[data-m]");
  if(b){S.measure = b.getAttribute("data-m"); paintControls(); run();}
});
$("#refreshBtn").addEventListener("click", function(){run(true);});
$("#ownGo").addEventListener("click", function(){S.conv = $("#fConv").value.trim(); S.value = $("#fValue").value.trim(); run();});
$("#ownClear").addEventListener("click", function(){$("#fConv").value = ""; $("#fValue").value = ""; if(S.conv || S.value){S.conv = ""; S.value = ""; run();}});

/* ── Boot ─────────────────────────────────────────────────── */
function boot(){
  var h = readHash(), first = true;
  if(h.r && X.PRESETS.some(function(p){return p[0] === h.r;})) S.range = h.r;
  if(h.m && X.MEASURES.some(function(p){return p[0] === h.m;})) S.measure = h.m;
  paintControls();
  accts = PL.accounts($("#acctPick"), {url: BASE + "/accounts", allow: ["google", "meta", "both"], platform: h.pf || "both", account: h.a || lastAccount(),
    onChange: function(a, platform){
      if(!a){
        S.acct = null; S.report = null; show("empty");
        setMsg((platform === "meta" ? "There are no Meta ad accounts this token can read yet." : "There are no accounts to show for this platform.") + " Pick another platform or account above.", "pm-note");
        return;
      }
      if(S.acct && a.id === S.acct.id && !first) return;
      first = false; S.acct = a; S.report = null;
      try{localStorage.setItem("xc_account", a.id);}catch(e){}
      run();
    },
    onError: function(err){show("empty"); setMsg(esc((err && err.message) || err || "The accounts could not be loaded."));}});
}
boot();
})();
