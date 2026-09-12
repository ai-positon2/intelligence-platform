# Intelligence by Position²

A Flask platform of 30+ GTM/revenue-intelligence agents (account de-anonymization, contact
finding, social/ad/event intelligence, job-change alerts, SEO tooling, per-client portals, and
more) for Position², a B2B digital-marketing agency — live at `intelligence.position2.com`.

This repo also carries the **ABM Signal Tracker**, a standalone weekly CLI job that started this
project and still runs on its own schedule (see [below](#abm-signal-tracker-cli)).

## Layout

```
app.py                  Flask app — 223 routes: auth, the agent roster, admin dashboards,
                        per-client portals. The main entry point; see its own module
                        docstring and section-comment banners for a map of the file.
tracker/                Signal ingestion + per-agent business logic (75 modules) imported by
                        app.py and by the ABM Signal Tracker CLI.
visitor_intelligence/   Anonymous-visitor de-anonymization engine + identity graph.
templates/              Jinja templates, one (or a small family) per agent/page (43 files).
static/                 Per-agent CSS/JS, shared design-system tokens, shared JS (theme,
                        background effects, page-view tracking).
tests/                  pytest suite, 162 files / 4,000+ tests — see Testing below.
scripts/                Operational scripts run manually or from GitHub Actions (frontend
                        build, dashboard refresh, snapshot import/sync).
scripts/legacy/         CSG/NorthStar's original single-client onboarding scripts (superseded
                        by the general per-client portal system in app.py) — kept because their
                        *output* (reports/dashboard_csg.html etc.) is still served live today.
docs/                   Design/planning docs for individual features, plus
                        CONTEXT_FOR_NEW_CHAT_*.md (a standing architecture/context brief kept
                        up to date for onboarding — human or AI — into a new session).
data/                   SQLite databases + JSON snapshots. Committed on purpose: Railway runs
                        this app with no persistent disk, so these files ARE the durable store,
                        refreshed and re-committed by scripts/ and .github/workflows/.
reports/                Pre-rendered HTML dashboards read directly by app.py at request time
                        (same reason as data/ above — not build artifacts, not dead weight).
apps/ad-intelligence/   Ad Intelligence React/Vite SOURCE.
ad_intelligence/        Ad Intelligence's built output, served at /ppc/ad-intelligence — committed
                        so Railway needs no Node build step (see Deploy below).
benchmarks/             Offline evaluation harnesses for specific agents (currently Event
                        Intelligence).
```

## Running the Flask app locally

```bash
pip install -r requirements.txt
python app.py           # http://localhost:8080
```

Every integration is read from an environment variable at call time (`GOOGLE_CLIENT_ID`,
`GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
...) — see Railway → Variables for the full production list. Nothing here needs every
credential to boot: a route whose integration isn't configured fails closed on that one feature
rather than at startup. `SECRET_KEY` is the one exception worth setting explicitly even for
local dev — without it the app falls back to a hardcoded dev key and logs a loud warning on
every start (every signed-in session becomes forgeable by anyone who's read this source).
`.env.example` covers the separate ABM Signal Tracker CLI's credentials, below.

## Testing

```bash
pytest tests/ -v
```

4,000+ tests, no external credentials required for the vast majority (network calls are
mocked). `.github/workflows/run-tests.yml` runs the full suite on every pull request;
`event-intelligence-tests.yml` additionally runs Event Intelligence's Postgres-backed tests
against a real `postgres:16` service container, since that subsystem's persistence logic is
part of what's under test.

## Deploy

Railway, auto-deploy on push to `main` (NIXPACKS builder, gunicorn — see `railway.toml` /
`Procfile`). `nixpacks.toml` additionally installs `ffmpeg` for Social Media Intelligence's
video-frame extraction.

**Frontend auto-build**: when `apps/ad-intelligence/**` changes on `main`,
`.github/workflows/build-frontend.yml` builds the React app on a clean Node 22 runner, copies
the result into `ad_intelligence/`, re-injects the Vimi chat widget, and commits it back —
which Railway then deploys. To do this locally instead: `bash scripts/build-frontend.sh`.

---

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
cp config.yaml config.yaml   # edit in place — it's already gitignored

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

- `credentials.apollo_api_key` — Apollo.io → Settings → Integrations → API
- `credentials.slack_webhook_url_high/medium/low` — Slack App → Incoming Webhooks (one per severity channel)
- `credentials.google_service_account_json` — path to downloaded GCP service account JSON
- `credentials.google_sheet_id` — from your Google Sheet URL
- `credentials.serpapi_key` — optional; leave blank for free Google News RSS fallback

For GitHub Actions: store `config.yaml`'s contents as repo secret `CONFIG_YAML` and the service
account JSON as `GOOGLE_SERVICE_ACCOUNT_JSON`.

### Output

- **Slack**: Per-signal Block Kit messages routed to `#signal-tracker-high/medium/low`
- **Google Sheets**: "Change Log" tab (one row per signal) + "Company List" tab
- **HTML dashboard**: `reports/latest.html`, regenerated after each run
- **SQLite**: `data/tracker.db` — full snapshot history and alert dedup log
