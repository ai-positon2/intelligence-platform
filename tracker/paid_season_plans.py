"""Paid Marketing Agents: Seasonality Analysis, from a percentage to dollars and the steps to make the change (no network).

paid_seasonality.verdict() says "raise budgets 15% before the month starts", on an average month's daily budget. A person needs three more things: what an average month's
budget is for this account, what that percentage comes to in dollars a day and over the month (set against what the usual pattern would spend anyway, so a month where spend
already follows demand is not told to spend more), and where to click. This adds them, and for the short holidays a seasonality adjustment in Google Ads with the conversion rate
change measured in past years.

baseline() works out an average month from the last whole months with their own seasons taken out; month_plan() turns one month ahead into dollars and steps. Every figure
that looks forward is an estimate and says what it assumes.
"""
from __future__ import annotations

import datetime as dt

from .paid_exp_plans import money

BASE_MONTHS = 3                     # the latest whole, mostly-active months an average month's spend is worked out from
MIN_CHANGE = 0.02                   # a gap under this share of an average month's spend is not worth a change
ADJUST_MAX_DAYS = 7                 # Google's seasonality adjustments are for shifts of up to a week
ADJUST_MIN = 0.10                   # a measured conversion rate change smaller than this is not worth an adjustment
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _dim(y, m):
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return (nxt - first).days


def baseline(buckets, indexes):
    """{daily, cpa, months}: what an average month spends a day and what a conversion costs in one, from the latest `BASE_MONTHS` usable whole months with each month's own
    season taken out (its spend, and its cost per conversion, divided by that calendar month's index). None when no month can be used."""
    cost_ix, cpa_ix = (indexes or {}).get("cost") or [], (indexes or {}).get("cpa") or []
    spend, cpa = [], []
    for b in [b for b in buckets if b.get("usable")][-BASE_MONTHS:]:
        c = cost_ix[b["m"] - 1]["index"] if len(cost_ix) >= b["m"] else None
        if c and b["days"]:
            spend.append(b["cost"] / b["days"] / (c / 100.0))
        p = cpa_ix[b["m"] - 1]["index"] if len(cpa_ix) >= b["m"] else None
        if p and b["conv"] > 0 and b["cost"] > 0:
            cpa.append(b["cost"] / b["conv"] / (p / 100.0))
    if not spend:
        return None
    return {"daily": sum(spend) / len(spend), "cpa": sum(cpa) / len(cpa) if cpa else None, "months": len(spend)}


def _signed(v):
    return "%+d%%" % round(v)


def _ev_days(e):
    a, b = dt.date.fromisoformat(e["start"]), dt.date.fromisoformat(e["end"])
    return (b - a).days + 1


def _day(d):
    return "%s %d" % (d.strftime("%b"), d.day)


def adjustment(e, platforms):
    """The seasonality adjustment for one holiday of a month ahead, or {"name", "eligible": False, "why"}."""
    base = {"key": e["key"], "name": e["name"], "start": e["start"], "end": e["end"]}
    days = _ev_days(e)
    google = "google" in platforms
    if not google:
        return dict(base, eligible=False, why="Meta has no seasonality adjustment: raise the budgets of the ad sets for these dates instead, by no more than about 20% at a time.")
    if days > ADJUST_MAX_DAYS:
        return dict(base, eligible=False, why="It runs %d days, and Google's seasonality adjustments are for up to %d: use the budget change for the month instead." % (days, ADJUST_MAX_DAYS))
    c = e.get("cvrChange")
    if c is None:
        return dict(base, eligible=False, why="There is no history of how conversion rate moved in these days, so there is nothing to base a change on.")
    if abs(c) < ADJUST_MIN:
        moved = "barely moved" if abs(c) < 0.005 else "moved only %s" % _signed(c * 100)
        return dict(base, eligible=False, why="Conversion rate %s in past years, too little to be worth an adjustment." % moved)
    years = e.get("n") or 1
    mod = int(round(c * 100 / 5.0)) * 5
    text = "Set the conversion rate change to about %s (measured over %d year%s)" % (_signed(mod), years, "" if years == 1 else "s")
    if years == 1:
        half = int(round(mod / 2 / 5.0)) * 5
        text += "; with only one year behind it, a smaller change such as %s is the safer start" % _signed(half)
        mod = half or mod
    return dict(base, eligible=True, modifier=mod, years=years, days=days, steps=[
        "Open Google Ads and find the seasonality adjustments tool (it sits with Bid strategies, under the Goals or Tools menu; Google has moved it more than once, so search the help for \"seasonality adjustments\" if it is not where you look).",
        "Create an adjustment for the campaigns on Smart Bidding (Target CPA, Target ROAS, Maximize conversions or Maximize conversion value). Choose the dates %s to %s (up to %d days)." % (
            _day(dt.date.fromisoformat(e["start"])), _day(dt.date.fromisoformat(e["end"])), ADJUST_MAX_DAYS),
        text + ".",
        "Campaigns on manual bids do not read it: raise their bids by the same share for those dates by hand, and put them back after."])


def _budget_steps(platforms, d, up, costly):
    """The steps to make a budget change, Google's then Meta's. `d` is the dollars record (fromDaily, toDaily, when, until, cur)."""
    steps, cur = [], d["cur"]
    now, to = money(d["fromDaily"], cur), money(d["toDaily"], cur)
    if "google" in platforms:
        if up:
            steps.append("In Google Ads open Campaigns, sort by Cost, and find the campaigns that convert and show Limited by budget in the Status column. Raise their daily budgets together from about %s to about %s a day in all, "
                         "splitting the rise across them in proportion to what each spends now." % (now, to))
            steps.append("Do it %s, and put the budgets back on %s (a reminder in the calendar helps: a raise nobody takes back is the usual leak)." % (d["when"], d["until"]))
            if costly:
                steps.append("A conversion costs more in this month, so raise in two steps, half now and half a week later, and look at cost per conversion before the second.")
            steps.append("Smart Bidding follows demand once the budget lets it, so there is no bid to change. On manual bids the budget is still the only change to make now.")
        else:
            steps.append("In Google Ads open Campaigns, sort by Cost per conversion (worst first), and lower the daily budgets of the dearest campaigns together from about %s to about %s a day in all." % (now, to))
            steps.append("Do it %s and restore them on %s. Leave a campaign still in its learning period alone: a cut there sets it back." % (d["when"], d["until"]))
    if "meta" in platforms:
        lead = "On Meta, open" if "google" in platforms else "Open"
        if up:
            steps.append("%s Ads Manager, sort the ad sets by results, and raise the budgets of the ad sets that convert at a good price, by no more than about 20%% at a time over several days so they stay out of the learning phase, until the account reaches about %s a day." % (lead, to))
        else:
            steps.append("%s Ads Manager and lower the budgets of the ad sets with the dearest conversions, by no more than about 20%% at a time, until the account is at about %s a day." % (lead, to))
    return steps


def month_plan(m, base, today, platforms, cur="USD"):
    """The plan for one month of paid_seasonality.upcoming(), or None. `m` carries budget (percent on an average month), cost (spend index), cpa (cost per conversion index),
    verdict, events (with cvrChange and n). `base` is baseline()."""
    platforms = list(platforms or ["google"])
    events = [adjustment(e, platforms) for e in (m.get("events") or [])]
    out = {"events": events, "dollars": None, "steps": [], "worth": None, "note": None}
    step = m.get("budget") or 0
    if base is None:
        out["note"] = "The last months are too thin to work an average month's spend out of, so the change is left as a percentage."
    elif step:
        dim = _dim(m["y"], m["m"])
        first = dt.date(m["y"], m["m"], 1)
        start = max(first, today)
        left = (dt.date(m["y"], m["m"], dim) - start).days + 1
        if left > 0:
            to_daily = base["daily"] * (1 + step / 100.0)
            pattern = base["daily"] * (m["cost"] / 100.0) if m.get("cost") else None
            ref = pattern if pattern is not None else base["daily"]
            gap = (to_daily - ref) * left
            if abs(to_daily - ref) < MIN_CHANGE * base["daily"]:
                out["note"] = "Spend usually follows demand in %s already (about %s a day on the usual pattern), so no change is needed beyond watching it." % (MONTH_NAMES[m["m"]], money(ref, cur))
            elif (step > 0) != (gap > 0):
                out["note"] = "On the usual pattern spend in %s runs at about %s a day, already %s the %s the plan asks for, so no %s is needed." % (
                    MONTH_NAMES[m["m"]], money(ref, cur), "above" if step > 0 else "below", money(to_daily, cur), "rise" if step > 0 else "cut")
            else:
                up = step > 0
                end = dt.date(m["y"], m["m"], dim)
                now_month = (m["y"], m["m"]) == (today.year, today.month)
                dollars = {"fromDaily": ref, "toDaily": to_daily, "gap": gap, "left": left, "cur": cur, "averageDaily": base["daily"],
                           "when": "from %s" % _day(start) if now_month else "a few days before %s starts (about %s)" % (MONTH_NAMES[m["m"]], _day(first - dt.timedelta(days=3))),
                           "until": _day(end + dt.timedelta(days=1))}
                out["dollars"] = dollars
                costly = m.get("verdict") == "peak_costly"
                out["steps"] = _budget_steps(platforms, dollars, up, costly)
                cpa = base["cpa"] * (m["cpa"] / 100.0) if base.get("cpa") and m.get("cpa") else base.get("cpa")
                more = "more" if gap > 0 else "less"
                worth = "About %s a day in all, %s %s over the %d days left in %s, set against what the usual pattern would spend (about %s a day)" % (
                    money(to_daily, cur), money(abs(gap), cur), more, left, MONTH_NAMES[m["m"]], money(ref, cur))
                if cpa and gap > 0:
                    worth += "; if the extra buys conversions at this month's expected cost (about %s each) that is about %d more, though extra money usually converts a little worse" % (money(cpa, cur), round(gap / cpa))
                elif cpa and gap < 0:
                    worth += "; at this month's expected cost per conversion (about %s) about %d conversions would not come" % (money(cpa, cur), round(-gap / cpa))
                out["worth"] = worth + ". An average month here spends about %s a day (worked out from the last %d months with their own seasons taken out)." % (money(base["daily"], cur), base["months"])
    if not out["steps"] and not out["note"] and not any(e["eligible"] for e in events):
        return None
    return out


def plans(ahead, base, today, platforms, cur="USD"):
    """Attach `plan` to each month of paid_seasonality.upcoming() that has one."""
    for m in ahead:
        p = month_plan(m, base, today, platforms, cur)
        if p:
            m["plan"] = p
    return ahead
