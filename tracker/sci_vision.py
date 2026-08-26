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
    "You are a creative and messaging analyst describing one social media post for a "
    "competitive-intelligence report -- both what is visually depicted AND what message "
    "is being communicated.\n\n"
    "VISUAL fields (subject, setting, people, product, style, on_screen_text) -- describe "
    "ONLY what you can actually see in the image. Never infer these from the caption. If "
    "the image shows nothing meaningful (a blank frame, a loading placeholder, a broken "
    "thumbnail), say so plainly instead of guessing.\n\n"
    "MESSAGING fields (messaging, cta, tone, hook, format_technique, branding) -- these "
    "MAY draw on the post's real caption/description text as well as the image, since "
    "that caption is the brand's own first-party copy, not a guess:\n"
    "- messaging: the core value proposition, offer, or idea being communicated.\n"
    "- cta: the specific call-to-action shown or stated (e.g. \"Shop now\", \"Link in bio\", "
    "\"Book a demo\") -- empty string if there genuinely isn't one.\n"
    "- tone: the emotional/brand voice in one or two words (e.g. playful, authoritative, "
    "urgent, aspirational, technical, irreverent).\n"
    "- hook: whatever is designed to grab attention in the first instant -- the opening "
    "visual, headline, or question. For a video frame, only fill this in if the frame IS "
    "the video's opening moment; otherwise leave it empty.\n"
    "- format_technique: the production style (e.g. UGC-style, studio product shot, "
    "talking-head, text-meme, screen recording, animated/motion graphic, customer "
    "testimonial, behind-the-scenes, carousel infographic).\n"
    "- branding: visible logo, brand colors, or other identifiable brand elements -- "
    "empty string if none are visible.\n\n"
    "Respond with ONLY a JSON object, no prose before or after: "
    '{"subject": str, "setting": str, "people": str, "product": str, "style": str, '
    '"on_screen_text": str, "messaging": str, "cta": str, "tone": str, "hook": str, '
    '"format_technique": str, "branding": str, "summary": str}. Use empty '
    'strings for fields that do not apply -- never omit a key.'
)

_FIELDS = ("subject", "setting", "people", "product", "style", "on_screen_text",
          "messaging", "cta", "tone", "hook", "format_technique", "branding", "summary")


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
    caption = (context.get("caption") or "")[:1200]
    user_text = "Analyze this image: describe what is depicted, and read its messaging and creative approach."
    if caption:
        user_text += (" The post's real caption/description (usable for the messaging fields, "
                      f"not for the visual fields): {caption!r}")

    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=900,
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
    caption = (context.get("caption") or "")[:1200]
    user_text = "Analyze this video frame: describe what is depicted, and read its messaging and creative approach."
    if caption:
        user_text += (" The video's real caption/description (usable for the messaging fields, "
                      f"not for the visual fields): {caption!r}")

    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=900,
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


def _dedupe_join(values, limit: int = 3, sep: str = "; ") -> str:
    """Unique, order-preserving, non-empty values folded into one string --
    used for the messaging-level fields (messaging/cta/tone/format_technique/
    branding) that are attributes of the whole video rather than a single
    frame, so repeating the same value once per sampled frame would just be
    noise."""
    seen = []
    for v in values:
        v = (v or "").strip()
        if v and v not in seen:
            seen.append(v)
        if len(seen) >= limit:
            break
    return sep.join(seen)


def summarize_frames(frame_analyses: list[dict], context: dict | None = None) -> dict:
    """Fold several per-frame analyze_image_bytes() results (a video's
    sampled frames) into one video-level creative_analysis. Purely
    mechanical -- the actual narrative synthesis across posts is
    tracker/sci_classify.py + tracker/sci_synthesize.py's job; this just
    gives Step 3 a usable per-post summary without a second Claude call per
    video.

    subject/setting/on_screen_text stay per-frame lists since what's on
    screen genuinely changes shot to shot. messaging/cta/tone/
    format_technique/branding are whole-video attributes (a single ad has
    one core message, one voice) so they're deduplicated into one string --
    this also keeps their key names identical to analyze_image()'s
    single-image shape, so callers never need to branch on post_type to
    read them. hook is taken from the OPENING frame only, since that's the
    one moment "hook" actually describes."""
    ok_frames = [f for f in frame_analyses if "error" not in f]
    if not ok_frames:
        return {"error": "no_frames_analyzed", "frame_count": len(frame_analyses)}
    return {
        "frame_count": len(frame_analyses),
        "frames_analyzed": len(ok_frames),
        "subjects": [f["subject"] for f in ok_frames if f.get("subject")],
        "settings": [f["setting"] for f in ok_frames if f.get("setting")],
        "on_screen_text": [f["on_screen_text"] for f in ok_frames if f.get("on_screen_text")],
        "messaging": _dedupe_join(f.get("messaging") for f in ok_frames),
        "cta": _dedupe_join(f.get("cta") for f in ok_frames),
        "tone": _dedupe_join(f.get("tone") for f in ok_frames),
        "format_technique": _dedupe_join(f.get("format_technique") for f in ok_frames),
        "branding": _dedupe_join(f.get("branding") for f in ok_frames),
        "hook": ok_frames[0].get("hook", ""),
        "summary": " / ".join(f["summary"] for f in ok_frames if f.get("summary")),
    }
