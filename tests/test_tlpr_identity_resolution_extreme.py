"""Thought Leader Intelligence identity resolution under extreme and
adversarial name input.

Identity is the highest-stakes step in this agent: every later phase
researches whoever Phase 0 returns, so a wrong match makes the entire
report about the wrong human being. tests/test_thought_leader_pr.py covers
the happy path and the "Rahul Gandhi" Apollo-anchoring incident; this file
covers what the existing suite does not: very common names, shared names
across unrelated fields, diacritics and non-Latin scripts, titles and
suffixes, mononyms, homoglyph impersonation, blank/whitespace/enormous
input, and a public figure with no social presence at all.

Every test drives the real functions. The only things mocked are the true
external boundaries: claude_websearch.ask (the Anthropic call),
apollo_client.search_people (the Apollo HTTP call), unipile_client.
get_user_profile, and sci_youtube_client.resolve_company_channel.
"""

from __future__ import annotations

import pytest

from tracker import claude_websearch, sci_name_match, thought_leader_pr as T


def _reply(text: str, search_count: int = 3) -> dict:
    return {"text": text, "search_count": search_count, "error": None,
            "usage": {}, "stop_reason": "end_turn"}


def _identity_json(**overrides) -> str:
    import json
    base = {"confidence": "high", "reasoning": "Confirmed across several sources.",
            "is_public_figure": True, "full_name": "Jane Doe"}
    base.update(overrides)
    return json.dumps(base)


@pytest.fixture(autouse=True)
def _no_side_effect_keys(monkeypatch):
    for key in ("APOLLO_API_KEY", "YOUTUBE_API_KEY", "DATABASE_URL", "APIFY_API_TOKEN",
                "UNIPILE_API_KEY", "UNIPILE_DSN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _stub_platforms(monkeypatch):
    monkeypatch.setattr(T, "_resolve_linkedin_platform",
                        lambda url, full_name="": {"url": url, "resolved": False, "provider_id": None,
                                                   "headline": None, "photo_url": None, "note": "stub"})
    monkeypatch.setattr(T, "_resolve_youtube_platform",
                        lambda name: {"url": None, "title": None, "resolved": False, "note": "stub"})


# ───────────────────────── very common names ─────────────────────────

class TestVeryCommonNames:
    """A name thousands of real people share. The whole design is that
    Apollo is a lead, never a conclusion, and that the model may refuse."""

    def test_six_different_john_smiths_are_all_offered_as_distinct_choices(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        rows = [{"full_name": "John Smith", "organization_name": "Company %d" % i,
                 "title": "Role %d" % i} for i in range(6)]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        candidates, error = T.search_name_candidates("John Smith")
        assert error is None
        assert len(candidates) == 6
        assert len({c["company"] for c in candidates}) == 6

    def test_two_john_smiths_at_the_same_company_collapse_to_one_choice(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        rows = [{"full_name": "John Smith", "organization_name": "Acme", "title": "CEO"},
                {"full_name": "john smith", "organization_name": "ACME", "title": "CTO"}]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        candidates, _ = T.search_name_candidates("John Smith")
        assert len(candidates) == 1

    def test_the_candidate_list_is_capped_at_max_name_candidates(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        captured = {}

        def fake_search(filters, api_key, per_page=None):
            captured["filters"] = filters
            captured["per_page"] = per_page
            return []

        monkeypatch.setattr(T.apollo_client, "search_people", fake_search)
        T.search_name_candidates("John Smith")
        assert captured["filters"]["max_people"] == T.MAX_NAME_CANDIDATES
        assert captured["per_page"] == T.MAX_NAME_CANDIDATES

    def test_a_common_name_with_a_low_confidence_reply_is_refused_not_guessed(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
            confidence="low", full_name="John Smith",
            reasoning="At least four public figures share this name.")))
        out = T.resolve_identity("John Smith")
        assert out["ok"] is False
        assert out["identity"] is None

    def test_a_company_hint_reaches_both_apollo_and_the_grounding_prompt(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        _stub_platforms(monkeypatch)
        seen = {}
        monkeypatch.setattr(T.apollo_client, "search_people",
                            lambda filters, api_key, per_page=None: seen.update(filters=filters) or [])
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda system, user, **kw: seen.update(user=user) or _reply(
                                _identity_json(full_name="John Smith")))
        T.resolve_identity("John Smith", company_hint="Acme Robotics")
        assert seen["filters"]["keywords"] == "John Smith Acme Robotics"
        assert "Acme Robotics" in seen["user"]


class TestSameNameDifferentFields:
    """An athlete and an academic who really do share a name. The Apollo
    row must not be allowed to decide which one the report is about."""

    def test_an_apollo_row_for_the_other_bearer_is_dropped_once_the_model_names_the_real_one(
            self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        athlete = {"full_name": "Michael Jordan", "title": "Athlete",
                   "organization_name": "Sports Inc",
                   "linkedin_url": "https://www.linkedin.com/in/athlete-mj/",
                   "photo_url": "https://img.example/athlete.jpg"}
        monkeypatch.setattr(T, "_best_apollo_candidate", lambda *a, **kw: athlete)
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
            full_name="Michael B. Jordan Whitfield", current_title="Professor of Law",
            current_company="Stanford")))
        out = T.resolve_identity("Michael Jordan")
        identity = out["identity"]
        assert identity["source"]["apollo_matched"] is False
        assert identity["current_title"] == "Professor of Law"
        assert identity["photo_url"] != "https://img.example/athlete.jpg"
        assert identity["platforms"]["linkedin"]["url"] is None

    def test_an_apollo_row_that_still_matches_the_confirmed_name_is_kept(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        row = {"full_name": "Michael Jordan", "title": "Professor of Law",
               "organization_name": "Stanford"}
        monkeypatch.setattr(T, "_best_apollo_candidate", lambda *a, **kw: row)
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
            full_name="Michael Jordan")))
        out = T.resolve_identity("Michael Jordan")
        assert out["identity"]["source"]["apollo_matched"] is True
        assert out["identity"]["current_company"] == "Stanford"

    def test_a_shared_surname_alone_never_qualifies_as_the_same_person(self):
        assert T._looks_like_same_person("Serena Williams", "Robin Williams") is False
        assert T._looks_like_same_person("Serena Williams", "Williams") is False

    def test_a_name_that_is_a_strict_prefix_of_another_person_is_still_rejected(self):
        assert T._looks_like_same_person("Ann Marie Slaughter", "Ann Slaughter") is False


# ───────────────────────── scripts, accents, transliteration ─────────────────────────

class TestDiacriticsAndNonLatinScripts:
    """sci_name_match.name_tokens is ASCII-only, which made every person
    whose name is not written in ASCII match NOBODY -- including their own
    verified LinkedIn profile. person_name_tokens is the fix; these tests
    fail if it is reverted."""

    @pytest.mark.parametrize("written,typed", [
        ("José Ángel Gurría", "Jose Angel Gurria"),
        ("Zoë Baird", "Zoe Baird"),
        ("Søren Kierkegaard", "Soren Kierkegaard"),
        ("Ana Botín", "Ana Botin"),
    ])
    def test_a_name_typed_without_its_accents_matches_the_accented_spelling(self, written, typed):
        assert T._looks_like_same_person(typed, written) is True
        assert T._looks_like_same_person(written, typed) is True

    @pytest.mark.parametrize("name", ["习近平", "Владимир Познер", "نجيب ساويرس", "नरेंद्र मोदी", "김정은"])
    def test_an_identical_non_latin_name_matches_itself(self, name):
        assert T._looks_like_same_person(name, name) is True

    @pytest.mark.parametrize("name,other", [
        ("习近平", "李克强"),
        ("Владимир Познер", "Сергей Иванов"),
    ])
    def test_two_different_non_latin_names_still_do_not_match(self, name, other):
        assert T._looks_like_same_person(name, other) is False

    def test_a_non_latin_profile_with_the_same_name_is_not_called_a_different_person(self):
        assert T._profile_name_disagrees("习近平", {"name": "习近平"}) is False

    def test_a_non_latin_profile_naming_someone_else_is_still_rejected(self):
        assert T._profile_name_disagrees("习近平", {"name": "李克强"}) is True

    def test_a_non_latin_named_subject_can_actually_verify_their_linkedin(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(T.unipile_client, "get_user_profile",
                            lambda slug, account_id: ({"name": "李显龙", "provider_id": "urn:li:9",
                                                       "headline": "Prime Minister"}, None))
        out = T._resolve_linkedin_platform("https://www.linkedin.com/in/leehsienloong/", "李显龙")
        assert out["resolved"] is True
        assert out["provider_id"] == "urn:li:9"

    def test_a_mixed_script_name_matches_on_its_latin_half(self):
        assert T._looks_like_same_person("Jack Ma 马云", "Jack Ma") is True

    def test_person_name_tokens_is_unchanged_for_plain_ascii_names(self):
        for value in ("Rahul Gandhi", "Jane R. Doe", "The Rock", "Elon Reeve Musk", ""):
            assert sci_name_match.person_name_tokens(value) == sci_name_match.name_tokens(value)

    def test_a_youtube_channel_for_a_non_latin_named_person_is_no_longer_rejected_outright(
            self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        monkeypatch.setattr(T.sci_youtube_client, "resolve_company_channel",
                            lambda name, key: {"title": "김연아", "channel_id": "UC1",
                                               "profile_url": "https://youtube.com/@yunakim"})
        out = T._resolve_youtube_platform("김연아")
        assert out["resolved"] is True


class TestTitlesSuffixesAndCredentials:
    def test_a_doctor_prefix_on_the_search_does_not_stop_a_plain_name_matching(self):
        assert T._looks_like_same_person("Dr Jane Doe", "Jane Doe") is False
        assert T._looks_like_same_person("Jane Doe", "Dr. Jane Doe") is True

    def test_a_generational_suffix_on_the_candidate_is_extra_not_disqualifying(self):
        assert T._looks_like_same_person("Martin Luther King", "Martin Luther King Jr") is True
        assert T._looks_like_same_person("John D Rockefeller", "John D Rockefeller III") is True

    def test_credentials_on_the_candidate_do_not_break_the_match(self):
        assert T._looks_like_same_person("Jane Doe", "Jane Doe, MD, PhD") is True

    def test_a_suffix_typed_into_the_search_must_also_appear_on_the_candidate(self):
        assert T._looks_like_same_person("Martin Luther King Jr", "Martin Luther King") is False

    def test_punctuation_only_differences_never_matter(self):
        assert T._looks_like_same_person("Jean-Claude Van Damme", "Jean Claude Van Damme") is True
        assert T._looks_like_same_person("O'Brien, Conan", "Conan O Brien") is True


class TestMononymsAndStageNames:
    def test_a_single_word_stage_name_matches_itself(self):
        assert T._looks_like_same_person("Beyonce", "Beyonce") is True

    def test_a_mononym_matches_a_longer_legal_name_that_contains_it(self):
        assert T._looks_like_same_person("Rihanna", "Robyn Rihanna Fenty") is True

    def test_a_mononym_does_not_match_an_unrelated_person(self):
        assert T._looks_like_same_person("Prince", "Prince Harry Windsor") is True
        assert T._looks_like_same_person("Prince Harry", "Prince") is False

    def test_a_mononym_resolves_end_to_end_without_an_apollo_row(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
            full_name="Sting", current_title="Musician", confidence="medium")))
        out = T.resolve_identity("Sting")
        assert out["ok"] is True
        assert out["identity"]["full_name"] == "Sting"


class TestHomoglyphImpersonation:
    """A Cyrillic "а" inside an otherwise-Latin name is the classic
    impersonation shape. It must not silently read as the Latin name."""

    def test_a_cyrillic_lookalike_name_does_not_match_its_latin_twin(self):
        latin = "Elon Musk"
        homoglyph = "Elоn Musk"  # Cyrillic small o
        assert latin != homoglyph
        assert T._looks_like_same_person(latin, homoglyph) is False

    def test_a_homoglyph_apollo_row_is_dropped_from_the_candidate_list(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        rows = [{"full_name": "Elоn Musk", "organization_name": "Not Tesla"}]
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: rows)
        candidates, _ = T.search_name_candidates("Elon Musk")
        assert candidates == []

    def test_a_zero_width_character_inside_a_name_does_not_defeat_the_match(self):
        """A zero-width space is invisible, so a row carrying one still
        names the same person a reader sees -- the rule to hold is that it
        changes nothing, not that it makes the row a different human."""
        assert T._looks_like_same_person("Elon Musk", "Elon​Musk") is True

    def test_a_fullwidth_latin_name_folds_onto_its_ascii_spelling(self):
        assert T._looks_like_same_person("Jane Doe", "Ｊａｎｅ Ｄｏｅ") is True


# ───────────────────────── degenerate input ─────────────────────────

class TestDegenerateNameInput:
    @pytest.mark.parametrize("name", ["", "   ", "\t\n", " "])
    def test_a_blank_or_whitespace_name_never_reaches_a_billed_call(self, name, monkeypatch):
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: pytest.fail("no billed call for a blank name"))
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        monkeypatch.setattr(T.apollo_client, "search_people",
                            lambda *a, **kw: pytest.fail("no Apollo call for a blank name"))
        if name.strip():
            pytest.skip("this input is not blank after strip()")
        assert T.resolve_identity(name)["ok"] is False
        assert T.search_name_candidates(name) == ([], None)

    def test_a_none_name_is_treated_as_blank_rather_than_crashing(self, monkeypatch):
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: pytest.fail("no billed call for a missing name"))
        out = T.resolve_identity(None)
        assert out["ok"] is False
        assert out["reasoning"] == "No name was provided."

    def test_a_thousands_of_characters_long_name_still_returns_a_clean_refusal(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
            confidence="none", is_public_figure=False, full_name=None,
            reasoning="No such person exists.")))
        out = T.resolve_identity("A" * 5000)
        assert out["ok"] is False
        assert out["identity"] is None

    def test_an_enormous_name_is_still_matched_without_pathological_slowness(self):
        huge = " ".join(["Smith"] * 2000)
        assert T._looks_like_same_person(huge, "John Smith") is True
        assert T._looks_like_same_person("John Smith", huge) is False

    def test_a_name_that_is_only_punctuation_matches_nothing(self):
        assert T._looks_like_same_person("!!! ???", "Jane Doe") is False

    def test_a_name_that_is_only_emoji_matches_nothing_and_never_crashes(self):
        assert T._looks_like_same_person("🙂🙃", "Jane Doe") is False

    def test_an_emoji_only_name_matches_itself_rather_than_everyone(self):
        assert T._looks_like_same_person("🙂", "🙂 Jane") is False


class TestNoPublicPresence:
    def test_a_confirmed_figure_with_no_social_handles_still_resolves(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
            full_name="Quiet Author", linkedin_url=None, x_handle=None, instagram_handle=None)))
        out = T.resolve_identity("Quiet Author")
        assert out["ok"] is True
        platforms = out["identity"]["platforms"]
        assert platforms["x"] == {"handle": None, "url": None, "resolved": False}
        assert platforms["instagram"] == {"handle": None, "url": None, "resolved": False}
        assert platforms["linkedin"]["resolved"] is False

    def test_no_handles_reads_as_unresolved_platforms_not_a_failed_identity(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
            full_name="Quiet Author")))
        out = T.resolve_identity("Quiet Author")
        assert out["error"] is None
        assert out["confidence"] == "high"

    def test_a_private_individual_is_refused_whatever_the_confidence(self, monkeypatch):
        _stub_platforms(monkeypatch)
        for confidence in ("high", "medium"):
            monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
                confidence=confidence, is_public_figure=False, full_name="Someone Private")))
            assert T.resolve_identity("Someone Private")["ok"] is False

    def test_a_deceased_public_figure_is_resolved_like_any_other(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json(
            full_name="Clayton Christensen", current_title="Professor (1952-2020)",
            reasoning="Widely covered academic; died in 2020.")))
        out = T.resolve_identity("Clayton Christensen")
        assert out["ok"] is True
        assert out["identity"]["current_title"] == "Professor (1952-2020)"

    def test_a_deceased_figures_stale_linkedin_still_soft_fails_rather_than_erroring(
            self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(T.unipile_client, "get_user_profile",
                            lambda slug, account_id: (None, {"kind": "http_status", "status": 404}))
        monkeypatch.setattr(T.unipile_client, "describe_error", lambda err: "Not found.")
        out = T._resolve_linkedin_platform("https://www.linkedin.com/in/gone/", "Clayton Christensen")
        assert out["resolved"] is False
        assert out["note"] == "Not found."


class TestHintedVersusUnhintedResolution:
    def test_a_title_hint_is_carried_into_the_grounding_prompt(self, monkeypatch):
        _stub_platforms(monkeypatch)
        seen = {}
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda system, user, **kw: seen.update(user=user) or _reply(
                                _identity_json()))
        T.resolve_identity("Jane Doe", title_hint="Chief Data Officer")
        assert "Chief Data Officer" in seen["user"]

    def test_no_hints_produces_a_prompt_with_no_invented_context_line(self, monkeypatch):
        _stub_platforms(monkeypatch)
        seen = {}
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda system, user, **kw: seen.update(user=user) or _reply(
                                _identity_json()))
        T.resolve_identity("Jane Doe")
        assert "Known context from the person searching" not in seen["user"]
        assert "A business database suggests" not in seen["user"]

    def test_a_supplied_linkedin_url_is_stated_to_the_model_verbatim(self, monkeypatch):
        _stub_platforms(monkeypatch)
        seen = {}
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda system, user, **kw: seen.update(user=user) or _reply(
                                _identity_json()))
        T.resolve_identity("Jane Doe", linkedin_url="https://www.linkedin.com/in/janedoe/")
        assert "https://www.linkedin.com/in/janedoe/" in seen["user"]

    def test_a_supplied_linkedin_url_wins_over_apollos_own(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        monkeypatch.setattr(T, "_best_apollo_candidate", lambda *a, **kw: {
            "full_name": "Jane Doe", "linkedin_url": "https://www.linkedin.com/in/wrong/"})
        seen = {}
        monkeypatch.setattr(T, "_resolve_linkedin_platform",
                            lambda url, full_name="": seen.update(url=url) or {
                                "url": url, "resolved": False, "provider_id": None,
                                "headline": None, "photo_url": None, "note": None})
        monkeypatch.setattr(T, "_resolve_youtube_platform",
                            lambda name: {"url": None, "resolved": False, "note": None})
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json()))
        T.resolve_identity("Jane Doe", linkedin_url="https://www.linkedin.com/in/right/")
        assert seen["url"] == "https://www.linkedin.com/in/right/"

    def test_a_supplied_x_handle_wins_over_both_apollo_and_the_model(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        monkeypatch.setattr(T, "_best_apollo_candidate", lambda *a, **kw: {
            "full_name": "Jane Doe", "twitter_url": "https://twitter.com/apollo_guess"})
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: _reply(_identity_json(x_handle="model_guess")))
        out = T.resolve_identity("Jane Doe", x_handle="the_real_one")
        assert out["identity"]["platforms"]["x"]["handle"] == "the_real_one"


class TestPlatformUrlParsingUnderAttack:
    @pytest.mark.parametrize("url", [
        "https://www.linkedin.com.attacker.io/in/janedoe",
        "https://notlinkedin.com/in/janedoe",
        "https://www.linkedin.com/in/",
        "https://www.linkedin.com/",
        "javascript:alert(1)",
        "//linkedin.com/in/janedoe/../../company/acme",
    ])
    def test_a_hostile_linkedin_url_never_yields_a_slug(self, url):
        assert T._linkedin_slug(url) is None

    @pytest.mark.parametrize("url", [
        "https://x.com/janedoe/status/1234567890",
        "https://twitter.com/i/user/1234",
        "https://x.com/home",
        "https://x.com/search?q=jane",
        "https://x.com.evil.io/janedoe",
        "https://x.com/",
        # a lookalike domain anybody can register: "notx.com" and
        # "faketwitter.com" both pass a bare endswith check
        "https://notx.com/janedoe",
        "https://faketwitter.com/janedoe",
        "https://my-x.com/janedoe",
    ])
    def test_a_non_profile_x_url_never_yields_a_handle(self, url):
        assert T._handle_from_url(url) is None

    def test_a_bare_profile_url_without_a_scheme_still_parses(self):
        assert T._linkedin_slug("linkedin.com/in/janedoe") == "janedoe"
        assert T._handle_from_url("x.com/janedoe") == "janedoe"

    @pytest.mark.parametrize("url,expected", [
        ("https://twitter.com/janedoe", "janedoe"),
        ("https://mobile.twitter.com/janedoe", "janedoe"),
        ("https://www.x.com/janedoe", "janedoe"),
    ])
    def test_a_real_subdomain_of_the_real_host_still_parses(self, url, expected):
        assert T._handle_from_url(url) == expected

    def test_a_real_linkedin_country_subdomain_still_parses(self):
        assert T._linkedin_slug("https://in.linkedin.com/in/janedoe") == "janedoe"

    def test_an_at_prefixed_handle_in_a_url_is_normalized(self):
        assert T._handle_from_url("https://x.com/@janedoe") == "janedoe"

    def test_an_instagram_handle_is_normalized_the_same_way_as_x(self):
        assert T._resolve_instagram_platform("  @janedoe") == {
            "handle": "janedoe", "url": "https://www.instagram.com/janedoe/", "resolved": True}

    def test_an_all_at_signs_handle_degrades_to_unresolved(self):
        assert T._resolve_x_platform("@@@")["resolved"] is False
        assert T._resolve_instagram_platform("@")["resolved"] is False


class TestApolloBoundaryFailures:
    def test_apollo_returning_a_non_list_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: None)
        candidates, error = T.search_name_candidates("Jane Doe")
        assert candidates == []
        assert error == {"code": "error", "message": "The candidate search could not be completed."}

    def test_apollo_returning_rows_that_are_not_dicts_is_skipped_not_raised(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        monkeypatch.setattr(T.apollo_client, "search_people",
                            lambda *a, **kw: ["not-a-row", 42, None])
        candidates, error = T.search_name_candidates("Jane Doe")
        assert candidates == [] and error is None

    def test_one_junk_row_never_costs_the_good_rows_beside_it(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: [
            "junk", {"full_name": "Jane Doe", "organization_name": "Acme"}, None])
        candidates, error = T.search_name_candidates("Jane Doe")
        assert error is None
        assert [c["full_name"] for c in candidates] == ["Jane Doe"]

    def test_a_non_dict_row_never_reaches_the_grounding_prompt_as_a_candidate(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: ["junk-row"])
        assert T._best_apollo_candidate("Jane Doe", None, None, "k") is None

    def test_a_non_dict_row_under_an_exact_linkedin_lookup_is_also_dropped(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        monkeypatch.setattr(T.apollo_client, "search_people", lambda *a, **kw: ["junk-row"])
        assert T._best_apollo_candidate(
            "Jane Doe", None, "https://linkedin.com/in/janedoe", "k") is None

    def test_an_apollo_timeout_during_resolution_never_blocks_the_grounding_call(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")

        def boom(*a, **kw):
            raise TimeoutError("apollo timed out")

        monkeypatch.setattr(T.apollo_client, "search_people", boom)
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _reply(_identity_json()))
        out = T.resolve_identity("Jane Doe")
        assert out["ok"] is True
        assert out["identity"]["source"]["apollo_matched"] is False

    def test_a_search_limit_error_is_surfaced_as_the_lookup_not_running(self, monkeypatch):
        err = {"kind": claude_websearch.ERR_SEARCH_LIMIT, "detail": "budget spent"}
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: {"text": "", "search_count": 0, "error": err, "usage": {}})
        out = T.resolve_identity("Jane Doe")
        assert out["ok"] is False
        assert out["error"] is err
        assert "could not run" in out["reasoning"]

    def test_a_confidence_value_the_model_invented_degrades_to_none(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: _reply(_identity_json(confidence="extremely high")))
        out = T.resolve_identity("Jane Doe")
        assert out["confidence"] == "none"
        assert out["ok"] is False

    def test_a_reply_with_a_confident_confidence_but_no_name_is_refused(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: _reply(_identity_json(full_name="")))
        assert T.resolve_identity("Jane Doe")["ok"] is False


class TestYouTubeChannelGate:
    def test_a_fan_channel_using_the_persons_name_plus_clips_is_still_accepted_as_a_superset(
            self, monkeypatch):
        """The TLI-local gate is a subset rule: every word of the searched
        name must appear in the channel title. "Sam Altman Clips" passes it,
        which is a known, deliberate limit of the rule rather than a bug --
        pinned here so a future change to the gate is a conscious one."""
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        monkeypatch.setattr(T.sci_youtube_client, "resolve_company_channel",
                            lambda name, key: {"title": "Sam Altman Clips", "channel_id": "UC1",
                                               "profile_url": "https://youtube.com/@clips"})
        assert T._resolve_youtube_platform("Sam Altman")["resolved"] is True

    def test_a_same_surname_strangers_channel_is_rejected(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        monkeypatch.setattr(T.sci_youtube_client, "resolve_company_channel",
                            lambda name, key: {"title": "Bob Altman", "channel_id": "UC2",
                                               "profile_url": "https://youtube.com/@bob"})
        out = T._resolve_youtube_platform("Sam Altman")
        assert out["resolved"] is False
        assert out["note"] == "No YouTube channel confidently matched this name."

    def test_a_channel_with_no_title_at_all_is_rejected(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        monkeypatch.setattr(T.sci_youtube_client, "resolve_company_channel",
                            lambda name, key: {"channel_id": "UC3"})
        assert T._resolve_youtube_platform("Sam Altman")["resolved"] is False

    def test_a_youtube_client_crash_degrades_to_unresolved(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")

        def boom(name, key):
            raise RuntimeError("quota exceeded")

        monkeypatch.setattr(T.sci_youtube_client, "resolve_company_channel", boom)
        assert T._resolve_youtube_platform("Sam Altman")["resolved"] is False


class TestUnipileProfileGate:
    def test_a_profile_whose_first_last_fields_name_someone_else_is_rejected(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(T.unipile_client, "get_user_profile",
                            lambda slug, account_id: ({"first_name": "Bob", "last_name": "Other",
                                                       "provider_id": "urn:li:1"}, None))
        out = T._resolve_linkedin_platform("https://www.linkedin.com/in/wrong/", "Jane Doe")
        assert out["resolved"] is False
        assert out["provider_id"] is None

    def test_a_profile_with_no_name_field_at_all_still_verifies(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(T.unipile_client, "get_user_profile",
                            lambda slug, account_id: ({"provider_id": "urn:li:2"}, None))
        out = T._resolve_linkedin_platform("https://www.linkedin.com/in/janedoe/", "Jane Doe")
        assert out["resolved"] is True

    def test_no_connected_account_reads_as_unverified_not_as_no_linkedin(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform", lambda p: None)
        out = T._resolve_linkedin_platform("https://www.linkedin.com/in/janedoe/", "Jane Doe")
        assert out["resolved"] is False
        assert "No connected LinkedIn account" in out["note"]

    def test_a_company_page_url_never_reaches_unipile_at_all(self, monkeypatch):
        monkeypatch.setattr(T.unipile_transport, "account_for_platform",
                            lambda p: pytest.fail("a company URL must not be looked up as a person"))
        out = T._resolve_linkedin_platform("https://www.linkedin.com/company/acme/", "Jane Doe")
        assert out["resolved"] is False
        assert out["note"] == "No LinkedIn URL to verify."
