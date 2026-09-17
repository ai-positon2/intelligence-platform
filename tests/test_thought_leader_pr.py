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
