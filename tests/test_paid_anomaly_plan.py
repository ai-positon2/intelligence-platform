"""Spend Anomaly Alert: what to check and in what order (tracker/paid_anomaly_plan.py). No network."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_paid_anomaly_math import TODAY, camp, run, rows_for, today_rows, usual_camps  # noqa: E402
from tracker import paid_anomaly_plan as plan  # noqa: E402


def test_every_kind_has_actions_and_a_word_and_no_dashes():
    assert set(plan.ACTIONS) == set(plan.KIND_WORD) == set(plan.ORDER)
    for k, acts in plan.ACTIONS.items():
        assert len(acts) >= 2 and all(a.endswith(".") and "—" not in a and "–" not in a for a in acts), k


def test_nothing_found_is_no_steps_and_no_recheck():
    assert plan.build(run(rows_for(14) + today_rows(10, 12), 12)) == {"steps": []}


def test_the_steps_are_the_worst_first_then_what_is_happening_now_and_end_with_a_recheck():
    rows = rows_for(14) + today_rows(10, 12, boost={9: 60, 10: 60, 11: 60})
    a = run(rows, 12, saved={"maxDaily": 100})
    steps = plan.build(a)["steps"]
    assert [(s["kind"], s["level"], s["day"]) for s in steps] == [("surge", "high", "today"), ("ceiling", "high", "today"), ("ceiling", "high", "yesterday"), ("spike", "medium", "today"), ("recheck", "info", "")]
    surge = [f for f in a["findings"] if f["kind"] == "surge"][0]
    assert steps[0]["id"] == "surge:2026-10-22" and steps[0]["actions"] == plan.ACTIONS["surge"] and steps[0]["evidence"] == surge["detail"] and steps[0]["title"] == surge["title"]
    assert steps[-1]["id"] == "recheck" and "every two hours" in steps[-1]["actions"][0] and steps[-1]["actions"] != steps[0]["actions"]


def test_the_campaigns_that_moved_are_named_with_what_they_spent():
    rows = rows_for(14) + today_rows(10, 12, boost={9: 60, 10: 60, 11: 60})
    camps = usual_camps() + [camp("1", "Brand", TODAY, 100), camp("2", "Generic", TODAY, 640)]
    step = [s for s in plan.build(run(rows, 12, campaigns=camps))["steps"] if s["kind"] == "spike"][0]
    assert step["where"] == ["Generic"] and step["examples"] == ["\"Generic\": $640 against a usual $140"]


def test_a_muted_finding_makes_no_step_and_a_low_one_does():
    rows = rows_for(14) + today_rows(10, 12, boost={9: 60, 10: 60, 11: 60})
    assert plan.build(run(rows, 12, saved={"mutedUntil": "2026-10-22"})) == {"steps": []}
    low = plan.build(run(rows_for(14) + today_rows(16, 12), 12))["steps"]
    assert [(s["kind"], s["level"]) for s in low] == [("spike", "low"), ("recheck", "info")]


def test_at_most_six_steps_and_three_places():
    a = {"findings": [{"kind": "spike", "key": "2026-10-%02d" % d, "level": "medium", "title": "t", "detail": "d", "day": "today",
                       "cause": {"movers": [{"name": "C%d" % i, "cost": 1000.0, "usual": 5.0} for i in range(5)]}} for d in range(9, 0, -1)]}
    steps = plan.build(a)["steps"]
    assert len(steps) == 7 and steps[-1]["id"] == "recheck" and len(steps[0]["where"]) == 3 and steps[0]["examples"][0] == "\"C0\": $1,000 against a usual $5.00"
    assert steps[0]["id"] == "spike:2026-10-01" and steps[5]["id"] == "spike:2026-10-06"


def test_equal_levels_are_ordered_by_kind_and_then_by_day():
    def f(kind, level="medium", key="2026-10-22", day="today"):
        return {"kind": kind, "key": key, "level": level, "title": kind, "detail": kind, "day": day}
    a = {"findings": [f("drop"), f("budget"), f("ceiling"), f("spike"), f("surge"), f("spike", key="2026-10-21", day="yesterday"), f("surge", level="low")]}
    assert [(s["kind"], s["level"], s["day"]) for s in plan.build(a)["steps"][:-1]] == [("surge", "medium", "today"), ("spike", "medium", "today"), ("spike", "medium", "yesterday"), ("ceiling", "medium", "today"),
                                                                                       ("budget", "medium", "today"), ("drop", "medium", "today")]
