"""Phase 0 (identity resolution) for Thought Leader Intelligence.

Mirrors tests/test_event_intel_phase2.py's mocking style: claude_websearch.ask
is monkeypatched at the module-attribute level rather than hitting a real
API, and the store functions are proven fail-soft the same way every other
tracker/*_store.py module is (no DATABASE_URL -> None/[]/False, never a
raised exception).
"""

from __future__ import annotations

import os

import pytest

from tracker import claude_websearch, thought_leader_pr as T


def _fake_search_result(text: str, search_count: int = 3) -> dict:
    return {"text": text, "search_count": search_count, "error": None,
            "usage": {}, "stop_reason": "end_turn"}


@pytest.fixture(autouse=True)
def _no_side_effect_keys(monkeypatch):
    """No test in this file should ever reach a real vendor: clear every key
    resolve_identity() checks so a missing monkeypatch fails loudly (a
    network error) rather than silently hitting a real API."""
    for key in ("APOLLO_API_KEY", "YOUTUBE_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)


def _stub_platforms(monkeypatch):
    """Neutral stand-ins for the three best-effort platform lookups so a test
    about the websearch/confidence logic isn't also exercising Unipile/
    YouTube's own network calls."""
    monkeypatch.setattr(T, "_resolve_linkedin_platform",
                        lambda url: {"url": url, "resolved": False, "provider_id": None,
                                     "headline": None, "photo_url": None, "note": "stub"})
    monkeypatch.setattr(T, "_resolve_youtube_platform",
                        lambda name: {"url": None, "title": None, "resolved": False, "note": "stub"})


class TestResolveIdentityGates:
    def test_blank_name_is_refused_without_any_lookup(self, monkeypatch):
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: pytest.fail("must not call websearch for a blank name"))
        out = T.resolve_identity("   ")
        assert out == {"ok": False, "confidence": "none", "reasoning": "No name was provided.",
                       "identity": None, "spend": {}, "error": None}

    def test_a_reply_with_no_search_is_refused_as_recalled_not_verified(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: _fake_search_result('{"confidence":"high"}', search_count=0))
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["confidence"] == "none"
        assert "recalled rather than verified" in out["reasoning"]

    def test_low_confidence_is_refused_rather_than_softened(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _fake_search_result(
            '{"confidence":"low","reasoning":"Several people share this name.",'
            '"is_public_figure":true,"full_name":"Some Person"}'))
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["confidence"] == "low"
        assert out["identity"] is None

    def test_private_individual_is_refused_even_at_high_confidence(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _fake_search_result(
            '{"confidence":"high","reasoning":"No public platform found.",'
            '"is_public_figure":false,"full_name":"Some Person"}'))
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["identity"] is None

    def test_unparsable_reply_is_refused_with_an_unparsable_error(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: _fake_search_result("not json at all"))
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["error"]["kind"] == claude_websearch.ERR_UNPARSABLE

    def test_a_transport_error_is_surfaced_with_a_reader_facing_reason(self, monkeypatch):
        err = {"kind": claude_websearch.ERR_TRANSPORT, "detail": "boom"}
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: {"text": "", "search_count": 0, "error": err, "usage": {}})
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["error"] is err


class TestResolveIdentityHappyPath:
    def test_high_confidence_public_figure_assembles_a_full_identity(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        monkeypatch.setattr(T, "_best_apollo_candidate", lambda *a, **kw: {
            "full_name": "Jane Doe", "title": "Chief Strategy Officer",
            "organization_name": "Acme Corp", "linkedin_url": "https://www.linkedin.com/in/janedoe/",
            "twitter_url": "https://twitter.com/janedoe", "photo_url": "https://img.example/jane.jpg",
        })
        monkeypatch.setattr(T, "_resolve_linkedin_platform", lambda url: {
            "url": url, "resolved": True, "provider_id": "urn:li:member:123",
            "headline": "CSO at Acme -- scaling go-to-market", "photo_url": "https://img.example/jane-li.jpg",
            "note": None})
        monkeypatch.setattr(T, "_resolve_youtube_platform", lambda name: {
            "url": "https://www.youtube.com/@janedoe", "title": "Jane Doe", "resolved": True, "note": None})
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _fake_search_result(
            '{"confidence":"high","reasoning":"Multiple sources confirm this is Jane Doe -- CSO at Acme.",'
            '"is_public_figure":true,"full_name":"Jane Doe","headline":"CSO at Acme",'
            '"current_title":"Chief Strategy Officer","current_company":"Acme Corp",'
            '"linkedin_url":"https://www.linkedin.com/in/janedoe/","x_handle":"janedoe",'
            '"disambiguating_facts":["Author of Scaling Go-to-Market","Keynoted SaaStr 2026",'
            '"Extra fact one","Extra fact two","Extra fact three -- should be dropped, only 4 kept"]}'))

        out = T.resolve_identity("Jane Doe", company_hint="Acme")

        assert out["ok"] is True
        assert out["confidence"] == "high"
        identity = out["identity"]
        assert identity["full_name"] == "Jane Doe"
        assert identity["current_company"] == "Acme Corp"
        # em-dash stripped from every free-text field the model wrote, same
        # discipline event_intel_resolve applies -- a dash here has shipped
        # to a live report before.
        assert "—" not in identity["reasoning"]
        assert "—" not in identity["headline"]
        assert len(identity["disambiguating_facts"]) == 4
        assert identity["platforms"]["linkedin"]["resolved"] is True
        assert identity["platforms"]["x"] == {"handle": "janedoe", "url": "https://x.com/janedoe",
                                              "resolved": True}
        assert identity["platforms"]["youtube"]["resolved"] is True
        assert identity["source"]["apollo_matched"] is True

    def test_apollo_miss_still_resolves_from_websearch_alone(self, monkeypatch):
        monkeypatch.setattr(T, "_best_apollo_candidate", lambda *a, **kw: None)
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _fake_search_result(
            '{"confidence":"medium","reasoning":"Identified via press coverage.",'
            '"is_public_figure":true,"full_name":"Jane Doe","x_handle":"@janedoe"}'))
        out = T.resolve_identity("Jane Doe")
        assert out["ok"] is True
        assert out["identity"]["source"]["apollo_matched"] is False
        # a leading @ on the model's own x_handle is stripped, same as the
        # user-supplied path
        assert out["identity"]["platforms"]["x"]["handle"] == "janedoe"


class TestLinkedInSlug:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.linkedin.com/in/satyanadella/", "satyanadella"),
        ("https://www.linkedin.com/in/satyanadella", "satyanadella"),
        ("http://linkedin.com/in/satya-nadella-123/", "satya-nadella-123"),
        ("", None),
        (None, None),
    ])
    def test_extracts_the_public_identifier(self, url, expected):
        assert T._linkedin_slug(url) == expected


class TestStoreFailSoft:
    """No DATABASE_URL configured (cleared by the autouse fixture above) --
    every store function must degrade quietly, never raise."""

    def test_create_run_returns_none(self):
        assert T.create_run(email="a@b.com", input_name="Jane Doe") is None

    def test_save_result_returns_false(self):
        assert T.save_result(1, "a@b.com", {"ok": True, "identity": {}}) is False

    def test_confirm_run_returns_false(self):
        assert T.confirm_run(1, "a@b.com") is False

    def test_get_run_returns_none(self):
        assert T.get_run(1, "a@b.com") is None

    def test_list_runs_returns_empty_list(self):
        assert T.list_runs("a@b.com") == []

    def test_start_collecting_returns_false(self):
        assert T.start_collecting(1, "a@b.com") is False

    def test_save_posts_returns_false(self):
        assert T.save_posts(1, "a@b.com", {}, {}) is False

    def test_save_posts_failed_returns_false(self):
        assert T.save_posts_failed(1, "a@b.com", "boom") is False


class TestCollectLinkedInPosts:
    def test_no_provider_id_is_refused_without_a_network_call(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform",
                            lambda p: pytest.fail("must not look up an account with no provider_id"))
        posts, err = T._collect_linkedin_posts({"url": "https://linkedin.com/in/x"}, 20)
        assert posts == [] and "verified LinkedIn profile" in err

    def test_no_connected_account_is_reported_plainly(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: None)
        posts, err = T._collect_linkedin_posts({"provider_id": "urn:li:member:1"}, 20)
        assert posts == [] and "No connected LinkedIn account" in err

    def test_a_transport_error_is_surfaced_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        def boom(*a, **kw):
            raise T.unipile_transport.UnipileTransportError("no working account")
        monkeypatch.setattr(T.unipile_transport, "fetch_posts", boom)
        posts, err = T._collect_linkedin_posts({"provider_id": "urn:li:member:1"}, 20)
        assert posts == [] and err == "no working account"

    def test_success_normalizes_through_the_real_linkedin_adapter(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        raw = [{"id": "p1", "share_url": "https://li/1", "text": "hello", "parsed_datetime": "2026-09-01"}]
        monkeypatch.setattr(T.unipile_transport, "fetch_posts", lambda *a, **kw: raw)
        posts, err = T._collect_linkedin_posts({"provider_id": "urn:li:member:1"}, 20)
        assert err is None
        assert posts[0]["platform_post_id"] == "p1"
        assert posts[0]["caption"] == "hello"


class TestCollectXPosts:
    def test_no_handle_is_refused(self):
        posts, err = T._collect_x_posts({}, 20)
        assert posts == [] and "No X handle" in err

    def test_no_token_is_reported_plainly(self, monkeypatch):
        monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
        posts, err = T._collect_x_posts({"handle": "janedoe"}, 20)
        assert posts == [] and "not configured" in err

    def test_a_transport_error_is_surfaced(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        def boom(*a, **kw):
            raise T.apify_transport.ApifyTransportError("actor failed")
        monkeypatch.setattr(T.sci_source_x, "collect", boom)
        posts, err = T._collect_x_posts({"handle": "janedoe"}, 20)
        assert posts == [] and err == "actor failed"

    def test_success_reuses_the_real_x_adapter_unmodified(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        captured = {}
        def fake_collect(handle, token, max_posts=25, strict=True):
            captured.update(handle=handle, token=token, max_posts=max_posts)
            return [{"platform_post_id": "t1", "caption": "hi"}]
        monkeypatch.setattr(T.sci_source_x, "collect", fake_collect)
        posts, err = T._collect_x_posts({"handle": "janedoe"}, 15)
        assert err is None
        assert posts == [{"platform_post_id": "t1", "caption": "hi"}]
        assert captured == {"handle": "janedoe", "token": "tok", "max_posts": 15}


class TestCollectYouTubePosts:
    def test_no_channel_id_is_refused(self):
        posts, err = T._collect_youtube_posts({}, 20)
        assert posts == [] and "No YouTube channel" in err

    def test_no_api_key_is_reported_plainly(self, monkeypatch):
        posts, err = T._collect_youtube_posts({"channel_id": "UC1"}, 20)
        assert posts == [] and "not configured" in err

    def test_empty_result_is_reported_as_no_recent_videos(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        monkeypatch.setattr(T.sci_youtube_client, "list_recent_videos", lambda *a, **kw: [])
        posts, err = T._collect_youtube_posts({"channel_id": "UC1"}, 20)
        assert posts == [] and "No recent videos" in err

    def test_success_passes_through_the_shared_post_shape(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        videos = [{"platform_post_id": "v1", "caption": "a video"}]
        monkeypatch.setattr(T.sci_youtube_client, "list_recent_videos", lambda *a, **kw: videos)
        posts, err = T._collect_youtube_posts({"channel_id": "UC1"}, 20)
        assert err is None and posts == videos


class TestCollectPostsJob:
    def _identity(self, **platforms):
        return {"identity": {"platforms": {
            "linkedin": platforms.get("linkedin", {}),
            "x": platforms.get("x", {}),
            "youtube": platforms.get("youtube", {}),
        }}}

    def test_no_run_or_no_identity_saves_a_failed_state(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: None)
        captured = {}
        monkeypatch.setattr(T, "save_posts_failed", lambda run_id, email, msg: captured.update(
            run_id=run_id, email=email, msg=msg) or True)
        monkeypatch.setattr(T, "save_posts", lambda *a, **kw: pytest.fail("must not save partial posts"))
        T.collect_posts_job(5, "a@b.com")
        assert captured["run_id"] == 5 and "no confirmed identity" in captured["msg"]

    def test_all_three_platforms_succeed(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity(
            linkedin={"provider_id": "p1"}, x={"handle": "h1"}, youtube={"channel_id": "c1"}))
        monkeypatch.setattr(T, "_collect_linkedin_posts", lambda p, n: ([{"platform_post_id": "l1"}], None))
        monkeypatch.setattr(T, "_collect_x_posts", lambda p, n: ([{"platform_post_id": "x1"}], None))
        monkeypatch.setattr(T, "_collect_youtube_posts", lambda p, n: ([{"platform_post_id": "y1"}], None))
        captured = {}
        monkeypatch.setattr(T, "save_posts", lambda run_id, email, posts, errors: captured.update(
            run_id=run_id, email=email, posts=posts, errors=errors) or True)
        T.collect_posts_job(7, "a@b.com")
        assert captured["errors"] == {}
        assert captured["posts"]["linkedin"] == [{"platform_post_id": "l1"}]
        assert captured["posts"]["x"] == [{"platform_post_id": "x1"}]
        assert captured["posts"]["youtube"] == [{"platform_post_id": "y1"}]

    def test_one_platform_failing_does_not_block_the_others_or_fail_the_run(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity())
        monkeypatch.setattr(T, "_collect_linkedin_posts", lambda p, n: ([], "No connected LinkedIn account."))
        monkeypatch.setattr(T, "_collect_x_posts", lambda p, n: ([{"platform_post_id": "x1"}], None))
        monkeypatch.setattr(T, "_collect_youtube_posts", lambda p, n: ([], "No YouTube channel."))
        captured = {}
        monkeypatch.setattr(T, "save_posts", lambda run_id, email, posts, errors: captured.update(
            posts=posts, errors=errors) or True)
        monkeypatch.setattr(T, "save_posts_failed", lambda *a, **kw: pytest.fail("a partial result is not a failure"))
        T.collect_posts_job(9, "a@b.com")
        assert captured["errors"] == {"linkedin": "No connected LinkedIn account.",
                                      "youtube": "No YouTube channel."}
        assert captured["posts"]["x"] == [{"platform_post_id": "x1"}]

    def test_an_unexpected_crash_still_reaches_a_terminal_state(self, monkeypatch):
        def boom(run_id, email):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(T, "get_run", boom)
        captured = {}
        monkeypatch.setattr(T, "save_posts_failed", lambda run_id, email, msg: captured.update(msg=msg) or True)
        T.collect_posts_job(3, "a@b.com")
        assert "unexpected error" in captured["msg"]


class TestResolveStalePosts:
    def test_a_fresh_collecting_run_is_left_alone(self):
        run = {"id": 1, "posts_status": "collecting",
              "updated_at": T.datetime.now(T.timezone.utc).isoformat()}
        out = T._resolve_stale_posts(run, "a@b.com")
        assert out["posts_status"] == "collecting"

    def test_a_stale_collecting_run_is_flipped_to_failed(self, monkeypatch):
        stale_time = (T.datetime.now(T.timezone.utc) - T.timedelta(minutes=T.STALE_RUN_MINUTES + 1)).isoformat()
        run = {"id": 1, "posts_status": "collecting", "updated_at": stale_time}
        monkeypatch.setattr(T, "save_posts_failed", lambda *a, **kw: True)
        out = T._resolve_stale_posts(run, "a@b.com")
        assert out["posts_status"] == "failed"
        assert "_run" in out["posts_errors"]

    def test_a_non_collecting_run_is_never_touched(self, monkeypatch):
        monkeypatch.setattr(T, "save_posts_failed",
                            lambda *a, **kw: pytest.fail("must not touch a non-collecting run"))
        run = {"id": 1, "posts_status": "ready", "updated_at": "2020-01-01T00:00:00+00:00"}
        assert T._resolve_stale_posts(run, "a@b.com") == run


class TestPhase2StoreFailSoft:
    def test_start_reacting_returns_false(self):
        assert T.start_reacting(1, "a@b.com") is False

    def test_save_reaction_returns_false(self):
        assert T.save_reaction(1, "a@b.com", {}, {}) is False

    def test_save_reaction_failed_returns_false(self):
        assert T.save_reaction_failed(1, "a@b.com", "boom") is False


class TestResolveStaleReaction:
    def test_a_fresh_collecting_run_is_left_alone(self):
        run = {"id": 1, "reaction_status": "collecting",
              "updated_at": T.datetime.now(T.timezone.utc).isoformat()}
        assert T._resolve_stale_reaction(run, "a@b.com")["reaction_status"] == "collecting"

    def test_a_stale_collecting_run_is_flipped_to_failed(self, monkeypatch):
        stale_time = (T.datetime.now(T.timezone.utc) - T.timedelta(minutes=T.STALE_RUN_MINUTES + 1)).isoformat()
        run = {"id": 1, "reaction_status": "collecting", "updated_at": stale_time}
        monkeypatch.setattr(T, "save_reaction_failed", lambda *a, **kw: True)
        out = T._resolve_stale_reaction(run, "a@b.com")
        assert out["reaction_status"] == "failed"
        assert "_run" in out["reaction_errors"]

    def test_a_stuck_posts_collection_never_strands_the_reaction_poll(self):
        """The two 'collecting' states are independent columns -- a posts
        collection stuck mid-flight must not make _resolve_stale_reaction
        think there is a reaction job to time out."""
        run = {"id": 1, "posts_status": "collecting", "reaction_status": "idle",
              "updated_at": "2020-01-01T00:00:00+00:00"}
        assert T._resolve_stale_reaction(run, "a@b.com") == run


class TestTopEngagedPosts:
    def test_ranks_by_combined_engagement_and_caps_the_count(self):
        posts = [{"platform_post_id": "low", "metrics": {"likes": 1}},
                {"platform_post_id": "high", "metrics": {"likes": 900, "comments": 40}},
                {"platform_post_id": "mid", "metrics": {"shares": 50}}]
        top = T._top_engaged_posts(posts, 2)
        assert [p["platform_post_id"] for p in top] == ["high", "mid"]

    def test_empty_input_is_fine(self):
        assert T._top_engaged_posts([], 5) == []


class TestCollectLinkedInComments:
    def test_no_posts_is_refused_without_a_network_call(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform",
                            lambda p: pytest.fail("must not look up an account with no posts"))
        out, err = T._collect_linkedin_comments([], 5, 20)
        assert out == [] and "No LinkedIn posts" in err

    def test_no_connected_account_is_reported_plainly(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: None)
        posts = [{"platform_post_id": "p1", "metrics": {"likes": 5}}]
        out, err = T._collect_linkedin_comments(posts, 5, 20)
        assert out == [] and "No connected LinkedIn account" in err

    def test_comments_are_collected_and_tagged_by_platform(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(T.unipile_client, "list_comments", lambda *a, **kw: (
            {"items": [{"id": "c1", "text": "great post",
                       "author_details": {"name": "Alex"}, "date": "2026-09-01",
                       "reaction_counter": 5}]}, None))
        posts = [{"platform_post_id": "p1", "post_url": "https://li/1", "metrics": {"likes": 5}}]
        out, err = T._collect_linkedin_comments(posts, 5, 20)
        assert err is None
        assert out == [{"platform": "linkedin", "comment_id": "c1", "text": "great post",
                        "author": "Alex", "posted_at": "2026-09-01", "likes": 5}]

    def test_a_post_with_no_comments_read_yields_the_no_comments_note(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(T.unipile_client, "list_comments", lambda *a, **kw: (None, {"kind": "http_status"}))
        posts = [{"platform_post_id": "p1", "metrics": {"likes": 5}}]
        out, err = T._collect_linkedin_comments(posts, 5, 20)
        assert out == [] and "No comments could be read" in err


class TestCollectXReplies:
    def test_no_posts_is_refused(self):
        out, err = T._collect_x_replies([], 5, 20)
        assert out == [] and "No X posts" in err

    def test_no_token_is_reported_plainly(self, monkeypatch):
        monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
        posts = [{"platform_post_id": "t1", "metrics": {"likes": 5}}]
        out, err = T._collect_x_replies(posts, 5, 20)
        assert out == [] and "not configured" in err

    def test_a_per_post_transport_failure_does_not_block_other_posts(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        calls = []
        def fake_collect(tweet_id, token, max_replies=20, strict=True):
            calls.append(tweet_id)
            if tweet_id == "bad":
                raise T.apify_transport.ApifyTransportError("actor failed")
            return [{"comment_id": "r1", "text": "nice", "author": "alex",
                    "posted_at": "2026-09-01", "likes": 3}]
        monkeypatch.setattr(T.apify_x_replies, "collect", fake_collect)
        posts = [{"platform_post_id": "bad", "metrics": {"likes": 999}},
                {"platform_post_id": "good", "metrics": {"likes": 1}}]
        out, err = T._collect_x_replies(posts, 5, 20)
        assert err is None
        assert calls == ["bad", "good"]
        assert out == [{"platform": "x", "comment_id": "r1", "text": "nice", "author": "alex",
                        "posted_at": "2026-09-01", "likes": 3}]


class TestCollectYouTubeComments:
    def test_no_posts_is_refused(self):
        out, err = T._collect_youtube_comments([], 5, 20)
        assert out == [] and "No YouTube videos" in err

    def test_no_api_key_is_reported_plainly(self, monkeypatch):
        posts = [{"platform_post_id": "v1", "metrics": {"likes": 5}}]
        out, err = T._collect_youtube_comments(posts, 5, 20)
        assert out == [] and "not configured" in err

    def test_success_tags_comments_by_platform(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        monkeypatch.setattr(T.sci_youtube_client, "list_video_comments", lambda *a, **kw: [
            {"comment_id": "c1", "text": "nice video", "author": "Sam",
             "posted_at": "2026-09-01", "likes": 2}])
        posts = [{"platform_post_id": "v1", "metrics": {"likes": 5}}]
        out, err = T._collect_youtube_comments(posts, 5, 20)
        assert err is None
        assert out == [{"platform": "youtube", "comment_id": "c1", "text": "nice video",
                        "author": "Sam", "posted_at": "2026-09-01", "likes": 2}]


class TestDigestComments:
    def test_orders_by_likes_and_prefixes_ids_by_platform(self):
        comments = [
            {"platform": "linkedin", "comment_id": "5", "text": "a", "likes": 1},
            {"platform": "x", "comment_id": "9", "text": "b", "likes": 50},
        ]
        digest = T._digest_comments(comments)
        assert digest[0]["id"] == "x:9"
        assert digest[1]["id"] == "linkedin:5"

    def test_missing_comment_id_falls_back_to_an_index(self):
        digest = T._digest_comments([{"platform": "youtube", "text": "a", "likes": 1}])
        assert digest[0]["id"] == "youtube:0"


class TestCleanReactionAnalysis:
    def test_sentiment_counts_come_from_labels_not_the_model(self):
        parsed = {
            "verdict": "Warmly received.",
            "comment_sentiment": {"linkedin:1": "positive", "x:2": "negative"},
            "themes": [], "notable_comments": [],
            "sentiment": {"counts": {"positive": 99}},
        }
        out = T._clean_reaction_analysis(parsed, {"linkedin:1", "x:2"})
        assert out["sentiment"]["counts"] == {"positive": 1, "neutral": 0, "negative": 1, "mixed": 0}
        assert out["sentiment"]["labelled"] == 2

    def test_a_hallucinated_comment_id_is_stripped_everywhere(self):
        parsed = {
            "comment_sentiment": {"linkedin:1": "positive", "ZZZ": "negative"},
            "themes": [{"label": "Praise", "stance": "praise", "detail": "d",
                       "comment_ids": ["linkedin:1", "ZZZ"]}],
            "notable_comments": [{"comment_id": "ZZZ", "why": "invented"}],
        }
        out = T._clean_reaction_analysis(parsed, {"linkedin:1"})
        assert out["themes"][0]["comment_ids"] == ["linkedin:1"]
        assert out["notable_comments"] == []

    def test_em_dashes_are_stripped_from_every_free_text_field(self):
        parsed = {
            "verdict": "Positive overall — no real backlash.",
            "comment_sentiment": {"x:1": "positive"},
            "themes": [{"label": "Praise", "stance": "praise",
                       "detail": "People loved it — especially the demo.",
                       "comment_ids": ["x:1"]}],
            "notable_comments": [{"comment_id": "x:1", "why": "Widely liked — top reply."}],
        }
        out = T._clean_reaction_analysis(parsed, {"x:1"})
        assert "—" not in out["verdict"]
        assert "—" not in out["themes"][0]["detail"]
        assert "—" not in out["notable_comments"][0]["why"]


class TestAnalyzeCommentSentiment:
    def test_no_comments_is_refused(self):
        assert "error" in T.analyze_comment_sentiment([])

    def test_missing_api_key_is_reported(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out = T.analyze_comment_sentiment([{"platform": "x", "comment_id": "1", "text": "hi", "likes": 1}])
        assert "ANTHROPIC_API_KEY" in out["error"]


class TestCollectReactionJob:
    def _identity_run(self, posts=None):
        return {"input_name": "Jane Doe",
               "identity": {"full_name": "Jane Doe", "current_company": "Acme"},
               "posts": posts or {}}

    def test_no_run_or_identity_saves_a_failed_state(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: None)
        captured = {}
        monkeypatch.setattr(T, "save_reaction_failed", lambda run_id, email, msg: captured.update(
            run_id=run_id, msg=msg) or True)
        monkeypatch.setattr(T, "save_reaction", lambda *a, **kw: pytest.fail("must not save a partial reaction"))
        T.collect_reaction_job(5, "a@b.com")
        assert captured["run_id"] == 5 and "no confirmed identity" in captured["msg"]

    def test_combines_comments_and_reddit_and_saves_ready(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([{"platform": "linkedin"}], None))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], "No X posts to read replies from."))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([{"platform": "youtube"}], None))
        monkeypatch.setattr(T, "analyze_comment_sentiment", lambda comments: {"verdict": "ok"})
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", lambda name, hint: {"thread_count": 3})

        captured = {}
        monkeypatch.setattr(T, "save_reaction", lambda run_id, email, reaction, errors: captured.update(
            reaction=reaction, errors=errors) or True)
        T.collect_reaction_job(9, "a@b.com")

        assert captured["errors"] == {"x": "No X posts to read replies from."}
        assert captured["reaction"]["comments_analyzed"] == 2
        assert captured["reaction"]["comment_sentiment"] == {"verdict": "ok"}
        assert captured["reaction"]["reddit"] == {"thread_count": 3}

    def test_a_reddit_pulse_crash_degrades_rather_than_failing_the_whole_run(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([], "x"))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], "x"))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([], "x"))

        def boom(name, hint):
            raise RuntimeError("reddit api down")
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", boom)
        captured = {}
        monkeypatch.setattr(T, "save_reaction", lambda run_id, email, reaction, errors: captured.update(
            reaction=reaction) or True)
        T.collect_reaction_job(1, "a@b.com")
        assert captured["reaction"]["reddit"]["thread_count"] == 0

    def test_an_unexpected_crash_still_reaches_a_terminal_state(self, monkeypatch):
        def boom(run_id, email):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(T, "get_run", boom)
        captured = {}
        monkeypatch.setattr(T, "save_reaction_failed", lambda run_id, email, msg: captured.update(msg=msg) or True)
        T.collect_reaction_job(3, "a@b.com")
        assert "unexpected error" in captured["msg"]
