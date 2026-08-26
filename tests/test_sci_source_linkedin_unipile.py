"""tracker/sci_source_linkedin_unipile.py -- normalize() contract plus
collect()'s pass-through to unipile_transport.fetch_posts."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_source_linkedin_unipile as src  # noqa: E402


def test_normalize_maps_a_single_image_post():
    items = [{"id": "1", "share_url": "https://linkedin.com/p/1", "text": "hi",
             "date": "2026-08-01T00:00:00Z",
             "attachments": [{"url": "https://cdn/1.jpg", "mimetype": "image/jpeg"}],
             "reaction_counter": 4, "comment_counter": 1, "repost_counter": 0}]
    out = src.normalize(items)
    assert len(out) == 1
    assert out[0]["post_type"] == "image"
    assert out[0]["media_urls"] == ["https://cdn/1.jpg"]
    assert out[0]["metrics"]["likes"] == 4


def test_normalize_flags_video_post_by_mimetype():
    items = [{"id": "2", "attachments": [{"url": "https://cdn/v.mp4", "mimetype": "video/mp4"}]}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "video"


def test_normalize_flags_carousel_for_multiple_image_attachments():
    items = [{"id": "3", "attachments": [
        {"url": "https://cdn/a.jpg", "mimetype": "image/jpeg"},
        {"url": "https://cdn/b.jpg", "mimetype": "image/jpeg"},
    ]}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "carousel"
    assert out[0]["media_urls"] == ["https://cdn/a.jpg", "https://cdn/b.jpg"]


def test_normalize_falls_back_to_text_with_no_attachments():
    items = [{"id": "4", "text": "just words"}]
    out = src.normalize(items)
    assert out[0]["post_type"] == "text"
    assert out[0]["media_urls"] == []


def test_normalize_skips_items_with_no_id():
    assert src.normalize([{"text": "no id"}]) == []


@patch("tracker.sci_source_linkedin_unipile.unipile_transport.fetch_posts")
def test_collect_strips_at_and_passes_is_company_true(mock_fetch):
    mock_fetch.return_value = []
    src.collect("@acmeco", strict=True)
    assert mock_fetch.call_args.args[0] == "acmeco"
    assert mock_fetch.call_args.kwargs.get("is_company") is True
    assert mock_fetch.call_args.kwargs.get("strict") is True
