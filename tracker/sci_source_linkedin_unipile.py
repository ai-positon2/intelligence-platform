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
from urllib.parse import urlparse

from tracker import sci_name_match, unipile_client, unipile_transport

logger = logging.getLogger(__name__)

PLATFORM = "linkedin"

# How well the page we landed on is corroborated as the company being
# researched. LinkedIn hands out one vanity slug per page and reuses names
# freely, so "the slug resolved" is not the same claim as "this is them":
# /company/notion is a 39-person IT consultancy with no posts, while Notion
# Labs is /company/notionhq. Both are called "Notion", both resolve, and one
# of them answers with an empty list that reads as "they post nothing".
VERIFIED_DOMAIN = "domain"      # the page's own website is the company's
VERIFIED_NAME = "name"          # the names agree, nothing corroborates further
VERIFIED_NONE = "none"          # nothing to check against
VERIFIED_MISMATCH = "mismatch"  # positive evidence this is a different company


class CompanyMismatch(Exception):
    """The resolved LinkedIn page belongs to someone else.

    Raised rather than returned, and deliberately NOT a
    UnipileTransportError: a transport failure is worth retrying through the
    other vendor, whereas a wrong handle produces exactly the same wrong page
    on any vendor, so sci_pipeline lets this one through instead of falling
    back to Apify with it."""

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


def _host(url: str) -> str:
    """A URL's bare host, lowercased, without a leading www."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(a: str, b: str) -> bool:
    """Whether two hosts belong to the same site.

    Suffix containment rather than "compare the last two labels": the latter
    reads acme.co.uk and rival.co.uk as the same site, which is a false
    confirmation in exactly the case this check exists to prevent. This
    accepts blog.hubspot.com against hubspot.com and nothing looser."""
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def verify_company_page(page: dict, company_name: str | None,
                        company_url: str | None) -> str:
    """How well `page` is corroborated as the company being researched.

    The website is the decisive signal and the name is the weak one, which is
    the opposite of what it looks like: two unrelated companies share a name
    routinely, and the impostor page that prompted this check shares one
    exactly. A differing domain is only treated as proof of a mismatch when
    the name disagrees too, because a real company's LinkedIn page listing a
    parent or campaign domain is ordinary."""
    page_site = _host((page or {}).get("website") or "")
    company_site = _host(company_url or "")
    page_name = (page or {}).get("name") or ""
    names_agree = bool(company_name and page_name
                       and sci_name_match.plausible_match(company_name, page_name))

    if page_site and company_site:
        if _same_site(page_site, company_site):
            return VERIFIED_DOMAIN
        return VERIFIED_NAME if names_agree else VERIFIED_MISMATCH
    if not company_name:
        return VERIFIED_NONE
    return VERIFIED_NAME if names_agree else VERIFIED_MISMATCH


def describe_company_page(page: dict) -> str:
    """The page we actually read, in the words a reader needs to recognise a
    wrong one on sight. Follower count earns its place here: it is not
    evidence on its own, but 882 followers under a household name is the
    thing a person spots instantly."""
    page = page or {}
    slug = page.get("public_identifier") or page.get("id") or "?"
    bits = []
    if page.get("name"):
        bits.append(str(page["name"]))
    followers = page.get("followers_count")
    if isinstance(followers, int):
        bits.append("{:,} followers".format(followers))
    bits.append(str(page["website"]) if page.get("website") else "no website listed")
    return "linkedin.com/company/%s (%s)" % (slug, ", ".join(bits))


def resolve_identifier(handle: str, account_id: str) -> str:
    """The numeric company id the posts endpoint requires.

    The posts endpoint takes an id, never a vanity slug: /users/position2/
    posts answers 422 invalid_recipient while /users/60223/posts answers
    200. This resolve step is what closes that gap, and it lives here rather
    than in unipile_transport because it is LinkedIn-specific -- the
    transport stays identifier-agnostic so the Instagram adapter can keep
    passing a username straight through."""
    page = resolve_company_page(handle, account_id)
    return str(page["id"])


def resolve_company_page(handle: str, account_id: str) -> dict:
    """The company page `handle` names, as a full profile rather than just an
    id, so the caller can check WHICH page it got. A numeric handle skips the
    lookup and carries only its id, which is also all a numeric handle can
    ever be checked against."""
    slug = company_slug(handle)
    if _NUMERIC.match(slug):
        return {"id": slug, "public_identifier": slug}
    company, err = unipile_client.get_company(slug, account_id)
    if err is not None:
        raise unipile_transport.UnipileTransportError(
            "Could not resolve LinkedIn company %r: %s" % (slug, unipile_client.describe_error(err)))
    return company


def collect_with_page(handle: str, max_posts: int = 40, strict: bool = True,
                      company_name: str | None = None,
                      company_url: str | None = None) -> tuple[list[dict], dict | None]:
    """Posts, plus a note recording which LinkedIn page they came from and how
    well that page was corroborated.

    The note is the whole point of this variant. An empty post list means two
    completely different things depending on it: "this company does not post
    on LinkedIn", or "we read some other company's page". Nothing downstream
    can tell those apart without being told which page was read."""
    account_id = unipile_transport.account_for_platform(PLATFORM)
    if not account_id:
        if strict:
            raise unipile_transport.UnipileTransportError(
                "No working Unipile account is connected for LinkedIn.")
        return [], None
    try:
        page = resolve_company_page(handle, account_id)
    except CompanyMismatch:
        raise
    except Exception as e:
        logger.warning("sci_source_linkedin_unipile: identifier resolution failed for %r: %s", handle, e)
        if strict:
            raise
        return [], None

    verification = verify_company_page(page, company_name, company_url)
    note = {"verification": verification, "page": describe_company_page(page),
            "public_identifier": page.get("public_identifier"),
            "page_name": page.get("name"), "website": page.get("website"),
            "followers": page.get("followers_count")}
    if verification == VERIFIED_MISMATCH:
        raise CompanyMismatch(
            "%s does not look like %s: the page reads %s. Nothing was collected from it, "
            "because reading the wrong company's posts is worse than reading none."
            % (company_slug(handle), company_name or "this company", note["page"]))

    raw_items = unipile_transport.fetch_posts(
        str(page["id"]), PLATFORM, is_company=True, max_posts=max_posts, strict=strict,
        account_id=account_id)
    return normalize(raw_items), note


def collect(handle: str, max_posts: int = 40, strict: bool = True,
            company_name: str | None = None, company_url: str | None = None) -> list[dict]:
    """Only ever called by sci_pipeline after it has confirmed a LinkedIn
    account is connected (see sci_pipeline._collect_linkedin). Posts only;
    use collect_with_page when the caller can act on which page was read."""
    return collect_with_page(handle, max_posts=max_posts, strict=strict,
                             company_name=company_name, company_url=company_url)[0]
