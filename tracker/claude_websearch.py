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
import re

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
# The web_search tool refused a call and returned an error block in place of
# results, so the model wrote its answer from whatever it already had. This
# is the tool failing, and it must never be reported as "we looked and found
# nothing".
ERR_SEARCH_LIMIT = "search_limit"

# Error codes that mean the search DID NOT RUN, as opposed to running and
# matching nothing. `too_many_requests` is rate limiting and `unavailable` is
# the tool being down; in both cases a query the model wanted answered came
# back with nothing, and no amount of caller planning would have avoided it.
#
# `max_uses_exceeded` is deliberately NOT in this list. See below.
SEARCH_STARVED_CODES = ("too_many_requests", "unavailable")

# The caller's own budget, spent.
#
# This was the most expensive mistake in this module's history, so the
# reasoning is written down rather than left to be re-derived.
#
# `max_uses_exceeded` was originally classed as starvation, on the theory that
# it came from "a separate server-side limit across the turn" that a caller
# could not avoid. The evidence for that theory was that max_uses is honoured:
# a probe capping it at 1 billed exactly one search. That is evidence max_uses
# is ENFORCED. It says nothing about how, and the how is this: when the model
# reaches for search N+1 under `max_uses: N`, the tool answers with an error
# block whose code is `max_uses_exceeded`. It is the enforcement mechanism.
#
# Measured directly (max_uses=1, a prompt needing three lookups): one billed
# search, seven real results, a complete answer naming the URL it found, and
# FIVE max_uses_exceeded blocks for the searches it went on to attempt.
#
# Every caller here also saturates its budget as a matter of course, so
# treating the code as a failed search discarded the results of very nearly
# every call that was working correctly. One live discovery run lost four of
# its six categories that way and produced a single event in half an hour.
#
# So a spent budget is reported, and is NOT an error. It says: this reply is
# complete and usable, and the model would have kept going if it could. What
# the caller does with that is the caller's business, and the two callers here
# do different things with it.
#
# A separate turn-level limit does also exist, and correcting the mistake
# above must not overcorrect into denying it. A probe at max_uses=1 sat for
# 471 seconds, ran ONE search, returned NO error block of any kind, and
# answered "I've hit a hard limit on web search tool calls for this turn and
# it isn't resetting despite waiting." Nothing in the reply structure said so:
# no error code, a normal stop_reason, real text. That shape is invisible here
# and there is nothing honest to key off, because the only statement of what
# happened is the model's own prose. `search_count` is the one hard number a
# caller can weigh against the budget it set, which is why it is reported on
# every reply.
SEARCH_BUDGET_CODES = ("max_uses_exceeded",)

_RESULT_KEYS = ("text", "raw", "error", "stop_reason", "text_block_count",
                "tool_version", "search_count", "tool_errors", "usage",
                "budget_spent")


def _search_count(resp, usage: dict) -> int:
    """How many WEB SEARCHES this reply actually ran.

    Not the number of server_tool_use blocks. web_search is one of several
    server-side tools and the model reaches for the others freely: a probe
    that capped web_search at 1 came back with eighteen server_tool_use
    blocks, seventeen of which were code execution. Counting blocks therefore
    reported eighteen searches for a reply that ran one.

    That number is not cosmetic. Two callers use it as the "did you actually
    look this up, or are you reciting?" guard and discard the answer when it
    is zero. Counting every tool alike lets a reply that ran no search at all
    satisfy the guard, and a recalled list of conferences is then accepted as
    a confirmed one.

    usage.server_tool_use.web_search_requests is the billed count and is
    authoritative. The block scan is the fallback for responses that carry no
    usage, and it filters on the tool name for the same reason.
    """
    billed = usage.get("web_search_requests")
    if isinstance(billed, int):
        return billed
    return sum(1 for b in getattr(resp, "content", None) or []
               if getattr(b, "type", "") == "server_tool_use"
               and getattr(b, "name", "") == "web_search")


def _tool_errors(resp) -> list:
    """Error codes the web_search tool returned in place of results.

    A web_search_tool_result block carries either a list of results or a
    single error object. The error is the only in-band signal that a search
    was refused rather than answered, and without reading it a starved search
    is indistinguishable from a thorough one that found nothing.
    """
    codes = []
    for b in getattr(resp, "content", None) or []:
        if getattr(b, "type", "") != "web_search_tool_result":
            continue
        content = getattr(b, "content", None)
        for item in (content if isinstance(content, list) else [content]):
            if item is None:
                continue
            code = getattr(item, "error_code", None)
            if code is None and isinstance(item, dict):
                code = item.get("error_code")
            if code:
                codes.append(str(code))
    return codes


def _usage(resp) -> dict:
    """Token and billed-search counts, flattened. Empty when unavailable.

    Reported so a run can say what it cost. Without this the only number
    anyone had was wall-clock time, which says nothing about the bill.
    """
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    out = {}
    for f in ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens"):
        v = getattr(u, f, None)
        if v is not None:
            out[f] = v
    stu = getattr(u, "server_tool_use", None)
    if stu is not None:
        v = getattr(stu, "web_search_requests", None)
        if v is not None:
            out["web_search_requests"] = v
    return out


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
    from .event_intel_jobs import CURRENT
    return Anthropic(api_key=key, timeout=timeout, max_retries=0 if CURRENT.get() else 2)


def _err(kind: str, detail: str) -> dict:
    return {"kind": kind, "detail": detail[:500]}


# What each failure means to a person reading a report, as opposed to a person
# fixing this file.
#
# `detail` is written for the log. It names the stop_reason, it quotes the
# tool's own error code back, and in one case it ends with "Raise max_tokens or
# lower max_uses", which is an instruction to us and gibberish to anybody else.
# Callers were interpolating it straight into report prose, so a live client
# report carried the line:
#
#   "Regional flagship (max_tokens: Ran out of output budget before finishing
#    (stop_reason=max_tokens). Raise max_tokens or lower max_uses.)"
#
# under a heading, in a document somebody is paying for. The reader learned
# nothing they could act on and was handed our plumbing to carry.
#
# These are clauses, not sentences, so a caller can put them where the reason
# belongs in its own sentence. Every kind this module can return has one, and
# an unknown kind falls back to a clause rather than to `detail`: falling back
# to the detail is exactly the leak this exists to close.
READER_REASON = {
    ERR_NOT_CONFIGURED: "the search service is not switched on for this server",
    ERR_NO_TOOL_VERSION: "the search service would not accept any search "
                         "version this server knows how to ask for",
    ERR_TRANSPORT: "the connection to the search service failed part-way",
    ERR_MAX_TOKENS: "the answer ran past the length it was allowed and "
                    "stopped mid-sentence",
    ERR_EMPTY: "the search ran but no answer came back with it",
    ERR_UNPARSABLE: "the answer came back in a shape that could not be read",
    ERR_SEARCH_LIMIT: "the search tool stopped returning results part-way "
                      "through",
}

_READER_FALLBACK = "the search could not be completed"


def reader_reason(err) -> str:
    """One clause saying why a call failed, safe to print to anybody.

    Takes the error dict `ask` returns, or a bare kind. Never returns the
    developer detail and never returns an empty string.
    """
    kind = err.get("kind") if isinstance(err, dict) else err
    return READER_REASON.get(kind or "", _READER_FALLBACK)


# ── what a run cost ──────────────────────────────────────────────────────
#
# Every reply already carries `usage`, and until now nothing added it up. The
# consequence, measured: the only cost figure anyone had for an Event &
# Conference Intelligence run was $9.13, from a design that had since been
# replaced, and a later instrumented run came in at $9.64 with a completely
# different shape (three 10-search calls accounting for a third of the input
# bill). A paying feature whose unit cost is invisible in production cannot
# be priced, budgeted, or caught regressing.
#
# These totals are summed through RETURN VALUES rather than into a module
# global on purpose. `event_intel_pipeline.run_job` is a thread entry point
# and two runs can be in flight in one process, so a shared accumulator
# would bill one client's run for another's searches. Adding dicts up the
# call tree cannot get that wrong.

# Sonnet list pricing per million tokens, and the server-side search line
# item per request. Declared here so a report explaining a cost and the
# arithmetic producing it cannot drift apart.
USD_PER_M_INPUT = 3.0
USD_PER_M_OUTPUT = 15.0
USD_PER_SEARCH = 0.01

_SPEND_KEYS = ("calls", "input_tokens", "output_tokens", "cache_read_tokens",
               "searches")


def spend_of(res: dict) -> dict:
    """One reply's usage, as a spend record. Never raises."""
    u = (res or {}).get("usage") or {}

    def _n(key):
        try:
            return int(u.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return {"calls": 1,
            "input_tokens": _n("input_tokens"),
            "output_tokens": _n("output_tokens"),
            "cache_read_tokens": _n("cache_read_input_tokens"),
            # The reply's own count, which is the billed one. See
            # SEARCH_BUDGET_CODES: server_tool_use blocks are not searches.
            "searches": int((res or {}).get("search_count") or 0)}


def spend_sum(*records) -> dict:
    """Add spend records. Accepts None and missing keys."""
    out = {k: 0 for k in _SPEND_KEYS}
    for r in records:
        if not r:
            continue
        if isinstance(r, (list, tuple)):
            r = spend_sum(*r)
        for k in _SPEND_KEYS:
            try:
                out[k] += int(r.get(k) or 0)
            except (TypeError, ValueError):
                pass
    return out


def spend_usd(record: dict) -> float:
    """What a spend record costs, rounded to the cent.

    Cached input is billed at a tenth of fresh input, and is counted
    separately rather than folded in, so a run that benefits from caching
    does not silently report the uncached price.
    """
    r = record or {}
    def _n(k):
        try:
            return int(r.get(k) or 0)
        except (TypeError, ValueError):
            return 0
    usd = (_n("input_tokens") / 1e6 * USD_PER_M_INPUT
           + _n("cache_read_tokens") / 1e6 * USD_PER_M_INPUT * 0.1
           + _n("output_tokens") / 1e6 * USD_PER_M_OUTPUT
           + _n("searches") * USD_PER_SEARCH)
    return round(usd, 2)


def _ask(system: str, user: str, *, max_uses: int = 8, max_tokens: int = 8000,
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

    `budget_spent` is True when the model reached for one more search than
    `max_uses` allowed. That is NOT an error and never sets one: the reply is
    complete and holds every result the searches it did run returned. It only
    says the model would have kept looking, which is worth a sentence in a
    report and is worth nothing at all if the reply already answered the
    question. Expect it to be True on most calls, because these callers size
    `max_uses` to what they are willing to spend rather than to what a model
    would ideally use.
    """
    client = _client(timeout)
    if client is None:
        return {"text": "", "raw": "", "stop_reason": None, "text_block_count": 0,
                "tool_version": None, "search_count": 0, "tool_errors": [],
                "usage": {}, "budget_spent": False,
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
                "tool_version": None, "search_count": 0, "tool_errors": [],
                "usage": {}, "budget_spent": False,
                "error": _err(kind, detail)}

    # Join EVERY text block, in order. Not content[-1]: with web_search on,
    # the answer is split into one block per cited span and the last one is a
    # fragment. This is the single highest-value line in the module.
    blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    joined = "".join(blocks)
    # `text` is what callers parse and store; `raw` stays exactly what
    # came back, so a diagnostic still shows the reply as it was sent.
    text = strip_citation_markup(joined)
    stop_reason = getattr(resp, "stop_reason", None)
    tool_errors = _tool_errors(resp)
    usage = _usage(resp)
    searches = _search_count(resp, usage)

    out = {"text": text, "raw": joined, "stop_reason": stop_reason,
           "text_block_count": len(blocks), "tool_version": used_version,
           "search_count": searches, "tool_errors": tool_errors,
           "usage": usage,
           # The model asked for one more search than it was given. Reported
           # on a successful reply, never as an error: the answer is complete
           # and every result it did get is in it.
           "budget_spent": any(c in SEARCH_BUDGET_CODES for c in tool_errors),
           "error": None}

    if any(c in SEARCH_STARVED_CODES for c in tool_errors):
        # Deliberately BEFORE the max_tokens and empty checks. A starved
        # search usually still produces prose, so every later check would
        # class it as a good answer.
        out["error"] = _err(
            ERR_SEARCH_LIMIT,
            "The web_search tool stopped returning results part-way through "
            "(%s) after %d searches, so this answer was written from an "
            "incomplete search rather than a finished one."
            % (", ".join(sorted(set(c for c in tool_errors
                                    if c in SEARCH_STARVED_CODES))), searches))
    elif stop_reason == "max_tokens":
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


_CITE_PAIR = re.compile(r"<cite\b[^>]*>(.*?)</cite>", re.I | re.S)
_CITE_ANY = re.compile(r"</?cite\b[^>]*>", re.I | re.S)
_CITE_EDGE = re.compile(r"^[\"\'\u201c\u2018]+|[\"\'\u201d\u2019]+$")


def strip_citation_markup(text: str) -> str:
    """Turn inline cite markup back into the quotation it was wrapping.

    With web_search on, the model marks every span it lifted off a page with
    an inline cite tag carrying the index of the result it came from. That is
    presentation markup for a chat client, and this module's callers are not
    one: they hand the text to json.loads and then store the strings. So the
    tag travelled all the way to a rendered page and readers saw it printed
    literally in the middle of a sentence.

    It also cannot simply be deleted. What the tag wraps is a direct quote
    from the event's own site, and the sentence around it reads as one:
    "a recap noted attendees came from 47 states". Dropping the marks turns a
    quotation into our own claim about someone else's page, which is the one
    thing this agent must never do. So the pair becomes real quotation marks,
    and quote characters the model already put inside are not doubled.

    An opener with no closer, or a stray closer, is markup with no quotation
    to recover, and is removed rather than shown.
    """
    if not text:
        return ""
    low = text.lower()
    # Both halves. A closing tag does not contain the opening one, so a guard
    # that looks only for the opener returns a stray closer to the caller
    # untouched, which is the one shape that reaches a page as visible markup.
    if "<cite" not in low and "</cite" not in low:
        return text

    def _one(m):
        inner = _CITE_ANY.sub("", m.group(1)).strip()
        inner = _CITE_EDGE.sub("", inner).strip()
        return "\u201c%s\u201d" % inner if inner else ""

    return _CITE_ANY.sub("", _CITE_PAIR.sub(_one, text))


_EM_EN_DASH = re.compile(r"\s*[\u2013\u2014]\s*")


def strip_em_dash(text: str) -> str:
    """An em or en dash the model wrote, turned into a comma.

    House style for every client-facing report in this codebase has no em
    dashes. A model asked to write a sentence reaches for one anyway, and it
    was found live in five different fields across three different modules
    (a marquee event's own name, an audit's `why`, a score's `description`
    and `relevance_note`, a resolved event's `edition`) because each field
    was sanitised for length and stray markup but never for this. This is the
    one place that fix now lives; event_intel_intake.py had its own private
    copy of exactly this substitution before this function existed, written
    when the same defect first showed up in a single field, and now uses this
    one instead.

    Comma, not deletion: the two clauses a dash was joining are usually still
    a complete thought without it, and dropping the dash outright collides
    two clauses into one run-on. Whitespace on both sides of the dash is
    absorbed into the substitution so a comma is never doubled against an
    existing space.

    Callers apply this to a field they already own the cleaning of (a report
    string, a name, a note), not to a whole raw model reply: a direct quote
    preserved from a page (see strip_citation_markup above) can legitimately
    contain a dash as part of the source's own words, and this module cannot
    tell the two apart once the quotation marks are in place. Every caller
    of this function is cleaning the model's OWN prose, not someone else's,
    which is the same call event_intel_intake.py already made.
    """
    return _EM_EN_DASH.sub(", ", str(text or ""))


def extract_json(raw: str, require: str | None = None):
    """Pull the first balanced JSON object or array out of a reply.

    Models wrap JSON in prose or a ```json fence even when told not to, and
    with web_search on, the citation-bearing prose is often unavoidable. Scans
    for a balanced structure while ignoring braces inside string literals,
    rather than a greedy first-to-last slice that swallows trailing text.

    `require` is the envelope key the caller asked the model for ("rows",
    "events", "scores"). Pass it. When a reply is cut off mid-array, which is
    what stop_reason=max_tokens produces, the outer object never closes and the
    first BALANCED object in the reply is the first row. That is a dict, so an
    isinstance check passes it, and the caller then reads an envelope key that
    is not there and concludes the model found nothing. A 300-exhibitor page
    becomes an event that publishes no exhibitors, recorded as successfully
    read. With `require` set, an object that is not the envelope is refused,
    the caller reports unreadable rather than empty, and recovery gets its
    chance.
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
                            found = json.loads(raw[start:i + 1])
                        except Exception:
                            break
                        if require and not (isinstance(found, dict)
                                            and require in found):
                            # Parsed, but it is not the thing that was asked
                            # for. Keep looking rather than hand back a row
                            # dressed as an envelope, or a bare array found
                            # somewhere inside one.
                            break
                        return found
            start = raw.find(opener, start + 1)
    return None


def ask(system: str, user: str, *, max_uses: int = 8, max_tokens: int = 8000,
        timeout: float = 280.0, model: str | None = None):
    """Record and bound event-worker calls; other platform callers are unchanged."""
    from .event_intel_jobs import CURRENT, reserve_call, finish_call
    import time
    if CURRENT.get() is None:
        return _ask(system,user,max_uses=max_uses,max_tokens=max_tokens,timeout=timeout,model=model)
    began = time.monotonic()
    try:
        reservation = reserve_call(system,user,model or os.getenv('ANTHROPIC_MODEL','claude-sonnet-5'),max_tokens,max_uses)
        if reservation['cached'] is not None:
            return reservation['cached']
    except RuntimeError as exc:
        return {'text':'', 'error':{'kind':'operational_limit','detail':str(exc)}, 'usage':{}}
    result = _ask(system,user,max_uses=max_uses,max_tokens=max_tokens,timeout=timeout,model=model)
    finish_call(reservation['id'],result,int((time.monotonic()-began)*1000))
    return result
