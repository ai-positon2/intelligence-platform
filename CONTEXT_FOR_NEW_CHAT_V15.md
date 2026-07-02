# Intelligence by Position² — Full Context (v15 · July 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v15 supersedes all earlier context files (v1–v14)** — those are stale; ignore/delete them.

What v15 adds on top of v14: (1) **Industries split** — the old `/industries/healthcare` B2B page was renamed to **Health Tech** (`/industries/health-tech`) and a brand-new **B2C Healthcare (Patient Growth)** page was built at `/industries/healthcare`, with the 11 healthcare agents divided 5 (B2B) / 6 (B2C), a data-driven hero-chip system, and two distinct slug-gated "signal→action" journey visuals; (2) the **Call & Sentiment Intelligence** dashboard was renamed **Sentiment Pulse** and its route moved to `/gtm/sentiment-pulse` (old URL 301-redirects), its proxy-data engine re-tuned to a healthy picture (positive NPS), its KPI cards redesigned, and a hero-spacing bug fixed; (3) the **brand logo + favicon were replaced with the real "Arena" mark** (green hexagon core + 3 steel-blue + 3 dark-green petals = two merged triangles / 6-point star); the favicon is a white-circle badge, and the on-page logo uses a **theme-aware circular background**; (4) a **light/dark theme toggle** was added to the internal app (user menu), with a full light theme via `[data-theme="light"]` overrides; (5) the **`/admin/visitors` "Recent visitor activity" table** is now unbounded (shows all rows); (6) the **Ad Intelligence** React dashboard was fixed (asset 404). **Latest `main` HEAD at end of this cycle: `b42e3a0`** (plus any follow-ups; always `git pull` to confirm).

---

## WHAT THIS IS

**Intelligence by Position²** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 is a B2B digital-marketing agency: SEO & organic growth, performance/paid media, paid social, content, brand & website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymises website visitors, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), and helps reps act via an embedded AI assistant called **Vimi** (visible label **GTM**).

There are **two surfaces**:
1. **Public / pre-login marketing site** — what prospects see. Sells the product; primary CTA "Request access"; sign-in is secondary; a "Watch the walkthrough" video button is prominent. Always dark-themed. Runs a first-party visitor-analytics tracker for anonymous visitors.
2. **Internal app** (behind Google SSO, `@position2.com` only) — the hub, GTM & SEO (SEO Studio) tool pages, Signal Tracker dashboards, Sentiment Pulse, Ad Intelligence, Vimi, admin (Usage / Visitor Analytics / Access Requests). **Supports a light/dark theme toggle (new in v15).**

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
│                       /favicon.ico|.svg, SEO Studio proxy, GTM sub-routes (/gtm/sentiment-pulse,
│                       /gtm/ad-intelligence, ...)
├── main.py           ← Weekly orchestrator for HEALTHCARE account (Sheets HIGH + News LOW) -> data/tracker.db
├── fetch_csg_*.py    ← CSG fetchers (news/jobs/sheets)
├── fetch_healthcare_jobs.py ← Healthcare creative/3D hiring via Google News RSS (UNHOOKED from refresh workflow)
├── weekly_digest.py  ← Ranks companies; writes reports/opportunities_<acct>.csv
├── tracker/          ← signal pipeline pkg (news_client.py [GDELT/SerpAPI + RSS], news_relevance.py,
│                       signal_score.py, dashboard_builder.py, sheets_client.py, notifier_slack.py, ...)
├── ad_intelligence/  ← ★ built React app (Vite) served directly by Flask (NOT an iframe).
│   ├── index.html    ← shell; asset <script>/<link> MUST point to /gtm/ad-intelligence/assets/… (see fix)
│   ├── assets/index-*.js, index-*.css   ← the built bundle
│   ├── favicon.svg, icons.svg, FLASK_INTEGRATION.py (integration notes)
├── static/
│   ├── css/ds-tokens.css       ← internal design tokens (Space Grotesk; --bg,--s1..3,--b1..3,--tx*,--accent,
│   │                             --logo-bg, etc.) + ★ [data-theme="light"] token flip (v15)
│   ├── css/ds-components.css    ← internal shared components (token-based) + light baseline
│   ├── css/gtm.css             ← GTM hub styles (topbar, user-pill, dash-card, c-* accents) + light block
│   ├── css/hub.css, css/seo.css, css/linkedin.css  ← per-page styles + light blocks (v15)
│   ├── css/admin.css           ← shared admin design system + multi-accent KPI palette + light block (v15)
│   ├── favicon.svg             ← ★ white-circle "Arena" badge (favicon; keep as-is)
│   ├── favicon.png, favicon.ico ← rasterized favicons (from the Arena badge)
│   ├── logo.svg                ← same white-circle badge (raster source)
│   ├── logo.png                ← 512px raster of the badge
│   ├── logo-mark.svg           ← ★ NEW v15: the Arena star ONLY (transparent, padded viewBox); used for the
│   │                             on-page brand logo on a theme-aware circular background
│   ├── js/theme.js             ← ★ NEW v15: light/dark toggle (localStorage 'p2-theme', applied in <head>)
│   ├── js/visitor_track.js, js/pfx_bg.js, js/anonymous_visitors.js, js/linkedin.js
├── templates/
│   ├── agents.html             ← ★ THE SINGLE SHARED MARKETING TEMPLATE. Every public page is a {% if page %} variant.
│   │                             page values: home, agents, agent, platform, signals, solutions,
│   │                             integrations, resources, security, login, privacy, terms,
│   │                             industries, industry, iagent
│   ├── call_sentiment.html     ← ★ "Sentiment Pulse" dashboard (renamed v15; GTM sub-page)
│   ├── admin_usage.html, admin_visitors.html, admin_requests.html
│   ├── hub.html, gtm.html, seo.html, accounts.html, embed.html, 403.html
│   ├── linkedin_scraper.html, anonymous_visitors.html
│   ├── login.html / login_preview.html / login_old_backup.html  ← NOT used by /login (agents.html page=login is)
│   └── ppc_chat_widget.html    ← SHARED Vimi chat widget (internal app)
├── reports/          ← dashboard.html / dashboard_csg.html (Signal Tracker dashboards, generated + Vimi-customised)
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml
```

### Deploy & data model
- **Code/UI** push to `main` → Railway redeploys (~90s). No hot reload.
- **Signal data** refreshed by GitHub Actions (`refresh-dashboards.yml`), which commits updated DBs + dashboards.
- **Signal Tracker dashboards** are GENERATED + Vimi-customised — never hand-edit `reports/dashboard*.html` structure; patch `tracker/dashboard_builder.py` AND splice the single-line `const DATA`.
- **Google Sheets is the data store** for: login log + page views ("Page Views" tab), demo/access requests ("Demo Requests" tab), anonymous visitor analytics ("Visitor Analytics" tab), person-level identities ("Visitor Identities" tab) — all via `GOOGLE_SA_JSON` service account against `LOGIN_LOG_SHEET_ID` / `DEMO_REQUEST_SHEET_ID`. The **Signal Tracker** HIGH signals come from separate Google Sheets configured in `CONFIG_YAML`. **Ad Intelligence** reads its own Google Sheet (`AD_INTEL_SHEET_ID`).

---

## ★ THE PUBLIC MARKETING SITE

### Single-template architecture — read this first
The **entire public site is rendered from ONE Jinja template, `templates/agents.html`**, via a `{% if page == '...' %}` chain in `<main>`. Routes in `app.py` render this template with a `page` variable. The nav, footer, "Request access" modal (`#nvfov`), video modal (`#vmodal`), animated background, custom cursor, page loader, and big inline `<script>` IIFE are all **shared across every public page**. There is a matching `{% elif page %}` chain in `<title>` and a per-page accent dict on `<body data-page style="--page:…;--page2:…">`.

### Public routes (all in app.py)
| Route | page= | Purpose |
|-------|-------|---------|
| `GET /` | `home` | Public homepage. Redirects to `/hub` if logged in. |
| `GET /login` | `login` | Centered Google sign-in card. Renders agents.html (page=login); header nav hidden. |
| `GET /agents` | `agents` | Agent directory (19 agents). |
| `GET /agents/<slug>` | `agent` | Per-agent detail. Unknown slug → 302 `/agents`. |
| `GET /platform`,`/signals`,`/solutions`,`/integrations`,`/resources`,`/security`,`/privacy`,`/terms` | resp. | Marketing pages. |
| `GET /industries` | `industries` | Industry list (clickable cards). |
| `GET /industries/<slug>` | `industry` | Per-industry page (unknown → 302 `/industries`). |
| `GET /industries/<islug>/agents/<aslug>` | `iagent` | Per-industry agent detail (unknown → 302 to industry). |
| `POST /api/demo-request` | — | "Request access" intake → Sheet + Slack + email; records `p2_vid`. Public. |
| `POST /api/atrack` | — | anonymous visitor-analytics ingest → "Visitor Analytics" tab. Public. |
| `POST /api/identify` | — | person-level identity ingest. Gated by `IDENTIFY_TOKEN`. |
| `GET /favicon.ico` | — | serves `static/favicon.ico` (image/x-icon). ★ split from .svg in v15. |
| `GET /favicon.svg` | — | serves `static/favicon.svg` (image/svg+xml). |
| `GET /admin/usage|visitors|requests` (+`/data`) | — | Admin dashboards. `@admin_required`. |
| `GET /admin/email-test` | — | Email diagnostic (JSON). `@admin_required`. |

NOTE: `/customers` 404s; `/context-graph` 404s (removed; do NOT reintroduce).

### Visual / design system (public site)
Fonts: Bricolage Grotesque (display), Instrument Serif italic (accents), Inter (body), JetBrains Mono (labels/`--mono`). Tokens in `:root` (`--bg:#040510`; cyan `#22d3ee`, violet `#8b5cf6`, indigo `#6366f1`, pink `#e879f9`, lime `#a3e635`; `--dim/--dim2/--dim3`, `--line/--line2`). Per-page `<body data-page>` accent + ambient Three.js nebula (+2D fallback), custom cursor, 2-col heroes, colored tiles. Card mouse-spotlight pattern via `--mx/--my`.

### Nav (header)
Header links (hidden on login): **Platform · Signals · Agents · Solutions · Industries**. Resources was removed from the header nav (route + footer column still exist).

### Agents — `AGENTS` in app.py (19)
signal-tracker, generative-search-visibility (FLAGSHIP), anonymous-visitors, technical-seo-geo-auditor, ai-readiness-auditor, keyword-opportunity-engine, content-brief-architect, content-authority-optimizer, competitor-seo-intelligence, local-visibility-builder, search-term-intelligence, linkedin-intelligence, ad-intelligence, pipeline-command-center, gbp-qc-agent, on-page-auditor, hub-spoke-architect, robots-monitor, article-enhancer. Categories: Signals, GEO, Web, SEO, Content, Paid, Social, Analytics. `AGENTS_BY_SLUG` maps slug→dict.

### Signals — 26 signal _types_ (catalog). `/signals` is a numbered editorial timeline.

### Honest-content principle (enforced)
No fabricated logos/quotes/metrics; previews labeled "representative." The one hard stat kept is "Recovers 95%+ of lost visitors" (industry figure).

### "Request access" lead form + notifications
Shared modal `#nvfov` → `POST /api/demo-request`. Notifiers: sheet, Slack (channel `C0BE016E2E8` = #intelligence-platform-request-access), email. Recipients via `DEMO_NOTIFY_EMAIL`.

### Email transport (Gmail API over HTTPS) — STILL PENDING two admin steps
Railway blocks SMTP. Code sends via Gmail API (HTTPS) when `GMAIL_SENDER` is set, reusing `GOOGLE_SA_JSON`. PENDING: (1) domain-wide delegation for the SA Client ID, scope `gmail.send`, enable Gmail API; (2) set `GMAIL_SENDER`. Then `/admin/email-test` → `method:"gmail_api"`, `SENT OK`.

---

## ★ INDUSTRIES SECTION (public site, in agents.html) — REWORKED in v15

### Data — `INDUSTRIES` list in app.py (right after `AGENTS_BY_SLUG`)
Helper `_isvg(inner)`. `INDUSTRIES_BY_SLUG` maps slug→dict. **Five industries, in display order:**
1. **health-tech** — `featured: True`, accent `#22d3ee`/`#34d399`. B2B "sell INTO healthcare." (renamed from the old `healthcare` page)
2. **healthcare** — `featured: False`, accent `#fb7185`/`#c084fc` (rose/violet). ★ NEW v15: B2C patient growth.
3. **technology-saas** — accent `#818cf8`/`#22d3ee`. (unchanged; ~5 non-clickable agents)
4. **financial-services** — accent `#34d399`/`#0ea5e9`. (unchanged)
5. **professional-services** — accent `#fbbf24`/`#e879f9`. (unchanged)

Each industry dict: `slug, name, short, featured, accent, accent2, icon, eyebrow, headline, headline_ital, lead, stats[{v,l}], segments[], chips[3], pains[{t,d}], signals[], agents[…], plays[{t,d}]`. ★ `chips` is NEW in v15 (3 strings) — drives the animated hero-art float chips (was hardcoded before).

**Health Tech (health-tech) specifics:** name **"Health Tech"** (short "Health Tech" — the "& Life Sciences" suffix was dropped); hero headline **"Win the health system" / "before the market moves."** (italic gradient); eyebrow "Industry · Health Tech"; chips `["Signal fired","Intent score 9.4","Committee mapped"]`; segments are vendor-side (digital health, medtech, health IT, payers, pharma, RCM). **5 B2B agents:**
1. `health-tech-account-tracker` (Health-Tech Account Tracker, LIVE)
2. `provider-payer-signal-tracker` (CORE)
3. `buying-committee-tracker`
4. `buyer-referrer-de-anonymization` (NEW)
5. `pipeline-command-center`

**Healthcare / Patient Growth (healthcare) specifics:** name "Healthcare & Patient Growth" (short "Healthcare"); hero **"You take care of your patients." / "We take care of finding them."**; chips `["Patient searching","You're the answer","Visit booked"]`; segments are patient-facing (multi-location clinics, hospitals, dental/DSO, dermatology/med-spa, behavioral, urgent/primary care); signals are patient-demand (near-me queries, AI citations, reviews, call topics, rating drops, NPS). **6 B2C agents:**
1. `patient-answer-visibility` (FLAGSHIP)
2. `call-sentiment-intelligence` (NEW)
3. `location-facility-visibility`
4. `clinical-authority-optimizer`
5. `condition-treatment-brief-architect`
6. `hipaa-aware-site-geo-auditor` (NEW)

Each agent is a full detail-page object: `slug, name, base, badge, accent, accent2, role, metric, icon, use (card blurb), summary, benefit, how, who, connects[], out[{t,s,w}]`.

### Template blocks in agents.html (industry family)
- `industries` — hero + `.ind-grid`; the **featured** card is the larger 2-col one; its "Explore {{ ind.short|lower }}" label is now **dynamic** (was hardcoded "healthcare"). List intro says "…with health tech and healthcare as our deepest builds."
- `industry` — hero2 with animated orb; the hero-art float chips loop over `industry.chips` (`{% set _chips = industry.chips or [...] %}`). Then stats, segment chips, challenges, a **slug-gated "signal→action" journey** (see below), reframed-agent grid (`.ind-agents`/`.ind-agent`, cursor spotlight, per-card accent, clickable → `/industries/{slug}/agents/{aslug}`), signal chips, plays, "more industries", CTA band.
- **Journey visuals are slug-gated (v15):**
  - `{% if industry.slug == 'health-tech' %}` → the B2B "Orbit Health" journey (funding + service-line → intent score 9.4 → committee mapped → Vimi drafts outreach); gradient id `ijg` (cyan/emerald).
  - `{% elif industry.slug == 'healthcare' %}` → the B2C "patient search → booked visit" journey (search → you're the answer, 4.9★/#1 map pack/AI-cited → they choose you → sentiment loop); gradient id `ijg2` (rose/violet). Reuses `.ij-*` classes.
- `iagent` — polished agent-detail layout (`.crumb`, `.detail-head/.halo`, `.panel/.trips`, `.side/.card/.cta-card`, `.aout`, `.related`).

### NO EM-DASHES rule (still enforced)
**No em-dashes ("—" / `&mdash;`) anywhere in the Industries data or the industries/industry/iagent template blocks** (and the shared chrome that renders on those pages). Keep future healthcare/industry copy em-dash-free. (This rule is for site copy; documentation like this file can use them.)

---

## ★ SENTIMENT PULSE DASHBOARD (internal, GTM) — RENAMED + RE-TUNED in v15

- **Was:** "Call & Sentiment Intelligence" at `/gtm/call-sentiment`. **Now:** **"Sentiment Pulse"** at **`/gtm/sentiment-pulse`** (+ trailing slash), `@login_required`, renders `templates/call_sentiment.html` (filename unchanged). The old `/gtm/call-sentiment` route now **301-redirects** to the new URL (function `call_sentiment_legacy`). Renamed in: `<title>`, breadcrumb, hero H1, the GTM hub card (`gtm.html`, `c-sentiment` accent), and the `/api/track` analytics label. Subheader: "A live read on how patients feel, by tracking every call, review and survey, and scoring sentiment for each location."
- **Purpose (display-only, PROXY DATA):** voice-of-patient sentiment unified across calls, Google reviews & surveys, scored per location. No real agent/pipeline behind it — a beautiful, fully interactive UI mockup. Network label "Cedar Valley Health"; 10 NC/SC/VA locations.
- **Engine (vanilla JS, seeded PRNG `mulberry32(20260701)`):** generates ~1,000 proxy interactions across 10 locations × 3 sources (call/review/survey) × 10 topics. Filters recompute everything (period/region/location/source/sentiment/topic/search/reset). KPIs, hand-built SVG charts, sortable per-location table with drawer, theme drivers, call-reasons, interactions feed.
- **v15 data tuning (fixes negative NPS / poor scores):** the label/score generator was recalibrated — `pPos = clamp((loc.base/100)*0.9 + (t.bias-0.5)*0.5 + (rnd()-0.5)*0.34, 0.28, 0.93)`; bands `pos=ri(76,98)`, `neu=ri(54,72)`, `neg=ri(14,52)`; review ratings `pos pick([5,5,5,4])`, `neu pick([4,4,3])`, `neg pick([2,3,1])`; severe-flag `neg && score<=26`. Result on the default 30-day view: ~72% pos / 16% neu / 12% neg, overall ~76/100, avg call ~73, GBP rating ~4.42, **NPS +54**, ~17 negative alerts. Interactions still = calls + reviews + surveys (source split 55/25/20 unchanged, so counts match the old totals).
- **v15 KPI card redesign (`.cs-kpi`):** per-accent tinted background + corner glow, icon chip (`.ki`), gradient top bar, pill-style delta (`.kd`), accent-colored value numbers (light in dark theme, dark-mixed in light theme). NPS card shows `+NN`.
- **v15 layout fix:** the hero had a huge gap because it inherited the shared `.page-header{min-height:calc(60vh-62px); justify-content:center}` from `gtm.css`; overridden locally in the page's inline `<style>` (`.page-header{min-height:0;display:block;margin:2px 0 22px}`).
- Design: ds-tokens + ds-components + gtm.css chrome, a big inline `<style>` (prefix `cs-`), self-initializing engine `<script>`, shared `pfx_bg.js` particle bg. `--pos:#2dd4aa; --neu:#f5a623; --neg:#fb7185`.

---

## ★ BRANDING — LOGO + FAVICON + THEME-AWARE LOGO (rebuilt in v15)

The brand mark is the **"Arena" logo**: a central bright-green hexagon (`#55be8c`) with **three steel-blue petals (`#4a6a7c`)** and **three dark-green petals (`#53795b`)** = two merged triangles / a 6-point star. Source of truth was the user-supplied `Arena Logo.svg` (which also contains a white "arena" wordmark that is NOT used on the site — only the icon was extracted).

Assets in `static/`:
- **`favicon.svg`** — the star centered on a **white circle** (badge). This is THE favicon; **keep as-is** (do not make it theme-aware). Also mirrored as `logo.svg`.
- **`favicon.png`, `favicon.ico`** — rasterized (via cairosvg) from the badge. `logo.png` = 512px raster.
- **`logo-mark.svg`** — ★ the **star ONLY**, transparent background, with the star centered at ~76% inside a `0 0 100 100` viewBox so it never touches the edges (this padding was required — a tight viewBox got clipped by the circular container).

On-page brand logo (all brand spots: public `nv-orb` ×3, internal `.brand-icon`, linkedin `.tb-brand-icon`, admin_requests `.orb`, login `.brand-mark`/`.card-mark`): an `<img src="/static/logo-mark.svg?v=3">` on a **theme-aware circular container**:
`background: var(--logo-bg, #151b2e); border-radius:50%; overflow:hidden; display:flex; align-items:center; justify-content:center; padding:0`.
`--logo-bg` = **`#ffffff` in light theme, dark navy `#151b2e` in dark theme.** Public site is always dark → dark circle.

Two bugs fixed while doing this (learn from them):
- **Clipping:** the star's points reached the viewBox edge and the circular `overflow:hidden` container cut them off → fixed by padding the star inside `logo-mark.svg`.
- **404:** the mark was first referenced at `/logo-mark.svg`, but static files are served under **`/static/`** (there is no root route like `/favicon.svg` has). Correct path: **`/static/logo-mark.svg`**. The broken-image alt text ("Inte…") in a circle is the tell-tale sign of this 404.

Favicon routes (app.py): `/favicon.ico` → `favicon_ico()` (image/x-icon), `/favicon.svg` → `favicon()` (image/svg+xml). Ad Intelligence has its own `favicon.svg`/`icons.svg` routes under `/gtm/ad-intelligence/` and `/ppc/ad-intelligence/`.

---

## ★ LIGHT / DARK THEME TOGGLE (internal app) — NEW in v15

- **Mechanism:** `static/js/theme.js` reads `localStorage['p2-theme']` (default `'dark'`), sets `document.documentElement.setAttribute('data-theme', theme)`, and exposes `window.P2toggleTheme(event)`. It is loaded in the `<head>` of every internal page so the theme is applied before paint (no flash). Dark is the default look (no attribute needed); light is `[data-theme="light"]`.
- **Toggle UI:** a "Light mode ☀ / Dark mode ☾" item (`#p2ThemeLabel`, `#p2ThemeIcon`) is injected at the top of the user-menu dropdown on every internal page (standard `.dd-items` pattern; linkedin uses `.tb-dd-items`). The public marketing site has no user menu, so the toggle lives only in the internal app; the public site stays dark.
- **Light theme implementation:** `[data-theme="light"]` overrides —
  - `ds-tokens.css`: a `:root[data-theme="light"]{…}` block flips `--bg/--bg2` (light), surfaces `--s1..3` (translucent black), borders `--b1..3`, text `--tx/--tx2/--tx3` (dark), shadows, and `--logo-bg:#fff`. Accent hues unchanged. Also added `--logo-bg:#151b2e` to the dark `:root`.
  - `ds-components.css`: light baseline for token-based components.
  - `gtm.css`, `hub.css`, `seo.css`, `linkedin.css`, `admin.css`: appended `[data-theme="light"]` blocks (with `!important`) for the hardcoded-dark chrome — body background, topbar, brand text, breadcrumb, user-pill/dropdown/dd-item, cards (`.dash-card/.card/.glass/.panel`), `.page-title` (dark gradient), tables, tags. admin.css also defines `--logo-bg`.
  - Inline blocks in `call_sentiment.html` (cs-* surfaces + KPI value color), `accounts.html`, `embed.html` (which have no shared CSS).
- **Known follow-up:** pages with deep custom inline widgets may have a few spots that still read dark in light mode and need polish. Hub/GTM/Sentiment Pulse/admin are the well-covered primary surfaces.

---

## ★ ADMIN DASHBOARDS (Usage / Visitor Analytics / Access Requests)

Shared `static/css/admin.css` (linked last, overrides inline). Breadcrumb `Hub › Admin › {Page}`, 3-dashboard user-pill menu, shared `pfx_bg.js`. `.kpis .kpi` / `.stats .stat` use a 6-colour nth-child palette with coloured top bar, per-accent tinted card wash + border, corner glow. KPI values are solid per-accent coloured numbers (a prior gradient-clip bug was fixed). Visitor Analytics KPIs laid out 4-per-row (Rage-clicks headline KPI removed; lower "Rage-click pages" section still exists).

**v15 fix — `/admin/visitors` "Recent visitor activity":** `_fetch_visitor_analytics()` in app.py previously capped the recent table two ways (`data[-200:]` window + `if len(recent) >= 60: break`) and read the sheet with a bounded range (`A1:AM8000`). Both caps were removed (iterates all rows, no break) and the read is now unbounded (`Visitor Analytics!A:AM`), so the table shows **all** visitor-activity rows to date.

---

## ★ AD INTELLIGENCE DASHBOARD (internal, GTM) — served as a built React app

- **Route:** `GET /gtm/ad-intelligence` (+ `/ppc/…` 301→gtm), `@login_required`, `send_from_directory("ad_intelligence","index.html")`. Assets at `/gtm/ad-intelligence/assets/<file>` (and `/ppc/…`), plus `favicon.svg`/`icons.svg` routes. It is a Vite React build served directly (NOT an iframe). Chatbot helper `get_ad_intelligence_data()` reads `AD_INTEL_SHEET_ID = 16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s`.
- **v15 fix — dashboard not opening:** `ad_intelligence/index.html` referenced its JS/CSS at absolute root `/assets/index-*.js|css`, which 404 because the Flask route only serves them under `/gtm/ad-intelligence/assets/`. Fixed the two refs to `/gtm/ad-intelligence/assets/…`. The JS bundle itself has no other absolute `/assets/` refs (it only calls `/api/whoami` and the Google Sheet). If it loads but shows no ads, the Ad Intel Google Sheet likely isn't shared with the service account.

---

## ★ SIGNAL PIPELINE + REFRESH (recent trouble area)

### How the "Refresh signals" button works
The app's Refresh modal triggers GitHub Actions workflow **`refresh-dashboards.yml`** (via `workflow_dispatch`, using Railway var `GH_DISPATCH_TOKEN`). On a GitHub runner it: (1) writes `config.yaml` from secret **`CONFIG_YAML`** and `service_account.json` from **`GOOGLE_SERVICE_ACCOUNT_JSON`**; (2) fetch steps (`continue-on-error`): Healthcare (`main.py`: Sheets HIGH + GDELT news LOW), CSG news (`fetch_csg_news.py`), CSG HIGH (`fetch_csg_sheets.py`); (3) rebuild dashboards (`scripts/refresh-dashboards.py`), weekly brief (`weekly_digest.py`), commit & push.

### Resilience (already in repo)
Removed the two Google-News-RSS creative/3D hiring fetch steps (503-block CI). RSS circuit breaker trips faster (`_CIRCUIT_THRESHOLD 12`, `_FEED_RETRIES 1`, `_FEED_BACKOFF 0.8`). GDELT circuit breaker added (`_GDELT_THRESHOLD=15`, harder backoff on 429). `main.py._load_config` returns `{}` (with warning) instead of `None` on empty config.

### ★ Signal refresh SETUP (GitHub Actions secrets — still the live blocker)
The refresh does nothing until the repo has these **GitHub Actions repository secrets** (SEPARATE from Railway variables):
- **`GOOGLE_SERVICE_ACCOUNT_JSON`** = the full SA JSON (same value as Railway `GOOGLE_SA_JSON`).
- **`CONFIG_YAML`** = the tracker config (`credentials.google_service_account_json: "service_account.json"` + `google_sheets` block). The two live Healthcare sheets:
  - C-suite: id `16M_DLwIhbKuQAv_Cafxl8krrNWO5iaTrBJdQaN9EI6g`, tab **"C-suite signals"** (Company Name | Domain | Person Name | Title | Action | Start Date | LinkedIn URL | Notes).
  - Funding: id `1nhu07HCyctjs5gfGdchu_n7Rxnpip0xVMwB9nIF_wa0`, tab **"Funding signals"** (Scan Date | Company Name | Signal Type | Funding Stage | Amount | … | Confidence | Summary).
- **Also share both Sheets with the service account `client_email` (Viewer)** — otherwise config loads but zero rows (most common miss).
Signal Type routing + column readers live in `main.py` (~lines 134–260). `tracker/sheets_client.py` reads via `values().get(range=tab_name)`. Working `CONFIG_YAML` value:
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
`GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SMTP_HOST/PASS/PORT/USER` (SMTP unusable on Railway). Also referenced: `OPENAI_INSIGHTS_MODEL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `SLACK_CHANNEL_ID` (default `C0BE016E2E8`), `SLACK_WEBHOOK_URL`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (optional), `IDENTIFY_TOKEN` (optional).
### GitHub Actions secrets (refresh workflow) — SEPARATE from Railway
`CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`. (See "Signal refresh SETUP" — populate these + share the Sheets.)

---

## HOW TO WORK ON THIS (proven-safe workflow)
1. Clone fresh into the bash sandbox `/tmp` each session (file tools only see `…/outputs`, NOT `/tmp`, so ALL edits to the repo go through bash/Python string-replace scripts). Sandbox network: **git over `github.com` works, but `api.github.com`, GDELT, OpenAI, RSS, Google APIs are BLOCKED** — you CANNOT verify visuals or fetch live Action status from the sandbox. **The sandbox may reset mid-session** (losing `/tmp` clone + git identity) — just re-clone and re-run `git config user.email/name`.
2. Edit real files via **Python string-replace/slice scripts in bash**. For tricky literals use single-quoted heredocs (`<<'PYEOF'`) and assert each replacement matched exactly once.
3. **Validate before every push:** `python3 -c "import ast; ast.parse(open('app.py').read())"`; Jinja-parse `agents.html` (`env.parse`) + render each changed `page` variant (ChainableUndefined env + stubbed `url_for`; extract the `INDUSTRIES` block by slicing `def _isvg(` → `INDUSTRIES_BY_SLUG =` and `exec`-ing it; pass `agent=…` for `iagent`); `node --check` external JS + inline `<script>`; guard no `{{`/`{%`/`{#` inside `<style>`/`<script>` (note: `gtm.html`/`admin_usage.html`/`call_sentiment.html` intentionally read `{{ user.* }}` in HTML — scope this guard to `call_sentiment` / new inline scripts); CSS brace balance; `<script>`/`<style>`/`<main>` tag balance; no duplicate `@app.route`.
4. **Rasterizing/verifying SVG:** `cairosvg` and `svgelements` are pip-installable in the sandbox (`pip install … --break-system-packages`); use `svgelements` for exact bbox and `cairosvg` to render a PNG you can view with the Read tool (great for logo work). No system SVG rasterizer (rsvg/inkscape) is present.
5. Push to `main` → Railway deploys ~90s. If rejected, `git pull --rebase origin main` then push. Push token = GitHub classic (`repo`+`workflow`) as `https://x-access-token:<TOKEN>@github.com/...`. **Redact tokens in all output (`sed -E 's/ghp_[A-Za-z0-9]+/[REDACTED]/g'`); the user should rotate the token after every session.**
6. The sandbox cannot verify visuals or live data — the user must eyeball the live page. **Browser favicon + CSS/JS caching is aggressive** — always hard-refresh; bump `?v=N` when replacing a cached asset in place.

### Gotchas
- `<title>` has the same `{% elif page %}` chain — anchor searches on body-specific strings.
- `@media (...){#x{...}}` is a `{#` Jinja-comment trap — keep a space. No `{{`/`{%`/`{#` in `<style>`/`<script>` (CSS `}}` is fine).
- `admin.css` loads **last** and intentionally overrides each admin page's inline CSS.
- **Static files are served at `/static/…`.** Only a few paths have dedicated root routes (`/favicon.svg`, `/favicon.ico`). Referencing a new static asset at the root (e.g. `/logo-mark.svg`) → 404; use `/static/…` (that's how `theme.js` and the CSS are referenced).
- Regex/`data:image/svg+xml,%3Csvg…%3C/svg%3E` replacements are dangerous — several templates use inline data-SVGs for **decorative** backgrounds (grain/noise) and `<select>` chevrons, not just favicons. Scope any favicon/logo replace so you don't clobber those (learned the hard way).
- Theme: default = dark (no attribute). Light = `document.documentElement[data-theme=light]`; overrides use `[data-theme="light"] …` (and `:root[data-theme="light"]` for tokens).
- CSS gradient-clip-to-text is fragile — prefer solid colours for values that must always render.

---

## CURRENT STATE (end of v15) — `main` HEAD ~`b42e3a0`
- Public site: 19 agents / 8 categories / 26 signal types; **Industries** = 5 (health-tech featured + healthcare B2C, both with clickable agent detail pages; tech/financial/professional lighter, non-clickable); honest content; em-dash-free industries copy.
- Branding: real **Arena** mark everywhere; white-circle favicon; on-page logo on a **theme-aware circular background** (`--logo-bg`), served from `/static/logo-mark.svg`.
- Internal: **light/dark toggle** in the user menu (persisted), full light theme via `[data-theme=light]`; **Sentiment Pulse** (`/gtm/sentiment-pulse`) with healthy tuned proxy data + redesigned KPI cards; **Ad Intelligence** React app opens correctly; `/admin/visitors` recent table unbounded.
- Pipeline: creative/3D-hiring RSS steps removed; faster RSS + GDELT circuit breakers; tolerant config loading.

## OPEN ITEMS / TODO
1. **Signal refresh secrets (highest priority — blocking Healthcare refresh):** set GitHub Actions secrets `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`, and share both Healthcare Sheets with the SA `client_email` (Viewer). Optional: workflow preflight that fails clearly on empty `CONFIG_YAML`.
2. **Light-theme polish:** audit the tool/dashboard pages with heavy custom inline styling (SEO Studio, LinkedIn, Anonymous Visitors, some Sentiment Pulse widgets) in light mode and fix any remaining dark-on-light / low-contrast spots.
3. **Email (pending):** Gmail domain-wide delegation (`gmail.send`) + enable Gmail API + set `GMAIL_SENDER` → `/admin/email-test` = SENT OK. (Fallback: Resend/SendGrid.)
4. **Ad Intelligence data:** if the dashboard opens but shows no ads, share `AD_INTEL_SHEET_ID` with the service account.
5. **GBP QC Agent embed:** provision `gbp-qc-agent-production.up.railway.app` + `frame-ancestors` + `?pt=`. Don't commit the secret zip.
6. **News freshness:** Google News RSS + GDELT rate-limit CI IPs; set `SERPAPI_KEY` for reliable news/hiring signals.
7. **Reverse-IP / identity:** set `IPINFO_TOKEN` / `IDENTIFY_TOKEN` to light those up.
8. **Non-healthcare industries:** tech/financial/professional agents have no detail pages yet (cards non-clickable) — add slugs + detail fields if wanted.
9. **Optional:** a theme toggle on the public marketing site (currently internal-only), if desired.
10. **Rotate the GitHub token** shared into chat (standing reminder).

---

## COMPETITOR / SIGNAL RESEARCH + ROADMAP (recorded, not built)
Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala.
- **Gaps:** co-op topic intent (Bombora), review-site intent (G2/Capterra), technographic change, **champion job-change tracking** (UserGems — highest ROI), hiring-surge, earnings/10-K mining, event attendance, layoffs, product-led usage.
- **Buildable now:** Earnings & Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief.
- **Differentiators:** generative-search/AI-answer visibility (early) + execution (the agency runs the plays). First-party web de-anon partly in place.
