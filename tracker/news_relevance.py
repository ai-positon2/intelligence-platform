"""Relevance filtering for company news.

Google News returns *everything* that mentions a company — share-price chatter,
listicles, incidental name-drops — which floods the tracker with low-value
"News Mention" signals. This module keeps only news that is genuinely about the
company AND about a real business event, so only relevant signals get stored.

Two layers, applied in order:

1. Heuristic gate (free, always on)
   Scores each article on company relevance + business-event keywords, and
   penalises obvious noise (market chatter, SEO listicles, how-to/review junk).
   Articles below ``min_score`` are dropped.

2. AI gate (optional, batched — one OpenAI call per company)
   When an OpenAI key is supplied, the heuristic survivors are sent in a single
   call and Kairo confirms which ones actually matter for B2B buying intent.
   Fail-open: if anything goes wrong, the heuristic survivors are kept.

The public entry point is :func:`filter_relevant_articles`.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# ── Business events worth storing ────────────────────────────────────────────
RELEVANT_KEYWORDS = [
    # funding / investment
    "funding", "raise", "raises", "raised", "series a", "series b", "series c",
    "series d", "seed round", "venture", "investment", "investor", "backed",
    "capital", "round of", "valuation",
    # M&A / corporate structure
    "acquir", "merger", "merges", "buyout", "takeover", "m&a", "acquisition",
    "majority stake", "to buy", "acquires",
    # leadership moves
    "appoint", "names ", "hires", "hired", "joins", "new ceo", "new cfo",
    "new cto", "new cmo", "new coo", "steps down", "resign", "departs",
    "promoted", "names new", "chief executive", "chief financial",
    "chief marketing", "chief technology", "leadership", "board of directors",
    # growth / product
    "expand", "expansion", "new office", "opens", "launch", "unveil",
    "rollout", "new product", "partnership", "partners with", "collaborat",
    "integration", "go-to-market", "enters", "wins contract", "secures",
    # restructuring
    "layoff", "restructur", "cuts jobs", "downsiz", "closure", "shuts down",
    # public-market events (not price chatter)
    "ipo", "goes public", "files for", "s-1", "spac", "debt financing",
    "public offering",
    # brand / web
    "rebrand", "relaunch", "new website", "redesign", "new brand", "awarded",
    "recognized as",
]

# ── Clear noise: incidental mentions, market chatter, SEO junk ───────────────
NOISE_PATTERNS = [
    r"\bshare price\b", r"\bstock (?:price|forecast|to buy|rises|falls|jumps|drops|surges|tumbles)\b",
    r"\bprice target\b", r"\b(?:buy|sell|hold) rating\b", r"\banalyst(?:s)? (?:rating|note|say|expect)\b",
    r"\bdividend\b", r"\bmarket cap\b", r"\bearnings per share\b", r"\b52[- ]week\b",
    r"\b%\s*(?:upside|downside|gain|loss|return)\b", r"\bshould you (?:buy|sell)\b",
    r"\bstocks? to (?:buy|watch|consider)\b", r"\b(?:best|top)\s+\d+\b",
    r"\b\d+\s+(?:best|top|things|reasons|ways|stocks)\b", r"\bhow to\b",
    r"\bbeginner'?s guide\b", r"\bhoroscope\b", r"\brecipe\b",
    r"\bbox office\b", r"\bvs\.?\s+\w+\s+(?:which|comparison)\b",
]

_SUFFIXES = ("inc", "llc", "corp", "corporation", "ltd", "limited", "co",
             "company", "group", "holdings", "plc", "the")

_RELEVANT_RE = [re.compile(re.escape(k)) for k in RELEVANT_KEYWORDS]
_NOISE_RE = [re.compile(p) for p in NOISE_PATTERNS]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9& ]", " ", (s or "").lower())


def _company_tokens(name: str) -> list[str]:
    n = _norm(name)
    words = [w for w in n.split() if w not in _SUFFIXES]
    long = [w for w in words if len(w) >= 4]
    return long or words or [n.strip()]


def score_article(company_name: str, article: dict) -> tuple[int, bool]:
    """Return (score, name_present). Higher = more relevant."""
    title = _norm(article.get("title", ""))
    summary = _norm(article.get("summary", ""))
    text = title + " " + summary

    toks = _company_tokens(company_name)
    name_present = any(t in text for t in toks) if toks else True
    name_in_title = any(t in title for t in toks) if toks else False

    score = 0
    if name_in_title:
        score += 2
    if any(r.search(title) for r in _RELEVANT_RE):
        score += 3
    elif any(r.search(summary) for r in _RELEVANT_RE):
        score += 1
    noise_hits = sum(1 for r in _NOISE_RE if r.search(text))
    score -= min(noise_hits * 4, 8)
    return score, name_present


def _ai_keep_indices(company_name: str, candidates: list[dict], ai_key: str,
                     model: str = "gpt-4o-mini") -> list[int] | None:
    """One batched OpenAI call. Returns indices to keep, or None on failure."""
    try:
        from openai import OpenAI
    except ImportError:
        return None
    listing = "\n".join(
        "%d. %s" % (i, (a.get("title", "") or "")[:140]) for i, a in enumerate(candidates)
    )
    system = (
        "You are Kairo, a B2B sales-intelligence filter for Position2 (a digital "
        "marketing agency). From a list of news headlines about a company, return "
        "ONLY the ones that signal a real business event a sales team would act on "
        "— funding, M&A, leadership change, expansion, product launch, partnership, "
        "restructuring, IPO, rebrand. Drop share-price/market chatter, listicles, "
        "how-to/review content, and incidental mentions. "
        'Return ONLY JSON: {"keep":[<indices>]}'
    )
    user = "Company: %s\nHeadlines:\n%s" % (company_name, listing)
    try:
        oai = OpenAI(api_key=ai_key)
        resp = oai.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_completion_tokens=200,
        )
        raw = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        keep = json.loads(m.group(0)).get("keep", [])
        return [int(i) for i in keep if isinstance(i, (int, float))]
    except Exception as exc:  # fail-open
        logger.warning("[NEWS] AI relevance filter failed for %s: %s", company_name, exc)
        return None


def filter_relevant_articles(company_name: str, articles: list[dict],
                             ai_key: str = "", model: str = "gpt-4o-mini",
                             min_score: int = 2) -> list[dict]:
    """Keep only business-relevant articles, best-first.

    Heuristic gate always runs; the AI gate runs only when ``ai_key`` is set and
    there are survivors. Order is preserved by descending relevance score so the
    single stored News Mention is the most significant one.
    """
    if not articles:
        return []

    scored = []
    for a in articles:
        sc, name_present = score_article(company_name, a)
        if name_present and sc >= min_score:
            scored.append((sc, a))
    scored.sort(key=lambda x: x[0], reverse=True)
    survivors = [a for _, a in scored]

    if not survivors:
        return []

    if ai_key:
        keep = _ai_keep_indices(company_name, survivors, ai_key, model)
        if keep is not None:
            survivors = [survivors[i] for i in keep if 0 <= i < len(survivors)] or survivors

    return survivors
