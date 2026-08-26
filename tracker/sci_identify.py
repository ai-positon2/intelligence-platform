"""Step 1 (IDENTIFY) for Social Creative Intelligence Analyst: resolve a
company name/URL to its actual handle on each of the 6 platforms, using
Claude's server-side web_search tool. Refusal to guess is enforced by a
confidence threshold on the model's own output, not by prompt wording alone
-- a 'low'/'none' confidence result is never treated as a usable handle by
callers (see sci_pipeline.py, which maps it straight to
sci_platform_runs.status='handle_not_found' without attempting a scrape).

In-house capability, not a swappable vendor -- reads ANTHROPIC_API_KEY
itself, matching tracker/lps_enrichment.py's degrade-to-None convention
rather than tracker/apollo_client.py's explicit-api-key-parameter one (there
is no alternate provider to swap this for).
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

PLATFORMS = ("instagram", "linkedin", "x", "tiktok", "youtube", "facebook")

# Anthropic's web_search tool is versioned by date and older versions get
# sunset -- newest first, so a fresh process tries the current one before
# falling back. _WEB_SEARCH_TOOL caches whichever version actually worked so
# every call after the first skips straight to it, but a cached version is
# only ever a fast-path hint: if it later stops working too (the next
# sunset), the loop below still falls through the rest of the list rather
# than failing every run the way a single hardcoded version did.
_WEB_SEARCH_TOOL_VERSIONS = ("web_search_20260318", "web_search_20260209", "web_search_20250305")
_WEB_SEARCH_TOOL = None

_UNSUPPORTED_TOOL_MARKERS = ("unsupported", "unknown tool", "invalid tool", "not supported",
                             "does not support", "unrecognized", "no such tool", "deprecated",
                             "invalid_value", "invalid_request_error")


def _tool_version_unsupported(err) -> bool:
    """"This dated web_search tool version is rejected outright", as distinct
    from a bad minute -- a rate limit, timeout, or 5xx must never be treated
    as reason to burn through the rest of the version list, since none of
    them would fix a transient problem either."""
    status = getattr(err, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return False
    text = str(err or "").lower()
    return any(m in text for m in _UNSUPPORTED_TOOL_MARKERS)


def _describe_exception(e) -> str:
    status = getattr(e, "status_code", None)
    msg = str(e) or type(e).__name__
    if len(msg) > 220:
        msg = msg[:220] + "..."
    return ("HTTP %s: %s" % (status, msg)) if status else msg


_SYSTEM = (
    "You identify a company's own official, organic (non-paid) presence on "
    "six social platforms: Instagram, LinkedIn, X (Twitter), TikTok, YouTube, "
    "and Facebook. Use web search to find and verify each one -- do not guess "
    "from pattern-matching a likely handle. Verify a candidate account "
    "actually belongs to this company (its bio, pinned post, or profile links "
    "back to the company's real website; it is not a fan page, a regional "
    "reseller, or a different company with a similar name) before reporting "
    "it as 'high' confidence. If you cannot find or verify an account on a "
    "platform, report confidence 'none' for that platform rather than "
    "inventing a plausible-looking handle -- a wrong handle is worse than no "
    "handle, since everything downstream of this step scrapes whatever you "
    "return here.\n\n"
    "After searching, respond with ONLY a JSON object (no prose before or "
    "after), with exactly these six keys: instagram, linkedin, x, tiktok, "
    "youtube, facebook. Each value is an object: "
    '{"handle": str|null, "profile_url": str|null, '
    '"confidence": "high"|"medium"|"low"|"none", "reasoning": str}. '
    '"handle" and "profile_url" MUST be null when confidence is "none".'
)


def _anthropic():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=90.0, max_retries=1)


def _empty_result(reasoning: str) -> dict[str, dict]:
    return {p: {"handle": None, "profile_url": None, "confidence": "none", "reasoning": reasoning}
           for p in PLATFORMS}


def _parse(raw: str) -> dict[str, dict] | None:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    out = {}
    for p in PLATFORMS:
        entry = parsed.get(p) if isinstance(parsed.get(p), dict) else {}
        confidence = entry.get("confidence") if entry.get("confidence") in ("high", "medium", "low", "none") else "none"
        # Only 'high'/'medium' ever carry a handle through -- 'low' and
        # 'none' both refuse to guess, regardless of what the model put in
        # the handle/profile_url fields of its own reply. This must not
        # trust the model to have already enforced that itself.
        usable = confidence in ("high", "medium")
        out[p] = {
            "handle": entry.get("handle") if usable else None,
            "profile_url": entry.get("profile_url") if usable else None,
            "confidence": confidence,
            "reasoning": str(entry.get("reasoning") or ""),
        }
    return out


def identify_handles(company_name: str, company_url: str | None = None) -> dict[str, dict]:
    """One Claude + web-search call resolving all 6 platform handles at once.
    Never raises -- an unconfigured key, a timeout, or an unparsable reply
    all degrade to every platform reporting confidence='none', so a caller
    that always maps 'none' to handle_not_found behaves correctly either
    way, without a separate error path to check."""
    client = _anthropic()
    if client is None:
        return _empty_result("ANTHROPIC_API_KEY is not configured on this deployment.")

    company_name = (company_name or "").strip()
    if not company_name:
        return _empty_result("No company name was provided.")

    user_text = f"Company: {company_name}"
    if company_url:
        user_text += f"\nKnown website: {company_url}"

    global _WEB_SEARCH_TOOL
    versions = _WEB_SEARCH_TOOL_VERSIONS
    if _WEB_SEARCH_TOOL in versions:
        versions = (_WEB_SEARCH_TOOL,) + tuple(v for v in versions if v != _WEB_SEARCH_TOOL)

    resp = None
    last_err = None
    for version in versions:
        try:
            resp = client.messages.create(
                model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
                max_tokens=4000,
                system=_SYSTEM,
                tools=[{"type": version, "name": "web_search", "max_uses": 15}],
                messages=[{"role": "user", "content": user_text}],
            )
        except Exception as e:
            last_err = e
            logger.warning("sci_identify: web_search tool '%s' failed for %r: %s",
                            version, company_name, e)
            if _tool_version_unsupported(e):
                resp = None
                continue
            resp = None
            break
        else:
            if _WEB_SEARCH_TOOL != version:
                _WEB_SEARCH_TOOL = version
                logger.info("sci_identify: web_search tool version '%s' confirmed working", version)
            break

    if resp is None:
        detail = _describe_exception(last_err) if last_err is not None else "no usable web_search tool version"
        return _empty_result("The identification step failed unexpectedly (%s)." % detail)

    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = text_blocks[-1] if text_blocks else ""
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("sci_identify: unparsable response for %r", company_name)
        return _empty_result("The identification step returned an unreadable response.")
    return parsed


def probe(company_name: str = "Nike") -> dict:
    """Admin self-test: runs identify_handles() against a real, easily
    verifiable company -- the exact code path a real run takes, including
    the web_search tool version fallback above -- and reports what actually
    happened instead of a blank page until the next real user run fails.
    Mirrors arena_client.probe()/sci_company_search.probe(). Never raises."""
    import time
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    result: dict = {"configured": bool(key), "key_len": len(key)}
    if not key:
        result["error"] = "ANTHROPIC_API_KEY is not set on this deployment."
        return result
    t0 = time.time()
    handles = identify_handles(company_name)
    result["elapsed_ms"] = int((time.time() - t0) * 1000)
    result["web_search_tool"] = _WEB_SEARCH_TOOL
    found = {p: e for p, e in handles.items() if e.get("confidence") in ("high", "medium")}
    result["ok"] = bool(found)
    result["found"] = {p: e["handle"] for p, e in found.items()}
    if not found:
        # Every platform came back 'none' -- surface the first reasoning
        # string, since a real vendor failure and a genuine "couldn't find
        # Nike's TikTok" both look the same shape otherwise.
        result["reasoning"] = next(iter(handles.values()), {}).get("reasoning")
    return result
