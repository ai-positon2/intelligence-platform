/* Meta Creative Analysis: the judging, as plain functions (so tests/test_creative_analysis_js.py runs them in Node).
 *
 * The server sends 90 days of daily ad rows and each ad's creative (tracker/paid_creative_roas.py). Everything here works on that one payload for whatever dates,
 * campaign and ad filters the reader picks:
 *
 *   Goal        Purchases (ROAS) when the account has purchase value, else Leads (cost per lead). The reader can switch.
 *   Totals      everything the active campaigns spent in the range, ads paused since included, which is what Ads Manager shows for those campaigns.
 *   Benchmark   the average of all those ads: ROAS, or leads per unit spent (shown as cost per lead). The Ads filter narrows the totals and the creatives, never the
 *               benchmark, so a filtered creative is still judged against the whole account, not against its own kind.
 *   Judged      only ads running now (the ad, its ad set and its campaign all on).
 *   Verdict     Scale   at least scaleX times the average and at least minRes results
 *               Keep    at least keepX times the average
 *               Cut     below that
 *               Too early  less than minSpend spent (by default one average cost per result)
 */
(function(root){
"use strict";

var VERDICTS = ["Scale","Keep","Cut","Early"];
var VLABEL = {Scale:"Scale", Keep:"Keep running", Cut:"Cut", Early:"Too early"};
var F = {date:0, ad:1, spend:2, impr:3, clicks:4, purch:5, rev:6, atc:7, v3:8, thru:9, leads:10};
var SUMS = ["spend","impr","clicks","purch","rev","atc","v3","thru","leads"];

function shift(d, n){ var x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0,10); }
function daysBetween(a, b){ return Math.round((new Date(b) - new Date(a)) / 864e5); }

function blank(){ var t = {ads:[]}; SUMS.forEach(function(k){ t[k] = 0; }); return t; }
function add(t, r){ SUMS.forEach(function(k){ t[k] += +r[F[k]] || 0; }); }
function addTotals(t, u){ SUMS.forEach(function(k){ t[k] += u[k]; }); }
function finish(t, goal){
  t.roas = t.spend ? t.rev / t.spend : 0;
  t.cpa = t.purch ? t.spend / t.purch : null;
  t.cpl = t.leads ? t.spend / t.leads : null;
  t.ctr = t.impr ? t.clicks / t.impr : 0;
  t.res = goal === "cpl" ? t.leads : t.purch;
  t.cpr = goal === "cpl" ? t.cpl : t.cpa;                       // cost per result
  t.cvr = t.clicks ? t.res / t.clicks : 0;
  t.score = goal === "cpl" ? (t.spend ? t.leads / t.spend : 0) : t.roas;   // higher is better, for both goals
  t.hook = t.v3 && t.impr ? t.v3 / t.impr : null;
  t.hold = t.v3 ? t.thru / t.v3 : null;
  t.cp3 = t.v3 ? t.spend / t.v3 : null;
  return t;
}

/* A campaign is on when Meta says so; an ad read without its campaign's status counts its campaign on when any ad of it is running. */
function campaignActive(D, a){
  if (a.campaign_status) return a.campaign_status === "ACTIVE";
  if (!D._liveCamps){ D._liveCamps = {}; D.ads.forEach(function(x){ if (x.status === "ACTIVE") D._liveCamps[x.campaign] = 1; }); }
  return !!D._liveCamps[a.campaign];
}

/* broad format for the Ads filter: single and flexible images together, single and flexible videos together */
function kindOf(a){ var f = String(a.format || ""); return /video/i.test(f) ? "Video" : /image/i.test(f) ? "Image" : (f || "Other"); }
function partLabel(a){ return a.partnership === true ? "Partnership ad" : a.partnership === false ? "Brand ad" : "Not known"; }
function adFilter(kind, partner){
  if (!kind && !partner) return null;
  return function(a){ return (!kind || kindOf(a) === kind) && (!partner || (partner === "1" ? a.partnership === true : a.partnership === false)); };
}

/* Purchases when the account has any purchase value in the payload, else leads when it has leads, else purchases. */
function defaultGoal(D){
  var rev = 0, leads = 0;
  D.rows.forEach(function(r){ rev += +r[F.rev] || 0; leads += +r[F.leads] || 0; });
  return rev > 0 ? "roas" : leads > 0 ? "cpl" : "roas";
}

/* o: {since, until, campaign, kind, partner, goal, scaleX, keepX, minRes, minSpend (null = one average cost per result)} */
function compute(D, o){
  var keep = adFilter(o.kind, o.partner), perAd = {}, order = [], total = blank(), paused = blank(), all = blank();
  D.rows.forEach(function(r){
    if (r[F.date] < o.since || r[F.date] > o.until) return;
    var ad = D.ads[r[F.ad]];
    if (!ad || (o.campaign && ad.campaign !== o.campaign) || !campaignActive(D, ad)) return;
    add(all, r);
    if (keep && !keep(ad)) return;
    add(total, r);
    if (ad.status !== "ACTIVE"){ add(paused, r); return; }
    var t = perAd[r[F.ad]];
    if (!t){ t = perAd[r[F.ad]] = blank(); t.ad = ad; t.ads = [ad]; order.push(r[F.ad]); }
    add(t, r);
  });
  finish(total, o.goal); finish(all, o.goal); finish(paused, o.goal);
  total.paused = paused; total.all = all; total.filtered = !!keep;
  var autoMin = all.cpr ? Math.round(all.cpr) : 50;
  var rules = {goal:o.goal, avg:all.score, scaleX:+o.scaleX || 1.2, keepX:+o.keepX || 0.8, minRes:+o.minRes || 0,
               minSpend:o.minSpend == null || o.minSpend === "" ? autoMin : +o.minSpend || 0, autoMin:autoMin};
  return {since:o.since, until:o.until, perAd:order.map(function(k){ return finish(perAd[k], o.goal); }), total:total, rules:rules};
}

function judge(t, R, money0){
  var m = money0 || function(x){ return "$" + Math.round(x); };
  var ratio = R.avg ? t.score / R.avg : 0, word = R.goal === "cpl" ? "lead" : "purchase";
  t.ratio = ratio;
  if (t.spend < R.minSpend){ t.verdict = "Early"; t.why = m(t.spend) + " spent, needs " + m(R.minSpend) + " before judging"; }
  else if (ratio >= R.scaleX && t.res >= R.minRes){ t.verdict = "Scale"; t.why = metricName(R.goal) + " " + metricText(t, R.goal, m) + " beats the average " + ratio.toFixed(2) + "×"; }
  else if (ratio >= R.keepX){ t.verdict = "Keep"; t.why = ratio >= R.scaleX ? "Beats the average but only " + Math.round(t.res) + " " + word + (Math.round(t.res) === 1 ? "" : "s") : metricName(R.goal) + " " + metricText(t, R.goal, m) + " is near the average"; }
  else { t.verdict = "Cut"; t.why = t.res ? metricName(R.goal) + " " + metricText(t, R.goal, m) + " is " + (100 - ratio * 100).toFixed(0) + "% worse than the average" : m(t.spend) + " spent, no " + word + "s"; }
  return t;
}
function metricName(goal){ return goal === "cpl" ? "Cost per lead" : "ROAS"; }
function metricText(t, goal, money){ return goal === "cpl" ? (t.cpl ? money(t.cpl) : "none") : t.roas.toFixed(2); }

function groupBy(list, keyFn, labelFn, goal){
  var m = {}, order = [];
  list.forEach(function(t){
    var k = keyFn(t); if (k == null) return;
    var g = m[k];
    if (!g){ g = m[k] = blank(); g.key = k; g.label = labelFn ? labelFn(t) : k; g.ads = []; order.push(k); }
    addTotals(g, t);
    g.ads.push.apply(g.ads, t.ads);
  });
  return order.map(function(k){ return finish(m[k], goal); });
}

/* each running ad, or ads sharing a name merged into one row ("name") */
function creatives(C, by, money0){
  var list = C.perAd;
  if (by === "name"){
    list = groupBy(list, function(t){ return t.ad.name.trim().toLowerCase(); }, function(t){ return t.ad.name; }, C.rules.goal);
    list.forEach(function(g){ g.ad = g.ads.filter(function(a){ return a.status === "ACTIVE"; })[0] || g.ads[0]; });
  }
  list.forEach(function(t){ judge(t, C.rules, money0); });
  return list;
}

/* tags in ad names, "Ad537_F:Static_SKU:LongTerm_T:Branded_X:IClearedMyEczema" -> {F:"Static", SKU:"LongTerm", ...} */
function tagsOf(name){ var o = {}, re = /(?:^|_)([A-Za-z]{1,4}):([^_]+)/g, m; while ((m = re.exec(String(name || "")))) o[m[1]] = m[2]; return o; }
var TAGS = {F:"format", SKU:"product", T:"ad type", C:"concept", X:"hook / message", H:"landing page", E:"creator", V:"visual", I:"iteration / style", PT:"video length", P:"version"};
function isUgc(name){ return /(^|[^a-z])ugc([^a-z]|$)/i.test(String(name || "")); }

/* the most common delimiter in the running ads' names, for the name-part breakdown */
function nameDelim(names){
  var counts = {"_":0, "|":0, " - ":0, "-":0};
  names.forEach(function(n){ Object.keys(counts).forEach(function(k){ counts[k] += String(n).split(k).length - 1; }); });
  return Object.keys(counts).sort(function(a, b){ return counts[b] - counts[a]; })[0];
}

/* a text cell must never run as a formula in Excel; a number is left alone */
function csvCell(v){ if (v == null) v = ""; var num = typeof v === "number"; v = String(v); if (!num && /^[=+\-@\t\r]/.test(v)) v = "'" + v; return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }

var api = {VERDICTS:VERDICTS, VLABEL:VLABEL, F:F, TAGS:TAGS, shift:shift, daysBetween:daysBetween, blank:blank, finish:finish, campaignActive:campaignActive, kindOf:kindOf,
  partLabel:partLabel, adFilter:adFilter, defaultGoal:defaultGoal, compute:compute, judge:judge, metricName:metricName, metricText:metricText, groupBy:groupBy,
  creatives:creatives, tagsOf:tagsOf, isUgc:isUgc, nameDelim:nameDelim, csvCell:csvCell};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.CAData = api;
})(typeof window !== "undefined" ? window : globalThis);
