"""Self-checks. Run after every build — a wrong budget move costs real money."""
from __future__ import annotations

import datetime as dt
import sys

import config
import engine
from run import load_all

TOL = 0.75   # dollars


def check(as_of: dt.date, export: str | None = None) -> list[str]:
    from run import DEFAULT_EXPORT
    df, budgets, clock, warns = load_all(export or DEFAULT_EXPORT, as_of)
    fails: list[str] = []

    for client in sorted(df["client"].dropna().unique()):
        sub = df[df["client"] == client]
        s = engine.summarise(client, engine.run(sub, budgets, clock, client))

        for a in s.accounts:
            tag = f"{client}/{a.account}"
            # Key on (location, category): "Brand" exists in every one of
            # Great Hill's six locations, so the name alone collides.
            cats = {(c.location, c.campaign): c for c in a.categories}

            # 1. moves net to zero
            net = {}
            for m in a.moves:
                kf, kt = (m.location, m.from_campaign), (m.location, m.to_campaign)
                net[kf] = net.get(kf, 0) - m.amount_per_day
                net[kt] = net.get(kt, 0) + m.amount_per_day
            if abs(sum(net.values())) > 0.01:
                fails.append(f"{tag}: moves do not net to zero ({sum(net.values()):+.2f})")

            for m in a.moves:
                # 2. both endpoints must be categories that exist here
                kf, kt = (m.location, m.from_campaign), (m.location, m.to_campaign)
                if kf not in cats or kt not in cats:
                    fails.append(f"{tag}: move references a category not in this account")
                    continue
                d, r = cats[kf], cats[kt]
                # 3. never across locations
                if d.location != r.location or d.location != m.location:
                    fails.append(f"{tag}: move crosses locations "
                                 f"({d.location} -> {r.location})")
                # 4. noise floor
                if m.amount_per_day < config.MIN_MOVE_PER_DAY - 0.01:
                    fails.append(f"{tag}: move of ${m.amount_per_day:.2f} below floor")
                # 5. never fund a dark category
                if not r.active:
                    fails.append(f"{tag}: budget routed to dark category {r.campaign}")
                # 6. a move must reflect a real budget change in both directions
                if d.new_daily >= d.cur_daily - 0.01:
                    fails.append(f"{tag}: '{d.campaign}' donates but its budget "
                                 f"does not fall ({d.cur_daily:.0f} -> {d.new_daily:.0f})")
                if r.new_daily <= r.cur_daily + 0.01:
                    fails.append(f"{tag}: '{r.campaign}' receives but its budget "
                                 f"does not rise ({r.cur_daily:.0f} -> {r.new_daily:.0f})")

            # 7. client policy floors respected
            rule = config.CLIENT_RULES.get(client.strip().lower())
            if rule:
                for c in a.categories:
                    if c.ctype not in rule.get("major", set()) and c.active:
                        if c.new_daily < rule.get("minor_floor", 0) - TOL:
                            fails.append(f"{tag}/{c.campaign}: below the "
                                         f"${rule['minor_floor']:.0f}/day policy floor")

            # 8. a zero-conversion category never gets MORE, where conversion
            #    tracking is actually reliable for the account
            if a.conv > 0 and a.target_cpa and (a.spend / a.conv) <= a.target_cpa * 3:
                for c in a.categories:
                    if c.conv == 0 and c.spend > 0 and c.new_daily > c.cur_daily + TOL:
                        fails.append(f"{tag}/{c.campaign}: funded despite 0 conversions")

            # 9. categories must reconcile to the account
            cs = sum(c.spend for c in a.categories)
            if abs(cs - a.spend) > 0.02:
                fails.append(f"{tag}: category spend {cs:.2f} != account {a.spend:.2f}")
            cm = sum(c.spend for c in a.campaigns)
            if abs(cm - a.spend) > 0.02:
                fails.append(f"{tag}: campaign spend {cm:.2f} != account {a.spend:.2f}")

            # 10. every campaign belongs to exactly one category
            if len({c.campaign for c in a.campaigns}) != sum(
                    len(c.members) for c in a.categories):
                fails.append(f"{tag}: campaigns not partitioned cleanly into categories")

            # 11. shared pools not double counted
            pooled = [c for c in a.campaigns if c.budget_name]
            if pooled:
                naive = sum(c.budget for c in a.campaigns)
                if a.budget_daily >= naive - TOL and len(
                        {c.budget_name for c in pooled}) < len(pooled):
                    fails.append(f"{tag}: shared budgets appear double-counted")

            # 12. forecast from run-rate, never the cap
            if abs(a.projected - (a.spend + a.current_daily * a.days_left)) > 0.01:
                fails.append(f"{tag}: projection not derived from actual run-rate")

        # 10. client totals reconcile
        if abs(sum(a.spend for a in s.accounts) - s.spend) > 0.02:
            fails.append(f"{client}: account spend does not sum to client spend")
        if abs(sum(a.allocated for a in s.accounts) - s.allocated) > 0.02:
            fails.append(f"{client}: allocations do not sum to client total")

    return fails


if __name__ == "__main__":
    as_of = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    f = check(as_of)
    print(f"[{'FAIL' if f else ' ok '}] {as_of}: {len(f)} issue(s)")
    for x in f[:40]:
        print("   ✗", x)
    sys.exit(1 if f else 0)
