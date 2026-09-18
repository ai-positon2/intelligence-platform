"""Facebook conversation ("pulse") about a person, for Thought Leader
Intelligence's Phase 2 (audience reaction).

Facebook is not one of Phase 1's owned platforms and has no keyword
search or comment capability in the actor this codebase already pays for
(tracker/sci_source_facebook.py's apify/facebook-posts-scraper reads one
page's own posts and nothing else). Getting real "what people are saying
about them on Facebook" coverage genuinely needed two NEW actors -- a
deliberate, confirmed-with-the-user vendor decision (2026-09-18), unlike
every sibling in this file's family (tlpr_linkedin_pulse.py,
tlpr_tiktok_pulse.py, tlpr_instagram_pulse.py), which all reuse a vendor
already integrated elsewhere in this codebase:
  1. scraper_one/facebook-posts-search -- keyword search for public posts,
     the Facebook equivalent of tlpr_x_pulse.py's search half. Third-party
     (non-Apify-official), moderate track record (21k+ users, 4.2/5).
  2. apify/facebook-comments-scraper -- reads the comment section of
     whatever posts the search above finds, mirroring
     tlpr_reddit_pulse.py's two-stage design exactly (search first, then
     read the comment section of the threads/posts search already
     confirmed are about this person) rather than a fictitious
     site-wide comment search. Official Apify, well-established
     (43k+ users, 4.75/5).

Neither actor's live response has been confirmed against a real run in
this session -- every field read below is best-effort against the
actors' own published input/output schemas (fetched 2026-09-18), the same
caution tracker/unipile_client.py already applies to get_user_profile and
search_posts.

Same division of labour as every other Claude-judged read in this
codebase: Claude judges (favorable or not, what themes recur, who is
worth reading directly), plain Python counts (how many posts and
comments, from how many distinct authors, total engagement).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone

from tracker import apify_transport, claude_websearch

logger = logging.getLogger(__name__)

FETCH_LIMIT = 40                  # posts fetched from the keyword search
MAX_POSTS_FOR_COMMENTS = 12        # only the most-engaged found posts get a comments fetch
MAX_COMMENTS_PER_POST = 15
MAX_ITEMS_DIGEST = 60              # what Claude actually reads (posts + comments combined)
MAX_TOP_POSTS = 12                 # what the UI shows as post cards

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

DEFAULT_SEARCH_ACTOR_ID = "scraper_one/facebook-posts-search"
DEFAULT_COMMENTS_ACTOR_ID = "apify/facebook-comments-scraper"


def search_actor_id() -> str:
    return os.environ.get("TLPR_APIFY_FACEBOOK_SEARCH_ACTOR_ID", DEFAULT_SEARCH_ACTOR_ID)


def comments_actor_id() -> str:
    return os.environ.get("TLPR_APIFY_FACEBOOK_COMMENTS_ACTOR_ID", DEFAULT_COMMENTS_ACTOR_ID)


_SYSTEM = (
    "You analyze what people on Facebook are actually saying ABOUT a "
    "named public figure -- independent public posts by OTHER people "
    "discussing them, plus comments left inside those posts' own comment "
    "sections -- for a PR/reputation research tool used by a marketing "
    "agency. You are given a mix of POSTS (author, text, engagement) and "
    "individual COMMENTS from inside those posts' own comment sections "
    "(each tagged kind, with which post it came from). Report what is "
    "genuinely there, including criticism, rather than a flattering "
    "summary.\n\n"
    "A post's text may be about a completely different, unrelated person "
    "who happens to share this name, or may be the subject's own post. "
    "Exclude anything clearly about someone else or clearly posted BY the "
    "subject, rather than forcing it into the read.\n\n"
    "Ground everything in the items you were given. Never infer a fact "
    "this data doesn't support. If the items are mostly incidental "
    "mentions rather than substantive discussion, say that plainly -- "
    "that is a real finding, not a failure.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"post_sentiment": {"<id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|"neutral", '
    '"detail": str, "post_ids": [str, ...]}], '
    '"notable_posts": [{"post_id": str, "why": str}], '
    '"risk_flags": [str, ...]}\n\n'
    "Rules: \"verdict\" is ONE sentence summarizing how Facebook talks "
    "about this person right now. \"post_sentiment\" must label EVERY id "
    "you were given (post or comment alike), judged toward this person "
    "specifically. \"themes\" is 2-5 recurring topics, each with a "
    "concrete \"detail\" (specific to these items, never generic advice) "
    "and 1-4 supporting ids (post or comment, mixed freely) copied "
    "exactly from the data. \"notable_posts\" is 2-4 items genuinely "
    "worth reading directly, each citing a real id and a one-sentence "
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


def _safe_int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _parse_timestamp(raw) -> str | None:
    """The search actor's own schema types `timestamp` as a bare number,
    unlike every other vendor in this codebase which hands back an ISO
    string -- guessed as Unix seconds (or milliseconds, if the magnitude
    says so) since that is Facebook's own API convention, then converted
    to ISO so this module's posted_at fields need no special-casing
    downstream."""
    if raw in (None, ""):
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    if n > 1e12:
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()
    except (ValueError, OSError):
        return None


def normalize(raw_items: list[dict]) -> list[dict]:
    """scraper_one/facebook-posts-search's own output shape into this
    module's common post-dict shape. Kept local rather than reused from
    tracker/sci_source_facebook.py: that module normalizes a DIFFERENT
    actor's (apify/facebook-posts-scraper) different field names."""
    out = []
    for item in raw_items or []:
        pid = str(item.get("postId") or "").strip()
        if not pid:
            continue
        author = item.get("author") or {}
        out.append({
            "platform_post_id": pid,
            "post_url": item.get("url"),
            "caption": item.get("postText") or "",
            "posted_at": _parse_timestamp(item.get("timestamp")),
            "metrics": {
                "likes": _safe_int(item.get("reactionsCount")),
                "comments": _safe_int(item.get("commentsCount")),
                "shares": _safe_int(item.get("sharesCount")),
            },
            "raw": item,
        })
    return out


def _mentions_person(post: dict, full_name: str) -> bool:
    """A keyword search actor is free-text and not always precise, so
    every hit is re-checked against its own text before it is allowed to
    count as a mention -- same discipline and same punctuation-fallback
    tlpr_reddit_pulse._mentions_person applies."""
    name = (full_name or "").strip().lower()
    if not name:
        return False
    haystack = (post.get("caption") or "").lower()
    if name in haystack:
        return True
    flat_name = re.sub(r"[^a-z0-9]", "", name)
    return bool(flat_name and flat_name in re.sub(r"[^a-z0-9]", "", haystack))


def collect_mentions(full_name: str, limit: int = FETCH_LIMIT) -> tuple[list[dict], dict]:
    """Every distinct Facebook post that genuinely mentions this person.
    Returns (posts, errors), never raises."""
    name = (full_name or "").strip()
    errors: dict[str, str] = {}
    if not name:
        return [], errors
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        errors["apify"] = "Apify is not configured on this deployment."
        return [], errors

    run_input = {"query": name, "resultsCount": limit, "searchType": "latest"}
    try:
        raw_items = apify_transport.run_actor_and_wait(search_actor_id(), run_input, token, strict=True)
        seen: dict[str, dict] = {}
        for post in normalize(raw_items):
            pid = post.get("platform_post_id")
            if pid and pid not in seen and _mentions_person(post, name):
                seen[pid] = post
    except apify_transport.ApifyTransportError as e:
        errors["facebook_search"] = str(e)
        return [], errors
    except Exception as e:
        # normalize() is now inside this try deliberately: it's a different
        # exception type than the transport's own error, and a narrow except
        # around only the actor call would let a malformed-data crash here
        # escape uncaught -- the same gap ff84a19 fixed in
        # thought_leader_pr.py's _collect_x_posts.
        logger.exception("tlpr_facebook_pulse: search crashed for %r", name)
        errors["facebook_search"] = "Could not read Facebook's search response (%s)." % type(e).__name__
        return [], errors
    return list(seen.values()), errors


def _engagement(post: dict) -> int:
    m = post.get("metrics") or {}
    return int(m.get("likes") or 0) + int(m.get("comments") or 0) + int(m.get("shares") or 0)


def _author(post: dict) -> str | None:
    author = (post.get("raw") or {}).get("author") or {}
    if isinstance(author, dict):
        return author.get("name")
    return None


def _collect_post_comments(posts: list[dict], token: str) -> list[dict]:
    """The comment section of each of the most-engaged found posts, in ONE
    actor run (the comments actor accepts multiple startUrls at once,
    unlike Reddit's own comments endpoint which only ever answers for one
    thread per call). One bad run degrades to no comments rather than no
    posts -- never lets Phase 2's whole Facebook pulse fail because the
    comments half alone came back empty."""
    top = sorted(posts, key=_engagement, reverse=True)[:MAX_POSTS_FOR_COMMENTS]
    urls = [p.get("post_url") for p in top if p.get("post_url")]
    if not urls:
        return []
    run_input = {"startUrls": [{"url": u} for u in urls], "resultsLimit": MAX_COMMENTS_PER_POST,
                "includeNestedComments": False, "viewOption": "RANKED_UNFILTERED"}
    try:
        raw_items = apify_transport.run_actor_and_wait(comments_actor_id(), run_input, token, strict=True)
    except apify_transport.ApifyTransportError as e:
        logger.warning("tlpr_facebook_pulse: comments fetch failed: %s", e)
        return []
    out = []
    for c in raw_items or []:
        text = (c.get("text") or "").strip()
        cid = c.get("commentId") or c.get("id")
        if not text or not cid:
            continue
        out.append({
            "id": str(cid),
            "post_title": c.get("postTitle"),
            "text": text,
            "author": c.get("profileName"),
            "likes": _safe_int(c.get("likesCount")),
            "url": c.get("commentUrl"),
        })
    return out


def _post_card(post: dict) -> dict:
    return {
        "id": post.get("platform_post_id"),
        "author": _author(post),
        "text": (post.get("caption") or "")[:280],
        "url": post.get("post_url"),
        "posted_at": post.get("posted_at"),
        "likes": (post.get("metrics") or {}).get("likes"),
    }


def _comment_card(comment: dict) -> dict:
    return {
        "id": "c_" + str(comment["id"]),
        "kind": "comment",
        "author": comment.get("author"),
        "text": ("Comment on: %s -- %s" % (comment.get("post_title") or "(untitled post)",
                                           comment.get("text") or ""))[:700],
        "url": comment.get("url"),
        "posted_at": None,
        "likes": comment.get("likes"),
    }


def aggregate(posts: list[dict]) -> dict:
    """The mechanical half: everything that is a count, computed in code so
    it always matches the posts it came from."""
    authors = Counter(a for a in (_author(p) for p in posts) if a)
    engagement_total = sum(_engagement(p) for p in posts)
    ordered = sorted(posts, key=_engagement, reverse=True)
    return {
        "post_count": len(posts),
        "author_count": len(authors),
        "engagement_total": engagement_total,
        "top_authors": [{"handle": a, "posts": n} for a, n in authors.most_common(12)],
        "top_posts": [_post_card(p) for p in ordered[:MAX_TOP_POSTS]],
    }


def _digest(posts: list[dict], comments: list[dict] | None = None,
           max_items: int = MAX_ITEMS_DIGEST) -> list[dict]:
    """What Claude actually reads: the top-engaged posts plus, tagged
    "kind": "comment", individual comments pulled from inside those
    posts' own comment sections. Comment ids are prefixed "c_" so they
    can never collide with a bare Facebook post id, the same convention
    tlpr_reddit_pulse._digest uses."""
    ordered = sorted(posts, key=_engagement, reverse=True)
    out = []
    for p in ordered:
        out.append({"id": p.get("platform_post_id"), "kind": "post",
                   "author": _author(p), "text": (p.get("caption") or "")[:400],
                   "url": p.get("post_url"), "posted_at": p.get("posted_at"),
                   "likes": (p.get("metrics") or {}).get("likes")})
    ordered_comments = sorted(comments or [], key=lambda c: c.get("likes") or 0, reverse=True)
    for c in ordered_comments:
        out.append({"id": "c_" + str(c["id"]), "kind": "comment", "author": c.get("author"),
                   "text": (c.get("text") or "")[:400], "url": c.get("url"),
                   "posted_at": None, "likes": c.get("likes")})
    return out[:max_items]


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
    """Strip every id the model was not actually given, drop any theme or
    notable-post entry left with no real citation, and strip_em_dash every
    free-text field the model wrote."""
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


def analyze(full_name: str, posts: list[dict], comments: list[dict] | None = None) -> dict:
    """The judgement half. Never raises -- returns {"error": ...} so a
    failed analysis still leaves the counted aggregates intact and
    rendered."""
    if not posts:
        return {"error": "No Facebook posts mentioning this person were found."}
    client = _anthropic()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    digest = _digest(posts, comments)
    valid_ids = {str(d["id"]) for d in digest if d.get("id")}
    digest_by_id = {str(d["id"]): d for d in digest if d.get("id")}
    payload = {"person": full_name, "items": digest}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=16000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("tlpr_facebook_pulse: analysis call failed for %r: %s", full_name, e)
        return {"error": "The Facebook conversation analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "".join(text_blocks)
    candidate = _extract_json_object(raw)
    if candidate is None:
        stop_reason = getattr(resp, "stop_reason", None)
        logger.warning("tlpr_facebook_pulse: unparsable analysis for %r (stop_reason=%s, text_blocks=%d, chars=%d)",
                       full_name, stop_reason, len(text_blocks), len(raw))
        if stop_reason == "max_tokens":
            return {"error": "The Facebook conversation analysis ran out of output budget before it "
                             "finished (stop_reason=max_tokens). Raise max_tokens in "
                             "tracker/tlpr_facebook_pulse.py or reduce MAX_ITEMS_DIGEST."}
        return {"error": "The Facebook conversation analysis returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The Facebook conversation analysis returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The Facebook conversation analysis returned an unexpected shape."}
    return _clean_analysis(parsed, valid_ids, digest_by_id)


def build_pulse(full_name: str) -> dict:
    """The whole Facebook conversation read, ready to store and render.
    Never raises: every failure mode degrades to a well-formed dict with
    a `note` explaining what a reader is looking at."""
    result: dict = {
        "person": full_name,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "post_count": 0,
        "comment_sample_count": 0,
        "author_count": 0,
        "engagement_total": 0,
        "top_authors": [],
        "top_posts": [],
        "analysis": None,
        "errors": {},
        "note": "",
    }
    try:
        posts, errors = collect_mentions(full_name)
    except Exception as e:
        logger.warning("tlpr_facebook_pulse: mention collection failed for %r: %s", full_name, e)
        result["note"] = ("Facebook search could not be completed for this person (%s)."
                          % (str(e)[:160] or type(e).__name__))
        return result

    result["errors"] = errors
    result.update(aggregate(posts))
    if not posts:
        result["note"] = ("No Facebook posts mentioning this person were found. That is a "
                          "finding, not an error: this person has no measurable Facebook "
                          "conversation to read.")
        return result

    token = os.environ.get("APIFY_API_TOKEN", "")
    comments = []
    if token:
        try:
            comments = _collect_post_comments(posts, token)
        except Exception as e:
            logger.warning("tlpr_facebook_pulse: comment collection failed for %r: %s", full_name, e)
    result["comment_sample_count"] = len(comments)
    result["analysis"] = analyze(full_name, posts, comments)
    return result
