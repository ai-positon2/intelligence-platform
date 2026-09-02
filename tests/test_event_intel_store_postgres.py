"""The store against a REAL Postgres.

Why this file exists. Every other test in this suite runs with DATABASE_URL
unset, which the persistence tests state plainly: "DATABASE_URL is unset under
test, so every read path must return a safe [default]". That is a reasonable
way to test the pure half, and it means no SQL in this subsystem was ever
executed by a test.

So the suite was green while `save_candidates` built an INSERT that never named
`run_id`, a NOT NULL column. Every insert it has ever attempted raised, was
caught, and returned 0; `get_candidates` then filtered on `run_id` and returned
nothing. The recommend play, the agent's headline mode, produced an empty list
of events on every run, and 443 passing tests said otherwise.

A green suite is not the gate. These tests skip when no database is configured,
and they are the ones that would have caught it. Point DATABASE_URL at a
throwaway database to run them:

    createdb evi_test
    DATABASE_URL=postgresql://localhost/evi_test pytest tests/test_event_intel_store_postgres.py
"""

import datetime
import os

import pytest

from tracker import event_intel_rubric as R
from tracker import event_intel_store as S

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs a real Postgres; set DATABASE_URL to a throwaway database")

EMAIL = "store-test@position2.com"


def _soon(days: int = 90) -> str:
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


@pytest.fixture()
def run():
    return S.save_run(EMAIL, "recommend", "postgres store test")


def _cand(name, **over):
    d = {"name": name, "category": R.CAT_VERTICAL_SUMMIT,
         "relevance": 32, "dm_access": 30, "engagement": 16,
         "relevance_note": "dense", "dm_access_note": "booths",
         "engagement_note": "buying", "description": "A real event.",
         "website": "https://x.example",
         "starts_on": _soon(), "ends_on": _soon(93)}
    d.update(over)
    return d


# ── runs ──────────────────────────────────────────────────────────────────

def test_a_run_round_trips(run):
    assert isinstance(run, int)
    S.update_run(run, status="running", stage="scoring")
    got = S.get_run(run, EMAIL)
    assert got and got["stage"] == "scoring"
    assert any(r["id"] == run for r in S.list_runs(EMAIL))


def test_a_run_is_not_readable_by_another_signed_in_user(run):
    assert S.get_run(run, "someone-else@position2.com") is None


# ── candidates: the path that was silently dead ───────────────────────────

def test_candidates_actually_reach_the_database(run):
    """The regression. This is the whole recommend mode's only storage."""
    saved = S.save_candidates(run, [_cand("Fintech Growth Summit"),
                                    _cand("PMM World", category=R.CAT_EMERGING)])
    assert saved == 2, "save_candidates reported it wrote nothing"
    back = S.get_candidates(run)
    assert len(back) == 2, "get_candidates could not find what was just saved"
    assert {c["name"] for c in back} == {"Fintech Growth Summit", "PMM World"}


def test_every_saved_candidate_carries_its_run_id(run):
    """The exact defect: the INSERT did not name run_id, so the column was
    NULL, so the WHERE clause that reads them back matched nothing."""
    S.save_candidates(run, [_cand("Anchored Event")])
    other = S.save_run(EMAIL, "recommend", "a different run")
    assert S.get_candidates(other) == [], "a candidate leaked across runs"
    assert len(S.get_candidates(run)) == 1


def test_the_total_that_comes_back_is_recomputed_from_its_sub_scores(run):
    S.save_candidates(run, [_cand("Recompute Me", total=999)])
    assert S.get_candidates(run)[0]["total"] == 78


def test_one_unwritable_row_does_not_empty_the_run(run):
    """A batch insert is all-or-nothing. One event whose date the model wrote
    as "Q2 2026" used to abort the statement and take every other event in the
    run with it, and the run still reported itself complete."""
    rows = [_cand("Good A"), _cand("Good B"),
            _cand("Vague", starts_on="Q2 2026", ends_on=None),
            _cand("Good C")]
    S.save_candidates(run, rows)
    back = S.get_candidates(run)
    assert len(back) == 4, "expected all four, got %s" % [c["name"] for c in back]
    vague = [c for c in back if c["name"] == "Vague"][0]
    assert vague["starts_on"] is None
    assert vague["quarter"] == "Q2 2026", "the reader lost the only timing given"


def test_cross_run_history_can_see_a_completed_run(run):
    S.save_candidates(run, [_cand("Historic Summit")])
    S.update_run(run, status="complete", stage="done")
    prior = S.prior_candidate_names(EMAIL)
    assert any("Historic Summit" in (p["names"] or []) for p in prior)


# ── events, participants, sources ─────────────────────────────────────────

def test_an_event_and_its_roster_round_trip(run):
    eid = S.save_event(run, {"name": "Money20/20 USA",
                             "website": "https://m.example",
                             "starts_on": _soon(), "location": "Las Vegas"})
    assert isinstance(eid, int)
    assert len(S.get_events(run)) == 1

    n = S.save_participants(run, eid, [
        {"org_name": "Acme Payments", "org_domain": "acme.com",
         "role": "exhibitor", "source_url": "https://m.example/exhibitors",
         "provenance": "page"},
        {"org_name": "Globex", "org_domain": "globex.io", "role": "sponsor",
         "source_url": "https://m.example/sponsors", "provenance": "page",
         "person_name": "Jane Doe", "person_title": "VP Marketing"}])
    assert n == 2
    assert len(S.get_participants(run)) == 2

    S.save_source(run, eid, "https://m.example/exhibitors", "exhibitor_list", "ok")
    assert len(S.get_sources(run)) == 1


def test_a_participant_with_no_source_is_refused(run):
    """Provenance discipline: a row nobody can trace back to a page is not a
    row, and guessing where it came from is the fabrication this agent exists
    to avoid."""
    assert S.save_participants(run, None, [
        {"org_name": "Nowhere Inc", "role": "exhibitor"}]) == 0


# ── outreach and outcomes ─────────────────────────────────────────────────

def test_outreach_rows_round_trip(run):
    w = S.save_run(EMAIL, "workroom", "work the room")
    n = S.save_outreach(w, run, "Money20/20 USA", "competitor", [
        {"org_name": "Acme Payments", "org_domain": "acme.com",
         "role": "exhibitor", "fit": "strong", "fit_note": "ICP match",
         "angle": "payments", "opener": "I saw Acme at Money20/20.",
         "draft_status": "kept"}])
    assert n == 1
    assert len(S.get_outreach(w)) == 1


def test_an_outcome_is_recorded_against_the_event_not_the_run():
    S.save_outcome(EMAIL, "Fintech Growth Summit", "going", note="booked")
    out = S.get_outcomes(EMAIL)
    assert out, "the decision did not come back"


def test_an_unknown_decision_is_refused_rather_than_stored():
    with pytest.raises(ValueError):
        S.save_outcome(EMAIL, "Some Event", "attending")
