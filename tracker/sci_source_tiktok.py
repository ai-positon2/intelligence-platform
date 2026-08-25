"""TikTok adapter for Social Creative Intelligence Analyst -- owns only the
actor id, its input shape, and normalizing its output into the shared post
dict shape (see sci_pipeline.py's module docstring for that shape). Actor
swap is a one-line env var change, never a code change here.

Media URL note: videoMeta.downloadAddr (when present) is a direct, playable
CDN link -- unlike webVideoUrl, which is the tiktok.com watch page and can't
be read by ffmpeg directly. _media_urls() prefers the direct link so
sci_video never needs a TikTok-specific URL-resolution step.
"""

from __future__ import annotations

import logging
import os

from tracker import apify_transport

logger = logging.getLogger(__name__)

PLATFORM = "tiktok"

# clockworks/tiktok-scraper is the most widely used, actively maintained
# TikTok actor on Apify; override per deployment via SCI_APIFY_TIKTOK_ACTOR_ID.
DEFAULT_ACTOR_ID = "clockworks/tiktok-scraper"


def actor_id() -> str:
    return os.environ.get("SCI_APIFY_TIKTOK_ACTOR_ID", DEFAULT_ACTOR_ID)


def build_input(handle: str, max_posts: int = 20) -> dict:
    return {
        "profiles": [handle.lstrip("@")],
        "resultsPerPage": max_posts,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
    }


def _media_urls(item: dict) -> list:
    urls = [u for u in (item.get("mediaUrls") or []) if u]
    if urls:
        return urls
    meta = item.get("videoMeta") or {}
    direct = meta.get("downloadAddr") or item.get("webVideoUrl")
    return [direct] if direct else []


def normalize(raw_items: list[dict]) -> list[dict]:
    out = []
    for item in raw_items or []:
        pid = str(item.get("id") or "").strip()
        if not pid:
            continue
        out.append({
            "platform_post_id": pid,
            "post_url": item.get("webVideoUrl"),
            "post_type": "video",
            "caption": item.get("text") or "",
            "posted_at": item.get("createTimeISO"),
            "media_urls": _media_urls(item),
            "metrics": {
                "likes": item.get("diggCount"),
                "comments": item.get("commentCount"),
                "shares": item.get("shareCount"),
                "views": item.get("playCount"),
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
