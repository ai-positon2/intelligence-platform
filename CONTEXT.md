# Position² Intelligence Platform — Full Context

> Paste this whole file as the opening prompt in a new AI chat to give it complete context about the platform. Keep it updated as the platform evolves.

You are helping develop and maintain the **Position² Intelligence Platform** — a B2B sales-intelligence web app for Position2 (a digital marketing agency: SEO, PPC/Performance Marketing, Content, Brand & Website, RevOps).

## Hosting & repo
- **Live URL:** https://intelligence.position2.com (Google OAuth login restricted to `@position2.com`; 7-day Flask sessions).
- **Repo (canonical, monorepo):** GitHub `ai-positon2/intelligence-platform`, branch `main`. Auto-deploys to Railway on push to main.
- **Old repos (archived/superseded):** `krishnaladha/company-signal-tracker` (was the Flask app) and `ai-positon2/ad-intelligence` (was the React app; source branch `claude/festive-mayer-tgmDu`). Both histories were preserved into the monorepo (ad-intelligence via `git subtree` under `apps/ad-intelligence/`).
- **Railway:** single service named "web", builder = NIXPACKS (clean **Python** detect), start command `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120 --workers 2 --threads 4 --graceful-timeout 120`. Custom domain intelligence.position2.com -> Port 8080. Changing the service's Source Repo does NOT change DNS (the domain is bound to the service).

## Tech stack
- Backend: Python 3, Flask, gunicorn. All routing in `app.py`.
- DB: SQLite committed to git — `data/tracker.db` (Healthcare, ~1,251 companies, ~350 signals) and `data/tracker_csg_v2.db` (CSG, ~294 companies, ~491 signals).
- AI: OpenAI. Env `OPENAI_MODEL` (currently `gpt-5.4-mini`). Use `max_completion_tokens` for chat.completions; `max_output_tokens` for the Responses API. Web search uses the Responses API tool, trying `web_search` then falling back to `web_search_preview`, then to a plain completion.
- Frontend: the Signal Tracker dashboards are **large prebuilt static HTML** (Healthcare ~4.3MB, CSG ~1.2MB) with all data embedded as a `const DATA = {...}` JSON blob + vanilla JS + Chart.js. The **Ad Intelligence** app is React 19 + Vite + TypeScript + Tailwind. Fonts: Space Grotesk + Inter.

## Monorepo structure
```
app.py                      # all Flask routes
templates/                  # hub, ppc, seo, accounts, admin_usage, anonymous_visitors,
                            #   login, 403, embed, ppc_chat_widget.html
reports/dashboard.html      # Healthcare dashboard (prebuilt, Kairo-injected)
reports/dashboard_csg.html  # CSG dashboard (prebuilt, Kairo-injected)
data/tracker.db, tracker_csg_v2.db
tracker/                    # pipeline: news_client.py, news_relevance.py, change_detector.py,
                            #   dashboard_builder.py, snapshot_store.py, csv_loader.py,
                            #   sheets_client.py, notifier_slack.py, notifier_sheets.py(unused)
apps/ad-intelligence/       # React SOURCE (Vite base = /ppc/ad-intelligence/)
ad_intelligence/            # BUILT React app, committed, served by Flask
scripts/                    # build-frontend.sh, inject_widget.py,
                            #   ad_intelligence_widget.html, refresh-dashboards.py
.github/workflows/          # build-frontend.yml, refresh-dashboards.yml, weekly_tracker.yml
railway.toml                # gunicorn start; Procfile mirrors it
config.yaml (gitignored, local), service_account.json (gitignored)
apollo-accounts-export.csv, CSG-company-list.xlsx
```

## Key Flask routes
- Nav/auth: `/`, `/login`, `/logout`, `/auth/google`, `/hub`, `/ppc`, `/seo`, `/accounts`
- Dashboards: **`/signal-tracker/<account_id>`** and `/signal-tracker/<account_id>/<section>` (canonical; the active tab is reflected in the URL). Old `/dashboard/...` 301-redirects here.
- `/ppc/ad-intelligence` (+ `/assets/<file>`), `/ppc/anonymous-visitors` (+ `/data`), `/seo/<tool_slug>`, `/admin/usage` (+ `/data`), `/health`
- APIs (all return HTTP 200 with `{"error": ...}` on failure, never an HTML 500; there is a JSON 500 handler for `/api/*`): `/api/track`, `/api/ppc-chat`, `/api/ppc-upload`, `/api/insights-meta/<id>`, `/api/insights/<id>`, `/api/company-analysis/<id>`, `/api/generate-email/<id>`, `/api/research-company/<id>`, `/api/kairo-chat/<id>` (POST), `/api/whoami`, `/api/refresh-dashboard` (POST), `/api/refresh-status`

## Signal Tracker dashboards
- Two accounts: `healthcare` and `csg`. Account picker at `/accounts`.
- Tabs: Overview, Companies, Signals, Trends, **Insights (Kairo)**.
- Signal types: `Funding Round`, `C-Suite Join`, `C-Suite Exit`, `Acquisition / M&A`, `News Mention`, `IPO Signal`. Severity: HIGH | MEDIUM | LOW.
- DB schema (simplified): `companies(apollo_id, name, domain, industry, city, state, ...)` and `alerts_sent(apollo_id, signal_type, signal_detail, severity, sent_at, signal_date, source_url, dry_run)`. `dry_run=0` = real signals.

## Kairo (the AI layer) — branded "Kairo" everywhere, never "ChatGPT"
- **Insights tab** (CSS prefix `ir9-`, dark aurora gradient, premium glass cards, everything clickable -> drawers) has 4 sub-tabs:
  - **Command Center:** gradient hero (headline + 3-sentence brief + "Kairo's take"), 4 count-up KPIs, 3 priority spotlight cards, a clickable Pipeline-composition segmented bar.
  - **Pipeline:** tier-grouped (Critical 80+, High 60-79, Watchlist <60), conic-gradient score rings, momentum badges, "Approach: <title>", signal/service chips. Opening a company auto-loads a **Deep Dossier** via `/api/company-analysis` (score rationale, objections + counters, subject lines, urgency). A scoring-methodology explainer/drawer exists.
  - **Actions:** kanban (Do today / This week / Monitor) with done-tracking persisted in localStorage.
  - **Radar:** intent-distribution donut, momentum donut, account signal-mix bars, market pulse, signal themes, strategic moves, attention flags — all clickable.
- **Ask Kairo** (header button + floating FAB) opens a slide-in chat backed by `/api/kairo-chat` — grounded on the account's signal DB and able to web-search; answers any question, drafts emails, researches companies.
- **Kairo Email** -> `/api/generate-email` (personalized cold email from real signals). **Research with Kairo** -> `/api/research-company` (web-search company research).
- `/api/insights` reads up to **200 signals** (ordered HIGH->MEDIUM->LOW severity, newest first), groups to ~60 companies x 5 signals, and returns: headline, brief, kairo_take, week_priority, market_pulse, strategic_moves, pipeline, actions, outreach, themes, risks. **No revenue/$ figures anywhere** — stripped from prompts and via a recursive `_strip_revenue_fields()`.

## Global Platform header (on every page)
Slim top bar: gradient "Platform" logo -> `/hub`, breadcrumb `Hub > PPC > <page>`, and a user pill (avatar/name + dropdown: Hub, admin-only Usage, Sign out). On templates it is Jinja; on the prebuilt dashboards it is `#kairo-plat` populated via `/api/whoami`; on the React app it is a `PlatformBar` component. Dashboards also have a floating "Ask Kairo" chat FAB (bottom-right). The `/ppc` pages use the older `templates/ppc_chat_widget.html` (-> `/api/ppc-chat`).

## News relevance filtering
`tracker/news_relevance.py` filters at ingestion so only business-relevant news is stored (heuristic keyword/noise scoring; optional batched AI gate via `signals.news_ai_filter` in config; `news_relevance_min_score` is wired through). Stored history was already pruned (Healthcare 817->211, CSG 1,156->215 News Mentions).

## Refresh ("Refresh Dashboard" button)
Opens a redesigned dark modal (4-step flow: HIGH from Sheets, LOW from Google News filtered, Rebuild & score both accounts, Publish) with a **"Run full refresh"** button -> `POST /api/refresh-dashboard` -> triggers the GitHub Action `refresh-dashboards.yml`, which fetches signals for both accounts -> rebuilds dashboards **preserving Kairo** -> prunes news -> commits -> Railway redeploys. A **live progress bar** polls `/api/refresh-status` (reads the Action's real step status: queued -> fetching -> rebuilding -> publishing -> done), shows elapsed time + logs link, and resumes if reopened. Requires Railway env `GH_DISPATCH_TOKEN` (GitHub PAT with `workflow` scope; optional `GH_REPO`, `GH_WORKFLOW`). The fetch needs repo secrets `CONFIG_YAML` and `GOOGLE_SERVICE_ACCOUNT_JSON`.

## Build/deploy automation (CI)
- Railway = clean Python serve; serves the **committed** `ad_intelligence/` build (deploy never depends on Node).
- `.github/workflows/build-frontend.yml`: on `apps/ad-intelligence/**` changes -> builds React (Node 22) -> copies to `ad_intelligence/` + re-injects the chat widget -> commits.
- `.github/workflows/refresh-dashboards.yml`: `workflow_dispatch` + weekly (Mon 08:30 UTC) -> fetch + rebuild + publish.
- `scripts/refresh-dashboards.py`: local one-command rebuild — for each account it prunes news, builds a *plain* dashboard from the DB to a temp file, then **splices only the `const DATA` blob** into the committed Kairo dashboard (preserving all customizations), with marker-checks that refuse to splice if Kairo markers are missing.
- Railway env vars: `GOOGLE_CLIENT_ID`, `SECRET_KEY`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `SERP_PLATFORM_TOKEN`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `GH_DISPATCH_TOKEN`.

## CRITICAL conventions & gotchas
- **Dashboards are 4-5MB** — never edit with tools that truncate; use Python with **256KB chunked writes** and precise string splices/injections.
- **All Kairo customizations live in the committed dashboard HTML, NOT in `tracker/dashboard_builder.py`.** A naive `python main.py --dashboard-only` rebuild produces a *plain* dashboard and **wipes Kairo** — always update data via `scripts/refresh-dashboards.py` / the refresh Action (which splice the DATA blob and preserve Kairo). Folding the Kairo injections into `dashboard_builder.py` is a known pending follow-up.
- CSS/JS namespaces: Insights = `ir9-`; signals infographics = `sgx-`; platform header = `kp-`/`#kairo-plat`; refresh modal = `rfx-`; FAB = `kairo-fab`.
- Always verify before pushing: `python -m py_compile app.py` / `ast.parse`; `node --check` the injected JS; confirm `const DATA` parses as JSON; check insights-section `<div>` open/close balance; confirm Kairo markers present (`INSIGHTS v10 JS`, `id="kairo-plat"`, `id="ir9-chat"`, etc.).
- The local git working copy has occasionally gotten into a corrupt/locked state; the reliable push pattern is: **clone the remote fresh, copy edited files in, commit, push** (and don't delete the clone until the push is confirmed).
- Current state: Kairo "v10.x" (Command Center/Pipeline/Actions/Radar, chat, deep dossier, infographics, Platform header, clickable cards, `/signal-tracker` URLs, live-progress refresh modal). Performance was tuned (rAF-throttled scroll, removed `backdrop-filter` on repeated cards, `content-visibility:auto` on long lists).

When making changes, follow these conventions, preserve Kairo, keep the no-revenue rule, verify, and push to `ai-positon2/intelligence-platform` main.
