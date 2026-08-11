"""Tests for the public role lookup that answers "who is the CMO of X".

The reported failure: asking for the CMO of Thoughtworks returned "our records
have nobody matching a CMO" plus a generic company overview, while Thoughtworks
publishes its CMO on its own leadership page. Apollo was right about its own
records; the research half answered a different question than the one asked.

So these cover two things:
  1. The lookup only ever asserts a named person when a live web search actually
     produced one WITH a checkable source URL. A confident model with no source
     is the hallucination this feature would otherwise introduce, so most of
     these tests are about refusing to answer.
  2. Both surfaces that can hit a records gap (the chat panel and the filter
     bar's empty-search note) actually attach the result, at most once, and
     never on a question Apollo already answered.
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


# The real shape of a good reply, taken from the actual reported question.
_GOOD = ('{"found": true, "name": "Julie Woods-Moss", "title": "Chief Marketing Officer", '
         '"source": "https://www.thoughtworks.com/en-us/profiles/leaders/julie-woods-moss", '
         '"as_of": "2026", "note": "Joined in 2019 as first CMO."}')


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com",
                               "name": "Test User", "given_name": "Test"}
    return c


@pytest.fixture
def no_postgres(monkeypatch):
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)


def _web(reply, used=True, record=None):
    """Stand-in for _responses_web_search."""
    def _fn(oai, model, msgs, max_tokens):
        if record is not None:
            record.append({"model": model, "msgs": msgs})
        return reply, used
    return _fn


# ── _cpi_extract_json ────────────────────────────────────────────────────────
# The lookup runs through the Responses API with a web-search tool, which returns
# prose, not guaranteed JSON mode. All of these are shapes that endpoint really
# produces, and json.loads() on the raw string throws for every one but the first.

def test_extract_plain_json():
    assert appmod._cpi_extract_json('{"found": true, "name": "A"}')["name"] == "A"


def test_extract_json_from_a_fenced_block():
    raw = '```json\n{"found": true, "name": "A"}\n```'
    assert appmod._cpi_extract_json(raw)["name"] == "A"


def test_extract_json_from_an_unlabelled_fence():
    raw = '```\n{"found": false}\n```'
    assert appmod._cpi_extract_json(raw) == {"found": False}


def test_extract_json_after_a_prose_preamble():
    raw = 'I searched and found the answer.\n{"found": true, "name": "A"}'
    assert appmod._cpi_extract_json(raw)["name"] == "A"


def test_extract_json_before_a_trailing_citation_block():
    """Web-search replies routinely append their own source list."""
    raw = '{"found": true, "name": "A"}\n\nSources:\n- https://example.com/team'
    assert appmod._cpi_extract_json(raw)["name"] == "A"


@pytest.mark.parametrize("raw", ["", None, "no json here at all", "{ not json ]"])
def test_extract_json_returns_none_rather_than_raising(raw):
    assert appmod._cpi_extract_json(raw) is None


# ── _cpi_role_lookup: it answers only when it really found someone ───────────

def test_a_sourced_role_holder_is_returned(monkeypatch):
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_GOOD))
    got = appmod._cpi_role_lookup(None, ["CMO"], "Thoughtworks", "thoughtworks.com")
    assert got["name"] == "Julie Woods-Moss"
    assert got["title"] == "Chief Marketing Officer"
    assert got["source"].startswith("https://www.thoughtworks.com/")
    # "CMO" and "Chief Marketing Officer" are the same role, resolved in code.
    assert got["exact_title_match"] is True


def test_a_not_found_reply_returns_nothing(monkeypatch):
    monkeypatch.setattr(appmod, "_responses_web_search",
                        _web('{"found": false, "note": "Role appears vacant."}'))
    assert appmod._cpi_role_lookup(None, ["CMO"], "Acme") is None


def test_a_named_person_with_no_source_is_discarded(monkeypatch):
    """The core guard. A model that names someone but cites nothing is the exact
    hallucination this feature would otherwise launder into a confident answer."""
    monkeypatch.setattr(appmod, "_responses_web_search",
                        _web('{"found": true, "name": "Someone Plausible", "title": "CMO"}'))
    assert appmod._cpi_role_lookup(None, ["CMO"], "Acme") is None


@pytest.mark.parametrize("source", [
    "linkedin.com/in/someone",        # no scheme, so not a link anyone can open
    "javascript:alert(1)",            # not fetchable, and not safe to render
    "ftp://files.example.com/x",
    "(company website)",
    "",
])
def test_a_source_that_is_not_a_real_web_url_is_discarded(monkeypatch, source):
    import json as _json
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_json.dumps(
        {"found": True, "name": "Someone", "title": "CMO", "source": source})))
    assert appmod._cpi_role_lookup(None, ["CMO"], "Acme") is None


def test_a_sourced_reply_with_no_name_is_discarded(monkeypatch):
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": true, "name": "", "title": "CMO", "source": "https://x.com/a"}'))
    assert appmod._cpi_role_lookup(None, ["CMO"], "Acme") is None


def test_an_adjacent_title_is_returned_but_flagged_as_not_exact(monkeypatch):
    """Plenty of companies have no CMO but do have a marketing lead. That is a
    useful answer, as long as it is never labelled as the CMO."""
    monkeypatch.setattr(appmod, "_responses_web_search", _web(
        '{"found": true, "name": "Dana Lee", "title": "VP of Marketing", '
        '"source": "https://acme.com/team"}'))
    got = appmod._cpi_role_lookup(None, ["CMO"], "Acme")
    assert got["name"] == "Dana Lee"
    assert got["title"] == "VP of Marketing"
    assert got["exact_title_match"] is False


def test_no_web_search_tool_means_no_claim_at_all(monkeypatch):
    """Without live search a model can only assert who held a role from stale
    background knowledge, and cannot produce a checkable URL. Silence beats a
    plausible guess here."""
    calls = []
    monkeypatch.setattr(appmod, "_responses_web_search",
                        _web(None, used=False, record=calls))
    assert appmod._cpi_role_lookup(None, ["CMO"], "Acme") is None
    assert len(calls) == 1, "an unavailable tool must not be retried on the next model"


def test_an_empty_web_reply_falls_through_to_the_next_model(monkeypatch):
    calls = []

    def _fn(oai, model, msgs, max_tokens):
        calls.append(model)
        return ("" if len(calls) == 1 else _GOOD), True

    monkeypatch.setattr(appmod, "_responses_web_search", _fn)
    assert appmod._cpi_role_lookup(None, ["CMO"], "Acme")["name"] == "Julie Woods-Moss"
    assert len(calls) == 2


def test_a_raising_model_is_survived(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("upstream 500")

    monkeypatch.setattr(appmod, "_responses_web_search", _boom)
    assert appmod._cpi_role_lookup(None, ["CMO"], "Acme") is None


@pytest.mark.parametrize("titles,company", [
    ([], "Acme"),            # nothing to look up
    (["CMO"], ""),           # no company to look it up at
    (None, None),
])
def test_nothing_to_look_up_costs_no_api_call(monkeypatch, titles, company):
    def _boom(*a, **k):
        raise AssertionError("must not call the model with nothing to look up")

    monkeypatch.setattr(appmod, "_responses_web_search", _boom)
    assert appmod._cpi_role_lookup(None, titles, company) is None


def test_the_question_names_the_company_and_the_title(monkeypatch):
    """A lookup that does not pin both would answer about the wrong business."""
    calls = []
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_GOOD, record=calls))
    appmod._cpi_role_lookup(None, ["CMO"], "Thoughtworks", "thoughtworks.com")
    asked = calls[0]["msgs"][-1]["content"]
    assert "CMO" in asked
    assert "Thoughtworks" in asked and "thoughtworks.com" in asked


def test_overlong_model_fields_are_truncated(monkeypatch):
    import json as _json
    monkeypatch.setattr(appmod, "_responses_web_search", _web(_json.dumps({
        "found": True, "name": "N" * 500, "title": "T" * 500,
        "source": "https://x.com/" + "p" * 900, "as_of": "a" * 200, "note": "z" * 900})))
    got = appmod._cpi_role_lookup(None, ["CMO"], "Acme")
    assert len(got["name"]) <= 120 and len(got["title"]) <= 160
    assert len(got["source"]) <= 400 and len(got["note"]) <= 400


# ── Provenance: a web-sourced name must never sit inside "our records" ──────

def _rendered(facts, research="brief"):
    cap = {}

    def _completion(oai, messages, max_tokens, temperature=None):
        cap["user"] = messages[-1]["content"]
        return "answer", "m"

    import app as _a
    real = _a._vimi_completion
    _a._vimi_completion = _completion
    try:
        _a._cpi_grounded_answer(None, facts, "CMO of thoughtworks", research=research)
    finally:
        _a._vimi_completion = real
    return cap["user"]


_FACTS = {"no_one_holds_the_requested_title": True, "requested_titles": ["CMO"],
          "company": "Thoughtworks, Ltd.",
          "other_senior_people_at_this_company": [{"full_name": "Sam Rao"}],
          "public_role_holder": {"name": "Julie Woods-Moss",
                                 "title": "Chief Marketing Officer",
                                 "source": "https://www.thoughtworks.com/x",
                                 "exact_title_match": True}}


def test_a_public_role_holder_is_rendered_outside_the_records_block():
    """<apollo_facts> is described to the model as our own records. A web-sourced
    person inside it invites the answer to present them as on file, in the same
    breath as saying our records do not have them."""
    out = _rendered(_FACTS)
    records = out.split("<apollo_facts>")[1].split("</apollo_facts>")[0]
    assert "Julie Woods-Moss" not in records
    assert "<public_role_holder>" in out
    public = out.split("<public_role_holder>")[1].split("</public_role_holder>")[0]
    assert "Julie Woods-Moss" in public and "thoughtworks.com/x" in public


def test_rendering_does_not_mutate_the_caller_facts():
    facts = dict(_FACTS)
    _rendered(facts)
    assert "public_role_holder" in facts, "popping must happen on a copy"


def test_a_long_people_list_cannot_push_the_role_holder_out():
    """The facts blob is size-trimmed. The one fact the answer must lead with has
    to be immune to that, not the first thing dropped."""
    facts = dict(_FACTS)
    facts["other_senior_people_at_this_company"] = [
        {"full_name": "Person %d" % i, "title": "Director of Something Long %d" % i,
         "headline": "x" * 200} for i in range(120)]
    out = _rendered(facts)
    assert "Julie Woods-Moss" in out


def test_no_public_block_is_added_when_there_is_no_role_holder():
    facts = {k: v for k, v in _FACTS.items() if k != "public_role_holder"}
    assert "<public_role_holder>" not in _rendered(facts)


# ── The fact key and the prompt that consumes it must stay in step ───────────

def test_the_answer_prompt_knows_about_the_public_role_holder_block():
    """If the key is renamed in code but not here, the block is silently ignored
    by the model and the feature dies without any test failing."""
    assert "public_role_holder" in appmod._CPI_ANSWER_SYSTEM
    assert "exact_title_match" in appmod._CPI_ANSWER_SYSTEM


def test_the_research_prompt_requires_answering_a_role_question_directly():
    """The original bug: research was told to write a company brief, so it wrote
    one instead of naming the person who was asked about."""
    p = appmod._CPI_RESEARCH_SYSTEM.lower()
    assert "cmo" in p and "role" in p
    assert "company overview" in p


# ── Wiring: the filter bar's empty-search note ───────────────────────────────

def _no_match_note(monkeypatch, role_result, titles=("CMO",)):
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: [
        {"id": "p1", "full_name": "Sam Rao", "title": "Head of Marketing",
         "organization_name": "Thoughtworks"}])
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", False))
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: role_result)
    seen = {}

    def _answer(oai, facts, question, research=""):
        seen["facts"] = facts
        return "answer"

    monkeypatch.setattr(appmod, "_cpi_grounded_answer", _answer)
    monkeypatch.setattr(appmod, "OpenAI", types.SimpleNamespace, raising=False)
    out = appmod._cpi_search_no_match_note(
        {"titles": list(titles), "company_domains": ["thoughtworks.com"]},
        ["Thoughtworks"], "apollo-key", {"credits": 0})
    return out, seen.get("facts", {})


def test_the_search_note_carries_a_found_role_holder(monkeypatch):
    role = {"name": "Julie Woods-Moss", "title": "Chief Marketing Officer",
            "source": "https://www.thoughtworks.com/x", "exact_title_match": True}
    _out, facts = _no_match_note(monkeypatch, role)
    assert facts["no_one_holds_the_requested_title"] is True
    assert facts["public_role_holder"]["name"] == "Julie Woods-Moss"


def test_the_search_note_omits_the_block_when_nothing_was_found(monkeypatch):
    _out, facts = _no_match_note(monkeypatch, None)
    assert "public_role_holder" not in facts


def test_a_failing_role_lookup_does_not_break_the_search_note(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("lookup exploded")

    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: [])
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", False))
    monkeypatch.setattr(appmod, "_cpi_role_lookup", _boom)
    monkeypatch.setattr(appmod, "_cpi_grounded_answer", lambda *a, **k: "answer")
    monkeypatch.setattr(appmod, "OpenAI", types.SimpleNamespace, raising=False)
    out = appmod._cpi_search_no_match_note(
        {"titles": ["CMO"], "company_domains": ["acme.com"]}, ["Acme"],
        "apollo-key", {"credits": 0})
    assert out["answer"] == "answer"


# ── Wiring: the chat panel ──────────────────────────────────────────────────

def _chat(monkeypatch, people_by_call, role_result, message="CMO of thoughtworks",
          titles=("CMO",), intent="person_at_company", filters_seen=None):
    """Drives the real /chat route with Apollo and OpenAI stubbed out.
    people_by_call is consumed one entry per search_people call, so the
    title-filtered search, the domain-scoped retry and the seniority fallback can
    each return something different."""
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(), raising=False)
    import json as _json
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": intent, "titles": list(titles), "company_name": "Thoughtworks",
        "seniorities": [], "max_results": 10}), "m"))
    monkeypatch.setattr(appmod, "_cpi_resolve_company", lambda *a, **k: (
        {"id": "org1", "name": "Thoughtworks, Ltd.", "primary_domain": "thoughtworks.com"}, None))
    # These tests are about what happens AFTER the company is pinned, so the
    # free pre-resolve probe is switched off: left live it would consume the
    # first people_by_call entry and silently shift every later stage's stub.
    # The probe has its own tests in test_cpi_free_probe.py.
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: None)
    calls = {"people": 0, "role": 0}

    def _sp(filters, key, **kw):
        i = calls["people"]
        calls["people"] += 1
        if filters_seen is not None:
            filters_seen.append(dict(filters))
        return people_by_call[min(i, len(people_by_call) - 1)]

    monkeypatch.setattr(ac, "search_people", _sp)
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", True))
    monkeypatch.setattr(appmod, "_cpi_enrich_person",
                        lambda *a, **k: {"matched": False})

    def _role(oai, t, company, domain=""):
        calls["role"] += 1
        return role_result

    monkeypatch.setattr(appmod, "_cpi_role_lookup", _role)
    seen = {}

    def _answer(oai, facts, question, research=""):
        seen["facts"] = facts
        return "answer"

    monkeypatch.setattr(appmod, "_cpi_grounded_answer", _answer)
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/chat", json={"message": message})
    assert r.status_code == 200
    return seen.get("facts", {}), calls


_ROLE = {"name": "Julie Woods-Moss", "title": "Chief Marketing Officer",
         "source": "https://www.thoughtworks.com/x", "exact_title_match": True}


def test_chat_the_exact_reported_scenario_now_names_the_public_cmo(monkeypatch):
    """Apollo has senior people at Thoughtworks but nobody titled CMO. The answer
    must be handed the publicly published CMO instead of only a records gap."""
    senior = [{"id": "p1", "full_name": "Sam Rao", "title": "Head of Delivery"}]
    facts, calls = _chat(monkeypatch, [[], senior], _ROLE)
    assert facts["no_one_holds_the_requested_title"] is True
    assert facts["public_role_holder"]["name"] == "Julie Woods-Moss"
    assert facts["public_role_holder"]["source"].startswith("https://")
    # The on-file people are still offered as the reachable contacts.
    assert facts["other_senior_people_at_this_company"][0]["full_name"] == "Sam Rao"


def test_chat_attaches_the_role_holder_when_apollo_has_nobody_at_all(monkeypatch):
    """The other dead end: not even a seniority fallback found anyone."""
    facts, _calls = _chat(monkeypatch, [[], []], _ROLE)
    assert facts["apollo_found_no_matching_people"] is True
    assert facts["public_role_holder"]["name"] == "Julie Woods-Moss"


def test_chat_omits_the_block_when_the_web_found_nobody(monkeypatch):
    facts, _calls = _chat(monkeypatch, [[], []], None)
    assert "public_role_holder" not in facts


def test_chat_does_not_look_up_a_role_apollo_already_answered(monkeypatch):
    """Cost and latency guard: the happy path must not pay for a web lookup."""
    found = [{"id": "p1", "full_name": "Julie Woods-Moss",
              "title": "Chief Marketing Officer"}]
    facts, calls = _chat(monkeypatch, [found], _ROLE)
    assert calls["role"] == 0, "no records gap means no public lookup"
    assert facts["person"]["full_name"] == "Julie Woods-Moss"


def test_chat_looks_the_role_up_at_most_once_per_question(monkeypatch):
    senior = [{"id": "p1", "full_name": "Sam Rao", "title": "Head of Delivery"}]
    _facts, calls = _chat(monkeypatch, [[], senior], _ROLE)
    assert calls["role"] == 1


# ── One company, several Apollo organization records ────────────────────────
# organization_ids scopes to exactly ONE record. A large company routinely has
# more (regional entities, a holding company, an acquired brand), so an
# executive filed under a sibling record reads as "nobody holds this title".

def test_a_title_missed_under_one_org_record_is_found_by_domain(monkeypatch):
    """The org-id scope finds nobody; the same search scoped by the shared
    employer domain finds the CMO on a sibling company record."""
    seen = []
    on_sibling = [{"id": "p9", "full_name": "Julie Woods-Moss",
                   "title": "Chief Marketing Officer"}]
    facts, calls = _chat(monkeypatch, [[], on_sibling], _ROLE, filters_seen=seen)
    # First search scoped by id, second by domain, and the answer is a real person
    # rather than a records gap.
    assert seen[0].get("organization_ids") == ["org1"]
    assert seen[1].get("company_domains") == ["thoughtworks.com"]
    assert "organization_ids" not in seen[1]
    assert facts["person"]["full_name"] == "Julie Woods-Moss"
    assert "no_one_holds_the_requested_title" not in facts
    assert calls["role"] == 0, "Apollo answered, so no public lookup is needed"


def test_the_domain_retry_still_refuses_a_wrong_title(monkeypatch):
    """The retry must not become a back door that passes off a Marketing Manager
    as the CMO just because the stricter scope found nothing."""
    wrong = [{"id": "p9", "full_name": "Someone Else", "title": "Marketing Manager"}]
    senior = [{"id": "p1", "full_name": "Sam Rao", "title": "Head of Delivery"}]
    facts, _calls = _chat(monkeypatch, [[], wrong, senior], _ROLE)
    assert facts["no_one_holds_the_requested_title"] is True
    assert facts["public_role_holder"]["name"] == "Julie Woods-Moss"


def test_no_domain_retry_when_the_first_search_already_worked(monkeypatch):
    found = [{"id": "p1", "full_name": "Julie Woods-Moss",
              "title": "Chief Marketing Officer"}]
    _facts, calls = _chat(monkeypatch, [found], _ROLE)
    assert calls["people"] == 1, "a successful search must not be retried"


def test_a_failing_domain_retry_falls_through_to_the_records_gap(monkeypatch):
    """Apollo erroring on the retry must not 500 the question."""
    import tracker.apollo_client as ac
    calls = {"n": 0}

    def _sp(filters, key, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("apollo 502 on the retry")
        return []

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(), raising=False)
    import json as _json
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": "person_at_company", "titles": ["CMO"],
        "company_name": "Thoughtworks", "max_results": 10}), "m"))
    monkeypatch.setattr(appmod, "_cpi_resolve_company", lambda *a, **k: (
        {"id": "org1", "name": "Thoughtworks, Ltd.",
         "primary_domain": "thoughtworks.com"}, None))
    # Off, so "the retry" really is call 2 here: the free pre-resolve probe
    # would otherwise take call 1 and move the failure onto the first search.
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: None)
    monkeypatch.setattr(ac, "search_people", _sp)
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", True))
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: _ROLE)
    seen = {}
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="": seen.setdefault("f", facts) and "a")
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/chat",
               json={"message": "CMO of thoughtworks"})
    assert r.status_code == 200
    assert seen["f"]["public_role_holder"]["name"] == "Julie Woods-Moss"
