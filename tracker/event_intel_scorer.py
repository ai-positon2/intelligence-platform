"""Step 4 and Step 8: apply the rubric, and write the description.

Separate from discovery on purpose. If each category's finder scored its own
events, six different standards would be applied to six slices of one list and
the totals would not be comparable, which is the one thing a ranked table has
to be. Here every surviving candidate is graded against the same rubric, with
the same client profile, in the same pass.

Two things this module deliberately does NOT do:

* It does not compute a total. It returns three sub-scores; the total and the
  tier are derived downstream in event_intel_store.normalise_candidate(), from
  those sub-scores and the matchmaking gate. A model that writes its own total
  eventually writes one that disagrees with its own breakdown.

* It does not see budget. The profile brief handed to it omits cost entirely,
  and each candidate's cost_note is withheld from the prompt. The skill is
  explicit that a cheap event reaching the wrong buyers is worse than an
  expensive one reaching the right ones.

Step 8, the description, lives here too because it needs exactly the context
scoring needs. It is deliberately split into two stored fields rather than one
paragraph: `description` is the conference's texture and is constant across
clients, `client_line` is why THIS client's product matters to THIS audience
and must not be interchangeable. Keeping them apart is what lets
flag_interchangeable() below actually measure the skill's anti-pattern instead
of asking a model whether it committed it.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re

from . import claude_websearch
from . import event_intel_rubric as rubric

logger = logging.getLogger(__name__)

BATCH = 6
MAX_CONCURRENCY = 3

_SYSTEM = """You score business events against one client's ICP using a fixed \
rubric, and you write each event's description. You are grading events you did \
not choose, against one standard, for one client.

THE CLIENT
{profile}

WHERE THIS CLIENT'S BUYERS PHYSICALLY ARE AT AN EVENT: {where_buyers}
Score density and reach on THAT side of the event. For a booth-driven client, \
a hall full of the right vendors is the buying audience; the ticket-holders \
are not. For an audience-driven client, the reverse.

THE RUBRIC
- relevance, 0 to 40: how closely the composition of this event matches the \
client's ICP above.
- dm_access, 0 to 40: density of actual decision-makers AND the structural \
reach to them. Floor layout, meeting infrastructure, side events, whether you \
can physically get to the people who sign.
- engagement, 0 to 20: are these people in a vendor-buying mindset, or is this \
a learning and keynote crowd who will not take a meeting?

Each sub-score needs a one-or-two-sentence `_note` giving the reasoning. The \
notes are the audit trail; a score without one cannot be checked.

HARD CONSTRAINTS.
1. Do NOT add anything for matchmaking programmes. That bonus is applied \
separately and adding it here would double-count it.
2. You have not been told what any of this costs, and you must not speculate. \
Cost never moves a score.
3. Do not inflate. Most events are mediocre for most clients. A rubric where \
everything lands between 75 and 85 has measured nothing.
4. Use only the facts given plus what you can verify by searching. Never \
invent an attendance figure.

THE DESCRIPTION, two fields, roughly 30 to 45 words in total.
- `description` is sentence one: the conference's texture. Scale, audience \
composition with NAMED roles and verticals, distinctive format. Numbers beat \
adjectives. "CMOs and growth leads from neobanks, payments and embedded \
finance" beats "fintech executives". This sentence is about the event and \
would be the same for any client.
- `client_line` is sentence two: why THIS audience needs THIS client's product \
now. It must be impossible to paste onto a different client or a different \
event. If you could, you have written the wrong sentence.

BANNED in both: "premier", "world-class", "leading", "must-attend", \
"unparalleled", and any sentence that would fit any other event.

Respond with ONLY a JSON object:
{{"scores": [{{"name": str, "relevance": int, "relevance_note": str, \
"dm_access": int, "dm_access_note": str, "engagement": int, \
"engagement_note": str, "description": str, "client_line": str}}]}}

`name` must exactly match the name you were given."""

# Whole words, not substrings. "leading" was missing entirely, which is the
# most common superlative of the set and one the prompt explicitly bans, so
# "the leading fintech conference" passed a check that reported itself as
# having run. Substring matching also fired "premier" on "premiere".
_BANNED = tuple(re.compile(r"\b%s\b" % p, re.I) for p in (
    r"premier", r"world[-\s]class", r"leading", r"industry[-\s]leading",
    r"must[-\s]attend", r"unparalleled", r"cutting[-\s]edge",
    r"best[-\s]in[-\s]class", r"unrivall?ed", r"game[-\s]chang\w+",
    r"the go[-\s]to event", r"can'?t[-\s]miss", r"flagship event",
))


def _candidate_brief(c: dict) -> str:
    """One candidate, as the scorer sees it. cost_note is not included."""
    bits = ["- %s" % c.get("name")]
    for label, key in (("edition", "edition"), ("where", "city"),
                       ("country", "country"), ("dates", "starts_on"),
                       ("days", "days"), ("industry", "industry"),
                       ("attendees, as published", "attendees"),
                       ("exhibitors, as published", "booths"),
                       ("who it says it is for", "audience_note"),
                       ("found as", "category_fit"),
                       ("organiser", "organizer"), ("site", "website")):
        if c.get(key):
            bits.append("  %s: %s" % (label, c[key]))
    return "\n".join(bits)


def _clean(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    out = {"name": name}
    for dim in rubric.DIMENSIONS:
        out[dim] = rubric.clamp_subscore(dim, raw.get(dim))
        out[dim + "_note"] = str(raw.get(dim + "_note") or "").strip()[:800] or None
    out["description"] = str(raw.get("description") or "").strip()[:900] or None
    out["client_line"] = str(raw.get("client_line") or "").strip()[:600] or None
    return out


def score_batch(batch: list[dict], profile: dict) -> dict:
    """Score up to BATCH candidates in one call. Never raises."""
    from .event_intel_discover import profile_brief
    system = _SYSTEM.format(
        profile=profile_brief(profile),
        where_buyers=rubric.CLASSIFICATION_WHERE_BUYERS_ARE.get(
            profile.get("classification"), "Confirm with the client."))
    user = ("Score these %d events and write each description:\n\n%s"
            % (len(batch), "\n".join(_candidate_brief(c) for c in batch)))
    res = claude_websearch.ask(system, user, max_uses=6, max_tokens=8000)
    if res.get("error"):
        return {"scores": {},
                "error": "%s: %s" % (res["error"]["kind"], res["error"]["detail"])}
    parsed = claude_websearch.extract_json(res.get("text") or "", require="scores")
    if not isinstance(parsed, dict):
        return {"scores": {},
                "error": "The scoring pass ran but its answer could not be read."}
    from .event_intel_discover import name_key
    out = {}
    for s in (parsed.get("scores") or []):
        clean = _clean(s)
        if clean:
            out[name_key(clean["name"])] = clean
    return {"scores": out, "error": None}


def score_all(candidates: list[dict], profile: dict) -> dict:
    """Score every candidate, in concurrent batches.

    A candidate the scorer never returned is kept and marked UNSCORED rather
    than dropped or defaulted to zero. Zero would rank it last, which reads as
    "we judged this and it is bad"; dropping it reads as "this does not
    exist". Neither is what happened.
    """
    batches = [candidates[i:i + BATCH] for i in range(0, len(candidates), BATCH)]
    merged: dict = {}
    errors: list[str] = []
    if batches:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(MAX_CONCURRENCY, len(batches))) as pool:
            futures = [pool.submit(score_batch, b, profile) for b in batches]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:
                    logger.exception("event_intel_scorer: batch crashed")
                    errors.append("A scoring batch failed: %s" % str(e)[:200])
                    continue
                if r.get("error"):
                    errors.append(r["error"])
                merged.update(r.get("scores") or {})

    from .event_intel_discover import name_key
    scored, unscored = [], []
    for c in candidates:
        c = dict(c)
        s = merged.get(name_key(c.get("name") or ""))
        if not s:
            c["unscored"] = True
            c["scoring_note"] = ("The scoring pass returned no result for this "
                                 "event, so it is unranked rather than ranked "
                                 "low.")
            unscored.append(c)
            continue
        for dim in rubric.DIMENSIONS:
            c[dim] = s[dim]
            c[dim + "_note"] = s[dim + "_note"]
        c["description"] = s["description"]
        c["client_line"] = s["client_line"]
        scored.append(c)
    return {"scored": scored, "unscored": unscored,
            "errors": errors, "batches": len(batches)}


# ── Step 8's anti-pattern, measured rather than requested ─────────────────

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set:
    return set(_WORD.findall((s or "").lower()))


def flag_interchangeable(candidates: list[dict], threshold: float = 0.8) -> list[dict]:
    """Find second sentences that could be pasted onto another event.

    The skill's own test is "this sentence must NOT be interchangeable across
    clients or events". Within one run that is directly checkable: if two
    events in the same list share most of their second sentence, then by
    construction it fits both, and it is the generic sentence the skill bans.

    Returns one entry per offending pair, so the report can name them rather
    than assert that a check was done.
    """
    lines = [(c.get("name"), _tokens(c.get("client_line")))
             for c in (candidates or []) if (c.get("client_line") or "").strip()]
    out = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            a, b = lines[i][1], lines[j][1]
            if not a or not b:
                continue
            overlap = len(a & b) / len(a | b)
            if overlap >= threshold:
                out.append({"a": lines[i][0], "b": lines[j][0],
                            "overlap": round(overlap, 3)})
    return out


def flag_banned_language(candidates: list[dict]) -> list[dict]:
    """Marketing superlatives the skill lists as anti-patterns."""
    out = []
    for c in (candidates or []):
        text = "%s %s" % (c.get("description") or "", c.get("client_line") or "")
        hits = sorted({m.group(0).lower()
                       for r in _BANNED for m in r.finditer(text)})
        if hits:
            out.append({"name": c.get("name"), "words": hits})
    return out


def flag_thin_descriptions(candidates: list[dict]) -> list[dict]:
    """A one-sentence entry, or a missing second sentence, is the other
    anti-pattern named in Step 8."""
    out = []
    for c in (candidates or []):
        missing = []
        if not (c.get("description") or "").strip():
            missing.append("conference description")
        if not (c.get("client_line") or "").strip():
            missing.append("client-specific case")
        if missing:
            out.append({"name": c.get("name"), "missing": missing})
    return out
