"""/p2/b2b-agents/social-creative-intelligence (page + analyze/status/run).
Every data route is @position2_required and scopes reads by the
server-verified session email, never a client-supplied one -- same pattern
as tests/test_linkedin_playbook_studio_route.py, which this mirrors.

Store functions are monkeypatched at the module level rather than hitting a
real Postgres (see tests/test_sci_store.py for that layer's own
ownership-scoping tests) -- this file is about the route/decorator/HTTP-shape
behavior, and about proving the background job's per-platform fault
isolation without spinning up a real thread.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import sci_store, sci_pipeline  # noqa: E402

_OWNER = "owner@position2.com"
_OTHER = "other@position2.com"


def _client(email=_OWNER):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


def _run(run_id=1, email=_OWNER, status="running"):
    return {"id": run_id, "email": email, "company_name": "Acme Inc", "company_url": None,
           "status": status, "error": None, "identify_result": None, "synthesis": None,
           "created_at": "2026-08-25T00:00:00", "updated_at": "2026-08-25T00:00:00"}


def _owner_scoped_get_run(monkeypatch, run=None):
    run = run or _run()

    def fake_get_run(run_id, email):
        if run_id == run["id"] and email == run["email"]:
            return run
        return None

    monkeypatch.setattr(sci_store, "get_run", fake_get_run)
    return run


# ── Login / access ───────────────────────────────────────────────────────────

def test_page_renders_for_any_position2_staff(monkeypatch):
    monkeypatch.setattr(sci_store, "list_runs", lambda email: [])
    resp = _client("someone@position2.com").get("/p2/b2b-agents/social-creative-intelligence")
    assert resp.status_code == 200
    assert b"Social Creative Intelligence Analyst" in resp.data


def test_page_requires_login():
    c = appmod.app.test_client()
    resp = c.get("/p2/b2b-agents/social-creative-intelligence", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_non_position2_email_is_bounced_to_app():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "outsider@gmail.com", "name": "T"}
    resp = c.get("/p2/b2b-agents/social-creative-intelligence", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("Location", "").rstrip("/").endswith("/app")


def test_analyze_requires_login():
    c = appmod.app.test_client()
    resp = c.post("/p2/b2b-agents/social-creative-intelligence/analyze",
                  json={"company_name": "Acme"}, follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


# ── /analyze ──────────────────────────────────────────────────────────────

def test_analyze_requires_a_company_name(monkeypatch):
    resp = _client().post("/p2/b2b-agents/social-creative-intelligence/analyze", json={})
    assert resp.status_code == 400


def test_analyze_starts_running_even_without_any_vendor_keys_configured(monkeypatch):
    """Starting the job must never be blocked just because APIFY_API_TOKEN /
    YOUTUBE_API_KEY / ANTHROPIC_API_KEY happen to be unset -- the job itself
    degrades gracefully once it actually runs (see test_sci_identify.py,
    test_sci_source_instagram.py). The background thread target is
    monkeypatched to a no-op here so the test doesn't depend on a real
    thread's timing."""
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sci_store, "save_run", lambda *a, **k: 1)
    monkeypatch.setattr(sci_pipeline, "_sci_run_analysis_job", lambda *a, **k: None)
    resp = _client().post("/p2/b2b-agents/social-creative-intelligence/analyze",
                          json={"company_name": "Acme Inc"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["run_id"] == 1
    assert body["status"] == "running"


def test_analyze_returns_500_when_the_run_cannot_be_saved(monkeypatch):
    monkeypatch.setattr(sci_store, "save_run", lambda *a, **k: None)
    resp = _client().post("/p2/b2b-agents/social-creative-intelligence/analyze",
                          json={"company_name": "Acme Inc"})
    assert resp.status_code == 500


# ── Ownership scoping ────────────────────────────────────────────────────────

def test_run_status_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    resp = _client(_OWNER).get("/p2/b2b-agents/social-creative-intelligence/runs/1/status")
    assert resp.status_code == 404


def test_run_status_404s_for_a_nonexistent_run(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OWNER))
    resp = _client(_OWNER).get("/p2/b2b-agents/social-creative-intelligence/runs/999/status")
    assert resp.status_code == 404


def test_run_status_200s_and_includes_platforms_for_the_owner(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OWNER, status="done"))
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [
        {"platform": "instagram", "status": "ok", "post_count": 12},
        {"platform": "youtube", "status": "no_presence", "post_count": 0},
    ])
    resp = _client(_OWNER).get("/p2/b2b-agents/social-creative-intelligence/runs/1/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "done"
    assert len(body["platforms"]) == 2


def test_run_detail_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    resp = _client(_OWNER).get("/p2/b2b-agents/social-creative-intelligence/runs/1")
    assert resp.status_code == 404


def test_run_detail_200s_with_platforms_and_posts_for_the_owner(monkeypatch):
    run = _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OWNER, status="done"))
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: [])
    resp = _client(_OWNER).get("/p2/b2b-agents/social-creative-intelligence/runs/1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["company_name"] == "Acme Inc"
    assert body["platforms"] == []
    assert body["posts"] == []


# ── Background job: per-platform fault isolation, run synchronously ────────

def test_one_platform_failing_does_not_stop_the_others_from_completing(monkeypatch):
    """The literal thread target, called directly (bypassing threading.Thread
    entirely) so the test is deterministic. instagram fails outright;
    youtube must still run and the run must still reach status='done'."""
    calls = []

    monkeypatch.setattr(sci_pipeline, "run_identify", lambda run_id, name, url: {
        "instagram": {"handle": "acme", "confidence": "high", "profile_url": None, "reasoning": ""},
        "youtube": {"handle": "AcmeOfficial", "confidence": "medium", "profile_url": None, "reasoning": ""},
        "linkedin": {"handle": None, "confidence": "none", "profile_url": None, "reasoning": "not found"},
        "x": {"handle": None, "confidence": "none", "profile_url": None, "reasoning": "not found"},
        "tiktok": {"handle": None, "confidence": "none", "profile_url": None, "reasoning": "not found"},
        "facebook": {"handle": None, "confidence": "none", "profile_url": None, "reasoning": "not found"},
    })

    def fake_collection(run_id, platform, handle):
        calls.append(("collect", platform))
        if platform == "instagram":
            raise RuntimeError("actor blocked")

    def fake_analysis(run_id, platform):
        calls.append(("analyze", platform))

    statuses = {}

    def fake_upsert(run_id, platform, **fields):
        statuses.setdefault(platform, {}).update(fields)

    monkeypatch.setattr(sci_pipeline, "run_platform_collection", fake_collection)
    monkeypatch.setattr(sci_pipeline, "run_platform_creative_analysis", fake_analysis)
    monkeypatch.setattr(sci_store, "upsert_platform_run", fake_upsert)

    final_status = {}
    monkeypatch.setattr(sci_store, "update_run_status",
                        lambda run_id, status, **k: final_status.update(status=status, **k))

    sci_pipeline._sci_run_analysis_job(1, _OWNER, "Acme Inc", None)

    assert ("collect", "instagram") in calls
    assert ("collect", "youtube") in calls
    assert ("analyze", "youtube") in calls
    # instagram's failure must not have prevented youtube's analysis step
    assert ("analyze", "instagram") not in calls  # never reached -- collection raised first
    assert statuses["instagram"]["status"] == "error"
    assert final_status["status"] == "done"


def test_the_whole_job_reports_error_status_on_a_totally_uncaught_failure(monkeypatch):
    monkeypatch.setattr(sci_pipeline, "run_identify",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    final_status = {}
    monkeypatch.setattr(sci_store, "update_run_status",
                        lambda run_id, status, **k: final_status.update(status=status, **k))
    sci_pipeline._sci_run_analysis_job(1, _OWNER, "Acme Inc", None)
    assert final_status["status"] == "error"
