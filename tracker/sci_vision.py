"""Claude vision for Social Creative Intelligence Analyst -- the core of
Step 3 ("understand the creative"): actually look at an image and describe
what's depicted, never infer it from the caption. Mirrors
tracker/lps_enrichment.py's _anthropic() convention: degrades to a clear
error dict on any failure (no key, a timeout, a malformed reply), never
raises -- one bad image must not fail the platform or the run.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a creative analyst describing what is actually depicted in a "
    "social media image, for a competitive-intelligence report. Describe "
    "only what you can see -- subject, setting, people, product, visual "
    "style, and any on-screen text -- never what the caption claims or "
    "implies. If the image shows nothing meaningful (a blank frame, a "
    "loading placeholder, a broken thumbnail), say so plainly instead of "
    "guessing. Respond with ONLY a JSON object, no prose before or after: "
    '{"subject": str, "setting": str, "people": str, "product": str, '
    '"style": str, "on_screen_text": str, "summary": str}. Use empty '
    'strings for fields that do not apply -- never omit a key.'
)

_FIELDS = ("subject", "setting", "people", "product", "style", "on_screen_text", "summary")


def _anthropic():
    """A configured Anthropic client, or None when this environment has no
    key. Mirrors tracker/lps_enrichment.py's _anthropic()."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=60.0, max_retries=1)


def _parse(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return {f: str(parsed.get(f) or "") for f in _FIELDS}


def analyze_image(image_url: str, context: dict | None = None) -> dict:
    """Describe one image. Returns the parsed fields dict on success, or
    {"error": "..."} on any failure -- callers check for the "error" key to
    decide creative_analysis_status, never an exception."""
    client = _anthropic()
    if client is None:
        return {"error": "not_configured"}
    if not image_url:
        return {"error": "no_image_url"}

    context = context or {}
    caption = (context.get("caption") or "")[:500]
    user_text = "Describe what is actually depicted in this image."
    if caption:
        user_text += (" For reference only (do not trust it as fact -- verify against the "
                      f"image itself), the post's caption was: {caption!r}")

    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=700,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": image_url}},
                    {"type": "text", "text": user_text},
                ],
            }],
        )
    except Exception as e:
        logger.warning("sci_vision: analyze_image failed for %s: %s", image_url, e)
        return {"error": "vendor_call_failed"}

    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("sci_vision: unparsable response for %s", image_url)
        return {"error": "unparsable_response"}
    return parsed


def analyze_image_bytes(image_bytes: bytes, media_type: str = "image/jpeg",
                        context: dict | None = None) -> dict:
    """Same as analyze_image, for a locally-extracted frame (sci_video.py)
    that has no public URL of its own -- sent as base64 instead of by URL."""
    client = _anthropic()
    if client is None:
        return {"error": "not_configured"}
    if not image_bytes:
        return {"error": "no_image_bytes"}

    import base64
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    context = context or {}
    caption = (context.get("caption") or "")[:500]
    user_text = "Describe what is actually depicted in this video frame."
    if caption:
        user_text += (" For reference only (do not trust it as fact -- verify against the "
                      f"frame itself), the video's caption was: {caption!r}")

    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=700,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": user_text},
                ],
            }],
        )
    except Exception as e:
        logger.warning("sci_vision: analyze_image_bytes failed: %s", e)
        return {"error": "vendor_call_failed"}

    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("sci_vision: unparsable response for a video frame")
        return {"error": "unparsable_response"}
    return parsed


def summarize_frames(frame_analyses: list[dict], context: dict | None = None) -> dict:
    """Fold several per-frame analyze_image() results (a video's sampled
    frames) into one video-level creative_analysis. Purely mechanical --
    the actual narrative/pacing/dialogue synthesis across frames is
    tracker/sci_classify.py's job in a later phase; this just gives Phase 1
    a usable per-post summary without inventing a second Claude call per
    video yet."""
    ok_frames = [f for f in frame_analyses if "error" not in f]
    if not ok_frames:
        return {"error": "no_frames_analyzed", "frame_count": len(frame_analyses)}
    return {
        "frame_count": len(frame_analyses),
        "frames_analyzed": len(ok_frames),
        "subjects": [f["subject"] for f in ok_frames if f.get("subject")],
        "settings": [f["setting"] for f in ok_frames if f.get("setting")],
        "on_screen_text": [f["on_screen_text"] for f in ok_frames if f.get("on_screen_text")],
        "summary": " / ".join(f["summary"] for f in ok_frames if f.get("summary")),
    }
