"""Paid Marketing Agents: Liine Lead Analysis, the read.

Liine has no key we can use: its connector signs in as a person, and only Claude can use it. So Claude pulls the data (a
written recipe, docs/liine-lead-analysis-plan.md) and saves it as Google Sheets in ONE Drive folder that is shared with the
platform's service account. Each run is a set of files titled `Liine Data YYYY-MM-DD HHMM` (plus ` part N` when one file is
too big). This module finds the newest run in the folder, reads all its parts and hands the rows to paid_liine_math.

Nothing here writes to Drive or Sheets. A run is read as a whole or not at all: if a part cannot be read, the page says so
and shows the last run that was whole, it never shows half a run.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import paid_liine_math as m

log = logging.getLogger(__name__)

FOLDER_ID = "1XGlTlyxeunMH1ARJOnzQ7e6wU32y2yQv"
TITLE_RE = re.compile(r"^Liine Data (\d{4}-\d{2}-\d{2} \d{4})(?: part (\d+))?$")
COLUMNS = ["table", "practice_id", "practice", "period", "key", "leads", "booked", "spend", "clicks", "value", "note"]
CACHE_SECONDS = 600
MAX_RUNS_TRIED = 3

_cache = {"at": 0.0, "value": None}
_lock = threading.Lock()


class LiineError(Exception):
    """Something the page can show the user as it is."""


def _services():
    """(drive, sheets) for the service account, read-only. Module level so a test can replace it."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = os.environ.get("GOOGLE_SA_JSON", "")
    if not raw:
        raise LiineError("GOOGLE_SA_JSON is not set on this server, so the Liine data cannot be read.")
    creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=[
        "https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/spreadsheets.readonly"])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False, static_discovery=True)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False, static_discovery=True)
    return drive, sheets


def runs(files):
    """Group the folder's files by run stamp: {stamp: [(part, id, title)]}, newest stamp first. Other files are ignored."""
    by = {}
    for f in files:
        mt = TITLE_RE.match((f.get("name") or "").strip())
        if not mt or f.get("mimeType") != "application/vnd.google-apps.spreadsheet":
            continue
        by.setdefault(mt.group(1), []).append((int(mt.group(2) or 1), f["id"], f["name"]))
    return [(s, sorted(by[s])) for s in sorted(by, reverse=True)]


def rows_of(values):
    """Sheet values (a list of lists) as dict rows, using the header row. Short rows are padded."""
    if not values:
        return []
    head = [str(h).strip() for h in values[0]]
    if "table" not in head:
        raise LiineError("A Liine file does not have the expected first row (table, practice_id, ...).")
    out = []
    for row in values[1:]:
        if not any(str(c).strip() for c in row):
            continue
        out.append({h: (str(row[i]) if i < len(row) else "") for i, h in enumerate(head)})
    return out


def _read_run(sheets, parts):
    rows = []
    for _part, fid, title in parts:
        try:
            got = sheets.spreadsheets().values().get(spreadsheetId=fid, range="A:K").execute()
        except Exception as e:  # noqa: BLE001
            raise LiineError("Could not read %s: %s" % (title, str(e)[:160]))
        rows.extend(rows_of(got.get("values", [])))
    return rows


def load(force=False, services=None):
    """The newest whole run as an analysis dict. Cached for 10 minutes. Raises LiineError with plain words."""
    now = time.time()
    with _lock:
        if not force and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
    drive, sheets = (services or _services)()
    try:
        listing = drive.files().list(
            q="'%s' in parents and trashed = false" % FOLDER_ID, orderBy="createdTime desc", pageSize=200,
            fields="files(id,name,mimeType,createdTime)").execute()
    except Exception as e:  # noqa: BLE001
        raise LiineError("Could not list the Liine folder. Is it still shared with the platform? (%s)" % str(e)[:160])
    found = runs(listing.get("files", []))
    if not found:
        raise LiineError("There is no Liine data in the folder yet. Ask Claude to pull the Liine data (see 'How the data gets here').")
    problems = []
    for stamp, parts in found[:MAX_RUNS_TRIED]:
        try:
            ds = m.parse(_read_run(sheets, parts))
        except LiineError as e:
            problems.append("%s: %s" % (stamp, e))
            continue
        miss = m.missing_tables(ds)
        if miss:
            problems.append("%s is missing %s" % (stamp, ", ".join(miss)))
            continue
        result = m.analyse(ds)
        result["run"] = {"stamp": stamp, "parts": len(parts), "newest": stamp == found[0][0], "skipped": problems}
        if problems:
            result["notes"].insert(0, {"level": "warn", "text": "The newest Liine run could not be used (%s). Showing the run of %s." % ("; ".join(problems), stamp)})
        with _lock:
            _cache["value"] = result
            _cache["at"] = now
        return result
    raise LiineError("None of the newest Liine runs could be used: %s" % "; ".join(problems))
