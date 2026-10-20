/* Brand IS Monitor: the wording and the shapes the page draws, as plain functions (so tests/test_brand_is_js.py runs them in Node).
 *
 * The server reads Google Ads, does every sum and ranks every finding (tracker/paid_brandis*.py). This file only decides how a share, a change and a status
 * read, how the weekly bars are laid out and what the CSV holds. It never works a figure out that the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  below: {label: "Below threshold", tone: "over"},
  ok: {label: "On target", tone: "ok"},
  thin: {label: "Too few searches", tone: "unk"},
  unmeasured: {label: "No share reported", tone: "unk"},
  none: {label: "No brand campaign", tone: "unk"}
};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var CAUSE = {budget: "Lost to budget", rank: "Lost to rank", both: "Budget and rank"};

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function causeText(c){ return CAUSE[c] || ""; }

function pct(v){ return v == null ? "n/a" : Math.round(v * 100) + "%"; }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function points(d){ var n = Math.round(Math.abs(d) * 100); return n + " point" + (n === 1 ? "" : "s"); }

/* the change on the week before, in words, and which way is good (up) */
function delta(d){
  if (d == null) return {text: "No week before to compare", tone: "none"};
  var n = Math.round(d * 100);
  if (n === 0) return {text: "Level with the week before", tone: "flat"};
  return {text: (n > 0 ? "Up " : "Down ") + points(d) + " on the week before", tone: n > 0 ? "good" : "bad"};
}

/* the account's last-week share as a bar with the threshold marked: widths are percentages of the bar */
function meter(account){
  var s = account.brand && account.brand.share;
  return {share: s == null ? null : Math.max(0, Math.min(100, s * 100)), threshold: Math.round(account.threshold * 100),
    tone: account.status === "below" ? "over" : account.status === "ok" ? "ok" : "unk"};
}

/* the eight weekly shares as bars: height is the share (0 to 100), a week with no share is flat and marked */
function trendBars(account){
  var th = account.threshold;
  return (account.trend || []).map(function(w){
    var has = w.share != null && w.eligible >= 100;
    return {from: w.from, to: w.to, share: w.share, height: has ? Math.max(2, Math.round(w.share * 100)) : 0, has: has,
      tone: !has ? "none" : w.share < th ? "low" : "ok",
      title: w.from + " to " + w.to + ": " + (has ? pct(w.share) + " of " + int(w.eligible) + " eligible searches" : w.measured || w.unmeasured ? "too few searches to judge" : "no brand impressions")};
  });
}

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, o = s.overall || {}, c = s.counts || {};
  var judged = (c.ok || 0) + (c.below || 0), notJudged = (c.thin || 0) + (c.unmeasured || 0) + (c.none || 0);
  return [
    ["Accounts read", int(s.accounts), "week of " + report.window.latestFrom + " to " + report.window.latestTo, ""],
    ["Under " + pct(report.threshold), int(s.below), s.below ? (s.high ? s.high + " by 10 points or more" : "none by 10 points or more") : judged ? "every judged account is on target" : "none could be judged", s.below ? "warn" : judged ? "good" : ""],
    ["Brand share, all together", pct(o.share), o.accounts ? "across " + o.accounts + " account" + (o.accounts === 1 ? "" : "s") + ", " + int(o.eligible) + " eligible searches" : "no account could be judged", ""],
    ["Fell on the week before", int(s.dropped), "5 points or more", s.dropped ? "warn" : ""],
    ["Not judged", int(notJudged), notJudged ? "too few searches, no share or no brand campaign" : "every account had enough to judge", ""]
  ];
}

/* which accounts a view shows, and how many each holds */
var VIEWS = [["attention", "Needs a look"], ["below", "Below threshold"], ["all", "All"]];
function needsLook(a){ return a.status === "below" || a.status === "unmeasured" || (a.findings || []).some(function(f){ return f.kind === "dropped"; }); }
function viewCount(report, key){
  var list = report.accounts || [];
  return key === "all" ? list.length : key === "below" ? list.filter(function(a){ return a.status === "below"; }).length : list.filter(needsLook).length;
}
function shown(report, key){
  var list = report.accounts || [];
  return key === "all" ? list : key === "below" ? list.filter(function(a){ return a.status === "below"; }) : list.filter(needsLook);
}
/* the view to open on: the first one with anything in it */
function firstView(report){
  return viewCount(report, "attention") ? "attention" : "all";
}

/* the address of a read: account, threshold (a whole percentage) and the campaigns marked brand or not brand */
function query(account, thresholdPercent, over, refresh){
  var brand = [], not = [];
  Object.keys(over || {}).forEach(function(id){ (over[id] ? brand : not).push(id); });
  var q = "account=" + encodeURIComponent(account) + "&threshold=" + encodeURIComponent(String(thresholdPercent));
  if (brand.length) q += "&brand=" + brand.join(",");
  if (not.length) q += "&notbrand=" + not.join(",");
  return q + (refresh ? "&refresh=1" : "");
}

/* what pressing "count as brand" or "do not count" on a campaign does to the marks: the new marks, with the mark dropped when it only restates the name's rule */
function mark(over, campaign, makeBrand){
  var next = {};
  Object.keys(over || {}).forEach(function(k){ next[k] = over[k]; });
  if (makeBrand === campaign.byName) delete next[campaign.id]; else next[campaign.id] = makeBrand;
  return next;
}

/* the CSV: one line per brand campaign, and one per account that has none */
var HEAD = ["Account", "Status", "Week from", "Week to", "Campaign", "Brand share", "Eligible searches", "Lost to budget", "Lost to rank", "Top of page", "Absolute top", "Why", "Bidding strategy", "Daily budget", "First finding", "Next step"];
function csvRows(report){
  var rows = [["Brand IS Monitor", report.account.name], ["Week", report.window.latestFrom + " to " + report.window.latestTo], ["Threshold", pct(report.threshold)], [], HEAD];
  report.accounts.forEach(function(a){
    var f = (a.findings || [])[0] || {}, base = [a.name, statusInfo(a.status).label, a.weekFrom, a.weekTo];
    var tail = [f.title || "", f.nextStep || ""];
    if (!a.brandCampaigns.length) { rows.push(base.concat(["(no brand campaign)", "", "", "", "", "", "", causeText(a.cause), "", ""], tail)); return; }
    a.brandCampaigns.forEach(function(c){
      rows.push(base.concat([c.name, c.share == null ? "" : Math.round(c.share * 1000) / 10 + "%", c.eligible, pctOrBlank(c.lostBudget), pctOrBlank(c.lostRank), pctOrBlank(c.top), pctOrBlank(c.absTop),
        causeText(c.cause), c.strategy || "", c.budget == null ? "" : c.budget], tail));
    });
  });
  return rows;
}
function pctOrBlank(v){ return v == null ? "" : Math.round(v * 1000) / 10 + "%"; }

var api = {STATUS: STATUS, LEVEL: LEVEL, VIEWS: VIEWS, statusInfo: statusInfo, levelInfo: levelInfo, causeText: causeText, pct: pct, int: int, points: points, delta: delta,
  meter: meter, trendBars: trendBars, tiles: tiles, viewCount: viewCount, shown: shown, firstView: firstView, needsLook: needsLook, query: query, mark: mark, csvRows: csvRows, HEAD: HEAD};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.BIData = api;
})(typeof window !== "undefined" ? window : globalThis);
