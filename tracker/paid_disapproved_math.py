"""Paid Marketing Agents: Disapproved Ads Tracker, the arithmetic and the rules (no network, no clock).

An ad is one enabled ad of an enabled ad group of an enabled campaign that Google has disapproved or limited:
    {id, campaignId, campaign, adGroupId, adGroup, type, approval, review, topics: [{t, ty, ev: [text]}], headlines: [text], url}
`cover` is {ad group id: how many approved enabled ads it has, or None for a group it was not read for} (None when it could not be read at all) and `weekly` is {ad group id: [{clicks, cost}] for the last five whole
weeks, oldest first} (None when it could not be read).

The question the numbers answer is not "how many ads are disapproved" but "which ad groups can show nothing", so a disapproved ad is serious only when no approved ad covers it.
"""
from __future__ import annotations

from . import paid_assets_plans as ap
from . import paid_brandspend_math as bm

DISAPPROVED = "DISAPPROVED"
LIMITED = ("APPROVED_LIMITED", "AREA_OF_INTEREST_ONLY")
WEEKS = 5                       # whole weeks of ad group clicks read: four to say what is usual, the last to say what it is now
HALF = 0.5                      # an ad group that makes under this share of its usual clicks is said to have lost them
TYPE_RANK = {"PROHIBITED": 0, "FULLY_LIMITED": 1, "LIMITED": 2, "AREA_OF_INTEREST_ONLY": 3, "BROADENING": 4, "DESCRIPTIVE": 5}
EXAMPLES = 3
NO_TOPIC = "NOT_NAMED"

median = bm.median
money = bm.money


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


# ── The weight of an ad group ───────────────────────────────────────────────

def weight(weeks):
    """{usualClicks, usualCost, lastClicks, lastCost, lostClicks} from an ad group's last WEEKS whole weeks (oldest first, [{clicks, cost}]); usual is the middle of the earlier weeks that
    had clicks, so a group already stopped for two weeks is still sized from the weeks before. None when no earlier week had a click."""
    if not weeks:
        return None
    earlier, last = weeks[:-1], weeks[-1]
    clicks = [w["clicks"] for w in earlier if w["clicks"] > 0]
    if not clicks:
        return None
    usual = median(clicks)
    cost = median([w["cost"] for w in earlier if w["clicks"] > 0])
    lost = usual - last["clicks"] if last["clicks"] < HALF * usual else 0
    return {"usualClicks": round(usual), "usualCost": round(cost, 2), "lastClicks": round(last["clicks"]), "lastCost": round(last["cost"], 2), "lostClicks": round(lost)}


# ── The items ───────────────────────────────────────────────────────────────

def _state(ad, cover):
    if ad["approval"] != DISAPPROVED:
        return "limited"
    n = None if cover is None else cover.get(ad["adGroupId"], 0)             # None: this ad group's cover was not read
    if n is None:
        return "unchecked"
    return "dead" if n == 0 else "covered"


def _topic_key(ad):
    return [t["t"] for t in ad["topics"]] or [NO_TOPIC]


def items_of(ads, cover, weekly):
    out = []
    for ad in ads:
        w = weight((weekly or {}).get(ad["adGroupId"]))
        out.append(dict(ad, state=_state(ad, cover), covers=None if cover is None else cover.get(ad["adGroupId"], 0), weight=w,
                        appeal=ad.get("review") == "UNDER_APPEAL", reviewing=ad.get("review") == "REVIEW_IN_PROGRESS"))
    order = {"dead": 0, "unchecked": 1, "covered": 2, "limited": 3}
    out.sort(key=lambda i: (order[i["state"]], i["campaign"].lower(), i["adGroup"].lower(), str(i["id"])))
    return out


def dead_groups(items):
    """[{id, name, campaign, ads, topics, weight}], one per ad group whose every enabled ad is disapproved, the one that made the most clicks first."""
    groups = {}
    for i in items:
        if i["state"] != "dead":
            continue
        g = groups.setdefault(i["adGroupId"], {"id": i["adGroupId"], "name": i["adGroup"], "campaign": i["campaign"], "ads": 0, "topics": [], "weight": i["weight"]})
        g["ads"] += 1
        for t in _topic_key(i):
            if t not in g["topics"]:
                g["topics"].append(t)
    return sorted(groups.values(), key=lambda g: (-((g["weight"] or {}).get("usualClicks") or 0), g["campaign"].lower(), g["name"].lower()))


def topics_of(items):
    """[{topic, label, type, ads, adGroups, campaigns, dead (ad groups with no approved ad), disapproved (ads), examples, hint}], the topic with the most stopped ad groups first, then the most ads."""
    by = {}
    for i in items:
        entries = i["topics"] or [{"t": NO_TOPIC, "ty": "", "ev": []}]
        for e in entries:
            g = by.setdefault(e["t"], {"topic": e["t"], "type": "", "ads": set(), "groups": set(), "camps": set(), "dead": set(), "dis": set(), "examples": []})
            g["ads"].add(i["id"])
            g["groups"].add(i["adGroupId"])
            g["camps"].add(i["campaign"])
            if i["state"] == "dead":
                g["dead"].add(i["adGroupId"])
            if i["approval"] == DISAPPROVED:
                g["dis"].add(i["id"])
            ty = str(e.get("ty") or "")
            if ty and (not g["type"] or TYPE_RANK.get(ty, 9) < TYPE_RANK.get(g["type"], 9)):
                g["type"] = ty
            for t in e.get("ev") or []:
                if t and t not in g["examples"] and len(g["examples"]) < EXAMPLES:
                    g["examples"].append(t)
    out = []
    for g in by.values():
        none = g["topic"] == NO_TOPIC
        out.append({"topic": g["topic"], "label": "Reason not named" if none else ap.topic_name(g["topic"]), "type": g["type"], "ads": len(g["ads"]), "adGroups": len(g["groups"]),
                    "campaigns": sorted(g["camps"]), "dead": len(g["dead"]), "disapproved": len(g["dis"]), "examples": g["examples"],
                    "hint": "Google did not name the policy in the read, so open the ad in Policy manager and read the reason beside it." if none else ap.topic_hint(g["topic"])})
    out.sort(key=lambda t: (-t["dead"], -t["ads"], t["label"].lower()))
    return out


# ── One account ─────────────────────────────────────────────────────────────

def analyse(ads, cover, weekly, *, currency="USD"):
    """The reading of one account from its flagged ads:
    {status, counts, items, deadGroups, topics, campaigns, adGroups, coverKnown, weightKnown, findings, plan}.
    status: "stopped" (an ad group has no approved ad), "disapproved", "limited" or "clear"."""
    items = items_of(ads, cover, weekly)
    dead = dead_groups(items)
    counts = {"disapproved": sum(1 for i in items if i["approval"] == DISAPPROVED), "limited": sum(1 for i in items if i["approval"] != DISAPPROVED),
              "stoppedGroups": len(dead), "appeal": sum(1 for i in items if i["appeal"]), "reviewing": sum(1 for i in items if i["reviewing"])}
    status = "stopped" if dead else "disapproved" if counts["disapproved"] else "limited" if counts["limited"] else "clear"
    out = {"status": status, "counts": counts, "items": items, "deadGroups": dead, "topics": topics_of(items),
           "campaigns": len({i["campaign"] for i in items}), "adGroups": len({i["adGroupId"] for i in items}),
           "coverKnown": cover is not None, "weightKnown": weekly is not None}
    from . import paid_disapproved_plan
    out["plan"] = paid_disapproved_plan.build(out, currency)
    out["findings"] = findings(out, currency)
    return out


# ── What to tell the person ─────────────────────────────────────────────────

def _first(a):
    steps = (a.get("plan") or {}).get("steps") or []
    return steps[0]["title"] if steps else None


def _next(a):
    t = _first(a)
    return ("Start with: %s. The plan below has every step and how to do it in Google Ads." % t) if t else "Open Policy manager in Google Ads and read the reason beside each ad."


def _names(items, key, n=3):
    seen = []
    for i in items:
        v = i[key]
        if v and v not in seen:
            seen.append(v)
    return ", ".join(seen[:n]) + (" and %d more" % (len(seen) - n) if len(seen) > n else "")


def findings(a, currency="USD"):
    """The ranked findings of one analysed account: [{level, kind, title, detail, nextStep}], high first."""
    out = []
    c = a["counts"]
    dead = a["deadGroups"]
    if dead:
        usual = sum((g["weight"] or {}).get("usualClicks") or 0 for g in dead)
        last = sum((g["weight"] or {}).get("lastClicks") or 0 for g in dead if g["weight"])
        detail = "Every enabled ad in %s is disapproved, so nothing in %s can show (%s%s)." % (
            plural(len(dead), "ad group"), "it" if len(dead) == 1 else "them", ", ".join("%s in %s" % (g["name"], g["campaign"]) for g in dead[:3]),
            " and %d more" % (len(dead) - 3) if len(dead) > 3 else "")
        if usual:
            detail += " Before, they made about %s clicks a week between them; last week they made %s." % (format(usual, ","), format(last, ","))
        out.append({"level": "high", "kind": "stopped", "title": "%s cannot show any ad" % plural(len(dead), "ad group"), "detail": detail, "nextStep": _next(a)})
    others = [i for i in a["items"] if i["state"] in ("covered", "unchecked")]
    if others:
        top = [t for t in a["topics"] if t["ads"]][:2]
        why = (" Flagged for %s." % " and ".join(t["label"].lower() for t in top)) if top and top[0]["topic"] != NO_TOPIC else ""
        if not a["coverKnown"]:
            tail = " Whether another approved ad covers each one could not be read, so none is called serious."
        else:
            tail = " Each has another approved ad in its ad group, so the ad group still shows."
        out.append({"level": "medium", "kind": "disapproved", "title": "%s disapproved" % plural(len(others), "ad is", "ads are"),
                    "detail": "In %s of %s (%s).%s%s" % (plural(len({i["adGroupId"] for i in others}), "ad group"), plural(len({i["campaign"] for i in others}), "campaign"), _names(others, "campaign"), why, tail),
                    "nextStep": _next(a) if not dead else "Fix the ad groups that cannot show an ad first; these follow."})
    lim = [i for i in a["items"] if i["state"] == "limited"]
    if lim:
        out.append({"level": "low", "kind": "limited", "title": "%s limited by policy" % plural(len(lim), "ad is", "ads are"),
                    "detail": "Limited ads still show, but not everywhere or not to everyone (%s)." % _names(lim, "campaign"),
                    "nextStep": "Check the reason in Policy manager: a limit that was not intended can be removed by editing the ad."})
    if c["appeal"] or c["reviewing"]:
        bits = []
        if c["appeal"]:
            bits.append("%s under appeal" % plural(c["appeal"], "ad is", "ads are"))
        if c["reviewing"]:
            bits.append("%s being reviewed" % plural(c["reviewing"], "ad is", "ads are"))
        out.append({"level": "info", "kind": "waiting", "title": " and ".join(bits).capitalize(), "detail": "They stay on the list until Google decides: a pending appeal is not a fix.",
                    "nextStep": "Check them again in a few business days."})
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    out.sort(key=lambda f: order[f["level"]])
    return out
