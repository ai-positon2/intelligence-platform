# Intelligence by Position² — Full Context (v14 · July 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v14 supersedes all earlier context files (v1–v13)** — those are stale; ignore/delete them.

What v14 adds on top of v13: (1) a full **Industries** section on the public site (nav tab + `/industries` list + per‑industry pages, with **Healthcare** as the deep showpiece and clickable per‑agent **detail pages** at `/industries/<industry>/agents/<agent>`); (2) two new healthcare agents (**Call & Sentiment Intelligence**, **Healthcare Account Tracker**) and a full **Call & Sentiment Intelligence dashboard** inside GTM (`/gtm/call-sentiment`, proxy data, fully interactive); (3) a batch of **admin dashboard polish** (KPI value bug fix + per‑card colour); (4) **favicon unified** across all pages; (5) **Resources** removed from the public header nav; (6) **signal‑pipeline resilience** fixes (faster RSS circuit breaker, removed the Google‑News‑RSS creative/3D‑hiring fetch steps, GDELT circuit breaker, tolerant config loading); (7) documentation of the **GitHub Actions secret setup** required for the refresh workflow. **Latest `main` HEAD at end of this cycle: `e74c417`** (plus any follow‑ups; always `git pull` to confirm).

---

## WHAT THIS IS

**Intelligence by Position²** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 is a B2B digital-marketing agency: SEO & organic growth, performance/paid media, paid social, content, brand & website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymises website visitors, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), and helps reps act via an embedded AI assistant called **Vimi** (visible label **GTM**).

There are **two surfaces**:
1. **Public / pre-login marketing site** — what prospects see. Sells the product; primary CTA "Request access"; sign-in is secondary; a "Watch the walkthrough" video button is prominent. Runs a first-party visitor-analytics tracker for anonymous visitors.
2. **Internal app** (behind Google SSO, `@position2.com` only) — the hub, GTM & SEO (SEO Studio) tool pages, Signal Tracker dashboards, Vimi, admin (Usage / Visitor Analytics / Access Requests).

- **Live URL:** `https://intelligence.position2.com`
- **GitHub:** `https://github.com/ai-positon2/intelligence-platform` (main app)
- **Hosting:** Railway — auto-deploys on every push to `main` (~90s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; signal data refreshes via GitHub Actions.
- **Auth:** Google SSO, `@position2.com` only. `@login_required` on protected routes; `@admin_required` for `/admin/*`.
- **Admins:** `krishna.ladha@position2.com`, `sudheer.d@position2.com` (set in `ADMIN_EMAILS`).

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py            ← Flask server (~3,600+ lines): routes, API, OpenAI (Vimi), insights, auth,
│                       AGENTS + SIGNALS + INDUSTRIES data, marketing routes, /api/demo-request,
│                       /api/track, /api/atrack, /api/identify, /admin/usage|visitors|requests,
│                       /favicon.ico|.svg, SEO Studio proxy, GTM sub-routes (incl. /gtm/call-sentiment)
├── main.py           ← Weekly orchestrator for HEALTHCARE account (Sheets HIGH + News LOW) -> data/tracker.db
├── fetch_csg_*.py    ← CSG fetchers (news/jobs/sheets)
├── fetch_healthcare_jobs.py ← Healthcare creative/3D hiring via Google News RSS (UNHOOKED from refresh workflow in v14)
├── weekly_digest.py  ← Ranks companies; writes reports/opportunities_<acct>.csv
├── tracker/          ← signal pipeline pkg (news_client.py [GDELT/SerpAPI + RSS], news_relevance.py,
│                       signal_score.py, dashboard_builder.py, sheets_client.py, notifier_slack.py, ...)
├── static/
│   ├── css/ds-tokens.css       ← internal design tokens (Space Grotesk; --bg,--s1..3,--b1..3,--tx*,--accent,etc.)
│   ├── css/ds-components.css    ← internal shared components
│   ├── css/gtm.css             ← GTM hub styles (topbar, user-pill, bg-grid, dash-card, c-* accents incl. c-sentiment)
│   ├── css/admin.css           ← shared admin design system (Usage/Visitor/Requests) + multi-accent KPI palette
│   ├── favicon.svg             ← ★ NEW v14: canonical favicon (indigo signal-wave)
│   ├── js/visitor_track.js     ← first-party anonymous visitor tracker (public pages)
│   ├── js/pfx_bg.js            ← shared Three.js WebGL particle background (admin + LinkedIn + call-sentiment)
│   ├── js/anonymous_visitors.js, js/linkedin.js
├── templates/
│   ├── agents.html             ← ★ THE SINGLE SHARED MARKETING TEMPLATE. Every public page is a {% if page %} variant.
│   │                             page values: home, agents, agent, platform, signals, solutions,
│   │                             integrations, resources, security, login, privacy, terms,
│   │                             industries, industry, iagent   (industries/industry/iagent NEW v14)
│   ├── call_sentiment.html     ← ★ NEW v14: Call & Sentiment Intelligence dashboard (GTM sub-page)
│   ├── admin_usage.html, admin_visitors.html, admin_requests.html
│   ├── hub.html, gtm.html, seo.html, accounts.html, embed.html, 403.html
│   ├── linkedin_scraper.html, anonymous_visitors.html
│   ├── login.html / login_preview.html / login_old_backup.html  ← NOT used by /login (see note)
│   └── ppc_chat_widget.html    ← SHARED Vimi chat widget (internal app)
├── reports/          ← dashboard.html / dashboard_csg.html (Signal Tracker dashboards, generated + Vimi-customised)
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml
```

### Deploy & data model
- **Code/UI** push to `main` → Railway redeploys (~90s). No hot reload.
- **Signal data** refreshed by GitHub Actions (`refresh-dashboards.yml`), which commits updated DBs + dashboards.
- **Signal Tracker dashboards** are GENERATED + Vimi-customised — never hand-edit `reports/dashboard*.html` structure; patch `tracker/dashboard_builder.py` AND splice the single-line `const DATA`.
- **Google Sheets is the data store** for: login log + page views ("Page Views" tab), demo/access requests ("Demo Requests" tab), anonymous visitor analytics ("Visitor Analytics" tab), person-level identities ("Visitor Identities" tab) — all via `GOOGLE_SA_JSON` service account against `LOGIN_LOG_SHEET_ID` / `DEMO_REQUEST_SHEET_ID`. The **Signal Tracker** HIGH signals come from separate Google Sheets configured in `CONFIG_YAML` (see "Signal refresh setup" below).

---

## ★ THE PUBLIC MARKETING SITE

### Single-template architecture — read this first
The **entire public site is rendered from ONE Jinja template, `templates/agents.html`**, via a `{% if page == '...' %}` chain in `<main>`. Routes in `app.py` render this template with a `page` variable. The nav, footer, "Request access" modal (`#nvfov`), video modal (`#vmodal`), animated background, custom cursor, page loader, and big inline `<script>` IIFE are all **shared across every public page**. There is a matching `{% elif page %}` chain in `<title>` and a per-page accent dict on `<body data-page style="--page:…;--page2:…">`.

### Public routes (all in app.py)
| Route | page= | Purpose |
|-------|-------|---------|
| `GET /` | `home` | Public homepage. Redirects to `/hub` if logged in. |
| `GET /login` | `login` | Centered Google sign-in card. **Renders agents.html (page=login); header nav links are HIDDEN on login.** |
| `GET /agents` | `agents` | Agent directory (19 agents). |
| `GET /agents/<slug>` | `agent` | Per-agent detail. Unknown slug → 302 `/agents`. |
| `GET /platform`,`/signals`,`/solutions`,`/integrations`,`/resources`,`/security`,`/privacy`,`/terms` | resp. | Marketing pages. |
| `GET /industries` | `industries` | ★ NEW v14: industry list (clickable cards). |
| `GET /industries/<slug>` | `industry` | ★ NEW v14: per-industry page (unknown → 302 `/industries`). |
| `GET /industries/<islug>/agents/<aslug>` | `iagent` | ★ NEW v14: per-industry agent detail (unknown → 302 to industry). |
| `POST /api/demo-request` | — | "Request access" intake → Sheet + Slack + email; records `p2_vid`. Public. |
| `POST /api/atrack` | — | anonymous visitor-analytics ingest → "Visitor Analytics" tab. Public. |
| `POST /api/identify` | — | person-level identity ingest. Gated by `IDENTIFY_TOKEN`. → "Visitor Identities" tab. |
| `GET /favicon.ico`, `GET /favicon.svg` | — | ★ NEW v14: serve `static/favicon.svg`. |
| `GET /admin/usage` (+`/data`) | — | Usage dashboard. `@admin_required`. |
| `GET /admin/visitors` (+`/data`) | — | Anonymous Visitor Analytics dashboard. `@admin_required`. |
| `GET /admin/requests` | — | Access-request submissions. `@admin_required`. |
| `GET /admin/email-test` | — | Email diagnostic (JSON). `@admin_required`. |

NOTE: `/customers` 404s; `/context-graph` 404s (removed in v13 — do NOT reintroduce).

### Visual / design system (public site)
Fonts: Bricolage Grotesque (display), Instrument Serif italic (accents), Inter (body), JetBrains Mono (labels/`--mono`). Tokens in `:root` (`--bg:#040510`; cyan `#22d3ee`, violet `#8b5cf6`, indigo `#6366f1`, pink `#e879f9`, lime `#a3e635`; `--dim/--dim2/--dim3`, `--line/--line2`). Per-page `<body data-page>` accent + ambient Three.js nebula (+2D fallback), custom cursor, 2-col heroes, colored tiles. Heavy use of `color-mix(in srgb, var(--page) N%, …)`. Card mouse-spotlight pattern: elements with `--mx/--my` set by a `mousemove` handler inside the big inline IIFE (`.tilt`, `.fcard`, `.acard`, and NEW `.ind-agent`).

### Nav (header)
Header links (in `agents.html` nav, hidden on login): **Platform · Signals · Agents · Solutions · Industries**. **"Resources" was REMOVED from the header nav in v14** (the `/resources` route and the footer "Resources" column still exist; only the header tab was removed).

### Agents — `AGENTS` in app.py (19)
signal-tracker (ABM Signal Tracker), generative-search-visibility (FLAGSHIP), anonymous-visitors, technical-seo-geo-auditor, ai-readiness-auditor, keyword-opportunity-engine, content-brief-architect, content-authority-optimizer, competitor-seo-intelligence, local-visibility-builder, search-term-intelligence, linkedin-intelligence, ad-intelligence, pipeline-command-center, gbp-qc-agent, on-page-auditor, hub-spoke-architect, robots-monitor, article-enhancer. Each AGENTS dict: slug, name, role, metric, badge, icon (`_svg(inner)`), accent, summary, benefit, how, who, connects, cat. Categories: Signals, GEO, Web, SEO, Content, Paid, Social, Analytics. `AGENTS_BY_SLUG` maps slug→dict.

### Signals — 26 signal _types_ (catalog). `/signals` is a numbered editorial timeline.

### Honest-content principle (enforced)
No fabricated logos/quotes/metrics; previews labeled "representative." The one hard stat kept is "Recovers 95%+ of lost visitors" (industry figure).

### "Request access" lead form + notifications
Shared modal `#nvfov` → `POST /api/demo-request`. Notifiers: sheet, Slack (channel `C0BE016E2E8` = #intelligence-platform-request-access), email. Recipients via `DEMO_NOTIFY_EMAIL`.

### Email transport (Gmail API over HTTPS) — STILL PENDING two admin steps
Railway blocks SMTP. Code sends via Gmail API (HTTPS) when `GMAIL_SENDER` is set, reusing `GOOGLE_SA_JSON`. PENDING: (1) domain-wide delegation for the SA Client ID, scope `gmail.send`, enable Gmail API; (2) set `GMAIL_SENDER` to a real mailbox. Then `/admin/email-test` → `method:"gmail_api"`, `SENT OK`.

---

## ★ NEW IN v14 — INDUSTRIES SECTION (public site, in agents.html)

### Data — `INDUSTRIES` list in app.py (right after `AGENTS_BY_SLUG`)
Helper `_isvg(inner)` (1.9-stroke variant of `_svg`). `INDUSTRIES_BY_SLUG` maps slug→dict. Four industries:
- **healthcare** — `featured: True`, accent `#22d3ee`/`#34d399`. The deep showpiece.
- **technology-saas** — accent `#818cf8`/`#22d3ee`.
- **financial-services** — accent `#34d399`/`#0ea5e9`.
- **professional-services** — accent `#fbbf24`/`#e879f9`.

Each industry dict: `slug, name, short, featured, accent, accent2, icon, eyebrow, headline, headline_ital, lead, stats[{v,l}], segments[], pains[{t,d}], signals[], agents[…], plays[{t,d}]`.

**Healthcare agents (11, each a full detail-page object)** — fields: `slug, name, base, badge, accent, accent2, role, metric, icon, use (card blurb), summary, benefit, how, who, connects[], out[{t,s,w}]`. The 11 (order shown on page):
1. Call & Sentiment Intelligence (NEW) — `call-sentiment-intelligence`
2. Healthcare Account Tracker (LIVE) — `healthcare-account-tracker`
3. Provider & Payer Signal Tracker (CORE) — `provider-payer-signal-tracker`
4. Patient-Answer Visibility (FLAGSHIP) — `patient-answer-visibility`
5. Patient & Referrer De-anonymization (NEW) — `patient-referrer-de-anonymization`
6. HIPAA-Aware Site & GEO Auditor (NEW) — `hipaa-aware-site-geo-auditor`
7. Clinical Authority Optimizer — `clinical-authority-optimizer`
8. Condition & Treatment Brief Architect — `condition-treatment-brief-architect`
9. Location & Facility Visibility — `location-facility-visibility`
10. Buying-Committee Tracker — `buying-committee-tracker`
11. Referral & Pipeline Command Center — `referral-pipeline-command-center`

The three non-healthcare industries have ~5 lighter agents each WITHOUT `slug` (so their cards are NOT clickable — only healthcare agents open detail pages).

### Template blocks in agents.html
- `{% elif page == 'industries' %}` — hero + `.ind-grid` of `.ind-card` (featured card is larger, 2-col with animated `.ind-orb`).
- `{% elif page == 'industry' %}` — hero2 + animated `.ind-hero-art` orb, `.stats`, `.ind-seg` chips, challenges (`.ind-flow`/`.icard`), featured-only "signal → action" journey visual (`.ind-journey`/`.ij-*`, healthcare only), reframed-agent grid (`.ind-agents`/`.ind-agent`), signal chips (`.ind-sigs`), plays, "more industries" chips, CTA band.
- `{% elif page == 'iagent' %}` — reuses the polished **agent-detail** layout (`.crumb`, `.detail-head/.halo`, `.panel/.trips/.trip`, `.side/.card/.cta-card`, `.aout` representative-output built from `agent.out`, `.related`). Breadcrumb: Industries › {short} › {name}. `--page` = agent.accent.
- `.ind-agent` cards: cursor spotlight (`--mx/--my`), glowing animated icon+ring, accent top-bar, "from {base}" pill, per-card accent set inline via `style="--page:{{a.accent}};--page2:{{a.accent2}}"`, clickable (`<a>` to `/industries/{{industry.slug}}/agents/{{a.slug}}`) with a "View details →" affordance; equal-height flex layout. Non-slug agents render as non-clickable `<div>`.
- CSS lives in the main `<style>` in agents.html (prefix `ind-`, `ia-`, `ij-`); body data-page dict + mesh backgrounds handle `industries`/`industry`/`iagent`.

### NO EM-DASHES rule (v14)
Per user request, **no em-dashes ("—" or `&mdash;`) anywhere in the Industries data or the industries/industry/iagent template blocks**, and the shared chrome that renders on those pages (request-access modal text, success messages, footer tagline, video-modal heading) was also scrubbed. Keep future healthcare/industry copy em-dash-free.

---

## ★ NEW IN v14 — CALL & SENTIMENT INTELLIGENCE DASHBOARD (internal, GTM)

- **Route:** `GET /gtm/call-sentiment` (+ trailing slash), `@login_required`, renders `templates/call_sentiment.html`. Card added to the GTM hub grid in `gtm.html` (`c-sentiment` accent class defined in `gtm.css`).
- **Purpose (display-only, PROXY DATA):** voice-of-patient sentiment unified across calls, Google reviews & surveys, scored per location. There is NO real agent/pipeline behind it — it is a beautiful, fully interactive UI mockup only. Labeled network "Cedar Valley Health"; **the word "Demo data" was intentionally removed** from the UI (still proxy data internally).
- **Design:** matches internal design system (ds-tokens + ds-components + gtm.css topbar/breadcrumb/user-pill), plus a big inline `<style>` (prefix `cs-`) and a self-initializing engine `<script>`. Includes the shared **`pfx_bg.js`** particle background. `--pos:#2dd4aa; --neu:#f5a623; --neg:#fb7185`.
- **Engine (vanilla JS, seeded PRNG `mulberry32`):** generates ~1,000 proxy interactions across 10 NC/SC/VA locations × 3 sources (call/review/survey) × 10 topics/emotions/snippets. Filters (period 7/30/90/all, region, location, source, sentiment, topic, search, reset) recompute everything. KPIs (interactions, calls, avg call sentiment, GBP rating, survey NPS, negative alerts), SVG charts (sentiment-over-time area/line, source donut, distribution, gauge, emotion), sortable per-location table with delta + slide-in drawer, theme drivers, call-reasons, filterable interactions feed. All charts are hand-built SVG/CSS — no external chart libs. Section labels: "Overview / By location / Drivers & verbatims". Filter bar is NOT sticky (was fixed in v14 after it collided with the fixed topbar).

---

## ★ ADMIN DASHBOARDS (Usage / Visitor Analytics / Access Requests)

Shared `static/css/admin.css` (linked last, overrides inline). Breadcrumb `Hub › Admin › {Page}`, 3-dashboard user-pill menu, shared `pfx_bg.js` particle bg. `.kpis .kpi` / `.stats .stat` use a **6-colour nth-child palette** (indigo/violet/green/amber/cyan/pink): coloured top bar, per-accent tinted card **background wash + border**, corner glow.

### v14 admin fixes (in admin.css / admin_visitors.html)
- **KPI value bug FIX:** values were gradient-**clipped-to-text** (`background-clip:text; color:transparent`); when the clip failed the gradient filled the box and hid the number (showed as solid "bars"). Now rendered as **solid, per-accent coloured numbers** with `-webkit-text-fill-color` + soft glow + tabular-nums.
- **More colour + readability:** per-card accent background wash + tinted border, brighter/larger corner glow, thicker top accent bar, brighter uppercase labels (`~#aeb9dd`) and sublines (`~#cfd7f4`).
- **Visitor Analytics KPIs:** the **Rage clicks** headline KPI was **removed** (8 KPIs remain), laid out as a fixed **4-per-row** grid (two balanced rows; responsive to 2/1). Note: a separate lower "Rage-click pages" section still exists.

---

## ★ FAVICON (v14)
Single canonical favicon everywhere: an **indigo (`#6366f1`) rounded-square with a white signal-wave**. Source of truth: `static/favicon.svg` + `/favicon.ico|.svg` routes. All templates that had a `<link rel="icon">` were unified to the same data-URI; `admin_visitors` (had none) got one; `admin_requests` (truncated) and `call_sentiment` (was teal) were corrected. (Browsers cache favicons hard — hard-refresh to see changes.)

---

## ★ SIGNAL PIPELINE + REFRESH (important — recent trouble area)

### How the "Refresh signals" button works
The app's Refresh modal triggers the GitHub Actions workflow **`refresh-dashboards.yml`** (via `workflow_dispatch`, using Railway var `GH_DISPATCH_TOKEN`). That workflow, on a GitHub runner:
1. Writes `config.yaml` from repo secret **`CONFIG_YAML`** and `service_account.json` from **`GOOGLE_SERVICE_ACCOUNT_JSON`**.
2. Fetch steps (all `continue-on-error: true`): Healthcare signals (`main.py`: Sheets HIGH + GDELT news LOW), CSG news (`fetch_csg_news.py`), CSG HIGH signals (`fetch_csg_sheets.py`).
3. Rebuild dashboards (`scripts/refresh-dashboards.py`), weekly brief (`weekly_digest.py`), commit & push.

### v14 pipeline changes (all in the repo now)
- **Removed the two Google-News-RSS creative/3D hiring fetch steps** (`fetch_healthcare_jobs.py`, `fetch_csg_jobs.py`) from `refresh-dashboards.yml` — Google News RSS 503-blocks GitHub runner IPs and stalled refreshes. Scripts remain in the repo but are unhooked.
- **RSS circuit breaker** in `tracker/news_client.py` trips faster: `_CIRCUIT_THRESHOLD 30→12`, `_FEED_RETRIES 2→1`, `_FEED_BACKOFF 1.5→0.8` (bail in ~15s when Google IP-blocks CI).
- **GDELT circuit breaker** added (`_GDELT_THRESHOLD=15`, `_gdelt_open`, harder backoff on HTTP 429) — GDELT also rate-limits shared CI IPs (429).
- **`main.py` `_load_config` hardened:** returns `{}` (with a clear yellow warning) instead of `None` when `config.yaml` is empty, so the run no longer crashes with `'NoneType' object has no attribute 'get'`; the dry_run check is guarded with `(config or {})`.

### ★ Signal refresh SETUP (GitHub Actions secrets — REQUIRED, currently the live blocker)
The refresh crashes/does nothing until the repo has these **GitHub Actions repository secrets** (Settings → Secrets and variables → Actions). **These are SEPARATE from Railway variables** — Railway powers the live web app; the Action reads only GitHub secrets. As of this session the repo had **NO secrets set** (that is the root cause of the empty-config crash).
- **`GOOGLE_SERVICE_ACCOUNT_JSON`** = the full service-account JSON (same value as Railway's `GOOGLE_SA_JSON`).
- **`CONFIG_YAML`** = the tracker config. `credentials.google_service_account_json: "service_account.json"`; `google_sheets` block with `*_sheet_id` + `*_tab` for the HIGH-signal Google Sheets. The user's two live Healthcare sheets:
  - C-suite: id `16M_DLwIhbKuQAv_Cafxl8krrNWO5iaTrBJdQaN9EI6g`, tab **"C-suite signals"** (columns: Company Name | Domain | Person Name | Title | Action | Start Date | LinkedIn URL | Notes — exact match for the parser).
  - Funding: id `1nhu07HCyctjs5gfGdchu_n7Rxnpip0xVMwB9nIF_wa0`, tab **"Funding signals"** (columns: Scan Date | Company Name | Signal Type | Funding Stage | Amount | … | Confidence | Summary).
- **Also share both Google Sheets with the service account's `client_email` (Viewer)** — otherwise the Action reads config but gets zero rows (most common miss).
- The Signal Type routing map + column readers live in `main.py` (~lines 134–260): funding rows read `Signal Type` / `Funding Stage`; csuite rows read `Action` / `Person Name` / `Start Date`. `tracker/sheets_client.py` reads each sheet via `values().get(range=tab_name)` (bare tab names with spaces work, per the app's own `"Page Views"`/`"Visitor Analytics"` usage). M&A/IPO/subsidiary sheets are optional (skipped if unset).

Working `CONFIG_YAML` value handed to the user this session:
```yaml
input:
  csv_file: "apollo-accounts-export.csv"
credentials:
  google_service_account_json: "service_account.json"
  serpapi_key: ""
  slack_webhook_url: ""
  dashboard_url: "https://intelligence.position2.com"
google_sheets:
  csuite_sheet_id: "16M_DLwIhbKuQAv_Cafxl8krrNWO5iaTrBJdQaN9EI6g"
  csuite_tab: "C-suite signals"
  funding_sheet_id: "1nhu07HCyctjs5gfGdchu_n7Rxnpip0xVMwB9nIF_wa0"
  funding_tab: "Funding signals"
signals:
  max_signal_age_days: 90
  news_ai_filter: false
  news_relevance_min_score: 2
behaviour:
  dry_run: false
  enrich_news: true
  dedup_window_days: 7
  force_csv_baseline: false
```

---

## ★ INTERNAL /seo TAB — "SEO Studio" (unchanged)
Proxies to `https://github.com/ai-positon2/seo-apps` (live `https://seo-apps-production-37a6.up.railway.app/`). `_seo_tools()` falls back to `_SEO_TOOLS_FALLBACK` (15 tools incl. gbp-qc-agent). `/seo/<slug>` embeds via `?pt=<SERP_PLATFORM_TOKEN>`. PENDING: GBP embed (`gbp-qc-agent-production.up.railway.app`) not provisioned; never commit the secret-laden `gbp-qc-agent.zip`.

## VIMI / SIGNAL TRACKER (unchanged)
- **Vimi** = embedded AI assistant (`ppc_chat_widget.html`, backend `/api/ppc-chat` + `/api/ppc-upload`). Do NOT rename Vimi plumbing (`window.ppcOpen`/`ppc-*`/`_build_ppc_context`). Visible label is **GTM**.
- **OpenAI chain:** `OPENAI_INSIGHTS_MODEL` → `gpt-5.4` → `OPENAI_MODEL` → `gpt-4o-mini`.
- **Signal Tracker:** Healthcare (~1,251) + CSG (294); importance `type_weight × severity × recency (+ multi-intent)`; 90-day retention.

---

## ENVIRONMENT VARIABLES

### Railway (runtime — the live web app)
14 service vars observed: `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SMTP_HOST/PASS/PORT/USER` (SMTP unusable on Railway) + 8 Railway-added. Also referenced in code: `OPENAI_INSIGHTS_MODEL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `SLACK_CHANNEL_ID` (default `C0BE016E2E8`), `SLACK_WEBHOOK_URL`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (optional, reverse-IP company on /admin/visitors), `IDENTIFY_TOKEN` (optional, enables /api/identify).
### GitHub Actions secrets (the refresh workflow) — SEPARATE from Railway
`CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`. (See "Signal refresh SETUP" above — these were unset and must be populated.)

---

## HOW TO WORK ON THIS (proven-safe workflow)
1. Clone fresh into the bash sandbox `/tmp` each session (file tools only see `…/outputs`, NOT `/tmp`, so ALL edits to the repo must go through bash/Python string-replace scripts — the Read/Write/Edit tools cannot see `/tmp`). Sandbox network: **git over `github.com` works, but `api.github.com`, GDELT, OpenAI, jsDelivr/CDNs, RSS, Google APIs are BLOCKED** — live data/CDN scripts (incl. particle Three.js) only run in production / the user's browser, and you CANNOT verify visuals or fetch the live Action status from the sandbox.
2. Edit real files via **Python string-replace/slice scripts in bash**. For tricky literals (JS, YAML, big data blocks) use a **single-quoted heredoc** (`<<'PYEOF'` / `<<'HTMLEOF'`) and splice; assert each replacement matched exactly once. Set `git config user.email/name` before committing.
3. **Validate before every push:** `python3 -c "import ast; ast.parse(open('app.py').read())"`; Jinja-parse the whole `agents.html` (`env.parse`) + render each changed `page` variant (ChainableUndefined env + stubbed `url_for`), passing real data (extract the `INDUSTRIES` block by slicing app.py from `def _isvg(` to `INDUSTRIES_BY_SLUG = …` and `exec`-ing it; **remember to pass `agent=…` when rendering `iagent`**); `node --check` external JS + inline `<script>` (skip/neutralise any with Jinja tokens); guard: no `{{`/`{%`/`{#` inside `<style>`/`<script>`; `<script>`/`<style>`/`<main>` tag balance; no duplicate `@app.route`; CSS brace balance for admin.css/gtm.css; for the call-sentiment engine you can run it under a minimal DOM stub in Node to confirm the data engine + all render fns execute.
4. Push to `main` → Railway deploys ~90s. If rejected, `git pull --rebase origin main` then push.
5. Push token = GitHub classic (`repo`+`workflow`) as `https://x-access-token:<TOKEN>@github.com/...`. **Redact tokens in all output (`sed -E 's/ghp_[A-Za-z0-9]+/[REDACTED]/g'`); remind the user to rotate the token after every session.**
6. The sandbox cannot verify visuals or live data — the user must eyeball the live page (and browser favicon caching is aggressive).

### Gotchas
- `<title>` has the same `{% elif page %}` chain — anchor searches on body-specific strings.
- `@media (...){#x{...}}` is a `{#` Jinja-comment trap — keep a space. No `{{`/`{%`/`{#` in `<style>`/`<script>` (CSS `}}` is fine). `admin_usage.html`/`gtm.html`/`call_sentiment.html` intentionally read `{{ user.email }}` — but keep NEW inline scripts Jinja-free (call_sentiment reads email via `document.body.dataset.email`).
- `admin.css` loads **last** and intentionally overrides each admin page's inline CSS. Inline `<style>` in admin pages sits BEFORE the admin.css link, so admin.css wins.
- Three.js/WebGL only where needed, reduced-motion aware, loads from CDN at runtime (prod only).
- CSS gradient-clip-to-text is fragile — prefer solid colours for values that must always render (see the KPI fix).

---

## CURRENT STATE (end of v14) — `main` HEAD ~`e74c417`
- Public site: 19 agents / 8 categories / 26 signal types; **Industries** section (4 industries; healthcare deep with 11 clickable agent detail pages); header nav = Platform·Signals·Agents·Solutions·Industries (Resources removed from header); honest content; em-dash-free industries copy; unified favicon.
- Internal: **Call & Sentiment Intelligence** dashboard live under GTM (`/gtm/call-sentiment`, proxy data). Admin KPI cards fixed + colourised; Visitor Analytics Rage-clicks KPI removed / 4-per-row.
- Pipeline: creative/3D-hiring RSS steps removed from refresh; faster RSS + new GDELT circuit breakers; tolerant config loading.

## OPEN ITEMS / TODO
1. **Signal refresh secrets (highest priority — currently blocking Healthcare refresh):** set the GitHub Actions secrets `CONFIG_YAML` (with the two sheet IDs + tabs above) and `GOOGLE_SERVICE_ACCOUNT_JSON` (= Railway `GOOGLE_SA_JSON`), and share both Google Sheets with the service account `client_email` (Viewer). Optional: add a workflow preflight step that fails with "CONFIG_YAML secret is empty" instead of a Python traceback.
2. **Email (pending):** domain-wide delegation (`gmail.send`) + enable Gmail API + set `GMAIL_SENDER` → `/admin/email-test` = SENT OK. (Fallback: Resend/SendGrid.)
3. **GBP QC Agent embed:** provision `gbp-qc-agent-production.up.railway.app` + `frame-ancestors` + `?pt=`. Don't commit the secret zip.
4. **News freshness:** Google News RSS + GDELT both rate-limit shared CI IPs; for reliable news/hiring signals set `SERPAPI_KEY` (SerpAPI, already supported by `get_news_articles`) rather than relying on the free endpoints from CI.
5. **Reverse-IP company / person-level identity:** set `IPINFO_TOKEN` / `IDENTIFY_TOKEN` to light up those features.
6. **Non-healthcare industries:** tech/financial/professional agents have no detail pages yet (cards non-clickable) — add slugs + detail fields if per-agent pages are wanted there too.
7. **Rotate the GitHub token** shared into chat (standing reminder).

---

## COMPETITOR / SIGNAL RESEARCH + ROADMAP (recorded, not built)
Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala.
- **Gaps:** co-op topic intent (Bombora), review-site intent (G2/Capterra), technographic change, **champion job-change tracking** (UserGems — highest ROI), hiring-surge, earnings/10-K mining, event attendance, layoffs, product-led usage.
- **Buildable now:** Earnings & Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief.
- **Differentiators:** generative-search/AI-answer visibility (early) + execution (the agency runs the plays). First-party web de-anon partly in place (visitor-analytics + reverse-IP + /api/identify).
