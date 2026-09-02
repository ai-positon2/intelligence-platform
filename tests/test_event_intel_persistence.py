"""The persistence layer's pure half: intake validation and candidate shaping.

These run without a database. Every function here is the one that decides
whether a row is trustworthy before it is ever written, so the tests are about
refusals and recomputation rather than about SQL.
"""

import datetime

import pytest

from tracker import event_intel_rubric as R
from tracker import event_intel_store as S


# ── the locked profile ────────────────────────────────────────────────────

def _intake(**over):
    base = {"client_name": "Northwind", "classification": R.CLASS_B2B_TO_MARKETING,
            "buyer_roles": "VP Marketing, CMO", "verticals": "fintech",
            "geo_scope": "North America"}
    base.update(over)
    return base


def test_a_valid_intake_derives_its_orientation():
    p = S.normalise_profile(_intake())
    assert p["orientation"] == R.ORIENTATION_BOOTH
    assert p["classification"] == R.CLASS_B2B_TO_MARKETING
    assert p["client_name"] == "Northwind"


def test_orientation_follows_the_classification_not_the_payload():
    """A caller cannot hand-set which crowd gets scored. It is derived, every
    time, from the one field the user actually chose."""
    p = S.normalise_profile(_intake(classification=R.CLASS_B2B_OTHER_FUNCTION,
                                    orientation=R.ORIENTATION_BOOTH))
    assert p["orientation"] == R.ORIENTATION_AUDIENCE


@pytest.mark.parametrize("bad", ["", None, "b2b", "enterprise saas"])
def test_intake_without_a_usable_classification_is_refused(bad):
    with pytest.raises(ValueError):
        S.normalise_profile(_intake(classification=bad))


def test_intake_without_a_client_name_is_refused():
    with pytest.raises(ValueError) as e:
        S.normalise_profile(_intake(client_name="   "))
    assert "too generic" in str(e.value)


def test_window_and_cap_are_clamped_to_sane_bounds():
    p = S.normalise_profile(_intake(window_months=900, max_events=999))
    assert p["window_months"] == 36
    assert p["max_events"] == 25
    p2 = S.normalise_profile(_intake(window_months=0, max_events=0))
    assert p2["window_months"] == 1 and p2["max_events"] == 1


def test_defaults_match_the_source_skill():
    p = S.normalise_profile(_intake())
    assert p["window_months"] == 12
    assert p["max_events"] == R.DEFAULT_CAP == 15


def test_junk_numbers_fall_back_to_the_default():
    p = S.normalise_profile(_intake(window_months="soon", max_events=None))
    assert p["window_months"] == 12 and p["max_events"] == 15


def test_budget_is_recorded_on_the_profile_but_is_not_a_scoring_field():
    p = S.normalise_profile(_intake(budget_note="about $40k for the year"))
    assert p["budget_note"] == "about $40k for the year"
    assert not any(k in R.DIMENSIONS for k in p)


def test_long_free_text_is_capped_rather_than_stored_whole():
    p = S.normalise_profile(_intake(buyer_roles="x" * 5000,
                                    force_exclude="y" * 9000))
    assert len(p["buyer_roles"]) == 400
    assert len(p["force_exclude"]) == 4000


# ── candidate shaping ─────────────────────────────────────────────────────

def _soon(days: int = 90) -> str:
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _cand(**over):
    base = {"name": "PMM Summit", "category": R.CAT_VERTICAL_SUMMIT,
            "relevance": 34, "dm_access": 33, "engagement": 16,
            "relevance_note": "n", "dm_access_note": "n", "engagement_note": "n",
            "website": "https://pmm.example",
            # Relative to the clock, not a literal. A hardcoded date silently
            # becomes a past date, and a past date is now a gap, so this
            # fixture would have started failing on its own.
            "starts_on": _soon(), "ends_on": _soon(93),
            "attendees": "600"}
    base.update(over)
    return base


def test_candidate_fields_and_normaliser_output_agree_exactly():
    """A field present in one and absent from the other is a KeyError at
    insert time, on a background thread, where it surfaces as a run that
    silently stores nothing."""
    got = set(S.normalise_candidate(_cand()))
    assert got == set(S._CANDIDATE_FIELDS)


def test_the_total_is_recomputed_and_a_supplied_one_is_ignored():
    """A model that scores 34/33/16 and then writes 'Total: 95' produces a row
    whose headline contradicts its own breakdown, and the headline is what
    people read."""
    c = S.normalise_candidate(_cand(total=95, tier="P1"))
    assert c["total"] == 34 + 33 + 16 == 83
    assert c["tier"] == R.TIER_P1


def test_sub_scores_are_clamped_before_they_are_totalled():
    c = S.normalise_candidate(_cand(relevance=99, dm_access=99, engagement=99))
    assert (c["relevance"], c["dm_access"], c["engagement"]) == (40, 40, 20)
    assert c["total"] == 100


def test_a_supplied_tier_cannot_promote_a_row_its_sub_scores_do_not_earn():
    """The mirror of the total: a model that scores 10/10/5 and labels the row
    P1 has written a row whose badge contradicts its own bars."""
    c = S.normalise_candidate(_cand(relevance=10, dm_access=10, engagement=5,
                                    tier="P1", total=91))
    assert c["tier"] == R.TIER_P3
    assert c["total"] == 25


def test_a_low_scorer_is_stored_and_tiered_p3_rather_than_dropped():
    """Exclusion happens at ranking time, and the excluded list is rendered.
    Dropping it here would make 'nothing else was found' and 'six more were
    found and none cleared the bar' look identical."""
    c = S.normalise_candidate(_cand(relevance=10, dm_access=10, engagement=5))
    assert c["total"] == 25 and c["tier"] == R.TIER_P3


def test_matchmaking_bonus_is_regated_at_write_time():
    app = S.normalise_candidate(_cand(organizer_run=True,
                                      matchmaking_evidence="Book meetings in Whova."))
    assert app["matchmaking"] == 0
    assert "whova" in app["matchmaking_reason"].lower()
    real = S.normalise_candidate(
        _cand(organizer_run=True,
              matchmaking_evidence="Hosted buyer programme run by the organiser."))
    assert real["matchmaking"] == 10
    assert real["total"] == 83 + 10


@pytest.mark.parametrize("bad", [{}, {"name": "X"}, {"name": "X", "category": "made_up"},
                                 {"category": R.CAT_EMERGING}])
def test_a_candidate_with_no_name_or_an_unknown_category_is_dropped(bad):
    assert S.normalise_candidate(bad) is None


def test_every_discovery_category_is_accepted():
    for cat in R.CATEGORIES:
        assert S.normalise_candidate(_cand(category=cat))["category"] == cat


def test_a_non_http_website_is_discarded_not_stored():
    assert S.normalise_candidate(_cand(website="javascript:alert(1)"))["website"] is None
    assert S.normalise_candidate(_cand(website="pmm.example"))["website"] is None
    assert S.normalise_candidate(_cand())["website"] == "https://pmm.example"


def test_non_http_sources_are_filtered_out():
    c = S.normalise_candidate(_cand(sources=["https://a.example", "javascript:x",
                                             "ftp://b", 42, None]))
    assert c["sources"] == ["https://a.example"]


def test_attendance_is_kept_as_the_event_published_it():
    """'12,000+' and 12000 are different claims. Coercing to an integer invents
    a precision the event never published."""
    c = S.normalise_candidate(_cand(attendees="12,000+ expected"))
    assert c["attendees"] == "12,000+ expected"


def test_gaps_are_recorded_on_every_candidate():
    thin = S.normalise_candidate({"name": "Thin", "category": R.CAT_EMERGING})
    assert thin["gaps"], "a candidate with nothing measured must say so"
    full = S.normalise_candidate(_cand())
    assert full["gaps"] == []


@pytest.mark.parametrize("raw,kept,quarter", [
    ("2026-11-04", "2026-11-04", None),
    ("2026-11-04T00:00:00Z", "2026-11-04", None),
    ("Q2 2026", None, "Q2 2026"),
    ("TBD", None, "TBD"),
    ("2026-13-45", None, "2026-13-45"),
    ("04/11/2026", None, "04/11/2026"),
])
def test_a_date_that_will_not_parse_is_kept_as_text_not_forced_into_a_date(
        raw, kept, quarter):
    """These land in a DATE column. Postgres answers "Q2 2026" by aborting the
    statement, which in a batch insert used to cost every other event in the
    run. The reader still needs the answer, so it is kept as the quarter."""
    c = S.normalise_candidate(_cand(starts_on=raw, ends_on=None))
    assert c["starts_on"] == kept, raw
    assert c["quarter"] == quarter, raw


def test_an_end_before_its_start_drops_the_end_not_the_start():
    c = S.normalise_candidate(_cand(starts_on="2027-05-10", ends_on="2027-05-02"))
    assert c["starts_on"] == "2027-05-10"
    assert c["ends_on"] is None


def test_cost_note_rides_along_without_touching_the_score():
    a = S.normalise_candidate(_cand())
    b = S.normalise_candidate(_cand(cost_note="$28,000 for a 3x3 booth"))
    assert a["total"] == b["total"]
    assert b["cost_note"].startswith("$28,000")


def test_the_store_degrades_to_empty_without_a_database():
    """DATABASE_URL is unset under test, so every read path must return a safe
    empty rather than raise into a request handler."""
    assert S.get_candidates(1) == []
    assert S.list_profiles("a@b.c") == []
    assert S.get_profile(1, "a@b.c") is None
    assert S.prior_candidate_names("a@b.c") == []
    assert S.save_candidates(1, [_cand()]) == 0
