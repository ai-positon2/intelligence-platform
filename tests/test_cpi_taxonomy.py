"""Tests for the industry vocabulary and the picker built on it.

Apollo's classification is the LinkedIn taxonomy, in which nothing is spelled
"healthcare": it is "hospital & health care", "medical practice",
"pharmaceuticals" and six more. A free-text industry box therefore invited a word
that cannot match, and the picker exists so the real values are visible.

Two properties matter and neither is about any single value:

  1. A picker must never offer something the filter would then reject. Any value
     it suggests has to survive being fed straight back into the matcher,
     otherwise the dropdown teaches people to run searches that match nothing,
     which is the failure it was built to prevent.
  2. Apollo publishes no endpoint listing its industries, so the seed list here is
     a written-down copy and could drift. Values observed on real Apollo records
     are correct by construction, so they are merged over the seed and marked
     confirmed. The tests pin that mechanism rather than the seed's contents.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tracker.apollo_client as ac  # noqa: E402
from tracker import apollo_taxonomy as tax  # noqa: E402


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com",
                               "name": "Test User", "given_name": "Test"}
    return c


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    appmod._CPI_INDUSTRY_SEEN.clear()
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    yield
    appmod._CPI_INDUSTRY_SEEN.clear()


# ── The vocabulary is internally consistent ──────────────────────────────────

def test_every_family_member_is_a_real_apollo_value():
    """A family is a shortcut for a set of Apollo industries. A member that is not
    one of them is a typo that would quietly narrow a search to nothing."""
    seed = set(tax.SEED_INDUSTRIES)
    for family, members in tax.FAMILIES.items():
        for m in members:
            assert m in seed, "%s lists %r, which is not an Apollo industry" % (family, m)


def test_every_alias_points_at_a_real_family():
    for alias, family in tax.ALIASES.items():
        assert family in tax.FAMILIES, "%r aliases the unknown family %r" % (alias, family)


def test_no_family_is_named_after_an_apollo_industry():
    """Found four when this was written: "retail", "real estate", "hospitality" and
    "sports" are all real Apollo values AND were family names, so the picker showed
    two identical-looking rows meaning different things and the broad one shadowed
    the precise one. Families are our invention, so they get names of their own."""
    seed = {tax.norm(v) for v in tax.SEED_INDUSTRIES}
    clashes = [f for f in tax.FAMILIES if tax.norm(f) in seed]
    assert clashes == [], "families named after Apollo industries: %s" % clashes


def test_no_alias_is_itself_an_apollo_industry():
    """Found three: "banking", "farming" and "utilities" are Apollo values that
    were aliased to broad families, so asking for banks returned insurers and
    accountants. That is the same over-broad match this module exists to prevent,
    one level up."""
    seed = {tax.norm(v) for v in tax.SEED_INDUSTRIES}
    clashes = [a for a in tax.ALIASES if tax.norm(a) in seed]
    assert clashes == [], "aliases that are really Apollo industries: %s" % clashes


@pytest.mark.parametrize("apollo_value,should_not_match", [
    ("banking", "insurance"),
    ("farming", "tobacco"),
    ("utilities", "mining & metals"),
    ("retail", "supermarkets"),
    ("hospitality", "restaurants"),
])
def test_typing_an_apollo_value_stays_precise(apollo_value, should_not_match):
    """Asking for exactly what Apollo calls something must not silently widen to
    that industry's neighbours. The broad set is still one keystroke away, under
    the family's own name."""
    kept, _ = ac.filter_by_industry([{"industry": should_not_match}], [apollo_value])
    assert kept == [], "%r must not match %r" % (apollo_value, should_not_match)
    kept, _ = ac.filter_by_industry([{"industry": apollo_value}], [apollo_value])
    assert len(kept) == 1


def test_no_alias_shadows_a_family_name():
    """An alias for a name that is already a family would make one of the two
    unreachable, and which one won would depend on dict ordering."""
    for alias in tax.ALIASES:
        assert alias not in tax.FAMILIES, "%r is both a family and an alias" % alias


def test_the_seed_list_has_no_duplicates_or_stray_formatting():
    """Apollo's values are lowercase and use "&" rather than "and". A seed entry
    that differs in case or spacing still matches (the matcher normalizes) but
    would be offered to a user looking wrong."""
    seen = set()
    for value in tax.SEED_INDUSTRIES:
        assert value == value.strip(), "%r has stray whitespace" % value
        assert value == value.lower(), "%r is not lowercase" % value
        key = tax.norm(value)
        assert key not in seen, "%r duplicates an earlier entry" % value
        seen.add(key)


def test_families_do_not_collapse_into_one_another():
    """Two families covering exactly the same industries would be two names for one
    filter, which makes the picker longer without making it more useful."""
    seen = {}
    for family, members in tax.FAMILIES.items():
        key = frozenset(tax.norm(m) for m in members)
        assert key not in seen, "%s and %s cover the same industries" % (family, seen[key])
        seen[key] = family


# ── The picker cannot suggest something the filter rejects ───────────────────

def test_every_seeded_industry_matches_itself():
    """The whole contract of the dropdown: pick a value, get companies in it."""
    for value in tax.SEED_INDUSTRIES:
        kept, _ = ac.filter_by_industry([{"industry": value}], [value])
        assert len(kept) == 1, "%r does not match a company filed under it" % value


def test_every_family_matches_each_of_its_industries():
    for family, members in tax.FAMILIES.items():
        for m in members:
            kept, _ = ac.filter_by_industry([{"industry": m}], [family])
            assert len(kept) == 1, "%s should match %r" % (family, m)


def test_every_alias_matches_what_its_family_matches():
    for alias, family in tax.ALIASES.items():
        member = tax.FAMILIES[family][0]
        kept, _ = ac.filter_by_industry([{"industry": member}], [alias])
        assert len(kept) == 1, "%r should match %r via %s" % (alias, member, family)


@pytest.mark.parametrize("query", ["", "heal", "soft", "fin", "medical",
                                   "care", "z", "  ", "&", "computer software"])
def test_nothing_the_picker_offers_is_rejected_by_the_filter(query):
    """The property that keeps the two halves honest, over a spread of queries
    including empty, one-letter, punctuation and an exact Apollo value."""
    for entry in tax.suggest(query):
        if entry["kind"] == "family":
            targets = entry["covers"]
        else:
            targets = [entry["value"]]
        for t in targets:
            kept, _ = ac.filter_by_industry([{"industry": t}], [entry["value"]])
            assert len(kept) == 1, \
                "picker offers %r but the filter drops %r" % (entry["value"], t)


# ── Ranking and shape ────────────────────────────────────────────────────────

def test_typing_heal_offers_healthcare_first_then_the_real_values():
    """The reported gap: someone types "heal" and needs to be told what Apollo
    actually calls it."""
    entries = tax.suggest("heal")
    assert entries[0]["value"] == "healthcare"
    assert entries[0]["kind"] == "family"
    values = [e["value"] for e in entries]
    for expected in ("hospital & health care", "mental health care",
                     "health, wellness & fitness"):
        assert expected in values


def test_a_family_is_offered_when_only_its_industries_match_the_query():
    """"pharma" appears nowhere in the word "healthcare", but it is most of
    "pharmaceuticals", which healthcare covers. Matching family names alone would
    hide the shortcut exactly when it is most useful."""
    entries = tax.suggest("pharma")
    assert entries[0]["value"] == "healthcare"
    assert entries[0]["kind"] == "family"


def test_industries_for_prefers_an_exact_value_over_its_substring_matches():
    """"design" is an Apollo industry and also sits inside "graphic design". A term
    that names a value exactly means that value, not everything containing it."""
    assert tax.industries_for("design") == ["design"]


def test_a_family_says_which_industries_it_covers():
    """Otherwise the picker implies Apollo has a value called "healthcare"."""
    entry = [e for e in tax.suggest("healthcare") if e["kind"] == "family"][0]
    assert "hospital & health care" in entry["covers"]
    assert len(entry["covers"]) >= 5


def test_families_rank_above_individual_industries():
    entries = tax.suggest("fin")
    kinds = [e["kind"] for e in entries]
    assert kinds == sorted(kinds, key=lambda k: 0 if k == "family" else 1)


def test_a_prefix_match_ranks_above_a_mid_string_one():
    """"media" is inside three Apollo values and starts only one of them, and the
    one it starts belongs first. Chosen over "health" deliberately: there the
    prefix match happens to sort first alphabetically too, so that query cannot
    tell the ranking apart from no ranking at all."""
    values = [e["value"] for e in tax.suggest("media") if e["kind"] == "industry"]
    assert values[0] == "media production"
    assert values != sorted(values), "this query must not be alphabetical by luck"
    assert "broadcast media" in values and "online media" in values


def test_an_empty_query_offers_the_whole_vocabulary_families_first():
    entries = tax.suggest("", limit=500)
    assert len(entries) >= len(tax.SEED_INDUSTRIES)
    assert entries[0]["kind"] == "family"


def test_a_query_matching_nothing_returns_nothing_rather_than_everything():
    assert tax.suggest("qqzzxx") == []


def test_industries_for_names_what_a_term_resolves_to():
    assert "hospital & health care" in tax.industries_for("healthcare")
    assert "hospital & health care" in tax.industries_for("healthtech")
    assert tax.industries_for("computer software") == ["computer software"]
    assert tax.industries_for("qqzzxx") == []


# ── Learning Apollo's real values ────────────────────────────────────────────

def test_a_value_apollo_uses_but_the_seed_lacks_is_offered_anyway():
    """The seed is a written-down copy of a taxonomy nothing enumerates, so it can
    fall behind. A value seen on a real record is correct by construction."""
    entries = tax.suggest("quantum", learned=["quantum computing"])
    assert [e["value"] for e in entries] == ["quantum computing"]
    assert entries[0]["confirmed"] is True


def test_a_seeded_value_never_seen_is_not_claimed_as_confirmed():
    entry = [e for e in tax.suggest("shipbuilding") if e["kind"] == "industry"][0]
    assert entry["confirmed"] is False


def test_a_learned_value_is_marked_confirmed_without_being_duplicated():
    entries = [e for e in tax.suggest("retail", learned=["retail"])
               if e["value"] == "retail"]
    assert len(entries) == 1
    assert entries[0]["confirmed"] is True


def test_industries_are_learned_from_the_records_apollo_returns():
    appmod._cpi_record_industries([
        {"industry": "Hospital & Health Care"},
        {"industry": "computer software", "industries": ["internet", "quantum tech"]},
        {"industry": None}, {}, "not a dict",
    ])
    seen = appmod._cpi_industries_seen()
    # Lowercased on the way in, so one industry is one entry however Apollo cased it.
    assert "hospital & health care" in seen
    assert "quantum tech" in seen
    assert "" not in seen


def test_a_junk_value_is_not_admitted_to_the_vocabulary():
    """These strings come from a third party and end up in a dropdown."""
    appmod._cpi_record_industries([{"industry": "x" * 400}])
    assert not any(len(v) > 80 for v in appmod._cpi_industries_seen())


def test_learning_is_capped():
    appmod._CPI_INDUSTRY_SEEN.update("industry-%d" % i
                                     for i in range(appmod._CPI_INDUSTRY_SEEN_MAX))
    appmod._cpi_record_industries([{"industry": "one more"}])
    assert "one more" not in appmod._CPI_INDUSTRY_SEEN


# ── The endpoint ─────────────────────────────────────────────────────────────

def test_the_picker_endpoint_answers_a_partial_word(client):
    r = client.get("/p2/b2b-agents/company-people-intelligence/industries?q=heal")
    assert r.status_code == 200
    body = r.get_json()
    assert body["query"] == "heal"
    assert body["entries"][0]["value"] == "healthcare"


def test_the_picker_endpoint_needs_no_apollo_key(client, monkeypatch):
    """It reads a written-down list and a local table, so it must work on an
    environment with no Apollo credentials at all, and cost nothing."""
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    r = client.get("/p2/b2b-agents/company-people-intelligence/industries")
    assert r.status_code == 200
    assert len(r.get_json()["entries"]) > 20


def test_the_picker_endpoint_is_staff_only():
    anon = appmod.app.test_client()
    r = anon.get("/p2/b2b-agents/company-people-intelligence/industries?q=heal")
    assert r.status_code in (301, 302, 401, 403)


def test_an_overlong_query_is_truncated_not_rejected(client):
    r = client.get("/p2/b2b-agents/company-people-intelligence/industries?q="
                   + "a" * 500)
    assert r.status_code == 200
    assert len(r.get_json()["query"]) <= 60
