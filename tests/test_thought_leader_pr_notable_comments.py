"""templates/thought_leader_pr.html's notableCommentsHtml(), executed for
real (not grepped) via Node -- see feedback_testing_discipline.md's rule
that a checker must actually run the JS bundle it claims to verify.

Regression coverage for a documented gap: the own-post-comments digest
sent to Claude is never persisted anywhere, only the analysis result
(tracker/thought_leader_pr._clean_reaction_analysis's notable_comments),
so a notable comment used to render Claude's "why" about a comment with
no way for the reader to see the comment itself. The backend now enriches
each entry with the real text/platform from digest_by_id
(tests/test_thought_leader_pr.py::TestCleanReactionAnalysis and
TestAnalyzeCommentSentiment cover that); this test covers the other half,
that the template actually renders the real text rather than only "why".
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

_ESC = r"""
function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
"""


def _extract_function():
    src = open(_TEMPLATE, encoding="utf-8").read()
    m = re.search(r"function notableCommentsHtml\([\s\S]*?\n    \}\n", src)
    assert m, "notableCommentsHtml was not found in the template"
    return m.group(0)


_DRIVER = r"""
%s
%s

var out = {};
out.withText = notableCommentsHtml([
  {comment_id: "linkedin:1", why: "A specific, well-argued compliment.",
   text: "This is exactly the leadership our industry needs.", platform: "linkedin"}
]);
out.withoutText = notableCommentsHtml([{comment_id: "x:1", why: "Widely liked."}]);
out.escapesTheText = notableCommentsHtml([
  {comment_id: "x:2", why: "ok", text: '<script>alert(1)</script>', platform: "x"}
]);
console.log(JSON.stringify(out));
"""


def _run():
    js = _DRIVER % (_ESC, _extract_function())
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "driver.js")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(js)
        proc = subprocess.run([shutil.which("node"), p], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        pytest.fail("driver failed: %s" % (proc.stderr or proc.stdout)[-3000:])
    import json
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_real_comment_text_is_rendered_not_just_why():
    out = _run()
    assert "This is exactly the leadership our industry needs." in out["withText"]
    assert "A specific, well-argued compliment." in out["withText"]
    assert "linkedin" in out["withText"]


def test_a_missing_text_falls_back_to_an_honest_placeholder_not_a_blank():
    out = _run()
    assert "The original comment text was not retained." in out["withoutText"]
    assert "Widely liked." in out["withoutText"]


def test_the_comment_text_is_html_escaped():
    out = _run()
    assert "<script>alert(1)</script>" not in out["escapesTheText"]
    assert "&lt;script&gt;" in out["escapesTheText"]
