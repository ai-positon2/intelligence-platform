# Intelligence by Position² — Full Context (v9 · June 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v9 supersedes all earlier context files (v1–v8)** — those are stale; ignore/delete them. v9 records the "favicon + topbar centering + GTM card renames + PPC→GTM breadcrumb + /ppc 301-redirects + a major refresh-pipeline reliability/performance overhaul (RSS timeouts, parallel prefetch, retry/backoff, circuit breaker, config hardening, and a news-source migration from Google News RSS to GDELT)" work cycle.

---

## WHAT THIS IS

**Intelligence by Position²** is an internal B2B revenue-/sales-intelligence web app for the Position2 agency team (Position2 is a B2B digital-marketing agency: SEO & organic growth, performance marketing, paid social, content, brand & website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymises website visitors, scrapes LinkedIn engagement, tracks competitor ads, ranks prospects by intent, and helps reps act via an embedded AI assistant called **Vimi**.

- **Live URL:** `https://intelligence.position2.com`
- **GitHub:** `https://github.com/ai-positon2/intelligence-platform` (public)
- **Hosting:** Railway — auto-deploys on every push to `main` (~90s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; data refreshes happen via GitHub Actions.
- **Auth:** Google SSO, `@position2.com` only. `@login_required` on protected routes; `@admin_required` for `/admin/*`.
- **Admins:** `krishna.ladha@position2.com`, `sudheer.d@position2.com`
- **Latest `main` HEAD (end of this cycle): `002f191`.**

### The two disciplines
The hub presents two disciplines as cards: **GTM** (go-to-market — was "PPC"; renamed in v8) and **SEO**. Tagline: "two disciplines · one intelligence layer."

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                       ← Flask server (~3,000 lines): routes, API, OpenAI (Vimi), insights scoring, live-refresh trigger, auth
├── main.py                      ← Weekly orchestrator for the HEALTHCARE account (Sheets HIGH signals + News LOW) -> data/tracker.db
├── fetch_csg_*.py               ← CSG fetchers (news/jobs/sheets)
├── fetch_healthcare_jobs.py     ← Healthcare creative-hiring scraper
├── weekly_digest.py             ← Ranks companies; writes reports/opportunities_<acct>.csv (+ optional AI brief / Slack post)
├── static/                      ← css/ ds-tokens.css, ds-components.css, hub.css, linkedin.css, anonymous_visitors.css, gtm.css, seo.css ; js/ linkedin.js, anonymous_visitors.js
├── data/                        ← tracker.db (healthcare ~1,251), tracker_csg_v2.db (CSG 294), weekly-stats.json
├── templates/
│   ├── hub.html                 ← Home hub (GTM + SEO discipline cards) — sets window.VIMI_NAME; topbar link to Context Graph (now centered); includes the Vimi widget
│   ├── context_graph.html       ← Interactive 3D "Context Graph" data-flow page (has favicon as of v9)
│   ├── ppc_chat_widget.html      ← SHARED Vimi chat widget v5 (filename still "ppc_*" — see GTM-rename note) — included on hub + tool pages
│   ├── gtm.html                 ← GTM discipline dashboards page (cards renamed in v9: "Target Accounts", "LinkedIn Intelligence")
│   ├── linkedin_scraper.html, anonymous_visitors.html, seo.html
│   ├── login.html               ← pre-login Vimi greeting bubble (has favicon as of v9)
│   ├── accounts.html, admin_usage.html, embed.html, 403.html
├── reports/
│   ├── dashboard.html (~4.4MB) / dashboard_csg.html (~1.6MB)  ← Signal Tracker dashboards (GENERATED + Vimi-customised; single-line `const DATA`); breadcrumb now "Hub › GTM › Signal Tracker"
│   └── opportunities_healthcare.csv / opportunities_csg.csv
├── ad_intelligence/             ← built React/Vite app (Competitor Ad Intelligence) served directly; assets load from absolute /assets/
├── tracker/                     ← signal pipeline package (dashboard_builder.py, change_detector.py, sheets_client.py, news_client.py, news_relevance.py, jobs_client.py, snapshot_store.py, csv_loader.py, notifier_slack.py, notifier_sheets.py, signal_score.py)
├── scripts/refresh-dashboards.py ← rebuilds BOTH dashboards preserving Vimi; prunes >90d + reclassifies + strict news
└── .github/workflows/
    ├── refresh-dashboards.yml    ← weekly (Mon 08:30 UTC) + manual: fetch -> prune/classify -> rebuild -> digest -> commit
    └── weekly_tracker.yml        ← weekly (Mon 08:00 UTC): healthcare main.py
```

### Deploy & data model
- **Code/UI** (templates/static/app.py): push to `main` → Railway redeploys (~90s). No hot reload. (`@app.after_request` sets no-cache on HTML so deploys show immediately.)
- **Data** (signals): refreshed by the GitHub Action (news/jobs/sheets run on GitHub's runners). The Action commits updated DBs + dashboards. NOTE: the Action checks out `main` at dispatch time, so a fresh run always picks up the latest pushed code — code fixes to fetchers take effect on the *next* run, not the one already running.
- **Dashboards are GENERATED + Vimi-customised.** Never hand-edit `reports/dashboard*.html` structure blindly. The refresh pipeline splices the fresh single-line `const DATA = {...};` blob into the committed Vimi HTML (preserving Insights/chat). Markers: `INSIGHTS v10 JS` and `id="vimi-plat"`. Structural JS changes must be applied to BOTH `tracker/dashboard_builder.py` AND the committed `reports/dashboard*.html`. (Editing static breadcrumb/label text in the committed HTML is safe — the refresh only re-splices `const DATA`.)

---

## PPC → GTM RENAME (the discipline only — done in v8, extended in v9)

The **discipline** formerly called "PPC" is now **GTM** (go-to-market), end to end.

**Renamed (user-facing + the discipline's own code/URLs):**
- Routes: `/gtm`, `/gtm/ad-intelligence`, `/gtm/anonymous-visitors` (+`/data`), `/gtm/linkedin-scraper`. The Python view fn `def ppc()` → `def gtm()` renders `gtm.html`.
- Files: `templates/ppc.html` → `gtm.html`; `static/css/ppc.css` → `gtm.css`.
- UI copy/labels, command palette, breadcrumbs, page titles, GTM page eyebrow + ghost watermark.

**v9 update — `/ppc*` page URLs now 301-REDIRECT to `/gtm*` (they no longer serve the page directly):**
- The legacy `/ppc*` decorators were removed from the GTM view functions; dedicated redirect routes now exist: `/ppc` & `/ppc/` → `/gtm`; `/ppc/ad-intelligence`(+`/`) → `/gtm/ad-intelligence`; `/ppc/anonymous-visitors` → `/gtm/anonymous-visitors`; `/ppc/linkedin-scraper` → `/gtm/linkedin-scraper` (all `code=301`).
- **Why:** old bookmarks/links still resolve (they redirect), but the **address bar now shows `/gtm`**. Previously the stacked `@app.route("/ppc")` decorators served the page in place, so the URL stayed `/ppc`.
- **The non-page `/ppc` plumbing aliases were deliberately LEFT serving** (NOT redirected): `/ppc/ad-intelligence/assets/<file>`, `/ppc/ad-intelligence/favicon.svg`, `/ppc/ad-intelligence/icons.svg`, `/ppc/anonymous-visitors/data`. These are fetched programmatically (never shown in the URL bar); redirecting them is needless risk.

**Deliberately NOT renamed (do not "fix" blindly):**
- The **shared Vimi chat-widget plumbing**: file `ppc_chat_widget.html`, routes `/api/ppc-chat` + `/api/ppc-upload`, JS globals `window.ppcOpen/ppcSend/...`, element IDs `ppc-btn`, CSS classes `ppc-*`, and `_build_ppc_context`/`_PPC_CTX_CACHE`. Shared by ALL disciplines (incl. SEO), baked into generated dashboards + the compiled React build. Renaming breaks live chat everywhere.
- **"PPC" as a service in Vimi's AI prompts** (Position2 sells PPC as a service — `SEO|PPC|Content|Brand|RevOps`). These live in `app.py` system prompts (e.g. "PPC Intelligence Assistant"). Renaming corrupts Vimi's recommendations.
- **Data files** (Apollo CSV, `data/linkedin.json`) and `tracker/news_relevance.py` keyword filter.
- Net result after v9: **every visible "PPC" label is now GTM** (hub card, GTM page, dashboard breadcrumb, command palette). The only remaining "PPC" strings are intentional plumbing/taxonomy above.

---

## v9 UI CHANGES (small, user-facing)

- **Favicon fixed on `login.html` and `context_graph.html`** — they were the only two real page templates missing the standard inline-SVG favicon `<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,...indigo rounded square + white signal-wave...">`. Added the exact same favicon used by every other template (it lives right after each `<title>`).
- **Context Graph topbar link centered** — in `hub.html`, the "✦ Context Graph" `<a>` is now absolutely centered in the topbar (`position:absolute; left:50%; top:50%; transform:translate(-50%,-50%)`), so it sits dead-center regardless of the brand/user-pill widths on either side (the topbar is `display:flex; justify-content:space-between`).
- **GTM dashboard card renames** (`gtm.html`, `<div class="card-name">` text only): "Company Signal Tracker" → **"Target Accounts"** (still links `/accounts`); "LinkedIn Scraper" → **"LinkedIn Intelligence"** (still links `/gtm/linkedin-scraper`). Routes, palette entries, and the marquee were intentionally left unchanged.
- **Signal Tracker breadcrumb** in both `reports/dashboard.html` and `reports/dashboard_csg.html`: `Hub › PPC › Signal Tracker` → `Hub › GTM › Signal Tracker` (link now `/gtm`). Edited directly in the committed HTML (safe; refresh only re-splices `const DATA`, which was verified to still parse).

---

## VIMI — THE EMBEDDED AI ASSISTANT (widget, v5 "Vimi Studio") — UNCHANGED this cycle

Shared widget `templates/ppc_chat_widget.html` (one file: CSS + markup + ~1k-line IIFE). Backed by `/api/ppc-chat` (chat) and `/api/ppc-upload` (files). Included on hub, gtm, anonymous_visitors, linkedin_scraper, context_graph. **NOTE: NOT on `seo.html`, `accounts.html`, `admin_usage.html`** (a known gap — see roadmap).

**Design language:** minimal dark canvas, slim glass header (small gradient avatar, name "Vimi", status dot, ghost icon buttons), welcome screen with a Three.js WebGL particle orb + centered greeting "Hi {name}!" + outlined pill quick-replies, glassy single-line composer. Inter (UI) + Sora (display). Light/dark + cozy/compact density.

**Fresh-chat behaviour:** opening Vimi always starts a new chat (welcome); the prior chat is auto-saved to History; empty sessions pruned; first-ever open shows onboarding once. Helper `freshChat()`.

**Feature set (client-side against existing endpoints):** streaming reveal, stop/regenerate, per-message copy/👍👎/save-to-memory, edit & resend, sessions/history drawer, voice (Web Speech) + spoken replies, slash commands (`/leads /email /research /export /digest /visitors /dossier /why`), @-mentions, keyboard (Cmd/Ctrl+K, ↑ to edit, Esc), source-citation pills, sortable tables, inline charts (Chart.js), export CSV/Excel(SheetJS)/JSON/PDF(jsPDF), "open in Gmail draft", contextual actions (dossier card parsed from a ```vimi-card``` JSON block, why-hot, digest, visitor summary), Memory UI, per-user defaults, onboarding, confirm-before-write modal + client audit log, sound/haptics, proactive nudge badge, draggable + resizable panel.

**Key globals / hooks (do not break):** `window.ppcOpen / ppcSend / ppcSugg / ppcNewChat / ppcShowMemory / ppcToggleExpand`; element IDs `k-*` (`k-fab`, `k-panel`, `k-body`, `k-input`, `k-send`, …) plus `ppc-btn`. Reads `window.VIMI_NAME`. Programmatic opens guarded by a `_justOpened` timestamp.

**Write-actions / Phase 2 (still mocked):** HubSpot/Slack/Sheets/deck go through confirm + audit UX and POST to `/api/vimi/{hubspot,slack,sheets,deck}` — **those routes don't exist**, so they degrade to "queued — activates when the connector is live." (Known UX/trust gap — see roadmap.)

**OpenAI model chain:** `OPENAI_INSIGHTS_MODEL` env → `gpt-5.4` → `OPENAI_MODEL` env → `gpt-4o-mini`. Web search via OpenAI Responses `web_search_preview`. Account-scoped Vimi endpoints: `/api/vimi-chat/<account>`, `/api/vimi-export`, `/api/insights/<account>`, `/api/company-analysis/<account>`, `/api/generate-email/<account>`, `/api/research-company/<account>`, `/api/decision-makers/<account>`.

---

## CONTEXT GRAPH PAGE (`/context-graph`) — 3D (from v8; favicon added v9)

`templates/context_graph.html` — interactive explainer of how a buyer's data connects and where the intelligence layer sits. Reachable from the hub topbar ("✦ Context Graph", now centered) and the command palette.

- **Nodes:** core entities Person / Account / Deal; person signals (Pages Visited, Email History, LinkedIn Signals, Intent Score); account signals (CRM Record, Market Signals, Buying Committee, Readiness State); Outreach Sent / Outcome; and the Vimi intelligence layer core. Categories colour-coded (person=blue, account=green, deal=orange, intelligence=purple).
- **3D rendering:** DOM nodes positioned by a true 3D projection (per-node Z depth via `ZMAP`), the constellation rotates with yaw/pitch (auto-spin + cursor) through a perspective projection (`FOC≈1300`, `DEPTH≈LW*0.42`, persp clamped 0.5–1.7). Nodes billboarded, depth-sorted via z-index; edges/packets/ripples read each node's projected screen position (`n._x/n._y`). Dragging adds a screen-space offset.
- **Detail view:** clicking a node opens a centered modal pop-up (thin custom scrollbar); each CTA routes to a relevant page. Hub's exact particle-engine background (`#bg`). Three.js `0.160.0` from jsdelivr.

---

## LOGIN PAGE — pre-login Vimi greeting bubble (from v8; favicon added v9)

`templates/login.html` shows a Vimi greeting bubble (bottom-right) that opens a self-contained mini-chat (no backend — chat endpoints are login-gated). Returning users greeted by real name (`localStorage['vimi_login_name']` written by the hub); everyone else sees a demo line (vars at top of the login script). Demo content — revert when the demo's over.

---

## ANONYMOUS VISITORS (`/gtm/anonymous-visitors`) — realism + performance (from v8)

Server data from a Google Sheet via `_fetch_anon_visitors_data()`; page shell loads data async; People/Companies tabs with search + filters; row click → profile drawer → Vimi Outreach card (shared VimiEngage code, also on `linkedin_scraper.html`). Realistic 3–7 pages/session with varied dwell; first-person CRM narrative. **TTL cache (`_ANON_CACHE`, 300s) + gzipped data route (`_ANON_GZ`) + 60s client cache; Refresh forces fresh via `?fresh=1`.** People table renders progressively (first 200 instantly, then 300-row rAF batches; `_pplJob` cancels in-flight fills on re-filter).

**DEMO PROXY PEOPLE (temporary):** `app.py` has a `_DEMO_PROXY_PEOPLE` block (10 US-native people) + a pin line that pins them to the top of the People list with today's date. **To restore originals: delete the block and the pin line** (both commented temporary). Remove after the demo.

---

## COMMAND PALETTE & GLOBAL NAV

Every working page (hub, gtm, seo, anonymous_visitors, linkedin_scraper, accounts, admin_usage) has a self-contained command-palette IIFE (`window.__kpal`) opened with **Cmd/Ctrl+K** (keyboard-only; the visible launcher was removed; `window.openPalette` is not exposed). Its `BASE` list includes Context Graph alongside Hub, GTM Tools, SEO Tools, both Signal Trackers, LinkedIn, Ad Intelligence, Anonymous Visitors, Switch Account.

---

## AUTH / UX FLOW

- **Deep links survive login:** `_login_redirect()` saves the intended path into `session['next_url']` (skips `/api`, `/auth`, login/logout; open-redirect-guarded); `/auth/google` pops it and returns it instead of always `/hub`.
- **Honest messaging:** first-time visitors see "Please sign in to continue."; only users with an existing session cookie see "Your session expired…".

---

## ACCOUNTS / SIGNAL-TRACKER PICKER (`/accounts`)

Single-viewport-ish: the old 210vh scroll pin is collapsed (`height:auto`, hero `position:relative`), scroll cue/tagline hidden, decorative `.ac-motif` hidden, ~56px gap between hero text and cards. Cards reveal via IntersectionObserver.

---

## SIGNAL TRACKER (the "Company Signals" dashboard — the webinar centerpiece)

Two accounts: **Healthcare** (`/signal-tracker/healthcare`, ~1,251 companies) and **CSG** (`/signal-tracker/csg`, 294 companies). `/dashboard/<account>` 301-redirects to `/signal-tracker/<account>`. **Eight signal types** — HIGH (curated Google Sheets, authenticated API — NOT IP-blocked): Funding Round, C-Suite Join, C-Suite Exit, Acquisition/M&A, IPO Signal, Subsidiary Change; MEDIUM (auto news/jobs): Product Launch, Partnership, Creative Hiring; LOW: News Mention. **Position² relevance filter** (`tracker/news_relevance.py`, keyword-only). **90-day retention.** **Importance scoring** (`tracker/signal_score.py`): `type_weight × severity × recency (+ multi-intent bonus)` — drives the Insights tab (`/api/insights/<account>`, score ≥ 6.0, top 120) and the weekly digest.

---

## DATA REFRESH PIPELINE + THE v9 RELIABILITY/PERFORMANCE OVERHAUL  ← important

### How refresh is triggered
- In-app **"Refresh signals"** button → `POST /api/refresh-dashboard` → dispatches the single `refresh-dashboards.yml` workflow (`ref: main`, no per-account input). **It is account-agnostic: one refresh refreshes BOTH Healthcare and CSG and rebuilds both dashboards**, even though the progress modal says "Fetching Healthcare signals…" (that's just the first/slowest step). Needs `GH_DISPATCH_TOKEN` (GitHub token w/ `workflow` scope) in Railway; without it the endpoint returns a friendly "not wired up" message.
- `GET /api/refresh-status` polls the run. **The in-app progress bar (% and elapsed timer) is a CLIENT-SIDE ESTIMATE, not real job progress** — don't trust it as truth; read the GitHub Actions logs for the real state.

### Workflow steps (`refresh-dashboards.yml`, all fetch steps `continue-on-error: true`)
checkout → setup-python 3.11 → install reqs → write `config.yaml`+`service_account.json` from secrets → snapshot Vimi dashboards → **Fetch Healthcare signals (`main.py`: Sheets HIGH + News LOW)** → **Fetch Healthcare creative/3D hiring (`fetch_healthcare_jobs.py`)** → **Fetch CSG news (`fetch_csg_news.py`)** → **Fetch CSG creative/3D hiring (`fetch_csg_jobs.py`)** → **Fetch CSG HIGH (`fetch_csg_sheets.py`)** → restore Vimi dashboards → rebuild + prune (`scripts/refresh-dashboards.py`) → weekly digest → commit & push.
- **`continue-on-error: true` masks failures** — a crashed fetch step still shows a green check and the job proceeds. Always read the step's log body, not just the icon.

### The problem we hit this cycle (multi-hour / failing refreshes) and the fixes
1. **No network timeout on RSS fetches.** `feedparser.parse(url)` does its own fetch with NO timeout, so a slow/throttled Google News endpoint could hang for hours. **Fix:** `tracker/news_client._fetch_feed(url)` now downloads with a hard 8s timeout (`_FEED_TIMEOUT`) + custom UA, then parses bytes; on failure returns an empty feed. `jobs_client.get_job_postings` also uses `_fetch_feed` now.
2. **Fully sequential per-company fetches.** ~1,251 Healthcare companies one at a time. **Fix:** parallel prefetch. `news_client.warm_news_cache(names, ...)` fills a module `_NEWS_CACHE` via a thread pool; `get_news_articles(..., _use_cache=True)` reads it instantly. `main.py` and `fetch_csg_news.py` warm the cache before their loops; `fetch_healthcare_jobs.py`/`fetch_csg_jobs.py` prefetch postings concurrently (DB writes stay single-threaded) and the old **1.2s-per-company sleeps were removed**.
3. **Google News 503 rate-limiting under burst.** 16 parallel workers tripped Google's limiter → 503s for every company. **Fix:** concurrency dropped **16 → 6** everywhere; `_fetch_feed` got **retry (2 attempts) + exponential backoff + upfront jitter** (`_FEED_RETRIES`, `_FEED_BACKOFF`).
4. **Google hard-blocks the GitHub Actions runner IP.** When every request 503s/times out, retrying 1,200+ companies still wasted ~1 hour. **Fix:** a **circuit breaker** in `news_client` (`_CIRCUIT_THRESHOLD = 30`, thread-safe). After 30 consecutive failures it "opens" and every further `_fetch_feed` returns instantly-empty, so a blocked run finishes in ~1–2 minutes instead of an hour.
5. **`main.py` crashed on a null config section.** `config.get("behaviour", {}).get("dry_run")` → `AttributeError: 'NoneType'...` because `behaviour:` was present-but-null in `CONFIG_YAML` (the `{}` default only applies when the key is ABSENT). This was masked by `continue-on-error`, so Healthcare's Sheets+News fetch had been silently doing nothing. **Fix:** all config-section reads in `main.py` hardened to `(config.get("section") or {})` (10 sites: behaviour/credentials/signals).
6. **News source migrated off Google News RSS → GDELT.** Because Google News RSS is IP-blocked from CI, `get_news_articles` now sources in this priority: **SerpAPI (if `serpapi_key` set in `CONFIG_YAML` credentials) → GDELT**. RSS was **removed from the CI news path** (kept only for local/dev callers of `_rss_articles`). New helpers in `news_client`: `_fetch_json(url)` (bounded timeout + light retry, **independent of the RSS circuit breaker** so blocked-RSS can't disable GDELT), `_gdelt_seendate_to_iso()`, `_gdelt_articles()` (free GDELT DOC API: `https://api.gdeltproject.org/api/v2/doc/doc?query="<company>"&mode=ArtList&format=json&sort=DateDesc&timespan=<days>days`; maps `title/url/domain/seendate` → the standard `{title,url,summary,source,published}` article dict; dedupes by title; freshness-filtered). Article `summary` is empty from GDELT (relevance filter then leans on the title).

### Current state of each signal type after v9
- **HIGH (Funding / C-Suite / M&A / IPO / Subsidiary)** — from curated Google Sheets via authenticated API; **never IP-blocked**; works reliably now that the `main.py` null-config crash is fixed. These are the demo-critical signals.
- **News Mention (LOW)** — now via **GDELT**, CI-reachable. ✅ (Coverage skews toward companies that appear in indexed online news; very small/obscure companies may show few mentions — a data-coverage reality, not a bug.)
- **Creative Hiring (MEDIUM, the "jobs" steps)** — STILL uses Google News RSS (GDELT indexes news, not job boards), so it is degraded from CI; the circuit breaker makes it fail-fast (minutes, not hours) but it returns little. Lowest-value of the eight types. A proper fix needs a dedicated jobs/ATS source (open item).

### SerpAPI option (preferred high-quality channel, not yet enabled)
The code prefers SerpAPI whenever `serpapi_key` exists in the `CONFIG_YAML` credentials (`creds.serpapi_key`; the `serpapi` Python pkg + `_serpapi_articles` already exist for news). At this scale (~1,545 companies, weekly) SerpAPI exceeds the free tier (100 searches/mo) and needs a paid plan (~$75/mo+) — a budget/ops decision. Drop a key into the secret and it auto-activates ahead of GDELT.

---

## PAGES & ROUTES (key)

| Route | Purpose |
|-------|---------|
| `GET /hub` | Home discipline picker (GTM + SEO); centered Context-Graph topbar link; sets `VIMI_NAME` |
| `GET /context-graph` | 3D interactive data-flow / intelligence-layer explainer |
| `GET /gtm` | GTM discipline dashboards page |
| `GET /ppc` (+ `/ppc/`) | **301 → `/gtm`** (legacy alias) |
| `GET /seo` (+ `/seo/<tool_slug>`) | SEO tool shells (SERP app embeds via `embed.html`) |
| `GET /gtm/anonymous-visitors` (+`/data`) | Visitor de-anonymisation (`/ppc/anonymous-visitors` 301→; `/ppc/.../data` still serves) |
| `GET /gtm/linkedin-scraper` | LinkedIn ABM intelligence (`/ppc/linkedin-scraper` 301→) |
| `GET /gtm/ad-intelligence[/…]` | Competitor Ad Intelligence (built React app; `/ppc/ad-intelligence` 301→; asset/icon/favicon `/ppc/...` aliases still serve) |
| `GET /signal-tracker/<account>[/<section>]` | Signal Tracker dashboards |
| `GET /accounts`, `GET /admin/usage` | account picker, admin usage |
| `POST /auth/google`, `GET /login`, `GET /logout` | Google SSO (returns `session['next_url']` or `/hub`) |
| `POST /api/refresh-dashboard`, `GET /api/refresh-status` | live-refresh trigger (both accounts) + progress estimate |
| `GET /api/insights/<account>` (+`/api/insights-meta`) | Vimi AI brief (scored, important-only) |
| `POST /api/ppc-chat`, `POST /api/ppc-upload` | Vimi chat backend (shared widget) + file upload |
| `/api/vimi-chat/<account>`, `/api/vimi-export`, `/api/generate-email/<account>`, `/api/company-analysis/<account>`, `/api/research-company/<account>`, `/api/decision-makers/<account>` | Vimi actions |
| `GET /api/whoami`, `POST /api/track`, `GET /health` | user info, page-time tracking, health |

---

## ENVIRONMENT VARIABLES (Railway)
`OPENAI_API_KEY`, `OPENAI_MODEL` (default gpt-4o-mini), `OPENAI_INSIGHTS_MODEL` (default gpt-5.4), `GOOGLE_CLIENT_ID`, `SECRET_KEY`, `LOGIN_LOG_SHEET_ID`, `GOOGLE_SA_JSON`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `SERP_PLATFORM_TOKEN`, `GH_DISPATCH_TOKEN` (and optionally `GH_REPO`, `GH_WORKFLOW`). GitHub Action secrets: `CONFIG_YAML` (yaml: behaviour/credentials/signals — incl. optional `serpapi_key`), `GOOGLE_SERVICE_ACCOUNT_JSON`. Optional: `SLACK_WEBHOOK_URL`. (No `VIMI_*`/`KAIRO_*`/`PPC_*` env vars exist.)

---

## HOW TO WORK ON THIS (proven-safe workflow)
1. Clone fresh into the bash sandbox `/tmp` each session (use a unique dir name; the file-tool `…/outputs` path is separate/stale for git, and the file tools can't reach `/tmp`). Sandbox network: **git over `github.com` works, but `api.github.com`, RSS, and GDELT endpoints are BLOCKED** — `workflow_dispatch`, run cancellation, and live scraping must happen in the GitHub Action / UI. `WebSearch` and the workspace `web_fetch` tool have network.
2. Edit real files (mostly `templates/*.html`, `static/*`, `tracker/*.py`, `fetch_*.py`, `app.py`, `scripts/*`). Make edits with Python string-replace scripts run via bash (the file Read/Write/Edit tools only see `…/outputs`, not `/tmp`).
3. **Validate before every push:** `python3 -c "import ast; ast.parse(...)"` for .py; Jinja parse (`jinja2.Environment(...).get_template(...)`) for templates; `node --check` each inline `<script>` (neutralise Jinja first); YAML parse for workflows; confirm dashboards' `const DATA` JSON parses; check for duplicate Flask `@app.route` rules when touching routes.
4. Push to `main` → Railway deploys ~90s (code/UI). **Data/fetcher changes only take effect on the NEXT GitHub Action run** (cancel any in-progress run and re-dispatch so it checks out the new code).
5. Pushing needs a GitHub token (classic w/ `repo`+`workflow`). **Redact tokens from all output and remind the user to revoke after.** If a push is rejected (weekly Action may push in parallel), `git pull --rebase origin main` then push.

### Gotchas
- `reports/dashboard*.html` are generated + single-line + Vimi-customised — patch the generator AND splice DATA; never hand-edit structure blindly. Markers: `INSIGHTS v10 JS`, `id="vimi-plat"`. (Editing static breadcrumb/label TEXT is fine.)
- **Jinja `{#`/`{{`/`{%` trap:** CSS/JS inside a Jinja-rendered template must not contain `{{`, `{%`, or `{#`. Intentional Jinja (`{{ ... | tojson }}`, `{% include %}`) is fine in real templates only.
- `re.sub` replacement strings process backslashes — when injecting JS with `\n`, prefer `String.fromCharCode(10)`.
- `dashboard_builder.py` uses CRLF line endings — preserve them.
- Three.js is `https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js` everywhere.
- The Vimi widget's outside-click close means programmatic opens need the `_justOpened` guard.
- **`/ppc*` page routes are now 301-redirects to `/gtm*`; the `/ppc*` ASSET/DATA aliases still serve.** Generated dashboards/breadcrumbs now link `/gtm`. There is no `url_for('ppc')` (routes referenced by literal path).
- **`continue-on-error: true`** on every fetch step hides crashes behind green checks — read the log body.
- **Config sections may be null** in `CONFIG_YAML` — always read with `(config.get("x") or {})`, never `config.get("x", {})`.
- The in-app refresh progress bar is a client-side estimate, not real progress.
- GitHub Actions runner IPs are blocked by Google News RSS (use GDELT/SerpAPI for news; jobs/creative-hiring still on RSS and degraded from CI).
- **Action node-version warning** (`actions/checkout@v4`, `actions/setup-python@v5` forced onto Node 24 because Node 20 is deprecated) is harmless/informational — a one-line bump to `checkout@v5`/`setup-python@v6` silences it (not done yet; optional).

---

## ROADMAP / PHASE 2 (agreed, not yet built)

1. **(deferred) Vimi on SEO:** the widget is missing on `seo.html` (and the SEO `embed.html` wrapper) — "one intelligence layer" should be everywhere.
2. **(deferred) Real Vimi write-backs:** implement `/api/vimi/{hubspot,slack,sheets,deck}` so the confirm-before-write flow stops silently no-op'ing ("queued"). UI + confirm + audit already ship.
3. **(deferred) Signal Tracker discoverability:** add a Signal Tracker / "Target Accounts" card to the hub (today it's only in the palette + via the GTM page's card → `/accounts` hop).
4. **(deferred) Login demo data → real:** wire the login bubble's "last visit" to a real last-page cookie, or revert it.
5. Admin usage + cost meter, server-side **permissions** for writes, persistent **audit log** (currently client-side).
6. True scheduled briefings posted into Vimi chat; optional server-side SSE streaming (today streaming is a client-side reveal).
7. Activate **CSG HIGH signals** end-to-end (CSG Sheets exist via `fetch_csg_sheets.py`; confirm IDs in `CONFIG_YAML`).
8. **Real refresh progress** wired to the actual GitHub Action status (replace the client-side estimate).
9. **Durable creative-hiring source** (a real jobs/ATS API) — Google News RSS is blocked from CI and GDELT isn't job-board data.
10. **(optional) SerpAPI** for highest-quality news (needs a paid key in `CONFIG_YAML`); bump deprecated GitHub Action versions.
11. **Remove the demo proxy people** (`_DEMO_PROXY_PEOPLE` + pin line) and the **login demo greeting** vars when the demo/webinar is over.
12. Richer feeds (funding/M&A APIs, LinkedIn hiring), outcome analytics, accessibility/perf, automated tests.

---

## LATEST STATE (end of this cycle)
`main` HEAD `002f191`. v9 added the **favicon** to `login.html` + `context_graph.html`, **centered** the Context-Graph topbar link, renamed the GTM cards to **"Target Accounts"** + **"LinkedIn Intelligence"**, changed the Signal Tracker **breadcrumb to GTM**, and converted the legacy **`/ppc*` page URLs into 301-redirects to `/gtm*`** (asset/data `/ppc*` aliases still serve). The big work was a **refresh-pipeline reliability/performance overhaul**: hard **timeouts** on all RSS fetches, **parallel prefetch** (16→6 workers), **retry+backoff+jitter**, a **circuit breaker** (stops hour-long blocked runs), **null-safe config access in `main.py`** (Healthcare fetch had been silently crashing), and a **news-source migration from the IP-blocked Google News RSS to the free, CI-reachable GDELT DOC API** (SerpAPI still preferred when a key is present). HIGH (Sheets) signals + News (GDELT) now flow reliably; creative-hiring (jobs) remains on RSS and degraded from CI (open item). Vimi backend write-integrations remain Phase 2. **Reminder:** rotate/revoke any GitHub token shared into a chat after use.
