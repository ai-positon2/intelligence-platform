"""Paid Marketing Agents: Strategy & Campaign Book, what a build is and what it is asked for.

The agent turns a client (a website, a brief, optionally a Google Ads account) into a written strategy and a campaign book: how the
money should be spent, which campaigns to build, and for each campaign its ad groups, keywords, negatives, ad copy and assets, plus the
campaign settings in the layout the QA Agent checks a live campaign against (Field | Value | Other things to check).

This file is the plain part: what the page may ask for (the research cards and the inputs), what a build's steps are, how a campaign
is named and how a budget is split. No network and no database here. Where the facts come from is tracker/paid_strategy_research.py,
the writing is tracker/paid_strategy_ai.py, what the writing is held to is tracker/paid_strategy_check.py, the book rows are
tracker/paid_strategy_book.py, the downloads are tracker/paid_strategy_export.py, what is saved is tracker/paid_strategy_store.py and the
background run is tracker/paid_strategy_job.py.
"""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlsplit

# ── What the page offers ────────────────────────────────────────────────────

# The research cards at the top of the page. `needs` is what must be given for a card to work; `paid` says the card spends money on
# an outside service beyond the model (so the page can say so); `blurb` is what the card says it adds.
CARDS = [
    {"key": "website", "title": "Client website", "needs": "website", "paid": False, "default": True,
     "blurb": "Reads the home page and up to a dozen more: services, prices, offers, proof, phone and forms, and how each page asks for the sale."},
    {"key": "competitors", "title": "Competitor websites", "needs": None, "paid": False, "default": True,
     "blurb": "Reads the websites of the competitors you name, or finds them from the web, and compares positioning, offers and messages."},
    {"key": "social", "title": "Social Media Intelligence", "needs": None, "paid": True, "default": True,
     "blurb": "What the brand posts on social media on its own: uses your latest finished Social Media Intelligence run for this company, or starts one."},
    {"key": "history", "title": "Google Ads history", "needs": "account", "paid": False, "default": True,
     "blurb": "What the account has already paid for: campaigns, keywords and search terms that converted, devices, places, conversion actions and live ads."},
    {"key": "keywords", "title": "Keyword research", "needs": None, "paid": False, "default": True,
     "blurb": "Google Keyword Planner: monthly searches, competition and bid ranges for ideas from the site and the competitors."},
    {"key": "seasonality", "title": "Seasonality", "needs": "account", "paid": False, "default": True,
     "blurb": "When the account's own demand rises and falls through the year, so the budget can follow it."},
    {"key": "market", "title": "Market, trends and ad policy", "needs": None, "paid": True, "default": True,
     "blurb": "A web search for how people choose in this market, what moves demand, and the advertising rules the industry must follow."},
]
CARD_KEYS = [c["key"] for c in CARDS]

GOALS = {"leads": "Leads (forms and enquiries)", "calls": "Phone calls", "bookings": "Bookings and appointments",
         "sales": "Online sales", "awareness": "Awareness and reach"}

# The kinds of campaign the strategist may plan. `qa` is the QA Agent's name for the type (its checklist), `code` the word in the name.
KINDS = {
    "search": {"label": "Search", "qa": "Search", "code": "Search"},
    "pmax": {"label": "Performance Max", "qa": "PMax", "code": "PMax"},
    "demandgen": {"label": "Demand Gen", "qa": "Demand Gen", "code": "DemandGen"},
    "display": {"label": "Display", "qa": "Display", "code": "Display"},
    "video": {"label": "Video (YouTube)", "qa": "Video", "code": "Video"},
}
KIND_KEYS = list(KINDS)

MAX_COMPETITORS = 8
MAX_BRIEF = 8000
CUSTOMER_ID = re.compile(r"^\d{10}$")
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def _today():
    return dt.date.today()


class BadPayload(ValueError):
    """What is wrong with what the page sent, in words the page can show."""


def _s(v, n):
    return " ".join(str(v or "").split())[:n]


def _long(v, n):
    """Free text that may run over several lines: lines are kept, runs of spaces are not."""
    out = [" ".join(line.split()) for line in str(v or "").replace("\r", "").split("\n")]
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:n]


def clean_url(raw, what="website"):
    """https://host/path from what a person types ("example.com", "http://www.example.com/x?y#z"). Query and fragment are dropped."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:                                      # a bracketed host that never closes, a port that is not a number
        raise BadPayload("That is not a web address I can read as the %s: %s" % (what, _s(raw, 80)))
    if parts.scheme.lower() not in ("http", "https") or not DOMAIN.match(host) or parts.username or parts.password:
        raise BadPayload("That is not a web address I can read as the %s: %s" % (what, _s(raw, 80)))
    if port not in (None, 80, 443):
        raise BadPayload("The %s has to use the normal web ports." % what)
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return "https://%s%s" % (host, path if path != "/" else "/")


def host_of(url):
    return (urlsplit(str(url or "")).hostname or "").lower().removeprefix("www.")


def _competitor(raw):
    """A competitor given as a web address or as a name. Returns {'name', 'url'}."""
    text = _s(raw, 200)
    if not text:
        return None
    if re.search(r"\.[a-z]{2,24}(/|$|\s)", text, re.I) and " " not in text.strip():
        try:
            url = clean_url(text, "competitor website")
        except BadPayload:
            return {"name": text, "url": ""}
        return {"name": host_of(url), "url": url}
    return {"name": text, "url": ""}


def clean_inputs(raw, *, today=None):
    """What the page may ask for, checked. Raises BadPayload with a sentence. Returns the normalised inputs."""
    if not isinstance(raw, dict):
        raise BadPayload("Send the client's details as a form.")
    today = today or _today()
    name = _s(raw.get("name"), 120)
    if len(name) < 2:
        raise BadPayload("Give the business or client a name.")
    website = clean_url(raw.get("website"))
    brief = _long(raw.get("brief"), MAX_BRIEF)
    account = re.sub(r"\D", "", str(raw.get("account") or ""))
    if account and not CUSTOMER_ID.match(account):
        raise BadPayload("A Google Ads account id has ten digits.")
    if not website and not account and len(brief) < 40:
        raise BadPayload("Give a website, a Google Ads account, or a client brief of a few sentences, so there is something to work from.")
    goal = str(raw.get("goal") or "leads").strip().lower()
    if goal not in GOALS:
        raise BadPayload("The goal has to be one of: %s." % ", ".join(GOALS))
    budget = raw.get("budget")
    if budget in (None, ""):
        budget = None
    else:
        try:
            budget = round(float(str(budget).replace(",", "")), 2)
        except ValueError:
            raise BadPayload("The monthly budget should be a number.")
        if not 0 < budget <= 10_000_000:
            raise BadPayload("The monthly budget should be more than 0 and no more than 10,000,000.")
    currency = re.sub(r"[^A-Za-z]", "", str(raw.get("currency") or "USD")).upper()[:3] or "USD"
    launch = str(raw.get("launch") or "").strip()
    if launch:
        try:
            when = dt.date.fromisoformat(launch)
        except ValueError:
            raise BadPayload("The launch date should look like 2026-11-30.")
        if not today - dt.timedelta(days=1) <= when <= today + dt.timedelta(days=400):
            raise BadPayload("The launch date should be from today to within the next 13 months.")
    else:
        when = today + dt.timedelta(days=14)
    competitors, seen = [], set()
    items = raw.get("competitors") or []
    if isinstance(items, str):
        items = re.split(r"[\n,;]+", items)
    if not isinstance(items, list):
        raise BadPayload("Competitors should be a list.")
    for item in items:
        c = _competitor(item)
        key = host_of((c or {}).get("url")) or ((c or {}).get("name") or "").lower()
        if c and key not in seen:
            seen.add(key)
            competitors.append(c)
    if len(competitors) > MAX_COMPETITORS:
        raise BadPayload("Name at most %d competitors." % MAX_COMPETITORS)
    kinds = raw.get("campaignTypes") or []
    if not isinstance(kinds, list) or any(k not in KINDS for k in kinds):
        raise BadPayload("The campaign types have to be from: %s." % ", ".join(KIND_KEYS))
    kinds = [k for k in KIND_KEYS if k in kinds]
    asked = raw.get("research")
    asked = asked if isinstance(asked, dict) else {}
    research = {}
    for card in CARDS:
        on = bool(asked.get(card["key"], card["default"]))
        if card["needs"] == "website" and not website:
            on = False
        if card["needs"] == "account" and not account:
            on = False
        research[card["key"]] = on
    return {"name": name, "website": website, "account": account, "industry": _s(raw.get("industry"), 120), "goal": goal,
            "budget": budget, "currency": currency, "launch": when.isoformat(), "locations": _s(raw.get("locations"), 500),
            "services": _s(raw.get("services"), 800), "brief": brief, "avoid": _s(raw.get("avoid"), 600),
            "competitors": competitors, "campaignTypes": kinds, "research": research}


# ── What a build does, in order ─────────────────────────────────────────────

RESEARCH_LABELS = {
    "website": "Reading the client's website", "competitors": "Finding and reading the competitors' websites",
    "social": "Social Media Intelligence: what the brand posts on its own", "history": "Reading the Google Ads account",
    "keywords": "Keyword research in Google Keyword Planner", "seasonality": "Seasonality of the account",
    "market": "The market, its trends and its advertising rules",
}
AI_STEPS = [("profile", "Understanding the business"), ("competitive", "Comparing the competitors"),
            ("strategy", "Writing the strategy"), ("architecture", "Designing the campaigns")]


def steps_for(inputs):
    """The build's steps, in order, as the page shows them. Each build of a campaign is added when the campaigns are known."""
    steps = []
    for key in CARD_KEYS:
        if inputs["research"].get(key):
            steps.append({"key": "r:" + key, "label": RESEARCH_LABELS[key], "state": "pending", "note": ""})
    for key, label in AI_STEPS:
        steps.append({"key": "a:" + key, "label": label, "state": "pending", "note": ""})
    steps.append({"key": "f:finish", "label": "Checking the plan and writing the Campaign Book", "state": "pending", "note": ""})
    return steps


def with_builds(steps, campaigns):
    """The steps with one 'build this campaign' step per campaign, just before the finish."""
    kept = [s for s in steps if not s["key"].startswith("b:")]
    at = next((i for i, s in enumerate(kept) if s["key"] == "f:finish"), len(kept))
    builds = [{"key": "b:" + c["key"], "label": "Building %s" % c["name"], "state": "pending", "note": ""} for c in campaigns]
    return kept[:at] + builds + kept[at:]


# ── Names and money ─────────────────────────────────────────────────────────

def slug(text, n=24):
    """CamelCase letters and digits only, for a campaign name: 'Implants & Veneers' -> 'ImplantsVeneers'."""
    words = re.findall(r"[A-Za-z0-9]+", str(text or ""))
    return "".join(w[:1].upper() + w[1:] for w in words)[:n]


def campaign_name(kind, theme, launch):
    """P2_<Search|PMax|...>_<Theme>_<launch date>: the team's name format (the QA Agent's first check), with the theme added."""
    code = KINDS[kind]["code"]
    day = str(launch or "").replace("-", "")
    theme = slug(theme)
    return "P2_%s_%s_%s" % (code, theme, day) if theme else "P2_%s_%s" % (code, day)


def daily_from_monthly(monthly):
    """Google Ads charges up to 2 times the daily budget on a day but averages 30.4 days a month."""
    return round(float(monthly) / 30.4, 2) if monthly else None


def split_budget(monthly, shares, floor=0):
    """Daily budgets for campaigns from their shares (any positive numbers). Rounded to cents, never below `floor`.
    The cents lost to rounding stay with the largest campaign so the total still equals monthly / 30.4."""
    total = sum(max(0.0, float(s or 0)) for s in shares)
    if not monthly or total <= 0:
        return [None for _ in shares]
    day = float(monthly) / 30.4
    floor = min(floor, day / len(shares))                   # a floor must never spend more than the budget has
    out = [max(floor, round(day * max(0.0, float(s or 0)) / total, 2)) for s in shares]
    drift = round(round(day, 2) - sum(out), 2)
    if drift and out and abs(drift) < 0.5 * max(1, len(out)):
        big = max(range(len(out)), key=lambda i: out[i])
        out[big] = round(out[big] + drift, 2)
    return out


# ── Reading a saved build for the page ──────────────────────────────────────

STALE_AFTER_S = 240         # a build whose heartbeat has been silent this long died with its process (a deploy, a crash); the run beats every HEARTBEAT_S
HEARTBEAT_S = 30
BUILDS_PER_DAY = 12         # per person; a build spends model credits and outside data


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def scrub(obj):
    """The same data with the characters no spreadsheet cell, no Postgres jsonb and no PDF can hold taken out of every string (a NUL in a web page
    would otherwise make a whole build unsaveable)."""
    if isinstance(obj, str):
        return _CONTROL.sub("", obj)
    if isinstance(obj, dict):
        return {(_CONTROL.sub("", k) if isinstance(k, str) else k): scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    return obj


def public(rec):
    """What the page is told about one build: everything it draws, none of the raw page text the research read."""
    if not rec:
        return None
    from . import paid_strategy_research as research
    return {"id": rec["id"], "name": rec["name"], "status": rec["status"], "error": rec.get("error") or "",
            "note": rec.get("note") or "", "createdBy": rec.get("created_by"), "createdAt": rec.get("created_at"),
            "updatedAt": rec.get("updated_at"), "accountId": rec.get("account_id") or "", "accountName": rec.get("account_name") or "",
            "stale": bool(rec.get("stale")), "inputs": rec.get("inputs") or {}, "progress": rec.get("progress") or {}, "research": research.public(rec.get("research") or {}),
            "plan": rec.get("plan") or {}, "cost": rec.get("cost") or {}}


def brief(rec):
    """One row of the list of saved builds."""
    plan = rec.get("plan") or {}
    inputs = rec.get("inputs") or {}
    return {"id": rec["id"], "name": rec["name"], "status": rec["status"], "createdBy": rec.get("created_by"),
            "createdAt": rec.get("created_at"), "accountName": rec.get("account_name") or "",
            "website": inputs.get("website") or "", "goal": inputs.get("goal") or "",
            "stale": bool(rec.get("stale")), "campaigns": rec.get("campaign_count", len(plan.get("campaigns") or [])), "cost": (rec.get("cost") or {}).get("usd")}
