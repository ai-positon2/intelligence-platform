# Intelligence by Position2 — Full Context for New Chat (v4 — June 2026)

Paste this entire file at the start of a new chat to continue work without losing context.

---

## WHAT THIS PROJECT IS

**Intelligence by Position2** is a B2B sales-intelligence web application built for the Position2 agency team. It surfaces buying signals (funding rounds, leadership changes, M&A, hiring surges) across prospect companies, ranks them by intent score, and helps reps write personalised outreach via an embedded AI called **Kairo**.

**Live URL:** `https://intelligence.position2.com`
**GitHub repo:** `https://github.com/ai-positon2/intelligence-platform`
**Local clone used for edits:** `/tmp/ip-fix` (in bash sandbox)
**Railway project:** Auto-deploys on every push to `main` (~90s build, NIXPACKS builder, gunicorn)
**Login:** Google SSO only (`@position2.com` domain). Implemented via `POST /auth/google` with `google-auth` library.

---

## ARCHITECTURE OVERVIEW

```
intelligence-platform/
├── app.py                        ← Flask server (~2,800 lines, all routes + API endpoints)
├── railway.toml                  ← NIXPACKS builder, gunicorn start command
├── requirements.txt
├── data/
│   ├── tracker.db                ← Healthcare account SQLite DB
│   └── tracker_csg_v2.db         ← CSG account SQLite DB
├── reports/
│   ├── dashboard.html            ← Healthcare Signal Tracker (~4.2 MB, single-line file)
│   └── dashboard_csg.html        ← CSG Signal Tracker (~1.5 MB, single-line file)
├── templates/
│   ├── login.html                ← Google SSO login page
│   ├── hub.html                  ← Home hub (tool picker)
│   ├── accounts.html             ← Account selector
│   ├── embed.html                ← Shared topbar wrapper for embedded SEO/PPC tools
│   ├── ppc.html                  ← PPC chat interface
│   ├── seo.html                  ← SEO tools shell
│   ├── admin_usage.html          ← Admin usage dashboard
│   └── ...others
└── ad_intelligence/              ← PPC Ad Intelligence tool (separate mini-app)
```

---

## DEPLOYMENT

- **Platform:** Railway (paid, NIXPACKS)
- **Start command:** `gunicorn app:app --bind 0.0.0.0:$PORT`
- **Auto-deploy:** Push to `main` → Railway rebuilds in ~90s
- **Force redeploy trick:** Update the `# deploy-touch: <timestamp>` comment in `app.py` when Railway doesn't pick up a change
- **No-cache headers:** All dashboard HTML served with `Cache-Control: no-cache, no-store, must-revalidate`
- **robots.txt:** `GET /robots.txt` → `Disallow: /` (blocks all crawlers — site is login-gated)

---

## TWO ACCOUNTS / DASHBOARDS

The app serves two parallel Signal Tracker accounts:

| Account | URL | HTML file | DB |
|---------|-----|-----------|----|
| Healthcare | `/signal-tracker/healthcare` | `reports/dashboard.html` | `data/tracker.db` |
| CSG | `/signal-tracker/csg` | `reports/dashboard_csg.html` | `data/tracker_csg_v2.db` |

Both dashboards are **massive single-line static HTML files** (~4 MB) served via `send_file()` with no-cache headers. They contain all company + signal data embedded as a JSON blob (`window.D = {...}` / `const D = {...}`).

**CRITICAL:** These files are single-line. Never open them in a text editor. Always patch via Python `str.replace()` with assertion guards. Validate JS after every patch with `node --check` or `node -e "new Function(block)"`.

---

## SIGNAL TRACKER DASHBOARD — FULL FEATURE MAP

### Data Structure (embedded JSON `D`)

```js
D = {
  feed: [{
    name, domain, score,        // company name, domain, intent score 0-100
    momentum,                    // "Rising" | "Steady" | "Cooling"
    timing,                      // "CALL TODAY" | "THIS WEEK" | "NEXT MONTH" | etc.
    contact,                     // e.g. "Chief Executive Officer"
    hook,                        // Kairo's suggested opener sentence
    why,                         // Why now — reason for timing
    pitch,                       // What to pitch
    next,                        // Suggested next step
    signals: [],                 // string array of signal labels
    svc: [],                     // service fit labels
  }],
  out: [{                        // Kairo output (talking points etc.)
    company, talking_points: []
  }],
  ins: {                         // Market insights
    market_pulse: [],
    themes: [{theme, count, campaign_angle, companies: []}],
    strategic_moves: [{move, rationale, impact, owner}],
    risks: [{company, flag, implication}]
  },
  acts: [],                      // Kanban actions
  meta: {cos, sigs},
}
```

### UI Sections

1. **Signal Tracker** (main view) — Company pipeline cards ranked by intent score
2. **Market Radar** — Overview charts: Intent Distribution donut, Momentum donut, Account Signal Mix bars + deeper sections (Market Pulse, Signal Themes, Attention Flags, Strategic Moves)
3. **Actions** — Kanban board (Do Today / This Week / Monitor)
4. **Trends** — Weekly trend charts

### Company Card → Centered Modal Flow

**Clicking a company card** (`irShowCo(i)`) opens a **900px centered overlay** (`#ir9-doss-overlay`, z-index: 800). It does NOT open the side drawer (`irOpen`).

**Modal content structure (top to bottom):**

1. **Action card** — 2×2 grid at the very top:
   - Kairo Email (primary indigo button, `data-iract="email"`)
   - Research with Kairo (secondary, `data-iract="research"`)
   - Decision Makers (secondary, `data-iract="dm"`)
   - LinkedIn (secondary, external link)

2. **Hero banner** — Company logo (Clearbit) with initials fallback, name, domain, SVG score ring, momentum/timing/contact badges

3. **Kairo's opener** — Quote card with gradient background

4. **Why Now / What to Pitch** — Side-by-side colored cards

5. **Signals** — Hover-interactive chips

6. **Service fit** — Green-accented chips

7. **Talking points** — Numbered card rows

8. **Next step** — Amber accent callout

9. **Dossier divider** — Then the `#ir9-deep-inline` slot loads the Kairo Deep Dossier

### Kairo Deep Dossier (`irDeepOpen` / `/api/company-analysis/<account_id>`)

Loads inside `#ir9-deep-inline` within the modal. Uses OpenAI (gpt-5.4 or fallback gpt-4o-mini) with web search. Returns:
- `dv3-hero` — Score hero card (ring + urgency + service)
- `dv3-grid` — 3-col intel cards (why/ctx/timing)
- `dv3-obj-card` — Accordion objections
- `dv3-subj-row` — Subject line rows with copy button

### Decision Makers Modal (`irDecisionMakers` / `/api/decision-makers/<account_id>`)

- Opens as `#cm-dm-overlay` (z-index: 900, above everything)
- Calls `/api/decision-makers/<account_id>?company=...&domain=...`
- Uses OpenAI with web search to find C-suite, founders, directors with LinkedIn URLs
- Displays in `cm-dm-person` cards with initials avatar, role, bio, LinkedIn link button
- Closes on backdrop click or Escape key

### Market Radar — All Clickable

Every card and legend item in Market Radar is now clickable:

- **Intent Distribution card** → `irShowKpiOverview('intent-all')` → drawer showing Critical/High/Watchlist bands with company lists
- **Momentum card** → `irShowKpiOverview('mom-all')` → Rising/Steady/Cooling breakdown
- **Signal Mix card** → `irShowKpi('sig')` → full signal type breakdown
- **Individual signal bars** → `irShowSigType(sigType)` → companies with that signal type
- **Legend items** (Critical/High/Watch/Rising/Steady/Cooling) → individual `irShowKpi(k)` handlers

### Action Buttons — Z-Index Fix

When action buttons (Email, Research) are clicked from inside the dossier modal, the dossier overlay (`#ir9-doss-overlay`) is closed first before opening `#ir9-sig-overlay` (z-index 700). Without this, the sub-modals open behind the dossier.

---

## FLASK APP — KEY API ENDPOINTS

| Route | Description |
|-------|-------------|
| `GET /` | Redirect to hub or login |
| `GET /robots.txt` | Returns `Disallow: /` |
| `GET /login` | Google SSO login page |
| `POST /auth/google` | Google credential verification |
| `GET /hub` | Home page — tool picker |
| `GET /accounts` | Account selector |
| `GET /signal-tracker/<account_id>` | Signal Tracker dashboard shell |
| `GET /dashboard/<account_id>` | Serves the static dashboard HTML |
| `GET /api/whoami` | Returns logged-in user info |
| `GET /api/company-analysis/<account_id>` | Kairo Deep Dossier (OpenAI + web search) |
| `GET /api/generate-email/<account_id>` | Kairo email generation |
| `GET /api/research-company/<account_id>` | Full company research (OpenAI + web search) |
| `GET /api/decision-makers/<account_id>` | C-suite / key people finder (OpenAI + web search) |
| `POST /api/kairo-chat/<account_id>` | Conversational Kairo (grounded on DB signals) |
| `GET /api/insights/<account_id>` | Generate market insights |
| `GET /api/weekly-stats[/<account_id>]` | Weekly stats JSON |
| `GET /ppc` | PPC chat shell |
| `POST /api/ppc-chat` | PPC chat (OpenAI) |
| `GET /seo` | SEO tools shell |
| `GET /seo/<tool_slug>` | Embedded SEO tool via embed.html |
| `GET /health` | Railway health check |
| `GET /admin/usage` | Admin usage dashboard |

### AI Model Chain

All AI endpoints use this priority chain:
```python
OPENAI_INSIGHTS_MODEL env var → "gpt-5.4" (ChatGPT 5.4) → OPENAI_MODEL env var → "gpt-4o-mini"
```

Web search uses `_responses_web_search()` helper which wraps OpenAI Responses API with `web_search_preview` tool.

---

## AUTHENTICATION

- Google OAuth 2.0 (credential posted from frontend, verified server-side via `google-auth`)
- Session stored in Flask session (cookie)
- `@login_required` decorator on all protected routes
- `@admin_required` for `/admin/*` routes
- Login tracking logged to Google Sheet (`LOGIN_LOG_SHEET_ID` env var)
- Google Client ID: set via `GOOGLE_CLIENT_ID` env var

### Allowed users

Defined in `ALLOWED_EMAILS` list in `app.py`. Currently `@position2.com` domain users.

Admin users (for `/admin/usage`): `krishna.ladha@position2.com`, `sudheer.d@position2.com`.

---

## ENVIRONMENT VARIABLES (Railway)

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI API (required for all AI features) |
| `OPENAI_MODEL` | Fallback model (default: `gpt-4o-mini`) |
| `OPENAI_INSIGHTS_MODEL` | Preferred model (default: `gpt-5.4`) |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `SECRET_KEY` | Flask session secret |
| `LOGIN_LOG_SHEET_ID` | Google Sheet ID for login tracking |
| `GOOGLE_SA_JSON` | Google Service Account JSON (base64 or raw) |

---

## DASHBOARD HTML FILES — CRITICAL CONSTRAINTS

### Single-line files
Both `reports/dashboard.html` and `reports/dashboard_csg.html` are **single-line** files (~4 MB). The entire HTML, CSS, and JS is on one line.

### Patching method
ALWAYS use Python `str.replace()` with exact string matching:
```python
c = open(path).read()
assert OLD_STRING in c, "anchor not found"
c = c.replace(OLD_STRING, NEW_STRING, 1)
open(path, 'w').write(c)
```

### JS validation after every patch
```bash
node -e "
const html = require('fs').readFileSync('reports/dashboard.html','utf8');
const re = /<script>([\s\S]*?)<\/script>/g;
let m, i=0, ok=true;
while((m=re.exec(html))!==null){
  i++;
  try{new Function(m[1]);}
  catch(e){console.log('Block '+i+' ERROR: '+e.message);ok=false;}
}
console.log(ok?'ALL CLEAR':'ERRORS');
"
```

### Common JS string pitfalls
Single quotes inside onclick/onerror attributes nested in JS strings must be escaped:
```js
// WRONG — breaks script block:
onerror="this.style.display='none'"
// CORRECT:
onerror="this.style.display=\'none\'"
```

### Both files must be patched identically
Every change applies to both `dashboard.html` (Healthcare) and `dashboard_csg.html` (CSG).

---

## KEY JS FUNCTIONS IN DASHBOARD

| Function | Purpose |
|----------|---------|
| `irShowCo(i)` | Opens company modal for feed index `i` |
| `irDossClose(e)` | Closes `#ir9-doss-overlay` (only on backdrop click) |
| `irDeepOpen(name)` | Loads Kairo Deep Dossier into `#ir9-deep-inline` |
| `irDecisionMakers(name,domain)` | Opens Decision Makers modal, calls API |
| `irResearch(co,dom)` | Opens Research modal (`#ir9-sig-overlay`) |
| `irSignalEmailSetup(co,dom,...)` | Opens email setup modal |
| `irShowKpi(k)` | Opens KPI drill-down drawer in side panel |
| `irShowKpiOverview(k)` | Opens intent/momentum overview drawer |
| `irShowSigType(sigType)` | Shows companies with a specific signal type |
| `irOpen(title,html)` | Opens side drawer (`.ir9-drw`, z-index: 700) |
| `irSigModalOpen(title,html)` | Opens centered 620px modal (`#ir9-sig-overlay`) |
| `_renderRad(ins)` | Renders Market Radar section |
| `_glanceCounts()` | Returns intent/momentum counts from D.feed |
| `ir9donut(parts,cTop,cBot)` | Builds conic-gradient donut chart HTML |
| `ir9leg(parts,iract)` | Builds clickable legend rows |
| `ir9barsClickable(rows)` | Builds clickable signal mix bars |
| `aiBtns(name,domain,svc)` | Builds 4-button action row (side drawer context) |
| `coHeader(name,domain,color)` | Builds company avatar+name+domain HTML |
| `esc(s)` | HTML-escape a string |
| `ini(name)` | Get 2-char initials |
| `scC(score)` | Score → color string |
| `momB(momentum)` | Momentum → badge HTML |
| `tCls(timing)` / `tLbl(timing)` | Timing → CSS class / label |
| `miniRow(name,dom,score,timing,i)` | Compact company row for drawers |
| `liPpl(name)` / `liCo(name)` | LinkedIn people/company search URLs |

---

## Z-INDEX LAYERS

| Element | Z-index | Purpose |
|---------|---------|---------|
| `.ir9-drw` (side drawer) | 700 | Company intel side panel |
| `#ir9-sig-overlay` | 700 | Centered 620px modal (email, research) |
| `#ir9-doss-overlay` | 800 | Kairo Deep Dossier 900px centered modal |
| `#cm-dm-overlay` | 900 | Decision Makers modal |

**Rule:** When opening a sub-modal from within `#ir9-doss-overlay`, close the dossier first:
```js
var _dov = document.getElementById('ir9-doss-overlay');
if(_dov) _dov.style.display = 'none';
```

---

## COMPANY LOGO STRATEGY

1. Try `https://logo.clearbit.com/<domain>` (free, no API key)
2. On `onerror`, hide `<img>` and show fallback `<span>` with initials

```html
<img src="https://logo.clearbit.com/domain.com"
     onerror="this.style.display=\'none\';this.nextSibling.style.display=\'flex\'"
     style="display:block">
<span style="display:none;...">CH</span>
```

---

## CSS CLASS NAMESPACING

| Prefix | Belongs to |
|--------|-----------|
| `.ir9-*` | Original Signal Tracker UI components |
| `.cm-*` | Company modal (beautiful redesign) — added this session |
| `.cm-dm-*` | Decision Makers modal |
| `.dv3-*` | Kairo Deep Dossier v3 content sections |
| `.ev3-*` | Animation system v3 |

---

## EMBED.HTML — SHARED TOPBAR

Used by all SEO/PPC embedded tools. Key recent change: **"Open in new tab" button was removed** (was `class="newtab-btn"`, appeared on every embedded tool page).

Template variables: `embed_url`, `breadcrumb`, `current`, `user`.

---

## RECENT CHANGES LOG (this session)

1. **Kairo Deep Dossier redesign** — moved from side drawer to centered 900px modal (`#ir9-doss-overlay`). `irShowCo` now opens this directly instead of side drawer.

2. **Fixed "Generate briefing" + "Ask Kairo" buttons** — unescaped single quotes in JS string caused entire script block to fail. Fixed `\'open\'` escaping in dossier objection accordions.

3. **Beautiful company modal** — rewrote `coHtml` in `irShowCo`:
   - Action card grid at top (Kairo Email, Research, Decision Makers, LinkedIn)
   - Hero banner with Clearbit logo, SVG score ring (no harsh glow)
   - Opener quote card, Why Now / What to Pitch 2-col grid
   - Signal chips, service fit chips, talking point cards, next step callout
   - CSS classes: `.cm-hero`, `.cm-avatar`, `.cm-score-wrap`, `.cm-opener`, `.cm-2col`, `.cm-card`, `.cm-sig-chip`, `.cm-svc-chip`, `.cm-tp-card`, `.cm-next`, `.cm-action-card`, `.cm-act-btn`

4. **Decision Makers feature** — New API endpoint + modal:
   - `GET /api/decision-makers/<account_id>` → OpenAI web search → JSON `{people: [{name,role,linkedin,bio}]}`
   - `irDecisionMakers(name,domain)` JS function
   - `#cm-dm-overlay` modal (z-index 900)
   - Changed DM button from `<a href="liPpl(...)">` to `<button data-iract="dm">`

5. **Sub-modal z-index fix** — Email/Research buttons in dossier modal now close dossier before opening sub-modal.

6. **Market Radar fully clickable** — All 3 cards + all legend rows now open detail drawers. Added `irShowKpiOverview()`, `irShowSigType()`, `ir9barsClickable()`. Added `k` values to all intentParts/momParts items.

7. **robots.txt** — Added `GET /robots.txt` route returning `Disallow: /`.

8. **Removed "Open in new tab"** — Removed from `templates/embed.html` topbar.

---

## HOW TO WORK ON THIS

### Making a change

1. Edit `reports/dashboard.html` + `reports/dashboard_csg.html` (both, always)
2. Validate JS: `node -e "..."` (see above)
3. Commit + push: `git add ... && git commit -m "..." && git push origin main`
4. Railway auto-deploys in ~90s
5. If Railway doesn't pick up: touch `app.py` with a timestamp comment

### Python syntax check for app.py
```bash
python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
```

### Force Railway redeploy
```bash
# Add/update timestamp comment in app.py:
# deploy-touch: 2026-06-16T10:00:00
git add app.py && git commit -m "force redeploy" && git push
```

---

## SQLITE SCHEMA (both DBs)

```sql
companies:  apollo_id PK, name, domain, industry, city, state, first_seen, is_active
snapshots:  id, apollo_id, snapshot_date, employees, annual_revenue, latest_funding_type, tech_stack JSON, leadership_json JSON
alerts_sent: id, apollo_id, signal_type, signal_detail, severity, sent_at, signal_date, source_url, dry_run
weekly_runs: id, run_date, companies_checked, signals_*, duration_seconds
```

Signal types: `Funding Round`, `C-Suite Change`, `M&A Signal`, `IPO Signal`, `News Mention`, `Subsidiary Change`

Severity: `HIGH` | `MEDIUM` | `LOW`

---

## KNOWN GOTCHAS

1. **Single-line HTML files** — Every regex/replace must match exactly. Use `python3 -c "assert OLD in c"` before patching.

2. **JS string quote escaping** — Single quotes inside onclick/onerror attributes embedded in JS string literals must use `\'`. Unescaped quotes crash the entire script block silently.

3. **Both files always** — `dashboard.html` and `dashboard_csg.html` must be patched identically.

4. **Sub-modal z-index** — Always close `#ir9-doss-overlay` before opening `#ir9-sig-overlay`. Dossier is z:800, sig-overlay is z:700.

5. **`window._ACCT_ID`** — Set at dashboard load time, used by `irDecisionMakers` and other fetch calls to know which account (`healthcare` or `csg`) to query.

6. **Clearbit logo** — Free but occasionally returns a default icon. The `onerror` fallback to initials handles this gracefully.

7. **Railway build time** — ~90 seconds. No hot-reload. Every change requires a full push + wait.

---

End of V4 context. Last commit: `6d31bfa` (fix: remove Open in new tab button).
