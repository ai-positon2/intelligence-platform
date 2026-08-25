"""Facebook adapter for Social Creative Intelligence Analyst -- owns only the
actor id, its input shape, and normalizing its output into the shared post
dict shape (see sci_pipeline.py's module docstring for that shape). Actor
swap is a one-line env var change, never a code change here.
"""

from __future__ import annotations

import logging
import os

from tracker import apify_transport

logger = logging.getLogger(__name__)

PLATFORM = "facebook"

# apify/facebook-posts-scraper is Apify's own official actor; override per
# deployment via SCI_APIFY_FACEBOOK_ACTOR_ID if it needs to be swapped.
DEFAULT_ACTOR_ID = "apify/facebook-posts-scraper"


def actor_id() -> str:
    return os.environ.get("SCI_APIFY_FACEBOOK_ACTOR_ID", DEFAULT_ACTOR_ID)


def build_input(handle: str, max_posts: int = 20) -> dict:
    url = handle if handle.startswith("http") else f"https://www.facebook.com/{handle.lstrip('@')}/"
    return {
        "startUrls": [{"url": url}],
        "resultsLimit": max_posts,
    }


def _post_type(item: dict) -> str:
    media = item.get("media") or []
    if len(media) > 1:
        return "carousel"
    if media:
        kind = (media[0].get("__typename") or media[0].get("type") or "").lower()
        return "video" if "video" in kind else "image"
    if item.get("videoUrl") or item.get("video_url"):
        return "video"
    if item.get("photo_image") or item.get("picture") or item.get("photoUrl"):
        return "image"
    return "text"


def _media_urls(item: dict) -> list:
    urls = []
    for m in item.get("media") or []:
        photo = m.get("photo_image")
        u = (photo.get("uri") if isinstance(photo, dict) else None) or m.get("url") \
            or m.get("videoUrl") or m.get("thumbnail")
        if u:
            urls.append(u)
    if not urls:
        photo = item.get("photo_image")
        single = item.get("videoUrl") or item.get("video_url") \
            or (photo.get("uri") if isinstance(photo, dict) else None) \
            or item.get("picture") or item.get("photoUrl")
        if single:
            urls.append(single)
    return urls


def normalize(raw_items: list[dict]) -> list[dict]:
    out = []
    for item in raw_items or []:
        pid = str(item.get("postId") or item.get("post_id") or item.get("id") or "").strip()
        if not pid:
            continue
        out.append({
            "platform_post_id": pid,
            "post_url": item.get("url") or item.get("postUrl"),
            "post_type": _post_type(item),
            "caption": item.get("text") or item.get("message") or "",
            "posted_at": item.get("time") or item.get("timestamp") or item.get("date"),
            "media_urls": _media_urls(item),
            "metrics": {
                "likes": item.get("likes"),
                "comments": item.get("comments"),
                "shares": item.get("shares"),
            },
            "raw": item,
        })
    return out


def collect(handle: str, token: str, max_posts: int = 40, strict: bool = True) -> list[dict]:
    """Scrape + normalize in one call. strict=True (the pipeline's default)
    raises apify_transport.ApifyTransportError on a transport/actor failure,
    distinct from a clean [] (the page really has no organic posts)."""
    raw_items = apify_transport.run_actor_and_wait(
        actor_id(), build_input(handle, max_posts), token, strict=strict)
    return normalize(raw_items)
