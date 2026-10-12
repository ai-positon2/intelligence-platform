# Budget Optimization Agent

Daily campaign-level budget reallocation recommendations, per client, posted to Slack.

## What it does
For every active sub-account, it answers one question: **will this account land the
month at 100% of budget, and if not, what moves fix it?**

- Pacing vs the ideal `(today-1)/days_in_month` rule, with a +2pp tolerance.
- A **landing projection** — spend-to-date plus the trailing run-rate across the
  remaining *active* days. This, not today's pacing %, drives the priority order,
  because being 5pp behind on day 7 is meaningless if the run-rate still lands the month.
- Campaign-to-campaign moves **inside a sub-account only**. Every account's
  moves net to zero. Cross-account gaps are flagged, never auto-moved.

## Ranking (all four signals, weights in `config.py`)
| Signal | Weight | What it measures |
|---|---|---|
| Efficiency | 40% | CPA vs the account's Target CPA, clipped 0.25–2.5x |
| Volume | 25% | Share of conversions produced |
| Absorption | 25% | Can it actually spend more? Utilisation + steadiness of daily spend. A campaign wandering below its cap is demand-limited — extra budget won't be spent. |
| Type | 10% | Strategic preference by campaign type |

## Guardrails
- Max 30% donated / 50% received per campaign per run — **measured against the
  campaign's pro-rata share of the new account total**, so an account-wide budget
  change is never mistaken for a reshuffle.
- Pmax / Demand Gen / Video capped at +30% (learning-phase protection).
- Brand floor 5%, Brand ceiling 35%, Pmax ceiling 55% of account daily.
- Moves below $5/day or 10% of the campaign are suppressed as noise.
- Dark campaigns (zero spend) receive nothing — they get flagged instead.
- When guardrails make the required change unreachable in one step, the shortfall
  is reported explicitly (`constrained_by`) rather than silently breached.

## Files
| File | Purpose |
|---|---|
| `config.py` | Every tunable. Tolerances, weights, caps, floors, Slack channel. |
| `pacing.py` | Calendar / schedule / base-plan ideal pacing. Parses "Monday - Friday". |
| `engine.py` | Scoring, water-fill allocation, move construction. |
| `loaders.py` | Drive xlsx -> normalized frames. One adapter per sheet layout. |
| `slack_fmt.py` | One post per client. |
| `run.py` | Orchestrator. `python3 run.py --as-of 2026-09-07 --client Riccobene` |
| `validate.py` | 7 self-checks. Run after every build. |

## Primary input: the Google Ads campaign export
`gads.py` reads the Google Ads UI export ("Campaign Spends.xlsx"). It is strictly
better than the Supermetrics `Data` tab because it carries:

| Column | Why it matters |
|---|---|
| `Budget` | The campaign's REAL daily budget. Move amounts stop being inferred. |
| `Budget name` | Identifies SHARED budget pools. Counted once, never double-summed, and two campaigns on one pool are never told to trade budget. |
| `Campaign status` | Distinguishes paused from enabled-but-dark. |
| `Search impr. share` | Auction headroom, feeding the absorption score. |
| `Bid strategy type` | Learning-phase guardrails. |

Required columns: `Day, Campaign status, Campaign, Budget, Budget name, Account,
Campaign type, Impr., Search impr. share, Cost, Conversions, Clicks`.

### Forecast vs lever — the distinction that matters
- **Forecast** uses ACTUAL spend per active day. Never the budget cap: most
  campaigns never spend their full cap, so projecting off budget wildly overstates.
- **Lever** is the set daily budget. To raise SPEND by X you must raise BUDGET by
  X/utilisation — and where utilisation is already low, more budget does nothing.
  That is a demand/bid problem, and the agent says so instead of recommending a raise.

### Three things the agent detects that a DPR cannot
1. **Budget-capped accounts** — set daily budgets that mathematically cannot reach
   the monthly allocation. No reallocation fixes it; the caps have to rise.
2. **Daily figures in the monthly budget column** — where the tracker's allocation
   ≈ the sum of daily budgets and MTD spend has already passed it. Pacing is
   suppressed for those accounts; the budget-neutral campaign moves still stand.
3. **Mid-month budget changes** — spend running far above the current cap means
   the cap was edited and the export shows the new value against old spend.

## Legacy: Supermetrics sheet extraction
Do **not** use Drive's `read_file_content` on these sheets: it flattens to markdown
and truncates (Riccobene's `Data` tab returned 248 of 3,540 rows).

Use `download_file_content` with
`exportMimeType=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.
That returns the complete workbook, every tab, base64. Decode with
`loaders.save_drive_payload()`.

## Adding a client
1. Download its sheet to `raw/<name>.xlsx`.
2. Add a line to `CLIENTS` in `run.py` naming the file and the sub-account column.
3. Ensure its accounts exist in the Budget Tracker with matching names.

Clients whose sheets use the standard Supermetrics `Data` tab layout need no new code.

## Validation
```
python3 run.py --as-of YYYY-MM-DD
python3 validate.py YYYY-MM-DD
```
Checks: moves net to zero, no cross-account moves, recommended dailies sum to the
required daily (or a declared constraint), guardrails respected, no sub-floor moves,
campaign spend reconciles to account spend, account totals reconcile to client totals.
Verified against Riccobene's own September DPR: $3,288.96 across 10 locations, exact match.
