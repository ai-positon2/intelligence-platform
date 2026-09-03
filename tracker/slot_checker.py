"""Slot Checker — appointment availability across the dental practice portfolio.

Reads the "Slot Checker" Google Sheet (refreshed weekly by the scraping agent)
and turns it into everything the dashboard renders. The derivation itself
(build_dashboard and everything below it) is a pure function over a plain
dict, unchanged from when that dict came from a committed file -- only where
the dict comes from changed.

Live sheet, with a committed-file fallback
───────────────────────────────────────────
`_rows_from_live_sheet()` reads two tabs -- "LPs" (the location registry) and
"Locations" (one row per office + service, with one column per date) -- via
the platform's service account (signal-tracker@signal-tracker-496308.iam.gserviceaccount.com,
shared as Viewer on the sheet). Their rows are handed to
scripts.import_slot_checker_snapshot.build_snapshot(), the same parser a
prior .xlsx-based snapshot used, since "LPs" lines up with that parser's
expected "All LPs" shape unchanged and "Locations" is reordered in
_reorder_locations_rows() to match its expected "Available Slots Final" shape.

If the live read or parse fails for any reason (revoked share, renamed tab,
network error, quota), `_snapshot()` falls back to the last snapshot committed
to data/slot_checker_snapshot.json -- same pattern as Job Change Alert's
JOB_CHANGE_TRACKED_SNAPSHOT_PATH fallback. This only ever serves *stale* data
on a live-read failure, never wrong data: a live read that succeeds but
legitimately returns zero rows is not treated as a failure and does not fall
back, since the sheet being genuinely emptied out is a fact to show, not an
error to paper over.

The one modelling decision worth knowing about
──────────────────────────────────────────────
A row in the source sheet is an OBSERVATION, not a fact. The sheet holds
several runs of the agent, and a practice re-scraped on consecutive days
appears more than once with different counts, because real availability moved
in between. So "current availability" is the NEWEST observation per (practice,
service), and summing the sheet as-is overstates the total -- it double- and
triple-counts exactly the practices the agent happened to revisit.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "slot_checker_snapshot.json"

SLOT_CHECKER_SHEET_ID = "10vENVV_SIqY4NsqHJJ67QFtjhi2FLAdi_Pp2pvXYr84"
LP_TAB = "LPs"
LOCATIONS_TAB = "Locations"

CACHE_TTL = 300  # seconds, matching every other sheet-backed panel in this app
_CACHE: dict = {"data": None, "ts": 0.0}

# A practice with at least one open slot but fewer than this across the whole
# window is reported as thin rather than healthy: it is bookable in principle,
# but a patient phoning about any particular day will usually be told no.
THIN_SLOT_CEILING = 20

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ── source ────────────────────────────────────────────────────────────────────

def load_snapshot(path=None) -> dict:
    """The committed snapshot, or an empty shape on any failure.

    Never raises: a missing or corrupt snapshot has to render as an empty
    dashboard with an explanation, not a 500.
    """
    p = Path(path or SNAPSHOT_PATH)
    try:
        with open(p) as f:
            snap = json.load(f)
    except FileNotFoundError:
        log.warning("slot_checker: no snapshot at %s", p)
        return _empty_snapshot()
    except Exception as e:
        log.warning("slot_checker: snapshot at %s unreadable: %s", p, e)
        return _empty_snapshot()
    if not isinstance(snap, dict) or "locations" not in snap:
        log.warning("slot_checker: snapshot at %s has no locations key", p)
        return _empty_snapshot()
    snap.setdefault("dates", [])
    snap.setdefault("locations", [])
    return snap


def _empty_snapshot() -> dict:
    return {"generated_at": "", "source": {}, "dates": [], "locations": []}


def _sheets_service():
    """Authenticated read-only Sheets client, matching app.py's own
    _sheets_service() (GOOGLE_SA_JSON env var, Railway). Duplicated here
    rather than imported from app.py to keep this tracker importable on its
    own, same as tracker/sheets_client.py's _get_service()."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_str = os.environ.get("GOOGLE_SA_JSON", "")
    if not sa_str:
        raise RuntimeError("GOOGLE_SA_JSON env var not set")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_str),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _iso_stamp_from_sheet(v: str) -> str:
    """A Sheets execution-time cell ('8/11/2026 14:52:13') as an ISO string.

    The prior .xlsx-based pipeline got real datetime objects from openpyxl,
    so import_slot_checker_snapshot._iso_stamp's isoformat() round-trip just
    worked. The Sheets API returns the same cell as a plain display string,
    which _iso_stamp does not parse (it only handles already-ISO strings) --
    and observations are sorted by this value as a plain string, so an
    unparsed 'M/D/YYYY' would sort '8/9/2026' after '8/13/2026' and pick the
    wrong observation as "current". Normalising here, before the row ever
    reaches _parse_slots, keeps that parser untouched.
    """
    v = (v or "").strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(v, fmt).isoformat()
        except ValueError:
            continue
    return v


def _reorder_locations_rows(rows: list) -> list:
    """"Locations" tab rows, reshaped into the "Available Slots Final" shape
    scripts.import_slot_checker_snapshot._parse_slots already knows how to
    read.

    Source shape (both header and data rows): office, category, url, service,
    checked_at, *dates. Target shape: url, service, checked_at, *dates --
    office and category are dropped: office is redundant once joined by URL
    (the "LPs" side already carries it), and category isn't part of the
    existing snapshot/dashboard contract. The Sheets API trims each row to
    its own last non-blank cell, so short rows are padded defensively rather
    than assumed to be full width.
    """
    def cell(row, i):
        return row[i] if len(row) > i else ""

    return [
        [cell(row, 2), cell(row, 3), _iso_stamp_from_sheet(cell(row, 4))] + list(row[5:])
        for row in rows
    ]


def _rows_from_live_sheet() -> tuple:
    """Live read of the weekly-refreshed Slot Checker Google Sheet.

    Returns (lp_rows, slot_rows) in the exact shape
    scripts.import_slot_checker_snapshot.build_snapshot() expects. "LPs"
    columns A-E already line up with build_snapshot()'s "All LPs" shape --
    anything past column E is an unrelated scratch list the sheet's owner
    keeps in spare columns and is simply never read. "Locations" is reordered
    by _reorder_locations_rows() into the "Available Slots Final" shape.
    """
    svc = _sheets_service()
    lp_rows = svc.spreadsheets().values().get(
        spreadsheetId=SLOT_CHECKER_SHEET_ID, range=f"{LP_TAB}!A1:N500").execute().get("values", [])
    loc_rows = svc.spreadsheets().values().get(
        spreadsheetId=SLOT_CHECKER_SHEET_ID, range=f"{LOCATIONS_TAB}!A1:AF5000").execute().get("values", [])
    return lp_rows, _reorder_locations_rows(loc_rows)


def _snapshot() -> dict:
    """Live sheet if reachable and parseable, else the last committed
    fallback snapshot. Never raises: a revoked share, a renamed tab, or a
    network error has to render a (possibly stale) dashboard, not a 500."""
    try:
        lp_rows, slot_rows = _rows_from_live_sheet()
        from scripts.import_slot_checker_snapshot import build_snapshot
        return build_snapshot(lp_rows, slot_rows, source=f"sheet:{SLOT_CHECKER_SHEET_ID}")
    except Exception as e:
        log.warning("slot_checker: live sheet read failed, falling back to committed snapshot: %s", e)
        return load_snapshot()


def fetch(force: bool = False) -> dict:
    """The dashboard payload, TTL-cached."""
    now = time.time()
    if not force and _CACHE["data"] is not None and (now - _CACHE["ts"]) < CACHE_TTL:
        return _CACHE["data"]
    data = build_dashboard(_snapshot())
    _CACHE["data"] = data
    _CACHE["ts"] = now
    return data


def reset_cache() -> None:
    """Drop the TTL cache. Tests need this; nothing in the app calls it."""
    _CACHE["data"] = None
    _CACHE["ts"] = 0.0


# ── derivation ────────────────────────────────────────────────────────────────

def _latest(service: dict) -> list:
    """Newest observation's counts for one service, or [] if there are none."""
    obs = service.get("observations") or []
    if not obs:
        return []
    return list(obs[-1].get("counts") or [])


def _pad(counts: list, n: int) -> list:
    """Counts aligned to the date axis, so a short row cannot skew a column."""
    out = [int(c or 0) for c in counts[:n]]
    return out + [0] * (n - len(out))


def build_dashboard(snap: dict) -> dict:
    dates = list(snap.get("dates") or [])
    n = len(dates)
    raw_locations = list(snap.get("locations") or [])

    practices = [_practice(loc, n) for loc in raw_locations]
    practices.sort(key=lambda p: (-p["total"], p["name"]))

    return {
        "generated_at": snap.get("generated_at", ""),
        "source": snap.get("source", {}),
        "dates": [{"date": d, "weekday": _weekday(d), "label": _daylabel(d)} for d in dates],
        "practices": practices,
        "totals": _totals(practices, dates),
        "by_state": _by_state(practices),
        "by_service": _by_service(practices, n),
        "by_date": _by_date(practices, dates),
        "by_weekday": _by_weekday(practices, dates),
        "by_brand": _by_brand(practices),
        "alerts": _alerts(practices),
        "freshness": _freshness(practices),
    }


def _practice(loc: dict, n: int) -> dict:
    services = []
    per_date = [0] * n
    runs = 0
    for sv in loc.get("services") or []:
        counts = _pad(_latest(sv), n)
        obs = sv.get("observations") or []
        runs = max(runs, len(obs))
        total = sum(counts)
        services.append({
            "name": sv.get("name", ""),
            "counts": counts,
            "total": total,
            "days_open": sum(1 for c in counts if c > 0),
            "runs": len(obs),
            "bookable": total > 0,
        })
        for i, c in enumerate(counts):
            per_date[i] += c
    services.sort(key=lambda s: (-s["total"], s["name"]))

    total = sum(per_date)
    days_open = sum(1 for c in per_date if c > 0)
    first_open = next((i for i, c in enumerate(per_date) if c > 0), None)

    return {
        "office": loc.get("office", ""),
        "name": loc.get("name", ""),
        "account": loc.get("account", ""),
        "brand": loc.get("brand", ""),
        "state": loc.get("state", ""),
        "city": loc.get("city", ""),
        "url": loc.get("url", ""),
        "system": loc.get("system", ""),
        "booking": loc.get("booking", ""),
        "checked_at": loc.get("checked_at", ""),
        "services": services,
        "service_count": len(services),
        "zero_services": sum(1 for s in services if not s["bookable"]),
        "counts": per_date,
        "total": total,
        "days_open": days_open,
        "peak": max(per_date) if per_date else 0,
        "first_open_index": first_open,
        "lead_days": first_open,
        "runs": runs,
        "status": _status(loc, services, total),
    }


def _status(loc: dict, services: list, total: int) -> str:
    """One of: no-data, none, thin, open.

    'no-data' and 'none' are deliberately separate. A practice the agent never
    returned a row for is a gap in the crawl; a practice it checked and found
    fully booked is a gap in capacity. Collapsing them would hide a broken
    crawl behind a plausible-looking business finding.
    """
    if not services:
        return "no-data"
    if total <= 0:
        return "none"
    if total < THIN_SLOT_CEILING:
        return "thin"
    return "open"


def _weekday(iso: str) -> str:
    try:
        return datetime.date.fromisoformat(iso).strftime("%a")
    except (ValueError, TypeError):
        return ""


def _daylabel(iso: str) -> str:
    try:
        d = datetime.date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso or ""
    return d.strftime("%-d %b") if hasattr(d, "strftime") else iso


def _totals(practices: list, dates: list) -> dict:
    with_data = [p for p in practices if p["status"] != "no-data"]
    slots = sum(p["total"] for p in practices)
    pairs = sum(p["service_count"] for p in practices)
    return {
        "slots": slots,
        "practices": len(practices),
        "practices_with_data": len(with_data),
        "practices_no_data": len(practices) - len(with_data),
        "practices_zero": sum(1 for p in practices if p["status"] == "none"),
        "practices_thin": sum(1 for p in practices if p["status"] == "thin"),
        "states": len({p["state"] for p in practices if p["state"]}),
        "services": len({s["name"] for p in practices for s in p["services"]}),
        "service_pairs": pairs,
        "service_pairs_zero": sum(p["zero_services"] for p in practices),
        "window_days": len(dates),
        "window": [dates[0], dates[-1]] if dates else [],
        "avg_per_practice": round(slots / len(with_data), 1) if with_data else 0,
        # Only a day that actually has slots can be the busiest. Naming the
        # first day of an all-empty window "busiest" is worse than saying
        # nothing, because it reads as a real finding.
        "busiest_day": _busiest(practices, dates),
    }


def _busiest(practices: list, dates: list) -> str:
    best, best_n = "", 0
    for i, d in enumerate(dates):
        n = sum(p["counts"][i] for p in practices)
        if n > best_n:
            best, best_n = d, n
    return best


def _by_state(practices: list) -> list:
    agg = defaultdict(lambda: {"practices": 0, "slots": 0, "zero": 0})
    for p in practices:
        st = p["state"] or "—"
        agg[st]["practices"] += 1
        agg[st]["slots"] += p["total"]
        if p["status"] in ("none", "no-data"):
            agg[st]["zero"] += 1
    out = [{"state": k, **v, "avg": round(v["slots"] / v["practices"], 1) if v["practices"] else 0}
           for k, v in agg.items()]
    out.sort(key=lambda r: (-r["slots"], r["state"]))
    return out


def _by_service(practices: list, n: int) -> list:
    agg = defaultdict(lambda: {"slots": 0, "practices": 0, "zero": 0, "counts": [0] * n})
    for p in practices:
        for s in p["services"]:
            a = agg[s["name"]]
            a["slots"] += s["total"]
            a["practices"] += 1
            if not s["bookable"]:
                a["zero"] += 1
            for i, c in enumerate(s["counts"]):
                a["counts"][i] += c
    out = [{"name": k, **v} for k, v in agg.items()]
    out.sort(key=lambda r: (-r["slots"], r["name"]))
    return out


def _by_date(practices: list, dates: list) -> list:
    out = []
    for i, d in enumerate(dates):
        slots = sum(p["counts"][i] for p in practices)
        out.append({
            "date": d,
            "weekday": _weekday(d),
            "label": _daylabel(d),
            "slots": slots,
            "practices_open": sum(1 for p in practices if p["counts"][i] > 0),
            "weekend": _weekday(d) in ("Sat", "Sun"),
        })
    return out


def _by_weekday(practices: list, dates: list) -> list:
    slots = Counter()
    days = Counter()
    for i, d in enumerate(dates):
        wd = _weekday(d)
        if not wd:
            continue
        days[wd] += 1
        slots[wd] += sum(p["counts"][i] for p in practices)
    return [{"day": wd, "slots": slots.get(wd, 0), "dates": days.get(wd, 0),
             "avg": round(slots[wd] / days[wd], 1) if days.get(wd) else 0}
            for wd in _WEEKDAYS if days.get(wd)]


def _by_brand(practices: list) -> list:
    agg = defaultdict(lambda: {"practices": 0, "slots": 0})
    for p in practices:
        key = p["brand"] or p["account"] or "—"
        agg[key]["practices"] += 1
        agg[key]["slots"] += p["total"]
    out = [{"brand": k, **v} for k, v in agg.items()]
    out.sort(key=lambda r: (-r["slots"], r["brand"]))
    return out


def _alerts(practices: list) -> dict:
    def brief(p):
        return {"name": p["name"], "state": p["state"], "office": p["office"],
                "total": p["total"], "url": p["url"], "status": p["status"],
                "checked_at": p["checked_at"]}

    unbookable = []
    for p in practices:
        for s in p["services"]:
            if not s["bookable"]:
                unbookable.append({"name": p["name"], "state": p["state"],
                                   "service": s["name"], "office": p["office"]})
    unbookable.sort(key=lambda r: (r["state"], r["name"], r["service"]))
    return {
        "no_data": [brief(p) for p in practices if p["status"] == "no-data"],
        "zero": [brief(p) for p in practices if p["status"] == "none"],
        "thin": sorted((brief(p) for p in practices if p["status"] == "thin"),
                       key=lambda r: r["total"]),
        "unbookable_services": unbookable,
    }


def _freshness(practices: list) -> dict:
    stamps = sorted(p["checked_at"] for p in practices if p["checked_at"])
    return {
        "oldest": stamps[0] if stamps else "",
        "newest": stamps[-1] if stamps else "",
        "runs": dict(sorted(Counter(p["runs"] for p in practices if p["runs"]).items())),
        "rechecked": sum(1 for p in practices if p["runs"] > 1),
    }
