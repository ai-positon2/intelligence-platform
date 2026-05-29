"""
enrich_csg_from_apollo.py — apply Apollo enrichment data to tracker_csg_v2.db

Updates each company's latest snapshot row with:
  - employees
  - annual_revenue
  - latest_funding_type

Also updates the parent `companies` table with:
  - logo_url   (so the dashboard avatar grid shows the real brand logo)

Re-runnable. Reads enrichments from apollo_enrichments.json if present in the
same folder (override), otherwise uses the built-in DEFAULT_ENRICHMENTS dict.

Usage (PowerShell, local):
  cd C:\\Users\\krishna.l\\company-signal-tracker
  python enrich_csg_from_apollo.py
  python build_csg_dashboard.py
  git add -A && git commit -m "Apollo enrichment: top 10 by signals (with logos)" && git push
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tracker_csg_v2.db"
OVERRIDE_JSON = Path(__file__).parent / "apollo_enrichments.json"

# Initial batch: top 10 CSG companies by signal count.
# All 10 are publicly traded (Apollo confirmed publicly_traded_symbol for 7;
# Brother / Sharp / BigBen verified manually via stock listings TSE:6448,
# TSE:6753, Euronext:NACON respectively).
#
# Apollo returned Brother under primary_domain "brother-usa.com" and BigBen
# under "bigben.eu" — that's why earlier extraction said they were missing.
# We now have employees + revenue for all 10.
DEFAULT_ENRICHMENTS: dict[str, dict] = {
    # domain -> {employees, annual_revenue, latest_funding_type, logo_url}
    "baslerweb.com":  {"employees": 890,    "annual_revenue": 263_183_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a965620ccc4f0001cef321/picture"},
    "brother.com":    {"employees": 2200,   "annual_revenue": 3_000_000_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b39c8dc7f5cc0001471e76/picture"},
    "samsung.com":    {"employees": 127000, "annual_revenue": 230_084_404_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6a06550fae34cf0001bd103f/picture"},
    "sharp.com":      {"employees": 19000,  "annual_revenue": 2_400_000_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69aeb37f150740000187a929/picture"},
    "alpsalpine.com": {"employees": 29000,  "annual_revenue": 6_644_560_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a3c8e801f31000011fb7ec/picture"},
    "apple.com":      {"employees": 164000, "annual_revenue": 416_161_000_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69afbb99ba3131000126d493/picture"},
    "bigben.fr":      {"employees": 490,    "annual_revenue": 312_000_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69ad89d8e0c9cd0001e78e21/picture"},
    "casio.com":      {"employees": 590,    "annual_revenue": 1_776_947_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a529a146d4870001a75528/picture"},
    "compal.com":     {"employees": 44000,  "annual_revenue": 27_722_035_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a41c681c0fd700017ae0d4/picture"},
    "corsair.com":    {"employees": 2600,   "annual_revenue": 1_472_480_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69ae8cd16138a6000139a662/picture"},
}


def load_enrichments() -> dict[str, dict]:
    if OVERRIDE_JSON.exists():
        print(f"Reading enrichments from {OVERRIDE_JSON.name}")
        return json.loads(OVERRIDE_JSON.read_text(encoding="utf-8"))
    print("Using built-in DEFAULT_ENRICHMENTS")
    return DEFAULT_ENRICHMENTS


def column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def main() -> None:
    enrich = load_enrichments()
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    # CIFS-safe: never use WAL on the CIFS-mounted DB
    conn.execute("PRAGMA journal_mode=DELETE")
    cur = conn.cursor()

    # Auto-add logo_url column if missing (idempotent)
    if not column_exists(cur, "companies", "logo_url"):
        cur.execute("ALTER TABLE companies ADD COLUMN logo_url TEXT")
        print("  + Added companies.logo_url column")

    today = datetime.now().strftime("%Y-%m-%d")
    n_snap_updated = 0
    n_snap_inserted = 0
    n_logo_updated = 0
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
            # 1. companies.logo_url
            if data.get("logo_url"):
                cur.execute(
                    "UPDATE companies SET logo_url = ? WHERE apollo_id = ?",
                    (data["logo_url"], apollo_id),
                )
                n_logo_updated += 1

            # 2. latest snapshot row: employees / annual_revenue / latest_funding_type
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

            if set_parts:
                if snap:
                    params.append(snap[0])
                    cur.execute(
                        f"UPDATE snapshots SET {', '.join(set_parts)} WHERE id = ?",
                        params,
                    )
                    print(f"  ✓ {name} ({domain}): updated snapshot id={snap[0]}"
                          + (" + logo" if data.get("logo_url") else ""))
                    n_snap_updated += 1
                else:
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
                    print(f"  + {name} ({domain}): inserted new snapshot dated {today}"
                          + (" + logo" if data.get("logo_url") else ""))
                    n_snap_inserted += 1
            elif data.get("logo_url"):
                print(f"  ✓ {name} ({domain}): logo only")

    conn.commit()
    conn.close()

    print()
    print(
        f"Done. Snapshots: {n_snap_updated} updated, {n_snap_inserted} inserted.  "
        f"Logos updated: {n_logo_updated}.  Domains missing in DB: {n_missing}."
    )
    print()
    print("Next steps:")
    print("  1. python build_csg_dashboard.py")
    print("  2. git add -A")
    print("  3. git commit -m 'Apollo enrichment: top 10 by signals (logos + Brother/BigBen recovered)'")
    print("  4. git push")


if __name__ == "__main__":
    main()
