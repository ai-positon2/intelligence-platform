"""Three facts the recommendation collected on every candidate and showed on
none of them, plus the batching that decided how comparable the scores were.

Format, confidence and the cited pages were all read by discovery from the
first version. `format` had no column, so it was dropped at write time;
`confidence` and `sources` reached the database and never reached the page.
Each of them qualifies the score it sits next to:

  * an 82 you fly to and an 82 you watch are not the same proposition;
  * a row the search could not firmly confirm is a row whose facts are softer
    than the row above it, and nothing said so;
  * a score you cannot trace to a page is one you have to take on trust.

The batching is the same kind of problem one level up. Scoring exists so that
ONE standard is applied to all six categories instead of six finders grading
their own. Past six candidates that is more than one call, and the calls were
sliced out of a list ordered by category, so one grader saw only flagships and
another only side events. They are dealt now, and the report says how many
graders there were.
"""

import os
import sys

import pytest

from tracker import event_intel_report as report
from tracker import event_intel_rubric as R
from tracker import event_intel_scorer as scorer
from tracker import event_intel_store as S

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_charts import _cand, _recommend, _render  # noqa: E402
from test_event_intel_event_view import page_script  # noqa: E402,F401


def _row(**kw):
    r = {"name": "PMM Summit", "category": R.CAT_VERTICAL_SUMMIT,
         "relevance": 34, "dm_access": 33, "engagement": 16,
         "relevance_note": "n", "dm_access_note": "n", "engagement_note": "n",
         "website": "https://pmm.example", "attendees": "600",
         "starts_on": "2027-05-01", "ends_on": "2027-05-03"}
    r.update(kw)
    return r


# ── format survives the write ─────────────────────────────────────────────

@pytest.mark.parametrize("raw,stored", [
    ("in_person", "in_person"), ("virtual", "virtual"), ("hybrid", "hybrid"),
    ("In Person", "in_person"), ("in-person", "in_person"),
])
def test_the_format_discovery_read_reaches_the_stored_row(raw, stored):
    assert S.normalise_candidate(_row(format=raw))["format"] == stored


@pytest.mark.parametrize("raw", ["TBC", "online-ish", "", None, "to be announced"])
def test_a_format_outside_the_closed_set_is_dropped_rather_than_stored(raw):
    """"TBC" rendered on a card reads as a fact about the event. The three
    formats are a closed set for the same reason the rubric's orientation is:
    a value nobody recognises must not be shown as if it meant something."""
    assert S.normalise_candidate(_row(format=raw))["format"] is None


def test_format_is_a_column_the_write_path_actually_names():
    """The defect this replaces: discovery read the format on every candidate
    and `CAND_FIELDS` had no place to put it, so every read was discarded
    silently at insert time."""
    assert "format" in S._CANDIDATE_FIELDS
    assert "format" in S.normalise_candidate(_row())


def test_a_rejected_format_is_reported_as_unknown_not_as_silence():
    """The two normalisations have to agree. If the gap check reads the raw
    string while the card reads the normalised one, a format the closed set
    rejected shows no chip AND no gap, and reads as though nobody asked."""
    gaps = S.normalise_candidate(_row(format="TBC"))["gaps"]
    assert any("in person, online or both" in g for g in gaps)


def test_a_known_format_raises_no_gap():
    gaps = S.normalise_candidate(_row(format="virtual"))["gaps"]
    assert not any("in person, online or both" in g for g in gaps)


def test_an_event_with_no_cited_page_says_so():
    gaps = R.gaps_for({"attendees": "1", "website": "https://x.example",
                       "starts_on": "2027-01-01", "format": "hybrid",
                       R.DIM_RELEVANCE: 30, R.DIM_DM_ACCESS: 30,
                       R.DIM_ENGAGEMENT: 10,
                       R.DIM_RELEVANCE + "_note": "y",
                       R.DIM_DM_ACCESS + "_note": "y",
                       R.DIM_ENGAGEMENT + "_note": "y"})
    assert len(gaps) == 1 and "can be checked against the organiser" in gaps[0]


# ── the card shows them ───────────────────────────────────────────────────

def test_the_card_says_whether_you_have_to_fly_there(page_script):
    body = _render(page_script, _recommend([
        _cand("Virtual One", 84, "P1", format="virtual"),
        _cand("Real One", 82, "P1", format="in_person")]))
    assert "Virtual" in body and "In person" in body


def test_the_qualifying_facts_are_not_hidden_behind_the_disclosure(page_script):
    """These qualify every other fact on the card, so they sit above the score
    rather than inside "Why these scores". Innerhtml cannot answer "is this
    visible", but it can answer these two, and the mutant that put `hidden` on
    the block survived every other test in this file."""
    body = _render(page_script, _recommend([
        _cand("Real One", 82, "P1", format="in_person", confidence="low")]))
    flags = body.index('class="cflags"')
    assert "<div class=\"cflags\">" in body, "the block must not be hidden or gated"
    assert flags < body.index('class="cwhy"'), "flags must precede the disclosure"


def test_an_unknown_format_says_it_is_unknown_rather_than_showing_nothing(page_script):
    """Silence would read as in person, because most events are."""
    body = _render(page_script, _recommend([_cand("Mystery", 84, "P1")]))
    assert "Format not stated" in body


def test_a_low_confidence_row_is_marked_on_the_card(page_script):
    body = _render(page_script, _recommend([
        _cand("Shaky", 84, "P1", confidence="low", format="hybrid")]))
    assert "Low confidence" in body


def test_a_confident_row_carries_no_confidence_badge(page_script):
    body = _render(page_script, _recommend([
        _cand("Solid", 84, "P1", confidence="high", format="hybrid")]))
    assert "Low confidence" not in body


def test_the_pages_behind_a_score_are_linked_from_the_card(page_script):
    body = _render(page_script, _recommend([
        _cand("Traceable", 84, "P1",
              sources=["https://ev.example/exhibit", "https://ev.example/about"])]))
    assert "https://ev.example/exhibit" in body
    assert "Checked against" in body


def test_a_candidate_with_no_sources_grows_no_empty_source_block(page_script):
    body = _render(page_script, _recommend([_cand("Bare", 84, "P1")]))
    assert "Checked against" not in body


def test_a_source_that_is_not_a_real_link_is_not_rendered_as_one(page_script):
    """The same rule the rest of this page keeps: these strings arrive from a
    model, so a javascript: URL must never become an href."""
    body = _render(page_script, _recommend([
        _cand("Sketchy", 84, "P1",
              sources=["javascript:alert(1)", "https://ok.example/x"])]))
    assert "javascript:" not in body
    assert "https://ok.example/x" in body


# ── how many graders there were ───────────────────────────────────────────

def _cands(n):
    return [{"name": "E%d" % i, "category": R.CATEGORIES[i % len(R.CATEGORIES)]}
            for i in range(n)]


def test_a_short_list_is_graded_in_one_pass():
    assert len(scorer.deal(_cands(6))) == 1
    assert scorer.deal([]) == []


def test_every_candidate_is_dealt_exactly_once():
    got = [c["name"] for b in scorer.deal(_cands(17)) for c in b]
    assert sorted(got) == sorted(c["name"] for c in _cands(17))


def test_no_grading_pass_sees_only_one_kind_of_event():
    """The defect: `merge()` returns candidates in CATEGORIES order, so a
    contiguous slice of six was one or two categories. One grader saw nothing
    but industry flagships and another nothing but side events, and "dense
    with the right buyers" means a different thing to each of them."""
    cands = []
    for cat in R.CATEGORIES:
        cands += [{"name": "%s-%d" % (cat, i), "category": cat} for i in range(3)]
    batches = scorer.deal(cands)
    assert len(batches) == 3
    # Not "more than one category": a slice of six off a list with three per
    # category still spans two, and a two-category grader is the defect. Every
    # pass must see the whole spread.
    for batch in batches:
        assert {c["category"] for c in batch} == set(R.CATEGORIES)


def test_the_number_of_passes_is_reported(monkeypatch):
    seen = []

    def fake(batch, profile):
        seen.append(len(batch))
        return {"scores": {}, "error": None}

    monkeypatch.setattr(scorer, "score_batch", fake)
    got = scorer.score_all(_cands(13), {})
    assert got["batches"] == 3 == len(seen)


def _assumptions(**kw):
    base = dict(shortfall=[], audit={}, generic={}, candidates=[],
                scoring_errors=[], interchangeable=[], banned=[], thin=[],
                unscored=[])
    base.update(kw)
    return report.assumptions(**base)


def test_more_than_one_grading_pass_is_disclosed():
    lines = _assumptions(candidates=_cands(13), scoring_batches=3)
    assert any("graded in 3 separate passes" in x for x in lines)
    assert any("dealt evenly" in x for x in lines)


def test_a_single_pass_makes_no_claim_about_passes():
    """A report that says "graded in 1 separate pass" over six events has
    turned a caveat into noise."""
    lines = _assumptions(candidates=_cands(4), scoring_batches=1)
    assert not any("separate passes" in x for x in lines)
    assert not any("separate passes" in x for x in _assumptions(candidates=_cands(4)))


def test_the_disclosed_total_counts_the_unscored_too():
    """They were handed to a grader like everything else. Counting only the
    survivors would understate how much was spread across passes.

    The head lost its leading "The" when the count learned to agree with its
    own verb: "The 1 events were graded in 2 separate passes" reached a
    client, and "The 1 event was" is no better. The number and the verb come
    from one place now, so the article had to go.
    """
    lines = _assumptions(candidates=_cands(10), unscored=_cands(3),
                         scoring_batches=3)
    assert any("13 events were graded" in x for x in lines)
