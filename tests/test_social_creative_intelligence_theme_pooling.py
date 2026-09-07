"""postHasAnalysis / postThemeWords: the report page's own "is this post
analyzed" and "what theme words does it contribute" logic must pool BOTH
vendors, the same rule tracker/sci_classify.py's _post_keywords/
_analysis_keywords already apply server-side. Before this fix, six call
sites on the page (the headline "Creative described" tile, each platform's
"Analyzed" stat, the "Recurring creative themes" chart, and its "example
post" link) checked creative_analysis_status alone -- a post Claude failed
on but ChatGPT successfully described was invisible to all of them, visible
only in the per-post detail modal (postHasAnalysis, tested separately in
test_social_creative_intelligence_creative_detail.py).

Runs the REAL text of these functions (extracted from the page's own inline
script, not retyped) in node -- a text match cannot tell a fixed call site
from one still checking a single vendor.
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

from test_social_creative_intelligence_datasources import _extract_fn, _page_html  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the page script")


def _post(**overrides):
    base = {
        "creative_analysis": None, "creative_analysis_status": "pending",
        "creative_analysis_openai": None, "creative_analysis_openai_status": "pending",
    }
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


def test_claude_only_success_counts_as_analyzed_and_contributes_words():
    post = _post(creative_analysis_status="ok",
                 creative_analysis={"subject": "shoe", "tone": "urgent"})
    result = _run(post)
    assert result["hasAnalysis"] is True
    assert set(result["words"]) == {"shoe", "urgent"}


def test_openai_only_success_counts_as_analyzed_and_contributes_words():
    # The exact regression: Claude's own pass failed, ChatGPT's succeeded.
    post = _post(creative_analysis_status="failed", creative_analysis=None,
                 creative_analysis_openai_status="ok",
                 creative_analysis_openai={"subject": "shoe", "tone": "urgent"})
    result = _run(post)
    assert result["hasAnalysis"] is True
    assert set(result["words"]) == {"shoe", "urgent"}


def test_agreement_between_both_vendors_counts_a_theme_twice():
    post = _post(creative_analysis_status="ok",
                 creative_analysis={"subject": "shoe", "tone": "urgent"},
                 creative_analysis_openai_status="ok",
                 creative_analysis_openai={"subject": "shoe", "tone": "playful"})
    result = _run(post)
    assert result["hasAnalysis"] is True
    assert result["words"].count("shoe") == 2
    assert set(result["words"]) == {"shoe", "urgent", "playful"}


def test_neither_vendor_succeeding_is_not_analyzed():
    post = _post(creative_analysis_status="failed", creative_analysis_openai_status="skipped")
    result = _run(post)
    assert result["hasAnalysis"] is False
    assert result["words"] == []


def test_a_vendors_own_error_dict_never_leaks_words_even_if_status_disagrees():
    # Defense in depth: analysisThemeWords' own {error} guard, independent of
    # the status field a caller already checked.
    post = _post(creative_analysis_status="ok", creative_analysis={"error": "unparsable_response"})
    result = _run(post)
    assert result["words"] == []
