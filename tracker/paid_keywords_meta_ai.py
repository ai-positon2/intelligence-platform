"""Paid Marketing Agents: Keyword Research for Meta, the judging.

Two questions to the model, each from the evidence and the answer before it:

  seeds      which phrases to look up in Meta's interest list (Meta's names are broad and canonical: "Diabetes", "Running", "Dentistry"), which words to filter Meta's behaviors by
             and which words make an interest wrong
  audiences  for every interest and behavior Meta returned (and the ones the account already aims at): is it a fit, what are the people after, and then the audiences worth testing
             built from the fits, the interests to exclude, lookalike sources, and the plan

The model sees the size of each audience and how each interest in use has done (all Meta's, copied by code) and is never asked for a number. What it writes is held to what Meta
returned: an interest id that was not in the list is dropped, an audience made of interests judged not a fit loses them, and a number in the write-up that was not in the evidence is
listed for a person to check. The house rules, the schema builders and the model call are Strategy & Campaign Book's (tracker/paid_strategy_ai.py).
"""
from __future__ import annotations

import logging
import re

from . import paid_keywords_ai as kai, paid_keywords_check as kck, paid_strategy_ai as ai, paid_strategy_check as ck
from .paid_business_check import _pick, _urgency
from .paid_strategy_ai import HOUSE, obj, rowspec, strs

log = logging.getLogger(__name__)

MODEL = ai.MODEL
INTENTS = ("core", "related", "competitor", "broad", "other")
ROLES = ("prospecting", "competitor", "test")
MAX_PHRASES = 20
MAX_AUDIENCES = 8
MAX_EXCLUSIONS = 20

SEEDS = obj(phrases=strs(), behaviorWords=strs(), avoid=strs(), assumptions=strs())
AUDIENCES = obj(rows=rowspec("id", "fit", "intent", "theme", "note"), audiences=ai.arr(obj(name=ai.S, role=ai.S, interestIds=strs(), why=ai.S, test=ai.S)),
                exclusions=rowspec("id", "why"), lookalikes=rowspec("seed", "why"), summary=strs(), quickWins=rowspec("what", "why"),
                questions=rowspec("area", "question", "why", "urgency"), risks=rowspec("risk", "mitigation"), assumptions=strs())
JUDGE_ONLY = obj(rows=rowspec("id", "fit", "intent", "theme", "note"))

NUMBERS = ("The sizes of the audiences and the results of the interests in use are Meta's, given to you so you can weigh them. Never write a number of your own: do not estimate reach, cost, "
           "results or revenue, and do not change one you were given.")
META_RULES = ("Meta's rules you must respect: Meta removed detailed targeting built on sensitive topics (health conditions, religion, race, sexuality, finances and the like), so a topic may "
              "have no interest, and an ad may never say or suggest that you know a personal attribute of the person seeing it. Write about the product, the activity and the need, not about people "
              "having a condition. Under Advantage+ audience, interests are suggestions to Meta, not limits.")

SEEDS_SYSTEM = HOUSE + "\n\n" + META_RULES + """

You plan audience research for a Meta (Facebook and Instagram) ad account. Meta ads are not bought on keywords: an ad set aims at interests and behaviors from Meta's own list. You are given
the client, what is known about its business, what the account's ad sets already aim at and how each did, and the competitors it named. Choose what to look up in Meta's list.
- phrases: 12 to 20 phrases of one to four words, each likely to be (or be close to) the name of a Meta interest: the thing the client sells, the need it meets, activities and topics its buyers
  care about, the places or kinds of organisation they use, and the brand names of rivals (as people who follow a rival). Plain and common words, no sentences. Do not repeat what the account already aims at.
- behaviorWords: up to six single words (for example travel, business owners, online shoppers) that pick the few of Meta's behaviors worth looking at; empty when none applies.
- avoid: words that make an interest wrong for this client (jobs, free, a place it does not serve, a service it does not offer).
- assumptions: what you had to assume.
"""

AUDIENCES_SYSTEM = HOUSE + "\n\n" + META_RULES + """

You judge interests for a client's Meta ad account and plan the audiences to test. You are given the interests and behaviors Meta returned (each with an id, its audience size and where it was found,
and, for the ones an ad set already aims at, how they did) and what is known about the business.
Return one row for EVERY interest given, with `id` copied exactly:
- fit: yes (people who care about what the client sells, or who plausibly buy it: worth an ad set), maybe (could be right but depends on something, which you say in the note), no (not a customer: a different
  product, a topic with no buying in it, a place or group the client does not serve, anything on the avoid list).
- intent: core (the product or need itself), related (something buyers also care about), competitor (a rival's brand), broad (a very wide interest that needs narrowing) or other.
- theme: two to four plain words naming the audience it belongs to (shared by interests that belong together).
- note: at most twelve words, the reason. Always give one for maybe and no.
An interest already in use is judged on its own merits: how it did is information, not an answer.
Then:
- audiences: three to eight audiences to test. Each: name, role (prospecting for people who have not heard of the client, competitor for followers of a rival, test for a wide or uncertain one), interestIds
  (ids copied from the list, only interests you judged yes or maybe, 2 to 8 each), why (one sentence), test (what to learn from it and how to tell). Do not build an audience of one very wide interest.
- exclusions: interests (ids from the list) that should be excluded from every ad set, with why (people who would never buy, or buyers' own staff and students).
- lookalikes: sources the client could build lookalike audiences from, from its own data (customer list, form fills, converting site visitors), each with why. Never say how big a source must be unless the evidence says.
- summary: up to seven bullets a busy account manager reads in a minute: what the interest list shows for this client, what the account already aims at and how it is doing, where the gaps are, what to do first.
- quickWins: the first moves (an audience to start, an interest to remove from an ad set, an interest that works to give more budget), with why. Name the interest or the audience.
- questions: what must be asked of the client, grouped by area (Offers, Locations and service area, Customers and who buys, Compliance, Budget), with why and urgency (before launch, first month, later).
- risks: what could go wrong and what to do. assumptions: what you had to assume.
""" + NUMBERS


JUDGE_SYSTEM = AUDIENCES_SYSTEM.split("\nThen:")[0] + "\n" + NUMBERS


def candidate_row(c):
    """One interest as the model is shown it: an object, so `id` is the whole of what it copies back."""
    row = {"id": c["id"], "name": c["name"], "kind": c["kind"], "audienceSize": c.get("sizeText") or "not given", "path": " > ".join(c.get("path") or []) or None, "foundFrom": c.get("from")}
    use = c.get("inUse")
    if use:
        row["alreadyAimedAt"] = {"adSets": use["adsets"], "spent": use["cost"], "results": use["conv"], "costPerResult": use["cpa"]}
    return row


def account_evidence(research):
    d = ((((research or {}).get("metaaccount") or {}).get("data")) or {})
    if not d:
        return None
    return {"totals": d.get("totals"), "adSets": [{"name": a["name"], "campaign": a.get("campaign"), "interests": [i["name"] for i in a["interests"]], "behaviors": [b["name"] for b in a["behaviors"]],
                                                 "audiences": a["audiences"], "spent": a["cost"], "results": a["conv"], "advantagePlus": a["advantage"]} for a in (d.get("adsets") or [])[:30]],
            "interestsInUse": [{"name": u["name"], "adSets": u["adsets"], "spent": u["cost"], "results": u["conv"], "verdict": u["verdict"]} for u in (d.get("inUse") or [])[:30]]}


def write_seeds(inputs, research, call):
    biz = kai.business_evidence(research) or {}
    user = ai._j({"client": ai.client_block(inputs), "business": biz or None, "siteHeadings": kai.site_headings(research), "account": account_evidence(research),
                  "competitorsNamed": [c.get("name") for c in inputs.get("competitors") or []], "avoidFromTheClient": [w for w in str(inputs.get("avoid") or "").replace(";", ",").split(",") if w.strip()],
                  "offerings": [{"name": o.get("name"), "priority": o.get("priority")} for o in biz.get("offerings") or []], "notAvailable": ai.missing(research, inputs)})
    return seeds(ai._call_stage(call, "seeds", SEEDS_SYSTEM, user, SEEDS, 3000), inputs)


def seeds(raw, inputs):
    """{phrases, behaviorWords, avoid, assumptions}: short phrases only, no repeats, the client's own avoid words added."""
    raw = raw if isinstance(raw, dict) else {}
    phrases = [p for p in ck.lines(raw.get("phrases"), MAX_PHRASES, 60) if 1 <= len(p.split()) <= 5 and re.search(r"[^\W\d_]", p)]       # short, and with a letter in it
    words = [w for w in ck.lines(raw.get("behaviorWords"), 6, 30)]
    mine = [w.strip() for w in str(inputs.get("avoid") or "").replace(";", ",").replace("\n", ",").split(",") if w.strip()]
    avoid = ck.dedupe_ci(ck.lines(raw.get("avoid"), 12, 40) + [ck.text(w, 40) for w in mine])[:20]
    return {"phrases": phrases, "behaviorWords": words, "avoid": avoid, "assumptions": ck.lines(raw.get("assumptions"), 6)}


def judged(raw, wanted):
    """(judged, dropped): {id: {fit, intent, theme, note}} for the candidates the model answered about, and how many answers were dropped (not a candidate, or a second answer)."""
    raw = raw if isinstance(raw, dict) else {}
    out, dropped = {}, 0
    for r in ck.rows(raw.get("rows"), ["id", "fit", "intent", "theme", "note"], n=400, each=240):
        oid = str(r["id"]).split(" | ")[0].strip()
        if oid not in wanted or oid in out:
            dropped += 1
            continue
        out[oid] = {"fit": _pick(r["fit"], ("yes", "maybe", "no"), "maybe"), "intent": _pick(r["intent"], INTENTS, "other"), "theme": ck.text(r["theme"], 60), "note": ck.text(r["note"], 160)}
    return out, dropped


def audiences(raw, by_id, judgements):
    """(kept, dropped): the audiences the model proposed, each made only of interests that were in the list and were not judged not a fit. An audience left with no interest is dropped."""
    raw = raw if isinstance(raw, dict) else {}
    kept, dropped, seen = [], 0, set()
    for r in raw.get("audiences") if isinstance(raw.get("audiences"), list) else []:
        if not isinstance(r, dict):
            continue
        name = ck.text(r.get("name"), 60)
        ids = []
        for x in r.get("interestIds") if isinstance(r.get("interestIds"), list) else []:
            oid = str(x).strip()
            if oid in by_id and oid not in ids and (judgements.get(oid) or {}).get("fit") != "no":
                ids.append(oid)
            else:
                dropped += 1
        if not name or name.lower() in seen or not ids:
            dropped += 1                                              # the whole audience: no name, a second of the same name, or nothing left in it
            continue
        seen.add(name.lower())
        items = [by_id[i] for i in ids[:12]]
        kept.append({"name": name, "role": _pick(r.get("role"), ROLES, "prospecting"), "why": ck.text(r.get("why"), 300), "test": ck.text(r.get("test"), 300),
                     "interests": [{"id": i["id"], "name": i["name"], "sizeText": i.get("sizeText") or "", "inUse": bool(i.get("inUse"))} for i in items],
                     "largest": max([i.get("size") or 0 for i in items] or [0]) or None})
        if len(kept) >= MAX_AUDIENCES:
            break
    return kept, dropped


def exclusions(raw, by_id, aud):
    """(kept, dropped): interests to exclude, each from the list; one that is part of a proposed audience is dropped (it cannot be both)."""
    raw = raw if isinstance(raw, dict) else {}
    used = {i["id"] for a in aud for i in a["interests"]}
    kept, dropped, seen = [], 0, set()
    for r in ck.rows(raw.get("exclusions"), ["id", "why"], n=MAX_EXCLUSIONS * 2, each=240):
        oid = str(r["id"]).strip()
        if oid not in by_id or oid in seen or oid in used:
            dropped += 1
            continue
        seen.add(oid)
        kept.append({"id": oid, "name": by_id[oid]["name"], "sizeText": by_id[oid].get("sizeText") or "", "why": r["why"], "inUse": bool(by_id[oid].get("inUse"))})
        if len(kept) >= MAX_EXCLUSIONS:
            break
    return kept, dropped


def write_judge(inputs, research, cands, avoid, call):
    """(judged, dropped) for a list of candidates only: used for the ones the first answer left out."""
    user = ai._j({"client": ai.client_block(inputs), "business": kai.business_evidence(research), "avoid": avoid or None, "interests": [candidate_row(c) for c in cands]})
    return judged(ai._call_stage(call, "judge", JUDGE_SYSTEM, user, JUDGE_ONLY, 9000), {c["id"] for c in cands})


def write_audiences(inputs, research, cands, seed_plan, call):
    """The whole answer, checked: {judged, audiences, exclusions, lookalikes, plan, dropped}. Interests the first answer left out are asked about once more."""
    by_id = {c["id"]: c for c in cands}
    biz = kai.business_evidence(research) or {}
    user = ai._j({"client": ai.client_block(inputs), "business": biz or None, "avoid": seed_plan.get("avoid"), "competitorsNamed": [c.get("name") for c in inputs.get("competitors") or []],
                  "account": account_evidence(research), "interests": [candidate_row(c) for c in cands], "notAvailable": ai.missing(research, inputs)})
    raw = ai._call_stage(call, "audiences", AUDIENCES_SYSTEM, user, AUDIENCES, 16000)
    got, dropped = judged(raw, set(by_id))
    left_out = [c for c in cands if c["id"] not in got]
    if left_out:                                                      # asked about once more; a second failure leaves them unjudged and says so
        try:
            more, d = write_judge(inputs, research, left_out, seed_plan.get("avoid"), call)
            got.update(more)
            dropped += d
        except Exception as e:                                        # noqa: BLE001
            log.warning("keywords meta: asking again about %d left-out interests failed: %s", len(left_out), str(e)[:200])
    aud, d2 = audiences(raw, by_id, got)
    exc, d3 = exclusions(raw, by_id, aud)
    questions = ck.rows(raw.get("questions") if isinstance(raw, dict) else [], ["question", "area", "why", "urgency"], n=20)
    for q in questions:
        q["area"] = q["area"] or "General"
        q["urgency"] = _urgency(q["urgency"])
    plan = {"summary": ck.lines(raw.get("summary"), 7, 320), "quickWins": ck.rows(raw.get("quickWins"), ["what", "why"], n=8), "risks": ck.rows(raw.get("risks"), ["risk", "mitigation"], n=8),
            "questions": questions, "lookalikes": ck.rows(raw.get("lookalikes"), ["seed", "why"], n=6), "assumptions": ck.lines(raw.get("assumptions"), 8)}
    items = [("In short", t) for t in plan["summary"]] + [("First move", "%s. %s" % (q["what"], q["why"])) for q in plan["quickWins"]]
    items += [("Risk", "%s. %s" % (r["risk"], r["mitigation"])) for r in plan["risks"]] + [("Audience %s" % a["name"], "%s %s" % (a["why"], a["test"])) for a in aud]
    plan["numbers"] = kck.unbacked_numbers(items, kck.numbers_in(user))
    return {"judged": got, "audiences": aud, "exclusions": exc, "plan": plan, "dropped": dropped + d2 + d3}
