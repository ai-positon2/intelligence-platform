"""Slot Checker parsing + derivation.

The failure this file mostly exists to prevent: reading the source sheet as one
run when it is actually several. A practice the agent re-scraped on three
consecutive days appears three times with three different sets of counts, so
summing rows overstates availability by roughly 70% -- and does it invisibly,
because the total still looks like a plausible number. Every "latest wins"
assertion below is guarding that.

The rest pin the source's real irregularities, all present in the 2026-08-21
export: a practice whose state survives only in its URL, two practices whose
cities collapse to the same string, 25 rows with no Location at all, a practice
with no slot rows whatsoever, and two booking systems with incompatible URLs.
"""
import json
import os
import sys
import tempfile

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.import_slot_checker_snapshot import build_snapshot  # noqa: E402
from tracker import slot_checker as sc  # noqa: E402

GD = "https://www.gentledental.com/ols?location_title=quincy&state=ma"
GD_DOVER = "https://www.gentledental.com/ols?location_title=dover&state=nh"
JARVIS = "https://schedule.jarvisanalytics.com/frame/42-north-dental?location_id=5900"

LP_HEADER = ["Account", "Location", "URL", "Comment", "Location Name"]
SLOT_HEADER = ["Url", "Service Name", "Execution Time", "2026-08-12", "2026-08-13", "2026-08-14"]


def _snap(lp_rows, slot_rows):
    return build_snapshot([LP_HEADER] + lp_rows, [SLOT_HEADER] + slot_rows)


@pytest.fixture(autouse=True)
def _no_cache():
    """This module has a real 300s TTL cache; leaving it warm would let one
    test's fixture answer another test's fetch()."""
    sc.reset_cache()
    yield
    sc.reset_cache()


# ── the multi-run problem ─────────────────────────────────────────────────────

def test_the_newest_observation_is_what_current_availability_means():
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 5, 5, 5],
         [GD, "Emergency Exam", "2026-08-13T09:00:00", 1, 0, 2]],
    )
    d = sc.build_dashboard(snap)
    assert d["totals"]["slots"] == 3, "summed the runs instead of taking the newest"
    assert d["practices"][0]["services"][0]["counts"] == [1, 0, 2]
    assert d["practices"][0]["services"][0]["runs"] == 2


def test_rows_out_of_chronological_order_still_resolve_to_the_newest():
    """The sheet is not sorted by execution time, so ordering cannot be assumed."""
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-13T09:00:00", 4, 0, 0],
         [GD, "Emergency Exam", "2026-08-11T09:00:00", 9, 9, 9]],
    )
    assert sc.build_dashboard(snap)["totals"]["slots"] == 4


def test_one_service_being_rechecked_does_not_disturb_another():
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 5, 0, 0],
         [GD, "Emergency Exam", "2026-08-13T09:00:00", 1, 0, 0],
         [GD, "Patient Exam", "2026-08-11T09:00:00", 7, 0, 0]],
    )
    d = sc.build_dashboard(snap)
    assert d["totals"]["slots"] == 8
    assert {s["name"]: s["total"] for s in d["practices"][0]["services"]} == {
        "Emergency Exam": 1, "Patient Exam": 7}


def test_service_pair_count_counts_pairs_not_rows():
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 1, 0, 0],
         [GD, "Emergency Exam", "2026-08-12T09:00:00", 1, 0, 0],
         [GD, "Emergency Exam", "2026-08-13T09:00:00", 1, 0, 0]],
    )
    assert sc.build_dashboard(snap)["totals"]["service_pairs"] == 1


# ── identity: the source has three partial sources of truth ──────────────────

def test_state_falls_back_to_the_url_when_location_name_omits_it():
    """079-GentleDental-Dover carries no state suffix; only its URL knows NH."""
    snap = _snap([["Gentle Dental", "Dover,NH", GD_DOVER, "Calendar View",
                   "079-GentleDental-Dover"]], [])
    assert snap["locations"][0]["state"] == "NH"


def test_two_practices_in_the_same_city_stay_distinguishable():
    """Location Name flattens both Worcester sites to 'Worcester'; the Location
    column is the only thing that tells them apart, so it wins for display."""
    a = "https://www.gentledental.com/ols?location_title=worcester-shrewsbury-st&state=ma"
    b = "https://www.gentledental.com/ols?location_title=worcester-trolley&state=ma"
    snap = _snap([
        ["Gentle Dental", "Worcester-shrewsbury-st", a, "Calendar View", "022-GentleDental-Worcester-MA"],
        ["Gentle Dental", "Worcester-at-the-trolley-yard", b, "Calendar View", "061-GentleDental-WorcesterTrolley-MA"],
    ], [])
    names = sorted(l["name"] for l in snap["locations"])
    assert names == ["Worcester Shrewsbury St", "Worcester at the Trolley Yard"]
    assert len(set(names)) == 2


def test_a_row_with_no_location_falls_back_to_the_account_name():
    """25 of the 82 rows have an empty Location; the Account reads better than
    a bare office number for those independents."""
    snap = _snap([["Wellesley Dental Group", None, JARVIS, "Calendar View",
                   "080-WellesleyDental-Wellesley-MA"]], [])
    assert snap["locations"][0]["name"] == "Wellesley"
    assert snap["locations"][0]["brand"] == "Wellesley Dental"


def test_office_number_and_brand_come_from_the_location_name():
    snap = _snap([["DCA Hollidaysburg", "Hollidaysburg", JARVIS, "Calendar View",
                   "097-DCA-Hollidaysburg-PA"]], [])
    loc = snap["locations"][0]
    assert (loc["office"], loc["brand"], loc["state"]) == ("097", "DCA", "PA")


def test_the_two_booking_systems_are_told_apart():
    snap = _snap([
        ["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"],
        ["DCA Hollidaysburg", "Hollidaysburg", JARVIS, "Calendar View", "097-DCA-Hollidaysburg-PA"],
    ], [])
    got = {l["system"]: l["key"] for l in snap["locations"]}
    assert got == {"gentledental": "quincy", "jarvis": "5900"}


# ── the date axis is whatever was scraped ────────────────────────────────────

def test_the_date_window_is_read_from_the_header_not_assumed():
    header = ["Url", "Service Name", "Execution Time", "2026-09-01", "2026-09-02"]
    snap = build_snapshot(
        [LP_HEADER, ["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [header, [GD, "Emergency Exam", "2026-08-31T09:00:00", 2, 3]],
    )
    assert snap["dates"] == ["2026-09-01", "2026-09-02"]
    assert sc.build_dashboard(snap)["totals"]["window_days"] == 2


def test_trailing_blank_header_columns_are_not_treated_as_dates():
    """openpyxl pads rows out to the sheet's widest column; those pads are not
    days and must not become zero-slot columns on the chart."""
    header = SLOT_HEADER + [None, None, ""]
    snap = build_snapshot(
        [LP_HEADER, ["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [header, [GD, "Emergency Exam", "2026-08-11T09:00:00", 1, 1, 1, None, None, None]],
    )
    assert len(snap["dates"]) == 3


def test_a_short_row_is_padded_rather_than_skewing_the_columns():
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 4]],
    )
    d = sc.build_dashboard(snap)
    assert d["practices"][0]["counts"] == [4, 0, 0]
    assert [r["slots"] for r in d["by_date"]] == [4, 0, 0]
    # Also at the per-service level, which is what the heatmap indexes by date.
    assert d["practices"][0]["services"][0]["counts"] == [4, 0, 0]
    assert d["by_service"][0]["counts"] == [4, 0, 0]


def test_non_numeric_and_blank_cells_read_as_zero_not_as_a_crash():
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", "", "n/a", 3]],
    )
    assert sc.build_dashboard(snap)["practices"][0]["counts"] == [0, 0, 3]


# ── status: a broken crawl must not read as a business finding ───────────────

def test_a_practice_with_no_rows_is_no_data_not_zero_availability():
    snap = _snap([["Torrington Dental Care", None, JARVIS, "Calendar View",
                   "052-TorringtonDental-Torrington-CT"]], [])
    d = sc.build_dashboard(snap)
    assert d["practices"][0]["status"] == "no-data"
    assert d["totals"]["practices_no_data"] == 1
    assert d["totals"]["practices_zero"] == 0
    assert [a["name"] for a in d["alerts"]["no_data"]] == ["Torrington"]
    assert d["alerts"]["zero"] == []


def test_a_practice_checked_and_found_empty_is_zero_not_no_data():
    snap = _snap(
        [["Gentle Dental", "exeter", GD, "Calendar View", "117-GentleDental-Exeter-NH"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 0, 0, 0]],
    )
    d = sc.build_dashboard(snap)
    assert d["practices"][0]["status"] == "none"
    assert d["totals"]["practices_zero"] == 1
    assert d["totals"]["practices_no_data"] == 0


def test_a_barely_bookable_practice_is_flagged_thin():
    snap = _snap(
        [["Gentle Dental", "auburn", GD, "Calendar View", "049-WillowRun-Auburn-ME"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 1, 1, 0]],
    )
    d = sc.build_dashboard(snap)
    assert d["practices"][0]["status"] == "thin"
    assert [a["total"] for a in d["alerts"]["thin"]] == [2]


def test_an_unbookable_service_is_listed_even_when_the_practice_is_healthy():
    """The practice-level total hides this: Quincy looks fine, but nobody can
    book an ortho consult there."""
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 40, 40, 40],
         [GD, "Free Orthodontic Consultation", "2026-08-11T09:00:00", 0, 0, 0]],
    )
    d = sc.build_dashboard(snap)
    assert d["practices"][0]["status"] == "open"
    assert [(u["name"], u["service"]) for u in d["alerts"]["unbookable_services"]] == [
        ("Quincy", "Free Orthodontic Consultation")]


# ── derived rollups ─────────────────────────────────────────────────────────

def test_lead_days_is_the_first_day_with_anything_open():
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 0, 0, 6]],
    )
    assert sc.build_dashboard(snap)["practices"][0]["lead_days"] == 2


def test_lead_days_is_none_when_nothing_is_open_rather_than_zero():
    """Zero would sort as 'bookable today', the exact opposite of the truth."""
    snap = _snap(
        [["Gentle Dental", "exeter", GD, "Calendar View", "117-GentleDental-Exeter-NH"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 0, 0, 0]],
    )
    assert sc.build_dashboard(snap)["practices"][0]["lead_days"] is None


def test_weekday_rollup_only_reports_weekdays_the_window_actually_covers():
    snap = _snap(
        [["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [[GD, "Emergency Exam", "2026-08-11T09:00:00", 1, 2, 3]],
    )
    d = sc.build_dashboard(snap)
    assert [r["day"] for r in d["by_weekday"]] == ["Wed", "Thu", "Fri"]
    assert d["by_date"][0]["weekend"] is False


def test_weekend_days_are_marked_as_such():
    header = ["Url", "Service Name", "Execution Time", "2026-08-15", "2026-08-16"]
    snap = build_snapshot(
        [LP_HEADER, ["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"]],
        [header, [GD, "Emergency Exam", "2026-08-14T09:00:00", 3, 1]],
    )
    d = sc.build_dashboard(snap)
    assert [r["weekend"] for r in d["by_date"]] == [True, True]
    assert [r["day"] for r in d["by_weekday"]] == ["Sat", "Sun"]


def test_state_rollup_averages_over_practices_not_over_slots():
    snap = _snap([
        ["Gentle Dental", "quincy", GD, "Calendar View", "007-GentleDental-Quincy-MA"],
        ["DCA Hollidaysburg", "Hollidaysburg", JARVIS, "Calendar View", "097-DCA-Hollidaysburg-PA"],
    ], [[GD, "Emergency Exam", "2026-08-11T09:00:00", 10, 10, 10]])
    by = {r["state"]: r for r in sc.build_dashboard(snap)["by_state"]}
    assert by["MA"]["avg"] == 30.0
    assert by["PA"]["slots"] == 0 and by["PA"]["zero"] == 1


# ── degradation ─────────────────────────────────────────────────────────────

def test_a_missing_snapshot_renders_empty_rather_than_raising():
    d = sc.build_dashboard(sc.load_snapshot("/nonexistent/slot_checker_snapshot.json"))
    assert d["totals"]["practices"] == 0
    assert d["practices"] == [] and d["dates"] == []


def test_a_corrupt_snapshot_renders_empty_rather_than_raising():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write("{not json at all")
        bad = f.name
    assert sc.load_snapshot(bad)["locations"] == []


def test_a_json_snapshot_of_the_wrong_shape_renders_empty():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([1, 2, 3], f)
        wrong = f.name
    assert sc.load_snapshot(wrong)["locations"] == []


def test_empty_sheets_produce_an_empty_dashboard_not_a_divide_by_zero():
    d = sc.build_dashboard(build_snapshot([LP_HEADER], [SLOT_HEADER]))
    assert d["totals"]["avg_per_practice"] == 0
    assert d["totals"]["busiest_day"] == ""


def test_a_ragged_snapshot_is_squared_up_against_the_date_axis():
    """The importer always emits full-length rows, so this defends the other way
    in: the snapshot is a committed JSON file a person can hand-edit, and
    load_snapshot accepts any JSON carrying a locations key. A row shorter than
    the date axis must be padded, and a row longer than it must be trimmed,
    because every chart indexes these lists positionally by date -- unequal
    lengths shift a service's availability onto the wrong days rather than
    failing loudly.
    """
    d = sc.build_dashboard({
        "dates": ["2026-08-12", "2026-08-13", "2026-08-14"],
        "locations": [{
            "office": "007", "name": "Quincy", "state": "MA",
            "services": [
                {"name": "Short", "observations": [{"at": "2026-08-11T09:00:00", "counts": [4]}]},
                {"name": "Long", "observations": [{"at": "2026-08-11T09:00:00",
                                                   "counts": [1, 1, 1, 99, 99]}]},
            ],
        }],
    })
    svc = {s["name"]: s["counts"] for s in d["practices"][0]["services"]}
    assert svc["Short"] == [4, 0, 0]
    assert svc["Long"] == [1, 1, 1], "trailing cells past the date axis leaked in"
    assert d["practices"][0]["counts"] == [5, 1, 1]
    assert [r["slots"] for r in d["by_date"]] == [5, 1, 1]


def test_a_service_with_no_observations_at_all_is_not_counted_as_bookable():
    d = sc.build_dashboard({
        "dates": ["2026-08-12"],
        "locations": [{"office": "007", "name": "Quincy", "state": "MA",
                       "services": [{"name": "Ghost", "observations": []}]}],
    })
    p = d["practices"][0]
    assert p["services"][0]["counts"] == [0]
    assert p["services"][0]["bookable"] is False
    assert p["status"] == "none"


# ── the committed snapshot itself ───────────────────────────────────────────

def test_the_committed_snapshot_parses_and_is_internally_consistent():
    """Recomputes the headline total independently of build_dashboard, so an
    arithmetic mistake in the rollups cannot agree with itself."""
    snap = sc.load_snapshot()
    if not snap["locations"]:
        pytest.skip("no committed snapshot in this checkout")
    expected = sum(
        sum(int(c or 0) for c in (sv["observations"][-1]["counts"] if sv["observations"] else []))
        for loc in snap["locations"] for sv in loc["services"]
    )
    d = sc.build_dashboard(snap)
    assert d["totals"]["slots"] == expected
    assert sum(r["slots"] for r in d["by_date"]) == expected
    assert sum(r["slots"] for r in d["by_state"]) == expected
    assert sum(r["slots"] for r in d["by_service"]) == expected
    assert sum(r["slots"] for r in d["by_weekday"]) == expected


def test_every_committed_practice_has_an_identity_worth_displaying():
    snap = sc.load_snapshot()
    if not snap["locations"]:
        pytest.skip("no committed snapshot in this checkout")
    for loc in snap["locations"]:
        assert loc["name"], f"nameless practice: {loc}"
        assert loc["state"], f"stateless practice: {loc['name']}"
        assert loc["office"], f"office-less practice: {loc['name']}"
