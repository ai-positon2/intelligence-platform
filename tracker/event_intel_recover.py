"""The second read path, for listings a plain fetch cannot see.

Most exhibitor directories built after about 2020 render their list in the
browser. A server-side GET of one returns the shell: navigation, a footer, a
cookie banner and an empty container. The first version of this agent fetched
those, found nobody, and produced a roster that was honest about its sources
and useless in practice. That was the single biggest gap between what the
agent claimed and what it delivered.

This is the fallback: when a page cannot be read directly, ask Claude to find
what that listing published, using its web_search tool, which reaches indexed
and rendered copies a raw request does not.

That is a genuinely weaker kind of evidence, and the whole design here follows
from refusing to pretend otherwise:

  * Recovered rows are stored with provenance `search`, never `page`, and the
    source ledger gets its own `recovered` status rather than `ok`. The report
    shows the split. A reader can always tell which half of a roster was
    parsed and which half was found.

  * A recovery that ran NO searches is thrown away entirely. The tool call
    reports how many searches happened; zero means the model answered from
    training data, which is precisely the "never complete a partial list from
    memory" rule the direct extractor also enforces. This check costs nothing
    and catches the failure that would do the most damage.

  * A row with no citation URL is dropped. The instruction is to name the page
    each organisation was found on; a row that cannot say where it came from
    is not evidence, it is a recollection.

  * Recovery never runs on its own. It is attempted only for a page the direct
    read already failed on, so it adds coverage and never replaces a source
    that could have been parsed.
"""

from __future__ import annotations

import logging

from . import claude_websearch
from .event_intel_harvest import clean_domain
from .event_intel_store import (ROLES, SOURCE_ERROR, SOURCE_RECOVERED,
                                VIA_SEARCH)

logger = logging.getLogger(__name__)

# Enough to reach a directory and a couple of mirrors of it, not enough to
# wander into general knowledge about the event.
MAX_SEARCHES = 6

_SYSTEM = """You are recovering ONE published listing from a business event \
whose own page could not be read directly, because it builds its list in the \
browser.

Search for what that listing publishes and report the organisations on it.

RULES. These are checked after you answer.

1. Report ONLY organisations you found named on this event's own published \
listings for this edition, or on a page quoting them. Never add an \
organisation you know attends this event but did not find named. Never \
complete a partial list from memory. A short honest list is the correct \
answer; a long one containing three guesses is not.
2. Every row MUST carry `found_at`: the URL of the page where you saw that \
organisation named. A row without one will be discarded, so do not write one.
3. `role` must be how the listing presents them: exhibitor, sponsor, speaker, \
partner, media, or attendee_declared. Use attendee_declared ONLY where a page \
explicitly says that specific company or person is attending. An exhibitor is \
not an attendee and a sponsor is not an attendee.
4. `org_domain` only where you actually saw the organisation's own website. \
Never derive a domain from a company name.
5. If you cannot find the listing, say so in `note` and return an empty rows \
array. That is a useful answer.

Respond with ONLY a JSON object:
{"rows": [{"org_name": str, "org_domain": str|null, "role": str, \
"person_name": str|null, "person_title": str|null, "tier": str|null, \
"booth": str|null, "found_at": str}], "note": str, "found_listing": bool}"""


def recover_page(url: str, kind: str, event_name: str, event_host: str = "",
                 event_edition: str | None = None) -> dict:
    """Try to recover one unreadable listing by searching.

    Returns {"source": ledger-entry, "rows": [...]}. Never raises. The ledger
    entry it returns is a NEW row describing the recovery attempt; the caller
    keeps the original failed-fetch row too, so the record shows both that the
    page could not be read and what was done about it.
    """
    source = {"url": url, "kind": kind, "status": SOURCE_ERROR,
              "http_status": None, "rows_found": 0, "note": "",
              "recovery": True}

    what = {"exhibitors": "exhibitor directory", "sponsors": "sponsor list",
            "speakers": "speaker list", "partners": "partner list"}.get(
                kind, "%s listing" % kind if kind != "unknown" else "participant listing")
    user = ("Event: %s%s\nThe %s at %s could not be read directly.\n"
            "Find what that listing publishes and report it."
            % (event_name, " (%s)" % event_edition if event_edition else "",
               what, url))

    res = claude_websearch.ask(_SYSTEM, user, max_uses=MAX_SEARCHES,
                               max_tokens=8000)
    if res.get("error"):
        source["note"] = ("Recovery by search failed: %s: %s"
                          % (res["error"]["kind"], res["error"]["detail"]))[:500]
        return {"source": source, "rows": []}

    # The check that matters most. A reply produced without a single search is
    # a recollection of this event, not a reading of its listing, and it is
    # the one failure mode that would fill a roster with plausible names.
    if not res.get("search_count"):
        source["note"] = ("Recovery by search was discarded: the model answered "
                          "without running a single search, which means the "
                          "answer came from training data rather than from this "
                          "event's listing.")
        return {"source": source, "rows": []}

    parsed = claude_websearch.extract_json(res.get("text") or "", require="rows")
    if not isinstance(parsed, dict):
        source["note"] = ("Recovery by search ran but its answer could not be "
                          "read.")
        return {"source": source, "rows": []}

    rows, uncited = [], 0
    for r in (parsed.get("rows") or []):
        if not isinstance(r, dict):
            continue
        name = str(r.get("org_name") or "").strip()
        role = str(r.get("role") or "").strip().lower()
        if not name or role not in ROLES:
            continue
        found_at = str(r.get("found_at") or "").strip()
        if not found_at.lower().startswith(("http://", "https://")):
            # No citation, no row. This is the difference between "we found
            # this published here" and "we came up with this".
            uncited += 1
            continue
        rows.append({
            "org_name": name[:200],
            "org_domain": clean_domain(r.get("org_domain"), event_host),
            "role": role,
            "person_name": (str(r.get("person_name") or "").strip() or None),
            "person_title": (str(r.get("person_title") or "").strip() or None),
            "tier": (str(r.get("tier") or "").strip() or None),
            "booth": (str(r.get("booth") or "").strip() or None),
            # The page it was actually seen on, not the page that failed. A
            # row has to point at something a reader can open.
            "source_url": found_at[:800],
            "provenance": VIA_SEARCH,
        })

    notes = []
    if parsed.get("note"):
        notes.append(str(parsed["note"])[:300])
    if uncited:
        notes.append("%d recovered row%s named no source page and %s discarded."
                     % (uncited, "" if uncited == 1 else "s",
                        "was" if uncited == 1 else "were"))
    if rows:
        source["status"] = SOURCE_RECOVERED
        source["rows_found"] = len(rows)
        notes.append("Recovered by searching, after %d search%s, because the "
                     "page itself could not be read. These rows were found "
                     "published elsewhere rather than parsed from the event's "
                     "own page."
                     % (res["search_count"], "" if res["search_count"] == 1 else "es"))
    elif not notes:
        notes.append("Recovery by search found nothing published for this "
                     "listing.")
    source["note"] = " ".join(notes)[:800]
    return {"source": source, "rows": rows}


def should_recover(source: dict) -> bool:
    """Whether a failed source is worth a search recovery.

    Not every failure is. A 404 means the page is not there, and searching for
    the contents of a page that does not exist invites a model to find
    something adjacent and present it as the thing. Recovery is for pages that
    exist and would not give up their contents.
    """
    from .event_intel_store import SOURCE_BLOCKED
    return (source or {}).get("status") in (SOURCE_BLOCKED, SOURCE_ERROR)
