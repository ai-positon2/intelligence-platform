# Creative & Asset Health plan

Sheet item #36 ("Creative & Asset Health", Monitoring, "New - not built", Google + Meta). The sheet says: "Spots tired creatives (falling CTR, rising frequency and CPM, low asset ratings) and
missing assets such as sitelinks, callouts and images." Section: Paid Marketing Agents (`/p2/paid-marketing/creative-health`). Built 2026-10-27 at Krishna's request, the third of "the remaining 6".

## What it is

For a Google Ads account, every account of an MCC or a Meta ad account it reads whether the creatives are wearing out and whether the campaigns hold the assets they need. It is not a second Ad
Strength or Asset Coverage page: Ad Strength rates how an ad is built today and Asset Coverage counts what a campaign holds against the rules; this watches how the creatives are doing over time
and alerts on the change. The missing assets are Asset Coverage's own rules and read (one saved read, so the two pages agree and opening one costs the other nothing); on Meta they are the ad
sets that hold too few ads.

## What it can and cannot see

Written on the page (`NOT_MEASURED` in `tracker/paid_creative_math.py`): how old a creative is (the API does not carry it, so a creative is judged on its last weeks, not its age); frequency on
Google (not reported for Search or Performance Max, so Google ads are judged on click-through rate and Google's own ratings); Meta frequency over an ad's whole life (it is for the week); Performance
Max assets' own weekly numbers (the asset group is judged, and Google's Low ratings name the weak assets inside it); and Meta ads not ranked yet (500 impressions).

## What is read

| Platform | Read | Used for |
|---|---|---|
| Google | `ad_group_ad` by `segments.week` over the last 5 whole Monday to Sunday weeks (impressions, clicks, cost), live ads only | click-through rate by week |
| Google | `asset_group` by `segments.week`, live Performance Max asset groups | the same, for Performance Max |
| Google | the first headline of each responsive search ad | naming an ad so two in one ad group read differently |
| Google | `ad_group_ad_asset_view.performance_label = LOW` and `asset_group_asset.performance_label = LOW` | the assets Google rates Low |
| Google | Asset Coverage's report (`tracker/paid_assets.py report_for`) | missing assets, with its severities |
| Meta | the live ads and Meta's three rankings, the read Ad Strength and Asset Coverage share (`tracker/paid_meta_creative.py raw_for`) | which ads are live, the rankings |
| Meta | `/{act}/insights` at ad level, `time_increment=7`, the last 5 seven-day buckets (spend, impressions, reach, link clicks) | click-through rate, frequency, cost per 1,000 by week |
| Meta | Meta's ad set coverage view | ad sets with a single live ad |

The weekly numbers of the ads are the point (a failure there is raised); every other part is soft: it leaves its check out, says so, and is named in the account's `unread`, which the monitor hands
to the engine as a hold so a problem of that kind is not marked fixed by a read that could not see it. The new field names are checked by the admin live query check
(`/p2/paid-marketing/api/query-check?...&agents=creative`).

## Rules (`tracker/paid_creative_math.py`)

The last 2 weeks are held against the 3 before. A creative is judged only with 1,000 impressions in the last weeks and 2,000 before (and 30 clicks before, for the click-through rate); one with less
is counted as not judged and the page says how many.

| Rule | Level | Meaning |
|---|---|---|
| `tired` | high for two signals or a click-through rate down 40 percent, else medium | one finding per creative. Signals: click-through rate down 25 percent, unlikely to be chance (two-proportion test, 1.96) and clearly more than the rest of the account fell (a fall every ad shares is the season or the auction, not the creative; a creative is held against the account without itself); Meta: a frequency of 3 or more in the last week that rose 20 percent (4 or more needs no rise); a cost per 1,000 up 25 percent and 15 percent more than the account's |
| `low_rating` | Google: medium when the ad is seen (2,000 impressions), else low; Meta: medium for two below-average rankings, high for three or two in the bottom 10 percent | one finding per ad or asset group naming each Low asset (Google); a Meta ad ranked below average on quality, engagement and conversion |
| `missing_asset` | the coverage rules' own (high, medium or low) | one finding per campaign (or asset group, or ad set) and kind of asset |

## The monitor

Daily on the shared monitor layer (`tracker/paid_creative_monitor.py`, agent `creative`): one issue per high or medium finding, the low ones in one quiet issue, at most 100 per account. The first
run, and the first read of any account, is a quiet baseline. Alerts go to the Slack channel set for the monitors, once a day with the others.

## Engine change made for it

`run_monitor` now takes an optional `hold` from a monitor (`{account id: {issue kinds}}`): an open issue of a held kind is left open instead of resolved. Account Watch and Tracking & Data Health have
soft parts too and do not use it yet.

## Not built, on purpose

Creative age and fatigue by age, Google frequency for Display and Demand Gen, per-asset weekly numbers for Performance Max, Meta creative-level breakdowns by placement, and a model that suggests the
replacement copy (the Ad Copy agent writes it).
