"""SQLite persistence for Job Change Alert events -- one row per person Apollo's
job-change workflow flagged in #job_change_alert_apollo. Mirrors snapshot_store.py's
shape (schema string, row_factory=sqlite3.Row, parameterized queries only)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_change_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    apollo_contact_id    TEXT UNIQUE,
    person_name          TEXT,
    linkedin_url         TEXT,
    new_title            TEXT,
    new_company_name     TEXT,
    apollo_account_id    TEXT,
    company_industry     TEXT,
    company_description  TEXT,
    city                 TEXT,
    employees            TEXT,
    revenue              TEXT,
    job_start_date       TEXT,
    detected_at          TEXT,
    slack_message_ts     TEXT,
    slack_permalink      TEXT,
    created_at           TEXT
);

CREATE INDEX IF NOT EXISTS idx_jce_detected_at ON job_change_events(detected_at DESC);
"""

_COLUMNS = (
    "apollo_contact_id", "person_name", "linkedin_url", "new_title", "new_company_name",
    "apollo_account_id", "company_industry", "company_description", "city", "employees",
    "revenue", "job_start_date", "detected_at", "slack_message_ts", "slack_permalink",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=DELETE")  # WAL unsupported on CIFS/network mounts
    except Exception:
        pass
    return conn


class JobChangeStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with _connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)

    def upsert_event(self, event: dict) -> bool:
        """Insert a new job-change event, or a no-op if apollo_contact_id already
        exists. Events with no apollo_contact_id (a malformed Apollo message) are
        always inserted -- there's no reliable key to dedup them on. Returns True
        if a new row was written."""
        row = {col: event.get(col) for col in _COLUMNS}
        row["created_at"] = _now()
        with _connect(self.db_path) as conn:
            if row["apollo_contact_id"]:
                existing = conn.execute(
                    "SELECT 1 FROM job_change_events WHERE apollo_contact_id=?",
                    (row["apollo_contact_id"],),
                ).fetchone()
                if existing:
                    return False
            conn.execute(
                f"""
                INSERT INTO job_change_events ({", ".join(_COLUMNS)}, created_at)
                VALUES ({", ".join(":" + c for c in _COLUMNS)}, :created_at)
                """,
                row,
            )
            return True

    def get_all_events(self) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM job_change_events ORDER BY detected_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_detected_at(self) -> str | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(detected_at) AS m FROM job_change_events"
            ).fetchone()
        return row["m"] if row else None

    def count(self) -> int:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM job_change_events").fetchone()
        return row["n"] if row else 0
