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


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _day(iso: str) -> str:
    """One date the way a person writes it, or the ISO string unchanged.

    Element 5 is stored as finished text, and it was stored as ISO while the
    ranked list a few lines below it on the same page rendered the same event
    as "Jun 8, 2027". Two date formats for one event, in one report.
    """
    parts = (iso or "")[:10].split("-")
    if len(parts) != 3:
        return iso or ""
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return iso
    if not 1 <= m <= 12:
        return iso
    return "%s %d, %d" % (_MONTHS[m - 1], d, y)


def _fmt_when(c: dict) -> str:
    s = (c.get("starts_on") or "")[:10]
    e = (c.get("ends_on") or "")[:10]
    if s and e and e != s:
        return "%s to %s" % (_day(s), _day(e))
    return _day(s) or "dates not announced"


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


# ── Element 4: what this run could not establish ──────────────────────────
#
# One list of facts, two renderings, and the split is the whole point.
#
# This used to be fifteen possible paragraphs, printed in full, one after
# another. Every one of them earns its place: the agent's value is that it
# says what it could not measure, and hiding that would make it a worse tool.
# But a live report came back with a 180-word block of them and the verdict
# from the person it was written for was that nobody would read it, which is
# the same as not saying it at all.
#
# So a fact is now a HEAD and a DETAIL. The head is one scannable line and is
# always visible. The detail carries every name, count and reason the
# paragraph carried, and the page puts it one click away. Nothing is dropped.
#
# `notes()` is the source. `assumptions()` is the same facts flattened back to
# prose for the CSV and for anything holding an older stored run.

LEVEL_GAP = "gap"        # something could not be measured. A hole.
LEVEL_THIN = "thin"      # measured, but on less than we wanted.
LEVEL_NOTE = "note"      # worth knowing, not a shortfall.
LEVEL_OK = "ok"          # nothing to report.

# Head lines are read in a column, so they are kept to a length that does not
# wrap on a phone. Detail has no cap: it is behind a disclosure and a reader
# who opened it asked for all of it.
HEAD_CHARS = 78


def _n(count: int, one: str, many: str) -> str:
    return "%d %s" % (count, one if count == 1 else many)


def _names(items, key="name", cap=12) -> str:
    out = [str(i.get(key)) for i in items if i.get(key)]
    if len(out) <= cap:
        return ", ".join(out)
    return "%s and %d more" % (", ".join(out[:cap]), len(out) - cap)


def notes(*, shortfall: list, audit: dict, generic: dict,
          candidates: list, scoring_errors: list,
          interchangeable: list, banned: list, thin: list,
          unscored: list, over_cap: list | None = None,
          finished: list | None = None,
          promoted: dict | None = None,
          scoring_batches: int = 0,
          graded: int | None = None) -> list[dict]:
    """Everything this run could not establish, as {level, head, detail}.

    Ordered by how much it should change a reader's confidence, not by the
    order the stages happened to run in.
    """
    out: list[dict] = []

    def add(level, head, detail=""):
        # Detail is stitched together from our sentences and from strings a
        # model wrote, and the seam showed: "Cross-client check: not measured"
        # was followed by "first run for this client", lowercase and with no
        # full stop. Tidied in one place rather than at every call site.
        d = " ".join(str(detail or "").split())
        if d:
            d = d[0].upper() + d[1:]
            if not d.endswith((".", "!", "?", "\u2026")):
                d += "."
        out.append({"level": level, "head": head.strip(), "detail": d})

    failed = [s for s in (shortfall or []) if s.get("status") == "error"]
    # `partial` is its own bucket, not a quiet member of `empty`. A partial
    # search is a hole in the coverage even when it confirmed an event, which
    # is exactly how the page's coverage chart reads it, and folding it into
    # "searched, and short" made one report describe one category both ways.
    partial = [s for s in (shortfall or []) if s.get("status") == "partial"]
    empty = [s for s in (shortfall or [])
             if s.get("status") not in ("error", "partial")]
    spent = [s for s in (shortfall or []) if s.get("budget_spent")]
    total_cats = len(rubric.CATEGORIES)

    if failed:
        add(LEVEL_GAP,
            "%d of %d category searches did not run" % (len(failed), total_cats),
            "This list is missing a kind of event rather than having found "
            "none. That is a hole in the analysis, not a finding about the "
            "market. %s"
            % " ".join("%s: %s" % (s["label"], _reason(s["why"]))
                       for s in failed))
    if partial:
        add(LEVEL_GAP,
            "%s did not finish searching"
            % _n(len(partial), "category search", "category searches"),
            "Whatever these categories hold, this run did not get to the end "
            "of it, so they are under-searched rather than settled. %s"
            % " ".join("%s: %s" % (s["label"], _reason(s["why"]))
                       for s in partial))
    if empty:
        add(LEVEL_THIN,
            "%s came back under the two-event quota"
            % _n(len(empty), "category", "categories"),
            "Searched, and short. %s"
            % " ".join("%s: %s" % (s["label"], _reason(s["why"]))
                       for s in empty))
    # Separated from the line above on purpose. "Short with searches to spare"
    # and "short having spent everything it was given" are different findings,
    # and only the second is worth spending more on.
    if spent:
        add(LEVEL_THIN,
            "%s used every search allowed"
            % _n(len(spent), "category", "categories"),
            "There may be more to find in %s than this run could reach: %s."
            % ("it" if len(spent) == 1 else "them",
               ", ".join(s["label"] for s in spent)))

    if audit and audit.get("error"):
        # Covers both kinds of failure: the call never happened, and the call
        # happened but produced nothing usable. "Did not run" was false for
        # the second.
        add(LEVEL_GAP, "The famous-event audit produced no usable result",
            "Any marquee event below has not been weighed against a more "
            "targeted alternative and may be there out of habit. %s"
            % audit["error"])
    elif audit and audit.get("checked"):
        cut = audit.get("cut") or []
        # The audit is one call per marquee event, so some can fail while
        # others succeed. `checked` is how many were SENT; the headline has to
        # say how many were actually weighed, or a run where two of five calls
        # broke reports five audits that did not happen. This is the same
        # defect class as a stage claiming what it intended rather than what
        # it produced, which a field audit found seven of.
        failed = audit.get("failed") or {}
        n = max(0, audit["checked"] - len(failed))
        # Cut events are NAMED. A count on its own leaves the reader unable to
        # tell which marquee event they were expecting to see and did not get,
        # which is the one question a cut list has to answer.
        weighed = [c for c in cut if not c.get("no_verdict")]
        skipped = [c for c in cut if c.get("no_verdict")]
        detail = ""
        if weighed:
            detail += "Cut after weighing: %s. " % _names(weighed)
        if skipped:
            # A different fact from "we compared it and it lost", and the
            # report has to keep them apart.
            detail += ("Cut because the audit returned no verdict for %s, so %s "
                       "never weighed against anything. "
                       % (_names(skipped),
                          "it was" if len(skipped) == 1 else "they were"))
        if failed:
            # Kept on the list, and said so. These were not weighed and are
            # not being presented as though they had been.
            names = [f.get("name") for f in failed.values() if f.get("name")]
            detail += ("%s could not be audited and %s left on the list "
                       "unweighed%s. "
                       % (_n(len(failed), "marquee event", "marquee events"),
                          "was" if len(failed) == 1 else "were",
                          (": %s" % ", ".join(names)) if names else ""))
        add(LEVEL_NOTE,
            "%s %s audited against a named alternative, %d %s cut"
            % (_n(n, "marquee event", "marquee events"),
               "was" if n == 1 else "were", len(cut),
               "was" if len(cut) == 1 else "were"),
            detail or "None of them was cut.")

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
                        % (_names(added), "was" if len(added) == 1 else "were"))
        if unconfirmed:
            bits.append("%s %s named as an alternative but could not be "
                        "confirmed, so %s not on the list and the event %s "
                        "replaced is simply gone"
                        % (_names(unconfirmed),
                           "was" if len(unconfirmed) == 1 else "were",
                           "it is" if len(unconfirmed) == 1 else "they are",
                           "it" if len(unconfirmed) == 1 else "they"))
        if not_attempted:
            bits.append("%s %s named but not looked up, because this run stops "
                        "after %d replacements"
                        % (_names(not_attempted),
                           "was" if len(not_attempted) == 1 else "were",
                           len(added) + len(unconfirmed)))
        add(LEVEL_NOTE if added and not unconfirmed else LEVEL_THIN,
            "Replacements for cut marquee events: %d in, %d unconfirmed"
            % (len(added), len(unconfirmed)),
            "%s." % "; ".join(bits))

    if generic:
        if not generic.get("measured"):
            add(LEVEL_NOTE, "Cross-client check: not measured",
                str(generic.get("why_not_measured") or ""))
        elif generic.get("flagged"):
            add(LEVEL_THIN, "Cross-client check: this list looks generic",
                str(generic.get("advice") or ""))
        else:
            add(LEVEL_NOTE, "Cross-client check: this list is client-specific",
                "Compared against %s for other clients. It overlaps the "
                "closest of them by %d%%, which is within the bar."
                % (_n(generic["checked"], "earlier recommendation",
                      "earlier recommendations"),
                   round((generic.get("worst") or {}).get("overlap", 0) * 100)))

    # Read off `candidates` itself (already annotated by
    # attach_cross_client_signal before notes() is ever called), the same
    # way the attendance-claims note just below reads `candidates` directly
    # rather than taking a separate summary parameter that could drift out
    # of step with it.
    watched = [c for c in (candidates or []) if c.get("cross_client_count")]
    if watched:
        add(LEVEL_NOTE,
            "%s on this list %s also being watched by other clients"
            % (_n(len(watched), "event", "events"),
               "is" if len(watched) == 1 else "are"),
            "%s. Aggregate counts only: no client names are ever shown."
            % "; ".join("%s (%d)" % (c.get("name"), c["cross_client_count"])
                       for c in watched[:6]))

    claimed = [c for c in (candidates or []) if c.get("attendees")]
    if claimed:
        add(LEVEL_NOTE,
            "Attendance figures are the events' own published claims",
            "Quoted as stated and not independently verified. %d of %d events "
            "below publish one at all."
            % (len(claimed), len(candidates or [])))

    if unscored:
        add(LEVEL_GAP,
            "%s could not be scored"
            % _n(len(unscored), "event", "events"),
            "Left out of the ranking rather than ranked low: %s."
            % _names(unscored, cap=6))

    # More than one grading pass means more than one grader, and totals from
    # different passes were never compared side by side. The candidates are
    # dealt across the passes so no pass sees a single category, which is what
    # keeps the totals close to comparable, but "close to" is not "identical"
    # and a ranked table invites the reader to assume identical.
    if (scoring_batches or 0) > 1:
        # `graded` is every event the scorer actually graded. It used to be
        # computed here as the kept list plus the unscored ones, which was
        # wrong in the same direction the whole agent was wrong: the grading
        # pass sees every candidate, and counting only the survivors printed
        # "The 1 events were graded in 2 separate passes" on a run that
        # graded seven. Left as a fallback for a stored run whose caller
        # predates the argument.
        n_graded = (graded if graded is not None
                    else len(candidates or []) + len(unscored or []))
        add(LEVEL_NOTE,
            "%s graded in %d separate passes, not one"
            % (_n(n_graded, "event was", "events were"), scoring_batches),
            "The categories were dealt evenly across the passes so no pass "
            "saw only one kind of event. Scores are still absolute against "
            "the rubric, but two events one point apart may have been graded "
            "in different passes, so treat small gaps near the top as a tie.")

    if scoring_errors:
        add(LEVEL_GAP,
            "Scoring reported %s" % _n(len(scoring_errors), "error", "errors"),
            "; ".join(scoring_errors[:3]) + ".")

    if interchangeable:
        add(LEVEL_THIN,
            "%s of events share a client-specific sentence"
            % _n(len(interchangeable), "pair", "pairs"),
            "Which means that sentence is not actually specific to either of "
            "them: %s."
            % "; ".join("%s and %s" % (p["a"], p["b"])
                        for p in interchangeable[:3]))
    if banned:
        add(LEVEL_THIN,
            "Marketing superlatives in %s"
            % _n(len(banned), "description", "descriptions"),
            "The description standard bans them because they carry no "
            "information: %s." % _names(banned, cap=4))
    if thin:
        add(LEVEL_THIN,
            "%s missing part of its description"
            % _n(len(thin), "event", "events"),
            "%s." % _names(thin, cap=4))

    # An edition that is already over, returned by a search asked for the next
    # one, is worth saying out loud twice over: it is a row the reader can see
    # is missing from the ranking, and it means the source being searched is
    # behind on that event.
    if finished:
        add(LEVEL_NOTE,
            "%s had already finished"
            % _n(len(finished), "event", "events"),
            "Left out of the ranking rather than scored against a date nobody "
            "can attend: %s. Where one of these is an annual fixture, the next "
            "edition is the thing to go looking for."
            % ", ".join("%s (ended %s)" % (f.get("name"), f.get("ends_on") or "?")
                        for f in finished[:6]))

    gapped = [c for c in (candidates or []) if c.get("gaps")]
    if gapped:
        add(LEVEL_THIN,
            "%s an unmeasured field"
            % _n(len(gapped), "event carries", "events carry"),
            "Listed against the row itself, so a score built on partial "
            "information is visible as such.")

    # Events that cleared the bar and were dropped only because the list has a
    # maximum length. rank() computes this precisely so it can be said; a list
    # truncated in silence reads as "nothing else qualified", which is a
    # different and false claim.
    if over_cap:
        add(LEVEL_THIN,
            "%s cleared the bar but fell outside the maximum list length"
            % _n(len(over_cap), "further event", "further events"),
            "So %s not shown: %s. Raise the maximum on the client profile to "
            "see %s."
            % ("it is" if len(over_cap) == 1 else "they are",
               _names(over_cap),
               "it" if len(over_cap) == 1 else "them"))

    if not out:
        add(LEVEL_OK, "Nothing material was left unmeasured on this run")
    return out


def flatten(facts: list[dict]) -> list[str]:
    """Pointers back to prose, in one place.

    Written once and called twice on purpose. Both `assumptions()` and the
    executive summary need this shape, and when each had its own copy of the
    expression a mutation that broke one left the other passing, which is
    exactly the drift the single source was meant to prevent.
    """
    return [(n["head"] + ". " + n["detail"]).strip() if n.get("detail")
            else n["head"] + "." for n in facts]


def assumptions(**kw) -> list[str]:
    """The same facts as `notes`, flattened back to prose.

    Kept because the shape is stored on finished runs and read by the CSV.
    Deriving it from `notes` rather than building it separately is what stops
    the two from ever disagreeing.
    """
    return flatten(notes(**kw))


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
            # Carried through from apply_outcome_pattern/attach_cross_client_
            # signal, both of which run on `kept` before top_five() truncates
            # it to this fixed field set. Without this, the flagship "top
            # five" element of the report -- the one a client actually
            # reads -- would show neither signal at all: order would change
            # silently with no reason on screen, and a live end-to-end check
            # against real Postgres is what caught this being dropped here,
            # not a unit test, since every existing test asserted on ORDER
            # or on the full candidate list rather than on this element's
            # own field set.
            "outcome_adjustment": c.get("outcome_adjustment"),
            "outcome_adjustment_reason": c.get("outcome_adjustment_reason"),
            "cross_client_count": c.get("cross_client_count"),
            "cross_client_note": c.get("cross_client_note"),
        })
    return out


def status_labels(rows: list[dict], cap: int = rubric.DEFAULT_CAP,
                  today=None) -> dict:
    """Which bucket every stored candidate actually landed in, by name key.

    Exists because the candidates CSV export used to decide "on the list"
    with its own one-line rule (`total >= RANK_FLOOR`), a second and
    disagreeing copy of the exact policy `rubric.rank()` already encodes. A
    real run's own export called a genuine second-tier event "no" while the
    web page for the SAME run showed it under "Worth a look" as an offered
    option, because `rank()` also asks whether the audience clears the
    relevance and consider gates, which a bare floor comparison cannot see.
    An event that is unattendable (`finished`) or that cleared the bar but
    was cut only by list length (`over_cap`) were two more states the export
    could not distinguish from a plain "no" either.

    This calls `rank()` itself rather than reading the run's stored summary,
    because the summary's own "top_five" is truncated to MAX_TOP and cannot
    tell a sixth kept event from an excluded one. Recomputing here is the
    only way the export and the page are guaranteed to agree, for the same
    reason `assumptions()` above derives from `notes()` instead of keeping
    its own copy.
    """
    from .event_intel_discover import name_key
    ranked = rubric.rank(rows, cap=cap, today=today)
    committed = {name_key(c.get("name") or "") for c in ranked["committed_below_bar"]}
    out = {}

    def _set(bucket, label):
        for c in bucket:
            key = name_key(c.get("name") or "")
            if key:
                out[key] = label

    # Order matters: a later call overwrites an earlier one, and `kept` must
    # win over the finished/excluded default a row would otherwise get if it
    # somehow appeared in more than one bucket.
    _set(ranked["excluded"], "Excluded, below the bar")
    _set(ranked["finished"], "Edition already finished")
    _set(ranked["over_cap"], "Cleared the bar, cut only by list length")
    _set(ranked["worth_a_look"],
        "Worth a look: below the bar, but the audience is genuinely this "
        "client's")
    _set(ranked["kept"], "Recommended")
    for key in committed & set(out):
        out[key] = ("Recommended: already committed, did not clear the bar "
                    "on its own merits")
    return out


def selection_snapshot(ranked, profile):
    """The persisted ordering is shared by every rendering and export."""
    from copy import deepcopy
    from datetime import date
    from .event_intel_identity import event_key
    groups = {}
    labels = {'kept': 'Recommended', 'worth_a_look': 'Worth a look: below the bar, but the audience is genuinely this client\'s',
              'excluded': 'Excluded, below the bar', 'finished': 'Edition already finished',
              'over_cap': 'Cleared the bar, cut only by list length',
              'incomplete': 'Incomplete run: not a recommendation'}
    for bucket, label in labels.items():
        groups[bucket] = []
        for row in ranked.get(bucket, []):
            row = deepcopy(row)
            row['event_identity'] = event_key(row)
            row['disposition'] = bucket
            row['status_label'] = label
            if bucket == 'kept' and row.get('committed') and (row.get('total') or 0) < 70:
                row['status_label'] = 'Recommended: already committed, did not clear the bar on its own merits'
            if bucket == 'over_cap' and row.get('cap_bucket') == 'worth_a_look':
                row['status_label'] = 'Worth a look, omitted only by list length'
            groups[bucket].append(row)
    return dict(groups, version=1, as_of=date.today().isoformat(),
                profile=deepcopy(profile), counts=deepcopy(ranked['counts']))


def disabled_cross_client_check():
    return {
        'measured': False, 'flagged': False, 'checked': 0,
        'worst': None, 'comparisons': [], 'advice': '',
        'why_not_measured': 'Cross-client comparisons are disabled pending verified client identity and consent.',
    }


def present_run(run, profile, rows, decisions):
    """Read a historical selection plus current, profile-scoped decisions.

    Legacy selections are reconstructed once per read in Python. Cross-client
    claims are suppressed, including claims persisted by older deployments.
    """
    from copy import deepcopy
    from datetime import date
    run = deepcopy(run)
    summary = run.setdefault('summary', {}) or {}
    run['summary'] = summary
    selection = summary.get('selection')
    if not selection or selection.get('version') != 1:
        when = str(run.get('created_at') or '')[:10]
        try:
            when = date.fromisoformat(when)
        except ValueError:
            when = date.today()
        ranked = rubric.rank(rows, cap=int(profile.get('max_events') or 15), today=when)
        if run.get('status') == 'failed':
            ranked = rubric.rank([], today=when)
            ranked['incomplete'] = rows
            ranked['counts']['incomplete'] = len(rows)
            summary['no_candidates'] = True
            summary['note'] = run.get('error') or 'This research could not be completed. No recommendation is approved.'
        selection = selection_snapshot(ranked, profile)
        selection['as_of'] = when.isoformat()
        selection['reconstructed_legacy'] = True
    def strip_cross(value):
        if isinstance(value, dict):
            return {k: strip_cross(v) for k, v in value.items() if not k.startswith('cross_client')}
        if isinstance(value, list):
            return [strip_cross(v) for v in value]
        return value
    summary = strip_cross(summary)
    summary['notes'] = [n for n in summary.get('notes', [])
                        if 'watched by other clients' not in str(n).lower()
                        and 'cross-client check' not in str(n).lower()]
    summary['assumptions'] = [n for n in summary.get('assumptions', [])
                              if 'watched by other clients' not in str(n).lower()
                        and 'cross-client check' not in str(n).lower()]
    summary['generic'] = disabled_cross_client_check()
    selection = strip_cross(selection)
    all_rows = []
    for bucket in ('kept', 'worth_a_look', 'excluded', 'over_cap', 'finished', 'incomplete'):
        selection[bucket] = annotate_outcomes(selection.get(bucket, []), decisions)['candidates']
        all_rows.extend(selection[bucket])
    summary['selection'] = selection
    summary['counts'] = selection['counts']
    summary['top_five'] = top_five(selection['kept'])
    for bucket in ('worth_a_look', 'excluded', 'over_cap', 'finished'):
        summary[bucket] = selection[bucket]
    outcome = annotate_outcomes(selection['kept'], decisions)
    from .event_intel_store import DECISION_LABELS
    summary['outcomes'] = {k: v for k, v in outcome.items() if k != 'candidates'}
    summary['outcomes']['labels'] = DECISION_LABELS
    run['summary'] = summary
    run['candidates'] = all_rows
    run['profile'] = dict(selection.get('profile') or profile,
                          confidential=profile.get('confidential', False))
    return strip_cross(run)


def executive_summary(*, profile: dict, ranked: dict, **kw) -> dict:
    """The five elements, in the skill's order, and nothing else.

    Deliberately returns no sixth element. The skill forbids meta-sections
    about the scoring process, and the easiest way to never add one is to have
    nowhere to put it.
    """
    p = profile or {}
    client = p.get("client_name") or "Client"
    counts = (ranked or {}).get("counts") or {}
    r = ranked or {}
    facts = notes(candidates=r.get("kept") or [],
                  over_cap=r.get("over_cap") or [],
                  finished=r.get("finished") or [],
                  # Every event the scorer graded, which is all three
                  # buckets and not just the survivors.
                  graded=(len(r.get("kept") or []) +
                          len(r.get("worth_a_look") or []) +
                          len(r.get("excluded") or []) +
                          len(kw.get("unscored") or [])),
                  **kw)
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
        # 4. Assumptions and notes, in both shapes, from one build.
        #
        # `notes` is what the page renders: a scannable head per fact with the
        # full reason one click behind it. `assumptions` is the same facts as
        # prose, kept for the CSV and for anything holding a run stored before
        # the pointers existed. Both come off the same list, so the two cannot
        # come to disagree.
        "notes": facts,
        "assumptions": flatten(facts),
        # 5. Top five must-attend.
        "top_five": top_five((ranked or {}).get("kept") or []),
        "counts": counts,
        "selection": selection_snapshot(ranked, profile),
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
    from .event_intel_identity import event_key
    seen = {"going": 0, "skipped": 0, "went": 0}
    annotated = []
    for c in (candidates or []):
        c = dict(c)
        c['event_identity'] = event_key(c)
        prior = (outcomes or {}).get(c['event_identity'])
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
        "by_identity": {c['event_identity']: {"decision": c['prior_decision'],
                         "note": c['prior_note'], "on": c['prior_on']}
                        for c in annotated if c.get('prior_decision')},
        "by_name": by_name,
        "counts": seen,
        "ruled_on": total,
        "note": (
            "%d of these are events you have already ruled on, and your own note "
            "is shown on each. They are not hidden: what was right to skip last "
            "time may not be right now, and that is your call rather than this "
            "tool's." % total) if total else None,
    }


def apply_outcome_pattern(candidates: list[dict], pattern: dict) -> list[dict]:
    """Attach rubric.outcome_adjustment()'s verdict to every row and RE-SORT
    by it. `total`/`tier` are never touched -- see rubric.outcome_adjustment's
    own docstring for why -- so this can only reorder candidates that
    rank() has already put in the SAME bucket; it must never be given the
    unbucketed, uncapped list to sort (call it separately on `kept` and on
    `worth_a_look`, after rank() has already decided both).
    """
    from . import event_intel_rubric as rubric
    by_cat = (pattern or {}).get("by_category") or {}
    by_fmt = (pattern or {}).get("by_format") or {}
    out = []
    for c in (candidates or []):
        c = dict(c)
        verdict = rubric.outcome_adjustment(
            c.get("category"), by_cat.get(c.get("category")),
            c.get("format"), by_fmt.get(c.get("format")))
        c["outcome_adjustment"] = verdict["adjustment"]
        c["outcome_adjustment_basis"] = verdict["basis"]
        c["outcome_adjustment_reason"] = verdict["reason"]
        out.append(c)
    out.sort(key=lambda c: (-((c.get("total") or 0) + (c.get("outcome_adjustment") or 0)),
                            (c.get("name") or "").lower()))
    return out


def attach_cross_client_signal(candidates: list[dict], signal: dict) -> list[dict]:
    """Attach cross_client_count/cross_client_note per row, from
    event_intel_audit.cross_client_signal()'s output.

    Pure information, never a reorder: nothing in this feature's spec asks
    it to move anything, unlike apply_outcome_pattern above. A row whose
    name_key is not in `signal`, or whose entry does not clear `fires`, gets
    no count at all -- not a zero, which would read as "checked and found
    none" about a check this run never actually ran for that event.
    """
    out = []
    for c in (candidates or []):
        c = dict(c)
        row = (signal or {}).get(c.get("name_key")) or {}
        if row.get("fires"):
            c["cross_client_count"] = row["count"]
            c["cross_client_note"] = (
                "Also kept by %d other clients with a similar buyer-access "
                "profile in the last quarter. Aggregate only: no client "
                "names are ever shown." % row["count"])
        else:
            c["cross_client_count"] = None
            c["cross_client_note"] = None
        out.append(c)
    return out
