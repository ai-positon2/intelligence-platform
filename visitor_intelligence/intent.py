"""
Behavioural intent scoring from a visitor's on-site journey.

Turns a list of page paths/titles + engagement into a 0-100 account intent score
and a GTM-legible stage. This is the first-party half of the intent picture; it
combines with firmographic fit (enrich.py) and, where available, Apollo's own
website-visitor intent signal to rank which identified accounts a rep should act
on first.
"""

from __future__ import annotations

from typing import List, Tuple

_HIGH = ("pricing", "demo", "contact", "trial", "buy", "checkout", "book",
        "quote", "roi", "compare", "get-started", "request", "talk-to")
_MID = ("product", "features", "solution", "solutions", "integrations", "docs",
        "case-study", "case_study", "customers", "platform", "use-case",
        "how-it-works", "services")


def score_intent(pages: List[str], pageviews: int = 0, sessions: int = 1,
                engaged_seconds: int = 0, third_party_intent: float = 0.0
                ) -> Tuple[float, str, List[str]]:
    """Return (score 0-100, stage, reasons)."""
    reasons: List[str] = []
    pages = pages or []
    pageviews = pageviews or len(pages)
    score = 0.0

    high = sum(1 for p in pages if any(k in (p or "").lower() for k in _HIGH))
    mid = sum(1 for p in pages if any(k in (p or "").lower() for k in _MID))
    if high:
        add = min(40, high * 20); score += add
        reasons.append("high-intent pages x%d (+%d)" % (high, add))
    if mid:
        add = min(20, mid * 7); score += add
        reasons.append("mid-intent pages x%d (+%d)" % (mid, add))

    depth = min(15, max(0, pageviews - 1) * 2)
    if depth:
        score += depth; reasons.append("depth %d pages (+%d)" % (pageviews, depth))

    if sessions >= 2:
        add = min(15, sessions * 5); score += add
        reasons.append("return visitor x%d (+%d)" % (sessions, add))

    if engaged_seconds >= 120:
        add = min(10, engaged_seconds // 120 * 5); score += add
        reasons.append("engaged %ds (+%d)" % (engaged_seconds, add))

    if third_party_intent:
        add = min(20, third_party_intent * 20); score += add
        reasons.append("third-party intent (+%.0f)" % add)

    score = min(100.0, round(score, 1))
    if score >= 70:
        stage = "decision"
    elif score >= 40:
        stage = "consideration"
    elif score >= 15:
        stage = "interest"
    else:
        stage = "awareness"
    return score, stage, reasons
