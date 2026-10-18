"""Pacing Desk: the monthly budgets (tracker/paid_pacing_budgets.py)."""
import datetime as dt

import pytest

from tracker import paid_pacing_budgets as b


def row(**kw):
    base = {"client": "Acme", "account": "Acme Dental", "allocated": 3000}
    base.update(kw)
    return base


# ── Months ──────────────────────────────────────────────────────────────────

def test_months():
    assert b.month_of(dt.date(2026, 10, 12)) == "2026-10" and b.month_of("2026-09-30T10:00:00+00:00") == "2026-09"
    assert b.previous_month("2026-10") == "2026-09" and b.previous_month("2026-01") == "2025-12"
    assert b.check_month("2026-10") == "2026-10"
    for bad in ("2026-13", "2026-00", "2026-1", "10/2026", None, 202610):
        with pytest.raises(b.BadBudget):
            b.check_month(bad)


def test_two_rows_are_the_same_one_ignoring_case_and_spacing():
    assert b.row_key(" Acme ", "Acme   Dental") == b.row_key("acme", "ACME DENTAL")
    assert b.row_key("Acme", "A") != b.row_key("Acme", "B") and b.row_key("A", "X") != b.row_key("B", "X")


# ── One row ─────────────────────────────────────────────────────────────────

def test_a_row_is_checked_and_cleaned():
    got = b.check_row(row(allocated="$3,000.456", targetCpa="45", daysRunning="Monday - Friday", status="paused", am=" Sam  K "))
    assert got == {"client": "Acme", "account": "Acme Dental", "allocated": 3000.46, "targetCpa": 45.0, "daysRunning": "Monday - Friday",
                   "status": "Paused", "am": "Sam K", "channel": "Google"}
    plain = b.check_row(row())
    assert plain["targetCpa"] is None and plain["daysRunning"] == "" and plain["status"] == "" and plain["am"] == ""


@pytest.mark.parametrize("bad,why", [
    (row(client=""), "client is required"), (row(account="  "), "account is required"), ({"client": "A", "account": "B"}, "allocated budget is required"),
    (row(allocated="lots"), "should be a number"), (row(allocated=-1), "between 0"), (row(allocated=10_000_001), "between 0"),
    (row(allocated=True), "should be a number"), (row(targetCpa=0), "more than 0"), (row(targetCpa="x"), "should be a number"),
    (row(status="Live"), "Active or Paused"), (row(daysRunning="weekdays"), "days running"), (row(client="x" * 121), "longer than"),
    ("a row", "JSON object"), (None, "JSON object"),
])
def test_a_bad_row_says_what_to_fix(bad, why):
    with pytest.raises(b.BadBudget, match=why):
        b.check_row(bad)


def test_a_zero_allocation_is_allowed_for_an_account_that_is_off():
    assert b.check_row(row(allocated=0))["allocated"] == 0


@pytest.mark.parametrize("text,want", [
    ("", ""), ("All days", ""), ("daily", ""), ("Monday - Friday", "Monday - Friday"), ("Mon-Fri", "Mon-Fri"),
    ("Monday, Wednesday, Thursday", "Monday, Wednesday, Thursday"), ("Monday – Friday", "Monday – Friday"), (None, ""),
])
def test_days_running_in_the_forms_the_engine_reads(text, want):
    assert b.days_running(text) == want


@pytest.mark.parametrize("text", ["Monday - Friday, Sunday", "Monday, Tuesday - Thursday", "Monday - Wednesday - Friday"])
def test_a_range_mixed_with_other_days_is_refused_because_the_engine_would_drop_the_rest(text):
    with pytest.raises(b.BadBudget, match="not both|either a range"):
        b.days_running(text)


def test_rows_are_checked_together_and_a_repeat_names_both_halves():
    assert len(b.check_rows([row(), row(account="B")])) == 2
    with pytest.raises(b.BadBudget, match="listed twice"):
        b.check_rows([row(), row(account="acme  dental")])
    with pytest.raises(b.BadBudget, match="Acme Dental: allocated budget is required"):
        b.check_rows([row(), {"client": "Acme", "account": "Acme Dental"}])
    with pytest.raises(b.BadBudget, match="more than"):
        b.check_rows([row(account=str(i)) for i in range(601)])
    with pytest.raises(b.BadBudget):
        b.check_rows("rows")


# ── Pasting from the Budget Tracker ─────────────────────────────────────────

TRACKER = "\t".join(b.DPR_HEADER) + "\n" + "\n".join("\t".join(r) for r in [
    ["Acme", "Acme Dental", "$3,000", "45", "10/1/2026", "Active", "Monday - Friday", "Sam", "Google"],
    ["Acme", "Acme Dental", "$900", "", "10/1/2026", "Active", "", "Sam", "Bing"],
    ["Beta", "Beta Imaging", "1,500.50", "10", "10/1/2026", "Paused", "", "", "Google"],
    ["Beta", "Beta Imaging", "9999", "10", "9/1/2026", "Active", "", "", "Google"],
    ["Gamma", "Gamma", "abc", "", "10/1/2026", "Active", "", "", "Google"],
    ["", "", "", "", "", "", "", "", ""],
])


def test_a_pasted_tracker_gives_the_months_google_rows():
    got = b.parse_paste(TRACKER, "2026-10")
    assert [(r["client"], r["account"], r["allocated"], r["targetCpa"], r["status"]) for r in got["rows"]] == [
        ("Acme", "Acme Dental", 3000.0, 45.0, "Active"), ("Beta", "Beta Imaging", 1500.5, 10.0, "Paused")]
    assert got["rows"][0]["daysRunning"] == "Monday - Friday" and got["rows"][0]["am"] == "Sam"
    assert got["ignored"] == 1                                            # September's Beta row
    reasons = {s["row"]: s["reason"] for s in got["skipped"]}
    assert "Bing budget, not Google Ads" in reasons["Acme Dental"] and "should be a number" in reasons["Gamma"]


def test_another_month_is_picked_by_the_date_column():
    got = b.parse_paste(TRACKER, "2026-09")
    assert [(r["account"], r["allocated"]) for r in got["rows"]] == [("Beta Imaging", 9999.0)]


def test_a_paste_without_a_header_row_uses_the_trackers_own_column_order():
    got = b.parse_paste("Acme\tAcme Dental\t3000\t45\t2026-10-01\tActive\tMonday - Friday\tSam\tGoogle", "2026-10")
    assert got["rows"][0]["allocated"] == 3000 and got["rows"][0]["daysRunning"] == "Monday - Friday"


def test_a_short_paste_with_commas_and_only_the_columns_it_has():
    got = b.parse_paste("Client,Account,Allocated Budget\nAcme,Acme Dental,3000\nBeta,Beta,\"1,200\"", "2026-10")
    assert [(r["account"], r["allocated"]) for r in got["rows"]] == [("Acme Dental", 3000.0), ("Beta", 1200.0)]


def test_a_row_repeated_in_a_paste_keeps_the_later_one_and_says_so():
    got = b.parse_paste("Client\tAccount\tAllocated Budget\nAcme\tA\t100\nAcme\ta\t200", "2026-10")
    assert [r["allocated"] for r in got["rows"]] == [200.0] and any("repeated" in s["reason"] for s in got["skipped"])


@pytest.mark.parametrize("text,why", [
    ("", "nothing was pasted"), ("   \n ", "nothing was pasted"), ("x" * 400_001, "more text"),
    ("Client\tAccount\tNotes\nA\tB\tC", "no allocated budget column"),
    ("Client\tAccount\tAllocated Budget\tDate\nA\tB\t5\t9/1/2026", "other months"),
    ("Client\tAccount\tAllocated Budget\nA\tB\tabc", "no budget rows"),
])
def test_a_paste_with_nothing_usable_says_why(text, why):
    with pytest.raises(b.BadBudget, match=why):
        b.parse_paste(text, "2026-10")


@pytest.mark.parametrize("cell,want", [
    ("2026-09-01", "2026-09"), ("2026-9-1", "2026-09"), ("2026-09-01 00:00:00", "2026-09"), ("9/1/2026", "2026-09"), ("09/01/26", "2026-09"),
    ("September 2026", "2026-09"), ("Sep-2026", "2026-09"), ("Sept 1, 2026", "2026-09"), ("Oct-26", "2026-10"), ("", None), ("soon", None),
    ("13/1/2026", None), ("2026-13-01", None),
])
def test_the_month_of_a_date_cell(cell, want):
    assert b.cell_month(cell) == want


# ── Seeding and the engine's layout ─────────────────────────────────────────

def test_the_first_month_is_seeded_from_the_last_stored_run():
    clients = [{"name": "Acme", "accounts": [{"name": "Acme Dental", "allocated": 3000, "schedule": "Monday - Friday", "targetCpl": 45},
                                              {"name": "Off", "allocated": 0}, {"name": "", "allocated": 5}, {"name": "Odd", "allocated": 700, "schedule": "weekdays"}]},
               {"id": "beta", "data": {"name": "Beta", "accounts": [{"name": "Beta Imaging", "allocated": 1500.5, "schedule": "All days", "targetCpa": 10}]}}]
    got = b.seed_from_run(clients)
    assert [(r["client"], r["account"], r["allocated"], r["targetCpa"], r["daysRunning"]) for r in got] == [
        ("Acme", "Acme Dental", 3000.0, 45.0, "Monday - Friday"), ("Acme", "Odd", 700.0, None, ""), ("Beta", "Beta Imaging", 1500.5, 10.0, "")]
    assert b.seed_from_run(None) == [] and b.seed_from_run([{"name": "A", "accounts": "x"}]) == []
    assert b.seed_from_run(["x", {"name": "A", "accounts": ["y", {"name": "B", "allocated": "5"}]}, {"name": "A"}]) == []


def test_the_rows_are_laid_out_like_the_dpr_tab_the_engine_reads():
    out = b.dpr_rows(b.check_rows([row(targetCpa=45, daysRunning="Monday - Friday", am="Sam", status="Paused"), row(account="B")]), "2026-10")
    assert out[0] == b.DPR_HEADER
    assert out[1] == ["Acme", "Acme Dental", 3000.0, 45.0, dt.datetime(2026, 10, 1), "Paused", "Monday - Friday", "Sam", "Google"]
    assert out[2] == ["Acme", "B", 3000.0, None, dt.datetime(2026, 10, 1), "Active", "All days", "", "Google"]


# ── The edges of what is accepted ───────────────────────────────────────────

def test_the_limits_themselves_are_allowed():
    assert b.check_row(row(client="x" * 120, account="y" * 200, am="z" * 80))["client"] == "x" * 120
    assert b.check_row(row(allocated=b.MAX_ALLOCATED))["allocated"] == b.MAX_ALLOCATED
    assert b.check_row(row(targetCpa=b.MAX_TARGET))["targetCpa"] == b.MAX_TARGET
    assert b.check_row(row(targetCpa=0.01))["targetCpa"] == 0.01
    with pytest.raises(b.BadBudget):
        b.check_row(row(targetCpa=b.MAX_TARGET + 1))
    assert len(b.check_rows([row(account=str(i)) for i in range(600)])) == 600
    head = "Client\tAccount\tAllocated Budget\nA\tB\t5\n"
    pad = " \n" * ((400_000 - len(head) - 2) // 2) + "  "
    assert len(head + pad) == 400_000
    assert b.parse_paste(head + pad, "2026-10")["rows"][0]["allocated"] == 5
    with pytest.raises(b.BadBudget, match="more text"):
        b.parse_paste(head + pad + " ", "2026-10")


def test_one_enormous_cell_is_refused_not_a_crash():
    with pytest.raises(b.BadBudget, match="could not be read as rows"):
        b.parse_paste("Client\tAccount\tAllocated Budget\nA\t" + "x" * 200_000 + "\t5", "2026-10")


def test_a_long_list_of_unusable_rows_is_listed_in_part_and_counted():
    text = "Client\tAccount\tAllocated Budget\nOk\tOk\t5\n" + "\n".join("C\tA%d\tnope" % i for i in range(100))
    got = b.parse_paste(text, "2026-10")
    assert len(got["skipped"]) == b.MAX_SKIPPED + 1 and "and 60 more" in got["skipped"][-1]["reason"]


def test_january_and_december_dates_are_dates():
    assert b.cell_month("12/1/2026") == "2026-12" and b.cell_month("1/1/2026") == "2026-01"
    assert b.cell_month("2026-12-01") == "2026-12" and b.cell_month("2026-01-01") == "2026-01"
    assert b.cell_month("0/1/2026") is None and b.cell_month("2026-00-01") is None
    assert b.cell_month("Dec 2026") == "2026-12" and b.cell_month("Jan-27") == "2027-01"


def test_a_pasted_row_missing_its_client_or_its_account_is_reported_not_dropped_quietly():
    got = b.parse_paste("Client\tAccount\tAllocated Budget\nAcme\tA\t5\n\tNoClient\t6\nNoAccount\t\t7", "2026-10")
    reasons = {s["row"]: s["reason"] for s in got["skipped"]}
    assert [r["account"] for r in got["rows"]] == ["A"]
    assert "client is required" in reasons["NoClient"] and "account is required" in reasons["row 3"]


def test_a_header_with_only_one_of_client_and_account_is_refused():
    with pytest.raises(b.BadBudget, match="both a Client and an Account"):
        b.parse_paste("Client\tAllocated Budget\nAcme\t5", "2026-10")


def test_a_stored_runs_allocation_must_be_a_real_positive_number():
    clients = [{"name": "A", "accounts": [{"name": "B", "allocated": "5"}, {"name": "C", "allocated": True}, {"name": "D", "allocated": -4}]}]
    assert b.seed_from_run(clients) == []


# ── Which rows a month uses ─────────────────────────────────────────────────

class MemStore:
    def __init__(self, **by_month):
        self.by = by_month

    def list_month(self, m):
        return [dict(r, updatedBy="ana", updatedAt="2026-09-01T00:00:00+00:00") for r in self.by.get(m, [])]

    def months(self):
        return sorted(m for m, rows in self.by.items() if rows)


def test_a_month_with_saved_rows_uses_them():
    book = b.book_for("2026-10", store=MemStore(**{"2026-10": [b.check_row(row())], "2026-09": [b.check_row(row(allocated=1))]}))
    assert book["source"] == {"kind": "saved"} and book["rows"][0]["allocated"] == 3000 and book["rows"][0]["updatedBy"] == "ana"


def test_a_new_month_carries_the_latest_earlier_months_rows_without_who_saved_them():
    store = MemStore(**{"2026-07": [b.check_row(row(allocated=1))], "2026-09": [b.check_row(row(allocated=2))], "2026-12": [b.check_row(row(allocated=3))]})
    book = b.book_for("2026-10", store=store)
    assert book["source"] == {"kind": "carried", "from": "2026-09"} and [r["allocated"] for r in book["rows"]] == [2]
    assert set(book["rows"][0]) == set(b.FIELDS)                  # nothing says a person saved it
    assert b.book_for("2026-06", store=store)["source"] == {"kind": "none"}            # nothing earlier than June: nothing to carry


def test_the_first_month_is_seeded_from_the_last_run():
    run = lambda: ("2026-09-30", [{"name": "Acme", "accounts": [{"name": "Acme Dental", "allocated": 3000}]}])
    book = b.book_for("2026-10", store=MemStore(), run_fn=run)
    assert book["source"] == {"kind": "seeded", "from": "2026-09-30"} and book["rows"][0]["account"] == "Acme Dental"


def test_nothing_anywhere_is_an_empty_book():
    assert b.book_for("2026-10", store=MemStore()) == {"month": "2026-10", "rows": [], "source": {"kind": "none"}}
    assert b.book_for("2026-10", store=MemStore(), run_fn=lambda: (None, []))["source"] == {"kind": "none"}
    with pytest.raises(b.BadBudget):
        b.book_for("October", store=MemStore())


def test_the_saved_version_is_the_newest_save_time():
    assert b.version_of([]) == "" and b.version_of(None) == ""
    assert b.version_of([{"updatedAt": "2026-10-01T10:00:00+00:00"}, {"updatedAt": "2026-10-02T09:00:00+00:00"}, {}]) == "2026-10-02T09:00:00+00:00"


# ── Channels: Google (the default) and Meta ─────────────────────────────────

@pytest.mark.parametrize("text,want", [("", "Google"), (None, "Google"), ("Google", "Google"), ("Google Ads", "Google"), ("google ads", "Google"), ("Meta", "Meta"),
                                       ("Meta Ads", "Meta"), ("Facebook", "Meta"), ("Instagram", "Meta"), ("FB", "Meta"), ("Bing", None), ("LinkedIn", None)])
def test_a_channel_cell_is_google_or_meta_or_something_else(text, want):
    assert b.channel_of(text) == want


def test_a_google_row_keeps_the_key_it_always_had_and_a_meta_row_has_its_own():
    assert b.row_key("Acme", "Acme Dental") == "acme|acme dental" == b.row_key("Acme", "Acme Dental", "Google")
    assert b.row_key("Acme", "Acme Dental", "Meta") == "acme|acme dental|meta"


def test_a_row_has_a_channel_google_unless_it_says_meta_and_any_other_is_refused():
    assert b.check_row(row())["channel"] == "Google" and b.check_row(row(channel="Meta Ads"))["channel"] == "Meta" and b.check_row(row(channel="facebook"))["channel"] == "Meta"
    with pytest.raises(b.BadBudget, match="channel should be Google or Meta"):
        b.check_row(row(channel="Bing"))


def test_the_same_account_may_have_a_google_row_and_a_meta_row_but_not_two_of_one():
    both = b.check_rows([row(), row(channel="Meta")])
    assert [r["channel"] for r in both] == ["Google", "Meta"]
    with pytest.raises(b.BadBudget, match="listed twice for Meta"):
        b.check_rows([row(channel="Meta"), row(account="acme  dental", channel="Facebook")])
    with pytest.raises(b.BadBudget, match="listed twice"):
        b.check_rows([row(), row()])


def test_the_engines_sheet_says_each_rows_channel():
    out = b.dpr_rows(b.check_rows([row(), row(channel="Meta", allocated=900)]), "2026-10")
    assert out[0][-1] == "Channels" and [r[-1] for r in out[1:]] == ["Google", "Meta"]


def test_a_pasted_meta_row_is_kept_and_another_channel_is_skipped_and_said_so():
    text = "Client\tAccount\tAllocated Budget\tDate\tChannels\nAcme\tAcme Dental\t3000\t10/1/2026\tGoogle\nAcme\tAcme Dental\t900\t10/1/2026\tMeta\nAcme\tAcme Dental\t500\t10/1/2026\tBing"
    got = b.parse_paste(text, "2026-10")
    assert [(r["channel"], r["allocated"]) for r in got["rows"]] == [("Google", 3000.0), ("Meta", 900.0)]
    assert got["skipped"] == [{"row": "Acme Dental", "reason": "Bing budget, not Google Ads or Meta Ads"}]


def test_a_run_that_paced_a_meta_account_seeds_it_as_a_meta_row():
    clients = [{"name": "Acme", "accounts": [{"name": "Acme Dental", "allocated": 3000}, {"name": "Acme Dental", "allocated": 900, "channel": "Meta"}]}]
    assert [(r["account"], r["channel"]) for r in b.seed_from_run(clients)] == [("Acme Dental", "Google"), ("Acme Dental", "Meta")]
