"""tracker/apify_x_replies.py -- the reply-thread adapter Thought Leader
Intelligence's Phase 2 uses, separate from tracker/sci_source_x.py (whose
actor does not scrape replies at all)."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import apify_x_replies as src  # noqa: E402


def test_build_input_targets_the_tweet_by_id():
    assert src.build_input("123", max_replies=15) == {"tweetIds": ["123"], "maxItems": 15}


def test_normalize_maps_a_genuine_reply():
    items = [{"id": "r1", "inReplyToId": "123", "text": "Great point!",
             "author": {"userName": "alex"}, "createdAt": "2026-08-01T00:00:00Z",
             "likeCount": 4}]
    out = src.normalize("123", items)
    assert out == [{"comment_id": "r1", "text": "Great point!", "author": "alex",
                    "posted_at": "2026-08-01T00:00:00Z", "likes": 4}]


def test_normalize_drops_an_item_replying_to_a_different_tweet():
    """The actor's own docs describe a conversation-search fallback that can
    surface other tweets from the same thread, not just direct replies to
    the one requested -- one of those is not 'how people reacted to this
    post' and must not be counted as if it were."""
    items = [{"id": "r2", "inReplyToId": "999", "text": "unrelated"}]
    assert src.normalize("123", items) == []


def test_normalize_skips_items_with_no_id():
    assert src.normalize("123", [{"text": "no id"}]) == []


def test_normalize_falls_back_to_full_text_and_name():
    items = [{"id": "r3", "inReplyToId": "123", "fullText": "long form reply",
             "author": {"name": "Alex Rivera"}}]
    out = src.normalize("123", items)
    assert out[0]["text"] == "long form reply"
    assert out[0]["author"] == "Alex Rivera"


@patch("tracker.apify_x_replies.apify_transport.run_actor_and_wait")
def test_collect_passes_strict_through_to_the_transport(mock_run):
    mock_run.return_value = []
    src.collect("123", "tok", strict=True)
    assert mock_run.call_args.kwargs.get("strict") is True


@patch("tracker.apify_x_replies.apify_transport.run_actor_and_wait")
def test_collect_uses_the_configured_actor_and_input(mock_run):
    mock_run.return_value = []
    src.collect("123", "tok", max_replies=10)
    args, kwargs = mock_run.call_args
    assert args[0] == src.DEFAULT_ACTOR_ID
    assert args[1] == {"tweetIds": ["123"], "maxItems": 10}
    assert args[2] == "tok"


def test_actor_id_is_overridable_per_deployment(monkeypatch):
    monkeypatch.setenv("TLPR_APIFY_X_REPLIES_ACTOR_ID", "custom/actor")
    assert src.actor_id() == "custom/actor"
