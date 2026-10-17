"""Account Audit: what changed since the last saved audit (tracker/paid_audit_history.py)."""
import copy

from tracker import paid_audit_history as h


def report(*findings, score=80, grade="B"):
    """A report with one section holding these findings: (id, result, severity, found)."""
    fs = [{"id": i, "result": r, "severity": s, "title": "Title " + i, "found": f, "section": "x", "platform": "google"} for i, r, s, f in findings]
    tally = {k: sum(1 for f in fs if f["result"] == k) for k in ("pass", "warn", "fail", "na", "unknown")}
    return {"summary": {"score": score, "grade": grade, "tally": tally, "critical": sum(1 for f in fs if f["severity"] == "critical" and f["result"] == "fail"),
                        "high": sum(1 for f in fs if f["severity"] == "high" and f["result"] == "fail")}, "sections": [{"key": "x", "findings": fs}]}


def saved_from(rep, **kw):
    snap = h.snapshot(rep)
    row = {"id": "s1", "createdAt": "2026-07-17T10:00:00+00:00", "createdBy": "ana@position2.com", "note": "Onboarding", "score": snap["score"], "grade": snap["grade"], "results": snap["results"]}
    row.update(kw)
    return row


def test_a_snapshot_keeps_the_scores_and_each_checks_result_without_the_whole_report():
    rep = report(("G-1", "fail", "critical", "x" * 500), ("G-2", "pass", "low", "Fine."), score=61, grade="C")
    snap = h.snapshot(rep)
    assert snap["score"] == 61 and snap["grade"] == "C" and snap["critical"] == 1 and snap["high"] == 0 and snap["tally"]["fail"] == 1
    assert snap["results"]["G-1"] == {"r": "fail", "s": "critical", "t": "Title G-1", "f": "x" * 240} and snap["results"]["G-2"]["r"] == "pass"
    assert set(snap) == {"score", "grade", "tally", "critical", "high", "results"}
    empty = h.snapshot({})
    assert empty["score"] is None and empty["results"] == {} and empty["critical"] == 0 and empty["high"] == 0 and empty["tally"] == {} and empty["grade"] is None


def test_every_kind_of_change_is_found_and_unchanged_checks_are_counted():
    before = report(("A", "fail", "critical", "was bad"), ("B", "warn", "high", "was meh"), ("C", "pass", "medium", "was fine"), ("D", "pass", "low", "was fine"), ("E", "fail", "high", "bad"),
                    ("F", "warn", "low", "meh"), ("G", "fail", "medium", "bad too"), score=60)
    after = report(("A", "pass", "critical", "now fine"), ("B", "pass", "high", "now fine"), ("C", "fail", "medium", "now bad"), ("D", "warn", "low", "now meh"), ("E", "fail", "high", "bad"),
                   ("F", "warn", "low", "meh"), ("G", "warn", "medium", "better"), score=72, grade="B")
    out = h.compare(after, saved_from(before))
    assert [r["id"] for r in out["fixed"]] == ["A", "B"]                                  # most severe first
    assert [r["id"] for r in out["improved"]] == ["G"] and out["improved"][0]["was"] == "fail" and out["improved"][0]["now"] == "warn"
    assert [r["id"] for r in out["newProblems"]] == ["C", "D"] and out["newProblems"][0]["found"] == "now bad" and out["newProblems"][0]["wasFound"] == "was fine"
    assert out["worse"] == [] and out["stillFailing"] == 1 and out["stillWarning"] == 1
    assert out["counts"] == {"fixed": 2, "improved": 1, "worse": 0, "newProblems": 2}
    assert out["scoreWas"] == 60 and out["scoreNow"] == 72 and out["scoreChange"] == 12 and out["gradeNow"] == "B"
    assert out["since"] == "2026-07-17T10:00:00+00:00" and out["by"] == "ana@position2.com" and out["note"] == "Onboarding"


def test_a_warning_that_became_a_failure_is_worse_and_a_pass_that_failed_is_a_new_problem():
    out = h.compare(report(("A", "fail", "high", "x"), ("B", "fail", "low", "y")), saved_from(report(("A", "warn", "high", "x"), ("B", "pass", "low", "y"))))
    assert [r["id"] for r in out["worse"]] == ["A"] and [r["id"] for r in out["newProblems"]] == ["B"]


def test_a_check_not_judged_in_either_audit_is_not_a_change():
    before = report(("A", "unknown", "high", "could not"), ("B", "pass", "high", "ok"), ("C", "na", "low", "n/a"), ("D", "fail", "low", "bad"))
    after = report(("A", "fail", "high", "now read: bad"), ("B", "unknown", "high", "could not"), ("C", "pass", "low", "now applies"), ("D", "na", "low", "n/a now"))
    out = h.compare(after, saved_from(before))
    assert out["notComparable"] == 4 and out["fixed"] == out["worse"] == out["newProblems"] == out["improved"] == []


def test_checks_added_since_the_saved_audit_are_counted_not_compared():
    out = h.compare(report(("A", "pass", "low", "x"), ("NEW", "fail", "critical", "x")), saved_from(report(("A", "pass", "low", "x"))))
    assert out["added"] == 1 and out["newProblems"] == [] and out["notComparable"] == 0


def test_a_missing_score_gives_no_change_and_the_lists_are_capped():
    before = report(*[("C%d" % i, "fail", "low", "bad") for i in range(60)], score=None, grade=None)
    after = report(*[("C%d" % i, "pass", "low", "ok") for i in range(60)], score=90)
    out = h.compare(after, saved_from(before))
    assert out["scoreChange"] is None and out["scoreWas"] is None and out["scoreNow"] == 90
    assert len(out["fixed"]) == 40 and out["counts"]["fixed"] == 60


def test_comparing_does_not_change_either_input():
    before, after = report(("A", "fail", "high", "x")), report(("A", "pass", "high", "y"))
    saved = saved_from(before)
    a, b = copy.deepcopy(after), copy.deepcopy(saved)
    h.compare(after, saved)
    assert after == a and saved == b


def test_an_audit_compared_with_itself_changes_nothing():
    rep = report(("A", "fail", "high", "x"), ("B", "warn", "low", "y"), ("C", "pass", "low", "z"))
    out = h.compare(rep, saved_from(rep))
    assert out["counts"] == {"fixed": 0, "improved": 0, "worse": 0, "newProblems": 0} and out["stillFailing"] == 1 and out["stillWarning"] == 1 and out["scoreChange"] == 0


def test_a_severity_the_model_does_not_know_sorts_after_every_known_one():
    rows = [{"severity": "weird", "id": "A"}, {"severity": "low", "id": "B"}, {"severity": "critical", "id": "C"}]
    assert [r["id"] for r in sorted(rows, key=h._order)] == ["C", "B", "A"]
