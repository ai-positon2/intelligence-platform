"""Orchestration for Social Media Intelligence. Keeps app.py thin
per repo convention -- every route does a cheap DB call or kicks off
_sci_run_analysis_job in a daemon thread; all the real work lives here.

Shared normalized post-dict shape every platform adapter's collect()/
normalize() returns, and tracker/sci_store.upsert_posts() expects:
    {
        "platform_post_id": str,       # required, unique within (run, platform)
        "post_url": str | None,
        "post_type": str,               # image|video|carousel|reel|short|story|text
        "caption": str,
        "posted_at": str | None,        # ISO 8601
        "media_urls": list[str],        # direct, fetchable URLs (images or video)
        "metrics": dict,                # any of likes/comments/shares/views/saves
        "raw": dict,                     # the untouched scraped/API item
    }

Threading model: one daemon thread per run (_sci_run_analysis_job), looping
over platforms SEQUENTIALLY, each wrapped in its own try/except so one
platform's failure never blanks out another's already-collected results --
see the plan's "Architecture decisions" for why (no connection pooling on
_pg_conn(), no queue infra in this codebase).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 30

# The hard cap on how many of a company's own posts/reels/videos this
# pipeline will ever fetch or keep, per platform, per run -- 25, on explicit
# user request (2026-09-07): "if I type a company's name, it should only
# search/scrape the last 25 posts/reels/etc on all social media platforms."
#
# Governs BOTH ends of collection: it is passed as the actual request size to
# every vendor (Apify's resultsLimit/maxItems/maxPosts, Unipile's max_posts,
# the YouTube Data API's maxResults, Reddit's own-post limit), so nothing is
# even scraped past this count, AND it is the ceiling _window_posts enforces
# afterward as a backstop in case a vendor slightly overshoots what it was
# asked for. Raising it in one place without the other would leave a vendor
# scraping more than gets kept, or (more wastefully) paying to scrape posts
# that only get discarded.
#
# Supersedes what used to be a platform-specific 100-post FLOOR for YouTube
# and Reddit (their free/cheap official APIs made pulling more harmless, so
# there was no reason to settle for a 20-post slice of an active channel).
# That reasoning no longer applies now that every platform is held to the
# same explicit 25-post ceiling; a floor higher than the ceiling would be a
# contradiction, not a bigger minimum.
MAX_POSTS_PER_PLATFORM = 25

LOW_ACTIVITY_THRESHOLD = 3
MAX_VIDEO_FRAMES = 6

# Confidence levels from sci_identify.identify_handles() that are trusted
# enough to actually attempt a scrape. 'low' and 'none' both refuse to guess.
_USABLE_CONFIDENCE = {"high", "medium"}


def _window_posts(posts: list[dict], days: int = DEFAULT_WINDOW_DAYS,
                  min_count: int = MAX_POSTS_PER_PLATFORM,
                  max_count: int = MAX_POSTS_PER_PLATFORM) -> list[dict]:
    """The spec's rule applied uniformly across platforms: keep the last
    `days` days of posts, or the most recent `min_count` posts, whichever is
    MORE -- i.e. the union of both rules, not the intersection -- but never
    more than `max_count`, the per-platform cap. Posts with no parseable
    posted_at sort last and count toward the min_count floor only, never the
    days window.

    With min_count and max_count both defaulting to the same
    MAX_POSTS_PER_PLATFORM, this settles to "the most recent 25 posts,
    period" in the common case; the two are kept as separate parameters
    rather than one, because a future caller may legitimately want a lower
    floor (e.g. to shorten the report for a genuinely low-activity account)
    without touching the ceiling every caller shares."""
    def _parsed_date(p):
        raw = p.get("posted_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None

    dated = [(p, _parsed_date(p)) for p in posts]
    dated.sort(key=lambda pair: pair[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    within_window = sum(1 for _, d in dated if d and d >= cutoff)
    keep = min(max(within_window, min_count), max_count)
    return [p for p, _ in dated[:keep]]


def _apply_youtube_fallback(result: dict, company_name: str) -> None:
    """Resolve YouTube directly when the identify step didn't. Mutates
    `result` in place; never raises.

    YouTube is one of only two platforms here with a sanctioned public
    search API, so when identify comes back empty for it there is still an
    authoritative way to find the channel: ask YouTube. Every other platform
    correctly stays gated behind identify, because the only alternative
    there is guessing a handle and scraping whatever it hits.

    This matters most in exactly the case that keeps happening: identify
    fails wholesale (one API call, so all seven platforms fail together) and
    the entire run returns nothing, even though the one platform that needs
    no scraper at all could have answered on its own."""
    entry = result.get("youtube") or {}
    if entry.get("confidence") in _USABLE_CONFIDENCE and entry.get("handle"):
        return
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return
    try:
        from tracker import sci_youtube_client
        found = sci_youtube_client.resolve_company_channel(company_name, api_key)
    except Exception as e:
        logger.warning("sci_pipeline: YouTube fallback resolution failed for %r: %s", company_name, e)
        return
    if not found:
        return
    # 'medium', never 'high': this is YouTube's own top match for the company
    # name, which is authoritative about what the channel IS but not proof
    # that it is the company's official one rather than a fan channel. The
    # reasoning string says so plainly, since it renders in the report.
    result["youtube"] = {
        "handle": found["handle"],
        "profile_url": found["profile_url"],
        "confidence": "medium",
        "reasoning": ("Matched directly against the YouTube Data API as %s, because the "
                      "identification step did not return a usable channel." % found["title"]),
    }
    logger.info("sci_pipeline: YouTube fallback resolved %r to %s", company_name, found["handle"])


def _apply_reddit_fallback(result: dict, company_name: str) -> None:
    """Resolve Reddit directly when the identify step didn't. Mutates
    `result` in place; never raises. Same reasoning as
    _apply_youtube_fallback: Reddit has a sanctioned API of its own, so it
    does not have to be hostage to a single identify call that fails for all
    seven platforms at once.

    Unlike YouTube's, this fallback never searches. Reddit's search ranks by
    engagement, so searching a company name returns whichever redditor
    talks about it most -- a person, not the brand -- and collecting that
    account's submissions would report a stranger's posts as the company's
    own content. Only an exact u/<name> handle hit counts (see
    sci_reddit_client.resolve_company_account), and most companies
    legitimately have none."""
    entry = result.get("reddit") or {}
    if entry.get("confidence") in _USABLE_CONFIDENCE and entry.get("handle"):
        return
    try:
        from tracker import sci_reddit_client
        if not sci_reddit_client.is_configured():
            return
        found = sci_reddit_client.resolve_company_account(company_name)
    except Exception as e:
        logger.warning("sci_pipeline: Reddit fallback resolution failed for %r: %s", company_name, e)
        return
    if not found:
        return
    result["reddit"] = {
        "handle": found["handle"],
        "profile_url": found["profile_url"],
        "confidence": "medium",
        "reasoning": ("Matched directly against the Reddit API as %s, because the "
                      "identification step did not return a usable account." % found["handle"]),
    }
    logger.info("sci_pipeline: Reddit fallback resolved %r to %s", company_name, found["handle"])


def run_identify(run_id: int, company_name: str, company_url: str | None) -> dict:
    """Step 1. Writes identify_result onto the run row and creates the
    per-platform rows up front (status='identifying' if usable,
    'handle_not_found' if not) so the UI has something to render for every
    platform immediately, before any scraping starts."""
    from tracker import sci_identify, sci_store

    result = sci_identify.identify_handles(company_name, company_url)
    _apply_youtube_fallback(result, company_name)
    _apply_reddit_fallback(result, company_name)
    sci_store.update_run_status(run_id, "running", identify_result=result)

    for platform, entry in result.items():
        confidence = entry.get("confidence", "none")
        # sci_identify.identify_handles (and the YouTube/Reddit fallbacks
        # above) always resolve a profile_url alongside the handle -- this
        # used to be dropped on the floor here, so the account directory
        # ("Every account, in one place") could never link to a platform,
        # however successfully it was identified or collected.
        if confidence in _USABLE_CONFIDENCE and entry.get("handle"):
            sci_store.upsert_platform_run(
                run_id, platform, handle=entry["handle"], handle_confidence=confidence,
                status="identifying", status_detail=None, profile_url=entry.get("profile_url"))
        else:
            sci_store.upsert_platform_run(
                run_id, platform, handle=entry.get("handle"), handle_confidence=confidence,
                status="handle_not_found", profile_url=entry.get("profile_url"),
                status_detail=entry.get("reasoning") or "Could not confidently identify this platform's account.")
    return result


# Same-shape Apify collectors, dispatched generically by _collect_via_apify.
# LinkedIn and Instagram are deliberately NOT in this registry -- both have
# their own _collect_* function that tries a connected Unipile account
# before ever falling back to Apify (see _collect_linkedin/_collect_instagram).
_APIFY_COLLECTORS = {
    "facebook": "sci_source_facebook",
    "tiktok": "sci_source_tiktok",
    "x": "sci_source_x",
}


def _collection_note(status: str, note: dict | None) -> str | None:
    """What to say about which page was read, or None to say nothing.

    Only two situations are worth a reader's attention. A page confirmed by
    its own website needs no caption. An unconfirmed page that produced posts
    is recorded but stays quiet, since the posts themselves are the evidence
    a person will judge. The case that must never pass silently is an
    unconfirmed page that produced NOTHING: rendered bare, that reads as
    "this company does not post on LinkedIn", which is a claim about the
    company rather than what it really is, a claim about a page we are not
    sure is theirs."""
    from tracker import sci_source_linkedin_unipile as ln
    if not note or note.get("verification") == ln.VERIFIED_DOMAIN:
        return None
    if status == "no_presence":
        return ("Read %s, which has no posts. That page could not be confirmed as this "
                "company's own, so this may be the wrong page rather than an empty one."
                % note["page"])
    return "Read %s." % note["page"]


def run_platform_collection(run_id: int, platform: str, handle: str,
                            company_name: str | None = None,
                            company_url: str | None = None) -> None:
    """Step 2 for one platform. Always terminates that platform's
    sci_platform_runs.status -- ok / low_activity / no_presence /
    scrape_failed -- and never raises past this function; the caller
    (_sci_run_analysis_job) wraps this in its own try/except as a second
    line of defense only."""
    from tracker import sci_store

    sci_store.upsert_platform_run(run_id, platform, status="collecting")
    # Which LinkedIn page the posts came from, and how well it was
    # corroborated as this company. None for every other platform, and for
    # LinkedIn served by Apify, which cannot report one.
    note = None
    try:
        if platform == "youtube":
            posts, vendor = _collect_youtube(handle)
        elif platform == "reddit":
            posts, vendor = _collect_reddit(handle)
        elif platform == "linkedin":
            posts, vendor, note = _collect_linkedin(handle, company_name, company_url)
        elif platform == "instagram":
            posts, vendor = _collect_instagram(handle)
        elif platform in _APIFY_COLLECTORS:
            posts, vendor = _collect_via_apify(platform, handle)
        else:
            sci_store.upsert_platform_run(
                run_id, platform, status="scrape_failed",
                status_detail=f"No collector registered for {platform} yet.")
            return
    except Exception as e:
        logger.warning("sci_pipeline: collection failed for run %s platform %s: %s", run_id, platform, e)
        sci_store.upsert_platform_run(run_id, platform, status="scrape_failed", status_detail=str(e)[:500])
        return

    windowed = _window_posts(posts)
    written = sci_store.upsert_posts(run_id, platform, windowed)

    if not posts:
        status = "no_presence"
    elif len(windowed) < LOW_ACTIVITY_THRESHOLD:
        status = "low_activity"
    else:
        status = "ok"

    dated = [p.get("posted_at") for p in windowed if p.get("posted_at")]
    sci_store.upsert_platform_run(
        run_id, platform, status=status, post_count=written,
        last_post_at=max(dated) if dated else None,
        collected_at=datetime.now(timezone.utc).isoformat(),
        source_vendor=vendor,
        status_detail=_collection_note(status, note))


def _collect_via_apify(platform: str, handle: str) -> tuple[list[dict], str]:
    import importlib
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN is not configured on this deployment.")
    module = importlib.import_module(f"tracker.{_APIFY_COLLECTORS[platform]}")
    return module.collect(handle, token, max_posts=MAX_POSTS_PER_PLATFORM, strict=True), "apify"


def _collect_linkedin(handle: str, company_name: str | None = None,
                     company_url: str | None = None) -> tuple[list[dict], str, dict | None]:
    """Unipile-first, Apify-fallback, in that order -- a connected Unipile
    account is a real authenticated LinkedIn session, not a scraper actor
    fighting LinkedIn's own detection, so it's tried first whenever one is
    available. Falls back to the pre-existing Apify path unchanged: an unset
    SCI_APIFY_LINKEDIN_ACTOR_ID still means "disabled" and must never reach
    apify_transport at all -- raising here (before any network call) is what
    lets this platform be killed instantly by unsetting the env var, with no
    deploy and no risk of a retry storm against a fragile, easily-detected
    actor. A Unipile call that raises (a genuinely broken connected account,
    not just "none configured") falls through to Apify rather than failing
    the whole platform outright, same as the account being absent."""
    from tracker import unipile_client
    if unipile_client.is_available("linkedin"):
        from tracker import sci_source_linkedin_unipile
        try:
            posts, note = sci_source_linkedin_unipile.collect_with_page(
                handle, max_posts=MAX_POSTS_PER_PLATFORM, strict=True,
                company_name=company_name, company_url=company_url)
            return posts, "unipile", note
        except sci_source_linkedin_unipile.CompanyMismatch:
            # Deliberately not caught by the fallback below. Every other
            # failure here is "this vendor could not answer", which another
            # vendor might; a handle pointing at the wrong company points
            # there just as squarely through Apify, so retrying would turn a
            # caught mistake into a confidently wrong report.
            raise
        except Exception as e:
            logger.warning("sci_pipeline: Unipile LinkedIn collection failed for %r, "
                           "falling back to Apify: %s", handle, e)

    from tracker import sci_source_linkedin
    if not sci_source_linkedin.actor_id():
        raise RuntimeError(
            "LinkedIn collection is disabled on this deployment "
            "(no Unipile account connected and no Apify actor configured).")
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN is not configured on this deployment.")
    return sci_source_linkedin.collect(
        handle, token, max_posts=MAX_POSTS_PER_PLATFORM, strict=True), "apify", None


def _collect_instagram(handle: str) -> tuple[list[dict], str]:
    """Unipile-first, Apify-fallback -- same shape as _collect_linkedin,
    carved out of the generic _APIFY_COLLECTORS dispatch specifically so
    Instagram can try a connected Unipile account before ever touching
    Apify. See _collect_linkedin's docstring for why a Unipile failure falls
    through to Apify rather than failing the platform outright."""
    from tracker import unipile_client
    if unipile_client.is_available("instagram"):
        from tracker import sci_source_instagram_unipile
        try:
            return sci_source_instagram_unipile.collect(
                handle, max_posts=MAX_POSTS_PER_PLATFORM, strict=True), "unipile"
        except Exception as e:
            logger.warning("sci_pipeline: Unipile Instagram collection failed for %r, "
                           "falling back to Apify: %s", handle, e)

    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        raise RuntimeError(
            "Instagram collection is unavailable "
            "(no Unipile account connected and APIFY_API_TOKEN is not configured).")
    from tracker import sci_source_instagram
    return sci_source_instagram.collect(
        handle, token, max_posts=MAX_POSTS_PER_PLATFORM, strict=True), "apify"


def _collect_youtube(handle: str) -> tuple[list[dict], str]:
    from tracker import sci_youtube_client
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured on this deployment.")
    channel_id = sci_youtube_client.resolve_channel(handle, api_key)
    if not channel_id:
        # Distinct from a real scrape failure: the handle just didn't resolve
        # to a channel. Treated as "found nothing" rather than "failed".
        return [], "youtube_api"
    # Note: list_recent_videos currently degrades a mid-fetch API failure to
    # [] rather than raising, unlike the Apify collectors -- acceptable for
    # Phase 1 since the official API is far less prone to the opaque
    # actor-blocked failures the scrapers see; worth tightening in Phase 4
    # alongside tracker/sci_scraper_registry.py's fallback work if it proves
    # to matter in practice.
    posts = sci_youtube_client.list_recent_videos(channel_id, api_key,
                                                   max_results=MAX_POSTS_PER_PLATFORM, days=DEFAULT_WINDOW_DAYS)
    return posts, "youtube_api"


def _collect_reddit(handle: str) -> tuple[list[dict], str]:
    """Submissions authored by the company's own Reddit account.

    Only owned posts land here. What Reddit says ABOUT the company is a
    different question with a different answer shape (no author, no
    creative to vision-analyze, and it exists for companies that have no
    Reddit account at all), so it is collected separately by
    run_reddit_pulse and stored on the run rather than as posts."""
    from tracker import sci_reddit_client
    if not sci_reddit_client.is_configured():
        raise RuntimeError(
            "Reddit collection is unavailable (REDDIT_CLIENT_ID and "
            "REDDIT_CLIENT_SECRET are not configured on this deployment).")
    username = (handle or "").strip()
    if username.lower().startswith("u/"):
        username = username[2:]
    if not username:
        return [], "reddit_api"
    return sci_reddit_client.list_user_posts(username, limit=MAX_POSTS_PER_PLATFORM), "reddit_api"


def run_reddit_pulse(run_id: int, company_name: str, company_url: str | None) -> None:
    """The brand-conversation half of Reddit, written onto the run row.

    Deliberately outside the per-platform loop: it runs whether or not the
    company has a Reddit account, which is the entire point -- for most
    companies the conversation is the only Reddit signal that exists. Its
    own try/except, because a failure here must leave the seven platforms'
    collected posts and the synthesis completely untouched."""
    from tracker import sci_reddit_pulse, sci_store
    try:
        pulse = sci_reddit_pulse.build_pulse(company_name, company_url)
        sci_store.update_run_status(run_id, "running", reddit_pulse=pulse)
    except Exception as e:
        logger.warning("sci_pipeline: Reddit pulse failed for run %s: %s", run_id, e)


# Post types read frame by frame rather than as a single still. Named once
# because three separate places used to spell the same tuple out inline.
VIDEO_POST_TYPES = ("video", "reel", "short")

# What a URL's own path says it is. Deliberately path-only: a signed CDN
# link carries a query string full of tokens and sizes that would produce
# false matches on either list.
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
                   ".heic", ".heif", ".avif")
_VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi",
                   ".m3u8", ".mpd", ".ts")


def _url_suffix(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        path = urlsplit(url or "").path
    except ValueError:
        return ""
    dot = path.rfind(".")
    slash = path.rfind("/")
    return path[dot:].lower() if dot > slash else ""


def _is_image_url(url: str) -> bool:
    return _url_suffix(url) in _IMAGE_SUFFIXES


def _is_video_url(url: str) -> bool:
    return _url_suffix(url) in _VIDEO_SUFFIXES


def _first_video_url(media_urls: list) -> str | None:
    for u in media_urls or []:
        if _is_video_url(u):
            return u
    return None


# Where each adapter leaves a video's own cover/poster image on the raw item.
# One list rather than a chain of ifs so adding a platform is one line, and
# so the shapes are readable side by side.
#
# It is the SAME question templates/social_media_intelligence.html's
# postThumbnail() answers in JS to draw each post's card, and the two must
# know the same shapes: that resolver is proven (the cards render), so this
# one is written to match it field for field, and
# tests/test_sci_pipeline_creative_target.py fails if the page learns a
# shape this does not. Two copies of one policy is this codebase's most
# repeated bug, and the two cannot share code across the language boundary,
# so the test is the join.
#
# This started life as a YouTube-only lookup (raw.snippet.thumbnails), added
# because yt-dlp/ffmpeg frame extraction is routinely blocked from a
# datacenter IP like Railway's. The problem is that YouTube is not the only
# platform whose frames fail to extract, and it is the only one that was
# given a fallback: an Instagram reel, a TikTok video, a LinkedIn video or a
# Facebook video whose extraction failed was recorded as "Could not extract
# any video frames" by BOTH vendors, while the cover image the platform had
# already handed us sat unused on the same row. On an account that posts
# mostly reels that is most of the creative in the report.
def _poster_candidates(raw: dict) -> list:
    def _dig(*path):
        cur = raw
        for key in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        return cur

    out = []
    # YouTube Data API (tracker/sci_youtube_client.py), best size first.
    thumbs = _dig("snippet", "thumbnails")
    if isinstance(thumbs, dict):
        for key in ("maxres", "standard", "high", "medium", "default"):
            entry = thumbs.get(key)
            if isinstance(entry, dict):
                out.append(entry.get("url"))
    # Instagram via Apify: displayUrl IS the reel cover; a carousel keeps
    # its slides' covers on childPosts.
    out.append(raw.get("displayUrl"))
    for child in raw.get("childPosts") or []:
        if isinstance(child, dict):
            out.append(child.get("displayUrl"))
    # Instagram via Unipile.
    out += [raw.get("preview_image"), raw.get("image_url")]
    for child in (raw.get("carousel_media") or raw.get("children") or []):
        if isinstance(child, dict):
            out += [child.get("preview_image"), child.get("image_url")]
    # TikTok via Apify. `covers` is an object of named sizes, not a list.
    out += [_dig("videoMeta", "coverUrl"), _dig("videoMeta", "originCover"),
            _dig("videoMeta", "dynamicCover"), _dig("videoMeta", "originalCoverUrl"),
            _dig("videoMeta", "cover"), _dig("covers", "default")]
    # LinkedIn. Unipile and the Apify actor describe a post's media
    # completely differently and both shapes land in this one field, so both
    # are read. Unipile: attachments[] of {type: 'img'|'video', url}, where
    # an img row's url IS the displayable image (a video row's url is the
    # mp4, which is what must never be returned here); some also carry a
    # separate poster alongside the video.
    for att in raw.get("attachments") or []:
        if not isinstance(att, dict) or att.get("unavailable"):
            continue
        if (att.get("type") or "").lower() == "img":
            out.append(att.get("url"))
        out += [att.get("preview_url"), att.get("thumbnail_url"), att.get("poster")]
    out.append(_dig("article", "picture_url"))
    # Apify: a flat images/imageUrls array.
    for img in raw.get("images") or raw.get("imageUrls") or []:
        out.append(img.get("url") if isinstance(img, dict) else img)
    # Reddit.
    out.append(raw.get("thumbnail_url"))
    # Facebook via Apify.
    out.append(raw.get("thumbnail"))
    for m in raw.get("media") or []:
        if isinstance(m, dict):
            photo = m.get("photo_image")
            out += [m.get("thumbnail"),
                    photo.get("uri") if isinstance(photo, dict) else None]
    photo = raw.get("photo_image")
    out += [photo.get("uri") if isinstance(photo, dict) else None,
            raw.get("picture"), raw.get("photoUrl")]
    # X: a video's media row carries its poster frame under media_url_https.
    for m in (raw.get("media") or (raw.get("extendedEntities") or {}).get("media") or []):
        if isinstance(m, dict):
            out.append(m.get("media_url_https"))
    return out


def _poster_image_url(post: dict) -> str | None:
    """The platform's own cover image for this post, or None.

    Never returns something that declares itself a video: the whole point is
    to hand a vision model a still it can actually read. A candidate with no
    file extension at all is accepted, since plenty of CDN image URLs have
    none."""
    raw = post.get("raw") or {}
    if not isinstance(raw, dict):
        return None
    for url in _poster_candidates(raw):
        if isinstance(url, str) and url.strip() and not _is_video_url(url):
            return url.strip()
    return None


def _still_image_url(post: dict, media_urls: list) -> str | None:
    """The single still to analyze for a post that is not read frame by frame.

    Was `media_urls[0]`, unconditionally, which is wrong whenever the leading
    media URL is a video while the post is not typed as one. That really
    happens: an Instagram carousel is typed "carousel" the moment it has
    childPosts even if the parent item carries a videoUrl, and an X or
    Facebook post with several media is typed "carousel" whichever kind
    leads. In each of those cases an .mp4 URL was being sent to two vision
    models, which is a guaranteed failure at both of them."""
    for u in media_urls or []:
        if _is_image_url(u):
            return u
    poster = _poster_image_url(post)
    if poster:
        return poster
    first = (media_urls or [None])[0]
    # No candidate announces itself either way: the leading URL is still the
    # best guess (and the long-standing behaviour), unless it announces
    # itself a video, in which case the caller reads the post as one.
    return None if (first and _is_video_url(first)) else first


def _run_claude_creative_analysis(post_id: int, post_type: str, media_urls: list,
                                  context: dict, frames: list | None, thumbnail_url: str | None,
                                  image_url: str | None = None) -> None:
    """The creative-analysis pass for one post: own try/except, own store
    call, takes the already-extracted frames/thumbnail_url from the caller
    rather than re-deriving them. This used to run alongside a second,
    independent ChatGPT-vision pass (see git history) -- that second opinion
    was removed for consistently lower-quality output, so this is now the
    only vision pass a post gets."""
    from tracker import sci_store, sci_vision, sci_audio

    try:
        if post_type in VIDEO_POST_TYPES:
            if frames:
                frame_analyses = [sci_vision.analyze_image_bytes(f, context=context) for f in frames]
                analysis = sci_vision.summarize_frames(frame_analyses, context=context)
                if "error" not in analysis:
                    # A failed/absent transcript degrades to None here -- it
                    # never turns a working frame analysis into a failure.
                    # Same URL choice as frame extraction: the video, not
                    # whichever media URL happens to lead.
                    analysis["dialogue_transcript"] = sci_audio.transcribe_video(
                        _first_video_url(media_urls) or media_urls[0])
            elif thumbnail_url:
                # Frame extraction failed (most commonly YouTube blocking
                # yt-dlp) -- fall back to the platform's own thumbnail image
                # rather than leaving this post fully unanalyzed. No
                # transcript is possible from a single static image.
                analysis = sci_vision.analyze_image(thumbnail_url, context=context)
                if "error" not in analysis:
                    analysis["frame_extraction_note"] = (
                        "Video frame extraction was unavailable for this post; "
                        "analyzed the platform-provided thumbnail instead.")
            else:
                sci_store.update_post_creative_analysis(
                    post_id, None, status="failed", error="Could not extract any video frames.")
                return
        else:
            # image or carousel -- analyze the still the caller picked (see
            # _still_image_url; NOT blindly media_urls[0], which can be a
            # video on a post that is not typed as one). The carousel's
            # remaining images stay in media_urls for a later phase that
            # wants to describe every slide, not just the cover.
            analysis = sci_vision.analyze_image(image_url, context=context)
    except Exception as e:
        logger.warning("sci_pipeline: Claude creative analysis failed for post %s: %s", post_id, e)
        sci_store.update_post_creative_analysis(post_id, None, status="failed", error=str(e)[:500])
        return

    status = "failed" if "error" in analysis else "ok"
    sci_store.update_post_creative_analysis(post_id, analysis, status=status, error=analysis.get("error"))


def _extract_video_evidence(post: dict, media_urls: list) -> tuple[list | None, str | None]:
    """Frame extraction (or its thumbnail fallback), done ONCE per post --
    there is exactly one real video to read regardless of how many vendors
    go on to analyze it. An extraction that raises is treated the same as
    one that returns [] (attempt the thumbnail fallback instead of losing
    the post outright); the caller's two vendor passes each still report
    their own 'Could not extract any video frames' if even the thumbnail
    lookup comes up empty."""
    from tracker import sci_video
    # The video, not just the first media URL: LinkedIn deliberately puts a
    # video ahead of its poster image, but nothing guarantees every adapter
    # does, and handing ffmpeg a JPEG to sample frames from is wasted work.
    video_url = _first_video_url(media_urls) or media_urls[0]
    try:
        frames = sci_video.extract_frames(video_url, n=MAX_VIDEO_FRAMES)
    except Exception as e:
        logger.warning("sci_pipeline: frame extraction failed for post %s: %s", post.get("id"), e)
        frames = None
    if frames:
        return frames, None
    return None, _poster_image_url(post)


def run_platform_creative_analysis(run_id: int, platform: str) -> None:
    """Step 3 for one platform's already-collected posts, staged per-post so
    one slow/failed video never blocks the rest of the platform. Marks the
    platform row analyzed_at when done, regardless of individual post
    failures -- per-post failure is recorded on the post row itself
    (creative_analysis_status), not surfaced as a platform-level failure."""
    from tracker import sci_store

    posts = sci_store.get_posts(run_id, platform)
    for post in posts:
        context = {"caption": post.get("caption", "")}
        post_type = post.get("post_type", "")
        media_urls = post.get("media_urls") or []
        if not media_urls:
            sci_store.update_post_creative_analysis(post["id"], None, status="skipped",
                                                     error="No media URL to analyze.")
            continue

        # How to read this creative comes from what the post actually
        # carries, not from its type label alone. A post typed image or
        # carousel whose only media is a video (an Instagram carousel with a
        # videoUrl on the parent item, for one) has to be sampled as a
        # video; sending its .mp4 URL to two vision models, which is what
        # the type label alone did, fails at both of them every time.
        image_url = _still_image_url(post, media_urls)
        read_as_video = post_type in VIDEO_POST_TYPES or (
            image_url is None and _first_video_url(media_urls) is not None)
        effective_type = post_type if post_type in VIDEO_POST_TYPES else (
            "video" if read_as_video else post_type)

        frames, thumbnail_url = None, None
        if read_as_video:
            frames, thumbnail_url = _extract_video_evidence(post, media_urls)

        _run_claude_creative_analysis(post["id"], effective_type, media_urls, context,
                                      frames, thumbnail_url, image_url)

    sci_store.upsert_platform_run(run_id, platform, analyzed_at=datetime.now(timezone.utc).isoformat())


def run_synthesis(run_id: int) -> None:
    """Steps 4 + 5 for the whole run, after every platform has finished
    collecting and analyzing. Own try/except -- a classify or synthesize
    failure must never fail the run itself; it still completes with
    whatever platform/post data it collected, just without a synthesis
    section (the per-platform posts render either way)."""
    from tracker import sci_classify, sci_synthesize, sci_store

    try:
        classify_result = sci_classify.classify_patterns(run_id)
        synthesis = sci_synthesize.synthesize_report(run_id, classify_result)
        sci_store.update_run_status(run_id, "running", synthesis=synthesis)
    except Exception as e:
        logger.warning("sci_pipeline: synthesis failed for run %s: %s", run_id, e)


def _sci_run_analysis_job(run_id: int, email: str, company_name: str, company_url: str | None) -> None:
    """The actual thread target -- all inputs explicit, never touches Flask's
    session/request/g (they're gone once the request that started this
    thread has returned). Outer try/except writes status='error' on any
    uncaught failure so nothing dies silently in the daemon thread; each
    platform gets its own try/except inside so one platform's exception
    can't take down the others."""
    from tracker import sci_store

    try:
        identify_result = run_identify(run_id, company_name, company_url)
        for platform, entry in identify_result.items():
            if entry.get("confidence") not in _USABLE_CONFIDENCE or not entry.get("handle"):
                continue
            try:
                run_platform_collection(run_id, platform, entry["handle"],
                                        company_name=company_name, company_url=company_url)
                run_platform_creative_analysis(run_id, platform)
            except Exception as e:
                logger.warning("sci_pipeline: platform %s failed entirely for run %s: %s", platform, run_id, e)
                sci_store.upsert_platform_run(run_id, platform, status="error", status_detail=str(e)[:500])
        run_reddit_pulse(run_id, company_name, company_url)
        run_synthesis(run_id)
        sci_store.update_run_status(run_id, "done")
    except Exception as e:
        logger.warning("sci_pipeline: analysis job failed for run %s: %s", run_id, e)
        sci_store.update_run_status(run_id, "error", error="Analysis could not be completed.")
