"""tracker/job_change_store.py: dedup on apollo_contact_id is the whole point --
without it, re-running the sync script (daily cron, or a manual "Sync now")
would re-insert every event it has ever seen on every run."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.job_change_store import JobChangeStore  # noqa: E402

_EVENT = {
    "apollo_contact_id": "contact_1",
    "person_name": "Jane Doe",
    "linkedin_url": "http://www.linkedin.com/in/janedoe",
    "new_title": "VP of Engineering",
    "new_company_name": "Acme Health",
    "apollo_account_id": "account_1",
    "company_industry": "hospital & health care",
    "company_description": "Acme Health builds things.",
    "city": "Austin",
    "employees": "500",
    "revenue": "$50M",
    "job_start_date": "Jun 01, 2026",
    "detected_at": "2026-06-01T00:00:00+00:00",
    "slack_message_ts": "1786771530.195749",
    "slack_permalink": "https://x/p1",
}


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as d:
        yield JobChangeStore(os.path.join(d, "job_change_alerts.db"))


def test_a_new_event_is_inserted(store):
    assert store.upsert_event(_EVENT) is True
    events = store.get_all_events()
    assert len(events) == 1
    assert events[0]["person_name"] == "Jane Doe"


def test_re_inserting_the_same_contact_id_is_a_noop(store):
    store.upsert_event(_EVENT)
    changed = store.upsert_event({**_EVENT, "new_title": "A different title, same person"})
    assert changed is False
    events = store.get_all_events()
    assert len(events) == 1
    assert events[0]["new_title"] == "VP of Engineering"  # first write wins, not silently overwritten


def test_events_with_no_contact_id_are_always_inserted(store):
    """No reliable dedup key exists for a malformed Apollo message -- better to
    keep a possible duplicate than silently drop a real job change."""
    no_id_event = {**_EVENT, "apollo_contact_id": None}
    assert store.upsert_event(no_id_event) is True
    assert store.upsert_event(no_id_event) is True
    assert len(store.get_all_events()) == 2


def test_get_latest_detected_at_reflects_the_newest_row(store):
    store.upsert_event({**_EVENT, "apollo_contact_id": "c1", "detected_at": "2026-01-01T00:00:00+00:00"})
    store.upsert_event({**_EVENT, "apollo_contact_id": "c2", "detected_at": "2026-06-01T00:00:00+00:00"})
    assert store.get_latest_detected_at() == "2026-06-01T00:00:00+00:00"


def test_count_matches_number_of_stored_events(store):
    assert store.count() == 0
    store.upsert_event({**_EVENT, "apollo_contact_id": "c1"})
    store.upsert_event({**_EVENT, "apollo_contact_id": "c2"})
    assert store.count() == 2


def test_get_all_events_orders_newest_first(store):
    store.upsert_event({**_EVENT, "apollo_contact_id": "old", "detected_at": "2026-01-01T00:00:00+00:00"})
    store.upsert_event({**_EVENT, "apollo_contact_id": "new", "detected_at": "2026-06-01T00:00:00+00:00"})
    events = store.get_all_events()
    assert [e["apollo_contact_id"] for e in events] == ["new", "old"]
