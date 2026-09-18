"""tracker/tlpr_x_pulse.py -- what OTHER people post on X about a person.

Mirrors tests/test_tlpr_press.py's contract: Claude judges (favorable or
not, what themes recur), plain Python counts (how many tweets, from how
many distinct authors, total engagement). Built on the same actor
tracker/sci_source_x.py already uses for the subject's own timeline
(apidojo/tweet-scraper) -- a different input shape, not a new vendor.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import apify_transport, tlpr_x_pulse as xp  # noqa: E402


def _tweet(tid, text="Jane Doe gave a great talk", author="someone", likes=5,
          shares=1, comments=0, posted_at="2026-05-02T10:00:00.000Z"):
    return {"id": tid, "url": "https://x.com/someone/status/%s" % tid, "text": text,
           "createdAt": posted_at, "likeCount": likes, "retweetCount": shares,
           "replyCount": comments, "author": {"userName": author}}


# ── collection: two independent queries, fault-isolated ───────────────────

def test_a_blank_name_collects_nothing_without_any_network_call(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(xp, "_run_actor", lambda *a, **kw: pytest.fail("must not call Apify"))
    tweets, errors = xp.collect_mentions("")
    assert tweets == [] and errors == {}


def test_no_apify_token_records_a_config_error(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    tweets, errors = xp.collect_mentions("Jane Doe")
    assert tweets == []
    assert "not configured" in errors["apify"]


def test_the_name_is_searched_as_an_exact_phrase(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    captured = {}
    monkeypatch.setattr(xp, "_run_actor", lambda run_input, token: captured.update(run_input) or [])
    xp.collect_mentions("Jane Doe")
    assert captured["searchTerms"] == ['"Jane Doe"']


def test_a_known_handle_excludes_the_subjects_own_voice_from_the_search_query(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    calls = []
    monkeypatch.setattr(xp, "_run_actor", lambda run_input, token: calls.append(run_input) or [])
    xp.collect_mentions("Jane Doe", x_handle="@janedoe")
    assert calls[0]["searchTerms"] == ['"Jane Doe" -from:janedoe']


def test_a_known_handle_adds_a_second_query_for_real_mentions(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    calls = []
    monkeypatch.setattr(xp, "_run_actor", lambda run_input, token: calls.append(run_input) or [])
    xp.collect_mentions("Jane Doe", x_handle="janedoe")
    assert len(calls) == 2
    assert calls[1]["mentioning"] == "janedoe"


def test_no_handle_runs_only_the_bare_name_search(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    calls = []
    monkeypatch.setattr(xp, "_run_actor", lambda run_input, token: calls.append(run_input) or [])
    xp.collect_mentions("Jane Doe")
    assert len(calls) == 1


def test_duplicate_tweets_across_both_queries_are_deduped(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(xp, "_run_actor", lambda run_input, token: [_tweet("1")])
    tweets, errors = xp.collect_mentions("Jane Doe", x_handle="janedoe")
    assert len(tweets) == 1


def test_the_search_query_failing_never_blocks_the_mentions_query(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    def fake(run_input, token):
        if "searchTerms" in run_input:
            raise apify_transport.ApifyTransportError("boom")
        return [_tweet("1")]
    monkeypatch.setattr(xp, "_run_actor", fake)
    tweets, errors = xp.collect_mentions("Jane Doe", x_handle="janedoe")
    assert len(tweets) == 1
    assert "x_search" in errors


def test_the_mentions_query_failing_never_blocks_the_search_query(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    def fake(run_input, token):
        if "mentioning" in run_input:
            raise apify_transport.ApifyTransportError("boom")
        return [_tweet("1")]
    monkeypatch.setattr(xp, "_run_actor", fake)
    tweets, errors = xp.collect_mentions("Jane Doe", x_handle="janedoe")
    assert len(tweets) == 1
    assert "x_mentions" in errors


def test_a_normalize_crash_in_one_query_does_not_lose_the_other_or_escape_uncaught(monkeypatch):
    """Reproduces the real incident: _run_actor succeeded but returned
    something sci_source_x.normalize() cannot iterate (a bare string, not a
    list of dicts) -- a different exception type than ApifyTransportError,
    which the old narrow except let escape all the way out of
    collect_mentions and get reported as the generic "X search could not be
    completed" with no diagnosable cause."""
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    def fake(run_input, token):
        if "searchTerms" in run_input:
            return "not a list of tweet dicts"
        return [_tweet("1")]
    monkeypatch.setattr(xp, "_run_actor", fake)
    tweets, errors = xp.collect_mentions("Jane Doe", x_handle="janedoe")
    assert len(tweets) == 1
    assert "x_search" in errors
    assert "x_mentions" not in errors


# ── mechanical aggregation ──────────────────────────────────────────────

def test_aggregate_counts_authors_and_engagement():
    from tracker import sci_source_x
    tweets = sci_source_x.normalize([
        _tweet("1", author="a", likes=10, shares=2),
        _tweet("2", author="b", likes=1),
    ])
    agg = xp.aggregate(tweets)
    assert agg["tweet_count"] == 2
    assert agg["author_count"] == 2
    assert agg["engagement_total"] == 14  # (10+2+0) + (1+1+0), default shares=1


def test_top_tweets_are_ordered_by_engagement():
    from tracker import sci_source_x
    tweets = sci_source_x.normalize([
        _tweet("low", likes=1), _tweet("high", likes=900, shares=40),
    ])
    assert xp.aggregate(tweets)["top_tweets"][0]["id"] == "high"


def test_an_unparseable_date_does_not_crash_aggregation():
    from tracker import sci_source_x
    tweets = sci_source_x.normalize([{"id": "1", "text": "x", "createdAt": "not-a-date"}])
    agg = xp.aggregate(tweets)
    assert agg["tweet_count"] == 1
    assert agg["earliest"] is None


# ── the model's output is never trusted unchecked ─────────────────────────

def test_sentiment_counts_are_computed_from_the_labels_not_from_the_model():
    parsed = {"verdict": "ok", "tweet_sentiment": {"0": "negative", "1": "negative", "2": "positive"}}
    out = xp._clean_analysis(parsed, {"0", "1", "2"})
    assert out["sentiment"]["counts"]["negative"] == 2
    assert out["sentiment"]["labelled"] == 3
    assert out["sentiment"]["negative_share"] == pytest.approx(2 / 3, rel=1e-3)


def test_a_hallucinated_tweet_id_is_stripped_everywhere():
    parsed = {
        "tweet_sentiment": {"0": "positive", "99": "negative"},
        "themes": [{"label": "Praise", "detail": "d", "tweet_ids": ["0", "99"]}],
    }
    out = xp._clean_analysis(parsed, {"0"})
    # only the counts are exposed (same as tlpr_press.py), not a raw per-id map
    assert out["sentiment"]["counts"]["positive"] == 1
    assert out["sentiment"]["labelled"] == 1
    assert out["themes"][0]["tweet_ids"] == ["0"]


def test_a_theme_with_no_surviving_citation_is_dropped_entirely():
    parsed = {"themes": [{"label": "Made up", "detail": "d", "tweet_ids": ["99"]}]}
    assert xp._clean_analysis(parsed, {"0"})["themes"] == []


def test_em_dashes_are_stripped_from_every_free_text_field():
    parsed = {
        "verdict": "They said — great things",
        "themes": [{"label": "A — theme", "detail": "some — detail", "tweet_ids": ["0"]}],
        "risk_flags": ["a — risk"],
    }
    out = xp._clean_analysis(parsed, {"0"})
    assert "—" not in out["verdict"]
    assert "—" not in out["themes"][0]["label"]
    assert "—" not in out["themes"][0]["detail"]
    assert "—" not in out["risk_flags"][0]


def test_notable_tweets_are_enriched_with_the_real_text_and_url():
    parsed = {"notable_tweets": [{"tweet_id": "0", "why": "Widely quoted."}]}
    digest_by_id = {"0": {"author": "jsmith", "text": "The real tweet text", "url": "https://x.com/x/status/0"}}
    out = xp._clean_analysis(parsed, {"0"}, digest_by_id)
    assert out["notable_tweets"][0]["author"] == "jsmith"
    assert out["notable_tweets"][0]["text"] == "The real tweet text"
    assert out["notable_tweets"][0]["url"] == "https://x.com/x/status/0"


def test_notable_tweets_without_a_digest_map_still_work():
    parsed = {"notable_tweets": [{"tweet_id": "0", "why": "ok"}]}
    out = xp._clean_analysis(parsed, {"0"})
    assert out["notable_tweets"][0]["tweet_id"] == "0"
    assert out["notable_tweets"][0]["author"] is None


def test_the_json_scan_survives_a_brace_inside_a_string():
    raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
    assert xp._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


# ── analyze / build_pulse degradation ──────────────────────────────────────

def test_analyze_needs_tweets():
    assert "error" in xp.analyze("Jane Doe", [])


def test_analyze_reports_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tracker import sci_source_x
    tweets = sci_source_x.normalize([_tweet("1")])
    out = xp.analyze("Jane Doe", tweets)
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_build_pulse_calls_a_genuine_zero_a_finding_not_an_error(monkeypatch):
    monkeypatch.setattr(xp, "collect_mentions", lambda n, h=None: ([], {}))
    out = xp.build_pulse("Jane Doe")
    assert out["tweet_count"] == 0
    assert "finding, not an error" in out["note"]
    assert out["analysis"] is None


def test_build_pulse_carries_source_errors_through(monkeypatch):
    from tracker import sci_source_x
    tweets = sci_source_x.normalize([_tweet("1")])
    monkeypatch.setattr(xp, "collect_mentions", lambda n, h=None: (tweets, {"x_mentions": "boom"}))
    monkeypatch.setattr(xp, "analyze", lambda n, t: {"verdict": "ok"})
    out = xp.build_pulse("Jane Doe")
    assert out["errors"] == {"x_mentions": "boom"}
    assert out["analysis"] == {"verdict": "ok"}


def test_build_pulse_never_raises_when_collection_explodes(monkeypatch):
    def boom(name, x_handle=None):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(xp, "collect_mentions", boom)
    out = xp.build_pulse("Jane Doe")
    assert out["tweet_count"] == 0
    assert out["note"]


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
    from tracker import sci_source_x
    tweets = sci_source_x.normalize([_tweet("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(xp, "_anthropic",
                        lambda: _FakeClient('{"verdict": "cut off mid-sen', stop_reason="max_tokens"))
    out = xp.analyze("Jane Doe", tweets)
    assert "max_tokens" in out["error"]
    assert "unreadable response" not in out["error"]


def test_analyze_still_reports_a_generic_unreadable_response_when_not_truncated(monkeypatch):
    from tracker import sci_source_x
    tweets = sci_source_x.normalize([_tweet("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(xp, "_anthropic",
                        lambda: _FakeClient("Sorry, I can't help with that.", stop_reason="end_turn"))
    out = xp.analyze("Jane Doe", tweets)
    assert out["error"] == "The X conversation analysis returned an unreadable response."
