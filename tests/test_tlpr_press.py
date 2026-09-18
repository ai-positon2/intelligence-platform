"""tracker/tlpr_press.py -- earned media / press coverage about a person.

Mirrors tests/test_tlpr_reddit_pulse.py's contract: Claude judges (what the
coverage says, whether it's favorable), plain Python counts (how many
articles, from how many outlets, over what span). A model that is also
allowed to tally its own judgements produces a confident sentiment split
that does not match the articles it was derived from.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import tlpr_press as press  # noqa: E402


def _article(title="Jane Doe named to industry board", source="Trade Press",
            published="2026-05-02T10:00:00Z", summary=""):
    return {"title": title, "url": "https://example.com/%s" % title[:8],
           "summary": summary, "source": source, "published": published}


# ── collection: two independent sources, fault-isolated ──────────────────

def test_a_blank_name_collects_nothing_without_any_network_call(monkeypatch):
    monkeypatch.setattr(press, "_gdelt_articles",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not fetch")))
    articles, errors = press.collect_coverage("   ")
    assert articles == [] and errors == {}


def test_gdelt_is_queried_with_the_bare_unquoted_name(monkeypatch):
    captured = {}
    monkeypatch.setattr(press, "_gdelt_articles", lambda name, pool, days: captured.update(name=name) or [])
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    press.collect_coverage("Jane Doe")
    assert captured["name"] == "Jane Doe"


def test_no_serpapi_key_records_a_config_error_but_keeps_gdelt_results(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.setattr(press, "_gdelt_articles", lambda *a, **kw: [_article()])
    articles, errors = press.collect_coverage("Jane Doe")
    assert len(articles) == 1
    assert "not configured" in errors["serpapi"]


def test_serpapi_gets_the_name_quoted_as_an_exact_phrase(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "k")
    monkeypatch.setattr(press, "_gdelt_articles", lambda *a, **kw: [])
    queries = []

    def fake_serpapi(q, key, pool, days):
        queries.append(q)
        return []
    monkeypatch.setattr(press, "_serpapi_articles", fake_serpapi)
    press.collect_coverage("Jane Doe")
    assert queries == ['"Jane Doe"']


def test_a_company_hint_adds_a_second_qualified_serpapi_query(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "k")
    monkeypatch.setattr(press, "_gdelt_articles", lambda *a, **kw: [])
    queries = []
    monkeypatch.setattr(press, "_serpapi_articles",
                        lambda q, key, pool, days: queries.append(q) or [])
    press.collect_coverage("Jane Doe", "Acme Corp")
    assert queries == ['"Jane Doe"', '"Jane Doe" Acme Corp']


def test_duplicate_titles_across_both_sources_are_deduped(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "k")
    monkeypatch.setattr(press, "_gdelt_articles", lambda *a, **kw: [_article("Same Headline")])
    monkeypatch.setattr(press, "_serpapi_articles", lambda *a, **kw: [_article("same headline")])
    articles, _ = press.collect_coverage("Jane Doe")
    assert len(articles) == 1


def test_a_gdelt_crash_never_blocks_serpapi_results(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "k")

    def boom(*a, **kw):
        raise RuntimeError("gdelt down")
    monkeypatch.setattr(press, "_gdelt_articles", boom)
    monkeypatch.setattr(press, "_serpapi_articles", lambda *a, **kw: [_article("Still found")])
    articles, errors = press.collect_coverage("Jane Doe")
    assert len(articles) == 1
    assert "serpapi" not in errors


def test_a_serpapi_crash_never_blocks_gdelt_results(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "k")
    monkeypatch.setattr(press, "_gdelt_articles", lambda *a, **kw: [_article("From GDELT")])

    def boom(*a, **kw):
        raise RuntimeError("serpapi down")
    monkeypatch.setattr(press, "_serpapi_articles", boom)
    articles, errors = press.collect_coverage("Jane Doe")
    assert len(articles) == 1
    assert "No SerpAPI results" in errors["serpapi"]


# ── mechanical aggregation ────────────────────────────────────────────────

def test_aggregate_counts_by_source_and_month():
    articles = [
        _article("A", source="Outlet One", published="2026-05-02T10:00:00Z"),
        _article("B", source="Outlet One", published="2026-05-20T10:00:00Z"),
        _article("C", source="Outlet Two", published="2026-06-01T10:00:00Z"),
    ]
    agg = press.aggregate(articles)
    assert agg["article_count"] == 3
    assert agg["source_count"] == 2
    assert {"name": "Outlet One", "articles": 2} in agg["sources"]
    assert agg["timeline"] == [{"month": "2026-05", "articles": 2}, {"month": "2026-06", "articles": 1}]
    assert agg["earliest"] and agg["latest"]


def test_top_articles_are_ordered_by_recency():
    articles = [
        _article("Old", published="2026-01-01T10:00:00Z"),
        _article("New", published="2026-06-01T10:00:00Z"),
    ]
    assert press.aggregate(articles)["top_articles"][0]["title"] == "New"


def test_an_unparseable_date_does_not_crash_aggregation():
    articles = [_article("No date", published="")]
    agg = press.aggregate(articles)
    assert agg["article_count"] == 1
    assert agg["earliest"] is None


# ── digest ordering ────────────────────────────────────────────────────────

def test_digest_orders_by_recency_and_assigns_sequential_ids():
    articles = [_article("Old", published="2026-01-01T10:00:00Z"),
               _article("New", published="2026-06-01T10:00:00Z")]
    digest = press._digest(articles)
    assert digest[0]["title"] == "New" and digest[0]["id"] == "0"
    assert digest[1]["title"] == "Old" and digest[1]["id"] == "1"


def test_digest_carries_the_real_url_for_later_linking():
    digest = press._digest([_article("A")])
    assert digest[0]["url"] == "https://example.com/A"


# ── the model's output is never trusted unchecked ─────────────────────────

def test_sentiment_counts_are_computed_from_the_labels_not_from_the_model():
    parsed = {
        "verdict": "Mostly positive coverage.",
        "article_sentiment": {"0": "positive", "1": "negative", "2": "negative"},
        "themes": [], "notable_articles": [], "risk_flags": [],
        "sentiment": {"counts": {"positive": 99, "negative": 0}},
    }
    out = press._clean_analysis(parsed, {"0", "1", "2"})
    assert out["sentiment"]["counts"] == {"positive": 1, "neutral": 0, "negative": 2, "mixed": 0}
    assert out["sentiment"]["labelled"] == 3
    assert out["sentiment"]["negative_share"] == round(2 / 3, 3)


def test_a_hallucinated_article_id_is_stripped_everywhere():
    parsed = {
        "article_sentiment": {"0": "positive", "ZZZ": "negative"},
        "themes": [{"label": "T", "detail": "d", "article_ids": ["0", "ZZZ"]}],
        "notable_articles": [{"article_id": "ZZZ", "why": "invented"}],
        "risk_flags": [],
    }
    out = press._clean_analysis(parsed, {"0"})
    assert out["themes"][0]["article_ids"] == ["0"]
    assert out["notable_articles"] == []


def test_a_theme_with_no_surviving_citation_is_dropped_entirely():
    parsed = {"article_sentiment": {},
              "themes": [{"label": "Invented", "detail": "d", "article_ids": ["nope"]}],
              "notable_articles": [], "risk_flags": []}
    assert press._clean_analysis(parsed, {"0"})["themes"] == []


def test_em_dashes_are_stripped_from_every_free_text_field():
    parsed = {
        "verdict": "Well covered — no controversy.",
        "article_sentiment": {"0": "positive"},
        "themes": [{"label": "Coverage", "detail": "Praised widely — especially the launch.",
                    "article_ids": ["0"]}],
        "notable_articles": [{"article_id": "0", "why": "Widely cited — top piece."}],
        "risk_flags": ["A minor gripe — nothing major."],
    }
    out = press._clean_analysis(parsed, {"0"})
    assert "—" not in out["verdict"]
    assert "—" not in out["themes"][0]["detail"]
    assert "—" not in out["notable_articles"][0]["why"]
    assert "—" not in out["risk_flags"][0]


def test_notable_articles_are_enriched_with_the_real_title_and_url():
    """Unlike a comment_id or thread_id, an article_id has a real public URL
    behind it -- the resolved title/url/source must come from the digest
    that was actually sent, never invented by re-trusting the model's own
    reply for anything beyond the id and the "why"."""
    digest_by_id = {"0": {"title": "Real Headline", "url": "https://ex.com/a", "source": "Outlet"}}
    parsed = {"article_sentiment": {}, "themes": [], "risk_flags": [],
              "notable_articles": [{"article_id": "0", "why": "Sets the record straight."}]}
    out = press._clean_analysis(parsed, {"0"}, digest_by_id)
    assert out["notable_articles"][0]["title"] == "Real Headline"
    assert out["notable_articles"][0]["url"] == "https://ex.com/a"
    assert out["notable_articles"][0]["source"] == "Outlet"


def test_notable_articles_without_a_digest_map_still_work(monkeypatch):
    parsed = {"article_sentiment": {}, "themes": [], "risk_flags": [],
              "notable_articles": [{"article_id": "0", "why": "Still valid."}]}
    out = press._clean_analysis(parsed, {"0"})
    assert out["notable_articles"][0]["why"] == "Still valid."
    assert out["notable_articles"][0]["url"] is None


def test_the_json_scan_survives_a_brace_inside_a_string():
    raw = 'Here you go:\n```json\n{"verdict": "They said { was odd", "themes": []}\n```'
    assert press._extract_json_object(raw) == '{"verdict": "They said { was odd", "themes": []}'


# ── analyze / build_press degradation ─────────────────────────────────────

def test_analyze_needs_articles():
    assert "error" in press.analyze("Jane Doe", [])


def test_analyze_reports_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = press.analyze("Jane Doe", [_article()])
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_build_press_calls_a_genuine_zero_a_finding_not_an_error(monkeypatch):
    monkeypatch.setattr(press, "collect_coverage", lambda n, c=None: ([], {}))
    out = press.build_press("Jane Doe")
    assert out["article_count"] == 0
    assert "finding, not an error" in out["note"]
    assert out["analysis"] is None


def test_build_press_carries_source_errors_through(monkeypatch):
    monkeypatch.setattr(press, "collect_coverage",
                        lambda n, c=None: ([_article()], {"serpapi": "not configured"}))
    monkeypatch.setattr(press, "analyze", lambda n, a, c=None: {"verdict": "ok"})
    out = press.build_press("Jane Doe")
    assert out["errors"] == {"serpapi": "not configured"}
    assert out["analysis"] == {"verdict": "ok"}


def test_build_press_never_raises_when_collection_explodes(monkeypatch):
    def boom(name, company_hint=None):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(press, "collect_coverage", boom)
    out = press.build_press("Jane Doe")
    assert out["article_count"] == 0
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
    """Regression: a reply cut off mid-JSON by max_tokens (no closing brace,
    so _extract_json_object's brace-depth scan never returns) used to
    collapse into the exact same "unreadable response" text as a reply that
    wrote no JSON at all -- indistinguishable from a live run, and from a
    log line, whether the fix is "raise max_tokens" or something else
    entirely. stop_reason is on the response already; this just names it."""
    monkeypatch.setattr(press, "_anthropic",
                        lambda: _FakeClient('{"verdict": "cut off mid-sen', stop_reason="max_tokens"))
    out = press.analyze("Jane Doe", [_article()])
    assert "max_tokens" in out["error"]
    assert "unreadable response" not in out["error"]


def test_analyze_still_reports_a_generic_unreadable_response_when_not_truncated(monkeypatch):
    monkeypatch.setattr(press, "_anthropic",
                        lambda: _FakeClient("Sorry, I can't help with that.", stop_reason="end_turn"))
    out = press.analyze("Jane Doe", [_article()])
    assert out["error"] == "The press coverage analysis returned an unreadable response."
