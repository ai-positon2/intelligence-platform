"""Core reallocation engine.

Contract
--------
campaigns : DataFrame with columns
    date, campaign, account, campaign_type, cost, conversions, conv_value,
    impressions, clicks
budgets   : DataFrame with columns
    client, account, allocated_budget, target_cpa, status, days_running

Rule: budget only ever moves BETWEEN CAMPAIGNS INSIDE THE SAME ACCOUNT.
Every account's moves net to zero. Cross-account gaps are flagged, never moved.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
from pacing import Clock, parse_schedule, verdict


# --------------------------------------------------------------------------- #
@dataclass
class Move:
    from_campaign: str
    to_campaign: str
    amount_per_day: float
    reason: str
    location: str = ""


@dataclass
class CampaignRow:
    campaign: str
    ctype: str
    spend: float
    conv: float
    conv_value: float
    impressions: float
    clicks: float
    cpa: float | None
    run_rate: float          # $/active day over trailing window
    share: float             # share of account MTD spend
    pacing: float            # spend / implied campaign allocation
    dev_pp: float
    status: str
    score: float = 0.0
    eff_idx: float = 0.0
    absorption: float = 0.0
    cur_daily: float = 0.0   # inferred daily budget (trailing active-day run-rate)
    new_daily: float = 0.0   # recommended daily budget
    active: bool = True      # enabled AND actually delivering
    location: str = ""       # move-confinement group
    category: str = ""       # the unit money actually moves between
    budget: float = 0.0      # REAL daily budget from the Google Ads export
    budget_name: str = ""    # non-empty => SHARED budget pool
    util: float = 0.0        # spend per active day / daily budget
    search_is: float | None = None   # search impression share
    flags: list[str] = field(default_factory=list)

    @property
    def vs_target(self) -> str:
        if self.cpa is None:
            return "no conversions"
        if self.eff_idx >= 1.15:
            return "beating target"
        if self.eff_idx <= 0.85:
            return "above target"
        return "at target"


@dataclass
class CategoryRow:
    """A modality / campaign type inside one location — the real budget unit.

    Nobody funds `P2_Heartland_MRI_ET_B`; they fund MRI. For OIA the shared
    budget pools are named after exactly these, so the category is also the
    only level at which the budget CAN be changed.

    Field names deliberately mirror CampaignRow so the allocator treats both
    the same way.
    """
    campaign: str            # the category label (Move reads this)
    ctype: str               # same label, for the type guardrail lookups
    location: str
    members: list[str] = field(default_factory=list)
    spend: float = 0.0
    conv: float = 0.0
    impressions: float = 0.0
    clicks: float = 0.0
    cpa: float | None = None
    run_rate: float = 0.0
    cur_daily: float = 0.0
    new_daily: float = 0.0
    util: float = 0.0
    absorption: float = 0.0
    eff_idx: float = 1.0
    score: float = 0.0
    share: float = 0.0
    active: bool = True
    budget_name: str = ""    # categories always tradeable; pools live below
    pooled: bool = False
    floor: float = 0.0       # $/day this category may not be cut below
    floor: float = 0.0       # $/day this category may not be cut below
    flags: list[str] = field(default_factory=list)

    @property
    def vs_target(self) -> str:
        if self.cpa is None:
            return "no conversions"
        if self.eff_idx >= 1.15:
            return "beating target"
        if self.eff_idx <= 0.85:
            return "above target"
        return "at target"


@dataclass
class AccountResult:
    client: str
    account: str
    allocated: float
    spend: float
    conv: float
    target_cpa: float | None
    schedule_txt: str
    pacing: float
    ideal: float
    ideal_sched: float
    dev_pp: float
    status: str
    days_left: int
    required_daily: float      # SPEND needed per active day to land the month
    current_daily: float       # actual spend per active day (drives forecast)
    campaigns: list[CampaignRow]
    moves: list[Move]
    categories: list[CategoryRow] = field(default_factory=list)
    budget_daily: float = 0.0  # sum of SET daily budgets (the lever we pull)
    budget_ambiguous: bool = False  # tracker figure looks daily, not monthly
    notes: list[str] = field(default_factory=list)
    # Shortfall between required_daily and what the guardrails actually allow.
    # Non-zero means "this cannot be fixed in one step without lifting a cap".
    constrained_by: float = 0.0
    # Set only when the tracker's target turned out not to be a CPA (see
    # target_is_cpa below) and real conversion value was available to compute it.
    conv_value: float = 0.0
    roas: float | None = None

    @property
    def cpa(self) -> float | None:
        return self.spend / self.conv if self.conv else None

    # --- the question that actually matters -------------------------------
    @property
    def projected(self) -> float:
        """Where the month lands if nothing changes.

        Forecast from ACTUAL spend per active day, never from the set daily
        budget. Most campaigns never spend their full cap, so projecting off
        budget overstates wildly — an account at 38% utilisation would look
        like it is about to triple its target.
        """
        return self.spend + self.current_daily * self.days_left

    @property
    def utilisation(self) -> float:
        """Spend per active day / set daily budget."""
        return self.current_daily / self.budget_daily if self.budget_daily else 0.0

    @property
    def ceiling(self) -> float:
        """Most this account can spend by month end at 100% utilisation."""
        return self.spend + self.budget_daily * self.days_left

    @property
    def budget_capped(self) -> bool:
        """True when the set daily budgets CANNOT reach the allocation.

        No reallocation fixes this — the caps themselves have to go up.

        Only meaningful while the account is actually respecting its caps.
        Where utilisation runs above ~105% the caps were changed mid-month and
        the export shows the NEW value against OLD spend, so a ceiling built
        from them would be fiction — and it would contradict the forecast,
        which is derived from real spend.
        """
        return (bool(self.allocated)
                and self.utilisation <= 1.05
                and self.ceiling < self.allocated * 0.97
                and self.projected < self.allocated * 0.97)

    @property
    def projected_pct(self) -> float:
        return self.projected / self.allocated if self.allocated else 0.0

    @property
    def landing(self) -> str:
        if self.budget_ambiguous:
            return "BUDGET UNCLEAR"
        p = self.projected_pct
        if p < 0.97:
            return "WILL UNDERSPEND"
        if p > 1.03:
            return "WILL OVERSPEND"
        return "ON TARGET"

    @property
    def gap(self) -> float:
        """$ the month will miss by (negative = underspend)."""
        return self.projected - self.allocated

    @property
    def priority(self) -> float:
        """Sort key — dollars at risk, not percentages."""
        return abs(self.gap)


# --------------------------------------------------------------------------- #
def _safe_cpa(spend: float, conv: float) -> float | None:
    return (spend / conv) if conv and conv > 0 else None


def _efficiency_index(cpa: float | None, target: float | None, spend: float) -> float:
    """target/actual, clipped. 1.0 == exactly on target. Thin data -> neutral."""
    if not target or target <= 0:
        return 1.0
    if spend < config.MIN_SPEND_TO_JUDGE:
        return 1.0                       # not enough signal to judge
    if cpa is None:                      # spent real money, zero conversions
        return config.EFF_CLIP_LO
    return float(np.clip(target / cpa, config.EFF_CLIP_LO, config.EFF_CLIP_HI))


def _absorption(daily: pd.Series, budget: float,
                search_is: float | None = None) -> tuple[float, float]:
    """Can this campaign absorb more budget?  -> (absorption 0..1, utilisation)

    With the real daily budget in hand this stops being a guess. A campaign
    spending at or near its cap every day is demand-rich and budget-limited:
    more budget converts into more volume. One drifting well below its cap is
    demand-limited; extra budget just sits there.

    Search impression share sharpens it — high utilisation AND low IS means
    there is auction left to buy.
    """
    d = daily[daily > 0]
    if len(d) == 0 or budget <= 0:
        return 0.0, 0.0
    util = float(d.mean() / budget)
    u = float(np.clip(util, 0, 1.2)) / 1.2
    cv = float(d.std() / d.mean()) if len(d) > 1 and d.mean() else 0.4
    steadiness = float(np.clip(1 - cv, 0, 1))
    score = 0.60 * u + 0.25 * steadiness
    # room left in the auction
    headroom = (1 - search_is) if search_is is not None else 0.5
    score += 0.15 * float(np.clip(headroom, 0, 1))
    return float(np.clip(score, 0, 1)), util


def _budget_total(rows: list[CampaignRow]) -> float:
    """Sum the account's daily budget WITHOUT double-counting shared pools.

    OIA runs shared budgets (MRI, CTScan, Ultrasound...) across a dozen
    campaigns each. Naively summing the Budget column would report twelve
    times the real cap.
    """
    total, seen = 0.0, set()
    for r in rows:
        if r.budget_name:
            if r.budget_name in seen:
                continue
            seen.add(r.budget_name)
        total += r.budget
    return total


def _type_pref(ctype: str) -> float:
    return config.TYPE_PREFERENCE.get(ctype, config.DEFAULT_TYPE_PREFERENCE)


def _norm(vals: list[float]) -> list[float]:
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return [0.5] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


# --------------------------------------------------------------------------- #
def analyse_account(client: str, account: str, alloc: float,
                    target_cpa: float | None, schedule_txt: str,
                    df: pd.DataFrame, clock: Clock) -> AccountResult:
    """df = this account's campaign-day rows for the current month."""
    schedule = parse_schedule(schedule_txt)
    days_left = max(clock.active_days_left(schedule), 1)
    days_done = max(clock.active_days_done(schedule), 1)

    spend = float(df["cost"].sum())
    conv = float(df["conversions"].sum())

    # The Budget Tracker column is "Target CPA/ROAS/CPM" — one column, three
    # different units. A ROAS target of 15 read as a $15 CPA would mark every
    # campaign in an ecommerce account as failing. If the account's blended CPA
    # is wildly above the stated target, the number is not a CPA; drop it and
    # judge efficiency on relative CPA within the account instead.
    rule = config.CLIENT_RULES.get(str(client).strip().lower())
    if rule and rule.get("target_cpl"):
        target_cpa = float(rule["target_cpl"])

    rule = config.CLIENT_RULES.get(str(client).strip().lower())
    if rule and rule.get("target_cpl"):
        target_cpa = float(rule["target_cpl"])

    target_is_cpa = True
    explicit = bool(rule and rule.get("target_cpl"))
    if target_cpa and conv > 0 and not explicit:
        blended = spend / conv
        if blended > target_cpa * config.TARGET_CPA_IMPLAUSIBLE_X:
            target_is_cpa = False
    if not target_is_cpa:
        stated_target, target_cpa = target_cpa, None
    else:
        stated_target = target_cpa

    pacing = spend / alloc if alloc else 0.0
    ideal = clock.ideal(schedule)
    status, dev_pp = verdict(pacing, ideal)

    required_daily = max((alloc - spend) / days_left, 0.0)

    # ---- per-campaign roll-up -------------------------------------------
    has_budget = "budget" in df.columns
    rows: list[CampaignRow] = []
    for camp, g in df.groupby("campaign", sort=False):
        g = g.sort_values("date")
        c_spend = float(g["cost"].sum())
        c_conv = float(g["conversions"].sum())
        daily = g.groupby("date")["cost"].sum()
        active_days_seen = daily[daily > 0]
        run_rate = (float(active_days_seen.mean())
                    if len(active_days_seen) else float(c_spend / days_done))

        # The real daily budget beats any inference. Take the most recent
        # non-zero setting — that is what is live in the account right now.
        if has_budget:
            bser = g["budget"][g["budget"] > 0]
            budget = float(bser.iloc[-1]) if len(bser) else 0.0
        else:
            budget = run_rate
        cur_daily = budget if budget > 0 else run_rate

        sis = None
        if "search_is" in g.columns:
            v = pd.to_numeric(g["search_is"], errors="coerce").dropna()
            sis = float(v.mean()) if len(v) else None

        status = str(g["status"].iloc[-1]) if "status" in g.columns else ""
        bname = (str(g["budget_name"].iloc[-1]).strip()
                 if "budget_name" in g.columns else "")
        absorption, util = _absorption(daily, cur_daily, sis)

        rows.append(CampaignRow(
            campaign=camp,
            ctype=str(g["campaign_type"].iloc[0] or "Unknown"),
            spend=c_spend, conv=c_conv,
            conv_value=float(g["conv_value"].sum()),
            impressions=float(g["impressions"].sum()),
            clicks=float(g["clicks"].sum()),
            cpa=_safe_cpa(c_spend, c_conv),
            run_rate=run_rate,
            share=c_spend / spend if spend else 0.0,
            pacing=0.0, dev_pp=0.0, status=status,
            cur_daily=cur_daily,
            location=str(g["location"].iloc[0]) if "location" in g.columns else account,
            budget=budget, budget_name=bname, util=util, search_is=sis,
            category=str(g["category"].iloc[0]) if "category" in g.columns else "Other",
            absorption=absorption,
            active=(status.lower() != "paused") and c_spend > 0,
        ))

    # campaign-level pacing: its MTD spend vs its pro-rata slice of the account
    for r in rows:
        implied_alloc = alloc * r.share if spend else 0.0
        r.pacing = (r.spend / implied_alloc) if implied_alloc else 0.0
        # More useful: is this campaign's run-rate enough to land the month?
        r.dev_pp = (r.pacing - ideal) * 100
        # (r.status stays Enabled/Paused — pacing verdict lives in dev_pp)
        r.eff_idx = _efficiency_index(r.cpa, target_cpa, r.spend)
        if target_cpa and r.conv == 0 and r.spend >= config.ZERO_CONV_SPEND_X * target_cpa:
            r.flags.append(f"0 conv on {config.CURRENCY}{r.spend:,.0f}")
        if r.spend < config.MIN_SPEND_TO_JUDGE:
            r.flags.append("thin data")
        if not r.active:
            why = "paused" if r.status.lower() == "paused" else \
                  "enabled but zero spend — disapproved, no impressions, or bid too low?"
            r.flags.append(f"NO SPEND — {why}")
        elif r.budget > 0 and r.util > 1.6:
            r.flags.append(
                f"spending {config.CURRENCY}{r.run_rate:,.0f}/day against a "
                f"{config.CURRENCY}{r.budget:,.0f}/day cap — the budget was almost "
                f"certainly changed mid-month; treat the cap as the current truth")
        elif r.budget > 0 and r.util >= 0.90:
            r.flags.append(f"maxing its {config.CURRENCY}{r.budget:,.0f}/day budget "
                           f"({r.util*100:.0f}% used)")
        elif r.budget > 0 and r.util < 0.50:
            r.flags.append(f"using only {r.util*100:.0f}% of its "
                           f"{config.CURRENCY}{r.budget:,.0f}/day budget")

    # ---- score -----------------------------------------------------------
    if rows:
        eff_n = _norm([r.eff_idx for r in rows])
        vol_n = _norm([r.conv for r in rows])
        abs_n = _norm([r.absorption for r in rows])
        typ_n = [_type_pref(r.ctype) for r in rows]
        for r, e, v, a, t in zip(rows, eff_n, vol_n, abs_n, typ_n):
            r.score = (config.W_EFFICIENCY * e + config.W_VOLUME * v +
                       config.W_ABSORPTION * a + config.W_TYPE * t)

    # The forecast runs on ACTUAL spend; the lever is the SET budget.
    current_daily = sum(r.run_rate for r in rows)      # actual $/active day
    budget_daily = _budget_total(rows)                 # shared pools counted ONCE

    # --- is the tracker's "Allocated Budget" actually a DAILY figure? -----
    # Several ecommerce accounts carry a daily number in a column labelled
    # monthly. Signature: the allocation ~= the sum of set daily budgets, and
    # month-to-date spend has already blown past it.
    ambiguous = bool(
        budget_daily > 0 and alloc > 0
        and abs(alloc - budget_daily) / max(alloc, budget_daily) < 0.20
        and spend > alloc * 1.5)

    # Budget target: to SPEND `required_daily` you need proportionally more
    # cap when utilisation is below 100%.
    util = float(np.clip(current_daily / budget_daily, 0.20, 1.0)) if budget_daily else 1.0
    required_budget = required_daily / util if not ambiguous else budget_daily

    # ------------------------------------------------------------------ #
    # Roll campaigns up into CATEGORIES, then move budget between those.
    # This is how the accounts are actually managed: you fund MRI, not
    # P2_Heartland_MRI_ET_B. It is also the only level that works when a
    # shared budget pool spans several campaigns.
    # ------------------------------------------------------------------ #
    cats: dict[tuple, CategoryRow] = {}
    for r in rows:
        key = (r.location or account, r.category or "Other")
        c = cats.get(key)
        if c is None:
            c = cats[key] = CategoryRow(campaign=key[1], ctype=key[1], location=key[0])
        c.members.append(r.campaign)
        c.spend += r.spend
        c.conv += r.conv
        c.impressions += r.impressions
        c.clicks += r.clicks
        c.run_rate += r.run_rate
        c.active = c.active or r.active
        if r.budget_name:
            c.pooled = True

    # A shared pool is one budget however many campaigns draw on it.
    for key, c in cats.items():
        seen, tot = set(), 0.0
        for r in rows:
            if (r.location or account, r.category or "Other") != key:
                continue
            if r.budget_name:
                if r.budget_name in seen:
                    continue
                seen.add(r.budget_name)
            tot += r.budget
        c.cur_daily = tot
        c.cpa = _safe_cpa(c.spend, c.conv)
        c.util = (c.run_rate / tot) if tot else 0.0
        c.share = c.spend / spend if spend else 0.0
        c.eff_idx = _efficiency_index(c.cpa, target_cpa, c.spend)
        u = float(np.clip(c.util, 0, 1.2)) / 1.2
        c.absorption = float(np.clip(0.75 * u + 0.25 * (1.0 if c.conv > 0 else 0.0), 0, 1))
        if target_cpa and c.conv == 0 and c.spend >= config.ZERO_CONV_SPEND_X * target_cpa:
            c.flags.append(f"{config.CURRENCY}{c.spend:,.0f} spent, 0 conversions")
        elif target_cpa and c.cpa and c.cpa > target_cpa:
            c.flags.append(f"CPL {config.CURRENCY}{c.cpa:,.0f} vs "
                           f"{config.CURRENCY}{target_cpa:,.0f} target")

    catlist = list(cats.values())
    if catlist:
        eff = _norm([c.eff_idx for c in catlist])
        vol = _norm([c.conv for c in catlist])
        ab = _norm([c.absorption for c in catlist])
        for c, e, v, a in zip(catlist, eff, vol, ab):
            c.score = (config.W_EFFICIENCY * e + config.W_VOLUME * v +
                       config.W_ABSORPTION * a + config.W_TYPE * _type_pref(c.ctype))

    # Is the conversion column actually capturing this account's results?
    # If the implied CPL is many times the target, it is not — the client
    # counts prospects somewhere Google cannot see, and zero-conversion
    # categories must not be read as failures.
    conv_reliable = bool(conv > 0 and target_cpa and
                         (spend / conv) <= target_cpa * 3)
    if target_cpa is None:
        conv_reliable = conv > 0

    # Group by location so one location never funds another.
    groups: dict[str, list[CategoryRow]] = {}
    for c in catlist:
        groups.setdefault(c.location, []).append(c)

    moves: list[Move] = []
    shortfall = 0.0
    for loc, grp in groups.items():
        g_cur = sum(x.cur_daily for x in grp)
        g_req = (required_budget * (g_cur / budget_daily)) if budget_daily > 0 else 0.0
        g_req = _apply_floors(grp, g_req, client,
                              underspending=(current_daily * days_left + spend)
                              < alloc * 0.97)
        g_moves, g_short = _build_moves(
            grp, g_req, g_cur, (rule or {}).get("type_max_share"), target_cpa,
            conv_reliable)
        for m in g_moves:
            m.location = loc
        moves += g_moves
        shortfall += g_short
    moves.sort(key=lambda m: -m.amount_per_day)
    moves = moves[:config.MAX_MOVES_PER_ACCOUNT_TOTAL]

    # Real ROAS, when the export carries conversion value — only meaningful
    # once we already suspect the tracker target isn't a CPA (below).
    conv_value_total = float(sum(r.conv_value for r in rows))
    roas = (conv_value_total / spend) if (not target_is_cpa and spend > 0
                                          and conv_value_total > 0) else None

    res = AccountResult(
        client=client, account=account, allocated=alloc, spend=spend, conv=conv,
        target_cpa=target_cpa, schedule_txt=schedule_txt or "All days",
        pacing=pacing, ideal=ideal, ideal_sched=clock.ideal_schedule(schedule),
        dev_pp=dev_pp, status=status,
        days_left=days_left, required_daily=required_daily,
        current_daily=current_daily, budget_daily=budget_daily,
        budget_ambiguous=ambiguous,
        campaigns=sorted(rows, key=lambda r: -r.spend), moves=moves,
        categories=sorted(catlist, key=lambda c: (c.location, -c.spend)), notes=[],
        constrained_by=shortfall, conv_value=conv_value_total, roas=roas)

    # ---- notes -----------------------------------------------------------
    cur = config.CURRENCY
    lag = clock.lag_days(schedule)
    if lag:
        res.notes.append(f"Data lags {lag} active day(s) — figures exclude them.")

    if ambiguous:
        res.notes.append(
            f"*Budget figure looks wrong.* The tracker says {cur}{alloc:,.0f} for the "
            f"month, but the set daily budgets total {cur}{budget_daily:,.0f} and "
            f"{cur}{spend:,.0f} has already been spent in {days_done} active day(s). "
            f"That reads as a DAILY budget in a monthly column. Pacing is not "
            f"reported for this account until it is confirmed — the campaign moves "
            f"below are budget-neutral and stand either way.")
    else:
        if res.budget_capped:
            res.notes.append(
                f"*Cannot reach budget at current caps.* Even at 100% utilisation the "
                f"set daily budgets ({cur}{budget_daily:,.0f}/day) top out at "
                f"{cur}{res.ceiling:,.0f} — {cur}{alloc - res.ceiling:,.0f} under the "
                f"{cur}{alloc:,.0f} allocation. Reallocation cannot fix this; the caps "
                f"have to rise.")
        elif res.landing == "WILL UNDERSPEND":
            res.notes.append(
                f"Spending {cur}{current_daily:,.0f}/day against {cur}{budget_daily:,.0f}/day "
                f"of budget ({res.utilisation*100:.0f}% used) — lands at "
                f"{cur}{res.projected:,.0f} ({res.projected_pct*100:.0f}%), "
                f"{cur}{abs(res.gap):,.0f} short. Needs {cur}{required_daily:,.0f}/day of "
                f"actual spend across {days_left} active days.")
        elif res.landing == "WILL OVERSPEND":
            res.notes.append(
                f"Spending {cur}{current_daily:,.0f}/day ({res.utilisation*100:.0f}% of "
                f"{cur}{budget_daily:,.0f}/day of budget) — lands at "
                f"{cur}{res.projected:,.0f} ({res.projected_pct*100:.0f}%), "
                f"{cur}{res.gap:,.0f} over. Cut to {cur}{required_daily:,.0f}/day of "
                f"actual spend across {days_left} active days.")

        # Utilisation diagnosis — the difference between a budget problem and
        # a demand problem, which is the thing AMs most often get wrong.
        if res.utilisation < 0.55 and res.landing == "WILL UNDERSPEND":
            res.notes.append(
                f"Only {res.utilisation*100:.0f}% of the set budget is being spent, so "
                f"raising caps will not help — this is a demand/bid/targeting problem, "
                f"not a budget one.")

    if abs(res.constrained_by) > max(0.05 * required_budget, 2.0):
        res.notes.append(
            f"Guardrails cap the single-step change at "
            f"{cur}{required_budget - res.constrained_by:,.0f}/day of budget — a further "
            f"{cur}{abs(res.constrained_by):,.0f}/day is needed. Split it over two steps "
            f"or lift the type share cap.")

    if explicit and conv > 0 and (spend / conv) > target_cpa * 3:
        res.notes.append(
            f"Google-tracked conversions give a CPL of {cur}{spend/conv:,.0f} against the "
            f"{cur}{target_cpa:,.0f} target. The target counts prospects (calls + forms + "
            f"online scheduling), which Google does not see in full — compare CPL between "
            f"categories here, not against the target in absolute terms.")

    if not target_is_cpa:
        if res.roas is not None:
            hit = res.roas >= stated_target
            res.notes.append(
                f"Target of {stated_target:g} is not a CPA — the account's blended CPA is "
                f"{cur}{spend/conv:,.0f}. Actual ROAS is *{res.roas:.2f}x* "
                f"({cur}{res.conv_value:,.0f} conversion value / {cur}{spend:,.0f} spend), "
                f"{'at or above' if hit else 'below'} the {stated_target:g}x target. "
                f"Campaigns are still ranked on relative CPA — ROAS isn't tracked per "
                f"campaign here, only at the account level.")
        else:
            res.notes.append(
                f"Target of {stated_target:g} is not a CPA — the account's blended CPA is "
                f"{cur}{spend/conv:,.0f}. It is almost certainly a ROAS or CPM target sharing "
                f"the same tracker column. Campaigns are ranked on relative CPA instead.")

    dead = [r for r in rows if not r.active]
    if dead:
        res.notes.append("Not spending at all: " + ", ".join(r.campaign for r in dead))
    return res


# --------------------------------------------------------------------------- #
def _build_moves(rows, required_daily: float, current_daily: float,
                 _client_caps: dict | None = None, _target: float | None = None,
                 _conv_reliable: bool = True):
    """Redistribute `required_daily` across campaigns by score, then diff."""
    if len(rows) < 2 or required_daily <= 0:
        for r in rows:
            r.new_daily = r.cur_daily
        return [], 0.0

    scores = np.array([max(r.score, 1e-6) for r in rows])
    # Absorption gates the target: no point funding a campaign that can't spend.
    gated = scores * np.array([0.35 + 0.65 * r.absorption for r in rows])
    target_share = gated / gated.sum()
    target = target_share * required_daily

    # ---- clamp to guardrails --------------------------------------------
    # Clamp against each campaign's PRO-RATA share of the NEW account total,
    # not its raw current budget. An account-wide cut is not a "move": the
    # guardrail exists to limit reshuffling BETWEEN campaigns, so a 40% cut
    # to the whole account must not read as every campaign breaching it.
    cur = np.array([r.cur_daily for r in rows])
    scale = (required_daily / current_daily) if current_daily > 0 else 1.0
    base = cur * scale
    lo = np.maximum(base * (1 - config.MAX_DONATE_REL),
                    np.array([float(getattr(r, "floor", 0.0)) for r in rows]))
    hi = base * (1 + np.array([min(
        config.MAX_RECEIVE_REL,
        config.TYPE_RECEIVE_CAP.get(r.ctype, config.MAX_RECEIVE_REL)) for r in rows]))
    # A campaign that isn't running gets nothing — fix why it's dark first.
    hi = np.where(np.array([not r.active for r in rows]), 0.0, hi)
    target = np.clip(target, np.minimum(lo, hi), hi)

    # ---- fold in type share floors / ceilings ----------------------------
    caps = dict(config.TYPE_MAX_SHARE)
    caps.update(_client_caps or {})
    for i, r in enumerate(rows):
        if r.ctype in config.TYPE_MIN_SHARE and r.active:
            lo[i] = max(lo[i], config.TYPE_MIN_SHARE[r.ctype] * required_daily)
        if r.ctype in caps:
            hi[i] = min(hi[i], caps[r.ctype] * required_daily)
        # Spent real money for zero conversions? It keeps what it has while
        # someone works out why, but it does not get more.
        # Only when the account's conversion tracking is actually capturing
        # results — where it is not (GD counts prospects in CallRail, which
        # Google never sees), every category would read as zero and the whole
        # account would freeze.
        if _conv_reliable and r.conv == 0 and r.spend > 0:
            hi[i] = min(hi[i], r.cur_daily)
    lo = np.minimum(lo, hi)

    # ---- water-fill to hit required_daily WITHOUT breaching the bounds ---
    # Naive "clamp then rescale" silently re-breaches the caps, so instead
    # push the residual only onto campaigns that still have headroom.
    target = _water_fill(target, lo, hi, required_daily)
    for r, t in zip(rows, target):
        r.new_daily = float(t)
    shortfall = required_daily - float(target.sum())

    # ---- express as pairwise transfers -----------------------------------
    # Deltas are ABSOLUTE: new budget minus current budget. A "move" therefore
    # only appears when one line genuinely comes down and another goes up.
    # (Measuring against a pro-rata baseline instead produced the nonsense of
    # "move $13/day from Brand" printed next to Brand's budget going up.)
    deltas = [(r, r.new_daily - r.cur_daily) for r in rows]
    donors = sorted([d for d in deltas if d[1] < 0], key=lambda x: x[1])
    recips = sorted([d for d in deltas if d[1] > 0], key=lambda x: -x[1])

    moves: list[Move] = []
    di = ri = 0
    pool_out = [(-d[1]) for d in donors]
    pool_in = [d[1] for d in recips]
    while di < len(donors) and ri < len(recips) and len(moves) < config.MAX_MOVES_PER_ACCOUNT:
        dr, rr = donors[di][0], recips[ri][0]

        # Two campaigns on the SAME shared budget already draw from one pool —
        # "move $20/day from A to B" is not a thing you can do in the UI, and
        # it would change nothing if you could. Skip to the next recipient.
        if dr.budget_name and dr.budget_name == rr.budget_name:
            ri += 1
            continue

        amt = min(pool_out[di], pool_in[ri])
        if amt >= config.MIN_MOVE_PER_DAY and amt >= config.MIN_MOVE_REL * max(dr.cur_daily, 1):
            moves.append(Move(dr.campaign, rr.campaign, round(amt, 2),
                              _reason(dr, rr, _target)))
        pool_out[di] -= amt
        pool_in[ri] -= amt
        if pool_out[di] <= 0.01:
            di += 1
        if pool_in[ri] <= 0.01:
            ri += 1
    return moves, shortfall


def _apply_floors(grp: list, g_req: float, client: str,
                  underspending: bool = False) -> float:
    """Apply a client's own policy floors before anything is reallocated.

    GD is managed as: $40 per prospect, Brand and PMax carry the spend, every
    other campaign type sits at $5/day — and when the account is running under
    budget those $5 lines are what get raised, not Brand and PMax.
    """
    rule = config.CLIENT_RULES.get(str(client).strip().lower())
    if not rule:
        return g_req
    floor = rule.get("minor_floor", 0.0)
    if underspending:
        floor *= rule.get("floor_relief", 1.0)
    major = rule.get("major", set())
    for c in grp:
        if c.ctype not in major and getattr(c, "active", True):
            c.floor = max(getattr(c, "floor", 0.0), floor)
    return g_req


def _apply_floors(grp: list, g_req: float, client: str,
                  underspending: bool = False) -> float:
    """Apply the client's own policy floors before anything is reallocated.

    GD is run as: $40 per prospect, Brand and PMax carry the spend, every other
    campaign type sits at $5/day — and when the account is running under budget
    those $5 lines are what get raised, not Brand and PMax.
    """
    rule = config.CLIENT_RULES.get(str(client).strip().lower())
    if not rule:
        return g_req
    floor = rule.get("minor_floor", 0.0)
    if underspending:
        floor *= rule.get("floor_relief", 1.0)
    major = rule.get("major", set())
    for c in grp:
        if c.ctype not in major and getattr(c, "active", True):
            c.floor = max(getattr(c, "floor", 0.0), floor)
    return g_req


def _water_fill(target: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                total: float, iters: int = 40) -> np.ndarray:
    """Scale `target` to sum to `total` while respecting [lo, hi] elementwise.

    Clamp, then push the leftover onto whichever campaigns still have room,
    repeatedly. If the bounds make `total` unreachable, sit on the nearest
    feasible edge rather than silently breaching a guardrail.
    """
    x = np.clip(target.astype(float), lo, hi)
    if hi.sum() < total:            # cannot spend that much within the caps
        return hi.copy()
    if lo.sum() > total:            # cannot go that low within the floors
        return lo.copy()
    for _ in range(iters):
        resid = total - x.sum()
        if abs(resid) < 1e-6:
            break
        room = (hi - x) if resid > 0 else (x - lo)
        movable = room > 1e-9
        if not movable.any():
            break
        w = room[movable] / room[movable].sum()
        x[movable] += np.sign(resid) * min(abs(resid), room[movable].sum()) * w
        x = np.clip(x, lo, hi)
    return x


def _reason(donor, recip, target: float | None = None) -> str:
    """Say the true thing, in the vocabulary the team uses: CPL against target."""
    cur = config.CURRENCY
    t = f" vs {cur}{target:,.0f} target" if target else ""

    if donor.conv == 0 and donor.spend > 0:
        d = f"{cur}{donor.spend:,.0f} spent, 0 conversions"
    elif donor.cpa and target and donor.cpa > target:
        d = f"CPL {cur}{donor.cpa:,.2f}{t}"
    elif getattr(donor, "util", 1) < 0.55 and donor.cur_daily > 0:
        d = f"using {donor.util*100:.0f}% of its {cur}{donor.cur_daily:,.0f}/day"
    elif donor.cpa:
        d = f"CPL {cur}{donor.cpa:,.2f} — on target, but the weakest here"
    else:
        d = "lowest return in this location"

    if recip.cpa and target and recip.cpa <= target:
        r = f"CPL {cur}{recip.cpa:,.2f}{t}"
    elif recip.cpa:
        r = f"CPL {cur}{recip.cpa:,.2f}"
    elif getattr(recip, "util", 0) > 0.9:
        r = "at its cap and still buying"
    else:
        r = "the stronger performer here"
    return f"{donor.campaign}: {d}. {recip.campaign}: {r}."


@dataclass
class ClientSummary:
    client: str
    allocated: float
    spend: float
    conv: float
    projected: float
    accounts: list[AccountResult]
    warnings: list[str] = field(default_factory=list)

    @property
    def pacing(self) -> float:
        return self.spend / self.allocated if self.allocated else 0.0

    @property
    def projected_pct(self) -> float:
        return self.projected / self.allocated if self.allocated else 0.0

    @property
    def cpa(self) -> float | None:
        return self.spend / self.conv if self.conv else None

    @property
    def at_risk(self) -> list[AccountResult]:
        return sorted([a for a in self.accounts if a.landing != "ON TARGET"],
                      key=lambda a: -a.priority)


def summarise(client: str, results: list[AccountResult],
              warnings: list[str] | None = None) -> ClientSummary:
    return ClientSummary(
        client=client,
        allocated=sum(a.allocated for a in results),
        spend=sum(a.spend for a in results),
        conv=sum(a.conv for a in results),
        projected=sum(a.projected for a in results),
        accounts=sorted(results, key=lambda a: -a.priority),
        warnings=warnings or [])


def run(campaigns: pd.DataFrame, budgets: pd.DataFrame,
        clock: Clock, client: str) -> list[AccountResult]:
    out: list[AccountResult] = []
    b = budgets[budgets["client"].str.strip().str.lower() == client.strip().lower()]
    # The export is Google-only; a Meta or Bing budget line is not ours to pace.
    if "channel" in b.columns:
        b = b[b["channel"].str.contains("Google", case=False, na=False)]
    b = b[b["allocated_budget"] > 0]
    for _, row in b.iterrows():
        acct = str(row["account"]).strip()
        df = campaigns[campaigns["account"].str.strip().str.lower() == acct.lower()]
        if df.empty:
            out.append(AccountResult(
                client=client, account=acct, allocated=float(row["allocated_budget"] or 0),
                spend=0.0, conv=0.0, target_cpa=row.get("target_cpa"),
                schedule_txt=row.get("days_running", ""), pacing=0.0,
                ideal=clock.ideal(parse_schedule(row.get("days_running"))),
                ideal_sched=clock.ideal_schedule(parse_schedule(row.get("days_running"))),
                dev_pp=-clock.ideal(parse_schedule(row.get("days_running"))) * 100,
                status="NO_DATA", days_left=0, required_daily=0.0, current_daily=0.0,
                campaigns=[], moves=[],
                notes=["No campaign rows matched this account — check the name mapping."]))
            continue
        # This account's own feed can lag the rest of the portfolio (a
        # Supermetrics sync gap that hasn't hit every account the same day) —
        # judge it against its own last complete day, not the portfolio's.
        acct_clock = clock.with_data_through(df["date"].max().date())
        out.append(analyse_account(
            client, acct, float(row["allocated_budget"] or 0),
            (float(row["target_cpa"]) if pd.notna(row.get("target_cpa")) and
             str(row.get("target_cpa")).replace(".", "").isdigit() else None),
            str(row.get("days_running") or ""), df, acct_clock))
    return out
