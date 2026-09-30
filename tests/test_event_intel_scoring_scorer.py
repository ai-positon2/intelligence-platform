"""The scorer: whose reply is whose, which edition it graded, and the
borderline re-score. Every model call is stubbed; nothing leaves the process.
"""

import json
import re

import pytest

from tracker import claude_websearch
from tracker import event_intel_report as REP
from tracker import event_intel_rubric as R
from tracker import event_intel_scorer as SC

PROFILE = {"client_name": "Northwind", "classification": R.CLASS_B2B_TO_MARKETING,
           "buyer_roles": "VP Marketing", "verticals": "fintech"}


def _cand(name, starts_on="2099-06-02", **kw):
    d = {"name": name, "category": R.CAT_VERTICAL_SUMMIT, "starts_on": starts_on,
         "website": "https://%s.example" % re.sub(r"\W", "", name).lower()}
    d.update(kw)
    return d


def _reply(name, rel=30, dm=30, eng=15, starts_on=None, **kw):
    r = {"name": name, "relevance": rel, "relevance_note": "r%d" % rel,
         "dm_access": dm, "dm_access_note": "d%d" % dm, "engagement": eng,
         "engagement_note": "e%d" % eng, "description": "About %s." % name,
         "client_line": "Why %s." % name}
    if starts_on:
        r["starts_on"] = starts_on
    r.update(kw)
    return r


def _stub(monkeypatch, answer):
    """`answer(user, call_no)` returns the list of reply rows for one call."""
    calls = []

    def fake_ask(system, user, **kw):
        calls.append({"system": system, "user": user, **kw})
        rows = answer(user, len(calls))
        if isinstance(rows, dict) and "error" in rows:
            return {"text": "", "error": rows["error"], "usage": {"input_tokens": 10}}
        return {"text": json.dumps({"scores": rows}), "error": None,
                "usage": {"input_tokens": 1000, "output_tokens": 100},
                "search_count": kw.get("max_uses", 0)}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    return calls


def _by_name(out):
    return {c["name"]: c for c in out["scored"]}


# ── 1. one event never borrows another candidate's reply ─────────────────

def test_an_event_does_not_borrow_the_score_of_a_longer_named_candidate(monkeypatch):
    """Reproduced: the grader returned only "Fintech Meetup Asia", and
    "Fintech Meetup" loosely matched it and was stored with Asia's 38."""
    _stub(monkeypatch, lambda u, n: [_reply("Fintech Meetup Asia", rel=38)])
    out = SC.score_all([_cand("Fintech Meetup"), _cand("Fintech Meetup Asia")], PROFILE)
    assert list(_by_name(out)) == ["Fintech Meetup Asia"]
    assert [c["name"] for c in out["unscored"]] == ["Fintech Meetup"]


def test_a_reply_two_candidates_match_loosely_goes_to_neither(monkeypatch):
    _stub(monkeypatch, lambda u, n: [_reply("Acme Growth Collective Retreat")])
    out = SC.score_all([_cand("Acme Growth"), _cand("Acme Growth Collective")], PROFILE)
    assert out["scored"] == []
    assert len(out["unscored"]) == 2


def test_a_reworded_name_is_still_matched_when_nobody_else_could_claim_it(monkeypatch):
    _stub(monkeypatch, lambda u, n: [_reply("GTM Unbound Festival")])
    out = SC.score_all([_cand("GTM Unbound"), _cand("Payments Leaders Forum")], PROFILE)
    assert "GTM Unbound" in _by_name(out)


# ── 2. two editions of one series ────────────────────────────────────────

def test_two_editions_keep_their_own_scores(monkeypatch):
    """Reproduced: 2026-06-02 and 2027-06-08 both got the 2027 scores."""
    _stub(monkeypatch, lambda u, n: [
        _reply("Money20/20 Europe", rel=36, starts_on="2026-06-02"),
        _reply("Money20/20 Europe", rel=12, starts_on="2027-06-08")])
    out = SC.score_all([_cand("Money20/20 Europe", "2026-06-02"),
                        _cand("Money20/20 Europe", "2027-06-08")], PROFILE)
    got = {c["starts_on"]: c["relevance"] for c in out["scored"]}
    assert got == {"2026-06-02": 36, "2027-06-08": 12}


def test_an_undated_reply_is_refused_when_two_editions_could_own_it(monkeypatch):
    _stub(monkeypatch, lambda u, n: [_reply("Money20/20 Europe", rel=36)])
    out = SC.score_all([_cand("Money20/20 Europe", "2026-06-02"),
                        _cand("Money20/20 Europe", "2027-06-08")], PROFILE)
    assert out["scored"] == [] and len(out["unscored"]) == 2


def test_an_undated_reply_is_taken_when_only_one_edition_is_in_the_batch(monkeypatch):
    _stub(monkeypatch, lambda u, n: [_reply("Money20/20 Europe", rel=36)])
    out = SC.score_all([_cand("Money20/20 Europe", "2026-06-02")], PROFILE)
    assert out["scored"][0]["relevance"] == 36


def test_a_reply_for_a_different_edition_is_not_used(monkeypatch):
    _stub(monkeypatch, lambda u, n: [
        _reply("Money20/20 Europe", rel=12, starts_on="2027-06-08")])
    out = SC.score_all([_cand("Money20/20 Europe", "2026-06-02")], PROFILE)
    assert out["scored"] == []


def test_editions_in_different_batches_do_not_overwrite_each_other(monkeypatch):
    """Across batches the winner used to be whichever batch finished last.
    Replies are now resolved against the batch that produced them."""
    def answer(user, n):
        rows = []
        for m in re.finditer(r"^- (.+)$\n(?:  .+\n)*?  dates: (\S+)", user, re.M):
            name, date = m.group(1), m.group(2)
            rel = 36 if date.startswith("2026") else 12
            # Undated on purpose: the worst case for a merged dict.
            rows.append(_reply(name, rel=rel))
        return rows
    _stub(monkeypatch, answer)
    cands = [_cand("Filler %d" % i) for i in range(6)]
    cands.insert(0, _cand("Money20/20 Europe", "2026-06-02"))
    cands.append(_cand("Money20/20 Europe", "2027-06-08"))
    out = SC.score_all(cands, PROFILE)
    assert out["batches"] == 2
    got = {c["starts_on"]: c["relevance"] for c in out["scored"]
           if c["name"] == "Money20/20 Europe"}
    assert got == {"2026-06-02": 36, "2027-06-08": 12}


def test_the_prompt_asks_for_the_edition_back(monkeypatch):
    calls = _stub(monkeypatch, lambda u, n: [])
    SC.score_all([_cand("X Summit", "2099-06-02")], PROFILE)
    assert "dates: 2099-06-02" in calls[0]["user"]
    assert '"starts_on"' in calls[0]["system"]
    assert "exactly match the name you were given" in calls[0]["system"]


def test_the_score_key_carries_the_edition():
    assert SC.score_key("A", "2026-06-02") != SC.score_key("A", "2027-06-08")
    assert SC.score_key("A") == SC.score_key("A", "not a date")


# ── 7a. absolute bands, not a curve ──────────────────────────────────────

def test_the_prompt_grades_against_fixed_bands_not_batch_mates(monkeypatch):
    calls = _stub(monkeypatch, lambda u, n: [])
    SC.score_all([_cand("X")], PROFILE)
    system = calls[0]["system"]
    assert "mediocre" not in system and "75 and 85" not in system
    for band in ("30-40", "20-29", "10-19", "0-9", "15-20", "10-14", "5-9", "0-4"):
        assert band in system, band
    assert "never against the" in system


# ── 7c. search budget per event ──────────────────────────────────────────

def test_a_small_batch_gets_a_small_search_budget(monkeypatch):
    calls = _stub(monkeypatch, lambda u, n: [])
    SC.score_all([_cand("Solo")], PROFILE)
    assert calls[0]["max_uses"] == 1
    assert SC.max_uses_for([{}] * 6) == 6 == SC.max_uses_for([{}] * 9)


# ── 7b. the borderline re-score ──────────────────────────────────────────

def test_an_event_on_the_relevance_gate_is_scored_three_times(monkeypatch):
    rels = {1: 23, 2: 26, 3: 25}
    calls = _stub(monkeypatch, lambda u, n: [_reply("Edge", rel=rels[n], dm=34, eng=16)])
    out = SC.score_all([_cand("Edge")], PROFILE)
    c = out["scored"][0]
    assert len(calls) == 3
    assert c["relevance"] == 25 and c["relevance_note"] == "r25"
    assert c["rescored"] is True
    assert c["score_readings"][0]["relevance"] == 23
    assert c["score_spread"]["relevance"] == 3
    assert "scored three times" in c["rescore_note"]
    assert "median" in c["rescore_note"]
    assert out["rescore"]["events"] == ["Edge"] and out["rescore"]["calls"] == 2
    # Every call is in the spend, the re-scores included.
    assert out["spend"]["calls"] == 3
    assert out["rescore"]["spend"]["calls"] == 2


def test_the_rescore_requests_differ_so_the_call_cache_cannot_replay_one(monkeypatch):
    """event_intel_jobs.reserve_call returns the stored reply for an
    identical request in the same run and stage."""
    calls = _stub(monkeypatch, lambda u, n: [_reply("Edge", rel=24, dm=34, eng=16)])
    SC.score_all([_cand("Edge")], PROFILE)
    users = [c["user"] for c in calls]
    assert len(set(users)) == 3
    assert "pass 2 of 3" in users[1] + users[2] and "pass 3 of 3" in users[1] + users[2]


@pytest.mark.parametrize("line", R.BORDERLINE_TOTALS)
def test_a_total_on_any_line_is_rescored(monkeypatch, line):
    rel = 30
    dm = line - rel - 10
    calls = _stub(monkeypatch, lambda u, n: [_reply("T", rel=rel, dm=dm, eng=10)])
    SC.score_all([_cand("T")], PROFILE)
    assert len(calls) == 3


def test_an_event_clear_of_every_line_is_scored_once(monkeypatch):
    calls = _stub(monkeypatch, lambda u, n: [_reply("Clear", rel=34, dm=30, eng=12)])
    out = SC.score_all([_cand("Clear")], PROFILE)   # 76, relevance 34
    assert len(calls) == 1
    assert "rescored" not in out["scored"][0]


def test_the_margin_is_the_line_it_says_it_is(monkeypatch):
    for rel, expect in ((R.RELEVANCE_GATE + R.BORDERLINE_MARGIN, 3),
                        (R.RELEVANCE_GATE + R.BORDERLINE_MARGIN + 1, 1)):
        # dm/eng keep the total away from every total line.
        calls = _stub(monkeypatch, lambda u, n, rel=rel: [_reply("M", rel=rel, dm=36, eng=20)])
        SC.score_all([_cand("M")], PROFILE)
        assert len(calls) == expect, (rel, len(calls))


def test_the_extra_calls_are_capped(monkeypatch):
    names = ["Edge %d" % i for i in range(9)]

    def answer(user, n):
        return [_reply(x, rel=24, dm=34, eng=16) for x in names if "- %s\n" % x in user + "\n"]
    calls = _stub(monkeypatch, answer)
    out = SC.score_all([_cand(x) for x in names], PROFILE)
    assert len(calls) == 2 + SC.RESCORE_MAX_BATCHES
    assert len(out["rescore"]["events"]) == SC.BATCH
    assert len(out["rescore"]["not_rescored"]) == 3


def test_a_failed_rescore_keeps_the_first_reading_and_says_why(monkeypatch):
    def answer(user, n):
        if n > 1:
            return {"error": {"kind": "max_tokens",
                              "detail": "Ran out (stop_reason=max_tokens). Raise max_tokens or lower max_uses."}}
        return [_reply("Edge", rel=23, dm=34, eng=16)]
    _stub(monkeypatch, answer)
    out = SC.score_all([_cand("Edge")], PROFILE)
    c = out["scored"][0]
    assert c["relevance"] == 23 and "rescored" not in c
    assert out["errors"] and all("max_" not in e for e in out["errors"])


def test_two_readings_take_the_lower_middle_never_rounding_up(monkeypatch):
    def answer(user, n):
        if n == 3:
            return {"error": {"kind": "transport", "detail": "HTTP 503"}}
        return [_reply("Edge", rel={1: 23, 2: 26}[n], dm=34, eng=16)]
    _stub(monkeypatch, answer)
    c = SC.score_all([_cand("Edge")], PROFILE)["scored"][0]
    assert c["relevance"] == 23
    assert "scored twice" in c["rescore_note"]


def test_the_rescore_caveat_reaches_the_row_and_the_report():
    row = {"name": "Edge", "rescore_note": SC._rescore_note(
        [_reply("E", rel=23), _reply("E", rel=26), _reply("E", rel=25)])}
    gaps = R.gaps_for(row)
    assert any(R.is_rescore_note(g) for g in gaps)
    # Not counted as an unmeasured field; reported as its own line.
    only = {"name": "Edge", "gaps": [g for g in gaps if R.is_rescore_note(g)]}
    facts = REP.notes(shortfall=[], audit={}, generic={}, candidates=[only],
                      scoring_errors=[], interchangeable=[], banned=[], thin=[],
                      unscored=[], rescored=[only])
    heads = [f["head"] for f in facts]
    assert not any("unmeasured field" in h for h in heads), heads
    assert any("scored again" in h for h in heads), heads


# ── 6. no developer detail in what the scorer stores ─────────────────────

def test_a_failed_batch_stores_a_reader_reason(monkeypatch):
    _stub(monkeypatch, lambda u, n: {"error": {
        "kind": "max_tokens",
        "detail": "Ran out of output budget before finishing "
                  "(stop_reason=max_tokens). Raise max_tokens or lower max_uses."}})
    out = SC.score_all([_cand("A")], PROFILE)
    [err] = out["errors"]
    assert claude_websearch.reader_reason("max_tokens") in err
    for token in ("max_tokens", "max_uses", "Raise", "stop_reason", ".."):
        assert token not in err, err


def test_a_crashed_batch_does_not_print_the_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("psycopg2.OperationalError: SECRET-HOST")
    monkeypatch.setattr(SC, "score_batch", boom)
    out = SC.score_all([_cand("A")], PROFILE)
    assert out["errors"] and "SECRET-HOST" not in out["errors"][0]


# ── 8. the grader is not primed about a promoted event ───────────────────

def test_a_promoted_event_is_briefed_like_any_other():
    c = _cand("INBOUND", audit_verdict="promoted",
              category_fit="On this list because the famous-event audit cut X "
                           "and named this as the more targeted alternative.")
    brief = SC._candidate_brief(c)
    assert "more targeted alternative" not in brief and "audit" not in brief
    plain = SC._candidate_brief(_cand("Other", category_fit="A vertical summit."))
    assert "found as: A vertical summit." in plain
