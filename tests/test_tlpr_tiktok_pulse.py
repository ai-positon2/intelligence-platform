"""tracker/tlpr_tiktok_pulse.py -- what OTHER people post on TikTok about a
person, via TikTok's own video search.

Mirrors tests/test_tlpr_x_pulse.py's contract. Built on the same actor
tracker/sci_source_tiktok.py already uses for a company's own profile
videos (clockworks/tiktok-scraper) -- a different input shape
(searchQueries + searchSection="/video" instead of profiles), not a new
vendor.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import apify_transport, tlpr_tiktok_pulse as tp  # noqa: E402


def _video(vid, text="Jane Doe gave a great talk", author="someone", likes=5,
          shares=1, comments=0, posted_at="2026-05-02T10:00:00.000Z"):
    return {"id": vid, "webVideoUrl": "https://www.tiktok.com/@someone/video/%s" % vid,
           "text": text, "createTimeISO": posted_at, "diggCount": likes,
           "shareCount": shares, "commentCount": comments,
           "authorMeta": {"nickName": author, "name": author}}


# ── collection ──────────────────────────────────────────────────────────

def test_a_blank_name_collects_nothing_without_any_network_call(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: pytest.fail("must not call Apify"))
    videos, errors = tp.collect_mentions("")
    assert videos == [] and errors == {}


def test_no_apify_token_records_a_config_error(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    videos, errors = tp.collect_mentions("Jane Doe")
    assert videos == []
    assert "not configured" in errors["apify"]


def test_the_search_is_scoped_to_the_video_section_by_name(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    captured = {}
    def fake(actor_id, run_input, token, strict=False):
        captured.update(run_input)
        return []
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", fake)
    tp.collect_mentions("Jane Doe")
    assert captured["searchQueries"] == ["Jane Doe"]
    assert captured["searchSection"] == "/video"


def test_a_transport_error_is_recorded_and_returns_no_videos(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    def fake(*a, **kw):
        raise apify_transport.ApifyTransportError("boom")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait", fake)
    videos, errors = tp.collect_mentions("Jane Doe")
    assert videos == []
    assert "tiktok_search" in errors


def test_duplicate_videos_in_the_search_results_are_deduped(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: [_video("1"), _video("1")])
    videos, errors = tp.collect_mentions("Jane Doe")
    assert len(videos) == 1


# ── mechanical aggregation ────────────────────────────────────────────────

def test_aggregate_counts_authors_and_engagement():
    from tracker import sci_source_tiktok
    videos = sci_source_tiktok.normalize([
        _video("1", author="a", likes=10, shares=2),
        _video("2", author="b", likes=1),
    ])
    agg = tp.aggregate(videos)
    assert agg["video_count"] == 2
    assert agg["author_count"] == 2
    assert agg["engagement_total"] == 14  # (10+2+0) + (1+1+0), default shares=1


def test_top_videos_are_ordered_by_engagement():
    from tracker import sci_source_tiktok
    videos = sci_source_tiktok.normalize([
        _video("low", likes=1), _video("high", likes=900, shares=40),
    ])
    assert tp.aggregate(videos)["top_videos"][0]["id"] == "high"


def test_an_unparseable_date_does_not_crash_aggregation():
    from tracker import sci_source_tiktok
    videos = sci_source_tiktok.normalize([{"id": "1", "text": "x", "createTimeISO": "not-a-date"}])
    agg = tp.aggregate(videos)
    assert agg["video_count"] == 1
    assert agg["earliest"] is None


# ── the model's output is never trusted unchecked ──────────────────────────

def test_sentiment_counts_are_computed_from_the_labels_not_from_the_model():
    parsed = {"verdict": "ok", "video_sentiment": {"0": "negative", "1": "negative", "2": "positive"}}
    out = tp._clean_analysis(parsed, {"0", "1", "2"})
    assert out["sentiment"]["counts"]["negative"] == 2
    assert out["sentiment"]["labelled"] == 3


def test_a_hallucinated_video_id_is_stripped_everywhere():
    parsed = {
        "video_sentiment": {"0": "positive", "99": "negative"},
        "themes": [{"label": "Praise", "detail": "d", "video_ids": ["0", "99"]}],
    }
    out = tp._clean_analysis(parsed, {"0"})
    assert out["sentiment"]["counts"]["positive"] == 1
    assert out["themes"][0]["video_ids"] == ["0"]


def test_a_theme_with_no_surviving_citation_is_dropped_entirely():
    parsed = {"themes": [{"label": "Made up", "detail": "d", "video_ids": ["99"]}]}
    assert tp._clean_analysis(parsed, {"0"})["themes"] == []


def test_em_dashes_are_stripped_from_every_free_text_field():
    parsed = {
        "verdict": "They said — great things",
        "themes": [{"label": "A — theme", "detail": "some — detail", "video_ids": ["0"]}],
        "risk_flags": ["a — risk"],
    }
    out = tp._clean_analysis(parsed, {"0"})
    assert "—" not in out["verdict"]
    assert "—" not in out["risk_flags"][0]


def test_notable_videos_are_enriched_with_the_real_text_and_url():
    parsed = {"notable_videos": [{"video_id": "0", "why": "Widely watched."}]}
    digest_by_id = {"0": {"author": "jsmith", "text": "the real caption", "url": "https://tiktok.com/x"}}
    out = tp._clean_analysis(parsed, {"0"}, digest_by_id)
    assert out["notable_videos"][0]["author"] == "jsmith"
    assert out["notable_videos"][0]["url"] == "https://tiktok.com/x"


def test_the_json_scan_survives_a_brace_inside_a_string():
    raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
    assert tp._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


# ── analyze / build_pulse degradation ───────────────────────────────────────

def test_analyze_needs_videos():
    assert "error" in tp.analyze("Jane Doe", [])


def test_analyze_reports_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tracker import sci_source_tiktok
    videos = sci_source_tiktok.normalize([_video("1")])
    out = tp.analyze("Jane Doe", videos)
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_build_pulse_calls_a_genuine_zero_a_finding_not_an_error(monkeypatch):
    monkeypatch.setattr(tp, "collect_mentions", lambda n: ([], {}))
    out = tp.build_pulse("Jane Doe")
    assert out["video_count"] == 0
    assert "finding, not an error" in out["note"]
    assert out["analysis"] is None


def test_build_pulse_carries_source_errors_through(monkeypatch):
    from tracker import sci_source_tiktok
    videos = sci_source_tiktok.normalize([_video("1")])
    monkeypatch.setattr(tp, "collect_mentions", lambda n: (videos, {"tiktok_search": "boom"}))
    monkeypatch.setattr(tp, "analyze", lambda n, v: {"verdict": "ok"})
    out = tp.build_pulse("Jane Doe")
    assert out["errors"] == {"tiktok_search": "boom"}
    assert out["analysis"] == {"verdict": "ok"}


def test_build_pulse_never_raises_when_collection_explodes(monkeypatch):
    def boom(name):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(tp, "collect_mentions", boom)
    out = tp.build_pulse("Jane Doe")
    assert out["video_count"] == 0
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
    from tracker import sci_source_tiktok
    videos = sci_source_tiktok.normalize([_video("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(tp, "_anthropic",
                        lambda: _FakeClient('{"verdict": "cut off mid-sen', stop_reason="max_tokens"))
    out = tp.analyze("Jane Doe", videos)
    assert "max_tokens" in out["error"]
    assert "unreadable response" not in out["error"]


def test_analyze_still_reports_a_generic_unreadable_response_when_not_truncated(monkeypatch):
    from tracker import sci_source_tiktok
    videos = sci_source_tiktok.normalize([_video("1")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(tp, "_anthropic",
                        lambda: _FakeClient("Sorry, I can't help with that.", stop_reason="end_turn"))
    out = tp.analyze("Jane Doe", videos)
    assert out["error"] == "The TikTok conversation analysis returned an unreadable response."
