"""tracker/tlpr_reddit_pulse.py -- Reddit conversation about a person.

Mirrors tests/test_sci_reddit_pulse.py's contract: Claude judges (praise vs.
complaint), plain Python counts (how many, in which subreddits). A model
that is also allowed to tally its own judgements produces a confident
sentiment split that does not match the threads it was derived from.
"""

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
    monkeypatch.setattr(pulse, "analyze", lambda n, p: {"verdict": "ok"})
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
