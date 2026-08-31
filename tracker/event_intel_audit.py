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
from .event_intel_discover import name_key

logger = logging.getLogger(__name__)

VERDICT_KEPT = "kept"
VERDICT_CUT = "cut"
VERDICT_UNAUDITED = "unaudited"

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

    parsed = claude_websearch.extract_json(res.get("text") or "")
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
               "why": why}
        out["verdicts"][key] = rec
        (out["kept"] if verdict == VERDICT_KEPT else out["cut"]).append(
            {"name": str(a.get("name") or "")[:250], **rec})

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
        v = verdicts.get(name_key(c.get("name") or ""))
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
        advice = (
            "%d%% of this list was also recommended to %s. Two clients with "
            "different ICPs should not get the same events. Replace three to "
            "five of the famous names with vertical-specific or regional "
            "alternatives before acting on it."
            % (round(worst["overlap"] * 100), worst["client_name"]))
    return {"measured": True, "flagged": flagged, "checked": len(comparisons),
            "worst": worst, "comparisons": comparisons[:5],
            "why_not_measured": "", "advice": advice}
