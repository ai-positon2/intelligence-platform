# Company Signal Tracker

Automated weekly tracker that monitors healthcare company signals from Apollo.io and fires structured alerts to Slack and Google Sheets.

## Quick Start

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

## CLI Reference

| Command | Effect |
|---|---|
| `python main.py` | Normal weekly run |
| `python main.py --dry-run` | Print alerts, no Slack/Sheets writes |
| `python main.py --force-refresh` | Re-enrich all companies |
| `python main.py --company-id abc123` | Process one company (debug) |
| `python main.py --reset-alerts` | Clear alert dedup history |
| `python main.py --verbose` | Enable debug logging |

## Credentials Setup

Edit `config.yaml` (gitignored) and fill in:

- `credentials.apollo_api_key` — Apollo.io → Settings → Integrations → API
- `credentials.slack_webhook_url_high/medium/low` — Slack App → Incoming Webhooks (one per severity channel)
- `credentials.google_service_account_json` — path to downloaded GCP service account JSON
- `credentials.google_sheet_id` — from your Google Sheet URL
- `credentials.serpapi_key` — optional; leave blank for free Google News RSS fallback

## Running Tests

```bash
pytest tests/ -v
```

## GitHub Actions

Store `config.yaml` contents as a repository secret named `CONFIG_YAML` and the service account JSON as `GOOGLE_SERVICE_ACCOUNT_JSON`. The workflow runs every Monday at 08:00 UTC and can also be triggered manually.

## Output

- **Slack**: Per-signal Block Kit messages routed to `#signal-tracker-high/medium/low`
- **Google Sheets**: "Change Log" tab (one row per signal) + "Company List" tab
- **HTML dashboard**: `reports/latest.html` regenerated after each run
- **SQLite**: `data/tracker.db` — full snapshot history and alert dedup log

---

## Monorepo layout (unified platform)

This repo is the single source for the Position² Intelligence Platform.

```
.                      Flask backend (app.py), serves everything
ad_intelligence/       Built Ad Intelligence app (served at /ppc/ad-intelligence) — committed
apps/ad-intelligence/  Ad Intelligence React/Vite SOURCE (build target of the above)
reports/               Prebuilt Signal Tracker dashboards
data/                  SQLite signal databases
tracker/               Signal ingestion pipeline
scripts/build-frontend.sh   Builds apps/ad-intelligence -> ad_intelligence/ (+ re-injects Kairo chat widget)
```

### Deploy (single Railway service)
Railway runs this as a clean **Python** service (gunicorn, see `railway.toml`) and serves the
committed `ad_intelligence/` build directly — fast and reliable, no Node build on Railway.

### Frontend auto-build (GitHub Actions)
When `apps/ad-intelligence/**` changes on `main`, `.github/workflows/build-frontend.yml` builds the
React app on a clean Node 22 runner, copies it into `ad_intelligence/`, re-injects the Kairo chat
widget, and commits the result back — which Railway then deploys. No manual build-and-copy.

### Rebuild the Ad Intelligence app locally
```
bash scripts/build-frontend.sh        # builds apps/ad-intelligence -> ad_intelligence/
```
