"""Paid Marketing Agents: Seasonality Analysis.

When in the year an ad account earns, and what to do about it. The page reads up to three years of the account's daily
numbers from Google Ads, Meta Ads or both (the stored days the other agents share, tracker/paid_gads.py and paid_meta.py),
and everything below is worked out here, in plain arithmetic, so every figure is one a test checks and no model is asked
to invent a pattern:

  * a seasonal INDEX for each calendar month (100 is an average month) for spend, clicks, conversions, value and the
    rates (conversion rate, cost per click, cost per conversion, return on ad spend). Each month is divided by a centred
    12-month moving average of the account's own history first, so an account that grew all year does not make every late
    month look like a peak. It needs 13 months in a row to say anything about the year; with less, the page says so and
    shows what it can (the trend, the days of the week);
  * the same for the days of the week, measured against each week's own average;
  * the effect of the retail holidays (Black Friday, Valentine's Day, ...) against the stretch of ordinary days before;
  * this year against last year;
  * the next three months: the expected index and a starting point for the budget. A starting point, not a rule.

What it does not know: the account's own promotions and outages (a month with a big sale looks like a season), and the
holidays of any country but the United States (the calendar is the US retail one; an account elsewhere simply shows
little effect for them).
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from . import paid_gads as g
from . import paid_meta as meta

log = logging.getLogger(__name__)

LOOKBACKS = (24, 36)               # months of history the page can ask for
DEFAULT_MONTHS = 36               # 36 whole months give every calendar month two years to be judged on, 24 give one
SLICE_DAYS = 400                   # one read of Google Ads never spans more than this
SLICE_DAYS_META = 93               # Meta is read 31 days at a time and one year of a busy account took 80 s: keep a request to about three of them
REFRESH_DAYS = 45                  # Refresh re-reads this many latest days: older ones no longer change
MIN_YEARLY_MONTHS = 13             # months in a row needed to see a yearly pattern at all
ACTIVE_SHARE = 0.6                 # a month with spend on fewer of its days than this was mostly paused: it is left out
EVENT_ACTIVE_SHARE = 0.8
PEAK = 110                         # an index at or above this is a peak, at or below TROUGH a trough
TROUGH = 90
DEAR = 105                         # a cost per conversion index at or above this means conversions cost more than usual
GAP = 15                           # spend and conversions this many index points apart: spend did not follow demand
BUDGET_CAP_UP, BUDGET_CAP_COSTLY, BUDGET_CAP_DOWN = 40, 20, 30

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# (key, label, kind): "sum" metrics are per day, "ratio" metrics are numerator / denominator of the period's totals.
SUMS = ["impr", "clicks", "cost", "conv", "value"]
RATIOS = {"ctr": ("clicks", "impr"), "cpc": ("cost", "clicks"), "cvr": ("conv", "clicks"), "cpa": ("cost", "conv"),
          "roas": ("value", "cost")}
KEYS = SUMS + list(RATIOS)
LABEL = {"impr": "Impressions", "clicks": "Clicks", "cost": "Spend", "conv": "Conversions", "value": "Conversion value",
         "ctr": "Click-through rate", "cpc": "Cost per click", "cvr": "Conversion rate", "cpa": "Cost per conversion",
         "roas": "Return on ad spend"}
# Columns of a stored row after [campaign index, day index]; the first five are the same on both platforms.
ROW_AT = {"impr": 0, "clicks": 1, "cost": 2, "conv": 3, "value": 4}


class BadRequest(ValueError):
    pass


# ── Dates ───────────────────────────────────────────────────────────────────

def _d(s):
    return dt.date.fromisoformat(s)


def _add_months(first, n):
    """The first day of the month n months from `first` (itself a first of a month)."""
    y, m = divmod(first.year * 12 + first.month - 1 + n, 12)
    return dt.date(y, m + 1, 1)


def window(months, today=None):
    """(start, end) for `months` whole months of history and the month under way: the end is yesterday (today is not over)."""
    today = today or dt.date.today()
    end = today - dt.timedelta(days=1)
    return _add_months(end.replace(day=1), -months).isoformat(), end.isoformat()


def slices(start, end, size=SLICE_DAYS):
    """[(first, last), ...] covering start..end in runs of at most `size` days, as even as can be, the NEWEST first, so a page
    that reads them one after another has the latest year on screen first."""
    a, b = _d(start), _d(end)
    total = (b - a).days + 1
    n = max(1, -(-total // size))
    out, last = [], b
    for k in range(n):
        length = total // n + (1 if k < total % n else 0)
        first = last - dt.timedelta(days=length - 1)
        out.append((first.isoformat(), last.isoformat()))
        last = first - dt.timedelta(days=1)
    return out


def check_months(raw):
    try:
        months = int(raw) if raw not in (None, "") else DEFAULT_MONTHS
    except (TypeError, ValueError):
        raise BadRequest("months should be one of %s" % ", ".join(str(m) for m in LOOKBACKS))
    if months not in LOOKBACKS:
        raise BadRequest("months should be one of %s" % ", ".join(str(m) for m in LOOKBACKS))
    return months


# ── Reading the platform ────────────────────────────────────────────────────

def daily_totals(shape):
    """One row a day, all campaigns added, from a stored-days shape (campaigns, days, rows[ci, di, numbers...])."""
    n = len(shape["days"])
    out = {"days": list(shape["days"])}
    for k in SUMS:
        out[k] = [0.0] * n
    for _ci, di, *nums in shape["rows"]:
        for k, at in ROW_AT.items():
            out[k][di] += nums[at]
    return out


def _join(parts):
    """Day columns of several reads laid end to end (oldest first)."""
    out = {"days": []}
    for k in SUMS:
        out[k] = []
    for p in parts:
        out["days"].extend(p["days"])
        for k in SUMS:
            out[k].extend(p[k])
    return out


def _add(a, b):
    if a["days"] != b["days"]:
        raise ValueError("the two platforms were read over different days")
    out = {"days": a["days"]}
    for k in SUMS:
        out[k] = [round(x + y, 6) for x, y in zip(a[k], b[k])]
    return out


def slice_size(accts):
    """Days in one read: Meta, alone or with Google, in small slices (the page waits on each), Google in whole years."""
    return SLICE_DAYS_META if any(a.get("platform") == "meta" for a in accts) else SLICE_DAYS


def read_part(store, acct, first, last, *, force=False, notes=None, stamps=None, http=None):
    """Daily totals for one platform's account over first..last (a slice)."""
    if acct.get("platform") == "meta":
        shape = meta.campaign_days(store, acct["id"], first, last, currency=acct.get("currency") or "USD", force=force,
                                   notes=notes, stamps=stamps, http=http)
    else:
        shape = g.campaign_days(store, acct["id"], first, last, force=force, notes=notes, stamps=stamps, http=http,
                                account=True)
    return daily_totals(shape)


def read(store, accts, start, end, *, force=False, http=None):
    """(daily, problems, notes, stamps) for one account or a Google and a Meta account together, over start..end, read
    in slices. A platform that cannot be read is left out and named in `problems`; every one failing raises the first error.
    Quota problems and the like surface as the platform's own AdsError."""
    notes, stamps, problems = [], [], []
    plan = slices(start, end, slice_size(accts))[::-1]               # oldest first, to lay the days end to end
    got = []
    for acct in accts:
        who = "Meta Ads" if acct.get("platform") == "meta" else "Google Ads"
        try:
            parts = [read_part(store, acct, a, b, force=force, notes=notes, stamps=stamps, http=http) for a, b in plan]
        except g.OverQuota:
            raise
        except g.AdsError as e:
            if len(accts) == 1:
                raise
            problems.append("%s could not be read, so these numbers leave it out: %s" % (who, e))
            continue
        got.append(_join(parts))
    if not got:
        raise g.AdsError("Neither platform could be read.")
    daily = got[0]
    for more in got[1:]:
        daily = _add(daily, more)
    return daily, problems, notes, stamps


def warm(store, accts, start, end, part, *, force=False, http=None):
    """Read one slice of the range into the stored days (nothing is analysed) and say how many there are. The page calls
    this slice by slice, newest first, so no single request is long and a failure keeps what was already read. With two
    platforms, one that cannot be read is named in `problems` and the other still counts."""
    plan = slices(start, end, slice_size(accts))
    if not 0 <= part < len(plan):
        raise BadRequest("part is out of range")
    first, last = plan[part]
    notes, stamps, problems, ok = [], [], [], 0
    for acct in accts:
        try:
            read_part(store, acct, first, last, force=force, notes=notes, stamps=stamps, http=http)
            ok += 1
        except g.OverQuota:
            raise
        except g.AdsError as e:
            if len(accts) == 1:
                raise
            problems.append("%s could not be read: %s" % ("Meta Ads" if acct.get("platform") == "meta" else "Google Ads", e))
    if not ok:
        raise g.AdsError("Neither platform could be read.")
    return {"part": part, "parts": len(plan), "start": first, "end": last, "stale": min(notes) if notes else None,
            "fetchedAt": g.oldest(stamps), "problems": problems}


# ── Arithmetic ──────────────────────────────────────────────────────────────

def _rnd(v, nd=1):
    return None if v is None else round(v, nd)


def _val(key, tot, days):
    """One metric from period totals: per day for a sum, a ratio of totals for a rate (None when the denominator is 0)."""
    if key in RATIOS:
        num, den = RATIOS[key]
        return tot[num] / tot[den] if tot[den] > 0 else None
    return tot[key] / days if days else None


def _totals(daily, idx):
    t = {k: 0.0 for k in SUMS}
    for i in idx:
        for k in SUMS:
            t[k] += daily[k][i]
    return t


def month_buckets(daily, first):
    """Calendar months from the month of day index `first` to the last day: each with its sums, the days it has in the
    data, the days it spent on, and whether the whole month is in the data. Months with no spend at all inside the
    account's life are kept (they were paused), they simply carry no weight later."""
    out, days = [], daily["days"]
    cur = None
    for i in range(first, len(days)):
        d = _d(days[i])
        if cur is None or (cur["y"], cur["m"]) != (d.year, d.month):
            cur = {"y": d.year, "m": d.month, "idx": [], "active": 0}
            out.append(cur)
        cur["idx"].append(i)
        if daily["cost"][i] > 0:
            cur["active"] += 1
    for b in out:
        t = _totals(daily, b["idx"])
        b.update(t)
        b["days"] = len(b["idx"])
        dim = (_add_months(dt.date(b["y"], b["m"], 1), 1) - dt.date(b["y"], b["m"], 1)).days
        b["full"] = b["days"] == dim
        b["usable"] = b["full"] and b["active"] >= ACTIVE_SHARE * dim
        del b["idx"]
    return out


def seasonal_index(series):
    """The seasonal index of each calendar month from `series`, [(year, month, value or None), ...] in consecutive months.

    A month's value is divided by the centred 12-month moving average around it (half weights at the two ends of the 13
    months), which removes growth and decline; the ratios of the same calendar month in different years are averaged.
    When all twelve months have a ratio the twelve are scaled to average exactly 100. Returns {month: {"index", "n",
    "years": [{"y", "index"}], "agree"}} for the months that have at least one ratio; `agree` is how many of the years sit
    on the same side of 100 as the average."""
    vals = [v for _y, _m, v in series]
    ratios = {m: [] for m in range(1, 13)}
    for i in range(6, len(vals) - 6):
        w = vals[i - 6:i + 7]
        if vals[i] is None or any(x is None for x in w):
            continue
        ma = (0.5 * w[0] + sum(w[1:12]) + 0.5 * w[12]) / 12
        if ma <= 0:
            continue
        ratios[series[i][1]].append((series[i][0], vals[i] / ma))
    means = {m: sum(r for _y, r in rs) / len(rs) for m, rs in ratios.items() if rs}
    scale = 1.0
    if len(means) == 12:
        scale = 12 / sum(means.values())
    out = {}
    for m, mean in means.items():
        idx = mean * scale * 100
        years = [{"y": y, "index": round(r * scale * 100, 1)} for y, r in ratios[m]]
        side = [(y["index"] >= 100) == (idx >= 100) for y in years]
        out[m] = {"index": round(idx, 1), "n": len(years), "years": years, "agree": sum(side)}
    return out


def month_series(buckets, key):
    """[(year, month, value)] over the consecutive months first..last that are usable (None for a month that is not)."""
    if not buckets:
        return []
    out = []
    for b in buckets:
        v = None
        if b["usable"]:
            v = _val(key, b, b["days"])
        out.append((b["y"], b["m"], v))
    return out


def month_indexes(buckets):
    """{metric: [12 entries for January..December: {"m", "index", "n", "years", "agree"} or {"m", "index": None}]}."""
    out = {}
    series_ok = bool(buckets)
    for key in KEYS:
        res = seasonal_index(month_series(buckets, key)) if series_ok else {}
        out[key] = [dict(res[m], m=m) if m in res else {"m": m, "index": None, "n": 0, "years": [], "agree": 0}
                    for m in range(1, 13)]
    return out


def usable_run(buckets):
    """The longest run of consecutive usable months."""
    best = run = 0
    for b in buckets:
        run = run + 1 if b["usable"] else 0
        best = max(best, run)
    return best


def week_buckets(daily, first):
    """Whole Monday-to-Sunday weeks inside the data from day index `first`, with their sums and days spent on."""
    days = daily["days"]
    out, i = [], first
    while i < len(days) and _d(days[i]).weekday() != 0:
        i += 1
    while i + 7 <= len(days):
        idx = list(range(i, i + 7))
        t = _totals(daily, idx)
        d0 = _d(days[i])
        iso = d0.isocalendar()
        t.update({"y": iso[0], "w": iso[1], "start": days[i], "idx": idx,
                  "active": sum(1 for j in idx if daily["cost"][j] > 0)})
        out.append(t)
        i += 7
    return out


def dow_indexes(daily, weeks):
    """({metric: [Monday..Sunday index or None]}, weeks used) from the latest 52 weeks that spent on at least 5 days, or
    (None, weeks) when there are fewer than 8. A sum is each day against its own week's average (so growth over the year does
    not tilt it); a rate is that weekday's total over its total of the other, against the same for all days."""
    use = [w for w in weeks if w["active"] >= 5][-52:]
    if len(use) < 8:
        return None, len(use)
    sums = {k: [0.0] * 7 for k in SUMS}
    rel = {k: [0.0] * 7 for k in SUMS}
    counted = {k: 0 for k in SUMS}
    for w in use:
        for j, di in enumerate(w["idx"]):
            for k in SUMS:
                sums[k][j] += daily[k][di]
        for k in SUMS:
            mean = w[k] / 7
            if mean > 0:
                counted[k] += 1
                for j, di in enumerate(w["idx"]):
                    rel[k][j] += daily[k][di] / mean
    out = {k: [round(100 * rel[k][j] / counted[k], 1) if counted[k] else None for j in range(7)] for k in SUMS}
    for key, (num, den) in RATIOS.items():
        tn, td = sum(sums[num]), sum(sums[den])
        overall = tn / td if td > 0 else None
        row = []
        for j in range(7):
            r = sums[num][j] / sums[den][j] if sums[den][j] > 0 else None
            row.append(round(100 * r / overall, 1) if r is not None and overall else None)
        out[key] = row
    return out, len(use)


# ── Holidays ────────────────────────────────────────────────────────────────

def easter(year):
    """Easter Sunday (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    gg = (b - f + 1) // 3
    h = (19 * a + b - d - gg + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = (h + el - 7 * m + 114) % 31 + 1
    return dt.date(year, month, day)


def nth_weekday(year, month, weekday, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
    return d


def last_weekday(year, month, weekday):
    d = _add_months(dt.date(year, month, 1), 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def events_of(year):
    """[(key, name, first day, last day)] of the US retail calendar for a year."""
    day = dt.timedelta(days=1)
    thanks = nth_weekday(year, 11, 3, 4)
    mothers = nth_weekday(year, 5, 6, 2)
    fathers = nth_weekday(year, 6, 6, 3)
    memorial = last_weekday(year, 5, 0)
    labor = nth_weekday(year, 9, 0, 1)
    ea = easter(year)
    return [
        ("valentines", "Valentine's Day run-up", dt.date(year, 2, 7), dt.date(year, 2, 14)),
        ("easter", "Easter week", ea - 7 * day, ea),
        ("mothers", "Mother's Day run-up", mothers - 7 * day, mothers),
        ("memorial", "Memorial Day weekend", memorial - 3 * day, memorial),
        ("fathers", "Father's Day run-up", fathers - 7 * day, fathers),
        ("july4", "July 4th", dt.date(year, 7, 1), dt.date(year, 7, 4)),
        ("school", "Back to school", dt.date(year, 8, 1), dt.date(year, 8, 28)),
        ("labor", "Labor Day weekend", labor - 3 * day, labor),
        ("halloween", "Halloween run-up", dt.date(year, 10, 24), dt.date(year, 10, 31)),
        ("prebf", "Week before Thanksgiving", thanks - 7 * day, thanks - day),
        ("bfcm", "Thanksgiving to Cyber Monday", thanks, thanks + 4 * day),
        ("xmasrun", "Christmas run-up", dt.date(year, 12, 15), dt.date(year, 12, 24)),
        ("xmasweek", "Between Christmas and New Year", dt.date(year, 12, 26), dt.date(year, 12, 31)),
    ]


def event_effects(daily, first, years):
    """What each holiday did to the account. For every year the window is fully inside the data and the account spent on
    at least 80% of its days, the window's per-day spend, conversions and rates are set against the 28 ordinary days before
    it (days that belong to no holiday, ending 8 days before the window so the run-up is not in the baseline). Returns
    a list, strongest conversion lift first, of {key, name, n, convLift, spendLift, cpaChange, cvrChange, years: [...]}."""
    days = daily["days"]
    pos = {d: i for i, d in enumerate(days)}
    lo, hi = _d(days[first]), _d(days[-1])
    window_days = set()
    allev = {y: events_of(y) for y in range(min(years) - 1, max(years) + 2)}
    for evs in allev.values():
        for _k, _n, a, b in evs:
            for i in range((b - a).days + 1):
                window_days.add(a + dt.timedelta(days=i))
    out = {}
    for y in years:
        for key, name, a, b in allev[y]:
            if a < lo or b > hi:
                continue
            idx = [pos[(a + dt.timedelta(days=i)).isoformat()] for i in range((b - a).days + 1)]
            if sum(1 for i in idx if daily["cost"][i] > 0) < EVENT_ACTIVE_SHARE * len(idx):
                continue
            base, d = [], a - dt.timedelta(days=8)
            while len(base) < 28 and d >= lo and (a - d).days <= 70:
                if d not in window_days and daily["cost"][pos[d.isoformat()]] > 0:
                    base.append(pos[d.isoformat()])
                d -= dt.timedelta(days=1)
            if len(base) < 14:
                continue
            w, bs = _totals(daily, idx), _totals(daily, base)
            wn, bn = len(idx), len(base)

            def lift(k):
                return None if bs[k] <= 0 else (w[k] / wn) / (bs[k] / bn) - 1

            def change(key2):
                x, z = _val(key2, w, wn), _val(key2, bs, bn)
                return None if x is None or not z else x / z - 1

            ev = out.setdefault(key, {"key": key, "name": name, "years": []})
            ev["years"].append({"y": y, "start": a.isoformat(), "end": b.isoformat(), "convLift": _rnd(lift("conv"), 3),
                                "spendLift": _rnd(lift("cost"), 3), "cpaChange": _rnd(change("cpa"), 3),
                                "cvrChange": _rnd(change("cvr"), 3)})
    res = []
    for ev in out.values():
        for k in ("convLift", "spendLift", "cpaChange", "cvrChange"):
            vals = [x[k] for x in ev["years"] if x[k] is not None]
            ev[k] = _rnd(sum(vals) / len(vals), 3) if vals else None
        ev["n"] = len(ev["years"])
        res.append(ev)
    res.sort(key=lambda e: (e["convLift"] is None, -(e["convLift"] or 0)))
    return res


# ── This year against last ──────────────────────────────────────────────────

def year_over_year(daily, first):
    """The last 4, 13 and 52 weeks against the same weeks a year (52 weeks) earlier, when that earlier stretch is in the
    data and the account spent on at least 80% of its days."""
    days = daily["days"]
    n = len(days)
    out = []
    for label, span in (("Last 4 weeks", 28), ("Last 13 weeks", 91), ("Last 52 weeks", 364)):
        cur = list(range(n - span, n))
        prev = [i - 364 for i in cur]
        if prev[0] < first:
            continue
        if sum(1 for i in prev if daily["cost"][i] > 0) < EVENT_ACTIVE_SHARE * span:
            continue
        a, b = _totals(daily, cur), _totals(daily, prev)

        def ch(x, z):
            return None if not z else x / z - 1
        row = {"label": label, "start": days[cur[0]], "end": days[-1], "prevStart": days[prev[0]], "prevEnd": days[prev[-1]],
               "cur": {k: round(a[k], 2) for k in SUMS}, "prev": {k: round(b[k], 2) for k in SUMS}}
        row["change"] = {k: _rnd(ch(a[k], b[k]), 3) for k in SUMS}
        for key in ("cpa", "roas", "cvr"):
            x, z = _val(key, a, span), _val(key, b, span)
            row["change"][key] = _rnd(None if x is None or not z else x / z - 1, 3)
        out.append(row)
    return out


# ── What to do ──────────────────────────────────────────────────────────────

def _step(change, cap_up, cap_down):
    """Half of the gap to an average month, in steps of 5%, within caps."""
    s = int(round(change * 0.5 / 5.0)) * 5
    return max(-cap_down, min(cap_up, s))


def verdict(conv, cpa):
    """(code, advice, budget change in percent) for a month from its conversion index and its cost-per-conversion index."""
    if conv is None:
        return "unknown", "Not enough history to say.", 0
    gap = conv - 100
    if conv >= PEAK:
        if cpa is None or cpa <= DEAR:
            step = _step(gap, BUDGET_CAP_UP, 0)
            return "peak", "Demand is high and a conversion does not cost more. Raise budgets before the month starts.", step
        step = _step(gap, BUDGET_CAP_COSTLY, 0)
        return "peak_costly", "Demand is high but so is the price of a conversion. Raise budgets in steps and watch cost per conversion.", step
    if conv <= TROUGH:
        step = _step(gap, 0, BUDGET_CAP_DOWN)
        if cpa is not None and cpa >= DEAR:
            return "quiet_costly", "Quiet and dearer than usual. Trim budgets and tighten targeting to protect cost per conversion.", step
        return "quiet", "Fewer conversions come in, at a normal price. Hold budgets in line and do not chase volume.", step
    return "typical", "An ordinary month. Keep budgets as they are.", 0


def upcoming(indexes, effects, today, months=3):
    """The current month and the next two (or `months`): expected index and a starting point for the budget, plus the holidays
    that fall in them with what they did before."""
    first = today.replace(day=1)
    by_key = {e["key"]: e for e in effects}
    out = []
    for k in range(months):
        m0 = _add_months(first, k)
        m = m0.month
        idx = {key: (indexes[key][m - 1] if indexes.get(key) else {}) for key in KEYS}
        conv, cpa = idx["conv"].get("index"), idx["cpa"].get("index")
        code, advice, step = verdict(conv, cpa)
        end = _add_months(m0, 1) - dt.timedelta(days=1)
        evs = []
        for key, name, a, b in events_of(m0.year):
            if b >= max(m0, today) and a <= end:
                h = by_key.get(key)
                evs.append({"key": key, "name": name, "start": a.isoformat(), "end": b.isoformat(),
                            "convLift": h["convLift"] if h else None, "spendLift": h["spendLift"] if h else None,
                            "cpaChange": h["cpaChange"] if h else None})
        out.append({"y": m0.year, "m": m, "name": MONTHS[m], "conv": conv, "cpa": cpa, "cost": idx["cost"].get("index"),
                    "cvr": idx["cvr"].get("index"), "roas": idx["roas"].get("index"), "n": idx["conv"].get("n", 0),
                    "verdict": code, "advice": advice, "budget": step, "events": evs})
    return out


def _pct(x):
    return ("+" if x >= 0 else "-") + "%d%%" % round(abs(x) * 100)


def headlines(indexes, dow, effects, yoy, spend_gap):
    """A few plain sentences from the numbers above. Nothing here is guessed: each is one measured thing."""
    out = []
    conv = [x for x in (indexes.get("conv") or []) if x["index"] is not None]
    if len(conv) >= 6:
        hi, lo = max(conv, key=lambda x: x["index"]), min(conv, key=lambda x: x["index"])
        out.append("Conversions peak in %s (%d, where 100 is an average month) and are slowest in %s (%d)."
                   % (MONTHS[hi["m"]], round(hi["index"]), MONTHS[lo["m"]], round(lo["index"])))
    cpa = [x for x in (indexes.get("cpa") or []) if x["index"] is not None]
    if len(cpa) >= 6:
        cheap, dear = min(cpa, key=lambda x: x["index"]), max(cpa, key=lambda x: x["index"])
        if dear["index"] - cheap["index"] >= 10:
            out.append("A conversion costs least in %s (%d) and most in %s (%d)."
                       % (MONTHS[cheap["m"]], round(cheap["index"]), MONTHS[dear["m"]], round(dear["index"])))
    for m, kind in spend_gap[:2]:
        if kind == "under":
            out.append("In %s conversions ran at %d but spend only at %d: budget did not follow demand."
                       % (MONTHS[m["m"]], round(m["conv"]), round(m["cost"])))
        else:
            out.append("In %s spend ran at %d but conversions only at %d: spend ran ahead of results."
                       % (MONTHS[m["m"]], round(m["cost"]), round(m["conv"])))
    if dow and dow.get("conv") and all(v is not None for v in dow["conv"]):
        best, worst = max(range(7), key=lambda j: dow["conv"][j]), min(range(7), key=lambda j: dow["conv"][j])
        if dow["conv"][best] - dow["conv"][worst] >= 15:
            out.append("%s is the strongest day for conversions (%d) and %s the weakest (%d)."
                       % (DAYS[best], round(dow["conv"][best]), DAYS[worst], round(dow["conv"][worst])))
    for e in [e for e in effects if e["convLift"] is not None][:2]:
        if e["convLift"] >= 0.15:
            bit = ""
            if e["spendLift"] is not None and e["cpaChange"] is not None:
                bit = " (spend %s, cost per conversion %s)" % (_pct(e["spendLift"]), _pct(e["cpaChange"]))
            out.append("%s lifted conversions by %s over the ordinary days before it%s."
                       % (e["name"], _pct(e["convLift"]), bit))
    if yoy:
        y = yoy[min(1, len(yoy) - 1)]
        c = y["change"]["conv"]
        s = y["change"]["cost"]
        if c is not None and s is not None:
            out.append("%s: conversions %s and spend %s against the same weeks a year earlier." % (y["label"], _pct(c), _pct(s)))
    return out


# ── The whole analysis ──────────────────────────────────────────────────────

def analyze(daily, *, today=None):
    """Everything the page draws, from one account's daily totals (see daily_totals)."""
    today = today or dt.date.today()
    days = daily["days"]
    first = next((i for i, c in enumerate(daily["cost"]) if c > 0), None)
    if first is None:
        return {"empty": True, "start": days[0] if days else None, "end": days[-1] if days else None}
    buckets = month_buckets(daily, first)
    run = usable_run(buckets)
    yearly = run >= MIN_YEARLY_MONTHS
    indexes = month_indexes(buckets) if yearly else month_indexes([])
    covered = sorted({x["m"] for x in indexes["conv"] if x["index"] is not None})
    weeks = week_buckets(daily, first)
    dow, dow_weeks = dow_indexes(daily, weeks)
    years = sorted({_d(d).year for d in days[first:]})
    effects = event_effects(daily, first, years) if len(days) - first >= 60 else []
    yoy = year_over_year(daily, first)
    spend_gap = []
    if yearly:
        for m in range(12):
            c, s = indexes["conv"][m]["index"], indexes["cost"][m]["index"]
            if c is None or s is None:
                continue
            if c - s >= GAP:
                spend_gap.append(({"m": m + 1, "conv": c, "cost": s}, "under", c - s))
            elif s - c >= GAP:
                spend_gap.append(({"m": m + 1, "conv": c, "cost": s}, "over", s - c))
        spend_gap.sort(key=lambda x: -x[2])
        spend_gap = [(a, b) for a, b, _ in spend_gap]
    ahead = upcoming(indexes, effects, today) if yearly else []
    note = None
    if not yearly:
        note = ("Seasonality needs at least %d months in a row with spend to tell a yearly pattern from growth, and this "
                "account has %d. The trend and the days of the week are shown; the rest fills in as history builds."
                % (MIN_YEARLY_MONTHS, run))
    elif len(covered) < 12:
        note = ("History covers %d of the 12 calendar months, so the others are left blank. Read the shown months against "
                "each other with care." % len(covered))
    dow_out = None
    if dow:
        dow_out = {"weeks": dow_weeks, "idx": dow}
    return {
        "empty": False, "start": days[first], "end": days[-1], "currency": None,
        "coverage": {"monthsInARow": run, "yearly": yearly, "calendarMonths": covered, "weeks": dow_weeks,
                     "needed": MIN_YEARLY_MONTHS, "note": note},
        "monthly": [{"y": b["y"], "m": b["m"], "days": b["days"], "active": b["active"], "full": b["full"],
                     "usable": b["usable"], **{k: round(b[k], 2) for k in SUMS}} for b in buckets],
        "weekly": [{"y": w["y"], "w": w["w"], "start": w["start"], "active": w["active"], **{k: round(w[k], 2) for k in SUMS}}
                   for w in weeks],
        "index": indexes if yearly else None,
        "dow": dow_out,
        "events": effects,
        "yoy": yoy,
        "upcoming": ahead,
        "headlines": headlines(indexes if yearly else {}, dow, effects, yoy, spend_gap),
        "labels": LABEL,
        "thresholds": {"peak": PEAK, "trough": TROUGH, "dear": DEAR},
    }


def report(store, accts, months, *, today=None, http=None):
    """The analysis for one account, or a Google and a Meta account together, over `months` months of history."""
    today = today or dt.date.today()
    if len(accts) == 2:
        cur = {(a.get("currency") or "USD") for a in accts}
        if len(cur) > 1:
            raise BadRequest("The two accounts bill in different currencies, so they cannot be added together.")
    start, end = window(months, today)
    daily, problems, notes, stamps = read(store, accts, start, end, http=http)
    out = analyze(daily, today=today)
    out["currency"] = accts[0].get("currency") or "USD"
    out["requestedStart"], out["requestedEnd"], out["months"] = start, end, months
    out["warnings"] = problems
    out["stale"] = min(notes) if notes else None
    out["fetchedAt"] = g.oldest(stamps)
    return out


ID_COMBO = re.compile(r"^(\d{10})\+(act_\d+)$")


def parse_account(raw):
    """["<google id>"], ["act_..."] or both of a combined "<google id>+<act_...>" as a list of ids."""
    raw = str(raw or "").strip()
    m = ID_COMBO.match(raw)
    if m:
        return [m.group(1), m.group(2)]
    if g.ID_RE.match(raw) or meta.is_meta(raw):
        return [raw]
    raise BadRequest("account should be a 10-digit Google Ads customer id, a Meta ad account id (act_...) or both joined by +")
