"""tracker/tlpr_reddit_pulse.py -- Reddit conversation about a person.

Mirrors tests/test_sci_reddit_pulse.py's contract: Claude judges (praise vs.
complaint), plain Python counts (how many, in which subreddits). A model
that is also allowed to tally its own judgements produces a confident
sentiment split that does not match the threads it was derived from.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import tlpr_reddit_pulse as pulse  # noqa: E402


def _post(pid, subreddit="technology", title="Jane Doe is great", body="", score=10,
          comments=5, posted_at="2026-05-02T10:00:00+00:00"):
    return {
        "platform_post_id": pid,
        "post_url": "https://www.reddit.com/r/%s/comments/%s/" % (subreddit, pid),
        "post_type": "text",
        "caption": (title + "\n\n" + body) if body else title,
        "posted_at": posted_at,
        "media_urls": [],
        "metrics": {"likes": score, "comments": comments},
        "raw": {"title": title, "subreddit": subreddit},
    }


# ── queries ──────────────────────────────────────────────────────────────

def test_the_persons_name_is_searched_as_an_exact_phrase():
    assert pulse.build_queries("Jane Harborview Doe") == ['"Jane Harborview Doe"']


def test_a_company_hint_becomes_its_own_precision_query():
    qs = pulse.build_queries("Jane Doe", "Acme Corp")
    assert '"Jane Doe" Acme Corp' in qs


def test_no_queries_without_a_name():
    assert pulse.build_queries("  ") == []


# ── false-positive filtering ─────────────────────────────────────────────

def test_a_hit_that_does_not_name_the_person_is_dropped():
    """A common name that is also an ordinary word/phrase otherwise collects
    every unrelated thread using those words separately."""
    hit = _post("x1", title="Grant application question", body="nothing to do with them")
    assert pulse._mentions_person(hit, "Grant Wilson") is False


def test_a_real_mention_survives_the_filter():
    hit = _post("x2", title="Grant Wilson's keynote was great", body="")
    assert pulse._mentions_person(hit, "Grant Wilson") is True


def test_hyphenation_variants_still_count_as_a_mention():
    hit = _post("x3", title="Anyone catch Jean Claude's talk?")
    assert pulse._mentions_person(hit, "Jean-Claude") is True


def test_collect_mentions_dedupes_across_queries(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "search_posts",
                        lambda q, **k: [_post("dup", title="Jane Doe rocks"),
                                        _post("b", title="Jane Doe meh")])
    found = pulse.collect_mentions("Jane Doe")
    assert sorted(p["platform_post_id"] for p in found) == ["b", "dup"]


def test_collect_mentions_survives_a_failing_search(monkeypatch):
    from tracker import sci_reddit_client

    def boom(q, **k):
        raise RuntimeError("reddit down")
    monkeypatch.setattr(sci_reddit_client, "search_posts", boom)
    assert pulse.collect_mentions("Jane Doe") == []


# ── mechanical aggregation ───────────────────────────────────────────────

def test_aggregate_counts_by_subreddit_and_month():
    posts = [
        _post("a", "technology", score=10, comments=2, posted_at="2026-05-02T10:00:00+00:00"),
        _post("b", "technology", score=5, comments=3, posted_at="2026-05-20T10:00:00+00:00"),
        _post("c", "business", score=1, comments=1, posted_at="2026-06-01T10:00:00+00:00"),
    ]
    agg = pulse.aggregate(posts)
    assert agg["thread_count"] == 3
    assert agg["comment_total"] == 6
    assert agg["score_total"] == 16
    assert agg["subreddit_count"] == 2
    assert agg["subreddits"][0] == {"name": "technology", "threads": 2, "comments": 5, "score": 15}
    assert agg["timeline"] == [{"month": "2026-05", "threads": 2}, {"month": "2026-06", "threads": 1}]


def test_top_threads_rank_by_real_engagement():
    posts = [_post("low", score=1, comments=1), _post("high", score=900, comments=40)]
    assert pulse.aggregate(posts)["top_threads"][0]["id"] == "high"


# ── reading each thread's own comment section ─────────────────────────────
#
# "What are people discussing in the comments" is a real, distinct ask from
# "how many threads mention this person" -- Reddit's own comment section is
# where most of a discussion's actual opinion lives, and none of it was
# read before this.

def test_collect_thread_comments_fetches_only_the_most_engaged_threads(monkeypatch):
    from tracker import sci_reddit_client
    posts = [_post("p%d" % i, score=i) for i in range(pulse.MAX_THREADS_FOR_COMMENTS + 5)]
    fetched = []
    monkeypatch.setattr(sci_reddit_client, "get_post_comments",
                        lambda pid, subreddit=None, limit=None: fetched.append(pid) or [])
    pulse._collect_thread_comments(posts)
    assert len(fetched) == pulse.MAX_THREADS_FOR_COMMENTS
    # the highest-scoring threads, not an arbitrary slice
    assert "p%d" % (len(posts) - 1) in fetched
    assert "p0" not in fetched


def test_collect_thread_comments_tags_each_comment_with_its_own_thread(monkeypatch):
    from tracker import sci_reddit_client
    posts = [_post("p1", subreddit="technology", title="Jane Doe keynote")]
    monkeypatch.setattr(sci_reddit_client, "get_post_comments", lambda pid, subreddit=None, limit=None: [
        {"id": "c1", "author": "u1", "body": "Great talk", "score": 5, "permalink": "/r/x/c1/"},
    ])
    out = pulse._collect_thread_comments(posts)
    assert out == [{"id": "c1", "thread_id": "p1", "thread_title": "Jane Doe keynote",
                   "subreddit": "technology", "body": "Great talk", "score": 5,
                   "permalink": "/r/x/c1/"}]


def test_collect_thread_comments_one_threads_fetch_failing_never_blocks_the_others(monkeypatch):
    from tracker import sci_reddit_client
    posts = [_post("bad", score=2), _post("good", score=1)]
    def fake(pid, subreddit=None, limit=None):
        if pid == "bad":
            raise RuntimeError("boom")
        return [{"id": "c1", "body": "ok", "score": 1, "permalink": "/x/"}]
    monkeypatch.setattr(sci_reddit_client, "get_post_comments", fake)
    out = pulse._collect_thread_comments(posts)
    assert len(out) == 1 and out[0]["thread_id"] == "good"


def test_collect_thread_comments_drops_a_comment_with_no_id(monkeypatch):
    from tracker import sci_reddit_client
    posts = [_post("p1")]
    monkeypatch.setattr(sci_reddit_client, "get_post_comments",
                        lambda pid, subreddit=None, limit=None: [{"body": "no id here"}])
    assert pulse._collect_thread_comments(posts) == []


def test_digest_merges_threads_and_comments_with_distinct_id_prefixes():
    posts = [_post("p1", title="Jane Doe keynote")]
    comments = [{"id": "c1", "thread_id": "p1", "thread_title": "Jane Doe keynote",
                "subreddit": "technology", "body": "Great talk", "score": 5,
                "permalink": "/r/x/comments/p1/t/c1/"}]
    digest = pulse._digest(posts, comments)
    ids = {d["id"] for d in digest}
    assert "p1" in ids and "c_c1" in ids
    comment_entry = next(d for d in digest if d["id"] == "c_c1")
    assert comment_entry["kind"] == "comment"
    assert "Great talk" in comment_entry["excerpt"]
    assert comment_entry["title"] == "Comment on: Jane Doe keynote"
    assert comment_entry["url"] == "https://www.reddit.com/r/x/comments/p1/t/c1/"


def test_digest_works_unchanged_with_no_comments_at_all():
    """Every analysis stored before this feature existed cites bare thread
    ids -- passing no comments must reproduce exactly the old digest."""
    posts = [_post("p1")]
    assert pulse._digest(posts) == pulse._digest(posts, None) == pulse._digest(posts, [])


def test_analyze_can_cite_a_comment_id_in_a_theme(monkeypatch):
    posts = [_post("p1", title="Jane Doe keynote")]
    comments = [{"id": "c1", "thread_id": "p1", "thread_title": "Jane Doe keynote",
                "subreddit": "technology", "body": "Overrated, honestly", "score": 5,
                "permalink": "/r/x/c1/"}]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(pulse, "_anthropic", lambda: _FakeClient(json.dumps({
        "verdict": "Mixed.",
        "thread_sentiment": {"c_c1": "negative"},
        "themes": [{"label": "Criticism", "stance": "complaint", "detail": "Called overrated.",
                   "thread_ids": ["c_c1"]}],
        "compared_to": [], "audience": [], "risk_flags": [],
    })))
    out = pulse.analyze("Jane Doe", posts, comments)
    assert out["thread_sentiment"] == {"c_c1": "negative"}
    assert out["themes"][0]["thread_ids"] == ["c_c1"]


def test_build_pulse_collects_and_counts_comments(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    posts = [_post("p1")]
    monkeypatch.setattr(pulse, "collect_mentions", lambda n, c=None: posts)
    monkeypatch.setattr(sci_reddit_client, "get_post_comments",
                        lambda pid, subreddit=None, limit=None: [
                            {"id": "c1", "body": "ok", "score": 1, "permalink": "/x/"}])
    captured = {}
    monkeypatch.setattr(pulse, "analyze", lambda n, p, c=None: captured.update(comments=c) or {"verdict": "ok"})
    out = pulse.build_pulse("Jane Doe")
    assert out["comment_sample_count"] == 1
    assert len(captured["comments"]) == 1


def test_build_pulse_comment_collection_failing_never_blocks_the_analysis(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    posts = [_post("p1")]
    monkeypatch.setattr(pulse, "collect_mentions", lambda n, c=None: posts)
    def boom(posts):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(pulse, "_collect_thread_comments", boom)
    monkeypatch.setattr(pulse, "analyze", lambda n, p, c=None: {"verdict": "ok"})
    out = pulse.build_pulse("Jane Doe")
    assert out["comment_sample_count"] == 0
    assert out["analysis"] == {"verdict": "ok"}


# ── the model's output is never trusted unchecked ────────────────────────

def test_sentiment_counts_are_computed_from_the_labels_not_from_the_model():
    parsed = {
        "verdict": "Mixed reception.",
        "thread_sentiment": {"a": "positive", "b": "negative", "c": "negative"},
        "themes": [], "compared_to": [], "audience": [], "risk_flags": [],
        "sentiment": {"counts": {"positive": 99, "negative": 0}},
    }
    out = pulse._clean_analysis(parsed, {"a", "b", "c"})
    assert out["sentiment"]["counts"] == {"positive": 1, "neutral": 0, "negative": 2, "mixed": 0}
    assert out["sentiment"]["labelled"] == 3
    assert out["sentiment"]["negative_share"] == round(2 / 3, 3)


def test_a_hallucinated_thread_id_is_stripped():
    parsed = {"thread_sentiment": {"a": "positive", "ZZZ": "negative"},
              "themes": [{"label": "T", "stance": "praise", "detail": "d",
                          "thread_ids": ["a", "ZZZ"]}],
              "compared_to": [], "audience": [], "risk_flags": []}
    out = pulse._clean_analysis(parsed, {"a"})
    assert out["thread_sentiment"] == {"a": "positive"}
    assert out["themes"][0]["thread_ids"] == ["a"]


def test_a_theme_with_no_surviving_citation_is_dropped_entirely():
    parsed = {"thread_sentiment": {},
              "themes": [{"label": "Invented", "stance": "praise", "detail": "d",
                          "thread_ids": ["nope"]}],
              "compared_to": [], "audience": [], "risk_flags": []}
    assert pulse._clean_analysis(parsed, {"a"})["themes"] == []


def test_an_em_dash_in_the_verdict_and_theme_detail_is_cleaned():
    """This module predates b00d931's shared fix and is a NEW copy of the
    "Claude writes free text" pattern -- it must not reintroduce the
    systemic em-dash leak that fix exists to prevent."""
    parsed = {
        "verdict": "Well liked — no real controversy.",
        "thread_sentiment": {"a": "positive"},
        "themes": [{"label": "Praise", "stance": "praise",
                    "detail": "People like his talks — especially the keynotes.",
                    "thread_ids": ["a"]}],
        "compared_to": [], "audience": [], "risk_flags": ["A minor gripe — nothing major."],
    }
    out = pulse._clean_analysis(parsed, {"a"})
    assert "—" not in out["verdict"]
    assert "—" not in out["themes"][0]["detail"]
    assert "—" not in out["risk_flags"][0]


def test_a_nameless_comparison_is_dropped():
    parsed = {"thread_sentiment": {}, "themes": [],
              "compared_to": [{"name": "  ", "context": "c", "thread_ids": ["a"]},
                              {"name": "John Rival", "context": "c", "thread_ids": ["a"]}],
              "audience": [], "risk_flags": []}
    out = pulse._clean_analysis(parsed, {"a"})
    assert [c["name"] for c in out["compared_to"]] == ["John Rival"]


def test_the_json_scan_survives_a_brace_inside_a_string():
    raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
    assert pulse._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


# ── analyze / build_pulse degradation ────────────────────────────────────

def test_analyze_needs_posts():
    assert "error" in pulse.analyze("Jane Doe", [])


def test_analyze_reports_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = pulse.analyze("Jane Doe", [_post("a")])
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_build_pulse_explains_an_unconfigured_deployment(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: False)
    out = pulse.build_pulse("Jane Doe")
    assert out["thread_count"] == 0
    assert "REDDIT_CLIENT_ID" in out["note"]
    assert out["analysis"] is None


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
    """Same fix as tests/test_tlpr_press.py's twin of this test -- see there
    for why the distinction matters."""
    monkeypatch.setattr(pulse, "_anthropic",
                        lambda: _FakeClient('{"verdict": "cut off mid-sen', stop_reason="max_tokens"))
    out = pulse.analyze("Jane Doe", [_post("a")])
    assert "max_tokens" in out["error"]
    assert "unreadable response" not in out["error"]


def test_analyze_still_reports_a_generic_unreadable_response_when_not_truncated(monkeypatch):
    monkeypatch.setattr(pulse, "_anthropic",
                        lambda: _FakeClient("Sorry, I can't help with that.", stop_reason="end_turn"))
    out = pulse.analyze("Jane Doe", [_post("a")])
    assert out["error"] == "The Reddit conversation analysis returned an unreadable response."


def test_build_pulse_calls_a_genuine_zero_a_finding_not_an_error(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(pulse, "collect_mentions", lambda n, c=None: [])
    out = pulse.build_pulse("Jane Doe")
    assert out["thread_count"] == 0
    assert "finding, not an error" in out["note"]


def test_build_pulse_carries_every_analyzed_thread_for_citations(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    posts = [_post("p%d" % i, score=i) for i in range(20)]
    monkeypatch.setattr(pulse, "collect_mentions", lambda n, c=None: posts)
    monkeypatch.setattr(pulse, "analyze", lambda n, p, c=None: {"verdict": "ok"})
    out = pulse.build_pulse("Jane Doe")
    assert len(out["threads"]) == 20
    assert len(out["top_threads"]) == pulse.MAX_TOP_THREADS


def test_build_pulse_never_raises_when_collection_explodes(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)

    def boom(name, company_hint=None):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(pulse, "collect_mentions", boom)
    out = pulse.build_pulse("Jane Doe")
    assert out["thread_count"] == 0
    assert out["note"]


def test_collect_mentions_actually_applies_the_mention_filter(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "search_posts", lambda q, **k: [
        _post("real", title="Grant Wilson's keynote was great"),
        _post("noise", title="Grant application question", body="unrelated thread"),
    ])
    found = pulse.collect_mentions("Grant Wilson")
    assert [p["platform_post_id"] for p in found] == ["real"]
