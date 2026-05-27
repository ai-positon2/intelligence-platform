"""One-time cleanup: remove false IPO Signal records.

These companies are already listed on NASDAQ/NYSE and were incorrectly
tagged as IPO signals in the Funding sheet (shelf registrations, earnings
reports, 8-K filings, and subsidiary info are NOT IPO events).

Run once:
    python fix_ipo_signals.py
Then:
    python main.py --sheets-only
    git add reports/dashboard.html
    git commit -m "Remove false IPO signals"
    git push
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tracker.db"

conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA journal_mode=DELETE")

# Fetch all current IPO signals so we can review what's there
rows = conn.execute("""
    SELECT id, signal_detail, source_url
    FROM alerts_sent
    WHERE signal_type = 'IPO Signal'
    ORDER BY sent_at DESC
""").fetchall()

if not rows:
    print("No IPO Signal records found.")
    conn.close()
    exit()

# These companies are already-public — their records are false positives.
# A genuine IPO signal would be for a PRIVATE company going public.
FALSE_POSITIVE_COMPANIES = [
    "Nutex Health",        # NASDAQ:NUTX — earnings report, not IPO
    "Karyopharm",          # NASDAQ:KPTI — S-3 shelf registration, not IPO
    "AngioDynamics",       # NASDAQ:ANGO — 8-K filing, not IPO
    "Omada Health",        # NASDAQ:OMDA — earnings report, not IPO
    "Guardian Flight",     # subsidiary info / already operating, not IPO
]

to_delete = []
to_keep   = []

for row_id, detail, source in rows:
    is_fp = any(fp.lower() in detail.lower() for fp in FALSE_POSITIVE_COMPANIES)
    if is_fp:
        to_delete.append((row_id, detail))
    else:
        to_keep.append((row_id, detail))

print(f"\nIPO Signal records found: {len(rows)}")
print(f"  False positives to remove: {len(to_delete)}")
print(f"  Legitimate IPO signals to keep: {len(to_keep)}\n")

if to_delete:
    print("Records to DELETE:")
    for row_id, detail in to_delete:
        print(f"  [{row_id}] {detail[:90]}")

if to_keep:
    print("\nRecords to KEEP:")
    for row_id, detail in to_keep:
        print(f"  [{row_id}] {detail[:90]}")

if not to_delete:
    print("\nNothing to delete.")
    conn.close()
    exit()

confirm = input(f"\nDelete {len(to_delete)} false-positive IPO records? [y/N] ").strip().lower()
if confirm != "y":
    print("Aborted.")
    conn.close()
    exit()

ids = [r[0] for r in to_delete]
conn.execute(
    f"DELETE FROM alerts_sent WHERE id IN ({','.join('?' * len(ids))})",
    ids,
)
conn.commit()
conn.close()

print(f"\n✓ Deleted {len(ids)} record(s).")
print("\nNext steps:")
print("  1. python main.py --sheets-only   ← regenerates dashboard")
print("  2. git add reports/dashboard.html")
print('  3. git commit -m "Remove false IPO signals"')
print("  4. git push")
