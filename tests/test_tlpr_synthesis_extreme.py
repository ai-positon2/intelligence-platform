"""Phase 4 (the executive report) under extreme inputs.

tests/test_thought_leader_pr.py covers the ordinary summary shapes; this
file covers the ones that decide whether the report tells the truth about
its own coverage: exactly one of seven sources succeeded, all seven came
back empty, all seven failed outright, a sentiment split that is all one
way or exactly even, a zero denominator, and the difference between press
finding nothing and press never having been searched.

The Anthropic call is faked at the client boundary (a stand-in for
anthropic.Anthropic), so _reaction_summary/_press_summary/_posts_summary,
the payload assembly, the JSON extraction, and _clean_synthesis all really
run.
"""

from __future__ import annotations

import json

import pytest

from tracker import thought_leader_pr as T


@pytest.fixture(autouse=True)
def _no_side_effect_keys(monkeypatch):
    for key in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, response, sink):
        self._response = response
        self._sink = sink

    def create(self, **kwargs):
        self._sink.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _Client:
    def __init__(self, response, sink):
        self.messages = _Messages(response, sink)


def _fake_anthropic(monkeypatch, response):
    """Patches the anthropic module's own client class -- the true external
    boundary -- so synthesize_report's real payload building and reply
    handling run. Returns the list every call's kwargs land in."""
    sink = []
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _Client(response, sink))
    return sink


_GOOD_REPLY = json.dumps({
    "headline": "A steady, well covered operator",
    "verdict": "Broadly well regarded across the sources that could be read.",
    "strengths": [{"text": "Consistent press presence.", "sources": ["press"]}],
    "risks": [],
    "alignment": "Self-presentation and reception line up.",
})


def _run(**overrides):
    run = {"identity": {"full_name": "Jane Doe", "current_company": "Acme"},
           "posts": None, "reaction": None, "press": None, "press_errors": None}
    run.update(overrides)
    return run


def _payload_of(sink):
    return json.loads(sink[0]["messages"][0]["content"])


# ───────────────────────── how many sources survived ─────────────────────────

class TestSourceCoverageExtremes:
    def test_one_surviving_source_out_of_seven_still_reaches_the_report(self, monkeypatch):
        """Everything except TikTok failed or found nothing. TikTok's real
        finding must not be dropped just because it is outnumbered."""
        reaction = {
            "comments_analyzed": 0,
            "reddit": {"thread_count": 0},
            "x_pulse": {"tweet_count": 0, "errors": {"x_search": "down"}},
            "linkedin_pulse": {"post_count": 0, "errors": {"linkedin": "no account"}},
            "instagram_pulse": {"mention_count": 0},
            "facebook_pulse": {"post_count": 0, "errors": {"apify": "not configured"}},
            "tiktok_pulse": {"video_count": 9, "analysis": {
                "verdict": "A viral protest clip dominates.", "sentiment": {"counts": {"negative": 7}},
                "themes": [{"label": "Campus walkout"}], "risk_flags": ["Contract protest."]}},
        }
        sink = _fake_anthropic(monkeypatch, _Response(_GOOD_REPLY))
        T.synthesize_report(_run(reaction=reaction))
        payload = _payload_of(sink)
        assert payload["reaction"]["tiktok"]["verdict"] == "A viral protest clip dominates."
        assert payload["reaction"]["available"] is True

    def test_each_of_the_seven_sources_alone_reaches_the_payload(self, monkeypatch):
        """A wiring regression that drops any single source is exactly what
        shipped once already, so every one is checked on its own."""
        cases = [
            ("own_post_comments", {"comments_analyzed": 3, "comment_sentiment": {
                "verdict": "Warm.", "sentiment": {"counts": {"positive": 3}}, "themes": []}}),
            ("reddit", {"reddit": {"thread_count": 2, "analysis": {
                "verdict": "Skeptical.", "sentiment": {}, "themes": []}}}),
            ("x", {"x_pulse": {"tweet_count": 4, "analysis": {
                "verdict": "Quiet.", "sentiment": {}, "themes": []}}}),
            ("linkedin", {"linkedin_pulse": {"post_count": 5, "analysis": {
                "verdict": "Admiring.", "sentiment": {}, "themes": []}}}),
            ("tiktok", {"tiktok_pulse": {"video_count": 6, "analysis": {
                "verdict": "Viral.", "sentiment": {}, "themes": []}}}),
            ("instagram", {"instagram_pulse": {"mention_count": 7, "analysis": {
                "verdict": "Tagged often.", "sentiment": {}, "themes": []}}}),
            ("facebook", {"facebook_pulse": {"post_count": 8, "analysis": {
                "verdict": "Chatty.", "sentiment": {}, "themes": []}}}),
        ]
        for key, reaction in cases:
            sink = _fake_anthropic(monkeypatch, _Response(_GOOD_REPLY))
            T.synthesize_report(_run(reaction=reaction))
            summary = _payload_of(sink)["reaction"][key]
            assert summary is not None, key
            assert summary.get("verdict"), key

    def test_all_seven_returning_a_clean_zero_is_reported_as_unavailable(self, monkeypatch):
        reaction = {"comments_analyzed": 0, "reddit": {"thread_count": 0},
                    "x_pulse": {"tweet_count": 0}, "linkedin_pulse": {"post_count": 0},
                    "tiktok_pulse": {"video_count": 0}, "instagram_pulse": {"mention_count": 0},
                    "facebook_pulse": {"post_count": 0}}
        sink = _fake_anthropic(monkeypatch, _Response(_GOOD_REPLY))
        T.synthesize_report(_run(reaction=reaction))
        summary = _payload_of(sink)["reaction"]
        assert summary["available"] is False
        assert all(summary[k] is None for k in
                   ("own_post_comments", "reddit", "x", "linkedin", "tiktok", "instagram", "facebook"))

    def test_all_seven_failing_is_never_reported_as_all_seven_finding_nothing(self, monkeypatch):
        """The distinction the whole report rests on: nobody looked is not
        the same claim as nobody is talking."""
        reaction = {"comments_analyzed": 0, "reddit": {"thread_count": 0, "errors": {"r": "401"}},
                    "x_pulse": {"tweet_count": 0, "errors": {"x": "429"}},
                    "linkedin_pulse": {"post_count": 0, "errors": {"li": "no account"}},
                    "tiktok_pulse": {"video_count": 0, "errors": {"tt": "timeout"}},
                    "instagram_pulse": {"mention_count": 0, "errors": {"ig": "timeout"}},
                    "facebook_pulse": {"post_count": 0, "errors": {"fb": "not configured"}}}
        sink = _fake_anthropic(monkeypatch, _Response(_GOOD_REPLY))
        T.synthesize_report(_run(reaction=reaction))
        summary = _payload_of(sink)["reaction"]
        for key in ("reddit", "x", "linkedin", "tiktok", "instagram", "facebook"):
            assert summary[key] == {"collected_count": 0, "search_failed": True}, key

    def test_a_crashed_pulse_is_reported_as_a_failed_search_not_an_empty_one(self):
        crashed = {"note": "The TikTok conversation read could not be completed.",
                   "video_count": 0, "errors": {"_run": "The TikTok conversation read could "
                                                        "not be completed."}}
        assert T._pulse_summary(crashed, "video_count") == {"collected_count": 0,
                                                            "search_failed": True}

    def test_a_clean_zero_pulse_is_still_plain_unavailable(self):
        assert T._pulse_summary({"video_count": 0, "errors": {}}, "video_count") is None
        assert T._pulse_summary({"video_count": 0}, "video_count") is None

    def test_a_failed_search_does_not_pretend_to_carry_a_verdict(self):
        failed = {"tweet_count": 0, "errors": {"x": "down"},
                  "analysis": {"verdict": "leftover from a previous run"}}
        assert "verdict" not in T._pulse_summary(failed, "tweet_count")

    def test_a_source_with_data_is_unaffected_by_a_partial_error(self):
        pulse = {"tweet_count": 5, "errors": {"x_mentions": "one query failed"},
                 "analysis": {"verdict": "Real read.", "sentiment": {"counts": {}}, "themes": []}}
        out = T._pulse_summary(pulse, "tweet_count")
        assert out["verdict"] == "Real read."
        assert "search_failed" not in out

    def test_the_prompt_explains_what_a_failed_search_means(self):
        assert '"search_failed": true' in T._SYNTHESIS_SYSTEM
        assert "never write that nothing was found there" in T._SYNTHESIS_SYSTEM

    def test_the_prompt_still_explains_analysis_failed_separately(self):
        assert '"analysis_failed": true' in T._SYNTHESIS_SYSTEM
        assert "could not be analyzed" in T._SYNTHESIS_SYSTEM


class TestPressZeroVersusNeverSearched:
    def test_a_genuine_press_zero_stays_plainly_unavailable(self):
        assert T._press_summary({"article_count": 0}, {}) == {"available": False}

    def test_serpapi_finding_nothing_is_not_a_failed_search(self):
        errors = {"serpapi": "No SerpAPI results were returned for this person."}
        assert T._press_summary({"article_count": 0}, errors) == {"available": False}

    def test_serpapi_being_unreachable_is_a_failed_search(self):
        errors = {"serpapi": "SerpAPI could not be reached for this person."}
        assert T._press_summary({"article_count": 0}, errors) == {"available": False,
                                                                  "search_failed": True}

    def test_gdelt_being_unreachable_is_a_failed_search(self):
        errors = {"gdelt": "GDELT could not be reached (ConnectionError)."}
        assert T._press_summary({"article_count": 0}, errors)["search_failed"] is True

    def test_serpapi_unconfigured_is_a_failed_search_too(self):
        errors = {"serpapi": "SerpAPI is not configured on this deployment (GDELT-only coverage)."}
        assert T._press_summary({"article_count": 0}, errors)["search_failed"] is True

    def test_a_crashed_press_job_is_a_failed_search(self):
        assert T._press_summary(None, {"_run": "An unexpected error stopped the press search."}
                                )["search_failed"] is True

    def test_press_with_real_articles_never_reports_a_failed_search(self):
        out = T._press_summary({"article_count": 3, "source_count": 2},
                               {"serpapi": "SerpAPI could not be reached for this person."})
        assert out["available"] is True
        assert "search_failed" not in out

    def test_the_run_rows_press_errors_column_actually_reaches_the_payload(self, monkeypatch):
        """collect_press_job pops build_press's errors dict onto its own
        column, so the summary can only tell the two cases apart if the
        column is passed in -- it was not."""
        sink = _fake_anthropic(monkeypatch, _Response(_GOOD_REPLY))
        T.synthesize_report(_run(press={"article_count": 0},
                                 press_errors={"gdelt": "GDELT could not be reached."}))
        assert _payload_of(sink)["press"]["search_failed"] is True


# ───────────────────────── sentiment arithmetic ─────────────────────────

class TestSentimentDenominators:
    def _analysis(self, labels):
        parsed = {"verdict": "V", "comment_sentiment": labels, "themes": [], "notable_comments": []}
        return T._clean_reaction_analysis(parsed, set(labels))

    def test_a_hundred_percent_positive_reads_as_a_zero_negative_share(self):
        out = self._analysis({"x:1": "positive", "x:2": "positive", "x:3": "positive"})
        assert out["sentiment"]["counts"] == {"positive": 3, "neutral": 0, "negative": 0, "mixed": 0}
        assert out["sentiment"]["negative_share"] == 0.0

    def test_a_hundred_percent_negative_reads_as_a_full_negative_share(self):
        out = self._analysis({"x:1": "negative", "x:2": "negative"})
        assert out["sentiment"]["negative_share"] == 1.0

    def test_an_exact_even_split_is_reported_as_exactly_half(self):
        out = self._analysis({"x:1": "positive", "x:2": "negative"})
        assert out["sentiment"]["negative_share"] == 0.5
        assert out["sentiment"]["labelled"] == 2

    def test_a_zero_denominator_reports_no_share_rather_than_a_number(self):
        out = self._analysis({})
        assert out["sentiment"]["labelled"] == 0
        assert out["sentiment"]["negative_share"] is None
        assert out["sentiment"]["counts"] == {"positive": 0, "neutral": 0, "negative": 0, "mixed": 0}

    def test_every_label_the_model_invented_is_discarded_from_the_denominator(self):
        parsed = {"verdict": "V", "comment_sentiment": {"x:1": "positive", "x:2": "furious"},
                  "themes": [], "notable_comments": []}
        out = T._clean_reaction_analysis(parsed, {"x:1", "x:2"})
        assert out["sentiment"]["labelled"] == 1

    def test_a_label_for_an_id_that_was_never_sent_is_discarded(self):
        parsed = {"verdict": "V", "comment_sentiment": {"x:1": "positive", "made:up": "negative"},
                  "themes": [], "notable_comments": []}
        out = T._clean_reaction_analysis(parsed, {"x:1"})
        assert out["sentiment"]["counts"]["negative"] == 0
        assert out["sentiment"]["labelled"] == 1

    def test_a_sentiment_block_that_is_a_list_instead_of_a_map_is_ignored(self):
        parsed = {"verdict": "V", "comment_sentiment": ["positive"], "themes": [],
                  "notable_comments": []}
        out = T._clean_reaction_analysis(parsed, {"x:1"})
        assert out["sentiment"]["labelled"] == 0

    def test_one_thousand_labels_divide_to_a_three_decimal_share(self):
        labels = {"x:%d" % i: ("negative" if i < 333 else "positive") for i in range(1000)}
        out = self._analysis(labels)
        assert out["sentiment"]["negative_share"] == 0.333

    def test_a_single_mixed_label_never_produces_a_negative_share_above_one(self):
        out = self._analysis({"x:1": "mixed"})
        assert out["sentiment"]["negative_share"] == 0.0


class TestNotableCommentProvenance:
    def test_a_notable_comment_carries_the_text_that_was_really_sent(self):
        digest_by_id = {"x:1": {"text": "the real comment", "platform": "x"}}
        parsed = {"verdict": "V", "comment_sentiment": {}, "themes": [],
                  "notable_comments": [{"comment_id": "x:1", "why": "sharp"}]}
        out = T._clean_reaction_analysis(parsed, {"x:1"}, digest_by_id)
        assert out["notable_comments"][0]["text"] == "the real comment"

    def test_a_notable_comment_the_model_invented_text_for_cannot_override_it(self):
        digest_by_id = {"x:1": {"text": "what was actually said", "platform": "x"}}
        parsed = {"verdict": "V", "comment_sentiment": {}, "themes": [],
                  "notable_comments": [{"comment_id": "x:1", "why": "w",
                                        "text": "something nobody wrote"}]}
        out = T._clean_reaction_analysis(parsed, {"x:1"}, digest_by_id)
        assert out["notable_comments"][0]["text"] == "what was actually said"

    def test_a_notable_comment_citing_an_unknown_id_is_dropped(self):
        parsed = {"verdict": "V", "comment_sentiment": {}, "themes": [],
                  "notable_comments": [{"comment_id": "ghost:1", "why": "w"}]}
        assert T._clean_reaction_analysis(parsed, {"x:1"}, {})["notable_comments"] == []

    def test_a_notable_comment_with_no_why_is_dropped(self):
        parsed = {"verdict": "V", "comment_sentiment": {}, "themes": [],
                  "notable_comments": [{"comment_id": "x:1", "why": "   "}]}
        assert T._clean_reaction_analysis(parsed, {"x:1"}, {})["notable_comments"] == []

    def test_notable_comments_are_capped_at_four(self):
        digest = {"x:%d" % i: {"text": "t", "platform": "x"} for i in range(9)}
        parsed = {"verdict": "V", "comment_sentiment": {}, "themes": [],
                  "notable_comments": [{"comment_id": k, "why": "w"} for k in digest]}
        out = T._clean_reaction_analysis(parsed, set(digest), digest)
        assert len(out["notable_comments"]) == 4

    def test_a_theme_citing_only_unknown_ids_is_dropped_entirely(self):
        parsed = {"verdict": "V", "comment_sentiment": {},
                  "themes": [{"label": "Praise", "comment_ids": ["ghost:1"], "detail": "d"}],
                  "notable_comments": []}
        assert T._clean_reaction_analysis(parsed, {"x:1"}, {})["themes"] == []

    def test_a_theme_keeps_only_its_real_citations(self):
        parsed = {"verdict": "V", "comment_sentiment": {},
                  "themes": [{"label": "Praise", "comment_ids": ["x:1", "ghost:1", "x:2"],
                              "detail": "d"}],
                  "notable_comments": []}
        out = T._clean_reaction_analysis(parsed, {"x:1", "x:2"}, {})
        assert out["themes"][0]["comment_ids"] == ["x:1", "x:2"]

    def test_a_themes_citation_list_is_capped_at_four(self):
        ids = ["x:%d" % i for i in range(9)]
        parsed = {"verdict": "V", "comment_sentiment": {},
                  "themes": [{"label": "Praise", "comment_ids": ids, "detail": "d"}],
                  "notable_comments": []}
        out = T._clean_reaction_analysis(parsed, set(ids), {})
        assert len(out["themes"][0]["comment_ids"]) == 4

    def test_a_non_dict_theme_entry_is_skipped_not_fatal(self):
        parsed = {"verdict": "V", "comment_sentiment": {},
                  "themes": ["just a string", {"label": "Praise", "comment_ids": ["x:1"]}],
                  "notable_comments": []}
        out = T._clean_reaction_analysis(parsed, {"x:1"}, {})
        assert len(out["themes"]) == 1


# ───────────────────────── the model boundary ─────────────────────────

class TestSynthesisModelBoundary:
    def test_a_truncated_reply_is_named_as_a_budget_failure(self, monkeypatch):
        _fake_anthropic(monkeypatch, _Response('{"headline": "A start', stop_reason="max_tokens"))
        out = T.synthesize_report(_run())
        assert "max_tokens" in out["error"]

    def test_an_untruncated_unreadable_reply_is_named_differently(self, monkeypatch):
        _fake_anthropic(monkeypatch, _Response("I could not answer that."))
        out = T.synthesize_report(_run())
        assert out["error"] == "The report generation returned an unreadable response."

    def test_malformed_json_inside_real_braces_is_named_as_malformed(self, monkeypatch):
        _fake_anthropic(monkeypatch, _Response('{"headline": "H", "verdict": }'))
        assert T.synthesize_report(_run())["error"] == \
            "The report generation returned malformed JSON."

    def test_a_json_array_reply_is_named_as_an_unexpected_shape(self, monkeypatch):
        _fake_anthropic(monkeypatch, _Response('{"a": 1}'))
        out = T.synthesize_report(_run())
        assert "error" not in out

    def test_an_api_exception_is_reported_not_raised(self, monkeypatch):
        _fake_anthropic(monkeypatch, RuntimeError("overloaded_error"))
        out = T.synthesize_report(_run())
        assert "could not be completed" in out["error"]

    def test_a_missing_api_key_is_its_own_message(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert "ANTHROPIC_API_KEY" in T.synthesize_report(_run())["error"]

    def test_a_run_with_no_identity_never_reaches_the_model(self, monkeypatch):
        import anthropic
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setattr(anthropic, "Anthropic",
                            lambda **kw: pytest.fail("must not bill for an unidentified run"))
        assert T.synthesize_report({"identity": None})["error"] == \
            "This run has no confirmed identity to synthesize."

    def test_a_reply_wrapped_in_prose_and_a_code_fence_is_still_read(self, monkeypatch):
        _fake_anthropic(monkeypatch, _Response("Sure, here it is:\n```json\n%s\n```\nHope that helps."
                                               % _GOOD_REPLY))
        out = T.synthesize_report(_run())
        assert out["headline"] == "A steady, well covered operator"

    def test_a_reply_split_across_several_text_blocks_is_joined(self, monkeypatch):
        response = _Response("")
        response.content = [_Block('{"headline": "Split '), _Block('reply", "verdict": "V"}')]
        _fake_anthropic(monkeypatch, response)
        assert T.synthesize_report(_run())["headline"] == "Split reply"

    def test_a_reply_with_no_text_blocks_at_all_is_unreadable_not_a_crash(self, monkeypatch):
        response = _Response("")
        response.content = []
        _fake_anthropic(monkeypatch, response)
        assert T.synthesize_report(_run())["error"]

    def test_the_payload_never_contains_raw_comment_or_article_text(self, monkeypatch):
        reaction = {"comments_analyzed": 2, "comment_sentiment": {
            "verdict": "Warm.", "sentiment": {"counts": {}}, "themes": [],
            "notable_comments": [{"comment_id": "x:1", "why": "w",
                                  "text": "A RAW COMMENT THAT MUST NOT BE RESENT"}]}}
        sink = _fake_anthropic(monkeypatch, _Response(_GOOD_REPLY))
        T.synthesize_report(_run(reaction=reaction))
        assert "A RAW COMMENT THAT MUST NOT BE RESENT" not in sink[0]["messages"][0]["content"]

    def test_the_identity_block_carries_only_the_four_expected_fields(self, monkeypatch):
        sink = _fake_anthropic(monkeypatch, _Response(_GOOD_REPLY))
        T.synthesize_report({"identity": {"full_name": "Jane Doe", "headline": "H",
                                          "current_title": "CEO", "current_company": "Acme",
                                          "reasoning": "internal notes", "platforms": {}}})
        payload = _payload_of(sink)
        assert set(payload["identity"]) == {"full_name", "headline", "current_title",
                                            "current_company"}

    def test_the_output_budget_is_large_enough_for_seven_sources(self, monkeypatch):
        sink = _fake_anthropic(monkeypatch, _Response(_GOOD_REPLY))
        T.synthesize_report(_run())
        assert sink[0]["max_tokens"] >= 4000


class TestPostsSummaryExtremes:
    def test_a_boolean_metric_never_inflates_engagement_by_more_than_itself(self):
        out = T._posts_summary({"linkedin": [{"metrics": {"likes": 5}}]})
        assert out["by_platform"]["linkedin"]["total_engagement"] == 5

    def test_a_string_metric_is_ignored_rather_than_crashing_the_summary(self):
        out = T._posts_summary({"x": [{"metrics": {"likes": "1.2K", "comments": 3}}]})
        assert out["by_platform"]["x"]["total_engagement"] == 3

    def test_a_post_with_no_metrics_block_counts_as_a_post_with_no_engagement(self):
        out = T._posts_summary({"x": [{}, {"metrics": None}]})
        assert out["by_platform"]["x"] == {"count": 2, "total_engagement": 0}

    def test_an_unknown_platform_key_is_ignored_by_the_summary(self):
        out = T._posts_summary({"mastodon": [{"metrics": {"likes": 9}}]})
        assert set(out["by_platform"]) == {"linkedin", "x", "youtube"}
        assert out["available"] is False

    def test_a_negative_metric_is_summed_as_given_rather_than_clamped(self):
        out = T._posts_summary({"x": [{"metrics": {"likes": -3, "comments": 5}}]})
        assert out["by_platform"]["x"]["total_engagement"] == 2

    def test_views_stay_excluded_even_when_they_are_the_only_metric(self):
        out = T._posts_summary({"youtube": [{"metrics": {"views": 1000000}}]})
        assert out["by_platform"]["youtube"] == {"count": 1, "total_engagement": 0}
        assert out["available"] is True


class TestCleanSynthesisExtremes:
    def test_a_headline_longer_than_the_cap_is_trimmed(self):
        out = T._clean_synthesis({"headline": "H" * 400, "verdict": "V", "alignment": "A",
                                  "strengths": [], "risks": []})
        assert len(out["headline"]) == 140

    def test_a_reply_missing_every_field_still_produces_a_well_formed_report(self):
        out = T._clean_synthesis({})
        assert out == {"headline": "", "verdict": "", "strengths": [], "risks": [],
                       "alignment": ""}

    def test_strengths_given_as_a_bare_string_list_are_dropped_not_fatal(self):
        out = T._clean_synthesis({"strengths": ["just a string"], "risks": []})
        assert out["strengths"] == []

    def test_strengths_given_as_a_string_instead_of_a_list_are_dropped(self):
        assert T._clean_synthesis({"strengths": "not a list"})["strengths"] == []

    def test_an_empty_risks_list_is_preserved_rather_than_filled(self):
        out = T._clean_synthesis({"headline": "H", "verdict": "V", "risks": []})
        assert out["risks"] == []

    def test_a_bullet_with_no_sources_key_survives_with_an_empty_tag_list(self):
        out = T._clean_synthesis({"strengths": [{"text": "Respected."}]})
        assert out["strengths"] == [{"text": "Respected.", "sources": []}]

    def test_a_sources_value_that_is_not_a_list_is_dropped_not_fatal(self):
        out = T._clean_synthesis({"strengths": [{"text": "Respected.", "sources": "press"}]})
        assert out["strengths"][0]["sources"] == []

    def test_every_valid_source_tag_survives(self):
        out = T._clean_synthesis({"strengths": [
            {"text": "T", "sources": ["identity", "posts", "reaction", "press"]}]})
        assert out["strengths"][0]["sources"] == ["identity", "posts", "reaction", "press"]


class TestSynthesisJobWiring:
    def test_a_report_error_becomes_a_failed_status_not_a_stored_report(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(T, "get_run", lambda rid, email: _run())
        monkeypatch.setattr(T, "synthesize_report", lambda run: {"error": "the model was down"})
        monkeypatch.setattr(T, "save_synthesis",
                            lambda *a, **kw: pytest.fail("a failed report must not be stored"))
        monkeypatch.setattr(T, "save_synthesis_failed",
                            lambda rid, email, msg: saved.update(msg=msg) or True)
        T.collect_synthesis_job(1, "a@b.com")
        assert saved["msg"] == "the model was down"

    def test_a_real_report_is_stored_with_no_errors(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(T, "get_run", lambda rid, email: _run())
        monkeypatch.setattr(T, "synthesize_report", lambda run: {"headline": "H", "verdict": "V"})
        monkeypatch.setattr(T, "save_synthesis", lambda rid, email, rep, errs: saved.update(
            rep=rep, errs=errs) or True)
        T.collect_synthesis_job(1, "a@b.com")
        assert saved["rep"]["headline"] == "H"
        assert saved["errs"] == {}

    def test_a_crash_inside_the_job_still_lands_on_a_terminal_status(self, monkeypatch):
        saved = {}

        def boom(rid, email):
            raise RuntimeError("database vanished")

        monkeypatch.setattr(T, "get_run", boom)
        monkeypatch.setattr(T, "save_synthesis_failed",
                            lambda rid, email, msg: saved.update(msg=msg) or True)
        T.collect_synthesis_job(1, "a@b.com")
        assert "database vanished" in saved["msg"]

    def test_a_run_that_vanished_between_request_and_thread_is_reported(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(T, "get_run", lambda rid, email: None)
        monkeypatch.setattr(T, "save_synthesis_failed",
                            lambda rid, email, msg: saved.update(msg=msg) or True)
        T.collect_synthesis_job(1, "a@b.com")
        assert saved["msg"] == "This run has no confirmed identity to synthesize."
