"""/p2/b2b-agents/linkedin-playbook-studio (page + search/analyze/history/
runs/playbook). Every data route is @position2_required and scopes reads by
the server-verified session email, never a client-supplied one -- the direct
fix for a prior standalone tool's IDOR (its saved-run lookup trusted a bare
id/email with no ownership check at all).

Store functions are monkeypatched at the module level rather than hitting a
real Postgres (see tests/test_linkedin_playbook_store.py for that layer's own
ownership-scoping tests) -- this file is about the route/decorator/HTTP-shape
behavior: who can reach what, and what status code a missing-or-not-yours run
returns.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import linkedin_playbook_store as lps_store  # noqa: E402
from tracker import arena_client  # noqa: E402

_OWNER = "owner@position2.com"
_OTHER = "other@position2.com"


def _client(email=_OWNER):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


def _run(run_id=1, email=_OWNER, run_type="OWN", status="complete"):
    return {
        "id": run_id, "email": email, "parent_run_id": None, "run_type": run_type,
        "company_id": "c1", "company_name": "Acme", "company_logo": None,
        "status": status, "error": None, "summary": "A summary", "scorecard_score": 7.0,
        "output": {"strategyagent.strategy": "text"},
        "created_at": "2026-08-20T00:00:00+00:00", "updated_at": "2026-08-20T00:00:00+00:00",
    }


def _owner_scoped_get_run(monkeypatch, run=None):
    """A get_run stand-in that only returns the run when the email matches --
    the same ownership behavior the real Postgres-backed store guarantees via
    its WHERE clause (see test_linkedin_playbook_store.py for that layer)."""
    run = run or _run()

    def fake_get_run(run_id, email):
        if run_id == run["id"] and email == run["email"]:
            return run
        return None

    monkeypatch.setattr(lps_store, "get_run", fake_get_run)
    return run


# ── Page ──────────────────────────────────────────────────────────────────

def test_page_renders_for_any_position2_staff():
    resp = _client("someone@position2.com").get("/p2/b2b-agents/linkedin-playbook-studio")
    assert resp.status_code == 200
    assert b"LinkedIn Playbook Studio" in resp.data


def test_page_requires_login():
    c = appmod.app.test_client()
    resp = c.get("/p2/b2b-agents/linkedin-playbook-studio", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


# ── Search ────────────────────────────────────────────────────────────────

def test_search_returns_companies_from_the_arena_client(monkeypatch):
    monkeypatch.setattr(arena_client, "search_companies", lambda q: [{"id": "1", "name": "Acme"}])
    resp = _client().get("/p2/b2b-agents/linkedin-playbook-studio/search?q=Acme")
    assert resp.status_code == 200
    assert resp.get_json()["companies"] == [{"id": "1", "name": "Acme"}]


def test_search_with_no_query_returns_an_empty_list_without_calling_arena(monkeypatch):
    called = []
    monkeypatch.setattr(arena_client, "search_companies", lambda q: called.append(q) or [])
    resp = _client().get("/p2/b2b-agents/linkedin-playbook-studio/search")
    assert resp.get_json()["companies"] == []
    assert called == []


def test_search_degrades_to_an_empty_list_without_a_configured_key(monkeypatch):
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    resp = _client().get("/p2/b2b-agents/linkedin-playbook-studio/search?q=Acme")
    assert resp.status_code == 200
    assert resp.get_json()["companies"] == []


# ── Analyze ───────────────────────────────────────────────────────────────

def test_analyze_requires_company_id_and_name():
    resp = _client().post("/p2/b2b-agents/linkedin-playbook-studio/analyze", json={"mode": "OWN"})
    assert resp.status_code == 400


def test_analyze_own_brand_starts_a_run_and_returns_its_id(monkeypatch):
    monkeypatch.setattr(lps_store, "save_run", lambda *a, **k: 42)
    resp = _client().post("/p2/b2b-agents/linkedin-playbook-studio/analyze",
                          json={"company_id": "c1", "company_name": "Acme", "mode": "OWN"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["run_id"] == 42 and body["status"] == "running"


def test_analyze_competitor_requires_a_completed_own_brand_parent(monkeypatch):
    monkeypatch.setattr(lps_store, "get_run", lambda run_id, email: None)
    resp = _client().post("/p2/b2b-agents/linkedin-playbook-studio/analyze",
                          json={"company_id": "c2", "company_name": "Globex",
                                "mode": "COMPETITOR", "parent_run_id": "1"})
    assert resp.status_code == 404


def test_analyze_competitor_refuses_a_parent_run_that_is_not_yet_complete(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, status="running"))
    resp = _client().post("/p2/b2b-agents/linkedin-playbook-studio/analyze",
                          json={"company_id": "c2", "company_name": "Globex",
                                "mode": "COMPETITOR", "parent_run_id": "1"})
    assert resp.status_code == 404


def test_analyze_competitor_cannot_use_someone_elses_run_as_the_parent(monkeypatch):
    """The parent-run lookup goes through the same ownership-scoped get_run as
    everything else -- a stranger's run id is indistinguishable from a
    nonexistent one."""
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER, status="complete"))
    resp = _client(_OWNER).post("/p2/b2b-agents/linkedin-playbook-studio/analyze",
                                json={"company_id": "c2", "company_name": "Globex",
                                      "mode": "COMPETITOR", "parent_run_id": "1"})
    assert resp.status_code == 404


def test_analyze_starts_running_even_without_a_configured_key(monkeypatch):
    """The background job itself degrades gracefully (see test_arena_client.py) --
    starting it must not be blocked just because ARENA_API_KEY happens to be unset."""
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    monkeypatch.setattr(lps_store, "save_run", lambda *a, **k: 1)
    resp = _client().post("/p2/b2b-agents/linkedin-playbook-studio/analyze",
                          json={"company_id": "c1", "company_name": "Acme", "mode": "OWN"})
    assert resp.status_code == 200


# ── Run status / detail / history ────────────────────────────────────────

def test_run_status_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-playbook-studio/runs/1/status")
    assert resp.status_code == 404


def test_run_status_returns_the_status_for_the_owning_user(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, status="running"))
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-playbook-studio/runs/1/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "running"


def test_run_detail_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    monkeypatch.setattr(lps_store, "get_children", lambda *a, **k: [])
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-playbook-studio/runs/1")
    assert resp.status_code == 404


def test_run_detail_returns_the_full_run_for_its_owner(monkeypatch):
    _owner_scoped_get_run(monkeypatch)
    monkeypatch.setattr(lps_store, "get_children", lambda *a, **k: [])
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-playbook-studio/runs/1")
    assert resp.status_code == 200
    assert resp.get_json()["company_name"] == "Acme"


def test_history_only_reflects_the_calling_users_own_email(monkeypatch):
    seen_emails = []

    def fake_list_runs(email, limit=100):
        seen_emails.append(email)
        return [_run()] if email == _OWNER else []

    monkeypatch.setattr(lps_store, "list_runs", fake_list_runs)
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-playbook-studio/history")
    assert resp.status_code == 200
    assert len(resp.get_json()["runs"]) == 1
    assert seen_emails == [_OWNER]


# ── Playbook ──────────────────────────────────────────────────────────────

def test_playbook_get_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-playbook-studio/runs/1/playbook")
    assert resp.status_code == 404


def test_playbook_get_returns_none_when_not_yet_generated(monkeypatch):
    _owner_scoped_get_run(monkeypatch)
    monkeypatch.setattr(lps_store, "get_playbook", lambda *a, **k: None)
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-playbook-studio/runs/1/playbook")
    assert resp.status_code == 200
    assert resp.get_json()["playbook"] is None


def test_playbook_post_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    """The generation trigger is ownership-checked exactly like every read --
    a stranger cannot spend this account's Arena credits generating a
    playbook for a run they don't own."""
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    resp = _client(_OWNER).post("/p2/b2b-agents/linkedin-playbook-studio/runs/1/playbook",
                                json={"mode": "OWN"})
    assert resp.status_code == 404


def test_playbook_post_starts_generation_for_the_runs_owner(monkeypatch):
    _owner_scoped_get_run(monkeypatch)
    resp = _client(_OWNER).post("/p2/b2b-agents/linkedin-playbook-studio/runs/1/playbook",
                                json={"mode": "OWN"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "running"
