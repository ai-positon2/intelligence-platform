# Claude Code Prompt — Company Signal Tracker (CSV-Based)

Paste everything below this line into Claude Code:

---

## TASK

Rebuild the company signal tracker from scratch using a provided CSV file as the company source instead of Apollo search. The CSV file is called `apollo-accounts-export.csv` and is already in the project folder.

---

## CSV COLUMN MAPPING

The CSV has these exact columns — map them precisely:

| CSV Column | Internal Field |
|---|---|
| Company Name | name |
| # Employees | employees |
| Industry | industry |
| Website | domain |
| Company Linkedin Url | linkedin_url |
| Company City | city |
| Company State | state |
| Company Country | country |
| Keywords | keywords |
| Technologies | tech_stack |
| Total Funding | total_funding |
| Latest Funding | latest_funding_type |
| Latest Funding Amount | latest_funding_amount |
| Last Raised At | last_raised_at |
| Annual Revenue | annual_revenue |
| Apollo Account Id | apollo_id |
| Short Description | description |
| Founded Year | founded_year |
| Primary Intent Topic | intent_topic_1 |
| Primary Intent Score | intent_score_1 |
| Secondary Intent Topic | intent_topic_2 |
| Secondary Intent Score | intent_score_2 |
| Account Stage | crm_stage |
| Technologies | tech_stack |
| SIC Codes | sic_codes |
| NAICS Codes | naics_codes |
| Logo Url | logo_url |
| Twitter Url | twitter_url |
| Facebook Url | facebook_url |
| Number of Retail Locations | retail_locations |
| Subsidiary of | subsidiary_of |

---

## TRACKING PARAMETERS

Track ALL of the following signals for every company on every weekly run. Compare new data against the previous week's snapshot stored in SQLite.

### TIER 1 — HIGH SEVERITY SIGNALS
These trigger immediate Slack alerts:

1. **C-Suite Join** — A new person with title containing CEO/CFO/CTO/COO/CMO/CRO/CPO/President/EVP/SVP joined the company (detected via Apollo people enrichment)
2. **C-Suite Exit** — A previously tracked executive is no longer at the company
3. **Funding Round** — `latest_funding_type` or `latest_funding_amount` or `last_raised_at` changed
4. **Acquisition / M&A** — News article contains "acquired", "merger", "acquisition", "acquires" for this company
5. **IPO Signal** — News contains "IPO", "going public", "S-1 filing"
6. **Subsidiary Change** — `subsidiary_of` field changes (company got acquired or spun off)

### TIER 2 — MEDIUM SEVERITY SIGNALS
These trigger Slack alerts and are logged to dashboard:

7. **Headcount Growth** — Employee count grew by 15%+ since last snapshot
8. **Headcount Shrink** — Employee count dropped by 10%+ since last snapshot
9. **Revenue Growth** — Annual revenue increased since last snapshot
10. **New Location** — City or state changed or expanded
11. **Job Posting Spike** — Open roles increased by 30%+ (via Apollo job postings endpoint)
12. **New Funding Stage** — Company moved from e.g. Series A to Series B
13. **Tech Stack Addition** — A new major technology added (e.g. added Salesforce, HubSpot, Marketo)
14. **Tech Stack Removal** — A key technology removed from stack
15. **Intent Score Spike** — Primary intent score increased by 20+ points since last snapshot
16. **Intent Topic Change** — Primary intent topic changed entirely

### TIER 3 — LOW SEVERITY SIGNALS
These are logged to dashboard only (no Slack unless configured):

17. **Title Change** — A tracked executive changed title (promotion/lateral)
18. **CRM Stage Change** — Account Stage field changes
19. **New Retail Location** — Number of retail locations increased
20. **Website Change** — Domain/website URL changed
21. **Description Update** — Short description meaningfully changed
22. **News Mention** — Company mentioned in news without a HIGH signal keyword

---

## FILE STRUCTURE

```
company-signal-tracker/
├── apollo-accounts-export.csv     ← input company list
├── main.py                        ← orchestrator
├── config.yaml                    ← all settings
├── requirements.txt
│
├── tracker/
│   ├── csv_loader.py              ← reads and parses the CSV
│   ├── apollo_client.py           ← enrichment + people + jobs endpoints
│   ├── news_client.py             ← Google News RSS per company
│   ├── snapshot_store.py          ← SQLite snapshots + alerts_sent
│   ├── change_detector.py         ← all 22 signal checks
│   ├── notifier_slack.py          ← Gmail SMTP → Slack channel email
│   └── dashboard_builder.py       ← generates reports/dashboard.html
│
├── data/
│   └── tracker.db                 ← SQLite (gitignored)
│
└── reports/
    └── dashboard.html             ← auto-generated weekly
```

---

## DASHBOARD REQUIREMENTS

Build `reports/dashboard.html` as a **single self-contained HTML file** with an extraordinary UI. Use Chart.js from CDN for charts. No frameworks — pure HTML, CSS, JavaScript inline. Dark theme.

### DESIGN SPEC

**Color Palette:**
- Background: `#0a0e1a`
- Card background: `#111827`
- Border: `#1f2937`
- Accent blue: `#3b82f6`
- Accent purple: `#8b5cf6`
- HIGH signal: `#ef4444`
- MEDIUM signal: `#f59e0b`
- LOW signal: `#6b7280`
- Text primary: `#f9fafb`
- Text secondary: `#9ca3af`
- Green: `#10b981`

**Typography:** Use Inter font from Google Fonts

**Layout:** Full-width, responsive, sidebar + main content

### SECTION 1 — TOP NAVIGATION BAR
- Logo/title: "Signal Tracker" with a pulse animation dot
- Last updated timestamp
- Total companies tracked count
- "Run Now" button (triggers `python main.py` via a note that this is manual)
- Search bar that filters the company table live

### SECTION 2 — KPI CARDS ROW (5 cards)
Animated number cards with icons:
1. **Companies Tracked** — total count with industry breakdown tooltip
2. **Signals This Week** — total with HIGH/MEDIUM/LOW breakdown
3. **HIGH Alerts** — count in red with flame icon
4. **Companies Hiring** — count where job posting spike detected
5. **Funding Activity** — count of companies with recent funding changes

Each card has a sparkline showing 8-week trend.

### SECTION 3 — SIGNAL FEED (left side, 40% width)
A live-feed style list of all signals from latest run, sorted HIGH → MEDIUM → LOW:

Each signal card shows:
- Severity badge (RED/YELLOW/GREY pill)
- Company logo (from Logo Url field) or initials avatar if no logo
- Company name (bold)
- Signal type (e.g. "C-Suite Join")
- One-line description (e.g. "Jane Doe joined as CFO")
- Time detected
- Quick action links: [LinkedIn] [Website] [Apollo]

Hovering a signal card highlights the company row in the table on the right.

### SECTION 4 — CHARTS ROW
Three charts side by side:

**Chart 1 — Weekly Signal Trend (Line chart)**
- X axis: last 8 weeks
- Y axis: signal count
- Three lines: HIGH (red), MEDIUM (amber), LOW (grey)
- Smooth curves, filled area under HIGH line

**Chart 2 — Signal Type Breakdown (Donut chart)**
- Each signal type as a segment
- Hover shows count + percentage
- Legend on the right

**Chart 3 — Top Industries by Signal (Horizontal bar chart)**
- Industries on Y axis
- Signal count on X axis
- Color gradient from blue to purple

### SECTION 5 — COMPANY ROSTER TABLE (full width)
A powerful sortable, filterable data table:

**Columns:**
| # | Logo | Company | Industry | Location | Employees | Revenue | Funding Stage | Last Signal | Signal Count | Intent Score | Tech Stack | Actions |

**Features:**
- Sticky header
- Click any column header to sort
- Filter bar above table: search by name, filter by state/industry/signal type
- Row color coding: red tint for HIGH signal companies, amber tint for MEDIUM
- Expandable rows — click a company to expand and show:
  - Full signal history for that company
  - All tracked executives with LinkedIn links
  - Tech stack as pill badges
  - Keywords as tags
  - Short description
  - All funding history
- Pagination: 25 rows per page with page controls
- "Export filtered view" button that downloads visible rows as CSV

### SECTION 6 — COMPANY DETAIL MODAL
When clicking a company name, open a full-screen modal with:

**Left panel:**
- Company logo (large)
- Name, website, LinkedIn, Twitter buttons
- Founded year, HQ location
- Short description
- Keywords as tag cloud

**Right panel tabs:**
- **Overview**: employees, revenue, funding stage, tech stack, intent scores
- **Signals**: full chronological signal history with dates
- **Leadership**: all tracked C-suite people with photo/title/LinkedIn
- **Tech Stack**: all technologies as categorised badges (CRM, Marketing, Infrastructure, etc.)
- **News**: last 5 news articles fetched for this company

---

## `config.yaml` STRUCTURE

```yaml
input:
  csv_file: "apollo-accounts-export.csv"
  batch_size: 50                    # Process 50 companies per run to manage credits

signals:
  headcount_growth_pct: 15
  headcount_shrink_pct: 10
  job_posting_spike_pct: 30
  intent_score_spike_points: 20
  c_suite_titles:
    - "Chief"
    - "CEO"
    - "COO"
    - "CFO"
    - "CTO"
    - "CMO"
    - "CRO"
    - "CPO"
    - "President"
    - "EVP"
    - "SVP"
    - "Vice President"
    - "Managing Director"
    - "General Counsel"
  funding_keywords:
    - "Series A"
    - "Series B"
    - "Series C"
    - "Series D"
    - "Seed"
    - "Debt Financing"
    - "IPO"
    - "Private Equity"
  news_high_keywords:
    - "acquired"
    - "acquisition"
    - "merger"
    - "IPO"
    - "going public"
    - "S-1"
    - "layoffs"
    - "bankruptcy"
  news_medium_keywords:
    - "funding"
    - "raises"
    - "expansion"
    - "new office"
    - "appoints"
    - "names"
    - "hires"
    - "partnership"

credentials:
  apollo_api_key: "YOUR_APOLLO_API_KEY"
  slack_channel_email: "YOUR_SLACK_CHANNEL_EMAIL"
  smtp_sender_email: "YOUR_GMAIL"
  smtp_app_password: "YOUR_APP_PASSWORD"
  serpapi_key: ""

behaviour:
  dry_run: false
  enrich_people: true
  enrich_jobs: true
  enrich_news: true
  max_people_per_company: 10
  dedup_window_days: 7
  batch_size: 50

schedule:
  run_day: "monday"
  run_time_utc: "08:00"
```

---

## `main.py` PIPELINE

```
def run(dry_run, enrich_sample, batch):

  STEP 1 — Load config.yaml
  STEP 2 — Initialize SQLite DB
  STEP 3 — Load companies from apollo-accounts-export.csv via csv_loader.py
            Print: "Loaded X companies from CSV"

  STEP 4 — For each company (in batches of config.batch_size):
    a. Load previous snapshot from SQLite
    b. If Apollo ID exists: call apollo_client.enrich_company(apollo_id)
       Else: call apollo_client.enrich_by_domain(domain)
    c. If enrich_people: call apollo_client.get_leadership(apollo_id)
    d. If enrich_jobs: call apollo_client.get_job_postings(apollo_id)
    e. If enrich_news: call news_client.get_news(company_name)
    f. Also compare CSV fields directly (intent scores, funding, tech stack)
       against previous snapshot — no API call needed for these
    g. Save new snapshot
    h. Run change_detector.detect_all_signals(old, new, config)
    i. For each signal: send Slack alert + record in SQLite
    j. Sleep 0.3s between companies

  STEP 5 — Generate dashboard.html
  STEP 6 — Send weekly summary to Slack
  STEP 7 — Print run summary to terminal

CLI flags:
  --dry-run          No Slack, no DB writes, print only
  --enrich-sample    Only process first 10 companies
  --batch N          Process next N companies (for manual batching)
  --company NAME     Process single company by name
  --dashboard-only   Regenerate dashboard from existing DB without API calls
  --reset-alerts     Clear dedup history
```

---

## SQLITE SCHEMA

```sql
CREATE TABLE companies (
  apollo_id TEXT PRIMARY KEY,
  name TEXT,
  domain TEXT,
  industry TEXT,
  city TEXT,
  state TEXT,
  first_seen TEXT,
  last_enriched TEXT,
  is_active INTEGER DEFAULT 1
);

CREATE TABLE snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  apollo_id TEXT,
  snapshot_date TEXT,
  employees INTEGER,
  annual_revenue REAL,
  total_funding REAL,
  latest_funding_type TEXT,
  latest_funding_amount REAL,
  last_raised_at TEXT,
  hq_city TEXT,
  hq_state TEXT,
  tech_stack TEXT,          -- JSON array
  leadership_json TEXT,     -- JSON array of {id, name, title, linkedin}
  open_job_count INTEGER,
  intent_score_1 REAL,
  intent_topic_1 TEXT,
  intent_score_2 REAL,
  intent_topic_2 TEXT,
  crm_stage TEXT,
  retail_locations INTEGER,
  raw_json TEXT
);

CREATE TABLE alerts_sent (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  apollo_id TEXT,
  signal_type TEXT,
  signal_detail TEXT,
  severity TEXT,
  sent_at TEXT,
  dry_run INTEGER DEFAULT 0
);

CREATE TABLE weekly_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT,
  companies_checked INTEGER,
  signals_high INTEGER,
  signals_medium INTEGER,
  signals_low INTEGER,
  duration_seconds REAL
);
```

---

## REQUIREMENTS

```
requests>=2.31.0
pyyaml>=6.0
feedparser>=6.0.11
typer>=0.12.0
rich>=13.7.0
pytest>=8.0.0
pandas>=2.0.0
```

Note: No gspread or google-auth needed since we are skipping Google Sheets for now.

---

## IMPORTANT IMPLEMENTATION NOTES

1. The CSV already contains a lot of baseline data — use it directly for the first snapshot WITHOUT making any Apollo API call. Only call Apollo on subsequent runs to detect changes.

2. This means the first run should: read CSV → save as baseline snapshots → generate dashboard → send summary. Zero Apollo credits used on first run.

3. From the second run onwards, enrich each company via Apollo to detect changes vs the CSV baseline.

4. The `Keywords` column in the CSV is extremely long — truncate to first 500 characters when storing, but use full text for signal detection.

5. The `Technologies` column is comma-separated — parse into a proper JSON array when storing.

6. Companies without an Apollo Account Id should still be tracked — use domain-based enrichment as fallback.

7. The dashboard must work by simply opening `reports/dashboard.html` in any browser — no server, no dependencies, fully self-contained with all data embedded as JSON inside the HTML file itself.

8. Add a `--dashboard-only` flag that rebuilds the dashboard from existing SQLite data without making any API calls — useful for refreshing the UI without spending credits.
