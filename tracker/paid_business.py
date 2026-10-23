"""Paid Marketing Agents: Business Research, what a build is and what it is asked for.

The agent turns a client (a website, a brief, optionally a Google Ads account) into a written picture of the business before any audit or strategy is
done: what it sells and to whom, what sets it apart and what proves it, how people become customers, what the rules and the reputation are, what the
website is set up to measure, what that means for paid media, and what still has to be asked of the client.

This file is the plain part: what the page may ask for (the research cards and the inputs) and what a build's steps are. No network and no database.
It shares its machinery with Strategy & Campaign Book (tracker/paid_strategy*.py): the same reading of a website, of competitors, of Google Ads and of a
Social Media Intelligence run, the same background job and the same table of saved builds (a Business Research build is the one whose inputs say
kind "business"). What is new is tracker/paid_business_research.py (reputation and the site's tracking), paid_business_ai.py (the writing),
paid_business_check.py (what of the writing is kept), paid_business_export.py (the downloads) and paid_business_job.py (the run).
"""
from __future__ import annotations

import re

from . import paid_strategy as ps

KIND = "business"
BUILDS_PER_DAY = ps.BUILDS_PER_DAY
RIVALS_READ = 3                      # how many competitors' sites are read: enough to say where the client stands, not a competitor study (that is its own agent)
MAX_COMPETITORS = RIVALS_READ        # every competitor named is read (tracker/paid_strategy_research.read_competitors), so no more than that can be named

CARDS = [
    {"key": "website", "title": "Client website", "needs": "website", "paid": False, "default": True,
     "blurb": "Reads the home page and up to a dozen more: services, prices, offers, proof, how it asks for the sale, and what the site is set up to measure (analytics, ad tags, call tracking, booking)."},
    {"key": "reputation", "title": "Reviews and reputation", "needs": None, "paid": True, "default": True,
     "blurb": "A web search for what customers say: ratings on the review sites, what they praise and complain about, press, awards, who runs the business and how big it is."},
    {"key": "social", "title": "Social Media Intelligence", "needs": None, "paid": True, "default": True,
     "blurb": "What the brand posts on social media on its own: uses your latest finished Social Media Intelligence run for this company, or starts one."},
    {"key": "history", "title": "Google Ads history", "needs": "account", "paid": False, "default": True,
     "blurb": "What the account has already paid for and what converted, which shows what the business really sells through ads and what people ask for."},
    {"key": "competitors", "title": "Competitors", "needs": None, "paid": False, "default": True,
     "blurb": "Reads the sites of up to three competitors (the ones you name, or found from the web) to show where the client is the same and where it stands apart."},
    {"key": "market", "title": "Market and ad rules", "needs": None, "paid": True, "default": True,
     "blurb": "A web search for how people choose in this market, what moves demand, and the advertising rules the industry must follow."},
]
CARD_KEYS = [c["key"] for c in CARDS]

RESEARCH_LABELS = {
    "website": "Reading the client's website and what it measures", "reputation": "Reviews, press and who is behind the business",
    "social": "Social Media Intelligence: what the brand posts on its own", "history": "Reading the Google Ads account",
    "competitors": "Finding and reading the competitors' websites", "market": "The market, its trends and its advertising rules",
}
AI_STEPS = [("core", "The business: what it sells, what sets it apart, what proves it"), ("audience", "Who buys, why, and how they decide"),
            ("implications", "What it means for paid media, and what to ask the client")]


def clean_inputs(raw):
    """What the page may ask for, checked. Raises ps.BadPayload with a sentence. Returns the normalised inputs (kind 'business')."""
    if not isinstance(raw, dict):
        raise ps.BadPayload("Send the client's details as a form.")
    name = ps._s(raw.get("name"), 120)
    if len(name) < 2:
        raise ps.BadPayload("Give the business or client a name.")
    website = ps.clean_url(raw.get("website"))
    brief = ps._long(raw.get("brief"), ps.MAX_BRIEF)
    account = re.sub(r"\D", "", str(raw.get("account") or ""))
    if account and not ps.CUSTOMER_ID.match(account):
        raise ps.BadPayload("A Google Ads account id has ten digits.")
    if not website and not account and len(brief) < 40:
        raise ps.BadPayload("Give a website, a Google Ads account, or a client brief of a few sentences, so there is something to work from.")
    goal = str(raw.get("goal") or "leads").strip().lower()
    if goal not in ps.GOALS:
        raise ps.BadPayload("The goal has to be one of: %s." % ", ".join(ps.GOALS))
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
            "locations": ps._s(raw.get("locations"), 500), "services": ps._s(raw.get("services"), 800), "brief": brief,
            "avoid": ps._s(raw.get("avoid"), 600), "competitors": competitors, "campaignTypes": [], "research": research}


def steps_for(inputs):
    """The build's steps, in order, as the page shows them."""
    steps = []
    for key in CARD_KEYS:
        if inputs["research"].get(key):
            steps.append({"key": "r:" + key, "label": RESEARCH_LABELS[key], "state": "pending", "note": ""})
    for key, label in AI_STEPS:
        steps.append({"key": "a:" + key, "label": label, "state": "pending", "note": ""})
    steps.append({"key": "f:finish", "label": "Checking the facts and writing the report", "state": "pending", "note": ""})
    return steps


def is_business(rec):
    return ((rec or {}).get("inputs") or {}).get("kind") == KIND


def brief(rec):
    """One row of the list of saved builds."""
    inputs = rec.get("inputs") or {}
    return {"id": rec["id"], "name": rec["name"], "status": rec["status"], "createdBy": rec.get("created_by"), "createdAt": rec.get("created_at"),
            "accountName": rec.get("account_name") or "", "website": inputs.get("website") or "", "industry": inputs.get("industry") or "",
            "stale": bool(rec.get("stale")), "cost": (rec.get("cost") or {}).get("usd")}
