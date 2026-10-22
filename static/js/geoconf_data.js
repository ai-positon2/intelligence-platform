/* Location Conflicts: the wording and the shapes the page draws, as plain functions (so tests/test_geoconf_js.py runs them in Node).
 *
 * The server reads where each campaign shows and holds every name that says a place against it (tracker/paid_geoconf*.py). This file only decides how a status and a level read, which accounts
 * a view shows, how a campaign's places and location option are said, and what the CSV holds. It never works out a finding the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  fix: {label: "Fix these", tone: "bad"},
  check: {label: "Small settings", tone: "under"},
  clean: {label: "Clean", tone: "ok"}
};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var SCOPE = {campaign: "Campaign", adgroup: "Ad group", ad: "Ad", account: "Account"};
var OPTION = {PRESENCE: "People in or regularly in the places", PRESENCE_OR_INTEREST: "People in, regularly in or interested in the places", TRAVEL_IN: "Also people travelling in the places"};
var CHANNEL = {SEARCH: "Search", PERFORMANCE_MAX: "Performance Max", DISPLAY: "Display", SHOPPING: "Shopping", DEMAND_GEN: "Demand Gen", VIDEO: "Video", META: "Meta ad set"};
var NAMED = {name_place: 1, copy_place: 1, kw_place: 1, group_copy: 1};

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function scopeText(s){ return SCOPE[s] || String(s || ""); }
function optionText(o){ return OPTION[o] || (o ? String(o) : "Not read"); }
function channelText(c){ return CHANNEL[c] || (c ? String(c) : "Campaign"); }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, c = s.counts || {};
  var fix = (s.high || 0) + (s.medium || 0);
  return [
    ["Campaigns read", int(s.campaigns), plural(s.ads, "ad") + " and the keywords checked against them", ""],
    ["To fix", int(fix), fix ? int(s.high) + " high, " + int(s.medium) + " medium" : "no campaign shows where it should not", fix ? "warn" : "good"],
    ["Small settings", int(s.low), s.low ? "shared places, schedules and devices to check" : "nothing to check", ""],
    ["Findings", int(s.findings), "in " + plural(s.accounts, "account"), ""],
    ["Accounts to fix", int(c.fix || 0), (c.check || 0) ? int(c.check) + " with small settings only" : "of " + plural(s.accounts, "account") + " read", c.fix ? "warn" : "good"]
  ];
}

/* which accounts a view shows, and how many each holds */
var VIEWS = [["attention", "Needs a look"], ["all", "All"]];
function needsLook(a){ return a.status === "fix" || a.status === "check"; }
function shown(report, key){ var list = report.accounts || []; return key === "all" ? list : list.filter(needsLook); }
function viewCount(report, key){ return shown(report, key).length; }
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }

function query(account, refresh){ return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : ""); }

/* the places a campaign shows in, in a sentence */
function targetsText(item){
  var t = (item.targets || []).filter(Boolean);
  if (!t.length) return "no location set";
  if (t.length <= 3) return t.join(", ");
  return t.slice(0, 3).join(", ") + " and " + (t.length - 3) + " more";
}
function excludedText(item){
  var t = (item.excluded || []).filter(Boolean);
  return t.length ? "not in " + (t.length <= 3 ? t.join(", ") : t.slice(0, 3).join(", ") + " and " + (t.length - 3) + " more") : "";
}

/* the campaigns that have something to look at, and the ones that have not */
function flagged(a){ return (a.items || []).filter(function(i){ return i.findings.length; }); }
function quiet(a){ return (a.items || []).filter(function(i){ return !i.findings.length; }); }

/* the words a finding can be answered with "this is not a place" for: only the findings that name places */
function ignorable(f){
  if (!NAMED[f.rule]) return [];
  var seen = {}, out = [];
  (f.places || []).forEach(function(p){ var k = String(p).toLowerCase(); if (p && !seen[k]) { seen[k] = 1; out.push(p); } });
  return out;
}

/* what only shows across the campaigns of an account, in sentences: [{kind, text}] */
function insights(report){
  var out = [], group = report.account && report.account.isGroup;
  (report.accounts || []).forEach(function(a){
    var prefix = group ? a.name + ": " : "";
    if (!a.namesRead) out.push({kind: "names", text: prefix + "The names of the places could not be read, so no place was compared with a name. Only the settings were checked."});
    if (!a.campaigns) { out.push({kind: "none", text: prefix + "No enabled campaign was found."}); return; }
    var pi = 0;
    (a.items || []).forEach(function(i){ if (i.positive === "PRESENCE_OR_INTEREST" || i.positive === "TRAVEL_IN") pi++; });
    if (pi && pi === a.campaigns) out.push({kind: "option", text: prefix + "Every campaign is set to show to people interested in the places, not only people in them. If that is wanted, say so in the settings below and the finding goes."});
    else if (pi) out.push({kind: "option", text: prefix + plural(pi, "campaign") + " of " + a.campaigns + " are set to show to people interested in the places, not only people in them."});
  });
  return out;
}

/* the CSV: one line per finding, and one per account with none */
var HEAD = ["Account", "Platform", "Campaign", "Ad group", "Level", "Problem", "Applies to", "What is wrong", "Places", "Evidence", "What to do"];
function csvRows(report){
  var rows = [["Location Conflicts", report.account.name], ["Read", report.readAt || report.generatedAt || ""], [], HEAD];
  report.accounts.forEach(function(a){
    var plat = a.platform === "meta" ? "Meta" : "Google Ads", any = false;
    (a.items || []).forEach(function(i){
      i.findings.forEach(function(f){
        any = true;
        rows.push([a.name, plat, f.campaign || i.name, f.adGroup || "", levelInfo(f.level).label, ruleLabel(report, f.rule), scopeText(f.scope), f.detail, (f.places || []).join(", "),
          (f.evidence || []).map(function(e){ return e.text; }).join(" | "), f.fix]);
      });
    });
    if (!any) rows.push([a.name, plat, "(nothing found)", "", "", "", "", "", "", "", ""]);
  });
  return rows;
}
function ruleLabel(report, rule){
  var r = (report.rules || []).filter(function(x){ return x.rule === rule; })[0];
  return r ? r.label : rule;
}

/* what the team saved, as the form shows it */
function savedView(saved){
  saved = saved || {};
  return {areas: (saved.areas || []).join("\n"), ignore: (saved.ignore || []).join(", "), acceptPresence: !!saved.acceptPresence};
}

var api = {STATUS: STATUS, LEVEL: LEVEL, VIEWS: VIEWS, SCOPE: SCOPE, OPTION: OPTION, statusInfo: statusInfo, levelInfo: levelInfo, scopeText: scopeText, optionText: optionText, channelText: channelText,
  int: int, plural: plural, tiles: tiles, shown: shown, viewCount: viewCount, firstView: firstView, needsLook: needsLook, query: query, targetsText: targetsText, excludedText: excludedText,
  flagged: flagged, quiet: quiet, ignorable: ignorable, insights: insights, csvRows: csvRows, ruleLabel: ruleLabel, savedView: savedView, HEAD: HEAD};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.GCData = api;
})(typeof window !== "undefined" ? window : globalThis);
