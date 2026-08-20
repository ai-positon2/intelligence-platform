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


def test_sse_final_event_wins_over_intermediate_chunks():
    body = "\n".join([
        'data: {"blockId":"b1","chunk":"partial"}',
        'data: {"event":"final","data":{"output":{"strategy":"the real answer"}}}',
    ])
    parsed = ac._parse_response_text(body)
    assert parsed == {"output": {"strategy": "the real answer"}}


def test_sse_empty_final_event_falls_back_to_accumulated_chunks():
    body = "\n".join([
        'data: {"blockId":"b1","chunk":"{\\"headline\\": "}',
        'data: {"blockId":"b1","chunk":"\\"hello\\"}"}',
        'data: {"event":"final","data":{"output":{}}}',
    ])
    parsed = ac._parse_response_text(body)
    assert parsed == {"output": {"b1": {"headline": "hello"}}}


def test_sse_chunk_that_never_parses_as_json_is_kept_as_raw_text():
    body = 'data: {"blockId":"b1","chunk":"just some prose, not JSON"}'
    parsed = ac._parse_response_text(body)
    assert parsed == {"output": {"b1": "just some prose, not JSON"}}


def test_sse_done_marker_lines_are_ignored():
    body = "\n".join([
        'data: {"event":"final","data":{"output":{"x":1}}}',
        "data: [DONE]",
    ])
    parsed = ac._parse_response_text(body)
    assert parsed == {"output": {"x": 1}}


def test_malformed_sse_lines_are_skipped_not_raised():
    body = "\n".join([
        "data: not json at all {{{",
        'data: {"event":"final","data":{"output":{"x":1}}}',
    ])
    parsed = ac._parse_response_text(body)
    assert parsed == {"output": {"x": 1}}


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


def test_a_block_matching_no_known_namespace_is_left_as_is():
    flat = {"getcompanyprofile": {"name": "Acme", "website": "acme.com"}}
    # getcompanyprofile isn't one of the scored namespaces (it never collides
    # with the five agents' field names), so it passes through untouched.
    assert ac.normalize_analysis_output(flat) == flat


def test_ties_are_broken_by_which_namespace_scores_first():
    # "summary" only appears in messagingagent's key set, so this block
    # unambiguously belongs there even though it's the only shared field.
    flat = {"block-1": {"summary": "text"}}
    assert ac.normalize_analysis_output(flat) == {"messagingagent.summary": "text"}


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
