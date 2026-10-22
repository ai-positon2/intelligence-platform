# Spend Anomaly Alert plan

Sheet item #30 ("Spend Anomaly Alert", "Hourly", owner Subbu, listed Live on the sheet but not built on this platform; the Daily Monitoring row "Check spend for spikes and anomalies" says Google only,
"Meta not covered yet", and the Live Agent Extensions list says "Extend to Meta accounts"). Purpose on the sheet: identify any sudden spike in account spend, which can also detect a hacked account or
unusual activity, and alert immediately by email and Slack. Section: Paid Marketing Agents (`/p2/paid-marketing/spend-anomaly`). Built here: Krishna's request, 2026-10-22 ("Build the next agent").

## What it is

For a Google Ads account, every account of an MCC or a Meta ad account it works out what the account **usually spends** and holds today so far and yesterday against it. The sheet gives no
limits, so the usual is the account's own. It also watches every account every two hours and alerts the team to what turns up after the first run.

It is not the Brand Spend Alert (brand campaigns against a usual week) or the Pacing Desk (spend against a monthly plan): this is the whole account, by the hour, for what is wrong now.

## What is read (read only)

* **Google Ads, one query per account:** the customer's cost by date and hour for the last 16 days, with the account's time zone (`customer.time_zone`). If Google refuses the hours, the daily form is
  used and the page says only whole days are judged.
* **Meta, per ad account:** insights by day (`level=account`, `time_increment=1`), and by hour with `breakdowns=hourly_stats_aggregated_by_advertiser_time_zone` when Meta gives it.
* **Only for an account that looks wrong** (a medium or high spike or surge): Google: the campaigns' cost by day for 15 days with their start dates, the enabled campaigns' budgets (a shared
  budget once), and the change history of the last 14 days (who changed which budget or campaign, with the old and new budget amounts). Meta: the campaigns' spend by day. Each part is soft: one
  that cannot be read is said and the finding stands without it.

The time is the account's own. "Today" and "this hour" come from its time zone (UTC with a warning when it is unknown). The read is kept in memory for ten minutes; Refresh reads again at most every
two minutes per account. What the team saved is applied on every request, so changing it never reads the account again.

## The arithmetic (tracker/paid_anomaly_math.py)

* **Usual day:** the median of at most 14 days before the day judged, from the day the account first spent; the same kind of day (weekdays with weekdays, weekend days with weekend days) when there are 4.
  At least 4 days are needed, or a usual daily spend saved by the team.
* **Usual by now:** the median of what those days had spent by this hour of the day. Never read below a seventh of a usual day (a day that spends late would make any morning spend look huge).
* **Floor:** a swing smaller than 25 units of the account's currency, or a tenth of a usual day, is never flagged. The team can raise the first.
* **Grade:** medium from 1.75x and high from 2.75x (and two floors over) at the normal sensitivity; low 2.5x and 4x, high 1.4x and 2x. A spend a bit over half way to medium is low (page only).
  Where the usual is nothing, the size counts: medium from two floors, high from six.

| Finding | Level | What it is |
| --- | --- | --- |
| spike | low, medium, high | today so far against the usual by now (from 03:00; before that the whole usual day is the measure), and yesterday against its usual day |
| surge | low, medium, high | the last three whole hours (two at 02:00) against their usual: a spike that is happening now, before the day's total shows it (ratios 2.5x and 4x at normal, scaled by sensitivity) |
| ceiling | high | a day (today or yesterday) that reaches the ceiling the team saved |
| budget | medium or high | more than 2.2x (high from 3x) the daily budgets of the enabled campaigns: Google may spend up to twice a campaign's budget, never more |
| drop | medium or high | from 14:00 spend at or under 15% of the usual by then (the latest two hours left out, the platform reports them late), or yesterday at or under 20% of a usual day; high when nothing at all and the usual is 250 or more |

A planned push can be muted until a date (at most 45 days ahead): its spikes, surges and budget findings become information, never alerted. A ceiling and a drop are never muted.

Cause (for a spike or a surge): the campaigns that spent the excess (one campaign with 60% or more, a campaign started in the last 3 days with a quarter or more, or the spread) and the changes
made lately, with a person who made no change in the 14 days before marked. That mark is withheld when the change history was cut at its limit.

## What the team saves (tracker/paid_anomaly_settings.py, `settings:anomaly`)

Per account: the usual daily spend, a ceiling, the smallest swing worth a word, a planned push (until a date) and a note. For everyone: the sensitivity. Each change says who made it and when.

## The monitor (tracker/paid_anomaly_monitor.py, `paid_monitors.py`)

One issue for each medium or high finding, with the fingerprint account:kind:day, so a spike that goes on all day is one issue, alerted once, and alerted again only when it gets worse. Low and
muted findings are on the page only. An account that cannot be read is named on the run and resolves nothing; a spent Google quota still lets Meta accounts through. The first run is a baseline and
sends nothing (so a spike already on when the monitor is first run is not alerted). It runs **every two hours** from its own workflow (`.github/workflows/paid-spend-anomaly.yml`, which posts
`{"agents": ["spend"]}`); the daily run leaves it alone ("frequent" in `paid_monitors.AGENTS`), and the Monday summary covers it. Twelve runs a day is twelve reads of every account: if Google's
shared read allowance runs short, make the cron less frequent rather than switching the alert off.

## What is not built or not verified

* None of the new Google queries (the customer by date and hour, the campaigns by day with start dates, the budgets, the change history with old and new resources) or the Meta hourly breakdown
  with `time_increment` has been run against the live APIs from here. Each fails soft: an hour read that Google or Meta refuses falls back to whole days, an extra read that fails is said.
* An account that was spending nothing and has never spent has no usual; one that has spent only in the last days is "too new to judge" unless the team saves a usual.
* Meta has no budget finding and no change history (a spike is traced to campaigns only), and no campaign start dates.
* The ceiling and the usual spend are per account; there is no per-client config (sheet item #37, Client Master Config) to hold them.
* No hack detection beyond naming a person who changed a budget or campaign and had made no change before.
* Nothing is deployed: the last deployed commit is older than this work (Railway outage). Needs `MONITOR_ALERT_SECRET` on Railway and as a GitHub Actions secret, and recipients on the page's Alerts panel.
