"""Spend Anomaly Alert: the arithmetic (tracker/paid_anomaly_math.py). No network."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_anomaly_math as m  # noqa: E402

TODAY = dt.date(2026, 10, 22)            # a Thursday
YESTERDAY = TODAY - dt.timedelta(days=1)


def rows_for(days_back, per_hour=10.0, *, hours=range(24), skip=()):
    """Hourly rows for the whole days before today: per_hour a day for each hour in `hours`."""
    out = []
    for n in range(1, days_back + 1):
        d = TODAY - dt.timedelta(days=n)
        if d in skip:
            continue
        for h in hours:
            out.append({"date": d.isoformat(), "hour": h, "cost": per_hour})
    return out


def today_rows(per_hour, through, boost=None):
    """Rows of today for the hours 0..through-1; `boost` {hour: cost} replaces an hour."""
    boost = boost or {}
    return [{"date": TODAY.isoformat(), "hour": h, "cost": boost.get(h, per_hour)} for h in range(through)]


def run(rows, hour, **kw):
    return m.analyse(rows, TODAY, hour, **kw)


def kinds(a):
    return [(f["kind"], f["key"], f["level"]) for f in a["findings"]]


# ── grade ───────────────────────────────────────────────────────────────────

def test_grade_needs_a_swing_over_the_floor():
    assert m.grade(120, 100, 25, 1.75, 2.75) is None                    # 20 more is under the floor, whatever the ratio
    assert m.grade(40, 20, 25, 1.75, 2.75) is None                      # twice, but 20 more is under the floor of 25
    assert m.grade(180, 100, 25, 1.75, 2.75) == "medium"
    assert m.grade(300, 100, 25, 1.75, 2.75) == "high"
    assert m.grade(140, 100, 25, 1.75, 2.75) == "low"                   # 1.4x is over half way to the medium ratio (1.375)
    assert m.grade(130, 100, 25, 1.75, 2.75) is None


def test_grade_high_needs_twice_the_floor_over():
    assert m.grade(280, 100, 100, 1.75, 2.75) == "medium"               # 2.8x but only 180 over, under 2 floors
    assert m.grade(300, 100, 100, 1.75, 2.75) == "high"                 # 200 over is two floors


def test_grade_with_nothing_usual_counts_the_size():
    assert m.grade(30, 0, 25, 1.75, 2.75) is None
    assert m.grade(50, 0, 25, 1.75, 2.75) == "medium"
    assert m.grade(149, 0, 25, 1.75, 2.75) == "medium"
    assert m.grade(150, 0, 25, 1.75, 2.75) == "high"
    assert m.grade(150, 10, 25, 1.75, 2.75) == "high"                   # a usual under half a floor counts as nothing


def test_floor_is_a_tenth_of_a_usual_day_or_the_minimum():
    assert m.floor_of(100) == 25.0
    assert m.floor_of(1000) == 100.0
    assert m.floor_of(100, 40) == 40.0
    assert m.floor_of(1000, 40) == 100.0
    assert m.floor_of(0) == 25.0


def test_sensitivity_changes_the_ratios():
    assert m.SENSITIVITY["low"] == (2.5, 4.0)
    assert m.SENSITIVITY["high"] == (1.4, 2.0)
    assert m.sensitivity_of("high") == "high"
    assert m.sensitivity_of("wild") == "normal"
    assert m.sensitivity_of(None) == "normal"


# ── the series ──────────────────────────────────────────────────────────────

def test_series_fills_days_without_a_row_with_zero_hours():
    days = m.series_of(rows_for(5, skip=(TODAY - dt.timedelta(days=3),)), TODAY)
    gap = TODAY - dt.timedelta(days=3)
    assert days[gap]["cost"] == 0.0 and days[gap]["hours"] == [0.0] * 24
    assert days[TODAY - dt.timedelta(days=1)]["cost"] == 240.0
    assert sorted(days)[0] == TODAY - dt.timedelta(days=m.WINDOW_DAYS) and sorted(days)[-1] == TODAY
    assert len(days) == m.WINDOW_DAYS + 1


def test_series_without_hours_has_none():
    rows = [{"date": (TODAY - dt.timedelta(days=n)).isoformat(), "hour": None, "cost": 50} for n in range(1, 6)]
    days = m.series_of(rows, TODAY)
    assert days[TODAY]["hours"] is None and days[TODAY - dt.timedelta(days=2)]["hours"] is None
    assert days[TODAY - dt.timedelta(days=2)]["cost"] == 50.0


def test_series_ignores_rows_outside_the_window_and_bad_ones():
    rows = [{"date": "2020-01-01", "hour": 3, "cost": 99}, {"date": "nope", "hour": 3, "cost": 99}, {"hour": 3, "cost": 5}, {"date": TODAY.isoformat(), "hour": 30, "cost": 7},
            {"date": TODAY.isoformat(), "hour": 3, "cost": "12.5"}, {"date": TODAY.isoformat(), "hour": 4, "cost": None}]
    days = m.series_of(rows, TODAY)
    assert days[TODAY]["cost"] == 19.5                                    # the bad hour still counts for the day, not for an hour
    assert days[TODAY]["hours"][3] == 12.5 and days[TODAY]["hours"][4] == 0.0
    assert sum(days[TODAY]["hours"]) == 12.5


def test_reference_takes_the_same_kind_of_day_when_there_are_enough():
    days = m.series_of(rows_for(15), TODAY)
    refs, basis = m.reference(days, TODAY, min(d for d, v in days.items() if v["cost"] > 0))
    assert basis == "weekdays" and all(d.weekday() < 5 for d in refs) and len(refs) == 10
    first = min(d for d, v in days.items() if v["cost"] > 0)
    refs, basis = m.reference(days, dt.date(2026, 10, 24), first)
    assert basis == "weekend days" and len(refs) == 4 and all(d.weekday() >= 5 for d in refs)
    refs, basis = m.reference(days, dt.date(2026, 10, 17), first)                       # only two weekend days in what was read
    assert basis == "the last %d days" % len(refs) and len(refs) == 10


def test_reference_stops_at_fourteen_days_and_before_the_first_spend():
    days = m.series_of(rows_for(15), TODAY)
    first = TODAY - dt.timedelta(days=15)
    refs, _ = m.reference(days, TODAY, first)
    assert all((TODAY - d).days <= 14 for d in refs)
    first = TODAY - dt.timedelta(days=3)
    refs, basis = m.reference(days, TODAY, first)
    assert [d for d in refs] == [TODAY - dt.timedelta(days=n) for n in (3, 2, 1)] and basis == "the last 3 days"


# ── today ───────────────────────────────────────────────────────────────────

def test_a_normal_day_has_nothing_to_say():
    a = run(rows_for(14) + today_rows(10, 12), 12)
    assert a["status"] == "ok" and a["findings"] == [] and a["level"] is None
    assert a["usualDay"] == 240.0 and a["today"]["cost"] == 120.0 and a["today"]["usualByNow"] == 120.0 and a["today"]["ratio"] == 1.0
    assert a["basis"] == "weekdays" and a["refDays"] == 10


def test_a_spike_by_this_hour_is_medium_and_names_when_it_began():
    a = run(rows_for(14) + today_rows(10, 12, boost={9: 60, 10: 60, 11: 60}), 12)
    # 9 hours of 10 and 3 of 60 = 270 so far, against 120 by now: 2.25x, 150 over
    spike = [f for f in a["findings"] if f["kind"] == "spike"][0]
    assert (spike["level"], spike["key"], spike["day"]) == ("medium", "2026-10-22", "today")
    assert spike["spent"] == 270.0 and spike["usual"] == 120.0 and spike["ratio"] == 2.25 and spike["started"] == 9
    assert spike["title"] == "Spending 2.2x its usual pace today" or spike["title"] == "Spending 2.3x its usual pace today"
    assert "270" in spike["detail"] and "120" in spike["detail"] and "09:00" in spike["detail"] and "12:00" in spike["detail"]
    assert spike["pace"] == 540
    assert a["status"] == "over" and a["level"] in ("medium", "high")


def test_a_surge_in_the_last_hours_is_found_before_the_day_total_shows_it():
    # 9 hours at 10, then 3 hours at 50: the day so far is 240 against a usual 120 by 12:00 (2.0x: medium) and the last three hours are 5x
    a = run(rows_for(14) + today_rows(10, 12, boost={9: 50, 10: 50, 11: 50}), 12)
    surge = [f for f in a["findings"] if f["kind"] == "surge"][0]
    assert surge["level"] == "high" and surge["spent"] == 150.0 and surge["usual"] == 30.0 and surge["ratio"] == 5.0 and surge["hours"] == 3
    assert surge["title"] == "Spending 5.0x its usual in the last 3 hours"
    assert "09:00 to 12:00" in surge["detail"]


def test_a_surge_is_high_while_the_day_so_far_is_only_a_little_over():
    # a usual 120 by 12:00; here 9 hours of 7 (63) and 3 hours of 40: 183 so far, 1.5x by now, but the last 3 hours are 4x
    a = run(rows_for(14) + today_rows(7, 12, boost={9: 40, 10: 40, 11: 40}), 12)
    assert [(f["kind"], f["level"]) for f in a["findings"]] == [("surge", "high"), ("spike", "low")]       # 1.5x by now is only a low spike
    assert a["status"] == "over"


def test_the_whole_day_already_spent_is_found_early_in_the_day():
    a = run(rows_for(14) + today_rows(10, 2, boost={0: 400, 1: 400}), 2)
    spike = [f for f in a["findings"] if f["kind"] == "spike"]
    assert len(spike) == 1 and spike[0]["level"] == "high" and spike[0]["ratio"] == 3.33 and spike[0]["usual"] == 240.0
    assert "usual pace" not in spike[0]["title"] and spike[0]["title"] == "Already spent 3.3x a usual day today"
    medium = [f for f in run(rows_for(14) + today_rows(10, 2, boost={0: 300, 1: 300}), 2)["findings"] if f["kind"] == "spike"]
    assert [f["level"] for f in medium] == ["medium"] and medium[0]["ratio"] == 2.5


def test_before_the_third_hour_the_pace_is_not_judged():
    a = run(rows_for(14) + today_rows(10, 2, boost={0: 90, 1: 90}), 2)
    assert [f["kind"] for f in a["findings"]] == ["surge"]                # 180 is under 1.75 a day and no pace is read this early; two hours at 9x their usual is a surge
    assert a["today"]["pace"] is None and a["findings"][0]["hours"] == 2
    b = run(rows_for(14) + today_rows(10, 1, boost={0: 90}), 1)
    assert b["findings"] == []                                            # one hour is too little to call a surge


def test_a_pace_is_the_usual_day_scaled():
    a = run(rows_for(14) + today_rows(20, 12), 12)
    spike = [f for f in a["findings"] if f["kind"] == "spike"][0]
    assert a["today"]["pace"] == 480 and spike["pace"] == 480 and spike["level"] == "medium" or spike["level"] == "high"
    assert spike["ratio"] == 2.0


def test_the_current_hour_is_left_out():
    rows = rows_for(14) + today_rows(10, 12) + [{"date": TODAY.isoformat(), "hour": 12, "cost": 5000}]
    a = run(rows, 12)
    assert a["today"]["cost"] == 120.0 and a["findings"] == []


def test_a_small_account_stays_quiet_under_the_floor():
    a = run(rows_for(14, per_hour=0.5) + today_rows(1.0, 12), 12)          # 12 a day usually, 12 by 12:00 against 6: double but 6 over
    assert a["findings"] == [] and a["status"] == "ok"


def test_an_idle_account_that_starts_spending_is_flagged_by_size():
    old = [d for d in (TODAY - dt.timedelta(days=n) for n in range(1, 15)) if d < TODAY - dt.timedelta(days=11)]
    rows = rows_for(14, skip=tuple(d for d in (TODAY - dt.timedelta(days=n) for n in range(1, 15)) if d not in old)) + [{"date": TODAY.isoformat(), "hour": 8, "cost": 80.0}]
    # it spent three weeks ago and nothing for eleven days: the usual is nothing, and 80 against a floor of 25 is flagged by its size
    a = run(rows, 12)
    assert a["status"] == "over" and [(f["kind"], f["level"]) for f in a["findings"] if f["kind"] == "spike"] == [("spike", "medium")]
    assert a["usualDay"] == 0.0


def test_an_account_with_no_spend_at_all_is_none():
    a = run([], 12)
    assert a["status"] == "none" and a["findings"] == [] and a["usualDay"] == 0.0


def test_too_few_days_is_new_and_a_saved_usual_judges_it():
    rows = rows_for(3) + today_rows(40, 12)
    a = run(rows, 12)
    assert a["status"] == "new" and a["findings"] == [] and "Fewer than 4 days" in a["notes"][0]
    b = run(rows, 12, saved={"usualDaily": 240})
    assert b["status"] == "over" and b["findings"][0]["kind"] == "spike" and b["findings"][0]["usual"] == 240.0 and b["usualDay"] == 240.0


def test_without_hours_only_the_whole_day_is_judged():
    rows = [{"date": (TODAY - dt.timedelta(days=n)).isoformat(), "hour": None, "cost": 240.0} for n in range(1, 15)] + [{"date": TODAY.isoformat(), "hour": None, "cost": 500.0}]
    a = run(rows, 12)
    assert a["today"]["hourly"] is False and a["curve"] is None and a["today"]["usualByNow"] is None
    spike = a["findings"][0]
    assert spike["kind"] == "spike" and spike["level"] == "medium" and spike["ratio"] == 2.08 and "no hours" in spike["detail"]
    assert not [f for f in a["findings"] if f["kind"] == "surge"]
    quiet = run(rows[:-1] + [{"date": TODAY.isoformat(), "hour": None, "cost": 300.0}], 12)
    assert quiet["findings"] == []


# ── yesterday ───────────────────────────────────────────────────────────────

def yesterday_rows(per_hour):
    return [{"date": YESTERDAY.isoformat(), "hour": h, "cost": per_hour} for h in range(24)]


def test_a_spike_yesterday_is_found_with_its_day_as_the_key():
    rows = rows_for(14, skip=(YESTERDAY,)) + yesterday_rows(30)
    a = run(rows, 6)
    f = [x for x in a["findings"] if x["day"] == "yesterday"][0]
    assert (f["kind"], f["key"], f["level"], f["spent"], f["usual"], f["ratio"]) == ("spike", "2026-10-21", "medium", 720.0, 240.0, 3.0) or f["level"] == "high"
    assert f["ratio"] == 3.0 and f["level"] == "high"                    # 3x is over 2.75x and 480 over is over two floors
    assert f["title"] == "Spent 3.0x a usual day yesterday"


def test_a_drop_yesterday_and_a_stop_yesterday():
    rows = rows_for(14, skip=(YESTERDAY,)) + yesterday_rows(1.0)          # 24 against 240
    a = run(rows, 6)
    f = [x for x in a["findings"] if x["kind"] == "drop"][0]
    assert f["level"] == "medium" and f["title"] == "Spent almost nothing yesterday" and f["key"] == "2026-10-21" and a["status"] == "under"
    f = [x for x in run(rows_for(14, skip=(YESTERDAY,)), 6)["findings"] if x["kind"] == "drop"][0]
    assert f["level"] == "medium" and f["title"] == "Spent nothing yesterday"                   # 240 a day is under ten minimum swings
    f = [x for x in run(rows_for(14, per_hour=12, skip=(YESTERDAY,)), 6)["findings"] if x["kind"] == "drop"][0]
    assert f["level"] == "high"                                                                # 288 a day is over ten (250)


def test_a_small_usual_is_not_a_drop():
    rows = rows_for(14, per_hour=0.5, skip=(YESTERDAY,))                   # 12 a day: under four floors
    assert run(rows, 6)["findings"] == []


# ── a drop today ────────────────────────────────────────────────────────────

def test_a_stop_today_is_found_in_the_afternoon_not_the_morning():
    rows = rows_for(14)
    a = run(rows, 16)
    f = a["findings"][0]
    assert (f["kind"], f["level"], f["title"], f["key"]) == ("drop", "medium", "Spending has stopped today", "2026-10-22") and a["status"] == "under"
    assert "by 14:00" in f["detail"] and "$140" in f["detail"] and "last 2 hours" in f["detail"]
    assert run(rows, 13)["findings"] == []                                # too early: a slow start
    b = run(rows + today_rows(1.0, 16), 16)
    assert b["findings"][0]["level"] == "medium" and b["findings"][0]["title"] == "Spending is far below its usual today"
    big = run(rows_for(14, per_hour=20), 16)                              # 280 by 14:00 is over ten minimum swings: a stop is high
    assert big["findings"][0]["level"] == "high"


def test_a_normal_afternoon_is_not_a_drop():
    assert run(rows_for(14) + today_rows(10, 16), 16)["findings"] == []
    assert run(rows_for(14) + today_rows(4, 16), 16)["findings"] == []     # 40% of usual is low but not a drop


# ── ceiling, budget and muting ──────────────────────────────────────────────

def test_a_saved_ceiling_is_high():
    a = run(rows_for(14) + today_rows(10, 12), 12, saved={"maxDaily": 100})
    f = [x for x in a["findings"] if x["kind"] == "ceiling" and x["key"] == "2026-10-22"][0]
    assert f["level"] == "high" and f["key"] == "2026-10-22" and f["ceiling"] == 100.0 and f["spent"] == 120.0
    assert f["title"] == "Spent $120 today, above the $100 a day the team set"
    b = run(rows_for(14) + today_rows(10, 12), 12, saved={"maxDaily": 120})
    assert [x["key"] for x in b["findings"] if x["kind"] == "ceiling" and x["day"] == "today"] == ["2026-10-22"]       # reaching it counts
    c = run(rows_for(14) + today_rows(10, 12), 12, saved={"maxDaily": 121})
    assert [x["key"] for x in c["findings"] if x["kind"] == "ceiling" and x["day"] == "today"] == []                   # 120 is under 121


def test_the_ceiling_is_also_checked_for_yesterday():
    a = run(rows_for(14), 6, saved={"maxDaily": 200})
    f = [x for x in a["findings"] if x["kind"] == "ceiling"]
    assert len(f) == 1 and f[0]["key"] == "2026-10-21" and f[0]["day"] == "yesterday"


def test_more_than_the_budgets_allow():
    rows = rows_for(14, per_hour=10) + today_rows(10, 24)                  # 240 a day
    a = run(rows, 24, budget=100)
    f = [x for x in a["findings"] if x["kind"] == "budget"]
    assert {x["key"] for x in f} == {"2026-10-21", "2026-10-22"} and all(x["level"] == "medium" for x in f) and f[0]["ratio"] == 2.4
    a = run(rows, 24, budget=80)
    assert all(x["level"] == "high" for x in a["findings"] if x["kind"] == "budget")      # 3x
    a = run(rows, 24, budget=120)
    assert [x for x in a["findings"] if x["kind"] == "budget"] == []                      # exactly twice is allowed
    a = run(rows, 24, budget=None)
    assert [x for x in a["findings"] if x["kind"] == "budget"] == []


def test_a_planned_push_can_be_muted_until_a_date():
    rows = rows_for(14) + today_rows(10, 12, boost={9: 60, 10: 60, 11: 60})
    a = run(rows, 12, saved={"mutedUntil": "2026-10-22"})
    assert all(f["level"] == "info" and f["muted"] for f in a["findings"] if f["kind"] in ("spike", "surge"))
    assert a["findings"][0]["title"].startswith("Expected (muted until 2026-10-22): ")
    assert a["status"] == "ok" and a["level"] == "info"
    b = run(rows, 12, saved={"mutedUntil": "2026-10-21"})
    assert [f["level"] for f in b["findings"] if f["kind"] == "spike"] == ["medium"]
    c = run(rows, 12, saved={"mutedUntil": "bad"})
    assert [f["level"] for f in c["findings"] if f["kind"] == "spike"] == ["medium"]


def test_muting_does_not_hide_a_ceiling_or_a_drop():
    a = run(rows_for(14) + today_rows(10, 12), 12, saved={"mutedUntil": "2026-12-31", "maxDaily": 100})
    assert [(f["level"], f["key"]) for f in a["findings"] if f["kind"] == "ceiling" and f["key"] == "2026-10-22"] == [("high", "2026-10-22")]
    b = run(rows_for(14), 16, saved={"mutedUntil": "2026-12-31"})
    assert b["findings"][0]["kind"] == "drop" and b["findings"][0]["level"] == "medium"


def test_sensitivity_moves_what_is_flagged():
    rows = rows_for(14) + today_rows(16, 12)                              # 192 against 120 by now: 1.6x, spread over the hours so there is no surge
    assert [(f["kind"], f["level"]) for f in run(rows, 12, sensitivity="normal")["findings"]] == [("spike", "low")]
    assert [(f["kind"], f["level"]) for f in run(rows, 12, sensitivity="high")["findings"]] == [("spike", "medium")]
    assert run(rows, 12, sensitivity="low")["findings"] == []
    assert [(f["kind"], f["level"]) for f in run(rows, 12)["findings"]] == [("spike", "low")]                  # normal is the default


def test_a_saved_minimum_swing_raises_the_floor():
    rows = rows_for(14) + today_rows(10, 12, boost={9: 60, 10: 60, 11: 60})
    assert [f["kind"] for f in run(rows, 12, saved={"minSwing": 500})["findings"]] == []
    assert run(rows, 12, saved={"minSwing": 500})["floor"] == 500.0


# ── the causes ──────────────────────────────────────────────────────────────

def camp(cid, name, date, cost, start=""):
    return {"id": cid, "name": name, "date": date.isoformat() if hasattr(date, "isoformat") else date, "cost": cost, "startDate": start}


def usual_camps():
    out = []
    for n in range(1, 15):
        d = TODAY - dt.timedelta(days=n)
        out += [camp("1", "Brand", d, 100), camp("2", "Generic", d, 140)]
    return out


def test_the_cause_is_the_one_campaign_that_spent_the_excess():
    rows = usual_camps() + [camp("1", "Brand", TODAY, 100), camp("2", "Generic", TODAY, 640)]
    c = m.cause_for(TODAY, rows, [TODAY - dt.timedelta(days=n) for n in range(1, 15)])
    assert c["kind"] == "one" and c["movers"][0]["name"] == "Generic" and c["movers"][0]["excess"] == 500.0 and c["movers"][0]["share"] == 1.0
    assert c["text"] == "100% of the excess is one campaign: \"Generic\" spent $640 against a usual $140."


def test_a_new_campaign_is_named_as_new():
    rows = usual_camps() + [camp("1", "Brand", TODAY, 100), camp("2", "Generic", TODAY, 140), camp("3", "Black Friday", TODAY, 400, start="2026-10-21")]
    c = m.cause_for(TODAY, rows, [TODAY - dt.timedelta(days=n) for n in range(1, 15)])
    assert c["kind"] == "new" and c["text"] == "A campaign started on 2026-10-21 did it: \"Black Friday\" spent $400 (100% of the excess)."


def test_an_old_campaign_is_not_new_and_a_bad_start_date_is_ignored():
    refs = [TODAY - dt.timedelta(days=n) for n in range(1, 15)]
    rows = usual_camps() + [camp("3", "Old", TODAY, 400, start="2026-01-01")]
    assert m.cause_for(TODAY, rows, refs)["kind"] == "one"
    rows = usual_camps() + [camp("3", "Odd", TODAY, 400, start="soon")]
    assert m.cause_for(TODAY, rows, refs)["kind"] == "one"


def test_a_spread_excess_lists_the_movers():
    rows = usual_camps() + [camp("1", "Brand", TODAY, 250), camp("2", "Generic", TODAY, 300), camp("4", "Display", TODAY, 140)]
    c = m.cause_for(TODAY, rows, [TODAY - dt.timedelta(days=n) for n in range(1, 15)])
    assert c["kind"] == "many"
    assert c["text"] == "It is spread over campaigns: \"Generic\" $300 (usual $140); \"Brand\" $250 (usual $100); \"Display\" $140 (usual $0.00)."
    assert [x["name"] for x in c["movers"]] == ["Generic", "Brand", "Display"]


def test_no_excess_gives_no_cause_and_no_rows_gives_none():
    refs = [TODAY - dt.timedelta(days=n) for n in range(1, 15)]
    assert m.cause_for(TODAY, usual_camps(), refs) == {"kind": "none", "text": "", "movers": []}
    assert m.cause_for(TODAY, [], refs) is None
    assert m.cause_for(TODAY, None, refs) is None


def test_changes_text_puts_an_unfamiliar_user_first():
    ch = [{"at": "2026-10-22 09:00:00", "user": "ann@x.com", "label": "a budget raised", "newUser": False}, {"at": "2026-10-22 11:00:00", "user": "zed@y.com", "label": "a campaign created", "newUser": True}]
    t = m.changes_text(ch)
    assert t == "Changes lately: a campaign created by zed@y.com (made no change in the days before); a budget raised by ann@x.com."
    assert m.changes_text([]) == "" and m.changes_text(None) == ""
    many = [{"at": "2026-10-22 0%d:00:00" % i, "user": "u%d" % i, "label": "x"} for i in range(5)]
    assert m.changes_text(many).endswith("; and 2 more.")


def test_the_findings_carry_the_cause_and_the_changes():
    rows = rows_for(14) + today_rows(10, 12, boost={9: 60, 10: 60, 11: 60})
    camps = usual_camps() + [camp("1", "Brand", TODAY, 100), camp("2", "Generic", TODAY, 640)]
    ch = [{"at": "2026-10-22 08:30:00", "user": "zed@y.com", "label": "a budget raised", "newUser": True}]
    a = run(rows, 12, campaigns=camps, changes=ch)
    spike = [f for f in a["findings"] if f["kind"] == "spike"][0]
    assert spike["cause"]["kind"] == "one"
    assert "\"Generic\" spent $640" in spike["detail"] and "zed@y.com (made no change in the days before)" in spike["detail"]


# ── the account's figures ───────────────────────────────────────────────────

def test_the_curve_is_today_against_the_usual_and_the_highest_seen():
    a = run(rows_for(14) + today_rows(10, 12), 12)
    c = a["curve"]
    assert c["hour"] == 12 and len(c["today"]) == 13 and len(c["usual"]) == 25 and len(c["upper"]) == 25
    assert c["today"][12] == 120.0 and c["usual"][12] == 120.0 and c["usual"][24] == 240.0 and c["upper"][24] == 240.0 and c["today"][0] == 0.0


def test_the_days_are_listed_oldest_first_with_today_partial():
    a = run(rows_for(14) + today_rows(10, 12), 12)
    assert len(a["days"]) == m.WINDOW_DAYS + 1 and a["days"][-1] == {"date": "2026-10-22", "cost": 120.0, "partial": True}
    assert a["days"][-2] == {"date": "2026-10-21", "cost": 240.0, "partial": False} and a["days"][0]["date"] == "2026-10-07"


def test_snapshot_and_sort_order():
    a = run(rows_for(14) + today_rows(10, 12, boost={9: 60, 10: 60, 11: 60}), 12)
    snap = m.snapshot_of(a)
    assert snap["status"] == "over" and snap["today"] == 270.0 and snap["usualByNow"] == 120.0 and snap["usualDay"] == 240.0 and snap["findings"] == len(a["findings"])
    ok = dict(run(rows_for(14) + today_rows(10, 12), 12), name="B")
    over = dict(a, name="A")
    none = dict(run([], 12), name="C")
    assert [x["name"] for x in sorted([none, ok, over], key=m.rank_key)] == ["A", "B", "C"]


def test_a_weekend_day_is_compared_with_weekend_days():
    sat = dt.date(2026, 10, 24)
    rows = []
    for n in range(1, 15):
        d = sat - dt.timedelta(days=n)
        per = 5.0 if d.weekday() >= 5 else 20.0
        rows += [{"date": d.isoformat(), "hour": h, "cost": per} for h in range(24)]
    rows += [{"date": sat.isoformat(), "hour": h, "cost": 14.0} for h in range(12)]
    a = m.analyse(rows, sat, 12)
    assert a["basis"] == "weekend days" and a["usualDay"] == 120.0 and a["refDays"] == 4          # 4 weekend days of 120
    assert [f["kind"] for f in a["findings"]] == ["spike", "surge"] or a["findings"][0]["kind"] == "spike"


# ── the edges ───────────────────────────────────────────────────────────────

def test_the_edges_of_a_grade_count():
    assert m.grade(60, 35, 25, 1.75, 2.75) == "low"                       # exactly the floor over counts
    assert m.grade(175, 100, 25, 1.75, 2.75) == "medium"                  # exactly the ratio counts
    assert m.grade(46, 20, 25, 1.75, 2.75) == "medium"                    # a usual of 20 is more than half a floor of 25: compared as a ratio (2.3x), not by its size


def test_the_ratios_are_pinned():
    assert m.SENSITIVITY == {"low": (2.5, 4.0), "normal": (1.75, 2.75), "high": (1.4, 2.0)}
    assert (m.SURGE_MID, m.SURGE_HIGH, m.BUDGET_OVER, m.BUDGET_HIGH) == (2.5, 4.0, 2.2, 3.0)


def test_one_hour_spent_is_judged_against_the_whole_usual_day():
    a = run(rows_for(14) + today_rows(10, 1, boost={0: 600}), 1)
    assert [(f["kind"], f["level"]) for f in a["findings"] if f["kind"] == "spike"] == [("spike", "medium")]


def test_a_day_that_spends_late_is_not_judged_by_an_empty_morning():
    late = rows_for(14, per_hour=20, hours=range(12, 24))                 # 240 a day, all after noon
    a = run(late + today_rows(10, 6), 6)                                  # 60 by 06:00 against nothing usual: the guard is a seventh of the day, 36
    assert [f for f in a["findings"] if f["kind"] == "spike"] == []
    b = run(late + today_rows(20, 6), 6)                                  # 120 against a guard of 36: really more
    assert [f["kind"] for f in b["findings"] if f["kind"] == "spike"] == ["spike"]


def test_a_surge_on_a_small_account_uses_half_the_minimum_swing():
    rows = rows_for(14, per_hour=3.0) + today_rows(3.0, 9) + [{"date": TODAY.isoformat(), "hour": h, "cost": 10.0} for h in (9, 10, 11)]
    a = run(rows, 12)
    assert [(f["kind"], f["level"]) for f in a["findings"]] == [("surge", "medium")]                # 30 against 9 is 21 over, over a floor of 12.5 and under 25 (the day so far is 21 over its usual: under 25)


def test_a_surge_is_high_from_four_times_and_medium_below():
    mid = run(rows_for(14) + today_rows(10, 9) + [{"date": TODAY.isoformat(), "hour": h, "cost": 35.0} for h in (9, 10, 11)], 12)    # 105 against 30: 3.5x
    assert [f["level"] for f in mid["findings"] if f["kind"] == "surge"] == ["medium"]
    top = run(rows_for(14) + today_rows(10, 9) + [{"date": TODAY.isoformat(), "hour": h, "cost": 40.0} for h in (9, 10, 11)], 12)     # 120 against 30: 4x
    assert [f["level"] for f in top["findings"] if f["kind"] == "surge"] == ["high"]


def test_exactly_twice_and_a_fifth_of_the_budget_is_over():
    rows = rows_for(14, per_hour=22.0, hours=range(10)) + today_rows(22.0, 10)                      # 220 a day
    a = run(rows, 12, budget=100)
    assert sorted((f["key"], f["level"], f["ratio"]) for f in a["findings"] if f["kind"] == "budget") == [("2026-10-21", "medium", 2.2), ("2026-10-22", "medium", 2.2)]


def test_a_drop_needs_the_usual_to_be_four_minimum_swings():
    assert run(rows_for(14, per_hour=6.0), 16)["findings"] == []          # 84 by 14:00 is under 100: too small to miss
    assert [f["kind"] for f in run(rows_for(14, per_hour=8.0), 16)["findings"]] == ["drop"]          # 112 by then is enough


def test_a_drop_is_a_fifth_of_the_usual_at_most_today_and_a_share_yesterday():
    rows = rows_for(14) + [{"date": TODAY.isoformat(), "hour": h, "cost": 1.5} for h in range(14)]        # 21 by 14:00 against 140: exactly 15%
    assert [f["kind"] for f in run(rows, 16)["findings"]] == ["drop"]
    rows = rows_for(14) + [{"date": TODAY.isoformat(), "hour": h, "cost": 1.6} for h in range(14)]        # 22.4: over 15%
    assert run(rows, 16)["findings"] == []
    rows = rows_for(14, skip=(YESTERDAY,)) + yesterday_rows(3.0)                                          # 72 against 240: 30%, not a drop
    assert [f for f in run(rows, 6)["findings"] if f["kind"] == "drop"] == []
    rows = rows_for(14, skip=(YESTERDAY,)) + yesterday_rows(2.0)                                          # 48 against 240: exactly 20%
    assert [f["kind"] for f in run(rows, 6)["findings"] if f["kind"] == "drop"] == ["drop"]


def test_the_cause_at_exactly_sixty_percent_is_one_campaign():
    refs = [TODAY - dt.timedelta(days=n) for n in range(1, 15)]
    rows = [camp("1", "A", TODAY - dt.timedelta(days=n), 100) for n in range(1, 15)] + [camp("2", "B", TODAY - dt.timedelta(days=n), 100) for n in range(1, 15)]
    rows += [camp("1", "A", TODAY, 160), camp("2", "B", TODAY, 140)]                                      # excess 60 and 40
    c = m.cause_for(TODAY, rows, refs)
    assert c["kind"] == "one" and c["movers"][0]["share"] == 0.6


def test_a_campaign_at_its_usual_is_not_a_mover():
    refs = [TODAY - dt.timedelta(days=n) for n in range(1, 15)]
    rows = usual_camps() + [camp("1", "Brand", TODAY, 100), camp("2", "Generic", TODAY, 140), camp("4", "Display", TODAY, 150), camp("5", "Shopping", TODAY, 170)]
    c = m.cause_for(TODAY, rows, refs)
    assert [x["name"] for x in c["movers"]] == ["Shopping", "Display"] and c["kind"] == "many"


def test_a_campaign_started_within_three_days_is_new_and_four_days_is_not():
    refs = [TODAY - dt.timedelta(days=n) for n in range(1, 15)]
    base = usual_camps() + [camp("1", "Brand", TODAY, 100), camp("2", "Generic", TODAY, 140)]
    assert m.cause_for(TODAY, base + [camp("3", "Fresh", TODAY, 400, start="2026-10-19")], refs)["kind"] == "new"
    assert m.cause_for(TODAY, base + [camp("3", "Fresh", TODAY, 400, start="2026-10-18")], refs)["kind"] == "one"
    assert m.cause_for(TODAY, base + [camp("3", "Fresh", TODAY, 400, start="2026-10-23")], refs)["kind"] == "one"          # not started yet on that day


def test_a_new_campaign_with_a_quarter_of_the_excess_is_named():
    refs = [TODAY - dt.timedelta(days=n) for n in range(1, 15)]
    rows = usual_camps() + [camp("1", "Brand", TODAY, 100), camp("2", "Generic", TODAY, 440), camp("3", "Fresh", TODAY, 140, start="2026-10-22")]      # excess 300 and 140: 32%
    c = m.cause_for(TODAY, rows, refs)
    assert c["kind"] == "new" and "\"Fresh\"" in c["text"] and "(32% of the excess)" in c["text"]
    rows = usual_camps() + [camp("1", "Brand", TODAY, 100), camp("2", "Generic", TODAY, 640), camp("3", "Fresh", TODAY, 140, start="2026-10-22")]      # 500 and 140: 22%
    assert m.cause_for(TODAY, rows, refs)["kind"] == "one"


def test_the_pace_is_judged_from_the_third_hour():
    a = run(rows_for(14) + today_rows(30, 3), 3)
    spike = [f for f in a["findings"] if f["kind"] == "spike"]
    assert [(f["title"], f["level"], f["pace"]) for f in spike] == [("Spending 2.5x its usual pace today", "medium", 600)]       # 90 against 30, but a usual by now is never read under a seventh of a day (36)


def _started(rows, hour, floor=25.0):
    days = m.series_of(rows, TODAY)
    first = min(d for d, v in days.items() if v["cost"] > 0)
    refs, _ = m.reference(days, TODAY, first)
    return m._started(days, TODAY, hour, m.Usual(days, refs), floor)


def test_the_hour_a_spike_began_is_the_first_hour_far_over_its_usual():
    assert _started(rows_for(14) + today_rows(10, 12, boost={7: 25, 9: 80}), 12) == 7                 # 25 is 2.5x the usual 10
    assert _started(rows_for(14) + today_rows(10, 12, boost={7: 24.9, 9: 80}), 12) == 9
    assert _started(rows_for(14) + today_rows(16, 12), 12) is None                                    # a little more every hour began nowhere
    assert _started(rows_for(14) + today_rows(10, 12) + [{"date": TODAY.isoformat(), "hour": 12, "cost": 5000}], 12) is None   # the hour in progress is not read


def test_a_small_hour_needs_a_third_of_the_floor_over_its_usual_too():
    small = rows_for(14, per_hour=2.0)
    assert _started(small + today_rows(2.0, 12, boost={5: 10.4}), 12, floor=25.0) == 5               # 2.5x is 5, a third of the floor over is 10.33
    assert _started(small + today_rows(2.0, 12, boost={5: 10.2}), 12, floor=25.0) is None
    assert _started(small + today_rows(2.0, 12, boost={5: 9.0}), 12, floor=6.0) == 5                 # a smaller floor lets 9 count (2 + 2)
