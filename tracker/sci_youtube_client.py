"""YouTube Data API v3 client for Social Creative Intelligence Analyst.

The one platform with a real, sanctioned public API -- no scraper needed.
Hand-rolled HTTP on `requests` (already a dependency) rather than pulling in
google-api-python-client's service-object machinery, matching
apollo_client.py/arena_client.py's convention. `api_key` is an explicit
parameter on every public function, mirroring apollo_client.py -- the caller
reads YOUTUBE_API_KEY from the environment and decides what an unset key
means for that call site.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

PLATFORM = "youtube"

_BASE_URL = "https://www.googleapis.com/youtube/v3"
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def _get(endpoint: str, api_key: str, **params) -> dict:
    params = {k: v for k, v in params.items() if v is not None}
    params["key"] = api_key
    resp = requests.get(f"{_BASE_URL}/{endpoint}", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def resolve_channel(handle_or_url: str, api_key: str) -> str | None:
    """Resolve a handle, @handle, channel URL, or bare channel ID to a
    channel ID. None (not raised) if it can't be confidently resolved --
    callers map that to sci_platform_runs.status = 'handle_not_found'."""
    value = (handle_or_url or "").strip()
    if not value or not api_key:
        return None

    m = re.search(r"youtube\.com/channel/([A-Za-z0-9_-]{24})", value)
    if m:
        return m.group(1)
    if _CHANNEL_ID_RE.match(value):
        return value

    m = re.search(r"youtube\.com/@([A-Za-z0-9_.-]+)", value)
    handle = m.group(1) if m else value.lstrip("@")

    try:
        data = _get("channels", api_key, part="id", forHandle=handle)
        items = data.get("items") or []
        if items:
            return items[0]["id"]
    except requests.RequestException as e:
        logger.warning("sci_youtube_client: forHandle lookup failed for %s: %s", handle, e)

    try:
        data = _get("search", api_key, part="snippet", type="channel", q=value, maxResults=1)
        items = data.get("items") or []
        if items:
            return items[0]["snippet"]["channelId"]
    except requests.RequestException as e:
        logger.warning("sci_youtube_client: channel search failed for %s: %s", value, e)
    return None


def _uploads_playlist_id(channel_id: str, api_key: str) -> str | None:
    try:
        data = _get("channels", api_key, part="contentDetails", id=channel_id)
        items = data.get("items") or []
        if not items:
            return None
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except (requests.RequestException, KeyError) as e:
        logger.warning("sci_youtube_client: uploads playlist lookup failed for %s: %s", channel_id, e)
        return None


def list_recent_videos(channel_id: str, api_key: str, max_results: int = 20, days: int = 30) -> list[dict]:
    """Normalized recent videos for a channel: the most recent `max_results`
    videos, or everything within the last `days`, whichever is more -- same
    "30 days OR 20 posts, whichever is more" rule the agent spec asks for
    across every platform. [] on any failure, never raised."""
    if not channel_id or not api_key:
        return []
    playlist_id = _uploads_playlist_id(channel_id, api_key)
    if not playlist_id:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    video_ids: list[str] = []
    snippets: dict[str, dict] = {}
    page_token = None
    try:
        while len(video_ids) < max(max_results, 50) and len(video_ids) < 200:
            data = _get("playlistItems", api_key, part="snippet,contentDetails",
                        playlistId=playlist_id, maxResults=50, pageToken=page_token)
            items = data.get("items") or []
            if not items:
                break
            for item in items:
                vid = item.get("contentDetails", {}).get("videoId")
                published = item.get("contentDetails", {}).get("videoPublishedAt")
                if not vid:
                    continue
                video_ids.append(vid)
                snippets[vid] = item.get("snippet", {}) | {"publishedAt": published}
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            oldest_this_page = items[-1].get("contentDetails", {}).get("videoPublishedAt")
            if oldest_this_page and len(video_ids) >= max_results:
                try:
                    if datetime.fromisoformat(oldest_this_page.replace("Z", "+00:00")) < cutoff:
                        break
                except ValueError:
                    pass
    except requests.RequestException as e:
        logger.warning("sci_youtube_client: playlistItems fetch failed for %s: %s", channel_id, e)
        return []

    kept = []
    for vid in video_ids:
        snip = snippets.get(vid, {})
        published_raw = snip.get("publishedAt")
        in_window = False
        if published_raw:
            try:
                in_window = datetime.fromisoformat(published_raw.replace("Z", "+00:00")) >= cutoff
            except ValueError:
                pass
        if in_window or len(kept) < max_results:
            kept.append(vid)
    kept = kept[:max(max_results, sum(1 for v in kept))]

    stats_by_id = _video_stats(kept, api_key)
    out = []
    for vid in kept:
        snip = snippets.get(vid, {})
        stats = stats_by_id.get(vid, {})
        out.append({
            "platform_post_id": vid,
            "post_url": f"https://www.youtube.com/watch?v={vid}",
            "post_type": "short" if _looks_like_short(snip) else "video",
            "caption": snip.get("title", ""),
            "posted_at": snip.get("publishedAt"),
            "media_urls": [f"https://www.youtube.com/watch?v={vid}"],
            "metrics": {
                "views": _to_int(stats.get("viewCount")),
                "likes": _to_int(stats.get("likeCount")),
                "comments": _to_int(stats.get("commentCount")),
            },
            "raw": {"snippet": snip, "statistics": stats},
        })
    return out


def _video_stats(video_ids: list[str], api_key: str) -> dict[str, dict]:
    if not video_ids:
        return {}
    out = {}
    try:
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            data = _get("videos", api_key, part="statistics", id=",".join(batch))
            for item in data.get("items") or []:
                out[item["id"]] = item.get("statistics", {})
    except requests.RequestException as e:
        logger.warning("sci_youtube_client: videos.statistics fetch failed: %s", e)
    return out


def _looks_like_short(snippet: dict) -> bool:
    title = (snippet.get("title") or "").lower()
    return "#shorts" in title or "#short" in title


def _to_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
