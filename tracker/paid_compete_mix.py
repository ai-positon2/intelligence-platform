"""Paid Marketing Agents: Competitor Analysis, the kinds of ads (no network, no clock but the one passed in).

The words of the ads are compared in tracker/paid_compete_math.py. This compares what KIND of ads each side runs, which the words cannot show:

  * the format (Meta: image, video, carousel, dynamic, catalog; Google: text, image, video);
  * on Meta, the call to action (the button) and the placements (Facebook, Instagram, Messenger, Audience Network, Threads);
  * how fresh the ads are: the share started in the last 30 days (ours on Meta only: Google gives no start day for an ad);
  * which of their formats run longest (a long run is the best public sign that an ad works).

Our side is read by tracker/paid_compete_own.py and turned into "units" here: one per ad, and on Google one per Performance Max asset group, which can show as text, image and video at once
(so our shares can add up to more than 100). Their side is every ad read, pictures and videos with no words included.

A difference is a gap only when it is as clear as a gap in the words: their share at least GAP_POINTS above ours, in at least MIN_ADS_WITH of their ads, in two competitors when two were read, from at
least MIN_POOL of their ads and MIN_OWN of ours. Short of that it says what it could not judge.
"""
from __future__ import annotations

from datetime import date

from . import paid_compete_math as cm

MIN_POOL = 8                      # their ads needed to judge a mix
MIN_OWN = 3                       # our units needed
MIN_ADS_WITH = 3
GAP_POINTS = 25
HIGH_POINTS = 45
NEW_DAYS = 30

FORMAT_LABEL = {"text": "Text", "image": "Single image", "video": "Video", "carousel": "Carousel", "dynamic": "Dynamic (several versions)", "catalog": "Catalog"}
NETWORK_LABEL = {"facebook": "Facebook", "instagram": "Instagram", "messenger": "Messenger", "audience_network": "Audience Network", "threads": "Threads"}
META_NETWORKS = ("facebook", "instagram", "messenger", "audience_network")          # where an ad with automatic placements can show
CTA_LABEL = {"LEARN_MORE": "Learn more", "SHOP_NOW": "Shop now", "SIGN_UP": "Sign up", "BOOK_TRAVEL": "Book now", "BOOK_NOW": "Book now", "CONTACT_US": "Contact us", "GET_QUOTE": "Get quote",
             "APPLY_NOW": "Apply now", "DOWNLOAD": "Download", "GET_OFFER": "Get offer", "SUBSCRIBE": "Subscribe", "CALL_NOW": "Call now", "MESSAGE_PAGE": "Send message", "SEND_MESSAGE": "Send message",
             "WHATSAPP_MESSAGE": "Send WhatsApp message", "WATCH_MORE": "Watch more", "ORDER_NOW": "Order now", "GET_STARTED": "Get started", "SEE_MORE": "See more", "REQUEST_TIME": "Request time",
             "SCHEDULE": "Schedule", "GET_DIRECTIONS": "Get directions", "NO_BUTTON": "No button", "SEE_DETAILS": "See details", "INSTALL_MOBILE_APP": "Install now", "BUY_NOW": "Buy now",
             "DONATE_NOW": "Donate now", "LISTEN_NOW": "Listen now", "PLAY_GAME": "Play game", "OPEN_LINK": "Open link", "SAVE": "Save", "LIKE_PAGE": "Like page", "INQUIRE_NOW": "Inquire now"}

# What to try when they run a kind of ad clearly more than we do
TRY = {
    ("format", "video"): "Make one short video (6 to 15 seconds, the message in the first 3 seconds, captions on) from the angle of your best ad, and run it beside your images.",
    ("format", "carousel"): "Build one carousel: one card per service, product or reason to choose you, each with its own headline and page.",
    ("format", "dynamic"): "Give Meta several images, headlines and texts in one ad (flexible format) and let it find the mix that works, instead of one fixed version.",
    ("format", "catalog"): "If you sell products, connect a catalog and test a catalog ad: it shows each person the products they looked at.",
    ("format", "image"): "Add single-image ads: they are the cheapest to make and to test, one message per picture.",
    ("format", "text"): "Make sure every search campaign has a responsive search ad with at least 8 headlines: text ads are the bulk of what they run.",
    ("network", "instagram"): "Put your ads on Instagram (or switch on automatic placements), with a vertical picture or video made for it.",
    ("network", "facebook"): "Put your ads on Facebook too, or switch on automatic placements.",
    ("network", "messenger"): "Try Messenger placements, or automatic placements, so the ad can show there too.",
    ("network", "audience_network"): "Try Audience Network placements, or automatic placements, and watch the cost per result there.",
    ("network", "threads"): "Allow Threads in placements: they are already there.",
    "cta": "Test their button against yours in two copies of the same ad: the button sets what a click promises.",
    "fresh": "Plan new ads every few weeks (new pictures, a new angle) and keep the ones that last: they launch new ads far more often than we do.",
}
ORDER = {"high": 0, "medium": 1, "low": 2}


def _pct(n, total):
    return round(100.0 * n / total, 1) if total else 0.0


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else round((xs[m - 1] + xs[m]) / 2.0, 1)


def _values(item, kind):
    """The values one ad or unit carries for a kind of mix: its formats, its button, its placements."""
    if kind == "format":
        return item.get("formats") or ([item["format"]] if item.get("format") else [])
    if kind == "cta":
        return [item["ctaType"]] if item.get("ctaType") else []
    return item.get("networks") or []


def shares(items, kind, *, who=lambda i: ""):
    """{value: {n, share, who}}: how many of `items` carry each value, their share of all items, and how many advertisers use it."""
    counts, whos = {}, {}
    for it in items:
        for v in set(_values(it, kind)):
            counts[v] = counts.get(v, 0) + 1
            whos.setdefault(v, set()).add(who(it))
    return {v: {"n": n, "share": _pct(n, len(items)), "who": len(whos[v])} for v, n in counts.items()}


def label_of(kind, v):
    if kind == "format":
        return FORMAT_LABEL.get(v, str(v).replace("_", " ").capitalize())
    if kind == "network":
        return NETWORK_LABEL.get(v, str(v).replace("_", " ").capitalize())
    return CTA_LABEL.get(v, str(v).replace("_", " ").capitalize())


def compare_kind(own, theirs, kind, *, n_competitors):
    """{judged, why, rows, gaps, edges} for one kind of mix. `rows` are every value either side uses, theirs first by share."""
    out = {"kind": kind, "judged": False, "why": "", "rows": [], "gaps": [], "edges": [], "theirAds": len(theirs), "ownUnits": len(own)}
    with_value = [a for a in theirs if _values(a, kind)]
    if len(with_value) < MIN_POOL:
        out["why"] = "Only %s of theirs say it, too few to compare (at least %d are needed)." % (cm.plural(len(with_value), "ad"), MIN_POOL)
        return out
    mine = [u for u in own if _values(u, kind)]
    if len(mine) < MIN_OWN:
        out["why"] = ("Only %s of ours could be read for it, too few to compare (at least %d are needed)." % (cm.plural(len(mine), "ad"), MIN_OWN)) if own else "None of our ads could be read for it."
        return out
    t, o = shares(with_value, kind, who=lambda a: a.get("who") or ""), shares(mine, kind)
    need_who = 2 if n_competitors >= 2 else 1
    for v in sorted(set(t) | set(o), key=lambda v: (-(t.get(v) or {}).get("share", 0), -(o.get(v) or {}).get("share", 0), v)):
        ts, os_ = t.get(v) or {"n": 0, "share": 0.0, "who": 0}, o.get(v) or {"n": 0, "share": 0.0, "who": 0}
        row = {"value": v, "label": label_of(kind, v), "their": ts["share"], "own": os_["share"], "ads": ts["n"], "who": ts["who"], "ownN": os_["n"]}
        out["rows"].append(row)
        diff = ts["share"] - os_["share"]
        if diff >= GAP_POINTS and ts["n"] >= MIN_ADS_WITH and ts["who"] >= need_who:
            out["gaps"].append(dict(row, level="high" if diff >= HIGH_POINTS and ts["share"] >= 50 else "medium"))
        elif -diff >= GAP_POINTS and os_["n"] >= MIN_ADS_WITH:
            out["edges"].append(row)
    out["judged"] = True
    return out


def durability(theirs):
    """[{format, label, ads, median, longest}] of their formats by how long their ads have run, the longest-lived first. Ads with no known run are left out."""
    by = {}
    for a in theirs:
        if a.get("format") and a.get("days") is not None:
            by.setdefault(a["format"], []).append(a["days"])
    rows = [{"format": f, "label": label_of("format", f), "ads": len(d), "median": _median(d), "longest": max(d)} for f, d in by.items()]
    return sorted(rows, key=lambda r: (-(r["median"] or 0), -r["ads"], r["format"]))


def _since(iso, today):
    d = cm.to_date(iso)
    return d is not None and (today - d).days <= NEW_DAYS


def freshness(own, theirs, *, today, own_known):
    """{their, own, theirNew, ownNew, gap}: the share of each side's ads started in the last NEW_DAYS days. `own_known` says whether our start days were read (Meta) or not (Google)."""
    dated = [a for a in theirs if a.get("firstShown")]
    t_new = sum(1 for a in dated if _since(a["firstShown"], today))
    out = {"their": _pct(t_new, len(dated)) if dated else None, "theirNew": t_new, "theirDated": len(dated), "own": None, "ownNew": None, "gap": None}
    if own_known:
        mine = [u for u in own if u.get("created")]
        o_new = sum(1 for u in mine if _since(u["created"], today))
        out.update(own=_pct(o_new, len(mine)) if mine else None, ownNew=o_new)
        if out["their"] is not None and out["own"] is not None and len(dated) >= MIN_POOL and len(mine) >= MIN_OWN and t_new >= MIN_ADS_WITH and out["their"] - out["own"] >= GAP_POINTS:
            out["gap"] = "high" if out["their"] - out["own"] >= HIGH_POINTS else "medium"
    return out


def analyse(platform, own, theirs, *, n_competitors, today, own_error=""):
    """The mix report for one platform. `own` is our units (tracker/paid_compete_own.py), `theirs` their ads (words or not, each once)."""
    kinds = ("format", "cta", "network") if platform == "meta" else ("format",)
    out = {"platform": platform, "ownError": own_error, "ownUnits": len(own), "theirAds": len(theirs),
           "kinds": {k: compare_kind(own, theirs, k, n_competitors=n_competitors) for k in kinds},
           "durability": durability(theirs), "fresh": freshness(own, theirs, today=today, own_known=platform == "meta" and not own_error),
           "automatic": sum(1 for u in own if u.get("automatic")) if platform == "meta" else 0,
           "pmaxNoVideo": sum(1 for u in own if u.get("kind") == "pmax" and "video" not in (u.get("formats") or [])) if platform == "google" else 0}
    if own_error:
        for k in out["kinds"].values():
            k.update(judged=False, why="Our own %s ads could not be read for this (%s)." % ("Meta" if platform == "meta" else "Google", own_error), rows=[], gaps=[], edges=[])
    return out


def ideas_of(mixes):
    """Ideas from the mix gaps, worst first: [{id, level, platform, title, why, tryThis, example}] (the same shape as tracker/paid_compete_math.py ideas_of)."""
    names = {"google": "Google", "meta": "Meta"}
    what = {"format": "format", "cta": "button", "network": "placement"}
    out = []
    for p, m in mixes.items():
        long_by = {r["format"]: r for r in m["durability"]}
        for kind, k in m["kinds"].items():
            for g in k["gaps"]:
                why = "%d%% of their ads are %s (%s, %s) against %d%% of ours." % (round(g["their"]), _phrase(kind, g["label"]), cm.plural(g["ads"], "ad"), cm.plural(g["who"], "competitor"), round(g["own"]))
                if kind == "format" and g["value"] in long_by and long_by[g["value"]]["median"]:
                    why += " Their %s ads have run %s at the median." % (g["label"].lower(), cm.plural(round(long_by[g["value"]]["median"]), "day"))
                if kind == "format" and g["value"] == "video" and p == "google" and m["pmaxNoVideo"]:
                    why += " %s of ours %s no video of our own, so Google makes one from the pictures." % (cm.plural(m["pmaxNoVideo"], "Performance Max asset group"), "has" if m["pmaxNoVideo"] == 1 else "have")
                tip = TRY.get((kind, g["value"])) or (TRY["cta"] if kind == "cta" else "Test this %s against what you run now." % what[kind])
                out.append({"id": "%s:%s:%s" % (p, kind, g["value"]), "level": g["level"], "platform": p, "title": "%s: they run more %s" % (names[p], _phrase(kind, g["label"])),
                            "why": why, "tryThis": tip, "example": None})
        f = m["fresh"]
        if f["gap"]:
            out.append({"id": "%s:fresh" % p, "level": f["gap"], "platform": p, "title": "%s: they launch new ads more often" % names[p],
                        "why": "%d%% of their ads started in the last %d days (%s) against %d%% of ours." % (round(f["their"]), NEW_DAYS, cm.plural(f["theirNew"], "ad"), round(f["own"])),
                        "tryThis": TRY["fresh"], "example": None})
    out.sort(key=lambda i: (ORDER[i["level"]], i["id"]))
    return out


def _phrase(kind, label):
    if kind == "format":
        return label.lower() + " ads" if not label.lower().endswith("ads") else label.lower()
    if kind == "network":
        return "ads on " + label
    return 'ads with the "%s" button' % label


def today_of(now):
    return now.date() if hasattr(now, "date") else (now or date.today())

