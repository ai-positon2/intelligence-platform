"""tracker/tlpr_linkedin_pulse.py -- what OTHER people post on LinkedIn
about a person, via Unipile's own post search.

Mirrors tests/test_tlpr_x_pulse.py's contract: Claude judges (favorable or
not, what themes recur), plain Python counts (how many posts, from how
many distinct authors, total engagement). Built on the same Unipile
account tracker/thought_leader_pr.py already uses for Phase 1's own
LinkedIn posts and Phase 2's own-post comments -- a different API call,
not a new vendor.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import tlpr_linkedin_pulse as lp  # noqa: E402


def _raw_post(pid, text="Jane Doe gave a great talk", author_id="a1", author_name="Someone",
             likes=5, comments=0, shares=1, posted_at="2026-05-02T10:00:00.000Z"):
    return {"id": pid, "social_id": "urn:li:activity:%s" % pid,
           "share_url": "https://www.linkedin.com/posts/%s" % pid,
           "text": text, "parsed_datetime": posted_at,
           "reaction_counter": likes, "comment_counter": comments, "repost_counter": shares,
           "author": {"id": author_id, "name": author_name, "public_identifier": author_name}}


def _envelope(items):
    return {"object": "PostList", "items": items, "cursor": None}


# ── collection: two independent queries, fault-isolated ─────────────────

def test_a_blank_name_collects_nothing_without_any_network_call(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: pytest.fail("must not call"))
    posts, errors = lp.collect_mentions("")
    assert posts == [] and errors == {}


def test_no_connected_account_records_an_error(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: None)
    posts, errors = lp.collect_mentions("Jane Doe")
    assert posts == []
    assert "No connected LinkedIn account" in errors["linkedin"]


def test_the_name_is_searched_as_an_exact_phrase(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: "acc1")
    captured = {}
    def fake_search(account_id, keywords=None, mentioning_member_ids=None, cursor=None, limit=50):
        captured["keywords"] = keywords
        return _envelope([]), None
    monkeypatch.setattr(lp.unipile_client, "search_posts", fake_search)
    lp.collect_mentions("Jane Doe")
    assert captured["keywords"] == '"Jane Doe"'


def test_a_known_provider_id_adds_a_second_mentioning_query(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: "acc1")
    calls = []
    def fake_search(account_id, keywords=None, mentioning_member_ids=None, cursor=None, limit=50):
        calls.append({"keywords": keywords, "mentioning_member_ids": mentioning_member_ids})
        return _envelope([]), None
    monkeypatch.setattr(lp.unipile_client, "search_posts", fake_search)
    lp.collect_mentions("Jane Doe", provider_id="12345")
    assert len(calls) == 2
    assert calls[1]["mentioning_member_ids"] == ["12345"]


def test_no_provider_id_runs_only_the_bare_keyword_search(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: "acc1")
    calls = []
    def fake_search(account_id, keywords=None, mentioning_member_ids=None, cursor=None, limit=50):
        calls.append(1)
        return _envelope([]), None
    monkeypatch.setattr(lp.unipile_client, "search_posts", fake_search)
    lp.collect_mentions("Jane Doe")
    assert len(calls) == 1


def test_duplicate_posts_across_both_queries_are_deduped(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: "acc1")
    monkeypatch.setattr(lp.unipile_client, "search_posts",
                        lambda *a, **kw: (_envelope([_raw_post("1")]), None))
    posts, errors = lp.collect_mentions("Jane Doe", provider_id="12345")
    assert len(posts) == 1


def test_the_subjects_own_posts_are_filtered_out_by_author_id(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: "acc1")
    monkeypatch.setattr(lp.unipile_client, "search_posts",
                        lambda *a, **kw: (_envelope([_raw_post("1", author_id="12345"),
                                                     _raw_post("2", author_id="other")]), None))
    posts, errors = lp.collect_mentions("Jane Doe", provider_id="12345")
    assert [p["platform_post_id"] for p in posts] == ["2"]


def test_the_keyword_query_failing_never_blocks_the_mentioning_query(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: "acc1")
    def fake_search(account_id, keywords=None, mentioning_member_ids=None, cursor=None, limit=50):
        if keywords:
            return None, {"kind": "http_status", "status": 500}
        return _envelope([_raw_post("1")]), None
    monkeypatch.setattr(lp.unipile_client, "search_posts", fake_search)
    posts, errors = lp.collect_mentions("Jane Doe", provider_id="12345")
    assert len(posts) == 1
    assert "linkedin_search" in errors


def test_the_mentioning_query_failing_never_blocks_the_keyword_query(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: "acc1")
    def fake_search(account_id, keywords=None, mentioning_member_ids=None, cursor=None, limit=50):
        if mentioning_member_ids:
            return None, {"kind": "http_status", "status": 500}
        return _envelope([_raw_post("1")]), None
    monkeypatch.setattr(lp.unipile_client, "search_posts", fake_search)
    posts, errors = lp.collect_mentions("Jane Doe", provider_id="12345")
    assert len(posts) == 1
    assert "linkedin_mentions" in errors


def test_an_envelope_with_no_recognizable_items_key_collects_nothing(monkeypatch):
    monkeypatch.setattr(lp.unipile_transport, "account_for_platform", lambda p: "acc1")
    monkeypatch.setattr(lp.unipile_client, "search_posts",
                        lambda *a, **kw: ({"object": "PostList", "somethingElse": []}, None))
    posts, errors = lp.collect_mentions("Jane Doe")
    assert posts == []


# ── mechanical aggregation ────────────────────────────────────────────────

def test_aggregate_counts_authors_and_engagement():
    from tracker import sci_source_linkedin_unipile
    posts = sci_source_linkedin_unipile.normalize([
        _raw_post("1", author_name="a", likes=10, shares=2),
        _raw_post("2", author_name="b", likes=1),
    ])
    agg = lp.aggregate(posts)
    assert agg["post_count"] == 2
    assert agg["author_count"] == 2
    assert agg["engagement_total"] == 14  # (10+2+0) + (1+1+0), default shares=1


def test_top_posts_are_ordered_by_engagement():
    from tracker import sci_source_linkedin_unipile
    posts = sci_source_linkedin_unipile.normalize([
        _raw_post("low", likes=1), _raw_post("high", likes=900, shares=40),
    ])
    assert lp.aggregate(posts)["top_posts"][0]["id"] == "high"


def test_an_unparseable_date_does_not_crash_aggregation():
    from tracker import sci_source_linkedin_unipile
    posts = sci_source_linkedin_unipile.normalize([{"id": "1", "text": "x", "parsed_datetime": "not-a-date"}])
    agg = lp.aggregate(posts)
    assert agg["post_count"] == 1
    assert agg["earliest"] is None


# ── the model's output is never trusted unchecked ──────────────────────────

def test_sentiment_counts_are_computed_from_the_labels_not_from_the_model():
    parsed = {"verdict": "ok", "post_sentiment": {"0": "negative", "1": "negative", "2": "positive"}}
    out = lp._clean_analysis(parsed, {"0", "1", "2"})
    assert out["sentiment"]["counts"]["negative"] == 2
    assert out["sentiment"]["labelled"] == 3


def test_a_hallucinated_post_id_is_stripped_everywhere():
    parsed = {
        "post_sentiment": {"0": "positive", "99": "negative"},
        "themes": [{"label": "Praise", "detail": "d", "post_ids": ["0", "99"]}],
    }
    out = lp._clean_analysis(parsed, {"0"})
    assert out["sentiment"]["counts"]["positive"] == 1
    assert out["sentiment"]["labelled"] == 1
    assert out["themes"][0]["post_ids"] == ["0"]


def test_a_theme_with_no_surviving_citation_is_dropped_entirely():
    parsed = {"themes": [{"label": "Made up", "detail": "d", "post_ids": ["99"]}]}
    assert lp._clean_analysis(parsed, {"0"})["themes"] == []


def test_em_dashes_are_stripped_from_every_free_text_field():
    parsed = {
        "verdict": "They said — great things",
        "themes": [{"label": "A — theme", "detail": "some — detail", "post_ids": ["0"]}],
        "risk_flags": ["a — risk"],
    }
    out = lp._clean_analysis(parsed, {"0"})
    assert "—" not in out["verdict"]
    assert "—" not in out["themes"][0]["label"]
    assert "—" not in out["risk_flags"][0]


def test_notable_posts_are_enriched_with_the_real_text_and_url():
    parsed = {"notable_posts": [{"post_id": "0", "why": "Widely shared."}]}
    digest_by_id = {"0": {"author": "jsmith", "text": "The real post text", "url": "https://linkedin.com/x"}}
    out = lp._clean_analysis(parsed, {"0"}, digest_by_id)
    assert out["notable_posts"][0]["author"] == "jsmith"
    assert out["notable_posts"][0]["url"] == "https://linkedin.com/x"


def test_the_json_scan_survives_a_brace_inside_a_string():
    raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
    assert lp._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


# ── analyze / build_pulse degradation ───────────────────────────────────────

def test_analyze_needs_posts():
    assert "error" in lp.analyze("Jane Doe", [])


def test_analyze_reports_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tracker import sci_source_linkedin_unipile
    posts = sci_source_linkedin_unipile.normalize([_raw_post("1")])
    out = lp.analyze("Jane Doe", posts)
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_build_pulse_calls_a_genuine_zero_a_finding_not_an_error(monkeypatch):
    monkeypatch.setattr(lp, "collect_mentions", lambda n, provider_id=None: ([], {}))
    out = lp.build_pulse("Jane Doe")
    assert out["post_count"] == 0
    assert "finding, not an error" in out["note"]
    assert out["analysis"] is None


def test_build_pulse_carries_source_errors_through(monkeypatch):
    from tracker import sci_source_linkedin_unipile
    posts = sci_source_linkedin_unipile.normalize([_raw_post("1")])
    monkeypatch.setattr(lp, "collect_mentions", lambda n, provider_id=None: (posts, {"linkedin_mentions": "boom"}))
    monkeypatch.setattr(lp, "analyze", lambda n, p: {"verdict": "ok"})
    out = lp.build_pulse("Jane Doe")
    assert out["errors"] == {"linkedin_mentions": "boom"}
    assert out["analysis"] == {"verdict": "ok"}


def test_build_pulse_never_raises_when_collection_explodes(monkeypatch):
    def boom(name, provider_id=None):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(lp, "collect_mentions", boom)
    out = lp.build_pulse("Jane Doe")
    assert out["post_count"] == 0
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
    from tracker import sci_source_linkedin_unipile
    posts = sci_source_linkedin_unipile.normalize([_raw_post("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(lp, "_anthropic",
                        lambda: _FakeClient('{"verdict": "cut off mid-sen', stop_reason="max_tokens"))
    out = lp.analyze("Jane Doe", posts)
    assert "max_tokens" in out["error"]
    assert "unreadable response" not in out["error"]


def test_analyze_still_reports_a_generic_unreadable_response_when_not_truncated(monkeypatch):
    from tracker import sci_source_linkedin_unipile
    posts = sci_source_linkedin_unipile.normalize([_raw_post("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(lp, "_anthropic",
                        lambda: _FakeClient("Sorry, I can't help with that.", stop_reason="end_turn"))
    out = lp.analyze("Jane Doe", posts)
    assert out["error"] == "The LinkedIn conversation analysis returned an unreadable response."
