/* Ad Copy Checker: the wording and the shapes the page draws, as plain functions (so tests/test_copycheck_js.py runs them in Node).
 *
 * The server reads the ads, runs the rules and writes the plan (tracker/paid_copycheck*.py). This file only decides how a status and a level read, which accounts a view shows, how a text
 * is cut into the plain parts and the parts a finding points at, and what the CSV holds. It never works out a finding the server did not send.
 */
(function(root){
"use strict";

var STATUS = {
  fix: {label: "Fix these", tone: "bad"},
  check: {label: "Small things", tone: "under"},
  clean: {label: "Clean", tone: "ok"}
};
var LEVEL = {high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
var SLOT = {headline: "Headline", longheadline: "Long headline", description: "Description", path: "Display path", primary: "Primary text", title: "Headline", link: "Description", sitelink: "Sitelink",
  sitelinkdesc: "Sitelink line", callout: "Callout", snippet: "Snippet value", asset: "Asset"};
var KIND = {rsa: "Responsive search ad", eta: "Expanded text ad", display: "Display ad", pmax: "Performance Max asset group", sitelink: "Sitelink", callout: "Callout", snippet: "Structured snippet", meta: "Meta ad"};

function statusInfo(s){ return STATUS[s] || {label: String(s), tone: "unk"}; }
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function slotText(s){ return SLOT[s] || String(s || ""); }
function kindText(k){ return KIND[k] || "Ad"; }
function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }

/* the figures the page opens with: [label, value, sub, tone] */
function tiles(report){
  var s = report.summary, c = s.counts || {};
  var fix = (s.high || 0) + (s.medium || 0);
  return [
    ["Ads read", int(s.ads), int(s.clean) + " with nothing wrong", ""],
    ["Ads to fix", int(fix), fix ? int(s.high) + " high, " + int(s.medium) + " medium" : "none has a slip that shows", fix ? "warn" : "good"],
    ["Small things", int(s.low), s.low ? "capitals, styles and words to confirm" : "nothing to tidy", ""],
    ["Findings", int(s.findings), "in " + plural(s.ads - s.clean, "ad"), ""],
    ["Accounts to fix", int(c.fix || 0), (c.check || 0) ? int(c.check) + " with small things only" : "of " + plural(s.accounts, "account") + " read", c.fix ? "warn" : "good"]
  ];
}

/* which accounts a view shows, and how many each holds */
var VIEWS = [["attention", "Needs a look"], ["all", "All"]];
function needsLook(a){ return a.status === "fix" || a.status === "check"; }
function shown(report, key){ var list = report.accounts || []; return key === "all" ? list : list.filter(needsLook); }
function viewCount(report, key){ return shown(report, key).length; }
function firstView(report){ return viewCount(report, "attention") ? "attention" : "all"; }

function query(account, refresh){ return "account=" + encodeURIComponent(account) + (refresh ? "&refresh=1" : ""); }

/* a text cut into the parts a finding points at and the plain parts between: [{t: text, f: [indexes of the findings that cover it]}]; findings that overlap an earlier one are left out of the
   marking (they are still listed) so the text always reads the same as the ad */
function segments(text, findings){
  var marks = [], last = 0, i;
  (findings || []).map(function(f, idx){ return {s: f.start, e: f.end, i: idx}; }).filter(function(m){ return m.e > m.s && m.s >= 0 && m.e <= text.length; })
    .sort(function(a, b){ return a.s - b.s || b.e - a.e; }).forEach(function(m){ if (m.s >= last) { marks.push(m); last = m.e; } });
  var out = [], at = 0;
  for (i = 0; i < marks.length; i++) {
    if (marks[i].s > at) out.push({t: text.slice(at, marks[i].s), f: []});
    out.push({t: text.slice(marks[i].s, marks[i].e), f: [marks[i].i]});
    at = marks[i].e;
  }
  if (at < text.length) out.push({t: text.slice(at), f: []});
  return out.length ? out : [{t: text, f: []}];
}

/* the findings of one text of one ad, in the order they sit */
function ofText(item, slot, text){
  return (item.findings || []).filter(function(f){ return f.slot === slot && f.text === text; });
}

/* the lines of an ad that have something wrong, in the order their worst finding comes: [{slot, text, findings}]. The server sends the findings (each knows its slot and its text) and how many lines the ad holds. */
function lines(item){
  var out = [], at = {};
  (item.findings || []).forEach(function(f){
    var k = f.slot + "\u0000" + f.text;
    if (!(k in at)) { at[k] = out.length; out.push({slot: f.slot, text: f.text, findings: []}); }
    out[at[k]].findings.push(f);
  });
  return out;
}

/* can the finding be answered with "this word is right"? Only a spelling or an unknown word can */
function canAllow(f){ return f.rule === "spelling" || f.rule === "vocab" || f.rule === "odd_caps" || f.rule === "caps_word"; }
function wordOf(f){ return String(f.excerpt || "").toLowerCase().replace(/[^a-z'-]/g, ""); }

function agePhrase(i){
  if (i.ageDays == null) return "";
  var back = i.reopened ? ", came back " + i.reopened + "x" : "";
  return (i.ageDays < 1 ? "first seen today" : "open " + plural(i.ageDays, "day")) + back;
}

/* what only shows across the ads of an account, in sentences */
function insights(report){
  var out = [];
  (report.accounts || []).forEach(function(a){
    var prefix = report.account.isGroup ? a.name + ": " : "";
    if (!a.spellingChecked) out.push({kind: "spelling", text: prefix + "Spelling was not checked: the dictionary is not installed on the server."});
    if (a.phones && a.phones.length > 1 && !a.phonesSaved) out.push({kind: "phones", text: prefix + "The ads carry " + a.phones.length + " different phone numbers (" + a.phones.slice(0, 4).map(function(p){ return p.number + " in " + plural(p.ads, "ad"); }).join(", ") + (a.phones.length > 4 ? " and more" : "") + "). Save the numbers the account should use in the settings and any other number becomes a finding."});
    if (a.vocab && a.vocab.length) out.push({kind: "vocab", text: prefix + "Words the dictionary does not know that the ads use often: " + a.vocab.slice(0, 6).map(function(v){ return v.word + " (" + plural(v.ads, "ad") + ")"; }).join(", ") + ". If they are right, add them to the allowed words."});
    (a.brandsRead || []).forEach(function(b){
      var forms = Object.keys(b.forms);
      if (forms.length > 1) out.push({kind: "brand", text: prefix + "The brand is written " + forms.length + " ways: " + forms.map(function(f){ return "\"" + f + "\" (" + b.forms[f] + ")"; }).join(", ") + "."});
    });
  });
  return out;
}

/* what Claude's grammar read says, in a sentence: how many lines it read and what it found, or why it could not */
function grammarNote(g){
  if (g.unavailable) return "Claude could not be reached (" + g.unavailable.replace(/_/g, " ") + "), so grammar was not read. The other checks stand.";
  var read = g.of > g.checked ? "the first " + int(g.checked) + " of " + int(g.of) + " lines" : plural(g.checked, "line");
  return (g.issues.length ? plural(g.issues.length, "error") + " of grammar or wording found in " : "No error of grammar or wording found in ") + read + ".";
}

/* the CSV: one line per finding, and one per account with none */
var HEAD = ["Account", "Platform", "Campaign", "Ad group", "Ad id", "Kind", "Level", "Problem", "Where", "Text", "Excerpt", "What is wrong", "Suggestion", "Open for days"];
function csvRows(report){
  var rows = [["Ad Copy Checker", report.account.name], ["Read", report.readAt || report.generatedAt || ""], [], HEAD];
  report.accounts.forEach(function(a){
    if (!a.items.length) { rows.push([a.name, a.platform === "meta" ? "Meta" : "Google Ads", "(nothing found)", "", "", "", "", "", "", "", "", "", "", ""]); return; }
    a.items.forEach(function(i){
      i.findings.forEach(function(f){
        rows.push([a.name, a.platform === "meta" ? "Meta" : "Google Ads", i.campaign, i.adGroup, i.id, kindText(i.kind), levelInfo(f.level).label, ruleLabel(report, f.rule), slotText(f.slot), f.text, f.excerpt, f.message, f.suggestion || "",
          i.ageDays == null ? "" : i.ageDays]);
      });
    });
  });
  return rows;
}
function ruleLabel(report, rule){
  var r = (report.rules || []).filter(function(x){ return x.rule === rule; })[0];
  return r ? r.label : rule;
}

var api = {STATUS: STATUS, LEVEL: LEVEL, VIEWS: VIEWS, KIND: KIND, SLOT: SLOT, statusInfo: statusInfo, levelInfo: levelInfo, slotText: slotText, kindText: kindText, int: int, plural: plural, tiles: tiles, shown: shown,
  viewCount: viewCount, firstView: firstView, needsLook: needsLook, query: query, segments: segments, lines: lines, ofText: ofText, canAllow: canAllow, wordOf: wordOf, agePhrase: agePhrase, insights: insights, grammarNote: grammarNote,
  csvRows: csvRows, ruleLabel: ruleLabel, HEAD: HEAD};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.CCData = api;
})(typeof window !== "undefined" ? window : globalThis);
