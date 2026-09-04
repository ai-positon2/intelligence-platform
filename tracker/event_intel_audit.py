"""Steps 3 and 6 of the recommendation: the two checks that stop a list being
the same list everybody gets.

STEP 3, the famous-event audit. The source skill:

    For every marquee event still on the list [...] write one line justifying
    why this specific event beats a more targeted alternative for THIS
    client's ICP [...] If you can't justify it, replace it with the targeted
    alternative.

A model asked to justify something it just chose will justify it. So the
justification here is not free text: it must NAME the more targeted
alternative it was weighed against, and that alternative must be a real event
found by search. A justification with no named alternative is not a
comparison, it is a restatement, and this module refuses to record it as a
pass. That single requirement is the difference between an audit and a
formality.

STEP 6, the cross-client pattern check. The skill:

    Before finalizing, ask: would this same list appear for a different client
    in a different vertical? If yes, it's too generic.

Asking a model to imagine that is the weakest possible version of the check,
because the previous lists are not in its context. They are, however, in
Postgres. So this measures the overlap against the lists this user was
actually handed before, for actually different clients. That is the one place
where having a database beats having a very good prompt, and it turns the
skill's rhetorical question into a number.

When there is nothing to compare against, the check reports that it could not
run. A first-ever run showing "0% overlap" would read as a pass for a test
that never happened.
"""

from __future__ import annotations

import logging

from . import claude_websearch
from .event_intel_discover import name_key, names_match

logger = logging.getLogger(__name__)

VERDICT_KEPT = "kept"
VERDICT_CUT = "cut"
VERDICT_UNAUDITED = "unaudited"
# An event that reached the list because the audit named it as a better fit
# than a marquee event it cut. It was never discovered by a category search,
# so it must never be presented as though it had been.
VERDICT_PROMOTED = "promoted"

# Each promotion costs one resolve_event call, which is a live search. Three
# is enough to replace the marquee events a normal run cuts without turning
# the audit into a second discovery pass.
MAX_PROMOTED = 3

# Above this share of a prior list for a DIFFERENT client, the list is
# generic. The skill's remedy at that point is to replace three to five famous
# events with vertical-specific or regional alternatives.
GENERIC_THRESHOLD = 0.5

_AUDIT_SYSTEM = """You audit famous conferences off a shortlist. Your default \
assumption is that a marquee event is on the list out of habit rather than fit.

THE CLIENT
{profile}

WHERE THIS CLIENT'S BUYERS PHYSICALLY ARE AT AN EVENT: {where_buyers}

For EACH event below, use web search to find the single most targeted \
alternative that serves this exact buyer profile better, then decide.

RULES.
1. You MUST name a real alternative event you confirmed exists by searching. \
"No alternative exists" is almost never true and is not an acceptable answer \
for a broad market; if you genuinely cannot find one after searching, say so \
in `alternative_note` and set `alternative` to null, and understand that the \
famous event will then be CUT, because an unaudited marquee name is exactly \
what this step removes.
2. Verdict "kept" means this famous event beats that named alternative FOR \
THIS CLIENT, and `why` explains it by referring to the client's buyer roles \
and verticals above. Scale, prestige and "everyone goes" are not reasons. A \
diluted flagship loses to a dense vertical summit.
3. Verdict "cut" means the alternative is better. Say which one and why in one \
line.
4. Be willing to cut. A shortlist where every famous event survived its own \
audit is a shortlist that was not audited.

Respond with ONLY a JSON object:
{{"audits": [{{"name": str, "verdict": "kept"|"cut", "alternative": str|null, \
"alternative_website": str|null, "alternative_note": str|null, "why": str}}]}}

`name` must exactly match the event name you were given."""


def _profile_line(profile: dict) -> str:
    from .event_intel_discover import profile_brief
    return profile_brief(profile)


def audit_famous(candidates: list[dict], profile: dict) -> dict:
    """Weigh every famous candidate against a named, searched alternative.

    Returns {"verdicts": {name_key: {...}}, "cut": [...], "kept": [...],
             "checked": int, "error": str|None}.

    A candidate that is not famous is never audited and never appears here;
    the caller marks those `unaudited`, which is a different thing from
    `kept` and is stored as such.
    """
    famous = [c for c in (candidates or []) if c.get("famous")]
    out = {"verdicts": {}, "cut": [], "kept": [], "checked": len(famous),
           "error": None}
    if not famous:
        return out

    from . import event_intel_rubric as rubric
    listing = "\n".join(
        "- %s%s%s" % (c["name"],
                      (" (%s)" % c["city"]) if c.get("city") else "",
                      (" - %s" % c["audience_note"][:200]) if c.get("audience_note") else "")
        for c in famous)
    system = _AUDIT_SYSTEM.format(
        profile=_profile_line(profile),
        where_buyers=rubric.CLASSIFICATION_WHERE_BUYERS_ARE.get(
            profile.get("classification"), "Confirm with the client."))
    res = claude_websearch.ask(
        system, "Audit these %d famous events:\n%s" % (len(famous), listing),
        max_uses=10, max_tokens=8000)

    if res.get("error"):
        # The audit not running is recorded, never silently skipped. An
        # unaudited flagship on the list is the thing this step exists to
        # catch, so the report has to be able to say the check did not happen.
        out["error"] = "%s: %s" % (res["error"]["kind"], res["error"]["detail"])
        return out

    parsed = claude_websearch.extract_json(res.get("text") or "", require="audits")
    if not isinstance(parsed, dict):
        out["error"] = "The famous-event audit ran but its answer could not be read."
        return out

    for a in (parsed.get("audits") or []):
        if not isinstance(a, dict):
            continue
        key = name_key(str(a.get("name") or ""))
        if not key:
            continue
        alternative = str(a.get("alternative") or "").strip()
        why = str(a.get("why") or "").strip()[:600]
        claimed = str(a.get("verdict") or "").strip().lower()

        # The enforcement. A "kept" with no named alternative is a restatement
        # dressed as a comparison, and it is downgraded to a cut rather than
        # accepted, because the comparison the step requires never happened.
        if claimed == VERDICT_KEPT and not alternative:
            verdict = VERDICT_CUT
            why = ("Cut: kept was claimed but no more targeted alternative was "
                   "named, so nothing was actually weighed against it. "
                   + why)[:600]
        else:
            verdict = VERDICT_KEPT if claimed == VERDICT_KEPT else VERDICT_CUT

        website = str(a.get("alternative_website") or "").strip()
        if website and not website.lower().startswith(("http://", "https://")):
            website = ""
        rec = {"verdict": verdict, "alternative": alternative or None,
               "alternative_website": website or None,
               "alternative_note": str(a.get("alternative_note") or "")[:400] or None,
               "why": why,
               # The name the audit answered under, kept so that a reply which
               # echoes back more than the event's name can still be matched to
               # the candidate it is about. See _verdict_for below.
               "name": str(a.get("name") or "")[:250]}
        out["verdicts"][key] = rec
        (out["kept"] if verdict == VERDICT_KEPT else out["cut"]).append(dict(rec))

    # A call that succeeded, was given famous events, and yielded not one
    # usable verdict has audited nothing: it is a parse or shape failure
    # wearing a success. Reported as a failed audit, because the alternative
    # is cutting every marquee event on the strength of a reply nobody could
    # read, which is the same silent wrong answer this module already refuses
    # to give on a transport error.
    if not out["verdicts"]:
        out["error"] = (
            "the call succeeded but returned no usable verdict for %s, so "
            "nothing was actually weighed"
            % ("the one marquee event it was given" if len(famous) == 1
               else "any of the %d marquee events it was given" % len(famous)))
    return out


def _verdict_for(name: str, verdicts: dict) -> dict | None:
    """This candidate's verdict, tolerating what the prompt itself added.

    The audit is SHOWN each marquee event as "Name (City) - audience note",
    because the city is what tells one edition from another. A reply that
    repeats the name it was given therefore comes back carrying the city, and
    an exact key lookup misses the candidate the verdict is about. The event
    is then cut with the reason "the famous-event audit returned no verdict
    for this event" while its verdict sits unread in the same dictionary.

    Found in the live run of 2026-09-03: INBOUND was audited, weighed against
    B2B Marketing Exchange, cut on the merits with a written reason, and then
    cut again for never having been audited. The reason the reader saw was
    the false one, and the alternative the audit had named was never promoted
    because the verdict it came attached to was treated as missing.

    The loose match is only ever accepted when it is UNAMBIGUOUS. Two marquee
    events whose names contain one another are exactly where guessing would
    staple one event's verdict onto another, and a wrong verdict is worse
    than the missing one this is fixing.
    """
    key = name_key(name or "")
    if not key:
        return None
    exact = verdicts.get(key)
    if exact is not None:
        return exact
    hits = [v for k, v in verdicts.items()
            if k != key and names_match(name or "", v.get("name") or k)]
    return hits[0] if len(hits) == 1 else None


def apply_audit(candidates: list[dict], audit: dict) -> list[dict]:
    """Stamp each candidate with its verdict, and drop the ones that were cut.

    A famous event the audit never returned a verdict for is CUT, not kept.
    The skill's rule is that a marquee name justifies its place or goes, and
    "the auditor did not get to it" is not a justification. The exception is
    when the audit itself failed to run: then nothing is cut, because cutting
    every flagship on a transport error would be a silent, wrong answer, and
    the run reports that the audit did not happen instead.
    """
    audit_ran = not audit.get("error")
    verdicts = audit.get("verdicts") or {}
    out = []
    for c in candidates or []:
        c = dict(c)
        if not c.get("famous"):
            c["audit_verdict"] = VERDICT_UNAUDITED
            c["audit_note"] = None
            out.append(c)
            continue
        if not audit_ran:
            c["audit_verdict"] = VERDICT_UNAUDITED
            c["audit_note"] = ("This is a marquee event and the famous-event "
                               "audit could not run, so it has not been weighed "
                               "against a more targeted alternative.")
            out.append(c)
            continue
        v = _verdict_for(c.get("name") or "", verdicts)
        if not v:
            c["audit_verdict"] = VERDICT_CUT
            c["audit_note"] = ("Cut: the famous-event audit returned no verdict "
                               "for this event, so it was never weighed against "
                               "a more targeted alternative.")
            # Recorded, not merely dropped. Writing the reason onto a row that
            # is then discarded means the reason is never stored and the event
            # leaves the report without appearing in any list, while the
            # summary's cut count stays at zero. A vanished event with a
            # confident "0 were cut" beside it is the worst output this step
            # can produce.
            audit.setdefault("cut", []).append(
                {"name": c.get("name"), "verdict": VERDICT_CUT,
                 "alternative": None, "why": c["audit_note"],
                 "no_verdict": True})
            continue
        c["audit_verdict"] = v["verdict"]
        note = v["why"]
        if v.get("alternative"):
            note = "%s Weighed against: %s." % (note, v["alternative"])
        c["audit_note"] = note[:1200]
        if v["verdict"] == VERDICT_CUT:
            continue
        out.append(c)
    return out


# ── Step 6: the cross-client pattern check, measured ──────────────────────


def alternatives_to_promote(audit: dict, candidates: list[dict]) -> list[dict]:
    """The alternatives worth putting on the list, in audit order.

    Only alternatives from CUT verdicts. A KEPT verdict means the marquee
    event justified its place AGAINST the named alternative, so that
    alternative lost the comparison; promoting it would add an event the
    audit had just implicitly rejected.

    Deduped two ways. Against the candidates already on the list, because a
    category search may well have found the same event, and a second copy
    would be scored twice and could occupy two slots under the cap. And
    against each other, because two cut marquee events routinely point at the
    same replacement.
    """
    out: list[dict] = []
    seen: set = set()
    for entry in (audit or {}).get("cut") or []:
        name = str((entry or {}).get("alternative") or "").strip()
        if not name:
            continue
        key = name_key(name)
        if not key or key in seen:
            continue
        if any(names_match(name, c.get("name") or "") for c in candidates or []):
            continue
        seen.add(key)
        out.append({
            "name": name,
            "website": entry.get("alternative_website") or None,
            "note": entry.get("alternative_note") or None,
            "replaces": str(entry.get("name") or "").strip() or None,
        })
    return out


def promote_alternatives(audit: dict, candidates: list[dict],
                         resolver=None, cap: int = MAX_PROMOTED,
                         replaced_from: list[dict] | None = None) -> dict:
    """Turn the audit's named alternatives into scoreable candidates.

    The gap this closes: the audit would cut a marquee event, name a better
    one and explain in detail why it is better, and then nothing ever looked
    the better one up. A run could end with an empty list while the system
    itself had already identified the event the client should attend. Live on
    2026-09-02: MarTech Conference was cut for being fully online with no
    exhibit floor, INBOUND was named as the in-person alternative with a real
    expo floor, and INBOUND was never scored, never stored and never shown.

    Every promotion is CONFIRMED before it is used. The audit names an event;
    it does not establish that the event exists, when it runs or where. That
    is the same standard discovery is held to, and skipping it here would put
    a conference on a client's travel calendar on the strength of one
    sentence written while cutting something else.

    An alternative that cannot be confirmed is REPORTED, never dropped and
    never injected half-formed. "The audit recommended this and it could not
    be confirmed" is a fact the reader needs; silence would hide the same gap
    this function exists to close.

    Returns {"promoted": [...], "unconfirmed": [...], "considered": int,
             "not_attempted": [...]}.
    """
    from . import event_intel_rubric as rubric
    if resolver is None:
        from .event_intel_resolve import resolve_event as resolver

    wanted = alternatives_to_promote(audit, candidates)
    out: dict = {"promoted": [], "unconfirmed": [], "considered": len(wanted),
                 "not_attempted": []}
    if not wanted:
        return out

    # By category of the event being replaced, so a promoted event lands in
    # the same slot on the report as the one it stands in for.
    #
    # Looked up in `replaced_from` rather than in `candidates`, because they
    # are two different lists doing two different jobs. `candidates` is what
    # is already ON the list, and is what a promotion is deduped against. The
    # event being replaced was just CUT, so by definition it is not on that
    # list, and looking it up there always failed: the promoted event got
    # category=None and was dropped by the store for having no category slot.
    # `replaced_from` is the pre-audit list, where the cut event still exists.
    by_name = {name_key(c.get("name") or ""): c
               for c in (replaced_from if replaced_from is not None
                         else candidates) or []}

    for alt in wanted[:max(0, cap)]:
        try:
            res = resolver(alt["name"])
        except Exception as e:                       # never raises upward
            logger.warning("event_intel_audit: resolving alternative %r failed: %s",
                           alt["name"], e)
            res = {"ok": False, "reasoning": "The lookup failed: %s" % str(e)[:200]}
        if not (res or {}).get("ok") or not (res or {}).get("event"):
            out["unconfirmed"].append({
                "name": alt["name"], "replaces": alt["replaces"],
                "why": (str((res or {}).get("reasoning") or "").strip()
                        or "The event could not be confirmed."),
                "confidence": (res or {}).get("confidence"),
            })
            continue
        # A replacement has to be attendable. The lookup is allowed to return
        # the most recent past edition when no future one is announced, which
        # is right for reading a roster and wrong here: offering an event that
        # has already happened in place of one just cut leaves the client with
        # a line they cannot act on, dressed as a recommendation.
        if rubric.has_finished(res["event"]):
            out["unconfirmed"].append({
                "name": alt["name"], "replaces": alt["replaces"],
                "why": ("The only edition that could be confirmed (%s) has "
                        "already finished, and no future one is announced, so "
                        "there is nothing here to attend."
                        % (res["event"].get("starts_on") or "date unknown")),
                "confidence": (res or {}).get("confidence"),
                "finished": True,
            })
            continue
        replaced = by_name.get(name_key(alt["replaces"] or ""))
        candidate = _candidate_from_alternative(res, alt, replaced)
        # A promoted event with no category is dropped by the store, silently,
        # after a live lookup and a live scoring call have already been spent
        # on it, while the summary goes on saying it was added to the list.
        # Refused here instead, and named, so the failure is in the report
        # rather than in a log line nobody reads.
        if candidate.get("category") not in rubric.CATEGORIES:
            out["unconfirmed"].append({
                "name": alt["name"], "replaces": alt["replaces"],
                "why": ("This was confirmed as a real, upcoming event, but the "
                        "marquee event it stands in for could not be matched to "
                        "one of the six discovery categories, so there is no "
                        "slot on the list to put it in."),
                "confidence": (res or {}).get("confidence"),
            })
            continue
        out["promoted"].append(candidate)

    # Named, never looked at, because the cost ceiling was reached. Said out
    # loud rather than trimmed away, so the reader can ask for the rest.
    for alt in wanted[max(0, cap):]:
        out["not_attempted"].append({"name": alt["name"],
                                     "replaces": alt["replaces"]})
    return out


def _candidate_from_alternative(res: dict, alt: dict,
                                replaced: dict | None) -> dict:
    """One confirmed alternative, in the shape the scorer and store expect.

    `famous` is forced FALSE regardless of how well known the event is. It is
    the flag that decides what gets audited, and a promoted event auditing
    into another promotion would recurse. It has also already been through the
    comparison this step exists to force: it IS the more targeted alternative.
    """
    ev = res.get("event") or {}
    sources = [p.get("url") for p in (res.get("pages") or [])
               if isinstance(p, dict) and p.get("url")][:8]
    website = ev.get("website") or alt.get("website")
    if website and website not in sources:
        sources.insert(0, website)

    replaces = alt.get("replaces")
    note = ("On this list because the famous-event audit cut %s and named this "
            "as the more targeted alternative. It was then looked up and "
            "confirmed separately; it was not found by a category search."
            % (replaces or "a marquee event"))
    if alt.get("note"):
        note = "%s The audit's reason: %s" % (note, alt["note"])

    return {
        "name": ev.get("name") or alt["name"],
        "edition": ev.get("edition"),
        "website": website or None,
        "organizer": ev.get("organizer"),
        "starts_on": ev.get("starts_on"),
        "ends_on": ev.get("ends_on"),
        "country": None,
        "city": ev.get("location"),
        "days": None,
        "industry": None,
        "attendees": ev.get("stated_size"),
        "booths": None,
        "audience_note": ev.get("audience_note"),
        "format": ev.get("format"),
        "cost_note": None,
        "organizer_run": False,
        # Not carried over from the audit's prose. The bonus needs evidence
        # the rubric has read, and a sentence about why one event beats
        # another is not that.
        "matchmaking_evidence": None,
        "famous": False,
        "category": (replaced or {}).get("category"),
        "category_fit": note[:500],
        "confidence": ev.get("confidence") or "medium",
        "sources": sources,
        "audit_verdict": VERDICT_PROMOTED,
        "audit_note": note[:800],
    }


def genericness(names: list[str], prior_runs: list[dict],
                this_client: str | None = None) -> dict:
    """How much of this list has already been handed to a different client.

    Pure. `prior_runs` comes from event_intel_store.prior_candidate_names(),
    each row carrying that run's client_name and the events it produced.

    Overlap is measured as the share of THIS list that also appeared in the
    prior one, not as a symmetric similarity: a five-event list wholly
    contained in a fifteen-event list is completely generic, and a symmetric
    score would call that a third.
    """
    mine = {name_key(n) for n in (names or []) if name_key(n)}
    if not mine:
        return {"measured": False, "flagged": False, "checked": 0,
                "why_not_measured": "This run produced no events to compare.",
                "worst": None, "comparisons": [], "advice": ""}

    me = (this_client or "").strip().lower()
    comparisons = []
    for run in (prior_runs or []):
        other_client = (run.get("client_name") or "").strip()
        if me and other_client.lower() == me:
            # Same client re-running SHOULD overlap heavily. Counting that as
            # genericness would flag the one case where a stable list is the
            # correct answer.
            continue
        theirs = {name_key(n) for n in (run.get("names") or []) if name_key(n)}
        if not theirs:
            continue
        shared = sorted(mine & theirs)
        comparisons.append({
            "run_id": run.get("id"),
            "client_name": other_client or "an earlier run",
            "classification": run.get("classification"),
            "overlap": round(len(shared) / len(mine), 3),
            "shared": shared,
            "shared_count": len(shared),
        })

    if not comparisons:
        return {
            "measured": False, "flagged": False, "checked": 0, "worst": None,
            "comparisons": [],
            "why_not_measured": (
                "There are no completed recommendations for a different client "
                "to compare this list against yet, so the cross-client check "
                "could not run. It becomes meaningful from the second client "
                "onwards."),
            "advice": "",
        }

    comparisons.sort(key=lambda c: -c["overlap"])
    worst = comparisons[0]
    flagged = worst["overlap"] >= GENERIC_THRESHOLD
    advice = ""
    if flagged:
        # Deliberately does NOT name the other client. This string is the
        # one that reaches the deliverable, and the deliverable leaves the
        # building: naming whose list it matched would put one client's
        # engagement into another client's report. The name stays available
        # on `worst` for whoever is running the analysis.
        advice = (
            "%d%% of this list was also recommended to a different client on "
            "this account. Two clients with different ICPs should not get the "
            "same events. Replace three to five of the famous names with "
            "vertical-specific or regional alternatives before acting on it."
            % round(worst["overlap"] * 100))
    return {"measured": True, "flagged": flagged, "checked": len(comparisons),
            "worst": worst, "comparisons": comparisons[:5],
            "why_not_measured": "", "advice": advice}
