"""templates/thought_leader_pr.html's sentimentBarsHtml(), executed for
real (not grepped) via Node -- see feedback_testing_discipline.md's rule
that a checker must actually run the JS bundle it claims to verify.

Regression coverage for a documented, previously-unfixed gap: a bare
sentiment percentage ("8% negative") hides its own denominator, so a
reader can't tell a thin read (1 of 12 items) from a deep one (40 of 400).
sentimentBarsHtml is the one shared renderer behind all five "how the room
reacts" sentiment displays (own-post comments, Reddit, X, and the four
genericPulseHtml platforms), so fixing it here fixes every call site.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE = os.path.join(_REPO_ROOT, "templates", "thought_leader_pr.html")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _extract_function():
    src = open(_TEMPLATE, encoding="utf-8").read()
    m = re.search(
        r"const SENTIMENT_LABEL[\s\S]*?function sentimentBarsHtml\([\s\S]*?\n    \}\n",
        src,
    )
    assert m, "sentimentBarsHtml (or its SENTIMENT_LABEL dependency) was not found in the template"
    return m.group(0)


_DRIVER = r"""
%s

var out = {};
out.withCounts = sentimentBarsHtml({positive: 1, neutral: 8, negative: 3, mixed: 0}, 12);
out.zeroTotal = sentimentBarsHtml({}, 0);
console.log(JSON.stringify(out));
"""


def _run():
    js = _DRIVER % _extract_function()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "driver.js")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(js)
        proc = subprocess.run([shutil.which("node"), p], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        pytest.fail("driver failed: %s" % (proc.stderr or proc.stdout)[-3000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_labelled_total_is_shown_once_as_a_caption():
    out = _run()
    assert "Based on 12 items Claude was able to label" in out["withCounts"]


def test_no_caption_is_shown_when_there_is_nothing_to_base_it_on():
    out = _run()
    assert "Based on" not in out["zeroTotal"]


def test_each_bars_own_raw_count_sits_behind_its_percentage():
    out = _run()
    html = out["withCounts"]
    # 1 of 12 -> 8%; the raw numerator (1) must be visible right there, not
    # just the percentage, so "8%" doesn't read the same as it would for
    # 1-of-12 versus (hypothetically) 33-of-400.
    assert re.search(r'class="tlpr-sent-pct">8%\s*<span class="tlpr-sent-n">\(1\)</span>', html)
    assert re.search(r'class="tlpr-sent-pct">67%\s*<span class="tlpr-sent-n">\(8\)</span>', html)
    assert re.search(r'class="tlpr-sent-pct">25%\s*<span class="tlpr-sent-n">\(3\)</span>', html)
    assert re.search(r'class="tlpr-sent-pct">0%\s*<span class="tlpr-sent-n">\(0\)</span>', html)
