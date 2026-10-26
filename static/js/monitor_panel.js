/* Paid Marketing: the alert panel the monitors share (Brand Spend Alert, Disapproved Ads Tracker).
 *
 *   MonitorPanel.mount(hostEl, {agent: "brandspend", label: "Brand Spend Alert"});
 *
 * It shows who is alerted (emails and a Slack channel), whether the senders are set up on the server, the baseline and the last runs, the issues that are open (with how long they have been)
 * and the ones fixed lately, and has Save, Test alert and Run now. The server does every judgement (tracker/paid_monitors.py state_for); the wording and the shapes are plain functions
 * here so tests/test_monitor_panel_js.py runs them in Node, and the panel itself is run in the pages' tests.
 */
(function(root){
"use strict";

var LEVEL = {critical: {label: "Critical", tone: "bad"}, high: {label: "High", tone: "bad"}, medium: {label: "Medium", tone: "warn"}, low: {label: "Low", tone: "wait"}, info: {label: "Info", tone: "unk"}};
function levelInfo(l){ return LEVEL[l] || {label: String(l), tone: "unk"}; }
function plural(n, one, many){ return n + " " + (n === 1 ? one : (many || one + "s")); }
function day(iso){ return iso ? String(iso).slice(0, 10) : ""; }
function when(iso){ return iso ? String(iso).slice(0, 16).replace("T", " ") + " UTC" : ""; }

/* what the senders on the server can do, in words */
function sendersText(s){
  s = s || {};
  return "Email " + (s.email ? "is set up" : "is not set up on this server") + " · Slack " + (s.slack ? "is set up" : "is not set up on this server");
}

/* who is alerted, in words */
function recipientsText(a){
  a = a || {};
  var e = (a.emails || []).length, ch = a.slackChannel;
  if (!e && !ch) return "Nobody is alerted yet: add an email address or a Slack channel.";
  return "Alerts go to " + (e ? plural(e, "email address", "email addresses") : "no email address") + " and " + (ch ? "the Slack channel " + ch : "no Slack channel") + ".";
}

/* the one line that says where the monitor stands */
function standing(state){
  var b = state.baseline, last = (state.runs || [])[0];
  if (!b && !last) return "It has not run yet. The first run sets the baseline: what is already open is marked as seen and nothing is sent, so the first alert is about what turns up after it.";
  var bits = [];
  if (b) bits.push("Baseline set " + day(b.at));
  if (last) bits.push(last.status === "failed" ? "the last run (" + when(last.finishedAt) + ") failed" : "last run " + when(last.finishedAt) + " (" + String(last.trigger || "").replace(/^page by .*/, "from the page") + ")");
  var c = state.counts || {};
  var crit = state.critical || 0, hi = [crit ? crit + " critical" : "", c.high ? c.high + " high" : ""].filter(Boolean).join(", ");
  bits.push(plural(state.openTotal || 0, "issue") + " open" + (hi ? " (" + hi + ")" : "") + ", " + plural((state.fixed || []).length, "fixed lately", "fixed lately"));
  return bits.join(" · ") + ".";
}

/* one run, in a line */
function runLine(r){
  if (r.status === "failed") return {when: when(r.finishedAt), tone: "bad", text: "Failed: " + (r.error || "no reason given")};
  var parts = ["read " + r.read + " of " + r.accounts + " accounts"];
  if (r.baseline && r.new === 0 && r.alerted === 0 && r.worse === 0 && !(r.reminders)) parts.push("nothing new");
  if (r.new) parts.push(r.new + " new");
  if (r.worse) parts.push(r.worse + " worse");
  if (r.reminders) parts.push(plural(r.reminders, "reminder"));
  if (r.resolved) parts.push(r.resolved + " fixed");
  parts.push(r.alerted ? plural(r.alerted, "issue") + " alerted" : "none alerted");
  if (r.unreadableCount) parts.push(r.unreadableCount + " not read");
  var bad = r.unreadableCount || (r.email && r.email.status === "failed") || (r.slack && r.slack.status === "failed");
  return {when: when(r.finishedAt), tone: bad ? "warn" : "ok", text: parts.join(" · ")};
}

/* the age of an issue in words */
function ageText(i){
  var t = i.ageDays == null ? "" : i.ageDays < 1 ? "since today" : plural(i.ageDays, "day");
  if (i.reopened) t += (t ? ", " : "") + "came back " + plural(i.reopened, "time");
  return t;
}

var api = {LEVEL: LEVEL, levelInfo: levelInfo, plural: plural, day: day, when: when, sendersText: sendersText, recipientsText: recipientsText, standing: standing, runLine: runLine, ageText: ageText};
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.MonitorPanel = api;

/* ── The panel itself ─────────────────────────────────────────── */
if (typeof document === "undefined" || typeof module !== "undefined") return;
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};

api.mount = function(host, opts){
  var API = "/p2/paid-marketing/api/monitors", S = {state: null, busy: "", note: "", polls: 0};
  function getJson(url, o){
    return fetch(url, Object.assign({credentials: "same-origin"}, o || {})).then(function(r){
      return r.json().catch(function(){return {};}).then(function(j){return {ok: r.ok, status: r.status, j: j};});
    });
  }
  function post(path, body){ return getJson(API + path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})}); }
  function draw(){
    var st = S.state;
    if (!st){ host.innerHTML = '<section class="pm-panel mp"><div class="pm-panel-h"><span class="pm-panel-t">Alerts and history</span></div><p class="pm-empty">' + esc(S.note || "Reading the alert settings…") + '</p></section>'; return; }
    var a = st.alerts || {};
    host.innerHTML = '<section class="pm-panel mp"><div class="pm-panel-h"><span class="pm-panel-t">Alerts and history</span><span class="pm-panel-s">' + esc(st.label) + ', checked every day</span></div>' +
      '<p class="mp-stand">' + esc(standing(st)) + '</p>' +
      '<div class="mp-form"><label>Email addresses<input type="text" id="mpEmails" value="' + esc((a.emails || []).join(", ")) + '" placeholder="name@position2.com, other@position2.com" autocomplete="off"></label>' +
      '<label>Slack channel<input type="text" id="mpSlack" value="' + esc(a.slackChannel || "") + '" placeholder="#ppc-alerts" autocomplete="off"></label>' +
      '<div class="mp-btns"><button type="button" class="pm-btn" id="mpSave"' + (S.busy ? " disabled" : "") + '>Save</button><button type="button" class="pm-btn-ghost" id="mpTest"' + (S.busy ? " disabled" : "") + '>Send a test alert</button>' +
      '<button type="button" class="pm-btn-ghost" id="mpRun"' + (S.busy || st.running ? " disabled" : "") + '>' + (st.running ? "Running…" : "Run now") + '</button></div></div>' +
      '<p class="mp-note">' + esc(recipientsText(a)) + ' ' + esc(sendersText(st.senders)) + '. A summary of everything open goes out on Mondays' + (st.digest ? " (last " + esc(st.digest) + ")" : "") + '.' + '</p>' +
      (S.note ? '<p class="mp-msg">' + esc(S.note) + '</p>' : "") + issuesHtml(st) + runsHtml(st) + '</section>';
  }
  function issuesHtml(st){
    var h = "";
    if (st.open.length){
      h += '<h4>Open issues <span class="dim">(' + st.openTotal + ')</span></h4><div class="bi-tbl"><table><thead><tr><th>Level</th><th>Account</th><th>Issue</th><th>Open for</th><th>Alerted</th></tr></thead><tbody>' + st.open.map(function(i){
        var l = levelInfo(i.level);
        return '<tr><td><span class="bi-lv ' + l.tone + '">' + esc(l.label) + '</span></td><td>' + esc(i.accountName) + (i.platform === "meta" ? ' <span class="bi-mk">Meta</span>' : "") + '</td><td>' + esc(i.title) + '</td><td>' + esc(ageText(i)) + '</td><td>' + (i.alertedAt ? esc(day(i.alertedAt)) : "not yet") + '</td></tr>';
      }).join("") + '</tbody></table></div>' + (st.openTotal > st.open.length ? '<p class="mp-note">The first ' + st.open.length + ' of ' + st.openTotal + '.</p>' : "");
    } else h += '<p class="pm-empty">Nothing is open.</p>';
    if (st.fixed.length) h += '<h4>Fixed lately</h4><div class="bi-tbl"><table><tbody>' + st.fixed.map(function(i){
      return '<tr><td>' + esc(i.accountName) + '</td><td>' + esc(i.title) + '</td><td class="r">fixed ' + esc(day(i.resolvedAt)) + ', open ' + esc(plural(i.ageDays || 0, "day")) + '</td></tr>';
    }).join("") + '</tbody></table></div>';
    return h;
  }
  function runsHtml(st){
    if (!st.runs.length) return "";
    return '<h4>Last runs</h4><ul class="mp-runs">' + st.runs.map(function(r){ var l = runLine(r); return '<li class="' + l.tone + '"><span>' + esc(l.when) + '</span> ' + esc(l.text) + '</li>'; }).join("") + '</ul>';
  }
  function load(){
    return getJson(API + "/" + opts.agent + "/state").then(function(r){
      if (!r.ok){ S.note = (r.j && r.j.error) || "The alert settings could not be read."; S.state = null; draw(); return; }
      S.state = r.j; draw();
      if (r.j.running && S.polls < 45){ S.polls++; setTimeout(load, 4000); } else S.polls = 0;
    }).catch(function(){ S.note = "The server could not be reached."; draw(); });
  }
  function act(label, fn){ S.busy = label; S.note = ""; draw(); fn().then(function(){ S.busy = ""; draw(); }); }
  host.addEventListener("click", function(e){
    var b = e.target.closest("button"); if (!b) return;
    if (b.id === "mpSave") act("save", function(){
      return post("/alerts", {emails: document.querySelector("#mpEmails").value, slackChannel: document.querySelector("#mpSlack").value}).then(function(r){
        S.note = r.ok ? "Saved." : ((r.j && r.j.error) || "Not saved."); return r.ok ? load() : null; });
    });
    if (b.id === "mpTest") act("test", function(){
      return post("/" + opts.agent + "/test-alert").then(function(r){
        if (!r.ok){ S.note = (r.j && r.j.error) || "The test alert failed."; return; }
        var e = r.j.email || {}, s = r.j.slack || {};
        S.note = r.j.sent ? "A test alert was sent (email: " + e.status + ", Slack: " + s.status + ")." : "Nothing was sent. Email: " + e.status + (e.error ? " (" + e.error + ")" : "") + ". Slack: " + s.status + (s.error ? " (" + s.error + ")" : "") + ".";
      });
    });
    if (b.id === "mpRun") act("run", function(){
      return post("/" + opts.agent + "/run").then(function(r){
        S.note = r.ok ? "The run started. It takes a minute or two; this page updates when it is done." : ((r.j && r.j.error) || "The run did not start.");
        if (r.ok) { S.polls = 0; setTimeout(load, 3000); }
      });
    });
  });
  load();
  return {reload: load};
};
})(typeof window !== "undefined" ? window : globalThis);
