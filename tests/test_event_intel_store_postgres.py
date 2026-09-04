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


def test_the_three_qualifying_facts_survive_the_round_trip(run):
    """format, confidence and the cited pages, through real SQL.

    `format` is the one that had no column at all, so every read of it was
    discarded at insert time and the pure tests could not tell. This is the
    same shape as the defect this whole file was opened for: a column the
    INSERT names and the table does not have raises, gets caught, and returns
    a count nobody checks."""
    assert S.save_candidates(run, [
        _cand("Virtual One", format="virtual", confidence="low",
              sources=["https://v.example/expo"]),
        _cand("Unstated One", format="TBC")]) == 2
    by = {r["name"]: r for r in S.get_candidates(run)}
    assert by["Virtual One"]["format"] == "virtual"
    assert by["Virtual One"]["confidence"] == "low"
    assert by["Virtual One"]["sources"] == ["https://v.example/expo"]
    # Outside the closed set, so it lands as NULL rather than as a format.
    assert by["Unstated One"]["format"] is None


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


# ── the recommend path, end to end, against this database ────────────────
#
# The gap that hid the promotion bug. Every stage was unit-tested and the
# wiring between two of them was wrong in a way no unit test could see: the
# pipeline handed promote_alternatives the one list that could not contain the
# event each alternative replaces, so every promoted event was built with no
# category and dropped by normalise_candidate. The summary went on saying it
# had been "added to this list".
#
# The same shape as the run_id defect this file was opened for, and the same
# lesson: the only test that catches it is one that runs the real path and
# then READS BACK what landed.

def _profile():
    return {"client_name": "Pipeline E2E", "website": "https://e2e.example",
            "classification": R.CLASS_B2B_TO_MARKETING,
            "buyer_roles": "VP Marketing", "verticals": "digital health",
            "window_months": 12, "max_events": 15}


def _scores(name, **over):
    d = {"name": name, "relevance": 33, "relevance_note": "dense",
         "dm_access": 30, "dm_access_note": "reachable",
         "engagement": 15, "engagement_note": "buying",
         "description": "A real description of %s." % name,
         "client_line": "Why %s matters to this client." % name}
    d.update(over)
    return d


def test_a_promoted_alternative_reaches_the_stored_candidates(monkeypatch):
    """One marquee event, cut, with a named replacement. The replacement has
    to end up in evi_candidates: it cost a live confirmation search and a live
    scoring call, and the summary claims it is on the list."""
    from tracker import event_intel_audit as A
    from tracker import event_intel_discover as D
    from tracker import event_intel_pipeline as P
    from tracker import event_intel_scorer as SC

    discovered = [
        {"name": "MarTech Conference", "famous": True, "website": "https://mc.example",
         "category": R.CAT_INDUSTRY_FLAGSHIP, "city": "Online", "format": "virtual",
         "starts_on": _soon(120), "ends_on": _soon(122), "sources": ["https://mc.example"],
         "confidence": "high"},
        {"name": "Health Growth Collective", "famous": False, "city": "Austin",
         "category": R.CAT_EMERGING, "website": "https://hgc.example",
         "starts_on": _soon(90), "ends_on": _soon(91), "format": "in_person",
         "sources": ["https://hgc.example"], "confidence": "high"},
    ]
    verdict = {"verdict": A.VERDICT_CUT, "alternative": "INBOUND",
               "alternative_website": "https://inbound.example",
               "alternative_note": "It has a real exhibit floor.",
               "why": "Fully online.", "name": "MarTech Conference"}
    audit = {"verdicts": {A.name_key("MarTech Conference"): verdict},
             "cut": [dict(verdict)], "kept": [], "checked": 1, "error": None}

    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [dict(c) for c in discovered], "shortfall": [],
        "statuses": {}, "categories_failed": 0, "found": len(discovered),
        "by_category": {}, "categories_searched": 6})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous",
                        lambda c, p: {k: (list(v) if isinstance(v, list) else v)
                                      for k, v in audit.items()})
    monkeypatch.setattr(P.event_intel_resolve, "resolve_event",
                        lambda name, year_hint=None: {
                            "ok": True, "confidence": "high",
                            "pages": [{"url": "https://inbound.example/exhibitors"}],
                            "event": {"name": "INBOUND", "website": "https://inbound.example",
                                      "starts_on": _soon(200), "ends_on": _soon(202),
                                      "organizer": "HubSpot", "location": "Boston, MA",
                                      "stated_size": "11,000 attendees",
                                      "format": "in_person", "confidence": "high"}})
    # promote_alternatives imports its default resolver inside the function
    # body, so patching the module attribute above is what reaches it. No
    # second patch is needed and adding one only hides which line matters.
    monkeypatch.setattr(SC, "score_batch", lambda batch, profile: {
        "scores": {SC.score_key(c["name"]): SC._clean(_scores(c["name"]))
                   for c in batch}, "error": None})
    monkeypatch.setattr(P.event_intel_scorer, "score_batch", SC.score_batch)

    run_id = S.save_run(EMAIL, "recommend", "pipeline e2e")
    P._run_recommend(run_id, EMAIL, _profile())

    row = S.get_run(run_id, EMAIL)
    assert row["status"] == "complete", row.get("error")
    names = [c["name"] for c in S.get_candidates(run_id)]
    assert "MarTech Conference" not in names, "the cut event survived the audit"
    assert "INBOUND" in names, (
        "the audit's replacement never reached the table, and the summary "
        "still says it did: %s" % names)

    stored = [c for c in S.get_candidates(run_id) if c["name"] == "INBOUND"][0]
    assert stored["category"] in R.CATEGORIES, stored["category"]
    assert stored["audit_verdict"] == A.VERDICT_PROMOTED
    assert stored["total"] == 78, stored["total"]
    assert D.name_key("INBOUND")  # the key the summary and the table share


def test_the_summary_never_claims_a_promotion_the_table_does_not_hold(monkeypatch):
    """The claim, checked against the rows. This is the sentence that was
    false for every run: "named by the audit ... then confirmed separately and
    added to this list", printed while the list held nothing of the sort."""
    from tracker import event_intel_pipeline as P
    test_a_promoted_alternative_reaches_the_stored_candidates(monkeypatch)
    runs = S.list_runs(EMAIL)
    run = S.get_run(runs[0]["id"], EMAIL)
    claimed = [p["name"] for p in
               ((run["summary"] or {}).get("audit") or {}).get("promoted") or []]
    names = {c["name"] for c in S.get_candidates(run["id"])}
    missing = [n for n in claimed if n not in names]
    assert not missing, (
        "the summary says these were added to the list and the table does "
        "not hold them: %s" % missing)
    assert P  # the path under test is the pipeline's, not a reconstruction


# ── the second tier, through the real path and read back ─────────────────
#
# The complaint this answers was measured on live clients: one run returned a
# single event and another returned none, while events that were real,
# upcoming, cited and aimed at the client's own buyers were discarded for
# scoring in the sixties. The fix splits the bar (which ranks) from the
# relevance gates (which decide what is an option at all).
#
# A unit test on `rank` proves the partition. It cannot prove the tier
# survives the store and reaches the summary the page reads, which is exactly
# the gap that let the promotion defect above ship inert for weeks.

def test_a_well_matched_event_below_the_bar_reaches_the_stored_summary(monkeypatch):
    """The whole point, end to end. One event clears 70, two score in the
    sixties with the right audience, one is for the wrong audience entirely.
    The run has to come back with one recommendation, two options and one
    cut, and the page reads all three off the stored summary."""
    from tracker import event_intel_pipeline as P
    from tracker import event_intel_scorer as SC

    discovered = [
        {"name": "Strong Flagship", "famous": False, "city": "Chicago",
         "category": R.CAT_INDUSTRY_FLAGSHIP, "website": "https://sf.example",
         "starts_on": _soon(120), "ends_on": _soon(122), "format": "in_person",
         "sources": ["https://sf.example"], "confidence": "high"},
        {"name": "On ICP Learning Crowd", "famous": False, "city": "Austin",
         "category": R.CAT_VERTICAL_SUMMIT, "website": "https://ol.example",
         "starts_on": _soon(90), "ends_on": _soon(91), "format": "in_person",
         "sources": ["https://ol.example"], "confidence": "high"},
        {"name": "Adjacent But Real", "famous": False, "city": "Boston",
         "category": R.CAT_EMERGING, "website": "https://ab.example",
         "starts_on": _soon(150), "ends_on": _soon(151), "format": "in_person",
         "sources": ["https://ab.example"], "confidence": "high"},
        {"name": "Wrong Audience Expo", "famous": False, "city": "Berlin",
         "category": R.CAT_FREE_VENDOR, "website": "https://wa.example",
         "starts_on": _soon(200), "ends_on": _soon(201), "format": "in_person",
         "sources": ["https://wa.example"], "confidence": "high"},
    ]
    # relevance / dm_access / engagement, chosen so each row lands in a
    # different bucket rather than trusting one number to imply the split.
    shape = {
        "Strong Flagship":       (36, 34, 18),   # 88, clears the bar
        "On ICP Learning Crowd": (32, 22, 10),   # 64, right audience
        "Adjacent But Real":     (26, 24, 11),   # 61, right audience
        "Wrong Audience Expo":   (10, 20, 10),   # 40, not for them
    }

    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [dict(c) for c in discovered], "shortfall": [],
        "statuses": {}, "categories_failed": 0, "found": len(discovered),
        "by_category": {}, "categories_searched": 6})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "verdicts": {}, "cut": [], "kept": [], "checked": 0, "error": None})

    def _fake_batch(batch, profile):
        out = {}
        for c in batch:
            rel, dm, eng = shape[c["name"]]
            out[SC.score_key(c["name"])] = SC._clean(_scores(
                c["name"], relevance=rel, dm_access=dm, engagement=eng))
        return {"scores": out, "error": None}

    monkeypatch.setattr(SC, "score_batch", _fake_batch)
    monkeypatch.setattr(P.event_intel_scorer, "score_batch", _fake_batch)

    run_id = S.save_run(EMAIL, "recommend", "second tier e2e")
    P._run_recommend(run_id, EMAIL, _profile())

    row = S.get_run(run_id, EMAIL)
    assert row["status"] == "complete", row.get("error")
    s = row["summary"] or {}

    kept = [c["name"] for c in (s.get("top_five") or [])]
    look = [c["name"] for c in (s.get("worth_a_look") or [])]
    cut = [c["name"] for c in (s.get("excluded") or [])]

    assert kept == ["Strong Flagship"], kept
    assert look == ["On ICP Learning Crowd", "Adjacent But Real"], look
    assert cut == ["Wrong Audience Expo"], cut

    # Before the split this run handed the client ONE event. The point is the
    # number of things they can actually act on.
    assert len(kept) + len(look) == 3, (
        "the run offers %d options where it used to offer 1" % (len(kept) + len(look)))

    # The card the page draws needs more than a name and a score.
    opt = (s.get("worth_a_look") or [])[0]
    for field in ("starts_on", "city", "description", "relevance", "total",
                  "category"):
        assert opt.get(field) not in (None, ""), (
            "%s did not survive the store, so the option cannot be rendered"
            % field)
    assert opt["total"] < R.RANK_FLOOR
    assert opt["relevance"] >= R.RELEVANCE_GATE

    assert (s.get("counts") or {}).get("worth_a_look") == 2, s.get("counts")
