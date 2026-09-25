/* Signed-in page-view tracking -> /api/track -> the "Page Views" sheet tab,
   which feeds Internal/External Usage, Public Page Analytics and Client Usage.

   Load with <script src=".../page_track.js" data-title="..." data-email="...">.
   Without data-title it reports document.title; without data-email, the
   body's data-email (the server also falls back to the session's email).

   A snapshot is sent EVERY time the page is hidden, carrying the total seconds
   the page has been VISIBLE so far: the old per-template copies sent once, on
   the first hide, so time after someone tabbed away and came back was lost.
   Each page load has one pvid; the dashboards keep its highest seq. */
(function () {
  "use strict";
  if (window.__p2pt) return;
  window.__p2pt = 1;

  var me = document.currentScript;
  var title = me && me.getAttribute("data-title");
  var email = me && me.getAttribute("data-email");

  function uuid() {
    try { if (crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  var pvid = uuid(), seq = 0, lastSent = -1;
  var visibleMs = 0, since = document.visibilityState === "hidden" ? 0 : Date.now();

  function pause() {
    if (since) { visibleMs += Date.now() - since; since = 0; }
  }

  function send() {
    var seconds = Math.round(visibleMs / 1000);
    // "hidden" then "pagehide" fire back to back on navigation with no
    // visible time in between; the second would be an identical snapshot.
    if (seconds === lastSent) return;
    lastSent = seconds;
    seq += 1;
    try {
      fetch("/api/track", {
        method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
        body: JSON.stringify({
          page: location.pathname,
          title: title != null ? title : document.title,
          seconds: seconds,
          email: email != null ? email : ((document.body && document.body.dataset.email) || ""),
          pvid: pvid, seq: seq
        })
      }).catch(function () {});
    } catch (e) {}
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") { pause(); send(); }
    else if (!since) since = Date.now();
  });
  addEventListener("pagehide", function () { pause(); send(); });
})();
