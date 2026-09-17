"""Phase 0 (identity resolution) for Thought Leader Intelligence.

Mirrors tests/test_event_intel_phase2.py's mocking style: claude_websearch.ask
is monkeypatched at the module-attribute level rather than hitting a real
API, and the store functions are proven fail-soft the same way every other
tracker/*_store.py module is (no DATABASE_URL -> None/[]/False, never a
raised exception).
"""

from __future__ import annotations

import os

import pytest

from tracker import claude_websearch, thought_leader_pr as T


def _fake_search_result(text: str, search_count: int = 3) -> dict:
    return {"text": text, "search_count": search_count, "error": None,
            "usage": {}, "stop_reason": "end_turn"}


@pytest.fixture(autouse=True)
def _no_side_effect_keys(monkeypatch):
    """No test in this file should ever reach a real vendor: clear every key
    resolve_identity() checks so a missing monkeypatch fails loudly (a
    network error) rather than silently hitting a real API."""
    for key in ("APOLLO_API_KEY", "YOUTUBE_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)


def _stub_platforms(monkeypatch):
    """Neutral stand-ins for the three best-effort platform lookups so a test
    about the websearch/confidence logic isn't also exercising Unipile/
    YouTube's own network calls."""
    monkeypatch.setattr(T, "_resolve_linkedin_platform",
                        lambda url: {"url": url, "resolved": False, "provider_id": None,
                                     "headline": None, "photo_url": None, "note": "stub"})
    monkeypatch.setattr(T, "_resolve_youtube_platform",
                        lambda name: {"url": None, "title": None, "resolved": False, "note": "stub"})


class TestResolveIdentityGates:
    def test_blank_name_is_refused_without_any_lookup(self, monkeypatch):
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: pytest.fail("must not call websearch for a blank name"))
        out = T.resolve_identity("   ")
        assert out == {"ok": False, "confidence": "none", "reasoning": "No name was provided.",
                       "identity": None, "spend": {}, "error": None}

    def test_a_reply_with_no_search_is_refused_as_recalled_not_verified(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: _fake_search_result('{"confidence":"high"}', search_count=0))
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["confidence"] == "none"
        assert "recalled rather than verified" in out["reasoning"]

    def test_low_confidence_is_refused_rather_than_softened(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _fake_search_result(
            '{"confidence":"low","reasoning":"Several people share this name.",'
            '"is_public_figure":true,"full_name":"Some Person"}'))
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["confidence"] == "low"
        assert out["identity"] is None

    def test_private_individual_is_refused_even_at_high_confidence(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _fake_search_result(
            '{"confidence":"high","reasoning":"No public platform found.",'
            '"is_public_figure":false,"full_name":"Some Person"}'))
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["identity"] is None

    def test_unparsable_reply_is_refused_with_an_unparsable_error(self, monkeypatch):
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: _fake_search_result("not json at all"))
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["error"]["kind"] == claude_websearch.ERR_UNPARSABLE

    def test_a_transport_error_is_surfaced_with_a_reader_facing_reason(self, monkeypatch):
        err = {"kind": claude_websearch.ERR_TRANSPORT, "detail": "boom"}
        monkeypatch.setattr(claude_websearch, "ask",
                            lambda *a, **kw: {"text": "", "search_count": 0, "error": err, "usage": {}})
        out = T.resolve_identity("Some Person")
        assert out["ok"] is False
        assert out["error"] is err


class TestResolveIdentityHappyPath:
    def test_high_confidence_public_figure_assembles_a_full_identity(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test-key")
        monkeypatch.setattr(T, "_best_apollo_candidate", lambda *a, **kw: {
            "full_name": "Jane Doe", "title": "Chief Strategy Officer",
            "organization_name": "Acme Corp", "linkedin_url": "https://www.linkedin.com/in/janedoe/",
            "twitter_url": "https://twitter.com/janedoe", "photo_url": "https://img.example/jane.jpg",
        })
        monkeypatch.setattr(T, "_resolve_linkedin_platform", lambda url: {
            "url": url, "resolved": True, "provider_id": "urn:li:member:123",
            "headline": "CSO at Acme -- scaling go-to-market", "photo_url": "https://img.example/jane-li.jpg",
            "note": None})
        monkeypatch.setattr(T, "_resolve_youtube_platform", lambda name: {
            "url": "https://www.youtube.com/@janedoe", "title": "Jane Doe", "resolved": True, "note": None})
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _fake_search_result(
            '{"confidence":"high","reasoning":"Multiple sources confirm this is Jane Doe -- CSO at Acme.",'
            '"is_public_figure":true,"full_name":"Jane Doe","headline":"CSO at Acme",'
            '"current_title":"Chief Strategy Officer","current_company":"Acme Corp",'
            '"linkedin_url":"https://www.linkedin.com/in/janedoe/","x_handle":"janedoe",'
            '"disambiguating_facts":["Author of Scaling Go-to-Market","Keynoted SaaStr 2026",'
            '"Extra fact one","Extra fact two","Extra fact three -- should be dropped, only 4 kept"]}'))

        out = T.resolve_identity("Jane Doe", company_hint="Acme")

        assert out["ok"] is True
        assert out["confidence"] == "high"
        identity = out["identity"]
        assert identity["full_name"] == "Jane Doe"
        assert identity["current_company"] == "Acme Corp"
        # em-dash stripped from every free-text field the model wrote, same
        # discipline event_intel_resolve applies -- a dash here has shipped
        # to a live report before.
        assert "—" not in identity["reasoning"]
        assert "—" not in identity["headline"]
        assert len(identity["disambiguating_facts"]) == 4
        assert identity["platforms"]["linkedin"]["resolved"] is True
        assert identity["platforms"]["x"] == {"handle": "janedoe", "url": "https://x.com/janedoe",
                                              "resolved": True}
        assert identity["platforms"]["youtube"]["resolved"] is True
        assert identity["source"]["apollo_matched"] is True

    def test_apollo_miss_still_resolves_from_websearch_alone(self, monkeypatch):
        monkeypatch.setattr(T, "_best_apollo_candidate", lambda *a, **kw: None)
        _stub_platforms(monkeypatch)
        monkeypatch.setattr(claude_websearch, "ask", lambda *a, **kw: _fake_search_result(
            '{"confidence":"medium","reasoning":"Identified via press coverage.",'
            '"is_public_figure":true,"full_name":"Jane Doe","x_handle":"@janedoe"}'))
        out = T.resolve_identity("Jane Doe")
        assert out["ok"] is True
        assert out["identity"]["source"]["apollo_matched"] is False
        # a leading @ on the model's own x_handle is stripped, same as the
        # user-supplied path
        assert out["identity"]["platforms"]["x"]["handle"] == "janedoe"


class TestLinkedInSlug:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.linkedin.com/in/satyanadella/", "satyanadella"),
        ("https://www.linkedin.com/in/satyanadella", "satyanadella"),
        ("http://linkedin.com/in/satya-nadella-123/", "satya-nadella-123"),
        ("", None),
        (None, None),
    ])
    def test_extracts_the_public_identifier(self, url, expected):
        assert T._linkedin_slug(url) == expected


class TestStoreFailSoft:
    """No DATABASE_URL configured (cleared by the autouse fixture above) --
    every store function must degrade quietly, never raise."""

    def test_create_run_returns_none(self):
        assert T.create_run(email="a@b.com", input_name="Jane Doe") is None

    def test_save_result_returns_false(self):
        assert T.save_result(1, "a@b.com", {"ok": True, "identity": {}}) is False

    def test_confirm_run_returns_false(self):
        assert T.confirm_run(1, "a@b.com") is False

    def test_get_run_returns_none(self):
        assert T.get_run(1, "a@b.com") is None

    def test_list_runs_returns_empty_list(self):
        assert T.list_runs("a@b.com") == []
