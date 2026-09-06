# Position2 event benchmark

`position2.json` is a dated, organizer-sourced starter set. The user selected Position2; its offer, segment, geography and planning window are clearly marked evaluation assumptions. The file is not a complete event catalog or an approved attendance plan.

## Run and review

1. Use the profile in the JSON after resolving any business assumptions. Submit a run through staging with matching web/worker code and configured provider credentials. Export the authenticated run-detail JSON to a local file.
2. Independently expand the reference set before judging discovery recall. Have two reviewers label event relevance and critical facts without using the agent's score as the answer key. Adjudicate disagreements.
3. Copy the template below to a review JSON. Use `event_identity` values from the report. Fill missing fields with actual measurements, not zeros. Keep undecided labels null.
4. Set cost/latency thresholds in the reference before running the acceptance evaluation. Set `reference_complete` only after independent coverage review, and label each reference event's `independent_relevance`.
5. Run the offline evaluator:

```sh
python -m tracker.event_intel_benchmark saved-report.json --review reviewed-labels.json
```

The command prints metrics and blockers. Exit code 2 means acceptance is not established. A missing label, partial report, incomplete reference or unmeasured cost must not become a green result.

```json
{
  "reviewers": [],
  "event_labels": {},
  "critical_facts_review_complete": false,
  "unsupported_personal_claims": null,
  "provider_reconciled_cost_usd": null,
  "measured_latency_seconds": null
}
```

`event_labels` maps each recommended event identity to a reviewer-adjudicated Boolean. Source date/city comparisons are reported only on matched reference cases; those comparisons alone do not establish overall factual quality.

## Mandatory challenge cases

- A 2027 event page with a 2026 sponsor directory must not establish 2027 company presence.
- INBOUND/UNBOUND series aliases must not merge unrelated hosts or invent a 2027 date.
- Sold-out and cancelled editions must not be offered as straightforward new attendance opportunities.
- Directory cases with 100, 300 and 1,000 rows need known source counts; measure entity/role/edition precision and recall, not only whether the parser returns rows.
- Kill a worker during a paid request and after a saved stage; distinguish a cached response from an unknown provider outcome.
- Repeat request keys and decisions across clients/accounts; verify deduplication and isolation on native PostgreSQL.

The current unit/SQL fixtures exercise failure handling. They are not the paid benchmark or independent business-quality evaluation.
