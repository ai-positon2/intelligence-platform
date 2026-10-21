# Brand Spend Alert plan

Sheet item #26 (roadmap: "Monitors spend on brand keywords and alerts the team when it moves outside expected limits"). Section: Paid Marketing Agents
(`/p2/paid-marketing/brand-spend`). Owner on the sheet: Krishna. Built here: Krishna's request, 2026-10-21 ("start building the next agent from this list").

## What it is

For a Google Ads account, or every account of an MCC, it reads what the **brand campaigns spent**, day by day, and says for each account whether last week's
spend was inside what is normal for that account, and if not, whether the money went on **more clicks or dearer clicks**, which campaign moved it, and what to do.
Brand spend that doubles is a competitor or a reseller on the brand, a budget someone raised, or clicks that are not people; brand spend that falls to nothing is
a paused campaign, a refused ad or a payment problem. Both cost the client, and neither is visible in a monthly report.

## What is read (read only)

One Google Ads read per account: for every Search campaign that is not removed, every day of the last 8 whole weeks (Monday to Sunday) and of this week up to
yesterday, the impressions, clicks and cost, with the campaign's status, bidding strategy and daily budget. Nothing is changed in Google Ads.

## Which campaigns are brand campaigns

The same rule as the Brand IS Monitor (`paid_brandis_math.is_brand`): the name holds brand or branded and does not say non-brand, nonbrand, NB, generic,
competitor or conquest; the person can count or not count any campaign. Brand keywords that sit inside campaigns that are not brand campaigns are not counted
(the read is by campaign; the person can count such a campaign as brand). Paused campaigns are read too, so a campaign that was switched off shows as spend that stopped.

## What "outside expected limits" means

Nothing on the sheet gives the limits, so they come from the account's own history, and the page says so:

- **Expected week** = the median weekly spend of the up to 7 whole weeks before the last one, counting only weeks with spend, and only when there are at least 4 of them.
- **Limit** = 30% either way by default (10 to 90%, changeable per read). A week more than 30% above is over, more than 30% below is under; twice the limit is high.
- A swing smaller than 25 in the account's currency is not flagged: it is noise on a small brand.
- Last week had no spend although earlier weeks did: **stopped**, always high.
- Fewer than 4 weeks of history: **new**, said as it is, never as a pass.
- **This week so far** (from Monday to yesterday, at least 2 days) is held to the same band, pro rata, so a spike shows before the week ends. It is a lower level because a
  part week is a rougher measure.

## Why it moved

Spend is clicks times cost per click. The last week's clicks and cost per click are held against the usual week's (medians): the cause is named clicks when 60% or more of
the move (in logs, so the two multiply fairly) is clicks, cost per click when 60% or more is that, otherwise both. Each brand campaign's spend is held against its own
usual week, and the biggest movers are named.

## The fix plan

Chosen from the cause: for a rise that is clicks, the search terms and the invalid clicks; for a rise that is cost per click, Auction Insights and the bidding setting; both
get the change history; a ceiling to put the spend back where it belongs. For a fall: whether the campaigns are running (status, ads, budget, billing), and whether the
brand is shown (the Brand IS Monitor). The worth is the arithmetic of the figures (the extra spent in a week against the usual week, the clicks missing), labelled an
estimate where it assumes anything. A rise is not always a fault: a launch, an event or press brings more people searching for the brand, and the plan says to check that first.

## Built after the first version (2026-10-21)

- **Alerts, the daily run, new since yesterday**: the shared monitor layer (`tracker/paid_monitor*.py`, tables `pm_mon_state`, `pm_mon_issues`, `pm_mon_snaps`). Each finding has a stable
  fingerprint; events are new, worse, reminder (a high issue still open after 7 days) and resolved. The first run of a monitor sets a baseline and sends nothing. Only medium and high
  are alerted; an alert counts as sent only when a channel took it. A weekly summary goes out on Mondays. A GitHub Actions workflow (`paid-monitors.yml`, daily) calls the
  secret-guarded `/p2/paid-marketing/api/monitors/scheduled`.
- **Fixed limits**: saved by the team per account (weekly minimum and maximum, a monthly budget), with a change log, in `settings:brandspend`. A breach is a high finding.
- **Brand words in non-brand campaigns (leak)**: from search terms of the last 30 days for accounts with saved brand words (the page suggests words from the campaign names).
- **Campaigns that never spent**: enabled brand campaigns with no spend in 8 weeks, from a separate campaign list read.
- **Insight**: conversions and cost per conversion against usual, impression share and what it lost to (rank or budget), month pace against the budget, days far above the usual with
  whether their clicks converted, the days in a row outside the band.

## Still not built (said out loud)

- **Performance Max leak**: `search_term_view` excludes Performance Max, so brand searches paid for there are not seen.
- **Meta**: the agent is Google Ads only.
- **Live verification**: none of the new queries (conversions and share fields, the campaign list, search_term_view) has been run against the real Google API from here.

## Phases

1. Plan and the pure logic (`tracker/paid_brandspend_math.py`). Tested and mutation-tested.
2. The fix plan (`tracker/paid_brandspend_plan.py`).
3. The read and the report (`tracker/paid_brandspend.py`), cached like the other Google agents.
4. Routes, the page, CSV.
5. Hub card, palette, counts, the assistant's text.
