"""Paid Marketing Agents: Keyword Research, what a build is and what it is asked for.

The agent turns a client (what it sells, a website, optionally a Google Ads account) into a list of keyword opportunities: what people search for that the
account does not yet bid on, how big each search is and how it moves through the year, what the competitors' sites are found for, what the account already
covers and what its negative keywords already block, grouped into themes a campaign can be built on, with the negatives to add and the questions to ask.

This file is the plain part: what the page may ask for (the research cards and the inputs) and what a build's steps are. No network and no database.
It shares its machinery with Strategy & Campaign Book and Business Research (tracker/paid_strategy*.py, paid_business*.py): the same reading of a website,
of competitors and of Google Ads, the same background job and the same table of saved builds (a Keyword Research build is the one whose inputs say kind
"keywords"). What is new is paid_keywords_research.py (Keyword Planner and the account's keywords), paid_keywords_data.py (the arithmetic: what is already
covered, blocked, how it moves through the year), paid_keywords_ai.py (the judging), paid_keywords_check.py (what of it is kept),
paid_keywords_export.py (the downloads) and paid_keywords_job.py (the run).
"""
from __future__ import annotations

import re

from . import paid_strategy as ps

KIND = "keywords"
BUILDS_PER_DAY = ps.BUILDS_PER_DAY
RIVALS_READ = 3                      # every competitor named is read (tracker/paid_strategy_research.read_competitors), so no more than this can be named
MAX_COMPETITORS = RIVALS_READ
MIN_SEARCHES = (10, 50, 100, 500)    # the smallest monthly search volume a keyword idea needs to be looked at
DEFAULT_MIN = 10

CARDS = [
    {"key": "business", "title": "Business Research of this client", "needs": None, "paid": False, "default": True,
     "blurb": "Starts from the latest finished Business Research for this client: what it sells, who buys, and what they search. Skipped if there is none."},
    {"key": "website", "title": "Client website", "needs": "website", "paid": False, "default": True,
     "blurb": "Reads the home page and up to a dozen more: the services and headings the site uses become starting points for Keyword Planner."},
    {"key": "account", "title": "Account keywords and search terms", "needs": "account", "paid": False, "default": True,
     "blurb": "Every keyword the account has, its campaign-level and shared-list negative keywords (an ad group's own negatives are not read), and 90 days of search terms, so nothing is suggested that the account already bids on or blocks, and what converts is not missed."},
    {"key": "competitors", "title": "Competitors", "needs": None, "paid": False, "default": True,
     "blurb": "Up to three competitor websites (the ones you name, or found from the web): what Keyword Planner finds each of them for."},
]
CARD_KEYS = [c["key"] for c in CARDS]

RESEARCH_LABELS = {
    "business": "Business Research of this client", "website": "Reading the client's website", "account": "The account's keywords, negatives and search terms",
    "competitors": "Finding and reading the competitors' websites", "planner": "Asking Google Keyword Planner",
}
AI_STEPS = [("themes", "Grouping the market into themes"), ("classify", "Judging each keyword: fit, intent and match type"), ("plan", "The plan: quick wins, negatives, tests and questions")]


def clean_inputs(raw):
    """What the page may ask for, checked. Raises ps.BadPayload with a sentence. Returns the normalised inputs (kind 'keywords')."""
    if not isinstance(raw, dict):
        raise ps.BadPayload("Send the client's details as a form.")
    name = ps._s(raw.get("name"), 120)
    if len(name) < 2:
        raise ps.BadPayload("Give the business or client a name.")
    website = ps.clean_url(raw.get("website"))
    account = re.sub(r"\D", "", str(raw.get("account") or ""))
    if account and not ps.CUSTOMER_ID.match(account):
        raise ps.BadPayload("A Google Ads account id has ten digits.")
    services = ps._s(raw.get("services"), 800)
    if not (services or website or account):
        raise ps.BadPayload("Give what the client sells, a website or a Google Ads account, so Keyword Planner has something to start from.")
    goal = str(raw.get("goal") or "leads").strip().lower()
    if goal not in ps.GOALS:
        raise ps.BadPayload("The goal has to be one of: %s." % ", ".join(ps.GOALS))
    try:
        floor = int(raw.get("minSearches") if raw.get("minSearches") not in (None, "") else DEFAULT_MIN)
    except (TypeError, ValueError):
        raise ps.BadPayload("The smallest monthly search volume has to be one of: %s." % ", ".join(str(n) for n in MIN_SEARCHES))
    if floor not in MIN_SEARCHES:
        raise ps.BadPayload("The smallest monthly search volume has to be one of: %s." % ", ".join(str(n) for n in MIN_SEARCHES))
    items = raw.get("competitors") or []
    if isinstance(items, str):
        items = re.split(r"[\n,;]+", items)
    if not isinstance(items, list):
        raise ps.BadPayload("Competitors should be a list.")
    competitors, seen = [], set()
    for item in items:
        c = ps._competitor(item)
        key = ps.host_of((c or {}).get("url")) or ((c or {}).get("name") or "").lower()
        if c and key not in seen:
            seen.add(key)
            competitors.append(c)
    if len(competitors) > MAX_COMPETITORS:
        raise ps.BadPayload("Name at most %d competitors." % MAX_COMPETITORS)
    asked = raw.get("research")
    asked = asked if isinstance(asked, dict) else {}
    research = {}
    for card in CARDS:
        on = bool(asked.get(card["key"], card["default"]))
        if (card["needs"] == "website" and not website) or (card["needs"] == "account" and not account):
            on = False
        research[card["key"]] = on
    return {"kind": KIND, "name": name, "website": website, "account": account, "industry": ps._s(raw.get("industry"), 120), "goal": goal,
            "budget": None, "currency": re.sub(r"[^A-Za-z]", "", str(raw.get("currency") or "USD")).upper()[:3] or "USD",
            "locations": ps._s(raw.get("locations"), 500), "services": services, "brief": ps._long(raw.get("brief"), 3000),
            "avoid": ps._s(raw.get("avoid"), 600), "minSearches": floor, "competitors": competitors, "campaignTypes": [], "research": research}


def steps_for(inputs):
    """The build's steps, in order, as the page shows them. Keyword Planner is always asked."""
    steps = []
    for key in CARD_KEYS:
        if inputs["research"].get(key):
            steps.append({"key": "r:" + key, "label": RESEARCH_LABELS[key], "state": "pending", "note": ""})
    steps.append({"key": "r:planner", "label": RESEARCH_LABELS["planner"], "state": "pending", "note": ""})
    for key, label in AI_STEPS:
        steps.append({"key": "a:" + key, "label": label, "state": "pending", "note": ""})
    steps.append({"key": "f:finish", "label": "Checking and putting the lists together", "state": "pending", "note": ""})
    return steps


def phrases(text):
    """Phrases from a free-text field: split on commas, semicolons, new lines and bars, no empties, no repeats, at most 20."""
    out = []
    for p in re.split(r"[;,\n|]+", str(text or "")):
        p = " ".join(p.split())
        if p and p.lower() not in [x.lower() for x in out]:
            out.append(p)
    return out[:20]


def is_keywords(rec):
    return ((rec or {}).get("inputs") or {}).get("kind") == KIND


def brief(rec):
    """One row of the list of saved builds."""
    inputs = rec.get("inputs") or {}
    return {"id": rec["id"], "name": rec["name"], "status": rec["status"], "createdBy": rec.get("created_by"), "createdAt": rec.get("created_at"),
            "accountName": rec.get("account_name") or "", "website": inputs.get("website") or "", "industry": inputs.get("industry") or "",
            "stale": bool(rec.get("stale")), "cost": (rec.get("cost") or {}).get("usd")}
