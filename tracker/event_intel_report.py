"""Step 7: the executive summary, assembled from stored data.

The obvious way to produce an executive summary is a final model call over the
finished table. This does not do that, for two reasons.

First, the skill specifies the summary EXACTLY: five elements, in order, and
"Never add meta-sections about the scoring process itself". A model handed a
finished analysis and asked to summarise it adds a methodology retrospective
roughly every other time. Assembling the five elements in code makes the
structure a fact rather than an instruction.

Second, every one of the five elements is already known. The title is the
client name. The client profile is the locked intake. The methodology is the
rubric. The assumptions are the gaps, the category shortfall, the audit
outcome and the genericness result, all of which were recorded as they
happened. The top five is the top five rows. A summary that restates stored
data has nothing to hallucinate, and it cannot quietly disagree with the table
underneath it, which is the failure mode of every generated summary.

The assumptions section is the one that earns its place. It is where a run
admits what it could not do: which discovery categories came back empty and
whether that was a market fact or a failed search, whether the famous-event
audit ran, whether the cross-client check could be measured at all, and which
attendance figures are the event's own unverified claims.
"""

from __future__ import annotations

from . import event_intel_rubric as rubric

MAX_TOP = 5


def _profile_sentence(profile: dict) -> str:
    p = profile or {}
    bits = []
    if p.get("website"):
        bits.append("%s (%s)" % (p.get("client_name") or "The client", p["website"]))
    else:
        bits.append(p.get("client_name") or "The client")
    if p.get("verticals"):
        bits.append("selling into %s" % p["verticals"])
    if p.get("buyer_roles"):
        bits.append("to %s" % p["buyer_roles"])
    if p.get("acv_band"):
        bits.append("at around %s" % p["acv_band"])
    if p.get("sales_cycle"):
        bits.append("on a %s cycle" % p["sales_cycle"])
    if p.get("geo_scope"):
        bits.append("across %s" % p["geo_scope"])
    return ", ".join(bits) + "."


def _fmt_when(c: dict) -> str:
    s = (c.get("starts_on") or "")[:10]
    e = (c.get("ends_on") or "")[:10]
    if s and e and e != s:
        return "%s to %s" % (s, e)
    return s or "dates not announced"


def _fmt_where(c: dict) -> str:
    return ", ".join([x for x in (c.get("city"), c.get("country")) if x]) or "location unconfirmed"


def assumptions(*, shortfall: list, audit: dict, generic: dict,
                candidates: list, scoring_errors: list,
                interchangeable: list, banned: list, thin: list,
                unscored: list, over_cap: list | None = None) -> list[str]:
    """Element 4. Everything this run could not establish, stated plainly.

    Ordered by how much it should change a reader's confidence, not by the
    order the stages happened to run in.
    """
    out: list[str] = []

    failed = [s for s in (shortfall or []) if s.get("status") == "error"]
    empty = [s for s in (shortfall or []) if s.get("status") != "error"]
    if failed:
        out.append(
            "%d discovery categor%s did not run, so this list is missing a kind "
            "of event rather than having found none: %s. That is a hole in the "
            "analysis, not a finding about the market."
            % (len(failed), "y" if len(failed) == 1 else "ies",
               "; ".join("%s (%s)" % (s["label"], s["why"]) for s in failed)))
    if empty:
        out.append(
            "%s came back under the two-event quota after searching: %s."
            % ("One category" if len(empty) == 1 else "%d categories" % len(empty),
               "; ".join("%s, %s" % (s["label"], s["why"].rstrip(".").lower())
                         for s in empty)))

    if audit and audit.get("error"):
        # Covers both kinds of failure: the call never happened, and the call
        # happened but produced nothing usable. "Did not run" was false for
        # the second.
        out.append(
            "The famous-event audit produced no usable result (%s), so any "
            "marquee event below has not been weighed against a more targeted "
            "alternative and may be there out of habit." % audit["error"])
    elif audit and audit.get("checked"):
        cut = audit.get("cut") or []
        n = audit["checked"]
        # Cut events are NAMED. A count on its own leaves the reader unable to
        # tell which marquee event they were expecting to see and did not get,
        # which is the one question a cut list has to answer.
        weighed = [c for c in cut if not c.get("no_verdict")]
        skipped = [c for c in cut if c.get("no_verdict")]
        line = ("%d marquee event%s %s audited against a named, more targeted "
                "alternative; %d %s cut."
                % (n, "" if n == 1 else "s", "was" if n == 1 else "were",
                   len(cut), "was" if len(cut) == 1 else "were"))
        if weighed:
            line += " Cut after weighing: %s." % ", ".join(
                str(c.get("name")) for c in weighed if c.get("name"))
        if skipped:
            # A different fact from "we compared it and it lost", and the
            # report has to keep them apart.
            line += (" Cut because the audit returned no verdict for %s, so %s "
                     "never weighed against anything."
                     % (", ".join(str(c.get("name")) for c in skipped if c.get("name")),
                        "it was" if len(skipped) == 1 else "they were"))
        out.append(line)

    if generic:
        if not generic.get("measured"):
            out.append("Cross-client check: %s" % generic.get("why_not_measured"))
        elif generic.get("flagged"):
            out.append("Cross-client check: %s" % generic.get("advice"))
        else:
            out.append(
                "Cross-client check: this list was compared against %d earlier "
                "recommendation%s for other clients and overlaps the closest of "
                "them by %d%%, which is within the bar for a client-specific list."
                % (generic["checked"], "" if generic["checked"] == 1 else "s",
                   round((generic.get("worst") or {}).get("overlap", 0) * 100)))

    claimed = [c for c in (candidates or []) if c.get("attendees")]
    if claimed:
        out.append(
            "Attendance figures are the events' own published claims, quoted as "
            "stated and not independently verified. %d of %d events below "
            "publish one at all."
            % (len(claimed), len(candidates or [])))

    if unscored:
        out.append(
            "%d event%s could not be scored and %s left out of the ranking "
            "rather than ranked low: %s."
            % (len(unscored), "" if len(unscored) == 1 else "s",
               "was" if len(unscored) == 1 else "were",
               ", ".join(c.get("name") or "?" for c in unscored[:6])))
    if scoring_errors:
        out.append("Scoring reported %d error%s: %s."
                   % (len(scoring_errors), "" if len(scoring_errors) == 1 else "s",
                      "; ".join(scoring_errors[:3])))

    if interchangeable:
        out.append(
            "%d pair%s of events were given near-identical client-specific "
            "sentences (%s), which means that sentence is not actually specific "
            "to either of them."
            % (len(interchangeable), "" if len(interchangeable) == 1 else "s",
               "; ".join("%s and %s" % (p["a"], p["b"]) for p in interchangeable[:3])))
    if banned:
        out.append("Marketing superlatives were used in %d description%s (%s), "
                   "which the description standard bans because they carry no "
                   "information."
                   % (len(banned), "" if len(banned) == 1 else "s",
                      ", ".join(b["name"] for b in banned[:4])))
    if thin:
        out.append("%d event%s missing part of its description (%s)."
                   % (len(thin), " is" if len(thin) == 1 else "s are",
                      ", ".join(t["name"] for t in thin[:4])))

    gapped = [c for c in (candidates or []) if c.get("gaps")]
    if gapped:
        out.append(
            "%d event%s carry at least one unmeasured field, listed against the "
            "row itself so a score built on partial information is visible as "
            "such." % (len(gapped), "" if len(gapped) == 1 else "s"))

    # Events that cleared the bar and were dropped only because the list has a
    # maximum length. rank() computes this precisely so it can be said; a list
    # truncated in silence reads as "nothing else qualified", which is a
    # different and false claim.
    if over_cap:
        names = [str(c.get("name")) for c in over_cap if c.get("name")]
        out.append(
            "%d further event%s scored above the bar but fell outside the "
            "maximum list length, so %s not shown: %s. Raise the maximum on "
            "the client profile to see %s."
            % (len(over_cap), "" if len(over_cap) == 1 else "s",
               "it is" if len(over_cap) == 1 else "they are",
               ", ".join(names[:12]) + (" and %d more" % (len(names) - 12)
                                        if len(names) > 12 else ""),
               "it" if len(over_cap) == 1 else "them"))

    if not out:
        out.append("Nothing material was left unmeasured on this run.")
    return out


def top_five(kept: list[dict]) -> list[dict]:
    """Element 5. Name, score, location, dates, and the one-line case, which
    is the description's second sentence: the only line in the whole report
    that is supposed to be about this client rather than this event."""
    out = []
    for c in (kept or [])[:MAX_TOP]:
        out.append({
            "name": c.get("name"),
            "edition": c.get("edition"),
            "total": c.get("total"),
            "tier": c.get("tier"),
            "where": _fmt_where(c),
            "when": _fmt_when(c),
            "case": (c.get("client_line") or c.get("description")
                     or "No case was written for this event."),
        })
    return out


def executive_summary(*, profile: dict, ranked: dict, **kw) -> dict:
    """The five elements, in the skill's order, and nothing else.

    Deliberately returns no sixth element. The skill forbids meta-sections
    about the scoring process, and the easiest way to never add one is to have
    nowhere to put it.
    """
    p = profile or {}
    client = p.get("client_name") or "Client"
    counts = (ranked or {}).get("counts") or {}
    return {
        # 1. Title. No dash: house style is a colon.
        "title": "%s: Conference Analysis" % client,
        # 2. Client profile.
        "client_profile": _profile_sentence(p),
        "classification_label": rubric.CLASSIFICATION_LABELS.get(p.get("classification"), ""),
        "where_buyers": rubric.CLASSIFICATION_WHERE_BUYERS_ARE.get(
            p.get("classification"), ""),
        # 3. Methodology as applied to this client.
        "methodology": rubric.methodology_note(p["classification"])
        if p.get("classification") in rubric.CLASSIFICATIONS else "",
        # 4. Assumptions and notes.
        "assumptions": assumptions(candidates=(ranked or {}).get("kept") or [],
                                   over_cap=(ranked or {}).get("over_cap") or [],
                                   **kw),
        # 5. Top five must-attend.
        "top_five": top_five((ranked or {}).get("kept") or []),
        "counts": counts,
    }


# ── The outcome loop ──────────────────────────────────────────────────────

def annotate_outcomes(candidates: list[dict], outcomes: dict) -> dict:
    """Attach what this user already decided about each event.

    An event the user explicitly skipped last quarter is NOT dropped from the
    list. Circumstances change, the client changed, the event changed, and a
    tool that silently hides a previous rejection is deciding on the user's
    behalf with information it does not have.

    What it must never do is present it as a fresh find. So the decision and
    the user's own note ride along on the candidate, and the summary counts
    how many of the ranked list are events they have already ruled on. That is
    the honest version of the source skill's "tighten over time" step, which
    asks for reply-rate data from a sequencer this platform does not have.
    """
    from .event_intel_discover import name_key
    seen = {"going": 0, "skipped": 0, "went": 0}
    annotated = []
    for c in (candidates or []):
        c = dict(c)
        prior = (outcomes or {}).get(name_key(c.get("name") or ""))
        if prior:
            c["prior_decision"] = prior.get("decision")
            c["prior_note"] = prior.get("note")
            c["prior_on"] = prior.get("updated_at")
            if prior.get("decision") in seen:
                seen[prior["decision"]] += 1
        else:
            c["prior_decision"] = None
            c["prior_note"] = None
            c["prior_on"] = None
        annotated.append(c)
    by_name = {c["name"]: {"decision": c["prior_decision"], "note": c["prior_note"],
                           "on": c["prior_on"]}
                for c in annotated if c.get("prior_decision") and c.get("name")}
    total = sum(seen.values())
    return {
        "candidates": annotated,
        "by_name": by_name,
        "counts": seen,
        "ruled_on": total,
        "note": (
            "%d of these are events you have already ruled on, and your own note "
            "is shown on each. They are not hidden: what was right to skip last "
            "time may not be right now, and that is your call rather than this "
            "tool's." % total) if total else None,
    }
