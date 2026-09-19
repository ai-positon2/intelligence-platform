"""templates/thought_leader_pr.html's auto-run sequencing, executed for
real (not grepped) via Node -- see feedback_testing_discipline.md's rule
that a checker must actually run the JS bundle it claims to verify.

Regression coverage for a documented race: on the initial render of a
confirmed run, renderPostsSection(run) is called first and, via
maybeAutoRun, may synchronously start Phase 3 (press) collection -- which
paints a loading spinner into #tlprPressSection AND kicks off its POST in
the background. The very next call in that same sequence,
renderPressSection(run), used to read the SAME run snapshot's now-stale
press_status ('idle') and repaint the idle "Find press coverage" CTA right
over the spinner that had just started, even though collection was
genuinely running. The real report app.py entry points hit are:
templates/thought_leader_pr.html's openHistoryRun() (one shared `run`
object across three render calls) and the confirm-success handler (used
to pass three SEPARATE throwaway object literals -- fixed to share one).
"""

import os
import re
import shutil
import subprocess
import tempfile

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE = os.path.join(_REPO_ROOT, "templates", "thought_leader_pr.html")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _extract_main_script():
    src = open(_TEMPLATE, encoding="utf-8").read()
    m = re.search(r"<script>\n(.*?)\n  </script>", src, re.S)
    assert m, "the main inline <script> block was not found in the template"
    body = m.group(1)
    assert "{{" not in body.replace("{{ runs|tojson }}", ""), \
        "an unexpected Jinja expression appeared in the script body -- update this test's stub"
    return body.replace("{{ runs|tojson }}", "[]")


_HARNESS = r"""
function Elem(id){
  this.id = id;
  this._html = '';
  this.dataset = {};
  this.classList = { add(){}, remove(){}, toggle(){}, contains(){ return false; } };
  this.disabled = false;
  this.hidden = false;
}
Object.defineProperty(Elem.prototype, 'innerHTML', {
  get(){ return this._html; },
  set(v){ this._html = v; },
});
Elem.prototype.addEventListener = function(){};
Elem.prototype.closest = function(){ return null; };

var ELEMENTS = {};
global.document = {
  getElementById: function(id){
    if (!ELEMENTS[id]) ELEMENTS[id] = new Elem(id);
    return ELEMENTS[id];
  },
  querySelector: function(){ return null; },
  querySelectorAll: function(){ return []; },
  addEventListener: function(){},
  createElement: function(){ return new Elem(null); },
};
global.window = global;
global.MutationObserver = function(){ this.observe = function(){}; };
global.requestAnimationFrame = function(fn){};
global.localStorage = { getItem(){ return null; }, setItem(){}, removeItem(){} };

var FETCH_CALLS = [];
global.fetch = function(url, opts){
  FETCH_CALLS.push({url: String(url), method: (opts && opts.method) || 'GET'});
  return new Promise(function(){}); // never resolves -- only the pre-`await` synchronous
                                      // painting inside each start*() matters for this test
};

%s

function elHtml(id){ return ELEMENTS[id] ? ELEMENTS[id].innerHTML : undefined; }
"""


def _run(script_js):
    js = _HARNESS % _extract_main_script() + "\n" + script_js
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "driver.js")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(js)
        proc = subprocess.run([shutil.which("node"), p], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        pytest.fail("driver failed: %s" % (proc.stderr or proc.stdout)[-4000:])
    return proc.stdout


def test_openhistoryrun_style_sequence_does_not_repaint_the_started_press_spinner():
    out = _run(r"""
      var run = {id: 42, posts_status: 'idle', press_status: 'idle', reaction_status: 'idle',
                 synthesis_status: 'idle'};
      renderPostsSection(run);
      renderPressSection(run);
      renderSynthesisSection(run);
      console.log('PRESS_HTML_START' + elHtml('tlprPressSection') + 'PRESS_HTML_END');
      console.log('FETCH_COUNT=' + FETCH_CALLS.filter(c => /collect-press/.test(c.url)).length);
    """)
    m = re.search(r"PRESS_HTML_START(.*?)PRESS_HTML_END", out, re.S)
    assert m, "could not read back #tlprPressSection's innerHTML: %s" % out
    press_html = m.group(1)
    assert "tlpr-card--posts-loading" in press_html, (
        "press section should still show its just-started loading spinner, not a stale "
        "idle CTA repainted over it: %r" % press_html
    )
    assert "tlpr-card--posts-cta" not in press_html
    assert "FETCH_COUNT=1" in out, "press collection must still be started exactly once, not zero or twice"


def test_the_confirm_handlers_three_render_calls_share_one_run_object():
    """The confirm-success handler used to pass three separate {id, X_status:
    'idle'} literals to renderPostsSection/renderPressSection/
    renderSynthesisSection -- even with maybeAutoRun's optimistic mutation,
    three unrelated objects can't communicate with each other. This proves
    the fix (one shared object) is what's actually wired into the handler,
    not just present as an unused helper."""
    src = open(_TEMPLATE, encoding="utf-8").read()
    start = src.find("const confirmBtn = e.target.closest")
    assert start != -1, "could not locate the #tlprConfirmBtn click handler"
    body = src[start:start + 1600]
    assert "const freshRun" in body
    assert body.count("renderPostsSection(freshRun)") == 1
    assert body.count("renderPressSection(freshRun)") == 1
    assert body.count("renderSynthesisSection(freshRun)") == 1
    assert "renderPostsSection({id: runId" not in body
    assert "renderPressSection({id: runId" not in body
    assert "renderSynthesisSection({id: runId" not in body
