"""tracker/sci_source_linkedin.py -- the feature-flag contract (actor_id()
is None when SCI_APIFY_LINKEDIN_ACTOR_ID is unset) plus normalize()."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_source_linkedin as src  # noqa: E402


def test_actor_id_is_none_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("SCI_APIFY_LINKEDIN_ACTOR_ID", raising=False)
    assert src.actor_id() is None


def test_actor_id_reads_the_env_var_when_set(monkeypatch):
    monkeypatch.setenv("SCI_APIFY_LINKEDIN_ACTOR_ID", "some/actor")
    assert src.actor_id() == "some/actor"


def test_normalize_maps_a_single_image_post():
    items = [{"postId": "1", "postUrl": "https://linkedin.com/p/1", "text": "hi",
             "postedAt": "2026-08-01T00:00:00.000Z", "images": [{"url": "https://cdn/1.jpg"}],
             "numLikes": 4, "numComments": 1, "numShares": 0}]
    out = src.normalize(items)
    assert len(out) == 1
    assert out[0]["post_type"] == "image"
    assert out[0]["media_urls"] == ["https://cdn/1.jpg"]


def test_normalize_flags_video_post():
    items = [{"postId": "2", "videoUrl": "https://cdn/v.mp4"}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "video"
    assert out[0]["media_urls"] == ["https://cdn/v.mp4"]


def test_normalize_skips_items_with_no_id():
    assert src.normalize([{"text": "no id"}]) == []


@patch("tracker.sci_source_linkedin.apify_transport.run_actor_and_wait")
def test_collect_passes_strict_through_to_the_transport(mock_run):
    mock_run.return_value = []
    src.collect("acmeco", "tok", strict=True)
    assert mock_run.call_args.kwargs.get("strict") is True
