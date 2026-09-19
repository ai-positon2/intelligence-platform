"""tracker/tlpr_facebook_pulse.py -- what OTHER people post and comment on
Facebook about a person.

The one pulse in this family built on two NEW actors
(scraper_one/facebook-posts-search, apify/facebook-comments-scraper)
rather than reusing a vendor already integrated elsewhere -- a
deliberate, user-confirmed decision (see the module's own docstring).
Two-stage design mirroring tests/test_tlpr_reddit_pulse.py: search finds
posts, then a second call reads the comment section of the posts search
already confirmed are about this person.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import apify_transport, tlpr_facebook_pulse as fp  # noqa: E402


def _raw_post(pid, text="Jane Doe gave a great talk", author="Someone",
              reactions=5, comments=0, shares=1, timestamp=1780000000):
    return {"postId": pid, "postText": text, "url": "https://facebook.com/posts/%s" % pid,
           "timestamp": timestamp, "reactionsCount": reactions,
           "commentsCount": comments, "sharesCount": shares,
           "author": {"id": "u1", "name": author}}


def _raw_comment(cid, text="Totally agree", author="Fan", likes="3", post_title="Jane Doe post"):
    return {"commentId": cid, "text": text, "profileName": author,
           "likesCount": likes, "commentUrl": "https://facebook.com/c/%s" % cid,
           "postTitle": post_title}


# ── normalize / timestamp parsing ──────────────────────────────────────

def test_normalize_drops_items_with_no_post_id():
    assert fp.normalize([{"postText": "no id here"}]) == []


def test_normalize_converts_unix_seconds_to_iso():
    posts = fp.normalize([_raw_post("1", timestamp=1780000000)])
    assert posts[0]["posted_at"].startswith("2026-")


def test_normalize_handles_millisecond_timestamps():
    posts = fp.normalize([_raw_post("1", timestamp=1780000000000)])
    assert posts[0]["posted_at"].startswith("2026-")


def test_normalize_survives_a_missing_timestamp():
    posts = fp.normalize([{"postId": "1", "postText": "x"}])
    assert posts[0]["posted_at"] is None


def test_normalize_survives_a_dataset_item_that_is_not_a_dict_at_all():
    """Same fix and same real live incident as tracker/sci_source_x.
    normalize() -- see that test's docstring."""
    posts = fp.normalize([{"postId": "1", "postText": "fine"}, "a stray non-post row", None])
    assert [p["platform_post_id"] for p in posts] == ["1"]


# ── collection: search then comments, fault-isolated ───────────────────

def test_a_blank_name_collects_nothing_without_any_network_call(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: pytest.fail("must not call Apify"))
    posts, errors = fp.collect_mentions("")
    assert posts == [] and errors == {}


def test_no_apify_token_records_a_config_error(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    posts, errors = fp.collect_mentions("Jane Doe")
    assert posts == []
    assert "not configured" in errors["apify"]


def test_the_name_is_the_search_query(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    captured = {}
    def fake(actor_id, run_input, token, strict=False):
        captured.update(run_input)
        return []
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", fake)
    fp.collect_mentions("Jane Doe")
    assert captured["query"] == "Jane Doe"


def test_a_search_transport_error_is_recorded(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    def fake(*a, **kw):
        raise apify_transport.ApifyTransportError("boom")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", fake)
    posts, errors = fp.collect_mentions("Jane Doe")
    assert posts == []
    assert "facebook_search" in errors


def test_a_bare_non_list_response_no_longer_crashes_normalize(monkeypatch):
    """normalize() used to run outside collect_mentions's narrow except,
    AND (since 2cefbe6) iterated raw_items assuming every entry was a dict --
    a non-list, non-dict response like this (whose characters iterate as a
    string) used to raise AttributeError there. normalize() is now
    per-item defensive (see tracker/sci_source_x.normalize()'s docstring
    for the real live incident this generalizes from), so this degrades to
    a clean empty result with no error at all, not even a caught one."""
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: "not a list of post dicts")
    posts, errors = fp.collect_mentions("Jane Doe")
    assert posts == []
    assert errors == {}


def test_collect_mentions_still_catches_a_crash_normalize_itself_cannot_prevent(monkeypatch):
    """Defense in depth: even with normalize() now hardened, collect_mentions
    keeps its own broad except around the whole query (search actor call +
    normalize) in case some OTHER, unanticipated step ever raises -- proven
    here by making normalize() itself blow up in a way no per-item guard
    could have anticipated."""
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", lambda *a, **kw: [])
    monkeypatch.setattr(fp, "normalize", lambda raw: (_ for _ in ()).throw(RuntimeError("kaboom")))
    posts, errors = fp.collect_mentions("Jane Doe")
    assert posts == []
    assert "facebook_search" in errors


def test_a_post_that_does_not_actually_mention_the_name_is_dropped(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: [_raw_post("1", text="Nothing about them here")])
    posts, errors = fp.collect_mentions("Jane Doe")
    assert posts == []


def test_a_hyphenated_name_matches_even_without_the_punctuation(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: [_raw_post("1", text="Great talk by JeanClaude Smith today")])
    posts, errors = fp.collect_mentions("Jean-Claude Smith")
    assert len(posts) == 1


def test_duplicate_posts_are_deduped(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: [_raw_post("1"), _raw_post("1")])
    posts, errors = fp.collect_mentions("Jane Doe")
    assert len(posts) == 1


def test_collect_post_comments_fetches_only_the_most_engaged_posts(monkeypatch):
    posts = fp.normalize([_raw_post(str(i), reactions=i) for i in range(fp.MAX_POSTS_FOR_COMMENTS + 3)])
    captured = {}
    def fake(actor_id, run_input, token, strict=False):
        captured.update(run_input)
        return []
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", fake)
    fp._collect_post_comments(posts, "tok")
    assert len(captured["startUrls"]) == fp.MAX_POSTS_FOR_COMMENTS


def test_collect_post_comments_drops_a_comment_with_no_text_or_id(monkeypatch):
    posts = fp.normalize([_raw_post("1")])
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: [{"commentId": "c1", "text": ""}, {"text": "no id"}])
    out = fp._collect_post_comments(posts, "tok")
    assert out == []


def test_one_comments_fetch_failing_never_blocks_the_posts(monkeypatch):
    posts = fp.normalize([_raw_post("1")])
    def fake(*a, **kw):
        raise apify_transport.ApifyTransportError("boom")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", fake)
    out = fp._collect_post_comments(posts, "tok")
    assert out == []


def test_collect_post_comments_reads_likes_and_author():
    posts = fp.normalize([_raw_post("1")])
    with_comments = [_raw_comment("c1", likes="7")]
    import unittest.mock
    with unittest.mock.patch.object(apify_transport, "run_actor_and_wait", return_value=with_comments):
        out = fp._collect_post_comments(posts, "tok")
    assert out[0]["id"] == "c1"
    assert out[0]["likes"] == 7
    assert out[0]["author"] == "Fan"


# ── mechanical aggregation ────────────────────────────────────────────────

def test_aggregate_counts_authors_and_engagement():
    posts = fp.normalize([
        _raw_post("1", author="a", reactions=10, shares=2),
        _raw_post("2", author="b", reactions=1),
    ])
    agg = fp.aggregate(posts)
    assert agg["post_count"] == 2
    assert agg["author_count"] == 2
    assert agg["engagement_total"] == 14  # (10+0+2) + (1+0+1), default shares=1


def test_top_posts_are_ordered_by_engagement():
    posts = fp.normalize([_raw_post("low", reactions=1), _raw_post("high", reactions=900, shares=40)])
    assert fp.aggregate(posts)["top_posts"][0]["id"] == "high"


# ── digest: posts and comments merged with distinct id prefixes ────────────

def test_digest_merges_posts_and_comments_with_distinct_id_prefixes():
    posts = fp.normalize([_raw_post("1")])
    comments = [{"id": "c1", "text": "great", "author": "Fan", "likes": 3, "url": "u", "post_title": "t"}]
    digest = fp._digest(posts, comments)
    kinds = {d["id"]: d["kind"] for d in digest}
    assert kinds["1"] == "post"
    assert kinds["c_c1"] == "comment"


def test_digest_works_unchanged_with_no_comments_at_all():
    posts = fp.normalize([_raw_post("1")])
    digest = fp._digest(posts, None)
    assert len(digest) == 1
    assert digest[0]["kind"] == "post"


# ── the model's output is never trusted unchecked ──────────────────────────

def test_sentiment_counts_are_computed_from_the_labels_not_from_the_model():
    parsed = {"verdict": "ok", "post_sentiment": {"0": "negative", "1": "negative", "2": "positive"}}
    out = fp._clean_analysis(parsed, {"0", "1", "2"})
    assert out["sentiment"]["counts"]["negative"] == 2
    assert out["sentiment"]["labelled"] == 3


def test_a_comment_id_can_be_cited_in_a_theme():
    parsed = {"themes": [{"label": "Support", "detail": "d", "post_ids": ["c_c1"]}]}
    out = fp._clean_analysis(parsed, {"c_c1"})
    assert out["themes"][0]["post_ids"] == ["c_c1"]


def test_a_hallucinated_id_is_stripped_everywhere():
    parsed = {
        "post_sentiment": {"0": "positive", "99": "negative"},
        "themes": [{"label": "Praise", "detail": "d", "post_ids": ["0", "99"]}],
    }
    out = fp._clean_analysis(parsed, {"0"})
    assert out["sentiment"]["counts"]["positive"] == 1
    assert out["themes"][0]["post_ids"] == ["0"]


def test_em_dashes_are_stripped_from_every_free_text_field():
    parsed = {
        "verdict": "They said — great things",
        "themes": [{"label": "A — theme", "detail": "some — detail", "post_ids": ["0"]}],
        "risk_flags": ["a — risk"],
    }
    out = fp._clean_analysis(parsed, {"0"})
    assert "—" not in out["verdict"]
    assert "—" not in out["risk_flags"][0]


def test_the_json_scan_survives_a_brace_inside_a_string():
    raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
    assert fp._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


# ── analyze / build_pulse degradation ───────────────────────────────────────

def test_analyze_needs_posts():
    assert "error" in fp.analyze("Jane Doe", [])


def test_analyze_reports_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    posts = fp.normalize([_raw_post("1")])
    out = fp.analyze("Jane Doe", posts)
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_build_pulse_calls_a_genuine_zero_a_finding_not_an_error(monkeypatch):
    monkeypatch.setattr(fp, "collect_mentions", lambda n: ([], {}))
    out = fp.build_pulse("Jane Doe")
    assert out["post_count"] == 0
    assert "finding, not an error" in out["note"]
    assert out["analysis"] is None


def test_build_pulse_skips_comments_without_an_apify_token(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    posts = fp.normalize([_raw_post("1")])
    monkeypatch.setattr(fp, "collect_mentions", lambda n: (posts, {}))
    monkeypatch.setattr(fp, "_collect_post_comments", lambda p, t: pytest.fail("must not call"))
    monkeypatch.setattr(fp, "analyze", lambda n, p, c=None: {"verdict": "ok"})
    out = fp.build_pulse("Jane Doe")
    assert out["comment_sample_count"] == 0


def test_build_pulse_carries_source_errors_through(monkeypatch):
    posts = fp.normalize([_raw_post("1")])
    monkeypatch.setattr(fp, "collect_mentions", lambda n: (posts, {"facebook_search": "boom"}))
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(fp, "_collect_post_comments", lambda p, t: [])
    monkeypatch.setattr(fp, "analyze", lambda n, p, c=None: {"verdict": "ok"})
    out = fp.build_pulse("Jane Doe")
    assert out["errors"] == {"facebook_search": "boom"}
    assert out["analysis"] == {"verdict": "ok"}


def test_build_pulse_never_raises_when_collection_explodes(monkeypatch):
    def boom(name):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(fp, "collect_mentions", boom)
    out = fp.build_pulse("Jane Doe")
    assert out["post_count"] == 0
    assert out["note"]


def test_build_pulse_comment_collection_failing_never_blocks_the_analysis(monkeypatch):
    posts = fp.normalize([_raw_post("1")])
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(fp, "collect_mentions", lambda n: (posts, {}))
    def boom(p, t):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(fp, "_collect_post_comments", boom)
    out = fp.build_pulse("Jane Doe")
    assert out["post_count"] == 1


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text, stop_reason):
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, text, stop_reason):
        self._text, self._stop_reason = text, stop_reason

    def create(self, **kwargs):
        return _FakeResponse(self._text, self._stop_reason)


class _FakeClient:
    def __init__(self, text, stop_reason="end_turn"):
        self.messages = _FakeMessages(text, stop_reason)


def test_analyze_names_a_truncated_reply_distinctly_from_a_generic_unreadable_one(monkeypatch):
    posts = fp.normalize([_raw_post("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(fp, "_anthropic",
                        lambda: _FakeClient('{"verdict": "cut off mid-sen', stop_reason="max_tokens"))
    out = fp.analyze("Jane Doe", posts)
    assert "max_tokens" in out["error"]
    assert "unreadable response" not in out["error"]


def test_analyze_still_reports_a_generic_unreadable_response_when_not_truncated(monkeypatch):
    posts = fp.normalize([_raw_post("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(fp, "_anthropic",
                        lambda: _FakeClient("Sorry, I can't help with that.", stop_reason="end_turn"))
    out = fp.analyze("Jane Doe", posts)
    assert out["error"] == "The Facebook conversation analysis returned an unreadable response."
