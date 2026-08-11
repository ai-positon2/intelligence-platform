"""Two reports against "CMOs of macmerise".

1. No Enrich button. That question parses as a people_list, and the list branch
   emitted no enrich metadata at all -- so the only way to act on a name the
   answer had just produced was to retype it as a fresh question. Every person
   an answer names who can be enriched now gets a button.

2. "?utm_source=openai" in the citation URL. OpenAI's web-search tool tags the
   sources it cites. Left in, it makes the link ugly, tags staff clicks as
   OpenAI-referred traffic in the destination's own analytics, and is not what
   anyone would copy if quoting the source by hand.
"""

import json as _json
import os
import sys
import types

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


# ── _cpi_clean_url ──────────────────────────────────────────────────────────

def test_the_reported_parameter_is_removed():
    assert appmod._cpi_clean_url(
        "https://in.linkedin.com/company/macmerise?utm_source=openai"
    ) == "https://in.linkedin.com/company/macmerise"


def test_a_whole_family_of_trackers_goes():
    got = appmod._cpi_clean_url(
        "https://x.com/a?utm_source=openai&utm_medium=ai&gclid=1&fbclid=2&msclkid=3")
    assert got == "https://x.com/a"


def test_a_meaningful_parameter_is_kept():
    """The point is to strip tracking, not to rewrite URLs: dropping a real
    query parameter would serve different content than the source cited."""
    assert appmod._cpi_clean_url(
        "https://example.com/search?q=macmerise&utm_source=openai"
    ) == "https://example.com/search?q=macmerise"


def test_a_mixed_query_keeps_its_order_and_loses_only_the_tracker():
    assert appmod._cpi_clean_url(
        "https://e.com/p?a=1&utm_campaign=x&b=2") == "https://e.com/p?a=1&b=2"


def test_a_url_with_nothing_to_strip_is_returned_byte_identical():
    """No rebuild when there is nothing to remove, so encoding quirks in a
    perfectly good URL are never "normalized" into a different string."""
    u = "https://example.com/a%20b?x=1%2F2"
    assert appmod._cpi_clean_url(u) == u


def test_a_fragment_survives():
    assert appmod._cpi_clean_url(
        "https://e.com/p?utm_source=openai#team") == "https://e.com/p#team"


def test_a_path_only_url_is_untouched():
    assert appmod._cpi_clean_url("https://e.com/leadership/") == "https://e.com/leadership/"


@pytest.mark.parametrize("junk", ["", "   ", "not a url", "mailto:a@b.com",
                                  "ftp://e.com/x?utm_source=openai", "javascript:alert(1)"])
def test_a_non_http_string_is_never_mangled(junk):
    """This runs over model output, so anything that merely looks URL-ish has to
    come back exactly as it went in."""
    assert appmod._cpi_clean_url(junk) == junk.strip()


def test_a_query_that_was_entirely_tracking_loses_the_question_mark():
    assert appmod._cpi_clean_url("https://e.com/p?utm_source=openai") == "https://e.com/p"


def test_case_variants_of_a_tracking_key_are_caught():
    assert appmod._cpi_clean_url("https://e.com/p?UTM_Source=openai") == "https://e.com/p"


# ── _cpi_strip_tracking over prose ──────────────────────────────────────────

def test_a_citation_inside_a_sentence_is_cleaned_and_keeps_its_period():
    out = appmod._cpi_strip_tracking(
        "Per the page: https://in.linkedin.com/company/macmerise?utm_source=openai.")
    assert out == "Per the page: https://in.linkedin.com/company/macmerise."


def test_a_citation_in_parentheses_does_not_swallow_the_bracket():
    out = appmod._cpi_strip_tracking("See (https://e.com/p?utm_source=openai) for more.")
    assert out == "See (https://e.com/p) for more."


def test_several_urls_in_one_answer_are_all_cleaned():
    out = appmod._cpi_strip_tracking(
        "One https://a.com/x?utm_source=openai and two https://b.com/y?utm_medium=ai here")
    assert "utm_" not in out
    assert "https://a.com/x" in out and "https://b.com/y" in out


def test_prose_with_no_urls_is_unchanged():
    s = "Binal Shah is the CMO. Nothing to clean here."
    assert appmod._cpi_strip_tracking(s) == s


def test_a_trailing_comma_is_not_eaten():
    out = appmod._cpi_strip_tracking("at https://e.com/p?utm_source=openai, which names her")
    assert out == "at https://e.com/p, which names her"


# ── The answer choke point ──────────────────────────────────────────────────

def test_every_answer_is_swept_on_the_way_out(monkeypatch):
    """Whatever the model returns, and whichever upstream step introduced the
    tag, the reader never sees it."""
    monkeypatch.setattr(appmod, "_vimi_completion", lambda oai, msgs, mt: (
        "Publicly listed here: https://in.linkedin.com/company/macmerise?utm_source=openai.", "m"))
    out = appmod._cpi_grounded_answer(object(), {"x": 1}, "who is the CMO")
    assert "utm_source" not in out
    assert "https://in.linkedin.com/company/macmerise." in out


def test_the_role_lookup_source_is_cleaned_before_it_becomes_a_fact(monkeypatch):
    """Cleaned at the source too, not only on the way out: a model handed a
    tagged URL reproduces the tag in prose the outbound sweep then has to
    catch. Belt and braces, cheaply."""
    monkeypatch.setattr(appmod, "_vimi_model_chain", lambda: ["m1"])
    monkeypatch.setattr(appmod, "_responses_web_search",
                        lambda oai, model, msgs, mt: (_json.dumps({
                            "found": True, "name": "Sahil Shah", "title": "CEO",
                            "source": "https://in.linkedin.com/company/macmerise?utm_source=openai",
                        }), True))
    got = appmod._cpi_role_lookup(object(), ["CEO"], "Macmerise")
    assert got["source"] == "https://in.linkedin.com/company/macmerise"


def test_the_research_text_is_cleaned_before_it_reaches_the_prompt(monkeypatch):
    monkeypatch.setattr(appmod, "_vimi_model_chain", lambda: ["m1"])
    monkeypatch.setattr(appmod, "_responses_web_search",
                        lambda oai, model, msgs, mt: (
                            "See https://e.com/p?utm_source=openai for detail", True))
    txt, used = appmod._cpi_research(object(), "tell me about Macmerise")
    assert "utm_source" not in txt
    assert used is True


# ── _cpi_enrich_chip ────────────────────────────────────────────────────────

def test_a_chip_carries_what_the_enrich_route_needs():
    chip = appmod._cpi_enrich_chip(
        {"id": "p1", "full_name": "Binal Shah", "title": "CMO",
         "organization_domain": "macmerise.com"})
    assert chip == {"type": "person", "name": "Binal Shah", "title": "CMO",
                    "domain": "macmerise.com", "apollo_id": "p1"}


def test_a_person_with_no_apollo_id_gets_no_chip():
    """The id is what makes the enrichment exact, so there is nothing to offer
    without one."""
    assert appmod._cpi_enrich_chip({"full_name": "Binal Shah"}) is None


def test_the_company_domain_falls_back_when_the_row_has_none():
    chip = appmod._cpi_enrich_chip({"id": "p1", "full_name": "Binal Shah"},
                                   fallback_domain="macmerise.com")
    assert chip["domain"] == "macmerise.com"


def test_a_masked_name_still_gets_a_chip():
    """"Binal S." is exactly the person worth enriching: the button is how the
    withheld surname gets bought."""
    chip = appmod._cpi_enrich_chip({"id": "p1", "full_name": "Binal S.",
                                    "name_masked": True})
    assert chip["apollo_id"] == "p1"


# ── End to end: "CMOs of macmerise" ─────────────────────────────────────────

def _ask_list(monkeypatch, people, message="CMOs of macmerise",
             intent="people_list", role=None):
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": intent, "titles": ["CMO"], "company_name": "Macmerise",
        "seniorities": [], "max_results": 10}), "m"))
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: {
        "id": "org-mac", "name": "Macmerise", "primary_domain": "macmerise.com"})
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: list(people))
    monkeypatch.setattr(ac, "search_companies", lambda *a, **k: [])
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: role)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    facts_box = {}
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="":
                        facts_box.setdefault("f", facts) and "answer")
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/chat", json={"message": message})
    assert r.status_code == 200
    return r.get_json(), facts_box.get("f", {})


_BINAL = {"id": "p-binal", "full_name": "Binal Shah", "title": "CMO",
          "organization_domain": "macmerise.com"}


def test_the_reported_list_answer_now_offers_a_button(monkeypatch):
    body, facts = _ask_list(monkeypatch, [_BINAL])
    assert facts["people"][0]["full_name"] == "Binal Shah"
    assert body["enrich"] == [{"type": "person", "name": "Binal Shah", "title": "CMO",
                               "domain": "macmerise.com", "apollo_id": "p-binal"}]


def test_every_person_in_a_list_gets_their_own_button(monkeypatch):
    people = [dict(_BINAL, id="p%d" % i, full_name="Person %d" % i) for i in range(4)]
    body, _facts = _ask_list(monkeypatch, people)
    assert [c["apollo_id"] for c in body["enrich"]] == ["p0", "p1", "p2", "p3"]


def test_the_buttons_are_capped_so_a_long_list_is_not_a_wall(monkeypatch):
    people = [dict(_BINAL, id="p%d" % i, full_name="Person %d" % i) for i in range(10)]
    body, _facts = _ask_list(monkeypatch, people)
    assert len(body["enrich"]) == appmod._CPI_CHAT_ENRICH_CHIP_CAP


def test_a_person_with_no_id_is_skipped_rather_than_offered(monkeypatch):
    body, _facts = _ask_list(monkeypatch, [{"full_name": "No Id", "title": "CMO"}, _BINAL])
    assert [c["apollo_id"] for c in body["enrich"]] == ["p-binal"]


def test_the_buttons_never_reach_the_model(monkeypatch):
    """UI wiring, not a fact. In the facts blob it would become prose."""
    _body, facts = _ask_list(monkeypatch, [_BINAL])
    assert "apollo_id" not in _json.dumps(facts, default=str)


def test_no_people_means_no_buttons(monkeypatch):
    body, _facts = _ask_list(monkeypatch, [])
    assert "enrich" not in body or not body["enrich"]


def test_the_named_role_holder_leads_and_is_not_duplicated(monkeypatch):
    """The consolation path offers the public name first, then the on-file
    people, and must not list the same person twice when they are both."""
    role = {"name": "Binal Shah", "title": "CMO",
            "source": "https://in.linkedin.com/company/macmerise",
            "exact_title_match": True}
    # A title question whose title nobody matches sends this down the
    # consolation path, where the role holder is found on file as _BINAL.
    body, facts = _ask_list(monkeypatch, [dict(_BINAL, title="Chief Creative Officer")],
                            message="ceo of macmerise", intent="person_at_company",
                            role=role)
    assert facts["no_one_holds_the_requested_title"] is True
    ids = [c["apollo_id"] for c in body["enrich"]]
    assert ids.count("p-binal") == 1, "the same person must not get two buttons"
    assert ids[0] == "p-binal", "the publicly named person leads"
