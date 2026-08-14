"""The chat panel's version of the defect the search grid had: an absence
asserted from a request that never completed.

The grid says "No matches" and a reader can shrug. The chat says "our records
have nobody matching a CMO at Thoughtworks" in fluent prose, in the same voice
it uses for things it actually established, and a reader believes it and stops
looking. Every claim of absence on this path therefore has to be traceable to a
search that ran.

Three places it was not:

  - _cpi_person_on_file returned None both when Apollo answered with nobody and
    when Apollo did not answer at all. The caller turned a bare None into
    "public_role_holder_not_in_our_records", so a timeout re-created word for
    word the assertion this function was written to stop. Its own docstring
    says it exists because "the answer used to assert 'our records do not have
    X on file' about a publicly-named person without anything ever having
    looked" -- and a failed lookup is nothing having looked.

  - The domain-scoped retry exists because one real company often has several
    Apollo organization records, so an org-id search finding nothing does NOT
    establish that nobody holds the title. Its failure was swallowed into
    `people = []`, which fed the "no_one_holds_the_requested_title" path whose
    entire premise is that the absence was checked. An executive filed under a
    sibling record would have been reported as not existing.

  - The consolation list ("nobody holds that title, here are the nearest people
    we do hold") rests on the same premise, so it must not be offered when the
    half that establishes the "nobody" is the half that failed.

The answer prompt already had the right rule for the first one: "If NEITHER key
is present, nobody checked: say nothing at all about whether we hold them." The
fix is to honor it by sending neither key, not to add a new instruction.
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
import tracker.apollo_client as ac  # noqa: E402

_CHAT = "/p2/b2b-agents/company-people-intelligence/chat"
_ROLE = {"found": True, "name": "Julie Woods-Moss",
         "title": "Chief Marketing Officer",
         "source": "https://www.thoughtworks.com/en-us/profiles/leaders/julie-woods-moss"}


@pytest.fixture
def chat(monkeypatch):
    """The chat route with everything outside Apollo held still, returning the
    facts dict the answer was actually built from."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": "person_at_company", "titles": ["CMO"],
        "company_name": "Thoughtworks", "max_results": 10}), "m"))
    monkeypatch.setattr(appmod, "_cpi_resolve_company", lambda *a, **k: (
        {"id": "org1", "name": "Thoughtworks, Ltd.",
         "primary_domain": "thoughtworks.com"}, None))
    # Off, so call counting below refers to the real searches only.
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("brief", True))
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: _ROLE)
    seen = {}
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="": seen.setdefault("f", facts) and "a")

    def _ask():
        c = appmod.app.test_client()
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
        r = c.post(_CHAT, json={"message": "CMO of thoughtworks"})
        assert r.status_code == 200, "a failed lookup must never 500 the question"
        return seen.get("f") or {}

    return _ask


def _searches(monkeypatch, *behaviours):
    """Drive successive search_people calls through the given behaviours."""
    calls = {"n": 0}

    def _sp(filters, key, **kw):
        calls["n"] += 1
        b = behaviours[min(calls["n"], len(behaviours)) - 1]
        if isinstance(b, Exception):
            raise b
        return list(b)

    monkeypatch.setattr(ac, "search_people", _sp)
    return calls


# ── "We do not hold them" has to mean somebody looked ──────────────────────

def test_the_on_file_check_reports_that_it_completed(monkeypatch):
    monkeypatch.setattr(ac, "search_people", lambda *a, **k: [])
    checked = {}
    assert appmod._cpi_person_on_file("Julie Woods-Moss", "thoughtworks.com",
                                      "k", checked=checked) is None
    assert checked.get("ok") is True, "a completed check did not say so"


def test_a_failed_on_file_check_does_not_report_completion(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("apollo down")

    monkeypatch.setattr(ac, "search_people", _boom)
    checked = {}
    assert appmod._cpi_person_on_file("Julie Woods-Moss", "thoughtworks.com",
                                      "k", checked=checked) is None
    assert not checked.get("ok"), "a failed lookup claimed to have completed"


def test_a_failed_on_file_check_claims_nothing_about_our_records(chat, monkeypatch):
    """The first search answers with nobody, so the flow reaches the role
    lookup; the on-file check for that person then fails. Neither key may go to
    the model, whose standing rule for that case is to say nothing at all."""
    _searches(monkeypatch, [], RuntimeError("apollo down on the on-file check"))
    facts = chat()
    assert "public_role_holder_not_in_our_records" not in facts, (
        "an absence was asserted from a lookup that never completed")
    assert "public_role_holder_is_on_file" not in facts
    assert facts.get("public_role_holder", {}).get("name") == "Julie Woods-Moss", (
        "the researched name should still reach the answer")


def test_a_completed_on_file_check_does_say_we_do_not_hold_them(chat, monkeypatch):
    """The mirror: when the check really ran, the claim is earned and must
    still be made, or this fix would have traded one silence for another."""
    _searches(monkeypatch, [], [])
    facts = chat()
    assert facts.get("public_role_holder_not_in_our_records") is True


# ── A retry that could not run does not establish an absence ───────────────

def test_a_failed_widening_retry_withdraws_the_absence(chat, monkeypatch):
    """One company often has several Apollo organization records. The org-id
    search finding nothing establishes nothing about the others, which is why
    the retry exists -- so its failure cannot be laundered into "nobody"."""
    _searches(monkeypatch, [], RuntimeError("apollo 502 on the retry"))
    facts = chat()
    assert facts.get("apollo_lookup_unavailable") is True
    assert "apollo_found_no_matching_people" not in facts, (
        "the answer claimed our records hold nobody, on a check that failed")


def test_a_completed_empty_retry_still_says_we_hold_nobody(chat, monkeypatch):
    """The mirror again: two successful empty searches DO establish it."""
    _searches(monkeypatch, [], [])
    facts = chat()
    assert facts.get("apollo_found_no_matching_people") is True
    assert "apollo_lookup_unavailable" not in facts


def test_the_researched_name_survives_a_failed_retry(chat, monkeypatch):
    """Withdrawing the absence must not also throw away the most useful thing
    left to say. Rerouting the whole reply to the records-unavailable path did
    exactly that, which is why the flag exists instead."""
    _searches(monkeypatch, [], RuntimeError("apollo 502 on the retry"))
    facts = chat()
    assert facts.get("public_role_holder", {}).get("name") == "Julie Woods-Moss"


def test_the_consolation_search_does_not_even_run_on_an_incomplete_check(chat, monkeypatch):
    """"Nobody holds that title, so here are the nearest people we do hold"
    rests on the same premise as the claim itself.

    Gated at the SEARCH, not at the presentation. By the time the consolation
    flag is read, `people` has already been replaced by the same-function list,
    so suppressing only the framing would hand the reader a list of near-misses
    with nothing saying they are near-misses: worse than the claim it set out to
    withdraw."""
    ran = {"n": 0}

    def _same_function(*a, **k):
        ran["n"] += 1
        return [{"id": "x", "full_name": "Someone Else", "title": "VP Marketing"}]

    monkeypatch.setattr(appmod, "_cpi_same_function_people", _same_function)
    _searches(monkeypatch, [], RuntimeError("apollo 502 on the retry"))
    facts = chat()
    assert ran["n"] == 0, "the consolation search ran on an unestablished absence"
    assert "no_one_holds_the_requested_title" not in facts
    assert "closest_people_we_hold" not in facts
    assert facts.get("apollo_lookup_unavailable") is True


def test_the_consolation_search_still_runs_on_an_established_absence(chat, monkeypatch):
    """The mirror: two clean empty searches earn the consolation list."""
    ran = {"n": 0}

    def _same_function(*a, **k):
        ran["n"] += 1
        return [{"id": "x", "full_name": "Someone Else", "title": "VP Marketing"}]

    monkeypatch.setattr(appmod, "_cpi_same_function_people", _same_function)
    _searches(monkeypatch, [], [])
    facts = chat()
    assert ran["n"] == 1
    assert facts.get("no_one_holds_the_requested_title") is True


# ── Names left masked because nobody answered ──────────────────────────────

def test_a_partly_unanswered_name_reveal_leaves_the_names_masked(monkeypatch):
    """Not a claim, so nothing here is untrue: the answer renders an un-revealed
    surname as "Vivek Sh." with its flag intact. It is wired through so the log
    can tell this apart from Apollo simply holding no better record."""
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: {})
    monkeypatch.setattr(appmod, "_cpi_id_cache_write", lambda p: None)
    seen = {}

    def _bulk(ids, api_key, failed=None):
        seen["failed_passed"] = failed is not None
        if failed is not None:
            failed.extend(ids)
        return {}

    monkeypatch.setattr(ac, "bulk_match_people", _bulk)
    people = [{"id": "p1", "full_name": "Vivek Sh***a", "name_masked": True}]
    out = appmod._cpi_reveal_names(people, "k")
    assert seen["failed_passed"] is True, "the unanswered ids are not being collected"
    assert out[0]["full_name"] == "Vivek Sh***a", "the row must survive untouched"
