/* Disapproved Ads Tracker: the wording and the shapes the page draws, as plain functions (so tests/test_disapproved_js.py runs them in Node).
 *
 * The server reads Google Ads, decides which ad groups can show nothing, groups the ads by policy and ranks every finding (tracker/paid_disapproved*.py). This file only decides
 * how a status, a policy name and a sum read, which accounts a view shows and what the CSV holds. It never works a figure out that the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  stopped: {label: "Ad groups stopped", tone: "bad"},
  disapproved: {label: "Ads disapproved", tone: "over"},
  limited: {label: "Ads limited", tone: "under"},
  clear: {label: "All clear", tone: "ok"}
};
var STATE = {
  dead: {label: "No approved ad in the ad group", tone: "bad"},
  covered: {label: "Another ad is showing", tone: "over"},
  unchecked: {label: "Not checked for cover", tone: "unk"},
  limited: {label: "Limited, still shows", tone: "under"}
};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var APPROVAL = {DISAPPROVED: "Disapproved", APPROVED_LIMITED: "Limited", AREA_OF_INTEREST_ONLY: "Area of interest only"};
var REVIEW = {UNDER_APPEAL: "Under appeal", REVIEW_IN_PROGRESS: "Being reviewed"};
var SYMBOL = {USD: "$", EUR: "€", GBP: "£", CAD: "CA$", AUD: "A$", INR: "₹", NZD: "NZ$", SGD: "S$"};

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function stateInfo(s){ return STATE[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function approvalText(a){ return APPROVAL[a] || String(a || ""); }
function reviewText(r){ return REVIEW[r] || ""; }

function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function money(v, cur){
  if (v == null) return "n/a";
  var body = Math.abs(v) >= 100 ? int(v) : (Math.round(v * 100) / 100).toFixed(2);
  var sym = SYMBOL[cur];
  return sym ? sym + body : body + " " + (cur || "");
}
/* Google's policy name in plain words, as the server writes it: DESTINATION_NOT_WORKING is "Destination not working" */
function topicName(t){
  var s = String(t || "").replace(/_/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : "";
}
function topicLabel(t){ return t === "NOT_NAMED" ? "Reason not named" : topicName(t); }

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, c = s.counts || {};
  return [
    ["Accounts read", int(s.accounts), "last " + report.window.weeks + " whole weeks read for the ad groups", ""],
    ["Need a look", int(s.needsLook), s.needsLook ? int(c.stopped || 0) + " with an ad group stopped" : "no ad is disapproved", s.needsLook ? "warn" : "good"],
    ["Ad groups stopped", int(s.stoppedGroups), s.stoppedGroups ? (s.lostClicks ? "about " + int(s.lostClicks) + " clicks a week not coming" : "every ad in them is disapproved") : "none has all its ads disapproved", s.stoppedGroups ? "warn" : "good"],
    ["Ads disapproved", int(s.disapproved), s.waiting ? int(s.waiting) + " under appeal or in review" : "none waiting on Google", ""],
    ["Ads limited", int(s.limited), "they still show, with limits", ""]
  ];
}

/* which accounts a view shows, and how many each holds */
var VIEWS = [["attention", "Needs a look"], ["all", "All"]];
function needsLook(a){ return a.status === "stopped" || a.status === "disapproved"; }
function shown(report, key){ var list = report.accounts || []; return key === "all" ? list : list.filter(needsLook); }
function viewCount(report, key){ return shown(report, key).length; }
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }

function query(account, refresh){ return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : ""); }

/* the plan's step titles in order, for the CSV */
function planTitles(a){
  return ((a.plan && a.plan.steps) || []).map(function(s){ return s.n + ". " + s.title; }).join(" | ");
}

/* the CSV: one line per flagged ad, and one per account with none */
var HEAD = ["Account", "Status", "Campaign", "Ad group", "Ad id", "Ad type", "Approval", "Review", "What it means", "Policy", "Text Google points at", "Headlines", "Final URL", "Clicks a week before", "Clicks last week", "Fix plan"];
function csvRows(report){
  var rows = [["Disapproved Ads Tracker", report.account.name], ["Weeks", report.window.from + " to " + report.window.to], [], HEAD];
  report.accounts.forEach(function(a){
    var base = [a.name, statusInfo(a.status).label];
    if (!a.items.length) { rows.push(base.concat(["(no flagged ad)", "", "", "", "", "", "", "", "", "", "", "", ""], [planTitles(a)])); return; }
    a.items.forEach(function(i){
      var w = i.weight || {};
      rows.push(base.concat([i.campaign, i.adGroup, i.id, String(i.type || "").replace(/_/g, " ").toLowerCase(), approvalText(i.approval), reviewText(i.review), stateInfo(i.state).label,
        i.topics.map(function(t){ return topicLabel(t.t); }).join("; "), [].concat.apply([], i.topics.map(function(t){ return t.ev || []; })).join("; "), (i.headlines || []).join(" | "), i.url || "",
        w.usualClicks == null ? "" : w.usualClicks, w.lastClicks == null ? "" : w.lastClicks, planTitles(a)]));
    });
  });
  return rows;
}

var api = {STATUS: STATUS, STATE: STATE, LEVEL: LEVEL, VIEWS: VIEWS, statusInfo: statusInfo, stateInfo: stateInfo, levelInfo: levelInfo, approvalText: approvalText, reviewText: reviewText, int: int,
  money: money, topicName: topicName, topicLabel: topicLabel, tiles: tiles, shown: shown, viewCount: viewCount, firstView: firstView, needsLook: needsLook, query: query, planTitles: planTitles,
  csvRows: csvRows, HEAD: HEAD};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.DAData = api;
})(typeof window !== "undefined" ? window : globalThis);
