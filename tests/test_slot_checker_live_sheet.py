"""Slot Checker's live Google Sheet read (tracker/slot_checker.py).

Covers the seam between the real "LPs" + "Locations" tab shapes and
scripts.import_slot_checker_snapshot.build_snapshot(), which still expects
the older "All LPs" / "Available Slots Final" column order. Two things this
file exists specifically to catch:

  * "Locations" carries (office, category, url, service, checked_at, *dates),
    not (url, service, checked_at, *dates) -- _reorder_locations_rows() has
    to reshape every row correctly, including short/ragged ones (the Sheets
    API trims a row to its own last non-blank cell).
  * The Sheets API returns an execution-time cell as a plain display string
    ('8/11/2026 14:52:13'), not a datetime object the way openpyxl did for
    the old .xlsx pipeline. Observations are sorted by that string, so an
    un-normalised 'M/D/YYYY' timestamp sorts wrong ('8/9/2026' after
    '8/13/2026') and picks the wrong observation as "current".
"""
import os
import sys
import unittest.mock as mock

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import slot_checker as sc  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cache():
    sc.reset_cache()
    yield
    sc.reset_cache()


# ── _iso_stamp_from_sheet ────────────────────────────────────────────────────

def test_iso_stamp_parses_the_real_sheet_format():
    assert sc._iso_stamp_from_sheet("8/11/2026 14:52:13") == "2026-08-11T14:52:13"


def test_iso_stamp_sorts_chronologically_unlike_the_raw_string():
    """The bug this guards: '8/9/2026' > '8/13/2026' as plain strings, but
    Aug 9 is before Aug 13. Sorting the normalised values must get this
    right, or the wrong observation is picked as "current"."""
    raw = ["8/13/2026 8:58:48", "8/9/2026 14:00:00"]
    normalised = sorted(sc._iso_stamp_from_sheet(v) for v in raw)
    assert normalised == ["2026-08-09T14:00:00", "2026-08-13T08:58:48"]


def test_iso_stamp_falls_back_to_the_raw_string_on_unparseable_input():
    assert sc._iso_stamp_from_sheet("not a date") == "not a date"
    assert sc._iso_stamp_from_sheet("") == ""
    assert sc._iso_stamp_from_sheet(None) == ""


# ── _reorder_locations_rows ──────────────────────────────────────────────────

def test_reorder_moves_url_service_timestamp_to_the_front():
    rows = [
        ["Location", "Services", "Url", "Service Name", "", "8/12/2026", "8/13/2026"],
        ["004-GentleDental-Burlington-MA", "Emergency/ Ortho", "https://x.test/burlington",
         "Emergency Exam", "8/11/2026 14:52:13", "3", "5"],
    ]
    out = sc._reorder_locations_rows(rows)
    assert out[0] == ["Url", "Service Name", "", "8/12/2026", "8/13/2026"]
    assert out[1] == ["https://x.test/burlington", "Emergency Exam",
                       "2026-08-11T14:52:13", "3", "5"]


def test_reorder_pads_short_rows_instead_of_crashing():
    """The Sheets API trims a row to its own last non-blank cell, so a row
    can legitimately be shorter than the header."""
    rows = [["004-GentleDental-Burlington-MA", "Emergency/ Ortho"]]  # url/service/dates all blank
    out = sc._reorder_locations_rows(rows)
    assert out == [["", "", "", ]]


def test_reorder_drops_office_and_category_keeps_every_date_column():
    rows = [["office", "category", "url", "svc", "8/1/2026 0:00:00", "1", "2", "3", "4"]]
    out = sc._reorder_locations_rows(rows)
    assert out[0][:3] == ["url", "svc", "2026-08-01T00:00:00"]
    assert out[0][3:] == ["1", "2", "3", "4"]


# ── _rows_from_live_sheet ─────────────────────────────────────────────────────

def test_rows_from_live_sheet_reads_lps_verbatim_and_reorders_locations():
    lp_values = [["Account", "Location", "URL", "Comment", "Location Name"],
                 ["Gentle Dental", "burlington", "https://x.test/burlington", "Tabular View",
                  "004-GentleDental-Burlington-MA"]]
    loc_values = [["Location", "Services", "Url", "Service Name", "", "8/12/2026"],
                  ["004-GentleDental-Burlington-MA", "Emergency/ Ortho",
                   "https://x.test/burlington", "Emergency Exam", "8/11/2026 14:52:13", "3"]]

    calls = []

    def fake_get(spreadsheetId, range):
        calls.append(range)
        values = lp_values if range.startswith(sc.LP_TAB) else loc_values
        return mock.Mock(execute=mock.Mock(return_value={"values": values}))

    fake_svc = mock.Mock()
    fake_svc.spreadsheets.return_value.values.return_value.get.side_effect = fake_get

    with mock.patch.object(sc, "_sheets_service", return_value=fake_svc):
        lp_rows, slot_rows = sc._rows_from_live_sheet()

    assert lp_rows == lp_values
    assert slot_rows[1] == ["https://x.test/burlington", "Emergency Exam",
                             "2026-08-11T14:52:13", "3"]
    assert any(c.startswith("LPs!") for c in calls)
    assert any(c.startswith("Locations!") for c in calls)


def test_rows_from_live_sheet_raises_when_google_sa_json_is_unset(monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    with pytest.raises(RuntimeError):
        sc._rows_from_live_sheet()


# ── _snapshot: live-first, committed-fallback ────────────────────────────────

def test_snapshot_uses_live_data_when_the_read_succeeds():
    fake_live = {"generated_at": "live", "source": {}, "dates": [], "locations": [{"office": "1"}]}
    with mock.patch.object(sc, "_rows_from_live_sheet", return_value=([], [])), \
         mock.patch("scripts.import_slot_checker_snapshot.build_snapshot", return_value=fake_live), \
         mock.patch.object(sc, "load_snapshot") as fake_load:
        snap = sc._snapshot()
    assert snap == fake_live
    fake_load.assert_not_called()


def test_snapshot_falls_back_to_committed_file_when_the_live_read_fails():
    fallback = {"generated_at": "fallback", "source": {}, "dates": [], "locations": []}
    with mock.patch.object(sc, "_rows_from_live_sheet", side_effect=RuntimeError("no creds")), \
         mock.patch.object(sc, "load_snapshot", return_value=fallback) as fake_load:
        snap = sc._snapshot()
    assert snap == fallback
    fake_load.assert_called_once()


def test_snapshot_falls_back_when_build_snapshot_itself_raises():
    with mock.patch.object(sc, "_rows_from_live_sheet", return_value=([], [])), \
         mock.patch("scripts.import_slot_checker_snapshot.build_snapshot",
                     side_effect=ValueError("bad shape")), \
         mock.patch.object(sc, "load_snapshot", return_value={"locations": []}) as fake_load:
        sc._snapshot()
    fake_load.assert_called_once()


def test_snapshot_does_not_fall_back_when_live_legitimately_returns_zero_rows():
    """A live read that succeeds but genuinely finds nothing is a fact to
    show (the sheet was emptied out), not an error to paper over with stale
    fallback data."""
    empty_live = {"generated_at": "live", "source": {}, "dates": [], "locations": []}
    with mock.patch.object(sc, "_rows_from_live_sheet", return_value=([], [])), \
         mock.patch("scripts.import_slot_checker_snapshot.build_snapshot", return_value=empty_live), \
         mock.patch.object(sc, "load_snapshot") as fake_load:
        snap = sc._snapshot()
    assert snap == empty_live
    fake_load.assert_not_called()


# ── end-to-end over a real slice of the actual sheet's shape ─────────────────

def test_real_shaped_rows_produce_the_expected_dashboard_slice():
    """A trimmed but real slice of both tabs (same columns, same values, one
    practice with two runs) taken from the live sheet, run through the exact
    reorder + build_snapshot the app uses -- proves the join, the observation
    ordering and the date window all come out right end to end."""
    lp_rows = [
        ["Account", "Location", "URL", "Comment", "Location Name"],
        ["Gentle Dental", "Attleboro", "https://www.gentledental.com/ols?location_title=attleboro&state=ma",
         "Calendar View", "020-GentleDental-SouthAttleboro-MA"],
    ]
    loc_rows = [
        ["Location", "Services", "Url", "Service Name", "", "8/12/2026", "8/13/2026"],
        ["020-GentleDental-SouthAttleboro-MA", "Emergency/ Ortho",
         "https://www.gentledental.com/ols?location_title=attleboro&state=ma",
         "Emergency Exam", "8/11/2026 14:52:13", "0", "1"],
        # A later re-scrape of the same (office, service): this is the one
        # that must win.
        ["020-GentleDental-SouthAttleboro-MA", "Emergency/ Ortho",
         "https://www.gentledental.com/ols?location_title=attleboro&state=ma",
         "Emergency Exam", "8/13/2026 8:58:48", "0", "4"],
    ]

    from scripts.import_slot_checker_snapshot import build_snapshot
    snap = build_snapshot(lp_rows, sc._reorder_locations_rows(loc_rows), source="test")

    assert snap["dates"] == ["2026-08-12", "2026-08-13"]
    assert len(snap["locations"]) == 1
    loc = snap["locations"][0]
    assert loc["office"] == "020"
    assert loc["brand"] == "Gentle Dental"
    assert loc["state"] == "MA"
    assert len(loc["services"]) == 1
    svc = loc["services"][0]
    assert svc["name"] == "Emergency Exam"
    # Two observations kept, newest (the later re-scrape) last.
    assert [o["counts"] for o in svc["observations"]] == [[0, 1], [0, 4]]
