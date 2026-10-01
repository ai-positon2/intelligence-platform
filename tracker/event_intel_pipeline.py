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
import re
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


# ── what "incomplete" means ──────────────────────────────────────────────
#
# `completion_state` used to be 'partial' whenever anything at all was less
# than perfect, and that was every run: one candidate the confirmation could
# not settle, one confirmed event whose attendance figure could not be read,
# or one event kept out because it is sold out. The report then replaced its
# own answer with "Research incomplete" on ten runs out of ten, which is the
# same as saying nothing. A reader stops believing a warning that is always
# on.
#
# So 'partial' now means one of the things that changes what the list is
# worth: a kind of event whose SEARCH did not finish, a scoring or audit call
# that broke, or an event left unscored because the evidence to score it was
# missing. Everything else is still reported where it belongs, and none of it
# is hidden; it just does not make the whole report provisional.

# The sentences the policy and admission checks write when a RULE kept an
# event out. Each is a finished judgement about a real event (sold out,
# outside the window, on the client's own exclusion list), not a hole in the
# research, so an event unscored for one of these alone is not incompleteness.
_POLICY_REASONS = (
    "the organizer reports this edition is cancelled",
    "this edition is sold out",
    "this event is on the client exclusion list",
    "the edition is outside the requested date window",
    "the location could not be verified against the client geography",
    "the organizer page describes restricted or unavailable access",
)

# The two sentences search_category appends to a category's detail when the
# search itself finished but part of what it found could not be settled: a
# candidate the confirmation could not conclude on, or a confirmed event whose
# published numbers could not be read. The page reads the same two shapes out
# of runs stored before `gap_kind` existed, so change both together.
_VERIFICATION_BITS = re.compile(
    r"\d+ of the \d+ candidates? found here could not be checked to a "
    r"conclusion \(.*\)\.|\d+ confirmed events? had published numbers we "
    r"could not finish reading\.")
_BUDGET_TAIL = re.compile(
    r"It also used every one of the \d+ searches allowed for finding events "
    r"here, so there may be more to find than this search could reach\.")

GAP_FAILED = "failed"          # the search returned nothing usable
GAP_UNFINISHED = "unfinished"  # the search was cut off part-way
GAP_BUDGET = "budget"          # searched to the limit it was given
GAP_UNRESOLVED = "unresolved"  # finished, but some candidates unsettled
GAP_SHORT = "short"            # finished, and the market is thin
GAP_MET = "met"
MATERIAL_GAPS = (GAP_FAILED, GAP_UNFINISHED)


def category_gap_kind(st: dict) -> str:
    """One reading of a category's stored result, for every surface.

    Takes a `statuses` entry (`detail`) or a `shortfall` entry (`why`). The
    ring, the bars, the reasons under them, the headline qualifier and
    `completion_state` all read this one classification, because the report
    used to describe one partial category as "came up short" in its ring and
    "did not finish" two sections further down.
    """
    status = (st or {}).get("status")
    text = str((st or {}).get("why") or (st or {}).get("detail") or "")
    if status not in ("error", "partial"):
        if "short_by" in st:  # a shortfall row is short by construction
            return GAP_SHORT
        kept = st.get("kept", st.get("found"))
        return (GAP_SHORT if kept is not None
                and int(kept or 0) < event_intel_rubric.CATEGORY_QUOTA else GAP_MET)
    # A stored error naming max_uses_exceeded is this run's own search budget
    # being enforced, a complete piece of work and not a broken one.
    if re.search(r"max_uses_exceeded", text, re.I):
        return GAP_BUDGET
    if status == "error":
        return GAP_FAILED
    rest = _BUDGET_TAIL.sub("", text)
    if _VERIFICATION_BITS.search(rest) and not _VERIFICATION_BITS.sub("", rest).strip(" ."):
        return GAP_UNRESOLVED
    return GAP_UNFINISHED


def unscored_kind(reasons: list[str]) -> str:
    """'policy' when a rule alone kept the event out, else 'evidence'."""
    for r in reasons or []:
        low = str(r).strip().lower()
        if not any(low.startswith(p) for p in _POLICY_REASONS):
            return "evidence"
    return "policy"


def _n(count: int, one: str, many: str) -> str:
    return "%d %s" % (count, one if count == 1 else many)


def completion(statuses: dict, *, scoring_errors=(), audit=None,
               unscored=()) -> dict:
    """{'state', 'gaps', 'qualifier'}: the material gaps, as reader clauses.

    `qualifier` is the one sentence the report prints under its answer, and
    the detail of the 'warn' note, so the two cannot say different things.
    It is empty on a run with nothing material missing.
    """
    quota = event_intel_rubric.CATEGORY_QUOTA
    holes = []
    for cat, st in (statuses or {}).items():
        kind = st.get("gap_kind") or category_gap_kind(st)
        kept = st.get("kept", st.get("found"))
        # A category that delivered its quota delivered it, whatever else
        # its search said: the ring counts it as met, and so does this.
        if kind in MATERIAL_GAPS and int(kept or 0) < quota:
            holes.append(st.get("label") or event_intel_rubric.CATEGORY_LABELS.get(cat) or "")
    gaps = []
    if holes:
        gaps.append("the search for %s did not finish (%s)"
                    % (_n(len(holes), "kind of event", "kinds of event"),
                       ", ".join(h for h in holes if h)))
    if scoring_errors:
        gaps.append("scoring reported %s" % _n(len(scoring_errors), "error", "errors"))
    audit = audit or {}
    if audit.get("error"):
        gaps.append("the marquee-event audit produced no usable result")
    elif audit.get("failed"):
        gaps.append("the marquee-event audit could not be completed for %s"
                    % _n(len(audit["failed"]), "event", "events"))
    evidence = [u for u in unscored or () if u.get("kind") == "evidence"]
    scoring = [u for u in unscored or () if u.get("kind") == "scoring"]
    if evidence:
        gaps.append("%s could not be scored for lack of evidence"
                    % _n(len(evidence), "event", "events"))
    if scoring:
        gaps.append("the scoring pass returned no result for %s"
                    % _n(len(scoring), "event", "events"))
    qualifier = ""
    if gaps:
        joined = gaps[0] if len(gaps) == 1 else "%s and %s" % (", ".join(gaps[:-1]), gaps[-1])
        qualifier = ("Treat this as provisional: %s. What was not measured, "
                     "below, has the detail." % joined)
    return {"state": "partial" if gaps else "complete", "gaps": gaps,
            "qualifier": qualifier}


def _stamp_gap_kinds(found: dict) -> None:
    """Write the one classification onto the stored rows it describes."""
    for st in (found.get("statuses") or {}).values():
        st["gap_kind"] = category_gap_kind(st)
    for s in found.get("shortfall") or []:
        s["gap_kind"] = category_gap_kind(s)


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
    access_links = []
    # One listing read once. "/exhibitors", "/exhibitors/" and "http://..."
    # were three pages, and 30 exhibitors came out as 90 participants (roster
    # audit, 2026-10-01); so were two addresses redirecting to one page.
    read_pages, saved_rows = set(), set()
    unique = []
    for page in pages:
        key = _page_key(page.get("url"))
        if key and key not in read_pages:
            read_pages.add(key)
            unique.append(page)
    read_pages = set()
    held = []
    for page in unique:
        page = dict(page, edition=str(event.get("starts_on") or event.get("edition") or "")[:4],
                    cache_identity=cache_identity, event_website=event.get("website") or "")
        try:
            got = durable_stage("harvest:" + page["url"], event_intel_harvest.harvest_page, page, event.get("name") or "", host)
        except Exception as e:
            # One page must never take down the rest of the roster.
            logger.warning("event_intel_pipeline: harvest crashed on %s: %s",
                           page.get("url"), e)
            store.save_source(run_id, event_id, page.get("url") or "",
                              page.get("kind") or "unknown", "error",
                              # The exception is logged above; the ledger
                              # note is printed in the report.
                              note="This page could not be read because the "
                                   "step reading it stopped unexpectedly.")
            unreadable += 1
            continue

        src = got["source"]
        landed = _page_key(src.get("final_url") or src.get("url"))
        if src["status"] == SOURCE_OK and landed in read_pages:
            store.save_source(run_id, event_id, src["url"], src["kind"], "duplicate",
                              src.get("http_status"), 0,
                              "This address opened a page already read above, so "
                              "its rows were not counted twice.")
            continue
        read_pages.add(landed)
        # The same company listed again on another of this event's pages
        # (a sponsor shown on the exhibitor list too keeps both roles).
        fresh = []
        for r in got["rows"]:
            k = (event_intel_workroom.org_key(r.get("org_name") or ""),
                 (r.get("person_name") or "").lower(), r.get("role"))
            if k not in saved_rows:
                saved_rows.add(k)
                fresh.append(r)
        got = dict(got, rows=fresh)
        if src["status"] == SOURCE_OK:
            access_links.extend(src.get("access_links", []))
        store.save_source(run_id, event_id, src["url"], src["kind"], src["status"],
                          src.get("http_status"), src.get("rows_found", 0),
                          src.get("note", ""), metadata={k:src[k] for k in ("agenda_excerpts", "access_links", "snapshots", "extraction", "coverage", "partial", "pages_read", "pages_seen", "pages_declared", "truncated", "expected_edition", "observed_roster_years") if k in src})
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
                          rsrc.get("note", ""), metadata={"recovery_of": src["url"]})
        if rec["rows"]:
            # Search recovery must not erase a known edition mismatch.
            if (src.get('coverage') or {}).get('edition_mismatch'):
                continue
            recovered += 1
            # Held until every page has been read: a row recovered for one
            # page that another page lists directly is that page's row.
            # Saved as it came, IMEX America's recovered "exhibitors" were
            # its partner list again, and every partner was on the roster
            # twice (live run 31, 2026-10-01: 106 rows for 57 companies).
            held.extend(rec["rows"])
    fresh = []
    for r in held:
        k = (event_intel_workroom.org_key(r.get("org_name") or ""),
             (r.get("person_name") or "").lower(), r.get("role"))
        if k not in saved_rows:
            saved_rows.add(k)
            fresh.append(r)
    if fresh:
        total_rows += store.save_participants(run_id, event_id, fresh)
    if access_links:
        from .event_intel_access_review import inspect
        review = durable_stage('access-review:'+str(event_id), inspect, access_links,
                               event.get('website') or host,
                               str(event.get('starts_on') or event.get('edition') or ''))
        for check in review['checks']:
            store.save_source(run_id, event_id, check['url'], 'access_review', check['status'],
                              note=check['note'], metadata={'access_review':check,
                                  'access_review_scope':{k:v for k,v in review.items() if k != 'checks'}})
    return {"rows": total_rows, "readable": readable, "unreadable": unreadable,
            "recovered": recovered}


def _page_key(url) -> str:
    """One listing however its address is written: no scheme, no www, no
    fragment, no trailing slash, host in lower case."""
    from urllib.parse import urlsplit
    parts = urlsplit(str(url or "").strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parts.path or "/").rstrip("/") or "/"
    return host + path + ("?" + parts.query if parts.query else "")


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
        # By name: keyed on the domain when there was one, the same company
        # with and without its website counted twice. A speaker stored with
        # their own name as the organisation is not an organisation.
        key = event_intel_workroom.org_key(p.get("org_name") or "")
        if key and key != event_intel_workroom.org_key(p.get("person_name") or ""):
            orgs.add(key)
        if p.get("org_domain"):
            with_domain.add(p["org_domain"])

    # One roster page per address: the registration-page checks are not
    # roster pages, a duplicate address is not a second page, and a page
    # recovered by searching is that page's outcome rather than another page
    # (roster audit, 2026-10-01: 2 pages were reported as "2 of 5").
    pages: dict = {}
    for src in sources:
        if src.get("kind") == "access_review" or src.get("status") == "duplicate":
            continue
        meta = src.get("metadata") or {}
        url = meta.get("recovery_of") or src.get("url")
        status = src["status"]
        if status == SOURCE_OK and (meta.get("partial") or (meta.get("coverage") or {}).get("partial")):
            status = "partial"
        prev = pages.get(url)
        rank = {SOURCE_OK: 3, "partial": 2, SOURCE_RECOVERED: 1}
        if prev is None or rank.get(status, 0) > rank.get(prev, 0):
            pages[url] = status
    states = list(pages.values())
    return {
        "participants": len(participants),
        "organisations": len(orgs),
        "resolvable_domains": len(with_domain),
        "by_role": by_role,
        "declared_attendees": by_role.get(ROLE_ATTENDEE_DECLARED, 0),
        "sources_tried": len(states),
        "sources_read": states.count(SOURCE_OK) + states.count("partial"),
        "sources_partial": states.count("partial"),
        "sources_recovered": states.count(SOURCE_RECOVERED),
        "sources_unreadable": len([x for x in states
                                   if x not in (SOURCE_OK, "partial", SOURCE_RECOVERED)]),
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
        # A side ledger. A storage hiccup writing it failed the whole roster
        # with developer text (roster audit, 2026-10-01); it is logged instead.
        try:
            record_event(run_id, event)
        except Exception:
            logger.exception("event_intel_pipeline: evidence ledger write failed for run %s", run_id)
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
    _stamp_gap_kinds(found)

    if not found["candidates"]:
        statuses = found.get('statuses') or {}
        failed_kinds = {s.get('error_kind') for s in statuses.values()
                        if s.get('status') == event_intel_discover.STATUS_ERROR}
        # Failed only when no category search ran at all. One broken search
        # beside five that finished empty used to fail the whole run with
        # "Event research could not be completed." and nothing else, which
        # threw away five real findings about this client's market to report
        # one hole. Those runs now finish, show every category's result, and
        # carry the hole in their own qualifier.
        total = len(event_intel_rubric.CATEGORIES)
        none_ran = bool(found.get('categories_failed')) and (
            found['categories_failed'] >= total or
            (statuses and all(s.get('status') == event_intel_discover.STATUS_ERROR
                              for s in statuses.values())))
        error = None
        if none_ran:
            error = ("This account had used its event research allowance for "
                     "the last 24 hours, so the search did not run. The "
                     "allowance frees up as earlier runs' calls pass 24 hours "
                     "old; start the run again then."
                     if failed_kinds == {event_intel_discover.claude_websearch.ERR_ACCOUNT_BUDGET}
                     else "Event research could not be completed.")
        done = completion(statuses)
        holes = [s for s in statuses.values()
                 if (s.get('gap_kind') or category_gap_kind(s)) in MATERIAL_GAPS]
        # The banner and the coverage verdict under it read the same
        # classification. An empty result after a search that FINISHED is a
        # finding about the market; one after a search that did not is a hole,
        # and the banner must never hedge the first or assert the second.
        if holes:
            note = ("No verified candidates survived this run, and %s did not "
                    "finish, so these results do not establish that the market has "
                    "no suitable events. The category results below say which."
                    % _n(len(holes), "category search", "category searches"))
        else:
            note = ("No verified candidates survived this run. Every category "
                    "search finished, so this is a finding about this client's "
                    "market for the window searched rather than a gap in the "
                    "search. The category results below say what each found.")
        summary = {"mode": "recommend",
                   "completion_state": "failed" if none_ran else done["state"],
                   "completion": done,
                   "spend": dict(found.get('spend') or {}, usd=event_intel_discover.claude_websearch.spend_usd(found.get('spend') or {})),
                   "no_candidates": True,
                   "shortfall": found["shortfall"],
                   "statuses": found["statuses"],
                   "categories_failed": found["categories_failed"],
                   "note": note}
        store.update_run(run_id, status="failed" if none_ran else "complete",
                         stage="done", error=error, summary=summary)
        return

    # Comparisons propose alternatives; admission and scoring determine inclusion.
    store.update_run(run_id, stage="auditing")
    audit = durable_stage("audit", event_intel_audit.audit_famous, found["candidates"], profile)
    checkpoint('audit', audit.get('spend'))
    survivors = event_intel_audit.retain_for_scoring(found["candidates"], audit)

    # Keep originals in the pool to avoid cyclic or duplicate alternative lookup.
    promoted = durable_stage("promote", event_intel_audit.promote_alternatives,
        audit, survivors, replaced_from=found["candidates"], profile=profile)
    checkpoint('promote', promoted.get('spend'))
    if promoted["promoted"]:
        survivors = survivors + promoted["promoted"]

    # Step 4 and 8. One rubric, one pass, over everything that survived.
    from .event_intel_policy import eligibility
    policy_unconfirmed = []
    eligible = []
    from .event_intel_admission import inspect_all
    admission = durable_stage('source-admission', inspect_all, survivors)
    for candidate, source_check in zip(survivors, admission):
        reasons = eligibility(candidate, profile, today=_as_of_date(profile))
        reasons.extend(source_check['reasons'])
        if reasons:
            policy_unconfirmed.append(dict(candidate, scoring_note=' '.join(reasons),
                                           unscored_kind=unscored_kind(reasons)))
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
    summary_note_committed = committed_note(ranked)

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
    summary['source_admission'] = admission
    # Which events had their dates taken from the organizer's own structured
    # listing during discovery. The field lives on the discovered event and
    # is not a candidate column, so the report could never say it.
    summary['dates_from'] = {c.get('name'): c.get('dates_from')
                             for c in found['candidates'] + list(promoted['promoted'] or [])
                             if c.get('dates_from') and c.get('name')}
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
                  "cut": [] if audit.get("comparison_only") else audit.get("cut") or [],
                  "comparison_only": bool(audit.get("comparison_only")),
                  "preferred_alternatives": audit.get("cut") or [],
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
        # `kind` says why each one is unscored, because only some of those
        # reasons make the report provisional: a sold-out edition was checked
        # and ruled on, an event with no readable organizer dates was not.
        # Anything the scorer itself returned nothing for is a scoring gap.
        "unscored": [{"name": c.get("name"), "note": c.get("scoring_note"),
                      "kind": c.get("unscored_kind") or "scoring"}
                     for c in scored["unscored"]],
        "orientation": profile.get("orientation"),
        "committed_below_bar": ranked["committed_below_bar"],
        "committed_off_audience": ranked.get("committed_off_audience") or [],
        "committed_note": summary_note_committed,
        "outcomes": {"counts": outcomes["counts"], "ruled_on": outcomes["ruled_on"],
                     "note": outcomes["note"], "by_name": outcomes["by_name"],
                     "by_identity": outcomes["by_identity"],
                     "labels": store.DECISION_LABELS},
    })
    done = completion(found.get("statuses") or {}, scoring_errors=scored["errors"],
                      audit=audit, unscored=summary["unscored"])
    summary['completion_state'] = done['state']
    summary['completion'] = done
    if done['gaps']:
        # Added to `notes` and then flattened again, so `assumptions` (what
        # the CSV and older readers use) carries the same line. Appending it
        # after executive_summary had flattened left the two disagreeing.
        summary['notes'] = list(summary.get('notes') or []) + [
            {'level': 'warn', 'head': 'This shortlist is provisional',
             'detail': done['qualifier']}]
        summary['assumptions'] = event_intel_report.flatten(summary['notes'])
    # Failed only when the scorer itself produced nothing. A run whose every
    # event was kept out by policy or evidence has a complete answer (nothing
    # eligible) and a Not scored list saying why, and failing it hid both.
    failed = not rows and bool(scored['errors'] or any(
        u.get('kind') == 'scoring' for u in summary['unscored']))
    store.update_run(run_id, status='failed' if failed else 'complete', stage='done', summary=summary,
                     error='No event could be verified and scored.' if failed else None)


def _as_of_date(profile: dict):
    """The run's pinned date as a date, or None for "today"."""
    from datetime import date
    try:
        return date.fromisoformat(str((profile or {}).get("as_of") or "")[:10])
    except ValueError:
        return None


def committed_note(ranked: dict) -> str | None:
    """What the client has already paid for that this run would not pick.

    Money already spent on an event that does not clear the bar, or that
    is aimed at someone else's buyers, is the most actionable single line
    this analysis produces, so it is said in the summary rather than left
    for the reader to notice a badge."""
    committed_lines = []
    below = ranked["committed_below_bar"]
    if below:
        committed_lines.append(
            "%d event%s you are already committed to scored below %d and %s "
            "kept on the list anyway, marked: %s."
            % (len(below), "" if len(below) == 1 else "s",
               event_intel_rubric.RANK_FLOOR,
               "was" if len(below) == 1 else "were",
               ", ".join("%s at %s" % (c["name"], c["total"]) for c in below)))
    off = ranked.get("committed_off_audience") or []
    if off:
        committed_lines.append(
            "%d event%s you are already committed to %s an audience that is "
            "not mainly this client's buyers and %s kept on the list anyway, "
            "marked: %s."
            % (len(off), "" if len(off) == 1 else "s",
               "draws" if len(off) == 1 else "draw",
               "was" if len(off) == 1 else "were",
               ", ".join(c["name"] for c in off)))
    return " ".join(committed_lines) or None


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
    written = event_intel_workroom.index_booth_notes(booth_notes)

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
    domain_key: dict = {}
    for p in participants:
        key = event_intel_workroom.org_key(p.get("org_name") or "")
        if not key:
            continue
        # One company under two names ("Salesforce" and "Salesforce.com" are
        # caught by org_key; "Meta" and "Facebook" are not) is still one
        # inbox when both rows carry the same website.
        domain = (p.get("org_domain") or "").lower()
        if domain:
            key = domain_key.setdefault(domain, key)
        prev = by_org.get(key)
        # A row that names a person beats one that does not: the named
        # contact is the whole difference between a message and an account
        # play, and it must not be lost to insertion order.
        if prev is None or (not (prev.get("person_name") or "")
                            and (p.get("person_name") or "")):
            by_org[key] = p
    rows = list(by_org.values())
    merged_rows = len([p for p in participants if event_intel_workroom.org_key(p.get("org_name") or "")]) - len(rows)
    # Notes tied to the one company each names. A note that names none is
    # reported, not silently dropped (workroom audit, 2026-10-01).
    matched = event_intel_workroom.match_booth_notes(written, rows)
    notes = matched["by_org"]

    store.update_run(run_id, stage="qualifying")
    drafted = durable_stage("qualify", event_intel_workroom.draft_all,
        rows, profile, event, event_class, notes)

    store.update_run(run_id, stage="checking_drafts")
    enforced = event_intel_workroom.enforce(
        drafted["rows"], event_class=event_class, notes=notes,
        event_name=event_name, client_name=profile.get("client_name"),
        client_site=profile.get("website"))

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
        "booth_notes_unmatched": matched["unmatched"],
        # Notes about conversations at an event that has not happened yet
        # are either for another edition or a mistake; the drafts built on
        # them say "at our booth" all the same.
        "notes_before_event": bool(notes) and window.get("state") == event_intel_workroom.WINDOW_EARLY,
        "merged_rows": merged_rows,
        "window_ends_on": event.get("ends_on"),
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
            if kwargs.get("as_of") and not profile.get("as_of"):
                # The date the run was submitted, so a resume after midnight
                # asks the same questions and replays its stored replies.
                profile = dict(profile, as_of=kwargs["as_of"])
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
        elif mode == "discover":
            # Retired play (see the module docstring above). The only way a
            # job can carry this mode today is a leftover row that was queued
            # before discover was removed, or before a worker existed to ever
            # claim it. It must fail here, explicitly: falling through to the
            # `else` below would silently run it as a lookup instead, treating
            # an audience description as an event name and handing back a
            # nonsensical result rather than an honest error.
            store.update_run(
                run_id, status="failed", stage="retired",
                error=("Audience search was retired before this run could be "
                       "processed. No new discover runs can be started, and "
                       "this leftover one will not run now."))
            return
        else:
            _run_lookup(run_id, query, kwargs.get("year_hint"))
    except Exception as e:
        logger.exception("event_intel_pipeline: run %s crashed", run_id)
        from .event_intel_jobs import reader_failure
        store.update_run(run_id, status="failed", error=reader_failure(e))


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

    # A company Apollo has already answered for is never paid for again: a
    # retry after a partial failure, or a new set of job titles, re-bills only
    # what was never looked up. The people lookup is free, so it reruns for
    # every match, old or new, against the titles asked for now.
    known = {}
    looked = set()
    for p in participants:
        d = p.get("org_domain")
        if d and p.get("resolution") in ("matched", "no_match"):
            looked.add(d)
            if p.get("resolution") == "matched" and isinstance(p.get("apollo"), dict):
                known[d] = {k: v for k, v in p["apollo"].items() if k != "contacts"}
    pending = [d for d in domains if d not in looked]

    previous_stage = run.get("stage") or "done"
    store.update_run(run_id, stage="resolving_companies")
    recorded = []

    def spent(n):
        recorded.append(n)
        store.add_credits(run_id, n)
    res = (event_intel_enrich.resolve_companies(pending, on_credit=spent)
           if pending else {"by_domain": {}, "credits": 0, "unmatched": [],
                            "unattempted": [], "error": None})
    # Whatever the resolver reported but did not announce batch by batch is
    # still recorded: a credit spent is never dropped from the run.
    if (res.get("credits") or 0) > sum(recorded):
        store.add_credits(run_id, res["credits"] - sum(recorded))
    matched = dict(known, **(res.get("by_domain") or {}))

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
        elif domain not in looked:
            # Explicitly recorded, not left blank. "We looked and Apollo has
            # no record" is a different fact from "we never looked".
            store.update_participant_resolution(ids, None, None, "no_match")

    # Credits were recorded batch by batch as they were spent. The stage goes
    # back to what it was: this runs beside the run, not as a stage of it.
    store.update_run(run_id, stage=previous_stage)

    return {
        "resolved": len(matched),
        "unmatched": len(res.get("unmatched") or []),
        "unattempted": len(res.get("unattempted") or []),
        "credits": res.get("credits", 0),
        "people": people.get("total", 0),
        "error": _resolve_error(res, people),
    }


def _resolve_error(res: dict, people: dict) -> str | None:
    """What the Match companies button says when something went wrong.

    event_intel_enrich writes its errors for whoever reads the log
    ("APOLLO_API_KEY is not configured on this deployment.", "Apollo company
    lookup failed: HTTP 503 ..."), and the button used to throw the whole
    response away, so a missing key or a failed batch looked exactly like a
    lookup that found nothing. The detail is logged here; the person who
    pressed the button gets what happened and what it cost.
    """
    company, contacts = res.get("error"), people.get("error")
    if not (company or contacts):
        return None
    logger.warning("event_intel_pipeline: company resolution error: %s / %s",
                   company, contacts)
    if "APOLLO_API_KEY" in str(company or contacts or ""):
        return ("Company matching is not switched on for this workspace, so "
                "no company was looked up and no credits were spent.")
    if company:
        return ("The company lookup stopped part-way, so %s not looked up. "
                "Credits were spent only on the companies that were. Run it "
                "again to match the rest."
                % _n(len(res.get("unattempted") or []), "company was",
                     "companies were"))
    return ("Companies were matched, but the contact lookup could not be "
            "completed, so some of them show no contacts.")
