/* Master Agent: the wording and the shapes the page draws, as plain functions (so tests/test_master_agent_js.py runs them in Node).
 *
 * The server keeps and checks the clients (tracker/paid_master.py). This file only decides how a field is named, which agents read it, what a client reads like in a list, which accounts have no
 * client, what a form turns into when it is saved, and how a difference between the master and an agent's own value is worded. It never decides what is valid: the server says so.
 */
(function(root){
"use strict";

var SHARED = [
  {key: "phones", label: "Phone numbers", hint: "617-555-0100, one per line", kind: "lines"},
  {key: "locations", label: "Places the client serves", hint: "Quincy, MA, one per line", kind: "lines"},
  {key: "areas", label: "Regions people say (not places Google targets)", hint: "Greater Boston, one per line", kind: "lines"},
  {key: "brandTerms", label: "Brand terms (words searches for the brand contain)", hint: "acme dental, acme", kind: "csv"},
  {key: "writtenAs", label: "How the name is written in copy", hint: "AcmeDental, one per line", kind: "lines"},
  {key: "allowWords", label: "Words that are right (the dictionary does not know them)", hint: "invisalign, zoomwhitening", kind: "csv"},
  {key: "domains", label: "Domains its ads may send people to", hint: "acmedental.com, one per line", kind: "lines"},
  {key: "competitors", label: "Competitors (name | website | Facebook page | Google advertiser id, one per line)", hint: "Tandem Diabetes | tandemdiabetes.com | TandemDiabetes", kind: "competitors"}
];
var TARGETS = [["cpa", "Cost per conversion"], ["roas", "Return on ad spend (4 is 400%)"], ["cvr", "Lowest conversion rate (%)"], ["ctr", "Lowest click-through rate (%)"]];
var BUDGETS = [["monthly", "Monthly budget"], ["weeklyMin", "Weekly minimum"], ["weeklyMax", "Weekly maximum"]];
var LABEL = {"budget.monthly": "Monthly budget", "budget.weeklyMin": "Weekly minimum", "budget.weeklyMax": "Weekly maximum", "targets.cpa": "Cost per conversion", "targets.roas": "Return on ad spend",
  "targets.cvr": "Lowest conversion rate", "targets.ctr": "Lowest click-through rate", tolerance: "Tolerance", phones: "Phone numbers", locations: "Places", areas: "Regions", brandTerms: "Brand terms",
  writtenAs: "Written name", allowWords: "Allowed words", domains: "Domains", competitors: "Competitors", offers: "Offers", notes: "Notes"};

function int(v){ return v == null ? "n/a" : Math.round(v).toLocaleString("en-US"); }
function plural(n, one, many){ return int(n) + " " + (n === 1 ? one : (many || one + "s")); }
function fieldLabel(key){ return LABEL[key] || String(key); }

/* "Read by Ad Copy Checker and Location Conflicts", or the honest "Not read by an agent yet" */
function usedBy(key, map){
  var list = (map || {})[key];
  if (!list || !list.length) return "Not read by an agent yet";
  return "Read by " + (list.length === 1 ? list[0] : list.slice(0, -1).join(", ") + " and " + list[list.length - 1]);
}

function valueText(v){
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "number") return v.toLocaleString("en-US", {maximumFractionDigits: 4});
  return v == null ? "" : String(v);
}

/* a number as it is typed into a box or written in a CSV: no thousands separator (the server reads "12,000" too, but a spreadsheet does not) */
function plain(v){
  if (Array.isArray(v)) return v.map(function(x){ return x && typeof x === "object" ? x.name : x; }).join(", ");
  return v == null ? "" : String(v);
}

/* which of a client's fields hold something: [filled, total]; the budget and targets count once per account */
function filled(c){
  var sh = c.shared || {}, n = 0, total = SHARED.length + 2;
  SHARED.forEach(function(f){ if ((sh[f.key] || []).length) n++; });
  if ((sh.offers || []).length) n++;
  if (sh.notes) n++;
  return [n, total];
}
function clientSummary(c){
  var accts = Object.keys(c.accounts || {}).length, f = filled(c);
  return plural(accts, "account") + ", " + f[0] + " of " + f[1] + " shared fields filled";
}

function ownersOf(state){
  var out = {};
  (state.clients || []).forEach(function(c){ Object.keys(c.accounts || {}).forEach(function(id){ out[id] = c.name; }); });
  return out;
}
/* the accounts no client owns yet, Google first, by name */
function unassigned(state, accounts){
  var own = ownersOf(state);
  return (accounts || []).filter(function(a){ return !own[a.id]; }).sort(function(a, b){
    return (a.platform === b.platform ? 0 : a.platform === "meta" ? 1 : -1) || String(a.name).toLowerCase().localeCompare(String(b.name).toLowerCase());
  });
}

function tiles(state, accounts){
  var own = ownersOf(state), covered = (accounts || []).filter(function(a){ return own[a.id]; }).length, total = (accounts || []).length, drift = (state.drift || []).length;
  return [
    ["Clients", int((state.clients || []).length), (state.clients || []).length ? "written down once" : "none yet: add the first", ""],
    ["Accounts covered", int(covered), "of " + plural(total, "account") + " the platform lists", total && covered === total ? "good" : ""],
    ["Without a client", int(total - covered), total - covered ? "no master figures for these yet" : "every account has a client", total - covered ? "warn" : "good"],
    ["Settings that differ", int(drift), drift ? "an agent's own saved value wins: settle them" : "no agent disagrees with the master", drift ? "warn" : "good"]
  ];
}

/* a blank form, or one made from a saved client: text for the boxes, numbers as typed */
function formOf(c){
  c = c || {};
  var sh = c.shared || {}, f = {id: c.id || "", name: c.name || "", accounts: {}, shared: {}, notes: sh.notes || ""};
  Object.keys(c.accounts || {}).forEach(function(id){
    var a = c.accounts[id] || {}, b = a.budget || {}, t = a.targets || {};
    f.accounts[id] = {monthly: plain(b.monthly), weeklyMin: plain(b.weeklyMin), weeklyMax: plain(b.weeklyMax), cpa: plain(t.cpa), roas: plain(t.roas), cvr: plain(t.cvr),
      ctr: plain(t.ctr), tolerance: plain(a.tolerance)};
  });
  SHARED.forEach(function(s){ f.shared[s.key] = s.kind === "competitors" ? competitorLines(sh[s.key]) : (sh[s.key] || []).join(s.kind === "csv" ? ", " : "\n"); });
  f.offers = (sh.offers || []).map(function(o){ return o.text + (o.ends ? " | " + o.ends : ""); }).join("\n");
  return f;
}

/* the competitors as the box holds them: "Name | website | Facebook page | advertiser id" per line (the last three left off when empty) */
function competitorLines(list){
  return (list || []).map(function(c){ return [c.name, c.domain || "", c.facebook || "", c.googleAdvertiser || ""].join(" | ").replace(/( \| )+$/, ""); }).join("\n");
}

/* "Free whitening | 2026-12-31" per line into [{text, ends}] */
function offersOf(text){
  return String(text || "").split("\n").map(function(l){ return l.trim(); }).filter(Boolean).map(function(l){
    var i = l.lastIndexOf("|");
    return i < 0 ? {text: l, ends: ""} : {text: l.slice(0, i).trim(), ends: l.slice(i + 1).trim()};
  });
}

/* what the page sends: the form as the server's clean_client reads it (the server checks everything) */
function payload(form, selected){
  var accounts = (selected || []).map(function(id){
    var a = form.accounts[id] || {};
    return {id: id, budget: {monthly: a.monthly || "", weeklyMin: a.weeklyMin || "", weeklyMax: a.weeklyMax || ""}, targets: {cpa: a.cpa || "", roas: a.roas || "", cvr: a.cvr || "", ctr: a.ctr || ""},
      tolerance: a.tolerance || ""};
  });
  var shared = {};
  SHARED.forEach(function(s){ shared[s.key] = form.shared[s.key] || ""; });
  shared.offers = offersOf(form.offers);
  shared.notes = form.notes || "";
  return {name: form.name, accounts: accounts, shared: shared};
}

/* a difference between the master and what an agent saved for itself, as a sentence */
function driftText(d){
  return d.agent + " has " + valueText(d.saved) + " for " + fieldLabel(d.field).toLowerCase() + " and the master says " + valueText(d.master) + ". The agent's own wins.";
}
function driftRows(state, nameOf){
  return (state.drift || []).map(function(d){ return {account: (nameOf && nameOf[d.account]) || d.account, client: d.client, agent: d.agent, field: fieldLabel(d.field), master: valueText(d.master), saved: valueText(d.saved)}; });
}

var HEAD = ["Client", "Account", "Platform", "Field", "Value"];
function csvRows(state, nameOf){
  var rows = [["Master Agent"], [], HEAD];
  (state.clients || []).forEach(function(c){
    Object.keys(c.accounts || {}).forEach(function(id){
      var a = c.accounts[id], plat = /^act_/.test(id) ? "Meta" : "Google Ads", name = (nameOf && nameOf[id]) || id;
      Object.keys(a.budget || {}).forEach(function(k){ rows.push([c.name, name, plat, fieldLabel("budget." + k), plain(a.budget[k])]); });
      Object.keys(a.targets || {}).forEach(function(k){ rows.push([c.name, name, plat, fieldLabel("targets." + k), plain(a.targets[k])]); });
      if (a.tolerance) rows.push([c.name, name, plat, fieldLabel("tolerance"), plain(a.tolerance)]);
    });
    var sh = c.shared || {};
    SHARED.forEach(function(s){ if ((sh[s.key] || []).length) rows.push([c.name, "(whole client)", "", fieldLabel(s.key), plain(sh[s.key])]); });
    (sh.offers || []).forEach(function(o){ rows.push([c.name, "(whole client)", "", "Offer", o.text + (o.ends ? " (ends " + o.ends + ")" : "")]); });
  });
  return rows;
}

var api = {SHARED: SHARED, TARGETS: TARGETS, BUDGETS: BUDGETS, HEAD: HEAD, int: int, plural: plural, fieldLabel: fieldLabel, usedBy: usedBy, valueText: valueText, competitorLines: competitorLines, filled: filled, clientSummary: clientSummary,
  ownersOf: ownersOf, unassigned: unassigned, tiles: tiles, formOf: formOf, offersOf: offersOf, payload: payload, driftText: driftText, driftRows: driftRows, csvRows: csvRows};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.MAData = api;
})(typeof window !== "undefined" ? window : globalThis);
