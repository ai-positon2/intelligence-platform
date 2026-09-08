"""ChatGPT (OpenAI) vision for Social Creative Intelligence Analyst -- a
SECOND, independent creative-analysis pass, run alongside tracker/sci_vision.py's
existing Claude pass, on the user's explicit request (2026-09-07): "add a
layer of ChatGPT Vision to read and analyze each creative... understand the
communication and messaging style". Reuses the platform's existing
OPENAI_API_KEY -- no new vendor account needed (see tracker/sci_audio.py for
this repo's other `from openai import OpenAI` usage in this same feature).

Deliberately asks the IDENTICAL question, in the identical schema, as
sci_vision.py -- importing SYSTEM_PROMPT and FIELDS from there rather than
keeping a second copy, since a "second opinion" only means something if both
vendors were asked the same thing. Only the vendor and wire format differ:
OpenAI's chat.completions API takes an image as an `image_url` content part
(a real URL or a base64 data: URI, unlike Anthropic's separate url/base64
source types), and JSON mode (response_format={"type":"json_object"}) is
used to get a reliably parseable reply rather than relying on prompting alone.

Mirrors sci_vision.py's contract exactly: degrades to {"error": "..."} on
any failure (no key, a timeout, a malformed reply), never raises -- one bad
image must not fail the platform, the run, or Claude's own already-stored
analysis of the same post. summarize_frames() has no vendor-specific logic
(pure aggregation over already-parsed per-frame dicts), so the OpenAI video
path reuses tracker/sci_vision.summarize_frames directly rather than
duplicating it.
"""

from __future__ import annotations

import json
import logging
import os
import time

from tracker import sci_vision

logger = logging.getLogger(__name__)

# gpt-4o-mini is vision-capable and is this platform's existing default for
# every other OpenAI text task (see OPENAI_MODEL's use across app.py) --
# picked here for the same reason: a second full vision pass on top of
# Claude's already doubles this run's vision spend, so the cheaper capable
# model is the right default. Override with OPENAI_VISION_MODEL for gpt-4o
# if the higher-quality model is worth the added cost for a given deployment.
DEFAULT_MODEL = "gpt-4o-mini"


def _openai():
    """A configured OpenAI client, or None when this environment has no key.
    timeout/max_retries mirror tracker/sci_vision.py's _anthropic() -- without
    an explicit timeout the SDK default is a 600s read timeout with 2
    retries, so one slow call could run for the better part of an hour. That
    would matter for any OpenAI client, but especially for this one: run_
    platform_creative_analysis runs this vendor's call concurrently with
    Claude's own (bounded to 60s) and waits on both, so an unbounded ChatGPT
    call becomes the run's long pole instead of a red herring next to it."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key, timeout=60.0, max_retries=1)


def _model() -> str:
    return os.environ.get("OPENAI_VISION_MODEL", DEFAULT_MODEL)


def _parse(raw: str) -> dict | None:
    """Same defensive shape as sci_vision._parse: JSON mode makes a fenced
    reply unlikely here, but stripping one costs nothing and keeps this
    module honest about not simply trusting the vendor's own contract."""
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
    return {f: str(parsed.get(f) or "") for f in sci_vision.FIELDS}


def _truncated(resp) -> bool:
    """OpenAI's spelling of the same condition sci_vision._truncated names:
    finish_reason == "length" means the reply hit max_tokens. It matters
    more here, not less, than on the Claude side: this call runs in JSON
    mode, so a cut-off reply is guaranteed to be an unterminated object that
    json.loads refuses, every time."""
    try:
        return (resp.choices[0].finish_reason or "") == "length"
    except (AttributeError, IndexError, TypeError):
        return False


def _user_text(subject: str, caption: str) -> str:
    text = "Analyze this %s: describe what is depicted, and read its messaging and creative approach." % subject
    if caption:
        text += (" The post's real caption/description (usable for the messaging fields, "
                "not for the visual fields): %r" % caption)
    return text


def analyze_image(image_url: str, context: dict | None = None) -> dict:
    """Describe one image -- the OpenAI counterpart to sci_vision.analyze_image,
    same return shape: the parsed FIELDS dict on success, or {"error": "..."}
    on any failure."""
    client = _openai()
    if client is None:
        return {"error": "not_configured"}
    if not image_url:
        return {"error": "no_image_url"}

    context = context or {}
    caption = (context.get("caption") or "")[:1200]

    try:
        resp = client.chat.completions.create(
            model=_model(),
            max_tokens=sci_vision.MAX_TOKENS,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sci_vision.SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                    {"type": "text", "text": _user_text("image", caption)},
                ]},
            ],
        )
    except Exception as e:
        logger.warning("sci_vision_openai: analyze_image failed for %s: %s", image_url, e)
        # Same retry, same reason, same shared fetcher as the Claude pass:
        # a refused vendor-side fetch of a signed CDN link is not a broken
        # integration, and it fails identically at both vendors, which is
        # exactly what makes it look like one. See
        # tracker/sci_vision.fetch_image_bytes.
        data, media_type = sci_vision.fetch_image_bytes(image_url)
        if not data:
            return {"error": "vendor_call_failed"}
        logger.info("sci_vision_openai: retrying %s with bytes fetched here", image_url)
        return analyze_image_bytes(data, media_type=media_type, context=context)

    if _truncated(resp):
        logger.warning("sci_vision_openai: reply for %s was cut off at max_tokens", image_url)
        return {"error": "response_truncated"}
    raw = (resp.choices[0].message.content or "") if resp.choices else ""
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("sci_vision_openai: unparsable response for %s", image_url)
        return {"error": "unparsable_response"}
    if not sci_vision._usable(parsed):
        logger.warning("sci_vision_openai: reply for %s described nothing", image_url)
        return {"error": "empty_response"}
    return parsed


def analyze_image_bytes(image_bytes: bytes, media_type: str = "image/jpeg",
                        context: dict | None = None) -> dict:
    """Same as analyze_image, for a locally-extracted video frame (the SAME
    bytes tracker/sci_video.py already extracted for Claude's pass -- the
    caller reuses them rather than re-downloading/re-decoding the video a
    second time) that has no public URL of its own -- sent as a base64 data
    URI, OpenAI's own convention for an inline image."""
    client = _openai()
    if client is None:
        return {"error": "not_configured"}
    if not image_bytes:
        return {"error": "no_image_bytes"}

    import base64
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    data_url = "data:%s;base64,%s" % (media_type, b64)
    context = context or {}
    caption = (context.get("caption") or "")[:1200]

    try:
        resp = client.chat.completions.create(
            model=_model(),
            max_tokens=sci_vision.MAX_TOKENS,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sci_vision.SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    {"type": "text", "text": _user_text("video frame", caption)},
                ]},
            ],
        )
    except Exception as e:
        logger.warning("sci_vision_openai: analyze_image_bytes failed: %s", e)
        return {"error": "vendor_call_failed"}

    if _truncated(resp):
        logger.warning("sci_vision_openai: a video frame's reply was cut off at max_tokens")
        return {"error": "response_truncated"}
    raw = (resp.choices[0].message.content or "") if resp.choices else ""
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("sci_vision_openai: unparsable response for a video frame")
        return {"error": "unparsable_response"}
    if not sci_vision._usable(parsed):
        logger.warning("sci_vision_openai: a video frame's reply described nothing")
        return {"error": "empty_response"}
    return parsed


# The probe image lives in tracker/sci_vision.py and is imported here rather
# than copied: both vendors' self-tests must be answering the same question
# about the same input, and two byte strings that are meant to be identical
# are two things that can stop being identical.
_PROBE_IMAGE_B64 = sci_vision._PROBE_IMAGE_B64


def probe() -> dict:
    """Prove the ChatGPT-vision integration end to end, in the shape app.py's
    other vendor self-tests (_apollo_selftest, _arena_selftest, unipile_
    client.probe, sci_reddit_client.probe) established. Costs one small real
    vision call against the tiny probe image above -- "OPENAI_API_KEY is
    set" alone does not prove the key is valid, the model is reachable, or
    that a reply actually parses; this does."""
    import base64
    key = os.environ.get("OPENAI_API_KEY", "")
    out: dict = {"configured": bool(key), "key_len": len(key), "model": _model(),
                "ok": False, "elapsed_ms": 0, "error": ""}
    if not key:
        out["error"] = "OPENAI_API_KEY is not set on this environment."
        return out
    started = time.monotonic()
    result = analyze_image_bytes(base64.b64decode(_PROBE_IMAGE_B64), media_type="image/png",
                                 context={"caption": "self-test probe"})
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if "error" in result:
        out["error"] = result["error"]
        return out
    out["ok"] = True
    out["sample_summary"] = (result.get("summary") or "")[:200]
    return out


# summarize_frames is intentionally NOT redefined here -- tracker.sci_vision.
# summarize_frames takes a list of already-parsed FIELDS dicts and does pure
# aggregation (dedupe/join/first-frame-hook), with no vendor-specific logic
# at all, so sci_pipeline calls it directly on this module's frame results
# too. Re-implementing it here would be a second copy of policy (which
# fields fold vs. list, which frame the hook comes from) that could silently
# drift from Claude's version.
