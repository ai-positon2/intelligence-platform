"""The scoring domain for Event & Conference Intelligence.

This module is a direct implementation of the gtm-skills
`conference-recommendation` rubric (dave-engel), with the parts that skill
states as instructions-to-a-human turned into things the code will not let
you get wrong. It is pure: no I/O, no model calls, no imports from the rest
of the package, so every rule below is unit-testable in isolation.

The rubric, verbatim from the source skill:

    Relevance                     /40   how closely composition matches ICP
    Decision-maker accessibility  /40   density of buyers AND structural reach
    Engagement mode               /20   buying mindset vs learning mindset
    + organizer-run matchmaking   +10   bonus, total out of 110

    P1  >= 80   must-attend
    P2  70-79   strong
    P3  <  70   EXCLUDED from the ranked list, no padding

Four rules from that skill are enforced here rather than requested:

1. **Budget can never move a score.** `score()` takes three sub-scores and a
   matchmaking claim. It has no budget parameter and no `**kwargs`, so budget
   cannot reach it even by accident. Cost travels beside a candidate as
   context and is rendered next to the score, never inside it.

2. **The +10 needs evidence, and self-serve app booking is not evidence.**
   The skill warns twice about over-applying this bonus. Here the bonus
   requires BOTH a model-asserted `organizer_run` flag AND evidence text that
   survives a veto list of the exact things the skill names as
   disqualifying (Whova, Brella, Swapcard, networking lounges, "pre-booking
   encouraged"). Either gate alone is bypassable by an agreeable model; both
   together are not.

3. **Where the buyers stand is declared, never inferred.** `orientation_for()`
   is a total function over the four classifications the skill lists, and it
   raises on anything else. The booth-driven insight, that at most B2B events
   every booth is staffed by a marketing or sales buyer, is the reason this
   exists: get it wrong and all three sub-scores measure the opposite side of
   the floor.

4. **No padding, and say what was dropped.** `rank()` returns the kept list
   AND the count and names of everything it excluded. A list silently
   truncated at 70 looks identical to a list where nothing scored below 70.
   Same defect the source ledger exists to prevent on the roster side.

Everything returned carries a `gaps` list: the things that could NOT be
measured for this candidate. A score computed from three confident sub-scores
and a score computed from three guesses are indistinguishable without it.
"""

from __future__ import annotations

import datetime
import re

# ── Step 0: classification, and which side of the floor it points at ──────
#
# The four rows of the skill's Step 0 table. Nothing infers these; the user
# picks one and it is stored on the profile as the run's audit trail.

CLASS_B2C_GENERAL = "b2c_general"
CLASS_B2C_BOOTH_DENSITY = "b2c_booth_density"
CLASS_B2B_TO_MARKETING = "b2b_to_marketing"
CLASS_B2B_OTHER_FUNCTION = "b2b_other_function"

CLASSIFICATIONS = (CLASS_B2C_GENERAL, CLASS_B2C_BOOTH_DENSITY,
                   CLASS_B2B_TO_MARKETING, CLASS_B2B_OTHER_FUNCTION)

ORIENTATION_BOOTH = "booth"
ORIENTATION_AUDIENCE = "audience"

_ORIENTATION = {
    CLASS_B2C_GENERAL: ORIENTATION_AUDIENCE,
    CLASS_B2C_BOOTH_DENSITY: ORIENTATION_BOOTH,
    CLASS_B2B_TO_MARKETING: ORIENTATION_BOOTH,
    CLASS_B2B_OTHER_FUNCTION: ORIENTATION_AUDIENCE,
}

CLASSIFICATION_LABELS = {
    CLASS_B2C_GENERAL: "B2C, selling to consumers",
    CLASS_B2C_BOOTH_DENSITY: "B2C brand selling to the other exhibitors",
    CLASS_B2B_TO_MARKETING: "B2B, selling to marketing, growth or sales",
    CLASS_B2B_OTHER_FUNCTION: "B2B, selling to a non-marketing function",
}

# The same fact as CLASSIFICATION_WHERE_BUYERS_ARE, as a lower-case noun
# phrase that can be dropped into the middle of a sentence. The capitalised
# version above is a sentence, and lower-casing a sentence to reuse it mid-
# paragraph is what produced "so behind the booths. at most b2b events" on
# every report this agent has ever printed.
CLASSIFICATION_BUYER_PLACE = {
    CLASS_B2C_GENERAL: "in the audience",
    CLASS_B2C_BOOTH_DENSITY: "at the exhibitor booths, among the other brands",
    CLASS_B2B_TO_MARKETING: "behind the booths",
    CLASS_B2B_OTHER_FUNCTION: "in the audience and the session tracks",
}

# The reason, where there is one worth stating. Kept whole, with its own
# capitals, because it is a sentence and gets rendered as one.
CLASSIFICATION_WHY = {
    CLASS_B2B_TO_MARKETING: (
        "At most B2B events every booth is staffed by a marketing or sales "
        "buyer, which is the whole reason a booth-to-booth motion works."),
    CLASS_B2C_BOOTH_DENSITY: (
        "The people who can sign are working the floor, not walking it."),
}

# Shown in the report so a reader can see which crowd was scored, and why.
CLASSIFICATION_WHERE_BUYERS_ARE = {
    CLASS_B2C_GENERAL: "In the audience.",
    CLASS_B2C_BOOTH_DENSITY: "At the exhibitor booths.",
    CLASS_B2B_TO_MARKETING: (
        "Behind the booths. At most B2B events every booth is staffed by a "
        "marketing or sales buyer, which is the whole reason a booth-to-booth "
        "motion works."),
    CLASS_B2B_OTHER_FUNCTION: "In the audience and the session tracks.",
}


def orientation_for(classification: str) -> str:
    """Which side of the floor every sub-score for this client measures.

    Raises on an unknown classification rather than defaulting. A default here
    would silently score the wrong crowd, which is the failure the skill's
    "never infer it" instruction exists to prevent, and it would be invisible
    in the output.
    """
    try:
        return _ORIENTATION[classification]
    except KeyError:
        raise ValueError(
            "Unknown classification %r. It must be one of: %s. This is never "
            "inferred: it decides which side of the floor every score measures."
            % (classification, ", ".join(CLASSIFICATIONS)))


# ── The three scored dimensions ───────────────────────────────────────────

DIM_RELEVANCE = "relevance"
DIM_DM_ACCESS = "dm_access"
DIM_ENGAGEMENT = "engagement"

DIMENSIONS = (DIM_RELEVANCE, DIM_DM_ACCESS, DIM_ENGAGEMENT)

DIMENSION_MAX = {DIM_RELEVANCE: 40, DIM_DM_ACCESS: 40, DIM_ENGAGEMENT: 20}

DIMENSION_LABELS = {
    DIM_RELEVANCE: "Relevance",
    DIM_DM_ACCESS: "Decision-maker access",
    DIM_ENGAGEMENT: "Engagement mode",
}

DIMENSION_MEANING = {
    DIM_RELEVANCE: "How closely the composition matches this client's ICP.",
    DIM_DM_ACCESS: ("Density of actual buyers, and the structural reach to "
                    "them: floor layout, meeting infrastructure, side events."),
    DIM_ENGAGEMENT: ("Are these people in a vendor-buying mindset, or is this "
                     "a learning and keynote crowd?"),
}

BASE_MAX = 100
MATCHMAKING_BONUS = 10
TOTAL_MAX = BASE_MAX + MATCHMAKING_BONUS

# ── Tiers ─────────────────────────────────────────────────────────────────

TIER_P1 = "P1"
TIER_P2 = "P2"
TIER_P3 = "P3"

TIER_MIN = {TIER_P1: 80, TIER_P2: 70}
TIER_LABELS = {
    TIER_P1: "Must-attend. Book it.",
    TIER_P2: "Strong. Attend if budget and calendar allow.",
    TIER_P3: "Below the priority bar. Shown as an option when the audience "
             "is genuinely this client's.",
}

# The priority bar. Above it an event is RECOMMENDED.
#
# This used to be the one line that decided whether a candidate was shown at
# all, and that conflated two different questions into one number:
#
#   1. Is this event's audience genuinely this client's? A fact about fit.
#   2. Is it a must-attend? A ranking judgement about priority.
#
# Only the second is a 70-out-of-100 question. The first is what keeps
# irrelevant events off the list, and it has its own measured dimension
# (`relevance`, 0 to 40) which was being ignored in favour of the total.
#
# The cost of conflating them was measured on real clients: a run for one
# client returned a single event and another returned none, while events that
# were real, upcoming, cited and squarely aimed at the client's own buyers sat
# in the discard pile because they were learning-crowd conferences rather than
# buying-floor ones. `engagement` is only 20 points and `dm_access` is
# structurally harsh for a conference, so a genuinely well-matched event lands
# in the sixties and a bar at 70 cuts it. The scoring prompt then compounds it
# by telling the grader "most events are mediocre for most clients", which
# pushes the whole distribution down while this threshold stayed put: the
# calibration instruction and the threshold were set independently and pull
# against each other.
#
# So the bar still decides what is RECOMMENDED, and no longer decides what
# EXISTS. Below it, the two gates below decide between a real option and a
# genuine miss.
RANK_FLOOR = TIER_MIN[TIER_P2]

# The two gates that separate "below the bar but worth your time" from
# "not for you". A candidate must clear BOTH to be shown as an option.
#
# RELEVANCE_GATE is on the `relevance` sub-score, which means precisely "how
# closely the composition of this event matches the client's ICP". 24 of 40 is
# a clear majority of the audience being the client's own buyers. It is the
# gate that answers "is this event actually for them", and it is why widening
# the list does not mean padding it with anything that was found.
#
# CONSIDER_FLOOR is on the total, and it is what stops the right audience at
# an unworkable event from being offered. Half marks overall. An event can be
# aimed exactly at the client's buyers and still be a keynote hall nobody can
# be reached in; that scores relevance well and everything else badly, and it
# is not an option.
#
# Neither gate pads. Nothing is topped up toward a target count, and an event
# below either gate is still reported, in the same discard bucket as before.
RELEVANCE_GATE = 24
CONSIDER_FLOOR = 50

DEFAULT_CAP = 15

# ── A client's own history, as a visible order signal (never a score input) ─
#
# `evi_outcomes` records what a client actually did with a past
# recommendation (went, going, skipped). Aggregated by discovery category and
# event format, a strong pattern becomes a small, reasoned nudge on which
# FUTURE candidates come first among their own bucket-mates -- never a reason
# to exclude one, and never a fact this rubric's own `score()` can see: that
# function is deliberately closed (no **kwargs) so nothing outside the three
# sub-scores and the matchmaking claim can move it, and client history is no
# more a fact about an event than budget is. See outcome_adjustment() below
# and event_intel_report.apply_outcome_pattern(), which applies it strictly
# after rank() has already decided bucket membership and the cap.
#
# OUTCOME_MIN_SAMPLE: a 2-of-2 streak is a coincidence dressed as a
# preference -- if the true rate were even odds, 2-of-2 has a 1-in-4 chance of
# happening anyway. At 3, a unanimous 3-of-3 has 1-in-8, the same order of
# strictness RELEVANCE_GATE and CONSIDER_FLOOR apply above.
OUTCOME_MIN_SAMPLE = 3

# The rate a pattern must clear, once OUTCOME_MIN_SAMPLE is met, before it is
# allowed to speak. 0.75 means "3 of 4 or better" or "3 of 3"; 2 of 3 (67%)
# does not clear it, because one flipped decision away from a coin flip is
# not yet a pattern this tool acts on, even quietly and even to reorder.
OUTCOME_SIGNAL_RATE = 0.75

# Half of MATCHMAKING_BONUS. That bonus is awarded for a fact ABOUT THE EVENT
# an auditor can check against a citation; this is inferred from the
# CLIENT'S OWN past behaviour toward a category or format, a full step
# removed from evidence about this specific event, so it is capped at half
# the strongest evidence-based bonus in this rubric.
OUTCOME_ADJUSTMENT = 5


def tier_for(total: int) -> str:
    if total >= TIER_MIN[TIER_P1]:
        return TIER_P1
    if total >= TIER_MIN[TIER_P2]:
        return TIER_P2
    return TIER_P3


# ── Step 2: the six discovery categories ──────────────────────────────────
#
# The skill names pattern-matching to famous conferences as "the single
# biggest failure mode". Its countermeasure is breadth: at least two
# candidates from each of six categories, found by active search rather than
# recall. Holding them as data here is what lets the discovery stage run one
# search PER CATEGORY with its own quota, instead of one search that a model
# answers entirely from category 1.

CAT_INDUSTRY_FLAGSHIP = "industry_flagship"
CAT_VERTICAL_SUMMIT = "vertical_summit"
CAT_REGIONAL_FLAGSHIP = "regional_flagship"
CAT_FREE_VENDOR = "free_vendor"
CAT_EMERGING = "emerging"
CAT_SIDE_EVENT = "side_event"

CATEGORIES = (CAT_INDUSTRY_FLAGSHIP, CAT_VERTICAL_SUMMIT, CAT_REGIONAL_FLAGSHIP,
              CAT_FREE_VENDOR, CAT_EMERGING, CAT_SIDE_EVENT)

CATEGORY_QUOTA = 2

CATEGORY_LABELS = {
    CAT_INDUSTRY_FLAGSHIP: "Industry flagship",
    CAT_VERTICAL_SUMMIT: "Vertical summit",
    CAT_REGIONAL_FLAGSHIP: "Regional flagship",
    CAT_FREE_VENDOR: "Free sponsor-funded event",
    CAT_EMERGING: "Emerging event",
    CAT_SIDE_EVENT: "Side event",
}

CATEGORY_BRIEF = {
    CAT_INDUSTRY_FLAGSHIP:
        "The obvious big name in the sector. Often right, not always.",
    CAT_VERTICAL_SUMMIT:
        ("Narrower events dense with the exact buyer role. Frequently higher "
         "buyer density than the flagship."),
    CAT_REGIONAL_FLAGSHIP:
        ("The leading event in a target geography. Often beats a global on "
         "buyer-density-per-dollar for a geographically concentrated client."),
    CAT_FREE_VENDOR:
        ("Events that are free or near-free to attend because a large player "
         "in this client's market pays for them, so the audience turns up for "
         "the content rather than for a ticket they bought. Dense with the "
         "client's buyers and routinely overlooked, because nobody markets an "
         "event they are giving away. WHAT THIS LOOKS LIKE DEPENDS ENTIRELY "
         "ON THE MARKET, and searching for the wrong market's version of it "
         "is how this category comes back empty when it is not: in B2B "
         "software it is the one-day AWS, Snowflake, HubSpot, Salesforce, "
         "Databricks or ServiceNow city circuit; in healthcare and consumer "
         "markets it is manufacturer, hospital, charity and patient-"
         "association days, free expo halls, and community or education "
         "events run by the big names in the category. Find this client's "
         "version. Do not search for another market's."),
    CAT_EMERGING:
        ("Years one to three. Not yet diluted by tourist attendees, often "
         "higher density per head."),
    CAT_SIDE_EVENT:
        ("Dinners, breakfasts, executive meetups and after-parties around the "
         "majors. Frequently denser than the main floor, and invisible to "
         "model recall, so they have to be searched for."),
}


def category_shortfall(by_category: dict) -> list[dict]:
    """Which categories came back under quota, and by how much.

    The skill says a genuinely empty category must be documented rather than
    silently dropped. This produces the list the report renders, so "no free
    vendor conferences serve this niche" is stated rather than looking
    identical to "we never searched for one".
    """
    out = []
    for cat in CATEGORIES:
        found = len(by_category.get(cat) or [])
        if found < CATEGORY_QUOTA:
            out.append({"category": cat, "label": CATEGORY_LABELS[cat],
                        "found": found, "quota": CATEGORY_QUOTA,
                        "short_by": CATEGORY_QUOTA - found})
    return out


# ── Step 5: the +10 matchmaking bonus, and its veto list ──────────────────
#
# The skill's test: "does the organizer promise to pair you with buyers
# matching stated criteria?" Self-serve booking in a conference app is a
# baseline expectation of any modern event, not a differentiator, and the
# skill calls out over-applying this bonus as a named failure.

_MATCHMAKING_VETO = (
    "whova", "brella", "swapcard", "grip app", "conference app",
    "event app", "networking lounge", "networking app",
    "schedule a meeting button", "self-serve", "self serve",
    "pre-booking encouraged", "attendees can book", "attendees are encouraged",
    "meeting scheduler in the app", "app-based networking",
)

# Evidence that describes no programme that exists YET. A hedge is not weak
# evidence, it is the absence of evidence wearing its clothes, so it refuses the
# bonus outright before either list below is consulted. "A hosted-buyer
# experience is planned for a future edition" contains the strongest phrase on
# the affirm list and describes something nobody can book this year.
_MATCHMAKING_HEDGE = tuple(re.compile(p, re.I) for p in (
    r"could\s+not\s+(be\s+)?confirm",
    r"couldn'?t\s+confirm",
    r"not\s+confirmed",
    r"unconfirmed",
    r"no\s+(evidence|mention|sign|details?)\s+of",
    r"\bno\s+(formal\s+)?matchmak",
    r"unclear\s+(whether|if)",
    r"(is|are|was|were)\s+(being\s+)?planned",
    r"planned\s+for",
    r"future\s+edition",
    r"next\s+(year|edition)",
    r"(may|might|could)\s+(offer|run|include|introduce)",
    r"expected\s+to\s+(offer|run|launch)",
    r"we\s+(believe|assume|think)",
    r"informal(ly)?",
))

# Two tiers, because the veto exists to reject the conference app and a single
# agreeable synonym used to defeat it. STRONG patterns name the organiser or an
# industry term of art that only means an organiser-run programme; they beat the
# veto, which is what lets a real hosted-buyer show that also ships Swapcard
# keep its bonus. SUPPORTING patterns are consistent with a real programme but
# are also exactly how app vendors and welcome parties describe themselves
# ("concierge", "speed dating", "meetings programme"), so on their own they
# qualify an event with no veto against it and never override one.
_MATCHMAKING_STRONG = tuple(re.compile(p, re.I) for p in (
    r"hosted[-\s]?buyer",
    r"hosted[-\s]?delegate",
    r"pre[-\s]?schedul",
    r"curated\s+(1:1|one[-\s]to[-\s]one|meeting|introduc)",
    r"organi[sz]e(r|rs|d)?[^.]{0,40}?(match|pair|schedul|introduc|curat)",
    r"organi[sz]er[-\s]run",
    r"double[-\s]?opt[-\s]?in",
    r"account[-\s]managed",
    r"(match|pair)\w*\s+(operated|run|managed|administered)\s+by\s+"
    r"(the\s+)?(show|organi[sz]er|event|team)",
    r"1:1s?\s+(are\s+)?(pre[-\s]?)?(matched|arranged|assigned|booked\s+for)",
))

_MATCHMAKING_SUPPORTING = tuple(re.compile(p, re.I) for p in (
    r"matchmak",
    r"concierge",
    r"speed[-\s]?dating",
    r"ai[-\s]?match",
    r"(connect|meetings?|buyer|delegate)\s+programm?e",
    r"introduc\w+\s+(you|vendors|buyers|exhibitors)",
))

# Kept as the union so anything that used to read this name still sees every
# affirming pattern.
_MATCHMAKING_AFFIRM = _MATCHMAKING_STRONG + _MATCHMAKING_SUPPORTING


def matchmaking_bonus(organizer_run: bool, evidence: str) -> dict:
    """Award the +10 only for organizer-run matchmaking-as-a-service.

    Two independent gates, because either alone is bypassable:

      * `organizer_run` is the model's own claim. A model asked "is this
        organizer-run?" is agreeable and will often say yes.
      * `evidence` is what it cited. If the only thing it can point to is a
        conference app, the claim is refused no matter what the flag says.

    Returns {"bonus": 0|10, "awarded": bool, "reason": str}. The reason is
    rendered next to the score, so a refused bonus is visible rather than
    looking like the model simply never considered it.
    """
    text = (evidence or "").strip()
    low = text.lower()

    if not organizer_run:
        return {"bonus": 0, "awarded": False,
                "reason": "No organizer-run matchmaking programme was found."}
    if not text:
        return {"bonus": 0, "awarded": False,
                "reason": ("Matchmaking was claimed but nothing was cited to "
                           "support it, so the bonus is not awarded.")}

    hedged = [r.pattern for r in _MATCHMAKING_HEDGE if r.search(text)]
    if hedged:
        return {"bonus": 0, "awarded": False,
                "reason": ("The evidence hedges rather than describes a "
                           "programme that runs at this edition, so there is "
                           "nothing here a delegate could actually book.")}

    vetoed = [v for v in _MATCHMAKING_VETO if v in low]
    strong = [r.pattern for r in _MATCHMAKING_STRONG if r.search(text)]
    supporting = [r.pattern for r in _MATCHMAKING_SUPPORTING if r.search(text)]

    if vetoed and not strong:
        # A supporting word does not clear a veto. This is the gap that used to
        # let "speed-dating style networking, self-serve sign-up in Brella"
        # collect the full ten points.
        return {"bonus": 0, "awarded": False,
                "reason": ("The only matchmaking here is self-serve booking in "
                           "a conference app (%s), which is a baseline "
                           "expectation of any modern event rather than a "
                           "differentiator." % vetoed[0])}
    if not (strong or supporting):
        return {"bonus": 0, "awarded": False,
                "reason": ("The evidence does not describe the organizer "
                           "taking responsibility for pairing attendees with "
                           "counterparts matching stated criteria.")}
    return {"bonus": MATCHMAKING_BONUS, "awarded": True,
            "reason": "Organizer-run matchmaking: %s" % text[:300]}


def _pattern_signal(pattern: dict | None) -> tuple:
    """One basis's rate, if OUTCOME_MIN_SAMPLE is met. Returns
    (direction, skipped, went_or_going, decisions) where direction is
    -1 (skip pattern), 1 (go pattern) or 0 (no pattern / not enough data)."""
    if not pattern:
        return 0, 0, 0, 0
    decisions = int(pattern.get("decisions") or 0)
    skipped = int(pattern.get("skipped") or 0)
    went = int(pattern.get("went_or_going") or 0)
    if decisions < OUTCOME_MIN_SAMPLE:
        return 0, skipped, went, decisions
    if skipped / decisions >= OUTCOME_SIGNAL_RATE:
        return -1, skipped, went, decisions
    if went / decisions >= OUTCOME_SIGNAL_RATE:
        return 1, skipped, went, decisions
    return 0, skipped, went, decisions


def outcome_adjustment(category: str | None, category_pattern: dict | None,
                       format: str | None, format_pattern: dict | None) -> dict:
    """A capped, ORDER-ONLY signal from a client's own outcome history with
    this discovery category or event format.

    Never a fact about THIS event, so it never reaches score(), never
    touches `total` or `tier`. Like matchmaking_bonus, always returns a
    reason, including when nothing is applied -- a refused adjustment is
    visible rather than looking like history was never considered.

    Each pattern is {"decisions": int, "skipped": int, "went_or_going": int}
    or None/empty. `category` is tried first and wins over `format` when both
    clear the gate: category is the more specific dimension this rubric
    already scores against, and format (in_person/virtual/hybrid) cuts
    across every category, so it is the weaker signal. `went` and `going`
    count identically as "did not skip": a confirmed attendance is stronger
    evidence than a stated intent, but the >=75% gate already requires a
    real majority, so a lone uncommitted "going" cannot swing it alone.

    Returns {"adjustment": -OUTCOME_ADJUSTMENT|0|OUTCOME_ADJUSTMENT,
             "applied": bool, "basis": "category"|"format"|None, "reason": str}.
    """
    for basis, label, pattern in (("category", category, category_pattern),
                                  ("format", format, format_pattern)):
        direction, skipped, went, decisions = _pattern_signal(pattern)
        if direction == 0:
            continue
        if direction < 0:
            return {"adjustment": -OUTCOME_ADJUSTMENT, "applied": True,
                    "basis": basis,
                    "reason": ("This client skipped %d of the last %d "
                              "recommended %s events, so this one is ordered "
                              "lower. It is still on the list: a pattern in "
                              "your own history is a reason to look twice, "
                              "never a reason to hide something."
                              % (skipped, decisions, label))}
        return {"adjustment": OUTCOME_ADJUSTMENT, "applied": True,
                "basis": basis,
                "reason": ("This client attended or committed to %d of the "
                          "last %d recommended %s events, so this one is "
                          "ordered higher." % (went, decisions, label))}
    return {"adjustment": 0, "applied": False, "basis": None,
            "reason": ("Not enough of this client's own history with this "
                      "category or format yet (fewer than %d decisions) to "
                      "adjust anything." % OUTCOME_MIN_SAMPLE)}


# ── Scoring ───────────────────────────────────────────────────────────────

_LEADING_INT = re.compile(r"^\s*(-?\d+)")


def read_subscore(dimension: str, value) -> tuple:
    """Return (clamped_score, was_readable).

    The distinction this adds is the whole point. A model that never returned
    `dm_access` and a model that looked at the event and scored it 0 are the
    same number, and they mean opposite things: one is a 40-point dimension
    nobody measured, the other is a verdict. Reported as a verdict, the first
    one quietly removes a real event from the list at a plausible-looking 56.

    "38/40" is read as 38. It is a very common way for a model to answer a
    question phrased "out of 40", and scoring it 0 punished the event for the
    grader's formatting.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0, False
    if isinstance(value, bool):
        return 0, False
    try:
        n = int(value)
    except (TypeError, ValueError):
        m = _LEADING_INT.match(str(value))
        if not m:
            return 0, False
        n = int(m.group(1))
    return max(0, min(DIMENSION_MAX.get(dimension, 0), n)), True


def clamp_subscore(dimension: str, value) -> int:
    """Clamp one sub-score into its dimension's range.

    A model asked for a score out of 40 will occasionally answer 45, or "38/40",
    or null. Clamping at the boundary rather than trusting the number is what
    keeps a total from exceeding 110 and quietly breaking every tier
    comparison downstream.
    """
    if dimension not in DIMENSION_MAX:
        raise ValueError("Unknown dimension %r" % dimension)
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = 0
    return max(0, min(DIMENSION_MAX[dimension], n))


def _unscored_dimensions(relevance, dm_access, engagement) -> list:
    """Which of the three dimensions the grader did not actually return."""
    given = {DIM_RELEVANCE: relevance, DIM_DM_ACCESS: dm_access,
             DIM_ENGAGEMENT: engagement}
    return [d for d in DIMENSIONS if not read_subscore(d, given[d])[1]]


def score(relevance, dm_access, engagement, *, organizer_run: bool = False,
          matchmaking_evidence: str = "") -> dict:
    """Total a candidate. Deliberately has no budget parameter.

    The skill is explicit that a cheap event reaching the wrong buyers is
    worse than an expensive one reaching the right ones, and that budget must
    never be an input to the rubric. The strongest way to guarantee that is
    for this function to be unable to see it: three sub-scores and a
    matchmaking claim, nothing else, and no **kwargs to smuggle one in.
    """
    subs = {
        DIM_RELEVANCE: clamp_subscore(DIM_RELEVANCE, relevance),
        DIM_DM_ACCESS: clamp_subscore(DIM_DM_ACCESS, dm_access),
        DIM_ENGAGEMENT: clamp_subscore(DIM_ENGAGEMENT, engagement),
    }
    base = sum(subs.values())
    mm = matchmaking_bonus(organizer_run, matchmaking_evidence)
    total = base + mm["bonus"]
    return {
        "sub_scores": subs,
        "base": base,
        "matchmaking": mm["bonus"],
        "matchmaking_awarded": mm["awarded"],
        "matchmaking_reason": mm["reason"],
        "total": total,
        "tier": tier_for(total),
    }


# What a candidate must carry before its score means anything. Missing values
# are reported, not filled in: the skill's output explicitly lists "unverified
# attendee counts" as something the assumptions section must name.
def _as_date(value):
    """A datetime.date from whatever the store handed back, or None."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def has_finished(candidate: dict, today=None) -> bool:
    """True when this edition is over.

    A recommendation is a claim about the future. Until this existed nothing in
    the recommend path compared a date to today, so a conference that ended in
    2019 could score 92, tier P1, and render under the label "Must-attend.
    Book it." beside its own past date. The end date decides it where there is
    one, because an event is still worth walking into on its final morning.
    """
    today = today or datetime.date.today()
    end = _as_date(candidate.get("ends_on")) or _as_date(candidate.get("starts_on"))
    return bool(end and end < today)


_GAP_CHECKS = (
    ("attendees", "The event publishes no attendance figure, so density is "
                  "judged from its stated audience rather than a headcount."),
    ("website", "No official site was confirmed, so everything here rests on "
                "secondary sources."),
    ("starts_on", "No dates are announced yet, so this cannot be placed in a "
                  "quarter with confidence."),
    ("format", "Whether this runs in person, online or both was not "
               "established, so the travel it implies is unknown."),
    ("sources", "No page was cited for this event, so none of the facts above "
                "can be checked against the organiser."),
)


def gaps_for(candidate: dict, today=None) -> list[str]:
    """What could NOT be measured for this candidate.

    A score built on three confident readings and a score built on three
    guesses render identically without this. Every checker in this codebase
    is required to report what it could not measure.
    """
    c = candidate or {}
    out = []
    for field, note in _GAP_CHECKS:
        if not c.get(field):
            out.append(note)
    # A dimension nobody scored is the most consequential thing that can be
    # missing from a row, because it is worth up to 40 of the 100 points the
    # row is being judged on.
    for dim in _unscored_dimensions(c.get(DIM_RELEVANCE), c.get(DIM_DM_ACCESS),
                                    c.get(DIM_ENGAGEMENT)):
        out.append("%s was never scored, so this total is out of %d, not %d."
                   % (DIMENSION_LABELS[dim], BASE_MAX - DIMENSION_MAX[dim],
                      BASE_MAX))
    for dim in DIMENSIONS:
        if not c.get(dim + "_note"):
            out.append("No reasoning was recorded for %s, so its sub-score "
                       "cannot be audited." % DIMENSION_LABELS[dim].lower())
    if has_finished(c, today):
        out.append("This edition has already ended, so it is history rather "
                   "than a recommendation.")
    return out


def is_worth_a_look(candidate: dict) -> bool:
    """Whether a below-the-bar candidate is a real option or a genuine miss.

    Both gates are measured, and both must pass. See RELEVANCE_GATE and
    CONSIDER_FLOOR for why there are two.

    An unreadable or missing `relevance` fails. That is deliberate and it is
    the safe direction: this function's whole job is to assert that an event
    is genuinely aimed at this client, and a dimension nobody scored is not
    evidence of anything. `event_intel_scorer` already routes a partially
    scored event to `unscored` for the same reason, so a row arriving here
    without a relevance score is one whose fit was never established, and
    offering it as an option would be padding the list with an unknown.
    """
    value, readable = read_subscore(DIM_RELEVANCE, candidate.get(DIM_RELEVANCE))
    if not readable:
        return False
    return value >= RELEVANCE_GATE and (candidate.get("total") or 0) >= CONSIDER_FLOOR


def rank(candidates: list[dict], cap: int = DEFAULT_CAP, today=None) -> dict:
    """Sort into recommended, worth-a-look and cut, cap, and report the rest.

    NEVER pads. The skill: "A short list of P1/P2 events beats a long list
    diluted with P3s." So the cap is a ceiling, nothing tops any list up
    toward it, and the three buckets are decided by measurements rather than
    by how many rows a section would like to have.

    `kept` is the recommendation: everything at or above RANK_FLOOR, plus
    anything already committed to.

    `worth_a_look` is the second tier, and it is the answer to a real
    complaint about this agent: it returned one event for one client and none
    for another, while events that were real, upcoming, cited and aimed
    squarely at that client's buyers sat in the discard pile for being
    learning-crowd conferences. These are full candidate rows, not name
    chips, because they are offered as options and an option a reader cannot
    read is not one. Two gates decide membership, both measured: the audience
    is genuinely this client's (RELEVANCE_GATE on the relevance sub-score),
    and the event is not structurally unworkable (CONSIDER_FLOOR on the
    total).

    `excluded` is everything else below the bar, with its score, as before. A
    list truncated in silence reads as "nothing else was found", which is a
    different and false claim from "six more were found and none cleared the
    bar".
    """
    scored = sorted((c for c in candidates or []),
                    key=lambda c: (-(c.get("total") or 0),
                                   (c.get("name") or "").lower()))
    kept, excluded, below, finished, considered = [], [], [], [], []
    for c in scored:
        # Before any question of merit: an edition that is over cannot be
        # attended. It is reported in its own bucket rather than dropped,
        # because "we found it and it already happened" is a different and more
        # useful statement than silence, and it tells the reader the next
        # edition is the thing to go looking for.
        if has_finished(c, today):
            finished.append({"name": c.get("name"), "total": c.get("total") or 0,
                             "ends_on": c.get("ends_on") or c.get("starts_on"),
                             "category": c.get("category")})
            continue
        if (c.get("total") or 0) >= RANK_FLOOR:
            kept.append(c)
        elif c.get("committed"):
            # An event the client has already committed to is kept whatever it
            # scores. Cutting it would hide the single most actionable thing
            # this analysis can say: that money is already spent on an event
            # that does not clear the bar. It is marked, never quietly mixed
            # in with the events that earned their place.
            kept.append(c)
            below.append({"name": c.get("name"), "total": c.get("total") or 0})
        elif is_worth_a_look(c):
            # Below the priority bar, but the audience is measurably this
            # client's and the event is workable. A real option, so it keeps
            # its whole row: a reader offered an event with no dates, city or
            # description has not been offered anything.
            considered.append(c)
        else:
            excluded.append({"name": c.get("name"), "total": c.get("total") or 0,
                             "tier": TIER_P3,
                             "category": c.get("category")})
    over_cap = []
    if cap and len(kept) > cap:
        # The cap never drops a committed event. Being pushed off the end of a
        # list by length is not a judgement, and this one has already been paid
        # for.
        head = kept[:cap]
        tail = kept[cap:]
        rescued = [c for c in tail if c.get("committed")]
        over_cap = [{"name": c.get("name"), "total": c.get("total") or 0}
                    for c in tail if not c.get("committed")]
        kept = head + rescued
    return {
        "kept": kept,
        # Ordered like the recommendation, and capped the same way: a second
        # tier that ran to forty rows would bury the list it sits under.
        "worth_a_look": considered[:cap] if cap else considered,
        "excluded": excluded,
        "over_cap": over_cap,
        "finished": finished,
        # Committed events that did not clear the bar on their own merits.
        "committed_below_bar": below,
        "counts": {
            "kept": len(kept),
            TIER_P1: sum(1 for c in kept if c.get("tier") == TIER_P1),
            TIER_P2: sum(1 for c in kept if c.get("tier") == TIER_P2),
            "worth_a_look": len(considered[:cap] if cap else considered),
            "excluded": len(excluded),
            "over_cap": len(over_cap),
            "finished": len(finished),
            "committed_below_bar": len(below),
        },
    }


def methodology_note(classification: str) -> str:
    """The scoring methodology as applied to THIS client, which is element 3
    of the executive summary the skill specifies."""
    orient = orientation_for(classification)
    side = ("the exhibitor booths" if orient == ORIENTATION_BOOTH
            else "the audience and session tracks")
    return (
        "Every event is scored out of 100 across three dimensions, plus a "
        "10-point bonus for organizer-run matchmaking, for a total out of 110. "
        "Relevance (40) is how closely the composition matches your ICP. "
        "Decision-maker access (40) is buyer density plus the structural reach "
        "to them. Engagement mode (20) is whether these people are in a "
        "vendor-buying mindset or a learning one. "
        "You are classified as %s, which puts the people you sell to %s.%s "
        "Relevance and access are therefore scored at %s rather than on the "
        "other side of the room. "
        "P1 is 80 or above, P2 is 70 to 79, and anything below 70 is excluded "
        "rather than used to pad the list. Cost is shown beside each event as "
        "context for your decision and is never an input to a score."
        % (CLASSIFICATION_LABELS[classification],
           CLASSIFICATION_BUYER_PLACE[classification],
           (" " + CLASSIFICATION_WHY[classification])
           if classification in CLASSIFICATION_WHY else "",
           side))
