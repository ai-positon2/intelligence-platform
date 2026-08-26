# Intelligence by Position2 - Full Context (v26 - August 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v26 supersedes all earlier context files (v1-v25)** - older versions are stale; ignore any pasted copy, and if `CONTEXT_FOR_NEW_CHAT_V25.md` (or older) still exists in the repo root, delete it as part of landing this file per the standing one-canonical-file convention.

**Latest `main` HEAD at the end of this cycle: `87c2d89`** (always `git pull` to confirm; Railway auto-deploys every push). `app.py` is **17,221 lines / 185 `@app.route` decorator lines** (211 total registered URL rules including loop-registered `add_url_rule` families and multi-decorator legacy redirects), up from 16,465 lines / 167 routes at v25. The test suite is now **94 files, 2,136 tests, all passing** (`PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q`), up from 61 files / 1,562 tests at v25.

**Important honesty note about this refresh:** v25 was authored right at the *start* of this cycle (commit `de0784e`), not the end - everything below from Ad Intelligence fixes through Unipile happened AFTER v25 was written and was never folded back in until now. That's why this refresh adds three entirely new, previously-undocumented, fully-shipped agents (LinkedIn Strategy Researcher native, Gentle Dental Slot Checker, Social Creative Intelligence Analyst) rather than incremental deltas - v25's own "WHAT V25 ADDS" section is gone from this file because it's now historical; its permanent effects (Contact Finder, ABM Signal Tracker, Job Change Alert) are still fully documented in their own sections below, unchanged.

---

## WHAT V26 ADDS ON TOP OF V25 (this cycle's work)

Seven threads, in the order they shipped:

1. **Ad Intelligence dashboard fixes** (`62da042`, `543e8ab`, `7ae5c5d`) - pinned the Vite build's asset base path (rebuilds had been silently reverting a prior asset-path fix), fixed a mismatched favicon, replaced a stale lightning-bolt logo and PPC header with the real Arena mark. Routine, small.
2. **LinkedIn Strategy Researcher built as an entirely new NATIVE agent** (`af0d1c7` through `f65b3b9`, ~15 commits), replacing the old external/iframed tool of the same name (which was renamed to **LinkedIn Social Researcher** and hidden to free up the name - see its own section below). Arena-vendor-backed, 5-agent competitive analysis + a locally-computed engagement tab + an on-demand Claude "AI Insights" synthesis + strategic playbook generation. Own dedicated section below - **the biggest single addition to this doc's coverage this cycle**, since it shipped entirely inside v25's blind spot.
3. **Gentle Dental Slot Checker built from scratch** (`b543477` through today's color-ramp commits, ~20 commits) - a real-time appointment-slot-availability dashboard for a dental-chain client, sourced from a weekly-refreshed Google Sheet a separate scraping agent populates. Own dedicated section below.
4. **Social Creative Intelligence Analyst built from scratch across 3 phases** (`d40d8ea`, `aab7435`, `7180e38`, plus many follow-up fixes through today) - given a company name, resolves its handles across 6 social platforms, scrapes recent posts, runs every image/video through Claude vision, and synthesizes a cited per-platform + cross-platform creative-strategy report. The largest single net-new agent this cycle. Own dedicated section below.
5. **Unipile added as a second collection vendor for SCI's LinkedIn/Instagram** (`87c2d89`, this session) - a connected-real-account model, tried first, falling back to the pre-existing Apify path unchanged if unavailable. Purely additive. Covered inside the SCI section below.
6. **Public agent run cap raised to 100 for `@position2.com` staff** (`d1b4218`) - external users keep the existing 10-runs-per-agent cap; internal accounts (same server-verified session email used everywhere else) now get 100, applied everywhere the cap is enforced (`/app` and client portals both).
7. **This context-file refresh itself** (v25 -> v26), on explicit user request: "Summarize in detail everything about this website and everything we have done... update the context file."

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 = a B2B digital-marketing agency: SEO/organic, performance/paid media, paid social, content, brand/website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, anesthesiologist/creative hiring, news, new-job-detected alerts on tracked people), de-anonymizes website visitors to company and (where a signal exists) person, **finds and enriches contacts at target companies via Apollo**, scrapes LinkedIn engagement, runs a native competitive-strategy analysis agent, tracks appointment-slot availability for a healthcare client, analyzes competitors' organic social creative across 6 platforms with real Claude-vision analysis, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), helps reps act via an embedded AI assistant (**Vimi**, visible label **GTM**), and serves **co-branded client portals** that can also embed **agents built entirely on other platforms.**

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` -> `https://seo-apps-production-37a6.up.railway.app`
- **Third-party agent frontend (NOT our code, NOT our repo):** `https://watchtower-by-position2.vercel.app`. The user builds these on an unrelated AI app-builder platform; we only receive and iframe the public URL, plus a `postMessage` run-signal snippet the user deployed into it. This backs **LinkedIn Social Researcher** (the old, external, currently-hidden agent - see its own section below), not the new native LinkedIn Strategy Researcher.
- **Hosting:** Railway, auto-deploys on every push to `main` (~60-100s for the Flask app via NIXPACKS/`gunicorn app:app`; a few minutes for `seo-apps`). HTML/CSS/JS goes live on push.
- **Admins (`ADMIN_EMAILS`):** `krishna.ladha@`, `sudheer.d@`, `reporting@`, `sparikh@`, `abhilash.dg@`, `pushpendra.k@` (all `position2.com`), unchanged this cycle. **This set is the ONLY place admin access is defined.** `admin_required` gates every `/p2/admin/*` route off it, the template context processor derives `is_admin` from it, and `/api/whoami` returns `is_admin` from it so client-rendered surfaces read the same flag. Add a person here and nowhere else.

### FOUR SURFACES + TWO-TIER AUTH (the biggest structural fact)

Google SSO is open to **any** Google account. That forces surface separation with two auth tiers, four surfaces total:

| Surface | Who | Auth | Namespace | Theme |
|---|---|---|---|---|
| **1. Public marketing site** | Logged-out prospects | none | top-level (`/`, `/agents`, `/platform`, `/why-intelligence`, ...) | always dark |
| **2. Member workspace `/app`** | ANY signed-in Google user | `@login_required` | `/app/*` | dark |
| **3. Internal staff app `/p2/*`** | `@position2.com` only | `@position2_required` | `/p2/*` (hub, b2b-agents, seo, abm-signal-tracker, admin, playbook, ...) | light/dark toggle |
| **4. Client portals `/<slug>`** | any signed-in Google account | `_client_gate()` | `/<client-slug>/*` (e.g. `/northstaranesthesia`) | dark, co-branded |

- After login: `@position2.com` -> `/p2/hub`; any other signed-in user -> `/app`.
- Old top-level internal paths (`/hub`, `/gtm/...`, `/admin/...`) 301-redirect to `/p2/...`, `/p2/gtm/*` 301-redirects to `/p2/b2b-agents/*`, and `/p2/accounts` + `/p2/signal-tracker/<account>` 301-redirect to `/p2/abm-signal-tracker/*` in exactly one hop.
- **Standing rename rule:** when a persisted URL/slug is renamed, the old one keeps 301-redirecting AND every read path keyed off the old slug is aliased to the new one. A past bug dropped historical runs because only routing was fixed, not the read side. See `[[feedback-persisted-identifier-renames]]`.
- Auth decorators in `app.py`: `login_required`, `admin_required` (= position2 + admin email), `position2_required`. Client gating is `_client_gate(client)` (not a decorator).

---

## ABM SIGNAL TRACKER (unchanged since v25)

**This is the general Healthcare/CSG/NorthStar account-based signal product** - not to be confused with the NorthStar-specific instance described further down, which independently happens to carry the same "ABM Signal Tracker" display name.

Renamed from "Company/Signal Tracker" a cycle ago: display text first (`280bb6c`), then real URLs (`12c90cb`, redirects preserved in one hop for all three prior generations of the URL), plus a card-grid layout bug fix on the account picker (`f45edf1` - a `minmax()` grid sizing bug plus a latent `.main` width bug it surfaced). See `tests/test_abm_signal_tracker_url_rename.py` and `tests/test_accounts_layout.py`.

---

## JOB CHANGE ALERT (unchanged since v25)

**What it is:** a live page tracking two things - (1) newly-detected job changes at tracked people, and (2) the full roster of people/companies being watched. Route base `/p2/b2b-agents/job-change-alert` (`@position2_required`).

**Origin and scope, worth knowing before touching it:** sourced entirely from Apollo's own native Slack notification workflow (`#job_change_alert_apollo`, channel `C0AUUH7BNUA`), parsed by `tracker/job_change_parser.py` and stored via `tracker/job_change_store.py` (SQLite, `data/job_change_alerts.db`). A separate, broken Arena-based "Track B" agent was explicitly, permanently abandoned in favor of this Slack-sourced approach. **Real scope limit, unfixable without a different data source: Apollo's notification only ever carries the person's NEW role, never their prior employer.**

**Tracked-contacts/companies roster (673 people / 274 companies)** is blocked from a live Sheets read by a Workspace DLP policy; runs off a manual `.xlsx`-import stopgap (`scripts/import_job_change_tracked_snapshot.py` -> `data/job_change_tracked_snapshot.json`) that self-heals to live data automatically once the sharing block is lifted.

**UI** (`templates/job_change_alert.html`): three tabs (Recent Job Changes / People We Track / Companies We Track), an 8-week "Weekly Momentum" trend chart, per-tab filters, a detail modal, working DuckDuckGo-sourced logos (Clearbit stopped serving anonymous embeds platform-wide, discovered and fixed this cycle before v25).

**Known open items:** `SLACK_BOT_TOKEN`'s read-scope for channel history is unverified; the token needs adding to GitHub Actions secrets separately from Railway; the tracked-roster sheet sharing block still needs a Workspace-admin fix; a third-party Coresignal-backed tool (Swan) reportedly catches job changes Apollo's alert misses (flagged, not built on).

---

## CONTACT FINDER (the biggest feature by audit depth)

**Route base:** `/p2/b2b-agents/company-people-intelligence` (slug/file/JS/CSS names all still say `company_people_intelligence` from before the display-name rename). Staff-only (`@position2_required`). **The user plans to open this to external paying clients "soon" - the last full audit cycle (pre-v25) was explicitly framed as pre-launch hardening.**

### The routes and the credit model

Apollo's **search** endpoints are free; only **enrichment** spends credits from one shared agency account:

- `mixed_people/api_search` and `mixed_companies/search`: **0 credits**. All browsing, filtering, counting and vocabulary learning runs here.
- `people/match`, `people/bulk_match`, `organizations/enrich`: **1 credit per record**. Only explicit user action reaches these.
- `people/bulk_match` is capped at 10 per request by Apollo and 50 ids per bulk reveal by us.
- Multiple caches exist purely to avoid paying twice: person profiles (version-stamped, 90-day positive TTL), company-name resolution (Postgres, survives a deploy), employer firmographics (30-day TTL).
- **Apollo's `organization_headcount_twelve_month_growth` is a FRACTION** (0.19 = 19%) for OUTPUT/display - a completely separate direction from the FILTER input (`headcount_growth_min/max`, whole percentages) - confirmed live against a real production Apollo key.

### Thirteen audit rounds, each one commit plus one test file whose module docstring states the defects in plain language

| Audit | Commit(s) | The recurring defect |
|---|---|---|
| 1. Search filters | `0f9469b`, `b5a9fd6` | the industry filter did not filter by industry |
| 2. Vocabulary pickers | `1173dbe`, `e55831b` | free text where a closed list existed |
| 3. Chat filters | `62dfa2c`, `c029778` | chat accepted values the pickers would reject |
| 4. Enrich flow | `af5ede0` | paying twice, promising data Apollo does not return |
| 5. Export flow | `4460bed` | the file did not say what the screen said |
| 6. History flow | `8ce0409` | purchases not recorded, no retention, duplicates |
| 7. Dashboard flow | `d2b38d6` | the grid described the tab, not its own rows |
| 8. Buckets + domain filter | `999af38`, `5f3e0f6`, `d5006b9` | saved-vs-net-new split misread; absence not gated on a real search |
| 9. Companies mirror + 4 more | `d1d62bd`, `fd8cdcc` | domain-unconfirmed fix missing on the companies twin; outage read as rejection |
| 10. Two deferred findings | `4a9eace` | cache hit billed like a fresh purchase; reason-count undercount |
| 11. Pre-launch: billing/filters/access/abuse | `2c9b83e` | fixable correctness/security gaps ahead of external launch |
| 12. Funding-ceiling crash | `0642c95` | unbounded numeric input wearing an "Apollo is down" costume |
| 13. Fill-filters mis-snap + keyword-echo | `6c10403` | unbounded-range heuristic always picks the extreme bucket |

**The pattern, stated once more:** a surface asserting something its data does not support. An empty result reading as a fact about the world rather than a fact about the request. A number silently clamped or misassigned with nothing surfaced that it happened. When auditing any other flow in this app, that is the thing to look for first.

**Also added pre-v25:** Claude as a second opinion on the NLP intent parser (`3256bac`) - critique-and-correct over OpenAI's extraction, wired into both Fill-filters and chat. **Ships inert: `ANTHROPIC_API_KEY` was not set at the time** - check current status in the ENVIRONMENT VARIABLES section below, since other features have since started depending on the same key.

### Known open items inside Contact Finder

- Verify current `ANTHROPIC_API_KEY` status (was unset as of the Claude cross-check shipping; multiple other features now share it - see below).
- External client launch is planned but not scheduled - the shared-credit-pool and `/credits`-aggregate-leak items are still open pending external-auth design.
- The chat path has never been exercised against live OpenAI/Apollo keys end to end.
- The 120-row history cap silently truncates a paged search; zero-result searches are never saved to history.
- `_cpi_probe_company_free` guesses only `.com` when deriving a domain from a name.
- The per-process rate limiter's true ceiling scales with gunicorn worker count (no shared/Redis store).

---

## TESTING DISCIPLINE

```bash
cd <repo> && PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q
```

- **Always set `PYTHONDONTWRITEBYTECODE=1` / use `python3 -B`.** macOS system Python writes `.pyc` files outside the repo, which once let a mutant survive a source restore and silently invalidated a mutation run.
- **Mutation testing is the gate, not the suite passing.** After writing tests for a fix, deliberately break each fixed line one at a time and confirm a test dies, then restore.
- **A mutation test needs fixture data where the buggy and fixed computation would actually diverge.**
- **Text assertions on a JS bundle cannot distinguish a working guard from a disabled one.** Several tests shim `document`/`window`/`fetch`, `eval` the real bundle/inline scripts in node, and assert on the HTML/requests actually produced. Skips (not fails) when node is unavailable.
- **Live-verifying a UI change needs a temporary, fully-removed preview route** (or a standalone preview script that monkeypatches the store layer with synthetic data and runs the real Flask app on a scratch port) - screenshot in both dark and light theme via the Browser pane, then confirm `git diff --stat app.py` shows zero changes before committing anything permanent.
- Do not write change-detector tests. If a mutant is unreachable in practice, say so in the commit message instead of pinning it.

---

## APOLLO PERSON ENRICHMENT ON EXTERNAL USAGE

Unchanged. Lives at `/p2/admin/external-usage` (see `[[project-person-enrichment]]`). Clicking a person opens a profile modal with Apollo-sourced identity, employment and contact details, labelled outbound links, and an AI read of the person. New members are auto-enriched. There is an Excel export. When Apollo cannot match a person, the flow falls back to enriching their company by email domain. **This shares `_enrich_people`'s cache with Contact Finder's chat-side name reveal.**

Two hard-won points that generalise: **validate response shape, not just HTTP status**; **version-stamp every enrichment cache** so a fixed bug can self-heal.

---

## LINKEDIN STRATEGY RESEARCHER (native, new this cycle) - NOT the external Watchtower one

**Naming collision, read this first:** there are two unrelated agents that have carried the display name "LinkedIn Strategy Researcher." This section documents the **new, native, in-repo** one (internal names: `linkedin_playbook_studio`, `lps_*`, `tracker/linkedin_playbook_store.py`, route base `/p2/b2b-agents/linkedin-strategy-researcher`). The **old, external** one - an iframe embed of a third-party AI app-builder tool at `watchtower-by-position2.vercel.app`, code not in this repo - held that exact name until it was renamed to **LinkedIn Social Researcher** and hidden from listings specifically to free the name up for this new one (commit `632a844`). See the "LinkedIn Social Researcher - Still Hidden" section further below for that one; do not conflate the two.

**What it does, in plain terms:** a user searches for a LinkedIn company page, then runs a multi-agent competitive-strategy analysis either on their own brand or on a named competitor. The report covers Company Profile, Posts, Strategy (personas, hooks/CTAs, audience detail), Content & Creative, Messaging, and a Competitive scorecard - plus a locally-computed Engagement tab (posting cadence, engagement rate, best format, since the vendor's own engagement fields are empty on every real run) and an on-demand Claude "AI Insights" synthesis. From a completed own-brand run, a user can kick off a competitor comparison; from any saved run, a strategic playbook (prioritized recommendations) can be generated.

**Vendor:** `tracker/arena_client.py` calls **Arena** (`agent.thearena.ai/api/workflows/<id>/execute`, auth via `ARENA_API_KEY` as an `X-API-Key` header). Three of Arena's six workflows are used: company search (free), the 5-agent analysis, and playbook generation (both described in code comments as billed). Responses are either whole-body JSON or an SSE stream that must be parsed and merged - the vendor's contract is otherwise undocumented, so this parsing logic was ported from a prior internal tool's TypeScript client.

**Routes** (all `/p2/b2b-agents/linkedin-strategy-researcher/...`, `@position2_required` unless noted): `GET /` (page), `GET /search`, `POST /analyze` (starts a background thread, returns `run_id`), `GET /runs/<id>/status` (polled), `GET /history`, `GET /runs/<id>` (full run, derived analytics recomputed on read), `POST /runs/<id>/insights` (backfill/regenerate AI Insights on an already-completed run without re-running the vendor workflow), `GET|POST /runs/<id>/playbook`. A legacy `/p2/b2b-agents/linkedin-playbook-studio(/...)` prefix 308-redirects to the current slug. Admin-only self-tests: `POST /p2/admin/external-usage/arena-check`, `POST .../lps-insights-check`.

**Data model** (`tracker/linkedin_playbook_store.py`, Postgres via `DATABASE_URL` - deliberately not SQLite, since this needs live per-click writes and Railway gives the app no persistent disk): `lps_runs` (id, email, parent_run_id, run_type, company_id/name/logo, status, error, summary, scorecard_score, output JSONB, timestamps) and `lps_playbooks` (id, run_id, email, mode, content JSONB), unique on `(run_id, mode)`. Every single-row read is ownership-scoped in the SQL itself (`WHERE id=%s AND email=%s`) - an explicit fix for a prior IDOR in the tool this was ported from.

**Report UI** (`templates/linkedin_playbook_studio.html`, 2086 lines): a sidebar-nav drawer with tabs (Overview, AI Insights, Engagement, Audience, Strategy, Content & Creative, Messaging, Competitive, Company, Posts), shape-aware rendering (distributions as bars, object arrays as cards, an SVG radar chart + arc gauge for the competitive scorecard) rather than a raw dump. Admins get a raw-JSON view. Print/PDF export forces light theme and lazy-loads the Playbook tab first so exports aren't blank.

**AI Insights** (`tracker/lps_enrichment.py`): one Claude call (`ANTHROPIC_MODEL`, needs `ANTHROPIC_API_KEY`) synthesizing one point of view across all five agents' output plus the locally-derived engagement metrics, under a hard rule to never state anything not traceable to the input JSON. Runs automatically at the end of every successful analysis, best-effort (failure never blocks the run from saving); for older runs it's on-demand via the `/insights` POST.

**Real bugs fixed, root causes worth remembering:**
- **Report drawer losing whole sections:** Arena's workflow emits one SSE `"final"` event *per agent*, not one for the whole run; the parser kept only the last one, silently discarding earlier agents. Also, namespace-guessing bailed on the ENTIRE response the moment any one key was already dotted. Fixed to merge all final events and namespace per-key.
- **Real production response shapes not matching assumptions:** the competitive scorecard is an array of `{metric,score}`, not a category-keyed object; hooks are `[{example,type}]` objects, not strings; `{l,v}` pairs where `v` is a sentence got coerced through `Number(v)||0` into zero-width bars; post engagement fields used guessed names instead of the vendor's real ones (`reaction_counter` etc.).
- **Vendor account lapsing, mis-read as a generic failure:** "No companies found" was collapsing six distinct failure modes into one message. Once errors carried a real `kind`, the true cause showed as the Arena workspace's **own connected LinkedIn account having lapsed** (a specific 500 body naming the missing field) - not a bad key, not a moved workflow. This is a **recurring operational risk**, not a one-time fix: it needs reconnecting at `agent.thearena.ai` periodically, and the error message now names this directly rather than suggesting "try again."
- **AI Insights never generating:** `max_tokens=2000` was routinely exceeded by a real run's synthesis reply, producing invalid/truncated JSON that silently became `None`. Fixed with `max_tokens=4096` plus an `8192` retry when `stop_reason == "max_tokens"`.
- **The vendor's own engagement fields are empty on every real run:** `tracker/lps_analytics.py` computes cadence/engagement/format-performance locally from the raw post feed instead of trusting the vendor's `contentcreativeagent.engagement.*`. Also fixed a UTF-8-decoded-as-Latin-1 mojibake bug in the vendor's post text.

**Known-open fragility:** `creativeinsightagent.*` is dead in practice (comes back empty on every real run, folded into Content & Creative). The whole SSE-parsing/namespace-guessing layer is reverse-engineered, not spec-driven - further vendor shape surprises are plausible. The AI Insights truncation retry doubles cost/latency on any reply that first hits the token cap.

**Env vars:** `ARENA_API_KEY` (missing = clean "not configured" error, not a crash), `DATABASE_URL`, `ANTHROPIC_API_KEY` (missing = insights silently never generate), `ANTHROPIC_MODEL` (opt, default `claude-sonnet-5`).

---

## GENTLE DENTAL SLOT CHECKER (new this cycle)

**What it is / audience:** "Gentle Dental" is a multi-brand dental-practice-chain client (82 locations across MA/NH/CT and others). This staff-only dashboard (`/p2/b2b-agents/gentle-dental-slot-checker`, `@position2_required`, not exposed to the client) answers, in the page's own words: *"What a new patient would actually be offered if they tried to book right now, across every location the agent checks."* A separate scraping agent crawls each location's real booking widget weekly and writes results into a Google Sheet; this feature is purely the read/visualize layer over that data. **Not listed in the `AGENTS`/`APP_AGENTS` registries at all** - it's a direct standalone route with its own hand-written card in `b2b_agents.html`, outside the usual roster mechanism.

**Routes:** `GET /p2/b2b-agents/gentle-dental-slot-checker` (page), `GET .../data` (JSON payload via `tracker.slot_checker.fetch()`, `?fresh=1` bypasses cache, relies on the platform's generic gzip hook), `GET .../insights` (AI briefing, `?fresh=1` forces regen). All `@position2_required`.

**Data model/source** (`tracker/slot_checker.py`): live source is a Google Sheet (`SLOT_CHECKER_SHEET_ID`), read via `GOOGLE_SA_JSON`, from two tabs (`LPs` = location registry, `Locations` = one row per office+service per date), reshaped to match the legacy `.xlsx` parser's expected input. **Each row is an observation, not a fact** - a rescraped location appears multiple times across days with different counts, so "current availability" means the newest observation per (practice, service), never a naive sum. TTL-cached 300s. **Fallback:** any exception on the live read (revoked share, renamed tab, network error, quota) falls back to the committed `data/slot_checker_snapshot.json` - a live read that *succeeds* with zero rows is not a failure and renders as-is. To refresh the fallback: export the sheet to `.xlsx`, run `scripts/import_slot_checker_snapshot.py <file>`, commit the resulting JSON. **Note:** that script's own docstring is currently stale (still claims the sheet can't be read live by the service account - it can, as of this feature's build; worth fixing next time this file is touched).

**AI Insights** (`tracker/slot_checker_insights.py`): one Claude call synthesizing the dashboard's own derived numbers into a headline, synthesis, top actions, and a coverage note, under the same "never state anything not in the given JSON" rule as LPS's. **On-demand, not automatic** - triggered by opening the Insights panel, 1-hour TTL-cached and invalidated early if the underlying snapshot's `generated_at` changes, so it costs roughly one Claude call per cache miss, not per view. Degrades cleanly without `ANTHROPIC_API_KEY`. **`probe()` self-test exists but has no admin route wired up** (unlike LPS's `lps-insights-check`) - currently unreachable except by calling it directly.

**UI** (`templates/gentle_dental_slot_checker.html`, `static/css/gentle_dental_slot_checker.css`): 4 KPI stat tiles, 3 summary cards (availability by day, capacity by state, brand mix), a main card with 4 tabs (Locations - default, sortable/searchable table with a centered drawer; Calendar - heatmap; Needs Attention - alerts; Services), a split filters/sort-export toolbar, and a compact AI Insights card at the page bottom.

**Calendar heatmap - current, live color scheme (changed today, don't assume an older version):** 6 discrete steps by open-slot count (`RAMP_BREAKS=[2,5,10,20]`): 0 / 1-2 / 3-5 / 6-10 / 11-20 / 21+. **Vivid red-to-green** by hue step (not opacity-faded single-hue): dark theme orange `#f97316` -> amber `#f59e0b` -> yellow `#eab308` -> lime `#84cc16` -> green `#22c55e`; light theme uses deeper equivalents. The **zero-slots cell is not part of the color ramp** - a transparent cell with a red-tinted hairline border and a red dash through the middle, deliberately signaling "needs action" rather than "no data." **This ramp was churned same-day**: it started black-to-green (opacity-faded single teal hue), was changed to this vivid red-to-green version, briefly tried a desaturated/pastel version, and was reverted back to the vivid version per explicit user feedback ("this looks terrible"). The vivid version above is the correct, current, live state - do not reintroduce the pastel one without being asked.

**Real bugs fixed:** a white flash on fast scroll (the page's `background:` shorthand set only a gradient image, silently zeroing the longhand `background-color`, so overscroll painted the browser's default white canvas in both themes - fixed with an explicit `background-color` declared as its own line after the shorthand); a hover tooltip rendering invisibly behind the centered drawer (a stale `z-index`, fixed plus a `click` handler added since hover never fires on touch).

**Known-open items:** the live-sheet-reliability caveat above; the stale docstring in the import script; the missing admin self-test route for AI Insights.

**Env vars:** `GOOGLE_SA_JSON`, `ANTHROPIC_API_KEY` (opt), `ANTHROPIC_MODEL` (opt, default `claude-sonnet-5`).

---

## SOCIAL CREATIVE INTELLIGENCE ANALYST (new this cycle, largest net-new agent)

**What it does, end to end:** given a company name/URL, resolves its handles across **Instagram, LinkedIn, X, TikTok, YouTube, Facebook**, collects recent organic posts, runs **every image and video through Claude vision** (plus Whisper audio transcription for spoken dialogue in video) to describe what the creative actually shows - not just captions or engagement counts - then a Claude synthesis pass writes a cited, per-platform + cross-platform report on content patterns, messaging/strategy, and what correlates with engagement. Internal, staff-only (`@position2_required`), route base `/p2/b2b-agents/social-creative-intelligence`. Built and shipped across three explicit phases (Instagram+YouTube, then Facebook/TikTok/X/LinkedIn, then classification+synthesis+report UI+audio) plus many follow-up fixes, all in this cycle.

**Pipeline** (`tracker/sci_pipeline.py`, one daemon thread per run, platforms processed sequentially, each in its own try/except so one platform's failure never blanks another's): **Identify** (`sci_identify.py` - one Claude call using the `web_search` tool, versioned-fallback across three dated tool-type strings since Anthropic sunsets old ones server-side, streamed via `messages.stream()` not a blocking call since a 6-platform/15-search lookup routinely exceeds a short timeout; refuses to guess - only `high`/`medium` confidence handles are trusted downstream) -> **Collect** (per-platform adapters, see vendors below) -> **Creative analysis** (`sci_vision.py`/`sci_video.py`/`sci_audio.py` - per-post, staged so one slow/failed video never blocks the rest) -> **Classify + Synthesize** (`sci_classify.py` pure aggregation, `sci_synthesize.py` one Claude call producing cited claims tied to real post ids).

**Vendors, two now, additive not exclusive:**
- **Apify** (`tracker/apify_transport.py` + per-platform `sci_source_<platform>.py` adapters) - actor-based scraping for Facebook/TikTok/X, and a fallback path for Instagram. `APIFY_API_TOKEN` **has never actually been set in production** - confirmed via a dedicated Apify pricing/tier research pass this cycle finding zero existing spend anywhere. LinkedIn is **feature-flagged off by design** via Apify (`SCI_APIFY_LINKEDIN_ACTOR_ID` has no default; unset means the pipeline never even calls Apify for it) - LinkedIn is the platform most exposed to scraping-detection/ToS enforcement.
- **Unipile** (`tracker/unipile_client.py` + `tracker/unipile_transport.py` + `sci_source_linkedin_unipile.py`/`sci_source_instagram_unipile.py`, added this session) - a fundamentally different model: connects one real, authenticated account per platform via a hosted-auth link a human clicks through, then acts through that account. LinkedIn and Instagram now try a connected Unipile account **first**, falling back to the pre-existing Apify path unchanged if none is connected - purely additive, zero behavior change wherever Unipile isn't configured. **No local table tracks which account is connected** (deliberately - `unipile_client.list_accounts()` is queried live every time, the same reasoning that prevents Arena's LinkedIn-connection-lapsing problem, described above in the LPS section, from being invisible here too). New `sci_platform_runs.source_vendor` column + a "via Unipile"/"via Apify" badge per platform section in the report makes which vendor served which platform visible.

**Data model** (`tracker/sci_store.py`, Postgres): `sci_runs` (one row per analysis), `sci_platform_runs` (one row per platform per run, own status so one platform failing doesn't blank the others; `source_vendor` column added this session), `sci_posts` (one row per scraped post, creative_analysis JSONB attached once complete), `sci_spend_log` (schema exists, not yet enforced against a cap - future phase).

**Report UI** (`templates/social_creative_intelligence.html`): an input form with live company-search-as-you-type (native, Apollo-backed via `sci_company_search.py` - deliberately NOT Arena, since Arena's own workspace periodically loses its connected LinkedIn account, the exact fragility documented in the LPS section above), a run-history list, and a centered-modal report drawer with a coverage strip, per-platform sections (format-mix bar, theme chips, top-performing-post cards with real thumbnails, cited claims linking to real posts), a mechanical overview dashboard (stat tiles with a staggered entrance animation, a platform post-count bar chart), and a cross-platform synthesis section with a hero treatment (pulsing "live" dot, collapsible evidence). **Admin-only "Data sources" panel** on the page itself shows live Unipile connection status per platform and a "Connect" button generating a hosted-auth link - nothing in this codebase can complete that login on someone's behalf.

**Real bugs fixed, root causes worth remembering:**
- **Every platform failing identification at once, twice, back to back, both in `sci_identify.py`:** first, a hardcoded single dated `web_search` tool version got sunset server-side (fixed with an ordered fallback list + a self-healing process-cache); then, once that bare-except stopped swallowing the real exception, the SAME failure shape recurred but now showing the real cause - `anthropic.APITimeoutError`, since a 6-platform/15-search lookup routinely exceeds a 90s blocking-call timeout (fixed by switching to `messages.stream()` plus a 280s backstop). **The lesson: a bare `except Exception` collapsing every failure into one generic string is what made two structurally different bugs look identical** - fixing the error-swallowing was as important as fixing either bug.
- **YouTube video creative_analysis null for ~100% of posts:** `yt-dlp` frame extraction gets blocked by YouTube's bot detection from datacenter IPs (Railway included) - a systemic failure, not flakiness. Fixed by falling back to the platform's own free static thumbnail (already returned by the Data API, previously discarded) whenever frame extraction returns nothing.
- **Messaging/strategy depth was a schema gap, not missing AI:** the vision schema only ever asked for pure visual description - no field existed for messaging/tone/CTA/hook, so synthesis had nothing to draw on even in principle. Fixed by adding those fields to the vision schema and requiring a distinct `messaging_and_strategy` narrative in synthesis, tied to what actually drove engagement.
- **Report was "wall of text" with broken video thumbnails:** `media_urls[0]` for a video post is the playable file, never a displayable image - every `<img>` silently 404'd via its own onerror handler. Fixed with a client-side `postThumbnail()` resolver reading each platform's real cover image out of the post's raw payload, plus converting synthesis prose from single paragraphs to short bullet lists with citations moved out of inline text into real linked post cards.
- **A copy-pasted broken-image-fallback bug, 4 times in one file:** every `onerror` handler called `.remove()` before `.closest(...)`, so `closest()` always failed on the now-detached node - silent (no visible error) because nothing had ever tested a real reachable-but-broken image URL until logos were added. Fixed by reordering all four call sites.

**Known-open items:** `UNIPILE_DSN` is not yet set to the account's real dashboard-issued host - the supplied key currently fails auth (`401`) against the shared gateway; the exact live v2 posts-endpoint response shape is unconfirmed against a real connected account (built defensively, flagged in the client's own docstring); no LinkedIn/Instagram account has actually been connected through Unipile yet (a human still needs to click the hosted-auth link); `APIFY_API_TOKEN` still not set, so Facebook/TikTok/X collection is currently fully inert.

**Env vars:** `APIFY_API_TOKEN` (opt, NOT SET), `YOUTUBE_API_KEY` (opt, NOT SET), `SCI_APIFY_<PLATFORM>_ACTOR_ID` x5 (opt overrides; LinkedIn's has no default, load-bearing differently from the rest), `UNIPILE_API_KEY` (**set**, but not yet authenticating - see above), `UNIPILE_DSN` (opt, NOT SET, almost certainly required). Reuses the platform's existing `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` and `OPENAI_API_KEY` (Whisper) - no new AI vendor account needed.

---

## THE NORTHSTAR ABM SIGNAL TRACKER (complete, maintenance mode, unchanged)

**Note the naming overlap with the general "ABM Signal Tracker" product above** - this is NorthStar's own specific client-portal instance, coincidentally identical display name, different code paths.

- **All 35 companies researched across 7 batches; 71 curated signals** (52 HIGH / 16 MEDIUM / 3 LOW).
- Data in SQLite (`data/tracker_northstar.db`), seeded from `data/northstar_signals_manual.json` via `seed_northstar_signals.py --prune` (**always with `--prune`**, the JSON is the source of truth).
- **`_quality_bar` inside that JSON is the living curation policy** - permanent 6-month admission cutoff by `signal_date`. See `[[feedback-signal-relevance-bar]]`.
- **"Creative Hiring" displays as "Anesthesiologists" for NorthStar only**, via a per-account override. Reuse this pattern; never globally rename a shared category.
- `reports/dashboard*.html` are **built artifacts committed to git**. Never hand-edit them.

---

## SURFACE 4 - CLIENT PORTALS

Per-client co-branded front door at `/<client-slug>`. Currently one client: `northstaranesthesia`. Unchanged.

- **`CLIENTS` registry** entry fields: `slug`, `name`, `short`, `website`, `logo`, `domains`, `accent`/`accent2`, `tagline`, `blurb`, `agents` (ordered `APP_AGENTS` slugs), `dashboards`, `linkedin_sheet`, `external_tools`.
- NorthStar: `domains=["northstaranesthesia.com"]`, `accent="#5b9dff"`. Its agents list still references **LinkedIn Social Researcher** (the old external tool, currently hidden - see below), not the new native LinkedIn Strategy Researcher.
- **Three agent types:** SERP-connected (`seo_slug`), dashboard-backed (`is_dashboard`), external-tool (`is_external`).
- `_client_agent_view(slug, client)`: **always pass `client`**. Omitting it silently resolves `connected=False` for an external-tool-only agent.

---

## LINKEDIN SOCIAL RESEARCHER - STILL HIDDEN, NOT DELETED (the OLD external agent - see the NEW native LinkedIn Strategy Researcher section above)

**Pulled from every listing on 2026-08-14; renamed 2026-08-20 to LinkedIn Social Researcher (slug `linkedin-social-researcher`) when its old name/slug moved to the brand-new native agent documented above.** Still hidden as of this cycle, no restore request received. Nothing underneath was deleted: `/p2/b2b-agents/linkedin-social-researcher` still resolves, the watchtower tool it embeds still loads, `APP_AGENTS_BY_SLUG` still holds the full entry, NorthStar's own `agents` list is untouched.

**To restore, in this exact order:**
1. Empty `HIDDEN_AGENT_SLUGS` in `app.py` (currently `{"linkedin-social-researcher"}` - drives `/app`'s main grid, sidebar, and every client portal).
2. Un-comment the card in `templates/b2b_agents.html` **and** its Ctrl+K command-palette entry (hand-written HTML, does NOT derive from `HIDDEN_AGENT_SLUGS`).
3. Bump `templates/hub.html`'s B2B card counts and "by the numbers" band (re-verify the arithmetic in the file's own comments, don't assume a number - it has moved multiple times).
4. Restore `templates/context.html`'s mentions.
5. `tests/test_hidden_agent_withdrawal.py` asserts the hidden state - flip or delete it at the same time.

**How to apply:** see `[[project-lsr-hidden]]` for the full restore checklist with test names.

---

## SEO SUITE (unchanged)

**16 tools** on `/p2/seo` (staff-only). Includes "Competitor Analysis" (real SEMrush-backed data, `_SEO_TOOLS_FALLBACK` is the actual source of truth since the SERP app's manifest fetch always fails in practice). A **separate, unrelated, dormant `/app` placeholder** happens to share the identical display name and slug - deliberately unconnected, since the real tool's client picker has no per-member scoping and opening it would be a cross-client data leak.

---

## THE EXTERNAL-TOOL PATTERN

An agent whose entire backend lives on a third-party AI app-builder platform we have no access to; we get a public URL.

1. Confirm no `X-Frame-Options`/CSP `frame-ancestors` blocks iframing.
2. Add it to `APP_AGENTS` with no `seo_slug`.
3. Add its slug to the client's `agents` list and its URL to `external_tools` (client portal), or add a small route rendering `templates/embed.html` (internal).
4. `client_embed.html` / `embed.html` iframe it; the address bar shows OUR path.
5. **Metering requires the external tool's cooperation** via `postMessage`. Deployed and working for LinkedIn Social Researcher (the old external tool, currently hidden from listings, but the mechanism itself is untouched).
6. **Any prompt written to be pasted into that other platform must be self-contained** and describe only that tool's own observable behaviour, never our internal routes, slugs, or architecture.

---

## LINKEDIN INTELLIGENCE (internal + per-client, multi-sheet, unchanged)

Route `/p2/b2b-agents/linkedin-intelligence`. Renders `templates/linkedin_scraper.html`; all content drawn client-side by `static/js/linkedin.js`. One row per person x post engagement, header-mapped, per-sheet caches so internal and each client portal read independent sheets.

**Do not confuse** this (your own engagement data from a Sheet) with: **LinkedIn Strategy Researcher** (the new NATIVE Arena-backed competitive-strategy agent, its own section above), **LinkedIn Social Researcher** (the OLD external tool, currently hidden), the ABM Signal Tracker's own News Mention/Partnership categories, Social Creative Intelligence Analyst (creative/vision analysis of organic posts across 6 platforms, an entirely different agent), or Job Change Alert (new-role detections from Slack).

---

## ADMIN ANALYTICS (all `@admin_required`, each has a `.../data` JSON endpoint, unchanged)

- **Internal Usage** `/p2/admin/internal-usage`, **External Usage** `/p2/admin/external-usage` (reads `Member Signins`, rich People table, Apollo profile modal, plus the SCI/Arena/Apollo/LPS-insights admin self-test check buttons), **Client Usage** `/p2/admin/client-usage`, **Anonymous Traffic** `/p2/admin/anonymous-traffic`, **Public Page Analytics** `/p2/admin/public-page-analytics`, **Public Agent Usage** `/p2/admin/public-agent-usage`, **Access Requests** `/p2/admin/access-requests`.

**Sheets read performance rule:** warm the IP cache concurrently, do concurrent per-thread `values().get()`, cache ~300s. **Do NOT use `batchGet`**, it returns empty in prod. See `[[feedback-sheets-read-performance]]`.

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                ← Flask server (~17,221 lines, 185 route decorator lines / 211
│                            registered rules): auth (3 decorators + client gate), all 4
│                            surfaces, AGENTS/APP_AGENTS/SIGNALS/INDUSTRIES/CLIENTS/ACCOUNTS
│                            registries, HIDDEN_AGENT_SLUGS, OpenAI (Vimi x2 + Contact
│                            Finder's chain) + Anthropic (Contact Finder cross-check, LPS/
│                            Slot Checker/SCI AI layers, SCI vision/synthesis), Contact
│                            Finder's cpi_* routes, Job Change Alert's routes, LPS's
│                            linkedin_playbook_studio* routes, Gentle Dental's gentle_dental_
│                            slot_checker* routes, SCI's social_creative_intelligence* routes
│                            + unipile-check/-connect admin routes, marketing routes, /api/*,
│                            /app/*, /p2/*, client-portal routes, Postgres history.
├── tracker/apollo_client.py        ← Apollo API client, Contact Finder's search/filter core
├── tracker/arena_client.py         ← Arena vendor client, backs LPS's own search/analysis/
│                                      playbook AND (deliberately not) SCI's company search
├── tracker/linkedin_playbook_store.py, tracker/lps_enrichment.py, tracker/lps_analytics.py
│                                      ← LPS's Postgres store, AI Insights, local engagement
│                                      computation (new this cycle)
├── tracker/slot_checker.py, tracker/slot_checker_insights.py
│                                      ← Gentle Dental's Sheets-backed data + AI Insights
│                                      (new this cycle)
├── tracker/sci_*.py (identify, pipeline, store, vision, video, audio, classify, synthesize,
│       youtube_client, company_search, source_instagram/facebook/tiktok/x/linkedin,
│       source_instagram_unipile/linkedin_unipile) + tracker/apify_transport.py +
│       tracker/unipile_client.py + tracker/unipile_transport.py
│                                      ← Social Creative Intelligence Analyst, both vendors
│                                      (new this cycle; Unipile added this session)
├── tracker/job_change_parser.py, tracker/job_change_store.py ← Job Change Alert
├── scripts/sync_job_change_alerts.py, scripts/import_job_change_tracked_snapshot.py,
│       scripts/import_slot_checker_snapshot.py ← subprocess-only scripts, never imported
├── tests/                ← 94 files, 2,136 tests. test_cpi_*.py, test_job_change_*.py,
│                            test_sci_*.py, test_unipile_*.py, one per audit/feature.
├── visitor_intelligence/ ← de-anon engine: resolver.py, pipeline.py, identity_graph.py.
├── tracker/              ← signal pipeline pkg (news_client, news_relevance, signal_score,
│                            dashboard_builder [build_dashboard(), takes hiring_opts],
│                            csv_loader, snapshot_store, sheets_client)
├── main.py               ← weekly orchestrator (Healthcare) -> data/tracker.db
├── build_northstar_dashboard.py, build_csg_dashboard.py
├── seed_northstar_signals.py   ← always run with --prune
├── ad_intelligence/      ← built React app served by Flask
├── static/
│   ├── css/ (ds-tokens, ds-components, gtm, hub, seo, linkedin, admin, aurora-app,
│   │        grid-tokens, client-portal, company_people_intelligence, job_change_alert,
│   │        linkedin_playbook_studio, gentle_dental_slot_checker, social_creative_
│   │        intelligence.css - the last 4 new this cycle)
│   └── js/ (theme, linkedin, visitor_track, pfx_bg, aurora, anonymous_visitors,
│           company_people_intelligence.js)
├── templates/
│   ├── agents.html          ← THE SINGLE SHARED MARKETING TEMPLATE, {% if page %} variants
│   ├── app.html, app_base.html, app_embed.html, app_history*.html, app_settings.html
│   ├── hub.html, b2b_agents.html, seo.html, accounts.html, embed.html, context.html, 403.html
│   ├── company_people_intelligence.html   ← Contact Finder
│   ├── job_change_alert.html, linkedin_playbook_studio.html, gentle_dental_slot_checker.html,
│   │       social_creative_intelligence.html   ← all new this cycle
│   ├── linkedin_scraper.html   ← serves BOTH internal and client LinkedIn dashboards
│   ├── admin_usage.html, admin_visitors.html, admin_members.html, admin_agent_runs.html,
│   │        admin_requests.html, admin_external_usage.html, admin_client_usage.html,
│   │        admin_client_detail.html
│   ├── _admin_menu.html     ← the ONE shared internal admin dropdown
│   ├── client_*.html        ← client-portal shell, home, agent detail, embed, history, denied
│   └── ppc_chat_widget.html ← shared Vimi chat widget (internal only)
├── data/job_change_alerts.db, data/job_change_alerts_manual.json,
│       data/job_change_tracked_snapshot.json, data/slot_checker_snapshot.json
│       ← new this cycle
├── reports/          ← dashboard*.html: BUILT ARTIFACTS, committed, never hand-edited
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml,
        sync-job-change-alerts.yml
```

### Deploy + data model

- **Code/UI** push to `main` -> Railway redeploys (~60-100s). No hot reload locally.
- **Google Sheets** is the primary store for internal analytics AND for Gentle Dental Slot Checker's live availability data. Job Change Alert's tracked-roster sheet is currently blocked (Workspace DLP) and falls back to a committed snapshot.
- **Postgres** (`DATABASE_URL`): `agent_run_history`, `cpi_search_history`, Contact Finder's persistent caches, `lps_runs`/`lps_playbooks` (LPS), `sci_runs`/`sci_platform_runs`/`sci_posts`/`sci_spend_log` (SCI).
- **SQLite** (committed): `data/tracker.db` (Healthcare), `data/tracker_csg_v2.db` (CSG), `data/tracker_northstar.db` (NorthStar), `data/job_change_alerts.db`. **Gitignored, real PII, NEVER commit: `data/identity_graph.db`.**

---

## VIMI, DE-ANON, STITCHING, AND THE OTHER SURFACES (unchanged)

- **Vimi** (label **GTM**): two backends, `/api/ppc-chat` (widget, `@position2_required`) and `/api/vimi-chat/<account_id>`. Never mix Healthcare and CSG in one answer.
- **Anonymous Visitors / de-anon:** `visitor_intelligence/`. Company-level multi-signal IP resolution, connection-type hard gate, noisy-OR confidence, Apollo enrichment, 0-100 intent. Person-level: persistent SQLite identity graph. **Never fabricates a person.**
- **`p2_vid` stitching:** Page Views and both login tabs carry a visitor-id column.
- **Surface 2, `/app`:** shell `app_base.html`, `APP_AGENTS` cards (minus `HIDDEN_AGENT_SLUGS`), a few wired to live seo-apps tools plus LinkedIn Social Researcher (currently hidden), the rest request-access-only.
- **Surface 1, public site:** one template `agents.html`, `{% if page %}` chain.
- **Surface 3, `/p2/*`:** `/p2/hub`, `/p2/b2b-agents` (Contact Finder, Job Change Alert, LinkedIn Strategy Researcher [native], Gentle Dental Slot Checker, Social Creative Intelligence Analyst, sentiment-pulse MOCK data, ad-intelligence React app, linkedin-intelligence, linkedin-social-researcher), `/p2/seo` + tools (16), `/p2/abm-signal-tracker/accounts` + signal trackers, `/p2/playbook`, admin dashboards.

**Agent roster hazard, worse than ever now:** the roster exists in **three independent lists** (`AGENTS`, `APP_AGENTS`, and a JS array in `templates/context.html`), plus the internal SEO Suite tools list, plus `HIDDEN_AGENT_SLUGS`, plus now FOUR agents this cycle alone (Job Change Alert, LPS, Gentle Dental, SCI) each with their own hand-written `b2b_agents.html` card and command-palette entry that derives from none of the above. **Nothing derives one from another.**

---

## BRANDING + THEME (unchanged)

"Arena" mark: bright-green hexagon `#55be8c` + steel-blue + dark-green petals = 6-point star. `theme.js` (`localStorage['p2-theme']`, default dark). Hard sign-out: `/logout` sends `Clear-Site-Data` + explicit cookie deletion. Bricolage Grotesque is the public body font.

---

## ENVIRONMENT VARIABLES

**Railway:** `DATABASE_URL`, `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_SA_JSON` (Sheets read - internal analytics AND Gentle Dental Slot Checker), `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`/`FLASK_SECRET_KEY` (confirmed set to a strong value), `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN` (post-only scope confirmed; read-scope for `#job_change_alert_apollo` history unverified), `SLACK_CHANNEL_ID`, `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), **`APOLLO_API_KEY` (Contact Finder + de-anon + person enrichment + SCI's own company search all depend on this one shared key/pool)**, **`ARENA_API_KEY` (LinkedIn Strategy Researcher's entire vendor backend depends on this - was missing from prior context-file revisions despite being load-bearing since before v25)**, **`ANTHROPIC_API_KEY` (shared across MANY features now: Contact Finder's Claude cross-check, LPS's AI Insights, Gentle Dental's AI Insights, and ALL of SCI's identify/vision/synthesis calls - verify current Railway status, it was unset as of Contact Finder's cross-check shipping but SCI's identify step has since been confirmed working in production, meaning it likely IS set now; don't assume either way without checking)**, `ANTHROPIC_MODEL` (opt, defaults to `claude-sonnet-5`), `VI_ENRICH_ON_VIEW` (opt), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt), `SMTP_*` (unusable on Railway).

**GitHub Actions secrets (separate store from Railway):** `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`, and `SLACK_BOT_TOKEN` needs adding here too for the Job Change Alert sync workflow.

**Social Creative Intelligence Analyst:** `APIFY_API_TOKEN` (opt, NOT SET - Facebook/TikTok/X collection all degrade to scrape_failed without it; Instagram falls back to this too but tries Unipile first), `YOUTUBE_API_KEY` (opt, NOT SET), `SCI_APIFY_INSTAGRAM_ACTOR_ID` / `SCI_APIFY_FACEBOOK_ACTOR_ID` / `SCI_APIFY_TIKTOK_ACTOR_ID` / `SCI_APIFY_X_ACTOR_ID` (opt overrides), `SCI_APIFY_LINKEDIN_ACTOR_ID` (opt, no default - LinkedIn stays fully disabled via Apify until set).

**Social Creative Intelligence Analyst - Unipile (this session):** `UNIPILE_API_KEY` (**set on Railway**, but as of this entry authenticates against NEITHER the shared `api.unipile.com` gateway nor any account-specific host yet configured - a live curl test got `401 invalid_credentials`), `UNIPILE_DSN` (opt, NOT SET - Unipile issues each account its own dashboard-issued host, `https://apiNN.unipile.com:PORT`, and this almost certainly needs setting explicitly). No LinkedIn/Instagram account is connected yet either way - both platforms behave exactly as they did before Unipile existed until one is (via the admin Data Sources panel's hosted-auth connect flow).

---

## HOW TO WORK ON THIS (proven-safe workflow)

1. **Clone fresh into the bash sandbox each session.** Sandbox network: `git` over `github.com` works; most external APIs are blocked, though outbound HTTPS to arbitrary hosts (e.g. `curl`-ing a vendor API directly to verify auth/routes) has worked when tried. WebSearch/WebFetch work.
2. Edit via file-edit tools or Python string-replace scripts (assert exactly-one match).
3. **Validate before every push, in this order:**
   - `PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q` (must be 2,136+ passing)
   - **mutation-test the actual fix** with genuinely distinguishing data, break each fixed line, confirm a test dies, restore
   - `python3 -c "import ast; ast.parse(open('app.py').read())"` (and any other changed `.py`)
   - import the app to catch route collisions
   - `node --check` any changed JS bundle (or extracted inline `<script>` blocks)
   - **for a UI-only change, live-verify via a temporary preview route or standalone preview script (monkeypatch the store layer, real Flask app, scratch port), screenshot both themes, confirm zero permanent diff before committing**
   - no em dashes on added lines
4. **Push once validated, without asking each time** - standing instruction. Still report what shipped, including the commit hash. Push URL = `https://<TOKEN>@github.com/ai-positon2/intelligence-platform`. **Redact the token in ALL visible output.** **The user pastes a fresh classic PAT each session and it must be rotated afterward - flag this every single push.**
5. **Never use an em dash in any written copy**, anywhere. Use commas, colons, periods, parentheses.
6. **A shared signal/category/label reused across client accounts gets a per-account override parameter, never a global rename.**
7. **When a user reports something is "still not fixed", re-measure the EXACT reported symptom empirically.**
8. **Never commit `data/identity_graph.db`.** Never put personal or sensitive data in URL parameters or query strings.
9. **When hiding (not deleting) a listed agent, check for hand-written HTML in addition to any registry/set-based filter.**
10. **When a persisted spreadsheet cannot be shared with a service account due to an org sharing policy, a manual, re-runnable `.xlsx` import script the fetcher falls back to (per-list, not all-or-nothing) is a legitimate stopgap** - self-heals to live data the moment real access is fixed.
11. **A bare `except Exception` that collapses every failure into one generic message hides the fact that two structurally different bugs can produce the identical symptom** - this cost a full extra diagnosis round for SCI's identify step this cycle. Prefer typed/classified errors from day one on any new vendor integration.
12. **When a design change (e.g. a color palette) gets reverted by explicit user feedback ("this looks terrible"), revert to the exact prior state via `git revert`, don't hand-retype values** - guarantees byte-for-byte restoration with zero transcription risk.
13. **A vendor's OWN documentation can lag its live API** - Unipile's docs described `/api/v1/...` paths; the live API was already on `/v2/...`. Confirm exact endpoint behavior against a real response (even an authenticated-but-error one, since route-exists-vs-doesn't is often distinguishable from the error type alone) before hardcoding a vendor's documented path as gospel.

### Gotchas (unchanged, still true)

- `templates/context.html` (Playbook), `templates/linkedin_scraper.html` (LinkedIn Intelligence), and the entire `company_people_intelligence` naming for Contact Finder are filename remnants of renamed features.
- The Contact Finder JS bundle is wrapped in an **IIFE**: only `window.cpi*` functions are reachable externally.
- `admin.css` loads last and overrides inline admin CSS. `aurora-app.css` (shared page chrome) loads AFTER a page's own stylesheet on several pages.
- A flex item that must shrink below its content needs its own `min-width:0`.
- Never put `{{`, `{%` or `{#` inside `<style>`/`<script>`.
- Python's `csv.writer` default `lineterminator` is `\r\n` regardless of how the file was opened.
- Flask's `render_template` caches compiled templates process-wide - a template edit needs a dev-server restart to show up locally.
- macOS sandbox has no `timeout` command; `zsh` does not word-split unquoted variables.

---

## OPEN ITEMS / TODO

1. **Rotate the GitHub token.** Pasted into chat each session; flag every session.
2. **Restore LinkedIn Social Researcher** (the old external tool) to the listings when the owner asks - checklist above and in `[[project-lsr-hidden]]`.
3. **Verify `ANTHROPIC_API_KEY`'s current Railway status** - genuinely unclear as of this writing whether it's set (SCI's identify step works in production, suggesting yes; Contact Finder's cross-check shipped when it was unset). Check directly before assuming either way; multiple features now depend on it.
4. **Set `UNIPILE_DSN`** to the account's real dashboard-issued host, and **connect a real LinkedIn/Instagram account** through the SCI admin Data Sources panel's hosted-auth flow - Unipile is otherwise fully inert.
5. **Set `APIFY_API_TOKEN`** (never has been) - Facebook/TikTok/X collection for SCI is fully inert without it.
6. **Fix `SLACK_BOT_TOKEN`'s scope/membership for `#job_change_alert_apollo`**, and add it as a GitHub Actions repo secret.
7. **Fix the Job Change Alert tracked-roster Google Sheet's sharing policy block.**
8. **Fix `scripts/import_slot_checker_snapshot.py`'s stale docstring** (claims the live sheet can't be read - it can, as of this feature's build).
9. **Wire an admin self-test route for `slot_checker_insights.probe()`**, matching the pattern every other AI-layer feature already has.
10. **Contact Finder's chat path has never run against live OpenAI + Apollo keys end to end.**
11. **Contact Finder residuals:** 120-row history cap truncates paged searches; zero-result searches never saved to history; `_cpi_probe_company_free` guesses only `.com`.
12. **Contact Finder's external client launch** is planned but not scheduled - the shared-credit-pool/`/credits`-aggregate exposure needs a decision once real external auth is designed.
13. **Hardcoded counts still in the codebase:** `ACCOUNTS["healthcare"]["description"]`'s "1,251", four places in `templates/agents.html`.
14. **Signal refresh secrets (blocking Healthcare refresh):** set GitHub Actions `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`.
15. **Agent roster will drift again**, now across even more mechanisms than v25 flagged. Consider deriving them if the roster changes materially again.
16. **Fully connect the `/app` "Competitor Analysis" placeholder** once the live SEO Studio tool's per-client data scoping is addressed.
17. **NorthStar client-side portal adoption is minimal.** A relationship conversation, not a code fix.
18. **`data/identity_graph.db` is on Railway's ephemeral disk.** Move to a persistent volume or Postgres.
19. **Cold-visitor identification** needs a licensed identity feed. Plug point ready.
20. **ABM Signal Tracker maintenance mode:** periodically prune `data/northstar_signals_manual.json` by `signal_date` and re-run `seed_northstar_signals.py --prune`.
21. **An open, unverified accuracy question for Job Change Alert:** a third-party Coresignal-backed skill (Swan) reportedly catches job changes Apollo's own alert misses.
22. **LPS's Arena-connected LinkedIn account will lapse again** - this is a recurring operational risk (see the LPS section above), not a one-time fix; the error message now names it directly when it happens.
23. **SCI's exact live Unipile posts-endpoint response shape needs confirming** against a real connected account once one exists - built defensively against Unipile's own (confirmed-stale) documentation.
24. **Advisory security/design audit (do not start without an explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID`, cookie flags, HSTS/security headers, CSRF, rate limiting, SSRF/`X-Forwarded-For` hardening; CSS token convergence, accessibility.

---

## COMPETITOR / ROADMAP (recorded, not built, unchanged)

Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala. Gaps: co-op topic intent, review-site intent, technographic change, champion job-change (partially closed by Job Change Alert, scoped to new-role-only), hiring-surge, earnings/10-K mining, event attendance, layoffs, PLG usage. Differentiators: generative-search/AI-answer visibility + agency execution + first-party web de-anon with a real engine + a working, deeply-audited (13 rounds) Apollo contact-finding surface with honest credit accounting + a live, Slack-sourced job-change detection feed with honest scope disclosure + a native LinkedIn competitive-strategy agent + **real Claude-vision creative analysis of organic social content across 6 platforms, not just metadata/metrics** (Social Creative Intelligence Analyst) - genuinely uncommon among the listed competitors, none of which look at the actual pixels of a competitor's creative.
