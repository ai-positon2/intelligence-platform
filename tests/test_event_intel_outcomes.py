"""Phase 9: exports, the outcome loop, and the guardrail self-test.

The through-line: a file that leaves the page carries the caveats the page
carried, and a decision the user already made is never quietly acted on for
them.
"""

import csv
import io

import pytest

import app as appmod
from tracker import event_intel_report as report
from tracker import event_intel_store as store
from tracker import event_intel_workroom as W

BASE = "/p2/b2b-agents/event-conference-intelligence"


def _client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    return c


# ── The outcome loop ──────────────────────────────────────────────────────

def test_an_event_already_ruled_on_is_annotated_not_hidden():
    """A previous rejection is information, not an instruction. What was right
    to skip last quarter may not be right now, and that is the user's call."""
    cands = [{"name": "Dreamforce"}, {"name": "Data Council"}]
    out = report.annotate_outcomes(cands, {
        "dreamforce": {"decision": "skipped", "note": "Too broad for us.",
                       "updated_at": "2026-03-01"}})
    assert len(out["candidates"]) == 2
    assert out["candidates"][0]["prior_decision"] == "skipped"
    assert out["candidates"][0]["prior_note"] == "Too broad for us."
    assert out["candidates"][1]["prior_decision"] is None
    assert out["ruled_on"] == 1
    assert "not hidden" in out["note"]


def test_the_annotation_matches_an_event_across_spelling_and_year():
    """The key has to survive "Dreamforce 2026" against "Dreamforce", or a
    decision silently stops applying the moment the edition rolls over."""
    out = report.annotate_outcomes(
        [{"name": "Dreamforce 2026"}],
        {"dreamforce": {"decision": "skipped", "note": None, "updated_at": None}})
    assert out["candidates"][0]["prior_decision"] == "skipped"


def test_nothing_ruled_on_produces_no_note_rather_than_a_zero():
    out = report.annotate_outcomes([{"name": "A"}], {})
    assert out["ruled_on"] == 0
    assert out["note"] is None


def test_the_by_name_map_only_carries_events_with_a_decision():
    out = report.annotate_outcomes(
        [{"name": "A"}, {"name": "B"}],
        {"a": {"decision": "going", "note": None, "updated_at": None}})
    assert list(out["by_name"]) == ["A"]


def test_decisions_are_counted_by_kind():
    out = report.annotate_outcomes(
        [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        {"a": {"decision": "going", "note": None, "updated_at": None},
         "b": {"decision": "skipped", "note": None, "updated_at": None},
         "c": {"decision": "went", "note": None, "updated_at": None}})
    assert out["counts"] == {"going": 1, "skipped": 1, "went": 1}


# ── Outcome-driven order signal ────────────────────────────────────────────

def _cand(name, total, category="vertical_summit", format="in_person"):
    return {"name": name, "total": total, "tier": "P2", "category": category,
            "format": format}


def test_a_strong_skip_pattern_reorders_but_never_touches_total_or_tier():
    """The one non-obvious design call, locked in: rank() has already
    decided bucket membership and the cap, and this can only reorder
    survivors within their bucket. "High but disliked" outscores "Low but
    liked" on the raw rubric (79 vs 76), but its category carries a strong
    skip pattern and the other candidate's category carries none, so the
    -5 adjustment (74) drops it below the untouched 76 -- flipping the
    order the raw totals alone would give."""
    kept = [_cand("Low but liked", 76, category="side_event"),
           _cand("High but disliked", 79, category="vertical_summit")]
    pattern = {"by_category": {"vertical_summit":
                               {"decisions": 4, "skipped": 4, "went_or_going": 0}},
              "by_format": {}}
    out = report.apply_outcome_pattern(kept, pattern)
    assert [c["name"] for c in out] == ["Low but liked", "High but disliked"], (
        "the skip pattern did not reorder the higher-scored, disliked event "
        "below the lower-scored, neutral one")
    totals = {c["name"]: c["total"] for c in out}
    assert totals == {"Low but liked": 76, "High but disliked": 79}, (
        "total was mutated by an order signal that must never touch it")
    assert [c["tier"] for c in out] == ["P2", "P2"], "tier was mutated too"


def test_a_disliked_event_still_appears_never_excluded():
    """Direct regression test for the "never exclude" principle: an event a
    client has a strong negative pattern against outscores a neutral one on
    the raw rubric (74 vs 70) and is knocked BEHIND it by the -5 adjustment
    (69 vs 70) -- but it is still ON THE LIST, just last, never dropped."""
    kept = [_cand("Barely cleared", 70, category="side_event"),
           _cand("Disliked category", 74, category="vertical_summit")]
    pattern = {"by_category": {"vertical_summit":
                               {"decisions": 5, "skipped": 5, "went_or_going": 0}},
              "by_format": {}}
    out = report.apply_outcome_pattern(kept, pattern)
    assert {c["name"] for c in out} == {"Barely cleared", "Disliked category"}
    assert out[-1]["name"] == "Disliked category"


def test_every_row_carries_the_adjustment_and_a_reason_even_at_zero():
    out = report.apply_outcome_pattern([_cand("X", 75)],
                                       {"by_category": {}, "by_format": {}})
    assert out[0]["outcome_adjustment"] == 0
    assert out[0]["outcome_adjustment_basis"] is None
    assert out[0]["outcome_adjustment_reason"]


def test_an_empty_pattern_changes_nothing_about_order():
    kept = [_cand("B", 80), _cand("A", 90)]
    out = report.apply_outcome_pattern(kept, {"by_category": {}, "by_format": {}})
    assert [c["name"] for c in out] == ["A", "B"]


# ── Cross-client social proof ──────────────────────────────────────────────

def test_a_firing_signal_attaches_the_count_and_an_aggregate_only_note():
    cands = [{"name": "MAICON", "name_key": "maicon"}]
    signal = {"maicon": {"count": 4, "fires": True}}
    out = report.attach_cross_client_signal(cands, signal)
    assert out[0]["cross_client_count"] == 4
    assert "no client names" in out[0]["cross_client_note"]


def test_a_non_firing_signal_attaches_nothing_not_a_zero():
    """A row that was checked and found to not clear the gate must read
    differently from a row that was never checked at all -- both get None,
    neither is presented as a measured zero."""
    cands = [{"name": "Small Event", "name_key": "small_event"}]
    signal = {"small_event": {"count": 1, "fires": False}}
    out = report.attach_cross_client_signal(cands, signal)
    assert out[0]["cross_client_count"] is None
    assert out[0]["cross_client_note"] is None


def test_a_row_with_no_name_key_gets_no_signal():
    out = report.attach_cross_client_signal([{"name": "X", "name_key": None}],
                                            {"x": {"count": 9, "fires": True}})
    assert out[0]["cross_client_count"] is None


def test_the_cross_client_note_never_reorders_the_list():
    """Pure information: unlike apply_outcome_pattern, nothing in this
    feature's spec asks it to move anything."""
    cands = [{"name": "B", "name_key": "b"}, {"name": "A", "name_key": "a"}]
    signal = {"a": {"count": 5, "fires": True}}
    out = report.attach_cross_client_signal(cands, signal)
    assert [c["name"] for c in out] == ["B", "A"]


@pytest.mark.parametrize("bad", ["attending", "", "GOING", "maybe", None])
def test_an_unknown_decision_is_refused_rather_than_stored(bad):
    """The report renders DECISION_LABELS, so an unrecognised key would print
    raw next to real labels."""
    with pytest.raises(ValueError):
        store.save_outcome("a@b.com", "Dreamforce", bad)


def test_an_outcome_with_no_event_name_is_refused():
    with pytest.raises(ValueError):
        store.save_outcome("a@b.com", "   ", store.DECISION_GOING)


def test_every_decision_has_its_own_wording():
    assert set(store.DECISION_LABELS) == set(store.DECISIONS)
    assert len(set(store.DECISION_LABELS.values())) == len(store.DECISIONS)


def test_the_outcomes_route_refuses_a_bad_decision_with_the_real_reason():
    r = _client().post(BASE + "/outcomes",
                       json={"event_name": "Dreamforce", "decision": "maybe"})
    assert r.status_code == 400
    assert "maybe" in r.get_json()["error"]


# ── Exports ───────────────────────────────────────────────────────────────

def _rows(resp):
    return list(csv.reader(io.StringIO(resp.get_data(as_text=True))))


def test_the_scored_export_carries_every_sub_score_and_its_reasoning(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "Northwind", "summary": {"title": "Northwind: Conference Analysis"}})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [{
        "name": "FinovateFall", "tier": "P1", "total": 97, "relevance": 36,
        "relevance_note": "Close ICP match.", "dm_access": 34,
        "dm_access_note": "Hosted buyers.", "engagement": 17,
        "engagement_note": "Buying crowd.", "matchmaking": 10,
        "matchmaking_reason": "Organiser-run.", "category": "industry_flagship",
        "cost_note": "Booth from $18k", "gaps": ["Attendance unverified."]}])
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head, body = rows[0], rows[1]
    for col in ("Relevance /40", "Relevance reasoning", "Decision-maker access /40",
                "Engagement /20", "Matchmaking bonus", "Cost (never scored)",
                "Not measured"):
        assert col in head, "%s is missing from the export" % col
    assert body[head.index("Relevance reasoning")] == "Close ICP match."
    assert body[head.index("Not measured")] == "Attendance unverified."


def test_the_scored_export_includes_the_cut_events_and_marks_them(monkeypatch):
    """A file listing only the survivors reads as "these were the only events
    found", which is the exact claim the screen refuses to make."""
    monkeypatch.setattr(store, "get_run", lambda rid, email: {"id": rid, "query": "N", "summary": {}})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [
        {"name": "Kept", "total": 88, "tier": "P1", "category": "industry_flagship"},
        {"name": "Cut", "total": 41, "tier": "P3", "category": "industry_flagship"}])
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head = rows[0]
    status = head.index("Status")
    assert [r[0] for r in rows[1:]] == ["Kept", "Cut"]
    assert rows[1][status] == "Recommended"
    assert rows[2][status] == "Excluded, below the bar"


def test_a_worth_a_look_event_says_so_rather_than_a_bare_no(monkeypatch):
    """The regression this column exists to close. `rank()` has TWO gates
    below RANK_FLOOR (relevance and consider), not a bare floor comparison,
    and a real run's export once called a genuine second-tier event "no"
    while the SAME run's web page showed it under "Worth a look" as an
    offered option."""
    monkeypatch.setattr(store, "get_run", lambda rid, email: {"id": rid, "query": "N", "summary": {}})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [
        {"name": "Second Tier", "total": 67, "tier": "P3", "relevance": 32,
         "category": "industry_flagship"}])
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head, body = rows[0], rows[1]
    assert body[head.index("Status")].startswith("Worth a look")


def test_the_status_column_matches_the_runs_own_cap(monkeypatch):
    """A client who raised or lowered max_events must see rows labelled
    against the ceiling THIS run actually used, not the module default."""
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "N", "summary": {}, "profile_id": 9})
    monkeypatch.setattr(store, "get_profile",
                        lambda pid, email: {"max_events": 1})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [
        {"name": "First", "total": 90, "tier": "P1", "category": "industry_flagship"},
        {"name": "Second", "total": 85, "tier": "P1", "category": "industry_flagship"}])
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head = rows[0]
    status = head.index("Status")
    assert rows[1][status] == "Recommended"
    assert rows[2][status] == "Cleared the bar, cut only by list length"


def test_the_export_carries_the_outcome_adjustment_and_its_reason(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "N", "summary": {}, "profile_id": 9})
    monkeypatch.setattr(store, "get_profile",
                        lambda pid, email: {"id": 9, "max_events": 15})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [
        {"name": "Disliked Summit", "total": 74, "tier": "P2",
         "category": "vertical_summit", "format": "in_person"}])
    monkeypatch.setattr(store, "outcome_pattern", lambda email, pid, exclude_run_id=None: {
        "by_category": {"vertical_summit":
                        {"decisions": 4, "skipped": 4, "went_or_going": 0}},
        "by_format": {}})
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head, body = rows[0], rows[1]
    adj = head.index("Your history with this category/format")
    why = head.index("Why")
    assert body[adj] == "-5"
    assert "skipped 4 of the last 4" in body[why]


def test_the_export_carries_the_cross_client_note_with_no_identity(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "N", "summary": {}, "profile_id": 9})
    monkeypatch.setattr(store, "get_profile",
                        lambda pid, email: {"id": 9, "max_events": 15,
                                            "classification": "b2b_to_marketing"})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [
        {"name": "Watched Summit", "total": 78, "tier": "P2",
         "category": "vertical_summit", "name_key": "watched"}])
    monkeypatch.setattr(store, "classification_population",
                        lambda *a, **k: 10)
    monkeypatch.setattr(store, "cross_client_interest", lambda *a, **k: {
        "watched": {"name": "Watched Summit", "distinct_clients": 4}})
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head, body = rows[0], rows[1]
    col = head.index("Also watched by other clients (aggregate, no names)")
    assert "4 other clients" in body[col]
    assert "@" not in body[col], "an email address leaked into the export"


def test_a_confidential_profile_gets_no_cross_client_column_data(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "N", "summary": {}, "profile_id": 9})
    monkeypatch.setattr(store, "get_profile",
                        lambda pid, email: {"id": 9, "max_events": 15,
                                            "confidential": True})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [
        {"name": "Private Summit", "total": 78, "tier": "P2",
         "category": "vertical_summit", "name_key": "private"}])
    called = []
    monkeypatch.setattr(store, "classification_population",
                        lambda *a, **k: called.append(1) or 10)
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head, body = rows[0], rows[1]
    col = head.index("Also watched by other clients (aggregate, no names)")
    assert body[col] == ""
    assert not called, "a confidential profile's export still queried the signal"


def test_an_unscored_event_says_so_in_the_export_rather_than_showing_blank(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {"id": rid, "query": "N", "summary": {}})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [
        {"name": "Ghost", "total": None, "category": "industry_flagship"}])
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    assert rows[1][rows[0].index("Total /110")] == "not scored"


def test_the_drafts_export_carries_the_rewrite_reason_and_the_phrase(monkeypatch):
    """This file is exactly the artefact somebody pastes into a sequencer. A
    version showing only the final opener would launder the screen."""
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "FinovateFall",
        "summary": {"event_name": "FinovateFall",
                    "send_note": "Nothing here has been sent.",
                    "repeats": {"crm_note": "No CRM is connected."}}})
    monkeypatch.setattr(store, "get_outreach", lambda rid: [{
        "org_name": "Meridian", "role": "sponsor", "person_name": "Sam",
        "fit": 78, "fit_note": "Fits.", "angle": "Budget.",
        "opener": "I saw Meridian on the sponsor list.",
        "draft_status": W.DRAFT_NO_EVIDENCE,
        "draft_reason": "Claimed a conversation nobody recorded.",
        "draft_flagged": ["good to meet", "you mentioned"],
        "event_name": "FinovateFall", "event_class": W.CLASS_EXHIBITED}])
    rows = _rows(_client().get(BASE + "/runs/7/outreach.csv"))
    head, body = rows[0], rows[1]
    assert body[head.index("Draft status")] == W.DRAFT_NO_EVIDENCE
    assert "nobody recorded" in body[head.index("Why the draft was changed")]
    assert "good to meet" in body[head.index("Phrases that triggered the change")]


def test_the_drafts_export_carries_the_caveats_off_the_page_with_it(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "E",
        "summary": {"event_name": "E", "send_note": "Nothing here has been sent.",
                    "repeats": {"crm_note": "No CRM is connected."}}})
    monkeypatch.setattr(store, "get_outreach", lambda rid: [
        {"org_name": "Acme", "event_class": W.CLASS_OWNED, "draft_status": "ok"}])
    text = _client().get(BASE + "/runs/7/outreach.csv").get_data(as_text=True)
    assert "Nothing here has been sent" in text
    assert "No CRM is connected" in text
    assert "claimed a conversation nobody recorded" in text


def test_a_drafts_row_uses_the_stores_role_wording(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {"id": rid, "query": "E", "summary": {}})
    monkeypatch.setattr(store, "get_outreach", lambda rid: [
        {"org_name": "Acme", "role": store.ROLE_EXHIBITOR,
         "event_class": W.CLASS_OWNED, "draft_status": "ok"}])
    rows = _rows(_client().get(BASE + "/runs/7/outreach.csv"))
    assert rows[1][rows[0].index("Listed as")] == store.ROLE_LABELS[store.ROLE_EXHIBITOR]
    assert "attendee" not in rows[1][rows[0].index("Listed as")].lower()


def test_an_unqualified_company_says_so_rather_than_exporting_a_blank_fit(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {"id": rid, "query": "E", "summary": {}})
    monkeypatch.setattr(store, "get_outreach", lambda rid: [
        {"org_name": "Acme", "fit": None, "qualify_note": "No result returned.",
         "event_class": W.CLASS_OWNED, "draft_status": "ok"}])
    rows = _rows(_client().get(BASE + "/runs/7/outreach.csv"))
    assert rows[1][rows[0].index("ICP fit /100")] == "not qualified"


@pytest.mark.parametrize("path", ["candidates.csv", "outreach.csv"])
def test_an_export_for_someone_elses_run_is_a_404(monkeypatch, path):
    monkeypatch.setattr(store, "get_run", lambda rid, email: None)
    assert _client().get(BASE + "/runs/7/" + path).status_code == 404


# ── The guardrail self-test ───────────────────────────────────────────────

def test_the_guardrail_self_test_proves_all_four_refusals():
    res = appmod._evi_guardrail_selftest()
    assert res["ok"] is True, res["failures"]
    assert res["passed"] == res["total"] >= 6
    names = " ".join(c["check"] for c in res["checks"]).lower()
    for rule in ("budget", "classification", "+10", "conversation",
                 "displacement", "total"):
        assert rule in names, "the self-test does not cover %s" % rule


def test_the_guardrail_self_test_states_what_it_did_not_check():
    """A green offline result must not read as "the agent works"."""
    res = appmod._evi_guardrail_selftest()
    assert res["not_checked"]
    joined = " ".join(res["not_checked"])
    assert "ANTHROPIC_API_KEY" in joined
    assert "not that the agent can run" in joined


def test_the_guardrail_self_test_actually_fails_when_a_guard_is_removed(monkeypatch):
    """A self-test that cannot fail is a green light wired to nothing."""
    from tracker import event_intel_workroom as wr
    monkeypatch.setattr(wr, "enforce",
                        lambda rows, **kw: {"rows": [dict(rows[0],
                                                          draft_status=wr.DRAFT_OK)],
                                            "rewritten": [], "rewritten_count": 0})
    res = appmod._evi_guardrail_selftest()
    assert res["ok"] is False
    assert res["failures"]


def test_the_guardrail_route_is_admin_only():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    r = c.post("/p2/admin/external-usage/evi-guardrail-check")
    assert r.status_code in (302, 403), \
        "a non-admin reached the self-test (%s)" % r.status_code
