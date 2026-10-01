"""The answer line, the qualifier under it, and the one reading of a category
that the ring, the coverage chart and the verdict share. Executed in node
through the page's own render(), never grepped.

The defect these pin: the headline was replaced by "Research incomplete" on
every production run, while the ring beside it said "6/6 categories
delivered" and a note told the reader to review unscored events on a run that
had none.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_event_view import page_script  # noqa: E402,F401
from test_event_intel_charts import _render, _render_parts, _recommend, _cand  # noqa: E402

VERIFY = ("1 of the 2 candidates found here could not be checked to a "
          "conclusion (The check ran but its answer could not be read.).")


def _answer(html):
    return re.search(r'<div class="al">(.*?)</div>', html).group(1)


def _qualifier(html):
    m = re.search(r'<div class="az aq"[^>]*>(.*?)</div>', html)
    return m.group(1) if m else ""


def _ring(html):
    m = re.search(r'<div class="ad">(.*?)</div>\s*</div>\s*</div>', html, re.S)
    return re.sub(r"<[^>]+>", " ", m.group(1)) if m else ""


def _sf(cat, status, why, found=0, **kw):
    return dict({"category": cat, "label": cat.replace("_", " ").title(), "status": status,
                 "found": found, "quota": 2, "short_by": 2 - found, "why": why}, **kw)


def test_the_answer_line_survives_a_provisional_run(page_script):
    html = _render(page_script, _recommend(
        [_cand("Winner", 90, "P1")], completion_state="partial",
        completion={"state": "partial", "gaps": ["x"],
                    "qualifier": "Treat this as provisional: scoring reported 1 error."}))
    assert _answer(html) == "1 must-attend event"
    assert _qualifier(html) == "Treat this as provisional: scoring reported 1 error."
    assert "Research incomplete" not in html
    assert "—" not in html


def test_a_stored_completion_with_nothing_material_says_nothing(page_script):
    html = _render(page_script, _recommend(
        [_cand("Winner", 90, "P1")], completion_state="complete",
        completion={"state": "complete", "gaps": [], "qualifier": ""}))
    assert _qualifier(html) == ""


@pytest.mark.parametrize("extra", [
    # Old runs: 'partial' set by one unsettled candidate, by a sold-out
    # event, by an event outside the window. None of it is material.
    {"completion_state": "partial"},
    {"completion_state": "partial",
     "shortfall": [_sf("side_event", "partial", VERIFY, found=1)]},
    {"completion_state": "partial", "unscored": [
        {"name": "Sold Summit", "note": "This edition is sold out; access must be "
                                        "resolved before recommending attendance."},
        {"name": "Old Expo", "note": "The edition is outside the requested date "
                                     "window or has invalid dates. The location could "
                                     "not be verified against the client geography."}]},
])
def test_an_old_run_with_nothing_material_keeps_a_clean_answer(page_script, extra):
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")], **extra))
    assert _answer(html) == "1 must-attend event"
    assert _qualifier(html) == ""


@pytest.mark.parametrize("extra,phrase", [
    ({"shortfall": [_sf("side_event", "error", "The search could not be completed.")]},
     "the search for 1 kind of event did not finish (Side Event)"),
    ({"shortfall": [_sf("side_event", "partial", "The search reported it could not be finished.")]},
     "did not finish"),
    ({"unscored": [{"name": "Unresolved summit", "note": "The edition dates need confirmation."}]},
     "1 event could not be scored for lack of evidence"),
    ({"unscored": [{"name": "X", "note": "The scoring pass returned no result for this event."}]},
     "the scoring pass returned no result for 1 event"),
    ({"audit": {"checked": 2, "failed": {"a": {"name": "A"}}}},
     "audit could not be completed for 1 event"),
    ({"notes": [{"level": "gap", "head": "Scoring reported 2 errors", "detail": "x"}]},
     "scoring reported 2 errors"),
])
def test_an_old_run_with_a_material_gap_gets_a_qualifier(page_script, extra, phrase):
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")], **extra))
    assert _answer(html) == "1 must-attend event"
    assert phrase in _qualifier(html)


def test_a_budget_spent_search_is_not_a_hole_in_the_ring_either(page_script):
    """coverageReason re-reads a stored max_uses_exceeded as a spent budget;
    the ring used to skip that and count it as a hole."""
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")], shortfall=[
        _sf("side_event", "error", "search_limit: max_uses_exceeded")]))
    ring = _ring(html)
    assert "did not finish" not in ring and "came up short" in ring
    assert _qualifier(html) == ""


def test_a_cut_off_partial_is_a_hole_in_the_ring_and_in_coverage(page_script):
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")], shortfall=[
        _sf("side_event", "partial", "The search reported it could not be finished.")]))
    assert "1 did not finish" in _ring(html)
    assert "The search did not finish" in html


def test_an_unsettled_partial_reads_one_way_everywhere(page_script):
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")], shortfall=[
        _sf("side_event", "partial", VERIFY, found=1)]))
    assert "1 came up short" in _ring(html) and "did not finish" not in _ring(html)
    assert "Searched to the end, but part of what it found could not be checked" in html
    assert "The search did not finish" not in html
    # And it is not called a finding about the market.
    assert "fact about this client" not in html.split("Category coverage")[1].split("Why six")[0]


def test_an_empty_category_is_a_fact_only_after_a_finished_search(page_script):
    done = _render(page_script, _recommend([_cand("Winner", 90, "P1")], shortfall=[
        _sf("side_event", "empty", "Nothing here.")]))
    assert "after a search that finished" in done
    spent = _render(page_script, _recommend([_cand("Winner", 90, "P1")], shortfall=[
        _sf("side_event", "error", "search_limit: max_uses_exceeded")]))
    assert "under-searched rather than settled" in spent


def test_a_zero_candidate_banner_never_contradicts_the_verdict(page_script):
    legacy = ("No verified candidates survived this run. Review the category "
              "results below: incomplete searches or verification do not "
              "establish that the market has no suitable events.")
    finished = _render(page_script, _recommend([], no_candidates=True, note=legacy,
        shortfall=[_sf(c, "empty", "Nothing here.") for c in ("side_event", "emerging")]))
    assert "Every category search finished" in finished and "do not establish" not in finished
    broken = _render(page_script, _recommend([], no_candidates=True, note=legacy,
        shortfall=[_sf("side_event", "error", "x"), _sf("emerging", "empty", "Nothing.")]))
    assert "do not establish that the market" in broken
    assert "hole in the analysis" in broken


def test_the_old_always_on_incomplete_note_is_dropped_when_nothing_is_missing(page_script):
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")],
        completion_state="partial",
        notes=[{"level": "note", "head": "Worth knowing", "detail": "d"},
               {"level": "warn", "head": "Research is incomplete",
                "detail": "Some events or checks remain unverified. Review the "
                          "coverage and unscored events before acting."}]))
    assert "Research is incomplete" not in html
    assert "Review the coverage and unscored events" not in html


def test_the_provisional_note_matches_the_qualifier_and_leads(page_script):
    q = "Treat this as provisional: scoring reported 1 error."
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")],
        completion={"state": "partial", "gaps": ["x"], "qualifier": q},
        notes=[{"level": "note", "head": "Attendance claims", "detail": "d"},
               {"level": "warn", "head": "This shortlist is provisional", "detail": q}]))
    notes = html.split("What was not measured")[1]
    assert notes.index("This shortlist is provisional") < notes.index("Attendance claims")
    assert notes.count("This shortlist is provisional") == 1
    assert 'n-warn' not in notes  # drawn with the hole styling that exists


@pytest.mark.parametrize("n,phrase", [(1, "It remains unverified, not because it was"),
                                      (2, "They remain unverified, not because they were")])
def test_the_unchecked_warning_agrees_with_its_count(page_script, n, phrase):
    st = {"side_event": {"proposed": 2 + n, "found": 2, "kept": 2, "rejected": [],
                         "unverified": [{"name": "U%d" % i, "reason": "r"} for i in range(n)],
                         "label": "Side event", "status": "partial"}}
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")], statuses=st))
    assert phrase in html
