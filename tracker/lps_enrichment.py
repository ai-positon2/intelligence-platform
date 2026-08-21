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
    "No markdown, no code fences, no commentary outside the JSON object. Output "
    "compact, single-line JSON with no indentation and no extra whitespace "
    "between keys -- every token spent on formatting is a token not spent on "
    "the analysis. Never use an em dash; use commas or periods instead."
)

# Every list-shaped key in the schema above, with the cap applied to each.
_LIST_FIELDS = {
    "topActions": 5,
    "strengths": 4,
    "risks": 4,
    "contentAngles": 5,
}

# A completed run's output easily has enough real content (100 posts' worth
# of derived metrics, five agents' sections) that a compact-JSON reply for
# this schema can still run to several thousand tokens. The original 2000
# cap was hit routinely -- even a tiny synthetic probe payload truncated mid
# response -- which produces invalid JSON that silently became None. If the
# first attempt still gets cut off, one retry with double the budget is
# cheap insurance against a repeat: the JSON-invalidity failure mode was
# deterministic, not flaky, so a bigger budget fixes it for good rather than
# masking it.
_MAX_TOKENS = 4096
_RETRY_MAX_TOKENS = 8192

ERR_EMPTY_SOURCE = "empty_source"
ERR_API = "api_error"
ERR_TRUNCATED = "truncated"
ERR_UNPARSABLE = "unparsable"
ERR_SHAPE = "shape"


def _anthropic():
    """A configured Anthropic client, or None when this environment has no
    key. Mirrors app.py's _cpi_anthropic -- every caller degrades to "no
    enrichment" exactly like Contact Finder degrades to "no second opinion".
    A generous timeout: the retry attempt below asks for up to 8192 tokens,
    which can take well over the default for a slow generation."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=120.0, max_retries=1)


def _err(kind: str, detail: str = "", status: int | None = None) -> dict:
    return {"kind": kind, "status": status, "detail": (detail or "")[:500]}


def describe_error(err: dict | None) -> str:
    """Human-facing text for an enrich_run_result() error. Never includes the
    raw exception text -- callers that want that detail for admins should
    read err['detail'] directly, gated the same way arena_client's callers
    gate vendor response bodies."""
    if not isinstance(err, dict):
        return "AI Insights could not be generated."
    kind = err.get("kind")
    status = err.get("status")
    if kind == ERR_EMPTY_SOURCE:
        return "There is no analysis data yet to synthesize a point of view from."
    if kind == ERR_API:
        if status in (401, 403):
            return "The AI Insights service rejected our API key. It needs to be renewed before this will work."
        if status == 429:
            return "The AI Insights service is rate-limiting us right now. Try again in a moment."
        if status:
            return "The AI Insights service returned an error (HTTP %s)." % status
        return "The AI Insights service could not be reached."
    if kind == ERR_TRUNCATED:
        return ("The AI's reply was cut off before it finished, even after retrying with more "
                "room. This is usually a one-off; try again.")
    if kind == ERR_UNPARSABLE:
        return "The AI's reply couldn't be understood. Try again."
    if kind == ERR_SHAPE:
        return "The AI's reply was missing required fields. Try again."
    return "AI Insights could not be generated."


def is_retryable(err: dict | None) -> bool:
    if not isinstance(err, dict):
        return False
    kind = err.get("kind")
    if kind == ERR_API:
        return err.get("status") not in (401, 403)
    return kind in (ERR_TRUNCATED, ERR_UNPARSABLE, ERR_SHAPE)


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


def _call(client, payload: dict, max_tokens: int) -> tuple[str, str | None]:
    resp = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=max_tokens,
        system=_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, default=str)}])
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return raw, getattr(resp, "stop_reason", None)


def enrich_run_result(output: dict, company_name: str, run_type: str) -> tuple[dict | None, dict | None]:
    """One Claude call synthesizing a completed run, returning (result, error).

    Callers pass the AUGMENTED output (tracker/lps_analytics.augment), so the
    prose here is repaired rather than mojibake and the derived.* metrics are
    already computed. `result` is None whenever `error` is set, and vice
    versa; both are None only when ANTHROPIC_API_KEY isn't configured at all
    (the caller is expected to have already checked for that, since it isn't
    a per-run failure worth recording). Never raises.
    """
    client = _anthropic()
    if client is None:
        return None, None
    source = {k: v for k, v in (output or {}).items() if k not in _NON_CONTENT_KEYS}
    if not source:
        return None, _err(ERR_EMPTY_SOURCE)
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

    raw = None
    stop_reason = None
    for max_tokens in (_MAX_TOKENS, _RETRY_MAX_TOKENS):
        try:
            raw, stop_reason = _call(client, payload, max_tokens)
        except Exception as e:
            status = getattr(e, "status_code", None)
            logger.warning("lps_enrichment: call failed for %r: %s", company_name, e)
            return None, _err(ERR_API, "%s: %s" % (type(e).__name__, e), status)
        if stop_reason != "max_tokens":
            break
    if stop_reason == "max_tokens":
        logger.warning("lps_enrichment: reply truncated for %r even at %d tokens",
                        company_name, _RETRY_MAX_TOKENS)
        return None, _err(ERR_TRUNCATED, "Response still hit the token limit after retrying with more room.")

    try:
        parsed = json.loads(raw)
    except Exception as e:
        logger.warning("lps_enrichment: unparsable reply for %r: %s", company_name, e)
        return None, _err(ERR_UNPARSABLE, "%s: %s" % (type(e).__name__, e))
    if not _is_dict(parsed):
        return None, _err(ERR_SHAPE, "Reply was valid JSON but not an object.")

    headline = parsed.get("headline")
    synthesis = parsed.get("synthesis")
    if (not isinstance(headline, str) or not headline.strip()
            or not isinstance(synthesis, str) or not synthesis.strip()):
        return None, _err(ERR_SHAPE, "Reply was missing a usable headline/synthesis.")

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
    return result, None


def enrich_run(output: dict, company_name: str, run_type: str) -> dict | None:
    """Back-compat wrapper for callers that only want the best-effort result
    and don't need the reason it failed (the analysis-time caller in app.py:
    a run still completes and saves without its synthesis, so there is
    nothing actionable to do with the error there)."""
    result, _error = enrich_run_result(output, company_name, run_type)
    return result


_SAMPLE_SOURCE = {
    "strategyagent.summary": "Acme posts about product launches and hiring.",
    "derived.postingCadence": {"postsPerWeek": 3.0, "longestGapDays": 6},
    "derived.engagement": {"average": 40, "rate": 0.4},
}


def probe(company_name: str = "Acme Corp") -> dict:
    """Admin self-test: runs enrich_run_result against a tiny synthetic
    payload -- the exact code path a real run takes -- and reports what
    actually happened instead of collapsing every outcome to None. Mirrors
    arena_client.probe() and app.py's _apollo_selftest. Never raises, so it
    is safe to expose on an admin-only route."""
    import time
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    result: dict[str, Any] = {"configured": bool(key), "key_len": len(key), "model": model}
    if not key:
        result["error"] = "ANTHROPIC_API_KEY is not set on this deployment."
        return result
    t0 = time.time()
    enrichment, err = enrich_run_result(_SAMPLE_SOURCE, company_name, "OWN")
    result["elapsed_ms"] = int((time.time() - t0) * 1000)
    if enrichment:
        result["ok"] = True
        result["headline"] = enrichment["headline"]
    else:
        result["ok"] = False
        result["error_kind"] = (err or {}).get("kind")
        result["error"] = describe_error(err)
        result["detail"] = (err or {}).get("detail")
    return result
