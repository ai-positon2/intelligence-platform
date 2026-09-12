#!/usr/bin/env python3
"""One-time migration: backfill snapshots.description from the legacy
raw_json blob, then clear raw_json.

save_snapshot() used to store the ENTIRE incoming company payload (~4.2KB
average) in every snapshot row, on every run, for every company -- to read
back exactly one field (description) in one place (change_detector.py's
Description Update check). At ~1,250 companies that grew data/tracker.db's
snapshots table to 90MB+ of the file's 93MB in three months of runs, closing
in on GitHub's 100MB hard per-file limit (this repo commits its databases --
see README.md / .gitignore). snapshot_store.py no longer writes raw_json;
this script backfills description for every row saved before that change and
clears the now-redundant raw_json, then VACUUMs to actually reclaim the disk
space (SQLite doesn't shrink a file on DELETE/UPDATE without one).

Usage:
    python scripts/migrate_snapshot_raw_json.py [db_path ...]

With no arguments, migrates every data/*.db file that has a snapshots table.
Idempotent: a row with no raw_json is skipped, so re-running is a no-op.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _default_db_paths() -> list[Path]:
    return sorted(p for p in (ROOT / "data").glob("*.db"))


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        print(f"  skip (not found): {db_path}")
        return
    before = db_path.stat().st_size
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
        if "raw_json" not in cols:
            print(f"  skip (no raw_json column, already migrated or never had it): {db_path}")
            return
        if "description" not in cols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN description TEXT")

        rows = conn.execute(
            "SELECT id, raw_json FROM snapshots "
            "WHERE raw_json IS NOT NULL AND raw_json != '' "
            "AND (description IS NULL OR description = '')"
        ).fetchall()
        updated = 0
        for row_id, raw in rows:
            try:
                desc = (json.loads(raw) or {}).get("description") or None
            except Exception:
                desc = None
            conn.execute("UPDATE snapshots SET description = ? WHERE id = ?", (desc, row_id))
            updated += 1

        cleared = conn.execute(
            "UPDATE snapshots SET raw_json = NULL WHERE raw_json IS NOT NULL AND raw_json != ''"
        ).rowcount
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()

    after = db_path.stat().st_size
    print(f"  {db_path}: backfilled description on {updated} row(s), cleared raw_json on "
          f"{cleared} row(s), {before/1024/1024:.1f} MB -> {after/1024/1024:.1f} MB")


def main(argv: list[str]) -> None:
    paths = [Path(p) for p in argv] if argv else _default_db_paths()
    if not paths:
        print("No .db files found under data/.")
        return
    for p in paths:
        migrate(p)


if __name__ == "__main__":
    main(sys.argv[1:])
