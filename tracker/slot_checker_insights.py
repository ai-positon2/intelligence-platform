"""Claude-powered synthesis layer for the Gentle Dental Slot Checker dashboard.

tracker/slot_checker.py already turns the weekly crawl into totals, breakdowns
by state/service/brand/weekday, and an alerts list -- but those are still
several independent tables sitting side by side, the same problem
tracker/lps_enrichment.py exists to solve for LinkedIn Strategy Researcher
runs. A person skimming this dashboard has to cross-reference the state bars
against the alerts tab against the service mix themselves to find the one
or two things actually worth acting on this week. This module makes ONE
Claude call over the dashboard's own derived numbers and writes a short
briefing that already did that cross-referencing.

Same hard rule as lps_enrichment, and for the same reason: this is read as a
finding about real dental locations and real patients being turned away, so
it must never invent a fact that is not traceable to the JSON it was given.
And it must degrade to None on any failure (no ANTHROPIC_API_KEY, a timeout,
a malformed reply) -- AI Insights is additive, never a reason the dashboard
itself fails to load.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a B2B operations analyst writing a short weekly briefing about "
    "appointment availability across a portfolio of dental locations. "
    "You are given JSON with: totals (aggregate slots/locations/"
    "services across the crawl window), by_state, by_service, by_brand, and "
    "by_weekday breakdowns, an alerts block (locations with no data, fully "
    "booked, or thin availability, plus specific services that are unbookable "
    "at otherwise-healthy locations), top_practices and bottom_practices "
    "(the busiest and least-available locations that do have data), and "
    "freshness (how stale the crawl is). Synthesize ONE point of view across "
    "all of it -- do not just restate a single table.\n\n"
    "Quote the real numbers you were given specifically: a named state or "
    "brand with its slot count, a named location with its total, the count of "
    "locations with no data at all. Concrete numbers and names are what makes "
    "this useful to someone who has not opened the dashboard.\n\n"
    "HARD RULE: never state a fact that is not traceable to a field in the "
    "JSON you were given. If a breakdown is empty, say so plainly instead of "
    "guessing. Never estimate, benchmark against an industry average, or "
    "invent a location, state, or service name not present in the data. It "
    "is always better to say less than to invent.\n\n"
    "Return ONLY a JSON object with these keys, nothing else:\n"
    '  "headline": one sentence, the single most useful thing to know this '
    "week (140 characters or fewer)\n"
    '  "synthesis": 2 to 4 short paragraphs giving the full point of view, '
    "separated by blank lines\n"
    '  "topActions": an array of up to 5 short (140 characters or fewer) '
    "prioritized action strings, each naming a specific location, state, or "
    "service from the data\n"
    '  "risks": an array of up to 4 short strings -- crawl gaps or capacity '
    "exposures visible in this data, each citing the number or name that "
    "shows it. Omit the key entirely rather than inventing one.\n"
    '  "opportunities": an array of up to 4 short strings -- where there is '
    "real spare capacity worth promoting or reallocating attention to, each "
    "grounded in a specific state, brand, or service figure. Omit the key "
    "entirely rather than inventing one.\n"
    '  "coverage": one short sentence naming how much of the portfolio has '
    "real data versus gaps, so a reader can gauge confidence\n\n"
    "No markdown, no code fences, no commentary outside the JSON object. "
    "Output compact, single-line JSON with no indentation and no extra "
    "whitespace between keys. Never use an em dash; use commas or periods "
    "instead."
)

_LIST_FIELDS = {"topActions": 5, "risks": 4, "opportunities": 4}

_MAX_TOKENS = 2048
_RETRY_MAX_TOKENS = 4096

# Re-generating on every page load would spend a Claude call per visitor for
# numbers that only change when the snapshot is re-imported (weekly). Cache
# the result until either the TTL lapses or the snapshot's generated_at
# stamp changes, whichever comes first -- so a re-import invalidates it
# immediately instead of waiting out the clock.
CACHE_TTL = 3600
_CACHE: dict = {"data": None, "error": None, "ts": 0.0, "generated_at": None}

ERR_EMPTY_SOURCE = "empty_source"
ERR_API = "api_error"
ERR_TRUNCATED = "truncated"
ERR_UNPARSABLE = "unparsable"
ERR_SHAPE = "shape"


def _anthropic():
    """A configured Anthropic client, or None when this environment has no
    key. Mirrors tracker/lps_enrichment.py's _anthropic()."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=60.0, max_retries=1)


def _err(kind: str, detail: str = "", status: int | None = None) -> dict:
    return {"kind": kind, "status": status, "detail": (detail or "")[:500]}


def describe_error(err: dict | None) -> str:
    """Human-facing text for a generate_insights() error. Never includes the
    raw exception text."""
    if not isinstance(err, dict):
        return "AI Insights could not be generated."
    kind = err.get("kind")
    status = err.get("status")
    if kind == ERR_EMPTY_SOURCE:
        return "There is no availability data yet to summarize."
    if kind == ERR_API:
        if status in (401, 403):
            return "The AI Insights service rejected our API key. It needs to be renewed before this will work."
        if status == 429:
            return "The AI Insights service is rate-limiting us right now. Try again in a moment."
        if status:
            return "The AI Insights service returned an error (HTTP %s)." % status
        return "The AI Insights service could not be reached."
    if kind == ERR_TRUNCATED:
        return "The AI's reply was cut off before it finished, even after retrying with more room. Try again."
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


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, (str, int, float)) and str(item).strip():
            out.append(str(item).strip())
    return out[:limit]


def compact_for_llm(dashboard: dict) -> dict:
    """The dashboard payload trimmed to what a briefing actually needs.

    Drops the per-date count arrays (14 numbers per practice/service that the
    model would otherwise have to read past for no benefit here -- the
    aggregates already carry the totals) and caps the alerts lists to
    concrete examples rather than sending all 82 practices twice.
    """
    practices = dashboard.get("practices") or []
    with_data = [p for p in practices if p.get("status") != "no-data"]
    top = sorted(with_data, key=lambda p: -(p.get("total") or 0))[:5]
    bottom = sorted(
        (p for p in with_data if (p.get("total") or 0) > 0),
        key=lambda p: (p.get("total") or 0),
    )[:5]

    def brief(p: dict) -> dict:
        return {"name": p.get("name"), "state": p.get("state"), "brand": p.get("brand"),
                "total": p.get("total"), "status": p.get("status")}

    alerts = dashboard.get("alerts") or {}

    def alert_brief(items: list, limit: int = 8) -> dict:
        return {"count": len(items), "examples": [
            {"name": x.get("name"), "state": x.get("state")} for x in items[:limit]]}

    by_service = [{"name": s.get("name"), "slots": s.get("slots"),
                   "practices": s.get("practices"), "zero": s.get("zero")}
                  for s in (dashboard.get("by_service") or [])]

    return {
        "totals": dashboard.get("totals") or {},
        "by_state": dashboard.get("by_state") or [],
        "by_service": by_service,
        "by_brand": dashboard.get("by_brand") or [],
        "by_weekday": dashboard.get("by_weekday") or [],
        "alerts": {
            "no_data": alert_brief(alerts.get("no_data") or []),
            "zero": alert_brief(alerts.get("zero") or []),
            "thin": alert_brief(alerts.get("thin") or []),
            "unbookable_services": {
                "count": len(alerts.get("unbookable_services") or []),
                "examples": [{"name": x.get("name"), "service": x.get("service")}
                             for x in (alerts.get("unbookable_services") or [])[:8]],
            },
        },
        "top_practices": [brief(p) for p in top],
        "bottom_practices": [brief(p) for p in bottom],
        "freshness": dashboard.get("freshness") or {},
    }


def _call(client, payload: dict, max_tokens: int) -> tuple[str, str | None]:
    resp = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=max_tokens,
        system=_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, default=str)}])
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return raw, getattr(resp, "stop_reason", None)


def generate_insights_result(dashboard: dict) -> tuple[dict | None, dict | None]:
    """One Claude call synthesizing the current dashboard, returning
    (result, error). `result` is None whenever `error` is set, and vice
    versa; both are None only when ANTHROPIC_API_KEY isn't configured (the
    caller is expected to check for that separately). Never raises.
    """
    client = _anthropic()
    if client is None:
        return None, None
    totals = (dashboard or {}).get("totals") or {}
    if not totals or not totals.get("practices"):
        return None, _err(ERR_EMPTY_SOURCE)
    payload = compact_for_llm(dashboard)

    raw = None
    stop_reason = None
    for max_tokens in (_MAX_TOKENS, _RETRY_MAX_TOKENS):
        try:
            raw, stop_reason = _call(client, payload, max_tokens)
        except Exception as e:
            status = getattr(e, "status_code", None)
            logger.warning("slot_checker_insights: call failed: %s", e)
            return None, _err(ERR_API, "%s: %s" % (type(e).__name__, e), status)
        if stop_reason != "max_tokens":
            break
    if stop_reason == "max_tokens":
        logger.warning("slot_checker_insights: reply truncated even at %d tokens", _RETRY_MAX_TOKENS)
        return None, _err(ERR_TRUNCATED, "Response still hit the token limit after retrying with more room.")

    try:
        parsed = json.loads(raw)
    except Exception as e:
        logger.warning("slot_checker_insights: unparsable reply: %s", e)
        return None, _err(ERR_UNPARSABLE, "%s: %s" % (type(e).__name__, e))
    if not isinstance(parsed, dict):
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
        "topActions": _string_list(parsed.get("topActions"), _LIST_FIELDS["topActions"]),
    }
    for field in ("risks", "opportunities"):
        items = _string_list(parsed.get(field), _LIST_FIELDS[field])
        if items:
            result[field] = items
    if coverage:
        result["coverage"] = coverage
    return result, None


def fetch(dashboard: dict, force: bool = False) -> tuple[dict | None, dict | None]:
    """TTL + snapshot-stamp cached wrapper the Flask route calls directly.

    A cache hit returns instantly instead of spending a Claude call per page
    view; a snapshot re-import (a new generated_at) invalidates it even
    inside the TTL window, so the briefing never quietly describes last
    week's numbers.
    """
    now = time.time()
    generated_at = (dashboard or {}).get("generated_at")
    fresh_cache = (
        _CACHE["ts"] and (now - _CACHE["ts"]) < CACHE_TTL
        and _CACHE["generated_at"] == generated_at
    )
    if not force and fresh_cache:
        return _CACHE["data"], _CACHE["error"]
    result, error = generate_insights_result(dashboard)
    _CACHE["data"] = result
    _CACHE["error"] = error
    _CACHE["ts"] = now
    _CACHE["generated_at"] = generated_at
    return result, error


def reset_cache() -> None:
    """Drop the cache. Tests need this; nothing in the app calls it."""
    _CACHE["data"] = None
    _CACHE["error"] = None
    _CACHE["ts"] = 0.0
    _CACHE["generated_at"] = None


_SAMPLE_DASHBOARD = {
    "generated_at": "probe",
    "totals": {"slots": 7070, "practices": 82, "practices_with_data": 81, "states": 7},
    "by_state": [{"state": "MA", "slots": 4800, "practices": 48, "avg": 100.0}],
    "by_service": [{"name": "New Patient Exam", "slots": 3000, "practices": 70, "zero": 2}],
    "by_brand": [{"brand": "Gentle Dental", "slots": 4600, "practices": 46}],
    "by_weekday": [{"day": "Sun", "slots": 400, "avg": 200.0}],
    "alerts": {"no_data": [{"name": "Torrington", "state": "CT"}],
               "zero": [{"name": "Exeter", "state": "NH"}], "thin": [], "unbookable_services": []},
    "top_practices": [{"name": "Boston Downtown", "state": "MA", "total": 210}],
    "bottom_practices": [{"name": "Exeter", "state": "NH", "total": 1}],
    "freshness": {"oldest": "2026-08-12", "newest": "2026-08-13"},
}


def probe() -> dict:
    """Admin self-test: runs generate_insights_result against a tiny
    synthetic dashboard -- the exact code path a real page load takes -- and
    reports what actually happened instead of collapsing every outcome to
    None. Mirrors tracker/lps_enrichment.py's probe(). Never raises."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    result: dict[str, Any] = {"configured": bool(key), "key_len": len(key), "model": model}
    if not key:
        result["error"] = "ANTHROPIC_API_KEY is not set on this deployment."
        return result
    t0 = time.time()
    insights, err = generate_insights_result(_SAMPLE_DASHBOARD)
    result["elapsed_ms"] = int((time.time() - t0) * 1000)
    if insights:
        result["ok"] = True
        result["headline"] = insights["headline"]
    else:
        result["ok"] = False
        result["error_kind"] = (err or {}).get("kind")
        result["error"] = describe_error(err)
        result["detail"] = (err or {}).get("detail")
    return result
