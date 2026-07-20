# Intelligence by Position2 - Full Context (v18 - July 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v18 supersedes all earlier context files (v1-v17)** - v17.md has been deleted from the repo; ignore any pasted copy of it as stale.

**What v18 adds on top of v17:**
1. **Vimi (the embedded AI assistant) was made materially smarter and more precise.** Its live-data context builder had a real bug (CSG account signals were silently never fetched, and Healthcare showed a hardcoded stale count); both are now fixed with live per-account counts. Vimi also gained a large "ground truth" platform-knowledge block (so it answers questions about how the platform's own features work, precisely, instead of guessing) and a web-search fallback chain, matching the pattern its sibling backend already had.
2. **Anonymous Website Visitors got a real de-anonymization engine.** The old single IPinfo-string lookup was replaced by a `visitor_intelligence/` package: multi-signal IP resolution, a connection-type hard gate (the main false-positive control), explainable confidence scoring, free-tier company enrichment by default with Apollo as an explicit opt-in "Enrich further" action, and a persistent person-level identity graph with deterministic clustering. Ships with an honest, explicit boundary: it cannot identify a cold anonymous stranger without a licensed identity feed, and does not pretend to.
3. **Anonymous Traffic dashboard performance + accuracy fixed.** Load time cut from 2-3 minutes to fast by resolving visitor IPs concurrently and skipping redundant RDAP lookups once IPinfo already gates the connection type; a mislabeling bug that called regional ISPs, proxies, and hosting providers "companies" was fixed; every KPI card now drills into real per-visitor lists.
4. **`/p2/context` was renamed to `/p2/playbook` and rebuilt from scratch** as an 8-chapter, launcher-card visual story (bento grids, spotlight explorer, clickable accordions) - "SEO fleet" was renamed to "SEO Suite" throughout its copy. Old `/p2/context` URLs still 301-redirect.
5. **New unlinked public comparison page at `/why-intelligence`** positioning the platform against SEO suites, AI content/SEO agents, and AI-visibility monitors, with an animated hero and a fact-checked comparison matrix. Direct-URL-only by design, like the Industries pages.
6. **LinkedIn Intelligence rebuilt** on a new live engagement Google Sheet with a richer, more colorful dashboard UI, and its URL renamed from `/p2/gtm/linkedin-scraper` to `/p2/gtm/linkedin-intelligence` (old URL 301-redirects).
7. **Pre-login and post-login activity are now stitched together** via a `p2_vid` cookie column added to the Page Views/login-log sheets, surfaced as "Linked to Pre-Login" KPIs and a merged click-through journey drawer on Internal Usage and Anonymous Traffic.
8. **Hard sign-out fixed** (`Clear-Site-Data` header + explicit cookie deletion) so a returning visitor can no longer get silently re-authenticated by a stale cookie.
9. **Pipeline Command Center agent removed** everywhere (public site, `/app`, Playbook).
10. **Admin dashboard "Agent Usage" renamed to "Public Agent Usage"** (URL now `/p2/admin/public-agent-usage`; old `/p2/admin/agent-runs` 301-redirects), and Internal Usage's row caps were removed entirely (shows every login/page view, not just the newest 2000).

**Latest `main` HEAD at end of this cycle: `6f01805`** (always `git pull` to confirm; Railway auto-deploys each push). `app.py` is now ~6,780 lines / 112 registered routes.

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 is a B2B digital-marketing agency: SEO and organic growth, performance/paid media, paid social, content, brand and website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymizes website visitors down to company and, where a signal is available, person, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), and helps reps act via an embedded AI assistant called **Vimi** (visible label **GTM**).

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` -> live at `https://seo-apps-production-37a6.up.railway.app`
- **Hosting:** Railway, auto-deploys on every push to `main` (~90s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; signal data refreshes via GitHub Actions. `seo-apps` is its own Railway service (a few minutes to build).
- **Admins (`ADMIN_EMAILS`):** `krishna.ladha@position2.com`, `sudheer.d@position2.com`, `reporting@position2.com`.

### THREE SURFACES + TWO-TIER AUTH (the biggest structural fact, unchanged since v16)
Google SSO is open to **any** Google account, not just `@position2.com`. That forced a three-surface split with two auth tiers:

| Surface | Who | Auth decorator | Namespace | Theme |
|---|---|---|---|---|
| **1. Public marketing site** | Logged-out prospects | none | top-level (`/`, `/agents`, `/platform`, `/why-intelligence`, ...) | always dark |
| **2. Member workspace `/app`** | ANY signed-in Google user | `@login_required` | `/app/*` | dark (particle bg) |
| **3. Internal staff app `/p2/*`** | `@position2.com` only | `@position2_required` | `/p2/*` (hub, gtm, seo, admin, playbook, ...) | light/dark toggle |

- Old top-level internal paths (e.g. `/hub`, `/gtm/...`, `/admin/...`) **301-redirect** to their `/p2/...` equivalents. So do the renamed sub-paths noted above (`/p2/context` -> `/p2/playbook`, `/p2/gtm/linkedin-scraper` -> `/p2/gtm/linkedin-intelligence`, `/p2/admin/agent-runs` -> `/p2/admin/public-agent-usage`, etc.) - this is a deliberate, standing pattern: **when a persisted URL/slug is renamed, the old one keeps 301-redirecting rather than 404ing, and any code that reads back data keyed by the old slug is updated to alias it to the new one too** (a past bug let a renamed `/app` agent slug drop historical runs from its stat card because only the URL redirect was added, not the read-side alias - fixed, and treated as a standing lesson for future renames).
- Auth decorators live in `app.py`: `login_required`, `admin_required` (= position2 + admin email), `position2_required`.

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                ← Flask server (~6,780 lines, 112 routes): auth (3 tiers), routes for all 3 surfaces,
│                            AGENTS/APP_AGENTS/SIGNALS/INDUSTRIES data, OpenAI (Vimi x2 backends), insights,
│                            marketing routes, /api/demo-request, /api/track|atrack|identify,
│                            /app/* member workspace + run history, /p2/* internal app + admin,
│                            Postgres run-history layer, SEO Studio proxy, favicons
├── visitor_intelligence/ ← NEW (v18): de-anonymization engine package - resolver.py (IP resolution +
│                            connection-type gate + confidence scoring), pipeline.py (orchestration + Apollo
│                            enrichment), identity_graph.py (SQLite person-level identity graph, union-find
│                            clustering), __init__.py (provider wiring). 24 offline tests: `python3 -m
│                            visitor_intelligence.tests`.
├── main.py                ← Weekly orchestrator for HEALTHCARE account (Sheets HIGH + News LOW) -> data/tracker.db
├── fetch_csg_*.py         ← CSG fetchers (news/jobs/sheets)
├── weekly_digest.py       ← Ranks companies; writes reports/opportunities_<acct>.csv
├── tracker/               ← signal pipeline pkg (news_client.py [GDELT/SerpAPI + RSS], news_relevance.py,
│                            signal_score.py, dashboard_builder.py, sheets_client.py, notifier_slack.py,
│                            apollo_client.py [also reused by visitor_intelligence], ...)
├── ad_intelligence/       ← built React app (Vite) served directly by Flask; assets under
│                            /p2/gtm/ad-intelligence/assets/
├── static/
│   ├── css/ds-tokens.css, ds-components.css   ← internal design tokens + shared components (+ [data-theme=light])
│   ├── css/gtm.css, hub.css, seo.css, linkedin.css, admin.css  ← per-surface styles (+ light blocks)
│   ├── favicon.svg / favicon.png / favicon.ico / logo.svg / logo.png  ← "Arena" mark (white-circle badge)
│   ├── logo-mark.svg   ← Arena star ONLY (transparent, padded) for on-page logo on a theme-aware circle
│   ├── js/theme.js     ← light/dark toggle (localStorage 'p2-theme', applied in <head>) - internal app only
│   ├── js/visitor_track.js, pfx_bg.js, anonymous_visitors.js, linkedin.js
├── templates/
│   ├── agents.html          ← THE SINGLE SHARED MARKETING TEMPLATE (public site). {% if page %} variants:
│   │                          home, agents, agent, platform, signals, solutions, integrations, resources,
│   │                          security, login, privacy, terms, why (NEW v18)  (industries/industry/iagent
│   │                          exist but are unlinked/direct-link-only, like why)
│   ├── app.html             ← /app member workspace home (sidebar + dashboard + agent cards)
│   ├── app_base.html        ← shared shell for ALL /app pages (topbar w/ search+bell+gear, sidebar)
│   ├── app_embed.html       ← /app/<slug>/use - embeds a live seo-apps tool (chrome-less), relays run-finished
│   ├── app_history.html     ← /app/history - Execution History list
│   ├── app_history_detail.html ← /app/history/<id> - a saved run's full output
│   ├── app_settings.html    ← /app/settings - connected agents, theme, account
│   ├── call_sentiment.html  ← "Sentiment Pulse" dashboard (internal, /p2/gtm/sentiment-pulse) - MOCK DATA
│   ├── context.html         ← renders BOTH /p2/playbook (current) and the old /p2/context redirect target;
│   │                          NOT renamed as a file even though the routes/labels moved to "Playbook"
│   ├── hub.html, gtm.html, seo.html, accounts.html, embed.html, 403.html
│   ├── admin_usage.html, admin_visitors.html, admin_members.html, admin_agent_runs.html, admin_requests.html
│   ├── linkedin_scraper.html   ← still the filename; serves /p2/gtm/linkedin-intelligence (route renamed, not file)
│   ├── anonymous_visitors.html ← now backed by the visitor_intelligence engine, not a bare IPinfo lookup
│   └── ppc_chat_widget.html ← SHARED Vimi chat widget (internal app, /api/ppc-chat backend)
├── reports/          ← dashboard.html / dashboard_csg.html (Signal Tracker dashboards, generated + Vimi-customized)
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml
```

### Deploy and data model
- **Code/UI** push to `main` -> Railway redeploys (~90s). **No hot reload** - the Flask dev server does not reload Jinja templates without a full restart (matters only for local testing, not prod).
- **Signal data** refreshed by GitHub Actions (`refresh-dashboards.yml`), which commits updated DBs + dashboards.
- **Google Sheets is the primary data store** for: login log + page views ("Page Views" tab, now vid-tagged - see below), demo/access requests ("Demo Requests" tab), anonymous visitor analytics ("Visitor Analytics" tab), person-level identities ("Visitor Identities" tab) - all via `GOOGLE_SA_JSON` service account against `LOGIN_LOG_SHEET_ID` / `DEMO_REQUEST_SHEET_ID`. Signal Tracker HIGH signals come from separate Sheets in `CONFIG_YAML`. Ad Intelligence reads `AD_INTEL_SHEET_ID`. LinkedIn Intelligence reads its own live sheet (see below).
- **Postgres** (Railway `DATABASE_URL`) is the store for **agent run history** (full outputs).
- **SQLite** (`data/tracker.db` Healthcare, `data/tracker_csg_v2.db` CSG, both committed to git) stores Signal Tracker alerts. **NEW v18:** `data/identity_graph.db` (visitor_intelligence's person-level identity graph, real visitor PII) is **gitignored and must never be committed** - Railway's disk is ephemeral, so this resets on redeploy unless pointed at a persistent volume or migrated to Postgres later.

---

## VIMI - THE EMBEDDED AI ASSISTANT (materially improved in v18)

**Vimi has two distinct backends that both carry the "Vimi" brand** - do not conflate or rename either without checking both:

1. **`/api/ppc-chat`** (widget `ppc_chat_widget.html`, embedded in `hub.html`/`gtm.html`/`anonymous_visitors.html`, visible label **GTM**, gated `@position2_required`). This is the officially-documented "Vimi" per this file - do NOT rename its plumbing (`window.ppcOpen`/`ppc-*`/`_build_ppc_context`).
2. **`/api/vimi-chat/<account_id>`** (used in `accounts.html` via `window.irChat`) - a per-account signal-tracker chat, separate code path, same brand.

### What was broken (found + fixed this cycle)
`_build_ppc_context()` (the function that assembles the live-data block injected into `/api/ppc-chat`'s system prompt) only ever read `data/tracker.db` (Healthcare) - `data/tracker_csg_v2.db` (CSG) was never touched, so any question about CSG signals got nothing, silently. It also hardcoded a stale label ("Healthcare - 1,251 companies") as static text instead of a live count. Vimi also had no web-search fallback (unlike its `/api/vimi-chat` sibling) and no grounding in how the platform's own features actually work, so feature/how-it-works questions could be guessed rather than answered.

### What changed
- `_build_ppc_context()`'s Signal Tracker section now loops over **both** account databases (`("Healthcare", "tracker.db")`, `("CSG", "tracker_csg_v2.db")`) and computes company/signal counts live from the DB every call (60s TTL cache, `_PPC_CTX_CACHE`/`_PPC_CTX_TTL`), instead of a single hardcoded Healthcare-only block. Verified against the real local DBs: Healthcare is actually 238 companies with signals / 827 total signals (not the old hardcoded "1,251" - see note below), CSG is 97 companies with signals / 201 total signals (previously absent entirely).
  - **Note on the "1,251" number:** it still appears correctly elsewhere as a *marketing* figure ("1,251 health-tech organizations tracked" on the public site and in Vimi's platform-knowledge block) - that is the total size of the watched Healthcare account universe, a different metric from "companies with an active signal in the tracker," which is what Vimi's live-data section reports. Both are real numbers; they were just being conflated in Vimi's old hardcoded string.
- A new `_VIMI_PLATFORM_KNOWLEDGE` constant (near `_PPC_CTX_CACHE`) is a fact-checked reference block, sourced from this context file, covering: how the visitor de-anonymization engine works (connection-type gate, confidence floor, company-vs-person distinction, free-vs-Apollo enrichment tiering), how Signal Tracker scoring works (`type_weight x severity x recency`, 90-day decay), what Ad Intelligence and SEO Studio do, and an explicit disclosure that **Sentiment Pulse is mock/proxy data**, not a real pipeline, so Vimi never misrepresents it. This block is injected into both `/api/ppc-chat` and `/api/vimi-chat`'s system prompts, immediately before the live-data section.
- **Web-search fallback added to `/api/ppc-chat`**, reusing the existing `_responses_web_search()` helper (OpenAI Responses API + `web_search`/`web_search_preview` tool) already used by `/api/vimi-chat`. Order: live data -> platform knowledge -> web search -> plain-completion fallback (`_vimi_completion`/`_vimi_model_chain`) -> "I don't know," never a guess.
- Both system prompts got an explicit "never guess, cite the live-data/platform-knowledge section you're answering from, use web search otherwise, say so if still unknown" instruction, plus a rule that Healthcare and CSG signal data must never be mixed together in one answer.
- OpenAI model chain (unchanged): `OPENAI_INSIGHTS_MODEL` -> `gpt-5.4` -> `OPENAI_MODEL` -> `gpt-4o-mini`.

---

## ANONYMOUS VISITORS - REAL DE-ANONYMIZATION ENGINE (new, v18, commit `4996173` + follow-ups)

The `visitor_intelligence/` package is now the real engine behind the "Anonymous Website Visitors" surface (`/p2/gtm/anonymous-visitors` and the `/p2/admin/anonymous-traffic` admin dashboard), replacing a bare IPinfo `_ip_company` string lookup.

**Company-level resolution:** multi-signal IP resolution (IPinfo + reverse DNS/PTR + RDAP), a **connection-type hard gate** - the main false-positive control: `business`/`education`/`government` connection types can be identified, `isp`/`mobile`/`hosting`/`proxy` are gated out as "not identifiable" rather than guessed at - explainable noisy-OR confidence scoring, Apollo firmographic + buying-committee enrichment (reuses `tracker/apollo_client.py`), and 0-100 intent scoring. Enrichment is **free-tier by default**; Apollo is an explicit opt-in "Enrich further" button on the visitor drawer (`VI_ENRICH_ON_VIEW` env flag controls whether it also fires automatically on page view - off by default so dashboards don't silently burn Apollo credits).

**Person-level resolution:** a persistent SQLite identity graph (`identity_graph.py`, path from `VI_GRAPH_DB`, default `data/identity_graph.db` - **gitignored, real PII**) with deterministic union-find clustering. Login events, form submits, and `/api/identify` calls retro-stitch prior anonymous `p2_vid` sessions to a resolved identity. A waterfall resolver tries first-party data, then an Apollo people match, then a pluggable external provider (`CoopFileProvider`, reads a hashed-email-to-identity CSV via `VI_COOP_FILE`). **It never fabricates a person.**

**Honest, explicitly-communicated boundary:** resolving a *cold, never-seen* anonymous visitor to a named stranger is not something buildable in code alone - it requires a licensed identity graph (e.g. RB2B, Vector, LiveRamp) or an owned publisher co-op. The `IdentityProvider` interface is the plug point; wiring a real feed in lights this up with zero code change. Do not scrape LinkedIn or fabricate identity data to work around this.

**Follow-up fixes this cycle (commits `ebf038a`, `c67e1c9`, `43e44e4`, `0250e14`):**
- Fixed the Anonymous Traffic dashboard's 2-3 minute load time by resolving visitor IPs concurrently and skipping RDAP lookups when IPinfo has already gated the connection type (RDAP was the slow path and was often redundant).
- Fixed a mislabeling bug where proxies, hosting providers, and RDAP registrar/handle strings were being surfaced as if they were the visiting company - now only surfaced as a company when the resolver is actually confident it is one.
- Fixed the connection-type gate itself mislabeling regional ISPs (e.g. Tata Teleservices) as businesses.
- Every KPI card on `/p2/admin/anonymous-traffic` now drills into a real per-visitor list instead of an aggregate-only chart for some cards; a visitor's name now surfaces in "Recent activity" once they sign in post-visit (see the `p2_vid` stitching note below).

**Env vars (new):** `APOLLO_API_KEY` (used by both `visitor_intelligence` and the existing Signal Tracker), `VI_ENRICH_ON_VIEW` (`1`/`true`/`yes` to auto-enrich on page view; default off), `VI_COOP_FILE` (path to an external identity feed CSV), `VI_GRAPH_DB` (SQLite path override, default `data/identity_graph.db`).

---

## PRE-LOGIN / POST-LOGIN STITCHING (`p2_vid`, new this cycle)

`Page Views` (the `/api/track` tab) previously stored no visitor-ID column, only email. A trailing `vid` column (from the `p2_vid` cookie, read server-side) is now appended, so every post-login page view on both `/app` and `/p2` is vid-tagged. `_login_events_by_vid()` joins `Member Signins` + the internal login log by vid, so:
- **Anonymous Traffic** (`/p2/admin/anonymous-traffic`) shows a "Signed in later" KPI, a per-visitor Status column (member/staff), and a click-through journey drawer merging pre-login views + the sign-in event + post-login page views into one timeline. A visitor's name now appears in "Recent activity" retroactively once they sign in.
- **Internal Usage** (`/p2/admin/internal-usage`) shows a "Linked to Pre-Login" KPI (replacing what was previously a plain Page Views card) and gives staff the same click-through journey drawer.
- Both dashboards had their row caps **removed entirely** - Internal Usage previously silently dropped the newest activity once a sheet tab passed 2000 (login log) / 1000 (page views) rows because the old cap read the *oldest* rows once the tab grew past it; now reads the full range and shows every login/page view.
- Old rows predating this change will show as "unlinked" - that's expected, the `vid` column didn't exist before this date.

---

## SURFACE 2 - THE `/app` MEMBER WORKSPACE

A signed-in, SaaS-style workspace for **any** Google user. Shell = `app_base.html` (topbar with search + notification bell + settings gear, left sidebar with agent nav + a pinned "Workspace" group = History/Settings at the bottom, particle background). Home = `app.html`.

### `/app` routes (all `@login_required`)
| Route | Purpose |
|---|---|
| `GET /app` | Workspace home. Sections: **Launch an agent** (connected agents), **More agents** (request-access cards), **Activity** (recent runs). Shows run counts + remaining cap. |
| `GET /app/<slug>` | Agent detail page (Details panel + sidebar CTA). |
| `POST /app/<slug>/request-access` | For not-yet-connected agents -> posts a Slack access request. |
| `GET /app/<slug>/use` | Embeds the live seo-apps tool chrome-less (`app_embed.html`, `?embed=1&pt=<SERP_PLATFORM_TOKEN>`). |
| `POST /app/<slug>/use/log-run` | Records that a run started (only when the tool's own CTA is clicked, not on any iframe click). |
| `POST /app/<slug>/use/finish-run` | Receives the tool's finished output and saves it to Postgres run history. |
| `GET /app/history` | **Execution History** list (all of the user's saved runs). |
| `GET /app/history/<int:run_id>` | A single saved run's full stored output. |
| `GET /app/settings` | Connected agents, theme, account. |

### `APP_AGENTS` (18 cards - Pipeline Command Center removed this cycle)
- **3 fully-connected agents** (have a `seo_slug` pointing at a live seo-apps tool, so they render live and save run history): `keyword-finder` (Keyword Finder), `content-brief-generator` (Content Brief Generator), `content-enhancer` (Content Enhancer).
- **The rest are request-access-only cards** (no `seo_slug`): ABM Signal Tracker, Generative Search Visibility, Anonymous Website Visitors, Technical SEO & GEO Auditor, AI Readiness Auditor, Content Authority Optimizer, Competitor SEO Intelligence, Local Visibility Builder, Search Term Intelligence, LinkedIn Intelligence, Competitor Ad Intelligence, GBP QC Agent, On-Page SEO Auditor, Hub & Spoke Architect, Robots & Index Monitor.
- `seo_slug` remains the single source of truth for "connected."

### Agent run history (Postgres) - unchanged from v17
Table `agent_run_history` (JSONB `output`), `_pg_conn()`/`_ensure_run_history_table()`. Cross-origin tools in the SEPARATE `seo-apps` repo `postMessage` `{source:'p2-seo-tool', type:'agent-run-finished', tool, output}`; `app_embed.html` relays to `POST /app/<slug>/use/finish-run` -> `_save_agent_run`. UI at `/app/history` + `/app/history/<id>`. `AGENT_RUN_CAP = 10` per user per agent; legacy agent slugs are normalized when reading so a rename doesn't make past runs vanish (see the standing rename-alias lesson at the top of this doc).

---

## SURFACE 1 - THE PUBLIC MARKETING SITE

### Single-template architecture
The **entire public site is rendered from ONE Jinja template, `templates/agents.html`**, via a `{% if page == '...' %}` chain in `<main>`. Routes in `app.py` render this template with a `page` variable. Nav, footer, "Request access" modal (`#nvfov`), video modal (`#vmodal`), animated background (Three.js nebula + 2D fallback), custom cursor, page loader, and a big inline `<script>` IIFE are shared across every public page.

### Public routes (top-level, no auth)
`GET /` (home; redirects to `/p2/hub` if a `@position2.com` user is logged in, `/app` if any other signed-in user), `/login` (page=login; shows a "you've been signed out" confirmation banner if reached via the hardened `/logout` flow), `/agents` (directory), `/agents/<slug>`, `/platform`, `/signals`, `/solutions`, `/integrations`, `/resources`, `/security`, `/privacy`, `/terms`. Public APIs: `POST /api/demo-request`, `POST /api/atrack`, `POST /api/identify` (gated by `IDENTIFY_TOKEN`, now also feeds the visitor_intelligence identity graph). Favicons: `/favicon.ico`, `/favicon.svg`.

**Unlinked, direct-URL-only pages (deliberate pattern, three of these now):**
- `/industries`, `/industries/<slug>`, `/industries/<islug>/agents/<aslug>` - registry + template blocks exist, routes are registered, but no nav link points to them.
- **NEW v18:** `/why-intelligence` (route `why_intelligence_page`, renders `agents.html` with `page='why'`) - positions the platform against SEO/content suites (Semrush, Ahrefs, Surfer, Clearscope, MarketMuse), AI content/SEO agents (Jasper, Writesonic, SEO.ai, Byword), AI-visibility monitors (Profound, AthenaHQ, Otterly, Peec), and the agency/manual path. Every competitor claim was verified against each vendor's own pricing/feature pages, then **re-verified with a second, independently-sourced pass** (G2/Capterra/docs/reviews instead of vendor pricing pages), which caught and corrected 3 inaccuracies before publishing (a false claim about Semrush's visitor-ID pixel, a false claim about Semrush+Ahrefs ad intelligence, and an overstatement of AI-visibility monitors' content-drafting capability). Deliberately fair: concedes Ahrefs/Semrush's data depth, makes no "first/only" claim on GEO since it is a crowded category, frames the wedge as breadth plus execution (buying signals + visitor de-anon + a team that runs the plays). Has an animated hero orbit graphic and a "Sources and method" block linking every source. Still unlinked/review-only; add nav/footer links if it's ever promoted.

`/customers` and `/context-graph` remain fully removed (404 - do not reintroduce).

### Visual / design system (public site)
Fonts: Bricolage Grotesque (body), Instrument Serif italic (accents), JetBrains Mono (labels/`--mono`). Tokens in `:root` (`--bg:#040510`; cyan `#22d3ee`, violet `#8b5cf6`, indigo `#6366f1`, pink `#e879f9`, lime `#a3e635`). Header nav (hidden on login): Platform / Signals / Agents / Solutions (Industries and Why-Intelligence deliberately not in nav; Resources is route+footer only).

### Agents - `AGENTS` in app.py (18, Pipeline Command Center removed)
signal-tracker, generative-search-visibility (flagship), anonymous-visitors, technical-seo-geo-auditor, ai-readiness-auditor, keyword-opportunity-engine, content-brief-architect, content-authority-optimizer, competitor-seo-intelligence, local-visibility-builder, search-term-intelligence, linkedin-intelligence, ad-intelligence, gbp-qc-agent, on-page-auditor, hub-spoke-architect, robots-monitor, article-enhancer. Note: the public `AGENTS` list and the `/app` `APP_AGENTS` list are different objects with different slugs for the same overlapping set of agents (`/app` uses friendlier names like "Keyword Finder").

### Honest-content principle (enforced)
No fabricated logos/quotes/metrics; previews labeled "representative." Hard stats kept: "Recovers 95%+ of lost visitors," "1,251 health-tech organizations tracked" (the watched account universe - see the Vimi section above for how this differs from live "companies with signals").

### "Request access" lead form + notifications
Shared modal `#nvfov` -> `POST /api/demo-request`. Notifiers: Sheet, Slack (`#intelligence-platform-request-access`), email. Distinct from the per-agent `/app` access request (below).

---

## AGENT ACCESS-REQUEST SLACK NOTIFICATION

Two different Slack posts hit the same `#intelligence-platform-request-access` channel (`SLACK_CHANNEL_ID` default `C0BE016E2E8`):
1. **Public "Request access" form** -> `_demo_request_to_slack()`: short plain-text message (Name/Email/Company/Interest/Message). No URL, no card.
2. **`/app` per-agent "Request Access"** -> `_agent_access_request_slack_blocks()` + `_agent_access_request_to_slack()`: a Block Kit message. Previously ended with a footer link to `/p2/admin/access-requests` that Slack auto-unfurled into a ~292 kB OpenGraph hero card on every notification - fixed by removing that footer block and setting `unfurl_links:false, unfurl_media:false` on `chat.postMessage`.

---

## SURFACE 3 - INTERNAL STAFF APP `/p2/*` (`@position2_required`)

All internal staff surfaces live under `/p2/*` (old top-level paths 301-redirect). Shell chrome: topbar + left nav + user-pill menu (with the light/dark toggle) + breadcrumb + particle bg.

- `GET /p2` / `/p2/` / `/p2/hub` - the internal hub landing.
- `GET /p2/gtm` - GTM hub; `GET /p2/gtm/sentiment-pulse` - **Sentiment Pulse** dashboard (mock data); `GET /p2/gtm/ad-intelligence` - Ad Intelligence React app.
- `GET /p2/gtm/linkedin-intelligence` (renamed this cycle from `/p2/gtm/linkedin-scraper`, which now 301-redirects) - **LinkedIn Intelligence**, rebuilt on a new live engagement sheet with a richer, more colorful UI.
- `GET /p2/seo` + `/p2/seo/<tool_slug>` - **SEO Studio** (proxies `seo-apps`). Its address bar now stays in sync with whichever tool is open inside the cross-origin iframe: `embed.html` listens for the SEO app's route-change `postMessage` and reflects it into `/p2/seo/<slug>` via `pushState` (previously switching tools in the embedded sidebar never changed the parent URL).
- `GET /p2/accounts`, `/p2/signal-tracker/<account_id>[/<section>]` - Signal Tracker dashboards.
- `GET /p2/playbook` + `/p2/playbook/<slug>` (renamed this cycle from `/p2/context`, which now 301-redirects) - the internal platform explainer/playbook. Rebuilt from a single long page into an 8-chapter launcher-card story (bento-grid overview, spotlight explorer, clickable agent-landscape accordions, before/after and navflow cards for how to move between the three surfaces); "SEO fleet" renamed to "SEO Suite" throughout its copy (filter/bucket data attributes like `data-f="seo"` deliberately left unchanged). The template file is still named `context.html` even though the route/label is "Playbook."
- **Admin dashboards:** `/p2/admin/internal-usage`, `/p2/admin/anonymous-traffic`, `/p2/admin/public-page-analytics`, `/p2/admin/public-agent-usage` (renamed this cycle from `/p2/admin/agent-runs`), `/p2/admin/access-requests`. Old slugs (`/p2/admin/usage|visitors|members|agent-runs`) still 301-redirect. Each has a `.../data` JSON endpoint. All `@admin_required`. KPI cards are clickable (drawer with per-metric explanation + breakdown, and on Anonymous Traffic/Internal Usage a merged pre/post-login journey timeline - see the `p2_vid` section above). Conversion % excludes `@position2.com`; company inferred from email domain.

### Sentiment Pulse (internal, display-only PROXY DATA - unchanged, still not a real pipeline)
`/p2/gtm/sentiment-pulse` (`call_sentiment.html`). Voice-of-patient sentiment across calls/reviews/surveys for a fictional network ("Cedar Valley Health," 10 NC/SC/VA locations), seeded PRNG-generated. Vimi is explicitly instructed to disclose this is mock data if asked.

### Ad Intelligence (internal, built React app)
`GET /p2/gtm/ad-intelligence` serves a Vite React build directly (not an iframe). Assets under `/p2/gtm/ad-intelligence/assets/...` (a root `/assets/...` ref 404s). Reads `AD_INTEL_SHEET_ID`.

---

## BRANDING - the "Arena" mark (stable)
Central bright-green hexagon (`#55be8c`) + three steel-blue petals (`#4a6a7c`) + three dark-green petals (`#53795b`) = a 6-point star. `static/favicon.svg` = star on a white circle, mirrored as `logo.svg`. `static/logo-mark.svg` = star only, transparent, padded (`0 0 100 100` viewBox). On-page logo = `<img src="/static/logo-mark.svg?v=3">` on a theme-aware circle. Static assets served at `/static/...` (only `/favicon.svg`/`/favicon.ico` have root routes).

## LIGHT / DARK THEME (internal app only)
`static/js/theme.js` reads `localStorage['p2-theme']` (default dark), sets `document.documentElement[data-theme]`, exposes `window.P2toggleTheme(event)`. Public marketing site + `/app` stay dark. Known follow-up: some heavy custom inline pages still need light-mode polish.

## HARD SIGN-OUT (new this cycle)
`/logout` now, in addition to `session.clear()`, sends `Clear-Site-Data: "cookies","storage"`, explicitly deletes the session cookie and `p2_seen` with `path="/"`, and sets `no-store`/`no-cache` headers, because a plain `Set-Cookie` deletion only removes a cookie whose attributes exactly match what `delete_cookie` emits - if the session cookie was ever issued with different attributes, the browser kept sending it and a signed-out user looked auto-logged-in on their next visit. The login page now shows a "You've been signed out" confirmation. Gotcha preserved in the code as a comment: do not touch `session.permanent` after `.clear()` - assigning it re-adds a key and stops Flask from deleting the cookie outright.

---

## SIGNAL PIPELINE + REFRESH (unchanged from v15, still the live blocker)
"Refresh signals" triggers GitHub Actions `refresh-dashboards.yml` (Railway var `GH_DISPATCH_TOKEN`): writes `config.yaml` from secret `CONFIG_YAML` + `service_account.json` from `GOOGLE_SERVICE_ACCOUNT_JSON`; runs fetch steps; rebuilds dashboards + weekly brief; commits. **Blocked until** GitHub Actions secrets `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON` are set (separate from Railway vars) and both Healthcare Sheets are shared with the SA `client_email` (Viewer):
- C-suite: id `16M_DLwIhbKuQAv_Cafxl8krrNWO5iaTrBJdQaN9EI6g`, tab "C-suite signals".
- Funding: id `1nhu07HCyctjs5gfGdchu_n7Rxnpip0xVMwB9nIF_wa0`, tab "Funding signals".
Signal Tracker: Healthcare universe 1,251 orgs tracked (238 currently have an active signal, 827 total signals live in the DB), CSG (97 companies with signals, 201 total signals). Importance `type_weight x severity x recency`; 90-day retention/decay.

---

## ENVIRONMENT VARIABLES
### Railway (runtime)
`DATABASE_URL` (Postgres, run history), `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` (default `C0BE016E2E8`), `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), `SMTP_*` (unusable on Railway).
**New this cycle (visitor_intelligence):** `APOLLO_API_KEY`, `VI_ENRICH_ON_VIEW` (opt, default off), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt, default `data/identity_graph.db`).
### GitHub Actions secrets (refresh workflow) - SEPARATE from Railway
`CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

---

## HOW TO WORK ON THIS (proven-safe workflow, unchanged)
1. **Clone fresh into the bash sandbox each session.** Sandbox network: git over `github.com` works, but `api.github.com`, GDELT, OpenAI, RSS, Google APIs are BLOCKED - live data/visuals can't be verified from the sandbox. The sandbox may reset/corrupt mid-session; if `git status` fails with "not a git repository" or template/data files look truncated, don't delete first - rename the broken directory aside, re-clone fresh, verify the fresh clone matches the last known-good commit hash and file counts, only then remove the broken copy.
2. Edit via Python string-replace/slice scripts (assert exactly-one match) or file-edit tools; new templates via single-quoted heredocs.
3. **Validate before every push:** `python3 -c "import ast; ast.parse(open('app.py').read())"`; import the app under a dummy `SECRET_KEY` to catch route collisions; Jinja-parse + render each changed template; `node --check` inline `<script>` (strip Jinja first); guard no `{{`/`{%`/`{#` inside `<style>`/`<script>`; CSS brace balance; tag balance; no duplicate `@app.route`; run the pytest suite (`python3 -m pytest`) and treat any newly-failing test as your problem to explain, not ignore.
4. Push to `main` -> Railway deploys ~90s. If rejected, `git pull --rebase origin main` then push. Push token = GitHub classic PAT (`repo`+`workflow`) as `https://x-access-token:<TOKEN>@github.com/...`. **Redact tokens in ALL output** (`sed -E 's#(https://)[^@]*@#\1***@#g'`); the user shares a fresh token each session and rotates it after. **Once changes are validated, push without asking "should I push?" first** - the user confirmed once that they want confirmation-free pushes for this project going forward; still report what shipped afterward, including the commit hash and a production health check.
5. Sandbox can't verify visuals/live data. For real mobile-viewport/rendering checks, use a local preview server with genuine device emulation. To audit auth-gated pages without any auth bypass: render via `app.test_client()` with a fake `session['google_user']`, dump the HTML, view it through the running server.
6. Browser favicon/CSS/JS caching is aggressive - hard-refresh; bump `?v=N` when replacing a cached asset in place.
7. **When renaming a persisted slug/id/URL, alias BOTH the URL (301-redirect) AND every read path that keys off the old value** (sheet columns, DB rows, admin aggregates, UI labels) - not just the most visible consumer. A slug rename that only fixes routing can silently make historical data (e.g. a run-history stat, a signal count) vanish or reset for real users.
8. **Never use an em dash ("-") in any written copy** - page content, UI labels, docs, commit messages, or chat responses meant as a deliverable. Use a comma, colon, semicolon, period, or parentheses instead.

### Gotchas
- `<title>` has the same `{% elif page %}` chain - anchor searches on body-specific strings.
- `@media (...){#x{...}}` is a `{#` Jinja-comment trap - keep a space.
- `admin.css` loads last and intentionally overrides each admin page's inline CSS. `hub.css` uses spaced selectors unlike most files - grep by value strings if a selector grep misses.
- Flex shrink: a `display:flex` item that must shrink below its content needs its OWN `min-width:0`. `padding` shorthand fully overrides an earlier longhand on all four sides.
- Theme: default = dark (no attribute). Light = `[data-theme="light"]`.
- Prefer solid colors over gradient-clip-to-text for values that must always render.
- The classifier/auto-mode may block writing config in sensitive locations, auth-bypass routes, or clicking buttons that fire real Slack/Sheets writes - don't work around it; ask the user or pivot to a safe method. Never test-and-send into Slack yourself.
- `templates/context.html` and `templates/linkedin_scraper.html` are file-name remnants of routes that have since been renamed ("Playbook", "LinkedIn Intelligence") - don't be misled into thinking the feature is still called that.

---

## OPEN ITEMS / TODO
1. **Signal refresh secrets (highest priority, blocking Healthcare refresh):** set GitHub Actions secrets `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`, share both Healthcare Sheets with the SA `client_email` (Viewer).
2. **Assign real agents to more `/app` cards:** only 3 are wired to live tools; set `seo_slug` to connect more.
3. **Light-theme polish** on heavy custom inline pages (SEO Studio, LinkedIn Intelligence, Anonymous Visitors, some Sentiment Pulse widgets).
4. **Email (pending):** Gmail domain-wide delegation (`gmail.send`) + `GMAIL_SENDER`.
5. **Ad Intelligence data:** share `AD_INTEL_SHEET_ID` with the SA if it opens but shows no ads.
6. **`SERP_PLATFORM_TOKEN` visible** in the public `/app` embed iframe URL - optional hardening = a server-side proxy.
7. **GBP QC Agent embed:** provision its Railway service + `frame-ancestors` + `?pt=`; never commit the secret zip.
8. **News freshness / reverse-IP:** set `SERPAPI_KEY` if not already set.
9. **visitor_intelligence identity graph durability:** `data/identity_graph.db` is gitignored (correctly, it's PII) but lives on Railway's ephemeral disk - point at a persistent volume or migrate to Postgres if long-term person-level continuity matters.
10. **Cold-visitor identification** (a never-before-seen anonymous stranger) requires a licensed identity feed (RB2B/Vector/LiveRamp) or an owned co-op file - not solvable in code alone; the `IdentityProvider` plug point is ready if one is procured.
11. **From an independent security/design/signals audit this cycle (advisory, not started, do not begin without explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID` defaults, session cookie flags, security headers/HSTS, untracking the committed `data/tracker.db`/`apollo-accounts-export.csv` from git, CSRF protection, rate limiting on public beacons, SSRF/`X-Forwarded-For` hardening; token convergence across competing CSS palettes, a single shared base template, adopting the currently-unused `ds-components.css` design system, self-hosting CDN libraries, accessibility fixes; wiring the tracking beacon's full behavioral data into intent scoring, account-level "hot accounts" rollup, unified lead score, alerting/digests.
12. **Rotate the GitHub token** shared into chat (standing reminder).

---

## COMPETITOR / SIGNAL RESEARCH + ROADMAP (recorded, not built)
Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala.
- **Gaps:** co-op topic intent (Bombora), review-site intent (G2/Capterra), technographic change, champion job-change tracking (UserGems, highest ROI), hiring-surge, earnings/10-K mining, event attendance, layoffs, product-led usage.
- **Buildable now:** Earnings and Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief.
- **Differentiators:** generative-search/AI-answer visibility (early) + execution (the agency runs the plays) + first-party web de-anonymization now with a real engine behind it (v18).
