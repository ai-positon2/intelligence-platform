# Event & Conference Intelligence: Phase 2 engineering and release status

Prepared 6 September 2026. Branch: `feat/event-intelligence-phase2`, based on local Phase 1 commit `3355161`.

**The local implementation adds durable execution, research provenance, bounded extraction, richer client context and a Position2 benchmark harness. Phase 2 acceptance is still open.** Native PostgreSQL CI passed 63 tests, including process-kill and cancellation/write checks. Authenticated deployed-browser validation, paid-provider quality measurement and human calibration remain incomplete. The implementation is in draft PR #1. An isolated staging web/worker/database was provisioned before the user limited further work to agent-specific repository changes; production was not changed. No further Railway or Google configuration changes are in scope.

## What is implemented

| Area | Implementation | Practical limit |
| --- | --- | --- |
| Series and editions | Separate `evi_catalog_series` and `evi_catalog_editions`; conservative edition identity includes date and geography. An organizer-reviewed, host-restricted alias registry joins INBOUND and UNBOUND at series level. | This is an initial catalog. It does not automatically resolve arbitrary aliases, reschedules or geographic variants. Existing decision identity is preserved. |
| Field observations | `evi_observations` records individual reported fields, cited URL, observation time and support status, scoped to the originating run. Source records retain retrieval timestamps, text SHA-256 hashes and structured extraction coverage. | Model-reported fields remain explicitly unverified. Literal occurrence in page text is not independent semantic verification of every claim. |
| Client context | Persist the service description, selected offer and target company characteristics. Pass them into discovery/scoring and qualification, together with company domain, enrichment data, resolution status and source evidence. Require an offer when launching a profile-based run; existing profiles can supply a run-specific offer. | No revenue or headcount is fabricated. Shared client identity across staff and independently verified company matching remain open. |
| Roster extraction | Read overlapping bounded chunks; preserve the tail; retain good chunks when others fail. Withhold unsupported organization/role claims and remove unsupported person/domain fields. Carry evidence status and edition context on stored participants. | Directory coverage remains explicitly unverified. Presence tests can establish literal support, not all entity relationships. Difficult JavaScript-only directories still need measured, site-specific adapters. |
| Edition and availability | Withhold rosters whose headings name another edition. Record availability; known cancelled events and sold-out events without an explicit existing commitment fail admission. Promoted alternatives carry these fields too. | Unknown availability is not a claim that tickets are available. Mixed or undated pages require review. |
| Durable execution | PostgreSQL job queue, atomic job/run submission, idempotency keys, bounded active runs, renewable leases, completed-stage checkpoints and retry-safe replay. A separate worker process runs research. | Native transaction/concurrency checks passed; broader deployment behavior and load still require evaluation. |
| Cancellation and fencing | Owned cancellation endpoint and page control. Database triggers reject stale-worker mutations; a shared lock on the job row coordinates a valid write with cancellation or lease transfer. Failed/cancelled reports cannot retain an approved saved selection. | Calls reserved before cancellation may still be billed. A provider-side request cannot be recalled by cancelling the local run. |
| Usage and limits | Record each worker-managed model call's stage, model, prompt/input hash, tool version, raw usage, elapsed time and error. Reserve account call/token allowances before dispatch. Preserve unknown provider outcomes and avoid silently repeating them. | Limits are calls and estimated token allowance, not a guaranteed dollar ceiling. Provider invoices and retry/billing edge cases still need reconciliation. |
| Reproducibility | Use Python 3.12 and the tested constraints only in the isolated event validation environment (`benchmarks/event_intelligence/requirements.txt`). Save runtime package versions at submission, hash event code and the alias registry, and retain stage versions. | Linux installation passed in native CI; live SDK/provider quality still requires measurement. Code changes invalidate pending/recovering jobs rather than mixing versions. |
| Evaluation | Position2 scenario, dated organizer-sourced starter references, review format and an offline evaluator that refuses acceptance with missing labels, costs, latency or coverage. | The starter reference set is incomplete. No top-five quality, recall or calibrated-score result is claimed. |

## Worker behavior

The web route now writes a queued job rather than starting a research thread. Deploy a separate worker service using the same application revision and database:

```sh
python -m tracker.event_intel_jobs --migrate
python -m tracker.event_intel_jobs
```

`Procfile` includes the worker entry. The existing `railway.toml` starts the web service and must not be inherited as the worker start command or HTTP health check. Railway now documents legacy TOML configuration as unavailable to new services; configure the new worker through its currently supported service/IaC mechanism and validate the deployment preview. [Railway configuration guidance](https://docs.railway.com/config-as-code). A web-only deployment would accept jobs that remain queued, so provision and validate the worker before enabling this revision for users.

A worker claims a job with `FOR UPDATE SKIP LOCKED`, gives it a 90-second lease and renews every 20 seconds. A restarted worker can reclaim an expired job, up to three attempts. Completed research stages and model responses are reused; deterministic output writes are replayed after clearing that run's incomplete output rows. A code-version mismatch stops the job before clearing its existing data. An in-flight provider call with no saved response requires reconciliation rather than an automatic repeat.

The database worker token is connection-local. Mutation triggers require the matching, uncancelled, unexpired job lease and lock its row during the write. PostgreSQL documents `SKIP LOCKED` as suitable for queue consumers, and `set_config(..., false)` as session-scoped. These documented primitives support the design; they do not replace testing our implementation on the deployment's database. [SELECT locking](https://www.postgresql.org/docs/current/sql-select.html), [session settings](https://www.postgresql.org/docs/current/functions-admin.html).

New browser submissions send a request key. Repeating that key with the same inputs returns the same run; different inputs are refused. Compatibility callers omitting a key receive a fresh one and therefore do not get retry deduplication. API integrations should retain the same key across a network retry.

The isolated event validation constraints require Python 3.12 or newer. The event CI workflow selects Python 3.12 explicitly. Shared requirements, the platform runtime and unrelated workflows retain their original configuration.

## Operational configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | Required | Same PostgreSQL database for web and worker. |
| `ANTHROPIC_API_KEY` | Required for live research | Configure through the deployment's secret manager. |
| `ANTHROPIC_MODEL` | Existing platform default retained | Logged on each worker call; validate the chosen model live. |
| `EVI_MAX_ACTIVE_PER_ACCOUNT` | `2` | Queued plus running jobs per signed-in account. |
| `EVI_DAILY_CALL_LIMIT` | `100` | Maximum worker model-call reservations in a rolling 24 hours. |
| `EVI_DAILY_TOKEN_ALLOWANCE` | `5000000` | Operational allowance for input bytes, requested output and a tool-result allowance. |

These caps may cause a partial result on a broad search. They should be tuned against measured Position2 runs. The allowance is not measured billable tokens; the ledger preserves the provider's actual usage separately. SDK automatic retries are disabled for worker-managed event calls; the rest of the platform retains its previous behavior.

The ledger accounts for queued event research, including resolution, extraction, recovery, recommendation and qualification calls. Profile-draft requests and separately triggered Apollo enrichment are outside this worker ledger and retain their existing accounting. Account scope is the existing signed-in email, not a newly invented organization/tenant identity.

## Schema, retained data and rollback

Additive tables: `evi_catalog_series`, `evi_catalog_editions`, `evi_observations`, `evi_jobs`, `evi_stages`, `evi_provider_calls`. Existing sources gain metadata, participants gain evidence, profiles gain product/company fields, and candidates gain availability fields. Database functions/triggers enforce worker ownership on run/output mutations.

Provider responses and stage results are retained so research can resume without repeating paid work. They can contain client-context-derived text and must remain run-owned. The API's execution ledger returns metadata, not cached full response bodies. Catalog observations are also ownership-scoped. Retention/expiry policy and administrative reconciliation UI are still needed before broad production operation.

Before rollback, drain or explicitly cancel queued/running jobs. Rolling back both services without handling the queue would strand jobs. Keep additive tables intact; do not delete history as a rollback mechanism. Web and worker code must match.

## Validation performed

The workspace results document (`outputs/event-intelligence-phase2-results.md`) contains final counts and logs. The application SQL tests run through an isolated PGlite adapter. New tests exercise submission deduplication, different-input refusal, cancellation ownership, stale-worker write rejection, stage/model-response reuse, unknown provider outcomes, account limits, lease reclaim with output replay, run-owned observations, product context, long-page chunking, unsupported entities, wrong-edition rosters, aliases and benchmark refusal without review.

The adapter autocommits each statement and uses one underlying WASM engine; those local results alone cannot establish native locking or rollback. Subsequent Linux/PostgreSQL 16 CI ran all 63 focused tests successfully, with no skips, on implementation commit `7d44ad2`: https://github.com/ai-positon2/intelligence-platform/actions/runs/34039777943. Eight native-only checks cover simultaneous submission, distinct claims, budget reservations, rollback, three real process-kill boundaries, and cancellation while a valid write holds a lock. Crash probes use synthetic provider responses and advance lease expiry after killing the subprocess; they establish persisted replay behavior, not actual provider billing semantics. No shared Python workflow or dependency pin is changed by the final patch.

## Position2 benchmark

The user selected Position2. The starter scenario uses B2B demand-generation/growth services, marketing leaders at B2B SaaS/technology companies, US events and a twelve-month window. The offer, buyer/vertical emphasis and geographic scope are evaluation assumptions rather than approved commercial strategy. Public service descriptions are supported by Position2's own [home page](https://www.position2.com/), [paid marketing page](https://www.position2.com/paid-marketing) and [SaaS page](https://www.position2.com/saas).

The reference file contains MarketingProfs B2B Forum, Forrester B2B Summit North America and UNBOUND. It deliberately includes edition and access traps: the Forrester sponsor page names 2026 while the next event is 2027; UNBOUND's organizer identifies a rename and sold-out 2026 edition. Each fixture links to its organizer source. These are starter cases, not a comprehensive list of events Position2 should attend.

See `benchmarks/event_intelligence/README.md` for the review workflow. No paid benchmark has been run. Staging was provisioned with provider access, but its Google sign-in rejected the unregistered origin. Further infrastructure and OAuth changes were stopped at the user's request. One reviewer was nominated, but independent reviewer labels and cost/latency targets have not been supplied, so calibration and acceptance remain unmeasured.

## Release work still required

1. Native PostgreSQL 16 concurrency, rollback, process-kill and cancellation/write checks passed. Still validate the actual production major version, connection-loss behavior and real-provider billing outcomes before release.
2. Linux installation passed. Isolated staging services were provisioned before the scope restriction; HTTP health and unauthenticated access controls passed. Authenticated page/API/CSV parity remains untested because Google rejected the unregistered origin. Further infrastructure/OAuth changes are outside the user's current scope.
3. Expand and adjudicate the Position2 reference set independently. Agree actual offer/geography plus cost/latency thresholds, run the paid benchmark, and have two reviewers label relevance, critical facts and unsupported claims. Calibrate only after those measurements exist.
4. Establish retention, invoice reconciliation and operator handling for unknown provider outcomes. Add site-specific directory adapters only where real benchmark misses justify them.

The original Phase 2 exit condition—measured quality, reliability and cost targets on representative data—is not yet met. This patch is a reviewable engineering foundation for that validation, not a declaration of autonomous client readiness.
