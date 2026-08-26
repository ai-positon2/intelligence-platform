"""tracker/unipile_transport.py -- account resolution, pagination, and the
strict/non-strict contract apify_transport.run_actor_and_wait established
(strict=True re-raises, strict=False swallows to [])."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import unipile_transport as ut  # noqa: E402


@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_account_for_platform_returns_none_when_nothing_connected(mock_list):
    mock_list.return_value = ([], None)
    assert ut.account_for_platform("linkedin") is None


@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_account_for_platform_returns_the_first_matching_id(mock_list):
    mock_list.return_value = ([{"id": "a1", "type": "LINKEDIN"}, {"id": "a2", "type": "LINKEDIN"}], None)
    assert ut.account_for_platform("linkedin") == "a1"


@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_fetch_posts_raises_strict_when_no_account_is_connected(mock_list):
    mock_list.return_value = ([], None)
    try:
        ut.fetch_posts("acmeco", "linkedin", strict=True)
        assert False, "expected UnipileTransportError"
    except ut.UnipileTransportError as e:
        assert "connected" in str(e).lower()


@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_fetch_posts_degrades_to_empty_list_when_not_strict(mock_list):
    mock_list.return_value = ([], None)
    assert ut.fetch_posts("acmeco", "linkedin", strict=False) == []


@patch("tracker.unipile_transport.unipile_client.list_posts")
@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_fetch_posts_pages_until_max_posts_reached(mock_list_accounts, mock_list_posts):
    mock_list_accounts.return_value = ([{"id": "a1", "type": "LINKEDIN"}], None)
    mock_list_posts.side_effect = [
        ({"items": [{"id": "1"}, {"id": "2"}], "cursor": "c2"}, None),
        ({"items": [{"id": "3"}], "cursor": None}, None),
    ]
    items = ut.fetch_posts("acmeco", "linkedin", max_posts=3, strict=True)
    assert [i["id"] for i in items] == ["1", "2", "3"]
    assert mock_list_posts.call_count == 2


@patch("tracker.unipile_transport.unipile_client.list_posts")
@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_fetch_posts_stops_at_max_pages_even_if_a_cursor_keeps_coming(mock_list_accounts, mock_list_posts):
    """A malformed response that never runs out of cursor must not loop
    forever -- max_pages is a hard ceiling independent of max_posts."""
    mock_list_accounts.return_value = ([{"id": "a1", "type": "LINKEDIN"}], None)
    mock_list_posts.return_value = ({"items": [{"id": "x"}], "cursor": "always-more"}, None)
    items = ut.fetch_posts("acmeco", "linkedin", max_posts=100, max_pages=3, strict=True)
    assert mock_list_posts.call_count == 3
    assert len(items) == 3


@patch("tracker.unipile_transport.unipile_client.list_posts")
@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_fetch_posts_raises_strict_on_a_transport_error(mock_list_accounts, mock_list_posts):
    from tracker import unipile_client
    mock_list_accounts.return_value = ([{"id": "a1", "type": "LINKEDIN"}], None)
    mock_list_posts.return_value = (None, {"kind": unipile_client.ERR_HTTP, "status": 500, "detail": "boom"})
    try:
        ut.fetch_posts("acmeco", "linkedin", strict=True)
        assert False, "expected UnipileTransportError"
    except ut.UnipileTransportError:
        pass
