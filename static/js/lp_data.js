/* Landing Page & Relevancy Check: the figures and wording the page draws, as plain functions (so tests/test_landing_page_check_js.py runs them in Node).
 *
 * The server reads Google Ads, fetches the pages, runs every check and scores them (tracker/paid_lp*.py). This file only decides how a finding is worded,
 * which pages a view shows and in what order, how money and rates read, and what each label means. It never judges a page. The shared pieces (the
 * result and severity labels, grade tones, the score ring, "5 minutes ago") are the Account Audit's (au_data.js).
 */
(function(root){
"use strict";

var AD = typeof module !== "undefined" && module.exports ? require("./au_data.js") : root.AuditData;

var VIEWS = [
  {key:"attention",  label:"Needs attention"},
  {key:"unverified", label:"Not verified"},
  {key:"healthy",    label:"Healthy"},
  {key:"all",        label:"All pages"}
];
var SORTS = [
  {key:"spend",    label:"Most spend"},
  {key:"score",    label:"Lowest score"},
  {key:"problems", label:"Most problems"}
];
/* How a keyword, search term or offer sits on a page: what the label says and how it is drawn. */
var ON = {
  headline: {label:"In the headline",  tone:"good"},
  page:     {label:"On the page",      tone:"good"},
  partial:  {label:"Partly there",     tone:"warn"},
  none:     {label:"Not on the page",  tone:"bad"},
  unknown:  {label:"Not checked",      tone:"unk"}
};
var RATING = {below:{label:"Below average", tone:"bad"}, average:{label:"Average", tone:"ok"}, above:{label:"Above average", tone:"good"}};

function onInfo(on){
  if(on === true) return ON.page;
  if(on === false) return ON.none;
  return ON[on == null ? "unknown" : on] || ON.unknown;
}
function ratingInfo(r){return RATING[r] || {label:"Not rated", tone:"unk"};}

/* A finding with the catalog's wording put back (the server sends the wording once, in the catalog). */
function full(f, catalog){
  var c = (catalog || {})[f.id] || {};
  return {id:f.id, result:f.result, severity:f.severity || c.severity || "low", found:f.found || "", items:f.items || [], count:f.count == null ? (f.items || []).length : f.count,
    caps:f.caps || "", detail:f.detail, title:c.title || f.id, why:c.why || "", fix:c.fix || "", section:c.section || ""};
}
function pageFindings(page, catalog){
  var out = [];
  (page.sections || []).forEach(function(sec){sec.findings.forEach(function(f){var x = full(f, catalog); x.sectionLabel = sec.label; out.push(x);});});
  return out;
}
function problems(page){
  var t = (page.summary && page.summary.tally) || {};
  return (t.fail || 0) + (t.warn || 0);
}
/* unverified: nothing could be judged on it. attention: a D or F, a critical failure, or a high one. healthy: the rest. */
function pageState(page){
  if(!page.rated) return "unverified";
  var s = page.summary || {};
  if(page.grade === "D" || page.grade === "F" || s.critical || s.high) return "attention";
  return "healthy";
}
function haystack(p){
  var parts = [p.id, p.url, (p.seen && p.seen.title) || "", ((p.seen && p.seen.h1) || []).join(" ")];
  (p.adGroups || []).forEach(function(g){
    parts.push(g.name, g.campaign);
    (g.keywords || []).forEach(function(k){parts.push(k.text);});
  });
  return parts.join(" ").toLowerCase();
}
function viewCounts(pages){
  var c = {attention:0, unverified:0, healthy:0, all:(pages || []).length};
  (pages || []).forEach(function(p){c[pageState(p)]++;});
  return c;
}
/* The default view: what needs attention if anything does, else everything. */
function defaultView(pages){
  var c = viewCounts(pages);
  return c.attention ? "attention" : "all";
}
function filterPages(pages, view, text){
  var t = String(text || "").trim().toLowerCase();
  return (pages || []).filter(function(p){
    if(view !== "all" && pageState(p) !== view) return false;
    return !t || haystack(p).indexOf(t) >= 0;
  });
}
function sortPages(pages, key){
  var list = (pages || []).slice();
  function score(p){return p.rated && p.score != null ? p.score : 101;}              // pages with no score go last when sorting for the lowest
  if(key === "score") list.sort(function(a, b){return score(a) - score(b) || b.spend - a.spend;});
  else if(key === "problems") list.sort(function(a, b){return problems(b) - problems(a) || b.spend - a.spend;});
  else list.sort(function(a, b){return b.spend - a.spend;});
  return list;
}

/* ── Wording ─────────────────────────────────────────────── */
function moneyText(n, currency){
  if(n == null || isNaN(n)) return "n/a";
  var cur = /^[A-Z]{3}$/.test(currency || "") ? currency : "USD";
  try{return new Intl.NumberFormat("en-US", {style:"currency", currency:cur, maximumFractionDigits:Math.abs(n) >= 100 ? 0 : 2}).format(n);}
  catch(e){return String(Math.round(n));}
}
function rateText(n){return n == null ? "n/a" : (Math.round(n * 10) / 10) + "%";}
function numText(n){return n == null ? "n/a" : Number(n).toLocaleString("en-US", {maximumFractionDigits:0});}
function shareText(part, whole){return whole ? Math.round(100 * part / whole) + "%" : "0%";}
/* A page address shortened for a list: no scheme or www, the middle cut when it is long. */
function shortPage(id, max){
  var s = String(id || "").replace(/^https?:\/\//, "").replace(/^www\./, ""), m = max || 64;
  if(s.length <= m) return s;
  var keep = m - 1, head = Math.ceil(keep * 0.55);
  return s.slice(0, head) + "…" + s.slice(s.length - (keep - head));
}
function hostOf(id){return String(id || "").split("/")[0];}
/* Phones against desktops: the two conversion rates, and the second as a share of the first, when both can be told. */
function deviceRates(devices){
  var d = devices || {}, out = {};
  ["mobile", "desktop", "tablet"].forEach(function(k){
    var v = d[k]; out[k] = v && v.clicks ? {clicks:v.clicks, cost:v.cost, conversions:v.conversions, rate:100 * v.conversions / v.clicks} : null;
  });
  out.gap = out.mobile && out.desktop && out.desktop.rate > 0 ? out.mobile.rate / out.desktop.rate : null;
  var total = 0; Object.keys(d).forEach(function(k){total += d[k].clicks || 0;});
  out.mobileShare = out.mobile && total ? 100 * out.mobile.clicks / total : null;
  return out;
}
function statusText(seen){
  if(!seen) return "Not fetched";
  if(seen.skipped) return "Not read in time";
  if(seen.templated) return "Not fetched (the address is filled in by Google when someone clicks)";
  if(seen.blocked) return "Refused our request (HTTP " + seen.status + ")";
  if(seen.status && seen.status >= 400) return "HTTP " + seen.status;
  if(!seen.status) return seen.error ? "Could not be reached (" + seen.error + ")" : "Could not be reached";
  return "HTTP " + seen.status + (seen.responseMs != null ? " in " + (seen.responseMs >= 1000 ? (seen.responseMs / 1000).toFixed(1) + " s" : seen.responseMs + " ms") : "");
}
/* What the page was seen to say and offer, as label and value rows. */
function seenRows(seen){
  if(!seen) return [];
  var rows = [["Answer", statusText(seen)]];
  if(seen.redirected) rows.push(["Ended on", seen.finalUrl || ""]);
  if(seen.title == null && seen.words == null) return rows;
  rows.push(["Title", seen.title || "none"]);
  rows.push(["Main headline", (seen.h1 && seen.h1.length ? seen.h1.join(" | ") : "none") + (seen.h1Count > 1 ? " (" + seen.h1Count + " H1 headings)" : "")]);
  rows.push(["Words", seen.scriptBuilt ? numText(seen.words) + " (built by scripts, so the words are not all in the HTML)" : numText(seen.words)]);
  rows.push(["Forms", seen.forms && seen.forms.length ? seen.forms.map(function(f){return AD.plural(f.fields, "field") + " (" + f.required + " required)";}).join("; ") : (seen.embeds && seen.embeds.length ? "an embedded form (" + seen.embeds[0] + ")" : "none")]);
  rows.push(["Calls to action", seen.ctas && seen.ctas.length ? seen.ctas.slice(0, 5).map(function(c){return c.text;}).join(", ") : "none found"]);
  rows.push(["Phone", seen.tel && seen.tel.length ? "tappable (" + seen.tel[0] + ")" : (seen.phones && seen.phones.length ? seen.phones[0] + " (text only)" : "none")]);
  rows.push(["Links", seen.links ? seen.links.total + " (" + seen.links.nav + " in the header or menu)" : "n/a"]);
  rows.push(["Images", seen.images ? seen.images.count + " (" + seen.images.noSize + " without a set size, " + seen.images.lazy + " lazy-loaded)" : "n/a"]);
  rows.push(["Scripts", seen.scripts != null ? seen.scripts + " (" + (seen.thirdParty || 0) + " from other sites, " + (seen.blockingScripts || 0) + " hold up the first paint)" : "n/a"]);
  rows.push(["Size and compression", seen.bytes ? Math.round(seen.bytes / 1024) + " KB" + (seen.truncated ? " (cut at the reading limit)" : "") + (seen.encoding ? ", " + seen.encoding : ", not compressed") : "n/a"]);
  rows.push(["Proof", seen.proof && seen.proof.length ? seen.proof.join(", ") : "none found"]);
  rows.push(["Privacy policy link", seen.privacyLink ? "yes" : "no"]);
  rows.push(["Tags seen", seen.tools && seen.tools.length ? seen.tools.join(", ") : "none seen"]);
  return rows;
}
var VERDICT = {
  matches:  {label:"The page keeps the promise",         tone:"good"},
  partly:   {label:"The page keeps part of the promise", tone:"warn"},
  mismatch: {label:"The page does not keep the promise", tone:"bad"}
};
function verdictInfo(v){return VERDICT[v] || null;}
/* The fix-first rows laid out as a playbook: each row's place, how much of the biggest row's spend it carries (for a bar that is never empty), and whether it is one of
   the three to start with. */
var START_HERE = 3;
function impact(rows){
  var max = 0, list = rows || [];
  list.forEach(function(r){if((r.spend || 0) > max) max = r.spend;});
  return list.map(function(r, i){
    return {rank:i + 1, share:max > 0 ? Math.max(4, Math.round(100 * (r.spend || 0) / max)) : 0, start:i < START_HERE};
  });
}
/* The counts under a fix-first row, as pills: how many pages, how many fail, how many warn. */
function fixPills(r){
  var n = (r.failed || 0) + (r.warned || 0), out = [{text:n + (n === 1 ? " page" : " pages"), tone:""}];
  if(r.failed) out.push({text:r.failed + " failing", tone:"bad"});
  if(r.warned) out.push({text:r.warned + (r.warned === 1 ? " warning" : " warnings"), tone:"warn"});
  return out;
}
/* The glyph and wording for the weight of a check. */
var SEV_WORD = {critical:"Critical", high:"High impact", medium:"Medium", low:"Minor"};
function sevWord(sev){return SEV_WORD[sev] || "Minor";}
function secText(ms){return ms == null ? "n/a" : ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : Math.round(ms) + " ms";}
/* How a lab score is drawn: 90 and over is good, 50 to 89 needs work, under 50 is poor (Lighthouse's own bands). */
function labTone(score){return score == null ? "unk" : score >= 90 ? "good" : score >= 50 ? "warn" : "bad";}
/* The lab test as label and value rows, and the biggest savings and field data in words. */
function labRows(lab){
  if(!lab) return [];
  var rows = [["Performance score", lab.score == null ? "not scored" : lab.score + " out of 100"], ["Largest content shown after", secText(lab.lcpMs)], ["Page blocked for", secText(lab.tbtMs)],
    ["Layout shift", lab.cls == null ? "n/a" : String(lab.cls)], ["First paint after", secText(lab.fcpMs)], ["Speed index", secText(lab.speedIndexMs)]];
  var f = lab.field;
  if(f) rows.push(["Real visitors (Chrome UX Report)", [f.category ? "rated " + f.category : "", f.lcpMs != null ? "largest content after " + secText(f.lcpMs) : "", f.inpMs != null ? "interaction delay " + secText(f.inpMs) : "",
    f.cls != null ? "layout shift " + f.cls : ""].filter(Boolean).join(", ") || "no figures"]);
  return rows;
}
/* The headline tiles of a report. */
function tiles(summary, currency){
  var s = summary || {}, t = s.tally || {};
  return [
    {key:"checked", label:"Pages checked", value:(s.checked || 0) + " of " + (s.pagesOfSpend || 0), tone:"neutral",
      hint:s.spend ? shareText(s.spendChecked, s.spend) + " of the spend" : "no page took money"},
    {key:"risk", label:"Spend on pages with problems", value:moneyText(s.spendAtRisk || 0, currency), tone:s.spendAtRisk ? "bad" : "good",
      hint:s.pagesAtRisk ? AD.plural(s.pagesAtRisk, "page") + " with a D, an F or a critical failure" : "no page has a critical failure or a D or F"},
    {key:"critical", label:"Failed critical checks", value:s.critical || 0, tone:s.critical ? "bad" : "good", hint:s.critical ? "A page that does not open, a test site, a blocked crawler or no way to act" : "None"},
    {key:"high", label:"Failed high checks", value:s.high || 0, tone:s.high ? "bad" : "good", hint:s.high ? "Fix these next" : "None"},
    {key:"warn", label:"Warnings", value:t.warn || 0, tone:t.warn ? "warn" : "good", hint:t.warn ? "Worth a look" : "None"},
    {key:"unverified", label:"Not verified", value:moneyText(s.spendUnverified || 0, currency), tone:s.spendUnverified ? "unk" : "good",
      hint:s.spendUnverified ? "spend on pages not fetched, refused us or that could not be judged" : "Every page was verified"}
  ];
}
function readNote(report){
  var a = (report && report.account) || {}, list = (report && report.accounts) || [];
  if(list.length > 1) return (a.name || "") + " (" + list.map(function(x){return x.id;}).join(" and ") + ")";
  return (a.name || a.id || "") + (a.id && a.name ? " (" + a.id + ")" : "");
}
/* Which platforms' ads send people to a page (a page record without the list is a Google page), and the words that name them. */
var PLATFORM = {google:"Google Ads", meta:"Meta Ads"};
function platformsOf(x){var p = (x && x.platforms) || []; return p.length ? p : ["google"];}
function reaches(page, platform){return platformsOf(page).indexOf(platform) >= 0;}
function platformNames(platforms, joiner){return (platforms && platforms.length ? platforms : ["google"]).map(function(k){return PLATFORM[k] || k;}).join(joiner || " and ");}
function staleText(x, ago){return (PLATFORM[(x && x.platform) || "google"] || "The platform") + " is not answering now, so this report is built from the copy saved " + ago + ".";}
function sourceText(platforms){
  return "Source: " + platformNames(platforms) + " (read only) and the pages themselves, saved for an hour. A score is the share of the checks' weight that passed (critical checks weigh most, a warning counts half); " +
    "checks that could not be judged are left out and counted. A page that does not open scores 0. The account's score weights each page by what it spent. Nothing is changed in " +
    platformNames(platforms, " or ") + ": make the changes there and on the site.";
}
function noGroupsText(platforms){
  var meta = (platforms || []).indexOf("meta") >= 0;
  return "No Search ad group" + (meta ? " or Meta ad set" : "") + " sends people to this page. It is reached by Shopping or Performance Max, which have no keywords or ad text to hold a page against, " +
    (meta ? "by Meta ads that carry no words, " : "") + "or the ads could not be read.";
}

var api = {
  VIEWS:VIEWS, SORTS:SORTS, ON:ON, RATING:RATING, onInfo:onInfo, ratingInfo:ratingInfo, full:full, pageFindings:pageFindings, problems:problems, pageState:pageState,
  viewCounts:viewCounts, defaultView:defaultView, filterPages:filterPages, sortPages:sortPages, moneyText:moneyText, rateText:rateText, numText:numText, shareText:shareText,
  shortPage:shortPage, hostOf:hostOf, secText:secText, labTone:labTone, labRows:labRows, VERDICT:VERDICT, verdictInfo:verdictInfo, impact:impact, fixPills:fixPills, sevWord:sevWord, START_HERE:START_HERE, deviceRates:deviceRates, statusText:statusText, seenRows:seenRows, tiles:tiles, readNote:readNote, PLATFORM:PLATFORM, platformsOf:platformsOf, reaches:reaches, platformNames:platformNames, staleText:staleText, sourceText:sourceText, noGroupsText:noGroupsText
};
if(typeof module !== "undefined" && module.exports) module.exports = api; else root.LpData = api;
})(typeof window !== "undefined" ? window : globalThis);
