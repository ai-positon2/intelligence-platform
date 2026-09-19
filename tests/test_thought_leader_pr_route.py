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


class TestSearchRoute:
    def test_a_blank_query_never_touches_apollo(self, monkeypatch):
        monkeypatch.setattr(T, "search_name_candidates",
                            lambda *a, **kw: pytest.fail("must not search for a blank query"))
        resp = _client().get("/p2/strategic-agents/thought-leader-pr/search?q=")
        assert resp.status_code == 200
        assert resp.get_json() == {"candidates": []}

    def test_candidates_and_the_company_hint_pass_through(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(T, "search_name_candidates", lambda q, company_hint=None: (
            captured.update(q=q, company_hint=company_hint) or
            [{"full_name": "Jane Doe", "title": "CEO", "company": "Acme"}], None))
        resp = _client().get("/p2/strategic-agents/thought-leader-pr/search?q=Jane+Doe&company=Acme")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["candidates"][0]["full_name"] == "Jane Doe"
        assert "error" not in body
        assert captured == {"q": "Jane Doe", "company_hint": "Acme"}

    def test_an_error_is_forwarded_alongside_any_fallback_candidates(self, monkeypatch):
        monkeypatch.setattr(T, "search_name_candidates", lambda *a, **kw: (
            [], {"code": "not_configured", "message": "Apollo is not configured on this deployment."}))
        resp = _client().get("/p2/strategic-agents/thought-leader-pr/search?q=Jane+Doe")
        body = resp.get_json()
        assert body["candidates"] == []
        assert body["error"]["code"] == "not_configured"

    def test_search_requires_auth_too(self):
        resp = appmod.app.test_client().get("/p2/strategic-agents/thought-leader-pr/search?q=Jane")
        assert resp.status_code in (302, 401, 403)


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

    def test_an_oversized_field_is_capped_before_it_reaches_the_resolver(self, monkeypatch):
        """An audit found none of /resolve's fields were length-capped,
        unlike equivalent fields elsewhere in this app -- a pasted
        oversized string became an outsized billed Claude/Apollo input
        instead of a request this route rejected or trimmed outright."""
        captured = {}
        monkeypatch.setattr(T, "create_run", lambda **kw: captured.update(kw) or 1)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: captured.update(
            call_args=a, call_kw=kw) or {
            "ok": False, "confidence": "none", "reasoning": "x", "identity": None, "spend": {}, "error": None})
        monkeypatch.setattr(T, "save_result", lambda *a, **kw: True)
        _client().post("/p2/strategic-agents/thought-leader-pr/resolve",
                       json={"name": "A" * 5000, "company_hint": "B" * 5000})
        assert len(captured["input_name"]) == 200
        assert len(captured["company_hint"]) == 200
        assert len(captured["call_args"][0]) == 200

    def test_repeated_resolves_are_rate_limited(self, monkeypatch):
        """An audit found TLI had no rate limit anywhere, unlike every
        sibling agent -- a script looping /resolve (a real billed Claude
        web_search call each time) had nothing to stop it."""
        monkeypatch.setattr(T, "create_run", lambda **kw: 1)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: {
            "ok": False, "confidence": "none", "reasoning": "x", "identity": None, "spend": {}, "error": None})
        monkeypatch.setattr(T, "save_result", lambda *a, **kw: True)
        limit, _window = appmod._CPI_RATE_LIMITS["tli-resolve"]
        c = _client()
        for _ in range(limit):
            resp = c.post("/p2/strategic-agents/thought-leader-pr/resolve", json={"name": "Jane Doe"})
            assert resp.status_code == 200
        resp = c.post("/p2/strategic-agents/thought-leader-pr/resolve", json={"name": "Jane Doe"})
        assert resp.status_code == 429


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

    def test_repeated_collect_requests_are_rate_limited_and_never_start_a_job(self, monkeypatch):
        """An audit found none of the four collect-* routes had a rate
        limit -- each is a real billed vendor call (Apify, Unipile,
        YouTube quota, GDELT/SerpAPI, or a Claude call), unlike every
        sibling agent's own billed steps. The shared "tli-collect" bucket
        covers all four; a normal single run (one call to each) never
        approaches it."""
        monkeypatch.setattr(T, "start_collecting", lambda run_id, email: True)
        monkeypatch.setattr(T, "collect_posts_job", lambda run_id, email: None)
        limit, _window = appmod._CPI_RATE_LIMITS["tli-collect"]
        c = _client()
        for _ in range(limit):
            resp = c.post("/p2/strategic-agents/thought-leader-pr/runs/4/collect")
            assert resp.status_code == 200
        resp = c.post("/p2/strategic-agents/thought-leader-pr/runs/4/collect")
        assert resp.status_code == 429


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


class TestCollectPressRoute:
    """Phase 3's own billed action -- same discipline as /collect and
    /collect-reaction: only its own explicit POST may start it, and it must
    hand off to a background thread rather than blocking on live vendor
    calls."""

    def test_collect_press_requires_auth(self):
        c = appmod.app.test_client()
        resp = c.post("/p2/strategic-agents/thought-leader-pr/runs/1/collect-press")
        assert resp.status_code in (302, 401, 403)

    def test_not_confirmed_or_missing_is_404_and_never_starts_a_job(self, monkeypatch):
        monkeypatch.setattr(T, "start_press", lambda run_id, email: False)
        monkeypatch.setattr(T, "collect_press_job",
                            lambda *a, **kw: pytest.fail("must not start searching press for an unconfirmed run"))
        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/1/collect-press")
        assert resp.status_code == 404

    def test_confirmed_run_starts_a_background_job(self, monkeypatch):
        import threading
        started = threading.Event()
        captured = {}

        def fake_job(run_id, email):
            captured.update(run_id=run_id, email=email)
            started.set()

        monkeypatch.setattr(T, "start_press", lambda run_id, email: True)
        monkeypatch.setattr(T, "collect_press_job", fake_job)

        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/8/collect-press")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True, "press_status": "collecting"}
        assert started.wait(timeout=2), "background job never ran"
        assert captured == {"run_id": 8, "email": _OWNER}


class TestCollectSynthesisRoute:
    """Phase 4's own billed action -- same discipline as /collect,
    /collect-reaction, and /collect-press: only its own explicit POST may
    start it, and it must hand off to a background thread rather than
    blocking on a live Anthropic call."""

    def test_collect_synthesis_requires_auth(self):
        c = appmod.app.test_client()
        resp = c.post("/p2/strategic-agents/thought-leader-pr/runs/1/collect-synthesis")
        assert resp.status_code in (302, 401, 403)

    def test_not_confirmed_or_missing_is_404_and_never_starts_a_job(self, monkeypatch):
        monkeypatch.setattr(T, "start_synthesizing", lambda run_id, email: False)
        monkeypatch.setattr(T, "collect_synthesis_job",
                            lambda *a, **kw: pytest.fail("must not start synthesizing an unconfirmed run"))
        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/1/collect-synthesis")
        assert resp.status_code == 404

    def test_confirmed_run_starts_a_background_job(self, monkeypatch):
        import threading
        started = threading.Event()
        captured = {}

        def fake_job(run_id, email):
            captured.update(run_id=run_id, email=email)
            started.set()

        monkeypatch.setattr(T, "start_synthesizing", lambda run_id, email: True)
        monkeypatch.setattr(T, "collect_synthesis_job", fake_job)

        resp = _client().post("/p2/strategic-agents/thought-leader-pr/runs/10/collect-synthesis")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True, "synthesis_status": "collecting"}
        assert started.wait(timeout=2), "background job never ran"
        assert captured == {"run_id": 10, "email": _OWNER}
