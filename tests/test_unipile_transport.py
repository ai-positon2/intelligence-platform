"""tracker/unipile_transport.py -- account resolution, pagination, and the
strict/non-strict contract apify_transport.run_actor_and_wait established
(strict=True re-raises, strict=False swallows to [])."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import unipile_transport as ut  # noqa: E402


def _acct(account_id="a1", platform="LINKEDIN", status="OK"):
    """An account in the shape /api/v1/accounts really returns: status lives
    under sources[], not at the top level."""
    return {"id": account_id, "type": platform, "sources": [{"status": status}]}


@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_account_for_platform_returns_none_when_nothing_connected(mock_list):
    mock_list.return_value = ([], None)
    assert ut.account_for_platform("linkedin") is None


@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_account_for_platform_returns_the_first_matching_id(mock_list):
    mock_list.return_value = ([_acct("a1"), _acct("a2")], None)
    assert ut.account_for_platform("linkedin") == "a1"


@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_account_for_platform_skips_accounts_whose_login_has_lapsed(mock_list):
    """A signed-out account keeps being listed, and sorts wherever the vendor
    feels like sorting it -- on the workspace this was first confirmed
    against, the first LinkedIn account returned was a lapsed one. Taking
    accounts[0] would have failed every collection while the panel reported
    LinkedIn as connected."""
    mock_list.return_value = ([_acct("stale", status="CREDENTIALS"), _acct("good")], None)
    assert ut.account_for_platform("linkedin") == "good"


@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_account_for_platform_returns_none_when_every_account_is_lapsed(mock_list):
    mock_list.return_value = ([_acct("s1", status="CREDENTIALS")], None)
    assert ut.account_for_platform("linkedin") is None


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
    mock_list_accounts.return_value = ([_acct("a1")], None)
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
    mock_list_accounts.return_value = ([_acct("a1")], None)
    mock_list_posts.return_value = ({"items": [{"id": "x"}], "cursor": "always-more"}, None)
    items = ut.fetch_posts("acmeco", "linkedin", max_posts=100, max_pages=3, strict=True)
    assert mock_list_posts.call_count == 3
    assert len(items) == 3


@patch("tracker.unipile_transport.unipile_client.list_posts")
@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_fetch_posts_raises_strict_on_a_transport_error(mock_list_accounts, mock_list_posts):
    from tracker import unipile_client
    mock_list_accounts.return_value = ([_acct("a1")], None)
    mock_list_posts.return_value = (None, {"kind": unipile_client.ERR_HTTP, "status": 500, "detail": "boom"})
    try:
        ut.fetch_posts("acmeco", "linkedin", strict=True)
        assert False, "expected UnipileTransportError"
    except ut.UnipileTransportError:
        pass


@patch("tracker.unipile_transport.unipile_client.list_posts")
@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_fetch_posts_stops_on_an_empty_page_even_though_a_cursor_came_back(
        mock_list_accounts, mock_list_posts):
    """The live API hands back a cursor on the last page too, so "there is a
    cursor" is not evidence there is more. Without an empty-page break a
    company with five posts spends max_pages round trips fetching nothing."""
    mock_list_accounts.return_value = ([_acct("a1")], None)
    mock_list_posts.side_effect = [
        ({"items": [{"id": "1"}], "cursor": "c2"}, None),
        ({"items": [], "cursor": "c3"}, None),
        ({"items": [{"id": "2"}], "cursor": "c4"}, None),
    ]
    items = ut.fetch_posts("60223", "linkedin", max_posts=40, max_pages=5, strict=True)
    assert [i["id"] for i in items] == ["1"]
    assert mock_list_posts.call_count == 2


@patch("tracker.unipile_transport.unipile_client.list_posts")
@patch("tracker.unipile_transport.unipile_client.list_accounts")
def test_a_caller_supplied_account_is_used_without_a_second_lookup(
        mock_list_accounts, mock_list_posts):
    """The LinkedIn adapter resolves an account for its own company lookup
    first. Re-resolving here would spend a second call and, worse, could
    pick a different account than the one the identifier was resolved
    through."""
    mock_list_posts.return_value = ({"items": []}, None)
    ut.fetch_posts("60223", "linkedin", strict=True, account_id="chosen")
    assert mock_list_accounts.call_count == 0
    assert mock_list_posts.call_args.args[0] == "chosen"
