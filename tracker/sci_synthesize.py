"""Step 5 for Social Media Intelligence: turn sci_classify's
mechanical pattern data into the cited, readable report. One Claude call,
given the per-platform pattern summary plus a compact digest of every
analyzed post (each post's "vision" object carries BOTH Claude's and
ChatGPT's independent reads, see _post_digest -- this is the merge point
where the two vendors' opinions actually reach the written narrative, not
just the word-cloud classification sci_classify.py already pools), so every
claim it writes can point at real posts.

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
    "and a digest of individual posts. Each post's \"vision\" object carries "
    "TWO INDEPENDENT reads of the same creative, from two different vision "
    "models that never saw each other's answer: \"claude\" and \"chatgpt\". "
    "Either may be null (that vendor was not run, or failed on this post) -- "
    "when only one is present, treat it exactly as you would have treated a "
    "single reading; when BOTH are present, they are your strongest evidence "
    "for a claim, since two independent models landing on the same read is "
    "meaningfully more reliable than either alone. If the two genuinely "
    "disagree on something material (a different subject, a different tone, "
    "a different read on the CTA), do not silently pick one -- either note "
    "the disagreement in messaging_and_strategy when it is significant enough "
    "to matter to the reader, or favor the reading multiple posts corroborate "
    "over one that stands alone; never fabricate an artificial consensus. "
    "Each vision reading includes both visual fields (subject, setting, "
    "people, product, style, on_screen_text: grounded strictly in what's "
    "depicted) and messaging fields (messaging, cta, tone, hook, "
    "format_technique, branding: grounded in the depicted creative AND the "
    "post's real caption/description copy, since that's the brand's own "
    "words, not a guess).\n\n"
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
    "field exactly as given -- never invent an id.\n\n"
    "The payload's \"evidence\" block says how many of the run's posts are "
    "actually included below: when posts_included is lower than posts_total, "
    "the digest is the run's best-evidenced and most-engaging posts rather "
    "than all of them, so write about the pattern they show and never state "
    "or imply a count of the company's posts.\n\n"
    "If a platform has little "
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


# How much of the run's evidence is sent, and how much room the reply gets.
#
# PAYLOAD_CHAR_BUDGET replaces a `json.dumps(payload)[:180000]` slice that
# cut the payload mid-string. That slice did not send less evidence, it sent
# BROKEN evidence: with 25 posts allowed per platform (sci_pipeline.
# MAX_POSTS_PER_PLATFORM) and two vision readings per post, four active
# platforms already serialize past 180,000 characters, so the model was
# handed a JSON document that ends inside a quoted string and never closes.
# _select_digests below drops whole posts instead, so what arrives is always
# valid and the payload says plainly how many posts it represents.
#
# The number itself is also raised from that slice's 180,000. The old figure
# was low enough to discard most of a busy run's evidence -- the expensive
# part of this pipeline, two vision readings per post, already paid for --
# while 480,000 characters is roughly 120k tokens, which fits a maximal
# seven-platform run (175 posts) inside the model's context with room to
# spare for one cheap call per run. So the budget now almost never bites,
# and when it does it drops whole posts rather than corrupting the payload.
#
# MAX_TOKENS was 6000, which is close enough to what this reply actually
# needs (seven platforms of bullets and claims, plus the cross-platform
# section) that a slightly wordier run overruns it -- and because the reply
# is one JSON object, overrunning it does not shorten the report, it destroys
# it: _parse() refuses the unterminated object and the entire run loses its
# synthesis. Doubled, with _truncated() naming the case if it ever happens
# again.
PAYLOAD_CHAR_BUDGET = 480000
MAX_TOKENS = 12000


def _anthropic():
    """Mirrors tracker/sci_vision.py's _anthropic()."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=90.0, max_retries=1)


def _clean_vision(analysis: dict | None) -> dict | None:
    """None for a vendor that was never run, failed, or returned nothing
    usable -- the model must never mistake an {"error": ...} dict for a real
    reading, and null is unambiguous in a way an error string embedded in
    the payload is not."""
    return analysis if analysis and "error" not in analysis else None


def _post_digest(post: dict) -> dict:
    return {
        "id": post["id"],
        "platform": post.get("platform"),
        "post_type": post.get("post_type"),
        "post_url": post.get("post_url"),
        "metrics": post.get("metrics") or {},
        "vision": {
            "claude": _clean_vision(post.get("creative_analysis")),
            "chatgpt": _clean_vision(post.get("creative_analysis_openai")),
        },
    }


def _engagement(post: dict) -> float:
    """Same shape as sci_classify._engagement_score: one comparable number
    for ranking posts, used here only to decide which posts survive the
    payload budget."""
    return sum(v for v in (post.get("metrics") or {}).values()
               if isinstance(v, (int, float)))


def _digest_rank(post: dict, digest: dict) -> tuple:
    """Sort key deciding which posts earn a place in the payload when they
    cannot all fit. Posts BOTH vendors read come first, then posts one
    vendor read, then the most engaging -- in that order because every claim
    the report makes has to cite real posts, and a post with no vision
    reading can support no claim about the creative, however popular it was."""
    readings = sum(1 for v in digest["vision"].values() if v)
    return (-readings, -_engagement(post))


def _select_digests(posts: list[dict], budget: int = PAYLOAD_CHAR_BUDGET,
                    base_len: int = 0) -> tuple[list[dict], int]:
    """The post digests that fit `budget`, in the run's own post order, plus
    how many posts were left out.

    Whole digests are dropped, never truncated: the point of the budget is
    that whatever reaches the model is complete and parseable. Selection
    walks the posts in _digest_rank order and keeps each one that still
    fits, so a small well-evidenced post can still get in after a large one
    was skipped."""
    digests = [_post_digest(p) for p in posts]
    order = sorted(range(len(digests)), key=lambda i: _digest_rank(posts[i], digests[i]))
    room = budget - base_len
    kept, used = [], 2  # the "posts" list's own brackets
    for i in order:
        size = len(json.dumps(digests[i])) + 1  # + its separating comma
        if used + size > room:
            continue
        kept.append(i)
        used += size
    kept.sort()
    return [digests[i] for i in kept], len(digests) - len(kept)


def _truncated(resp) -> bool:
    """Whether the reply ran out of output budget. See MAX_TOKENS above for
    why this is fatal rather than merely lossy for a JSON reply."""
    return getattr(resp, "stop_reason", None) == "max_tokens"


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
        "evidence": {"posts_total": len(posts), "posts_included": len(posts)},
        "posts": [],
    }
    # The posts get whatever the rest of the payload leaves them. Measured
    # against the real serialized base rather than a guessed allowance,
    # because `patterns` grows with the number of platforms too.
    base_len = len(json.dumps(payload))
    digests, omitted = _select_digests(posts, PAYLOAD_CHAR_BUDGET, base_len)
    payload["posts"] = digests
    payload["evidence"]["posts_included"] = len(digests)
    if omitted:
        logger.info("sci_synthesize: run %s sent %s of %s posts (payload budget)",
                    run_id, len(digests), len(posts))
    body = json.dumps(payload)
    # A belt-and-braces guarantee, not a second policy: whatever the inputs,
    # this call never sends a JSON document that does not parse. Slicing the
    # string was the original bug; dropping the last digest keeps it valid.
    while len(body) > PAYLOAD_CHAR_BUDGET and payload["posts"]:
        payload["posts"].pop()
        payload["evidence"]["posts_included"] = len(payload["posts"])
        body = json.dumps(payload)

    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": body}],
        )
    except Exception as e:
        logger.warning("sci_synthesize: synthesize_report failed for run %s: %s", run_id, e)
        return {"error": "vendor_call_failed"}

    if _truncated(resp):
        logger.warning("sci_synthesize: synthesis reply for run %s was cut off at max_tokens", run_id)
        return {"error": "response_truncated"}
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse(raw, valid_ids)
    if parsed is None:
        logger.warning("sci_synthesize: unparsable synthesis response for run %s", run_id)
        return {"error": "unparsable_response"}
    return parsed
