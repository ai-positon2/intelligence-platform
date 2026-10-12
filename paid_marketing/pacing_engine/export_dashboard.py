"""Build one JSON payload for the dashboard from every client's results."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import engine
from run import DEFAULT_EXPORT, load_all

OUT = Path(__file__).parent / "out"


def build(as_of: dt.date, export: str = DEFAULT_EXPORT) -> dict:
    df, budgets, clock, warns = load_all(export, as_of)
    clients = []
    for name in sorted(df["client"].dropna().unique()):
        sub = df[df["client"] == name]
        accts = engine.run(sub, budgets, clock, name)
        cw = [w for w in warns if name.lower() in w.lower()
              or any(a in w for a in sub["account"].unique())]
        s = engine.summarise(name, accts, cw)
        solid = [a for a in s.accounts if not a.budget_ambiguous]
        clients.append({
            "name": name,
            "allocated": round(sum(a.allocated for a in solid), 2),
            "spend": round(s.spend, 2),
            "spendSolid": round(sum(a.spend for a in solid), 2),
            "projected": round(sum(a.projected for a in solid), 2),
            "conv": round(s.conv, 1),
            "cpa": round(s.cpa, 2) if s.cpa else None,
            "warnings": s.warnings,
            "accounts": [{
                "name": a.account,
                "landing": a.landing,
                "allocated": round(a.allocated, 2),
                "spend": round(a.spend, 2),
                "pacing": round(a.pacing, 4),
                "ideal": round(a.ideal, 4),
                "idealSched": round(a.ideal_sched, 4),
                "projected": round(a.projected, 2),
                "projectedPct": round(a.projected_pct, 4),
                "gap": round(a.gap, 2),
                "daysLeft": a.days_left,
                "schedule": a.schedule_txt,
                "currentDaily": round(a.current_daily, 2),
                "requiredDaily": round(a.required_daily, 2),
                "budgetDaily": round(a.budget_daily, 2),
                "utilisation": round(a.utilisation, 4),
                "targetCpa": a.target_cpa,
                "cpa": round(a.cpa, 2) if a.cpa else None,
                "conv": round(a.conv, 1),
                "ambiguous": a.budget_ambiguous,
                "capped": a.budget_capped,
                "notes": a.notes,
                "targetCpl": a.target_cpa,
                "convValue": round(a.conv_value, 2),
                "roas": round(a.roas, 3) if a.roas else None,
                "categories": [{
                    "name": c.campaign, "loc": c.location,
                    "spend": round(c.spend, 2), "conv": round(c.conv, 1),
                    "cpl": round(c.cpa, 2) if c.cpa else None,
                    "budget": round(c.cur_daily, 2),
                    "newDaily": round(c.new_daily, 2),
                    "util": round(c.util, 3), "pooled": c.pooled,
                    "members": c.members, "active": c.active,
                    "flags": c.flags, "score": round(c.score, 3),
                } for c in a.categories],
                "moves": [{"loc": m.location, "from": m.from_campaign,
                           "to": m.to_campaign, "amt": m.amount_per_day,
                           "why": m.reason} for m in a.moves],
                "campaigns": [{
                    "name": c.campaign, "loc": c.location, "type": c.ctype,
                    "status": c.status, "spend": round(c.spend, 2),
                    "conv": round(c.conv, 1),
                    "cpa": round(c.cpa, 2) if c.cpa else None,
                    "vsTarget": c.vs_target,
                    "budget": round(c.budget, 2), "pool": c.budget_name,
                    "newDaily": round(c.new_daily, 2),
                    "util": round(c.util, 3),
                    "sis": round(c.search_is, 3) if c.search_is else None,
                    "active": c.active, "flags": c.flags,
                } for c in a.campaigns],
            } for a in s.accounts],
        })

    orphan = [w for w in warns
              if not any(c["name"].lower() in w.lower() for c in clients)]
    return {
        "asOf": str(as_of),
        "dataThrough": str(clock.data_through),
        "month": as_of.strftime("%B %Y"),
        "elapsed": clock.elapsed, "daysInMonth": clock.dim,
        "idealPacing": round(clock.ideal_calendar(), 4),
        "tolerance": 2.0,
        "clients": clients,
        "portfolioWarnings": orphan,
    }


if __name__ == "__main__":
    as_of = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    d = build(as_of)
    p = OUT / "dashboard.json"
    p.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
    n_a = sum(len(c["accounts"]) for c in d["clients"])
    n_c = sum(len(a["campaigns"]) for c in d["clients"] for a in c["accounts"])
    n_m = sum(len(a["moves"]) for c in d["clients"] for a in c["accounts"])
    print(f"{len(d['clients'])} clients, {n_a} accounts, {n_c} campaigns, "
          f"{n_m} moves -> {p} ({p.stat().st_size/1024:.0f} KB)")
