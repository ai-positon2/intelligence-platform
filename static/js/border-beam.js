/*
 * Vanilla JS wrapper for border-beam.css -- toggles a `.border-beam` glow on
 * focus/blur of a search-style input.
 *
 * Two mount modes, because a search box comes in two different shapes on
 * this platform:
 *
 *  - mountBorderBeam(input): for a BARE input that already has its own
 *    visible border/radius (no separate icon wrapper, e.g. a plain form
 *    field). An <input> can't render ::before/::after itself (form controls
 *    are replaced elements), so the input is moved inside a new wrapper span
 *    that the CSS actually paints the beam onto, copying the input's own
 *    border-radius onto that wrapper so the ring traces the same shape.
 *
 *  - mountBorderBeamOn(container, trigger): for an input that sits INSIDE an
 *    already-rounded icon+input container (e.g. a "⌕ [search...]" pill).
 *    There, the visible rounded border belongs to the container, not the
 *    input -- wrapping just the input would draw a sharp-cornered ring
 *    floating inside a pill. This applies the beam directly to the existing
 *    container (no new element), triggered by the given input's focus/blur.
 *
 * Both auto-detect the target's own computed border-radius so the ring
 * always matches the real visible shape, rather than needing every call
 * site to know and hand-pass the right --radius-* token.
 */

function supportsBeam() {
  return typeof CSS !== "undefined" &&
    CSS.supports && CSS.supports("mask-composite", "exclude") &&
    CSS.supports("(--x: 0deg)");
}

function applyBeamStyles(el, opts) {
  el.classList.add("border-beam");
  if (opts.className) el.classList.add(opts.className);
  el.style.setProperty("--beam-radius", opts.radius);
  if (opts.color) el.style.setProperty("--beam-color", opts.color);
  if (opts.color2) el.style.setProperty("--beam-color-2", opts.color2);
  if (opts.duration) el.style.setProperty("--beam-duration", opts.duration + "s");

  var fadeTimer = null;

  function activate() {
    clearTimeout(fadeTimer);
    el.removeAttribute("data-beam-fading");
    el.setAttribute("data-beam-active", "");
  }

  function deactivate() {
    if (!el.hasAttribute("data-beam-active")) return;
    el.removeAttribute("data-beam-active");
    el.setAttribute("data-beam-fading", "");
    fadeTimer = setTimeout(function () {
      el.removeAttribute("data-beam-fading");
    }, 450);
  }

  return {
    activate: activate,
    deactivate: deactivate,
    clearFadeTimer: function () {
      clearTimeout(fadeTimer);
    },
  };
}

export function mountBorderBeam(input, opts) {
  if (!input || input.closest(".border-beam")) return null;
  opts = opts || {};
  if (!supportsBeam()) return null;

  var radius = opts.radius || getComputedStyle(input).borderRadius;

  var wrap = document.createElement("span");
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  // The input was very likely relying on ITS OWN flex-item/stretch sizing to
  // fill its old parent (a search-box row, a stacked form field, ...). Now
  // that the wrapper is the flex/block item instead, the input needs its own
  // explicit full-size rule to still fill the wrapper the same way.
  input.style.display = "block";
  input.style.width = "100%";
  input.style.boxSizing = "border-box";

  var api = applyBeamStyles(wrap, Object.assign({}, opts, { radius: radius }));
  input.addEventListener("focus", api.activate);
  input.addEventListener("blur", api.deactivate);
  if (document.activeElement === input) api.activate();

  return {
    activate: api.activate,
    deactivate: api.deactivate,
    destroy: function () {
      api.clearFadeTimer();
      input.removeEventListener("focus", api.activate);
      input.removeEventListener("blur", api.deactivate);
      if (wrap.parentNode) {
        wrap.parentNode.insertBefore(input, wrap);
        wrap.remove();
      }
      input.style.display = "";
      input.style.width = "";
      input.style.boxSizing = "";
    },
  };
}

export function mountBorderBeamOn(container, trigger, opts) {
  if (!container || !trigger || container.classList.contains("border-beam")) return null;
  opts = opts || {};
  if (!supportsBeam()) return null;

  var radius = opts.radius || getComputedStyle(container).borderRadius;
  var api = applyBeamStyles(container, Object.assign({}, opts, { radius: radius }));
  trigger.addEventListener("focus", api.activate);
  trigger.addEventListener("blur", api.deactivate);
  if (document.activeElement === trigger) api.activate();

  return {
    activate: api.activate,
    deactivate: api.deactivate,
    destroy: function () {
      api.clearFadeTimer();
      trigger.removeEventListener("focus", api.activate);
      trigger.removeEventListener("blur", api.deactivate);
      container.classList.remove("border-beam");
      container.removeAttribute("data-beam-active");
      container.removeAttribute("data-beam-fading");
      container.style.removeProperty("--beam-radius");
      container.style.removeProperty("--beam-color");
      container.style.removeProperty("--beam-color-2");
      container.style.removeProperty("--beam-duration");
    },
  };
}

export function autoMountBorderBeams(root, selector, opts) {
  var scope = root || document;
  var found = scope.querySelectorAll(selector || "[data-beam-input]");
  var mounted = [];
  found.forEach(function (el) {
    if (!el.isConnected) return;
    var m = mountBorderBeam(el, opts);
    if (m) mounted.push(m);
  });
  return mounted;
}
