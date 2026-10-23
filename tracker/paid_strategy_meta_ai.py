"""Paid Marketing Agents: Strategy & Campaign Book, the writing of the Meta plan.

A Meta plan is written in two of the Campaign Book's stages, next to the Google ones, from the same business profile, competitor read and strategy:

    architecture  the Meta campaigns to build (an objective each) and each one's settings
    build         one call per campaign: ad sets with an audience, and ads (primary text, headline, description, button, concept)

Meta ads are not bought on keywords. An ad set aims at interests and behaviors from Meta's own list, at the advertiser's audiences and at lookalikes, so the model is shown Meta's list (with
the size of each audience, and how the ones the account already aims at did) and may only name an interest from it; what it names is then looked up in Meta's list once more and what Meta has
no such name for is dropped (tracker/paid_strategy_meta_check.py). The text is held to what Meta shows. The house rules, the schema builders and the model call are the Campaign Book's
(tracker/paid_strategy_ai.py); this file asks, the check file decides what is kept.
"""
from __future__ import annotations

from . import paid_meta_interests as mi, paid_strategy as ps, paid_strategy_ai as ai, paid_strategy_check as ck, paid_strategy_meta_check as mc, paid_strategy_research_meta as mrs
from .paid_strategy_ai import HOUSE

ARCHITECTURE_META, BUILD_META = ai.ARCHITECTURE_META, ai.BUILD_META

META_STANDARDS = """Position2's standing settings for Meta campaigns (a starter: the team has not written its Meta standards down yet, so say what you set and why): special ad category none unless the ads are about credit, jobs, housing, or social issues, elections or politics; Advantage campaign budget on; highest volume bidding to start, a cost per result goal only after about 50 results in a week; conversion location Website unless the objective needs another; attribution 7-day click and 1-day view; location type people living in or recently in the place; Advantage+ placements; no end date; campaign names P2_<MetaLeads, MetaSales, MetaTraffic or MetaAwareness>_<theme>_<launch date>; at least 3 and at most 10 ads in an ad set; every ad has a Facebook Page and an Instagram account; tracking parameters on every ad; creative enhancements off unless the client agrees."""

META_RULES = """Rules of thumb for Meta (state when you are using one as an assumption): an ad set needs about 50 results in a week to leave the learning phase, so a small budget gets few ad sets; a Leads or Sales campaign needs a pixel (a dataset) and a conversion event; one idea in each ad set; people who have not heard of the client and people who have visited are different ad sets; the creative matters more than the audience, so give Meta three or more different ads (a different picture or video and a different first line) in every ad set; Advantage+ audience treats the interests as suggestions, not limits."""

META_POLICY = """Meta's advertising policies you must follow: never say or suggest that you know a personal attribute of the person who sees the ad (a health condition, race, religion, sexuality, money trouble, age or anything like it) and write about the product and the need, not about the person; no before and after claims; healthcare, finance and legal ads have extra rules; a special ad category limits targeting by age, gender and postcode. Meta removed detailed targeting built on sensitive topics, so a topic may have no interest."""

STRATEGY_ADDENDUM = """

This client advertises on Meta (Facebook and Instagram){also}. Plan Meta as a channel of its own in `channels` (name it Meta Ads, with its share of the budget), say in `bidding` how a Meta campaign starts and moves on, put the Meta audiences in `targeting.audiences` (interests only from the list `interestsMetaHas`, or the client's own audiences and lookalikes), put the pixel (dataset), the Conversions API, event match quality and the aggregated event measurement in `measurement.tracking`, and plan creative tests (a new angle, a video against an image) in `tests`. Meta's own numbers for the account (`metaAccount`) are the basis for targets, never Google's. """ + META_STANDARDS + "\n\n" + META_RULES + "\n\n" + META_POLICY

ARCH_META_SYSTEM = HOUSE + "\n\n" + META_STANDARDS + "\n\n" + META_RULES + "\n\n" + META_POLICY + """

Design the Meta campaigns to build. For each: kind (one of metaleads, metasales, metatraffic, metaawareness, only from the kinds allowed), theme (a short name: Implants, Brand, Retargeting, Offer...), purpose, share (its share of the monthly budget for Meta; shares are normalised in code), landingPage (an address from the client's pages given, or empty), audience (who it is for), notes, and settings: for each of specialCategory, budgetType, bidStrategy, conversionLocation, optimization (the performance goal), conversionEvent, attribution, locations, ages, genders, languages, placements and creativeEnhancements give a `value` and the `why`. Locations are specific place names, from the client's service area and the account's best places. Ages and genders stay open unless the evidence gives a reason (and a special ad category forces them open): say so in why. The bidStrategy value names the start and the move on. Choose the objective from the client's goal: leads and calls and bookings are metaleads, online sales are metasales, a campaign that only sends people to the site is metatraffic and awareness is metaawareness. Plan only as many campaigns as the budget can feed, at most 6, with an ad set budget of at least what about 50 results a week costs at the account's own cost per result when that is given; say in notes what each campaign does that the others do not."""

BUILD_META_SYSTEM = HOUSE + "\n\n" + META_STANDARDS + "\n\n" + META_RULES + "\n\n" + META_POLICY + """

Ad copy rules: primary text is at most 125 characters, headlines at most 40, descriptions at most 30 (optional). Anything longer is thrown away, so count. Do not repeat a headline. Use the client's own words and the facts given. A price, percentage, number of years, rating, "free", guarantee or "best" may appear only if it is in the facts, and otherwise must not appear. No competitor names in ad text. No dashes. Landing page addresses must come from the client's pages given.

Build ONE Meta campaign.
- adSets: two to five, each with one audience idea: name, theme, audienceType (interests, lookalike, retargeting or broad), interests (two to eight, copied EXACTLY from the list `interestsMetaHas` and from nothing else: an interest that is not on that list is thrown away), behaviors (only from that list), customAudiences (what to build or use: website visitors, a customer list, people who watched the video, each with its window), lookalikes (the source and the percent), excludes (audiences and interests to leave out), budgetShare (its share of the campaign's budget), placements (Advantage+ placements unless there is a reason), landingPage from the pages given.
- ads: three to six in each ad set, each different from the others in picture or video and in its first line: name, format (image, video or carousel; at least one video in the campaign unless the budget is small), primaryText, headline, description (may be empty), cta (one of Learn More, Get Quote, Book Now, Contact Us, Call Now, Sign Up, Apply Now, Get Offer, Shop Now, Download, Subscribe, Send Message, Order Now, Get Directions), concept (what is shown and why it should work for this audience, using the social findings when given), script (the first three seconds and the structure for a video, empty for an image), finalUrl from the pages given.
- Never write for a custom audience or lookalike that needs data the client may not have without saying so in the ad set's theme."""


def audience_for_prompt(research):
    return mrs.audience_evidence(research)


def meta_history_for_prompt(research):
    return mrs.evidence(research)


def write_architecture(inputs, research, profile, strategy, call, budget):
    """The Meta campaign skeletons, checked. `budget` is the monthly budget the Meta side got."""
    user = ai._j({"client": ai.client_block(inputs), "allowedKinds": ps.kinds_for(inputs, "meta"), "profile": {k: profile.get(k) for k in ("summary", "offerings", "audiences", "compliance")},
                  "strategy": {k: strategy.get(k) for k in ("channels", "bidding", "targeting", "budget", "measurement")}, "clientPages": ai.pages_for_prompt(research),
                  "metaAccount": meta_history_for_prompt(research), "metaAudience": audience_for_prompt(research), "monthlyBudgetForMeta": budget})
    return mc.architecture(ai._call_stage(call, "architecture:meta", ARCH_META_SYSTEM, user, ARCHITECTURE_META, 9000), inputs, budget=budget)


def write_build(campaign, inputs, research, profile, strategy, competitive, call, problems=None):
    """(build, drops) for one Meta campaign, before the interests are looked up again."""
    ctx = {"site": inputs.get("website"), "known": ck.known_urls(research), "landingPage": ""}
    user = ai._j({"client": ai.client_block(inputs), "campaign": {k: campaign.get(k) for k in ("name", "kind", "objective", "theme", "purpose", "landingPage", "audience", "notes", "settings", "daily")},
                  "facts": ai._facts_for_prompt(profile), "positioning": profile.get("positioning"), "messaging": strategy.get("messaging"), "clientPages": ai.pages_for_prompt(research),
                  "interestsMetaHas": (audience_for_prompt(research) or {}).get("interestsMetaHas"), "interestsInUse": (audience_for_prompt(research) or {}).get("interestsInUse"),
                  "account": meta_history_for_prompt(research), "competitors": [{k: c.get(k) for k in ("name", "positioning", "messages", "themes")} for c in (competitive or {}).get("competitors", [])],
                  "whiteSpace": (competitive or {}).get("whiteSpace"), "social": ai.social_evidence(research), "compliance": profile.get("compliance"), "fixTheseFromLastTime": problems or None})
    raw = ai._call_stage(call, "build:" + campaign["key"], BUILD_META_SYSTEM, user, BUILD_META, 16000)
    drops = {}
    built = mc.build(raw, ctx, drops)
    built["drops"] = drops
    return built, drops


def verify(built, get_fn):
    """The build with every interest in it looked up in Meta's list: (build, dropped interest names, notes). When `get_fn` is missing nothing is checked and every interest is marked unverified."""
    names = [n for g in built.get("adSets") or [] for n in g.get("interests") or []]
    if not names:
        return built, [], []
    if get_fn is None:
        built, _ = mc.verify_interests(built, {}, unanswered=set(names))
        return built, [], ["Meta's interest list could not be asked, so no interest is checked."]
    answers, notes = mi.validate(names, get_fn)
    failed = {n for n in names if any('"%s"' % n in note for note in notes)}
    built, dropped = mc.verify_interests(built, answers, unanswered=failed)
    return built, dropped, notes
