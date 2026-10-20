"""Paid Marketing Agents: Brand IS Monitor, the reading and the report.

For a Google Ads account (or every account of an MCC) this reads, for every enabled Search campaign and every whole week of the last eight, the
impressions, clicks, cost and the Search impression share with the shares lost to budget and to rank (one read per account). The arithmetic and
the findings are tracker/paid_brandis_math.py. Google Ads only: Meta has no impression share. Nothing is changed in the account.

The read is cached like the other Google agents (tracker/paid_geo.py _cached): one read per account a day-ish, Refresh limited, a spent quota
served from the last saved read with its date. The cache holds the rows, not the verdict, so changing the threshold or which campaigns count as
brand never reads Google again.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_brandis_math as m
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_MEMBERS = seg.MAX_MEMBERS

_FIELDS = ("campaign.id, campaign.name, campaign.bidding_strategy_type, campaign_budget.amount_micros, segments.week, "
           "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.search_impression_share, "
           "metrics.search_budget_lost_impression_share, metrics.search_rank_lost_impression_share, "
           "metrics.search_top_impression_share, metrics.search_absolute_top_impression_share")


def gaql(first, last):
    """The read: every enabled Search campaign, one row per campaign per week, between two dates (a Monday and a Sunday)."""
    return ("SELECT %s FROM campaign WHERE campaign.advertising_channel_type = 'SEARCH' AND campaign.status = 'ENABLED' "
            "AND segments.date BETWEEN '%s' AND '%s'" % (_FIELDS, first, last))


# ── Reading the API's rows ──────────────────────────────────────────────────

def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_rows(api_rows):
    """The campaign-week rows (the shape tracker/paid_brandis_math.py reads) from Google's answer. A row without a campaign or a week is dropped."""
    out = []
    for r in api_rows:
        cid = str(_get(r, "campaign", "id", default="") or "")
        week = str(_get(r, "segments", "week", default="") or "")
        if not cid or not week:
            continue
        mt = r.get("metrics") or {}
        micros = _num(mt.get("costMicros"))
        budget = _num(_get(r, "campaignBudget", "amountMicros"))
        out.append({"id": cid, "name": _get(r, "campaign", "name", default=""), "week": week,
                    "strategy": str(_get(r, "campaign", "biddingStrategyType", default="") or ""),
                    "budget": None if budget is None else round(budget / 1e6, 2),
                    "impressions": int(_num(mt.get("impressions")) or 0), "clicks": int(_num(mt.get("clicks")) or 0),
                    "cost": round((micros or 0.0) / 1e6, 2),
                    "share": _num(mt.get("searchImpressionShare")), "lostBudget": _num(mt.get("searchBudgetLostImpressionShare")),
                    "lostRank": _num(mt.get("searchRankLostImpressionShare")), "top": _num(mt.get("searchTopImpressionShare")),
                    "absTop": _num(mt.get("searchAbsoluteTopImpressionShare"))})
    return out


def google_raw(account, today, *, search_fn=None, workers=6):
    """{accounts: [{id, name, currency, rows}], warnings, members}: the raw read of one account or an MCC's accounts together. An account that
    cannot be read is left out and said so; every one failing is raised."""
    search = search_fn or gads.search
    members = account.get("members") or [account]
    wk = m.weeks(today)
    first, last = wk[0][0], wk[-1][1]

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
    return {"accounts": [{"id": mem["id"], "name": mem["name"], "currency": mem.get("currency") or account.get("currency") or "USD", "rows": rows}
                         for mem, rows in ok],
            "warnings": ["%s could not be read: %s" % (mem["name"], e) for mem, rows, e in done if e],
            "members": [mem["name"] for mem in members] if account.get("members") else []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"below": 0, "unmeasured": 1, "thin": 2, "none": 3, "ok": 4}


def parse_threshold(value):
    """A threshold between 0.50 and 0.99 from "90", "90%", "0.9" or a number; None or "" gives the default."""
    if value in (None, ""):
        return m.DEFAULT_THRESHOLD
    try:
        v = float(str(value).strip().rstrip("%"))
    except ValueError:
        raise BadRequest("The threshold must be a number between 50 and 99 (a percentage).")
    v = v / 100.0 if v > 1 else v
    if not (m.MIN_THRESHOLD - 1e-9 <= v <= m.MAX_THRESHOLD + 1e-9):
        raise BadRequest("The threshold must be between 50% and 99%.")
    return round(v, 4)


def parse_overrides(brand=None, not_brand=None):
    """{campaign id: True or False} from two lists (or comma separated text) of campaign ids the person marked as brand or not brand."""
    def ids(v):
        if isinstance(v, str):
            v = v.split(",")
        return [str(x).strip() for x in (v or []) if str(x).strip().isdigit()]
    out = {i: False for i in ids(not_brand)}
    out.update({i: True for i in ids(brand)})
    if len(out) > 500:
        raise BadRequest("Too many campaigns marked.")
    return out


def assemble(raw, account, today, threshold, overrides):
    accounts = []
    for a in raw["accounts"]:
        result = m.analyse(a["rows"], today, threshold=threshold, overrides=overrides)
        accounts.append(dict(result, id=a["id"], name=a["name"], currency=a["currency"], campaignCount=len({r["id"] for r in a["rows"]})))
    accounts.sort(key=lambda a: (_RANK[a["status"]], a["brand"]["share"] if a["status"] == "below" else 0, a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    wk = m.weeks(today)
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "window": {"from": wk[0][0].isoformat(), "to": wk[-1][1].isoformat(), "weeks": len(wk), "latestFrom": wk[-1][0].isoformat(), "latestTo": wk[-1][1].isoformat()},
        "threshold": threshold, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "warnings": list(raw.get("warnings") or []), "accounts": accounts,
        "summary": {"counts": counts, "accounts": len(accounts), "below": counts["below"],
                    "high": sum(1 for a in accounts if a["findings"] and a["findings"][0]["level"] == "high"),
                    "dropped": sum(1 for a in accounts if any(f["kind"] == "dropped" for f in a["findings"]))},
    }


def report_for(account_id, *, threshold=None, overrides=None, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None):
    """The Brand IS report for a Google Ads account or an MCC the root MCC holds. `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_geo, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    th = threshold if isinstance(threshold, float) else parse_threshold(threshold)
    account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
    raw, stale = paid_geo._cached("brandis", account["id"], "wk", day, day,
                                  lambda: google_raw(account, day, search_fn=search_fn), force=force, store=store, now=now)
    report = assemble(raw, account, day, th, overrides or {})
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
