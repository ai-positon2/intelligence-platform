"""/p2/strategic-agents/thought-leader-pr (page + resolve/confirm/run).

Mirrors tests/test_social_media_intelligence_route.py: every route is
@position2_required and gated by the server-verified session email; the
resolve endpoint is the one billed step (a claude_websearch call under the
hood) so it must only ever fire on an explicit POST, never on the page's own
GET. thought_leader_pr's own functions are monkeypatched at the module level
here -- its resolution logic has its own dedicated tests in
tests/test_thought_leader_pr.py.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import thought_leader_pr as T  # noqa: E402

_OWNER = "owner@position2.com"
_NON_P2 = "someone@gmail.com"


def _client(email=_OWNER):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


class TestAuthGate:
    def test_anonymous_request_is_redirected_not_served(self):
        c = appmod.app.test_client()
        resp = c.get("/p2/strategic-agents/thought-leader-pr")
        assert resp.status_code in (302, 401, 403)

    def test_a_non_position2_email_cannot_reach_the_page(self):
        c = _client(_NON_P2)
        resp = c.get("/p2/strategic-agents/thought-leader-pr")
        assert resp.status_code in (302, 401, 403)

    def test_resolve_requires_auth_too(self):
        c = appmod.app.test_client()
        resp = c.post("/p2/strategic-agents/thought-leader-pr/resolve", json={"name": "Jane Doe"})
        assert resp.status_code in (302, 401, 403)


class TestPageRenders:
    def test_page_loads_for_a_position2_user(self, monkeypatch):
        monkeypatch.setattr(T, "list_runs", lambda email: [])
        resp = _client().get("/p2/strategic-agents/thought-leader-pr")
        assert resp.status_code == 200
        assert b"Thought Leader Intelligence" in resp.data

    def test_page_never_calls_resolve_identity_itself(self, monkeypatch):
        """The billed lookup must only ever run from an explicit POST -- same
        rule Contact Finder and Event & Conference Intelligence both follow."""
        monkeypatch.setattr(T, "list_runs", lambda email: [])
        monkeypatch.setattr(T, "resolve_identity",
                            lambda *a, **kw: pytest.fail("GET must never resolve an identity"))
        resp = _client().get("/p2/strategic-agents/thought-leader-pr")
        assert resp.status_code == 200


class TestResolveRoute:
    def test_blank_name_is_rejected_before_touching_the_resolver(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity",
                            lambda *a, **kw: pytest.fail("must not resolve a blank name"))
        resp = _client().post("/p2/strategic-agents/thought-leader-pr/resolve", json={"name": "   "})
        assert resp.status_code == 400

    def test_a_confident_result_is_persisted_and_returned(self, monkeypatch):
        identity = {"full_name": "Jane Doe", "confidence": "high", "platforms": {}}
        result = {"ok": True, "confidence": "high", "reasoning": "ok", "identity": identity,
                 "spend": {}, "error": None}
        monkeypatch.setattr(T, "create_run", lambda **kw: 42)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: result)
        saved = {}
        monkeypatch.setattr(T, "save_result", lambda run_id, email, res: saved.update(
            run_id=run_id, email=email, res=res) or True)

        resp = _client().post("/p2/strategic-agents/thought-leader-pr/resolve",
                              json={"name": "Jane Doe", "company_hint": "Acme"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["run_id"] == 42
        assert body["identity"]["full_name"] == "Jane Doe"
        assert saved["run_id"] == 42 and saved["email"] == _OWNER

    def test_x_handle_is_stripped_of_a_leading_at(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(T, "create_run", lambda **kw: captured.update(kw) or 1)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: captured.update(call_kw=kw) or {
            "ok": False, "confidence": "none", "reasoning": "x", "identity": None, "spend": {}, "error": None})
        monkeypatch.setattr(T, "save_result", lambda *a, **kw: True)
        _client().post("/p2/strategic-agents/thought-leader-pr/resolve",
                       json={"name": "Jane Doe", "x_handle": "@janedoe"})
        assert captured["x_handle_hint"] == "janedoe"
        assert captured["call_kw"]["x_handle"] == "janedoe"

    def test_a_run_that_could_not_be_persisted_still_returns_the_result(self, monkeypatch):
        """create_run failing (no DATABASE_URL) must not block showing the
        user their result -- persistence is a nice-to-have, not a gate."""
        result = {"ok": False, "confidence": "none", "reasoning": "not found",
                 "identity": None, "spend": {}, "error": None}
        monkeypatch.setattr(T, "create_run", lambda **kw: None)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: result)
        monkeypatch.setattr(T, "save_result", lambda *a, **kw: pytest.fail("must not be called with no run_id"))
        resp = _client().post("/p2/strategic-agents/thought-leader-pr/resolve", json={"name": "Jane Doe"})
        assert resp.status_code == 200
        assert resp.get_json()["run_id"] is None


class TestConfirmAndRunRoutes:
    def test_confirm_is_scoped_and_reports_failure_as_404(self, monkeypatch):
        monkeypatch.setattr(T, "confirm_run", lambda run_id, email: False)
        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/1/confirm")
        assert resp.status_code == 404

    def test_confirm_success(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(T, "confirm_run", lambda run_id, email: captured.update(
            run_id=run_id, email=email) or True)
        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/7/confirm")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}
        assert captured == {"run_id": 7, "email": _OWNER}

    def test_get_run_not_found_is_404(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: None)
        resp = _client().get("/p2/strategic-agents/thought-leader-pr/runs/1")
        assert resp.status_code == 404

    def test_get_run_is_scoped_to_the_session_email(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(T, "get_run", lambda run_id, email: captured.update(
            run_id=run_id, email=email) or {"id": run_id, "status": "confirmed"})
        resp = _client("someone.else@position2.com").get(
            "/p2/strategic-agents/thought-leader-pr/runs/9")
        assert resp.status_code == 200
        assert captured == {"run_id": 9, "email": "someone.else@position2.com"}


class TestCollectRoute:
    """Phase 1's own billed action -- must never fire from anything but its
    own explicit POST, and must actually hand off to a background thread
    rather than blocking the request on three live vendor calls."""

    def test_collect_requires_auth(self):
        c = appmod.app.test_client()
        resp = c.post("/p2/strategic-agents/thought-leader-pr/runs/1/collect")
        assert resp.status_code in (302, 401, 403)

    def test_not_confirmed_or_missing_is_404_and_never_starts_a_job(self, monkeypatch):
        monkeypatch.setattr(T, "start_collecting", lambda run_id, email: False)
        monkeypatch.setattr(T, "collect_posts_job",
                            lambda *a, **kw: pytest.fail("must not start collecting an unconfirmed run"))
        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/1/collect")
        assert resp.status_code == 404

    def test_confirmed_run_starts_a_background_job(self, monkeypatch):
        import threading
        started = threading.Event()
        captured = {}

        def fake_job(run_id, email):
            captured.update(run_id=run_id, email=email)
            started.set()

        monkeypatch.setattr(T, "start_collecting", lambda run_id, email: True)
        monkeypatch.setattr(T, "collect_posts_job", fake_job)

        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/4/collect")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True, "posts_status": "collecting"}
        assert started.wait(timeout=2), "background job never ran"
        assert captured == {"run_id": 4, "email": _OWNER}


class TestCollectReactionRoute:
    """Phase 2's own billed action -- same discipline as /collect: only its
    own explicit POST may start it, and it must hand off to a background
    thread rather than blocking on live vendor calls."""

    def test_collect_reaction_requires_auth(self):
        c = appmod.app.test_client()
        resp = c.post("/p2/strategic-agents/thought-leader-pr/runs/1/collect-reaction")
        assert resp.status_code in (302, 401, 403)

    def test_not_confirmed_or_missing_is_404_and_never_starts_a_job(self, monkeypatch):
        monkeypatch.setattr(T, "start_reacting", lambda run_id, email: False)
        monkeypatch.setattr(T, "collect_reaction_job",
                            lambda *a, **kw: pytest.fail("must not start analyzing an unconfirmed run"))
        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/1/collect-reaction")
        assert resp.status_code == 404

    def test_confirmed_run_starts_a_background_job(self, monkeypatch):
        import threading
        started = threading.Event()
        captured = {}

        def fake_job(run_id, email):
            captured.update(run_id=run_id, email=email)
            started.set()

        monkeypatch.setattr(T, "start_reacting", lambda run_id, email: True)
        monkeypatch.setattr(T, "collect_reaction_job", fake_job)

        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/6/collect-reaction")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True, "reaction_status": "collecting"}
        assert started.wait(timeout=2), "background job never ran"
        assert captured == {"run_id": 6, "email": _OWNER}
