"""Reddit brand conversation ("pulse") for Social Creative Intelligence.

WHY THIS EXISTS AT ALL, AND WHY IT IS NOT JUST A SEVENTH POST FEED:

The other six platforms in this agent answer one question -- what does this
company publish? Reddit answers a different and, for most B2B companies, a
more useful one: what does the market SAY about this company? Almost no B2B
company posts as u/Acme, so wiring Reddit up purely as a seventh owned-posts
feed would hand nearly every report a seventh empty row reading "Account not
confidently identified", which is worse than not adding Reddit at all.

So Reddit contributes two separate things:
  1. Owned posts, when a real company account exists -- handled by
     sci_reddit_client.resolve_company_account + the normal pipeline, and
     genuinely absent for most companies.
  2. This: the conversation about the company across all of Reddit, which
     works for ANY company with a discussion footprint, owned account or not.

The division of labour below is deliberate: Claude judges (what is this
thread about, is it praise or a complaint, who is the company compared
against), and plain Python counts (how many threads, in which subreddits,
trending which way). Asking a model to also tally its own judgements is how
you get a confident sentiment split that does not match the threads it was
derived from.

Mirrors tracker/sci_synthesize.py's conventions: reads ANTHROPIC_API_KEY
itself, degrades to an error dict rather than raising, and every claim must
cite thread ids that were actually in the digest it was given.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# How many threads to pull per query before dedupe. Reddit's search caps a
# page at 100; three queries at 100 is well inside the app-only budget of
# ~100 requests/minute while giving the analysis a real corpus.
PER_QUERY_LIMIT = 100
MAX_THREADS_ANALYZED = 60
MAX_TOP_THREADS = 12

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

_SYSTEM = (
    "You analyze what people on Reddit actually say about a company, for a "
    "marketing team that needs to act on it. You are given real Reddit "
    "threads: subreddit, title, an excerpt of the body, score and comment "
    "count. Reddit is candid and unfiltered, which is exactly why it is "
    "worth reading -- report what is genuinely there, including criticism, "
    "rather than a flattering summary.\n\n"
    "Ground everything in the threads you were given. Never infer a fact "
    "about the company that no thread supports, and never soften a "
    "recurring complaint into a neutral observation. If the threads are "
    "mostly incidental mentions rather than real discussion of the company, "
    "say that plainly -- that is a real and useful finding, not a failure.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"thread_sentiment": {"<thread_id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|'
    '"comparison"|"neutral", "detail": str, "thread_ids": [str, ...]}], '
    '"competitors": [{"name": str, "context": str, "thread_ids": [str, ...]}], '
    '"audience": [str, ...], "opportunities": [str, ...]}\n\n'
    "Rules: \"verdict\" is ONE sentence a marketer could repeat in a "
    "meeting. \"thread_sentiment\" must label EVERY thread id you were "
    "given, judged toward the company specifically, not the thread's "
    "general mood. \"themes\" is 3-6 recurring topics, each with a concrete "
    "\"detail\" (one or two sentences, specific to this company, never "
    "generic marketing advice) and 1-4 supporting thread_ids copied exactly "
    "from the digest. \"competitors\" lists companies Reddit users actually "
    "name alongside this one, with what the comparison was about; empty "
    "list if none appear. \"audience\" is 2-4 short notes on who is talking "
    "and what they care about. \"opportunities\" is 2-4 specific, concrete "
    "content or messaging openings this conversation suggests."
)


def _anthropic():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=120.0, max_retries=1)


def _domain(company_url: str | None) -> str | None:
    if not company_url:
        return None
    m = re.search(r"^(?:https?://)?(?:www\.)?([^/\s?#]+)", company_url.strip())
    if not m:
        return None
    host = m.group(1).lower()
    return host if "." in host else None


def build_queries(company_name: str, company_url: str | None = None) -> list[str]:
    """The searches that make up one pulse. Quoted exact-phrase first,
    because an unquoted multi-word company name matches any thread using
    those words separately and floods the corpus with noise."""
    name = (company_name or "").strip()
    if not name:
        return []
    queries = ['"%s"' % name]
    domain = _domain(company_url)
    if domain:
        # A domain mention is the highest-precision signal Reddit offers:
        # nobody writes acme.com incidentally the way they write "acme".
        queries.append('"%s"' % domain)
    return queries


def _mentions_company(post: dict, company_name: str, domain: str | None) -> bool:
    """Reddit's search is fuzzy and will return threads that match on
    stemming or on only one word of a multi-word name, so every hit is
    re-checked against its own text before it is allowed to count as a
    mention. Without this, a company called "Northstar Anesthesia" collects
    every thread mentioning anesthesia."""
    name = (company_name or "").strip().lower()
    if not name:
        return False
    raw = post.get("raw") or {}
    haystack = " ".join([
        str(raw.get("title") or ""),
        str(post.get("caption") or ""),
        str(raw.get("subreddit") or ""),
        str(raw.get("domain") or ""),
    ]).lower()
    if name in haystack:
        return True
    # "Position2" is written "Position 2" about as often as not, so compare
    # with separators stripped on both sides too.
    flat_name = re.sub(r"[^a-z0-9]", "", name)
    if flat_name and flat_name in re.sub(r"[^a-z0-9]", "", haystack):
        return True
    return bool(domain and domain in haystack)


def collect_mentions(company_name: str, company_url: str | None = None,
                     limit: int = PER_QUERY_LIMIT) -> list[dict]:
    """Every distinct Reddit thread that really mentions this company,
    newest-agnostic, deduped across queries. [] on any failure."""
    from tracker import sci_reddit_client

    domain = _domain(company_url)
    seen: dict[str, dict] = {}
    for query in build_queries(company_name, company_url):
        # Two passes per query: `relevance` surfaces the threads that
        # matter, `new` guarantees the last few weeks are represented even
        # when they are all low-score. Ranking alone would hide a fresh
        # complaint wave behind a popular two-year-old thread.
        for sort in ("relevance", "new"):
            try:
                found = sci_reddit_client.search_posts(
                    query, sort=sort, time_filter="year", limit=limit)
            except Exception as e:
                logger.warning("sci_reddit_pulse: search failed for %r (%s): %s", query, sort, e)
                continue
            for post in found:
                pid = post.get("platform_post_id")
                if pid and pid not in seen and _mentions_company(post, company_name, domain):
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
        "upvote_ratio": raw.get("upvote_ratio"),
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
    """Same string-aware brace scan as tracker/sci_identify.py -- a model
    reply routinely arrives wrapped in a code fence or a sentence of
    preamble, and a brace inside a quoted value must not unbalance it."""
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
    """Strip every thread id the model was not actually given, and drop any
    theme or competitor left with no real citation. Same discipline as
    sci_synthesize._clean_claims: an uncheckable citation is worse than none,
    because it renders as a link to a thread that does not exist."""
    def _cited(entries, *, name_key):
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = [str(i) for i in (entry.get("thread_ids") or []) if str(i) in valid_ids]
            if not ids or not str(entry.get(name_key) or "").strip():
                continue
            out.append({**entry, "thread_ids": ids[:4]})
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
        "verdict": str(parsed.get("verdict") or "").strip(),
        "sentiment": {
            "counts": {s: counts.get(s, 0) for s in _SENTIMENTS},
            "labelled": total,
            # Computed here, never taken from the model: the share of
            # judged threads that were negative is the number a marketer
            # will act on, and it has to match the labels above exactly.
            "negative_share": round(counts.get("negative", 0) / total, 3) if total else None,
        },
        "thread_sentiment": labels,
        "themes": _cited(parsed.get("themes"), name_key="label")[:6],
        "competitors": _cited(parsed.get("competitors"), name_key="name")[:6],
        "audience": [str(a).strip() for a in (parsed.get("audience") or []) if str(a).strip()][:4],
        "opportunities": [str(o).strip() for o in (parsed.get("opportunities") or []) if str(o).strip()][:4],
    }


def analyze(company_name: str, posts: list[dict]) -> dict:
    """The judgement half. Never raises -- returns {"error": ...} so a failed
    analysis still leaves the counted aggregates intact and rendered."""
    if not posts:
        return {"error": "No Reddit threads mentioning this company were found."}
    client = _anthropic()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    digest = _digest(posts)
    valid_ids = {str(d["id"]) for d in digest if d.get("id")}
    payload = {"company": company_name, "threads": digest}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=4000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("sci_reddit_pulse: analysis call failed for %r: %s", company_name, e)
        return {"error": "The Reddit conversation analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    # web_search is not used here, but joining every text block rather than
    # reading the last one is the house rule now -- reading content[-1] is
    # exactly what silently broke sci_identify for months.
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    candidate = _extract_json_object(raw)
    if candidate is None:
        return {"error": "The Reddit conversation analysis returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The Reddit conversation analysis returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The Reddit conversation analysis returned an unexpected shape."}
    return _clean_analysis(parsed, valid_ids)


def build_pulse(company_name: str, company_url: str | None = None) -> dict:
    """The whole Reddit conversation read, ready to store and render.
    Never raises: every failure mode degrades to a well-formed dict with a
    `note` explaining what a reader is looking at, because a missing
    section with no explanation is what makes a report look broken."""
    from tracker import sci_reddit_client

    result: dict = {
        "company": company_name,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "queries": build_queries(company_name, company_url),
        "thread_count": 0,
        "subreddits": [],
        "timeline": [],
        "top_threads": [],
        "threads": [],
        "community": None,
        "analysis": None,
        "note": "",
    }
    if not sci_reddit_client.is_configured():
        result["note"] = ("Reddit is not configured on this deployment. Set REDDIT_CLIENT_ID "
                          "and REDDIT_CLIENT_SECRET from a script app at reddit.com/prefs/apps.")
        return result

    try:
        result["community"] = sci_reddit_client.resolve_company_subreddit(company_name)
    except Exception as e:
        logger.warning("sci_reddit_pulse: subreddit lookup failed for %r: %s", company_name, e)

    try:
        posts = collect_mentions(company_name, company_url)
    except Exception as e:
        logger.warning("sci_reddit_pulse: mention collection failed for %r: %s", company_name, e)
        result["note"] = "Reddit search could not be completed for this company."
        return result

    result.update(aggregate(posts))
    # Every thread the analysis was actually shown, so a cited thread_id
    # always resolves to a real card in the report. top_threads alone is not
    # enough: a theme can legitimately cite a low-engagement thread, and a
    # citation that renders as a dead reference is worse than no citation.
    result["threads"] = [_thread_card(p) for p in
                         sorted(posts, key=_engagement, reverse=True)[:MAX_THREADS_ANALYZED]]
    if not posts:
        result["note"] = ("No Reddit threads mentioning this company were found in the last year. "
                          "That is a finding, not an error: this brand has no measurable Reddit "
                          "conversation to read.")
        return result
    result["analysis"] = analyze(company_name, posts)
    return result
