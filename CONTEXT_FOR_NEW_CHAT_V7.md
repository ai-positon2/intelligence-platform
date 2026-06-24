# Intelligence by Position² — Full Context (v7 · June 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v7 supersedes all earlier context files (v1–v6)** — those are stale; ignore/delete them. v7 records the big "Vimi rebrand + chatbot ground-up redesign + Context Graph page" work cycle.

---

## WHAT THIS IS

**Intelligence by Position²** is an internal B2B revenue-/sales-intelligence web app for the Position2 agency team (Position2 is a B2B digital-marketing agency: SEO & organic growth, performance marketing/PPC, paid social, content, brand & website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, creative hiring, news), de-anonymises website visitors, scrapes LinkedIn engagement, tracks competitor ads, ranks prospects by intent, and helps reps act via an embedded AI assistant called **Vimi** (renamed from "Kairo" in this cycle — there is now **no** "Kairo" anywhere).

- **Live URL:** `https://intelligence.position2.com`
- **GitHub:** `https://github.com/ai-positon2/intelligence-platform` (public)
- **Hosting:** Railway — auto-deploys on every push to `main` (~90s, NIXPACKS, `gunicorn app:app`). HTML/CSS/JS goes live on push; data refreshes happen via GitHub Actions.
- **Auth:** Google SSO, `@position2.com` only. `@login_required` on protected routes; `@admin_required` for `/admin/*`.
- **Admins:** `krishna.ladha@position2.com`, `sudheer.d@position2.com`
- **Latest `main` HEAD (end of this cycle): `f6a6769`.**

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                       ← Flask server (~2,900 lines): routes, API, OpenAI (Vimi), insights scoring, live-refresh trigger
├── main.py                      ← Weekly orchestrator for the HEALTHCARE account (Sheets HIGH signals + News RSS LOW) -> data/tracker.db
├── fetch_csg_news.py / fetch_csg_jobs.py / fetch_csg_sheets.py   ← CSG fetchers (news/jobs/sheets)
├── fetch_healthcare_jobs.py     ← Healthcare creative-hiring scraper
├── weekly_digest.py             ← Ranks companies; writes reports/opportunities_<acct>.csv (+ optional AI brief / Slack post)
├── build_csg_dashboard.py
├── static/                      ← css/ ds-tokens.css, ds-components.css, hub.css, linkedin.css, anonymous_visitors.css, ppc.css, seo.css ; js/ linkedin.js, anonymous_visitors.js
├── data/                        ← tracker.db (healthcare ~1,251), tracker_csg_v2.db (CSG 294), weekly-stats.json
├── templates/
│   ├── hub.html                 ← Home hub (discipline picker) — single-viewport; sets window.VIMI_NAME; includes the shared widget; topbar link to Context Graph
│   ├── context_graph.html       ← NEW this cycle — interactive "Context Graph" data-flow page (see below)
│   ├── ppc_chat_widget.html     ← SHARED Vimi chat widget v5 (included on hub + every tool page) — fully redesigned this cycle
│   ├── linkedin_scraper.html, anonymous_visitors.html, ppc.html, seo.html
│   ├── accounts.html, admin_usage.html, login.html, embed.html, 403.html
├── reports/
│   ├── dashboard.html (~4.4MB) / dashboard_csg.html (~1.6MB)  ← Signal Tracker dashboards (GENERATED + Vimi-customised; single-line `const DATA`)
│   └── opportunities_healthcare.csv / opportunities_csg.csv
├── tracker/                     ← signal pipeline package (dashboard_builder.py, change_detector.py, sheets_client.py, news_client.py, news_relevance.py, jobs_client.py, snapshot_store.py, csv_loader.py, notifier_slack.py, notifier_sheets.py, signal_score.py)
├── scripts/refresh-dashboards.py ← rebuilds BOTH dashboards preserving Vimi; prunes >90d + reclassifies + strict news
└── .github/workflows/
    ├── refresh-dashboards.yml    ← weekly (Mon 08:30 UTC) + manual: fetch -> prune/classify -> rebuild -> digest -> commit
    └── weekly_tracker.yml        ← weekly (Mon 08:00 UTC): healthcare main.py
```

### Deploy & data model
- **Code/UI** (templates/static/app.py): push to `main` → Railway redeploys (~90s). No hot reload.
- **Data** (signals): refreshed by the GitHub Action (RSS/jobs/sheets run on GitHub's runners — no AI cost except the optional news gate, OFF by default). The Action commits updated DBs + dashboards.
- **Dashboards are GENERATED + Vimi-customised.** Never hand-edit `reports/dashboard*.html` structure blindly. The refresh pipeline splices the fresh single-line `const DATA = {...};` blob into the committed Vimi HTML (preserving Insights/chat). `scripts/refresh-dashboards.py` looks for the markers `INSIGHTS v10 JS` and `id="vimi-plat"` (the latter was `kairo-plat` before the rebrand). Structural JS changes (new KPI tiles etc.) must be applied to BOTH `tracker/dashboard_builder.py` AND the committed `reports/dashboard*.html`.

---

## VIMI REBRAND (this cycle)
The assistant was renamed **Kairo → Vimi** EVERYWHERE, case-preserving, across all files (templates, app.py, scripts, reports, workflows, docs). Producers and consumers were renamed in lockstep so nothing broke:
- API routes: `/api/vimi-chat/<account>` and `/api/vimi-export` (were `/api/kairo-*`); client write-action stubs `/api/vimi/{hubspot,slack,sheets,deck}`.
- JS global `window.VIMI_NAME` (set by hub + context_graph before the widget include; widget falls back to "there").
- localStorage keys `vimi_*` (sessions/mem/settings/pins/audit/onb/nudge) — note: this reset users' saved chat history/memory once.
- Python helpers `_vimi_completion`, `_vimi_model_chain`, `_vimi_chat_json`; system prompts say "You are Vimi…".
- Dashboard splice marker `id="vimi-plat"`; the model insight JSON key `vimi_take`; the fenced dossier block the widget parses is now ```vimi-card```.
- **QA done:** repo-wide case-insensitive search for "kairo" returns **zero**.

---

## VIMI — THE EMBEDDED AI ASSISTANT (widget, v5 "Vimi Studio")

Shared widget `templates/ppc_chat_widget.html` (one file: CSS + markup + ~1k-line IIFE). Backed by `/api/ppc-chat` (chat) and `/api/ppc-upload` (files). Ground-up redesigned this cycle.

**Design language**
- Minimal dark canvas, **slim glass header** (small gradient avatar, name "Vimi", status dot, ghost icon buttons: History, New chat, Memory, Settings, Expand, Close). No big colored header band.
- Welcome screen: a **Three.js WebGL particle orb** (point-cloud sphere, additive twinkle shader + faint link segments, cyan/violet/indigo palette — same engine as the login/hub pages, `three@0.160.0` from jsdelivr), a centered greeting "Hi {name}! How can I help you today?", and outlined **pill quick-replies** (Hot leads / Competitor ads / Buying signals / Draft outreach).
- **Glassy single-line composer**: attach / voice / emoji on the left, "Ask anything…", circular up-arrow send on the right.
- Fonts: Inter (UI) + Sora (display). Light/dark themes + cozy/compact density (apply live, persisted).

**Fresh-chat behaviour (this cycle)**
- Opening Vimi **always starts a new chat** (welcome). The chat you were in is auto-saved to History first. Empty "New chat" sessions are pruned so History only shows real conversations. First-ever open shows onboarding once, then a fresh chat. Past chats are reachable from the **History drawer** (search / reopen / delete). Helper: `freshChat()`.

**Feature set (all client-side, working against existing endpoints)**
Streaming replies (client reveal of the `/api/ppc-chat` answer), stop/regenerate, per-message copy/👍👎/save-to-memory, edit & resend, sessions/history drawer, voice input (Web Speech API) + optional spoken replies (speechSynthesis), slash commands (`/leads /email /research /export /digest /visitors /dossier /why`), @-mentions (company list from `/api/insights/<acct>`, with fallback), keyboard (Cmd/Ctrl+K open, ↑ to edit last, Esc), inline source-citation pills, sortable/filterable tables, inline charts (Chart.js), one-click export CSV / Excel (SheetJS) / JSON / PDF (jsPDF), "open in Gmail draft" (compose URL), contextual actions (draft outreach, decision-makers, account **dossier card** parsed from a ```vimi-card``` JSON block, why-hot explainer, digest, visitor summary), smarter Memory UI (personal/team, categories, suggestions), per-user defaults (focus vertical, target CPA, email tone), onboarding flow, **confirm-before-write modal** + client **audit log**, graceful retry/error states, sound + haptics (optional), proactive **nudge badge** (counts from `/api/insights`), **draggable + resizable** panel.

**Key globals / hooks (do not break):** `window.ppcOpen / ppcSend / ppcSugg / ppcNewChat / ppcShowMemory / ppcToggleExpand`; element IDs are `k-*` (e.g. `k-fab`, `k-border`, `k-panel`, `k-hdr`, `k-body`, `k-input`, `k-send`, `k-stop`, `k-voice`, `k-mem-badge`, `k-settings`, `k-memsheet`, `k-confirm`, `k-onb`, `k-fileinput`). The widget reads `window.VIMI_NAME`. Outside-click closes the panel — programmatic opens are guarded by a `_justOpened` timestamp (the old hub-pin gotcha).

**Write-actions / Phase 2:** HubSpot create/update, Slack send, Google-Sheets export, slide-deck generation go through the confirmation + audit UX and POST to `/api/vimi/{hubspot,slack,sheets,deck}`. **Those routes don't exist yet**, so they degrade cleanly to "queued — activates when the connector is live." Making them real needs `app.py` work + connector credentials (currently mocked by design).

**OpenAI model chain:** `OPENAI_INSIGHTS_MODEL` env → `gpt-5.4` → `OPENAI_MODEL` env → `gpt-4o-mini`. Web search via OpenAI Responses `web_search_preview`.

---

## CONTEXT GRAPH PAGE (NEW · `/context-graph`)

`templates/context_graph.html` — a beautiful, interactive explainer of how a buyer's data connects and where the intelligence layer sits. Reachable from the hub topbar ("✦ Context Graph").

- **Nodes:** core entities **Person / Account / Deal**; **person signals** (Pages Visited, Email History, LinkedIn Signals, Intent Score); **account signals** (CRM Record, Market Signals, Buying Committee, Readiness State); **Outreach Sent / Outcome**; and the **Vimi intelligence layer** core. Categories colour-coded: person=blue, account=green, deal=orange, intelligence=purple.
- **Flow narrative:** signals stream into Person/Account → converge into Deal → everything feeds the Vimi intelligence layer (scores intent, prioritises) → which drives Outreach → Outcome (loop back).
- **Visuals/interaction (alive like login):** connections are **travelling-particle filaments** on a 2D canvas; a **Three.js ambient point-field** + **blurred bokeh orbs** behind; nodes **drift**, the constellation **sways + parallaxes** with the cursor (depth layers), nodes are **draggable** (spring back); glamour: glowing **data packets** fire signals→cores and **burst into ripples** on arrival; hovering a node fires packets into its cores. Every node is **clickable** → right-side **detail drawer** (what it holds, data sources, where it flows, how the intelligence layer uses it) + CTA (Open Signal Tracker / Ask Vimi). Clickable **legend** filters by category. Layout sits in a padded band so nothing overlaps the title/legend. Uses `three@0.160.0`.

---

## SIGNAL TRACKER (unchanged from v6)

Two accounts: **Healthcare** (`/signal-tracker/healthcare`, ~1,251 companies) and **CSG** (`/signal-tracker/csg`, 294 companies). **Eight signal types** — HIGH (curated from Google Sheets): Funding Round, C-Suite Join, C-Suite Exit, Acquisition/M&A, IPO Signal, Subsidiary Change; MEDIUM (auto from news/jobs): Product Launch, Partnership, Creative Hiring; LOW: News Mention (important-only). **Position² relevance filter** (`tracker/news_relevance.py`, keyword-only, zero LLM cost) keeps only marketing-actionable signals. **90-day retention** pruned every refresh. **Importance scoring** (`tracker/signal_score.py`): `type_weight × severity × recency (+ multi-intent bonus)` — drives the Insights tab (`/api/insights/<account>`, score ≥ 6.0, top 120) and the weekly digest.

---

## PAGES & ROUTES (key)

| Route | Purpose |
|-------|---------|
| `GET /hub` | Home discipline picker; topbar link to Context Graph; sets `VIMI_NAME` |
| `GET /context-graph` | NEW interactive data-flow / intelligence-layer explainer |
| `GET /ppc`, `GET /seo` (+`/seo/<tool>`) | Tool shells |
| `GET /ppc/anonymous-visitors` (+`/data`) | Visitor de-anonymisation |
| `GET /ppc/linkedin-scraper` | LinkedIn ABM intelligence |
| `GET /ppc/ad-intelligence[/…]` | Competitor Ad Intelligence (React/Vite build) |
| `GET /signal-tracker/<account>[/<section>]` | Signal Tracker dashboards |
| `GET /accounts`, `GET /admin/usage` | account picker, admin usage |
| `POST /api/refresh-dashboard`, `GET /api/refresh-status` | live-refresh trigger + progress |
| `GET /api/insights/<account>` (+`/api/insights-meta`) | Vimi AI brief (scored, important-only) |
| `POST /api/ppc-chat`, `POST /api/ppc-upload` | Vimi chat backend (shared widget) + file upload |
| `/api/generate-email`, `/api/company-analysis`, `/api/research-company`, `/api/decision-makers/<account>`, `/api/vimi-chat/<account>`, `/api/vimi-export` | Vimi actions |
| `GET /api/whoami`, `POST /api/track` | user info, page-time tracking |

---

## ENVIRONMENT VARIABLES (Railway)
`OPENAI_API_KEY`, `OPENAI_MODEL` (default gpt-4o-mini), `OPENAI_INSIGHTS_MODEL` (default gpt-5.4), `GOOGLE_CLIENT_ID`, `SECRET_KEY`, `LOGIN_LOG_SHEET_ID`, `GOOGLE_SA_JSON`, `GH_DISPATCH_TOKEN` (live-refresh button). GitHub Action secrets: `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`. Optional: `SLACK_WEBHOOK_URL`. (No `VIMI_*`/`KAIRO_*` env vars exist.)

---

## HOW TO WORK ON THIS (proven-safe workflow)
1. Clone fresh into the bash sandbox `/tmp` each session (the file-tool `…/outputs` path is separate/stale for git). Sandbox network: **git over `github.com` works, but `api.github.com` and most RSS endpoints are BLOCKED** — workflow_dispatch + live scraping must run in the GitHub Action. `WebSearch` and the workspace `web_fetch` tool have network.
2. Edit real files (mostly `templates/*.html`, `static/*`, `tracker/*.py`, `fetch_*.py`, `app.py`, `scripts/*`).
3. **Validate before every push:** `python3 -c "import ast; ast.parse(...)"` for .py; Jinja parse (`jinja2.Environment(...).get_template(...)`) for templates; `node --check` each inline `<script>` (extract the block that has no `{{` Jinja); YAML parse for workflows; confirm dashboards' `const DATA` JSON parses and `<script>` tags balance.
4. Push to `main` → Railway deploys ~90s. Data changes also need the Action.
5. Pushing needs a GitHub token (classic w/ `repo`+`workflow`). **Redact tokens from all output and remind the user to revoke after.** The weekly Action may push in parallel — if a push is rejected, `git pull --rebase origin main` then push (then re-run QA).

### Gotchas
- `reports/dashboard*.html` are generated + single-line + Vimi-customised — patch the generator AND splice DATA; never hand-edit blindly. Splice markers: `INSIGHTS v10 JS`, `id="vimi-plat"`.
- **Jinja `{#`/`{{` trap:** CSS/JS inside a Jinja-rendered template must not contain `{{`, `{%`, or `{#` (e.g. write `{ #id` with a space in media queries). The shared widget is `{% include %}`-d, so the same rule applies there. Intentional Jinja (e.g. `window.VIMI_NAME = {{ ... | tojson }}`) is fine in real templates only.
- `re.sub` replacement strings process backslashes — when injecting JS that contains `\n`, pass a `lambda` replacement (or use `String.fromCharCode(10)` in the JS). Learned the hard way building the particle orb.
- `dashboard_builder.py` uses CRLF line endings — preserve them.
- Three.js is loaded from `https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js` (same version on login/hub/widget/context-graph).
- The widget's outside-click close means programmatic opens need the `_justOpened` guard.

---

## ROADMAP / PHASE 2 (agreed, not yet built)
1. **Real write-backs for Vimi:** implement `/api/vimi/{hubspot,slack,sheets,deck}` wired to the HubSpot/Slack/Apollo/Google-Sheets connectors (+ slide-deck generator). UI + confirm + audit already ship.
2. **Admin usage + cost meter**, server-side **permissions** (who can trigger writes), persistent **audit log** (currently client-side).
3. **True scheduled briefings** posted into Vimi chat; optional **server-side SSE streaming** (today streaming is a client-side reveal).
4. Activate **CSG HIGH signals** (create CSG Google Sheets + add IDs to `CONFIG_YAML`).
5. Richer data feeds (News API, funding/M&A feed, LinkedIn hiring), outcome analytics (signal → outreach → reply → meeting → deal), accessibility/perf, automated tests.

---

## LATEST STATE (end of this cycle)
`main` HEAD `f6a6769`. Assistant fully rebranded **Kairo → Vimi** (zero "kairo" left). The shared Vimi widget was rebuilt from scratch (v5 "Vimi Studio": minimal glass header, Three.js particle-orb welcome, centered greeting, pill quick-replies, glassy composer, light/dark + density) with the full feature set (streaming, sessions/history, voice, slash, @, tables, charts, exports, memory, settings, onboarding, confirm-before-write, audit, nudges, drag/resize). Vimi now **opens a fresh chat every time** while saving and preserving all past chats in History. A new **/context-graph** page explains the data model and intelligence layer with a live, draggable particle-flow graph. Backend write-integrations remain Phase 2.
