"""X (Twitter) conversation ("pulse") about a person, for Thought Leader
Intelligence's Phase 2 (audience reaction).

Deliberately mirrors tracker/tlpr_reddit_pulse.py's reasoning, aimed at a
different gap: Phase 1/2's existing X coverage (tracker/sci_source_x.py's
own-timeline fetch, tracker/apify_x_replies.py's reply-thread fetch) only
ever reads what is directly attached to the subject's OWN tweets. Neither
says anything about what OTHER people are posting independently, elsewhere
on the timeline -- a standalone tweet quoting, criticizing or praising the
person without ever replying to them is invisible to both. This module is
that missing leg, the X equivalent of what tlpr_reddit_pulse.py already
does for Reddit.

Built on the SAME actor tracker/sci_source_x.py already pays for
(apidojo/tweet-scraper) -- this is a new INPUT SHAPE against an
already-integrated vendor, not a new vendor decision. Two independent
queries, merged and deduped by tweet id, mirroring tlpr_press.py's own
two-query pattern (a bare query plus a hint-qualified one):
  1. searchTerms=['"Full Name" -from:handle'] -- prose mentions: someone
     typing the name without necessarily @-tagging them. The "-from:"
     exclusion (a real Twitter/X advanced-search operator, not a guess
     about the actor's own output shape) drops retweets/quotes that would
     otherwise surface the subject's own voice in a search meant to
     capture what OTHERS say. Only added when a handle is known.
  2. mentioning=handle -- the actor's own dedicated "@-mentions this
     user" filter. Only run when a handle is known; a bare name search
     alone (query 1, unqualified) is what a person with no known handle
     still gets.

Same division of labour as every other Claude-judged read in this
codebase: Claude judges (favorable or not, what themes recur, who is
worth reading directly), plain Python counts (how many tweets, from how
many distinct authors, total engagement, over what span).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from tracker import apify_transport, claude_websearch, sci_source_x

logger = logging.getLogger(__name__)

FETCH_LIMIT = 100          # per query, before cross-query dedup
MAX_TWEETS_DIGEST = 60     # what Claude actually reads
MAX_TOP_TWEETS = 12        # what the UI shows as tweet cards

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

_SYSTEM = (
    "You analyze what people on X (Twitter) are actually saying ABOUT a "
    "named public figure -- independent posts by OTHER people discussing "
    "them, not replies on the person's own tweets -- for a PR/reputation "
    "research tool used by a marketing agency. You are given real tweets: "
    "author handle, text, and engagement counts. X is candid and "
    "unfiltered, which is exactly why it is worth reading -- report what "
    "is genuinely there, including criticism, rather than a flattering "
    "summary.\n\n"
    "A tweet may still be authored by the subject themselves (a retweet "
    "or quote can slip past a search) or be about a different, unrelated "
    "person who happens to share this name. Exclude anything clearly "
    "written BY the subject or clearly about someone else, rather than "
    "forcing it into the read.\n\n"
    "Ground everything in the tweets you were given. Never infer a fact "
    "this data doesn't support. If the tweets are mostly incidental "
    "mentions rather than substantive discussion, say that plainly -- "
    "that is a real finding, not a failure.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"tweet_sentiment": {"<tweet_id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|"neutral", '
    '"detail": str, "tweet_ids": [str, ...]}], '
    '"notable_tweets": [{"tweet_id": str, "why": str}], '
    '"risk_flags": [str, ...]}\n\n'
    "Rules: \"verdict\" is ONE sentence summarizing how X talks about this "
    "person right now. \"tweet_sentiment\" must label EVERY tweet id you "
    "were given that is genuinely about this person, toward them "
    "specifically -- omit any id you determined is the subject's own "
    "voice or about someone else. \"themes\" is 2-5 recurring topics, "
    "each with a concrete \"detail\" (specific to these tweets, never "
    "generic advice) and 1-4 supporting tweet_ids copied exactly from the "
    "data. \"notable_tweets\" is 2-4 posts genuinely worth reading "
    "directly, each citing a real tweet_id and a one-sentence \"why\". "
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


def _run_actor(run_input: dict, token: str) -> list[dict]:
    return apify_transport.run_actor_and_wait(sci_source_x.actor_id(), run_input, token, strict=True)


def collect_mentions(full_name: str, x_handle: str | None = None,
                     limit: int = FETCH_LIMIT) -> tuple[list[dict], dict]:
    """Every distinct tweet that genuinely mentions this person, deduped by
    id across both queries. Returns (tweets, errors) -- one query failing
    never blocks the other, same fault isolation as every other multi-
    source collector in this codebase (tlpr_press.collect_coverage,
    Phase 1's per-platform posts)."""
    name = (full_name or "").strip()
    errors: dict[str, str] = {}
    if not name:
        return [], errors
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        errors["apify"] = "Apify is not configured on this deployment."
        return [], errors
    handle = (x_handle or "").lstrip("@").strip() or None

    seen: dict[str, dict] = {}

    def _add(raw_items):
        for t in sci_source_x.normalize(raw_items):
            pid = t.get("platform_post_id")
            if pid and pid not in seen:
                seen[pid] = t

    term = '"%s"' % name
    if handle:
        term += " -from:%s" % handle
    try:
        _add(_run_actor({"searchTerms": [term], "maxItems": limit, "sort": "Latest"}, token))
    except apify_transport.ApifyTransportError as e:
        errors["x_search"] = str(e)

    if handle:
        try:
            _add(_run_actor({"mentioning": handle, "maxItems": limit, "sort": "Latest"}, token))
        except apify_transport.ApifyTransportError as e:
            errors.setdefault("x_mentions", str(e))

    return list(seen.values()), errors


def _parse_posted(tweet: dict) -> datetime | None:
    raw = tweet.get("posted_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _month(tweet: dict) -> str | None:
    dt = _parse_posted(tweet)
    return dt.strftime("%Y-%m") if dt else None


def _engagement(tweet: dict) -> int:
    m = tweet.get("metrics") or {}
    return int(m.get("likes") or 0) + int(m.get("shares") or 0) + int(m.get("comments") or 0)


def _author(tweet: dict) -> str | None:
    """Best-effort author handle straight from what the vendor returned,
    never trusted to be present under any one key -- this actor's exact
    output shape has never been confirmed against a live response (see
    tracker/sci_source_x.py, which has the same gap), so every plausible
    path is tried and the field is simply omitted rather than guessed at
    if none of them are there."""
    raw = tweet.get("raw") or {}
    author = raw.get("author") or {}
    if isinstance(author, dict):
        handle = author.get("userName") or author.get("username") or author.get("screen_name")
        if handle:
            return str(handle)
    for key in ("authorHandle", "username", "screen_name"):
        if raw.get(key):
            return str(raw[key])
    return None


def _tweet_card(tweet: dict) -> dict:
    return {
        "id": tweet.get("platform_post_id"),
        "author": _author(tweet),
        "text": (tweet.get("caption") or "")[:280],
        "url": tweet.get("post_url"),
        "posted_at": tweet.get("posted_at"),
        "likes": (tweet.get("metrics") or {}).get("likes"),
    }


def aggregate(tweets: list[dict]) -> dict:
    """The mechanical half: everything that is a count, computed in code so
    it always matches the tweets it came from."""
    authors = Counter(a for a in (_author(t) for t in tweets) if a)
    months = Counter(m for m in (_month(t) for t in tweets) if m)
    dates = sorted(d for d in (_parse_posted(t) for t in tweets) if d)
    engagement_total = sum(_engagement(t) for t in tweets)

    ordered = sorted(tweets, key=_engagement, reverse=True)
    return {
        "tweet_count": len(tweets),
        "author_count": len(authors),
        "engagement_total": engagement_total,
        "top_authors": [{"handle": a, "tweets": n} for a, n in authors.most_common(12)],
        "timeline": [{"month": m, "tweets": n} for m, n in sorted(months.items())],
        "earliest": dates[0].isoformat() if dates else None,
        "latest": dates[-1].isoformat() if dates else None,
        "top_tweets": [_tweet_card(t) for t in ordered[:MAX_TOP_TWEETS]],
    }


def _digest(tweets: list[dict], max_items: int = MAX_TWEETS_DIGEST) -> list[dict]:
    """What Claude actually reads, ordered by engagement so a cap bites the
    least-noticed tweets first, never an arbitrary slice."""
    ordered = sorted(tweets, key=_engagement, reverse=True)[:max_items]
    out = []
    for i, t in enumerate(ordered):
        out.append({
            "id": str(i),
            "author": _author(t),
            "text": (t.get("caption") or "")[:400],
            "url": t.get("post_url"),
            "posted_at": t.get("posted_at"),
            "likes": (t.get("metrics") or {}).get("likes"),
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
    """Strip every tweet id the model was not actually given, drop any
    theme or notable-tweet entry left with no real citation, and
    strip_em_dash every free-text field the model wrote. notable_tweets is
    enriched server-side from digest_by_id (never from the model's own
    reply), same reasoning as tlpr_press._clean_analysis: a tweet_id has a
    real public URL behind it, worth surfacing as a link a reader can
    actually open."""
    digest_by_id = digest_by_id or {}
    _clean = claude_websearch.strip_em_dash

    def _cited(entries, *, name_key):
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = [str(i) for i in (entry.get("tweet_ids") or []) if str(i) in valid_ids]
            name = _clean(str(entry.get(name_key) or "").strip())
            if not ids or not name:
                continue
            cleaned = dict(entry, tweet_ids=ids[:4], **{name_key: name})
            if "detail" in cleaned:
                cleaned["detail"] = _clean(str(cleaned["detail"] or ""))
            out.append(cleaned)
        return out

    sentiment_raw = parsed.get("tweet_sentiment")
    labels = {}
    if isinstance(sentiment_raw, dict):
        for tid, label in sentiment_raw.items():
            if str(tid) in valid_ids and label in _SENTIMENTS:
                labels[str(tid)] = label
    counts = Counter(labels.values())
    total = sum(counts.values())

    notable = []
    for entry in (parsed.get("notable_tweets") or []):
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("tweet_id") or "")
        why = _clean(str(entry.get("why") or "").strip())
        if tid in valid_ids and why:
            src = digest_by_id.get(tid) or {}
            notable.append({"tweet_id": tid, "why": why[:300],
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
        "notable_tweets": notable[:4],
        "risk_flags": [_clean(str(r).strip()) for r in (parsed.get("risk_flags") or []) if str(r).strip()][:4],
    }


def analyze(full_name: str, tweets: list[dict]) -> dict:
    """The judgement half. Never raises -- returns {"error": ...} so a
    failed analysis still leaves the counted aggregates intact and
    rendered."""
    if not tweets:
        return {"error": "No X posts mentioning this person were found."}
    client = _anthropic()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    digest = _digest(tweets)
    valid_ids = {d["id"] for d in digest}
    digest_by_id = {d["id"]: d for d in digest}
    payload = {"person": full_name, "tweets": digest}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=8000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("tlpr_x_pulse: analysis call failed for %r: %s", full_name, e)
        return {"error": "The X conversation analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "".join(text_blocks)
    candidate = _extract_json_object(raw)
    if candidate is None:
        stop_reason = getattr(resp, "stop_reason", None)
        logger.warning("tlpr_x_pulse: unparsable analysis for %r (stop_reason=%s, text_blocks=%d, chars=%d)",
                       full_name, stop_reason, len(text_blocks), len(raw))
        if stop_reason == "max_tokens":
            return {"error": "The X conversation analysis ran out of output budget before it "
                             "finished (stop_reason=max_tokens). Raise max_tokens in "
                             "tracker/tlpr_x_pulse.py or reduce MAX_TWEETS_DIGEST."}
        return {"error": "The X conversation analysis returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The X conversation analysis returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The X conversation analysis returned an unexpected shape."}
    return _clean_analysis(parsed, valid_ids, digest_by_id)


def build_pulse(full_name: str, x_handle: str | None = None) -> dict:
    """The whole X conversation read, ready to store and render. Never
    raises: every failure mode degrades to a well-formed dict with a
    `note` explaining what a reader is looking at."""
    result: dict = {
        "person": full_name,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "tweet_count": 0,
        "author_count": 0,
        "engagement_total": 0,
        "top_authors": [],
        "timeline": [],
        "earliest": None,
        "latest": None,
        "top_tweets": [],
        "analysis": None,
        "errors": {},
        "note": "",
    }
    try:
        tweets, errors = collect_mentions(full_name, x_handle)
    except Exception as e:
        logger.warning("tlpr_x_pulse: mention collection failed for %r: %s", full_name, e)
        result["note"] = "X search could not be completed for this person."
        return result

    result["errors"] = errors
    result.update(aggregate(tweets))
    if not tweets:
        result["note"] = ("No X posts mentioning this person were found. That is a finding, "
                          "not an error: this person has no measurable X conversation to read.")
        return result
    result["analysis"] = analyze(full_name, tweets)
    return result
