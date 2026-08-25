"""tracker/sci_pipeline.py -- Phase 2 additions: the facebook/tiktok/x
registry dispatch and, most importantly, LinkedIn's feature-flag contract.
LinkedIn is the platform most exposed to scraping-detection/ToS enforcement,
so the requirement is strict: an unset SCI_APIFY_LINKEDIN_ACTOR_ID must never
reach apify_transport (no network call, no retry storm against a fragile
actor) and must degrade that one platform to scrape_failed without touching
any other platform's row.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_pipeline  # noqa: E402


def test_collect_linkedin_raises_without_calling_apify_when_actor_id_unset(monkeypatch):
    monkeypatch.delenv("SCI_APIFY_LINKEDIN_ACTOR_ID", raising=False)
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    with patch("tracker.apify_transport.run_actor_and_wait") as mock_run:
        try:
            sci_pipeline._collect_linkedin("acmeco")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "disabled" in str(e).lower()
        assert mock_run.call_count == 0


def test_collect_linkedin_raises_without_calling_apify_when_token_unset(monkeypatch):
    monkeypatch.setenv("SCI_APIFY_LINKEDIN_ACTOR_ID", "some/actor")
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    with patch("tracker.apify_transport.run_actor_and_wait") as mock_run:
        try:
            sci_pipeline._collect_linkedin("acmeco")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "apify_api_token" in str(e).lower()
        assert mock_run.call_count == 0


def test_collect_linkedin_calls_apify_once_when_fully_configured(monkeypatch):
    monkeypatch.setenv("SCI_APIFY_LINKEDIN_ACTOR_ID", "some/actor")
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    with patch("tracker.apify_transport.run_actor_and_wait") as mock_run:
        mock_run.return_value = []
        sci_pipeline._collect_linkedin("acmeco")
        assert mock_run.call_count == 1


def test_run_platform_collection_marks_linkedin_scrape_failed_without_a_transport_call(monkeypatch):
    monkeypatch.delenv("SCI_APIFY_LINKEDIN_ACTOR_ID", raising=False)
    from tracker import sci_store
    calls = []
    monkeypatch.setattr(sci_store, "upsert_platform_run",
                        lambda run_id, platform, **kw: calls.append((platform, kw)))
    monkeypatch.setattr(sci_store, "upsert_posts", lambda *a, **k: 0)
    with patch("tracker.apify_transport.run_actor_and_wait") as mock_run:
        sci_pipeline.run_platform_collection(1, "linkedin", "acmeco")
        assert mock_run.call_count == 0
    terminal = [kw for platform, kw in calls if platform == "linkedin" and kw.get("status")]
    assert terminal
    assert terminal[-1]["status"] == "scrape_failed"
    assert "disabled" in terminal[-1]["status_detail"].lower()


def test_a_linkedin_scrape_failure_does_not_retry(monkeypatch):
    """No loop anywhere calls apify_transport more than once per collection
    attempt -- a strict failure surfaces immediately as scrape_failed."""
    monkeypatch.setenv("SCI_APIFY_LINKEDIN_ACTOR_ID", "some/actor")
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    from tracker import sci_store, apify_transport
    calls = []
    monkeypatch.setattr(sci_store, "upsert_platform_run",
                        lambda run_id, platform, **kw: calls.append((platform, kw)))
    monkeypatch.setattr(sci_store, "upsert_posts", lambda *a, **k: 0)
    with patch("tracker.apify_transport.run_actor_and_wait") as mock_run:
        mock_run.side_effect = apify_transport.ApifyTransportError("actor blocked")
        sci_pipeline.run_platform_collection(1, "linkedin", "acmeco")
        assert mock_run.call_count == 1
    terminal = [kw for platform, kw in calls if platform == "linkedin" and kw.get("status")]
    assert terminal[-1]["status"] == "scrape_failed"


def test_facebook_tiktok_x_all_dispatch_through_the_apify_registry(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "upsert_posts", lambda *a, **k: 0)
    for platform, module_name in (("facebook", "sci_source_facebook"),
                                  ("tiktok", "sci_source_tiktok"),
                                  ("x", "sci_source_x")):
        with patch(f"tracker.{module_name}.apify_transport.run_actor_and_wait") as mock_run:
            mock_run.return_value = []
            sci_pipeline.run_platform_collection(1, platform, "handle")
            assert mock_run.call_count == 1, f"{platform} did not dispatch through apify_transport"
