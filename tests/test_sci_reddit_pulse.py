"""tracker/sci_reddit_pulse.py -- the brand-conversation read.

The contract worth protecting here is the division of labour: Claude judges
(what is this thread about, is it praise or a complaint) and plain Python
counts (how many, in which subreddits). A model that is also allowed to tally
its own judgements produces a confident sentiment split that does not match
the threads it was derived from, which is worse than no split at all.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_reddit_pulse as pulse  # noqa: E402


def _post(pid, subreddit="sysadmin", title="Acme is great", body="", score=10,
          comments=5, posted_at="2026-05-02T10:00:00+00:00"):
    return {
        "platform_post_id": pid,
        "post_url": "https://www.reddit.com/r/%s/comments/%s/" % (subreddit, pid),
        "post_type": "text",
        "caption": (title + "\n\n" + body) if body else title,
        "posted_at": posted_at,
        "media_urls": [],
        "metrics": {"likes": score, "comments": comments},
        "raw": {"title": title, "subreddit": subreddit, "upvote_ratio": 0.9},
    }


# ── queries ──────────────────────────────────────────────────────────────

def test_the_company_name_is_searched_as_an_exact_phrase():
    """Unquoted, a multi-word name matches any thread using those words
    separately and floods the corpus with noise."""
    qs = pulse.build_queries("Harborview Compliance Systems")
    assert qs == ['"Harborview Compliance Systems"']


def test_a_known_domain_becomes_its_own_high_precision_query():
    qs = pulse.build_queries("Acme", "https://www.acme.com/pricing")
    assert '"acme.com"' in qs


def test_no_queries_without_a_company_name():
    assert pulse.build_queries("  ") == []


# ── false-positive filtering ─────────────────────────────────────────────

def test_a_hit_that_does_not_name_the_company_is_dropped():
    """Reddit's search matches on stemming and on single words of a
    multi-word name, so "Northstar Anesthesia" otherwise collects every
    thread that merely mentions anesthesia."""
    hit = _post("x1", title="General anesthesia question", body="nothing to do with them")
    assert pulse._mentions_company(hit, "Northstar Anesthesia", None) is False


def test_a_real_mention_survives_the_filter():
    hit = _post("x2", title="Northstar Anesthesia billing", body="")
    assert pulse._mentions_company(hit, "Northstar Anesthesia", None) is True


def test_spacing_variants_still_count_as_a_mention():
    """"Position2" is written "Position 2" about as often as not."""
    hit = _post("x3", title="Anyone used Position 2 for demand gen?")
    assert pulse._mentions_company(hit, "Position2", None) is True


def test_a_domain_only_mention_counts():
    hit = _post("x4", title="Found this vendor", body="see harborview.io for the docs")
    assert pulse._mentions_company(hit, "Harborview", "harborview.io") is True


def test_collect_mentions_dedupes_across_queries_and_sorts(monkeypatch):
    """relevance and new are both run per query, so the same thread comes
    back more than once and must be counted once."""
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "search_posts",
                        lambda q, **k: [_post("dup", title="Acme rocks"), _post("b", title="Acme meh")])
    found = pulse.collect_mentions("Acme", None)
    assert sorted(p["platform_post_id"] for p in found) == ["b", "dup"]


def test_collect_mentions_survives_a_failing_search(monkeypatch):
    from tracker import sci_reddit_client

    def boom(q, **k):
        raise RuntimeError("reddit down")
    monkeypatch.setattr(sci_reddit_client, "search_posts", boom)
    assert pulse.collect_mentions("Acme", None) == []


# ── mechanical aggregation ───────────────────────────────────────────────

def test_aggregate_counts_by_subreddit_and_month():
    posts = [
        _post("a", "sysadmin", score=10, comments=2, posted_at="2026-05-02T10:00:00+00:00"),
        _post("b", "sysadmin", score=5, comments=3, posted_at="2026-05-20T10:00:00+00:00"),
        _post("c", "msp", score=1, comments=1, posted_at="2026-06-01T10:00:00+00:00"),
    ]
    agg = pulse.aggregate(posts)
    assert agg["thread_count"] == 3
    assert agg["comment_total"] == 6
    assert agg["score_total"] == 16
    assert agg["subreddit_count"] == 2
    assert agg["subreddits"][0] == {"name": "sysadmin", "threads": 2, "comments": 5, "score": 15}
    assert agg["timeline"] == [{"month": "2026-05", "threads": 2}, {"month": "2026-06", "threads": 1}]


def test_aggregate_tolerates_an_unparseable_date():
    agg = pulse.aggregate([_post("a", posted_at="not-a-date")])
    assert agg["thread_count"] == 1
    assert agg["timeline"] == []


def test_top_threads_rank_by_real_engagement():
    posts = [_post("low", score=1, comments=1), _post("high", score=900, comments=40)]
    assert pulse.aggregate(posts)["top_threads"][0]["id"] == "high"


# ── the model's output is never trusted unchecked ────────────────────────

def test_sentiment_counts_are_computed_from_the_labels_not_from_the_model():
    """The model is asked to label threads, never to tally them. A count it
    reports itself routinely disagrees with the labels beside it."""
    parsed = {
        "verdict": "Mixed reception.",
        "thread_sentiment": {"a": "positive", "b": "negative", "c": "negative"},
        "themes": [], "competitors": [], "audience": [], "opportunities": [],
        # A deliberately wrong self-reported tally, which must be ignored.
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
              "competitors": [], "audience": [], "opportunities": []}
    out = pulse._clean_analysis(parsed, {"a"})
    assert out["thread_sentiment"] == {"a": "positive"}
    assert out["themes"][0]["thread_ids"] == ["a"]


def test_a_theme_with_no_surviving_citation_is_dropped_entirely():
    """An uncheckable citation renders as a link to a thread that does not
    exist, which is worse than the theme simply not appearing."""
    parsed = {"thread_sentiment": {},
              "themes": [{"label": "Invented", "stance": "praise", "detail": "d",
                          "thread_ids": ["nope"]}],
              "competitors": [], "audience": [], "opportunities": []}
    assert pulse._clean_analysis(parsed, {"a"})["themes"] == []


def test_an_unknown_sentiment_label_is_ignored():
    parsed = {"thread_sentiment": {"a": "ecstatic"}, "themes": [],
              "competitors": [], "audience": [], "opportunities": []}
    out = pulse._clean_analysis(parsed, {"a"})
    assert out["sentiment"]["labelled"] == 0
    assert out["sentiment"]["negative_share"] is None


def test_a_nameless_competitor_is_dropped():
    parsed = {"thread_sentiment": {}, "themes": [],
              "competitors": [{"name": "  ", "context": "c", "thread_ids": ["a"]},
                              {"name": "Vanta", "context": "c", "thread_ids": ["a"]}],
              "audience": [], "opportunities": []}
    out = pulse._clean_analysis(parsed, {"a"})
    assert [c["name"] for c in out["competitors"]] == ["Vanta"]


def test_the_json_scan_survives_a_brace_inside_a_string():
    raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
    assert pulse._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


def test_the_json_scan_returns_none_without_an_object():
    assert pulse._extract_json_object("no json at all") is None


# ── analyze / build_pulse degradation ────────────────────────────────────

def test_analyze_needs_posts():
    assert "error" in pulse.analyze("Acme", [])


def test_analyze_reports_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = pulse.analyze("Acme", [_post("a")])
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_build_pulse_explains_an_unconfigured_deployment(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: False)
    out = pulse.build_pulse("Acme", None)
    assert out["thread_count"] == 0
    assert "REDDIT_CLIENT_ID" in out["note"]
    assert out["analysis"] is None


def test_build_pulse_calls_a_genuine_zero_a_finding_not_an_error(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "resolve_company_subreddit", lambda n: None)
    monkeypatch.setattr(pulse, "collect_mentions", lambda n, u=None: [])
    out = pulse.build_pulse("Acme", None)
    assert out["thread_count"] == 0
    assert "finding, not an error" in out["note"]


def test_build_pulse_carries_every_analyzed_thread_for_citations(monkeypatch):
    """A theme can legitimately cite a low-engagement thread, so top_threads
    alone is not enough to resolve citations against."""
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "resolve_company_subreddit", lambda n: None)
    posts = [_post("p%d" % i, score=i) for i in range(20)]
    monkeypatch.setattr(pulse, "collect_mentions", lambda n, u=None: posts)
    monkeypatch.setattr(pulse, "analyze", lambda n, p: {"verdict": "ok"})
    out = pulse.build_pulse("Acme", None)
    assert len(out["threads"]) == 20
    assert len(out["top_threads"]) == pulse.MAX_TOP_THREADS


def test_build_pulse_never_raises_when_collection_explodes(monkeypatch):
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "resolve_company_subreddit", lambda n: None)

    def boom(name, url=None):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(pulse, "collect_mentions", boom)
    out = pulse.build_pulse("Acme", None)
    assert out["thread_count"] == 0
    assert out["note"]


def test_collect_mentions_actually_applies_the_mention_filter(monkeypatch):
    """Wiring test, not a unit test: every _mentions_company assertion above
    calls it directly and stays green even if collect_mentions never does.
    Reddit's search returns fuzzy matches on real queries, so an unfiltered
    corpus quietly fills the whole analysis with threads about a different
    subject entirely."""
    from tracker import sci_reddit_client
    monkeypatch.setattr(sci_reddit_client, "search_posts", lambda q, **k: [
        _post("real", title="Northstar Anesthesia billing question"),
        _post("noise", title="General anesthesia dosing", body="unrelated thread"),
    ])
    found = pulse.collect_mentions("Northstar Anesthesia", None)
    assert [p["platform_post_id"] for p in found] == ["real"]
