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
    for key in ("APOLLO_API_KEY", "YOUTUBE_API_KEY", "DATABASE_URL", "APIFY_API_TOKEN",
               "UNIPILE_API_KEY", "UNIPILE_DSN", "ANTHROPIC_API_KEY"):
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
            '"instagram_handle":"@janedoe",'
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
        # a leading @ on the model's own instagram_handle is stripped, same
        # as x_handle
        assert identity["platforms"]["instagram"] == {
            "handle": "janedoe", "url": "https://www.instagram.com/janedoe/", "resolved": True}
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
        # no instagram_handle in the model's reply degrades to unresolved,
        # never a hard failure
        assert out["identity"]["platforms"]["instagram"] == {
            "handle": None, "url": None, "resolved": False}


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

    def test_a_normalize_crash_is_caught_here_not_left_to_escape(self, monkeypatch):
        """Regression test for the real production failure: fetch_posts()
        succeeds (raw data came back fine), but normalize() -- which runs
        AFTER the transport call, outside its UnipileTransportError guard --
        chokes on the shape of that particular person's data. Before this
        fix, this exception was not a UnipileTransportError, so it escaped
        _collect_linkedin_posts entirely and crashed collect_posts_job for
        ALL THREE platforms with a generic 'unexpected error', reproducing
        identically on every 'Try again' click."""
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(T.unipile_transport, "fetch_posts", lambda *a, **kw: [{"id": "p1"}])
        def boom(raw):
            raise AttributeError("'NoneType' object has no attribute 'get'")
        monkeypatch.setattr(T.sci_source_linkedin_unipile, "normalize", boom)
        posts, err = T._collect_linkedin_posts({"provider_id": "urn:li:member:1"}, 20)
        assert posts == []
        assert "LinkedIn" in err and "AttributeError" in err


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

    def test_a_normalize_crash_inside_collect_is_caught_here_not_left_to_escape(self, monkeypatch):
        """Same regression class as LinkedIn's: sci_source_x.collect() calls
        normalize() after its own transport call succeeds, so a malformed
        item there raises something other than ApifyTransportError and, pre-
        fix, escaped this function's narrow except entirely."""
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        def boom(handle, token, max_posts=25, strict=True):
            raise KeyError("id")
        monkeypatch.setattr(T.sci_source_x, "collect", boom)
        posts, err = T._collect_x_posts({"handle": "janedoe"}, 20)
        assert posts == []
        assert "X" in err and "KeyError" in err


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
        assert "kaboom" in captured["msg"]

    def test_one_platforms_normalize_crash_no_longer_takes_down_the_whole_run(self, monkeypatch):
        """The exact production scenario this fix addresses: LinkedIn's raw
        response normalizes fine for most people but raises for this one
        (a real, not hypothetical, crash -- see TestCollectLinkedInPosts's
        own regression test for the same bug one layer down). Before the
        fix, collect_posts_job had no way to know that exception came from
        deep inside a single platform's helper -- it looked exactly like
        collect_posts_job itself crashing, so save_posts_failed wiped out
        X's and YouTube's already-collected posts too."""
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity(
            linkedin={"provider_id": "p1"}, x={"handle": "h1"}, youtube={"channel_id": "c1"}))
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(T.unipile_transport, "fetch_posts", lambda *a, **kw: [{"id": "p1"}])
        monkeypatch.setattr(T.sci_source_linkedin_unipile, "normalize",
                            lambda raw: (_ for _ in ()).throw(AttributeError("boom")))
        monkeypatch.setattr(T, "_collect_x_posts", lambda p, n: ([{"platform_post_id": "x1"}], None))
        monkeypatch.setattr(T, "_collect_youtube_posts", lambda p, n: ([{"platform_post_id": "y1"}], None))
        captured = {}
        monkeypatch.setattr(T, "save_posts", lambda run_id, email, posts, errors: captured.update(
            posts=posts, errors=errors) or True)
        monkeypatch.setattr(T, "save_posts_failed",
                            lambda *a, **kw: pytest.fail("must not blank-fail the whole run"))
        T.collect_posts_job(11, "a@b.com")
        assert "LinkedIn" in captured["errors"]["linkedin"]
        assert captured["posts"]["x"] == [{"platform_post_id": "x1"}]
        assert captured["posts"]["youtube"] == [{"platform_post_id": "y1"}]


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

    def test_a_normalize_crash_for_one_post_does_not_block_the_others(self, monkeypatch):
        """Same regression class as sci_source_x.collect() itself:
        apify_x_replies.collect() normalizes after its transport call
        succeeds, so a crash there is not an ApifyTransportError and, pre-
        fix, escaped this per-post try/except and crashed the whole
        reaction job over one tweet's malformed replies."""
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        def fake_collect(tweet_id, token, max_replies=20, strict=True):
            if tweet_id == "bad":
                raise TypeError("'NoneType' object is not iterable")
            return [{"comment_id": "r1", "text": "nice", "author": "alex",
                    "posted_at": "2026-09-01", "likes": 3}]
        monkeypatch.setattr(T.apify_x_replies, "collect", fake_collect)
        posts = [{"platform_post_id": "bad", "metrics": {"likes": 999}},
                {"platform_post_id": "good", "metrics": {"likes": 1}}]
        out, err = T._collect_x_replies(posts, 5, 20)
        assert err is None
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

    def _stub_new_pulses(self, monkeypatch):
        """LinkedIn/TikTok/Instagram/Facebook pulses stubbed to a harmless
        default so a test about the ORIGINAL three sources isn't also
        exercising these four new real vendor call chains -- same reasoning
        as _stub_platforms in TestResolveIdentityHappyPath."""
        monkeypatch.setattr(T.tlpr_linkedin_pulse, "build_pulse", lambda name, provider_id=None: {"post_count": 0})
        monkeypatch.setattr(T.tlpr_tiktok_pulse, "build_pulse", lambda name: {"video_count": 0})
        monkeypatch.setattr(T.tlpr_instagram_pulse, "build_pulse",
                            lambda name, instagram_handle=None: {"mention_count": 0})
        monkeypatch.setattr(T.tlpr_facebook_pulse, "build_pulse", lambda name: {"post_count": 0})

    def test_no_run_or_identity_saves_a_failed_state(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: None)
        captured = {}
        monkeypatch.setattr(T, "save_reaction_failed", lambda run_id, email, msg: captured.update(
            run_id=run_id, msg=msg) or True)
        monkeypatch.setattr(T, "save_reaction", lambda *a, **kw: pytest.fail("must not save a partial reaction"))
        T.collect_reaction_job(5, "a@b.com")
        assert captured["run_id"] == 5 and "no confirmed identity" in captured["msg"]

    def test_combines_comments_and_reddit_and_x_pulse_and_saves_ready(self, monkeypatch):
        self._stub_new_pulses(monkeypatch)
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([{"platform": "linkedin"}], None))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], "No X posts to read replies from."))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([{"platform": "youtube"}], None))
        monkeypatch.setattr(T, "analyze_comment_sentiment", lambda comments: {"verdict": "ok"})
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", lambda name, hint: {"thread_count": 3})
        captured_x_call = {}
        monkeypatch.setattr(T.tlpr_x_pulse, "build_pulse", lambda name, handle: captured_x_call.update(
            name=name, handle=handle) or {"tweet_count": 7})

        captured = {}
        monkeypatch.setattr(T, "save_reaction", lambda run_id, email, reaction, errors: captured.update(
            reaction=reaction, errors=errors) or True)
        T.collect_reaction_job(9, "a@b.com")

        assert captured["errors"] == {"x": "No X posts to read replies from."}
        assert captured["reaction"]["comments_analyzed"] == 2
        assert captured["reaction"]["comment_sentiment"] == {"verdict": "ok"}
        assert captured["reaction"]["reddit"] == {"thread_count": 3}
        assert captured["reaction"]["x_pulse"] == {"tweet_count": 7}
        assert captured_x_call == {"name": "Jane Doe", "handle": None}

    def test_x_pulse_is_called_with_the_resolved_handle(self, monkeypatch):
        self._stub_new_pulses(monkeypatch)
        run = self._identity_run()
        run["identity"]["platforms"] = {"x": {"handle": "janedoe", "resolved": True}}
        monkeypatch.setattr(T, "get_run", lambda run_id, email: run)
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", lambda name, hint: {"thread_count": 0})
        captured_x_call = {}
        monkeypatch.setattr(T.tlpr_x_pulse, "build_pulse", lambda name, handle: captured_x_call.update(
            handle=handle) or {"tweet_count": 0})
        monkeypatch.setattr(T, "save_reaction", lambda *a, **kw: True)
        T.collect_reaction_job(9, "a@b.com")
        assert captured_x_call["handle"] == "janedoe"

    def test_a_reddit_pulse_crash_degrades_rather_than_failing_the_whole_run(self, monkeypatch):
        self._stub_new_pulses(monkeypatch)
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([], "x"))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], "x"))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([], "x"))
        monkeypatch.setattr(T.tlpr_x_pulse, "build_pulse", lambda name, handle: {"tweet_count": 0})

        def boom(name, hint):
            raise RuntimeError("reddit api down")
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", boom)
        captured = {}
        monkeypatch.setattr(T, "save_reaction", lambda run_id, email, reaction, errors: captured.update(
            reaction=reaction) or True)
        T.collect_reaction_job(1, "a@b.com")
        assert captured["reaction"]["reddit"]["thread_count"] == 0

    def test_an_x_pulse_crash_degrades_rather_than_failing_the_whole_run(self, monkeypatch):
        self._stub_new_pulses(monkeypatch)
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([], "x"))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], "x"))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([], "x"))
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", lambda name, hint: {"thread_count": 0})

        def boom(name, handle):
            raise RuntimeError("apify down")
        monkeypatch.setattr(T.tlpr_x_pulse, "build_pulse", boom)
        captured = {}
        monkeypatch.setattr(T, "save_reaction", lambda run_id, email, reaction, errors: captured.update(
            reaction=reaction) or True)
        T.collect_reaction_job(1, "a@b.com")
        assert captured["reaction"]["x_pulse"]["tweet_count"] == 0

    def test_linkedin_pulse_is_called_with_the_resolved_provider_id(self, monkeypatch):
        run = self._identity_run()
        run["identity"]["platforms"] = {"linkedin": {"provider_id": "urn:li:member:123"}}
        monkeypatch.setattr(T, "get_run", lambda run_id, email: run)
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", lambda name, hint: {"thread_count": 0})
        monkeypatch.setattr(T.tlpr_x_pulse, "build_pulse", lambda name, handle: {"tweet_count": 0})
        monkeypatch.setattr(T.tlpr_tiktok_pulse, "build_pulse", lambda name: {"video_count": 0})
        monkeypatch.setattr(T.tlpr_instagram_pulse, "build_pulse",
                            lambda name, instagram_handle=None: {"mention_count": 0})
        monkeypatch.setattr(T.tlpr_facebook_pulse, "build_pulse", lambda name: {"post_count": 0})
        captured_call = {}
        monkeypatch.setattr(T.tlpr_linkedin_pulse, "build_pulse", lambda name, provider_id=None:
                            captured_call.update(provider_id=provider_id) or {"post_count": 0})
        monkeypatch.setattr(T, "save_reaction", lambda *a, **kw: True)
        T.collect_reaction_job(9, "a@b.com")
        assert captured_call["provider_id"] == "urn:li:member:123"

    def test_instagram_pulse_is_called_with_the_resolved_handle(self, monkeypatch):
        run = self._identity_run()
        run["identity"]["platforms"] = {"instagram": {"handle": "janedoe"}}
        monkeypatch.setattr(T, "get_run", lambda run_id, email: run)
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", lambda name, hint: {"thread_count": 0})
        monkeypatch.setattr(T.tlpr_x_pulse, "build_pulse", lambda name, handle: {"tweet_count": 0})
        monkeypatch.setattr(T.tlpr_linkedin_pulse, "build_pulse", lambda name, provider_id=None: {"post_count": 0})
        monkeypatch.setattr(T.tlpr_tiktok_pulse, "build_pulse", lambda name: {"video_count": 0})
        monkeypatch.setattr(T.tlpr_facebook_pulse, "build_pulse", lambda name: {"post_count": 0})
        captured_call = {}
        monkeypatch.setattr(T.tlpr_instagram_pulse, "build_pulse", lambda name, instagram_handle=None:
                            captured_call.update(instagram_handle=instagram_handle) or {"mention_count": 0})
        monkeypatch.setattr(T, "save_reaction", lambda *a, **kw: True)
        T.collect_reaction_job(9, "a@b.com")
        assert captured_call["instagram_handle"] == "janedoe"

    def test_all_four_new_pulses_land_in_the_saved_reaction(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", lambda name, hint: {"thread_count": 0})
        monkeypatch.setattr(T.tlpr_x_pulse, "build_pulse", lambda name, handle: {"tweet_count": 0})
        monkeypatch.setattr(T.tlpr_linkedin_pulse, "build_pulse", lambda name, provider_id=None: {"post_count": 1})
        monkeypatch.setattr(T.tlpr_tiktok_pulse, "build_pulse", lambda name: {"video_count": 2})
        monkeypatch.setattr(T.tlpr_instagram_pulse, "build_pulse",
                            lambda name, instagram_handle=None: {"mention_count": 3})
        monkeypatch.setattr(T.tlpr_facebook_pulse, "build_pulse", lambda name: {"post_count": 4})
        captured = {}
        monkeypatch.setattr(T, "save_reaction", lambda run_id, email, reaction, errors: captured.update(
            reaction=reaction) or True)
        T.collect_reaction_job(9, "a@b.com")
        assert captured["reaction"]["linkedin_pulse"] == {"post_count": 1}
        assert captured["reaction"]["tiktok_pulse"] == {"video_count": 2}
        assert captured["reaction"]["instagram_pulse"] == {"mention_count": 3}
        assert captured["reaction"]["facebook_pulse"] == {"post_count": 4}

    def test_each_new_pulse_crashing_degrades_rather_than_failing_the_whole_run(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T, "_collect_linkedin_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_x_replies", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T, "_collect_youtube_comments", lambda p, mp, mc: ([], None))
        monkeypatch.setattr(T.tlpr_reddit_pulse, "build_pulse", lambda name, hint: {"thread_count": 0})
        monkeypatch.setattr(T.tlpr_x_pulse, "build_pulse", lambda name, handle: {"tweet_count": 0})

        def boom(*a, **kw):
            raise RuntimeError("vendor down")
        monkeypatch.setattr(T.tlpr_linkedin_pulse, "build_pulse", boom)
        monkeypatch.setattr(T.tlpr_tiktok_pulse, "build_pulse", boom)
        monkeypatch.setattr(T.tlpr_instagram_pulse, "build_pulse", boom)
        monkeypatch.setattr(T.tlpr_facebook_pulse, "build_pulse", boom)
        captured = {}
        monkeypatch.setattr(T, "save_reaction", lambda run_id, email, reaction, errors: captured.update(
            reaction=reaction) or True)
        T.collect_reaction_job(9, "a@b.com")
        assert captured["reaction"]["linkedin_pulse"]["post_count"] == 0
        assert captured["reaction"]["tiktok_pulse"]["video_count"] == 0
        assert captured["reaction"]["instagram_pulse"]["mention_count"] == 0
        assert captured["reaction"]["facebook_pulse"]["post_count"] == 0

    def test_an_unexpected_crash_still_reaches_a_terminal_state(self, monkeypatch):
        def boom(run_id, email):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(T, "get_run", boom)
        captured = {}
        monkeypatch.setattr(T, "save_reaction_failed", lambda run_id, email, msg: captured.update(msg=msg) or True)
        T.collect_reaction_job(3, "a@b.com")
        assert "unexpected error" in captured["msg"]


class TestPhase3StoreFailSoft:
    def test_start_press_returns_false(self):
        assert T.start_press(1, "a@b.com") is False

    def test_save_press_returns_false(self):
        assert T.save_press(1, "a@b.com", {}, {}) is False

    def test_save_press_failed_returns_false(self):
        assert T.save_press_failed(1, "a@b.com", "boom") is False


class TestResolveStalePress:
    def test_a_fresh_collecting_run_is_left_alone(self):
        run = {"id": 1, "press_status": "collecting",
              "updated_at": T.datetime.now(T.timezone.utc).isoformat()}
        assert T._resolve_stale_press(run, "a@b.com")["press_status"] == "collecting"

    def test_a_stale_collecting_run_is_flipped_to_failed(self, monkeypatch):
        stale_time = (T.datetime.now(T.timezone.utc) - T.timedelta(minutes=T.STALE_RUN_MINUTES + 1)).isoformat()
        run = {"id": 1, "press_status": "collecting", "updated_at": stale_time}
        monkeypatch.setattr(T, "save_press_failed", lambda *a, **kw: True)
        out = T._resolve_stale_press(run, "a@b.com")
        assert out["press_status"] == "failed"
        assert "_run" in out["press_errors"]

    def test_a_stuck_reaction_analysis_never_strands_the_press_poll(self):
        """The three 'collecting' states are independent columns -- a
        reaction analysis stuck mid-flight must not make _resolve_stale_press
        think there is a press job to time out."""
        run = {"id": 1, "reaction_status": "collecting", "press_status": "idle",
              "updated_at": "2020-01-01T00:00:00+00:00"}
        assert T._resolve_stale_press(run, "a@b.com") == run


class TestCollectPressJob:
    def _identity_run(self):
        return {"input_name": "Jane Doe",
               "identity": {"full_name": "Jane Doe", "current_company": "Acme"}}

    def test_no_run_or_identity_saves_a_failed_state(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: None)
        captured = {}
        monkeypatch.setattr(T, "save_press_failed", lambda run_id, email, msg: captured.update(
            run_id=run_id, msg=msg) or True)
        monkeypatch.setattr(T, "save_press", lambda *a, **kw: pytest.fail("must not save a partial press result"))
        T.collect_press_job(5, "a@b.com")
        assert captured["run_id"] == 5 and "no confirmed identity" in captured["msg"]

    def test_builds_press_and_saves_ready_with_its_errors_split_out(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T.tlpr_press, "build_press", lambda name, hint: {
            "article_count": 3, "errors": {"serpapi": "not configured"}})

        captured = {}
        monkeypatch.setattr(T, "save_press", lambda run_id, email, press_data, errors: captured.update(
            press_data=press_data, errors=errors) or True)
        T.collect_press_job(9, "a@b.com")

        assert captured["errors"] == {"serpapi": "not configured"}
        assert captured["press_data"] == {"article_count": 3}

    def test_a_build_press_call_with_no_errors_key_saves_an_empty_errors_dict(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: self._identity_run())
        monkeypatch.setattr(T.tlpr_press, "build_press", lambda name, hint: {"article_count": 0})
        captured = {}
        monkeypatch.setattr(T, "save_press", lambda run_id, email, press_data, errors: captured.update(
            errors=errors) or True)
        T.collect_press_job(9, "a@b.com")
        assert captured["errors"] == {}

    def test_an_unexpected_crash_still_reaches_a_terminal_state(self, monkeypatch):
        def boom(run_id, email):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(T, "get_run", boom)
        captured = {}
        monkeypatch.setattr(T, "save_press_failed", lambda run_id, email, msg: captured.update(msg=msg) or True)
        T.collect_press_job(3, "a@b.com")
        assert "unexpected error" in captured["msg"]


class TestPhase4StoreFailSoft:
    def test_start_synthesizing_returns_false(self):
        assert T.start_synthesizing(1, "a@b.com") is False

    def test_save_synthesis_returns_false(self):
        assert T.save_synthesis(1, "a@b.com", {}, {}) is False

    def test_save_synthesis_failed_returns_false(self):
        assert T.save_synthesis_failed(1, "a@b.com", "boom") is False


class TestResolveStaleSynthesis:
    def test_a_fresh_collecting_run_is_left_alone(self):
        run = {"id": 1, "synthesis_status": "collecting",
              "updated_at": T.datetime.now(T.timezone.utc).isoformat()}
        assert T._resolve_stale_synthesis(run, "a@b.com")["synthesis_status"] == "collecting"

    def test_a_stale_collecting_run_is_flipped_to_failed(self, monkeypatch):
        stale_time = (T.datetime.now(T.timezone.utc) - T.timedelta(minutes=T.STALE_RUN_MINUTES + 1)).isoformat()
        run = {"id": 1, "synthesis_status": "collecting", "updated_at": stale_time}
        monkeypatch.setattr(T, "save_synthesis_failed", lambda *a, **kw: True)
        out = T._resolve_stale_synthesis(run, "a@b.com")
        assert out["synthesis_status"] == "failed"
        assert "_run" in out["synthesis_errors"]

    def test_a_stuck_press_search_never_strands_the_synthesis_poll(self):
        """The four 'collecting' states are independent columns -- a press
        search stuck mid-flight must not make _resolve_stale_synthesis
        think there is a synthesis job to time out."""
        run = {"id": 1, "press_status": "collecting", "synthesis_status": "idle",
              "updated_at": "2020-01-01T00:00:00+00:00"}
        assert T._resolve_stale_synthesis(run, "a@b.com") == run


class TestPostsSummary:
    def test_no_posts_at_all_is_unavailable(self):
        assert T._posts_summary(None)["available"] is False
        assert T._posts_summary({})["available"] is False

    def test_an_empty_per_platform_result_is_also_unavailable(self):
        assert T._posts_summary({"linkedin": [], "x": [], "youtube": []})["available"] is False

    def test_counts_and_engagement_are_computed_per_platform(self):
        posts = {"linkedin": [{"metrics": {"likes": 5, "comments": 2}},
                              {"metrics": {"likes": 3}}],
                "x": [], "youtube": []}
        out = T._posts_summary(posts)
        assert out["available"] is True
        assert out["by_platform"]["linkedin"] == {"count": 2, "total_engagement": 10}
        assert out["by_platform"]["x"] == {"count": 0, "total_engagement": 0}


class TestReactionSummary:
    def test_no_reaction_is_unavailable(self):
        assert T._reaction_summary(None) == {"available": False}

    def test_nothing_analyzed_is_unavailable(self):
        assert T._reaction_summary({"comments_analyzed": 0, "reddit": {"thread_count": 0}}
                                   )["available"] is False

    def test_an_errored_comment_sentiment_is_omitted_not_faked(self):
        out = T._reaction_summary({"comments_analyzed": 3,
                                   "comment_sentiment": {"error": "boom"}, "reddit": {}})
        assert out["available"] is True
        assert out["own_post_comments"] is None

    def test_real_findings_are_pulled_through(self):
        reaction = {
            "comments_analyzed": 4,
            "comment_sentiment": {"verdict": "Warm.", "sentiment": {"counts": {"positive": 3}},
                                  "themes": [{"label": "Praise"}]},
            "reddit": {"thread_count": 2,
                      "analysis": {"verdict": "Mixed.", "sentiment": {"counts": {"negative": 1}},
                                  "themes": [{"label": "Skepticism"}], "risk_flags": ["A gripe."]}},
        }
        out = T._reaction_summary(reaction)
        assert out["own_post_comments"]["verdict"] == "Warm."
        assert out["own_post_comments"]["themes"] == ["Praise"]
        assert out["reddit"]["verdict"] == "Mixed."
        assert out["reddit"]["risk_flags"] == ["A gripe."]


class TestPressSummary:
    def test_no_articles_is_unavailable(self):
        assert T._press_summary(None) == {"available": False}
        assert T._press_summary({"article_count": 0}) == {"available": False}

    def test_an_errored_analysis_still_reports_the_raw_counts(self):
        out = T._press_summary({"article_count": 5, "source_count": 3, "analysis": {"error": "boom"}})
        assert out == {"available": True, "article_count": 5, "source_count": 3}

    def test_real_findings_are_pulled_through(self):
        press = {"article_count": 7, "source_count": 4,
                 "analysis": {"verdict": "Well covered.", "sentiment": {"counts": {"positive": 5}},
                             "themes": [{"label": "Growth marketing"}], "risk_flags": ["ROI scrutiny."]}}
        out = T._press_summary(press)
        assert out["verdict"] == "Well covered."
        assert out["themes"] == ["Growth marketing"]
        assert out["risk_flags"] == ["ROI scrutiny."]


class TestCleanSynthesis:
    def test_strengths_and_risks_keep_only_known_source_tags(self):
        parsed = {"headline": "H", "verdict": "V", "alignment": "A",
                  "strengths": [{"text": "Widely respected.", "sources": ["press", "made_up"]}],
                  "risks": []}
        out = T._clean_synthesis(parsed)
        assert out["strengths"] == [{"text": "Widely respected.", "sources": ["press"]}]

    def test_a_blank_bullet_text_is_dropped(self):
        parsed = {"headline": "H", "verdict": "V", "alignment": "A",
                  "strengths": [{"text": "   ", "sources": ["press"]}], "risks": []}
        assert T._clean_synthesis(parsed)["strengths"] == []

    def test_em_dashes_are_stripped_from_every_free_text_field(self):
        parsed = {
            "headline": "A rising voice — with one open question",
            "verdict": "Well regarded — though one campaign is under scrutiny.",
            "alignment": "Self-presentation matches perception — mostly.",
            "strengths": [{"text": "Cited widely — a real authority.", "sources": ["press"]}],
            "risks": [{"text": "One unverified claim — worth watching.", "sources": ["press"]}],
        }
        out = T._clean_synthesis(parsed)
        assert "—" not in out["headline"]
        assert "—" not in out["verdict"]
        assert "—" not in out["alignment"]
        assert "—" not in out["strengths"][0]["text"]
        assert "—" not in out["risks"][0]["text"]

    def test_lists_are_capped_at_five(self):
        parsed = {"headline": "H", "verdict": "V", "alignment": "A",
                  "strengths": [{"text": "s%d" % i, "sources": []} for i in range(8)], "risks": []}
        assert len(T._clean_synthesis(parsed)["strengths"]) == 5


class TestExtractJsonObject:
    def test_the_json_scan_survives_a_brace_inside_a_string(self):
        raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
        assert T._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


class TestSynthesizeReport:
    def test_no_identity_is_refused(self):
        out = T.synthesize_report({"identity": None})
        assert "error" in out

    def test_missing_api_key_is_reported(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out = T.synthesize_report({"identity": {"full_name": "Jane Doe"}})
        assert "ANTHROPIC_API_KEY" in out["error"]


class TestCollectSynthesisJob:
    def test_no_run_or_identity_saves_a_failed_state(self, monkeypatch):
        monkeypatch.setattr(T, "get_run", lambda run_id, email: None)
        captured = {}
        monkeypatch.setattr(T, "save_synthesis_failed", lambda run_id, email, msg: captured.update(
            run_id=run_id, msg=msg) or True)
        monkeypatch.setattr(T, "save_synthesis", lambda *a, **kw: pytest.fail("must not save a partial report"))
        T.collect_synthesis_job(5, "a@b.com")
        assert captured["run_id"] == 5 and "no confirmed identity" in captured["msg"]

    def test_a_real_report_is_saved_as_ready(self, monkeypatch):
        run = {"input_name": "Jane Doe", "identity": {"full_name": "Jane Doe"}}
        monkeypatch.setattr(T, "get_run", lambda run_id, email: run)
        monkeypatch.setattr(T, "synthesize_report", lambda r: {"headline": "ok", "verdict": "v"})
        captured = {}
        monkeypatch.setattr(T, "save_synthesis", lambda run_id, email, report, errors: captured.update(
            report=report, errors=errors) or True)
        T.collect_synthesis_job(9, "a@b.com")
        assert captured["report"] == {"headline": "ok", "verdict": "v"}
        assert captured["errors"] == {}

    def test_an_error_result_is_saved_as_failed_not_ready(self, monkeypatch):
        run = {"input_name": "Jane Doe", "identity": {"full_name": "Jane Doe"}}
        monkeypatch.setattr(T, "get_run", lambda run_id, email: run)
        monkeypatch.setattr(T, "synthesize_report", lambda r: {"error": "vendor call failed"})
        captured = {}
        monkeypatch.setattr(T, "save_synthesis_failed", lambda run_id, email, msg: captured.update(msg=msg) or True)
        monkeypatch.setattr(T, "save_synthesis", lambda *a, **kw: pytest.fail("an error result must not be saved as ready"))
        T.collect_synthesis_job(9, "a@b.com")
        assert captured["msg"] == "vendor call failed"

    def test_an_unexpected_crash_still_reaches_a_terminal_state(self, monkeypatch):
        def boom(run_id, email):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(T, "get_run", boom)
        captured = {}
        monkeypatch.setattr(T, "save_synthesis_failed", lambda run_id, email, msg: captured.update(msg=msg) or True)
        T.collect_synthesis_job(3, "a@b.com")
        assert "unexpected error" in captured["msg"]


class TestSearchNameCandidates:
    """The cheap Apollo-only picker shown as the user types a name, ahead of
    resolve_identity's own paid websearch call -- same shape as Social Media
    Intelligence's /search ahead of /analyze."""

    def test_a_blank_name_searches_nothing(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        monkeypatch.setattr(T.apollo_client, "search_people",
                            lambda *a, **kw: pytest.fail("must not call Apollo for a blank name"))
        candidates, error = T.search_name_candidates("")
        assert candidates == [] and error is None

    def test_no_api_key_reports_a_typed_not_configured_error(self):
        candidates, error = T.search_name_candidates("Jane Doe")
        assert candidates == []
        assert error == {"code": "not_configured",
                         "message": "Apollo is not configured on this deployment."}

    def test_multiple_apollo_rows_become_real_candidates(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        rows = [
            {"full_name": "Jane Doe", "title": "CEO", "organization_name": "Acme Corp",
             "city": "Austin", "state": "TX", "country": "US",
             "photo_url": "https://img.example/1.jpg", "linkedin_url": "https://linkedin.com/in/jane1"},
            {"full_name": "Jane Doe", "title": "VP Marketing", "organization_name": "Globex",
             "city": None, "state": None, "country": "UK",
             "photo_url": None, "linkedin_url": "https://linkedin.com/in/jane2"},
        ]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        candidates, error = T.search_name_candidates("Jane Doe")
        assert error is None
        assert len(candidates) == 2
        assert candidates[0] == {
            "full_name": "Jane Doe", "title": "CEO", "company": "Acme Corp",
            "location": "Austin, TX, US", "photo_url": "https://img.example/1.jpg",
            "linkedin_url": "https://linkedin.com/in/jane1",
        }
        assert candidates[1]["location"] == "UK"

    def test_a_company_hint_is_folded_into_the_apollo_keywords(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        captured = {}
        def fake_search(filters, api_key, per_page=None):
            captured["filters"] = filters
            return []
        monkeypatch.setattr(T.apollo_client, "search_people", fake_search)
        T.search_name_candidates("Jane Doe", company_hint="Acme")
        assert captured["filters"]["keywords"] == "Jane Doe Acme"

    def test_a_duplicate_name_and_company_pair_is_collapsed(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        rows = [
            {"full_name": "Jane Doe", "organization_name": "Acme Corp"},
            {"full_name": "Jane Doe", "organization_name": "Acme Corp"},
        ]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        candidates, error = T.search_name_candidates("Jane Doe")
        assert len(candidates) == 1

    def test_a_row_with_no_name_is_dropped_not_crashed_on(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        monkeypatch.setattr(T.apollo_client, "search_people",
                            lambda *a, **kw: [{"full_name": "", "organization_name": "Acme"}])
        candidates, error = T.search_name_candidates("Jane Doe")
        assert candidates == [] and error is None

    def test_an_apollo_crash_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        def boom(*a, **kw):
            raise RuntimeError("apollo is down")
        monkeypatch.setattr(T.apollo_client, "search_people", boom)
        candidates, error = T.search_name_candidates("Jane Doe")
        assert candidates == []
        assert error["code"] == "error"

    def test_rows_whose_own_name_does_not_match_are_dropped(self, monkeypatch):
        """Regression test for a real incident: searching "rahul gandhi"
        returned six Apollo rows, none of them actually named Rahul Gandhi
        -- Apollo's `keywords` filter matched loosely on a company name
        ("Rahul Traders"), a title ("Gandhi Fellow"), or an employer
        ("Indira Gandhi ..."), not the person's own name. Exact rows from
        that real response, reproduced here."""
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        rows = [
            {"full_name": "Gandhi", "title": "Owner", "organization_name": "Rahul Traders"},
            {"full_name": "Murali Rahul", "title": "Doctor", "organization_name": "Gandhi Hospital"},
            {"full_name": "Rahul Bharat", "title": "Rahul Gandhi", "organization_name": "Bharat"},
            {"full_name": "Rahul Kumar", "title": "Gandhi Fellow", "organization_name": "Piramal Foundation"},
            {"full_name": "Rahul Upadhyay", "title": "PhD Scholar", "organization_name": "Indira Gandhi ..."},
        ]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        candidates, error = T.search_name_candidates("rahul gandhi", company_hint="Congress leader")
        assert error is None
        assert candidates == []

    def test_a_row_matching_both_name_words_is_kept(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        rows = [{"full_name": "Rahul Gandhi", "title": "Member of Parliament",
                "organization_name": "Indian National Congress"}]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        candidates, error = T.search_name_candidates("rahul gandhi")
        assert error is None
        assert len(candidates) == 1
        assert candidates[0]["full_name"] == "Rahul Gandhi"


class TestLooksLikeSamePerson:
    def test_exact_name_matches(self):
        assert T._looks_like_same_person("Rahul Gandhi", "Rahul Gandhi") is True

    def test_an_extra_middle_name_still_matches(self):
        assert T._looks_like_same_person("Elon Musk", "Elon Reeve Musk") is True

    def test_surname_only_does_not_match(self):
        assert T._looks_like_same_person("Rahul Gandhi", "Gandhi") is False

    def test_shared_given_name_alone_does_not_match(self):
        assert T._looks_like_same_person("Rahul Gandhi", "Rahul Kumar") is False

    def test_no_overlap_does_not_match(self):
        assert T._looks_like_same_person("Rahul Gandhi", "Priya Sharma") is False

    def test_blank_candidate_name_does_not_match(self):
        assert T._looks_like_same_person("Rahul Gandhi", "") is False


class TestBestApolloCandidate:
    def test_an_exact_name_match_is_returned(self, monkeypatch):
        rows = [{"full_name": "Jane Doe", "title": "CEO", "organization_name": "Acme"}]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        cand = T._best_apollo_candidate("Jane Doe", None, None, "test-key")
        assert cand == rows[0]

    def test_a_given_linkedin_url_is_trusted_even_if_the_name_field_differs(self, monkeypatch):
        """An exact linkedin_urls lookup is authoritative on the URL the
        caller themselves supplied -- Apollo's own full_name field (a
        nickname, maiden name, or a slight mismatch) must not veto it."""
        rows = [{"full_name": "J. Doe", "linkedin_url": "https://linkedin.com/in/janedoe"}]
        captured = {}
        def fake_search(filters, api_key, per_page=None):
            captured["filters"] = filters
            return rows
        monkeypatch.setattr(T.apollo_client, "search_people", fake_search)
        cand = T._best_apollo_candidate("Jane Doe", None, "https://linkedin.com/in/janedoe", "test-key")
        assert cand == rows[0]
        assert captured["filters"]["linkedin_urls"] == ["https://linkedin.com/in/janedoe"]

    def test_no_plausible_candidate_returns_none_not_the_top_junk_hit(self, monkeypatch):
        """Regression test for the real "Rahul Gandhi" incident: before this
        fix, _best_apollo_candidate returned candidates[0] unconditionally
        whenever no EXACT match existed, handing a completely unrelated
        person to resolve_identity's grounding prompt as "a business
        database suggests this may be [them]." That candidate then
        anchored identity resolution onto the wrong person instead of the
        real, well-known public figure being searched."""
        rows = [{"full_name": "Gandhi", "title": "Owner", "organization_name": "Rahul Traders"},
               {"full_name": "Murali Rahul", "title": "Doctor", "organization_name": "Gandhi Hospital"}]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        cand = T._best_apollo_candidate("Rahul Gandhi", "Congress leader", None, "test-key")
        assert cand is None

    def test_a_plausible_non_exact_candidate_is_still_offered(self, monkeypatch):
        rows = [{"full_name": "Jane R. Doe", "title": "VP", "organization_name": "Acme"}]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        cand = T._best_apollo_candidate("Jane Doe", None, None, "test-key")
        assert cand == rows[0]
