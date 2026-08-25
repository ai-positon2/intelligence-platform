"""Instagram adapter for Social Creative Intelligence Analyst -- owns only the
actor id, its input shape, and normalizing its output into the shared post
dict shape (see sci_pipeline.py's module docstring for that shape). Actor
swap (if this one gets deprecated or blocked) is a one-line env var change,
never a code change here.
"""

from __future__ import annotations

import logging
import os

from tracker import apify_transport

logger = logging.getLogger(__name__)

PLATFORM = "instagram"

# apify/instagram-scraper is Apify's own official actor; override per
# deployment via SCI_APIFY_INSTAGRAM_ACTOR_ID if it needs to be swapped.
DEFAULT_ACTOR_ID = "apify/instagram-scraper"


def actor_id() -> str:
    return os.environ.get("SCI_APIFY_INSTAGRAM_ACTOR_ID", DEFAULT_ACTOR_ID)


def build_input(handle: str, max_posts: int = 20) -> dict:
    return {
        "directUrls": [f"https://www.instagram.com/{handle.lstrip('@')}/"],
        "resultsType": "posts",
        "resultsLimit": max_posts,
    }


def _post_type(item: dict) -> str:
    t = (item.get("type") or "").lower()
    if item.get("childPosts"):
        return "carousel"
    if t == "video" and item.get("productType") == "clips":
        return "reel"
    if t == "video":
        return "video"
    if t == "sidecar":
        return "carousel"
    return "image"


def normalize(raw_items: list[dict]) -> list[dict]:
    out = []
    for item in raw_items or []:
        pid = str(item.get("id") or item.get("shortCode") or "").strip()
        if not pid:
            continue
        media_urls = []
        if item.get("videoUrl"):
            media_urls.append(item["videoUrl"])
        elif item.get("displayUrl"):
            media_urls.append(item["displayUrl"])
        for child in item.get("childPosts") or []:
            child_url = child.get("videoUrl") or child.get("displayUrl")
            if child_url:
                media_urls.append(child_url)
        out.append({
            "platform_post_id": pid,
            "post_url": item.get("url") or (f"https://www.instagram.com/p/{item.get('shortCode')}/"
                                            if item.get("shortCode") else None),
            "post_type": _post_type(item),
            "caption": item.get("caption") or "",
            "posted_at": item.get("timestamp"),
            "media_urls": media_urls,
            "metrics": {
                "likes": item.get("likesCount"),
                "comments": item.get("commentsCount"),
                "views": item.get("videoViewCount") or item.get("videoPlayCount"),
            },
            "raw": item,
        })
    return out


def collect(handle: str, token: str, max_posts: int = 40, strict: bool = True) -> list[dict]:
    """Scrape + normalize in one call. With strict=True (the pipeline's
    default), a transport/actor failure raises apify_transport.
    ApifyTransportError -- distinct from a clean [] (the actor ran fine and
    the profile really has nothing), which the pipeline maps to
    'no_presence' rather than 'scrape_failed'."""
    raw_items = apify_transport.run_actor_and_wait(
        actor_id(), build_input(handle, max_posts), token, strict=strict)
    return normalize(raw_items)
