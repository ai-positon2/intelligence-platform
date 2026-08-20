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
    def __init__(self, response_text=None, exc=None, capture=None):
        self._text = response_text
        self._exc = exc
        self._capture = capture

    def create(self, **kwargs):
        if self._capture is not None:
            self._capture.append(kwargs)
        if self._exc:
            raise self._exc
        return type("FakeResponse", (), {"content": [_FakeBlock(self._text)]})()


class _FakeAnthropicClient:
    def __init__(self, response_text=None, exc=None, capture=None):
        self.messages = _FakeMessages(response_text, exc, capture)


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
