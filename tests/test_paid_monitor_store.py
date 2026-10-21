"""The monitors' shared store: the in-memory stand-in the monitor tests run on and the real Postgres store are held to one contract.

The Postgres half runs only when TEST_DATABASE_URL names a throwaway database (the tests create and empty the pm_mon_ tables in it); without it that half is skipped and says so.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402

PG = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.skipif(not PG, reason="TEST_DATABASE_URL is not set: the real store was not exercised"))])
def store(request, monkeypatch):
    if request.param == "memory":
        yield MemStore()
        return
    from tracker import paid_monitor_store as real
    monkeypatch.setenv("DATABASE_URL", PG)
    real._READY = False
    real.ensure_tables()
    with real._tx() as cur:
        cur.execute("TRUNCATE pm_mon_state, pm_mon_issues, pm_mon_snaps")
    yield real


def issue(fp="a:1", agent="brandspend", **kw):
    row = {"agent": agent, "fingerprint": fp, "accountId": "111", "accountName": "Acme <Dental>", "platform": "google", "kind": "over", "level": "medium", "title": "Brand spend is $301",
           "detail": "Up 48%.", "nextStep": "Start with the auction.", "data": {"week": "2026-10-12", "n": [1, 2]}, "status": "open", "firstSeen": "2026-10-20T09:00:00+00:00",
           "lastSeen": "2026-10-21T09:00:00+00:00", "resolvedAt": None, "reopened": 0, "alertedAt": None, "alertedLevel": None, "alert": {}}
    row.update(kw)
    return row


def test_a_named_value_is_kept_and_replaced_and_a_missing_one_is_the_default(store):
    assert store.state_get("alerts") is None and store.state_get("alerts", {"x": 1}) == {"x": 1}
    store.state_set("alerts", {"emails": ["a@b.co"], "slackChannel": "#ppc"})
    store.state_set("alerts", {"emails": [], "slackChannel": "#new"})
    assert store.state_get("alerts") == {"emails": [], "slackChannel": "#new"}


def test_a_lock_is_held_until_it_is_released(store):
    assert store.claim("lock:x", 1800) is True and store.claim("lock:x", 1800) is False
    store.release("lock:x")
    assert store.claim("lock:x", 1800) is True and store.claim("lock:y", 1800) is True


def test_issues_come_back_as_they_went_in_for_the_agent_that_saved_them(store):
    a, b = issue("a:1"), issue("b:2", agent="disapproved", title="Other")
    store.issues_put([a, b])
    got = store.issues_all("brandspend")
    assert len(got) == 1 and got[0]["fingerprint"] == "a:1" and got[0]["accountName"] == "Acme <Dental>" and got[0]["data"] == {"week": "2026-10-12", "n": [1, 2]}
    assert got[0]["firstSeen"].startswith("2026-10-20T09:00:00") and got[0]["resolvedAt"] is None and got[0]["alertedAt"] is None and got[0]["reopened"] == 0
    assert [x["fingerprint"] for x in store.issues_all("disapproved")] == ["b:2"] and store.issues_all("nobody") == []


def test_saving_an_issue_again_replaces_it_and_keeps_its_times(store):
    store.issues_put([issue("a:1")])
    store.issues_put([issue("a:1", level="high", status="resolved", resolvedAt="2026-10-22T09:00:00+00:00", reopened=2, alertedAt="2026-10-21T10:00:00+00:00", alertedLevel="medium",
                            alert={"email": "sent"}, title="Changed")])
    [g] = store.issues_all("brandspend")
    assert g["level"] == "high" and g["status"] == "resolved" and g["reopened"] == 2 and g["title"] == "Changed" and g["alert"] == {"email": "sent"} and g["alertedLevel"] == "medium"
    assert g["resolvedAt"].startswith("2026-10-22T09:00:00") and g["alertedAt"].startswith("2026-10-21T10:00:00") and g["firstSeen"].startswith("2026-10-20T09:00:00")


def test_issues_are_listed_most_recently_seen_first(store):
    store.issues_put([issue("old", lastSeen="2026-10-01T00:00:00+00:00"), issue("new", lastSeen="2026-10-21T00:00:00+00:00"), issue("mid", lastSeen="2026-10-10T00:00:00+00:00")])
    assert [g["fingerprint"] for g in store.issues_all("brandspend")] == ["new", "mid", "old"]


def test_daily_figures_are_kept_per_account_and_day_and_a_second_run_that_day_replaces_the_first(store):
    store.snaps_put("brandspend", "2026-10-20", {"111": {"status": "ok", "cost": 203}, "222": {"status": "over", "cost": 301}})
    store.snaps_put("brandspend", "2026-10-21", {"111": {"status": "over", "cost": 250}})
    store.snaps_put("brandspend", "2026-10-21", {"111": {"status": "over", "cost": 260}})
    store.snaps_put("disapproved", "2026-10-21", {"111": {"status": "stopped"}})
    assert store.snaps_get("brandspend", "111", "2026-10-01") == [{"status": "ok", "cost": 203, "day": "2026-10-20"}, {"status": "over", "cost": 260, "day": "2026-10-21"}]
    assert store.snaps_get("brandspend", "111", "2026-10-21") == [{"status": "over", "cost": 260, "day": "2026-10-21"}] and store.snaps_get("brandspend", "999", "2026-10-01") == []
    assert store.snaps_get("disapproved", "111", "2026-10-01")[0]["status"] == "stopped"


def test_old_days_are_forgotten_for_that_agent_only(store):
    store.snaps_put("brandspend", "2026-06-01", {"111": {"cost": 1}})
    store.snaps_put("brandspend", "2026-10-21", {"111": {"cost": 2}})
    store.snaps_put("disapproved", "2026-06-01", {"111": {"cost": 3}})
    store.snaps_prune("brandspend", "2026-10-01")
    assert [s["day"] for s in store.snaps_get("brandspend", "111", "2000-01-01")] == ["2026-10-21"] and len(store.snaps_get("disapproved", "111", "2000-01-01")) == 1


def test_nothing_to_save_is_fine(store):
    store.issues_put([])
    store.snaps_put("brandspend", "2026-10-21", {})
    assert store.issues_all("brandspend") == []


@pytest.mark.skipif(not PG, reason="TEST_DATABASE_URL is not set: the real store was not exercised")
def test_a_lock_older_than_its_time_to_live_can_be_taken_by_the_next_run(monkeypatch):
    from tracker import paid_monitor_store as real
    monkeypatch.setenv("DATABASE_URL", PG)
    real._READY = False
    real.ensure_tables()
    with real._tx() as cur:
        cur.execute("TRUNCATE pm_mon_state")
    assert real.claim("lock:z", 1800) is True and real.claim("lock:z", 1800) is False
    assert real.claim("lock:z", 0) is True                       # a holder older than the time to live (here none at all) died with its process
