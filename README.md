# Intelligence by Position²

A Flask platform of 30+ GTM/revenue-intelligence agents (account de-anonymization, contact
finding, social/ad/event intelligence, job-change alerts, SEO tooling, per-client portals, and
more) for Position², a B2B digital-marketing agency, live at `intelligence.position2.com`.

This repo also carries the **ABM Signal Tracker**, a standalone weekly CLI job that started this
project and still runs on its own schedule (see [below](#abm-signal-tracker-cli)).

## Contents

- [Surfaces and auth](#surfaces-and-auth)
- [Agents](#agents)
  - [B2B Intelligence Suite](#b2b-intelligence-suite-p2b2b-agents-staff-only)
  - [Signal and visitor intelligence](#signal-and-visitor-intelligence)
  - [SEO / GEO Suite](#seo--geo-suite-p2seo-staff-only)
  - [Client portals](#client-portals)
  - [Admin analytics](#admin-analytics-all-admin_required)
- [Layout](#layout)
- [Running locally](#running-the-flask-app-locally)
- [Environment variables](#environment-variables)
- [Testing](#testing)
- [Deploy](#deploy)
- [Known gaps](#known-gaps--open-items)
- [ABM Signal Tracker (CLI)](#abm-signal-tracker-cli)

---

## Surfaces and auth

Google SSO is open to **any** Google account, so the app separates what a signed-in user can see
into four surfaces:

| # | Surface | Who | Auth | Namespace |
|---|---|---|---|---|
| 1 | Public marketing site | Logged-out visitors | none | `/`, `/agents`, `/platform`, ... |
| 2 | Member workspace | Any signed-in Google user | `@login_required` | `/app/*` |
| 3 | Internal staff app | `@position2.com` accounts only | `@position2_required` | `/p2/*` |
| 4 | Client portals | Any signed-in Google account, gated per client | `_client_gate()` | `/<client-slug>/*` |

`ADMIN_EMAILS` (checked in `app.py`) is the single source of truth for admin rights; `admin_required`
gates every `/p2/admin/*` route off it. Most of the agent catalog below lives on surface 3
(`/p2/b2b-agents/...`) and is what this README documents in the most detail; a subset is also
exposed, metered, on surface 2 (`/app/...`) for any signed-in Google account, and a smaller subset
again is embedded per-client on surface 4.

---

## Agents

### B2B Intelligence Suite (`/p2/b2b-agents/*`, staff-only)

The flagship product: ten purpose-built agents, each with its own `tracker/*.py` domain logic,
Postgres or SQLite-backed persistence, and a dedicated report UI.

#### Contact Finder
`/p2/b2b-agents/company-people-intelligence` · `tracker/apollo_client.py`

Live Apollo.io company/people search and a grounded chat layer on top of it: point it at a role
and a company and it finds the person, or ask for a list by title, seniority, or industry.
Apollo's *search* endpoints (`mixed_people/api_search`, `mixed_companies/search`) are free;
*enrichment* (`people/match`, `people/bulk_match`, `organizations/enrich`) spends credits from one
shared agency pool, so every credit-spending action is explicit user action, never a side effect
of browsing. Person profiles, company-name resolution, and employer firmographics are all cached
(Postgres, TTL'd) to avoid paying twice for the same lookup. The biggest single feature by audit
depth in this codebase (thirteen recorded audit rounds); planned for external client launch.

#### Job Change Alert
`/p2/b2b-agents/job-change-alert` · `tracker/job_change_parser.py`, `tracker/job_change_store.py`

Tracks two things: newly detected job changes at people you watch, and the full tracked
people/company roster. Sourced entirely from Apollo's native Slack notification workflow
(parsed out of a specific channel), not from a scraper. Real, permanent scope limit: Apollo's
notification only ever carries a person's *new* role, never their prior employer.

#### LinkedIn Strategy Researcher
`/p2/b2b-agents/linkedin-strategy-researcher` · `tracker/arena_client.py`, `tracker/linkedin_playbook_store.py`

Search any company's LinkedIn page, then run a five-agent competitive-strategy analysis on your
own brand or a named competitor: company profile, posts, strategy (personas/hooks/CTAs/audience),
content & creative, messaging, and a competitive scorecard, plus a locally computed engagement
tab and an on-demand Claude "AI Insights" synthesis. Backed by the **Arena** vendor
(`ARENA_API_KEY`); results persist in Postgres so a playbook can be generated from any saved run.
Not to be confused with **LinkedIn Social Researcher** below, an unrelated, older, external tool
that briefly held the same name.

#### 42 North Dental Slot Checker
`/p2/b2b-agents/42-north-dental-slot-checker` · `tracker/slot_checker.py`

A read/visualize layer over a separate weekly scrape of a multi-brand dental chain's real booking
widgets (82 locations): what a new patient would actually be offered if they tried to book right
now, across every location. Source is a Google Sheet with a committed-JSON fallback if the live
read fails for any reason. Includes an on-demand AI briefing. Not part of the standard agent
registries; it's a hand-added card.

#### Social Media Intelligence
`/p2/b2b-agents/social-media-intelligence` · `tracker/sci_*.py` (identify, pipeline, vision, video, audio, classify, synthesize)

Given a company name or URL, resolves its handles across **Instagram, LinkedIn, X, TikTok,
YouTube, Facebook** and separately reads **Reddit** brand conversation as a seventh surface, pulls
recent organic posts, runs every image and video through **Claude vision** (plus Whisper
transcription for spoken video dialogue) to describe what the creative actually shows, then writes
a cited, per-platform and cross-platform report on content patterns and what correlates with
engagement. Two collection vendors, additive not exclusive: **Unipile** (a real authenticated
account per platform, live for LinkedIn) tried first, falling back to **Apify** (actor-based
scraping) where configured. Genuinely uncommon in this space: it looks at the actual pixels of a
competitor's creative, not just engagement metadata.

#### Event & Conference Intelligence
`/p2/b2b-agents/event-conference-intelligence` · `tracker/event_intel_*.py` (store, rubric, harvest, discover, audit, scorer, report, workroom, pipeline, intake)

Three modes over one Postgres store: **recommend** (score a client's whole event calendar against
its ICP, 0-110 on relevance/decision-maker access/engagement, and return a ranked shortlist plus a
"worth a look" second tier, with nothing padded in to fill it), **lookup** (name an event, get the
participant roster it publishes), and **workroom** (post-event follow-up: who to talk to and what
to say, refusing to fabricate a conversation nobody actually recorded). Events sell attendee
lists, they do not publish them, so what's collected is exhibitors/sponsors/speakers/partners,
each labeled by the role its own source page gave it. Two features use this platform's own
persistent database to do things a stateless research session structurally can't: outcome-driven
re-ranking from a client's own accept/reject history, and k-anonymity-gated cross-client interest
("N other similar clients also kept this event," with no other client's identity in the raw data).

#### LinkedIn Intelligence
`/p2/b2b-agents/linkedin-intelligence` · `static/js/linkedin.js`

Your own LinkedIn engagement data (people × post engagement) read from a Google Sheet and rendered
client-side, one sheet per surface (internal, and independently per client portal). Distinct from
every other LinkedIn-named agent in this repo (see the naming note in the sidebar of the codebase
docs) - this one is *your own* engagement, not a competitive read.

#### LinkedIn Social Researcher (currently hidden from listings)
`/p2/b2b-agents/linkedin-social-researcher`

An older, entirely external agent: an iframe embed of a third-party AI app-builder tool
(`watchtower-by-position2.vercel.app`), not this repo's code. Reads a year of a company's LinkedIn
posts and returns messaging, content mix, creative formats, engagement, and a 30/60/90 playbook.
Pulled from listings at the owner's request; nothing underneath was deleted, so a bookmarked link
still resolves and past runs still show in history.

#### Competitor Ad Intelligence
`/p2/b2b-agents/ad-intelligence` (also served as a built React/Vite app at `/ppc/ad-intelligence`)

Continuously collects competitor ad creative across platforms and surfaces messaging themes,
formats, and changes over time. Source lives in `apps/ad-intelligence/` (Vite); the built output
is committed to `ad_intelligence/` so Railway needs no Node build step at deploy time - a GitHub
Action rebuilds and re-commits it automatically whenever the source changes.

#### ABM Signal Tracker (in-app view)
`/p2/abm-signal-tracker/*`

The always-on view of the same account-intent-monitoring product described in the
[standalone CLI section](#abm-signal-tracker-cli) below: 26 signal types (funding, leadership
change, M&A, IPO, product launches, hiring surges, and more), each scored as
`type_weight × severity × recency` with a bonus when signals stack, refreshed weekly. A separate,
NorthStar-specific instance (coincidentally sharing the same display name) backs that client's
portal.

---

### Signal and visitor intelligence

#### Anonymous Website Visitors (de-anonymization)
`visitor_intelligence/` (resolver, pipeline, identity graph)

Company-level, multi-signal IP resolution with a connection-type hard gate and noisy-OR
confidence scoring, Apollo enrichment, and a 0-100 intent score; person-level identification
layers a persistent identity graph on top where a real signal exists. Deliberately **never
fabricates a person** - the graph only ever holds what a real signal actually supports. The
person-level store (`data/identity_graph.db`) holds real visitor PII and is gitignored; it is
never committed.

#### Vimi (GTM assistant)
`/api/ppc-chat`, `/api/vimi-chat/<account_id>`

An embedded AI assistant (public-facing label **GTM**) that helps reps act on signal data without
leaving the page. Two backends so account data never crosses a boundary it shouldn't.

---

### SEO / GEO Suite (`/p2/seo/*`, staff-only)

16 SEO/GEO tools, most backed by a separate React/Vite frontend (`seo-apps`, its own Railway
service) embedded here: **Keyword Research**, **Content Research**, **Competitor Analysis**,
**Article Recommendation**, **Content Enhancement**, **Enhance Existing Article**, **On-Page SEO
Audit**, **SEO & GEO Audit**, **Agent Readiness Audit**, **Image Alt Tag Audit**, **Location +
Service Pages**, **Hub & Spoke**, **Knowledge Base**, **Robots Monitor**, **Team Insights**, and
**GBP QC Agent**. `On-Page SEO Audit` runs 23 sections against live Core Web Vitals/PageSpeed
data; `Robots Monitor` crawls sitemaps daily and fires a Slack alert the moment a production page
goes unexpectedly noindexed.

---

### Client portals

`/<client-slug>/*`, gated per client via `_client_gate()`. A co-branded front door for a named
client (currently `northstaranesthesia`), assembled from a `CLIENTS` registry entry: branding,
which agents that client can see, which dashboards, and any purely-external embedded tools. Three
agent connection types: SERP-connected, dashboard-backed, and external-tool (iframe).

---

### Admin analytics (all `@admin_required`)

`/p2/admin/internal-usage`, `/p2/admin/external-usage` (also hosts the live self-test buttons for
Arena, Apollo, Unipile, and LPS AI Insights), `/p2/admin/client-usage`, `/p2/admin/anonymous-traffic`,
`/p2/admin/public-page-analytics`, `/p2/admin/public-agent-usage`, `/p2/admin/access-requests`.

---

## Layout

```
app.py                  Flask app, 224 routes: auth, the agent roster, admin dashboards,
                        per-client portals. The main entry point; see its own module
                        docstring and section-comment banners for a map of the file.
tracker/                Signal ingestion + per-agent business logic (76 modules) imported by
                        app.py and by the ABM Signal Tracker CLI.
visitor_intelligence/   Anonymous-visitor de-anonymization engine + identity graph.
templates/              Jinja templates, one (or a small family) per agent/page (43 files).
static/                 Per-agent CSS/JS, shared design-system tokens, shared JS (theme,
                        background effects, page-view tracking).
tests/                  pytest suite, 172 files / 4,350+ tests, see Testing below.
scripts/                Operational scripts run manually or from GitHub Actions (frontend
                        build, dashboard refresh, snapshot import/sync).
scripts/legacy/         CSG/NorthStar's original single-client onboarding scripts (superseded
                        by the general per-client portal system in app.py), kept because their
                        *output* (reports/dashboard_csg.html etc.) is still served live today.
docs/                   Design/planning docs for individual features, plus
                        CONTEXT_FOR_NEW_CHAT_*.md (a standing architecture/context brief kept
                        up to date for onboarding, human or AI, into a new session; read
                        this before making any non-trivial change).
data/                   SQLite databases + JSON snapshots. Committed on purpose: Railway runs
                        this app with no persistent disk, so these files ARE the durable store,
                        refreshed and re-committed by scripts/ and .github/workflows/.
                        apollo-accounts-export.csv is the ABM Signal Tracker's own seed company
                        list (Apollo export), also committed on purpose and actively read by
                        main.py / tracker/csv_loader.py, not stray data.
reports/                Pre-rendered HTML dashboards read directly by app.py at request time
                        (same reason as data/ above, not build artifacts, not dead weight).
apps/ad-intelligence/   Ad Intelligence React/Vite SOURCE.
ad_intelligence/        Ad Intelligence's built output, served at /ppc/ad-intelligence, committed
                        so Railway needs no Node build step (see Deploy below).
benchmarks/             Offline evaluation harnesses for specific agents (currently Event
                        Intelligence), has its own separate, pinned requirements-constraints.txt.
```

## Running the Flask app locally

```bash
pip install -r requirements.txt
python app.py           # http://localhost:8080
```

Every integration is read from an environment variable at call time (`GOOGLE_CLIENT_ID`,
`GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
...). Nothing here needs every credential to boot: a route whose integration isn't configured
fails closed on that one feature rather than at startup. `SECRET_KEY` is the one exception worth
setting explicitly even for local dev: without it the app falls back to a hardcoded dev key and
logs a loud warning on every start (every signed-in session becomes forgeable by anyone who's read
this source). `.env.example` covers the separate ABM Signal Tracker CLI's credentials, below.

## Environment variables

The app degrades feature-by-feature, not all-or-nothing, when a variable below is unset; see
each agent's section above for what specifically goes inert.

| Variable | Used by |
|---|---|
| `SECRET_KEY` / `FLASK_SECRET_KEY` | Session signing, **set this even locally** |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google SSO |
| `GOOGLE_SA_JSON` | Sheets reads: internal analytics, 42 North Dental Slot Checker |
| `DATABASE_URL` | Postgres, agent run history, Contact Finder caches, LinkedIn Strategy Researcher, Social Media Intelligence, Event & Conference Intelligence |
| `APOLLO_API_KEY` | Contact Finder, de-anonymization, person enrichment, Social Media Intelligence's own company search, one shared key/pool |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Contact Finder's cross-check, LinkedIn Strategy Researcher's AI Insights, 42 North's AI Insights, all of Social Media Intelligence's identify/vision/synthesis calls, Event & Conference Intelligence |
| `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL` | Vimi, Contact Finder's chat chain, Social Media Intelligence's Whisper transcription |
| `ARENA_API_KEY` | LinkedIn Strategy Researcher's entire vendor backend |
| `UNIPILE_API_KEY`, `UNIPILE_DSN` | Social Media Intelligence's LinkedIn/Instagram collection (LinkedIn is live) |
| `APIFY_API_TOKEN` | Social Media Intelligence's Facebook/TikTok/X collection (not currently set, see Known gaps) |
| `YOUTUBE_API_KEY` | Social Media Intelligence's YouTube collection (opt) |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | Social Media Intelligence's Reddit brand-conversation read (not currently set) |
| `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `SLACK_WEBHOOK_URL` | Job Change Alert ingestion, ABM Signal Tracker alerts |
| `SERP_PLATFORM_TOKEN` | SEO Suite's embedded `seo-apps` service |
| `IPINFO_TOKEN`, `IDENTIFY_TOKEN` (opt) | Visitor de-anonymization |
| `LOGIN_LOG_SHEET_ID`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID` | Various Sheets-backed logs |
| `GH_DISPATCH_TOKEN` | Triggers GitHub Actions from the app |
| `SMTP_*` | Unusable on Railway (kept for local/dev only) |

See Railway → Variables for the authoritative production list; a variable's absence is always
handled explicitly in code, never assumed.

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q
```

4,350+ tests across 172 files, no external credentials required for the vast majority (network
calls are mocked). `.github/workflows/run-tests.yml` installs `requirements.txt`, runs `pip check`,
then runs the full suite on every pull request and push to `main`;
`event-intelligence-tests.yml` additionally runs Event & Conference Intelligence's Postgres-backed
tests against a real `postgres:16` service container, since that subsystem's persistence logic is
part of what's under test. Always run with `python3 -B` / `PYTHONDONTWRITEBYTECODE=1` locally:
stray `.pyc` files have previously let a reverted fix keep passing.

## Deploy

Railway, auto-deploy on push to `main` (NIXPACKS builder, gunicorn, see `railway.toml` /
`Procfile`). `nixpacks.toml` additionally installs `ffmpeg` for Social Media Intelligence's
video-frame extraction.

**Frontend auto-build**: when `apps/ad-intelligence/**` changes on `main`,
`.github/workflows/build-frontend.yml` builds the React app on a clean Node 22 runner, copies
the result into `ad_intelligence/`, re-injects the Vimi chat widget, and commits it back,
which Railway then deploys. To do this locally instead: `bash scripts/build-frontend.sh`.

## Known gaps / open items

Kept short on purpose; the full, current, line-item list (with commit references) lives in
`docs/CONTEXT_FOR_NEW_CHAT_V29.md`; this is the subset worth knowing before touching the code.

- **No CSRF token and no explicit `SESSION_COOKIE_SECURE`/`SESSION_COOKIE_SAMESITE`.** Session
  cookies rely on Flask/browser defaults rather than an explicit, hardened configuration. Several
  routes derive per-request identity (rate limiting, abuse checks) from `X-Forwarded-For` without
  validating it against a trusted proxy chain (no `ProxyFix`), which is spoofable by a direct
  client. Flagged in the codebase's own docs as a scoped-but-not-started security pass; worth
  doing before Contact Finder's planned external client launch.
- **No security response headers** (`Content-Security-Policy`, `X-Content-Type-Options`,
  `Strict-Transport-Security`) are set anywhere; the only `after_request` hooks handle caching and
  gzip.
- **Several Social Media Intelligence vendor integrations are wired but unconfigured in
  production**: `APIFY_API_TOKEN` (Facebook/TikTok/X collection), `YOUTUBE_API_KEY`,
  `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`. Each fails closed to "no data for this platform"
  rather than erroring, which is easy to misread as "this company posts nothing there."
  Reddit specifically is a known, deliberate pause, not an oversight.
- **The agent roster is defined in three independent lists** (`AGENTS`, `APP_AGENTS`, a JS array
  in `templates/context.html`) plus `HIDDEN_AGENT_SLUGS` plus several hand-written
  `b2b_agents.html` cards; nothing derives one from another, so adding or renaming an agent means
  touching all of them by hand. Worth deriving from one source if the roster changes materially
  again.
- **`app.py` is a single ~18,000-line, ~950 KB file.** Every route, registry, and integration for
  every surface lives in it; there is no blueprint/package split. Deliberate so far (the module
  docstring and section banners serve as a map), but it is the one structural choice a fresh
  reviewer should expect to question as the platform keeps growing.
- **Several agents run a shared credit pool or per-process rate limiter with no shared store**
  (Apollo credits across Contact Finder/de-anon/enrichment; the rate limiter's real ceiling scales
  with gunicorn worker count, no Redis). Fine at current scale; worth revisiting before the
  external client launch.
- **Six API keys from earlier development sessions were exposed in scratch files and still need
  rotating** (Anthropic, OpenAI, Unipile, Google Cloud/YouTube, Apollo). This needs the account
  owner's own login to each vendor console; tracked as an explicitly deferred item.

## ABM Signal Tracker (CLI)

The original project: an automated weekly job that monitors healthcare company signals from
Apollo.io and fires structured alerts to Slack and Google Sheets. Still runs on its own
schedule (`.github/workflows/weekly_tracker.yml` and `refresh-dashboards.yml`), independent of
the Flask app above.

### Quick start

```bash
# 1. Install Python 3.11+, then:
pip install -r requirements.txt

# 2. Copy and fill in your credentials
cp config.yaml config.yaml   # edit in place, it's already gitignored

# 3. Test without writing anything
python main.py --dry-run

# 4. Clear dedup state before first real run
python main.py --reset-alerts

# 5. First live run
python main.py
```

### CLI reference

| Command | Effect |
|---|---|
| `python main.py` | Normal weekly run |
| `python main.py --dry-run` | Print alerts, no Slack/Sheets writes |
| `python main.py --force-refresh` | Re-enrich all companies |
| `python main.py --company-id abc123` | Process one company (debug) |
| `python main.py --reset-alerts` | Clear alert dedup history |
| `python main.py --verbose` | Enable debug logging |

### Credentials setup

Edit `config.yaml` (gitignored) and fill in:

- `credentials.apollo_api_key`: Apollo.io → Settings → Integrations → API
- `credentials.slack_webhook_url_high/medium/low`: Slack App → Incoming Webhooks (one per severity channel)
- `credentials.google_service_account_json`: path to downloaded GCP service account JSON
- `credentials.google_sheet_id`: from your Google Sheet URL
- `credentials.serpapi_key`: optional; leave blank for free Google News RSS fallback

For GitHub Actions: store `config.yaml`'s contents as repo secret `CONFIG_YAML` and the service
account JSON as `GOOGLE_SERVICE_ACCOUNT_JSON`.

### Output

- **Slack**: Per-signal Block Kit messages routed to `#signal-tracker-high/medium/low`
- **Google Sheets**: "Change Log" tab (one row per signal) + "Company List" tab
- **HTML dashboard**: `reports/latest.html`, regenerated after each run
- **SQLite**: `data/tracker.db`, full snapshot history and alert dedup log
