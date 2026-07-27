"""
build_northstar_dashboard.py
=============================
One-shot script: reads northstar-company-details.csv (an Apollo account export
for the NorthStar Anesthesia ABM universe) and generates
reports/dashboard_northstar.html backed by a persistent SQLite database.

Usage:
    python build_northstar_dashboard.py

Re-run any time you want to refresh the dashboard. Companies are upserted into
the DB on every run so new rows in the CSV appear automatically. Signals are
written separately (seed script TBD once NorthStar sends the signal set).

DB path: data/tracker_northstar.db  (isolated per-client DB)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from tracker.csv_loader import load_companies
from tracker.dashboard_builder import build_dashboard
from tracker.snapshot_store import SnapshotStore

CSV_PATH = ROOT / "northstar-company-details.csv"
OUT_PATH = ROOT / "reports" / "dashboard_northstar.html"
OUT_CLIENT_PATH = ROOT / "reports" / "dashboard_northstar_client.html"
DB_PATH  = ROOT / "data" / "tracker_northstar.db"

# CSS injected into the client-facing build only. The dashboard engine is shared
# with the internal /p2 dashboards, so it ships an internal-ops chrome (Refresh
# Dashboard modal with terminal commands, the Position2 staff identity, a "Switch
# Account" link to /accounts). None of that belongs in a client's co-branded
# portal, so the client variant hides it via CSS. The company/signal data and the
# whole Overview/Companies/Signals/Trends experience are untouched.
CLIENT_HIDE_CSS = """
<style id="p2-client-mode">
  #refresh-btn,#refresh-overlay,#refresh-modal{display:none!important}
  .sidebar-switch-btn,.sidebar-user{display:none!important}
  .sidebar-footer{border-top:none!important;padding-top:8px!important}
</style>
"""


def build_northstar():
    if not CSV_PATH.exists():
        print("ERROR: %s not found." % CSV_PATH)
        print("       Export the NorthStar-Funding 'Company details' tab as CSV")
        print("       to the project root and re-run.")
        sys.exit(1)

    print("Reading %s ..." % CSV_PATH.name)
    companies = load_companies(CSV_PATH)
    if not companies:
        print("ERROR: no companies loaded from CSV.")
        sys.exit(1)

    print("Opening DB -> %s ..." % DB_PATH)
    store = SnapshotStore(DB_PATH)

    # Upsert every company so new CSV rows appear in the DB automatically.
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
    print("  Companies loaded:     %d" % len(companies))
    print("  Signals in DB:        %d" % alert_count)

    print("Building dashboard -> %s ..." % OUT_PATH)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    northstar_refresh_opts = [
        {
            "id": "opt-companies",
            "icon": "🏢",
            "title": "Refresh Company Universe",
            "desc": "Re-reads northstar-company-details.csv and upserts every account into the DB.<br>Safe to re-run — existing companies are updated in place.",
            "cmd": "python build_northstar_dashboard.py",
        },
        {
            "id": "opt-signals",
            "icon": "⚡",
            "title": "Load Signals",
            "desc": "Loads the NorthStar signal set (funding, M&amp;A, C-suite, news) into the DB.<br>Run after the signal sheet is finalised.",
            "cmd": "python seed_northstar_signals.py",
        },
        {
            "id": "opt-rebuild",
            "icon": "⚙️",
            "title": "Rebuild Dashboard",
            "desc": "Regenerates the dashboard HTML from the database.<br>Run this after loading signals.",
            "cmd": "python build_northstar_dashboard.py",
        },
    ]

    out = build_dashboard(
        companies_from_csv=companies,
        store=store,
        output_path=OUT_PATH,
        max_signal_age_days=90,
        refresh_opts=northstar_refresh_opts,
    )

    # Client-facing variant: same dashboard, internal-ops chrome hidden.
    html = OUT_PATH.read_text(encoding="utf-8")
    if "</head>" in html:
        html_client = html.replace("</head>", CLIENT_HIDE_CSS + "</head>", 1)
    else:
        html_client = CLIENT_HIDE_CSS + html
    OUT_CLIENT_PATH.write_text(html_client, encoding="utf-8")

    print("\n  Internal dashboard:   %s" % out)
    print("  Client dashboard:     %s" % OUT_CLIENT_PATH)
    print("  Companies:            %d" % len(companies))


if __name__ == "__main__":
    build_northstar()
