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
    $("recent").innerHTML = rec.length ? table(["Sent", "Account", "Who", "What was done"], rec.map(function (x) {
      return "<tr><td>" + when(x.at) + "</td><td>" + esc(x.accountName) + "</td><td>" + esc(x.who) + (x.via ? " (" + esc(x.via) + ")" : "") + "</td><td>" + esc(x.line) + "</td></tr>";
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
  $("runNow").addEventListener("click", function () {
    $("runNow").disabled = true; say("");
    call("run", {}).then(function () { say("Started. It takes a few minutes; this page refreshes the list.", "pm-note"); setTimeout(load, 60000); })
      .catch(function (e) { say(esc(e.message)); $("runNow").disabled = false; });
  });
  load();
})();
