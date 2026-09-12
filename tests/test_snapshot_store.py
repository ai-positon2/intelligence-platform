"""Tests for tracker/snapshot_store.py, in particular the removal of raw_json
(save_snapshot() used to store the entire incoming payload, ~4.2KB average,
per company per run -- 90MB+ after three months at ~1,250 companies, closing
in on GitHub's 100MB per-file hard limit, since this repo commits its
databases). Only one field was ever read back out of it, so it now has its
own column instead and raw_json is no longer written.
"""

import json

import pytest

from tracker.snapshot_store import SnapshotStore


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path / "test.db")


def test_save_snapshot_does_not_populate_raw_json(store):
    store.upsert_company({"apollo_id": "a1", "name": "Acme", "domain": "acme.com",
                           "industry": "Widgets", "city": "Austin", "state": "TX"})
    store.save_snapshot("a1", {"employees": 200, "description": "Makes widgets."})

    snap = store.get_latest_snapshot("a1")
    assert snap["description"] == "Makes widgets."
    assert not snap.get("raw_json"), "raw_json should no longer be written"


def test_description_round_trips_through_save_and_get(store):
    store.upsert_company({"apollo_id": "a1", "name": "Acme", "domain": "acme.com",
                           "industry": "Widgets", "city": "Austin", "state": "TX"})
    store.save_snapshot("a1", {"description": "A short company description."})

    snap = store.get_latest_snapshot("a1")
    assert snap["description"] == "A short company description."


def test_missing_description_saves_as_null_not_a_crash(store):
    store.upsert_company({"apollo_id": "a1", "name": "Acme", "domain": "acme.com",
                           "industry": "Widgets", "city": "Austin", "state": "TX"})
    store.save_snapshot("a1", {"employees": 50})

    snap = store.get_latest_snapshot("a1")
    assert snap["description"] is None


def test_get_latest_snapshot_returns_the_most_recent_row(store):
    store.upsert_company({"apollo_id": "a1", "name": "Acme", "domain": "acme.com",
                           "industry": "Widgets", "city": "Austin", "state": "TX"})
    store.save_snapshot("a1", {"description": "First."})
    store.save_snapshot("a1", {"description": "Second."})

    snap = store.get_latest_snapshot("a1")
    assert snap["description"] == "Second."


def test_an_existing_database_with_the_old_schema_still_opens_and_gains_the_column(tmp_path):
    """A DB created before this change (raw_json present, no description
    column) must open cleanly under the new schema -- this is exactly the
    shape of every already-committed data/*.db file."""
    import sqlite3
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE companies (
            apollo_id TEXT PRIMARY KEY, name TEXT, domain TEXT, industry TEXT,
            city TEXT, state TEXT, first_seen TEXT, last_enriched TEXT,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, apollo_id TEXT, snapshot_date TEXT,
            employees INTEGER, raw_json TEXT
        );
        CREATE TABLE alerts_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT, apollo_id TEXT, signal_type TEXT,
            signal_detail TEXT, severity TEXT, sent_at TEXT
        );
        CREATE TABLE weekly_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_date TEXT, companies_checked INTEGER,
            signals_high INTEGER, signals_medium INTEGER, signals_low INTEGER, duration_seconds REAL
        );
    """)
    conn.execute(
        "INSERT INTO snapshots (apollo_id, snapshot_date, employees, raw_json) VALUES (?, ?, ?, ?)",
        ("a1", "2026-01-01T00:00:00+00:00", 100, json.dumps({"description": "Legacy description."})),
    )
    conn.commit()
    conn.close()

    store = SnapshotStore(db_path)  # must not raise on an old-shape DB
    snap = store.get_latest_snapshot("a1")
    assert snap["employees"] == 100
    assert snap["description"] is None, "not backfilled automatically -- that's a one-time migration"
    assert json.loads(snap["raw_json"])["description"] == "Legacy description."
