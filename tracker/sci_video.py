"""Video frame extraction for Social Creative Intelligence Analyst, Phase 1
scope: sampled frames only, fed through sci_vision.analyze_image per frame
and folded into a post-level summary by sci_vision.summarize_frames(). Audio
transcription is a later phase (tracker/sci_audio.py).

Shells out to `ffmpeg`/`ffprobe` via subprocess rather than a Python video
library -- installed on the deployed container via nixpacks.toml
([phases.setup] nixPkgs = ["ffmpeg"]), matching how this repo already shells
out for other system tools rather than adding a heavyweight dependency.

One real wrinkle beyond the original plan: ffmpeg can read a direct CDN video
URL (what the Instagram/Facebook/TikTok/X scrapers hand back) but cannot read
a YouTube watch-page URL -- there is no direct media file behind it. This
module resolves a YouTube URL to its direct stream URL via `yt-dlp -g`
first (new dependency: yt-dlp, added to requirements.txt) and feeds ffmpeg
that resolved URL instead, so the extraction call itself stays uniform for
every platform.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_FFPROBE_TIMEOUT = 30
_FFMPEG_TIMEOUT = 30
_YTDLP_TIMEOUT = 30


def _resolve_playable_url(video_url: str) -> str | None:
    """A direct, ffmpeg-readable media URL for `video_url`. Returns the input
    unchanged for a non-YouTube URL (already a direct CDN link); resolves a
    YouTube watch URL via yt-dlp; None if resolution fails."""
    if "youtube.com" not in video_url and "youtu.be" not in video_url:
        return video_url
    try:
        result = subprocess.run(
            ["yt-dlp", "-f", "best[ext=mp4]/best", "-g", video_url],
            capture_output=True, text=True, timeout=_YTDLP_TIMEOUT,
        )
        if result.returncode != 0:
            logger.warning("sci_video: yt-dlp resolution failed for %s: %s",
                           video_url, result.stderr[:300])
            return None
        urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return urls[0] if urls else None
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        logger.warning("sci_video: yt-dlp resolution errored for %s: %s", video_url, e)
        return None


def _probe_duration(playable_url: str) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", playable_url],
            capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, OSError, FileNotFoundError, ValueError) as e:
        logger.warning("sci_video: ffprobe failed for %s: %s", playable_url, e)
        return None


def _extract_frame_at(playable_url: str, timestamp: float) -> bytes | None:
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            result = subprocess.run(
                ["ffmpeg", "-y", "-ss", str(timestamp), "-i", playable_url,
                 "-frames:v", "1", "-q:v", "3", tmp.name],
                capture_output=True, timeout=_FFMPEG_TIMEOUT,
            )
            if result.returncode != 0:
                return None
            tmp.seek(0)
            data = tmp.read()
            return data or None
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        logger.warning("sci_video: ffmpeg frame extraction failed at %ss for %s: %s",
                       timestamp, playable_url, e)
        return None


def extract_frames(video_url: str, n: int = 6) -> list[bytes]:
    """Up to `n` evenly-spaced JPEG frames from `video_url`, as raw bytes.
    [] (not an error) on any failure -- the caller marks that post's
    creative_analysis_status='failed' rather than blocking the platform."""
    playable_url = _resolve_playable_url(video_url)
    if not playable_url:
        return []
    duration = _probe_duration(playable_url)
    if not duration or duration <= 0:
        # Duration probe failed -- still try a single frame near the start
        # rather than giving up on the whole post.
        frame = _extract_frame_at(playable_url, 0.5)
        return [frame] if frame else []

    n = max(1, n)
    step = duration / (n + 1)
    timestamps = [step * (i + 1) for i in range(n)]
    frames = []
    for ts in timestamps:
        frame = _extract_frame_at(playable_url, ts)
        if frame:
            frames.append(frame)
    return frames
