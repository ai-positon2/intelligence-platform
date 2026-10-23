"""Paid Marketing Agents: Keyword Research for a Meta ad account, what is read and what is worked out in code.

Meta ads are not bought on keywords, so the research asks the question Meta can answer: which interests and behaviors fit this business, how big is each, and which of them do
the account's ad sets already aim at? (tracker/paid_meta_interests.py has Meta's own reads of the interest list.) Two pieces of research feed the judging:

  metaaccount   the account's ad sets with their targeting and 90 days of what each spent and brought, so the interests already in use are known and so is how each does
  interests     the interests (and behaviors) Meta has for the phrases the first writing stage chose, with the size of each audience, marked with the ones already in use

and this file does the arithmetic the page shows: how each interest in use is doing (against the account's own cost per result), which interests the judging called a fit that no
ad set aims at, and which interests in use the judging called not a fit. The model is never asked for a number. No database; the network is a function handed in.
"""
from __future__ import annotations

import json
import time

from . import paid_meta as meta
from . import paid_meta_audiences as ma
from . import paid_meta_interests as mi
from . import paid_strategy_research as rs
from .paid_strategy_research import failed, piece

MAX_CANDIDATES = 100
MIN_RESULTS_TO_JUDGE = 3          # fewer results than this and a cost per result is not a measure
WEAK_SPEND_X = 2.0                # an interest that spent this many times the account's cost per result and brought nothing is weak
MIN_WEAK_SPEND = 50.0             # ... and at least this much (in the account's currency): below it there is nothing to say
GOOD_CPA_X = 0.8                  # a cost per result this far under the account's, with enough results, is working
BAD_CPA_X = 1.5                   # and this far over is weak


def _window(today=None, days=90):
    return rs.window(today, days)


def _named(a, typ):
    return [{"id": i, "name": n} for t, i, n in a.get("include") or [] if t == typ]


def _adset_row(a, stats):
    s = (stats.get(a["id"]) or {})
    m = s.get("m") or {}
    cost, conv = float(m.get("cost") or 0), float(m.get("conv") or 0)
    return {"id": a["id"], "name": a["name"], "campaign": s.get("campaign") or "", "status": a.get("status") or "",
            "interests": _named(a, "interest"), "behaviors": _named(a, "behavior"), "audiences": [n for t, _i, n in a.get("include") or [] if t == "audience"],
            "excluded": [n for _t, _i, n in a.get("exclude") or []], "advantage": bool(a.get("suggestion")),
            "cost": round(cost, 2), "conv": round(conv, 1), "cpa": round(cost / conv, 2) if conv else None}


def use_verdict(row, avg_cpa):
    """('works' | 'weak' | 'watch' | 'idle', a sentence with the numbers) for one interest in use, judged against the account's own cost per result."""
    cost, conv, cpa = row["cost"], row["conv"], row.get("cpa")
    if cost <= 0:
        return "idle", "No spend in the last 90 days."
    if conv == 0:
        floor = max(MIN_WEAK_SPEND, WEAK_SPEND_X * avg_cpa) if avg_cpa else MIN_WEAK_SPEND
        if cost >= floor:
            return "weak", "Spent %s and brought no results%s." % ("{:,.0f}".format(cost), (", more than %d times the account's usual cost of a result (%s)" % (WEAK_SPEND_X, "{:,.2f}".format(avg_cpa))) if avg_cpa and cost >= WEAK_SPEND_X * avg_cpa else "")
        return "watch", "Spent %s and brought no results yet: too little to say." % "{:,.0f}".format(cost)
    if avg_cpa and conv >= MIN_RESULTS_TO_JUDGE:
        if cpa <= GOOD_CPA_X * avg_cpa:
            return "works", "%s results at %s each, under the account's %s." % ("{:,.0f}".format(conv), "{:,.2f}".format(cpa), "{:,.2f}".format(avg_cpa))
        if cpa >= BAD_CPA_X * avg_cpa:
            return "weak", "%s results at %s each, over the account's %s." % ("{:,.0f}".format(conv), "{:,.2f}".format(cpa), "{:,.2f}".format(avg_cpa))
    if avg_cpa and conv >= MIN_RESULTS_TO_JUDGE:
        return "watch", "%s results at %s each: about the account's usual." % ("{:,.0f}".format(conv), "{:,.2f}".format(cpa))
    return "watch", "%s results so far: too few to compare." % "{:,.0f}".format(conv)


def read_meta_account(account, *, pages_fn, today=None):
    """What the account's ad sets aim at and how each did in 90 days. `pages_fn(path, params)` reads a Graph list. The ad sets failing is the failure of the piece; their results failing
    leaves the targeting and says the results are missing."""
    started = time.monotonic()
    start, end = _window(today)
    act = account["id"]
    try:
        adsets = ma.parse_adsets(pages_fn(*mi.adsets_request(act)))
    except Exception as e:                                           # noqa: BLE001
        return failed("metaaccount", "The ad sets of this Meta ad account could not be read (%s)." % rs._clip(e, 140), time.monotonic() - started)
    notes, stats = [], {}
    try:
        stats = ma.parse_insights(pages_fn("/%s/insights" % act, {"level": "adset", "time_range": json.dumps({"since": start, "until": end}), "fields": ma.INSIGHT_FIELDS, "limit": meta.PAGE_LIMIT}))
    except Exception as e:                                           # noqa: BLE001
        notes.append("What each ad set spent and brought could not be read (%s), so the interests in use are listed without results." % rs._clip(e, 120))
    rows = [_adset_row(a, stats) for a in adsets]
    cost, conv = sum(r["cost"] for r in rows), sum(r["conv"] for r in rows)
    avg = round(cost / conv, 2) if conv else None
    used = mi.in_use(adsets, stats)
    in_use = []
    for oid, u in used.items():
        verdict, why = use_verdict(u, avg) if stats else ("unknown", "Results could not be read.")
        in_use.append({"id": oid, "name": u["name"], "kind": u["kind"], "adsets": u["adsets"], "cost": u["cost"], "conv": u["conv"], "cpa": u["cpa"], "verdict": verdict, "why": why})
    in_use.sort(key=lambda u: -u["cost"])
    data = {"account": {"id": act, "name": account.get("name") or "", "currency": account.get("currency") or "USD", "platform": "meta"}, "window": {"start": start, "end": end},
            "adsets": rows[:200], "adsetCount": len(rows), "totals": {"cost": round(cost, 2), "conv": round(conv, 1), "cpa": avg}, "inUse": in_use, "notes": notes,
            "advantageAdsets": sum(1 for r in rows if r["advantage"])}
    if not rows:
        return piece("metaaccount", "ok", "The account has no ad sets.", data, time.monotonic() - started)
    note = "%d ad sets, %d interests and behaviors in use, %s spent and %s results in 90 days." % (len(rows), len(in_use), "{:,.0f}".format(cost), "{:,.0f}".format(conv))
    return piece("metaaccount", "partial" if notes else "ok", " ".join([note] + notes), data, time.monotonic() - started)


def read_interests(seeds, research, *, get_fn):
    """The interests Meta has for the phrases chosen, and which of them the account already aims at. `seeds` is {phrases, behaviorWords, avoid}. Failing to find any interest at all is
    the failure of the piece (Meta keeps no interest for some topics, for health conditions among them, and the page says so)."""
    started = time.monotonic()
    phrases = [p for p in (seeds or {}).get("phrases") or [] if p]
    if not phrases:
        return failed("interests", "There was no phrase to look up in Meta's interests.", time.monotonic() - started)
    in_use = ((((research or {}).get("metaaccount") or {}).get("data")) or {}).get("inUse") or []
    used = {u["id"]: u for u in in_use}
    found, notes = mi.find(phrases, get_fn)
    names = [u["name"] for u in sorted(in_use, key=lambda u: -u["cost"]) if u["kind"] == "interest"][:4]
    for p in phrases[:4]:
        top = (found.get(p) or [None])[0]
        if top:
            names.append(top["name"])
    sugg, n2 = mi.suggestions(names, get_fn)
    beh, n3 = mi.behaviors(get_fn) if (seeds or {}).get("behaviorWords") else ([], [])
    notes += n2 + n3
    cands = mi.candidates(found, sugg, beh, used, avoid=(seeds or {}).get("avoid") or [], phrases_for_behaviors=(seeds or {}).get("behaviorWords") or [])
    if not cands:
        empty = [p for p in phrases if not found.get(p)]
        why = "Meta has no interest for any of these phrases: %s." % ", ".join('"%s"' % p for p in empty[:6])
        return failed("interests", why + " (Meta keeps no interest for some topics, health conditions among them.) " + " ".join(notes), time.monotonic() - started)
    used_first = [c for c in cands if c["inUse"]] + [c for c in cands if not c["inUse"]]
    cut = len(cands) > MAX_CANDIDATES
    kept = used_first[:MAX_CANDIDATES]
    empty = [p for p in phrases if not found.get(p)]
    data = {"phrases": phrases, "candidates": kept, "found": len(cands), "cut": cut, "emptyPhrases": empty, "suggested": len(sugg), "behaviors": len(beh), "notes": notes}
    note = "%d interests and behaviors from %d phrases%s." % (len(kept), len(phrases), (" (the first %d of %d)" % (MAX_CANDIDATES, len(cands))) if cut else "")
    return piece("interests", "partial" if notes else "ok", " ".join([note] + notes), data, time.monotonic() - started)


# ── The judged interests as rows ────────────────────────────────────────────

FITS = ("yes", "maybe", "no")


def assemble(cands, judged):
    """[{id, name, kind, size, sizeText, small, path, topic, from, inUse, fit, intent, theme, note}] one per candidate, judged or not (an unjudged one has fit 'unjudged')."""
    out = []
    for c in cands:
        j = judged.get(c["id"]) or {}
        out.append(dict(c, fit=j.get("fit") or "unjudged", intent=j.get("intent") or "", theme=j.get("theme") or "", note=j.get("note") or ""))
    return out


def coverage(rows, in_use):
    """The counts the page shows, from the rows and the account's interests in use."""
    fit = [r for r in rows if r["fit"] == "yes"]
    new = [r for r in fit if not r.get("inUse")]
    by_id = {r["id"]: r for r in rows}
    misfit = [u for u in in_use if (by_id.get(u["id"]) or {}).get("fit") == "no"]
    return {"judged": sum(1 for r in rows if r["fit"] != "unjudged"), "unjudged": sum(1 for r in rows if r["fit"] == "unjudged"), "yes": len(fit), "new": len(new),
            "inUse": len(in_use), "inUseFit": sum(1 for r in fit if r.get("inUse")), "misfitSpend": round(sum(u["cost"] for u in misfit), 2), "misfit": [u["id"] for u in misfit],
            "weak": [u["id"] for u in in_use if u.get("verdict") == "weak"], "works": [u["id"] for u in in_use if u.get("verdict") == "works"]}
