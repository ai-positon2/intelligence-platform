# KPI Monitoring plan

Sheet item #32 ("KPI Monitoring", Monitoring, "Not built", P1 suggested (raised from P4 because it is the only monitor tied to client goals), cadence Daily, owner TBD; Google + Meta). The sheet says:
"Tracks CPA, ROAS, CPL, CVR and CTR against each client's targets and flags campaigns that drift, are stuck in Learning, limited by budget, or not delivering." It absorbs "KPI Monitoring; Bidding
Status; Learning Phase & Delivery (Meta)". Section: Paid Marketing Agents (`/p2/paid-marketing/kpi-monitoring`). Built here at Krishna's request, 2026-10-22 ("Start working on the next agent").

## What it is

For a Google Ads account, every account of an MCC or a Meta ad account, it reads each live campaign's last 14 days (spend, clicks, impressions, conversions, conversion value) and its delivery
status, and holds the **last 7 whole days** against the campaign's target and against the **7 days before**. A finding is a campaign that is off its target, has stopped converting, is not
delivering, is stuck learning, or is held back by its budget or its bid strategy.

It is not the Performance Summarizer (what happened) or the Spend Anomaly Alert (spend against its usual): this is performance against **what the client asked for**.

## Where the targets come from

The Client Master Config (sheet #37) is not built, so the targets are what the team saves on the page, per account (and, where one campaign has its own, per campaign). In the order used:

1. a target saved for the campaign;
2. a target saved for the account;
3. for CPA and ROAS on Google, the campaign's own bid strategy target (Target CPA, Target ROAS, or a Maximize strategy's optional target): the client's goal as the account already states it.

Without any target a campaign is still checked for delivery, learning, the budget and its bid strategy, and for having stopped converting; the page says which checks it could not run.
CPL is the CPA target: a lead account's cost per conversion is its cost per lead. CVR and CTR are saved as the lowest wanted (a percent).

## What is read (Google Ads)

| Read | Field names | Why |
|---|---|---|
| Campaigns by day | `campaign.id`, `campaign.name`, `segments.date`, `metrics.impressions`, `clicks`, `cost_micros`, `conversions`, `conversions_value` for enabled campaigns, last 14 days | the two weeks |
| Status and bidding | `campaign.primary_status`, `primary_status_reasons`, `bidding_strategy_system_status`, `bidding_strategy_type`, `target_cpa.target_cpa_micros`, `maximize_conversions.target_cpa_micros`, `target_roas.target_roas`, `maximize_conversion_value.target_roas`, `start_date`, `advertising_channel_type` | learning, delivery, the strategy's own target |
| Budget lost | `metrics.search_budget_lost_impression_share` for `LAST_7_DAYS` (soft) | how much the budget costs |

The days and the campaigns are the point (a failure there is raised); the status and the budget-lost reads are soft (a refusal leaves those checks out and says so). Meta: the campaign
insights by day (the platform's existing reader), and the ad sets' `effective_status` and `learning_stage_info` mapped onto Google's words (learning, limited by data, not eligible), soft.

## Rules

| Rule | Level | Meaning |
|---|---|---|
| `not_delivering` | high, medium or low | Google says the campaign is Not eligible, Misconfigured or Ended (high); no impressions in 7 days though it is enabled and started (medium); it has not started (low) |
| `bidding` | high or medium | the bid strategy is misconfigured (high), or held back by a bid ceiling or floor (medium) |
| `no_conv` | high | no conversions in 7 days with at least twice the target CPA spent, or at least 5 conversions the week before and none now while the spend went on |
| `cpa` | high or medium | CPA above the target by more than the tolerance (20 percent unless saved): high when it is 2x the target, or 1.5x and the week before was off too |
| `roas` | high or medium | ROAS below the target by more than the tolerance, same levels |
| `learning` | low or medium | the bid strategy is learning (low; medium after 14 days, counted from the first day the monitor saw it) or limited by data (medium: too few conversions to learn from) |
| `budget` | medium or low | Google marks it limited by budget or it lost at least 20 percent of impressions to the budget: medium when it is on target (room to grow), low when it is not |
| `cvr` | medium or low | conversion rate below the lowest wanted (medium at 60 percent of it or less) |
| `ctr` | medium or low | click-through rate below the lowest wanted (same levels) |

Enough data first: CPA and ROAS need 5 conversions in the 7 days, CVR needs 100 clicks, CTR 1,000 impressions; a campaign without that is listed as "could not judge" with the number it had,
never called on target. Campaigns the team muted (a test, a campaign being replaced) are shown and never alerted.

## What the team saves (`settings:kpi`)

Per account: `targets` (cpa, roas, cvr, ctr), `tolerance` (percent, default 20), `campaigns` (a campaign's own targets, up to 100) and `muted` (campaign ids with why, up to 100). Each change is logged.

## The monitor

Daily on the shared monitor layer: one issue per high or medium finding, fingerprint `kpi:<account>:<rule>:<campaign>`; low findings in one quiet issue per account. The first run is a baseline.
A learning campaign's age is the age of its open issue, so the page and the monitor both know how long it has been learning without the monitor writing anything the page reads. Reads: two
Google queries an account when healthy (the days, the status) and one more for the budget lost: three, in the daily run; if Google's shared read allowance runs short, move it to its own workflow.

## Not built or not verified

* None of the new Google queries (campaign metrics by day, the status and strategy fields, the budget-lost metric) or Meta's `learning_stage_info` was run against the live APIs from here.
* No per-campaign goal beyond the four figures; a campaign's target is not read from the conversion goals or the client's sheet (that is the Client Master Config).
* Meta has no budget-limited check (Meta reports no such field per campaign), and its learning state is Meta's own word mapped onto Google's.
* A campaign with a brand-new target is judged on the last 7 days, not on the days since the target was set.
* Currency is the account's; an MCC with several currencies is shown account by account.
