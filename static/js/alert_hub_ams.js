/* Alert Hub: the account managers tagged on Slack (GET/POST /p2/paid-marketing/api/alerthub/ams, tracker/paid_am.py). */
(function () {
  "use strict";
  var URL = "/p2/paid-marketing/api/alerthub/ams";
  var box = document.getElementById("amRows"), msg = document.getElementById("amMsg"), note = document.getElementById("amNote"), btn = document.getElementById("amSave"),
      sheet = document.getElementById("amSheet"), paste = document.getElementById("amPaste"), pasted = document.getElementById("amPasted");
  if (!box) return;
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function say(html, kind) { msg.innerHTML = html ? '<div class="' + (kind || "pm-err") + '">' + html + "</div>" : ""; }
  function paint(d) {
    var ps = d.people || [];
    sheet.value = d.sheet || "";
    pasted.hidden = !d.pasted;
    pasted.innerHTML = d.pasted ? esc(d.pasted) + ' rows pasted from the DPR tab are in use. <a href="#" id="amClear">Stop using them</a>' : "";
    note.textContent = "read from " + (d.source || "nothing yet") + ", " + d.accounts + " accounts";
    box.innerHTML = ps.length ? '<div class="mp-form">' + ps.map(function (p) {
      return '<label class="cc-f"><span>' + esc(p.name) + (p.inAllocations ? "" : " (no longer in the sheet)") + '</span><input type="text" data-name="' + esc(p.name) +
        '" value="' + esc(p.slack) + '" placeholder="U0123ABCD or name@position2.com" autocomplete="off" spellcheck="false"></label>';
    }).join("") + "</div>" : '<p class="pm-panel-s">No account managers found yet. Paste the DPR rows above and save.</p>';
    if (d.problem) say(esc(d.problem));
  }
  function call(opts) {
    return fetch(URL, Object.assign({ credentials: "same-origin" }, opts || {})).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "HTTP " + r.status); return j; });
    });
  }
  call().then(function (d) { say(""); paint(d); }).catch(function (e) { say(esc(e.message)); });
  btn.addEventListener("click", function () {
    var people = [].map.call(box.querySelectorAll("input[data-name]"), function (el) { return { name: el.getAttribute("data-name"), slack: el.value.trim() }; });
    btn.disabled = true; say("");
    call({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ people: people, sheet: sheet.value.trim(), paste: paste.value }) })
      .then(function (d) { paste.value = ""; say("Saved. The next alerts tag them.", "pm-note"); paint(d); })
      .catch(function (e) { say(esc(e.message)); })
      .then(function () { btn.disabled = false; });
  });
  pasted.addEventListener("click", function (e) {
    if (!e.target || e.target.id !== "amClear") return;
    e.preventDefault();
    call({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clearRows: true }) })
      .then(function (d) { say(""); paint(d); }).catch(function (err) { say(esc(err.message)); });
  });
})();
