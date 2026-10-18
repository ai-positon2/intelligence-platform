"""Paid Marketing Agents: Pacing Desk, reading the month from Meta Ads.

The pacing engine judges an account on what it has spent this month and what its campaigns' daily budgets are set to. For a Meta ad account
that is read here and handed to the engine in exactly the rows tracker/paid_pacing_read.py gives it for a Google account (one row per campaign
per day), so the engine, the page and the saved runs need no second shape:

  * the days: each campaign's spend, link clicks, impressions and results (a result is what the campaign's objective counts, see
    tracker/paid_meta.py) for every day of the month so far;
  * each campaign's daily budget: the campaign's own when it has one (a campaign budget), else the sum of its running ad sets' daily budgets;
    a LIFETIME budget is spread over the days of its flight, and a campaign whose budget cannot be told (a lifetime budget with no end
    date) is listed with none and said so, because the engine would otherwise call an account budget-capped for a budget nobody knows.

Which Meta ad accounts are read: those an allocation row for the Meta channel names (the allocation is what the account is paced against;
an ad account with none has nothing to be paced against). An allocation row is matched to an ad account by name (case, spacing and
punctuation ignored; one name inside the other counts when only one ad account fits), and the spend is named as the ALLOCATION names the
account, which is what the engine matches on. A row that fits no ad account, or fits several, is said so and not guessed.

A failure of any part of one account is that account's alone (it is left out and named, never turned into a row of zeros). Meta asking
the app to wait stops the read and is raised: every other account would only be told the same.

Read only: nothing here changes an account.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads as base
from . import paid_meta as meta
from . import paid_meta_segments as mseg
from . import paid_pacing_read as reader

WORKERS = 2                                  # Meta rate-limits an app far sooner than Google does
CAMPAIGN_FIELDS = "id,name,effective_status,objective,bid_strategy,daily_budget,lifetime_budget,start_time,stop_time"
ADSET_FIELDS = "id,campaign_id,effective_status,daily_budget,lifetime_budget,start_time,end_time"
DAY_FIELDS = "campaign_id,campaign_name,objective,spend,impressions,inline_link_clicks,actions,action_values"
BIDS = {"LOWEST_COST_WITHOUT_CAP": "Lowest cost", "LOWEST_COST_WITH_BID_CAP": "Bid cap", "COST_CAP": "Cost cap", "LOWEST_COST_WITH_MIN_ROAS": "Minimum ROAS"}
RUNNING = ("ACTIVE", "WITH_ISSUES", "IN_PROCESS")


def norm(name):
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def match_accounts(rows, accounts):
    """([(allocation row, Meta ad account)], [reason an allocation row was not matched]) for the Meta rows of the month's allocations."""
    found, notes = [], []
    for r in rows:
        if r.get("channel") != "Meta":
            continue
        want = norm(r["account"])
        exact = [a for a in accounts if norm(a["name"]) == want]
        near = exact or [a for a in accounts if want and (want in norm(a["name"]) or norm(a["name"]) in want)]
        if len(near) == 1:
            found.append((r, near[0]))
        elif not near:
            notes.append("The Meta allocation for %s / %s fits no Meta ad account the token can read, so it is not paced." % (r["client"], r["account"]))
        else:
            notes.append("The Meta allocation for %s / %s fits %d Meta ad accounts (%s), so it is not paced. Name the account exactly as Meta does." % (
                r["client"], r["account"], len(near), ", ".join(a["name"] for a in near[:3])))
    return found, notes


def _time(v):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return None


def _money(v, currency):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return round(x if currency in meta.ZERO_DECIMAL else x / 100.0, 2)


def _flight_days(start, end):
    s, e = _time(start), _time(end)
    if not s or not e or e <= s:
        return None
    return max(1, (e - s).days + (1 if (e - s).seconds else 0))


def daily_budget(camp, adsets, currency):
    """(dollars a day, note or None) for one campaign: its own daily budget, else its running ad sets' summed; a lifetime budget over its flight."""
    own = _money(camp.get("daily_budget"), currency)
    if own:
        return own, None
    life = _money(camp.get("lifetime_budget"), currency)
    if life:
        n = _flight_days(camp.get("start_time"), camp.get("stop_time"))
        return (round(life / n, 2), None) if n else (0.0, "%s has a lifetime budget and no end date, so its budget a day is not known" % camp.get("name"))
    day, notes = 0.0, 0
    for s in adsets:
        if s.get("effective_status") not in RUNNING:
            continue
        d = _money(s.get("daily_budget"), currency)
        if d:
            day += d
            continue
        life = _money(s.get("lifetime_budget"), currency)
        n = _flight_days(s.get("start_time"), s.get("end_time"))
        if life and n:
            day += life / n
        elif life:
            notes += 1
    return round(day, 2), ("%s has ad sets on a lifetime budget with no end date, so part of its budget a day is not known" % camp.get("name")) if notes else None


def _status(s):
    return meta.STATUS.get(str(s or ""), "ENABLED")


def campaign_row(camp, budget):
    return {"id": str(camp.get("id") or ""), "name": str(camp.get("name") or "").strip(), "status": _status(camp.get("effective_status")),
            "type": mseg.campaign_type(camp.get("objective")),
            "bid": BIDS.get(str(camp.get("bid_strategy") or ""), str(camp.get("bid_strategy") or "Lowest cost").replace("_", " ").capitalize()),
            "budget": budget, "pool": ""}


def parse_days(rows, camps):
    """[{...campaign, date, impr, clicks, cost, conv, value, sis}] from the daily insights rows; `camps` is {campaign id: campaign row}."""
    out = []
    for r in rows:
        cid, day = str(r.get("campaign_id") or ""), r.get("date_start")
        if not cid or not day:
            continue
        c = camps.get(cid) or campaign_row({"id": cid, "name": r.get("campaign_name"), "objective": r.get("objective")}, 0.0)
        n = meta.numbers(r)
        out.append(dict(c, date=str(day), impr=float(n[0]), clicks=float(n[1]), cost=round(float(n[2]), 2), conv=float(n[3]), value=float(n[4]), sis=None))
    return out


def ends_for(accounts, as_of, now=None):
    """{ad account id: the last day to read}: the day before the run's own, and never past the account's own yesterday (its time zone)."""
    last = as_of - dt.timedelta(days=1)
    return {a["id"]: min(reader.account_today(a.get("tz") or a.get("timeZone"), now) - dt.timedelta(days=1), last).isoformat() for a in accounts}


def read_account(account, start, end, *, store, fetch=None):
    """{rows, notes} for one Meta ad account's month so far through `end`: a row per campaign per day (a zero row on `end` for a running campaign with
    none, as for Google), each with its settings and budget."""
    pages = fetch or (lambda path, params: meta._pages(path, params, store))
    act, cur = account["id"], account.get("currency") or "USD"
    camps_raw = pages(act + "/campaigns", {"fields": CAMPAIGN_FIELDS, "limit": meta.PAGE_LIMIT})
    sets = pages(act + "/adsets", {"fields": ADSET_FIELDS, "limit": meta.PAGE_LIMIT, "effective_status": json.dumps(list(RUNNING))})
    by_camp = {}
    for s in sets:
        by_camp.setdefault(str(s.get("campaign_id") or ""), []).append(s)
    notes, camps = [], {}
    for c in camps_raw:
        budget, note = daily_budget(c, by_camp.get(str(c.get("id") or ""), []), cur)
        if note and c.get("effective_status") in RUNNING:
            notes.append(note)
        camps[str(c.get("id") or "")] = campaign_row(c, budget)
    days = []
    if end >= start:
        days = parse_days(pages(act + "/insights", {"level": "campaign", "time_increment": 1, "fields": DAY_FIELDS, "limit": meta.PAGE_LIMIT,
                                                    "time_range": json.dumps({"since": start, "until": end})}), camps)
    enabled = [c for c in camps.values() if c["status"] == "ENABLED"]
    return {"rows": reader.add_idle(days, enabled, max(end, start)), "notes": notes}


def read_all(matched, start, ends, *, store, fetch=None, workers=WORKERS):
    """{rows, unread, notes} for the matched [(allocation row, Meta ad account)]. Each is read on its own and named as the allocation names it;
    a failure is recorded and the account left out. Meta asking to wait is raised."""
    def one(pair):
        row, acct = pair
        try:
            return pair, read_account(acct, start, ends[acct["id"]], store=store, fetch=fetch), None
        except meta.OverQuota:
            raise
        except base.AdsError as e:
            return pair, None, e
    rows, unread, notes = [], [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for (row, acct), got, err in ex.map(one, matched):
            if err is not None:
                unread.append({"id": acct["id"], "name": row["account"], "reason": str(getattr(err, "detail", None) or err)[:200]})
                continue
            rows += [dict(r, account=row["account"], accountId=acct["id"]) for r in got["rows"]]
            notes += ["%s (Meta): %s" % (row["account"], n) for n in got["notes"]]
            if (acct.get("currency") or "USD") != "USD":
                notes.append("%s (Meta) reports in %s; its amounts are shown as dollars without any conversion" % (row["account"], acct["currency"]))
    return {"rows": rows, "unread": unread, "notes": notes}
