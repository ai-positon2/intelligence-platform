"""Orchestration for Event & Conference Intelligence.

Queued runs execute in the durable event worker. Completed research stages
are checkpointed for replay; the run's `stage` column advances so the polling
UI can show progress. Failed runs remain explicitly incomplete.

    recommend  the gtm-skills conference-recommendation play: discover across
               six categories -> audit the famous names -> score every
               survivor on one rubric -> rank, excluding everything under 70
               -> check the list against what this user was handed for other
               clients -> assemble a five-element executive summary
    lookup     resolve one event -> harvest its published pages -> summarise
    workroom   the gtm-skills event-radar play over a roster this agent
               already harvested: declare the event class -> qualify the
               roster to the ICP -> draft one opener per company -> throw
               away every draft that claims a conversation nobody recorded

Apollo company resolution is deliberately NOT part of either path. It is the
only step that spends credits, so it is a separate, explicitly-triggered
route (see resolve_run_companies below), which is the same rule Contact
Finder arrived at over thirteen audit rounds: only an explicit user action
reaches a billed endpoint.

The summary this writes is the honest one. `roster_note` states, in the
report's own words, that what was collected is what the event publishes and
not the attendee list, and `sources_unreadable` carries the count of pages
that could not be read, so a short roster is never silently presented as a
complete one.
"""

from __future__ import annotations

import logging
from .event_intel_jobs import stage as durable_stage
from urllib.parse import urlparse

from . import (event_intel_audit, event_intel_discover, event_intel_enrich,
               event_intel_harvest, event_intel_report, event_intel_resolve,
               event_intel_recover, event_intel_rubric, event_intel_scorer,
               event_intel_workroom)
from . import event_intel_store as store
from .event_intel_store import (ROLE_ATTENDEE_DECLARED, SOURCE_OK,
                                SOURCE_RECOVERED, VIA_PAGE, VIA_SEARCH)

logger = logging.getLogger(__name__)

# `discover` was retired. It described an audience and got back events ranked
# by how many of the user's own named accounts appeared in the sampled
# rosters, and to anybody meeting this page for the first time it read as a
# shorter, worse `recommend`. Its pipeline is gone, so no new one can start.
#
# Runs already in the table keep their stored summary and still render, and
# the one live trace is the workroom roster picker, which still accepts a
# discover run as its source: a roster that was harvested is a roster,
# whatever play harvested it.

ROSTER_NOTE = (
    "This roster is what the event publishes openly: its exhibitors, sponsors, "
    "speakers and partners. Events do not publish their attendee list, so this "
    "is not one. Every row says which page it came from and how that page "
    "described them."
)


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        h = (urlparse(url).netloc or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _harvest_event(run_id: int, event_id: int, event: dict,
                   pages: list[dict]) -> dict:
    """Fetch and extract every page for one event, writing the source ledger
    as it goes. Returns counts for the summary."""
    host = _host(event.get("website"))
    from datetime import date
    from .event_intel_identity import event_key
    try:
        date.fromisoformat(str(event.get('starts_on') or ''))
        cache_identity = event_key(event)
    except ValueError:
        cache_identity = None
    total_rows, readable, unreadable, recovered = 0, 0, 0, 0
    for page in pages:
        page = dict(page, edition=str(event.get("starts_on") or event.get("edition") or "")[:4],
                    cache_identity=cache_identity)
        try:
            got = durable_stage("harvest:" + page["url"], event_intel_harvest.harvest_page, page, event.get("name") or "", host)
        except Exception as e:
            # One page must never take down the rest of the roster.
            logger.warning("event_intel_pipeline: harvest crashed on %s: %s",
                           page.get("url"), e)
            store.save_source(run_id, event_id, page.get("url") or "",
                              page.get("kind") or "unknown", "error",
                              note="Harvest failed unexpectedly: %s" % str(e)[:200])
            unreadable += 1
            continue

        src = got["source"]
        store.save_source(run_id, event_id, src["url"], src["kind"], src["status"],
                          src.get("http_status"), src.get("rows_found", 0),
                          src.get("note", ""), metadata={k:src[k] for k in ("snapshots", "extraction", "coverage", "pages_read", "pages_seen", "pages_declared", "truncated", "expected_edition", "observed_roster_years") if k in src})
        if src["status"] == SOURCE_OK:
            readable += 1
        else:
            unreadable += 1
        if got["rows"]:
            total_rows += store.save_participants(run_id, event_id, got["rows"])

        # The second read path. Only ever for a page the direct read already
        # failed on, so it adds coverage and never substitutes for a page that
        # could have been parsed. The failed attempt keeps its own ledger row
        # above: the record shows both that the page could not be read and
        # what was done about it.
        if (src.get("coverage") or {}).get("edition_mismatch") or not event_intel_recover.should_recover(src):
            continue
        try:
            rec = durable_stage("recover:" + src["url"], event_intel_recover.recover_page,
                src["url"], src["kind"], event.get("name") or "", host,
                event.get("edition"))
        except Exception as e:
            logger.warning("event_intel_pipeline: recovery crashed on %s: %s",
                           src["url"], e)
            continue
        rsrc = rec["source"]
        store.save_source(run_id, event_id, rsrc["url"], rsrc["kind"],
                          rsrc["status"], None, rsrc.get("rows_found", 0),
                          rsrc.get("note", ""))
        if rec["rows"]:
            # Search recovery must not erase a known edition mismatch.
            if (src.get('coverage') or {}).get('edition_mismatch'):
                continue
            recovered += 1
            total_rows += store.save_participants(run_id, event_id, rec["rows"])
    return {"rows": total_rows, "readable": readable, "unreadable": unreadable,
            "recovered": recovered}


def _summarise(run_id: int) -> dict:
    """Build the run summary from what actually landed, not from what was
    attempted. Counts are read back out of the store so the summary can never
    disagree with the rows the report renders."""
    participants = store.get_participants(run_id)
    sources = store.get_sources(run_id)

    by_role: dict[str, int] = {}
    by_provenance = {VIA_PAGE: 0, VIA_SEARCH: 0}
    orgs, with_domain = set(), set()
    for p in participants:
        by_role[p["role"]] = by_role.get(p["role"], 0) + 1
        prov = p.get("provenance") or VIA_PAGE
        by_provenance[prov] = by_provenance.get(prov, 0) + 1
        key = (p.get("org_domain") or p["org_name"]).lower()
        orgs.add(key)
        if p.get("org_domain"):
            with_domain.add(p["org_domain"])

    # A recovered source is neither read nor simply unreadable, and folding it
    # into either number would hide the thing the reader most needs to know.
    recovered_srcs = [s for s in sources if s["status"] == SOURCE_RECOVERED]
    unreadable = [s for s in sources
                  if s["status"] not in (SOURCE_OK, SOURCE_RECOVERED)]
    return {
        "participants": len(participants),
        "organisations": len(orgs),
        "resolvable_domains": len(with_domain),
        "by_role": by_role,
        "declared_attendees": by_role.get(ROLE_ATTENDEE_DECLARED, 0),
        "sources_tried": len(sources),
        "sources_read": len(sources) - len(unreadable) - len(recovered_srcs),
        "sources_recovered": len(recovered_srcs),
        "sources_unreadable": len(unreadable),
        "by_provenance": by_provenance,
        "provenance_note": (
            "%d row%s parsed from the event's own pages and %d recovered by "
            "searching, because those pages build their lists in the browser "
            "and cannot be read directly. Recovered rows carry the page they "
            "were found on."
            % (by_provenance.get(VIA_PAGE, 0),
               "" if by_provenance.get(VIA_PAGE, 0) == 1 else "s",
               by_provenance.get(VIA_SEARCH, 0))
            if by_provenance.get(VIA_SEARCH) else None),
        "roster_note": ROSTER_NOTE,
        "cost_estimate": event_intel_enrich.estimate_cost(sorted(with_domain)),
    }


def _run_lookup(run_id: int, query: str, year_hint: str | None) -> None:
    store.update_run(run_id, stage="resolving")
    res = durable_stage("resolve", event_intel_resolve.resolve_event, query, year_hint)
    if not res.get("ok"):
        # A named event we could not pin to one edition has nothing safe to
        # harvest. Failing here beats returning a convincing roster for the
        # wrong year, which is indistinguishable from a right one.
        store.update_run(run_id, status="failed", stage="resolving",
                         error=res.get("reasoning") or
                         "The event could not be identified confidently.",
                         summary={"confidence": res.get("confidence"),
                                  "roster_note": ROSTER_NOTE})
        return

    event = res["event"]
    event_id = store.save_event(run_id, event)
    if event_id is None:
        store.update_run(run_id, status="failed", stage="resolving",
                         error="The resolved event could not be saved.")
        return

    if __import__("os").environ.get("DATABASE_URL"):
        from .event_intel_evidence import record_event
        record_event(run_id, event)
    pages = res.get("pages") or []
    if not pages:
        store.update_run(
            run_id, status="complete", stage="done",
            summary={**_summarise(run_id),
                     "no_pages": True,
                     "no_pages_note": (
                         "This event was identified, but no page publishing its "
                         "exhibitors, sponsors or speakers could be found. That is "
                         "a real finding about the event rather than a failure: "
                         "many events publish nothing until closer to the date.")})
        return

    store.update_run(run_id, stage="harvesting")
    _harvest_event(run_id, event_id, event, pages)
    store.update_run(run_id, status="complete", stage="done",
                     summary=_summarise(run_id))


def _run_recommend(run_id: int, email: str, profile: dict) -> None:
    """The full recommendation play, one stage at a time.

    Every stage writes its own outcome into the summary even when it fails, so
    a run that lost its famous-event audit still produces a usable list that
    says the audit did not happen, rather than a list that looks audited.

    The one non-obvious ordering choice: candidates are SAVED and then READ
    BACK before ranking. The store recomputes each total from its own
    sub-scores, so ranking the rows that came back from Postgres guarantees the
    order on screen agrees with the bars on screen. Ranking the in-memory rows
    would let a bug in the recompute show up as a table sorted by numbers it is
    not displaying.
    """
    store.update_run(run_id, stage="discovering_categories")
    stage_spend = {}
    def checkpoint(stage, usage):
        stage_spend[stage] = usage or {}
        spend = event_intel_discover.claude_websearch.spend_sum(*stage_spend.values())
        spend['usd'] = event_intel_discover.claude_websearch.spend_usd(spend)
        spend['by_stage'] = dict(stage_spend)
        store.update_run(run_id, summary={'mode':'recommend','completion_state':'running','spend':spend})
    found = durable_stage("discover", event_intel_discover.discover, profile)
    checkpoint('discover', found.get('spend'))

    if not found["candidates"]:
        store.update_run(
            run_id, status="failed" if found['categories_failed'] else "complete", stage="done",
            error="Event research could not be completed." if found['categories_failed'] else None,
            summary={"mode": "recommend",
                     "completion_state": "failed" if found['categories_failed'] else "complete",
                     "spend": dict(found.get('spend') or {}, usd=event_intel_discover.claude_websearch.spend_usd(found.get('spend') or {})),
                     "no_candidates": True,
                     "shortfall": found["shortfall"],
                     "statuses": found["statuses"],
                     "categories_failed": found["categories_failed"],
                     "note": (
                         "No events were found for this profile. Every category "
                         "result is listed below, including the ones that failed "
                         "to run, so this can be told apart from a market with "
                         "genuinely nothing in it.")})
        return

    # Step 3. Marquee names justify themselves against a named alternative or
    # they come off the list.
    store.update_run(run_id, stage="auditing")
    audit = durable_stage("audit", event_intel_audit.audit_famous, found["candidates"], profile)
    checkpoint('audit', audit.get('spend'))
    survivors = event_intel_audit.apply_audit(found["candidates"], audit)

    # The other half of Step 3. Cutting a marquee event on the strength of a
    # named alternative and then never looking that alternative up leaves the
    # client with a shorter list and no replacement, while the run has already
    # worked out what the replacement should be. Each one is confirmed by its
    # own lookup before it is allowed onto the list.
    # `survivors` is what is already on the list, for dedup. The pre-audit
    # list is where the event each alternative REPLACES still exists, and it
    # is the only place a promoted event can pick up the category slot it
    # needs to survive the store.
    promoted = durable_stage("promote", event_intel_audit.promote_alternatives,
        audit, survivors, replaced_from=found["candidates"], profile=profile)
    checkpoint('promote', promoted.get('spend'))
    if promoted["promoted"]:
        survivors = survivors + promoted["promoted"]

    # Step 4 and 8. One rubric, one pass, over everything that survived.
    from .event_intel_policy import eligibility
    policy_unconfirmed = []
    eligible = []
    for candidate in survivors:
        reasons = eligibility(candidate, profile)
        if reasons:
            policy_unconfirmed.append(dict(candidate, scoring_note=' '.join(reasons)))
        else:
            eligible.append(candidate)
    survivors = eligible
    store.update_run(run_id, stage="scoring")
    scored = durable_stage("score", event_intel_scorer.score_all, survivors, profile)
    checkpoint('score', scored.get('spend'))

    scored["unscored"].extend(policy_unconfirmed)
    interchangeable = event_intel_scorer.flag_interchangeable(scored["scored"])
    banned = event_intel_scorer.flag_banned_language(scored["scored"])
    thin = event_intel_scorer.flag_thin_descriptions(scored["scored"])

    store.update_run(run_id, stage="ranking")
    saved = store.save_candidates(run_id, scored["scored"])
    rows = store.get_candidates(run_id)
    from .event_intel_identity import event_key
    expected = {event_key(c) for c in scored['scored']}
    actual = {event_key(c) for c in rows}
    if saved != len(scored['scored']) or len(rows) != saved or actual != expected:
        spend = event_intel_discover.claude_websearch.spend_sum(found.get('spend'), audit.get('spend'), promoted.get('spend'), scored.get('spend'))
        spend['usd'] = event_intel_discover.claude_websearch.spend_usd(spend)
        store.update_run(run_id, status='failed', stage='saving',
            error='The scored events could not all be saved. This report is incomplete.',
            summary={'mode':'recommend','completion_state':'failed','spend':spend,
                     'expected_saved':len(scored['scored']),'actual_saved':len(rows)})
        return
    if __import__("os").environ.get("DATABASE_URL"):
        from .event_intel_evidence import record_event
        for candidate in rows:
            record_event(run_id, candidate)
    cap = int(profile.get("max_events") or event_intel_rubric.DEFAULT_CAP)
    ranked = event_intel_rubric.rank(rows, cap=cap)
    if ranked["committed_below_bar"]:
        # Money already spent on an event that does not clear the bar is the
        # most actionable single line this analysis produces, so it is said in
        # the summary rather than left for the reader to notice a badge.
        summary_note_committed = (
            "%d event%s you are already committed to scored below 70 and %s "
            "kept on the list anyway, marked: %s."
            % (len(ranked["committed_below_bar"]),
               "" if len(ranked["committed_below_bar"]) == 1 else "s",
               "was" if len(ranked["committed_below_bar"]) == 1 else "were",
               ", ".join("%s at %s" % (c["name"], c["total"])
                         for c in ranked["committed_below_bar"])))
    else:
        summary_note_committed = None

    # What this user already decided about any of these. Attached, never used
    # to filter: a previously rejected event stays on the list carrying the
    # reason it was rejected.
    outcomes = event_intel_report.annotate_outcomes(
        ranked["kept"], store.get_outcomes(email, profile.get("id")))
    ranked["kept"] = outcomes["candidates"]

    # This client's own outcome history, as a visible ORDER signal within a
    # bucket rank() has already decided -- never a reason an event appears or
    # disappears. Run strictly after rank()'s bucket/cap decisions above,
    # never before: see rubric.outcome_adjustment's docstring for why moving
    # `total` itself would risk the exact fit-vs-priority exclusion this
    # feature is built not to do.
    pattern = store.outcome_pattern(email, profile.get("id"),
                                    exclude_run_id=run_id)
    ranked["kept"] = event_intel_report.apply_outcome_pattern(
        ranked["kept"], pattern)
    ranked["worth_a_look"] = event_intel_report.apply_outcome_pattern(
        ranked["worth_a_look"], pattern)

    # Neither cross-client interest nor list-overlap claims are reliable
    # until client identity, consent, and confidential-profile isolation exist.
    generic = event_intel_report.disabled_cross_client_check()

    summary = event_intel_report.executive_summary(
        profile=profile, ranked=ranked,
        shortfall=found["shortfall"], audit=audit, generic=generic,
        scoring_errors=scored["errors"], interchangeable=interchangeable,
        banned=banned, thin=thin, unscored=scored["unscored"],
        promoted=promoted, scoring_batches=scored.get("batches") or 0)
    # What the run cost, summed from every stage's own report rather than
    # from a shared counter: `run_job` is a thread entry point and two runs
    # can be in flight in one process, so a global would bill one client for
    # another's searches.
    #
    # This exists because the feature had no measured unit cost at all. The
    # only figure anyone had was $9.13, from a pipeline design that had since
    # been replaced; the first instrumented run came in at $9.64 with a
    # completely different shape.
    spend = event_intel_discover.claude_websearch.spend_sum(
        found.get("spend"), scored.get("spend"),
        audit.get("spend"), promoted.get("spend"))
    spend["usd"] = event_intel_discover.claude_websearch.spend_usd(spend)
    spend["by_stage"] = {
        "discover": found.get("spend") or {},
        "score": scored.get("spend") or {},
        "audit": audit.get("spend") or {},
        "promote": promoted.get("spend") or {},
    }

    summary.update({
        "mode": "recommend",
        "spend": spend,
        "shortfall": found["shortfall"],
        "statuses": found["statuses"],
        "categories_failed": found["categories_failed"],
        "discovered": found["found"],
        "audit": {"checked": audit["checked"], "error": audit.get("error"),
                  # Which marquee events could not be audited at all. Stored
                  # because `checked` counts what was SENT, and one call per
                  # event means some can fail while others succeed: without
                  # this a stored run reads as five audits with three
                  # verdicts and no account of the other two.
                  "failed": audit.get("failed") or {},
                  "cut": audit.get("cut") or [],
                  "promoted": [{"name": c.get("name"),
                                "replaces": c.get("audit_note")}
                               for c in promoted["promoted"]],
                  "unconfirmed": promoted["unconfirmed"],
                  "not_attempted": promoted["not_attempted"]},
        "generic": generic,
        # The second tier. Full rows, because these are offered as options
        # and are rendered with their dates, city and description the same
        # way the recommendation is.
        "worth_a_look": ranked["worth_a_look"],
        "excluded": ranked["excluded"],
        "over_cap": ranked["over_cap"],
        "finished": ranked["finished"],
        "unscored": [{"name": c.get("name"), "note": c.get("scoring_note")}
                     for c in scored["unscored"]],
        "orientation": profile.get("orientation"),
        "committed_below_bar": ranked["committed_below_bar"],
        "committed_note": summary_note_committed,
        "outcomes": {"counts": outcomes["counts"], "ruled_on": outcomes["ruled_on"],
                     "note": outcomes["note"], "by_name": outcomes["by_name"],
                     "by_identity": outcomes["by_identity"],
                     "labels": store.DECISION_LABELS},
    })
    partial = bool(found['categories_failed'] or scored['unscored'] or scored['errors'] or audit.get('error') or audit.get('failed') or promoted['unconfirmed'] or any(s.get('status') == 'partial' for s in found['statuses'].values()))
    summary['completion_state'] = 'partial' if partial else 'complete'
    if partial:
        summary.setdefault('notes', []).append({'level':'warn','head':'Research is incomplete', 'detail':'Some events or checks remain unverified. Review the coverage and unscored events before acting.'})
    failed = not rows and bool(scored['unscored'] or scored['errors'])
    store.update_run(run_id, status='failed' if failed else 'complete', stage='done', summary=summary,
                     error='No event could be verified and scored.' if failed else None)


def _run_workroom(run_id: int, email: str, source_run_id: int, profile: dict,
                  event_class: str, booth_notes: str | None,
                  ends_on_override: str | None = None) -> None:
    """event-radar, over a roster that is already on disk.

    The source run is re-read here rather than passed in, so this always works
    from what was actually stored. A roster held in memory from the request
    that started this run would be the caller's idea of the roster; the rows
    in Postgres are the roster.
    """
    store.update_run(run_id, stage="reading_roster")
    participants = store.get_participants(source_run_id)
    events = store.get_events(source_run_id)
    event = dict(events[0]) if events else {}
    event_name = event.get("name") or "this event"
    if ends_on_override:
        event["ends_on"] = ends_on_override

    window = event_intel_workroom.window_state(event.get("ends_on"))
    notes = event_intel_workroom.index_booth_notes(booth_notes)

    if not participants:
        store.update_run(
            run_id, status="complete", stage="done",
            summary={"mode": "workroom", "event_class": event_class,
                     "event_name": event_name, "window": window,
                     "no_roster": True,
                     "note": ("The run this was built from has no roster rows, "
                              "so there is nobody to qualify. Harvest the "
                              "event first.")})
        return

    # One row per company. A company on the floor as both exhibitor and
    # sponsor is one conversation, not two, and drafting twice for it would
    # produce two different openers for the same inbox.
    by_org: dict = {}
    for p in participants:
        key = event_intel_workroom.org_key(p.get("org_name") or "")
        if not key:
            continue
        prev = by_org.get(key)
        # A row that names a person beats one that does not: the named
        # contact is the whole difference between a message and an account
        # play, and it must not be lost to insertion order.
        if prev is None or (not (prev.get("person_name") or "")
                            and (p.get("person_name") or "")):
            by_org[key] = p
    rows = list(by_org.values())

    store.update_run(run_id, stage="qualifying")
    drafted = durable_stage("qualify", event_intel_workroom.draft_all,
        rows, profile, event, event_class, notes)

    store.update_run(run_id, stage="checking_drafts")
    enforced = event_intel_workroom.enforce(
        drafted["rows"], event_class=event_class, notes=notes,
        event_name=event_name, client_name=profile.get("client_name"))

    split = event_intel_workroom.split_by_fit(enforced["rows"])
    repeats = event_intel_workroom.repeat_signal(
        [r.get("org_name") for r in split["kept"]],
        store.prior_participant_events(email, exclude_run_id=source_run_id))

    store.update_run(run_id, stage="saving")
    expected_rows = split["kept"] + split["cut"] + split["unqualified"]
    saved = store.save_outreach(run_id, source_run_id, event_name, event_class, expected_rows)
    if saved != len(expected_rows) or len(store.get_outreach(run_id)) != len(expected_rows):
        store.update_run(run_id, status='failed', stage='saving', error='The drafts could not all be saved. Please retry.',
                         summary={'mode':'workroom','completion_state':'failed','expected_saved':len(expected_rows),'actual_saved':saved})
        return

    play = event_intel_workroom.play_for(event_class)
    store.update_run(run_id, status="complete", stage="done", summary={
        "mode": "workroom",
        "event_class": event_class,
        "event_class_label": play["label"],
        "event_class_signal": play["signal"],
        "event_class_why": play["why"],
        "play": play["play"],
        "event_name": event_name,
        "source_run_id": source_run_id,
        "window": window,
        "counts": split["counts"],
        "floor": split["floor"],
        "rewritten": enforced["rewritten"],
        "rewritten_count": enforced["rewritten_count"],
        "booth_notes_given": len(notes),
        "qualify_errors": drafted["errors"],
        "unqualified_count": drafted["missing"],
        "repeats": repeats,
        "roster_note": ROSTER_NOTE,
        "send_note": (
            "Nothing here has been sent and this platform has no sender. These "
            "are drafts to read, edit and send yourself."),
    })


def run_job(run_id: int, mode: str, query: str, **kwargs) -> None:
    """Thread entry point. Never lets an exception escape: an unhandled one
    would leave the run stuck on 'running' forever with nothing said, which
    is the failure mode a polling UI cannot recover from."""
    try:
        if mode == "recommend":
            profile = kwargs.get("profile") or {}
            if not profile.get("classification"):
                # The skill's HARD STOP, enforced here as well as at the route.
                # Nothing is discovered or scored until the classification is
                # locked, because it decides which side of the floor is scored.
                store.update_run(
                    run_id, status="failed", stage="discovering_categories",
                    error=("This run has no locked client profile, so there is "
                           "no way to know which side of the event floor to "
                           "score. Lock a profile and run it again."))
                return
            _run_recommend(run_id, kwargs.get("email") or "", profile)
        elif mode == "workroom":
            profile = kwargs.get("profile") or {}
            event_class = kwargs.get("event_class") or ""
            source_run_id = kwargs.get("source_run_id")
            if event_class not in event_intel_workroom.EVENT_CLASSES:
                # The same hard stop the recommendation play has, for the same
                # reason: the class decides the play, and a guess would write a
                # competitor follow-up in the voice of an owned-event one.
                store.update_run(
                    run_id, status="failed", stage="reading_roster",
                    error=("This run has no declared event class, so there is "
                           "no way to know what your relationship to the event "
                           "was. Declare it and run it again."))
                return
            if not source_run_id:
                store.update_run(
                    run_id, status="failed", stage="reading_roster",
                    error=("This run has no roster to work from. Run a lookup "
                           "on the event first, then work the room from it."))
                return
            _run_workroom(run_id, kwargs.get("email") or "", int(source_run_id),
                          profile, event_class, kwargs.get("booth_notes"),
                          kwargs.get("ends_on"))
        else:
            _run_lookup(run_id, query, kwargs.get("year_hint"))
    except Exception as e:
        logger.exception("event_intel_pipeline: run %s crashed", run_id)
        store.update_run(run_id, status="failed",
                         error="The run failed unexpectedly: %s" % str(e)[:300])


def resolve_run_companies(run_id: int, email: str, titles: list[str] | None = None) -> dict:
    """The one billed step, triggered explicitly.

    Resolves every participant domain in a run to an Apollo company, then does
    a free people lookup at whatever matched. Returns what it spent, so the
    caller can show it rather than leave a user to infer it.
    """
    run = store.get_run(run_id, email)
    if not run:
        return {"error": "not_found"}

    participants = store.get_participants(run_id)
    # Same company under several roles resolves once and updates every row,
    # which is both cheaper and stops one exhibitor showing different
    # firmographics in the exhibitor list and the sponsor list.
    by_domain: dict[str, list[int]] = {}
    for p in participants:
        d = p.get("org_domain")
        if d:
            by_domain.setdefault(d, []).append(p["id"])
    domains = sorted(by_domain)
    if not domains:
        return {"resolved": 0, "credits": 0, "people": 0,
                "note": ("No participant had a published website link, so there is "
                         "nothing to look up. Company names alone are not enough: "
                         "guessing a domain from a name attaches real firmographics "
                         "to the wrong company.")}

    store.update_run(run_id, stage="resolving_companies")
    res = event_intel_enrich.resolve_companies(domains)
    matched = res.get("by_domain") or {}

    people = {"by_domain": {}, "total": 0, "error": None}
    if matched:
        people = event_intel_enrich.find_people(sorted(matched), titles=titles)

    # Domains no call ever covered, because a batch failed and the ones after
    # it never ran. Their rows are left exactly as they were: unresolved is the
    # truthful state for a company nobody looked up.
    unattempted = set(res.get("unattempted") or [])

    for domain, ids in by_domain.items():
        if domain in unattempted:
            continue
        company = matched.get(domain)
        if company:
            payload = dict(company)
            contacts = (people.get("by_domain") or {}).get(domain) or []
            if contacts:
                payload["contacts"] = contacts
            store.update_participant_resolution(ids, domain, payload, "matched")
        else:
            # Explicitly recorded, not left blank. "We looked and Apollo has
            # no record" is a different fact from "we never looked".
            store.update_participant_resolution(ids, None, None, "no_match")

    if res.get("credits"):
        store.add_credits(run_id, res["credits"])
    store.update_run(run_id, stage="done")

    return {
        "resolved": len(matched),
        "unmatched": len(res.get("unmatched") or []),
        "unattempted": len(res.get("unattempted") or []),
        "credits": res.get("credits", 0),
        "people": people.get("total", 0),
        "error": res.get("error") or people.get("error"),
    }
