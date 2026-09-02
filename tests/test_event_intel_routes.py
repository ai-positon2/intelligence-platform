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
    (BASE + "/profiles", "GET"),
    (BASE + "/profiles", "POST"),
    (BASE + "/profiles/1", "POST"),
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


# ── the locked profile, and the hard stop in front of recommend mode ──────

def _p2(monkeypatch=None):
    return _client("someone@position2.com")


def test_recommend_without_a_profile_is_refused_with_the_real_reason():
    """The source skill's HARD STOP. A default classification would score the
    opposite side of the trade-show floor and nothing downstream would look
    wrong, so the route refuses rather than assumes."""
    r = _p2().post(BASE + "/run", json={"mode": "recommend"})
    assert r.status_code == 400
    assert "which side of the event floor" in r.get_json()["error"].lower()


def test_recommend_with_an_unknown_profile_id_is_refused():
    r = _p2().post(BASE + "/run", json={"mode": "recommend", "profile_id": 999999})
    assert r.status_code == 400


def test_an_unknown_mode_is_still_refused():
    r = _p2().post(BASE + "/run", json={"mode": "attendees", "query": "x"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "Unknown mode."


def test_recommend_is_an_accepted_mode(monkeypatch):
    """Proved by how it fails: a mode the route did not know would come back
    'Unknown mode', not a complaint about the profile."""
    r = _p2().post(BASE + "/run", json={"mode": "recommend", "profile_id": 1})
    assert r.get_json()["error"] != "Unknown mode."


def test_a_profile_with_a_bad_classification_is_a_400_carrying_the_reason():
    r = _p2().post(BASE + "/profiles",
                   json={"client_name": "Northwind", "classification": "b2b"})
    assert r.status_code == 400
    assert "never inferred" in r.get_json()["error"]


def test_a_profile_with_no_client_name_is_a_400():
    from tracker import event_intel_rubric as rubric
    r = _p2().post(BASE + "/profiles",
                   json={"client_name": "  ",
                         "classification": rubric.CLASS_B2B_TO_MARKETING})
    assert r.status_code == 400
    assert "too generic" in r.get_json()["error"]


def test_profile_routes_are_position2_gated():
    for path in (BASE + "/profiles", BASE + "/profiles/1"):
        r = _client().post(path, json={})
        assert r.status_code in (302, 401, 403), path


def test_the_page_offers_exactly_the_four_classifications_the_rubric_knows():
    """Rendered from the rubric's own vocabulary, so the form can never offer a
    fifth option the scorer would refuse."""
    from tracker import event_intel_rubric as rubric
    html = _p2().get(BASE).get_data(as_text=True)
    for key in rubric.CLASSIFICATIONS:
        assert key in html, key
    assert html.count('data-classification="') == len(rubric.CLASSIFICATIONS)


# ── the retired play ──────────────────────────────────────────────────────
#
# `discover` described an audience and got back events ranked by how many of
# your own named accounts turned up in them. It was retired because it read,
# to anybody arriving on the page, as a shorter and worse `recommend`.
#
# Retiring is not deleting. No new run can start, but runs already stored
# still open from history and are still valid as a workroom source, so these
# two tests pull in opposite directions on purpose: the door is shut and the
# records are still readable.


def test_no_new_run_can_be_started_on_the_retired_play():
    c = _client("harness@position2.com")
    r = c.post(BASE + "/run", json={"mode": "discover",
                                    "query": "VPs of marketing at fintechs"})
    assert r.status_code == 400, (
        "the retired play still accepts runs (%s)" % r.status_code)


def test_the_retired_play_is_gone_from_the_page_that_starts_runs():
    c = _client("harness@position2.com")
    html = c.get(BASE).get_data(as_text=True)
    assert 'data-play="discover"' not in html, "the retired play still has a card"
    assert 'id="discoverFields"' not in html, "the retired play still has its form"
    assert "setMode('discover')" not in html, "the retired play is still selectable"


def test_a_run_already_stored_under_the_retired_play_still_opens(monkeypatch):
    """The reason the drawer and the roster picker still know the word. A
    retirement that quietly broke every run somebody had already paid for
    would be a deletion wearing a retirement's clothes."""
    run = {"id": 7, "mode": "discover", "status": "complete", "stage": "done",
           "query": "VPs of marketing at fintechs", "error": None,
           "credits_spent": 0, "created_at": "2026-08-31T10:00:00",
           "summary": {"discovered_events": 2}}
    monkeypatch.setattr(store, "get_run", lambda run_id, email: dict(run))
    monkeypatch.setattr(store, "get_events", lambda run_id: [])
    monkeypatch.setattr(store, "get_participants", lambda run_id: [])
    monkeypatch.setattr(store, "get_sources", lambda run_id: [])
    monkeypatch.setattr(store, "get_candidates", lambda run_id: [])
    monkeypatch.setattr(store, "get_outreach", lambda run_id: [])
    c = _client("harness@position2.com")
    r = c.get(BASE + "/runs/7")
    assert r.status_code == 200, "a stored run of the retired play no longer opens"
    assert r.get_json()["mode"] == "discover"


def test_a_stored_run_of_the_retired_play_is_still_a_usable_roster(monkeypatch):
    """Work the room reads a roster somebody already harvested. A roster that
    was harvested is a roster, whatever play harvested it."""
    runs = [{"id": 7, "mode": "discover", "query": "fintech VPs",
             "status": "complete", "created_at": "2026-08-31T10:00:00",
             "credits_spent": 0, "participant_count": 12,
             "event_name": "Fintech Summit"}]
    monkeypatch.setattr(store, "list_runs", lambda email, limit=60: runs)
    monkeypatch.setattr(store, "list_profiles", lambda email: [])
    c = _client("harness@position2.com")
    html = c.get(BASE).get_data(as_text=True)
    assert 'value="7"' in html and "Fintech Summit" in html, (
        "a harvested roster from the retired play is no longer offered to "
        "work the room")
