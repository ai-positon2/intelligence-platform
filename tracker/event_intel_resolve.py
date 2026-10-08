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
import re

from . import claude_websearch

logger = logging.getLogger(__name__)

# Page kinds worth harvesting, in the order they tend to be worth reading.
# `attendees` is last and is expected to be absent almost always -- events
# sell that list rather than publish it.
PAGE_KINDS = ("exhibitors", "sponsors", "speakers", "agenda", "partners", "attendees")
# What a reply calls a page kind, beyond the exact plural asked for. Unmapped
# kinds ("Sponsor", "exhibitor", "floor_plan") were dropped, and a run whose
# every page was dropped reported "a real finding about the event" (roster
# audit, 2026-10-01).
_KIND_ALIASES = {
    "exhibitor": "exhibitors", "exhibitor_list": "exhibitors", "exhibitor list": "exhibitors",
    "exhibitor_directory": "exhibitors", "floor_plan": "exhibitors", "floorplan": "exhibitors",
    "floor plan": "exhibitors", "expo": "exhibitors", "directory": "exhibitors",
    "sponsor": "sponsors", "sponsorship": "sponsors", "sponsor_list": "sponsors",
    "speaker": "speakers", "speaker_list": "speakers", "faculty": "speakers",
    "programme": "agenda", "program": "agenda", "schedule": "agenda", "sessions": "agenda",
    "partner": "partners", "partner_list": "partners",
    "attendee": "attendees", "attendee_list": "attendees", "delegates": "attendees",
}


def _kind(raw) -> str:
    k = " ".join(str(raw or "").strip().lower().replace("-", "_").split())
    if k in PAGE_KINDS:
        return k
    return _KIND_ALIASES.get(k) or _KIND_ALIASES.get(k.replace(" ", "_")) or ""


def _url_key(url: str) -> str:
    """The same listing however its address was written."""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host + ((parts.path or "/").rstrip("/") or "/") + ("?" + parts.query if parts.query else "")

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
    "not one either. Leaving `pages` short is the correct answer.\n"
    "5. When you set confidence \"low\" or \"none\", list in `choices` the "
    "real events you found whose names are the same as, or close to, what "
    "the user typed, so the user can pick the one they meant: at most 6, the "
    "likeliest first. Each must be a DIFFERENT event (two editions of one "
    "event are one choice), with the official website you actually found for "
    "it and one sentence in `about` saying who runs it and who it is for. "
    "Leave `choices` empty when you are confident.\n\n"
    "Respond with ONLY a JSON object, no prose before or after:\n"
    '{"confidence": "high"|"medium"|"low"|"none", "reasoning": str, '
    '"name": str|null, "edition": str|null, "website": str|null, '
    '"organizer": str|null, "starts_on": "YYYY-MM-DD"|null, '
    '"ends_on": "YYYY-MM-DD"|null, "location": str|null, "venue": str|null, '
    '"format": "in_person"|"virtual"|"hybrid"|null, '
    '"stated_size": str|null, "audience_note": str|null, '
    '"organizer_run": true|false, "matchmaking_evidence": str|null, '
    '"country": str|null, "city": str|null, "availability": "open"|"sold_out"|"cancelled"|"unknown", "availability_source": str|null, '
    '"pages": [{"url": str, "kind": "exhibitors"|"sponsors"|"speakers"|'
    '"agenda"|"partners"|"attendees", "note": str}], '
    '"choices": [{"name": str, "organizer": str|null, "edition": str|null, '
    '"starts_on": "YYYY-MM-DD"|null, "location": str|null, "website": str, '
    '"about": str}]}\n\n'
    "`stated_size` is the event's OWN published attendance claim, quoted as "
    "they state it (\"12,000+ attendees\"), or null. Never estimate one. "
    "`matchmaking_evidence` quotes or closely paraphrases what the ORGANISER "
    "says they do to pair attendees, or null. Set `organizer_run` true only "
    "when the organiser takes active responsibility for pairing people "
    "against stated criteria; a conference app where attendees book their "
    "own meetings, invite-only admission, or a parent conference's "
    "programme is not that, so say so in the evidence and set it false.\n"
    "`audience_note` is who the event says it is for, in one sentence.\n"
    "`organizer` is the organising company or body's own name only, as it "
    "names itself (\"Web Summit\", \"Informa Connect\"), never a description "
    "of who founded or runs it."
)


# A description the model wrapped in brackets after the name. Printed in the
# drawer's heading line it read "organised by Web Summit (company founded by
# Paddy Cosgrave)" (live run 35, 2026-10-02). A one or two word bracket is
# kept, because "Informa (UBM)" is how an organiser can name itself.
_ORG_ASIDE = re.compile(r"\s*\((?:[^()]*\s){2,}[^()]*\)\s*$")


def organizer_name(value):
    """The organiser's name without a trailing descriptive aside."""
    v = (value or "").strip()
    stripped = _ORG_ASIDE.sub("", v).strip()
    return stripped or v


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
        kind = _kind(p.get("kind"))
        low = url.lower()
        if not (low.startswith("https://") or low.startswith("http://")):
            continue
        if not kind or _url_key(url) in seen:
            continue
        seen.add(_url_key(url))
        out.append({"url": url, "kind": kind, "note": str(p.get("note") or "")[:300]})
    return out


def _failed(confidence: str, reasoning: str) -> dict:
    return {"ok": False, "confidence": confidence, "reasoning": reasoning,
            "event": None, "pages": [], "error": None, "choices": []}


MAX_CHOICES = 6


def _clean_choices(raw) -> list[dict]:
    """The events an ambiguous lookup found, for the reader to pick from.

    Only an entry with a name and a real http(s) website survives: the
    website is what pins the follow-up lookup to that one event, and a
    choice without one would send it straight back into the ambiguity.
    Deduped by website, because two editions of one event are one choice.
    """
    from urllib.parse import urlparse
    clean = claude_websearch.strip_em_dash
    out, seen = [], set()
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue

        def field(key, cap=200):
            v = c.get(key)
            return "" if v is None or isinstance(v, (dict, list)) else clean(str(v).strip())[:cap]
        name, website = field("name"), str(c.get("website") or "").strip()[:500]
        site = urlparse(website)
        if not name or site.scheme not in ("http", "https") or not site.hostname:
            continue
        if _url_key(website) in seen:
            continue
        seen.add(_url_key(website))
        starts = field("starts_on", 10)
        try:
            starts = datetime.date.fromisoformat(starts).isoformat()
        except ValueError:
            starts = None
        out.append({"name": name, "website": website,
                    "organizer": organizer_name(field("organizer")) or None,
                    "edition": field("edition") or None, "starts_on": starts,
                    "location": field("location") or None,
                    "about": field("about", 300) or None})
        if len(out) == MAX_CHOICES:
            break
    return out


def pick_note(pick: dict) -> str:
    """The prompt lines that pin a lookup to the event the reader picked."""
    lines = ["%s: %s" % (label, pick.get(key)) for key, label in (
        ("name", "Name"), ("organizer", "Organiser"), ("edition", "Edition"),
        ("starts_on", "Starts"), ("location", "Location"), ("website", "Website"))
        if pick.get(key)]
    return ("\nSeveral events share this name. The user was shown the ones a "
            "previous lookup found and picked THIS one:\n" + "\n".join(lines) +
            "\nResolve this event and no other event that shares its name. "
            "Start from its website above. If it runs several editions, apply "
            "rule 1 to this event's own editions.")


def resolve_event(query: str, year_hint: str | None = None,
                  pick: dict | None = None) -> dict:
    """Find one named event, and report what the lookup cost.

    A thin wrapper for the same reason `event_intel_discover.confirm_event`
    has one: this function refuses a reply in several honest ways and each of
    them is billed. A ten-search lookup costs about $0.50 whether it lands or
    is refused, and the refused ones are the version worth being able to see.
    """
    box = {}
    out = _resolve_event(query, year_hint, box, pick)
    out["spend"] = box.get("spend") or claude_websearch.spend_sum()
    return out


def _resolve_event(query: str, year_hint: str | None, box: dict,
                   pick: dict | None = None) -> dict:
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
    if pick:
        user += pick_note(pick)

    # max_tokens raised 6000 -> 9000. Same live-run evidence as the budgets
    # in event_intel_discover and event_intel_audit: with web_search on, the
    # model narrates between search rounds and that narration spends the
    # output budget alongside the answer. The two resolve calls in that run
    # produced 2,399 and 5,410 output tokens, and 5,410 of 6,000 is 90% of
    # the budget on a sample of two.
    #
    # Running out here is not a soft failure. A truncated resolve is a
    # promoted alternative that cannot be read, and an alternative dropped
    # between the audit naming it and the store holding it is exactly the
    # defect `1beed4c` was opened for.
    res = claude_websearch.ask(_SYSTEM, user, max_uses=10, max_tokens=9000)
    box["spend"] = claude_websearch.spend_of(res)
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

    def text(key):
        """A field as text whatever JSON type it came back as: `"edition":
        2027` raised AttributeError on .strip() and failed the run with that
        exception's own text, after the lookup was paid for (roster audit)."""
        value = parsed.get(key)
        if value is None or isinstance(value, (dict, list)):
            return ""
        return str(value).strip()

    def iso(key):
        value = text(key)[:10]
        try:
            return datetime.date.fromisoformat(value).isoformat()
        except ValueError:
            return None

    confidence = text("confidence").lower() or "none"
    reasoning = claude_websearch.strip_em_dash(text("reasoning"))[:1200]
    name = claude_websearch.strip_em_dash(text("name"))
    website = text("website")

    from urllib.parse import urlparse
    parsed_site = urlparse(website)
    if confidence not in _MIN_CONFIDENCE or not name or parsed_site.scheme not in ('http','https') or not parsed_site.hostname:
        # Deliberately not downgraded into a partial result. A named event we
        # could not pin to one edition has nothing safe to harvest. What it
        # can do is hand back the events it did find, so the reader picks
        # one instead of retyping the name and paying for the same search.
        # A lookup that was already pinned to a pick offers no second list:
        # that would be a loop with a bill attached.
        out = _failed(confidence if confidence in
                      ("high", "medium", "low", "none") else "none",
                      reasoning or "The event could not be identified confidently.")
        if not pick:
            out["choices"] = _clean_choices(parsed.get("choices"))
        return out

    # Every free-text field the model wrote here (not `website`, not the
    # dates) goes through strip_em_dash: this event dict is what a promoted
    # alternative's name, edition and audience_note are built from, and a
    # dash left in `edition` here was found live in a client's top-five list.
    _clean = claude_websearch.strip_em_dash
    starts_on, ends_on = iso("starts_on"), iso("ends_on")
    # The year the user typed is the edition they asked for. A lookup for
    # 2025 that came back with 2027 was accepted and that edition harvested
    # without a word (roster audit, 2026-10-01).
    asked = re.search(r"\b(20\d{2})\b", str(year_hint or ""))
    found_year = (starts_on or "")[:4] or (re.search(r"\b(20\d{2})\b", text("edition")) or [None, None])[1]
    if asked and found_year and found_year != asked.group(1):
        return _failed(confidence, (
            "The lookup found the %s edition of %s, not the %s edition you asked "
            "for, so nothing was harvested. Run it again without a year to take "
            "the %s edition, or check that the %s edition exists."
            % (found_year, name, asked.group(1), found_year, asked.group(1))))
    event = {
        "name": name,
        "edition": _clean(text("edition")) or None,
        "website": website or None,
        "organizer": organizer_name(_clean(text("organizer"))) or None,
        "starts_on": starts_on,
        "ends_on": ends_on,
        "location": _clean(text("location")) or None,
        "country": _clean(text("country")) or None,
        # A city, not a venue address: "location" is a fallback only when it
        # is short enough to be one.
        "city": _clean(text("city") or (text("location") if len(text("location")) <= 80 else "")) or None,
        "availability": text("availability") if text("availability") in ("open","sold_out","cancelled") else "unknown",
        "availability_source": text("availability_source")[:1000] or None,
        "venue": _clean(text("venue")) or None,
        "format": text("format")[:16] or None,
        "stated_size": _clean(text("stated_size")) or None,
        "audience_note": _clean(text("audience_note")) or None,
        # A recommendation's promoted alternative is scored on these, the
        # same way a discovered event is: without them it could never earn
        # the matchmaking bonus the event it replaced kept.
        "organizer_run": parsed.get("organizer_run") is True,
        "matchmaking_evidence": _clean(text("matchmaking_evidence"))[:800] or None,
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
