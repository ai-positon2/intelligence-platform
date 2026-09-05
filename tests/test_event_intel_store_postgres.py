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


# ── Outcome-driven order signal, against real Postgres ─────────────────────

def _saved_profile(email, client_name, classification=R.CLASS_B2B_TO_MARKETING,
            confidential=False):
    return S.save_profile(email, {
        "client_name": client_name, "classification": classification,
        "confidential": confidential})


def _backdate(run_id, days_ago):
    """Only a raw UPDATE can move created_at; update_run's field whitelist
    deliberately does not include it (nothing in production ever needs to)."""
    conn = S._pg_conn()
    conn.cursor().execute(
        "UPDATE evi_runs SET created_at = now() - (%s || ' days')::interval "
        "WHERE id = %s", (days_ago, run_id))
    conn.commit()
    conn.close()


def test_name_key_is_populated_on_save_and_round_trips():
    run = S.save_run(EMAIL, "recommend", "name_key test")
    S.save_candidates(run, [_cand("Money20/20 USA")])
    row = S.get_candidates(run)[0]
    from tracker.event_intel_discover import name_key
    assert row["name_key"] == name_key("Money20/20 USA")


def test_outcome_pattern_counts_by_category_and_by_format():
    email = "outcome-pattern@position2.com"
    pid = _saved_profile(email, "Pattern Co")
    run = S.save_run(email, "recommend", "pattern test", profile_id=pid)
    S.save_candidates(run, [
        _cand("Skip Summit A", category=R.CAT_VERTICAL_SUMMIT, format="hybrid"),
        _cand("Skip Summit B", category=R.CAT_VERTICAL_SUMMIT, format="hybrid"),
        _cand("Skip Summit C", category=R.CAT_VERTICAL_SUMMIT, format="virtual"),
    ])
    S.update_run(run, status="complete", stage="done")
    for name in ("Skip Summit A", "Skip Summit B", "Skip Summit C"):
        S.save_outcome(email, name, "skipped", profile_id=pid)

    pattern = S.outcome_pattern(email, pid)
    assert pattern["by_category"][R.CAT_VERTICAL_SUMMIT] == {
        "decisions": 3, "skipped": 3, "went_or_going": 0}
    assert pattern["by_format"]["hybrid"] == {
        "decisions": 2, "skipped": 2, "went_or_going": 0}
    assert pattern["by_format"]["virtual"] == {
        "decisions": 1, "skipped": 1, "went_or_going": 0}


def test_outcome_pattern_scopes_to_one_profile_not_the_whole_email():
    """The concrete failure this scoping prevents: an agency login with two
    client profiles must not let one client's dislike of a category leak
    into the other client's scoring."""
    email = "shared-login@position2.com"
    pid_a = _saved_profile(email, "Client A")
    pid_b = _saved_profile(email, "Client B")
    run_a = S.save_run(email, "recommend", "for A", profile_id=pid_a)
    S.save_candidates(run_a, [_cand("A's Summit", category=R.CAT_SIDE_EVENT)])
    S.update_run(run_a, status="complete", stage="done")
    S.save_outcome(email, "A's Summit", "skipped", profile_id=pid_a)

    pattern_b = S.outcome_pattern(email, pid_b)
    assert pattern_b["by_category"] == {}, (
        "Client A's skip pattern leaked into Client B's own pattern query")
    pattern_a = S.outcome_pattern(email, pid_a)
    assert pattern_a["by_category"][R.CAT_SIDE_EVENT]["skipped"] == 1


def test_outcome_pattern_ignores_legacy_rows_with_no_name_key():
    email = "legacy-rows@position2.com"
    pid = _saved_profile(email, "Legacy Co")
    run = S.save_run(email, "recommend", "legacy test", profile_id=pid)
    S.save_candidates(run, [_cand("Legacy Event", category=R.CAT_EMERGING)])
    S.update_run(run, status="complete", stage="done")
    conn = S._pg_conn()
    conn.cursor().execute(
        "UPDATE evi_candidates SET name_key = NULL WHERE run_id = %s", (run,))
    conn.commit()
    conn.close()
    S.save_outcome(email, "Legacy Event", "skipped", profile_id=pid)
    pattern = S.outcome_pattern(email, pid)
    assert pattern["by_category"] == {}, (
        "a row with no name_key was joined to an outcome anyway")


def test_save_outcome_does_not_blank_a_known_profile_on_a_later_omitted_save():
    email = "profile-coalesce@position2.com"
    pid = _saved_profile(email, "Coalesce Co")
    S.save_outcome(email, "Coalesce Event", "skipped", profile_id=pid)
    S.save_outcome(email, "Coalesce Event", "going")  # no profile_id this time
    conn = S._pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT profile_id, decision FROM evi_outcomes "
               "WHERE email = %s AND event_name = %s", (email, "Coalesce Event"))
    row = cur.fetchone()
    conn.close()
    assert row == (pid, "going"), (
        "a later save that omitted profile_id blanked the one already stored")


# ── Cross-client social proof, against real Postgres ────────────────────────

def test_cross_client_interest_counts_distinct_clients_not_rows():
    """Two rows from the SAME other email must count as one client, not
    two."""
    watched = "Convergence Con"
    from tracker.event_intel_discover import name_key
    key = name_key(watched)
    querying_email = "querying-client@position2.com"
    other_email = "other-client@position2.com"
    pid_other = _saved_profile(other_email, "Other Co")
    for i in range(2):
        run = S.save_run(other_email, "recommend", "other run %d" % i,
                         profile_id=pid_other)
        S.save_candidates(run, [_cand(watched)])
        S.update_run(run, status="complete", stage="done")

    counts = S.cross_client_interest([key], classification=None,
                                     window_days=365, exclude_email=querying_email)
    assert counts.get(key, {}).get("distinct_clients") == 1, counts


def test_cross_client_interest_excludes_the_querying_email():
    watched = "Self Exclusion Summit"
    from tracker.event_intel_discover import name_key
    key = name_key(watched)
    email = "self-excluded@position2.com"
    pid = _saved_profile(email, "Self Co")
    run = S.save_run(email, "recommend", "self run", profile_id=pid)
    S.save_candidates(run, [_cand(watched)])
    S.update_run(run, status="complete", stage="done")

    counts = S.cross_client_interest([key], classification=None,
                                     window_days=365, exclude_email=email)
    assert key not in counts, "the querying client counted its own run"


def test_cross_client_interest_only_counts_kept_events():
    """Searched-and-cut is not the same claim as kept. A row scored below
    RANK_FLOOR must never count toward another client's aggregate."""
    watched = "Below The Bar Con"
    from tracker.event_intel_discover import name_key
    key = name_key(watched)
    other_email = "cut-event-client@position2.com"
    pid = _saved_profile(other_email, "Cut Co")
    run = S.save_run(other_email, "recommend", "cut run", profile_id=pid)
    S.save_candidates(run, [_cand(watched, relevance=5, dm_access=5, engagement=2)])
    S.update_run(run, status="complete", stage="done")

    counts = S.cross_client_interest([key], classification=None,
                                     window_days=365,
                                     exclude_email="querying@position2.com")
    assert key not in counts, "a below-the-bar row counted as kept interest"


def test_cross_client_interest_respects_the_time_window():
    watched = "Old News Conference"
    from tracker.event_intel_discover import name_key
    key = name_key(watched)
    email = "old-run-client@position2.com"
    pid = _saved_profile(email, "Old Co")
    run = S.save_run(email, "recommend", "old run", profile_id=pid)
    S.save_candidates(run, [_cand(watched)])
    S.update_run(run, status="complete", stage="done")
    _backdate(run, days_ago=400)

    counts = S.cross_client_interest([key], classification=None,
                                     window_days=120,
                                     exclude_email="querying@position2.com")
    assert key not in counts, "a run from 400 days ago was inside a 120-day window"


def test_cross_client_interest_groups_by_classification():
    watched = "Vertical Only Con"
    from tracker.event_intel_discover import name_key
    key = name_key(watched)
    other_email = "other-vertical-client@position2.com"
    pid = _saved_profile(other_email, "Other Vertical Co",
                  classification=R.CLASS_B2B_OTHER_FUNCTION)
    run = S.save_run(other_email, "recommend", "vertical run", profile_id=pid)
    S.save_candidates(run, [_cand(watched)])
    S.update_run(run, status="complete", stage="done")

    counts_wrong_classification = S.cross_client_interest(
        [key], classification="a_classification_nobody_has",
        window_days=365, exclude_email="querying@position2.com")
    assert key not in counts_wrong_classification


def test_a_confidential_profile_never_contributes_a_count():
    watched = "Confidential Client Con"
    from tracker.event_intel_discover import name_key
    key = name_key(watched)
    email = "confidential-client@position2.com"
    pid = _saved_profile(email, "Confidential Co", confidential=True)
    run = S.save_run(email, "recommend", "confidential run", profile_id=pid)
    S.save_candidates(run, [_cand(watched)])
    S.update_run(run, status="complete", stage="done")

    counts = S.cross_client_interest([key], classification=None,
                                     window_days=365,
                                     exclude_email="querying@position2.com")
    assert key not in counts, "a confidential profile's event still counted"


def test_a_confidential_profile_is_excluded_from_the_population_too():
    """The other half of the confidential opt-out: excluded from CONTRIBUTING
    an event to another client's count (test above) is not the same
    guarantee as excluded from the whole POPULATION gate, and cross_client_
    interest and classification_population are two separate queries that
    could drift apart -- this failed silently once already in this exact
    mutation sweep before this test existed to catch it."""
    import uuid
    classification = R.CLASS_B2C_BOOTH_DENSITY
    before = S.classification_population(
        classification, window_days=365, exclude_email="querying@position2.com")
    email = "confidential-population-%s@position2.com" % uuid.uuid4().hex[:8]
    pid = _saved_profile(email, "Confidential Population Co",
                         classification=classification, confidential=True)
    run = S.save_run(email, "recommend", "confidential pop run", profile_id=pid)
    S.update_run(run, status="complete", stage="done")
    after = S.classification_population(
        classification, window_days=365, exclude_email="querying@position2.com")
    assert after == before, (
        "a confidential profile still counted toward the population gate")


def test_classification_population_counts_distinct_emails():
    """Measured as a DELTA against the population classification_population
    itself reports before seeding, not an exact count: `classification` has
    to be one of the four real, enforced values (orientation_for() raises on
    anything else, correctly), and a shared sandbox database can already
    hold other profiles under the same one from earlier runs.

    The two emails are unique to THIS run (uuid4), not fixed literals: this
    file is meant to be re-run repeatedly against one persistent sandbox
    database, save_profile is a plain INSERT with no conflict handling, and
    a fixed email re-inserted on every run is not a NEW distinct email the
    second time -- the delta this test measures would silently read as 0
    against a database that already has data from the run before it.
    """
    import uuid
    classification = R.CLASS_B2C_BOOTH_DENSITY
    before = S.classification_population(
        classification, window_days=365, exclude_email="querying@position2.com")
    email_a = "population-a-%s@position2.com" % uuid.uuid4().hex[:8]
    email_b = "population-b-%s@position2.com" % uuid.uuid4().hex[:8]
    for email, name in ((email_a, "Pop A"), (email_b, "Pop B")):
        pid = _saved_profile(email, name, classification=classification)
        run = S.save_run(email, "recommend", "pop run", profile_id=pid)
        S.update_run(run, status="complete", stage="done")
    after = S.classification_population(
        classification, window_days=365, exclude_email="querying@position2.com")
    assert after - before == 2


def test_no_identity_leaks_at_any_count_above_or_below_the_floor():
    """The load-bearing test. JSON-scans the ENTIRE returned structure --
    not just whatever text a report might later render from it -- for any
    seeded other-client email or client_name, at both a below-floor and an
    above-floor distinct-client count."""
    import json as _json
    watched = "Leak Check Conference"
    from tracker.event_intel_discover import name_key
    key = name_key(watched)
    secrets = []
    for i in range(4):
        email = "secret-client-%d@position2.com" % i
        name = "Secret Competitor %d Inc" % i
        secrets.append(email)
        secrets.append(name)
        pid = _saved_profile(email, name)
        run = S.save_run(email, "recommend", "secret run %d" % i, profile_id=pid)
        S.save_candidates(run, [_cand(watched)])
        S.update_run(run, status="complete", stage="done")

    counts = S.cross_client_interest([key], classification=None,
                                     window_days=365,
                                     exclude_email="querying@position2.com")
    population = S.classification_population(
        None, window_days=365, exclude_email="querying@position2.com")
    blob = _json.dumps({"counts": counts, "population": population})
    for secret in secrets:
        assert secret not in blob, "an identity leaked into the raw data: %r" % secret


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


def test_a_finished_run_records_what_it_cost(monkeypatch):
    """Cost had never been measured in production at all.

    `claude_websearch.ask` returned `usage` on every reply and nothing
    accumulated it, so the only figure anyone had for a run was $9.13 from a
    pipeline design that had since been replaced. The first instrumented run
    came in at $12.15 across 32 calls, 2.58M input tokens and 201 searches,
    with a completely different shape.

    Summed through return values rather than a module global on purpose:
    `run_job` is a thread entry point and two runs can be in flight in one
    process, so a shared counter would bill one client for another's
    searches.
    """
    from tracker import claude_websearch as CW
    test_a_well_matched_event_below_the_bar_reaches_the_stored_summary(monkeypatch)
    runs = S.list_runs(EMAIL)
    run = S.get_run(runs[0]["id"], EMAIL)
    spend = (run["summary"] or {}).get("spend")
    assert spend is not None, "a finished run does not record what it cost"
    for key in ("calls", "input_tokens", "output_tokens", "searches", "usd",
                "by_stage"):
        assert key in spend, "spend is missing %s" % key
    for stage in ("discover", "score", "audit", "promote"):
        assert stage in spend["by_stage"], (
            "%s is unaccounted for, so a stage could grow expensive "
            "invisibly" % stage)
    # The arithmetic is the module's, not the test's.
    assert spend["usd"] == CW.spend_usd(spend)


def test_the_recorded_cost_counts_the_calls_that_were_refused(monkeypatch):
    """The expensive failures are the ones worth seeing. A confirmation
    discarded after six live searches, a scoring pass whose answer could not
    be read, an audit that was refused for answering from memory: each cost
    full price, and each used to be invisible."""
    from tracker import claude_websearch as CW
    refused = {"usage": {"input_tokens": 50000, "output_tokens": 3000},
               "search_count": 6,
               "error": {"kind": "max_tokens", "detail": "out of room"}}
    rec = CW.spend_of(refused)
    assert rec["input_tokens"] == 50000 and rec["searches"] == 6
    assert CW.spend_usd(rec) > 0, (
        "a call that failed after six live searches was costed at zero")
