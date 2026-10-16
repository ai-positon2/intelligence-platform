"""Paid Marketing Agents: Asset Coverage Audit, what each kind of campaign should hold, and what is missing.

Pure rules, no reading and no I/O (tracker/paid_assets.py reads Google Ads and calls this).

An *asset* is anything an ad is built from or shown with: a sitelink, a callout, a structured snippet, a call, an image, a
logo, a business name, a video, the headlines and descriptions of a Performance Max asset group. Google Ads attaches them at
three levels (an ad group's own, the campaign's, the account's), and the lowest level that holds a live one wins for that kind
of asset. This decides, for each campaign (and each Performance Max asset group, Display ad and Demand Gen ad), how many live
assets of each kind it has, how many it should have, and how bad the shortfall is.

Two numbers per kind of asset:

    min     what the format needs to run at all (Google's own floor). A sitelink needs at least 2 live ones to show.
    target  what is worth having: the point past which another one adds little. These are Position2's rule of thumb,
            not Google's, and are said so on the page. Change one number here and every account follows.

A link counts as live when it is enabled and not disapproved, paused or past its end date. A link under review counts (it
will serve) but is reported. Google's "limited" status (approved with a label, for example a policy that limits where it
shows) is not a problem and counts as live.
"""
from __future__ import annotations

import datetime as dt

HIGH, MEDIUM, LOW, INFO = "high", "medium", "low", "info"
SEVERITY_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1, INFO: 0}

# ── What each kind of asset is called, and what is held against it ──────────

# Search and Performance Max campaigns show these beside the ad. `fields` are the Google Ads field types that count.
# `missing` / `thin` are the severity when none is live / fewer than the target are.
CAMPAIGN_ASSETS = {
    "SITELINK": {"label": "Sitelinks", "one": "sitelink", "min": 2, "target": 4, "missing": HIGH, "thin": MEDIUM,
                 "why": "Sitelinks show only when at least 2 are live, and 4 or more fill the space under the ad."},
    "CALLOUT": {"label": "Callouts", "one": "callout", "min": 2, "target": 4, "missing": HIGH, "thin": MEDIUM,
                "why": "Callouts show only when at least 2 are live, and 4 or more give the ad room to sell."},
    "STRUCTURED_SNIPPET": {"label": "Structured snippets", "one": "structured snippet", "min": 1, "target": 1, "missing": MEDIUM, "thin": LOW,
                           "why": "A snippet lists what the business offers (services, brands, types) under the ad."},
    "CALL": {"label": "Call", "one": "call asset", "min": 1, "target": 1, "missing": LOW, "thin": LOW,
             "why": "Lets people ring from the ad. Worth it for a business that takes calls."},
    "AD_IMAGE": {"label": "Images", "one": "image", "min": 1, "target": 1, "missing": MEDIUM, "thin": LOW, "channels": ("SEARCH",),
                 "why": "Image assets put a picture beside Search ads."},
    "BUSINESS_NAME": {"label": "Business name", "one": "business name", "min": 1, "target": 1, "missing": LOW, "thin": LOW, "channels": ("SEARCH",),
                      "why": "Shows the business name on the ad, with the logo."},
    "LOGO": {"label": "Business logo", "one": "logo", "fields": ("LOGO", "BUSINESS_LOGO"), "min": 1, "target": 1, "missing": LOW, "thin": LOW, "channels": ("SEARCH",),
             "why": "Shows the logo beside Search ads."},
    "PROMOTION": {"label": "Promotions", "one": "promotion", "min": 0, "target": 1, "missing": INFO, "thin": INFO,
                  "why": "A promotion asset puts an offer or sale on the ad. Only worth adding when there is one."},
    "PRICE": {"label": "Prices", "one": "price asset", "min": 0, "target": 1, "missing": INFO, "thin": INFO,
              "why": "A price asset lists products or services with their prices."},
    "LEAD_FORM": {"label": "Lead forms", "one": "lead form", "min": 0, "target": 1, "missing": INFO, "thin": INFO,
                  "why": "A lead form lets people enquire without leaving Google."},
}
CAMPAIGN_ASSET_ORDER = ("SITELINK", "CALLOUT", "STRUCTURED_SNIPPET", "CALL", "AD_IMAGE", "BUSINESS_NAME", "LOGO", "PROMOTION", "PRICE", "LEAD_FORM")
LOCATION = {"label": "Location", "one": "location asset", "min": 1, "target": 1, "missing": INFO, "thin": INFO,
            "why": "A linked Business Profile or location set shows the address and distance. Only worth it for a business with places people visit."}
LOCATION_SET_TYPES = ("LOCATION_SYNC", "BUSINESS_PROFILE_DYNAMIC_LOCATION_GROUP", "STATIC_LOCATION_GROUP", "DYNAMIC_LOCATION_GROUP")

# Fields whose links Google reports impressions for: a live link that was never shown in 30 days on a campaign that was is "not shown".
SHOWN_FIELDS = ("SITELINK", "CALLOUT", "STRUCTURED_SNIPPET", "CALL", "PROMOTION", "PRICE", "AD_IMAGE")

# The assets inside an ad: a Performance Max asset group, a responsive Display ad, a Demand Gen ad. (label, min, target, missing, thin, fields)
def _s(label, one, mn, target, missing, thin, fields=None):
    return {"label": label, "one": one, "min": mn, "target": target, "missing": missing, "thin": thin, "fields": fields}


PMAX_GROUP = {
    "HEADLINE": _s("Headlines", "headline", 3, 8, HIGH, LOW),
    "LONG_HEADLINE": _s("Long headlines", "long headline", 1, 3, HIGH, LOW),
    "DESCRIPTION": _s("Descriptions", "description", 2, 4, HIGH, LOW),
    "MARKETING_IMAGE": _s("Landscape images", "landscape image", 1, 3, HIGH, MEDIUM),
    "SQUARE_MARKETING_IMAGE": _s("Square images", "square image", 1, 3, HIGH, MEDIUM),
    "PORTRAIT_MARKETING_IMAGE": _s("Portrait images", "portrait image", 0, 1, LOW, LOW, ("PORTRAIT_MARKETING_IMAGE", "TALL_PORTRAIT_MARKETING_IMAGE")),
    "LOGO": _s("Logo", "logo", 1, 1, HIGH, LOW, ("LOGO", "BUSINESS_LOGO")),
    "BUSINESS_NAME": _s("Business name", "business name", 1, 1, HIGH, LOW),
    "YOUTUBE_VIDEO": _s("Videos", "video", 0, 1, MEDIUM, LOW),
}
PMAX_GROUP_ORDER = ("HEADLINE", "LONG_HEADLINE", "DESCRIPTION", "MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "PORTRAIT_MARKETING_IMAGE", "LOGO", "BUSINESS_NAME", "YOUTUBE_VIDEO")
# For these, a link on the campaign (brand guidelines) counts for the asset group too.
PMAX_FROM_CAMPAIGN = {"LOGO": ("LOGO", "BUSINESS_LOGO"), "BUSINESS_NAME": ("BUSINESS_NAME",)}

DISPLAY_AD = {
    "HEADLINE": _s("Headlines", "headline", 1, 3, HIGH, LOW),
    "LONG_HEADLINE": _s("Long headline", "long headline", 1, 1, HIGH, LOW),
    "DESCRIPTION": _s("Descriptions", "description", 1, 3, HIGH, LOW),
    "MARKETING_IMAGE": _s("Landscape images", "landscape image", 1, 3, HIGH, MEDIUM),
    "SQUARE_MARKETING_IMAGE": _s("Square images", "square image", 1, 3, HIGH, MEDIUM),
    "LOGO": _s("Logos", "logo", 0, 1, MEDIUM, LOW, ("LOGO", "SQUARE_LOGO")),
    "BUSINESS_NAME": _s("Business name", "business name", 1, 1, HIGH, LOW),
    "YOUTUBE_VIDEO": _s("Videos", "video", 0, 1, LOW, LOW),
}
DISPLAY_AD_ORDER = ("HEADLINE", "LONG_HEADLINE", "DESCRIPTION", "MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "LOGO", "BUSINESS_NAME", "YOUTUBE_VIDEO")

DEMAND_GEN_IMAGE_AD = {
    "MARKETING_IMAGE": _s("Landscape images", "landscape image", 0, 1, MEDIUM, LOW),
    "SQUARE_MARKETING_IMAGE": _s("Square images", "square image", 0, 1, MEDIUM, LOW),
    "PORTRAIT_MARKETING_IMAGE": _s("Portrait images", "portrait image", 0, 1, MEDIUM, LOW),
    "LOGO": _s("Logo", "logo", 1, 1, HIGH, LOW),
    "BUSINESS_NAME": _s("Business name", "business name", 1, 1, HIGH, LOW),
    "HEADLINE": _s("Headlines", "headline", 1, 3, HIGH, LOW),
    "DESCRIPTION": _s("Descriptions", "description", 1, 3, HIGH, LOW),
}
DEMAND_GEN_IMAGE_AD_ORDER = ("MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "PORTRAIT_MARKETING_IMAGE", "LOGO", "BUSINESS_NAME", "HEADLINE", "DESCRIPTION")
DEMAND_GEN_VIDEO_AD = {
    "YOUTUBE_VIDEO": _s("Videos", "video", 1, 1, HIGH, LOW),
    "LOGO": _s("Logo", "logo", 1, 1, HIGH, LOW),
    "BUSINESS_NAME": _s("Business name", "business name", 1, 1, HIGH, LOW),
    "HEADLINE": _s("Headlines", "headline", 1, 3, HIGH, LOW),
    "LONG_HEADLINE": _s("Long headlines", "long headline", 1, 2, HIGH, LOW),
    "DESCRIPTION": _s("Descriptions", "description", 1, 3, HIGH, LOW),
}
DEMAND_GEN_VIDEO_AD_ORDER = ("YOUTUBE_VIDEO", "LOGO", "BUSINESS_NAME", "HEADLINE", "LONG_HEADLINE", "DESCRIPTION")
# Demand Gen image ads need at least one image of any shape, whichever the three.
DEMAND_GEN_ANY_IMAGE = ("MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "PORTRAIT_MARKETING_IMAGE")

AD_SPECS = {
    "RESPONSIVE_DISPLAY_AD": (DISPLAY_AD, DISPLAY_AD_ORDER, "Responsive display ad"),
    "DEMAND_GEN_MULTI_ASSET_AD": (DEMAND_GEN_IMAGE_AD, DEMAND_GEN_IMAGE_AD_ORDER, "Demand Gen image ad"),
    "DEMAND_GEN_VIDEO_RESPONSIVE_AD": (DEMAND_GEN_VIDEO_AD, DEMAND_GEN_VIDEO_AD_ORDER, "Demand Gen video ad"),
}

# The campaign types this audits and how, and the ones it leaves alone (and why).
CHANNELS = {"SEARCH": "Search", "PERFORMANCE_MAX": "Performance Max", "DISPLAY": "Display", "DEMAND_GEN": "Demand Gen"}
NOT_AUDITED = {"SHOPPING": "Shopping (built from the product feed)", "VIDEO": "Video", "SMART": "Smart (Google manages the assets)",
               "LOCAL_SERVICES": "Local Services (Google manages the listing)", "LOCAL": "Local", "HOTEL": "Hotel", "TRAVEL": "Travel",
               "MULTI_CHANNEL": "App", "APP": "App"}


def channel_label(ctype):
    return CHANNELS.get(ctype) or NOT_AUDITED.get(ctype) or str(ctype or "").replace("_", " ").title()


# ── One link ────────────────────────────────────────────────────────────────

def link_state(link, today=None):
    """live | pending | disapproved | paused | expired for one asset link ({st: status, ps: primary status, rs: reasons, e: end date}).
    Under review counts toward coverage; the other three do not."""
    today = today or dt.date.today()
    ps, rs, st = str(link.get("ps") or ""), set(link.get("rs") or []), str(link.get("st") or "ENABLED")
    if st != "ENABLED" or ps == "PAUSED":
        return "paused"
    if ps == "NOT_ELIGIBLE" or "ASSET_DISAPPROVED" in rs:
        return "disapproved"
    end = str(link.get("e") or "")
    if end and end < today.isoformat():
        return "expired"
    if ps == "PENDING" or "ASSET_UNDER_REVIEW" in rs:
        return "pending"
    return "live"


def tally(links, today=None):
    """{live, pending, disapproved, paused, expired, have} for a list of links; `have` is what counts toward coverage."""
    out = {"live": 0, "pending": 0, "disapproved": 0, "paused": 0, "expired": 0}
    for l in links:
        out[link_state(l, today)] += 1
    out["have"] = out["live"] + out["pending"]
    return out


# ── One kind of asset on one campaign / group / ad ──────────────────────────

def _plural(spec, n):
    return spec["one"] if n == 1 else spec["one"] + "s"


def judge(spec, have):
    """(status, severity, add) for `have` live assets against the spec:

        ok       at or past the target
        thin     past the floor but short of the target
        short    some, but under the floor (one sitelink: Google needs two to show any)
        missing  none

    `add` is how many more reach the target."""
    if have >= spec["target"] and have >= spec["min"]:
        return "ok", None, 0
    add = spec["target"] - have
    if have == 0:
        return "missing", spec["missing"], add
    if have < spec["min"]:
        return "short", spec["missing"], add
    return "thin", spec["thin"], add


def slot(key, spec, have, counts, *, source, note=None, extra=None):
    """The result for one kind of asset: have, min, target, status, severity, how many to add, what is wrong with the ones there.
    `counts` is tally() of the links (None where the format does not report them)."""
    status, severity, add = judge(spec, have)
    counts = counts or {}
    out = {"key": key, "label": spec["label"], "have": have, "min": spec["min"], "target": spec["target"], "status": status,
           "severity": severity, "add": add, "source": source,
           "disapproved": counts.get("disapproved", 0), "paused": counts.get("paused", 0), "pending": counts.get("pending", 0),
           "expired": counts.get("expired", 0), "notShown": 0}
    if note:
        out["note"] = note
    if extra:
        out.update(extra)
    out["message"] = message(spec, out)
    return out


def message(spec, s):
    """The sentence for a slot: what is short and what to add, plus what is wrong with the ones there. '' when all is well."""
    bits = []
    if s["status"] == "missing":
        bits.append("No live %s." % _plural(spec, 2))
    elif s["status"] in ("short", "thin"):
        bits.append("%d live %s." % (s["have"], _plural(spec, s["have"])))
    if s["status"] == "short":
        bits.append("At least %d are needed." % s["min"])
    if s["status"] != "ok":
        bits.append("Add %d more to reach %d." % (s["add"], s["target"]) if s["have"] else "Add %d." % s["add"])
    for key, word in (("disapproved", "disapproved"), ("expired", "past their end date"), ("pending", "under review")):
        if s.get(key):
            bits.append("%d %s." % (s[key], word))
    return " ".join(bits)


def worst(slots):
    sev = [s["severity"] for s in slots if s.get("severity")]
    return max(sev, key=lambda x: SEVERITY_RANK[x]) if sev else None
