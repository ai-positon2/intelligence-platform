"""Market Radar, Phase 9: what runs really cost, against the plan's estimates.

Read-only and free: it adds up the cost ledger (mr_provider_calls, written by
tracker/market_radar_ledger) for one person's runs.

  * By kind of run: a competitor search ("baseline"/"refresh") and a
    collection ("collect": detectors, radar, pulse, signals, report and,
    weekly, the update). Median, 90th percentile and the most expensive.
  * By stage: which steps the money goes to.
  * A full first run per company: its latest finished search plus the first
    collection after it, which is what a new client costs.

A call without a measured cost counts at its reservation, the same rule the
ledger uses, and the runs holding one are counted, so an estimate is never
met by leaving calls out.

The estimates are the plan's (Market_Radar_Agent_Plan.md, cost section):
about $0.50 for a first run with Sonnet writing the report, $0.07 to $0.12
for a weekly refresh, and a hard cap of $1.00 per run.
"""
from __future__ import annotations

from decimal import Decimal

from . import market_radar_costs as costs
from .market_radar_store import _tx

MEASURED = ("done", "failed")
ESTIMATES = {"first_run_usd": Decimal("0.50"), "weekly_usd": Decimal("0.12"),
             "cap_usd": Decimal("1.00")}
SEARCH_MODES = ("baseline", "refresh")


def _pct(values, q):
    """The q-th percentile (nearest rank) of a non-empty sorted list."""
    k = max(0, min(len(values) - 1, -(-len(values) * q // 100) - 1))
    return values[int(k)]


def _spread(values):
    values = sorted(values)
    if not values:
        return {"runs": 0}
    return {"runs": len(values), "median_usd": costs.usd(_pct(values, 50)),
            "p90_usd": costs.usd(_pct(values, 90)), "max_usd": costs.usd(values[-1]),
            "mean_usd": costs.usd(sum(values) / len(values))}


def rows(owner_email):
    """(run_id, client_id, mode, status, created_at, stage, call_status,
    reserved, actual) for every finished run of this person, one row per
    paid call, plus one row with no call for a run that made none."""
    with _tx() as cur:
        cur.execute(
            """SELECT r.id, r.client_id, r.mode, r.status, r.created_at,
                      c.stage, c.status, c.reserved_usd, c.actual_usd
               FROM mr_runs r LEFT JOIN mr_provider_calls c ON c.run_id = r.id
               WHERE r.owner_email = %s AND r.status IN ('complete', 'failed')
               ORDER BY r.id, c.id""", (owner_email,))
        return cur.fetchall()


def build(raw):
    runs = {}
    stages = {}
    for run_id, client_id, mode, status, created, stage, cstatus, reserved, actual in raw:
        run = runs.setdefault(run_id, {"client": client_id, "mode": mode, "status": status,
                                       "created": created, "usd": Decimal(0), "partial": False})
        if stage is None:
            continue
        measured = cstatus in MEASURED
        usd = Decimal(actual or 0) if measured else Decimal(reserved or 0)
        run["usd"] += usd
        run["partial"] = run["partial"] or not measured
        s = stages.setdefault(stage, {"calls": 0, "usd": Decimal(0), "unmeasured": 0,
                                      "runs": set()})
        s["calls"] += 1
        s["usd"] += usd
        s["unmeasured"] += 0 if measured else 1
        s["runs"].add(run_id)

    done = {k: v for k, v in runs.items() if v["status"] == "complete"}
    search = [v["usd"] for v in done.values() if v["mode"] in SEARCH_MODES]
    collect = [v["usd"] for v in done.values() if v["mode"] == "collect"]

    # A first run: each company's latest finished search, plus the first
    # collection that started after it.
    first = []
    for client in {v["client"] for v in done.values()}:
        mine = sorted(((k, v) for k, v in done.items() if v["client"] == client),
                      key=lambda kv: kv[1]["created"])
        searches = [kv for kv in mine if kv[1]["mode"] in SEARCH_MODES]
        if not searches:
            continue
        sid, s = searches[-1]
        after = [kv for kv in mine if kv[1]["mode"] == "collect" and kv[1]["created"] > s["created"]]
        if after:
            first.append({"client_id": client, "search_run": sid, "collect_run": after[0][0],
                          "usd": s["usd"] + after[0][1]["usd"]})

    total = sum(stages[s]["usd"] for s in stages) or Decimal(0)
    out = {
        "estimates": {k: costs.usd(v) for k, v in ESTIMATES.items()},
        "search": _spread(search),
        "collect": _spread(collect),
        "first_run": dict(_spread([f["usd"] for f in first]),
                          companies=sorted(first, key=lambda f: -f["usd"])),
        "failed_runs": sum(1 for v in runs.values() if v["status"] == "failed"),
        "runs_with_unmeasured_calls": sum(1 for v in runs.values() if v["partial"]),
        "over_cap": sorted(k for k, v in runs.items() if v["usd"] > ESTIMATES["cap_usd"]),
        "stages": sorted(({"stage": k, "calls": v["calls"], "runs": len(v["runs"]),
                           "usd": costs.usd(v["usd"]),
                           "usd_per_run": costs.usd(v["usd"] / len(v["runs"])),
                           "share": round(float(v["usd"] / total), 3) if total else 0.0,
                           "unmeasured_calls": v["unmeasured"]}
                          for k, v in stages.items()), key=lambda s: -s["usd"]),
    }
    for f in out["first_run"]["companies"]:
        f["usd"] = costs.usd(f["usd"])
    out["verdict"] = verdict(out, first, collect)
    return out


def verdict(out, first, collect):
    """Plain sentences comparing what was measured with the estimates."""
    lines = []
    if first:
        worst = max(f["usd"] for f in first)
        lines.append("A first run (search plus collection) cost %s at the median and %s at most, "
                     "against an estimate of %s." % (out["first_run"]["median_usd"],
                                                      costs.usd(worst),
                                                      costs.usd(ESTIMATES["first_run_usd"])))
    else:
        lines.append("No company has a finished search followed by a collection yet, so a first "
                     "run cannot be priced.")
    if collect:
        within = sum(1 for c in collect if c <= ESTIMATES["weekly_usd"])
        lines.append("%d of %d collections cost no more than the weekly estimate of %s "
                     "(median %s)." % (within, len(collect), costs.usd(ESTIMATES["weekly_usd"]),
                                        out["collect"]["median_usd"]))
    if out["over_cap"]:
        lines.append("Runs over the %s cap: %s." % (costs.usd(ESTIMATES["cap_usd"]),
                                                    ", ".join(map(str, out["over_cap"]))))
    if out["runs_with_unmeasured_calls"]:
        lines.append("%d runs hold calls without a measured cost; they are counted at their "
                     "reservation, so these figures may be slightly high."
                     % out["runs_with_unmeasured_calls"])
    return lines


def report(owner_email):
    return build(rows(owner_email))
