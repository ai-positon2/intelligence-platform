"""/p2/b2b-agents/job-change-alert (page + /data + /sync). The page itself is
@position2_required (any Position2 staff); /sync is admin_required since it
shells out to an external API and writes to the db, matching every other
side-effecting admin action in this app."""

import json
import os
import sys
import tempfile

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
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
def seeded_db(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "job_change_alerts.db")
        store = JobChangeStore(db_path)
        store.upsert_event(_EVENT)
        monkeypatch.setattr(appmod, "JOB_CHANGE_DB_PATH", db_path)
        yield store


def _client(email="reporting@position2.com"):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


def test_page_renders_for_any_position2_staff(seeded_db):
    resp = _client("someone@position2.com").get("/p2/b2b-agents/job-change-alert")
    assert resp.status_code == 200
    assert b"Job Change Alert" in resp.data


def test_data_endpoint_returns_the_seeded_event(seeded_db):
    resp = _client().get("/p2/b2b-agents/job-change-alert/data")
    body = resp.get_json()
    assert body["total"] == 1
    assert body["events"][0]["person_name"] == "Jane Doe"
    assert body["last_synced"] == "2026-06-01T00:00:00+00:00"


def test_data_endpoint_never_leaks_the_literal_unavailable_string(seeded_db, monkeypatch):
    """The parser already normalizes '[Unavailable]' to None -- this pins that
    the route doesn't re-introduce it (e.g. via a default-value fallback)."""
    resp = _client().get("/p2/b2b-agents/job-change-alert/data")
    body = json.dumps(resp.get_json())
    assert "[Unavailable]" not in body


def test_sync_route_is_forbidden_for_non_admin_position2_staff(seeded_db):
    resp = _client("someone@position2.com").post("/p2/b2b-agents/job-change-alert/sync")
    assert resp.status_code == 403


def test_sync_route_requires_login():
    c = appmod.app.test_client()
    resp = c.post("/p2/b2b-agents/job-change-alert/sync", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_sync_route_degrades_gracefully_without_a_slack_token(seeded_db, monkeypatch):
    """The realistic first-run condition: SLACK_BOT_TOKEN isn't scoped for
    channel history yet. The sync script must still exit cleanly (added=0),
    not 500 the request."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    resp = _client().post("/p2/b2b-agents/job-change-alert/sync")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["added"] == 0


def test_sync_route_reports_failure_if_the_script_itself_errors(seeded_db, monkeypatch):
    import subprocess as _subprocess

    class _FailedProc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _FailedProc())
    resp = _client().post("/p2/b2b-agents/job-change-alert/sync")
    assert resp.status_code == 500
    assert resp.get_json()["ok"] is False
