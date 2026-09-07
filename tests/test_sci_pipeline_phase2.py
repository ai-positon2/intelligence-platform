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


# --- per-platform collection depth --------------------------------------
#
# 2026-09-07: replaced the old per-platform depth split (YouTube/Reddit at a
# 100-post floor, everything else at 20) with ONE hard cap shared by every
# platform, on explicit user request: "it should only search/scrape the last
# 25 posts/reels/etc on all social media platforms." See
# sci_pipeline.MAX_POSTS_PER_PLATFORM's own comment for the full reasoning.

def test_every_platform_shares_the_same_cap():
    assert sci_pipeline.MAX_POSTS_PER_PLATFORM == 25


def test_windowing_caps_every_platform_the_same_even_a_very_active_account():
    """The old floor-only design let _window_posts return MORE than the
    per-platform depth for an account posting more than that inside the
    30-day window (that was the whole point of the floor being a floor, not
    a ceiling). The new design is a hard ceiling: even 60 posts, all within
    the window, get capped at MAX_POSTS_PER_PLATFORM."""
    recent = [{"platform_post_id": str(i), "posted_at": "2026-09-01T00:00:00Z"} for i in range(60)]
    assert len(sci_pipeline._window_posts(recent)) == sci_pipeline.MAX_POSTS_PER_PLATFORM


def test_windowing_still_floors_a_low_activity_account():
    """The union-of-both-rules floor behaviour survives for the case it
    actually exists for: an account with almost nothing in the last 30 days
    still gets its most recent posts rather than an empty/near-empty list."""
    old = [{"platform_post_id": str(i), "posted_at": "2015-01-01T00:00:00Z"} for i in range(60)]
    assert len(sci_pipeline._window_posts(old)) == sci_pipeline.MAX_POSTS_PER_PLATFORM


def test_run_platform_collection_caps_every_platform_the_same(monkeypatch):
    """Wiring test: run_platform_collection must pass a genuinely capped list
    to sci_store, for EVERY platform -- not just the previously-special ones."""
    from tracker import sci_store
    written = {}
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "upsert_posts",
                        lambda run_id, platform, posts: written.setdefault(platform, len(posts)))
    posts = [{"platform_post_id": str(i), "posted_at": "2026-09-01T00:00:00Z"} for i in range(60)]
    monkeypatch.setattr(sci_pipeline, "_collect_youtube", lambda h: (posts, "youtube_api"))
    sci_pipeline.run_platform_collection(1, "youtube", "@acme")
    assert written["youtube"] == sci_pipeline.MAX_POSTS_PER_PLATFORM, \
        "youtube was not capped to the shared per-platform limit"


def test_collect_youtube_requests_exactly_the_shared_cap(monkeypatch):
    """The cap must reach the actual vendor request, not just the post-hoc
    trim -- otherwise the API is still asked for (and, for a scraped
    platform, billed for) more than gets kept."""
    from tracker import sci_youtube_client
    calls = {}
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.setattr(sci_youtube_client, "resolve_channel", lambda h, k: "chan1")

    def fake_list(channel_id, api_key, max_results=20, days=30):
        calls["max_results"] = max_results
        return []
    monkeypatch.setattr(sci_youtube_client, "list_recent_videos", fake_list)
    sci_pipeline._collect_youtube("@acme")
    assert calls["max_results"] == sci_pipeline.MAX_POSTS_PER_PLATFORM


def test_collect_via_apify_requests_exactly_the_shared_cap(monkeypatch):
    from tracker import sci_source_tiktok
    calls = {}
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")

    def fake_collect(handle, token, max_posts=25, strict=True):
        calls["max_posts"] = max_posts
        return []
    monkeypatch.setattr(sci_source_tiktok, "collect", fake_collect)
    sci_pipeline._collect_via_apify("tiktok", "acme")
    assert calls["max_posts"] == sci_pipeline.MAX_POSTS_PER_PLATFORM


# --- Reddit: two different questions, only one of them about owned posts ---
#
# Reddit contributes both a (usually absent) company account and the brand
# conversation, which exists for companies with no Reddit account at all.
# Conflating the two is what would make Reddit a seventh empty row.

def test_collect_reddit_requests_exactly_the_shared_cap(monkeypatch):
    from tracker import sci_reddit_client
    calls = {}
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)

    def fake_list_user_posts(username, limit=100):
        calls["limit"] = limit
        return []
    monkeypatch.setattr(sci_reddit_client, "list_user_posts", fake_list_user_posts)
    sci_pipeline._collect_reddit("acme")
    assert calls["limit"] == sci_pipeline.MAX_POSTS_PER_PLATFORM


def test_collect_reddit_refuses_without_credentials(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    try:
        sci_pipeline._collect_reddit("u/acme")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "REDDIT_CLIENT_ID" in str(e)


def test_collect_reddit_strips_the_u_prefix_before_fetching(monkeypatch):
    from tracker import sci_reddit_client
    seen = {}
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "list_user_posts",
                        lambda u, limit=None: seen.setdefault("user", u) or [])
    posts, vendor = sci_pipeline._collect_reddit("u/acme")
    assert seen["user"] == "acme"
    assert vendor == "reddit_api"


def test_reddit_fallback_never_searches(monkeypatch):
    """The mirror of sci_reddit_client's own rule, enforced at the pipeline
    boundary: only an exact handle hit may resolve a Reddit account."""
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    called = []
    monkeypatch.setattr(sci_reddit_client, "resolve_company_account",
                        lambda name: called.append(name) or None)
    monkeypatch.setattr(sci_reddit_client, "search_posts",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not search")))
    result = _all_none()
    sci_pipeline._apply_reddit_fallback(result, "Acme Inc")
    assert called == ["Acme Inc"]
    assert result["reddit"]["handle"] is None


def test_reddit_fallback_applies_an_exact_handle_hit(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "resolve_company_account", lambda name: {
        "kind": "user", "handle": "u/position2",
        "profile_url": "https://www.reddit.com/user/position2/", "title": "position2"})
    result = _all_none()
    sci_pipeline._apply_reddit_fallback(result, "Position2")
    assert result["reddit"]["handle"] == "u/position2"
    assert result["reddit"]["confidence"] in sci_pipeline._USABLE_CONFIDENCE
    assert "Reddit API" in result["reddit"]["reasoning"]


def test_reddit_fallback_is_a_no_op_without_credentials(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: False)
    result = _all_none()
    sci_pipeline._apply_reddit_fallback(result, "Position2")
    assert result["reddit"]["handle"] is None


def test_reddit_fallback_never_raises(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "resolve_company_account",
                        lambda name: (_ for _ in ()).throw(RuntimeError("boom")))
    result = _all_none()
    sci_pipeline._apply_reddit_fallback(result, "Position2")
    assert result["reddit"]["handle"] is None


def test_run_identify_actually_applies_the_reddit_fallback(monkeypatch):
    """Wiring test, for the same reason the YouTube one exists: every
    assertion above calls _apply_reddit_fallback directly and stays green
    even if run_identify never calls it."""
    from tracker import sci_store, sci_identify, sci_reddit_client
    rows = {}
    monkeypatch.setattr(sci_store, "update_run_status", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "upsert_platform_run",
                        lambda run_id, platform, **k: rows.__setitem__(platform, k))
    monkeypatch.setattr(sci_identify, "identify_handles", lambda *a, **k: _all_none())
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "resolve_company_account", lambda name: {
        "kind": "user", "handle": "u/position2",
        "profile_url": "https://www.reddit.com/user/position2/", "title": "position2"})
    monkeypatch.setenv("YOUTUBE_API_KEY", "")

    result = sci_pipeline.run_identify(1, "Position2", "http://www.position2.com")
    assert result["reddit"]["handle"] == "u/position2"
    assert rows["reddit"]["status"] == "identifying"


def test_run_reddit_pulse_writes_the_pulse_onto_the_run(monkeypatch):
    from tracker import sci_store, sci_reddit_pulse
    written = {}
    monkeypatch.setattr(sci_reddit_pulse, "build_pulse",
                        lambda name, url: {"company": name, "thread_count": 3})
    monkeypatch.setattr(sci_store, "update_run_status",
                        lambda run_id, status, **k: written.update(k))
    sci_pipeline.run_reddit_pulse(9, "Acme", "acme.com")
    assert written["reddit_pulse"]["thread_count"] == 3


def test_run_reddit_pulse_never_raises(monkeypatch):
    """A failure here must leave every platform's collected posts and the
    synthesis completely untouched."""
    from tracker import sci_reddit_pulse
    monkeypatch.setattr(sci_reddit_pulse, "build_pulse",
                        lambda name, url: (_ for _ in ()).throw(RuntimeError("boom")))
    sci_pipeline.run_reddit_pulse(9, "Acme", None)  # must not raise
