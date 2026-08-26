"""tracker/sci_pipeline.py -- Unipile as a second collection vendor for
LinkedIn/Instagram. The contract under test: a connected Unipile account is
tried FIRST, a Unipile failure falls through to the pre-existing Apify path
rather than failing the platform outright, and with neither vendor
available both platforms still degrade to a clear RuntimeError exactly like
before this vendor existed (tests/test_sci_pipeline_phase2.py covers that
degradation path in detail for LinkedIn already; this file only adds the
NEW Unipile-specific branches).
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_pipeline  # noqa: E402


# ── LinkedIn ──────────────────────────────────────────────────────────────

@patch("tracker.sci_source_linkedin_unipile.collect")
@patch("tracker.unipile_client.is_available")
def test_collect_linkedin_prefers_unipile_when_available(mock_available, mock_collect):
    mock_available.return_value = True
    mock_collect.return_value = [{"platform_post_id": "1"}]
    posts, vendor = sci_pipeline._collect_linkedin("acmeco")
    assert vendor == "unipile"
    assert posts == [{"platform_post_id": "1"}]
    mock_available.assert_called_once_with("linkedin")


@patch("tracker.sci_source_linkedin.collect")
@patch("tracker.sci_source_linkedin.actor_id", return_value="some/actor")
@patch("tracker.sci_source_linkedin_unipile.collect")
@patch("tracker.unipile_client.is_available")
def test_collect_linkedin_falls_back_to_apify_when_unipile_not_connected(
        mock_available, mock_unipile_collect, mock_actor_id, mock_apify_collect, monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    mock_available.return_value = False
    mock_apify_collect.return_value = [{"platform_post_id": "2"}]
    posts, vendor = sci_pipeline._collect_linkedin("acmeco")
    assert vendor == "apify"
    assert mock_unipile_collect.call_count == 0


@patch("tracker.sci_source_linkedin.collect")
@patch("tracker.sci_source_linkedin.actor_id", return_value="some/actor")
@patch("tracker.sci_source_linkedin_unipile.collect")
@patch("tracker.unipile_client.is_available")
def test_collect_linkedin_falls_back_to_apify_when_unipile_raises(
        mock_available, mock_unipile_collect, mock_actor_id, mock_apify_collect, monkeypatch):
    """A connected-but-broken Unipile account (revoked session, rate limit)
    must not fail the whole platform outright when Apify is still a valid
    fallback -- same reasoning as apify_transport's own strict/non-strict
    split, one layer up."""
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    mock_available.return_value = True
    mock_unipile_collect.side_effect = RuntimeError("session expired")
    mock_apify_collect.return_value = [{"platform_post_id": "3"}]
    posts, vendor = sci_pipeline._collect_linkedin("acmeco")
    assert vendor == "apify"
    assert posts == [{"platform_post_id": "3"}]


@patch("tracker.sci_source_linkedin.actor_id", return_value=None)
@patch("tracker.unipile_client.is_available", return_value=False)
def test_collect_linkedin_still_raises_when_neither_vendor_is_configured(mock_available, mock_actor_id):
    try:
        sci_pipeline._collect_linkedin("acmeco")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "disabled" in str(e).lower()


# ── Instagram ─────────────────────────────────────────────────────────────

@patch("tracker.sci_source_instagram_unipile.collect")
@patch("tracker.unipile_client.is_available")
def test_collect_instagram_prefers_unipile_when_available(mock_available, mock_collect):
    mock_available.return_value = True
    mock_collect.return_value = [{"platform_post_id": "1"}]
    posts, vendor = sci_pipeline._collect_instagram("acmeco")
    assert vendor == "unipile"
    mock_available.assert_called_once_with("instagram")


@patch("tracker.sci_source_instagram.collect")
@patch("tracker.sci_source_instagram_unipile.collect")
@patch("tracker.unipile_client.is_available")
def test_collect_instagram_falls_back_to_apify_when_unipile_not_connected(
        mock_available, mock_unipile_collect, mock_apify_collect, monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    mock_available.return_value = False
    mock_apify_collect.return_value = [{"platform_post_id": "2"}]
    posts, vendor = sci_pipeline._collect_instagram("acmeco")
    assert vendor == "apify"
    assert mock_unipile_collect.call_count == 0


@patch("tracker.unipile_client.is_available", return_value=False)
def test_collect_instagram_raises_when_neither_vendor_is_configured(mock_available, monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    try:
        sci_pipeline._collect_instagram("acmeco")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "unavailable" in str(e).lower()


# ── run_platform_collection records which vendor served the platform ───────

@patch("tracker.sci_source_instagram_unipile.collect")
@patch("tracker.unipile_client.is_available")
def test_run_platform_collection_records_source_vendor(mock_available, mock_collect):
    from tracker import sci_store
    mock_available.return_value = True
    mock_collect.return_value = [{"platform_post_id": "1", "posted_at": None}]
    calls = []
    with patch.object(sci_store, "upsert_platform_run",
                      side_effect=lambda run_id, platform, **kw: calls.append(kw)), \
         patch.object(sci_store, "upsert_posts", return_value=1):
        sci_pipeline.run_platform_collection(1, "instagram", "acmeco")
    terminal = [kw for kw in calls if kw.get("status")]
    assert terminal[-1]["source_vendor"] == "unipile"


# ── instagram is no longer dispatched through the generic Apify registry ───

def test_instagram_is_not_in_the_apify_collectors_registry():
    assert "instagram" not in sci_pipeline._APIFY_COLLECTORS
