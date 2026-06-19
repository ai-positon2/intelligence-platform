# Intelligence by Position² — Full Context (v5 · June 2026)

Paste this entire file at the start of a new chat to give Claude full context on this platform. This v5 supersedes all earlier context files (v1–v4) — those are stale; ignore/delete them.

---

## WHAT THIS IS

**Intelligence by Position²** is a B2B sales-intelligence web app for the Position2 agency team. It surfaces buying signals (funding, leadership changes, M&A, hiring), de-anonymises website visitors, scrapes LinkedIn post engagement, ranks prospects by intent, and helps reps write personalised outreach via an embedded AI assistant called **Kairo**.

- **Live URL:** `https://intelligence.position2.com`
- **GitHub:** `https://github.com/ai-positon2/intelligence-platform` (public)
- **Local clone for edits (bash sandbox):** `/tmp/ip-fix`
- **Hosting:** Railway — auto-deploys on every push to `main` (~90s, NIXPACKS builder, `gunicorn app:app`)
- **Auth:** Google SSO, `@position2.com` only. `POST /auth/google` (google-auth). `@login_required` on protected routes; `@admin_required` for `/admin/*`.
- **Admins:** `krishna.ladha@position2.com`, `sudheer.d@position2.com`

---

## ARCHITECTURE (current — post "foundation" refactor)

```
intelligence-platform/
├── app.py                 ← Flask server (~2,800 lines): all routes, API endpoints, OpenAI calls
├── static/                ← NEW (foundation refactor): de-fragmented assets, served at /static
│   ├── css/
│   │   ├── ds-tokens.css       ← DESIGN SYSTEM: canonical tokens (color/surface/type/space/radius/motion)
│   │   ├── ds-components.css    ← DESIGN SYSTEM: reusable ds-* component library
│   │   ├── linkedin.css         ← LinkedIn Scraper page styles
│   │   ├── anonymous_visitors.css
│   │   ├── hub.css / ppc.css / seo.css
│   ├── js/
│   │   ├── linkedin.js          ← LinkedIn Scraper page logic (classic script; reads window.__LIDATA__)
│   │   └── anonymous_visitors.js
├── data/
│   ├── linkedin.json            ← LinkedIn Scraper dataset (injected by Flask, NOT embedded in HTML)
│   ├── tracker.db               ← Healthcare Signal Tracker SQLite
│   ├── tracker_csg_v2.db        ← CSG Signal Tracker SQLite
│   └── weekly-stats.json
├── templates/
│   ├── hub.html             ← Home hub (tool picker / landing)
│   ├── linkedin_scraper.html    ← LinkedIn ABM intelligence (slim shell, links static assets)
│   ├── anonymous_visitors.html  ← Anonymous visitor de-anonymisation (fetches data via API)
│   ├── ppc.html / seo.html      ← PPC + SEO tool shells
│   ├── accounts.html / admin_usage.html / login.html / embed.html / 403.html
│   └── ppc_chat_widget.html     ← shared chat widget include
├── reports/
│   ├── dashboard.html (4.4MB) / dashboard_csg.html (1.6MB)  ← Signal Tracker dashboards (GENERATED, single-line; see below)
├── tracker/, build_csg_dashboard.py, seed_csg_signals.py …  ← Signal Tracker data pipeline + generators
└── ad_intelligence/, apps/ad-intelligence/                  ← PPC Ad Intelligence mini-app (Vite/React build under apps/)
```

### Deploy
Push to `main` → Railway rebuilds (~90s). No hot reload. Force redeploy by touching a `# deploy-touch:` comment in `app.py`. `GET /robots.txt` → `Disallow: /`. Dashboards served with `Cache-Control: no-cache`.

---

## THE FOUNDATION REFACTOR (most important recent change — done June 2026)

The app used to be giant single-line HTML files with data, CSS and JS all inline (the LinkedIn page was a 244KB monolith with a 190KB embedded `D = {…}` blob). This was fragile — string-replace edits, truncation risk. It was **de-fragmented**:

- **LinkedIn Scraper**: data → `data/linkedin.json` (loaded by the Flask route, injected as `window.__LIDATA__ = {{ li_data|tojson|safe }}`); logic → `static/js/linkedin.js` (classic `<script src>`, same global scoping as before); styles → `static/css/linkedin.css`. Template now ~51KB. Route loads JSON: `linkedin_scraper()` reads `data/linkedin.json` and passes `li_data`.
- **Anonymous Visitors / hub / ppc / seo**: inline `<style>` extracted to `static/css/<page>.css`; Anonymous Visitors logic → `static/js/anonymous_visitors.js` (its data already comes from `GET /ppc/anonymous-visitors/data`).
- **Design system (#4)** introduced and linked on every page: `ds-tokens.css` + `ds-components.css`.

**Status:** Foundation + design system are merged to `main` and live. LinkedIn page's top nav-stat cards have been upgraded onto the new look (bigger/bolder/animated). The full `ds-*` component swap of every page is **in progress, page by page** — LinkedIn done first; Anonymous Visitors next.

### NOT yet refactored (intentionally)
`reports/dashboard.html` (4.4MB) and `dashboard_csg.html` (1.6MB) — the Signal Tracker dashboards. They are **generated** by Python build scripts (`tracker/dashboard_builder.py`, `build_csg_dashboard.py`) and served via `send_file`. Editing them by hand is wrong (regenerated) — the correct fix is at the generator level (a future task).

---

## DESIGN SYSTEM (static/css/ds-tokens.css + ds-components.css)

**Tokens (`:root` in ds-tokens.css):**
- Canvas `--bg:#050714`; surfaces `--s1/--s2/--s3` (translucent white); borders `--b1/--b2/--b3`
- Text `--tx:#e8ecf8 / --tx2:#8b94be / --tx3:#58607e`
- Accents (each with `-d` soft fill): `--accent:#7c83f5` (+`--accent2:#9b87fd`,`--accent-g` glow), `--green:#2dd4aa`, `--amber:#f5a623`, `--purple:#b78bfa`, `--cyan:#22d3ee`, `--rose:#fb7185`, `--hub:#ff7a59` (HubSpot), `--slack:#36c5f0`, `--linkedin:#0a66c2`
- Radius `--r/--rl/--rxl/--r2xl/--rpill`; spacing `--sp1..7`; type `--fs-2xs..2xl`; shadows `--shadow-sm/md/lg`; motion `--dur*`,`--ease`
- Font: **Space Grotesk** (Google Fonts)

**Components (`ds-*`):** `.ds-card` (+`--pad`,`--hover`), `.ds-sec`/`.ds-sec-title`/`.ds-sec-count`, `.ds-statrow`/`.ds-stat`/`.ds-stat-label`/`.ds-stat-val` (`.is-green/amber/purple/cyan/accent`), `.ds-badge` (`.is-*`), `.ds-chip` (`.is-on`), `.ds-btn` (`--primary`/`--ghost`), `.ds-avatar` (+`--sq`, gradient base + optional `<img>` overlay), `.ds-fbar`/`.ds-fbar__label`/`.ds-select`/`.ds-search`, `.ds-overlay`/`.ds-modal`, `.ds-drawer`, `.ds-empty`.

Theme is **dark**. Scrollbars are hidden site-wide (`scrollbar-width:none` + `::-webkit-scrollbar{display:none}`) but scroll still works. An animated three.js particle background runs at low opacity (0.18 desktop / 0.12 mobile) for readability.

---

## PAGES & ROUTES

| Route | Page / purpose |
|-------|----------------|
| `GET /` | redirect to hub or login |
| `GET /login`, `POST /auth/google`, `GET /logout` | Google SSO |
| `GET /hub` | Home — discipline picker (PPC / SEO cards). Pinned cinematic hero (~142vh), then discipline cards. |
| `GET /ppc` | PPC tool shell |
| `GET /seo` + `GET /seo/<tool_slug>` | SEO tools (embedded via embed.html) |
| `GET /ppc/anonymous-visitors` + `/data` | Anonymous Visitors dashboard (shell + JSON data API) |
| `GET /ppc/linkedin-scraper` | **LinkedIn Scraper** (loads data/linkedin.json) |
| `GET /ppc/ad-intelligence[/…]` | PPC Ad Intelligence mini-app (React/Vite build) |
| `GET /signal-tracker/<account_id>[/<section>]` | Signal Tracker dashboards (healthcare / csg), served from reports/*.html |
| `GET /accounts` | account selector |
| `GET /admin/usage` (+`/data`) | admin usage dashboard |
| `GET /api/whoami` | logged-in user |

### Kairo AI API endpoints (OpenAI-backed)
`/api/generate-email/<account_id>`, `/api/company-analysis/<account_id>`, `/api/research-company/<account_id>`, `/api/decision-makers/<account_id>`, `/api/kairo-chat/<account_id>` (POST), `/api/kairo-export` (POST), `/api/ppc-chat` (POST), `/api/insights`, `/api/weekly-stats`.
**Model chain:** `OPENAI_INSIGHTS_MODEL` env → `gpt-5.4` → `OPENAI_MODEL` env → `gpt-4o-mini`. Web search via `_responses_web_search()` (OpenAI Responses API `web_search_preview`).

---

## LINKEDIN SCRAPER — DEEP DETAIL (`/ppc/linkedin-scraper`)

Data file `data/linkedin.json` → `window.__LIDATA__` (global `D` in linkedin.js). Shape:
```
D = {
  posts:[{id,url,snippet,date,author,engagers:[{name,title,headline,company,industry,location,seniority,dm("Yes"/"No"),reaction("LIKE"/"EMPATHY"/…),degree("SECOND_DEGREE"/"THIRD_DEGREE"),commented("Yes"/"No"),url,pic}]}],
  people:[{name,first,last,url,title,headline,company,industry,size,location,country,seniority,dm,degree,bucket("csuite"/"vpdirector"/"managers"/"ics"/"unknown"),posts_engaged}],
  companies:[{name,industry,size,hq,country,people_count,dm_count,seniority_map{},posts_engaged,people:[…]}],
  company_lb:[[name,count],…],
  stats:{total_people,total_dms,total_posts,total_engagements,csuite_count,vp_count}
}
```
Current dataset: 99 people, 8 posts, 73 companies, 37 decision makers, 25 C-suite.

**UI structure:** A 4-card mega-tab row is the single KPI+nav header — **People / Companies / Posts / Hot Leads**. People/Companies/Posts switch panes (`switchTab`); **Hot Leads** (distinct orange tab) opens a centered modal of HubSpot-matched leads. Top cards are now 42px bold tabular numbers, color-coded (green/cyan/accent), with hover lift + radial glow + glowing active underline; counts animate up.
- **People tab**: two-row filter bar — seniority chips + search + live count on top; Country, Location, Degree, Activity selects + engagement date-range + 🎯 Decision Makers toggle + Hide Position² + Reset below. People grouped into seniority buckets as cards; clicking a card opens the person drawer. Company leaderboard sidebar.
- **Companies tab**: Min-People chips + search; company cards show name, industry·size, People + Decision Makers stats, location, a seniority-distribution bar. (Decluttered — no DM/posts pills or mini-avatars.) Click → company drawer.
- **Posts tab**: seniority chips + company select + date range + 🔍 search + "↕ Most engaged" sort; post cards → engager modal.
- Key JS in `static/js/linkedin.js`: `buildPeopleTab/buildCompaniesTab/buildPostTab`, `applyPLF` (people filters incl. `_city`/`_country` derived from location, engagement-date map `window.PDATES`), `applyPF` (posts, `PF.sort`), `applyCF` (companies), `openPersonDrawer/openCompanyDrawer/openEngDrawer/openPostModal`, `keHotLead*`/`openHotLeads`, helpers `av()` (avatar: gradient base + photo overlay w/ graceful onerror fallback), `ini/esc/gradFor`.

---

## KAIRO ENGAGE — outreach popup (demo of HubSpot + Slack)

Shared, self-contained component (`.ke-*` CSS, `window.KairoEngage.open(payload)`), embedded inline on the LinkedIn Scraper and Anonymous Visitors pages. **HubSpot + Slack are NOT really connected — all of this is mocked for demo.**

Trigger: clicking a person (anon visitor, or a LinkedIn engager / Hot Lead) opens a centered popup. Flow:
1. Activity card at top (clickable, expands): website-visit detail (pages on position2.com) for anon, or the LinkedIn post (snippet, reactions, "View on LinkedIn") for LinkedIn.
2. CRM match card — "We checked your HubSpot CRM and found {first} is a known contact… prospective client…" with a mock chat snippet + deal stage / owner (always **Krishna Ladha** or **Sudheer D.**) / open-deal.
3. Email composer ONLY (no Slack tab/integration strip — removed): To, Tone presets (Warm/Direct/Consultative — genuinely different copy), Subject, Kairo-drafted body, Regenerate, Send (simulated → success + "logged to HubSpot"). The email is **personalised to the post/page** the person engaged with. Copy is send-ready quality, no em-dashes, full signature using the owner's name.

Anon vs LinkedIn popups are visually distinct (purple CRM-led vs blue engagement-led). All sends/integrations simulated.

---

## ANONYMOUS VISITORS (`/ppc/anonymous-visitors`)
De-anonymised website visitors. Data via `GET /ppc/anonymous-visitors/data`. People/Companies tabs, filters (industry/seniority/engagement/location/size/date), drawers. A **"🔥 Hot Signals"** panel ("● In your HubSpot list" indicator) lists matched contacts; clicking a person opens the Kairo popup (HubSpot-match + email). Logic in `static/js/anonymous_visitors.js`.

---

## SIGNAL TRACKER DASHBOARDS (generated)
Two accounts — Healthcare (`/signal-tracker/healthcare`, `reports/dashboard.html`, `data/tracker.db`) and CSG (`/signal-tracker/csg`, `reports/dashboard_csg.html`, `data/tracker_csg_v2.db`). Single-line generated HTML with embedded `D` JSON. Sections: Signal Tracker (company cards by intent score), Market Radar, Actions (kanban), Trends. Company card → centered dossier modal with Kairo email / research / decision-makers actions. **Patch via generators, not by hand.** SQLite schema: `companies, snapshots, alerts_sent, weekly_runs`. Signal types: Funding Round, C-Suite Change, M&A Signal, IPO Signal, News Mention, Subsidiary Change.

---

## ENVIRONMENT VARIABLES (Railway)
`OPENAI_API_KEY`, `OPENAI_MODEL` (default gpt-4o-mini), `OPENAI_INSIGHTS_MODEL` (default gpt-5.4), `GOOGLE_CLIENT_ID`, `SECRET_KEY`, `LOGIN_LOG_SHEET_ID`, `GOOGLE_SA_JSON`.

---

## HOW TO WORK ON THIS (workflow that's proven safe)

1. Clone fresh: `git clone https://github.com/ai-positon2/intelligence-platform.git /tmp/ip-fix` (sandbox `/tmp` is cleared between sessions; the file-tool path `…/outputs/ip-fix` is separate/stale — do git work in `/tmp`).
2. Edit the real files (now mostly `static/css/*.css`, `static/js/*.js`, `data/*.json`, `templates/*.html`).
3. **Validate before every push** (integrity gate):
   - Templates: balanced `<script>`/`</script>`, ends with `</html>`, Jinja parses (`python3 -c "import jinja2;jinja2.Environment().parse(open(f).read())"`).
   - JS: `node --check static/js/<file>.js`; for inline-in-template JS, extract `<script>` blocks and `new Function(block)`.
   - app.py: `python3 -c "import ast; ast.parse(open('app.py').read())"`.
   - Optionally headless-run page JS against a DOM shim to confirm it renders.
4. Push to `main` → Railway deploys ~90s. For big/risky changes use a **branch + PR** (e.g. the foundation work used branch `foundation-refactor`).
5. Pushing needs a GitHub token (`github_pat_…` or classic with repo scope) supplied by the user; redact it from all output and remind the user to revoke it after.

### Gotchas
- Reports dashboards are single-line + generated — never hand-edit.
- Inline `<script>` blocks that contain Jinja (`{{ user.email }}`) can't be externalised to static files — leave those inline.
- LinkedIn JSON strings use JS escapes (`\[`, `\&`) — eval as JS (not strict JSON.parse) when extracting.
- Top-level `let/const` + `function` decls in a classic script are reachable from inline `onclick=` handlers (global lexical env) — preserved when externalising as a classic `<script src>` (not `type=module`).
- Avatars: LinkedIn photo URLs expire/403 — `av()` always renders a gradient + initials base with the photo overlaid, so failures degrade gracefully.

---

## ROADMAP (agreed priorities, in order)
1. ✅ **Foundation** — de-fragment monoliths into static/data files. (Done for the 5 render_template pages; reports/* generators pending.)
2. 🔜 **Design system adoption** — swap each page's bespoke components onto shared `ds-*`. In progress page-by-page (LinkedIn done; Anonymous Visitors next). Verify each visually (Claude in Chrome when available, else a presented HTML preview) before pushing.
3. Real HubSpot/Slack/email integrations (currently mocked).
4. Live data + identity resolution / enrichment (fix messy location/title/country fields).
5. Kairo as a real agent (grounded personalisation, sequences, A/B, learning from replies).
6. Outcome analytics (signal → outreach → reply → meeting → deal).
7. Accessibility, performance (virtualise big tables), automated tests (Playwright), security/permissions.

---

## LATEST STATE (June 2026)
`main` HEAD `82a7575`. Foundation refactor + design system live. LinkedIn Scraper top cards upgraded. Next planned: bring Anonymous Visitors (and remaining pages) onto `ds-*` with the same alive/bold top-card treatment, previewing each before deploy.
