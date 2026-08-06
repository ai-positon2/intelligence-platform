"""Tests for the Contact Finder company-resolution helpers.

These cover the three bugs that made a real lookup fail in production:
  1. Apollo returning the same company several times, which was shown to the
     user as several identical options to disambiguate between.
  2. Apollo storing stylized names ("Position²") that never compared equal to
     what the user typed ("Position2"), so an exact match was missed and a
     single company was reported as ambiguous.
  3. A disambiguation pick arriving as free text ("Acme (acme.com)"), which was
     then searched as a literal company NAME and found nothing.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


# ── _cpi_norm_name ───────────────────────────────────────────────────────────

def test_norm_name_folds_typographic_characters():
    """Apollo stores "Position²"; the user types "Position2". Without NFKC the
    superscript is dropped entirely ("position" vs "position2") and the two
    never match, which is what caused a single company to look ambiguous."""
    assert appmod._cpi_norm_name("Position2") == appmod._cpi_norm_name("Position²")


def test_norm_name_strips_legal_suffixes():
    assert appmod._cpi_norm_name("Acme Inc") == appmod._cpi_norm_name("Acme")
    assert appmod._cpi_norm_name("The Acme Company") == appmod._cpi_norm_name("Acme")


def test_norm_name_handles_empty_and_none():
    assert appmod._cpi_norm_name("") == ""
    assert appmod._cpi_norm_name(None) == ""


# ── _cpi_clean_company_name ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,name,domain", [
    ("Position2 (position2.com)", "Position2", "position2.com"),
    ("Acme Health (acme.com)", "Acme Health", "acme.com"),
    ("acme.com", "", "acme.com"),
    ("Acme Health", "Acme Health", ""),
    ("", "", ""),
])
def test_clean_company_name_splits_out_domains(raw, name, domain):
    got_name, got_domain = appmod._cpi_clean_company_name(raw)
    assert got_domain == domain
    # A bare domain leaves no usable name behind; everything else keeps its name.
    if name:
        assert got_name == name


# ── _cpi_domain_key ──────────────────────────────────────────────────────────

def test_domain_key_normalizes_scheme_and_www():
    assert appmod._cpi_domain_key({"primary_domain": "https://www.Acme.com/"}) == "acme.com"
    assert appmod._cpi_domain_key({"domain": "ACME.com"}) == "acme.com"
    assert appmod._cpi_domain_key({}) == ""


# ── _cpi_dedup_orgs ──────────────────────────────────────────────────────────

def test_dedup_collapses_repeated_company():
    """The production bug: three identical options offered as a choice."""
    rows = [
        {"id": "a", "name": "Position²", "primary_domain": "position2.com"},
        {"id": "b", "name": "Position²", "primary_domain": "position2.com"},
        {"id": "c", "name": "Position2", "domain": "www.position2.com"},
    ]
    assert len(appmod._cpi_dedup_orgs(rows)) == 1


def test_dedup_keeps_genuinely_different_companies():
    rows = [
        {"id": "a", "name": "Acme Inc", "primary_domain": "acme.com"},
        {"id": "b", "name": "Acme LLC", "primary_domain": "acme.net"},
    ]
    assert len(appmod._cpi_dedup_orgs(rows)) == 2


def test_dedup_falls_back_to_name_when_domain_missing():
    rows = [
        {"id": "a", "name": "Acme Health"},
        {"id": "b", "name": "Acme Health"},
    ]
    assert len(appmod._cpi_dedup_orgs(rows)) == 1


# ── _cpi_resolve_company ─────────────────────────────────────────────────────

@pytest.fixture
def fake_search(monkeypatch):
    """Stub tracker.apollo_client.search_companies and record the filters it got."""
    calls = []
    results = {"rows": []}

    def _fake(filters, api_key, page=1, per_page=25, strict=False):
        calls.append(filters)
        return list(results["rows"])

    import tracker.apollo_client as ac
    monkeypatch.setattr(ac, "search_companies", _fake)
    return calls, results


def test_resolve_dedups_before_asking_user_to_choose(fake_search):
    """Three Apollo rows for one company must resolve, not disambiguate."""
    calls, results = fake_search
    results["rows"] = [
        {"id": "a", "name": "Position²", "primary_domain": "position2.com"},
        {"id": "b", "name": "Position²", "primary_domain": "position2.com"},
        {"id": "c", "name": "Position²", "primary_domain": "position2.com"},
    ]
    org, choices = appmod._cpi_resolve_company("Position2", "key")
    assert choices is None
    assert org and org["id"] == "a"


def test_resolve_matches_across_typographic_name(fake_search):
    """Two distinct companies, one of which is the exact (NFKC-folded) match."""
    calls, results = fake_search
    results["rows"] = [
        {"id": "a", "name": "Position²", "primary_domain": "position2.com"},
        {"id": "b", "name": "Position2 Marketing Partners", "primary_domain": "p2partners.com"},
    ]
    org, choices = appmod._cpi_resolve_company("Position2", "key")
    assert choices is None
    assert org and org["id"] == "a"


def test_resolve_still_disambiguates_genuinely_ambiguous_names(fake_search):
    calls, results = fake_search
    results["rows"] = [
        {"id": "a", "name": "Acme", "primary_domain": "acme.com", "city": "Austin"},
        {"id": "b", "name": "Acme", "primary_domain": "acme.net", "city": "Denver"},
    ]
    org, choices = appmod._cpi_resolve_company("Acme", "key")
    assert org is None
    assert choices and len(choices) == 2
    assert {c["domain"] for c in choices} == {"acme.com", "acme.net"}


def test_resolve_caps_choices_at_five(fake_search):
    calls, results = fake_search
    results["rows"] = [
        {"id": str(i), "name": "Acme", "primary_domain": "acme%d.com" % i} for i in range(9)
    ]
    org, choices = appmod._cpi_resolve_company("Acme", "key")
    assert org is None
    assert len(choices) == 5


def test_resolve_by_domain_skips_disambiguation(fake_search):
    """The disambiguation-pick path: an explicit domain resolves exactly, and
    must query Apollo by domain rather than by name."""
    calls, results = fake_search
    results["rows"] = [
        {"id": "a", "name": "Position²", "primary_domain": "position2.com"},
    ]
    org, choices = appmod._cpi_resolve_company("", "key", domain="position2.com")
    assert choices is None
    assert org and org["id"] == "a"
    assert calls[0].get("domains") == ["position2.com"]


def test_resolve_strips_domain_out_of_name(fake_search):
    """"Position2 (position2.com)" must not be searched as a literal name."""
    calls, results = fake_search
    results["rows"] = [
        {"id": "a", "name": "Position²", "primary_domain": "position2.com"},
    ]
    org, choices = appmod._cpi_resolve_company("Position2 (position2.com)", "key")
    assert choices is None
    assert org and org["id"] == "a"
    assert calls[0].get("domains") == ["position2.com"]
    assert "q_organization_name" not in calls[0]


def test_resolve_domain_miss_falls_back_to_name(fake_search):
    """A domain Apollo does not index should not dead-end when a name is also
    available: the name search is still tried."""
    calls, results = fake_search

    import tracker.apollo_client as ac

    def _staged(filters, api_key, page=1, per_page=25, strict=False):
        calls.append(filters)
        if filters.get("domains"):
            return []
        return [{"id": "a", "name": "Acme Health", "primary_domain": "acme.com"}]

    ac.search_companies = _staged
    org, choices = appmod._cpi_resolve_company("Acme Health", "key", domain="unknown-xyz.com")
    assert choices is None
    assert org and org["id"] == "a"
    assert len(calls) == 2


def test_resolve_returns_nothing_when_apollo_has_nothing(fake_search):
    calls, results = fake_search
    results["rows"] = []
    org, choices = appmod._cpi_resolve_company("NoSuchCompanyXyz", "key")
    assert org is None
    assert choices is None


def test_resolve_domain_branch_rejects_a_non_matching_hit(fake_search):
    """q_organization_domains_list is a fuzzy search input, not a strict filter.
    A neighbouring company must never be returned as the requested domain."""
    calls, results = fake_search
    results["rows"] = [{"id": "other", "name": "Unrelated Co", "primary_domain": "somethingelse.com"}]
    org, choices = appmod._cpi_resolve_company("", "key", domain="acme.com")
    assert org is None and choices is None


def test_resolve_empty_normalized_name_is_not_an_exact_match(fake_search):
    """"The Company Group" normalizes to "", which must not compare equal to
    every other empty-normalizing candidate and pick one at random."""
    calls, results = fake_search
    results["rows"] = [
        {"id": "a", "name": "The Group", "primary_domain": "a.com"},
        {"id": "b", "name": "Company Holdings", "primary_domain": "b.com"},
    ]
    org, choices = appmod._cpi_resolve_company("The Company Group", "key")
    assert org is None
    assert choices and len(choices) == 2


def test_choices_carry_the_apollo_org_id(fake_search):
    """The org id rides along so a pick needs no second lookup and a candidate
    with no domain is still selectable."""
    calls, results = fake_search
    results["rows"] = [
        {"id": "org-a", "name": "Acme", "primary_domain": "acme.com"},
        {"id": "org-b", "name": "Acme"},
    ]
    org, choices = appmod._cpi_resolve_company("Acme", "key")
    assert org is None
    assert {c["id"] for c in choices} == {"org-a", "org-b"}


# ── _cpi_title_matches ───────────────────────────────────────────────────────

# ── _cpi_reveal_names ────────────────────────────────────────────────────────

@pytest.fixture
def no_postgres(monkeypatch):
    """Postgres unavailable -- the id-keyed name-reveal cache must degrade to
    always calling Apollo rather than erroring."""
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)


def test_reveal_names_merges_real_name_without_leaking_contact_fields(no_postgres, monkeypatch):
    """The production bug: search_people's masked "Sanjeev" must become
    "Sanjeev Dhanaraj" in a chat answer, but revealing a name must not smuggle
    an email/phone into an answer the user never asked for contact info on."""
    import tracker.apollo_client as ac

    def _fake_bulk(ids, api_key):
        assert ids == ["p1"]
        return {"p1": {"id": "p1", "first_name": "Sanjeev", "last_name": "Dhanaraj",
                       "title": "Vice President Marketing",
                       "linkedin_url": "https://linkedin.com/in/sanjeev",
                       "email": "sanjeev@position2.com"}}
    monkeypatch.setattr(ac, "bulk_match_people", _fake_bulk)

    people = [{"id": "p1", "full_name": "Sanjeev", "title": "VP Marketing"}]
    out = appmod._cpi_reveal_names(people, "key")

    assert out[0]["full_name"] == "Sanjeev Dhanaraj"
    assert out[0]["title"] == "Vice President Marketing"
    assert out[0]["linkedin_url"] == "https://linkedin.com/in/sanjeev"
    assert "emails" not in out[0] and "phones" not in out[0] and "email" not in out[0]


def test_reveal_names_keeps_original_when_apollo_has_no_match(no_postgres, monkeypatch):
    import tracker.apollo_client as ac
    monkeypatch.setattr(ac, "bulk_match_people", lambda ids, api_key: {})
    people = [{"id": "p1", "full_name": "Sanjeev", "title": "VP Marketing"}]
    out = appmod._cpi_reveal_names(people, "key")
    assert out[0]["full_name"] == "Sanjeev"
    assert out[0]["title"] == "VP Marketing"


def test_reveal_names_keeps_original_when_bulk_match_raises(no_postgres, monkeypatch):
    """Apollo unreachable during the reveal call must not blank out or crash on
    a person the caller already has a real search hit for."""
    import tracker.apollo_client as ac
    def _boom(ids, api_key):
        raise RuntimeError("apollo down")
    monkeypatch.setattr(ac, "bulk_match_people", _boom)
    people = [{"id": "p1", "full_name": "Sanjeev"}]
    out = appmod._cpi_reveal_names(people, "key")
    assert out[0]["full_name"] == "Sanjeev"


def test_reveal_names_skips_people_with_no_apollo_id(no_postgres):
    people = [{"full_name": "No Id Here"}]
    out = appmod._cpi_reveal_names(people, "key")
    assert out == people


def test_reveal_names_noop_without_api_key(no_postgres):
    people = [{"id": "p1", "full_name": "Sanjeev"}]
    out = appmod._cpi_reveal_names(people, "")
    assert out == people


@pytest.mark.parametrize("actual,requested,expected", [
    # Abbreviation and full form are the same role.
    ("Chief Marketing Officer", ["CMO"], True),
    ("CMO", ["Chief Marketing Officer"], True),
    ("Chief Marketing Officer (CMO)", ["CMO"], True),
    ("Global CMO", ["Chief Marketing Officer"], True),
    # The production trap: Apollo's loose title search returns a near miss that
    # must NOT be presentable as the requested role.
    ("Marketing Manager", ["CMO"], False),
    ("VP of Marketing", ["Chief Marketing Officer"], False),
    ("Marketing Coordinator", ["CMO"], False),
    ("Chief Financial Officer", ["CMO"], False),
    # VP forms.
    ("Vice President of Sales", ["VP of Sales"], True),
    ("VP Sales", ["Vice President Sales"], True),
    ("Sales Manager", ["VP of Sales"], False),
    # Degenerate input.
    ("", ["CMO"], False),
    ("Chief Marketing Officer", [], False),
])
def test_title_matches(actual, requested, expected):
    assert appmod._cpi_title_matches(actual, requested) is expected
