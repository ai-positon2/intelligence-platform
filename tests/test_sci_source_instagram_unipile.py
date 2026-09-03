"""tracker/sci_source_instagram_unipile.py -- normalize() contract plus
collect()'s pass-through to unipile_transport.fetch_posts."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_source_instagram_unipile as src  # noqa: E402


def test_normalize_maps_a_single_image_post():
    items = [{"id": "1", "code": "abc123", "caption": {"text": "hi"},
             "taken_at": "2026-08-01T00:00:00Z", "image_url": "https://cdn/1.jpg",
             "like_count": 10, "comment_count": 2}]
    out = src.normalize(items)
    assert len(out) == 1
    assert out[0]["post_type"] == "image"
    assert out[0]["caption"] == "hi"
    assert out[0]["post_url"] == "https://www.instagram.com/p/abc123/"
    assert out[0]["metrics"]["likes"] == 10


def test_normalize_flags_reel_for_clips_product_type():
    items = [{"id": "2", "video": {"url": "https://cdn/v.mp4"}, "product_type": "clips"}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "reel"
    assert out[0]["media_urls"] == ["https://cdn/v.mp4"]


def test_normalize_flags_plain_video_without_clips_product_type():
    items = [{"id": "3", "video": {"url": "https://cdn/v.mp4"}}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "video"


def test_normalize_flags_carousel_and_collects_child_media():
    items = [{"id": "4", "carousel_media": [
        {"image_url": "https://cdn/a.jpg"}, {"video": {"url": "https://cdn/b.mp4"}},
    ]}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "carousel"
    assert out[0]["media_urls"] == ["https://cdn/a.jpg", "https://cdn/b.mp4"]


def test_normalize_accepts_a_plain_string_caption_too():
    items = [{"id": "5", "caption": "plain string caption", "image_url": "https://cdn/1.jpg"}]
    out = src.normalize(items)
    assert out[0]["caption"] == "plain string caption"


def test_normalize_skips_items_with_no_id_or_code():
    assert src.normalize([{"caption": "no id"}]) == []


@patch("tracker.sci_source_instagram_unipile.unipile_transport.fetch_posts")
def test_collect_strips_at_and_passes_is_company_false(mock_fetch):
    mock_fetch.return_value = []
    src.collect("@acmeco", strict=True)
    assert mock_fetch.call_args.args[0] == "acmeco"
    assert mock_fetch.call_args.kwargs.get("is_company") is False
    assert mock_fetch.call_args.kwargs.get("strict") is True
