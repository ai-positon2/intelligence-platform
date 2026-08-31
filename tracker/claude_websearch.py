"""Shared Claude + server-side web_search helper.

Every hard-won lesson from tracker/sci_identify.py, factored out so the next
feature that needs a grounded web lookup does not have to rediscover them:

1. The web_search tool is VERSIONED BY DATE and Anthropic sunsets old
   versions server-side. A single hardcoded version fails every call the day
   it retires, so this tries a newest-first list and caches whichever one
   actually worked as a fast-path hint (never as a permanent choice -- when
   the cached one stops working the loop still falls through the rest).
2. "This tool version is rejected" must be told apart from "this was a bad
   minute". A 429 or a 5xx would fail identically on every other version, so
   burning through the list on one is pure waste and hides the real cause.
3. A multi-search lookup routinely outruns a short blocking timeout, which
   surfaces as a generic failure on every subtask at once. Always stream.
4. THE ONE THAT COST TWO ROUNDS OF FIXES: with web_search active the API
   splits the reply into one text block per cited span, so the answer arrives
   in pieces and content[-1] is a bare tail like '}'. Join every text block,
   in order. See [[reference-anthropic-web-search-blocks]].
5. A bare `except Exception` collapsing every failure into one string is how
   two structurally different bugs came to look identical. Errors here carry
   a `kind`, and callers can act on it.

tracker/sci_identify.py predates this module and still carries its own copy
of the same logic. It is deliberately NOT refactored here: it is a working,
twice-debugged path and this module has no production mileage yet. Migrate it
the next time that file is opened for another reason.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# Newest first. Keep in sync with tracker/sci_identify.py until that module
# migrates onto this one.
WEB_SEARCH_TOOL_VERSIONS = ("web_search_20260318", "web_search_20260209",
                            "web_search_20250305")
_WEB_SEARCH_TOOL: str | None = None

_UNSUPPORTED_TOOL_MARKERS = ("unsupported", "unknown tool", "invalid tool",
                             "not supported", "does not support", "unrecognized",
                             "no such tool", "deprecated", "invalid_value",
                             "invalid_request_error")

# Failure kinds. Callers switch on these rather than matching on message text.
ERR_NOT_CONFIGURED = "not_configured"
ERR_NO_TOOL_VERSION = "no_tool_version"
ERR_TRANSPORT = "transport"
ERR_MAX_TOKENS = "max_tokens"
ERR_EMPTY = "empty_response"
ERR_UNPARSABLE = "unparsable"

_RESULT_KEYS = ("text", "raw", "error", "stop_reason", "text_block_count",
                "tool_version", "search_count")


def _tool_version_unsupported(err) -> bool:
    """True only for "this dated tool version is rejected outright". A rate
    limit, timeout or 5xx is a bad minute, not a bad version, and must never
    be treated as a reason to burn through the rest of the list."""
    status = getattr(err, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return False
    text = str(err or "").lower()
    return any(m in text for m in _UNSUPPORTED_TOOL_MARKERS)


def describe_exception(e) -> str:
    status = getattr(e, "status_code", None)
    msg = str(e) or type(e).__name__
    if len(msg) > 220:
        msg = msg[:220] + "..."
    return ("HTTP %s: %s" % (status, msg)) if status else msg


def _client(timeout: float):
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=timeout, max_retries=2)


def _err(kind: str, detail: str) -> dict:
    return {"kind": kind, "detail": detail[:500]}


def ask(system: str, user: str, *, max_uses: int = 8, max_tokens: int = 8000,
        timeout: float = 280.0, model: str | None = None) -> dict:
    """One streamed Claude call, with the web_search tool when max_uses > 0.

    max_uses <= 0 means "no tool at all", which is a real mode rather than a
    degenerate one: extracting rows from a page already in hand must NOT be
    allowed to search, or the model completes a partial list from elsewhere
    and the output stops being a record of what that page said. Sending
    max_uses: 0 in a tools array would be a different thing (a tool offered
    and capped at zero) and some API versions reject it outright, so the tool
    is omitted entirely, which also skips the version loop below since there
    is no versioned tool to negotiate.

    Never raises. Returns a dict with `text` (every text block joined, in
    order) and `error` (None on success, else a {kind, detail} dict). A
    caller that only checks `text` still behaves correctly, because `text` is
    "" on every failure path.
    """
    client = _client(timeout)
    if client is None:
        return {"text": "", "raw": "", "stop_reason": None, "text_block_count": 0,
                "tool_version": None, "search_count": 0,
                "error": _err(ERR_NOT_CONFIGURED,
                              "ANTHROPIC_API_KEY is not configured on this deployment.")}

    model_id = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    global _WEB_SEARCH_TOOL
    if max_uses > 0:
        versions = WEB_SEARCH_TOOL_VERSIONS
        if _WEB_SEARCH_TOOL in versions:
            versions = (_WEB_SEARCH_TOOL,) + tuple(v for v in versions if v != _WEB_SEARCH_TOOL)
    else:
        versions = (None,)

    resp, used_version, last_err = None, None, None
    for version in versions:
        tools = ([{"type": version, "name": "web_search", "max_uses": max_uses}]
                 if version else [])
        try:
            with client.messages.stream(
                model=model_id,
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                resp = stream.get_final_message()
        except Exception as e:
            last_err = e
            logger.warning("claude_websearch: call with tool '%s' failed: %s", version, e)
            resp = None
            if version and _tool_version_unsupported(e):
                continue
            break
        else:
            used_version = version
            if version and _WEB_SEARCH_TOOL != version:
                _WEB_SEARCH_TOOL = version
                logger.info("claude_websearch: tool version '%s' confirmed working", version)
            break

    if resp is None:
        kind = ERR_TRANSPORT if last_err is not None else ERR_NO_TOOL_VERSION
        detail = describe_exception(last_err) if last_err is not None \
            else "no usable web_search tool version"
        if last_err is not None and max_uses > 0 and _tool_version_unsupported(last_err):
            # Fell out of the loop with every dated version rejected, which is
            # a different operator action (this module needs a new version
            # string) from a network or key problem.
            kind = ERR_NO_TOOL_VERSION
        return {"text": "", "raw": "", "stop_reason": None, "text_block_count": 0,
                "tool_version": None, "search_count": 0, "error": _err(kind, detail)}

    # Join EVERY text block, in order. Not content[-1]: with web_search on,
    # the answer is split into one block per cited span and the last one is a
    # fragment. This is the single highest-value line in the module.
    blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    text = "".join(blocks)
    searches = sum(1 for b in resp.content
                   if getattr(b, "type", "") == "server_tool_use")
    stop_reason = getattr(resp, "stop_reason", None)

    out = {"text": text, "raw": text, "stop_reason": stop_reason,
           "text_block_count": len(blocks), "tool_version": used_version,
           "search_count": searches, "error": None}

    if stop_reason == "max_tokens":
        # Distinct from "we could not read it": it ran out of room mid-answer.
        # Named so it never hides behind a generic unreadable-response string.
        out["error"] = _err(ERR_MAX_TOKENS,
                            "Ran out of output budget before finishing "
                            "(stop_reason=max_tokens). Raise max_tokens or lower max_uses.")
    elif not blocks:
        out["error"] = _err(ERR_EMPTY,
                            "The search ran but the model never wrote an answer "
                            "(stop_reason=%s)." % stop_reason)
    return out


def extract_json(raw: str):
    """Pull the first balanced JSON object or array out of a reply.

    Models wrap JSON in prose or a ```json fence even when told not to, and
    with web_search on, the citation-bearing prose is often unavoidable. Scans
    for a balanced structure while ignoring braces inside string literals,
    rather than a greedy first-to-last slice that swallows trailing text.
    """
    if not raw:
        return None
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(raw)):
                ch = raw[i]
                if esc:
                    esc = False
                    continue
                if ch == "\\" and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw[start:i + 1])
                        except Exception:
                            break
            start = raw.find(opener, start + 1)
    return None
