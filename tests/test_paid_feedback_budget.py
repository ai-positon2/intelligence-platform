"""Client Feedback Agent: the budget tool answers from the Pacing Desk's allocations and latest run, not from a Google Sheet."""
import pytest

from tracker import paid_feedback as fb
from tracker import paid_pacing_budget_store as bstore
from tracker import paid_refresh as rf
from tracker import paid_store as store


def book(kind="saved", frm=None, rows=None):
    rows = rows if rows is not None else [
        {"client": "Acme", "account": "Acme Dental", "allocated": 3000, "targetCpa": 45, "daysRunning": "Monday - Friday", "status": "Active", "am": "Sam"},
        {"client": "Beta", "account": "Beta Imaging", "allocated": 1500.5, "targetCpa": None, "daysRunning": "", "status": "", "am": ""}]
    return {"month": "2026-10", "rows": rows, "source": dict({"kind": kind}, **({"from": frm} if frm else {}))}


def run(as_of="2026-10-12", ambiguous=False):
    return {"asOf": as_of, "clients": [{"name": "Acme", "accounts": [{"name": "Acme Dental", "spend": 1463.2, "projected": 4123.4, "landing": "WILL OVERSPEND"}]},
                                       {"name": "Beta", "accounts": [{"name": "Beta Imaging", "spend": 500, "projected": 999, "landing": "BUDGET UNCLEAR", "ambiguous": ambiguous}]}]}


def test_the_answer_lists_each_allocation_with_where_the_latest_run_has_it():
    out = fb.budget_snapshot(book(), run())
    lines = out.split("\n")
    assert lines[0] == "Allocations for October 2026 (saved by the team). Spend and projection are from the Google Ads run of 2026-10-12."
    assert lines[1].startswith("Client | Account | Allocated | Target CPA")
    assert lines[2] == "Acme | Acme Dental | $3,000 | $45 | Monday - Friday | Active | $1,463 | $4,123 | will overspend"
    assert lines[3] == "Beta | Beta Imaging | $1,500 |  | every day |  | $500 | $999 | budget unclear"


def test_a_projection_the_engine_could_not_stand_behind_is_left_out():
    assert fb.budget_snapshot(book(), run(ambiguous=True)).split("\n")[3].endswith("| $500 |  | budget unclear")


def test_a_run_from_another_month_is_not_mixed_in():
    out = fb.budget_snapshot(book(), run(as_of="2026-09-30"))
    assert "Google Ads run" not in out and out.split("\n")[2].endswith("| Active |  |  | ")


def test_without_any_run_it_still_lists_the_allocations():
    out = fb.budget_snapshot(book(), None)
    assert "Google Ads run" not in out and "Acme Dental | $3,000" in out


@pytest.mark.parametrize("kind,frm,want", [("carried", "2026-09", "carried over from September 2026, not yet saved for October 2026"),
                                           ("seeded", "2026-09-30", "taken from the Pacing Desk run of 2026-09-30, not yet saved"),
                                           ("none", None, "none saved yet")])
def test_the_answer_says_when_the_rows_are_not_ones_the_team_saved(kind, frm, want):
    assert want in fb.budget_snapshot(book(kind, frm), None).split("\n")[0]


def test_a_client_filter_matches_the_client_or_the_account_ignoring_case():
    assert "Beta Imaging" not in fb.budget_snapshot(book(), None, "acme") and "Acme Dental" in fb.budget_snapshot(book(), None, "ACME")
    assert "Beta Imaging" in fb.budget_snapshot(book(), None, "imaging") and "Acme Dental" not in fb.budget_snapshot(book(), None, "imaging")


def test_a_filter_that_matches_nothing_and_an_empty_book_say_so():
    assert fb.budget_snapshot(book(), None, "zzz").endswith("No allocation matches 'zzz'.")
    assert fb.budget_snapshot(book("none", rows=[]), None).endswith("Add them in the Pacing Desk's Allocations.")


def test_the_tool_reads_the_platform_not_a_sheet(monkeypatch):
    seen = []

    class NoNetwork:
        def __call__(self, *a, **k):
            seen.append(a)
            raise AssertionError("the budget tool must not call Google")
    monkeypatch.setattr(bstore, "list_month", lambda m: [dict(r, updatedBy="a", updatedAt="x") for r in book()["rows"]])
    monkeypatch.setattr(bstore, "months", lambda: ["2026-10"])
    monkeypatch.setattr(store, "latest_date", lambda: "2026-10-12")
    monkeypatch.setattr(store, "get_snapshot", lambda d: run())
    monkeypatch.setattr(rf, "pacific_day", lambda when=None: __import__("datetime").date(2026, 10, 14))
    tools = fb.Tools(get=NoNetwork(), post=NoNetwork())
    out = tools.get_budget_snapshot({"client": "acme"})
    assert "Acme Dental | $3,000" in out and "Beta" not in out and seen == []


def test_a_down_store_for_the_run_still_answers_with_the_allocations(monkeypatch):
    monkeypatch.setattr(bstore, "list_month", lambda m: book()["rows"])
    monkeypatch.setattr(bstore, "months", lambda: [])
    monkeypatch.setattr(store, "latest_date", lambda: (_ for _ in ()).throw(store.StoreUnavailable("down")))
    out = fb.Tools(get=None, post=None).get_budget_snapshot({})
    assert "Acme Dental | $3,000" in out and "Google Ads run" not in out


def test_the_tool_and_its_step_line_no_longer_name_the_sheet():
    spec = next(t for t in fb.TOOL_SPECS if t["name"] == "get_budget_snapshot")
    assert "Budget Tracker" not in spec["description"] and "allocations" in spec["description"]
    assert fb.step_label("get_budget_snapshot", {"client": "OIA"}) == "Reading the budget allocations for OIA"
    assert not hasattr(fb, "BUDGET_TRACKER_ID")


def test_a_filter_that_matches_only_the_client_or_only_the_account_still_finds_the_row():
    rows = book()["rows"] + [{"client": "Gamma", "account": "Riverside", "allocated": 10, "targetCpa": None, "daysRunning": "", "status": "", "am": ""}]
    assert "Riverside" in fb.budget_snapshot(book(rows=rows), None, "gamma")
    assert "Riverside" in fb.budget_snapshot(book(rows=rows), None, "riverside")
