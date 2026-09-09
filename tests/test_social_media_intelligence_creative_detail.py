"""The creative-detail modal: what the AI made of one post's creative,
readable as a single account -- the UI surface that makes tracker/
sci_vision.py's per-post reading actually visible to a reader, rather than
only present in raw JSON.

Runs the REAL text of the new functions (extracted from the page's own
inline script, not retyped) in node against a minimal document/CURRENT_RUN
shim. Extraction helpers are IMPORTED from test_social_media_intelligence_
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
from test_social_media_intelligence_datasources import (  # noqa: E402
    _extract_var, _extract_fn, _extract_line_fn, _extract_line_var, _page_html as _sci_page_html,
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
        _extract_fn(html, "relTime"),
        _extract_fn(html, "fmtNum"),
        _extract_line_fn(html, "fmtFullNum"),
        _extract_var(html, "METRIC_ICONS"),
        _extract_fn(html, "metricChips"),
        _extract_var(html, "PLATFORM_META"),
        _extract_var(html, "DEFAULT_META"),
        _extract_line_fn(html, "platformMeta"),
        _extract_fn(html, "postThumbnail"),
        _extract_line_var(html, "VIEW_GLYPH"),
        _extract_fn(html, "postTitle"),
        _extract_fn(html, "postHasAnalysis"),
        _extract_fn(html, "cdetailHero"),
        _extract_var(html, "CDETAIL_FIELDS"),
        _extract_fn(html, "cdetailNorm"),
        _extract_var(html, "CDETAIL_ERROR_TEXT"),
        _extract_fn(html, "cdetailReason"),
        _extract_line_var(html, "CDETAIL_SUMMARY_MAX_POINTS"),
        _extract_line_var(html, "CDETAIL_SUMMARY_MAX_CHARS"),
        _extract_fn(html, "cdetailSummaryPoints"),
        _extract_fn(html, "cdetailSummaryCard"),
        _extract_fn(html, "cdetailRow"),
        _extract_fn(html, "cdetailInsights"),
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
    }
    base.update(overrides)
    return base


def test_opens_and_shows_the_analysis_when_it_succeeded():
    post = _post(
        creative_analysis={"summary": "A product shot.", "messaging": "New launch", "cta": "Shop now",
                           "tone": "urgent", "subject": "running shoes"},
        creative_analysis_status="ok",
    )
    out = _run(1, {"posts": [post]})
    assert out["open"] is True
    html = out["panel"]
    assert "New launch" in html
    assert "Shop now" in html
    assert "running shoes" in html


# ── The hero: the post itself, ahead of what the AI made of it ─────────────

def test_the_hero_shows_the_real_caption_and_platform_and_format():
    post = _post(caption="Big news for our team today!", platform="linkedin", post_type="video")
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert "Big news for our team today!" in html
    assert "Linkedin" not in html  # platformLabel must fix the capitalize-mangled proper noun
    assert "LinkedIn" in html
    assert "Video" in html


def test_a_caption_less_post_says_so_rather_than_showing_nothing():
    post = _post(caption="")
    out = _run(1, {"posts": [post]})
    assert "No caption on this post." in out["panel"]


def test_the_hero_shows_metrics_and_relative_age():
    post = _post(metrics={"likes": 24, "comments": 3, "views": 336})
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert "24" in html and "336" in html


def test_a_real_post_url_becomes_a_view_original_link():
    post = _post(post_url="https://instagram.com/p/abc123/")
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert 'href="https://instagram.com/p/abc123/"' in html
    assert "View original post" in html


def test_a_post_with_no_url_gets_no_dead_link():
    post = _post(post_url=None)
    out = _run(1, {"posts": [post]})
    assert "View original post" not in out["panel"]


def test_a_thumbnail_renders_when_the_post_has_one():
    post = _post(post_type="image", raw={}, media_urls=["https://cdn.example/p1.jpg"])
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert 'sci-cdetail-hero-media' in html
    assert 'src="https://cdn.example/p1.jpg"' in html


def test_a_thumbnailless_post_gets_the_notext_hero_variant_not_a_broken_image():
    post = _post(post_type="text", raw={}, media_urls=[])
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert "sci-cdetail-hero-notext" in html
    assert "sci-cdetail-hero-media" not in html


def test_a_stray_caption_is_escaped_not_executed():
    post = _post(caption="<img src=x onerror=alert(1)>")
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert "<img src=x onerror" not in html
    assert "&lt;img src=x onerror" in html


# ── The reading: one field per row, no vendor comparison ───────────────────

def test_a_not_configured_deployment_says_so_not_a_generic_failure():
    post = _post(creative_analysis=None, creative_analysis_status="skipped")
    out = _run(1, {"posts": [post]})
    assert "not configured on this deployment" in out["panel"]


def test_a_failed_analysis_shows_the_specific_error_text():
    post = _post(creative_analysis={"error": "unparsable_response"}, creative_analysis_status="failed")
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


def test_a_field_with_nothing_said_does_not_render_a_row():
    post = _post(creative_analysis={"summary": "s", "branding": ""}, creative_analysis_status="ok")
    out = _run(1, {"posts": [post]})
    assert "Branding" not in out["panel"]


def test_post_has_analysis_true_only_when_the_analysis_succeeded():
    html = _sci_page_html()
    src = _extract_fn(html, "postHasAnalysis")
    js = (src +
         "\nconsole.log(JSON.stringify(["
         "  postHasAnalysis({creative_analysis_status:'ok'}),"
         "  postHasAnalysis({creative_analysis_status:'pending'}),"
         "  postHasAnalysis({creative_analysis_status:'failed'})"
         "]));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout.strip().splitlines()[-1]) == [True, False, False]


def test_every_card_opens_the_detail_modal_regardless_of_analysis_status():
    """Static-source check on renderPostCard itself: the whole card opens the
    modal unconditionally now -- not gated by postHasAnalysis the way the
    small analyze-button toggle used to be, since the modal itself already
    says plainly when a reading was never run or failed (see cdetailInsights).
    A post with nothing to show yet still deserves one click to confirm
    that, not to be quietly excluded from the affordance every other post
    card gets."""
    html = _sci_page_html()
    block = _extract_fn(html, "renderPostCard")
    assert "postHasAnalysis" not in block
    assert 'onclick="openCreativeDetail(' in block
    # Keyboard-operable: this is a <div>, not the <a> it used to be, so it
    # has to carry its own focusability and activation keys.
    assert 'tabindex="0"' in block
    assert 'role="button"' in block
    assert "onkeydown=" in block
    # The one explicit way out to the real post must stop its own click from
    # also opening the modal underneath it.
    assert 'class="sci-postcard-view"' in block
    assert "event.stopPropagation()" in block


def test_the_card_is_not_an_anchor_wrapping_the_whole_post():
    """The card used to be `<a href=post_url>`, so every click navigated
    away from the report. It must not be an <a> at the top level any more --
    the whole point is that a click opens the modal instead."""
    html = _sci_page_html()
    block = _extract_fn(html, "renderPostCard")
    # Substring-safe against the unrelated nested `sci-postcard-view` anchor:
    # matches only the exact top-level class, not anything it prefixes.
    assert "'<a class=\"sci-postcard' + (" not in block
    assert "'<div class=\"sci-postcard' + (" in block


# ── The reason a post has no reading, in the reader's own words ────────────
#
# The pipeline records the real cause on creative_analysis_error (an HTTP
# status, "Could not extract any video frames"), and this panel used to
# collapse every one of them into "The vendor call failed for this post" --
# the one sentence that tells whoever is debugging nothing at all.

def test_the_stored_error_detail_is_shown_alongside_the_plain_reason():
    post = _post(
        creative_analysis=None, creative_analysis_status="failed",
        creative_analysis_error="Could not extract any video frames.",
    )
    out = _run(1, {"posts": [post]})
    assert "Could not extract any video frames." in out["panel"]


def test_a_raw_vendor_message_reaches_the_panel_rather_than_being_swallowed():
    post = _post(
        creative_analysis=None, creative_analysis_status="failed",
        creative_analysis_error="Error code: 400 - could not fetch the supplied image URL",
    )
    out = _run(1, {"posts": [post]})
    assert "could not fetch the supplied image URL" in out["panel"]


def test_an_error_code_already_said_in_plain_language_is_not_repeated():
    """The stored column often carries the same short code the map above
    already renders as a sentence. Printing both would just be noise."""
    post = _post(
        creative_analysis={"error": "unparsable_response"}, creative_analysis_status="failed",
        creative_analysis_error="unparsable_response",
    )
    out = _run(1, {"posts": [post]})
    assert "not in a readable shape" in out["panel"]
    assert "unparsable_response" not in out["panel"]


def test_a_truncated_reply_is_named_as_such():
    post = _post(
        creative_analysis={"error": "response_truncated"}, creative_analysis_status="failed",
        creative_analysis_error="response_truncated",
    )
    out = _run(1, {"posts": [post]})
    assert "ran out of room mid-answer" in out["panel"]


def test_a_vendor_that_described_nothing_is_named_as_such():
    """Not "the call failed": the call worked and the answer was empty, and
    those point at completely different things to go and look at."""
    post = _post(
        creative_analysis={"error": "empty_response"}, creative_analysis_status="failed",
        creative_analysis_error="empty_response",
    )
    out = _run(1, {"posts": [post]})
    assert "described nothing about this creative" in out["panel"]


def test_a_stored_error_is_escaped_like_any_other_text():
    post = _post(
        creative_analysis=None, creative_analysis_status="failed",
        creative_analysis_error='<img src=x onerror="alert(1)">',
    )
    out = _run(1, {"posts": [post]})
    assert "<img src=x" not in out["panel"]
    assert "&lt;img" in out["panel"]


# ── The lead summary: short pointers, never a wall of quoted prose ─────────
#
# The bug this guards against: a video's folded summary (tracker/sci_vision.
# summarize_frames) used to join EVERY sampled frame's own full-sentence
# summary with " / ", producing a run-on paragraph of near-duplicate
# sentences. The backend caps that at 2 frames now, but this page must not
# trust stored data (written before that cap, or simply still long) to
# already be short -- it splits and truncates defensively on read.

def test_a_short_single_sentence_summary_renders_as_one_point():
    post = _post(creative_analysis={"summary": "A candid shot from a company event."},
                 creative_analysis_status="ok")
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert html.count("sci-cdetail-summary-point") == 1
    assert "A candid shot from a company event." in html


def test_a_slash_joined_video_summary_becomes_separate_pointers():
    post = _post(
        creative_analysis={"summary": "Opens on the logo. / Shows the product in hand."},
        creative_analysis_status="ok",
    )
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert html.count("sci-cdetail-summary-point") == 2
    assert "Opens on the logo." in html
    assert "Shows the product in hand." in html


def test_more_than_three_joined_sentences_are_capped_not_all_shown():
    post = _post(
        creative_analysis={"summary": "One. / Two. / Three. / Four. / Five."},
        creative_analysis_status="ok",
    )
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert html.count("sci-cdetail-summary-point") == 3
    assert "Four." not in html
    assert "Five." not in html


def test_an_overlong_single_sentence_is_truncated_with_an_ellipsis():
    long_sentence = "This is a very long run-on summary sentence " + ("padding word " * 20) + "at the very end."
    post = _post(creative_analysis={"summary": long_sentence}, creative_analysis_status="ok")
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert "…" in html
    assert "at the very end." not in html
    # Truncated at a word boundary, not mid-word.
    assert "…</div>" in html or "… " in html


def test_a_post_with_no_summary_shows_no_summary_card():
    post = _post(creative_analysis={"messaging": "something"}, creative_analysis_status="ok")
    out = _run(1, {"posts": [post]})
    assert "sci-cdetail-summary" not in out["panel"]


def test_the_summary_is_no_longer_a_field_row_among_the_others():
    """Summary is pulled out into its own card, not left in CDETAIL_FIELDS
    -- it must never render twice."""
    post = _post(creative_analysis={"summary": "s"}, creative_analysis_status="ok")
    out = _run(1, {"posts": [post]})
    html = out["panel"]
    assert '<div class="sci-cdetail-label">Summary</div>' not in html
