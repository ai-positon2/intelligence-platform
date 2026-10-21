"""Paid Marketing Agents: Disapproved Ads Tracker, the arithmetic and the rules (no network, no clock).

An ad is one enabled ad of an enabled ad group of an enabled campaign that Google has disapproved or limited:
    {id, campaignId, campaign, adGroupId, adGroup, type, approval, review, topics: [{t, ty, ev: [text]}], headlines: [text], url}
and may carry {kind: "ad" (the default), "asset" (an asset of a Performance Max asset group; adGroupId is then "pmax:<asset group id>:<field>" and `need` the approved assets that field needs),
"keyword" (a disapproved keyword), "meta" (a Meta ad; adGroupId is "meta:<ad set id>")}.
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
SUSPECT_MIN_ADS = 2             # a text found in this many flagged ads is a suspect...
SUSPECT_SHARE = 0.30            # ...when it is in this share of the ones that have text
URL_MIN_ADS = 3
MAX_SUSPECTS = 4

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
    if ad.get("kind") == "keyword":
        return "keyword"
    if ad["approval"] != DISAPPROVED:
        return "limited"
    n = None if cover is None else cover.get(ad["adGroupId"], 0)             # None: this ad group's cover was not read
    if n is None:
        return "unchecked"
    return "dead" if n < ad.get("need", 1) else "covered"


def _topic_key(ad):
    return [t["t"] for t in ad["topics"]] or [NO_TOPIC]


def items_of(ads, cover, weekly):
    out = []
    for ad in ads:
        w = weight((weekly or {}).get(ad["adGroupId"]))
        out.append(dict(ad, kind=ad.get("kind") or "ad", platform=ad.get("platform") or "google", need=ad.get("need", 1), state=_state(ad, cover),
                        covers=None if cover is None else cover.get(ad["adGroupId"], 0), weight=w,
                        appeal=ad.get("review") == "UNDER_APPEAL", reviewing=ad.get("review") == "REVIEW_IN_PROGRESS"))
    order = {"dead": 0, "unchecked": 1, "covered": 2, "keyword": 3, "limited": 4}
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
    """[{topic, label, type, ads, adGroups, campaigns, dead (ad groups with no approved ad), disapproved (ads), examples, hint}], the topic with the most stopped ad groups first, then the most disapproved ads, then the most ads."""
    by = {}
    for i in items:
        if i["kind"] == "keyword":                                         # keywords have their own step and finding: they are not ads
            continue
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
    out.sort(key=lambda t: (-t["dead"], -t["disapproved"], -t["ads"], t["label"].lower()))
    return out


# ── Age, and what the flagged ads have in common ────────────────────────────

def group_fp(account_id, group_id):
    return "group:%s:%s" % (account_id, group_id)


def topic_fp(account_id, topic, disapproved):
    return "%s:%s:%s" % ("topic" if disapproved else "limited", account_id, topic)


def _age(fp, ages, now):
    """(days open, times it came back) of the issue the monitor keeps under this fingerprint, or (None, 0) when it is not kept (a first look, or no store)."""
    a = (ages or {}).get(fp)
    if not a or not a.get("firstSeen") or now is None:
        return None, int((a or {}).get("reopened") or 0)
    from . import paid_monitor
    return paid_monitor.age_days(a["firstSeen"], now), int(a.get("reopened") or 0)


def suspects_of(items):
    """What the flagged ads have in common, most telling first: [{kind: "headline"|"evidence"|"url", text, ads, of}], at most MAX_SUSPECTS. A headline (or a text Google pointed at) found in
    at least SUSPECT_MIN_ADS ads and SUSPECT_SHARE of those that have any, and a final URL in at least URL_MIN_ADS and the same share of the ads: the one cause to fix once."""
    ads = [i for i in items if i["kind"] != "keyword"]
    out = []
    for kind, field, floor in (("headline", lambda i: i.get("headlines") or [], SUSPECT_MIN_ADS), ("evidence", lambda i: [e for t in i["topics"] for e in t.get("ev") or []], SUSPECT_MIN_ADS),
                               ("url", lambda i: [i["url"]] if i.get("url") else [], URL_MIN_ADS)):
        have = [i for i in ads if field(i)]
        count = {}
        for i in have:
            for text in set(field(i)):
                count[text] = count.get(text, 0) + 1
        for text, n in count.items():
            if n >= floor and n >= SUSPECT_SHARE * len(have):
                out.append({"kind": kind, "text": text, "ads": n, "of": len(have)})
    out.sort(key=lambda x: (-x["ads"] / float(x["of"]), -x["ads"], x["kind"], x["text"]))
    return out[:MAX_SUSPECTS]


def _words(items):
    """(one, many) for what the items are: ("ad", "ads"), ("asset", "assets") or ("ad or asset", "ads and assets")."""
    kinds = {i["kind"] for i in items}
    if kinds == {"asset"}:
        return "asset", "assets"
    if "asset" in kinds:
        return "ad or asset", "ads and assets"
    return "ad", "ads"


def _noun(items, n):
    """"3 ads are", "1 asset is", "5 ads and assets are": the count with what is counted."""
    one, many = _words(items)
    return plural(n, one + " is", many + " are")


# ── One account ─────────────────────────────────────────────────────────────

def analyse(ads, cover, weekly, *, currency="USD", account_id="", ages=None, now=None):
    """The reading of one account from its flagged ads:
    {status, counts, items, deadGroups, topics, suspects, campaigns, adGroups, coverKnown, weightKnown, findings, plan}.
    status: "stopped" (an ad group has no approved ad), "disapproved", "limited" or "clear".
    `ages` is {fingerprint: {firstSeen, reopened}} of the issues the monitor keeps (paid_monitor_store), `now` the time of the read: they give each stopped ad group and each policy its
    age and the number of times it came back; without them nothing is said about age."""
    items = items_of(ads, cover, weekly)
    dead = dead_groups(items)
    topics = topics_of(items)
    for g in dead:
        g["fp"] = group_fp(account_id, g["id"])
        g["ageDays"], g["reopened"] = _age(g["fp"], ages, now)
        w = g["weight"]
        g["lostSoFar"] = round(w["usualClicks"] / 7.0 * g["ageDays"]) if w and g["ageDays"] else None
    for t in topics:
        t["fp"] = topic_fp(account_id, t["topic"], t["disapproved"] > 0)
        t["ageDays"], t["reopened"] = _age(t["fp"], ages, now)
    plain = [i for i in items if i["kind"] != "keyword"]
    counts = {"disapproved": sum(1 for i in plain if i["approval"] == DISAPPROVED), "limited": sum(1 for i in plain if i["approval"] != DISAPPROVED),
              "keywords": sum(1 for i in items if i["kind"] == "keyword"), "stoppedGroups": len(dead), "appeal": sum(1 for i in items if i["appeal"]),
              "reviewing": sum(1 for i in items if i["reviewing"]), "assets": sum(1 for i in items if i["kind"] == "asset"), "meta": sum(1 for i in items if i["kind"] == "meta")}
    status = "stopped" if dead else "disapproved" if counts["disapproved"] or counts["keywords"] else "limited" if counts["limited"] else "clear"
    kw_topics = {}
    for i in items:
        if i["kind"] == "keyword":
            for t in _topic_key(i):
                kw_topics[t] = kw_topics.get(t, 0) + 1
    out = {"status": status, "counts": counts, "items": items, "deadGroups": dead, "topics": topics, "suspects": suspects_of(items),
           "keywordTopics": [{"topic": t, "label": "Reason not named" if t == NO_TOPIC else ap.topic_name(t), "keywords": n} for t, n in sorted(kw_topics.items(), key=lambda kv: (-kv[1], kv[0]))],
           "campaigns": len({i["campaign"] for i in items}), "adGroups": len({i["adGroupId"] for i in items}),
           "coverKnown": cover is not None, "weightKnown": weekly is not None, "agesKnown": ages is not None and now is not None}
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
        ages = [g["ageDays"] for g in dead if g["ageDays"] is not None]
        if ages and max(ages) >= 1:
            lost = sum(g["lostSoFar"] or 0 for g in dead)
            detail += " The longest has been stopped for %s%s." % (plural(max(ages), "day"), (" (about %s clicks lost since)" % format(lost, ",")) if lost else "")
        out.append({"level": "high", "kind": "stopped", "title": "%s cannot show any ad" % plural(len(dead), "ad group"), "detail": detail, "nextStep": _next(a)})
    others = [i for i in a["items"] if i["state"] in ("covered", "unchecked")]
    if others:
        top = [t for t in a["topics"] if t["ads"]][:2]
        why = (" Flagged for %s." % " and ".join(t["label"].lower() for t in top)) if top and top[0]["topic"] != NO_TOPIC else ""
        one, many = _words(others)
        if not a["coverKnown"]:
            tail = " Whether another approved %s covers each one could not be read, so none is called serious." % one
        else:
            tail = " Each has another approved %s in its %s, so the ad group still shows." % (one, "asset group" if one == "asset" else "ad group")
        out.append({"level": "medium", "kind": "disapproved", "title": "%s disapproved" % _noun(others, len(others)),
                    "detail": "In %s of %s (%s).%s%s" % (plural(len({i["adGroupId"] for i in others}), "ad group"), plural(len({i["campaign"] for i in others}), "campaign"), _names(others, "campaign"), why, tail),
                    "nextStep": _next(a) if not dead else "Fix the ad groups that cannot show an ad first; these follow."})
    kws = [i for i in a["items"] if i["state"] == "keyword"]
    if kws:
        out.append({"level": "medium", "kind": "keywords", "title": "%s disapproved" % plural(len(kws), "keyword is", "keywords are"),
                    "detail": "A disapproved keyword cannot trigger any ad (%s: %s)." % (plural(len({i["adGroupId"] for i in kws}), "ad group"), ", ".join('"%s"' % (i["headlines"][0] if i["headlines"] else i["id"]) for i in kws[:3]) + (" and %d more" % (len(kws) - 3) if len(kws) > 3 else "")),
                    "nextStep": "Start with: Fix the disapproved keywords." if any(s["id"] == "keywords" for s in a["plan"]["steps"]) else _next(a)})
    lim = [i for i in a["items"] if i["state"] == "limited"]
    if lim:
        out.append({"level": "low", "kind": "limited", "title": "%s limited by policy" % _noun(lim, len(lim)),
                    "detail": "Limited ones still show, but not everywhere or not to everyone (%s)." % _names(lim, "campaign"),
                    "nextStep": "Check the reason in Policy manager: a limit that was not intended can be removed by editing the ad."})
    if c["appeal"] or c["reviewing"]:
        bits = []
        if c["appeal"]:
            bits.append("%s under appeal" % _noun([i for i in a["items"] if i["appeal"]], c["appeal"]))
        if c["reviewing"]:
            bits.append("%s being reviewed" % _noun([i for i in a["items"] if i["reviewing"]], c["reviewing"]))
        out.append({"level": "info", "kind": "waiting", "title": " and ".join(bits).capitalize(), "detail": "They stay on the list until Google decides: a pending appeal is not a fix.",
                    "nextStep": "Check them again in a few business days."})
    sus = a.get("suspects") or []
    if sus and len([i for i in a["items"] if i["kind"] != "keyword"]) >= 3:
        s = sus[0]
        what = {"headline": 'The headline "%s"' % s["text"], "evidence": 'The text "%s", which Google points at,' % s["text"], "url": "The page %s" % s["text"]}[s["kind"]]
        out.append({"level": "medium" if s["ads"] >= 5 else "low", "kind": "common", "title": "%s is in %d of the %d flagged ads that have one" % (what, s["ads"], s["of"]) if s["kind"] != "url"
                    else "%s is the destination of %d of the %d flagged ads" % (what, s["ads"], s["of"]),
                    "detail": "One shared cause is the likeliest reason so many are flagged together: fix it once and they clear together.%s" % ((" %d other thing%s is shared too." % (len(sus) - 1, "" if len(sus) == 2 else "s")) if len(sus) > 1 else ""),
                    "nextStep": "Start with: Fix the shared cause once." if any(x["id"] == "common" for x in a["plan"]["steps"]) else _next(a)})
    rec = [t for t in a["topics"] if t["reopened"]] + [g for g in dead if g["reopened"]]
    if rec:
        worst = max(rec, key=lambda x: x["reopened"])
        name = worst.get("label") or worst["name"]
        out.append({"level": "medium", "kind": "recurring", "title": "%s has come back %s after it was fixed" % (name, plural(worst["reopened"], "time")),
                    "detail": "Something puts it back: a template, a bulk upload, an automated rule or a rule in a tool the client uses. Fixing the ad again will not last until that is found.",
                    "nextStep": "Open Tools, then Change history in Google Ads and look for who or what edited these ads each time they came back."})
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    out.sort(key=lambda f: order[f["level"]])
    return out
