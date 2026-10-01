"""The three exports say what the page says, in the page's words.

candidates.csv left out the events the page lists under "Not scored", and it
and the other two printed stored tokens (kept, cut, going, skipped,
rewritten_no_booth_note, literal_support_only, complete) into a file that
leaves the page that explained them.
"""

import csv
import io

import app as appmod
from tracker import event_intel_audit as A
from tracker import event_intel_store as store
from tracker import event_intel_workroom as W
from tracker.event_intel_identity import event_key

BASE = "/p2/strategic-agents/event-conference-intelligence"
RAW = {"kept", "cut", "unaudited", "promoted", "going", "skipped", "went",
       "rewritten_no_booth_note", "review_required", "account_play",
       "literal_support_only", "complete", "failed", "unverified", "ok"}


def _client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    return c


def _rows(resp):
    assert resp.status_code == 200
    return list(csv.reader(io.StringIO(resp.get_data(as_text=True))))


def _no_raw_tokens(rows):
    for r in rows[1:]:
        for cell in r:
            assert cell.strip() not in RAW, cell


def test_unscored_events_are_in_the_scored_export_with_status_and_reason(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "N", "status": "complete", "summary": {"unscored": [
            {"name": "Sold Summit", "kind": "policy",
             "note": "This edition is sold out; access must be resolved before recommending attendance."},
            {"name": "Dateless Forum",
             "note": "The named event and its date range could not be found together in readable organizer text."},
            {"name": "Crashed Con", "kind": "scoring",
             "note": "Unexpected failure: KeyError('x')"}]}})
    monkeypatch.setattr(store, "get_candidates", lambda rid: [
        {"name": "Kept", "total": 88, "tier": "P1", "category": "industry_flagship"}])
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head = rows[0]
    by = {r[0]: r for r in rows[1:]}
    assert set(by) == {"Kept", "Sold Summit", "Dateless Forum", "Crashed Con"}
    assert all(len(r) == len(head) for r in rows)
    st, why, total = head.index("Status"), head.index("Not measured"), head.index("Total /110")
    assert by["Sold Summit"][st] == "Not scored: kept out by a rule (see reason)"
    assert "sold out" in by["Sold Summit"][why]
    assert by["Dateless Forum"][st] == "Not scored: the evidence to score it was incomplete"
    assert by["Crashed Con"][st] == "Not scored: the scoring pass returned no result"
    assert "KeyError" not in by["Crashed Con"][why]
    assert by["Dateless Forum"][total] == "not scored"


def test_the_verdict_and_decision_columns_are_labelled(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {"id": rid, "query": "N", "summary": {}})
    cands = [{"name": n, "total": 88 - i, "tier": "P1", "category": "industry_flagship",
              "audit_verdict": v} for i, (n, v) in enumerate(
                  [("A", A.VERDICT_KEPT), ("B", A.VERDICT_UNAUDITED),
                   ("C", A.VERDICT_PROMOTED), ("D", "a_future_verdict")])]
    monkeypatch.setattr(store, "get_candidates", lambda rid: cands)
    monkeypatch.setattr(store, "get_outcomes", lambda email, pid=None: {
        event_key({"name": "A"}): {"decision": store.DECISION_GOING, "note": ""},
        event_key({"name": "B"}): {"decision": store.DECISION_SKIPPED, "note": ""}})
    rows = _rows(_client().get(BASE + "/runs/7/candidates.csv"))
    head = rows[0]
    by = {r[0]: r for r in rows[1:]}
    v, d = head.index("Famous-event verdict"), head.index("Your decision")
    assert by["A"][v] == "Kept after comparison with an alternative"
    assert by["B"][v] == "Not compared"
    assert by["C"][v] == "Added as a better-targeted alternative"
    assert by["D"][v] == "Compared"
    assert by["A"][d] == store.DECISION_LABELS[store.DECISION_GOING]
    assert by["B"][d] == store.DECISION_LABELS[store.DECISION_SKIPPED]
    _no_raw_tokens(rows)


def test_the_drafts_export_labels_every_status(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {
        "id": rid, "query": "E", "summary": {"event_name": "E"}})
    monkeypatch.setattr(store, "get_outreach", lambda rid: [
        {"org_name": "O%d" % i, "event_class": W.CLASS_OWNED, "draft_status": s}
        for i, s in enumerate([W.DRAFT_OK, W.DRAFT_REVIEW, W.DRAFT_ACCOUNT])])
    rows = _rows(_client().get(BASE + "/runs/7/outreach.csv"))
    col = rows[0].index("Draft status")
    body = [r for r in rows[1:] if r and r[0].startswith("O")]
    assert {r[col] for r in body} <= set(W.DRAFT_LABELS.values())
    text = "\n".join(",".join(r) for r in rows)
    assert "rewritten_no_booth_note" not in text


def test_the_roster_export_labels_evidence_and_run_status(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda rid, email: {"id": rid, "query": "E", "status": "complete"})
    monkeypatch.setattr(store, "get_sources", lambda rid: [])
    monkeypatch.setattr(store, "get_events", lambda rid: [{"name": "E"}])
    monkeypatch.setattr(store, "get_participants", lambda rid: [
        {"org_name": "Acme", "role": "exhibitor", "evidence": {"status": "literal_support_only"}},
        {"org_name": "Beta", "role": "sponsor", "evidence": {}}])
    rows = _rows(_client().get(BASE + "/runs/7/export.csv"))
    head = rows[0]
    ev, rs = head.index("Evidence status"), head.index("Run status")
    assert [r[ev] for r in rows[1:]] == ["Named on the published page", "Not verified"]
    assert {r[rs] for r in rows[1:]} == {"Complete"}
    _no_raw_tokens(rows)
