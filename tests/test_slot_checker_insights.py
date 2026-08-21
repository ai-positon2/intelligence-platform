"""tracker/slot_checker_insights.py: the Claude synthesis layer over the
Gentle Dental Slot Checker's own derived dashboard numbers.

Same architectural rule under test as test_lps_enrichment.py, which this file
mirrors closely: anything that could go wrong (no ANTHROPIC_API_KEY, an empty
dashboard, a network error, a malformed reply) must degrade to (None, error),
never raise -- the Flask route treats this as strictly best-effort, and the
dashboard itself must render fine without it. Also tested: compact_for_llm
drops the noisy per-date arrays and caps alert examples, and fetch()'s cache
is both TTL-bound and invalidated by a snapshot's generated_at changing.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import slot_checker_insights as sci  # noqa: E402


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
    "headline": "MA carries most of the portfolio's capacity; three practices need attention.",
    "synthesis": "Paragraph one.\n\nParagraph two.",
    "topActions": ["Call ahead at Exeter NH, which is fully booked.", "Check Torrington's crawl."],
    "risks": ["Torrington returned no data at all."],
    "opportunities": ["MA has 4,800 slots across 48 practices, room to promote."],
    "coverage": "81 of 82 practices returned real data.",
})

_SAMPLE_DASHBOARD = {
    "generated_at": "2026-08-21T00:00:00+00:00",
    "totals": {"slots": 7070, "practices": 82, "practices_with_data": 81, "states": 7},
    "by_state": [{"state": "MA", "slots": 4800, "practices": 48, "avg": 100.0}],
    "by_service": [{"name": "New Patient Exam", "slots": 3000, "practices": 70, "zero": 2}],
    "by_brand": [{"brand": "Gentle Dental", "slots": 4600, "practices": 46}],
    "by_weekday": [{"day": "Sun", "slots": 400, "avg": 200.0}],
    "practices": [
        {"name": "Boston Downtown", "state": "MA", "brand": "Gentle Dental", "total": 210, "status": "open"},
        {"name": "Exeter", "state": "NH", "brand": "Gentle Dental", "total": 0, "status": "none"},
        {"name": "Torrington", "state": "CT", "brand": "Gentle Dental", "total": 0, "status": "no-data"},
    ],
    "alerts": {
        "no_data": [{"name": "Torrington", "state": "CT"}],
        "zero": [{"name": "Exeter", "state": "NH"}],
        "thin": [],
        "unbookable_services": [],
    },
    "freshness": {"oldest": "2026-08-12", "newest": "2026-08-13"},
}


@pytest.fixture(autouse=True)
def _clean_cache():
    sci.reset_cache()
    yield
    sci.reset_cache()


# ── _anthropic: degrades exactly like arena_client / lps_enrichment ─────────

def test_anthropic_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert sci._anthropic() is None


def test_anthropic_returns_a_real_client_with_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    client = sci._anthropic()
    assert client is not None
    assert type(client).__name__ == "Anthropic"


# ── generate_insights_result: the degrade-to-(None, error) contract ────────

def test_returns_none_none_without_a_configured_key(monkeypatch):
    monkeypatch.setattr(sci, "_anthropic", lambda: None)
    result, err = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert result is None
    assert err is None


def test_returns_empty_source_error_when_totals_has_no_practices(monkeypatch):
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, err = sci.generate_insights_result({"totals": {}})
    assert result is None
    assert err["kind"] == sci.ERR_EMPTY_SOURCE


def test_parses_a_well_formed_reply(monkeypatch):
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, err = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert err is None
    assert "Torrington" not in result["headline"]  # sanity: headline is the model's, unmodified
    assert result["headline"].startswith("MA carries")
    assert "Paragraph one." in result["synthesis"]
    assert result["topActions"] == ["Call ahead at Exeter NH, which is fully booked.", "Check Torrington's crawl."]
    assert result["risks"] == ["Torrington returned no data at all."]
    assert result["opportunities"] == ["MA has 4,800 slots across 48 practices, room to promote."]
    assert result["coverage"].startswith("81 of 82")


def test_sends_a_compact_payload_not_the_raw_practice_list(monkeypatch):
    """The dashboard's raw form carries 82 practices' worth of per-date count
    arrays. compact_for_llm should strip that before it ever reaches Claude --
    this pins that the payload actually sent is the compacted one."""
    capture = []
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY, capture=capture)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert len(capture) == 1
    sent = json.loads(capture[0]["messages"][0]["content"])
    assert "practices" not in sent  # the raw per-practice list, not the compacted view
    assert sent["totals"]["slots"] == 7070
    assert sent["top_practices"][0]["name"] == "Boston Downtown"
    assert sent["alerts"]["no_data"]["count"] == 1


def test_returns_none_on_malformed_json(monkeypatch):
    fake = _FakeAnthropicClient(response_text="not json at all")
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, err = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert result is None
    assert err["kind"] == sci.ERR_UNPARSABLE


def test_returns_shape_error_when_reply_is_a_json_array(monkeypatch):
    fake = _FakeAnthropicClient(response_text=json.dumps(["headline", "synthesis"]))
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, err = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert result is None
    assert err["kind"] == sci.ERR_SHAPE


def test_returns_shape_error_when_headline_missing(monkeypatch):
    fake = _FakeAnthropicClient(response_text=json.dumps({"synthesis": "S"}))
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, err = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert result is None
    assert err["kind"] == sci.ERR_SHAPE


def test_returns_api_error_with_status_on_exception(monkeypatch):
    exc = RuntimeError("rejected")
    exc.status_code = 401
    fake = _FakeAnthropicClient(exc=exc)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, err = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert result is None
    assert err["kind"] == sci.ERR_API
    assert err["status"] == 401


def test_truncates_action_lists_to_their_caps(monkeypatch):
    reply = json.dumps({
        "headline": "H", "synthesis": "S",
        "topActions": ["a", "b", "c", "d", "e", "f", "g"],
        "risks": ["1", "2", "3", "4", "5"],
        "opportunities": ["x", "y", "z", "w", "v"],
    })
    fake = _FakeAnthropicClient(response_text=reply)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, _ = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert result["topActions"] == ["a", "b", "c", "d", "e"]
    assert result["risks"] == ["1", "2", "3", "4"]
    assert result["opportunities"] == ["x", "y", "z", "w"]


def test_omits_optional_list_keys_when_not_provided(monkeypatch):
    fake = _FakeAnthropicClient(response_text=json.dumps({"headline": "H", "synthesis": "S"}))
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, _ = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert "risks" not in result
    assert "opportunities" not in result
    assert "coverage" not in result
    assert result["topActions"] == []


# ── truncation retry ─────────────────────────────────────────────────────────

def test_a_truncated_reply_is_retried_with_a_bigger_budget(monkeypatch):
    capture = []
    fake = _FakeAnthropicClient(capture=capture, sequence=[
        ('{"headline": "cut off mid-strin', "max_tokens"),
        (_GOOD_REPLY, "end_turn"),
    ])
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, err = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert err is None
    assert result["headline"].startswith("MA carries")
    assert len(capture) == 2
    assert capture[1]["max_tokens"] == sci._RETRY_MAX_TOKENS


def test_still_truncated_after_retry_reports_truncated_error(monkeypatch):
    fake = _FakeAnthropicClient(sequence=[
        ('{"cut', "max_tokens"),
        ('{"still cut', "max_tokens"),
    ])
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    result, err = sci.generate_insights_result(_SAMPLE_DASHBOARD)
    assert result is None
    assert err["kind"] == sci.ERR_TRUNCATED


# ── compact_for_llm ──────────────────────────────────────────────────────────

def test_compact_for_llm_picks_top_and_bottom_practices_with_data():
    out = sci.compact_for_llm(_SAMPLE_DASHBOARD)
    names_top = [p["name"] for p in out["top_practices"]]
    names_bottom = [p["name"] for p in out["bottom_practices"]]
    assert "Boston Downtown" in names_top
    # Torrington has no data and Exeter has zero slots -- neither is a
    # meaningful "bottom" practice (bottom means "bookable but barely"),
    # so both must be excluded rather than polluting that list with zeros.
    assert "Torrington" not in names_bottom
    assert "Exeter" not in names_bottom


def test_compact_for_llm_caps_alert_examples():
    big_dashboard = dict(_SAMPLE_DASHBOARD)
    big_dashboard["alerts"] = {
        "no_data": [{"name": f"P{i}", "state": "MA"} for i in range(20)],
        "zero": [], "thin": [], "unbookable_services": [],
    }
    out = sci.compact_for_llm(big_dashboard)
    assert out["alerts"]["no_data"]["count"] == 20
    assert len(out["alerts"]["no_data"]["examples"]) == 8


# ── describe_error / is_retryable ────────────────────────────────────────────

def test_describe_error_handles_every_kind():
    for kind, status in [
        (sci.ERR_EMPTY_SOURCE, None), (sci.ERR_API, 401), (sci.ERR_API, 429),
        (sci.ERR_API, 500), (sci.ERR_API, None), (sci.ERR_TRUNCATED, None),
        (sci.ERR_UNPARSABLE, None), (sci.ERR_SHAPE, None),
    ]:
        msg = sci.describe_error({"kind": kind, "status": status, "detail": ""})
        assert isinstance(msg, str) and msg


def test_describe_error_handles_none_and_garbage():
    assert isinstance(sci.describe_error(None), str)
    assert isinstance(sci.describe_error({}), str)


def test_is_retryable_false_for_auth_errors():
    assert sci.is_retryable({"kind": sci.ERR_API, "status": 401}) is False
    assert sci.is_retryable({"kind": sci.ERR_API, "status": 403}) is False


def test_is_retryable_true_for_transient_kinds():
    assert sci.is_retryable({"kind": sci.ERR_API, "status": 500}) is True
    assert sci.is_retryable({"kind": sci.ERR_TRUNCATED}) is True
    assert sci.is_retryable({"kind": sci.ERR_UNPARSABLE}) is True


def test_is_retryable_false_for_non_dict():
    assert sci.is_retryable(None) is False


# ── fetch(): TTL + generated_at cache ────────────────────────────────────────

def test_fetch_caches_within_the_ttl(monkeypatch):
    capture = []
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY, capture=capture)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    sci.fetch(_SAMPLE_DASHBOARD)
    sci.fetch(_SAMPLE_DASHBOARD)
    assert len(capture) == 1


def test_fetch_force_bypasses_the_cache(monkeypatch):
    capture = []
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY, capture=capture)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    sci.fetch(_SAMPLE_DASHBOARD)
    sci.fetch(_SAMPLE_DASHBOARD, force=True)
    assert len(capture) == 2


def test_fetch_invalidates_when_generated_at_changes(monkeypatch):
    """A re-imported snapshot must produce a fresh briefing even inside the
    TTL window -- otherwise the dashboard would show this week's numbers next
    to last week's AI summary, silently disagreeing with itself."""
    capture = []
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY, capture=capture)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    sci.fetch(_SAMPLE_DASHBOARD)
    newer = dict(_SAMPLE_DASHBOARD, generated_at="2026-08-28T00:00:00+00:00")
    sci.fetch(newer)
    assert len(capture) == 2


# ── probe() ──────────────────────────────────────────────────────────────────

def test_probe_reports_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = sci.probe()
    assert out["configured"] is False
    assert "error" in out


def test_probe_reports_ok_on_a_successful_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    fake = _FakeAnthropicClient(response_text=_GOOD_REPLY)
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    out = sci.probe()
    assert out["ok"] is True
    assert out["headline"]


def test_probe_reports_failure_kind_when_the_call_fails(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    fake = _FakeAnthropicClient(response_text="not json")
    monkeypatch.setattr(sci, "_anthropic", lambda: fake)
    out = sci.probe()
    assert out["ok"] is False
    assert out["error_kind"] == sci.ERR_UNPARSABLE
