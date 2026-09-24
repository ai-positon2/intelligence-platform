"""Thought Leader Intelligence's HTTP surface under hostile and
out-of-order use.

tests/test_thought_leader_pr_route.py proves the ordinary path of each
route. This file covers what a real caller (or a script, or a second
browser tab) can actually do to them: a run id that does not exist, phases
called out of order, the same collect endpoint POSTed twice at once,
bodies that are not the JSON the route expects, oversized payloads, and
every route reached with no session at all.

Nothing below reaches Postgres or a vendor: thought_leader_pr's own store
and job functions are monkeypatched at the module boundary, which is where
app.py actually calls them.
"""

from __future__ import annotations

import os
import sys
import threading

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import thought_leader_pr as T  # noqa: E402

_OWNER = "owner@position2.com"
_OTHER = "someone.else@position2.com"
_NON_P2 = "outsider@gmail.com"

_BASE = "/p2/strategic-agents/thought-leader-pr"
_COLLECT_ROUTES = ["collect", "collect-reaction", "collect-press", "collect-synthesis"]
_STARTERS = {"collect": "start_collecting", "collect-reaction": "start_reacting",
             "collect-press": "start_press", "collect-synthesis": "start_synthesizing"}
_JOBS = {"collect": "collect_posts_job", "collect-reaction": "collect_reaction_job",
         "collect-press": "collect_press_job", "collect-synthesis": "collect_synthesis_job"}
_STATUS_KEY = {"collect": "posts_status", "collect-reaction": "reaction_status",
               "collect-press": "press_status", "collect-synthesis": "synthesis_status"}


@pytest.fixture(autouse=True)
def _clean_rate_limit_state():
    """The limiter's state is a process-global keyed by email, so a test
    that deliberately exhausts a bucket would otherwise poison every test
    after it in this file."""
    appmod._CPI_RATE_STATE.clear()
    yield
    appmod._CPI_RATE_STATE.clear()


def _client(email=_OWNER):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


def _never(*a, **kw):
    pytest.fail("this must not be reached")


# ───────────────────────── auth, on every route ─────────────────────────

_ALL_ROUTES = (
    [("GET", _BASE), ("GET", _BASE + "/search?q=jane"), ("POST", _BASE + "/resolve"),
     ("POST", _BASE + "/runs/1/confirm"), ("GET", _BASE + "/runs/1")]
    + [("POST", "%s/runs/1/%s" % (_BASE, r)) for r in _COLLECT_ROUTES]
)


class TestAuthGateEverywhere:
    @pytest.mark.parametrize("method,path", _ALL_ROUTES,
                             ids=[p for _m, p in _ALL_ROUTES])
    def test_an_anonymous_caller_is_turned_away(self, method, path):
        c = appmod.app.test_client()
        resp = c.open(path, method=method)
        assert resp.status_code in (302, 401, 403)

    @pytest.mark.parametrize("method,path", _ALL_ROUTES,
                             ids=[p for _m, p in _ALL_ROUTES])
    def test_a_non_position2_session_is_turned_away(self, method, path):
        resp = _client(_NON_P2).open(path, method=method)
        assert resp.status_code in (302, 401, 403)

    @pytest.mark.parametrize("route", _COLLECT_ROUTES)
    def test_an_anonymous_collect_never_starts_a_background_job(self, route, monkeypatch):
        monkeypatch.setattr(T, _STARTERS[route], _never)
        monkeypatch.setattr(T, _JOBS[route], _never)
        resp = appmod.app.test_client().post("%s/runs/1/%s" % (_BASE, route))
        assert resp.status_code in (302, 401, 403)

    @pytest.mark.parametrize("route", _COLLECT_ROUTES)
    def test_a_collect_route_refuses_a_get(self, route):
        resp = _client().get("%s/runs/1/%s" % (_BASE, route))
        assert resp.status_code == 405

    def test_resolve_refuses_a_get(self):
        assert _client().get(_BASE + "/resolve").status_code == 405

    def test_confirm_refuses_a_get(self):
        assert _client().get(_BASE + "/runs/1/confirm").status_code == 405


class TestRunIdHandling:
    @pytest.mark.parametrize("route", _COLLECT_ROUTES)
    def test_a_run_that_does_not_exist_is_a_404_not_a_crash(self, route, monkeypatch):
        monkeypatch.setattr(T, _STARTERS[route], lambda run_id, email: False)
        monkeypatch.setattr(T, _JOBS[route], _never)
        resp = _client().post("%s/runs/999999/%s" % (_BASE, route))
        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_a_missing_run_on_the_read_route_is_a_404(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: None)
        resp = _client().get(_BASE + "/runs/999999")
        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Not found"}

    @pytest.mark.parametrize("bad", ["abc", "1.5", "-1", "1%20OR%201=1", "null", ""])
    def test_a_run_id_that_is_not_a_positive_integer_never_reaches_the_store(
            self, bad, monkeypatch):
        monkeypatch.setattr(T, "get_run", _never)
        resp = _client().get("%s/runs/%s" % (_BASE, bad))
        assert resp.status_code in (404, 308)

    def test_an_enormous_run_id_is_handled_by_the_store_not_by_a_crash(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(T, "get_run", lambda run_id, email: seen.update(run_id=run_id) or None)
        resp = _client().get("%s/runs/%d" % (_BASE, 10 ** 30))
        assert resp.status_code == 404
        assert seen["run_id"] == 10 ** 30

    def test_a_run_is_always_read_under_the_session_email_not_a_requested_one(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(T, "get_run", lambda run_id, email: seen.update(email=email) or None)
        _client(_OTHER).get(_BASE + "/runs/5?email=" + _OWNER)
        assert seen["email"] == _OTHER

    @pytest.mark.parametrize("route", _COLLECT_ROUTES)
    def test_a_collect_claim_is_always_made_under_the_session_email(self, route, monkeypatch):
        seen = {}
        monkeypatch.setattr(T, _STARTERS[route],
                            lambda run_id, email: seen.update(email=email) or False)
        monkeypatch.setattr(T, _JOBS[route], _never)
        _client(_OTHER).post("%s/runs/5/%s" % (_BASE, route))
        assert seen["email"] == _OTHER


class TestOutOfOrderPhases:
    """Every collect phase is gated on the confirmed identity alone, on
    purpose: each one writes plainly around whatever earlier phase never
    ran. What must NOT happen is a phase silently doing nothing, or
    crashing, when an earlier one is missing."""

    def test_synthesis_before_any_other_phase_still_starts(self, monkeypatch):
        started = threading.Event()
        monkeypatch.setattr(T, "start_synthesizing", lambda run_id, email: True)
        monkeypatch.setattr(T, "collect_synthesis_job",
                            lambda run_id, email: started.set())
        resp = _client().post(_BASE + "/runs/3/collect-synthesis")
        assert resp.status_code == 200
        assert started.wait(timeout=2)

    def test_synthesis_of_a_run_with_no_reaction_or_press_writes_around_them(self, monkeypatch):
        """The real job function, with only an identity on the run row."""
        import anthropic

        captured = {}
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setattr(T, "get_run", lambda rid, email: {
            "identity": {"full_name": "Jane Doe"}, "posts": None, "reaction": None,
            "press": None, "press_errors": None})
        monkeypatch.setattr(T, "save_synthesis", lambda rid, email, rep, errs: True)
        monkeypatch.setattr(T, "save_synthesis_failed",
                            lambda rid, email, msg: pytest.fail("a thin run is not a failure: %s" % msg))

        class _B:
            type = "text"
            text = '{"headline": "H", "verdict": "V", "strengths": [], "risks": [], "alignment": "A"}'

        class _R:
            content = [_B()]
            stop_reason = "end_turn"

        class _M:
            def create(self, **kw):
                captured.update(kw)
                return _R()

        class _C:
            messages = _M()

        monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _C())
        T.collect_synthesis_job(3, _OWNER)
        import json
        payload = json.loads(captured["messages"][0]["content"])
        assert payload["reaction"] == {"available": False}
        assert payload["press"] == {"available": False}
        assert payload["posts"]["available"] is False

    def test_reaction_before_posts_reads_an_empty_posts_block(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(T, "get_run", lambda rid, email: {
            "id": 1, "identity": {"full_name": "Jane Doe", "platforms": {}}, "posts": None})
        for name in ("tlpr_reddit_pulse", "tlpr_x_pulse", "tlpr_linkedin_pulse",
                     "tlpr_tiktok_pulse", "tlpr_instagram_pulse", "tlpr_facebook_pulse"):
            monkeypatch.setattr(getattr(T, name), "build_pulse",
                                lambda *a, **kw: {"note": "stub", "thread_count": 0,
                                                  "tweet_count": 0, "post_count": 0,
                                                  "video_count": 0, "mention_count": 0})
        monkeypatch.setattr(T, "save_reaction", lambda rid, email, reaction, errors: saved.update(
            reaction=reaction, errors=errors) or True)
        T.collect_reaction_job(1, _OWNER)
        assert saved["reaction"]["comments_analyzed"] == 0
        assert saved["errors"]["linkedin"] == "No LinkedIn posts to read comments from."
        assert saved["errors"]["x"] == "No X posts to read replies from."
        assert saved["errors"]["youtube"] == "No YouTube videos to read comments from."

    def test_posts_collection_of_a_run_with_no_platforms_reports_each_platform(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(T, "get_run", lambda rid, email: {
            "id": 1, "identity": {"full_name": "Jane Doe", "platforms": {}}})
        monkeypatch.setattr(T, "save_posts", lambda rid, email, posts, errors: saved.update(
            posts=posts, errors=errors) or True)
        T.collect_posts_job(1, _OWNER)
        assert saved["posts"] == {"linkedin": [], "x": [], "youtube": []}
        assert set(saved["errors"]) == {"linkedin", "x", "youtube"}

    def test_a_confirmed_run_with_no_identity_fails_the_phase_rather_than_the_request(
            self, monkeypatch):
        saved = {}
        monkeypatch.setattr(T, "get_run", lambda rid, email: {"id": 1, "identity": None})
        monkeypatch.setattr(T, "save_reaction_failed",
                            lambda rid, email, msg: saved.update(msg=msg) or True)
        T.collect_reaction_job(1, _OWNER)
        assert saved["msg"] == "This run has no confirmed identity to analyze."


class TestDoubleSubmitAndRaces:
    """start_* is a CLAIM, not a status write: its UPDATE only matches a row
    whose status is not already 'collecting'. The route's half of that
    contract is that it starts a thread only when the claim succeeded."""

    @pytest.mark.parametrize("route", _COLLECT_ROUTES)
    def test_two_simultaneous_posts_start_exactly_one_job(self, route, monkeypatch):
        claim_lock = threading.Lock()
        claimed = {"taken": False}
        jobs = []

        def claim(run_id, email):
            with claim_lock:
                if claimed["taken"]:
                    return False
                claimed["taken"] = True
                return True

        monkeypatch.setattr(T, _STARTERS[route], claim)
        monkeypatch.setattr(T, _JOBS[route], lambda run_id, email: jobs.append(run_id))

        client = _client()
        results = []
        barrier = threading.Barrier(2)

        def fire():
            barrier.wait()
            results.append(client.post("%s/runs/4/%s" % (_BASE, route)).status_code)

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert sorted(results) == [200, 404]
        assert len(jobs) <= 1

    @pytest.mark.parametrize("route", _COLLECT_ROUTES)
    def test_a_second_sequential_post_while_collecting_is_refused(self, route, monkeypatch):
        state = {"collecting": False}
        jobs = []

        def claim(run_id, email):
            if state["collecting"]:
                return False
            state["collecting"] = True
            return True

        monkeypatch.setattr(T, _STARTERS[route], claim)
        monkeypatch.setattr(T, _JOBS[route], lambda run_id, email: jobs.append(run_id))
        client = _client()
        assert client.post("%s/runs/4/%s" % (_BASE, route)).status_code == 200
        assert client.post("%s/runs/4/%s" % (_BASE, route)).status_code == 404

    @pytest.mark.parametrize("route", _COLLECT_ROUTES)
    def test_a_refused_claim_never_reports_the_phase_as_started(self, route, monkeypatch):
        monkeypatch.setattr(T, _STARTERS[route], lambda run_id, email: False)
        monkeypatch.setattr(T, _JOBS[route], _never)
        body = _client().post("%s/runs/4/%s" % (_BASE, route)).get_json()
        assert _STATUS_KEY[route] not in body

    @pytest.mark.parametrize("route", _COLLECT_ROUTES)
    def test_a_successful_claim_reports_that_phases_own_status_key(self, route, monkeypatch):
        monkeypatch.setattr(T, _STARTERS[route], lambda run_id, email: True)
        monkeypatch.setattr(T, _JOBS[route], lambda run_id, email: None)
        body = _client().post("%s/runs/4/%s" % (_BASE, route)).get_json()
        assert body == {"ok": True, _STATUS_KEY[route]: "collecting"}

    def test_two_different_phases_on_one_run_both_start(self, monkeypatch):
        started = []
        for route in ("collect-press", "collect-reaction"):
            monkeypatch.setattr(T, _STARTERS[route], lambda run_id, email: True)
            monkeypatch.setattr(T, _JOBS[route],
                                lambda run_id, email, r=route: started.append(r))
        client = _client()
        assert client.post(_BASE + "/runs/4/collect-press").status_code == 200
        assert client.post(_BASE + "/runs/4/collect-reaction").status_code == 200
        assert len(started) == 2

    def test_the_response_returns_without_waiting_for_the_job_to_finish(self, monkeypatch):
        """A press search is minutes of live vendor calls; the request must
        hand off and return, not block a Railway worker on it."""
        release = threading.Event()
        entered = threading.Event()

        def slow_job(run_id, email):
            entered.set()
            release.wait(timeout=10)

        monkeypatch.setattr(T, "start_press", lambda run_id, email: True)
        monkeypatch.setattr(T, "collect_press_job", slow_job)
        try:
            resp = _client().post(_BASE + "/runs/4/collect-press")
            assert resp.status_code == 200
            assert entered.wait(timeout=5)
        finally:
            release.set()


class TestMalformedRequestBodies:
    def test_a_body_that_is_not_json_at_all_is_a_clean_400(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity", _never)
        resp = _client().post(_BASE + "/resolve", data="not json",
                              content_type="application/json")
        assert resp.status_code == 400

    def test_a_json_array_body_is_a_clean_400(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity", _never)
        resp = _client().post(_BASE + "/resolve", json=["name"])
        assert resp.status_code == 400

    def test_a_json_string_body_is_a_clean_400(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity", _never)
        assert _client().post(_BASE + "/resolve", json="Jane Doe").status_code == 400

    def test_an_empty_body_is_a_clean_400(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity", _never)
        assert _client().post(_BASE + "/resolve").status_code == 400

    def test_a_body_with_no_name_key_is_a_clean_400(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity", _never)
        assert _client().post(_BASE + "/resolve", json={"company_hint": "Acme"}).status_code == 400

    def test_a_null_name_is_a_clean_400(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity", _never)
        assert _client().post(_BASE + "/resolve", json={"name": None}).status_code == 400

    def test_a_numeric_name_is_a_clean_400_rather_than_a_type_error(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity", _never)
        assert _client().post(_BASE + "/resolve", json={"name": 12345}).status_code == 400

    def test_a_list_valued_optional_hint_is_ignored_rather_than_a_type_error(self, monkeypatch):
        """An optional hint of the wrong type is not worth refusing a
        request over -- it just is not a hint. What must not happen is the
        500 it used to produce."""
        captured = {}
        monkeypatch.setattr(T, "create_run", lambda **kw: 1)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: captured.update(kw=kw) or {
            "ok": False, "confidence": "none", "reasoning": "", "identity": None,
            "spend": {}, "error": None})
        monkeypatch.setattr(T, "save_result", lambda *a, **kw: True)
        resp = _client().post(_BASE + "/resolve",
                              json={"name": "Jane Doe", "company_hint": ["Acme"],
                                    "title_hint": {"a": 1}, "x_handle": 42})
        assert resp.status_code == 200
        assert captured["kw"] == {"company_hint": None, "title_hint": None,
                                  "linkedin_url": None, "x_handle": None}

    def test_a_megabyte_of_name_is_capped_before_anything_is_billed(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(T, "create_run", lambda **kw: captured.update(kw) or 1)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: captured.update(
            args=a, kw=kw) or {"ok": False, "confidence": "none", "reasoning": "",
                               "identity": None, "spend": {}, "error": None})
        monkeypatch.setattr(T, "save_result", lambda *a, **kw: True)
        resp = _client().post(_BASE + "/resolve", json={
            "name": "A" * 1000000, "company_hint": "B" * 1000000,
            "title_hint": "C" * 1000000, "linkedin_url": "D" * 1000000,
            "x_handle": "E" * 1000000})
        assert resp.status_code == 200
        assert len(captured["args"][0]) == 200
        assert len(captured["kw"]["company_hint"]) == 200
        assert len(captured["kw"]["title_hint"]) == 200
        assert len(captured["kw"]["linkedin_url"]) == 500
        assert len(captured["kw"]["x_handle"]) == 100

    def test_an_unknown_extra_field_is_ignored_rather_than_forwarded(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(T, "create_run", lambda **kw: captured.update(create=kw) or 1)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: captured.update(kw=kw) or {
            "ok": False, "confidence": "none", "reasoning": "", "identity": None,
            "spend": {}, "error": None})
        monkeypatch.setattr(T, "save_result", lambda *a, **kw: True)
        _client().post(_BASE + "/resolve", json={"name": "Jane Doe", "email": "attacker@evil.io",
                                                 "is_admin": True})
        assert "email" not in captured["kw"]
        assert captured["create"]["email"] == _OWNER

    def test_a_form_encoded_post_is_a_clean_400_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(T, "resolve_identity", _never)
        resp = _client().post(_BASE + "/resolve", data={"name": "Jane Doe"})
        assert resp.status_code == 400


class TestSearchRouteExtremes:
    def test_a_two_hundred_character_cap_applies_to_the_typed_query(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(T, "search_name_candidates",
                            lambda q, company_hint=None: seen.update(q=q) or ([], None))
        _client().get("%s/search?q=%s" % (_BASE, "z" * 5000))
        assert len(seen["q"]) == 200

    def test_a_whitespace_only_query_never_touches_apollo(self, monkeypatch):
        monkeypatch.setattr(T, "search_name_candidates", _never)
        resp = _client().get(_BASE + "/search?q=%20%20")
        assert resp.get_json() == {"candidates": []}

    def test_a_missing_q_parameter_never_touches_apollo(self, monkeypatch):
        monkeypatch.setattr(T, "search_name_candidates", _never)
        assert _client().get(_BASE + "/search").get_json() == {"candidates": []}

    def test_a_non_latin_query_survives_url_encoding_intact(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(T, "search_name_candidates",
                            lambda q, company_hint=None: seen.update(q=q) or ([], None))
        _client().get(_BASE + "/search?q=%E4%B9%A0%E8%BF%91%E5%B9%B3")
        assert seen["q"] == "习近平"

    def test_repeated_searches_are_rate_limited(self, monkeypatch):
        monkeypatch.setattr(T, "search_name_candidates", lambda *a, **kw: ([], None))
        limit, _window = appmod._CPI_RATE_LIMITS["tli-search"]
        c = _client()
        for _ in range(limit):
            assert c.get(_BASE + "/search?q=jane").status_code == 200
        assert c.get(_BASE + "/search?q=jane").status_code == 429

    def test_the_rate_limit_is_per_user_not_global(self, monkeypatch):
        monkeypatch.setattr(T, "search_name_candidates", lambda *a, **kw: ([], None))
        limit, _window = appmod._CPI_RATE_LIMITS["tli-search"]
        first = _client(_OWNER)
        for _ in range(limit):
            first.get(_BASE + "/search?q=jane")
        assert first.get(_BASE + "/search?q=jane").status_code == 429
        assert _client(_OTHER).get(_BASE + "/search?q=jane").status_code == 200

    def test_an_apollo_crash_is_a_200_with_an_error_not_a_500(self, monkeypatch):
        monkeypatch.setattr(T, "search_name_candidates",
                            lambda *a, **kw: ([], {"code": "error", "message": "down"}))
        resp = _client().get(_BASE + "/search?q=jane")
        assert resp.status_code == 200
        assert resp.get_json()["error"]["code"] == "error"


class TestCollectRateLimitSharedBucket:
    def test_the_four_collect_phases_share_one_budget(self, monkeypatch):
        for route in _COLLECT_ROUTES:
            monkeypatch.setattr(T, _STARTERS[route], lambda run_id, email: True)
            monkeypatch.setattr(T, _JOBS[route], lambda run_id, email: None)
        limit, _window = appmod._CPI_RATE_LIMITS["tli-collect"]
        c = _client()
        for i in range(limit):
            route = _COLLECT_ROUTES[i % len(_COLLECT_ROUTES)]
            assert c.post("%s/runs/4/%s" % (_BASE, route)).status_code == 200
        assert c.post(_BASE + "/runs/4/collect").status_code == 429

    def test_a_rate_limited_collect_never_claims_the_run(self, monkeypatch):
        monkeypatch.setattr(T, "start_collecting", lambda run_id, email: True)
        monkeypatch.setattr(T, "collect_posts_job", lambda run_id, email: None)
        limit, _window = appmod._CPI_RATE_LIMITS["tli-collect"]
        c = _client()
        for _ in range(limit):
            c.post(_BASE + "/runs/4/collect")
        monkeypatch.setattr(T, "start_collecting", _never)
        assert c.post(_BASE + "/runs/4/collect").status_code == 429

    def test_a_rate_limited_resolve_never_creates_a_run_row(self, monkeypatch):
        monkeypatch.setattr(T, "create_run", lambda **kw: 1)
        monkeypatch.setattr(T, "resolve_identity", lambda *a, **kw: {
            "ok": False, "confidence": "none", "reasoning": "", "identity": None,
            "spend": {}, "error": None})
        monkeypatch.setattr(T, "save_result", lambda *a, **kw: True)
        limit, _window = appmod._CPI_RATE_LIMITS["tli-resolve"]
        c = _client()
        for _ in range(limit):
            c.post(_BASE + "/resolve", json={"name": "Jane Doe"})
        monkeypatch.setattr(T, "create_run", _never)
        assert c.post(_BASE + "/resolve", json={"name": "Jane Doe"}).status_code == 429


class TestConfirmRoute:
    def test_confirming_a_run_that_is_not_awaiting_review_is_a_404(self, monkeypatch):
        monkeypatch.setattr(T, "confirm_run", lambda run_id, email: False)
        resp = _client().post(_BASE + "/runs/1/confirm")
        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_confirming_twice_is_refused_the_second_time(self, monkeypatch):
        state = {"confirmed": False}

        def confirm(run_id, email):
            if state["confirmed"]:
                return False
            state["confirmed"] = True
            return True

        monkeypatch.setattr(T, "confirm_run", confirm)
        c = _client()
        assert c.post(_BASE + "/runs/1/confirm").status_code == 200
        assert c.post(_BASE + "/runs/1/confirm").status_code == 404

    def test_another_users_run_cannot_be_confirmed(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(T, "confirm_run",
                            lambda run_id, email: seen.update(email=email) or False)
        _client(_OTHER).post(_BASE + "/runs/1/confirm")
        assert seen["email"] == _OTHER


class TestPageRenderExtremes:
    def test_the_page_renders_with_no_runs_at_all(self, monkeypatch):
        monkeypatch.setattr(T, "list_runs", lambda email: [])
        assert _client().get(_BASE).status_code == 200

    def test_the_page_renders_when_the_store_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(T, "list_runs", lambda email: [])
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _client().get(_BASE).status_code == 200

    def test_a_run_with_a_non_latin_name_renders_without_a_unicode_error(self, monkeypatch):
        monkeypatch.setattr(T, "list_runs", lambda email: [
            {"id": 1, "input_name": "习近平 🙂", "status": "confirmed", "confidence": "high",
             "identity": {"full_name": "习近平"}, "created_at": "2026-09-01T00:00:00+00:00",
             "posts_status": "idle", "reaction_status": "idle", "press_status": "idle",
             "synthesis_status": "idle"}])
        resp = _client().get(_BASE)
        assert resp.status_code == 200

    def test_a_run_name_containing_html_is_not_rendered_as_markup(self, monkeypatch):
        monkeypatch.setattr(T, "list_runs", lambda email: [
            {"id": 1, "input_name": "<script>alert(1)</script>", "status": "confirmed",
             "confidence": "high", "identity": {"full_name": "<img onerror=x>"},
             "created_at": "2026-09-01T00:00:00+00:00", "posts_status": "idle",
             "reaction_status": "idle", "press_status": "idle", "synthesis_status": "idle"}])
        body = _client().get(_BASE).data.decode("utf-8")
        assert "<script>alert(1)</script>" not in body

    def test_the_page_never_bills_a_lookup_on_load(self, monkeypatch):
        monkeypatch.setattr(T, "list_runs", lambda email: [])
        monkeypatch.setattr(T, "resolve_identity", _never)
        monkeypatch.setattr(T, "search_name_candidates", _never)
        for name in ("collect_posts_job", "collect_reaction_job", "collect_press_job",
                     "collect_synthesis_job"):
            monkeypatch.setattr(T, name, _never)
        assert _client().get(_BASE).status_code == 200
