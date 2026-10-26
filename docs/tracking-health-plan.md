# Tracking & Data Health plan

Sheet item #33 ("Tracking & Data Health", Monitoring, "New - not built", owner Krishna, Google + Meta). The sheet says: "Catches conversions dropping to zero, broken tags, pixels and CAPI, GA4 and CRM
link breaks, shrinking audience lists and stale dashboards." Section: Paid Marketing Agents (`/p2/paid-marketing/tracking-health`). Built 2026-10-26 at Krishna's request ("Start working on the
remaining 6 agents one by one": #33, #34, #36, #37, #38, #39; #35 was already live).

## What it is

For a Google Ads account, every account of an MCC or a Meta ad account it reads what shows whether the account's measurement still works, and holds the **last whole days** against the **21 days
before them** (days 28 to 8 before today). A finding is a thing a person can open and fix. It does not judge performance (KPI Monitoring does that): it judges whether the numbers can be trusted.

## What it can and cannot see

The platforms do not show whether a tag is on the website, so the agent watches for what a broken tag does: conversions that stop. What it cannot see is written on the page (`NOT_MEASURED` in
`tracker/paid_track_math.py`), never left out silently: the tag on the site, Google Analytics 4 itself and what a CRM holds (only what reaches Google Ads or Meta is seen), Meta's Conversions API and
the pixel's event counts (the Meta token lacks pixel stats permission) and dashboards.

## What is read

| Platform | Read | Used for |
|---|---|---|
| Google | the account's cost and conversions by day (`customer`, 30 days, in the account's time zone) | conversions stopped or dropped, spend continuing |
| Google | `conversion_action` (status, type) and conversions by day segmented by `segments.conversion_action` | an action that stopped recording |
| Google | `user_list` sizes (the larger of search and display) | shrinking lists |
| Google | `customer.conversion_tracking_setting.accepted_customer_data_terms` | customer data terms |
| Google | `offline_conversion_upload_client_summary` (status, last upload) | offline conversions (a CRM link) |
| Meta | campaign insights by day, summed per day (the platform's reader) | conversions stopped or dropped |
| Meta | `/{act}/adspixels` (`last_fired_time`, `is_unavailable`) | a silent or unavailable pixel |
| Meta | `/{act}/customaudiences` (`approximate_count_lower_bound`) | shrinking audiences |

The days are the point (a failure there is raised); every other read is soft (a refusal leaves that check out and says so). The new field names are checked by the admin live query check
(`/p2/paid-marketing/api/query-check?...&agents=track`), which sends each one to a real account.

## Rules (`tracker/paid_track_math.py`)

| Rule | Level | Meaning |
|---|---|---|
| `no_conv` | high | spend in the window and no conversion, over 3 days (a fast account) or 7 days (a slower one), when the usual rate promised at least 5 in that window |
| `conv_drop` | medium | the last 7 days at or under a fifth of a usual week (the usual week at least 10 conversions) with the spend at half its usual or more |
| `action_stopped` | high or medium | a conversion action with at least 5 a week usually recorded none in 7 days: high when it carried half of the account's conversions; not listed when the whole account stopped |
| `list_lost` | high | a list is empty, or (Google) fell under the 1,000 people Search needs |
| `list_shrunk` | medium | a list lost 30 percent against the largest reading of the last 14 days (the monitor's own daily snapshots), from 500 people or more |
| `upload_stale` | medium | an enabled offline conversion action and no upload for 14 days, or Google's log says no recent upload or needs attention (no log row at all is "not judged": a link that never worked cannot be told from an action that was never used) |
| `pixel_unavailable` | high | Meta marks the pixel unavailable |
| `pixel_stale` | medium (high with conversions stopped for 3 days; low while the account still records conversions, because the pixel is then probably one it no longer uses) | the pixel has not fired for 48 hours (or never) while the account spent in the last 7 days |
| `terms` | low (medium when the account has a customer list) | customer data terms not accepted |

Enough data first: an account whose usual rate promises under 5 conversions in a window is listed as **not judged** with the number it had, never called fine. Nothing spent means nothing to judge.
A list is judged from the second day the monitor has seen it.

## The monitor

Daily on the shared monitor layer (`tracker/paid_track_monitor.py`, agent `track`): one issue per high or medium finding (fingerprint `track:<account>:<key>`), the low ones in one quiet issue per
account, at most 100 per account. Each run saves the lists' sizes per account per day. The first run of the monitor, and the first read of any account, is a quiet baseline. Alerts go to the Slack
channel set for the monitors, once a day with the others.

## Not built, on purpose

Team-saved settings (thresholds are fixed and written on the page), a tag-on-site scan (fetching the account's final URLs and looking for the conversion tag), GA4 property checks and Meta CAPI health.
Each needs access or a design this agent does not have yet.

## Found by the first live read (2026-10-26)

On Beta Bionics the first version flagged an upload action that had never received an upload and a pixel that last fired 133 days ago while the account recorded 703 conversions a week. Neither is
a broken tag, so the first is now "not judged" and the second is low. Every new Google query and Meta request was accepted by the real APIs (admin live query check, `agents=track`).
