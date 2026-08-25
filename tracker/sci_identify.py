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

    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=4000,
            system=_SYSTEM,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 15}],
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception as e:
        logger.warning("sci_identify: identify_handles failed for %r: %s", company_name, e)
        return _empty_result("The identification step failed unexpectedly.")

    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = text_blocks[-1] if text_blocks else ""
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("sci_identify: unparsable response for %r", company_name)
        return _empty_result("The identification step returned an unreadable response.")
    return parsed
