# Intelligence by Position2 - Full Context (v29 - September 2026)

Paste this entire file at the start of a new chat to give the assistant full context on this platform. **v29 supersedes all earlier context files (v1-v28)** - older versions are stale; ignore any pasted copy, and if `CONTEXT_FOR_NEW_CHAT_V28.md` (or older) still exists in the repo root, delete it as part of landing this file per the standing one-canonical-file convention.

**Latest `main` HEAD at the end of this cycle: `5ce36c7`** (always `git pull` to confirm; Railway auto-deploys every push). `app.py` is **18,067 lines / 203 `@app.route` decorator lines**, up from 17,879 / 199 at v28. The test suite is now **131 files, roughly 3,619 offline tests plus 32 dedicated real-Postgres tests, all passing** (`PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q`), up from 113 files / 2,838 tests at v28. **32 routes still carry `@admin_required`** (unchanged).

**What kind of cycle this was:** v28 built the Event & Conference Intelligence agent and got it working. This entire cycle, by far the longest and most eventful single stretch this platform has had, was spent making that one agent trustworthy, then making it good, and touched nothing else. It went in three passes. First, a series of **audits against increasingly real conditions** (code review, then a stubbed pipeline run, then real Anthropic+Apollo+Postgres keys, then a real printed client report) that each found bugs invisible to the pass before it, because this agent's specific failure mode is reporting a clean, confident number while the real thing quietly didn't happen. Second, a **complete redesign of the report itself**, twice, on direct user feedback that a technically-correct report nobody would read is not a report. Third, on the explicit instruction to make it **"crazy good, not just bug-free,"** two brand-new features that use this platform's one real structural advantage over a stateless AI research session: a Postgres database that remembers every run, forever.

The single most transferable lesson of the cycle, stated by the user directly and confirmed by everything that followed: this agent is still architecturally "ask an LLM to search the web six times," the same primitive any deep-research tool has, and a platform that ALSO has persistent, cross-run, cross-client memory can do things a stateless session structurally cannot - until this cycle that memory was almost entirely unused. **The second most transferable lesson, found and re-found at every depth of testing: a green suite proves the code runs, not that the thing it claims happened actually did.** Four separate live runs against real money and real keys each found at least one bug an already-fully-passing offline suite had missed, right up through the fourth run, by which point the agent was genuinely solid.

---

## WHAT V29 ADDS ON TOP OF V28 (this cycle's work - full detail is in the EVENT & CONFERENCE INTELLIGENCE section below; this is the index)

Roughly 60 commits, one agent, four escalating rounds of scrutiny plus two new features, in the order they happened.

1. **Code-level audit** (`9686f40`, `7e2ce8d`, `c26a894`, `1f00c4c`, `8338d46`) - found the recommend mode had never actually saved a row to the database (a missing `run_id` column made every INSERT silently fail, and 443 green tests said otherwise), plus date-blindness, a one-word matchmaking bonus, substring category filters, and a JSON-parse bug that made a 300-exhibitor page read as "publishes no exhibitors."
2. **First live-API run** (same commits) - real Anthropic+Apollo+Postgres money for the first time. Found `find_people` silently discarding 100% of Apollo's real results (it grouped on a field the live API returns null for), Apollo returning masked names on this plan, and a starved web search being reported as a confirmed empty market instead of a hole in the analysis.
3. **Discover drawer redesign, then retirement** (`302daec` through `fdd0510`) - a full visual rebuild of the results view, four more defects it exposed, then the `discover` play itself was cut entirely (three plays remain: recommend / lookup / workroom) once its own overlap with `recommend` stopped making sense to explain to a user.
4. **Two-stage discovery + real client intake** (`9ba6186`, `3f276e1`, `5dbea5d`) - replaced one big, expensive web-search call per category with a cheap "propose candidates" call plus one "confirm" call per candidate (a search call's input cost grows with the SQUARE of how many searches happen inside it, so this is both cheaper and more honest), and replaced a 13-field client-profile form with "give us a name and a URL" - the agent now reads the client's own website itself, with no search tool at all, in about 15 seconds instead of up to ten minutes.
5. **Chart kit + three real live runs** (`79840c6`, `b4b2627`, `816c9ae`, `03ea4e5`, `d875378`) - every report got real drawn charts instead of paragraphs; a "Worth a look" second tier was added after real runs kept returning zero events (one threshold at 70 had been deciding both what's recommended AND what merely exists); the famous-event audit was split to one call per event; a fully cost-instrumented live run ($12.15, 21 minutes) found and fixed six more live-only bugs, including one that silently discarded two genuinely on-ICP events for running out of output budget mid-narration.
6. **Report rewritten for a human, twice** (`7935891`, `9b639b8`/`20cf11c`, `32f6587`) - on direct feedback that the already-fixed report was still "too text heavy, nobody would read it." Answer-first, one scannable line per problem with the full reason folded behind it, and three separate kinds of writing (raw internal category names, this codebase's own error-log language, and the model's own process narration) were all found reaching a paying client's report and stripped out.
7. **A worse version of an already-fixed bug** (`d425794`) - the identical "ran out of output budget mid-narration" defect, this time in the step that scores up to 6 events per call, which meant losing just one call could zero out an entire run's results with nothing on screen saying why.
8. **A full audit of the whole recommend path against a real pipeline run** (`1beed4c`/`91fa2ab`/`7052015`) - seven bugs, every one the same shape: the report said what a stage MEANT to do, not what it actually produced. The costliest: the famous-event audit's own replacement-event feature had been completely inert since the day it shipped, because production could never satisfy the one condition every existing test had faked.
9. **A dedicated error-free sweep** (`b00d931`, `5cdfddf`) - found a systemic em-dash leak (two independent partial fixes, each unaware of the other, plus nine more completely unenforced fields) and the CSV export quietly disagreeing with the web page for the identical run.
10. **"Make it crazy good"** (`5ce36c7`) - two new features built on the platform's persistent database: a client's own accept/reject history nudges future recommendations (reorder only, never the score), and k-anonymity-gated cross-client interest ("N other similar clients also kept this event," with no identity in the raw data). A live end-to-end test at zero API cost caught the report's own top-five list silently dropping both brand-new fields.

The v28 material directly below (its own three-thread summary) is left in place as history, not because it is still the current cycle.

---

## WHAT V28 ADDED ON TOP OF V27 (kept as history)

Eleven commits in three threads, in the order they shipped.

**Thread 1, the tenth agent (`5bc89da`, `b9fb771`, `18adf08`, `76b5715`, `5fc90dc`, `cccc4b2`)** - Event & Conference Intelligence, its own full section below. Worth reading even if you never touch this agent, because the shape of the cycle is instructive: v1 (`5bc89da`) was built from the agent's own description and was **rejected outright** ("the output and the look and feel and everything is shit"). The root cause was not effort, it was that the two `gtm-skills` source plays this agent was supposed to implement had never been read. The rebuild (`b9fb771`) reads them and turns their load-bearing rules into **things the code refuses**, which is the whole difference between a play a human runs in a chat and one a platform can be trusted to run unattended. Then `18adf08` fixed a first-run dead end reported from the live page, `76b5715` traced all 20 form controls end to end with unique sentinels and found seven things the page promised and did not do, and `5fc90dc` + `cccc4b2` rebuilt the page twice.

**Thread 2, the shared grid (`b151ab0`, `fcc0c39`, `bf0968d`)** - reported as "too broad, no spacing at the sides" on one page, and it turned out three pages had the same defect in different amounts. `static/css/grid-tokens.css` was **linked from pages that never read it**: they hand-typed `padding: 34px 30px` and capped at 1440px while their siblings used `var(--margin)`. Now every agent page's container takes the margin token, every top bar takes `--bleed` so the logo lines up with the content beneath it, and every bar is 62px. `tests/test_agent_page_grid.py` (31 checks over ten stylesheets) is the guard, because **this regression is invisible on the page it happens to and only shows up beside a sibling**, which is why it survived this long. `bf0968d` then reverted one part of it: Contact Finder's column gap went back to 22px, held with a comment, because the token widened it to 32px and cost the filter column 10px on the page about to go to external clients.

**Thread 3, Unipile made real (`9ae0b54`, `54faee4`)** - the user supplied a working DSN and API key, and everything in the v27 client turned out to be wrong. Rewritten against live 200s. LinkedIn company posts now collect through a real authenticated account. Then `54faee4` added the guard that a carelessly-picked handle exposed: a wrong-but-real company page returns an empty list perfectly cleanly, and "this company posts nothing" is a very different claim from "we read the wrong page".

**Plus `587635d`** - Contact Finder's results and assistant were stacking vertically for anyone who had ever chosen the List view, permanently, because the view is remembered. Its own section below.

Plus this context-file refresh itself (v27 -> v28), on explicit user request.

---

## WHAT THIS IS

**Intelligence by Position2** is a B2B revenue-/sales-intelligence web app for the Position2 agency (Position2 = a B2B digital-marketing agency: SEO/organic, performance/paid media, paid social, content, brand/website, RevOps/HubSpot). It surfaces buying signals (funding, leadership change, M&A, IPO, product launches, partnerships, anesthesiologist/creative hiring, news, new-job-detected alerts on tracked people), de-anonymizes website visitors to company and (where a signal exists) person, **finds and enriches contacts at target companies via Apollo**, scrapes LinkedIn engagement, runs a native competitive-strategy analysis agent, tracks appointment-slot availability for a healthcare client, analyzes competitors' organic social creative across 6 platforms with real Claude-vision analysis, tracks competitor ads, tracks brand visibility in AI answer engines (GEO), ranks prospects by intent, runs a suite of SEO/GEO tools (SEO Studio), helps reps act via an embedded AI assistant (**Vimi**, visible label **GTM**), and serves **co-branded client portals** that can also embed **agents built entirely on other platforms.**

- **Live URL:** `https://intelligence.position2.com`
- **GitHub (main app, Flask):** `https://github.com/ai-positon2/intelligence-platform`
- **GitHub (embedded SEO tools, React/Vite, SEPARATE Railway service):** `https://github.com/ai-positon2/seo-apps` -> `https://seo-apps-production-37a6.up.railway.app`
- **Third-party agent frontend (NOT our code, NOT our repo):** `https://watchtower-by-position2.vercel.app`. The user builds these on an unrelated AI app-builder platform; we only receive and iframe the public URL, plus a `postMessage` run-signal snippet the user deployed into it. This backs **LinkedIn Social Researcher** (the old, external, currently-hidden agent - see its own section below), not the new native LinkedIn Strategy Researcher.
- **Hosting:** Railway, auto-deploys on every push to `main` (~60-100s for the Flask app via NIXPACKS/`gunicorn app:app`; a few minutes for `seo-apps`). HTML/CSS/JS goes live on push.
- **Admins (`ADMIN_EMAILS`):** `krishna.ladha@`, `sudheer.d@`, `reporting@`, `sparikh@`, `abhilash.dg@`, `pushpendra.k@`, `sangeeta@` (all `position2.com`); `sangeeta@` added in the v27 cycle, unchanged since. `tests/test_admin_access.py` proves the claim below rather than restating it: it AST-parses every `@admin_required` view, sweeps all 32 routes, and checks that the nine pre-rename `/p2/admin/*` URLs are bare 301s onto gated pages. **This set is the ONLY place admin access is defined.** `admin_required` gates every `/p2/admin/*` route off it, the template context processor derives `is_admin` from it, and `/api/whoami` returns `is_admin` from it so client-rendered surfaces read the same flag. Add a person here and nowhere else.

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

## EVENT & CONFERENCE INTELLIGENCE (the 10th B2B agent; built at v28, this whole cycle went into hardening it and then making it "crazy good")

**Route base:** `/p2/b2b-agents/event-conference-intelligence`, staff-only (`@position2_required`). Page `templates/event_conference_intelligence.html` (report logic inline as JS) + `static/css/event_conference_intelligence.css`. Domain in `tracker/event_intel_*.py` (`store`, `rubric`, `harvest`, `recover`, `resolve`, `enrich`, `discover`, `audit`, `scorer`, `report`, `workroom`, `pipeline`, `intake`, plus a shared `claude_websearch` helper). Postgres tables `evi_events`, `evi_participants`, `evi_sources`, `evi_runs`, `evi_profiles`, `evi_candidates`, `evi_outreach`, `evi_outcomes`.

**Three modes over one store** (a fourth, `discover`, was built at v28 and RETIRED this cycle - see below):

| Mode | What it answers | Source play |
|---|---|---|
| `recommend` | score a client's calendar of events and say which to attend, with a ranked shortlist plus a "worth a look" second tier | gtm-skills `conference-recommendation` |
| `lookup` | name an event, get the participant roster it publishes | native |
| `workroom` | post-event follow-up: who to talk to and what to say | gtm-skills `event-radar` |

**`discover` (describe an audience, get events ranked by target-account overlap) was retired in `fdd0510`.** It overlapped too much with `recommend` on the surface question ("which events?") to explain to a user as a genuinely separate choice, once the two were pinned side by side and it became clear the real difference was depth, not input: `recommend` runs the full six-category search plus a rubric-based audit and score (roughly 20-40 minutes, real cost), while `discover` was one lighter call ranked only by the model's own fit judgment. The read path for stored runs is deliberately KEPT so history still opens - retiring is not deleting. `tracker/event_intel_discover.py` itself was NOT deleted; it is the unrelated six-category search step inside `recommend` and is a trap to confuse with the retired play by name alone.

### The constraint the whole agent is built around

**Events sell attendee lists, they do not publish them.** So what this collects is exhibitors, sponsors, speakers and partners, each row carrying the role its own source page gave it. `event_intel_store.ROLE_LABELS` is the single place that decides what a row is called, and **exactly one role is allowed to use the word "attending"**: `ROLE_ATTENDEE_DECLARED`, "Publicly said they are attending", which comes from a person publicly saying so. Everything else is labelled Exhibitor / Sponsor / Speaker / Partner / Media. A roster that calls an exhibitor an attendee is the same defect the thirteen Contact Finder audit rounds kept finding, in a new costume.

`evi_sources` is why the schema has four tables rather than three. **A roster assembled from four of an event's seven pages looks identical to a complete one, only shorter.** Recording every page tried, including the ones that 403'd or came back as a JavaScript shell, is what lets the report say "2 pages could not be read" instead of quietly understating an event. Render `evi_sources` unconditionally; it is not a debug panel.

### The rules the code REFUSES to break (this is the point of the agent)

gtm-skills plays are prompts a human runs in a chat, so every rule in them is a **request**. The rebuild turns the load-bearing ones into **refusals**. That is what makes this better than the source rather than equal to it:

- **`rubric.score()` has no budget parameter and no `**kwargs`.** Cost cannot reach a score even by accident. A test asserts the signature. Cost travels beside a candidate and renders next to the score, never inside it.
- **The +10 organizer-matchmaking bonus needs two independent gates:** the model's `organizer_run` flag AND evidence text surviving a veto list (Whova, Brella, Swapcard, networking lounges, "pre-booking encouraged" are apps and lounges, not programmes). Either gate alone is bypassable by an agreeable model. **A refused bonus states its reason on screen.**
- **`orientation_for()` and `workroom.play_for()` RAISE on unknown input.** A default would silently score the opposite side of the trade-show floor, or write a competitor follow-up in an owned-event voice, and nothing downstream would look wrong.
- **The six discovery categories are searched SEPARATELY**, so a model cannot fill the quota out of category one and relabel a flagship. `empty` (a real market finding) and `error` (a hole in the analysis) stay distinct and are never collapsed.
- **Totals are recomputed from their own sub-scores at write time** (`normalise_candidate`), so the headline can never contradict the bars.
- **A famous-event verdict of "kept" with no named alternative is downgraded to a cut**, because the comparison that step requires never actually happened.
- **The workroom booth rule:** a draft opener may reference a conversation ONLY where the USER wrote a note about that specific company. `enforce()` throws away any draft claiming one, replaces it, and **shows both the offending phrase and the reason**. This is the rule a model breaks most eagerly, and it applies to every event class: the class does not create the evidence, the note does.
- **Displacement language is refused on a competitor's event.** A roster row with nobody named gets an account play, never a message addressed to a person who was never identified.
- **The non-ICP tail is cut and COUNTED**, because event-radar's own warning is that conferences attract everyone.
- **The missing CRM is reported as missing.** No CRM is wired. In its place the agent surfaces companies seen on the floor at this user's own earlier events, which is a real signal Postgres can answer and a prompt cannot. The cross-client check is measured against real prior runs, never imagined.
- **The executive summary is assembled in code**, with no model call.

### The rubric (verbatim from the source skill, implemented in `event_intel_rubric.py`) - CHANGED this cycle, do not trust the v28 numbers below without reading this

```
Relevance                     /40   how closely composition matches ICP
Decision-maker accessibility  /40   density of buyers AND structural reach
Engagement mode               /20   buying mindset vs learning mindset
+ organizer-run matchmaking   +10   bonus, total out of 110
+/- outcome adjustment        +/-5  this client's own history with this category/format (new, see below)

P1  >= 80        must-attend           -> "kept"
P2  70-79        strong                -> "kept"
(new) worth a look, 50-69 total AND relevance >= 24/40   -> "worth_a_look" (full row, not excluded)
below either gate -> EXCLUDED from every list, no padding
```

**Why the "worth a look" tier exists (`b4b2627`, 2026-09-04):** real live runs kept coming back with ZERO events for a real client, and the cause was architectural, not strictness. `RANK_FLOOR=70` was doing double duty, deciding both what gets RECOMMENDED and what merely EXISTS as an option, while the scorer's own prompt says "most events are mediocre for most clients" - pushing every score down while the bar stayed fixed. `RELEVANCE_GATE=24` and `CONSIDER_FLOOR=50` (both must pass, via `rubric.is_worth_a_look()`) now decide whether something is an option at all; `RANK_FLOOR` still only ranks within the options. A missing or unreadable relevance score FAILS the gate on purpose - absence is not evidence of fit. Measured on a real client's seeded run: 1 recommendation became 1 recommendation plus 5 "worth a look" options, with the genuinely wrong-audience events still correctly cut. **This is tuned on very little live data (2 runs) and may need revisiting.**

**The outcome adjustment (new, `5ce36c7`, "make it crazy good"), described in full further down this section.** Purely a reorder within a bucket already decided by the rules above - it can never move an event into or out of `kept`/`worth_a_look`, and it never touches `total` or `tier`.

The module is pure: no I/O, no model calls, no imports from the rest of the package. The page reads these numbers from the module at render time rather than typing them, so a reweighting moves the page.

### Cost model (differs from Contact Finder, and the v27 doc had it wrong)

**Apollo's `mixed_companies/search` bills per CALL**, not per record and not free. `mixed_people/api_search` does not bill. So:

- **Harvesting is free and automatic.** Company resolution is a **separate, explicitly-triggered route** that states its ceiling before spending.
- Resolution **batches by domain**, so 60 exhibitors cost 3 credits rather than 60.
- Harvesting therefore works hard to keep each exhibitor's **published link**: a domain is exact where a fuzzy name match is not, and deriving a domain from a company name is the defect already logged against `_cpi_probe_company_free`.
- **A participant with no published link is reported unresolved, never guessed.**

### The two harvest defects that made v1's roster unusable

1. **Pagination was not followed.** Reading page one of a fourteen-page exhibitor directory and calling it the roster is an undercount with nothing on screen to reveal it. `harvest_page` now follows `next_page_links` (same host and path, pagination param only, forward only).
2. **A page yielding nobody while carrying a browser-side mount point was reported as "this event has no exhibitors".** An unread page and an empty page render identically and mean opposite things. Such a page is now `blocked`, and gets a second read path: `event_intel_recover.recover_page` searches for the listing and stores it at its own evidence grade (source status `recovered`, row `provenance=search`). **A recovery that ran zero searches is discarded outright** as a recollection rather than a read.

### `tracker/claude_websearch.py`

Factors out the `web_search` plumbing `sci_identify` learned the hard way: dated tool versions with an ordered fallback, "version rejected" told apart from "bad minute", streamed rather than blocking, and **every text block joined rather than `content[-1]`** (see `[[reference-anthropic-web-search-blocks]]`). `sci_identify` deliberately keeps its own copy for now: it is twice-debugged in production and this module has no production mileage yet.

### The field-by-field audit (`76b5715`) and why it is the model for auditing any page here

Every one of the 20 form controls was traced from the DOM through the route, the store, the prompts and the report **with a unique sentinel in each field**, rather than by reading the code. Seven fields or claims did not survive the trip, and **all seven passed the entire suite**, because no test covered the path. Four were the same defect the agent exists to prevent, something disappearing with a confident number beside it:

- A marquee event the auditor returned no verdict for was cut, its explanation written onto a row that was then discarded, so it appeared in no list while the summary said "0 were cut".
- An audit call that succeeded but yielded no readable verdict for ANY famous event cut every flagship silently. That is a parse failure wearing a success; it now reports a failed audit and cuts nobody.
- Events scoring above 70 but falling outside `max_events` simply vanished. `rank()` computes `over_cap` precisely so this can be said, and nothing said it.
- Discover mode's form promises events "ranked by how many of your own target accounts turn up", and they were rendered in discovery order. Now ordered by overlap, and because only the best-fitting few rosters are sampled, **an unread roster says so** instead of looking identical to one containing none of your accounts.

Two more were fields collected and thrown away (`budget_note`, stored and rendered nowhere while its own hint said "Shown beside each event"; `force_include`, a dead column now wired as "already committed to these"). The seventh was the string "1 marquee event were audited".

### Page design (`5fc90dc`, then `cccc4b2`)

The page first read as "a form on a flat panel" beside Contact Finder and SCI. `5fc90dc` drew the scoring model in the hero as bars to scale, grouped the intake into five labelled steps, and put a sticky rail beside the form carrying run history and spend. `cccc4b2` then removed the scoring card at the user's request and **gave the weight to the four play cards** instead, since they decide what every field below them means: each with its own hue set once per card as `--p1`/`--p2` and used by icon, lit edge, border, glow and action label; a ghosted step number 01 to 04; and the action spelled out along the bottom edge. **`data-action` is now the only place a play's action wording lives** and `setMode` reads the label off the selected card, because the second table it used to keep had already drifted (two modes showed a bare "Run").

**Class-name collision worth remembering:** `.evi-play` was simultaneously the hero play card and the workroom report's play block, so every rule written for one landed on the other. The report's is now `.evi-classplay` and a test asserts the two never share a class again.

### Admin self-tests

`POST /p2/admin/external-usage/evi-guardrail-check` (offline, free, proves the six refusals hold **on the deployed code** and states what it could NOT check) and `POST /p2/admin/external-usage/evi-resolve-check`.

### Two-stage discovery replaced one big search call per category (`9ba6186`, 2026-09-03)

**Why:** a server-side search call's INPUT cost grows with roughly the SQUARE of how many searches happen inside that one call, not linearly (measured live and confirmed to ~1%: a 6-search call costs 50-59k input tokens, a 10-search call costs 152k, predicted 55k x (10/6)^2 = 153k - see `[[reference-anthropic-web-search-blocks]]`). So a bigger budget was never the fix for categories starving. Each of the six discovery categories is now two stages: `propose_category` names candidates only (no deep verification, `FIND_MAX_USES=6`), then `confirm_event` runs once per named candidate, independently (`CONFIRM_MAX_USES=6`, `MAX_INFLIGHT=4` module-level semaphore held only around the search call itself, never while a category waits on its own confirmations - holding it there would deadlock). This is also more honest: the confirmation step did not propose the event, so it can say no, and a confirmation that cites no page is rejected outright. That creates a real third outcome that must never collapse into the other two: `CONFIRM_OK`, `CONFIRM_REJECTED` (a real, searched, ruled-out fact about the client's year - a legitimate `empty` category) and `CONFIRM_UNCHECKED` (a hole in the analysis, forces `error`).

### The client intake now asks for two things, not thirteen (`3f276e1`, `5dbea5d`, `3712468`)

The old form asked for 13 fields up front. `event_intel_intake.py` + route `POST /profiles/draft` now takes a client name and a URL, fetches that site itself (`event_intel_harvest.fetch_page`, `pick_links` follows a few of the site's own links), and fills the rest of the form in - **with NO search tool at all** (`MAX_USES=0`). The first version let the model search for itself and was measured live at 29s, 167s, 450s, and once over ten minutes with a starve; fetching the pages directly and handing plain text to a model with no tool is **15 seconds**, and is also more honest, since `sources` becomes the pages actually retrieved (with real HTTP status) rather than URLs the model claims it opened. Hard, mutation-tested rules: it saves nothing itself (the profile route is still the only writer); it never overwrites a field the user already typed; an unevidenced field is emptied and named in `unknown`; a draft that ran no fetch or cites no page is discarded outright; **the suggested classification is proposed, never auto-selected** - a person has to click it before it counts, which is the one invariant the whole intake screen rests on.

### The report was completely redrawn, then rewritten for a reader, twice

`79840c6` gave all three surviving plays a shared chart kit (`evDonut`/`evBars`/`evCols`/`evFunnel`/`evRing`), replacing paragraphs with real drawn charts that share one color-band vocabulary across the whole page. But a fixed report still read, in the user's own words after the redesign, as "too text heavy, nobody would read it." **`7935891` introduced the pattern that actually fixed it: HEAD + DETAIL.** Every finding (`event_intel_report.notes()`) is now `{level, head, detail}` - one always-visible scannable line, with every name/count/reason that used to be a wall of paragraph text folded behind a `<details>`. The executive summary now leads with one answer line ("1 event worth the trip, none unmissable") naming the leader as a clickable control, before any of the analysis. **Three separate kinds of writing were then found reaching a paying client's actual report, unedited** (`9b639b8`, `20cf11c`): raw internal category keys and enum values printed verbatim; this codebase's OWN error-log language (a live report literally printed "Raise max_tokens or lower max_uses" under a heading); and the model's own uncensored process narration ("i attempted to research... however the web_search tool hit a hard per-turn call limit..."), printed whole and sometimes cut off mid-word. All three are now filtered at dedicated points (`claude_websearch.reader_reason()`, `_reader_note()`) rather than trusted to be clean by construction. `32f6587` then redrew the Category coverage chart specifically: it no longer takes an inline note at all, reasons live in one grouped block below the chart (grouped by what they actually SAY, not by category, so three categories cut off by the identical cause get one sentence, not three).

### Full audit against a real pipeline run, 2026-09-04 (`1beed4c`/`91fa2ab`/`7052015`)

Run through a complete pipeline (real merge, real audit, real promotion, real store, real rank, real summary - only the three model-facing calls stubbed), not against the modules in isolation. **Seven defects, every one the exact same shape: the report stated something true of what a stage INTENDED and false of what it actually PRODUCED**, and every one of them passed all 978 tests that existed before this audit. The costliest: `promote_alternatives` (the famous-event audit's "here's a real replacement for the marquee event we cut" feature, shipped at v28 in `8338d46`) had been **completely inert in production since the day it shipped** - its category lookup depended on the cut event still being present in a list the pipeline had, by that point, already removed it from, so the lookup could never succeed outside of a hand-built test that (necessarily, since production cannot) passed the cut event back in by hand.

### Four real live-money runs, escalating

1. **First live end-to-end recommend run** (2026-09-03, $9.13, 33.8 min): 3 events found, 1 kept - unusable, but for a precisely diagnosed reason (search starvation under the old single-call-per-category design, fixed by the two-stage split above).
2. **First run after the two-stage split + the "worth a look" tier** (2026-09-04, `d875378`, $12.15, 21.1 min, fully cost-instrumented for the first time): 4 recommended + 2 second-tier, where the same client had returned ZERO before. Found and fixed SIX more live-only bugs in one pass, the standout being output budgets tuned for a single-item reply that were four times too small once the model's own search narration started eating into the same token budget - two named, genuinely on-ICP events (a Gartner event, a Growth Marketing Summit) were silently discarded mid-run for hitting `stop_reason=max_tokens`, after already being paid for.
3. **A worse version of the same output-truncation bug** (`d425794`, 2026-09-05): found in `score_batch`, which scores up to 6 events in ONE call - losing that one call zeroes an entire run's results, not just one event's. The same defect was found by inspection (before it ever failed live) in `event_intel_workroom.draft_batch` and fixed the same way. A third live run confirmed both fixes: 0 categories failed, 0 unscored, 5 kept + 1 worth-a-look, $14.44, 25 minutes.
4. **A dedicated error-free sweep** (2026-09-05, `b00d931`/`5cdfddf`) rendered the two most recent real stored runs through the actual page script (not just unit tests) and found a systemic em-dash leak across the whole pipeline (two independent, incomplete private regexes that didn't know about each other, plus nine more completely unenforced free-text fields - now one shared `claude_websearch.strip_em_dash` applied at every field boundary) and the candidates CSV export quietly disagreeing with the web page for the identical run (the CSV had its own, second copy of the "on the list" policy, missing the `worth_a_look` tier entirely). **A fourth live run, unprompted, then hit the exact per-event-audit-failure case the `03ea4e5` one-call-per-event split exists to survive, and every part of that fix worked correctly on real, unengineered failure.**

### "Make it crazy good" - the two new features built on the platform's own database (`5ce36c7`, 2026-09-05)

The user's framing, after the error-free sweep above: this agent is still architecturally "ask an LLM to search the web six times," the same primitive any deep-research tool has - what would make it categorically different is the one thing a stateless session can never have, a Postgres database that persists across every run and every client. Four directions were proposed; the user chose all four, and `EnterPlanMode` was used given the scope (schema changes, new cross-tenant data access, a new paid-vendor dependency, and a new always-on production job all being on the table).

**Shipped and live-verified:**
- **Outcome-driven scoring** (`rubric.outcome_adjustment`, `store.outcome_pattern`) - a client's own accumulated went/going/skipped history, per category and format, becomes a small, capped (`OUTCOME_ADJUSTMENT=5`, half the organizer-matchmaking bonus), REORDER-ONLY signal, gated at 3+ decisions and 75%+ agreement. It can never touch `rank()`'s bucket/cap decisions or an event's `total`/`tier` - deliberately the same fit-vs-priority separation `RANK_FLOOR` already established. Scoped by `(email, profile_id)`, not email alone, so one login managing several client profiles can never leak one client's dislikes into another's scores.
- **Cross-client interest** (`audit.cross_client_signal`, `store.cross_client_interest`) - "N other clients with a similar buyer profile also kept this event," using only the event's own public name. This is the FIRST query in the whole codebase that intentionally crosses `email` boundaries (every existing query, including the pre-existing `genericness()` prior-candidate check, filters to one email). It is held to a stricter privacy bar than that pre-existing check too: `genericness()`'s own return value still carries another client's real name, masked only by the rendered text; this new function's return value has no field an identity could occupy even by accident, verified by a test that JSON-scans the ENTIRE raw structure, not just what gets displayed. Gated by real k-anonymity: `CROSS_CLIENT_MIN_DISTINCT=3` clients **plus** `CROSS_CLIENT_MIN_POPULATION=5` in the whole comparison population - the second gate matters concretely, because the real sandbox has only 3 distinct clients today, so a bare distinct-count floor alone would be close to fully identifying by elimination. A new `evi_profiles.confidential` flag (default off) opts a client out of the feature in both directions at once.
- **The live-Postgres-only bug this exact discipline exists to catch:** a real end-to-end run (seeded outcome history + seeded peer interest, zero Anthropic API cost since only the 3 model-facing calls were stubbed and everything else hit real SQL) found that `event_intel_report.top_five()` builds a fixed field subset and was silently dropping BOTH brand-new fields - every other surface (CSV, run-detail JSON, template badges) carried them correctly, only the one thing a client actually reads first did not. 3,619 offline + 32 real-Postgres tests, 13/13 targeted mutants killed against a baseline verified green on both suites together (a harness that only ran the offline suite would have missed 3 real survivors entirely).

**Two more directions were explicitly scoped and NOT built, blocked on the user, not on effort:**
- **Structured event-database integration** (10times/PredictHQ/Bizzabo-style) - the actual fix for categories still starving and for attendee counts being unverified claims rather than real numbers. Needs a vendor choice plus a paid API key only the user can obtain; PredictHQ was suggested as a starting point but nothing was committed to.
- **Continuous monitoring/alerts.** This repo already has a proven, reusable pattern for exactly this - three existing scheduled GitHub Actions (`refresh-dashboards.yml`, `weekly_tracker.yml`, `sync-job-change-alerts.yml`) already run cron jobs against production. The code itself would be a small addition. What is explicitly NOT done without a fresh, separate confirmation: adding a new workflow with a live schedule and wiring `DATABASE_URL` in as a real GitHub repo secret, since that starts a process running unattended against production data on its own recurring cadence, a different risk class than a normal code push, and outside the "always push code" standing authorization this whole project otherwise operates under.

### Open on this agent

**Still never been run against a live vendor calendar of dozens of real events end to end** - all four live runs so far used a small hand-seeded or lightly-scoped candidate set. `RELEVANCE_GATE=24`/`CONSIDER_FLOOR=50` are tuned on 1-2 runs of real data. Cost is now instrumented per stage but has only been measured across a handful of runs ($9.13 to $14.44, 21-34 minutes). `audit_famous`'s replacement-event promotion path, though now fixed at its root cause, has still never been directly observed promoting a real event end to end in a live run (the two live runs since the fix each had a reason the promotion path correctly did not need to fire). The six burned API keys from earlier sessions (Anthropic, OpenAI, Unipile, Google Cloud/YouTube, Apollo - see `scratchpad/.evi_keys.env` from prior sessions) still need rotating through each provider's own console plus a Railway update; this needs the user's own login and was explicitly deprioritized this cycle ("don't think about key rotation for now"). A mid-session incident this cycle where a sandbox working tree lost 273 untouched tracked files and its `origin` remote was resolved (the token was recovered from a sibling session's clone) but its root cause was never established.

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

### The layout fix (`587635d`) and the test weakness it exposed

Reported as "why are these two sections above and below and not side by side". **Not a CSS bug.** `window.cpiSetView` added a `wide` class to `.cpi-layout` whenever the table view was chosen, which deliberately dropped the assistant below the results, and the view is persisted in `localStorage["cpi-view"]`. So **one visit in List view left the page stacked on every visit after it**, with nothing on screen saying why or how to undo it, while the page's own empty state still read "ask the assistant on the right".

The old rule was not arbitrary (a table squeezed beside a fixed rail does get cramped), so the capability was kept and made explicit rather than removed: `cpiToggleChat()` is a chevron in the assistant's header that tucks the rail to a 54px strip, remembered in `localStorage["cpi-chat-tucked"]`, offered only above the 1080px breakpoint since below it the two are already one column. The rail is clamped to `clamp(340px,26vw,440px)` so extra viewport width feeds the results column alone (past ~440px a chat bubble's line length stops being readable). At 1512px that is 857px of results, or 1196px tucked.

Two things fixed while in the header: it was a wrapping flex row where the credits notice, List and History each carried their own `margin-left:auto`, which works only while everything happens to fit (at 1512 without the credits pill, History dropped to a third line against the left margin). It is now `.page-hdr-meta` + `.page-hdr-actions`. And `.cpi-empty` got a min-height, because a 130px box beside a rail nearly the height of the window reads as a hole in the page rather than a results area waiting for a search.

**Two test lessons, both general:**

- The existing `tests/test_cpi_table_view.py` drives the page's real JS in node and passed through all of this, because **its DOM shim answers `document.querySelector()` with `null` for everything**. The damaging line ran `if(lay)` and did nothing. A shim that cannot return an element cannot see what a function does to one. The new `tests/test_cpi_layout.py` resolves `.cpi-layout` for real.
- A CSS assertion helper that reads only the **first** matching rule for a selector is checking the losing declaration. Appending a later `.page-hdr-credits{margin-left:auto}` sailed straight past it. `_bodies()` reads every rule for the selector, which is the only reading the cascade makes true.

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
- **A DOM shim that answers `document.querySelector()` with `null` cannot see what a function does to the element it would have returned.** This is exactly how Contact Finder's stacking bug survived a node-executing test that passed on every run. If a test drives real page JS, the shim has to resolve the selectors the page actually queries.
- **A CSS assertion that reads only the first matching rule for a selector is checking the losing declaration.** The whole point of the cascade is that the last one wins, so a later override sails past it. Read every rule for the selector.
- **A test asserting "the page shows 70" cannot tell a rendered rubric value from a literal 70 that happens to match.** Monkeypatch the module and require the page to move with it.
- **A mutant that changes nothing is a finding, not a pass.** A rule stated twice cannot be tested, and both times that showed up this cycle the duplicate line was genuinely dead and got deleted.
- **UNAPPLIED mutants score as MISSES, never as passes.** An anchor whose indentation no longer matches the source silently applies nothing, and the gate reports success. Check the applied count, not just the killed count.
- **A `timeout`-killed mutation run leaves the mutant on disk.** The `finally` restore does not survive SIGTERM. Always grep for sentinels after an interrupted gate; this silently removed a source line once this cycle.
- **A colour checker that only parses `rgba()` silently exempts every `color-mix()` value.** Self-test the parser against the syntax the page actually uses; an rgba-only one reported three false failures here.
- Do not write change-detector tests. If a mutant is unreachable in practice, say so in the commit message instead of pinning it.

---

## APOLLO PERSON ENRICHMENT ON EXTERNAL USAGE

Unchanged. Lives at `/p2/admin/external-usage` (see `[[project-person-enrichment]]`). Clicking a person opens a profile modal with Apollo-sourced identity, employment and contact details, labelled outbound links, and an AI read of the person. New members are auto-enriched. There is an Excel export. When Apollo cannot match a person, the flow falls back to enriching their company by email domain. **This shares `_enrich_people`'s cache with Contact Finder's chat-side name reveal.**

Two hard-won points that generalise: **validate response shape, not just HTTP status**; **version-stamp every enrichment cache** so a fixed bug can self-heal.

---

## LINKEDIN STRATEGY RESEARCHER (native, built in the v26 cycle) - NOT the external Watchtower one

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

## GENTLE DENTAL SLOT CHECKER (built in the v26 cycle)

**What it is / audience:** "Gentle Dental" is a multi-brand dental-practice-chain client (82 locations across MA/NH/CT and others). This staff-only dashboard (`/p2/b2b-agents/42-north-dental-slot-checker`, `@position2_required`, not exposed to the client) answers, in the page's own words: *"What a new patient would actually be offered if they tried to book right now, across every location the agent checks."* A separate scraping agent crawls each location's real booking widget weekly and writes results into a Google Sheet; this feature is purely the read/visualize layer over that data. **Not listed in the `AGENTS`/`APP_AGENTS` registries at all** - it's a direct standalone route with its own hand-written card in `b2b_agents.html`, outside the usual roster mechanism.

**Routes:** `GET /p2/b2b-agents/42-north-dental-slot-checker` (page), `GET .../data` (JSON payload via `tracker.slot_checker.fetch()`, `?fresh=1` bypasses cache, relies on the platform's generic gzip hook), `GET .../insights` (AI briefing, `?fresh=1` forces regen). All `@position2_required`.

**Data model/source** (`tracker/slot_checker.py`): live source is a Google Sheet (`SLOT_CHECKER_SHEET_ID`), read via `GOOGLE_SA_JSON`, from two tabs (`LPs` = location registry, `Locations` = one row per office+service per date), reshaped to match the legacy `.xlsx` parser's expected input. **Each row is an observation, not a fact** - a rescraped location appears multiple times across days with different counts, so "current availability" means the newest observation per (practice, service), never a naive sum. TTL-cached 300s. **Fallback:** any exception on the live read (revoked share, renamed tab, network error, quota) falls back to the committed `data/slot_checker_snapshot.json` - a live read that *succeeds* with zero rows is not a failure and renders as-is. To refresh the fallback: export the sheet to `.xlsx`, run `scripts/import_slot_checker_snapshot.py <file>`, commit the resulting JSON. **Note:** that script's own docstring is currently stale (still claims the sheet can't be read live by the service account - it can, as of this feature's build; worth fixing next time this file is touched).

**AI Insights** (`tracker/slot_checker_insights.py`): one Claude call synthesizing the dashboard's own derived numbers into a headline, synthesis, top actions, and a coverage note, under the same "never state anything not in the given JSON" rule as LPS's. **On-demand, not automatic** - triggered by opening the Insights panel, 1-hour TTL-cached and invalidated early if the underlying snapshot's `generated_at` changes, so it costs roughly one Claude call per cache miss, not per view. Degrades cleanly without `ANTHROPIC_API_KEY`. **`probe()` self-test exists but has no admin route wired up** (unlike LPS's `lps-insights-check`) - currently unreachable except by calling it directly.

**UI** (`templates/42_north_dental_slot_checker.html`, `static/css/42_north_dental_slot_checker.css`): 4 KPI stat tiles, 3 summary cards (availability by day, capacity by state, brand mix), a main card with 4 tabs (Locations - default, sortable/searchable table with a centered drawer; Calendar - heatmap; Needs Attention - alerts; Services), a split filters/sort-export toolbar, and a compact AI Insights card at the page bottom.

**Calendar heatmap - current, live color scheme (changed today, don't assume an older version):** 6 discrete steps by open-slot count (`RAMP_BREAKS=[2,5,10,20]`): 0 / 1-2 / 3-5 / 6-10 / 11-20 / 21+. **Vivid red-to-green** by hue step (not opacity-faded single-hue): dark theme orange `#f97316` -> amber `#f59e0b` -> yellow `#eab308` -> lime `#84cc16` -> green `#22c55e`; light theme uses deeper equivalents. The **zero-slots cell is not part of the color ramp** - a transparent cell with a red-tinted hairline border and a red dash through the middle, deliberately signaling "needs action" rather than "no data." **This ramp was churned same-day**: it started black-to-green (opacity-faded single teal hue), was changed to this vivid red-to-green version, briefly tried a desaturated/pastel version, and was reverted back to the vivid version per explicit user feedback ("this looks terrible"). The vivid version above is the correct, current, live state - do not reintroduce the pastel one without being asked.

**Real bugs fixed:** a white flash on fast scroll (the page's `background:` shorthand set only a gradient image, silently zeroing the longhand `background-color`, so overscroll painted the browser's default white canvas in both themes - fixed with an explicit `background-color` declared as its own line after the shorthand); a hover tooltip rendering invisibly behind the centered drawer (a stale `z-index`, fixed plus a `click` handler added since hover never fires on touch).

**Known-open items:** the live-sheet-reliability caveat above; the stale docstring in the import script; the missing admin self-test route for AI Insights.

**Env vars:** `GOOGLE_SA_JSON`, `ANTHROPIC_API_KEY` (opt), `ANTHROPIC_MODEL` (opt, default `claude-sonnet-5`).

---

## SOCIAL CREATIVE INTELLIGENCE ANALYST (built in the v26 cycle; almost all of the v27 cycle went into its report)

**What it does, end to end:** given a company name/URL, resolves its handles across **Instagram, LinkedIn, X, TikTok, YouTube, Facebook** (six owned accounts) and separately reads **Reddit** as a seventh surface (see below), collects recent organic posts, runs **every image and video through Claude vision** (plus Whisper audio transcription for spoken dialogue in video) to describe what the creative actually shows - not just captions or engagement counts - then a Claude synthesis pass writes a cited, per-platform + cross-platform report on content patterns, messaging/strategy, and what correlates with engagement. Internal, staff-only (`@position2_required`), route base `/p2/b2b-agents/social-creative-intelligence`. Built and shipped across three explicit phases (Instagram+YouTube, then Facebook/TikTok/X/LinkedIn, then classification+synthesis+report UI+audio) plus many follow-up fixes, all in this cycle.

**Pipeline** (`tracker/sci_pipeline.py`, one daemon thread per run, platforms processed sequentially, each in its own try/except so one platform's failure never blanks another's): **Identify** (`sci_identify.py` - one Claude call using the `web_search` tool, versioned-fallback across three dated tool-type strings since Anthropic sunsets old ones server-side, streamed via `messages.stream()` not a blocking call since a 6-platform/15-search lookup routinely exceeds a short timeout; refuses to guess - only `high`/`medium` confidence handles are trusted downstream) -> **Collect** (per-platform adapters, see vendors below) -> **Creative analysis** (`sci_vision.py`/`sci_video.py`/`sci_audio.py` - per-post, staged so one slow/failed video never blocks the rest) -> **Classify + Synthesize** (`sci_classify.py` pure aggregation, `sci_synthesize.py` one Claude call producing cited claims tied to real post ids).

**Vendors, two now, additive not exclusive:**
- **Apify** (`tracker/apify_transport.py` + per-platform `sci_source_<platform>.py` adapters) - actor-based scraping for Facebook/TikTok/X, and a fallback path for Instagram. `APIFY_API_TOKEN` **has never actually been set in production** - confirmed via a dedicated Apify pricing/tier research pass this cycle finding zero existing spend anywhere. LinkedIn is **feature-flagged off by design** via Apify (`SCI_APIFY_LINKEDIN_ACTOR_ID` has no default; unset means the pipeline never even calls Apify for it) - LinkedIn is the platform most exposed to scraping-detection/ToS enforcement.
- **Unipile** (`tracker/unipile_client.py` + `tracker/unipile_transport.py` + `sci_source_linkedin_unipile.py`/`sci_source_instagram_unipile.py`) - a fundamentally different model: connects one real, authenticated account per platform via a hosted-auth link a human clicks through, then acts through that account. **LIVE for LinkedIn as of this cycle** (`9ae0b54`), and the workspace currently holds 17 LinkedIn accounts, 11 of them working. LinkedIn and Instagram try a connected Unipile account **first**, falling back to the pre-existing Apify path unchanged if none is connected: purely additive, zero behavior change wherever Unipile isn't configured. **No local table tracks which account is connected** (deliberately - `unipile_client.list_accounts()` is queried live every time, the same reasoning that prevents Arena's LinkedIn-connection-lapsing problem, described above in the LPS section, from being invisible here too). `sci_platform_runs.source_vendor` + a "via Unipile"/"via Apify" badge per platform section makes which vendor served which platform visible. See the dedicated Unipile subsection below.

**Data model** (`tracker/sci_store.py`, Postgres): `sci_runs` (one row per analysis), `sci_platform_runs` (one row per platform per run, own status so one platform failing doesn't blank the others; `source_vendor` column added in the v27 cycle), `sci_posts` (one row per scraped post, creative_analysis JSONB attached once complete), `sci_spend_log` (schema exists, not yet enforced against a cap - future phase).

**Reddit, the 7th surface** (`tracker/sci_reddit_client.py`, `sci_reddit_pulse.py`): deliberately **not** a company account. It answers "what are people saying about this company", via app-only OAuth (Reddit's unauthenticated `.json` endpoints now 403 with an HTML block page even from a residential IP, so there is no unauthenticated fallback). Renders as sentiment, subreddits, recurring themes, competitor mentions, top threads and a timeline. A community found for the brand appears in the account directory explicitly labelled "not a company account". **Currently inert: `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` are unset**, and the normalizer is fixture-proven only.

**Report UI** (`templates/social_creative_intelligence.html`, ~2,700 lines with the report logic inline as JS; `static/css/social_creative_intelligence.css`, ~1,300 lines): an input form with live company-search-as-you-type (native, Apollo-backed via `sci_company_search.py` - deliberately NOT Arena, since Arena's own workspace periodically loses its connected LinkedIn account, the exact fragility documented in the LPS section above), a run-history list, and a centered-modal report drawer.

The drawer is **a rail of panes**, not one scroll: an overview pane, a cross-platform pane, then one pane per platform. Panes are built from a shared chart primitive set - `trendSvg` (magnitude over time), `rankBars` (horizontal, because the labels are words), `mixBar` (one 100% bar plus a named key), `donut`, `heatGrid` (five discrete steps, never continuous alpha - nobody reads a 6% opacity difference), and `radarSvg` (profiles across shared measures). **One delegated tooltip/highlight layer serves the whole page**, not one per chart: `data-tip` builds a tooltip, `data-hi` dims everything in the same chart that is not the hovered key, which is what welds a legend row to its bar segment. Charts draw once, on the pane the reader is actually looking at (`revealCharts`), with staggered entrances.

Chart rules that are load-bearing and easy to undo by accident:
- **Hues are assigned by identity, never by array position, and never cycled.** `seedFormatHues` decides once; `formatCi` is a pure lookup, never an assignment (an earlier version assigned lazily on first ask and was correct only by accident of render order).
- **Each scorecard column is measured against its OWN column max.** Interactions and posts-per-week share no unit, and one shared scale makes cadence invisible beside a five-figure like count. The radar view inherits exactly this normalisation, which is why it is the same numbers rather than new ones.
- **The radar only draws measures every compared platform reports.** LinkedIn publishes no view counts; plotting it at zero views is a different claim from not measuring it. Past three platforms it becomes small multiples rather than overlaid polygons.
- **Gradients and patterns are referenced by id and a pane draws several trends, so each stamps its own** (`SVG_UID`). A shared id silently repaints the first chart with the second's hue.
- **`.sci-acc-off` dims via explicit colours, never `opacity`.** An ancestor opacity that halves contrast is invisible to a checker reading computed `color`, which is exactly how unreadable text survives a green audit.
- **No URL is ever constructed from a handle.** `absUrl()` completes a bare domain to `https://` and returns null for anything carrying another scheme, rather than prefixing `https://` onto a `javascript:` payload.

**Admin-only "Data sources" panel** on the page itself shows live Unipile connection status per platform and a Connect button generating a hosted-auth link - nothing in this codebase can complete that login on someone's behalf. A "via Unipile" / "via Apify" / "via YouTube API" / "via Reddit API" badge per platform section makes which vendor served which platform visible.

**Print is a different medium and is styled separately:** the section link chips are hidden on paper (they name a destination without showing one), the account directory stays and prints real URLs as text, the dot floor and loading skeletons are dropped, and every label is re-inked for white.

**Real bugs fixed, root causes worth remembering:**
- **Every platform failing identification at once, twice, back to back, both in `sci_identify.py`:** first, a hardcoded single dated `web_search` tool version got sunset server-side (fixed with an ordered fallback list + a self-healing process-cache); then, once that bare-except stopped swallowing the real exception, the SAME failure shape recurred but now showing the real cause - `anthropic.APITimeoutError`, since a 6-platform/15-search lookup routinely exceeds a 90s blocking-call timeout (fixed by switching to `messages.stream()` plus a 280s backstop). **The lesson: a bare `except Exception` collapsing every failure into one generic string is what made two structurally different bugs look identical** - fixing the error-swallowing was as important as fixing either bug.
- **YouTube video creative_analysis null for ~100% of posts:** `yt-dlp` frame extraction gets blocked by YouTube's bot detection from datacenter IPs (Railway included) - a systemic failure, not flakiness. Fixed by falling back to the platform's own free static thumbnail (already returned by the Data API, previously discarded) whenever frame extraction returns nothing.
- **Messaging/strategy depth was a schema gap, not missing AI:** the vision schema only ever asked for pure visual description - no field existed for messaging/tone/CTA/hook, so synthesis had nothing to draw on even in principle. Fixed by adding those fields to the vision schema and requiring a distinct `messaging_and_strategy` narrative in synthesis, tied to what actually drove engagement.
- **Report was "wall of text" with broken video thumbnails:** `media_urls[0]` for a video post is the playable file, never a displayable image - every `<img>` silently 404'd via its own onerror handler. Fixed with a client-side `postThumbnail()` resolver reading each platform's real cover image out of the post's raw payload, plus converting synthesis prose from single paragraphs to short bullet lists with citations moved out of inline text into real linked post cards.
- **A copy-pasted broken-image-fallback bug, 4 times in one file:** every `onerror` handler called `.remove()` before `.closest(...)`, so `closest()` always failed on the now-detached node - silent (no visible error) because nothing had ever tested a real reachable-but-broken image URL until logos were added. Fixed by reordering all four call sites.

**Known-open items:** `APIFY_API_TOKEN` still not set, so Facebook/TikTok/X collection is fully inert; `YOUTUBE_API_KEY` not set; Reddit's credentials unset; Instagram through Unipile unverified. **The failure mode is silent by design:** Unipile unavailable -> fall through to Apify -> no Apify token -> that platform ends as `scrape_failed`. Nothing errors; those platforms simply show no collected posts, which is easy to misread as "this company posts nothing there". LinkedIn is the one platform where that chain now terminates in a real read.

---

### UNIPILE IS NOW LIVE FOR LINKEDIN (`9ae0b54`, `54faee4`) - the biggest correction in this cycle

At v27 this integration was inert and every route in it was a guess. The user supplied a working DSN (`api27.unipile.com:15703`) and API key, and **the client was rewritten wholesale against live responses**. LinkedIn company posts now collect through a real authenticated account. What was wrong, in the order it was found, because each one is a general lesson:

1. **The path prefix.** `unipile_client` shipped with `/v2/...` everywhere. The live API is **`/api/v1`**; `/v2` and bare `/v1` both 404. The v27 doc confidently recorded "the docs say `/api/v1`, the live API is already on `/v2`" and had it exactly backwards, because that probe was run against the shared `api.unipile.com` gateway, **which is a different service**. A key is valid only against its own DSN.
2. **Where an account's status lives.** `acct.get("status")` returns `None` for every live account. The real usability is at **`sources[].status`**. This mattered enormously: of the 17 accounts in the workspace, **6 were `CREDENTIALS` (signed out) and the FIRST listed account was one of them**, so `accounts[0]` would have failed every single collection while the admin panel cheerfully said "connected". `account_status()` / `is_connected()` / `connected_only=True` exist for exactly this.
3. **The hosted-auth link requires `expiresOn`.** Every Connect button was returning 400. And the field wants a specific millisecond-precision-with-`Z` format that `isoformat()` does not produce, so `_expires_on()` builds it by hand.
4. **The posts endpoint rejects vanity slugs.** It wants the numeric company id, so `resolve_company_page` / `get_company` resolve first.
5. **The last page still returns a cursor.** Paging on "cursor is present" never terminates; page emptiness is the terminator.

**The wrong-company-page trap (`54faee4`), found by actually running the thing.** `@notion` resolves to a real, live LinkedIn page belonging to a 39-person consultancy, which returns an empty post list perfectly cleanly and lands as `no_presence`. **Both pages are literally named "Notion"**, so a name check alone confirms the impostor. Only the `website` field separates them. `verify_company_page()` therefore returns one of `domain` / `name` / `none` / `mismatch`:

- domains agree (by suffix containment, so `www.acme.com` and `shop.acme.com` match but `acme.co.uk` and `rival.co.uk` do not) -> `domain`, silent.
- domains present and disagree -> `mismatch` only if the names ALSO disagree, so a legitimately different domain does not cause a false rejection.
- no domain to compare -> falls back to the name.

A mismatch raises **`CompanyMismatch`, deliberately NOT a subclass of `UnipileTransportError`**, so a wrong handle is never retried through Apify as though it were a transport failure. Anything short of `domain` verification attaches a note that the report prints: "Read linkedin.com/company/x (Name, N followers, no website listed), which has no posts. That page could not be confirmed as this company's own, so this may be the wrong page rather than an empty one."

**Judgement calls made and worth preserving:** `posted_at` is read only from `parsed_datetime`/`posted_at`, never the relative `date` string; `_media_urls` orders video URLs first, then images, then `article.picture_url`; `_usable_attachments` drops `unavailable:true` entries; `_metrics` omits a falsy `impressions_counter` but preserves a genuine zero likes/comments/shares, because "nobody engaged" is data and "not reported" is not.

**Instagram through Unipile is still unverified.** All 17 accounts in the workspace are LinkedIn, so `sci_source_instagram_unipile.py`'s field names remain docs-derived guesses and are marked as such in the module. Connect an Instagram account through the Data sources panel, then check `normalize()` against one live response before trusting it.

**The Data sources panel** now reports both numbers: "11 accounts working, 6 need reconnecting" / "3 accounts signed out + Reconnect" / "Connect". Listed and working are genuinely different facts and the panel used to conflate them.

**Env vars:** `APIFY_API_TOKEN` (opt, NOT SET), `YOUTUBE_API_KEY` (opt, NOT SET), `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`/`REDDIT_USER_AGENT` (opt, NOT SET), `SCI_APIFY_<PLATFORM>_ACTOR_ID` x5 (opt overrides; LinkedIn's has no default, load-bearing differently from the rest), `UNIPILE_API_KEY` (**set and working**), `UNIPILE_DSN` (**set and working**, `https://api27.unipile.com:15703`). Reuses the platform's existing `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` and `OPENAI_API_KEY` (Whisper) - no new AI vendor account needed.

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
├── app.py                ← Flask server (~18,067 lines, 203 route decorator lines): auth
│                            (3 decorators + client gate), all 4
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
│                                      (built in the v26 cycle; Unipile made live in v28)
├── tracker/event_intel_*.py (store, rubric, harvest, recover, resolve, enrich, discover,
│       audit, scorer, report, workroom, pipeline, intake) + tracker/claude_websearch.py
│                                      ← Event & Conference Intelligence (built at v28; this
│                                      whole v29 cycle hardened it and added outcome-learning
│                                      + cross-client intelligence on top of its Postgres store)
├── tracker/job_change_parser.py, tracker/job_change_store.py ← Job Change Alert
├── scripts/sync_job_change_alerts.py, scripts/import_job_change_tracked_snapshot.py,
│       scripts/import_slot_checker_snapshot.py ← subprocess-only scripts, never imported
├── tests/                ← 131 files, ~3,619 offline tests + 32 dedicated real-Postgres
│                            tests. test_cpi_*.py, test_job_change_*.py, test_sci_*.py,
│                            test_unipile_*.py, test_event_intel_*.py (by far the largest
│                            single group now), test_agent_page_grid.py, one per audit/feature.
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
│   │        linkedin_playbook_studio, 42_north_dental_slot_checker, social_creative_
│   │        intelligence, event_conference_intelligence.css - the last one new this cycle)
│   └── js/ (theme, linkedin, visitor_track, pfx_bg, aurora, anonymous_visitors,
│           company_people_intelligence.js, scroll-rail.js)
├── templates/
│   ├── agents.html          ← THE SINGLE SHARED MARKETING TEMPLATE, {% if page %} variants
│   ├── app.html, app_base.html, app_embed.html, app_history*.html, app_settings.html
│   ├── hub.html, b2b_agents.html, seo.html, accounts.html, embed.html, context.html, 403.html
│   ├── company_people_intelligence.html   ← Contact Finder
│   ├── job_change_alert.html, linkedin_playbook_studio.html, 42_north_dental_slot_checker.html,
│   │       social_creative_intelligence.html, event_conference_intelligence.html
│   │       ← the last one new this cycle
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
- **Google Sheets** is the primary store for internal analytics AND for 42 North Dental Slot Checker's live availability data. Job Change Alert's tracked-roster sheet is currently blocked (Workspace DLP) and falls back to a committed snapshot.
- **Postgres** (`DATABASE_URL`): `agent_run_history`, `cpi_search_history`, Contact Finder's persistent caches, `lps_runs`/`lps_playbooks` (LPS), `sci_runs`/`sci_platform_runs`/`sci_posts`/`sci_spend_log` (SCI), `evi_events`/`evi_participants`/`evi_sources`/`evi_runs`/`evi_profiles`/`evi_candidates`/`evi_outreach`/`evi_outcomes` (Event & Conference Intelligence - `evi_candidates` gained `name_key`/`format` columns and `evi_outcomes`/`evi_runs` gained `profile_id` this cycle; `evi_profiles` gained `confidential` for the new cross-client feature). **This is the only Postgres store on the whole platform doing genuine cross-run, cross-client learning as of this cycle** - see the "make it crazy good" section above.
- **SQLite** (committed): `data/tracker.db` (Healthcare), `data/tracker_csg_v2.db` (CSG), `data/tracker_northstar.db` (NorthStar), `data/job_change_alerts.db`. **Gitignored, real PII, NEVER commit: `data/identity_graph.db`.**

---

## VIMI, DE-ANON, STITCHING, AND THE OTHER SURFACES (unchanged)

- **Vimi** (label **GTM**): two backends, `/api/ppc-chat` (widget, `@position2_required`) and `/api/vimi-chat/<account_id>`. Never mix Healthcare and CSG in one answer.
- **Anonymous Visitors / de-anon:** `visitor_intelligence/`. Company-level multi-signal IP resolution, connection-type hard gate, noisy-OR confidence, Apollo enrichment, 0-100 intent. Person-level: persistent SQLite identity graph. **Never fabricates a person.**
- **`p2_vid` stitching:** Page Views and both login tabs carry a visitor-id column.
- **Surface 2, `/app`:** shell `app_base.html`, `APP_AGENTS` cards (minus `HIDDEN_AGENT_SLUGS`), a few wired to live seo-apps tools plus LinkedIn Social Researcher (currently hidden), the rest request-access-only.
- **Surface 1, public site:** one template `agents.html`, `{% if page %}` chain.
- **Surface 3, `/p2/*`:** `/p2/hub`, `/p2/b2b-agents` (Contact Finder, Job Change Alert, LinkedIn Strategy Researcher [native], 42 North Dental Slot Checker, Social Creative Intelligence Analyst, Event & Conference Intelligence, sentiment-pulse MOCK data, ad-intelligence React app, linkedin-intelligence, linkedin-social-researcher), `/p2/seo` + tools (16), `/p2/abm-signal-tracker/accounts` + signal trackers, `/p2/playbook`, admin dashboards.

**Agent roster hazard, worse than ever now:** the roster exists in **three independent lists** (`AGENTS`, `APP_AGENTS`, and a JS array in `templates/context.html`), plus the internal SEO Suite tools list, plus `HIDDEN_AGENT_SLUGS`, plus now FOUR agents this cycle alone (Job Change Alert, LPS, Gentle Dental, SCI) each with their own hand-written `b2b_agents.html` card and command-palette entry that derives from none of the above. **Nothing derives one from another.**

---

## SHARED UI CONVENTIONS (the grid, and the /p2/b2b-agents scroll rail)

### THE SHARED RESPONSIVE GRID (`b151ab0`, `fcc0c39`, `bf0968d`) - read this before styling any agent page

`static/css/grid-tokens.css` defines the platform's page geometry as custom properties only, no element rules, so it can be added to any page without restyling it:

```
--margin  20 -> 32 (428) -> 48 (768) -> 80 (1280) -> 120 (1440) -> 160 (1728) -> 200 (1920)
--gutter  16 -> 24 (768) -> 32 (1440) -> 40 (1728) -> 48 (1920)
--maxw    1920px
--bleed   max(--margin, (100vw - --maxw)/2 + --margin)   /* side padding for a full-width bar */
```

**The defect this cycle found:** the file was *linked* from pages that never *read* it. Event & Conference Intelligence hand-typed `padding: 34px 30px` and capped at 1440px, so on a laptop it ran nearly edge to edge while every sibling kept a real margin. Contact Finder and LinkedIn Intelligence had `.topbar{padding:0 32px}` while their content sat at `var(--margin)`, so at 1512 **the logo was 88px inboard of the content it sits above**. And the account pill was pushed off the right edge on a phone by up to 104px.

**The shape every agent page now uses:**

- `.main { max-width: 1320px; padding: 36px var(--margin) 72px }` (or that page's own cap; 1320 is the top of the existing band)
- `.topbar { height: 62px; padding: 0 var(--bleed) }` so the bar lines up with the centered container
- page-level column grids take `var(--gutter)`
- below 640px the breadcrumb's ancestors hide and the current page ellipsises

**`tests/test_agent_page_grid.py`** is the guard: 31 checks over all ten agent stylesheets asserting the container takes the margin token, the bar takes `--bleed`, and every bar is 62px. **This regression is invisible on the page it happens to and only shows up beside a sibling**, which is exactly why it survived so long, and exactly why it needs a test rather than an eye.

**Deliberately NOT converted, do not "fix" these:**

- **Hub.** It looks like the same defect (content at 24px, no cap) but it is a centered full-bleed splash with an edge-to-edge starfield and marquee, not a left-aligned content page.
- **`embed.html`** keeps `--margin-app`, not `--bleed`. `--bleed` aligns a bar with a `--maxw`-capped container; that page is a full-screen iframe with no container, so `--bleed` would inset its bar 520px at 2560px wide.
- **The 35 `auto-fit` grids** across these pages. `auto-fit` is correct for an open-ended list. It was only wrong on Event Intelligence's two radiogroups, where a fixed set of four meant to read as one choice was being dealt 3 + 1 with a hole in the row.
- **Contact Finder's column gap**, held at 22px with a comment saying so. `--gutter` widened it to 32px at 1512, taking 10px off the filter column and reflowing the seniority chips from 9 + 2 to 8 + 3. Consistent, but a visible cost with no visible benefit on the page about to go to external clients.
- **The `--fs-` type scale.** No agent page uses it; converting one would make it the odd page out rather than a consistent one.

### The scroll rail

**`static/css/gtm.css` zeroes the native scrollbar on everything it touches** (`*::-webkit-scrollbar{width:0!important;display:none!important}` plus `scrollbar-width:none`). That is a deliberate look, but it means pages using it have no scroll affordance at all: nothing showing how far the page runs, and nothing to drag. `/p2/b2b-agents` is roughly two screens of agent cards with no indicator.

**`static/css/scroll-rail.css` + `static/js/scroll-rail.js`** put one back, currently linked from `templates/b2b_agents.html` **only** (a test asserts that scoping, since picking it up elsewhere would double up with pages that still show a native bar). A hairline down the right edge: 2px track and 3px gradient thumb at rest, 4 and 5 on hover, wearing the aurora palette (`--au-brand` green -> `--au-brand2` blue -> `--au-brand3` purple) so it reads as part of the surface rather than a browser control. Proportional height, drag to scrub, click the bare track to jump.

Details that are load-bearing:
- **Fixed and overlaid, never a gutter.** The page reserves no scrollbar width, so anything taking layout space reflows the whole card grid inward.
- **Not `display:none` in its off state.** The script sizes the thumb from the rail's own `clientHeight`, and an undisplayed rail measures zero, which would leave it permanently off with no way back. It uses `opacity` + `pointer-events` instead.
- **Off entirely** when the page does not scroll, and on coarse pointers (a 13px drag target is not a touch target, and phones draw their own indicator).
- **`aria-hidden`.** It is a visual affordance over scrolling that already works from wheel and keyboard; exposing it would add a tab stop offering nothing the arrow keys do not already do.
- **Its resting opacity is a measured value, not a taste call.** Drawn at `.34` it sampled at 1.96:1 against the page behind it, and an indicator nobody can see is not indicating. It rests at `.62` in dark and `.74` in light, because a translucent mark washes out far faster on a bright ground; light also re-steps the gradient darker. Both themes clear 3:1 at rest and hovered.

**Reusable elsewhere:** any page that pulls in `gtm.css` and scrolls has the same missing-affordance problem. Adding the two `<link>`/`<script>` lines is the whole integration; update `test_b2b_agents_scroll_rail.py`'s scoping assertion when you do.

---

## BRANDING + THEME (unchanged)

"Arena" mark: bright-green hexagon `#55be8c` + steel-blue + dark-green petals = 6-point star. `theme.js` (`localStorage['p2-theme']`, default dark). Hard sign-out: `/logout` sends `Clear-Site-Data` + explicit cookie deletion. Bricolage Grotesque is the public body font.

---

## ENVIRONMENT VARIABLES

**Railway:** `DATABASE_URL`, `GH_DISPATCH_TOKEN`, `GMAIL_SENDER`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_SA_JSON` (Sheets read - internal analytics AND 42 North Dental Slot Checker), `LOGIN_LOG_SHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INSIGHTS_MODEL`, `SECRET_KEY`/`FLASK_SECRET_KEY` (confirmed set to a strong value), `SERP_PLATFORM_TOKEN`, `SLACK_BOT_TOKEN` (post-only scope confirmed; read-scope for `#job_change_alert_apollo` history unverified), `SLACK_CHANNEL_ID`, `SLACK_WEBHOOK_URL`, `DEMO_REQUEST_SHEET_ID`, `ANON_VISITORS_SHEET_ID`, `AD_INTEL_SHEET_ID`, `DEMO_NOTIFY_EMAIL`, `IPINFO_TOKEN` (opt), `IDENTIFY_TOKEN` (opt), **`APOLLO_API_KEY` (Contact Finder + de-anon + person enrichment + SCI's own company search all depend on this one shared key/pool)**, **`ARENA_API_KEY` (LinkedIn Strategy Researcher's entire vendor backend depends on this - was missing from prior context-file revisions despite being load-bearing since before v25)**, **`ANTHROPIC_API_KEY` (shared across MANY features now: Contact Finder's Claude cross-check, LPS's AI Insights, Gentle Dental's AI Insights, and ALL of SCI's identify/vision/synthesis calls - **confirmed SET on Railway** as of 2026-08-21, so the Claude cross-check in Contact Finder is live, not inert)**, `ANTHROPIC_MODEL` (opt, defaults to `claude-sonnet-5`), `VI_ENRICH_ON_VIEW` (opt), `VI_COOP_FILE` (opt), `VI_GRAPH_DB` (opt), `SMTP_*` (unusable on Railway).

**GitHub Actions secrets (separate store from Railway):** `CONFIG_YAML`, `GOOGLE_SERVICE_ACCOUNT_JSON`, and `SLACK_BOT_TOKEN` needs adding here too for the Job Change Alert sync workflow.

**Social Creative Intelligence Analyst:** `APIFY_API_TOKEN` (opt, NOT SET - Facebook/TikTok/X collection all degrade to scrape_failed without it; Instagram falls back to this too but tries Unipile first), `YOUTUBE_API_KEY` (opt, NOT SET), `SCI_APIFY_INSTAGRAM_ACTOR_ID` / `SCI_APIFY_FACEBOOK_ACTOR_ID` / `SCI_APIFY_TIKTOK_ACTOR_ID` / `SCI_APIFY_X_ACTOR_ID` (opt overrides), `SCI_APIFY_LINKEDIN_ACTOR_ID` (opt, no default - LinkedIn stays fully disabled via Apify until set).

**Social Creative Intelligence Analyst - Reddit (7th platform):** `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` (opt, **NOT SET** - both required; create a *script* app at https://www.reddit.com/prefs/apps), `REDDIT_USER_AGENT` (opt, has a sane default - Reddit throttles and blocks generic User-Agents much harder than well-identified ones, so set a unique descriptive string). Verified live while building: Reddit's unauthenticated `.json` endpoints (`/search.json`, `/user/<n>/submitted.json`, `/r/<n>/about.json`) now return **403 with an HTML block page** even from a residential IP, so there is no unauthenticated fallback and the app-only OAuth path is the only one that can work. The token endpoint answered bogus credentials with a clean `401 {"message": "Unauthorized"}`, so the route is real and only credentials are missing. Admin self-test: `POST /p2/admin/external-usage/sci-reddit-check` (distinguishes "token minted" from "authenticated read succeeded", which are genuinely different failures here). Reuses `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` for the conversation analysis.

**Social Creative Intelligence Analyst - Unipile (LIVE as of this cycle):** `UNIPILE_API_KEY` (**set on Railway and working**) and `UNIPILE_DSN` (**set on Railway and working**, `https://api27.unipile.com:15703`). Both are required together: **a key is valid only against its own DSN**, and authenticating the same key against the shared `api.unipile.com` gateway returns `401 invalid_credentials`, which is what made v26 and v27 read this as a bad key. The live API is at **`/api/v1`**, not `/v2` (the v27 doc had this backwards). Header is `X-API-KEY`.

**Reading account status:** `GET /api/v1/accounts` returns every account connected to the Unipile **workspace**, with no top-level `status` field. Real usability lives at **`sources[].status`**, where `OK` means working and `CREDENTIALS` means signed out. As measured while building: **17 accounts, all LinkedIn, 11 working and 6 signed out**, and the first account in the list was one of the signed-out ones. Always filter with `connected_only=True`; never take `accounts[0]`.

**Connecting an account:** `POST /api/v1/hosted/accounts/link` with `{"type":"create","providers":[...],"api_url":<dsn>,"expiresOn":<ms-precision UTC + "Z">}`. `expiresOn` is **required** and its regex rejects `datetime.isoformat()` output, which is why `_expires_on()` formats it by hand. Nothing in this codebase can complete the resulting login on someone's behalf; a human clicks it through. An account connected from Unipile's own dashboard is indistinguishable to us from one connected through our button, so either route is fine.

**Admin self-test:** `POST /p2/admin/external-usage/unipile-check` (free) reports `configured`, the `dsn` in use, per-account `{id, name, platform, status, connected}`, and both `by_platform` (listed) and `connected_by_platform` (actually usable). The SCI Data sources panel is the same data with a Connect/Reconnect button.

**Operational risk, unchanged and worth restating:** whichever account is connected inherits the exact risk that already breaks LPS. A connected LinkedIn account lapses periodically and collection silently stops working. It should belong to someone who will notice. Unipile flags roughly 100 lookups per account per day as automation; every collection currently runs through the same first healthy account, so spreading load across the 11 available would be cheap insurance at higher volume (noted, not built).

---

## HOW TO WORK ON THIS (proven-safe workflow)

1. **Clone fresh into the bash sandbox each session.** Sandbox network: `git` over `github.com` works; most external APIs are blocked, though outbound HTTPS to arbitrary hosts (e.g. `curl`-ing a vendor API directly to verify auth/routes) has worked when tried. WebSearch/WebFetch work.
2. Edit via file-edit tools or Python string-replace scripts (assert exactly-one match).
3. **Validate before every push, in this order:**
   - `PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q` (must be 2,838+ passing)
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
14. **Probe a live vendor API before writing a single route down, and probe it against the right host.** Every Unipile route in this codebase at v27 was wrong, including the ones a previous session had "confirmed", because that confirmation was run against the shared gateway rather than the workspace's own DSN. A vendor's own documentation lags its live API in both directions.
15. **When a vendor returns a list of connected things, find out where the real status lives before trusting the obvious field.** Unipile's accounts carry no top-level `status`; it is at `sources[].status`, and reading the obvious field would have made every collection fail while the admin panel said "connected".
16. **An empty result from a real, live, wrong record is the most dangerous shape there is.** A wrong-but-real LinkedIn company page returns zero posts perfectly cleanly. Verify identity before reporting absence, and say on screen which one you could not confirm.
17. **When a UI capability is tied to some other setting (a view, a mode, a filter), and that setting is persisted, the coupling becomes permanent and invisible.** Give the capability its own control instead. Contact Finder's stacking is the case study.
18. **A layout regression that is invisible on its own page and only shows up beside a sibling needs a test, not an eye.** `tests/test_agent_page_grid.py` exists for exactly this class of defect.
19. **Read the source play before implementing an agent that claims to implement it.** The Event & Conference Intelligence v1 was rejected outright, and the root cause was that the two `gtm-skills` plays it was built from had never been opened. When a play states a rule, make the code refuse it rather than asking a model to honour it.
13. **A vendor's OWN documentation can lag its live API, and so can your own notes about it.** v27 recorded that Unipile's docs said `/api/v1/...` while the live API was on `/v2/...`. The live API is on `/api/v1`, and that note was written from a probe against the wrong host. Confirm exact endpoint behavior against a real response, from the right base URL, before hardcoding anything.
20. **A green suite proves the code runs, not that the thing it claims happened actually did.** Four separate live-money runs of Event & Conference Intelligence each found at least one bug a fully-passing offline suite had missed - up through the FOURTH one. When a feature's whole job is reporting whether something real happened (a search ran, an audit returned a verdict, a database row was saved), stub as little as possible and run it against the real thing before trusting it, no matter how green the suite is.
21. **Any model call that writes output for N items in ONE reply needs its output-token budget scaled by N, not held to the number that was right for a single item.** This exact bug (a budget tuned for one item, silently truncating and discarding results once search narration ate into the same budget) was found and fixed twice on Event & Conference Intelligence, the second time in a call that could zero out an ENTIRE run's results rather than just one item's, because losing one over-budget batch call loses every item inside it.
22. **A search-tool call's input cost grows with roughly the SQUARE of how many searches happen inside that one call**, not linearly (measured live to ~1% accuracy on this platform - see `[[reference-anthropic-web-search-blocks]]`). Splitting one expensive call that fans out over a list into many small calls (one per item) is both cheaper in total AND more honest, because each item can now independently fail or say no rather than one call grading its own homework across all of them.
23. **HEAD + DETAIL is the report pattern for anything that must be both scannable and complete: one always-visible short line, with the full reason/name/count list folded behind a `<details>`.** Built for Event & Conference Intelligence's executive summary after direct user feedback that a technically complete, correct report was still "too text heavy, nobody would read it" - a report unread is the same as a report unsaid, especially for a section whose whole job is stating what could not be measured.
24. **When a feature crosses a privacy boundary the codebase has never crossed before (here: a query spanning more than one client's data), design the RETURN VALUE so it structurally cannot carry the sensitive field, not just the rendered text.** A pre-existing check in this same codebase (`genericness()`) still carries another client's real name in its own return value, safe only because every caller happens to render it carefully; the new cross-client-interest feature was deliberately built so no caller could leak it even by accident, and that guarantee has its own test that scans the entire raw structure rather than just what gets displayed.

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

1. **Rotate the GitHub token.** Pasted into chat each session; flag every session. Many pushes have now been made against the current one across the v28 and v29 cycles.
2. **Rotate the Unipile API key, the YouTube API key, and now also the Anthropic, OpenAI, and Apollo keys used across this cycle's live Event & Conference Intelligence runs.** The user explicitly deprioritized this for the v29 cycle ("don't think about key rotation for now, focus on making this error-free") - it is not forgotten, it was consciously deferred, and none of it has been done since. All six live in `scratchpad/.evi_keys.env` from prior sessions and need rotating through each provider's own console plus a Railway env var update; this needs the user's own login, it cannot be done from a sandbox session.
3. **Event & Conference Intelligence has now been run against real conditions four separate times this cycle** (see the agent's own section above for each run's cost/duration/result), but only against small, hand-scoped candidate sets - never against a large, realistic live vendor calendar. `RELEVANCE_GATE`/`CONSIDER_FLOOR` are tuned on 1-2 runs of real scores. The famous-event-audit promotion path is fixed at its root cause but has still never been directly observed promoting a real event end to end.
5. **Verify Instagram through Unipile.** All 17 workspace accounts are LinkedIn, so `sci_source_instagram_unipile.py`'s field names are docs-derived guesses. Connect an Instagram account through the Data sources panel, then check `normalize()` against one live response.
6. **Confirm the Data sources panel reads "11 accounts working, 6 need reconnecting"** in production. The admin routes are auth-gated, so the Railway env vars could not be verified from the sandbox; the user set them and reported success, and the client was verified against the live API directly.
7. **Restore LinkedIn Social Researcher** (the old external tool) to the listings when the owner asks - checklist in `[[project-lsr-hidden]]`.
8. **Set `APIFY_API_TOKEN`** (never has been) - Facebook/TikTok/X collection for SCI is fully inert without it.
9. **Fix `SLACK_BOT_TOKEN`'s scope/membership for `#job_change_alert_apollo`**, and add it as a GitHub Actions repo secret.
10. **Fix the Job Change Alert tracked-roster Google Sheet's sharing policy block.**
11. **Fix `scripts/import_slot_checker_snapshot.py`'s stale docstring** (claims the live sheet can't be read - it can).
12. **Wire an admin self-test route for `slot_checker_insights.probe()`**, matching the pattern every other AI-layer feature already has.
13. **Contact Finder's chat path has never run against live OpenAI + Apollo keys end to end.**
14. **Contact Finder residuals:** 120-row history cap truncates paged searches; zero-result searches never saved to history; `_cpi_probe_company_free` guesses only `.com`.
15. **Contact Finder's external client launch** is planned but not scheduled - the shared-credit-pool/`/credits`-aggregate exposure needs a decision once real external auth is designed.
16. **Hardcoded counts still in the codebase:** `ACCOUNTS["healthcare"]["description"]`'s "1,251", four places in `templates/agents.html`.
17. **Signal refresh secrets (blocking Healthcare refresh):** set GitHub Actions `CONFIG_YAML` + `GOOGLE_SERVICE_ACCOUNT_JSON`.
18. **Agent roster will drift again.** It now lives in `AGENTS`, `APP_AGENTS`, a JS array in `templates/context.html`, the SEO Suite tools list, `HIDDEN_AGENT_SLUGS`, and five hand-written `b2b_agents.html` cards with their own command-palette entries. **Nothing derives one from another.** Consider deriving them if the roster changes materially again.
19. **Fully connect the `/app` "Competitor Analysis" placeholder** once the live SEO Studio tool's per-client data scoping is addressed.
20. **NorthStar client-side portal adoption is minimal.** A relationship conversation, not a code fix.
21. **`data/identity_graph.db` is on Railway's ephemeral disk.** Move to a persistent volume or Postgres.
22. **Cold-visitor identification** needs a licensed identity feed. Plug point ready.
23. **ABM Signal Tracker maintenance mode:** periodically prune `data/northstar_signals_manual.json` by `signal_date` and re-run `seed_northstar_signals.py --prune`.
24. **An open, unverified accuracy question for Job Change Alert:** a third-party Coresignal-backed skill (Swan) reportedly catches job changes Apollo's own alert misses.
25. **LPS's Arena-connected LinkedIn account will lapse again**, and now so can the Unipile one. Recurring operational risk, not a one-time fix; the error messages name it directly when it happens.
26. **Set `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`** - SCI's 7th surface is fully inert without them, and `sci_reddit_client.normalize()` is fixture-proven only. **The user explicitly paused this work ("leave reddit for a while"); do not resume unasked.**
27. **The bklit-derived chart layer has one deliberate open judgement:** the radar is offered as a secondary "Shape" view with the table as default, because radar area genuinely invites a wrong read. If it ever becomes the default, the note under it stops being enough.
28. **Spread Unipile collection across the connected accounts** rather than always using the first healthy one. Cheap insurance against the ~100-lookups-per-account-per-day guidance at higher volume. Noted, not requested.
29. **Advisory security/design audit (do not start without an explicit ask):** fail-closed `SECRET_KEY`/`GOOGLE_CLIENT_ID`, cookie flags, HSTS/security headers, CSRF, rate limiting, SSRF/`X-Forwarded-For` hardening; CSS token convergence, accessibility.
30. **Event & Conference Intelligence's two newest features (outcome learning, cross-client interest) are live-verified but have essentially no real usage history yet** - the outcome-adjustment gate (3+ decisions, 75%+ agreement) and the cross-client k-anonymity gates (3 distinct clients AND a 5-client population) will not visibly fire until there is meaningfully more real run history and more real clients than the 3 currently in the sandbox.
31. **Two Event & Conference Intelligence directions are explicitly scoped, written up, and blocked on the user, not on effort:** a structured events-database vendor integration (needs the user to pick a vendor and obtain a paid API key), and turning the already-built monitoring/alert code into a live, scheduled GitHub Actions job against production (needs the user's explicit go-ahead separately from writing the code, since an unattended recurring production job is a different risk class than a code push).

---

## COMPETITOR / ROADMAP (recorded, not built, unchanged)

Competitors: 6sense, Demandbase, ZoomInfo, Bombora, Common Room, Warmly, Clay, UserGems, Apollo, RB2B/Koala. Gaps: co-op topic intent, review-site intent, technographic change, champion job-change (partially closed by Job Change Alert, scoped to new-role-only), hiring-surge, earnings/10-K mining, event attendance, layoffs, PLG usage. Differentiators: generative-search/AI-answer visibility + agency execution + first-party web de-anon with a real engine + a working, deeply-audited (13 rounds) Apollo contact-finding surface with honest credit accounting + a live, Slack-sourced job-change detection feed with honest scope disclosure + a native LinkedIn competitive-strategy agent + **real Claude-vision creative analysis of organic social content across 6 platforms plus a Reddit brand-conversation read, not just metadata/metrics** (Social Creative Intelligence Analyst) - genuinely uncommon among the listed competitors, none of which look at the actual pixels of a competitor's creative.
