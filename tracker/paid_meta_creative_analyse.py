"""Paid Marketing Agents: what the Meta ad read means for the work, the two views of tracker/paid_meta_creative.py.

quality(...)   Ad Strength's counterpart. Meta rates no ad out of Excellent, but it ranks each ad that has had 500 impressions on three things against the ads competing for the same
               audience (quality, expected engagement rate, expected conversion rate) and it reports how often the same people saw the ad. The view lists the ads that need work with
               the money behind each, worst and dearest first, and what to try for each.
coverage(...)  Asset Coverage's counterpart. Meta has no asset rules to hold an ad set to, so an ad set is held to a team default instead: at least TARGET_ADS live ads (so there is
               something to compare), not more than MAX_ADS_PER_SET (so none starves), a mix of formats, and no ad Meta has stopped or limited. These are our defaults and the page says so.

Every number is the account's own and every threshold is a named constant below. Nothing here asks a model.
"""
from __future__ import annotations

import datetime as dt

from . import paid_meta_creative as read

MIN_RANKED_IMPRESSIONS = 500          # Meta's own bar for ranking an ad
FATIGUE_MEDIUM, FATIGUE_HIGH = 4.0, 6.0   # 30-day frequency: how many times the same person saw the ad
MIN_FATIGUE_IMPRESSIONS = 1000
MIN_SPEND_NO_RESULT = 50.0
TARGET_ADS = 3
MAX_ADS_PER_SET = 10
SEV = {"high": 3, "medium": 2, "low": 1}
DIM = {"quality": "quality", "engagement": "expected engagement rate", "conversion": "expected conversion rate"}
FIX = {
    "quality": "Replace the image or video and rewrite the text. Meta's quality ranking is about how the ad reads and looks (thin or sensational wording, a weak visual), so keep the offer, "
               "test a new creative next to this one and pause this one once the new one is delivering.",
    "engagement": "People are not stopping for it. Test a new hook in the first line of the text and the first second of a video, or a different image, as a second ad in the same ad set.",
    "conversion": "People respond but then do not convert. Check that the landing page keeps the promise of the ad, that it loads fast on a phone, and that the form or the checkout works.",
}
RESULT_KINDS = {"SALES", "LEADS", "APP"}


def _money(v):
    return "{:,.0f}".format(v)


def _share(a, b):
    return a / b if b else 0.0


def _flag(code, severity, text, nxt):
    return {"code": code, "severity": severity, "text": text, "next": nxt}


def _ranking(word):
    if word not in read.RANKINGS:
        return {"word": None, "rank": None, "label": "Not ranked"}
    rank, label = read.RANKINGS[word]
    return {"word": word, "rank": rank, "label": label}


def _base(raw, account, today, view):
    return {"platform": "meta", "view": view, "account": account, "window": raw.get("window"), "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "warnings": list(raw.get("warnings") or []), "insightsRead": bool(raw.get("insightsRead", True))}


# ── Quality (Ad Strength's counterpart) ─────────────────────────────────────

def ad_item(ad, num, total_spend, any_results):
    """One live ad with its numbers, Meta's three rankings, and what is wrong with it (flags, worst first)."""
    num = num or {}
    ranks = {k: _ranking((num.get("rank") or {}).get(k)) for k in DIM}
    spend, imp, freq, results = num.get("spend") or 0.0, num.get("impressions") or 0, num.get("frequency") or 0.0, num.get("results") or 0.0
    ranked = imp >= MIN_RANKED_IMPRESSIONS and any(r["rank"] is not None for r in ranks.values())
    flags = []
    for key in ("quality", "engagement", "conversion"):
        rk = ranks[key]["rank"]
        if rk is not None and rk >= 2:
            level = "high" if rk >= 4 else "medium" if rk == 3 else "low"
            flags.append(_flag("rank_" + key, level, "Meta ranks this ad's %s %s against the ads competing for the same audience." % (DIM[key], ranks[key]["label"].lower()), FIX[key]))
    if imp >= MIN_FATIGUE_IMPRESSIONS and freq >= FATIGUE_MEDIUM:
        flags.append(_flag("fatigue", "high" if freq >= FATIGUE_HIGH else "medium",
                           "The same people have seen it %.1f times in %d days." % (freq, read.WINDOW_DAYS),
                           "The audience is wearing out. Add fresh creative to the ad set, widen the audience, or lower how often it is shown (a frequency cap or a bigger audience)."))
    if (num.get("objective") in RESULT_KINDS and any_results and results <= 0 and spend >= max(MIN_SPEND_NO_RESULT, 0.01 * total_spend)):
        flags.append(_flag("no_results", "high" if spend >= 3 * MIN_SPEND_NO_RESULT else "medium", "It spent %s in %d days and brought no result." % (_money(spend), read.WINDOW_DAYS),
                           "Check that the pixel or form event that counts as a result is firing, then pause the ad or replace the creative."))
    flags.sort(key=lambda f: -SEV[f["severity"]])
    return {"id": ad["id"], "name": ad["name"], "adset": ad["adset"], "campaign": ad["campaign"], "status": ad["status"], "format": ad["format"], "spend": spend, "impressions": imp,
            "reach": num.get("reach") or 0, "frequency": freq, "clicks": num.get("clicks") or 0, "results": results, "costPer": round(spend / results, 2) if results else None,
            "ranks": ranks, "ranked": ranked, "flags": flags, "severity": flags[0]["severity"] if flags else "", "next": flags[0]["next"] if flags else ""}


def quality(raw, account, today=None):
    ads, ins = raw.get("ads") or [], raw.get("insights") or {}
    live = [a for a in ads if a["status"] == "ACTIVE"]
    total_spend = sum((ins.get(a["id"]) or {}).get("spend") or 0.0 for a in live)
    any_results = any((ins.get(a["id"]) or {}).get("results") for a in live)
    items = [ad_item(a, ins.get(a["id"]), total_spend, any_results) for a in live]
    delivered = [i for i in items if i["impressions"] > 0]
    ranked = [i for i in delivered if i["ranked"]]
    below = [i for i in ranked if any(r["rank"] is not None and r["rank"] >= 2 for r in i["ranks"].values())]
    counts = {}
    for k in DIM:
        c = {"above": 0, "average": 0, "below": 0, "unknown": 0}
        for i in delivered:
            rk = i["ranks"][k]["rank"]
            c["unknown" if rk is None else "above" if rk == 0 else "average" if rk == 1 else "below"] += 1
        counts[k] = c
    flagged = sorted([i for i in delivered if i["flags"]], key=lambda i: (-SEV[i["severity"]], -i["spend"]))
    spend_below = sum(i["spend"] for i in below)
    out = _base(raw, account, today, "quality")
    out["summary"] = {"ads": len(live), "delivered": len(delivered), "idle": len(live) - len(delivered), "ranked": len(ranked), "notRanked": len(delivered) - len(ranked),
                      "belowAverage": len(below), "spend": round(total_spend, 2), "spendBelow": round(spend_below, 2), "shareBelow": round(_share(spend_below, total_spend), 4),
                      "fatigued": sum(1 for i in delivered if any(f["code"] == "fatigue" for f in i["flags"])),
                      "noResults": sum(1 for i in delivered if any(f["code"] == "no_results" for f in i["flags"])), "counts": counts, "flagged": len(flagged)}
    out["items"] = sorted(items, key=lambda i: (-SEV.get(i["severity"], 0), -i["spend"], i["name"].lower()))
    out["fixFirst"] = flagged[:12]
    out["headline"] = _quality_headline(out["summary"], out["insightsRead"])
    out["notes"] = ["Meta ranks an ad only after %d impressions, and ranks it against the ads competing for the same audience, so a ranking says how the ad compares, not how it performs." % MIN_RANKED_IMPRESSIONS,
                    "Frequency is the average number of times a person saw the ad in the last %d days. A retargeting ad is meant to be seen more often than a cold one, so judge it against its job." % read.WINDOW_DAYS,
                    "Results are what each campaign's objective counts (purchases, leads, installs). A campaign aimed at traffic or reach has no result to judge."]
    return out


def _quality_headline(s, insights_read):
    if not insights_read:
        return "Meta's rankings could not be read this time, so the ads are listed without them."
    if not s["ads"]:
        return "No ad in this account is live."
    if not s["delivered"]:
        return "%d ads are live but none delivered in the last %d days." % (s["ads"], read.WINDOW_DAYS)
    if not s["ranked"]:
        return "None of the %d delivering ads has had the %d impressions Meta needs to rank it yet." % (s["delivered"], MIN_RANKED_IMPRESSIONS)
    if not s["belowAverage"]:
        return "None of the %d ranked ads is below average on any of Meta's three rankings." % s["ranked"]
    return "%d of %d ranked ads are below average on at least one of Meta's rankings, and they took %d%% of the spend." % (s["belowAverage"], s["ranked"], round(100 * s["shareBelow"]))


# ── Coverage (Asset Coverage's counterpart) ─────────────────────────────────

def coverage(raw, account, today=None):
    ads, ins = raw.get("ads") or [], raw.get("insights") or {}
    live = [a for a in ads if a["status"] == "ACTIVE"]
    problems = [a for a in ads if a["status"] in read.PROBLEM_STATUSES]
    spend_of = lambda a: (ins.get(a["id"]) or {}).get("spend") or 0.0
    total_spend = sum(spend_of(a) for a in live)
    sets = {}
    for a in live:
        key = a["adsetId"] or a["adset"] or "?"
        s = sets.setdefault(key, {"id": a["adsetId"], "name": a["adset"] or "(no name)", "campaign": a["campaign"], "campaignId": a["campaignId"], "ads": 0, "formats": {},
                                   "dynamic": 0, "texts": 0, "spend": 0.0})
        s["ads"] += 1
        s["formats"][a["format"]] = s["formats"].get(a["format"], 0) + 1
        s["dynamic"] += 1 if a["dynamic"] else 0
        s["texts"] += a["texts"]
        s["spend"] = round(s["spend"] + spend_of(a), 2)
    rows = []
    for s in sets.values():
        flags, share = [], _share(s["spend"], total_spend)
        level = "high" if share >= 0.15 else "medium" if share >= 0.03 or not raw.get("insightsRead", True) else "low"
        if s["ads"] == 1:
            flags.append(_flag("single_ad", level, "Only one live ad: nothing to compare it with, and nothing to fall back on when it wears out.",
                               "Add at least two more ads that differ in the hook or the format, so Meta can learn which works."))
        elif s["ads"] < TARGET_ADS:
            flags.append(_flag("few_ads", "low", "%d live ads. Our target is %d or more, so there is room to test." % (s["ads"], TARGET_ADS), "Add one more ad with a different hook or format."))
        elif s["ads"] > MAX_ADS_PER_SET:
            flags.append(_flag("many_ads", "low", "%d live ads in one ad set. Delivery can spread so thin that none gets enough to judge." % s["ads"],
                               "Pause the weakest ads (the Ad Quality view of this agent lists them) and keep %d to %d." % (TARGET_ADS, MAX_ADS_PER_SET)))
        if s["ads"] >= TARGET_ADS and len([k for k, n in s["formats"].items() if n]) == 1 and not s["dynamic"]:
            only = next(iter(s["formats"]))
            flags.append(_flag("one_format", "low", "All %d ads are %s." % (s["ads"], only if only == "other" else only + "s"),
                               "Add a different format (a short vertical video next to images, or the reverse) so the ad set reaches people who respond to something else."))
        rows.append(dict(s, share=round(share, 4), flags=flags, severity=flags[0]["severity"] if flags else "", formatList=sorted(s["formats"])))
    rows.sort(key=lambda r: (-SEV.get(r["severity"], 0), -r["spend"], r["name"].lower()))
    camps = {}
    for a in live:
        c = camps.setdefault(a["campaignId"] or a["campaign"], {"id": a["campaignId"], "name": a["campaign"] or "(no name)", "ads": 0, "adsets": set(), "formats": set(), "spend": 0.0})
        c["ads"] += 1
        c["adsets"].add(a["adsetId"])
        c["formats"].add(a["format"])
        c["spend"] += spend_of(a)
    campaigns = []
    for c in camps.values():
        flags = []
        if "video" not in c["formats"] and c["ads"]:
            flags.append(_flag("no_video", "low", "No video among its ads. Placements such as Reels and Stories are made for video.", "Add a short vertical video to this campaign."))
        campaigns.append({"id": c["id"], "name": c["name"], "ads": c["ads"], "adsets": len(c["adsets"]), "formats": sorted(c["formats"]), "spend": round(c["spend"], 2), "flags": flags,
                          "severity": flags[0]["severity"] if flags else ""})
    campaigns.sort(key=lambda c: (-c["spend"], c["name"].lower()))
    stopped = [{"id": a["id"], "name": a["name"], "adset": a["adset"], "campaign": a["campaign"], "status": a["status"], "label": read.PROBLEM_STATUSES[a["status"]], "format": a["format"],
                "severity": "high" if a["status"] == "DISAPPROVED" else "medium",
                "next": "Open it in Ads Manager, read the reason Meta gives, and fix the creative or the link, or ask for a review. The Disapproved Ads agent lists the reasons."} for a in problems]
    stopped.sort(key=lambda r: (-SEV[r["severity"]], r["name"].lower()))
    steps = []
    for r in rows:
        for f in r["flags"]:
            if f["severity"] in ("high", "medium"):
                steps.append({"severity": f["severity"], "where": r["name"], "campaign": r["campaign"], "text": f["text"], "next": f["next"], "spend": r["spend"]})
    for s in stopped:
        steps.append({"severity": s["severity"], "where": s["name"], "campaign": s["campaign"], "text": "%s by Meta." % s["label"], "next": s["next"], "spend": 0.0})
    steps.sort(key=lambda x: (-SEV[x["severity"]], -x["spend"]))
    n_sets = len(rows)
    enough = sum(1 for r in rows if r["ads"] >= TARGET_ADS)
    out = _base(raw, account, today, "coverage")
    out["summary"] = {"adsets": n_sets, "ads": len(live), "singleAd": sum(1 for r in rows if r["ads"] == 1), "enough": enough, "shareEnough": round(_share(enough, n_sets), 4),
                      "withVideo": sum(1 for r in rows if "video" in r["formats"]), "stopped": len(stopped), "campaigns": len(campaigns),
                      "needsWork": sum(1 for r in rows if r["flags"]) + len(stopped)}
    out["adsets"], out["campaigns"], out["stopped"], out["nextSteps"] = rows, campaigns, stopped, steps[:15]
    out["headline"] = _coverage_headline(out["summary"])
    out["targets"] = {"ads": TARGET_ADS, "maxAds": MAX_ADS_PER_SET}
    out["notes"] = ["Meta has no asset rules the way Google does, so an ad set is held to a team default: at least %d live ads, no more than %d, and more than one format. Change the defaults in "
                    "tracker/paid_meta_creative_analyse.py." % (TARGET_ADS, MAX_ADS_PER_SET),
                    "A format is worked out from the creative (a video, a carousel of two or more cards, an image). A flexible creative that mixes images and texts counts as one ad."]
    return out


def _coverage_headline(s):
    if not s["ads"] and not s["stopped"]:
        return "No ad in this account is live."
    if not s["adsets"]:
        return "No live ad set. %d ads are stopped or limited." % s["stopped"]
    bits = ["%d of %d ad sets have %d or more live ads" % (s["enough"], s["adsets"], TARGET_ADS)]
    if s["singleAd"]:
        bits.append("%d run on a single ad" % s["singleAd"])
    if s["stopped"]:
        bits.append("%d ads are stopped or limited by Meta" % s["stopped"])
    return ", ".join(bits) + "."
