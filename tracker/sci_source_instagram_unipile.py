"""Instagram adapter for Social Creative Intelligence Analyst, backed by
Unipile instead of Apify -- tried FIRST by tracker/sci_pipeline.py's
_collect_instagram, ahead of the Apify actor path
(tracker/sci_source_instagram.py).

Field names below (caption.text, video.url/preview_image, like_count,
comment_count) are Unipile's documented shape for this endpoint; carried
through defensively for the same reason tracker/sci_source_linkedin_unipile.py
does -- the live v2 API has already proven to diverge from the docs in its
URL paths, so field-name assumptions get the same "confirm once a real
account is connected" caveat.
"""

from __future__ import annotations

import logging

from tracker import unipile_transport

logger = logging.getLogger(__name__)

PLATFORM = "instagram"


def _post_type(item: dict) -> str:
    if item.get("carousel_media") or item.get("children"):
        return "carousel"
    video = item.get("video") or {}
    if isinstance(video, dict) and video.get("url"):
        return "reel" if item.get("product_type") == "clips" else "video"
    return "image"


def _media_urls(item: dict) -> list:
    urls = []
    video = item.get("video") or {}
    if isinstance(video, dict) and video.get("url"):
        urls.append(video["url"])
    elif item.get("preview_image"):
        urls.append(item["preview_image"])
    elif item.get("image_url"):
        urls.append(item["image_url"])
    for child in item.get("carousel_media") or item.get("children") or []:
        if not isinstance(child, dict):
            continue
        child_video = child.get("video") or {}
        u = child_video.get("url") if isinstance(child_video, dict) else None
        u = u or child.get("preview_image") or child.get("image_url")
        if u:
            urls.append(u)
    return urls


def _caption_text(item: dict) -> str:
    caption = item.get("caption")
    if isinstance(caption, dict):
        return caption.get("text") or ""
    return caption or ""


def normalize(raw_items: list[dict]) -> list[dict]:
    out = []
    for item in raw_items or []:
        pid = str(item.get("id") or item.get("code") or "").strip()
        if not pid:
            continue
        out.append({
            "platform_post_id": pid,
            "post_url": item.get("permalink") or item.get("url") or
                        (f"https://www.instagram.com/p/{item.get('code')}/" if item.get("code") else None),
            "post_type": _post_type(item),
            "caption": _caption_text(item),
            "posted_at": item.get("taken_at") or item.get("posted_at") or item.get("timestamp"),
            "media_urls": _media_urls(item),
            "metrics": {
                "likes": item.get("like_count"),
                "comments": item.get("comment_count"),
                "views": item.get("play_count") or item.get("view_count"),
            },
            "raw": item,
        })
    return out


def collect(handle: str, max_posts: int = 40, strict: bool = True) -> list[dict]:
    """Only ever called by sci_pipeline after it has confirmed an Instagram
    account is connected (see sci_pipeline._collect_instagram). `handle` is
    the Instagram username, passed straight through as the identifier --
    Unipile's Instagram integration authenticates with a real username/
    password session, not the official Graph API, so it can look up any
    public profile's posts, not just the connected account's own."""
    identifier = handle.lstrip("@")
    raw_items = unipile_transport.fetch_posts(
        identifier, PLATFORM, is_company=False, max_posts=max_posts, strict=strict)
    return normalize(raw_items)
