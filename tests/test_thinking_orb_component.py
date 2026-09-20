"""static/js/thinking-orb.js, executed for real (not grepped) via Node --
see feedback_testing_discipline.md's rule that a checker must actually run
the JS bundle it claims to verify.

Regression coverage for a real bug found while wiring the second page
(LinkedIn Strategy Researcher) into this shared component: several call
sites want an ICON-ONLY orb (their own separate ".running-text" element
already carries the sentence) and pass an explicit empty label for that --
but mountThinkingOrb used `opts.label || DEFAULT_LABELS[state]`, and `||`
treats an empty string the same as "not provided", so every icon-only
mount silently grew a redundant default label ("Working...", "Thinking...")
duplicating the text right next to it. Caught by actually rendering one in
a browser harness, not by reading the code.
"""

import os
import shutil
import subprocess
import tempfile

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMPONENT = os.path.join(_REPO_ROOT, "static", "js", "thinking-orb.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

_HARNESS = r"""
function Elem(id){
  this.id = id; this._html = ''; this.dataset = {}; this._attrs = {}; this.style = {};
  this.classList = { add(){}, remove(){}, toggle(){}, contains(){ return false; } };
  this.isConnected = true;
}
Object.defineProperty(Elem.prototype, 'innerHTML', {
  get(){ return this._html; }, set(v){ this._html = v; this._children = []; },
});
Elem.prototype.appendChild = function(child){ (this._children = this._children || []).push(child); return child; };
Elem.prototype.setAttribute = function(k, v){ this._attrs[k] = String(v); };
Elem.prototype.getAttribute = function(k){ return this._attrs[k] === undefined ? null : this._attrs[k]; };
Elem.prototype.querySelectorAll = function(sel){
  var out = [];
  (this._children || []).forEach(function(c){
    if (sel === 'canvas' && c.tagName === 'canvas') out.push(c);
  });
  return out;
};
Object.defineProperty(Elem.prototype, 'textContent', {
  get(){ return this._text || ''; }, set(v){ this._text = String(v); },
});

global.document = {
  createElement: function(tag){ var e = new Elem(null); e.tagName = tag; return e; },
  documentElement: new Elem('html'),
  addEventListener: function(){},
  removeEventListener: function(){},
  visibilityState: 'visible',
};
global.window = global;
global.MutationObserver = function(){ this.observe = function(){}; this.disconnect = function(){}; };
global.IntersectionObserver = function(){ this.observe = function(){}; this.disconnect = function(){}; };
global.requestAnimationFrame = function(){ return 0; };
global.cancelAnimationFrame = function(){};
global.matchMedia = function(){ return { matches: false, addEventListener(){}, removeEventListener(){} }; };
global.performance = { now: function(){ return 0; } };
global.CanvasRenderingContext2D = function(){};
Elem.prototype.getContext = function(){
  return {
    setTransform(){}, clearRect(){}, beginPath(){}, arc(){}, fill(){}, moveTo(){}, lineTo(){}, stroke(){},
    fillStyle: '', strokeStyle: '', lineWidth: 0,
  };
};

const componentUrl = 'file://' + process.argv[2].replace(/\\/g, '/');

(async () => {
  // A dynamic import() here (not a static one) is deliberate: it is NOT
  // hoisted, so the DOM stubs above are already in place on `global`
  // before thinking-orb.js's own top-level code (and its own static
  // import of thinking-orbs-engine.js, which never touches the DOM at
  // module scope anyway) runs. This loads the REAL, unmodified files via
  // Node's real ES module loader -- no hand-rolled CJS shim to keep in
  // sync with their export shape.
  const { mountThinkingOrb } = await import(componentUrl);

  const out = {};

  // 1. No label option at all -> default label for the state.
  const c1 = new Elem(null);
  mountThinkingOrb(c1, { state: 'working' });
  out.defaultLabelWhenOmitted = c1._children[1]._text;

  // 2. Explicit empty label -> stays empty (the bug).
  const c2 = new Elem(null);
  mountThinkingOrb(c2, { state: 'working', label: '' });
  out.emptyLabelStaysEmpty = c2._children[1]._text;

  // 3. A real label is used verbatim.
  const c3 = new Elem(null);
  mountThinkingOrb(c3, { state: 'searching', label: 'Searching Apollo…' });
  out.realLabelUsed = c3._children[1]._text;

  console.log(JSON.stringify(out));
})();
"""


def _run():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "driver.mjs")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(_HARNESS)
        proc = subprocess.run([shutil.which("node"), p, _COMPONENT],
                               capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        pytest.fail("driver failed: %s" % (proc.stderr or proc.stdout)[-4000:])
    import json
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_omitting_the_label_option_falls_back_to_the_states_default():
    out = _run()
    assert out["defaultLabelWhenOmitted"] == "Working…"


def test_an_explicit_empty_label_stays_empty_not_the_default():
    out = _run()
    assert out["emptyLabelStaysEmpty"] == ""


def test_a_real_label_is_rendered_verbatim():
    out = _run()
    assert out["realLabelUsed"] == "Searching Apollo…"
