"""Pacing Desk: building today's run on the platform.

The engine (paid_marketing/pacing_engine) judges every account on what it has spent this month, what each campaign's daily budget is set
to, and what the account was allocated for the month. It no longer reads Google Sheets for any of that:

  spend, campaign budgets, shared pools   the Google Ads API, every account under the manager account (tracker/paid_pacing_read.py)
  the month's allocation per account      the platform's own table (tracker/paid_pacing_budgets.py): the month's saved rows, else the
                                          last month's carried forward, else the accounts of the last run it stored

A run reads every account (the read is saved, so changing an allocation re-runs the engine on that read instead of asking Google again),
writes the two workbooks the engine expects into a throwaway folder, runs the engine's own 12 self-checks
(validate.py) and then its exporter (export_dashboard.py) in a separate process, and stores the result exactly as a pushed run is stored.
A run the engine cannot finish is NOT published: the page keeps the previous one and says why.

The page starts a run by itself when none has been built since the Pacific day began (one read of the accounts per day, not one per
visit), and the Refresh button starts one on demand. Both are held to a cooldown, so a busy morning cannot spend Google's daily allowance.
The engine's folder holds client spend and is deleted when the run ends; the read itself is kept in the platform's own Google Ads cache
(pm_gads_cache), like every other Google Ads read.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from . import paid_gads as base
from . import paid_gads_segments as gads
from . import paid_pacing_budgets as budgets
from . import paid_pacing_read as reader

log = logging.getLogger(__name__)

ENGINE_DIR = Path(__file__).resolve().parent.parent / "paid_marketing" / "pacing_engine"
SPEND_FILE = "all_accounts_spend_data.xlsx"      # the names the engine's own run.py and loaders.py read
BUDGET_FILE = "budget_tracker.xlsx"
STALE_S = 10 * 60          # a run older than this lost its worker
ENGINE_TIMEOUT_S = 6 * 60
COOLDOWN_S = 10 * 60       # after a read of Google Ads that worked, nobody can start another for this long
READ_TTL_S = 60 * 60       # a saved read can be run again (with new allocations) for this long, within the Pacific day it was made
RETRY_S = 60               # after one that failed
AUTO_RETRY_S = 30 * 60     # the page does not start a run by itself again this soon after any run
PACIFIC = "America/Los_Angeles"
READ_KEY = base.CACHE_V + "pacing:read"


class RefreshError(RuntimeError):
    """A refresh could not run; the message is shown on the page."""


class RefreshBusy(RefreshError):
    """A refresh is already running."""


class RefreshTooSoon(RefreshError):
    def __init__(self, message, retry_in):
        super().__init__(message)
        self.retry_in = retry_in


def config_problem():
    """None when a refresh can run, else what is missing, in words."""
    return gads.config_problem()


def _now():
    return dt.datetime.now(dt.timezone.utc)


def pacific_day(when=None):
    from zoneinfo import ZoneInfo
    return (when or _now()).astimezone(ZoneInfo(PACIFIC)).date()


# ── Running the engine ──────────────────────────────────────────────────────

def write_workbook(path, tab, rows):
    """The rows (header first; dates as dates) as a one-tab workbook, the shape the engine's openpyxl loaders read."""
    import openpyxl
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(tab)
    for r in rows:
        ws.append(list(r))
    wb.save(path)


def _run(script, args, cwd, channel="Google"):
    env = dict(os.environ, PACING_CHANNEL=channel)
    p = subprocess.run([sys.executable, script, *args], cwd=cwd, capture_output=True,
                       text=True, timeout=ENGINE_TIMEOUT_S, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_engine(spend_rows, budget_rows, as_of, channel="Google"):
    """(dashboard.json, self-check flags) for as_of, built by the engine in a throwaway folder. `channel` (Google or Meta) says which of the allocation rows
    the spend is matched against. Raises RefreshError when the engine itself
    stops.

    A failed self-check does not stop the run. On the team's real September data the engine's own validate.py flags policy-floor and
    shared-pool items on most days and the team publishes regardless, so gating on it would mean Refresh never publishes. The flags
    travel with the run and the page shows them."""
    tmp = tempfile.mkdtemp(prefix="pacing-")
    try:
        work = Path(tmp)
        for f in ENGINE_DIR.glob("*.py"):
            shutil.copy(f, work / f.name)
        (work / "raw").mkdir()
        (work / "out").mkdir()      # export_dashboard.py writes here and does not create it
        write_workbook(work / "raw" / SPEND_FILE, "Data", spend_rows)
        write_workbook(work / "raw" / BUDGET_FILE, "DPR", budget_rows)
        day = as_of.isoformat()
        code, out = _run("validate.py", [day], tmp, channel)
        issues = [ln.strip().lstrip("✗").strip() for ln in out.splitlines() if "✗" in ln]
        if code != 0 and not issues:
            raise RefreshError("The engine stopped: %s" % _tail(out))
        code, out = _run("export_dashboard.py", [day], tmp, channel)
        dash = work / "out" / "dashboard.json"
        if code != 0 or not dash.exists():
            raise RefreshError("The engine stopped: %s" % _tail(out))
        return json.loads(dash.read_text(encoding="utf-8")), issues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tail(out):
    lines = [ln for ln in (out or "").strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else "no output")[:300]


# ── The job ─────────────────────────────────────────────────────────────────

def latest_run(store):
    """(date, clients) of the last run the platform stored, for seeding the first month's budgets."""
    date = store.latest_date()
    snap = store.get_snapshot(date) if date else None
    return date, (snap or {}).get("clients") or []


def _source_note(book, month):
    kind, frm = book["source"]["kind"], book["source"].get("from")
    if kind == "carried":
        return "The %s allocations are carried over from %s. Open Allocations to change them and save." % (_month_name(month), _month_name(frm))
    if kind == "seeded":
        return "The %s allocations were taken from the run of %s. Open Allocations to confirm them and save." % (_month_name(month), frm)
    return ""


def _month_name(m):
    return dt.date(int(m[:4]), int(m[5:7]), 1).strftime("%B %Y")


def _read_google(store, step, accounts_fn, search_fn, now):
    """The month read from Google Ads: {asOf, accounts, rows, unread, notes, readAt}. Raises RefreshError."""
    step("Listing the Google Ads accounts")
    try:
        accounts = (accounts_fn or gads.list_accounts)()
        if not accounts:
            raise RefreshError("The manager account has no enabled client accounts to read.")
        as_of, start, ends = reader.plan_window(accounts, now=now)
        step("Reading %d Google Ads accounts" % len(accounts))
        got = reader.read_all(accounts, start.isoformat(), ends, search_fn=search_fn)
    except gads.GadsError as e:
        raise RefreshError("Google Ads would not answer: %s" % e) from e
    if reader.too_many_unread(accounts, got["unread"]):
        raise RefreshError("Google Ads would not return %d of %d accounts (for example %s: %s), so the run would be missing too much. "
                           "Nothing was published." % (len(got["unread"]), len(accounts), got["unread"][0]["name"], got["unread"][0]["reason"]))
    if not got["rows"]:
        raise RefreshError("No campaign rows came back for %s from any account." % _month_name(budgets.month_of(as_of)))
    return {"asOf": as_of.isoformat(), "accounts": len(accounts), "rows": got["rows"], "unread": got["unread"], "notes": got["notes"],
            "readAt": (now or _now()).isoformat()}


def _usable_read(store, now=None):
    """The saved read, if there is one a run can be made from again: made within READ_TTL_S, and on this Pacific day (the day it is judged
    against would otherwise be yesterday's)."""
    try:
        hit = store.get_gads_entry(READ_KEY, READ_TTL_S)
    except Exception:
        log.exception("pacing refresh: could not look for a saved read")
        return None
    if hit is None:
        return None
    read, saved = hit
    now = now or _now()
    try:
        made = dt.datetime.fromisoformat(read["readAt"])
        if made.tzinfo is None:
            made = made.replace(tzinfo=dt.timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    return read if pacific_day(made) == pacific_day(now) else None


def refresh(store, pacing, step=lambda s: None, *, budget_store=None, accounts_fn=None, search_fn=None, now=None, reuse=False):
    """Read, run, check, publish. Returns the stored run's summary. With `reuse` the engine runs on the saved read (see _usable_read)
    and Google Ads is not asked again; without a usable one it reads as usual."""
    problem = config_problem()
    if problem:
        raise RefreshError(problem)
    if budget_store is None:
        from . import paid_pacing_budget_store as budget_store
    read = _usable_read(store, now) if reuse else None
    if read is None:
        read = _read_google(store, step, accounts_fn, search_fn, now)
        try:
            store.put_gads_cache(READ_KEY, read)
        except Exception:
            log.exception("pacing refresh: could not save the read")       # the run still goes ahead; only the cooldown and a rerun are lost
        again = False
    else:
        step("Using the Google Ads read of %s" % read["readAt"][11:16])
        again = True
    as_of = dt.date.fromisoformat(read["asOf"])
    month = budgets.month_of(as_of)
    book = budgets.book_for(month, store=budget_store, run_fn=lambda: latest_run(store))
    if not book["rows"]:
        raise RefreshError("There are no %s allocations yet. Open Allocations, add each account's budget for the month (or paste it from the "
                           "Budget Tracker) and run the refresh again." % _month_name(month))
    step("Running the engine for %s" % as_of.strftime("%b %-d"))
    body, issues = run_engine(reader.export_rows(read["rows"]), budgets.dpr_rows(book["rows"], month), as_of)
    notes = list(filter(None, [_source_note(book, month)]))
    warnings = ["Google Ads would not return %s (%s). Its pacing is missing from this run." % (u["name"], u["reason"]) for u in read["unread"]]
    body["source"] = {"kind": "googleAds", "accounts": read["accounts"], "unread": len(read["unread"]), "readAt": read["readAt"],
                      "budgets": dict(book["source"], month=month), "notes": warnings + read["notes"]}
    step("Publishing")
    meta, clients, latest = pacing.build_seed(body)
    store.save_snapshot(meta, clients, latest)
    return {"date": meta["asOf"], "dataThrough": meta.get("dataThrough"), "clients": meta["clientCount"], "accounts": meta["accountCount"],
            "notes": notes + warnings[:5] + read["notes"][:5], "issues": issues[:40], "issueCount": len(issues),
            "unread": len(read["unread"]), "budgets": book["source"]["kind"], "reusedRead": again, "readAt": read["readAt"]}


def _age_s(iso, now=None):
    try:
        t = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return ((now or _now()) - t).total_seconds()


def cooldown_left(state, read_saved_at=None, now=None):
    """Seconds before Google Ads may be read again (0 when it may be now): COOLDOWN_S after the last read that was saved, RETRY_S after a
    run that failed. Running the engine again on a saved read never waits (see start)."""
    left = 0
    age = _age_s(read_saved_at, now)
    if age is not None:
        left = max(left, int(COOLDOWN_S - age + 0.999))
    failed = _age_s((state or {}).get("finishedAt"), now) if (state or {}).get("state") == "failed" else None
    if failed is not None:
        left = max(left, int(RETRY_S - failed + 0.999))
    return max(0, left)


def _wait_text(seconds):
    minutes = (seconds + 59) // 60
    return "%d seconds" % seconds if seconds < 60 else "%d minute%s" % (minutes, "" if minutes == 1 else "s")


def is_due(state, last_built, now=None):
    """True when the page should start a run by itself: nothing has been built since the Pacific day began and no run (good or bad) was
    made in the last AUTO_RETRY_S, so a failing run is not repeated on every visit. `last_built` is when the latest stored run was made."""
    now = now or _now()
    if (state or {}).get("state") == "running":
        return False
    since = _age_s((state or {}).get("finishedAt"), now)
    if since is not None and since < AUTO_RETRY_S:
        return False
    if not last_built:
        return True
    try:
        built = dt.datetime.fromisoformat(last_built)
    except ValueError:
        return True
    if built.tzinfo is None:
        built = built.replace(tzinfo=dt.timezone.utc)
    return pacific_day(built) < pacific_day(now)


def start(store, pacing, by, *, auto=False, recompute=False, budget_store=None, last_built=None):
    """Claim the refresh and run it on a thread. Raises RefreshBusy when one is running and RefreshTooSoon inside the cooldown. With `auto`
    (the page asking, not a person) it also refuses unless a run is due. With `recompute` (allocations changed) a saved read from this
    Pacific day is run again with no cooldown and no call to Google; without one it is a normal refresh."""
    state = store.get_refresh() or {}
    if auto and not is_due(state, last_built):
        raise RefreshTooSoon("A run was already built today.", 0)
    reuse = bool(recompute and _usable_read(store) is not None)
    if not reuse:
        left = cooldown_left(state, _saved_at(store))
        if left:
            raise RefreshTooSoon("Google Ads was just read. Try again in %s." % _wait_text(left), left)
    if not store.claim_refresh(by, STALE_S):
        raise RefreshBusy("A refresh is already running.")

    def step(s):
        try:
            store.update_refresh(step=s)
        except Exception:
            log.exception("pacing refresh: could not record a step")

    def work():
        try:
            result = refresh(store, pacing, step, budget_store=budget_store, reuse=reuse)
            store.update_refresh(state="done", result=result, error=None,
                                 finishedAt=_now().isoformat())
            log.info("pacing refresh: published %s (%d clients)", result["date"], result["clients"])
        except Exception as e:
            known = isinstance(e, (RefreshError, pacing.BadPayload))
            msg = str(e) if known else "The refresh failed unexpectedly (%s)." % type(e).__name__
            if not known:
                log.exception("pacing refresh failed")
            try:
                store.update_refresh(state="failed", error=msg[:600], finishedAt=_now().isoformat())
            except Exception:
                log.exception("pacing refresh: could not record the failure")

    threading.Thread(target=work, name="pacing-refresh", daemon=True).start()


def _saved_at(store):
    try:
        return store.gads_saved_at(READ_KEY)
    except Exception:
        log.exception("pacing refresh: could not tell when Google Ads was last read")
        return None


def status(store, last_built=None):
    """What the page shows: the current or last run, and whether it can run, may run now, and is due."""
    st = dict(store.get_refresh() or {})
    if st.get("state") == "running":
        age = _age_s(st.get("startedAt"))
        if age is not None and age >= STALE_S:
            st.update(state="failed", error="The last refresh stopped without finishing. Try again.")
    st["configProblem"] = config_problem()
    st["cooldownLeft"] = cooldown_left(st, _saved_at(store))
    st["due"] = st["configProblem"] is None and is_due(st, last_built)
    return st
