/* Shared "thinking orb" loading indicator, used across every Strategic
 * Agent page in place of a bare CSS spinner ring while a background job
 * (Apollo search, a vendor scrape, a Claude call, ...) is running.
 *
 * A vanilla-JS port of thinking-orbs' own React <ThinkingOrb> component
 * (see static/js/thinking-orbs-engine.js's header) -- the lifecycle logic
 * below (rAF loop, DPR handling, theme resolution, reduced-motion static
 * frame, offscreen/hidden-tab pause) mirrors ports/../src/ThinkingOrb.tsx
 * and src/theme.ts line for line where this app's own conventions allow;
 * only the animation MATH comes from the vendored engine, unmodified.
 *
 * Usage:
 *   import { mountThinkingOrb } from '/static/js/thinking-orb.js';
 *   const orb = mountThinkingOrb(document.getElementById('slot'), {
 *     state: 'searching', size: 64,
 *     labels: ['Searching Apollo…', 'Matching company records…'],
 *   });
 *   ...
 *   orb.setState('solving', 'Scoring candidates…');   // next phase
 *   orb.destroy();                                    // when the card is replaced
 *
 * `state` is one of the library's 9 verbs: working, searching, solving,
 * listening, connecting, weaving, composing, breathing, shaping -- pick
 * whichever reads truest to what the task is actually doing (see the
 * per-agent call sites for the mapping used on this page).
 */

import { MODE_DRAWS, resolvePreset } from './thinking-orbs-engine.js';

const DEFAULT_LABELS = {
  working: 'Working…',
  searching: 'Searching…',
  solving: 'Solving…',
  listening: 'Listening…',
  connecting: 'Connecting…',
  weaving: 'Weaving…',
  composing: 'Composing…',
  breathing: 'Thinking…',
  shaping: 'Shaping…'
};

const DEFAULT_LABEL_INTERVAL_MS = 2600;

function ancestorTheme(el) {
  let node = el;
  while (node) {
    const attr = node.getAttribute && node.getAttribute('data-theme');
    if (attr === 'dark') return true;
    if (attr === 'light') return false;
    if (node.classList && node.classList.contains('dark')) return true;
    if (node.classList && node.classList.contains('light')) return false;
    node = node.parentElement;
  }
  return null;
}

function systemDark() {
  return typeof matchMedia === 'undefined' || matchMedia('(prefers-color-scheme: dark)').matches;
}

/**
 * Mount one orb + label into `container` (any empty block-level element;
 * its existing content is replaced). Returns a controller.
 *
 * opts:
 *   state          one of the 9 verbs above (default 'working')
 *   size            64 (card-scale, default) or 20 (inline-with-text scale)
 *   theme          'auto' (default, follows the page's data-theme/system) | 'dark' | 'light'
 *   speed          animation speed multiplier (default 1)
 *   label          a single status string, OR
 *   labels         an array of status strings to cycle through (texture for a
 *                  long-running phase -- "Searching Apollo…" -> "Cross-
 *                  referencing LinkedIn…" -> ...), shown one at a time
 *   labelIntervalMs  ms between label cycles (default 2600)
 *   layout         'row' (orb beside label, default for size 20) |
 *                  'column' (orb above label, default for size 64)
 */
export function mountThinkingOrb(container, opts = {}) {
  if (!container) throw new Error('mountThinkingOrb: no container element given');
  const size = opts.size === 20 ? 20 : 64;
  const theme = opts.theme || 'auto';
  const speed = opts.speed || 1;
  let state = opts.state || 'working';
  // An explicit '' means icon-only (the caller has its own text elsewhere,
  // e.g. a sibling .running-text element) -- only fall back to the state's
  // default label when no label/labels option was passed AT ALL, not when
  // one was passed and happens to be empty.
  let labels = opts.labels && opts.labels.length ? opts.labels.slice()
    : opts.label != null ? [opts.label]
    : opts.labels != null ? []
    : [DEFAULT_LABELS[state] || DEFAULT_LABELS.working];
  const labelIntervalMs = opts.labelIntervalMs || DEFAULT_LABEL_INTERVAL_MS;
  const layout = opts.layout || (size === 20 ? 'row' : 'column');

  container.innerHTML = '';
  container.classList.add('thinking-orb-wrap', 'thinking-orb-wrap--' + layout);

  const canvas = document.createElement('canvas');
  canvas.className = 'thinking-orb-canvas';
  canvas.setAttribute('role', 'img');
  canvas.style.width = size + 'px';
  canvas.style.height = size + 'px';
  container.appendChild(canvas);

  const labelEl = document.createElement('div');
  labelEl.className = 'thinking-orb-label';
  container.appendChild(labelEl);

  const ctx = canvas.getContext('2d');

  // ---- theme resolution (explicit prop -> ancestor data-theme -> system) ----
  let dark = true;
  function resolveDark() {
    if (theme === 'dark') { dark = true; return; }
    if (theme === 'light') { dark = false; return; }
    const fromTree = ancestorTheme(container);
    dark = fromTree === null ? systemDark() : fromTree;
  }
  resolveDark();

  const mq = typeof matchMedia !== 'undefined' ? matchMedia('(prefers-color-scheme: dark)') : null;
  const onMq = () => { resolveDark(); paintNow(); };
  mq && mq.addEventListener('change', onMq);
  const themeObserver = typeof MutationObserver !== 'undefined'
    ? new MutationObserver(() => { resolveDark(); paintNow(); })
    : null;
  themeObserver && themeObserver.observe(document.documentElement, {
    attributes: true, attributeFilter: ['class', 'data-theme'], subtree: true
  });

  const reducedMq = typeof matchMedia !== 'undefined' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
  let reduced = reducedMq ? reducedMq.matches : false;
  const onReducedChange = (e) => { reduced = e.matches; paintNow(); };
  reducedMq && reducedMq.addEventListener('change', onReducedChange);

  // ---- canvas sizing (DPR-capped, matches the library's own cap of 2) ----
  const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1);
  canvas.width = Math.round(size * dpr);
  canvas.height = Math.round(size * dpr);

  let draw, effSpeed, currentPreset;
  function resolveDraw() {
    currentPreset = resolvePreset(state, size);
    draw = MODE_DRAWS[currentPreset.mode];
    effSpeed = currentPreset.speed * speed;
  }
  resolveDraw();

  function frame(tSec) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    draw(ctx, size, tSec, dark, currentPreset.opts);
  }
  function paintNow() {
    frame(reduced ? 0.6 : (performance.now() / 1000) * effSpeed);
  }

  let raf = 0;
  let running = false;
  function loop() {
    frame((performance.now() / 1000) * effSpeed);
    if (running) raf = requestAnimationFrame(loop);
  }
  function start() {
    if (running || opts.paused || reduced) return;
    running = true;
    raf = requestAnimationFrame(loop);
  }
  function stop() {
    running = false;
    cancelAnimationFrame(raf);
  }

  paintNow();

  let visible = true;
  function cleanup() {
    stop();
    clearInterval(labelTimer);
    io && io.disconnect();
    themeObserver && themeObserver.disconnect();
    mq && mq.removeEventListener('change', onMq);
    reducedMq && reducedMq.removeEventListener('change', onReducedChange);
    document.removeEventListener('visibilitychange', onVisChange);
  }
  const io = typeof IntersectionObserver !== 'undefined'
    ? new IntersectionObserver(([entry]) => {
        // Most call sites in this codebase mount into a placeholder that
        // later gets REPLACED wholesale (a parent's innerHTML reassigned
        // once the real content/next phase is ready) rather than calling
        // destroy() explicitly. A removed element still intersects nothing,
        // so this callback fires for that case too -- distinguish "still in
        // the document, just scrolled offscreen" (pause only) from "no
        // longer in the document at all" (fully clean up: without this, the
        // rAF loop does stop, since nothing keeps re-scheduling it once
        // paused, but the theme MutationObserver/matchMedia/visibilitychange
        // listeners registered on long-lived globals would otherwise hold
        // this whole closure -- detached canvas included -- alive forever).
        if (!canvas.isConnected) { cleanup(); return; }
        visible = entry.isIntersecting;
        if (visible && document.visibilityState !== 'hidden') start();
        else stop();
      })
    : null;
  io && io.observe(canvas);
  function onVisChange() {
    if (document.visibilityState === 'hidden') stop();
    else if (visible) start();
  }
  document.addEventListener('visibilitychange', onVisChange);
  if (!io && !reduced) start();

  // ---- label text + a11y, with optional cycling ----
  let labelIdx = 0;
  let labelTimer = null;
  function renderLabel() {
    const text = labels[labelIdx] || '';
    labelEl.textContent = text;
    canvas.setAttribute('aria-label', text || DEFAULT_LABELS[state] || 'Loading');
  }
  function startLabelCycle() {
    clearInterval(labelTimer);
    labelIdx = 0;
    renderLabel();
    if (labels.length > 1) {
      labelTimer = setInterval(() => {
        labelIdx = (labelIdx + 1) % labels.length;
        labelEl.classList.add('is-changing');
        requestAnimationFrame(() => {
          renderLabel();
          requestAnimationFrame(() => labelEl.classList.remove('is-changing'));
        });
      }, labelIntervalMs);
    }
  }
  startLabelCycle();

  return {
    /** Switch to a new state (and optionally new label/labels) -- for a
     * multi-phase job that wants ONE orb to carry across phases instead of
     * remounting. Most call sites in this codebase instead destroy() and
     * mount a fresh orb per phase, since each phase already renders its
     * own card; this exists for the cases that don't. */
    setState(newState, newLabelOrLabels) {
      state = newState;
      resolveDraw();
      if (newLabelOrLabels != null) {
        labels = Array.isArray(newLabelOrLabels) ? newLabelOrLabels.slice() : [newLabelOrLabels];
        startLabelCycle();
      } else {
        labels = [DEFAULT_LABELS[state] || DEFAULT_LABELS.working];
        startLabelCycle();
      }
      paintNow();
    },
    setLabel(newLabelOrLabels) {
      labels = Array.isArray(newLabelOrLabels) ? newLabelOrLabels.slice() : [newLabelOrLabels];
      startLabelCycle();
    },
    destroy: cleanup
  };
}

/**
 * Convenience for the common case: a page's classic (non-module) inline
 * script has ALREADY rendered one or more static "Loading…" placeholders
 * before this module (loaded via a fire-and-forget dynamic import() from
 * that classic script, so there is no script-type-vs-load-order puzzle to
 * solve at each call site) has had a chance to run. Marking each
 * placeholder `data-orb-state="<state>"` and calling this once mounts an
 * orb into every one found, using the element's own existing text as the
 * label (so a no-JS/failed-import fallback still shows something
 * sensible) -- size defaults to 20 (inline-with-text scale) since that's
 * what a text-swap placeholder almost always wants; pass
 * data-orb-size="64" on an element to opt into the card scale instead.
 * Elements already removed from the document by the time this runs (a
 * fast-resolving fetch beat the dynamic import) are skipped, not an
 * error -- this is a race this helper is explicitly designed to lose
 * gracefully.
 */
export function autoMountOrbPlaceholders(root) {
  const scope = root || document;
  const found = scope.querySelectorAll('[data-orb-state]');
  const mounted = [];
  found.forEach((el) => {
    if (!el.isConnected) return;
    const state = el.getAttribute('data-orb-state');
    const size = el.getAttribute('data-orb-size') === '64' ? 64 : 20;
    const label = (el.textContent || '').trim();
    mounted.push(mountThinkingOrb(el, { state, size, label, layout: size === 64 ? 'column' : 'row' }));
  });
  return mounted;
}
