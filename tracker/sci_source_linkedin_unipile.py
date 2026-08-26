"""LinkedIn adapter for Social Creative Intelligence Analyst, backed by
Unipile instead of Apify -- tried FIRST by tracker/sci_pipeline.py's
_collect_linkedin, ahead of the Apify actor path
(tracker/sci_source_linkedin.py), since a connected Unipile account is a
real authenticated LinkedIn session rather than a scraper actor fighting
LinkedIn's own detection.

Field names below (reaction_counter, comment_counter, repost_counter,
impressions_counter, an `attachments` array of {url, mimetype,
url_expires_at}) are Unipile's documented v1 shape for this endpoint; the
live v2 API has already proven to differ from the docs in its URL paths
(see tracker/unipile_client.py's module docstring), so these field names are
carried through defensively (chained .get() fallbacks, same style as
tracker/sci_source_linkedin.py) rather than assumed exact -- confirm against
a real response once a LinkedIn account is actually connected.
"""

from __future__ import annotations

import logging

from tracker import unipile_transport

logger = logging.getLogger(__name__)

PLATFORM = "linkedin"


def _is_video_attachment(a: dict) -> bool:
    return (a.get("mimetype") or "").startswith("video") or a.get("type") == "video"


def _post_type(item: dict) -> str:
    attachments = [a for a in (item.get("attachments") or []) if isinstance(a, dict)]
    if any(_is_video_attachment(a) for a in attachments):
        return "video"
    if len(attachments) > 1:
        return "carousel"
    return "image" if attachments else "text"


def _media_urls(item: dict) -> list:
    urls = []
    for a in item.get("attachments") or []:
        u = a.get("url") if isinstance(a, dict) else a
        if u:
            urls.append(u)
    return urls


def normalize(raw_items: list[dict]) -> list[dict]:
    out = []
    for item in raw_items or []:
        pid = str(item.get("id") or item.get("social_id") or item.get("urn") or "").strip()
        if not pid:
            continue
        out.append({
            "platform_post_id": pid,
            "post_url": item.get("share_url") or item.get("post_url") or item.get("url"),
            "post_type": _post_type(item),
            "caption": item.get("text") or item.get("commentary") or "",
            "posted_at": item.get("parsed_datetime") or item.get("date") or item.get("posted_at"),
            "media_urls": _media_urls(item),
            "metrics": {
                "likes": item.get("reaction_counter") or item.get("like_count"),
                "comments": item.get("comment_counter") or item.get("comment_count"),
                "shares": item.get("repost_counter") or item.get("share_count"),
                "impressions": item.get("impressions_counter"),
            },
            "raw": item,
        })
    return out


def collect(handle: str, max_posts: int = 40, strict: bool = True) -> list[dict]:
    """Only ever called by sci_pipeline after it has confirmed a LinkedIn
    account is connected (see sci_pipeline._collect_linkedin). `handle` is
    passed straight through as the identifier -- if Unipile's live endpoint
    turns out to need a separate public-identifier-to-internal-id resolve
    step (its docs suggested one; the live API's exact behavior here is
    unconfirmed, see tracker/unipile_client.py's _POSTS_PATH docstring),
    that resolution belongs here, not in unipile_transport, which stays
    identifier-agnostic."""
    identifier = handle.lstrip("@")
    raw_items = unipile_transport.fetch_posts(
        identifier, PLATFORM, is_company=True, max_posts=max_posts, strict=strict)
    return normalize(raw_items)
