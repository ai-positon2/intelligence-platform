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
