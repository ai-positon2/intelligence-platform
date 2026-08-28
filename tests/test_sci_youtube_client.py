"""tracker/sci_youtube_client.py -- resolve_channel and list_recent_videos
against mocked HTTP responses. No live network calls, no real API key."""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_youtube_client as yt  # noqa: E402


def _resp(json_data):
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = json_data
    return mock


def test_resolve_channel_recognizes_a_bare_channel_id():
    assert yt.resolve_channel("UC" + "x" * 22, "key") == "UC" + "x" * 22


def test_resolve_channel_parses_a_channel_url():
    assert yt.resolve_channel("https://www.youtube.com/channel/" + "UC" + "y" * 22, "key") == "UC" + "y" * 22


@patch("tracker.sci_youtube_client.requests.get")
def test_resolve_channel_via_handle_lookup(mock_get):
    mock_get.return_value = _resp({"items": [{"id": "UC123"}]})
    assert yt.resolve_channel("@acmeinc", "key") == "UC123"


@patch("tracker.sci_youtube_client.requests.get")
def test_resolve_channel_returns_none_when_nothing_matches(mock_get):
    mock_get.return_value = _resp({"items": []})
    assert yt.resolve_channel("some nonexistent brand", "key") is None


def test_resolve_channel_returns_none_without_a_key():
    assert yt.resolve_channel("acme", "") is None


@patch("tracker.sci_youtube_client.requests.get")
def test_list_recent_videos_normalizes_snippet_and_stats(mock_get):
    mock_get.side_effect = [
        _resp({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}),
        _resp({"items": [
            {"contentDetails": {"videoId": "v1", "videoPublishedAt": "2026-08-01T00:00:00Z"},
             "snippet": {"title": "Our new launch", "publishedAt": "2026-08-01T00:00:00Z"}},
        ], "nextPageToken": None}),
        _resp({"items": [{"id": "v1", "statistics": {"viewCount": "500", "likeCount": "40"}}]}),
    ]
    out = yt.list_recent_videos("UC123", "key", max_results=5, days=30)
    assert len(out) == 1
    assert out[0]["platform_post_id"] == "v1"
    assert out[0]["metrics"]["views"] == 500
    assert out[0]["post_type"] == "video"


@patch("tracker.sci_youtube_client.requests.get")
def test_list_recent_videos_folds_the_description_into_the_caption(mock_get):
    mock_get.side_effect = [
        _resp({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}),
        _resp({"items": [
            {"contentDetails": {"videoId": "v1", "videoPublishedAt": "2026-08-01T00:00:00Z"},
             "snippet": {"title": "Our new launch", "description": "30% off this week. Link in bio.",
                        "publishedAt": "2026-08-01T00:00:00Z"}},
        ], "nextPageToken": None}),
        _resp({"items": []}),
    ]
    out = yt.list_recent_videos("UC123", "key", max_results=5, days=30)
    assert out[0]["caption"] == "Our new launch\n\n30% off this week. Link in bio."


@patch("tracker.sci_youtube_client.requests.get")
def test_list_recent_videos_caption_falls_back_to_title_without_a_description(mock_get):
    mock_get.side_effect = [
        _resp({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}),
        _resp({"items": [
            {"contentDetails": {"videoId": "v1", "videoPublishedAt": "2026-08-01T00:00:00Z"},
             "snippet": {"title": "Our new launch", "publishedAt": "2026-08-01T00:00:00Z"}},
        ], "nextPageToken": None}),
        _resp({"items": []}),
    ]
    out = yt.list_recent_videos("UC123", "key", max_results=5, days=30)
    assert out[0]["caption"] == "Our new launch"


@patch("tracker.sci_youtube_client.requests.get")
def test_list_recent_videos_flags_a_shorts_title(mock_get):
    mock_get.side_effect = [
        _resp({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}),
        _resp({"items": [
            {"contentDetails": {"videoId": "v2", "videoPublishedAt": "2026-08-01T00:00:00Z"},
             "snippet": {"title": "Quick tip #shorts", "publishedAt": "2026-08-01T00:00:00Z"}},
        ], "nextPageToken": None}),
        _resp({"items": []}),
    ]
    out = yt.list_recent_videos("UC123", "key")
    assert out[0]["post_type"] == "short"


def test_list_recent_videos_returns_empty_without_a_channel_or_key():
    assert yt.list_recent_videos("", "key") == []
    assert yt.list_recent_videos("UC123", "") == []


# --- resolve_company_channel: YouTube resolved without the identify step ---

@patch("tracker.sci_youtube_client.requests.get")
def test_resolve_company_channel_finds_a_channel_from_a_bare_company_name(mock_get):
    mock_get.side_effect = [
        _resp({"items": [{"id": {"channelId": "UC" + "a" * 22}}]}),
        _resp({"items": [{"snippet": {"title": "Position2", "customUrl": "@position2"}}]}),
    ]
    out = yt.resolve_company_channel("Position2", "key")
    assert out["channel_id"] == "UC" + "a" * 22
    assert out["handle"] == "@position2"
    assert out["title"] == "Position2"
    assert out["profile_url"] == "https://www.youtube.com/@position2"


@patch("tracker.sci_youtube_client.requests.get")
def test_resolve_company_channel_reads_channel_id_from_snippet_too(mock_get):
    """search?type=channel carries the id in both id.channelId and
    snippet.channelId; neither shape may be the one that breaks it."""
    mock_get.side_effect = [
        _resp({"items": [{"snippet": {"channelId": "UC" + "b" * 22}}]}),
        _resp({"items": [{"snippet": {"title": "Acme", "customUrl": None}}]}),
    ]
    out = yt.resolve_company_channel("Acme", "key")
    assert out["channel_id"] == "UC" + "b" * 22
    assert out["handle"] == "UC" + "b" * 22
    assert out["profile_url"] == "https://www.youtube.com/channel/UC" + "b" * 22


@patch("tracker.sci_youtube_client.requests.get")
def test_resolve_company_channel_survives_a_failed_detail_lookup(mock_get):
    """The second call is cosmetic -- losing it must not lose the channel."""
    import requests as _requests
    mock_get.side_effect = [
        _resp({"items": [{"id": {"channelId": "UC" + "c" * 22}}]}),
        _requests.RequestException("boom"),
    ]
    out = yt.resolve_company_channel("Acme", "key")
    assert out["channel_id"] == "UC" + "c" * 22
    assert out["title"] == "Acme"


@patch("tracker.sci_youtube_client.requests.get")
def test_resolve_company_channel_returns_none_when_nothing_matches(mock_get):
    mock_get.side_effect = [_resp({"items": []})]
    assert yt.resolve_company_channel("Nonexistent Co", "key") is None


def test_resolve_company_channel_returns_none_without_a_name_or_key():
    assert yt.resolve_company_channel("", "key") is None
    assert yt.resolve_company_channel("Acme", "") is None
