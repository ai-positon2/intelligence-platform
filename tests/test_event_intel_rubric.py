"""The gtm-skills conference-recommendation rubric, tested as a contract.

Each test names the rule from the source skill it protects. The rubric is the
part of this agent a future edit is most likely to soften by accident: a
default classification, a padded list, a bonus awarded on the model's say-so.
"""

import inspect

import datetime

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


@pytest.mark.parametrize("hollow", [
    "A hosted-buyer style experience is planned for a future edition.",
    "We could not confirm any matchmaking, but the organiser introduces you "
    "informally at the welcome party.",
    "The organiser may introduce you to relevant buyers.",
    "Hosted buyer programme expected to launch next year.",
])
def test_hedged_evidence_earns_nothing(hollow):
    """A hedge is not weak evidence, it is the absence of evidence wearing its
    clothes. Each of these contains a phrase from the affirm list and describes
    nothing a delegate could book at this edition."""
    r = R.matchmaking_bonus(True, hollow)
    assert r["bonus"] == 0, hollow
    assert r["awarded"] is False


@pytest.mark.parametrize("weak_plus_app", [
    "Attendees book their own meetings through the Swapcard app; there is "
    "also a concierge desk.",
    "The event runs a meetings programme: attendees use the conference app "
    "to request 1:1s.",
    "Speed-dating style networking session, self-serve sign-up in Brella.",
])
def test_a_supporting_word_does_not_clear_the_app_veto(weak_plus_app):
    """The hole this closes: any single agreeable word used to override the
    veto, so "speed-dating, self-serve sign-up in Brella" collected the full
    ten points. Ten points is exactly the width of the P2 to P1 band."""
    r = R.matchmaking_bonus(True, weak_plus_app)
    assert r["bonus"] == 0, weak_plus_app
    assert "conference app" in r["reason"]


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
    """Complete means every sub-score too. The earlier version of this fixture
    omitted all three and still expected silence, which is the row the
    never-scored check exists to catch."""
    complete = {"attendees": "4,000", "website": "https://x.example",
                "starts_on": "2026-05-01", "ends_on": "2026-05-03",
                "format": "in_person", "sources": ["https://x.example/expo"]}
    for i, d in enumerate(R.DIMENSIONS):
        complete[d + "_note"] = "reasoned"
        complete[d] = 10 + i
    assert R.gaps_for(complete, today=datetime.date(2026, 1, 1)) == []


def test_missing_reasoning_is_itself_a_gap():
    c = {"attendees": "1", "website": "https://x.example", "starts_on": "2026-01-01",
         "format": "hybrid", "sources": ["https://x.example/expo"],
         R.DIM_RELEVANCE: 30, R.DIM_DM_ACCESS: 30, R.DIM_ENGAGEMENT: 10,
         R.DIM_RELEVANCE + "_note": "yes", R.DIM_DM_ACCESS + "_note": "yes"}
    gaps = R.gaps_for(c, today=datetime.date(2025, 1, 1))
    assert len(gaps) == 1
    assert "engagement mode" in gaps[0].lower()


# ── a dimension nobody scored is not a dimension scored zero ──────────────

def test_a_dimension_the_grader_skipped_is_named_as_unscored():
    """The failure this replaces: a missing dm_access clamped to 0, the event
    totalled 56, fell under the floor, and was reported as judged and found
    wanting. A 40-point dimension nobody looked at is the single most
    consequential thing that can be absent from a row."""
    c = {"attendees": "1", "website": "https://x.example", "starts_on": "2026-01-01",
         R.DIM_RELEVANCE: 38, R.DIM_ENGAGEMENT: 18}
    for d in R.DIMENSIONS:
        c[d + "_note"] = "yes"
    gaps = R.gaps_for(c, today=datetime.date(2025, 1, 1))
    joined = " ".join(gaps).lower()
    assert "decision-maker access was never scored" in joined
    assert "out of 60, not 100" in joined


@pytest.mark.parametrize("raw,expected,readable", [
    (38, 38, True),
    ("38", 38, True),
    ("38/40", 38, True),
    (99, 40, True),
    (-5, 0, True),
    (None, 0, False),
    ("", 0, False),
    ("n/a", 0, False),
])
def test_read_subscore_separates_a_verdict_from_an_absence(raw, expected, readable):
    got, ok = R.read_subscore(R.DIM_RELEVANCE, raw)
    assert got == expected, raw
    assert ok is readable, raw


# ── an edition that is over is not a recommendation ───────────────────────

def test_a_finished_edition_is_reported_as_history():
    c = {"attendees": "1", "website": "https://x.example",
         "starts_on": "2026-03-01", "ends_on": "2026-03-03"}
    for d in R.DIMENSIONS:
        c[d] = 20
        c[d + "_note"] = "yes"
    gaps = R.gaps_for(c, today=datetime.date(2026, 9, 1))
    assert any("already ended" in g for g in gaps), gaps


def test_rank_keeps_a_finished_edition_out_of_the_list():
    """Before this, a conference that ended in 2019 could score 92, tier P1,
    and render under the label "Must-attend. Book it." beside its own past
    date."""
    past = {"name": "Ghost Summit", "total": 92, "tier": R.TIER_P1,
            "starts_on": "2019-03-01", "ends_on": "2019-03-03"}
    live = {"name": "Real Summit", "total": 84, "tier": R.TIER_P1,
            "starts_on": "2026-11-01", "ends_on": "2026-11-03"}
    out = R.rank([past, live], today=datetime.date(2026, 9, 1))
    assert [c["name"] for c in out["kept"]] == ["Real Summit"]
    assert [c["name"] for c in out["finished"]] == ["Ghost Summit"]
    assert out["counts"]["finished"] == 1


def test_an_undated_event_is_never_treated_as_finished():
    """No date is a gap, not a verdict. An annual event whose next edition is
    not announced must not be filed under history."""
    assert R.has_finished({"name": "X"}, today=datetime.date(2026, 9, 1)) is False


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



# ── the methodology paragraph is on every report a client reads ───────────

@pytest.mark.parametrize("cls", R.CLASSIFICATIONS)
def test_the_methodology_paragraph_is_not_lower_cased_mid_sentence(cls):
    """It used to read "so behind the booths. at most b2b events every booth
    is staffed by...": a whole multi-sentence string put through .lower() to
    be reused mid-paragraph."""
    note = R.methodology_note(cls)
    for sentence in note.split(". "):
        s = sentence.strip()
        if s and s[0].isalpha():
            assert s[0].isupper(), "sentence starts lower-case: %r" % s[:60]


@pytest.mark.parametrize("cls", R.CLASSIFICATIONS)
def test_the_methodology_paragraph_keeps_the_segment_name_capitalised(cls):
    """The client's own segment name, printed as "b2b", in the paragraph that
    explains how their money is being allocated."""
    note = R.methodology_note(cls)
    assert "b2b" not in note and "b2c" not in note


@pytest.mark.parametrize("cls", R.CLASSIFICATIONS)
def test_the_methodology_paragraph_has_no_verbless_fragment(cls):
    """"so in the audience." was rendered as a sentence."""
    for sentence in R.methodology_note(cls).split(". "):
        s = sentence.strip().rstrip(".")
        assert not s.lower().startswith("so "), s[:60]
