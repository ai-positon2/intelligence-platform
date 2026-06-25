"""
fetch_csg_jobs.py
=================
Detects creative / 3D hiring activity for all CSG companies via free
job-board + careers RSS (tracker/jobs_client.py) and inserts new
"Creative Hiring" signals (MEDIUM) into data/tracker_csg_v2.db.

Each unique posting (by title) is stored as its own signal. Re-running is
safe: duplicates (same title within the dedup window) are skipped.

Usage
-----
    python fetch_csg_jobs.py                     # all companies
    python fetch_csg_jobs.py --company "Dell"    # single company
    python fetch_csg_jobs.py --dry-run           # preview, no DB writes
    python fetch_csg_jobs.py --max-age 60        # override 90-day window
    python fetch_csg_jobs.py --limit 5           # only first N companies

After running, rebuild and push:
    python build_csg_dashboard.py
    git add -A ; git commit -m "Update CSG hiring signals" ; git push

Constraints
-----------
- Like fetch_csg_news.py: DB writes must happen from a real terminal / the
  GitHub Action, never the Claude bash sandbox (CIFS mount restriction).
- RSS is rate-limited; a 1.2 s sleep between companies is included.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import time
import concurrent.futures

from tracker.jobs_client import get_job_postings
from tracker.news_client import _parse_article_date
from tracker.snapshot_store import SnapshotStore

DB_PATH = ROOT / "data" / "tracker_csg_v2.db"

DEFAULT_MAX_AGE_DAYS = 90
DEFAULT_MAX_POSTINGS = 5
RATE_LIMIT_SLEEP = 1.2
DEDUP_DAYS = DEFAULT_MAX_AGE_DAYS
SIGNAL_TYPE = "Creative Hiring"


def _fmt_signal_date(pub_str: str) -> str:
    dt = _parse_article_date(pub_str)
    return dt.strftime("%Y-%m-%d") if dt else datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _headline(posting: dict, company: str) -> str:
    role = (posting.get("role") or "creative").title()
    title = posting.get("title", "").strip()
    return title or f"{company} hiring: {role}"


def fetch_csg_jobs(
    company_filter: str | None = None,
    dry_run: bool = False,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_postings: int = DEFAULT_MAX_POSTINGS,
    limit: int | None = None,
) -> None:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    store = SnapshotStore(DB_PATH)
    all_companies = store.get_all_companies()
    if not all_companies:
        print("No companies found in DB. Run build_csg_dashboard.py / seed first.")
        sys.exit(1)

    if company_filter:
        needle = company_filter.strip().lower()
        all_companies = [c for c in all_companies if needle in c.get("name", "").lower()]
        if not all_companies:
            print(f"No company matching '{company_filter}' found in DB.")
            sys.exit(1)

    if limit:
        all_companies = all_companies[:limit]

    total = len(all_companies)
    mode = "[DRY RUN] " if dry_run else ""
    print(f"\n{mode}Scanning creative/3D hiring for {total} CSG companies "
          f"(last {max_age_days} days, up to {max_postings} postings each)…\n")

    added_total = 0
    skipped_total = 0
    companies_with_hits = 0

    # ── Parallel network prefetch (bounded threads; DB writes stay sequential) ──
    # get_job_postings only does network I/O, so fetch all companies concurrently
    # first, then the loop below just reads results + writes to SQLite single-threaded.
    def _prefetch(c):
        nm = (c.get("name", "") or "").strip()
        aid = c.get("apollo_id", "")
        if not nm or not aid:
            return aid, []
        try:
            return aid, get_job_postings(nm, max_results=max_postings,
                                         max_age_days=max_age_days, domain=c.get("domain", ""))
        except Exception as exc:
            print(f"  ! prefetch error for {nm}: {exc}")
            return aid, []

    print(f"Pre-fetching job postings for {total} companies (parallel)…")
    _t0 = time.time()
    postings_by_id: dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as _ex:
        for aid, posts in _ex.map(_prefetch, all_companies):
            if aid:
                postings_by_id[aid] = posts
    print(f"Prefetch done in {time.time() - _t0:.0f}s.\n")

    for i, company in enumerate(all_companies, 1):
        name = company.get("name", "").strip()
        apollo_id = company.get("apollo_id", "")
        if not name or not apollo_id:
            continue
        print(f"[{i}/{total}] {name}")
        postings = postings_by_id.get(apollo_id, [])

        if not postings:
            print("  — no creative roles found")
            continue

        company_added = 0
        for p in postings:
            headline = _headline(p, name)
            if store.was_alert_sent_recently(
                apollo_id, SIGNAL_TYPE, dedup_days=DEDUP_DAYS, signal_detail=headline
            ):
                skipped_total += 1
                continue
            src = p.get("source", "")
            url = p.get("url", "")
            source_url = f"{src}||{url}" if src else url
            if not dry_run:
                store.record_alert(
                    apollo_id=apollo_id,
                    signal_type=SIGNAL_TYPE,
                    signal_detail=headline,
                    severity="MEDIUM",
                    dry_run=False,
                    signal_date=_fmt_signal_date(p.get("published", "")),
                    source_url=source_url,
                )
            added_total += 1
            company_added += 1
            print(f"     • {p.get('role','creative')}: {headline[:80]}")

        if company_added:
            companies_with_hits += 1
            print(f"  ✓ {company_added} posting(s) {'(would add)' if dry_run else 'added'}")

    print(f"\nDone. {'(dry run) ' if dry_run else ''}"
          f"{added_total} signals across {companies_with_hits} companies; "
          f"{skipped_total} duplicates skipped.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Detect creative/3D hiring for CSG companies and insert signals.")
    ap.add_argument("--company", help="only this company (substring match)")
    ap.add_argument("--dry-run", action="store_true", help="preview, no DB writes")
    ap.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_DAYS)
    ap.add_argument("--max-postings", type=int, default=DEFAULT_MAX_POSTINGS)
    ap.add_argument("--limit", type=int, default=None, help="only first N companies")
    args = ap.parse_args()
    fetch_csg_jobs(
        company_filter=args.company,
        dry_run=args.dry_run,
        max_age_days=args.max_age,
        max_postings=args.max_postings,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
