"""What ask() does when the web_search tool refuses to keep searching.

The tool can return an error block instead of results, leaving the model to
write its answer from whatever it already had. Observed live on 2026-09-02:
two of six discovery categories reported that the search tool had stopped
answering part-way through, one after an initial batch of eight parallel
queries. max_uses itself is honoured (a probe capping it at 1 billed exactly
one search), so this is a server-side limit across the turn rather than a
budget the caller can lower to avoid.

The danger is that a starved call looks like a good one from the outside. It
has text, a normal stop_reason, and a non-zero search count. Only the error
block distinguishes it, so these tests drive ask() end to end with a faked
transport rather than testing the block reader on its own: the reader working
while ask() ignores it is exactly the bug this guards.
"""

import types

import pytest

from tracker import claude_websearch as C


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ErrItem:
    def __init__(self, code):
        self.type = "web_search_tool_result_error"
        self.error_code = code


def _resp(*, text="Here is what I could find.", tool_error=None,
          searches=3, stop_reason="end_turn", usage=True):
    content = [_Block(type="text", text=text)]
    for _ in range(searches):
        content.append(_Block(type="server_tool_use", name="web_search"))
    if tool_error:
        content.append(_Block(type="web_search_tool_result",
                              content=[_ErrItem(tool_error)]))
    r = types.SimpleNamespace(content=content, stop_reason=stop_reason)
    if usage:
        r.usage = types.SimpleNamespace(
            input_tokens=1200, output_tokens=340,
            server_tool_use=types.SimpleNamespace(web_search_requests=searches))
    return r


@pytest.fixture()
def transport(monkeypatch):
    """Drive the real ask() over a faked stream."""
    box = {"resp": _resp()}

    class _Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self): return box["resp"]

    class _Messages:
        def stream(self, **kw):
            box["kw"] = kw
            return _Stream()

    monkeypatch.setattr(C, "_client",
                        lambda timeout: types.SimpleNamespace(messages=_Messages()))
    return box


def test_a_starved_search_is_an_error_even_though_it_returned_text(transport):
    """The regression D5 exposed. Text present, stop_reason end_turn, three
    searches on the clock: every signal a caller checks says success."""
    transport["resp"] = _resp(tool_error="max_uses_exceeded")
    r = C.ask("s", "u", max_uses=8)
    assert r["error"] is not None, "a cut-off search was reported as a good answer"
    assert r["error"]["kind"] == C.ERR_SEARCH_LIMIT
    assert "incomplete search" in r["error"]["detail"]


@pytest.mark.parametrize("code", ["max_uses_exceeded", "too_many_requests",
                                  "unavailable"])
def test_every_starvation_code_is_treated_as_a_cut_off_search(transport, code):
    transport["resp"] = _resp(tool_error=code)
    assert C.ask("s", "u")["error"]["kind"] == C.ERR_SEARCH_LIMIT


def test_a_tool_error_that_is_not_starvation_does_not_fake_a_limit(transport):
    """query_too_long is the model's own fault on one query, not the tool
    refusing to work. Calling it a limit would tell a client their whole
    category went unsearched over a single bad query."""
    transport["resp"] = _resp(tool_error="query_too_long")
    r = C.ask("s", "u")
    assert r["error"] is None
    assert r["tool_errors"] == ["query_too_long"]


def test_starvation_outranks_max_tokens_and_empty(transport):
    """A starved call usually still produces prose, so every later check would
    classify it as a good answer. Order matters here, not just presence."""
    transport["resp"] = _resp(tool_error="max_uses_exceeded",
                              stop_reason="max_tokens")
    assert C.ask("s", "u")["error"]["kind"] == C.ERR_SEARCH_LIMIT


def test_a_clean_call_reports_no_tool_errors(transport):
    transport["resp"] = _resp()
    r = C.ask("s", "u")
    assert r["error"] is None and r["tool_errors"] == []


def test_the_bill_is_reported_so_a_run_can_say_what_it_cost(transport):
    """Before this, the only number anyone had for a run was wall-clock time,
    which says nothing about the bill."""
    transport["resp"] = _resp(searches=7)
    u = C.ask("s", "u")["usage"]
    assert u["input_tokens"] == 1200 and u["output_tokens"] == 340
    assert u["web_search_requests"] == 7


def test_a_response_without_usage_reports_an_empty_bill_rather_than_zeros(transport):
    """Zeros would read as a free call. Absent has to stay absent."""
    transport["resp"] = _resp(usage=False)
    assert C.ask("s", "u")["usage"] == {}


def test_every_documented_result_key_is_present_on_a_starved_call(transport):
    transport["resp"] = _resp(tool_error="max_uses_exceeded")
    r = C.ask("s", "u")
    assert not [k for k in C._RESULT_KEYS if k not in r]


# ── search_count must mean SEARCHES ──────────────────────────────────────
#
# web_search is one of several server-side tools. A live probe that capped
# web_search at 1 returned eighteen server_tool_use blocks, seventeen of them
# code execution. Counting blocks reported eighteen searches for a reply that
# ran one, and two callers use that number as their "did you actually look
# this up, or are you reciting?" guard.

def _mixed(*, web=0, other=0, usage=True):
    content = [_Block(type="text", text="answer")]
    for _ in range(web):
        content.append(_Block(type="server_tool_use", name="web_search"))
    for _ in range(other):
        content.append(_Block(type="server_tool_use", name="code_execution"))
    r = types.SimpleNamespace(content=content, stop_reason="end_turn")
    if usage:
        r.usage = types.SimpleNamespace(
            input_tokens=10, output_tokens=10,
            server_tool_use=types.SimpleNamespace(web_search_requests=web))
    return r


def test_other_server_tools_are_not_counted_as_searches(transport):
    """The regression. Seventeen code-execution blocks and one search used to
    read as eighteen searches."""
    transport["resp"] = _mixed(web=1, other=17)
    assert C.ask("s", "u")["search_count"] == 1


def test_a_reply_that_ran_no_search_at_all_reports_zero(transport):
    """The number the recalled-answer guards test. If tool use of any kind
    counts, a reply that searched nothing satisfies the guard and its events
    are accepted as confirmed rather than discarded."""
    transport["resp"] = _mixed(web=0, other=6)
    assert C.ask("s", "u")["search_count"] == 0, (
        "a reply that never searched was reported as having searched")


def test_the_block_scan_fallback_also_filters_by_tool_name(transport):
    """No usage on the response, so the count comes from the blocks. It must
    filter there too, or the fallback quietly restores the bug."""
    transport["resp"] = _mixed(web=2, other=9, usage=False)
    assert C.ask("s", "u")["search_count"] == 2


def test_the_billed_count_wins_over_the_block_scan(transport):
    """usage.web_search_requests is what Anthropic charges for and is
    authoritative even when the block count disagrees."""
    r = _mixed(web=3, other=0)
    r.usage.server_tool_use.web_search_requests = 5
    transport["resp"] = r
    assert C.ask("s", "u")["search_count"] == 5
