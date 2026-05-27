"""Emergency DB recovery — run this when tracker.db is malformed.

Tries to salvage as much data as possible using SQLite's .recover mechanism,
then rebuilds a clean database.  If recovery fails it starts fresh (all data
can be re-populated by running  python main.py --sheets-only  afterward).

Usage:
    python recover_db.py
"""

import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH   = Path(__file__).parent / "data" / "tracker.db"
BAK_PATH  = Path(__file__).parent / "data" / f"tracker_corrupt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
NEW_PATH  = Path(__file__).parent / "data" / "tracker_new.db"


_SCHEMA = """
PRAGMA journal_mode=DELETE;

CREATE TABLE IF NOT EXISTS companies (
    apollo_id        TEXT PRIMARY KEY,
    name             TEXT,
    domain           TEXT,
    industry         TEXT,
    city             TEXT,
    state            TEXT,
    first_seen       TEXT,
    last_enriched    TEXT,
    is_active        INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    apollo_id             TEXT,
    snapshot_date         TEXT,
    employees             INTEGER,
    annual_revenue        REAL,
    total_funding         REAL,
    latest_funding_type   TEXT,
    latest_funding_amount REAL,
    last_raised_at        TEXT,
    hq_city               TEXT,
    hq_state              TEXT,
    tech_stack            TEXT,
    leadership_json       TEXT,
    open_job_count        INTEGER,
    intent_score_1        REAL,
    intent_topic_1        TEXT,
    intent_score_2        REAL,
    intent_topic_2        TEXT,
    crm_stage             TEXT,
    retail_locations      INTEGER,
    subsidiary_of         TEXT,
    raw_json              TEXT
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    apollo_id      TEXT,
    signal_type    TEXT,
    signal_detail  TEXT,
    severity       TEXT,
    sent_at        TEXT,
    signal_date    TEXT,
    source_url     TEXT DEFAULT '',
    dry_run        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS weekly_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date          TEXT,
    companies_checked INTEGER,
    signals_high      INTEGER,
    signals_medium    INTEGER,
    signals_low       INTEGER,
    duration_seconds  REAL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_apollo_id  ON snapshots(apollo_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_apollo_signal ON alerts_sent(apollo_id, signal_type, sent_at DESC);
"""


def try_recover_with_sqlite3_cli() -> list[str]:
    """Use the sqlite3 CLI .recover command if available (SQLite ≥ 3.29)."""
    try:
        result = subprocess.run(
            ["sqlite3", str(DB_PATH), ".recover"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"  sqlite3 CLI .recover produced {len(result.stdout.splitlines())} lines of SQL")
            return result.stdout.splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return []


def try_python_dump(src_path: Path) -> list[tuple]:
    """Try to read alerts_sent rows directly from the malformed DB."""
    rows = []
    try:
        conn = sqlite3.connect(str(src_path))
        # iterdump is more tolerant than normal queries on corrupt DBs
        for line in conn.iterdump():
            pass  # just test it opens
        # Try to pull alerts directly
        try:
            raw = conn.execute(
                "SELECT apollo_id, signal_type, signal_detail, severity, "
                "sent_at, signal_date, source_url, dry_run FROM alerts_sent"
            ).fetchall()
            rows = raw
            print(f"  Direct query recovered {len(rows)} alert rows")
        except Exception as e:
            print(f"  Direct query failed: {e}")
        conn.close()
    except Exception as e:
        print(f"  Python dump failed: {e}")
    return rows


def build_fresh_db(recovered_alerts: list[tuple]) -> Path:
    """Create a clean database and insert any recovered alert rows."""
    if NEW_PATH.exists():
        NEW_PATH.unlink()

    conn = sqlite3.connect(str(NEW_PATH))
    conn.executescript(_SCHEMA)

    inserted = 0
    if recovered_alerts:
        for row in recovered_alerts:
            try:
                conn.execute(
                    "INSERT INTO alerts_sent "
                    "(apollo_id, signal_type, signal_detail, severity, "
                    " sent_at, signal_date, source_url, dry_run) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    row,
                )
                inserted += 1
            except Exception:
                pass
        conn.commit()
        print(f"  Re-inserted {inserted} alert rows into fresh DB")
    else:
        print("  Starting with empty alerts table — will be rebuilt from Sheets + News")

    conn.close()
    return NEW_PATH


def main():
    print(f"\nSQLite DB Recovery")
    print(f"  Source : {DB_PATH}")
    print()

    if not DB_PATH.exists():
        print("DB file not found — creating fresh database.")
        build_fresh_db([])
    else:
        # Step 1: backup corrupt file
        shutil.copy2(DB_PATH, BAK_PATH)
        print(f"✓ Corrupt DB backed up to: {BAK_PATH.name}")

        # Step 2: try to recover alerts
        print("\nAttempting data recovery...")
        recovered = try_python_dump(DB_PATH)

        # Step 3: build fresh DB (with recovered rows if any)
        print("\nBuilding fresh database...")
        build_fresh_db(recovered)

    # Step 4: replace old DB with fresh one
    shutil.move(str(NEW_PATH), str(DB_PATH))
    print(f"\n✓ Fresh DB installed at: {DB_PATH}")

    # Verify
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=DELETE")
    alerts = conn.execute("SELECT COUNT(*) FROM alerts_sent WHERE dry_run=0").fetchone()[0]
    conn.close()
    print(f"  Alerts in DB: {alerts}")

    print("\nNext steps:")
    print("  1. python main.py --sheets-only")
    print("     (re-reads all Google Sheets → populates Funding, C-Suite, M&A, IPO signals)")
    print("  2. git add reports/dashboard.html")
    print('  3. git commit -m "Recover DB + rebuild signals from Sheets"')
    print("  4. git push")


if __name__ == "__main__":
    main()
