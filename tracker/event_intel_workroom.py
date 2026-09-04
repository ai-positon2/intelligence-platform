"""Work-the-room: the gtm-skills `event-radar` play, on the stack P2 has.

event-radar assumes three things this deployment does not own: a CRM to read
prior context out of, a sending sequence to hand drafts to, and an attendee
list arriving by webhook from Goldcast or Hopin or a paid Apify scrape. None
of those exist here, and pretending otherwise would produce a play that looks
complete and cannot be run.

What P2 does own is the roster this agent already harvested: the exhibitors,
sponsors, speakers and partners an event PUBLISHES. So the input changes and
the discipline stays. The play here is: take a roster you already have,
declare what your relationship to the event was, qualify it to the ICP, and
draft one opener per company that is true.

Four of event-radar's rules are the whole value of the play, and all four are
written here as things the code refuses rather than things the prompt asks:

  NEVER pretend you spoke to someone at the booth.
      A booth angle requires a note the USER wrote about that specific
      company. Any draft that claims a conversation without one is rejected
      and replaced, and the replacement says why. This is the rule a model
      breaks most eagerly, because "great chatting at the booth" is the most
      natural sentence in the genre.

  NEVER fire competitor-event follow-ups with aggressive displacement.
      Displacement language is scanned for on competitor-class events and the
      draft is rejected. "Soft angle only" is unmeasurable as an instruction
      and trivial as a check.

  If attendance data is anonymous, do not fire individual outreach.
      Most published roster rows are a company with no person on them. A row
      with no named person gets an account play, never an opener addressed to
      a person who was never identified.

  MUST qualify to ICP before mass-reach-out.
      Rows below the floor are cut and COUNTED, and the count is shown. "This
      event attracts a huge non-ICP tail" is the skill's own warning; a list
      that quietly keeps the tail has ignored it.

One thing here has no counterpart in the source skill, because it needs data
a chat-run play does not have. event-radar's Step 4 reads the CRM for prior
context. There is no CRM here, and that step is reported as unavailable
rather than faked. In its place this module reads THIS user's own prior event
runs: a company on the floor at three of your last five events is a different
prospect from one you have seen once, and that is a fact Postgres can answer
and a prompt cannot.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import logging
import re

from . import claude_websearch

logger = logging.getLogger(__name__)

# ── Step 1. The event class, declared and never inferred ──────────────────

CLASS_OWNED = "owned"
CLASS_EXHIBITED = "exhibited"
CLASS_ATTENDED = "attended"
CLASS_COMPETITOR = "competitor"
CLASS_PARTNER = "partner"
EVENT_CLASSES = (CLASS_OWNED, CLASS_EXHIBITED, CLASS_ATTENDED,
                 CLASS_COMPETITOR, CLASS_PARTNER)

# Straight from the skill's own table. The signal strength and the play both
# change with the class, so the class cannot be guessed: only the user knows
# whether they had a booth.
CLASS_PLAY = {
    CLASS_OWNED: {
        "label": "Our own event",
        "signal": "High",
        "why": "They chose you. Attending your event is an act of interest, "
               "not a coincidence of calendars.",
        "play": "Follow up on the topic they came for. Do not pivot to a "
                "different pitch: the session they picked is the angle.",
        "opener_rule": "Reference the specific session or topic. Never open "
                       "with a generic thank-you for attending.",
    },
    CLASS_EXHIBITED: {
        "label": "We had a booth",
        "signal": "Medium-high",
        "why": "You were both there and you had a stand, so a real "
               "conversation may have happened. May.",
        "play": "Reference the booth conversation, and only where someone "
                "actually wrote one down.",
        "opener_rule": "A conversation may be referenced ONLY where a booth "
                       "note exists for that company. Without one, treat it "
                       "as a shared-event opener.",
    },
    CLASS_ATTENDED: {
        "label": "We attended, no booth",
        "signal": "Medium",
        "why": "Same industry, same week, same room. That is real but it is "
               "not a relationship.",
        "play": "Lead with the shared experience and an actual takeaway from "
                "the event, then ask what stuck with them.",
        "opener_rule": "Offer a specific observation from the event. A "
                       "shared-attendance opener with nothing to say is worse "
                       "than no opener.",
    },
    CLASS_COMPETITOR: {
        "label": "A competitor's event",
        "signal": "Low-medium",
        "why": "They are in the market and shopping. That is the entire "
               "signal, and it is enough to warrant a soft approach.",
        "play": "Soft displacement only. Lead with the question this buyer "
                "persona actually asks, never with a comparison.",
        "opener_rule": "No displacement language, no competitor comparison, "
                       "no suggestion that they chose wrongly.",
    },
    CLASS_PARTNER: {
        "label": "A partner or adjacent vendor's event",
        "signal": "Medium",
        "why": "Same buyer pool, no conflict. The partner did the "
               "qualification for you.",
        "play": "Joint-buyer angle: the problem that sits next to the one the "
                "partner solves.",
        "opener_rule": "Name the adjacency. Never imply a partnership that "
                       "does not exist.",
    },
}


def play_for(event_class: str) -> dict:
    """The play for a declared event class.

    Raises rather than defaulting, for the same reason the rubric's
    orientation_for() raises: a wrong default here produces a competitor-event
    follow-up written as if it were an owned-event follow-up, which is the
    single most damaging thing this play can output, and nothing downstream
    would look wrong.
    """
    try:
        return CLASS_PLAY[event_class]
    except KeyError:
        raise ValueError(
            "Unknown event class %r. It must be one of: %s. This is never "
            "inferred: only the user knows whether they had a booth."
            % (event_class, ", ".join(EVENT_CLASSES)))


# ── The 48 to 72 hour window ──────────────────────────────────────────────

PRIME_HOURS = 48
WINDOW_HOURS = 72

WINDOW_PRIME, WINDOW_CLOSING, WINDOW_EXPIRED, WINDOW_EARLY = (
    "prime", "closing", "expired", "early")


def _as_date(value) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def window_state(ends_on, now: datetime.datetime | None = None) -> dict:
    """Where this event sits in the skill's 48-to-72-hour window.

    Returned as a state plus the hours, never as a block. An expired window is
    a fact the user should see at the top of the page, not a reason to refuse
    to produce the work they asked for: they may be writing a deliberately
    late follow-up and they do not need a tool arguing with them. What they do
    need is to not believe they are inside the window when they are not.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    end = _as_date(ends_on)
    if not end:
        return {"state": None, "hours": None, "known": False,
                "note": ("No end date is recorded for this event, so the "
                         "48-to-72-hour follow-up window cannot be placed. "
                         "The window is not assumed to be open.")}
    # End of the event's final day, in UTC. The event's own timezone is not
    # known, so this is deliberately the generous reading: it can be a few
    # hours optimistic, never pessimistic, and the note says so.
    ended = datetime.datetime.combine(
        end, datetime.time(23, 59), tzinfo=datetime.timezone.utc)
    hours = (now - ended).total_seconds() / 3600.0
    if hours < 0:
        return {"state": WINDOW_EARLY, "hours": round(-hours, 1), "known": True,
                "note": ("This event has not ended yet. The follow-up window "
                         "opens when it does, in about %d hours."
                         % round(-hours))}
    if hours <= PRIME_HOURS:
        return {"state": WINDOW_PRIME, "hours": round(hours, 1), "known": True,
                "note": ("%d hours since this event ended. This is the window "
                         "the play is built for." % round(hours))}
    if hours <= WINDOW_HOURS:
        return {"state": WINDOW_CLOSING, "hours": round(hours, 1), "known": True,
                "note": ("%d hours since this event ended. The 72-hour window "
                         "closes in about %d hours."
                         % (round(hours), round(WINDOW_HOURS - hours)))}
    return {"state": WINDOW_EXPIRED, "hours": round(hours, 1), "known": True,
            "note": ("%d days since this event ended, so the 72-hour window "
                     "has passed. Event freshness is no longer the reason to "
                     "reach out, and an opener that leans on it will read as "
                     "late." % round(hours / 24))}


# ── The booth rule ────────────────────────────────────────────────────────

_NONWORD = re.compile(r"[^a-z0-9]+")
_ORG_NOISE = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|bv|nv|sa|ag|plc|"
    r"holdings|group|technologies|technology|solutions|systems|software|"
    r"labs|the)\b")


def org_key(name: str) -> str:
    """A company name reduced to something two spellings of it agree on.

    "Acme Technologies, Inc." and "Acme Technologies" have to collide, or a
    booth note the user wrote against one spelling will not be found against
    the other, and the booth rule below will refuse a conversation that really
    happened. As in discovery's name_key, a name made entirely of noise words
    falls back to the plain form rather than collapsing to empty.
    """
    plain = " ".join(_NONWORD.sub(" ", (name or "").lower()).split())
    stripped = " ".join(_ORG_NOISE.sub(" ", plain).split())
    return stripped or plain


def index_booth_notes(raw: str | None) -> dict:
    """Parse the user's booth notes into {org_key: note}.

    One company per line, "Company: what was said". Free text on purpose: this
    is a rep typing up a day on the floor, and a form with required fields
    would simply not get filled in. What matters is not the format, it is that
    the note came from a person rather than from a model.
    """
    out: dict[str, str] = {}
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, _, note = line.partition(":")
        key, note = org_key(name), note.strip()
        if key and note:
            # Two lines about the same company are joined rather than one
            # silently winning: a rep who wrote twice said two things.
            out[key] = ("%s %s" % (out[key], note)).strip() if key in out else note
    return out


# Language that asserts a conversation took place. Any of it, on a row with no
# booth note the user wrote, is a fabricated interaction.
_CLAIMS_CONTACT = tuple(re.compile(p) for p in (
    r"\b(good|great|nice|lovely|enjoyed)\s+(chat|chatting|talking|speaking|meeting|catching)",
    r"\bgreat\s+to\s+(meet|see|chat|talk|connect)",
    r"\bgood\s+to\s+(meet|see|chat|talk|connect)",
    r"\bas\s+(promised|discussed|mentioned)",
    r"\byou\s+(mentioned|asked|said|told\s+me|brought\s+up|were\s+asking)",
    r"\bwe\s+(spoke|talked|chatted|met|discussed|covered)",
    r"\bour\s+(conversation|chat|discussion|talk)\b",
    r"\bwhen\s+we\s+(spoke|met|talked)",
    r"\b(thanks|thank\s+you)\s+for\s+(stopping|swinging|coming)\s+by",
    r"\bat\s+(our|the)\s+(booth|stand|table)\b",
    r"\bafter\s+(we|our)\s+(spoke|talked|met|chat)",
    r"\bfollowing\s+up\s+on\s+(our|that|the)\s+(chat|conversation|discussion)",
    r"\bpicking\s+up\s+where\s+we\s+left",
))

# Displacement, which the skill bans outright on a competitor's event.
_AGGRESSIVE = tuple(re.compile(p) for p in (
    r"\b(switch|switching|migrate|migrating|move\s+off|move\s+away)\b",
    r"\brip\s+and\s+replace\b",
    r"\breplace\s+(your|their|them)\b",
    r"\b(better|cheaper|faster|stronger)\s+than\b",
    r"\bunlike\s+\w+,",
    r"\bwhy\s+(customers|companies|teams)\s+(leave|left|churn)",
    r"\b(outperform|outgrow|beat)s?\b",
    r"\bmaking\s+the\s+switch\b",
    r"\bstuck\s+(with|on)\b",
    r"\btired\s+of\b",
    r"\bfed\s+up\b",
    r"\bdisappointed\s+(with|by)\b",
    r"\bcompetitor'?s?\s+(gaps|shortcomings|limitations)",
))


def claims_contact(text: str) -> list[str]:
    """Every phrase in this draft that asserts a prior interaction."""
    low = (text or "").lower()
    return sorted({m.group(0).strip() for p in _CLAIMS_CONTACT
                   for m in p.finditer(low)})


def is_aggressive(text: str) -> list[str]:
    """Every displacement phrase in this draft."""
    low = (text or "").lower()
    return sorted({m.group(0).strip() for p in _AGGRESSIVE
                   for m in p.finditer(low)})


# ── Step 3. Qualify to ICP, and cut the tail ──────────────────────────────

ICP_FLOOR = 55
BATCH = 12
MAX_CONCURRENCY = 3
# The web_search tool is offered here (max_uses > 0 in the call below), and a
# model asked to write a specific, non-generic angle for a named company
# routinely reaches for it to check what the company does. That makes this
# call's output budget subject to the same trap event_intel_scorer's was
# found to have live: the model narrates between search rounds, that
# narration spends the OUTPUT budget alongside the answer, and this call
# writes THREE fields per company for up to BATCH companies in one call.
#
# A live 6-event, 6-search SCORE batch needed 22,192 output tokens against an
# 8,000 budget and was truncated. This call's batch is twice the size (12
# companies) and writes comparably sized fields (fit_note, angle, opener), at
# a smaller search budget (4 vs 6), so the same 8,000 ceiling that failed at
# half this batch size cannot be trusted here either. Held above what the
# scorer needed, scaled for double the batch: not yet independently measured
# live for THIS call, so treat this as a floor to verify, not a proven number.
DRAFT_MAX_TOKENS = 40000

_SYSTEM = """You are qualifying companies from one event's published roster \
against one client's ICP, and drafting one opening line for each.

THE CLIENT
{profile}

THE EVENT
{event}

THE CLIENT'S RELATIONSHIP TO THIS EVENT: {class_label}. {class_why}
THE PLAY FOR THIS RELATIONSHIP: {class_play}
THE OPENER RULE FOR THIS RELATIONSHIP: {class_rule}

For each company give:
- `fit`, 0 to 100: how well this company matches the client's ICP. Events \
attract an enormous non-ICP tail, and cutting it is the point of this step. \
Most rosters are mostly tail. Do not flatter the list.
- `fit_note`: one sentence on why, naming what about the company decided it.
- `angle`: one sentence on the specific reason to contact THIS company after \
THIS event. Not a description of the client's product.
- `opener`: one or two sentences, the actual first line of the message.

ABSOLUTE CONSTRAINTS. These are checked after you answer and a draft that \
breaks one is thrown away, so writing one wastes the slot.

1. You were NOT at any conversation. Unless a booth note is supplied below \
for a company, you must not write anything implying you met, spoke to, \
chatted with, or promised anything to anyone there. No "great chatting", no \
"as promised", no "you mentioned". You know only that the company appeared on \
the published roster.
2. Where a booth note IS supplied for a company, use it, and use only what it \
actually says.
3. {competitor_rule}
4. Where no named person is given, the company is all you know. Write the \
opener for whoever is eventually identified, and do not invent a name, a \
title, or a person's action.
5. Never state the company attended. The roster says how they appeared: \
exhibitor, sponsor, speaker, partner. Use that word.

Respond with ONLY a JSON object:
{{"companies": [{{"org": str, "fit": int, "fit_note": str, "angle": str, \
"opener": str}}]}}

`org` must exactly match the company name you were given."""

_COMPETITOR_RULE_ON = (
    "This is a COMPETITOR'S event. Soft angle only. No displacement language, "
    "no comparison, no suggestion they chose wrongly, no 'switch' or 'replace' "
    "or 'better than'. Lead with the question this buyer actually has.")
_COMPETITOR_RULE_OFF = (
    "Do not disparage any other vendor, and do not position by comparison.")


def profile_brief(profile: dict) -> str:
    """The client, as the qualifier sees them."""
    bits = ["Client: %s" % (profile.get("client_name") or "unnamed")]
    for label, key in (("sells to", "buyer_roles"), ("verticals", "verticals"),
                       ("deal size", "acv_band"), ("sales cycle", "sales_cycle"),
                       ("geography", "geo_scope"), ("site", "website")):
        if profile.get(key):
            bits.append("%s: %s" % (label, profile[key]))
    return "\n".join(bits)


def event_brief(event: dict) -> str:
    bits = ["Event: %s" % (event.get("name") or "unnamed")]
    for label, key in (("dates", "starts_on"), ("ended", "ends_on"),
                       ("where", "location"), ("organiser", "organizer"),
                       ("site", "website")):
        if event.get(key):
            bits.append("%s: %s" % (label, event[key]))
    return "\n".join(bits)


def _roster_brief(rows: list[dict], notes: dict) -> str:
    from .event_intel_store import ROLE_LABELS
    out = []
    for r in rows:
        line = "- %s (on the roster as: %s)" % (
            r.get("org_name"), ROLE_LABELS.get(r.get("role"), r.get("role")))
        if r.get("person_name"):
            line += "\n  named person on the roster: %s%s" % (
                r["person_name"],
                ", %s" % r["person_title"] if r.get("person_title") else "")
        note = notes.get(org_key(r.get("org_name") or ""))
        if note:
            line += "\n  BOOTH NOTE WRITTEN BY THE USER: %s" % note
        out.append(line)
    return "\n".join(out)


def _clean_draft(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    org = str(raw.get("org") or "").strip()
    if not org:
        return None
    try:
        fit = int(raw.get("fit"))
    except (TypeError, ValueError):
        fit = None
    if fit is not None:
        fit = max(0, min(100, fit))
    return {"org": org, "fit": fit,
            "fit_note": str(raw.get("fit_note") or "").strip()[:600] or None,
            "angle": str(raw.get("angle") or "").strip()[:600] or None,
            "opener": str(raw.get("opener") or "").strip()[:900] or None}


def draft_batch(rows: list[dict], profile: dict, event: dict,
                event_class: str, notes: dict) -> dict:
    """Qualify and draft one batch. Never raises."""
    play = play_for(event_class)
    system = _SYSTEM.format(
        profile=profile_brief(profile), event=event_brief(event),
        class_label=play["label"], class_why=play["why"],
        class_play=play["play"], class_rule=play["opener_rule"],
        competitor_rule=(_COMPETITOR_RULE_ON if event_class == CLASS_COMPETITOR
                         else _COMPETITOR_RULE_OFF))
    user = ("Qualify and draft for these %d companies from the roster:\n\n%s"
            % (len(rows), _roster_brief(rows, notes)))
    res = claude_websearch.ask(system, user, max_uses=4, max_tokens=DRAFT_MAX_TOKENS)
    if res.get("error"):
        return {"drafts": {},
                "error": "%s: %s" % (res["error"]["kind"], res["error"]["detail"])}
    parsed = claude_websearch.extract_json(res.get("text") or "", require="companies")
    if not isinstance(parsed, dict):
        return {"drafts": {},
                "error": "The qualification pass ran but its answer could not be read."}
    out = {}
    for d in (parsed.get("companies") or []):
        clean = _clean_draft(d)
        if clean:
            out[org_key(clean["org"])] = clean
    return {"drafts": out, "error": None}


def draft_all(rows: list[dict], profile: dict, event: dict,
              event_class: str, notes: dict) -> dict:
    """Qualify and draft every roster row, in concurrent batches.

    A row the model never returned is kept and marked, exactly as an unscored
    candidate is in the recommendation play. Silence from the model is not
    evidence about the company.
    """
    batches = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    merged: dict = {}
    errors: list[str] = []
    if batches:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(MAX_CONCURRENCY, len(batches))) as pool:
            futures = [pool.submit(draft_batch, b, profile, event,
                                   event_class, notes) for b in batches]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:
                    logger.exception("event_intel_workroom: batch crashed")
                    errors.append("A qualification batch failed: %s" % str(e)[:200])
                    continue
                if r.get("error"):
                    errors.append(r["error"])
                merged.update(r.get("drafts") or {})

    out, missing = [], []
    for r in rows:
        row = dict(r)
        d = merged.get(org_key(row.get("org_name") or ""))
        if not d:
            row.update({"fit": None, "fit_note": None, "angle": None,
                        "opener": None, "unqualified": True,
                        "qualify_note": ("The qualification pass returned "
                                         "nothing for this company, so it is "
                                         "unscored rather than scored low.")})
            missing.append(row)
        else:
            row.update({"fit": d["fit"], "fit_note": d["fit_note"],
                        "angle": d["angle"], "opener": d["opener"],
                        "unqualified": False, "qualify_note": None})
        out.append(row)
    return {"rows": out, "errors": errors, "missing": len(missing),
            "batches": len(batches)}


# ── The enforcement pass ──────────────────────────────────────────────────
#
# Everything above asked the model nicely. This is where the asking stops.

DRAFT_OK = "ok"
DRAFT_NO_EVIDENCE = "rewritten_no_booth_note"
DRAFT_AGGRESSIVE = "rewritten_aggressive"
DRAFT_ACCOUNT = "account_play"


def fallback_opener(*, org: str, event_name: str, role_label: str,
                    event_class: str, client_name: str | None = None) -> str:
    """A true opener, built in code from facts already established.

    Deliberately not a second model call. A model that has just fabricated a
    booth conversation is not the thing to ask for a replacement, and a
    deterministic sentence that is merely serviceable beats a fluent one that
    might re-offend. It is written to be edited: the user knows what they
    actually have to say, and this gives them a true first line to say it
    after.
    """
    # A company name takes a singular verb and "we" takes a plural one.
    # Interpolating the name into a sentence written for "we" is what produced
    # "Northwind Analytics were there too" on every branded run.
    if client_name:
        subject, was = client_name, "was"
    else:
        subject, was = "We", "were"

    if event_class == CLASS_COMPETITOR:
        # Says nothing about where the sender was. The previous version opened
        # "We were not there", which nothing in the profile, the roster or the
        # declared class establishes, and which is simply false whenever a rep
        # attends a competitor's event for recon. No displacement language and
        # no comparison, per this class's rule.
        return ("I saw %s at %s. Teams looking at that end of the market tend "
                "to arrive at the same question, and it is worth twenty "
                "minutes if it is on your list too." % (org, event_name))
    if event_class == CLASS_OWNED:
        # Not a thank-you for attending, which this class's own opener_rule
        # forbids and which the previous version was. Without a session on
        # record the honest move is to name the agenda as the thing worth
        # picking up, and leave the sender to name which part.
        return ("%s joined us at %s. Something on that agenda earned the "
                "registration, and that is the thread I would rather pick up "
                "than send a general follow-up." % (org, event_name))
    if event_class == CLASS_PARTNER:
        return ("I noticed %s at %s. %s %s next door to that problem, and the "
                "overlap is usually worth a short conversation."
                % (org, event_name, subject,
                   "works" if client_name else "work"))
    if event_class == CLASS_EXHIBITED:
        return ("I saw %s on the %s list at %s. %s had a stand there too, and "
                "if we did not get to speak, there is one thing I would have "
                "asked." % (org, role_label.lower(), event_name, subject))
    # CLASS_ATTENDED. Deliberately not the exhibited sentence: the two used to
    # return byte-identical text, which quietly contradicted this module's
    # whole premise that the class changes the play. Having no booth is the
    # difference, and it is the honest thing to lead with when there is no
    # observation on record to offer instead.
    return ("I saw %s on the %s list at %s. %s %s in the audience that week "
            "rather than on the floor, so what I am curious about is how it "
            "looked from your side of it."
            % (org, role_label.lower(), event_name, subject, was))


def enforce(rows: list[dict], *, event_class: str, notes: dict,
            event_name: str, client_name: str | None = None) -> dict:
    """Apply the four rules to every draft, and rewrite what breaks them.

    Returns the rows with a `draft_status` and, where a draft was thrown away,
    the reason and the phrase that did it. The reason is kept and shown rather
    than swallowed: a user who can see that eleven drafts claimed a booth
    conversation that never happened learns something about the tool, and a
    silent rewrite teaches them nothing.

    Ordering matters. The anonymity rule is applied LAST, because a draft can
    both fabricate a conversation and be addressed to a company with nobody
    named on it, and the fabrication is the more serious of the two: it is the
    one that would go out over the user's name and be false.
    """
    from .event_intel_store import ROLE_LABELS
    play = play_for(event_class)
    out, rewritten = [], []
    for r in rows:
        row = dict(r)
        row["draft_status"] = DRAFT_OK
        row["draft_reason"] = None
        row["draft_flagged"] = []
        opener = row.get("opener") or ""
        key = org_key(row.get("org_name") or "")
        note = notes.get(key)
        role_label = ROLE_LABELS.get(row.get("role"), row.get("role") or "roster")

        # Rule 1. A conversation may be referenced only where a human wrote a
        # note about this specific company.
        claims = claims_contact(opener) if opener else []
        if claims and not note:
            row["draft_flagged"] = claims
            row["draft_status"] = DRAFT_NO_EVIDENCE
            row["draft_reason"] = (
                "This draft claimed a conversation (%s) that nobody recorded. "
                "No booth note was written for %s, so there is no evidence "
                "anyone spoke to them, and it has been replaced with an opener "
                "that only says what is known."
                % ("; ".join('"%s"' % c for c in claims), row.get("org_name")))
            opener = fallback_opener(
                org=row.get("org_name") or "this company", event_name=event_name,
                role_label=role_label, event_class=event_class,
                client_name=client_name)

        # Rule 2. Displacement, on a competitor's event.
        if event_class == CLASS_COMPETITOR and opener:
            harsh = is_aggressive(opener)
            if harsh:
                row["draft_flagged"] = harsh
                row["draft_status"] = DRAFT_AGGRESSIVE
                row["draft_reason"] = (
                    "This is a competitor's event, where the play is a soft "
                    "angle only, and this draft used displacement language "
                    "(%s). It has been replaced."
                    % "; ".join('"%s"' % h for h in harsh))
                opener = fallback_opener(
                    org=row.get("org_name") or "this company",
                    event_name=event_name, role_label=role_label,
                    event_class=event_class, client_name=client_name)

        # Rule 3. Nobody named means no personal outreach.
        if not (row.get("person_name") or "").strip():
            row["draft_status"] = (DRAFT_ACCOUNT if row["draft_status"] == DRAFT_OK
                                   else row["draft_status"])
            row["account_note"] = (
                "The roster names %s but no person at it, so this is an account "
                "play, not a message to send. Identify the right owner first: "
                "the opener is the second step, not the first."
                % (row.get("org_name") or "this company"))
        else:
            row["account_note"] = None

        row["opener"] = opener or None
        row["booth_note"] = note
        row["play"] = play["play"]
        if row["draft_status"] in (DRAFT_NO_EVIDENCE, DRAFT_AGGRESSIVE):
            rewritten.append({"org": row.get("org_name"),
                              "status": row["draft_status"],
                              "flagged": row["draft_flagged"]})
        out.append(row)
    return {"rows": out, "rewritten": rewritten,
            "rewritten_count": len(rewritten)}


def split_by_fit(rows: list[dict], floor: int = ICP_FLOOR) -> dict:
    """Cut the non-ICP tail, and count what was cut.

    The skill's warning is that conferences attract everyone. A list that
    keeps the tail has not qualified anything, and a list that drops it
    silently is indistinguishable from a small event. So both halves come
    back, and the report states the size of each.
    """
    kept, cut, unqualified = [], [], []
    for r in (rows or []):
        if r.get("unqualified") or r.get("fit") is None:
            unqualified.append(r)
        elif r["fit"] >= floor:
            kept.append(r)
        else:
            cut.append(r)
    kept.sort(key=lambda r: (-(r.get("fit") or 0),
                             (r.get("org_name") or "").lower()))
    cut.sort(key=lambda r: (-(r.get("fit") or 0),
                            (r.get("org_name") or "").lower()))
    return {"kept": kept, "cut": cut, "unqualified": unqualified, "floor": floor,
            "counts": {"kept": len(kept), "cut": len(cut),
                       "unqualified": len(unqualified),
                       "roster": len(rows or [])}}


# ── The CRM step, replaced by something this deployment can actually know ──

def repeat_signal(org_names: list[str], prior: dict) -> dict:
    """Which of these companies you have seen on an event floor before.

    event-radar's Step 4 reads the CRM for prior context: already in sequence,
    open deal, existing customer. There is no CRM wired to this platform and
    inventing that context would be the worst kind of confident wrong answer,
    so it is reported as unavailable.

    What IS knowable is this user's own event history in Postgres. A company
    exhibiting at three of the events you have looked at is buying floor space
    across your market, and that is a real and different signal from a company
    you are seeing once. `prior` maps org_key to the list of prior event names.
    """
    seen = []
    for name in (org_names or []):
        events = prior.get(org_key(name)) or []
        if len(events) >= 2:
            seen.append({"org": name, "count": len(events),
                         "events": sorted(events)[:6]})
    seen.sort(key=lambda s: (-s["count"], s["org"].lower()))
    return {
        "repeats": seen,
        "measured": bool(prior),
        "crm": None,
        "crm_note": (
            "No CRM is connected to this platform, so whether these companies "
            "are already in a sequence, already customers, or already on an "
            "open deal is not known here. Check before anyone sends anything."),
        "why_not_measured": (None if prior else
                             "This is the first event roster on this account, "
                             "so there is no history to compare it against."),
    }
