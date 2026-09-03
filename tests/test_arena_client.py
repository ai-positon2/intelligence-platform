"""Pure-function tests for tracker/arena_client.py's response parsing --
whole-body JSON, SSE-style `data:` line streams, the flat-output-to-namespace
guessing, and playbook content extraction. No network calls: these are the
exact edge cases the vendor's API is documented (via a prior tool's source,
since this account's own docs weren't available) to actually return.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import arena_client as ac  # noqa: E402


# ── Whole-body JSON vs. SSE parsing ──────────────────────────────────────────

def test_a_plain_json_object_body_parses_directly():
    parsed = ac._parse_response_text('{"output": {"companies": []}}')
    assert parsed == {"output": {"companies": []}}


def test_sse_final_event_output_is_layered_over_accumulated_chunks():
    # The final event's own keys win, but data accumulated from other blocks
    # along the way isn't discarded just because a final event also arrived --
    # a real multi-agent run's final event for one agent must not erase
    # another agent's already-streamed data (see the merge test below).
    body = "\n".join([
        'data: {"blockId":"b1","chunk":"partial"}',
        'data: {"event":"final","data":{"output":{"strategy":"the real answer"}}}',
    ])
    parsed = ac._parse_response_text(body)
    assert parsed["output"] == {"b1": "partial", "strategy": "the real answer"}


def test_multiple_final_events_are_merged_not_overwritten():
    # This multi-agent workflow emits one "final" event per agent as it
    # completes, not a single final event for the whole run. Overwriting
    # (instead of merging) meant only the LAST agent to finish survived --
    # every earlier agent's namespace silently vanished from the response,
    # which is exactly the "some report tabs are empty" symptom this fixes.
    body = "\n".join([
        'data: {"event":"final","data":{"output":{"strategyagent.strategy":"x"}}}',
        'data: {"event":"final","data":{"output":{"getcompanyprofile.name":"Acme"}}}',
    ])
    parsed = ac._parse_response_text(body)
    assert parsed["output"] == {"strategyagent.strategy": "x", "getcompanyprofile.name": "Acme"}


def test_sse_empty_final_event_falls_back_to_accumulated_chunks():
    body = "\n".join([
        'data: {"blockId":"b1","chunk":"{\\"headline\\": "}',
        'data: {"blockId":"b1","chunk":"\\"hello\\"}"}',
        'data: {"event":"final","data":{"output":{}}}',
    ])
    parsed = ac._parse_response_text(body)
    assert parsed["output"] == {"b1": {"headline": "hello"}}


def test_sse_chunk_that_never_parses_as_json_is_kept_as_raw_text():
    body = 'data: {"blockId":"b1","chunk":"just some prose, not JSON"}'
    parsed = ac._parse_response_text(body)
    assert parsed["output"] == {"b1": "just some prose, not JSON"}


def test_sse_done_marker_lines_are_ignored():
    body = "\n".join([
        'data: {"event":"final","data":{"output":{"x":1}}}',
        "data: [DONE]",
    ])
    parsed = ac._parse_response_text(body)
    assert parsed["output"] == {"x": 1}


def test_malformed_sse_lines_are_skipped_not_raised():
    body = "\n".join([
        "data: not json at all {{{",
        'data: {"event":"final","data":{"output":{"x":1}}}',
    ])
    parsed = ac._parse_response_text(body)
    assert parsed["output"] == {"x": 1}
    assert parsed["_sseDebug"]["unparsedLineCount"] == 1


# ── SSE diagnostics (_sseDebug) ──────────────────────────────────────────

def test_sse_debug_reports_zero_events_for_a_namespace_that_never_arrived():
    # This is the concrete tool for telling "the vendor's stream never sent
    # this agent's data" apart from "our merge dropped it" without needing
    # production log access -- see run_analysis, which carries this into the
    # stored output under output["_sseDebug"] for the existing admin raw-data
    # view to surface on the next real run.
    body = 'data: {"event":"final","data":{"output":{"strategyagent.strategy":"x"}}}'
    parsed = ac._parse_response_text(body)
    debug = parsed["_sseDebug"]
    assert debug["eventTypeCounts"] == {"final": 1}
    assert debug["finalEventOutputKeys"] == ["strategyagent.strategy"]
    assert debug["chunkBlockIds"] == []
    assert "creativeinsightagent.imageryTypes" not in debug["finalEventOutputKeys"]


def test_run_analysis_attaches_sse_debug_to_the_stored_output(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")

    class _Resp:
        text = 'data: {"event":"final","data":{"output":{"strategyagent.strategy":"x"}}}'

        def raise_for_status(self):
            pass

    monkeypatch.setattr(ac.requests, "post", lambda *a, **kw: _Resp())
    output = ac.run_analysis("Acme", "1", "a@position2.com", "OWN")
    assert output["strategyagent.strategy"] == "x"
    assert output["_sseDebug"]["eventTypeCounts"] == {"final": 1}


def test_a_whole_body_json_response_has_no_sse_debug():
    # Only the SSE code path can say anything about what arrived on the wire;
    # a plain JSON body has no such distinction to report.
    parsed = ac._parse_response_text('{"output": {"a": 1}}')
    assert "_sseDebug" not in parsed


# ── extract_output ────────────────────────────────────────────────────────

def test_extract_output_reads_top_level_output():
    assert ac.extract_output({"output": {"a": 1}}) == {"a": 1}


def test_extract_output_reads_nested_result_output():
    assert ac.extract_output({"result": {"output": {"a": 1}}}) == {"a": 1}


def test_extract_output_falls_back_to_the_whole_parsed_dict():
    assert ac.extract_output({"a": 1}) == {"a": 1}


# ── normalize_analysis_output: namespace guessing ────────────────────────────

def test_already_namespaced_output_is_returned_unchanged():
    output = {"strategyagent.strategy": "x"}
    assert ac.normalize_analysis_output(output) == output


def test_a_flat_block_is_namespaced_by_its_field_names():
    flat = {"block-uuid-1": {"strategy": "x", "personas": ["a"]}}
    normalized = ac.normalize_analysis_output(flat)
    assert normalized == {"strategyagent.strategy": "x", "strategyagent.personas": ["a"]}


def test_a_getcompanyprofile_block_is_namespaced():
    # getcompanyprofile and getcompanypost are this app's own two extra
    # namespaces beyond the five analysis agents ported from a prior tool --
    # they used to be absent from the scored groups entirely, so a flat
    # response for either one passed through unmatched and never reached the
    # UI as `getcompanyprofile.name` etc., reading as "no data returned" even
    # when the vendor's profile fetch actually succeeded.
    flat = {"getcompanyprofile": {"name": "Acme", "website": "acme.com"}}
    assert ac.normalize_analysis_output(flat) == {
        "getcompanyprofile.name": "Acme", "getcompanyprofile.website": "acme.com",
    }


def test_a_block_matching_no_known_namespace_is_left_as_is():
    flat = {"block-1": {"totally": "unrecognized", "fields": "here"}}
    assert ac.normalize_analysis_output(flat) == flat


def test_ties_are_broken_by_which_namespace_scores_first():
    # "summary" only appears in messagingagent's key set, so this block
    # unambiguously belongs there even though it's the only shared field.
    flat = {"block-1": {"summary": "text"}}
    assert ac.normalize_analysis_output(flat) == {"messagingagent.summary": "text"}


def test_normalize_handles_a_mixed_response_of_dotted_and_flat_keys():
    # A response where most agents already stream back namespaced keys but
    # one lands as a flat nested block (e.g. under an opaque block id) used
    # to skip normalization ENTIRELY the moment any key had a dot -- so that
    # one flat block's data was silently dropped instead of namespaced. Each
    # key must be judged on its own, not the response as a whole.
    mixed = {
        "strategyagent.strategy": "already dotted",
        "block-uuid-9": {"imageryTypes": ["carousel"], "textStyle": "bold"},
    }
    normalized = ac.normalize_analysis_output(mixed)
    assert normalized == {
        "strategyagent.strategy": "already dotted",
        "creativeinsightagent.imageryTypes": ["carousel"],
        "creativeinsightagent.textStyle": "bold",
    }


# ── Missing-namespace diagnostics ────────────────────────────────────────

def test_log_missing_namespaces_warns_when_one_is_absent(caplog):
    with caplog.at_level("WARNING"):
        ac._log_missing_namespaces({"strategyagent.strategy": "x"})
    assert any("missing namespace" in r.message for r in caplog.records)


def test_log_missing_namespaces_silent_when_everything_present(caplog):
    output = {f"{ns}.x": 1 for ns in ac._EXPECTED_NAMESPACES}
    with caplog.at_level("WARNING"):
        ac._log_missing_namespaces(output)
    assert not any("missing namespace" in r.message for r in caplog.records)


def test_log_missing_namespaces_never_raises_on_bad_input():
    ac._log_missing_namespaces(None)  # type: ignore[arg-type]


# ── extract_companies ────────────────────────────────────────────────────

def test_extract_companies_from_the_documented_shape():
    parsed = {"output": {"companylistingagent.companies": [
        {"id": "1", "name": "Acme", "followers_count": 1000},
    ]}}
    companies = ac.extract_companies(parsed)
    assert len(companies) == 1
    assert companies[0]["name"] == "Acme"
    assert companies[0]["followers_count"] == 1000.0


def test_extract_companies_from_a_nested_companylistingagent_block():
    parsed = {"output": {"companylistingagent": {"companies": [{"id": "1", "name": "Acme"}]}}}
    companies = ac.extract_companies(parsed)
    assert companies[0]["name"] == "Acme"


def test_extract_companies_skips_rows_with_no_name():
    parsed = {"output": {"companies": [{"id": "1"}, {"id": "2", "name": "Acme"}]}}
    companies = ac.extract_companies(parsed)
    assert len(companies) == 1 and companies[0]["name"] == "Acme"


def test_extract_companies_returns_empty_list_for_no_match():
    assert ac.extract_companies({"output": {}}) == []


# ── Playbook content parsing ──────────────────────────────────────────────

def test_playbook_content_as_a_plain_dict():
    output = {"content": {"headline": "Do this"}}
    assert ac._parse_playbook_content(output) == {"headline": "Do this"}


def test_playbook_content_as_a_json_string():
    output = {"content": json.dumps({"headline": "Do this"})}
    assert ac._parse_playbook_content(output) == {"headline": "Do this"}


def test_playbook_content_fenced_in_a_code_block():
    output = {"content": "```json\n" + json.dumps({"headline": "Do this"}) + "\n```"}
    assert ac._parse_playbook_content(output) == {"headline": "Do this"}


def test_playbook_content_under_the_dotted_key():
    output = {"playbookagent.content": {"headline": "Do this"}}
    assert ac._parse_playbook_content(output) == {"headline": "Do this"}


def test_playbook_content_nested_under_the_agent_block():
    output = {"playbookagent": {"content": {"headline": "Do this"}}}
    assert ac._parse_playbook_content(output) == {"headline": "Do this"}


def test_playbook_content_that_is_not_json_becomes_raw_text():
    output = {"content": "Just do these three things."}
    assert ac._parse_playbook_content(output) == {"raw": "Just do these three things."}


def test_playbook_content_from_an_opaque_block_id_fallback():
    output = {"block-uuid-1": {"content": "Prose playbook text."}}
    assert ac._parse_playbook_content(output) == {"raw": "Prose playbook text."}


def test_playbook_content_missing_entirely_returns_empty_dict():
    assert ac._parse_playbook_content({}) == {}


# ── Graceful degradation without a configured key ────────────────────────

def test_search_companies_returns_empty_list_without_a_key(monkeypatch):
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    assert ac.search_companies("Acme") == []


def test_run_analysis_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    assert ac.run_analysis("Acme", "1", "a@position2.com", "OWN") is None


def test_run_playbook_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    assert ac.run_playbook("a@position2.com", "1", "OWN") is None


def test_execute_never_raises_on_a_network_failure(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")

    def _boom(*a, **kw):
        raise ac.requests.RequestException("connection refused")

    monkeypatch.setattr(ac.requests, "post", _boom)
    assert ac.search_companies("Acme") == []


# ── Failure kinds: every one of these used to look like "no results" ──────

class _FakeResp:
    def __init__(self, status=200, text="{}"):
        self.status_code = status
        self.text = text


def _post_returning(*responses):
    """A requests.post stand-in that yields the given responses/exceptions in
    order, so retry behavior is observable."""
    calls = []

    def _post(*a, **kw):
        calls.append(kw.get("json"))
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    _post.calls = calls
    return _post


def test_a_missing_key_is_reported_as_not_configured(monkeypatch):
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    result = ac.search_companies_result("Acme")
    assert result["companies"] == []
    assert result["error"]["kind"] == ac.ERR_NOT_CONFIGURED
    assert "ARENA_API_KEY" in ac.describe_error(result["error"])


def test_a_rejected_key_is_reported_as_an_http_error_with_its_status(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post",
                        _post_returning(_FakeResp(401, '{"message":"invalid api key"}')))
    result = ac.search_companies_result("Acme")
    assert result["error"]["kind"] == ac.ERR_HTTP
    assert result["error"]["status"] == 401
    # The vendor's own words survive into the operator-facing detail.
    assert "invalid api key" in result["error"]["detail"]
    assert "rejected our API key" in ac.describe_error(result["error"])


def test_a_rejected_key_is_not_retried(monkeypatch):
    """Retrying a 401 only delays telling the operator the key is dead."""
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    post = _post_returning(_FakeResp(401, "nope"))
    monkeypatch.setattr(ac.requests, "post", post)
    ac.search_companies_result("Acme")
    assert len(post.calls) == 1


def test_a_deleted_workflow_reports_the_id_changed(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post", _post_returning(_FakeResp(404, "not found")))
    result = ac.search_companies_result("Acme")
    assert result["error"]["status"] == 404
    assert "no longer recognises this workflow" in ac.describe_error(result["error"])


def test_a_transient_server_error_is_retried_and_can_then_succeed(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    post = _post_returning(
        _FakeResp(503, "unavailable"),
        _FakeResp(200, '{"output":{"companies":[{"id":"1","name":"Acme"}]}}'),
    )
    monkeypatch.setattr(ac.requests, "post", post)
    result = ac.search_companies_result("Acme")
    assert len(post.calls) == 2
    assert result["error"] is None
    assert [c["name"] for c in result["companies"]] == ["Acme"]


def test_a_rate_limit_is_retried_up_to_the_attempt_cap(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    post = _post_returning(_FakeResp(429, "slow down"))
    monkeypatch.setattr(ac.requests, "post", post)
    result = ac.search_companies_result("Acme")
    assert len(post.calls) == ac._SEARCH_ATTEMPTS
    assert result["error"]["attempts"] == ac._SEARCH_ATTEMPTS
    assert "rate-limiting" in ac.describe_error(result["error"])


def test_a_timeout_is_reported_as_a_timeout_not_as_zero_results(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    monkeypatch.setattr(ac.requests, "post",
                        _post_returning(ac.requests.Timeout("too slow")))
    result = ac.search_companies_result("Acme")
    assert result["error"]["kind"] == ac.ERR_TIMEOUT
    assert ac.is_retryable(result["error"])


def test_the_retry_budget_stops_attempts_that_cannot_finish(monkeypatch):
    """A retry that would run past the budget (and so past gunicorn's worker
    timeout) is skipped rather than started."""
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    post = _post_returning(ac.requests.Timeout("too slow"))
    monkeypatch.setattr(ac.requests, "post", post)
    parsed, err = ac._execute("wf", {}, timeout=100, attempts=3, budget=60.0)
    assert parsed is None
    assert len(post.calls) == 1
    assert err["kind"] == ac.ERR_TIMEOUT


def test_a_200_with_no_company_list_is_a_shape_error_not_an_empty_search(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post",
                        _post_returning(_FakeResp(200, '{"output":{"somethingElse":42}}')))
    result = ac.search_companies_result("Acme")
    assert result["companies"] == []
    assert result["error"]["kind"] == ac.ERR_SHAPE
    assert "somethingElse" in result["error"]["detail"]


def test_a_200_with_an_empty_company_list_is_a_genuine_zero_result(monkeypatch):
    """The one case where the page may say "nothing matched": the vendor
    itself looked and returned an empty list."""
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post",
                        _post_returning(_FakeResp(200, '{"output":{"companies":[]}}')))
    result = ac.search_companies_result("Nonexistent Ltd")
    assert result["companies"] == []
    assert result["error"] is None


def test_find_company_rows_separates_missing_from_empty():
    assert ac.find_company_rows({"companies": []}) == ([], "companies")
    assert ac.find_company_rows({"nothing": 1}) == (None, "")


def test_an_unreadable_body_never_escapes_as_an_exception(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")

    class _Exploding:
        status_code = 200

        @property
        def text(self):
            raise ValueError("stream closed")

    monkeypatch.setattr(ac.requests, "post", _post_returning(_Exploding()))
    result = ac.search_companies_result("Acme")
    assert result["error"]["kind"] == ac.ERR_UNPARSABLE


def test_is_retryable_says_no_to_a_dead_key_and_yes_to_a_blip():
    assert not ac.is_retryable({"kind": ac.ERR_HTTP, "status": 401})
    assert not ac.is_retryable({"kind": ac.ERR_NOT_CONFIGURED})
    assert not ac.is_retryable(None)
    assert ac.is_retryable({"kind": ac.ERR_HTTP, "status": 502})
    assert ac.is_retryable({"kind": ac.ERR_NETWORK})


def test_search_companies_still_returns_a_bare_list(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post",
                        _post_returning(_FakeResp(200, '{"output":{"companies":[{"id":"1","name":"Acme"}]}}')))
    assert [c["name"] for c in ac.search_companies("Acme")] == ["Acme"]


def test_run_analysis_result_carries_the_failure_reason(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post", _post_returning(_FakeResp(402, "no credit")))
    attempt = ac.run_analysis_result("Acme", "1", "a@position2.com", "OWN")
    assert attempt["output"] is None
    assert attempt["error"]["status"] == 402
    assert "out of credit" in ac.describe_error(attempt["error"])


def test_a_billed_analysis_run_is_never_retried(monkeypatch):
    """A multi-minute billed run gets one attempt: a retry would double the
    vendor spend on a failure a second attempt does not fix."""
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    post = _post_returning(_FakeResp(503, "unavailable"))
    monkeypatch.setattr(ac.requests, "post", post)
    ac.run_analysis_result("Acme", "1", "a@position2.com", "OWN")
    assert len(post.calls) == 1


# ── probe (the admin self-test) ───────────────────────────────────────────

def test_probe_reports_a_working_integration(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post",
                        _post_returning(_FakeResp(200, '{"output":{"companies":[{"id":"1","name":"Microsoft"}]}}')))
    out = ac.probe()
    assert out["configured"] and out["companies"] == 1
    assert out["sample"] == ["Microsoft"]
    assert out["error"] == ""


def test_probe_names_the_http_status_when_the_key_is_rejected(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post", _post_returning(_FakeResp(403, "forbidden")))
    out = ac.probe()
    assert out["http_status"] == 403
    assert out["error_kind"] == ac.ERR_HTTP
    assert "forbidden" in out["detail"]


def test_probe_without_a_key_says_so_without_calling_out(monkeypatch):
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(ac.requests, "post", lambda *a, **k: called.append(1))
    out = ac.probe()
    assert out["configured"] is False and not called


def test_probe_flags_a_successful_call_that_found_nothing(monkeypatch):
    """If even the probe company comes back empty, the provider's data or plan
    is the suspect, not this platform."""
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post",
                        _post_returning(_FakeResp(200, '{"output":{"companies":[]}}')))
    out = ac.probe()
    assert out["companies"] == 0
    assert "no companies" in out["error"]


def test_probe_never_raises(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac, "search_companies_result", lambda q: (_ for _ in ()).throw(RuntimeError("boom")))
    out = ac.probe()
    assert out["error_kind"] == "exception" and "boom" in out["error"]


# ── The vendor's own workspace being unconfigured ─────────────────────────
# Observed in production: the search workflow answers HTTP 500 with
# {"success":false,"error":"LinkedIn Company Search is missing required
# fields: LinkedIn Account"} once the Arena workspace's LinkedIn connection
# lapses. A 500 would normally be retried, but this one cannot succeed until
# somebody reconnects that account, and it is the actual reason this agent has
# twice looked broken.

_VENDOR_UNCONFIGURED = ('{"success":false,"error":"LinkedIn Company Search is '
                        'missing required fields: LinkedIn Account"}')


def test_a_vendor_workspace_gap_is_named_not_called_a_server_error(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.requests, "post",
                        _post_returning(_FakeResp(500, _VENDOR_UNCONFIGURED)))
    result = ac.search_companies_result("Microsoft")
    message = ac.describe_error(result["error"])
    assert result["error"]["config"] is True
    assert "LinkedIn Account" in message
    assert "Arena workspace" in message


def test_a_vendor_workspace_gap_is_not_retried_despite_being_a_500(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    post = _post_returning(_FakeResp(500, _VENDOR_UNCONFIGURED))
    monkeypatch.setattr(ac.requests, "post", post)
    result = ac.search_companies_result("Microsoft")
    assert len(post.calls) == 1
    assert ac.is_retryable(result["error"]) is False


def test_a_plain_500_is_still_treated_as_a_retryable_blip(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "test-key")
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    post = _post_returning(_FakeResp(500, "internal error"))
    monkeypatch.setattr(ac.requests, "post", post)
    result = ac.search_companies_result("Microsoft")
    assert len(post.calls) == ac._SEARCH_ATTEMPTS
    assert result["error"]["config"] is False
    assert ac.is_retryable(result["error"]) is True
