"""Tests for not re-paying to resolve a company Apollo already told us about.

Reported: a chat answer read "our records have nobody matching CMO at
Thoughtworks" right next to "1 Apollo credit used", which reads as paying for
nothing. The credit was actually spent identifying WHICH company to search
(mixed_companies/search bills 1 credit on any call that returns a result), not
on the person search, which is free either way -- but that purchase is only
worth making once. Before this fix, _cpi_resolve_company (chat's company
resolver) cached nothing at all: the exact same company, asked about twice in
two different questions or by two different people, paid twice.
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


@pytest.fixture(autouse=True)
def clear_cache():
    appmod._CPI_ORG_RESOLVE_CACHE.clear()
    yield
    appmod._CPI_ORG_RESOLVE_CACHE.clear()


def _fake_search(monkeypatch, rows):
    """Stub search_companies, counting calls and billing exactly like Apollo:
    1 credit per call that returns at least one row, 0 for an empty result."""
    import tracker.apollo_client as ac
    calls = []

    def _fake(filters, api_key, page=1, per_page=25, strict=False):
        calls.append(dict(filters))
        return list(rows)

    monkeypatch.setattr(ac, "search_companies", _fake)
    return calls


_ACME = {"id": "a1", "name": "Acme Health", "primary_domain": "acmehealth.com"}


# ── _cpi_org_cache_key ────────────────────────────────────────────────────────

def test_a_domain_is_the_preferred_key():
    assert appmod._cpi_org_cache_key("Acme", "acme.com") == "d:acme.com"


def test_falls_back_to_the_normalized_name_with_no_domain():
    assert appmod._cpi_org_cache_key("Acme Inc", "") == "n:acme"


def test_nothing_to_key_on_is_an_empty_string():
    assert appmod._cpi_org_cache_key("", "") == ""


# ── _cpi_resolve_company_direct: resolving twice must not pay twice ─────────

def test_the_same_query_is_not_re_resolved(monkeypatch):
    calls = _fake_search(monkeypatch, [_ACME])
    spend1, spend2 = {"credits": 0}, {"credits": 0}
    org1, _c1 = appmod._cpi_resolve_company_direct("Acme Health", "key", spend=spend1)
    org2, _c2 = appmod._cpi_resolve_company_direct("Acme Health", "key", spend=spend2)
    assert org1["id"] == org2["id"] == "a1"
    assert len(calls) == 1, "the second identical question must not touch Apollo at all"
    assert spend1["credits"] == 1
    assert spend2["credits"] == 0, "a cache hit spends nothing, on any spend dict"


def test_a_later_question_by_the_resolved_name_also_hits(monkeypatch):
    """The first question resolved by domain (e.g. after a typo correction);
    a later, ordinary question naming the company outright must still hit."""
    calls = _fake_search(monkeypatch, [_ACME])
    appmod._cpi_resolve_company_direct("acmehealth.com", "key", domain="acmehealth.com")
    org, _choices = appmod._cpi_resolve_company_direct("Acme Health", "key")
    assert org["id"] == "a1"
    assert len(calls) == 1


def test_a_later_question_by_domain_also_hits_a_name_resolved_entry(monkeypatch):
    calls = _fake_search(monkeypatch, [_ACME])
    appmod._cpi_resolve_company_direct("Acme Health", "key")
    org, _choices = appmod._cpi_resolve_company_direct("", "key", domain="acmehealth.com")
    assert org["id"] == "a1"
    assert len(calls) == 1


def test_disambiguation_choices_are_cached_too(monkeypatch):
    """Asking the same ambiguous name twice must not run the search twice
    either, even though nothing was resolved to a single company."""
    calls = _fake_search(monkeypatch, [
        {"id": "a", "name": "Acme", "primary_domain": "acme.com"},
        {"id": "b", "name": "Acme", "primary_domain": "acme.net"}])
    org1, choices1 = appmod._cpi_resolve_company_direct("Acme", "key")
    org2, choices2 = appmod._cpi_resolve_company_direct("Acme", "key")
    assert org1 is None and org2 is None
    assert len(choices1) == len(choices2) == 2
    assert len(calls) == 1


def test_a_miss_is_not_cached_but_costs_nothing_to_repeat(monkeypatch):
    """Apollo bills 0 credits for a call that returns nothing, so there is no
    saving from caching a miss, and it is deliberately not cached (see the
    module comment): a genuinely new company appearing under that name later
    must not be told it does not exist because of a stale negative."""
    calls = _fake_search(monkeypatch, [])
    appmod._cpi_resolve_company_direct("NoSuchCompanyXyz", "key")
    appmod._cpi_resolve_company_direct("NoSuchCompanyXyz", "key")
    assert len(calls) == 2, "both calls happened, but neither one was billable"


def test_different_companies_do_not_collide(monkeypatch):
    import tracker.apollo_client as ac
    by_name = {"acme": [_ACME], "widgetco": [
        {"id": "w1", "name": "WidgetCo", "primary_domain": "widgetco.com"}]}

    def _fake(filters, api_key, page=1, per_page=25, strict=False):
        return list(by_name.get((filters.get("name") or "").lower(), []))

    monkeypatch.setattr(ac, "search_companies", _fake)
    a, _ = appmod._cpi_resolve_company_direct("Acme", "key")
    w, _ = appmod._cpi_resolve_company_direct("WidgetCo", "key")
    assert a["id"] == "a1" and w["id"] == "w1"


def test_the_cache_expires(monkeypatch):
    calls = _fake_search(monkeypatch, [_ACME])
    appmod._cpi_resolve_company_direct("Acme Health", "key")
    key = appmod._cpi_org_cache_key("Acme Health", "")
    appmod._CPI_ORG_RESOLVE_CACHE[key]["ts"] -= appmod._CPI_ORG_RESOLVE_TTL_S + 1
    appmod._cpi_resolve_company_direct("Acme Health", "key")
    assert len(calls) == 2


# ── the durable (DB-backed) cache: survives a process restart ───────────────
#
# _CPI_ORG_RESOLVE_CACHE lives in process memory, and Railway restarts this
# process on every deploy to this repo -- which is every push. That silently
# defeated the whole point of the cache above: the first question about any
# company asked right after a deploy always re-paid its resolution credit,
# even though it had already been resolved (possibly minutes earlier) by the
# process that just got replaced. These tests fake the DB layer itself (a
# plain dict standing in for Postgres) so they exercise the real read/write
# wiring in _cpi_resolve_company_direct without needing a live database.

def test_a_restart_does_not_re_pay_when_the_durable_cache_still_has_it(monkeypatch):
    calls = _fake_search(monkeypatch, [_ACME])
    store = {}
    monkeypatch.setattr(appmod, "_cpi_org_db_write",
                        lambda keys, org, choices: store.update({k: (org, choices) for k in keys}))
    monkeypatch.setattr(appmod, "_cpi_org_db_read", lambda key: store.get(key))

    org1, _c1 = appmod._cpi_resolve_company_direct("Acme Health", "key")
    assert org1["id"] == "a1" and len(calls) == 1

    # The process restart Railway does on every deploy -- memory is gone.
    appmod._CPI_ORG_RESOLVE_CACHE.clear()

    org2, _c2 = appmod._cpi_resolve_company_direct("Acme Health", "key")
    assert org2["id"] == "a1"
    assert len(calls) == 1, "the durable cache must absorb this, not Apollo"


def test_a_durable_cache_miss_falls_through_to_apollo_normally(monkeypatch):
    """No DB configured (or a genuine miss) must behave exactly like today:
    resolve from Apollo, no error, still populates the in-memory cache."""
    calls = _fake_search(monkeypatch, [_ACME])
    monkeypatch.setattr(appmod, "_cpi_org_db_write", lambda keys, org, choices: None)
    monkeypatch.setattr(appmod, "_cpi_org_db_read", lambda key: None)

    org1, _c1 = appmod._cpi_resolve_company_direct("Acme Health", "key")
    org2, _c2 = appmod._cpi_resolve_company_direct("Acme Health", "key")
    assert org1["id"] == org2["id"] == "a1"
    assert len(calls) == 1, "the in-memory cache still absorbs the second call"


def test_disambiguation_choices_also_survive_a_restart(monkeypatch):
    calls = _fake_search(monkeypatch, [
        {"id": "a", "name": "Acme", "primary_domain": "acme.com"},
        {"id": "b", "name": "Acme", "primary_domain": "acme.net"}])
    store = {}
    monkeypatch.setattr(appmod, "_cpi_org_db_write",
                        lambda keys, org, choices: store.update({k: (org, choices) for k in keys}))
    monkeypatch.setattr(appmod, "_cpi_org_db_read", lambda key: store.get(key))

    org1, choices1 = appmod._cpi_resolve_company_direct("Acme", "key")
    assert org1 is None and len(choices1) == 2

    appmod._CPI_ORG_RESOLVE_CACHE.clear()

    org2, choices2 = appmod._cpi_resolve_company_direct("Acme", "key")
    assert org2 is None and len(choices2) == 2
    assert len(calls) == 1


# ── End to end: two chat questions about the same company ───────────────────

def _chat_message(monkeypatch, message, seen_calls):
    import json as _json
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(), raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": "person_at_company", "titles": ["CMO"], "company_name": "Thoughtworks",
        "seniorities": [], "max_results": 10}), "m"))

    def _sc(filters, api_key, page=1, per_page=25, strict=False):
        seen_calls.append(1)
        return [{"id": "org1", "name": "Thoughtworks, Ltd.",
                 "primary_domain": "thoughtworks.com"}]

    monkeypatch.setattr(ac, "search_companies", _sc)
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: [])
    # This test is about the PAID resolver's cache, so the free pre-resolve
    # probe is switched off rather than left to short-circuit it.
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer", lambda *a, **k: "answer")
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c.post("/p2/b2b-agents/company-people-intelligence/chat", json={"message": message})


def test_a_second_chat_question_about_the_same_company_is_free(monkeypatch):
    """The realistic case the bug report exercised: someone asks about a
    company, does not get a person, and asks again (or a teammate does) --
    that must not double the cost of a search that already found nobody."""
    seen = []
    r1 = _chat_message(monkeypatch, "CMO of Thoughtworks", seen)
    r2 = _chat_message(monkeypatch, "CTO of Thoughtworks", seen)
    assert r1.status_code == r2.status_code == 200
    assert len(seen) == 1, "the second question must not re-resolve the company"
    body2 = r2.get_json()
    assert not body2.get("credits"), "a cache hit spends nothing"
