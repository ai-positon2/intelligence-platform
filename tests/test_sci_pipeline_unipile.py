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

@patch("tracker.sci_source_linkedin_unipile.collect_with_page")
@patch("tracker.unipile_client.is_available")
def test_collect_linkedin_prefers_unipile_when_available(mock_available, mock_collect):
    mock_available.return_value = True
    mock_collect.return_value = ([{"platform_post_id": "1"}], {"verification": "domain", "page": "x"})
    posts, vendor, note = sci_pipeline._collect_linkedin("acmeco")
    assert vendor == "unipile"
    assert posts == [{"platform_post_id": "1"}]
    mock_available.assert_called_once_with("linkedin")


@patch("tracker.sci_source_linkedin.collect")
@patch("tracker.sci_source_linkedin.actor_id", return_value="some/actor")
@patch("tracker.sci_source_linkedin_unipile.collect_with_page")
@patch("tracker.unipile_client.is_available")
def test_collect_linkedin_falls_back_to_apify_when_unipile_not_connected(
        mock_available, mock_unipile_collect, mock_actor_id, mock_apify_collect, monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    mock_available.return_value = False
    mock_apify_collect.return_value = [{"platform_post_id": "2"}]
    posts, vendor, note = sci_pipeline._collect_linkedin("acmeco")
    assert vendor == "apify"
    assert mock_unipile_collect.call_count == 0


@patch("tracker.sci_source_linkedin.collect")
@patch("tracker.sci_source_linkedin.actor_id", return_value="some/actor")
@patch("tracker.sci_source_linkedin_unipile.collect_with_page")
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
    posts, vendor, note = sci_pipeline._collect_linkedin("acmeco")
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


# ── A wrong LinkedIn page must not become a confident report ──────────────

@patch("tracker.sci_source_linkedin.actor_id", return_value="some/actor")
@patch("tracker.sci_source_linkedin_unipile.collect_with_page")
@patch("tracker.unipile_client.is_available", return_value=True)
def test_a_wrong_company_page_is_not_retried_through_apify(
        mock_available, mock_collect, mock_actor_id):
    """Every other Unipile failure falls through to Apify, because "this
    vendor could not answer" is a question another vendor might. A handle
    pointing at the wrong company points there just as squarely through
    Apify, so retrying would turn a caught mistake into a confidently wrong
    report."""
    from tracker import sci_source_linkedin_unipile as ln
    mock_collect.side_effect = ln.CompanyMismatch("wrong page")
    try:
        sci_pipeline._collect_linkedin("notion", "Acme Dental", "https://acmedental.com")
        assert False, "expected CompanyMismatch"
    except ln.CompanyMismatch:
        pass


@patch("tracker.sci_source_linkedin_unipile.collect_with_page")
@patch("tracker.unipile_client.is_available", return_value=True)
def test_the_company_being_researched_reaches_the_collector(mock_available, mock_collect):
    """The check cannot run on a name the collector never receives, and
    nothing else in the call chain would fail if it went missing."""
    mock_collect.return_value = ([], None)
    sci_pipeline._collect_linkedin("acmeco", "Acme Corp", "https://acme.com")
    assert mock_collect.call_args.kwargs["company_name"] == "Acme Corp"
    assert mock_collect.call_args.kwargs["company_url"] == "https://acme.com"


# ── How an empty result is described ──────────────────────────────────────

def test_an_empty_unconfirmed_page_is_never_described_as_an_empty_company():
    """Rendered bare, "no presence" is a claim about the company. What was
    actually established is a claim about a page nobody confirmed is theirs."""
    from tracker import sci_source_linkedin_unipile as ln
    note = {"verification": ln.VERIFIED_NAME,
            "page": "linkedin.com/company/notion (Notion, 882 followers, no website listed)"}
    detail = sci_pipeline._collection_note("no_presence", note)
    assert "linkedin.com/company/notion" in detail
    assert "882 followers" in detail
    assert "wrong page" in detail


def test_a_confirmed_page_needs_no_caption():
    from tracker import sci_source_linkedin_unipile as ln
    assert sci_pipeline._collection_note("no_presence", {"verification": ln.VERIFIED_DOMAIN,
                                                         "page": "x"}) is None
    assert sci_pipeline._collection_note("ok", {"verification": ln.VERIFIED_DOMAIN, "page": "x"}) is None


def test_an_unconfirmed_page_that_did_produce_posts_is_recorded_but_not_alarming():
    """The posts are the evidence a reader will judge, so this stays a plain
    record rather than a warning."""
    from tracker import sci_source_linkedin_unipile as ln
    detail = sci_pipeline._collection_note("ok", {"verification": ln.VERIFIED_NAME,
                                                  "page": "linkedin.com/company/stripe (Stripe)"})
    assert detail == "Read linkedin.com/company/stripe (Stripe)."


def test_platforms_that_cannot_report_a_page_are_left_uncaptioned():
    assert sci_pipeline._collection_note("no_presence", None) is None


@patch("tracker.sci_pipeline._collect_linkedin")
def test_run_platform_collection_stores_the_warning_where_the_report_reads_it(mock_collect):
    """status_detail is the field the report renders in an empty platform
    pane. A warning written anywhere else is a warning nobody sees."""
    from tracker import sci_source_linkedin_unipile as ln
    from tracker import sci_store
    mock_collect.return_value = ([], "unipile", {
        "verification": ln.VERIFIED_NAME,
        "page": "linkedin.com/company/notion (Notion, 882 followers, no website listed)"})
    written = {}
    with patch.object(sci_store, "upsert_platform_run",
                      side_effect=lambda run_id, platform, **f: written.update(f)), \
         patch.object(sci_store, "upsert_posts", return_value=0):
        sci_pipeline.run_platform_collection(1, "linkedin", "notion",
                                             company_name="Notion", company_url="https://notion.com")
    assert written["status"] == "no_presence"
    assert "linkedin.com/company/notion" in written["status_detail"]
