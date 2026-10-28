/* Competitor Analysis: the wording and the shapes the page draws, as plain functions (so tests/test_competitor_analysis_js.py runs them in Node).
 *
 * The server decides everything (tracker/paid_compete*.py): which messages the competitors use more than we do, the ideas, what was read and what could not be. This file only words it, lays it
 * out in tables and turns the report into a CSV. It never judges an ad.
 */
(function(root){
"use strict";

var PLAT = {google: "Google Ads", meta: "Meta Ads"};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}};

function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }
function day(iso){ return iso ? String(iso).slice(0, 10) : ""; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function money(v){ return "$" + (Math.round(v * 100) / 100).toFixed(2); }

/* a read, how old it is */
function ageText(d){
  if (d == null) return "not read yet";
  return d < 1 ? "read today" : "read " + plural(d, "day") + " ago";
}

/* the tiles above the page */
function tiles(rep){
  var c = rep.counts || {}, comps = rep.competitors || [], ages = [];
  Object.keys(rep.analysis || {}).forEach(function(p){ (rep.analysis[p].competitors || []).forEach(function(s){ if (s.ageDays != null) ages.push(s.ageDays); }); });
  var oldest = ages.length ? Math.max.apply(null, ages) : null;
  return [
    ["Competitors", int(comps.length), comps.length ? "saved for this account" : "none saved yet: add the first", comps.length ? "" : "warn"],
    ["Competitor ads read", int(c.competitorAds || 0), c.reads ? "from " + plural(c.reads, "read") : "press Refresh to read them", c.reads ? "" : "warn"],
    ["Message gaps", int(c.gaps || 0), c.gaps ? "things they say more than we do" : (c.reads ? "no clear gap against our ads" : "needs their ads first"), c.gaps ? "warn" : (c.reads ? "good" : "")],
    ["Ideas", int(c.ideas || 0), c.ideas ? "to test in our ads" : "none yet", ""],
    ["Last read", ages.length ? (oldest < 1 ? "today" : plural(oldest, "day") + " ago") : "never", ages.length ? "the oldest of the competitors' reads" : "no competitor has been read", ""]
  ];
}

/* what a refresh would cost, in words */
function estimateText(est, cacheDays){
  est = est || {};
  if (!est.reads) return "Nothing can be read yet: a competitor needs a website or a Facebook page.";
  return "A refresh reads " + plural(est.reads, "competitor page") + ", up to " + plural(est.ads, "ad") + ", about " + money(est.usd) + " at most and " + (est.minutes ? "up to about " + plural(est.minutes, "minute") : "a few minutes") +
    " (Google shows each text ad as a picture, so those reads are slow). Reads newer than " + plural(cacheDays || 7, "day") + " are not paid for again.";
}

/* the refresh in progress or last done, in words */
function jobText(job){
  if (!job) return "";
  var steps = job.steps || [], done = steps.filter(function(s){ return s.status === "done" || s.status === "failed"; }).length, failed = steps.filter(function(s){ return s.status === "failed"; }).length;
  if (job.status === "running") return "Reading competitor ads: " + done + " of " + steps.length + " done. This takes a minute or two.";
  if (job.status === "failed") return "The refresh failed: " + (job.error || "no reason given") + ".";
  if (!steps.length) return "Every competitor was read within the last week, so nothing was read again" + (job.skippedFresh ? " (" + plural(job.skippedFresh, "read") + " kept)." : ".");
  return "Last refresh " + day(job.finishedAt || job.startedAt) + " by " + (job.by || "someone") + ": " + plural(steps.length - failed, "read") + " done" + (failed ? ", " + failed + " failed" : "") + ".";
}
function stepLine(s){
  var what = s.name + " on " + (s.platform === "google" ? "Google" : "Meta");
  if (s.status === "done") return {text: what + ": " + plural(s.ads, "ad") + (s.capped ? " (limit of " + int(s.limit) + " reached, they run more)" : "") + (s.partial ? " (stopped early, only part was read)" : ""), tone: s.partial || s.capped ? "warn" : "ok"};
  if (s.status === "failed") return {text: what + ": " + s.error, tone: "bad"};
  return {text: what + ": " + (s.status === "reading" ? "reading" : "waiting"), tone: "wait"};
}

/* the competitors of one platform as table rows */
function competitorRows(rep, platform){
  var a = (rep.analysis || {})[platform];
  if (!a) return [];
  return (a.competitors || []).map(function(s){
    var longest = (s.longest || [])[0];
    var formats = Object.keys(s.formats || {}).map(function(k){ return s.formats[k] + " " + k; }).join(", ");
    var extra = s.capped ? "Limit of " + int(s.limit) + " ads reached: they run more than were read." : (s.partial ? "Stopped early: only part of their ads were read." : "");
    return {name: s.name, status: s.error ? "failed" : (s.skipped ? "skipped" : (s.count ? "read" : "empty")), note: s.error || s.skipped || (s.count ? ageText(s.ageDays) : "read, no ads found"), flag: s.error || s.skipped ? "" : extra,
      noText: s.error || s.skipped || !s.noText ? "" : plural(s.noText, "ad") + (s.noText === 1 ? " has" : " have") + " no text to read (a picture or a video only).",
      capped: !!s.capped, partial: !!s.partial, ads: s.count, active: s.active, newest: s.new, median: s.medianDays == null ? "" : Math.round(s.medianDays), longest: longest ? longest.days : null, formats: formats,
      ctas: Object.keys(s.ctas || {}).map(function(k){ return k + " (" + s.ctas[k] + ")"; }).join(", "), landing: (s.landing || []).slice(0, 2).map(function(l){ return l.path + " (" + l.ads + ")"; }).join(", ")};
  });
}

/* the messages as bars: what they say, what we say */
function gapRows(rep, platform){
  var a = (rep.analysis || {})[platform];
  if (!a) return {judged: false, why: "", gaps: [], edges: []};
  var c = a.compare || {};
  return {judged: !!c.judged, why: c.why || "", gaps: (c.gaps || []).map(function(g){ return {label: g.label, their: g.theirShare, own: g.ownShare, points: g.points, level: g.level, ads: g.ads, who: g.who,
    example: (a.examples || {})[g.theme] || null}; }), edges: (c.edges || []).map(function(g){ return {label: g.label, their: g.theirShare, own: g.ownShare, points: g.points}; })};
}
function gapText(g){
  return Math.round(g.their) + "% of their lines, " + Math.round(g.own) + "% of ours (" + plural(g.ads, "ad") + " from " + plural(g.who, "competitor") + ")";
}

function exampleText(e){
  return e ? "\"" + e.line + "\" (" + e.who + (e.days ? ", running " + plural(e.days, "day") : "") + ")" : "";
}

/* the ideas as cards */
function ideaCards(rep){
  return (rep.ideas || []).map(function(i){
    var l = levelInfo(i.level);
    return {id: i.id, tone: l.tone, level: l.label, platform: PLAT[i.platform] || i.platform, title: i.title, why: i.why, tryThis: i.tryThis, example: exampleText(i.example)};
  });
}

/* what one platform's ads of the competitors say about the offer and what has run longest */
function offerRows(rep, platform){
  var a = (rep.analysis || {})[platform];
  return a ? (a.offers || []).map(function(o){ return {text: o.text, lines: o.n, who: (o.who || []).join(", "), days: o.maxDays || 0}; }) : [];
}
function adRows(list){
  return (list || []).map(function(x){ return {who: x.who, headline: x.headline, body: x.body, format: x.format, cta: x.cta, days: x.days, since: x.firstShown, url: x.url, copies: x.copies}; });
}
function winnerRows(rep, platform){
  var a = (rep.analysis || {})[platform];
  return a ? adRows(a.winners) : [];
}
function newestRows(rep, platform){
  var a = (rep.analysis || {})[platform], out = [];
  if (!a) return out;
  (a.competitors || []).forEach(function(s){ (s.newest || []).forEach(function(x){ out.push(x); }); });
  out.sort(function(x, y){ return String(y.firstShown).localeCompare(String(x.firstShown)); });
  return adRows(out.slice(0, 6));
}

/* the company search as cards to pick from */
function resultCards(res){
  return ((res && res.companies) || []).map(function(c){
    var bits = [c.industry, c.location].filter(function(x){ return x; });
    return {name: c.name, domain: c.domain, website: c.website || "", logo: c.logo || "", sub: bits.join(" \u00b7 "), description: c.description || "", added: !!c.added, hasFacebook: !!c.facebook,
            company: {name: c.name, domain: c.domain, facebook: c.facebook || "", fbSource: c.fbSource || "", logo: c.logo || "", industry: c.industry || ""}};
  });
}
function searchNote(res, q){
  if (!res) return "";
  var n = (res.companies || []).length;
  if (res.error && !n) return res.error.message || "The company search is not available.";
  if (!n) return "No company matched \"" + q + "\". Try a shorter name, or type its website address.";
  return (res.error ? res.error.message + " " : "") + plural(n, "company", "companies") + " found. Pick the ones that are competitors.";
}

/* the competitors the account has, as cards, and what is known about each one's Facebook page */
function facebookText(c, finding){
  if (finding) return {text: "Looking for its Facebook page\u2026", tone: "wait"};
  if (!c.facebook) return {text: "No Facebook page yet, so its Meta ads cannot be read.", tone: "warn"};
  var how = {search: "found by searching for the company", apollo: "from the company's own record", manual: "typed by the team"}[c.fbSource];
  return {text: "Facebook page" + (how ? ", " + how : ""), tone: "ok"};
}
function competitorCards(rep, finding){
  finding = finding || {};
  return (rep.competitors || []).map(function(c){
    var fb = facebookText(c, !!finding[c.name]);
    return {name: c.name, domain: c.domain || "", logo: c.logo || "", industry: c.industry || "", facebook: c.facebook || "", fbText: fb.text, fbTone: fb.tone, finding: !!finding[c.name], canFind: !c.facebook && !finding[c.name], advertiser: c.googleAdvertiser || ""};
  });
}
function forText(rep){
  var names = Object.keys(rep.names || {}).map(function(p){ return rep.names[p]; }).join(" + ");
  return "These competitors are saved for " + (names || "this ad account") + " only. Each ad account has its own list.";
}

/* what the page sends */
function addBody(account, company, names){ return {account: account, op: "add", competitor: company, names: names || {}}; }
function removeBody(account, name, names){ return {account: account, op: "remove", competitor: {name: name}, names: names || {}}; }
function pageBody(account, comp, facebook, names){
  return {account: account, op: "update", competitor: {name: comp.name, domain: comp.domain, googleAdvertiser: comp.googleAdvertiser || "", logo: comp.logo || "", industry: comp.industry || "", facebook: String(facebook || "").trim(), fbSource: "manual"}, names: names || {}};
}
function regionBody(account, region, names){ return {account: account, op: "region", region: String(region || "").trim().toUpperCase() || "US", names: names || {}}; }
function findBody(account, name, names, force){ return {account: account, name: name, names: names || {}, force: !!force}; }

/* ── The ads themselves ────────────────────────────────────────────────── */

var SORTS = [["longest", "Running longest"], ["newest", "Newest first"]];
var FORMAT = {image: "Image", video: "Video", carousel: "Carousel", dynamic: "Dynamic", catalog: "Catalog", text: "Text ad"};
function formatLabel(f){ f = String(f || ""); return FORMAT[f] || (f ? f.charAt(0).toUpperCase() + f.slice(1) : ""); }
function ctaLabel(ad){
  if (ad.cta) return String(ad.cta);
  var t = String(ad.ctaType || "").toLowerCase().replace(/_/g, " ");
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : "";
}
/* an address that goes into a link or a picture: only https for what is drawn, http or https for a landing page, nothing with a space in it, whatever the server let through */
function safeUrl(u, secure){
  u = typeof u === "string" ? u.trim() : "";
  if (!u || /\s/.test(u)) return "";
  return (secure ? /^https:\/\//i : /^https?:\/\//i).test(u) ? u : "";
}
function str(v){ return v == null ? "" : String(v); }
/* Google gives the picture of a video creative as its YouTube thumbnail, which carries the video's id: the link that plays it (only for that exact host and an id of the right shape) */
function youtube(url){
  var m = /^https:\/\/i\.ytimg\.com\/vi\/([A-Za-z0-9_-]{11})\//.exec(url || "");
  return m ? "https://www.youtube.com/watch?v=" + m[1] : "";
}
function mediaItems(list){
  return (list || []).map(function(m){
    return {kind: m && m.kind === "video" ? "video" : "image", url: safeUrl(m && m.url, true), watch: youtube(safeUrl(m && m.url, true)), poster: safeUrl(m && m.poster, true), title: str(m && m.title), body: str(m && m.body), cta: str(m && m.cta), link: safeUrl(m && m.link)};
  }).filter(function(m){ return m.url; });
}
function adsQuery(account, f, offset, limit){
  f = f || {};
  var p = ["account=" + encodeURIComponent(account || "")];
  ["platform", "competitor", "format", "sort", "q"].forEach(function(k){ var v = str(f[k]).trim(); if (v) p.push(k + "=" + encodeURIComponent(v)); });
  if (offset) p.push("offset=" + offset);
  if (limit) p.push("limit=" + limit);
  return "?" + p.join("&");
}
/* one ad of the gallery as what a card shows */
function adCard(ad){
  var heads = ad.headlines || [], lines = ad.bodies || [], media = mediaItems(ad.media), shown = ad.mediaCount == null ? media.length : ad.mediaCount;
  return {id: ad.id, platform: ad.platform, platformLabel: PLAT[ad.platform] || str(ad.platform), competitor: str(ad.competitor), advertiser: str(ad.who), format: formatLabel(ad.format),
    title: str(heads[0]), moreHeads: heads.slice(1), lines: lines, text: str(ad.text), cta: ctaLabel(ad), caption: str(ad.caption), link: safeUrl(ad.url), landing: str(ad.landing), libraryUrl: safeUrl(ad.adUrl, true),
    media: media, extra: Math.max(0, shown - media.length), pictureOnly: !heads.length && !lines.length && !str(ad.text),
    status: ad.active === true ? "Active" : (ad.active === false ? "Stopped" : ""),
    running: ad.days == null ? "" : plural(ad.days, "day") + " running" + (ad.firstShown ? ", since " + day(ad.firstShown) : ""),
    copies: ad.copies > 1 ? "Shown " + ad.copies + " times" : "", networks: (ad.networks || []).join(", ")};
}
function countOptions(counts, label){
  var keys = Object.keys(counts || {}).sort(function(a, b){ return counts[b] - counts[a] || a.localeCompare(b); });
  return keys.map(function(k){ return [k, label(k) + " (" + counts[k] + ")"]; });
}
function filterOptions(res){
  var f = (res && res.facets) || {}, total = function(o){ return Object.keys(o || {}).reduce(function(n, k){ return n + o[k]; }, 0); };
  return {
    competitors: [["", "All competitors (" + total(f.competitors) + ")"]].concat(countOptions(f.competitors, function(k){ return k; })),
    formats: [["", "All formats"]].concat(countOptions(f.formats, formatLabel)),
    platforms: Object.keys(f.platforms || {}).length > 1 ? [["", "Google and Meta"]].concat(countOptions(f.platforms, function(k){ return PLAT[k] || k; })) : null
  };
}
function pageText(res){
  res = res || {};
  var shown = Math.min((res.offset || 0) + (res.ads || []).length, res.total || 0);
  return res.total ? "Showing " + int(shown) + " of " + plural(res.total, "ad") : "";
}
function creativeNote(res){
  var n = (res && res.creativeMissing) || 0;
  return n ? plural(n, "ad") + (n === 1 ? " was" : " were") + " read before pictures and links were kept, so " + (n === 1 ? "it shows" : "they show") + " words only. Press Refresh competitor ads to read " + (n === 1 ? "it" : "them") + " again." : "";
}
function emptyText(res){
  var f = (res && res.facets) || {}, pool = Object.keys(f.platforms || {}).reduce(function(n, k){ return n + f.platforms[k]; }, 0);
  if (!((res && res.reads) || []).length) return "No competitors are saved for this account yet.";
  if (!pool) return "No competitor ads have been read yet. Press Refresh competitor ads to read them.";
  return "No ad matches these filters.";
}

/* one line that says where the report stands */
function standing(rep){
  if (rep.status === "no_competitors") return "No competitors are saved for this account yet. Type a company name below and pick the ones that are competitors, then press Refresh competitor ads.";
  if (rep.status === "no_competitor_data") return "The competitors are saved, but none of their ads has been read yet. Press Refresh competitor ads.";
  return rep.headline;
}

/* ── CSV ───────────────────────────────────────────────────────────────── */

var HEAD = ["Section", "Platform", "Competitor", "Item", "Detail", "Figure"];
function csvRows(rep){
  var rows = [["Competitor Analysis"], [], HEAD];
  (rep.ideas || []).forEach(function(i){ rows.push(["Idea", PLAT[i.platform] || i.platform, "", i.title, i.why + " Try: " + i.tryThis + (i.example ? " Example: " + exampleText(i.example) : ""), levelInfo(i.level).label]); });
  (rep.platforms || []).forEach(function(p){
    var a = (rep.analysis || {})[p];
    if (!a) return;
    (a.compare.gaps || []).forEach(function(g){ rows.push(["Gap", PLAT[p], "", g.label, gapText({their: g.theirShare, own: g.ownShare, ads: g.ads, who: g.who}), g.points]); });
    (a.competitors || []).forEach(function(s){ rows.push(["Competitor", PLAT[p], s.name, s.error || s.skipped || "ads", s.error || s.skipped ? "" : s.active + " active, " + s.new + " new in 30 days, median " + (s.medianDays == null ? "n/a" : Math.round(s.medianDays)) + " days running" + (s.capped ? ". Limit of " + int(s.limit) + " ads reached: they run more than were read" : "") + (s.partial ? ". Stopped early: only part was read" : "") + (s.noText ? ". " + plural(s.noText, "ad") + " without text (picture or video only) not counted" : ""), s.count]); });
    (a.offers || []).forEach(function(o){ rows.push(["Offer", PLAT[p], (o.who || []).join(", "), o.text, "in " + plural(o.n, "line"), o.maxDays || 0]); });
    (a.winners || []).forEach(function(w){ rows.push(["Longest running", PLAT[p], w.who, w.headline, w.body, w.days == null ? "" : w.days]); });
  });
  return rows;
}

var api = {PLAT: PLAT, HEAD: HEAD, int: int, plural: plural, day: day, levelInfo: levelInfo, money: money, ageText: ageText, tiles: tiles, estimateText: estimateText, jobText: jobText, stepLine: stepLine,
  competitorRows: competitorRows, gapRows: gapRows, gapText: gapText, exampleText: exampleText, ideaCards: ideaCards, offerRows: offerRows, winnerRows: winnerRows, newestRows: newestRows,
  resultCards: resultCards, searchNote: searchNote, facebookText: facebookText, competitorCards: competitorCards, forText: forText, addBody: addBody, removeBody: removeBody, pageBody: pageBody, regionBody: regionBody, findBody: findBody,
  standing: standing, csvRows: csvRows, SORTS: SORTS, formatLabel: formatLabel, ctaLabel: ctaLabel, safeUrl: safeUrl, adsQuery: adsQuery, adCard: adCard, filterOptions: filterOptions, pageText: pageText, creativeNote: creativeNote, emptyText: emptyText};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.CAData = api;
})(typeof window !== "undefined" ? window : globalThis);
