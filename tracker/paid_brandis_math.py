"""Paid Marketing Agents: Brand IS Monitor, the arithmetic and the rules (no network, no clock but the one passed in).

A campaign row is one Search campaign in one week:
    {id, name, week (the Monday, ISO), impressions, clicks, cost, share, lostBudget, lostRank, top, absTop}
where a share is a number between 0 and 1 or None when Google gave none.

Google reports a share per campaign, not a count of searches. Several campaigns are put together by what each was *eligible* for: its impressions
divided by its share. The combined share is the impressions over the eligible searches, and the shares lost to budget and to rank are weighted by
eligible searches the same way.
"""
from __future__ import annotations

import datetime as dt
import re

WEEKS = 8
MIN_ELIGIBLE = 100              # eligible searches in a week below which no verdict is given
DEFAULT_THRESHOLD = 0.90
MIN_THRESHOLD, MAX_THRESHOLD = 0.50, 0.99
HIGH_GAP = 0.10                 # this far under the threshold is high
DROP = 0.05                     # a fall of this much on last week is said even above the threshold
CAUSE_SHARE = 0.60              # this much of the lost share from one cause names it the cause


class BadRequest(ValueError):
    pass


# ── Weeks ───────────────────────────────────────────────────────────────────

def weeks(today, n=WEEKS):
    """[(monday, sunday)] of the last n whole weeks that ended before today, oldest first."""
    last_sunday = today - dt.timedelta(days=today.weekday() + 1)
    return [(last_sunday - dt.timedelta(days=7 * i + 6), last_sunday - dt.timedelta(days=7 * i)) for i in range(n - 1, -1, -1)]


# ── Which campaigns are brand campaigns ─────────────────────────────────────

_NOT_BRAND = re.compile(r"non[\s_\-]*brand|(?<![a-z])nb(?![a-z])|un[\s_\-]*brand|(?<![a-z])generic(?![a-z])|competitor|conquest", re.I)
_BRAND = re.compile(r"(?<![a-z])brand(ed|s)?(?![a-z])", re.I)


def is_brand(name):
    """True for a name that says brand and does not say non-brand, generic, competitor or conquest ("P2_Brand", "Brand - Search", "Branded terms")."""
    name = str(name or "")
    return bool(_BRAND.search(name)) and not _NOT_BRAND.search(name)


def split_campaigns(campaigns, overrides=None):
    """([brand campaigns], [others]): each {id, name, ...} placed by its name, unless `overrides` ({campaign id: True or False}) says otherwise.
    Each returned campaign carries `brand` and `byOverride`."""
    overrides = overrides or {}
    brand, other = [], []
    for c in campaigns:
        cid = str(c["id"])
        forced = overrides.get(cid)
        put = bool(forced) if forced is not None else is_brand(c["name"])
        item = dict(c, id=cid, brand=put, byOverride=forced is not None, byName=is_brand(c.get("name")))
        (brand if put else other).append(item)
    return brand, other


# ── Putting shares together ─────────────────────────────────────────────────

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _r(v, n=4):
    return None if v is None else round(v, n)


def combine(rows):
    """One figure from campaign rows (any campaigns, any weeks):
    {impressions, clicks, cost, measured, unmeasured, eligible, share, lostBudget, lostRank, top, absTop}.
    `measured` is the impressions of rows that carry a share, `unmeasured` those that do not; `share` is None when nothing was measured."""
    imps = clicks = cost = measured = unmeasured = eligible = 0.0
    weighted = {k: [0.0, 0.0] for k in ("lostBudget", "lostRank", "top", "absTop")}       # [sum of eligible x value, sum of eligible]
    for r in rows:
        i = _f(r.get("impressions")) or 0.0
        imps += i
        clicks += _f(r.get("clicks")) or 0.0
        cost += _f(r.get("cost")) or 0.0
        s = _f(r.get("share"))
        if s is None or s <= 0 or i <= 0:
            unmeasured += i
            continue
        e = i / s
        measured += i
        eligible += e
        for key, acc in weighted.items():
            v = _f(r.get(key))
            if v is not None:
                acc[0] += e * v
                acc[1] += e
    out = {"impressions": int(round(imps)), "clicks": int(round(clicks)), "cost": round(cost, 2),
           "measured": int(round(measured)), "unmeasured": int(round(unmeasured)), "eligible": int(round(eligible)),
           "share": _r(measured / eligible) if eligible > 0 else None}
    for key, (num, den) in weighted.items():
        out[key] = _r(num / den) if den > 0 else None
    return out


def cause(lost_budget, lost_rank):
    """"budget", "rank", "both" or None (nothing lost, or nothing known) from the two lost shares."""
    lb, lr = lost_budget or 0.0, lost_rank or 0.0
    total = lb + lr
    if total <= 0:
        return None
    if lb / total >= CAUSE_SHARE:
        return "budget"
    if lr / total >= CAUSE_SHARE:
        return "rank"
    return "both"


def pct(v, digits=0):
    return "n/a" if v is None else ("%." + str(digits) + "f%%") % (v * 100)


def points(v):
    """A difference of shares as "5 points"."""
    n = round(abs(v) * 100)
    return "%d point%s" % (n, "" if n == 1 else "s")


# ── One account ─────────────────────────────────────────────────────────────

def week_rows(rows, monday):
    key = monday.isoformat() if hasattr(monday, "isoformat") else str(monday)
    return [r for r in rows if r.get("week") == key]


def analyse(campaign_rows, today, *, threshold=DEFAULT_THRESHOLD, overrides=None, currency="USD"):
    """The reading of one account from its rows (every enabled Search campaign, every week of the window):
    {status, threshold, brand (the latest week combined), previous, delta, cause, trend, campaigns, brandCampaigns, otherCampaigns, findings}.
    status: "ok", "below", "thin" (too few searches to judge), "unmeasured" (impressions but no share) or "none" (no brand campaign)."""
    wk = weeks(today)
    latest, previous = wk[-1][0], wk[-2][0]
    names = {}
    for r in campaign_rows:
        names.setdefault(str(r["id"]), {"id": str(r["id"]), "name": r.get("name") or "", "strategy": r.get("strategy") or "",
                                        "budget": r.get("budget")})
    brand, other = split_campaigns(list(names.values()), overrides)
    brand_ids = {c["id"] for c in brand}
    in_brand = [r for r in campaign_rows if str(r["id"]) in brand_ids]

    trend = []
    for monday, sunday in wk:
        c = combine(week_rows(in_brand, monday))
        trend.append({"from": monday.isoformat(), "to": sunday.isoformat(), **c})
    now, before = trend[-1], trend[-2]

    campaigns = []
    for c in brand:
        cur = combine(week_rows([r for r in in_brand if str(r["id"]) == c["id"]], latest))
        campaigns.append(dict(c, **cur, cause=cause(cur["lostBudget"], cur["lostRank"]),
                              status=_status(cur, threshold)))
    campaigns.sort(key=lambda c: (c["share"] is None, c["share"] if c["share"] is not None else 2, c["name"].lower()))

    status = "none" if not brand else _status(now, threshold)
    delta = None
    if now["share"] is not None and before["share"] is not None and now["eligible"] >= MIN_ELIGIBLE and before["eligible"] >= MIN_ELIGIBLE:
        delta = round(now["share"] - before["share"], 4)
    why = cause(now["lostBudget"], now["lostRank"]) if status == "below" else None

    out = {"status": status, "threshold": threshold, "brand": now, "previous": before, "delta": delta, "cause": why, "trend": trend,
           "weekFrom": latest.isoformat(), "weekTo": wk[-1][1].isoformat(),
           "brandCampaigns": campaigns,
           "otherCampaigns": [{"id": c["id"], "name": c["name"], "byOverride": c["byOverride"], "byName": c["byName"]} for c in sorted(other, key=lambda c: c["name"].lower())]}
    from . import paid_brandis_plan
    out["plan"] = paid_brandis_plan.build(out, currency)
    out["findings"] = findings(out)
    return out


def _status(c, threshold):
    if c["measured"] == 0 and c["unmeasured"] > 0:
        return "unmeasured"
    if c["share"] is None or c["eligible"] < MIN_ELIGIBLE:
        return "thin"
    return "below" if c["share"] < threshold else "ok"


# ── What to tell the person ─────────────────────────────────────────────────

def _first(a, *ids):
    """The title of the first step of the plan with one of these ids (or the first step at all), for a finding to point at."""
    steps = (a.get("plan") or {}).get("steps") or []
    for s in steps:
        if not ids or s["id"] in ids:
            return s["title"]
    return steps[0]["title"] if steps else None


def _next(a, *ids):
    title = _first(a, *ids)
    return ("Start with: %s. The fix plan below has every step, how to do it and what it is worth." % title) if title else "Open the campaigns in Google Ads and check the bids, the budget and the status of the brand ads."


def _loss_line(c):
    parts = []
    if c.get("lostBudget") is not None:
        parts.append("%s lost to budget" % pct(c["lostBudget"]))
    if c.get("lostRank") is not None:
        parts.append("%s lost to rank" % pct(c["lostRank"]))
    return ", ".join(parts)


def findings(a):
    """The ranked findings of one analysed account: [{level, kind, title, detail, nextStep}], high first."""
    out = []
    th, now = a["threshold"], a["brand"]
    if a["status"] == "none":
        return [{"level": "info", "kind": "none", "title": "No brand campaign was found",
                 "detail": "No enabled Search campaign has the word brand in its name (and not non-brand, generic or competitor). Nothing can be judged for this account.",
                 "nextStep": "If one of the campaigns is a brand campaign, mark it as one below and read again."}]
    if a["status"] == "unmeasured":
        out.append({"level": "info", "kind": "unmeasured", "title": "The brand campaigns had impressions but Google gave no impression share",
                    "detail": "%s impressions last week and no share, so nothing is judged. Google leaves the share out when a campaign is very new or has too little traffic." % format(now["unmeasured"], ","),
                    "nextStep": "Read again in a week."})
    elif a["status"] == "thin":
        out.append({"level": "info", "kind": "thin", "title": "Too few brand searches last week to judge",
                    "detail": "The brand campaigns were eligible for about %s searches last week; a verdict needs %d." % (format(now["eligible"], ","), MIN_ELIGIBLE),
                    "nextStep": "Nothing to do. A small brand is not a problem."})
    elif a["status"] == "below":
        gap = th - now["share"]
        level = "high" if gap >= HIGH_GAP - 1e-9 else "medium"
        detail = "The brand campaigns were shown on %s of the searches they were eligible for last week (%s to %s), %s under the %s threshold" % (
            pct(now["share"]), a["weekFrom"], a["weekTo"], points(gap), pct(th))
        loss = _loss_line(now)
        out.append({"level": level, "kind": "below", "title": "Brand impression share is %s, under %s" % (pct(now["share"]), pct(th)),
                    "detail": detail + (": " + loss if loss else "") + ".", "nextStep": _next(a)})
    d = a["delta"]
    if d is not None and d <= -DROP + 1e-9:
        out.append({"level": "medium" if a["status"] != "below" else "low", "kind": "dropped",
                    "title": "Brand impression share fell %s on the week before" % points(d),
                    "detail": "%s last week, %s the week before." % (pct(now["share"]), pct(a["previous"]["share"])),
                    "nextStep": ("Start with: %s. The fix plan below has every step." % _first(a, "changed")) if _first(a, "changed") and any(s["id"] == "changed" for s in a["plan"]["steps"]) else _next(a)})
    for c in a["brandCampaigns"]:
        if c["status"] == "below" and a["status"] != "none" and len(a["brandCampaigns"]) > 1:
            out.append({"level": "low", "kind": "campaign", "title": "%s is at %s" % (c["name"], pct(c["share"])),
                        "detail": "Eligible for about %s searches last week, %s." % (format(c["eligible"], ","), _loss_line(c) or "no loss breakdown"),
                        "nextStep": "Work through the fix plan on this campaign first: it is the one furthest from the threshold."})
        elif c["status"] == "unmeasured":
            out.append({"level": "info", "kind": "campaign-unmeasured", "title": "%s had impressions but no impression share" % c["name"],
                        "detail": "%s impressions last week." % format(c["unmeasured"], ","), "nextStep": "Read again in a week."})
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    out.sort(key=lambda f: order[f["level"]])
    return out
