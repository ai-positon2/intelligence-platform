"""Pacing math: ideal vs actual, schedule-aware."""
from __future__ import annotations

import calendar
import datetime as dt
import re

import config

# "Days Ads are Running" strings seen in the Budget Tracker
_DAYNUM = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
           "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
           "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}


def parse_schedule(text: str | None) -> set[int]:
    """'Monday - Friday' -> {0,1,2,3,4};  'Monday, Wednesday, Thursday' -> {0,2,3}.

    Unparseable / empty -> all 7 days.
    """
    if not text or not str(text).strip():
        return set(range(7))
    s = str(text).lower().replace("–", "-").replace("—", "-")

    # Range form: "monday - friday", "monday -sunday"
    m = re.search(r"([a-z]+)\s*-\s*([a-z]+)", s)
    if m and m.group(1) in _DAYNUM and m.group(2) in _DAYNUM:
        a, b = _DAYNUM[m.group(1)], _DAYNUM[m.group(2)]
        return {d % 7 for d in range(a, (b if b >= a else b + 7) + 1)}

    # List form: "monday, wednesday, thursday"
    found = {_DAYNUM[t] for t in re.findall(r"[a-z]+", s) if t in _DAYNUM}
    return found or set(range(7))


def month_days(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def active_days(year: int, month: int, schedule: set[int],
                lo: int = 1, hi: int | None = None) -> int:
    """Count scheduled days in [lo, hi] of the month (inclusive)."""
    hi = hi or month_days(year, month)
    return sum(1 for d in range(lo, hi + 1)
               if dt.date(year, month, d).weekday() in schedule)


class Clock:
    """Everything time-related for one run, in one place."""

    def __init__(self, as_of: dt.date, data_through: dt.date | None = None):
        self.as_of = as_of
        self.year, self.month = as_of.year, as_of.month
        self.dim = month_days(self.year, self.month)
        # Complete days of data we are allowed to judge on. Today is never
        # complete -- some accounts' near-real-time reporting can already
        # show a partial row for it by the time we run -- so never let this
        # exceed yesterday, however fresh the raw data claims to be.
        yesterday = as_of - dt.timedelta(days=1)
        self.data_through = min(data_through, yesterday) if data_through else yesterday
        self.elapsed = as_of.day - 1                       # client's rule
        self.remaining = self.dim - self.elapsed
        # The elapsed to measure a reporting *lag* against, kept separate
        # from `elapsed` itself once `with_data_through` narrows the latter
        # for one account — see lag_days().
        self._lag_reference_elapsed = self.elapsed

    # --- benchmarks -------------------------------------------------------
    def ideal_calendar(self) -> float:
        return self.elapsed / self.dim

    def ideal_schedule(self, schedule: set[int]) -> float:
        tot = active_days(self.year, self.month, schedule)
        if not tot:
            return 0.0
        done = active_days(self.year, self.month, schedule, 1, max(self.elapsed, 1)) \
            if self.elapsed else 0
        return done / tot

    def ideal(self, schedule: set[int], baseplan_pct: float | None = None) -> float:
        mode = config.PACING_BENCHMARK
        if mode == "schedule":
            return self.ideal_schedule(schedule)
        if mode == "baseplan" and baseplan_pct is not None:
            return baseplan_pct
        return self.ideal_calendar()

    # --- remaining capacity ----------------------------------------------
    def active_days_left(self, schedule: set[int]) -> int:
        """Scheduled days from today through month end, inclusive of today."""
        return active_days(self.year, self.month, schedule, self.as_of.day, self.dim)

    def active_days_done(self, schedule: set[int]) -> int:
        return active_days(self.year, self.month, schedule, 1, self.elapsed) \
            if self.elapsed else 0

    def lag_days(self, schedule: set[int]) -> int:
        """Scheduled days between data_through and yesterday — the reporting gap."""
        if self.data_through >= self.as_of - dt.timedelta(days=1):
            return 0
        return active_days(self.year, self.month, schedule,
                           self.data_through.day + 1, self._lag_reference_elapsed)

    # --- per-account data lag ----------------------------------------------
    def with_data_through(self, data_through: dt.date) -> "Clock":
        """A clock judging pacing as of one account's own last complete day,
        when its feed lags behind the rest of the portfolio (e.g. a
        Supermetrics sync gap that hit this account but not others). The
        portfolio-wide clock already reports `data_through` as the max date
        seen across every account, which can hide a real per-account gap —
        this narrows `elapsed` to what THIS account's own data actually
        covers, so ideal-pacing and run-rate math isn't judged against days
        it has no data for.

        `active_days_left` deliberately keeps using the true calendar day
        (`as_of`), not this narrowed elapsed — a lagging report doesn't
        change how many real days are left in the month to spend.
        """
        if data_through >= self.data_through:
            return self
        c = Clock(self.as_of, data_through)
        c.elapsed = min(self.elapsed, data_through.day)
        c.remaining = self.dim - c.elapsed
        return c


def verdict(actual_pct: float, ideal_pct: float) -> tuple[str, float]:
    """-> (UNDER | ON_PACE | OVER, deviation in percentage points)."""
    dev_pp = (actual_pct - ideal_pct) * 100
    if dev_pp > config.UPPER_TOL_PP:
        return "OVER", dev_pp
    if dev_pp < -config.LOWER_TOL_PP:
        return "UNDER", dev_pp
    return "ON_PACE", dev_pp
