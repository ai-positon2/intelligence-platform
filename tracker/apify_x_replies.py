"""X (Twitter) reply adapter for Thought Leader Intelligence's Phase 2
(audience reaction) -- deliberately separate from tracker/sci_source_x.py.

The actor already used there (apidojo/tweet-scraper, "Tweet Scraper V2")
scrapes a profile's own timeline and does not fetch replies at all. This
uses a different, purpose-built actor (apidojo/twitter-replies-scraper)
that takes a tweet id and returns its reply thread. Social Media
Intelligence has no comment-sentiment feature to share this with, so
nothing here touches sci_source_x.py -- same reasoning as this feature's own
tracker/tlpr_reddit_pulse.py being a sibling of sci_reddit_pulse.py rather
than a shared one.
"""

from __future__ import annotations

import logging
import os

from tracker import apify_transport

logger = logging.getLogger(__name__)

# A separate, purpose-built actor -- overridable per deployment, same
# escape hatch tracker/sci_source_x.py's own DEFAULT_ACTOR_ID gives.
DEFAULT_ACTOR_ID = "apidojo/twitter-replies-scraper"


def actor_id() -> str:
    return os.environ.get("TLPR_APIFY_X_REPLIES_ACTOR_ID", DEFAULT_ACTOR_ID)


def build_input(tweet_id: str, max_replies: int = 20) -> dict:
    return {"tweetIds": [str(tweet_id)], "maxItems": max_replies}


def normalize(tweet_id: str, raw_items: list[dict]) -> list[dict]:
    out = []
    tweet_id = str(tweet_id)
    for item in raw_items or []:
        # Keep only items that are actually replies TO this tweet: the
        # actor's own docs describe a conversation-search fallback mode that
        # can surface other tweets from the same thread, not just direct
        # replies, and a reply to someone else's comment is not "how people
        # reacted to this post."
        in_reply_to = str(item.get("inReplyToId") or "").strip()
        if in_reply_to and in_reply_to != tweet_id:
            continue
        cid = str(item.get("id") or "").strip()
        if not cid:
            continue
        author = item.get("author") or {}
        out.append({
            "comment_id": cid,
            "text": item.get("text") or item.get("fullText") or "",
            "author": author.get("userName") or author.get("name"),
            "posted_at": item.get("createdAt"),
            "likes": item.get("likeCount"),
        })
    return out


def collect(tweet_id: str, token: str, max_replies: int = 20, strict: bool = True) -> list[dict]:
    """Scrape + normalize in one call, mirroring sci_source_x.collect()'s
    contract: strict=True raises apify_transport.ApifyTransportError on a
    transport/actor failure, distinct from a clean [] (the post really has
    no replies)."""
    raw_items = apify_transport.run_actor_and_wait(
        actor_id(), build_input(tweet_id, max_replies), token, strict=strict)
    return normalize(tweet_id, raw_items)
