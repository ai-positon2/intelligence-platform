"""tracker/sci_source_tiktok.py -- normalize() contract, the
direct-download-link-over-webpage-URL preference, and the strict-vs-swallow
behavior collect() passes through to apify_transport."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_source_tiktok as src  # noqa: E402


def test_normalize_maps_a_video_post():
    items = [{"id": "1", "webVideoUrl": "https://tiktok.com/@a/video/1", "text": "hi",
             "createTimeISO": "2026-08-01T00:00:00.000Z",
             "videoMeta": {"downloadAddr": "https://cdn/direct.mp4"},
             "diggCount": 10, "commentCount": 2, "shareCount": 1, "playCount": 100}]
    out = src.normalize(items)
    assert len(out) == 1
    assert out[0]["platform_post_id"] == "1"
    assert out[0]["post_type"] == "video"
    assert out[0]["metrics"]["views"] == 100


def test_media_urls_prefers_direct_download_addr_over_watch_page_url():
    items = [{"id": "2", "webVideoUrl": "https://tiktok.com/@a/video/2",
             "videoMeta": {"downloadAddr": "https://cdn/direct2.mp4"}}]
    out = src.normalize(items)
    assert out[0]["media_urls"] == ["https://cdn/direct2.mp4"]


def test_media_urls_falls_back_to_watch_page_url_when_no_direct_link():
    items = [{"id": "3", "webVideoUrl": "https://tiktok.com/@a/video/3"}]
    out = src.normalize(items)
    assert out[0]["media_urls"] == ["https://tiktok.com/@a/video/3"]


def test_normalize_skips_items_with_no_id():
    assert src.normalize([{"text": "no id"}]) == []


@patch("tracker.sci_source_tiktok.apify_transport.run_actor_and_wait")
def test_collect_passes_strict_through_to_the_transport(mock_run):
    mock_run.return_value = []
    src.collect("acme", "tok", strict=True)
    assert mock_run.call_args.kwargs.get("strict") is True
