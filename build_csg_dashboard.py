"""
build_csg_dashboard.py
======================
One-shot script: reads CSG-company-list.xlsx and generates
reports/dashboard_csg.html -- no database required.

Usage (run locally OR in the bash sandbox):
    python build_csg_dashboard.py

Re-run any time you update the XLSX or want to refresh the dashboard.
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

XLSX_PATH = ROOT / "CSG-company-list.xlsx"
OUT_PATH  = ROOT / "reports" / "dashboard_csg.html"

COUNTRY_NORM = {
    "PRC":           "China",
    "US":            "United States",
    "UK":            "United Kingdom",
    "Taiwan/Europe": "Taiwan",
}


class _MockStore:
    """Mimics SnapshotStore using only in-memory data. No SQLite needed."""

    def __init__(self, companies):
        self._companies = companies

    def get_all_companies(self):
        return [
            {
                "apollo_id": c["apollo_id"],
                "name":      c["name"],
                "domain":    c.get("domain", ""),
                "industry":  c.get("industry", ""),
                "city":      c.get("city", ""),
                "state":     c.get("state", ""),
                "is_active": 1,
            }
            for c in self._companies
        ]

    def get_latest_snapshot(self, apollo_id):
        return None

    def get_recent_alerts(self, limit=10000, max_age_days=90):
        return []

    def get_weekly_runs(self, limit=8):
        return []


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

        raw_name  = str(row[0]).strip()
        raw_hq    = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        raw_roles = str(row[2]).strip() if len(row) > 2 and row[2] else ""

        country   = COUNTRY_NORM.get(raw_hq, raw_hq)
        apollo_id = _make_id(raw_name)
        description = ("Target roles: " + raw_roles) if raw_roles else ""

        company = {
            "apollo_id":               apollo_id,
            "name":                    raw_name,
            "domain":                  "",
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

    print("Building dashboard -> %s ..." % OUT_PATH)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    store = _MockStore(companies)
    out = build_dashboard(
        companies_from_csv=companies,
        store=store,
        output_path=OUT_PATH,
        max_signal_age_days=365,
    )

    print("\n  Dashboard written to: %s" % out)
    print("  Companies:            %d" % len(companies))
    print("\n  Deploy with:")
    print("    git add reports/dashboard_csg.html")
    print('    git commit -m "Add CSG dashboard"')
    print("    git push\n")


if __name__ == "__main__":
    build_csg()
