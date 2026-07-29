# Intelligence by Position2 - Full Context (v20 - July 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v20 supersedes all earlier context files (v1-v19)** - older versions are stale; ignore any pasted copy.

**What v20 adds on top of v19 (this cycle's work):**
1. **A new client-portal agent type: "external tool."** An agent whose entire backend lives on a completely separate, third-party platform the user built themselves (not this repo, not seo-apps) - only a public frontend URL is shared with this codebase. The portal iframes that URL behind the client's own path so the third-party host is never shown in the address bar. First one: **LinkedIn Strategy Researcher** (competitive LinkedIn analysis - messaging, creative, cadence, an AI-built action plan), backend built on an unrelated AI app-builder platform, frontend at `https://watchtower-by-position2.vercel.app/linkedin.html`.
2. **LinkedIn Strategy Researcher shipped in TWO places, with different metering:**
   - **NorthStar client portal** (`/northstaranesthesia/agents/linkedin-strategy-researcher`): run-metered and capped at `AGENT_RUN_CAP` (10) per user, same as a SERP tool - but see caveat below.
   - **Internal `/p2/gtm` bucket** (`/p2/gtm/linkedin-strategy-researcher`, staff-only): the SAME tool, embedded the same masked way, but **uncapped and unmetered** - a plain internal utility, not a client-facing product.
3. **Client Usage per-person tables redesigned: tabbed Logins / Page views / Agent runs.** Each segment table (Position2 team, client team) now has three tabs that double as that segment's totals for the metric; clicking a tab re-ranks the whole table by it and swaps every row's headline number + caption (`mostly Keyword Finder`, `last seen 07-28`). Backend now reads the Agent Runs sheet per person (scoped to that client's configured agents) and folds in runs-only people who have no tracked page views. Person drawer gained an Agent runs stat + an "Agents used" breakdown + green run-events in the timeline.
4. **The "note beside Use agent" copy was removed** from client agent detail pages (`.cta-note`, all three states) - it was redundant boilerplate ("Opens X, ready to use for Y.").
5. **A real, un-solved metering gap, left deliberately unfixed for now (see Open Items #1):** the external-tool run cap above currently counts a "run" as *opening the Use page*, not as *actually researching a company* - the third-party tool doesn't emit any signal back to us, so page-open is the only event we can see. A postMessage contract + paste-ready prompt for the external platform were drafted to fix this (see that section) but the user has not deployed it yet.

**Latest `main` HEAD at end of this cycle: `1186b41`** (always `git pull` to confirm; Railway auto-deploys each push). `app.py` is now **~7,922 lines / 148 registered routes**.

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 = a B2B digital-marketing agency: SEO/organic, performance/paid media, paid social, content, brand/website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymizes website visitors to company and (where a signal exists) person, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), helps reps act via an embedded AI assistant (**Vimi**, visible label **GTM**), and serves **co-branded client portals** that can also embed **agents built entirely on other platforms.**

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` -> `https://seo-apps-production-37a6.up.railway.app`
- **Third-party agent frontend (NOT our code, NOT our repo):** `https://watchtower-by-position2.vercel.app` - the user builds these on an unrelated AI app-builder platform; we only receive and iframe the public URL. No visibility into, or control over, that codebase.
- **Hosting:** Railway, auto-deploys on every push to `main` (~60-100s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; signal data refreshes via GitHub Actions. `seo-apps` is its own Railway service.
- **Admins (`ADMIN_EMAILS`, app.py ~line 1217):** `krishna.ladha@position2.com`, `sudheer.d@position2.com`, `reporting@position2.com`, `sparikh@position2.com`, `abhilash.dg@position2.com`.

### FOUR SURFACES + TWO-TIER AUTH (the biggest structural fact)
Google SSO is open to **any** Google account. That forces surface separation with two auth tiers, four surfaces total:

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
├── app.py                ← Flask server (~7,922 lines, 148 routes): auth (3 decorators + client gate),
│                            all 4 surfaces, AGENTS/APP_AGENTS/SIGNALS/INDUSTRIES/CLIENTS registries,
│                            OpenAI (Vimi x2 backends), marketing routes, /api/demo-request,
│                            /api/track|atrack|identify, /app/* + run history, /p2/* + admin analytics,
│                            client-portal routes (incl. external-tool agents), internal /p2/gtm
│                            external-tool route, LinkedIn Intelligence (per-sheet), Postgres history
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
│   │        admin_agent_runs.html, admin_requests.html, admin_external_usage.html,
│   │        admin_client_usage.html (cards), admin_client_detail.html (per-client dashboard, NOW tabbed)
│   ├── _admin_menu.html     ← the ONE shared internal admin dropdown (SVG icons, 3 sections)
│   ├── client_base.html     ← shared shell for all client-portal pages (co-branded topbar, tracks /api/track)
│   ├── client_portal.html   ← client home (agent cards grid)
│   ├── client_agent.html    ← client agent detail (note-beside-button copy REMOVED in v20)
│   ├── client_embed.html    ← client agent "use" shell (iframe: SERP tool OR live dashboard OR external tool)
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
  - **Agent Runs** tab (`_AR_TAB`, `A:F`): 0 Timestamp, 1 Date, 2 Email, 3 Name, 4 slug, 5 AgentName. Written by `_log_agent_run`. **No client column** - a run row only knows email + agent slug, not which portal it happened on. Per-client readers (Client Usage) infer client by intersecting with that client's configured `agents` list; this is exact for client-team members (they only ever reach their own portal) and best-effort for Position2 staff who may share an agent across portals/surfaces.
  - **Visitor Analytics** tab: pre-login journey by `p2_vid`.
  - **Demo Requests** tab (`DEMO_REQUEST_SHEET_ID`): access-request form.
- **Postgres** (`DATABASE_URL`): agent run history (full JSON outputs), table `agent_run_history`.
- **SQLite** (committed): `data/tracker.db` (Healthcare), `data/tracker_csg_v2.db` (CSG), `data/tracker_northstar.db` (NorthStar). **Gitignored, real PII, never commit:** `data/identity_graph.db`.
- **Sheets read performance rule (important):** for fast admin dashboards, warm the IP cache concurrently, do concurrent per-thread `values().get()`, and cache ~300s. **Do NOT use `batchGet`** - it returned empty in prod. Several admin endpoints follow this.

---

## SURFACE 4 - CLIENT PORTALS

A per-client, co-branded front door at `/<client-slug>`. Only known slugs get routes (no top-level catch-all, so an unknown path never resolves here).

### `CLIENTS` registry (app.py ~line 2354)
Currently one client: `northstaranesthesia`. Each entry has: `slug`, `name`, `short`, `website`, `logo` (served from `/static/clients/<slug>/...`), `domains` (email domains allowed in addition to `@position2.com`), `accent`/`accent2`, `tagline`, `blurb`, `agents` (ordered list of APP_AGENTS slugs to show), `dashboards` (map of agent-slug -> pre-built static HTML file for that client), `linkedin_sheet` (a Google Sheet ID that makes LinkedIn Intelligence render as a *live* co-branded dashboard), and **`external_tools`** (NEW v20: map of agent-slug -> a full external URL to iframe, see below).

NorthStar: `domains=["northstaranesthesia.com"]`, `accent="#5b9dff"`, `agents=["signal-tracker","linkedin-intelligence","linkedin-strategy-researcher","ad-intelligence","keyword-finder","content-brief-generator","content-enhancer"]`, `dashboards={"signal-tracker": reports/dashboard_northstar_client.html}`, `linkedin_sheet="13V-W-yG5O-OoLJHjxsPKLjrpRyRdk647GgkIGw823oE"`, `external_tools={"linkedin-strategy-researcher": "https://watchtower-by-position2.vercel.app/linkedin.html"}`.

**Real-world usage note (discovered this cycle via the Client Usage dashboard):** as of this writing, **zero** NorthStar-domain people have ever signed in or viewed the portal - every recorded view/login/run on `/northstaranesthesia` is Position2 staff. Worth surfacing to whoever owns the NorthStar relationship.

### Agent types inside a client portal (three, all keyed off the SAME `agents` list)
1. **SERP-connected agent** (`seo_slug` set on the APP_AGENTS entry): iframes the seo-apps tool; run-metered via the `agent-run-started`/`agent-run-finished` postMessage contract (a run = an actual tool run, not a page-open).
2. **Dashboard-backed agent** (`is_dashboard`): a co-branded dashboard (static file or Flask-rendered live route, e.g. LinkedIn Intelligence). Shown **Live**, **never run-metered** - a dashboard has no "runs."
3. **External-tool agent** (`is_external`, NEW v20): the agent's entire backend lives on a platform we don't own or see the code of; only a public URL is shared with us. `_client_external_tool(client, agent_slug)` looks it up in `client["external_tools"]`. Rendered via the SAME `client_embed.html` shell, iframed, host masked behind the portal path - but unlike a dashboard, IS run-metered (capped at `AGENT_RUN_CAP`), because it's a product the client actively uses, not a passive dashboard. **Caveat:** since the external tool doesn't (yet) emit any postMessage back to us, a "run" is currently counted the moment the Use page loads, not the moment the user actually researches a company - see Open Items #1 for the fix in flight.

### Access control + helpers
- `_client_allowed(client, email)`: True if email ends with `@position2.com` OR any client domain.
- `_client_gate(client)`: returns a login redirect (no user) or a 403 `client_denied.html` (not allowed), else None.
- `_client_agent_view(slug, client)`: enriches an APP_AGENTS entry with `connected`, `is_dashboard`, and `is_external`.
- `_client_external_tool(client, agent_slug)`: the external URL for an agent in this client, or None.
- `_client_agents(client)`, `_client_home`, `_client_agent_detail`, `_client_agent_use` (branches: dashboard -> external -> SERP/default), `_client_agent_dashboard` (404s for external agents - no metering bypass), `_client_linkedin_data`, `_client_agent_log_run`, `_client_agent_finish_run`, `_client_history`, `_client_history_detail`.

### Routes (registered per known slug in a loop)
`/<slug>` (home), `/<slug>/history`, `/<slug>/history/<id>`, `/<slug>/agents/<agent_slug>` (detail - no more note-beside-button copy, v20), `/<slug>/agents/<agent_slug>/use` (embed shell - branches to dashboard, external, or SERP), `/<slug>/agents/<agent_slug>/dashboard` (co-branded dashboard, static OR live-rendered; 404 for external agents), `/<slug>/agents/<agent_slug>/dashboard/data` (gated JSON for the live LinkedIn dashboard), `/<slug>/agents/<agent_slug>/use/log-run` + `.../finish-run`.

### Client run metering + history
SERP-tool and external-tool agents in a client portal are run-metered (`AGENT_RUN_CAP=10` per user per agent, shared "Agent Runs" sheet, so they also show on the internal Public Agent Usage admin dashboard). Finished runs save to the same Postgres history and appear on the client's own `/history` page. Dashboard-backed agents are not metered.

### NorthStar dashboards
- **ABM Signal Tracker:** built by `build_northstar_dashboard.py` (loads `northstar-company-details.csv` via `tracker/csv_loader.load_companies` -> `data/tracker_northstar.db` -> `tracker/dashboard_builder.build_dashboard()`), writes `reports/dashboard_northstar.html` and a client variant `reports/dashboard_northstar_client.html` (internal-ops chrome hidden via injected `CLIENT_HIDE_CSS`). Registered in both `ACCOUNTS` (internal `/p2/signal-tracker/northstar`) and `CLIENTS[...]["dashboards"]`.
- **LinkedIn Intelligence (live):** same engine as internal (below), pointed at the client's `linkedin_sheet`.
- **LinkedIn Strategy Researcher (external, v20):** competitive LinkedIn analysis tool - messaging, creative, posting cadence, AI-built action plan. Backend on `watchtower-by-position2.vercel.app`, NOT this repo. Tested against Beta Bionics successfully; a large-company (HubSpot-scale) analysis has a repeatable failure mode where the analysis itself succeeds but the tool's own final "save results" DB write (`UPDATE async_jobs SET status, completed_at, output...`) fails, so the user sees "Analysis couldn't complete." That's a persistence bug inside watchtower's own codebase (not ours to fix - we only have the frontend URL). Also noted in passing: the raw DB error leaks to the client UI, there's no caching, and the failure copy ("temporary hiccup") undersells that it's deterministic for large payloads.

---

## THE EXTERNAL-TOOL PATTERN (new concept in v20, likely to recur)

Sometimes an agent isn't built in this repo OR in seo-apps - the user builds it themselves on a **completely separate, unrelated third-party AI app-builder platform**, and only hands us a public frontend URL (currently a Vercel deployment). This platform has nothing to do with Intelligence Platform's architecture; we have no access to its code, its data, or its ability to emit events, beyond what a cross-origin iframe can see.

**How we integrate one:**
1. Confirm the frontend has no `X-Frame-Options`/CSP `frame-ancestors` blocking iframing (checked via response headers).
2. Add the agent to `APP_AGENTS` like any other (name, icon, colors, copy, tags) - no `seo_slug`.
3. Add its slug to the client's `agents` list, and its full URL to that client's `external_tools` map (client-portal) - or, for an internal-only copy, add a small dedicated route that renders `templates/embed.html` with the URL (see `/p2/gtm/linkedin-strategy-researcher`, `app.py` ~line 2847).
4. `client_embed.html` (client portals) and `embed.html` (internal `/p2` pages) both just iframe the URL - the masking is free, since the browser's address bar shows OUR path, not the iframe's `src`.
5. **Metering is the hard part.** We can't see inside a cross-origin iframe, so we cannot know when the user actually *used* the tool (vs. just opened it) unless the tool itself tells us. The only way to get a true "run" signal is for the external tool to call `window.parent.postMessage({...}, 'https://intelligence.position2.com')` at the moment of a real action, guarded by `if (window.parent !== window)` so it's a no-op standalone. A paste-ready prompt for the external AI platform to add this was drafted this cycle (self-contained, describes only "notify your parent page on real analysis start/finish," no mention of our internal architecture) - **not yet deployed by the user.** Until it is, the pragmatic fallback (used for LinkedIn Strategy Researcher today) is to count a run on page-load of the Use page, which over-counts relative to "actually researched a company" but at least keeps the cap from being silently unenforced.
6. **Internal vs. client copies of the same external tool can have different metering rules** - see LinkedIn Strategy Researcher: capped on the NorthStar portal, fully uncapped on `/p2/gtm` (internal staff tool, not a client deliverable).

---

## LINKEDIN INTELLIGENCE (internal + per-client, multi-sheet) - unchanged since v19

Route `/p2/gtm/linkedin-intelligence` (old `/p2/gtm/linkedin-scraper` 301-redirects). Renders `templates/linkedin_scraper.html`; all content is drawn client-side by `static/js/linkedin.js` from `window.__LI_DATA_URL__` (JSON). The sheet is "one row per person x post engagement," header-mapped (column order can drift safely).

- `_fetch_linkedin_intel_data(force, sheet_id)` + `_linkedin_data_response(sheet_id, force)` with **per-sheet caches** (`_LI_CACHES`, `_LI_GZS`, `_LI_TABS`) so the internal dashboard and each client portal read independent sheets. `_li_first_tab()` auto-detects the first worksheet title (no longer assumes "Sheet1").
- `templates/linkedin_scraper.html` has a `client_mode` flag: when true it hides the internal topbar, the Vimi widget, and the Ctrl-K command palette, and injects a client-gated `data_url`.
- **Employee-vs-external label fix:** the sheet's "Relationship to Target" (Employee/External/Unknown) drives a per-person badge, using `<Client> Employee` (via `li_cfg`/`window.__LI_CFG__`) not a hardcoded "P2".
- **Duplicate-company merge:** companies with the same display name are merged even when the sheet gives them different (or blank) Company IDs.

**Do not confuse this with LinkedIn Strategy Researcher** (v20, external, competitive analysis of OTHER companies' LinkedIn presence) - "LinkedIn Intelligence" (unchanged) is about YOUR OWN post/people engagement data pulled from a Sheet.

---

## ADMIN ANALYTICS (all `@admin_required`, each has a `.../data` JSON endpoint)

The internal admin dropdown lists these. All KPI cards are clickable into detail. The menu itself is one shared partial (see next section).

- **Internal Usage** `/p2/admin/internal-usage` - `@position2.com` staff logins + page views (`_fetch_usage_data(internal=True)`, reads Login Log `A:U`). "Linked to Pre-Login" KPI + merged journey drawer via `p2_vid`. No row caps.
- **External Usage** `/p2/admin/external-usage` - everyone who signed in with a NON-`@position2.com` email (`_fetch_usage_data(internal=False)`, reads the **`Member Signins`** tab, not the Login Log - non-P2 sign-ins are only in Member Signins). Joins the Agent Runs tab so each person carries what they ran; adds email-domain (company) rollups. Rich "People" table (avatar w/ recency dot, name + pre-login badge, company chip, icon metric chips, time on site, first/last seen, device, traffic source), clickable into a full-journey drawer.
- **Client Usage** `/p2/admin/client-usage` - landing = one card per client portal (`_fetch_all_client_summaries`). Per-client dashboard `/p2/admin/client-usage/<slug>` (`_fetch_client_usage(slug)`): reads Page Views filtered by `/<slug>` URL path, splits every metric into **Position2 team vs the client's own team vs "Other"** by email domain.
  - **v20 redesign - tabbed per-person tables.** Each segment panel (Position2 team, client team) has three tabs - **Logins / Page views / Agent runs** - and each tab's own number IS that segment's total for the metric (no separate "totals" row needed). Clicking a tab client-side re-ranks that panel's people by the chosen metric, rebuilds each row's headline number + a proportional bar + a metric-specific caption (`mostly Keyword Finder` on runs, `last seen 07-28` on logins, duration+date on views), and highlights zero-activity rows as dimmed. Tab selection is independent per panel (`TAB_STATE` keyed by segment).
  - **Backend enrichment (v20):** `_fetch_client_usage` now also reads the **Agent Runs** sheet, filters to the agents configured for that client (`client["agents"]`, canonicalized via `_canonical_agent_slug` for renamed agents), and attaches `agent_runs` (count) + `agent_breakdown` (list of `{name, count}`) to every person. People who ran an agent but have zero tracked page views are synthesized into the people list so they never silently vanish from the Agent Runs view. New KPIs: `total_logins`, `total_runs`; new per-segment fields: `logins`, `runs`.
  - **Person drawer (v20):** now shows an Agent runs stat alongside Logins/Page views/Time/Pages, an "Agents used" breakdown list, and green `run` events interleaved with page-view and login events in the activity timeline.
  - Helpers: `_fmt_secs`, `_cu_read_tab`, `_cu_name_map`, `_cu_pretty_name`, `_cu_url_belongs`, `_cu_client_of_url`. Per-slug 300s cache (`_CU_CACHE`, `_CU_ALL_CACHE`).
- **Anonymous Traffic** `/p2/admin/anonymous-traffic` - visitor_intelligence engine; concurrent IP resolve; per-visitor drill-downs; "Signed in later" via `p2_vid`.
- **Public Page Analytics** `/p2/admin/public-page-analytics` (old `/p2/admin/members`) - public member sign-ins + journeys.
- **Public Agent Usage** `/p2/admin/public-agent-usage` (old `/p2/admin/agent-runs`) - per-user/per-agent run counts vs cap.
- **Access Requests** `/p2/admin/access-requests` - the Request Access form + per-agent access requests.

---

## THE SHARED ADMIN DROPDOWN MENU (`templates/_admin_menu.html`) - unchanged since v19

The internal topbar user-menu is **one shared partial** included as the inner content of every internal page's `.dd-items` container (15 templates). `app_base.html` (public `/app`) and `client_base.html` (client portals) keep their OWN small menus - the shared admin partial is internal-only.

- **Consistent inline-SVG icons** (currentColor + fixed size). `theme.js` swaps a sun/moon SVG for the toggle.
- **Three divider-separated sections:** (1) Light mode, Platform Playbook; (2) Internal Usage, External Usage, Client Usage; (3) Anonymous Traffic, Public Page Analytics, Public Agent Usage, Access Requests; then Sign out.
- **Admin items are gated by `{% if is_admin %}`**, from a Flask context processor (`_inject_app_agents`) computing `_get_user().email in ADMIN_EMAILS` template-wide.
- A canonical `.dd-*` baseline + `.dd-item-icon svg` sizing live in `aurora-app.css` (loaded by every internal page, currently `?v=2`).

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

Shell `app_base.html`. `APP_AGENTS` (18+ cards; `keyword-finder`, `content-brief-generator`, `content-enhancer` are the 3 wired to live seo-apps tools via `seo_slug`; `linkedin-strategy-researcher` is external-tool-only, scoped to client portals + internal GTM, not on `/app`; the rest are request-access-only). Run history in Postgres, `AGENT_RUN_CAP=10`. `/app/history` + `/app/history/<id>`. `app_embed.html` relays the cross-origin tool's `postMessage` run-finished to `/app/<slug>/use/finish-run`.

---

## SURFACE 1 - PUBLIC MARKETING SITE (unchanged)

One template `templates/agents.html`, `{% if page %}` chain. Routes: `/`, `/login`, `/agents`, `/agents/<slug>`, `/platform`, `/signals`, `/solutions`, `/integrations`, `/resources`, `/security`, `/privacy`, `/terms`. Unlinked/direct-URL-only: `/industries*`, `/why-intelligence`. Public APIs: `/api/demo-request`, `/api/atrack`, `/api/identify` (token-gated). Honest-content principle: no fabricated logos/quotes/metrics. Request-access modal `#nvfov` -> Sheet + Slack (`#intelligence-platform-request-access`) + email.

---

## SURFACE 3 - INTERNAL STAFF APP `/p2/*`

`/p2/hub`, `/p2/gtm` (+ `/p2/gtm/sentiment-pulse` MOCK data, `/p2/gtm/ad-intelligence` React app, `/p2/gtm/linkedin-intelligence`, and NEW v20 `/p2/gtm/linkedin-strategy-researcher` - external tool, staff-only, uncapped), `/p2/seo` + `/p2/seo/<tool>` (SEO Studio, proxies seo-apps; iframe route-sync via postMessage), `/p2/accounts` + `/p2/signal-tracker/<account_id>`, `/p2/playbook` (old `/p2/context` redirects; template still `context.html`), and the admin dashboards above. Sentiment Pulse = seeded PRNG mock data (Vimi discloses this).

**GTM bucket cards now:** Target Accounts, Ad Intelligence, Anonymous Visitors, LinkedIn Intelligence, **LinkedIn Strategy Researcher (new)**, Job Change Alert (coming soon). Card color themes live in `static/css/gtm.css` (`c-signals`, `c-adint`, `c-visitors`, `c-linkedin`, **`c-lir` new**, `c-job`).

---

## BRANDING + THEME

"Arena" mark: bright-green hexagon `#55be8c` + steel-blue + dark-green petals = 6-point star. `logo-lockup.svg` used in internal topbars. `theme.js` (`localStorage['p2-theme']`, default dark, `window.P2toggleTheme`); public + `/app` + client portals stay dark; only `/p2/*` toggles. Hard sign-out: `/logout` sends `Clear-Site-Data` + explicit cookie deletion + `no-store` (don't touch `session.permanent` after `.clear()`).

---

## ENVIRONMENT VARIABLES

**Railway:** `DATABASE_URL`, `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` (default `C0BE016E2E8`), `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), `APOLLO_API_KEY`, `VI_ENRICH_ON_VIEW` (opt), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt), `SMTP_*` (unusable on Railway).
**GitHub Actions secrets (separate):** `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`.
**Not ours / third-party platform:** whatever the watchtower external-tool platform runs on is entirely outside this env list - we have zero configuration access to it.

---

## HOW TO WORK ON THIS (proven-safe workflow)

1. **Clone fresh into the bash sandbox each session** (work in the scratchpad, e.g. `.../scratchpad/ip_fresh`). Sandbox network: git over `github.com` works, but `api.github.com`, GDELT, OpenAI, RSS, Google APIs are BLOCKED - live data/visuals can't be verified from the sandbox (no `service_account.json` locally either). If the sandbox resets/corrupts, rename the broken dir aside, re-clone, verify against the last known-good hash, then remove the broken copy.
2. Edit via file-edit tools or Python string-replace/slice scripts (assert exactly-one match). New templates via Write.
3. **Validate before every push:** `python3 -c "import ast; ast.parse(open('app.py').read())"`; import the app to catch route collisions AND confirm new routes registered; Jinja-render each changed template (with a fake `user`) to catch template errors; for heavy inline JS, extract and `node --check` (stub undefined globals, strip Jinja first); check `{%if%}/{%endif%}` balance; never put `{{`/`{%`/`{#` inside `<style>`/`<script>` (keep a space in `@media(...){#x`); no duplicate `@app.route`. Where possible, unit-test new data functions by monkeypatching the sheet-reader with synthetic rows (this cycle: stubbed `_cu_read_tab` with fabricated Page Views / login / Agent Runs rows to prove the Client Usage per-person aggregation before ever touching production data).
4. **For visual/UI changes on auth-gated pages, build a throwaway local preview harness** rather than trying to eyeball raw HTML: monkeypatch `_get_user` to return a fake signed-in admin, monkeypatch the sheet-reader with synthetic data, register a couple of `/preview*` routes (or just hit the real routes once auth is faked), run it as a `.claude/launch.json` server, and drive it with the browser tools (screenshot, click, JS-exec) exactly like a live page. Delete/ignore the harness file afterward - it's scratch, not part of the app.
5. Push to `main` -> Railway deploys ~60-100s. If rejected, `git pull --rebase origin main` then push. Push URL = `https://x-access-token:<TOKEN>@github.com/ai-positon2/intelligence-platform` (derive repo path with `git config --get remote.origin.url | sed -E 's#https?://[^/]*/##'`). **Redact tokens in ALL output** (`sed -E 's/ghp_[A-Za-z0-9]+/[REDACTED]/g'`). The user pastes a fresh classic PAT (`repo`+`workflow`) each session and rotates it after - **remind them to rotate at the end of every session.** Push without asking "should I push?" once validated; report the commit hash + a live health/route check after.
6. **Verify live in the authenticated browser.** Auth-gated pages can't be seen from the sandbox; use the Claude-in-Chrome tools (the user's real logged-in Chrome session, `reporting@position2.com` = admin) to navigate + screenshot. **Poll the deploy properly, not with a fragile shell one-liner:** an unauthenticated `curl` of a route only ever shows 302 (login redirect, if position2_required/admin_required) or 404 (route doesn't exist yet); it can NEVER show the authenticated page content, so don't try to `grep` a curl body for template markers to "confirm" a UI change is live - that check is meaningless for gated pages and produced a false positive this cycle. Confirm gated-page deploys by reloading in the authenticated browser and reading the DOM/JS state directly (or, for a route's mere existence, watch the 404->302 flip on an unauthenticated curl, which IS meaningful).
7. Browser caching is aggressive - bump `?v=N` when replacing a cached CSS/JS asset in place.
8. **Standing rename rule** (see auth section) - alias URL 301 AND every read path.
9. **Never use an em dash in any written copy** (page content, UI, docs, commit messages, chat deliverables). Use commas/colons/periods/parentheses.
10. **If an agent's backend lives on a separate, unrelated third-party platform** (not this repo, not seo-apps - the user built it themselves elsewhere), don't write integration instructions that assume access to that codebase. Any prompt/instructions meant to be pasted into that OTHER platform must be self-contained and describe only that tool's own observable behavior (e.g. "postMessage your parent on real user action X") - never reference this platform's internal routes, slugs, or architecture, since that context is meaningless (and potentially confusing or leaky) to a completely different AI system.

### Gotchas
- Two sign-in tabs with DIFFERENT column layouts: Login Log (`A:U`) = staff only; `Member Signins` (`A:T`) = non-staff. External Usage MUST read Member Signins or it shows almost nobody.
- The **Agent Runs** sheet has no client/portal column - per-client run stats are inferred by intersecting with that client's `agents` list, not read directly. Exact for client-only users, approximate for staff who share agents across surfaces.
- Do NOT use Sheets `batchGet` (empty in prod); use concurrent `values().get()` + cache.
- `admin.css` loads last and overrides inline admin CSS. `hub.css` uses spaced selectors.
- Flex item that must shrink below content needs its OWN `min-width:0`; `padding` shorthand overrides longhands.
- `templates/context.html` and `templates/linkedin_scraper.html` are filename remnants of renamed features (Playbook, LinkedIn Intelligence) - don't be misled.
- **Two different things are both called "LinkedIn X"**: LinkedIn Intelligence (unchanged, internal engagement-data dashboard from a Sheet) vs. LinkedIn Strategy Researcher (new v20, external competitive-analysis tool on watchtower). Don't conflate them when editing.
- The classifier/auto-mode may block writing config in sensitive locations, auth-bypass routes, or firing real Slack/Sheets writes - don't work around it; ask or pivot. Never test-send into Slack yourself.
- `zsh` (not bash) does NOT word-split unquoted variables - affects multi-file shell loops; prefer globs.
- macOS sandbox has no `timeout` command; don't rely on it in shell one-liners.
- Flask's `render_template` caches compiled templates process-wide; a local preview harness that edits a template mid-session needs a server restart (not just a page reload) to pick up the change.

---

## OPEN ITEMS / TODO
1. **Deploy the postMessage run-signal for LinkedIn Strategy Researcher (external tool).** A self-contained prompt was drafted for the user to paste into the third-party platform: on a real "analyze this company" action (not the search/resolve step), guarded by `window.parent !== window`, wrapped in try/catch, `postMessage({source:'p2-agent', type:'agent-run-started'}, 'https://intelligence.position2.com')` - plus an optional `agent-run-finished` with the report payload. Once deployed: wire a matching listener into `client_embed.html`'s external branch (mirroring the existing SERP postMessage listener), remove the page-open `_log_agent_run` call in `_client_agent_use`'s external branch, and re-verify the cap only advances on a real analysis, not a page open.
2. **Signal refresh secrets (blocking Healthcare refresh):** set GitHub Actions `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`, share both Healthcare Sheets with the SA `client_email` (Viewer).
3. **NorthStar signals (pending from the user):** the ABM Signal Tracker + LinkedIn dashboards are built; the user said they will send signal content/details to fill in "later."
4. **NorthStar client-side portal adoption is currently zero** (see Client Usage note above) - worth a conversation with whoever owns that relationship; not something code can fix.
5. **Assign real agents to more `/app` + client cards:** only 3 seo-apps tools + 1 external tool are wired to live surfaces; set `seo_slug` or `external_tools` to connect more.
6. **Light-theme polish** on heavy custom inline pages.
7. **Ad Intelligence data:** share `AD_INTEL_SHEET_ID` with the SA if empty.
8. **visitor_intelligence identity graph durability:** `data/identity_graph.db` is on Railway's ephemeral disk - move to a persistent volume or Postgres for long-term person continuity.
9. **Cold-visitor identification** needs a licensed identity feed - not solvable in code; plug point ready.
10. **watchtower's large-company persistence bug** (deterministic `async_jobs` UPDATE failure on big analyses) lives in the external tool's own codebase - not fixable from here; flag to whoever owns that platform if it keeps affecting real client usage.
11. **Advisory security/design audit (do not start without explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID`, cookie flags, HSTS/security headers, untrack committed `data/tracker.db`/`apollo-accounts-export.csv`, CSRF, rate limiting, SSRF/`X-Forwarded-For` hardening; CSS token convergence, adopt `ds-components.css`, self-host CDN libs, a11y.
12. **Rotate the GitHub token** shared into chat (standing reminder).

---

## COMPETITOR / ROADMAP (recorded, not built)
Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala. Gaps: co-op topic intent, review-site intent, technographic change, champion job-change (UserGems), hiring-surge, earnings/10-K mining, event attendance, layoffs, PLG usage. Buildable now: Earnings/Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief. Differentiators: generative-search/AI-answer visibility + agency execution + first-party web de-anon with a real engine.
