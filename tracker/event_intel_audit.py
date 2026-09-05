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

# How many replacements a run will actually put on the list. Three is enough
# to replace the marquee events a normal run cuts without turning the audit
# into a second discovery pass.
MAX_PROMOTED = 3

# How many lookups it will spend getting there, which is the COST. These were
# one number, and a live run showed why they cannot be: the audit named four
# alternatives, the cap allowed three lookups, and two of those three were
# confirmed and then dropped for an unrelated reason. Yield was one event out
# of four named, while the fourth, SaaStr Annual, sat unexamined because
# lookups spent on events that never made the list had been counted against
# it. A cap on attempts stops the run at the wrong moment: it should bound
# what this step is allowed to spend, and keep going until it has the
# replacements or has run out of budget.
MAX_PROMOTION_LOOKUPS = 5

# Above this share of a prior list for a DIFFERENT client, the list is
# generic. The skill's remedy at that point is to replace three to five famous
# events with vertical-specific or regional alternatives.
GENERIC_THRESHOLD = 0.5

# One call per famous event, and these are that call's budgets.
#
# The shape here used to be a single call over every famous event at
# max_uses=10. Input cost grows with the SQUARE of one call's search count,
# because every search round re-sends the whole accumulated conversation, so
# that shape was the most expensive in the codebase: a live run measured
# 152,820 input tokens and 241 seconds for one ten-search audit, against
# 50,829 to 58,918 for the six-search calls beside it.
#
# Splitting it does not just spread that cost, it REDUCES it. N searches over
# k calls costs about N**2/k instead of N**2, so five three-search calls buy
# fifteen searches for roughly 69k input tokens where one ten-search call
# bought ten for 153k. Each event also gets three searches to itself where
# the single call had ten to divide among five, so the per-event search budget
# went UP while the bill went down.
#
# Two things beyond cost made this worth the change. A truncated single reply
# reported EVERY marquee event on the client's list as unaudited, and that
# reply overran its budget in the one run we measured (11,754 output tokens
# against 8,000). And the single call had to echo each event's name back so
# its verdicts could be matched up, which is the source of the city-suffix
# name-matching bug that has now cost real events twice; a call about one
# event needs no echo, because the caller already knows which event it asked
# about.
AUDIT_MAX_USES = 3
# Held at the floor every searching stage is held to, not trimmed to what one
# verdict needs. max_tokens is a CEILING, not a charge: only tokens actually
# generated are billed, so a generous limit costs nothing and a tight one
# throws away a search that has already been paid for. Trimming this was how
# a live run lost two on-profile events at the confirm stage.
AUDIT_MAX_TOKENS = 9000
# Concurrent audits in flight. The audits are independent, so the stage's wall
# time is one call rather than the sum, and this is the same ceiling
# event_intel_discover applies to its own fan-out.
AUDIT_MAX_INFLIGHT = 3

_AUDIT_SYSTEM = """You audit ONE famous conference off a client's shortlist. \
Your default assumption is that a marquee event is on the list out of habit \
rather than fit.

THE CLIENT
{profile}

WHERE THIS CLIENT'S BUYERS PHYSICALLY ARE AT AN EVENT: {where_buyers}

Use web search to find the single most targeted alternative that serves this \
exact buyer profile better than the event below, then decide.

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
5. You have ONE event to judge and a small search budget. Spend it on finding \
the alternative, not on studying the event you were given, and write your \
answer while you still have room to finish it.

Respond with ONLY a JSON object:
{{"verdict": "kept"|"cut", "alternative": str|null, \
"alternative_website": str|null, "alternative_note": str|null, "why": str}}"""


def _profile_line(profile: dict) -> str:
    from .event_intel_discover import profile_brief
    return profile_brief(profile)


def _event_label(c: dict) -> str:
    """One famous event, as the audit is shown it."""
    return "%s%s%s" % (c.get("name") or "",
                       (" (%s)" % c["city"]) if c.get("city") else "",
                       (" - %s" % c["audience_note"][:200])
                       if c.get("audience_note") else "")


def _record(cand: dict, a: dict) -> dict:
    """One reply, as a verdict record for the candidate it was asked about.

    Every free-text field here is the model's own written prose (a name it
    chose for an alternative, a reason, a caveat), so every one of them goes
    through strip_em_dash: house style has none, and `alternative` in
    particular becomes `replaces` for a promotion and a name printed directly
    in the report's cut list, not just a sentence buried in `why`.
    """
    alternative = claude_websearch.strip_em_dash(
        str(a.get("alternative") or "").strip())
    why = claude_websearch.strip_em_dash(str(a.get("why") or "").strip())[:600]
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
    return {"verdict": verdict, "alternative": alternative or None,
            "alternative_website": website or None,
            "alternative_note": claude_websearch.strip_em_dash(
                str(a.get("alternative_note") or ""))[:400] or None,
            "why": why,
            # The CANDIDATE's name, never one echoed back by the model. One
            # call judges one known event, so there is nothing to match up and
            # no way for a reply that repeats "Adobe Summit (Las Vegas)" to
            # key itself away from the row it is about.
            "name": str(cand.get("name") or "")[:250]}


def _audit_one(cand: dict, system: str) -> dict:
    """One famous event, weighed against one searched alternative.

    Returns {"rec": dict|None, "error": str|None, "spend": {...}}. Never
    raises: a failure here must cost its own event's verdict and nothing
    else, which is the whole point of splitting the call up.
    """
    res = claude_websearch.ask(
        system, "Audit this famous event:\n- %s" % _event_label(cand),
        max_uses=AUDIT_MAX_USES, max_tokens=AUDIT_MAX_TOKENS)
    # Counted before any of the ways this reply can be refused below. The
    # search is billed whether or not its answer turns out to be usable, and
    # an audit that gets thrown away is the version worth seeing.
    box = {"rec": None, "error": None, "spend": claude_websearch.spend_of(res)}

    if res.get("error"):
        box["error"] = "%s: %s" % (res["error"]["kind"], res["error"]["detail"])
        return box

    # The same refusal event_intel_discover applies to a category search and
    # event_intel_recover to a recovered roster. It matters more here than in
    # either: this reply CUTS an event off a client's list and names its
    # replacement, and the rule at the top of this module is that the
    # alternative "must be a real event found by search". A reply that ran no
    # search is a recollection, and acting on it removes a real recommendation
    # and promotes a remembered one.
    if not res.get("search_count"):
        box["error"] = ("it was answered without a single search being run, so "
                        "its verdict was recalled rather than checked")
        return box

    # Read off the reply's OWN outermost object, never by scanning for a
    # "verdict" key wherever it appears. `extract_json(require=...)` walks
    # forward until some balanced object carries the key, which for a
    # single-object schema means a reply in a shape nobody asked for gets
    # rifled for anything verdict-shaped inside it: a `{"verdicts": [...]}`
    # envelope would yield its first row, and this function would then cut a
    # real marquee event off a client's list on the strength of a reply it did
    # not actually understand. The envelope has to be one of the two we asked
    # for, or the answer is unreadable.
    top = claude_websearch.extract_json(res.get("text") or "")
    parsed = None
    if isinstance(top, dict):
        if "verdict" in top:
            parsed = top
        elif isinstance(top.get("audits"), list) and top["audits"]:
            # One verdict wrapped in the list envelope this prompt used to ask
            # for is still a complete answer to the question, and refusing it
            # would discard a live search already paid for.
            first = top["audits"][0]
            parsed = first if isinstance(first, dict) else None
    if parsed is None:
        logger.warning("event_intel_audit: unreadable audit reply for %r "
                       "(stop=%s, searches=%s, keys=%s)", cand.get("name"),
                       res.get("stop_reason"), res.get("search_count"),
                       sorted(top)[:6] if isinstance(top, dict) else None)
        box["error"] = "its answer could not be read"
        return box

    box["rec"] = _record(cand, parsed)
    return box


def audit_famous(candidates: list[dict], profile: dict) -> dict:
    """Weigh every famous candidate against a named, searched alternative.

    Returns {"verdicts": {name_key: {...}}, "cut": [...], "kept": [...],
             "checked": int, "failed": {name_key: str}, "error": str|None}.

    One call per famous event, run concurrently. See AUDIT_MAX_USES for why
    that is both cheaper and better than the single call this used to make.

    `failed` is the half of the split that matters for correctness. Five
    independent calls are five independent chances to lose a call, and
    apply_audit CUTS a famous event it has no verdict for, so without a record
    of which events failed their own audit a transport blip would quietly
    remove a real recommendation from a client's list. `error` is now reserved
    for the case where NOTHING was audited, which is the only case in which
    the check as a whole can be said not to have run.

    A candidate that is not famous is never audited and never appears here;
    the caller marks those `unaudited`, which is a different thing from
    `kept` and is stored as such.
    """
    import concurrent.futures

    famous = [c for c in (candidates or []) if c.get("famous")]
    out = {"verdicts": {}, "cut": [], "kept": [], "checked": len(famous),
           "failed": {}, "error": None, "spend": claude_websearch.spend_sum()}
    if not famous:
        return out

    from . import event_intel_rubric as rubric
    system = _AUDIT_SYSTEM.format(
        profile=_profile_line(profile),
        where_buyers=rubric.CLASSIFICATION_WHERE_BUYERS_ARE.get(
            profile.get("classification"), "Confirm with the client."))

    boxes: dict = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(AUDIT_MAX_INFLIGHT, len(famous))) as pool:
        futures = {pool.submit(_audit_one, c, system): i
                   for i, c in enumerate(famous)}
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                boxes[i] = fut.result()
            except Exception as e:                   # never raises upward
                logger.warning("event_intel_audit: auditing %r raised: %s",
                               famous[i].get("name"), e)
                boxes[i] = {"rec": None, "spend": None,
                            "error": "the audit call failed: %s" % str(e)[:200]}

    # Walked in LIST order, not completion order, so the cut and kept lists
    # read in the order the client's own shortlist is in.
    spends = []
    for i, c in enumerate(famous):
        box = boxes.get(i) or {}
        spends.append(box.get("spend"))
        key = name_key(c.get("name") or "")
        if not key:
            continue
        rec = box.get("rec")
        if box.get("error") or not rec:
            # The display name is carried alongside the reason because
            # `name_key` is lossy ("Adobe Summit" -> "adobe") and a report
            # that cannot name the event it failed to audit is telling the
            # reader a number they cannot act on.
            out["failed"][key] = {
                "name": c.get("name"),
                "why": box.get("error") or "it returned no usable verdict"}
            continue
        out["verdicts"][key] = rec
        (out["kept"] if rec["verdict"] == VERDICT_KEPT
         else out["cut"]).append(dict(rec))
    out["spend"] = claude_websearch.spend_sum(*spends)

    # Every audit failed, so the check as a whole did not happen. Reported the
    # way a transport error always has been, because the alternative is
    # cutting every marquee event on the strength of replies nobody could
    # read. Individual failures are in `failed` and are handled per event.
    if not out["verdicts"]:
        first = next((f.get("why") for f in out["failed"].values()),
                     "nothing was returned")
        out["error"] = (
            "no marquee event could be audited: for %s, %s"
            % ("the one it was given" if len(famous) == 1
               else "each of the %d it was given" % len(famous), first))
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
    "the auditor did not get to it" is not a justification. There are two
    exceptions, and both are the same principle: an event is only cut when
    something was actually weighed against it.

    The audit as a whole failed to run: nothing is cut, because cutting every
    flagship on a transport error would be a silent, wrong answer, and the run
    reports that the audit did not happen instead.

    THIS event's own audit call failed, which `audit["failed"]` records: it is
    marked unaudited and kept. The audit is one call per famous event, so each
    event carries its own chance of a transport error or an unreadable reply,
    and a client's real recommendation must not disappear because one call of
    five came back broken. Without this the split would have made the pipeline
    strictly worse than the single call it replaced, which set one error for
    everybody and cut nobody.
    """
    audit_ran = not audit.get("error")
    verdicts = audit.get("verdicts") or {}
    failed = audit.get("failed") or {}
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
            entry = failed.get(name_key(c.get("name") or ""))
            why = (entry.get("why") if isinstance(entry, dict)
                   else entry) or None
            if why:
                # This event's own call broke. Nothing was weighed against it,
                # so it cannot be cut for losing a comparison that never
                # happened, and it is reported as unweighed rather than
                # presented as an audited pick.
                c["audit_verdict"] = VERDICT_UNAUDITED
                c["audit_note"] = (
                    "This is a marquee event and its audit could not be "
                    "completed (%s), so it has not been weighed against a "
                    "more targeted alternative." % why)[:1200]
                out.append(c)
                continue
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


def _row_for(name: str, by_key: dict) -> dict | None:
    """The candidate row a name refers to: exact key, else one loose match.

    The same discipline `_verdict_for` needs, for the same reason, and this
    is the second place the same root cause has bitten. The audit is SHOWN
    each marquee event as "Name (City) - audience note", so a reply that
    echoes the name it was given comes back carrying the city, and
    `name_key` then produces a completely different key:

        name_key("Adobe Summit (Las Vegas)")     -> "adobe las vegas"
        name_key("Adobe Summit")                 -> "adobe"

    A plain dict lookup misses, the replaced event's CATEGORY cannot be
    found, and the promotion is refused for having no slot on the list. Two
    real, upcoming, high-confidence replacements were lost that way in the
    live run of 2026-09-04, both reported to the client as unconfirmable:

        MAICON                  replacing Adobe Summit (Las Vegas)
        B2B Marketing Exchange  replacing Content Marketing World (Denver, CO)

    Loose matching is accepted ONLY when it is unambiguous, exactly as in
    `_verdict_for`: stapling one event's category onto another is worse than
    the missing promotion this repairs.
    """
    key = name_key(name or "")
    if not key:
        return None
    exact = by_key.get(key)
    if exact is not None:
        return exact
    hits = [row for k, row in by_key.items()
            if k != key and names_match(name or "", row.get("name") or k)]
    return hits[0] if len(hits) == 1 else None


def promote_alternatives(audit: dict, candidates: list[dict],
                         resolver=None, cap: int = MAX_PROMOTED,
                         replaced_from: list[dict] | None = None,
                         lookups: int = MAX_PROMOTION_LOOKUPS) -> dict:
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
                 "not_attempted": [], "spend": claude_websearch.spend_sum()}
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
    pool = (replaced_from if replaced_from is not None else candidates) or []
    by_name = {name_key(c.get("name") or ""): c for c in pool}

    spends = []
    looked_up = 0
    for alt in wanted:
        # Stop on the goal or on the budget, whichever comes first. See
        # MAX_PROMOTION_LOOKUPS for why these are two numbers.
        if len(out["promoted"]) >= max(0, cap) or looked_up >= max(0, lookups):
            break
        looked_up += 1
        try:
            res = resolver(alt["name"])
        except Exception as e:                       # never raises upward
            logger.warning("event_intel_audit: resolving alternative %r failed: %s",
                           alt["name"], e)
            res = {"ok": False, "reasoning": "The lookup failed: %s" % str(e)[:200]}
        # Every lookup is billed at about $0.50, including the ones whose
        # answer is refused two lines below.
        spends.append((res or {}).get("spend"))
        out["spend"] = claude_websearch.spend_sum(*spends)
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
        replaced = _row_for(alt["replaces"] or "", by_name)
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

    # Named, never looked at, because the list was full or the lookup budget
    # ran out. Said out loud rather than trimmed away, so the reader can ask
    # for the rest.
    for alt in wanted[looked_up:]:
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


# ── Cross-client social proof, k-anonymity gated ───────────────────────────
#
# genericness() above compares different client PROFILES under one shared
# login (the population event_intel_store.prior_candidate_names() reads).
# This is a genuinely different check: whether OTHER Position2 clients --
# real different logins, potentially competitors of one another -- are
# independently converging on the same event. Position2 sells this platform
# to more than one client at a time, and that shared history is exactly the
# thing a one-shot deep-research session can never have; the whole point is
# to use it, safely.
#
# "Safely" here means classic k-anonymity, not merely "don't print the
# name". event_intel_store.cross_client_interest() already withholds every
# identifying field at the SQL layer -- there is no email, run_id or
# client_name anywhere in what it returns, so there is nothing for a caller
# to leak by accident the way genericness()'s own worst/comparisons dict
# still can. What is left to get right is the THRESHOLD: a raw count is not
# automatically safe just because it carries no name.

# k=2 is trivially reversible: told "one other client also kept this event",
# anyone who already suspects a specific competitor is on this list has that
# suspicion CONFIRMED outright, with certainty, by a system that named no
# one. k=3 is the standard k-anonymity minimum for exactly this reason: even
# a reader who correctly guesses ONE of the three cannot tell whether the
# other two are real or whether the set would not have fired at all with
# fewer than three, which is what makes the guess unconfirmed.
CROSS_CLIENT_MIN_DISTINCT = 3

# k-anonymity's guarantee assumes there is a real population to hide within.
# A classification bucket that only has, say, four clients ever makes "3
# others" close to fully identifying by elimination for whoever the fourth
# is. This second gate requires the WHOLE bucket (this client included) to
# be at least CROSS_CLIENT_MIN_DISTINCT + 2 = 5 distinct clients before the
# feature is allowed to fire on it AT ALL, regardless of how the count for
# any one event comes out. On a small client base this correctly suppresses
# the feature entirely -- that is the gate working, not a bug to chase; it
# starts firing on its own once the classification's client base actually
# grows past it.
CROSS_CLIENT_MIN_POPULATION = CROSS_CLIENT_MIN_DISTINCT + 2


def cross_client_signal(counts: dict, population: int) -> dict:
    """Which of these events are safe to say "N other clients also kept
    this" about, and how many.

    Pure. `counts` is event_intel_store.cross_client_interest()'s return
    value ({name_key: {"name", "distinct_clients"}}); `population` is
    event_intel_store.classification_population()'s count for the same
    classification and window. Neither carries an identity field, and
    neither does this function's output: {name_key: {"count": int,
    "fires": bool}}, nothing else, by construction -- there is no field
    here FOR an email or client_name to occupy even by accident.

    `fires` requires BOTH gates: the count clears CROSS_CLIENT_MIN_DISTINCT
    AND the classification's whole population clears
    CROSS_CLIENT_MIN_POPULATION. Either alone is not enough.
    """
    enough_population = population >= CROSS_CLIENT_MIN_POPULATION
    out = {}
    for key, row in (counts or {}).items():
        if not key:
            continue
        n = int((row or {}).get("distinct_clients") or 0)
        out[key] = {"count": n,
                    "fires": bool(enough_population and n >= CROSS_CLIENT_MIN_DISTINCT)}
    return out
