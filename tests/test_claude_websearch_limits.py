"""What ask() does when the web_search tool returns an error instead of results.

Two very different things arrive by the same route, and telling them apart is
what this file is for.

A STARVED search is the tool failing: rate limiting, or the tool being down.
A query the model wanted answered came back with nothing, and no amount of
caller planning would have avoided it. That is `too_many_requests` and
`unavailable`, and the danger is that it looks like a good call from the
outside: text, a normal stop_reason, a non-zero search count. Only the error
block distinguishes it.

A SPENT BUDGET is the tool doing its job: `max_uses_exceeded` is how max_uses
is enforced. It arrives on complete, correct, useful replies, and every caller
in this repo saturates its budget by design. It used to be classed as
starvation, which discarded the results of nearly every call that was working;
one live discovery run lost four of six categories and produced a single
event in half an hour.

These tests drive ask() end to end over a faked transport rather than testing
the block reader on its own: the reader working while ask() ignores it, or
ask() throwing away what the reader correctly read, are both bugs that have
actually happened here.
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
          tool_errors=(), searches=3, stop_reason="end_turn", usage=True):
    content = [_Block(type="text", text=text)]
    for _ in range(searches):
        content.append(_Block(type="server_tool_use", name="web_search"))
    for code in ([tool_error] if tool_error else []) + list(tool_errors):
        content.append(_Block(type="web_search_tool_result",
                              content=[_ErrItem(code)]))
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
    transport["resp"] = _resp(tool_error="too_many_requests")
    r = C.ask("s", "u", max_uses=8)
    assert r["error"] is not None, "a cut-off search was reported as a good answer"
    assert r["error"]["kind"] == C.ERR_SEARCH_LIMIT
    assert "incomplete search" in r["error"]["detail"]


@pytest.mark.parametrize("code", ["too_many_requests", "unavailable"])
def test_every_starvation_code_is_treated_as_a_cut_off_search(transport, code):
    transport["resp"] = _resp(tool_error=code)
    assert C.ask("s", "u")["error"]["kind"] == C.ERR_SEARCH_LIMIT


# ── a spent budget is not a failure ──────────────────────────────────────
#
# `max_uses_exceeded` used to sit in the starvation list, on the theory that
# it came from a server-side limit no caller could avoid. It does not. It is
# how max_uses is enforced: reach for search N+1 under `max_uses: N` and the
# tool answers with that code.
#
# Measured against the live API at max_uses=1 with a prompt needing three
# lookups: one billed search, seven real results, a complete answer naming
# the URL it found, and five max_uses_exceeded blocks for the searches it
# went on to attempt. Every caller in this repo saturates its budget by
# design, so classing the code as failure threw away the work of nearly
# every call that was behaving correctly.

def test_a_spent_budget_is_not_an_error_because_it_is_the_budget_working(transport):
    """The bug that cost a live run four of its six discovery categories and
    produced one event in half an hour."""
    transport["resp"] = _resp(tool_error="max_uses_exceeded")
    r = C.ask("s", "u", max_uses=6)
    assert r["error"] is None, (
        "a reply that used its whole search budget was discarded as a failure")
    assert r["text"], "the answer it did write must survive"


def test_a_spent_budget_is_reported_so_a_caller_can_still_say_so(transport):
    """Not an error, but not nothing either: the model wanted another search.
    A caller that found nothing needs to be able to say which of the two it
    was looking at."""
    transport["resp"] = _resp(tool_error="max_uses_exceeded")
    assert C.ask("s", "u")["budget_spent"] is True


def test_a_clean_call_did_not_spend_its_budget(transport):
    transport["resp"] = _resp()
    assert C.ask("s", "u")["budget_spent"] is False


def test_an_unconfigured_deployment_still_reports_no_spent_budget(monkeypatch):
    """Every early return has to carry the key, or a caller reading it off a
    transport failure gets a KeyError instead of a diagnosis."""
    monkeypatch.setattr(C, "_client", lambda timeout: None)
    r = C.ask("s", "u")
    assert r["error"]["kind"] == C.ERR_NOT_CONFIGURED
    assert r["budget_spent"] is False


def test_real_starvation_alongside_a_spent_budget_still_errors(transport):
    """Both codes at once, which is the common shape: the tool rate-limits
    part-way through and the model burns the rest of its budget retrying. The
    rate limit is the one that means a query went unanswered, so it wins."""
    transport["resp"] = _resp(
        tool_errors=("max_uses_exceeded", "too_many_requests"))
    r = C.ask("s", "u")
    assert r["error"]["kind"] == C.ERR_SEARCH_LIMIT
    assert r["budget_spent"] is True
    assert "max_uses_exceeded" not in r["error"]["detail"], (
        "the message names the codes that broke the search, and a spent "
        "budget is not one of them")


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
    transport["resp"] = _resp(tool_error="unavailable",
                              stop_reason="max_tokens")
    assert C.ask("s", "u")["error"]["kind"] == C.ERR_SEARCH_LIMIT


def test_a_spent_budget_does_not_outrank_a_truncated_answer(transport):
    """The mirror of the test above, and the reason a spent budget is not
    simply ignored. Budget spent AND the answer cut off mid-sentence is a
    real failure, and it must still be reported as the truncation it is."""
    transport["resp"] = _resp(tool_error="max_uses_exceeded",
                              stop_reason="max_tokens")
    r = C.ask("s", "u")
    assert r["error"]["kind"] == C.ERR_MAX_TOKENS
    assert r["budget_spent"] is True


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


# -- citation markup ------------------------------------------------------
# Same reason as above: the stripper working while ask() never calls it is
# exactly the bug. The tag is assembled rather than written out so that
# nothing between here and disk can soften the string under test.

_O = "<" + "cite"
_C = "<" + "/cite>"


def test_ask_hands_back_text_with_the_citation_markup_already_gone(transport):
    transport["resp"] = _resp(
        text="attendees came from " + _O + ' index="1-2">47 states' + _C + ".")
    r = C.ask("s", "u")
    assert "cite" not in r["text"].lower(), \
        "ask() returned citation markup for its caller to store"
    assert r["text"] == "attendees came from \u201c47 states\u201d."


def test_the_untouched_reply_is_still_available_for_a_diagnostic(transport):
    """A probe that reports what actually came back has to see what actually
    came back. `raw` is that; `text` is what callers parse."""
    original = "came from " + _O + ' index="1-2">47 states' + _C + "."
    transport["resp"] = _resp(text=original)
    r = C.ask("s", "u")
    assert r["raw"] == original
    assert r["text"] != r["raw"]


def test_a_reply_with_no_markup_is_passed_through_unchanged(transport):
    transport["resp"] = _resp(text='He said "no" about <b>everything</b>.')
    assert C.ask("s", "u")["text"] == 'He said "no" about <b>everything</b>.'
