"""LinkedIn adapter for Social Creative Intelligence Analyst, backed by
Unipile instead of Apify -- tried FIRST by tracker/sci_pipeline.py's
_collect_linkedin, ahead of the Apify actor path
(tracker/sci_source_linkedin.py), since a connected Unipile account is a
real authenticated LinkedIn session rather than a scraper actor fighting
LinkedIn's own detection.

Every field read below was confirmed against a real 200 from a live account
on 2026-09-01, against a real company page's 99 most recent posts. The
shape:

    {"object": "Post", "provider": "LINKEDIN",
     "id": "7500213493552427008",
     "social_id": "urn:li:activity:7500213493552427008",
     "share_url": "https://www.linkedin.com/posts/...",
     "date": "15h",                                  <- RELATIVE, not a date
     "parsed_datetime": "2026-08-31T15:30:44.091Z",  <- the real one
     "text": "...",
     "reaction_counter": 4, "comment_counter": 1,
     "repost_counter": 0, "impressions_counter": 0,
     "is_repost": false, "mentions": [],
     "attachments": [{"type": "img"|"video", "url": ..., "size": {...},
                      "unavailable": false, ...}],
     "article": {"title", "url", "picture_url", "published_at", ...} | absent,
     "author": {"public_identifier", "id", "name", "is_company",
                "profile_picture_url"}}

Two of those are worth naming because a plausible reading of them is wrong.
`date` is a relative string ("15h", "3w") and is useless as a timestamp, so
`parsed_datetime` is the field to trust. And `impressions_counter` is 0 on
every post of a page you do not administer -- it is not a real zero, so it
is dropped rather than reported as one.
"""

from __future__ import annotations

import logging
import re

from tracker import unipile_client, unipile_transport

logger = logging.getLogger(__name__)

PLATFORM = "linkedin"

# /company/, /showcase/ and /school/ are all company-shaped pages on
# LinkedIn and all resolve through the same endpoint.
_COMPANY_PATH = re.compile(r"(?:company|showcase|school)/([^/?#]+)", re.I)
_NUMERIC = re.compile(r"^\d+$")


def company_slug(handle: str) -> str:
    """The vanity slug out of whatever the identify step produced.

    That step is a language model told to return "a handle", and it variously
    returns "position2", "@position2", "company/position2", or the full
    profile URL. All four mean the same page, and three of the four are a
    422 from the posts endpoint, so they are normalized here rather than
    trusted."""
    h = (handle or "").strip().strip("@").strip()
    m = _COMPANY_PATH.search(h)
    if m:
        return m.group(1).strip("/")
    # A bare URL with no /company/ segment: take the last path segment.
    if "://" in h or h.lower().startswith("linkedin.com") or h.lower().startswith("www.linkedin.com"):
        tail = h.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
        return tail or h
    return h.split("?")[0].rstrip("/")


def _is_video_attachment(a: dict) -> bool:
    return (a.get("type") or "").lower() == "video" or (a.get("mimetype") or "").startswith("video")


def _usable_attachments(item: dict) -> list[dict]:
    """Attachments that still resolve. LinkedIn marks expired or removed
    media `unavailable: true` and leaves the row in place, so an
    unfiltered read hands the vision step URLs that 404."""
    return [a for a in (item.get("attachments") or [])
            if isinstance(a, dict) and a.get("url") and not a.get("unavailable")]


def _post_type(item: dict) -> str:
    attachments = _usable_attachments(item)
    if any(_is_video_attachment(a) for a in attachments):
        return "video"
    if len(attachments) > 1:
        return "carousel"
    if attachments:
        return "image"
    return "article" if (item.get("article") or {}).get("url") else "text"


def _media_urls(item: dict) -> list:
    """Video URLs first, then images, then an article's cover.

    Order matters, not just membership: sci_pipeline analyzes media_urls[0]
    and nothing else, so on a post carrying both a video and a poster image
    the video has to lead or the whole clip is judged from one still. The
    article cover is included because a link post is otherwise dropped
    entirely as "no media to analyze", and its cover image is the only
    creative it has."""
    attachments = _usable_attachments(item)
    urls = [a["url"] for a in attachments if _is_video_attachment(a)]
    urls += [a["url"] for a in attachments if not _is_video_attachment(a)]
    if not urls:
        cover = (item.get("article") or {}).get("picture_url")
        if cover:
            urls.append(cover)
    return urls


def _caption(item: dict) -> str:
    """The post's own words, plus an article's headline when it links one.

    A link post's `text` is often a one-line teaser while the headline it is
    teasing carries the actual claim, and the synthesis step reads captions
    for messaging. Appending it costs nothing on the posts that have no
    article."""
    text = (item.get("text") or item.get("commentary") or "").strip()
    title = ((item.get("article") or {}).get("title") or "").strip()
    if title and title.lower() not in text.lower():
        return (text + "\n\n" + title).strip() if text else title
    return text


def _metrics(item: dict) -> dict:
    metrics = {
        "likes": item.get("reaction_counter") if item.get("reaction_counter") is not None
                 else item.get("like_count"),
        "comments": item.get("comment_counter") if item.get("comment_counter") is not None
                    else item.get("comment_count"),
        "shares": item.get("repost_counter") if item.get("repost_counter") is not None
                  else item.get("share_count"),
    }
    # Impressions are visible only to a page's own admins; through anyone
    # else's session every post reads 0. Reporting that as a real zero would
    # put "0 impressions" on posts with hundreds of reactions, so an
    # all-zero impressions field is treated as absent rather than measured.
    impressions = item.get("impressions_counter")
    if impressions:
        metrics["impressions"] = impressions
    return metrics


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
            "caption": _caption(item),
            # `date` is deliberately NOT a fallback here: it is a relative
            # string ("3w"), and storing it as posted_at would silently
            # corrupt every date-ordered chart downstream. A post with no
            # parsed_datetime has no known date, and says so.
            "posted_at": item.get("parsed_datetime") or item.get("posted_at"),
            "media_urls": _media_urls(item),
            "metrics": _metrics(item),
            "raw": item,
        })
    return out


def resolve_identifier(handle: str, account_id: str) -> str:
    """The numeric company id the posts endpoint requires.

    The posts endpoint takes an id, never a vanity slug: /users/position2/
    posts answers 422 invalid_recipient while /users/60223/posts answers
    200. This resolve step is what closes that gap, and it lives here rather
    than in unipile_transport because it is LinkedIn-specific -- the
    transport stays identifier-agnostic so the Instagram adapter can keep
    passing a username straight through."""
    slug = company_slug(handle)
    if _NUMERIC.match(slug):
        return slug
    company, err = unipile_client.get_company(slug, account_id)
    if err is not None:
        raise unipile_transport.UnipileTransportError(
            "Could not resolve LinkedIn company %r: %s" % (slug, unipile_client.describe_error(err)))
    return str(company["id"])


def collect(handle: str, max_posts: int = 40, strict: bool = True) -> list[dict]:
    """Only ever called by sci_pipeline after it has confirmed a LinkedIn
    account is connected (see sci_pipeline._collect_linkedin)."""
    account_id = unipile_transport.account_for_platform(PLATFORM)
    if not account_id:
        if strict:
            raise unipile_transport.UnipileTransportError(
                "No working Unipile account is connected for LinkedIn.")
        return []
    try:
        identifier = resolve_identifier(handle, account_id)
    except Exception as e:
        logger.warning("sci_source_linkedin_unipile: identifier resolution failed for %r: %s", handle, e)
        if strict:
            raise
        return []
    raw_items = unipile_transport.fetch_posts(
        identifier, PLATFORM, is_company=True, max_posts=max_posts, strict=strict,
        account_id=account_id)
    return normalize(raw_items)
