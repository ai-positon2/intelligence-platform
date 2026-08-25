# Intelligence by Position2 - Full Context (v25 - August 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v25 supersedes all earlier context files (v1-v24)** - older versions are stale; ignore any pasted copy, and if `CONTEXT_FOR_NEW_CHAT_V24.md` (or older) still exists in the repo root, delete it as part of landing this file per the standing one-canonical-file convention.

**Latest `main` HEAD at the end of this cycle: `fbdb21f`** (always `git pull` to confirm; Railway auto-deploys every push). `app.py` is **16,465 lines / 167 `@app.route` decorators** (183 total registered URL rules including loop-registered `add_url_rule` families), up from 15,733 lines / 147 routes at v24. The test suite is now **61 files, 1,562 tests, all passing** (`PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q`), up from 48 files / 1,434 tests at v24. Contact Finder's own JS bundle is now **3,035 lines** (was 2,995).

---

## WHAT V25 ADDS ON TOP OF V24 (this cycle's work)

Six separate threads of work, in order:

1. **A live user bug report and a pre-launch security/correctness audit of Contact Finder, in preparation for opening it to external paying clients "soon."** Fixed a real filter-crashing bug (numbers over 2^31-1 in a funding filter 422'd the whole search - `0642c95`), fixed six more issues from five parallel background investigations spanning billing, filter accuracy, cross-account access, and abuse limits (`2c9b83e`), and fixed a live-reported "Fill filters" bug where any open-ended employee-count query ("500+ employees") always snapped to the top bucket (5,001+) and echoed the user's own role wording into a literal-text keyword filter that guaranteed zero results (`6c10403`).
2. **Claude added as a second opinion on the NLP intent parser**, on explicit user request ("I also have a claude API key, use it if you want") - a critique-and-correct pass over the same OpenAI extraction that missed the Fill-filters bug above, wired into both Fill-filters and chat (`3256bac`). **Ships inert:** requires `ANTHROPIC_API_KEY` on Railway, which is not yet set - this is a real, actionable near-term TODO, not background flavor text.
3. **"Company Signal Tracker" / "Signal Tracker" renamed to "ABM Signal Tracker" everywhere - display text first (`280bb6c`), then real URLs a session later (`12c90cb`) - plus a card-grid layout bug fixed on the account picker (`f45edf1`).** See the dedicated section below; this product is distinct from the NorthStar-specific "ABM Signal Tracker" instance further down this document, which happens to now share the same display name after the rename.
4. **Job Change Alert shipped as an entirely new, real, live feature on P2 Intelligence** - the last "Coming soon" placeholder card on `/p2/b2b-agents` is now a full dashboard, sourced from Slack + a tracked-contacts/companies roster, built and then iterated on visually across roughly fifteen follow-up passes in the same continuous thread of work. This is the single largest addition since v24 and has its own dedicated section below.
5. A build-artifact refresh commit (`461345e`, `dff08df` - Ad Intelligence rebuild + Signal Tracker dashboard data refresh, both routine scheduled/CI jobs, not hand-authored changes).
6. **This context-file refresh itself** (V24 -> V25), on explicit request.

**The recurring defect class Contact Finder audits keep finding, restated once more because this cycle added two more instances: a surface asserting something its data does not support, or a value clamped/coerced with no signal that it happened.** The funding-ceiling crash (a validation gap wearing an "Apollo is down" costume) and the employee-bucket mis-snap (silently substituting a wildly wrong bucket for the one actually requested) are both this same family. Read the Contact Finder section below in full before touching search/filter code.

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 = a B2B digital-marketing agency: SEO/organic, performance/paid media, paid social, content, brand/website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, anesthesiologist/creative hiring, news, and now **new-job-detected alerts on tracked people**), de-anonymizes website visitors to company and (where a signal exists) person, **finds and enriches contacts at target companies via Apollo**, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), helps reps act via an embedded AI assistant (**Vimi**, visible label **GTM**), and serves **co-branded client portals** that can also embed **agents built entirely on other platforms.**

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` -> `https://seo-apps-production-37a6.up.railway.app`
- **Third-party agent frontend (NOT our code, NOT our repo):** `https://watchtower-by-position2.vercel.app`. The user builds these on an unrelated AI app-builder platform; we only receive and iframe the public URL, plus a `postMessage` run-signal snippet the user deployed into it.
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
- Old top-level internal paths (`/hub`, `/gtm/...`, `/admin/...`) 301-redirect to `/p2/...`, `/p2/gtm/*` 301-redirects to `/p2/b2b-agents/*`, and (new this cycle) `/p2/accounts` + `/p2/signal-tracker/<account>` 301-redirect to `/p2/abm-signal-tracker/*` in exactly one hop.
- **Standing rename rule:** when a persisted URL/slug is renamed, the old one keeps 301-redirecting AND every read path keyed off the old slug is aliased to the new one. A past bug dropped historical runs because only routing was fixed, not the read side. See `[[feedback-persisted-identifier-renames]]`.
- Auth decorators in `app.py`: `login_required`, `admin_required` (= position2 + admin email), `position2_required`. Client gating is `_client_gate(client)` (not a decorator).

---

## ABM SIGNAL TRACKER - RENAMED FROM "COMPANY/SIGNAL TRACKER", URLS MOVED (this cycle, `280bb6c`, `12c90cb`, `f45edf1`)

**This is the general Healthcare/CSG/NorthStar account-based signal product** - not to be confused with the NorthStar-specific instance described further down, which independently happens to carry the same "ABM Signal Tracker" display name after this rename (it was already internally called that before this cycle; this rename brought the OTHER two accounts' product name into line with it).

1. **Display-text rename (`280bb6c`).** ~40 exact-string edits across 26 files changed every user- or AI-facing occurrence of "Company Signal Tracker" / "Signal Tracker" to "ABM Signal Tracker": page titles/breadcrumbs (`templates/accounts.html`), the command-palette entry duplicated in 8 templates, the marketing mock dashboard preview, the Ad Intelligence widget's source pill (2 copies), Vimi's platform-knowledge grounding text, the `get_signal_tracker` tool's description (not its function/tool NAME - deliberately unchanged), the Slack weekly digest, 2 GitHub Actions workflow names, and docs. **`reports/dashboard*.html` are pre-generated, committed HTML files served directly via `send_file`, not Jinja templates** - `scripts/refresh-dashboards.py` only ever splices the `const DATA = {...}` blob back in on refresh, so the 3 title/logo/nav strings in all 4 committed report files were hand-patched directly (this is both correct AND is what actually changed the live pages, rather than waiting on the next scheduled rebuild) - `tracker/dashboard_builder.py` (the generator) was fixed too so future rebuilds produce the right name unprompted.
2. **Route rename (`12c90cb`), a session later, on explicit request: `/p2/accounts` -> `/p2/abm-signal-tracker/accounts`, `/p2/signal-tracker/<account>` -> `/p2/abm-signal-tracker/<account>`.** Real persisted URLs, so it followed `[[feedback-persisted-identifier-renames]]`: every prior URL generation (there were three generations already: bare `/signal-tracker/<id>`, `/p2/signal-tracker/<id>`, and the separate `/dashboard/<id>` alias) now redirects to the new canonical path in exactly one hop, via dedicated single-hop redirects pulled out of the generic `_P2_LEGACY_RULES` catch-all rather than left to double-redirect through it. Updated every internal reference: `_build_account_card`'s live card href, Vimi's platform-knowledge text, the 8-template command-palette entries, `b2b_agents.html`'s dashboard card, and the sidebar "Switch Account" link hand-patched into all 4 `reports/dashboard*.html` files plus `tracker/dashboard_builder.py`. **Caught one real bug the rename would otherwise have silently introduced:** `dashboard.html`/`dashboard_csg.html`'s client-side `curAcct()` regexes the account id back out of `location.pathname` for section-switching via `history.replaceState`; its regex only recognized `dashboard`/`signal-tracker` as path segments, so on the real new path it would have silently dropped the account id from the URL bar on every section click. Fixed by adding `abm-signal-tracker` to the alternation. `tests/test_abm_signal_tracker_url_rename.py` (11 tests) pins every redirect hop and the regex fix.
3. **Account-picker card-grid bug fixed same cycle (`f45edf1`), reported live via screenshot: with exactly 3 accounts, the grid showed 2 cards then a 3rd stranded alone on its own row with a big empty gap.** Two independent bugs: a `minmax(380px,460px)` grid inside a `max-width:1000px` box only ever fit 2 columns (fixed to `minmax(320px,1fr)` in a wider box); and widening the grid surfaced a separate latent bug where `.main` was silently rendering ~40% wider than the real viewport because a non-wrapping marquee track was feeding its full un-clipped width into `.main`'s automatic min-width fallback (fixed with an explicit `.main{width:100%}`). Also gave NorthStar's account card its own gradient (`#5b9dff`) - it had been falling through to the generic grey "not configured" placeholder. `tests/test_accounts_layout.py` (4 tests).

---

## JOB CHANGE ALERT - SHIPPED THIS CYCLE, THE LAST "COMING SOON" CARD ON /P2/B2B-AGENTS

**What it is:** a live page tracking two things - (1) newly-detected job changes at tracked people ("this person just started a new role, the best time to reach out is within days"), and (2) the full roster of people/companies being watched. Route base `/p2/b2b-agents/job-change-alert` (`@position2_required`).

**Origin and scope, worth knowing before touching it:** two genuinely separate efforts shared the name "Job Change Alert" in Slack. **Track A** is Apollo's own native notification workflow, posting one fixed-template card per detected job change into `#job_change_alert_apollo` (channel ID `C0AUUH7BNUA`) - name/title/company/industry/description/city/employees/revenue/LinkedIn/start-date, with a known upstream data-quality gap (many fields legitimately come back `[Unavailable]`). **Track B** was a custom agent being built on Arena (Position2's separate internal AI-agent platform, workflow id `94c6d702-...`) with a watchlist/dedup/cooldown design - **confirmed broken** (repeated `execution_error`s, an always-empty watchlist config) and **explicitly, permanently skipped per the user's direction**: this feature is built entirely from Track A's Slack data instead, directly into this Flask app, not on Arena. **Scope limit inherited from Track A and unfixable without a different data source: Apollo's notification only ever carries the person's NEW role, never their prior employer** - this is a "new job detected" feed, not a "moved from A to B" feed. Cross-referencing Apollo's own API for the prior employer is a real, deliberately-deferred follow-up.

**Data model / files:**
- `tracker/job_change_parser.py` - pure regex parser (`parse_job_change_message`) for Apollo's fixed Slack mrkdwn template, including `_clean_slack_text()` which strips `<url|label>` link syntax and `:emoji:` shortcodes out of the one multiline free-text field (`company_description`) - added after a live-reported bug where raw Slack markup leaked into the UI.
- `tracker/job_change_store.py` - SQLite (`data/job_change_alerts.db`), table `job_change_events`, dedup key = `apollo_contact_id` extracted from the message's own `/contacts/<id>` link.
- `data/job_change_alerts_manual.json` - the source-of-truth ledger (same role as `data/northstar_signals_manual.json`), one entry per parsed Slack message, seeded from 39 real backfilled messages and appended to by every sync run.
- `scripts/sync_job_change_alerts.py` - pulls `conversations.history` from the Slack channel, parses, dedups, appends. **Best-effort by design:** a missing/under-scoped `SLACK_BOT_TOKEN` or the bot not being a channel member just logs and no-ops, never breaks the page. Run as a **subprocess** from the Flask route (never imported - its own `os.chdir()` on import is only safe isolated in its own process, same reasoning as `scripts/refresh-dashboards.py`). `.github/workflows/sync-job-change-alerts.yml` runs it on a daily cron + `workflow_dispatch`.
- **Tracked-contacts/companies roster (673 people / 274 companies):** the live Google Sheet (`Job_change_tracker-For SFO Companies`) still cannot be read by the platform's service account - Position2's Workspace has an external-sharing DLP policy blocking that specific service-account domain, confirmed by attempting to add it as a Viewer and getting an explicit Google policy-block message. **Unblocked with a manual `.xlsx` import as a stopgap**: the user exports the sheet from their own logged-in session, and `scripts/import_job_change_tracked_snapshot.py` (header-driven column mapping, same logic as the live fetcher) turns it into a committed `data/job_change_tracked_snapshot.json`. `_fetch_job_change_tracked_data()` falls back to this snapshot **independently per list** (contacts/companies) whenever the live Sheets read comes back empty, so the moment the sharing block is lifted, live data resumes automatically with zero code changes. **To refresh:** re-export to `.xlsx`, re-run the import script, commit the updated JSON. **Real fix still needed:** a Position2 Workspace admin must allowlist the service account's domain in `admin.google.com`, or the sheet owner must re-share from a personal (non-Workspace) Google account, the way the already-working Anonymous Visitors sheet happens to be owned.

**Routes:**
- `GET /p2/b2b-agents/job-change-alert` - renders `templates/job_change_alert.html`.
- `GET /p2/b2b-agents/job-change-alert/data` - the 39+ parsed events, JSON.
- `GET /p2/b2b-agents/job-change-alert/tracked` - the roster (contacts/companies), gzip-cached, live-Sheets-then-snapshot-fallback as described above.
- `POST /p2/b2b-agents/job-change-alert/sync` (`admin_required`) - runs the sync script synchronously so staff don't have to wait for the daily cron.

**UI (`templates/job_change_alert.html`, `static/css/job_change_alert.css`, `static/js/` inline):** three tabs - **Recent Job Changes**, **People We Track**, **Companies We Track** - built to match the platform's Anonymous Visitors/LinkedIn Intelligence visual language (aurora blobs, glowing count-up stat cards, the shared WebGL particle background via `pfx_bg.js`). Notable pieces, roughly in the order they were added across this cycle's many follow-up passes:
- Per-tab filters (industry/timeframe/seniority/department/location/size-bucket) built from already-loaded distinct values, no extra backend calls.
- A hand-built SVG area/line trend chart ("Weekly Momentum," 8-week job-change counts) with Catmull-Rom smoothing, gradient fill, gridlines, hover tooltips, and a stroke-draw-in animation - replaced an earlier bar-list that didn't read as a trend with real, sparse data. **A "vs prior week" delta badge that briefly sat next to this chart's title was added, then removed again this cycle per explicit user request** - the chart itself is unaffected.
- A centered, type-accent-colored detail modal (2-column grid) opening on any row click across all three tabs, reusing the page's amber/coral (events) / violet/indigo (people) / indigo/sky (companies) accent language.
- Working company logos via `https://icons.duckduckgo.com/ip3/{domain}.ico` - **`logo.clearbit.com`, this app's long-standing default logo source, no longer serves logos to anonymous/unauthenticated embeds at all** (a universal production bug discovered and fixed this cycle, not merely a sandbox network restriction as first assumed) - DuckDuckGo's icon service is free, keyless, and cleanly 404s (preserving the existing fallback-to-initials UX).
- A rotating latest-signal ticker, removable filter chips, and a 48-hour freshness glow on recent rows.
- Sleek, page-wide custom scrollbars (overriding `aurora-app.css`'s chunky global default), and per-tab dedicated columns for LinkedIn (`.jc-li-badge`) and, on the events tab, separately for New Role vs. New Company.
- The three tab labels: no emoji prefixes, and rebuilt as a full-width, edge-to-edge, three-way segmented control with a glowing gradient pill that slides behind the active tab and recolors per section.

**Turning the card live:** `templates/b2b_agents.html`'s "Coming soon" placeholder became a live card; `templates/hub.html`'s B2B Agents card and "by the numbers" band both bumped (see the hub.html comments in the file itself, which state the exact arithmetic and are pinned by `tests/test_hub_card_counts.py`); command-palette entries added across the same 8 templates that already carry the shared block.

**Known open items:**
1. **`SLACK_BOT_TOKEN`'s scope is unverified for reading channel history** - it has only ever been used to *post* (`chat.postMessage`); reading needs `channels:history` (or the workspace equivalent) plus actual channel membership. The feature works fine off the backfilled data regardless; this only affects whether new alerts keep flowing in automatically.
2. **`SLACK_BOT_TOKEN` needs adding as a GitHub Actions repo secret** (separate secret store from Railway, even though it may already exist there) for the scheduled sync workflow to have any chance of finding new events.
3. **The tracked-roster Google Sheet sharing block** (above) needs a Workspace-admin fix or a re-share from a personal account; the `.xlsx` snapshot is a real stopgap, not a permanent solution.
4. **An open accuracy question, unresolved and not this feature's to fix:** a third-party skill on a platform called Swan (backed by Coresignal, fresher LinkedIn data than Apollo's own DB) caught a real job change that never triggered an Apollo alert. Nothing built on this; flagged to the user.
5. The separate, broken Arena "Track B" agent (above) remains exactly as broken as it was found, untouched, if that track is ever picked back up instead.

---

## CONTACT FINDER (the biggest feature, and where the other half of this cycle's work landed)

**Route base:** `/p2/b2b-agents/company-people-intelligence` (the slug was never renamed when the display name changed from "Company & People Intelligence" to "Contact Finder"; the URL, the Python function prefix `cpi_*`, the helper prefix `_cpi_*`, the JS file, the CSS file and the template all still say `company_people_intelligence`). Staff-only (`@position2_required`). **The user plans to open this to external paying clients "soon" (as of the last time this was discussed) - this cycle's audit work was explicitly framed as pre-launch hardening.**

### The routes and the credit model (unchanged this cycle, restated because it's the design constraint everything follows from)

Apollo's **search** endpoints are free; only **enrichment** spends credits from one shared agency account:

- `mixed_people/api_search` and `mixed_companies/search`: **0 credits**. All browsing, filtering, counting and vocabulary learning runs here.
- `people/match`, `people/bulk_match`, `organizations/enrich`: **1 credit per record**. Only explicit user action reaches these.
- `people/bulk_match` is capped at 10 per request by Apollo and 50 ids per bulk reveal by us.
- Every code path that can spend threads a `spend = {"credits": 0}` dict through and the response carries `credits` back to the UI.
- Multiple caches exist purely to avoid paying twice: person profiles (version-stamped, 90-day positive TTL, in-process `_PE_MEM` then Postgres `person_enrichment`), company-name resolution (Postgres, survives a deploy), employer firmographics (`_CPI_FIRMO_CACHE` + `_cpi_firmo_db_read/write`, 30-day TTL).
- **Apollo's `organization_headcount_twelve_month_growth` is a FRACTION** (0.19 = 19%, 1.5 = 150%) for OUTPUT/display - a completely separate direction from the FILTER input (`headcount_growth_min/max`), which takes whole percentages (`7` not `0.07`) - confirmed live this cycle against a real production-linked Apollo key, do not conflate the two.

### THIS CYCLE'S FIXES, in the order found and fixed

**Pre-launch audit (`2c9b83e`), five parallel background investigations (Fill-filters parsing, search/verification, credit accounting, cross-account access control, XSS/error-leakage/abuse limits):**
- **Fixed:** Companies-tab search never billed a credit despite genuinely costing one; a credit spent resolving a company NAME vanished from the ledger if the search that followed then failed; `exclude_technologies`/`technologies_all`/`email_status`/`market_segments`/`job_titles` were allow-listed for the NLP intent parser but never explained in its system prompt (same root cause as the keywords bug below); the Companies tab could get silently flipped to People for any query the intent taxonomy has no "list of companies" bucket for; `rejectFilterKey`/`cpiRelax` (JS) used `STATE.entity` instead of `STATE.shownEntity` (the same mixup already fixed once for `cpiOpenDetails` in a prior cycle, recurring at a different call site); tenure-min/max restore treated a real `0` as blank; headcount growth of exactly `0%` got dropped by the same "0 means no data" rule as employee count/revenue (growth is legitimately often 0); `/list`'s row cap had a check-then-insert race; `/export` had no row cap at all; no `MAX_CONTENT_LENGTH` anywhere on the platform; `/count`/`/parse-query`/`/chat` had zero rate limiting despite real per-call OpenAI/Apollo cost (added a lightweight per-user, per-process limiter - **per-process means the true ceiling is roughly limit x gunicorn worker count**, since there's no Redis/shared store).
- **Loud instead of silent:** `SECRET_KEY` falling back to a hardcoded checked-in dev secret, and `GOOGLE_CLIENT_ID` unset accepting UNVERIFIED sign-ins, both now `log.error` loudly instead of degrading silently. **`SECRET_KEY` is confirmed set to a strong value on Railway** (user confirmed same day - this item is closed).
- **Flagged, deliberately not changed:** the Apollo/OpenAI credit pool is fully shared with no per-client quota, and `/credits` reports an ALL-user aggregate to any signed-in caller - fine internally, a cross-tenant leak once external clients share the endpoint, contingent on how external-client auth ends up being gated (not decided yet).
- `tests/test_cpi_prelaunch_audit.py` (30 tests) + new `tests/conftest.py` (resets the rate limiter's in-process state between tests).

**Funding-value ceiling crash, found via live probing with a real Apollo key (`0642c95`):** `total_funding_min/max`/`latest_funding_min/max` above `2**31-1` got a hard 422 from Apollo instead of being clamped or ignored, confirmed exactly at the boundary. "Companies that raised over $5 billion" is an ordinary ask and would have crashed the whole search with a misleading "try again in a moment" message (never true for a value that 422s identically every retry). Fixed by clamping both bounds to the ceiling before sending and reporting the clamp back (`funding_value_clamped`) rather than silently answering a smaller question. `revenue_min/max` has no such ceiling (tested to $2 trillion, no error) - the ceiling is specific to the two funding-amount params. **Also confirmed live and closed out every remaining unverified-filter question from prior audits:** `founded_min/max`, `num_jobs_min/max`, `headcount_growth_min/max`, `funded_after/funded_before`, `exclude_locations` are all genuinely Apollo-enforced. **One real caveat, not a bug:** Apollo excludes any company with no funding data on file from ANY funding filter, even a trivially-satisfied `min=0` - worth a UI hint if this filter sees more use.

**"Fill filters" bug, reported live by the user via screenshot, not found by a background audit (`6c10403`):** query "top executives in tech industry in san francisco... employees more than 500" filled EMPLOYEES as "5,001+" and a KEYWORDS chip of "top executives," returning zero results.
- `snapEmployeeBucket` (JS) picked Apollo's headcount bucket by raw interval-overlap width; for ANY open-ended query (a min with no max), the top-most bucket's overlap is infinite, so it always won regardless of how small the requested minimum actually was. Fixed by special-casing the open-ended case: pick the bucket that CONTAINS the requested minimum instead of maximizing overlap.
- The NLP system prompt never explained what `keywords` compiles to (`q_keywords`, a literal text match, ANDed against filters), so the model would echo role wording into it on top of correctly mapping it to `seniorities` - guaranteed zero rows when combined with a correct seniority filter. Fixed with a deterministic stoplist guard plus explicit prompt guidance.
- `tests/test_cpi_fill_filters_audit.py`. **Pattern to watch for elsewhere in the intent-parser prompt: any JSON key declared in the schema but never given its own explanatory paragraph is a gap where the model free-styles.**

**Claude added as a second opinion on the NLP intent parse (`3256bac`), on explicit user request.** A single OpenAI call can still mis-follow instructions on a case the prompt doesn't already name by heart, which is exactly why prompt patches for reported bugs (like the one directly above) never fully close this defect class.
- `_cpi_verify_intent_with_claude(text, intent, context="")` sends the same request plus the first model's own JSON answer to Claude - **critique-and-correct, not a blind dual-extract-and-diff** (diffing would false-positive constantly on free-text fields like titles/industries where two correct extractions rarely use identical wording) - asking it to catch the same failure family: invented keywords, dropped locations, mis-picked numeric buckets. Wired into both `cpi_parse_query` and `cpi_chat` (chat passes the last 12 turns as context).
- New client factory `_cpi_anthropic()`, exact mirror of the existing `_cpi_oai()`: returns `None` without `ANTHROPIC_API_KEY`, model pinned via `ANTHROPIC_MODEL` (default `claude-sonnet-5`). **Feature is completely inert until `ANTHROPIC_API_KEY` is set on Railway - it is NOT yet set.** Ships safe with zero behavior change for any environment without one.
- **Best-effort contract, same as every other optional-AI-extra in this app:** no key, a network failure, a non-JSON reply, or a JSON reply that isn't an object all fall back to the ORIGINAL, unverified intent untouched.
- **Found and fixed one adjacent latent bug while wiring this in:** `cpi_chat` never guarded against `json.loads(raw)` returning a non-dict (parse-query already did) - would have crashed as an unhandled `AttributeError`/500 on a stray top-level JSON array from the model, instead of the graceful "try rephrasing" every other parse failure gets.
- `anthropic>=0.122.0` added to `requirements.txt`. `tests/test_cpi_claude_crosscheck.py` (13 tests). **Watch for the real-network-leak trap again:** the first draft of the chat-side test let `cpi_chat` reach its real company-resolve path and made a genuine ~19s outbound call to Apollo before failing - fixed by mocking the same helpers `tests/test_cpi_chat_history.py`'s own `_chat()` helper already mocks. This is now the second time this exact leak pattern has been hit; copy that helper's mock list rather than reconstruct it.

### Full audit history (thirteen rounds now, each one commit plus one test file whose module docstring states the defects in plain language)

| Audit | Commit(s) | Test file | The recurring defect |
|---|---|---|---|
| 1. Search filters | `0f9469b`, `b5a9fd6` | `test_cpi_filter_audit.py`, `test_cpi_industry_filter.py` | the industry filter did not filter by industry |
| 2. Vocabulary pickers | `1173dbe`, `e55831b` | `test_cpi_vocab_pickers.py`, `test_cpi_taxonomy.py` | free text where a closed list existed |
| 3. Chat filters | `62dfa2c`, `c029778` | `test_cpi_chat_audit.py` | the chat accepted filter values the pickers would reject |
| 4. Enrich flow | `af5ede0` | `test_cpi_enrich_audit.py` | paying twice, promising phone numbers Apollo does not return |
| 5. Export flow | `4460bed` | `test_cpi_export_audit.py` | the file did not say what the screen said |
| 6. History flow | `8ce0409` | `test_cpi_history_audit.py` | purchases not recorded, no retention, duplicates |
| 7. Dashboard flow | `d2b38d6` | `test_cpi_dashboard_audit.py`, `test_cpi_dashboard_behaviour.py` | the grid described the tab, not its own rows |
| 8. People-search buckets + domain filter | `999af38`, `5f3e0f6`, `d5006b9` | `test_cpi_search_buckets_audit.py`, `test_cpi_domain_unconfirmed_audit.py` | Apollo splits saved-vs-net-new and the app read one array; chat absence claims not gated on a real search; "Apollo didn't say" read as "Apollo said no" |
| 9. All searches/filters, companies mirror + 4 more | `d1d62bd`, `fd8cdcc` | (extended domain-unconfirmed, employer-facts-outage, HQ-relax-entity, numeric-coercion, credit-pluralization audits) | companies never got the domain-unconfirmed fix its person-search twin got; a fetch outage read as a false rejection; a stale entity variable; a dead relax button; an uncaught type error |
| 10. The two deliberately-deferred findings | `4a9eace` | `test_cpi_enrich_email_credit_audit.py`, `test_cpi_verify_rows_multi_reason_audit.py` | a cache hit billed like a fresh purchase; a reason-count undercount |
| 11. Pre-launch: billing, filters, access, abuse | `2c9b83e` | `test_cpi_prelaunch_audit.py` | fixable-in-code correctness/security gaps ahead of an external client launch |
| 12. Funding-ceiling crash + live filter verification | `0642c95` | (probe script, not a permanent test - a live-key probe against production Apollo) | a validation gap (unbounded numeric input) wearing an "Apollo is down" costume |
| 13. Fill-filters mis-snap + keyword-echo | `6c10403` | `test_cpi_fill_filters_audit.py` | an unbounded-range heuristic always picks the extreme bucket; an unexplained schema field lets the model free-style |

**The pattern, stated once more:** a surface asserting something its data does not support. An empty result reading as a fact about the world rather than a fact about the request. A tab toggle doubling as a claim about what is on screen. A header promising a percent over a column holding a fraction. A fetch that failed read as a fetch that succeeded and found nothing. A number silently clamped or misassigned with nothing surfaced that it happened. When auditing any other flow in this app, that is the thing to look for first.

### Known open items inside Contact Finder

- **`ANTHROPIC_API_KEY` is not yet set on Railway** - the Claude cross-check ships fully inert until it is. Setting it is a pure accuracy upgrade with the documented best-effort fallback if anything about it misbehaves.
- **External client launch is planned "soon" but not yet scheduled/executed** - the shared-credit-pool and `/credits`-aggregate-leak items above are still open, contingent on how external auth for that launch ends up being designed.
- The **chat path has never been exercised against live OpenAI/Apollo keys** end to end (only the free `mixed_people/api_search` was ever called, via the connected Apollo MCP, plus one real-key funding-filter probe this cycle).
- The **120-row history cap silently truncates a paged search**.
- **Zero-result searches are never saved to history.**
- `_cpi_probe_company_free` **guesses only `.com`** when deriving a domain from a name.
- **Apollo's advanced-filter-requires-upgrade risk for `organization_founded_year_range`/department-headcount on people search** - unconfirmed against production, `_post()`'s 422 retry behavior not hardened.
- **`_cpi_verify_rows` returns a 3-tuple and `rejected`'s per-reason values are not summable for a total** - contract facts from a prior cycle, still true, re-read the docstrings before modifying either function.
- **The per-process rate limiter's true ceiling scales with gunicorn worker count** - there is no shared/Redis-backed limiter store yet.

---

## TESTING DISCIPLINE (unchanged in method, reinforced this cycle)

```bash
cd <repo> && PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q
```

- **Always set `PYTHONDONTWRITEBYTECODE=1` / use `python3 -B`.** macOS system Python writes `.pyc` files outside the repo, which once let a mutant survive a source restore and silently invalidated a mutation run.
- **Mutation testing is the gate, not the suite passing.** After writing tests for a fix, deliberately break each fixed line one at a time and confirm a test dies, then restore. Every bug fixed this cycle was verified this way.
- **A mutation test needs fixture data where the buggy and fixed computation would actually diverge** - coincidentally-matching numbers can make a test pass identically against both versions of the code (see the `rejected`-values lesson from a prior cycle, still worth re-reading).
- **Text assertions on a JS bundle cannot distinguish a working guard from a disabled one.** Several `test_cpi_*`/`test_job_change_*` tests shim `document`/`window`/`fetch`, `eval` the real bundle/inline scripts in node, and assert on the HTML/requests actually produced. Skips (not fails) when node is unavailable.
- **Live-verifying a UI change needs a temporary, fully-removed preview route** - add a route that sets `session["google_user"]` and redirects, screenshot in both dark and light theme via the Browser pane, then confirm via `grep -n "__preview" app.py` and `git diff --stat app.py` showing zero changes before every commit. This was used constantly across the Job Change Alert follow-up passes.
- Do not write change-detector tests. If a mutant is unreachable in practice, say so in the commit message instead of pinning it.

---

## APOLLO PERSON ENRICHMENT ON EXTERNAL USAGE

Unchanged this cycle. Lives at `/p2/admin/external-usage` (see `[[project-person-enrichment]]`). Clicking a person opens a profile modal with Apollo-sourced identity, employment and contact details, labelled outbound links, and an AI read of the person. New members are auto-enriched. There is an Excel export. When Apollo cannot match a person, the flow falls back to enriching their company by email domain rather than showing nothing. **This shares `_enrich_people`'s cache with Contact Finder's chat-side name reveal** - both read the same `_PE_MEM`/`person_enrichment` table, so a change to one's cache semantics affects the other.

Two hard-won points that generalise: **validate response shape, not just HTTP status** (a 200 with an empty payload was once cached as "this person has no data," poisoning the negative cache); **version-stamp every enrichment cache** so a fixed bug can self-heal rather than serving its own old wrong answers forever.

---

## THE NORTHSTAR ABM SIGNAL TRACKER (complete, maintenance mode, unchanged this cycle)

**Note the naming overlap with the general "ABM Signal Tracker" product renamed above** - this is NorthStar's own specific client-portal instance, already called "ABM Signal Tracker" before this cycle's rename brought Healthcare/CSG's product name into line with it. Different code paths, coincidentally identical display name.

- **All 35 companies researched across 7 batches; 71 curated signals** (52 HIGH / 16 MEDIUM / 3 LOW). No pending batches.
- Data lives in SQLite (`data/tracker_northstar.db`), populated from `data/northstar_signals_manual.json` by `seed_northstar_signals.py`. **Always run with `--prune`**: without it the seed is insert-only. The JSON is the source of truth.
- **`_quality_bar` inside that JSON is the living curation policy.** Permanent **6-month admission cutoff by `signal_date`**, plus fixed categorization conventions - see `[[feedback-signal-relevance-bar]]`.
- **Canonical `signal_type` strings** are exact-match constants shared across `signal_score.py`, `dashboard_builder.py` and the seed data. A near-miss silently drops the signal from every KPI/chart/filter without erroring.
- **"Creative Hiring" displays as "Anesthesiologists" for NorthStar only**, via a per-account `hiring_opts` override. The stored `signal_type` never changes. Reuse this pattern; never globally rename a shared category.
- `reports/dashboard*.html` are **built artifacts committed to git**. Never hand-edit them.

---

## SURFACE 4 - CLIENT PORTALS

Per-client co-branded front door at `/<client-slug>`. Currently one client: `northstaranesthesia`. Unchanged this cycle.

- **`CLIENTS` registry** entry fields: `slug`, `name`, `short`, `website`, `logo`, `domains`, `accent`/`accent2`, `tagline`, `blurb`, `agents` (ordered APP_AGENTS slugs), `dashboards` (agent-slug -> pre-built static HTML), `linkedin_sheet`, `external_tools` (agent-slug -> full external URL to iframe).
- NorthStar: `domains=["northstaranesthesia.com"]`, `accent="#5b9dff"`, agents list currently shows 5 live (LinkedIn Social Researcher, renamed from LinkedIn Strategy Researcher on 2026-08-20 when that name moved to a new, unrelated agent, remains hidden platform-wide, see below - it returns to 6 whenever LSR is restored, no code change needed there).
- Sign-in to the portal is open to any Google account.
- **Three agent types, all keyed off the same `agents` list:** SERP-connected (`seo_slug`, run-metered via postMessage), dashboard-backed (`is_dashboard`, shown Live, never metered), external-tool (`is_external`, iframed, run-metered on a real postMessage signal).
- `_client_agent_view(slug, client)`: **always pass `client`**. Omitting it silently resolves `connected=False` for an external-tool-only agent and 400s the log-run endpoints.

---

## LINKEDIN STRATEGY RESEARCHER - STILL HIDDEN, NOT DELETED (unchanged this cycle, carried over from v24)

**Pulled from every listing on 2026-08-14; the owner expected to ask for it back "in a few days" - still hidden as of this cycle, with no restore request received yet.** Renamed 2026-08-20 to LinkedIn Social Researcher (slug also moved, to `linkedin-social-researcher`) when the old name/slug moved to a new, unrelated agent. Nothing underneath was deleted or disabled: `/p2/b2b-agents/linkedin-social-researcher` still resolves, the watchtower tool it embeds still loads, `APP_AGENTS_BY_SLUG` still holds the full entry, and NorthStar's own ordered `agents` list in `CLIENTS` is untouched.

**To restore, in this exact order:**
1. Empty `HIDDEN_AGENT_SLUGS` in `app.py` (drives `/app`'s main grid, the `/app` sidebar, and every client portal - three consuming surfaces from one flag).
2. Un-comment the card in `templates/b2b_agents.html` **and** its Ctrl+K command-palette entry - **these do NOT derive from `HIDDEN_AGENT_SLUGS`**, they are hand-written HTML.
3. Put `templates/hub.html`'s B2B card back to `7` dashboards / `7` live and the "by the numbers" band count up by 1 from whatever it is at restore time (it has moved twice already for unrelated reasons this cycle and last - re-verify the arithmetic in the file's own comments rather than assuming a number), and the card description back to mentioning "strategy research".
4. Restore `templates/context.html`'s LSR mentions in the two spots noted at hide time.
5. `tests/test_hidden_agent_withdrawal.py` asserts the hidden state - flip it to a restored-state test or delete it at the same time as steps 1-4, not before.

**How to apply:** see `[[project-lsr-hidden]]` for the full restore checklist with test names.

---

## SEO SUITE (unchanged this cycle, carried over from v24)

**16 tools** on `/p2/seo` (staff-only). Includes "Competitor Analysis" (real SEMrush-backed data, `_SEO_TOOLS_FALLBACK` since the SERP app's `/tools.json` manifest fetch always fails in practice - the fallback list IS the actual source of truth). A **separate, unrelated, dormant `/app` placeholder** happens to share the identical display name ("Competitor Analysis," slug `competitor-seo-intelligence`) - its Request Access button was reopened but it remains **deliberately unconnected**, because the real tool's client picker shows every client with no visible per-member scoping, which would be a cross-client data leak if opened to any signed-in Google account without a staff review step. Keep these two straight; they only share a name.

---

## THE EXTERNAL-TOOL PATTERN

An agent whose entire backend lives on a third-party AI app-builder platform we have no access to; we get a public URL.

1. Confirm no `X-Frame-Options`/CSP `frame-ancestors` blocks iframing.
2. Add it to `APP_AGENTS` with no `seo_slug`.
3. Add its slug to the client's `agents` list and its URL to `external_tools` (client portal), or add a small route rendering `templates/embed.html` (internal).
4. `client_embed.html` / `embed.html` iframe it; the address bar shows OUR path.
5. **Metering requires the external tool's cooperation.** It must call `window.parent.postMessage({source:'p2-agent', type:'agent-run-started'|'agent-run-finished'}, 'https://intelligence.position2.com')`, guarded by `if (window.parent !== window)`. Deployed and working for LinkedIn Social Researcher (renamed from LinkedIn Strategy Researcher on 2026-08-20, currently hidden from listings, but the mechanism itself is untouched).
6. **Any prompt written to be pasted into that other platform must be self-contained** and describe only that tool's own observable behaviour, never our internal routes, slugs, or architecture.

---

## LINKEDIN INTELLIGENCE (internal + per-client, multi-sheet, unchanged this cycle)

Route `/p2/b2b-agents/linkedin-intelligence`. Renders `templates/linkedin_scraper.html`; all content drawn client-side by `static/js/linkedin.js`. One row per person x post engagement, header-mapped.

- `_fetch_linkedin_intel_data(force, sheet_id)` with **per-sheet caches** so internal and each client portal read independent sheets.
- **Do not confuse** LinkedIn Intelligence (your own engagement data from a Sheet) with LinkedIn Social Researcher (external competitive analysis, renamed from LinkedIn Strategy Researcher on 2026-08-20 when that name moved to a new, unrelated agent, currently hidden from listings), the ABM Signal Tracker's own News Mention/Partnership categories, or Job Change Alert (a completely separate feature, sourced from Slack, tracking new-role detections rather than engagement).

---

## ADMIN ANALYTICS (all `@admin_required`, each has a `.../data` JSON endpoint, unchanged this cycle)

- **Internal Usage** `/p2/admin/internal-usage`: staff logins + page views, "Linked to Pre-Login" KPI, merged journey drawer via `p2_vid`.
- **External Usage** `/p2/admin/external-usage`: everyone who signed in with a non-`@position2.com` email (reads the **`Member Signins`** tab, NOT the Login Log). Rich sortable/filterable People table, AI priority sort, "What they ran" column, Apollo person profile modal.
- **Client Usage** `/p2/admin/client-usage` (+ `/<slug>`): splits a portal's activity into Position2 team vs client team vs Other by email domain.
- **Anonymous Traffic** `/p2/admin/anonymous-traffic`: visitor_intelligence engine, concurrent IP resolve, per-visitor drill-downs.
- **Public Page Analytics** `/p2/admin/public-page-analytics`: public member sign-ins + journeys, rich Members table.
- **Public Agent Usage** `/p2/admin/public-agent-usage`: per-user/per-agent run counts vs cap.
- **Access Requests** `/p2/admin/access-requests`.

**Sheets read performance rule:** warm the IP cache concurrently, do concurrent per-thread `values().get()`, cache ~300s. **Do NOT use `batchGet`**, it returns empty in prod. See `[[feedback-sheets-read-performance]]`.

**Performance pass, prior cycle but worth restating since it touches every page load (`72dc345`):** `_agent_run_counts`/`_agent_access_requests_raw` were uncached and hit a fresh Sheets read on nearly every navigation - now 30s TTL cached. `_fetch_visitor_analytics_uncached` did 5-7 serial Sheets round-trips including a duplicate `_read_access_requests()` call - now one `ThreadPoolExecutor` pool. Static assets had NO browser caching (Flask's own defaults send an explicit `Cache-Control: no-cache`) - fixed via `app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600`. Responses beyond two hand-gzipped JSON endpoints were uncompressed - added a generic gzip `after_request` hook above an 800-byte floor. **Deliberately left alone:** `theme.js`'s blocking `<script>` tag - it sets `data-theme` before first paint to avoid a light-theme user seeing a dark flash on every navigation; a real UX tradeoff, not an oversight.

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                ← Flask server (~16,465 lines, 167 routes + loop-registered families):
│                            auth (3 decorators + client gate), all 4 surfaces, AGENTS/APP_AGENTS/
│                            SIGNALS/INDUSTRIES/CLIENTS/ACCOUNTS registries, HIDDEN_AGENT_SLUGS,
│                            OpenAI (Vimi x2 + Contact Finder's own chain) + Anthropic (Contact
│                            Finder's Claude cross-check, inert w/o ANTHROPIC_API_KEY), Contact
│                            Finder's cpi_* routes and _cpi_* helpers, Job Change Alert's routes
│                            and _fetch_job_change_tracked_data(), marketing routes, /api/demo-
│                            request, /api/track|atrack|identify|whoami, /app/* + run history,
│                            /p2/* + admin analytics, client-portal routes, LinkedIn Intelligence
│                            (per-sheet), Postgres history. Does NOT build ABM Signal Tracker
│                            dashboards, just serves the pre-built HTML.
├── tracker/apollo_client.py ← Apollo API client, ~971+ lines: search_people, search_companies,
│                            all filter-building + domain/industry enforcement logic (see
│                            Contact Finder section - this is the file most audit-cycle bugs
│                            have lived in), plus the funding-value clamp added this cycle.
├── tracker/job_change_parser.py, tracker/job_change_store.py ← Job Change Alert's parser + SQLite
│                            store, new this cycle.
├── scripts/sync_job_change_alerts.py, scripts/import_job_change_tracked_snapshot.py ← new
│                            this cycle, both invoked as subprocesses, never imported.
├── tests/                ← 61 files, 1,562 tests. Most are named test_cpi_*.py or
│                            test_job_change_*.py, one per audit/feature.
│                            test_cpi_dashboard_behaviour.py executes the JS bundle in node.
├── visitor_intelligence/ ← de-anon engine: resolver.py, pipeline.py, identity_graph.py.
├── tracker/              ← signal pipeline pkg (news_client, news_relevance, signal_score,
│                            dashboard_builder [build_dashboard(), takes hiring_opts],
│                            csv_loader, snapshot_store, sheets_client, apollo_client)
├── main.py               ← weekly orchestrator (Healthcare) -> data/tracker.db
├── build_northstar_dashboard.py, build_csg_dashboard.py
├── seed_northstar_signals.py   ← always run with --prune
├── ad_intelligence/      ← built React app served by Flask
├── static/
│   ├── css/ (ds-tokens, ds-components, gtm, hub, seo, linkedin, admin, aurora-app,
│   │        grid-tokens, client-portal, company_people_intelligence.css,
│   │        job_change_alert.css — new this cycle)
│   ├── js/ (theme, linkedin, visitor_track, pfx_bg, aurora, anonymous_visitors,
│   │       company_people_intelligence.js — 3,035 lines, IIFE)
│   └── clients/northstaranesthesia/logo-white.svg
├── templates/
│   ├── agents.html          ← THE SINGLE SHARED MARKETING TEMPLATE, {% if page %} variants
│   ├── app.html, app_base.html, app_embed.html, app_history*.html, app_settings.html
│   ├── hub.html, b2b_agents.html (was gtm.html — LSR card commented out, Job Change Alert
│   │        now a live card), seo.html, accounts.html (now /p2/abm-signal-tracker/accounts),
│   │        embed.html, context.html (=Playbook), 403.html
│   ├── company_people_intelligence.html   ← Contact Finder (670 lines)
│   ├── job_change_alert.html    ← Job Change Alert, new this cycle
│   ├── linkedin_scraper.html   ← serves BOTH internal and client LinkedIn dashboards
│   ├── admin_usage.html, admin_visitors.html, admin_members.html, admin_agent_runs.html,
│   │        admin_requests.html, admin_external_usage.html, admin_client_usage.html,
│   │        admin_client_detail.html
│   ├── _admin_menu.html     ← the ONE shared internal admin dropdown
│   ├── client_*.html        ← client-portal shell, home, agent detail, embed, history, denied
│   └── ppc_chat_widget.html ← shared Vimi chat widget (internal only)
├── data/job_change_alerts.db, data/job_change_alerts_manual.json,
│       data/job_change_tracked_snapshot.json ← Job Change Alert's data, new this cycle
├── reports/          ← dashboard*.html: BUILT ARTIFACTS, committed, never hand-edited
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml,
        sync-job-change-alerts.yml (new this cycle)
```

### Deploy + data model (unchanged)

- **Code/UI** push to `main` -> Railway redeploys (~60-100s). No hot reload locally.
- **Google Sheets** is the primary store for internal analytics. Two sign-in tabs with different column layouts (Login Log, `Member Signins`), Page Views, Agent Runs, Visitor Analytics, Demo Requests. Job Change Alert's own tracked-roster sheet is currently blocked (see the Job Change Alert section) and falls back to a committed `.xlsx`-derived JSON snapshot.
- **Postgres** (`DATABASE_URL`): `agent_run_history`, `cpi_search_history` (Contact Finder), plus Contact Finder's persistent caches (org resolution, firmographics, learned vocabulary, `person_enrichment`, `cpi_person_enrichment` id-cache).
- **SQLite** (committed): `data/tracker.db` (Healthcare), `data/tracker_csg_v2.db` (CSG), `data/tracker_northstar.db` (NorthStar), `data/job_change_alerts.db` (new this cycle). **Gitignored, real PII, NEVER commit: `data/identity_graph.db`.**

---

## VIMI, DE-ANON, STITCHING, AND THE OTHER SURFACES (unchanged this cycle)

- **Vimi** (label **GTM**): two backends, `/api/ppc-chat` (widget, `@position2_required`) and `/api/vimi-chat/<account_id>`. Never mix Healthcare and CSG in one answer.
- **Anonymous Visitors / de-anon:** `visitor_intelligence/`. Company-level multi-signal IP resolution, connection-type hard gate, noisy-OR confidence, Apollo enrichment, 0-100 intent. Person-level: persistent SQLite identity graph. **Never fabricates a person.**
- **`p2_vid` stitching:** Page Views and both login tabs carry a visitor-id column.
- **Surface 2, `/app`:** shell `app_base.html`, `APP_AGENTS` cards (minus `HIDDEN_AGENT_SLUGS`), 3 wired to live seo-apps tools plus LSR (currently hidden), the rest request-access-only. Per-agent `lock_label` and `no_request` keys control the locked-card CTA, enforced server-side too.
- **Surface 1, public site:** one template `agents.html`, `{% if page %}` chain.
- **Surface 3, `/p2/*`:** `/p2/hub`, `/p2/b2b-agents` (+ Contact Finder, Job Change Alert, sentiment-pulse MOCK data, ad-intelligence React app, linkedin-intelligence, linkedin-social-researcher, linkedin-strategy-researcher [new agent, this cycle]), `/p2/seo` + tools (16), `/p2/abm-signal-tracker/accounts` + signal trackers (renamed URL this cycle), `/p2/playbook`, admin dashboards.

**Agent roster hazard, still live:** the roster exists in **three independent lists** (`AGENTS`, `APP_AGENTS`, and a JS array in `templates/context.html`), plus the internal SEO Suite tools list, plus `HIDDEN_AGENT_SLUGS` as a fourth cross-cutting mechanism, plus now Job Change Alert's own card is hand-written in `b2b_agents.html` like every other B2B agent card. **Nothing derives one from another.**

---

## BRANDING + THEME (unchanged)

"Arena" mark: bright-green hexagon `#55be8c` + steel-blue + dark-green petals = 6-point star. `theme.js` (`localStorage['p2-theme']`, default dark). Hard sign-out: `/logout` sends `Clear-Site-Data` + explicit cookie deletion. Bricolage Grotesque is the public body font.

---

## ENVIRONMENT VARIABLES

**Railway:** `DATABASE_URL`, `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`/`FLASK_SECRET_KEY` (**confirmed set to a strong value this cycle**), `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN` (**scope for reading `#job_change_alert_apollo` history is unverified - see Job Change Alert section**), `SLACK_CHANNEL_ID`, `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), **`APOLLO_API_KEY` (Contact Finder + de-anon + person enrichment all depend on this one shared key and its shared credit pool)**, **`ANTHROPIC_API_KEY` (opt, NOT YET SET - Contact Finder's Claude cross-check is fully inert without it), `ANTHROPIC_MODEL` (opt, defaults to `claude-sonnet-5`)**, `VI_ENRICH_ON_VIEW` (opt), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt), `SMTP_*` (unusable on Railway).
**GitHub Actions secrets (separate store from Railway):** `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`, and **`SLACK_BOT_TOKEN` needs adding here too** for the new Job Change Alert sync workflow (it may already exist on Railway, but that is a separate secret store).

**Social Creative Intelligence Analyst (Phase 1 Instagram + YouTube 2026-08-25, Phase 2 Facebook/TikTok/X/LinkedIn same day, Phase 3 classification + cross-platform synthesis + report UI + audio transcription 2026-08-26 -- unhidden and fully listed on b2b_agents.html and the command palette as of Phase 3):** `APIFY_API_TOKEN` (opt, NOT YET SET -- Instagram/Facebook/TikTok/X collection all degrade to scrape_failed without it), `YOUTUBE_API_KEY` (opt, NOT YET SET -- YouTube collection returns no videos without it), `SCI_APIFY_INSTAGRAM_ACTOR_ID` / `SCI_APIFY_FACEBOOK_ACTOR_ID` / `SCI_APIFY_TIKTOK_ACTOR_ID` / `SCI_APIFY_X_ACTOR_ID` (all opt, each overrides that platform's default actor if it needs swapping), `SCI_APIFY_LINKEDIN_ACTOR_ID` (opt but load-bearing differently from the others -- LinkedIn ships with NO default actor, so this platform stays fully disabled, never even calling Apify, until this is explicitly set; most exposed to scraping-detection/ToS enforcement of the six). Reuses the platform's existing `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` for identification, vision analysis, and the cross-platform synthesis report, and the existing `OPENAI_API_KEY` (already set for other agents) for Whisper audio transcription of video posts -- no new vendor account needed for either.

---

## HOW TO WORK ON THIS (proven-safe workflow, reinforced this cycle)

1. **Clone fresh into the bash sandbox each session.** Sandbox network: `git` over `github.com` works; `api.github.com`, most external APIs are blocked (**Apollo's free MCP tools DO work and were used extensively for live filter verification - 0 credits, no confirmation needed for `apollo_mixed_people_api_search`; paid Apollo MCP endpoints need explicit confirmation before spending from the shared pool**). WebSearch/WebFetch work.
2. Edit via file-edit tools or Python string-replace scripts (assert exactly-one match).
3. **Validate before every push, in this order:**
   - `PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q` (must be 1,562+ passing)
   - **mutation-test the actual fix** with genuinely distinguishing data, break each fixed line, confirm a test dies, restore
   - `python3 -c "import ast; ast.parse(open('app.py').read())"` (and any other changed `.py`)
   - import the app to catch route collisions
   - `node --check` any changed JS bundle (or extracted inline `<script>` blocks for a Jinja template)
   - **for a UI-only change, live-verify via a temporary preview-login route, screenshot both themes, then remove the route and confirm zero diff on `app.py` before committing**
   - no em dashes on added lines
4. **Push once validated, without asking each time** - this is a standing instruction from the user ("Yes. Always push."), not a one-off. Still report what shipped afterward, including the commit hash. Push URL = `https://<TOKEN>@github.com/ai-positon2/intelligence-platform`. **Redact the token in ALL visible output:** `sed -E 's#ghp_[A-Za-z0-9]+#[REDACTED]#g'`. **The user pastes a fresh classic PAT each session and it must be rotated afterward - flag this every single push, it has now been reused across many consecutive sessions.**
5. **If a multi-agent background review hits an org-wide API spend limit mid-task, it can simply be re-launched once the limit resets, same session** - this has happened before and recovered cleanly; don't treat it as a dead end.
6. **Never use an em dash in any written copy**, anywhere. Use commas, colons, periods, parentheses.
7. **A shared signal/category/label reused across client accounts gets a per-account override parameter, never a global rename.**
8. **When a user reports something is "still not fixed", re-measure the EXACT reported symptom empirically** rather than assuming your first fix must have been under-deployed or insufficient.
9. **Never commit `data/identity_graph.db`.** Never put personal or sensitive data in URL parameters or query strings.
10. **When hiding (not deleting) a listed agent, check for hand-written HTML in addition to any registry/set-based filter** - `HIDDEN_AGENT_SLUGS` drives three surfaces automatically but card grids and command-palette entries are hand-written and need a separate edit.
11. **A logo/icon service that "isn't reachable from this sandbox" may actually be broken in production for everyone** - confirm the failure mode (universal API deprecation vs. a genuine sandbox-only network block) before writing off a fix as unverifiable; `logo.clearbit.com` turned out to be the former this cycle, and DuckDuckGo's icon service was both fixable and verifiable from the same sandbox.
12. **When a persisted spreadsheet cannot be shared with a service account due to an org sharing policy, a manual, re-runnable `.xlsx` import script that the fetcher falls back to (per-list, not all-or-nothing) is a legitimate stopgap** - it self-heals to live data the moment the real access is fixed, with zero further code changes, as long as the fallback is wired at the point of use rather than as a one-time seed.

### Gotchas (unchanged, still true)

- `templates/context.html` (Playbook), `templates/linkedin_scraper.html` (LinkedIn Intelligence), and the entire `company_people_intelligence` naming for Contact Finder are filename remnants of renamed features.
- The Contact Finder JS bundle is wrapped in an **IIFE**: only `window.cpi*` functions are reachable externally.
- `admin.css` loads last and overrides inline admin CSS. Similarly, `aurora-app.css` (shared page chrome) loads AFTER a page's own stylesheet on several pages, so a page-scoped CSS override needs combined selectors and/or `!important` to reliably win regardless of `<link>` order - this bit the Job Change Alert scrollbar fix this cycle.
- A flex item that must shrink below its content needs its own `min-width:0`.
- A CSS rule can silently win over another of equal specificity purely by being declared later.
- Never put `{{`, `{%` or `{#` inside `<style>`/`<script>`.
- Python's `csv.writer` default `lineterminator` is `\r\n` regardless of how the file was opened.
- Flask's `render_template` caches compiled templates process-wide - a template edit needs a dev-server restart to show up locally, separate from any browser HTTP cache on its linked CSS/JS (which needs its own `?v=N` cache-buster bump). Both traps together are easy to mistake for "the fix isn't working."
- macOS sandbox has no `timeout` command; `zsh` does not word-split unquoted variables.

---

## OPEN ITEMS / TODO

1. **Rotate the GitHub token.** Used for many consecutive pushes across many sessions now, pasted into chat each time. This is the one piece of cleanup the assistant cannot do itself; flag it every session.
2. **Restore LinkedIn Social Researcher (formerly named LinkedIn Strategy Researcher, renamed 2026-08-20) to the listings** when the owner asks - full itemized checklist above and in `[[project-lsr-hidden]]`.
3. **Set `ANTHROPIC_API_KEY` on Railway** to activate Contact Finder's Claude cross-check - a pure accuracy upgrade sitting inert.
4. **Fix `SLACK_BOT_TOKEN`'s scope/membership for `#job_change_alert_apollo`**, and **add `SLACK_BOT_TOKEN` as a GitHub Actions repo secret**, so Job Change Alert's daily sync actually pulls new events instead of relying entirely on the backfill.
5. **Fix the Job Change Alert tracked-roster Google Sheet's sharing policy block** (Workspace admin allowlist, or re-share from a personal account) - currently running off a manual `.xlsx` snapshot stopgap that needs periodic re-export.
6. **Contact Finder's chat path has never run against live OpenAI + Apollo keys end to end.**
7. **Contact Finder residuals:** the 120-row history cap silently truncates a paged search; zero-result searches are never saved to history; `_cpi_probe_company_free` guesses only `.com`.
8. **Apollo's advanced-filter-requires-plan-upgrade risk** (founded-year range, department headcount on people search) is unconfirmed against production's actual `APOLLO_API_KEY` plan tier, and `_post()`'s 422 retry logic isn't hardened against it.
9. **Contact Finder's external client launch** is planned but not scheduled - the shared-credit-pool/`/credits`-aggregate cross-tenant exposure needs a decision once real external auth is designed.
10. **Hardcoded counts still in the codebase:** `ACCOUNTS["healthcare"]["description"]`'s hardcoded "1,251", four places in `templates/agents.html`.
11. **Signal refresh secrets (blocking Healthcare refresh):** set GitHub Actions `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`.
12. **Agent roster will drift again**, now across four+ mechanisms. Consider deriving them if the roster changes materially again.
13. **Fully connect the `/app` "Competitor Analysis" placeholder** once the live SEO Studio tool's per-client data scoping is addressed.
14. **Assign real agents to more `/app` + client cards.**
15. **NorthStar client-side portal adoption is minimal.** A relationship conversation, not a code fix.
16. **`data/identity_graph.db` is on Railway's ephemeral disk.** Move to a persistent volume or Postgres.
17. **Cold-visitor identification** needs a licensed identity feed. Plug point ready.
18. **Light-theme polish** on heavy custom inline pages generally (Job Change Alert's own light theme was explicitly verified this cycle and is in good shape; this item is about the remaining older pages).
19. **ABM Signal Tracker maintenance mode:** periodically prune `data/northstar_signals_manual.json` by `signal_date` and re-run `seed_northstar_signals.py --prune`.
20. **An open, unverified accuracy question for Job Change Alert:** a third-party Coresignal-backed skill (Swan) reportedly catches job changes Apollo's own alert misses - nothing built on this, flagged only.
21. **Advisory security/design audit (do not start without an explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID`, cookie flags, HSTS/security headers, CSRF, rate limiting (note: Contact Finder now has a basic per-process one, the rest of the app has none), SSRF/`X-Forwarded-For` hardening; CSS token convergence, accessibility.

---

## COMPETITOR / ROADMAP (recorded, not built, unchanged)

Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala. Gaps: co-op topic intent, review-site intent, technographic change, champion job-change (**partially closed by Job Change Alert this cycle, though scoped to new-role-only, no prior-employer data**), hiring-surge, earnings/10-K mining, event attendance, layoffs, PLG usage. Differentiators: generative-search/AI-answer visibility + agency execution + first-party web de-anon with a real engine + a working, deeply-audited (13 rounds now) Apollo contact-finding surface with honest credit accounting + **a live, Slack-sourced job-change detection feed with an honest scope disclosure about what it can and cannot say.**
