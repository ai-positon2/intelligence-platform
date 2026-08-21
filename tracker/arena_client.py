"""Arena (agent.thearena.ai) API client -- data fetching only, no business logic.

Three of the six workflows this vendor account exposes are used here: company
search, the multi-agent LinkedIn strategy analysis, and playbook generation.
The other two (history, single-run lookup) are deliberately not ported --
tracker/linkedin_playbook_store.py persists every run at write time, so this
app never needs to ask the vendor "what did user X save", which is exactly the
unscoped-by-id lookup that made a prior standalone tool's history workflow an
IDOR (any caller could read any email's saved runs).

Response parsing (whole-body JSON vs. SSE-style `data:` lines, and the flat-
output-to-namespace guessing) is ported from that prior tool's TypeScript
client, since the vendor's own contract is otherwise undocumented here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

ARENA_BASE = "https://agent.thearena.ai/api/workflows"

_WORKFLOW_SEARCH = "c821b89f-5f32-44b3-9cc6-c0eea5b72b36"
_WORKFLOW_ANALYSIS = "13e76c2b-bbdc-43c5-835e-92027a6c43e9"
_WORKFLOW_PLAYBOOK = "00bfdfb5-3726-4a32-a130-1eeb51d6a238"

# The multi-agent analysis workflow's full output contract -- one dotted key
# per field each of the five agents can return, plus the two pass-through
# namespaces (company profile, recent posts) that never collide with them.
ANALYSIS_OUTPUTS = [
    "strategyagent.strategy", "strategyagent.personas", "strategyagent.hookLibrary",
    "strategyagent.ctaLibrary", "strategyagent.audienceDetail",
    "contentcreativeagent.content", "contentcreativeagent.creative",
    "contentcreativeagent.engagement", "contentcreativeagent.topicClusters",
    "messagingagent.company", "messagingagent.messaging", "messagingagent.stats",
    "messagingagent.summary",
    "creativeinsightagent.imageryTypes", "creativeinsightagent.recommendations",
    "creativeinsightagent.observations", "creativeinsightagent.textStyle",
    "competitiveagent.campaigns", "competitiveagent.competitive",
    "competitiveagent.launches", "competitiveagent.messagingEvolution",
    "competitiveagent.recommendations", "competitiveagent.scorecard",
    "competitiveagent.scorecardOverall",
    "getcompanyprofile.id", "getcompanyprofile.name", "getcompanyprofile.description",
    "getcompanyprofile.public_identifier", "getcompanyprofile.profile_url",
    "getcompanyprofile.followers_count", "getcompanyprofile.employee_count",
    "getcompanyprofile.website", "getcompanyprofile.logo", "getcompanyprofile.profile",
    "getcompanypost.items",
]

# namespace -> the field names that, together, identify a nested output block
# as belonging to that agent. Used to place an already-flat (non-dotted)
# response onto the right namespace -- the same disambiguation the vendor's
# own streaming shape sometimes requires. The five *agent groups are ported
# verbatim from the prior tool's AGENT_KEY_GROUPS table; getcompanyprofile and
# getcompanypost are added here since this app also requests those two
# namespaces (the prior tool never had to place them, since it only ever read
# a single already-namespaced field: history/single-run lookups it made
# unscoped by id -- see the module docstring).
_AGENT_KEY_GROUPS: list[tuple[str, set[str]]] = [
    ("strategyagent", {"strategy", "personas", "hookLibrary", "ctaLibrary", "audienceDetail"}),
    ("contentcreativeagent", {"content", "creative", "engagement", "topicClusters"}),
    ("messagingagent", {"company", "messaging", "stats", "summary"}),
    ("creativeinsightagent", {"imageryTypes", "recommendations", "observations", "textStyle"}),
    ("competitiveagent", {"campaigns", "competitive", "launches", "messagingEvolution",
                          "recommendations", "scorecard", "scorecardOverall"}),
    ("getcompanyprofile", {"id", "name", "description", "public_identifier", "profile_url",
                          "followers_count", "employee_count", "website", "logo", "profile"}),
    ("getcompanypost", {"items"}),
]

# The full set of namespaces ANALYSIS_OUTPUTS asks for, used only to log when
# one goes missing from a response -- see _log_missing_namespaces below.
_EXPECTED_NAMESPACES = sorted({k.split(".", 1)[0] for k in ANALYSIS_OUTPUTS})

_TIMEOUT_SEARCH = 25
_TIMEOUT_LONG = 300  # own-brand/competitor analysis and playbook generation

# Company search is cheap and fast, so a transient failure is worth retrying
# inside the request; the analysis and playbook workflows are multi-minute
# billed runs, so those get one attempt and a clear error instead.
_SEARCH_ATTEMPTS = 3
# Total wall clock a search may spend across all attempts. gunicorn's worker
# timeout is 120s (railway.toml), so the budget has to leave room for one full
# per-attempt timeout under it -- a retry that cannot finish inside the budget
# is skipped rather than started.
_SEARCH_BUDGET = 60.0
_RETRY_BACKOFF = (0.6, 1.8)
# Statuses worth a second attempt: the vendor is up but momentarily unable.
# 401/403/404 are deliberately absent -- a bad key or a workflow that no
# longer exists fails identically on every retry, so retrying only delays
# telling the operator what is actually wrong.
_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Failure kinds. Every one of these used to be flattened into "no results",
# which is why a dead key, a deleted workflow, a rate limit and a genuinely
# unknown company all showed the same "No companies found." in the UI.
ERR_NOT_CONFIGURED = "not_configured"
ERR_TIMEOUT = "timeout"
ERR_HTTP = "http_status"
ERR_NETWORK = "network"
ERR_UNPARSABLE = "unparsable"
ERR_SHAPE = "unexpected_shape"


def _api_key() -> str:
    return os.environ.get("ARENA_API_KEY", "")


def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


def _err(kind: str, detail: str = "", status: int | None = None) -> dict:
    """One failure, described. `detail` is for operators (logs, the admin
    self-test) and may quote the vendor's own words; it never carries the API
    key, which only ever lives in a request header."""
    return {"kind": kind, "status": status, "detail": detail[:500], "attempts": 1}


def describe_error(err: dict | None) -> str:
    """A failure kind rendered for whoever is looking at the screen. Says what
    happened and who can do something about it, since "try again" is useless
    advice for a revoked key and essential advice for a rate limit."""
    if not _is_dict(err):
        return ""
    kind, status = err.get("kind"), err.get("status")
    if kind == ERR_NOT_CONFIGURED:
        return ("Company search is not configured on this deployment: "
                "ARENA_API_KEY is missing.")
    if kind == ERR_HTTP and status in (401, 403):
        return ("The LinkedIn data provider rejected our API key (HTTP %s). "
                "The key needs to be renewed before search or analysis will "
                "work." % status)
    if kind == ERR_HTTP and status == 404:
        return ("The provider no longer recognises this workflow (HTTP 404), "
                "so its ID has changed on their side.")
    if kind == ERR_HTTP and status == 429:
        return "The provider is rate-limiting us. Try again in a minute."
    if kind == ERR_HTTP and status == 402:
        return ("The provider refused the call for billing reasons (HTTP 402), "
                "so the account is likely out of credit.")
    if kind == ERR_HTTP:
        return ("The provider returned an error (HTTP %s). This is on their "
                "side, not ours." % (status if status is not None else "?"))
    if kind == ERR_TIMEOUT:
        return "The provider did not respond in time. Try again in a moment."
    if kind == ERR_NETWORK:
        return "The provider could not be reached. Try again in a moment."
    if kind == ERR_UNPARSABLE:
        return "The provider's response could not be read."
    if kind == ERR_SHAPE:
        return ("The provider answered, but with no company list in it, so "
                "their response format has changed.")
    return "Company search is unavailable right now."


def is_retryable(err: dict | None) -> bool:
    """Whether trying the same call again could plausibly succeed. Drives both
    this module's own retries and whether the page offers a Retry button --
    offering one for a revoked key would just teach people to click it."""
    if not _is_dict(err):
        return False
    if err.get("kind") in (ERR_TIMEOUT, ERR_NETWORK):
        return True
    return err.get("kind") == ERR_HTTP and err.get("status") in _RETRY_STATUSES


def _execute_once(url: str, key: str, payload: dict, timeout: int) -> tuple[dict | None, dict | None]:
    """One POST, parsed. Returns (parsed, None) or (None, error)."""
    headers = {"Content-Type": "application/json", "X-API-Key": key}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.Timeout as e:
        return None, _err(ERR_TIMEOUT, "No response within %ss: %s" % (timeout, e))
    except requests.RequestException as e:
        return None, _err(ERR_NETWORK, "%s: %s" % (type(e).__name__, e))
    try:
        status = int(getattr(resp, "status_code", 200) or 200)
        if status >= 400:
            body = (getattr(resp, "text", "") or "").strip()
            return None, _err(ERR_HTTP, "HTTP %s. Body: %s" % (status, body[:300] or "(empty)"),
                              status=status)
        return _parse_response_text(resp.text), None
    except Exception as e:
        # Anything past a completed HTTP round trip -- an unreadable body, a
        # parse blowup -- still has to come back as a stated reason, because
        # one caller of this is a daemon thread that would otherwise die mute.
        return None, _err(ERR_UNPARSABLE, "%s: %s" % (type(e).__name__, e))


def _execute(workflow_id: str, payload: dict, timeout: int, attempts: int = 1,
             budget: float | None = None) -> tuple[dict | None, dict | None]:
    """POST one workflow. Returns (parsed, None) on success, or (None, error)
    describing what went wrong. Never raises -- every caller degrades to a
    stated reason rather than a 500, matching tracker/apollo_client.py's
    philosophy, but unlike the previous version of this function the reason
    survives all the way to whoever is looking at the page."""
    key = _api_key()
    if not key:
        logger.info("arena_client: ARENA_API_KEY not configured, skipping call")
        return None, _err(ERR_NOT_CONFIGURED, "ARENA_API_KEY is not set on this deployment.")
    url = f"{ARENA_BASE}/{workflow_id}/execute"
    started = time.monotonic()
    last: dict | None = None
    for attempt in range(1, max(1, attempts) + 1):
        parsed, err = _execute_once(url, key, payload, timeout)
        if err is None:
            return parsed, None
        err["attempts"] = attempt
        last = err
        if attempt >= attempts or not is_retryable(err):
            break
        delay = _RETRY_BACKOFF[min(attempt - 1, len(_RETRY_BACKOFF) - 1)]
        if budget is not None and (time.monotonic() - started) + delay + timeout > budget:
            logger.warning("arena_client: workflow %s out of retry budget after %s attempt(s)",
                           workflow_id, attempt)
            break
        time.sleep(delay)
    logger.warning("arena_client: workflow %s failed after %s attempt(s): %s",
                   workflow_id, (last or {}).get("attempts"), (last or {}).get("detail"))
    return None, last


def _parse_response_text(text: str) -> dict:
    """Whole-body JSON first; if that fails, treat the body as an SSE-style
    stream of `data: {...}` lines."""
    trimmed = text.strip()
    try:
        parsed = json.loads(trimmed)
        if _is_dict(parsed):
            return parsed
    except (ValueError, json.JSONDecodeError):
        pass
    return _parse_sse_text(trimmed)


def _parse_sse_text(trimmed: str) -> dict:
    output: dict = {}
    final_output: dict = {}
    chunks_by_block: dict[str, str] = {}
    # Diagnostic only (see _sseDebug below) -- never affects parsing behavior.
    event_type_counts: dict[str, int] = {}
    final_event_output_keys: set[str] = set()
    unparsed_line_count = 0

    for line in trimmed.split("\n"):
        l = line.strip()
        if not l.startswith("data:"):
            continue
        body = l[5:].strip()
        if not body or body in ("[DONE]", '"[DONE]"'):
            continue
        try:
            evt = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            unparsed_line_count += 1
            continue
        if not _is_dict(evt):
            continue
        evt_type = evt.get("event")
        if isinstance(evt_type, str):
            event_type_counts[evt_type] = event_type_counts.get(evt_type, 0) + 1
        # The streaming API's final event carries the complete, structured
        # output: {event: 'final', data: {output: {<blockId>: {...}}}}. This
        # multi-agent workflow can emit more than one "final" event -- one per
        # agent/node as it finishes, not one for the whole run -- so this
        # merges every final event's output rather than letting the last one
        # overwrite the others (which previously discarded every namespace
        # except whichever agent's final event happened to arrive last).
        if evt.get("event") == "final" and _is_dict(evt.get("data")) and _is_dict(evt["data"].get("output")):
            final_output.update(evt["data"]["output"])
            final_event_output_keys.update(evt["data"]["output"].keys())
            continue
        # Intermediate streamed text chunks per block; accumulated as a
        # fallback in case no final event is present in the stream.
        block_id, chunk = evt.get("blockId"), evt.get("chunk")
        if isinstance(block_id, str) and isinstance(chunk, str):
            chunks_by_block[block_id] = chunks_by_block.get(block_id, "") + chunk
            continue
        _merge_event_output(evt, output)

    if not output:
        for block_id, chunk_text in chunks_by_block.items():
            merged = _parse_chunk_records(chunk_text)
            if merged:
                output[block_id] = merged
            elif chunk_text.strip():
                output[block_id] = chunk_text.strip()

    # Some workflows (e.g. the playbook) stream the whole content as chunks
    # and then emit a final event with an EMPTY output -- so the final events'
    # data (if any) is layered on top of, not instead of, whatever was
    # accumulated from chunk/intermediate events, since either source alone
    # might be incomplete.
    output.update(final_output)
    # A namespace missing from `output` after all of the above means the
    # vendor's stream never carried it -- these three numbers are what
    # distinguish "the wire never had it" (all zero/absent here) from a future
    # parsing regression, without needing production log access to tell them
    # apart. Stored alongside the run so the existing admin raw-data view
    # surfaces it for free on the next real run.
    return {
        "output": output,
        "_sseDebug": {
            "eventTypeCounts": event_type_counts,
            "finalEventOutputKeys": sorted(final_event_output_keys),
            "chunkBlockIds": sorted(chunks_by_block.keys()),
            "unparsedLineCount": unparsed_line_count,
        },
    }


def _parse_chunk_records(chunk_text: str) -> dict:
    """Streamed chunks may be one large JSON document spanning many lines, or
    newline-separated JSON documents. Try the whole thing first, then merge
    every top-level JSON object line by line; non-object lines are skipped."""
    whole = chunk_text.strip()
    if whole:
        try:
            parsed = json.loads(whole)
            if _is_dict(parsed):
                return parsed
        except (ValueError, json.JSONDecodeError):
            pass
    merged: dict = {}
    for line in chunk_text.split("\n"):
        doc = line.strip()
        if not doc:
            continue
        try:
            parsed = json.loads(doc)
            if _is_dict(parsed):
                merged.update(parsed)
        except (ValueError, json.JSONDecodeError):
            continue
    return merged


def _merge_event_output(evt: dict, output: dict) -> None:
    for candidate in (evt.get("output"), evt.get("result"), evt.get("data")):
        if not _is_dict(candidate):
            continue
        if _is_dict(candidate.get("output")):
            output.update(candidate["output"])
        else:
            output.update(candidate)
    if isinstance(evt.get("key"), str) and "value" in evt:
        output[evt["key"]] = evt["value"]


def extract_output(parsed: dict) -> dict:
    """parsed -> its 'output' dict, wherever it landed."""
    if _is_dict(parsed.get("output")):
        return parsed["output"]
    result = parsed.get("result")
    if _is_dict(result) and _is_dict(result.get("output")):
        return result["output"]
    return parsed


def normalize_analysis_output(output: dict) -> dict:
    """Namespace a flat (non-dotted) analysis output onto 'strategyagent.strategy'-
    style keys by scoring each nested object's field names against the known
    agent/namespace key sets. Handles a MIXED response -- some keys already
    dotted, others not -- by deciding per key, not for the response as a
    whole: a response where most agents stream back already-namespaced keys
    but one or two land as a flat nested block (e.g. under an opaque block id)
    must still get that block namespaced, not skipped just because its
    siblings were already dotted."""
    normalized: dict = {}
    for key, value in output.items():
        if "." in key:
            normalized[key] = value
            continue
        if not _is_dict(value):
            normalized[key] = value
            continue
        child_keys = set(value.keys())
        best_ns, best_score = None, 0
        for namespace, known_keys in _AGENT_KEY_GROUPS:
            score = len(child_keys & known_keys)
            if score > best_score:
                best_ns, best_score = namespace, score
        if best_ns:
            for child_key, child_value in value.items():
                normalized[f"{best_ns}.{child_key}"] = child_value
        else:
            normalized[key] = value
    return normalized


def _log_missing_namespaces(output: dict) -> None:
    """Best-effort diagnostic: if a namespace we explicitly requested via
    ANALYSIS_OUTPUTS never shows up (dotted or otherwise) in the parsed
    result, log the namespaces that are missing and the top-level keys that
    ARE present. Never raises. Purely observational -- this exists so a
    report showing empty sections for specific namespaces can be diagnosed
    from Railway logs next time, instead of guessed at from screenshots."""
    try:
        present = {k.split(".", 1)[0] for k in output}
        missing = [ns for ns in _EXPECTED_NAMESPACES if ns not in present]
        if missing:
            logger.warning(
                "arena_client: analysis output missing namespace(s) %s -- "
                "top-level keys present were: %s", missing, sorted(output.keys()),
            )
    except Exception:
        pass


def _to_company(r: dict) -> dict | None:
    name = r.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    def _str(v):
        return v if isinstance(v, str) and v.strip() else None

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    raw_id = r.get("id")
    company_id = raw_id if isinstance(raw_id, str) else (str(raw_id) if isinstance(raw_id, (int, float)) else "")

    return {
        "id": company_id,
        "name": name,
        "logo": _str(r.get("logo")),
        "industry": _str(r.get("industry")),
        "location": _str(r.get("location")),
        "description": _str(r.get("description")),
        "summary": _str(r.get("summary")),
        "followers_count": _num(r.get("followers_count")),
        "profile_url": _str(r.get("profile_url")) or _str(r.get("linkedinUrl")),
        "website": _str(r.get("website")),
    }


def find_company_rows(output: dict) -> tuple[list | None, str]:
    """Locate the list of company records in a search response, and say where
    it was found. Returns (None, "") when the response carries no company list
    at all, which is a DIFFERENT outcome from finding an empty one: an empty
    list means the vendor looked and found nothing, while no list at all means
    the response wasn't the shape this client understands. Collapsing those two
    into "no results" is what made a broken integration look like an unknown
    company."""
    named = (
        ("companies", output.get("companies")),
        ("companylistingagent.companies", output.get("companylistingagent.companies")),
    )
    listing = output.get("companylistingagent")
    if _is_dict(listing):
        named = named + (("companylistingagent", listing.get("companies")),)
    for where, value in named:
        if isinstance(value, list):
            return value, where
    for key, v in output.items():
        if isinstance(v, list) and any(_is_dict(x) and isinstance(x.get("name"), str) for x in v):
            return v, key
        if _is_dict(v) and isinstance(v.get("companies"), list):
            return v["companies"], "%s.companies" % key
    return None, ""


def extract_companies(parsed: dict) -> list[dict]:
    rows, _ = find_company_rows(extract_output(parsed))
    return [c for c in (_to_company(r) for r in (rows or []) if _is_dict(r)) if c is not None]


def search_companies_result(company_name: str) -> dict:
    """Company search, with the reason attached when it comes back empty.

    Returns {"companies": [...], "error": <error|None>, "elapsed_ms": int,
    "source": <where the rows were found>}. `error` is None both on success and
    on a genuine zero-result search -- the caller tells those apart by whether
    `companies` is empty, and only claims "nothing matched" when error is
    None."""
    started = time.monotonic()
    parsed, err = _execute(_WORKFLOW_SEARCH, {
        "companyName": company_name,
        "stream": False,
        "selectedOutputs": ["companylistingagent.companies"],
    }, _TIMEOUT_SEARCH, attempts=_SEARCH_ATTEMPTS, budget=_SEARCH_BUDGET)
    elapsed = int((time.monotonic() - started) * 1000)
    if err is not None:
        return {"companies": [], "error": err, "elapsed_ms": elapsed, "source": ""}
    output = extract_output(parsed or {})
    rows, where = find_company_rows(output)
    if rows is None:
        detail = "HTTP 200 but no company list. Top-level keys: %s" % (sorted(output.keys())[:12],)
        logger.warning("arena_client: search returned an unrecognised shape. %s", detail)
        return {"companies": [], "error": _err(ERR_SHAPE, detail), "elapsed_ms": elapsed, "source": ""}
    companies = [c for c in (_to_company(r) for r in rows if _is_dict(r)) if c is not None]
    return {"companies": companies, "error": None, "elapsed_ms": elapsed, "source": where}


def search_companies(company_name: str) -> list[dict]:
    """Best-effort company search. [] if ARENA_API_KEY isn't configured or the
    call fails -- never raises. Callers that need to explain an empty result
    should use search_companies_result instead."""
    return search_companies_result(company_name)["companies"]


def run_analysis_result(company_name: str, company_id: str, email: str, run_type: str,
                        parent_run_id: str = "") -> dict:
    """Run the multi-agent LinkedIn strategy analysis. Returns
    {"output": <namespaced dict|None>, "error": <error|None>}. This is the slow
    (multi-minute) billed call, so it gets a single attempt: a retry would
    double the vendor spend on a run whose failure is usually a bad key or a
    changed workflow, neither of which a second attempt fixes. Callers run it
    off the request thread; see app.py's analyze route."""
    is_competitor = run_type == "COMPETITOR"
    parsed, err = _execute(_WORKFLOW_ANALYSIS, {
        "companyName": company_name,
        "companyId": company_id,
        "email": email,
        "type": "COMPETITOR" if is_competitor else "OWN",
        "isCompetitor": is_competitor,
        "id": parent_run_id if is_competitor else "",
        "stream": True,
        "selectedOutputs": ANALYSIS_OUTPUTS,
        "includeThinking": False,
        "includeToolCalls": False,
    }, _TIMEOUT_LONG)
    if err is not None:
        return {"output": None, "error": err}
    output = normalize_analysis_output(extract_output(parsed or {}))
    _log_missing_namespaces(output)
    if _is_dict((parsed or {}).get("_sseDebug")):
        output["_sseDebug"] = parsed["_sseDebug"]
    return {"output": output, "error": None}


def run_analysis(company_name: str, company_id: str, email: str, run_type: str,
                 parent_run_id: str = "") -> dict | None:
    """run_analysis_result's output alone, or None if the call failed."""
    return run_analysis_result(company_name, company_id, email, run_type, parent_run_id)["output"]


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$")


def _strip_code_fence(text: str) -> str:
    m = _CODE_FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _parse_playbook_content(output: dict) -> dict:
    agent_block = output.get("playbookagent")
    content_raw = output.get("content")
    if content_raw is None:
        content_raw = output.get("playbookagent.content")
    if content_raw is None and _is_dict(agent_block):
        content_raw = agent_block.get("content")

    # Streamed responses key content by an opaque block id with an empty
    # final event -- fall back to the first block value that carries anything.
    if content_raw is None:
        for v in output.values():
            if isinstance(v, str) and v.strip():
                content_raw = v
                break
            if _is_dict(v):
                content_raw = v.get("content") if isinstance(v.get("content"), str) and v["content"].strip() else v
                break

    if isinstance(content_raw, str):
        cleaned = _strip_code_fence(content_raw)
        try:
            parsed = json.loads(cleaned)
            if _is_dict(parsed):
                return parsed
        except (ValueError, json.JSONDecodeError):
            pass
        return {"raw": cleaned}
    if _is_dict(content_raw):
        return content_raw
    return {}


def run_playbook(email: str, run_id: str, mode: str) -> dict | None:
    """Generate a strategic playbook for a saved run. Returns the playbook
    content dict, or None if ARENA_API_KEY isn't configured or the call fails
    outright. A response that doesn't parse as structured content degrades to
    {"raw": <text>} rather than being dropped -- some playbooks come back as
    prose, not JSON."""
    mode = "COMPETITOR" if str(mode).upper() == "COMPETITOR" else "OWN"
    parsed, err = _execute(_WORKFLOW_PLAYBOOK, {
        "email": email,
        "id": run_id,
        "mode": mode,
        "stream": True,
        "selectedOutputs": ["playbookagent.content"],
        "includeThinking": False,
        "includeToolCalls": False,
    }, _TIMEOUT_LONG)
    if err is not None:
        return None
    return _parse_playbook_content(extract_output(parsed or {}))


def probe(company_name: str = "Microsoft") -> dict:
    """Prove the Arena integration end to end and report exactly where it
    fails, in the shape app.py's _apollo_selftest established.

    Probes with a company every LinkedIn dataset contains, so a zero-result
    verdict is unambiguous: if this returns companies, the key, the workflow ID
    and the response shape are all good, and an empty search on the page is a
    genuine absence of data rather than a broken integration. Free -- the
    search workflow is not one of the billed multi-agent runs."""
    key = _api_key()
    out = {"configured": bool(key), "key_len": len(key), "workflow": _WORKFLOW_SEARCH,
           "base_url": ARENA_BASE, "probe": company_name, "elapsed_ms": 0,
           "http_status": None, "attempts": 0, "companies": 0, "sample": [],
           "source": "", "error_kind": "", "error": "", "detail": ""}
    if not key:
        out["error_kind"] = ERR_NOT_CONFIGURED
        out["error"] = describe_error(_err(ERR_NOT_CONFIGURED))
        return out
    try:
        result = search_companies_result(company_name)
    except Exception as e:  # defensive: probe must never 500 the admin page
        out["error_kind"] = "exception"
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        return out
    err = result.get("error")
    out["elapsed_ms"] = result.get("elapsed_ms", 0)
    out["source"] = result.get("source") or ""
    out["companies"] = len(result.get("companies") or [])
    out["sample"] = [c.get("name") for c in (result.get("companies") or [])[:5]]
    out["attempts"] = (err or {}).get("attempts", 1)
    if err:
        out["http_status"] = err.get("status")
        out["error_kind"] = err.get("kind") or ""
        out["error"] = describe_error(err)
        out["detail"] = err.get("detail") or ""
    elif not out["companies"]:
        out["error"] = ("The call succeeded but even %s returned no companies, "
                        "which points at the provider's own data or plan limits "
                        "rather than at this platform." % company_name)
    return out
