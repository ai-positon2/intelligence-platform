"""The request ask() sends and the record it keeps.

Five contracts, each from a real defect:

  * `temperature` reaches only a model that accepts it. The production
    default (claude-sonnet-5) returns a 400 for any sampling parameter, and
    that 400 would be read as a rejected tool version.
  * a call has a wall-clock bound, because a streamed reply that keeps
    sending events never trips the per-read timeout.
  * the URLs the searches actually returned are kept, so a citation can be
    checked against what was searched rather than against what was typed.
  * a call that never reached the provider is not counted as a call.
  * only a successful reply is replayed from the call ledger: a stored 529
    used to come back on every retry of the same prompt.

Driven end to end over a faked transport, like test_claude_websearch_limits.
"""

import types

import pytest

from tracker import claude_websearch as C


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _resp(text="answer", urls=(), searches=1):
    content = [_Block(type="text", text=text)]
    if urls:
        content.append(_Block(type="web_search_tool_result",
                              content=[_Block(type="web_search_result", url=u)
                                       for u in urls]))
    r = types.SimpleNamespace(content=content, stop_reason="end_turn")
    r.usage = types.SimpleNamespace(
        input_tokens=10, output_tokens=5,
        server_tool_use=types.SimpleNamespace(web_search_requests=searches))
    return r


@pytest.fixture()
def transport(monkeypatch):
    box = {"resp": _resp(), "events": 0, "calls": 0}

    class _Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self):
            for i in range(box["events"]):
                yield i
        def get_final_message(self): return box["resp"]

    class _Messages:
        def stream(self, **kw):
            box["kw"] = kw
            box["calls"] += 1
            return _Stream()

    monkeypatch.setattr(C, "_client",
                        lambda timeout: types.SimpleNamespace(messages=_Messages()))
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    return box


# ── temperature ───────────────────────────────────────────────────────────

def test_temperature_zero_is_sent_to_a_model_that_accepts_it(transport):
    C.ask("s", "u", model="claude-sonnet-4-6")
    assert transport["kw"]["temperature"] == 0.0


def test_temperature_is_never_sent_to_the_production_default(transport):
    """claude-sonnet-5 rejects sampling parameters with a 400. Sending one
    would fail every call this module makes."""
    C.ask("s", "u")
    assert transport["kw"]["model"] == "claude-sonnet-5"
    assert "temperature" not in transport["kw"]


@pytest.mark.parametrize("model", ["claude-opus-5", "claude-opus-4-7",
                                   "claude-opus-4-8", "claude-fable-5-1",
                                   "claude-opus-5-5", "some-future-model"])
def test_an_unlisted_model_gets_no_sampling_parameter(transport, model):
    C.ask("s", "u", model=model)
    assert "temperature" not in transport["kw"]


def test_temperature_none_is_never_sent(transport):
    C.ask("s", "u", model="claude-haiku-4-5", temperature=None)
    assert "temperature" not in transport["kw"]


# ── wall-clock deadline ───────────────────────────────────────────────────

def test_a_call_past_its_deadline_is_abandoned_with_a_reader_reason(transport):
    transport["events"] = 3
    r = C.ask("s", "u", deadline=-1)
    assert r["error"]["kind"] == C.ERR_DEADLINE
    assert r["text"] == ""
    reason = C.reader_reason(r["error"])
    assert reason != C._READER_FALLBACK and "deadline" not in reason


def test_a_call_inside_its_deadline_is_untouched(transport):
    transport["events"] = 3
    r = C.ask("s", "u", deadline=600)
    assert r["error"] is None and r["text"] == "answer"


# ── what the searches returned ────────────────────────────────────────────

def test_the_urls_the_searches_returned_are_kept(transport):
    transport["resp"] = _resp(urls=["https://a.example/x", "https://b.example/",
                                    "https://a.example/x"])
    r = C.ask("s", "u")
    assert r["result_urls"] == ["https://a.example/x", "https://b.example/"]


def test_a_reply_with_no_search_results_keeps_an_empty_list(transport):
    assert C.ask("s", "u")["result_urls"] == []


# ── what counts as a call ─────────────────────────────────────────────────

@pytest.mark.parametrize("kind", sorted(C.NOT_SENT_KINDS))
def test_a_call_that_was_never_sent_is_not_counted(kind):
    assert C.spend_of({"text": "", "error": {"kind": kind, "detail": "x"},
                       "usage": {}})["calls"] == 0


def test_a_missing_reply_is_not_a_call():
    assert C.spend_of(None)["calls"] == 0
    assert C.spend_of({})["calls"] == 0


def test_a_call_that_failed_at_the_provider_still_counts():
    assert C.spend_of({"error": {"kind": C.ERR_TRANSPORT, "detail": "529"},
                       "usage": {}})["calls"] == 1


# ── the call ledger replays successes only ────────────────────────────────

@pytest.fixture()
def ledger(monkeypatch, transport):
    from tracker import event_intel_jobs as J
    box = {"cached": None, "finished": []}
    monkeypatch.setattr(J, "reserve_call",
                        lambda *a: {"id": 7, "cached": box["cached"]})
    monkeypatch.setattr(J, "finish_call",
                        lambda cid, result, ms: box["finished"].append((cid, result)))
    token = J.CURRENT.set({"run_id": 1, "token": "t", "email": "e"})
    yield box
    J.CURRENT.reset(token)


def test_what_the_ledger_hands_back_is_the_answer(ledger, transport):
    """reserve_call decides what is replayed: it re-issues a failure of the
    moment under a NEW row (tests/test_event_intel_platform_jobs.py covers
    that against Postgres), so whatever it still returns as cached is the
    answer. Re-attempting it here as well wrote a second outcome over the
    first row and skipped the daily limits."""
    ledger["cached"] = {"text": "", "error": {"kind": C.ERR_MAX_TOKENS,
                                              "detail": "stop_reason=max_tokens"}, "usage": {}}
    r = C.ask("s", "u")
    assert transport["calls"] == 0 and ledger["finished"] == []
    assert r["error"]["kind"] == C.ERR_MAX_TOKENS


def test_a_call_that_raises_still_records_an_outcome(ledger, monkeypatch):
    """A row with no response reads as "unknown outcome" to every later
    attempt, and the retry is refused."""
    def boom(*a, **k):
        raise RuntimeError("socket closed")
    monkeypatch.setattr(C, "_ask", boom)
    with pytest.raises(RuntimeError):
        C.ask("s", "u")
    [(cid, result)] = ledger["finished"]
    assert cid == 7 and result["error"]["kind"] == C.ERR_TRANSPORT


def test_a_stored_success_is_replayed_without_a_call(ledger, transport):
    ledger["cached"] = {"text": "kept", "error": None, "usage": {}}
    r = C.ask("s", "u")
    assert r["text"] == "kept" and transport["calls"] == 0
    assert ledger["finished"] == []
