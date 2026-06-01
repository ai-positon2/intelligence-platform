# Company Signal Tracker — Full Context for New Chat (v3 — May 29, 2026)

Paste this entire file at the start of a new chat to continue work without losing context.

---

## WHAT THIS PROJECT IS

Two parallel signal-tracker dashboards served by a single Flask app on Railway:

1. **Healthcare Dashboard** — 1,251 healthcare companies from an Apollo CSV export. Tracks funding rounds, C-suite changes, M&A, IPO, news mentions. HIGH signals come from user-maintained Google Sheets; LOW signals from Google News RSS.
2. **CSG Dashboard** — ~294 companies from `CSG-company-list.xlsx`. Same signal types. Signals seeded manually via `seed_csg_signals.py` and news fetched via `fetch_csg_news.py`. Has its own separate DB and dashboard HTML.

**Live URL:** `https://web-production-068e7.up.railway.app` (or `company-signal-tracker-production.up.railway.app` if a custom domain is wired up)
**Login:** `krishna.ladha@position2.com` / `signals@P2`
**Local folder:** `C:\Users\krishna.l\company-signal-tracker\`
**Git remote:** `https://github.com/krishnaladha/company-signal-tracker`

**Chrome-on-Position2 issue (still unresolved as of May 29):** The Railway domains DNS-fail in Chrome (both laptop and mobile on the user's managed Work profile). Likely cause is Chrome's secure DNS / managed-profile DNS-over-HTTPS provider blocking Railway, or Chrome's negative DNS cache poisoned by hitting the wrong URL earlier. **Workaround: use Firefox**, which works on the same network. Setup-time tried: `ipconfig /flushdns`, clearing host cache at `chrome://net-internals/#dns`, disabling secure DNS, HSTS clear, Guest mode — none worked.

---

## FULL ARCHITECTURE

```
company-signal-tracker/
├── app.py                           ← Flask server (serves both dashboards, login, health)
├── main.py                          ← Healthcare orchestrator (665 lines)
├── build_csg_dashboard.py           ← CSG dashboard builder (reads XLSX → writes dashboard_csg.html)
├── fetch_csg_news.py                ← CSG news fetcher (Google News RSS → tracker_csg_v2.db)
├── seed_csg_signals.py              ← CSG signal seeder (KNOWN_SIGNALS → tracker_csg_v2.db)
├── enrich_csg_from_apollo.py        ← (NEW v3) DB-level enrichment script for logos + employees + revenue
├── cleanup_cache.ps1                ← (NEW v3) PowerShell cleanup script for caches / orphans
├── fix_ipo_signals.py               ← One-time cleanup: deletes 5 false IPO records (not yet run)
├── fix_news_signals.py              ← One-time fix script
├── fix_csuite_sources.py            ← One-time fix script
├── config.yaml                      ← All settings, sheet IDs, credentials (gitignored)
├── service_account.json             ← Google service account (gitignored)
├── apollo-accounts-export.csv       ← Healthcare master company list (1,251 companies)
├── CSG-company-list.xlsx            ← CSG master company list (~294 companies)
├── railway.toml                     ← Railway deploy config (gunicorn app:app)
│
├── tracker/
│   ├── dashboard_builder.py         ← HTML generator for BOTH dashboards (~4,170 lines)
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
│   └── tracker_csg_v2.db            ← CSG SQLite DB (active — NEVER write from bash)
│
└── reports/
    ├── dashboard.html               ← Healthcare dashboard (~5.6 MB, 3,847 lines, CRLF)
    └── dashboard_csg.html           ← CSG dashboard (~1.4 MB, 3,847 lines, CRLF)
```

---

## CRITICAL CONSTRAINTS — READ FIRST

### 1. NEVER write to SQLite DB from bash sandbox

The DBs live on a CIFS/network mount. The bash sandbox holds a stale cached copy. Writing from bash corrupts the live DB. All DB writes happen by running Python scripts locally in the user's PowerShell terminal.

* Active CSG DB: `data/tracker_csg_v2.db`
* Active Healthcare DB: `data/tracker.db`
* SQLite configured with `journal_mode=DELETE` (WAL is incompatible with CIFS)

### 2. CIFS lag — bash sees stale view; file tools see truth

The bash sandbox's view of files in the user's mounted folder lags behind reality. When the file tools (Read, Edit, Write) modify a file, bash may still see the old version for some time. Always verify file integrity via the Read tool, not bash.

### 3. NEVER use `json.dumps` to re-serialize the embedded `DATA` blob

This was a hard-learned lesson. Doing `data = json.loads(...)` + edit + `json.dumps(data)` + write **corrupts the file** because:
- It changes CRLF → LF line endings on write
- It collapses certain characters in unpredictable ways
- It lost 519 lines from `dashboard_csg.html` once, breaking the entire dashboard render

**Always use surgical per-field regex substitution** on individual JSON entries to preserve every byte except the field being changed. Pattern:

```python
# Read raw bytes, decode, find specific company by apollo_id, find balanced { ... },
# regex-replace just the needed fields inside that span, write back as bytes (preserves CRLF)
```

### 4. Git lock files

When the file tools write files via the sandbox, git operations sometimes fail with `index.lock` or `refs/remotes/origin/main.lock`. The user must clear these from PowerShell:

```powershell
Remove-Item .git\index.lock -Force -ErrorAction SilentlyContinue
Remove-Item .git\refs\remotes\origin\main.lock -Force -ErrorAction SilentlyContinue
```

### 5. `app.py` keeps getting truncated in the working tree

The local working copy of `app.py` repeatedly ends up missing its last 7 lines (truncated syntax error at `return jsonify({"error": f"weekly-stats for ...`). This doesn't affect Railway (which pulls from `origin/main`, where it's fine), but it does block local runs and `git commit -am`. Always run `git checkout app.py` before any commit to restore from git.

### 6. Google News RSS — inaccessible from bash sandbox

Must use Claude's WebSearch tool for any news fetching in the sandbox. Actual RSS fetching happens locally via `fetch_csg_news.py`.

### 7. Do NOT commit large bak files

`data/tracker_corrupt_20260521_124645.bak` was 158 MB and was deleted via cleanup_cache.ps1. It's in `.gitignore` under `data/*.bak`.

### 8. Bash sandbox is read-only on CIFS mount

The workspace bash sandbox can READ files in the user's mount but CAN NOT write or delete. All file modifications must go through the Edit/Write tools. Deletions must happen via PowerShell scripts the user runs locally.

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

* `GET /` → redirects to `/accounts`
* `GET /accounts` → account picker page
* `GET /dashboard/<account_id>` → serves dashboard HTML (with no-cache headers)
* `GET /login`, `POST /login` → auth (email + password)
* `GET /logout`
* `GET /health` → JSON health check (Railway uses this)
* `GET /api/weekly-stats[/<account_id>]` → JSON weekly stats

No-cache headers (added to prevent browser caching stale dashboards):

```python
response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
response.headers["Pragma"] = "no-cache"
response.headers["Expires"] = "0"
```

---

## DASHBOARD BUILDER (`tracker/dashboard_builder.py`)

Single function `build_dashboard(companies_from_csv, store, output_path, ...)` generates a 3,800+ line self-contained HTML file. Both dashboards use this same builder. ~4,170 lines as of this session (was ~3,400 before v3 work).

### Company Modal — 5 tabs

```
Tab 0: Overview     → employees, revenue, funding, tech stack, intent scores
Tab 1: Signals      → chronological signal history (all signal types)
Tab 2: Leadership   → C-suite people with LinkedIn links
Tab 3: Tech Stack   → categorised tech badges
Tab 4: News         → News Mention signals as article cards
```

### KPI strip (top of dashboard)

* Total Companies, HIGH Alerts, C-Suite Changes, Funding Rounds, M&A, IPO Signals, News Mentions
* Clicking each opens a KPI modal with a filterable list
* Numbers come from `DATA.kpis.*` — static integers baked in at build time, NOT computed live

---

## DASHBOARD JS — Key Boot-time Logic (v3 additions)

### 1. Signal dedup IIFE (NEW v3)

Runs once on page load, immediately after `const DATA = {...}` parses. Located right before `// ── State ──` at:

* `reports/dashboard.html` ~line 1461
* `reports/dashboard_csg.html` ~line 1485
* `tracker/dashboard_builder.py` ~line 1759

```js
(function _dedupSignalsOnce(){
  // Collapses duplicate signals on (apollo_id | signal_type | first 80 chars of detail)
  // Keeps the row with the most recent sent_at
  // Recomputes DATA.kpis.signals_this_week, csuite_changes, funding_signals,
  // ma_signals, ipo_signals, news_signals from the deduped array
  // (severity counts high_alerts/medium_alerts/low_alerts are NOT recomputed since
  // those are per-unique-company counts in the source build, not per-signal)
})();
```

Logs `[dedup] signals: N -> M` in the browser console.

### 2. KPI count-up animation REMOVED (was causing user-perceived "numbers changing every refresh")

The `countUp()` function at line ~1640/1664/1938 in all three files was replaced with an instant-render version:

```js
function countUp(el, target, duration=1200) {
  // Animation disabled — set final value instantly so mid-animation
  // captures (and the v3 scroll-reveal hiding the card while it animates)
  // can't make the displayed number look smaller than the true KPI value.
  if (!isNaN(target)) el.textContent = target.toLocaleString();
}
```

**Why this matters:** The original count-up animated 0→target over 1.2s with cubic ease-out, which lingers at ~67% of target for a noticeable fraction. Combined with the v3 scroll-reveal that makes KPI cards initially opacity-0, screenshots consistently caught numbers at ~67% target (e.g., 839 instead of 1,251). The user reported "numbers change every refresh" — actual cause was animation timing, not real data variability.

---

## ANIMATION SYSTEM (v1 + v2 + v3 + v3.5)

All three layers coexist in both dashboards. They're namespaced (`#ev3-*`, `.ev3-*`, `window._ev3`) so they don't clobber each other.

### v1 (in renderKPIs and similar) — sparkline charts, basic transitions, KPI card stagger

### v2 (~330 lines, sections A–O) — scroll progress bar, toasts (toasts now removed), 3D tilt on KPI cards, magnetic buttons, search highlight, section crossfade, sticky header shadow

### v3 — Premium Polish Layer (NEW THIS SESSION)

CSS injected before `</style>`, JS injected as IIFE before `</script>`. What's IN:

* **A. Drifting mesh-gradient backdrop** — full-viewport fixed-position, indigo + cyan + purple blurred blobs, very slow drift (28-38s loops)
* **B. Film-grain noise overlay** — 5% opacity SVG-based fractalNoise pattern
* **C. Cursor-tracking spotlight on KPI cards + panels** — radial gradient follows mouse via CSS variables
* **E. Severity pulse ring on HIGH badges** — pulsing concentric red ring on `.ev3-high` elements
* **F. Header shimmer reveal** — gradient text sweep on h1, h2, .section-title on first scroll into view (IntersectionObserver)
* **G. Modal entrance** — scale(0.9) + translateY(18px) + blur(6px) → 1/0/0 with cubic-bezier spring
* **H. Table row hover glow-sweep** — horizontal indigo gradient swipes across row on hover
* **I. Section scroll-reveal fade-up** — `.panel`, `.kpi-card`, `.chart-card` fade in with translateY(22px) + scale(0.985)
* **K. Toast spring entrance** — bounce-in with rotation
* **L. Sidebar active indicator gradient bar** — left rail on active nav item, indigo→purple→cyan
* **M. Refresh button radial-ripple expand on hover**
* **N. Reduced-motion media query** — disables all v3 animations for `prefers-reduced-motion: reduce`

### What was REMOVED from v3 during this session

* **D. Conic-gradient rotating border on HIGH cards** — removed per user request ("remove the animation inside 'High alerts'")
* **Welcome toasts** — both "Dashboard ready ✅" and "Premium animations engaged ✨" removed per user request
* **Confetti burst** on first HIGH badge sighting — removed per user request

### v3.5 — Typography Hero + Severity Visual System (NEW THIS SESSION)

Added Space Grotesk font to Google Fonts import. CSS additions:

* **Hero KPI numbers** — 44px (up from 30px), white→indigo→cyan gradient text fill, tabular numerics, scales 1.03 on card hover. HIGH severity card uses red→amber gradient
* **KPI labels** — uppercase 10px micro-caps with `letter-spacing:.14em` (the blue accent line that was here originally was REMOVED per user request)
* **Severity left-edge rails** — 3px colored bar on `.kpi-card.high-card` with inset-glow shadow
* **Severity rails on signal rows** — 3px bar on `.mini-alert::before`, color varies by `sev-high`/`sev-medium`/`sev-low` class, widens to 5px on hover
* **Severity chips** — `.chip-h`, `.chip-m`, `.chip-l` are now outlined pills with glowing status dots, lift+scale on hover
* **Section header gradient underline** — 32px gradient bar under h2 and `.section-title` (indigo→purple→cyan→transparent)
* **Tabular lining numerics** everywhere — table cells, KPI numbers, badge counts, timestamps
* **Badge-count refinement** — indigo gradient fill with subtle border

---

## APOLLO ENRICHMENT — TOP 40 CSG COMPANIES DONE

Three batches via Apollo `bulk_enrich`, all surgically patched into `dashboard_csg.html` (CRLF preserved):

### Batch 1: rows 1-10 by signal count
Basler AG, Brother Industries, Samsung Electronics (×3 entries share samsung.com), Sharp Corp, Alps Alpine, Apple Inc., BigBen Interactive, Casio, Compal Electronics, Corsair.

### Batch 2: rows 11-30
Gigabyte, HP, Kontron, Motorola, Nokia, Sony (×2), Whirlpool SA, Acer, Cellularline, Dell Technologies, Emdoor, Epson, Foxconn, Framework, Fujitsu (×2), Geo Ltd, Hamilton Beach, HCLTech, Honor, HPE.

### Batch 3: rows 31-40
Huawei, IGEL Technology, Koss Corporation, Lava International, LG Electronics, Logitech, MAINGEAR, MiTAC, NEC Corporation, NZXT.

**Total Apollo credits used so far: 40** (1 per match).

**Fields enriched per company:**

* `logo_url` — Apollo CDN URL (e.g., `https://zenprospect-production.s3.amazonaws.com/uploads/pictures/.../picture`)
* `employees` — integer headcount
* `annual_revenue` — integer dollars
* `annual_revenue_fmt` — formatted string ("$416.2B", "$263M", etc.)
* `latest_funding_type` — "Public" or empty (for private companies)

**Apollo data-quality flags worth knowing:**

* **Sharp** (`sharp.com`) — Apollo returned Sharp HealthCare San Diego, NOT Sharp Corp Japan. Numbers (19,000 emp / $2.4B) are the wrong entity's. Logo is generic Sharp.
* **Casio** (`casio.com`) — Apollo returned Casio India subsidiary (590 emp / $1.8B), not Casio Computer Co Ltd parent.
* **Brother** (`brother.com`) — Apollo's primary_domain came back as `brother-usa.com` (subsidiary), 2,200 emp / $3B.
* **BigBen** (`bigben.fr`) — Apollo returned `bigben.eu` (parent Bigben Interactive), 490 emp / $312M.
* **Whirlpool** (`whirlpool.com.br`) — Record name "Whirlpool Jovens Talentos" (recruiting brand) but financials and ticker WHRL3.SA are Whirlpool S.A. Brazil. Data is fine.
* **Sony** (`sony.com`) — Apollo's "Sony Electronics" subsidiary; numbers shown are Sony Group parent.
* **Motorola** (`motorola.com`) — Apollo said "Motorola Mobility (Lenovo)" but the ticker MSI is Motorola Solutions (different entity). Numbers are Mobility's.
* **LG** (`lge.com`) — Apollo returned `lg.com` (parent — accepted as same company).
* **MiTAC** (`mitac.com`) — Apollo returned 0 revenue (treated as missing); funding marked Public manually (TWSE:3706 verified externally).
* **Kontron, Acer** — Apollo flag missing but both verified publicly traded externally (Vienna:KTN and TWSE:2353); marked Public manually.
* **Lava** (`lavamobiles.com`) — Apollo had `nse` exchange but no symbol. Lava International filed DRHP but is still pre-IPO. Marked Private.
* **Cellularline** — Apollo returned 0 employees (treated as missing).
* **5 confirmed private (no funding stage set):** Huawei, IGEL (PE-owned by TA Associates), Lava, MAINGEAR, NZXT, Framework, Emdoor, Geo, Honor.

### `enrich_csg_from_apollo.py` (NEW v3)

Re-runnable DB-update script. Reads `DEFAULT_ENRICHMENTS` dict (or `apollo_enrichments.json` override file if present). Updates:

* `companies.logo_url` (adds the column via `ALTER TABLE` if missing — idempotent)
* `snapshots.employees`, `snapshots.annual_revenue`, `snapshots.latest_funding_type` (on latest snapshot row)

Currently contains all 40 enriched companies. To run locally:

```powershell
cd C:\Users\krishna.l\company-signal-tracker
python enrich_csg_from_apollo.py
python build_csg_dashboard.py   # regenerate HTML from DB
git add -A && git commit -m "..." && git push
```

### Next batch ready to go: rows 41-50

Not yet enriched. When the user says "enrich next 10", identify rows 41-50 by sorting `DATA.companies` by `signal_count` desc, name asc, and skipping the 40 already-enriched domains. State the 10-credit cost upfront, fire `apollo_organizations_bulk_enrich`, parse via subagent or direct Grep on the saved response file, surgically patch the HTML, extend `enrich_csg_from_apollo.py`.

---

## CLEANUP DONE THIS SESSION

`cleanup_cache.ps1` was written for the user to run locally (bash sandbox can't delete from CIFS). It targets:

* `__pycache__/` directories everywhere (~608 KB, 25 .pyc files, mixed Python 3.10 + 3.14 builds)
* `.pytest_cache/` (3.7 KB)
* `data/.fuse_hidden*` orphans (~39 MB across 1,257 files — filesystem garbage from CIFS)
* Empty scratch DB files: `tracker_csg.db`, `tracker_csg_fresh.db`, `tracker_csg_ingested.db` + journals, `test.tmp`, `test_write.tmp`
* `data/tracker_corrupt_20260521_124645.bak` (158.9 MB)
* `reports/dashboard_2026-05-11.html` + `reports/latest.html` (stale dated snapshots)
* `CONTEXT_FOR_NEW_CHAT.md` (v1 — superseded)

Estimated cleanup: ~199 MB freed across ~1,295 files.

`.gitignore` was also tightened:

* Added `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`
* Added `data/*.tmp`, `data/*.db-journal`, `data/.fuse_hidden*`
* Fixed the malformed line 33 (had `d a t a / * . b a k` with literal spaces)

---

## CURRENT GIT STATE

Latest commits on `origin/main` (as of session end):

```
39a0438 Dashboard refresh
529fca5 Clean up cache files; tighten .gitignore
b2b3a01 Apply v3 + v3.5 visual system to Healthcare dashboard (parity with CSG)
6ea6c98 Fix dashboard render: re-apply Apollo enrichment via surgical patch (preserve CRLF + line count)
5c322fa Apollo enrichment: top 10 CSG companies by signal count
876d6b8 Remove accent line in front of KPI labels
155c65f v3.5: hero typography + severity visual system
827e309 v3 tweaks: remove welcome toasts and HIGH alerts conic border
630ffa6 Animation v3: mesh bg, cursor spotlight, conic borders, pulse rings, scroll reveal, confetti, modal entrance
e6e92fe Fix: restore truncated JS tail in dashboard_csg.html
```

**Uncommitted changes in working tree (session end):**

* `reports/dashboard.html` — v3 + v3.5 + dedup + countUp-instant + Healthcare animation parity (NOT YET PUSHED to git — needs commit)
* `reports/dashboard_csg.html` — rows 11-40 Apollo enrichment (logos, employees, revenue, funding) + signal dedup + countUp-instant + v3 cleanups (NOT YET PUSHED)
* `tracker/dashboard_builder.py` — v3 + v3.5 + dedup + countUp-instant baked in for future builds (NOT YET PUSHED)
* `enrich_csg_from_apollo.py` — extended with all 40 companies (NOT YET PUSHED)
* `cleanup_cache.ps1` — new file (NOT YET PUSHED)
* `.gitignore` — tightened (NOT YET PUSHED)
* `CONTEXT_FOR_NEW_CHAT_V3.md` — this file (NOT YET PUSHED)

**Suggested deploy command (run from PowerShell):**

```powershell
cd C:\Users\krishna.l\company-signal-tracker
git checkout app.py
.\cleanup_cache.ps1
python enrich_csg_from_apollo.py
Remove-Item .git\index.lock -Force -ErrorAction SilentlyContinue
git add reports/dashboard.html reports/dashboard_csg.html tracker/dashboard_builder.py enrich_csg_from_apollo.py cleanup_cache.ps1 .gitignore CONTEXT_FOR_NEW_CHAT_V3.md
git commit -m "Session wrap: v3 animations + Apollo top-40 enrichment + signal dedup + cache cleanup"
git push
```

---

## DASHBOARD STATE METRICS (verified at session end)

| File | Size | Lines | CRLF pairs | Ends with |
|------|------|-------|------------|-----------|
| `reports/dashboard.html` | ~5.6 MB | 3,847 | 3,847 | `</script>\r\n</body>\r\n</html>` |
| `reports/dashboard_csg.html` | ~1.4 MB | 3,847 | 3,789 | `</script>\r\n</body>\r\n</html>` |
| `tracker/dashboard_builder.py` | — | 4,169 | mixed | `</html>"""` |

DATA.kpis values currently embedded:

**Healthcare:** total_companies=1251, signals_this_week=1961, high_alerts=88, medium_alerts=0, low_alerts=679, csuite_changes=163, funding_signals=39, ma_signals=21, ipo_signals=2, news_signals=1736

**CSG:** total_companies=294, signals_this_week=770, high_alerts=78, medium_alerts=0, low_alerts=122, csuite_changes=61, funding_signals=5, ma_signals=34, ipo_signals=3, news_signals=667

Note: signal dedup IIFE will recompute `signals_this_week`, `csuite_changes`, `funding_signals`, `ma_signals`, `ipo_signals`, `news_signals` client-side based on deduped `DATA.signals` array — so user-facing numbers may be slightly lower (e.g., the Guardian Flight IPO dupe will collapse 2 → 1).

---

## RAILWAY DEPLOYMENT

* Platform: Railway (paid)
* Service name: **"web"** (NOT "company-signal-tracker" — that's the source service, doesn't serve traffic)
* Live URL: `https://web-production-068e7.up.railway.app`
* Region: US West, 1 Replica
* Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
* Health check: `GET /health` (timeout 30s, restart on failure)
* Auto-deploy: git push to `origin/main` → Railway auto-rebuilds in ~60s

**IMPORTANT:** The domain `company-signal-tracker-production.up.railway.app` was the source repo service URL and historically didn't resolve — but as of session end the user appears to be hitting it successfully (may have wired up a custom domain). Both URLs may work now.

---

## HEALTHCARE DASHBOARD — SIGNAL ARCHITECTURE

### HIGH signals (Google Sheets — user manually maintains)

1. Funding Round — `funding_sheet_id` → tab "Funding signals"
2. C-Suite Join / Exit — `csuite_sheet_id` → tab "C-suite signals"
3. Acquisition / M&A — `ma_sheet_id`
4. IPO Signal — `ipo_sheet_id`
5. Subsidiary Change — `subsidiary_sheet_id`

### LOW signals (Google News RSS — automatic)

* News Mention — articles not matching HIGH/MEDIUM keyword patterns

M&A and IPO are intentionally NOT detected from News RSS (only from Sheets).

### CLI flags (`main.py`)

```bash
python main.py --sheets-only      # Refresh HIGH signals from Sheets only
python main.py --news-only        # Refresh Google News RSS only
python main.py --dashboard-only   # Rebuild dashboard.html from DB (no API calls)
python main.py --reset-alerts     # Clear dedup history
python main.py --dry-run          # No DB writes, print only
python main.py --company "NAME"   # Single company
```

---

## CSG DASHBOARD — SIGNAL ARCHITECTURE

What it is: a self-contained HTML file (`reports/dashboard_csg.html`) with all ~294 company data embedded as JSON. Dark theme. Pure HTML/CSS/JS — no server-side rendering.

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

### Full refresh workflow (all run locally in PowerShell)

```powershell
python seed_csg_signals.py          # re-seed known signals (safe to re-run, dupes skipped)
python fetch_csg_news.py            # fetch latest news (takes ~6 min)
python enrich_csg_from_apollo.py    # apply Apollo enrichment data (logos + emp + rev)
python build_csg_dashboard.py       # rebuild HTML from DB
git checkout app.py
git add -A
git commit -m "Update CSG dashboard"
git push
```

CSG DB path: `data/tracker_csg_v2.db` — never use any other DB for CSG work.

---

## SQLITE SCHEMA (both DBs share this schema)

```sql
-- companies
apollo_id TEXT PK, name, domain, industry, city, state, first_seen, last_enriched, is_active,
logo_url TEXT  -- added by enrich_csg_from_apollo.py if missing

-- snapshots (one per run per company)
id, apollo_id, snapshot_date, employees, annual_revenue, total_funding,
latest_funding_type, latest_funding_amount, last_raised_at,
hq_city, hq_state, tech_stack JSON, leadership_json JSON,
open_job_count, intent_score_1, intent_topic_1, intent_score_2, intent_topic_2,
crm_stage, retail_locations, subsidiary_of, raw_json

-- alerts_sent (one per signal fired)
id, apollo_id, signal_type, signal_detail, severity, sent_at,
signal_date,    -- actual event date (Announcement Date or article pub date)
source_url,     -- encoded as "Source Name||https://url.com" or plain URL
dry_run

-- weekly_runs
id, run_date, companies_checked, signals_high, signals_medium, signals_low, duration_seconds
```

### Key data concepts

* `signal_date` = actual event date; `sent_at` = when tracker recorded it
* `source_url` format: `"Name||https://url"` — split on `||` → label + link
* Dedup window: 7 days on (apollo_id + signal_type + headline) in the build pipeline
* **NEW v3:** dashboard-side JS dedup on (apollo_id + signal_type + first 80 chars of detail), keeps newest sent_at — runs on every page load

---

## PENDING TASKS (not yet run locally)

1. **Healthcare:** `python main.py --reset-alerts --sheets-only` — repopulates DB with correct Announcement Dates (old records used `sent_at` as date instead of actual event date)
2. **Healthcare:** `python main.py --news-only` — fetch fresh news articles with real decoded URLs
3. **Healthcare:** `python fix_ipo_signals.py` — deletes 5 false IPO records for already-public companies: Nutex Health, Karyopharm Therapeutics, AngioDynamics, Omada Health, **Guardian Flight** (the duplicate the user spotted in the IPO modal)
4. **CSG enrichment:** rows 41-50 next batch when the user is ready
5. **Run:** `.\cleanup_cache.ps1` to remove caches + FUSE orphans + corrupt .bak
6. **Run:** `python enrich_csg_from_apollo.py` to persist the 40 enriched companies into `tracker_csg_v2.db`
7. **Push all uncommitted working-tree changes** (see commit command above)

---

## ENVIRONMENT

* Python 3.14 (local), SQLite `journal_mode=DELETE`
* Libraries: `feedparser`, `google-auth`, `google-api-python-client`, `typer`, `rich`, `pyyaml`, `requests`, `flask`, `gunicorn`, `openpyxl`
* Local folder: `C:\Users\krishna.l\company-signal-tracker\`
* Bash sandbox mounts at: `/sessions/<session>/mnt/company-signal-tracker/`
* Bash sandbox uses a proxy that may block `*.railway.app` — can't test live URL from sandbox

---

## HOW TO WORK ON THIS — Key files to read first

* `C:\Users\krishna.l\company-signal-tracker\tracker\dashboard_builder.py` — HTML generator (single source of truth for layout)
* `C:\Users\krishna.l\company-signal-tracker\reports\dashboard_csg.html` — live CSG HTML (active development surface)
* `C:\Users\krishna.l\company-signal-tracker\reports\dashboard.html` — live Healthcare HTML
* `C:\Users\krishna.l\company-signal-tracker\app.py` — Flask server
* `C:\Users\krishna.l\company-signal-tracker\main.py` — Healthcare signal orchestrator
* `C:\Users\krishna.l\company-signal-tracker\enrich_csg_from_apollo.py` — DB-level enrichment helper

### Deploy workflow

1. Edit files in bash sandbox (Claude writes via Edit/Write tools, surgical regex preferred — never `json.dumps` the DATA blob)
2. Verify integrity via Read tool, NOT bash (CIFS lag)
3. User runs from PowerShell:

```powershell
cd C:\Users\krishna.l\company-signal-tracker
git checkout app.py
Remove-Item .git\index.lock -Force -ErrorAction SilentlyContinue
Remove-Item .git\refs\remotes\origin\main.lock -Force -ErrorAction SilentlyContinue
git add -A
git commit -m "Your message"
git push origin main
```

4. Railway auto-redeploys in ~60 seconds

### Run CSG refresh (all local, no bash)

```powershell
cd C:\Users\krishna.l\company-signal-tracker
python seed_csg_signals.py
python fetch_csg_news.py
python enrich_csg_from_apollo.py
python build_csg_dashboard.py
git checkout app.py
git add -A && git commit -m "Update CSG dashboard" && git push
```

---

## CONVENTIONS / STYLE FOR ME (Claude)

When working on this project:

* **Surgical regex patches** for changing the embedded `DATA` blob — never `json.loads` + `json.dumps`
* **Preserve CRLF line endings** by reading/writing in binary mode
* **Verify integrity** after every multi-edit operation: line count, CRLF count, file-ends-with `</html>` (or `</html>"""` for the Python builder)
* **Set up TaskCreate** for any multi-step work (≥3 steps), update statuses as work progresses
* **Stay quiet about the bash CIFS-cached view** when it shows stale data — just verify with the Read tool and proceed
* **Always remind the user to `git checkout app.py`** before pushing, since the working tree keeps getting truncated
* **Apollo MCP** has a mandatory confirmation rule — must state exact credit cost ("N credits") before calling `apollo_organizations_bulk_enrich` or `apollo_organizations_enrich`
* **When in doubt about Apollo data quality** — flag the company name in the response (subsidiary vs parent, etc.) and let the user decide

---

End of v3 context. Continue from "Enrich next 10" (rows 41-50) or whatever the user's next request is.
