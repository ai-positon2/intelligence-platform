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
