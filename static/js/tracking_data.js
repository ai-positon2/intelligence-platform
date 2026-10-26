/* Tracking Data Health: the wording and the shapes the page draws, as plain functions (so tests/test_tracking_js.py runs them in Node).
 *
 * The server reads each account's last five weeks and judges whether its conversion numbers can be trusted (tracker/paid_tracking*.py). This file only decides how a status, a level
 * and a figure read, which accounts a view shows, how the 35 days are drawn and how an action, a list or a pixel is described. It never works out a finding the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  broken: {label: "Tracking broken", tone: "bad"},
  fix: {label: "Needs a fix", tone: "under"},
  check: {label: "Worth a look", tone: "wait"},
  healthy: {label: "Healthy", tone: "ok"}
};
var LEVEL = {critical: {label: "Critical", tone: "bad"}, high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var ACTION_STATE = {stopped: {label: "Stopped", tone: "bad"}, ok: {label: "Recording", tone: "ok"}, quiet: {label: "Too few to judge", tone: "unk"}, secondary: {label: "Secondary, not judged", tone: "unk"},
  muted: {label: "Left out", tone: "unk"}};
var LIST_STATE = {below: {label: "Under 1,000", tone: "bad"}, shrinking: {label: "Shrinking", tone: "warn"}, ok: {label: "Steady", tone: "ok"}, "new": {label: "No history yet", tone: "unk"},
  muted: {label: "Left out", tone: "unk"}};
var PIXEL_STATE = {silent: {label: "Silent", tone: "bad"}, firing: {label: "Firing", tone: "ok"}, idle: {label: "Quiet, no spend", tone: "unk"}, unused: {label: "Not in use", tone: "unk"},
  muted: {label: "Left out", tone: "unk"}};

function info(map, k){ return map[k] || {label: String(k), tone: "unk"}; }
function statusInfo(s){ return info(STATUS, s); }
function levelInfo(l){ return info(LEVEL, l); }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function dec(v){ return v == null ? "n/a" : (Math.abs(v - Math.round(v)) < 1e-9 ? int(v) : (Math.round(v * 10) / 10).toLocaleString("en-US")); }
function plural(n, one, many){ return dec(n) + " " + (n === 1 ? one : (many || one + "s")); }
function scoreTone(s){ return s >= 85 ? "ok" : s >= 65 ? "warn" : "bad"; }     /* one critical finding (60) is red */
function day(iso){
  var p = String(iso || "").split("-"), m = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][(+p[1] || 1) - 1];
  return p.length === 3 ? m + " " + (+p[2]) : String(iso || "");
}

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary || {}, c = s.counts || {}, n = s.accounts || 0;
  var worst = (s.critical || 0) + (s.high || 0);
  return [
    ["Accounts read", int(n), (c.healthy || 0) + " healthy", ""],
    ["Tracking broken", int(c.broken || 0), c.broken ? int(s.critical) + " critical finding" + (s.critical === 1 ? "" : "s") : "no conversions or pixel stopped", c.broken ? "warn" : "good"],
    ["To fix", int(worst + (s.medium || 0)), worst ? int(s.high) + " high, " + int(s.medium) + " medium" : "nothing high or medium", worst ? "warn" : "good"],
    ["Health score", n ? int(s.scoreTotal / n) : "n/a", n > 1 ? "the average of " + plural(n, "account") : "out of 100", n && s.scoreTotal / n < 65 ? "warn" : ""]
  ];
}

/* which accounts a view shows */
var VIEWS = [["attention", "Needs a look"], ["all", "All"]];
function needsLook(a){ return a.status !== "healthy"; }
function shown(report, key){ var list = report.accounts || []; return key === "all" ? list : list.filter(needsLook); }
function viewCount(report, key){ return shown(report, key).length; }
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }

/* the 35 days as bars (clicks) and dots (conversions) in a w by h box: {bars: [{x, y, w, h, date, clicks, conv, expected}], dots: [{cx, cy}], maxClicks, maxConv} */
function chart(days, w, h){
  days = days || [];
  var n = days.length || 1, step = w / n, pad = 4;
  var maxC = Math.max.apply(null, [1].concat(days.map(function(d){ return d.clicks || 0; })));
  var maxV = Math.max.apply(null, [1].concat(days.map(function(d){ return d.conv || 0; })));
  var bars = days.map(function(d, i){
    var bh = Math.round((d.clicks || 0) / maxC * (h - pad) * 10) / 10;
    return {x: Math.round(i * step * 10) / 10, y: Math.round((h - bh) * 10) / 10, w: Math.max(1, Math.round((step - 1) * 10) / 10), h: bh, date: d.date, clicks: d.clicks, conv: d.conv, expected: !!d.expected};
  });
  var dots = days.map(function(d, i){
    return {cx: Math.round((i * step + step / 2) * 10) / 10, cy: Math.round((h - pad - (d.conv || 0) / maxV * (h - 2 * pad)) * 10) / 10, zero: !(d.conv > 0)};
  });
  return {bars: bars, dots: dots, maxClicks: maxC, maxConv: maxV};
}

function actionRows(a){
  return (a.actions || []).map(function(x){
    var s = info(ACTION_STATE, x.state);
    return {id: x.id, name: x.name, state: x.state, label: s.label, tone: s.tone, base: dec(x.base), recent: dec(x.recent) + " in " + plural(x.days || 3, "day"), primary: x.primary,
      judged: x.state === "stopped" || x.state === "ok", why: x.mutedWhy || "", key: "action:" + x.id};
  });
}
function listRows(a){
  return (a.lists || []).map(function(x){
    var s = info(LIST_STATE, x.state);
    return {id: x.id, name: x.name, state: x.state, label: s.label, tone: s.tone, size: int(x.size), before: x.before == null ? "n/a" : int(x.before),
      change: x.change == null ? "" : (x.change > 0 ? "+" : "") + Math.round(x.change * 100) + "%", why: x.mutedWhy || "", key: "list:" + x.id};
  });
}
function pixelRows(a){
  return (a.pixels || []).map(function(x){
    var s = info(PIXEL_STATE, x.state);
    return {id: x.id, name: x.name, state: x.state, label: s.label, tone: s.tone, last: x.hours == null ? "never in 35 days" : x.hours < 1 ? "under an hour ago" : plural(Math.floor(x.hours), "hour") + " ago",
      why: x.mutedWhy || "", key: "pixel:" + x.id};
  });
}

/* the sentences under the tiles: what could not be judged or read, and the expected drops */
function insights(report){
  var out = [], group = report.account && report.account.isGroup, waiting = 0;
  (report.accounts || []).forEach(function(a){
    var prefix = group ? a.name + ": " : "";
    (a.notes || []).forEach(function(t){ out.push({kind: "data", text: prefix + t}); });
    if (a.expectedToday) out.push({kind: "muted", text: prefix + "Yesterday is inside a window when the team expected a drop (" + a.expectedToday + "), so what was found then is shown and not alerted."});
    if (a.lists && a.lists.length && a.lists.every(function(l){ return l.state === "new" || l.state === "muted"; })) waiting++;
  });
  if (waiting) out.push({kind: "data", text: "List trends start once the daily check has kept the lists' sizes for a few days" + (group ? " (" + plural(waiting, "account") + " waiting)." : ".")});   /* one line, not one per account */
  return out;
}

/* the 'why' of a finding and the button it offers */
function findingView(f){
  var l = levelInfo(f.level), was = f.level !== f.baseLevel ? levelInfo(f.baseLevel).label : "";
  var tag = f.acked ? "Acknowledged by " + (f.acked.by || "the team") + (f.acked.note ? ": " + f.acked.note : "") : f.expected ? "Expected drop: " + f.expected : "";
  return {label: l.label, tone: l.tone, was: was, tag: tag, canAck: !f.acked && f.baseLevel !== "low", canUnack: !!f.acked};
}

function query(account, refresh){ return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : ""); }

var api = {STATUS: STATUS, LEVEL: LEVEL, VIEWS: VIEWS, statusInfo: statusInfo, levelInfo: levelInfo, int: int, dec: dec, plural: plural, scoreTone: scoreTone, day: day, tiles: tiles,
  needsLook: needsLook, shown: shown, viewCount: viewCount, firstView: firstView, chart: chart, actionRows: actionRows, listRows: listRows, pixelRows: pixelRows, insights: insights,
  findingView: findingView, query: query};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.THData = api;
})(typeof window !== "undefined" ? window : globalThis);
