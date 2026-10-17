# Pacing Desk: where its numbers come from

Page: `/p2/paid-marketing/pacing-desk`. The engine (`paid_marketing/pacing_engine`) is unchanged in how it judges an account; what changed is
how it is fed. It used to read two Google Sheets (the "All Accounts Spend Data" sheet that Supermetrics filled, and the Budget Tracker's DPR
tab) with a service account that someone had to share both sheets with. It now reads Google Ads directly, like the other Paid Marketing agents.

## What comes from where

| Input | Source |
|---|---|
| Spend, conversions, conversion value, impressions, clicks, per campaign per day, month to date | Google Ads API (`tracker/paid_pacing_read.py`) |
| Each campaign's real daily budget, whether the budget is a shared pool and its name, status, bidding strategy, campaign type | Google Ads API |
| Search impression share (the headroom signal) | Google Ads API, with the days; asked for again without it if Google refuses it |
| Campaigns that are enabled but did not serve this month | Google Ads API (a second query per account), so their budget still counts towards what the account can spend |
| What each account is meant to spend this month (the allocation), its target CPA, the days its ads run, status, AM | The platform's own table `pm_pacing_budgets` (`tracker/paid_pacing_budgets.py`, `paid_pacing_budget_store.py`), edited in **Allocations** on the page |

Google Ads cannot say what the client agreed to spend, which is why the allocation is the one input kept in the platform.

## Allocations

One row per client and account per month. A month nobody has saved uses, in order: last month's rows carried forward (marked on the page and in
the run's notes until someone saves them), then, for the very first month, the accounts and allocations of the last run the platform stored.
Rows can be pasted from the Budget Tracker (a copy of its cells, not a connection to it): the paste is read for review, only Google rows for the
chosen month are used, and nothing changes until the person chooses how to use them and saves. A save names the version it started from, so two
people editing at once cannot overwrite each other (the second is told who saved in between). Looking never saves.

## A run

1. List the Google Ads accounts (the Performance Summarizer's cached list).
2. Work out the day: the engine treats `as_of` as today and expects data through yesterday. Accounts sit in different time zones, so the newest
   "yesterday" among them sets the day and each account is read through its own yesterday. On the first of a month the run stays on the month that
   just ended.
3. Read every account, 4 at a time, two read-only queries each. An account that cannot be read is named in the run's notes and left out, never turned
   into zeros. More than a quarter unread, or the daily allowance spent, and nothing is published (the page keeps the last good run).
4. Save the read (`pm_gads_cache`). Changing an allocation re-runs the engine on this read (no call to Google, no wait) for as long as it is from the
   same Pacific day and under an hour old.
5. Write the two workbooks the engine's loaders expect into a throwaway folder, run `validate.py` and `export_dashboard.py` in a separate process,
   and store the result exactly as a pushed run is stored. The engine's self-check flags travel with the run.

## When a run starts

* The page starts one by itself when none has been built since the Pacific day began (one read of the accounts per day, not one per visit).
* **Refresh** starts one on demand. Google Ads is not read again within 10 minutes of the last read (a failed run can be retried after a minute), so
  a busy morning cannot spend the developer token's daily allowance.
* A run in progress is shared: a second person sees the same progress, not a second run.

## Configuration

Only the Google Ads variables the other agents use (`GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_DEVELOPER_TOKEN`,
`GOOGLE_ADS_REFRESH_TOKEN`, optionally `GOOGLE_ADS_LOGIN_CUSTOMER_ID`). `PACING_SPEND_SHEET_ID` and `PACING_BUDGET_SHEET_ID` are no longer read and can
be removed from Railway. The service account no longer needs the two sheets shared with it. `POST /api/pacing/seed` (a run pushed from outside)
still works.

## What the engine does not get from Google Ads

* Budgets for other channels (Bing, Meta, MNTN): the Pacing Desk paces Google only; a pasted row for another channel is skipped.
* Amounts are shown as dollars; an account that reports in another currency is noted in the run, not converted.
* The Budget Tracker's other columns (channel list, notes) are not kept.
