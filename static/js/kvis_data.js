/* Keyword Visibility Tracker: the wording and the shapes the page draws, as plain functions (so tests/test_kvis_js.py runs them in Node).
 *
 * The server reads the highest-spend keywords and the auctions and holds the last whole week against the four before it (tracker/paid_kvis*.py). This file only decides how a share, a status and a
 * level read, which accounts a view shows, how a keyword's figures are laid out and what the CSV holds. It never works out a finding the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  fix: {label: "Needs a fix", tone: "bad"},
  check: {label: "Worth a look", tone: "under"},
  clean: {label: "Holding", tone: "ok"}
};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var COMP_RULES = {brand_hijack: 1, comp_new: 1, comp_up: 1};

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }
function pct(v){ return v == null ? "n/a" : Math.round(v * 100) + "%"; }
function score(v){ return v == null ? "n/a" : String(Math.round(v * 10) / 10); }

var SYMBOL = {USD: "$", EUR: "€", GBP: "£", CAD: "CA$", AUD: "A$", INR: "₹", NZD: "NZ$", SGD: "S$"};
/* 12.5 as "$12.50", 1234.5 as "$1,235": from 100 up there are no pence; a currency without a symbol is written as its code after the figure (the server's rule) */
function money(v, cur){
  if (v == null) return "n/a";
  var body = Math.abs(v) >= 100 ? int(v) : (Math.round(v * 100) / 100).toFixed(2);
  var sym = SYMBOL[cur];
  return sym ? sym + body : body + " " + (cur || "");
}

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, c = s.counts || {};
  var fix = (s.high || 0) + (s.medium || 0);
  return [
    ["Keywords tracked", int(s.keywords), int(s.judged) + " with enough searches to judge", ""],
    ["To fix", int(fix), fix ? int(s.high) + " high, " + int(s.medium) + " medium" : "no keyword or competitor needs action", fix ? "warn" : "good"],
    ["Not judged", int(s.notJudged), s.notJudged ? "too few searches last week" : "every keyword had enough", ""],
    ["Competitors flagged", int(s.competitors), s.competitors ? "new or overlapping more" : "none new in the auctions", s.competitors ? "warn" : "good"],
    ["Accounts to fix", int(c.fix || 0), (c.check || 0) ? int(c.check) + " worth a look only" : "of " + plural(s.accounts, "account") + " read", c.fix ? "warn" : "good"]
  ];
}

var VIEWS = [["attention", "Needs a look"], ["all", "All"]];
function needsLook(a){ return a.status === "fix" || a.status === "check"; }
function shown(report, key){ var list = report.accounts || []; return key === "all" ? list : list.filter(needsLook); }
function viewCount(report, key){ return shown(report, key).length; }
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }
function query(account, refresh){ return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : ""); }

/* the keywords with something to look at, the judged ones with nothing, and the ones with too little to judge */
function flagged(a){ return (a.items || []).filter(function(i){ return i.findings.length; }); }
function quiet(a){ return (a.items || []).filter(function(i){ return !i.findings.length && i.judged; }); }
function notJudged(a){ return (a.items || []).filter(function(i){ return !i.judged; }); }

function matchText(m){ return m ? String(m).toLowerCase() : ""; }
/* one keyword's figures: [{label, now, before}] */
function figureRows(item){
  var r = item.recent || {}, p = item.prior || {};
  return [
    {label: "At the top of the page", now: pct(r.top), before: pct(p.top)},
    {label: "Lost to rank", now: pct(r.lostRank), before: pct(p.lostRank)},
    {label: "Quality Score", now: score(item.qs), before: score(item.qsBefore)}
  ];
}

/* a competitor finding can be answered with "ignore this domain" */
function ignorable(f){ return COMP_RULES[f.rule] && f.facts && f.facts.domain ? f.facts.domain : ""; }

/* what only shows across the keywords of an account, in sentences: [{kind, text}] */
function insights(report){
  var out = [], group = report.account && report.account.isGroup;
  (report.accounts || []).forEach(function(a){
    var prefix = group ? a.name + ": " : "";
    if (!a.keywords) { out.push({kind: "none", text: prefix + "No Search keyword spent in the last 28 days, so there is nothing to track."}); return; }
    if (a.notJudged) out.push({kind: "data", text: prefix + plural(a.notJudged, "keyword") + " of " + a.keywords + " had too few searches last week to be judged (at least " + (report.minimums || {}).eligible + " eligible searches are needed)."});
    if (!a.auctionRead) out.push({kind: "auction", text: prefix + "No auction insights came back, so competitors were not checked."});
    if (a.read > a.tracked) out.push({kind: "tracked", text: prefix + "The " + a.tracked + " highest-spend keywords of the " + a.read + " read are tracked; the number can be changed in the settings below."});
  });
  return out;
}

/* the CSV: one line per finding, and one per account with none */
var HEAD = ["Account", "Keyword", "Match type", "Campaign", "Level", "Problem", "What is wrong", "Evidence", "What to do"];
function csvRows(report){
  var rows = [["Keyword Visibility Tracker", report.account.name], ["Read", report.readAt || report.generatedAt || ""], [], HEAD];
  report.accounts.forEach(function(a){
    var any = false;
    (a.items || []).forEach(function(i){
      i.findings.forEach(function(f){
        any = true;
        rows.push([a.name, f.keyword, matchText(i.match), f.campaign || i.campaign, levelInfo(f.level).label, ruleLabel(report, f.rule), f.detail, (f.evidence || []).join(" | "), f.fix]);
      });
    });
    (a.competitorFindings || []).forEach(function(f){
      any = true;
      rows.push([a.name, "", "", (f.campaigns || []).join(", "), levelInfo(f.level).label, ruleLabel(report, f.rule), f.detail, (f.evidence || []).join(" | "), f.fix]);
    });
    if (!any) rows.push([a.name, "(nothing found)", "", "", "", "", "", "", ""]);
  });
  return rows;
}
function ruleLabel(report, rule){
  var r = (report.rules || []).filter(function(x){ return x.rule === rule; })[0];
  return r ? r.label : rule;
}

/* what the team saved, as the form shows it */
function num(v){ return v == null || v === "" ? "" : String(v); }
function savedView(saved){
  saved = saved || {};
  return {topIs: num(saved.topIs), qsMin: num(saved.qsMin), topN: num(saved.topN), ignore: (saved.ignore || []).join(", ")};
}

var api = {STATUS: STATUS, LEVEL: LEVEL, VIEWS: VIEWS, statusInfo: statusInfo, levelInfo: levelInfo, int: int, plural: plural, pct: pct, score: score, money: money, tiles: tiles, shown: shown,
  viewCount: viewCount, firstView: firstView, needsLook: needsLook, query: query, flagged: flagged, quiet: quiet, notJudged: notJudged, matchText: matchText, figureRows: figureRows,
  ignorable: ignorable, insights: insights, csvRows: csvRows, ruleLabel: ruleLabel, savedView: savedView, HEAD: HEAD};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.KVData = api;
})(typeof window !== "undefined" ? window : globalThis);
