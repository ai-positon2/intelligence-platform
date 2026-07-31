# Intelligence by Position2 - Full Context (v22 - July 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v22 supersedes all earlier context files (v1-v21)** - older versions are stale; ignore any pasted copy.

**What v22 adds on top of v21 (this cycle's work):**
1. **The NorthStar ABM Signal Tracker research is now 100% complete: all 35 companies researched, in 7 batches total (batches 1-3 were v21; 4-7 landed this cycle).** The tracker went from 102 signals / 15 companies at the start of this cycle to a fully-researched universe, then had a **standing 6-month signal-recency rule** applied on top (see below), landing at a final, curated **71 signals across all 35 companies** (52 HIGH / 16 MEDIUM / 3 LOW; by category: 25 Creative Hiring, 12 Product Launch, 9 C-Suite Join, 9 Partnership, 8 Acquisition/M&A, 5 News Mention, 3 C-Suite Exit). No more "next batch" work remains here - future Signal Tracker work is either periodic re-pruning as signals age past 6 months, or a user-directed refresh pass.
2. **A permanent signal-curation policy was established mid-cycle and hardened across every subsequent batch:** (a) a hard **signal_date >= 6 months ago** admission rule (not just a severity input - a signal older than this is excluded outright), now baked into `data/northstar_signals_manual.json`'s `_quality_bar.rules` as standing policy for all future research; (b) a fixed categorization convention - **identifying an account's incumbent vendor -> `Partnership`** (NorthStar would displace them), **the account self-staffing its own openings -> `Creative Hiring`**, **new OR/ASC/hospital capacity or major equipment -> `Product Launch`** (not `News Mention`), **NorthStar's own existing account relationships (its own job postings, an account it already services) -> `Partnership`**, **a single robotic-system deployment -> `MEDIUM`** severity. See [[feedback-signal-relevance-bar]].
3. **Two more admin dashboards got real fixes, not just polish:** External Usage's Agent Runs table now shows **what each run actually was** (not just which agent), joining Postgres `agent_run_history` titles onto the Sheets-sourced run rows. Client Usage's per-client detail page now (a) scopes a Position2 staffer's shown logins/activity to **actual `/​<slug>` portal usage only** (not their platform-wide activity elsewhere), and (b) **drops anyone from the list who has neither signed in nor viewed the portal** - a person is only shown if they have a genuine footprint on that specific client surface.
4. **Client Usage detail page's top KPI row got a full visual redesign** (per-card accent color + icon badge + breathing glow + gradient-fill numbers) and its **layout switched from CSS grid to flexbox-with-grow**, so an uneven last row (e.g. 6 cards + 1 orphan) stretches every card to fill the row evenly instead of stranding one card next to dead space.
5. **Public Page Analytics gained a rich "Members" table directly below its KPI cards** (`/p2/admin/public-page-analytics`), visually matching External Usage's "People" table - avatar with online-dot, company chip, sign-ins/page-views metric chips with proportional bar, time-on-site, relative+absolute first-seen/last-active timestamps, device, source - replacing the old compact 8-row mini-list that used to sit further down the page. Row click still opens the same full-journey drawer this page already had.
6. **The client-portal external-tool run-metering gap flagged in v21 (Open Item #2) is now fixed.** `_client_agent_use`'s external branch no longer logs a run the instant the page loads; the external tool (NorthStar's LinkedIn Strategy Researcher, on `watchtower-by-position2.vercel.app`) now emits the same `postMessage` run contract a SERP tool does (`source:'p2-agent'`, `agent-run-started` / `agent-run-finished`), and `client_embed.html`'s external branch listens for it exactly like the SERP branch already did. This also fixed a latent 400 bug: `_client_agent_log_run`/`_client_agent_finish_run` were calling `_client_agent_view(agent_slug)` without the `client` argument, which silently made an external-tool-only agent (no `seo_slug`) resolve `connected=False` and reject every log-run call - invisible until the client-side listener actually started calling it.
7. **A two-stage hover bug on the Signal Tracker's KPI cards was fixed, with a lesson worth repeating: the first fix was real but not the root cause.** User reported hovering a KPI card visibly expanded it. First pass found and removed a decorative JS 3D-tilt-on-mousemove effect (`scale(1.02)` + `rotateX/rotateY` on hover) - a genuine bug, verified fixed via synthetic mousemove, but the user came back with a fresh screenshot showing the SAME visual symptom still live. Root cause turned out to be unrelated: a CSS specificity/declaration-order collision (`.kpi-card>*{position:relative}`, declared AFTER and at equal specificity to `.kpi-tooltip{position:absolute}`) silently pulled a hidden tooltip element into normal document flow; revealing it on `:hover` added ~40px of real height to the card, and because cards share a grid row, the whole row grew with it. Fixed with a more-specific selector re-pinning the tooltip out of flow. **Lesson: when a user says "still not fixed," re-measure the EXACT reported symptom empirically (`getBoundingClientRect()` before/after a real hover) rather than trusting that removing one plausible-looking mechanism was sufficient.**

**Latest `main` HEAD at end of this cycle: `d49c5a0`** (always `git pull` to confirm; Railway auto-deploys each push). `app.py` is now **7,973 lines / 119 `@app.route` decorators + 10 loop-registered `add_url_rule` families**. This cycle's non-Signal-Tracker code touched `app.py`, `templates/admin_external_usage.html`, `templates/admin_client_usage.html`/`admin_client_detail.html`, `templates/admin_members.html`, `templates/client_embed.html`, and `tracker/dashboard_builder.py`; the Signal Tracker research work touched only `data/northstar_signals_manual.json`, `seed_northstar_signals.py`-driven rebuilds of `data/tracker_northstar.db` and `reports/dashboard_northstar*.html`.

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 = a B2B digital-marketing agency: SEO/organic, performance/paid media, paid social, content, brand/website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, anesthesiologist/creative hiring, news), de-anonymizes website visitors to company and (where a signal exists) person, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), helps reps act via an embedded AI assistant (**Vimi**, visible label **GTM**), and serves **co-branded client portals** that can also embed **agents built entirely on other platforms.**

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` -> `https://seo-apps-production-37a6.up.railway.app`
- **Third-party agent frontend (NOT our code, NOT our repo):** `https://watchtower-by-position2.vercel.app` - the user builds these on an unrelated AI app-builder platform; we only receive and iframe the public URL, and this cycle the user deployed a `postMessage` run-signal into it (see item 6 above) so we finally get real run events from it. Still no other visibility into, or control over, that codebase.
- **Hosting:** Railway, auto-deploys on every push to `main` (~60-100s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; signal data refreshes via GitHub Actions (Healthcare/CSG) or the manual NorthStar seed script (see below). `seo-apps` is its own Railway service.
- **Admins (`ADMIN_EMAILS`, app.py ~line 1218):** `krishna.ladha@position2.com`, `sudheer.d@position2.com`, `reporting@position2.com`, `sparikh@position2.com`, `abhilash.dg@position2.com`.

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
├── app.py                ← Flask server (~7,973 lines, 119 routes + 10 loop-registered families):
│                            auth (3 decorators + client gate), all 4 surfaces, AGENTS/APP_AGENTS/
│                            SIGNALS/INDUSTRIES/CLIENTS registries, OpenAI (Vimi x2 backends),
│                            marketing routes, /api/demo-request, /api/track|atrack|identify,
│                            /app/* + run history, /p2/* + admin analytics, client-portal routes
│                            (incl. external-tool agents, now real postMessage-metered),
│                            internal /p2/gtm external-tool route, LinkedIn Intelligence
│                            (per-sheet), Postgres history. Does NOT build Signal Tracker
│                            dashboards - just serves the pre-built HTML files (see below).
├── visitor_intelligence/ ← de-anonymization engine: resolver.py (IP resolution + connection-type gate +
│                            confidence), pipeline.py (orchestration + Apollo), identity_graph.py
│                            (SQLite person graph, union-find), __init__.py. Tests: python3 -m visitor_intelligence.tests
├── tracker/              ← signal pipeline pkg (news_client, news_relevance, signal_score,
│                            dashboard_builder [build_dashboard(), takes hiring_opts - per-account
│                            category-label override, see below], csv_loader [load_companies()],
│                            snapshot_store [SnapshotStore: companies, snapshots, alerts_sent,
│                            weekly_runs tables], sheets_client, apollo_client, ...)
│                          - shared by Signal Tracker + client dashboards
├── main.py               ← weekly orchestrator (Healthcare) -> data/tracker.db
├── build_northstar_dashboard.py, build_csg_dashboard.py  ← per-account/client dashboard builders
├── seed_northstar_signals.py   ← idempotent loader, data/northstar_signals_manual.json -> DB -> rebuild.
│                            Has a --prune flag (added this cycle's earlier half): without it, seeding
│                            is insert-only, so curating/removing/re-severity-ing signals directly in
│                            the JSON never reached the DB. Always use --prune now; the JSON is the
│                            source of truth and stale DB rows must be reconciled away, not just left.
├── northstar-company-details.csv ← NorthStar's 35-company universe (fully researched as of this cycle)
├── ad_intelligence/      ← built React app (Vite) served by Flask; assets at /p2/gtm/ad-intelligence/assets/
├── static/
│   ├── css/ds-tokens.css, ds-components.css        ← internal design tokens + shared components
│   ├── css/gtm.css, hub.css, seo.css, linkedin.css, admin.css, aurora-app.css, grid-tokens.css,
│   │        client-portal.css                       ← per-surface styles (each with [data-theme=light] blocks)
│   ├── js/theme.js        ← light/dark toggle (localStorage 'p2-theme'); swaps a sun/moon SVG in #p2ThemeIcon
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
│   ├── admin_usage.html, admin_visitors.html, admin_members.html (Public Page Analytics - now has
│   │        a rich Members table right below its KPI cards, see item 5 above),
│   │        admin_agent_runs.html, admin_requests.html,
│   │        admin_external_usage.html (People table now has a "What they ran" column),
│   │        admin_client_usage.html (cards), admin_client_detail.html (per-client dashboard, tabbed,
│   │        redesigned KPI row, scoped/filtered person list - see items 3-4 above)
│   ├── _admin_menu.html     ← the ONE shared internal admin dropdown (SVG icons, 3 sections)
│   ├── client_base.html     ← shared shell for all client-portal pages (co-branded topbar, tracks /api/track)
│   ├── client_portal.html   ← client home (agent cards grid)
│   ├── client_agent.html    ← client agent detail (note-beside-button copy removed)
│   ├── client_embed.html    ← client agent "use" shell (iframe: SERP tool OR live dashboard OR external
│   │                            tool - external branch now has real postMessage-run-listener JS, see item 6)
│   ├── client_history.html, client_history_detail.html, client_denied.html
│   └── ppc_chat_widget.html ← shared Vimi chat widget (internal only)
├── reports/          ← dashboard*.html (Signal Tracker dashboards, incl. dashboard_northstar*.html - these
│                        are BUILT ARTIFACTS committed to git, regenerated by the build_*.py scripts, then
│                        served as static files by app.py - never hand-edit these HTML files directly)
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml
```

### Deploy + data model
- **Code/UI** push to `main` -> Railway redeploys (~60-100s). No hot reload locally.
- **Google Sheets is the primary data store** for internal analytics (Login Log, Page Views, Agent Runs, etc. - see below). **NorthStar's Signal Tracker data lives in SQLite instead** (`data/tracker_northstar.db`), populated by the manual research pipeline (see dedicated section) rather than Sheets, at least for now.
  - **Login Log** (the default/first tab, range `A:U`): `@position2.com` staff sign-ins only (written by `_log_login_to_sheet`). Email @col 5, name @col 6.
  - **`Member Signins`** tab (`_MEMBER_TAB`, range `A:T`): every NON-`@position2.com` sign-in (written by `_log_member_signin`). Email @col 5, name @col 6, picture @col 8, visitor-id @col 9, browser @col 11, OS @col 13, device @col 14. **Column indices differ from the Login Log** - this matters and is mapped per-tab.
  - **Page Views** tab (`A:N`): every tracked page view. Cols: 0 Timestamp(IST), 1 Date, 2 Time, 3 Day, 4 Email, 5 Page Title, 6 Page URL, 7 Seconds, 8 Duration, 9 IP, 10 Browser, 11 OS, 12 Device, 13 Visitor ID. Written by `/api/track`. **Client Usage now derives a Position2 staffer's "logins" for a given client slug from this tab's page-view dates on that slug's URL path, not from their platform-wide Login Log rows** (see item 3 above) - it's the only way to know they actually touched that specific portal.
  - **Agent Runs** tab (`_AR_TAB`, `A:F`): 0 Timestamp, 1 Date, 2 Email, 3 Name, 4 slug, 5 AgentName. Written by `_log_agent_run`. **No client column** - a run row only knows email + agent slug, not which portal it happened on. Per-client readers (Client Usage) infer client by intersecting with that client's configured `agents` list.
  - **Visitor Analytics** tab: pre-login journey by `p2_vid`.
  - **Demo Requests** tab (`DEMO_REQUEST_SHEET_ID`): access-request form.
- **Postgres** (`DATABASE_URL`): agent run history (full JSON outputs), table `agent_run_history` (`email`, `agent_slug`, `title`, `output` JSONB, `created_at`). **External Usage's Agent Runs table now joins this table's `title` column onto each Sheets-sourced run row** (via a FIFO queue keyed by `(email, agent_slug)`, since the Sheets row has no run-content field of its own) - see item 3 above.
- **SQLite** (committed): `data/tracker.db` (Healthcare), `data/tracker_csg_v2.db` (CSG), `data/tracker_northstar.db` (NorthStar - 35 companies, 71 signals as of this cycle, fully researched). **Gitignored, real PII, never commit:** `data/identity_graph.db`.
- **Sheets read performance rule (important):** for fast admin dashboards, warm the IP cache concurrently, do concurrent per-thread `values().get()`, and cache ~300s. **Do NOT use `batchGet`** - it returned empty in prod. Several admin endpoints follow this.

---

## THE NORTHSTAR ABM SIGNAL TRACKER - RESEARCH PIPELINE (now fully complete)

The Signal Tracker started this era as an empty shell (0 signals), then went through 7 research batches across two cycles (batches 1-3 in v21's cycle, 4-7 this cycle) to reach **all 35 companies researched, 71 curated signals**. It is fed by a manual-research-to-database pipeline, not live Sheets, and that pipeline (plus its curation policy) is now mature and stable.

### The data model (`tracker/snapshot_store.py`, `SnapshotStore` class)
- `companies` table: `apollo_id` (primary key), `name`, `domain`, `industry`, `city`, `state`, `first_seen`, `last_enriched`, `is_active`.
- `alerts_sent` table: `apollo_id`, `signal_type`, `signal_detail`, `severity`, `sent_at`, `signal_date`, `source_url`, `dry_run`. One row per individual signal event. Written via `store.record_alert(apollo_id, signal_type, signal_detail, severity, dry_run=False, signal_date=None, source_url="")`.
- **Canonical `signal_type` strings** the dashboard's category chart/filter/KPI tiles all expect exactly (see `tracker/dashboard_builder.py`'s `catMap`): `"Funding Round"`, `"Acquisition / M&A"`, `"IPO Signal"`, `"C-Suite Join"`, `"C-Suite Exit"`, `"News Mention"`, `"Product Launch"`, `"Partnership"`, `"Creative Hiring"`. Get these exact strings wrong and a signal silently won't count toward any KPI tile.
- `severity` is `HIGH` / `MEDIUM` / `LOW` - drives the H/M/L chip and each company's `max_severity` rollup (NOT a per-signal aggregate - "High Alerts" on the dashboard means "N companies whose HIGHEST signal is HIGH," not "N signals are HIGH").
- `get_recent_alerts(limit, max_age_days)` does NOT filter by `companies.is_active` - it's a bare join. Deactivating a company alone doesn't stop its old signals from counting; delete its `alerts_sent` rows too for a real removal.
- **Important gotcha discovered this cycle: `max_age_days` filters on `sent_at` (the row's DB-insertion timestamp), not `signal_date` (the real-world event date).** For NorthStar's one-shot manual backfill, every row's `sent_at` is "whenever it was seeded," effectively "now" - so bumping `build_dashboard()`'s `max_signal_age_days` parameter is a no-op for this account. The only way to actually prune by real event age is to filter `data/northstar_signals_manual.json` by `signal_date` directly and re-seed with `--prune`. This will bite the same way for CSG/Healthcare if their signals are ever manually backfilled instead of fetched live day-by-day.

### The "Creative Hiring" -> "Anesthesiologists" per-account override
`Creative Hiring` is a shared, cross-account signal category (Healthcare/CSG track literal 3D/creative-industry hiring under this label; it has its own weight in `tracker/signal_score.py`'s `SIGNAL_WEIGHTS`). Renaming it outright would have broken Healthcare/CSG semantics. Instead:
- `build_dashboard()` in `tracker/dashboard_builder.py` has an optional `hiring_opts: dict` param (`icon`, `label`, `tooltip`, `badge`, `empty_msg`), defaulting to the original Creative Hiring text/emoji when omitted.
- The `_HTML_TEMPLATE` raw-string has placeholder tokens (`__HIRING_ICON__`, `__HIRING_LABEL__`, `__HIRING_TOOLTIP__`, `__HIRING_BADGE__`, `__HIRING_EMPTY_MSG__`) substituted by `_render_html()` at build time.
- **The underlying stored `signal_type` stays `"Creative Hiring"`** in the database and in `SIGNAL_WEIGHTS` - only the display text/icon changes. Healthcare/CSG's `reports/dashboard.html` / `dashboard_csg.html` are provably untouched.
- `build_northstar_dashboard.py` passes `hiring_opts={"icon": "🩺", "label": "Anesthesiologists", "tooltip": "Anesthesiologist / CRNA hiring signals", "badge": "Anesthesia Hiring", "empty_msg": "No anesthesiologist-hiring signals detected yet."}`.
- **If another client ever needs a similarly-renamed category, this is the pattern to reuse** - a per-account `_opts` dict threaded through `build_dashboard()` and substituted via template placeholders, never a global rename.

### The manual research -> database pipeline
1. **`data/northstar_signals_manual.json`** - the source-of-truth research log. Structure: `_readme` (schema notes), `_quality_bar` (see below - the living curation policy), `batches_loaded` (`[1,2,3,4,5,6,7]`, all done), `companies_covered` (all 35 names, fully researched), `companies_removed_from_universe` (18 names dropped in an earlier cycle, kept for audit trail), `signals` (flat array of `{company_name, signal_type, signal_detail, signal_date, source_url, severity}`).
2. **`_quality_bar` block (`why`, `include_HIGH`, `include_MEDIUM`, `exclude_always`, `rules`)** - a living document of what belongs on this tracker at all, built and refined across the whole 7-batch arc. Current `rules` (10 total) include, most importantly: **a permanent 6-month admission cutoff by `signal_date`** (added this cycle, supersedes an older 12-18-month exec-change-decay rule for admission purposes - a signal that's stale by the 6-month rule is excluded outright regardless of what the decay rule would have said), plus the categorization conventions in "what v22 adds" item 2 above. Treat this JSON block as authoritative before starting any future research batch or re-curation pass - read it first rather than re-deriving policy from memory.
3. **`seed_northstar_signals.py`** - reads the JSON, looks up each `company_name` against the live `companies` table, calls `record_alert()` for each signal **not already present** (dedup: exact match on `apollo_id + signal_type + signal_detail`). **Always run with `--prune`** (see architecture section) so JSON-side removals/edits actually reach the DB - it is the source of truth, not the DB. Then rebuilds both HTML files via `build_northstar_dashboard.build_northstar()`, unless run with `--no-build`.
4. **Research method:** for each batch of ~4-10 companies, one `general-purpose` subagent per company was dispatched in parallel (single message, multiple `Agent` tool calls), briefed with the exact company name/domain/city/employees, the 9 canonical categories, the current `_quality_bar` rules (including the 6-month cutoff once it existed), and an explicit **no-fabrication instruction** - "an empty category is a perfectly good and expected result," cite a real source URL for every claim. Agents repeatedly self-corrected on ambiguous cases without being told to: excluding a same-named-but-unrelated company, recognizing NorthStar's OWN job postings at an account it already services (logging it as `Partnership`/existing-relationship rather than misreading it as fresh prospect intent), and declining to count AI-search-summary claims with no fetchable source.
5. **Confidence -> severity mapping:** each finding comes back tagged HIGH/MEDIUM/LOW confidence, written directly into the `severity` column - the closest available proxy for alert importance, and exactly what drives the dashboard's H/M/L chip.
6. **After every seed, independently recompute expected KPI numbers by hand from the JSON** and diff against the dashboard's embedded `DATA.kpis` blob before pushing - cheap insurance for real data feeding actual sales outreach.

### Batches researched (all 7, 35/35 companies, done)
- **Batches 1-3** (earlier cycle, no 6-month rule yet): Banner Health, Orlando Health Jewett Orthopedic Institute, HEALTH FIRST, Southwest Surgical Associates, Baptist Health South Florida, Kelsey Seybold, AdventHealth, United Surgical Partners International (USPI), Texas Orthopedic Hospital, Grand View Health, Wyandot Memorial. Best find: NorthStar is already the incumbent CRNA/anesthesia vendor at multiple AdventHealth sites (logged as Partnership).
- **Batch 4** (this cycle, 10 companies, quality-bar-curated brief): Physicians Outpatient Surgery Center, Atlas Healthcare Partners, Houston Methodist, AMSURG, Texas Health Resources, Broward Health, Novant Health, UMC El Paso, OPTIM Health System, St Clair Health. 47 new signals. Best finds: Broward Health's incumbent (Anesco) terminated for cause with the replacement (Envision) still unstable 18 months later; AMSURG's Ascension acquisition triggered an FTC-mandated divestiture in Nashville (NorthStar's own home market); named incumbents identified at Houston Methodist (USAP), Novant (Providence Anesthesiology Associates), OPTIM (NAPA/Tattnall).
- **Batch 5** (5 companies, first batch under the new hard 6-month cutoff): Riddle Surgical Center, South Texas Spine & Surgical Hospital, Miami Jewish Health, Promedica, Regency Hospital Company. 3 of 5 came back empty (South Texas Spine: only pre-window/awards; Miami Jewish Health: geriatric/PACE, no surgical footprint; Promedica: real activity but all pre-window or non-operational). 4 signals from the other 2: Regency's parent Select Medical's $3.9B PE take-private, a Regency Minneapolis CEO vacancy, a Regency Meridian MS facility closure, Riddle Surgical Center's incumbent (United Anesthesia Services) with an open CRNA req.
- **Batch 6** (5 companies, all 5 yielded signals - 15 total): Cullman Regional Medical Center, Magee-Womens Hospital of UPMC, HonorHealth, Doctors Hospital of Augusta, Cleveland Clinic Florida. HonorHealth alone yielded 6: a CEO transition pair, two facility expansions with explicit new-OR counts, a credit downgrade tied to a separate acquisition's integration costs, a confirmed incumbent (Valley Anesthesiology Consultants/Envision across 5 sites). Cleveland Clinic Florida: a new 200-bed hospital + ASC, an orthopaedic roll-up, a multi-facility self-staffing push.
- **Batch 7** (final 4, completing the universe): Wilmington Surgical Associates, Flowers Hospital, Findlay Surgery Center, Central Ohio Urology Group. Two came back empty (both correctly - one agent specifically caught and excluded a same-named-but-different company to avoid misattribution). Central Ohio Urology Group surfaced NorthStar's OWN existing contract there, correctly logged as Partnership rather than misread as prospect hiring intent.

**Final state: 71 signals, all 35 companies covered, 52 HIGH / 16 MEDIUM / 3 LOW.** No pending research batches remain.

---

## SURFACE 4 - CLIENT PORTALS

A per-client, co-branded front door at `/<client-slug>`. Only known slugs get routes (no top-level catch-all, so an unknown path never resolves here).

### `CLIENTS` registry (app.py ~line 2398)
Currently one client: `northstaranesthesia`. Each entry has: `slug`, `name`, `short`, `website`, `logo` (served from `/static/clients/<slug>/...`), `domains` (email domains allowed in addition to `@position2.com`), `accent`/`accent2`, `tagline`, `blurb`, `agents` (ordered list of APP_AGENTS slugs to show), `dashboards` (map of agent-slug -> pre-built static HTML file for that client), `linkedin_sheet` (a Google Sheet ID that makes LinkedIn Intelligence render as a *live* co-branded dashboard), and `external_tools` (map of agent-slug -> a full external URL to iframe).

NorthStar: `domains=["northstaranesthesia.com"]`, `accent="#5b9dff"`, `agents=["signal-tracker","linkedin-intelligence","linkedin-strategy-researcher","keyword-finder","content-brief-generator","content-enhancer"]` (6 agents - `ad-intelligence` was removed from this list in an earlier cycle), `dashboards={"signal-tracker": reports/dashboard_northstar_client.html}`, `linkedin_sheet="13V-W-yG5O-OoLJHjxsPKLjrpRyRdk647GgkIGw823oE"`, `external_tools={"linkedin-strategy-researcher": "https://watchtower-by-position2.vercel.app/linkedin.html"}`.

**Real-world usage note:** per the Client Usage dashboard - even after this cycle's fix to scope Position2-team activity to actual portal visits and drop no-footprint people from the list - NorthStar-domain client-side adoption of the portal has been minimal to date. Worth surfacing to whoever owns the NorthStar relationship, now that the Signal Tracker finally has a complete, curated dataset to show them.

### Agent types inside a client portal (three, all keyed off the SAME `agents` list)
1. **SERP-connected agent** (`seo_slug` set on the APP_AGENTS entry): iframes the seo-apps tool; run-metered via the `agent-run-started`/`agent-run-finished` postMessage contract (a run = an actual tool run, not a page-open).
2. **Dashboard-backed agent** (`is_dashboard`): a co-branded dashboard (static file or Flask-rendered live route, e.g. LinkedIn Intelligence, the Signal Tracker). Shown **Live**, **never run-metered** - a dashboard has no "runs."
3. **External-tool agent** (`is_external`): the agent's entire backend lives on a platform we don't own or see the code of; only a public URL is shared with us. `_client_external_tool(client, agent_slug)` looks it up in `client["external_tools"]`. Rendered via the SAME `client_embed.html` shell, iframed, host masked behind the portal path. **As of this cycle, IS run-metered on a real signal, not on page load:** the external tool now emits the same `postMessage` run contract a SERP tool does (`source:'p2-agent'`, `agent-run-started`/`agent-run-finished`), and `client_embed.html`'s external branch listens for it - see the fix in "what v22 adds" item 6. This resolved a prior gap where opening the tool and never touching it counted as a run.

### Access control + helpers
- `_client_allowed(client, email)`: True if email ends with `@position2.com` OR any client domain.
- `_client_gate(client)`: returns a login redirect (no user) or a 403 `client_denied.html` (not allowed), else None.
- `_client_agent_view(slug, client)`: enriches an APP_AGENTS entry with `connected`, `is_dashboard`, and `is_external`. **Always pass `client`** when calling this for an external-tool-only agent (no `seo_slug`) - omitting it silently resolves `connected=False` and 400s the log-run/finish-run endpoints (a real bug hit and fixed this cycle).
- `_client_external_tool(client, agent_slug)`: the external URL for an agent in this client, or None.
- `_client_agents(client)`, `_client_home`, `_client_agent_detail`, `_client_agent_use` (branches: dashboard -> external -> SERP/default), `_client_agent_dashboard` (404s for external agents - no metering bypass), `_client_linkedin_data`, `_client_agent_log_run`, `_client_agent_finish_run`, `_client_history`, `_client_history_detail`.

### Routes (registered per known slug in a loop)
`/<slug>` (home), `/<slug>/history`, `/<slug>/history/<id>`, `/<slug>/agents/<agent_slug>` (detail), `/<slug>/agents/<agent_slug>/use` (embed shell - branches to dashboard, external, or SERP), `/<slug>/agents/<agent_slug>/dashboard` (co-branded dashboard, static OR live-rendered; 404 for external agents), `/<slug>/agents/<agent_slug>/dashboard/data` (gated JSON for the live LinkedIn dashboard), `/<slug>/agents/<agent_slug>/use/log-run` + `.../finish-run`.

### Client run metering + history
SERP-tool AND external-tool agents in a client portal are run-metered (`AGENT_RUN_CAP=10` per user per agent, shared "Agent Runs" sheet, so they also show on the internal Public Agent Usage admin dashboard). Finished runs save to the same Postgres history and appear on the client's own `/history` page. Dashboard-backed agents (including the Signal Tracker) are not metered.

### NorthStar dashboards
- **ABM Signal Tracker:** built by `build_northstar_dashboard.py` - fully researched, 71 signals, 35/35 companies (see the dedicated section above). Registered in both `ACCOUNTS` (internal `/p2/signal-tracker/northstar`) and `CLIENTS[...]["dashboards"]`. **Never hand-edit `reports/dashboard_northstar*.html` directly** - they're regenerated from the CSV + SQLite DB by the build script every time. Its KPI-card hover behavior was also fixed this cycle (see "what v22 adds" item 7) - the fix lives in the shared `tracker/dashboard_builder.py` engine, so Healthcare/CSG dashboards got the same hover-stability fix as a side effect (verified: no visual/behavioral regression, since the fixed rules were dead weight for them too).
- **LinkedIn Intelligence (live):** same engine as internal (below), pointed at the client's `linkedin_sheet`.
- **LinkedIn Strategy Researcher (external):** competitive LinkedIn analysis tool - messaging, creative, posting cadence, AI-built action plan. Backend on `watchtower-by-position2.vercel.app`, NOT this repo. Now emits real run-start/run-finish postMessage events (this cycle's fix). Has a known large-company persistence bug (deterministic `async_jobs` UPDATE failure) inside the external tool's own codebase - not ours to fix.

---

## THE EXTERNAL-TOOL PATTERN

Sometimes an agent isn't built in this repo OR in seo-apps - the user builds it themselves on a **completely separate, unrelated third-party AI app-builder platform**, and only hands us a public frontend URL (currently a Vercel deployment). This platform has nothing to do with Intelligence Platform's architecture; we have no access to its code, its data, or its ability to emit events, beyond what a cross-origin iframe can see - EXCEPT that we can ask the user to add a small, self-contained `postMessage` snippet to it, which is exactly what closed the metering gap this cycle.

**How we integrate one:**
1. Confirm the frontend has no `X-Frame-Options`/CSP `frame-ancestors` blocking iframing (checked via response headers).
2. Add the agent to `APP_AGENTS` like any other (name, icon, colors, copy, tags) - no `seo_slug`.
3. Add its slug to the client's `agents` list, and its full URL to that client's `external_tools` map (client-portal) - or, for an internal-only copy, add a small dedicated route that renders `templates/embed.html` with the URL (see `/p2/gtm/linkedin-strategy-researcher`, uncapped by design).
4. `client_embed.html` (client portals) and `embed.html` (internal `/p2` pages) both just iframe the URL - the masking is free, since the browser's address bar shows OUR path, not the iframe's `src`.
5. **Metering requires the external tool's cooperation.** We can't see inside a cross-origin iframe, so the only way to get a true "run" signal is for the external tool to call `window.parent.postMessage({...}, 'https://intelligence.position2.com')` at the moment of a real action, guarded by `if (window.parent !== window)` so it's a no-op standalone. **This is now deployed and working** for LinkedIn Strategy Researcher (`source:'p2-agent'`, `agent-run-started`/`agent-run-finished`) - `client_embed.html`'s external branch listens for it, verifies the message's origin matches the tool's own origin, and calls the same log-run/finish-run endpoints a SERP tool uses. This is the template to reuse for any future external-tool integration.
6. **Internal vs. client copies of the same external tool can have different metering rules** - see LinkedIn Strategy Researcher: capped on the NorthStar portal, fully uncapped on `/p2/gtm` (internal staff tool, not a client deliverable).

---

## LINKEDIN INTELLIGENCE (internal + per-client, multi-sheet) - unchanged

Route `/p2/gtm/linkedin-intelligence` (old `/p2/gtm/linkedin-scraper` 301-redirects). Renders `templates/linkedin_scraper.html`; all content is drawn client-side by `static/js/linkedin.js` from `window.__LI_DATA_URL__` (JSON). The sheet is "one row per person x post engagement," header-mapped (column order can drift safely).

- `_fetch_linkedin_intel_data(force, sheet_id)` + `_linkedin_data_response(sheet_id, force)` with **per-sheet caches** (`_LI_CACHES`, `_LI_GZS`, `_LI_TABS`) so the internal dashboard and each client portal read independent sheets.
- `templates/linkedin_scraper.html` has a `client_mode` flag: when true it hides the internal topbar, the Vimi widget, and the Ctrl-K command palette, and injects a client-gated `data_url`.
- **Employee-vs-external label fix:** the sheet's "Relationship to Target" (Employee/External/Unknown) drives a per-person badge, using `<Client> Employee` (via `li_cfg`/`window.__LI_CFG__`) not a hardcoded "P2".

**Do not confuse this with LinkedIn Strategy Researcher** (external, competitive analysis of OTHER companies' LinkedIn presence) - "LinkedIn Intelligence" is about YOUR OWN post/people engagement data pulled from a Sheet. Also don't confuse either of these with the ABM Signal Tracker's own News Mention/Partnership categories (which can themselves be about LinkedIn-adjacent activity) - three different things share overlapping names in this codebase; check the route/file, not just the label, before editing.

---

## ADMIN ANALYTICS (all `@admin_required`, each has a `.../data` JSON endpoint)

The internal admin dropdown lists these. All KPI cards are clickable into detail. The menu itself is one shared partial (see next section).

- **Internal Usage** `/p2/admin/internal-usage` - `@position2.com` staff logins + page views. "Linked to Pre-Login" KPI + merged journey drawer via `p2_vid`. No row caps.
- **External Usage** `/p2/admin/external-usage` - everyone who signed in with a NON-`@position2.com` email (reads the **`Member Signins`** tab, not the Login Log). Joins the Agent Runs tab so each person carries what they ran; **the Agent Runs table itself now has a "What they ran" column**, joining Postgres `agent_run_history` titles onto each Sheets run row (this cycle's fix, item 3 above). Rich "People" table, clickable into a full-journey drawer.
- **Client Usage** `/p2/admin/client-usage` - landing = one card per client portal. Per-client dashboard `/p2/admin/client-usage/<slug>`: reads Page Views filtered by `/<slug>` URL path, splits every metric into **Position2 team vs the client's own team vs "Other"** by email domain. **This cycle: Position2-team login/activity is now scoped to actual `/<slug>` portal usage** (derived from that person's page-view dates on this specific slug, not their platform-wide Login Log rows), **and anyone with neither a portal sign-in nor a portal page-view is dropped from the list entirely** - previously a Position2 staffer's general platform activity elsewhere could make them appear here with 0 real portal engagement. **Tabbed per-person tables** - each segment panel has three tabs (**Logins / Page views / Agent runs**) that double as that segment's totals; clicking a tab re-ranks the panel and swaps each row's headline number + a proportional bar + a metric-specific caption. Person drawer shows an Agent runs stat, an "Agents used" breakdown, and green `run` events in the timeline. **The top KPI row got a full visual redesign this cycle** (per-card accent/icon/glow/gradient-fill numbers) **and switched from CSS grid to flexbox-with-grow** so an uneven last row (6 cards + 1 orphan) fills evenly instead of stranding a card. Helpers: `_fmt_secs`, `_cu_read_tab`, `_cu_name_map`, `_cu_pretty_name`, `_cu_url_belongs`, `_cu_client_of_url`. Per-slug 300s cache (`_CU_CACHE`, `_CU_ALL_CACHE`).
- **Anonymous Traffic** `/p2/admin/anonymous-traffic` - visitor_intelligence engine; concurrent IP resolve; per-visitor drill-downs; "Signed in later" via `p2_vid`.
- **Public Page Analytics** `/p2/admin/public-page-analytics` (old `/p2/admin/members`) - public member sign-ins + journeys. **This cycle: gained a rich "Members" table right below the KPI cards** (item 5 above), visually matching External Usage's People table (avatar+online-dot, company chip, sign-ins/page-views metric chips with bar, time-on-site, relative+absolute timestamps, device, source), replacing the old compact 8-row mini-list. Row click opens the page's existing full-journey drawer (unchanged).
- **Public Agent Usage** `/p2/admin/public-agent-usage` (old `/p2/admin/agent-runs`) - per-user/per-agent run counts vs cap.
- **Access Requests** `/p2/admin/access-requests` - the Request Access form + per-agent access requests.

---

## THE SHARED ADMIN DROPDOWN MENU (`templates/_admin_menu.html`) - unchanged

The internal topbar user-menu is **one shared partial** included as the inner content of every internal page's `.dd-items` container (15 templates). `app_base.html` (public `/app`) and `client_base.html` (client portals) keep their OWN small menus - the shared admin partial is internal-only.

- **Consistent inline-SVG icons** (currentColor + fixed size). `theme.js` swaps a sun/moon SVG for the toggle.
- **Three divider-separated sections:** (1) Light mode, Platform Playbook; (2) Internal Usage, External Usage, Client Usage; (3) Anonymous Traffic, Public Page Analytics, Public Agent Usage, Access Requests; then Sign out.
- **Admin items are gated by `{% if is_admin %}`**, from a Flask context processor (`_inject_app_agents`) computing `_get_user().email in ADMIN_EMAILS` template-wide.
- A canonical `.dd-*` baseline + `.dd-item-icon svg` sizing live in `aurora-app.css` (loaded by every internal page, currently `?v=2`).

---

## VIMI - THE EMBEDDED AI ASSISTANT (unchanged)

Two backends, both branded "Vimi": `/api/ppc-chat` (widget `ppc_chat_widget.html`, label **GTM**, `@position2_required`) and `/api/vimi-chat/<account_id>` (per-account, via `window.irChat`). `_build_ppc_context()` loops BOTH signal DBs (Healthcare + CSG) with live counts (60s cache). `_VIMI_PLATFORM_KNOWLEDGE` grounds feature questions (and discloses Sentiment Pulse is mock data). Web-search fallback via `_responses_web_search()`. Model chain: `OPENAI_INSIGHTS_MODEL` -> `gpt-5.4` -> `OPENAI_MODEL` -> `gpt-4o-mini`. Never guess; never mix Healthcare and CSG in one answer.

---

## ANONYMOUS VISITORS / DE-ANON ENGINE (unchanged)

`visitor_intelligence/` package. Company-level: multi-signal IP resolution + **connection-type hard gate** (business/education/government identifiable; isp/mobile/hosting/proxy gated out), noisy-OR confidence, Apollo enrichment (free-tier default; Apollo opt-in), 0-100 intent. Person-level: persistent SQLite identity graph (`identity_graph.py`, union-find), waterfall resolver (first-party -> Apollo people-match -> pluggable `IdentityProvider` like `CoopFileProvider` via `VI_COOP_FILE`). **Never fabricates a person.** Honest boundary: cold-stranger ID needs a licensed feed (RB2B/Vector/LiveRamp) or owned co-op; the plug point is ready. Env: `APOLLO_API_KEY`, `VI_ENRICH_ON_VIEW` (default off), `VI_COOP_FILE`, `VI_GRAPH_DB` (default `data/identity_graph.db`, gitignored).

---

## PRE-LOGIN / POST-LOGIN STITCHING (`p2_vid`)

Page Views + login tabs carry a `p2_vid` cookie column. Anonymous Traffic + Internal Usage show "Linked to Pre-Login" KPIs and merged journey drawers. Old rows predating the column show "unlinked" - expected.

---

## SURFACE 2 - `/app` MEMBER WORKSPACE (unchanged)

Shell `app_base.html`. `APP_AGENTS` (18+ cards; `keyword-finder`, `content-brief-generator`, `content-enhancer` are the 3 wired to live seo-apps tools via `seo_slug`; `linkedin-strategy-researcher` is external-tool-only, scoped to client portals + internal GTM, not on `/app`; the rest are request-access-only). Run history in Postgres, `AGENT_RUN_CAP=10`. `/app/history` + `/app/history/<id>`. `app_embed.html` relays the cross-origin tool's `postMessage` run-finished to `/app/<slug>/use/finish-run`.

---

## SURFACE 1 - PUBLIC MARKETING SITE (unchanged)

One template `templates/agents.html`, `{% if page %}` chain. Routes: `/`, `/login`, `/agents`, `/agents/<slug>`, `/platform`, `/signals`, `/solutions`, `/integrations`, `/resources`, `/security`, `/privacy`, `/terms`. Unlinked/direct-URL-only: `/industries*`, `/why-intelligence`. Public APIs: `/api/demo-request`, `/api/atrack`, `/api/identify` (token-gated). Honest-content principle: no fabricated logos/quotes/metrics. Request-access modal `#nvfov` -> Sheet + Slack (`#intelligence-platform-request-access`) + email.

---

## SURFACE 3 - INTERNAL STAFF APP `/p2/*`

`/p2/hub`, `/p2/gtm` (+ `/p2/gtm/sentiment-pulse` MOCK data, `/p2/gtm/ad-intelligence` React app, `/p2/gtm/linkedin-intelligence`, `/p2/gtm/linkedin-strategy-researcher` - external tool, staff-only, uncapped), `/p2/seo` + `/p2/seo/<tool>` (SEO Studio, proxies seo-apps; iframe route-sync via postMessage), `/p2/accounts` + `/p2/signal-tracker/<account_id>` (this is where the internal, un-co-branded NorthStar Signal Tracker HTML is served from), `/p2/playbook` (old `/p2/context` redirects; template still `context.html`), and the admin dashboards above. Sentiment Pulse = seeded PRNG mock data (Vimi discloses this).

**GTM bucket cards:** Target Accounts, Ad Intelligence, Anonymous Visitors, LinkedIn Intelligence, LinkedIn Strategy Researcher, Job Change Alert (coming soon). Card color themes live in `static/css/gtm.css` (`c-signals`, `c-adint`, `c-visitors`, `c-linkedin`, `c-lir`, `c-job`).

---

## BRANDING + THEME

"Arena" mark: bright-green hexagon `#55be8c` + steel-blue + dark-green petals = 6-point star. `logo-lockup.svg` used in internal topbars. `theme.js` (`localStorage['p2-theme']`, default dark, `window.P2toggleTheme`); public + `/app` + client portals stay dark; only `/p2/*` toggles. Hard sign-out: `/logout` sends `Clear-Site-Data` + explicit cookie deletion + `no-store` (don't touch `session.permanent` after `.clear()`).

---

## ENVIRONMENT VARIABLES

**Railway:** `DATABASE_URL`, `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` (default `C0BE016E2E8`), `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), `APOLLO_API_KEY`, `VI_ENRICH_ON_VIEW` (opt), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt), `SMTP_*` (unusable on Railway).
**GitHub Actions secrets (separate):** `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`.
**Not ours / third-party platform:** whatever the watchtower external-tool platform runs on is entirely outside this env list - we have zero configuration access to it, beyond the postMessage snippet the user added into it.

---

## HOW TO WORK ON THIS (proven-safe workflow)

1. **Clone fresh into the bash sandbox each session** (work in the scratchpad, e.g. `.../scratchpad/ip_fresh`). Sandbox network: git over `github.com` works, but `api.github.com`, GDELT, OpenAI, RSS, Google APIs are BLOCKED - live data/visuals can't be verified from the sandbox (no `service_account.json` locally either). **WebSearch/WebFetch DO work** (used heavily across the NorthStar research batches). If the sandbox resets/corrupts, rename the broken dir aside, re-clone, verify against the last known-good hash, then remove the broken copy.
2. Edit via file-edit tools or Python string-replace/slice scripts (assert exactly-one match). New templates via Write.
3. **Validate before every push:** `python3 -c "import ast; ast.parse(open('app.py').read())"`; import the app to catch route collisions AND confirm new routes registered; Jinja-render each changed template (with a fake `user`) to catch template errors; for heavy inline JS, extract and `node --check` (stub undefined globals, strip Jinja first); check `{%if%}/{%endif%}` balance; never put `{{`/`{%`/`{#` inside `<style>`/`<script>` (keep a space in `@media(...){#x`); no duplicate `@app.route`. Where possible, unit-test new data functions by monkeypatching the sheet-reader with synthetic rows. **For any data file rewrite (CSV, JSON, SQLite), always `git diff --stat` before committing and sanity-check the size/shape looks like the intended edit** - an unexpectedly huge diff on a small edit usually means a line-ending or formatting mismatch (Python's default `csv.writer` line terminator is `\r\n`; pass `lineterminator="\n"` explicitly to match a `\n`-terminated source file).
4. **For real-world research/data-population tasks** (not just code): dispatch one `general-purpose` subagent per unit of work (e.g. one company) in parallel, in a single message with multiple `Agent` tool calls. Brief them with an explicit no-fabrication instruction, the current `_quality_bar` rules, and a request to name what they couldn't find, not just what they found. Then independently recompute expected aggregate totals from the raw data and diff against what actually rendered, before pushing.
5. **For visual/UI changes on auth-gated pages, build a throwaway local preview harness** rather than trying to eyeball raw HTML: monkeypatch `_get_user` to return a fake signed-in admin (must be a real address in `ADMIN_EMAILS` for admin-gated pages), monkeypatch the data-fetch function (e.g. `_fetch_member_analytics`) with a synthetic fixture covering edge cases (linked/unlinked, missing fields, varied devices), register it as a `.claude/launch.json` server, and drive it with the browser tools (screenshot, click, JS-exec, `get_page_text`/`read_page` for structured verification) exactly like a live page. Delete/ignore the harness file and the launch.json entry afterward - it's scratch, not part of the app.
6. Push to `main` -> Railway deploys ~60-100s. If rejected, `git pull --rebase origin main` then push. Push URL = `https://x-access-token:<TOKEN>@github.com/ai-positon2/intelligence-platform` (derive repo path with `git config --get remote.origin.url | sed -E 's#https?://[^/]*/##'`). **Redact tokens in ALL output** (`sed -E 's/ghp_[A-Za-z0-9]+/[REDACTED]/g'`). The user pastes a fresh classic PAT (`repo`+`workflow`) each session and rotates it after - **remind them to rotate at the end of every session**, especially after a session with many pushes. Push without asking "should I push?" once validated; report the commit hash + a live health/route check after.
7. **Verify live in the authenticated browser.** Auth-gated pages can't be seen from the sandbox; use the Claude-in-Chrome tools (the user's real logged-in Chrome session, `reporting@position2.com` = admin) to navigate + screenshot/measure. **Poll the deploy properly, not with a fragile shell one-liner:** an unauthenticated `curl` of a route only ever shows 302 (login redirect) or 404 (route doesn't exist yet); it can NEVER show the authenticated page content - don't try to `grep` a curl body for template markers to "confirm" a UI change is live. Confirm gated-page deploys by reloading in the authenticated browser and reading the DOM/JS state directly.
8. Browser caching is aggressive - bump `?v=N` when replacing a cached CSS/JS asset in place.
9. **Standing rename rule** (see auth section) - alias URL 301 AND every read path.
10. **Never use an em dash in any written copy** (page content, UI, docs, commit messages, chat deliverables). Use commas/colons/periods/parentheses.
11. **If an agent's backend lives on a separate, unrelated third-party platform**, don't write integration instructions that assume access to that codebase - any prompt meant to be pasted into that OTHER platform must be self-contained and describe only that tool's own observable behavior, never our internal routes/slugs/architecture. This worked exactly as intended this cycle for the LinkedIn Strategy Researcher postMessage fix.
12. **A shared signal/category/label that's reused across multiple client accounts should get a per-account override parameter, never a global rename** - see the Creative Hiring -> Anesthesiologists pattern. Check `tracker/signal_score.py`'s `SIGNAL_WEIGHTS` and `dashboard_builder.py`'s `catMap` before renaming anything that looks like a fixed category string; both Healthcare and CSG share the same engine.
13. **When asked to remove items from a list the user pasted as a screenshot, verify it's actually the complete list first** - a plain-looking scrollable table can be a filtered or sorted subset. Cross-check names against the authoritative source (CSV, DB) before deleting, and when removing an entity, delete its dependent rows too (signals, snapshots) rather than just soft-deactivating it, unless the read path already correctly filters on the active flag (check this - `get_recent_alerts` here does not).
14. **When curating/pruning by a real-world date, confirm which timestamp column actually drives the filter before changing a "days" parameter** - a DB-insertion timestamp (`sent_at`) and a real event date (`signal_date`) are NOT interchangeable, and for a manually-backfilled dataset the former is a no-op proxy for age. This cost a wasted round-trip this cycle (bumped `max_signal_age_days` 90->180, rebuilt, saw zero change) before the actual fix (filtering the JSON by `signal_date` directly) was found.
15. **When a user reports a UI bug is "still not fixed" after you already shipped a fix, do not assume your first fix must have been insufficiently deployed - re-verify the EXACT reported symptom empirically against the live page** (e.g. `getBoundingClientRect()` before/after a real hover, not just "no console errors" or "one plausible-looking mechanism is gone"). This cycle's Signal Tracker hover bug had two independent causes; fixing only the first (a JS 3D-tilt effect) looked complete in isolated testing but left the actual user-visible symptom (a CSS-driven layout shift) untouched.

### Gotchas
- Two sign-in tabs with DIFFERENT column layouts: Login Log (`A:U`) = staff only; `Member Signins` (`A:T`) = non-staff. External Usage MUST read Member Signins or it shows almost nobody.
- The **Agent Runs** sheet has no client/portal column - per-client run stats are inferred by intersecting with that client's `agents` list, not read directly. It also has no run-content field - "what they ran" is joined in from Postgres `agent_run_history.title` via a FIFO queue keyed by `(email, agent_slug)`, not stored in the sheet itself.
- Do NOT use Sheets `batchGet` (empty in prod); use concurrent `values().get()` + cache.
- **`SnapshotStore.get_recent_alerts()` does not filter by `companies.is_active`** - a LEFT JOIN with no active-only WHERE clause. Deactivating a company doesn't stop its signals from counting; delete `alerts_sent` rows for real removals. **Its `max_age_days` filters on `sent_at` (insertion time), not `signal_date` (real event date)** - a no-op for manually-backfilled data; prune the source JSON by `signal_date` instead and re-seed with `--prune`.
- `admin.css` loads last and overrides inline admin CSS. `hub.css` uses spaced selectors.
- Flex item that must shrink below content needs its OWN `min-width:0`; `padding` shorthand overrides longhands.
- **A CSS rule can silently win over another of EQUAL specificity purely by being declared later in the same stylesheet** - a `.kpi-card>*{position:relative}` rule added after `.kpi-tooltip{position:absolute}` overrode it without any `!important` or higher-specificity selector involved, pulling a hidden tooltip into document flow and causing a hover-triggered layout shift. When a hover/interaction bug looks CSS-driven and the "obvious" JS handler removal doesn't fix it, suspect a later declaration-order collision next, not a caching issue.
- `templates/context.html` and `templates/linkedin_scraper.html` are filename remnants of renamed features (Playbook, LinkedIn Intelligence) - don't be misled.
- **Three different things share overlapping "LinkedIn"/signal-related names**: LinkedIn Intelligence (internal engagement-data dashboard from a Sheet) vs. LinkedIn Strategy Researcher (external competitive-analysis tool on watchtower) vs. the Signal Tracker's own News Mention/Partnership categories (which can themselves be about LinkedIn-adjacent activity). Don't conflate them when editing.
- Python's `csv.writer` default `lineterminator` is `\r\n` regardless of how the file was opened - pass `lineterminator="\n"` explicitly when rewriting a CSV that uses plain `\n`, or every line will show as changed in git even when only a few rows' content actually differs.
- `signal_type` strings in the Signal Tracker are exact-match constants shared across `signal_score.py`, `dashboard_builder.py`'s `catMap`, and any seed data - a typo or near-miss (e.g. "M&A" vs "Acquisition / M&A") silently drops that signal from every KPI/chart/filter without erroring.
- `_client_agent_view(agent_slug)` called WITHOUT the `client` argument silently breaks connectivity resolution for external-tool-only agents (no `seo_slug`) - always pass `client` when the call site knows it.
- The classifier/auto-mode may block writing config in sensitive locations, auth-bypass routes, or firing real Slack/Sheets writes - don't work around it; ask or pivot. Never test-send into Slack yourself.
- `zsh` (not bash) does NOT word-split unquoted variables - affects multi-file shell loops; prefer globs.
- macOS sandbox has no `timeout` command; don't rely on it in shell one-liners.
- Flask's `render_template` caches compiled templates process-wide; a local preview harness that edits a template mid-session needs a server restart (not just a page reload) to pick up the change.

---

## OPEN ITEMS / TODO
1. **Signal refresh secrets (blocking Healthcare refresh):** set GitHub Actions `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`, share both Healthcare Sheets with the SA `client_email` (Viewer).
2. **NorthStar client-side portal adoption is still minimal** even after this cycle's Client Usage accuracy fixes surfaced it more honestly - worth a conversation with whoever owns that relationship; not something code can fix.
3. **Assign real agents to more `/app` + client cards:** only 3 seo-apps tools + 1 external tool are wired to live surfaces; set `seo_slug` or `external_tools` to connect more.
4. **Light-theme polish** on heavy custom inline pages.
5. **Ad Intelligence data:** share `AD_INTEL_SHEET_ID` with the SA if empty.
6. **visitor_intelligence identity graph durability:** `data/identity_graph.db` is on Railway's ephemeral disk - move to a persistent volume or Postgres for long-term person continuity.
7. **Cold-visitor identification** needs a licensed identity feed - not solvable in code; plug point ready.
8. **watchtower's large-company persistence bug** (deterministic `async_jobs` UPDATE failure on big analyses) lives in the external tool's own codebase - not fixable from here.
9. **Signal Tracker maintenance mode:** the 35-company universe is fully researched with a permanent 6-month recency cutoff - the only remaining recurring task is periodically re-running `seed_northstar_signals.py --prune` after pruning `data/northstar_signals_manual.json` by `signal_date` as time passes, so signals age out on schedule rather than accumulating stale entries. Not a "next batch" anymore unless the user explicitly asks for a refresh/re-research pass.
10. **Minor doc/comment drift to clean up if touched again:** the `_client_external_tool` docstring and a `CLIENTS["northstaranesthesia"]["external_tools"]` comment in `app.py` still describe external-tool agents as "not run-metered" - stale since this cycle's fix made them run-metered on a real postMessage signal. Harmless (code behavior is correct) but worth a quick comment fix next time that function is touched.
11. **Advisory security/design audit (do not start without explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID`, cookie flags, HSTS/security headers, untrack committed `data/tracker.db`/`apollo-accounts-export.csv`, CSRF, rate limiting, SSRF/`X-Forwarded-For` hardening; CSS token convergence, adopt `ds-components.css`, self-host CDN libs, a11y.
12. **Rotate the GitHub token** shared into chat (standing reminder - especially due now, given how many pushes have used the current one across this cycle).

---

## COMPETITOR / ROADMAP (recorded, not built)
Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala. Gaps: co-op topic intent, review-site intent, technographic change, champion job-change (UserGems), hiring-surge, earnings/10-K mining, event attendance, layoffs, PLG usage. Buildable now: Earnings/Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief. Differentiators: generative-search/AI-answer visibility + agency execution + first-party web de-anon with a real engine.
