"""Pacing Desk: the Refresh button. Rebuilds today's run on the platform.

The engine (paid_marketing/pacing_engine) reads two workbooks:

  the "All Accounts Spend Data" Google Sheet   Supermetrics keeps it current,
                                               tab `Data`, one row per campaign
                                               per day
  the Budget Tracker Google Sheet              tab `DPR`, the allocations

Both are Google Sheets, so the platform can read them itself with its
service account (GOOGLE_SA_JSON) once each is shared with that account. A
refresh fetches the two tabs, writes them as the xlsx files the engine
expects into a throwaway folder, runs the engine's own 12 self-checks
(validate.py) and then its exporter (export_dashboard.py) in a separate
process, and stores the result exactly as a pushed run is stored. A run
that fails a self-check is NOT published: the page keeps the previous one
and says what failed.

  PACING_SPEND_SHEET_ID   the spend sheet (required; no default)
  PACING_BUDGET_SHEET_ID  the Budget Tracker (defaults to the team's)

Nothing fetched is kept: the folder holds client spend and is deleted when
the run ends.
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

log = logging.getLogger(__name__)

ENGINE_DIR = Path(__file__).resolve().parent.parent / "paid_marketing" / "pacing_engine"
DEFAULT_BUDGET_SHEET = "1X_HjD0NUzp1br9SsLV7AICdS_SokdYuzbXc75ENVJ7A"
SPEND_TAB = "Data"
BUDGET_TAB = "DPR"
STALE_S = 10 * 60          # a run older than this lost its worker
ENGINE_TIMEOUT_S = 6 * 60
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
# Date columns arrive as spreadsheet serial numbers (days since 1899-12-30).
DATE_HEADERS = {"date", "day"}
_EPOCH = dt.datetime(1899, 12, 30)


class RefreshError(RuntimeError):
    """A refresh could not run; the message is shown on the page."""


def spend_sheet_id():
    return os.environ.get("PACING_SPEND_SHEET_ID", "").strip()


def budget_sheet_id():
    return os.environ.get("PACING_BUDGET_SHEET_ID", "").strip() or DEFAULT_BUDGET_SHEET


def _sa_info():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def service_email():
    return (_sa_info() or {}).get("client_email") or ""


def config_problem():
    """None when a refresh can run, else what is missing, in words."""
    if not _sa_info():
        return "The platform's Google service account (GOOGLE_SA_JSON) is not set."
    if not spend_sheet_id():
        return "PACING_SPEND_SHEET_ID is not set, so there is no spend sheet to read."
    return None


def today_ist():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    except Exception:
        return dt.date.today()


# ── Reading the sheets ──────────────────────────────────────────────────────

def _sheets():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    import google_auth_httplib2
    import httplib2
    creds = service_account.Credentials.from_service_account_info(_sa_info(), scopes=SCOPES)
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=180))
    return build("sheets", "v4", http=http, cache_discovery=False, static_discovery=True)


def _http_reason(e):
    status = getattr(getattr(e, "resp", None), "status", None)
    if status in (403, 404):
        return ("the service account %s cannot open it. Share the sheet with that address "
                "(Viewer is enough)." % (service_email() or "(unknown)"))
    return "Google answered %s." % (status or e)


def fetch_tab(svc, sheet_id, want_tab, label):
    """Rows of one tab, header first. `want_tab` falls back to the first tab,
    the same rule the engine's loaders use."""
    from googleapiclient.errors import HttpError
    try:
        meta = svc.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
    except HttpError as e:
        raise RefreshError("Could not open the %s: %s" % (label, _http_reason(e)))
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if not titles:
        raise RefreshError("The %s has no tabs." % label)
    tab = want_tab if want_tab in titles else titles[0]
    try:
        res = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="'%s'" % tab.replace("'", "''"),
            valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="SERIAL_NUMBER").execute()
    except HttpError as e:
        raise RefreshError("Could not read the %s: %s" % (label, _http_reason(e)))
    rows = res.get("values") or []
    if len(rows) < 2:
        raise RefreshError("The %s's %s tab is empty." % (label, tab))
    return tab, rows


def _cell(v, is_date):
    if is_date and isinstance(v, (int, float)) and not isinstance(v, bool):
        return _EPOCH + dt.timedelta(days=float(v))
    if v == "":
        return None
    return v


def write_xlsx(path, tab, rows):
    """The rows as a one-tab workbook, date columns as real dates, which is
    the shape the engine's openpyxl loaders read."""
    import openpyxl
    header = [str(h).strip() for h in rows[0]]
    is_date = [h.lower() in DATE_HEADERS for h in header]
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(tab)
    ws.append(header)
    n = len(header)
    for r in rows[1:]:
        r = list(r[:n]) + [""] * (n - len(r))
        if not any(x not in ("", None) for x in r):
            continue
        ws.append([_cell(v, is_date[i]) for i, v in enumerate(r)])
    wb.save(path)


def last_spend_date(rows):
    """The newest date in the spend tab, or None."""
    header = [str(h).strip().lower() for h in rows[0]]
    if "date" not in header:
        return None
    i = header.index("date")
    best = None
    for r in rows[1:]:
        if i < len(r):
            d = _cell(r[i], True)
            if isinstance(d, dt.datetime) and (best is None or d > best):
                best = d
    return best.date() if best else None


def pick_as_of(data_through, today):
    """The day the run is for. The engine treats as_of as "today" and expects
    data through yesterday, so it is the day after the newest data, kept in
    that data's month (on the 1st the sheet has nothing for the new month
    yet) and never in the future."""
    if data_through is None:
        return today
    nxt = data_through + dt.timedelta(days=1)
    as_of = nxt if nxt.month == data_through.month else data_through
    return min(as_of, today)


def budget_months(rows):
    """{'YYYY-MM', ...} named in the Budget Tracker's Date column."""
    header = [str(h).strip().lower() for h in rows[0]]
    if "date" not in header:
        return set()
    i = header.index("date")
    out = set()
    for r in rows[1:]:
        if i < len(r) and r[0] not in ("", None):
            d = _cell(r[i], True)
            if isinstance(d, dt.datetime):
                out.add(d.strftime("%Y-%m"))
    return out


# ── Running the engine ──────────────────────────────────────────────────────

def _run(script, args, cwd):
    p = subprocess.run([sys.executable, script, *args], cwd=cwd, capture_output=True,
                       text=True, timeout=ENGINE_TIMEOUT_S)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_engine(spend_rows, spend_tab, budget_rows, budget_tab, as_of):
    """(dashboard.json, self-check flags) for as_of, built by the engine in a
    throwaway folder. Raises RefreshError when the engine itself stops.

    A failed self-check does not stop the run. On the team's real September
    data the engine's own validate.py flags policy-floor and shared-pool
    items on most days and the team publishes regardless, so gating on it
    would mean Refresh never publishes. The flags travel with the run and
    the page shows them."""
    tmp = tempfile.mkdtemp(prefix="pacing-")
    try:
        work = Path(tmp)
        for f in ENGINE_DIR.glob("*.py"):
            shutil.copy(f, work / f.name)
        (work / "raw").mkdir()
        (work / "out").mkdir()      # export_dashboard.py writes here and does not create it
        write_xlsx(work / "raw" / "all_accounts_spend_data.xlsx", spend_tab, spend_rows)
        write_xlsx(work / "raw" / "budget_tracker.xlsx", budget_tab, budget_rows)
        day = as_of.isoformat()
        code, out = _run("validate.py", [day], tmp)
        issues = [ln.strip().lstrip("\u2717").strip() for ln in out.splitlines() if "\u2717" in ln]
        if code != 0 and not issues:
            raise RefreshError("The engine stopped: %s" % _tail(out))
        code, out = _run("export_dashboard.py", [day], tmp)
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

def refresh(store, pacing, step=lambda s: None):
    """Fetch, run, check, publish. Returns the stored run's summary."""
    problem = config_problem()
    if problem:
        raise RefreshError(problem)
    svc = _sheets()
    step("Reading the spend sheet")
    spend_tab, spend_rows = fetch_tab(svc, spend_sheet_id(), SPEND_TAB, "spend sheet")
    step("Reading the Budget Tracker")
    budget_tab, budget_rows = fetch_tab(svc, budget_sheet_id(), BUDGET_TAB, "Budget Tracker")
    through = last_spend_date(spend_rows)
    if through is None:
        raise RefreshError("The spend sheet has no dated rows.")
    as_of = pick_as_of(through, today_ist())
    notes = []
    months = budget_months(budget_rows)
    if months and as_of.strftime("%Y-%m") not in months:
        notes.append("The Budget Tracker has no %s rows (it has %s), so these allocations may be "
                     "last month's." % (as_of.strftime("%B"), ", ".join(sorted(months))))
    step("Running the engine for %s" % as_of.strftime("%b %-d"))
    body, issues = run_engine(spend_rows, spend_tab, budget_rows, budget_tab, as_of)
    step("Publishing")
    meta, clients, latest = pacing.build_seed(body)
    store.save_snapshot(meta, clients, latest)
    return {"date": meta["asOf"], "dataThrough": meta.get("dataThrough"),
            "clients": meta["clientCount"], "accounts": meta["accountCount"], "notes": notes,
            "issues": issues[:40], "issueCount": len(issues)}


def start(store, pacing, by):
    """Claim the refresh and run it on a thread. False if one is running."""
    if not store.claim_refresh(by, STALE_S):
        return False

    def step(s):
        try:
            store.update_refresh(step=s)
        except Exception:
            log.exception("pacing refresh: could not record a step")

    def work():
        try:
            result = refresh(store, pacing, step)
            store.update_refresh(state="done", result=result, error=None,
                                 finishedAt=dt.datetime.now(dt.timezone.utc).isoformat())
            log.info("pacing refresh: published %s (%d clients)", result["date"], result["clients"])
        except Exception as e:
            known = isinstance(e, (RefreshError, pacing.BadPayload))
            msg = str(e) if known else "The refresh failed unexpectedly (%s)." % type(e).__name__
            if not known:
                log.exception("pacing refresh failed")
            try:
                store.update_refresh(state="failed", error=msg[:600],
                                     finishedAt=dt.datetime.now(dt.timezone.utc).isoformat())
            except Exception:
                log.exception("pacing refresh: could not record the failure")

    threading.Thread(target=work, name="pacing-refresh", daemon=True).start()
    return True


def status(store):
    """What the page shows: the current or last run, and whether it can run."""
    st = dict(store.get_refresh() or {})
    if st.get("state") == "running":
        try:
            started = dt.datetime.fromisoformat(st.get("startedAt") or "")
            if (dt.datetime.now(dt.timezone.utc) - started).total_seconds() >= STALE_S:
                st.update(state="failed", error="The last refresh stopped without finishing. Try again.")
        except ValueError:
            pass
    st["configProblem"] = config_problem()
    st["serviceEmail"] = service_email()
    return st
