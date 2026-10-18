/* Cross-Channel & Attribution Analysis: the figures and wording the page draws, as plain functions (so tests/test_cross_channel_js.py runs them in Node).
 *
 * The server reads both platforms, does every sum and ranks every finding (tracker/paid_xchannel*.py). This file only decides how a number reads, which way a change is good,
 * how each part of the report is worded for the page and the shape of its charts. It never works a figure out that the server did not send. The shared money, number and date
 * helpers are the Test & Experiment Tracker's (exp_data.js).
 */
(function(root){
"use strict";

var X = typeof module !== "undefined" && module.exports ? require("./exp_data.js") : root.ExpData;

var PRESETS = [["30", "Last 30 days"], ["60", "Last 60 days"], ["90", "Last 90 days"], ["last_month", "Last month"], ["mtd", "This month so far"]];
var MEASURES = [["primary", "Conversions"], ["all", "All conversions"]];
var MEASURE_HINT = {primary: "What each platform bids on: Google's Conversions column, Meta's results for each campaign's objective.", all: "Every conversion each platform counts, including secondary actions."};
var NAME = {google: "Google Ads", meta: "Meta Ads"};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "For information", tone: "neutral"}};
var ROLE = {capture: {label: "Captures demand", tone: "good"}, create: {label: "Creates demand", tone: "wait"}, mixed: {label: "Mixed", tone: "neutral"}};
/* which way a change is good: 1 higher is better, -1 lower is better, 0 neither */
var WAY = {cost: 0, impr: 0, clicks: 1, conv: 1, value: 1, ctr: 1, cvr: 1, roas: 1, cpa: -1, cpc: -1};
var QUIET = 2;                              /* a change under this many percent reads as no change */

function levelInfo(l){return LEVEL[l] || {label: String(l || "Unknown"), tone: "unk"};}
function roleInfo(r){return ROLE[r] || {label: "Mixed", tone: "neutral"};}
function name(p){return NAME[p] || p;}
function poss(p){var n = name(p); return n + (/s$/.test(n) ? "'" : "'s");}
function num(v){return X.num(v);}
function money(v, cur){return X.money(v, cur);}
function ratio(v){return v == null || isNaN(v) ? "n/a" : (Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100).toString();}
function pct(v){return v == null || isNaN(v) ? "n/a" : Math.round(v) + "%";}

/* "+12%", "-3%", "no change" and the tone it is drawn in for the metric `key` */
function change(v){
  if(v == null || isNaN(v)) return "";
  if(Math.abs(v) < QUIET) return "no change";
  return (v > 0 ? "+" : "-") + Math.round(Math.abs(v)) + "%";
}
function changeTone(key, v){
  if(v == null || isNaN(v) || Math.abs(v) < QUIET) return "neutral";
  var way = WAY[key] || 0;
  return way === 0 ? "neutral" : (v > 0) === (way > 0) ? "good" : "bad";
}

function tiles(rep){
  var b = rep.blended, c = b.cur, cur = rep.account.currency;
  var cpaTone = changeTone("cpa", b.delta.cpa);
  return [
    {label: "Spend", value: money(c.cost, cur), hint: b.delta.cost == null ? rep.range.label : change(b.delta.cost) + " on the period before", tone: "neutral"},
    {label: "Conversions claimed", value: num(c.conv), hint: rep.platforms.length > 1 ? "the platforms' own claims, added" : name(rep.platforms[0]) + "'s own claim", tone: "neutral"},
    {label: "Cost per conversion", value: money(c.cpa, cur), hint: b.delta.cpa == null ? "blended" : "blended, " + change(b.delta.cpa) + " on the period before", tone: cpaTone},
    {label: "Return on ad spend", value: c.roas == null ? "n/a" : ratio(c.roas) + "x", hint: c.roas == null ? "no conversion value tracked" : b.delta.roas == null ? "blended" : "blended, " + change(b.delta.roas) + " on the period before", tone: changeTone("roas", b.delta.roas)},
    {label: "Findings", value: String(rep.summary.findings), hint: rep.summary.high ? rep.summary.high + " high" : "none high", tone: rep.summary.high ? "bad" : rep.summary.findings ? "warn" : "good"}
  ];
}

function leadLine(rep){
  var c = rep.blended.cur, cur = rep.account.currency, plats = rep.platforms.map(name);
  var who = plats.length > 1 ? plats[0] + " and " + plats[1] : plats[0];
  if(!c.cost) return who + " had no spend in " + rep.range.label.toLowerCase() + ".";
  var s = who + " spent " + money(c.cost, cur) + " in " + rep.range.label + " and " + (plats.length > 1 ? "between them claim " : "claims ") + num(c.conv) + " conversions" + (c.cpa == null ? "." : ", " + money(c.cpa, cur) + " each.");
  if(rep.reconciliation && rep.reconciliation.conversions){
    var r = rep.reconciliation.conversions;
    s += r.over ? " Your own system recorded " + num(r.actual) + ", so some are counted twice." : " Your own system recorded " + num(r.actual) + ".";
  }
  return s;
}

/* each of spend, conversions and value as one bar split between the platforms */
function shareBars(rep){
  var out = [], keys = [["cost", "Spend"], ["conv", "Conversions"], ["value", "Conversion value"]];
  keys.forEach(function(k){
    var segs = rep.platforms.map(function(p){return {platform: p, name: name(p), pct: rep.channels[p].share[k[0]]};}).filter(function(s){return s.pct != null;});
    if(segs.length) out.push({key: k[0], label: k[1], segments: segs});
  });
  return out;
}

/* the change in the blended cost per conversion as signed rows scaled to the biggest, or null with the reason */
function waterfall(rep, cur){
  var d = rep.decomposition;
  if(!d || !d.ok) return null;
  var rows = [{label: "Where the conversions came from (the mix)", value: d.mix}];
  Object.keys(d.rate).forEach(function(p){rows.push({label: poss(p) + " own cost per conversion", value: d.rate[p]});});
  var big = Math.max.apply(null, rows.map(function(r){return Math.abs(r.value);}).concat([0.0001]));
  rows.forEach(function(r){r.width = Math.round(Math.abs(r.value) / big * 100); r.text = (r.value >= 0 ? "+" : "-") + money(Math.abs(r.value), cur); r.tone = Math.abs(r.value) < 0.005 ? "neutral" : r.value > 0 ? "bad" : "good";});
  return {from: d.from, to: d.to, change: d.change, rows: rows, fromText: money(d.from, cur), toText: money(d.to, cur), changeText: (d.change >= 0 ? "+" : "-") + money(Math.abs(d.change), cur)};
}

/* a weekly line chart for key "cost" or "conv": one line per platform, in svg units */
function weeklyChart(rep, key, w, h){
  var pad = {l: 44, r: 12, t: 10, b: 24}, weeks = rep.weeks || [];
  if(weeks.length < 2) return null;
  var max = 0;
  weeks.forEach(function(wk){rep.platforms.forEach(function(p){if(wk[p] && wk[p][key] > max) max = wk[p][key];});});
  if(max <= 0) return null;
  var top = niceMax(max), iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  var x = function(i){return pad.l + iw * i / (weeks.length - 1);}, y = function(v){return pad.t + ih * (1 - v / top);};
  var series = rep.platforms.map(function(p){
    var pts = [];
    weeks.forEach(function(wk, i){if(wk[p]) pts.push([Math.round(x(i) * 10) / 10, Math.round(y(wk[p][key]) * 10) / 10]);});
    return {platform: p, name: name(p), points: pts, path: pts.map(function(q, i){return (i ? "L" : "M") + q[0] + " " + q[1];}).join(" ")};
  });
  var ticks = [0, 0.5, 1].map(function(f){return {y: Math.round(y(top * f) * 10) / 10, label: key === "cost" ? money(top * f, rep.account.currency) : num(top * f)};});
  var step = Math.max(1, Math.ceil(weeks.length / 6)), xt = [];
  weeks.forEach(function(wk, i){if(i % step === 0) xt.push({x: Math.round(x(i) * 10) / 10, label: X.dateText(wk.start)});});
  return {w: w, h: h, pad: pad, max: top, series: series, yTicks: ticks, xTicks: xt, baseline: Math.round(y(0) * 10) / 10};
}
function niceMax(v){
  var e = Math.pow(10, Math.floor(Math.log10(v))), f = v / e;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
}

/* what one link test says in words */
function linkText(t){
  var when = t.lag === 0 ? "in the same week" : "a week later";
  if(!t.enough) return {text: "Not enough weeks to test " + name(t.spend) + " spend against " + name(t.conversions) + " conversions " + when + ".", tone: "neutral"};
  var r = Math.round(t.r * 100) / 100;
  if(t.reads === "together") return {text: "When " + name(t.spend) + " spend rose, " + name(t.conversions) + " conversions rose " + when + " (correlation " + r + " over " + t.n + " weeks).", tone: "good"};
  if(t.reads === "opposite") return {text: "When " + name(t.spend) + " spend rose, " + name(t.conversions) + " conversions fell " + when + " (correlation " + r + " over " + t.n + " weeks).", tone: "warn"};
  return {text: name(t.spend) + " spend and " + name(t.conversions) + " conversions " + when + ": no clear link (correlation " + r + " over " + t.n + " weeks).", tone: "neutral"};
}

/* the person's own numbers, as the rows the page draws */
function claimRows(rec, cur){
  if(!rec) return [];
  var rows = [], c = rec.conversions, v = rec.value;
  if(c){
    rows.push({label: "Conversions the platforms claim", value: num(c.claimed)});
    rows.push({label: "Conversions your system recorded", value: num(c.actual)});
    rows.push({label: "Claimed against recorded", value: (Math.round(c.ratio * 100) / 100) + "x", tone: c.over ? "bad" : "neutral"});
    rows.push({label: "Spend per recorded conversion", value: money(rec.blendedCpa, cur), tone: "good"});
    Object.keys(c.perPlatform).forEach(function(p){
      var x = c.perPlatform[p];
      rows.push({label: name(p) + " cost per conversion" + (c.over ? ", scaled to what was recorded" : ""), value: x.cpa == null ? "n/a" : money(x.cpa, cur)});
    });
  }
  if(v){
    rows.push({label: "Conversion value the platforms claim", value: money(v.claimed, cur)});
    rows.push({label: "Revenue your system recorded", value: money(v.actual, cur)});
    rows.push({label: "Return on the revenue recorded", value: rec.blendedRoas == null ? "n/a" : ratio(rec.blendedRoas) + "x", tone: "good"});
    Object.keys(v.perPlatform).forEach(function(p){
      var x = v.perPlatform[p];
      rows.push({label: name(p) + " return" + (v.over ? ", scaled to what was recorded" : ""), value: x.roas == null ? "n/a" : ratio(x.roas) + "x"});
    });
  }
  return rows;
}

function attributionText(a){
  var out = {google: [], meta: []};
  if(a.google && a.google.counted && a.google.counted.length){
    a.google.counted.forEach(function(x){
      out.google.push({name: x.name, detail: x.model + ", clicks counted for " + x.clickWindow + " days" + (x.viewWindow ? ", views for " + x.viewWindow + " day" + (x.viewWindow === 1 ? "" : "s") : "") + (x.conversions == null ? "" : ", " + num(x.conversions) + " conversions in the range")});
    });
  }
  if(a.meta && a.meta.groups){
    a.meta.groups.forEach(function(g){
      out.meta.push({name: g.click + " day click" + (g.view ? ", " + g.view + " day view" : ", no view window"), detail: pct(g.share) + " of spend in " + g.adsets + " ad set" + (g.adsets === 1 ? "" : "s") + (g.assumed ? " (Meta's default: the ad set states none)" : "")});
    });
  }
  return out;
}

function accountNote(rep){return (rep.accounts || []).map(function(a){return a.name + " (" + name(a.platform) + ")";}).join(" + ");}

function typeRows(rep){
  var rows = [];
  rep.platforms.forEach(function(p){rep.channels[p].types.forEach(function(t){rows.push({platform: p, platformName: name(p), t: t});});});
  rows.sort(function(a, b){return b.t.cost - a.t.cost;});
  return rows;
}

var api = {PRESETS: PRESETS, MEASURES: MEASURES, MEASURE_HINT: MEASURE_HINT, LEVEL: LEVEL, ROLE: ROLE, levelInfo: levelInfo, roleInfo: roleInfo, name: name, poss: poss, num: num, money: money, ratio: ratio, pct: pct, change: change,
  changeTone: changeTone, tiles: tiles, leadLine: leadLine, shareBars: shareBars, waterfall: waterfall, weeklyChart: weeklyChart, niceMax: niceMax, linkText: linkText, claimRows: claimRows,
  attributionText: attributionText, accountNote: accountNote, typeRows: typeRows};
if(typeof module !== "undefined" && module.exports) module.exports = api; else root.XchData = api;
})(typeof window !== "undefined" ? window : globalThis);
