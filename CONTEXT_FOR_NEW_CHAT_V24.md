# Intelligence by Position2 - Full Context (v24 - August 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v24 supersedes all earlier context files (v1-v23)** - older versions are stale; ignore any pasted copy, and if `CONTEXT_FOR_NEW_CHAT_V23.md` (or older) still exists in the repo root, delete it as part of landing this file per the standing one-canonical-file convention.

**Latest `main` HEAD at the end of this cycle: `4a9eace`** (always `git pull` to confirm; Railway auto-deploys every push). `app.py` is **15,733 lines / 147 `@app.route` decorators + loop-registered `add_url_rule` families**, up from 14,725 lines / 143 routes at v23. The test suite is now **48 files, 1,434 tests, all passing** (`PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q`), up from 28 files / 1,122 tests at v23 - the growth is almost entirely three more rounds of Contact Finder auditing (below). Contact Finder's own JS bundle is now **2,995 lines** (was 2,068) and its template is **670 lines** (was 585).

---

## WHAT V24 ADDS ON TOP OF V23 (this cycle's work)

This cycle was four separate asks in one continuous session, in order:

1. **LinkedIn Strategy Researcher was pulled from every listing, deliberately not deleted** (`5dfb214`). The owner asked for it back to be temporarily unlisted (`/p2/b2b-agents`, `/app`, and the NorthStar client portal), expecting to ask for it restored "in a few days." Nothing underneath was touched: the route, the watchtower embed, the `APP_AGENTS_BY_SLUG` entry, and NorthStar's own agent ordering are all intact - only the listing surfaces hide it, via one new set, `HIDDEN_AGENT_SLUGS = {"linkedin-strategy-researcher"}`, plus two hand-written HTML spots (the b2b_agents card and its Ctrl+K palette entry) that don't derive from that set and had to be commented out separately. See the dedicated section below - **restoring this is a known, itemized, near-term task**, not speculative cleanup.
2. **A second agent, "Competitor Analysis," went live on the separately-hosted SEO Studio app** (`https://seo-apps-production-37a6.up.railway.app/competitor-analysis`, real SEMrush data, confirmed live by visiting it), and was wired into the internal `/p2/seo` SEO Suite listing (`18810ad`) - 16 SEO tools now, not 15. One step later the same day, the *dormant, unrelated* `/app` placeholder that already existed under the same display name ("Competitor Analysis," slug `competitor-seo-intelligence`, a different registry entry that happens to share a name) had its Request Access button turned back on (`fb89ad7`) - but was deliberately **not** connected live, because the real tool's client picker shows every client's data (Tealium, 42ND, YBH, Beta Bionics, ...) with no visible per-member scoping, which would be a cross-client data leak for any signed-in Google account, not just an unfinished feature. See the dedicated section below.
3. **A five-part live-verified audit of every Contact Finder search and filter** ("check everything works, no bugs"), which is the bulk of this cycle. Found and fixed **six real bugs across three commits** (`d1d62bd`, `fd8cdcc`, `4a9eace`), all documented in detail in the Contact Finder section below, plus closed out every remaining open question about which Apollo filters are strict vs. loose. **The org hit its monthly API-spend limit mid-audit and all four parallel background review agents died before returning findings**; they were re-launched successfully once the limit reset, in the same session - worth knowing this can happen and is recoverable, not a dead end.
4. **This context-file refresh itself** (V23 -> V24), on explicit request, alongside a memory-file update.

**The recurring defect class this whole platform's Contact Finder work keeps finding, restated because it produced three more real bugs this cycle: a surface asserting something its data does not support.** Two of six new bugs this cycle were fresh instances of it (a transport failure treated as "Apollo has nothing," an if/elif chain that silently underreported how many rows a second filter genuinely excluded); the other four were adjacent correctness bugs surfaced by the same disciplined audit (a stale-entity-variable UI bug, a dead "Remove that filter" button, an uncaught type-coercion crash, a phantom credit charge on cache hits). See the Contact Finder section for all six with full detail - they are worth reading in full if touching that feature, because each one is a small masterclass in a way this codebase's own data can lie by omission.

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 = a B2B digital-marketing agency: SEO/organic, performance/paid media, paid social, content, brand/website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, anesthesiologist/creative hiring, news), de-anonymizes website visitors to company and (where a signal exists) person, **finds and enriches contacts at target companies via Apollo**, scrapes LinkedIn engagement, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), helps reps act via an embedded AI assistant (**Vimi**, visible label **GTM**), and serves **co-branded client portals** that can also embed **agents built entirely on other platforms.**

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
| **3. Internal staff app `/p2/*`** | `@position2.com` only | `@position2_required` | `/p2/*` (hub, b2b-agents, seo, admin, playbook, ...) | light/dark toggle |
| **4. Client portals `/<slug>`** | any signed-in Google account | `_client_gate()` | `/<client-slug>/*` (e.g. `/northstaranesthesia`) | dark, co-branded |

- After login: `@position2.com` -> `/p2/hub`; any other signed-in user -> `/app`.
- Old top-level internal paths (`/hub`, `/gtm/...`, `/admin/...`) 301-redirect to `/p2/...`, and `/p2/gtm/*` 301-redirects to `/p2/b2b-agents/*`.
- **Standing rename rule:** when a persisted URL/slug is renamed, the old one keeps 301-redirecting AND every read path keyed off the old slug is aliased to the new one. A past bug dropped historical runs because only routing was fixed, not the read side. See `[[feedback-persisted-identifier-renames]]`.
- Auth decorators in `app.py`: `login_required`, `admin_required` (= position2 + admin email), `position2_required`. Client gating is `_client_gate(client)` (not a decorator).

---

## LINKEDIN STRATEGY RESEARCHER - HIDDEN, NOT DELETED (this cycle, `5dfb214`)

**Pulled from every listing on 2026-08-14 on explicit request; the owner expects to ask for it back "in a few days."** Nothing underneath was deleted or disabled: `/p2/b2b-agents/linkedin-strategy-researcher` still resolves, the watchtower tool it embeds still loads, `APP_AGENTS_BY_SLUG` still holds the full entry so past agent-run history still renders a real name, and NorthStar's own ordered `agents` list in `CLIENTS` is completely untouched so the agent returns to its original position the moment it is un-hidden.

**To restore, in this exact order** (this is a known near-term task, not hypothetical):
1. Empty `HIDDEN_AGENT_SLUGS` in `app.py` (one set; it drives `/app`'s main grid, the `/app` sidebar via the `app_agents` context processor, and every client portal through `_client_agents` - three consuming surfaces from one flag).
2. Un-comment the card in `templates/b2b_agents.html` **and** its Ctrl+K command-palette entry lower in the same file - **these do NOT derive from `HIDDEN_AGENT_SLUGS`**, they are hand-written HTML, and the hide comes apart if only one half is done. The commented-out card carries its own restore note for this reason.
3. Put `templates/hub.html`'s B2B card back to `7` dashboards / `6` live, the `data-lxn` band back up (it went from 21 to 20 to 21 again this cycle for unrelated reasons - see the SEO Suite section below, so re-verify the arithmetic against whatever the count is at restore time rather than assuming 21), and the card description back to "LinkedIn engagement **and strategy research**."
4. Restore `templates/context.html`'s "4 live agents ... LinkedIn Strategy Researcher" and "(unlimited on LinkedIn Strategy Researcher)" lines.
5. `tests/test_hidden_agent_withdrawal.py` asserts the hidden state (15 tests) - flip it to a restored-state test or delete it at the same time as step 1-4, not before.

**Deliberately left alone both when hidden and worth re-confirming at restore time:** the public marketing directory (`AGENTS` in `app.py`, driving `/agents`/`/platform`/`/`) never had an LSR entry at all, so there is nothing to touch there either way. `templates/context.html` still names LSR in three other spots (a GTM persona paragraph, a "LinkedIn Strategy" chip, an every-agent explorer JS array) that were reported to the owner but intentionally not changed, since the original hide request named three specific pages and these are a fourth.

**How to apply:** see `[[project-lsr-hidden]]` for the full restore checklist with test names, and the agent-roster-drift section below for why this needed two different mechanisms (a set plus hand-written HTML) in the first place.

---

## SEO SUITE - COMPETITOR ANALYSIS ADDED, AND A SEPARATE `/app` PLACEHOLDER OPENED FOR REQUESTS (this cycle, `18810ad`, `fb89ad7`)

**Two distinct things happened under the same display name, on two different registries, one day apart - keep them straight:**

1. **A real, live tool: "Competitor Analysis" on the SEO Studio SERP app**, confirmed live via browser visit (`https://seo-apps-production-37a6.up.railway.app/competitor-analysis`, real SEMrush-backed traffic/keyword/backlink/authority comparisons, a client picker). Added to `_SEO_TOOLS_FALLBACK` (the internal `/p2/seo` staff SEO Suite listing, now **16 tools**, slug `competitor-analysis`). `_seo_tools()` prefers a live `/tools.json` manifest fetch from the SERP app and only falls back to this hardcoded list when that fetch fails - confirmed live that the fetch **always** fails currently (the SERP app is a client-routed SPA with no such JSON endpoint), so this fallback list is the actual, only source of truth for what `/p2/seo` shows today, not a fallback in the rare-case sense.
2. **A completely separate, pre-existing, dormant `/app` placeholder card** with the coincidentally identical name "Competitor Analysis" (`APP_AGENTS` slug `competitor-seo-intelligence` - note the different slug from the real tool's `competitor-analysis`). This card had `no_request: True` (blocking even a request-access click) since a much earlier cycle when the tool it described didn't exist yet. Since the tool now genuinely exists, `no_request` was removed (`fb89ad7`) so members can ask for access again - **but the card was deliberately left unconnected** (no `seo_slug`/`external_url`), because the real tool's client picker exposes every client Position2 runs it for with no visible per-member access scoping; opening it to any signed-in Google account without a staff review step in between would be a genuine cross-client data leak, not just an unfinished feature. `lock_label: "Need extensive testing"` was left in place for the same reason. This was a judgment call made and explicitly surfaced, not silently decided.

Both additions are covered by `tests/test_seo_competitor_analysis.py` (8 tests: SEO Suite listing, live embed, 404 handling, the `/app` placeholder's request-flow behavior and its deliberate non-connection).

---

## CONTACT FINDER (the biggest feature, and where nearly this entire cycle's audit work landed)

**Route base:** `/p2/b2b-agents/company-people-intelligence` (the slug was never renamed when the display name changed from "Company & People Intelligence" to "Contact Finder"; the URL, the Python function prefix `cpi_*`, the helper prefix `_cpi_*`, the JS file, the CSS file and the template all still say `company_people_intelligence`). Staff-only (`@position2_required`).

**Files:** `app.py` (all `cpi_*` routes and `_cpi_*` helpers), `tracker/apollo_client.py` (971 lines, the actual Apollo API client - `search_people`, `search_companies`, filter-building, all the domain/industry enforcement logic), `templates/company_people_intelligence.html` (670 lines), `static/js/company_people_intelligence.js` (2,995 lines, wrapped in an IIFE so **only `window.cpi*` functions are reachable from outside**), `static/css/company_people_intelligence.css`.

### The routes and the credit model (unchanged this cycle, restated because it's the design constraint everything follows from)

Apollo's **search** endpoints are free; only **enrichment** spends credits from one shared agency account:

- `mixed_people/api_search` and `mixed_companies/search`: **0 credits**. All browsing, filtering, counting and vocabulary learning runs here.
- `people/match`, `people/bulk_match`, `organizations/enrich`: **1 credit per record**. Only explicit user action reaches these.
- `people/bulk_match` is capped at 10 per request by Apollo and 50 ids per bulk reveal by us.
- Every code path that can spend threads a `spend = {"credits": 0}` dict through and the response carries `credits` back to the UI.
- Multiple caches exist purely to avoid paying twice: person profiles (version-stamped, 90-day positive TTL, in-process `_PE_MEM` then Postgres `person_enrichment`), company-name resolution (Postgres, survives a deploy), employer firmographics (`_CPI_FIRMO_CACHE` + `_cpi_firmo_db_read/write`, 30-day TTL).
- **Apollo's `organization_headcount_twelve_month_growth` is a FRACTION** (0.19 = 19%, 1.5 = 150%) - settled from repo evidence, still not verified against a live production probe (the free tier strips this field entirely).

### THIS CYCLE'S SIX BUGS, in the order found and fixed - read these in full before touching search/filter code

**Bug 1 - `search_companies`'s own domain filter dropped companies Apollo returned with no domain field at all** (`d1d62bd`). `search_people`'s identical bug had been fixed one commit earlier in a prior session (splitting `company_dropped`/`company_unconfirmed`), but nobody had mirrored the fix onto `search_companies`. Worst case: a domain-scoped company search where Apollo's own returned row lacks a domain is exactly the single-company-search scenario, and the one company being searched for could vanish outright. Fixed the identical way: `domain_dropped` (confirmed different domain, still dropped) split from `domain_unconfirmed` (no domain returned at all, kept and flagged `domain_unconfirmed=True` on the row). **Named distinctly from the people-side `company_dropped`/`company_unconfirmed`** because `_CPI_VERIFY_LABELS["company"]` reads "working somewhere else" - correct for a person's employer, nonsense for a company row - so `cpi_search` folds the count into the same `company_unconfirmed` response field but reports the drop under its own `"domain"` reason and label ("a different company at that domain"). The grid badge (`coCell`), card view (`companyCard`) and JS state (`STATE.companyUnconfirmed`) already generalized across both entity types, so this needed one shared badge tweak, not a parallel UI. Same commit also gave `exclude_keywords` (the company search's client-side keyword-exclusion post-filter, since Apollo has no native text-exclusion param) a drop count it never reported - the one filter on that page that could silently narrow a page with zero reason shown.

**Bug 2 - `_cpi_attach_employer_facts` called `search_companies` without `strict=True`** (`fd8cdcc`). A transport failure during the paid employer-lookup batch (up to 50 companies' industry/headcount/revenue/HQ/technology fetched in one call) was indistinguishable from "Apollo has nothing on these companies," and `_cpi_verify_rows` then dropped every row waiting on that data under a specific, **false** reason - "outside the industry," "headquartered elsewhere" - for a question Apollo never actually answered. Fixed with an `orgs = None` sentinel that distinguishes "the fetch itself never came back" (`stats["fetch_failed_ids"]`) from a bug in the processing loop after a real answer arrived. `_cpi_verify_rows` returns a **third value** now (`employer_unavailable` count) and skips every employer-dependent check for a row flagged `employer_lookup_failed`, keeping it instead of rejecting it under a reason nothing checked; the title check (which doesn't need employer data) still runs for these rows. **Signature/contract note: `_cpi_verify_rows` returns a 3-tuple, not 2 - grep every call site before adding a new one.**

**Bug 3 - `cpiOpenDetails` read `STATE.entity` instead of `STATE.shownEntity`** (`fd8cdcc`). This is the one row-describing call site that the earlier `STATE.entity`/`STATE.shownEntity` split (a structural fix from a prior audit cycle, splitting "what the next search looks for" from "what the rows on screen actually are") missed. Flip the People/Companies toggle without re-searching, click a still-visible old row's Details modal, and it renders as the wrong entity type - a company profile built from a person row, or vice versa.

**Bug 4 - the "Remove that filter" button for an HQ rejection always cleared `company_locations`** (`fd8cdcc`), but `_cpi_verify_rows` checks HQ against a *different* filter key, `locations`, on the Companies tab - so on that tab the button cleared nothing and re-ran the identical search, reproducing the identical rejection while telling the user it had been removed. `REJECT_FILTER["hq"]` can't be one fixed key when the underlying filter name differs by entity; resolved via a shared `rejectFilterKey()` JS function that both the button-rendering code and the button's own click handler read, so the two can no longer disagree about which reasons are clickable. **This is the second time an entity-specific reason existed and only one of two functions got updated for it - watch for a third the next time an entity-conditional filter key is added.**

**Bug 5 - numeric range filters (`employee_min/max`, `revenue_min/max`, ...) reached `_cpi_num_in_range` with no type coercion** (`fd8cdcc`). A non-numeric value raised a bare `TypeError` that `cpi_search`'s own broad `except Exception` caught and reported as "Apollo did not answer this search... Try again in a moment" - a validation bug wearing an outage's clothes, worst because retrying fails identically every time. Not reachable from the shipped filter panel today (`numVal()` always sends real numbers) or from `cpi_parse_query` (its `allow` list has no numeric keys at all), but cheap to close off for any future caller: cast every numeric filter key through `_cpi_int_or_none`, already used for the chat intent parser's own numbers.

**Bug 6 (two findings, both fixed same day on request after being explicitly deferred once) - `4a9eace`:**
- **`_cpi_enrich_person`'s email branch over-billed on a cache hit.** It added 1 credit to `spend` any time `_enrich_people` returned `matched: true`, without checking whether that match came from `_enrich_people`'s own two-tier cache (in-process `_PE_MEM`, then Postgres) or a fresh, billed Apollo call - both return the identical shape. The `apollo_id` branch three lines below was already guarded against exactly this. Fixed by checking the same two caches `_enrich_people` itself consults, in the same order, before calling it, and only billing when neither had the email. **Currently unreachable from the shipped UI** (`email` is always `""` at both call sites today) - a landmine closed before anyone wires an email-based enrich control into the UI, not evidence the path is now exercised.
- **`_cpi_verify_rows`'s if/elif chain attributed a row's rejection to only the FIRST filter it failed**, undercounting how many rows a second filter was genuinely also responsible for excluding (a row both wrong-industry and undersized only ever showed up under "industry"). Fixed by checking every condition independently and tallying a row under every reason it fails - which means `dropped`'s per-reason values can now legitimately **sum to more than the true number of rows removed** (one row counted in two buckets). This was flagged as a real redesign risk the first time and handled carefully: `cpi_search` now captures an actual before/after row count (`verify_dropped_rows`) per `_cpi_verify_rows` call and computes `rejected_total` from that, **never** from `sum(rejected.values())`; the JS mirrors this with `STATE.rejectedTotal` read straight off the server response, replacing `rejectedReasons()`'s old self-sum. **Contract note for whoever touches this next: `rejected`'s values are NOT a partition of the rows removed and must never be summed for a total - only `rejected_total` (or `len(rows)-len(kept)` at the `_cpi_verify_rows` level) is that number.**

### A testing lesson worth internalizing from this cycle

**A mutation test needs genuinely distinguishing fixture data, or it proves nothing.** While fixing bug 6's second half, the first attempt at a Node-harness mutation test (`test_cpi_dashboard_behaviour.py`) passed against BOTH the fixed code and the deliberately-reintroduced bug, because the test fixture's per-reason counts happened to sum to exactly the same number as the real total (18+6=24, matching `total:24`) - so a self-sum and a server-supplied total produced identical output by coincidence. The fixture had to be rewritten with genuinely overlapping counts (18+8=26 summed, but only 20 rows actually removed) before the mutation test could tell the two implementations apart at all. **When mutation-testing a fix to how numbers are aggregated, deliberately pick fixture data where the buggy and fixed computation would diverge - don't reuse whatever numbers were already in a test.**

### Also live-verified this cycle and confirmed NOT bugs (closes out every open filter-behavior question)

Live probes against the free `mixed_people/api_search` endpoint (0 credits, no confirmation needed), baseline = 355 people at betabionics.com, method = one filter alone plus a negative control, compared against the unchanged baseline:

- `contact_email_status`, `market_segments`, `q_keywords`, `q_organization_job_titles`, `organization_job_locations` (the `job_locations` filter), `currently_using_all_of_technology_uids` (`technologies_all`), and `currently_not_using_any_of_technology_uids` (`exclude_technologies`) **all filter strictly on Apollo's own side** - no local re-verification gap, matching the existing strict-filter list (`person_seniorities`, `organization_naics_codes`, `organization_locations`, `currently_using_any_of_technology_uids`, `organization_num_employees_ranges`, `revenue_range`, `person_total_yoe_range`, `person_days_in_current_title_range`, `person_locations`, strict `include_similar_titles=false`, and `q_organization_keyword_tags` with OR semantics).
- `job_locations` is correctly labeled "Hiring in" in the UI, not confusable with employer HQ location.
- The department-headcount dropdown's 14 values match Apollo's exact valid key set.
- Since `technologies_all`/`exclude_technologies` are confirmed strict, the only real gap was cosmetic: neither key was in the `needs_employer` list that forces the paid company-detail lookup on, so their tech-stack badge never rendered even on a correct match. Fixed, one line, same commit as bug 5.
- **Still unconfirmed against production, flagged not fixed:** Apollo's own tool schema documents `organization_founded_year_range` and `organization_department_or_subdepartment_counts` as "Advanced filter: free plans receive an upgrade-required error" for PEOPLE search specifically (not company search) - both are reachable from this page's UI (Founded year, Dept. headcount). A probe against the connected MCP account with permissive bounds returned the unfiltered baseline with no error, which doesn't rule out production's own `APOLLO_API_KEY` behaving differently on a stricter plan. If it ever does 422, the existing `strict=True` + generic except already reports `search_failed: true` rather than a false "no results" - the one gap left is that `_post()` retries a 422 three times with backoff before giving up (a non-retryable client-side rejection, unlike 429/5xx), and the message doesn't distinguish "this filter needs a plan upgrade" from "try again in a moment." Not fixed - watch for it, and fix `_post()`'s retry logic if it ever actually fires.

### Full audit history (ten rounds now, each one commit plus one test file whose module docstring states the defects in plain language)

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
| 9. All searches/filters, companies mirror + 4 more | `d1d62bd`, `fd8cdcc` | `test_cpi_domain_unconfirmed_audit.py` (extended), `test_cpi_employer_facts_outage_audit.py`, `test_cpi_hq_relax_entity_audit.py`, `test_cpi_numeric_filter_coercion_audit.py`, `test_cpi_credit_toast_pluralization_audit.py` | companies never got the domain-unconfirmed fix its person-search twin got; a fetch outage read as a false rejection; a stale entity variable; a dead relax button; an uncaught type error |
| 10. The two deliberately-deferred findings | `4a9eace` | `test_cpi_enrich_email_credit_audit.py`, `test_cpi_verify_rows_multi_reason_audit.py` | a cache hit billed like a fresh purchase; a reason-count undercount that risked becoming an overcount if fixed carelessly |

**The pattern, stated once more:** a surface asserting something its data does not support. An empty result reading as a fact about the world rather than a fact about the request. A tab toggle doubling as a claim about what is on screen. A header promising a percent over a column holding a fraction. A fetch that failed read as a fetch that succeeded and found nothing. When auditing any other flow in this app, that is the thing to look for first, and the way to find it is to ask what makes that particular flow different from the others.

### Known open items inside Contact Finder (mostly unchanged from v23, plus this cycle's additions)

- The **chat path has never been exercised against live OpenAI/Apollo keys** end to end (sandbox network blocks both; only the free `mixed_people/api_search` was ever called, via the connected Apollo MCP).
- The **120-row history cap silently truncates a paged search**.
- **Zero-result searches are never saved to history.**
- `_cpi_probe_company_free` **guesses only `.com`** when deriving a domain from a name.
- **Apollo's advanced-filter-requires-upgrade risk for `organization_founded_year_range`/department-headcount on people search** (see above) - unconfirmed against production, `_post()`'s 422 retry behavior not hardened.
- **`_cpi_verify_rows` now returns a 3-tuple and `rejected`'s per-reason values are not summable for a total** - both are new contract facts as of this cycle; re-read the docstrings before modifying either function.

---

## TESTING DISCIPLINE (unchanged in method from v23, reinforced this cycle)

```bash
cd <repo> && PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q
```

- **Always set `PYTHONDONTWRITEBYTECODE=1` / use `python3 -B`.** macOS system Python writes `.pyc` files outside the repo, which once let a mutant survive a source restore and silently invalidated a mutation run.
- **Mutation testing is the gate, not the suite passing.** After writing tests for a fix, deliberately break each fixed line one at a time and confirm a test dies, then restore. Every one of this cycle's six bugs was verified this way.
- **A mutation test needs fixture data where the buggy and fixed behavior actually diverge** (new this cycle, see the Contact Finder section above) - coincidentally-matching numbers can make a test pass identically against both versions of the code.
- **Text assertions on a JS bundle cannot distinguish a working guard from a disabled one.** `tests/test_cpi_dashboard_behaviour.py` shims `document`/`window`/`fetch`, `eval`s the real bundle in node, and asserts on the HTML/requests it actually produces. It skips (not fails) when node is unavailable.
- Do not write change-detector tests. If a mutant is unreachable in practice, say so in the commit message instead of pinning it.

---

## APOLLO PERSON ENRICHMENT ON EXTERNAL USAGE

Unchanged this cycle. Lives at `/p2/admin/external-usage` (see `[[project-person-enrichment]]`). Clicking a person opens a profile modal with Apollo-sourced identity, employment and contact details, labelled outbound links, and an AI read of the person. New members are auto-enriched. There is an Excel export. When Apollo cannot match a person, the flow falls back to enriching their company by email domain rather than showing nothing. **This shares `_enrich_people`'s cache with Contact Finder's chat-side name reveal** (see bug 6 above) - both read the same `_PE_MEM`/`person_enrichment` table, so a change to one's cache semantics affects the other.

Two hard-won points that generalise: **validate response shape, not just HTTP status** (a 200 with an empty payload was once cached as "this person has no data," poisoning the negative cache); **version-stamp every enrichment cache** so a fixed bug can self-heal rather than serving its own old wrong answers forever.

---

## THE NORTHSTAR ABM SIGNAL TRACKER (complete, maintenance mode, unchanged this cycle)

- **All 35 companies researched across 7 batches; 71 curated signals** (52 HIGH / 16 MEDIUM / 3 LOW). No pending batches.
- Data lives in SQLite (`data/tracker_northstar.db`), populated from `data/northstar_signals_manual.json` by `seed_northstar_signals.py`. **Always run with `--prune`**: without it the seed is insert-only. The JSON is the source of truth.
- **`_quality_bar` inside that JSON is the living curation policy.** Permanent **6-month admission cutoff by `signal_date`**, plus fixed categorization conventions - see `[[feedback-signal-relevance-bar]]`.
- **Canonical `signal_type` strings** are exact-match constants shared across `signal_score.py`, `dashboard_builder.py` and the seed data. A near-miss silently drops the signal from every KPI/chart/filter without erroring.
- **"Creative Hiring" displays as "Anesthesiologists" for NorthStar only**, via a per-account `hiring_opts` override. The stored `signal_type` never changes. Reuse this pattern; never globally rename a shared category.
- `reports/dashboard*.html` are **built artifacts committed to git**. Never hand-edit them.

---

## SURFACE 4 - CLIENT PORTALS

Per-client co-branded front door at `/<client-slug>`. Currently one client: `northstaranesthesia`. Unchanged this cycle apart from `HIDDEN_AGENT_SLUGS` now also filtering `_client_agents(client)` (see the LSR section above).

- **`CLIENTS` registry** entry fields: `slug`, `name`, `short`, `website`, `logo`, `domains`, `accent`/`accent2`, `tagline`, `blurb`, `agents` (ordered APP_AGENTS slugs), `dashboards` (agent-slug -> pre-built static HTML), `linkedin_sheet`, `external_tools` (agent-slug -> full external URL to iframe).
- NorthStar: `domains=["northstaranesthesia.com"]`, `accent="#5b9dff"`, agents list currently shows 5 live (was 6 before LSR was hidden this cycle - it returns to 6 when LSR is restored, no code change needed there since `CLIENTS["northstaranesthesia"]["agents"]` was never touched).
- Sign-in to the portal is open to any Google account.
- **Three agent types, all keyed off the same `agents` list:** SERP-connected (`seo_slug`, run-metered via postMessage), dashboard-backed (`is_dashboard`, shown Live, never metered), external-tool (`is_external`, iframed, run-metered on a real postMessage signal).
- `_client_agent_view(slug, client)`: **always pass `client`**. Omitting it silently resolves `connected=False` for an external-tool-only agent and 400s the log-run endpoints.

---

## THE EXTERNAL-TOOL PATTERN

An agent whose entire backend lives on a third-party AI app-builder platform we have no access to; we get a public URL.

1. Confirm no `X-Frame-Options`/CSP `frame-ancestors` blocks iframing.
2. Add it to `APP_AGENTS` with no `seo_slug`.
3. Add its slug to the client's `agents` list and its URL to `external_tools` (client portal), or add a small route rendering `templates/embed.html` (internal).
4. `client_embed.html` / `embed.html` iframe it; the address bar shows OUR path.
5. **Metering requires the external tool's cooperation.** It must call `window.parent.postMessage({source:'p2-agent', type:'agent-run-started'|'agent-run-finished'}, 'https://intelligence.position2.com')`, guarded by `if (window.parent !== window)`. Deployed and working for LinkedIn Strategy Researcher (currently hidden from listings, see above, but the mechanism itself is untouched).
6. **Any prompt written to be pasted into that other platform must be self-contained** and describe only that tool's own observable behaviour, never our internal routes, slugs, or architecture.

---

## LINKEDIN INTELLIGENCE (internal + per-client, multi-sheet, unchanged this cycle)

Route `/p2/b2b-agents/linkedin-intelligence`. Renders `templates/linkedin_scraper.html`; all content drawn client-side by `static/js/linkedin.js`. One row per person x post engagement, header-mapped.

- `_fetch_linkedin_intel_data(force, sheet_id)` with **per-sheet caches** so internal and each client portal read independent sheets.
- **Do not confuse** LinkedIn Intelligence (your own engagement data from a Sheet) with LinkedIn Strategy Researcher (external competitive analysis, currently hidden from listings) or with the Signal Tracker's own News Mention/Partnership categories.

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

---

## ARCHITECTURE

```
intelligence-platform/
├── app.py                ← Flask server (~15,733 lines, 147 routes + loop-registered families):
│                            auth (3 decorators + client gate), all 4 surfaces, AGENTS/APP_AGENTS/
│                            SIGNALS/INDUSTRIES/CLIENTS/ACCOUNTS registries, HIDDEN_AGENT_SLUGS,
│                            OpenAI (Vimi x2 + Contact Finder's own chain), Contact Finder's cpi_*
│                            routes and _cpi_* helpers, marketing routes, /api/demo-request,
│                            /api/track|atrack|identify|whoami, /app/* + run history,
│                            /p2/* + admin analytics, client-portal routes, LinkedIn Intelligence
│                            (per-sheet), Postgres history. Does NOT build Signal Tracker
│                            dashboards, just serves the pre-built HTML.
├── tracker/apollo_client.py ← Apollo API client, 971 lines: search_people, search_companies,
│                            all filter-building + domain/industry enforcement logic (see
│                            Contact Finder section - this is the file most of this cycle's
│                            bugs lived in).
├── tests/                ← 48 files, 1,434 tests. Most are named test_cpi_*.py, one per audit.
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
│   │        grid-tokens, client-portal, company_people_intelligence.css)
│   ├── js/ (theme, linkedin, visitor_track, pfx_bg, aurora, anonymous_visitors,
│   │       company_people_intelligence.js — 2,995 lines, IIFE)
│   └── clients/northstaranesthesia/logo-white.svg
├── templates/
│   ├── agents.html          ← THE SINGLE SHARED MARKETING TEMPLATE, {% if page %} variants
│   ├── app.html, app_base.html, app_embed.html, app_history*.html, app_settings.html
│   ├── hub.html, b2b_agents.html (was gtm.html — LSR card commented out, see above),
│   │        seo.html, accounts.html, embed.html, context.html (=Playbook), 403.html
│   ├── company_people_intelligence.html   ← Contact Finder (670 lines)
│   ├── linkedin_scraper.html   ← serves BOTH internal and client LinkedIn dashboards
│   ├── admin_usage.html, admin_visitors.html, admin_members.html, admin_agent_runs.html,
│   │        admin_requests.html, admin_external_usage.html, admin_client_usage.html,
│   │        admin_client_detail.html
│   ├── _admin_menu.html     ← the ONE shared internal admin dropdown
│   ├── client_*.html        ← client-portal shell, home, agent detail, embed, history, denied
│   └── ppc_chat_widget.html ← shared Vimi chat widget (internal only)
├── reports/          ← dashboard*.html: BUILT ARTIFACTS, committed, never hand-edited
└── .github/workflows/ refresh-dashboards.yml, weekly_tracker.yml, build-frontend.yml
```

### Deploy + data model (unchanged)

- **Code/UI** push to `main` -> Railway redeploys (~60-100s). No hot reload locally.
- **Google Sheets** is the primary store for internal analytics. Two sign-in tabs with different column layouts (Login Log, `Member Signins`), Page Views, Agent Runs, Visitor Analytics, Demo Requests.
- **Postgres** (`DATABASE_URL`): `agent_run_history`, `cpi_search_history` (Contact Finder), plus Contact Finder's persistent caches (org resolution, firmographics, learned vocabulary, `person_enrichment`, `cpi_person_enrichment` id-cache).
- **SQLite** (committed): `data/tracker.db` (Healthcare), `data/tracker_csg_v2.db` (CSG), `data/tracker_northstar.db` (NorthStar). **Gitignored, real PII, NEVER commit: `data/identity_graph.db`.**

---

## VIMI, DE-ANON, STITCHING, AND THE OTHER SURFACES (unchanged this cycle)

- **Vimi** (label **GTM**): two backends, `/api/ppc-chat` (widget, `@position2_required`) and `/api/vimi-chat/<account_id>`. Never mix Healthcare and CSG in one answer.
- **Anonymous Visitors / de-anon:** `visitor_intelligence/`. Company-level multi-signal IP resolution, connection-type hard gate, noisy-OR confidence, Apollo enrichment, 0-100 intent. Person-level: persistent SQLite identity graph. **Never fabricates a person.**
- **`p2_vid` stitching:** Page Views and both login tabs carry a visitor-id column.
- **Surface 2, `/app`:** shell `app_base.html`, `APP_AGENTS` cards (minus `HIDDEN_AGENT_SLUGS`), 3 wired to live seo-apps tools plus LSR (currently hidden), the rest request-access-only. Per-agent `lock_label` and `no_request` keys control the locked-card CTA, enforced server-side too.
- **Surface 1, public site:** one template `agents.html`, `{% if page %}` chain.
- **Surface 3, `/p2/*`:** `/p2/hub`, `/p2/b2b-agents` (+ Contact Finder, sentiment-pulse MOCK data, ad-intelligence React app, linkedin-intelligence, linkedin-strategy-researcher), `/p2/seo` + tools (16 now), `/p2/accounts` + signal trackers, `/p2/playbook`, admin dashboards.

**Agent roster hazard, still live, and this cycle proved it again:** the roster exists in **three independent lists** (`AGENTS`, `APP_AGENTS`, and a JS array in `templates/context.html`), plus the internal SEO Suite tools list, plus now `HIDDEN_AGENT_SLUGS` as a fourth cross-cutting mechanism. **Nothing derives one from another.** The LSR hide this cycle needed edits in the set AND two hand-written HTML spots precisely because of this; expect the same friction on the next roster change.

---

## BRANDING + THEME (unchanged)

"Arena" mark: bright-green hexagon `#55be8c` + steel-blue + dark-green petals = 6-point star. `theme.js` (`localStorage['p2-theme']`, default dark). Hard sign-out: `/logout` sends `Clear-Site-Data` + explicit cookie deletion. Bricolage Grotesque is the public body font.

---

## ENVIRONMENT VARIABLES (unchanged)

**Railway:** `DATABASE_URL`, `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_SA_JSON`, `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`/`FLASK_SECRET_KEY`, `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), **`APOLLO_API_KEY` (Contact Finder + de-anon + person enrichment all depend on this one shared key and its shared credit pool)**, `VI_ENRICH_ON_VIEW` (opt), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt), `SMTP_*` (unusable on Railway).
**GitHub Actions secrets (separate):** `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

---

## HOW TO WORK ON THIS (proven-safe workflow, reinforced this cycle)

1. **Clone fresh into the bash sandbox each session.** Sandbox network: `git` over `github.com` works; `api.github.com`, most external APIs are blocked (**Apollo's free MCP tools DO work and were used extensively this cycle for live filter verification - 0 credits, no confirmation needed for `apollo_mixed_people_api_search`; paid Apollo MCP endpoints need explicit confirmation before spending from the shared pool**). WebSearch/WebFetch work.
2. Edit via file-edit tools or Python string-replace scripts (assert exactly-one match).
3. **Validate before every push, in this order:**
   - `PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q` (must be 1,434+ passing)
   - **mutation-test the actual fix** with genuinely distinguishing data (see the testing-lesson callout above), break each fixed line, confirm a test dies, restore
   - `python3 -c "import ast; ast.parse(open('app.py').read())"` (and any other changed `.py`)
   - import the app to catch route collisions
   - `node --check` any changed JS bundle
   - no em dashes on added lines
4. **Push once validated, without asking each time** - this is a standing instruction from the user ("Yes. Always push."), not a one-off. Still report what shipped afterward, including the commit hash. Push URL = `https://<TOKEN>@github.com/ai-positon2/intelligence-platform`. **Redact the token in ALL visible output:** `sed -E 's#ghp_[A-Za-z0-9]+#[REDACTED]#g'`. **The user pastes a fresh classic PAT each session and it must be rotated afterward - flag this every single push, it has now been reused across many consecutive sessions.**
5. **If a multi-agent background review hits an org-wide API spend limit mid-task, it can simply be re-launched once the limit resets, same session** - this happened this cycle and recovered cleanly; don't treat it as a dead end or silently give up on the deeper audit.
6. **Never use an em dash in any written copy**, anywhere. Use commas, colons, periods, parentheses.
7. **A shared signal/category/label reused across client accounts gets a per-account override parameter, never a global rename.**
8. **When a user reports something is "still not fixed", re-measure the EXACT reported symptom empirically** rather than assuming your first fix must have been under-deployed or insufficient.
9. **Never commit `data/identity_graph.db`.** Never put personal or sensitive data in URL parameters or query strings.
10. **When hiding (not deleting) a listed agent, check for hand-written HTML in addition to any registry/set-based filter** - `HIDDEN_AGENT_SLUGS` drives three surfaces automatically but the b2b_agents card grid and its command-palette entry are hand-written and need a separate edit, per the LSR precedent this cycle.

### Gotchas (unchanged, still true)

- `templates/context.html` (Playbook), `templates/linkedin_scraper.html` (LinkedIn Intelligence), and the entire `company_people_intelligence` naming for Contact Finder are filename remnants of renamed features.
- The Contact Finder JS bundle is wrapped in an **IIFE**: only `window.cpi*` functions are reachable externally.
- `admin.css` loads last and overrides inline admin CSS.
- A flex item that must shrink below its content needs its own `min-width:0`.
- A CSS rule can silently win over another of equal specificity purely by being declared later.
- Never put `{{`, `{%` or `{#` inside `<style>`/`<script>`.
- Python's `csv.writer` default `lineterminator` is `\r\n` regardless of how the file was opened.
- Flask's `render_template` caches compiled templates process-wide.
- macOS sandbox has no `timeout` command; `zsh` does not word-split unquoted variables.

---

## OPEN ITEMS / TODO

1. **Rotate the GitHub token.** Used for many consecutive pushes across many sessions now, pasted into chat each time. This is the one piece of cleanup the assistant cannot do itself; flag it every session.
2. **Restore LinkedIn Strategy Researcher to the listings** when the owner asks (expected "in a few days" as of this cycle) - full itemized checklist above and in `[[project-lsr-hidden]]`.
3. **Contact Finder's chat path has never run against live OpenAI + Apollo keys.**
4. **Contact Finder residuals (unchanged from v23):** the 120-row history cap silently truncates a paged search; zero-result searches are never saved to history; `_cpi_probe_company_free` guesses only `.com`.
5. **Apollo's advanced-filter-requires-plan-upgrade risk** (founded-year range, department headcount on people search) is unconfirmed against production's actual `APOLLO_API_KEY` plan tier, and `_post()`'s 422 retry logic isn't hardened against it - see the Contact Finder section.
6. **Verify the headcount-growth unit against live Apollo** the first time someone with the production key looks at a fast-growing company.
7. **Hardcoded counts still in the codebase:** the hub band's "300+ live signals" understatement, `ACCOUNTS["healthcare"]["description"]`'s hardcoded "1,251", four places in `templates/agents.html`.
8. **Signal refresh secrets (blocking Healthcare refresh):** set GitHub Actions `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`.
9. **Agent roster will drift again**, and now has a fourth mechanism (`HIDDEN_AGENT_SLUGS`) layered on top of the three lists. Consider deriving them if the roster changes materially again.
10. **Fully connect the `/app` "Competitor Analysis" placeholder** once the live SEO Studio tool's per-client data scoping is addressed - currently deliberately left request-only, see the SEO Suite section above.
11. **Assign real agents to more `/app` + client cards.**
12. **NorthStar client-side portal adoption is minimal.** A relationship conversation, not a code fix.
13. **`data/identity_graph.db` is on Railway's ephemeral disk.** Move to a persistent volume or Postgres.
14. **Cold-visitor identification** needs a licensed identity feed. Plug point ready.
15. **Light-theme polish** on heavy custom inline pages.
16. **Signal Tracker maintenance mode:** periodically prune `data/northstar_signals_manual.json` by `signal_date` and re-run `seed_northstar_signals.py --prune`.
17. **Advisory security/design audit (do not start without an explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID`, cookie flags, HSTS/security headers, CSRF, rate limiting, SSRF/`X-Forwarded-For` hardening; CSS token convergence, accessibility.

---

## COMPETITOR / ROADMAP (recorded, not built, unchanged)

Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala. Gaps: co-op topic intent, review-site intent, technographic change, champion job-change, hiring-surge, earnings/10-K mining, event attendance, layoffs, PLG usage. Differentiators: generative-search/AI-answer visibility + agency execution + first-party web de-anon with a real engine + **a working, deeply-audited Apollo contact-finding surface with honest credit accounting and no known silent-failure modes left in its search/filter path.**
