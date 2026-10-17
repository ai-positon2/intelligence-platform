"""Paid Marketing Agents: Pacing Desk, reading the month from Google Ads.

The pacing engine (paid_marketing/pacing_engine) judges every account on what it has spent so far this month and what its campaigns' daily
budgets are set to. This module reads exactly that straight from the Google Ads API, so the Pacing Desk no longer depends on a Google
Sheet that Supermetrics fills:

  * the month so far, one row per campaign per day (cost, conversions, conversion value, impressions, clicks, search impression share),
    with each campaign's status, bidding strategy, and its REAL daily budget and whether that budget is a shared pool;
  * every enabled campaign, even one that has not served a day this month, so its budget still counts towards what the account can
    spend (without it an account looks "budget capped" when a dark campaign holds part of the budget).

Two read-only queries per account (4 accounts at a time). An account whose read fails is reported by name and left out; it is never
turned into a row of zeros, because the engine would then call a read failure a pacing verdict.

The rows come back in the shape of the Google Ads UI's campaign export, which is the engine's primary input (paid_marketing/
pacing_engine/gads.py load_export), so nothing downstream changes. Read only: nothing here changes an account.
"""
from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads

log = logging.getLogger(__name__)

WORKERS = 4
DEFAULT_TZ = "America/Los_Angeles"      # when an account does not say its own
MAX_UNREAD_SHARE = 0.25                 # more than this share of accounts unread and the run is not worth publishing

HEADER = ["Day", "Campaign status", "Campaign", "Budget", "Budget name", "Budget type", "Account", "Campaign type",
          "Bid strategy type", "Impr.", "Search impr. share", "Cost", "Conversions", "Conv. value", "Clicks"]

TYPES = {"SEARCH": "Search", "PERFORMANCE_MAX": "Performance Max", "DEMAND_GEN": "Demand Gen", "DISCOVERY": "Demand Gen",
         "DISPLAY": "Display", "SHOPPING": "Shopping", "VIDEO": "Video", "SMART": "Smart", "LOCAL": "Local",
         "LOCAL_SERVICES": "Local Services", "MULTI_CHANNEL": "App", "HOTEL": "Hotel", "TRAVEL": "Travel"}
BIDS = {"MAXIMIZE_CONVERSIONS": "Maximize conversions", "MAXIMIZE_CONVERSION_VALUE": "Maximize conversion value",
        "TARGET_CPA": "Target CPA", "TARGET_ROAS": "Target ROAS", "TARGET_SPEND": "Maximize clicks",
        "MANUAL_CPC": "Manual CPC", "MANUAL_CPM": "Manual CPM", "TARGET_IMPRESSION_SHARE": "Target impression share",
        "TARGET_CPM": "Target CPM", "PERCENT_CPC": "Percent CPC", "COMMISSION": "Commission"}


# ── The window ──────────────────────────────────────────────────────────────

def _zone(name):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:                       # a zone name this machine does not know
        return ZoneInfo(DEFAULT_TZ)


def account_today(tz, now=None):
    """The calendar date it is right now in an account's own time zone (Google reports a day in the account's zone)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(_zone(tz)).date()


def plan_window(accounts, now=None):
    """(as_of, month_start, ends): the day the run is for, the first day of its month, and {account id: last day to read}.

    The engine treats as_of as "today" and expects data through yesterday. Accounts sit in different time zones (Position2's are mostly
    American, with a few in Australia or the UK), so the run follows the MOST COMMON yesterday among them (the earlier one on a tie): one
    account in Sydney must not move every American account's "today" a day on, which would skew every ideal-pacing figure by a whole day.
    Each account is read through its own yesterday, never past the run's (an account already a day ahead is read only that far; one a day
    behind is read through its own, and the engine judges it against its own last day). On the first of a month nothing has been spent in
    the new month yet, so the run stays on the month that just ended (as_of is its last day), the same rule the sheet-based refresh followed."""
    if not accounts:
        raise ValueError("no accounts to read")
    yest = {a["id"]: account_today(a.get("timeZone") or a.get("tz"), now) - dt.timedelta(days=1) for a in accounts}
    counts = {}
    for d in yest.values():
        counts[d] = counts.get(d, 0) + 1
    usual = min(d for d, n in counts.items() if n == max(counts.values()))
    nxt = usual + dt.timedelta(days=1)
    as_of = nxt if nxt.month == usual.month else usual
    return as_of, as_of.replace(day=1), {i: min(d, usual).isoformat() for i, d in yest.items()}


# ── Queries and parsing ─────────────────────────────────────────────────────

CAMPAIGN_FIELDS = ("campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, campaign.bidding_strategy_type, "
                   "campaign_budget.id, campaign_budget.name, campaign_budget.amount_micros, campaign_budget.explicitly_shared")


def q_days(start, end, with_share=True):
    share = ", metrics.search_impression_share" if with_share else ""
    return ("SELECT %s, segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, "
            "metrics.conversions_value%s FROM campaign WHERE segments.date BETWEEN '%s' AND '%s' AND campaign.status != 'REMOVED'"
            % (CAMPAIGN_FIELDS, share, start, end))


def q_enabled():
    return "SELECT %s FROM campaign WHERE campaign.status = 'ENABLED'" % CAMPAIGN_FIELDS


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _share(v):
    """A search impression share as a fraction, or None when Google gave none for the day."""
    if isinstance(v, bool) or v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _campaign(row):
    """The campaign's settings from either query's row."""
    c, b = row.get("campaign") or {}, row.get("campaignBudget") or {}
    shared = bool(b.get("explicitlyShared"))
    return {"id": str(c.get("id") or ""), "name": str(c.get("name") or "").strip(),
            "status": str(c.get("status") or ""), "type": TYPES.get(str(c.get("advertisingChannelType") or ""), "Other"),
            "bid": BIDS.get(str(c.get("biddingStrategyType") or ""), str(c.get("biddingStrategyType") or "").replace("_", " ").capitalize()),
            "budget": round(_f(b.get("amountMicros")) / 1e6, 2), "pool": str(b.get("name") or "").strip() if shared else ""}


def parse_days(rows):
    """[{...campaign settings, date, impr, clicks, cost, conv, value, sis}] from the per-day query."""
    out = []
    for r in rows:
        c = _campaign(r)
        day = _get(r, "segments", "date")
        if not c["id"] or not day:
            continue
        m = r.get("metrics") or {}
        c.update(date=str(day), impr=_f(m.get("impressions")), clicks=_f(m.get("clicks")), cost=round(_f(m.get("costMicros")) / 1e6, 2),
                 conv=_f(m.get("conversions")), value=_f(m.get("conversionsValue")), sis=_share(m.get("searchImpressionShare")))
        out.append(c)
    return out


def parse_enabled(rows):
    return [c for c in (_campaign(r) for r in rows) if c["id"]]


def add_idle(days, enabled, end):
    """The per-day rows plus a zero row on `end` for every enabled campaign that has none that day: a campaign that has not served
    this month still holds its budget, and one that stopped spending days ago must not make the account look as if its feed lags."""
    have = {(d["id"], d["date"]) for d in days}
    out = list(days)
    for c in enabled:
        if (c["id"], end) not in have:
            out.append(dict(c, date=end, impr=0.0, clicks=0.0, cost=0.0, conv=0.0, value=0.0, sis=None))
    return out


# ── Reading ─────────────────────────────────────────────────────────────────

def read_account(account, start, end, *, search_fn=None):
    """{rows, notes} for one account: its month so far through `end` (ISO dates). Search impression share is read with the days and, if
    Google refuses it, asked for again without it (a missing share is only a missing headroom signal). A failure of either query is
    raised. An account whose own calendar has not finished a day of the month yet (`end` before `start`, for the hours when it is
    already the next month somewhere else) has no days to read and only reports its enabled campaigns."""
    search = search_fn or gads.search
    notes, days = [], []
    if end >= start:
        try:
            days = parse_days(search(account["id"], q_days(start, end)))
        except gads.GadsError as e:
            if e.kind != "bad_request":
                raise
            days = parse_days(search(account["id"], q_days(start, end, with_share=False)))
            notes.append("impression share could not be read")
    enabled = parse_enabled(search(account["id"], q_enabled()))
    return {"rows": add_idle(days, enabled, max(end, start)), "notes": notes}


def read_all(accounts, start, ends, *, search_fn=None, workers=WORKERS):
    """{rows: [...], unread: [{id, name, reason}], notes: [...]} for the accounts. Each account is read on its own; a failure is
    recorded and the account left out. Two accounts that share a name are reported (the engine matches on the name)."""
    def one(a):
        try:
            got = read_account(a, start, ends[a["id"]], search_fn=search_fn)
            return a, got, None
        except gads.GadsError as e:
            return a, None, e
    rows, unread, notes = [], [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for a, got, err in ex.map(one, accounts):
            if err is not None:
                if err.kind in ("quota", "auth", "not_configured"):
                    raise err                  # the same reason would stop every other account: do not grind through the list
                unread.append({"id": a["id"], "name": a["name"], "reason": str(err.detail or err)[:200]})
                continue
            for r in got["rows"]:
                rows.append(dict(r, account=a["name"], accountId=a["id"]))
            for n in got["notes"]:
                notes.append("%s: %s" % (a["name"], n))
            if (a.get("currency") or "USD") != "USD":
                notes.append("%s reports in %s; its amounts are shown as dollars without any conversion" % (a["name"], a["currency"]))
    names = {}
    for a in accounts:
        names.setdefault(a["name"].strip().lower(), []).append(a["name"])
    for n in names.values():
        if len(n) > 1:
            notes.append("%d accounts are named \"%s\"; their numbers are added together" % (len(n), n[0]))
    return {"rows": rows, "unread": unread, "notes": notes}


def too_many_unread(accounts, unread):
    return bool(accounts) and len(unread) / len(accounts) > MAX_UNREAD_SHARE


# ── The engine's input ──────────────────────────────────────────────────────

def _status(s):
    return {"ENABLED": "Enabled", "PAUSED": "Paused"}.get(s, s.capitalize())


def export_rows(rows):
    """The rows in the layout of the Google Ads UI's campaign export (HEADER first), sorted by account, campaign and day."""
    out = [list(HEADER)]
    for r in sorted(rows, key=lambda r: (r["account"].lower(), r["name"].lower(), r["date"])):
        out.append([r["date"], _status(r["status"]), r["name"], r["budget"], r["pool"] or "--", "Standard" if not r["pool"] else "Shared",
                    r["account"], r["type"], r["bid"], r["impr"], r["sis"] if r["sis"] is not None else " --", r["cost"], r["conv"],
                    r["value"], r["clicks"]])
    return out
