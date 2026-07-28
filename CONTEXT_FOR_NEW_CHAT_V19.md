# Intelligence by Position2 - Full Context (v19 - July 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v19 supersedes all earlier context files (v1-v18)** - older versions are stale; ignore any pasted copy.

**What v19 adds on top of v18 (this cycle's work):**
1. **Client Portals - a whole new fourth surface.** Per-client, co-branded, gated workspaces at `/<client-slug>` (first + only one live: `/northstaranesthesia` = NorthStar Anesthesia). Driven by a `CLIENTS` registry; each client gets a curated subset of agents, its own logo/accent, and access restricted to `@position2.com` staff plus that client's own email domain(s).
2. **NorthStar ABM Signal Tracker** built and embedded in its portal (53 companies), plus **NorthStar LinkedIn Intelligence** as a *live* co-branded dashboard reading NorthStar's own engagement sheet (identical UI to the internal /p2 one, only the input sheet differs).
3. **Two new admin analytics pages:** **External Usage** (everyone who signs in with a non-`@position2.com` email) and **Client Usage** (per-client-portal analytics, split Position2-team vs client-team by email domain, with per-person activity timelines and clickable drill-downs).
4. **The internal admin dropdown menu was unified into one shared partial** (`templates/_admin_menu.html`), given a consistent inline-SVG icon set, and grouped into three divider-separated sections. Admin-menu visibility is now driven by an `is_admin` template variable from a context processor (single source of truth = `ADMIN_EMAILS`), fixing per-template drift.
5. **`@position2.com` staff now land on `/p2/hub` automatically** after login (previously the generic `/app`).
6. **Two admins added:** `ADMIN_EMAILS` is now 5 people (added `sparikh@position2.com`, `abhilash.dg@position2.com`).

**Latest `main` HEAD at end of this cycle: `9d40d8a`** (always `git pull` to confirm; Railway auto-deploys each push). `app.py` is now **~7,790 lines / 146 registered routes**.

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 = a B2B digital-marketing agency: SEO/organic, performance/paid media, paid social, content, brand/website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymizes website visitors to company and (where a signal exists) person, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), helps reps act via an embedded AI assistant (**Vimi**, visible label **GTM**), and now serves **co-branded client portals**.

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` -> `https://seo-apps-production-37a6.up.railway.app`
- **Hosting:** Railway, auto-deploys on every push to `main` (~60-100s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; signal data refreshes via GitHub Actions. `seo-apps` is its own Railway service.
- **Admins (`ADMIN_EMAILS`, app.py ~line 1217):** `krishna.ladha@position2.com`, `sudheer.d@position2.com`, `reporting@position2.com`, `sparikh@position2.com`, `abhilash.dg@position2.com`.

### FOUR SURFACES + TWO-TIER AUTH (the biggest structural fact)
Google SSO is open to **any** Google account. That forces surface separation with two auth tiers, now four surfaces total:

| Surface | Who | Auth | Namespace | Theme |
|---|---|---|---|---|
| **1. Public marketing site** | Logged-out prospects | none | top-level (`/`, `/agents`, `/platform`, `/why-intelligence`, ...) | always dark |
| **2. Member workspace `/app`** | ANY signed-in Google user | `@login_required` | `/app/*` | dark |
| **3. Internal staff app `/p2/*`** | `@position2.com` only | `@position2_required` | `/p2/*` (hub, gtm, seo, admin, playbook, ...) | light/dark toggle |
| **4. Client portals `/<slug>`** | `@position2.com` + that client's domain(s) | `_client_gate()` | `/<client-slug>/*` (e.g. `/northstaranesthesia`) | dark, co-branded |

- After login: `@position2.com` -> `/p2/hub`; any other signed-in user -> `/app`. (Both the `auth_google` callback and `index()` enforce this.)
- Old top-level internal paths (`/hub`, `/gtm/...`, `/admin/...`) 301-redirect to `/p2/...`. Renamed sub-paths also keep redirecting.
- **Standing rename rule:** when a persisted URL/slug is renamed, the old one keeps 301-redirecting AND every read path keyed off the old slug is aliased to the new one (a past bug dropped historical runs because only routing was fixed, not the read side).
- Auth decorators in `app.py`: `login_required`, `admin_required` (= position2 + admin email), `position2_required`. Client gating is `_client_gate(client)` (not a decorator).

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                ← Flask server (~7,790 lines, 146 routes): auth (3 decorators + client gate),
│                            all 4 surfaces, AGENTS/APP_AGENTS/SIGNALS/INDUSTRIES/CLIENTS registries,
│                            OpenAI (Vimi x2 backends), marketing routes, /api/demo-request,
│                            /api/track|atrack|identify, /app/* + run history, /p2/* + admin analytics,
│                            client-portal routes, LinkedIn Intelligence (per-sheet), Postgres history
├── visitor_intelligence/ ← de-anonymization engine: resolver.py (IP resolution + connection-type gate +
│                            confidence), pipeline.py (orchestration + Apollo), identity_graph.py
│                            (SQLite person graph, union-find), __init__.py. Tests: python3 -m visitor_intelligence.tests
├── tracker/              ← signal pipeline pkg (news_client, news_relevance, signal_score,
│                            dashboard_builder [build_dashboard()], csv_loader [load_companies()],
│                            sheets_client, apollo_client, ...) - shared by Signal Tracker + client dashboards
├── main.py               ← weekly orchestrator (Healthcare) -> data/tracker.db
├── build_northstar_dashboard.py, build_csg_dashboard.py  ← per-account/client dashboard builders
├── ad_intelligence/      ← built React app (Vite) served by Flask; assets at /p2/gtm/ad-intelligence/assets/
├── static/
│   ├── css/ds-tokens.css, ds-components.css        ← internal design tokens + shared components
│   ├── css/gtm.css, hub.css, seo.css, linkedin.css, admin.css, aurora-app.css, grid-tokens.css,
│   │        client-portal.css                       ← per-surface styles (each with [data-theme=light] blocks)
│   ├── js/theme.js        ← light/dark toggle (localStorage 'p2-theme'); now swaps a sun/moon SVG in #p2ThemeIcon
│   ├── js/linkedin.js     ← LinkedIn Intelligence dashboard renderer (reads window.__LI_DATA_URL__)
│   ├── js/visitor_track.js, pfx_bg.js, aurora.js, anonymous_visitors.js
│   ├── clients/northstaranesthesia/logo-white.svg  ← client logos live here
│   └── logo-lockup.svg, logo-mark.svg, favicon.*   ← "Arena" brand
├── templates/
│   ├── agents.html          ← THE SINGLE SHARED MARKETING TEMPLATE (public site), {% if page %} variants
│   ├── app.html, app_base.html, app_embed.html, app_history.html, app_history_detail.html, app_settings.html
│   ├── hub.html, gtm.html, seo.html, accounts.html, embed.html, context.html (=Playbook), 403.html
│   ├── linkedin_scraper.html   ← serves BOTH /p2/gtm/linkedin-intelligence AND the client LinkedIn dashboard
│   │                              (client_mode flag hides internal chrome; data_url injected per surface)
│   ├── anonymous_visitors.html, call_sentiment.html
│   ├── admin_usage.html, admin_visitors.html, admin_members.html (Public Page Analytics),
│   │        admin_agent_runs.html, admin_requests.html, admin_external_usage.html (NEW),
│   │        admin_client_usage.html (NEW, cards), admin_client_detail.html (NEW, per-client dashboard)
│   ├── _admin_menu.html     ← NEW: the ONE shared internal admin dropdown (SVG icons, 3 sections)
│   ├── client_base.html     ← shared shell for all client-portal pages (co-branded topbar, tracks /api/track)
│   ├── client_portal.html   ← client home (agent cards grid)
│   ├── client_agent.html    ← client agent detail
│   ├── client_embed.html    ← client agent "use" shell (iframe: SERP tool OR live dashboard)
│   ├── client_history.html, client_history_detail.html, client_denied.html
│   └── ppc_chat_widget.html ← shared Vimi chat widget (internal only)
├── reports/          ← dashboard*.html (Signal Tracker dashboards, incl. dashboard_northstar*.html)
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml
```

### Deploy + data model
- **Code/UI** push to `main` -> Railway redeploys (~60-100s). No hot reload locally.
- **Google Sheets is the primary data store.** One spreadsheet (`LOGIN_LOG_SHEET_ID`) holds multiple tabs that drive analytics:
  - **Login Log** (the default/first tab, range `A:U`): `@position2.com` staff sign-ins only (written by `_log_login_to_sheet`). Email @col 5, name @col 6.
  - **`Member Signins`** tab (`_MEMBER_TAB`, range `A:T`): every NON-`@position2.com` sign-in (written by `_log_member_signin`). Email @col 5, name @col 6, picture @col 8, visitor-id @col 9, browser @col 11, OS @col 13, device @col 14. **Column indices differ from the Login Log** - this matters and is mapped per-tab.
  - **Page Views** tab (`A:N`): every tracked page view. Cols: 0 Timestamp(IST), 1 Date, 2 Time, 3 Day, 4 Email, 5 Page Title, 6 Page URL, 7 Seconds, 8 Duration, 9 IP, 10 Browser, 11 OS, 12 Device, 13 Visitor ID. Written by `/api/track`.
  - **Agent Runs** tab (`_AR_TAB`, `A:F`): 0 Timestamp, 1 Date, 2 Email, 3 Name, 4 slug, 5 AgentName. Written by `_log_agent_run`.
  - **Visitor Analytics** tab: pre-login journey by `p2_vid`.
  - **Demo Requests** tab (`DEMO_REQUEST_SHEET_ID`): access-request form.
- **Postgres** (`DATABASE_URL`): agent run history (full JSON outputs), table `agent_run_history`.
- **SQLite** (committed): `data/tracker.db` (Healthcare), `data/tracker_csg_v2.db` (CSG), `data/tracker_northstar.db` (NorthStar). **Gitignored, real PII, never commit:** `data/identity_graph.db`.
- **Sheets read performance rule (important):** for fast admin dashboards, warm the IP cache concurrently, do concurrent per-thread `values().get()`, and cache ~300s. **Do NOT use `batchGet`** - it returned empty in prod. Several admin endpoints follow this.

---

## SURFACE 4 - CLIENT PORTALS (new in v19)

A per-client, co-branded front door at `/<client-slug>`. Only known slugs get routes (no top-level catch-all, so an unknown path never resolves here).

### `CLIENTS` registry (app.py ~line 2354)
Currently one client: `northstaranesthesia`. Each entry has: `slug`, `name`, `short`, `website`, `logo` (served from `/static/clients/<slug>/...`), `domains` (email domains allowed in addition to `@position2.com`), `accent`/`accent2`, `tagline`, `blurb`, `agents` (ordered list of APP_AGENTS slugs to show), `dashboards` (map of agent-slug -> pre-built static HTML file for that client), and `linkedin_sheet` (a Google Sheet ID that makes LinkedIn Intelligence render as a *live* co-branded dashboard).

NorthStar: `domains=["northstaranesthesia.com"]`, `accent="#5b9dff"`, `agents=["signal-tracker","linkedin-intelligence","ad-intelligence","keyword-finder","content-brief-generator","content-enhancer"]`, `dashboards={"signal-tracker": reports/dashboard_northstar_client.html}`, `linkedin_sheet="13V-W-yG5O-OoLJHjxsPKLjrpRyRdk647GgkIGw823oE"`.

### Access control + helpers
- `_client_allowed(client, email)`: True if email ends with `@position2.com` OR any client domain.
- `_client_gate(client)`: returns a login redirect (no user) or a 403 `client_denied.html` (not allowed), else None.
- `_client_agent_view(slug, client)`: enriches an APP_AGENTS entry with `connected` and `is_dashboard`. `is_dashboard` is true when the client has a pre-built dashboard file for the agent OR a live dashboard (`_client_live_dashboard`, currently LinkedIn Intelligence when `linkedin_sheet` is set). Dashboard-backed agents render a co-branded dashboard in-portal, show as **Live**, and are **not run-metered**.
- `_client_agents(client)`, `_client_home`, `_client_agent_detail`, `_client_agent_use`, `_client_agent_dashboard`, `_client_linkedin_data`, `_client_agent_log_run`, `_client_agent_finish_run`, `_client_history`, `_client_history_detail`.

### Routes (registered per known slug in a loop)
`/<slug>` (home), `/<slug>/history`, `/<slug>/history/<id>`, `/<slug>/agents/<agent_slug>` (detail), `/<slug>/agents/<agent_slug>/use` (embed shell), `/<slug>/agents/<agent_slug>/dashboard` (serves the co-branded dashboard HTML, static file OR live-rendered), `/<slug>/agents/<agent_slug>/dashboard/data` (gated JSON for the live LinkedIn dashboard), `/<slug>/agents/<agent_slug>/use/log-run` + `.../finish-run`.

### Client run metering + history
SERP-tool agents in a client portal are run-metered exactly like `/app` (`AGENT_RUN_CAP=10` per user per agent, shared "Agent Runs" sheet, so they also show on the internal Public Agent Usage admin dashboard). Finished runs save to the same Postgres history and appear on the client's own `/history` page. Dashboard-backed agents are not metered.

### NorthStar dashboards
- **ABM Signal Tracker:** built by `build_northstar_dashboard.py` (loads `northstar-company-details.csv` via `tracker/csv_loader.load_companies` -> `data/tracker_northstar.db` -> `tracker/dashboard_builder.build_dashboard()`), writes `reports/dashboard_northstar.html` and a client variant `reports/dashboard_northstar_client.html` (internal-ops chrome hidden via injected `CLIENT_HIDE_CSS`). Registered in both `ACCOUNTS` (internal `/p2/signal-tracker/northstar`) and `CLIENTS[...]["dashboards"]`.
- **LinkedIn Intelligence (live):** same engine as internal (below), pointed at the client's `linkedin_sheet`.

---

## LINKEDIN INTELLIGENCE (internal + per-client, now multi-sheet)

Route `/p2/gtm/linkedin-intelligence` (old `/p2/gtm/linkedin-scraper` 301-redirects). Renders `templates/linkedin_scraper.html`; all content is drawn client-side by `static/js/linkedin.js` from `window.__LI_DATA_URL__` (JSON). The sheet is "one row per person x post engagement," header-mapped (column order can drift safely).

**v19 change - parameterized by spreadsheet ID:**
- `_fetch_linkedin_intel_data(force, sheet_id)` + `_linkedin_data_response(sheet_id, force)` with **per-sheet caches** (`_LI_CACHES`, `_LI_GZS`, `_LI_TABS`) so the internal dashboard and each client portal read independent sheets. `_li_first_tab()` auto-detects the first worksheet title (no longer assumes "Sheet1").
- `templates/linkedin_scraper.html` gained a `client_mode` flag: when true it hides the internal topbar, the Vimi widget, and the Ctrl-K command palette, and injects a client-gated `data_url`. The internal route passes `client_mode=False` + `url_for('linkedin_scraper_data')`; the client route renders it with `client_mode=True` + `/<slug>/agents/linkedin-intelligence/dashboard/data`.
- **Employee-vs-external label fix:** the sheet's "Relationship to Target" (Employee/External/Unknown) drives a per-person badge. For the internal (Position2) sheet it reads "Position2 Employee"; for a client sheet it now reads "<Client> Employee" (passed via `li_cfg`/`window.__LI_CFG__`), not a hardcoded "P2".
- **Duplicate-company merge:** companies with the same display name are merged even when the sheet gives them different (or blank) Company IDs.

---

## ADMIN ANALYTICS (all `@admin_required`, each has a `.../data` JSON endpoint)

The internal admin dropdown lists these. All KPI cards are clickable into detail. The menu itself is one shared partial (see next section).

- **Internal Usage** `/p2/admin/internal-usage` - `@position2.com` staff logins + page views (`_fetch_usage_data(internal=True)`, reads Login Log `A:U`). "Linked to Pre-Login" KPI + merged journey drawer via `p2_vid`. No row caps.
- **External Usage** `/p2/admin/external-usage` (NEW v19) - everyone who signed in with a NON-`@position2.com` email (`_fetch_usage_data(internal=False)`, reads the **`Member Signins`** tab, not the Login Log - that was the key bug: non-P2 sign-ins are only in Member Signins). Joins the Agent Runs tab so each person carries what they ran; adds email-domain (company) rollups. **Top of the page is a rich, beautiful "People" table** (right after the KPI cards): per person = avatar w/ recency dot, name + pre-login badge, company chip, icon metric chips (logins/agent-runs/page-views, page-views with a proportional bar), time on site, first/last seen (relative + absolute), device (browser/OS/device), traffic source. Rows click into a full-journey drawer (pre-login -> sign-in -> post-login pages -> agent runs, all timestamped). Per-user fields incl. `page_views`, `browser`, `os`, `device`.
- **Client Usage** `/p2/admin/client-usage` (NEW v19) - landing = one card per client portal (`_fetch_all_client_summaries`: page-views + people + last-active per client). Per-client dashboard `/p2/admin/client-usage/<slug>` (`_fetch_client_usage(slug)`): reads Page Views filtered by `/<slug>` URL path and **splits every metric into two audiences by email domain - Position2 team vs the client's own team** (plus an "Other" bucket). Includes per-person **activity timelines** (page views w/ timestamps + login events merged from both sign-in tabs), a portal-wide recent-activity feed, top pages (with unique-viewer counts), and browser/device rollups. The dashboard's cards/rows are clickable into a slide-in drawer: a person -> their login+pageview timeline; a top page -> who viewed it and when; the Views KPI -> full activity feed; the People KPI -> everyone. Names enriched from both sign-in tabs (email@5, name@6), falling back to a prettified email local-part. Helpers: `_fmt_secs`, `_cu_read_tab`, `_cu_name_map`, `_cu_pretty_name`, `_cu_url_belongs`, `_cu_client_of_url`. Per-slug 300s cache (`_CU_CACHE`, `_CU_ALL_CACHE`).
- **Anonymous Traffic** `/p2/admin/anonymous-traffic` - visitor_intelligence engine; concurrent IP resolve; per-visitor drill-downs; "Signed in later" via `p2_vid`.
- **Public Page Analytics** `/p2/admin/public-page-analytics` (old `/p2/admin/members`) - public member sign-ins + journeys.
- **Public Agent Usage** `/p2/admin/public-agent-usage` (old `/p2/admin/agent-runs`) - per-user/per-agent run counts vs cap.
- **Access Requests** `/p2/admin/access-requests` - the Request Access form + per-agent access requests.

---

## THE SHARED ADMIN DROPDOWN MENU (`templates/_admin_menu.html`, new in v19)

The internal topbar user-menu was previously copy-pasted across ~14 templates in two class variants (`dd-item` and `tb-dd-item`) with mixed emoji icons, causing visual drift and a missing item on the LinkedIn page. Now it is **one shared partial** included as the inner content of every internal page's `.dd-items` container (15 templates: hub, gtm, seo, accounts, embed, context, call_sentiment, anonymous_visitors, linkedin_scraper, and the 6 admin pages). `app_base.html` (public `/app`) and `client_base.html` (client portals) keep their OWN small menus - the shared admin partial is internal-only.

- **Consistent inline-SVG icons** (currentColor + fixed size, so identical on every page regardless of that page's own CSS). `theme.js` swaps a sun/moon SVG for the toggle.
- **Three divider-separated sections:** (1) Light mode, Platform Playbook; (2) Internal Usage, External Usage, Client Usage; (3) Anonymous Traffic, Public Page Analytics, Public Agent Usage, Access Requests; then Sign out.
- **Admin items are gated by `{% if is_admin %}`.** `is_admin` comes from a Flask **context processor** (`_inject_app_agents`) that computes `_get_user().email in ADMIN_EMAILS` and exposes it template-wide, alongside `app_agents` and `google_client_id`. This replaced a hardcoded 3-email list that had been duplicated in ~9 templates (the cause of "abhilash can't see admin menu" - it was a template drift bug, not a permissions bug).
- A canonical `.dd-*` baseline + `.dd-item-icon svg` sizing live in `aurora-app.css` (loaded by every internal page). When editing `aurora-app.css`, bump its `?v=N` cache-buster across templates (currently `?v=2`).

---

## VIMI - THE EMBEDDED AI ASSISTANT (unchanged since v18)

Two backends, both branded "Vimi": `/api/ppc-chat` (widget `ppc_chat_widget.html`, label **GTM**, `@position2_required`) and `/api/vimi-chat/<account_id>` (per-account, via `window.irChat`). `_build_ppc_context()` loops BOTH signal DBs (Healthcare + CSG) with live counts (60s cache). `_VIMI_PLATFORM_KNOWLEDGE` grounds feature questions (and discloses Sentiment Pulse is mock data). Web-search fallback via `_responses_web_search()`. Model chain: `OPENAI_INSIGHTS_MODEL` -> `gpt-5.4` -> `OPENAI_MODEL` -> `gpt-4o-mini`. Never guess; never mix Healthcare and CSG in one answer.

---

## ANONYMOUS VISITORS / DE-ANON ENGINE (unchanged since v18)

`visitor_intelligence/` package. Company-level: multi-signal IP resolution + **connection-type hard gate** (business/education/government identifiable; isp/mobile/hosting/proxy gated out), noisy-OR confidence, Apollo enrichment (free-tier default; Apollo opt-in), 0-100 intent. Person-level: persistent SQLite identity graph (`identity_graph.py`, union-find), waterfall resolver (first-party -> Apollo people-match -> pluggable `IdentityProvider` like `CoopFileProvider` via `VI_COOP_FILE`). **Never fabricates a person.** Honest boundary: cold-stranger ID needs a licensed feed (RB2B/Vector/LiveRamp) or owned co-op; the plug point is ready. Env: `APOLLO_API_KEY`, `VI_ENRICH_ON_VIEW` (default off), `VI_COOP_FILE`, `VI_GRAPH_DB` (default `data/identity_graph.db`, gitignored).

---

## PRE-LOGIN / POST-LOGIN STITCHING (`p2_vid`, since v18)

Page Views + login tabs carry a `p2_vid` cookie column. Anonymous Traffic + Internal Usage show "Linked to Pre-Login" KPIs and merged journey drawers. Old rows predating the column show "unlinked" - expected.

---

## SURFACE 2 - `/app` MEMBER WORKSPACE (unchanged)

Shell `app_base.html`. `APP_AGENTS` (18 cards; `keyword-finder`, `content-brief-generator`, `content-enhancer` are the 3 wired to live seo-apps tools via `seo_slug`; the rest are request-access-only). Run history in Postgres, `AGENT_RUN_CAP=10`. `/app/history` + `/app/history/<id>`. `app_embed.html` relays the cross-origin tool's `postMessage` run-finished to `/app/<slug>/use/finish-run`.

---

## SURFACE 1 - PUBLIC MARKETING SITE (unchanged)

One template `templates/agents.html`, `{% if page %}` chain. Routes: `/`, `/login`, `/agents`, `/agents/<slug>`, `/platform`, `/signals`, `/solutions`, `/integrations`, `/resources`, `/security`, `/privacy`, `/terms`. Unlinked/direct-URL-only: `/industries*`, `/why-intelligence`. Public APIs: `/api/demo-request`, `/api/atrack`, `/api/identify` (token-gated). Honest-content principle: no fabricated logos/quotes/metrics. Request-access modal `#nvfov` -> Sheet + Slack (`#intelligence-platform-request-access`) + email.

---

## SURFACE 3 - INTERNAL STAFF APP `/p2/*` (unchanged structure)

`/p2/hub`, `/p2/gtm` (+ `/p2/gtm/sentiment-pulse` MOCK data, `/p2/gtm/ad-intelligence` React app, `/p2/gtm/linkedin-intelligence`), `/p2/seo` + `/p2/seo/<tool>` (SEO Studio, proxies seo-apps; iframe route-sync via postMessage), `/p2/accounts` + `/p2/signal-tracker/<account_id>`, `/p2/playbook` (old `/p2/context` redirects; template still `context.html`), and the admin dashboards above. Sentiment Pulse = seeded PRNG mock data (Vimi discloses this).

---

## BRANDING + THEME

"Arena" mark: bright-green hexagon `#55be8c` + steel-blue + dark-green petals = 6-point star. `logo-lockup.svg` used in internal topbars. `theme.js` (`localStorage['p2-theme']`, default dark, `window.P2toggleTheme`); public + `/app` + client portals stay dark; only `/p2/*` toggles. Hard sign-out: `/logout` sends `Clear-Site-Data` + explicit cookie deletion + `no-store` (don't touch `session.permanent` after `.clear()`).

---

## ENVIRONMENT VARIABLES

**Railway:** `DATABASE_URL`, `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` (default `C0BE016E2E8`), `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), `APOLLO_API_KEY`, `VI_ENRICH_ON_VIEW` (opt), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt), `SMTP_*` (unusable on Railway).
**GitHub Actions secrets (separate):** `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

---

## HOW TO WORK ON THIS (proven-safe workflow)

1. **Clone fresh into the bash sandbox each session** (work in the scratchpad, e.g. `.../scratchpad/ip_fresh`). Sandbox network: git over `github.com` works, but `api.github.com`, GDELT, OpenAI, RSS, Google APIs are BLOCKED - live data/visuals can't be verified from the sandbox (no `service_account.json` locally either). If the sandbox resets/corrupts, rename the broken dir aside, re-clone, verify against the last known-good hash, then remove the broken copy.
2. Edit via file-edit tools or Python string-replace/slice scripts (assert exactly-one match). New templates via Write.
3. **Validate before every push:** `python3 -c "import ast; ast.parse(open('app.py').read())"`; import the app to catch route collisions AND confirm new routes registered; Jinja-render each changed template (with a fake `user`) to catch template errors; for heavy inline JS, extract and `node --check` (stub undefined globals, strip Jinja first); check `{%if%}/{%endif%}` balance; never put `{{`/`{%`/`{#` inside `<style>`/`<script>` (keep a space in `@media(...){#x`); no duplicate `@app.route`. Where possible, unit-test new data functions by monkeypatching the sheet-reader with synthetic rows.
4. Push to `main` -> Railway deploys ~60-100s. If rejected, `git pull --rebase origin main` then push. Push URL = `https://x-access-token:<TOKEN>@github.com/ai-positon2/intelligence-platform` (derive repo path with `git config --get remote.origin.url | sed -E 's#https?://[^/]*/##'`). **Redact tokens in ALL output** (`sed -E 's/ghp_[A-Za-z0-9]+/[REDACTED]/g'`). The user pastes a fresh classic PAT (`repo`+`workflow`) each session and rotates it after - **remind them to rotate at the end of every session.** Push without asking "should I push?" once validated; report the commit hash + a live health/route check after.
5. **Verify live in the authenticated browser.** Auth-gated pages can't be seen from the sandbox; use the Claude-in-Chrome tools (the user's real logged-in Chrome session, `reporting@position2.com` = admin) to navigate + screenshot. Poll the deploy first: an unauthenticated `curl` of a NEW route returns 302 (login redirect) once live, 404 while the old build is still up - watch for the 404->302 flip before browser-verifying.
6. Browser caching is aggressive - bump `?v=N` when replacing a cached CSS/JS asset in place.
7. **Standing rename rule** (see auth section) - alias URL 301 AND every read path.
8. **Never use an em dash in any written copy** (page content, UI, docs, commit messages, chat deliverables). Use commas/colons/periods/parentheses.

### Gotchas
- Two sign-in tabs with DIFFERENT column layouts: Login Log (`A:U`) = staff only; `Member Signins` (`A:T`) = non-staff. External Usage MUST read Member Signins or it shows almost nobody.
- Do NOT use Sheets `batchGet` (empty in prod); use concurrent `values().get()` + cache.
- `admin.css` loads last and overrides inline admin CSS. `hub.css` uses spaced selectors.
- Flex item that must shrink below content needs its OWN `min-width:0`; `padding` shorthand overrides longhands.
- `templates/context.html` and `templates/linkedin_scraper.html` are filename remnants of renamed features (Playbook, LinkedIn Intelligence) - don't be misled.
- The classifier/auto-mode may block writing config in sensitive locations, auth-bypass routes, or firing real Slack/Sheets writes - don't work around it; ask or pivot. Never test-send into Slack yourself.
- `zsh` (not bash) does NOT word-split unquoted variables - affects multi-file shell loops; prefer globs.

---

## OPEN ITEMS / TODO
1. **Signal refresh secrets (blocking Healthcare refresh):** set GitHub Actions `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`, share both Healthcare Sheets with the SA `client_email` (Viewer).
2. **NorthStar signals (pending from the user):** the ABM Signal Tracker + LinkedIn dashboards are built; the user said they will send signal content/details to fill in "later."
3. **Assign real agents to more `/app` + client cards:** only 3 are wired to live tools; set `seo_slug` to connect more.
4. **Light-theme polish** on heavy custom inline pages.
5. **Ad Intelligence data:** share `AD_INTEL_SHEET_ID` with the SA if empty.
6. **visitor_intelligence identity graph durability:** `data/identity_graph.db` is on Railway's ephemeral disk - move to a persistent volume or Postgres for long-term person continuity.
7. **Cold-visitor identification** needs a licensed identity feed - not solvable in code; plug point ready.
8. **Advisory security/design audit (do not start without explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID`, cookie flags, HSTS/security headers, untrack committed `data/tracker.db`/`apollo-accounts-export.csv`, CSRF, rate limiting, SSRF/`X-Forwarded-For` hardening; CSS token convergence, adopt `ds-components.css`, self-host CDN libs, a11y.
9. **Rotate the GitHub token** shared into chat (standing reminder).

---

## COMPETITOR / ROADMAP (recorded, not built)
Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala. Gaps: co-op topic intent, review-site intent, technographic change, champion job-change (UserGems), hiring-surge, earnings/10-K mining, event attendance, layoffs, PLG usage. Buildable now: Earnings/Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief. Differentiators: generative-search/AI-answer visibility + agency execution + first-party web de-anon with a real engine.
