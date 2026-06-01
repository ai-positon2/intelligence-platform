# Company Signal Tracker — Full Context for New Chat (v2 — May 28 2026)

Paste this entire file at the start of a new chat to continue work without losing context.

---

## WHAT THIS PROJECT IS

Two parallel signal-tracker dashboards served by a single Flask app on Railway:

1. **Healthcare Dashboard** — 1,251 healthcare companies from an Apollo CSV export. Tracks funding rounds, C-suite changes, M&A, IPO, news mentions. HIGH signals come from user-maintained Google Sheets; LOW signals from Google News RSS.

2. **CSG Dashboard** — ~292 companies from `CSG-company-list.xlsx`. Same signal types. Signals seeded manually via `seed_csg_signals.py` and news fetched via `fetch_csg_news.py`. Has its own separate DB and dashboard HTML.

**Live URL:** `https://web-production-068e7.up.railway.app`
**Login:** `krishna.ladha@position2.com` / `signals@P2`
**Local folder:** `C:\Users\krishna.l\company-signal-tracker\`
**Git remote:** `https://github.com/krishnaladha/company-signal-tracker`

> **IMPORTANT DNS NOTE:** The `.railway.app` domain sometimes fails to resolve from the user's corporate network. Use the URL above (`web-production-068e7.up.railway.app`). If it shows DNS_PROBE_FINISHED_NXDOMAIN, run `ipconfig /flushdns` or try on a different network/hotspot.

---

## FULL ARCHITECTURE

```
company-signal-tracker/
├── app.py                           ← Flask server (serves both dashboards, login, health)
├── main.py                          ← Healthcare orchestrator (665 lines)
├── build_csg_dashboard.py           ← CSG dashboard builder (reads XLSX → writes dashboard_csg.html)
├── fetch_csg_news.py                ← CSG news fetcher (Google News RSS → tracker_csg_v2.db)
├── seed_csg_signals.py              ← CSG signal seeder (KNOWN_SIGNALS → tracker_csg_v2.db)
├── fix_ipo_signals.py               ← One-time cleanup: deletes 5 false IPO records (not yet run)
├── fix_news_signals.py              ← One-time fix script
├── fix_csuite_sources.py            ← One-time fix script
├── config.yaml                      ← All settings, sheet IDs, credentials (gitignored)
├── service_account.json             ← Google service account (gitignored)
├── apollo-accounts-export.csv       ← Healthcare master company list (1,251 companies)
├── CSG-company-list.xlsx            ← CSG master company list (~292 companies)
├── railway.toml                     ← Railway deploy config (gunicorn app:app)
│
├── tracker/
│   ├── dashboard_builder.py         ← HTML generator for BOTH dashboards (~3,400 lines)
│   ├── snapshot_store.py            ← SQLite read/write (383 lines)
│   ├── change_detector.py           ← 22 signal checks
│   ├── csv_loader.py                ← Reads apollo-accounts-export.csv
│   ├── sheets_client.py             ← Google Sheets API
│   ├── news_client.py               ← Google News RSS + URL decoder
│   ├── notifier_slack.py            ← Slack webhook
│   └── notifier_sheets.py
│
├── data/
│   ├── tracker.db                   ← Healthcare SQLite DB (CIFS-mounted — NEVER write from bash)
│   └── tracker_csg_v2.db           ← CSG SQLite DB (active — NEVER write from bash)
│   (tracker_corrupt_20260521_124645.bak — 158 MB, gitignored via data/*.bak)
│
└── reports/
    ├── dashboard.html               ← Healthcare dashboard (auto-generated)
    └── dashboard_csg.html           ← CSG dashboard (auto-generated, 3,450 lines)
```

---

## CRITICAL CONSTRAINTS — READ FIRST

### 1. NEVER write to SQLite DB from bash sandbox
The DBs live on a CIFS/network mount. The bash sandbox holds a stale cached copy. Writing from bash corrupts the live DB. **All DB writes happen by running Python scripts locally in the user's PowerShell terminal.**

- Active CSG DB: `data/tracker_csg_v2.db`
- Active Healthcare DB: `data/tracker.db`
- Never use `data/tracker.db` for CSG work
- SQLite configured with `journal_mode=DELETE` (WAL is incompatible with CIFS)

### 2. Git lock files
When Claude writes files via sandbox, git operations sometimes fail with `index.lock` or `refs/remotes/origin/main.lock`. The user must clear these from PowerShell:
```powershell
Remove-Item .git\index.lock -Force -ErrorAction SilentlyContinue
Remove-Item .git\refs\remotes\origin\main.lock -Force -ErrorAction SilentlyContinue
```

### 3. Google News RSS — inaccessible from bash sandbox
Must use Claude's WebSearch tool for any news fetching in the sandbox. Actual RSS fetching happens locally via `fetch_csg_news.py`.

### 4. Do NOT commit large bak files
`data/tracker_corrupt_20260521_124645.bak` is 158 MB and must never be committed (exceeds GitHub 100 MB limit). It's in `.gitignore` under `data/*.bak`.

---

## APP.PY — FLASK SERVER

Two accounts registered:
```python
ACCOUNTS = {
    "healthcare": {
        "name": "Healthcare",
        "dashboard": Path(__file__).parent / "reports" / "dashboard.html",
    },
    "csg": {
        "name": "CSG",
        "dashboard": Path(__file__).parent / "reports" / "dashboard_csg.html",
    },
}
```

Routes:
- `GET /` → redirects to `/accounts`
- `GET /accounts` → account picker page
- `GET /dashboard/<account_id>` → serves dashboard HTML (with no-cache headers)
- `GET /login`, `POST /login` → auth (email + password)
- `GET /logout`
- `GET /health` → JSON health check (Railway uses this)
- `GET /api/weekly-stats[/<account_id>]` → JSON weekly stats

**No-cache headers** (added to prevent browser caching stale dashboards):
```python
response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
response.headers["Pragma"] = "no-cache"
response.headers["Expires"] = "0"
```

**Credentials:**
- Email: `krishna.ladha@position2.com`
- Password: `signals@P2`

---

## CSG DASHBOARD — COMPLETE PICTURE

### What it is
A self-contained HTML file (`reports/dashboard_csg.html`) with all ~292 company data embedded as JSON. Dark theme. Pure HTML/CSS/JS — no server-side rendering.

### How it's generated
```
CSG-company-list.xlsx
        ↓
build_csg_dashboard.py   ← reads XLSX, upserts companies to DB, calls dashboard_builder.py
        ↓
tracker/dashboard_builder.py  ← generates the full HTML with embedded DATA JSON
        ↓
reports/dashboard_csg.html   ← committed to git → Railway serves it
```

### How signals get into the CSG dashboard
Two sources:
1. `python seed_csg_signals.py` — inserts manually-researched signals from `KNOWN_SIGNALS` dict
2. `python fetch_csg_news.py` — fetches Google News RSS for all 292 companies, stores as "News Mention" signals

After either, run `python build_csg_dashboard.py` to regenerate the HTML, then push to git.

### Full refresh workflow (all run locally in PowerShell)
```powershell
python seed_csg_signals.py          # re-seed known signals (safe to re-run, dupes skipped)
python fetch_csg_news.py            # fetch latest news (takes ~6 min)
python build_csg_dashboard.py       # rebuild HTML from DB
git add -A
git commit -m "Update CSG dashboard"
git push
```

### CSG DB path
`data/tracker_csg_v2.db` — never use any other DB for CSG work.

---

## DASHBOARD BUILDER (`tracker/dashboard_builder.py`)

Single function `build_dashboard(companies_from_csv, store, output_path, ...)` generates a 3,400+ line self-contained HTML file. Both dashboards use this same builder.

### Company Modal — 5 tabs
```
Tab 0: Overview     → employees, revenue, funding, tech stack, intent scores
Tab 1: Signals      → chronological signal history (all signal types)
Tab 2: Leadership   → C-suite people with LinkedIn links
Tab 3: Tech Stack   → categorised tech badges
Tab 4: News         → News Mention signals as article cards
```

### News Tab (`renderNewsTab`)
- Filters company alerts for `signal_type === 'News Mention'`
- Shows: headline (strips "In the news: " prefix), pub date, "Read article" link
- URL decoded from `source_url` via `_parseStoredSource(raw)` → splits on `||`
- Empty state if no news yet

### JS helpers
```javascript
// Parse stored source URL format: "Source Name||https://url.com"
_parseStoredSource(raw) → { name, url }

// Fix UTC midnight parsing bug (date-only strings → append T00:00:00)
formatFullDate(dateStr)   // e.g. "May 27, 2026"
relTime(dateStr)          // e.g. "2 days ago"
```

### KPI cards (top of dashboard)
- Total Companies, HIGH Alerts, C-Suite Changes, Funding Rounds, M&A, IPO Signals, News Mentions
- Clicking each opens a KPI modal with a filterable list

---

## ANIMATION SYSTEM (v1 + v2) — CURRENT STATE

Both `tracker/dashboard_builder.py` and `reports/dashboard_csg.html` have full animation code injected.

### CSS Animation Layer v2 (injected before `</style>`, around line 672–902 of dashboard_csg.html)
Keyframes and rules for:
- Scroll progress bar (`#scroll-progress`)
- Scroll-to-top button (`#scroll-top-btn`)
- Toast notification system (`.toast`, `.toast-out`, `#toast-container`)
- HIGH severity glow (`glowPulse`)
- Sidebar nav stagger entrance (`.nav-item:nth-child(n)`)
- Section header underline slide
- Panel lift on hover (`.panel`, `.kpi-card`)
- Badge bounce, avatar morph
- KPI modal row accent
- Signal link underline
- Row click highlight (`.row-selected`)
- 3D tilt host (`.kpi-card { transform-style: preserve-3d }`)
- Expand/chevron rotation (`.expand-btn.ev2-open`)
- Skeleton shimmer
- Copy tooltip
- Sticky header shadow
- 30+ keyframes total: `toastIn`, `toastOut`, `float`, `fadeInUp`, `sidebarItem`, `glowPulse`, `highlightPulse`, `breathe`, `shimmerMove`, `popIn`, `subtleWiggle`, `progressGlow`, `countDigit`, `expandIn`, `drawLine`, etc.

### JS Animation Engine v1 (DOMContentLoaded block, ~200 lines)
- Count-up animation on KPI numbers (IntersectionObserver)
- Ripple effect on buttons (material-style)
- Stagger entrance on signal cards
- Patches `window.copyCmd`, `window.showSection`, `window.renderTable`

### JS Animation Engine v2 (sections A–O, ~330 lines, after v1)
```
A — Scroll progress bar + scroll-to-top button (DOM-injected)
B — window._showToast(msg, icon, durationMs) + copyCmd toast wrap
C — 3D perspective tilt on KPI cards (mousemove → rotateX/rotateY)
D — Magnetic button effect (mouse-tracking translate)
E — Table row click highlight (classList.add('row-selected'))
F — Section crossfade (wraps window.showSection)
G — Search cell highlight (wraps window.renderTable, adds tbl-match class)
H — Chevron rotation (toggles ev2-open on expand-btn)
I — Logo shimmer on hover
J — Badge breathing animation (pulse-ring)
K — Sticky header shadow on scroll
L — Filter/export toast notifications
M — KPI number MutationObserver (re-animates on data change)
N — (reserved)
O — DOMContentLoaded: logo wiggle, refresh btn pulse, welcome toast after 1200ms
```

**Key welcome toast:**
```javascript
setTimeout(function() {
  window._showToast && window._showToast('Dashboard ready', '✅', 2000);
}, 1200);
```
If you see this toast pop up bottom-right after page load, the animation system is working.

---

## CURRENT GIT STATE

Latest commits on `origin/main`:
```
e6e92fe Fix: restore truncated JS tail in dashboard_csg.html  ← LATEST (deployed)
ff2b340 Update CSG dashboard
a15f055 Fix: disable browser caching on dashboard routes
9190532 Animation v2: 3D tilt, magnetic, toasts, scroll bar, crossfade, search highlight
090946e Add JS animation engine to dashboard_builder.py
bdae9b5 Fix refresh modal scroll on CSG dashboard
```

### dashboard_csg.html current state
- 3,450 lines, ~1.36 MB
- **Complete and valid** — was truncated at 2,862 lines in `ff2b340` (Python script crashed mid-generation), fixed in `e6e92fe` by stitching the missing 588-line JS tail from `9190532`
- Animation CSS at lines ~672–902
- Main JS at lines ~903–2,862 (data + all app logic)
- Animation v2 JS at lines ~2,863–3,449
- Closes properly: `</script>`, `</body>`, `</html>` at the end

---

## RAILWAY DEPLOYMENT

- **Platform:** Railway (paid, ~$4.62 left / 20 days as of May 28)
- **Service name:** "web" (NOT "company-signal-tracker" — that's the source service)
- **Live URL:** `https://web-production-068e7.up.railway.app`
- **Region:** US West, 1 Replica
- **Start command:** `gunicorn app:app --bind 0.0.0.0:$PORT`
- **Health check:** `GET /health` (timeout 30s, restart on failure)
- **Auto-deploy:** git push to `origin/main` → Railway auto-rebuilds in ~60s

**IMPORTANT:** The domain `company-signal-tracker-production.up.railway.app` does NOT work — it's the source repo service, not the web server. Always use `web-production-068e7.up.railway.app`.

---

## HEALTHCARE DASHBOARD — SIGNAL ARCHITECTURE

### HIGH signals (Google Sheets — user manually maintains)
1. **Funding Round** — `funding_sheet_id` → tab "Funding signals"
2. **C-Suite Join / Exit** — `csuite_sheet_id` → tab "C-suite signals"
3. **Acquisition / M&A** — `ma_sheet_id`
4. **IPO Signal** — `ipo_sheet_id`
5. **Subsidiary Change** — `subsidiary_sheet_id`

### LOW signals (Google News RSS — automatic)
- **News Mention** — articles not matching HIGH/MEDIUM keyword patterns

M&A and IPO are intentionally NOT detected from News RSS (only from Sheets).

### CLI flags
```bash
python main.py --sheets-only      # Refresh HIGH signals from Sheets only
python main.py --news-only        # Refresh Google News RSS only
python main.py --dashboard-only   # Rebuild dashboard.html from DB (no API calls)
python main.py --reset-alerts     # Clear dedup history
python main.py --dry-run          # No DB writes, print only
python main.py --company "NAME"   # Single company
```

---

## SQLITE SCHEMA (both DBs share this schema)

```sql
-- companies
apollo_id TEXT PK, name, domain, industry, city, state, first_seen, last_enriched, is_active

-- snapshots (one per run per company)
id, apollo_id, snapshot_date, employees, annual_revenue, total_funding,
latest_funding_type, latest_funding_amount, last_raised_at,
hq_city, hq_state, tech_stack JSON, leadership_json JSON,
open_job_count, intent_score_1, intent_topic_1, intent_score_2, intent_topic_2,
crm_stage, retail_locations, subsidiary_of, raw_json

-- alerts_sent (one per signal fired)
id, apollo_id, signal_type, signal_detail, severity, sent_at,
signal_date,    ← actual event date (Announcement Date or article pub date)
source_url,     ← encoded as "Source Name||https://url.com" or plain URL
dry_run

-- weekly_runs
id, run_date, companies_checked, signals_high, signals_medium, signals_low, duration_seconds
```

### Key data concepts
- `signal_date` = actual event date; `sent_at` = when tracker recorded it
- `source_url` format: `"Name||https://url"` — split on `||` → label + link
- Dedup window: 7 days on (apollo_id + signal_type + headline)
- `_parseStoredSource(raw)` JS function handles the `||` split

---

## PENDING TASKS (not yet run locally)

1. **Healthcare:** `python main.py --reset-alerts --sheets-only` — repopulates DB with correct Announcement Dates (old records used `sent_at` as date instead of actual event date)

2. **Healthcare:** `python main.py --news-only` — fetch fresh news articles with real decoded URLs

3. **Healthcare:** `python fix_ipo_signals.py` — deletes 5 false IPO records for already-public companies: Nutex Health, Karyopharm Therapeutics, AngioDynamics, Omada Health, Guardian Flight

---

## ENVIRONMENT

- Python 3.14 (local), SQLite `journal_mode=DELETE`
- Libraries: `feedparser`, `google-auth`, `google-api-python-client`, `typer`, `rich`, `pyyaml`, `requests`, `flask`, `gunicorn`, `openpyxl`
- Local folder: `C:\Users\krishna.l\company-signal-tracker\`
- Bash sandbox mounts at: `/sessions/.../mnt/company-signal-tracker/`
- Bash sandbox uses a proxy that blocks `*.railway.app` — can't test live URL from sandbox

---

## HOW TO WORK ON THIS

### Read key files:
- `C:\Users\krishna.l\company-signal-tracker\tracker\dashboard_builder.py` — HTML generator
- `C:\Users\krishna.l\company-signal-tracker\reports\dashboard_csg.html` — live CSG HTML
- `C:\Users\krishna.l\company-signal-tracker\app.py` — Flask server
- `C:\Users\krishna.l\company-signal-tracker\main.py` — Healthcare signal orchestrator

### Deploy changes:
1. Edit files in bash sandbox (Claude writes via Edit/Write tools)
2. User runs from PowerShell:
   ```powershell
   cd C:\Users\krishna.l\company-signal-tracker
   Remove-Item .git\index.lock -Force -ErrorAction SilentlyContinue
   Remove-Item .git\refs\remotes\origin\main.lock -Force -ErrorAction SilentlyContinue
   git add -A
   git commit -m "Your message"
   git push origin main
   ```
3. Railway auto-redeploys in ~60 seconds

### Run CSG refresh (all local, no bash):
```powershell
cd C:\Users\krishna.l\company-signal-tracker
python seed_csg_signals.py
python fetch_csg_news.py
python build_csg_dashboard.py
git add -A && git commit -m "Update CSG dashboard" && git push
```

### Verify animations are working:
After loading the dashboard, look for the **"Dashboard ready ✅"** toast notification in the bottom-right corner after ~1 second. If you see it, the v2 animation JS is running correctly.
