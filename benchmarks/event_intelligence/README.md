# Position2 event benchmark

`position2.json` is a dated, organizer-sourced starter set. The user selected Position2; its offer, segment, geography and planning window are clearly marked evaluation assumptions. The file is not a complete event catalog or an approved attendance plan.

## Run and review

1. Use the profile in the JSON after resolving any business assumptions. Submit a run through staging with matching web/worker code and configured provider credentials. Export the authenticated run-detail JSON to a local file.
2. Independently expand the reference set before judging discovery recall. Have two reviewers label event relevance and critical facts without using the agent's score as the answer key. Adjudicate disagreements.
3. Generate a report-bound review JSON with `python -m tracker.event_intel_benchmark saved-report.json --prepare-review > review.json`. Use `event_identity` values from the report. Fill missing fields with actual measurements, not zeros. Keep undecided labels null.
4. Set cost/latency thresholds in the reference before running the acceptance evaluation. Set `reference_complete` only after independent coverage review, and label each reference event's `independent_relevance`.
5. Run the offline evaluator:

```sh
python -m tracker.event_intel_benchmark saved-report.json --review reviewed-labels.json
```

The command prints metrics and blockers. Exit code 2 means acceptance is not established. A missing label, partial report, incomplete reference or unmeasured cost must not become a green result.

```json
{
  "report_sha256": "COPY_FROM_GENERATED_PACKET",
  "reviewers": [],
  "event_labels": {},
  "critical_fact_labels": {},
  "critical_facts_review_complete": false,
  "unsupported_personal_claims": null,
  "provider_reconciled_cost_usd": null,
  "measured_latency_seconds": null
}
```

`event_labels` maps each recommended event identity to a reviewer-adjudicated Boolean. `critical_fact_labels` maps every recommended identity to Boolean judgments for `starts_on`, `ends_on`, `city` and `country`. Unknown judgments remain null and block acceptance. `report_sha256` must match the exact captured report; reruns and changed evidence require a new packet and review. Source date/city comparisons are reported only on matched reference cases; those comparisons alone do not establish overall factual quality.

## Mandatory challenge cases

- A 2027 event page with a 2026 sponsor directory must not establish 2027 company presence.
- INBOUND/UNBOUND series aliases must not merge unrelated hosts or invent a 2027 date.
- Sold-out and cancelled editions must not be offered as straightforward new attendance opportunities.
- Directory cases with 100, 300 and 1,000 rows need known source counts; measure entity/role/edition precision and recall, not only whether the parser returns rows.
- Kill a worker during a paid request and after a saved stage; distinguish a cached response from an unknown provider outcome.
- Repeat request keys and decisions across clients/accounts; verify deduplication and isolation on native PostgreSQL.

The current unit/SQL fixtures exercise failure handling. They are not the paid benchmark or independent business-quality evaluation.

## Phase 3 plan review

Capture authenticated plan response objects (the JSON returned when saving a plan) into a local JSON array. Keep client-confidential captures outside the repository. Generate a packet:

```sh
python -m tracker.event_intel_plan_review captured-plans.json > plan-review-packet.json
```

The packet supplies snapshot fingerprints and required checks with null labels. Each reviewer independently checks the cited sources and the client's objectives, then supplies an object in `reviewers`:

```json
{
  "reviewers": [
    {"name": "Reviewer A", "cases": {"EXACT_SNAPSHOT_FINGERPRINT": {
      "action_appropriate": null,
      "access_claims_accurate": null,
      "no_unsupported_personal_claims": null,
      "topic_matches_useful": null,
      "person_company_claims_accurate": null
    }}},
    {"name": "Reviewer B", "cases": {}}
  ]
}
```

Copy the required checks for each case from the packet; topic/person checks are required when those outputs exist. Replace null only with an actual Boolean judgment. Distinct names are a declared review record, not authentication of reviewer identity. Do not invent reviewers or labels. Evaluate using:

```sh
python -m tracker.event_intel_plan_review captured-plans.json --review completed-plan-review.json
```

A changed plan or evidence snapshot requires new labels. Missing/false judgments block the review; disagreements need correction and another review. Passing this bounded case review **never** establishes release acceptance. The live Phase 2 discovery, factual-accuracy, cost and latency benchmark remains mandatory. A calibrated ranking or verified fact catalog also needs its own broader evaluation, beyond this packet.

## Evaluation version 2 safeguards

The evaluator validates nonnegative finite cost/latency measurements and targets; Booleans, strings, NaN and infinity are invalid numeric evidence. Ratio targets must be finite numbers between zero and one. Completion flags must be literal true, reviewer names must be nonempty and distinct after normalization, and unsupported-personal-claim counts must be nonnegative integers. These checks do not authenticate the declared reviewers or independently reconcile invoices.

Critical-fact accuracy is calculated from adjudicated labels across every recommended event, in addition to direct reference comparisons. Missing values cannot agree with other missing values. Recall counts distinct reference editions and requires complete identifying facts for a match; duplicate/ambiguous references and duplicate recommended identities block acceptance. A same-series event in another year is a separate reference case. Existing review files without snapshot hashes and complete fact judgments must be regenerated, not backfilled with invented labels.
