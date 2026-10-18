"""Paid Marketing Agents: Pacing Desk, the monthly budgets the engine paces against.

Google Ads knows what an account has spent and what each campaign's daily budget is set to. It does not know what the CLIENT agreed to
spend this month, and that figure (the allocation) is what every pacing verdict is measured against. It used to be read from the
team's Budget Tracker sheet. It now lives in the platform, one row per client and account per month:

    client | account | allocated (the month's budget) | target CPA (optional) | days running (optional) | status | AM

The rows are kept by tracker/paid_pacing_budget_store.py. This module is the logic that needs no database: checking a row, reading
rows pasted from the Budget Tracker (a copy of its cells, not a connection to it), carrying last month's rows forward when a month
starts, seeding the very first month from the last run the platform stored, and laying the rows out the way the engine reads them.

A row belongs to a channel: Google (the default) or Meta. The Pacing Desk paces both; a pasted row for any other channel is skipped and said so.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_ROWS = 600
MAX_SKIPPED = 40                               # skipped rows listed back; the rest are counted
MAX_ALLOCATED = 10_000_000
MAX_TARGET = 1_000_000
DPR_HEADER = ["Client", "Account", "Allocated Budget", "Target CPA/ROAS/CPM", "Date", "Account Status", "Days Ads are Running", "AM", "Channels"]
STATUSES = ("Active", "Paused")
CHANNELS = ("Google", "Meta")

_DAYS = {"monday", "mon", "tuesday", "tue", "tues", "wednesday", "wed", "thursday", "thu", "thur", "thurs", "friday", "fri", "saturday",
         "sat", "sunday", "sun"}
_ALL_DAYS = {"", "all", "all days", "daily", "every day", "everyday", "7 days", "7 days a week"}

HEADERS = {   # normalised header -> field
    "client": "client", "account": "account", "accountname": "account",
    "allocatedbudget": "allocated", "allocated": "allocated", "budget": "allocated", "monthlybudget": "allocated",
    "targetcparoascpm": "targetCpa", "targetcpa": "targetCpa", "target": "targetCpa", "targetcpl": "targetCpa",
    "date": "date", "month": "date",
    "accountstatus": "status", "status": "status",
    "daysadsarerunning": "daysRunning", "daysrunning": "daysRunning", "days": "daysRunning",
    "am": "am", "accountmanager": "am", "channels": "channel", "channel": "channel", "platform": "channel",
}
DEFAULT_ORDER = ["client", "account", "allocated", "targetCpa", "date", "status", "daysRunning", "am", "channel"]   # the tracker's own column order


class BadBudget(ValueError):
    """A budget row or paste that cannot be used; the message says what to fix."""


def month_of(day):
    """'YYYY-MM' of a date or an ISO date string."""
    if isinstance(day, str):
        day = dt.date.fromisoformat(day[:10])
    return "%04d-%02d" % (day.year, day.month)


def check_month(month):
    if not isinstance(month, str) or not MONTH_RE.match(month):
        raise BadBudget("month should look like 2026-10")
    return month


def previous_month(month):
    y, m = int(month[:4]), int(month[5:])
    return "%04d-%02d" % ((y - 1, 12) if m == 1 else (y, m - 1))


def row_key(client, account, channel="Google"):
    """What makes two rows the same one: client, account and channel, ignoring case and spacing. A Google row's key is what it always was, so rows saved
    before there were channels are still the same rows."""
    key = re.sub(r"\s+", " ", str(client).strip().lower()) + "|" + re.sub(r"\s+", " ", str(account).strip().lower())
    return key + "|meta" if channel == "Meta" else key


def channel_of(text):
    """'Google' or 'Meta' from a channel cell (empty means Google), or None for any other channel (Bing, LinkedIn, ...)."""
    s = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not s:
        return "Google"
    if "google" in s or s in ("adwords", "ppc"):
        return "Google"
    if "meta" in s or "facebook" in s or "instagram" in s or s in ("fb", "ig"):
        return "Meta"
    return None


# ── One row ─────────────────────────────────────────────────────────────────

def money(v, field):
    """A number from a cell: 1200, "1,200.50", "$1,200". Empty gives None. Raises BadBudget for anything else."""
    if isinstance(v, bool):
        raise BadBudget("%s should be a number" % field)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[,$\s]", "", str(v))
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        raise BadBudget("%s should be a number, got %r" % (field, str(v)[:40]))


def days_running(text):
    """The days an account's ads run, as the engine reads them ("Monday - Friday", "Monday, Wednesday"); "" for every day.
    Raises BadBudget for text the engine would silently read as every day."""
    s = re.sub(r"\s+", " ", str(text or "").strip().lower().replace("–", "-").replace("—", "-"))
    if s in _ALL_DAYS:
        return ""
    words = re.findall(r"[a-z]+", s)
    if not words or any(w not in _DAYS for w in words):
        raise BadBudget("days running should look like \"Monday - Friday\" or \"Monday, Wednesday, Thursday\" (or be empty for every day), got %r" % str(text)[:40])
    if "-" in s and len(words) != 2:                  # the engine reads the first range it finds and drops every other day named
        raise BadBudget("days running is either a range (\"Monday - Friday\") or a list (\"Monday, Wednesday, Thursday\"), not both, got %r" % str(text)[:40])
    return re.sub(r"\s+", " ", str(text).strip())


def _text(v, field, limit, required=False):
    s = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
    if required and not s:
        raise BadBudget("%s is required" % field)
    if len(s) > limit:
        raise BadBudget("%s is longer than %d characters" % (field, limit))
    return s


def check_row(body):
    """The row to store from one submitted row: {client, account, allocated, targetCpa, daysRunning, status, am, channel}. Raises BadBudget."""
    if not isinstance(body, dict):
        raise BadBudget("a budget row should be a JSON object")
    client = _text(body.get("client"), "client", 120, required=True)
    account = _text(body.get("account"), "account", 200, required=True)
    allocated = money(body.get("allocated"), "allocated budget")
    if allocated is None:
        raise BadBudget("allocated budget is required")
    if allocated < 0 or allocated > MAX_ALLOCATED:
        raise BadBudget("allocated budget should be between 0 and %s" % format(MAX_ALLOCATED, ","))
    target = money(body.get("targetCpa"), "target CPA")
    if target is not None and not 0 < target <= MAX_TARGET:
        raise BadBudget("target CPA should be more than 0 (or empty)")
    status = _text(body.get("status"), "status", 40)
    if status and status.lower() not in {s.lower() for s in STATUSES}:
        raise BadBudget("status should be Active or Paused (or empty)")
    status = next((s for s in STATUSES if s.lower() == status.lower()), "")
    channel = channel_of(body.get("channel"))
    if channel is None:
        raise BadBudget("channel should be Google or Meta (or empty for Google), got %r" % str(body.get("channel"))[:40])
    return {"client": client, "account": account, "allocated": round(allocated, 2), "targetCpa": None if target is None else round(target, 2),
            "daysRunning": days_running(body.get("daysRunning")), "status": status, "am": _text(body.get("am"), "AM", 80), "channel": channel}


def check_rows(rows):
    """[row, ...] checked, with no client, account and channel twice. Raises BadBudget naming the row at fault."""
    if not isinstance(rows, list):
        raise BadBudget("rows should be a list")
    if len(rows) > MAX_ROWS:
        raise BadBudget("%d rows is more than the %d this accepts" % (len(rows), MAX_ROWS))
    out, seen = [], {}
    for i, r in enumerate(rows, 1):
        try:
            row = check_row(r)
        except BadBudget as e:
            who = (r.get("account") if isinstance(r, dict) else None) or "row %d" % i
            raise BadBudget("%s: %s" % (who, e)) from None
        k = row_key(row["client"], row["account"], row["channel"])
        if k in seen:
            raise BadBudget("%s / %s is listed twice%s" % (row["client"], row["account"], " for Meta" if row["channel"] == "Meta" else ""))
        seen[k] = True
        out.append(row)
    return out


# ── Rows pasted from the Budget Tracker ─────────────────────────────────────

def _norm_header(h):
    return re.sub(r"[^a-z]", "", str(h).lower())


_MONTHS = {m.lower(): i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
                                               "December"], 1)}


def cell_month(v):
    """'YYYY-MM' from a Date cell (2026-09-01, 9/1/2026, September 2026, Sep-2026, Sep 1, 2026), or None when it is not a date."""
    s = re.sub(r"\s+", " ", str(v or "").strip())
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-\d{1,2})?(?:[ T].*)?$", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return "%s-%02d" % (m.group(1), int(m.group(2)))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$", s)
    if m and 1 <= int(m.group(1)) <= 12:
        y = int(m.group(3))
        return "%04d-%02d" % (y + 2000 if y < 100 else y, int(m.group(1)))
    m = re.match(r"^([A-Za-z]{3,9})\.?(?:[ -]\d{1,2},?)?[ -](\d{4}|\d{2})$", s)
    if m and m.group(1).lower()[:3] in {k[:3] for k in _MONTHS}:
        mon = next(i for k, i in _MONTHS.items() if k[:3] == m.group(1).lower()[:3])
        y = int(m.group(2))
        return "%04d-%02d" % (y + 2000 if y < 100 else y, mon)
    return None


def parse_paste(text, month):
    """{rows, skipped, ignored}: the budget rows in `text` (cells copied from the Budget Tracker: tab separated, or comma separated) that
    belong to `month`. A header row is used if there is one, else the tracker's own column order is assumed. A row dated another month is
    counted in `ignored`; a row for another channel, or one that cannot be used, is listed in `skipped` with the reason. Raises BadBudget
    when nothing usable is there."""
    check_month(month)
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text.strip():
        raise BadBudget("nothing was pasted")
    if len(text) > 400_000:
        raise BadBudget("that is more text than a Budget Tracker holds; paste only the month you need")
    delim = "\t" if "\t" in text else ","
    try:
        table = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    except csv.Error:
        raise BadBudget("that could not be read as rows and columns: paste the cells copied from the Budget Tracker") from None
    if not table:
        raise BadBudget("nothing was pasted")
    mapped = [HEADERS.get(_norm_header(c)) for c in table[0]]
    if "client" in mapped and "account" in mapped:
        fields, body = mapped, table[1:]
    elif sum(1 for m in mapped if m) >= 2:
        raise BadBudget("the pasted header needs both a Client and an Account column")
    else:
        fields, body = DEFAULT_ORDER[:len(table[0])], table
    if "allocated" not in fields:
        raise BadBudget("no allocated budget column found: paste the tracker's header row with its cells")
    rows, skipped, ignored = [], [], 0
    for n, cells in enumerate(body, 1):
        rec = {f: (cells[i].strip() if i < len(cells) else "") for i, f in enumerate(fields) if f}
        if not rec.get("account") and not rec.get("client"):
            continue
        chan = rec.get("channel", "")
        if channel_of(chan) is None:
            skipped.append({"row": rec.get("account") or "row %d" % n, "reason": "%s budget, not Google Ads or Meta Ads" % chan})
            continue
        got = cell_month(rec.get("date"))
        if rec.get("date") and got and got != month:
            ignored += 1
            continue
        status = rec.get("status", "")
        try:
            row = check_row({"client": rec.get("client"), "account": rec.get("account"), "allocated": rec.get("allocated"),
                             "targetCpa": rec.get("targetCpa"), "daysRunning": rec.get("daysRunning"), "am": rec.get("am"), "channel": chan,
                             "status": status if status.lower() in {s.lower() for s in STATUSES} else ""})
        except BadBudget as e:
            skipped.append({"row": rec.get("account") or "row %d" % n, "reason": str(e)})
            continue
        rows.append(row)
    seen = {}
    for r in rows:                      # a pasted account twice: the later row is the one meant
        seen[row_key(r["client"], r["account"], r["channel"])] = r
    unique = list(seen.values())
    if len(unique) < len(rows):
        skipped.append({"row": "", "reason": "%d rows repeated an account already pasted; the later one was kept" % (len(rows) - len(unique))})
    if len(skipped) > MAX_SKIPPED:
        skipped = skipped[:MAX_SKIPPED] + [{"row": "", "reason": "and %d more rows that could not be used" % (len(skipped) - MAX_SKIPPED)}]
    if not unique:
        raise BadBudget("no budget rows for %s were found in what was pasted%s" % (
            month, " (%d were for other months)" % ignored if ignored else ""))
    return {"rows": unique, "skipped": skipped, "ignored": ignored}


# ── A month's book ──────────────────────────────────────────────────────────

def seed_from_run(clients):
    """Budget rows read out of a stored Pacing Desk run (its clients' accounts), for the first month the platform keeps its own."""
    rows = []
    for c in clients if isinstance(clients, list) else []:
        if not isinstance(c, dict):
            continue
        data = c.get("data") if isinstance(c.get("data"), dict) and "accounts" not in c else c
        accounts = data.get("accounts")
        for a in accounts if isinstance(accounts, list) else []:
            if not isinstance(a, dict):
                continue
            alloc = a.get("allocated")
            if not isinstance(alloc, (int, float)) or isinstance(alloc, bool) or alloc <= 0 or not a.get("name"):
                continue
            sched = str(a.get("schedule") or "")
            try:
                days = days_running(sched)
            except BadBudget:
                days = ""
            tgt = a.get("targetCpl") if isinstance(a.get("targetCpl"), (int, float)) else a.get("targetCpa")
            try:
                rows.append(check_row({"client": data.get("name"), "account": a["name"], "allocated": alloc, "channel": a.get("channel"),
                                       "targetCpa": tgt if isinstance(tgt, (int, float)) and not isinstance(tgt, bool) and tgt > 0 else None,
                                       "daysRunning": days}))
            except BadBudget:
                continue
    return rows


def dpr_rows(rows, month):
    """The rows as the Budget Tracker's DPR tab, header first, dated the first of `month`: the layout the engine's loader reads."""
    first = dt.datetime(int(month[:4]), int(month[5:]), 1)
    out = [list(DPR_HEADER)]
    for r in rows:
        out.append([r["client"], r["account"], r["allocated"], r.get("targetCpa"), first, r.get("status") or "Active",
                    r.get("daysRunning") or "All days", r.get("am") or "", r.get("channel") or "Google"])
    return out


# ── Which rows a month uses ─────────────────────────────────────────────────

FIELDS = ("client", "account", "allocated", "targetCpa", "daysRunning", "status", "am", "channel")


def book_for(month, *, store, run_fn=None):
    """{month, rows, source} for the month the engine is about to pace. The rows are, in order of preference:

      saved    the month's own rows (someone saved them);
      carried  the most recent earlier month's rows, because a month's budgets are usually the last month's until someone changes them;
      seeded   the accounts and allocations of the last Pacing Desk run the platform stored (run_fn() gives (date, clients)), for the
               first month the platform keeps its own;
      none     nothing yet.

    `source` says which, and (carried, seeded) where from, so the page can ask a person to confirm rows nobody has saved."""
    check_month(month)
    rows = store.list_month(month)
    if rows:
        return {"month": month, "rows": rows, "source": {"kind": "saved"}}
    earlier = [m for m in store.months() if m < month]
    if earlier:
        return {"month": month, "rows": [{k: r.get(k) for k in FIELDS} for r in store.list_month(earlier[-1])],
                "source": {"kind": "carried", "from": earlier[-1]}}
    date, clients = run_fn() if run_fn else (None, [])
    seeded = seed_from_run(clients)
    if seeded:
        return {"month": month, "rows": seeded, "source": {"kind": "seeded", "from": date}}
    return {"month": month, "rows": [], "source": {"kind": "none"}}


def version_of(saved_rows):
    """The text that says which saved version of a month a person is looking at: the newest save time among its saved rows ("" when
    nothing is saved). A save that names a different version is refused, so two people editing at once cannot overwrite each other."""
    return max([r.get("updatedAt") or "" for r in saved_rows or []] or [""])
