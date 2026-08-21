"""/p2/b2b-agents/linkedin-strategy-researcher (page + search/analyze/history/
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
    resp = _client("someone@position2.com").get("/p2/b2b-agents/linkedin-strategy-researcher")
    assert resp.status_code == 200
    assert b"LinkedIn Strategy Researcher" in resp.data


def test_page_requires_login():
    c = appmod.app.test_client()
    resp = c.get("/p2/b2b-agents/linkedin-strategy-researcher", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


# ── Search ────────────────────────────────────────────────────────────────

def test_search_returns_companies_from_the_arena_client(monkeypatch):
    monkeypatch.setattr(arena_client, "search_companies", lambda q: [{"id": "1", "name": "Acme"}])
    resp = _client().get("/p2/b2b-agents/linkedin-strategy-researcher/search?q=Acme")
    assert resp.status_code == 200
    assert resp.get_json()["companies"] == [{"id": "1", "name": "Acme"}]


def test_search_with_no_query_returns_an_empty_list_without_calling_arena(monkeypatch):
    called = []
    monkeypatch.setattr(arena_client, "search_companies", lambda q: called.append(q) or [])
    resp = _client().get("/p2/b2b-agents/linkedin-strategy-researcher/search")
    assert resp.get_json()["companies"] == []
    assert called == []


def test_search_degrades_to_an_empty_list_without_a_configured_key(monkeypatch):
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    resp = _client().get("/p2/b2b-agents/linkedin-strategy-researcher/search?q=Acme")
    assert resp.status_code == 200
    assert resp.get_json()["companies"] == []


# ── Analyze ───────────────────────────────────────────────────────────────

def test_analyze_requires_company_id_and_name():
    resp = _client().post("/p2/b2b-agents/linkedin-strategy-researcher/analyze", json={"mode": "OWN"})
    assert resp.status_code == 400


def test_analyze_own_brand_starts_a_run_and_returns_its_id(monkeypatch):
    monkeypatch.setattr(lps_store, "save_run", lambda *a, **k: 42)
    resp = _client().post("/p2/b2b-agents/linkedin-strategy-researcher/analyze",
                          json={"company_id": "c1", "company_name": "Acme", "mode": "OWN"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["run_id"] == 42 and body["status"] == "running"


def test_analyze_competitor_requires_a_completed_own_brand_parent(monkeypatch):
    monkeypatch.setattr(lps_store, "get_run", lambda run_id, email: None)
    resp = _client().post("/p2/b2b-agents/linkedin-strategy-researcher/analyze",
                          json={"company_id": "c2", "company_name": "Globex",
                                "mode": "COMPETITOR", "parent_run_id": "1"})
    assert resp.status_code == 404


def test_analyze_competitor_refuses_a_parent_run_that_is_not_yet_complete(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, status="running"))
    resp = _client().post("/p2/b2b-agents/linkedin-strategy-researcher/analyze",
                          json={"company_id": "c2", "company_name": "Globex",
                                "mode": "COMPETITOR", "parent_run_id": "1"})
    assert resp.status_code == 404


def test_analyze_competitor_cannot_use_someone_elses_run_as_the_parent(monkeypatch):
    """The parent-run lookup goes through the same ownership-scoped get_run as
    everything else -- a stranger's run id is indistinguishable from a
    nonexistent one."""
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER, status="complete"))
    resp = _client(_OWNER).post("/p2/b2b-agents/linkedin-strategy-researcher/analyze",
                                json={"company_id": "c2", "company_name": "Globex",
                                      "mode": "COMPETITOR", "parent_run_id": "1"})
    assert resp.status_code == 404


def test_analyze_starts_running_even_without_a_configured_key(monkeypatch):
    """The background job itself degrades gracefully (see test_arena_client.py) --
    starting it must not be blocked just because ARENA_API_KEY happens to be unset."""
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    monkeypatch.setattr(lps_store, "save_run", lambda *a, **k: 1)
    resp = _client().post("/p2/b2b-agents/linkedin-strategy-researcher/analyze",
                          json={"company_id": "c1", "company_name": "Acme", "mode": "OWN"})
    assert resp.status_code == 200


# ── Background analysis job: AI enrichment is additive, never blocking ─────
# _lps_run_analysis_job runs off-thread in production, but it's a plain
# function -- called directly here (bypassing threading.Thread) so these
# tests are synchronous and deterministic. The one thing under test: an
# extra Claude synthesis pass (tracker/lps_enrichment.py) must never be able
# to stop a run that the vendor's own analysis already completed
# successfully, whether enrichment is unavailable, returns nothing, or
# outright raises.

from tracker import lps_enrichment  # noqa: E402


def _fake_vendor_output():
    return {
        "getcompanyprofile.name": "Acme",
        "messagingagent.summary": "A summary.",
        "competitiveagent.scorecardOverall": 5,
    }


def test_analysis_job_merges_enrichment_fields_into_the_saved_output(monkeypatch):
    monkeypatch.setattr(arena_client, "run_analysis", lambda *a, **k: _fake_vendor_output())
    monkeypatch.setattr(lps_enrichment, "enrich_run", lambda output, name, run_type: {
        "headline": "H", "synthesis": "S", "topActions": ["do this"], "coverage": "C",
    })
    saved = {}

    def _capture_update(run_id, status, **kwargs):
        saved["status"] = status
        saved.update(kwargs)

    monkeypatch.setattr(lps_store, "update_run_status", _capture_update)
    appmod._lps_run_analysis_job(1, "Acme", "c1", _OWNER, "OWN", "")

    assert saved["status"] == "complete"
    assert saved["output"]["aienrichment.headline"] == "H"
    assert saved["output"]["aienrichment.synthesis"] == "S"
    assert saved["output"]["aienrichment.topActions"] == ["do this"]
    assert saved["output"]["aienrichment.coverage"] == "C"
    # the vendor's own fields are untouched
    assert saved["output"]["getcompanyprofile.name"] == "Acme"


def test_analysis_job_completes_normally_when_enrichment_returns_none(monkeypatch):
    monkeypatch.setattr(arena_client, "run_analysis", lambda *a, **k: _fake_vendor_output())
    monkeypatch.setattr(lps_enrichment, "enrich_run", lambda *a, **k: None)
    saved = {}
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda run_id, status, **kwargs: saved.update(status=status, **kwargs))
    appmod._lps_run_analysis_job(1, "Acme", "c1", _OWNER, "OWN", "")

    assert saved["status"] == "complete"
    assert "aienrichment.headline" not in saved["output"]


def test_analysis_job_completes_normally_when_enrichment_raises(monkeypatch):
    """The one case this feature exists to guard against: a bug in the new
    enrichment call must not turn a perfectly good vendor analysis into a
    failed run."""
    monkeypatch.setattr(arena_client, "run_analysis", lambda *a, **k: _fake_vendor_output())

    def _boom(*a, **k):
        raise RuntimeError("enrichment blew up")

    monkeypatch.setattr(lps_enrichment, "enrich_run", _boom)
    saved = {}
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda run_id, status, **kwargs: saved.update(status=status, **kwargs))
    appmod._lps_run_analysis_job(1, "Acme", "c1", _OWNER, "OWN", "")

    assert saved["status"] == "complete"
    assert saved["output"]["getcompanyprofile.name"] == "Acme"


def test_analysis_job_never_calls_enrichment_when_the_vendor_analysis_itself_fails(monkeypatch):
    monkeypatch.setattr(arena_client, "run_analysis", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(lps_enrichment, "enrich_run", lambda *a, **k: called.append(1))
    saved = {}
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda run_id, status, **kwargs: saved.update(status=status, **kwargs))
    appmod._lps_run_analysis_job(1, "Acme", "c1", _OWNER, "OWN", "")

    assert not called
    assert saved["status"] == "error"


# ── Run status / detail / history ────────────────────────────────────────

def test_run_status_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1/status")
    assert resp.status_code == 404


def test_run_status_returns_the_status_for_the_owning_user(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, status="running"))
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "running"


def test_run_detail_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    monkeypatch.setattr(lps_store, "get_children", lambda *a, **k: [])
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1")
    assert resp.status_code == 404


def test_run_detail_returns_the_full_run_for_its_owner(monkeypatch):
    _owner_scoped_get_run(monkeypatch)
    monkeypatch.setattr(lps_store, "get_children", lambda *a, **k: [])
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1")
    assert resp.status_code == 200
    assert resp.get_json()["company_name"] == "Acme"


def test_history_only_reflects_the_calling_users_own_email(monkeypatch):
    seen_emails = []

    def fake_list_runs(email, limit=100):
        seen_emails.append(email)
        return [_run()] if email == _OWNER else []

    monkeypatch.setattr(lps_store, "list_runs", fake_list_runs)
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-strategy-researcher/history")
    assert resp.status_code == 200
    assert len(resp.get_json()["runs"]) == 1
    assert seen_emails == [_OWNER]


# ── Playbook ──────────────────────────────────────────────────────────────

def test_playbook_get_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1/playbook")
    assert resp.status_code == 404


def test_playbook_get_returns_none_when_not_yet_generated(monkeypatch):
    _owner_scoped_get_run(monkeypatch)
    monkeypatch.setattr(lps_store, "get_playbook", lambda *a, **k: None)
    resp = _client(_OWNER).get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1/playbook")
    assert resp.status_code == 200
    assert resp.get_json()["playbook"] is None


def test_playbook_post_404s_for_a_run_that_belongs_to_someone_else(monkeypatch):
    """The generation trigger is ownership-checked exactly like every read --
    a stranger cannot spend this account's Arena credits generating a
    playbook for a run they don't own."""
    _owner_scoped_get_run(monkeypatch, _run(run_id=1, email=_OTHER))
    resp = _client(_OWNER).post("/p2/b2b-agents/linkedin-strategy-researcher/runs/1/playbook",
                                json={"mode": "OWN"})
    assert resp.status_code == 404


def test_playbook_post_starts_generation_for_the_runs_owner(monkeypatch):
    _owner_scoped_get_run(monkeypatch)
    resp = _client(_OWNER).post("/p2/b2b-agents/linkedin-strategy-researcher/runs/1/playbook",
                                json={"mode": "OWN"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "running"


# ── Old slug redirects ───────────────────────────────────────────────────────
# This agent briefly launched as "LinkedIn Playbook Studio" at
# /p2/b2b-agents/linkedin-playbook-studio before being renamed the same day.
# 308 (not 301) so a still-open tab's POST to /analyze or /playbook keeps its
# body instead of the browser silently retrying it as a bodyless GET.

def test_the_old_slug_root_redirects(monkeypatch):
    resp = _client().get("/p2/b2b-agents/linkedin-playbook-studio", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["Location"].endswith("/p2/b2b-agents/linkedin-strategy-researcher")


def test_the_old_slug_preserves_query_string_on_redirect(monkeypatch):
    resp = _client().get("/p2/b2b-agents/linkedin-playbook-studio/search?q=Acme", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["Location"].endswith(
        "/p2/b2b-agents/linkedin-strategy-researcher/search?q=Acme")


def test_the_old_slug_preserves_method_and_body_on_redirect(monkeypatch):
    """A 301 here would let the browser downgrade this to a bodyless GET,
    silently dropping the analysis request -- the same reasoning as the
    /p2/gtm legacy redirect."""
    resp = _client().post("/p2/b2b-agents/linkedin-playbook-studio/analyze",
                          json={"company_id": "c1", "company_name": "Acme", "mode": "OWN"},
                          follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["Location"].endswith("/p2/b2b-agents/linkedin-strategy-researcher/analyze")


def test_the_old_slug_redirects_deep_sub_paths_too(monkeypatch):
    resp = _client().get("/p2/b2b-agents/linkedin-playbook-studio/runs/1/playbook",
                         follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["Location"].endswith(
        "/p2/b2b-agents/linkedin-strategy-researcher/runs/1/playbook")


# ── Read-time augmentation: derived analytics + text repair ────────────────
# tracker/lps_analytics.py is applied when a run is READ, not frozen into the
# stored blob at analysis time, so every run already in the database gains the
# computed sections without re-running a multi-minute vendor workflow. These
# tests pin that placement: the route's response must carry derived.* keys and
# repaired text even though the stored output has neither.

_MOJI_QUOTE = "’".encode("utf-8").decode("latin-1")


def _run_with_posts(run_id=1, email=_OWNER):
    run = _run(run_id=run_id, email=email)
    run["output"] = {
        "getcompanyprofile.followers_count": 1000,
        "getcompanyprofile.employee_count": 100,
        "getcompanypost.items": [
            {"parsed_datetime": "2026-08-10T09:00:00.000Z",
             "text": "we" + _MOJI_QUOTE + "re shipping",
             "reaction_counter": 40, "comment_counter": 8, "repost_counter": 2,
             "attachments": [{"type": "video", "url": "https://x/v"}],
             "author": {"name": "Acme", "is_company": True}},
            {"parsed_datetime": "2026-08-17T09:00:00.000Z", "text": "second post",
             "reaction_counter": 10, "comment_counter": 0, "repost_counter": 0,
             "attachments": [], "author": {"name": "Acme", "is_company": True}},
        ],
    }
    return run


def test_run_detail_adds_computed_analytics_that_the_stored_output_lacks(monkeypatch):
    run = _run_with_posts()
    stored_keys = sorted(run["output"])
    _owner_scoped_get_run(monkeypatch, run)
    monkeypatch.setattr(lps_store, "get_children", lambda *a, **k: [])
    # Nothing is written back: the computed view is per-request, so an
    # improvement to lps_analytics applies to the whole history on next read.
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda *a, **k: pytest.fail("read path must not persist"))

    body = _client().get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1").get_json()

    assert not [k for k in stored_keys if k.startswith("derived.")], \
        "the stored blob is vendor-only before the request"
    assert body["output"]["derived.activity"]["postsAnalyzed"] == 2
    assert body["output"]["derived.engagement"]["avgTotal"] == 30
    assert body["output"]["derived.insights"]


def test_run_detail_repairs_mojibake_in_post_text(monkeypatch):
    """The vendor double-decodes text before sending it, so post bodies arrive
    as "we<a-hat-euro-TM>re". The report was rendering that verbatim."""
    _owner_scoped_get_run(monkeypatch, _run_with_posts())
    monkeypatch.setattr(lps_store, "get_children", lambda *a, **k: [])

    body = _client().get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1").get_json()
    assert body["output"]["getcompanypost.items"][0]["text"] == "we’re shipping"


def test_run_detail_survives_an_output_that_is_not_a_dict(monkeypatch):
    run = _run()
    run["output"] = None
    _owner_scoped_get_run(monkeypatch, run)
    monkeypatch.setattr(lps_store, "get_children", lambda *a, **k: [])

    resp = _client().get("/p2/b2b-agents/linkedin-strategy-researcher/runs/1")
    assert resp.status_code == 200
    assert resp.get_json()["output"] is None


def test_run_detail_still_scopes_by_owner_after_augmentation(monkeypatch):
    """Augmentation runs after the ownership-scoped lookup, so it must not
    become a way to read someone else's run."""
    _owner_scoped_get_run(monkeypatch, _run_with_posts(email=_OTHER))
    monkeypatch.setattr(lps_store, "get_children", lambda *a, **k: [])

    assert _client().get(
        "/p2/b2b-agents/linkedin-strategy-researcher/runs/1").status_code == 404


# ── The analysis job's own two contracts ───────────────────────────────────

def test_analysis_job_derives_the_summary_column_from_the_summary_object(monkeypatch):
    """Every real run returns messagingagent.summary as an OBJECT, never a
    bare string, so an isinstance(str) check discarded it every time and the
    history table's Summary column showed a dash for every row."""
    monkeypatch.setattr(arena_client, "run_analysis", lambda *a, **k: {
        "messagingagent.summary": {"text": "The real summary.", "moves": ["m"]},
    })
    monkeypatch.setattr(lps_enrichment, "enrich_run", lambda *a, **k: None)
    saved = {}
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda run_id, status, **kwargs: saved.update(status=status, **kwargs))
    appmod._lps_run_analysis_job(1, "Acme", "c1", _OWNER, "OWN", "")

    assert saved["summary"] == "The real summary."


def test_analysis_job_hands_enrichment_the_computed_metrics(monkeypatch):
    """Claude is given the augmented view so cadence and format performance
    arrive as finished numbers rather than 100 raw posts to do arithmetic on."""
    monkeypatch.setattr(arena_client, "run_analysis",
                        lambda *a, **k: _run_with_posts()["output"])
    seen = {}

    def _capture(output, name, run_type):
        seen["keys"] = [k for k in output if k.startswith("derived.")]
        return None

    monkeypatch.setattr(lps_enrichment, "enrich_run", _capture)
    monkeypatch.setattr(lps_store, "update_run_status", lambda *a, **k: None)
    appmod._lps_run_analysis_job(1, "Acme", "c1", _OWNER, "OWN", "")

    assert "derived.engagement" in seen["keys"]


def test_analysis_job_merges_the_newer_enrichment_list_fields(monkeypatch):
    monkeypatch.setattr(arena_client, "run_analysis", lambda *a, **k: _fake_vendor_output())
    monkeypatch.setattr(lps_enrichment, "enrich_run", lambda *a, **k: {
        "headline": "H", "synthesis": "S", "topActions": ["a"],
        "strengths": ["s"], "risks": ["r"], "contentAngles": ["c"],
    })
    saved = {}
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda run_id, status, **kwargs: saved.update(status=status, **kwargs))
    appmod._lps_run_analysis_job(1, "Acme", "c1", _OWNER, "OWN", "")

    assert saved["output"]["aienrichment.strengths"] == ["s"]
    assert saved["output"]["aienrichment.risks"] == ["r"]
    assert saved["output"]["aienrichment.contentAngles"] == ["c"]
    assert "aienrichment.coverage" not in saved["output"]


# ── On-demand AI Insights backfill ─────────────────────────────────────────
# The synthesis is generated at analysis time and stored, so runs that
# completed before that feature shipped have no aienrichment.* keys and can
# never grow them on a read (unlike derived.*, which is recomputed every
# time). This route makes the one Claude call against the vendor output that
# is already saved, instead of re-running the multi-minute vendor workflow.

_INSIGHTS_URL = "/p2/b2b-agents/linkedin-strategy-researcher/runs/1/insights"


def _enrichment():
    return {"headline": "H", "synthesis": "S", "topActions": ["a"], "coverage": "C"}


def test_insights_cannot_be_generated_for_someone_elses_run(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _owner_scoped_get_run(monkeypatch, _run(email=_OTHER))
    assert _client().post(_INSIGHTS_URL).status_code == 404


def test_insights_require_login(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _owner_scoped_get_run(monkeypatch)
    resp = appmod.app.test_client().post(_INSIGHTS_URL)
    assert resp.status_code in (302, 401, 403)


def test_insights_refuse_a_run_that_has_not_finished(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _owner_scoped_get_run(monkeypatch, _run(status="running"))
    resp = _client().post(_INSIGHTS_URL)
    assert resp.status_code == 409


def test_insights_report_a_clear_error_when_no_key_is_configured(monkeypatch):
    """A 503 with a readable message, not a background thread that silently
    does nothing and leaves the UI polling forever."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _owner_scoped_get_run(monkeypatch)
    resp = _client().post(_INSIGHTS_URL)
    assert resp.status_code == 503
    assert "error" in resp.get_json()


def test_insights_start_a_background_job(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _owner_scoped_get_run(monkeypatch)
    started = []
    monkeypatch.setattr(appmod.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda s: started.append(k)})())
    resp = _client().post(_INSIGHTS_URL)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "running"
    assert started[0]["args"] == (1, _OWNER)


def test_insights_job_merges_the_synthesis_into_the_stored_output(monkeypatch):
    run = _run_with_posts()
    run["output"]["strategyagent.strategy"] = "vendor text"
    _owner_scoped_get_run(monkeypatch, run)
    monkeypatch.setattr(lps_enrichment, "enrich_run", lambda *a, **k: _enrichment())
    saved = {}
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda run_id, status, **kwargs: saved.update(status=status, **kwargs))
    appmod._lps_run_insights_job(1, _OWNER)

    assert saved["status"] == "complete"
    assert saved["output"]["aienrichment.headline"] == "H"
    assert saved["output"]["aienrichment.coverage"] == "C"
    assert saved["output"]["strategyagent.strategy"] == "vendor text"


def test_insights_job_does_not_persist_the_recomputed_metrics(monkeypatch):
    """derived.* is handed to Claude but must stay out of the stored blob, so
    later improvements to lps_analytics keep applying retroactively instead of
    being frozen into whichever runs happened to be enriched."""
    _owner_scoped_get_run(monkeypatch, _run_with_posts())
    monkeypatch.setattr(lps_enrichment, "enrich_run", lambda *a, **k: _enrichment())
    saved = {}
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda run_id, status, **kwargs: saved.update(status=status, **kwargs))
    appmod._lps_run_insights_job(1, _OWNER)

    assert not [k for k in saved["output"] if k.startswith("derived.")]


def test_insights_job_hands_claude_the_computed_metrics(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run_with_posts())
    seen = {}

    def _capture(output, name, run_type):
        seen["derived"] = [k for k in output if k.startswith("derived.")]
        seen["name"] = name
        return None

    monkeypatch.setattr(lps_enrichment, "enrich_run", _capture)
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda *a, **k: pytest.fail("nothing to save"))
    appmod._lps_run_insights_job(1, _OWNER)

    assert "derived.engagement" in seen["derived"]
    assert seen["name"] == "Acme"


def test_insights_job_saves_nothing_when_the_synthesis_comes_back_empty(monkeypatch):
    _owner_scoped_get_run(monkeypatch, _run_with_posts())
    monkeypatch.setattr(lps_enrichment, "enrich_run", lambda *a, **k: None)
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda *a, **k: pytest.fail("must not write an empty synthesis"))
    appmod._lps_run_insights_job(1, _OWNER)


def test_insights_job_never_raises_when_the_claude_call_blows_up(monkeypatch):
    """It runs on a daemon thread with nothing to catch it, and the run it is
    enriching is already complete and correct."""
    _owner_scoped_get_run(monkeypatch, _run_with_posts())

    def _boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(lps_enrichment, "enrich_run", _boom)
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda *a, **k: pytest.fail("nothing should be written"))
    appmod._lps_run_insights_job(1, _OWNER)


def test_insights_job_refuses_a_run_that_is_not_the_callers(monkeypatch):
    """The job re-reads through the ownership-scoped get_run rather than
    trusting the run_id it was handed."""
    _owner_scoped_get_run(monkeypatch, _run_with_posts(email=_OTHER))
    monkeypatch.setattr(lps_enrichment, "enrich_run",
                        lambda *a, **k: pytest.fail("must not enrich another user's run"))
    monkeypatch.setattr(lps_store, "update_run_status",
                        lambda *a, **k: pytest.fail("must not write"))
    appmod._lps_run_insights_job(1, _OWNER)
