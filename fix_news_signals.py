"""One-time cleanup: remove M&A and IPO signals that were created from
Google News headlines (they were false positives — e.g. 'Ciscomani hosts
federal leaders' is NOT an IPO).

Going forward, Acquisition / M&A and IPO Signal only come from your Funding
Google Sheet. Google News produces News Mention (LOW) only.

Run once:
    python fix_news_signals.py
Then:
    python main.py --sheets-only
    git add reports/dashboard.html
    git commit -m "IPO/M&A signals from Sheets only; news → News tab"
    git push
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tracker.db"

conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA journal_mode=DELETE")

rows = conn.execute("""
    SELECT id, signal_type, signal_detail, source_url
    FROM alerts_sent
    WHERE signal_type IN ('Acquisition / M&A', 'IPO Signal')
    ORDER BY sent_at DESC
""").fetchall()

if not rows:
    print("Nothing to clean up — no M&A or IPO signals in DB.")
    conn.close()
    exit()

print(f"Found {len(rows)} M&A / IPO signal(s) to remove:\n")
for r in rows:
    print(f"  [{r[0]}] {r[1]}")
    print(f"         {r[2][:80]}")
    print(f"         src: {(r[3] or '(none)')[:80]}")
    print()

confirm = input(f"Delete all {len(rows)} records? [y/N] ").strip().lower()
if confirm != "y":
    print("Aborted.")
    conn.close()
    exit()

result = conn.execute("""
    DELETE FROM alerts_sent
    WHERE signal_type IN ('Acquisition / M&A', 'IPO Signal')
""")
conn.commit()
conn.close()

print(f"\n✓ Deleted {result.rowcount} record(s).")
print("\nNext steps:")
print("  1. python main.py --sheets-only   ← re-reads Funding sheet, adds correct signals")
print("  2. git add reports/dashboard.html")
print('  3. git commit -m "IPO/M&A signals from Sheets only"')
print("  4. git push")
