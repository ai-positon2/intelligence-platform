"""Instagram adapter for Social Creative Intelligence Analyst, backed by
Unipile instead of Apify -- tried FIRST by tracker/sci_pipeline.py's
_collect_instagram, ahead of the Apify actor path
(tracker/sci_source_instagram.py).

UNCONFIRMED, and deliberately so. As of 2026-09-01 the Unipile workspace
this deployment uses has 17 connected accounts and every one of them is
LinkedIn, so there is no way to see a real Instagram response. What that
does and does not leave verified:

  Confirmed: the route this goes through (GET /api/v1/users/{id}/posts) and
  the list envelope, from the LinkedIn side of the same endpoint. And that
  this adapter is never reached without an account, since
  unipile_client.is_available("instagram") is a live check that currently
  returns False, so Instagram still falls through to Apify exactly as
  before.

  NOT confirmed: every field name below (caption.text, video.url/
  preview_image, like_count, comment_count, carousel_media). These are
  Unipile's documented shape, and this vendor's docs have already been
  wrong once about something as basic as the API path prefix. They are read
  defensively (chained .get() fallbacks) but they are not evidence.

Connect a real Instagram account through the admin Data sources panel, then
check normalize() against one live response before trusting an Instagram
report. tracker/sci_source_linkedin_unipile.py is what this file should look
like afterwards: its docstring quotes the actual payload.
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
