"""Render one Slack post per client.

Written for someone scanning on a phone: verdict first, dollars second,
campaign detail last. Accounts needing nothing collapse to a single line.
"""
from __future__ import annotations

import config
from engine import AccountResult, ClientSummary

CUR = config.CURRENCY
ICON = {"ON TARGET": ":large_green_circle:",
        "WILL UNDERSPEND": ":large_blue_circle:",
        "WILL OVERSPEND": ":red_circle:",
        "BUDGET UNCLEAR": ":grey_question:"}


def _money(v: float) -> str:
    return f"{CUR}{v:,.0f}"


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _account_block(a: AccountResult) -> str:
    L = [f"{ICON.get(a.landing, ':white_circle:')} *{a.account}* — "
         f"{_money(a.spend)} of {_money(a.allocated)} ({_pct(a.pacing)})"
         + ("" if a.budget_ambiguous else
            f" · projects to {_money(a.projected)} ({a.projected_pct*100:.0f}%)")]

    if a.budget_daily:
        L.append(f"    _{_money(a.current_daily)}/day actual against "
                 f"{_money(a.budget_daily)}/day of budget "
                 f"({a.utilisation*100:.0f}% used) · {a.days_left} active days left_")

    if not a.budget_ambiguous and a.landing != "ON TARGET":
        verb, tail = ("Raise", "short") if a.gap < 0 else ("Cut", "over")
        L.append(f"    → {verb} to {_money(a.required_daily)}/day of actual spend "
                 f"— otherwise {_money(abs(a.gap))} {tail}")

    for m in a.moves:
        where = f" _({m.location})_" if m.location and m.location != a.account else ""
        L.append(f"    • Move *{_money(m.amount_per_day)}/day*{where} "
                 f"`{m.from_campaign}` → `{m.to_campaign}`")
        L.append(f"      _{m.reason}_")

    shown = 0
    for c in a.campaigns:
        bad = [f for f in c.flags
               if "NO SPEND" in f or "0 conv" in f or "changed mid-month" in f]
        if bad and shown < 4:
            L.append(f"    :warning: `{c.campaign}` — {bad[0]}")
            shown += 1

    for n in a.notes:
        L.append(f"    {n}" if n.startswith("*") else f"    _{n}_")
    return "\n".join(L)


def render_client(s: ClientSummary, clock, month_label: str) -> str:
    ideal = clock.ideal_calendar()
    L = [f"*{s.client}* — budget pacing, {month_label}",
         f"Day {clock.as_of.day} · ideal pacing *{_pct(ideal)}* "
         f"({clock.elapsed}/{clock.dim}) · tolerance +{config.UPPER_TOL_PP:.0f}pp · "
         f"data through {clock.data_through:%b} {clock.data_through.day}"]

    sched = {round(a.ideal_sched, 4) for a in s.accounts if a.schedule_txt}
    if sched and max(abs(x - ideal) for x in sched) > 0.01:
        lo, hi = min(sched), max(sched)
        rng = _pct(lo) if abs(hi - lo) < 1e-6 else f"{_pct(lo)}–{_pct(hi)}"
        L.append(f"_Schedule-aware ideal for these run-days is {rng} — "
                 f"the calendar rule flatters part-week accounts._")
    L.append("")

    solid = [a for a in s.accounts if not a.budget_ambiguous]
    alloc = sum(a.allocated for a in solid)
    spend = sum(a.spend for a in solid)
    proj = sum(a.projected for a in solid)
    L.append(f"*Client total:* {_money(spend)} of {_money(alloc)} "
             f"({_pct(spend/alloc) if alloc else 'n/a'}) · projects to {_money(proj)} "
             f"({proj/alloc*100:.0f}%)" if alloc else "*Client total:* n/a")
    L[-1] += f" · {s.conv:,.0f} conv" + (f" · CPA {_money(s.cpa)}" if s.cpa else "")

    risk = [a for a in s.accounts if a.landing not in ("ON TARGET",)]
    capped = [a for a in s.accounts if a.budget_capped and not a.budget_ambiguous]
    moves = sum(len(a.moves) for a in s.accounts)
    bits = [f"{len(risk)} of {len(s.accounts)} accounts off-target",
            f"{moves} campaign move{'s' if moves != 1 else ''}"]
    if capped:
        bits.append(f"*{len(capped)} cannot reach budget at current caps*")
    L.append(" · ".join(bits))
    L.append("")

    if risk:
        L.append("*Needs action* _(largest dollar gap first)_")
        for a in sorted(risk, key=lambda x: -x.priority):
            L.append(_account_block(a))
            L.append("")

    ok = [a for a in s.accounts if a.landing == "ON TARGET"]
    movers = [a for a in ok if a.moves]
    if movers:
        L.append("*On budget, but worth a campaign shift*")
        for a in movers:
            L.append(_account_block(a))
            L.append("")

    quiet = [a for a in ok if not a.moves]
    if quiet:
        L.append("*No action* — " + ", ".join(
            f"{a.account} ({a.projected_pct*100:.0f}%)" for a in quiet))

    if s.warnings:
        L.append("")
        L.append("*Data flags*")
        for w in s.warnings:
            L.append(f"    • {w}")

    L.append("")
    L.append("_Daily budgets and utilisation are read from the Google Ads export. "
             "Moves stay inside each location and net to zero. "
             "Automated draft — verify before applying._")
    return "\n".join(L)
