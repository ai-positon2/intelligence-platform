"""Phase 3: audio extraction + transcription for video posts, filling in the
dialogue_transcript that tracker/sci_vision.py's summarize_frames() docstring
flagged as a later phase's job. Reuses tracker/sci_video.py's
_resolve_playable_url so a YouTube watch-page URL resolves to a playable
stream the same way frame extraction already does (via
sci_video.resolve_playable_url) -- ffmpeg cannot read either a bare webpage
URL or an audio-less video, and this module treats both as "no transcript
available" rather than an error that blocks the rest of the post's creative
analysis.

Transcription is OpenAI Whisper, reusing the platform's existing
OPENAI_API_KEY -- no new vendor account needed (see tracker/news_relevance.py
for this repo's other `from openai import OpenAI` usage). Own try/except
throughout: a failed extraction or transcription degrades this one post's
dialogue_transcript to None, never fails the post's creative_analysis as a
whole -- sci_vision's frame descriptions already stand on their own.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

from tracker import sci_video

logger = logging.getLogger(__name__)

# A social post's audio track is always short; this is a generous cap that
# also bounds Whisper cost/latency per call.
_MAX_AUDIO_SECONDS = 120
_FFMPEG_TIMEOUT = 60


def extract_audio(video_url: str) -> bytes | None:
    """Up to _MAX_AUDIO_SECONDS of mono 16kHz mp3 audio from `video_url`, as
    raw bytes. None (not an error) if the URL doesn't resolve, the source
    has no audio track, or extraction fails for any other reason."""
    playable_url = sci_video.resolve_playable_url(video_url)
    if not playable_url:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", playable_url, "-t", str(_MAX_AUDIO_SECONDS),
                 "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", tmp.name],
                capture_output=True, timeout=_FFMPEG_TIMEOUT,
            )
            if result.returncode != 0:
                return None
            tmp.seek(0)
            data = tmp.read()
            return data or None
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        logger.warning("sci_audio: extract_audio failed for %s: %s", video_url, e)
        return None


def _openai():
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key)


def transcribe(audio_bytes: bytes) -> str | None:
    """Whisper transcription of `audio_bytes`. None if there's no key, no
    audio, or the vendor call fails -- never raises. An empty string is a
    valid, meaningful result (a video with no spoken dialogue), distinct
    from None ("could not tell")."""
    if not audio_bytes:
        return None
    client = _openai()
    if client is None:
        return None
    try:
        import io
        buf = io.BytesIO(audio_bytes)
        buf.name = "audio.mp3"
        resp = client.audio.transcriptions.create(model="whisper-1", file=buf)
        return (resp.text or "").strip()
    except Exception as e:
        logger.warning("sci_audio: transcribe failed: %s", e)
        return None


def transcribe_video(video_url: str) -> str | None:
    """extract_audio + transcribe in one call -- what sci_pipeline actually
    calls per video post. A failure anywhere in the chain collapses to None,
    which sci_pipeline writes as a missing dialogue_transcript, never a
    failure of the post's creative_analysis as a whole."""
    audio = extract_audio(video_url)
    if audio is None:
        return None
    return transcribe(audio)
