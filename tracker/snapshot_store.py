"""SQLite persistence — companies, snapshots, alerts, weekly run records."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_snapshots_apollo_id    ON snapshots(apollo_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_apollo_signal   ON alerts_sent(apollo_id, signal_type, sent_at DESC);
"""


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


class SnapshotStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with _connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
            # Migration: add signal_date column to existing databases
            try:
                conn.execute("ALTER TABLE alerts_sent ADD COLUMN signal_date TEXT")
            except Exception:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE alerts_sent ADD COLUMN source_url TEXT DEFAULT ''")
            except Exception:
                pass  # Column already exists

    # ── Companies ──────────────────────────────────────────────────────────────

    def upsert_company(self, company: dict) -> None:
        now = _now()
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO companies (apollo_id, name, domain, industry, city, state, first_seen, last_enriched)
                VALUES (:apollo_id, :name, :domain, :industry, :city, :state, :now, :now)
                ON CONFLICT(apollo_id) DO UPDATE SET
                    name=excluded.name, domain=excluded.domain, industry=excluded.industry,
                    city=excluded.city, state=excluded.state, last_enriched=excluded.last_enriched,
                    is_active=1
                """,
                {**company, "now": now},
            )

    def get_all_companies(self) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM companies WHERE is_active=1 ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    # ── Snapshots ──────────────────────────────────────────────────────────────

    def save_snapshot(self, apollo_id: str, data: dict) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO snapshots (
                    apollo_id, snapshot_date, employees, annual_revenue, total_funding,
                    latest_funding_type, latest_funding_amount, last_raised_at,
                    hq_city, hq_state, tech_stack, leadership_json, open_job_count,
                    intent_score_1, intent_topic_1, intent_score_2, intent_topic_2,
                    crm_stage, retail_locations, subsidiary_of, raw_json
                ) VALUES (
                    :apollo_id, :snapshot_date, :employees, :annual_revenue, :total_funding,
                    :latest_funding_type, :latest_funding_amount, :last_raised_at,
                    :hq_city, :hq_state, :tech_stack, :leadership_json, :open_job_count,
                    :intent_score_1, :intent_topic_1, :intent_score_2, :intent_topic_2,
                    :crm_stage, :retail_locations, :subsidiary_of, :raw_json
                )
                """,
                {
                    "apollo_id": apollo_id,
                    "snapshot_date": _now(),
                    "employees": data.get("employees"),
                    "annual_revenue": data.get("annual_revenue"),
                    "total_funding": data.get("total_funding"),
                    "latest_funding_type": data.get("latest_funding_type"),
                    "latest_funding_amount": data.get("latest_funding_amount"),
                    "last_raised_at": data.get("last_raised_at"),
                    "hq_city": data.get("city") or data.get("hq_city"),
                    "hq_state": data.get("state") or data.get("hq_state"),
                    "tech_stack": json.dumps(data.get("tech_stack") or []),
                    "leadership_json": json.dumps(data.get("leadership") or []),
                    "open_job_count": data.get("open_job_count") or 0,
                    "intent_score_1": data.get("intent_score_1"),
                    "intent_topic_1": data.get("intent_topic_1"),
                    "intent_score_2": data.get("intent_score_2"),
                    "intent_topic_2": data.get("intent_topic_2"),
                    "crm_stage": data.get("crm_stage"),
                    "retail_locations": data.get("retail_locations"),
                    "subsidiary_of": data.get("subsidiary_of"),
                    "raw_json": json.dumps(data, default=str),
                },
            )
            conn.execute(
                "UPDATE companies SET last_enriched=? WHERE apollo_id=?",
                (_now(), apollo_id),
            )

    def get_latest_snapshot(self, apollo_id: str) -> dict | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE apollo_id=? ORDER BY snapshot_date DESC LIMIT 1",
                (apollo_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("tech_stack", "leadership_json"):
            try:
                result[field] = json.loads(result[field] or "[]")
            except Exception:
                result[field] = []
        return result

    def has_any_snapshot(self, apollo_id: str) -> bool:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM snapshots WHERE apollo_id=? LIMIT 1", (apollo_id,)
            ).fetchone()
        return row is not None

    def has_any_snapshots_at_all(self) -> bool:
        """True if the snapshots table contains at least one row."""
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone()
        return row is not None

    # ── Alerts ─────────────────────────────────────────────────────────────────

    def record_alert(
        self,
        apollo_id: str,
        signal_type: str,
        signal_detail: str,
        severity: str,
        dry_run: bool = False,
        signal_date: str | None = None,
        source_url: str = "",
    ) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO alerts_sent (apollo_id, signal_type, signal_detail, severity, sent_at, signal_date, source_url, dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (apollo_id, signal_type, signal_detail, severity, _now(), signal_date or _now(), source_url or "", int(dry_run)),
            )

    def was_alert_sent_recently(
        self, apollo_id: str, signal_type: str, dedup_days: int,
        signal_detail: str | None = None,
    ) -> bool:
        """Return True if this alert was already sent within dedup_days.

        When signal_detail is provided (e.g. person headline for C-Suite),
        the check is scoped to that exact detail so multiple people at the
        same company each get their own alert.
        """
        with _connect(self.db_path) as conn:
            if signal_detail:
                row = conn.execute(
                    """
                    SELECT 1 FROM alerts_sent
                    WHERE apollo_id=? AND signal_type=? AND signal_detail=? AND dry_run=0
                      AND datetime(sent_at) >= datetime('now', ? || ' days')
                    LIMIT 1
                    """,
                    (apollo_id, signal_type, signal_detail, f"-{dedup_days}"),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT 1 FROM alerts_sent
                    WHERE apollo_id=? AND signal_type=? AND dry_run=0
                      AND datetime(sent_at) >= datetime('now', ? || ' days')
                    LIMIT 1
                    """,
                    (apollo_id, signal_type, f"-{dedup_days}"),
                ).fetchone()
        return row is not None

    def get_recent_alerts(self, limit: int = 200, max_age_days: int = 90) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT a.*, c.name AS company_name, c.domain, c.industry
                FROM alerts_sent a
                LEFT JOIN companies c ON a.apollo_id = c.apollo_id
                WHERE datetime(a.sent_at) >= datetime('now', ? || ' days')
                  AND a.dry_run = 0
                ORDER BY a.sent_at DESC
                LIMIT ?
                """,
                (f"-{max_age_days}", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_company_alerts(self, apollo_id: str, limit: int = 50) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM alerts_sent WHERE apollo_id=? ORDER BY sent_at DESC LIMIT ?",
                (apollo_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_source_url_if_better(
        self,
        apollo_id: str,
        signal_type: str,
        signal_detail: str,
        source_url: str,
    ) -> None:
        """Patch source_url on the most recent matching alert when we have a better value.

        "Better" means:
          - The existing record has no source_url (empty / NULL), OR
          - The existing record only has a LinkedIn URL but the new value is NOT LinkedIn
            (i.e. we now have the real press-release / article link from Notes).

        Called when dedup blocks re-insertion so that already-stored alerts get
        the correct source link without needing a full reset.
        """
        if not source_url:
            return
        new_is_linkedin = "linkedin.com" in source_url.lower()
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE alerts_sent
                SET    source_url = ?
                WHERE  id = (
                    SELECT id FROM alerts_sent
                    WHERE  apollo_id     = ?
                      AND  signal_type   = ?
                      AND  signal_detail = ?
                      AND  dry_run       = 0
                    ORDER BY sent_at DESC
                    LIMIT 1
                )
                AND (
                    source_url IS NULL
                    OR source_url = ''
                    OR (? = 0 AND lower(source_url) LIKE '%linkedin.com%')
                )
                """,
                (source_url, apollo_id, signal_type, signal_detail, int(new_is_linkedin)),
            )

    def reset_alerts(self) -> None:
        with _connect(self.db_path) as conn:
            conn.execute("DELETE FROM alerts_sent")
        logger.info("Alert history cleared.")

    # ── Weekly runs ────────────────────────────────────────────────────────────

    def record_weekly_run(
        self,
        companies_checked: int,
        signals_high: int,
        signals_medium: int,
        signals_low: int,
        duration_seconds: float,
    ) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO weekly_runs
                    (run_date, companies_checked, signals_high, signals_medium, signals_low, duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (_now(), companies_checked, signals_high, signals_medium, signals_low, duration_seconds),
            )

    def get_weekly_runs(self, limit: int = 8) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM weekly_runs ORDER BY run_date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Dashboard helpers ──────────────────────────────────────────────────────

    def get_all_active_companies(self) -> list[dict]:
        """Companies joined with their most recent snapshot."""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT c.*, s.employees, s.annual_revenue, s.latest_funding_type,
                       s.open_job_count, s.hq_city, s.hq_state, s.intent_score_1, s.intent_topic_1
                FROM companies c
                LEFT JOIN snapshots s
                    ON s.apollo_id = c.apollo_id
                   AND s.snapshot_date = (
                       SELECT MAX(s2.snapshot_date) FROM snapshots s2 WHERE s2.apollo_id = c.apollo_id
                   )
                WHERE c.is_active=1
                ORDER BY c.name
                """
            ).fetchall()
        return [dict(r) for r in rows]
