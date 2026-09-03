"""One-time fix: clear LinkedIn source_url on C-Suite alerts so next
--sheets-only run will backfill them with the actual press source URL
from the Notes column.

Run once from the project root:
    python fix_csuite_sources.py
Then:
    python main.py --sheets-only
    git add reports/dashboard.html
    git commit -m "Fix C-suite source URLs"
    git push
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tracker.db"

conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA journal_mode=DELETE")

# Show what we're about to fix
rows = conn.execute("""
    SELECT id, signal_detail, source_url
    FROM alerts_sent
    WHERE signal_type IN ('C-Suite Join', 'C-Suite Exit')
      AND lower(source_url) LIKE '%linkedin.com%'
    ORDER BY sent_at DESC
""").fetchall()

if not rows:
    print("Nothing to fix — no C-Suite alerts have LinkedIn-only source URLs.")
    conn.close()
    exit()

print(f"Found {len(rows)} C-Suite alert(s) with LinkedIn source URL:")
for r in rows:
    print(f"  [{r[0]}] {r[1][:70]}")
    print(f"         src: {r[2]}")

confirm = input(f"\nClear source_url for these {len(rows)} records? [y/N] ").strip().lower()
if confirm != "y":
    print("Aborted.")
    conn.close()
    exit()

result = conn.execute("""
    UPDATE alerts_sent
    SET source_url = ''
    WHERE signal_type IN ('C-Suite Join', 'C-Suite Exit')
      AND lower(source_url) LIKE '%linkedin.com%'
""")
conn.commit()
conn.close()

print(f"\n✓ Cleared {result.rowcount} record(s).")
print("\nNext steps:")
print("  1. python main.py --sheets-only")
print("  2. git add reports/dashboard.html")
print('  3. git commit -m "Fix C-suite source URLs"')
print("  4. git push")
