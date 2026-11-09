/* Change History: the allowed addresses, the channel, the last runs and what was flagged (/p2/paid-marketing/api/changes/*, tracker/paid_changes.py). */
(function () {
  "use strict";
  var API = "/p2/paid-marketing/api/changes/";
  var $ = function (id) { return document.getElementById(id); };
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function say(html, kind) { $("msg").innerHTML = html ? '<div class="' + (kind || "pm-err") + '">' + html + "</div>" : ""; }
  function call(path, body) {
    var opts = { credentials: "same-origin" };
    if (body) { opts.method = "POST"; opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    return fetch(API + path, opts).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "HTTP " + r.status); return j; });
    });
  }
  function when(iso) { var d = new Date(iso); return isNaN(d) ? esc(iso) : esc(d.toLocaleString()); }
  function table(head, rows) {
    return '<div class="bi-tbl"><table><thead><tr>' + head.map(function (h) { return "<th>" + h + "</th>"; }).join("") + "</tr></thead><tbody>" +
      rows.join("") + "</tbody></table></div>";
  }
  function paint(d) {
    var s = d.settings || {};
    $("allow").value = (s.allow || []).join("\n");
    $("channel").value = s.channel || d.channel || "";
    $("setNote").textContent = (s.allow || []).length + " allowed" + (s.savedBy ? ", saved by " + s.savedBy : "");
    var rec = d.recent || [];
    $("recent").innerHTML = rec.length ? table(["Changed (account's time)", "Account", "Who", "What was done", "Sent"], rec.map(function (x) {
      var span = esc(x.first || "") + (x.last && x.last !== x.first ? " to " + esc(x.last.slice(x.last.slice(0, 10) === (x.first || "").slice(0, 10) ? 11 : 0)) : "");
      return "<tr><td>" + span + "</td><td>" + esc(x.accountName) + "</td><td>" + esc(x.who) + (x.via ? " (" + esc(x.via) + ")" : "") + "</td><td>" + esc(x.line) + "</td><td>" + when(x.at) + "</td></tr>";
    })) : '<p class="pm-panel-s">Nothing flagged yet.' + (d.baseline ? "" : " The first run sets the baseline and sends nothing.") + "</p>";
    var runs = d.runs || [];
    $("runs").innerHTML = runs.length ? table(["When", "How", "Accounts read", "Flagged", "Messages", "Problem"], runs.map(function (r) {
      var un = (r.unread || []).map(function (u) { return esc(u.name) + ": " + esc(u.error); }).join("; ");
      return "<tr><td>" + when(r.at) + "</td><td>" + esc(r.trigger) + (r.baseline ? " (baseline)" : "") + "</td><td>" + r.read + " of " + r.accounts + "</td><td>" +
        r.flagged + " in " + r.accountsFlagged + "</td><td>" + r.sent + "</td><td>" + esc(r.error) + (un ? (r.error ? "; " : "") + "not read: " + un : "") + "</td></tr>";
    })) : '<p class="pm-panel-s">No run yet.</p>';
    $("runNow").disabled = !!d.running;
    $("runNow").textContent = d.running ? "Running..." : "Run now";
  }
  function load() { return call("state").then(function (d) { paint(d); }).catch(function (e) { say(esc(e.message)); }); }
  $("save").addEventListener("click", function () {
    $("save").disabled = true; say("");
    call("settings", { allow: $("allow").value, channel: $("channel").value.trim() })
      .then(function () { say("Saved.", "pm-note"); return load(); })
      .catch(function (e) { say(esc(e.message)); })
      .then(function () { $("save").disabled = false; });
  });
  function result(r) {
    if (!r) return "Done.";
    if (r.error) return "Done, with a problem: " + esc(r.error);
    var un = (r.unread || []).length ? " " + r.unread.length + " could not be read." : "";
    if (r.baseline) return "Done. Read " + r.read + " of " + r.accounts + " accounts and set the baseline; nothing is sent on a first run." + un;
    return "Done. Read " + r.read + " of " + r.accounts + " accounts: " + (r.flagged ? r.flagged + " changes by someone not on the list, sent to Slack in " + r.sent + " message" + (r.sent === 1 ? "" : "s") + "." : "no new changes by anyone not on the list, so nothing was sent.") + un;
  }
  function follow(before, tries) {
    call("state").then(function (d) {
      var top = (d.runs || [])[0];
      if (!d.running && top && top.at !== before) { paint(d); say(result(top), top.error ? "pm-err" : "pm-note"); return; }
      if (tries > 120) { paint(d); say("Still running. Reload the page in a minute to see the result.", "pm-note"); return; }
      $("runNow").disabled = true; $("runNow").textContent = "Running...";
      setTimeout(function () { follow(before, tries + 1); }, 3000);
    }).catch(function (e) { say(esc(e.message)); $("runNow").disabled = false; });
  }
  $("runNow").addEventListener("click", function () {
    $("runNow").disabled = true; $("runNow").textContent = "Running..."; say("Running. This takes a minute or less.", "pm-note");
    call("state").then(function (d) {
      var before = ((d.runs || [])[0] || {}).at || "";
      return call("run", {}).then(function () { setTimeout(function () { follow(before, 0); }, 2000); });
    }).catch(function (e) { say(esc(e.message)); $("runNow").disabled = false; $("runNow").textContent = "Run now"; });
  });

  // ── Look at a day (nothing is sent) ──
  function iso(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
  var today = new Date(), oldest = new Date(); oldest.setDate(today.getDate() - 29);
  var yest = new Date(); yest.setDate(today.getDate() - 1);
  $("dayDate").max = iso(today); $("dayDate").min = iso(oldest); $("dayDate").value = iso(yest);
  call("accounts").then(function (d) {
    $("dayAcct").innerHTML = '<option value="">All Google Ads accounts</option>' + (d.accounts || []).map(function (a) {
      return '<option value="' + esc(a.id) + '">' + esc(a.name) + " (" + esc(a.id) + ")</option>";
    }).join("");
  }).catch(function () {});
  function dayPaint(d) {
    var head = "<p class=\"pm-panel-s\">" + esc(d.date) + ": read " + d.read + " of " + d.of + " account" + (d.of === 1 ? "" : "s") + ", " +
      (d.flagged ? d.flagged + " change" + (d.flagged === 1 ? "" : "s") + " by someone not on the allowed list." : "no change by anyone not on the allowed list.") +
      (d.noAllowList ? " No allowed addresses are saved, so every change counts as flagged." : "") + "</p>";
    var rows = [];
    (d.accounts || []).forEach(function (a) {
      if (!a.lines.length && a.unnamed && d.accounts.length > 1) return;
      if (!a.lines.length) { rows.push("<tr><td>" + esc(a.name) + "</td><td colspan=\"2\">No changes that day.</td><td>" + a.total + "</td></tr>"); return; }
      a.lines.forEach(function (x, i) {
        rows.push("<tr><td>" + (i ? "" : esc(a.name)) + "</td><td>" + esc(x.who) + (x.via ? " (" + esc(x.via) + ")" : "") + (x.allowed ? " <span class=\"pm-panel-s\">allowed</span>" : "") +
          "</td><td>" + esc(x.line) + "</td><td>" + (i ? "" : a.flagged + " of " + a.total) + "</td></tr>");
      });
    });
    var other = (d.accounts || []).filter(function (a) { return a.unnamed; });
    var otherHtml = other.length ? '<p class="pm-panel-s" style="margin-top:14px">Also changed that day, in Google\'s list of changed items, which does not say who and counts Google\'s own automatic updates too (policy reviews, asset ratings). Shown here only, never flagged or sent.</p>' +
      table(["Account", "What changed", "When"], other.map(function (a) {
        return "<tr><td>" + esc(a.name) + "</td><td>" + esc(a.unnamed.line) + "</td><td>" + esc(a.unnamed.first.slice(11)) + " to " + esc(a.unnamed.last.slice(11)) + "</td></tr>";
      })) : "";
    var un = (d.unread || []).map(function (u) { return esc(u.name) + ": " + esc(u.error); }).join("; ");
    $("dayOut").innerHTML = head + (rows.length ? table(["Account", "Who", "What was done", "Flagged"], rows) : "") + otherHtml + (un ? '<p class="pm-err">Not read: ' + un + "</p>" : "");
  }
  $("dayGo").addEventListener("click", function () {
    var btn = $("dayGo");
    btn.disabled = true; btn.textContent = "Reading...";
    $("dayOut").innerHTML = '<p class="pm-panel-s">Reading the change history' + ($("dayAcct").value ? "" : " of every account; this takes up to a minute") + ".</p>";
    call("day", { date: $("dayDate").value, account: $("dayAcct").value, everyone: $("dayAll").checked })
      .then(dayPaint)
      .catch(function (e) { $("dayOut").innerHTML = '<div class="pm-err">' + esc(e.message) + "</div>"; })
      .then(function () { btn.disabled = false; btn.textContent = "Show changes"; });
  });
  load();
})();
