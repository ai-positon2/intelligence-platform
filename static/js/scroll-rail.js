/* Scroll rail: a hairline indicator and scrubber for pages whose native
   scrollbar is hidden (gtm.css zeroes it globally).
   Styling lives in static/css/scroll-rail.css; this file only does geometry. */
(function () {
  'use strict';
  if (window.__p2Rail) return;
  window.__p2Rail = 1;

  var MIN_THUMB = 44;   // px. Below this there is nothing a hand can catch.
  var MIN_RAIL = 60;    // px. Shorter than this and the rail is not worth drawing.
  var IDLE_MS = 1100;

  var doc = document.scrollingElement || document.documentElement;
  var reduced = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

  var rail, thumb;
  var idleTimer = null, frame = 0, dragging = null;
  // Kept rather than read back off the element: the drag needs to know where
  // the thumb currently sits, and parsing that out of a computed transform is
  // a round trip through a string for a number we already had.
  var geo = { thumbH: 0, travel: 0, max: 0, y: 0 };

  function build() {
    rail = document.createElement('div');
    rail.className = 'p2-rail';
    // A visual affordance over scrolling that already works from the wheel and
    // the keyboard. Exposing it would add a tab stop offering nothing the
    // arrow keys do not already do.
    rail.setAttribute('aria-hidden', 'true');

    thumb = document.createElement('div');
    thumb.className = 'p2-rail-thumb';
    rail.appendChild(thumb);
    document.body.appendChild(rail);

    thumb.addEventListener('pointerdown', onGrab);
    rail.addEventListener('pointerdown', onTrack);
    rail.addEventListener('pointerenter', wake);
  }

  function measure() {
    var railH = rail.clientHeight;
    var scrollH = doc.scrollHeight, clientH = doc.clientHeight;
    geo.max = scrollH - clientH;
    if (railH < MIN_RAIL || geo.max <= 8) { geo.thumbH = geo.travel = 0; return false; }
    var h = Math.round(railH * (clientH / scrollH));
    geo.thumbH = Math.min(railH, Math.max(MIN_THUMB, h));
    geo.travel = railH - geo.thumbH;
    return true;
  }

  function draw() {
    frame = 0;
    // A page that fits on the screen has nothing to indicate, and a rail on it
    // is furniture. Same for a viewport too short to draw one in.
    if (!measure()) { rail.classList.remove('on'); return; }
    rail.classList.add('on');
    var t = geo.max ? clamp(doc.scrollTop / geo.max, 0, 1) : 0;
    geo.y = Math.round(t * geo.travel);
    thumb.style.setProperty('--h', geo.thumbH + 'px');
    thumb.style.setProperty('--y', geo.y + 'px');
  }

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }
  function schedule() { if (!frame) frame = requestAnimationFrame(draw); }

  function wake() {
    if (!rail) return;
    rail.classList.remove('idle');
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(function () {
      // Never fade out from under a hand that is on it.
      if (dragging) return;
      var hovered = false;
      try { hovered = rail.matches(':hover'); } catch (e) {}
      if (!hovered) rail.classList.add('idle');
    }, IDLE_MS);
  }

  function goTo(t, smooth) {
    window.scrollTo({
      top: clamp(t, 0, 1) * geo.max,
      behavior: (smooth && !(reduced && reduced.matches)) ? 'smooth' : 'auto'
    });
  }

  function onGrab(e) {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();          // the track handler must not also fire
    if (!measure()) return;
    // Grab offset, so the thumb does not jump to centre itself under the
    // cursor the instant it is picked up.
    dragging = { offset: e.clientY - rail.getBoundingClientRect().top - geo.y };
    rail.classList.add('drag');
    try { thumb.setPointerCapture(e.pointerId); } catch (_) {}
    thumb.addEventListener('pointermove', onDrag);
    thumb.addEventListener('pointerup', onDrop);
    thumb.addEventListener('pointercancel', onDrop);
    wake();
  }

  function onDrag(e) {
    if (!dragging || !geo.travel) return;
    var y = e.clientY - rail.getBoundingClientRect().top - dragging.offset;
    goTo(y / geo.travel, false);
    wake();
  }

  function onDrop(e) {
    dragging = null;
    rail.classList.remove('drag');
    try { thumb.releasePointerCapture(e.pointerId); } catch (_) {}
    thumb.removeEventListener('pointermove', onDrag);
    thumb.removeEventListener('pointerup', onDrop);
    thumb.removeEventListener('pointercancel', onDrop);
    wake();
  }

  // Clicking the bare track puts the thumb where you clicked rather than
  // paging by one screen, which is what the gesture looks like it means.
  function onTrack(e) {
    if (e.target === thumb || !measure() || !geo.travel) return;
    var y = e.clientY - rail.getBoundingClientRect().top - geo.thumbH / 2;
    goTo(y / geo.travel, true);
    wake();
  }

  function start() {
    if (!document.body) return;
    build();
    draw();
    wake();
    window.addEventListener('scroll', function () { schedule(); wake(); }, { passive: true });
    window.addEventListener('resize', schedule);
    // The page grows after load: ambient scripts insert into the header and
    // the card grid reflows. Without this the thumb keeps the height it was
    // born with and stops matching the page it is measuring.
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(schedule);
      ro.observe(document.documentElement);
      ro.observe(document.body);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
