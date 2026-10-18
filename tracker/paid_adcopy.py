"""Paid Marketing Agents: Ad Copy & Creative Generator, what a build is and what it is asked for.

The agent writes ad copy that is ready to paste (Google Search, Performance Max, Demand Gen and Responsive Display, Meta feed and Meta Stories and Reels) and the
creative briefs for the formats that need an image or a video. It writes only from what it is given: the client's website, the facts the person confirmed with the
client, and optionally the keywords and the best existing ads of a Google Ads account. What is kept of the writing is decided in code
(tracker/paid_adcopy_check.py), never by the model.

This file is the plain part: the formats and their limits (one table, `FORMATS`, that the prompts, the checks, the downloads and the page all read), what the page may
ask for, and what a build's steps are. No network and no database. It shares its machinery with Strategy & Campaign Book, Business Research and Keyword Research
(tracker/paid_strategy*.py): the same background job, the same table of saved builds (an Ad Copy build is the one whose inputs say kind "adcopy") and the same reading of
a website.
"""
from __future__ import annotations

import re

from . import paid_strategy as ps

KIND = "adcopy"
BUILDS_PER_DAY = ps.BUILDS_PER_DAY
MAX_KEYWORDS = 20
MAX_ANGLES = 5
MAX_FACTS = 1500

# Meta's lengths are the ones Meta recommends (it cuts the text off after them), as published; Google's are Google Ads' own hard limits. One place to change them.
META_PRIMARY, META_HEADLINE, META_DESCRIPTION = 125, 40, 30


def _field(key, label, limit, lo, hi, *, cased=False, optional=False):
    """One piece of text a format holds: `limit` characters at most, between `lo` and `hi` of them (`lo` is what a build must keep to be called complete)."""
    return {"key": key, "label": label, "limit": limit, "min": lo, "max": hi, "cased": cased, "optional": optional}


FORMATS = {
    "google_rsa": {"label": "Google Search ad (Responsive Search Ad)", "short": "Google Search", "platform": "google",
                   "fields": [_field("headlines", "Headlines", 30, 3, 15, cased=True), _field("descriptions", "Descriptions", 90, 2, 4)],
                   "paths": 15, "cta": None, "brief": False,
                   "about": "15 headlines up to 30 characters and 4 descriptions up to 90, written to be mixed by Google. Headlines are in Start Case."},
    "google_pmax": {"label": "Google Performance Max asset group", "short": "Performance Max", "platform": "google",
                    "fields": [_field("headlines", "Headlines", 30, 3, 15, cased=True), _field("long_headlines", "Long headlines", 90, 1, 5),
                              _field("descriptions", "Descriptions", 90, 2, 5), _field("short_description", "Short description", 60, 1, 1)],
                    "paths": 0, "cta": "google", "brief": False,
                    "about": "Headlines up to 30, long headlines up to 90, descriptions up to 90 and the one description Google requires to be 60 or fewer."},
    "google_demand_gen": {"label": "Google Demand Gen ad", "short": "Demand Gen", "platform": "google",
                          "fields": [_field("headlines", "Headlines", 40, 3, 5, cased=True), _field("descriptions", "Descriptions", 90, 2, 5)],
                          "paths": 0, "cta": "google", "brief": True,
                          "about": "Headlines up to 40 and descriptions up to 90, with a creative brief for the image or video."},
    "google_display": {"label": "Google Responsive Display ad", "short": "Responsive Display", "platform": "google",
                       "fields": [_field("headlines", "Short headlines", 30, 2, 5, cased=True), _field("long_headlines", "Long headline", 90, 1, 1),
                                 _field("descriptions", "Descriptions", 90, 2, 5)],
                       "paths": 0, "cta": "google", "brief": True,
                       "about": "Short headlines up to 30, one long headline up to 90 and descriptions up to 90, with a creative brief for the images."},
    "meta_feed": {"label": "Meta feed ad (Facebook and Instagram)", "short": "Meta feed", "platform": "meta",
                  "fields": [_field("primary_texts", "Primary texts", META_PRIMARY, 2, 5), _field("headlines", "Headlines", META_HEADLINE, 2, 5),
                            _field("descriptions", "Link descriptions", META_DESCRIPTION, 0, 3, optional=True)],
                  "paths": 0, "cta": "meta", "brief": True,
                  "about": "Primary texts up to 125 characters (the part shown before 'See more'), headlines up to 40 and link descriptions up to 30, with a creative brief."},
    "meta_stories_reels": {"label": "Meta Stories and Reels ad", "short": "Meta Stories and Reels", "platform": "meta",
                           "fields": [_field("primary_texts", "Primary texts", META_PRIMARY, 2, 4), _field("headlines", "Headlines", META_HEADLINE, 2, 4)],
                           "paths": 0, "cta": "meta", "brief": True,
                           "about": "Short primary texts and headlines for a full-screen vertical ad, with a creative brief that opens with the hook and gives a script."},
}
FORMAT_KEYS = list(FORMATS)
DEFAULT_FORMATS = ["google_rsa", "meta_feed"]

# Image and video sizes the platforms publish; shown in the brief, never asked of the model. Check them against the platform before a brief goes to a designer.
SPECS = {
    "google_demand_gen": [{"asset": "Image", "size": "1200 x 628 (1.91:1), 1200 x 1200 (1:1) and 960 x 1200 (4:5)", "note": "At least one landscape and one square."},
                          {"asset": "Video", "size": "16:9, 1:1 or 9:16", "note": "Vertical and square work best on mobile."},
                          {"asset": "Logo", "size": "1200 x 1200 (1:1)", "note": "The business name and logo show with the ad."}],
    "google_display": [{"asset": "Landscape image", "size": "1200 x 628 (1.91:1)", "note": "Required."},
                       {"asset": "Square image", "size": "1200 x 1200 (1:1)", "note": "Required."},
                       {"asset": "Logo", "size": "1200 x 1200 (1:1), and 1200 x 300 (4:1) if there is a wide one", "note": "Keep the artwork clear of the edges."}],
    "meta_feed": [{"asset": "Image", "size": "1080 x 1080 (1:1) and 1080 x 1350 (4:5)", "note": "4:5 takes the most space in the feed."},
                  {"asset": "Video", "size": "1080 x 1350 (4:5) or 1080 x 1080 (1:1)", "note": "Make the first seconds carry the message: many people watch without sound."}],
    "meta_stories_reels": [{"asset": "Video or image", "size": "1080 x 1920 (9:16)", "note": "Keep text and logos clear of the top and the bottom of the frame, where the app puts its own buttons and captions."}],
}

CTAS = {"google": ["Learn more", "Get quote", "Apply now", "Sign up", "Contact us", "Subscribe", "Download", "Book now", "Shop now"],
        "meta": ["Learn more", "Shop now", "Sign up", "Book now", "Contact us", "Get quote", "Apply now", "Download", "Call now", "Get offer", "Subscribe", "Send message"]}

ANGLES = {"benefit": "What the customer gets", "problem": "The problem the customer has", "proof": "Reviews, years or credentials the client can prove",
          "offer": "A real offer or price the client confirmed", "urgency": "A real reason to act now", "question": "A question the customer asks",
          "local": "Where the client is, near me, the area served", "action": "A plain call to action"}
DEFAULT_ANGLES = ["benefit", "problem", "proof", "action"]
NEEDS_FACTS = {"proof": "proof", "offer": "an offer", "urgency": "a deadline or limit"}      # angles the writing may use only when the evidence holds the thing

TONES = {"plain": "Plain and direct", "professional": "Professional and calm", "friendly": "Warm and friendly", "premium": "Premium and understated",
         "urgent": "Energetic (never a made-up deadline)"}
DEFAULT_TONE = "plain"

CARDS = [
    {"key": "website", "title": "Client website", "needs": "website", "paid": False, "default": True,
     "blurb": "Reads the home page and up to a dozen more: what it sells, how it talks, the offers and proof it states. A claim in the copy is allowed only if it is on these pages or in the facts you give."},
    {"key": "account", "title": "Google Ads account", "needs": "account", "paid": False, "default": True,
     "blurb": "Reads the account's keywords and its best responsive search ads, so the copy uses the words that earn clicks and does not repeat what already runs."},
]
CARD_KEYS = [c["key"] for c in CARDS]
RESEARCH_LABELS = {"website": "Reading the client's website", "account": "The account's keywords and best ads"}


def lines_of(text, n=20, each=300):
    """A free-text field cut into items on new lines, semicolons and bars: no empties, no repeats (ignoring case), at most `n`."""
    out = []
    for p in re.split(r"[;\n|]+", str(text or "")):
        p = " ".join(p.split())[:each]
        if p and p.lower() not in [x.lower() for x in out]:
            out.append(p)
    return out[:n]


def phrases(text, n=MAX_KEYWORDS):
    """The same, also splitting on commas (a list of keywords or of words to avoid)."""
    out = []
    for p in re.split(r"[;,\n|]+", str(text or "")):
        p = " ".join(p.split())[:80]
        if p and p.lower() not in [x.lower() for x in out]:
            out.append(p)
    return out[:n]


def _as_list(v):
    if isinstance(v, str):
        return [v]
    return v if isinstance(v, list) else []


def clean_inputs(raw):
    """What the page may ask for, checked. Raises ps.BadPayload with a sentence. Returns the normalised inputs (kind 'adcopy')."""
    if not isinstance(raw, dict):
        raise ps.BadPayload("Send the details as a form.")
    name = ps._s(raw.get("name"), 120)
    if len(name) < 2:
        raise ps.BadPayload("Give the business or client a name.")
    product = ps._long(raw.get("product"), 800)
    if len(product) < 3:
        raise ps.BadPayload("Say what the ads are for: the service, product or offer to promote.")
    website = ps.clean_url(raw.get("website"))
    account_raw = str(raw.get("account") or "").strip()
    if account_raw.lower().startswith("act_") or "+" in account_raw:
        raise ps.BadPayload("Only a Google Ads account can be read for this. Leave the account empty for a Meta client and give the facts below.")
    account = re.sub(r"\D", "", account_raw)
    if account and not ps.CUSTOMER_ID.match(account):
        raise ps.BadPayload("A Google Ads account id has ten digits.")
    goal = str(raw.get("goal") or "leads").strip().lower()
    if goal not in ps.GOALS:
        raise ps.BadPayload("The goal has to be one of: %s." % ", ".join(ps.GOALS))
    tone = str(raw.get("tone") or DEFAULT_TONE).strip().lower()
    if tone not in TONES:
        raise ps.BadPayload("The tone has to be one of: %s." % ", ".join(TONES))
    formats = []
    for f in _as_list(raw.get("formats")) or DEFAULT_FORMATS:
        f = str(f).strip()
        if f not in FORMATS:
            raise ps.BadPayload("Unknown ad format: %s." % ps._s(f, 40))
        if f not in formats:
            formats.append(f)
    if not formats:
        raise ps.BadPayload("Pick at least one ad format.")
    angles = []
    for a in _as_list(raw.get("angles")) or DEFAULT_ANGLES:
        a = str(a).strip()
        if a not in ANGLES:
            raise ps.BadPayload("Unknown angle: %s." % ps._s(a, 40))
        if a not in angles:
            angles.append(a)
    if not angles:
        raise ps.BadPayload("Pick at least one angle.")
    if len(angles) > MAX_ANGLES:
        raise ps.BadPayload("Pick at most %d angles." % MAX_ANGLES)
    asked = raw.get("research")
    asked = asked if isinstance(asked, dict) else {}
    research = {}
    for card in CARDS:
        on = bool(asked.get(card["key"], card["default"]))
        if (card["needs"] == "website" and not website) or (card["needs"] == "account" and not account):
            on = False
        research[card["key"]] = on
    rivals = phrases(raw.get("competitors"), 8) if not isinstance(raw.get("competitors"), list) else [ps._s(x, 80) for x in raw["competitors"] if ps._s(x, 80)][:8]
    return {"kind": KIND, "name": name, "website": website, "account": account, "industry": ps._s(raw.get("industry"), 120), "goal": goal,
            "budget": None, "currency": "USD", "locations": ps._s(raw.get("locations"), 500), "services": product, "product": product, "brief": "",
            "audience": ps._long(raw.get("audience"), 600), "tone": tone, "angles": angles, "keywords": phrases(raw.get("keywords")), "facts": ps._long(raw.get("facts"), MAX_FACTS),
            "avoid": ps._s(raw.get("avoid"), 600), "competitors": [{"name": c, "url": ""} for c in rivals], "campaignName": ps._s(raw.get("campaignName"), 80),
            "adGroupName": ps._s(raw.get("adGroupName"), 80), "language": ps._s(raw.get("language"), 40) or "English", "formats": formats,
            "briefs": bool(raw.get("briefs", True)), "campaignTypes": [], "research": research}


def steps_for(inputs):
    """The build's steps, in order, as the page shows them: what is read, one writing step per format, the checks."""
    steps = []
    for key in CARD_KEYS:
        if inputs["research"].get(key):
            steps.append({"key": "r:" + key, "label": RESEARCH_LABELS[key], "state": "pending", "note": ""})
    for f in inputs["formats"]:
        steps.append({"key": "a:" + f, "label": "Writing the %s copy" % FORMATS[f]["short"], "state": "pending", "note": ""})
    steps.append({"key": "f:finish", "label": "Checking every line against the limits, the rules and the facts", "state": "pending", "note": ""})
    return steps


def wants_brief(inputs, fmt):
    return bool(FORMATS[fmt]["brief"] and inputs.get("briefs", True))


def is_adcopy(rec):
    return ((rec or {}).get("inputs") or {}).get("kind") == KIND


def brief(rec):
    """One row of the list of saved builds."""
    inputs = rec.get("inputs") or {}
    return {"id": rec["id"], "name": rec["name"], "status": rec["status"], "createdBy": rec.get("created_by"), "createdAt": rec.get("created_at"),
            "accountName": rec.get("account_name") or "", "website": inputs.get("website") or "", "product": (inputs.get("product") or "")[:120],
            "formats": [FORMATS[f]["short"] for f in inputs.get("formats") or [] if f in FORMATS], "stale": bool(rec.get("stale")), "cost": (rec.get("cost") or {}).get("usd")}
