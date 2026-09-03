"""LinkedIn adapter for Social Creative Intelligence Analyst -- feature
flagged, unlike every other platform adapter. SCI_APIFY_LINKEDIN_ACTOR_ID
must be set explicitly (no shipped default) because LinkedIn is the platform
most exposed to scraping-detection and ToS enforcement action. actor_id()
returns None when unset, and sci_pipeline treats that as "this platform is
disabled" rather than a transport failure: it never calls apify_transport
here at all, so there is no retry storm against a fragile actor, and killing
this platform needs no deploy -- just unsetting the env var.
"""

from __future__ import annotations

import logging
import os

from tracker import apify_transport

logger = logging.getLogger(__name__)

PLATFORM = "linkedin"


def actor_id() -> str | None:
    return os.environ.get("SCI_APIFY_LINKEDIN_ACTOR_ID") or None


def build_input(handle: str, max_posts: int = 20) -> dict:
    url = handle if handle.startswith("http") else f"https://www.linkedin.com/company/{handle.lstrip('@')}/posts/"
    return {
        "urls": [url],
        "maxPosts": max_posts,
    }


def _post_type(item: dict) -> str:
    images = item.get("images") or item.get("imageUrls") or []
    if item.get("videoUrl") or item.get("video"):
        return "video"
    if len(images) > 1:
        return "carousel"
    return "image" if images else "text"


def _media_urls(item: dict) -> list:
    urls = []
    video = item.get("videoUrl") or item.get("video")
    if video:
        urls.append(video)
    for img in item.get("images") or item.get("imageUrls") or []:
        u = img.get("url") if isinstance(img, dict) else img
        if u:
            urls.append(u)
    return urls


def normalize(raw_items: list[dict]) -> list[dict]:
    out = []
    for item in raw_items or []:
        pid = str(item.get("postId") or item.get("urn") or item.get("id") or "").strip()
        if not pid:
            continue
        out.append({
            "platform_post_id": pid,
            "post_url": item.get("postUrl") or item.get("url"),
            "post_type": _post_type(item),
            "caption": item.get("text") or item.get("commentary") or "",
            "posted_at": item.get("postedAt") or item.get("publishedAt") or item.get("date"),
            "media_urls": _media_urls(item),
            "metrics": {
                "likes": item.get("numLikes") or item.get("likesCount"),
                "comments": item.get("numComments") or item.get("commentsCount"),
                "shares": item.get("numShares") or item.get("sharesCount"),
            },
            "raw": item,
        })
    return out


def collect(handle: str, token: str, max_posts: int = 40, strict: bool = True) -> list[dict]:
    """Only ever called by sci_pipeline after it has confirmed actor_id() is
    set -- see sci_pipeline._collect_linkedin. Assumes a real actor id."""
    raw_items = apify_transport.run_actor_and_wait(
        actor_id(), build_input(handle, max_posts), token, strict=strict)
    return normalize(raw_items)
