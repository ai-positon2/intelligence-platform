/* Spend Anomaly Alert: the wording and the shapes the page draws, as plain functions (so tests/test_anomaly_js.py runs them in Node).
 *
 * The server reads the spend, works out the usual and the findings and writes the plan (tracker/paid_anomaly*.py). This file only decides how a status and a level read, which accounts a
 * view shows, where the lines and bars of a chart go, and what the CSV holds. It never works out a finding the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  over: {label: "Above usual", tone: "over"},
  under: {label: "Below usual", tone: "under"},
  ok: {label: "Normal", tone: "ok"},
  new: {label: "Too new to judge", tone: "unk"},
  none: {label: "No spend", tone: "unk"}
};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Expected", tone: "unk"}};
var KIND = {spike: "Spike", surge: "Surge", ceiling: "Ceiling", budget: "Budget", drop: "Drop"};
var SENS = {low: "Low", normal: "Normal", high: "High"};

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function kindText(k){ return KIND[k] || String(k || ""); }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }

/* money the way the server writes it: a half rounded up from 100 on, cents under 100 */
var SYMBOL = {USD: "$", EUR: "€", GBP: "£", INR: "₹", CAD: "CA$", AUD: "A$", NZD: "NZ$", SGD: "S$", JPY: "¥", CHF: "CHF ", MXN: "MX$", BRL: "R$"};
function money(v, cur){
  if (v == null || isNaN(v)) return "n/a";
  var sym = SYMBOL[cur] != null ? SYMBOL[cur] : (cur ? cur + " " : ""), neg = v < 0, a = Math.abs(v);
  var body = a >= 100 ? Math.floor(a + 0.5).toLocaleString("en-US") : a.toFixed(2);
  return (neg ? "-" : "") + sym + body;
}
function ratioText(r){ return r == null ? "n/a" : (r < 10 ? r.toFixed(1) : String(Math.round(r))) + "x"; }
function hh(h){ return (h < 10 ? "0" : "") + h + ":00"; }

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, c = s.counts || {}, o = s.overall;
  var look = (c.over || 0) + (c.under || 0);
  return [
    ["Accounts read", int(s.accounts), int(c.ok || 0) + " normal", ""],
    ["Need a look", int(look), look ? (c.over || 0) + " above usual, " + (c.under || 0) + " below" : "all spending as usual", look ? "warn" : "good"],
    ["High", int(s.high), s.high ? "worth a look now" : "none at the highest level", s.high ? "warn" : "good"],
    o ? ["Spent so far today", money(o.cost, o.currency), "a usual " + money(o.usual, o.currency) + " by now in " + plural(o.accounts, "account"), o.cost > o.usual * 1.25 ? "warn" : ""]
      : ["Spent so far today", "n/a", "the accounts are in different currencies", ""],
    ["Too new to judge", int(c.new || 0), c.new ? "fewer than 4 days of spend" : "every account has a usual", ""]
  ];
}

/* which accounts a view shows, and how many each holds */
var VIEWS = [["attention", "Needs a look"], ["all", "All"]];
function needsLook(a){ return a.status === "over" || a.status === "under"; }
function shown(report, key){ var list = report.accounts || []; return key === "all" ? list : list.filter(needsLook); }
function viewCount(report, key){ return shown(report, key).length; }
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }

function query(account, refresh){ return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : ""); }

/* the headline of an account's row: its worst finding, or why there is none */
function headline(a){
  var f = (a.findings || []).filter(function(x){ return x.level !== "info"; })[0];
  if (f) return f.title;
  if (a.status === "new") return "Fewer than 4 days of spend are known";
  if (a.status === "none") return "No spend in the last 15 days";
  return "";
}

/* how today so far reads against the usual by now: {text, tone} */
function paceText(a){
  var t = a.today;
  if (!t || t.cost == null) return {text: "", tone: ""};
  var cur = a.currency;
  if (t.usualByNow != null) {
    var r = t.ratio;
    return {text: money(t.cost, cur) + " by " + hh(t.through) + ", usual " + money(t.usualByNow, cur) + (r == null ? "" : " (" + ratioText(r) + ")"), tone: r != null && r >= 1.75 ? "over" : (r != null && r <= 0.2 ? "under" : "")};
  }
  return {text: money(t.cost, cur) + " so far today" + (a.usualDay ? ", a usual day is " + money(a.usualDay, cur) : ""), tone: ""};
}

/* what only shows across the accounts, in sentences: [{kind, text}] */
function insights(report){
  var out = [], list = report.accounts || [], group = report.account && report.account.isGroup;
  var flat = list.filter(function(a){ return a.hourly === false && a.status !== "none"; });
  if (flat.length) out.push({kind: "hours", text: (group ? flat.length + " of " + list.length + " accounts" : "This account") + " gave no spend by hour, so " + (group ? "they are" : "it is") +
    " judged by whole days only (" + flat.slice(0, 3).map(function(a){ return a.name; }).join(", ") + (flat.length > 3 ? " and more" : "") + "). A spike inside a day shows only once the day's total is over the usual."});
  list.forEach(function(a){
    var sv = a.saved || {}, today = a.today && a.today.date;
    if (sv.mutedUntil && today && sv.mutedUntil >= today) out.push({kind: "mute", text: (group ? a.name + ": " : "") + "A planned push is muted until " + sv.mutedUntil + (sv.note ? " (" + sv.note + ")" : "") + ". Spikes up to that day are shown as expected and are not alerted."});
    if (a.status === "new" && !sv.usualDaily) out.push({kind: "new", text: (group ? a.name + ": " : "") + "Fewer than 4 days of spend are known, so there is no usual to compare with. Save its usual daily spend below to judge it now."});
  });
  return out;
}

/* ── Charts: where things go (the page turns these into SVG) ──────────────── */

/* today's spend through the day against the usual and the highest seen: the lines of a chart W by H with a padding. x is the hour 0..24, y the money. */
function curveGeometry(curve, W, H, pad){
  if (!curve) return null;
  pad = pad || {l: 44, r: 14, t: 12, b: 26};
  var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  var top = Math.max.apply(null, curve.upper.concat(curve.usual, curve.today, [1])) * 1.08;
  function x(h){ return round1(pad.l + iw * h / 24); }
  function y(v){ return round1(pad.t + ih * (1 - v / top)); }
  function path(vals){ return vals.map(function(v, i){ return (i ? "L" : "M") + x(i) + " " + y(v); }).join(""); }
  var ticks = [0, 6, 12, 18, 24].map(function(h){ return {h: h, x: x(h), label: h === 24 ? "24" : hh(h).slice(0, 2)}; });
  var yt = [0, 0.5, 1].map(function(f){ var v = top / 1.08 * f; return {v: v, y: y(v)}; });
  var last = curve.today.length - 1;
  return {usual: path(curve.usual), upper: path(curve.upper), today: path(curve.today), dot: {x: x(last), y: y(curve.today[last])}, ticks: ticks, yticks: yt, W: W, H: H, pad: pad,
    base: y(0), nowX: x(curve.hour)};
}
function round1(v){ return Math.round(v * 10) / 10; }

/* the last days as bars with the usual day as a line: [{date, x, y, w, h, partial, label}] and the y of the usual */
function barsGeometry(days, usualDay, W, H, pad){
  pad = pad || {l: 44, r: 14, t: 12, b: 26};
  var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b, n = days.length || 1;
  var top = Math.max.apply(null, days.map(function(d){ return d.cost; }).concat([usualDay || 0, 1])) * 1.1;
  var slot = iw / n, w = Math.max(3, slot * 0.68);
  var bars = days.map(function(d, i){
    var h = ih * d.cost / top;
    return {date: d.date, x: round1(pad.l + slot * i + (slot - w) / 2), y: round1(pad.t + ih - h), w: round1(w), h: round1(h), partial: !!d.partial, cost: d.cost, label: d.date.slice(8) + "/" + d.date.slice(5, 7)};
  });
  return {bars: bars, usualY: usualDay ? round1(pad.t + ih * (1 - usualDay / top)) : null, W: W, H: H, pad: pad, base: round1(pad.t + ih), top: top};
}

/* ── The CSV: one line per finding, and one per account with none ──────────── */
var HEAD = ["Account", "Platform", "Status", "Level", "Kind", "Day", "Problem", "Detail", "Spent", "Usual", "Ratio", "Today so far", "Usual by now", "Usual day", "Next step"];
function csvRows(report){
  var rows = [["Spend Anomaly Alert", report.account.name], ["Read", report.generatedAt || ""], [], HEAD];
  report.accounts.forEach(function(a){
    var plat = a.platform === "meta" ? "Meta" : "Google Ads", st = statusInfo(a.status).label;
    var fs = a.findings || [];
    if (!fs.length) { rows.push([a.name, plat, st, "", "", "", headline(a) || "Nothing unusual", "", "", "", "", a.today.cost, a.today.usualByNow == null ? "" : a.today.usualByNow, a.usualDay, ""]); return; }
    fs.forEach(function(f){
      rows.push([a.name, plat, st, levelInfo(f.level).label, kindText(f.kind), f.key, f.title, f.detail, f.spent == null ? "" : f.spent, f.usual == null ? "" : f.usual, f.ratio == null ? "" : f.ratio,
        a.today.cost, a.today.usualByNow == null ? "" : a.today.usualByNow, a.usualDay, f.nextStep]);
    });
  });
  return rows;
}

/* what the team saved, as the form shows it: {usualDaily, maxDaily, minSwing, mutedUntil, note} to strings, and a muted push that has ended is said so */
function savedView(saved, todayIso){
  saved = saved || {};
  var ended = !!saved.mutedUntil && !!todayIso && saved.mutedUntil < todayIso;
  return {usualDaily: saved.usualDaily == null ? "" : String(saved.usualDaily), maxDaily: saved.maxDaily == null ? "" : String(saved.maxDaily), minSwing: saved.minSwing == null ? "" : String(saved.minSwing),
    mutedUntil: saved.mutedUntil || "", note: saved.note || "", ended: ended};
}

var api = {STATUS: STATUS, LEVEL: LEVEL, KIND: KIND, SENS: SENS, VIEWS: VIEWS, HEAD: HEAD, statusInfo: statusInfo, levelInfo: levelInfo, kindText: kindText, int: int, plural: plural, money: money,
  ratioText: ratioText, hh: hh, tiles: tiles, shown: shown, viewCount: viewCount, firstView: firstView, needsLook: needsLook, query: query, headline: headline, paceText: paceText,
  insights: insights, curveGeometry: curveGeometry, barsGeometry: barsGeometry, csvRows: csvRows, savedView: savedView};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.AnData = api;
})(typeof window !== "undefined" ? window : globalThis);
