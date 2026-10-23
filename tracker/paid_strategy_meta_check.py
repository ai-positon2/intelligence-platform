"""Paid Marketing Agents: Strategy & Campaign Book, what a Meta plan is held to.

The model writes the Meta campaigns (tracker/paid_strategy_meta_ai.py); this file decides what of it is kept, the way tracker/paid_strategy_check.py does for Google Ads:

  * a Meta campaign has an objective (Leads, Sales, Traffic or Awareness) and settings that are each a value and a reason, with Meta's own standard answers where the model left one empty;
  * ad text over what Meta shows (125 characters of primary text, 40 of headline, 30 of description) never reaches the book: it is dropped and counted, never cut short, and an ad left
    with no primary text or no headline is dropped with it;
  * a button that is not one of Meta's call to action buttons is dropped, an address that is not the client's own is dropped, and a claim the client has not confirmed is listed;
  * an interest that Meta has no such name for is dropped (an interest that cannot be told to be there because Meta did not answer is kept and marked unverified);
  * budgets add up to the share of the monthly budget this platform got.

Every function is a plain function of its arguments: the model is not called and nothing is read from the network.
"""
from __future__ import annotations

import re

from . import paid_strategy as ps, paid_strategy_check as ck
from .paid_meta_creative_analyse import MAX_ADS_PER_SET, TARGET_ADS

LIMITS = {"primary": 125, "headline": 40, "description": 30}
CTAS = ["Learn More", "Get Quote", "Book Now", "Contact Us", "Call Now", "Sign Up", "Apply Now", "Get Offer", "Shop Now", "Download", "Subscribe", "Send Message", "Order Now",
        "Get Directions", "See More", "Watch More"]
FORMATS = ("image", "video", "carousel")
AUDIENCE_TYPES = ("interests", "lookalike", "retargeting", "broad")
MAX_CAMPAIGNS = 6
MAX_AD_SETS = 6
MAX_ADS = 8
LEARNING_RESULTS_A_WEEK = 50               # Meta's own number: an ad set needs about this many results in a week to leave the learning phase

SETTING_KEYS = ["specialCategory", "budgetType", "bidStrategy", "conversionLocation", "optimization", "conversionEvent", "attribution", "locations", "ages", "genders", "languages",
                "placements", "creativeEnhancements"]
OPTIMIZATION = {"metaleads": "Maximize number of conversions (leads)", "metasales": "Maximize number of conversions (purchases)", "metatraffic": "Maximize number of landing page views",
                "metaawareness": "Maximize reach"}
EVENT = {"metaleads": "Lead", "metasales": "Purchase"}
DEFAULTS = {
    "specialCategory": ("None", "The ads are not about credit, jobs, housing, or social issues, elections or politics."),
    "budgetType": ("Advantage campaign budget, daily", "One budget that Meta spreads across the ad sets that are doing best."),
    "bidStrategy": ("Highest volume", "The standard start while a campaign has no results history."),
    "conversionLocation": ("Website", "The ads send people to the client's website."),
    "attribution": ("7-day click and 1-day view", "Meta's own default."),
    "ages": ("All ages Meta allows (18 and over)", "The offer is not age specific."),
    "genders": ("All genders", "The offer is not gender specific."),
    "languages": ("All languages (left empty)", "The ads and the site are in the language the audience speaks."),
    "placements": ("Advantage+ placements", "Meta's recommended setting: it shows the ads where they do best."),
    "creativeEnhancements": ("Off", "So the ads run exactly as the client approved them."),
}
_KIND_WORDS = {"leads": "metaleads", "sales": "metasales", "traffic": "metatraffic", "awareness": "metaawareness"}


def _setting(raw, key, fallback_value="", fallback_why=""):
    r = raw.get(key) if isinstance(raw.get(key), dict) else {}
    value, why = ck.text(r.get("value"), 400), ck.text(r.get("why"), 400)
    if not value and key in DEFAULTS:
        value, why = DEFAULTS[key]
    return {"value": value or fallback_value, "why": why or fallback_why}


def kind_of(raw):
    """The Meta kind a model wrote ("metaleads", "Meta Leads", "leads"), or ''."""
    k = re.sub(r"[^a-z]", "", str(raw or "").lower())
    k = _KIND_WORDS.get(k, k)
    return k if k in ps.META_KINDS else ""


# ── The campaigns ───────────────────────────────────────────────────────────

def architecture(raw, inputs, budget=None):
    """The Meta campaign skeletons: at most MAX_CAMPAIGNS, only the kinds asked for, each named the team's way, budgets split so they add up to `budget` (the monthly budget
    this platform got; the whole budget when only Meta is planned)."""
    raw = raw if isinstance(raw, dict) else {}
    allowed = ps.kinds_for(inputs, "meta")
    budget = inputs.get("budget") if budget is None else budget
    campaigns, keys, names = [], set(), set()
    for r in raw.get("campaigns") if isinstance(raw.get("campaigns"), list) else []:
        if not isinstance(r, dict):
            continue
        kind = kind_of(r.get("kind"))
        if not kind or kind not in allowed:
            continue
        theme = ck.text(r.get("theme"), 60)
        key = ps.slug("%s %s" % (kind, theme), 40).lower() or kind
        base, n = key, 2
        while key in keys:
            key, n = "%s%d" % (base, n), n + 1
        keys.add(key)
        name = base_name = ps.campaign_name(kind, theme, inputs.get("launch"))
        while name in names:                                 # a repeated campaign name is refused by Meta as well
            name = "%s_%d" % (base_name, len(names) + 1)
        names.add(name)
        sets = r.get("settings") if isinstance(r.get("settings"), dict) else {}
        settings = {k: _setting(sets, k) for k in SETTING_KEYS}
        if not settings["optimization"]["value"]:
            settings["optimization"] = {"value": OPTIMIZATION[kind], "why": "The result the objective counts."}
        if not settings["conversionEvent"]["value"]:
            ev = EVENT.get(kind)
            settings["conversionEvent"] = {"value": ev or "None: this objective does not count a conversion event", "why": "The event the campaign optimises for." if ev else "Traffic and Awareness do not optimise for a conversion."}
        if not settings["locations"]["value"]:
            settings["locations"] = {"value": inputs.get("locations") or "To be confirmed with the client", "why": "Taken from the client's service area."}
        campaigns.append({"key": key, "kind": kind, "kindLabel": ps.KINDS[kind]["label"], "objective": ps.KINDS[kind]["objective"], "platform": "meta", "theme": theme or ps.KINDS[kind]["label"],
                          "name": name, "purpose": ck.text(r.get("purpose"), 500), "share": ck.number(r.get("share"), 0, 1000, 0) or 0, "landingPage": ck.text(r.get("landingPage"), 300),
                          "keywordFocus": [], "audience": ck.text(r.get("audience"), 400), "notes": ck.text(r.get("notes"), 500), "settings": settings})
        if len(campaigns) >= MAX_CAMPAIGNS:
            break
    if not campaigns:
        return {"campaigns": []}
    shares = [c["share"] or 1 for c in campaigns]
    days = ps.split_budget(budget, shares, floor=1)
    total = sum(shares)
    for c, d, s in zip(campaigns, days, shares):
        c["share"] = round(100.0 * s / total, 1)
        c["daily"] = d
        c["monthly"] = round(d * 30.4, 0) if d else None
    return {"campaigns": campaigns}


# ── One campaign ────────────────────────────────────────────────────────────

def ads(raw, ctx, fallback_url, drops):
    """The ads of one ad set: each with a primary text, a headline, an optional description and button, kept only when its text fits what Meta shows."""
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        primary = ck.fit([r.get("primaryText")], LIMITS["primary"], 1, drops=drops, what="primary texts")
        head = ck.fit([r.get("headline")], LIMITS["headline"], 1, drops=drops, what="headlines")
        desc = ck.fit([r.get("description")], LIMITS["description"], 1, drops=drops, what="descriptions") if ck.text(r.get("description"), 300) else []
        if not primary or not head:
            drops["ads"] = drops.get("ads", 0) + 1
            continue
        cta = next((c for c in CTAS if c.lower() == ck.text(r.get("cta"), 40).lower()), "")
        if ck.text(r.get("cta"), 40) and not cta:
            drops["buttons"] = drops.get("buttons", 0) + 1
        url, verified = ck.own_url(r.get("finalUrl") or fallback_url, ctx["site"], ctx["known"])
        if (r.get("finalUrl") or fallback_url) and not url:
            drops["landing pages"] = drops.get("landing pages", 0) + 1
        fmt = ck.text(r.get("format"), 30).lower()
        out.append({"name": ck.text(r.get("name"), 80), "format": fmt if fmt in FORMATS else "image", "primaryText": primary[0], "headline": head[0], "description": desc[0] if desc else "",
                    "cta": cta, "concept": ck.text(r.get("concept"), 500), "script": ck.para(r.get("script"), 900), "finalUrl": url, "landingVerified": verified})
        if len(out) >= MAX_ADS:
            break
    return out


def build(raw, ctx, drops):
    """One Meta campaign: ad sets with an audience and ads. `ctx` has site and known."""
    raw = raw if isinstance(raw, dict) else {}
    sets = []
    for g in raw.get("adSets") if isinstance(raw.get("adSets"), list) else []:
        if not isinstance(g, dict) or not ck.text(g.get("name"), 80):
            continue
        url, verified = ck.own_url(g.get("landingPage"), ctx["site"], ctx["known"])
        if g.get("landingPage") and not url:
            drops["landing pages"] = drops.get("landing pages", 0) + 1
        typ = ck.text(g.get("audienceType"), 30).lower()
        typ = next((t for t in AUDIENCE_TYPES if t in typ), "interests")
        sets.append({"name": ck.text(g["name"], 80), "theme": ck.text(g.get("theme"), 200), "audienceType": typ, "interests": ck.lines(g.get("interests"), 10, 80), "behaviors": ck.lines(g.get("behaviors"), 6, 80),
                     "customAudiences": ck.lines(g.get("customAudiences"), 6, 160), "lookalikes": ck.lines(g.get("lookalikes"), 6, 160), "excludes": ck.lines(g.get("excludes"), 8, 160),
                     "budgetShare": ck.number(g.get("budgetShare"), 0, 1000, 0) or 0, "placements": ck.text(g.get("placements"), 200), "landingPage": url, "landingVerified": verified,
                     "ads": ads(g.get("ads"), ctx, url or ctx.get("landingPage"), drops), "interestsChecked": []})
        if len(sets) >= MAX_AD_SETS:
            break
    shares = [s["budgetShare"] or 1 for s in sets]
    total = sum(shares) or 1
    for s, sh in zip(sets, shares):
        s["budgetShare"] = round(100.0 * sh / total, 1)
    return {"adSets": sets, "assets": {}}


def verify_interests(built, answers, unanswered=()):
    """The build with every interest checked against Meta's list: `answers` is {name: Meta's interest or None} (tracker/paid_meta_interests.validate). An interest Meta has no such name for
    is dropped and counted; one whose name is in `unanswered` (Meta did not answer for it, so nothing can be said) is kept with `verified` False. Each ad set gets `interestsChecked`:
    [{name, id, sizeText, verified}]. Returns (build, dropped names)."""
    dropped = []
    for s in built.get("adSets") or []:
        checked, kept = [], []
        for name in s.get("interests") or []:
            hit = (answers or {}).get(name)
            if hit:
                checked.append({"name": hit["name"], "id": hit["id"], "sizeText": hit.get("sizeText") or "", "verified": True})
                kept.append(hit["name"])
            elif name in (unanswered or ()):
                checked.append({"name": name, "id": "", "sizeText": "", "verified": False})
                kept.append(name)
            else:
                dropped.append(name)
        s["interests"], s["interestsChecked"] = kept, checked
    if dropped:
        drops = built.setdefault("drops", {})
        drops["interests"] = drops.get("interests", 0) + len(dropped)
    return built, dropped


def ad_texts(built):
    """Every piece of ad text in a Meta build as (where, text), for the claim scan."""
    out = []
    for g in built.get("adSets") or []:
        for i, a in enumerate(g.get("ads") or [], 1):
            w = "%s, ad %d" % (g["name"], i)
            out += [(w + " primary text", a["primaryText"]), (w + " headline", a["headline"])]
            if a.get("description"):
                out.append((w + " description", a["description"]))
    return out


def shortfalls(built):
    """What a Meta build is short of, in words (an empty list when it has what it needs)."""
    out = []
    if not built.get("adSets"):
        out.append("No ad sets were written.")
    for g in built.get("adSets") or []:
        if not (g["interests"] or g["behaviors"] or g["customAudiences"] or g["lookalikes"] or g["audienceType"] == "broad"):
            out.append("Ad set %s has no audience: name interests, a custom audience or a lookalike, or make it a broad (Advantage+) audience on purpose." % g["name"])
        n = len(g["ads"])
        if n < TARGET_ADS:
            out.append("Ad set %s has %d ad%s after the limits were applied (Position2's default is at least %d, so Meta has something to learn from)." % (g["name"], n, "" if n == 1 else "s", TARGET_ADS))
        elif n > MAX_ADS_PER_SET:
            out.append("Ad set %s has %d ads: more than %d splits the budget too thin." % (g["name"], n, MAX_ADS_PER_SET))
    return out


def learning_gaps(campaigns, research, cur="USD"):
    """Warnings, one per ad set, for an ad set whose budget cannot reach Meta's learning bar: about 50 results in a week at the cost of a result the account has had in the last 90 days
    for that objective (or its overall cost per result). An ad set with no cost per result to go on is not judged, and the page's checks say so."""
    d = ((research or {}).get("metahistory") or {}).get("data") or {}
    by_obj = {}
    for c in d.get("campaigns") or []:                       # every campaign of the objective counts, the ones that brought nothing too: that is what a result cost
        o = by_obj.setdefault(c["objective"], [0.0, 0.0])
        o[0] += c["cost"]
        o[1] += c["conv"]
    overall = (d.get("totals") or {}).get("cpa")
    out, unjudged = [], 0
    for c in campaigns:
        if c.get("platform") != "meta" or not c.get("daily"):
            continue
        obj = c["objective"].upper()
        cost, conv = by_obj.get(obj, [0.0, 0.0])
        cpa, basis = (round(cost / conv, 2), "the account's last 90 days on %s campaigns" % c["objective"]) if conv else (overall, "the account's last 90 days across every objective")
        if c["kind"] in ("metatraffic", "metaawareness") or not cpa:
            unjudged += 1
            continue
        for g in (c.get("build") or {}).get("adSets") or []:
            daily = c["daily"] * g["budgetShare"] / 100.0
            per_week = daily * 7 / cpa
            if per_week < LEARNING_RESULTS_A_WEEK:
                out.append("%s, ad set %s gets %s a day, about %d result%s a week at %s each (%s): Meta wants about %d a week to leave the learning phase. Give it more of the budget or plan fewer ad sets." % (
                    c["name"], g["name"], "{:,.2f}".format(daily), per_week, "" if round(per_week) == 1 else "s", "{:,.2f}".format(cpa), basis, LEARNING_RESULTS_A_WEEK))
    return out, unjudged
