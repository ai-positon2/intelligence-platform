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
    "You are a B2B LinkedIn competitive-strategy analyst. You are given the full "
    "JSON output of a five-agent LinkedIn analysis workflow for one company -- "
    "namespaced fields such as strategyagent.*, contentcreativeagent.*, "
    "messagingagent.*, creativeinsightagent.*, competitiveagent.*, "
    "getcompanyprofile.*, and getcompanypost.*. Synthesize ONE point of view "
    "across all of it. Do not just restate a single section.\n\n"
    "HARD RULE: never state a fact that is not traceable to a field in the JSON "
    "you were given. If a namespace is missing, or its arrays/objects are empty, "
    "say so plainly (for example: \"no organic posts were available to "
    "analyze\") instead of guessing or inventing plausible-sounding detail. "
    "Ground every conclusion in the specific field it comes from. It is always "
    "better to say less than to invent.\n\n"
    "Return ONLY a JSON object with these keys, nothing else:\n"
    '  "headline": one sentence, the single most useful thing to know about '
    "this company's LinkedIn presence (140 characters or fewer)\n"
    '  "synthesis": 2 to 4 short paragraphs giving the full point of view, '
    "separated by blank lines\n"
    '  "topActions": an array of up to 5 short (140 characters or fewer) '
    "prioritized action strings, each grounded in something in the source data\n"
    '  "coverage": one short sentence naming which agent sections had real '
    "data and which came back empty, so a reader can gauge how much of this "
    "is founded on real signal\n\n"
    "No markdown, no code fences, no commentary outside the JSON object. Never "
    "use an em dash; use commas or periods instead."
)


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


def enrich_run(output: dict, company_name: str, run_type: str) -> dict | None:
    """One Claude call synthesizing a completed run's full vendor output.

    Returns {"headline", "synthesis", "topActions", "coverage"}, or None when
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
    payload = {"company_name": company_name, "run_type": run_type, "agent_output": source}
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=1200,
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

    top_actions = []
    if isinstance(parsed.get("topActions"), list):
        for a in parsed["topActions"]:
            if isinstance(a, (str, int, float)) and str(a).strip():
                top_actions.append(str(a).strip())

    coverage = parsed.get("coverage")
    coverage = coverage.strip() if isinstance(coverage, str) and coverage.strip() else None

    result = {"headline": headline.strip(), "synthesis": synthesis.strip(), "topActions": top_actions[:5]}
    if coverage:
        result["coverage"] = coverage
    return result
