"""Step 2 of the recommendation: discover with breadth, one category at a time.

The source skill names its own single biggest failure mode here:

    The default failure is pattern-matching to famous conferences [...]
    Famous-event bias produces lists that look identical across wildly
    different clients. They should not.

Its countermeasure is six named discovery categories with a quota of two each,
found by ACTIVE SEARCH rather than recall. The obvious implementation is one
prompt listing all six categories, and it does not work: a model handed six
categories and asked for fifteen events answers mostly out of category one,
because that is where its recall is densest, and then labels a flagship a
"vertical summit" to fill the quota. The categories become decoration.

So this searches ONE CATEGORY AT A TIME, six times, each search able to
return only events of that one kind and with no way to satisfy itself from
another. A category that genuinely has nothing has to come back empty and say
why.

Each category is then TWO stages rather than one, which is the second thing
this module learned the hard way. The first live end-to-end run gave every
category a single call and asked it to find events and confirm them out of
one budget of eight searches. All six saturated that budget, five reported
nothing, and the run produced three events. A server-side search call re-sends
everything it has already read on every later turn, so one call's input bill
grows with the SQUARE of its search count and a bigger budget makes it worse
rather than better.

    stage one   name candidates in this category. Names only.
    stage two   one separate call per candidate, each carrying only the pages
                it opened itself, to confirm the event and read its numbers.

The same searches, split this way, cost about a quarter as much and run in
parallel. They also buy something the single call could not have: the finder
no longer grades its own homework. Confirmation is a search that did not
propose the event, and it is allowed to say no.

Four statuses, never collapsed into each other:

    ok      the search ran, and what it found was confirmed
    empty   the search ran and this category genuinely has nothing here
    partial some of it worked, and the report says which part did not
    error   the search did not run

`empty` and `error` are the pair that matters, and confirmation gives the
distinction a second place to go wrong. "Three plausible names, all checked,
none with an edition in the client's window" is a finding about the market and
is `empty`. "Three plausible names we could not check" is a hole and is
`error`. Only the reason tells them apart, so the reason is always recorded.

A SPENT BUDGET IS NONE OF THE FOUR. This is the correction that mattered most
here. `max_uses_exceeded` is how the web_search tool enforces the caller's own
`max_uses`, and every call in this module saturates its budget by design, so
for a while the wrapper classed each of those replies as a failed search and
this module threw the whole reply away. A live Beta Bionics run lost four of
its six categories that way, spent half an hour doing it, and produced one
event. The candidates those searches found had already been named in the reply
that was discarded.

So a spent budget is not a status. It rides alongside as `budget_spent` and is
mentioned only where it changes what a reader should conclude: on a category
that came up short, where "it looked with everything it had" and "it looked
with searches to spare" are different findings and only the first is worth
spending more on.

This module produces FACTS ONLY. Nothing here scores anything. Scoring is a
separate pass over the merged set so that one consistent standard is applied
to all six categories, rather than each category's finder grading its own
homework.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import datetime
import threading
from urllib.parse import urlparse

from . import claude_websearch
from . import event_intel_rubric as rubric

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"
# Some events confirmed, but the search behind them was cut short. Distinct
# from ok (a finished search) and from error (nothing usable came back),
# because the shortfall for this category is real but its events are not
# suspect.
STATUS_PARTIAL = "partial"

# How many web-search calls may be in flight at once, across the whole run.
#
# Six concurrent multi-search calls reliably tripped rate limiting, which is
# why discovery used to run three categories at a time. That limit was always
# about calls in flight rather than about categories, and this module now
# makes several small calls per category instead of one large one, so the cap
# lives on the only thing it was ever really about: the API call itself.
#
# The semaphore is held around `ask` and NOTHING else, deliberately. A
# category waits for its own confirmations to come back, so a category that
# kept its slot while waiting would deadlock the moment every slot was held
# by a category doing the same thing.
MAX_INFLIGHT = 4
_INFLIGHT = threading.Semaphore(MAX_INFLIGHT)

# Kept as the pool width for categories. Every category can now be submitted
# at once because _INFLIGHT, not the pool, is what throttles the API.
MAX_CONCURRENCY = len(rubric.CATEGORIES)

# Asked-for per category. One above the quota of two, so that a single
# unusable result does not put the category under quota on its own.
PER_CATEGORY = 4

# Search budgets, per call.
#
# These numbers are the answer to the first live end-to-end run. That run gave
# each category ONE call with a budget of eight searches, and asked it to both
# find events and confirm them. All six saturated the budget. Five categories
# came back with nothing, one ran out of output tokens mid-answer, and three
# events survived the whole run.
#
# The fix is not a bigger budget. A server-side search call re-sends every
# result it has already collected on every later turn, so the input bill for a
# single call grows with the SQUARE of its search count. The six calls in that
# run spent 163k, 276k, 457k, 549k, 490k and 293k input tokens for eight
# searches each, and the two slowest took sixteen and nineteen minutes.
# Doubling to sixteen searches would have cost roughly four times as much per
# call and taken over an hour.
#
# Splitting the same searches across several small calls breaks that square
# into pieces. Finding names is one call. Confirming each name is its own
# call, carrying only the pages it opened itself. Sixteen searches spread that
# way cost about what eight cost in one call, and they run in parallel.
FIND_MAX_USES = 6
FIND_MAX_TOKENS = 3000
CONFIRM_MAX_USES = 6
CONFIRM_MAX_TOKENS = 4000

# What a confirmation concluded. Three outcomes, never two: an event the
# confirmer SEARCHED and ruled out is a fact about the market, while an event
# it could not check is a hole in the analysis. Collapsing them would let a
# failed confirmation read as "we checked, and it does not qualify".
CONFIRM_OK = "confirmed"
CONFIRM_REJECTED = "rejected"
CONFIRM_UNCHECKED = "unconfirmed"


def _ask(system: str, user: str, **kw) -> dict:
    """claude_websearch.ask, throttled to MAX_INFLIGHT calls in flight."""
    with _INFLIGHT:
        return claude_websearch.ask(system, user, **kw)

# ── stage one: find names ─────────────────────────────────────────────────
#
# This call does one job: name events that belong to this category and are
# worth confirming. It does NOT extract published numbers, dates or costs,
# because that is what used to make one call do eight searches and then run
# out of room to answer.

_FIND_SYSTEM = """You find real business events that a specific company should \
consider attending, exhibiting at or sponsoring. You are searching ONE \
category of event at a time and you must not return events from other \
categories.

THE CATEGORY YOU ARE SEARCHING NOW: {category_label}
What that means: {category_brief}

THE CLIENT
{profile}

WHERE THIS CLIENT'S BUYERS PHYSICALLY ARE AT AN EVENT: {where_buyers}
That is not a detail. It decides which side of the floor matters, so judge \
an event by the side of it this client would actually work.

TODAY IS {today}. Every judgement about whether an event is upcoming, and \
every reading of the client's time window, is measured from that date and not \
from your own sense of when now is.

YOUR ONLY JOB IS TO NAME CANDIDATES. Something else confirms them afterwards \
and reads their published numbers, so do not gather dates, attendee counts, \
ticket prices or exhibitor counts here. Spend your searches on FINDING events \
rather than on studying the ones you have already found. Naming six plausible \
candidates is more useful than fully researching one.

RULES.
1. Use web search. Do not answer from memory. A plausible-sounding conference \
name that does not exist costs somebody a travel budget, so every candidate \
must be one you saw on a real page during this search, and `website` must be \
a URL you actually opened. A candidate you cannot cite is one you did not \
find, so leave it out.
2. Return ONLY events that genuinely belong to the category above. If this \
category has nothing for this client, return an empty array and explain \
concretely in `note` what you searched for and why nothing fits. An honest \
empty category is a finding. A flagship relabelled to fill a quota is not.
3. Prefer events whose next edition starts on or after {today}. You are not \
being asked to verify the date here, only to avoid naming events you already \
know are finished for good.

Respond with ONLY a JSON object, no prose before or after:
{{"candidates": [{{"name": str, "website": str|null, "why": str}}], \
"note": str, "search_complete": true|false}}

`name` is the event as it brands itself, without the year. `why` is one \
sentence saying why it belongs to the "{category_label}" category \
specifically.

`note` is printed in the client's report as the reason this category came \
back the way it did, so write it as a finding about THEIR MARKET and nothing \
else: at most two sentences, in the third person, about what this category \
holds for them or why it holds nothing. Do not narrate what you did, what you \
intended to do, or what you were unable to do, and do not mention searching, \
tools, budgets or limits: those are recorded separately and printing them \
here hands the client a problem they cannot act on. If you have nothing to \
say about their market, return an empty string. A note that describes your \
own process is dropped, so it costs you the chance to say anything at all.

YOUR SEARCH BUDGET IS {max_uses} SEARCHES. That is a deliberate limit, not an \
accident, and using every one of it is the expected outcome rather than a \
problem. When you reach for one more the tool will answer with \
`max_uses_exceeded`. That is this budget being enforced. It is not a fault, \
it is not the tool breaking, and it does not invalidate anything you found \
before it. Plan for {max_uses} searches and answer with what they gave you.

`search_complete` is the most important field in this object when it is \
false, so it has to mean one thing only. Set it to FALSE only if a search you \
needed came back BROKEN: rate-limited, unavailable, erroring, or returning \
nothing where results should have been, or if something stopped you before \
you could write this answer. Set it to TRUE if your searches worked, \
including when you used all {max_uses} of them and would have liked more. \
Running out of a budget is not the same as being cut off, and reporting it as \
one tells the client their category went unsearched when in fact it was \
searched to the limit we paid for. An empty `candidates` array with \
search_complete true means "I searched this category properly and it \
genuinely has nothing for this client", and it is reported to the client in \
exactly those words. The same empty array with search_complete false means "a \
search I needed did not work", which is a completely different statement."""


# ── stage two: confirm one name ───────────────────────────────────────────
#
# A separate call per candidate, each carrying only the pages it opened
# itself. That is what keeps the input bill flat, but it buys something the
# single-call version could not have: the finder no longer grades its own
# homework. This module already refused to let each category score its own
# events, for exactly this reason. Confirmation is the same principle applied
# one step earlier, and it is allowed to say no.

_CONFIRM_SYSTEM = """You confirm whether ONE named business event is real, \
upcoming and worth putting in front of a specific company, and you report \
what that event publishes about itself.

THE EVENT TO CONFIRM: {event_name}
Someone proposed it as: {category_label} ({category_brief})
They said: {why}
{website_line}
THE CLIENT
{profile}

WHERE THIS CLIENT'S BUYERS PHYSICALLY ARE AT AN EVENT: {where_buyers}
When you describe who attends, describe the side of the floor this client \
would actually work.

TODAY IS {today}. Every judgement about whether an edition is upcoming is \
measured from that date and not from your own sense of when now is.

YOU ARE THE CHECK, NOT THE ADVOCATE. Whoever proposed this event may simply \
be wrong: the event may not exist, may have been discontinued, may have no \
announced future edition, or may belong to a different category. Saying so is \
a useful answer and it is the answer this step exists to produce. Do not \
stretch to make a proposal work.

RULES.
1. Use web search and open the event's own pages. Do not answer from memory. \
Return at least one URL in `sources`; an event you cannot cite is an event \
you did not confirm.
2. Confirm the next edition that STARTS ON OR AFTER {today}. An edition that \
has already finished cannot be attended. If only a past edition exists, set \
`confirmed` false and say so. If the next edition is announced but undated, \
confirm it with null dates rather than guessing one.
3. `attendees` and `booths` are the event's OWN published claims, quoted as \
they state them ("12,000+ attendees", "430 exhibitors"), or null. NEVER \
estimate either. A number you invented is indistinguishable from one they \
published.
4. `matchmaking_evidence` must quote or closely paraphrase what the ORGANISER \
says they do. If the only thing on offer is a conference app where attendees \
book their own meetings (Whova, Brella, Swapcard and the like), say exactly \
that: it is a real and useful answer. Set `organizer_run` true only when the \
organiser takes active responsibility for pairing people against stated \
criteria.
5. `famous` is honest self-assessment: true if you could have named this event \
without searching. Being famous is not disqualifying, it just gets audited.
6. `cost_note` is any published cost to attend, exhibit or sponsor, quoted as \
published, or null. It is recorded as context for the client's decision and \
is never used to score anything, so do not soften or inflate it.
7. If the event is real and upcoming but does NOT belong to the \
"{category_label}" category, still confirm it and say so plainly in \
`category_fit`. Miscategorised is not the same as useless.

Respond with ONLY a JSON object, no prose before or after:
{{"confirmed": true|false, "reject_reason": str|null, "facts_complete": \
true|false, "event": {{"name": str, "edition": str|null, "website": str|null, \
"organizer": str|null, "starts_on": "YYYY-MM-DD"|null, \
"ends_on": "YYYY-MM-DD"|null, "country": str|null, "city": str|null, \
"days": int|null, "industry": str|null, "attendees": str|null, \
"booths": str|null, "audience_note": str|null, "format": \
"in_person"|"virtual"|"hybrid"|null, "cost_note": str|null, \
"organizer_run": true|false, "matchmaking_evidence": str|null, \
"famous": true|false, "category_fit": str, "confidence": \
"high"|"medium"|"low", "sources": [str]}}}}

`name` is the event as it brands itself. Correct the proposed name if the \
real one differs; you are looking at the page and they were not.

`reject_reason` is required when `confirmed` is false, and it must say which \
of these happened in plain words: the event does not exist, it has been \
discontinued, it has no edition starting on or after {today}, or you could \
not find enough to tell. Set `event` to null when you reject.

YOUR SEARCH BUDGET IS {max_uses} SEARCHES for this one event. Using all of \
them is expected. When you reach for one more the tool answers with \
`max_uses_exceeded`, which is that budget being enforced rather than anything \
going wrong, and it takes nothing away from what you already read.

`facts_complete` is false if a search you needed came back BROKEN, \
rate-limited or unavailable before you had finished reading this event's own \
pages, or if you ran out of searches with a specific number still unread. It \
is true if your searches worked and you simply reported the fields this \
event publishes. A confirmed event with `facts_complete` false keeps its \
confirmation and is reported as one whose published numbers we could not \
finish reading. Never pad a field you did not get to; a null there is honest \
and an invented number is not."""


def profile_brief(profile: dict) -> str:
    """The locked intake, rendered for a prompt.

    Budget is deliberately absent. It is recorded on the profile and shown in
    the report beside each event, but it never reaches a model that is
    describing or (later) scoring one, because the skill is explicit that a
    cheap event reaching the wrong buyers is worse than an expensive one
    reaching the right ones.
    """
    p = profile or {}
    lines = ["Client: %s" % (p.get("client_name") or "unnamed")]
    if p.get("website"):
        lines.append("Website: %s" % p["website"])
    lines.append("Classification: %s"
                 % rubric.CLASSIFICATION_LABELS.get(p.get("classification"),
                                                    p.get("classification") or "?"))
    for label, key in (("Buyer roles", "buyer_roles"), ("Target verticals", "verticals"),
                       ("Deal size", "acv_band"), ("Sales cycle", "sales_cycle"),
                       ("Geographic scope", "geo_scope")):
        if p.get(key):
            lines.append("%s: %s" % (label, p[key]))
    lines.append("Time window: the next %s months" % (p.get("window_months") or 12))
    if p.get("force_include"):
        lines.append("The client is ALREADY COMMITTED to these events. Return "
                     "them if they fall in your category so they can be scored "
                     "alongside the rest; the client needs to know what their "
                     "committed events are worth, not to have them left out: %s"
                     % p["force_include"])
    if p.get("force_exclude"):
        lines.append("Do NOT return these (already attended, known duds, "
                     "disqualified): %s" % p["force_exclude"])
    return "\n".join(lines)


# ── dedup ─────────────────────────────────────────────────────────────────

_NOISE = re.compile(r"\b(20\d\d|conference|conf|summit|expo|show|the|annual|"
                    r"europe|emea|apac|usa|us|uk|na|world|global|international)\b")
_NONWORD = re.compile(r"[^a-z0-9]+")


def name_key(name: str) -> str:
    """A comparison key for event names.

    "SaaStr Annual 2026", "SaaStr Annual" and "Saastr annual conference" are
    one event found three times. Stripping the year and the generic show words
    is what stops the same event occupying three of fifteen slots, which would
    be the loudest possible version of the famous-event bias this whole stage
    exists to prevent.
    """
    plain = " ".join(_NONWORD.sub(" ", (name or "").lower()).split())
    stripped = " ".join(_NOISE.sub(" ", plain).split())
    # A name made entirely of generic show words ("The 2026 Conference") strips
    # to nothing, and an empty key would make merge() drop a real event on the
    # floor. Fall back to the unstripped form rather than losing the row.
    return stripped or plain


def host_key(url: str) -> str:
    if not url:
        return ""
    try:
        h = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    return h[4:] if h.startswith("www.") else h


# The region words name_key strips. Kept as their own signal because dropping
# them is right in one direction and wrong in the other: "MarTech Summit
# Europe" and "MarTech Summit" are one event found twice, while "Money20/20
# USA" and "Money20/20 Europe" are two events on two continents with two buyer
# sets, and merging them silently deletes one of them from the client's year.
_REGION_WORDS = frozenset((
    "europe", "emea", "apac", "usa", "us", "uk", "na", "world", "global",
    "international", "america", "americas", "asia", "japan", "china", "india",
    "australia", "canada", "germany", "france", "london", "berlin", "paris",
    "singapore", "dubai", "amsterdam", "vegas",
))


def region_key(name: str) -> str:
    """The region words in a name, normalised, or "" if it names no region."""
    plain = " ".join(_NONWORD.sub(" ", (name or "").lower()).split())
    found = sorted({t for t in plain.split() if t in _REGION_WORDS})
    return " ".join(found)


def _tokens(key: str) -> tuple:
    return tuple(key.split())


def _contains_tokens(hay: tuple, needle: tuple) -> bool:
    """Whole-token containment, never a raw substring.

    This is the difference between "CES" matching "CES 2027" and "CES"
    matching "ProCESsing Summit". The second one used to silently delete a real
    recommendation with no trace anywhere in the report.
    """
    n = len(needle)
    if not n or n > len(hay):
        return False
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def names_match(a: str, b: str) -> bool:
    """One event under two names.

    Two tests: the stripped names must contain one another token for token,
    AND their regions must not contradict. A name with no region is compatible
    with any region, which is what keeps "MarTech Summit" and "MarTech Summit
    Europe" together.
    """
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return False
    ta, tb = _tokens(ka), _tokens(kb)
    if not (_contains_tokens(ta, tb) or _contains_tokens(tb, ta)):
        return False
    ra, rb = region_key(a), region_key(b)
    return not ra or not rb or ra == rb


def site_key(url: str) -> str:
    """Host AND path, because the host alone is not an event.

    Deduping on the bare host merged every event an organiser or a vendor runs:
    AWS re:Invent with every AWS Summit, and every side event on lu.ma with
    every other one. Those are the free-vendor and side-event categories, the
    two the six-category split exists to surface, emptied by the dedup step.
    Two rows are the same page when they are the same page.
    """
    host = host_key(url)
    if not host:
        return ""
    try:
        path = (urlparse(url).path or "").lower().rstrip("/")
    except Exception:
        path = ""
    for tail in ("/index.html", "/index.htm", "/index.php", "/home"):
        if path.endswith(tail):
            path = path[:-len(tail)]
    return host + path


def _excluded(name: str, force_exclude: str | None) -> bool:
    """Honour the profile's force-exclude list on our side too.

    The prompt asks the model to skip these, and a model asked to skip
    something occasionally returns it anyway. A second pass in code costs
    nothing and turns a request into a guarantee.
    """
    if not force_exclude:
        return False
    if not name_key(name):
        return False
    return any(names_match(name, line)
               for line in re.split(r"[\n,;]+", force_exclude) if line.strip())


def committed_keys(force_include: str | None) -> set:
    """The events the client has already committed to, as written."""
    return {line.strip() for line in re.split(r"[\n,;]+", str(force_include or ""))
            if line.strip() and name_key(line)}


def is_committed(name: str, keys: set) -> bool:
    """Same whole-name match force-exclude uses, so "Money20/20" written on the
    profile matches "Money20/20 USA 2026" coming back from a search and
    "SaaStr" matches "SaaStr Annual", while "Money20/20 USA" no longer claims
    the client has paid for "Money20/20 Europe"."""
    if not name_key(name):
        return False
    return any(names_match(name, k) for k in (keys or set()))


def merge(by_category: dict, force_exclude: str | None = None,
          force_include: str | None = None) -> list[dict]:
    """Flatten the six category results into one deduped candidate list.

    First find wins, and the order is CATEGORIES order, which deliberately
    puts the industry flagship first: if the same event surfaces as both a
    flagship and a "vertical summit", it is a flagship, and letting it keep
    the narrower label would disguise exactly the bias being guarded against.
    """
    out: list[dict] = []
    kept_names: list[str] = []
    seen_sites: set[str] = set()
    committed = committed_keys(force_include)
    for cat in rubric.CATEGORIES:
        for ev in (by_category.get(cat) or []):
            name = ev.get("name") or ""
            if not name_key(name):
                continue
            if _excluded(name, force_exclude):
                continue
            sk = site_key(ev.get("website") or "")
            if sk and sk in seen_sites:
                continue
            # Compared against the names already kept rather than against a set
            # of keys, because "same event" now depends on two names together
            # (their regions have to agree) and cannot be reduced to one string.
            if any(names_match(name, kept) for kept in kept_names):
                continue
            kept_names.append(name)
            if sk:
                seen_sites.add(sk)
            # Set here, in code, from the user's own profile. A model that
            # returns committed:true for an event nobody committed to must not
            # be able to promote itself past the floor.
            ev = dict(ev)
            ev["committed"] = is_committed(ev.get("name") or "", committed)
            out.append(ev)
    return out


# ── one category ──────────────────────────────────────────────────────────

def _clean_event(raw: dict, category: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    website = str(raw.get("website") or "").strip()
    if website and not website.lower().startswith(("http://", "https://")):
        website = ""

    def _t(k, cap=400):
        v = str(raw.get(k) or "").strip()
        return v[:cap] or None

    try:
        days = int(raw.get("days"))
        days = days if 1 <= days <= 30 else None
    except (TypeError, ValueError):
        days = None

    return {
        "name": name[:250],
        "edition": _t("edition", 80),
        "website": website or None,
        "organizer": _t("organizer", 200),
        "starts_on": raw.get("starts_on") or None,
        "ends_on": raw.get("ends_on") or None,
        "country": _t("country", 100),
        "city": _t("city", 120),
        "days": days,
        "industry": _t("industry", 160),
        "attendees": _t("attendees", 80),
        "booths": _t("booths", 80),
        "audience_note": _t("audience_note", 600),
        "format": _t("format", 16),
        "cost_note": _t("cost_note", 400),
        "organizer_run": bool(raw.get("organizer_run")),
        "matchmaking_evidence": _t("matchmaking_evidence", 800),
        "famous": bool(raw.get("famous")),
        "category": category,
        "category_fit": _t("category_fit", 500),
        "confidence": str(raw.get("confidence") or "medium").strip().lower()[:10],
        "sources": [u for u in (raw.get("sources") or [])
                    if isinstance(u, str)
                    and u.lower().startswith(("http://", "https://"))][:8],
    }


def _today() -> str:
    """Today, as the prompts state it.

    A named seam rather than an inline call, so a test can fix the date
    without reaching into the datetime module for the whole process.
    """
    return datetime.date.today().isoformat()


def _prompt_common(profile: dict) -> dict:
    return {
        "today": _today(),
        "profile": profile_brief(profile),
        "where_buyers": rubric.CLASSIFICATION_WHERE_BUYERS_ARE.get(
            profile.get("classification"), "Confirm with the client."),
    }


def find_system(category: str, profile: dict) -> str:
    """The stage-one system prompt, assembled in one place.

    Tests used to call `_FIND_SYSTEM.format(...)` with a hand-written kwargs
    list, which broke every time a placeholder was added and told us nothing
    about the code that ships. Both callers go through here now, so a new
    placeholder is filled for the tests and for production by the same line.
    """
    return _FIND_SYSTEM.format(
        category_label=rubric.CATEGORY_LABELS[category],
        category_brief=rubric.CATEGORY_BRIEF[category],
        max_uses=FIND_MAX_USES,
        **_prompt_common(profile))


def confirm_system(proposal: dict, category: str, profile: dict) -> str:
    """The stage-two system prompt. See find_system for why this exists."""
    site = (proposal or {}).get("website") or ""
    return _CONFIRM_SYSTEM.format(
        event_name=(proposal or {}).get("name") or "",
        category_label=rubric.CATEGORY_LABELS[category],
        category_brief=rubric.CATEGORY_BRIEF[category],
        why=(proposal or {}).get("why") or "no reason given",
        website_line=("Their link for it: %s\n" % site) if site else "",
        max_uses=CONFIRM_MAX_USES,
        **_prompt_common(profile))


# How much of the finder's own note the report carries, and what it drops.
#
# `note` is free prose from a model. The prompt asks for "what you searched and
# what you could not confirm", and the report prints the answer as the reason a
# category came up short. A live run answered with 600 characters of
# first-person narration, and every one of them was printed under a heading,
# cut off mid-word:
#
#   "i attempted to research emerging (1st-3rd edition) b2b marketing/growth/
#    sales events for position2 [dash] candidates i intended to verify included
#    newer community-driven events such as mops-apalooza ... however, the
#    web_search tool hit a hard per-turn call limit partway through this
#    research session and returned 'server tool use limit exceeded' on every su"
#
# Length and content are two separate faults and a cap only fixes the first.
# No cap turns a sentence about our tooling into a sentence about this client's
# market: a reader told about a "per-turn call limit" has been handed a problem
# they cannot act on, sitting in the one paragraph that is supposed to be about
# their market. Those sentences are dropped outright, and what the search could
# not finish is already said, in this module's own words, by the status and the
# detail that travel beside the note.
#
# Only the note is cleaned this way. A rejection reason is prose from the same
# model, but it is one reason about one event and it has somewhere else to go:
# a rejection this filter emptied would have to become "could not be checked",
# which is a different finding, and making that switch on a keyword match is
# not a trade this is confident enough to make.
NOTE_CHARS = 220

_NOTE_PLUMBING = ("web_search", "search tool", "tool call", "tool use",
                  "per-turn", "per turn", "max_uses", "max_tokens",
                  "stop_reason", "rate limit", "this turn", "server tool",
                  "call limit", "token limit", "tool limit",
                  # Self-narration. The prompt now forbids it, and a model
                  # that ignores that produces the worst sentence in the
                  # report: "I attempted to research emerging events for
                  # position2, candidates i intended to verify included..."
                  # says nothing about the client's market and takes a
                  # paragraph to say it. Dropping a sentence too many is the
                  # safe direction: an emptied note falls back to a sentence
                  # of this module's own, which is always true.
                  "i attempted", "i intended", "i tried", "i searched",
                  "i was unable", "i could not", "i ran out", "i did not",
                  "i have not", "my search", "let me", "i will")

_NOTE_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_NOTE_DASH = re.compile(r"\s*[\u2013\u2014]\s*")


def _reader_note(raw) -> str:
    """The model's note as report copy: no plumbing, capped, punctuated.

    Returns "" when nothing survives, which the callers already handle: an
    absent note is a category that said nothing, and every one of them has a
    sentence of our own to fall back on.
    """
    text = _NOTE_DASH.sub(", ", " ".join(str(raw or "").split()))
    if not text:
        return ""
    kept, used = [], 0
    for piece in _NOTE_SENTENCE.findall(text):
        s = piece.strip()
        if not s:
            continue
        low = s.lower()
        if any(bit in low for bit in _NOTE_PLUMBING):
            continue
        if kept and used + 1 + len(s) > NOTE_CHARS:
            break
        used += (1 if kept else 0) + len(s)
        kept.append(s)
    if not kept:
        return ""
    out = " ".join(kept)
    if len(out) > NOTE_CHARS:
        # One sentence longer than the whole allowance. Cut at a word rather
        # than mid-word: ending a client's report on "on every su" is what
        # started this.
        cut = out[:NOTE_CHARS]
        at = cut.rfind(" ")
        out = (cut[:at] if at > NOTE_CHARS * 0.6 else cut).rstrip(" ,;:.-") + "\u2026"
    elif not out.endswith((".", "!", "?", "\u2026")):
        out += "."
    return out[0].upper() + out[1:]


def _searches_used(res: dict) -> str:
    """How much of the budget the call actually spent, as a sentence.

    Attached to every reply this module cannot use, because the number is the
    difference between two failures that read identically and are not the
    same thing. A call that spent its whole budget and still produced nothing
    usable is a hard category. A call that ran one search out of six and then
    wrote an apology is a tool that stopped answering, and there is no point
    re-reading the prompt over it.

    Recorded because it happened. A live probe at a budget of one search sat
    for 471 seconds, ran ONE search, returned NO error block of any kind, and
    answered "I've hit a hard limit on web search tool calls for this turn
    and it isn't resetting despite waiting." Nothing in the reply structure
    said so. The only hard evidence was the search count.

    It stays a statement of fact rather than a diagnosis: reading intent out
    of the model's prose is exactly what this module refuses to do elsewhere.
    """
    ran = res.get("search_count")
    if not isinstance(ran, int):
        return ""
    return ("It ran %d of the %d searches it was allowed."
            % (ran, FIND_MAX_USES))


def _clean_proposal(raw: dict) -> dict | None:
    """A candidate name, trimmed. Rejects anything with no usable name.

    The website is kept when it is a real http(s) URL and dropped otherwise,
    exactly as `_clean_event` does, because a proposal carrying
    `javascript:alert(1)` would hand that string straight to the confirmer's
    prompt.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name or not name_key(name):
        return None
    site = str(raw.get("website") or "").strip()
    if not site.lower().startswith(("http://", "https://")):
        site = ""
    return {"name": name[:250], "website": site or None,
            "why": str(raw.get("why") or "").strip()[:300]}


def _dedupe_proposals(proposals: list) -> list:
    """Drop a candidate this category has already named.

    A finder asked for four candidates sometimes returns the same event twice
    under two brandings. Confirming both costs a whole extra call and then
    `merge` throws one away, so it is cheaper and clearer to notice here. The
    test is the one `merge` uses, so the two stages agree about what counts as
    the same event.
    """
    out, seen_sites = [], set()
    for pr in proposals:
        if any(names_match(pr["name"], k["name"]) for k in out):
            continue
        sk = site_key(pr.get("website") or "")
        if sk and sk in seen_sites:
            continue
        if sk:
            seen_sites.add(sk)
        out.append(pr)
    return out


def propose_category(category: str, profile: dict) -> dict:
    """Stage one: name the candidates in one category. Never raises.

    Returns {"category", "status", "proposals", "note", "detail",
    "budget_spent"}. `status` uses the module's ok / empty / partial / error
    vocabulary and describes the SEARCH, never the market.

    `budget_spent` says the finder reached for one more search than
    FIND_MAX_USES allowed. It is deliberately NOT a status: a category that
    named four candidates with its whole budget did a complete piece of work,
    and downgrading it would put "this category did not run" in a report
    about a search that ran perfectly. It travels alongside the status
    instead, and is spent only where it changes what a reader should
    conclude, which is on a category that came up short.
    """
    system = find_system(category, profile)
    user = ("Name up to %d candidate events in the \"%s\" category for this "
            "client. Search actively, and spend your searches on finding "
            "events rather than on studying the ones you have found. If this "
            "category has nothing for them, return an empty array and say why."
            % (PER_CATEGORY, rubric.CATEGORY_LABELS[category]))

    res = _ask(system, user, max_uses=FIND_MAX_USES, max_tokens=FIND_MAX_TOKENS)
    budget = bool(res.get("budget_spent"))

    def _out(status, proposals, note, detail):
        return {"category": category, "status": status, "proposals": proposals,
                "note": note, "detail": detail, "budget_spent": budget}

    if res.get("error"):
        err = res["error"]
        # The kind is a machine token and belongs in the log, not in a
        # sentence a client reads. Rendered under a category label it
        # produced "Side event: Transport: peer closed connection", a double
        # colon around a word that means nothing to the reader.
        # Both halves of the failure go somewhere, and to different readers.
        # The kind and the full detail go to the log, where the stop_reason
        # and the tool's own error code are the whole point. The report gets
        # a clause written for a person: `detail` ends with advice like
        # "Raise max_tokens or lower max_uses", and a live client report
        # printed that sentence under a category heading.
        logger.warning("event_intel_discover: find failed for category %s "
                       "(%s: %s)", category, err["kind"], err["detail"])
        return _out(STATUS_ERROR, [], "",
                    "The search for this category could not be completed: %s."
                    % claude_websearch.reader_reason(err))

    # The same refusal event_intel_recover applies to a recovered roster, for
    # the same reason and with more at stake: a reply that ran no search is a
    # recollection, and here the thing being recalled is whole conferences
    # rather than rows on a page somebody can check.
    if not res.get("search_count"):
        return _out(STATUS_ERROR, [], "",
                    "The model answered this category without running a "
                    "single search, so its events are recalled rather "
                    "than confirmed and were discarded.")

    parsed = claude_websearch.extract_json(res.get("text") or "",
                                           require="candidates")
    if not isinstance(parsed, dict):
        logger.warning("event_intel_discover: unparsable find reply for category "
                       "%s (blocks=%s, stop=%s, searches=%s of %s)", category,
                       res.get("text_block_count"), res.get("stop_reason"),
                       res.get("search_count"), FIND_MAX_USES)
        return _out(STATUS_ERROR, [], "",
                    "The search ran but its answer could not be read. %s"
                    % _searches_used(res))

    proposals = []
    for c in (parsed.get("candidates") or []):
        clean = _clean_proposal(c)
        if clean:
            proposals.append(clean)
    proposals = _dedupe_proposals(proposals)[:PER_CATEGORY]
    note = _reader_note(parsed.get("note"))

    # A search that was cut off is not a category that is empty. The model is
    # asked to declare this outright, because the alternative signals are all
    # unreliable: the tool does not always emit an error block, and reading the
    # failure out of the prose note means pattern-matching English. A run that
    # got starved once reported "Emerging event: empty", which the report then
    # renders to a paying client as "there is nothing in this category for
    # you", when the truth was that the search never finished.
    complete = parsed.get("search_complete")
    if complete is False:
        # Two different sentences, because two different things happened. A
        # finder that ran out of the searches we gave it stopped where we told
        # it to stop, and saying "the search was cut off" about our own budget
        # invites somebody to go hunting for a fault that is not there.
        detail = (("The finder used all %d of the searches it was given in "
                   "this category and would have kept looking, so treat this "
                   "as the first %d searches' worth rather than the whole of "
                   "what is out there." % (FIND_MAX_USES, FIND_MAX_USES))
                  if budget else
                  ("The model reported that it could not finish searching this "
                   "category, so an empty result here is a gap in the search "
                   "rather than a fact about the market."))
        if proposals:
            return _out(STATUS_PARTIAL, proposals, note, detail)
        return _out(STATUS_ERROR, [], note, detail)

    if not proposals and complete is None:
        # Nothing found and no declaration either way. Report the thing that
        # could not be measured instead of picking the flattering reading.
        return _out(STATUS_EMPTY, [], note,
                    "This category returned no events and did not say "
                    "whether its search finished, so it cannot be told "
                    "apart from a search that was cut off.")

    return _out(STATUS_OK if proposals else STATUS_EMPTY, proposals, note, "")


def _unchecked(name: str, reason: str) -> dict:
    return {"kind": CONFIRM_UNCHECKED, "event": None, "name": name,
            "reason": reason, "facts_complete": False}


def confirm_event(proposal: dict, category: str, profile: dict) -> dict:
    """Stage two: check one candidate with a search that did not propose it.

    Never raises. Returns {"kind", "event", "name", "reason",
    "facts_complete"} where kind is one of CONFIRM_OK, CONFIRM_REJECTED or
    CONFIRM_UNCHECKED.

    The three-way split is the point of the whole stage. An event this call
    SEARCHED and ruled out is a fact about the client's market and belongs in
    the report as one. An event it could not check is a hole. Two outcomes
    would force one of those to wear the other's clothes.
    """
    name = (proposal or {}).get("name") or ""
    if not name_key(name):
        return _unchecked(name, "The candidate had no usable name.")

    system = confirm_system(proposal, category, profile)
    user = ("Confirm \"%s\" and report what it publishes about itself. If it "
            "is not real, not upcoming, or you cannot tell, say so instead."
            % name)

    res = _ask(system, user, max_uses=CONFIRM_MAX_USES,
               max_tokens=CONFIRM_MAX_TOKENS)
    if res.get("error"):
        err = res["error"]
        # Named on the "could not be checked" list, next to the event, in the
        # report. The kind is a machine token and the detail is written for
        # this file's author, so the pair rendered as
        # "max_tokens: Ran out of output budget before finishing
        # (stop_reason=max_tokens). Raise max_tokens or lower max_uses."
        # beside an event name a reader was trying to make a decision about.
        logger.warning("event_intel_discover: confirm failed for %r (%s: %s)",
                       name[:80], err["kind"], err["detail"])
        return _unchecked(name, "The check could not be completed: %s."
                          % claude_websearch.reader_reason(err))

    if not res.get("search_count"):
        return _unchecked(name, "The model answered without running a single "
                                "search, so it recalled this event rather than "
                                "confirming it.")

    parsed = claude_websearch.extract_json(res.get("text") or "",
                                           require="confirmed")
    if not isinstance(parsed, dict):
        logger.warning("event_intel_discover: unparsable confirm reply for %r "
                       "(blocks=%s, stop=%s)", name[:80],
                       res.get("text_block_count"), res.get("stop_reason"))
        return _unchecked(name, "The check ran but its answer could not be read.")

    facts_complete = parsed.get("facts_complete") is not False

    if not parsed.get("confirmed"):
        reason = str(parsed.get("reject_reason") or "").strip()[:400]
        if not reason:
            # Refused without saying why. That is not a finding about the
            # market, so it must not be recorded as one.
            return _unchecked(name, "The check refused this event without "
                                    "saying what it found.")
        return {"kind": CONFIRM_REJECTED, "event": None, "name": name,
                "reason": reason, "facts_complete": facts_complete}

    event = _clean_event(parsed.get("event") or {}, category)
    if event is None:
        return _unchecked(name, "The check confirmed this event but returned "
                                "nothing usable to describe it.")
    if not event["sources"]:
        # Confirmation that cites nothing is assertion, not confirmation. The
        # prompt asks for a URL; asking is not the same as getting, and this
        # stage exists precisely so that "confirmed" means a second search
        # actually saw the thing.
        return _unchecked(event["name"], "The check confirmed this event "
                                         "without citing a single page, so "
                                         "nothing here can be checked.")

    event["facts_complete"] = facts_complete
    event["proposed_as"] = (proposal or {}).get("why") or None
    return {"kind": CONFIRM_OK, "event": event, "name": event["name"],
            "reason": "", "facts_complete": facts_complete}


def search_category(category: str, profile: dict) -> dict:
    """One category: name candidates, then confirm each one separately.

    Never raises. Returns {"category", "status", "events", "note", "detail",
    "proposed", "rejected"}. `status` separates "ran and found nothing" from
    "did not run", because those two are indistinguishable in the output
    otherwise and mean opposite things.

    `rejected` carries the candidates a confirmation searched and ruled out,
    with the reason. Those are not noise: three plausible names that all turn
    out to have no upcoming edition is a real and reportable fact about the
    client's year, and it used to be invisible because the same call that
    proposed an event also decided whether to keep it.
    """
    found = propose_category(category, profile)
    proposals = found["proposals"]
    base = {"category": category, "note": found["note"],
            "proposed": len(proposals), "rejected": [],
            "budget_spent": found.get("budget_spent", False)}

    if not proposals:
        return dict(base, status=found["status"], events=[],
                    detail=found["detail"])

    results = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(proposals))) as pool:
        futures = [pool.submit(confirm_event, pr, category, profile)
                   for pr in proposals]
        for pr, fut in zip(proposals, futures):
            try:
                results.append(fut.result())
            except Exception as e:
                # One candidate crashing must never cost the others.
                logger.exception("event_intel_discover: confirming %r crashed",
                                 (pr.get("name") or "")[:80])
                results.append(_unchecked(pr.get("name") or "",
                                          "Unexpected failure: %s" % str(e)[:200]))

    events = [r["event"] for r in results if r["kind"] == CONFIRM_OK]
    rejected = [{"name": r["name"], "reason": r["reason"]}
                for r in results if r["kind"] == CONFIRM_REJECTED]
    unchecked = [r for r in results if r["kind"] == CONFIRM_UNCHECKED]
    base["rejected"] = rejected

    bits = []
    if unchecked:
        bits.append("%d of the %d candidates found here could not be checked "
                    "at all (%s)."
                    % (len(unchecked), len(proposals),
                       "; ".join(sorted({u["reason"] for u in unchecked}))[:400]))
    incomplete = [e for e in events if not e.get("facts_complete")]
    if incomplete:
        bits.append("%d confirmed event%s had published numbers we could not "
                    "finish reading." % (len(incomplete),
                                         "" if len(incomplete) == 1 else "s"))

    if events:
        # Confirmed events stand on their own. The status only says whether
        # anything ELSE about this category fell short, so a shortfall never
        # casts doubt on an event a search actually confirmed.
        partial = bool(bits) or found["status"] == STATUS_PARTIAL
        detail = " ".join(([found["detail"]] if found["status"] == STATUS_PARTIAL
                           else []) + bits)
        return dict(base, status=STATUS_PARTIAL if partial else STATUS_OK,
                    events=events, detail=detail)

    # Nothing survived. Which of the two absences this is depends entirely on
    # WHY, and the whole module is built around not guessing.
    if unchecked or found["status"] == STATUS_PARTIAL:
        detail = " ".join(([found["detail"]] if found["status"] == STATUS_PARTIAL
                           else []) + bits)
        return dict(base, status=STATUS_ERROR, events=[], detail=detail)

    # Every candidate was searched and ruled out. That is a finished piece of
    # work and a real finding: this category has nothing UPCOMING here.
    return dict(base, status=STATUS_EMPTY, events=[],
                detail=("%d candidate%s in this category were checked and none "
                        "qualified. %s"
                        % (len(rejected), "" if len(rejected) == 1 else "s",
                           " ".join("%s: %s" % (r["name"], r["reason"])
                                    for r in rejected)))[:900])


def discover(profile: dict) -> dict:
    """All six categories, concurrently, then merged and deduped.

    Returns everything the report needs to explain its own coverage:

        candidates  the merged, deduped list (facts, unscored)
        by_category what each category actually returned
        statuses    per category: ok / empty / error, with the reason
        shortfall   categories under the quota of two, with WHY, separating
                    a genuinely empty market from a search that failed
    """
    by_category: dict[str, list[dict]] = {}
    statuses: dict[str, dict] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        futures = {pool.submit(search_category, cat, profile): cat
                   for cat in rubric.CATEGORIES}
        for fut in concurrent.futures.as_completed(futures):
            cat = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                # One category crashing must never cost the other five.
                logger.exception("event_intel_discover: category %s crashed", cat)
                r = {"category": cat, "status": STATUS_ERROR, "events": [],
                     "note": "", "proposed": 0, "rejected": [],
                     "budget_spent": False,
                     "detail": "Unexpected failure: %s" % str(e)[:200]}
            by_category[cat] = r["events"]
            statuses[cat] = {"status": r["status"], "note": r["note"],
                             "detail": r["detail"],
                             "label": rubric.CATEGORY_LABELS[cat],
                             "found": len(r["events"]),
                             # Whether the finder ran out of the searches it
                             # was given. Not a status: see propose_category.
                             "budget_spent": r.get("budget_spent", False),
                             # What the finder named, and what the separate
                             # confirmation ruled out. Reported so the page can
                             # say "we looked at five and kept two" instead of
                             # showing two events and letting the reader assume
                             # two were all there ever was.
                             "proposed": r.get("proposed", len(r["events"])),
                             "rejected": r.get("rejected") or []}

    candidates = merge(by_category, (profile or {}).get("force_exclude"),
                       (profile or {}).get("force_include"))

    # Coverage is reported from what SURVIVED dedup and the exclude list, not
    # from what each search returned. Measured before, the report could tell a
    # client that the free-vendor category was covered in a run where every one
    # of its events had been merged away and it contributed nothing.
    surviving: dict[str, list] = {c: [] for c in rubric.CATEGORIES}
    for ev in candidates:
        surviving.setdefault(ev.get("category"), []).append(ev)
    for cat, st in statuses.items():
        st["kept"] = len(surviving.get(cat) or [])
        st["merged_away"] = max(0, st["found"] - st["kept"])

    shortfall = []
    for s in rubric.category_shortfall(surviving):
        st = statuses.get(s["category"]) or {}
        s["status"] = st.get("status", STATUS_ERROR)
        # The distinction the whole module is built around. A partial search
        # reads from `detail` for the same reason an errored one does: the
        # reason it fell short is a fact about the SEARCH, and the model's own
        # note is a description of the market. Printing the note for a search
        # that was cut off is how "we could not finish looking" gets rendered
        # as "there is nothing here for you".
        if s["status"] in (STATUS_ERROR, STATUS_PARTIAL):
            s["why"] = st.get("detail") or "The search for this category did not run."
        else:
            # The fallback has to match what was actually found. A category
            # that found one event and wanted two is short, not empty, and
            # "this category returned nothing for this client" printed under a
            # bar reading 1 of 2 contradicts the bar it is explaining. It was
            # unreachable while every category came back with a note, and
            # _reader_note made it reachable by design: a note that only
            # narrated the search is now dropped, and this sentence is what
            # takes its place.
            found = int(s.get("found") or 0)
            s["why"] = st.get("note") or (
                "This category returned nothing for this client."
                if not found else
                "The search finished and found only %s here for this client."
                % ("one event" if found == 1 else "%d events" % found))
        if s["status"] not in (STATUS_ERROR, STATUS_PARTIAL) and st.get("merged_away"):
            s["why"] = ("%s %d of the %d events found here were the same events "
                        "already listed under another category."
                        % (s["why"], st["merged_away"], st["found"]))
        # A category that came up short after using every search it was given
        # is a different thing from one that came up short with searches to
        # spare, and only the first is worth spending more on. Said only on
        # the categories that fell short, and only when the reason above did
        # not already say it.
        s["budget_spent"] = bool(st.get("budget_spent"))
        if s["budget_spent"] and "searches it was given" not in s["why"]:
            s["why"] = ("%s It also used every one of the %d searches allowed "
                        "for finding events here, so there may be more to "
                        "find than this search could reach."
                        % (s["why"].rstrip() or "", FIND_MAX_USES)).strip()
        shortfall.append(s)

    ran = sum(1 for s in statuses.values() if s["status"] != STATUS_ERROR)
    return {
        "candidates": candidates,
        "by_category": by_category,
        "statuses": statuses,
        "shortfall": shortfall,
        "categories_searched": ran,
        "categories_failed": len(rubric.CATEGORIES) - ran,
        "found": len(candidates),
    }
