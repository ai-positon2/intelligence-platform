# Event & Conference Intelligence: Phase 1 implementation

Prepared 6 September 2026. Base commit: `a8896ca12ae8a7cdc7a4250e3aa57c191338b3be`. Local branch: `fix/event-intelligence-phase1`.

The implementation addresses the audited failures in report consistency, decision ownership, misleading cross-client signals, unsupported outreach claims, event admission, failed saves, and public-page fetching. It is ready for code review after the checks recorded below. It is **not yet cleared for production release**: the audit's native PostgreSQL and authenticated deployed-renderer exit checks remain open.

## Changes and their effect

| Area | Previous failure | Phase 1 behavior |
| --- | --- | --- |
| Report selection | Page, top five and CSV selected or ordered events differently; finished high scorers could displace upcoming events. | Persist one versioned selection with full rows, ordered buckets, analysis date and profile snapshot. Page/API/CSV use it. A profile edit does not rewrite an existing analysis. |
| Candidate conservation | Worth-a-look rows beyond the cap disappeared. | Retain them with an explicit over-cap disposition. Finished and excluded rows also retain their full evidence fields. |
| Decisions | A staff login's decision could affect another client or edition; reruns multiplied history. | New `evi_decisions` table has one row per profile and event identity. Source run and profile ownership are checked. History counts decisions without joining every candidate occurrence. |
| Decision controls | A successful save disappeared after reload. | Reload overlays current decisions on the stored selection. POST returns the refreshed report. CSV carries decision, note and analysis date. Controls use event identity. |
| Cross-client claims | Run counts and ambiguous client identities supported misleading interest/overlap claims. | Disable both interest and genericness comparisons in the recommendation path; remove historical interest and comparison metadata from presented reports. Confidentiality survives ordinary profile edits; non-boolean confidentiality inputs are refused. |
| Outreach | A model could invent a meeting, promise or personal action without triggering a phrase blacklist. | Every exposed opener, angle and fit explanation passes conservative enforcement. Openers use a bounded introduction without a conversation claim. Legacy stored drafts are checked on API and CSV reads. Review is required. |
| Eligibility | Discovery and promoted alternatives used different admission rules. | Shared checks enforce dates/window, geography, exclusions, identity confidence and an event-site source. Apply at confirmation, promotion and before scoring. Missing facts remain unconfirmed. |
| Empty and partial results | Unfinished research could look like an empty market. | Explicit completeness is required for a verified empty result. Failed categories, unconfirmed candidates and scoring failures remain distinguishable; partial recommendations carry a warning. |
| Persistence | Zero or partial saves could produce successful reports. | Verify recommendation write count, read-back count and identities; verify outreach save/read-back counts. Failed recommendation saves expose stored rows as incomplete, with no approved top five. |
| Spend | Empty/failed recommendation paths lost measured stage usage. | Retain usage on empty/failure exits and checkpoint completed discovery, audit, promotion and scoring stages. Preserve checkpoints if a later exception ends the job. |
| HTTP reads | A URL or redirect could target a private destination. | Validate public IP addresses, pin the actual connection to the validated IP, retain original TLS hostname validation, and revalidate redirects. Reject private/reserved destinations, URL credentials and nonstandard ports. |

## Data and compatibility decisions

- `evi_decisions` is an additive schema change created through the existing table-initialization mechanism. Its uniqueness key is `(profile_id, event_identity)`; profile and source run are foreign keys. No legacy rows are deleted or rewritten.
- Legacy `evi_outcomes` rows are not automatically copied or reused: their client and edition ownership cannot be established reliably. Old decisions therefore stop influencing recommendations until a user records them against an explicit report/profile. A reviewed migration can be added later.
- Event identity includes normalized name, full start date, country and city. Missing dates use run-local identity. This prevents regional, yearly and same-year edition collisions. It is deliberately conservative: spelling changes, rescheduling, geographic aliases and organizer renames can create separate identities. This is not a canonical event catalog.
- Old reports without a saved selection are reconstructed using their creation date and available profile data, and marked `reconstructed_legacy`. An original historical cap or ordering that was never stored cannot be recovered exactly.
- Decision state is current; recommendation ordering is historical. Changing a decision updates the buttons and export without silently rescoring a completed report.
- Profiles may be saved as drafts. Launching a profile-based run additionally requires buyer roles, verticals, geography and client website.
- Outreach is intentionally less personalized. Free-text booth notes remain internal context; the system does not translate them into an asserted meeting history. Company fit remains model-estimated and requires review against evidence.
- Existing report status compatibility is retained: a completed job can carry `completion_state=partial` plus visible warnings. Storage failures and wholly failed discovery are terminal failures.

## Validation

Final full-suite result: **3,647 passed, 36 skipped** in 88.03 seconds. The 36 database tests were then run separately through the isolated SQL adapter: **36 passed** in 1.64 seconds. The final focused check passed **191 tests**. `git diff --check` passed. Checks cover the actual template JavaScript, Flask routes, application SQL and controlled provider responses. No live paid model or Apollo calls were made for this implementation.

New regressions cover finished-event selection, saved ordering after profile edits, cap conservation, same-year editions, regional audit collisions, current decision overlays, private destinations and redirects, unsupported draft claims, uncertain eligibility, failed-discovery spend, failed/wrong persistence and incomplete report presentation. SQL tests cover same-login/different-client isolation, reruns counting once, confidentiality preservation and POST/reload/CSV parity with foreign-run refusal.

SQL was exercised with PostgreSQL's PGlite WASM engine through an audit-only adapter outside this repository. The adapter executes application SQL, but autocommits statements and does not emulate native connection lifecycle, rollback or concurrent transactions. Passing it does **not** prove native PostgreSQL behavior or deployment safety.

Four public organizer pages remained readable through the hardened fetcher, including the INBOUND redirect to UNBOUND. This is a transport compatibility check, not proof of event-fact or model accuracy.

## Remaining release checks

1. Run `tests/test_event_intel_store_postgres.py` against a fresh, disposable native PostgreSQL database, with the same major version as deployment. Do not point this test suite at production. Check table creation, foreign keys, unique upserts, rollback, and simultaneous writes from separate connections. The existing tests alone do not establish concurrent-write behavior.
2. Validate a staging deployment with authenticated access: complete a recommendation, compare visible selection/top five/CSV, change a decision, reload, edit the profile cap, and confirm the original report selection remains stable. Verify incomplete runs and old reports visibly retain their limitations.
3. Run a small paid-provider evaluation on agreed client profiles and an independently verified event set. Check admission precision, coverage, source accuracy, scoring rationale, costs, and refusal/partial paths. Production sign-in was still unavailable when rechecked; browser tabs remained on login pages.
4. Review the additive schema change and take the deployment's normal database backup. Release the backend and template together because the refreshed decision response and report contract change together.

If rolling back the application, keep the new table intact. The old application will not read new decisions and will restore the audited defects, so rollback is an operational fallback, not an acceptable permanent state.

## Still outside this patch

This patch does not establish field-level claim provenance, verify every extracted person/company against page text, calibrate model scores, provide a complete country taxonomy, or create shared client identity across staff accounts. A same-host source URL is an admission check, not proof that every event claim appears on that page. Free-text geographic scopes still need a structured model, particularly exclusions and complex region descriptions.

Durable jobs, restart recovery, cancellation, idempotency, account-wide budgets and full provider billing reconciliation remain open. Recommendation checkpoints retain observed completed-stage usage; a killed process or a model call that never returns usage cannot be fully accounted for this way. Lookup/recovery/workroom costs are not a complete ledger.

Accordingly, these changes improve supervised use but do not change the audit's conclusion to “autonomous client-ready.” Phase 1's implementation can be reviewed now; its production acceptance is still pending the checks above.
