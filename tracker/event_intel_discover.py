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

So this runs SIX SEPARATE SEARCHES, one per category, each of which can only
return events of that one kind and has no way to satisfy itself from another.
A category that genuinely has nothing has to come back empty and say why.

Three statuses, never collapsed into each other:

    ok      the search ran and found events
    empty   the search ran and this category genuinely has nothing here
    error   the search did not run

`empty` and `error` are the pair that matters. "No free vendor conference
serves this niche vertical" is a real finding about the client's market.
"The free vendor conference search timed out" is a hole in the analysis. They
render identically as an absence, so the difference is recorded explicitly.

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
from urllib.parse import urlparse

from . import claude_websearch
from . import event_intel_rubric as rubric

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"

# Three concurrent searches. Six at once reliably trips rate limiting, and one
# at a time turns a recommend run into six sequential multi-search lookups.
MAX_CONCURRENCY = 3

# Asked-for per category. One above the quota of two, so that a single
# unusable result does not put the category under quota on its own.
PER_CATEGORY = 4

_SYSTEM = """You find real business events that a specific company should \
consider attending, exhibiting at or sponsoring. You are searching ONE \
category of event at a time and you must not return events from other \
categories.

THE CATEGORY YOU ARE SEARCHING NOW: {category_label}
What that means: {category_brief}

THE CLIENT
{profile}

WHERE THIS CLIENT'S BUYERS PHYSICALLY ARE AT AN EVENT: {where_buyers}
That is not a detail. It decides which side of the floor matters, so when you \
describe an event, describe the side of it this client would actually work.

TODAY IS {today}. Every judgement about whether an event is upcoming, and \
every reading of the client's time window, is measured from that date and not \
from your own sense of when now is.

RULES.
1. Use web search. Do not answer from memory. Every event you return must be \
one you confirmed exists by visiting a real page during this search, and every \
URL you return must be one you actually opened. A plausible-sounding \
conference name that does not exist costs somebody a travel budget. Return at \
least one URL in `sources` for every event; an event you cannot cite is an \
event you did not confirm, so leave it out.
2. Return ONLY events that genuinely belong to the category above. If this \
category has nothing for this client, return an empty array and explain \
concretely in `note` what you searched for and why nothing fits. An honest \
empty category is a finding. A flagship relabelled to fill a quota is not.
3. Return the next edition that STARTS ON OR AFTER {today}. An edition that \
has already finished cannot be attended and must not be returned as a \
recommendation. Where only a past edition exists, say so in `note` and leave \
the event out; where the next edition is announced but undated, return it with \
null dates rather than guessing one.
4. `attendees` and `booths` are the event's OWN published claims, quoted as \
they state them ("12,000+ attendees", "430 exhibitors"), or null. NEVER \
estimate either. A number you invented is indistinguishable from one they \
published.
5. `matchmaking_evidence` must quote or closely paraphrase what the ORGANISER \
says they do. If the only thing on offer is a conference app where attendees \
book their own meetings (Whova, Brella, Swapcard and the like), say exactly \
that: it is a real and useful answer. Set `organizer_run` true only when the \
organiser takes active responsibility for pairing people against stated \
criteria.
6. `famous` is honest self-assessment: true if you could have named this event \
without searching. Being famous is not disqualifying, it just gets audited.
7. `cost_note` is any published cost to attend, exhibit or sponsor, quoted as \
published, or null. It is recorded as context for the client's decision and \
is never used to score anything, so do not soften or inflate it.

Respond with ONLY a JSON object, no prose before or after:
{{"events": [{{"name": str, "edition": str|null, "website": str|null, \
"organizer": str|null, "starts_on": "YYYY-MM-DD"|null, \
"ends_on": "YYYY-MM-DD"|null, "country": str|null, "city": str|null, \
"days": int|null, "industry": str|null, "attendees": str|null, \
"booths": str|null, "audience_note": str|null, "format": \
"in_person"|"virtual"|"hybrid"|null, "cost_note": str|null, \
"organizer_run": true|false, "matchmaking_evidence": str|null, \
"famous": true|false, "category_fit": str, "confidence": \
"high"|"medium"|"low", "sources": [str]}}], "note": str}}

`category_fit` is one sentence saying why this event belongs to the \
"{category_label}" category specifically. `note` says what you searched and \
what you could not confirm."""


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


def search_category(category: str, profile: dict) -> dict:
    """One category, one search. Never raises.

    Returns {"category", "status", "events", "note", "detail"}. `status`
    separates "ran and found nothing" from "did not run", because those two
    are indistinguishable in the output otherwise and mean opposite things.
    """
    system = _SYSTEM.format(
        category_label=rubric.CATEGORY_LABELS[category],
        category_brief=rubric.CATEGORY_BRIEF[category],
        today=datetime.date.today().isoformat(),
        profile=profile_brief(profile),
        where_buyers=rubric.CLASSIFICATION_WHERE_BUYERS_ARE.get(
            profile.get("classification"), "Confirm with the client."))
    user = ("Find up to %d events in the \"%s\" category for this client. "
            "Search actively. If this category has nothing for them, return an "
            "empty array and say why."
            % (PER_CATEGORY, rubric.CATEGORY_LABELS[category]))

    res = claude_websearch.ask(system, user, max_uses=8, max_tokens=8000)
    if res.get("error"):
        err = res["error"]
        return {"category": category, "status": STATUS_ERROR, "events": [],
                "note": "", "detail": "%s: %s" % (err["kind"], err["detail"])}

    # The same refusal event_intel_recover applies to a recovered roster, for
    # the same reason and with more at stake: a reply that ran no search is a
    # recollection, and here the thing being recalled is whole conferences
    # rather than rows on a page somebody can check.
    if not res.get("search_count"):
        return {"category": category, "status": STATUS_ERROR, "events": [],
                "note": "",
                "detail": ("The model answered this category without running a "
                           "single search, so its events are recalled rather "
                           "than confirmed and were discarded.")}

    parsed = claude_websearch.extract_json(res.get("text") or "", require="events")
    if not isinstance(parsed, dict):
        logger.warning("event_intel_discover: unparsable reply for category %s "
                       "(blocks=%s, stop=%s)", category,
                       res.get("text_block_count"), res.get("stop_reason"))
        return {"category": category, "status": STATUS_ERROR, "events": [],
                "note": "",
                "detail": "The search ran but its answer could not be read."}

    events = []
    for e in (parsed.get("events") or [])[:PER_CATEGORY]:
        clean = _clean_event(e, category)
        if clean:
            events.append(clean)
    note = str(parsed.get("note") or "")[:600]
    return {"category": category,
            "status": STATUS_OK if events else STATUS_EMPTY,
            "events": events, "note": note, "detail": ""}


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
                     "note": "", "detail": "Unexpected failure: %s" % str(e)[:200]}
            by_category[cat] = r["events"]
            statuses[cat] = {"status": r["status"], "note": r["note"],
                             "detail": r["detail"],
                             "label": rubric.CATEGORY_LABELS[cat],
                             "found": len(r["events"])}

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
        # The distinction the whole module is built around.
        s["why"] = (st.get("detail") or "The search for this category did not run.") \
            if s["status"] == STATUS_ERROR \
            else (st.get("note") or "This category returned nothing for this client.")
        if s["status"] != STATUS_ERROR and st.get("merged_away"):
            s["why"] = ("%s %d of the %d events found here were the same events "
                        "already listed under another category."
                        % (s["why"], st["merged_away"], st["found"]))
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
