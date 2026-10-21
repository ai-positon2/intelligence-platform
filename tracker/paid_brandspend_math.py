"""Paid Marketing Agents: Brand Spend Alert, the arithmetic and the rules (no network, no clock but the one passed in).

A campaign-day row is one Search campaign on one day:
    {id, name, date (ISO), status, strategy, budget, impressions, clicks, cost}

Nothing on the sheet says what the expected limits are, so they come from the account's own history: the median weekly spend of the weeks before the last one,
with a band of 30% either way (changeable). The cause of a move is split into clicks and cost per click, because spend is the one times the other.
Which campaigns are brand campaigns is the Brand IS Monitor's rule (tracker/paid_brandis_math.py).
"""
from __future__ import annotations

import datetime as dt
import math

from . import paid_brandis_math as bm

WEEKS = bm.WEEKS
MIN_BASE = 4                    # weeks with spend before the last one that an expected week needs
BASE_MAX = 7                    # and the most that are used
DEFAULT_LIMIT = 0.30
MIN_LIMIT, MAX_LIMIT = 0.10, 0.90
MIN_SWING = 25.0                # a move smaller than this in the account's currency is noise on a small brand
CAUSE_SHARE = 0.60              # this much of the move from one of clicks and cost per click names the cause
MIN_PARTIAL_DAYS = 2            # days of this week needed before it is held to the band

weeks = bm.weeks
is_brand = bm.is_brand
split_campaigns = bm.split_campaigns


class BadRequest(ValueError):
    pass


def window(today):
    """(first day, last day) to read: the Monday of the oldest of the eight whole weeks, and yesterday."""
    return weeks(today)[0][0], today - dt.timedelta(days=1)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def median(values):
    v = sorted(values)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def money(v, currency="USD"):
    """The Brand IS Monitor's money, with a half rounded up from 100 on (Python rounds 1234.5 down, the page rounds it up, and the page and the server must write the same figure)."""
    from .paid_brandis_plan import money as _money
    if v is not None and abs(v) >= 100:
        v = math.floor(abs(v) + 0.5) * (1 if v >= 0 else -1)
    return _money(v, currency)


def pct(v, digits=0):
    return ("%." + str(digits) + "f%%") % (v * 100)


# ── Totals ──────────────────────────────────────────────────────────────────

def total(rows):
    """{cost, clicks, impressions, conversions, cpc, days}: the rows put together; `days` is how many different days had spend; cpc is None without clicks."""
    cost = sum(_f(r.get("cost")) for r in rows)
    clicks = sum(_f(r.get("clicks")) for r in rows)
    out = {"cost": round(cost, 2), "clicks": int(round(clicks)), "impressions": int(round(sum(_f(r.get("impressions")) for r in rows))),
           "conversions": round(sum(_f(r.get("conversions")) for r in rows), 2), "days": len({r.get("date") for r in rows if _f(r.get("cost")) > 0})}
    out["cpc"] = round(cost / clicks, 4) if clicks > 0 else None
    return out


def in_week(rows, monday, sunday):
    lo, hi = monday.isoformat(), sunday.isoformat()
    return [r for r in rows if lo <= str(r.get("date"))[:10] <= hi]


def _trend(rows, wk):
    out = []
    for m, s in wk:
        t = total(in_week(rows, m, s))
        t["from"], t["to"] = m.isoformat(), s.isoformat()
        out.append(t)
    return out


def baseline(trend):
    """The usual week from the weeks before the last one: {weeks, cost, clicks, cpc, low, high are added by the caller} or None when fewer than MIN_BASE of them had spend."""
    prev = [w for w in trend[:-1] if w["cost"] > 0][-BASE_MAX:]
    if len(prev) < MIN_BASE:
        return None
    return {"weeks": len(prev), "cost": round(median([w["cost"] for w in prev]), 2),
            "clicks": median([w["clicks"] for w in prev]), "cpc": median([w["cpc"] for w in prev if w["cpc"] is not None])}


def judge(cost, usual, limit):
    """("over"|"under"|"ok", ratio, difference) of a spend against the usual spend, with the band and the minimum swing; ("ok", None, 0) with no usual."""
    if not usual or usual <= 0:
        return "ok", None, 0.0
    ratio, diff = cost / usual, cost - usual
    if abs(diff) < MIN_SWING:
        return "ok", round(ratio, 4), round(diff, 2)
    if ratio > 1 + limit + 1e-9:
        return "over", round(ratio, 4), round(diff, 2)
    if ratio < 1 - limit - 1e-9:
        return "under", round(ratio, 4), round(diff, 2)
    return "ok", round(ratio, 4), round(diff, 2)


def level_of(state, ratio, limit):
    """"high" at twice the limit (or with nothing spent), else "medium"."""
    if state == "stopped":
        return "high"
    if ratio is None:
        return "medium"
    return "high" if abs(ratio - 1) >= 2 * limit - 1e-9 else "medium"


def cause(now, base):
    """"clicks", "cpc" or "both" for the move of the last week against the usual one, or None when either side has no clicks."""
    if not base or not now["clicks"] or not base.get("clicks") or not now["cpc"] or not base.get("cpc"):
        return None
    dc, dp = math.log(now["clicks"] / base["clicks"]), math.log(now["cpc"] / base["cpc"])
    tot = abs(dc) + abs(dp)
    if tot < 1e-9:
        return None
    if abs(dc) / tot >= CAUSE_SHARE:
        return "clicks"
    if abs(dp) / tot >= CAUSE_SHARE:
        return "cpc"
    return "both"


# ── One account ─────────────────────────────────────────────────────────────

def _campaign_moves(brand_rows, brand, wk, limit):
    out = []
    for c in brand:
        mine = [r for r in brand_rows if str(r["id"]) == c["id"]]
        tr = _trend(mine, wk)
        now, base = tr[-1], baseline(tr)
        usual = base["cost"] if base else None
        if now["cost"] <= 0 and base:
            state, diff = "stopped", -usual
        elif base:
            state, _ratio, diff = judge(now["cost"], usual, limit)
            state = {"ok": "steady"}.get(state, state)
        elif now["cost"] > 0 and not any(w["cost"] > 0 for w in tr[:-1]):
            state, diff = "new", now["cost"]
        else:
            state, diff = "unknown", None
        latest_status = ""
        for r in sorted(mine, key=lambda r: str(r.get("date"))):
            latest_status = r.get("status") or latest_status
        out.append({"id": c["id"], "name": c["name"], "status": latest_status, "strategy": c.get("strategy") or "", "budget": c.get("budget"),
                    "cost": now["cost"], "clicks": now["clicks"], "cpc": now["cpc"], "usual": usual, "diff": None if diff is None else round(diff, 2), "state": state})
    out.sort(key=lambda c: (-abs(c["diff"] or 0.0), c["name"].lower()))
    return out


def analyse(campaign_rows, today, *, limit=DEFAULT_LIMIT, overrides=None, currency="USD"):
    """The reading of one account from its rows (every Search campaign that is not removed, every day from the oldest whole week to yesterday):
    {status, limit, brand (last week), expected, ratio, diff, cause, trend, partial, campaigns, otherCampaigns, findings, plan}.
    status: "ok", "over", "under", "stopped" (nothing last week after weeks of spend), "new" (under MIN_BASE weeks of spend to compare), "idle" (none in the window), "none" (no brand campaign)."""
    wk = weeks(today)
    names = {}
    for r in campaign_rows:
        names.setdefault(str(r["id"]), {"id": str(r["id"]), "name": r.get("name") or "", "strategy": r.get("strategy") or "", "budget": r.get("budget")})
    brand, other = split_campaigns(list(names.values()), overrides)
    ids = {c["id"] for c in brand}
    in_brand = [r for r in campaign_rows if str(r["id"]) in ids]
    trend = _trend(in_brand, wk)
    now, base = trend[-1], baseline(trend)
    spent_before = any(w["cost"] > 0 for w in trend[:-1])

    ratio = diff = why = None
    if not brand:
        status = "none"
    elif not spent_before and now["cost"] <= 0 and not _partial_cost(in_brand, today):
        status = "idle"
    elif now["cost"] <= 0 and spent_before:
        status = "stopped"
    elif base is None:
        status = "new"
    else:
        status, ratio, diff = judge(now["cost"], base["cost"], limit)
        if status != "ok":
            why = cause(now, base)
    expected = None
    if base:
        expected = dict(base, low=round(base["cost"] * (1 - limit), 2), high=round(base["cost"] * (1 + limit), 2))
        if ratio is None and now["cost"] > 0:
            ratio = round(now["cost"] / base["cost"], 4)
            diff = round(now["cost"] - base["cost"], 2)
    if status == "stopped" and base:
        ratio, diff = 0.0, -base["cost"]

    out = {"status": status, "limit": limit, "brand": now, "expected": expected, "ratio": ratio, "diff": diff, "cause": why, "trend": trend,
           "weekFrom": wk[-1][0].isoformat(), "weekTo": wk[-1][1].isoformat(),
           "level": level_of(status, ratio, limit) if status in ("over", "under", "stopped") else None,
           "partial": _partial(in_brand, today, base, limit),
           "campaigns": _campaign_moves(in_brand, brand, wk, limit),
           "otherCampaigns": [{"id": c["id"], "name": c["name"], "byOverride": c["byOverride"], "byName": c["byName"]} for c in sorted(other, key=lambda c: c["name"].lower())]}
    for c in brand:                                           # the marks, for the page's buttons
        for mine in out["campaigns"]:
            if mine["id"] == c["id"]:
                mine["byOverride"], mine["byName"] = c["byOverride"], c["byName"]
    from . import paid_brandspend_plan
    out["plan"] = paid_brandspend_plan.build(out, currency)
    out["findings"] = findings(out, currency)
    return out


def _partial_cost(rows, today):
    monday = today - dt.timedelta(days=today.weekday())
    return sum(_f(r.get("cost")) for r in rows if str(r.get("date")) >= monday.isoformat())


def _partial(rows, today, base, limit):
    """This week so far (Monday to yesterday) against the usual week pro rata: {days, cost, expected, ratio, diff, status} or None before the second day of the week."""
    days = today.weekday()
    if days < MIN_PARTIAL_DAYS or not base:
        return None
    monday = today - dt.timedelta(days=days)
    t = total([r for r in rows if monday.isoformat() <= str(r.get("date")) < today.isoformat()])
    usual = base["cost"] / 7.0 * days
    status, ratio, diff = judge(t["cost"], usual, limit)
    return {"days": days, "from": monday.isoformat(), "cost": t["cost"], "clicks": t["clicks"], "expected": round(usual, 2), "ratio": ratio, "diff": diff, "status": status}


# ── What to tell the person ─────────────────────────────────────────────────

def _first(a, *ids):
    steps = (a.get("plan") or {}).get("steps") or []
    for s in steps:
        if not ids or s["id"] in ids:
            return s["title"]
    return steps[0]["title"] if steps else None


def _next(a):
    t = _first(a)
    return ("Start with: %s. The plan below has every step, how to do it and what it is worth." % t) if t else "Open the brand campaigns in Google Ads and compare last week with the weeks before."


_CAUSE_TEXT = {("over", "clicks"): "more clicks", ("over", "cpc"): "dearer clicks", ("under", "clicks"): "fewer clicks", ("under", "cpc"): "cheaper clicks",
               ("over", "both"): "both the number of clicks and what each costs", ("under", "both"): "both the number of clicks and what each costs"}


def _move_line(a):
    base, now = a["expected"], a["brand"]
    parts = []
    if base and base.get("clicks") and now["clicks"]:
        parts.append("clicks %s" % _change(now["clicks"] / base["clicks"]))
    if base and base.get("cpc") and now["cpc"]:
        parts.append("cost per click %s" % _change(now["cpc"] / base["cpc"]))
    return ", ".join(parts)


def _change(ratio):
    n = round((ratio - 1) * 100)
    return "level" if n == 0 else ("up %d%%" % n if n > 0 else "down %d%%" % -n)


def findings(a, currency="USD"):
    """The ranked findings of one analysed account: [{level, kind, title, detail, nextStep}], high first."""
    out = []
    st, now, ex = a["status"], a["brand"], a["expected"]
    if st == "none":
        return [{"level": "info", "kind": "none", "title": "No brand campaign was found",
                 "detail": "No Search campaign that spent in the last 8 weeks has the word brand in its name (and not non-brand, generic or competitor). Nothing can be judged for this account.",
                 "nextStep": "If one of the campaigns is a brand campaign, mark it as one below and read again."}]
    if st == "idle":
        return [{"level": "info", "kind": "idle", "title": "The brand campaigns spent nothing in the last 8 weeks",
                 "detail": "There is nothing to compare. They may be new, paused or switched off on purpose.", "nextStep": "If they should be running, open them in Google Ads and look at their status."}]
    if st == "new":
        out.append({"level": "info", "kind": "new", "title": "Not enough weeks of brand spend to say what is normal",
                    "detail": "The brand campaigns spent %s last week, and fewer than %d earlier weeks had spend to compare with." % (money(now["cost"], currency), MIN_BASE),
                    "nextStep": "Nothing to do yet. It is judged once %d weeks of spend are there." % MIN_BASE})
    elif st == "stopped":
        out.append({"level": "high", "kind": "stopped", "title": "Brand spend stopped: nothing was spent last week",
                    "detail": "The brand campaigns spent nothing from %s to %s%s." % (a["weekFrom"], a["weekTo"], (", against a usual week of %s" % money(ex["cost"], currency)) if ex else ""),
                    "nextStep": _next(a)})
    elif st in ("over", "under"):
        word = "above" if st == "over" else "below"
        detail = "The brand campaigns spent %s from %s to %s, %d%% %s a usual week of %s (the middle of the last %d weeks that had spend; the band is %s to %s)" % (
            money(now["cost"], currency), a["weekFrom"], a["weekTo"], round(abs(a["ratio"] - 1) * 100), word, money(ex["cost"], currency), ex["weeks"],
            money(ex["low"], currency), money(ex["high"], currency))
        line = _move_line(a)
        if a["cause"]:
            detail += ": " + _CAUSE_TEXT[(st, a["cause"])] + (" (%s)" % line if line else "")
        out.append({"level": a["level"], "kind": st, "title": "Brand spend is %s, %s the usual %s" % (money(now["cost"], currency), word, money(ex["cost"], currency)),
                    "detail": detail + ".", "nextStep": _next(a)})
    p = a.get("partial")
    if p and p["status"] != "ok" and st not in ("none", "idle"):
        word = "ahead of" if p["status"] == "over" else "behind"
        out.append({"level": "low" if st in ("over", "under", "stopped") else "medium", "kind": "partial-" + p["status"],
                    "title": "This week so far is %s the usual pace" % word,
                    "detail": "%s spent in the %d days since Monday, against about %s for the same days of a usual week (a part week is a rough measure: it ignores which days are busier)." % (
                        money(p["cost"], currency), p["days"], money(p["expected"], currency)),
                    "nextStep": "Look at the brand campaigns for this week in Google Ads before it ends: the change history and the search terms show what started it."})
    movers = [c for c in a["campaigns"] if c["state"] in ("over", "under", "stopped", "new")]
    if st in ("over", "under", "stopped") and len(a["campaigns"]) > 1 and movers and a["diff"]:
        top = movers[0]
        if top["diff"] is not None and abs(top["diff"]) >= 0.5 * abs(a["diff"]) and (top["diff"] > 0) == (a["diff"] > 0):
            out.append({"level": "low", "kind": "campaign", "title": "%s accounts for most of the change" % top["name"],
                        "detail": "It spent %s last week against %s usually, a move of %s%s." % (
                            money(top["cost"], currency), money(top["usual"], currency) if top["usual"] is not None else "nothing before",
                            money(abs(top["diff"]), currency), " (status in Google Ads: %s)" % top["status"].lower() if top["status"] else ""),
                        "nextStep": "Open this campaign first."})
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    out.sort(key=lambda f: order[f["level"]])
    return out
