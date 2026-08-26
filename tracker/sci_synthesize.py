"""Step 5 for Social Creative Intelligence Analyst: turn sci_classify's
mechanical pattern data into the cited, readable report. One Claude call,
given the per-platform pattern summary plus a compact digest of every
analyzed post (what sci_vision actually saw, the post's own metrics and
URL), so every claim it writes can point at real posts.

Every claim carries post_ids: list[int] that must resolve to real
sci_posts.id values from this run -- _parse()/_clean_claims() strip any id
the model didn't actually receive rather than trust its citations blindly,
and drop a claim entirely if nothing it cited survives. This is the literal
implementation of "back every claim with 2-3 concrete example posts."

Mirrors tracker/sci_vision.py's _anthropic()/degrade-to-error-dict
convention: never raises, one failure here must not fail the whole run.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a creative-intelligence analyst writing a report on a company's "
    "organic social content, platform by platform and then across platforms. "
    "You are given, for each platform, a pattern summary (format mix, "
    "recurring visual/production themes, which posts got the most engagement) "
    "and a digest of individual posts. Each post's \"what_was_seen\" is what "
    "Claude vision actually saw -- not the caption -- and includes both "
    "visual fields (subject, setting, people, product, style, on_screen_text: "
    "grounded strictly in what's depicted) and messaging fields (messaging, "
    "cta, tone, hook, format_technique, branding: grounded in the depicted "
    "creative AND the post's real caption/description copy, since that's the "
    "brand's own words, not a guess).\n\n"
    "Write for someone skimming on a screen, not reading a print essay: "
    "SHORT, SCANNABLE BULLET POINTS, never a dense paragraph. For each "
    "platform with real activity, produce TWO distinct bulleted lists (do "
    "not blend them):\n"
    "- summary: 2-5 short bullets, each ONE focused, self-contained idea in "
    "plain language (one or two sentences), describing what the content "
    "actually looks like and shows.\n"
    "- messaging_and_strategy: 2-5 short analytical bullets, each ONE "
    "focused idea. Across the list, cover: what value proposition(s) and "
    "message pillars recur; what tone/brand voice comes through; what "
    "production technique(s) dominate (UGC vs. studio vs. talking-head vs. "
    "meme-format vs. testimonial, etc.); how hooks and CTAs are actually "
    "used; and -- critically -- what specifically about the creative or "
    "messaging differs between the platform's top-engaging posts and its "
    "weaker ones. Be concrete and specific to this company's actual "
    "content; never write generic marketing advice that could apply to any "
    "brand.\n\n"
    "Never write a raw post id inline in summary or messaging_and_strategy "
    "text (e.g. never write \"(id 12, 13)\") -- a normal reader has no idea "
    "what that means. Citations belong ONLY in claims, below, where the "
    "reader will see them rendered as real linked post cards (thumbnail, "
    "title, date), never bare numbers.\n\n"
    "Ground every claim in what was actually seen, and every claim must cite "
    "2-3 real post ids from the digest that support it, using the \"id\" "
    "field exactly as given -- never invent an id. If a platform has little "
    "or no organic activity (a status of no_presence, low_activity, "
    "handle_not_found, scrape_failed, or error), say so plainly as a single "
    "summary bullet instead of fabricating patterns from nothing, and leave "
    "messaging_and_strategy as an empty list for that platform -- there is "
    "nothing real to analyze. Respond with ONLY a JSON object, no prose "
    "before or after:\n"
    '{"platforms": {"<platform>": {"summary": [str, ...], '
    '"messaging_and_strategy": [str, ...], "claims": [{"text": str, '
    '"post_ids": [int, ...]}]}}, "cross_platform": {"summary": [str, ...], '
    '"messaging_and_strategy": [str, ...], "claims": [{"text": str, '
    '"post_ids": [int, ...]}]}}'
)


def _anthropic():
    """Mirrors tracker/sci_vision.py's _anthropic()."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=90.0, max_retries=1)


def _post_digest(post: dict) -> dict:
    analysis = post.get("creative_analysis") or {}
    return {
        "id": post["id"],
        "platform": post.get("platform"),
        "post_type": post.get("post_type"),
        "post_url": post.get("post_url"),
        "metrics": post.get("metrics") or {},
        "what_was_seen": analysis if analysis and "error" not in analysis else None,
    }


def _clean_points(points) -> list[str]:
    """summary/messaging_and_strategy are now bulleted lists, not a single
    paragraph -- but tolerate a bare string too (a model slip, or an older
    stored run's shape) by treating it as one bullet, rather than dropping
    it or crashing."""
    if isinstance(points, str):
        points = [points] if points.strip() else []
    if not isinstance(points, list):
        return []
    return [text for text in (str(p or "").strip() for p in points) if text]


def _clean_claims(claims, valid_ids: set) -> list:
    out = []
    for c in claims or []:
        if not isinstance(c, dict):
            continue
        text = str(c.get("text") or "").strip()
        ids = [i for i in (c.get("post_ids") or []) if isinstance(i, int) and i in valid_ids]
        if text and ids:
            out.append({"text": text, "post_ids": ids[:3]})
    return out


def _parse(raw: str, valid_ids: set) -> dict | None:
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

    platforms = {}
    for platform, entry in (parsed.get("platforms") or {}).items():
        if not isinstance(entry, dict):
            continue
        platforms[platform] = {
            "summary": _clean_points(entry.get("summary")),
            "messaging_and_strategy": _clean_points(entry.get("messaging_and_strategy")),
            "claims": _clean_claims(entry.get("claims"), valid_ids),
        }
    cross = parsed.get("cross_platform") or {}
    cross_platform = {
        "summary": _clean_points(cross.get("summary")),
        "messaging_and_strategy": _clean_points(cross.get("messaging_and_strategy")),
        "claims": _clean_claims(cross.get("claims"), valid_ids),
    }
    return {"platforms": platforms, "cross_platform": cross_platform}


def synthesize_report(run_id: int, classify_result: dict) -> dict:
    """Returns the synthesis dict to be written onto sci_runs.synthesis, or
    a clear {"error": ...} dict on any failure -- never raises.
    classify_result is sci_classify.classify_patterns(run_id)'s output,
    passed in rather than re-fetched so the caller controls exactly what was
    classified."""
    from tracker import sci_store

    client = _anthropic()
    if client is None:
        return {"error": "not_configured"}

    posts = sci_store.get_posts(run_id)
    if not posts:
        return {"error": "no_posts_to_synthesize"}
    valid_ids = {p["id"] for p in posts}
    platform_runs = sci_store.get_platform_runs(run_id)

    payload = {
        "platform_status": {
            pr["platform"]: {"status": pr["status"], "status_detail": pr.get("status_detail"),
                             "post_count": pr.get("post_count")}
            for pr in platform_runs
        },
        "patterns": {k: v for k, v in classify_result.items() if k != "_all"},
        "cross_platform_patterns": classify_result.get("_all", {}),
        "posts": [_post_digest(p) for p in posts],
    }

    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=6000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload)[:180000]}],
        )
    except Exception as e:
        logger.warning("sci_synthesize: synthesize_report failed for run %s: %s", run_id, e)
        return {"error": "vendor_call_failed"}

    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse(raw, valid_ids)
    if parsed is None:
        logger.warning("sci_synthesize: unparsable synthesis response for run %s", run_id)
        return {"error": "unparsable_response"}
    return parsed
