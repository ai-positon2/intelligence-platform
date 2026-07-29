"""
seed_northstar_signals.py
==========================
Loads manually-researched signals for the NorthStar Anesthesia ABM Signal
Tracker into data/tracker_northstar.db, then rebuilds the dashboard.

This is the interim signal pipeline while NorthStar's permanent signal source
(Sheets or otherwise) is being wired up: real web research is gathered a
batch of companies at a time and appended to data/northstar_signals_manual.json,
then this script loads it. Re-running is safe -- it skips any (company,
signal_type, signal_detail) combination already in the database, so appending
a new batch to the JSON file and re-running only inserts what's new.

The JSON is the source of truth, so curation (removing a weak signal, rewording
one, or changing its severity) happens there and is applied with --prune, which
deletes any alerts_sent row that no longer matches a JSON entry. Without --prune
the script is insert-only and a removed or reworded signal would linger in the
database and keep counting toward the KPI tiles.

Usage:
    python seed_northstar_signals.py             # insert new signals, then rebuild
    python seed_northstar_signals.py --prune     # also delete DB rows no longer in the JSON
    python seed_northstar_signals.py --no-build  # load signals only
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from tracker.snapshot_store import SnapshotStore

DB_PATH = ROOT / "data" / "tracker_northstar.db"
SIGNALS_JSON = ROOT / "data" / "northstar_signals_manual.json"


def _already_recorded(apollo_id: str, signal_type: str, signal_detail: str) -> bool:
    """True if this exact signal is already in alerts_sent (idempotent re-runs)."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        row = con.execute(
            "SELECT 1 FROM alerts_sent WHERE apollo_id=? AND signal_type=? AND signal_detail=? LIMIT 1",
            (apollo_id, signal_type, signal_detail),
        ).fetchone()
        return row is not None
    finally:
        con.close()


def _reconcile(desired: dict) -> tuple[int, int]:
    """Delete alerts_sent rows absent from the JSON, and re-sync changed fields.

    `desired` maps (apollo_id, signal_type, signal_detail) -> the JSON entry.
    The dedup key deliberately excludes severity, so a severity-only edit in the
    JSON is invisible to the insert path -- it is applied here instead.

    Returns (deleted, updated).
    """
    con = sqlite3.connect(str(DB_PATH))
    deleted = updated = 0
    try:
        rows = con.execute(
            "SELECT id, apollo_id, signal_type, signal_detail, severity, signal_date, source_url FROM alerts_sent"
        ).fetchall()
        for rid, apollo_id, stype, detail, sev, sdate, surl in rows:
            entry = desired.get((apollo_id, stype, detail))
            if entry is None:
                con.execute("DELETE FROM alerts_sent WHERE id=?", (rid,))
                print("  - dropped: %s | %s" % (stype, detail[:78]))
                deleted += 1
                continue
            want = (entry["severity"], entry["signal_date"], entry.get("source_url", ""))
            if (sev, sdate, surl) != want:
                con.execute(
                    "UPDATE alerts_sent SET severity=?, signal_date=?, source_url=? WHERE id=?",
                    (*want, rid),
                )
                if sev != entry["severity"]:
                    print("  ~ severity %s -> %s: %s" % (sev, entry["severity"], detail[:62]))
                updated += 1
        con.commit()
    finally:
        con.close()
    return deleted, updated


def main() -> None:
    if not SIGNALS_JSON.exists():
        print("ERROR: %s not found." % SIGNALS_JSON)
        sys.exit(1)

    data = json.loads(SIGNALS_JSON.read_text(encoding="utf-8"))
    signals = data.get("signals", [])
    if not signals:
        print("No signals in %s -- nothing to do." % SIGNALS_JSON.name)
        return

    store = SnapshotStore(DB_PATH)
    name_to_id = {c["name"]: c["apollo_id"] for c in store.get_all_companies()}

    if "--prune" in sys.argv:
        desired = {}
        for sig in signals:
            apollo_id = name_to_id.get(sig["company_name"])
            if apollo_id:
                desired[(apollo_id, sig["signal_type"], sig["signal_detail"])] = sig
        print("Reconciling %s against the JSON (%d entries)..." % (DB_PATH.name, len(desired)))
        deleted, updated = _reconcile(desired)
        print("  Deleted (no longer in JSON): %d" % deleted)
        print("  Re-synced (field changed):   %d" % updated)
        print()

    inserted, skipped_dupe, skipped_unmatched = 0, 0, 0
    unmatched_names = set()

    for sig in signals:
        company_name = sig["company_name"]
        apollo_id = name_to_id.get(company_name)
        if not apollo_id:
            skipped_unmatched += 1
            unmatched_names.add(company_name)
            continue

        if _already_recorded(apollo_id, sig["signal_type"], sig["signal_detail"]):
            skipped_dupe += 1
            continue

        store.record_alert(
            apollo_id=apollo_id,
            signal_type=sig["signal_type"],
            signal_detail=sig["signal_detail"],
            severity=sig["severity"],
            dry_run=False,
            signal_date=sig["signal_date"],
            source_url=sig.get("source_url", ""),
        )
        inserted += 1

    print("Signals loaded from %s (batches %s):" % (SIGNALS_JSON.name, data.get("batches_loaded", data.get("batch", "?"))))
    print("  Inserted:            %d" % inserted)
    print("  Already in DB:       %d" % skipped_dupe)
    print("  Unmatched company:   %d" % skipped_unmatched)
    if unmatched_names:
        print("  -> names that didn't match the companies table exactly:")
        for n in sorted(unmatched_names):
            print("       %r" % n)

    if "--no-build" in sys.argv:
        return

    print("\nRebuilding dashboard...")
    import build_northstar_dashboard
    build_northstar_dashboard.build_northstar()


if __name__ == "__main__":
    main()
