/* Creative & Asset Health: the wording and the shapes the page draws, as plain functions (so tests/test_creative_health_js.py runs them in Node).
 *
 * The server reads each account and judges what changed (tracker/paid_creative*.py). This file only decides how a figure, a status and a level read, which accounts a view
 * shows, and what the CSV holds. It never works out a finding the server did not send.
 */
(function(root){
"use strict";

var STATUS = {fix: {label: "Needs a fix", tone: "bad"}, check: {label: "Worth a look", tone: "under"}, clean: {label: "Nothing unusual", tone: "ok"}};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var CHECK = {ok: {label: "Fine", tone: "ok"}, problem: {label: "Problem", tone: "bad"}, unknown: {label: "Not judged", tone: "unk"}};
var VIEWS = [["attention", "Needs a look"], ["all", "All"]];

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function checkInfo(s){ return CHECK[s] || {label: String(s), tone: "unk"}; }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }

function unjudged(report){
  var n = 0;
  (report.accounts || []).forEach(function(a){ (a.checks || []).forEach(function(c){ if (c.state === "unknown") n++; }); });
  return n;
}

/* the figures the page opens with: [label, value, sub, tone] */
function notJudged(report){
  var n = 0;
  (report.accounts || []).forEach(function(a){ n += (a.headline && a.headline.notJudged) || 0; });
  return n;
}
function tiles(report){
  var s = report.summary, c = s.counts || {}, fix = (s.high || 0) + (s.medium || 0), nj = notJudged(report);
  return [
    ["Creatives read", int(s.creatives), int(s.judged) + " judged on their last weeks", ""],
    ["Wearing out", int(s.tired), s.tired ? "losing clicks, shown too often or getting dearer" : "none found among those judged", s.tired ? "warn" : "good"],
    ["To fix", int(fix), fix ? int(s.high) + " high, " + int(s.medium) + " medium" : "nothing needs fixing", fix ? "warn" : "good"],
    ["Accounts to fix", int(c.fix || 0), (c.check || 0) ? int(c.check) + " worth a look only" : "of " + plural(s.accounts, "account") + " read", c.fix ? "warn" : "good"],
    ["Not judged", int(nj), "too little delivery in the last weeks to tell", ""]
  ];
}

function needsLook(a){ return a.status === "fix" || a.status === "check"; }
function shown(report, key){ var list = report.accounts || []; return key === "all" ? list : list.filter(needsLook); }
function viewCount(report, key){ return shown(report, key).length; }
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }
function query(account, refresh){ return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : ""); }

/* what only shows across the accounts, in sentences: [{kind, text}] */
function insights(report){
  var out = [], group = report.account && report.account.isGroup, notes = {};
  (report.accounts || []).forEach(function(a){
    (a.checks || []).forEach(function(c){
      if (c.state !== "unknown") return;
      (notes[c.key] = notes[c.key] || {label: c.label, n: 0, note: c.note});
      notes[c.key].n++;
    });
  });
  Object.keys(notes).forEach(function(k){
    var x = notes[k];
    out.push({kind: "unjudged", text: group ? x.label + ": " + plural(x.n, "account") + " could not be judged yet (open an account to see why)." : x.label + ": " + x.note});
  });
  var nj = notJudged(report);
  if (nj) out.push({kind: "unjudged", text: plural(nj, "creative") + " had too little delivery in the last weeks to judge, so they are not listed as fine."});
  return out;
}

/* the CSV: one line per finding, and one per account with none */
var HEAD = ["Account", "Platform", "Level", "Area", "Campaign", "Group", "Problem", "What is wrong", "Evidence", "What to do"];
function csvRows(report){
  var rows = [["Creative & Asset Health", report.account.name], ["Read", report.readAt || report.generatedAt || ""], [], HEAD];
  report.accounts.forEach(function(a){
    var plat = a.platform === "meta" ? "Meta" : "Google Ads";
    if (!(a.findings || []).length) { rows.push([a.name, plat, "", "", "", "", "(nothing found)", "", "", ""]); return; }
    a.findings.forEach(function(f){
      var w = f.where || {};
      rows.push([a.name, plat, levelInfo(f.level).label, f.area, w.campaign || "", w.group || "", f.title, f.detail, (f.evidence || []).join(" | "), f.fix]);
    });
  });
  return rows;
}

/* an account's one-line summary under its name */
function accountSub(a){
  var h = a.headline || {}, parts = [a.platform === "meta" ? "Meta Ads" : "Google Ads"];
  parts.push(plural(h.creatives || 0, "creative") + " read, " + int(h.judged || 0) + " judged");
  if (h.tired) parts.push(int(h.tired) + " wearing out");
  var unk = (a.checks || []).filter(function(c){ return c.state === "unknown"; }).length;
  if (unk) parts.push(unk + " not judged");
  return parts.join(", ");
}

var api = {STATUS: STATUS, LEVEL: LEVEL, CHECK: CHECK, VIEWS: VIEWS, HEAD: HEAD, statusInfo: statusInfo, levelInfo: levelInfo, checkInfo: checkInfo, int: int, plural: plural, tiles: tiles,
  shown: shown, viewCount: viewCount, firstView: firstView, needsLook: needsLook, query: query, insights: insights, csvRows: csvRows, accountSub: accountSub, unjudged: unjudged, notJudged: notJudged};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.CHData = api;
})(typeof window !== "undefined" ? window : globalThis);
