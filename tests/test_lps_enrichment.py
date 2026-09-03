"""tracker/lps_enrichment.py: the Claude synthesis layer that runs once per
completed LinkedIn Strategy Researcher analysis, reading everything the
vendor's five agents returned and writing one point of view across all of it.

The one architectural rule under test throughout: anything that could go
wrong (no ANTHROPIC_API_KEY, an empty source, a network error, a malformed
reply) must degrade to None, never raise -- app.py's background analysis job
treats this as strictly best-effort, and a run has to complete and save
without it exactly as it would if this module didn't exist. Also tested: the
"never invent" contract is enforced structurally by requiring both headline
and synthesis to be present non-empty strings, and _sseDebug is excluded from
what gets sent to the model.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import lps_enrichment  # noqa: E402


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessages:
    def __init__(self, response_text=None, exc=None, capture=None, stop_reason=None, sequence=None):
        self._text = response_text
        self._exc = exc
        self._capture = capture
        self._stop_reason = stop_reason
        # When set, a list of (text, stop_reason) pairs consumed one per call
        # -- lets a test simulate "truncated, then a good reply on retry".
        self._sequence = list(sequence) if sequence is not None else None

    def create(self, **kwargs):
        if self._capture is not None:
            self._capture.append(kwargs)
        if self._exc:
            raise self._exc
        if self._sequence is not None:
            text, stop_reason = self._sequence.pop(0)
        else:
            text, stop_reason = self._text, self._stop_reason
        return type("FakeResponse", (), {"content": [_FakeBlock(text)], "stop_reason": stop_reason})()


class _FakeAnthropicClient:
    def __init__(self, response_text=None, exc=None, capture=None, stop_reason=None, sequence=None):
        self.messages = _FakeMessages(response_text, exc, capture, stop_reason, sequence)


_GOOD_REPLY = json.dumps({
    "headline": "No organic LinkedIn activity was found to analyze.",
    "synthesis": "Paragraph one.\n\nParagraph two.",
    "topActions": ["Start posting consistently.", "Publish product education content."],
    "coverage": "Profile and competitive data was available; posts and engagement were not.",
})

_SAMPLE_OUTPUT = {
    "getcompanyprofile.name": "Boat",
    "getcompanyprofile.description": "a lifestyle brand",
    "competitiveagent.scorecardOverall": 1,
    "_sseDebug": {"eventTypeCounts": {"final": 1}},
}


# ── _anthropic: the client factory degrades like arena_client's own key check ──

def test_anthropic_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert lps_enrichment._anthropic() is None


def test_anthropic_returns_a_real_client_with_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    client = lps_enrichment._anthropic()
    assert client is not None
    assert type(client).__name__ == "Anthropic"


# ── enrich_run: the degrade-to-None contract ────────────────────────────────

def test_enrich_run_returns_none_without_a_configured_key(monkeypatch):
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: None)
    assert lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN") is None


def test_enrich_run_returns_none_for_an_empty_source(monkeypatch):
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    assert lps_enrichment.enrich_run({}, "Boat", "OWN") is None


def test_enrich_run_returns_none_when_only_debug_keys_are_present(monkeypatch):
    """_sseDebug alone is bookkeeping, not real vendor output -- there is
    nothing here worth synthesizing, so this must not spend a Claude call."""
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    assert lps_enrichment.enrich_run({"_sseDebug": {}}, "Boat", "OWN") is None


def test_enrich_run_parses_a_well_formed_reply(monkeypatch):
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    out = lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert out["headline"] == "No organic LinkedIn activity was found to analyze."
    assert "Paragraph one." in out["synthesis"]
    assert out["topActions"] == ["Start posting consistently.", "Publish product education content."]
    assert out["coverage"].startswith("Profile and competitive")


def test_enrich_run_excludes_debug_keys_from_the_claude_payload(monkeypatch):
    """_sseDebug is internal wire-parsing bookkeeping, not something about the
    company -- sending it to the model would waste tokens and could read as
    source material to synthesize a "coverage" claim from."""
    capture = []
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY, capture=capture)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert len(capture) == 1
    sent = capture[0]["messages"][0]["content"]
    assert "_sseDebug" not in sent
    assert "eventTypeCounts" not in sent
    assert "Boat" in sent


def test_enrich_run_returns_none_on_malformed_json(monkeypatch):
    fake = _FakeAnthropicClient(response_text="not json at all")
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    assert lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN") is None


def test_enrich_run_returns_none_when_the_reply_is_a_json_array_not_object(monkeypatch):
    fake = _FakeAnthropicClient(response_text=json.dumps(["headline", "synthesis"]))
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    assert lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN") is None


def test_enrich_run_returns_none_when_headline_is_missing(monkeypatch):
    reply = json.dumps({"synthesis": "Some text."})
    fake = _FakeAnthropicClient(response_text=reply)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    assert lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN") is None


def test_enrich_run_returns_none_when_synthesis_is_blank(monkeypatch):
    reply = json.dumps({"headline": "A headline.", "synthesis": "   "})
    fake = _FakeAnthropicClient(response_text=reply)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    assert lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN") is None


def test_enrich_run_returns_none_on_an_api_exception(monkeypatch):
    fake = _FakeAnthropicClient(exc=RuntimeError("boom"))
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    assert lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN") is None


def test_enrich_run_truncates_top_actions_to_five(monkeypatch):
    reply = json.dumps({
        "headline": "H", "synthesis": "S",
        "topActions": ["one", "two", "three", "four", "five", "six", "seven"],
    })
    fake = _FakeAnthropicClient(response_text=reply)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    out = lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert out["topActions"] == ["one", "two", "three", "four", "five"]


def test_enrich_run_filters_non_string_top_actions(monkeypatch):
    reply = json.dumps({"headline": "H", "synthesis": "S", "topActions": ["ok", None, {}, "  ", "also ok"]})
    fake = _FakeAnthropicClient(response_text=reply)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    out = lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert out["topActions"] == ["ok", "also ok"]


def test_enrich_run_omits_coverage_key_when_not_provided(monkeypatch):
    reply = json.dumps({"headline": "H", "synthesis": "S"})
    fake = _FakeAnthropicClient(response_text=reply)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    out = lps_enrichment.enrich_run(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert "coverage" not in out
    assert out["topActions"] == []


# ── enrich_run_result: the same contract, but the caller also learns why ────
# This is the path app.py's on-demand "Generate AI insights" job uses -- the
# bug this module exists to fix (2026-08-21) was every one of these outcomes
# collapsing to a bare None, indistinguishable from each other and from "the
# call is just slow", so these tests each pin one distinct kind.

def test_enrich_run_result_reports_empty_source(monkeypatch):
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result, err = lps_enrichment.enrich_run_result({}, "Boat", "OWN")
    assert result is None
    assert err["kind"] == lps_enrichment.ERR_EMPTY_SOURCE


def test_enrich_run_result_reports_api_exceptions_with_status(monkeypatch):
    exc = RuntimeError("rejected")
    exc.status_code = 401
    fake = _FakeAnthropicClient(exc=exc)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result, err = lps_enrichment.enrich_run_result(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert result is None
    assert err["kind"] == lps_enrichment.ERR_API
    assert err["status"] == 401
    assert "rejected" in err["detail"]


def test_enrich_run_result_reports_malformed_json_as_unparsable(monkeypatch):
    fake = _FakeAnthropicClient(response_text="not json at all")
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result, err = lps_enrichment.enrich_run_result(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert result is None
    assert err["kind"] == lps_enrichment.ERR_UNPARSABLE


def test_enrich_run_result_reports_missing_headline_as_shape(monkeypatch):
    reply = json.dumps({"synthesis": "Some text."})
    fake = _FakeAnthropicClient(response_text=reply)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result, err = lps_enrichment.enrich_run_result(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert result is None
    assert err["kind"] == lps_enrichment.ERR_SHAPE


def test_enrich_run_result_returns_no_error_on_success(monkeypatch):
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result, err = lps_enrichment.enrich_run_result(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert err is None
    assert result["headline"]


# ── truncation retry: the actual root cause found live on 2026-08-21 --
# a real run's compact-JSON reply routinely ran past the old 2000-token cap
# and the model's own pretty-printing habit made it worse, producing an
# unterminated JSON string that quietly became None. ─────────────────────────

def test_a_truncated_reply_is_retried_with_a_bigger_budget(monkeypatch):
    capture = []
    fake = _FakeAnthropicClient(capture=capture, sequence=[
        ('{"headline": "cut off mid-strin', "max_tokens"),
        (_GOOD_REPLY, "end_turn"),
    ])
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result, err = lps_enrichment.enrich_run_result(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert err is None
    assert result["headline"] == "No organic LinkedIn activity was found to analyze."
    assert len(capture) == 2
    assert capture[0]["max_tokens"] == lps_enrichment._MAX_TOKENS
    assert capture[1]["max_tokens"] == lps_enrichment._RETRY_MAX_TOKENS


def test_a_reply_truncated_twice_reports_the_truncated_kind(monkeypatch):
    fake = _FakeAnthropicClient(stop_reason="max_tokens", response_text='{"headline": "still cut off')
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result, err = lps_enrichment.enrich_run_result(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert result is None
    assert err["kind"] == lps_enrichment.ERR_TRUNCATED


def test_a_non_truncated_reply_is_not_retried(monkeypatch):
    """A normal reply (stop_reason != 'max_tokens') must cost exactly one
    call -- retrying every reply "just in case" would double every real
    run's Claude spend for nothing."""
    capture = []
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY, stop_reason="end_turn", capture=capture)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    lps_enrichment.enrich_run_result(_SAMPLE_OUTPUT, "Boat", "OWN")
    assert len(capture) == 1


# ── describe_error / is_retryable: what a person and the UI see ─────────────

def test_describe_error_names_a_rejected_key():
    err = {"kind": lps_enrichment.ERR_API, "status": 401}
    assert "renewed" in lps_enrichment.describe_error(err)


def test_describe_error_names_a_rate_limit():
    err = {"kind": lps_enrichment.ERR_API, "status": 429}
    assert "rate-limiting" in lps_enrichment.describe_error(err)


def test_describe_error_handles_none():
    assert lps_enrichment.describe_error(None)


def test_is_retryable_false_for_a_rejected_key():
    assert lps_enrichment.is_retryable({"kind": lps_enrichment.ERR_API, "status": 401}) is False


def test_is_retryable_true_for_a_rate_limit():
    assert lps_enrichment.is_retryable({"kind": lps_enrichment.ERR_API, "status": 429}) is True


def test_is_retryable_true_for_truncated():
    assert lps_enrichment.is_retryable({"kind": lps_enrichment.ERR_TRUNCATED}) is True


def test_is_retryable_false_for_none():
    assert lps_enrichment.is_retryable(None) is False


# ── probe: the admin self-test, exercised against a real failure and a real
# success so it can never itself become the thing silently swallowing
# information (that was the bug it exists to prevent). ──────────────────────

def test_probe_reports_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = lps_enrichment.probe()
    assert result["configured"] is False
    assert "not set" in result["error"]


def test_probe_reports_ok_on_success(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result = lps_enrichment.probe()
    assert result["ok"] is True
    assert result["headline"]


def test_probe_reports_the_real_failure_kind(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    fake = _FakeAnthropicClient(response_text="not json")
    monkeypatch.setattr(lps_enrichment, "_anthropic", lambda: fake)
    result = lps_enrichment.probe()
    assert result["ok"] is False
    assert result["error_kind"] == lps_enrichment.ERR_UNPARSABLE
