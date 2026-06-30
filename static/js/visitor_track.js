/* ════════════════════════════════════════════════════════════════
   Intelligence by Position² — first-party visitor analytics (public pages)
   Captures anonymous, pre-login visitor behaviour and beacons one rich
   row per page view to /api/atrack. No third-party scripts. Honors DNT.
   ════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.__P2VT) return;
  window.__P2VT = 1;

  try {
    if (navigator.doNotTrack === "1" || window.doNotTrack === "1" ||
        navigator.globalPrivacyControl === true) return;
  } catch (e) {}

  var ENDPOINT = window.__P2_ATRACK__ || "/api/atrack";

  function uuid() {
    try { if (crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
  function ls(k, v) {
    try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); }
    catch (e) { return null; }
  }
  function ss(k, v) {
    try { if (v === undefined) return sessionStorage.getItem(k); sessionStorage.setItem(k, v); }
    catch (e) { return null; }
  }
  function host(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return ""; } }

  var vid = ls("p2_vid"), isNew = false;
  if (!vid) { vid = uuid(); ls("p2_vid", vid); isNew = true; }

  var sid = ss("p2_sid");
  var qp = new URLSearchParams(location.search);
  function qpv(k) { return qp.get(k) || ""; }
  if (!sid) {
    sid = uuid(); ss("p2_sid", sid);
    ss("p2_sstart", Date.now() + "");
    ss("p2_landing", location.pathname);
    ss("p2_ref", document.referrer || "");
    ss("p2_utm", JSON.stringify({
      source: qpv("utm_source"), medium: qpv("utm_medium"),
      campaign: qpv("utm_campaign"), term: qpv("utm_term"), content: qpv("utm_content")
    }));
  }
  var pages = (parseInt(ss("p2_pages"), 10) || 0) + 1;
  ss("p2_pages", pages + "");
  var utm = {}; try { utm = JSON.parse(ss("p2_utm") || "{}"); } catch (e) {}

  var t0 = Date.now();
  var engaged = 0, lastActive = Date.now(), maxScroll = 0, clicks = 0, rage = 0;
  var lastClick = { t: 0, x: 0, y: 0 };
  var events = [];
  var cta = {};
  var searchTerms = {};
  var formStage = "";
  var video = "";
  var cwv = { lcp: 0, cls: 0, inp: 0 };

  function logEvent(type, label, extra) {
    if (events.length < 80) {
      var o = { t: type, l: label, s: Math.round((Date.now() - t0) / 1000) };
      if (extra) o.x = extra;
      events.push(o);
    }
  }
  function bumpCta(label) { cta[label] = (cta[label] || 0) + 1; logEvent("cta", label); }
  function stage(s) {
    var order = { open: 1, started: 2, submitted: 3 };
    if ((order[s] || 0) > (order[formStage] || 0)) formStage = s;
  }

  ["mousemove", "keydown", "scroll", "pointerdown", "touchstart"].forEach(function (ev) {
    addEventListener(ev, function () { lastActive = Date.now(); }, { passive: true });
  });
  setInterval(function () {
    if (document.visibilityState === "visible" && (Date.now() - lastActive) < 15000) engaged++;
  }, 1000);

  addEventListener("scroll", function () {
    var d = document.documentElement, b = document.body;
    var sh = Math.max(d.scrollHeight, b.scrollHeight) - innerHeight;
    if (sh <= 0) { maxScroll = 100; return; }
    var p = Math.round((scrollY / sh) * 100);
    if (p > maxScroll) maxScroll = Math.min(100, p);
  }, { passive: true });

  addEventListener("click", function (e) {
    clicks++;
    var now = Date.now();
    if (now - lastClick.t < 800 &&
        Math.abs(e.clientX - lastClick.x) < 32 && Math.abs(e.clientY - lastClick.y) < 32) {
      rage++; logEvent("rage", location.pathname);
    }
    lastClick = { t: now, x: e.clientX, y: e.clientY };

    var t = e.target;
    if (t.closest("[data-video]")) { video = video || "opened"; bumpCta("watch_walkthrough"); return; }
    var demo = t.closest("[data-demo]");
    if (demo) { stage("open"); bumpCta("request_access:" + (demo.getAttribute("data-interest") || "Request access")); return; }
    if (t.closest(".g_id_signin, [data-signin], a[href='/login']")) { bumpCta("sign_in"); return; }
    var card = t.closest(".acard");
    if (card) { bumpCta("agent_card:" + ((card.getAttribute("data-name") || "").slice(0, 40))); return; }
    var a = t.closest("a[href]");
    if (a) {
      var href = a.getAttribute("href") || "";
      if (/^https?:\/\//i.test(href) && host(href) && host(href) !== location.hostname.replace(/^www\./, "")) {
        bumpCta("outbound:" + host(href));
      } else if (a.closest("nav")) {
        logEvent("nav", href);
      }
    }
  }, true);

  ["#dirSearch", "#sigSearch", "input[type=search]"].forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (el) {
      var tmr;
      el.addEventListener("input", function () {
        clearTimeout(tmr);
        tmr = setTimeout(function () {
          var v = (el.value || "").trim().toLowerCase();
          if (v.length >= 2) { searchTerms[v] = 1; logEvent("search", v.slice(0, 40)); }
        }, 600);
      });
    });
  });

  document.addEventListener("focusin", function (e) {
    if (e.target.closest("#nvfov, form, [data-demo-form]")) stage("started");
  });
  document.addEventListener("p2:lead_submit", function () { stage("submitted"); logEvent("conversion", "lead_submit"); });

  try {
    new PerformanceObserver(function (l) {
      var es = l.getEntries(); cwv.lcp = Math.round(es[es.length - 1].startTime);
    }).observe({ type: "largest-contentful-paint", buffered: true });
  } catch (e) {}
  try {
    new PerformanceObserver(function (l) {
      l.getEntries().forEach(function (en) { if (!en.hadRecentInput) cwv.cls += en.value; });
    }).observe({ type: "layout-shift", buffered: true });
  } catch (e) {}
  try {
    new PerformanceObserver(function (l) {
      l.getEntries().forEach(function (en) {
        var d = en.duration || 0; if (d > cwv.inp) cwv.inp = Math.round(d);
      });
    }).observe({ type: "event", buffered: true, durationThreshold: 40 });
  } catch (e) {}

  var sent = false;
  function payload() {
    return {
      vid: vid, sid: sid, isNew: isNew,
      page: location.pathname, title: document.title, ref: ss("p2_ref") || "",
      utm: utm, landing: ss("p2_landing") || location.pathname, pagesInSession: pages,
      tOnPage: Math.round((Date.now() - t0) / 1000), engaged: engaged, scroll: maxScroll,
      clicks: clicks, cta: cta, video: video, form: formStage,
      search: Object.keys(searchTerms).join(" | "), rage: rage,
      lcp: cwv.lcp, cls: Math.round(cwv.cls * 1000) / 1000, inp: cwv.inp,
      vw: innerWidth, vh: innerHeight, sw: screen.width, sh: screen.height,
      lang: navigator.language || "", events: events
    };
  }
  function send() {
    if (sent) return; sent = true;
    var body = JSON.stringify(payload());
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "text/plain;charset=UTF-8" }));
        return;
      }
    } catch (e) {}
    try { fetch(ENDPOINT, { method: "POST", body: body, headers: { "Content-Type": "text/plain" }, keepalive: true }).catch(function () {}); } catch (e) {}
  }
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") send(); });
  addEventListener("pagehide", send);
})();
