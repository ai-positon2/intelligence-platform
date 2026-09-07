"""tracker/sci_classify.py -- pure aggregation over sci_store.get_posts(),
no vendor calls. Format mix, recurring themes, top-engaging post ids, and
the per-platform vs. cross-platform ("_all") split."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_classify  # noqa: E402


def _post(id_, platform, post_type="image", status="ok", analysis=None, metrics=None,
         openai_status=None, openai_analysis=None):
    return {"id": id_, "platform": platform, "post_type": post_type,
           "creative_analysis_status": status, "creative_analysis": analysis or {},
           "creative_analysis_openai_status": openai_status,
           "creative_analysis_openai": openai_analysis or {},
           "metrics": metrics or {}}


def test_classify_patterns_returns_empty_but_well_formed_for_no_posts(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: [])
    result = sci_classify.classify_patterns(1)
    assert result["_all"]["post_count"] == 0
    assert result["_all"]["top_themes"] == []


def test_classify_patterns_splits_by_platform_and_pools_into_all(monkeypatch):
    from tracker import sci_store
    posts = [_post(1, "instagram"), _post(2, "instagram"), _post(3, "youtube")]
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: posts)
    result = sci_classify.classify_patterns(1)
    assert result["instagram"]["post_count"] == 2
    assert result["youtube"]["post_count"] == 1
    assert result["_all"]["post_count"] == 3


def test_classify_patterns_counts_format_mix():
    posts = [_post(1, "x", post_type="video"), _post(2, "x", post_type="video"),
            _post(3, "x", post_type="image")]
    result = sci_classify._group_patterns(posts)
    assert result["format_mix"] == {"video": 2, "image": 1}


def test_classify_patterns_only_counts_themes_from_successfully_analyzed_posts():
    posts = [
        _post(1, "x", status="ok", analysis={"subject": "running shoes", "setting": "studio", "style": ""}),
        _post(2, "x", status="failed", analysis={"subject": "should not count"}),
        _post(3, "x", status="ok", analysis={"subject": "running shoes", "setting": "", "style": "bold"}),
    ]
    result = sci_classify._group_patterns(posts)
    assert "should not count" not in result["top_themes"]
    assert "running shoes" in result["top_themes"]
    assert result["analyzed_count"] == 2


def test_classify_patterns_pulls_video_themes_from_the_folded_frame_lists():
    posts = [_post(1, "x", status="ok", analysis={"subjects": ["a runner", "a shoe"], "settings": ["track"]})]
    result = sci_classify._group_patterns(posts)
    assert "a runner" in result["top_themes"]
    assert "track" in result["top_themes"]


def test_classify_patterns_ranks_top_engaging_posts_by_summed_metrics():
    posts = [
        _post(1, "x", metrics={"likes": 5}),
        _post(2, "x", metrics={"likes": 500, "comments": 20}),
        _post(3, "x", metrics={"likes": 50}),
    ]
    result = sci_classify._group_patterns(posts)
    assert result["top_engaging_post_ids"][0] == 2


def test_classify_patterns_folds_tone_and_format_technique_into_themes():
    posts = [
        _post(1, "x", status="ok", analysis={"subject": "shoes", "tone": "urgent",
                                             "format_technique": "studio product shot"}),
        _post(2, "x", status="ok", analysis={"subject": "shoes", "tone": "urgent",
                                             "format_technique": "studio product shot"}),
    ]
    result = sci_classify._group_patterns(posts)
    assert "urgent" in result["top_themes"]
    assert "studio product shot" in result["top_themes"]


def test_classify_patterns_treats_a_post_with_no_creative_analysis_as_no_themes():
    posts = [_post(1, "x", status="ok", analysis=None)]
    result = sci_classify._group_patterns(posts)
    assert result["top_themes"] == []


# ── Pooling both vendors' independent reads (2026-09-07) ────────────────

def test_a_post_counted_as_analyzed_when_only_openai_succeeded():
    """Claude erroring on a post Claude vision couldn't score is not the
    same as nothing being known about it -- ChatGPT's own successful read
    still counts as real evidence."""
    posts = [_post(1, "x", status="failed", analysis={"subject": "should not count"},
                   openai_status="ok", openai_analysis={"subject": "sneakers"})]
    result = sci_classify._group_patterns(posts)
    assert result["analyzed_count"] == 1
    assert "sneakers" in result["top_themes"]
    assert "should not count" not in result["top_themes"]


def test_both_vendors_agreeing_on_a_theme_ranks_it_above_a_single_vendor_theme():
    posts = [
        _post(1, "x", status="ok", analysis={"tone": "urgent"},
             openai_status="ok", openai_analysis={"tone": "urgent"}),
        _post(2, "x", status="ok", analysis={"tone": "playful"}, openai_status=None),
    ]
    result = sci_classify._group_patterns(posts)
    # "urgent" got two independent votes (Claude + ChatGPT on post 1);
    # "playful" got exactly one (Claude only, post 2) -- top_themes is
    # ordered by count, so the two-vendor theme must rank first.
    assert result["top_themes"].index("urgent") < result["top_themes"].index("playful")


def test_a_failed_openai_read_contributes_no_keywords():
    posts = [_post(1, "x", status="ok", analysis={"subject": "shoes"},
                   openai_status="failed", openai_analysis={"error": "vendor_call_failed"})]
    result = sci_classify._group_patterns(posts)
    assert result["top_themes"] == ["shoes"]


def test_pending_openai_status_is_not_treated_as_a_successful_read():
    """A post whose OpenAI pass simply hasn't been written yet (status
    'pending', the column's own DEFAULT) must not be read as if it were ok."""
    posts = [_post(1, "x", status="ok", analysis={"subject": "shoes"},
                   openai_status="pending", openai_analysis=None)]
    result = sci_classify._group_patterns(posts)
    assert result["top_themes"] == ["shoes"]
