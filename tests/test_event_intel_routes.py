"""Event & Conference Intelligence: routing, gating, and the export.

Companion to test_event_intel_honesty.py, which covers what the agent is
allowed to claim. This file covers who can reach it and what leaves it.
"""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

import app as appmod  # noqa: E402
from tracker import event_intel_store as store  # noqa: E402

BASE = "/p2/b2b-agents/event-conference-intelligence"

ROUTES = [
    (BASE, "GET"),
    (BASE + "/run", "POST"),
    (BASE + "/runs/1/status", "GET"),
    (BASE + "/runs/1", "GET"),
    (BASE + "/runs/1/resolve", "POST"),
    (BASE + "/runs/1/export.csv", "GET"),
]


def _client(email=None):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.mark.parametrize("path,method", ROUTES)
def test_every_route_is_registered(path, method):
    """A route that silently failed to register would 404 in production while
    every unit test below still passed against the functions directly."""
    adapter = appmod.app.url_map.bind("localhost")
    assert adapter.test(path, method), "%s %s is not registered" % (method, path)


@pytest.mark.parametrize("path,method", ROUTES)
def test_no_route_is_reachable_logged_out(path, method):
    r = _client().open(path, method=method)
    assert r.status_code in (301, 302, 401, 403), (
        "%s %s answered %s to an anonymous request" % (method, path, r.status_code))


@pytest.mark.parametrize("path,method", ROUTES)
def test_no_route_is_reachable_by_a_non_position2_account(path, method):
    """Google SSO is open to any Google account, so a signed-in outsider is
    the realistic threat here, not an anonymous one."""
    r = _client("someone@gmail.com").open(path, method=method)
    assert r.status_code in (301, 302, 401, 403), (
        "%s %s answered %s to a non-Position2 signed-in user"
        % (method, path, r.status_code))


def test_the_admin_selftest_is_admin_only():
    path = "/p2/admin/external-usage/evi-resolve-check"
    assert appmod.app.url_map.bind("localhost").test(path, "POST")
    # A Position2 staffer who is not in ADMIN_EMAILS must not reach it.
    r = _client("not.an.admin@position2.com").post(path)
    assert r.status_code in (301, 302, 401, 403), r.status_code


def test_run_rejects_an_unknown_mode_and_an_empty_query(monkeypatch):
    monkeypatch.setattr(store, "save_run", lambda *a, **k: 1)
    c = _client("reporting@position2.com")
    assert c.post(BASE + "/run", json={"mode": "sideways", "query": "x"}).status_code == 400
    assert c.post(BASE + "/run", json={"mode": "lookup", "query": "  "}).status_code == 400


def test_run_reports_storage_being_unavailable_rather_than_pretending(monkeypatch):
    """save_run returns None when DATABASE_URL is unset. Answering 200 with a
    null run_id would leave the page polling forever for a run that was never
    created."""
    monkeypatch.setattr(store, "save_run", lambda *a, **k: None)
    r = _client("reporting@position2.com").post(
        BASE + "/run", json={"mode": "lookup", "query": "Web Summit"})
    assert r.status_code == 500
    assert "storage" in r.get_json()["error"].lower()


def test_a_run_belonging_to_someone_else_is_a_404(monkeypatch):
    """get_run scopes ownership in the SQL, so a foreign run reads as absent.
    This asserts the route actually honours that instead of 500ing on None,
    which is the shape an IDOR takes when it is fixed carelessly."""
    monkeypatch.setattr(store, "get_run", lambda run_id, email: None)
    c = _client("reporting@position2.com")
    assert c.get(BASE + "/runs/99").status_code == 404
    assert c.get(BASE + "/runs/99/status").status_code == 404
    assert c.get(BASE + "/runs/99/export.csv").status_code == 404


def test_run_detail_always_carries_the_source_ledger(monkeypatch):
    """The list of pages that could NOT be read is what stops a short roster
    reading as a complete one, so it must not be optional or conditional."""
    monkeypatch.setattr(store, "get_run", lambda run_id, email: {
        "id": run_id, "mode": "lookup", "query": "q", "status": "complete",
        "stage": "done", "error": None, "summary": {}, "credits_spent": 0})
    monkeypatch.setattr(store, "get_events", lambda run_id: [])
    monkeypatch.setattr(store, "get_participants", lambda run_id, role=None: [])
    called = {}

    def _sources(run_id):
        called["yes"] = True
        return []
    monkeypatch.setattr(store, "get_sources", _sources)

    body = _client("reporting@position2.com").get(BASE + "/runs/5").get_json()
    assert called.get("yes"), "the run detail route never read the source ledger"
    assert "sources" in body
    assert body["role_labels"]["exhibitor"] == "Exhibitor"


def test_the_csv_says_what_the_screen_says(monkeypatch):
    """Audit round 5's finding was an export that did not match its screen.
    Every row here carries the same role wording and the source URL, so the
    file cannot be mistaken for an attendee list once detached from the page
    that explains it."""
    monkeypatch.setattr(store, "get_run", lambda run_id, email: {
        "id": run_id, "mode": "lookup", "query": "Widget Expo", "status": "complete",
        "stage": "done", "error": None, "summary": {}, "credits_spent": 1})
    monkeypatch.setattr(store, "get_events", lambda run_id: [{"name": "Widget Expo"}])
    monkeypatch.setattr(store, "get_sources", lambda run_id: [])
    monkeypatch.setattr(store, "get_participants", lambda run_id, role=None: [
        {"org_name": "Acme Robotics", "org_domain": "acme.test", "role": "exhibitor",
         "person_name": None, "person_title": None, "tier": None, "booth": "214",
         "apollo": {"name": "Acme", "industry": "robotics", "employees": 400},
         "source_url": "https://widgetexpo.test/exhibitors"},
        {"org_name": "Vandelay", "org_domain": None, "role": "attendee_declared",
         "person_name": None, "person_title": None, "tier": None, "booth": None,
         "apollo": None, "source_url": "https://widgetexpo.test/community"},
    ])
    r = _client("reporting@position2.com").get(BASE + "/runs/5/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["Content-Type"]
    assert "widget-expo-participants.csv" in r.headers["Content-Disposition"]
    text = r.get_data(as_text=True)
    assert "Listed as" in text and "Source page" in text
    # The exhibitor is labelled Exhibitor, and only the declared row mentions
    # attending. Same wording as the screen, from the same ROLE_LABELS map.
    assert "Exhibitor" in text
    assert "Publicly said they are attending" in text
    acme_line = [ln for ln in text.splitlines() if "Acme Robotics" in ln][0]
    assert "attend" not in acme_line.lower(), acme_line
    assert "https://widgetexpo.test/exhibitors" in acme_line


def test_the_csv_is_crlf_terminated():
    """csv.writer's default lineterminator is \\r\\n regardless of how the
    handle was opened. That is correct for CSV and is asserted here so nobody
    "fixes" it into \\n and quietly breaks Excel on some platforms."""
    import csv
    import io
    buf = io.StringIO()
    csv.writer(buf).writerow(["a", "b"])
    assert buf.getvalue().endswith("\r\n")
