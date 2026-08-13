# Intelligence by Position2 - Full Context (v23 - August 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v23 supersedes all earlier context files (v1-v22)** - older versions are stale; ignore any pasted copy.

**Latest `main` HEAD at the end of this cycle: `d2b38d6`** (always `git pull` to confirm; Railway auto-deploys every push). `app.py` is **14,725 lines / 143 `@app.route` decorators + loop-registered `add_url_rule` families**, up from 7,973 lines at v22. The repo now has a **real automated test suite: `tests/`, 28 files, 1,122 tests, all passing** (`python3 -B -m pytest tests/ -q`). That suite did not exist at v22 and is the single biggest change in how work gets done here.

---

## WHAT V23 ADDS ON TOP OF V22 (this cycle's work)

1. **A whole new agent was designed, built, and then audited seven times: Contact Finder** (internal slug and URL still `company-people-intelligence`, ~4,500 lines of `app.py` plus a 2,068-line JS bundle plus a 585-line template). It is an Apollo-powered people/company search engine with a grounded natural-language chat, a paid-enrichment flow, a saved-search history workspace, and CSV/XLSX export. This is now the largest single feature in the codebase and most of this document's new material is about it.
2. **The `/p2/gtm` section was renamed to `/p2/b2b-agents`, URLs included** (`b3e98f9`). Every `/p2/gtm/*` path 301-redirects. `templates/gtm.html` became `templates/b2b_agents.html`. The hub card was corrected and its counts pinned to the page rather than hardcoded (`134a2b1`, `6d82c9c`).
3. **Apollo person enrichment shipped into the External Usage admin page** (profile modal, contact details, AI person read, Excel export, auto-enrich of new members, a company-by-domain fallback for people Apollo cannot match). See the dedicated section below and `[[project-person-enrichment]]`.
4. **The agent roster was reconciled across all three drifting lists** (`AGENTS`, `APP_AGENTS`, and the playbook's list in `templates/context.html`), which v22 flagged as a known hazard. The lists still drift by construction; nothing derives one from another.
5. **`/security` was removed entirely and `/privacy` + `/terms` were replaced with Position2's real published policies** (`be230be`, `0766a58`), plus a pass to genericize compliance-certification language on the pre-login pages. The platform no longer claims certifications it does not hold.
6. **Seven consecutive deep audits of Contact Finder** (search filters, Apollo vocabulary pickers, chatbot filters, enrich flow, export flow, history flow, dashboard flow), each one landing as its own commit with its own test file. Every audit found the same class of defect and it is worth internalising: **a surface asserting something its data does not support**, most often an empty or blank result reading as a fact about the world rather than a fact about the request.
7. **Mutation testing became the QA gate**, and when text assertions on a JS bundle proved unable to distinguish a working guard from a disabled one, **the repo gained its first test that executes the client bundle in node against a DOM shim** (`tests/test_cpi_dashboard_behaviour.py`).
8. **The NorthStar portal sign-in was opened to any Google account** (`4683601`), removing the client-domain restriction on that portal's front door.

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 = a B2B digital-marketing agency: SEO/organic, performance/paid media, paid social, content, brand/website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, anesthesiologist/creative hiring, news), de-anonymizes website visitors to company and (where a signal exists) person, **finds and enriches contacts at target companies via Apollo**, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), helps reps act via an embedded AI assistant (**Vimi**, visible label **GTM**), and serves **co-branded client portals** that can also embed **agents built entirely on other platforms.**

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` -> `https://seo-apps-production-37a6.up.railway.app`
- **Third-party agent frontend (NOT our code, NOT our repo):** `https://watchtower-by-position2.vercel.app`. The user builds these on an unrelated AI app-builder platform; we only receive and iframe the public URL, plus a `postMessage` run-signal snippet the user deployed into it.
- **Hosting:** Railway, auto-deploys on every push to `main` (~60-100s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push.
- **Admins (`ADMIN_EMAILS`):** `krishna.ladha@`, `sudheer.d@`, `reporting@`, `sparikh@`, `abhilash.dg@`, `pushpendra.k@` (all `position2.com`). **This set is the ONLY place admin access is defined.** `admin_required` gates every `/p2/admin/*` route off it, the template context processor derives `is_admin` from it, and `/api/whoami` returns `is_admin` from it so client-rendered surfaces read the same flag. Add a person here and nowhere else.

### FOUR SURFACES + TWO-TIER AUTH (the biggest structural fact)

Google SSO is open to **any** Google account. That forces surface separation with two auth tiers, four surfaces total:

| Surface | Who | Auth | Namespace | Theme |
|---|---|---|---|---|
| **1. Public marketing site** | Logged-out prospects | none | top-level (`/`, `/agents`, `/platform`, `/why-intelligence`, ...) | always dark |
| **2. Member workspace `/app`** | ANY signed-in Google user | `@login_required` | `/app/*` | dark |
| **3. Internal staff app `/p2/*`** | `@position2.com` only | `@position2_required` | `/p2/*` (hub, b2b-agents, seo, admin, playbook, ...) | light/dark toggle |
| **4. Client portals `/<slug>`** | any signed-in Google account (was `@position2.com` + client domains until `4683601`) | `_client_gate()` | `/<client-slug>/*` (e.g. `/northstaranesthesia`) | dark, co-branded |

- After login: `@position2.com` -> `/p2/hub`; any other signed-in user -> `/app`.
- Old top-level internal paths (`/hub`, `/gtm/...`, `/admin/...`) 301-redirect to `/p2/...`, and **`/p2/gtm/*` now 301-redirects to `/p2/b2b-agents/*`** (new this cycle).
- **Standing rename rule:** when a persisted URL/slug is renamed, the old one keeps 301-redirecting AND every read path keyed off the old slug is aliased to the new one. A past bug dropped historical runs because only routing was fixed, not the read side. See `[[feedback-persisted-identifier-renames]]`.
- Auth decorators in `app.py`: `login_required`, `admin_required` (= position2 + admin email), `position2_required`. Client gating is `_client_gate(client)` (not a decorator).

---

## CONTACT FINDER (the big new feature)

**Route base:** `/p2/b2b-agents/company-people-intelligence` (the slug was never renamed when the display name changed from "Company & People Intelligence" to "Contact Finder" in `dea726f`; the URL, the Python function prefix `cpi_*`, the helper prefix `_cpi_*`, the JS file, the CSS file and the template all still say `company_people_intelligence`). Staff-only (`@position2_required`).

**Files:** `app.py` (~lines 7160-11900, all `cpi_*` routes and `_cpi_*` helpers), `templates/company_people_intelligence.html` (585 lines), `static/js/company_people_intelligence.js` (2,068 lines, wrapped in an IIFE so **only `window.cpi*` functions are reachable from outside**), `static/css/company_people_intelligence.css`. **The template's asset version is currently `?v=20`** on both the CSS link (line ~12) and the JS script (line ~569); bump both together whenever either file changes, and there is a test pinning that they move together.

### The nine routes

| Route | Method | What it does | Apollo cost |
|---|---|---|---|
| `/company-people-intelligence` | GET | renders the page, injects every other URL | free |
| `.../search` | POST | people or company search | **0 credits** |
| `.../industries` | GET | learned industry vocabulary for the picker | free |
| `.../vocab` | GET | learned vocabulary for the other closed-list pickers | free |
| `.../enrich` | POST | one person or company full enrichment | **1 credit** |
| `.../enrich-bulk` | POST | reveal contacts for selected rows | **1 credit each**, capped |
| `.../history` | GET/POST | list, save | free |
| `.../history/<id>` | GET/DELETE | reopen, delete | free |
| `.../export` | POST | CSV or XLSX of the rows the client holds | free |
| `.../chat` | POST | grounded NL Q&A | varies, reported back |

### The credit model (the single most important design constraint)

Apollo's **search** endpoints are free; only **enrichment** spends credits from one shared agency account. Everything in this feature is shaped by that split:

- `mixed_people/api_search` and `mixed_companies/search`: **0 credits**. All browsing, filtering, counting and vocabulary learning runs here.
- `people/match`, `people/bulk_match`, `organizations/enrich`: **1 credit per record**. Only explicit user action reaches these.
- `people/bulk_match` is **capped at 10 per request** by Apollo and at **50 ids per bulk reveal** by us (`_CPI_BULK_ENRICH_CAP = 50`).
- Every code path that can spend threads a `spend = {"credits": 0}` dict through and the response carries `credits` back to the UI, so a user always sees what a question cost. `_cpi_chat_reply` attaches it to every chat branch.
- Multiple caches exist purely to avoid paying twice: `_cpi_id_cache_read/write` (person profiles, version-stamped via `_CPI_ID_CACHE_VERSION`), `_cpi_org_db_read/write` (company-name resolution, TTL `_CPI_ORG_RESOLVE_TTL_S` = 24h, **Postgres-backed so it survives a deploy**, which was a real fix in `4ef4da1`), `_cpi_firmo_db_read/write` (employer firmographics, TTL 30 days).
- `_cpi_probe_company_free` resolves a typed company name **without** spending, and staff can switch the paid company lookup off entirely (`9193163`).

### The four surfaces of the page

1. **Search** (left): entity toggle People/Companies, then the filter panel. Filters map onto Apollo's real vocabulary: titles, seniorities, industries, keywords, name, NAICS/SIC codes, technologies (any/all), market segments, hiring job titles, email status, company domains, person/company locations, and numeric spans for employees, revenue, total funding, founded year, and headcount growth. **Closed-vocabulary filters all have pickers** rather than free text (`1173dbe`), fed by `/industries` and `/vocab`, which learn real values off search results and persist them.
2. **Results grid** (centre): person or company cards. `STATE.entity` is what the next search will look for; **`STATE.shownEntity` is what the rows currently on screen actually are.** Those are separate variables and conflating them was the root of six symptoms fixed in the dashboard audit.
3. **Chat** (right): a persistent panel, not the floating Vimi widget. Its own backend.
4. **History drawer**: saved searches, chat answers, and paid reveals, reopenable into the grid.

### The verification pass (and why an empty result is not a fact)

Apollo's filters are not all honoured server-side, so `_cpi_verify_rows` re-checks each returned row against the filters the user actually set and drops rows that do not match, recording **why** in a `rejected` dict keyed by filter name with `_CPI_VERIFY_LABELS` supplying human wording. That is what lets an empty page say "Apollo returned 24 people, and on checking, none of them matched: 18 outside the industry, 6 wrong seniority" instead of "No matches". Reasons that removed nothing are filtered out and the list is sorted worst-first (`rejectedReasons()` in the JS, single source for both the count line and the empty state).

### The chat pipeline

Stateless: the client resends the full conversation each turn, same pattern as `/api/ppc-chat`, so "the second one" resolves against a company list from two turns ago without server-side session state.

1. One JSON-mode OpenAI call (`_CPI_INTENT_SYSTEM`) turns the message plus history into a structured intent.
2. **Python, not the model, decides which Apollo calls to make.** If a company name resolves to more than one plausible match, the reply is always a disambiguation payload (clickable candidate chips), never a guess. The user's pick comes back as `selected_org_id` / `selected_domain` / `selected_name` as **structured fields, not free text**, because free text goes back through the intent parser as a company name containing a domain and resolves to nothing.
3. The answer is either templated directly from the fetched JSON (zero hallucination risk) or generated by one more grounded call fed **only** the fetched facts, forbidden from stating anything not present, and then enforced in code afterward.
4. Web research (`_cpi_research`, `_cpi_web_answer`) supplements Apollo when Apollo has nothing, with citations; tracking parameters are stripped from cited URLs (`_CPI_TRACKING_PARAMS`, `_cpi_strip_tracking`), because OpenAI's web-search tool appends `?utm_source=openai` to everything it cites.
5. Every person the answer names gets an Enrich chip (`_cpi_enrich_chip`, capped at `_CPI_CHAT_ENRICH_CHIP_CAP = 6`), so enrichment stays opt-in and one click.
6. Surnames are revealed only for people the question was actually about (`_cpi_reveal_names`, cap `_CPI_CHAT_REVEAL_CAP = 10`), after a bug where the app bought surnames for everyone on the page.

### History

Postgres table `cpi_search_history(id, email, entity, label, filters JSONB, total, rows JSONB, created_at, answer)`, PK `id`, index `(email, created_at DESC)`. Retention: `_CPI_HISTORY_KEEP = 60` rows per user, `_CPI_HISTORY_MAX_ROWS = 120` result rows per entry, `_CPI_HISTORY_TTL_DAYS = 90`.

- `_cpi_history_expire(cur)` sweeps **every user's** expired rows, not just the one being served, and runs from the drawer's own read. There is no scheduler in this app, so any use of the tool by anyone retires everyone's expired rows. Cheap, because the table is capped per user.
- The sweep runs **before** the listing query, so an expired entry is never shown once and swept later.
- `_cpi_history_save(..., dedupe="")`: when a dedupe key is supplied, an existing matching entry is refreshed in place and its **credits are summed, not overwritten** (a re-enrich is normally a free cache hit, and writing 0 would erase the record of the 1 really spent).
- Three entity kinds live here: `"people"`, `"companies"`, and `"revealed"`. Bulk reveals are stored as `"revealed"` because `_cpi_person_row` output is already in search-row shape, so the entry reopens into the grid and exports like a saved search without needing a fourth reopen path.
- **Every history write is wrapped in try/except.** A failing history write must never turn a successful, already-paid-for enrichment into a 500.
- `_cpi_history_label` builds the human label from the filters (`_CPI_LABEL_LISTS`, `_CPI_LABEL_PLACES`, `_CPI_LABEL_SPANS`). Two shape rules worth keeping: the unit attaches to whichever number **ends** the phrase ("20%+", "50-200", "under 200"), and a **year is never compacted** (otherwise "founded 2K-2K").

### Export

`_CPI_PERSON_COLS` / `_CPI_COMPANY_COLS` define the column sets. `_cpi_export_cell` formats each cell; `_CPI_EXPORT_PERCENT_COLS` = `{growth6, growth12, organization_growth6, organization_growth12}` route through `_cpi_export_percent`, which multiplies the fraction Apollo sends by 100 and leaves anything unparseable exactly as sent rather than silently dropping it. Every percent column's header ends in `%` and there is a test asserting that. `_CPI_EXPORT_NON_FILTERS` keeps bookkeeping keys (`max_people`, `max_companies`, `credits`, `from_cache`, `dedupe`) off the details sheet.

**Apollo's `organization_headcount_twelve_month_growth` is a FRACTION** (0.19 = 19%, 1.5 = 150%). This was settled from repo evidence, not a live probe: the fixtures record 0.19 and 0.08, and the older External Usage export path has multiplied the same field by 100 since long before this page existed. `pmGrowth` in the JS now multiplies unconditionally. **If growth figures ever look wrong on a card, this convention is the first thing to re-check**, because the free Apollo endpoint strips organization firmographics down to id/name/domain and the app's own key only exists on Railway, so it could not be verified live from the sandbox.

### The seven audits, and the pattern they all found

Each audit is one commit plus one test file. Read the test file's module docstring first: each one states the defects in plain language.

| Audit | Commit | Test file | The recurring defect |
|---|---|---|---|
| Search filters | `0f9469b`, `b5a9fd6` | `test_cpi_filter_audit.py`, `test_cpi_industry_filter.py` | the industry filter did not filter by industry |
| Vocabulary pickers | `1173dbe`, `e55831b` | `test_cpi_vocab_pickers.py`, `test_cpi_taxonomy.py` | free text where a closed list existed; a panel clipping its own dropdown |
| Chat filters | `62dfa2c`, `c029778` | `test_cpi_chat_audit.py` | the chat accepted filter values the pickers would reject |
| Enrich flow | `af5ede0` | `test_cpi_enrich_audit.py` | paying twice, and promising phone numbers Apollo does not return |
| Export flow | `4460bed` | `test_cpi_export_audit.py` | the file did not say what the screen said |
| History flow | `8ce0409` | `test_cpi_history_audit.py` | purchases not recorded, no retention, duplicates |
| Dashboard flow | `d2b38d6` | `test_cpi_dashboard_audit.py`, `test_cpi_dashboard_behaviour.py` | the grid described the tab, not its own rows |

**The pattern, stated once:** a surface asserting something its data does not support. An empty result reading as a fact about the world rather than a fact about the request. A tab toggle doubling as a claim about what is on screen. A header promising a percent over a column holding a fraction. When auditing any other flow in this app, that is the thing to look for first, and the way to find it is to ask what makes that particular flow different from the others (history is the only surface whose data outlives the request that made it; the dashboard is where a value becomes a statement on a screen).

**One mutant was deliberately left uncaught** in the dashboard audit: making the `shownEntity` stamp unconditional. Load more is the only caller of `cpiRunSearch(false)` and it is hidden after a cross-tab switch, so the distinction is unreachable and pinning it would be a change-detector test. This is recorded in the commit message on purpose.

### Known open items inside Contact Finder

- The **chat path has never been exercised against live OpenAI/Apollo keys** end to end (sandbox network blocks both; only the free `mixed_people/api_search` was ever called via MCP).
- The **120-row history cap silently truncates a paged search**.
- **Zero-result searches are never saved to history.**
- `_cpi_probe_company_free` **guesses only `.com`** when deriving a domain from a name.

---

## TESTING DISCIPLINE (new in this cycle, and non-negotiable now)

The repo had no automated tests at v22. It now has 28 files and 1,122 tests.

```bash
cd <repo> && PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q
```

- **Always set `PYTHONDONTWRITEBYTECODE=1` / use `python3 -B`.** macOS system Python writes `.pyc` files to `/Users/<you>/Library/Caches/com.apple.python`, **outside the repo**, which once let a mutant survive a source restore and silently invalidated a mutation run.
- **Mutation testing is the gate, not the suite passing.** After writing tests for a fix, deliberately break each fixed line one at a time and confirm a test dies. A test that passes against the mutant is a test that was not testing the fix.
- **Text assertions on a JS bundle cannot distinguish a working guard from a disabled one.** `if(false) STATE.shownEntity = STATE.entity;` still contains the substring a text assertion looks for. When that mattered, `tests/test_cpi_dashboard_behaviour.py` was written: it shims `document`/`window`/`fetch`, `eval`s the real bundle in node, drives `cpiRunSearch`/`cpiSetEntity`/`cpiToggleSelect`/`cpiExport`, and asserts on the HTML produced and the request bodies captured. It **skips** rather than fails when node is absent. Reuse this harness shape for any future client-side behaviour claim.
- Tests that slice a function body out of the bundle (`_fn(name, until)`) are brittle against refactors by design, so that an assertion cannot pass by matching the same text elsewhere in the file. If one fails after a refactor, check the delimiter before assuming a regression.
- Do not write change-detector tests. If a mutant is unreachable in practice, say so in the commit message instead of pinning it.

---

## APOLLO PERSON ENRICHMENT ON EXTERNAL USAGE

Shipped this cycle into `/p2/admin/external-usage` (see `[[project-person-enrichment]]` for the full detail). Clicking a person opens a profile modal with Apollo-sourced identity, employment and contact details, labelled outbound links, and an AI read of the person. New members are auto-enriched. There is an Excel export. When Apollo cannot match a person, the flow falls back to enriching their company by email domain rather than showing nothing.

Two hard-won points that generalise:

- **Validate response shape, not just HTTP status.** A 200 with an empty payload was being cached as "this person has no data", poisoning the negative cache. `b2e5ec9` closed that hole and added a self-test.
- **Version-stamp every enrichment cache** so a fixed bug can self-heal rather than serving its own old wrong answers forever.
- Apollo hides phone numbers behind a separate nested `contact` object and often does not return them at all. Do not build UI that promises a phone number.

---

## THE NORTHSTAR ABM SIGNAL TRACKER (complete, maintenance mode)

Unchanged this cycle apart from two automated dashboard refreshes (`e85b951`, `c420fd5`). Summary retained because it is still live:

- **All 35 companies researched across 7 batches; 71 curated signals** (52 HIGH / 16 MEDIUM / 3 LOW). No pending batches.
- Data lives in SQLite (`data/tracker_northstar.db`), populated from `data/northstar_signals_manual.json` by `seed_northstar_signals.py`. **Always run with `--prune`**: without it the seed is insert-only, so curating or removing a signal in the JSON never reaches the DB. The JSON is the source of truth.
- **`_quality_bar` inside that JSON is the living curation policy.** Read it before any research or re-curation pass rather than re-deriving policy from memory. It carries a permanent **6-month admission cutoff by `signal_date`**, plus fixed categorization conventions: identifying an account's incumbent vendor -> `Partnership`; the account self-staffing -> `Creative Hiring`; new OR/ASC/hospital capacity or major equipment -> `Product Launch`; NorthStar's own job postings at an account it already services -> `Partnership`; a single robotic-system deployment -> `MEDIUM`. See `[[feedback-signal-relevance-bar]]`.
- **Canonical `signal_type` strings** are exact-match constants shared by `signal_score.py`'s `SIGNAL_WEIGHTS`, `dashboard_builder.py`'s `catMap`, and the seed data: `"Funding Round"`, `"Acquisition / M&A"`, `"IPO Signal"`, `"C-Suite Join"`, `"C-Suite Exit"`, `"News Mention"`, `"Product Launch"`, `"Partnership"`, `"Creative Hiring"`. A near-miss silently drops the signal from every KPI, chart and filter without erroring.
- **`get_recent_alerts()` does not filter on `companies.is_active`** (bare join). Deactivating a company does not stop its signals counting; delete its `alerts_sent` rows. Its `max_age_days` filters on **`sent_at` (insertion time), not `signal_date`** (real event date), which makes it a no-op for manually-backfilled data. Prune the JSON by `signal_date` and re-seed instead.
- **"Creative Hiring" displays as "Anesthesiologists" for NorthStar only**, via a `hiring_opts` dict threaded into `build_dashboard()` and substituted into `__HIRING_*__` placeholders. The stored `signal_type` never changes. **Reuse this per-account-override pattern; never globally rename a shared category.**
- `reports/dashboard*.html` are **built artifacts committed to git**. Never hand-edit them.

---

## SURFACE 4 - CLIENT PORTALS

Per-client co-branded front door at `/<client-slug>`. Only known slugs get routes. Currently one client: `northstaranesthesia`.

- **`CLIENTS` registry** entry fields: `slug`, `name`, `short`, `website`, `logo`, `domains`, `accent`/`accent2`, `tagline`, `blurb`, `agents` (ordered APP_AGENTS slugs), `dashboards` (agent-slug -> pre-built static HTML), `linkedin_sheet` (a Sheet ID that makes LinkedIn Intelligence render as a live co-branded dashboard), `external_tools` (agent-slug -> full external URL to iframe).
- NorthStar: `domains=["northstaranesthesia.com"]`, `accent="#5b9dff"`, 6 agents (`signal-tracker`, `linkedin-intelligence`, `linkedin-strategy-researcher`, `keyword-finder`, `content-brief-generator`, `content-enhancer`), `linkedin_sheet="13V-W-yG5O-OoLJHjxsPKLjrpRyRdk647GgkIGw823oE"`, `external_tools={"linkedin-strategy-researcher": "https://watchtower-by-position2.vercel.app/linkedin.html"}`.
- **Sign-in to the portal is now open to any Google account** (`4683601`), not just `@position2.com` plus the client's domains.
- **Three agent types, all keyed off the same `agents` list:** SERP-connected (`seo_slug`, run-metered via postMessage), dashboard-backed (`is_dashboard`, shown Live, never metered), external-tool (`is_external`, iframed, **run-metered on a real postMessage signal, not on page load**).
- `_client_agent_view(slug, client)`: **always pass `client`**. Omitting it silently resolves `connected=False` for an external-tool-only agent and 400s the log-run endpoints.
- Client-side adoption of the NorthStar portal remains minimal. Not something code can fix.

---

## THE EXTERNAL-TOOL PATTERN

An agent whose entire backend lives on a third-party AI app-builder platform we have no access to; we get a public URL.

1. Confirm no `X-Frame-Options`/CSP `frame-ancestors` blocks iframing.
2. Add it to `APP_AGENTS` with no `seo_slug`.
3. Add its slug to the client's `agents` list and its URL to `external_tools` (client portal), or add a small route rendering `templates/embed.html` (internal).
4. `client_embed.html` / `embed.html` iframe it; the address bar shows OUR path.
5. **Metering requires the external tool's cooperation.** It must call `window.parent.postMessage({source:'p2-agent', type:'agent-run-started'|'agent-run-finished'}, 'https://intelligence.position2.com')`, guarded by `if (window.parent !== window)`. Deployed and working for LinkedIn Strategy Researcher.
6. **Any prompt written to be pasted into that other platform must be self-contained** and describe only that tool's own observable behaviour, never our internal routes, slugs, or architecture.

---

## LINKEDIN INTELLIGENCE (internal + per-client, multi-sheet)

Route `/p2/b2b-agents/linkedin-intelligence` (both the old `linkedin-scraper` slug and the old `/p2/gtm/` prefix redirect). Renders `templates/linkedin_scraper.html`; all content drawn client-side by `static/js/linkedin.js` from `window.__LI_DATA_URL__`. The sheet is one row per person x post engagement, header-mapped so column order can drift safely.

- `_fetch_linkedin_intel_data(force, sheet_id)` with **per-sheet caches** so internal and each client portal read independent sheets.
- `client_mode` flag hides internal chrome and injects a client-gated `data_url`.
- **Do not confuse** LinkedIn Intelligence (your own engagement data from a Sheet) with LinkedIn Strategy Researcher (external competitive analysis of other companies) or with the Signal Tracker's own News Mention/Partnership categories. Three different things share overlapping names; check the route, not the label.

---

## ADMIN ANALYTICS (all `@admin_required`, each has a `.../data` JSON endpoint)

- **Internal Usage** `/p2/admin/internal-usage`: staff logins + page views, "Linked to Pre-Login" KPI, merged journey drawer via `p2_vid`, no row caps.
- **External Usage** `/p2/admin/external-usage`: everyone who signed in with a non-`@position2.com` email (reads the **`Member Signins`** tab, NOT the Login Log). Rich sortable/filterable People table including an **AI priority sort**, a "What they ran" column joining Postgres `agent_run_history.title` onto Sheets run rows via a FIFO queue keyed by `(email, agent_slug)`, and the **Apollo person profile modal** described above.
- **Client Usage** `/p2/admin/client-usage` (+ `/<slug>`): splits a portal's activity into Position2 team vs client team vs Other by email domain. A staffer's logins are derived from actual page views on that slug's path; anyone with no footprint on that portal is dropped.
- **Anonymous Traffic** `/p2/admin/anonymous-traffic`: visitor_intelligence engine, concurrent IP resolve, per-visitor drill-downs, "Signed in later" via `p2_vid`.
- **Public Page Analytics** `/p2/admin/public-page-analytics`: public member sign-ins + journeys, rich Members table below the KPI cards.
- **Public Agent Usage** `/p2/admin/public-agent-usage`: per-user/per-agent run counts vs cap.
- **Access Requests** `/p2/admin/access-requests`.

**Sheets read performance rule:** warm the IP cache concurrently, do concurrent per-thread `values().get()`, cache ~300s. **Do NOT use `batchGet`**, it returns empty in prod. See `[[feedback-sheets-read-performance]]`.

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                ← Flask server (~14,725 lines, 143 routes + loop-registered families):
│                            auth (3 decorators + client gate), all 4 surfaces, AGENTS/APP_AGENTS/
│                            SIGNALS/INDUSTRIES/CLIENTS/ACCOUNTS registries, OpenAI (Vimi x2 +
│                            Contact Finder's own chain), Contact Finder (~4,500 lines of cpi_*
│                            routes and _cpi_* helpers), marketing routes, /api/demo-request,
│                            /api/track|atrack|identify|whoami, /app/* + run history,
│                            /p2/* + admin analytics, client-portal routes, LinkedIn Intelligence
│                            (per-sheet), Postgres history. Does NOT build Signal Tracker
│                            dashboards, just serves the pre-built HTML.
├── tests/                ← 28 files, 1,122 tests. Most are named test_cpi_*.py, one per audit.
│                            test_cpi_dashboard_behaviour.py executes the JS bundle in node.
├── visitor_intelligence/ ← de-anon engine: resolver.py, pipeline.py, identity_graph.py.
│                            Tests: python3 -m visitor_intelligence.tests
├── tracker/              ← signal pipeline pkg (news_client, news_relevance, signal_score,
│                            dashboard_builder [build_dashboard(), takes hiring_opts],
│                            csv_loader, snapshot_store, sheets_client, apollo_client)
├── main.py               ← weekly orchestrator (Healthcare) -> data/tracker.db
├── build_northstar_dashboard.py, build_csg_dashboard.py
├── seed_northstar_signals.py   ← always run with --prune
├── northstar-company-details.csv
├── ad_intelligence/      ← built React app served by Flask
├── static/
│   ├── css/ds-tokens.css, ds-components.css, gtm.css, hub.css, seo.css, linkedin.css,
│   │        admin.css, aurora-app.css, grid-tokens.css, client-portal.css,
│   │        company_people_intelligence.css
│   ├── js/theme.js, linkedin.js, visitor_track.js, pfx_bg.js, aurora.js,
│   │       anonymous_visitors.js, company_people_intelligence.js (2,068 lines, IIFE)
│   ├── clients/northstaranesthesia/logo-white.svg
│   └── logo-lockup.svg, logo-mark.svg, favicon.*
├── templates/
│   ├── agents.html          ← THE SINGLE SHARED MARKETING TEMPLATE, {% if page %} variants
│   ├── app.html, app_base.html, app_embed.html, app_history*.html, app_settings.html
│   ├── hub.html, b2b_agents.html (was gtm.html), seo.html, accounts.html, embed.html,
│   │        context.html (=Playbook), 403.html
│   ├── company_people_intelligence.html   ← Contact Finder (585 lines, assets at ?v=20)
│   ├── linkedin_scraper.html   ← serves BOTH internal and client LinkedIn dashboards
│   ├── anonymous_visitors.html, call_sentiment.html
│   ├── admin_usage.html, admin_visitors.html, admin_members.html, admin_agent_runs.html,
│   │        admin_requests.html, admin_external_usage.html (2,297 lines), admin_client_usage.html,
│   │        admin_client_detail.html
│   ├── _admin_menu.html     ← the ONE shared internal admin dropdown
│   ├── client_*.html        ← client-portal shell, home, agent detail, embed, history, denied
│   └── ppc_chat_widget.html ← shared Vimi chat widget (internal only)
├── reports/          ← dashboard*.html: BUILT ARTIFACTS, committed, never hand-edited
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml
```

### Deploy + data model

- **Code/UI** push to `main` -> Railway redeploys (~60-100s). No hot reload locally.
- **Google Sheets** is the primary store for internal analytics. **Two sign-in tabs with DIFFERENT column layouts:** Login Log (default tab, `A:U`, `@position2.com` staff only, email @col 5, name @col 6) and **`Member Signins`** (`A:T`, every non-staff sign-in, email @col 5, name @col 6, picture @col 8, visitor-id @col 9, browser @col 11, OS @col 13, device @col 14). Page Views (`A:N`: 0 Timestamp IST, 1 Date, 2 Time, 3 Day, 4 Email, 5 Page Title, 6 Page URL, 7 Seconds, 8 Duration, 9 IP, 10 Browser, 11 OS, 12 Device, 13 Visitor ID). Agent Runs (`A:F`: Timestamp, Date, Email, Name, slug, AgentName; **no client column and no run-content field**). Visitor Analytics tab: pre-login journey by `p2_vid`. Demo Requests tab.
- **Postgres** (`DATABASE_URL`): `agent_run_history` (`email`, `agent_slug`, `title`, `output` JSONB, `created_at`), **`cpi_search_history`** (Contact Finder, see above), plus Contact Finder's persistent caches (org resolution, firmographics, learned vocabulary).
- **SQLite** (committed): `data/tracker.db` (Healthcare), `data/tracker_csg_v2.db` (CSG), `data/tracker_northstar.db` (NorthStar). **Gitignored, real PII, NEVER commit: `data/identity_graph.db`.**

---

## VIMI, DE-ANON, STITCHING, AND THE OTHER SURFACES

- **Vimi** (label **GTM**): two backends, `/api/ppc-chat` (widget, `@position2_required`) and `/api/vimi-chat/<account_id>`. `_build_ppc_context()` loops BOTH signal DBs with live counts (60s cache). `_VIMI_PLATFORM_KNOWLEDGE` grounds feature questions and discloses that Sentiment Pulse is mock data. Web-search fallback via `_responses_web_search()`. Never mix Healthcare and CSG in one answer.
- **Anonymous Visitors / de-anon:** `visitor_intelligence/`. Company-level multi-signal IP resolution with a **connection-type hard gate** (business/education/government identifiable; isp/mobile/hosting/proxy gated out), noisy-OR confidence, Apollo enrichment, 0-100 intent. Person-level: persistent SQLite identity graph with union-find, waterfall resolver. **Never fabricates a person.** Cold-stranger ID needs a licensed feed; the plug point is ready.
- **`p2_vid` stitching:** Page Views and both login tabs carry a visitor-id column. Rows predating the column show "unlinked", which is expected.
- **Surface 2, `/app`:** shell `app_base.html`, `APP_AGENTS` cards, 3 wired to live seo-apps tools via `seo_slug` plus LinkedIn Strategy Researcher (free and uncapped, `81efbe8`), the rest request-access-only. Run history in Postgres, `AGENT_RUN_CAP=10`. Per-agent `lock_label` and `no_request` keys control the locked-card CTA, and `no_request` is enforced server-side too (a direct POST 400s).
- **Surface 1, public site:** one template `agents.html` with an `{% if page %}` chain. Routes `/`, `/login`, `/agents`, `/agents/<slug>`, `/platform`, `/signals`, `/solutions`, `/integrations`, `/resources`, `/privacy`, `/terms`. **`/security` was removed this cycle.** Unlinked/direct-URL-only: `/industries*`, `/why-intelligence`. Honest-content principle: no fabricated logos, quotes, metrics, or certifications.
- **Surface 3, `/p2/*`:** `/p2/hub`, **`/p2/b2b-agents`** (+ `/company-people-intelligence` = Contact Finder, `/sentiment-pulse` MOCK data, `/ad-intelligence` React app, `/linkedin-intelligence`, `/linkedin-strategy-researcher`), `/p2/seo` + `/p2/seo/<tool>`, `/p2/accounts` + `/p2/signal-tracker/<account_id>`, `/p2/playbook` (template still `context.html`), and the admin dashboards.

**Agent roster hazard, still live:** the roster exists in **three independent lists** (`AGENTS` for every pre-login marketing page, `APP_AGENTS` for `/app`, and a JS array in `templates/context.html` for the playbook), plus the internal SEO Suite tools list. **Nothing derives one from another.** They were reconciled this cycle but will drift again on the next roster edit. Two order-dependent slices exist, `agents[:4]` (home orbit) and `agents[:3]` (featured cards), so **reordering `AGENTS` silently changes what the homepage features.**

---

## BRANDING + THEME

"Arena" mark: bright-green hexagon `#55be8c` + steel-blue + dark-green petals = 6-point star. `logo-lockup.svg` in internal topbars. `theme.js` (`localStorage['p2-theme']`, default dark, `window.P2toggleTheme`); public, `/app` and client portals stay dark, only `/p2/*` toggles. Hard sign-out: `/logout` sends `Clear-Site-Data` + explicit cookie deletion + `no-store` (do not touch `session.permanent` after `.clear()`). Bricolage Grotesque is the public body font.

---

## ENVIRONMENT VARIABLES

**Railway:** `DATABASE_URL`, `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`/`FLASK_SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), **`APOLLO_API_KEY` (Contact Finder + de-anon + person enrichment all depend on this one shared key and its shared credit pool)**, `VI_ENRICH_ON_VIEW` (opt), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt), `SMTP_*` (unusable on Railway).
**GitHub Actions secrets (separate):** `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

---

## HOW TO WORK ON THIS (proven-safe workflow)

1. **Clone fresh into the bash sandbox each session** (scratchpad, e.g. `.../scratchpad/ip`). Sandbox network: git over `github.com` works; `api.github.com`, OpenAI, Apollo, Google APIs, GDELT and RSS are **BLOCKED**, so live data cannot be verified from the sandbox and there is no `service_account.json` locally. **WebSearch/WebFetch DO work.** If the sandbox corrupts, rename the broken dir aside, re-clone, verify against the last known-good hash, remove the broken copy.
2. Edit via file-edit tools or Python string-replace scripts (assert exactly-one match). New files via Write.
3. **Validate before every push, in this order:**
   - `PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q` (must be 1,122+ passing)
   - **mutation-test the actual fix**: break each fixed line, confirm a test dies, restore
   - `python3 -c "import ast; ast.parse(open('app.py').read())"`
   - import the app to catch route collisions and confirm new routes registered
   - Jinja-render each changed template with a fake `user`
   - for heavy inline JS, extract and `node --check` (stub globals, strip Jinja first)
   - `git diff -U0 | grep '^+' | grep $'—'` must be empty (no em dashes on added lines)
   - for any data-file rewrite, `git diff --stat` and sanity-check the shape
4. **For real-world research/data-population tasks:** one `general-purpose` subagent per unit of work, dispatched in parallel in a single message. Brief them with an explicit no-fabrication instruction and a request to name what they could NOT find. Then independently recompute expected aggregates from the raw data and diff against what rendered.
5. **For visual/UI changes on auth-gated pages, build a throwaway local preview harness:** monkeypatch `_get_user` to return a fake signed-in admin (must be a real address in `ADMIN_EMAILS`), monkeypatch the data fetch with a synthetic fixture covering edge cases, register it in `.claude/launch.json`, drive it with the browser tools. **`.claude/launch.json` goes ONLY at `/Users/teampx/.claude/launch.json`, never inside the repo.** Delete the harness and the launch.json entry afterward.
6. **Push only after explicit user confirmation**, because Railway auto-deploys `main` to production. Push URL = `https://<TOKEN>@github.com/ai-positon2/intelligence-platform`. **Redact the token in ALL visible output:** `sed -E 's#ghp_[A-Za-z0-9]+#[REDACTED]#g'`. The user pastes a fresh classic PAT each time and **it must be rotated afterward**; flag this every single push. Report the commit hash after.
7. **Verify live in the authenticated browser.** An unauthenticated `curl` of a gated route only ever shows 302 or 404; it can never confirm a UI change. Use the Claude-in-Chrome tools (the user's real logged-in Chrome, `reporting@position2.com` = admin), reload, and read the DOM/JS state directly.
8. **Browser caching is aggressive.** Bump `?v=N` when replacing a cached CSS/JS asset in place, and move CSS and JS together.
9. **Never use an em dash in any written copy**, anywhere: page content, UI strings, docs, commit messages, chat replies. Use commas, colons, periods, parentheses. See `[[feedback-no-em-dashes]]`.
10. **A shared signal/category/label reused across client accounts gets a per-account override parameter, never a global rename.**
11. **When a user reports something is "still not fixed", re-measure the EXACT reported symptom empirically** against the live page rather than assuming your first fix must have been under-deployed. A past hover bug had two independent causes and fixing only the first looked complete in isolation.
12. **Never commit `data/identity_graph.db`** (gitignored, real PII). Never put personal or sensitive data in URL parameters or query strings.
13. Apollo's **paid** MCP endpoints (`apollo_mixed_companies_search`, `apollo_organizations_enrich`, `apollo_organizations_job_postings`, `apollo_people_match`, `apollo_people_bulk_match`) carry a mandatory-confirmation instruction. Only `apollo_mixed_people_api_search` is free. Confirm before spending from the shared pool.

### Gotchas

- `templates/context.html` (Playbook) and `templates/linkedin_scraper.html` (LinkedIn Intelligence) are filename remnants of renamed features. So is the entire `company_people_intelligence` naming for Contact Finder. Do not be misled.
- The Contact Finder JS bundle is **wrapped in an IIFE**: only `window.cpi*` functions are reachable externally. A test or harness cannot call an internal helper directly.
- `admin.css` loads last and overrides inline admin CSS. `hub.css` uses spaced selectors.
- A flex item that must shrink below its content needs its OWN `min-width:0`; `padding` shorthand overrides longhands.
- **A CSS rule can silently win over another of EQUAL specificity purely by being declared later.** When a hover/interaction bug looks CSS-driven and removing the obvious JS handler does not fix it, suspect declaration order before suspecting caching.
- Never put `{{`, `{%` or `{#` inside `<style>`/`<script>`; keep a space in `@media(...){#x`.
- Python's `csv.writer` default `lineterminator` is `\r\n` regardless of how the file was opened. Pass `lineterminator="\n"` when rewriting a `\n`-terminated CSV or every line shows as changed.
- Flask's `render_template` caches compiled templates process-wide; a preview harness that edits a template mid-session needs a server restart, not a reload.
- `zsh` does not word-split unquoted variables. macOS sandbox has no `timeout` command.
- The classifier/auto-mode may block writing config in sensitive locations or firing real Slack/Sheets writes. Do not work around it; ask or pivot. Never test-send into Slack.

---

## OPEN ITEMS / TODO

1. **Rotate the GitHub token.** It has been used for 34+ consecutive pushes and pasted into chat 34+ times. This is the one piece of cleanup the assistant cannot do itself; flag it every session.
2. **Contact Finder's chat path has never run against live OpenAI + Apollo keys.** Everything is tested against fixtures and the free search endpoint. First live use should be watched.
3. **Contact Finder residuals:** the 120-row history cap silently truncates a paged search; zero-result searches are never saved to history; `_cpi_probe_company_free` guesses only `.com`.
4. **Verify the headcount-growth unit against live Apollo** the first time someone with the production key looks at a fast-growing company. The fraction convention was settled from repo evidence, not a live probe.
5. **Hardcoded counts still in the codebase:** the hub band's "300+ live signals" understatement, `ACCOUNTS["healthcare"]["description"]`'s hardcoded "1,251", and four places in `templates/agents.html`. The B2B Agents hub card counts were fixed this cycle; these were not.
6. **Signal refresh secrets (blocking Healthcare refresh):** set GitHub Actions `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`, share both Healthcare Sheets with the SA `client_email` (Viewer).
7. **Agent roster will drift again.** Three independent lists, nothing derived. Consider deriving them if the roster changes materially.
8. **Assign real agents to more `/app` + client cards.** Only 3 seo-apps tools + 1 external tool are wired to live surfaces.
9. **NorthStar client-side portal adoption is minimal.** A relationship conversation, not a code fix.
10. **`data/identity_graph.db` is on Railway's ephemeral disk.** Move to a persistent volume or Postgres for long-term person continuity.
11. **Cold-visitor identification** needs a licensed identity feed. Plug point ready.
12. **Light-theme polish** on heavy custom inline pages (SEO Studio, LinkedIn, Anonymous Visitors, some Sentiment Pulse widgets).
13. **Signal Tracker maintenance mode:** the only recurring task is periodically pruning `data/northstar_signals_manual.json` by `signal_date` and re-running `seed_northstar_signals.py --prune` so signals age out on schedule.
14. **Advisory security/design audit (do not start without an explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID`, cookie flags, HSTS/security headers, untrack committed `data/tracker.db` and `apollo-accounts-export.csv`, CSRF, rate limiting, SSRF/`X-Forwarded-For` hardening; CSS token convergence, adopt `ds-components.css`, self-host CDN libs, accessibility.
15. **Stale doc/comment drift:** the `_client_external_tool` docstring and a `CLIENTS[...]["external_tools"]` comment still describe external-tool agents as "not run-metered". Harmless (behaviour is correct), worth a fix next time that code is touched.

---

## COMPETITOR / ROADMAP (recorded, not built)

Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala. Gaps: co-op topic intent, review-site intent, technographic change, champion job-change (UserGems), hiring-surge, earnings/10-K mining, event attendance, layoffs, PLG usage. Buildable now: Earnings/Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief. Differentiators: generative-search/AI-answer visibility + agency execution + first-party web de-anon with a real engine + **a working Apollo contact-finding surface with honest credit accounting**.
