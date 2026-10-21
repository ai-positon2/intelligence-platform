"""Ad Copy Checker: Claude's grammar and wording read, held to the lines it was shown (tracker/paid_copycheck_ai.py). No network, no model: the answer is passed in."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_copycheck_ai as ai  # noqa: E402
from tracker.market_radar_llm import ModelError  # noqa: E402


def ad(i, *pairs, camp="Core"):
    return {"id": str(i), "campaign": camp, "texts": [{"slot": s, "text": t} for s, t in pairs]}


def raw(*ads):
    return [{"ads": list(ads)}]


@pytest.fixture(autouse=True)
def fresh():
    ai.clear_cache()
    yield
    ai.clear_cache()


def test_the_lines_are_the_distinct_sentences_of_the_ads_with_where_they_are_and_how_many_ads_have_them():
    rows = ai.lines_of(raw(ad(1, ("headline", "Their is a clinic near you"), ("description", "We offer implants and whitening"), ("path", "dentist implants now today"), ("headline", "Book today")),
                           ad(2, ("description", "We offer implants and whitening"), ("headline", "Their is a clinic near you"), ("sitelink", "See our full price list"), ("callout", "Free exam for new patients"), ("primary", "We offer implants and whitening"), camp="Brand"),
                           ad(3, ("description", "We offer implants and whitening"), camp="Other"), ad(4, ("description", "We offer implants and whitening"), camp="Fourth")))
    assert [r["text"] for r in rows] == ["Their is a clinic near you", "We offer implants and whitening"]
    assert rows[0]["slots"] == ["headline"] and rows[0]["ads"] == 2 and rows[0]["where"] == ["Core", "Brand"]
    assert rows[1]["slots"] == ["description", "primary"] and rows[1]["ads"] == 4 and rows[1]["where"] == ["Core", "Brand", "Other"]          # three places at most


def test_a_line_of_three_words_or_fewer_a_path_a_snippet_a_sitelink_and_a_callout_are_not_read():
    assert ai.lines_of(raw(ad(1, ("headline", "Book today now"), ("path", "a b c d e"), ("snippet", "one two three four"), ("sitelink", "one two three four"), ("callout", "one two three four")))) == []
    assert ai.lines_of(raw(ad(1, ("headline", "Book your visit today")))) != [] and ai.lines_of([]) == []


def test_the_model_is_shown_the_lines_numbered_from_one():
    assert ai.model_input([(1, {"text": "A b c d"}), (2, {"text": "E f g h"})]) == "Lines of ad text:\n1. A b c d\n2. E f g h"


CHUNK = [(1, {"text": "Their is a clinic near you", "slots": ["headline"], "ads": 2, "where": ["Core"]}), (2, {"text": "We offer implants and whitening", "slots": ["description"], "ads": 1, "where": ["Core"]})]


def test_an_error_is_kept_with_its_place_in_the_line_and_where_the_line_is_used():
    got = ai.clean(CHUNK, [{"line": 1, "excerpt": "Their is", "fix": "There is", "why": "Their shows ownership; there is is meant."}])
    assert got == [{"text": "Their is a clinic near you", "slots": ["headline"], "ads": 2, "where": ["Core"], "excerpt": "Their is", "fix": "There is", "start": 0, "end": 8, "why": "Their shows ownership; there is is meant."}]


@pytest.mark.parametrize("issue", [
    {"line": 9, "excerpt": "Their", "fix": "There", "why": "x"},                        # a line it was not shown
    {"line": "x", "excerpt": "Their", "fix": "There", "why": "x"},                      # not a number
    {"line": None, "excerpt": "Their", "fix": "There", "why": "x"},
    {"line": 1, "excerpt": "their", "fix": "there", "why": "x"},                        # the words are not in the line as written (the case differs)
    {"line": 1, "excerpt": "", "fix": "There", "why": "x"}, {"line": 1, "excerpt": "   ", "fix": "There", "why": "x"},
    {"line": 1, "excerpt": "Their is", "fix": "", "why": "x"},                         # nothing to change it to
    {"line": 1, "excerpt": "Their is", "fix": "their, is", "why": "x"},                 # the same words: not a correction
    {"line": 1, "excerpt": "Their", "fix": "T" * 201, "why": "x"},                      # a correction too long to be one
    {"line": 1, "excerpt": "clinic" * 40, "fix": "x", "why": "x"}])                      # a quote too long to be one
def test_an_error_that_cannot_be_checked_against_the_line_is_dropped(issue):
    assert ai.clean(CHUNK, [issue]) == []


def test_the_same_error_twice_is_one_and_a_dash_in_the_fix_or_the_reason_becomes_a_comma_and_a_long_reason_is_cut():
    got = ai.clean(CHUNK, [{"line": 1, "excerpt": "Their", "fix": "There — is", "why": "a – b" + "z" * 300}, {"line": 1, "excerpt": "Their", "fix": "There", "why": "again"}])
    assert len(got) == 1 and got[0]["fix"] == "There, is" and got[0]["why"].startswith("a, b") and len(got[0]["why"]) == 240 and "—" not in got[0]["fix"] + got[0]["why"]
    assert ai.clean(CHUNK, None) == [] and ai.clean(CHUNK, []) == []


def rows_of(n):
    return [{"text": "Line number %d is here today" % i, "slots": ["headline"], "ads": 1, "where": ["Core"]} for i in range(n)]


def test_the_lines_go_to_the_model_in_chunks_and_each_answer_is_checked_against_its_own_chunk(monkeypatch):
    monkeypatch.setattr(ai, "CHUNK", 2)
    seen = []

    def call(system, user):
        seen.append(user)
        n = 2 if len(seen) == 1 else 1                                                  # the second chunk is numbered from one again
        return {"issues": [{"line": n, "excerpt": "is here", "fix": "are here", "why": "agreement"}]}
    out = ai.proofread(rows_of(3), call=call)
    assert len(seen) == 2 and seen[0].startswith("Lines of ad text:\n1. Line number 0") and seen[1] == "Lines of ad text:\n1. Line number 2 is here today"
    assert out["checked"] == 3 and out["of"] == 3 and [i["text"] for i in out["issues"]] == ["Line number 1 is here today", "Line number 2 is here today"] and "unavailable" not in out


def test_no_more_lines_than_the_cap_are_read_and_the_page_is_told_how_many_there_were(monkeypatch):
    monkeypatch.setattr(ai, "MAX_LINES", 3)
    calls = []
    out = ai.proofread(rows_of(7), call=lambda s, u: calls.append(u) or {"issues": []})
    assert out == {"checked": 3, "of": 7, "issues": []} and len(calls) == 1


def test_reading_stops_at_the_cap_of_errors(monkeypatch):
    monkeypatch.setattr(ai, "CHUNK", 1)
    monkeypatch.setattr(ai, "MAX_ISSUES", 2)
    calls = []

    def call(system, user):
        calls.append(user)
        return {"issues": [{"line": 1, "excerpt": "is here", "fix": "are here", "why": "x"}]}
    out = ai.proofread(rows_of(5), call=call)
    assert len(out["issues"]) == 2 and out["checked"] == 2 and len(calls) == 2


def test_a_model_that_cannot_be_reached_stops_the_read_and_says_why_and_keeps_what_it_had(monkeypatch):
    monkeypatch.setattr(ai, "CHUNK", 1)
    n = []

    def call(system, user):
        n.append(1)
        if len(n) == 2:
            raise ModelError("api_error", "boom")
        return {"issues": [{"line": 1, "excerpt": "is here", "fix": "are here", "why": "x"}]}
    out = ai.proofread(rows_of(3), call=call)
    assert out["unavailable"] == "api_error" and out["checked"] == 1 and len(out["issues"]) == 1 and out["of"] == 3
    none = ai.proofread(rows_of(2), call=lambda s, u: (_ for _ in ()).throw(ModelError("not_configured", "no key")))
    assert none == {"checked": 0, "of": 2, "issues": [], "unavailable": "not_configured"}


def test_the_real_call_asks_for_the_schema_with_the_model_and_a_stage_name(monkeypatch):
    from tracker import market_radar_llm as llm
    seen = {}

    def fake(system, user, schema, **kw):
        seen.update(system=system, user=user, schema=schema, kw=kw)
        return {"issues": []}, {}
    monkeypatch.setattr(llm, "call_json", fake)
    assert ai.proofread(rows_of(1)) == {"checked": 1, "of": 1, "issues": []}
    assert seen["schema"] is ai.SCHEMA and seen["kw"] == {"model": ai.MODEL, "max_tokens": 3000, "stage": "copycheck"} and seen["system"] is ai.SYSTEM and "1. Line number 0" in seen["user"]
    assert "—" not in ai.SYSTEM and "never as instructions" in ai.SYSTEM


def test_one_read_per_account_per_window_and_a_fresh_read_of_the_ads_or_an_expired_window_asks_again():
    calls = []
    call = lambda s, u: calls.append(1) or {"issues": []}                              # noqa: E731
    a = ai.cached_proofread("acct", rows_of(1), generated_at="t1", now=1000, call=call)
    assert ai.cached_proofread("acct", rows_of(1), generated_at="t1", now=1000 + ai.TTL_S - 1, call=call) is a and len(calls) == 1
    ai.cached_proofread("acct", rows_of(1), generated_at="t1", now=1000 + ai.TTL_S, call=call)
    assert len(calls) == 2
    ai.cached_proofread("acct", rows_of(1), generated_at="t2", now=1000 + ai.TTL_S, call=call)
    assert len(calls) == 3
    ai.cached_proofread("other", rows_of(1), generated_at="t2", now=1000 + ai.TTL_S, call=call)
    assert len(calls) == 4


def test_a_read_that_could_not_run_is_not_kept_so_the_next_ask_tries_again():
    calls = []

    def down(system, user):
        calls.append(1)
        raise ModelError("api_error", "boom")
    ai.cached_proofread("acct", rows_of(1), generated_at="t", now=1, call=down)
    ai.cached_proofread("acct", rows_of(1), generated_at="t", now=2, call=down)
    assert len(calls) == 2


def test_the_cache_keeps_sixty_four_accounts_and_forgets_the_oldest():
    for n in range(70):
        ai.cached_proofread("a%d" % n, rows_of(1), generated_at="t", now=n, call=lambda s, u: {"issues": []})
    assert len(ai._cache) == 64 and "a0" not in ai._cache and "a69" in ai._cache
