"""tracker/sci_source_x.py -- normalize() contract, the highest-bitrate
mp4-variant selection for video tweets, and the strict-vs-swallow behavior
collect() passes through to apify_transport."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_source_x as src  # noqa: E402


def test_normalize_maps_a_text_only_tweet():
    items = [{"id": "1", "url": "https://x.com/a/status/1", "text": "hello",
             "createdAt": "2026-08-01T00:00:00.000Z",
             "likeCount": 3, "retweetCount": 1, "replyCount": 0, "viewCount": 50}]
    out = src.normalize(items)
    assert len(out) == 1
    assert out[0]["post_type"] == "text"
    assert out[0]["media_urls"] == []
    assert out[0]["metrics"]["likes"] == 3


def test_normalize_maps_a_photo_tweet():
    items = [{"id": "2", "media": [{"type": "photo", "media_url_https": "https://cdn/p.jpg"}]}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "image"
    assert out[0]["media_urls"] == ["https://cdn/p.jpg"]


def test_normalize_picks_the_highest_bitrate_mp4_variant_for_video():
    items = [{"id": "3", "media": [{
        "type": "video",
        "video_info": {"variants": [
            {"content_type": "video/mp4", "bitrate": 250000, "url": "https://cdn/low.mp4"},
            {"content_type": "video/mp4", "bitrate": 2000000, "url": "https://cdn/high.mp4"},
            {"content_type": "application/x-mpegURL", "bitrate": 0, "url": "https://cdn/master.m3u8"},
        ]},
    }]}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "video"
    assert out[0]["media_urls"] == ["https://cdn/high.mp4"]


def test_normalize_flags_multiple_media_items_as_carousel():
    items = [{"id": "4", "media": [
        {"type": "photo", "media_url_https": "https://cdn/a.jpg"},
        {"type": "photo", "media_url_https": "https://cdn/b.jpg"},
    ]}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "carousel"


def test_normalize_skips_items_with_no_id():
    assert src.normalize([{"text": "no id"}]) == []


@patch("tracker.sci_source_x.apify_transport.run_actor_and_wait")
def test_collect_passes_strict_through_to_the_transport(mock_run):
    mock_run.return_value = []
    src.collect("acme", "tok", strict=True)
    assert mock_run.call_args.kwargs.get("strict") is True
