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


# How much of a category's own explanation the summary quotes.
#
# These reasons come from two places and one of them is a model. A finder's
# note is up to 600 characters of its own prose, and folding several of them
# into one sentence produced a live report whose assumptions paragraph ran to
# 180 words, ended mid-word, and rendered "AWS Summit city tours" as "aws
# summit city tours" because the sentence it was folded into needed a
# lowercase clause.
#
# So each reason is quoted rather than folded: its own sentence, after a
# colon, with its own capitalisation left alone.
REASON_CHARS = 200


def _reason(text: str, cap: int = REASON_CHARS) -> str:
    """One category's explanation, trimmed to whole sentences.

    Whole sentences while they fit, rather than the first sentence alone: a
    finder's note often puts what it searched in sentence one and what it
    concluded in sentence two, and keeping only the first throws away the
    half a reader wants.

    Never cuts mid-word, and marks the cut when it makes one, so a reader can
    tell a trimmed explanation from a model that stopped mid-thought. Only
    one of those two is worth going to investigate.

    The first character is capitalised, never lowercased. Uppercasing a
    leading letter cannot damage anything; lowercasing a clause to fold it
    into a sentence turned "AWS Summit city tours" into "aws summit city
    tours" in a live report.
    """
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    out = ""
    for sentence in _SENTENCE.findall(t):
        # Each match begins with the whitespace that followed the previous
        # sentence's full stop, so it is stripped before joining. Without
        # this every sentence break printed a double space.
        nxt = (out + " " + sentence.strip()).strip()
        if out and len(nxt) > cap:
            break
        out = nxt
        if len(out) >= cap:
            break
    if not out:
        out = t
    # Whether anything was left behind. A cut that lands on a sentence
    # boundary is still a cut, and it is the one shape that could pass for a
    # complete explanation: the reader sees a tidy full stop and has no way to
    # know a second sentence said what the search concluded.
    dropped = len(out) < len(t)
    if len(out) > cap:
        cut = out[:cap]
        at = cut.rfind(" ")
        if at > cap * 0.6:
            cut = cut[:at]
        out = cut.rstrip(" ,;:.-")
        dropped = True
    if dropped:
        out = out.rstrip(" .,;:-") + "\u2026"
    elif not out.endswith((".", "!", "?", "\u2026")):
        out += "."
    return out[0].upper() + out[1:]


# A sentence ends at a full stop followed by whitespace and a capital, or at
# the end of the string. Not at any full stop: "e.g. AWS Summit" and
# "attd.kenes.com" are not sentence ends, and splitting there would cut a
# reason in half mid-clause.
_SENTENCE = __import__("re").compile(
    r"[\s\S]+?(?:[.!?](?=\s+[A-Z(\"\u201c])|[.!?]$|$)")


def _fmt_where(c: dict) -> str:
    return ", ".join([x for x in (c.get("city"), c.get("country")) if x]) or "location unconfirmed"


def assumptions(*, shortfall: list, audit: dict, generic: dict,
                candidates: list, scoring_errors: list,
                interchangeable: list, banned: list, thin: list,
                unscored: list, over_cap: list | None = None,
                finished: list | None = None,
                promoted: dict | None = None,
                scoring_batches: int = 0) -> list[str]:
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
            "of event rather than having found none. That is a hole in the "
            "analysis, not a finding about the market. %s"
            % (len(failed), "y" if len(failed) == 1 else "ies",
               " ".join("%s: %s" % (s["label"], _reason(s["why"]))
                        for s in failed)))
    if empty:
        out.append(
            "%s came back under the two-event quota after searching. %s"
            % ("One category" if len(empty) == 1 else "%d categories" % len(empty),
               " ".join("%s: %s" % (s["label"], _reason(s["why"]))
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

    # Where a promoted event came from. Without this it sits in the middle of
    # a category's results looking like something that category's search
    # found, and the reader has no way to know it arrived by a different route
    # and was confirmed by a different check.
    pr = promoted or {}
    added, unconfirmed = pr.get("promoted") or [], pr.get("unconfirmed") or []
    not_attempted = pr.get("not_attempted") or []
    if added or unconfirmed or not_attempted:
        bits = []
        if added:
            bits.append("%s %s named by the audit as a better fit than a "
                        "marquee event it cut, then confirmed separately and "
                        "added to this list"
                        % (", ".join(str(c.get("name")) for c in added),
                           "was" if len(added) == 1 else "were"))
        if unconfirmed:
            bits.append("%s %s named as an alternative but could not be "
                        "confirmed, so %s not on the list and the event %s "
                        "replaced is simply gone"
                        % (", ".join(str(c.get("name")) for c in unconfirmed),
                           "was" if len(unconfirmed) == 1 else "were",
                           "it is" if len(unconfirmed) == 1 else "they are",
                           "it" if len(unconfirmed) == 1 else "they"))
        if not_attempted:
            bits.append("%s %s named but not looked up, because this run stops "
                        "after %d replacements"
                        % (", ".join(str(c.get("name")) for c in not_attempted),
                           "was" if len(not_attempted) == 1 else "were",
                           len(added) + len(unconfirmed)))
        out.append("Replacements for cut marquee events: %s." % "; ".join(bits))

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
    # More than one grading pass means more than one grader, and totals from
    # different passes were never compared side by side. The candidates are
    # dealt across the passes so no pass sees a single category, which is what
    # keeps the totals close to comparable, but "close to" is not "identical"
    # and a ranked table invites the reader to assume identical.
    if (scoring_batches or 0) > 1:
        out.append(
            "The %d events were graded in %d separate passes rather than one, "
            "with the categories dealt evenly across them so no pass saw only "
            "one kind of event. Scores are still absolute against the rubric, "
            "but two events one point apart may have been graded in different "
            "passes, so treat small gaps near the top as a tie."
            % (len(candidates or []) + len(unscored or []), scoring_batches))

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

    # An edition that is already over, returned by a search asked for the next
    # one, is worth saying out loud twice over: it is a row the reader can see
    # is missing from the ranking, and it means the source being searched is
    # behind on that event.
    if finished:
        names = ", ".join("%s (ended %s)" % (f.get("name"), f.get("ends_on") or "?")
                          for f in finished[:6])
        out.append(
            "%d event%s found for this profile had already finished and %s left "
            "out of the ranking rather than scored against a date nobody can "
            "attend: %s. Where one of these is an annual fixture, the next "
            "edition is the thing to go looking for."
            % (len(finished), "" if len(finished) == 1 else "s",
               "was" if len(finished) == 1 else "were", names))

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
        # client_line is the only sentence in the report written about THIS
        # client; description is written about the event and would read the
        # same for anybody. Falling back from one to the other is sometimes
        # the best available answer, but doing it silently puts the generic
        # line in the flagship slot with nothing on screen to say so.
        case = c.get("client_line")
        generic = not case
        if generic:
            case = c.get("description")
        out.append({
            "name": c.get("name"),
            "edition": c.get("edition"),
            "total": c.get("total"),
            "tier": c.get("tier"),
            "where": _fmt_where(c),
            "when": _fmt_when(c),
            "case": case or ("The sub-scores for this event were recorded but "
                             "no written case was, so read the breakdown "
                             "rather than this line."),
            "case_is_generic": bool(generic and c.get("description")),
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
                                   finished=(ranked or {}).get("finished") or [],
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
