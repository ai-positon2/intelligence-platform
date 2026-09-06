# Event intelligence Phase 3: plans and reported results

The first increment adds a plan and results page to completed lookup and recommendation reports. It is scoped to Event & Conference Intelligence. Phase 3 is not complete, and Phase 2's live Position2 benchmark and independent relevance review remain open.

## Behavior

The report's “Plan & results” link opens a client and edition selector. A report already tied to a client keeps that client fixed. A generic lookup requires an owned client profile before saving. Plans follow the existing conservative event identity (normalized name, full start date and geography); undated identities remain local to the run. Repeated research for the same client and identity reuses the plan. An organizer rename, date correction or location change can produce a separate identity; no fuzzy migration is attempted.

Users choose attend, exhibit, sponsor, host a side event, request meetings, or consider a future edition. Budget and access constraints are recorded for review, not automatically checked or fulfilled. Considering a future edition does not schedule monitoring.

Target company domains match exact normalized roster domains only. Published role, source URL, literal evidence status and roster-year context are displayed. Historical, unknown-edition and unverified rows cannot trigger a future meeting suggestion. Current/future-year roster support is labelled announced, not confirmed attendance or meeting access. The year check cannot distinguish two editions of the same event within one year; it remains literal source support, not independent verification. Missing matches do not establish absence. Recommendation reports without harvested rosters will have no established matches.

Reported conversations, meetings, qualified opportunities, pipeline, spend and effort are stored separately from attendance preferences. Blank means unknown; zero means reported zero. Actual results require a nonfuture as-of date. Monetary values share the selected currency; there is no conversion or causal ROI calculation. Plans do not alter relevance scores or preference learning.

## Implementation and boundaries

- `tracker/event_intel_planning.py`: validation, owner/edition checks, roster comparison and versioned persistence.
- `evi_execution_plans`: additive table keyed by profile and event identity. The existing store initialization creates it when the code is run against a database. No external database migration is performed by preparing this PR.
- Event-only GET/POST `/runs/<id>/plan` routes require a Position2 session. POST requires JSON. Foreign runs/profiles are rejected.
- PostgreSQL transaction advisory locks serialize edits for one plan. A stale version returns HTTP 409 instead of overwriting another tab's changes.
- `templates/event_intel_plan.html`: escaped server-rendered form with a JSON save request. The main event report receives only a link.

No CRM updates, email delivery, paid provider calls, infrastructure settings, or automated bookings are introduced. This increment is a separate branch and draft PR based on the Phase 2 branch; it is not a production rollout.

## Validation

Tests cover unknown vs zero results, invalid numeric/date inputs, domain normalization, evidence timing, client/edition isolation, rerun persistence, stale edits, authentication, JSON input and HTML escaping. A native PostgreSQL test races two saves and requires exactly one successful writer. The event-only CI workflow runs the storage and HTTP tests against PostgreSQL 16; isolated local SQL tests do not establish concurrency behavior.

## Remaining Phase 3 work

Fresh verified edition-fact reuse needs cache freshness, provenance, invalidation and cross-client isolation rules. Rich account/person context, organizer access routes and agenda fit need reliable sources. Action recommendations need evidence and constraint evaluation beyond the limited roster-based hint here. Results need attribution definitions and review before any learning or ROI claims. CRM, outreach and monitoring require separate integration scope. The live Position2 benchmark and human review still gate business-quality conclusions.

## Second increment: fresh-source extraction reuse

The catalog currently contains model-reported dates and other event facts, not independently verified facts. This increment therefore reuses only roster extraction after a fresh HTTP fetch returns identical readable text. It does not reuse resolver answers or client relevance scores.

The key includes the exact dated event identity, requested extraction URL, page kind, event name/host, source-text hash, extraction prompt hash, configured model and explicit extraction version. Entries expire for reuse after seven days, measured from extraction rather than cache access. Any change causes extraction again. Missing/invalid dates, truncated pages, empty results, chunk failures and incomplete originating jobs cannot provide hits. Fetch failures never fall back to an old roster. Pagination is fetched and checked anew, and each page retains its own roster-year evidence.

Reuse is scoped to the same signed-in account; profiles within that account can benefit because the extraction has no client scoring input. There is no cross-account catalog sharing. Only an originating run and job both marked complete qualify. A database worker fence protects cache writes from cancelled/expired workers. The new `evi_extraction_cache` table is additive and uses the existing schema initialization.

The source metadata records the originating run, extraction timestamp, a fresh source snapshot and reuse status. Original provider spend is not copied into the new run. Literal support and incomplete-directory caveats remain unchanged. Reuse avoids repeat extraction calls; it does not establish independent verification or improve accuracy of an incorrect original extraction. Bump `event_intel_cache.VERSION` whenever extraction parsing, chunking or support rules change. Expired entries are ineligible, but this increment does not add a background storage-purge job.

Validation includes two complete synthetic pipeline runs: both fetch the roster, only the first extracts it, and the second preserves provenance. Tests also cover changed inputs, expiry/version invalidation, account separation, incomplete results, and per-page year evidence. Verified event-fact sharing remains future work pending a real verification/review model.
