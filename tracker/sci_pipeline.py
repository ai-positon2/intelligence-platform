"""Orchestration for Social Creative Intelligence Analyst. Keeps app.py thin
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
DEFAULT_MIN_POSTS = 20
LOW_ACTIVITY_THRESHOLD = 3
MAX_VIDEO_FRAMES = 6

# Confidence levels from sci_identify.identify_handles() that are trusted
# enough to actually attempt a scrape. 'low' and 'none' both refuse to guess.
_USABLE_CONFIDENCE = {"high", "medium"}


def _window_posts(posts: list[dict], days: int = DEFAULT_WINDOW_DAYS,
                  min_count: int = DEFAULT_MIN_POSTS) -> list[dict]:
    """The spec's rule applied uniformly across platforms: keep the last
    `days` days of posts, or the most recent `min_count` posts, whichever is
    MORE -- i.e. the union of both rules, not the intersection. Posts with no
    parseable posted_at sort last and count toward the min_count floor only,
    never the days window."""
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
    keep = max(within_window, min_count)
    return [p for p, _ in dated[:keep]]


def _apply_youtube_fallback(result: dict, company_name: str) -> None:
    """Resolve YouTube directly when the identify step didn't. Mutates
    `result` in place; never raises.

    YouTube is the only one of the six platforms with a sanctioned public
    search API, so when identify comes back empty for it there is still an
    authoritative way to find the channel: ask YouTube. Every other platform
    correctly stays gated behind identify, because the only alternative
    there is guessing a handle and scraping whatever it hits.

    This matters most in exactly the case that keeps happening: identify
    fails wholesale (one API call, so all six platforms fail together) and
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


def run_identify(run_id: int, company_name: str, company_url: str | None) -> dict:
    """Step 1. Writes identify_result onto the run row and creates the
    per-platform rows up front (status='identifying' if usable,
    'handle_not_found' if not) so the UI has something to render for every
    platform immediately, before any scraping starts."""
    from tracker import sci_identify, sci_store

    result = sci_identify.identify_handles(company_name, company_url)
    _apply_youtube_fallback(result, company_name)
    sci_store.update_run_status(run_id, "running", identify_result=result)

    for platform, entry in result.items():
        confidence = entry.get("confidence", "none")
        if confidence in _USABLE_CONFIDENCE and entry.get("handle"):
            sci_store.upsert_platform_run(
                run_id, platform, handle=entry["handle"], handle_confidence=confidence,
                status="identifying", status_detail=None)
        else:
            sci_store.upsert_platform_run(
                run_id, platform, handle=entry.get("handle"), handle_confidence=confidence,
                status="handle_not_found",
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


def run_platform_collection(run_id: int, platform: str, handle: str) -> None:
    """Step 2 for one platform. Always terminates that platform's
    sci_platform_runs.status -- ok / low_activity / no_presence /
    scrape_failed -- and never raises past this function; the caller
    (_sci_run_analysis_job) wraps this in its own try/except as a second
    line of defense only."""
    from tracker import sci_store

    sci_store.upsert_platform_run(run_id, platform, status="collecting")
    try:
        if platform == "youtube":
            posts, vendor = _collect_youtube(handle)
        elif platform == "linkedin":
            posts, vendor = _collect_linkedin(handle)
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
        source_vendor=vendor)


def _collect_via_apify(platform: str, handle: str) -> tuple[list[dict], str]:
    import importlib
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN is not configured on this deployment.")
    module = importlib.import_module(f"tracker.{_APIFY_COLLECTORS[platform]}")
    return module.collect(handle, token, strict=True), "apify"


def _collect_linkedin(handle: str) -> tuple[list[dict], str]:
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
            return sci_source_linkedin_unipile.collect(handle, strict=True), "unipile"
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
    return sci_source_linkedin.collect(handle, token, strict=True), "apify"


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
            return sci_source_instagram_unipile.collect(handle, strict=True), "unipile"
        except Exception as e:
            logger.warning("sci_pipeline: Unipile Instagram collection failed for %r, "
                           "falling back to Apify: %s", handle, e)

    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        raise RuntimeError(
            "Instagram collection is unavailable "
            "(no Unipile account connected and APIFY_API_TOKEN is not configured).")
    from tracker import sci_source_instagram
    return sci_source_instagram.collect(handle, token, strict=True), "apify"


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
                                                   max_results=DEFAULT_MIN_POSTS, days=DEFAULT_WINDOW_DAYS)
    return posts, "youtube_api"


def _video_thumbnail_url(post: dict) -> str | None:
    """Best-effort static thumbnail for a video post whose frames couldn't be
    extracted -- most commonly YouTube, where yt-dlp/ffmpeg frame extraction
    (tracker/sci_video.py) frequently gets blocked by YouTube's bot detection
    from a datacenter IP like Railway's, leaving creative_analysis null for
    every video on that platform. YouTube's Data API returns a real
    thumbnail URL for free (tracker/sci_youtube_client.py already stores it
    under raw.snippet.thumbnails); analyzing that instead of giving up keeps
    the post grounded in real visual data rather than leaving it blank.
    Written platform-agnostically so any other adapter that starts
    populating the same raw.snippet.thumbnails shape benefits automatically."""
    thumbs = ((post.get("raw") or {}).get("snippet") or {}).get("thumbnails") or {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbs.get(key)
        url = entry.get("url") if isinstance(entry, dict) else None
        if url:
            return url
    return None


def run_platform_creative_analysis(run_id: int, platform: str) -> None:
    """Step 3 for one platform's already-collected posts, staged per-post so
    one slow/failed video never blocks the rest of the platform. Marks the
    platform row analyzed_at when done, regardless of individual post
    failures -- per-post failure is recorded on the post row itself
    (creative_analysis_status), not surfaced as a platform-level failure."""
    from tracker import sci_store, sci_vision, sci_video, sci_audio

    posts = sci_store.get_posts(run_id, platform)
    for post in posts:
        context = {"caption": post.get("caption", "")}
        post_type = post.get("post_type", "")
        media_urls = post.get("media_urls") or []
        if not media_urls:
            sci_store.update_post_creative_analysis(post["id"], None, status="skipped",
                                                     error="No media URL to analyze.")
            continue
        try:
            if post_type in ("video", "reel", "short"):
                frames = sci_video.extract_frames(media_urls[0], n=MAX_VIDEO_FRAMES)
                if not frames:
                    thumbnail_url = _video_thumbnail_url(post)
                    if not thumbnail_url:
                        sci_store.update_post_creative_analysis(
                            post["id"], None, status="failed", error="Could not extract any video frames.")
                        continue
                    # Frame extraction failed (most commonly YouTube blocking
                    # yt-dlp) -- fall back to the platform's own thumbnail
                    # image rather than leaving this post fully unanalyzed.
                    # No transcript is possible from a single static image.
                    analysis = sci_vision.analyze_image(thumbnail_url, context=context)
                    if "error" not in analysis:
                        analysis["frame_extraction_note"] = (
                            "Video frame extraction was unavailable for this post; "
                            "analyzed the platform-provided thumbnail instead.")
                    status = "failed" if "error" in analysis else "ok"
                    sci_store.update_post_creative_analysis(
                        post["id"], analysis, status=status, error=analysis.get("error"))
                    continue
                frame_analyses = [sci_vision.analyze_image_bytes(f, context=context) for f in frames]
                analysis = sci_vision.summarize_frames(frame_analyses, context=context)
                if "error" not in analysis:
                    # A failed/absent transcript degrades to None here -- it
                    # never turns a working frame analysis into a failure.
                    analysis["dialogue_transcript"] = sci_audio.transcribe_video(media_urls[0])
            else:
                # image or carousel -- analyze the first image; carousel's
                # remaining images are in media_urls for a later phase that
                # wants to describe every slide, not just the cover.
                analysis = sci_vision.analyze_image(media_urls[0], context=context)
            status = "failed" if "error" in analysis else "ok"
            sci_store.update_post_creative_analysis(
                post["id"], analysis, status=status, error=analysis.get("error"))
        except Exception as e:
            logger.warning("sci_pipeline: creative analysis failed for post %s: %s", post["id"], e)
            sci_store.update_post_creative_analysis(post["id"], None, status="failed", error=str(e)[:500])

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
                run_platform_collection(run_id, platform, entry["handle"])
                run_platform_creative_analysis(run_id, platform)
            except Exception as e:
                logger.warning("sci_pipeline: platform %s failed entirely for run %s: %s", platform, run_id, e)
                sci_store.upsert_platform_run(run_id, platform, status="error", status_detail=str(e)[:500])
        run_synthesis(run_id)
        sci_store.update_run_status(run_id, "done")
    except Exception as e:
        logger.warning("sci_pipeline: analysis job failed for run %s: %s", run_id, e)
        sci_store.update_run_status(run_id, "error", error="Analysis could not be completed.")
