"""Paid Marketing Agents: Business Research, what of the writing is kept.

The model's three answers (tracker/paid_business_ai.py) are text it chose. This file decides what is kept, with the same rules as Strategy & Campaign Book's
(tracker/paid_strategy_check.py): every field has its type and no markup, no em or en dashes, a fact is only "on a page" when its quote is word for word on a
page that was read, a page address must be on the client's own site, a claim that is not on the client's pages or in what the search found is listed for the
client to confirm. What the research did not find is never filled in: a competitor that was not read, a reputation that was not searched, a social finding
with no social run are dropped from the writing.
"""
from __future__ import annotations

import re

from . import paid_strategy as ps, paid_strategy_check as ck

PRIORITY = ("high", "medium", "low")
STRENGTH = ("proven", "claimed", "generic")


def _site(research):
    return (((research or {}).get("website") or {}).get("data") or {}).get("site", {}).get("url") or ""


def _pick(value, allowed, default):
    """The allowed word the text begins with or is: "High." and "high priority" are high; anything else is the default."""
    words = re.findall(r"[a-z]+", str(value or "").lower())
    return next((w for w in words[:2] if w in allowed), default)


def _urgency(value):
    t = str(value or "").lower()
    if "before" in t or "launch" in t:
        return "Before launch"
    if "later" in t or "quarter" in t:
        return "Later"
    return "First month"


def _own_page(raw, research, known):
    url, seen = ck.own_url(raw, _site(research), known)
    return url, seen


def _source(raw, research, known):
    """Where a point of difference or a proof says it was found: a page of the client's own site, or a page the reputation search read. Anything else is
    not kept (a link on the page must not take a person to an address a web page told the model to name)."""
    url, _seen = ck.own_url(raw, _site(research), known)
    if url:
        return url
    d = ((research or {}).get("reputation") or {}).get("data") or {}
    outside = set(d.get("sources") or []) | {r.get("url") for r in d.get("ratings") or [] if r.get("url")}
    try:
        cleaned = ps.clean_url(raw, "source")
    except ps.BadPayload:
        return ""
    return cleaned if cleaned in outside else ""


def core(raw, site_norm, outside_norm, research):
    """The business. `site_norm` is the client's pages (and the brief) as ck.norm made them, `outside_norm` what the reputation search found."""
    raw = raw if isinstance(raw, dict) else {}
    pos = raw.get("positioning") if isinstance(raw.get("positioning"), dict) else {}
    known = ck.known_urls(research)
    offerings = []
    for r in ck.rows(raw.get("offerings"), ["name", "description", "audience", "pricing", "page", "priority", "why"], n=14):
        r["priority"] = _pick(r["priority"], PRIORITY, "medium")
        r["page"], r["pageSeen"] = _own_page(r["page"], research, known)
        offerings.append(r)
    usps = []
    for r in ck.rows(raw.get("usps"), ["usp", "why_it_matters", "evidence", "source", "strength"], n=10):
        r["strength"] = _pick(r["strength"], STRENGTH, "claimed")
        r["source"] = _source(r["source"], research, known)
        r["verified"] = ck.in_corpus(r["evidence"], site_norm)                                  # word for word on a page of the client's
        r["viaSearch"] = not r["verified"] and ck.in_corpus(r["evidence"], outside_norm)          # only in what the reputation search reported (it names its sources)
        if r["strength"] == "proven" and not (r["verified"] or r["viaSearch"]):
            r["strength"] = "claimed"                      # a proof nobody can find word for word is a claim
        usps.append(r)
    facts = []
    for r in ck.rows(raw.get("facts"), ["fact", "quote", "source"], n=30, each=400):
        r["verified"] = ck.in_corpus(r["quote"], site_norm)
        facts.append(r)
    return {"summary": ck.para(raw.get("summary"), 1600), "snapshot": ck.rows(raw.get("snapshot"), ["item", "detail"], n=10), "offerings": offerings, "usps": usps,
            "proof": [dict(p, source=_source(p["source"], research, known)) for p in ck.rows(raw.get("proof"), ["point", "source"], n=10)],
            "positioning": {"promise": ck.text(pos.get("promise"), 300), "tone": ck.text(pos.get("tone"), 240), "wordsUsed": ck.lines(pos.get("wordsUsed"), 12, 60),
                            "wordsToAvoid": ck.lines(pos.get("wordsToAvoid"), 10, 60)},
            "compliance": ck.rows(raw.get("compliance"), ["topic", "rule", "impact"], n=8), "facts": facts,
            "siteGaps": ck.rows(raw.get("siteGaps"), ["issue", "why", "fix"], n=10), "assumptions": ck.lines(raw.get("assumptions"), 10)}


def audience(raw):
    raw = raw if isinstance(raw, dict) else {}
    voice = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    geo = raw.get("geography") if isinstance(raw.get("geography"), dict) else {}
    people = ck.rows(raw.get("audiences"), ["name", "who", "situation", "goal", "worries", "searches", "priority"], n=8)
    for p in people:
        p["priority"] = _pick(p["priority"], PRIORITY, "medium")
    return {"audiences": people, "journey": ck.rows(raw.get("journey"), ["stage", "what_happens", "friction", "opportunity"], n=8),
            "triggers": ck.lines(raw.get("triggers"), 8), "objections": ck.rows(raw.get("objections"), ["objection", "answer", "proof"], n=8),
            "voice": {"style": ck.text(voice.get("style"), 300), "vocabulary": ck.lines(voice.get("vocabulary"), 12, 60), "avoid": ck.lines(voice.get("avoid"), 10, 60)},
            "geography": {"summary": ck.para(geo.get("summary"), 600), "areas": ck.rows(geo.get("areas"), ["area", "note"], n=10)},
            "seasonality": ck.rows(raw.get("seasonality"), ["window", "why"], n=8)}


def implications(raw, research):
    """What it means. What the research did not read is left empty whatever the model wrote about it."""
    raw = raw if isinstance(raw, dict) else {}
    r = research or {}
    known = ck.known_urls(r)
    read_rivals = any(c.get("ok") for c in (((r.get("competitors") or {}).get("data") or {}).get("competitors") or []))
    has_rep = (r.get("reputation") or {}).get("state") in ("ok", "partial")
    has_social = bool(((r.get("social") or {}).get("data") or {}).get("platforms")) or bool(((r.get("social") or {}).get("data") or {}).get("crossPlatform"))
    comp = raw.get("competitive") if isinstance(raw.get("competitive"), dict) and read_rivals else {}
    rep = raw.get("reputation") if isinstance(raw.get("reputation"), dict) and has_rep else {}
    first = []
    for x in ck.rows(raw.get("advertiseFirst"), ["what", "why", "page", "evidence"], n=8):
        x["page"], x["pageSeen"] = _own_page(x["page"], r, known)
        first.append(x)
    questions = ck.rows(raw.get("questions"), ["question", "area", "why", "urgency"], n=30)       # a question with no area is still a question
    for q in questions:
        q["area"] = q["area"] or "General"
        q["urgency"] = _urgency(q["urgency"])
    steps = ck.rows(raw.get("nextSteps"), ["step", "owner", "when"], n=10)
    for s in steps:
        s["owner"] = {"position2": "Position2", "client": "Client"}.get(s["owner"].lower(), s["owner"])
    return {"summary": ck.lines(raw.get("summary"), 8, 320), "advertiseFirst": first, "holdBack": ck.rows(raw.get("holdBack"), ["what", "why"], n=8),
            "angles": ck.rows(raw.get("angles"), ["angle", "audience", "proof", "example"], n=8), "tests": ck.rows(raw.get("tests"), ["hypothesis", "how", "metric"], n=8),
            "competitive": {"summary": ck.para(comp.get("summary"), 1000), "edge": ck.lines(comp.get("edge"), 6), "exposed": ck.lines(comp.get("exposed"), 6)} if comp else {},
            "reputation": {"summary": ck.para(rep.get("summary"), 800), "strengths": ck.lines(rep.get("strengths"), 6), "risks": ck.lines(rep.get("risks"), 6),
                           "actions": ck.lines(rep.get("actions"), 6)} if rep else {},
            "social": ck.rows(raw.get("social"), ["insight", "use"], n=6) if has_social else [],
            "tracking": ck.rows(raw.get("tracking"), ["item", "seen", "action"], n=10), "questions": questions,
            "risks": ck.rows(raw.get("risks"), ["risk", "mitigation"], n=8), "nextSteps": steps}


def claim_items(core_, audience_, impl):
    """(where, text) of everything the writing says that could end up in an ad or be repeated to the client, for the claims scan."""
    items = [("Summary", core_.get("summary"))]
    items += [("Fact", f["fact"]) for f in core_.get("facts") or []]
    items += [("USP", u["usp"]) for u in core_.get("usps") or []]
    items += [("Proof", p["point"]) for p in core_.get("proof") or []]
    items += [("Pricing of " + o["name"], o["pricing"]) for o in core_.get("offerings") or []]
    items += [("Objection answer", o["answer"]) for o in audience_.get("objections") or []]
    items += [("In short", t) for t in impl.get("summary") or []]
    items += [("Advertise first", a["why"]) for a in impl.get("advertiseFirst") or []] + [("Advertise first", a["evidence"]) for a in impl.get("advertiseFirst") or []]
    items += [("Angle", a["example"]) for a in impl.get("angles") or []]
    items += [("Reputation", (impl.get("reputation") or {}).get("summary"))]
    return [(w, t) for w, t in items if t]
