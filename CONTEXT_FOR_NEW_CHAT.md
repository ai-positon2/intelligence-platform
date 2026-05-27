# Company Signal Tracker — Full Context for New Chat

Paste this entire file at the start of a new chat to continue work without losing context.

---

## WHAT THIS PROJECT IS

A **weekly company signal tracker** for **1,251 healthcare companies** (from an Apollo CSV export). It detects business events (funding rounds, C-suite changes, M&A, IPO, news mentions, etc.) and presents them in a self-contained HTML dashboard. No Apollo API calls during runs — HIGH signals come from user-maintained Google Sheets, LOW signals from Google News RSS.

**Deployed on:** Railway at `https://company-signal-tracker-production.up.railway.app`  
**Local folder:** `C:\Users\krishna.l\company-signal-tracker\`  
**Run by:** `python main.py [flags]`

---

## ARCHITECTURE OVERVIEW

```
company-signal-tracker/
├── main.py                          ← orchestrator (665 lines)
├── config.yaml                      ← all settings, sheet IDs, credentials
├── apollo-accounts-export.csv       ← master company list (1,251 healthcare companies)
├── service_account.json             ← Google service account credentials
├── fix_ipo_signals.py               ← one-time cleanup script (already created, not yet run)
├── fix_news_signals.py              ← one-time fix script
├── fix_csuite_sources.py            ← one-time fix script
│
├── tracker/
│   ├── csv_loader.py                ← reads apollo-accounts-export.csv
│   ├── sheets_client.py             ← Google Sheets API (reads signal sheets)
│   ├── news_client.py               ← Google News RSS (249 lines)
│   ├── snapshot_store.py            ← SQLite (383 lines)
│   ├── change_detector.py           ← 22 signal checks (503 lines)
│   ├── dashboard_builder.py         ← generates dashboard.html (2,916 lines)
│   ├── notifier_slack.py            ← Slack webhook notifications
│   └── notifier_sheets.py
│
├── data/
│   └── tracker.db                   ← SQLite DB (CIFS-mounted — NEVER write from bash sandbox)
│
└── reports/
    └── dashboard.html               ← auto-generated, git-pushed to Railway
```

---

## CRITICAL CONSTRAINT — DB SAFETY

**NEVER write to `tracker.db` from the bash sandbox / Claude shell tools.**  
The DB lives on a CIFS/network mount. The bash sandbox holds a stale cached copy. Writing from it corrupts the live DB. All DB changes must be made by running Python scripts locally (e.g. `python fix_ipo_signals.py`).

SQLite is configured with `journal_mode=DELETE` (not WAL) because WAL is incompatible with CIFS mounts.

---

## CLI FLAGS

```bash
python main.py --sheets-only      # Refresh HIGH signals from Google Sheets only (skip News RSS)
python main.py --news-only        # Refresh Google News RSS only (skip Sheets)
python main.py --dashboard-only   # Rebuild dashboard.html from DB without any API calls
python main.py --reset-alerts     # Clear dedup history (use before re-running to fix wrong dates)
python main.py --dry-run          # No DB writes, print only
python main.py --company "NAME"   # Process single company
python main.py --enrich-sample    # Process first 10 companies only
```

---

## SIGNAL ARCHITECTURE

### HIGH signals (from Google Sheets — user manually maintains)
1. **Funding Round** — `google_sheets.funding_sheet_id` → tab "Funding signals"
2. **C-Suite Join / Exit** — `google_sheets.csuite_sheet_id` → tab "C-suite signals"
3. **Acquisition / M&A** — `google_sheets.ma_sheet_id`
4. **IPO Signal** — `google_sheets.ipo_sheet_id`
5. **Subsidiary Change** — `google_sheets.subsidiary_sheet_id`

### LOW signals (from Google News RSS — automatic)
22. **News Mention** — any news article that doesn't match HIGH/MEDIUM keyword patterns

> M&A and IPO are intentionally **not** detected from News RSS (removed to avoid false positives). They only come from Google Sheets.

---

## GOOGLE SHEETS COLUMN LAYOUT

**Funding sheet** (`funding_tab: "Funding signals"`):  
`Company Name | Domain | Funding Stage | Funding Amount (USD) | Raised At | Announcement Date | Signal Type | Source Name | Source URL | Confidence | Summary | Lead Investor`

**C-Suite sheet** (`csuite_tab: "C-suite signals"`):  
`Company Name | Domain | Person Name | Title | Action | Start Date | LinkedIn URL | Notes`  
- Action can be: `Join`, `Exit`, `Appointed`, `Promoted`, `Departed`, `Left`, `Resigned`
- Notes format: `Source: Becker's Hospital Review, https://... / Confidence: High / Summary: ...`

**Signal Type routing map** (normalised Signal Type column → internal type):
```python
"public_offering"  → ("Funding Round", "HIGH")   # shelf/ATM for already-public companies
"spac"             → ("IPO Signal",    "HIGH")
"ipo"              → ("IPO Signal",    "HIGH")
"acquisition"      → ("Acquisition / M&A", "HIGH")
"funding" / "series_a/b/c/d" / "seed" / "grant" / "debt" → ("Funding Round", "HIGH")
"c_suite_join" / "executive_hire"   → ("C-Suite Join", "HIGH")
"c_suite_exit" / "executive_depart" → ("C-Suite Exit", "HIGH")
```

---

## SQLITE SCHEMA

```sql
-- companies table
apollo_id TEXT PK, name, domain, industry, city, state, first_seen, last_enriched, is_active

-- snapshots table (one row per run per company)
id, apollo_id, snapshot_date, employees, annual_revenue, total_funding,
latest_funding_type, latest_funding_amount, last_raised_at, hq_city, hq_state,
tech_stack (JSON), leadership_json (JSON), open_job_count,
intent_score_1, intent_topic_1, intent_score_2, intent_topic_2,
crm_stage, retail_locations, subsidiary_of, raw_json

-- alerts_sent table (one row per signal fired)
id, apollo_id, signal_type, signal_detail, severity, sent_at,
signal_date,     ← actual event date (Announcement Date / article pub date)
source_url,      ← encoded as "Name||URL" or plain URL
dry_run

-- weekly_runs table
id, run_date, companies_checked, signals_high, signals_medium, signals_low, duration_seconds
```

---

## KEY DATA CONCEPTS

### `signal_date` vs `sent_at`
- `signal_date` = actual event date (Announcement Date from sheet, or article publication date from RSS)
- `sent_at` = when the tracker recorded/detected the signal
- Dashboard displays `signal_date` for the date shown on each signal card

### Source URL encoding
- Stored as `"Source Name||https://url.com"` — the `||` separator lets JS decode both label and link
- JS function `_parseStoredSource(raw)` splits on `||` → `{ name, url }`
- Fallback: if no `||`, treat as plain URL if starts with `http`

### Dedup window
- `was_alert_sent_recently(apollo_id, signal_type, 7_days, signal_detail=headline)`
- Matches on (apollo_id + signal_type + headline) within 7-day window
- Use `--reset-alerts` to clear and repopulate with correct dates

### First run behaviour
- Fresh DB → no snapshots → first run saves baselines only, zero signals fired
- Second run detects changes vs baselines
- `config.yaml: force_csv_baseline: false` (auto-set; was `true` only on very first ever run)

---

## DASHBOARD ARCHITECTURE (`tracker/dashboard_builder.py` — 2,916 lines)

Single self-contained HTML file with all company data embedded as JSON. Dark theme. Pure HTML/CSS/JS — no server needed.

### Company Modal — 5 tabs
```javascript
renderModalTab(c, idx):
  idx === 0 → renderOverviewTab(c)    // employees, revenue, funding, tech stack, intent
  idx === 1 → renderSignalsTab(c)     // chronological signal history
  idx === 2 → renderLeadershipTab(c)  // C-suite people with LinkedIn
  idx === 3 → renderTechTab(c)        // tech stack categorised badges
  idx === 4 → renderNewsTab(c)        // news article cards ← recently implemented
```

### `renderNewsTab(c)` — just implemented
Filters `c.alerts` for `signal_type === 'News Mention'`. Shows article cards with:
- Headline (strips "In the news: " prefix from `signal_detail`)
- Publication date (`signal_date`) formatted via `formatFullDate()` + `relTime()`
- "Read article" link using decoded URL from `_parseStoredSource(source_url)`
- Empty state: "No news signals yet. Run `--news-only` to fetch the latest news."

### Date display fix
`formatFullDate()` and `relTime()` append `T00:00:00` to date-only strings (`YYYY-MM-DD`) before parsing, to avoid UTC midnight being interpreted as IST "5:30 AM" artifact.

### JS helper — `_parseStoredSource(raw)`
```javascript
// Parses "Name||URL" → { name, url }
// Falls back to { name: raw, url: null } if no ||
```

---

## KEY FILE: `tracker/news_client.py`

### `_decode_google_news_url(google_url)`
Pure base64 decode — NO HTTP requests. Extracts real article URL from `https://news.google.com/rss/articles/CBMi...` redirect links. Called in `_rss_articles()` and `get_leadership_from_news()`. Falls back to original URL on failure.

### Constants
```python
MAX_NEWS_AGE_DAYS = 90   # articles older than 90 days are ignored
```

---

## PENDING TASKS (not yet run locally)

1. **`python main.py --reset-alerts --sheets-only`** — repopulates DB with correct Announcement Dates (old records had `sent_at` as the date instead of actual event date)

2. **`python main.py --news-only`** — fetches fresh news articles, stores real decoded URLs, populates News Mention signals so the new News tab shows content

3. **`python fix_ipo_signals.py`** — deletes 5 false IPO records for already-public companies:
   - Nutex Health, Karyopharm Therapeutics, AngioDynamics, Omada Health, Guardian Flight

---

## RECENT CHANGES (last 2 sessions)

| What | Where | Status |
|---|---|---|
| `renderNewsTab(c)` function added — shows News Mention signals in News tab | `dashboard_builder.py` ~line 2480 | ✅ Done |
| `renderModalTab` wired to call `renderNewsTab(c)` instead of static placeholder | `dashboard_builder.py` ~line 2477 | ✅ Done |
| `PUBLIC_OFFERING` signal type routed to "Funding Round" (not "IPO Signal") | `main.py` `_SIGNAL_TYPE_MAP` | ✅ Done |
| `signal_date` field added to `ChangeEvent` dataclass | `change_detector.py` | ✅ Done |
| `signal_date` passed through `ev()` helper and `record_alert()` | `main.py`, `snapshot_store.py` | ✅ Done |
| `event_date` separated from `date` — Announcement Date shown, not Scan Date | `main.py` all sheet sections | ✅ Done |
| `_decode_google_news_url()` added (base64 decode, no HTTP) | `news_client.py` | ✅ Done |
| News article pub date stored as `signal_date` | `change_detector.py` | ✅ Done |
| C-suite `source_url` backfill — overwrites LinkedIn URL with better source if available | `main.py`, `snapshot_store.py` | ✅ Done |
| M&A + IPO removed from News RSS detection (Google Sheets only) | `change_detector.py` | ✅ Done |
| `fix_ipo_signals.py` created to delete 5 false IPO records | root folder | ✅ Created, not yet run |
| Date-only UTC parse bug fixed (appends `T00:00:00`) | `dashboard_builder.py` `formatFullDate()`/`relTime()` | ✅ Done |

---

## GOOGLE SHEETS IDs (from config.yaml)

```yaml
funding_sheet_id:  "1nhu07HCyctjs5gfGdchu_n7Rxnpip0xVMwB9nIF_wa0"
csuite_sheet_id:   "16M_DLwIhbKuQAv_Cafxl8krrNWO5iaTrBJdQaN9EI6g"
ma_sheet_id:       ""   # not yet set up
ipo_sheet_id:      ""   # not yet set up
subsidiary_sheet_id: "" # not yet set up
funding_tab:  "Funding signals"
csuite_tab:   "C-suite signals"
```

---

## DEPLOYMENT

- **Platform:** Railway
- **Auto-deploy:** git push → Railway rebuilds
- **Dashboard URL:** `https://company-signal-tracker-production.up.railway.app`
- **Dashboard served by:** `app.py` (Flask) serving `reports/dashboard.html`
- After each run, `dashboard.html` is rebuilt → git-pushed → Railway serves updated version

---

## ENVIRONMENT

- Python 3.14 (local), SQLite with `journal_mode=DELETE`
- Libraries: `feedparser`, `google-auth`, `google-api-python-client`, `typer`, `rich`, `pyyaml`, `requests`
- Folder path: `C:\Users\krishna.l\company-signal-tracker\`
- Bash sandbox mounts at: `/sessions/.../mnt/company-signal-tracker/`
- **REMINDER:** Never write DB from bash. Read is OK.

---

## HOW TO CONTINUE

To work on the dashboard HTML/JS, Claude should `Read` the file at:
`C:\Users\krishna.l\company-signal-tracker\tracker\dashboard_builder.py`

To work on signal logic, read `main.py` and `tracker/change_detector.py`.

For the pending runs, the user needs to execute these **locally** (not via Claude bash):
```bash
python main.py --reset-alerts --sheets-only
python main.py --news-only
python fix_ipo_signals.py
```
