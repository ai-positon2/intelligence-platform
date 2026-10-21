"""Paid Marketing Agents: Brand Spend Alert, the reading and the report.

For a Google Ads account (or every account of an MCC) this reads, for every Search campaign that is not removed and every day from the oldest of the last eight whole weeks to
yesterday, the impressions, clicks and cost (one read per account). The arithmetic and the findings are tracker/paid_brandspend_math.py, the fix plan paid_brandspend_plan.py.
Google Ads only. Nothing is changed in the account.

The read is cached like the other Google agents (tracker/paid_geo.py _cached): one read per account a day-ish, Refresh limited, a spent quota served from the last saved read with
its date. The cache holds the rows, not the verdict, so changing the limit or which campaigns count as brand never reads Google again.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_brandis as bis
from . import paid_brandspend_math as m
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_MEMBERS = seg.MAX_MEMBERS
parse_overrides = bis.parse_overrides

_FIELDS = ("campaign.id, campaign.name, campaign.status, campaign.bidding_strategy_type, campaign_budget.amount_micros, segments.date, "
           "metrics.impressions, metrics.clicks, metrics.cost_micros")


def gaql(first, last):
    """The read: every Search campaign that is not removed, one row per campaign per day, between two dates."""
    return ("SELECT %s FROM campaign WHERE campaign.advertising_channel_type = 'SEARCH' AND campaign.status != 'REMOVED' "
            "AND segments.date BETWEEN '%s' AND '%s'" % (_FIELDS, first, last))


# ── Reading the API's rows ──────────────────────────────────────────────────

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_rows(api_rows):
    """The campaign-day rows (the shape tracker/paid_brandspend_math.py reads) from Google's answer. A row without a campaign or a day is dropped, and so is one with no
    impressions, clicks or cost (it says nothing and a month of them for every campaign would fill the saved copy)."""
    out = []
    for r in api_rows:
        c = r.get("campaign") or {}
        cid, day = str(c.get("id") or ""), str((r.get("segments") or {}).get("date") or "")
        if not cid or not day:
            continue
        mt = r.get("metrics") or {}
        micros = _num(mt.get("costMicros")) or 0.0
        impressions, clicks = int(_num(mt.get("impressions")) or 0), int(_num(mt.get("clicks")) or 0)
        if micros <= 0 and not impressions and not clicks:
            continue
        budget = _num((r.get("campaignBudget") or {}).get("amountMicros"))
        out.append({"id": cid, "name": c.get("name") or "", "date": day, "status": str(c.get("status") or ""), "strategy": str(c.get("biddingStrategyType") or ""),
                    "budget": None if budget is None else round(budget / 1e6, 2), "impressions": impressions, "clicks": clicks, "cost": round(micros / 1e6, 2)})
    return out


def google_raw(account, today, *, search_fn=None, workers=6):
    """{accounts: [{id, name, currency, rows}], warnings, members}: the raw read of one account or an MCC's accounts together. An account that
    cannot be read is left out and said so; every one failing is raised."""
    search = search_fn or gads.search
    members = account.get("members") or [account]
    first, last = m.window(today)

    def run(member):
        try:
            return member, read_rows(search(member["id"], gaql(first, last))), None
        except gads.GadsError as e:
            return member, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as ex:
        done = list(ex.map(run, members))
    ok = [(mem, rows) for mem, rows, e in done if rows is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    return {"accounts": [{"id": mem["id"], "name": mem["name"], "currency": mem.get("currency") or account.get("currency") or "USD", "rows": rows} for mem, rows in ok],
            "warnings": ["%s could not be read: %s" % (mem["name"], e) for mem, rows, e in done if e],
            "members": [mem["name"] for mem in members] if account.get("members") else []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"stopped": 0, "over": 1, "under": 2, "new": 3, "ok": 4, "idle": 5, "none": 6}


def parse_limit(value):
    """A limit between 0.10 and 0.90 from "30", "30%", "0.3" or a number; None or "" gives the default."""
    if value in (None, ""):
        return m.DEFAULT_LIMIT
    try:
        v = float(str(value).strip().rstrip("%"))
    except ValueError:
        raise BadRequest("The limit must be a number between 10 and 90 (a percentage).")
    v = v / 100.0 if v > 1 else v
    if not (m.MIN_LIMIT - 1e-9 <= v <= m.MAX_LIMIT + 1e-9) or v != v:
        raise BadRequest("The limit must be between 10% and 90%.")
    return round(v, 4)


def overall(accounts):
    """Last week's brand spend of every account that has a usual week to compare with, and what a usual week of those accounts is:
    {cost, expected, accounts}; both None when no account had one."""
    judged = [a for a in accounts if a.get("expected")]
    if not judged:
        return {"cost": None, "expected": None, "accounts": 0}
    return {"cost": round(sum(a["brand"]["cost"] for a in judged), 2), "expected": round(sum(a["expected"]["cost"] for a in judged), 2), "accounts": len(judged)}


def assemble(raw, account, today, limit, overrides):
    accounts = []
    for a in raw["accounts"]:
        result = m.analyse(a["rows"], today, limit=limit, overrides=overrides, currency=a["currency"])
        accounts.append(dict(result, id=a["id"], name=a["name"], currency=a["currency"], campaignCount=len({r["id"] for r in a["rows"]})))
    accounts.sort(key=lambda a: (_RANK[a["status"]], -abs((a["ratio"] or 1.0) - 1.0), a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    wk = m.weeks(today)
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "window": {"from": wk[0][0].isoformat(), "to": wk[-1][1].isoformat(), "weeks": len(wk), "latestFrom": wk[-1][0].isoformat(), "latestTo": wk[-1][1].isoformat()},
        "limit": limit, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "warnings": list(raw.get("warnings") or []), "accounts": accounts,
        "summary": {"counts": counts, "accounts": len(accounts), "needsLook": counts["stopped"] + counts["over"] + counts["under"],
                    "high": sum(1 for a in accounts if a.get("level") == "high"),
                    "thisWeek": sum(1 for a in accounts if a["partial"] and a["partial"]["status"] != "ok" and a["status"] in ("ok", "new")),
                    "overall": overall(accounts)},
    }


def report_for(account_id, *, limit=None, overrides=None, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None):
    """The Brand Spend report for a Google Ads account or an MCC the root MCC holds. `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_geo, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    lim = limit if isinstance(limit, float) else parse_limit(limit)
    account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
    raw, stale = paid_geo._cached("brandspend", account["id"], "wk", day, day, lambda: google_raw(account, day, search_fn=search_fn), force=force, store=store, now=now)
    report = assemble(raw, account, day, lim, overrides or {})
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
