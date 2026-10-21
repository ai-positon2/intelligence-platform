/* Brand Spend Alert: the wording and the shapes the page draws, as plain functions (so tests/test_brand_spend_js.py runs them in Node).
 *
 * The server reads Google Ads, does every sum, judges every week against the usual one and ranks every finding (tracker/paid_brandspend*.py). This file only decides how a
 * sum of money, a change and a status read, how the weekly bars and the band around the usual week are laid out and what the CSV holds. It never works a figure out that
 * the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  stopped: {label: "Spend stopped", tone: "bad"},
  over: {label: "Over the usual", tone: "over"},
  under: {label: "Under the usual", tone: "under"},
  ok: {label: "As usual", tone: "ok"},
  new: {label: "Too new to judge", tone: "unk"},
  idle: {label: "No spend", tone: "unk"},
  none: {label: "No brand campaign", tone: "unk"}
};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var CAUSE = {
  over: {clicks: "More clicks", cpc: "Dearer clicks", both: "Clicks and their price"},
  under: {clicks: "Fewer clicks", cpc: "Cheaper clicks", both: "Clicks and their price"}
};
var SYMBOL = {USD: "$", EUR: "€", GBP: "£", CAD: "CA$", AUD: "A$", INR: "₹", NZD: "NZ$", SGD: "S$"};

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function causeText(status, c){ return (CAUSE[status] || {})[c] || ""; }

function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function pct(v){ return v == null ? "n/a" : Math.round(v * 100) + "%"; }

/* 12.5 as "$12.50", 1234.5 as "$1,235": from 100 up there are no pence; a currency without a symbol is written as its code after the figure (the server's rule) */
function money(v, cur){
  if (v == null) return "n/a";
  var body = Math.abs(v) >= 100 ? int(v) : (Math.round(v * 100) / 100).toFixed(2);
  var sym = SYMBOL[cur];
  return sym ? sym + body : body + " " + (cur || "");
}

/* the move against the usual week, in words, and which way is bad */
function change(a){
  if (a.ratio == null) return {text: "No usual week to compare", tone: "none"};
  var n = Math.round((a.ratio - 1) * 100);
  if (n === 0) return {text: "Level with a usual week", tone: "flat"};
  return {text: (n > 0 ? "Up " : "Down ") + Math.abs(n) + "% on a usual week", tone: a.status === "ok" ? "flat" : "bad"};
}

/* what this week so far says, in a few words (empty when it is not judged or is on its usual pace) */
function partialText(a){
  var p = a.partial;
  if (!p || p.status === "ok") return "";
  return "This week so far is " + (p.status === "over" ? "ahead of" : "behind") + " the usual pace";
}

/* the last weeks as bars on one scale, with the usual band drawn behind them: heights and the band are percentages of the tallest of the bars and the top of the band */
function trendBars(a){
  var ex = a.expected, tr = a.trend || [];
  var top = Math.max.apply(null, tr.map(function(w){ return w.cost; }).concat(ex ? [ex.high] : [], [1])) * 1.12;      /* a little headroom so the label of the tallest bar is inside the chart */
  var bars = tr.map(function(w){
    var tone = !w.cost ? "none" : ex && w.cost > ex.high ? "low" : ex && w.cost < ex.low ? "under" : "ok";
    return {from: w.from, to: w.to, cost: w.cost, height: w.cost > 0 ? Math.max(2, Math.round(w.cost / top * 100)) : 0, tone: tone,
      title: w.from + " to " + w.to + ": " + (w.cost > 0 ? int(w.clicks) + " clicks, " + int(w.cost) + " spent" : "no spend")};
  });
  return {bars: bars, band: ex ? {low: Math.round(ex.low / top * 100), high: Math.round(ex.high / top * 100), usual: Math.round(ex.cost / top * 100)} : null};
}

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, o = s.overall || {}, c = s.counts || {}, cur = report.account.currency;
  var notJudged = (c.new || 0) + (c.idle || 0) + (c.none || 0);
  var diff = o.cost != null && o.expected ? Math.round((o.cost / o.expected - 1) * 100) : null;
  return [
    ["Accounts read", int(s.accounts), "week of " + report.window.latestFrom + " to " + report.window.latestTo, ""],
    ["Outside the usual", int(s.needsLook), s.needsLook ? (s.high ? s.high + " by twice the limit or more" : "none by twice the limit or more") : "every account is inside its usual band", s.needsLook ? "warn" : "good"],
    ["Brand spend last week", o.cost == null ? "n/a" : money(o.cost, cur), o.cost == null ? "no account had a usual week to compare" : "usual " + money(o.expected, cur) + (diff == null ? "" : ", " + (diff >= 0 ? "up " : "down ") + Math.abs(diff) + "%") + " across " + o.accounts + " account" + (o.accounts === 1 ? "" : "s"), ""],
    ["This week off pace", int(s.thisWeek), "accounts that were fine last week", s.thisWeek ? "warn" : ""],
    ["Not judged", int(notJudged), notJudged ? "too new, no spend or no brand campaign" : "every account had enough history", ""]
  ];
}

/* which accounts a view shows, and how many each holds */
var VIEWS = [["attention", "Needs a look"], ["outside", "Outside the usual"], ["all", "All"]];
function outside(a){ return a.status === "stopped" || a.status === "over" || a.status === "under"; }
function needsLook(a){ return outside(a) || (a.findings || []).some(function(f){ return f.level === "high" || f.level === "medium"; }); }
function shown(report, key){
  var list = report.accounts || [];
  return key === "all" ? list : key === "outside" ? list.filter(outside) : list.filter(needsLook);
}
function viewCount(report, key){ return shown(report, key).length; }
/* the view to open on: the first one with anything in it */
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }

/* the address of a read: the account (what the team saved, the limit and the marks, is applied by the server) and Refresh */
function query(account, refresh){
  return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : "");
}

/* what pressing "count as brand" or "do not count" on a campaign saves for the team: the value to send, null when it only restates what the name says (that takes the mark away) */
function markBody(campaign, makeBrand){
  return {campaign: campaign.id, value: makeBrand === campaign.byName ? null : makeBrand};
}

/* the figures of the agreed limits form: what the page sends to save them, from the typed text */
function limitsBody(account, text){
  return {account: account, weeklyMin: text.weeklyMin, weeklyMax: text.weeklyMax, monthlyBudget: text.monthlyBudget, brandTerms: text.brandTerms};
}

/* the month as a bar: spent so far, where it is heading and the budget, as shares of the longest of them */
function monthBar(a){
  var m = a.month;
  if (!m || !m.budget) return null;
  var top = Math.max(m.budget, m.projected, m.spent) || 1;
  return {spent: Math.round(m.spent / top * 100), projected: Math.round(m.projected / top * 100), budget: Math.round(m.budget / top * 100), tone: m.status === "over" ? "bad" : m.status === "pace-over" ? "warn" : m.status === "pace-under" ? "low" : "ok"};
}

/* the daily history as cells, oldest first: the day, how it stood and a title */
var CELL_TONE = {ok: "ok", over: "over", under: "under", stopped: "bad", "new": "unk", idle: "unk", none: "unk"};
function historyCells(a){
  var h = a.history;
  if (!h) return [];
  return (h.statuses || []).map(function(d){ return {day: d.day, tone: CELL_TONE[d.status] || "unk", title: d.day + ": " + (STATUS[d.status] ? STATUS[d.status].label : String(d.status)) + (d.cost != null ? ", " + int(d.cost) + " spent" : "")}; });
}

/* the plan's step titles in order, for the CSV: "1. Check the brand campaigns are running | 2. ..." */
function planTitles(a){
  return ((a.plan && a.plan.steps) || []).map(function(s){ return s.n + ". " + s.title; }).join(" | ");
}

/* the CSV: one line per brand campaign, and one per account that has none */
var HEAD = ["Account", "Status", "Week from", "Week to", "Campaign", "Spend last week", "Usual week", "Change", "Clicks", "Cost per click", "Campaign status", "Bidding strategy", "Daily budget", "Cause", "First finding", "Next step", "Other findings", "Fix plan"];
function csvRows(report){
  var cur = report.account.currency;
  var rows = [["Brand Spend Alert", report.account.name], ["Week", report.window.latestFrom + " to " + report.window.latestTo], ["Limit", "plus or minus " + pct(report.limit) + " of a usual week"], [], HEAD];
  report.accounts.forEach(function(a){
    var f = (a.findings || [])[0] || {}, base = [a.name, statusInfo(a.status).label, a.weekFrom, a.weekTo];
    var tail = [causeText(a.status, a.cause), f.title || "", f.nextStep || "", (a.findings || []).slice(1).map(function(x){ return x.title; }).join(" | "), planTitles(a)];
    if (!a.campaigns.length) { rows.push(base.concat(["(no brand campaign)", "", "", "", "", "", "", "", ""], tail)); return; }
    a.campaigns.forEach(function(c){
      rows.push(base.concat([c.name, c.cost, c.usual == null ? "" : c.usual, c.diff == null ? "" : c.diff, c.clicks, c.cpc == null ? "" : Math.round(c.cpc * 100) / 100, c.status || "", c.strategy || "", c.budget == null ? "" : c.budget], tail));
    });
  });
  return rows;
}

var api = {STATUS: STATUS, LEVEL: LEVEL, VIEWS: VIEWS, statusInfo: statusInfo, levelInfo: levelInfo, causeText: causeText, int: int, pct: pct, money: money, change: change,
  partialText: partialText, trendBars: trendBars, tiles: tiles, shown: shown, viewCount: viewCount, firstView: firstView, needsLook: needsLook, outside: outside, query: query,
  markBody: markBody, limitsBody: limitsBody, monthBar: monthBar, historyCells: historyCells, planTitles: planTitles, csvRows: csvRows, HEAD: HEAD};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.BSData = api;
})(typeof window !== "undefined" ? window : globalThis);
