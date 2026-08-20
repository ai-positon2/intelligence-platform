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

_TIMEOUT_SEARCH = 30
_TIMEOUT_LONG = 300  # own-brand/competitor analysis and playbook generation


def _api_key() -> str:
    return os.environ.get("ARENA_API_KEY", "")


def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


def _execute(workflow_id: str, payload: dict, timeout: int) -> dict | None:
    """POST one workflow, return its raw parsed response dict, or None if
    ARENA_API_KEY isn't configured or the call fails outright. Never raises --
    every caller degrades to "not available" rather than a 500, matching
    tracker/apollo_client.py's philosophy."""
    key = _api_key()
    if not key:
        logger.info("arena_client: ARENA_API_KEY not configured, skipping call")
        return None
    url = f"{ARENA_BASE}/{workflow_id}/execute"
    headers = {"Content-Type": "application/json", "X-API-Key": key}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("arena_client: workflow %s failed: %s", workflow_id, e)
        return None
    try:
        return _parse_response_text(resp.text)
    except Exception as e:
        logger.warning("arena_client: workflow %s response parse failed: %s", workflow_id, e)
        return None


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
            continue
        if not _is_dict(evt):
            continue
        # The streaming API's final event carries the complete, structured
        # output: {event: 'final', data: {output: {<blockId>: {...}}}}. This
        # multi-agent workflow can emit more than one "final" event -- one per
        # agent/node as it finishes, not one for the whole run -- so this
        # merges every final event's output rather than letting the last one
        # overwrite the others (which previously discarded every namespace
        # except whichever agent's final event happened to arrive last).
        if evt.get("event") == "final" and _is_dict(evt.get("data")) and _is_dict(evt["data"].get("output")):
            final_output.update(evt["data"]["output"])
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
    return {"output": output}


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


def extract_companies(parsed: dict) -> list[dict]:
    output = extract_output(parsed)
    candidates = [output.get("companies"), output.get("companylistingagent.companies")]
    listing = output.get("companylistingagent")
    if _is_dict(listing):
        candidates.append(listing.get("companies"))

    raw: list = []
    for c in candidates:
        if isinstance(c, list) and c:
            raw = c
            break
    if not raw:
        for v in output.values():
            if isinstance(v, list) and any(_is_dict(x) and isinstance(x.get("name"), str) for x in v):
                raw = v
                break
            if _is_dict(v) and isinstance(v.get("companies"), list) and v["companies"]:
                raw = v["companies"]
                break

    return [c for c in (_to_company(r) for r in raw if _is_dict(r)) if c is not None]


def search_companies(company_name: str) -> list[dict]:
    """Best-effort company search. [] if ARENA_API_KEY isn't configured or the
    call fails -- never raises."""
    parsed = _execute(_WORKFLOW_SEARCH, {
        "companyName": company_name,
        "stream": False,
        "selectedOutputs": ["companylistingagent.companies"],
    }, _TIMEOUT_SEARCH)
    if parsed is None:
        return []
    return extract_companies(parsed)


def run_analysis(company_name: str, company_id: str, email: str, run_type: str,
                 parent_run_id: str = "") -> dict | None:
    """Run the multi-agent LinkedIn strategy analysis. Returns the namespaced
    output dict, or None if ARENA_API_KEY isn't configured or the call fails.
    This is the slow (multi-minute) call -- callers run it off the request
    thread; see app.py's linkedin_playbook_studio analyze route."""
    is_competitor = run_type == "COMPETITOR"
    parsed = _execute(_WORKFLOW_ANALYSIS, {
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
    if parsed is None:
        return None
    output = normalize_analysis_output(extract_output(parsed))
    _log_missing_namespaces(output)
    return output


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
    parsed = _execute(_WORKFLOW_PLAYBOOK, {
        "email": email,
        "id": run_id,
        "mode": mode,
        "stream": True,
        "selectedOutputs": ["playbookagent.content"],
        "includeThinking": False,
        "includeToolCalls": False,
    }, _TIMEOUT_LONG)
    if parsed is None:
        return None
    return _parse_playbook_content(extract_output(parsed))
