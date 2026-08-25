"""Tests for tracker/apify_transport.py: uses mocked HTTP responses. No live
network calls, no real Apify token."""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import apify_transport  # noqa: E402


def _resp(json_data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


@patch("tracker.apify_transport.requests.get")
@patch("tracker.apify_transport.requests.post")
def test_run_actor_and_wait_returns_dataset_items_on_success(mock_post, mock_get):
    mock_post.return_value = _resp({"data": {"id": "run1"}})
    mock_get.side_effect = [
        _resp({"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds1"}}),
        _resp([{"id": "post1"}, {"id": "post2"}]),
    ]
    items = apify_transport.run_actor_and_wait("some/actor", {"foo": "bar"}, "tok",
                                               poll_interval=0)
    assert items == [{"id": "post1"}, {"id": "post2"}]


@patch("tracker.apify_transport.requests.get")
@patch("tracker.apify_transport.requests.post")
def test_run_actor_and_wait_polls_until_finished(mock_post, mock_get):
    mock_post.return_value = _resp({"data": {"id": "run1"}})
    mock_get.side_effect = [
        _resp({"data": {"status": "RUNNING"}}),
        _resp({"data": {"status": "RUNNING"}}),
        _resp({"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds1"}}),
        _resp([{"id": "post1"}]),
    ]
    items = apify_transport.run_actor_and_wait("some/actor", {}, "tok", poll_interval=0)
    assert items == [{"id": "post1"}]
    assert mock_get.call_count == 4


@patch("tracker.apify_transport.requests.get")
@patch("tracker.apify_transport.requests.post")
def test_run_actor_and_wait_swallows_failure_to_empty_list_by_default(mock_post, mock_get):
    mock_post.side_effect = Exception("network exploded")
    items = apify_transport.run_actor_and_wait("some/actor", {}, "tok")
    assert items == []


@patch("tracker.apify_transport.requests.get")
@patch("tracker.apify_transport.requests.post")
def test_run_actor_and_wait_raises_when_strict(mock_post, mock_get):
    mock_post.return_value = _resp({"data": {"id": "run1"}})
    mock_get.return_value = _resp({"data": {"status": "FAILED"}})
    try:
        apify_transport.run_actor_and_wait("some/actor", {}, "tok", strict=True, poll_interval=0)
        assert False, "expected ApifyTransportError"
    except apify_transport.ApifyTransportError:
        pass


@patch("tracker.apify_transport.requests.get")
@patch("tracker.apify_transport.requests.post")
def test_run_actor_and_wait_raises_on_timeout_when_strict(mock_post, mock_get):
    mock_post.return_value = _resp({"data": {"id": "run1"}})
    mock_get.return_value = _resp({"data": {"status": "RUNNING"}})
    try:
        apify_transport.run_actor_and_wait("some/actor", {}, "tok", strict=True,
                                           timeout=0, poll_interval=0)
        assert False, "expected ApifyTransportError"
    except apify_transport.ApifyTransportError:
        pass
