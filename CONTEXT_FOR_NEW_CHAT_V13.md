# Intelligence by Position² — Full Context (v13 · June 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v13 supersedes all earlier context files (v1–v12)** — those are stale; ignore/delete them.

What v13 adds on top of v12: (1) **removed the demo/fake layer** — the Context Graph page, the Anonymous-Visitors "proxy people," and the HubSpot "Vimi Engage" CRM-match mock (on both Anonymous Visitors and LinkedIn Intelligence); (2) built a real **anonymous (pre-login) visitor analytics** system — a first-party tracker on the public site, an `/api/atrack` ingest endpoint, a "Visitor Analytics" Google-Sheet tab, and an admin-only **`/admin/visitors`** dashboard; (3) **visitor identity** — stitch anonymous sessions to people on access-form submit, optional reverse-IP company enrichment, and an `/api/identify` ingest scaffold for person-level providers; (4) unified the **three admin dashboards** (Usage / Visitor Analytics / Access Requests) into one design system via shared `static/css/admin.css` + shared WebGL particle background `static/js/pfx_bg.js`, a `Hub › Admin › {Page}` breadcrumb topbar, the 3-dashboard user-pill menu everywhere, and a multi-accent card palette. **Latest `main` HEAD: `60b6700`.**

---

## WHAT THIS IS

**Intelligence by Position²** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 is a B2B digital-marketing agency: SEO & organic growth, performance/paid media, paid social, content, brand & website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymises website visitors, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), and helps reps act via an embedded AI assistant called **Vimi**.

There are **two surfaces**:
1. **Public / pre-login marketing site** — what prospects see. Sells the product; primary CTA "Request access"; sign-in is secondary; a "Watch the walkthrough" video button is prominent. **As of v13 it also runs a first-party visitor-analytics tracker for anonymous visitors.**
2. **Internal app** (behind Google SSO, `@position2.com` only) — the hub, GTM & SEO (SEO Studio) tool pages, Signal Tracker dashboards, Vimi, admin (Usage / Visitor Analytics / Access Requests).

- **Live URL:** `https://intelligence.position2.com`
- **GitHub:** `https://github.com/ai-positon2/intelligence-platform` (main app)
- **Hosting:** Railway — auto-deploys on every push to `main` (~90s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; signal data refreshes via GitHub Actions.
- **Auth:** Google SSO, `@position2.com` only. `@login_required` on protected routes; `@admin_required` for `/admin/*`.
- **Admins:** `krishna.ladha@position2.com`, `sudheer.d@position2.com` (set in `ADMIN_EMAILS`).
- **Latest `main` HEAD (end of this cycle): `60b6700`.**

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py            ← Flask server (~3,400+ lines): routes, API, OpenAI (Vimi), insights, auth,
│                       AGENTS + SIGNALS data, marketing routes, /api/demo-request (sheet+Slack+email),
│                       /api/track (logged-in page views), /api/atrack (anon visitor analytics),
│                       /api/identify (person-level identity ingest), /admin/usage, /admin/visitors,
│                       /admin/requests, /admin/email-test, SEO Studio proxy (_seo_tools, /seo/<slug>)
├── main.py           ← Weekly orchestrator for HEALTHCARE account (Sheets HIGH + News LOW) -> data/tracker.db
├── fetch_csg_*.py    ← CSG fetchers (news/jobs/sheets)
├── weekly_digest.py  ← Ranks companies; writes reports/opportunities_<acct>.csv
├── tracker/          ← signal pipeline pkg (news_client.py [GDELT/SerpAPI], news_relevance.py,
│                       signal_score.py, dashboard_builder.py, notifier_slack.py, ...)
├── static/
│   ├── css/admin.css           ← ★ NEW v13: shared admin design system (Usage bg + cards, VA fonts, breadcrumb)
│   ├── js/visitor_track.js     ← ★ NEW v13: first-party anonymous visitor tracker (public pages)
│   ├── js/pfx_bg.js            ← ★ NEW v13: shared Three.js WebGL particle background (admin dashboards)
│   ├── js/anonymous_visitors.js← Anon Visitors page logic (HubSpot/Vimi-Engage mock REMOVED in v13)
│   └── js/linkedin.js          ← LinkedIn Intelligence logic (Hot-Leads/Vimi-Engage mock REMOVED in v13)
├── templates/
│   ├── agents.html             ← ★ THE SINGLE SHARED MARKETING TEMPLATE. Every public page is a {% if page %} variant.
│   │                             (v13: includes visitor_track.js for anonymous visitors only)
│   ├── admin_usage.html        ← Usage dashboard (the design "source of truth"; links admin.css; inline pfx_bg particles)
│   ├── admin_visitors.html     ← ★ NEW v13: Anonymous Visitor Analytics dashboard
│   ├── admin_requests.html     ← Access-Requests admin table (now on shared admin.css shell)
│   ├── hub.html, gtm.html, seo.html, accounts.html, embed.html, 403.html
│   ├── linkedin_scraper.html, anonymous_visitors.html
│   └── ppc_chat_widget.html    ← SHARED Vimi chat widget (internal app)
│   (context_graph.html was DELETED in v13)
├── reports/          ← dashboard.html / dashboard_csg.html (Signal Tracker dashboards, generated + Vimi-customised)
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml
```

### Deploy & data model
- **Code/UI** push to `main` → Railway redeploys (~90s). No hot reload.
- **Signal data** refreshed by GitHub Actions (news/jobs/sheets), which commit updated DBs + dashboards.
- **Signal Tracker dashboards** are GENERATED + Vimi-customised — never hand-edit `reports/dashboard*.html` structure; patch `tracker/dashboard_builder.py` AND splice the single-line `const DATA`.
- **Google Sheets is the data store** for: login log + page views ("Page Views" tab), demo/access requests ("Demo Requests" tab), anonymous visitor analytics ("Visitor Analytics" tab), and person-level identities ("Visitor Identities" tab) — all via the `GOOGLE_SA_JSON` service account against `LOGIN_LOG_SHEET_ID` / `DEMO_REQUEST_SHEET_ID`.

---

## ★ THE PUBLIC MARKETING SITE

### Single-template architecture — read this first
The **entire public site is rendered from ONE Jinja template, `templates/agents.html`**, via a `{% if page == '...' %}` chain in `<main>`. Routes in `app.py` render this template with a `page` variable. The nav, footer, "Request access" modal, video modal, animated background, custom cursor, page loader, and big inline `<script>` IIFE are all **shared across every public page**.

`page` values: `home`, `agents`, `agent`, `platform`, `signals`, `solutions`, `integrations`, `resources`, `security`, `login`, `privacy`, `terms`.

### Public routes (all in app.py)
| Route | page= | Purpose |
|-------|-------|---------|
| `GET /` | `home` | Public homepage. Redirects to `/hub` if logged in. |
| `GET /login` | `login` | Centered Google sign-in card. |
| `GET /agents` | `agents` | Agent directory (19 agents). |
| `GET /agents/<slug>` | `agent` | Per-agent detail. Unknown slug → 302 `/agents`. |
| `GET /platform`,`/signals`,`/solutions`,`/integrations`,`/resources`,`/security`,`/privacy`,`/terms` | resp. | Marketing pages. |
| `POST /api/demo-request` | — | "Request access" intake → Sheet + Slack + email. **v13: also records the visitor's `p2_vid` cookie ("Visitor ID" column) for identity stitching.** Public. |
| `POST /api/atrack` | — | ★ NEW v13: anonymous visitor-analytics ingest (sendBeacon/JSON). Public. One row per page view → "Visitor Analytics" tab. |
| `POST /api/identify` | — | ★ NEW v13: person-level identity ingest. **Disabled unless `IDENTIFY_TOKEN` set**; caller must present it (`X-Identify-Token`/`?token=`). → "Visitor Identities" tab. |
| `GET /admin/usage` (+`/admin/usage/data`) | — | Usage dashboard (logged-in page views/logins). `@admin_required`. |
| `GET /admin/visitors` (+`/admin/visitors/data`) | — | ★ NEW v13: Anonymous Visitor Analytics dashboard. `@admin_required`. |
| `GET /admin/requests` | — | Access-request submissions. `@admin_required`. |
| `GET /admin/email-test` | — | Email diagnostic (JSON). `@admin_required`. |

NOTE: `/customers` 404s; **`/context-graph` 404s as of v13 (removed)**.

### Visual / design system (public site)
Fonts: Bricolage Grotesque (display), Instrument Serif italic (accents), Inter (body), JetBrains Mono (labels). Tokens `:root` (`--bg:#040510`; cyan/violet/indigo/pink/lime accents). Per-page `<body data-page>` accent + ambient Three.js nebula (+2D fallback), custom cursor, 2-col heroes, colored tiles. No tilted cards.

### "Watch the walkthrough" video
YouTube `https://youtu.be/WqLzs2cFrhg`; opens in shared `#vmodal`.

### Agents — `AGENTS` in app.py (19)
signal-tracker, generative-search-visibility (FLAGSHIP), anonymous-visitors, technical-seo-geo-auditor, ai-readiness-auditor, keyword-opportunity-engine, content-brief-architect, content-authority-optimizer, competitor-seo-intelligence, local-visibility-builder, search-term-intelligence, linkedin-intelligence, ad-intelligence, pipeline-command-center, gbp-qc-agent, on-page-auditor, hub-spoke-architect, robots-monitor, article-enhancer. Categories: Signals, GEO, Web, SEO, Content, Paid, Social, Analytics.

### Signals — 26 signal _types_ (catalog). `/signals` is a numbered editorial timeline.

### Honest-content principle (enforced)
No fabricated logos/quotes/metrics; previews labeled "representative." v13 audit: marketing site has no fabricated logos/quotes/testimonials. The one hard stat kept is "Recovers 95%+ of lost visitors" (industry figure).

### "Request access" lead form + notifications
Shared modal `#nvfov` → `POST /api/demo-request` (now sends `vid`; on success dispatches `p2:lead_submit` so the tracker records the conversion). Notifiers: sheet, Slack (channel `C0BE016E2E8` = #intelligence-platform-request-access), email. Recipients via `DEMO_NOTIFY_EMAIL`: krishna.ladha@, abhilash.dg@, sudheer.d@, sparikh@position2.com.

### Email transport (Gmail API over HTTPS) — STILL PENDING two admin steps
Railway blocks SMTP. Code sends via Gmail API (HTTPS) when `GMAIL_SENDER` is set, reusing `GOOGLE_SA_JSON`. PENDING: (1) domain-wide delegation for the SA Client ID, scope `gmail.send`, enable Gmail API; (2) set `GMAIL_SENDER` to a real mailbox. Then `/admin/email-test` → `method:"gmail_api"`, `SENT OK`.

---

## ★ NEW IN v13 — ANONYMOUS VISITOR ANALYTICS

### Client tracker — `static/js/visitor_track.js`
- Included in `agents.html` **only for anonymous visitors**: `{% if not user %}<script src=".../visitor_track.js"></script>{% endif %}`.
- **Respects Do-Not-Track / Global Privacy Control.**
- Sets persistent `p2_vid` (localStorage + 1-year first-party cookie so the server can read it on form submit/login) → new vs returning. Per-tab `p2_sid` session with landing page, referrer, UTMs captured at session start.
- Per page view captures: referrer + host, UTM ×5, pages/session, time on page, **engaged time** (active seconds), max scroll %, total clicks, **CTA clicks** (Request access / Watch walkthrough / Sign in / agent cards / outbound), nav clicks, on-site search terms, **lead-form funnel** (open→started→submitted via `p2:lead_submit`), walkthrough-video opens, **rage clicks**, **Core Web Vitals** (LCP/CLS/INP), viewport/screen, language.
- Sends ONE consolidated beacon per page view via `navigator.sendBeacon` (fallback fetch keepalive) to `/api/atrack` on `visibilitychange→hidden` / `pagehide`.

### Ingest — `POST /api/atrack`
Parses beacon (JSON or text/plain), derives server-side fields (browser/OS/device via `_parse_ua`, IP from `X-Forwarded-For`, bot flag via `_BOT_RE`, IST timestamps), appends one row to the **"Visitor Analytics"** tab (auto-creates tab+header). Column order is `_VA_HEADER` (39 cols incl. Visitor ID, Session ID, New Visitor, Page, Referrer, UTM ×5, Landing, Pages In Session, Time On Page, Engaged Time, Max Scroll %, Total Clicks, CTA Clicks, Video, Form Stage, Search Terms, Rage Clicks, LCP, CLS, INP, Viewport, Screen, Language, Browser, OS, Device, Bot, IP, Events JSON).

### Dashboard — `/admin/visitors` (`admin_visitors.html`, data from `_fetch_visitor_analytics()`)
Admin-only. **Bots excluded from headline metrics.** KPIs (page views, unique visitors, sessions, new/returning, bounce, avg engaged, access requests + conversion rate, walkthrough views, rage clicks, identified visitors, companies); visits-over-time; top pages/referrers/UTMs; device/OS/browser; scroll-depth; Core Web Vitals (color-graded); CTA leaderboard; on-site searches; request-access funnel; landing pages; rage-click pages; recent activity; **Identified visitors & companies** table + **Top companies** chart.

### Visitor identity (3 mechanisms)
1. **Stitch on conversion (live, free):** `p2_vid` recorded on `/api/demo-request` ("Visitor ID" col on "Demo Requests"; `_read_access_requests` reads `A1:J`). `_va_identity_map()` maps `visitor_id → {name,email,company,source}` from form submissions, linking a known person to their prior anonymous sessions.
2. **Reverse-IP company (gated):** `_ip_company(ip)` via IPinfo, **only if `IPINFO_TOKEN` set** (cached per IP, resolved at dashboard load). No token → blank (never fabricated). Feeds "Top companies".
3. **Person-level provider (scaffold):** `POST /api/identify` → "Visitor Identities" tab, **gated by `IDENTIFY_TOKEN`**. No vendor pixel installed — to get individuals' names, contract a provider (RB2B/Vector), point its webhook here, add an EU consent banner.

### Privacy
Tracker honors DNT/GPC. Anonymous-visitor tracking (IP, reverse-IP, cookies) intersects CCPA/CPRA + GDPR; person-level identification needs EU consent. No SOC2/ISO claims.

---

## ★ REMOVED IN v13 (the demo/fake layer)
- **Context Graph** — `context_graph.html` deleted; `/context-graph` route + all nav/palette links removed (now 404s — do NOT reintroduce).
- **Anonymous Visitors proxy people** — `_DEMO_PROXY_PEOPLE` (10 fabricated people) + "● In your HubSpot list" pill removed. Page shows only real Sheet data.
- **HubSpot "Vimi Engage" CRM-match mock** — removed from `anonymous_visitors.html` + `static/js/anonymous_visitors.js` AND `linkedin_scraper.html` + `static/js/linkedin.js` (fake CRM records/conversations/"email sent", "Vimi found a CRM match" banners, the "🔥 Hot Leads — matched from HubSpot" tab/modal, the `KE_HOTLEADS` list). Clicking a person now opens the normal real drawer. (HubSpot **connector positioning** on the marketing site is legitimate copy and was kept.)

---

## ★ ADMIN DASHBOARDS — UNIFIED DESIGN (v13)
All three (Usage / Visitor Analytics / Access Requests) share one look:
- **`static/css/admin.css`** is linked **last** in each `<head>` so it overrides each page's inline CSS via source order. It defines: the Usage **background** (gradient + dotted grid via `body::before` + soft blob via `body::after`; each page's own bg layers hidden with `.bg,.bg-grid,.bg-blob,#scene,#cnv,.mesh,.vig,.li-aurora{display:none}`), the **Visitor-Analytics font stack**, the **breadcrumb topbar** + **user-pill menu**, a harmonized **card system** (stat cards, content cards, bars, sparkline, tabs, tables, pills), and a **multi-accent palette** that cycles indigo/violet/green/amber/cyan/pink across `.kpis .kpi` / `.stats .stat` cards (colored top bar + tinted value + corner glow) and bar fills.
- **`static/js/pfx_bg.js`** — Usage's Three.js WebGL particle field (`__pfx` IIFE, loads `three@0.160.0` from jsDelivr, `#pfx-bg` canvas `z-index:-1`, reduced-motion aware) extracted into a shared file and included on Visitor Analytics + Access Requests. (admin_usage keeps its identical inline copy, guarded by `window.__pfx`.)
- **Topbar standard:** brand + **`Hub › Admin › {Page}`** breadcrumb (left) + **user-pill dropdown** listing all three dashboards above Sign out, active page highlighted (right).

### Admin nav across the internal app
The admin-gated user-pill dropdown (`{% if user.email in [admins] %}`) on hub/gtm/seo/accounts/anonymous_visitors/embed (and linkedin_scraper via `tb-dd-item`) lists all three dashboards above Sign out; an inline script highlights the dd-item matching the current path.

---

## ★ INTERNAL /seo TAB — "SEO Studio" (unchanged)
Proxies to `https://github.com/ai-positon2/seo-apps` (live `https://seo-apps-production-37a6.up.railway.app/`). `_SERP_BASE`; `_seo_tools()` falls back to `_SEO_TOOLS_FALLBACK` (15 tools incl. gbp-qc-agent). `/seo/<slug>` embeds via `?pt=<SERP_PLATFORM_TOKEN>`. **PENDING:** GBP embed (`gbp-qc-agent-production.up.railway.app`) not provisioned; fix on GBP Railway side; never commit the secret-laden `gbp-qc-agent.zip`.

---

## VIMI / SIGNAL TRACKER / REFRESH (unchanged)
- **Vimi** = embedded AI assistant (`ppc_chat_widget.html`, backend `/api/ppc-chat` + `/api/ppc-upload`). Do NOT rename Vimi plumbing (`window.ppcOpen`/`ppc-*`/`_build_ppc_context`). Visible label is **GTM**.
- **OpenAI chain:** `OPENAI_INSIGHTS_MODEL` → `gpt-5.4` → `OPENAI_MODEL` → `gpt-4o-mini`.
- **Signal Tracker:** Healthcare (~1,251) + CSG (294); importance `type_weight × severity × recency (+ multi-intent)`; 90-day retention.

---

## ENVIRONMENT VARIABLES (Railway)
Core: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `GOOGLE_CLIENT_ID`, `SECRET_KEY`, `LOGIN_LOG_SHEET_ID`, `DEMO_REQUEST_SHEET_ID` (optional), `GOOGLE_SA_JSON`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `SERP_PLATFORM_TOKEN`, `GH_DISPATCH_TOKEN` (+ `GH_REPO`/`GH_WORKFLOW`).
Notifications: `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` (default `C0BE016E2E8`), `SLACK_WEBHOOK_URL`, `DEMO_NOTIFY_EMAIL`, `GMAIL_SENDER`, SMTP `SMTP_*` (unusable on Railway).
**NEW v13 (optional; features off until set):** `IPINFO_TOKEN` (reverse-IP company on /admin/visitors), `IDENTIFY_TOKEN` (enables + secures `POST /api/identify`).
GitHub Action secrets: `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

---

## HOW TO WORK ON THIS (proven-safe workflow)
1. Clone fresh into the bash sandbox `/tmp` each session (file tools only see `…/outputs`, NOT `/tmp`). Sandbox network: **git over `github.com` works, but `api.github.com`, GDELT, OpenAI, jsDelivr/CDNs, RSS are BLOCKED** — live data/CDN scripts (incl. particle Three.js) only run in production / the user's browser.
2. Edit real files via **Python string-replace/slice scripts in bash**. For tricky literals (JS, `"\n".join`) use a **single-quoted heredoc** (`<<'PYEOF'`) and splice; assert each replacement matched exactly once. NEVER put `//` comments in Python edit scripts. Set `git config user.email/name` before committing.
3. **Validate before every push:** `python3 -c "import ast; ast.parse(open('app.py').read())"`; Jinja-parse + render every `page` variant (ChainableUndefined env + stubbed `url_for`); `node --check` external JS + inline `<script>` (neutralise Jinja first); no `{{`/`{%`/`{#` in `<style>`/`<script>`; no duplicate `@app.route`; `<script>` tag balance; `admin.css` brace balance; smoke-test `_fetch_visitor_analytics()` by importing `app` with google libs stubbed + monkeypatching `_va_sheets_service`/`_ip_company`/`_read_access_requests`.
4. Push to `main` → Railway deploys ~90s. If rejected, `git pull --rebase origin main` then push.
5. Push token = GitHub classic (`repo`+`workflow`) as `https://x-access-token:<TOKEN>@github.com/...`. **Redact tokens (`sed -E 's/ghp_[A-Za-z0-9]+/[REDACTED]/g'`); remind the user to rotate the token after every session.**
6. The sandbox **cannot verify visuals or live data** — the user must eyeball the live page.

### Gotchas
- `<title>` has the same `{% elif page %}` chain — anchor searches on body-specific strings.
- `@media (...){#x{...}}` is a `{#` Jinja-comment trap — keep a space. No `{{`/`{%`/`{#` in `<style>`/`<script>` (CSS `}}` is fine). `admin_usage.html` intentionally has `{{ user.email }}` in a tracking script.
- `admin.css` loads **last** and intentionally overrides each admin page's inline CSS + hides their bg layers. If a page looks wrong, look for higher-specificity inline rules.
- Three.js/WebGL only where needed, reduced-motion aware, loads from CDN at runtime (prod only).

---

## CURRENT STATE (end of v13) — `main` HEAD `60b6700`
- Public site: 19 agents / 8 categories / 26 signal types; honest content; **anonymous visitor tracker live**.
- Demo/fake layer (Context Graph, proxy people, HubSpot Vimi-Engage mock) **removed**.
- **Anonymous visitor analytics** end-to-end (tracker → /api/atrack → "Visitor Analytics" → /admin/visitors); identity stitching on conversion live; reverse-IP + person-level provider scaffolded behind env vars.
- **Three admin dashboards unified** (shared admin.css + pfx_bg.js, breadcrumb topbar, user-pill 3-dashboard menu, multi-accent cards).

## OPEN ITEMS / TODO
1. **Email (highest priority, pending):** domain-wide delegation (`gmail.send`) + enable Gmail API + set `GMAIL_SENDER` → `/admin/email-test` = SENT OK. (Fallback: Resend/SendGrid.)
2. **GBP QC Agent embed:** provision `gbp-qc-agent-production.up.railway.app` + `frame-ancestors` + `?pt=`. Don't commit the secret zip.
3. **Reverse-IP company:** set `IPINFO_TOKEN` (or swap to Clearbit Reveal / in-house pipeline) to light up "Top companies". Geo (country/city) not yet wired.
4. **Person-level identity:** contract a provider, set `IDENTIFY_TOKEN`, point its webhook at `POST /api/identify`, add an EU consent banner.
5. **Optional:** enable the stubbed PostHog/GA4/Plausible shim (keys empty in `CFG`) for replay/funnels; self-host walkthrough MP4 for 1080p.
6. **Rotate the GitHub token** shared into chat (standing reminder).

---

## COMPETITOR / SIGNAL RESEARCH + ROADMAP (recorded, not built)
Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala.
- **Gaps:** co-op topic intent (Bombora), review-site intent (G2/Capterra), technographic change, **champion job-change tracking** (UserGems — highest ROI), hiring-surge, earnings/10-K mining, event attendance, layoffs, product-led usage.
- **Buildable now:** Earnings & Filings, Website-Change, Layoffs, Hiring Intent, light Technographic, Account-Brief.
- **With a connector:** Champion Tracker & Buying-Committee Mapper (HubSpot + Apollo), GA4/GSC signals, ad-account signals, de-anon vendor, product-usage. (v13: first-party web de-anon partly in place via visitor-analytics + reverse-IP + /api/identify.)
- **Requires buying data:** co-op topic intent, review-site intent, licensed technographics, event attendance.
- **Differentiators:** generative-search/AI-answer visibility (early) + execution (the agency runs the plays).
