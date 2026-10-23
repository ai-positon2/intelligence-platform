"""Paid Marketing Agents: Meta's interests and behaviors, the Meta side of keyword research.

Meta ads are not bought on keywords. What Meta lets an advertiser aim at is a list of interests and behaviors Meta keeps itself (each with an id and the size of the audience it
holds), plus the advertiser's own audiences and lookalikes. So the question Keyword Research asks of Google ("what do people search for that we do not bid on?") becomes, for Meta,
"which interests fit this business that no ad set is aimed at yet, and how big is each?". Three of Meta's own reads answer it:

    search       /search?type=adinterest&q=...                  interests whose name matches a phrase
    suggestions  /search?type=adinterestsuggestion&interest_list=...   interests that people in the given ones also have
    behaviors    /{act}/targetingbrowse?limit_type=behaviors  Meta's list of behaviors (no phrase: the list is read once and filtered here)

This module builds those requests, reads what comes back, says which of the interests an account's ad sets already name (and what they cost and brought), and checks that an
interest somebody wrote down is one Meta has (an interest that is not in Meta's list cannot be targeted). Read only: nothing here changes an account. The network is a function
handed in (`get_fn(path, params)` returns the Graph answer's rows), so tests run on fakes.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

SEARCH_LIMIT = 25
SUGGEST_LIMIT = 25
MAX_QUERIES = 24
MAX_SEEDS_FOR_SUGGESTIONS = 8
WORKERS = 4
SMALL_AUDIENCE = 50_000          # below this Meta cannot deliver an ad set reliably (and its estimates are rounded hard)
LOCALE = "en_US"


def search_request(q, limit=SEARCH_LIMIT):
    """(path, params) of the interest search for one phrase."""
    return "/search", {"type": "adinterest", "q": str(q), "limit": limit, "locale": LOCALE}


def suggest_request(names, limit=SUGGEST_LIMIT):
    """(path, params) of the interests that go with the given interest names."""
    return "/search", {"type": "adinterestsuggestion", "interest_list": json.dumps([str(n) for n in names][:MAX_SEEDS_FOR_SUGGESTIONS]), "limit": limit, "locale": LOCALE}


def behaviors_request(act):
    """(path, params) of Meta's whole list of behaviors. Meta's search no longer answers `class=behaviors` (a 400 on a real account, 2026-10-23); the account's targeting browse does."""
    return "/%s/targetingbrowse" % act, {"limit_type": "behaviors"}


def _int(v):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None                    # Meta says -1 when it will not say


def size_text(low, high):
    """'1.2M to 1.5M', '800K to 1.0M' or '' when Meta gave no size."""
    def short(n):
        if n >= 1_000_000:
            return "%.1fM" % (n / 1_000_000)
        if n >= 1_000:
            return "%dK" % round(n / 1_000)
        return str(n)
    if low is None and high is None:
        return ""
    if low is None or high is None or low == high:
        return short(high if high is not None else low)
    return "%s to %s" % (short(low), short(high))


def parse_options(rows, kind="interest"):
    """[{id, name, low, high, size, sizeText, path, topic, kind}] from Meta's interest or behavior rows. A row with no id or name is dropped. `size` is the middle of the range."""
    out, seen = [], set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        oid, name = str(r.get("id") or "").strip(), " ".join(str(r.get("name") or "").split())
        if not oid or not name or oid in seen:
            continue
        seen.add(oid)
        low = _int(r.get("audience_size_lower_bound", r.get("audience_size")))
        high = _int(r.get("audience_size_upper_bound", r.get("audience_size")))
        size = int((low + high) / 2) if low is not None and high is not None else (low if low is not None else high)
        path = [str(p) for p in r.get("path") or [] if p] if isinstance(r.get("path"), list) else []
        out.append({"id": oid, "name": name, "low": low, "high": high, "size": size, "sizeText": size_text(low, high), "path": path,
                    "topic": str(r.get("topic") or ""), "kind": kind})
    return out


def _soft(fn, notes, what):
    """Run one read; a failure is said, never raised (one phrase Meta does not answer must not lose the rest)."""
    try:
        return fn()
    except Exception as e:                                          # noqa: BLE001
        notes.append("%s could not be read (%s)." % (what, str(e)[:120]))
        return None


def find(phrases, get_fn, *, workers=WORKERS):
    """({phrase: [options]}, notes): Meta's interests for each phrase, a few at a time. At most MAX_QUERIES phrases."""
    phrases = [p for p in dict.fromkeys(" ".join(str(x or "").split()) for x in phrases) if p][:MAX_QUERIES]
    notes = []

    def one(p):
        local = []
        rows = _soft(lambda: get_fn(*search_request(p)), local, "The search for \"%s\"" % p)
        return p, parse_options(rows), local
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = list(ex.map(one, phrases))
    found = {}
    for p, opts, local in results:
        found[p] = opts
        notes += local
    return found, notes


def suggestions(names, get_fn):
    """([options], notes): the interests that go with the given interest names (Meta takes at most a few at a time)."""
    notes = []
    names = [n for n in dict.fromkeys(names) if n][:MAX_SEEDS_FOR_SUGGESTIONS]
    if not names:
        return [], notes
    rows = _soft(lambda: get_fn(*suggest_request(names)), notes, "Meta's suggestions")
    return parse_options(rows), notes


def behaviors(get_fn, act):
    """([options], notes): Meta's behaviors, read through an ad account (Meta lists them per account). With no account nothing is read and the note says so."""
    notes = []
    if not act:
        return [], ["Meta's behaviors are read through an ad account and none was given, so no behavior is offered."]
    rows = _soft(lambda: get_fn(*behaviors_request(act)), notes, "Meta's behaviors")
    return parse_options(rows, kind="behavior"), notes


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def validate(names, get_fn, *, workers=WORKERS):
    """({name: option or None}, notes): for each interest somebody wrote down, the Meta interest of exactly that name (ignoring case and punctuation), or None when Meta has
    no such interest. A phrase Meta did not answer is None too, and is said in the notes: it is unverified, not wrong."""
    names = [n for n in dict.fromkeys(" ".join(str(x or "").split()) for x in names) if n]
    found, notes = find(names, get_fn, workers=workers)
    out = {}
    for n in names:
        hit = next((o for o in found.get(n, []) if _norm(o["name"]) == _norm(n)), None)
        out[n] = hit
    return out, notes


# ── What the account's ad sets already aim at ───────────────────────────────

def in_use(adsets, stats):
    """{id: {name, kind, adsets, cost, conv, cpa}}: for every interest and behavior some ad set includes, how many ad sets name it and what those ad sets spent and brought.
    `adsets` is paid_meta_audiences.parse_adsets, `stats` paid_meta_audiences.parse_insights. An ad set that names three interests counts under each of them (the page says so)."""
    out = {}
    for a in adsets or []:
        s = ((stats or {}).get(a["id"]) or {}).get("m") or {}
        for typ, oid, name in a.get("include") or []:
            if typ not in ("interest", "behavior") or not oid:
                continue
            o = out.setdefault(oid, {"name": name, "kind": typ, "adsets": 0, "cost": 0.0, "conv": 0.0})
            o["adsets"] += 1
            o["cost"] += float(s.get("cost") or 0)
            o["conv"] += float(s.get("conv") or 0)
    for o in out.values():
        o["cost"], o["conv"] = round(o["cost"], 2), round(o["conv"], 1)
        o["cpa"] = round(o["cost"] / o["conv"], 2) if o["conv"] else None
    return out


def candidates(found, suggested, behaviors_list, used, *, avoid=(), phrases_for_behaviors=()):
    """The interests and behaviors worth judging, one entry each: {id, name, size, sizeText, small, path, topic, kind, from, inUse}.
    `found` is find()'s answer, `suggested` and `behaviors_list` are option lists. A behavior is kept only when one of `phrases_for_behaviors` appears in its name or path (Meta's
    list holds hundreds that have nothing to do with a business). `avoid` are words that leave an interest out. Interests the account already names carry `inUse`."""
    avoid = [_norm(a) for a in avoid if _norm(a)]
    words = [_norm(p) for p in phrases_for_behaviors if _norm(p)]
    pool = []
    for phrase, opts in (found or {}).items():
        pool += [dict(o, **{"from": "search: " + phrase}) for o in opts]
    pool += [dict(o, **{"from": "suggested by Meta"}) for o in suggested or []]
    for o in behaviors_list or []:
        hay = _norm(o["name"] + " " + " ".join(o.get("path") or []))
        if any(w and w in hay for w in words):
            pool.append(dict(o, **{"from": "Meta behavior"}))
    out, seen = [], set()
    for o in pool:
        if o["id"] in seen:
            continue
        seen.add(o["id"])
        hay = _norm(o["name"])
        if any(a in hay for a in avoid):
            continue
        used_by = (used or {}).get(o["id"])
        out.append({"id": o["id"], "name": o["name"], "size": o.get("size"), "sizeText": o.get("sizeText") or "", "small": o.get("size") is not None and o["size"] < SMALL_AUDIENCE,
                    "path": o.get("path") or [], "topic": o.get("topic") or "", "kind": o.get("kind") or "interest", "from": o["from"], "inUse": used_by})
    return out


def adsets_request(act):
    """(path, params) of the account's ad sets with their targeting (the same read the Audience Segments agent makes)."""
    from . import paid_meta_audiences as ma
    return "/%s/adsets" % act, {"fields": ma.ADSET_FIELDS, "limit": 200, "effective_status": json.dumps(["ACTIVE", "PAUSED"])}
