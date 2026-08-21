"""Claude-powered synthesis layer for LinkedIn Strategy Researcher runs.

The vendor's five agents each report only what they individually found -- a
company with little organic LinkedIn activity (a real "Boat" run this feature
was built against had no posts, no engagement, no creative, no competitor
campaigns/launches) ends up as several honest "no data" sections sitting side
by side, with nothing pulling a point of view across them. This module makes
ONE extra call per completed run, after the vendor's own workflow returns,
reading everything the agents actually produced and writing a single
synthesis that spans namespaces instead of being siloed per tab.

The one rule that makes this safe to bolt on: it must never invent a fact that
isn't traceable to the JSON it was given (see _SYSTEM below) -- a company with
nothing real to report on should get an honest "no organic content was found"
synthesis, not a plausible-sounding fabrication. And it must degrade to None
on any failure (no ANTHROPIC_API_KEY, a timeout, a malformed reply), exactly
like tracker/arena_client.py degrades on a missing ARENA_API_KEY -- enrichment
is additive, never a reason a run can fail or block.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Internal bookkeeping the vendor response carries that has nothing to do with
# what the company actually is -- sending it to Claude would waste tokens and
# could read as source material to synthesize from.
_NON_CONTENT_KEYS = {"_sseDebug"}

_SYSTEM = (
    "You are a B2B LinkedIn competitive-strategy analyst. You are given the "
    "JSON output of a five-agent LinkedIn analysis workflow for one company -- "
    "namespaced fields such as strategyagent.*, contentcreativeagent.*, "
    "messagingagent.*, creativeinsightagent.*, competitiveagent.*, and "
    "getcompanyprofile.* -- plus a derived.* block of metrics computed "
    "directly from that company's real post feed (posting cadence, engagement "
    "averages and rate, which post format actually earns engagement, content "
    "signals, voice mix) and a postFeedSummary with its best posts. "
    "Synthesize ONE point of view across all of it. Do not just restate a "
    "single section.\n\n"
    "The derived.* numbers are arithmetic over real posts, so quote them "
    "specifically: a named format with its multiple, the posts-per-week "
    "figure, the engagement rate. Concrete numbers are what makes this useful. "
    "Prefer the finding a reader could act on over a description of what the "
    "company does.\n\n"
    "HARD RULE: never state a fact that is not traceable to a field in the JSON "
    "you were given. If a namespace is missing, or its arrays/objects are empty, "
    "say so plainly (for example: \"no organic posts were available to "
    "analyze\") instead of guessing or inventing plausible-sounding detail. "
    "Ground every conclusion in the specific field it comes from. Never "
    "estimate, benchmark against an industry average, or compare to a "
    "competitor that is not named in the data. It is always better to say less "
    "than to invent.\n\n"
    "Return ONLY a JSON object with these keys, nothing else:\n"
    '  "headline": one sentence, the single most useful thing to know about '
    "this company's LinkedIn presence (140 characters or fewer)\n"
    '  "synthesis": 2 to 4 short paragraphs giving the full point of view, '
    "separated by blank lines\n"
    '  "topActions": an array of up to 5 short (140 characters or fewer) '
    "prioritized action strings, each grounded in something in the source data\n"
    '  "strengths": an array of up to 4 short strings, what is demonstrably '
    "working, each citing the field or number that shows it\n"
    '  "risks": an array of up to 4 short strings, the gaps or exposures '
    "visible in this data. Omit the key entirely rather than inventing one.\n"
    '  "contentAngles": an array of up to 5 short, specific post or campaign '
    "angles this company could run next, each derived from a theme, hook, "
    "persona or gap present in the data\n"
    '  "coverage": one short sentence naming which sections had real data and '
    "which came back empty, so a reader can gauge how much of this is founded "
    "on real signal\n\n"
    "No markdown, no code fences, no commentary outside the JSON object. Never "
    "use an em dash; use commas or periods instead."
)

# Every list-shaped key in the schema above, with the cap applied to each.
_LIST_FIELDS = {
    "topActions": 5,
    "strengths": 4,
    "risks": 4,
    "contentAngles": 5,
}


def _anthropic():
    """A configured Anthropic client, or None when this environment has no
    key. Mirrors app.py's _cpi_anthropic -- every caller degrades to "no
    enrichment" exactly like Contact Finder degrades to "no second opinion"."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=60.0, max_retries=1)


def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


def _string_list(value: Any, limit: int) -> list[str]:
    """A model-returned array coerced to clean strings, capped. A non-list, or
    a list of objects, yields [] rather than str()-ing dicts into the report."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, (str, int, float)) and str(item).strip():
            out.append(str(item).strip())
    return out[:limit]


def enrich_run(output: dict, company_name: str, run_type: str) -> dict | None:
    """One Claude call synthesizing a completed run.

    Callers pass the AUGMENTED output (tracker/lps_analytics.augment), so the
    prose here is repaired rather than mojibake and the derived.* metrics are
    already computed. Returns the synthesis dict, or None when
    ANTHROPIC_API_KEY isn't configured, the source output is empty, or the
    call/parse fails -- never raises. app.py's background analysis job treats
    this as strictly best-effort: a run still completes and saves without it.
    """
    client = _anthropic()
    if client is None:
        return None
    source = {k: v for k, v in (output or {}).items() if k not in _NON_CONTENT_KEYS}
    if not source:
        return None
    # Send the compacted view, not the whole run. The raw output carries up to
    # 100 full post bodies with attachment URLs and ~72 viewer-permission
    # booleans (roughly 320KB on the real Google run, most of it noise the
    # model has to read past); compact_for_llm swaps that for the computed
    # metrics plus the top posts, which is both far cheaper and strictly more
    # useful signal.
    try:
        from tracker import lps_analytics
        source = lps_analytics.compact_for_llm(source)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("lps_enrichment: compaction failed, sending full output: %s", e)
    payload = {"company_name": company_name, "run_type": run_type, "agent_output": source}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=2000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}])
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        parsed = json.loads(raw)
    except Exception as e:
        logger.warning("lps_enrichment: synthesis failed for %r: %s", company_name, e)
        return None
    if not _is_dict(parsed):
        return None

    headline = parsed.get("headline")
    synthesis = parsed.get("synthesis")
    if (not isinstance(headline, str) or not headline.strip()
            or not isinstance(synthesis, str) or not synthesis.strip()):
        return None

    coverage = parsed.get("coverage")
    coverage = coverage.strip() if isinstance(coverage, str) and coverage.strip() else None

    result: dict[str, Any] = {
        "headline": headline.strip(),
        "synthesis": synthesis.strip(),
        # topActions stays present-but-possibly-empty for backward
        # compatibility with the existing contract; the newer list fields are
        # omitted entirely when the model returns nothing usable, so the
        # report can skip their sections rather than render empty headings.
        "topActions": _string_list(parsed.get("topActions"), _LIST_FIELDS["topActions"]),
    }
    for field in ("strengths", "risks", "contentAngles"):
        items = _string_list(parsed.get(field), _LIST_FIELDS[field])
        if items:
            result[field] = items
    if coverage:
        result["coverage"] = coverage
    return result
