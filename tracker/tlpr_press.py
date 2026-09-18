"""Earned media / press coverage about a person, for Thought Leader
Intelligence's Phase 3.

Built on tracker/news_client.py's low-level fetchers (_gdelt_articles,
_serpapi_articles) rather than its own get_news_articles(). That function's
relevance filter (tracker/news_relevance.py) is scored entirely for
CORPORATE business events -- funding, M&A, leadership moves, product
launches -- and would silently drop most of what actually makes up a
person's earned media: interviews, op-eds, quoted commentary, awards,
conference mentions. tracker/jobs_client.py already reaches into
news_client's private fetchers for its own different-domain need
(creative-hiring signals), so bypassing get_news_articles() here is a
precedented pattern, not a new one.

Precision comes from the query itself rather than a post-hoc keyword
filter: GDELT already wraps its query in an exact phrase match, and this
module quotes the person's name for SerpAPI the same way. A common name
still collides with unrelated people who share it -- the same limitation
tlpr_reddit_pulse.py accepts for Reddit -- so the system prompt below asks
Claude to notice and exclude coverage that is clearly about someone else.

Same division of labour as tlpr_reddit_pulse.py: Claude judges (what the
coverage is actually about, whether it's favorable), plain Python counts
(how many articles, from how many distinct outlets, over what span).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from tracker import claude_websearch
from .news_client import MAX_NEWS_AGE_DAYS, _gdelt_articles, _parse_article_date, _serpapi_articles

logger = logging.getLogger(__name__)

FETCH_POOL = 20            # per source query, before cross-source dedup
MAX_ARTICLES_DIGEST = 40   # what Claude actually reads
MAX_TOP_ARTICLES = 12      # what the UI shows as article cards

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

_SYSTEM = (
    "You analyze earned media and press coverage about a named public "
    "figure, for a PR/reputation research tool used by a marketing agency. "
    "You are given real news articles that mention them: title, source "
    "publication, publish date, and a snippet when one is available. "
    "Report what the coverage actually says, including anything "
    "unflattering or critical, rather than a flattering summary.\n\n"
    "Some articles may be about a different, unrelated person who happens "
    "to share this name -- a real risk of name-based news search. Use the "
    "role/company context you were given about the actual subject to judge "
    "this, and exclude anything clearly about someone else rather than "
    "forcing it into the read.\n\n"
    "Ground everything in the articles you were given. Never infer a fact "
    "this data doesn't support. If the coverage is thin, generic, or mostly "
    "incidental mentions rather than substantive coverage of this person, "
    "say that plainly -- that is a real finding, not a failure.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"verdict": str, '
    '"article_sentiment": {"<article_id>": "positive"|"neutral"|"negative"|"mixed"}, '
    '"themes": [{"label": str, "stance": "praise"|"complaint"|"question"|"neutral", '
    '"detail": str, "article_ids": [str, ...]}], '
    '"notable_articles": [{"article_id": str, "why": str}], '
    '"risk_flags": [str, ...]}\n\n'
    "Rules: \"verdict\" is ONE sentence summarizing their current earned-media "
    "position. \"article_sentiment\" must label EVERY article id you were "
    "given that is genuinely about this person (toward them specifically, "
    "not the outlet's general tone) -- omit any id you determined is about "
    "someone else. \"themes\" is 2-5 recurring topics the coverage centers "
    "on, each with a concrete \"detail\" (specific to these articles, never "
    "generic advice) and 1-4 supporting article_ids copied exactly from the "
    "data. \"notable_articles\" is 2-4 pieces genuinely worth reading "
    "directly, each citing a real article_id and a one-sentence \"why\". "
    "\"risk_flags\" is 0-4 specific reputational risks this coverage "
    "surfaces -- empty list if it raises none, never invented to fill the "
    "field."
)


def _anthropic():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=120.0, max_retries=1)


def collect_coverage(full_name: str, company_hint: str | None = None,
                     max_age_days: int = MAX_NEWS_AGE_DAYS) -> tuple[list[dict], dict]:
    """Every distinct article turned up by GDELT + SerpAPI, deduped by
    normalized title. Returns (articles, errors) -- one source being
    unavailable never blocks the other, same fault isolation as Phase 1/2's
    own per-platform collectors."""
    name = (full_name or "").strip()
    errors: dict[str, str] = {}
    if not name:
        return [], errors

    seen: dict[str, dict] = {}

    def _add(raw_articles, source_key):
        for a in raw_articles or []:
            title = (a.get("title") or "").strip()
            if not title:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen[key] = dict(a, fetched_from=source_key)

    # GDELT: free, no key needed, and news_client wraps the query as an
    # exact phrase itself -- pass the bare name, never pre-quote it.
    try:
        _add(_gdelt_articles(name, FETCH_POOL, max_age_days), "gdelt")
    except Exception as e:
        logger.warning("tlpr_press: GDELT fetch failed for %r: %s", name, e)

    serpapi_key = os.environ.get("SERPAPI_KEY", "")
    if not serpapi_key:
        errors["serpapi"] = "SerpAPI is not configured on this deployment (GDELT-only coverage)."
    else:
        # Unlike GDELT, news_client does not quote a SerpAPI query itself, so
        # the exact-phrase precision has to be added here. A second,
        # hint-qualified query gets run only when a company/employer is
        # known -- the same extra precision boost tlpr_reddit_pulse adds for
        # Reddit, and just as valuable here: a bare common name alone pulls
        # in every unrelated person who shares it.
        queries = ['"%s"' % name]
        hint = (company_hint or "").strip()
        if hint:
            queries.append('"%s" %s' % (name, hint))
        got_any = False
        for q in queries:
            try:
                hits = _serpapi_articles(q, serpapi_key, FETCH_POOL, max_age_days)
                if hits:
                    got_any = True
                _add(hits, "serpapi")
            except Exception as e:
                logger.warning("tlpr_press: SerpAPI fetch failed for %r: %s", q, e)
        if not got_any and "serpapi" not in errors:
            errors["serpapi"] = "No SerpAPI results were returned for this person."

    return list(seen.values()), errors


def _parse_published(article: dict) -> datetime | None:
    return _parse_article_date(article.get("published") or "")


def _month(article: dict) -> str | None:
    dt = _parse_published(article)
    return dt.strftime("%Y-%m") if dt else None


def aggregate(articles: list[dict]) -> dict:
    """The mechanical half: everything that is a count, computed in code so
    it always matches the articles it came from."""
    by_source = Counter(a.get("source") or "Unknown" for a in articles)
    months = Counter(m for m in (_month(a) for a in articles) if m)
    dates = sorted(d for d in (_parse_published(a) for a in articles) if d)

    ordered = sorted(
        articles,
        key=lambda a: _parse_published(a) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return {
        "article_count": len(articles),
        "source_count": len(by_source),
        "sources": [{"name": name, "articles": n} for name, n in by_source.most_common(12)],
        "timeline": [{"month": m, "articles": n} for m, n in sorted(months.items())],
        "earliest": dates[0].isoformat() if dates else None,
        "latest": dates[-1].isoformat() if dates else None,
        "top_articles": [_article_card(a) for a in ordered[:MAX_TOP_ARTICLES]],
    }


def _article_card(article: dict) -> dict:
    return {
        "title": article.get("title"),
        "url": article.get("url"),
        "source": article.get("source"),
        "published": article.get("published"),
    }


def _digest(articles: list[dict], max_items: int = MAX_ARTICLES_DIGEST) -> list[dict]:
    """What Claude actually reads. Ordered by recency so that if the cap
    bites, it drops the oldest coverage rather than an arbitrary slice --
    there is no engagement metric for news the way Reddit has score."""
    ordered = sorted(
        articles,
        key=lambda a: _parse_published(a) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:max_items]
    out = []
    for i, a in enumerate(ordered):
        out.append({
            "id": str(i),
            "title": a.get("title"),
            "url": a.get("url"),
            "source": a.get("source"),
            "published": a.get("published"),
            "snippet": (a.get("summary") or "")[:400],
        })
    return out


def _extract_json_object(raw: str) -> str | None:
    """Same string-aware brace scan as tlpr_reddit_pulse._extract_json_object
    -- a model reply routinely arrives wrapped in a code fence or a sentence
    of preamble, and a brace inside a quoted value must not unbalance it."""
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
    """Strip every article id the model was not actually given, drop any
    theme or notable-article entry left with no real citation -- an
    uncheckable citation is worse than none -- and strip_em_dash every
    free-text field the model wrote, same discipline as every other
    Claude-authored field in this codebase since b00d931.

    Unlike a comment_id or thread_id, an article_id has a real, public URL
    behind it -- worth surfacing so a notable article is a link a reader can
    actually open, not just a paraphrase. digest_by_id resolves that title/
    url/source directly from what was fed to the model, never from the
    model's own reply, so a fabricated title/url is not possible here."""
    digest_by_id = digest_by_id or {}
    _clean = claude_websearch.strip_em_dash

    def _cited(entries, *, name_key):
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = [str(i) for i in (entry.get("article_ids") or []) if str(i) in valid_ids]
            name = _clean(str(entry.get(name_key) or "").strip())
            if not ids or not name:
                continue
            cleaned = dict(entry, article_ids=ids[:4], **{name_key: name})
            if "detail" in cleaned:
                cleaned["detail"] = _clean(str(cleaned["detail"] or ""))
            out.append(cleaned)
        return out

    sentiment_raw = parsed.get("article_sentiment")
    labels = {}
    if isinstance(sentiment_raw, dict):
        for aid, label in sentiment_raw.items():
            if str(aid) in valid_ids and label in _SENTIMENTS:
                labels[str(aid)] = label
    counts = Counter(labels.values())
    total = sum(counts.values())

    notable = []
    for entry in (parsed.get("notable_articles") or []):
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("article_id") or "")
        why = _clean(str(entry.get("why") or "").strip())
        if aid in valid_ids and why:
            src = digest_by_id.get(aid) or {}
            notable.append({"article_id": aid, "why": why[:300],
                            "title": src.get("title"), "url": src.get("url"),
                            "source": src.get("source")})

    return {
        "verdict": _clean(str(parsed.get("verdict") or "").strip()),
        "sentiment": {
            "counts": {s: counts.get(s, 0) for s in _SENTIMENTS},
            "labelled": total,
            "negative_share": round(counts.get("negative", 0) / total, 3) if total else None,
        },
        "themes": _cited(parsed.get("themes"), name_key="label")[:5],
        "notable_articles": notable[:4],
        "risk_flags": [_clean(str(r).strip()) for r in (parsed.get("risk_flags") or []) if str(r).strip()][:4],
    }


def analyze(full_name: str, articles: list[dict], company_hint: str | None = None) -> dict:
    """The judgement half. Never raises -- returns {"error": ...} so a
    failed analysis still leaves the counted aggregates intact and
    rendered."""
    if not articles:
        return {"error": "No press coverage was found to analyze."}
    client = _anthropic()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    digest = _digest(articles)
    valid_ids = {d["id"] for d in digest}
    digest_by_id = {d["id"]: d for d in digest}
    payload = {"person": full_name, "company_or_role_context": company_hint, "articles": digest}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=4000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:
        logger.warning("tlpr_press: analysis call failed for %r: %s", full_name, e)
        return {"error": "The press coverage analysis could not be completed (%s)."
                         % (str(e)[:160] or type(e).__name__)}
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    candidate = _extract_json_object(raw)
    if candidate is None:
        return {"error": "The press coverage analysis returned an unreadable response."}
    try:
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return {"error": "The press coverage analysis returned malformed JSON."}
    if not isinstance(parsed, dict):
        return {"error": "The press coverage analysis returned an unexpected shape."}
    return _clean_analysis(parsed, valid_ids, digest_by_id)


def build_press(full_name: str, company_hint: str | None = None) -> dict:
    """The whole earned-media read, ready to store and render. Never
    raises: every failure mode degrades to a well-formed dict with a `note`
    explaining what a reader is looking at."""
    result: dict = {
        "person": full_name,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "article_count": 0,
        "source_count": 0,
        "sources": [],
        "timeline": [],
        "earliest": None,
        "latest": None,
        "top_articles": [],
        "analysis": None,
        "errors": {},
        "note": "",
    }
    try:
        articles, errors = collect_coverage(full_name, company_hint)
    except Exception as e:
        logger.warning("tlpr_press: coverage collection failed for %r: %s", full_name, e)
        result["note"] = "Press coverage search could not be completed for this person."
        return result

    result["errors"] = errors
    result.update(aggregate(articles))
    if not articles:
        result["note"] = ("No recent press coverage mentioning this person was found. "
                          "That is a finding, not an error: this person has no measurable "
                          "earned-media footprint in the last %d days." % MAX_NEWS_AGE_DAYS)
        return result
    result["analysis"] = analyze(full_name, articles, company_hint)
    return result
