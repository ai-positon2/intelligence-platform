# Intelligence by Position² — Full Context (v8 · June 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v8 supersedes all earlier context files (v1–v7)** — those are stale; ignore/delete them. v8 records the big "PPC → GTM rename + Context Graph goes 3D + login greeting bubble + Anonymous-Visitors realism & performance + UX-flow fixes" work cycle.

---

## WHAT THIS IS

**Intelligence by Position²** is an internal B2B revenue-/sales-intelligence web app for the Position2 agency team (Position2 is a B2B digital-marketing agency: SEO & organic growth, performance marketing, paid social, content, brand & website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymises website visitors, scrapes LinkedIn engagement, tracks competitor ads, ranks prospects by intent, and helps reps act via an embedded AI assistant called **Vimi**.

- **Live URL:** `https://intelligence.position2.com`
- **GitHub:** `https://github.com/ai-positon2/intelligence-platform` (public)
- **Hosting:** Railway — auto-deploys on every push to `main` (~90s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; data refreshes happen via GitHub Actions.
- **Auth:** Google SSO, `@position2.com` only. `@login_required` on protected routes; `@admin_required` for `/admin/*`.
- **Admins:** `krishna.ladha@position2.com`, `sudheer.d@position2.com`
- **Latest `main` HEAD (end of this cycle): `828758b`.**

### The two disciplines
The hub presents two disciplines as cards: **GTM** (go-to-market — was "PPC"; renamed this cycle) and **SEO**. Tagline: "two disciplines · one intelligence layer."

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                       ← Flask server (~3,000 lines): routes, API, OpenAI (Vimi), insights scoring, live-refresh trigger, auth
├── main.py                      ← Weekly orchestrator for the HEALTHCARE account (Sheets HIGH signals + News RSS LOW) -> data/tracker.db
├── fetch_csg_*.py               ← CSG fetchers (news/jobs/sheets)
├── fetch_healthcare_jobs.py     ← Healthcare creative-hiring scraper
├── weekly_digest.py             ← Ranks companies; writes reports/opportunities_<acct>.csv (+ optional AI brief / Slack post)
├── static/                      ← css/ ds-tokens.css, ds-components.css, hub.css, linkedin.css, anonymous_visitors.css, gtm.css, seo.css ; js/ linkedin.js, anonymous_visitors.js
├── data/                        ← tracker.db (healthcare ~1,251), tracker_csg_v2.db (CSG 294), weekly-stats.json
├── templates/
│   ├── hub.html                 ← Home hub (GTM + SEO discipline cards) — sets window.VIMI_NAME; topbar link to Context Graph; includes the Vimi widget
│   ├── context_graph.html       ← Interactive 3D "Context Graph" data-flow page (see below)
│   ├── ppc_chat_widget.html      ← SHARED Vimi chat widget v5 (filename still "ppc_*" — see GTM-rename note) — included on hub + tool pages
│   ├── gtm.html                 ← GTM discipline dashboards page (renamed from ppc.html this cycle)
│   ├── linkedin_scraper.html, anonymous_visitors.html, seo.html
│   ├── accounts.html, admin_usage.html, login.html, embed.html, 403.html
├── reports/
│   ├── dashboard.html (~4.4MB) / dashboard_csg.html (~1.6MB)  ← Signal Tracker dashboards (GENERATED + Vimi-customised; single-line `const DATA`)
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
- **Data** (signals): refreshed by the GitHub Action (RSS/jobs/sheets run on GitHub's runners). The Action commits updated DBs + dashboards.
- **Dashboards are GENERATED + Vimi-customised.** Never hand-edit `reports/dashboard*.html` structure blindly. The refresh pipeline splices the fresh single-line `const DATA = {...};` blob into the committed Vimi HTML (preserving Insights/chat). Markers: `INSIGHTS v10 JS` and `id="vimi-plat"`. Structural JS changes must be applied to BOTH `tracker/dashboard_builder.py` AND the committed `reports/dashboard*.html`.

---

## PPC → GTM RENAME (this cycle — the discipline only)

The **discipline** formerly called "PPC" is now **GTM** (go-to-market), end to end, while old URLs still work and the shared assistant plumbing was deliberately left alone.

**What was renamed (user-facing + the discipline's own code/URLs):**
- Routes: `/gtm`, `/gtm/ad-intelligence`, `/gtm/anonymous-visitors` (+`/data`), `/gtm/linkedin-scraper`. The Python view fn `def ppc()` → `def gtm()` renders `gtm.html`.
- **Old `/ppc*` paths are kept as working aliases** (extra `@app.route` decorators on the same view fns) so generated dashboards (which link `/ppc`), the compiled React Ad-Intelligence app, and bookmarks don't break.
- Files: `templates/ppc.html` → `templates/gtm.html`; `static/css/ppc.css` → `static/css/gtm.css` (via `git mv`, `url_for` updated).
- UI copy/labels: hub card title "PPC"→"GTM", category "Paid Acquisition"→"Go-To-Market", card desc de-PPC'd; command-palette "GTM Tools — Go-to-market intelligence"; breadcrumbs/page titles; GTM page eyebrow "Paid Acquisition" removed and the ghost watermark "PAID MEDIA"→"GTM".

**What was deliberately NOT renamed (and why — do not "fix" these blindly):**
- The **shared Vimi chat-widget plumbing**: file `ppc_chat_widget.html`, routes `/api/ppc-chat` + `/api/ppc-upload`, JS globals `window.ppcOpen/ppcSend/...`, element IDs `ppc-btn`, CSS classes `ppc-*`, and `_build_ppc_context`/`_PPC_CTX_CACHE`. This is the assistant's infra, shared by **all** disciplines (incl. SEO), and is baked into the generated dashboards and the compiled React build. Renaming it would break live chat everywhere for zero user benefit.
- **"PPC" as a service in Vimi's AI prompts** (Position2 sells PPC as a service — `SEO|PPC|Content|Brand|RevOps`). Renaming would corrupt Vimi's recommendations.
- **Data files** (Apollo CSV, `data/linkedin.json`) and `tracker/news_relevance.py` keyword (filters marketing-relevant news). Not website text; editing corrupts data/filtering.

---

## VIMI — THE EMBEDDED AI ASSISTANT (widget, v5 "Vimi Studio") — UNCHANGED this cycle

Shared widget `templates/ppc_chat_widget.html` (one file: CSS + markup + ~1k-line IIFE). Backed by `/api/ppc-chat` (chat) and `/api/ppc-upload` (files). Included on hub, gtm, anonymous_visitors, linkedin_scraper, context_graph. **NOTE: NOT on `seo.html`, `accounts.html`, `admin_usage.html`** (a known gap — see roadmap).

**Design language:** minimal dark canvas, slim glass header (small gradient avatar, name "Vimi", status dot, ghost icon buttons), welcome screen with a Three.js WebGL particle orb + centered greeting "Hi {name}!" + outlined pill quick-replies, glassy single-line composer. Inter (UI) + Sora (display). Light/dark + cozy/compact density.

**Fresh-chat behaviour:** opening Vimi always starts a new chat (welcome); the prior chat is auto-saved to History; empty sessions pruned; first-ever open shows onboarding once. Helper `freshChat()`.

**Feature set (client-side against existing endpoints):** streaming reveal, stop/regenerate, per-message copy/👍👎/save-to-memory, edit & resend, sessions/history drawer, voice (Web Speech) + spoken replies, slash commands (`/leads /email /research /export /digest /visitors /dossier /why`), @-mentions, keyboard (Cmd/Ctrl+K, ↑ to edit, Esc), source-citation pills, sortable tables, inline charts (Chart.js), export CSV/Excel(SheetJS)/JSON/PDF(jsPDF), "open in Gmail draft", contextual actions (dossier card parsed from a ```vimi-card``` JSON block, why-hot, digest, visitor summary), Memory UI, per-user defaults, onboarding, confirm-before-write modal + client audit log, sound/haptics, proactive nudge badge, draggable + resizable panel.

**Key globals / hooks (do not break):** `window.ppcOpen / ppcSend / ppcSugg / ppcNewChat / ppcShowMemory / ppcToggleExpand`; element IDs `k-*` (`k-fab`, `k-panel`, `k-body`, `k-input`, `k-send`, …) plus `ppc-btn`. Reads `window.VIMI_NAME`. Programmatic opens guarded by a `_justOpened` timestamp.

**Write-actions / Phase 2 (still mocked):** HubSpot/Slack/Sheets/deck go through confirm + audit UX and POST to `/api/vimi/{hubspot,slack,sheets,deck}` — **those routes don't exist**, so they degrade to "queued — activates when the connector is live." (Known UX/trust gap — see roadmap.)

**OpenAI model chain:** `OPENAI_INSIGHTS_MODEL` env → `gpt-5.4` → `OPENAI_MODEL` env → `gpt-4o-mini`. Web search via OpenAI Responses `web_search_preview`. There are also account-scoped Vimi endpoints: `/api/vimi-chat/<account>`, `/api/vimi-export`, `/api/insights/<account>`, `/api/company-analysis/<account>`, `/api/generate-email/<account>`, `/api/research-company/<account>`, `/api/decision-makers/<account>`.

---

## CONTEXT GRAPH PAGE (`/context-graph`) — NOW 3D (this cycle)

`templates/context_graph.html` — interactive explainer of how a buyer's data connects and where the intelligence layer sits. Reachable from the hub topbar ("✦ Context Graph") and the command palette.

- **Nodes:** core entities **Person / Account / Deal**; person signals (Pages Visited, Email History, LinkedIn Signals, Intent Score); account signals (CRM Record, Market Signals, Buying Committee, Readiness State); Outreach Sent / Outcome; and the **Vimi intelligence layer** core. Categories colour-coded (person=blue, account=green, deal=orange, intelligence=purple).
- **3D rendering (this cycle):** the DOM nodes are now positioned by a **true 3D projection** — each node has a Z depth (`ZMAP`: left "person-signal" column floats toward the viewer, right "account-signal" column recedes, the Vimi core forward). The whole constellation rotates with **yaw/pitch** (slow auto-spin + cursor) through a **perspective projection** (`FOC≈1300`, `DEPTH≈LW*0.42`, persp clamped 0.5–1.7). Nodes are **billboarded** (always face camera, so text stays crisp), **depth-sorted via z-index**, and core auras scale with depth. Edges/packets/ripples read each node's projected screen position (`n._x/n._y`), so they bend through the same 3D space automatically. Dragging adds a screen-space offset on top of the projection.
- **Detail view (this cycle):** clicking a node opens a **centered modal pop-up** (was a right side-drawer) with a **thin custom scrollbar**. Each node's CTA routes to a **relevant page** (Pages Visited/Person → Anonymous Visitors; LinkedIn/Committee → LinkedIn Scraper; Account/Market/Outcome → Signal Tracker; Deal/CRM → Accounts; Email/Outreach → open Vimi; intelligence nodes → "Ask Vimi"). Side-node vertical spacing widened; cards polished; title has a soft glow.
- **Background (this cycle):** uses the **hub's exact particle engine** (1000-point twinkling sphere + faint cyan link filaments, cursor parallax) rendered into `#bg`.
- Three.js `0.160.0` from jsdelivr.

---

## LOGIN PAGE (this cycle) — pre-login Vimi greeting bubble

`templates/login.html` now shows a **Vimi greeting bubble** (bottom-right, matches the hub bubble) that opens a **self-contained mini-chat** (no backend — chat endpoints are login-gated).
- **Returning users** (who logged in before on this browser) are greeted by their **real name**: the hub writes `localStorage['vimi_login_name']` (from `window.VIMI_NAME`); the login page reads it.
- **Everyone else** sees a demo line (vars at top of the login script: name `Sajjan`, `Acme Corp`, `23rd June`, page `/gtm/anonymous-visitors`): *"Hi {name} 👋 you visited the Website Visitors page and were checking out {company} on {date}. Want to continue from there?"*
- Typing an affirmative (yes/sure/ok/continue) → Vimi replies with a clickable CTA to the page; other input gets a friendly nudge. (Demo content — revert when the demo's over.)

---

## ANONYMOUS VISITORS (`/gtm/anonymous-visitors`) — realism + performance (this cycle)

Server data comes from a Google Sheet via `_fetch_anon_visitors_data()` (people + companies); page shell loads data async; People/Companies tabs with search + filters; click a row → profile drawer → **Vimi Outreach** card (shared VimiEngage code, also on `linkedin_scraper.html`).

**Realism changes (both anonymous_visitors.html + linkedin_scraper.html):**
- The Vimi Outreach card's `pagesViewed()` now generates a **realistic 3–7 pages** per session (was driven by raw data that could read 55), with **varied per-page dwell time** (e.g. `47s`, `2m 13s`); session minutes = sum of dwell; the headline "X pages viewed" matches the detail. The **arrival-timestamp column was removed** (only time-spent shows).
- The CRM-match narrative is now **first-person (Vimi)**: "I checked your HubSpot CRM and found {Name} is already a known contact…".

**DEMO PROXY PEOPLE (temporary — for a live demo):** `app.py` has a clearly-marked block `_DEMO_PROXY_PEOPLE` (10 US-native people) plus a pin line (`people_table = [dict(x) for x in _DEMO_PROXY_PEOPLE] + people_table`) that **pins them to the top** of the People list with today's date. **To restore originals: delete the `_DEMO_PROXY_PEOPLE` block and the pin line** — both are commented as temporary. (User will request removal after the demo.)

**Performance changes:**
- `_fetch_anon_visitors_data(force=False)` is now **TTL-cached (`_ANON_CACHE`, 300s)** — the slow Sheets reads happen only on the first load / once per 5 min. The data route gzips the JSON (`_ANON_GZ`, cached, keyed to the data timestamp, respects `Accept-Encoding`) and sets `Cache-Control: private, max-age=60`. The **Refresh button forces fresh** via `?fresh=1` (`loadData(true)`).
- The People table renders **progressively**: `renderPeopleProgressive()` paints the **first 200 rows instantly**, then fills the rest in 300-row `requestAnimationFrame` batches. A job token (`_pplJob`) **cancels any in-flight fill when the list is re-filtered**, so search/filter stays snappy.

---

## COMMAND PALETTE & GLOBAL NAV (this cycle)

Every working page (hub, gtm, seo, anonymous_visitors, linkedin_scraper, accounts, admin_usage) has a self-contained command palette IIFE (`window.__kpal`) opened with **Cmd/Ctrl+K**. Its `BASE` list now includes **Context Graph** alongside Hub, GTM Tools, SEO Tools, both Signal Trackers, LinkedIn, Ad Intelligence, Anonymous Visitors, Switch Account.
- A visible "⌘ Jump to…" launcher button was added and then **removed** at the user's request — the palette is **keyboard-only (Cmd/Ctrl+K)** again. `window.openPalette` is no longer exposed.

---

## AUTH / UX FLOW FIXES (this cycle)

- **Deep links survive login:** `_login_redirect()` (used by `login_required` + `admin_required`) saves the intended path into `session['next_url']` (skips `/api`, `/auth`, login/logout; open-redirect-guarded); `/auth/google` pops it and returns it instead of always `/hub`.
- **Honest messaging:** first-time visitors see "Please sign in to continue."; only users whose session cookie existed see "Your session expired…".
- **GTM copy** de-PPC'd (hub card desc + palette descriptions).
- **GTM Dashboards** page: "Paid Acquisition" eyebrow removed; heading enlarged to 44px, subtitle to 17px.

---

## ACCOUNTS / SIGNAL-TRACKER PICKER (`/accounts`) (this cycle)

`accounts.html` was a scroll-driven landing (`.ac-pin{height:210vh}` + sticky full-screen hero + scroll cue) so the Healthcare/CSG cards sat ~2 screens down. Now **single-viewport-ish**: the 210vh pin is collapsed (`height:auto`, hero `position:relative`), the scroll cue/tagline are hidden, the decorative `.ac-motif` constellation behind the title is hidden, and there's comfortable top-aligned spacing (~56px gap between hero text and cards). A little natural scroll is fine; the long scroll is gone. (Cards reveal via IntersectionObserver; the pin-scroll JS goes inert at zero scroll progress.)

---

## SIGNAL TRACKER (unchanged)

Two accounts: **Healthcare** (`/signal-tracker/healthcare`, ~1,251 companies) and **CSG** (`/signal-tracker/csg`, 294 companies). `/dashboard/<account>` 301-redirects to `/signal-tracker/<account>`. **Eight signal types** — HIGH (curated Google Sheets): Funding Round, C-Suite Join, C-Suite Exit, Acquisition/M&A, IPO Signal, Subsidiary Change; MEDIUM (auto news/jobs): Product Launch, Partnership, Creative Hiring; LOW: News Mention. **Position² relevance filter** (`tracker/news_relevance.py`, keyword-only). **90-day retention**. **Importance scoring** (`tracker/signal_score.py`): `type_weight × severity × recency (+ multi-intent bonus)` — drives the Insights tab (`/api/insights/<account>`, score ≥ 6.0, top 120) and the weekly digest.

---

## PAGES & ROUTES (key)

| Route | Purpose |
|-------|---------|
| `GET /hub` | Home discipline picker (GTM + SEO cards); Context-Graph topbar link; sets `VIMI_NAME` |
| `GET /context-graph` | 3D interactive data-flow / intelligence-layer explainer |
| `GET /gtm` (alias `/ppc`) | GTM discipline dashboards page |
| `GET /seo` (+ `/seo/<tool_slug>`) | SEO tool shells (SERP app embeds via `embed.html`) |
| `GET /gtm/anonymous-visitors` (+`/data`) (aliases `/ppc/...`) | Visitor de-anonymisation |
| `GET /gtm/linkedin-scraper` (alias `/ppc/...`) | LinkedIn ABM intelligence |
| `GET /gtm/ad-intelligence[/…]` (alias `/ppc/...`) | Competitor Ad Intelligence (built React app) |
| `GET /signal-tracker/<account>[/<section>]` | Signal Tracker dashboards |
| `GET /accounts`, `GET /admin/usage` | account picker, admin usage |
| `POST /auth/google`, `GET /login`, `GET /logout` | Google SSO (returns `session['next_url']` or `/hub`) |
| `POST /api/refresh-dashboard`, `GET /api/refresh-status` | live-refresh trigger + progress |
| `GET /api/insights/<account>` (+`/api/insights-meta`) | Vimi AI brief (scored, important-only) |
| `POST /api/ppc-chat`, `POST /api/ppc-upload` | Vimi chat backend (shared widget) + file upload |
| `/api/vimi-chat/<account>`, `/api/vimi-export`, `/api/generate-email/<account>`, `/api/company-analysis/<account>`, `/api/research-company/<account>`, `/api/decision-makers/<account>` | Vimi actions |
| `GET /api/whoami`, `POST /api/track`, `GET /health` | user info, page-time tracking, health |

---

## ENVIRONMENT VARIABLES (Railway)
`OPENAI_API_KEY`, `OPENAI_MODEL` (default gpt-4o-mini), `OPENAI_INSIGHTS_MODEL` (default gpt-5.4), `GOOGLE_CLIENT_ID`, `SECRET_KEY`, `LOGIN_LOG_SHEET_ID`, `GOOGLE_SA_JSON`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `SERP_PLATFORM_TOKEN`, `GH_DISPATCH_TOKEN`. GitHub Action secrets: `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`. Optional: `SLACK_WEBHOOK_URL`. (No `VIMI_*`/`KAIRO_*`/`PPC_*` env vars exist.)

---

## HOW TO WORK ON THIS (proven-safe workflow)
1. Clone fresh into the bash sandbox `/tmp` each session (the file-tool `…/outputs` path is separate/stale for git, and the file tools can't reach `/tmp`). Sandbox network: **git over `github.com` works, but `api.github.com` and most RSS endpoints are BLOCKED** — workflow_dispatch + live scraping must run in the GitHub Action. `WebSearch` and the workspace `web_fetch` tool have network.
2. Edit real files (mostly `templates/*.html`, `static/*`, `tracker/*.py`, `fetch_*.py`, `app.py`, `scripts/*`). Make edits with Python string-replace scripts run via bash (the file Read/Write/Edit tools only see the `…/outputs` folder, not `/tmp`).
3. **Validate before every push:** `python3 -c "import ast; ast.parse(...)"` for .py; Jinja parse (`jinja2.Environment(...).get_template(...)`) for templates; `node --check` each inline `<script>` (neutralise Jinja `{{ }}`/`{% %}`/`{# #}` first); YAML parse for workflows; confirm dashboards' `const DATA` JSON parses.
4. Push to `main` → Railway deploys ~90s. Data changes also need the Action.
5. Pushing needs a GitHub token (classic w/ `repo`+`workflow`). **Redact tokens from all output and remind the user to revoke after.** If a push is rejected (the weekly Action may push in parallel), `git pull --rebase origin main` then push.

### Gotchas
- `reports/dashboard*.html` are generated + single-line + Vimi-customised — patch the generator AND splice DATA; never hand-edit blindly. Markers: `INSIGHTS v10 JS`, `id="vimi-plat"`.
- **Jinja `{#`/`{{`/`{%` trap:** CSS/JS inside a Jinja-rendered template must not contain `{{`, `{%`, or `{#` (e.g. write `){ #id` with a space in media queries — this bit the command-palette FAB and the context-graph modal). Intentional Jinja (e.g. `window.VIMI_NAME = {{ ... | tojson }}`, `{% include %}`) is fine in real templates only.
- `re.sub` replacement strings process backslashes — when injecting JS that contains `\n`, prefer `String.fromCharCode(10)` (the context-graph shaders use this).
- `dashboard_builder.py` uses CRLF line endings — preserve them.
- Three.js is `https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js` everywhere (login/hub/widget/context-graph).
- The Vimi widget's outside-click close means programmatic opens need the `_justOpened` guard.
- **`/ppc*` routes are aliases of `/gtm*`** — keep both; generated dashboards and the React app still reference `/ppc`. There is **no `url_for('ppc')`** anywhere (routes referenced by literal path).
- The Ad-Intelligence React app loads assets from absolute `/assets/` (no `/gtm` or `/ppc` prefix), so the page mount can be renamed freely.
- **Demo proxy people** in `app.py` (`_DEMO_PROXY_PEOPLE` + pin line) and the **login demo greeting** vars are temporary — remove/revert when the demo ends.

---

## ROADMAP / PHASE 2 (agreed, not yet built)

From the most recent UX audit, the user fixed #1, #2, #6, #7 and **deferred the rest**:
1. **(deferred) Vimi on SEO:** the widget is missing on `seo.html` (and the SEO `embed.html` wrapper) — "one intelligence layer" should be everywhere.
2. **(deferred) Real Vimi write-backs:** implement `/api/vimi/{hubspot,slack,sheets,deck}` (HubSpot/Slack/Apollo/Sheets + deck) so the confirm-before-write flow stops silently no-op'ing ("queued"). UI + confirm + audit already ship.
3. **(deferred) Signal Tracker discoverability:** add a Signal Tracker card to the hub (today it's only in the palette + via the GTM page's "Signals" card → `/accounts` picker hop).
4. **(deferred) Login demo data → real:** wire the login bubble's "last visit" to a real last-page cookie, or revert it.
5. Admin usage + cost meter, server-side **permissions** for writes, persistent **audit log** (currently client-side).
6. True scheduled briefings posted into Vimi chat; optional server-side SSE streaming (today streaming is a client-side reveal).
7. Activate **CSG HIGH signals** (create CSG Google Sheets + add IDs to `CONFIG_YAML`).
8. Richer data feeds (News API, funding/M&A, LinkedIn hiring), outcome analytics, accessibility/perf, automated tests.
9. **Remove the demo proxy people** from `_DEMO_PROXY_PEOPLE` (+ pin line) when the demo is over.

---

## LATEST STATE (end of this cycle)
`main` HEAD `828758b`. The **PPC discipline is now GTM** (routes `/gtm*` + `/ppc*` aliases, `gtm.html`/`gtm.css`, all labels/links), while the shared Vimi widget plumbing (`ppc_chat_widget.html`, `/api/ppc-chat`, `window.ppcOpen`, `ppc-*`) and the "PPC" service taxonomy were intentionally preserved. The **Context Graph renders in true 3D** (per-node Z depth, yaw/pitch perspective projection, billboarded depth-sorted nodes, hub-style particle background, centered modal detail with thin scrollbar, relevant per-node CTAs). The **login page** has a pre-login Vimi greeting bubble + self-contained mini-chat (real name for returning users, demo otherwise). **Anonymous Visitors** got realistic page/dwell data, a first-person CRM narrative, 10 temporary demo proxy people pinned to the top, a 300s server cache + gzip + 60s client cache + force-fresh refresh, and progressive 200-then-background row rendering. **Auth** preserves `next_url` through login with accurate first-visit vs expired messaging. The **command palette** gained a Context Graph entry (visible launcher was removed; Cmd/Ctrl+K only). The **Accounts** picker is single-viewport (210vh pin removed, motif hidden). Vimi backend write-integrations remain Phase 2.
