"""tracker/sci_classify.py -- pure aggregation over sci_store.get_posts(),
no vendor calls. Format mix, recurring themes, top-engaging post ids, and
the per-platform vs. cross-platform ("_all") split."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_classify  # noqa: E402


def _post(id_, platform, post_type="image", status="ok", analysis=None, metrics=None):
    return {"id": id_, "platform": platform, "post_type": post_type,
           "creative_analysis_status": status, "creative_analysis": analysis or {},
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


def test_classify_patterns_treats_a_post_with_no_creative_analysis_as_no_themes():
    posts = [_post(1, "x", status="ok", analysis=None)]
    result = sci_classify._group_patterns(posts)
    assert result["top_themes"] == []
