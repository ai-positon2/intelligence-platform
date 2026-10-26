/* Tracking & Data Health: the wording and the shapes the page draws, as plain functions (so tests/test_tracking_health_js.py runs them in Node).
 *
 * The server reads each account and holds the last days against the 3 weeks before (tracker/paid_track*.py). This file only decides how a figure, a status and a level read, which accounts a view
 * shows, and what the CSV holds. It never works out a finding the server did not send.
 */
(function(root){
"use strict";

var STATUS = {fix: {label: "Needs a fix", tone: "bad"}, check: {label: "Worth a look", tone: "under"}, clean: {label: "Tracking is fine", tone: "ok"}};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var CHECK = {ok: {label: "Fine", tone: "ok"}, problem: {label: "Problem", tone: "bad"}, unknown: {label: "Not judged", tone: "unk"}};
var SYMBOL = {USD: "$", EUR: "€", GBP: "£", CAD: "CA$", AUD: "A$", INR: "₹", NZD: "NZ$", SGD: "S$"};
var VIEWS = [["attention", "Needs a look"], ["all", "All"]];

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function checkInfo(s){ return CHECK[s] || {label: String(s), tone: "unk"}; }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }

/* 12.5 as "$12.50", 1234.5 as "$1,235": from 100 up there are no pence; a currency without a symbol is written as its code after the figure (the server's rule) */
function money(v, cur){
  if (v == null) return "n/a";
  var body = Math.abs(v) >= 100 ? int(v) : (Math.round(v * 100) / 100).toFixed(2);
  var sym = SYMBOL[cur];
  return sym ? sym + body : body + " " + (cur || "");
}

function unjudged(report){
  var n = 0;
  (report.accounts || []).forEach(function(a){ (a.checks || []).forEach(function(c){ if (c.state === "unknown") n++; }); });
  return n;
}

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, c = s.counts || {}, fix = (s.high || 0) + (s.medium || 0), nj = unjudged(report);
  return [
    ["Accounts read", int(s.accounts), (c.clean || 0) + " fine, " + (c.fix || 0) + " to fix", ""],
    ["Conversions stopped", int(s.dark), s.dark ? "spending, and nothing recorded" : "no account is spending with nothing recorded", s.dark ? "warn" : "good"],
    ["To fix", int(fix), fix ? int(s.high) + " high, " + int(s.medium) + " medium" : "no tracking problem was found", fix ? "warn" : "good"],
    ["Accounts to fix", int(c.fix || 0), (c.check || 0) ? int(c.check) + " worth a look only" : "of " + plural(s.accounts, "account") + " read", c.fix ? "warn" : "good"],
    ["Checks not judged", int(nj), "too little data, or the platform did not show it", ""]
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
      (notes[c.key] = notes[c.key] || {label: c.label, n: 0, note: c.note, names: []});
      notes[c.key].n++;
      notes[c.key].names.push(a.name);
    });
    if (!(a.headline || {}).spend7 && a.platform) out.push({kind: "idle", text: (group ? a.name + ": " : "") + "Nothing was spent in the last 7 days, so there was nothing to judge."});
  });
  Object.keys(notes).forEach(function(k){
    var x = notes[k];
    out.push({kind: "unjudged", text: group ? x.label + ": " + plural(x.n, "account") + " could not be judged (open an account to see why)." : x.label + ": " + x.note});
  });
  return out;
}

/* the CSV: one line per finding, and one per account with none */
var HEAD = ["Account", "Platform", "Level", "Area", "Problem", "What is wrong", "Evidence", "What to do"];
function csvRows(report){
  var rows = [["Tracking & Data Health", report.account.name], ["Read", report.readAt || report.generatedAt || ""], [], HEAD];
  report.accounts.forEach(function(a){
    var plat = a.platform === "meta" ? "Meta" : "Google Ads";
    if (!(a.findings || []).length) { rows.push([a.name, plat, "", "", "(nothing found)", "", "", ""]); return; }
    a.findings.forEach(function(f){
      rows.push([a.name, plat, levelInfo(f.level).label, f.area, f.title, f.detail, (f.evidence || []).join(" | "), f.fix]);
    });
  });
  return rows;
}

/* an account's one-line summary under its name */
function accountSub(a){
  var checks = a.checks || [], bad = checks.filter(function(c){ return c.state === "problem"; }).length, unk = checks.filter(function(c){ return c.state === "unknown"; }).length;
  var parts = [a.platform === "meta" ? "Meta Ads" : "Google Ads"];
  if (bad) parts.push(plural(bad, "check") + " failing");
  else parts.push("every check that could be run passed");
  if (unk) parts.push(unk + " not judged");
  return parts.join(", ");
}

var api = {STATUS: STATUS, LEVEL: LEVEL, CHECK: CHECK, VIEWS: VIEWS, HEAD: HEAD, statusInfo: statusInfo, levelInfo: levelInfo, checkInfo: checkInfo, int: int, plural: plural, money: money, tiles: tiles,
  shown: shown, viewCount: viewCount, firstView: firstView, needsLook: needsLook, query: query, insights: insights, csvRows: csvRows, accountSub: accountSub, unjudged: unjudged};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.THData = api;
})(typeof window !== "undefined" ? window : globalThis);
