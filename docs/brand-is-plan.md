# Brand IS Monitor plan

Sheet item #25 (roadmap: "Tracks impression share on brand campaigns and alerts the account manager when it falls below the threshold"; the sheet's
Agents&Usage tab says it ran weekly, on Mondays, and sent an Arena message). Section: Paid Marketing Agents (`/p2/paid-marketing/brand-is`).
Owner on the sheet: Divith. Built here: Krishna's request, 2026-10-20.

## What it is

For a Google Ads account, or every account of an MCC, it reads the **Search impression share of the brand campaigns**, week by week, and says for each
account whether the brand is being shown on enough of the searches for it, why it is not (budget or rank), and what moved since last week.
Brand clicks are the cheapest a client buys, so a brand search nobody is shown for is lost to a competitor or a reseller for nothing.

## What is read (read only, enabled Search campaigns only)

One Google Ads read per account: for every enabled Search campaign and every whole week (Monday to Sunday) of the last 8, the impressions, clicks, cost,
Search impression share, the share lost to budget, the share lost to rank, top and absolute top impression share, the daily budget and the bidding
strategy. Nothing is changed in Google Ads.

## Which campaigns are brand campaigns

By name, because no client config says it yet (the roadmap's Client Master Config, #37, will): a Search campaign is a brand campaign when its name holds the
word "brand" or "branded" and does not say non-brand, nonbrand, NB, generic, competitor or conquest. The page shows every campaign it counted and every
one it did not, with a switch to count or not count any campaign (kept in the browser for now; saved for the team when the alerts arrive).

## The numbers

Google gives a share per campaign, not a count. Several campaigns are put together the only honest way: each campaign's impressions divided by its
share is the number of searches it was **eligible** for, and the combined share is the impressions over the eligible searches. The shares lost to budget and
to rank are weighted by eligible searches the same way. A week with fewer than 100 eligible searches is "too few to judge". Google reports a share under
10% as "below 10%" (0.0999); it is kept as it is and said so. A campaign with impressions and no share is counted as not measured, not as zero.

## What it says

- **Below the threshold** (default 90%, changeable per read, 50 to 99%): medium; 10 points or more under: high.
- **Why**: 60% or more of the lost share is budget: the budget is the problem; 60% or more is rank: bids, Quality Score, a competitor; otherwise both.
- **Dropped**: 5 points or more under last week's share, even when still above the threshold.
- **A brand campaign under the threshold** by itself (with 100 or more eligible searches).
- **No brand campaign found**, **too few searches**, **no share reported**: said as they are, never as a pass.
Every finding carries the figures and one next step. A next step is a suggestion for a person; the agent changes nothing.

## The fix plan (added 2026-10-20)

An account under the threshold gets an ordered plan (`tracker/paid_brandis_plan.py`). Each step has why it applies (the figures behind it), how to do it in Google Ads and
what it is worth. Chosen and sized from what was read: the loss to budget (the extra spend a day to win it back, from last week's spend at its cost per impression, and the
budget against the spend), the loss to rank by the bidding strategy in use (target impression share, smart bidding with or without a target, maximize clicks, manual CPC),
Quality Score, Auction Insights, what changed when the share fell 5 points or more, and the one brand campaign holding the share down. The order is: what changed (when
the fall is 15 points or more), budget, bids, the lagging campaign, quality, competition.

**Check the settings** (`tracker/paid_brandis_diag.py`, `/api/brandis/diagnose`, one account at a time): five small read only reads for the brand campaigns (Google's status
and its reasons, the bidding target, location and bid limit, the budget and whether it is shared, the keywords' Quality Score and its three parts, the ads' strength and
approval) and the plan is rebuilt from them: the real target and bid limit against last week's CPC, the named weak keywords and ads, Google's own status as the first step,
and a step that is known not to apply is said not to apply.

## Not in this version (said out loud)

- **Competitors bidding on the brand terms**: Auction Insights is not available through the Google Ads API. The finding points to it in the Google Ads screen.
- **Alerts** (email, Slack, the weekly Monday run): a later slice, on the same pattern as the Unknown Domains Alert (a secret-guarded scheduled address, a baseline,
  alerted once). It needs the alert recipients and a place to save settings for the team.
- **Meta**: Meta has no impression share; the agent is Google Ads only.
- **Shopping and Performance Max brand terms** are not separated out by Google; only Search campaigns have a brand share to read.

## Phases

1. Plan and the pure logic (`tracker/paid_brandis_math.py`): weeks, brand rule, combining, verdicts, findings. Tested and mutation-tested.
2. The read and the report (`tracker/paid_brandis.py`), cached like the other Google agents.
3. Routes, the page, CSV.
4. Hub card, palette, counts, the assistant's text.
5. Checked read-only on production.
6. The scheduled run and alerts (needs the user's go-ahead on recipients and the secret).
