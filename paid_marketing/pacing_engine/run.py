"""Orchestrator.

    python3 run.py [--as-of YYYY-MM-DD] [--client X]
    python3 run.py --export "Campaign Spends.xlsx"   # manual Google Ads UI export instead

Input is either the auto-refreshing "All Accounts Spend Data" Google Sheet
(the default -- see DEFAULT_EXPORT; fetch it into raw/ before running) or a
manually-downloaded Google Ads UI export passed via --export. load_any_export
tells the two shapes apart by header, so either just works. Budgets come from
the Budget Tracker sheet (raw/budget_tracker.xlsx).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import categories
import engine
import gads
import loaders
import slack_fmt
from pacing import Clock

OUT = Path(__file__).parent / "out"
# The daily driver is now the auto-refreshing "All Accounts Spend Data" Google
# Sheet (fetched into raw/ each run) rather than a manually-downloaded
# Campaign Spends.xlsx. --export still accepts either shape -- load_any_export
# tells them apart by header.
DEFAULT_EXPORT = str(Path(__file__).parent / "raw" / "all_accounts_spend_data.xlsx")


def load_all(export: str, as_of: dt.date):
    budgets = loaders.load_budgets()
    raw = gads.load_any_export(export)
    df, warns = gads.resolve(raw, budgets)
    df["category"] = [categories.categorise(cl, cm, ac, ty) for cl, cm, ac, ty in
                      zip(df["client"], df["campaign"], df["account"], df["campaign_type"])]
    df = df[(df["date"].dt.year == as_of.year) & (df["date"].dt.month == as_of.month)]
    if df.empty:
        raise SystemExit(f"No rows for {as_of:%B %Y} in {export}")
    clock = Clock(as_of=as_of, data_through=df["date"].max().date())
    return df, budgets, clock, warns


def slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in s.lower()).strip("-")


def to_json(s, clock, as_of):
    return {
        "client": s.client, "as_of": str(as_of),
        "data_through": str(clock.data_through),
        "ideal_pacing": round(clock.ideal_calendar(), 4),
        "allocated": round(s.allocated, 2), "spend": round(s.spend, 2),
        "projected": round(s.projected, 2), "conversions": s.conv,
        "warnings": s.warnings,
        "accounts": [{
            "account": x.account, "allocated": x.allocated, "spend": round(x.spend, 2),
            "pacing": round(x.pacing, 4), "ideal": round(x.ideal, 4),
            "ideal_schedule": round(x.ideal_sched, 4),
            "dev_pp": round(x.dev_pp, 2), "status": x.status,
            "landing": x.landing, "projected": round(x.projected, 2),
            "projected_pct": round(x.projected_pct, 4), "gap": round(x.gap, 2),
            "conv_value": round(x.conv_value, 2), "roas": round(x.roas, 3) if x.roas else None,
            "days_left": x.days_left, "schedule": x.schedule_txt,
            "current_daily": round(x.current_daily, 2),
            "required_daily": round(x.required_daily, 2),
            "constrained_by": round(x.constrained_by, 2),
            "notes": x.notes,
            "campaigns": [{
                "campaign": c.campaign, "location": c.location, "type": c.ctype,
                "status": c.status, "spend": round(c.spend, 2), "conversions": c.conv,
                "cpa": round(c.cpa, 2) if c.cpa else None, "vs_target": c.vs_target,
                "daily_budget": round(c.budget, 2),
                "recommended_daily": round(c.new_daily, 2),
                "utilisation": round(c.util, 3),
                "search_impr_share": round(c.search_is, 3) if c.search_is else None,
                "absorption": round(c.absorption, 3), "score": round(c.score, 3),
                "active": c.active, "flags": c.flags,
            } for c in x.campaigns],
            "moves": [{"location": m.location, "from": m.from_campaign,
                       "to": m.to_campaign, "per_day": m.amount_per_day,
                       "reason": m.reason} for m in x.moves],
        } for x in s.accounts],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default=DEFAULT_EXPORT)
    ap.add_argument("--as-of", default=str(dt.date.today()))
    ap.add_argument("--client", action="append")
    a = ap.parse_args()
    as_of = dt.date.fromisoformat(a.as_of)
    OUT.mkdir(exist_ok=True)

    df, budgets, clock, warns = load_all(a.export, as_of)
    clients = a.client or sorted(df["client"].dropna().unique())

    for client in clients:
        sub = df[df["client"] == client]
        if sub.empty:
            print(f"[skip] {client}: no rows")
            continue
        cw = [w for w in warns if client.lower() in w.lower()
              or any(x in w for x in sub["account"].unique())]
        results = engine.run(sub, budgets, clock, client)
        s = engine.summarise(client, results, cw)
        msg = slack_fmt.render_client(s, clock, as_of.strftime("%B %Y"))
        (OUT / f"{slug(client)}.slack.md").write_text(msg, encoding="utf-8")
        (OUT / f"{slug(client)}.json").write_text(
            json.dumps(to_json(s, clock, as_of), indent=2), encoding="utf-8")
        print(f"[ok] {client:<24} {len(s.accounts):>3} accts  "
              f"{sum(len(x.moves) for x in s.accounts):>3} moves  "
              f"${s.spend:>10,.0f} / ${s.allocated:>10,.0f}  "
              f"-> {s.projected_pct*100:>3.0f}%")

    # Portfolio-level warnings that belong to no client
    orphan = [w for w in warns if not any(c.lower() in w.lower() for c in clients)]
    if orphan:
        (OUT / "_portfolio-warnings.txt").write_text("\n".join(orphan), encoding="utf-8")
        print(f"\n[!] {len(orphan)} portfolio warning(s) -> out/_portfolio-warnings.txt")
        for w in orphan:
            print("   ", w)


if __name__ == "__main__":
    main()
