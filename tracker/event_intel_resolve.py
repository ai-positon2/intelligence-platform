"""Step 1 (RESOLVE) for Event & Conference Intelligence.

Turns a free-text event name into one canonical, dated, located event plus
the URLs of the pages that event publishes its participants on.

Two failure modes this module exists to prevent, both learned elsewhere in
this codebase:

1. **The confident false match.** tracker/sci_youtube_client.py found that a
   search for a plausible-sounding name returns a result that looks right and
   is not. Conferences are worse: the name is often a series ("SaaStr Annual"),
   the same name is reused every year, and unrelated events share words. So
   the model is required to return a `confidence` and an `edition`, and
   anything below `medium` is refused rather than harvested. Harvesting the
   wrong year's exhibitor list produces a roster that is entirely real,
   entirely verifiable, and entirely useless.

2. **The invented URL.** Never construct a roster URL from a pattern
   (`<site>/exhibitors`). The SCI report carries the same rule for social
   handles, for the same reason: a fabricated URL is indistinguishable from a
   real one until someone clicks it. Every URL here must be one the model
   actually found, and the harvester verifies each by fetching it.
"""

from __future__ import annotations

import datetime
import logging

from . import claude_websearch

logger = logging.getLogger(__name__)

# Page kinds worth harvesting, in the order they tend to be worth reading.
# `attendees` is last and is expected to be absent almost always -- events
# sell that list rather than publish it.
PAGE_KINDS = ("exhibitors", "sponsors", "speakers", "agenda", "partners", "attendees")

_MIN_CONFIDENCE = ("high", "medium")

_SYSTEM = (
    "You resolve a named business event, conference, trade show or summit to "
    "one specific edition, using web search, and you report where that "
    "edition publishes its participant lists.\n\n"
    "RULES.\n"
    "1. Resolve to ONE edition, not a series. Most events run annually under "
    "the same name, so 'edition' means the specific instance (for example "
    "\"2026\" or \"Spring 2026\"). If the user named a year, use it. If not, "
    "resolve the next upcoming edition, and if none is announced, the most "
    "recent past one, and say which in `reasoning`.\n"
    "2. VERIFY, do not pattern-match. A plausible-sounding name is not a "
    "match. Confirm the official website really belongs to this event and "
    "that the name matches, then set confidence: \"high\" when the official "
    "site confirms name, edition and dates; \"medium\" when the event is "
    "clearly identified but a detail is unconfirmed; \"low\" when several "
    "different events share the name and you cannot choose; \"none\" when you "
    "cannot find it at all. Return confidence \"low\" or \"none\" rather than "
    "picking the most likely candidate. Everything downstream harvests "
    "whatever you return here.\n"
    "3. NEVER construct a URL. Every URL in `pages` must be one you actually "
    "found and visited during this search. Do not append a guessed path like "
    "/exhibitors to the event's domain. A guessed URL is worse than a missing "
    "one because it looks real. If an event publishes no exhibitor list, "
    "return no exhibitors page.\n"
    "4. Do not report an attendee list unless the event genuinely publishes "
    "one openly. Almost none do. An exhibitor directory is NOT an attendee "
    "list, a sponsor page is NOT an attendee list, and a registration page is "
    "not one either. Leaving `pages` short is the correct answer.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"confidence": "high"|"medium"|"low"|"none", "reasoning": str, '
    '"name": str|null, "edition": str|null, "website": str|null, '
    '"organizer": str|null, "starts_on": "YYYY-MM-DD"|null, '
    '"ends_on": "YYYY-MM-DD"|null, "location": str|null, "venue": str|null, '
    '"format": "in_person"|"virtual"|"hybrid"|null, '
    '"stated_size": str|null, "audience_note": str|null, '
    '"pages": [{"url": str, "kind": "exhibitors"|"sponsors"|"speakers"|'
    '"agenda"|"partners"|"attendees", "note": str}]}\n\n'
    "`stated_size` is the event's OWN published attendance claim, quoted as "
    "they state it (\"12,000+ attendees\"), or null. Never estimate one. "
    "`audience_note` is who the event says it is for, in one sentence."
)


def _clean_pages(raw) -> list[dict]:
    """Keep only well-formed http(s) pages with a known kind, deduped by URL.

    A `javascript:` or `data:` URL reaching the report would be a live XSS
    sink the moment it is rendered as an href, so the scheme is allow-listed
    here rather than sanitised at render time in three separate places.
    """
    out, seen = [], set()
    for p in (raw or []):
        if not isinstance(p, dict):
            continue
        url = str(p.get("url") or "").strip()
        kind = str(p.get("kind") or "").strip().lower()
        low = url.lower()
        if not (low.startswith("https://") or low.startswith("http://")):
            continue
        if kind not in PAGE_KINDS or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "kind": kind, "note": str(p.get("note") or "")[:300]})
    return out


def _failed(confidence: str, reasoning: str) -> dict:
    return {"ok": False, "confidence": confidence, "reasoning": reasoning,
            "event": None, "pages": [], "error": None}


def resolve_event(query: str, year_hint: str | None = None) -> dict:
    """Resolve one named event. Never raises.

    Returns {"ok": bool, "confidence": str, "reasoning": str,
             "event": dict|None, "pages": [...], "error": {kind,detail}|None}.
    ok is True only at high/medium confidence with a real website, because a
    low-confidence resolution is precisely the case where harvesting produces
    a convincing roster for the wrong event.
    """
    query = (query or "").strip()
    if not query:
        return _failed("none", "No event name was provided.")

    # The prompt already says to prefer the next upcoming edition, but with no
    # date in the conversation the model has no way to tell upcoming from past
    # and will happily resolve last year's. Observed on 2026-09-02: a lookup
    # for INBOUND returned the 2025 edition, five days before the run's own
    # date would have made that obviously finished.
    user = ("Event: %s\nTODAY IS %s. An edition is upcoming only if it starts "
            "on or after that date." % (query, datetime.date.today().isoformat()))
    if year_hint:
        user += "\nEdition/year the user is asking about: %s" % year_hint

    res = claude_websearch.ask(_SYSTEM, user, max_uses=10, max_tokens=6000)
    if res.get("error"):
        err = res["error"]
        # The developer detail goes to the log; the person waiting on the
        # lookup gets a reason they can read. See claude_websearch.
        logger.warning("event_intel_resolve: lookup failed for %r (%s: %s)",
                       query, err["kind"], err["detail"])
        out = _failed("none", "The event lookup could not run: %s."
                      % claude_websearch.reader_reason(err))
        out["error"] = err
        return out

    # A reply that ran no search is a recollection, and this module's whole
    # subject is the confident false match: the same name is reused every year
    # and unrelated events share words, which is exactly what recall gets
    # wrong. It also confirms the famous-event audit's replacements, where the
    # comment promises "the same standard discovery is held to" and discovery
    # discards a recalled answer outright.
    if not res.get("search_count"):
        return _failed("none",
                       "The lookup was answered without a single search being "
                       "run, so this event was recalled rather than found, and "
                       "a remembered conference is the one thing this step "
                       "cannot rely on.")

    parsed = claude_websearch.extract_json(res.get("text") or "",
                                          require="confidence")
    if not isinstance(parsed, dict):
        logger.warning("event_intel_resolve: unparsable reply for %r "
                       "(blocks=%s, stop_reason=%s, chars=%s)", query,
                       res.get("text_block_count"), res.get("stop_reason"),
                       len(res.get("text") or ""))
        out = _failed("none", "The event lookup returned an unreadable response.")
        out["error"] = {"kind": claude_websearch.ERR_UNPARSABLE,
                        "detail": (res.get("text") or "")[:400]}
        return out

    confidence = str(parsed.get("confidence") or "none").lower()
    reasoning = str(parsed.get("reasoning") or "")[:1200]
    name = (parsed.get("name") or "").strip()
    website = (parsed.get("website") or "").strip()

    if confidence not in _MIN_CONFIDENCE or not name:
        # Deliberately not downgraded into a partial result. A named event we
        # could not pin to one edition has nothing safe to harvest.
        return _failed(confidence if confidence in
                       ("high", "medium", "low", "none") else "none",
                       reasoning or "The event could not be identified confidently.")

    event = {
        "name": name,
        "edition": (parsed.get("edition") or "").strip() or None,
        "website": website or None,
        "organizer": (parsed.get("organizer") or "").strip() or None,
        "starts_on": (parsed.get("starts_on") or None),
        "ends_on": (parsed.get("ends_on") or None),
        "location": (parsed.get("location") or "").strip() or None,
        "venue": (parsed.get("venue") or "").strip() or None,
        "format": (parsed.get("format") or "").strip() or None,
        "stated_size": (parsed.get("stated_size") or "").strip() or None,
        "audience_note": (parsed.get("audience_note") or "").strip() or None,
        "confidence": confidence,
        "reasoning": reasoning,
    }
    return {"ok": True, "confidence": confidence, "reasoning": reasoning,
            "event": event, "pages": _clean_pages(parsed.get("pages")),
            "error": None}


# `_DISCOVER_SYSTEM` and `discover_events()` lived here and are gone with the
# discover play. resolve_event() below stays: lookup runs on it, and so does
# the recommendation's alternative-promotion step, which has to confirm a
# replacement event really exists before it goes on a client's list.


def probe(query: str = "Web Summit") -> dict:
    """Admin self-test. Runs the real resolve path against a large, easily
    verifiable event and reports what actually came back, so an operator can
    tell 'the key is missing' from 'the tool version retired' from 'the model
    replied with prose'. Mirrors sci_identify.probe()."""
    res = resolve_event(query)
    ev = res.get("event") or {}
    return {
        "ok": bool(res.get("ok")),
        "query": query,
        "confidence": res.get("confidence"),
        "name": ev.get("name"),
        "edition": ev.get("edition"),
        "website": ev.get("website"),
        "starts_on": ev.get("starts_on"),
        "page_count": len(res.get("pages") or []),
        "page_kinds": sorted({p["kind"] for p in (res.get("pages") or [])}),
        "error": res.get("error"),
        "reasoning": (res.get("reasoning") or "")[:400],
    }
