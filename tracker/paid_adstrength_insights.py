"""Paid Marketing Agents: Ad Strength Analysis, what the ratings mean for the work.

Pure functions over what tracker/paid_adstrength.py read. For each Responsive Search Ad and each Performance Max asset group:
how weak it is, how much it matters (its impressions), Google's own tips sorted into the ones a headline or description change
can answer and the ones it cannot (sitelinks are an asset added on the campaign, not text in the ad), and the gaps the rules see
in the text itself:

    too few headlines or descriptions     Google takes 15 headlines and 4 descriptions; more distinct text gives it more to test
    keywords missing from the text        Google's most common tip, checked against the ad group's own top keywords
    text Google already rates Low         the first to replace
    near-duplicate headlines              the same words twice are one idea, not two
    pinned headlines                      every pin takes combinations away

For Performance Max, whose tips the API does not carry, the asset counts are held against what Google accepts.

Google does not publish how ad strength is scored. Nothing here claims a change will reach Good or Excellent; it points at what
Google's own tips and ratings point at.
"""
from __future__ import annotations

import re

RANK = {"POOR": 1, "AVERAGE": 2, "GOOD": 3, "EXCELLENT": 4}
LABEL = {"POOR": "Poor", "AVERAGE": "Average", "GOOD": "Good", "EXCELLENT": "Excellent", "PENDING": "Not rated yet",
         "UNSPECIFIED": "Not rated yet", "UNKNOWN": "Not rated yet", "NO_ADS": "No ads"}
WEIGHT = {1: 3, 2: 2}                  # Poor counts more than Average; Good and Excellent are not work

RSA_HEADLINES, RSA_DESCRIPTIONS = 15, 4
HEADLINE_MAX, DESCRIPTION_MAX, LONG_HEADLINE_MAX = 30, 90, 90

# Performance Max: (Google's field type, what the page calls it, the least Google accepts, the most it accepts)
PMAX_SPEC = [("HEADLINE", "headlines", 3, 15), ("LONG_HEADLINE", "long headlines", 1, 5), ("DESCRIPTION", "descriptions", 2, 5),
             ("MARKETING_IMAGE", "landscape images", 1, 20), ("SQUARE_MARKETING_IMAGE", "square images", 1, 20),
             ("PORTRAIT_MARKETING_IMAGE", "portrait images", 0, 20), ("YOUTUBE_VIDEO", "videos", 0, 5)]
PMAX_TEXT = ("HEADLINE", "LONG_HEADLINE", "DESCRIPTION")
FEW_IMAGES = 3                          # fewer than this many of a kind of image is worth saying

_STOP = frozenset("a an the of in for and to on at by with your our you we is are".split())


def rank_of(strength):
    return RANK.get(str(strength or "").upper())


def label_of(strength):
    return LABEL.get(str(strength or "").upper(), "Not rated yet")


def needs_work(strength):
    r = rank_of(strength)
    return r is None or r < 3


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


# ── Words ───────────────────────────────────────────────────────────────────

def tokens(text):
    out = set()
    for w in re.findall(r"\w+", str(text).lower()):                 # any script: a keyword in Hindi or Chinese has words too (the old a-z only gave it none)
        if w in _STOP:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return frozenset(out)


def covers(keyword, texts):
    """True when one of the texts carries every word of the keyword."""
    want = tokens(keyword)
    if not want:
        return False
    # A word in a script without spaces (Chinese, Japanese) is found inside the text, not as one of its words; every other word must be one of the text's.
    for t in texts:
        have, low = tokens(t), str(t).lower()
        if all(w in have or (not w.isascii() and w in low) for w in want):
            return True
    return False


def near_duplicates(texts):
    """Pairs of texts that say the same thing: the same words, or all but one of at least four."""
    seen, out = [], []
    for t in texts:
        k = tokens(t)
        if not k:
            continue
        for other, ok in seen:
            small = min(len(k), len(ok))
            if k == ok or (small >= 4 and len(k & ok) >= small - 1 and abs(len(k) - len(ok)) <= 1):
                out.append((other, t))
                break
        seen.append((t, k))
    return out


# ── Google's tips ───────────────────────────────────────────────────────────

def classify_tip(tip):
    """(kind, text_fix): what a tip of Google's is about, and whether a headline or description change can answer it. Google's
    wording changes, so anything unrecognised is kept as it is and marked "other"."""
    t = str(tip).lower()
    if "sitelink" in t:
        return "sitelinks", False
    if "callout" in t or "structured snippet" in t or "image" in t or "logo" in t or "call asset" in t or "phone" in t:
        return "assets", False
    if "keyword" in t and "headline" in t:
        return "keywords_headlines", True
    if "keyword" in t and "description" in t:
        return "keywords_descriptions", True
    if "headline" in t:
        return "headlines", True
    if "description" in t:
        return "descriptions", True
    return "other", False


# ── Search ads (RSA) ────────────────────────────────────────────────────────

def _gap(kind, severity, text):
    return {"kind": kind, "severity": severity, "text": text}


def analyse_rsa(ad):
    """The ad with its rating read: gaps, Google's tips sorted, keyword coverage, what to do first and how much it matters."""
    heads, descs = [h["text"] for h in ad["headlines"]], [d["text"] for d in ad["descriptions"]]
    rank = rank_of(ad["strength"])
    tips = [dict(zip(("text", "kind", "textFix"), (t, *classify_tip(t)))) for t in ad.get("tips", [])]
    gaps = []
    if rank is None:
        gaps.append(_gap("unrated", "info", "Google has not rated this ad yet. A new ad, or one with too little traffic, stays unrated until it has some."))
    if len(heads) < RSA_HEADLINES:
        gaps.append(_gap("few_headlines", "action" if len(heads) < 10 else "info",
                         "%s of up to %d. More distinct headlines give Google more combinations to test." % (plural(len(heads), "headline"), RSA_HEADLINES)))
    if len(descs) < RSA_DESCRIPTIONS:
        gaps.append(_gap("few_descriptions", "action" if len(descs) < 3 else "info",
                         "%s of up to %d." % (plural(len(descs), "description"), RSA_DESCRIPTIONS)))
    keywords = ad.get("keywords") or []
    cover = {"headlines": {"covered": [], "missing": []}, "descriptions": {"covered": [], "missing": []}}
    for k in keywords:
        cover["headlines"]["covered" if covers(k, heads) else "missing"].append(k)
        cover["descriptions"]["covered" if covers(k, descs) else "missing"].append(k)
    if keywords:
        hm, dm = cover["headlines"]["missing"], cover["descriptions"]["missing"]
        if len(hm) >= max(2, len(keywords) // 2):
            gaps.append(_gap("keywords_headlines", "action", "No headline carries %s, among the ad group's top keywords." % _names(hm[:5])))
        if len(dm) >= max(2, len(keywords) // 2) and len(dm) > len(hm):
            gaps.append(_gap("keywords_descriptions", "info", "No description carries %s." % _names(dm[:5])))
    low_h = [h["text"] for h in ad["headlines"] if h["label"] == "LOW"]
    low_d = [d["text"] for d in ad["descriptions"] if d["label"] == "LOW"]
    if low_h or low_d:
        gaps.append(_gap("low_assets", "action", "Google rates %s Low: %s. These are the first to replace." % (
            plural(len(low_h) + len(low_d), "text"), _names(low_h + low_d, 4))))
    dup = near_duplicates(heads)
    if dup:
        gaps.append(_gap("duplicates", "action", "%s say nearly the same thing (\"%s\" and \"%s\")." % (
            plural(len(dup), "headline pair"), dup[0][0], dup[0][1])))
    pinned = [h for h in ad["headlines"] if h["pin"]]
    if pinned and (len(pinned) >= 3 or len(pinned) * 2 >= len(heads)):
        gaps.append(_gap("pins", "info", "%d of %d headlines are pinned to a position, which takes combinations away from Google." % (len(pinned), len(heads))))
    if any(t["kind"] in ("sitelinks", "assets") for t in tips):
        gaps.append(_gap("assets", "info", "Google also asks for extra assets (%s). Those are added on the campaign or account, not in the ad text, "
                         "so the headline and description changes below do not cover them." % ", ".join(
                             sorted({"sitelinks" if t["kind"] == "sitelinks" else "other assets" for t in tips if t["kind"] in ("sitelinks", "assets")}))))
    text_fixable = any(g["kind"] in ("few_headlines", "few_descriptions", "keywords_headlines", "keywords_descriptions", "low_assets", "duplicates")
                       for g in gaps) or any(t["textFix"] for t in tips)
    return _item(ad, rank, name=ad["adGroup"] or ad["campaign"], gaps=gaps, tips=tips, textFixable=text_fixable, keywordCoverage=cover,
                 stats={"headlines": len(heads), "descriptions": len(descs), "pinned": len(pinned), "lowHeadlines": len(low_h), "lowDescriptions": len(low_d)})


def _names(items, n=5):
    items = list(items)
    shown = ", ".join('"%s"' % x for x in items[:n])
    return shown + (" and %d more" % (len(items) - n) if len(items) > n else "")


# ── Performance Max ─────────────────────────────────────────────────────────

def analyse_group(g):
    rank = rank_of(g["strength"])
    counts, gaps, rows = g.get("counts") or {}, [], []
    if rank is None:
        gaps.append(_gap("unrated", "info", "Google has not rated this asset group yet."))
    for ftype, noun, low, high in PMAX_SPEC:
        n = counts.get(ftype, 0)
        rows.append({"type": ftype, "label": noun, "count": n, "min": low, "max": high})
        if n < low:
            gaps.append(_gap("missing_" + ftype.lower(), "action", "Has %s; Google asks for at least %d." % (plural(n, noun[:-1], noun), low)))
        elif ftype in PMAX_TEXT and n < high:
            gaps.append(_gap("room_" + ftype.lower(), "info", "%d of up to %d %s. More distinct text gives Google more to test." % (n, high, noun)))
        elif ftype == "YOUTUBE_VIDEO" and n == 0:
            gaps.append(_gap("no_video", "info", "No video. Without one Google makes its own from the images, which is rarely as good."))
        elif ftype in ("MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "PORTRAIT_MARKETING_IMAGE") and n < FEW_IMAGES:
            gaps.append(_gap("few_" + ftype.lower(), "info", "Only %s. Google accepts up to %d, and a wider choice helps it fit more placements." % (plural(n, noun[:-1], noun), high)))
    dup = near_duplicates((g.get("texts") or {}).get("HEADLINE", []))
    if dup:
        gaps.append(_gap("duplicates", "action", "%s say nearly the same thing (\"%s\" and \"%s\")." % (
            plural(len(dup), "headline pair"), dup[0][0], dup[0][1])))
    g_rec = g.get("google")
    if g_rec:
        gaps.append(_gap("google_suggests", "info", "Google itself suggests improving this asset group%s." % (
            " (it names %s as reachable)" % label_of(g_rec["adStrength"]) if g_rec.get("adStrength") and rank_of(g_rec["adStrength"]) else "")))
    text_fixable = any(x["kind"] in ("missing_headline", "missing_long_headline", "missing_description", "room_headline", "room_long_headline",
                                     "room_description", "duplicates") for x in gaps)
    return _item(g, rank, name=g["assetGroup"], gaps=gaps, tips=[], textFixable=text_fixable, assets=rows,
                 stats={k: counts.get(k, 0) for k, *_ in PMAX_SPEC})


# ── Both ────────────────────────────────────────────────────────────────────

def _item(src, rank, **extra):
    weight = WEIGHT.get(rank, 0)
    out = dict(src)
    out.update(extra)
    out.update({"strengthLabel": label_of(src["strength"]), "rank": rank, "belowGood": rank is not None and rank < 3,
                "priority": weight * (src.get("impressions", 0) + 1) if weight else 0,
                "weight": weight})
    out["nextStep"] = next_step(out)
    return out


def next_step(item):
    """The one line that says what to do with this item."""
    if item["rank"] is None:
        return "Wait: Google has not rated it yet."
    if not item["belowGood"]:
        return "Nothing needed. It is already %s." % item["strengthLabel"]
    if item["textFixable"]:
        return "Change the headlines and descriptions below to lift it to Good."
    if any(g["kind"] in ("assets", "sitelinks") for g in item["gaps"]):
        return "The text is not what holds it back: add the extra assets Google asks for."
    if "assetGroupId" in item:
        return "Every text slot is full and Google shows no tips for Performance Max, so what holds it back is not in the API. Look at the images and video."
    return "Google gives no text change for this one."


def summarise(search, pmax):
    """The numbers at the top of the page, over both kinds of ad."""
    def n(view, s):
        return view["counts"].get(s, 0)
    rated = [v for v in (search, pmax)]
    return {
        "reviewed": sum(v["total"] for v in rated), "belowGood": sum(v["belowGood"] for v in rated),
        "poor": sum(n(v, "POOR") for v in rated), "average": sum(n(v, "AVERAGE") for v in rated),
        "good": sum(n(v, "GOOD") for v in rated), "excellent": sum(n(v, "EXCELLENT") for v in rated),
        "unrated": sum(v["total"] - sum(n(v, s) for s in RANK) for v in rated),
        "impressionsBelowGood": sum(v["impressionsBelowGood"] for v in rated), "impressions": sum(v["impressions"] for v in rated),
    }
