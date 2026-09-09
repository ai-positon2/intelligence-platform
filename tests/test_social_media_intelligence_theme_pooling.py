"""postHasAnalysis / postThemeWords: the report page's own "is this post
analyzed" and "what theme words does it contribute" logic, gated on the
post's own creative_analysis_status -- the same rule tracker/sci_classify.
py's _post_keywords/_analysis_keywords apply server-side.

Runs the REAL text of these functions (extracted from the page's own inline
script, not retyped) in node.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

from test_social_media_intelligence_datasources import _extract_fn, _page_html  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the page script")


def _post(**overrides):
    base = {"creative_analysis": None, "creative_analysis_status": "pending"}
    base.update(overrides)
    return base


def _run(post):
    html = _page_html()
    src = "\n".join([
        _extract_fn(html, "postHasAnalysis"),
        _extract_fn(html, "analysisThemeWords"),
        _extract_fn(html, "postThemeWords"),
    ])
    js = (src +
         "\nvar post = " + json.dumps(post) + ";" +
         "\nconsole.log(JSON.stringify({"
         "  hasAnalysis: postHasAnalysis(post),"
         "  words: postThemeWords(post)"
         "}));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "the extracted script threw:\n%s" % r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_successful_analysis_counts_as_analyzed_and_contributes_words():
    post = _post(creative_analysis_status="ok",
                 creative_analysis={"subject": "shoe", "tone": "urgent"})
    result = _run(post)
    assert result["hasAnalysis"] is True
    assert set(result["words"]) == {"shoe", "urgent"}


def test_a_failed_analysis_is_not_analyzed():
    post = _post(creative_analysis_status="failed")
    result = _run(post)
    assert result["hasAnalysis"] is False
    assert result["words"] == []


def test_an_error_dict_never_leaks_words_even_if_status_disagrees():
    # Defense in depth: analysisThemeWords' own {error} guard, independent of
    # the status field a caller already checked.
    post = _post(creative_analysis_status="ok", creative_analysis={"error": "unparsable_response"})
    result = _run(post)
    assert result["words"] == []
