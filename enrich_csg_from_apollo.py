"""
enrich_csg_from_apollo.py — apply Apollo enrichment data to tracker_csg_v2.db

Updates the latest snapshot row for each matching company with:
  - employees
  - annual_revenue
  - latest_funding_type

Re-runnable. Reads enrichments from apollo_enrichments.json if present in the
same folder (override), otherwise uses the built-in DEFAULT_ENRICHMENTS dict.

Usage (PowerShell, local):
  cd C:\\Users\\krishna.l\\company-signal-tracker
  python enrich_csg_from_apollo.py
  python build_csg_dashboard.py
  git add -A && git commit -m "Apollo enrichment: top 10 by signals" && git push
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tracker_csg_v2.db"
OVERRIDE_JSON = Path(__file__).parent / "apollo_enrichments.json"

# Initial batch: top 10 CSG companies by signal count.
# Values for Apple/Samsung/Sharp/etc. came from Apollo bulk_enrich on 2026-05-28.
# brother.com and bigben.fr were not matched by Apollo — only Funding Stage is
# filled for those (verifiable from their public stock listings: TSE:6448 and
# Euronext:NACON respectively).
DEFAULT_ENRICHMENTS: dict[str, dict] = {
    # domain -> {employees, annual_revenue, latest_funding_type}
    "baslerweb.com":  {"employees": 890,    "annual_revenue": 263_183_000,     "latest_funding_type": "Public"},
    "brother.com":    {"employees": None,   "annual_revenue": None,            "latest_funding_type": "Public"},
    "samsung.com":    {"employees": 127000, "annual_revenue": 230_084_404_000, "latest_funding_type": "Public"},
    "sharp.com":      {"employees": 19000,  "annual_revenue": 2_400_000_000,   "latest_funding_type": "Public"},
    "alpsalpine.com": {"employees": 29000,  "annual_revenue": 6_644_560_000,   "latest_funding_type": "Public"},
    "apple.com":      {"employees": 164000, "annual_revenue": 416_161_000_000, "latest_funding_type": "Public"},
    "bigben.fr":      {"employees": None,   "annual_revenue": None,            "latest_funding_type": "Public"},
    "casio.com":      {"employees": 590,    "annual_revenue": 1_776_947_000,   "latest_funding_type": "Public"},
    "compal.com":     {"employees": 44000,  "annual_revenue": 27_722_035_000,  "latest_funding_type": "Public"},
    "corsair.com":    {"employees": 2600,   "annual_revenue": 1_472_480_000,   "latest_funding_type": "Public"},
}


def load_enrichments() -> dict[str, dict]:
    if OVERRIDE_JSON.exists():
        print(f"Reading enrichments from {OVERRIDE_JSON.name}")
        return json.loads(OVERRIDE_JSON.read_text(encoding="utf-8"))
    print("Using built-in DEFAULT_ENRICHMENTS")
    return DEFAULT_ENRICHMENTS


def main() -> None:
    enrich = load_enrichments()
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    # CIFS-safe: never use WAL on the CIFS-mounted DB
    conn.execute("PRAGMA journal_mode=DELETE")
    cur = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    n_updated = 0
    n_inserted = 0
    n_missing = 0

    for domain, data in enrich.items():
        cur.execute(
            "SELECT apollo_id, name FROM companies WHERE LOWER(domain) = LOWER(?)",
            (domain,),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"  - {domain!r}: NOT FOUND in companies table")
            n_missing += 1
            continue

        for apollo_id, name in rows:
            cur.execute(
                "SELECT id FROM snapshots WHERE apollo_id = ? "
                "ORDER BY snapshot_date DESC, id DESC LIMIT 1",
                (apollo_id,),
            )
            snap = cur.fetchone()

            set_parts: list[str] = []
            params: list = []
            if data.get("employees") is not None:
                set_parts.append("employees = ?")
                params.append(data["employees"])
            if data.get("annual_revenue") is not None:
                set_parts.append("annual_revenue = ?")
                params.append(data["annual_revenue"])
            if data.get("latest_funding_type") is not None:
                set_parts.append("latest_funding_type = ?")
                params.append(data["latest_funding_type"])

            if not set_parts:
                print(f"  - {name} ({domain}): nothing to update")
                continue

            if snap:
                params.append(snap[0])
                cur.execute(
                    f"UPDATE snapshots SET {', '.join(set_parts)} WHERE id = ?",
                    params,
                )
                print(f"  ✓ {name} ({domain}): updated snapshot id={snap[0]}")
                n_updated += 1
            else:
                # No snapshot yet — insert a fresh one with only the enriched fields
                cur.execute(
                    "INSERT INTO snapshots (apollo_id, snapshot_date, employees, annual_revenue, latest_funding_type) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        apollo_id,
                        today,
                        data.get("employees"),
                        data.get("annual_revenue"),
                        data.get("latest_funding_type"),
                    ),
                )
                print(f"  + {name} ({domain}): inserted new snapshot dated {today}")
                n_inserted += 1

    conn.commit()
    conn.close()

    print()
    print(
        f"Done. Updated {n_updated} snapshots, inserted {n_inserted}, "
        f"missed {n_missing} domain(s) not in companies table."
    )
    print()
    print("Next steps:")
    print("  1. python build_csg_dashboard.py")
    print("  2. git add -A")
    print("  3. git commit -m 'Apollo enrichment: top 10 by signals'")
    print("  4. git push")


if __name__ == "__main__":
    main()
