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
    TIER_P3: "Below the bar. Excluded from the ranked list.",
}

# The one line that decides whether a candidate is shown at all.
RANK_FLOOR = TIER_MIN[TIER_P2]

DEFAULT_CAP = 15


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
    CAT_FREE_VENDOR: "Free vendor conference",
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
        ("One-day AWS, Snowflake, HubSpot, Salesforce, Databricks or "
         "ServiceNow city events. Packed with budget owners, free to attend, "
         "and the most under-utilised circuit in B2B field sales."),
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

# Patterns that indicate the organizer takes active responsibility for pairing.
# Stems rather than exact phrases: "the organizer pre-schedules 1:1 meetings"
# and "pre-scheduled meetings" describe the same programme, and an exact-match
# list rejects one of them. A false negative here silently strips a real P1
# event of a bonus it earned, which is worse than the false positive the veto
# list already catches.
_MATCHMAKING_AFFIRM = tuple(re.compile(p, re.I) for p in (
    r"hosted[-\s]?buyer",
    r"hosted[-\s]?delegate",
    r"matchmak",
    r"pre[-\s]?schedul",
    r"curated\s+(1:1|one[-\s]to[-\s]one|meeting|introduc)",
    r"organi[sz]e(r|rs|d)?[^.]{0,40}?(match|pair|schedul|introduc|curat)",
    r"organi[sz]er[-\s]run",
    r"double[-\s]?opt[-\s]?in",
    r"account[-\s]managed",
    r"concierge",
    r"speed[-\s]?dating",
    r"ai[-\s]?match",
    r"(connect|meetings?|buyer|delegate)\s+programm?e",
    r"1:1s?\s+(are\s+)?(pre[-\s]?)?(matched|arranged|assigned|booked\s+for)",
    r"introduc\w+\s+(you|vendors|buyers|exhibitors)",
))


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

    vetoed = [v for v in _MATCHMAKING_VETO if v in low]
    affirmed = [r.pattern for r in _MATCHMAKING_AFFIRM if r.search(text)]

    if vetoed and not affirmed:
        return {"bonus": 0, "awarded": False,
                "reason": ("The only matchmaking here is self-serve booking in "
                           "a conference app (%s), which is a baseline "
                           "expectation of any modern event rather than a "
                           "differentiator." % vetoed[0])}
    if not affirmed:
        return {"bonus": 0, "awarded": False,
                "reason": ("The evidence does not describe the organizer "
                           "taking responsibility for pairing attendees with "
                           "counterparts matching stated criteria.")}
    return {"bonus": MATCHMAKING_BONUS, "awarded": True,
            "reason": "Organizer-run matchmaking: %s" % text[:300]}


# ── Scoring ───────────────────────────────────────────────────────────────

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
_GAP_CHECKS = (
    ("attendees", "The event publishes no attendance figure, so density is "
                  "judged from its stated audience rather than a headcount."),
    ("website", "No official site was confirmed, so everything here rests on "
                "secondary sources."),
    ("starts_on", "No dates are announced yet, so this cannot be placed in a "
                  "quarter with confidence."),
)


def gaps_for(candidate: dict) -> list[str]:
    """What could NOT be measured for this candidate.

    A score built on three confident readings and a score built on three
    guesses render identically without this. Every checker in this codebase
    is required to report what it could not measure.
    """
    out = []
    for field, note in _GAP_CHECKS:
        if not (candidate or {}).get(field):
            out.append(note)
    for dim in DIMENSIONS:
        if not (candidate or {}).get(dim + "_note"):
            out.append("No reasoning was recorded for %s, so its sub-score "
                       "cannot be audited." % DIMENSION_LABELS[dim].lower())
    return out


def rank(candidates: list[dict], cap: int = DEFAULT_CAP) -> dict:
    """Sort, cut everything below the floor, cap, and report what was dropped.

    NEVER pads. The skill: "A short list of P1/P2 events beats a long list
    diluted with P3s." So the cap is a ceiling and nothing tops the list up
    toward it.

    Returns the kept rows plus `excluded` (every candidate that scored below
    70, with its score) and `over_cap`. Both are rendered. A list truncated in
    silence reads as "nothing else was found", which is a different and false
    claim from "six more were found and none cleared the bar".
    """
    scored = sorted((c for c in candidates or []),
                    key=lambda c: (-(c.get("total") or 0),
                                   (c.get("name") or "").lower()))
    kept, excluded = [], []
    for c in scored:
        if (c.get("total") or 0) >= RANK_FLOOR:
            kept.append(c)
        else:
            excluded.append({"name": c.get("name"), "total": c.get("total") or 0,
                             "tier": TIER_P3,
                             "category": c.get("category")})
    over_cap = []
    if cap and len(kept) > cap:
        over_cap = [{"name": c.get("name"), "total": c.get("total") or 0}
                    for c in kept[cap:]]
        kept = kept[:cap]
    return {
        "kept": kept,
        "excluded": excluded,
        "over_cap": over_cap,
        "counts": {
            "kept": len(kept),
            TIER_P1: sum(1 for c in kept if c.get("tier") == TIER_P1),
            TIER_P2: sum(1 for c in kept if c.get("tier") == TIER_P2),
            "excluded": len(excluded),
            "over_cap": len(over_cap),
        },
    }


def methodology_note(classification: str) -> str:
    """The scoring methodology as applied to THIS client, which is element 3
    of the executive summary the skill specifies."""
    orient = orientation_for(classification)
    where = CLASSIFICATION_WHERE_BUYERS_ARE[classification]
    side = ("the exhibitor booths" if orient == ORIENTATION_BOOTH
            else "the audience and session tracks")
    return (
        "Every event is scored out of 100 across three dimensions, plus a "
        "10-point bonus for organizer-run matchmaking, for a total out of 110. "
        "Relevance (40) is how closely the composition matches your ICP. "
        "Decision-maker access (40) is buyer density plus the structural reach "
        "to them. Engagement mode (20) is whether these people are in a "
        "vendor-buying mindset or a learning one. "
        "You are classified as %s, so %s Density and reach are therefore "
        "scored at %s. "
        "P1 is 80 or above, P2 is 70 to 79, and anything below 70 is excluded "
        "rather than used to pad the list. Cost is shown beside each event as "
        "context for your decision and is never an input to a score."
        % (CLASSIFICATION_LABELS[classification].lower(), where.lower(), side))
