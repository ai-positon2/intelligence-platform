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
# This call runs live search too (rule 4 forbids inventing a figure the
# rubric needs verified), so it hits the same output-starvation trap as
# find/confirm/audit/resolve: the model narrates between search rounds and
# that narration spends the OUTPUT budget alongside the answer. A live run at
# BATCH=6 hit it hard: a 4-EVENT batch with 6 searches produced 14,601 output
# tokens against a budget of 8,000 and was truncated mid-JSON. Every other
# stage's single-item budget (9,000) was raised on evidence that topped out
# around 6,000-11,000; this call writes THREE scored notes plus a two-part
# description for every event in the batch, on top of the same narration
# overhead, so its floor has to scale with BATCH rather than sit at the
# single-item number.
#
# The failure mode here is worse than anywhere else in the pipeline: score_all
# can run several batches concurrently, but when the whole candidate pool fits
# in one batch (a small pool, or a client near the floor), that ONE call
# truncating discards every survivor of discovery, confirmation and the
# audit in a single stroke. A live run did exactly this: discovery, confirm
# and the audit all worked, and the run still finished with ZERO recommended
# events because its only scoring batch was cut off.
#
# Verified live after this fix, directly against score_batch with a real
# 6-event (full BATCH) batch and 6 live searches: stop_reason=end_turn, all 6
# scored, 22,192 output tokens used. That was against a first attempt of
# 24,000 -- a 92% fill with no margin for a batch that happens to write
# longer notes -- so the ceiling is held above the measured number rather
# than pinned to it. Raising it costs nothing unless a batch actually needs
# the room: max_tokens is a ceiling, not a charge.
SCORE_MAX_TOKENS = 32000

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
        # read_subscore, not clamp_subscore. Clamping turns a dimension the
        # grader never returned into a 0, and 0 is a verdict: rubric.gaps_for
        # reads the stored value and can no longer tell "we looked and it
        # scores nothing" from "nobody scored this at all". The second is
        # worth up to 40 of 100 points, and reported as the first it cuts a
        # real event off the list at a plausible-looking total. None is kept
        # here and handled in score_all; rubric.score() still clamps it to 0
        # for arithmetic, so nothing downstream sees a null total.
        value, readable = rubric.read_subscore(dim, raw.get(dim))
        out[dim] = value if readable else None
        # House style has no em dashes. This is written prose the grader
        # composes itself (a note, a description, the client-fit sentence),
        # not an echo of anything already cleaned upstream, and it was found
        # live carrying dashes into a client's report before this was added.
        out[dim + "_note"] = claude_websearch.strip_em_dash(
            str(raw.get(dim + "_note") or "").strip())[:800] or None
    out["description"] = claude_websearch.strip_em_dash(
        str(raw.get("description") or "").strip())[:900] or None
    out["client_line"] = claude_websearch.strip_em_dash(
        str(raw.get("client_line") or "").strip())[:600] or None
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
    res = claude_websearch.ask(system, user, max_uses=6, max_tokens=SCORE_MAX_TOKENS)
    # Counted on every path out of here, including the two refusals below: a
    # scoring pass whose answer could not be read cost exactly what a
    # readable one cost.
    spend = claude_websearch.spend_of(res)
    if res.get("error"):
        return {"scores": {}, "spend": spend,
                "error": "%s: %s" % (res["error"]["kind"], res["error"]["detail"])}
    parsed = claude_websearch.extract_json(res.get("text") or "", require="scores")
    if not isinstance(parsed, dict):
        return {"scores": {}, "spend": spend,
                "error": "The scoring pass ran but its answer could not be read."}
    out = {}
    for s in (parsed.get("scores") or []):
        clean = _clean(s)
        if clean:
            out[score_key(clean["name"])] = clean
    return {"scores": out, "error": None, "spend": spend}


def deal(candidates: list[dict], size: int = BATCH) -> list[list[dict]]:
    """Split candidates into batches, DEALT round-robin rather than sliced.

    This module exists so that one standard is applied to every candidate
    instead of six category finders each grading their own. Past `size`
    candidates that guarantee is weaker than it looks: each batch is a
    separate call, and a batch can only calibrate against the events inside
    it.

    Slicing made that as bad as it can get. `merge()` returns candidates in
    CATEGORIES order, so a contiguous slice of six was usually one or two
    categories: one grader saw nothing but industry flagships and another
    nothing but side events, and "dense with the right buyers" means a
    different thing to each of them. Dealing spreads every category across
    every batch, so each grader sees the same mix.

    It does not make separate calls into one grader. That is what `batches`
    in the returned dict is for, and the report says how many ran.
    """
    n = len(candidates)
    if n <= size:
        return [list(candidates)] if n else []
    count = (n + size - 1) // size
    out: list[list[dict]] = [[] for _ in range(count)]
    for i, c in enumerate(candidates):
        out[i % count].append(c)
    return out


def score_key(name: str) -> tuple:
    """The key `merge` would agree with: the stripped name AND its region.

    name_key alone strips region words, which is right for deciding that
    "MarTech Summit" and "MarTech Summit Europe" are one event and wrong for
    deciding that "Money20/20 USA" and "Money20/20 Europe" are. merge() knows
    that and keeps both; this dict used name_key on its own, so the two
    editions shared one slot and the second one graded overwrote the first.
    Both rows were then stored with one edition's scores, notes and
    description, which reads as a confident grade of the wrong continent.
    """
    from .event_intel_discover import name_key, region_key
    return (name_key(name), region_key(name))


def _lookup(scores: dict, name: str) -> dict | None:
    """This candidate's scores, tolerating a name the grader reworded.

    The prompt asks for the name it was given, back verbatim. Asking is not
    getting, and the exact-key lookup this replaces sent an event with its own
    scores to the unscored bucket whenever the grader returned "SaaStr" for
    "SaaStr Annual". `merge` and `_dedupe_proposals` already treat those as
    one event, so the strict comparison here disagreed with the rest of the
    module about what the same event is.

    Loose matching is accepted ONLY when exactly one candidate matches, the
    same guard event_intel_audit._verdict_for uses: one event wearing
    another's sub-scores is a worse outcome than the miss.
    """
    from .event_intel_discover import names_match
    key = score_key(name or "")
    if not key[0]:
        return None
    exact = scores.get(key)
    if exact is not None:
        return exact
    # names_match compares regions as well as names, so a reply that dropped
    # the region cannot be matched to one edition while another edition of the
    # same series is also in the dict: that comes back ambiguous and the event
    # is reported unscored, which is the honest answer.
    hits = [v for k, v in scores.items()
            if k != key and names_match(name or "", v.get("name") or k[0])]
    return hits[0] if len(hits) == 1 else None


def score_all(candidates: list[dict], profile: dict) -> dict:
    """Score every candidate, in concurrent batches.

    A candidate the scorer never returned is kept and marked UNSCORED rather
    than dropped or defaulted to zero. Zero would rank it last, which reads as
    "we judged this and it is bad"; dropping it reads as "this does not
    exist". Neither is what happened.
    """
    batches = deal(candidates, BATCH)
    merged: dict = {}
    errors: list[str] = []
    spends: list = []
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
                spends.append(r.get("spend"))
                if r.get("error"):
                    errors.append(r["error"])
                merged.update(r.get("scores") or {})

    scored, unscored = [], []
    for c in candidates:
        c = dict(c)
        s = _lookup(merged, c.get("name") or "")
        if not s:
            c["unscored"] = True
            c["scoring_note"] = ("The scoring pass returned no result for this "
                                 "event, so it is unranked rather than ranked "
                                 "low.")
            unscored.append(c)
            continue
        # A reply that scored two of the three dimensions is not a score. The
        # missing one is worth up to 40 points, so ranking the event on what
        # did come back presents a partial total as a verdict and quietly
        # drops a strong event under the floor. Unranked and named is the same
        # answer this function already gives for a reply that never arrived.
        missing = [d for d in rubric.DIMENSIONS if s[d] is None]
        if missing:
            c["unscored"] = True
            c["scoring_note"] = (
                "The scoring pass returned no %s for this event, and that "
                "dimension is worth up to %d of the 100 points, so it is "
                "unranked rather than ranked on a partial total."
                % (" or ".join(rubric.DIMENSION_LABELS[d].lower()
                               for d in missing),
                   sum(rubric.DIMENSION_MAX[d] for d in missing)))
            unscored.append(c)
            continue
        for dim in rubric.DIMENSIONS:
            c[dim] = s[dim]
            c[dim + "_note"] = s[dim + "_note"]
        c["description"] = s["description"]
        c["client_line"] = s["client_line"]
        scored.append(c)
    return {"scored": scored, "unscored": unscored,
            "errors": errors, "batches": len(batches),
            "spend": claude_websearch.spend_sum(*spends)}


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
