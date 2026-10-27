/* Alert Hub: the wording and the shapes the page draws, as plain functions (so tests/test_alert_hub_js.py runs them in Node).
 *
 * The server decides everything (tracker/paid_alerthub.py): which issue is P1, P2 or P3, which one another issue covers, where each client's alerts go and what was sent. This file only words it,
 * filters the list of open issues, turns a form into what the server reads and says what the rules are.
 */
(function(root){
"use strict";

var TONE = {P1: "bad", P2: "warn", P3: "wait"};
/* what the rules' (monitor.kind) pairs mean, in words */
var KIND = {
  "watch.account_status": "an account that is not serving", "watch.account_gone": "an account that is gone",
  "spend.drop": "a drop in spend", "track.no_conv": "conversions stopped", "track.conv_drop": "conversions dropped",
  "disapproved.stopped": "an ad group that cannot show an ad", "kpi.not_delivering": "a campaign not delivering", "kpi.no_conv": "a campaign that stopped converting"
};
var FAMILY = {not_delivering: "cannot show or deliver", conv_stopped: "stopped converting"};

function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }
function day(iso){ return iso ? String(iso).slice(0, 10) : ""; }
function when(iso){ return iso ? String(iso).slice(0, 16).replace("T", " ") + " UTC" : ""; }
function kindText(key){ return KIND[key] || key; }
function orText(a){ return a.length <= 1 ? (a[0] || "") : a.slice(0, -1).join(", ") + " or " + a[a.length - 1]; }
function listText(a){ return a.length <= 1 ? (a[0] || "") : a.slice(0, -1).join(", ") + " and " + a[a.length - 1]; }

function floorText(f){ return f === "P1" ? "P1 only" : "P1 and P2"; }

/* the tiles above the page */
function tiles(state){
  var c = state.counts || {P1: 0, P2: 0, P3: 0}, held = state.held || 0, rows = destinations(state), set = rows.filter(function(r){ return r.ready; }).length;
  var own = (state.clients || []).filter(function(x){ return x.route && (x.route.slackChannel || (x.route.emails || []).length); }).length;
  return [
    ["P1 open", int(c.P1), c.P1 ? "alerted as they turn up, and again after a week" : "none open", c.P1 ? "bad" : "good"],
    ["P2 open", int(c.P2), c.P2 ? "alerted as they turn up" : "none open", c.P2 ? "warn" : "good"],
    ["P3 open", int(c.P3), "on the pages and in the Monday summary, never one by one", ""],
    ["Held back", int(held), held ? "another monitor's issue already says it" : "no issue is covered by another", ""],
    ["Destinations", int(set), "the default" + ((state.clients || []).length ? " and " + plural(own, "client") + " with a channel of their own" : ""), set ? "good" : "warn"]
  ];
}

/* the default destination first, then one row per client of the Master Agent */
function destinations(state){
  var d = state.default || {}, rows = [{key: "default", name: "Default", accounts: null, slack: d.slackChannel || "", emails: d.emails || [], floor: "P2", own: true, ready: !!(d.slackChannel || (d.emails || []).length)}];
  (state.clients || []).forEach(function(c){
    var r = c.route || {}, own = !!(r.slackChannel || (r.emails || []).length);
    rows.push({key: c.id, name: c.name, accounts: c.accounts, slack: r.slackChannel || "", emails: r.emails || [], floor: r.floor || "P2", own: own, ready: own || rows[0].ready});
  });
  return rows;
}

/* where a destination sends, in words */
function destText(r, dflt){
  if (!r.own) return "Uses the default destination" + (dflt && (dflt.slackChannel || (dflt.emails || []).length) ? "" : " (none set)") + ", " + floorText(r.floor);
  var bits = [];
  if (r.slack) bits.push("Slack " + r.slack);
  if ((r.emails || []).length) bits.push(plural(r.emails.length, "email"));
  return bits.join(" and ") + ", " + floorText(r.floor);
}

/* what the page sends to save a client's destination */
function routeBody(clientId, slack, emails, floor){
  return {clientId: clientId, slackChannel: String(slack || "").trim(), emails: String(emails || ""), floor: floor === "P1" ? "P1" : "P2"};
}
function emailsText(a){ return (a || []).join(", "); }

/* ── The open issues ────────────────────────────────────────────────────── */

/* rows that pass the three filters ("all" lets everything through; client "none" is an account in no client) */
function filterOpen(rows, f){
  f = f || {};
  return (rows || []).filter(function(r){
    if (f.p && f.p !== "all" && r.p !== f.p) return false;
    if (f.client && f.client !== "all" && (f.client === "none" ? r.clientId : r.clientId !== f.client)) return false;
    if (f.agent && f.agent !== "all" && r.agent !== f.agent) return false;
    if (f.held === "hide" && r.coveredBy) return false;
    return true;
  });
}
/* the choices of the client and monitor filters, with how many rows each has */
function facets(rows){
  var clients = {}, agents = {}, none = 0;
  (rows || []).forEach(function(r){
    if (r.clientId) clients[r.clientId] = {id: r.clientId, name: r.clientName, count: ((clients[r.clientId] || {}).count || 0) + 1}; else none++;
    agents[r.agent] = {id: r.agent, name: r.agentLabel, count: ((agents[r.agent] || {}).count || 0) + 1};
  });
  var byName = function(a, b){ return a.name.toLowerCase().localeCompare(b.name.toLowerCase()); };
  return {clients: Object.keys(clients).map(function(k){ return clients[k]; }).sort(byName).concat(none ? [{id: "none", name: "No client", count: none}] : []), agents: Object.keys(agents).map(function(k){ return agents[k]; }).sort(byName)};
}
function ageText(r){
  if (r.ageDays == null) return "";
  return r.ageDays < 1 ? "since today" : plural(r.ageDays, "day");
}
function alertedText(r){
  if (r.coveredBy) return "held back";
  return r.alertedAt ? day(r.alertedAt) : (r.p === "P3" ? "not alerted (P3)" : "not yet");
}
function coveredText(r){
  return r.coveredBy ? r.coveredBy.why + " (" + r.coveredBy.agentLabel + ": " + r.coveredBy.title + ")" : "";
}

/* ── What was sent ─────────────────────────────────────────────────────── */

function sendStatus(s){
  if (s.sent) return "sent";
  var sl = (s.slack || {}), em = (s.email || {});
  if (sl.status === "not_configured" && em.status === "not_configured") return "not sent: no Slack channel or email is set";
  var why = sl.status === "failed" ? sl.error : em.status === "failed" ? em.error : "";
  return "not delivered" + (why ? ": " + why : "") + ", tried again at the next run";
}
function sendLine(s){
  var where = (s.name || "Default") + " (" + [s.channel, s.emails ? plural(s.emails, "email") : ""].filter(Boolean).join(", ") + ")";
  var n = [s.p1 ? s.p1 + " P1" : "", s.p2 ? s.p2 + " P2" : ""].filter(Boolean).join(", ");
  return {text: where + ": " + plural(s.issues, "issue") + (n ? " (" + n + ")" : "") + ", " + sendStatus(s), tone: s.sent ? "ok" : "bad"};
}
function logLine(e){
  var sends = e.sends || [], ok = sends.filter(function(s){ return s.sent; }).length, bits = [];
  bits.push(e.agents && e.agents.length > 3 ? e.agents.length + " monitors" : listText(e.agents || []));
  if (sends.length) bits.push(ok === sends.length ? "sent to " + plural(ok, "destination") : ok + " of " + plural(sends.length, "destination") + " took it");
  else bits.push("nothing sent");
  if (e.held) bits.push(e.held + " held back");
  if (e.belowFloor) bits.push(e.belowFloor + " under a floor");
  return {when: when(e.at), trigger: String(e.trigger || "").replace(/^page by .*/, "from the page"), text: bits.join(" · "), tone: sends.some(function(s){ return !s.sent; }) ? "warn" : "ok", sends: sends.map(sendLine)};
}

/* ── The rules, in words ───────────────────────────────────────────────── */

function ruleLines(state){
  var r = state.rules || {}, label = {};
  (state.agents || []).forEach(function(a){ label[a.agent] = a.label; });
  var pair = function(k){ var i = k.indexOf("."); return {agent: k.slice(0, i), text: kindText(k)}; };
  var lines = [];
  var symptoms = Object.keys(r.symptoms || {}).map(function(a){
    var kinds = r.symptoms[a];
    return (label[a] || a) + " (" + (kinds ? listText(kinds.map(function(k){ return kindText(a + "." + k); })) : "every finding") + ")";
  });
  var byAgent = {};
  (r.root || []).forEach(function(k){ (byAgent[pair(k).agent] = byAgent[pair(k).agent] || []).push(pair(k).text); });
  var roots = Object.keys(byAgent).map(function(a){ return (label[a] || a) + " finds " + orText(byAgent[a]); });
  if (roots.length) lines.push("When " + orText(roots) + ", what follows from it is held back while it is open: " + listText(symptoms) + ".");
  Object.keys(r.families || {}).forEach(function(f){
    lines.push("Two monitors saying the same thing about one account (" + (FAMILY[f] || f) + ") are said once, by the first of: " + r.families[f].map(function(k){ return (label[pair(k).agent] || pair(k).agent) + ", " + pair(k).text; }).join("; then ") + ".");
  });
  lines.push("An issue is only held back by one of the same or a higher level, so a P1 is never hidden behind a P2. A held-back issue is not marked as alerted: it stays open on its own page, and when the issue that covers it is fixed it is alerted at the next run.");
  return lines;
}

/* ── CSV ───────────────────────────────────────────────────────────────── */

var HEAD = ["Severity", "Client", "Account", "Platform", "Monitor", "Issue", "Open (days)", "Alerted", "Held back by"];
function csvRows(state){
  var rows = [["Alert Hub"], [], HEAD];
  (state.open || []).forEach(function(r){
    rows.push([r.p, r.clientName || "(no client)", r.accountName, r.platform === "meta" ? "Meta" : "Google Ads", r.agentLabel, r.title, r.ageDays == null ? "" : r.ageDays, r.alertedAt ? day(r.alertedAt) : "", r.coveredBy ? r.coveredBy.agentLabel + ": " + r.coveredBy.title : ""]);
  });
  return rows;
}

var api = {TONE: TONE, KIND: KIND, HEAD: HEAD, int: int, plural: plural, day: day, when: when, kindText: kindText, floorText: floorText, tiles: tiles, destinations: destinations, destText: destText, routeBody: routeBody,
  emailsText: emailsText, filterOpen: filterOpen, facets: facets, ageText: ageText, alertedText: alertedText, coveredText: coveredText, sendStatus: sendStatus, sendLine: sendLine, logLine: logLine,
  ruleLines: ruleLines, csvRows: csvRows};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.AHData = api;
})(typeof window !== "undefined" ? window : globalThis);
