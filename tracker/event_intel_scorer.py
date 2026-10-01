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
from .event_intel_jobs import ContextExecutor
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

# Searches per event, and the ceiling per call. The budget was a flat
# max_uses=6 per batch whatever its size, so an event scored in a batch of
# one had six searches to itself and the same event in a full batch had one.
# That is two different depths of evidence behind two numbers presented as
# comparable, and it is the variance the borderline re-score below exists to
# damp: a lone re-scored event would be graded on six times the evidence of
# its first reading. One per event, capped at the old ceiling, keeps a full
# batch exactly as it was and stops a small batch paying for searches that
# only make its grade less comparable.
SCORE_SEARCHES_PER_EVENT = 1
SCORE_MAX_USES = 6

# The borderline re-score. An event within rubric.BORDERLINE_MARGIN of a
# cut-off is graded RESCORE_PASSES more times and keeps the per-dimension
# median, because relevance swings by up to three points between runs and a
# verdict decided by which side of 24 or 70 one reading fell on is noise.
# Capped at RESCORE_MAX_BATCHES extra calls per run (one batch per pass, so
# at most BATCH events get the treatment): the closest to a line go first.
RESCORE_PASSES = 2
RESCORE_MAX_BATCHES = 2

_SYSTEM = """You score business events against one client's ICP using a fixed \
rubric, and you write each event's description. You are grading events you did \
not choose, against one standard, for one client.

THE CLIENT
{profile}

WHERE THIS CLIENT'S BUYERS PHYSICALLY ARE AT AN EVENT: {where_buyers}
Score density and reach on THAT side of the event. For a booth-driven client, \
a hall full of the right vendors is the buying audience; the ticket-holders \
are not. For an audience-driven client, the reverse.

THE RUBRIC. Each band is a fixed description. Place the event in the band \
its evidence supports, then within the band by how strongly.
- relevance, 0 to 40: how closely the composition of this event matches the \
client's ICP above. A buyer is someone in one of the client's buyer roles at \
an organisation the client sells to. The verticals listed are where the \
client sells most, not all it sells to: count an organisation outside them \
when the client's product is plainly bought by organisations like it.
  30-40: the event is built for the client's buyers. They are the audience \
the programme and the relevant side of the floor are aimed at.
  20-29: the client's buyers are one of the core audiences the event is \
built for, beside others (partners, peers, adjacent roles). 24 and above \
means a buyer of this client finds sessions made for them and their peers \
there in numbers; under 24, they are present but not who it is for.
  10-19: the client's buyers attend, but the event is aimed at another \
function or market and they are a side crowd.
  0-9: they are rare, or the event serves a different market.
- dm_access, 0 to 40: density of actual decision-makers AND the structural \
reach to them. Floor layout, meeting infrastructure, side events, whether you \
can physically get to the people who sign.
  30-40: the people who sign attend in numbers and the format gives a \
structured way to reach them (hosted meetings, a floor they work, small \
formats).
  20-29: they attend and can be reached, but it takes effort (a large open \
floor, general networking).
  10-19: few of them attend, or they attend but the format keeps them out of \
reach (keynote halls, no floor, no meeting space).
  0-9: the people who sign are essentially absent or unreachable.
- engagement, 0 to 20: are these people in a vendor-buying mindset, or is this \
a learning and keynote crowd who will not take a meeting?
  15-20: they come to evaluate and buy; vendor meetings are part of why they \
attend.
  10-14: a mix of learning and evaluating; vendors are part of the draw.
  5-9: mainly learning and keynotes; vendor conversations happen at the \
margins.
  0-4: no buying mindset at all.

Each sub-score needs a one-or-two-sentence `_note` giving the reasoning. The \
notes are the audit trail; a score without one cannot be checked.

HARD CONSTRAINTS.
1. Do NOT add anything for matchmaking programmes. That bonus is applied \
separately and adding it here would double-count it.
2. You have not been told what any of this costs, and you must not speculate. \
Cost never moves a score.
3. Score each event against the bands on its own evidence, never against the \
other events listed with it. The same event must get the same score whichever \
events it is graded beside.
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
{{"scores": [{{"name": str, "starts_on": str|null, "relevance": int, \
"relevance_note": str, "dm_access": int, "dm_access_note": str, \
"engagement": int, "engagement_note": str, "description": str, \
"client_line": str}}]}}

`name` must exactly match the name you were given, and `starts_on` must be \
exactly the date on that event's `dates` line (null if it had none). Two \
editions of one series can share a name; the date is how your answer is \
matched to the edition you graded."""

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
    """One candidate, as the scorer sees it. cost_note is not included.

    Nor, for an event the famous-event audit promoted, is the note saying so.
    That note tells the grader the audit "named this as the more targeted
    alternative", which is a verdict on the very question it is about to
    grade, handed to it before it looks. Every other candidate is graded
    without being told what anybody thought of it, and this one must be too.
    """
    promoted = c.get("audit_verdict") == "promoted"
    bits = ["- %s" % c.get("name")]
    for label, key in (("edition", "edition"), ("where", "city"),
                       ("country", "country"), ("dates", "starts_on"),
                       ("days", "days"), ("industry", "industry"),
                       ("attendees, as published", "attendees"),
                       ("exhibitors, as published", "booths"),
                       ("who it says it is for", "audience_note"),
                       ("found as", "category_fit"),
                       ("organiser", "organizer"), ("site", "website")):
        if promoted and key == "category_fit":
            continue
        if c.get(key):
            value = c[key]
            if key == "starts_on":
                value = _edition(value) or value
            bits.append("  %s: %s" % (label, value))
    return "\n".join(bits)


def _edition(value) -> str:
    """The ISO start date that tells one edition from another, or ""."""
    import datetime
    text = str(value or "").strip()[:10]
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


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
    # The edition the grader says it graded. Echoed back so two editions of
    # one series in one batch cannot be told apart only by a region word the
    # name may not carry ("Money20/20 Europe" 2026 and 2027).
    out["starts_on"] = _edition(raw.get("starts_on")) or None
    return out


def max_uses_for(batch: list) -> int:
    """This batch's search budget: SCORE_SEARCHES_PER_EVENT each, capped."""
    return max(1, min(SCORE_MAX_USES, len(batch or []) * SCORE_SEARCHES_PER_EVENT))


def score_batch(batch: list[dict], profile: dict, rescore_pass: int = 0) -> dict:
    """Score up to BATCH candidates in one call. Never raises.

    `rescore_pass` marks an independent re-grade of borderline events. It
    changes the request text on purpose: event_intel_jobs.reserve_call
    returns the stored reply for a byte-identical request in the same run and
    stage, so a second pass sent with the first pass's exact wording would
    come back as a copy of it and the "median of three" would be one reading
    counted three times.
    """
    from .event_intel_discover import profile_brief
    system = _SYSTEM.format(
        profile=profile_brief(profile),
        where_buyers=rubric.CLASSIFICATION_WHERE_BUYERS_ARE.get(
            profile.get("classification"), "Confirm with the client."))
    user = ("Score these %d events and write each description:\n\n%s"
            % (len(batch), "\n".join(_candidate_brief(c) for c in batch)))
    if rescore_pass:
        user = ("Independent re-grade, pass %d of %d. Grade each event on its "
                "own evidence against the bands, exactly as a first reading "
                "would.\n\n%s" % (rescore_pass + 1, RESCORE_PASSES + 1, user))
    res = claude_websearch.ask(system, user, max_uses=max_uses_for(batch),
                               max_tokens=SCORE_MAX_TOKENS)
    # Counted on every path out of here, including the two refusals below: a
    # scoring pass whose answer could not be read cost exactly what a
    # readable one cost.
    spend = claude_websearch.spend_of(res)
    if res.get("error"):
        # Reader English only. This string is printed under "Scoring
        # reported an error" in a client's report, and the developer detail
        # it used to carry read "Max_tokens: Ran out of output budget before
        # finishing (stop_reason=max_tokens). Raise max_tokens or lower
        # max_uses.." there. The detail goes to the log, where it is useful.
        logger.warning("event_intel_scorer: scoring call failed (%s): %s",
                       res["error"].get("kind"), res["error"].get("detail"))
        return {"scores": {}, "spend": spend,
                "error": "A scoring pass for %d %s failed: %s." % (
                    len(batch), "event" if len(batch) == 1 else "events",
                    claude_websearch.reader_reason(res["error"]))}
    parsed = claude_websearch.extract_json(res.get("text") or "", require="scores")
    if not isinstance(parsed, dict):
        return {"scores": {}, "spend": spend,
                "error": "The scoring pass ran but its answer could not be read."}
    out = {}
    for s in (parsed.get("scores") or []):
        clean = _clean(s)
        if clean:
            out[score_key(clean["name"], clean.get("starts_on"))] = clean
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


def score_key(name: str, starts_on=None) -> tuple:
    """The key `merge` would agree with: the stripped name, its region, and
    the edition's start date.

    name_key alone strips region words, which is right for deciding that
    "MarTech Summit" and "MarTech Summit Europe" are one event and wrong for
    deciding that "Money20/20 USA" and "Money20/20 Europe" are. merge() knows
    that and keeps both; this dict used name_key on its own, so the two
    editions shared one slot and the second one graded overwrote the first.
    Both rows were then stored with one edition's scores, notes and
    description, which reads as a confident grade of the wrong continent.

    The date is the same defect one level down. "Money20/20 Europe" starting
    2026-06-02 and the one starting 2027-06-08 have the same name AND the
    same region, and both were stored with the 2027 edition's scores; across
    batches the winner was whichever batch finished last. An undated reply
    keys with "" and is matched to an edition only when that is unambiguous.
    """
    from .event_intel_discover import name_key, region_key
    return (name_key(name), region_key(name), _edition(starts_on))


def _compatible(a: str, b: str) -> bool:
    """Two editions that could be one: equal, or either undated."""
    return not a or not b or a == b


def _lookup(scores: dict, name: str, starts_on=None,
            others: list | None = None) -> dict | None:
    """This candidate's scores, tolerating a name the grader reworded.

    The prompt asks for the name it was given, back verbatim. Asking is not
    getting, and the exact-key lookup this replaces sent an event with its own
    scores to the unscored bucket whenever the grader returned "SaaStr" for
    "SaaStr Annual". `merge` and `_dedupe_proposals` already treat those as
    one event, so the strict comparison here disagreed with the rest of the
    module about what the same event is.

    Loose matching is accepted ONLY when it is unambiguous: one event wearing
    another's sub-scores is a worse outcome than the miss. `others` is the
    rest of the batch, as (name, starts_on) pairs, and it is what makes
    "unambiguous" checkable. Checking only the returned names was not enough:
    with "Fintech Meetup" and "Fintech Meetup Asia" both in a batch and only
    the Asia edition graded, "Fintech Meetup" loosely matched the one reply
    there was and was stored with Asia's 38. A reply is never borrowed when
    it is an exact answer for another candidate, or when another candidate
    matches it just as loosely.
    """
    from .event_intel_discover import names_match
    key = score_key(name or "", starts_on)
    if not key[0]:
        return None
    exact = scores.get(key)
    if exact is not None:
        return exact
    rivals = [(n, score_key(n or "", d)) for n, d in (others or [])]
    rivals = [(n, k) for n, k in rivals if k != key and k[0]]

    # Same name and region, one side undated: the grader dropped or
    # reformatted the date. Taken only when no other edition in the batch
    # could be the one it meant.
    direct = [v for k, v in scores.items()
              if k[:2] == key[:2] and _compatible(k[2], key[2])]
    if direct:
        if len(direct) == 1 and not any(rk[:2] == key[:2] for _, rk in rivals):
            return direct[0]
        return None

    # names_match compares regions as well as names, so a reply that dropped
    # the region cannot be matched to one edition while another edition of the
    # same series is also in the dict: that comes back ambiguous and the event
    # is reported unscored, which is the honest answer.
    hits = []
    for k, v in scores.items():
        if not _compatible(k[2], key[2]):
            continue
        replied = v.get("name") or k[0]
        if not names_match(name or "", replied):
            continue
        # Another candidate in the batch matches this reply at least as
        # well. That covers the reply being that candidate's own exact
        # answer (the Fintech Meetup Asia case) and the reply fitting two
        # candidates equally loosely; either way it is not this one's.
        if any(names_match(n or "", replied) and _compatible(rk[2], k[2])
               for n, rk in rivals):
            continue
        hits.append(v)
    return hits[0] if len(hits) == 1 else None


def _resolve(batch: list[dict], scores: dict) -> list:
    """Each candidate in `batch` matched to its own reply, or None.

    Replies are resolved against the batch that produced them and nothing
    else. A grader can only have graded what it was shown, and merging every
    batch's replies into one dict first is what let the last batch to finish
    overwrite another batch's edition of the same series.
    """
    out = []
    for i, c in enumerate(batch):
        others = [(o.get("name"), o.get("starts_on"))
                  for j, o in enumerate(batch) if j != i]
        out.append(_lookup(scores or {}, c.get("name") or "",
                           c.get("starts_on"), others))
    return out


def _total_of(s: dict, c: dict) -> int:
    """The total rubric.score would give these readings, bonus included.

    Only used to decide what is borderline; the stored total is still derived
    downstream in event_intel_store.normalise_candidate.
    """
    return rubric.score(s[rubric.DIM_RELEVANCE], s[rubric.DIM_DM_ACCESS],
                        s[rubric.DIM_ENGAGEMENT],
                        organizer_run=bool(c.get("organizer_run")),
                        matchmaking_evidence=str(c.get("matchmaking_evidence")
                                                 or ""))["total"]


def borderline_distance(s: dict, c: dict) -> int | None:
    """How far this reading sits from the nearest cut-off, when it is within
    rubric.BORDERLINE_MARGIN of one; None when it is clear of every line."""
    if any(s.get(d) is None for d in rubric.DIMENSIONS):
        return None
    total = _total_of(s, c)
    gaps = [abs(s[rubric.DIM_RELEVANCE] - rubric.RELEVANCE_GATE)]
    gaps += [abs(total - line) for line in rubric.BORDERLINE_TOTALS]
    nearest = min(gaps)
    return nearest if nearest <= rubric.BORDERLINE_MARGIN else None


def _median_reading(readings: list[dict]) -> dict:
    """Per-dimension median of full readings, with the note that goes with
    each chosen value. The lower middle on an even count, so a missing third
    reading can never round a near miss up."""
    import statistics
    out = dict(readings[0])
    for dim in rubric.DIMENSIONS:
        value = statistics.median_low([r[dim] for r in readings])
        chosen = next(r for r in readings if r[dim] == value)
        out[dim] = value
        out[dim + "_note"] = chosen.get(dim + "_note")
    return out


def _rescore_note(readings: list[dict]) -> str:
    spread = max(max(r[d] for r in readings) - min(r[d] for r in readings)
                 for d in rubric.DIMENSIONS)
    times = {2: "twice", 3: "three times"}.get(len(readings),
                                               "%d times" % len(readings))
    shown = ("the median is shown" if len(readings) % 2
             else "the lower of the middle readings is shown")
    return ("%s %d points of a cut-off, so it was scored %s and %s. The "
            "readings differed by up to %d %s on a single dimension."
            % (rubric.RESCORE_NOTE_PREFIX, rubric.BORDERLINE_MARGIN, times,
               shown, spread, "point" if spread == 1 else "points"))


def _run(jobs: list, profile: dict, errors: list, spends: list) -> list:
    """Run score_batch jobs concurrently; results in job order."""
    results = [None] * len(jobs)
    if not jobs:
        return results
    with ContextExecutor(max_workers=min(MAX_CONCURRENCY, len(jobs))) as pool:
        futures = {pool.submit(score_batch, b, profile, **kw): i
                   for i, (b, kw) in enumerate(jobs)}
        for fut in concurrent.futures.as_completed(futures):
            try:
                r = fut.result()
            except Exception:
                # The exception text is a developer's, and `errors` is
                # printed to the client. It goes to the log.
                logger.exception("event_intel_scorer: batch crashed")
                errors.append("A scoring pass failed before it returned "
                              "anything.")
                continue
            spends.append(r.get("spend"))
            if r.get("error"):
                errors.append(r["error"])
            results[futures[fut]] = r
    return results


def score_all(candidates: list[dict], profile: dict) -> dict:
    """Score every candidate, in concurrent batches.

    A candidate the scorer never returned is kept and marked UNSCORED rather
    than dropped or defaulted to zero. Zero would rank it last, which reads as
    "we judged this and it is bad"; dropping it reads as "this does not
    exist". Neither is what happened.

    Then the borderline re-score: anything whose first reading sits within
    rubric.BORDERLINE_MARGIN of a cut-off is graded RESCORE_PASSES more times
    (one extra batch per pass, at most RESCORE_MAX_BATCHES extra calls) and
    keeps the per-dimension median. The spend of those calls is in `spend`
    like every other call's, and `rescore` says what was re-scored.
    """
    candidates = list(candidates or [])
    index_batches = deal(list(range(len(candidates))), BATCH)
    errors: list[str] = []
    spends: list = []
    results = _run([([candidates[i] for i in idx], {}) for idx in index_batches],
                   profile, errors, spends)

    readings: dict = {}
    for idx, r in zip(index_batches, results):
        batch = [candidates[i] for i in idx]
        for i, s in zip(idx, _resolve(batch, (r or {}).get("scores") or {})):
            if s:
                readings[i] = [s]

    # Which first readings sit on a line, closest first.
    near = sorted(((d, (candidates[i].get("name") or "").lower(), i)
                   for i, rs in readings.items()
                   if not rubric.has_finished(candidates[i])
                   for d in [borderline_distance(rs[0], candidates[i])]
                   if d is not None))
    room = BATCH * RESCORE_MAX_BATCHES // max(1, RESCORE_PASSES)
    chosen = [i for _, _, i in near[:min(BATCH, room)]]
    skipped = [candidates[i].get("name") for _, _, i in near[len(chosen):]]
    rescore_spends: list = []
    if chosen:
        batch = [candidates[i] for i in chosen]
        passes = min(RESCORE_PASSES, RESCORE_MAX_BATCHES)
        again = _run([(batch, {"rescore_pass": p + 1}) for p in range(passes)],
                     profile, errors, rescore_spends)
        for r in again:
            for i, s in zip(chosen, _resolve(batch, (r or {}).get("scores") or {})):
                if s and all(s.get(d) is not None for d in rubric.DIMENSIONS):
                    readings[i].append(s)
    spends.extend(rescore_spends)

    scored, unscored, rescored = [], [], []
    for i, c in enumerate(candidates):
        c = dict(c)
        rs = readings.get(i)
        s = rs[0] if rs else None
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
        if len(rs) > 1:
            s = _median_reading(rs)
            c["rescored"] = True
            c["score_readings"] = [{d: r[d] for d in rubric.DIMENSIONS}
                                   for r in rs]
            c["score_spread"] = {d: max(r[d] for r in rs) - min(r[d] for r in rs)
                                 for d in rubric.DIMENSIONS}
            c["rescore_note"] = _rescore_note(rs)
            rescored.append(c.get("name"))
        for dim in rubric.DIMENSIONS:
            c[dim] = s[dim]
            c[dim + "_note"] = s[dim + "_note"]
        c["description"] = s["description"]
        c["client_line"] = s["client_line"]
        scored.append(c)
    return {"scored": scored, "unscored": unscored,
            "errors": errors, "batches": len(index_batches),
            "rescore": {"events": rescored,
                        "calls": len(rescore_spends),
                        "not_rescored": skipped,
                        "spend": claude_websearch.spend_sum(*rescore_spends)},
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
