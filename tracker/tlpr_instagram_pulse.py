"""Instagram conversation ("pulse") about a person, for Thought Leader
Intelligence's Phase 2 (audience reaction).

Instagram has no free-text caption/keyword search across the platform --
apify/instagram-scraper's own `search` input only resolves hashtags,
profiles, and places (confirmed against its live input schema on
2026-09-18), never arbitrary text in what other people wrote. What
Instagram DOES expose, and what this module reads, is its own dedicated
Mentions feature: `resultsType: "mentions"` against a profile URL returns
posts by OTHER accounts that explicitly @-tagged that profile. That is a
narrower, more literal claim than "what people are saying about them" on
X or LinkedIn -- it only ever surfaces posts where someone chose to tag
the person, not every post that merely names them -- and this module's
docstrings and error notes say so rather than overstating the coverage.

Reading it at all requires knowing the person's OWN Instagram handle,
which Phase 0 identity resolution never looked for before this feature:
tracker/thought_leader_pr.py's `_SYSTEM` prompt now also asks for
`instagram_handle`, mirroring the existing `x_handle` field exactly (a
soft-fail, best-effort platform entry with no separate verification
call -- see `_resolve_instagram_platform`). A person with no discoverable
Instagram handle simply gets no Instagram pulse, same as a person with no
discoverable YouTube channel gets no YouTube posts.

Built on the SAME actor tracker/sci_source_instagram.py already pays for
(apify/instagram-scraper) -- this is a new INPUT SHAPE (resultsType=
"mentions" instead of "posts") against an already-integrated vendor, not
a new vendor decision.

Same division of labour as every other Claude-judged read in this
codebase: Claude judges (favorable or not, what themes recur, who is
worth reading directly), plain Python counts (how many mentioning posts,
from how many distinct accounts, total engagement, over what span).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from tracker import apify_transport, claude_websearch, sci_source_instagram

logger = logging.getLogger(__name__)

FETCH_LIMIT = 50             # mentioning posts fetched from the profile's Mentions tab
MAX_MENTIONS_DIGEST = 60      # what Claude actually reads
MAX_TOP_MENTIONS = 12         # what the UI shows as post cards

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

_SYSTEM = (
    "You analyze what people on Instagram are actually saying ABOUT a "
    "named public figure -- posts by OTHER accounts that explicitly "
    "tagged/mentioned them, not their own posts -- for a PR/reputation "
    "research tool used by a marketing agency. You are given real "
    "Instagram posts that tagged this person: the tagging account's "
    "name, the caption, and engagement counts. Report what is genuinely "
    "there, including criticism, rather than a flattering summary.\n\n"
    "These are posts where someone chose to explicitly tag this person, "
    "which is a narrower signal than a general web mention -- a brand "
    "partnership tag, a fan account, an event photo, a criticism. Note "
    "when the mentions read as mostly promotional/tagged-in-passing "
    "rather than substantive discussion -- that is a real finding, not "
    "a failure.\n\n"
    "Ground everything in the posts you were given. Never infer a fact "
    "this data doesn't support.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"mention_sentiment": {"<mention_id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|"neutral", '
    '"detail": str, "mention_ids": [str, ...]}], '
    '"notable_mentions": [{"mention_id": str, "why": str}], '
    '"risk_flags": [str, ...]}\n\n'
    "Rules: \"verdict\" is ONE sentence summarizing how Instagram tags "
    "this person right now. \"mention_sentiment\" must label EVERY "
    "mention id you were given. \"themes\" is 2-5 recurring patterns, "
    "each with a concrete \"detail\" (specific to these posts, never "
    "generic advice) and 1-4 supporting mention_ids copied exactly from "
    "the data. \"notable_mentions\" is 2-4 posts genuinely worth reading "
    "directly, each citing a real mention_id and a one-sentence \"why\". "
    "\"risk_flags\" is 0-4 specific reputational risks this conversation "
    "surfaces -- empty list if it raises none, never invented to fill "
    "the field."
)


def _anthropic():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=120.0, max_retries=1)


def collect_mentions(instagram_handle: str, limit: int = FETCH_LIMIT) -> tuple[list[dict], dict]:
    """Every post on this person's own Instagram Mentions tab -- posts by
    OTHER accounts that tagged them. Returns (posts, errors), never
    raises."""
    handle = (instagram_handle or "").lstrip("@").strip()
    errors: dict[str, str] = {}
    if not handle:
        return [], errors
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        errors["apify"] = "Apify is not configured on this deployment."
        return [], errors

    run_input = {"resultsType": "mentions",
                "directUrls": [f"https://www.instagram.com/{handle}/"],
                "resultsLimit": limit}
    try:
        raw_items = apify_transport.run_actor_and_wait(
            sci_source_instagram.actor_id(), run_input, token, strict=True)
    except apify_transport.ApifyTransportError as e:
        errors["instagram_mentions"] = str(e)
        return [], errors

    seen: dict[str, dict] = {}
    for p in sci_source_instagram.normalize(raw_items):
        pid = p.get("platform_post_id")
        if pid and pid not in seen:
            seen[pid] = p
    return list(seen.values()), errors


def _parse_posted(post: dict) -> datetime | None:
    raw = post.get("posted_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _month(post: dict) -> str | None:
    dt = _parse_posted(post)
    return dt.strftime("%Y-%m") if dt else None


def _engagement(post: dict) -> int:
    m = post.get("metrics") or {}
    return int(m.get("likes") or 0) + int(m.get("comments") or 0) + int(m.get("views") or 0)


def _author(post: dict) -> str | None:
    """The account that tagged this person -- NOT the subject, who is only
    ever the profile these posts were read from."""
    raw = post.get("raw") or {}
    return raw.get("ownerUsername") or raw.get("ownerFullName")


def _post_card(post: dict) -> dict:
    return {
        "id": post.get("platform_post_id"),
        "author": _author(post),
        "text": (post.get("caption") or "")[:280],
        "url": post.get("post_url"),
        "posted_at": post.get("posted_at"),
        "likes": (post.get("metrics") or {}).get("likes"),
    }


def aggregate(posts: list[dict]) -> dict:
    """The mechanical half: everything that is a count, computed in code so
    it always matches the posts it came from."""
    authors = Counter(a for a in (_author(p) for p in posts) if a)
    months = Counter(m for m in (_month(p) for p in posts) if m)
    dates = sorted(d for d in (_parse_posted(p) for p in posts) if d)
    engagement_total = sum(_engagement(p) for p in posts)

    ordered = sorted(posts, key=_engagement, reverse=True)
    return {
        "mention_count": len(posts),
        "author_count": len(authors),
        "engagement_total": engagement_total,
        "top_authors": [{"handle": a, "mentions": n} for a, n in authors.most_common(12)],
        "timeline": [{"month": m, "mentions": n} for m, n in sorted(months.items())],
        "earliest": dates[0].isoformat() if dates else None,
        "latest": dates[-1].isoformat() if dates else None,
        "top_mentions": [_post_card(p) for p in ordered[:MAX_TOP_MENTIONS]],
    }


def _digest(posts: list[dict], max_items: int = MAX_MENTIONS_DIGEST) -> list[dict]:
    """What Claude actually reads, ordered by engagement so a cap bites the
    least-noticed posts first, never an arbitrary slice."""
    ordered = sorted(posts, key=_engagement, reverse=True)[:max_items]
    out = []
    for i, p in enumerate(ordered):
        out.append({
            "id": str(i),
            "author": _author(p),
            "text": (p.get("caption") or "")[:400],
            "url": p.get("post_url"),
            "posted_at": p.get("posted_at"),
            "likes": (p.get("metrics") or {}).get("likes"),
        })
    return out


def _extract_json_object(raw: str) -> str | None:
    """Same string-aware brace scan as tlpr_press._extract_json_object."""
    start = raw.find("{")
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None


def _clean_analysis(parsed: dict, valid_ids: set[str],
                    digest_by_id: dict[str, dict] | None = None) -> dict:
    """Strip every mention id the model was not actually given, drop any
    theme or notable-mention entry left with no real citation, and
    strip_em_dash every free-text field the model wrote."""
    digest_by_id = digest_by_id or {}
    _clean = claude_websearch.strip_em_dash

    def _cited(entries, *, name_key):
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = [str(i) for i in (entry.get("mention_ids") or []) if str(i) in valid_ids]
            name = _clean(str(entry.get(name_key) or "").strip())
            if not ids or not name:
                continue
            cleaned = dict(entry, mention_ids=ids[:4], **{name_key: name})
            if "detail" in cleaned:
                cleaned["detail"] = _clean(str(cleaned["detail"] or ""))
            out.append(cleaned)
        return out

    sentiment_raw = parsed.get("mention_sentiment")
    labels = {}
    if isinstance(sentiment_raw, dict):
        for mid, label in sentiment_raw.items():
            if str(mid) in valid_ids and label in _SENTIMENTS:
                labels[str(mid)] = label
    counts = Counter(labels.values())
    total = sum(counts.values())

    notable = []
    for entry in (parsed.get("notable_mentions") or []):
        if not isinstance(entry, dict):
            continue
        mid = str(entry.get("mention_id") or "")
        why = _clean(str(entry.get("why") or "").strip())
        if mid in valid_ids and why:
            src = digest_by_id.get(mid) or {}
            notable.append({"mention_id": mid, "why": why[:300],
                            "author": src.get("author"), "text": src.get("text"),
                            "url": src.get("url")})

    return {
        "verdict": _clean(str(parsed.get("verdict") or "").strip()),
        "sentiment": {
            "counts": {s: counts.get(s, 0) for s in _SENTIMENTS},
            "labelled": total,
            "negative_share": round(counts.get("negative", 0) / total, 3) if total else None,
        },
        "themes": _cited(parsed.get("themes"), name_key="label")[:5],
        "notable_mentions": notable[:4],
        "risk_flags": [_clean(str(r).strip()) for r in (parsed.get("risk_flags") or []) if str(r).strip()][:4],
    }


def analyze(full_name: str, posts: list[dict]) -> dict:
    """The judgement half. Never raises -- returns {"error": ...} so a
    failed analysis still leaves the counted aggregates intact and
    rendered."""
    if not posts:
        return {"error": "No Instagram posts mentioning this person were found."}
    client = _anthropic()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    digest = _digest(posts)
    valid_ids = {d["id"] for d in digest}
    digest_by_id = {d["id"]: d for d in digest}
    payload = {"person": full_name, "posts": digest}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=8000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("tlpr_instagram_pulse: analysis call failed for %r: %s", full_name, e)
        return {"error": "The Instagram conversation analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "".join(text_blocks)
    candidate = _extract_json_object(raw)
    if candidate is None:
        stop_reason = getattr(resp, "stop_reason", None)
        logger.warning("tlpr_instagram_pulse: unparsable analysis for %r (stop_reason=%s, text_blocks=%d, chars=%d)",
                       full_name, stop_reason, len(text_blocks), len(raw))
        if stop_reason == "max_tokens":
            return {"error": "The Instagram conversation analysis ran out of output budget before it "
                             "finished (stop_reason=max_tokens). Raise max_tokens in "
                             "tracker/tlpr_instagram_pulse.py or reduce MAX_MENTIONS_DIGEST."}
        return {"error": "The Instagram conversation analysis returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The Instagram conversation analysis returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The Instagram conversation analysis returned an unexpected shape."}
    return _clean_analysis(parsed, valid_ids, digest_by_id)


def build_pulse(full_name: str, instagram_handle: str | None = None) -> dict:
    """The whole Instagram mentions read, ready to store and render. Never
    raises: every failure mode degrades to a well-formed dict with a
    `note` explaining what a reader is looking at."""
    result: dict = {
        "person": full_name,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "mention_count": 0,
        "author_count": 0,
        "engagement_total": 0,
        "top_authors": [],
        "timeline": [],
        "earliest": None,
        "latest": None,
        "top_mentions": [],
        "analysis": None,
        "errors": {},
        "note": "",
    }
    if not (instagram_handle or "").strip():
        result["note"] = "No Instagram handle was found for this person, so their Mentions tab can't be read."
        return result
    try:
        posts, errors = collect_mentions(instagram_handle)
    except Exception as e:
        logger.warning("tlpr_instagram_pulse: mention collection failed for %r: %s", full_name, e)
        result["note"] = "Instagram could not be read for this person."
        return result

    result["errors"] = errors
    result.update(aggregate(posts))
    if not posts:
        result["note"] = ("No Instagram posts tagging this person were found. That is a finding, "
                          "not an error: nobody has tagged them there recently.")
        return result
    result["analysis"] = analyze(full_name, posts)
    return result
