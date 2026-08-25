"""tracker/sci_source_facebook.py -- normalize() contract and the
strict-vs-swallow behavior collect() passes through to apify_transport."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_source_facebook as src  # noqa: E402


def test_normalize_maps_a_single_photo_post():
    items = [{"postId": "1", "url": "https://fb.com/p/1", "text": "hi",
             "time": "2026-08-01T00:00:00", "photo_image": {"uri": "https://cdn/1.jpg"},
             "likes": 5, "comments": 1, "shares": 0}]
    out = src.normalize(items)
    assert len(out) == 1
    assert out[0]["platform_post_id"] == "1"
    assert out[0]["post_type"] == "image"
    assert out[0]["media_urls"] == ["https://cdn/1.jpg"]
    assert out[0]["metrics"]["likes"] == 5


def test_normalize_flags_multiple_media_items_as_carousel():
    items = [{"postId": "2", "text": "album",
             "media": [{"__typename": "Photo", "photo_image": {"uri": "https://cdn/a.jpg"}},
                       {"__typename": "Photo", "photo_image": {"uri": "https://cdn/b.jpg"}}]}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "carousel"
    assert out[0]["media_urls"] == ["https://cdn/a.jpg", "https://cdn/b.jpg"]


def test_normalize_flags_video_media_as_video():
    items = [{"postId": "3", "media": [{"__typename": "Video", "url": "https://cdn/v.mp4"}]}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "video"


def test_normalize_skips_items_with_no_id():
    assert src.normalize([{"text": "no id here"}]) == []


@patch("tracker.sci_source_facebook.apify_transport.run_actor_and_wait")
def test_collect_passes_strict_through_to_the_transport(mock_run):
    mock_run.return_value = []
    src.collect("acmepage", "tok", strict=True)
    assert mock_run.call_args.kwargs.get("strict") is True
