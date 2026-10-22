"""Paid Marketing Agents: Spend Anomaly Alert, the arithmetic (no network, no clock but the one passed in).

An account's spend is read by day, and by hour where the platform gives it (Google Ads does; Meta is tried). From that the account's *usual* is worked out, and today and yesterday are
held against it. The sheet gives no limits, so the usual is the account's own:

  usual day      the median of the days before (at most 14, the same kind of day: weekdays with weekdays, weekend days with weekend days, when there are 4 of that kind)
  usual by now   the median of what those days had spent by this hour of the day (so 10 a.m. is not judged against a whole day)
  a floor        a swing smaller than 25 units of the account's currency, or 10% of a usual day, is noise whatever the ratio
  the ratios     the sensitivity: low 2.5x and 4x, normal 1.75x and 2.75x, high 1.4x and 2x (medium and high)

What it can find, each with a fingerprint the monitor keeps (the kind and the day):

  spike    today so far or yesterday above the usual (and today already above a whole usual day)
  surge    the last three whole hours spending far above their usual: a spike that is happening now, before the day's total shows it
  ceiling  a daily figure the team saved for the account has been passed
  budget   more spent in a day than Google allows for the budgets the account has (it may go up to twice a campaign's daily budget)
  drop     far less than usual, late enough in the day (or yesterday) that it is not just a slow start

A planned push can be muted until a date (its spikes become information). A spike is traced to what moved: the campaign that spent the excess, a campaign started in the last days,
and the changes made in the account lately (the person who made them is flagged when they had made none in the days before). Statuses: over, under, ok, new (not enough days to know
a usual), none (no spend in the window).
"""
from __future__ import annotations

import datetime as dt

from .paid_brandspend_math import median, money

HISTORY_DAYS = 14                # days before a day that make its usual
MIN_REF_DAYS = 4                 # days a usual needs
WINDOW_DAYS = HISTORY_DAYS + 1   # days read before today (the oldest has the 14 days of its own before it only in part)
MIN_SWING = 25.0
FLOOR_SHARE = 0.10
SENSITIVITY = {"low": (2.5, 4.0), "normal": (1.75, 2.75), "high": (1.4, 2.0)}
DEFAULT_SENSITIVITY = "normal"
MIN_HOUR_FOR_PACE = 3            # before this hour of the day the spend so far says too little about the day
SURGE_HOURS = 3
SURGE_MID, SURGE_HIGH = 2.5, 4.0   # at the normal sensitivity; the others scale them the way they scale the day's ratios
LAG_HOURS = 2                    # the platform reports the latest hours late: a drop is judged on the hours before these
DROP_FROM_HOUR = 14              # and not before the afternoon
DROP_SHARE = 0.15                # today so far at or under this share of the usual by then is a drop
DROP_SHARE_DAY = 0.20            # and a whole day at or under this share of a usual day
DROP_MIN_FLOORS = 4              # a usual smaller than this many minimum swings is too small to miss
DROP_HIGH_FLOORS = 10            # and a stop of one at least this large is high
BUDGET_OVER, BUDGET_HIGH = 2.2, 3.0
CAUSE_SHARE = 0.60
NEW_CAMPAIGN_DAYS = 3
MAX_MOVERS = 3
LEVELS = {"info": 0, "low": 1, "medium": 2, "high": 3}
_STATUS_RANK = {"over": 0, "under": 1, "new": 2, "ok": 3, "none": 4}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x else 0.0


def _day(v):
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])


def sensitivity_of(value):
    return value if value in SENSITIVITY else DEFAULT_SENSITIVITY


def floor_of(usual_day, min_swing=None):
    """The smallest swing worth a word: the saved one (or 25 units), or a tenth of a usual day when that is more."""
    return max(float(min_swing) if min_swing else MIN_SWING, FLOOR_SHARE * (usual_day or 0.0))


def grade(spent, usual, floor, mid, high):
    """The level of `spent` against `usual`: "high", "medium", "low" or None. A swing under the floor is nothing; where there is nothing usual to compare with, the size itself counts."""
    excess = spent - usual
    if excess < floor:
        return None
    if usual < floor / 2.0:
        return "high" if spent >= 6 * floor else "medium" if spent >= 2 * floor else None
    r = spent / usual
    if r >= high and excess >= 2 * floor:
        return "high"
    if r >= mid:
        return "medium"
    if r >= 1 + (mid - 1) / 2.0:
        return "low"
    return None


# ── The series ──────────────────────────────────────────────────────────────

def series_of(rows, today):
    """{date: {cost, hours}} for every day from WINDOW_DAYS before `today` to today, from rows [{date, hour (or None), cost}]. A day with no row spent nothing; `hours` is the 24 hours of
    the day (None for a day that came without hours, and for all of them when the platform gave none)."""
    today = _day(today)
    first = today - dt.timedelta(days=WINDOW_DAYS)
    days = {first + dt.timedelta(days=i): {"cost": 0.0, "hours": []} for i in range(WINDOW_DAYS + 1)}
    hourly = any(r.get("hour") is not None for r in rows)
    for r in rows:
        try:
            d = _day(r["date"])
        except (KeyError, ValueError):
            continue
        if d not in days:
            continue
        c = _f(r.get("cost"))
        days[d]["cost"] += c
        if r.get("hour") is not None:
            h = int(r["hour"])
            if 0 <= h <= 23:
                if not days[d]["hours"]:
                    days[d]["hours"] = [0.0] * 24
                days[d]["hours"][h] += c
    for d, v in days.items():
        if hourly and not v["hours"]:
            v["hours"] = [0.0] * 24            # a day that spent nothing has no row at all
        elif not hourly:
            v["hours"] = None
        v["cost"] = round(v["cost"], 4)
    return days


def _cum(hours):
    out, t = [0.0], 0.0
    for x in hours:
        t += x
        out.append(t)
    return out                                                                       # out[h] = spent in the hours before hour h


def reference(days, date, first_spend):
    """([the days before `date` that make its usual], the words for what they are): at most HISTORY_DAYS days from the day the account first spent, the same kind of day when there are
    MIN_REF_DAYS of them."""
    before = [d for d in sorted(days) if first_spend <= d < date and (date - d).days <= HISTORY_DAYS]
    same = [d for d in before if (d.weekday() >= 5) == (date.weekday() >= 5)]
    if len(same) >= MIN_REF_DAYS:
        return same, "weekend days" if date.weekday() >= 5 else "weekdays"
    return before, "the last %d days" % len(before)


class Usual:
    """What a day is usually like for the days before `date`: the cost, the cost by each hour of the day and by hour (None when the days came without hours)."""

    def __init__(self, days, refs):
        self.refs = refs
        self.n = len(refs)
        self.day = median([days[d]["cost"] for d in refs]) if refs else 0.0
        self.max_day = max([days[d]["cost"] for d in refs], default=0.0)
        hourly = refs and all(days[d]["hours"] is not None for d in refs)
        self.cum = self.upper = self.hour = None
        if hourly:
            cums = [_cum(days[d]["hours"]) for d in refs]
            self.cum = [median([c[h] for c in cums]) for h in range(25)]
            self.upper = [max(c[h] for c in cums) for h in range(25)]
            self.hour = [median([days[d]["hours"][h] for d in refs]) for h in range(24)]


# ── The causes ──────────────────────────────────────────────────────────────

def cause_for(date, campaign_rows, refs, currency="USD"):
    """What moved on `date`: {kind: one|new|many|none, text, movers: [{id, name, cost, usual, excess, share, startDate}]} from the campaign-day rows [{id, name, date, cost, startDate}]."""
    if not campaign_rows:
        return None
    by = {}
    for r in campaign_rows:
        by.setdefault(str(r["id"]), {"id": str(r["id"]), "name": r.get("name") or str(r["id"]), "start": r.get("startDate") or "", "days": {}})
        by[str(r["id"])]["days"][str(r["date"])[:10]] = by[str(r["id"])]["days"].get(str(r["date"])[:10], 0.0) + _f(r.get("cost"))
    iso, ref_iso = _day(date).isoformat(), [d.isoformat() for d in refs]
    movers = []
    for c in by.values():
        cost = c["days"].get(iso, 0.0)
        usual = median([c["days"].get(d, 0.0) for d in ref_iso]) if ref_iso else 0.0
        if cost - usual > 0:
            movers.append({"id": c["id"], "name": c["name"], "cost": round(cost, 2), "usual": round(usual, 2), "excess": round(cost - usual, 2), "startDate": c["start"]})
    movers.sort(key=lambda x: -x["excess"])
    total = sum(x["excess"] for x in movers)
    if total <= 0:
        return {"kind": "none", "text": "", "movers": []}
    for x in movers:
        x["share"] = round(x["excess"] / total, 3)
    movers = movers[:MAX_MOVERS]
    top = movers[0]
    fresh = [x for x in movers if _recent(x["startDate"], date) and x["share"] >= 0.25]
    if fresh:
        x = fresh[0]
        return {"kind": "new", "movers": movers, "text": "A campaign started on %s is part of it: \"%s\" spent %s (%d%% of the excess)." % (x["startDate"], x["name"], money(x["cost"], currency), round(x["share"] * 100))}
    if top["share"] >= CAUSE_SHARE:
        return {"kind": "one", "movers": movers, "text": "%d%% of the excess is one campaign: \"%s\" spent %s against a usual %s." % (round(top["share"] * 100), top["name"], money(top["cost"], currency), money(top["usual"], currency))}
    return {"kind": "many", "movers": movers, "text": "It is spread over campaigns: %s." % "; ".join("\"%s\" %s (usual %s)" % (x["name"], money(x["cost"], currency), money(x["usual"], currency)) for x in movers)}


def _recent(start, date):
    """Did a campaign start on `start` within NEW_CAMPAIGN_DAYS days before `date` (or on it)?"""
    try:
        return bool(start) and 0 <= (_day(date) - _day(start)).days <= NEW_CAMPAIGN_DAYS
    except ValueError:
        return False


def changes_text(changes, limit=3):
    """One sentence on the changes made in the account lately (those by someone who made none before come first), or ""."""
    if not changes:
        return ""
    ordered = sorted(changes, key=lambda c: (not c.get("newUser"), str(c.get("at") or "")))[:limit]
    parts = ["%s by %s%s" % (c.get("label") or "a change", c.get("user") or "an unknown user", " (made no change in the days before)" if c.get("newUser") else "") for c in ordered]
    return "Changes lately: %s%s." % ("; ".join(parts), "; and %d more" % (len(changes) - limit) if len(changes) > limit else "")


# ── Findings ────────────────────────────────────────────────────────────────

def _ratio(a, b):
    return None if not b else round(a / b, 2)


def _x(r):
    return "%.1fx" % r if r is not None and r < 10 else ("%dx" % round(r)) if r is not None else "far more than"


def _hh(h):
    return "%02d:00" % h


def _muted(date, saved):
    until = (saved or {}).get("mutedUntil")
    try:
        return bool(until) and _day(date) <= _day(until)
    except ValueError:
        return False


def _finding(kind, date, level, title, detail, next_step, **extra):
    return dict({"kind": kind, "key": _day(date).isoformat(), "level": level, "title": title, "detail": detail, "nextStep": next_step}, **extra)


def _mute(f, saved):
    """The finding as information when its day falls in the days the team muted (a planned push); a ceiling or a drop is never passed through here."""
    if _muted(f["key"], saved):
        return dict(f, level="info", muted=True, title="Expected (muted until %s): %s" % (saved["mutedUntil"], f["title"]))
    return f


def analyse(rows, today, hour, *, currency="USD", sensitivity=None, saved=None, campaigns=None, budget=None, changes=None):
    """One account. `rows` [{date, hour, cost}], `today` the account's own date, `hour` the hour it is there (0-23: the hours before it are whole), `saved` what the team saved for the
    account {usualDaily, maxDaily, minSwing, mutedUntil, note}, `campaigns` [{id, name, date, cost, startDate}] for tracing a spike, `budget` the daily budgets added up (a shared one once),
    `changes` [{at, user, label, newUser}] made in the last days. Returns the account's verdict (see the module docstring)."""
    today = _day(today)
    saved = saved or {}
    mid, high = SENSITIVITY[sensitivity_of(sensitivity)]
    days = series_of(rows, today)
    spent_days = [d for d, v in days.items() if v["cost"] > 0]
    first_spend = min(spent_days) if spent_days else today
    yesterday = today - dt.timedelta(days=1)
    hourly = days[today]["hours"] is not None
    cur = days[today]["cost"]
    cum_t = _cum(days[today]["hours"]) if hourly else None
    spent_now = cum_t[min(hour, 24)] if hourly else cur
    ref_t, basis = reference(days, today, first_spend)
    ref_y, _basis_y = reference(days, yesterday, first_spend)
    u_t, u_y = Usual(days, ref_t), Usual(days, ref_y)
    saved_usual = _f(saved.get("usualDaily")) or None
    findings, note = [], []
    usual_day = u_t.day if u_t.n >= MIN_REF_DAYS else (saved_usual or 0.0)
    judged = u_t.n >= MIN_REF_DAYS or bool(saved_usual)
    floor = floor_of(usual_day or u_y.day, saved.get("minSwing"))
    swing = float(saved.get("minSwing") or MIN_SWING)                       # the smallest swing worth a word, before the tenth of a usual day is added
    usual_by_now = u_t.cum[min(hour, 24)] if (u_t.cum is not None and u_t.n >= MIN_REF_DAYS) else None
    pace = None

    def trace(date, refs):
        c = cause_for(date, campaigns, refs, currency) if campaigns else None
        extra = [c["text"]] if c and c.get("text") else []
        t = changes_text(changes)
        return c, " ".join(extra + ([t] if t else []))

    # today so far
    if judged and hour >= 1:
        lvl, by_now_lvl = grade(spent_now, usual_day, floor, mid, high), None
        if usual_by_now is not None and hour >= MIN_HOUR_FOR_PACE:
            eff = max(usual_by_now, 0.15 * usual_day)
            by_now_lvl = grade(spent_now, eff, floor, mid, high)
            pace = round(usual_day * spent_now / eff) if eff else None
        level = by_now_lvl or lvl                                     # the pace by this hour is never milder than the whole day's: the day's total is the larger usual
        if level and LEVELS[level] >= LEVELS["low"]:
            if by_now_lvl and (usual_by_now or 0) > 0:
                head = "Spending %s its usual pace today" % _x(_ratio(spent_now, max(usual_by_now, 0.15 * usual_day)))
                line = "%s spent so far today (to %s), against a usual %s by this time of day; a usual day is %s, so the day would end near %s." % (
                    money(spent_now, currency), _hh(hour), money(usual_by_now, currency), money(usual_day, currency), money(pace, currency))
            else:
                head = "Already spent %s a usual day today" % _x(_ratio(spent_now, usual_day))
                line = "%s spent so far today against a usual day of %s%s." % (money(spent_now, currency), money(usual_day, currency), "" if hourly else " (the platform gave no hours, so the pace through the day cannot be judged)")
            started = _started(days, today, hour, u_t, floor) if u_t.n >= MIN_REF_DAYS else None
            c, why = trace(today, ref_t)
            findings.append(_mute(_finding("spike", today, level, head, " ".join(x for x in (line, ("It began around %s." % _hh(started)) if started is not None else "", why) if x),
                                           "Open the campaigns that spent most today and check their budgets, bids and the change history now.",
                                           spent=round(spent_now, 2), usual=round(usual_by_now if by_now_lvl else usual_day, 2), usualDay=round(usual_day, 2), ratio=_ratio(spent_now, usual_by_now if by_now_lvl else usual_day),
                                           pace=pace, cause=c, started=started, day="today"), saved))
    elif not judged:
        note.append("Fewer than %d days of spend are known, so a usual day cannot be worked out yet. Save the usual daily spend for this account to judge it now." % MIN_REF_DAYS)

    # yesterday
    y_cost = days[yesterday]["cost"]
    if u_y.n >= MIN_REF_DAYS or saved_usual:
        y_usual = u_y.day if u_y.n >= MIN_REF_DAYS else saved_usual
        lvl = grade(y_cost, y_usual, floor_of(y_usual, saved.get("minSwing")), mid, high)
        if lvl and LEVELS[lvl] >= LEVELS["low"]:
            c, why = trace(yesterday, ref_y)
            findings.append(_mute(_finding("spike", yesterday, lvl, "Spent %s a usual day yesterday" % _x(_ratio(y_cost, y_usual)),
                                           " ".join(x for x in ("%s spent yesterday against a usual %s." % (money(y_cost, currency), money(y_usual, currency)), why) if x),
                                           "Open the campaigns that spent most yesterday and check what changed.", spent=round(y_cost, 2), usual=round(y_usual, 2), usualDay=round(y_usual, 2),
                                           ratio=_ratio(y_cost, y_usual), cause=c, day="yesterday"), saved))
        # a drop yesterday
        if y_usual >= DROP_MIN_FLOORS * swing and y_cost <= DROP_SHARE_DAY * y_usual:
            zero = y_cost <= 0
            findings.append(_finding("drop", yesterday, "high" if zero and y_usual >= DROP_HIGH_FLOORS * swing else "medium",
                                     "Spent almost nothing yesterday" if not zero else "Spent nothing yesterday",
                                     "%s spent yesterday against a usual %s." % (money(y_cost, currency), money(y_usual, currency)),
                                     "Check the account's billing, disapproved ads, paused campaigns and budgets that ran out.", spent=round(y_cost, 2), usual=round(y_usual, 2), ratio=_ratio(y_cost, y_usual), day="yesterday"))

    # a surge in the last whole hours
    if hourly and u_t.hour is not None and u_t.n >= MIN_REF_DAYS and hour >= 2:
        k = min(SURGE_HOURS, hour)
        last = sum(days[today]["hours"][hour - k:hour])
        usual_last = median([sum(days[d]["hours"][hour - k:hour]) for d in ref_t])
        s_floor = max(swing / 2.0, 0.08 * usual_day)
        lvl = grade(last, usual_last, s_floor, SURGE_MID * mid / SENSITIVITY[DEFAULT_SENSITIVITY][0], SURGE_HIGH * high / SENSITIVITY[DEFAULT_SENSITIVITY][1])
        if lvl and LEVELS[lvl] >= LEVELS["low"]:
            c, why = trace(today, ref_t)
            findings.append(_mute(_finding("surge", today, lvl, "Spending %s its usual in the last %d hours" % (_x(_ratio(last, usual_last)), k),
                                           " ".join(x for x in ("%s spent from %s to %s, against a usual %s for those hours." % (money(last, currency), _hh(hour - k), _hh(hour), money(usual_last, currency)), why) if x),
                                           "Check right now for a budget raised, a new campaign, a changed bid strategy or a user who should not have access.",
                                           spent=round(last, 2), usual=round(usual_last, 2), ratio=_ratio(last, usual_last), hours=k, cause=c, day="today"), saved))

    # a ceiling the team saved
    ceiling = _f(saved.get("maxDaily"))
    if ceiling > 0:
        for date, cost, word in ((today, spent_now, "today"), (yesterday, y_cost, "yesterday")):
            if cost >= ceiling:
                findings.append(_finding("ceiling", date, "high", "Spent %s %s, above the %s a day the team set" % (money(cost, currency), word, money(ceiling, currency)),
                                         "The ceiling saved for this account is %s a day and %s %s." % (money(ceiling, currency), money(cost, currency), "has been spent so far today" if word == "today" else "was spent yesterday"),
                                         "Check the budgets and the change history, and tell the account manager.", spent=round(cost, 2), ceiling=ceiling, day=word))

    # more than the budgets allow
    if budget and budget > 0:
        for date, cost, word in ((today, spent_now, "today"), (yesterday, y_cost, "yesterday")):
            r = cost / budget
            if r >= BUDGET_OVER and cost - budget >= floor:
                findings.append(_mute(_finding("budget", date, "high" if r >= BUDGET_HIGH else "medium", "Spent %s the account's daily budgets %s" % (_x(r), word),
                                               "%s spent %s against daily budgets that add up to %s. Google may spend up to twice a campaign's budget on one day, so this is more than a budget alone allows: "
                                               "a budget raised, a shared budget, or a campaign paused after it had spent." % (money(cost, currency), word, money(budget, currency)),
                                               "Compare the budgets with the change history and the campaigns that spent.", spent=round(cost, 2), budget=round(budget, 2), ratio=round(r, 2), day=word), saved))

    # a drop so far today
    if hourly and u_t.cum is not None and u_t.n >= MIN_REF_DAYS and hour >= DROP_FROM_HOUR:
        through = hour - LAG_HOURS
        seen, usual = cum_t[through], u_t.cum[through]
        if usual >= DROP_MIN_FLOORS * swing and seen <= DROP_SHARE * usual:
            zero = seen <= 0
            findings.append(_finding("drop", today, "high" if zero and usual >= DROP_HIGH_FLOORS * swing else "medium", "Spending has stopped today" if zero else "Spending is far below its usual today",
                                     "%s spent by %s against a usual %s by then (the last %d hours are left out, the platform reports them late)." % (money(seen, currency), _hh(through), money(usual, currency), LAG_HOURS),
                                     "Check billing, disapproved ads, paused campaigns and budgets that ran out.", spent=round(seen, 2), usual=round(usual, 2), ratio=_ratio(seen, usual), day="today"))

    findings.sort(key=lambda f: (-LEVELS[f["level"]], f["kind"], f["key"]))
    top = max((f["level"] for f in findings), key=lambda l: LEVELS[l], default=None)
    over = any(f["kind"] != "drop" and LEVELS[f["level"]] >= LEVELS["medium"] for f in findings)
    under = any(f["kind"] == "drop" and LEVELS[f["level"]] >= LEVELS["medium"] for f in findings)
    if not spent_days:
        status = "none"
    elif over:
        status = "over"
    elif under:
        status = "under"
    elif not judged:
        status = "new"
    else:
        status = "ok"
    curve = None
    if hourly and u_t.cum is not None:
        curve = {"today": [round(x, 2) for x in cum_t[:min(hour, 24) + 1]], "usual": [round(x, 2) for x in u_t.cum], "upper": [round(x, 2) for x in u_t.upper], "hour": hour}
    return {"status": status, "level": top, "findings": findings, "notes": note, "usualDay": round(usual_day, 2), "basis": basis, "refDays": u_t.n, "floor": round(floor, 2), "sensitivity": sensitivity_of(sensitivity),
            "today": {"date": today.isoformat(), "cost": round(spent_now, 2), "through": hour, "hourly": hourly, "usualByNow": None if usual_by_now is None else round(usual_by_now, 2),
                      "ratio": _ratio(spent_now, usual_by_now if usual_by_now else usual_day), "pace": pace},
            "yesterday": {"date": yesterday.isoformat(), "cost": round(y_cost, 2), "usual": round(u_y.day, 2), "ratio": _ratio(y_cost, u_y.day)},
            "days": [{"date": d.isoformat(), "cost": round(v["cost"], 2), "partial": d == today} for d, v in sorted(days.items())],
            "curve": curve, "budget": None if not budget else round(budget, 2), "saved": {k: saved.get(k) for k in ("usualDaily", "maxDaily", "minSwing", "mutedUntil", "note")}, "currency": currency}


def _started(days, today, hour, usual, floor):
    """The first whole hour of today that spent far more than that hour usually does, or None."""
    if usual.hour is None or days[today]["hours"] is None:
        return None
    for h in range(min(hour, 24)):
        spent, typical = days[today]["hours"][h], usual.hour[h]
        if spent >= max(2.5 * typical, typical + floor / 3.0):
            return h
    return None


def snapshot_of(a):
    """The figures of one analysed account that the monitor keeps for the history."""
    return {"status": a["status"], "level": a["level"], "today": a["today"]["cost"], "usualByNow": a["today"]["usualByNow"], "ratio": a["today"]["ratio"], "usualDay": a["usualDay"],
            "yesterday": a["yesterday"]["cost"], "findings": len(a["findings"])}


def rank_key(a):
    return (_STATUS_RANK.get(a["status"], 9), -LEVELS.get(a.get("level") or "", -1), -(a["today"].get("ratio") or 0), (a.get("name") or "").lower())
