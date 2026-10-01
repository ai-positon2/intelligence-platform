"""What "incomplete" means, as the pipeline writes it.

`completion_state` was 'partial' on every production run, because anything at
all short of perfect set it: one candidate a confirmation could not settle, a
confirmed event whose attendance figure could not be read, an event kept out
for being sold out. The report then replaced its answer with a warning on ten
runs out of ten. These pin the narrower rule: only a search that did not
finish, a scoring or audit call that broke, or an event unscored for missing
evidence makes a run provisional.
"""

import pytest

from tracker import event_intel_pipeline as P
from tracker import event_intel_rubric as R
from tests.test_event_intel_recommend import _FakeStore, _wire, _cand, PROFILE

VERIFY = ("1 of the 2 candidates found here could not be checked to a "
          "conclusion (The check ran but its answer could not be read.).")
NUMBERS = "1 confirmed event had published numbers we could not finish reading."


@pytest.mark.parametrize("st,kind", [
    ({"status": "error", "detail": "The search could not be completed."}, P.GAP_FAILED),
    ({"status": "error", "detail": "search_limit: max_uses_exceeded"}, P.GAP_BUDGET),
    ({"status": "partial", "detail": "The search reported it could not be finished."}, P.GAP_UNFINISHED),
    ({"status": "partial", "detail": VERIFY}, P.GAP_UNRESOLVED),
    ({"status": "partial", "detail": NUMBERS}, P.GAP_UNRESOLVED),
    ({"status": "partial", "detail": VERIFY + " " + NUMBERS}, P.GAP_UNRESOLVED),
    # The find call was cut off AND a candidate was unsettled: still a hole.
    ({"status": "partial", "detail": "The search reported it could not be finished. " + VERIFY}, P.GAP_UNFINISHED),
    # No reason at all is never read as "only verification".
    ({"status": "partial"}, P.GAP_UNFINISHED),
    ({"status": "empty", "short_by": 2, "why": "Nothing here."}, P.GAP_SHORT),
    ({"status": "ok", "kept": 2}, P.GAP_MET),
    ({"status": "ok", "kept": 1}, P.GAP_SHORT),
])
def test_one_classification_of_a_category(st, kind):
    assert P.category_gap_kind(st) == kind


def test_a_budget_tail_does_not_hide_a_verification_only_partial():
    why = (VERIFY + " It also used every one of the 8 searches allowed for "
           "finding events here, so there may be more to find than this "
           "search could reach.")
    assert P.category_gap_kind({"status": "partial", "why": why}) == P.GAP_UNRESOLVED


@pytest.mark.parametrize("reasons,kind", [
    (["This edition is sold out; access must be resolved before recommending attendance."], "policy"),
    (["The edition is outside the requested date window or has invalid dates.",
      "The location could not be verified against the client geography."], "policy"),
    (["This event is on the client exclusion list."], "policy"),
    (["The named event and its date range could not be found together in readable organizer text."], "evidence"),
    (["This edition is sold out; access must be resolved before recommending attendance.",
      "An event-site source is required to verify this edition."], "evidence"),
])
def test_why_an_event_is_unscored(reasons, kind):
    assert P.unscored_kind(reasons) == kind


def _st(**cats):
    return {c: dict(v, label=R.CATEGORY_LABELS[c]) for c, v in cats.items()}


def test_policy_exclusions_and_unsettled_candidates_are_not_incompleteness():
    done = P.completion(
        _st(vertical_summit={"status": "partial", "detail": VERIFY, "kept": 1},
            emerging={"status": "partial", "detail": NUMBERS, "kept": 2}),
        unscored=[{"name": "Sold Out Summit", "kind": "policy"}])
    assert done == {"state": "complete", "gaps": [], "qualifier": ""}


def test_a_search_that_did_not_finish_makes_the_run_provisional_and_is_named():
    done = P.completion(_st(free_vendor={"status": "error", "detail": "x", "kept": 0}))
    assert done["state"] == "partial"
    assert "Free sponsor-funded event" in done["qualifier"]
    assert "—" not in done["qualifier"]


def test_a_cut_off_search_that_still_met_its_quota_is_delivered():
    done = P.completion(_st(side_event={"status": "partial", "detail": "cut off", "kept": 2}))
    assert done["state"] == "complete"


@pytest.mark.parametrize("kw,phrase", [
    ({"scoring_errors": ["x"]}, "scoring reported 1 error"),
    ({"audit": {"error": "x"}}, "audit produced no usable result"),
    ({"audit": {"failed": {"a": {"name": "A"}}}}, "could not be completed for 1 event"),
    ({"unscored": [{"kind": "evidence"}, {"kind": "evidence"}]}, "2 events could not be scored for lack of evidence"),
    ({"unscored": [{"kind": "scoring"}]}, "returned no result for 1 event"),
])
def test_each_material_gap_is_said(kw, phrase):
    done = P.completion({}, **kw)
    assert done["state"] == "partial" and phrase in done["qualifier"]


def _discover(statuses, shortfall=None, candidates=None, failed=0):
    return lambda profile: {
        "candidates": candidates or [], "by_category": {}, "statuses": statuses,
        "shortfall": shortfall if shortfall is not None else [
            {"category": c, "label": R.CATEGORY_LABELS[c], "found": 0, "quota": 2,
             "short_by": 2, "status": s["status"], "why": s.get("detail") or "none"}
            for c, s in statuses.items()],
        "categories_searched": len(statuses) - failed, "categories_failed": failed,
        "found": len(candidates or [])}


def test_one_broken_category_beside_five_empty_ones_finishes_with_its_results(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    statuses = {c: {"status": "empty", "detail": "", "label": R.CATEGORY_LABELS[c]}
                for c in R.CATEGORIES}
    statuses[R.CAT_FREE_VENDOR] = {"status": "error", "error_kind": "transport",
                                  "detail": "The search could not be completed.",
                                  "label": R.CATEGORY_LABELS[R.CAT_FREE_VENDOR]}
    monkeypatch.setattr(P.event_intel_discover, "discover", _discover(statuses, failed=1))
    P._run_recommend(1, "me@p2.example", PROFILE)
    run = fake.runs[1]
    assert run["status"] == "complete", run.get("error")
    s = run["summary"]
    assert s["no_candidates"] and s["completion_state"] == "partial"
    assert len(s["statuses"]) == 6 and len(s["shortfall"]) == 6
    assert "do not establish that the market" in s["note"]
    assert s["statuses"][R.CAT_FREE_VENDOR]["gap_kind"] == P.GAP_FAILED


def test_every_empty_category_after_a_finished_search_is_a_finding(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    statuses = {c: {"status": "empty", "detail": ""} for c in R.CATEGORIES}
    monkeypatch.setattr(P.event_intel_discover, "discover", _discover(statuses))
    P._run_recommend(1, "me@p2.example", PROFILE)
    s = fake.runs[1]["summary"]
    assert s["completion_state"] == "complete"
    assert "Every category search finished" in s["note"]
    assert "do not establish" not in s["note"]


def test_a_run_where_no_category_ran_is_still_failed(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    statuses = {c: {"status": "error", "error_kind": "transport", "detail": "x"}
                for c in R.CATEGORIES}
    monkeypatch.setattr(P.event_intel_discover, "discover", _discover(statuses, failed=6))
    P._run_recommend(1, "me@p2.example", PROFILE)
    assert fake.runs[1]["status"] == "failed"


def _full_run(monkeypatch, fake, *, admission_reasons=(), score_errors=(), scorer_unscored=(),
              excluded=("Sold Summit",)):
    _wire(monkeypatch, fake)
    cands = [_cand("PMM Summit", dates_from="organizer_structured_data"), _cand("Sold Summit")]
    monkeypatch.setattr(P.event_intel_discover, "discover", _discover(
        {c: {"status": "ok", "kept": 2, "detail": ""} for c in R.CATEGORIES},
        shortfall=[], candidates=cands))
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "checked": 0, "error": None, "cut": [], "kept": [], "verdicts": {}})
    monkeypatch.setattr(P.event_intel_audit, "promote_alternatives", lambda *a, **k: {
        "promoted": [], "unconfirmed": [], "not_attempted": []})
    import tracker.event_intel_admission as ADM
    monkeypatch.setattr(ADM, "inspect_all", lambda evs: [
        {"name": e["name"], "support": "literal_name_and_dates_only", "checks": [],
         "reasons": list(admission_reasons) if e["name"] in excluded else []}
        for e in evs])

    def score_all(c, p):
        sc = [dict(x, relevance=36, dm_access=34, engagement=16, relevance_note="n",
                   dm_access_note="n", engagement_note="n", description="d.",
                   client_line="c.") for x in c if x["name"] not in scorer_unscored]
        un = [dict(x, scoring_note="The scoring pass returned no result for this event.")
              for x in c if x["name"] in scorer_unscored]
        return {"scored": sc, "unscored": un, "errors": list(score_errors), "batches": 1}
    monkeypatch.setattr(P.event_intel_scorer, "score_all", score_all)
    P._run_recommend(1, "me@p2.example", PROFILE)
    return fake.runs[1]


def test_a_sold_out_event_alone_does_not_make_the_run_provisional(monkeypatch):
    run = _full_run(monkeypatch, _FakeStore(), admission_reasons=[
        "This edition is sold out; access must be resolved before recommending attendance."])
    s = run["summary"]
    assert s["completion_state"] == "complete"
    assert [u["kind"] for u in s["unscored"]] == ["policy"]
    assert not any(n["level"] == "warn" for n in s["notes"])


def test_the_provisional_note_is_in_notes_and_in_assumptions(monkeypatch):
    run = _full_run(monkeypatch, _FakeStore(), score_errors=["x"])
    s = run["summary"]
    assert s["completion_state"] == "partial"
    warn = [n for n in s["notes"] if n["level"] == "warn"]
    assert len(warn) == 1 and warn[0]["detail"] == s["completion"]["qualifier"]
    assert any(a.startswith("This shortlist is provisional.") for a in s["assumptions"])
    assert len(s["assumptions"]) == len(s["notes"])


def test_every_event_kept_out_by_policy_is_a_complete_empty_answer(monkeypatch):
    run = _full_run(monkeypatch, _FakeStore(), admission_reasons=[
        "This event is on the client exclusion list."], scorer_unscored=("PMM Summit",))
    # PMM Summit returned nothing from the scorer: that is a real failure.
    assert run["status"] == "failed"
    run2 = _full_run(monkeypatch, _FakeStore(), admission_reasons=[
        "This event is on the client exclusion list."],
        excluded=("PMM Summit", "Sold Summit"))
    assert run2["status"] == "complete" and run2["summary"]["completion_state"] == "complete"
    assert {u["kind"] for u in run2["summary"]["unscored"]} == {"policy"}


def test_recovered_dates_are_recorded_in_the_summary(monkeypatch):
    run = _full_run(monkeypatch, _FakeStore())
    assert run["summary"]["dates_from"] == {"PMM Summit": "organizer_structured_data"}


# ── the committed-event line (2026-10-01) ──

def _committed(name, relevance, dm, eng):
    import datetime
    soon = (datetime.date.today() + datetime.timedelta(days=60)).isoformat()
    return {"name": name, "committed": True, "relevance": relevance, "dm_access": dm,
            "engagement": eng, "total": relevance + dm + eng,
            "starts_on": soon, "ends_on": soon}


def test_the_committed_line_names_the_bar_and_the_audience_cut():
    from tracker import event_intel_pipeline as P, event_intel_rubric as R
    ranked = R.rank([_committed("Low Score Expo", 30, 10, 5),
                     _committed("Wrong Crowd Summit", 10, 40, 20)])
    note = P.committed_note(ranked)
    assert "scored below %d" % R.RANK_FLOOR in note and "Low Score Expo at 45" in note
    assert "not mainly this client's buyers" in note and "Wrong Crowd Summit" in note
    assert "—" not in note


def test_no_committed_line_when_nothing_committed_falls_short():
    from tracker import event_intel_pipeline as P, event_intel_rubric as R
    assert P.committed_note(R.rank([_committed("Fine Forum", 35, 35, 15)])) is None
