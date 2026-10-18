"""Pacing Desk: the saved monthly budgets (tracker/paid_pacing_budget_store.py), run against a real Postgres.

Uses the throwaway cluster the Account Audit store tests start (initdb and pg_ctl from the machine). Where those programs are not installed
these tests are SKIPPED, loudly: the SQL has then not been run by anything.
"""
import pytest

from tests.test_paid_audit_store import pg as _cluster
from tracker import paid_pacing_budgets as budgets
from tracker import paid_pacing_budget_store as store


@pytest.fixture(scope="module")
def pg(_cluster):
    store._READY = False
    yield _cluster
    store._READY = False


def r(account="Acme Dental", client="Acme", allocated=3000, **kw):
    return budgets.check_row(dict({"client": client, "account": account, "allocated": allocated}, **kw))


def test_a_months_rows_are_saved_and_read_back(pg):
    got = store.replace_month("2026-10", [r(targetCpa=45, daysRunning="Monday - Friday", am="Sam", status="Paused"), r("B", "Beta", 1500.5)], "ana@position2.com")
    assert [(x["client"], x["account"], x["allocated"]) for x in got] == [("Acme", "Acme Dental", 3000.0), ("Beta", "B", 1500.5)]
    first = got[0]
    assert (first["targetCpa"], first["daysRunning"], first["status"], first["am"], first["updatedBy"]) == (45.0, "Monday - Friday", "Paused", "Sam", "ana@position2.com")
    assert "T" in first["updatedAt"] and got[1]["targetCpa"] is None and got[1]["daysRunning"] == ""
    assert store.list_month("2026-10") == got and store.list_month("2026-09") == []


def test_replacing_a_month_replaces_all_of_it_and_only_it(pg):
    store.replace_month("2026-11", [r("A"), r("B")], "ana@position2.com")
    store.replace_month("2026-12", [r("Z")], "ana@position2.com")
    store.replace_month("2026-11", [r("C")], "ben@position2.com")
    assert [x["account"] for x in store.list_month("2026-11")] == ["C"] and [x["account"] for x in store.list_month("2026-12")] == ["Z"]
    assert store.replace_month("2026-11", [], "ben@position2.com") == []


def test_the_months_that_have_rows(pg):
    store.replace_month("2025-03", [r("M")], "ana@position2.com")
    ms = store.months()
    assert ms == sorted(ms) and "2025-03" in ms and "2026-10" in ms


def test_a_row_is_added_then_changed_in_place_by_client_and_account(pg):
    store.replace_month("2027-01", [], "ana@position2.com")
    one = store.save_row("2027-01", r(allocated=1000), "ana@position2.com")
    assert one["allocated"] == 1000.0
    two = store.save_row("2027-01", r(account="ACME  dental", allocated=1200, am="Sam"), "ben@position2.com")     # same row, written differently
    assert two["allocated"] == 1200.0 and two["account"] == "ACME dental" and two["updatedBy"] == "ben@position2.com"
    assert len(store.list_month("2027-01")) == 1


def test_renaming_a_row_does_not_leave_the_old_one_behind(pg):
    store.replace_month("2027-02", [r("Old Name")], "ana@position2.com")
    store.save_row("2027-02", r("New Name"), "ana@position2.com", replaces=("Acme", "Old Name"))
    assert [x["account"] for x in store.list_month("2027-02")] == ["New Name"]


def test_a_row_is_removed_and_only_that_row(pg):
    store.replace_month("2027-03", [r("A"), r("B")], "ana@position2.com")
    assert store.delete_row("2027-03", "acme", "a") is True and store.delete_row("2027-03", "Acme", "A") is False
    assert [x["account"] for x in store.list_month("2027-03")] == ["B"]


def test_the_same_account_in_two_months_is_two_rows(pg):
    store.replace_month("2027-04", [r("Same", allocated=100)], "ana@position2.com")
    store.replace_month("2027-05", [r("Same", allocated=200)], "ana@position2.com")
    assert store.list_month("2027-04")[0]["allocated"] == 100.0 and store.list_month("2027-05")[0]["allocated"] == 200.0


def test_the_same_account_under_two_clients_is_two_rows(pg):
    store.replace_month("2027-06", [r("Shared", "One"), r("Shared", "Two")], "ana@position2.com")
    assert [x["client"] for x in store.list_month("2027-06")] == ["One", "Two"]


def test_a_google_row_and_a_meta_row_for_the_same_account_are_two_rows(pg):
    store.replace_month("2027-01", [r(), r(channel="Meta", allocated=900)], "ana@position2.com")
    got = store.list_month("2027-01")
    assert sorted((x["channel"], x["allocated"]) for x in got) == [("Google", 3000.0), ("Meta", 900.0)]
    store.save_row("2027-01", r(channel="Meta", allocated=1200), "ben@position2.com")                # changes the Meta row, not the Google one
    assert sorted((x["channel"], x["allocated"]) for x in store.list_month("2027-01")) == [("Google", 3000.0), ("Meta", 1200.0)]
    assert store.delete_row("2027-01", "Acme", "Acme Dental", "Meta") is True
    assert [(x["channel"], x["allocated"]) for x in store.list_month("2027-01")] == [("Google", 3000.0)]


def test_a_table_made_before_there_were_channels_gets_the_column_and_its_rows_are_google(pg):
    from tracker import paid_store
    conn = paid_store._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE pm_pacing_budgets")
            cur.execute("CREATE TABLE pm_pacing_budgets (month TEXT NOT NULL, ckey TEXT NOT NULL, client TEXT NOT NULL, account TEXT NOT NULL, allocated NUMERIC NOT NULL, "
                        "target_cpa NUMERIC, days_running TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', am TEXT NOT NULL DEFAULT '', updated_by TEXT, "
                        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (month, ckey))")
            cur.execute("INSERT INTO pm_pacing_budgets (month, ckey, client, account, allocated) VALUES ('2026-09', 'old|row', 'Old', 'Row', 700)")
        conn.commit()
    finally:
        conn.close()
    store._READY = False
    got = store.list_month("2026-09")
    assert [(x["client"], x["account"], x["channel"]) for x in got] == [("Old", "Row", "Google")]
    store.replace_month("2026-09", [r(channel="Meta")], "ana@position2.com")                          # and it takes a Meta row after the change
    assert [x["channel"] for x in store.list_month("2026-09")] == ["Meta"]
