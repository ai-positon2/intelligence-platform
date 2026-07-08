# Intelligence by Position² — Full Context (v17 · July 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v17 supersedes all earlier context files (v1–v16)** — those are stale; ignore/delete them.

**What v17 adds on top of v15/v16** (v16 was never committed to the repo; it lived only as a pasted doc — v17 folds it in and brings everything current):
1. **Three surfaces + two-tier auth.** Google sign-in is now open to *any* Google account, so the app split into three surfaces (public marketing / signed-in member area `/app` / internal staff `/p2/*`). All internal staff routes were **relocated under `/p2/*`** (old top-level paths 301-redirect).
2. **New public member workspace at `/app`** — a signed-in SaaS-style workspace (left sidebar, dashboard shell, particle bg) for *any* Google user, exposing a curated set of agents. Three are wired to live SEO tools; the rest are "request access" cards.
3. **Agent run history (Postgres).** Every real agent run is saved with its **full output** to a Postgres table; users get an **Execution History** list + detail view at `/app/history`. A per-user/per-agent run cap (10) is enforced.
4. **Industries feature hidden site-wide** (routes unregistered → 404; data kept in code, not deleted).
5. **Admin dashboards renamed** (display + URLs): Internal Usage, Anonymous Traffic, Public Page Analytics, Public Agent Usage.
6. **Body font → Bricolage Grotesque** on public-facing pages; white overscroll bounce bars fixed site-wide.
7. **This cycle's fixes** (see "RECENT WORK"): a full **mobile-optimisation audit** (6 layout bugs fixed), the **Execution History card height** fix, and a **trim of the agent access-request Slack notification** (removed footer + link unfurl).

**Latest `main` HEAD at end of this cycle: `68050a3`** (always `git pull` to confirm; Railway auto-deploys each push).

---

## WHAT THIS IS

**Intelligence by Position²** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 is a B2B digital-marketing agency: SEO & organic growth, performance/paid media, paid social, content, brand & website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymises website visitors, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), and helps reps act via an embedded AI assistant called **Vimi** (visible label **GTM**).

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` → live at `https://seo-apps-production-37a6.up.railway.app`
- **Hosting:** Railway — auto-deploys on every push to `main` (~90s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; signal data refreshes via GitHub Actions. `seo-apps` is its own Railway service (a few min to build).
- **Admins (`ADMIN_EMAILS`):** `krishna.ladha@position2.com`, `sudheer.d@position2.com`, `reporting@position2.com`.

### ★ THREE SURFACES + TWO-TIER AUTH (the biggest structural fact)
Google SSO is open to **any** Google account (not just `@position2.com`). That forced a three-surface split with two auth tiers:

| Surface | Who | Auth decorator | Namespace | Theme |
|---|---|---|---|---|
| **1. Public marketing site** | Logged-out prospects | none | top-level (`/`, `/agents`, `/platform`, …) | always dark |
| **2. Member workspace `/app`** | ANY signed-in Google user | `@login_required` | `/app/*` | dark (has particle bg) |
| **3. Internal staff app `/p2/*`** | `@position2.com` only | `@position2_required` | `/p2/*` (hub, gtm, seo, admin, …) | light/dark toggle |

- Old top-level internal paths (e.g. `/hub`, `/gtm/…`, `/admin/…`) **301-redirect** to their `/p2/…` equivalents.
- Auth decorators live in `app.py`: `login_required` (line ~1243), `admin_required` (~1251, = position2 + admin email), `position2_required` (~1265).

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py            ← Flask server (~3,600+ lines): auth (3 tiers), routes for all 3 surfaces,
│                       AGENTS/APP_AGENTS/SIGNALS/INDUSTRIES data, OpenAI (Vimi), insights,
│                       marketing routes, /api/demo-request, /api/track|atrack|identify,
│                       /app/* member workspace + run history, /p2/* internal app + admin,
│                       Postgres run-history layer, SEO Studio proxy, favicons
├── main.py           ← Weekly orchestrator for HEALTHCARE account (Sheets HIGH + News LOW) -> data/tracker.db
├── fetch_csg_*.py    ← CSG fetchers (news/jobs/sheets)
├── weekly_digest.py  ← Ranks companies; writes reports/opportunities_<acct>.csv
├── tracker/          ← signal pipeline pkg (news_client.py [GDELT/SerpAPI + RSS], news_relevance.py,
│                       signal_score.py, dashboard_builder.py, sheets_client.py, notifier_slack.py, ...)
├── ad_intelligence/  ← built React app (Vite) served directly by Flask; assets under /p2/gtm/ad-intelligence/assets/
├── static/
│   ├── css/ds-tokens.css, ds-components.css   ← internal design tokens + shared components (+ [data-theme=light])
│   ├── css/gtm.css, hub.css, seo.css, linkedin.css, admin.css  ← per-surface styles (+ light blocks)
│   ├── favicon.svg / favicon.png / favicon.ico / logo.svg / logo.png  ← "Arena" mark (white-circle badge)
│   ├── logo-mark.svg   ← Arena star ONLY (transparent, padded) for on-page logo on a theme-aware circle
│   ├── js/theme.js     ← light/dark toggle (localStorage 'p2-theme', applied in <head>) — internal app only
│   ├── js/visitor_track.js, pfx_bg.js, anonymous_visitors.js, linkedin.js
├── templates/
│   ├── agents.html          ← ★ THE SINGLE SHARED MARKETING TEMPLATE (public site). {% if page %} variants:
│   │                          home, agents, agent, platform, signals, solutions, integrations, resources,
│   │                          security, login, privacy, terms  (industries/industry/iagent exist but are 404'd)
│   ├── app.html             ← ★ /app member workspace home (sidebar + dashboard + agent cards)
│   ├── app_base.html        ← ★ shared shell for ALL /app pages (topbar w/ search+bell+gear, sidebar)
│   ├── app_embed.html       ← ★ /app/<slug>/use — embeds a live seo-apps tool (chrome-less), relays run-finished
│   ├── app_history.html     ← ★ /app/history — Execution History list
│   ├── app_history_detail.html ← ★ /app/history/<id> — a saved run's full output
│   ├── app_settings.html    ← ★ /app/settings — connected agents, theme, account
│   ├── call_sentiment.html  ← "Sentiment Pulse" dashboard (internal, /p2/gtm/sentiment-pulse)
│   ├── hub.html, gtm.html, seo.html, accounts.html, embed.html, 403.html
│   ├── admin_usage.html, admin_visitors.html, admin_members.html, admin_agent_runs.html, admin_requests.html
│   ├── linkedin_scraper.html, anonymous_visitors.html
│   └── ppc_chat_widget.html ← SHARED Vimi chat widget (internal app)
├── reports/          ← dashboard.html / dashboard_csg.html (Signal Tracker dashboards, generated + Vimi-customised)
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml
```

### Deploy & data model
- **Code/UI** push to `main` → Railway redeploys (~90s). **No hot reload** — the Flask dev server does not reload Jinja templates without a full restart (matters only for local testing, not prod).
- **Signal data** refreshed by GitHub Actions (`refresh-dashboards.yml`), which commits updated DBs + dashboards.
- **Google Sheets is the primary data store** for: login log + page views ("Page Views" tab), demo/access requests ("Demo Requests" tab), anonymous visitor analytics ("Visitor Analytics" tab), person-level identities ("Visitor Identities" tab) — all via `GOOGLE_SA_JSON` service account against `LOGIN_LOG_SHEET_ID` / `DEMO_REQUEST_SHEET_ID`. Signal Tracker HIGH signals come from separate Sheets in `CONFIG_YAML`. Ad Intelligence reads `AD_INTEL_SHEET_ID`.
- **Postgres** (Railway `DATABASE_URL`) is the store for **agent run history** (full outputs) — see below. This is the only non-Sheets datastore.

---

## ★ SURFACE 2 — THE `/app` MEMBER WORKSPACE (new since v15)

A signed-in, SaaS-style workspace for **any** Google user. Shell = `app_base.html` (topbar with search + notification bell + settings gear, left sidebar with agent nav + a pinned "Workspace" group = History/Settings at the bottom, particle background). Home = `app.html`.

### `/app` routes (all `@login_required`)
| Route | Purpose |
|---|---|
| `GET /app` | Workspace home. Sections: **Launch an agent** (connected agents), **More agents** (request-access cards), **Activity** (recent runs). Shows run counts + remaining cap. |
| `GET /app/<slug>` | Agent detail page (Details panel + sidebar CTA). |
| `POST /app/<slug>/request-access` | For not-yet-connected agents → posts a Slack access request (see below). |
| `GET /app/<slug>/use` | Embeds the live seo-apps tool chrome-less (`app_embed.html`, `?embed=1&pt=<SERP_PLATFORM_TOKEN>`). |
| `POST /app/<slug>/use/log-run` | Records that a run started (only when the tool's own CTA is clicked, not on any iframe click). |
| `POST /app/<slug>/use/finish-run` | Receives the tool's finished output and saves it to Postgres run history. |
| `GET /app/history` | **Execution History** list (all of the user's saved runs). |
| `GET /app/history/<int:run_id>` | A single saved run's full stored output. |
| `GET /app/settings` | Connected agents, theme, account. |

### `APP_AGENTS` (in app.py ~line 1379; `APP_AGENTS_BY_SLUG` maps slug→dict)
- **3 fully-connected agents** (have a `seo_slug` pointing at the seo-apps tool, so they render live and save run history):
  1. `keyword-finder` → **Keyword Finder** (seo_slug `keyword-research`, accent cyan/indigo, `_SVG_COMPASS`)
  2. `content-brief-generator` → **Content Brief Generator** (seo_slug `article-recommendation`, violet/pink, `_SVG_BRIEF`)
  3. `content-enhancer` → **Content Enhancer** (seo_slug `article-enhancement`, emerald/cyan, `_SVG_ALCHEMY`)
- The **rest of the marketing agents** appear as **request-access-only cards** (NO `seo_slug` → every reader treats them as "not connected"): ABM Signal Tracker, Generative Search Visibility, Anonymous Website Visitors, Technical SEO & GEO Auditor, AI Readiness Auditor, Content Authority Optimizer, Competitor SEO Intelligence, Local Visibility Builder, Search Term Intelligence, LinkedIn Intelligence, Competitor Ad Intelligence, Pipeline Command Center, GBP QC Agent, On-Page SEO Auditor, Hub & Spoke Architect, Robots & Index Monitor.
- `seo_slug` is the **single source of truth for "connected."** No `seo_slug` = request-access card. (There is NO more "Coming soon" placeholder card — it was removed.)

### ★ Agent run history (Postgres) — the run-history feature
- **Store:** Postgres via `DATABASE_URL` (Railway). Connection helper `_pg_conn()` (returns `None` if unset). Table auto-created once per process by `_ensure_run_history_table()`:
  ```sql
  CREATE TABLE IF NOT EXISTS agent_run_history (
      id SERIAL PRIMARY KEY, email TEXT NOT NULL, name TEXT,
      agent_slug TEXT NOT NULL, agent_name TEXT, title TEXT,
      output JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
  CREATE INDEX idx_agent_run_history_email ON agent_run_history (email, created_at DESC);
  ```
- **How output arrives:** the three live tools live in the SEPARATE `seo-apps` repo, cross-origin. Each tool's completion handler `postMessage`s `{source:'p2-seo-tool', type:'agent-run-finished', tool, output}` to the parent window. `app_embed.html` relays that to `POST /app/<slug>/use/finish-run`, which calls `_save_agent_run(...)` → JSONB row. `_run_title()` derives a short human label from the output. All failures are swallowed (missing `DATABASE_URL`, conn error) so a DB outage never breaks a run.
- **UI:** `/app/history` lists runs (icon + title + agent name + `display_ts` + arrow, client-side search box, "N of M runs" counter); `/app/history/<id>` shows the full stored output. Reopenable on any device.
- **Run cap:** `AGENT_RUN_CAP = 10` per user **per agent**; remaining shown on the dashboard; an admin usage view (`/p2/admin/agent-runs`) tracks runs. Runs are only counted/logged when the tool is genuinely used (its CTA clicked), not on mere open/iframe click. Legacy agent slugs are normalized when reading so a rename doesn't make past runs vanish.

---

## ★ SURFACE 1 — THE PUBLIC MARKETING SITE

### Single-template architecture — read this first
The **entire public site is rendered from ONE Jinja template, `templates/agents.html`**, via a `{% if page == '...' %}` chain in `<main>`. Routes in `app.py` render this template with a `page` variable. The nav, footer, "Request access" modal (`#nvfov`), video modal (`#vmodal`), animated background (Three.js nebula + 2D fallback), custom cursor, page loader, and big inline `<script>` IIFE are **shared across every public page**. There is a matching `{% elif page %}` chain in `<title>` and a per-page accent dict on `<body data-page style="--page:…;--page2:…">`.

### Public routes (top-level, no auth)
`GET /` (home; redirects to `/p2/hub` if a `@position2.com` user is logged in, `/app` if any other signed-in user), `/login` (page=login; header nav hidden), `/agents` (directory), `/agents/<slug>` (detail; unknown→302 `/agents`), `/platform`, `/signals`, `/solutions`, `/integrations`, `/resources`, `/security`, `/privacy`, `/terms`. Public APIs: `POST /api/demo-request` (Request-access intake → Sheet + Slack + email), `POST /api/atrack` (anon analytics), `POST /api/identify` (gated by `IDENTIFY_TOKEN`). Favicons: `/favicon.ico`, `/favicon.svg`.

**Industries are HIDDEN (v17):** `/industries`, `/industries/<slug>`, `/industries/<islug>/agents/<aslug>` are **unregistered → 404**. The `INDUSTRIES` registry + template blocks (`industries`/`industry`/`iagent`) still exist in code (not deleted, just not routed). `/customers` and `/context-graph` also 404 (removed; do not reintroduce).

### Visual / design system (public site)
Fonts: **Bricolage Grotesque is now the body font** (was display-only); Instrument Serif italic (accents), JetBrains Mono (labels/`--mono`). Tokens in `:root` (`--bg:#040510`; cyan `#22d3ee`, violet `#8b5cf6`, indigo `#6366f1`, pink `#e879f9`, lime `#a3e635`; `--dim*`, `--line*`). Per-page `<body data-page>` accent + ambient nebula, custom cursor, 2-col heroes, colored tiles, card mouse-spotlight via `--mx/--my`. Header nav (hidden on login): **Platform · Signals · Agents · Solutions** (Industries removed from nav; Resources route+footer only).

### Agents — `AGENTS` in app.py (19)
signal-tracker, generative-search-visibility (FLAGSHIP), anonymous-visitors, technical-seo-geo-auditor, ai-readiness-auditor, keyword-opportunity-engine, content-brief-architect, content-authority-optimizer, competitor-seo-intelligence, local-visibility-builder, search-term-intelligence, linkedin-intelligence, ad-intelligence, pipeline-command-center, gbp-qc-agent, on-page-auditor, hub-spoke-architect, robots-monitor, article-enhancer. Categories: Signals, GEO, Web, SEO, Content, Paid, Social, Analytics. (Note: the **public** `AGENTS` list and the **`/app`** `APP_AGENTS` list are different objects with different slugs — `/app` uses friendlier names like "Keyword Finder".)

### Honest-content principle (enforced)
No fabricated logos/quotes/metrics; previews labeled "representative." Only hard stat kept: "Recovers 95%+ of lost visitors."

### "Request access" lead form + notifications
Shared modal `#nvfov` → `POST /api/demo-request`. Notifiers: Sheet, Slack (channel `C0BE016E2E8` = #intelligence-platform-request-access), email. This is DISTINCT from the per-agent access request in `/app` (below).

---

## ★ AGENT ACCESS-REQUEST SLACK NOTIFICATION (trimmed in v17)

Two different Slack posts hit the same #intelligence-platform-request-access channel (`SLACK_CHANNEL_ID` default `C0BE016E2E8`):
1. **Public "Request access" form** → `_demo_request_to_slack()` (app.py ~316): a short plain-text message (Name/Email/Company/Interest/Message). No URL, no card.
2. **`/app` per-agent "Request Access"** → `_agent_access_request_slack_blocks()` + `_agent_access_request_to_slack()` (app.py ~1885): a Block Kit message (header "🙋 New agent access request", Requested by / Agent fields, and a reason quote or "_No reason given_").

**v17 change:** the agent-access message used to end with a divider + a context line linking to `/p2/admin/access-requests`. Slack auto-unfurled that URL into a large OpenGraph hero card (~292 kB image), making every notification long/noisy. Fix (`68050a3`): removed the divider + footer link block (message now ends at the fields/reason), and set `unfurl_links:false, unfurl_media:false` on `chat.postMessage` so nothing unfurls even if a requester types a URL in their reason.

---

## ★ SURFACE 3 — INTERNAL STAFF APP `/p2/*` (`@position2_required`)

All internal staff surfaces live under `/p2/*` (old top-level paths 301-redirect). Shell chrome: topbar + left nav + user-pill menu (with the light/dark toggle) + breadcrumb + particle bg.

- `GET /p2` / `/p2/` / `/p2/hub` — the internal hub landing.
- `GET /p2/gtm` — GTM hub; `GET /p2/gtm/sentiment-pulse` — **Sentiment Pulse** dashboard; `GET /p2/gtm/ad-intelligence` — Ad Intelligence React app.
- `GET /p2/seo` + `/p2/seo/<tool_slug>` — **SEO Studio** (proxies `seo-apps` via `?pt=<SERP_PLATFORM_TOKEN>`).
- `GET /p2/accounts`, `/p2/signal-tracker/<account_id>[/<section>]` — Signal Tracker dashboards.
- **Admin dashboards** (renamed in v16 — display + URL): `/p2/admin/internal-usage` (was Usage), `/p2/admin/anonymous-traffic` (was Visitor Analytics), `/p2/admin/public-page-analytics` (was Member Analytics), `/p2/admin/public-agent-usage` (was Agent Usage), `/p2/admin/agent-runs`, `/p2/admin/access-requests`. Old slugs (`/p2/admin/usage|visitors|members|requests`) still resolve/redirect. Each has a `…/data` JSON endpoint. All `@admin_required`. Shared `static/css/admin.css` (loaded last, overrides inline). KPI cards are clickable (drawer with per-metric explanation + breakdown); 6-colour nth-child accent palette; solid per-accent value numbers (not gradient-clipped). Conversion % excludes `@position2.com`; company inferred from email domain; `p2_vid` captured in the internal login log for visitor-ID linking. `/p2/admin/anonymous-traffic` recent table is unbounded (shows all rows).

### Sentiment Pulse (internal, display-only PROXY DATA)
"Sentiment Pulse" at `/p2/gtm/sentiment-pulse` (renders `call_sentiment.html`). Voice-of-patient sentiment across calls/reviews/surveys, scored per location. No real pipeline — a fully interactive UI mockup (network "Cedar Valley Health", 10 NC/SC/VA locations). Seeded PRNG `mulberry32(20260701)` generates ~1,000 proxy interactions; tuned to a healthy picture (~72% pos, overall ~76/100, NPS +54). KPI cards `.cs-kpi` (per-accent tint, corner glow, pill delta). `--pos:#2dd4aa; --neu:#f5a623; --neg:#fb7185`.

### Ad Intelligence (internal, built React app)
`GET /p2/gtm/ad-intelligence` serves a Vite React build directly (NOT an iframe). Assets under `/p2/gtm/ad-intelligence/assets/…` — the index.html asset refs MUST be that absolute path (a root `/assets/…` ref 404s). Reads `AD_INTEL_SHEET_ID`; if it opens but shows no ads, the Sheet isn't shared with the service account.

---

## ★ BRANDING — the "Arena" mark (stable since v15)
Central bright-green hexagon (`#55be8c`) + three steel-blue petals (`#4a6a7c`) + three dark-green petals (`#53795b`) = a 6-point star. `static/favicon.svg` = star on a **white circle** (THE favicon; keep as-is), mirrored as `logo.svg`; `favicon.png/.ico`, `logo.png` rasterized from it. `static/logo-mark.svg` = the **star only**, transparent, padded inside a `0 0 100 100` viewBox (padding required so a circular `overflow:hidden` container doesn't clip the points). On-page logo = `<img src="/static/logo-mark.svg?v=3">` on a theme-aware circle (`--logo-bg`: `#fff` light / `#151b2e` dark). **Static assets are served at `/static/…`** — referencing a new asset at the root 404s (only `/favicon.svg`,`/favicon.ico` have root routes).

## ★ LIGHT / DARK THEME (internal app only)
`static/js/theme.js` reads `localStorage['p2-theme']` (default dark), sets `document.documentElement[data-theme]` in `<head>` (no flash), exposes `window.P2toggleTheme(event)`. Toggle item injected atop the user-menu dropdown on every internal page. Light = `[data-theme="light"]` overrides in ds-tokens/ds-components/gtm/hub/seo/linkedin/admin CSS + inline blocks in call_sentiment/accounts/embed. Public marketing site + `/app` stay dark. Known follow-up: some heavy custom inline pages still need light-mode polish.

---

## ★ SIGNAL PIPELINE + REFRESH (unchanged from v15; still the live blocker)
"Refresh signals" triggers GitHub Actions `refresh-dashboards.yml` (`workflow_dispatch`, Railway var `GH_DISPATCH_TOKEN`): writes `config.yaml` from secret `CONFIG_YAML` + `service_account.json` from `GOOGLE_SERVICE_ACCOUNT_JSON`; runs fetch steps (Healthcare `main.py`, CSG); rebuilds dashboards + weekly brief; commits. **Blocked until** the repo has GitHub Actions secrets `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON` (SEPARATE from Railway vars) **and both Healthcare Sheets are shared with the SA `client_email` (Viewer)**:
- C-suite: id `16M_DLwIhbKuQAv_Cafxl8krrNWO5iaTrBJdQaN9EI6g`, tab "C-suite signals".
- Funding: id `1nhu07HCyctjs5gfGdchu_n7Rxnpip0xVMwB9nIF_wa0`, tab "Funding signals".
Resilience already in repo: creative/3D-hiring RSS steps removed; faster RSS + GDELT circuit breakers; tolerant config loading. Signal Tracker: Healthcare (~1,251) + CSG (294); importance `type_weight × severity × recency`; 90-day retention.

## VIMI / OPENAI (unchanged)
**Vimi** = embedded AI assistant (`ppc_chat_widget.html`, backend `/api/ppc-chat` + `/api/ppc-upload`); do NOT rename Vimi plumbing (`window.ppcOpen`/`ppc-*`/`_build_ppc_context`); visible label is **GTM**. OpenAI chain: `OPENAI_INSIGHTS_MODEL` → `gpt-5.4` → `OPENAI_MODEL` → `gpt-4o-mini`.

---

## ENVIRONMENT VARIABLES
### Railway (runtime)
`DATABASE_URL` (Postgres, run history), `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` (default `C0BE016E2E8`), `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), `SMTP_*` (unusable on Railway).
### GitHub Actions secrets (refresh workflow) — SEPARATE from Railway
`CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

---

## HOW TO WORK ON THIS (proven-safe workflow)
1. **Clone fresh into the bash sandbox `/tmp` each session.** (In some environments file tools also work on `/tmp`; the safe default is bash/Python string-replace + heredocs.) Sandbox network: **git over `github.com` works, but `api.github.com`, GDELT, OpenAI, RSS, Google APIs are BLOCKED** — you cannot verify live data/visuals from the sandbox. The sandbox may reset mid-session (re-clone + re-run `git config user.email/name`).
2. Edit via Python string-replace/slice scripts (assert exactly-one match) or file-edit tools; new templates via single-quoted heredocs.
3. **Validate before every push:** `python3 -c "import ast; ast.parse(open('app.py').read())"`; Jinja-parse + render each changed template (ChainableUndefined env, stub `url_for`); `node --check` inline `<script>` (strip Jinja first); guard no `{{`/`{%`/`{#` inside `<style>`/`<script>` (scope carefully — several internal templates legitimately read `{{ user.* }}` in HTML); CSS brace balance; tag balance; no duplicate `@app.route`.
4. Push to `main` → Railway deploys ~90s. If rejected, `git pull --rebase origin main` then push. Push token = GitHub classic PAT (`repo`+`workflow`) as `https://x-access-token:<TOKEN>@github.com/...`. **Redact tokens in ALL output (`sed -E 's/ghp_[A-Za-z0-9]+/[REDACTED]/g'`); the user shares a fresh token each session and rotates it after.**
5. Sandbox can't verify visuals/live data. **For real mobile-viewport / rendering checks, use a local preview server with genuine device emulation** (config at `~/.claude/launch.json`, `mcp__Claude_Preview__preview_resize preset:"mobile"` = 375×812) — the claude-in-chrome `resize_window` does NOT change the CSS viewport in this environment (window resizes but `innerWidth`/`matchMedia` stay desktop). To audit auth-gated pages without any auth bypass: render via `app.test_client()` with a fake `session['google_user']`, dump the HTML into `static/_audit/*.html`, view it through the running server; regenerate after each template edit (it's a frozen snapshot). Trust direct DOM/computed-style measurement over a single screenshot when they disagree (stale-capture glitches happen).
6. **Browser favicon + CSS/JS caching is aggressive** — hard-refresh; bump `?v=N` when replacing a cached asset in place.

### Gotchas
- `<title>` has the same `{% elif page %}` chain — anchor searches on body-specific strings.
- `@media (...){#x{...}}` is a `{#` Jinja-comment trap — keep a space. CSS `}}` inside `<style>` is fine.
- `admin.css` loads **last** and intentionally overrides each admin page's inline CSS. `hub.css` uses SPACED selectors (`.topbar {`) unlike most files (`.topbar{`) — grep by value strings if a selector grep misses.
- Flex shrink: a `display:flex` item that must shrink below its content needs its OWN `min-width:0`. CSS custom props set inline only cascade to descendants of that element. `padding` shorthand fully overrides an earlier longhand on all four sides.
- Theme: default = dark (no attribute). Light = `[data-theme="light"]` (`:root[data-theme="light"]` for tokens).
- Prefer solid colours over gradient-clip-to-text for values that must always render.
- The classifier/auto-mode may block writing config in sensitive locations, auth-bypass routes, or clicking buttons that could fire real Slack/Sheets writes — don't work around it; ask the user or pivot to a safe method (test_client dump, etc.). Never test-and-send into Slack yourself.

---

## RECENT WORK (this cycle, HEADs after v16's `0410d4d`)
- `1d0a3c0`…`2af5cb9` — built the **`/app` member workspace**: sidebar + dashboard shell, particle bg, agent cards, per-user/per-agent run cap (10) + admin usage view, agent detail + Details panel polish, pinned Workspace nav group, "Request Access" modal to collect why, Block-Kit access-request Slack message, renamed the 3 live agents to friendly names, dropped the placeholder 4th card, Bricolage body font, fixed white overscroll bars, removed "Coming soon" eyebrow + topbar clock.
- `8519635` — **run history to Postgres** + real History detail view (`agent_run_history` table, finish-run relay from seo-apps tools).
- `f8c324f` — **Industries hidden site-wide** (routes unregistered → 404, data kept).
- `d95a913` — **full mobile-optimisation audit + fixes** (6 bugs, all in shared templates/CSS so each fix propagates): (1) `.sectionpad` had zero horizontal padding on 6+ secondary marketing pages → text flush to the edge (fixed by splitting the padding shorthand into `padding-top`/`padding-bottom`); (2) mobile nav dropdown had no backdrop scrim → hero text bled through (added `.nv-scrim` + body scroll-lock + JS); (3) `app_base.html` `.tb-search` didn't shrink → bell/gear icons pushed off-screen on EVERY `/app` page (added `min-width:0`); (4) History-detail tab buttons had an invisible active gradient (moved `--ac/--ac2` custom props up to a common ancestor); (5) `/p2/hub` injected clock pill + Admin label overflowed the topbar (added a `@media(max-width:600px)` rule inside the JS-injected LUX-kit CSS); (6) shared admin breadcrumb overflowed on all 5 admin pages (added `@media(max-width:640px){.bc{display:none}}` in admin.css). Verified live via element-rect measurement (resize_window unreliable here).
- `f7722d8` — **Execution History card height fix**: `app_history.html` `.hbox` had a hardcoded `min-height:160px` that ballooned the card with a big empty void when there were few runs. Dropped it from the base `.hbox` (padding 10→8px so it hugs its rows), moved the min-height onto a new `.hbox--empty` modifier used only by the no-runs empty state (`.h-empty` given `flex:1`). Verified live: card 160px→92px hugging its single row.
- `68050a3` (current HEAD) — **trimmed the agent access-request Slack post** (removed divider + `/p2/admin/access-requests` footer link → kills the ~292 kB OpenGraph card unfurl; set `unfurl_links/media:false`). Deployed; not self-verified into Slack by design.

---

## OPEN ITEMS / TODO
1. **Signal refresh secrets (highest priority — blocking Healthcare refresh):** set GitHub Actions secrets `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`, share both Healthcare Sheets with the SA `client_email` (Viewer).
2. **Assign real agents to more `/app` cards:** only 3 are wired to live tools; the rest are request-access. Provide a `/p2/seo/<slug>` tool + friendly name to connect more (set `seo_slug`).
3. **Light-theme polish** on heavy custom inline pages (SEO Studio, LinkedIn, Anonymous Visitors, some Sentiment Pulse widgets).
4. **Email (pending):** Gmail domain-wide delegation (`gmail.send`) + enable Gmail API + set `GMAIL_SENDER` → `/p2/admin/email-test` = SENT OK. (Fallback: Resend/SendGrid.)
5. **Ad Intelligence data:** share `AD_INTEL_SHEET_ID` with the SA if it opens but shows no ads.
6. **`SERP_PLATFORM_TOKEN` visible** in the public `/app` embed iframe URL — optional hardening = a server-side proxy.
7. **GBP QC Agent embed:** provision `gbp-qc-agent-production.up.railway.app` + `frame-ancestors` + `?pt=`; never commit the secret zip.
8. **News freshness / reverse-IP:** set `SERPAPI_KEY`, `IPINFO_TOKEN`, `IDENTIFY_TOKEN`.
9. **Light-mode polish for `/app`** if a member-facing theme toggle is ever wanted (currently `/app` is dark-only).
10. **Rotate the GitHub token** shared into chat (standing reminder).

---

## COMPETITOR / SIGNAL RESEARCH + ROADMAP (recorded, not built)
Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala.
- **Gaps:** co-op topic intent (Bombora), review-site intent (G2/Capterra), technographic change, champion job-change tracking (UserGems — highest ROI), hiring-surge, earnings/10-K mining, event attendance, layoffs, product-led usage.
- **Buildable now:** Earnings & Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief.
- **Differentiators:** generative-search/AI-answer visibility (early) + execution (the agency runs the plays). First-party web de-anon partly in place.
