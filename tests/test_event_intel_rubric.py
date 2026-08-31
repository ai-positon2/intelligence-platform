"""The gtm-skills conference-recommendation rubric, tested as a contract.

Each test names the rule from the source skill it protects. The rubric is the
part of this agent a future edit is most likely to soften by accident: a
default classification, a padded list, a bonus awarded on the model's say-so.
"""

import inspect

import pytest

from tracker import event_intel_rubric as R


# ── Step 0: classification is declared, never inferred ────────────────────

def test_every_classification_maps_to_a_side_of_the_floor():
    for c in R.CLASSIFICATIONS:
        assert R.orientation_for(c) in (R.ORIENTATION_BOOTH, R.ORIENTATION_AUDIENCE)
        assert c in R.CLASSIFICATION_LABELS
        assert c in R.CLASSIFICATION_WHERE_BUYERS_ARE


def test_b2b_selling_to_marketing_is_booth_driven():
    """The core insight of the skill: at most B2B events every booth is
    staffed by a marketing or sales buyer. Flip this and every sub-score
    measures the opposite crowd."""
    assert R.orientation_for(R.CLASS_B2B_TO_MARKETING) == R.ORIENTATION_BOOTH
    assert R.orientation_for(R.CLASS_B2C_BOOTH_DENSITY) == R.ORIENTATION_BOOTH
    assert R.orientation_for(R.CLASS_B2B_OTHER_FUNCTION) == R.ORIENTATION_AUDIENCE
    assert R.orientation_for(R.CLASS_B2C_GENERAL) == R.ORIENTATION_AUDIENCE


def test_unknown_classification_raises_rather_than_defaulting():
    """A default would silently score the wrong side of the floor, and would
    be invisible in the output."""
    for bad in ("", None, "b2b", "enterprise", "B2B_TO_MARKETING"):
        with pytest.raises(ValueError):
            R.orientation_for(bad)


# ── The rubric arithmetic ─────────────────────────────────────────────────

def test_dimension_weights_are_40_40_20():
    assert R.DIMENSION_MAX[R.DIM_RELEVANCE] == 40
    assert R.DIMENSION_MAX[R.DIM_DM_ACCESS] == 40
    assert R.DIMENSION_MAX[R.DIM_ENGAGEMENT] == 20
    assert sum(R.DIMENSION_MAX.values()) == R.BASE_MAX == 100
    assert R.TOTAL_MAX == 110


def test_subscores_clamp_to_their_dimension_ceiling():
    assert R.clamp_subscore(R.DIM_RELEVANCE, 99) == 40
    assert R.clamp_subscore(R.DIM_ENGAGEMENT, 99) == 20
    assert R.clamp_subscore(R.DIM_DM_ACCESS, -5) == 0


def test_subscores_survive_junk_from_a_model():
    for junk in (None, "", "n/a", [], {}, float("nan")):
        assert R.clamp_subscore(R.DIM_RELEVANCE, junk) == 0
    assert R.clamp_subscore(R.DIM_RELEVANCE, "37") == 37
    assert R.clamp_subscore(R.DIM_RELEVANCE, 36.6) == 37


def test_perfect_score_is_110_not_more():
    s = R.score(40, 40, 20, organizer_run=True,
                matchmaking_evidence="Hosted buyer programme, organizer matches "
                                     "vendors to pre-qualified buyers.")
    assert s["total"] == 110
    assert s["tier"] == R.TIER_P1


def test_tier_boundaries_are_exact():
    assert R.tier_for(80) == R.TIER_P1
    assert R.tier_for(79) == R.TIER_P2
    assert R.tier_for(70) == R.TIER_P2
    assert R.tier_for(69) == R.TIER_P3
    assert R.tier_for(0) == R.TIER_P3


# ── Budget must never move a score ────────────────────────────────────────

def test_score_cannot_see_budget_at_all():
    """The skill: budget is context, never an input to the rubric. Enforced
    structurally rather than by a guard, because a guard can be removed and a
    missing parameter cannot be passed."""
    sig = inspect.signature(R.score)
    names = set(sig.parameters)
    assert not any("budget" in n or "cost" in n or "price" in n for n in names), names
    kinds = {p.kind for p in sig.parameters.values()}
    assert inspect.Parameter.VAR_KEYWORD not in kinds, \
        "**kwargs would let a caller smuggle budget into the rubric"
    assert inspect.Parameter.VAR_POSITIONAL not in kinds


def test_methodology_note_states_cost_is_not_scored():
    note = R.methodology_note(R.CLASS_B2B_TO_MARKETING)
    assert "never an input to a score" in note
    assert "booth" in note.lower()


# ── Step 5: the +10 bonus and its veto list ───────────────────────────────

def test_no_bonus_without_an_organizer_run_claim():
    r = R.matchmaking_bonus(False, "Hosted buyer programme with 1:1 matching.")
    assert r["bonus"] == 0 and r["awarded"] is False


def test_no_bonus_when_the_claim_cites_nothing():
    r = R.matchmaking_bonus(True, "   ")
    assert r["bonus"] == 0
    assert "nothing was cited" in r["reason"]


@pytest.mark.parametrize("app", [
    "Attendees can book meetings in Whova.",
    "Networking via the Brella app.",
    "Swapcard powers the meeting scheduler.",
    "Pre-booking encouraged through the conference app.",
    "There is a networking lounge and a schedule a meeting button.",
])
def test_self_serve_app_booking_never_earns_the_bonus(app):
    """Named explicitly in the skill as NOT qualifying: a baseline expectation
    of any modern event, not a differentiator."""
    r = R.matchmaking_bonus(True, app)
    assert r["bonus"] == 0, app
    assert r["awarded"] is False


@pytest.mark.parametrize("real", [
    "Money20/20 Connect: the organizer pre-schedules 1:1 meetings against stated criteria.",
    "WTM Hosted Buyer programme, account-managed pairing.",
    "Curated 1:1 speed-dating run by the organiser.",
    "AI matching operated by the show, double opt-in.",
])
def test_organizer_run_matchmaking_earns_the_bonus(real):
    r = R.matchmaking_bonus(True, real)
    assert r["bonus"] == R.MATCHMAKING_BONUS, real
    assert r["awarded"] is True


def test_a_real_programme_still_qualifies_when_the_app_is_also_mentioned():
    """Most hosted-buyer shows ALSO ship a Swapcard app. The veto is for
    events where the app is the only thing on offer, not for any mention."""
    r = R.matchmaking_bonus(
        True, "Hosted buyer programme; meetings also visible in Swapcard.")
    assert r["bonus"] == R.MATCHMAKING_BONUS


def test_a_refused_bonus_says_why():
    r = R.matchmaking_bonus(True, "Attendees book their own meetings in Whova.")
    assert r["reason"] and len(r["reason"]) > 20
    assert "whova" in r["reason"].lower()


# ── Step 2: the six categories ────────────────────────────────────────────

def test_all_six_discovery_categories_are_present():
    assert len(R.CATEGORIES) == 6
    for c in R.CATEGORIES:
        assert c in R.CATEGORY_LABELS and c in R.CATEGORY_BRIEF
    assert R.CAT_FREE_VENDOR in R.CATEGORIES
    assert R.CAT_SIDE_EVENT in R.CATEGORIES


def test_free_vendor_category_keeps_the_reason_it_exists():
    assert "under-utilised" in R.CATEGORY_BRIEF[R.CAT_FREE_VENDOR]


def test_shortfall_names_every_category_under_quota():
    short = R.category_shortfall({R.CAT_INDUSTRY_FLAGSHIP: [1, 2],
                                  R.CAT_VERTICAL_SUMMIT: [1]})
    names = {s["category"] for s in short}
    assert R.CAT_INDUSTRY_FLAGSHIP not in names
    assert R.CAT_VERTICAL_SUMMIT in names
    assert R.CAT_FREE_VENDOR in names
    assert len(short) == 5
    v = [s for s in short if s["category"] == R.CAT_VERTICAL_SUMMIT][0]
    assert v["found"] == 1 and v["short_by"] == 1


def test_a_full_sweep_reports_no_shortfall():
    full = {c: [1, 2] for c in R.CATEGORIES}
    assert R.category_shortfall(full) == []


# ── Step 7 / the no-padding rule ──────────────────────────────────────────

def _c(name, total, cat=R.CAT_INDUSTRY_FLAGSHIP):
    return {"name": name, "total": total, "tier": R.tier_for(total), "category": cat}


def test_rank_excludes_everything_below_seventy():
    out = R.rank([_c("A", 85), _c("B", 69), _c("C", 70), _c("D", 12)])
    assert [c["name"] for c in out["kept"]] == ["A", "C"]
    assert {e["name"] for e in out["excluded"]} == {"B", "D"}
    assert out["counts"]["excluded"] == 2


def test_rank_never_pads_toward_the_cap():
    out = R.rank([_c("A", 85), _c("B", 40)], cap=15)
    assert len(out["kept"]) == 1


def test_rank_sorts_descending_and_counts_tiers():
    out = R.rank([_c("mid", 75), _c("top", 92), _c("also", 81)])
    assert [c["name"] for c in out["kept"]] == ["top", "also", "mid"]
    assert out["counts"][R.TIER_P1] == 2
    assert out["counts"][R.TIER_P2] == 1


def test_rank_reports_what_the_cap_dropped_rather_than_truncating_silently():
    out = R.rank([_c("e%d" % i, 90 - i) for i in range(20)], cap=15)
    assert len(out["kept"]) == 15
    assert len(out["over_cap"]) == 5
    assert out["counts"]["over_cap"] == 5


def test_rank_handles_an_empty_input():
    out = R.rank([])
    assert out["kept"] == [] and out["excluded"] == [] and out["counts"]["kept"] == 0


# ── Reporting what could not be measured ──────────────────────────────────

def test_gaps_name_the_unmeasured_fields():
    gaps = R.gaps_for({"name": "X"})
    joined = " ".join(gaps).lower()
    assert "attendance figure" in joined
    assert "official site" in joined
    assert "dates" in joined
    assert len(gaps) >= 6


def test_a_complete_candidate_reports_no_gaps():
    complete = {"attendees": "4,000", "website": "https://x.example",
                "starts_on": "2026-05-01"}
    for d in R.DIMENSIONS:
        complete[d + "_note"] = "reasoned"
    assert R.gaps_for(complete) == []


def test_missing_reasoning_is_itself_a_gap():
    c = {"attendees": "1", "website": "https://x.example", "starts_on": "2026-01-01",
         R.DIM_RELEVANCE + "_note": "yes", R.DIM_DM_ACCESS + "_note": "yes"}
    gaps = R.gaps_for(c)
    assert len(gaps) == 1
    assert "engagement mode" in gaps[0].lower()


@pytest.mark.parametrize("vague", [
    "There is a large networking area and plenty of people to meet.",
    "Lots of senior buyers attend and the floor is easy to work.",
    "The organiser says it is a great place to do business.",
    "Three days of sessions with breaks between them.",
])
def test_vague_evidence_earns_nothing_even_with_the_claim_set(vague):
    """The gap between the veto list and the affirm patterns. Evidence that
    names neither a conference app nor an organizer-run programme describes
    no differentiator, so it earns no differentiator bonus. Without this the
    bonus reduces to the model's own say-so."""
    r = R.matchmaking_bonus(True, vague)
    assert r["bonus"] == 0, vague
    assert r["awarded"] is False
    assert "responsibility for pairing" in r["reason"]
