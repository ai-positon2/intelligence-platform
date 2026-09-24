"""LinkedIn-wide conversation ("pulse") about a person, for Thought Leader
Intelligence's Phase 2 (audience reaction).

Mirrors tracker/tlpr_x_pulse.py's reasoning, aimed at the same gap on a
different platform: Phase 1/2's existing LinkedIn coverage (Phase 1's own
posts, Phase 2's tracker/unipile_client.list_comments on those posts) only
ever reads what is directly attached to the subject's OWN posts. A
standalone post BY SOMEONE ELSE that discusses this person without ever
tagging or replying to them is invisible to both.

Built on the SAME vendor tracker/unipile_client.py already pays for and
already has a connected account through (Unipile) -- this is a new API
CALL (search_posts) against an already-integrated vendor, not a new
vendor decision. Two independent queries, merged and deduped by post id:
  1. keywords='"Full Name"' -- LinkedIn's own native post-search filter.
  2. mentioning={member:[provider_id]} -- LinkedIn's own dedicated
     @-mention filter, only run when Phase 0 already resolved the
     person's numeric provider_id (tracker/thought_leader_pr.
     _resolve_linkedin_platform).

Unlike X's actor, LinkedIn's search has no "-from:handle" exclusion
operator, so the person's own posts are dropped client-side in
collect_mentions() by comparing each result's author id against
provider_id -- keyword search has no way to ask LinkedIn to exclude an
author up front the way X's advanced search does.

Same division of labour as every other Claude-judged read in this
codebase: Claude judges (favorable or not, what themes recur, who is
worth reading directly), plain Python counts (how many posts, from how
many distinct authors, total engagement, over what span).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from tracker import claude_websearch, sci_name_match, sci_source_linkedin_unipile, unipile_client, unipile_transport

logger = logging.getLogger(__name__)

FETCH_LIMIT = 50            # per query, before cross-query dedup
MAX_POSTS_DIGEST = 60        # what Claude actually reads
MAX_TOP_POSTS = 12           # what the UI shows as post cards

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

_SYSTEM = (
    "You analyze what people on LinkedIn are actually saying ABOUT a "
    "named public figure -- independent posts by OTHER people discussing "
    "them, not comments on the person's own posts -- for a PR/reputation "
    "research tool used by a marketing agency. You are given real "
    "LinkedIn posts: author name, text, and engagement counts. Report "
    "what is genuinely there, including criticism, rather than a "
    "flattering summary.\n\n"
    "A post may still be authored by the subject themselves (a repost "
    "can slip past a search) or be about a different, unrelated person "
    "who happens to share this name. Exclude anything clearly written BY "
    "the subject or clearly about someone else, rather than forcing it "
    "into the read.\n\n"
    "Ground everything in the posts you were given. Never infer a fact "
    "this data doesn't support. If the posts are mostly incidental "
    "mentions rather than substantive discussion, say that plainly -- "
    "that is a real finding, not a failure.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"post_sentiment": {"<post_id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|"neutral", '
    '"detail": str, "post_ids": [str, ...]}], '
    '"notable_posts": [{"post_id": str, "why": str}], '
    '"risk_flags": [str, ...]}\n\n'
    "Rules: \"verdict\" is ONE sentence summarizing how LinkedIn talks "
    "about this person right now. \"post_sentiment\" must label EVERY "
    "post id you were given that is genuinely about this person, toward "
    "them specifically -- omit any id you determined is the subject's "
    "own voice or about someone else. \"themes\" is 2-5 recurring "
    "topics, each with a concrete \"detail\" (specific to these posts, "
    "never generic advice) and 1-4 supporting post_ids copied exactly "
    "from the data. \"notable_posts\" is 2-4 posts genuinely worth "
    "reading directly, each citing a real post_id and a one-sentence "
    "\"why\". \"risk_flags\" is 0-4 specific reputational risks this "
    "conversation surfaces -- empty list if it raises none, never "
    "invented to fill the field."
)


def _anthropic():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=120.0, max_retries=1)


def _items_from(payload) -> list[dict]:
    """The search envelope's result list, read defensively: the live shape
    for /linkedin/search has not been confirmed the way list_posts' was,
    so every plausible key name is tried rather than assuming one, same
    caution as unipile_client._accounts_from."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "elements", "results", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _author_id(post: dict) -> str | None:
    author = (post.get("raw") or {}).get("author") or {}
    if isinstance(author, dict):
        return str(author.get("id") or author.get("public_identifier") or "") or None
    return None


def _author_name(post: dict) -> str:
    author = (post.get("raw") or {}).get("author") or {}
    if isinstance(author, dict):
        return str(author.get("name") or "")
    return ""


def _is_own_post(post: dict, provider_id: str | None, full_name: str) -> bool:
    """Whether this post's author is the subject themselves, so it gets
    filtered out of 'what LinkedIn is saying about them'. provider_id is
    authoritative when known. When it is NOT known (Phase 0 never resolved
    it, or the search ran before identity confirmation), this used to fail
    OPEN -- every post, including the subject's own, was kept -- so a
    LinkedIn-wide keyword search for someone's own name would show their
    own posts back to them as if they were audience reaction. Falls back to
    a strict name match on the post's own author field (every significant
    word of the searched name must appear in the author's name, the same
    rule thought_leader_pr._looks_like_same_person uses for Apollo/YouTube,
    reimplemented here to avoid importing thought_leader_pr, which already
    imports this module)."""
    if provider_id:
        return _author_id(post) == str(provider_id)
    search_tokens = sci_name_match.person_name_tokens(full_name)
    author_tokens = sci_name_match.person_name_tokens(_author_name(post))
    if not search_tokens or not author_tokens:
        return False
    return search_tokens <= author_tokens


def collect_mentions(full_name: str, provider_id: str | None = None,
                     limit: int = FETCH_LIMIT) -> tuple[list[dict], dict]:
    """Every distinct LinkedIn post that genuinely mentions this person,
    deduped by id across both queries and with the person's own posts
    filtered out. Returns (posts, errors) -- one query failing never
    blocks the other, same fault isolation as every other multi-source
    collector in this codebase."""
    name = (full_name or "").strip()
    errors: dict[str, str] = {}
    if not name:
        return [], errors
    account_id = unipile_transport.account_for_platform("linkedin")
    if not account_id:
        errors["linkedin"] = "No connected LinkedIn account is available."
        return [], errors

    seen: dict[str, dict] = {}

    def _add(payload):
        for p in sci_source_linkedin_unipile.normalize(_items_from(payload)):
            pid = p.get("platform_post_id")
            if not pid or pid in seen:
                continue
            if _is_own_post(p, provider_id, name):
                continue
            seen[pid] = p

    try:
        data, err = unipile_client.search_posts(account_id, keywords='"%s"' % name, limit=limit)
        if err is not None:
            errors["linkedin_search"] = unipile_client.describe_error(err)
        else:
            _add(data)
    except Exception as e:
        logger.warning("tlpr_linkedin_pulse: keyword search failed for %r: %s", name, e)
        errors["linkedin_search"] = str(e)

    if provider_id:
        try:
            data, err = unipile_client.search_posts(account_id, mentioning_member_ids=[str(provider_id)],
                                                     limit=limit)
            if err is not None:
                errors.setdefault("linkedin_mentions", unipile_client.describe_error(err))
            else:
                _add(data)
        except Exception as e:
            logger.warning("tlpr_linkedin_pulse: mentioning search failed for %r: %s", name, e)
            errors.setdefault("linkedin_mentions", str(e))

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


def _as_int(value) -> int:
    """A metric as a number, or 0. Nothing upstream of here coerces the
    vendor's own metric values -- sci_source_linkedin_unipile.normalize passes them through
    exactly as the actor sent them -- so a count that arrives as a
    display string ("1.2K") or any other non-numeric value reaches
    _engagement untouched. aggregate() runs OUTSIDE build_pulse's own
    try/except, so one such value on one item used to raise straight out
    of a function whose docstring promises it never does, costing this
    whole source's read instead of that one item's engagement number.
    Mirrors tlpr_facebook_pulse._safe_int, which this family already keeps
    per module."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _engagement(post: dict) -> int:
    m = post.get("metrics") or {}
    return _as_int(m.get("likes")) + _as_int(m.get("shares")) + _as_int(m.get("comments"))


def _author(post: dict) -> str | None:
    author = (post.get("raw") or {}).get("author") or {}
    if isinstance(author, dict):
        return author.get("name") or author.get("public_identifier")
    return None


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
        "post_count": len(posts),
        "author_count": len(authors),
        "engagement_total": engagement_total,
        "top_authors": [{"handle": a, "posts": n} for a, n in authors.most_common(12)],
        "timeline": [{"month": m, "posts": n} for m, n in sorted(months.items())],
        "earliest": dates[0].isoformat() if dates else None,
        "latest": dates[-1].isoformat() if dates else None,
        "top_posts": [_post_card(p) for p in ordered[:MAX_TOP_POSTS]],
    }


def _digest(posts: list[dict], max_items: int = MAX_POSTS_DIGEST) -> list[dict]:
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
    """Strip every post id the model was not actually given, drop any
    theme or notable-post entry left with no real citation, and
    strip_em_dash every free-text field the model wrote."""
    digest_by_id = digest_by_id or {}
    _clean = claude_websearch.strip_em_dash

    def _cited(entries, *, name_key):
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = [str(i) for i in (entry.get("post_ids") or []) if str(i) in valid_ids]
            name = _clean(str(entry.get(name_key) or "").strip())
            if not ids or not name:
                continue
            cleaned = dict(entry, post_ids=ids[:4], **{name_key: name})
            if "detail" in cleaned:
                cleaned["detail"] = _clean(str(cleaned["detail"] or ""))
            out.append(cleaned)
        return out

    sentiment_raw = parsed.get("post_sentiment")
    labels = {}
    if isinstance(sentiment_raw, dict):
        for pid, label in sentiment_raw.items():
            if str(pid) in valid_ids and label in _SENTIMENTS:
                labels[str(pid)] = label
    counts = Counter(labels.values())
    total = sum(counts.values())

    notable = []
    for entry in (parsed.get("notable_posts") or []):
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("post_id") or "")
        why = _clean(str(entry.get("why") or "").strip())
        if pid in valid_ids and why:
            src = digest_by_id.get(pid) or {}
            notable.append({"post_id": pid, "why": why[:300],
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
        "notable_posts": notable[:4],
        "risk_flags": [_clean(str(r).strip()) for r in (parsed.get("risk_flags") or []) if str(r).strip()][:4],
    }


def analyze(full_name: str, posts: list[dict]) -> dict:
    """The judgement half. Never raises -- returns {"error": ...} so a
    failed analysis still leaves the counted aggregates intact and
    rendered."""
    if not posts:
        return {"error": "No LinkedIn posts mentioning this person were found."}
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
            max_tokens=16000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("tlpr_linkedin_pulse: analysis call failed for %r: %s", full_name, e)
        return {"error": "The LinkedIn conversation analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "".join(text_blocks)
    candidate = _extract_json_object(raw)
    if candidate is None:
        stop_reason = getattr(resp, "stop_reason", None)
        logger.warning("tlpr_linkedin_pulse: unparsable analysis for %r (stop_reason=%s, text_blocks=%d, chars=%d)",
                       full_name, stop_reason, len(text_blocks), len(raw))
        if stop_reason == "max_tokens":
            return {"error": "The LinkedIn conversation analysis ran out of output budget before it "
                             "finished (stop_reason=max_tokens). Raise max_tokens in "
                             "tracker/tlpr_linkedin_pulse.py or reduce MAX_POSTS_DIGEST."}
        return {"error": "The LinkedIn conversation analysis returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The LinkedIn conversation analysis returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The LinkedIn conversation analysis returned an unexpected shape."}
    return _clean_analysis(parsed, valid_ids, digest_by_id)


def build_pulse(full_name: str, provider_id: str | None = None) -> dict:
    """The whole LinkedIn conversation read, ready to store and render.
    Never raises: every failure mode degrades to a well-formed dict with a
    `note` explaining what a reader is looking at."""
    result: dict = {
        "person": full_name,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "post_count": 0,
        "author_count": 0,
        "engagement_total": 0,
        "top_authors": [],
        "timeline": [],
        "earliest": None,
        "latest": None,
        "top_posts": [],
        "analysis": None,
        "errors": {},
        "note": "",
    }
    try:
        posts, errors = collect_mentions(full_name, provider_id)
    except Exception as e:
        logger.warning("tlpr_linkedin_pulse: mention collection failed for %r: %s", full_name, e)
        result["note"] = ("LinkedIn search could not be completed for this person (%s)."
                          % (str(e)[:160] or type(e).__name__))
        return result

    result["errors"] = errors
    result.update(aggregate(posts))
    if not posts:
        note = ("No LinkedIn posts mentioning this person were found. That is a finding, "
                "not an error: this person has no measurable LinkedIn conversation to read.")
        # Same reasoning as tlpr_x_pulse.build_pulse: every entry left in
        # `errors` here is a real query failure, not a benign empty result.
        if errors:
            note += " " + " ".join(errors.values())
        result["note"] = note
        return result
    result["analysis"] = analyze(full_name, posts)
    return result
