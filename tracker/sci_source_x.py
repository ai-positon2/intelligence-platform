"""X (Twitter) adapter for Social Creative Intelligence Analyst -- owns only
the actor id, its input shape, and normalizing its output into the shared
post dict shape (see sci_pipeline.py's module docstring for that shape).
Actor swap is a one-line env var change, never a code change here.
"""

from __future__ import annotations

import logging
import os

from tracker import apify_transport

logger = logging.getLogger(__name__)

PLATFORM = "x"

# apidojo/tweet-scraper ("Tweet Scraper V2") is a widely used, actively
# maintained X actor; override per deployment via SCI_APIFY_X_ACTOR_ID.
DEFAULT_ACTOR_ID = "apidojo/tweet-scraper"


def actor_id() -> str:
    return os.environ.get("SCI_APIFY_X_ACTOR_ID", DEFAULT_ACTOR_ID)


def build_input(handle: str, max_posts: int = 20) -> dict:
    return {
        "twitterHandles": [handle.lstrip("@")],
        "maxItems": max_posts,
        "sort": "Latest",
    }


def _media_items(item: dict) -> list:
    return item.get("media") or (item.get("extendedEntities") or {}).get("media") or []


def _media_urls(item: dict) -> list:
    urls = []
    for m in _media_items(item):
        kind = (m.get("type") or "").lower()
        if kind in ("video", "animated_gif"):
            variants = (m.get("video_info") or {}).get("variants") or m.get("variants") or []
            mp4s = [v for v in variants
                    if (v.get("content_type") or v.get("contentType") or "").endswith("mp4")]
            best = max(mp4s, key=lambda v: v.get("bitrate") or 0, default=None)
            if best and best.get("url"):
                urls.append(best["url"])
        else:
            u = m.get("media_url_https") or m.get("url")
            if u:
                urls.append(u)
    return urls


def _post_type(item: dict) -> str:
    media = _media_items(item)
    if not media:
        return "text"
    if len(media) > 1:
        return "carousel"
    return "video" if (media[0].get("type") or "").lower() in ("video", "animated_gif") else "image"


def normalize(raw_items: list[dict]) -> list[dict]:
    out = []
    for item in raw_items or []:
        pid = str(item.get("id") or item.get("id_str") or item.get("tweetId") or "").strip()
        if not pid:
            continue
        out.append({
            "platform_post_id": pid,
            "post_url": item.get("url") or item.get("twitterUrl"),
            "post_type": _post_type(item),
            "caption": item.get("text") or item.get("fullText") or item.get("full_text") or "",
            "posted_at": item.get("createdAt") or item.get("created_at"),
            "media_urls": _media_urls(item),
            "metrics": {
                "likes": item.get("likeCount") or item.get("favorite_count"),
                "shares": item.get("retweetCount") or item.get("retweet_count"),
                "comments": item.get("replyCount") or item.get("reply_count"),
                "views": item.get("viewCount") or item.get("views"),
            },
            "raw": item,
        })
    return out


def collect(handle: str, token: str, max_posts: int = 40, strict: bool = True) -> list[dict]:
    """Scrape + normalize in one call. strict=True (the pipeline's default)
    raises apify_transport.ApifyTransportError on a transport/actor failure,
    distinct from a clean [] (the account really has no organic posts)."""
    raw_items = apify_transport.run_actor_and_wait(
        actor_id(), build_input(handle, max_posts), token, strict=strict)
    return normalize(raw_items)
