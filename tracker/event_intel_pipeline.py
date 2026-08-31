"""Orchestration for Event & Conference Intelligence.

One daemon thread per run, mirroring tracker/sci_pipeline.py: each stage is
wrapped so one failure never blanks the stages that already succeeded, and
the run's `stage` column is advanced as it goes so a polling UI can say what
is happening rather than spinning on "running".

    lookup   resolve one event -> harvest its published pages -> summarise
    discover find candidate events for an audience -> harvest the top few
             -> rank by how many of the user's own target accounts appear

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
from urllib.parse import urlparse

from . import event_intel_enrich, event_intel_harvest, event_intel_resolve
from . import event_intel_store as store
from .event_intel_store import (ROLE_ATTENDEE_DECLARED, SOURCE_OK)

logger = logging.getLogger(__name__)

# Discover mode harvests only the best-fitting few, because harvesting is the
# expensive half in wall-clock and the ranking question is answered by a
# sample of the roster rather than all of it.
DISCOVER_HARVEST_TOP = 3

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
    total_rows, readable, unreadable = 0, 0, 0
    for page in pages:
        try:
            got = event_intel_harvest.harvest_page(page, event.get("name") or "", host)
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
                          src.get("note", ""))
        if src["status"] == SOURCE_OK:
            readable += 1
        else:
            unreadable += 1
        if got["rows"]:
            total_rows += store.save_participants(run_id, event_id, got["rows"])
    return {"rows": total_rows, "readable": readable, "unreadable": unreadable}


def _summarise(run_id: int) -> dict:
    """Build the run summary from what actually landed, not from what was
    attempted. Counts are read back out of the store so the summary can never
    disagree with the rows the report renders."""
    participants = store.get_participants(run_id)
    sources = store.get_sources(run_id)

    by_role: dict[str, int] = {}
    orgs, with_domain = set(), set()
    for p in participants:
        by_role[p["role"]] = by_role.get(p["role"], 0) + 1
        key = (p.get("org_domain") or p["org_name"]).lower()
        orgs.add(key)
        if p.get("org_domain"):
            with_domain.add(p["org_domain"])

    unreadable = [s for s in sources if s["status"] != SOURCE_OK]
    return {
        "participants": len(participants),
        "organisations": len(orgs),
        "resolvable_domains": len(with_domain),
        "by_role": by_role,
        "declared_attendees": by_role.get(ROLE_ATTENDEE_DECLARED, 0),
        "sources_tried": len(sources),
        "sources_read": len(sources) - len(unreadable),
        "sources_unreadable": len(unreadable),
        "roster_note": ROSTER_NOTE,
        "cost_estimate": event_intel_enrich.estimate_cost(sorted(with_domain)),
    }


def _run_lookup(run_id: int, query: str, year_hint: str | None) -> None:
    store.update_run(run_id, stage="resolving")
    res = event_intel_resolve.resolve_event(query, year_hint)
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


def _run_discover(run_id: int, audience: str, region: str | None,
                  targets: list[str]) -> None:
    store.update_run(run_id, stage="discovering")
    found = event_intel_resolve.discover_events(audience, region)
    if found.get("error"):
        store.update_run(run_id, status="failed", stage="discovering",
                         error="Event discovery could not run (%s)."
                         % found["error"]["detail"])
        return
    events = found.get("events") or []
    if not events:
        store.update_run(run_id, status="complete", stage="done",
                         summary={**_summarise(run_id), "no_events": True,
                                  "note": found.get("note") or
                                  "No events matched that description."})
        return

    saved: list[tuple[int, dict]] = []
    for e in events:
        eid = store.save_event(run_id, e)
        if eid is not None:
            saved.append((eid, e))

    # Harvest only the best-fitting few. The ranking question is answered by
    # a sample; harvesting eight full exhibitor directories to rank them would
    # cost minutes for information the fit score already carries.
    store.update_run(run_id, stage="harvesting")
    for event_id, event in saved[:DISCOVER_HARVEST_TOP]:
        if not event.get("website"):
            continue
        sub = event_intel_resolve.resolve_event(
            "%s %s" % (event["name"], event.get("edition") or ""))
        if sub.get("ok") and sub.get("pages"):
            _harvest_event(run_id, event_id, sub["event"], sub["pages"])

    summary = _summarise(run_id)
    summary["harvested_events"] = min(len(saved), DISCOVER_HARVEST_TOP)
    summary["discovered_events"] = len(saved)
    summary["discover_note"] = found.get("note") or ""
    if targets:
        summary["target_overlap"] = _target_overlap(run_id, targets)
    store.update_run(run_id, status="complete", stage="done", summary=summary)


def _target_overlap(run_id: int, targets: list[str]) -> dict:
    """How many of the user's own named accounts appear in each event's
    harvested roster. This is the number the whole discover mode exists to
    produce: it ranks events by the density of accounts you already care
    about, rather than by attendance."""
    from .event_intel_harvest import clean_domain
    wanted_domains = {d for d in (clean_domain(t) for t in targets) if d}
    wanted_names = {t.strip().lower() for t in targets if t.strip()}

    hits: dict[int, list[str]] = {}
    for p in store.get_participants(run_id):
        eid = p.get("event_id")
        if eid is None:
            continue
        dom = (p.get("org_domain") or "").lower()
        name = (p.get("org_name") or "").strip().lower()
        if (dom and dom in wanted_domains) or (name and name in wanted_names):
            bucket = hits.setdefault(eid, [])
            if p["org_name"] not in bucket:
                bucket.append(p["org_name"])
    return {str(k): v for k, v in hits.items()}


def run_job(run_id: int, mode: str, query: str, **kwargs) -> None:
    """Thread entry point. Never lets an exception escape: an unhandled one
    would leave the run stuck on 'running' forever with nothing said, which
    is the failure mode a polling UI cannot recover from."""
    try:
        if mode == "discover":
            _run_discover(run_id, query, kwargs.get("region"),
                          kwargs.get("targets") or [])
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

    for domain, ids in by_domain.items():
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
        "credits": res.get("credits", 0),
        "people": people.get("total", 0),
        "error": res.get("error") or people.get("error"),
    }
