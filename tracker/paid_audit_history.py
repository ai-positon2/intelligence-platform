"""Paid Marketing Agents: Account Audit, what changed since the last saved audit.

An audit is made at onboarding and again every quarter, so the useful question the second time is what moved. A saved audit keeps each
check's result (not the whole report), and this compares the audit just made with one saved earlier: which checks were put right, which
got better, which are new problems, which got worse. Only a check judged in both audits (pass, warning or fail) is compared; a check that
was not applicable or could not be read in either one is counted as not comparable, never as a change. Plain arithmetic, no model.
"""
from __future__ import annotations

from . import paid_audit_model as model

FOUND_CHARS = 240
JUDGED = {"pass": 0, "warn": 1, "fail": 2}          # how bad: a higher number is worse
LIMIT_PER_LIST = 40


def snapshot(report):
    """What is saved of an audit: the scores and every check's result, severity, title and what was found (shortened)."""
    s = report.get("summary") or {}
    results = {}
    for sec in report.get("sections") or []:
        for f in sec["findings"]:
            results[f["id"]] = {"r": f["result"], "s": f["severity"], "t": f["title"], "f": f["found"][:FOUND_CHARS]}
    return {"score": s.get("score"), "grade": s.get("grade"), "tally": s.get("tally") or {}, "critical": s.get("critical") or 0, "high": s.get("high") or 0,
            "results": results}


def _row(cid, now, was):
    return {"id": cid, "title": now["t"], "severity": now["s"], "was": was["r"], "now": now["r"], "found": now["f"], "wasFound": was["f"]}


def _order(row):
    return (model.SEVERITIES.index(row["severity"]) if row["severity"] in model.SEVERITIES else 9, row["id"])


def compare(report, saved):
    """The audit just made against a saved one (see snapshot): {since, by, note, scoreWas, scoreNow, scoreChange, gradeWas, gradeNow, fixed,
    improved, worse, newProblems, stillFailing, stillWarning, notComparable, added}. `saved` is the saved row with its `results`."""
    now = snapshot(report)
    was = saved.get("results") or {}
    fixed, improved, worse, new, still_fail, still_warn, skipped, added = [], [], [], [], 0, 0, 0, 0
    for cid, n in now["results"].items():
        w = was.get(cid)
        if w is None:
            added += 1
            continue
        if n["r"] not in JUDGED or w["r"] not in JUDGED:
            skipped += 1
            continue
        a, b = JUDGED[w["r"]], JUDGED[n["r"]]
        if b == a:
            still_fail += n["r"] == "fail"
            still_warn += n["r"] == "warn"
        elif b == 0:
            fixed.append(_row(cid, n, w))
        elif b < a:
            improved.append(_row(cid, n, w))
        elif a == 0:
            new.append(_row(cid, n, w))
        else:
            worse.append(_row(cid, n, w))
    for lst in (fixed, improved, worse, new):
        lst.sort(key=_order)
    change = None if now["score"] is None or saved.get("score") is None else now["score"] - saved["score"]
    return {"since": saved.get("createdAt"), "by": saved.get("createdBy"), "note": saved.get("note") or "", "scoreWas": saved.get("score"), "scoreNow": now["score"],
            "scoreChange": change, "gradeWas": saved.get("grade"), "gradeNow": now["grade"],
            "fixed": fixed[:LIMIT_PER_LIST], "improved": improved[:LIMIT_PER_LIST], "worse": worse[:LIMIT_PER_LIST], "newProblems": new[:LIMIT_PER_LIST],
            "counts": {"fixed": len(fixed), "improved": len(improved), "worse": len(worse), "newProblems": len(new)},
            "stillFailing": still_fail, "stillWarning": still_warn, "notComparable": skipped, "added": added}
