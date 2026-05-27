"""
build_csg_dashboard.py
======================
One-shot script: reads CSG-company-list.xlsx and generates
reports/dashboard_csg.html backed by a persistent SQLite database.

Usage:
    python build_csg_dashboard.py

Re-run any time you want to refresh the dashboard. Companies are
upserted into the DB on every run so new rows in the XLSX appear
automatically. Signals are written separately via seed_csg_signals.py.

DB path: data/tracker_csg_v2.db  (never touches the corrupted legacy DBs)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl not found.  Run:  pip install openpyxl")
    sys.exit(1)

from tracker.dashboard_builder import build_dashboard
from tracker.snapshot_store import SnapshotStore

XLSX_PATH = ROOT / "CSG-company-list.xlsx"
OUT_PATH  = ROOT / "reports" / "dashboard_csg.html"
DB_PATH   = ROOT / "data" / "tracker_csg_v2.db"

COUNTRY_NORM = {
    "PRC":           "China",
    "US":            "United States",
    "UK":            "United Kingdom",
    "Taiwan/Europe": "Taiwan",
}


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")[:64]


def _make_id(name):
    return "csg:" + _slugify(name)


def build_csg():
    if not XLSX_PATH.exists():
        print("ERROR: %s not found." % XLSX_PATH)
        print("       Place CSG-company-list.xlsx in the project root and re-run.")
        sys.exit(1)

    print("Reading %s ..." % XLSX_PATH.name)
    wb = openpyxl.load_workbook(XLSX_PATH)
    ws = wb.active

    companies = []
    skipped = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            skipped += 1
            continue

        raw_name   = str(row[0]).strip()
        raw_hq     = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        raw_roles  = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        raw_domain = str(row[3]).strip() if len(row) > 3 and row[3] else ""

        country   = COUNTRY_NORM.get(raw_hq, raw_hq)
        apollo_id = _make_id(raw_name)
        description = ("Target roles: " + raw_roles) if raw_roles else ""

        company = {
            "apollo_id":               apollo_id,
            "name":                    raw_name,
            "domain":                  raw_domain,
            "city":                    country,
            "state":                   "",
            "country":                 country,
            "industry":                "Technology",
            "tier":                    2,
            "keywords":                raw_roles,
            "description":             description,
            "logo_url":                "",
            "linkedin_url":            "",
            "twitter_url":             "",
            "facebook_url":            "",
            "employees":               None,
            "annual_revenue":          None,
            "annual_revenue_fmt":      "--",
            "total_funding":           None,
            "total_funding_fmt":       "--",
            "latest_funding_type":     "",
            "latest_funding_amount":   None,
            "latest_funding_amount_fmt": "--",
            "last_raised_at":          "",
            "founded_year":            None,
            "tech_stack":              [],
            "intent_score_1":          0,
            "intent_topic_1":          "",
            "intent_score_2":          0,
            "intent_topic_2":          "",
            "crm_stage":               "",
            "retail_locations":        None,
            "subsidiary_of":           "",
        }
        companies.append(company)

    print("  Loaded %d companies (skipped %d blank rows)" % (len(companies), skipped))

    print("Opening DB -> %s ..." % DB_PATH)
    store = SnapshotStore(DB_PATH)

    # Upsert every company so new XLSX rows appear in the DB automatically
    for co in companies:
        store.upsert_company({
            "apollo_id": co["apollo_id"],
            "name":      co["name"],
            "domain":    co.get("domain", ""),
            "industry":  co.get("industry", "Technology"),
            "city":      co.get("city", ""),
            "state":     co.get("state", ""),
        })

    alert_count = len(store.get_recent_alerts(limit=100_000, max_age_days=730))
    print("  Signals in DB:        %d" % alert_count)

    print("Building dashboard -> %s ..." % OUT_PATH)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    csg_refresh_opts = [
        {
            "id": "opt-seed",
            "icon": "🌱",
            "title": "Re-seed Signals",
            "desc": "Re-inserts all manually-researched signals from KNOWN_SIGNALS into the database.<br>Safe to re-run — duplicates are automatically skipped.",
            "cmd": "python seed_csg_signals.py",
        },
        {
            "id": "opt-rebuild",
            "icon": "⚙️",
            "title": "Rebuild Dashboard",
            "desc": "Regenerates the dashboard HTML from the database.<br>Run this after re-seeding or adding new signals.",
            "cmd": "python build_csg_dashboard.py",
        },
        {
            "id": "opt-publish",
            "icon": "🚀",
            "title": "Publish to Live Site",
            "desc": "After rebuilding the dashboard, push to Railway to go live.<br>Run this from <strong>C:\\Users\\krishna.l\\company-signal-tracker\\</strong>",
            "cmd": "git add -A ; git commit -m \"Update CSG dashboard\" ; git push",
        },
    ]

    out = build_dashboard(
        companies_from_csv=companies,
        store=store,
        output_path=OUT_PATH,
        max_signal_age_days=365,
        refresh_opts=csg_refresh_opts,
    )

    print("\n  Dashboard written to: %s" % out)
    print("  Companies:            %d" % len(companies))
    print("\n  Deploy with:")
    print("    git add reports/dashboard_csg.html")
    print('    git commit -m "Add CSG dashboard"')
    print("    git push\n")


if __name__ == "__main__":
    build_csg()
