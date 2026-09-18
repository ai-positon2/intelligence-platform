"""Reddit conversation ("pulse") about a person, for Thought Leader
Intelligence's Phase 2 (audience reaction).

Deliberately mirrors tracker/sci_reddit_pulse.py almost exactly -- that
module's reasoning for WHY Reddit needs its own read (most subjects have no
owned Reddit account, so a mentions-search is the only way Reddit
contributes anything) applies even more to a person than to a company: a
thought leader owning a Reddit account is rarer still. This file only
changes what has to change for the subject being a named individual rather
than a company: no domain-based precision query (people don't have one),
no owned-subreddit resolution step, and a system prompt that asks about a
person's reputation rather than a company's positioning.

Same division of labour as the original: Claude judges (is this thread
praise or a complaint, who else does Reddit compare them to), plain Python
counts (how many threads, in which subreddits, trending which way). Asking
a model to also tally its own judgements is how you get a confident
sentiment split that does not match the threads it was derived from.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from tracker import claude_websearch

logger = logging.getLogger(__name__)

PER_QUERY_LIMIT = 100
MAX_THREADS_ANALYZED = 60
MAX_TOP_THREADS = 12

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

_SYSTEM = (
    "You analyze what people on Reddit actually say about a named public "
    "figure, for a PR/reputation research tool used by a marketing agency. "
    "You are given real Reddit threads: subreddit, title, an excerpt of the "
    "body, score and comment count. Reddit is candid and unfiltered, which "
    "is exactly why it is worth reading -- report what is genuinely there, "
    "including criticism, rather than a flattering summary.\n\n"
    "Ground everything in the threads you were given. Never infer a fact "
    "about this person that no thread supports, and never soften a "
    "recurring complaint into a neutral observation. If the threads are "
    "mostly incidental mentions rather than real discussion of the person, "
    "say that plainly -- that is a real and useful finding, not a "
    "failure.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"thread_sentiment": {"<thread_id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|'
    '"comparison"|"neutral", "detail": str, "thread_ids": [str, ...]}], '
    '"compared_to": [{"name": str, "context": str, "thread_ids": [str, ...]}], '
    '"audience": [str, ...], "risk_flags": [str, ...]}\n\n'
    "Rules: \"verdict\" is ONE sentence a comms lead could repeat in a "
    "meeting. \"thread_sentiment\" must label EVERY thread id you were "
    "given, judged toward this person specifically, not the thread's "
    "general mood. \"themes\" is 3-6 recurring topics, each with a concrete "
    "\"detail\" (one or two sentences, specific to this person, never "
    "generic advice) and 1-4 supporting thread_ids copied exactly from the "
    "digest. \"compared_to\" lists other people or organizations Reddit "
    "users actually name alongside this person, with what the comparison "
    "was about; empty list if none appear. \"audience\" is 2-4 short notes "
    "on who is talking and what they care about. \"risk_flags\" is 0-4 "
    "specific, concrete reputational risks this conversation surfaces "
    "(a recurring controversy, a credibility question) -- empty list if "
    "the conversation raises none, never invented to fill the field."
)


def _anthropic():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=120.0, max_retries=1)


def build_queries(full_name: str, company_hint: str | None = None) -> list[str]:
    """The searches that make up one pulse. Quoted exact-phrase first,
    because an unquoted multi-word name matches any thread using those
    words separately and floods the corpus with noise (worse for a person
    than a company: "Grant Wilson" unquoted matches any thread mentioning a
    grant and a wilson[e]d anything, in two unrelated words)."""
    name = (full_name or "").strip()
    if not name:
        return []
    queries = ['"%s"' % name]
    hint = (company_hint or "").strip()
    if hint:
        # A person's name plus their known employer is the highest-precision
        # second query available -- there is no domain equivalent for an
        # individual the way sci_reddit_pulse has one for a company.
        queries.append('"%s" %s' % (name, hint))
    return queries


def _mentions_person(post: dict, full_name: str) -> bool:
    """Reddit's search is fuzzy and will return threads matching on
    stemming or on only part of a multi-word name, so every hit is
    re-checked against its own text before it is allowed to count as a
    mention. A common name (or one that is also an ordinary word) collects
    unrelated threads otherwise."""
    name = (full_name or "").strip().lower()
    if not name:
        return False
    raw = post.get("raw") or {}
    haystack = " ".join([
        str(raw.get("title") or ""),
        str(post.get("caption") or ""),
        str(raw.get("subreddit") or ""),
    ]).lower()
    if name in haystack:
        return True
    # A hyphenated or apostrophed name ("Jean-Claude", "O'Brien") is written
    # without the punctuation about as often as with it -- same fallback
    # sci_reddit_pulse._mentions_company uses for "Position2" vs "Position 2".
    flat_name = re.sub(r"[^a-z0-9]", "", name)
    return bool(flat_name and flat_name in re.sub(r"[^a-z0-9]", "", haystack))


def collect_mentions(full_name: str, company_hint: str | None = None,
                     limit: int = PER_QUERY_LIMIT) -> list[dict]:
    """Every distinct Reddit thread that really mentions this person,
    deduped across queries. [] on any failure."""
    from tracker import sci_reddit_client

    seen: dict[str, dict] = {}
    for query in build_queries(full_name, company_hint):
        for sort in ("relevance", "new"):
            try:
                found = sci_reddit_client.search_posts(
                    query, sort=sort, time_filter="year", limit=limit)
            except Exception as e:
                logger.warning("tlpr_reddit_pulse: search failed for %r (%s): %s", query, sort, e)
                continue
            for post in found:
                pid = post.get("platform_post_id")
                if pid and pid not in seen and _mentions_person(post, full_name):
                    seen[pid] = post
    return list(seen.values())


def _month(posted_at: str | None) -> str | None:
    if not posted_at:
        return None
    try:
        return datetime.fromisoformat(str(posted_at).replace("Z", "+00:00")).strftime("%Y-%m")
    except ValueError:
        return None


def _engagement(post: dict) -> int:
    m = post.get("metrics") or {}
    return int(m.get("likes") or 0) + int(m.get("comments") or 0)


def aggregate(posts: list[dict]) -> dict:
    """The mechanical half: everything that is a count, computed in code so
    it always matches the threads it came from."""
    by_sub = defaultdict(lambda: {"threads": 0, "comments": 0, "score": 0})
    months = Counter()
    score_total = comment_total = 0
    for post in posts:
        raw = post.get("raw") or {}
        metrics = post.get("metrics") or {}
        sub = raw.get("subreddit") or "unknown"
        score = int(metrics.get("likes") or 0)
        comments = int(metrics.get("comments") or 0)
        by_sub[sub]["threads"] += 1
        by_sub[sub]["score"] += score
        by_sub[sub]["comments"] += comments
        score_total += score
        comment_total += comments
        month = _month(post.get("posted_at"))
        if month:
            months[month] += 1

    subreddits = sorted(
        ({"name": name, **vals} for name, vals in by_sub.items()),
        key=lambda s: (s["threads"], s["score"]), reverse=True)
    top = sorted(posts, key=_engagement, reverse=True)[:MAX_TOP_THREADS]
    return {
        "thread_count": len(posts),
        "comment_total": comment_total,
        "score_total": score_total,
        "subreddit_count": len(by_sub),
        "subreddits": subreddits[:12],
        "timeline": [{"month": m, "threads": n} for m, n in sorted(months.items())],
        "top_threads": [_thread_card(p) for p in top],
    }


def _thread_card(post: dict) -> dict:
    raw = post.get("raw") or {}
    metrics = post.get("metrics") or {}
    return {
        "id": post.get("platform_post_id"),
        "title": raw.get("title") or (post.get("caption") or "")[:120],
        "subreddit": raw.get("subreddit"),
        "url": post.get("post_url"),
        "score": metrics.get("likes"),
        "comments": metrics.get("comments"),
        "posted_at": post.get("posted_at"),
        "flair": raw.get("link_flair_text"),
    }


def _digest(posts: list[dict]) -> list[dict]:
    """What Claude actually reads. Ordered by engagement so that if the cap
    bites, it drops the threads nobody engaged with rather than an
    arbitrary slice."""
    ordered = sorted(posts, key=_engagement, reverse=True)[:MAX_THREADS_ANALYZED]
    out = []
    for post in ordered:
        raw = post.get("raw") or {}
        body = (post.get("caption") or "").strip()
        out.append({
            "id": post.get("platform_post_id"),
            "subreddit": raw.get("subreddit"),
            "title": raw.get("title"),
            "excerpt": body[:700],
            "score": (post.get("metrics") or {}).get("likes"),
            "comments": (post.get("metrics") or {}).get("comments"),
            "posted_at": post.get("posted_at"),
        })
    return out


def _extract_json_object(raw: str) -> str | None:
    """Same string-aware brace scan as sci_reddit_pulse._extract_json_object
    (itself mirroring sci_identify.py) -- a model reply routinely arrives
    wrapped in a code fence or a sentence of preamble, and a brace inside a
    quoted value must not unbalance it."""
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


def _clean_analysis(parsed: dict, valid_ids: set[str]) -> dict:
    """Strip every thread id the model was not actually given, drop any
    theme or comparison left with no real citation -- an uncheckable
    citation is worse than none, because it renders as a link to a thread
    that does not exist -- and strip_em_dash every free-text field the model
    wrote, the same discipline every other Claude-authored field in this
    codebase follows since b00d931 unified it (this module's parent,
    sci_reddit_pulse.py, predated that fix and has since been backfilled to
    match)."""
    _clean = claude_websearch.strip_em_dash

    def _cited(entries, *, name_key):
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = [str(i) for i in (entry.get("thread_ids") or []) if str(i) in valid_ids]
            name = _clean(str(entry.get(name_key) or "").strip())
            if not ids or not name:
                continue
            cleaned = dict(entry, thread_ids=ids[:4], **{name_key: name})
            for key in ("detail", "context"):
                if key in cleaned:
                    cleaned[key] = _clean(str(cleaned[key] or ""))
            out.append(cleaned)
        return out

    sentiment_raw = parsed.get("thread_sentiment")
    labels = {}
    if isinstance(sentiment_raw, dict):
        for tid, label in sentiment_raw.items():
            if str(tid) in valid_ids and label in _SENTIMENTS:
                labels[str(tid)] = label
    counts = Counter(labels.values())
    total = sum(counts.values())
    return {
        "verdict": _clean(str(parsed.get("verdict") or "").strip()),
        "sentiment": {
            "counts": {s: counts.get(s, 0) for s in _SENTIMENTS},
            "labelled": total,
            "negative_share": round(counts.get("negative", 0) / total, 3) if total else None,
        },
        "thread_sentiment": labels,
        "themes": _cited(parsed.get("themes"), name_key="label")[:6],
        "compared_to": _cited(parsed.get("compared_to"), name_key="name")[:6],
        "audience": [_clean(str(a).strip()) for a in (parsed.get("audience") or []) if str(a).strip()][:4],
        "risk_flags": [_clean(str(r).strip()) for r in (parsed.get("risk_flags") or []) if str(r).strip()][:4],
    }


def analyze(full_name: str, posts: list[dict]) -> dict:
    """The judgement half. Never raises -- returns {"error": ...} so a failed
    analysis still leaves the counted aggregates intact and rendered."""
    if not posts:
        return {"error": "No Reddit threads mentioning this person were found."}
    client = _anthropic()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    digest = _digest(posts)
    valid_ids = {str(d["id"]) for d in digest if d.get("id")}
    payload = {"person": full_name, "threads": digest}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=8000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("tlpr_reddit_pulse: analysis call failed for %r: %s", full_name, e)
        return {"error": "The Reddit conversation analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "".join(text_blocks)
    candidate = _extract_json_object(raw)
    if candidate is None:
        stop_reason = getattr(resp, "stop_reason", None)
        logger.warning("tlpr_reddit_pulse: unparsable analysis for %r (stop_reason=%s, text_blocks=%d, chars=%d)",
                       full_name, stop_reason, len(text_blocks), len(raw))
        if stop_reason == "max_tokens":
            return {"error": "The Reddit conversation analysis ran out of output budget before "
                             "it finished (stop_reason=max_tokens). Raise max_tokens in "
                             "tracker/tlpr_reddit_pulse.py or reduce MAX_THREADS_ANALYZED."}
        return {"error": "The Reddit conversation analysis returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The Reddit conversation analysis returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The Reddit conversation analysis returned an unexpected shape."}
    return _clean_analysis(parsed, valid_ids)


def build_pulse(full_name: str, company_hint: str | None = None) -> dict:
    """The whole Reddit conversation read, ready to store and render. Never
    raises: every failure mode degrades to a well-formed dict with a `note`
    explaining what a reader is looking at, because a missing section with
    no explanation is what makes a report look broken."""
    from tracker import sci_reddit_client

    result: dict = {
        "person": full_name,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "queries": build_queries(full_name, company_hint),
        "thread_count": 0,
        "subreddits": [],
        "timeline": [],
        "top_threads": [],
        "threads": [],
        "analysis": None,
        "note": "",
    }
    if not sci_reddit_client.is_configured():
        result["note"] = ("Reddit is not configured on this deployment. Set REDDIT_CLIENT_ID "
                          "and REDDIT_CLIENT_SECRET from a script app at reddit.com/prefs/apps.")
        return result

    try:
        posts = collect_mentions(full_name, company_hint)
    except Exception as e:
        logger.warning("tlpr_reddit_pulse: mention collection failed for %r: %s", full_name, e)
        result["note"] = "Reddit search could not be completed for this person."
        return result

    result.update(aggregate(posts))
    result["threads"] = [_thread_card(p) for p in
                         sorted(posts, key=_engagement, reverse=True)[:MAX_THREADS_ANALYZED]]
    if not posts:
        result["note"] = ("No Reddit threads mentioning this person were found in the last year. "
                          "That is a finding, not an error: this person has no measurable Reddit "
                          "conversation to read.")
        return result
    result["analysis"] = analyze(full_name, posts)
    return result
