"""
fetch_csg_news.py
=================
Fetches Google News RSS for all CSG companies and inserts new
"News Mention" signals into data/tracker_csg_v2.db.

Each unique article (by title) is stored as its own signal — so you get a
full news picture per company, not just one headline per run.  Re-running
is safe: duplicates (same title within 90 days) are automatically skipped.

Usage
-----
    python fetch_csg_news.py                     # all 292 companies
    python fetch_csg_news.py --company "Dell"    # single company
    python fetch_csg_news.py --dry-run           # preview, no DB writes
    python fetch_csg_news.py --max-age 60        # override 90-day window
    python fetch_csg_news.py --max-articles 3    # fewer articles per company

After running, rebuild and push:
    python build_csg_dashboard.py
    git add -A ; git commit -m "Update CSG news signals" ; git push

Constraints
-----------
- NEVER run this from the Claude bash sandbox. DB writes must happen
  from your local terminal only (CIFS mount restriction).
- Google News RSS is rate-limited. A 1.2 s sleep between companies is
  included — full 292-company run takes ~6 minutes.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from tracker.news_client import get_news_articles, _parse_article_date
from tracker.snapshot_store import SnapshotStore

DB_PATH = ROOT / "data" / "tracker_csg_v2.db"

# How many days of news to look back
DEFAULT_MAX_AGE_DAYS = 90

# Articles per company per run  (Google News RSS typically returns ≤10 anyway)
DEFAULT_MAX_ARTICLES = 5

# Seconds between company fetches — keeps Google from rate-limiting us
RATE_LIMIT_SLEEP = 1.2

# Dedup window: if the same article title was already stored within this many
# days, skip it.  Set equal to MAX_AGE_DAYS so old articles never re-appear.
DEDUP_DAYS = DEFAULT_MAX_AGE_DAYS


# ---------------------------------------------------------------------------
# Relevance filter
# ---------------------------------------------------------------------------

def _is_relevant(article: dict, company_name: str) -> bool:
    """Return True only if the article is clearly about this company.

    Google News RSS already queries with the company name in quotes, so most
    results are relevant.  We add a secondary check: the company name (or a
    known short-form) must appear in the title or summary.  This catches the
    rare case where a subsidiary name overshadows the parent in the snippet.
    """
    name_lc = company_name.lower()
    title_lc = article.get("title", "").lower()
    summary_lc = article.get("summary", "").lower()
    combined = title_lc + " " + summary_lc

    # Direct match
    if name_lc in combined:
        return True

    # Brand short-forms (e.g. "Hewlett-Packard" → also accept "hp")
    short_forms: dict[str, list[str]] = {
        "hewlett-packard": ["hp"],
        "hp inc": ["hp"],
        "lg electronics": ["lg"],
        "samsung electronics": ["samsung"],
        "sony group": ["sony"],
        "panasonic holdings": ["panasonic"],
        "toshiba corporation": ["toshiba"],
        "nec corporation": ["nec"],
        "acer inc": ["acer"],
        "asus": ["asus", "asustek"],
        "lenovo group": ["lenovo"],
        "microsoft surface": ["microsoft", "surface"],
        "apple mac": ["apple"],
    }
    for canonical, alts in short_forms.items():
        if canonical in name_lc:
            if any(alt in combined for alt in alts):
                return True

    # Partial word boundary match for multi-word names (e.g. "Dell Technologies" → "dell")
    first_word = name_lc.split()[0]
    if len(first_word) >= 4 and re.search(rf'\b{re.escape(first_word)}\b', combined):
        return True

    return False


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _fmt_signal_date(pub_str: str) -> str:
    """Convert RFC 2822 pub string to YYYY-MM-DD, or today if unparseable."""
    dt = _parse_article_date(pub_str)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Main fetch loop
# ---------------------------------------------------------------------------

def fetch_csg_news(
    company_filter: str | None = None,
    dry_run: bool = False,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_articles: int = DEFAULT_MAX_ARTICLES,
) -> None:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    store = SnapshotStore(DB_PATH)
    all_companies = store.get_all_companies()

    if not all_companies:
        print("No companies found in DB. Run seed_csg_signals.py first.")
        sys.exit(1)

    # Optional single-company filter
    if company_filter:
        needle = company_filter.strip().lower()
        all_companies = [c for c in all_companies if needle in c.get("name", "").lower()]
        if not all_companies:
            print(f"No company matching '{company_filter}' found in DB.")
            sys.exit(1)

    total = len(all_companies)
    mode_label = "[DRY RUN] " if dry_run else ""
    print(f"\n{mode_label}Fetching Google News for {total} CSG companies "
          f"(last {max_age_days} days, up to {max_articles} articles each)…\n")

    added_total   = 0   # articles inserted
    skipped_total = 0   # duplicates / irrelevant
    companies_with_news = 0

    for idx, company in enumerate(all_companies, start=1):
        name      = company.get("name", "")
        apollo_id = company.get("apollo_id", "")

        print(f"  [{idx:>3}/{total}] {name}", end="", flush=True)

        articles = get_news_articles(
            company_name=name,
            max_articles=max_articles,
            max_age_days=max_age_days,
        )

        if not articles:
            print("  — no results")
            time.sleep(RATE_LIMIT_SLEEP)
            continue

        company_added = 0

        for article in articles:
            title   = article.get("title", "").strip()
            url     = article.get("url", "")
            source  = article.get("source", "")
            pub_str = article.get("published", "")

            if not title:
                skipped_total += 1
                continue

            # Relevance check
            if not _is_relevant(article, name):
                skipped_total += 1
                continue

            # Dedup: skip if this exact headline was already stored
            if store.was_alert_sent_recently(
                apollo_id, "News Mention",
                dedup_days=DEDUP_DAYS,
                signal_detail=title,
            ):
                skipped_total += 1
                continue

            # Format source_url as "Source Name||https://..."
            source_url = f"{source}||{url}" if source else url

            if not dry_run:
                store.record_alert(
                    apollo_id=apollo_id,
                    signal_type="News Mention",
                    signal_detail=title,
                    severity="LOW",
                    dry_run=False,
                    signal_date=_fmt_signal_date(pub_str),
                    source_url=source_url,
                )

            added_total  += 1
            company_added += 1

        if company_added > 0:
            companies_with_news += 1
            print(f"  ✓  {company_added} article(s) {'(would add)' if dry_run else 'added'}")
        else:
            print("  — all duplicates / irrelevant")

        time.sleep(RATE_LIMIT_SLEEP)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'─' * 55}")
    print(f"  {'[DRY RUN] ' if dry_run else ''}Done.")
    print(f"  Companies with news : {companies_with_news} / {total}")
    print(f"  Articles added      : {added_total}")
    print(f"  Skipped (dup/irrel) : {skipped_total}")

    if added_total > 0 and not dry_run:
        print(f"\n  Next steps:")
        print(f"    python build_csg_dashboard.py")
        print(f"    git add -A ; git commit -m \"Update CSG news signals\" ; git push")
    elif dry_run and added_total > 0:
        print(f"\n  Re-run without --dry-run to write to DB.")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Google News for CSG companies and insert signals into tracker_csg_v2.db",
    )
    parser.add_argument(
        "--company", "-c",
        metavar="NAME",
        default=None,
        help="Process a single company by name (partial match, case-insensitive)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview results without writing to the database",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        metavar="DAYS",
        help=f"Only include articles newer than N days (default: {DEFAULT_MAX_AGE_DAYS})",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=DEFAULT_MAX_ARTICLES,
        metavar="N",
        help=f"Max articles to store per company (default: {DEFAULT_MAX_ARTICLES})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    fetch_csg_news(
        company_filter=args.company,
        dry_run=args.dry_run,
        max_age_days=args.max_age,
        max_articles=args.max_articles,
    )
