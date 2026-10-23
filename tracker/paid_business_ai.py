"""Paid Marketing Agents: Business Research, the writing.

Three questions to the model, one after the other, each from the evidence and the answers before it:

  core         the business: what it is, what it sells, what sets it apart and what proves it, the voice, the rules, the facts an ad may state, what the site gets wrong
  audience     who buys and why: the people, what they are trying to do, how they decide and what stops them, the journey from search to customer, where it serves
  implications what it means for paid media (what to advertise first, what to hold back, angles and tests), the competitors, the reputation and the social
               findings in use, what the site is set up to measure, the questions to put to the client, the risks and the next steps

Every answer is a JSON schema answer, written plainly, and is checked by tracker/paid_business_check.py before it is kept. The model never does
arithmetic and is never shown a number it could mistake for its own: ratings and counts come from tracker/paid_business_research.py as the search returned them.
The prompts, the evidence builders and the model call are Strategy & Campaign Book's (tracker/paid_strategy_ai.py).
"""
from __future__ import annotations

from . import paid_business_check as bck, paid_business_research as brs, paid_strategy_ai as ai, paid_strategy_check as ck, paid_strategy_research as rs
from .paid_strategy_ai import HOUSE, S, arr, obj, rowspec, strs

MODEL = ai.MODEL

CORE = obj(
    summary=S, snapshot=rowspec("item", "detail"),
    offerings=rowspec("name", "description", "audience", "pricing", "page", "priority", "why"),
    usps=rowspec("usp", "why_it_matters", "evidence", "source", "strength"),
    proof=rowspec("point", "source"), positioning=obj(promise=S, tone=S, wordsUsed=strs(), wordsToAvoid=strs()),
    compliance=rowspec("topic", "rule", "impact"), facts=rowspec("fact", "quote", "source"), siteGaps=rowspec("issue", "why", "fix"), assumptions=strs())

AUDIENCE = obj(
    audiences=rowspec("name", "who", "situation", "goal", "worries", "searches", "priority"),
    journey=rowspec("stage", "what_happens", "friction", "opportunity"), triggers=strs(), objections=rowspec("objection", "answer", "proof"),
    voice=obj(style=S, vocabulary=strs(), avoid=strs()), geography=obj(summary=S, areas=rowspec("area", "note")), seasonality=rowspec("window", "why"))

IMPLICATIONS = obj(
    summary=strs(), advertiseFirst=rowspec("what", "why", "page", "evidence"), holdBack=rowspec("what", "why"),
    angles=rowspec("angle", "audience", "proof", "example"), tests=rowspec("hypothesis", "how", "metric"),
    competitive=obj(summary=S, edge=strs(), exposed=strs()), reputation=obj(summary=S, strengths=strs(), risks=strs(), actions=strs()), social=rowspec("insight", "use"),
    tracking=rowspec("item", "seen", "action"), questions=rowspec("area", "question", "why", "urgency"), risks=rowspec("risk", "mitigation"),
    nextSteps=rowspec("step", "owner", "when"))

CORE_SYSTEM = HOUSE + """

You are the first reader for a client the agency is about to work for, before any audit or strategy. Read the client's pages, the brief and everything else given, and describe the business so that someone who has never heard of it understands it:
- summary: what the business is, who it serves, where, and how it makes money, in a short paragraph.
- snapshot: rows of item and detail, for: business model, who it serves, where it operates, size and stage (only what a source says), ownership (only what a source says), how a customer buys.
- offerings: each thing it sells. `audience` who it is for, `pricing` what the pages say about price (a quoted price or "not shown"), `page` the address of the page that sells it, `priority` high, medium or low for paid search (what to advertise first, from demand, margin and how clearly the site sells it), `why` the reason for that priority.
- usps: what truly sets it apart from others that sell the same thing. `evidence` words copied EXACTLY from a page (at least 12 characters) when a page backs it, `source` that page's address, `strength` proven (a page or an outside source backs it), claimed (it says so but nothing backs it) or generic (every competitor says it).
- proof: trust points that can be shown (reviews, years, credentials, guarantees, case results), each with where it is.
- positioning: the promise it makes, its tone, the words its own pages use for what it sells (wordsUsed) and words the client uses or would not want used (wordsToAvoid, from the brief or from what its pages avoid).
- compliance: rules that touch what can be said or targeted in advertising for this business (healthcare, finance, legal, regulated devices, personalised advertising limits, claims that need proof), from the market evidence or the nature of the business.
- facts: short facts an ad may state, each with `quote` copied EXACTLY from a page (at least 12 characters) and `source` the page address. Include only what the pages say: prices, offers, hours, locations, credentials, claims.
- siteGaps: what on the site will waste paid traffic (no phone or form near the top, thin or missing pages for a service, no proof, no price where buyers want one, a slow or confusing path), each with the fix.
- assumptions: what you had to assume."""

AUDIENCE_SYSTEM = HOUSE + """

Now work out who buys and how they decide, from the evidence and what the first reader found.
- audiences: the groups of people who buy (a person, or a person and the one who refers or pays). `who` them in a sentence, `situation` what is happening in their life or work when they look, `goal` what they are trying to get done, `worries` what holds them back, `searches` what they type or ask (use the account's real search terms and the customers' own questions when they were given), `priority` high, medium or low.
- journey: the steps from first search to customer (aware, compare, choose, contact, convert, return), what happens at each, the friction, and the chance for advertising or the site.
- triggers: the moments that make someone start looking.
- objections: what stops people choosing this business, the honest answer, and the proof for it (or "none found").
- voice: the writing style that fits (style), words and phrases customers use (vocabulary), and what to keep out (avoid).
- geography: where customers come from (summary) and the areas that matter (area and note), only from what the evidence says.
- seasonality: times of year or events that move demand, each with why, only when the evidence says so."""

IMPLICATIONS_SYSTEM = HOUSE + """

Now say what the research means for paid media and for the next conversation with the client. Never promise a result; a test is a hypothesis.
- summary: eight bullets a busy account manager can read in a minute: who the client is, what sets it apart, who to reach, what to advertise first, what to hold back, what the site and tracking need, the biggest risk, the first move.
- advertiseFirst: what to put paid money behind first, why (demand, margin, how clearly the site sells it, what already converted), the page to send people to, and the evidence.
- holdBack: what not to advertise yet, and why (no page, no proof, a regulated claim, nothing known about margin).
- angles: messages worth testing, for which audience, the proof it rests on, and a one-line example of how it would sound.
- tests: hypotheses to test early: the hypothesis, how to test it, the metric.
- competitive: how the client compares with the competitors that were read (summary), where it stands apart (edge) and where it is exposed (exposed). Write nothing about a competitor that was not read. If none was read, leave the summary empty.
- reputation: what the reviews and press say (summary), what to lean on (strengths), what could hurt (risks), and what to do about it (actions). Use only the reputation evidence; if there is none, leave the summary empty.
- social: what the brand's own posts show and how to use it in ads (only from the social evidence).
- tracking: set-up items to check with the client, from what the pages carry and the account's conversion actions: `item` (analytics, conversion actions, call tracking, forms, CRM, consent), `seen` what the evidence shows (say "not seen" when it is not in the evidence, and that a tag may load later), `action` what to confirm or set up.
- questions: what must be asked of the client, grouped by `area` (Goals and targets, Budget, Offers and pricing, Customers, Conversion tracking, Lead handling and CRM, Brand and compliance, Access and assets), the question, why it matters, and urgency (before launch, first month, later). Do not ask what the evidence already answers.
- risks: what could go wrong and what to do about it.
- nextSteps: the next steps, who does each (Position2 or the client) and when."""


def reputation_evidence(research):
    d = ((research or {}).get("reputation") or {}).get("data") or {}
    return {k: d.get(k) for k in ("ratings", "praise", "complaints", "questions", "press", "awards", "people", "company")} if d else None


def tech_evidence(research):
    d = (((research or {}).get("website") or {}).get("data") or {}).get("tech") or {}
    if not d or not d.get("pages"):
        return None
    return {"toolsSeen": [{"name": t["name"], "category": t["category"], "ids": t["ids"], "pages": t["pages"]} for t in d.get("tools") or []], "schemaTypes": d.get("schema"),
            "pagesRead": d.get("pages"), "mobileViewportPages": d.get("mobilePages"), "notSeen": d.get("gaps"), "note": d.get("note")}


def conversion_actions(research):
    h = ((research or {}).get("history") or {}).get("data") or {}
    return h.get("actions") or None


def write_core(inputs, research, call):
    user = ai._j({"client": ai.client_block(inputs), "website": ai.site_evidence(research), "account": ai.history_evidence(research), "reputation": reputation_evidence(research),
                  "market": ai.market_evidence(research), "social": ai.social_evidence(research), "notAvailable": ai.missing(research, inputs)})
    return bck.core(ai._call_stage(call, "core", CORE_SYSTEM, user, CORE, 10000), ck.norm(rs.corpus(research)), ck.norm(brs.corpus(research)), research)


def _core_for_prompt(core):
    return {k: core.get(k) for k in ("summary", "snapshot", "offerings", "usps", "proof", "positioning", "compliance", "siteGaps")}


def write_audience(inputs, research, core, call):
    user = ai._j({"client": ai.client_block(inputs), "firstReader": _core_for_prompt(core), "website": ai.site_evidence(research, per_page=700, total=12000),
                  "account": ai.history_evidence(research), "reputation": reputation_evidence(research), "market": ai.market_evidence(research),
                  "social": ai.social_evidence(research), "notAvailable": ai.missing(research, inputs)})
    return bck.audience(ai._call_stage(call, "audience", AUDIENCE_SYSTEM, user, AUDIENCE, 9000))


def write_implications(inputs, research, core, audience, call):
    rivals = ai.rival_evidence(research, per_page=500)
    user = ai._j({"client": ai.client_block(inputs), "firstReader": _core_for_prompt(core), "audience": {k: audience.get(k) for k in ("audiences", "journey", "triggers", "objections", "voice")},
                  "competitors": rivals, "reputation": reputation_evidence(research), "social": ai.social_evidence(research), "siteTracking": tech_evidence(research),
                  "conversionActions": conversion_actions(research), "account": ai.history_evidence(research), "market": ai.market_evidence(research),
                  "notAvailable": ai.missing(research, inputs)})
    return bck.implications(ai._call_stage(call, "implications", IMPLICATIONS_SYSTEM, user, IMPLICATIONS, 12000), research)
