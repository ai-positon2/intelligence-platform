/* KPI Monitoring: the wording and the shapes the page draws, as plain functions (so tests/test_kpi_js.py runs them in Node).
 *
 * The server reads each campaign's last two weeks and holds the last 7 days against its target (tracker/paid_kpi*.py). This file only decides how a figure, a status and a level read, which
 * accounts a view shows, how a campaign's four figures are laid out against their targets and what the CSV holds. It never works out a finding the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  fix: {label: "Needs a fix", tone: "bad"},
  check: {label: "Worth a look", tone: "under"},
  clean: {label: "On track", tone: "ok"}
};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var STATE = {on: {label: "On target", tone: "ok"}, off: {label: "Off target", tone: "bad"}, unknown: {label: "Not enough data", tone: "unk"}, none: {label: "No target", tone: "unk"}};
var METRIC = {cpa: "Cost per conversion", roas: "Return on ad spend", cvr: "Conversion rate", ctr: "Click-through rate"};
var ORDER = ["cpa", "roas", "cvr", "ctr"];
var SOURCE = {campaign: "saved for this campaign", account: "saved for the account", strategy: "the campaign's bid strategy"};
var CHANNEL = {SEARCH: "Search", PERFORMANCE_MAX: "Performance Max", DISPLAY: "Display", SHOPPING: "Shopping", DEMAND_GEN: "Demand Gen", VIDEO: "Video", META_LEADS: "Meta leads", META_SALES: "Meta sales",
  META_APP: "Meta app", META_OTHER: "Meta"};
var SYMBOL = {USD: "$", EUR: "€", GBP: "£", CAD: "CA$", AUD: "A$", INR: "₹", NZD: "NZ$", SGD: "S$"};

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function stateInfo(s){ return STATE[s] || {label: String(s), tone: "unk"}; }
function channelText(c){ return CHANNEL[c] || (c ? String(c) : "Campaign"); }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }

/* 12.5 as "$12.50", 1234.5 as "$1,235": from 100 up there are no pence; a currency without a symbol is written as its code after the figure (the server's rule) */
function money(v, cur){
  if (v == null) return "n/a";
  var body = Math.abs(v) >= 100 ? int(v) : (Math.round(v * 100) / 100).toFixed(2);
  var sym = SYMBOL[cur];
  return sym ? sym + body : body + " " + (cur || "");
}
/* a figure of one metric: money, a multiple, or a percent with one decimal */
function fmt(metric, v, cur){
  if (v == null) return "n/a";
  if (metric === "cpa") return money(v, cur);
  if (metric === "roas") return (Math.round(v * 100) / 100).toFixed(2) + "x";
  return (Math.round(v * 1000) / 10).toFixed(1) + "%";
}

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, c = s.counts || {};
  var fix = (s.high || 0) + (s.medium || 0), judged = (s.onTarget || 0) + (s.offTarget || 0);
  return [
    ["Campaigns read", int(s.campaigns), (s.onTarget || 0) + " on target, " + (s.offTarget || 0) + " off", ""],
    ["To fix", int(fix), fix ? int(s.high) + " high, " + int(s.medium) + " medium" : "nothing is off its target or held back", fix ? "warn" : "good"],
    ["Off target", int(s.offTarget), "of " + plural(judged, "campaign") + " that could be judged", s.offTarget ? "warn" : "good"],
    ["Not judged", int((s.notJudged || 0) + (s.withoutTarget || 0)), (s.withoutTarget || 0) + " with no target, " + (s.notJudged || 0) + " with too little data", ""],
    ["Accounts to fix", int(c.fix || 0), (c.check || 0) ? int(c.check) + " worth a look only" : "of " + plural(s.accounts, "account") + " read", c.fix ? "warn" : "good"]
  ];
}

/* which accounts a view shows, and how many each holds */
var VIEWS = [["attention", "Needs a look"], ["all", "All"]];
function needsLook(a){ return a.status === "fix" || a.status === "check"; }
function shown(report, key){ var list = report.accounts || []; return key === "all" ? list : list.filter(needsLook); }
function viewCount(report, key){ return shown(report, key).length; }
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }

function query(account, refresh){ return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : ""); }

/* the campaigns with something to look at, the ones with nothing, and the ones the team left out */
function flagged(a){ return (a.items || []).filter(function(i){ return !i.muted && i.findings.length; }); }
function quiet(a){ return (a.items || []).filter(function(i){ return !i.muted && !i.findings.length; }); }
function leftOut(a){ return (a.items || []).filter(function(i){ return i.muted; }); }

/* one campaign's four figures against their targets: [{metric, label, now, before, target, source, state, why}] (a metric with no figure and no target is left out) */
function metricRows(item, cur){
  return (item.metrics || []).filter(function(m){ return m.value != null || m.target != null || m.prior != null; }).sort(function(a, b){ return ORDER.indexOf(a.metric) - ORDER.indexOf(b.metric); }).map(function(m){
    return {metric: m.metric, label: METRIC[m.metric] || m.label, now: fmt(m.metric, m.value, cur), before: fmt(m.metric, m.prior, cur), target: m.target == null ? "none saved" : fmt(m.metric, m.target, cur),
      source: m.source ? (SOURCE[m.source] || m.source) : "", state: m.state, why: m.why || ""};
  });
}
/* the account as one, the same way: only the figures that have one or a target */
function headlineRows(a){ return metricRows({metrics: (a.headline && a.headline.metrics) || []}, a.currency); }

/* what only shows across the campaigns of an account, in sentences: [{kind, text}] */
function insights(report){
  var out = [], group = report.account && report.account.isGroup;
  (report.accounts || []).forEach(function(a){
    var prefix = group ? a.name + ": " : "";
    if (!a.campaigns) { out.push({kind: "none", text: prefix + "No enabled campaign was found."}); return; }
    if (!a.targetsSet) out.push({kind: "targets", text: prefix + "No target is saved here and no campaign states one in its bid strategy, so cost, return and rates were not judged. Save the targets below; delivery, learning and the budget were checked."});
    else if (a.withoutTarget) out.push({kind: "targets", text: prefix + plural(a.withoutTarget, "campaign") + " of " + a.campaigns + " have no target, so only their delivery, learning and budget were checked."});
    if (a.notJudged) out.push({kind: "data", text: prefix + plural(a.notJudged, "campaign") + " with a target had too little in the last 7 days to be judged (at least " + (report.minimums || {}).conversions + " conversions are needed for a cost or return)."});
    if (a.muted) out.push({kind: "muted", text: prefix + plural(a.muted, "campaign") + " " + (a.muted === 1 ? "is" : "are") + " left out of the checks by the team."});
  });
  return out;
}

/* the CSV: one line per finding, and one per account with none */
var HEAD = ["Account", "Platform", "Campaign", "Level", "Problem", "What is wrong", "Evidence", "What to do"];
function csvRows(report){
  var rows = [["KPI Monitoring", report.account.name], ["Read", report.readAt || report.generatedAt || ""], [], HEAD];
  report.accounts.forEach(function(a){
    var plat = a.platform === "meta" ? "Meta" : "Google Ads", any = false;
    (a.items || []).forEach(function(i){
      i.findings.forEach(function(f){
        any = true;
        rows.push([a.name, plat, f.campaign || i.name, levelInfo(f.level).label, ruleLabel(report, f.rule), f.detail, (f.evidence || []).join(" | "), f.fix]);
      });
    });
    if (!any) rows.push([a.name, plat, "(nothing found)", "", "", "", "", ""]);
  });
  return rows;
}
function ruleLabel(report, rule){
  var r = (report.rules || []).filter(function(x){ return x.rule === rule; })[0];
  return r ? r.label : rule;
}

/* what the team saved, as the forms show it: an account's four targets and tolerance, a campaign's four targets, and why a campaign is left out */
function num(v){ return v == null || v === "" ? "" : String(v); }
function savedView(saved){
  saved = saved || {};
  var t = saved.targets || {};
  return {cpa: num(t.cpa), roas: num(t.roas), cvr: num(t.cvr), ctr: num(t.ctr), tolerance: num(saved.tolerance)};
}
function campaignView(saved, id){
  var c = ((saved || {}).campaigns || {})[id] || {};
  return {cpa: num(c.cpa), roas: num(c.roas), cvr: num(c.cvr), ctr: num(c.ctr)};
}
function mutedWhy(saved, id){ return (((saved || {}).muted) || {})[id] || ""; }

var api = {STATUS: STATUS, LEVEL: LEVEL, STATE: STATE, VIEWS: VIEWS, METRIC: METRIC, ORDER: ORDER, statusInfo: statusInfo, levelInfo: levelInfo, stateInfo: stateInfo, channelText: channelText,
  int: int, plural: plural, money: money, fmt: fmt, tiles: tiles, shown: shown, viewCount: viewCount, firstView: firstView, needsLook: needsLook, query: query, flagged: flagged, quiet: quiet,
  leftOut: leftOut, metricRows: metricRows, headlineRows: headlineRows, insights: insights, csvRows: csvRows, ruleLabel: ruleLabel, savedView: savedView, campaignView: campaignView, mutedWhy: mutedWhy,
  HEAD: HEAD};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.KPData = api;
})(typeof window !== "undefined" ? window : globalThis);
