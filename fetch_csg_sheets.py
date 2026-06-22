"""
fetch_csg_sheets.py
===================
Fetches HIGH-confidence signals (C-Suite Join/Exit, IPO, Acquisition / M&A,
Funding Round, Subsidiary Change — and any Sheet-promoted Product Launch /
Partnership / Creative Hiring) for all CSG companies from user-maintained
Google Sheets, and writes them into data/tracker_csg_v2.db.

Mirrors fetch_csg_news.py / fetch_csg_jobs.py. Reuses main.py's sheet-row
detection so column layouts and routing stay identical to the Healthcare flow.

CSG sheet IDs are read from the `google_sheets_csg` block of config.yaml
(falls back to `google_sheets`). If no CSG sheet IDs are configured, this is a
safe no-op (fetches nothing, writes nothing).

Usage:
    python fetch_csg_sheets.py                 # all CSG companies
    python fetch_csg_sheets.py --dry-run       # preview, no DB writes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from tracker import sheets_client
from tracker.snapshot_store import SnapshotStore
import main as mainmod  # reuse _load_config + _detect_sheet_events

DB_PATH = ROOT / "data" / "tracker_csg_v2.db"
DEDUP_DAYS = 90


def fetch_csg_sheets(config_path: str = "config.yaml", dry_run: bool = False) -> None:
    cfg = mainmod._load_config(Path(config_path))
    # Prefer CSG-specific sheet IDs; fall back to the shared google_sheets block.
    if cfg.get("google_sheets_csg"):
        cfg = {**cfg, "google_sheets": cfg["google_sheets_csg"]}

    all_sheet_data = sheets_client.fetch_all_signals(cfg)
    counts = {k: len(v) for k, v in all_sheet_data.items()}
    print(f"CSG sheet rows loaded: {counts}")
    if not any(counts.values()):
        print("No CSG sheet data found. Add 'google_sheets_csg' IDs to config.yaml "
              "(and share the sheets with the service account). Nothing to do.")
        return

    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    store = SnapshotStore(DB_PATH)
    added = skipped = 0
    for c in store.get_all_companies():
        name = c.get("name", ""); domain = c.get("domain", "")
        if not name or not c.get("apollo_id"):
            continue
        sigs = sheets_client.get_company_signals(name, domain, all_sheet_data)
        events = mainmod._detect_sheet_events(c, sigs, cfg)
        for ev in events:
            if store.was_alert_sent_recently(c["apollo_id"], ev.signal_type, DEDUP_DAYS,
                                             signal_detail=ev.headline):
                skipped += 1
                continue
            if not dry_run:
                store.record_alert(
                    c["apollo_id"], ev.signal_type, ev.headline, ev.severity, False,
                    source_url=getattr(ev, "source_url", ""),
                    signal_date=getattr(ev, "signal_date", None) or None,
                )
            added += 1
            print(f"  [{ev.signal_type}] {name}: {ev.headline[:70]}")

    print(f"\nDone. {'(dry run) ' if dry_run else ''}{added} HIGH signals added, "
          f"{skipped} duplicates skipped.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch CSG HIGH signals from Google Sheets.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    fetch_csg_sheets(a.config, a.dry_run)


if __name__ == "__main__":
    main()
