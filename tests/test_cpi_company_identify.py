"""Tests for typo-tolerant company resolution and the no-dead-end answer.

The reported failure: "cmo of thoughworks" (one dropped letter) returned the
whole reply "I couldn't find a company called “thoughworks” in Apollo." Two
separate defects produced that:

  1. The typed name went to Apollo's company search exactly as typed, and Apollo
     does not index the misspelling, so nothing resolved.
  2. Nothing resolving ended the question. Our records being silent about a
     company says nothing about who runs it, and the company published the
     answer on its own site.

So these cover: the web identification only ever asserts a company with a real
source URL (same refuse-by-default discipline as the role lookup), it runs only
when Apollo has already failed, Apollo is then re-asked under the real name and
domain, and no question with a company in it can dead-end any more.
"""

import os
import sys
import types

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


_IDENT = ('{"found": true, "name": "Thoughtworks", "domain": "thoughtworks.com", '
          '"source": "https://www.thoughtworks.com/", "note": ""}')

_ROLE = {"name": "Julie Woods-Moss", "title": "Chief Marketing Officer",
         "source": "https://www.thoughtworks.com/x", "exact_title_match": True}


def _web(reply, used=True, record=None):
    """Stand-in for _responses_web_search."""
    def _fn(oai, model, msgs, max_tokens):
        if record is not None:
            record.append({"model": model, "msgs": msgs})
        return reply, used
    return _fn


@pytest.fixture(autouse=True)
def clear_caches():
    """Both resolvers memoize, so a leaked entry would let one test decide
    another's outcome."""
    appmod._CPI_IDENTIFY_CACHE.clear()
    appmod._CPI_NAME_RESOLVE_CACHE.clear()
    yield
    appmod._CPI_IDENTIFY_CACHE.clear()
    appmod._CPI_NAME_RESOLVE_CACHE.clear()


# ── _cpi_company_identify: it names a company only when it really found one ───

def test_a_sourced_company_is_identified(monkeypatch):
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT))
    got = appmod._cpi_company_identify(object(), "thoughworks")
    assert got["name"] == "Thoughtworks"
    assert got["domain"] == "thoughtworks.com"
    assert got["source"].startswith("https://")
    # The typed string travels with the result so the answer can say what it read.
    assert got["typed"] == "thoughworks"


def test_no_openai_client_means_no_lookup(monkeypatch):
    """The filter bar and the search grid work without an OpenAI key. Asking for
    an identification there must degrade, not raise."""
    called = []
    monkeypatch.setattr(appmod, "_responses_web_search",
                        _web(_IDENT, record=called))
    assert appmod._cpi_company_identify(None, "thoughworks") is None
    assert called == [], "no client means no API call at all"


@pytest.mark.parametrize("typed", ["", "   ", None])
def test_nothing_typed_is_nothing_to_identify(monkeypatch, typed):
    called = []
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT, record=called))
    assert appmod._cpi_company_identify(object(), typed) is None
    assert called == []


def test_a_company_with_no_source_is_discarded(monkeypatch):
    """The core guard. A near-miss on a company name means answering confidently
    about the wrong business, so an unsourced claim is refused."""
    monkeypatch.setattr(appmod, "_responses_web_search",
                        _web('{"found": true, "name": "Thoughtworks"}'))
    assert appmod._cpi_company_identify(object(), "thoughworks") is None


@pytest.mark.parametrize("source", [
    "thoughtworks.com",            # bare domain, not a URL
    "javascript:alert(1)",
    "ftp://files.example.com",
    "(the company website)",
    "",
])
def test_a_non_http_source_is_not_a_source(monkeypatch, source):
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": true, "name": "Thoughtworks", "source": "%s"}' % source))
    assert appmod._cpi_company_identify(object(), "thoughworks") is None


def test_a_not_found_reply_returns_nothing(monkeypatch):
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": false, "note": "Could be several different companies."}'))
    assert appmod._cpi_company_identify(object(), "acme") is None


def test_a_sourced_reply_with_no_name_returns_nothing(monkeypatch):
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": true, "name": "", "source": "https://example.com/"}'))
    assert appmod._cpi_company_identify(object(), "acme") is None


def test_no_web_tool_means_no_claim_and_no_second_model(monkeypatch):
    """A model resolving a company name from background knowledge alone is
    guessing, and walking the chain cannot make the tool appear."""
    seen = []
    monkeypatch.setattr(appmod, "_responses_web_search",
                        _web(_IDENT, used=False, record=seen))
    assert appmod._cpi_company_identify(object(), "thoughworks") is None
    assert len(seen) == 1, "no web tool on this key is not a bad model pick"


def test_a_domain_is_normalized_to_a_bare_host(monkeypatch):
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": true, "name": "Thoughtworks", '
        '"domain": "https://WWW.Thoughtworks.com/en-us/about", '
        '"source": "https://www.thoughtworks.com/"}'))
    got = appmod._cpi_company_identify(object(), "thoughworks")
    assert got["domain"] == "thoughtworks.com"


@pytest.mark.parametrize("domain", ["n/a", "unknown", "none", "not found", "-"])
def test_a_domain_shaped_like_prose_is_dropped_but_the_name_kept(monkeypatch, domain):
    """Apollo treats its domain param as a fuzzy relevance hint, so forwarding
    "unknown" as a domain returns an unrelated company rather than nothing."""
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": true, "name": "Thoughtworks", "domain": "%s", '
        '"source": "https://www.thoughtworks.com/"}' % domain))
    got = appmod._cpi_company_identify(object(), "thoughworks")
    assert got["domain"] == ""
    assert got["name"] == "Thoughtworks"


def test_a_success_is_cached_and_not_looked_up_twice(monkeypatch):
    calls = []
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT, record=calls))
    first = appmod._cpi_company_identify(object(), "thoughworks")
    second = appmod._cpi_company_identify(object(), "Thoughworks")   # same key
    assert first == second
    assert len(calls) == 1, "the same misspelling must not pay twice"


def test_a_miss_is_not_cached(monkeypatch):
    """A miss costs nothing to repeat and may succeed once the web catches up,
    unlike a wrong positive which would be pinned for a day."""
    calls = []
    monkeypatch.setattr(appmod, "_responses_web_search",
                        _web('{"found": false}', record=calls))
    appmod._cpi_company_identify(object(), "thoughworks")
    appmod._cpi_company_identify(object(), "thoughworks")
    assert len(calls) == 2


def test_the_question_names_the_typed_string(monkeypatch):
    seen = []
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT, record=seen))
    appmod._cpi_company_identify(object(), "thoughworks")
    ask = seen[0]["msgs"][-1]["content"]
    assert "thoughworks" in ask


def test_identify_fields_are_truncated(monkeypatch):
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": true, "name": "%s", "domain": "thoughtworks.com", '
        '"source": "https://x.com/%s", "note": "%s"}'
        % ("N" * 400, "p" * 900, "T" * 900)))
    got = appmod._cpi_company_identify(object(), "x")
    assert len(got["name"]) <= 160
    assert len(got["source"]) <= 400
    assert len(got["note"]) <= 300


# ── _cpi_resolve_company: one web-assisted second chance ─────────────────────

def _companies(monkeypatch, by_query):
    """Stubs search_companies, answering per normalized q_organization_name /
    domain so a retry under a different name can return something different."""
    import tracker.apollo_client as ac
    seen = []

    def _sc(filters, key, **kw):
        seen.append(dict(filters))
        q = (filters.get("name") or (filters.get("domains") or [""])[0] or "").lower()
        return list(by_query.get(q, []))

    monkeypatch.setattr(ac, "search_companies", _sc)
    return seen


_TW = {"id": "org1", "name": "Thoughtworks, Ltd.", "primary_domain": "thoughtworks.com"}


def test_the_reported_typo_now_resolves_to_the_real_company(monkeypatch):
    """Apollo has nothing under the misspelling; the web says which company that
    is; Apollo is asked again under the real domain and answers."""
    seen = _companies(monkeypatch, {"thoughworks": [], "thoughtworks.com": [_TW]})
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT))
    notes = {}
    org, choices = appmod._cpi_resolve_company("thoughworks", "key", oai=object(),
                                               notes=notes)
    assert org["name"] == "Thoughtworks, Ltd."
    assert choices is None
    assert notes["identified"]["name"] == "Thoughtworks"
    # Typed name first, then the identified domain. Never the other way around:
    # the web call is only worth making once Apollo has actually failed.
    assert seen[0].get("name") == "thoughworks"
    assert seen[1].get("domains") == ["thoughtworks.com"]


def test_a_company_the_web_knows_but_apollo_does_not_still_reports_the_name(monkeypatch):
    """Apollo has no record even under the real name. The identification is still
    handed back, because the caller needs it to research the right company."""
    _companies(monkeypatch, {})
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT))
    notes = {}
    org, choices = appmod._cpi_resolve_company("thoughworks", "key", oai=object(),
                                               notes=notes)
    assert (org, choices) == (None, None)
    assert notes["identified"]["name"] == "Thoughtworks"
    assert notes["identified"]["domain"] == "thoughtworks.com"


def test_no_web_call_when_apollo_already_resolved_the_name(monkeypatch):
    """Cost and latency guard: the happy path must not pay for a web lookup."""
    _companies(monkeypatch, {"thoughtworks": [_TW]})
    calls = []
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT, record=calls))
    org, _choices = appmod._cpi_resolve_company("Thoughtworks", "key", oai=object())
    assert org["id"] == "org1"
    assert calls == []


def test_no_web_call_when_the_name_was_merely_ambiguous(monkeypatch):
    """Several real matches is a question for the user, not a spelling problem."""
    _companies(monkeypatch, {"acme": [
        {"id": "a1", "name": "Acme Health", "primary_domain": "acmehealth.com"},
        {"id": "a2", "name": "Acme Robotics", "primary_domain": "acmerobots.com"}]})
    calls = []
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT, record=calls))
    org, choices = appmod._cpi_resolve_company("Acme", "key", oai=object())
    assert org is None and len(choices) == 2
    assert calls == []


def test_without_an_openai_client_resolution_behaves_exactly_as_before(monkeypatch):
    seen = _companies(monkeypatch, {})
    calls = []
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT, record=calls))
    assert appmod._cpi_resolve_company("thoughworks", "key") == (None, None)
    assert calls == []
    assert len(seen) == 1, "one Apollo search, no retry"


def test_an_identification_that_changes_nothing_costs_no_second_search(monkeypatch):
    """The web agreeing with the spelling Apollo already missed, with no domain
    to add, means there is nothing new to search for."""
    seen = _companies(monkeypatch, {})
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": true, "name": "Thoughworks", "source": "https://example.com/"}'))
    org, choices = appmod._cpi_resolve_company("thoughworks", "key", oai=object())
    assert (org, choices) == (None, None)
    assert len(seen) == 1


def test_the_retry_keeps_the_exact_domain_guard(monkeypatch):
    """Apollo's domain filter is a fuzzy relevance hint, not an equality filter.
    An identified domain that comes back with a NEIGHBOURING company must not be
    accepted as the answer just because the retry was the last resort."""
    neighbour = {"id": "other", "name": "Thought Machine",
                 "primary_domain": "thoughtmachine.net"}
    _companies(monkeypatch, {"thoughworks": [], "thoughtworks.com": [neighbour],
                             "thoughtworks": []})
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT))
    org, choices = appmod._cpi_resolve_company("thoughworks", "key", oai=object())
    assert (org, choices) == (None, None), "a wrong domain must fail, not mislead"


def test_the_retry_falls_back_from_domain_to_name(monkeypatch):
    """A real company whose domain Apollo does not index is still findable under
    the identified name."""
    _companies(monkeypatch, {"thoughworks": [], "thoughtworks.com": [],
                             "thoughtworks": [_TW]})
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT))
    org, _choices = appmod._cpi_resolve_company("thoughworks", "key", oai=object())
    assert org["id"] == "org1"


def test_a_failing_identification_does_not_break_resolution(monkeypatch):
    _companies(monkeypatch, {})

    def _boom(*a, **k):
        raise RuntimeError("web search exploded")

    monkeypatch.setattr(appmod, "_responses_web_search", _boom)
    assert appmod._cpi_resolve_company("thoughworks", "key", oai=object()) == (None, None)


# ── _cpi_web_answer: the shared "our records cannot settle this" path ────────

def test_the_web_answer_attaches_a_sourced_role_holder(monkeypatch):
    seen = {}
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: _ROLE)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", True))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="": seen.setdefault("f", facts) and "a")
    answer, researched, web = appmod._cpi_web_answer(
        None, {"company_not_in_our_records": True}, "who is the CMO of Thoughtworks",
        ["CMO"], "Thoughtworks", "thoughtworks.com")
    assert answer == "a"
    assert researched is True and web is True
    assert seen["f"]["public_role_holder"]["name"] == "Julie Woods-Moss"


def test_the_web_answer_does_not_mutate_the_caller_facts(monkeypatch):
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: _ROLE)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", True))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer", lambda *a, **k: "a")
    facts = {"company_not_in_our_records": True}
    appmod._cpi_web_answer(None, facts, "q", ["CMO"], "Thoughtworks")
    assert "public_role_holder" not in facts


def test_a_failing_role_lookup_still_produces_an_answer(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("lookup exploded")

    monkeypatch.setattr(appmod, "_cpi_role_lookup", _boom)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", True))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer", lambda *a, **k: "a")
    answer, _r, _w = appmod._cpi_web_answer(None, {}, "q", ["CMO"], "Thoughtworks")
    assert answer == "a"


def test_no_title_means_no_role_lookup(monkeypatch):
    """"Tell me about Thoughtworks" has no role to look up."""
    calls = []
    monkeypatch.setattr(appmod, "_cpi_role_lookup",
                        lambda *a, **k: calls.append(1) or _ROLE)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", True))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer", lambda *a, **k: "a")
    appmod._cpi_web_answer(None, {}, "tell me about them", [], "Thoughtworks")
    assert calls == []


# ── Wiring: the chat panel, end to end ──────────────────────────────────────

def _chat(monkeypatch, message, intent_extra=None, companies=None, people=None,
          identify=_IDENT, role=_ROLE):
    """Drives the real /chat route with Apollo and OpenAI stubbed out."""
    import json as _json
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    intent = {"intent": "person_at_company", "titles": ["CMO"],
              "company_name": "thoughworks", "seniorities": [], "max_results": 10}
    intent.update(intent_extra or {})
    monkeypatch.setattr(appmod, "_vimi_chat_json",
                        lambda oai, msgs, mt: (_json.dumps(intent), "m"))
    _companies(monkeypatch, companies or {})
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: list(people or []))
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_enrich_person", lambda *a, **k: {"matched": False})
    monkeypatch.setattr(appmod, "_responses_web_search", _web(identify))
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: role)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", True))
    seen = {}

    def _answer(oai, facts, question, research=""):
        seen["facts"] = facts
        return "answer"

    monkeypatch.setattr(appmod, "_cpi_grounded_answer", _answer)
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/gtm/company-people-intelligence/chat", json={"message": message})
    assert r.status_code == 200
    return seen.get("facts", {}), r.get_json()


def test_chat_the_exact_reported_scenario_no_longer_dead_ends(monkeypatch):
    """The screenshot: "cmo of thoughworks" answered only with "I couldn't find a
    company called thoughworks in Apollo." Now the web identifies the company,
    Apollo resolves it, and the CMO question gets a real answer."""
    cmo = [{"id": "p1", "full_name": "Julie Woods-Moss",
            "title": "Chief Marketing Officer"}]
    facts, body = _chat(monkeypatch, "cmo of thoughworks",
                        companies={"thoughworks": [], "thoughtworks.com": [_TW]},
                        people=cmo)
    assert "couldn't find a company" not in body["answer"]
    assert facts["person"]["full_name"] == "Julie Woods-Moss"


def test_chat_answers_from_the_web_when_the_company_is_not_in_our_records(monkeypatch):
    """Apollo has no record of the company under any spelling. That is a fact
    about our records, not the end of the question."""
    facts, body = _chat(monkeypatch, "cmo of thoughworks", companies={})
    assert facts["company_not_in_our_records"] is True
    assert facts["company"] == "Thoughtworks"
    assert facts["requested_titles"] == ["CMO"]
    assert facts["public_role_holder"]["name"] == "Julie Woods-Moss"
    assert body["answer"] == "answer"
    assert body.get("web_search") is True


def test_chat_never_replies_with_the_old_dead_end_string(monkeypatch):
    """Even with nothing identifiable and nothing in Apollo, the reply is an
    answer to the question asked, not a report about our database."""
    _facts, body = _chat(monkeypatch, "cmo of zzqqxx", companies={},
                         identify='{"found": false}', role=None)
    assert "in Apollo" not in body["answer"]
    assert body["answer"] == "answer"


def test_chat_reports_a_corrected_company_name(monkeypatch):
    """A silent correction is how a confident answer about the wrong company gets
    believed, so the reader is told which name was read."""
    facts, _body = _chat(monkeypatch, "cmo of thoughworks",
                         intent_extra={"company_name": "Thoughtworks",
                                       "company_name_typed": "thoughworks"},
                         companies={})
    assert facts["interpreted_company_name_as"] == {"typed": "thoughworks",
                                                    "understood_as": "Thoughtworks"}


def test_chat_reports_a_name_the_web_corrected_too(monkeypatch):
    """Either step can be the one that fixed the spelling. Here the parser passed
    the typo through and the web identification is what corrected it, so the note
    still has to appear."""
    facts, _body = _chat(monkeypatch, "cmo of thoughworks", companies={})
    assert facts["interpreted_company_name_as"] == {"typed": "thoughworks",
                                                   "understood_as": "Thoughtworks"}


def test_chat_does_not_claim_a_correction_that_did_not_happen(monkeypatch):
    facts, _body = _chat(monkeypatch, "cmo of Thoughtworks",
                         intent_extra={"company_name": "Thoughtworks"},
                         companies={})
    assert "interpreted_company_name_as" not in facts


def test_chat_still_answers_a_company_question_with_no_title(monkeypatch):
    """"tell me about thoughworks": nothing in our records, no role to look up,
    and still a real answer."""
    facts, body = _chat(monkeypatch, "tell me about thoughworks",
                        intent_extra={"intent": "company_info", "titles": []},
                        companies={}, role=None)
    assert facts["company_not_in_our_records"] is True
    assert "requested_titles" not in facts
    assert body["answer"] == "answer"


# ── Wiring: the filter bar ──────────────────────────────────────────────────

def test_the_filter_bar_resolves_a_misspelled_company_too(monkeypatch):
    """Same defect, second surface: the People filter's "at company" field ran
    the typed name straight at Apollo and reported no such company."""
    seen = _companies(monkeypatch, {"thoughworks": [], "thoughtworks": [_TW]})
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT))
    org_id, org_name, choices, found = appmod._cpi_resolve_company_name(
        "thoughworks", "key", oai=object())
    assert (found, org_id, choices) == (True, "org1", None)
    assert org_name == "Thoughtworks, Ltd."
    assert [f.get("name") for f in seen] == ["thoughworks", "Thoughtworks"]


def test_the_filter_bar_still_reports_a_company_that_does_not_exist(monkeypatch):
    _companies(monkeypatch, {})
    monkeypatch.setattr(appmod, "_responses_web_search", _web('{"found": false}'))
    assert appmod._cpi_resolve_company_name("zzqqxx", "key", oai=object()) == (
        None, None, None, False)


def test_the_filter_bar_caches_under_the_typed_spelling(monkeypatch):
    """Retyping the same misspelling must skip both the web call and the extra
    company search, not just the first one."""
    seen = _companies(monkeypatch, {"thoughworks": [], "thoughtworks": [_TW]})
    web_calls = []
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_IDENT, record=web_calls))
    appmod._cpi_resolve_company_name("thoughworks", "key", oai=object())
    appmod._cpi_resolve_company_name("thoughworks", "key", oai=object())
    assert len(web_calls) == 1
    assert len(seen) == 2, "the second call is served from the resolve cache"


def test_the_filter_bar_works_with_no_openai_key(monkeypatch):
    seen = _companies(monkeypatch, {})
    assert appmod._cpi_resolve_company_name("thoughworks", "key") == (
        None, None, None, False)
    assert len(seen) == 1


# ── The answer prompt has to know what these facts mean ─────────────────────

def test_the_answer_prompt_covers_the_new_facts():
    p = appmod._CPI_ANSWER_SYSTEM
    assert "company_not_in_our_records" in p
    assert "interpreted_company_name_as" in p
    # The records gap must not be allowed to become the whole answer, which is
    # exactly what the reported reply did.
    assert "never a reason to decline" in p


def test_the_intent_prompt_asks_for_a_corrected_company_name():
    p = appmod._CPI_INTENT_SYSTEM
    assert "company_name_typed" in p
    assert "thoughworks" in p, "the reported failure is the worked example"


def test_the_identify_prompt_requires_a_real_source():
    p = appmod._CPI_COMPANY_IDENTIFY_SYSTEM
    assert "http(s) URL" in p
    assert "Guessing is a failure" in p
