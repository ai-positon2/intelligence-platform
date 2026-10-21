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
           "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.search_impression_share, metrics.search_budget_lost_impression_share, "
           "metrics.search_rank_lost_impression_share")
GAQL_CAMPAIGNS = "SELECT campaign.id, campaign.name, campaign.status FROM campaign WHERE campaign.advertising_channel_type = 'SEARCH' AND campaign.status != 'REMOVED'"
TERMS_MAX = 5000                 # search terms read for the leak (the dearest first)


def gaql_terms(first, last):
    """The search terms the Search campaigns paid for between two dates, the dearest first: the leak looks in them for the team's brand words."""
    return ("SELECT campaign.id, campaign.name, search_term_view.search_term, metrics.cost_micros, metrics.clicks, metrics.conversions FROM search_term_view "
            "WHERE campaign.advertising_channel_type = 'SEARCH' AND segments.date BETWEEN '%s' AND '%s' AND metrics.cost_micros > 0 "
            "ORDER BY metrics.cost_micros DESC LIMIT %d" % (first, last, TERMS_MAX))


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
        share = {k: (None if mt.get(f) is None else _num(mt.get(f))) for k, f in (("share", "searchImpressionShare"), ("lostBudget", "searchBudgetLostImpressionShare"),
                                                                                    ("lostRank", "searchRankLostImpressionShare"))}
        out.append({"id": cid, "name": c.get("name") or "", "date": day, "status": str(c.get("status") or ""), "strategy": str(c.get("biddingStrategyType") or ""),
                    "budget": None if budget is None else round(budget / 1e6, 2), "impressions": impressions, "clicks": clicks, "cost": round(micros / 1e6, 2),
                    "conversions": round(_num(mt.get("conversions")) or 0.0, 2), **share})
    return out


def read_campaigns(api_rows):
    """[{id, name, status}] of every Search campaign Google lists that is not removed."""
    out, seen = [], set()
    for r in api_rows:
        c = r.get("campaign") or {}
        if c.get("id") and str(c["id"]) not in seen:
            seen.add(str(c["id"]))
            out.append({"id": str(c["id"]), "name": c.get("name") or "", "status": str(c.get("status") or "")})
    return out


def read_terms(api_rows):
    """[{campaignId, campaign, term, cost, clicks, conversions}] from the search term read."""
    out = []
    for r in api_rows:
        c, t, mt = r.get("campaign") or {}, r.get("searchTermView") or {}, r.get("metrics") or {}
        if c.get("id") and t.get("searchTerm"):
            out.append({"campaignId": str(c["id"]), "campaign": c.get("name") or "", "term": str(t["searchTerm"]), "cost": round((_num(mt.get("costMicros")) or 0.0) / 1e6, 2),
                        "clicks": int(_num(mt.get("clicks")) or 0), "conversions": round(_num(mt.get("conversions")) or 0.0, 2)})
    return out


def leak_window(today):
    """(first, last) days of the search terms read: the LEAK_DAYS days that ended yesterday."""
    from . import paid_brandspend_insight as ins
    return today - dt.timedelta(days=ins.LEAK_DAYS), today - dt.timedelta(days=1)


def google_raw(account, today, *, search_fn=None, workers=6):
    """{accounts: [{id, name, currency, rows, campaigns, warnings}], warnings, members}: the raw read of one account or an MCC's accounts together. An account that
    cannot be read is left out and said so; every one failing is raised. The list of campaigns is soft: without it a brand campaign that never spent cannot be seen."""
    search = search_fn or gads.search
    members = account.get("members") or [account]
    first, last = m.window(today)

    def run(member):
        try:
            rows = read_rows(search(member["id"], gaql(first, last)))
        except gads.GadsError as e:
            return member, None, e
        extra = {"campaigns": None, "warnings": []}
        try:
            extra["campaigns"] = read_campaigns(search(member["id"], GAQL_CAMPAIGNS))
        except gads.GadsError as e:
            extra["warnings"].append("The list of campaigns could not be read (%s), so a brand campaign that never spent cannot be seen." % e)
        return member, dict(extra, rows=rows), None
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as ex:
        done = list(ex.map(run, members))
    ok = [(mem, got) for mem, got, e in done if got is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    return {"accounts": [{"id": mem["id"], "name": mem["name"], "currency": mem.get("currency") or account.get("currency") or "USD", "rows": got["rows"], "campaigns": got["campaigns"],
                          "warnings": got["warnings"]} for mem, got in ok],
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


def snapshot_of(a):
    """The figures of one analysed account that the monitor keeps each day for the history."""
    return {"status": a["status"], "cost": a["brand"]["cost"], "ratio": a.get("ratio"), "level": a.get("level"), "share": (a.get("share") or {}).get("now"),
            "usual": (a.get("expected") or {}).get("cost")}


def assemble(raw, account, today, limit, overrides, settings=None, leaks=None, histories=None):
    """The report from the raw read. `settings` is what the team saved (paid_brandspend_settings.load), `leaks` {account id: search term rows} for the accounts with brand words, `histories`
    {account id: [{day, status, cost}]} from the monitor's daily figures; each reading that needs one says so when it is missing."""
    from . import paid_brandspend_settings as st
    accounts, warnings = [], list(raw.get("warnings") or [])
    for a in raw["accounts"]:
        saved = st.account_of(settings or {}, a["id"])
        extra = {"campaigns": a.get("campaigns"), "settings": saved, "history": (histories or {}).get(a["id"]), "leak": (leaks or {}).get(a["id"])}
        result = m.analyse(a["rows"], today, limit=limit, overrides=overrides, currency=a["currency"], extra=extra)
        accounts.append(dict(result, id=a["id"], name=a["name"], currency=a["currency"], campaignCount=len({r["id"] for r in a["rows"]}), saved=saved))
        warnings += ["%s: %s" % (a["name"], w) if account.get("members") else w for w in a.get("warnings") or []]
        if saved["brandTerms"] and extra["leak"] is None:
            warnings.append("%sThe search terms could not be read, so brand searches paid for by other campaigns were not looked for." % ("%s: " % a["name"] if account.get("members") else ""))
    accounts.sort(key=lambda a: (_RANK[a["status"]], -abs((a["ratio"] or 1.0) - 1.0), a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    wk = m.weeks(today)
    high = sum(1 for a in accounts if a.get("level") == "high" or any(f["level"] == "high" for f in a["findings"]))
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "window": {"from": wk[0][0].isoformat(), "to": wk[-1][1].isoformat(), "weeks": len(wk), "latestFrom": wk[-1][0].isoformat(), "latestTo": wk[-1][1].isoformat()},
        "limit": limit, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "warnings": warnings, "accounts": accounts,
        "summary": {"counts": counts, "accounts": len(accounts), "needsLook": sum(1 for a in accounts if a["status"] in ("stopped", "over", "under") or any(f["level"] in ("high", "medium") for f in a["findings"])),
                    "high": high, "thisWeek": sum(1 for a in accounts if a["partial"] and a["partial"]["status"] != "ok" and a["status"] in ("ok", "new")),
                    "overall": overall(accounts)},
    }


def read_leaks(accounts, today, *, search_fn=None, store=None, now=None, force=False):
    """({account id: search term rows}, warnings) for the accounts that have brand words saved. Each account's terms are one read, kept like the rest; one that fails is left out."""
    from . import paid_geo, paid_store
    store = store or paid_store
    search = search_fn or gads.search
    first, last = leak_window(today)
    out, warnings = {}, []
    for a in accounts:
        try:
            raw, _stale = paid_geo._cached("brandspend-leak", a["id"], "t", today, today, lambda a=a: {"rows": read_terms(search(a["id"], gaql_terms(first, last)))}, force=force, store=store, now=now)
            out[a["id"]] = raw["rows"]
        except gads.GadsError as e:
            warnings.append("%s: the search terms could not be read (%s)." % (a["name"], e))
    return out, warnings


def histories_for(mon, ids, today):
    """{account id: the daily figures saved for it, the last 60 days} from the monitor's store; {} when there is no store or it is unavailable."""
    if mon is None:
        return {}
    since = (today - dt.timedelta(days=60)).isoformat()
    try:
        return {i: mon.snaps_get("brandspend", i, since) for i in ids}
    except Exception:                                                                  # noqa: BLE001 (no history is not a reason to fail the report)
        return {}


def report_for(account_id, *, limit=None, overrides=None, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None, mon=None, settings=None):
    """The Brand Spend report for a Google Ads account or an MCC the root MCC holds. `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account. `mon` is the monitors' store (the team's settings and the daily history come from it; without one the defaults apply),
    `settings` those settings when the caller has them already."""
    from . import paid_brandspend_settings as st
    from . import paid_geo, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    warnings = []
    if settings is None:
        settings = {}
        if mon is not None:
            try:
                settings = st.load(mon)
            except Exception as e:                                                       # noqa: BLE001
                warnings.append("The team's saved settings could not be read (%s), so the defaults were used." % e)
    lim = limit if isinstance(limit, float) else parse_limit(limit) if limit not in (None, "") else (settings.get("limit") or m.DEFAULT_LIMIT)
    marks = {k: bool(v) for k, v in (settings.get("marks") or {}).items()}
    marks.update(overrides or {})
    account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
    raw, stale = paid_geo._cached("brandspend", account["id"], "wk", day, day, lambda: google_raw(account, day, search_fn=search_fn), force=force, store=store, now=now)
    with_terms = [a for a in raw["accounts"] if st.account_of(settings, a["id"])["brandTerms"]]
    leaks, leak_warnings = read_leaks(with_terms, day, search_fn=search_fn, store=store, now=now, force=force) if with_terms else ({}, [])
    report = assemble(raw, account, day, lim, marks, settings=settings, leaks=leaks, histories=histories_for(mon, [a["id"] for a in raw["accounts"]], day))
    report["warnings"] = warnings + report["warnings"] + leak_warnings
    report["saved"] = {"limit": settings.get("limit"), "marks": settings.get("marks") or {}, "teamSettings": mon is not None and not warnings}
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
