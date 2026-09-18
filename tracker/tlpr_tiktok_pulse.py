"""TikTok conversation ("pulse") about a person, for Thought Leader
Intelligence's Phase 2 (audience reaction).

TikTok is not one of Phase 1's owned platforms (identity resolution never
looks for a TikTok handle -- see tracker/thought_leader_pr.py's
`platforms` dict), so unlike tlpr_linkedin_pulse.py and tlpr_x_pulse.py
this module has no "the subject's own voice slipping through search" case
to guard against with an author-exclusion filter: there is no known
handle to exclude. What it reads is exactly TikTok's own video search for
this person's name -- genuinely site-wide, not scoped to any one account.

Built on the SAME actor tracker/sci_source_tiktok.py already pays for
(clockworks/tiktok-scraper) -- this is a new INPUT SHAPE (searchQueries +
searchSection="/video" instead of profiles) against an already-integrated
vendor, not a new vendor decision. `searchSection` is set explicitly to
"/video" rather than left at TikTok's own default "Top" mix, because a
"Top" search also returns PROFILE rows with none of the fields a video
has -- normalize() already drops anything with no usable id, but reading
only videos avoids silently mixing two different row shapes into one
digest.

Comment-thread reading (mirroring what tlpr_reddit_pulse.py does for
Reddit) is deliberately NOT built here: this actor puts requested
comments in a SEPARATE named dataset reachable only via a
`commentsDatasetUrl` on each item, a second Apify dataset fetch this
module has no way to verify against a live response in this session.
Shipping the search half now, without an unverified second hop, is the
same discipline as tlpr_press.py naming a truncated reply distinctly
rather than guessing at a shape.

Same division of labour as every other Claude-judged read in this
codebase: Claude judges (favorable or not, what themes recur, who is
worth reading directly), plain Python counts (how many videos, from how
many distinct creators, total engagement, over what span).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from tracker import apify_transport, claude_websearch, sci_source_tiktok

logger = logging.getLogger(__name__)

FETCH_LIMIT = 50            # videos fetched from the search query
MAX_VIDEOS_DIGEST = 60       # what Claude actually reads
MAX_TOP_VIDEOS = 12          # what the UI shows as video cards

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

_SYSTEM = (
    "You analyze what people on TikTok are actually saying ABOUT a "
    "named public figure -- independent videos by OTHER creators "
    "discussing them, found by searching TikTok's own video search for "
    "their name -- for a PR/reputation research tool used by a marketing "
    "agency. You are given real videos: creator name, caption/text, and "
    "engagement counts. Report what is genuinely there, including "
    "criticism, rather than a flattering summary.\n\n"
    "A video's caption may be about a completely different, unrelated "
    "person who happens to share this name, or may be the subject's own "
    "video if they have a TikTok presence themselves. Exclude anything "
    "clearly about someone else or clearly posted BY the subject, rather "
    "than forcing it into the read.\n\n"
    "Ground everything in the videos you were given. Never infer a fact "
    "this data doesn't support. If the videos are mostly incidental "
    "name-drops rather than substantive discussion, say that plainly -- "
    "that is a real finding, not a failure.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"video_sentiment": {"<video_id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|"neutral", '
    '"detail": str, "video_ids": [str, ...]}], '
    '"notable_videos": [{"video_id": str, "why": str}], '
    '"risk_flags": [str, ...]}\n\n'
    "Rules: \"verdict\" is ONE sentence summarizing how TikTok talks "
    "about this person right now. \"video_sentiment\" must label EVERY "
    "video id you were given that is genuinely about this person, toward "
    "them specifically -- omit any id you determined is about someone "
    "else. \"themes\" is 2-5 recurring topics, each with a concrete "
    "\"detail\" (specific to these videos, never generic advice) and 1-4 "
    "supporting video_ids copied exactly from the data. \"notable_videos\" "
    "is 2-4 videos genuinely worth reading directly, each citing a real "
    "video_id and a one-sentence \"why\". \"risk_flags\" is 0-4 specific "
    "reputational risks this conversation surfaces -- empty list if it "
    "raises none, never invented to fill the field."
)


def _anthropic():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=120.0, max_retries=1)


def collect_mentions(full_name: str, limit: int = FETCH_LIMIT) -> tuple[list[dict], dict]:
    """Every TikTok video this person's name turns up in TikTok's own video
    search. Returns (videos, errors), never raises."""
    name = (full_name or "").strip()
    errors: dict[str, str] = {}
    if not name:
        return [], errors
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        errors["apify"] = "Apify is not configured on this deployment."
        return [], errors

    run_input = {"searchQueries": [name], "searchSection": "/video",
                "resultsPerPage": limit, "shouldDownloadVideos": False,
                "shouldDownloadCovers": False}
    try:
        raw_items = apify_transport.run_actor_and_wait(
            sci_source_tiktok.actor_id(), run_input, token, strict=True)
    except apify_transport.ApifyTransportError as e:
        errors["tiktok_search"] = str(e)
        return [], errors

    seen: dict[str, dict] = {}
    for v in sci_source_tiktok.normalize(raw_items):
        vid = v.get("platform_post_id")
        if vid and vid not in seen:
            seen[vid] = v
    return list(seen.values()), errors


def _parse_posted(video: dict) -> datetime | None:
    raw = video.get("posted_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _month(video: dict) -> str | None:
    dt = _parse_posted(video)
    return dt.strftime("%Y-%m") if dt else None


def _engagement(video: dict) -> int:
    m = video.get("metrics") or {}
    return int(m.get("likes") or 0) + int(m.get("shares") or 0) + int(m.get("comments") or 0)


def _author(video: dict) -> str | None:
    author = (video.get("raw") or {}).get("authorMeta") or {}
    if isinstance(author, dict):
        return author.get("nickName") or author.get("name")
    return None


def _video_card(video: dict) -> dict:
    return {
        "id": video.get("platform_post_id"),
        "author": _author(video),
        "text": (video.get("caption") or "")[:280],
        "url": video.get("post_url"),
        "posted_at": video.get("posted_at"),
        "likes": (video.get("metrics") or {}).get("likes"),
    }


def aggregate(videos: list[dict]) -> dict:
    """The mechanical half: everything that is a count, computed in code so
    it always matches the videos it came from."""
    authors = Counter(a for a in (_author(v) for v in videos) if a)
    months = Counter(m for m in (_month(v) for v in videos) if m)
    dates = sorted(d for d in (_parse_posted(v) for v in videos) if d)
    engagement_total = sum(_engagement(v) for v in videos)

    ordered = sorted(videos, key=_engagement, reverse=True)
    return {
        "video_count": len(videos),
        "author_count": len(authors),
        "engagement_total": engagement_total,
        "top_authors": [{"handle": a, "videos": n} for a, n in authors.most_common(12)],
        "timeline": [{"month": m, "videos": n} for m, n in sorted(months.items())],
        "earliest": dates[0].isoformat() if dates else None,
        "latest": dates[-1].isoformat() if dates else None,
        "top_videos": [_video_card(v) for v in ordered[:MAX_TOP_VIDEOS]],
    }


def _digest(videos: list[dict], max_items: int = MAX_VIDEOS_DIGEST) -> list[dict]:
    """What Claude actually reads, ordered by engagement so a cap bites the
    least-noticed videos first, never an arbitrary slice."""
    ordered = sorted(videos, key=_engagement, reverse=True)[:max_items]
    out = []
    for i, v in enumerate(ordered):
        out.append({
            "id": str(i),
            "author": _author(v),
            "text": (v.get("caption") or "")[:400],
            "url": v.get("post_url"),
            "posted_at": v.get("posted_at"),
            "likes": (v.get("metrics") or {}).get("likes"),
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
    """Strip every video id the model was not actually given, drop any
    theme or notable-video entry left with no real citation, and
    strip_em_dash every free-text field the model wrote."""
    digest_by_id = digest_by_id or {}
    _clean = claude_websearch.strip_em_dash

    def _cited(entries, *, name_key):
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = [str(i) for i in (entry.get("video_ids") or []) if str(i) in valid_ids]
            name = _clean(str(entry.get(name_key) or "").strip())
            if not ids or not name:
                continue
            cleaned = dict(entry, video_ids=ids[:4], **{name_key: name})
            if "detail" in cleaned:
                cleaned["detail"] = _clean(str(cleaned["detail"] or ""))
            out.append(cleaned)
        return out

    sentiment_raw = parsed.get("video_sentiment")
    labels = {}
    if isinstance(sentiment_raw, dict):
        for vid, label in sentiment_raw.items():
            if str(vid) in valid_ids and label in _SENTIMENTS:
                labels[str(vid)] = label
    counts = Counter(labels.values())
    total = sum(counts.values())

    notable = []
    for entry in (parsed.get("notable_videos") or []):
        if not isinstance(entry, dict):
            continue
        vid = str(entry.get("video_id") or "")
        why = _clean(str(entry.get("why") or "").strip())
        if vid in valid_ids and why:
            src = digest_by_id.get(vid) or {}
            notable.append({"video_id": vid, "why": why[:300],
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
        "notable_videos": notable[:4],
        "risk_flags": [_clean(str(r).strip()) for r in (parsed.get("risk_flags") or []) if str(r).strip()][:4],
    }


def analyze(full_name: str, videos: list[dict]) -> dict:
    """The judgement half. Never raises -- returns {"error": ...} so a
    failed analysis still leaves the counted aggregates intact and
    rendered."""
    if not videos:
        return {"error": "No TikTok videos mentioning this person were found."}
    client = _anthropic()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    digest = _digest(videos)
    valid_ids = {d["id"] for d in digest}
    digest_by_id = {d["id"]: d for d in digest}
    payload = {"person": full_name, "videos": digest}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=8000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("tlpr_tiktok_pulse: analysis call failed for %r: %s", full_name, e)
        return {"error": "The TikTok conversation analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "".join(text_blocks)
    candidate = _extract_json_object(raw)
    if candidate is None:
        stop_reason = getattr(resp, "stop_reason", None)
        logger.warning("tlpr_tiktok_pulse: unparsable analysis for %r (stop_reason=%s, text_blocks=%d, chars=%d)",
                       full_name, stop_reason, len(text_blocks), len(raw))
        if stop_reason == "max_tokens":
            return {"error": "The TikTok conversation analysis ran out of output budget before it "
                             "finished (stop_reason=max_tokens). Raise max_tokens in "
                             "tracker/tlpr_tiktok_pulse.py or reduce MAX_VIDEOS_DIGEST."}
        return {"error": "The TikTok conversation analysis returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The TikTok conversation analysis returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The TikTok conversation analysis returned an unexpected shape."}
    return _clean_analysis(parsed, valid_ids, digest_by_id)


def build_pulse(full_name: str) -> dict:
    """The whole TikTok conversation read, ready to store and render.
    Never raises: every failure mode degrades to a well-formed dict with a
    `note` explaining what a reader is looking at."""
    result: dict = {
        "person": full_name,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "video_count": 0,
        "author_count": 0,
        "engagement_total": 0,
        "top_authors": [],
        "timeline": [],
        "earliest": None,
        "latest": None,
        "top_videos": [],
        "analysis": None,
        "errors": {},
        "note": "",
    }
    try:
        videos, errors = collect_mentions(full_name)
    except Exception as e:
        logger.warning("tlpr_tiktok_pulse: mention collection failed for %r: %s", full_name, e)
        result["note"] = "TikTok search could not be completed for this person."
        return result

    result["errors"] = errors
    result.update(aggregate(videos))
    if not videos:
        result["note"] = ("No TikTok videos mentioning this person were found. That is a finding, "
                          "not an error: this person has no measurable TikTok conversation to read.")
        return result
    result["analysis"] = analyze(full_name, videos)
    return result
