"""Claude vision for Social Media Intelligence -- the core of
Step 3 ("understand the creative"): actually look at an image and describe
what's depicted, never infer it from the caption. Mirrors
tracker/lps_enrichment.py's _anthropic() convention: degrades to a clear
error dict on any failure (no key, a timeout, a malformed reply), never
raises -- one bad image must not fail the platform or the run.

SYSTEM_PROMPT and FIELDS are public (not module-private) so the rest of the
pipeline can read the exact question this module asks and the exact fields
it answers, without a second copy of either drifting out of sync."""

from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
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
    "summary: ONE crisp, plain-language sentence (roughly 12-20 words) giving the single "
    "clearest description of what this shows and communicates -- never a paragraph, never "
    "multiple sentences, and never restate the caption verbatim.\n\n"
    "Respond with ONLY a JSON object, no prose before or after: "
    '{"subject": str, "setting": str, "people": str, "product": str, "style": str, '
    '"on_screen_text": str, "messaging": str, "cta": str, "tone": str, "hook": str, '
    '"format_technique": str, "branding": str, "summary": str}. Use empty '
    'strings for fields that do not apply -- never omit a key.'
)

FIELDS = ("subject", "setting", "people", "product", "style", "on_screen_text",
          "messaging", "cta", "tone", "hook", "format_technique", "branding", "summary")

# One reading of one creative: 13 fields of a sentence or two each. 900 was
# the original budget and it is too tight for a text-heavy creative (an
# infographic or a carousel cover whose on_screen_text alone runs long) --
# and a reply cut off mid-JSON does not degrade gracefully, it fails to parse
# and the whole post is reported as unreadable. See _truncated() below: the
# cut-off case is now named rather than silently blamed on the model's
# formatting.
MAX_TOKENS = 1500


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
    return {f: str(parsed.get(f) or "") for f in FIELDS}


# ── Fetching the image ourselves when the vendor cannot ────────────────────
#
# The vendor accepts an image as a URL and fetches it from its own
# infrastructure. That is the cheap path and the one tried first, but it
# depends on a third party being able to reach a link we did not issue, and
# most of what this pipeline collects is a signed, expiring, sometimes
# geo-fenced CDN URL (scontent.cdninstagram.com, video.xx.fbcdn.net,
# pbs.twimg.com, media.licdn.com). When one of those refuses the vendor's
# fetcher, the call comes back as a plain API error and the post is recorded
# as "vendor_call_failed", which looks exactly like a broken integration
# and is not one.
#
# So a failed URL call is retried once with the bytes fetched from here.
# Railway can generally reach these CDNs even when the vendor cannot, and
# this module already has a base64 path built for video frames.
#
# The fetch goes through event_intel_http.public_get rather than plain
# requests, deliberately: these URLs arrive from third-party scrapers, so
# fetching one server-side is an SSRF surface, and that helper is this
# repo's reviewed answer to it (DNS pinning, private and reserved addresses
# refused, every redirect revalidated). It is named for the events agent
# only because that is where it was first needed; nothing in it is specific
# to that feature.
IMAGE_FETCH_TIMEOUT = 20
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_FETCH_UA = ("Mozilla/5.0 (compatible; Position2-Intelligence/1.0; "
             "+https://intelligence.position2.com)")

# What the vendor will actually accept. An image in any other format is
# not worth sending: it would come back as the same error we are retrying.
_MEDIA_TYPES = {"image/jpeg": "image/jpeg", "image/jpg": "image/jpeg",
                "image/pjpeg": "image/jpeg", "image/png": "image/png",
                "image/webp": "image/webp", "image/gif": "image/gif"}


def fetch_image_bytes(url: str) -> tuple[bytes | None, str]:
    """(bytes, media_type) for `url`, or (None, "") on any failure.

    Never raises, and never returns something the vendor cannot read: an
    unknown content type, an empty body, or anything over MAX_IMAGE_BYTES
    is treated as a failure rather than sent on."""
    if not url:
        return None, ""
    resp = None
    try:
        from .event_intel_http import public_get
        resp = public_get(url, timeout=IMAGE_FETCH_TIMEOUT, stream=True, headers={
            "User-Agent": _FETCH_UA,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        })
        if resp.status_code != 200:
            logger.warning("sci_vision: image fetch for %s returned HTTP %s",
                           url, resp.status_code)
            return None, ""
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        media_type = _MEDIA_TYPES.get(ctype)
        if not media_type:
            logger.warning("sci_vision: image fetch for %s served %r, not an image "
                           "the vendor reads", url, ctype)
            return None, ""
        data = resp.raw.read(MAX_IMAGE_BYTES + 1, decode_content=True)
        if not data:
            return None, ""
        if len(data) > MAX_IMAGE_BYTES:
            logger.warning("sci_vision: image at %s is larger than %s bytes",
                           url, MAX_IMAGE_BYTES)
            return None, ""
        return data, media_type
    except Exception as e:
        logger.warning("sci_vision: image fetch for %s failed: %s", url, e)
        return None, ""
    finally:
        try:
            if resp is not None:
                resp.close()
        except Exception:
            pass


def _usable(parsed: dict | None) -> bool:
    """True when a parsed reply actually says something about the creative.

    _parse() coerces a reply into all thirteen FIELDS with `str(x or "")`,
    which means a reply of `{}` -- or one whose keys are all different from
    the ones asked for -- comes back as a complete, well-formed dict of
    thirteen empty strings. Without this check that is stored as
    creative_analysis_status='ok': the post counts toward "Creative
    described", the detail panel prints "Not noted" thirteen times, and the
    synthesis step is handed a reading that contains no information while
    looking exactly like one that does. A vendor that told us nothing has to
    be visible as such."""
    if not parsed:
        return False
    return any((parsed.get(f) or "").strip() for f in FIELDS)


def _truncated(resp) -> bool:
    """Whether the model ran out of output budget mid-reply.

    The reply is a JSON object, so being cut off never yields a shorter
    answer -- it yields an unterminated one that json.loads refuses. Left
    unchecked that surfaces as "the vendor replied, but not in a readable
    shape", which points the reader at the wrong thing entirely: the vendor
    answered fine, we simply did not leave it room to finish. This repo has
    already paid for the same defect once on the events agent (a truncated
    score_batch reply wiped an entire run's results to zero), which is why
    it is named explicitly here rather than inferred."""
    return getattr(resp, "stop_reason", None) == "max_tokens"


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
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
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
        # The vendor could not complete the call on this URL. Very often
        # that is the vendor's own fetch of a signed CDN link being refused
        # rather than anything wrong with the request, so fetch the image
        # here and ask again with the bytes. See fetch_image_bytes.
        data, media_type = fetch_image_bytes(image_url)
        if not data:
            return {"error": "vendor_call_failed"}
        logger.info("sci_vision: retrying %s with bytes fetched here", image_url)
        return analyze_image_bytes(data, media_type=media_type, context=context)

    if _truncated(resp):
        logger.warning("sci_vision: reply for %s was cut off at max_tokens", image_url)
        return {"error": "response_truncated"}
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("sci_vision: unparsable response for %s", image_url)
        return {"error": "unparsable_response"}
    if not _usable(parsed):
        logger.warning("sci_vision: reply for %s described nothing", image_url)
        return {"error": "empty_response"}
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
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
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

    if _truncated(resp):
        logger.warning("sci_vision: a video frame's reply was cut off at max_tokens")
        return {"error": "response_truncated"}
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("sci_vision: unparsable response for a video frame")
        return {"error": "unparsable_response"}
    if not _usable(parsed):
        logger.warning("sci_vision: a video frame's reply described nothing")
        return {"error": "empty_response"}
    return parsed


# A tiny, fully inert 1x1 transparent PNG -- used only to prove the vendor
# round trip end to end (key valid, model reachable, a reply actually
# parses) without depending on any external image URL staying up.
_PROBE_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def probe() -> dict:
    """Prove the vision pass end to end, in the shape app.py's other
    vendor self-tests established (_apollo_selftest, _arena_selftest,
    unipile_client.probe, sci_reddit_client.probe). "ANTHROPIC_API_KEY is
    set" does not prove the key is valid, the model name is one the account
    can reach, or that a reply parses; one small real call does.

    An all-empty reading counts as a FAILURE here, not a pass: see _usable.
    A probe that goes green on a vendor returning nothing would be worse
    than no probe at all."""
    import base64
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    out: dict = {"configured": bool(key), "key_len": len(key),
                 "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
                 "ok": False, "elapsed_ms": 0, "error": ""}
    if not key:
        out["error"] = "ANTHROPIC_API_KEY is not set on this environment."
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


# A small, permanent, publicly reachable image already served by this
# deployment -- used to probe the URL-fetch path specifically, which probe()
# above does NOT exercise: probe() sends the tiny inert image as base64,
# exactly the shape a video frame takes, but every real POST's still image
# goes through analyze_image(image_url), where the vendor fetches the link
# itself server-side (see the module docs above fetch_image_bytes for why
# that is a different, less reliable path than handing over bytes we already
# hold). A deployment can pass probe() -- key valid, model reachable, a
# reply parses -- while every real post still fails, if the vendor's own
# fetch of a signed third-party CDN link (or this repo's SSRF-guarded local
# retry of the same link) is what is actually broken. Pointing at our own
# already-public favicon isolates that question from "is Instagram/TikTok/
# LinkedIn's CDN specifically blocking this" -- it answers "can the vendor
# fetch ANY external URL at all" first, which is the cheaper, more diagnostic
# question to answer before chasing a CDN-specific block.
PROBE_IMAGE_URL = "https://intelligence.position2.com/static/favicon.png"


def probe_url() -> dict:
    """Same proof as probe(), but over analyze_image(url) -- the code path
    every real post's still image actually takes -- rather than
    analyze_image_bytes(). See PROBE_IMAGE_URL above for why this is a
    distinct question from probe()'s."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    out: dict = {"configured": bool(key), "key_len": len(key),
                 "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
                 "probe_url": PROBE_IMAGE_URL,
                 "ok": False, "elapsed_ms": 0, "error": ""}
    if not key:
        out["error"] = "ANTHROPIC_API_KEY is not set on this environment."
        return out
    started = time.monotonic()
    result = analyze_image(PROBE_IMAGE_URL, context={"caption": "self-test probe"})
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if "error" in result:
        out["error"] = result["error"]
        return out
    out["ok"] = True
    out["sample_summary"] = (result.get("summary") or "")[:200]
    return out


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
    one moment "hook" actually describes. summary is capped the same way
    as the messaging-level fields (_dedupe_join, limit=2): every sampled
    frame of one video tends to describe near-identical content in
    slightly different words, so joining all of them (the original shape
    of this line) produced a run-on wall of near-duplicate sentences
    instead of a readable summary -- two is enough to note a real scene
    change without that."""
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
        "summary": _dedupe_join((f.get("summary") for f in ok_frames), limit=2, sep=" / "),
    }
