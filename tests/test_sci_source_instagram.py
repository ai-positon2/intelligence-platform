"""tracker/sci_source_instagram.py -- normalize() contract and the
strict-vs-swallow behavior collect() passes through to apify_transport."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_source_instagram as src  # noqa: E402


def test_normalize_maps_a_single_image_post():
    items = [{"id": "1", "shortCode": "abc", "type": "Image", "caption": "hi",
             "timestamp": "2026-08-01T00:00:00.000Z", "displayUrl": "https://cdn/img.jpg",
             "likesCount": 10, "commentsCount": 2}]
    out = src.normalize(items)
    assert len(out) == 1
    assert out[0]["platform_post_id"] == "1"
    assert out[0]["post_type"] == "image"
    assert out[0]["media_urls"] == ["https://cdn/img.jpg"]
    assert out[0]["metrics"]["likes"] == 10


def test_normalize_flags_a_reel_as_reel_not_video():
    items = [{"id": "2", "type": "Video", "productType": "clips", "videoUrl": "https://cdn/v.mp4"}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "reel"


def test_normalize_flags_a_carousel_and_collects_child_media():
    items = [{
        "id": "3", "type": "Sidecar", "displayUrl": "https://cdn/cover.jpg",
        "childPosts": [{"displayUrl": "https://cdn/c1.jpg"}, {"videoUrl": "https://cdn/c2.mp4"}],
    }]
    out = src.normalize(items)
    assert out[0]["post_type"] == "carousel"
    assert out[0]["media_urls"] == ["https://cdn/cover.jpg", "https://cdn/c1.jpg", "https://cdn/c2.mp4"]


def test_normalize_skips_items_with_no_id():
    assert src.normalize([{"type": "Image"}]) == []


@patch("tracker.sci_source_instagram.apify_transport.run_actor_and_wait")
def test_collect_passes_strict_through_to_the_transport(mock_run):
    mock_run.return_value = []
    src.collect("acme", "tok", strict=True)
    assert mock_run.call_args.kwargs.get("strict") is True
