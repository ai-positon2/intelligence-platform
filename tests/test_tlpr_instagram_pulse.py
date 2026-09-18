"""tracker/tlpr_instagram_pulse.py -- who tags a person on Instagram,
via that person's own Mentions tab.

Instagram has no free-text keyword search (confirmed against
apify/instagram-scraper's own input schema), so this is a narrower claim
than tlpr_x_pulse.py/tlpr_linkedin_pulse.py: only posts where someone
EXPLICITLY tagged the person, and only readable when Phase 0 identity
resolution found their Instagram handle. Built on the same actor
tracker/sci_source_instagram.py already uses for a company's own profile
posts -- a different input shape (resultsType="mentions" instead of
"posts"), not a new vendor.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import apify_transport, tlpr_instagram_pulse as ip  # noqa: E402


def _mention(pid, caption="Great seeing @janedoe today", owner="fan_account",
            owner_full="Fan Account", likes=5, comments=0, posted_at="2026-05-02T10:00:00.000Z"):
    return {"id": pid, "shortCode": pid, "caption": caption, "timestamp": posted_at,
           "likesCount": likes, "commentsCount": comments,
           "displayUrl": "https://instagram.com/p/%s/img.jpg" % pid,
           "ownerUsername": owner, "ownerFullName": owner_full}


# ── collection ──────────────────────────────────────────────────────────

def test_a_blank_handle_collects_nothing_without_any_network_call(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: pytest.fail("must not call Apify"))
    posts, errors = ip.collect_mentions("")
    assert posts == [] and errors == {}


def test_no_apify_token_records_a_config_error(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    posts, errors = ip.collect_mentions("janedoe")
    assert posts == []
    assert "not configured" in errors["apify"]


def test_the_run_reads_the_mentions_tab_of_the_given_profile(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    captured = {}
    def fake(actor_id, run_input, token, strict=False):
        captured.update(run_input)
        return []
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", fake)
    ip.collect_mentions("@janedoe")
    assert captured["resultsType"] == "mentions"
    assert captured["directUrls"] == ["https://www.instagram.com/janedoe/"]


def test_a_transport_error_is_recorded_and_returns_no_posts(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    def fake(*a, **kw):
        raise apify_transport.ApifyTransportError("boom")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", fake)
    posts, errors = ip.collect_mentions("janedoe")
    assert posts == []
    assert "instagram_mentions" in errors


def test_a_normalize_crash_is_caught_here_not_left_to_escape(monkeypatch):
    """normalize() used to run outside the narrow except -- a crash there
    (a different exception type than ApifyTransportError) escaped
    collect_mentions entirely and was only ever caught by build_pulse's
    generic outer catch. Same gap and fix as tlpr_x_pulse.py."""
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: "not a list of post dicts")
    posts, errors = ip.collect_mentions("janedoe")
    assert posts == []
    assert "instagram_mentions" in errors


def test_duplicate_posts_in_the_mentions_results_are_deduped(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: [_mention("1"), _mention("1")])
    posts, errors = ip.collect_mentions("janedoe")
    assert len(posts) == 1


# ── mechanical aggregation ────────────────────────────────────────────────

def test_aggregate_counts_authors_and_engagement():
    from tracker import sci_source_instagram
    posts = sci_source_instagram.normalize([
        _mention("1", owner="a", likes=10, comments=2),
        _mention("2", owner="b", likes=1),
    ])
    agg = ip.aggregate(posts)
    assert agg["mention_count"] == 2
    assert agg["author_count"] == 2
    assert agg["engagement_total"] == 13  # (10+2+0) + (1+0+0)


def test_the_author_of_a_mention_is_the_tagging_account_not_the_subject():
    from tracker import sci_source_instagram
    posts = sci_source_instagram.normalize([_mention("1", owner="fan_account", owner_full="Fan Account")])
    assert ip._author(posts[0]) == "fan_account"


def test_top_mentions_are_ordered_by_engagement():
    from tracker import sci_source_instagram
    posts = sci_source_instagram.normalize([
        _mention("low", likes=1), _mention("high", likes=900, comments=40),
    ])
    assert ip.aggregate(posts)["top_mentions"][0]["id"] == "high"


def test_an_unparseable_date_does_not_crash_aggregation():
    from tracker import sci_source_instagram
    posts = sci_source_instagram.normalize([{"id": "1", "caption": "x", "timestamp": "not-a-date"}])
    agg = ip.aggregate(posts)
    assert agg["mention_count"] == 1
    assert agg["earliest"] is None


# ── the model's output is never trusted unchecked ──────────────────────────

def test_sentiment_counts_are_computed_from_the_labels_not_from_the_model():
    parsed = {"verdict": "ok", "mention_sentiment": {"0": "negative", "1": "negative", "2": "positive"}}
    out = ip._clean_analysis(parsed, {"0", "1", "2"})
    assert out["sentiment"]["counts"]["negative"] == 2
    assert out["sentiment"]["labelled"] == 3


def test_a_hallucinated_mention_id_is_stripped_everywhere():
    parsed = {
        "mention_sentiment": {"0": "positive", "99": "negative"},
        "themes": [{"label": "Praise", "detail": "d", "mention_ids": ["0", "99"]}],
    }
    out = ip._clean_analysis(parsed, {"0"})
    assert out["sentiment"]["counts"]["positive"] == 1
    assert out["themes"][0]["mention_ids"] == ["0"]


def test_a_theme_with_no_surviving_citation_is_dropped_entirely():
    parsed = {"themes": [{"label": "Made up", "detail": "d", "mention_ids": ["99"]}]}
    assert ip._clean_analysis(parsed, {"0"})["themes"] == []


def test_em_dashes_are_stripped_from_every_free_text_field():
    parsed = {
        "verdict": "They said — great things",
        "themes": [{"label": "A — theme", "detail": "some — detail", "mention_ids": ["0"]}],
        "risk_flags": ["a — risk"],
    }
    out = ip._clean_analysis(parsed, {"0"})
    assert "—" not in out["verdict"]
    assert "—" not in out["risk_flags"][0]


def test_notable_mentions_are_enriched_with_the_real_text_and_url():
    parsed = {"notable_mentions": [{"mention_id": "0", "why": "Popular tag."}]}
    digest_by_id = {"0": {"author": "fan_account", "text": "caption", "url": "https://instagram.com/p/0/"}}
    out = ip._clean_analysis(parsed, {"0"}, digest_by_id)
    assert out["notable_mentions"][0]["author"] == "fan_account"
    assert out["notable_mentions"][0]["url"] == "https://instagram.com/p/0/"


def test_the_json_scan_survives_a_brace_inside_a_string():
    raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
    assert ip._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


# ── analyze / build_pulse degradation ───────────────────────────────────────

def test_analyze_needs_posts():
    assert "error" in ip.analyze("Jane Doe", [])


def test_analyze_reports_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tracker import sci_source_instagram
    posts = sci_source_instagram.normalize([_mention("1")])
    out = ip.analyze("Jane Doe", posts)
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_build_pulse_with_no_handle_needs_no_network_call(monkeypatch):
    monkeypatch.setattr(ip, "collect_mentions", lambda h: pytest.fail("must not call"))
    out = ip.build_pulse("Jane Doe", instagram_handle=None)
    assert out["mention_count"] == 0
    assert "No Instagram handle" in out["note"]
    assert out["analysis"] is None


def test_build_pulse_calls_a_genuine_zero_a_finding_not_an_error(monkeypatch):
    monkeypatch.setattr(ip, "collect_mentions", lambda h: ([], {}))
    out = ip.build_pulse("Jane Doe", instagram_handle="janedoe")
    assert out["mention_count"] == 0
    assert "finding, not an error" in out["note"]
    assert out["analysis"] is None


def test_build_pulse_carries_source_errors_through(monkeypatch):
    from tracker import sci_source_instagram
    posts = sci_source_instagram.normalize([_mention("1")])
    monkeypatch.setattr(ip, "collect_mentions", lambda h: (posts, {"instagram_mentions": "boom"}))
    monkeypatch.setattr(ip, "analyze", lambda n, p: {"verdict": "ok"})
    out = ip.build_pulse("Jane Doe", instagram_handle="janedoe")
    assert out["errors"] == {"instagram_mentions": "boom"}
    assert out["analysis"] == {"verdict": "ok"}


def test_build_pulse_never_raises_when_collection_explodes(monkeypatch):
    def boom(handle):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(ip, "collect_mentions", boom)
    out = ip.build_pulse("Jane Doe", instagram_handle="janedoe")
    assert out["mention_count"] == 0
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
    from tracker import sci_source_instagram
    posts = sci_source_instagram.normalize([_mention("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ip, "_anthropic",
                        lambda: _FakeClient('{"verdict": "cut off mid-sen', stop_reason="max_tokens"))
    out = ip.analyze("Jane Doe", posts)
    assert "max_tokens" in out["error"]
    assert "unreadable response" not in out["error"]


def test_analyze_still_reports_a_generic_unreadable_response_when_not_truncated(monkeypatch):
    from tracker import sci_source_instagram
    posts = sci_source_instagram.normalize([_mention("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ip, "_anthropic",
                        lambda: _FakeClient("Sorry, I can't help with that.", stop_reason="end_turn"))
    out = ip.analyze("Jane Doe", posts)
    assert out["error"] == "The Instagram conversation analysis returned an unreadable response."
