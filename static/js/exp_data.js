/* Test & Experiment Tracker: the figures and wording the page draws, as plain functions (so tests/test_experiment_tracker_js.py runs them in Node).
 *
 * The server reads Google Ads, runs every comparison and decides every verdict (tracker/paid_exp*.py). This file only decides how a verdict is labelled and
 * coloured, which tests a view shows and in what order, how rates, money and gaps read, and the shape of the chart of each experiment's two arms. It never judges
 * a test. The shared pieces ("5 minutes ago", plurals) are the Account Audit's (au_data.js).
 */
(function(root){
"use strict";

var AD = typeof module !== "undefined" && module.exports ? require("./au_data.js") : root.AuditData;

/* A verdict, for an experiment ("trial") and for the ads of an ad group ("ad"): what it says and how it is drawn. */
var VERDICT = {
  trial: {
    winner:        {label:"Trial is ahead",   tone:"good",    icon:"up"},
    behind:        {label:"Trial is behind",  tone:"bad",     icon:"down"},
    no_difference: {label:"No difference",    tone:"neutral", icon:"equal"},
    too_early:     {label:"Too early",        tone:"wait",    icon:"clock"},
    no_data:       {label:"No data",          tone:"unk",     icon:"help"},
    not_measured:  {label:"Not measured",     tone:"unk",     icon:"help"}
  },
  ad: {
    winner:        {label:"Clear leader",     tone:"good",    icon:"crown"},
    partial:       {label:"Leads some",       tone:"good",    icon:"crown"},
    behind:        {label:"Behind",           tone:"bad",     icon:"down"},
    no_difference: {label:"No difference",    tone:"neutral", icon:"equal"},
    too_early:     {label:"Too early",        tone:"wait",    icon:"clock"},
    no_data:       {label:"Not shown",        tone:"unk",     icon:"help"}
  }
};
/* What a test needs from a person, in the order the page lists them. */
var KIND = {
  decide: {label:"Decide now",           tone:"bad",  icon:"flag"},
  apply:  {label:"Apply it",             tone:"good", icon:"check"},
  end:    {label:"End it",               tone:"warn", icon:"stop"},
  pause:  {label:"Pause the losers",     tone:"warn", icon:"pause"},
  stuck:  {label:"Not getting traffic",  tone:"warn", icon:"clock"},
  check:  {label:"Check it",             tone:"unk",  icon:"help"}
};
var STATE = {running:"Running", "setting up":"Setting up", ended:"Ended", applied:"Applied"};
var EXP_VIEWS = [
  {key:"needs",    label:"Needs a decision"},
  {key:"running",  label:"Running"},
  {key:"finished", label:"Finished"},
  {key:"all",      label:"All"}
];
var AD_VIEWS = [
  {key:"leader",  label:"Has a leader"},
  {key:"flat",    label:"No difference"},
  {key:"early",   label:"Too early"},
  {key:"all",     label:"All"}
];
var MIN_POINT = {convRate:20, ctr:200};          /* a running rate is drawn once its arm has this many clicks (or impressions): before that it swings too wildly to read */

function verdictInfo(v, kind){return (VERDICT[kind === "ad" ? "ad" : "trial"][v]) || {label:"Unknown", tone:"unk", icon:"help"};}
function kindInfo(k){return KIND[k] || {label:"Look", tone:"unk", icon:"help"};}
function stateInfo(s){return STATE[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1) : "Unknown");}

function pct(n){return n == null || isNaN(n) ? "n/a" : (Math.round(n * 10) / 10) + "%";}
function rel(n){
  if(n == null || isNaN(n)) return "";
  var r = Math.round(n * 10) / 10;
  return (r > 0 ? "+" : "") + r + "%";
}
function conf(c){return c == null || isNaN(c) ? "" : c >= 100 ? "over 99.9% sure" : (Math.round(c * 10) / 10) + "% sure";}
function num(n){return n == null || isNaN(n) ? "n/a" : Number(n).toLocaleString("en-US", {maximumFractionDigits:0});}
function money(n, currency){
  if(n == null || isNaN(n)) return "n/a";
  var cur = /^[A-Z]{3}$/.test(currency || "") ? currency : "USD";
  try{return new Intl.NumberFormat("en-US", {style:"currency", currency:cur, maximumFractionDigits:Math.abs(n) >= 100 ? 0 : 2}).format(n);}
  catch(e){return String(Math.round(n));}
}
function dateText(iso){
  if(!iso) return "";
  var d = new Date(iso + "T00:00:00Z"); if(isNaN(d)) return iso;
  return d.toLocaleDateString("en-US", {month:"short", day:"numeric", timeZone:"UTC"});
}
function span(start, end){
  if(!start) return "";
  return dateText(start) + (end ? " to " + dateText(end) : " onward");
}

/* The experiments a view shows. "needs" is the ones the server listed as needing a decision, by id. */
function needsIds(report, type){
  var out = {};
  ((report && report.needs) || []).forEach(function(n){if(n.ref && n.ref.type === type) out[n.ref.id] = n.kind;});
  return out;
}
function expCounts(report){
  var list = (report && report.experiments) || [], need = needsIds(report, "experiment");
  return {
    needs: list.filter(function(e){return need[e.id];}).length,
    running: list.filter(function(e){return e.state === "running" || e.state === "setting up";}).length,
    finished: list.filter(function(e){return e.state === "ended" || e.state === "applied";}).length,
    all: list.length
  };
}
function filterExperiments(report, view){
  var list = (report && report.experiments) || [], need = needsIds(report, "experiment");
  return list.filter(function(e){
    if(view === "needs") return !!need[e.id];
    if(view === "running") return e.state === "running" || e.state === "setting up";
    if(view === "finished") return e.state === "ended" || e.state === "applied";
    return true;
  });
}
function defaultExpView(report){
  var c = expCounts(report);
  return c.needs ? "needs" : c.running ? "running" : "all";
}
function adCounts(tests){
  var l = tests || [];
  return {
    leader: l.filter(function(t){return t.verdict === "winner" || t.verdict === "partial";}).length,
    flat: l.filter(function(t){return t.verdict === "no_difference";}).length,
    early: l.filter(function(t){return t.verdict === "too_early" || t.verdict === "no_data";}).length,
    all: l.length
  };
}
function adMatches(t, text){
  var q = String(text || "").trim().toLowerCase(); if(!q) return true;
  var hay = [t.name, t.campaign].concat((t.ads || []).map(function(a){return (a.headlines || []).join(" ") + " " + (a.descriptions || []).join(" ");})).join(" ").toLowerCase();
  return q.split(/\s+/).every(function(w){return hay.indexOf(w) !== -1;});
}
function filterAdTests(tests, view, text){
  return (tests || []).filter(function(t){
    var v = t.verdict;
    var ok = view === "leader" ? (v === "winner" || v === "partial") : view === "flat" ? v === "no_difference" : view === "early" ? (v === "too_early" || v === "no_data") : true;
    return ok && adMatches(t, text);
  });
}
function defaultAdView(tests){
  var c = adCounts(tests);
  return c.leader ? "leader" : "all";
}

/* The headline tiles. */
function tiles(summary, currency){
  var s = summary || {};
  return [
    {key:"needs", label:"Need a decision", value:s.needsAction || 0, tone:s.needsAction ? "bad" : "good", hint:s.needsAction ? "Apply, end, pause or look into these first" : "Nothing is waiting on a person"},
    {key:"running", label:"Experiments running", value:s.running || 0, tone:"neutral",
      hint:(s.experiments || 0) ? AD.plural(s.experiments, "experiment") + " in all: " + (s.ended || 0) + " ended, " + (s.applied || 0) + " applied" : "No experiment in this account"},
    {key:"result", label:"Running with a result", value:(s.winners || 0) + (s.behind || 0), tone:(s.winners || s.behind) ? "good" : "neutral",
      hint:(s.winners || 0) + " ahead, " + (s.behind || 0) + " behind, " + (s.noDifference || 0) + " no difference, " + (s.tooEarly || 0) + " too early"},
    {key:"ads", label:"Ad groups testing copy", value:s.adTests || 0, tone:"neutral",
      hint:(s.adTests || 0) > (s.adTestsShown || 0) ? "the " + (s.adTestsShown || 0) + " that spent most are shown" : "two or more live ads in each"},
    {key:"leaders", label:"Ad tests with a leader", value:s.adConclusive || 0, tone:s.adConclusive ? "good" : "neutral",
      hint:(s.adNoDifference || 0) + " no difference, " + (s.adTooEarly || 0) + " too early"},
    {key:"spend", label:"Spend in tests", value:money((s.experimentSpend || 0) + (s.adSpend || 0), currency), tone:"neutral", hint:"experiments over their run, ad tests over 30 days"}
  ];
}
/* One sentence for the top of the report. */
/* Which platforms the report read (a report that does not say is a Google report), and the words that name them. */
var PLATFORM = {google:"Google Ads", meta:"Meta Ads"};
function platformsOf(report){var p = (report && report.summary && report.summary.platforms) || []; return p.length ? p : ["google"];}
function platformNames(platforms, joiner){return (platforms && platforms.length ? platforms : ["google"]).map(function(k){return PLATFORM[k] || k;}).join(joiner || " and ");}
function isMetaId(id){return String(id || "").indexOf("meta:") === 0;}
function staleText(x, ago){return (PLATFORM[(x && x.platform) || "google"] || "The platform") + " is not answering now, so this report is built from the copy saved " + ago + ".";}
function accountNote(report){
  var a = (report && report.account) || {}, list = (report && report.accounts) || [];
  if(list.length > 1) return (a.name || "") + " (" + list.map(function(x){return x.id;}).join(" and ") + ")";
  return (a.name || a.id || "") + (a.id && a.name ? " (" + a.id + ")" : "");
}
function sourceText(platforms){
  var p = platforms && platforms.length ? platforms : ["google"], both = p.length > 1;
  var conv = both ? "Conversions are each platform's own and may be counted late." : p[0] === "meta" ? "Conversions are what Meta counts for each campaign's objective and may be counted late." : "Conversions are Google's own and may be counted late.";
  return "Source: " + platformNames(p) + " (read only), saved for an hour. A verdict is a two-sided two-proportion z-test at 95 percent confidence, made only when a test has 7 days and enough clicks, impressions or conversions to count. " +
    conv + " Nothing is changed in " + platformNames(p, " or ") + ": apply, end or pause a test there.";
}
/* "3 ad groups", "3 ad sets" or "3 ad groups and ad sets": what the ad tests are called for the platforms read. */
function adUnit(platforms, n){
  var p = platforms && platforms.length ? platforms : ["google"], g = p.indexOf("google") >= 0, m = p.indexOf("meta") >= 0;
  if(g && m) return n + (n === 1 ? " ad group or ad set" : " ad groups and ad sets");
  return AD.plural(n, m ? "ad set" : "ad group");
}

function leadLine(report){
  var s = (report && report.summary) || {};
  var parts = [];
  if(s.needsAction) parts.push(AD.plural(s.needsAction, "test") + (s.needsAction === 1 ? " needs" : " need") + " a decision");
  else if(s.experiments || s.adTests) parts.push("Nothing needs a decision right now");
  else parts.push("This account has no test to track");
  var bits = [];
  if(s.experiments) bits.push(AD.plural(s.running || 0, "experiment") + " running" + ((s.winners || s.behind) ? ", " + ((s.winners || 0) + (s.behind || 0)) + " with a result" : ""));
  if(s.adTests) bits.push(adUnit(platformsOf(report), s.adTests) + " testing copy" + (s.adConclusive ? ", " + s.adConclusive + " with a clear leader" : ""));
  return parts[0] + (bits.length ? ". " + bits.join(" and ") + "." : ".");
}

/* ── The picture of an experiment's two arms ─────────────────────────────── */
function dayList(dates){
  if(!dates.length) return [];
  var out = [], t = Date.parse(dates[0] + "T00:00:00Z"), end = Date.parse(dates[dates.length - 1] + "T00:00:00Z");
  for(; t <= end; t += 86400000) out.push(new Date(t).toISOString().slice(0, 10));
  return out;
}
/* A scale for values up to `max`: the step between ticks (1, 2, 2.5 or 5 of a power of ten) and the top, a whole number of steps, with at most five steps. */
function scale(max){
  if(!(max > 0)) return {top:1, step:0.25};
  var exp = Math.pow(10, Math.floor(Math.log(max) / Math.LN10)), r6 = function(v){return Math.round(v * 1e6) / 1e6;};
  /* max is under 10 times exp, so a step of 5 times exp always holds it in two steps: the search cannot come up empty */
  var step = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5].map(function(m){return r6(m * exp);}).filter(function(st){return Math.ceil(max / st) <= 5;})[0];
  return {top:r6(Math.ceil(max / step) * step), step:step};
}
/* The conversion rate (or click rate) each arm had earned by each day of the experiment, as drawn coordinates in a w by h box with `pad` around it.
 * `exp` is one experiment of the report; `metric` is "convRate" or "ctr" (the verdict's, or conversion rate when there are conversions). */
function chart(exp, w, h, pad){
  var arms = (exp && exp.arms || []).filter(function(a){return a.days && a.days.length;});
  var all = []; arms.forEach(function(a){a.days.forEach(function(d){all.push(d[0]);});});
  all.sort(); all = all.filter(function(d, i){return i === 0 || d !== all[i - 1];});
  var days = dayList(all), cmp = exp && exp.comparison;
  var anyConv = arms.some(function(a){return a.days.some(function(d){return d[4] > 0;});});
  var metric = (cmp && cmp.metric) || (anyConv ? "convRate" : "ctr");
  var P = pad == null ? 28 : pad, W = w || 600, H = h || 200;
  var out = {metric:metric, label:metric === "ctr" ? "Click rate" : "Conversion rate", w:W, h:H, pad:P, days:days, series:[], yMax:0, yTicks:[], xTicks:[]};
  if(days.length < 2) return out;
  var perArm = arms.map(function(a){
    var byDate = {}; a.days.forEach(function(d){byDate[d[0]] = d;});
    var hits = 0, of = 0, pts = [];
    days.forEach(function(day, i){
      var d = byDate[day];
      if(d){hits += metric === "ctr" ? d[2] : d[4]; of += metric === "ctr" ? d[1] : d[2];}
      pts.push(of >= MIN_POINT[metric] ? {i:i, date:day, rate:100 * hits / of, of:of} : null);
    });
    return {name:a.name, control:!!a.control, pts:pts};
  });
  var max = 0; perArm.forEach(function(s){s.pts.forEach(function(p){if(p && p.rate > max) max = p.rate;});});
  if(!(max > 0)) return out;
  var sc = scale(max * 1.08), top = sc.top, iw = W - 2 * P, ih = H - 2 * P;
  var X = function(i){return Math.round((P + iw * i / (days.length - 1)) * 10) / 10;};
  var Y = function(v){return Math.round((H - P - ih * v / top) * 10) / 10;};
  out.yMax = top;
  perArm.forEach(function(s){
    var d = "", last = null;
    var pts = s.pts.filter(Boolean).map(function(p){return {x:X(p.i), y:Y(p.rate), date:p.date, rate:Math.round(p.rate * 100) / 100};});
    pts.forEach(function(p, i){d += (i ? "L" : "M") + p.x + " " + p.y;});
    last = pts.length ? pts[pts.length - 1] : null;
    out.series.push({name:s.name, control:s.control, d:d, points:pts, last:last});
  });
  for(var v = 0; v <= top + 1e-9; v += sc.step){var tv = Math.round(v * 1e6) / 1e6; out.yTicks.push({value:tv, y:Y(tv)});}
  var step = Math.max(1, Math.ceil(days.length / 5));
  for(var j = 0; j < days.length; j += step) out.xTicks.push({x:X(j), label:dateText(days[j])});
  return out;
}
/* The two bars of the arms' rates: each as a share of the longer one, so a gap shows as a gap. */
function bars(cmp){
  if(!cmp || cmp.control == null || cmp.trial == null) return null;
  var top = Math.max(cmp.control, cmp.trial);
  if(!(top > 0)) return {control:0, trial:0};
  return {control:Math.round(100 * cmp.control / top), trial:Math.round(100 * cmp.trial / top)};
}
/* The ruler under a verdict: how sure the data is, from 50 percent (a coin toss) to 100, with the 95 percent line a verdict needs. */
function ruler(c){
  if(c == null || isNaN(c)) return null;
  var at = Math.max(0, Math.min(100, (c - 50) * 2));
  return {at:Math.round(at * 10) / 10, line:90, passed:c >= 95};
}
function waitText(w){
  if(!w) return "";
  var more = w.more || {}, parts = [];
  if(more.clicks) parts.push(num(more.clicks) + " more clicks in each arm");
  if(more.impressions) parts.push(num(more.impressions) + " more impressions in each arm");
  if(more.conversions) parts.push(num(more.conversions) + " more conversions");
  if(w.days) parts.push(AD.plural(w.days, "more day"));
  return parts.join(", ");
}

var api = {
  VERDICT:VERDICT, KIND:KIND, STATE:STATE, EXP_VIEWS:EXP_VIEWS, AD_VIEWS:AD_VIEWS, verdictInfo:verdictInfo, kindInfo:kindInfo, stateInfo:stateInfo, PLATFORM:PLATFORM, platformsOf:platformsOf, platformNames:platformNames, isMetaId:isMetaId, staleText:staleText, accountNote:accountNote, sourceText:sourceText, adUnit:adUnit,
  pct:pct, rel:rel, conf:conf, num:num, money:money, dateText:dateText, span:span, expCounts:expCounts, filterExperiments:filterExperiments, defaultExpView:defaultExpView,
  adCounts:adCounts, filterAdTests:filterAdTests, defaultAdView:defaultAdView, tiles:tiles, leadLine:leadLine, chart:chart, bars:bars, ruler:ruler, waitText:waitText, scale:scale
};
if(typeof module !== "undefined" && module.exports) module.exports = api; else root.ExpData = api;
})(typeof window !== "undefined" ? window : globalThis);
