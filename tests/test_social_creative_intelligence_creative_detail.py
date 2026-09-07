"""The creative-detail modal: Claude's and ChatGPT's independent reads of
one post, side by side -- the UI surface that makes the second-opinion data
tracker/sci_vision_openai.py collects actually visible to a reader, rather
than only present in raw JSON.

Runs the REAL text of the new functions (extracted from the page's own
inline script, not retyped) in node against a minimal document/CURRENT_RUN
shim. Extraction helpers are IMPORTED from test_social_creative_intelligence_
datasources, not copied -- two copies of the same extraction logic drift,
exactly the lesson tests/test_event_intel_client_picker.py's own docstring
already states about this codebase's node harnesses.
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

import app as appmod  # noqa: E402
from test_social_creative_intelligence_datasources import (  # noqa: E402
    _extract_var, _extract_fn, _extract_line_fn, _page_html as _sci_page_html,
)

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the page script")


_SHIM = """
var __els = {
  cdetailOverlay: {classList: {_set: {}, add: function(c){ this._set[c] = true; },
                               remove: function(c){ delete this._set[c]; },
                               contains: function(c){ return !!this._set[c]; }}},
  cdetailPanel: {innerHTML: ''},
};
global.document = { getElementById: function(id){ return __els[id] || null; } };
"""


def _run(post_id, current_run):
    html = _sci_page_html()
    src = "\n".join([
        _extract_line_fn(html, "esc"),
        _extract_var(html, "PLATFORM_LABEL"),
        _extract_fn(html, "platformLabel"),
        _extract_fn(html, "postFormatLabel"),
        _extract_fn(html, "fmtDate"),
        _extract_fn(html, "postTitle"),
        _extract_fn(html, "postHasAnalysis"),
        _extract_var(html, "CDETAIL_FIELDS"),
        _extract_fn(html, "cdetailField"),
        _extract_var(html, "CDETAIL_ERROR_TEXT"),
        _extract_fn(html, "cdetailColumn"),
        _extract_fn(html, "openCreativeDetail"),
        _extract_fn(html, "closeCreativeDetail"),
    ])
    js = (_SHIM +
         "\nvar CURRENT_RUN = " + json.dumps(current_run) + ";" +
         "\n" + src +
         "\nopenCreativeDetail(" + json.dumps(post_id) + ");" +
         "\nconsole.log(JSON.stringify({"
         "  panel: __els.cdetailPanel.innerHTML,"
         "  open: __els.cdetailOverlay.classList.contains('open')"
         "}));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "the extracted script threw:\n%s" % r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _post(**overrides):
    base = {
        "id": 1, "platform": "instagram", "post_type": "image", "caption": "Our new launch",
        "posted_at": "2026-08-01T00:00:00Z", "raw": {},
        "creative_analysis": None, "creative_analysis_status": "pending",
        "creative_analysis_openai": None, "creative_analysis_openai_status": "pending",
    }
    base.update(overrides)
    return base


def test_opens_and_shows_both_vendors_when_both_succeeded():
    post = _post(
        creative_analysis={"summary": "A product shot.", "messaging": "New launch", "cta": "Shop now",
                           "tone": "urgent", "subject": "running shoes"},
        creative_analysis_status="ok",
        creative_analysis_openai={"summary": "GPT's take.", "messaging": "Launch energy", "cta": "Buy now",
                                  "tone": "excited", "subject": "sneakers"},
        creative_analysis_openai_status="ok",
    )
    out = _run(1, {"posts": [post]})
    assert out["open"] is True
    html = out["panel"]
    assert "Claude" in html and "ChatGPT" in html
    assert "New launch" in html and "Launch energy" in html
    assert "Shop now" in html and "Buy now" in html


def test_a_not_configured_openai_column_says_so_not_a_generic_failure():
    post = _post(
        creative_analysis={"summary": "s"}, creative_analysis_status="ok",
        creative_analysis_openai=None, creative_analysis_openai_status="skipped",
    )
    out = _run(1, {"posts": [post]})
    assert "not configured on this deployment" in out["panel"]


def test_a_failed_column_shows_the_specific_vendor_error_text():
    post = _post(
        creative_analysis={"summary": "s"}, creative_analysis_status="ok",
        creative_analysis_openai={"error": "unparsable_response"}, creative_analysis_openai_status="failed",
    )
    out = _run(1, {"posts": [post]})
    assert "not in a readable shape" in out["panel"]


def test_video_folded_array_fields_are_joined_not_shown_as_a_literal_array():
    """A video's summarize_frames() output turns subject/setting/on_screen_text
    into arrays -- these must render as readable joined text, not [object
    Object] or a raw JSON array string."""
    post = _post(
        post_type="video",
        creative_analysis={"summary": "s", "subject": ["logo intro", "product in hand"]},
        creative_analysis_status="ok",
    )
    out = _run(1, {"posts": [post]})
    assert "logo intro / product in hand" in out["panel"]
    assert "[object" not in out["panel"]


def test_an_unfound_post_id_renders_a_clear_empty_state_not_a_crash():
    out = _run(999, {"posts": [_post(id=1)]})
    assert out["open"] is True
    assert "could not be found" in out["panel"]


def test_a_users_own_field_text_is_escaped():
    """The model's own written text is untrusted, same as anywhere else on
    this page -- an HTML tag inside a field must never be interpreted."""
    post = _post(
        creative_analysis={"summary": "s", "messaging": "<script>evil()</script>"},
        creative_analysis_status="ok",
    )
    out = _run(1, {"posts": [post]})
    assert "<script>evil()</script>" not in out["panel"]
    assert "&lt;script&gt;evil()&lt;/script&gt;" in out["panel"]


def test_post_has_analysis_true_when_either_vendor_succeeded():
    html = _sci_page_html()
    src = _extract_fn(html, "postHasAnalysis")
    js = (src +
         "\nconsole.log(JSON.stringify(["
         "  postHasAnalysis({creative_analysis_status:'ok', creative_analysis_openai_status:'pending'}),"
         "  postHasAnalysis({creative_analysis_status:'pending', creative_analysis_openai_status:'ok'}),"
         "  postHasAnalysis({creative_analysis_status:'failed', creative_analysis_openai_status:'skipped'})"
         "]));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout.strip().splitlines()[-1]) == [True, True, False]


def test_the_analyze_button_only_renders_when_there_is_something_to_show():
    """Static-source check on renderPostCard itself: the button must be
    gated by postHasAnalysis, not always present -- a dead button on every
    card (most of which will have nothing yet, or an error) would be worse
    than none."""
    html = _sci_page_html()
    block = _extract_fn(html, "renderPostCard")
    assert "postHasAnalysis(post)" in block
    assert "openCreativeDetail(" in block
    # The click must never also trigger the card's own navigation.
    assert "event.preventDefault()" in block
    assert "event.stopPropagation()" in block
