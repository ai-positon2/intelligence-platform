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


# --- YouTube must not be hostage to the identify step -------------------
#
# identify is a single API call covering all six platforms, so when it fails
# it fails for all six at once and the whole run returns nothing. YouTube is
# the one platform that needs no scraper and no identify step: it has a
# sanctioned search API of its own.

def _all_none():
    """Exactly what identify_handles() returns when it fails outright: all
    six platforms 'none', carrying the real production error string."""
    from tracker import sci_identify
    return {p: {"handle": None, "profile_url": None, "confidence": "none",
                "reasoning": "The identification step returned an unreadable response."}
            for p in sci_identify.PLATFORMS}


def test_youtube_falls_back_to_the_data_api_when_identify_returns_nothing(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    result = _all_none()
    with patch("tracker.sci_youtube_client.resolve_company_channel") as mock_resolve:
        mock_resolve.return_value = {"channel_id": "UC" + "a" * 22, "title": "Position2",
                                     "handle": "@position2",
                                     "profile_url": "https://www.youtube.com/@position2"}
        sci_pipeline._apply_youtube_fallback(result, "Position2")
    assert result["youtube"]["handle"] == "@position2"
    assert result["youtube"]["confidence"] in sci_pipeline._USABLE_CONFIDENCE
    # ...and it says plainly where the answer came from.
    assert "YouTube Data API" in result["youtube"]["reasoning"]


def test_youtube_fallback_leaves_the_other_five_platforms_alone(monkeypatch):
    """The refuse-to-guess contract still holds everywhere a scraper is the
    only alternative -- only YouTube has an authoritative lookup."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    result = _all_none()
    with patch("tracker.sci_youtube_client.resolve_company_channel") as mock_resolve:
        mock_resolve.return_value = {"channel_id": "UC" + "a" * 22, "title": "P2",
                                     "handle": "@p2", "profile_url": "https://youtube.com/@p2"}
        sci_pipeline._apply_youtube_fallback(result, "Position2")
    for platform in ("instagram", "linkedin", "x", "tiktok", "facebook"):
        assert result[platform]["handle"] is None
        assert result[platform]["confidence"] == "none"


def test_youtube_fallback_does_not_override_a_successful_identification(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    result = _all_none()
    result["youtube"] = {"handle": "@verified", "profile_url": "https://youtube.com/@verified",
                         "confidence": "high", "reasoning": "Verified via the company site."}
    with patch("tracker.sci_youtube_client.resolve_company_channel") as mock_resolve:
        sci_pipeline._apply_youtube_fallback(result, "Position2")
        mock_resolve.assert_not_called()
    assert result["youtube"]["handle"] == "@verified"


def test_youtube_fallback_is_a_no_op_without_an_api_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    result = _all_none()
    sci_pipeline._apply_youtube_fallback(result, "Position2")
    assert result["youtube"]["handle"] is None


def test_youtube_fallback_never_raises_when_the_lookup_blows_up(monkeypatch):
    """A broken fallback must not take down the run it was added to rescue."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    result = _all_none()
    with patch("tracker.sci_youtube_client.resolve_company_channel",
               side_effect=RuntimeError("boom")):
        sci_pipeline._apply_youtube_fallback(result, "Position2")
    assert result["youtube"]["handle"] is None


def test_run_identify_actually_applies_the_youtube_fallback(monkeypatch):
    """Wiring test, not a unit test: every assertion above calls
    _apply_youtube_fallback() directly and so stays green even if nothing
    ever calls it. This one drives run_identify() end to end and fails if
    the fallback is not wired in, which is the only way it helps anyone."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    from tracker import sci_store

    rows = {}
    monkeypatch.setattr(sci_store, "update_run_status", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "upsert_platform_run",
                        lambda run_id, platform, **k: rows.__setitem__(platform, k))

    from tracker import sci_identify
    monkeypatch.setattr(sci_identify, "identify_handles", lambda *a, **k: _all_none())

    with patch("tracker.sci_youtube_client.resolve_company_channel") as mock_resolve:
        mock_resolve.return_value = {"channel_id": "UC" + "a" * 22, "title": "Position2",
                                     "handle": "@position2",
                                     "profile_url": "https://www.youtube.com/@position2"}
        result = sci_pipeline.run_identify(1, "Position2", "http://www.position2.com")

    assert result["youtube"]["handle"] == "@position2"
    # The platform row must be queued for collection, not written off as
    # handle_not_found the way every other platform correctly is.
    assert rows["youtube"]["status"] == "identifying"
    assert rows["youtube"]["handle"] == "@position2"
    assert rows["facebook"]["status"] == "handle_not_found"
