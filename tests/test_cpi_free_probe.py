"""Tests for the two fixes to "1 Apollo credit used" with no Enrich button.

Reported against "CMO of tealium": the answer named Tealium's published CMO
(Heidi Bullock, from the live web), said our records do not have her, listed
seven other senior people, and charged 1 Apollo credit -- with no way to act on
the name it had just produced.

Two distinct defects behind that:

1. The credit went on mixed_companies/search purely to learn Apollo's
   organization id for Tealium. But mixed_people/api_search is FREE and returns
   each person's employer id and name, so one free domain-scoped people search
   yields the same organization id. _cpi_probe_company_free does that first and
   the paid search now only runs when the free route cannot confirm the company.

2. "Our records do not have Heidi Bullock on file" was never checked against
   anything. The only search that had run was filtered BY TITLE, so a published
   CMO sitting in Apollo under a different title read as absent. The claim is now
   established in code by a free name lookup, and either way the reply carries
   Enrich metadata so the user can spend a credit on that person deliberately --
   Apollo's people/match resolves by name plus domain even when the title-scoped
   search never surfaced them.
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


@pytest.fixture(autouse=True)
def clear_caches():
    appmod._CPI_ORG_RESOLVE_CACHE.clear()
    yield
    appmod._CPI_ORG_RESOLVE_CACHE.clear()


def _people_stub(monkeypatch, handler):
    """Stub search_people with a handler over the filters it is given."""
    import tracker.apollo_client as ac
    seen = []

    def _sp(filters, api_key, page=1, per_page=25, strict=False, meta=None):
        seen.append(dict(filters))
        return handler(dict(filters))

    monkeypatch.setattr(ac, "search_people", _sp)
    return seen


_TEALIUM_ROW = {"id": "p1", "full_name": "Ted Purcell",
                "title": "Chief Revenue Officer",
                "organization_id": "org-tealium",
                "organization_name": "Tealium",
                "organization_domain": "tealium.com"}


# ── _cpi_probe_company_free: pinning a company for 0 credits ────────────────

def test_a_plain_name_is_pinned_from_the_guessed_domain(monkeypatch):
    seen = _people_stub(monkeypatch, lambda f: [_TEALIUM_ROW])
    org = appmod._cpi_probe_company_free("Tealium", "key")
    assert org == {"id": "org-tealium", "name": "Tealium",
                   "primary_domain": "tealium.com"}
    assert seen[0]["company_domains"] == ["tealium.com"]


def test_the_probe_never_touches_the_company_search(monkeypatch):
    """The whole point: no mixed_companies/search call, so no credit."""
    import tracker.apollo_client as ac
    _people_stub(monkeypatch, lambda f: [_TEALIUM_ROW])
    calls = []
    monkeypatch.setattr(ac, "search_companies",
                        lambda *a, **k: calls.append(1) or [])
    appmod._cpi_probe_company_free("Tealium", "key")
    assert calls == [], "the probe must not run the billable company search"


def test_a_legal_suffix_still_matches(monkeypatch):
    """Apollo stores "Thoughtworks, Ltd."; the user types "Thoughtworks"."""
    _people_stub(monkeypatch, lambda f: [dict(_TEALIUM_ROW,
                                              organization_id="org-tw",
                                              organization_name="Thoughtworks, Ltd.",
                                              organization_domain="thoughtworks.com")])
    org = appmod._cpi_probe_company_free("Thoughtworks", "key")
    assert org and org["id"] == "org-tw"


def test_a_different_company_at_the_guessed_domain_is_refused(monkeypatch):
    """The guard that keeps this from answering about the wrong business:
    "Delta" guesses delta.com, which is Delta Air Lines. That is not an exact
    normalized match for what was typed, so the probe declines and the paid
    resolver (with its disambiguation prompt) runs instead."""
    _people_stub(monkeypatch, lambda f: [dict(_TEALIUM_ROW,
                                              organization_id="org-dal",
                                              organization_name="Delta Air Lines",
                                              organization_domain="delta.com")])
    assert appmod._cpi_probe_company_free("Delta", "key") is None


def test_a_row_with_no_employer_id_is_refused(monkeypatch):
    """Without an organization id nothing downstream can scope a search to this
    company, so it is not a usable resolution."""
    _people_stub(monkeypatch, lambda f: [dict(_TEALIUM_ROW, organization_id=None)])
    assert appmod._cpi_probe_company_free("Tealium", "key") is None


def test_no_people_at_the_guessed_domain_is_refused(monkeypatch):
    _people_stub(monkeypatch, lambda f: [])
    assert appmod._cpi_probe_company_free("Tealium", "key") is None


def test_an_input_that_is_already_a_domain_is_left_to_the_normal_path(monkeypatch):
    seen = _people_stub(monkeypatch, lambda f: [_TEALIUM_ROW])
    assert appmod._cpi_probe_company_free("tealium.com", "key") is None
    assert seen == [], "a real domain resolves exactly, so do not guess at it"


def test_apollo_erroring_in_the_probe_is_not_fatal(monkeypatch):
    import tracker.apollo_client as ac

    def _boom(*a, **k):
        raise RuntimeError("apollo 502")

    monkeypatch.setattr(ac, "search_people", _boom)
    assert appmod._cpi_probe_company_free("Tealium", "key") is None


def test_no_api_key_means_no_probe(monkeypatch):
    seen = _people_stub(monkeypatch, lambda f: [_TEALIUM_ROW])
    assert appmod._cpi_probe_company_free("Tealium", "") is None
    assert seen == []


# ── _cpi_person_name_matches: who Apollo actually returned ──────────────────

def test_a_middle_initial_still_matches():
    assert appmod._cpi_person_name_matches("Heidi A. Bullock", "Heidi Bullock")


def test_a_shared_first_name_does_not_match():
    """q_keywords is a fuzzy relevance hint, so this is the check that stops a
    same-company namesake being presented as the published role holder."""
    assert not appmod._cpi_person_name_matches("Heidi Chen", "Heidi Bullock")


def test_a_masked_last_name_does_not_match():
    """Apollo masks last names on some plan tiers. A masked row cannot be
    confirmed to be the right person, so it must not be claimed as one."""
    assert not appmod._cpi_person_name_matches("Heidi B.", "Heidi Bullock")


def test_a_one_word_name_is_never_a_match():
    assert not appmod._cpi_person_name_matches("Heidi Bullock", "Heidi")


def test_case_and_accents_do_not_break_a_match():
    assert appmod._cpi_person_name_matches("JOSÉ GARCÍA", "Jose Garcia")


def test_an_honorific_or_suffix_is_ignored():
    assert appmod._cpi_person_name_matches("Dr. Heidi Bullock Jr.", "Heidi Bullock")


# ── _cpi_person_on_file: the claim that used to be asserted unchecked ───────

def test_a_person_on_file_under_another_title_is_found(monkeypatch):
    """The exact false negative: the title search found no CMO, but she is on
    file as VP Marketing, and nobody had ever looked."""
    row = {"id": "p-heidi", "full_name": "Heidi Bullock",
           "title": "VP, Marketing", "organization_domain": "tealium.com"}
    seen = _people_stub(monkeypatch, lambda f: [row])
    got = appmod._cpi_person_on_file("Heidi Bullock", "tealium.com", "key")
    assert got["id"] == "p-heidi"
    assert seen[0]["keywords"] == "Heidi Bullock"
    assert seen[0]["company_domains"] == ["tealium.com"]


def test_a_wrong_person_coming_back_is_not_treated_as_a_match(monkeypatch):
    _people_stub(monkeypatch, lambda f: [{"id": "x", "full_name": "Someone Else"}])
    assert appmod._cpi_person_on_file("Heidi Bullock", "tealium.com", "key") is None


def test_the_on_file_lookup_costs_no_credits(monkeypatch):
    """It is a people search, which Apollo does not bill, and it must never
    reach the billable match/enrich endpoints on its own."""
    import tracker.apollo_client as ac
    _people_stub(monkeypatch, lambda f: [{"id": "p", "full_name": "Heidi Bullock"}])
    billed = []
    monkeypatch.setattr(ac, "search_companies", lambda *a, **k: billed.append(1) or [])
    monkeypatch.setattr(appmod, "_cpi_enrich_person",
                        lambda *a, **k: billed.append(1) or {"matched": False})
    appmod._cpi_person_on_file("Heidi Bullock", "tealium.com", "key")
    assert billed == []


def test_a_url_shaped_domain_is_cleaned_before_searching(monkeypatch):
    seen = _people_stub(monkeypatch, lambda f: [])
    appmod._cpi_person_on_file("Heidi Bullock", "https://www.tealium.com/", "key")
    assert seen[0]["company_domains"] == ["tealium.com"]


def test_a_missing_domain_means_no_lookup(monkeypatch):
    seen = _people_stub(monkeypatch, lambda f: [])
    assert appmod._cpi_person_on_file("Heidi Bullock", "", "key") is None
    assert seen == []


# ── End to end: the reported question ───────────────────────────────────────

_ROLE = {"name": "Heidi Bullock", "title": "Chief Marketing Officer",
         "source": "https://tealium.com/company/leadership/",
         "exact_title_match": True}


def _ask(monkeypatch, people_handler, role=_ROLE, message="CMO of tealium",
         company_rows=None):
    """Drives the real /chat route. search_people is answered from the filters
    it receives (rather than a call sequence) so the probe, the title search,
    the fallback and the name lookup can each be recognised for what they are."""
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": "person_at_company", "titles": ["CMO"], "company_name": "Tealium",
        "seniorities": [], "max_results": 10}), "m"))
    seen_people = _people_stub(monkeypatch, people_handler)
    billed = []

    def _sc(filters, api_key, page=1, per_page=25, strict=False):
        billed.append(dict(filters))
        return list(company_rows or [])

    monkeypatch.setattr(ac, "search_companies", _sc)
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: role)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    facts_box = {}

    def _answer(oai, facts, question, research=""):
        facts_box["f"] = facts
        return "answer"

    monkeypatch.setattr(appmod, "_cpi_grounded_answer", _answer)
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/gtm/company-people-intelligence/chat", json={"message": message})
    assert r.status_code == 200
    return r.get_json(), facts_box.get("f", {}), billed, seen_people


def _tealium_handler(titled=(), senior=(), named=()):
    """search_people answers by which stage is asking."""
    def _h(f):
        if f.get("keywords"):                      # the name lookup
            return list(named)
        if f.get("titles"):                        # the title-scoped search
            return list(titled)
        if f.get("seniorities"):                   # the senior fallback
            return list(senior)
        return [_TEALIUM_ROW]                      # the free probe
    return _h


def test_the_reported_question_now_costs_nothing(monkeypatch):
    body, facts, billed, _seen = _ask(
        monkeypatch, _tealium_handler(titled=[], senior=[_TEALIUM_ROW]))
    assert billed == [], "no mixed_companies/search means no credit"
    assert not body.get("credits")
    assert facts["no_one_holds_the_requested_title"] is True


def test_the_reported_question_now_offers_an_enrich_button(monkeypatch):
    body, _facts, _billed, _seen = _ask(
        monkeypatch, _tealium_handler(titled=[], senior=[_TEALIUM_ROW]))
    assert body["enrich"] == {"type": "person", "name": "Heidi Bullock",
                              "title": "Chief Marketing Officer",
                              "domain": "tealium.com", "apollo_id": ""}


def test_the_other_records_gap_also_offers_the_button(monkeypatch):
    """The reported screenshot hit the "nobody holds that title" branch, but the
    same question against a company where Apollo has NOBODY at all takes a
    different branch, which needs the button just as much: a public name with no
    way to act on it is the whole complaint."""
    body, facts, _billed, _seen = _ask(
        monkeypatch, _tealium_handler(titled=[], senior=[]))
    assert facts["apollo_found_no_matching_people"] is True
    assert body["enrich"] == {"type": "person", "name": "Heidi Bullock",
                              "title": "Chief Marketing Officer",
                              "domain": "tealium.com", "apollo_id": ""}


def test_the_other_records_gap_also_checks_before_claiming_absence(monkeypatch):
    heidi = {"id": "p-heidi", "full_name": "Heidi Bullock", "title": "VP, Marketing"}
    body, facts, _billed, _seen = _ask(
        monkeypatch, _tealium_handler(titled=[], senior=[], named=[heidi]))
    assert facts["apollo_found_no_matching_people"] is True
    assert facts["public_role_holder_is_on_file"]["title"] == "VP, Marketing"
    assert body["enrich"]["apollo_id"] == "p-heidi"


def test_a_publicly_named_person_who_is_on_file_is_reported_as_on_file(monkeypatch):
    """The false negative, fixed: she is in Apollo under a different title, so
    the answer is told that in code rather than guessing she is absent."""
    heidi = {"id": "p-heidi", "full_name": "Heidi Bullock", "title": "VP, Marketing",
             "organization_domain": "tealium.com"}
    body, facts, _billed, _seen = _ask(
        monkeypatch, _tealium_handler(titled=[], senior=[_TEALIUM_ROW], named=[heidi]))
    assert facts["public_role_holder_is_on_file"]["title"] == "VP, Marketing"
    assert "public_role_holder_not_in_our_records" not in facts
    # ...and the button carries her real Apollo id, so the enrichment is exact.
    assert body["enrich"]["apollo_id"] == "p-heidi"


def test_a_person_genuinely_not_on_file_says_so_from_a_real_check(monkeypatch):
    _body, facts, _billed, _seen = _ask(
        monkeypatch, _tealium_handler(titled=[], senior=[_TEALIUM_ROW], named=[]))
    assert facts["public_role_holder_not_in_our_records"] is True
    assert "public_role_holder_is_on_file" not in facts


def test_nothing_is_claimed_either_way_when_there_is_no_public_name(monkeypatch):
    """No role holder means nobody was looked up, so the answer must not be
    handed either flag to reason from."""
    _body, facts, _billed, _seen = _ask(
        monkeypatch, _tealium_handler(titled=[], senior=[_TEALIUM_ROW]), role=None)
    assert "public_role_holder_is_on_file" not in facts
    assert "public_role_holder_not_in_our_records" not in facts


def test_the_enrich_metadata_never_reaches_the_model(monkeypatch):
    """It is UI wiring, not a fact. In the facts it would become prose."""
    _body, facts, _billed, _seen = _ask(
        monkeypatch, _tealium_handler(titled=[], senior=[_TEALIUM_ROW]))
    blob = _json.dumps(facts, default=str)
    assert "apollo_id" not in blob


def test_a_company_the_probe_cannot_confirm_still_falls_back_to_the_paid_path(monkeypatch):
    """No regression for the ambiguous case: the paid resolver still runs, and
    still gets to disambiguate."""
    def _h(f):
        if f.get("keywords"):
            return []
        if f.get("titles") or f.get("seniorities"):
            return []
        return [dict(_TEALIUM_ROW, organization_name="Delta Air Lines",
                     organization_domain="delta.com")]

    _body, _facts, billed, _seen = _ask(
        monkeypatch, _h, message="CMO of Delta",
        company_rows=[{"id": "o1", "name": "Delta Air Lines",
                       "primary_domain": "delta.com"}])
    assert billed, "the paid resolver must still run when the probe declines"


def test_the_probe_runs_before_the_paid_search_not_after(monkeypatch):
    """Ordering is the whole saving: probing after paying would save nothing."""
    order = []
    import tracker.apollo_client as ac

    def _sp(filters, api_key, page=1, per_page=25, strict=False, meta=None):
        if not (filters.get("titles") or filters.get("seniorities")
                or filters.get("keywords")):
            order.append("probe")
        return [_TEALIUM_ROW] if not filters.get("titles") else []

    def _sc(filters, api_key, page=1, per_page=25, strict=False):
        order.append("paid")
        return []

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": "person_at_company", "titles": ["CMO"], "company_name": "Tealium",
        "seniorities": [], "max_results": 10}), "m"))
    monkeypatch.setattr(ac, "search_people", _sp)
    monkeypatch.setattr(ac, "search_companies", _sc)
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer", lambda *a, **k: "answer")
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    c.post("/p2/gtm/company-people-intelligence/chat",
           json={"message": "CMO of Tealium"})
    assert order and order[0] == "probe"
    assert "paid" not in order, "a confirmed free probe must skip the paid search"


# ── The prompt must not let the model invent the on-file verdict ────────────

def test_the_prompt_forbids_guessing_whether_we_hold_the_person():
    p = appmod._CPI_ANSWER_SYSTEM
    assert "public_role_holder_is_on_file" in p
    assert "public_role_holder_not_in_our_records" in p
    # The old prompt told the model to assert the negative unconditionally.
    assert "Then note separately that our own records do not have them" not in p
