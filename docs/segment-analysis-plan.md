# Segment Analysis agent: plan

Branch: `Divith`. Section: Paid Marketing Agents (`/p2/paid-marketing/segment-analysis`).
Status: **Phase 0 done. Decided: Google Ads API as the data source; rules for
every insight plus a Claude-written summary built only from the rule output.
Phases 1 to 4 built; Phase 5 (review, push) waiting on a go-ahead.**

## What it is

Pick an account and a date range; get the same segment report the PPC team
builds by hand today (reference: "McHale- Segment Analysis-June 2026"), as a
dashboard, plus actionable insights for the account.

## The reference, read from the sheet

Seven segment tables, every one with the same columns:

`Segment | Impressions | Clicks | Cost | % Cost | CTR | CPC | Conversions | CPL`

| Segment | Source dimension (Google Ads) | Notes from the sheet |
|---|---|---|
| Day of the Week | segments.day_of_week | 7 rows |
| Hour of the day | segments.hour | Search only reports a real hour pattern; one row per hour |
| Age | age_range_view | Includes an "Undetermined" row and a blank row of zeros |
| Gender | gender_view | Female / Male / Undetermined + Grand Total |
| Location / City | geographic / user_location_view | ~50 cities; long tail |
| Device | segments.device | Computers / Mobile / TV |
| Network | segments.ad_network_type | Search, Cross-network, Gmail, Discover |

Each table also has a second block split by **Campaign Type** (Brand, Non Brand,
Performance Max, Demand Gen), mapped from campaign name (the "Campaign Type
Look Up" tab). A `Remarks` tab holds one line per segment ("Thursdays are the
best performing", "55+ best considering spend and conversions"), written by
hand today. A QC checklist confirms CTR, CPC and CPL are calculated, remarks
are right, spelling is checked.

Metric rules to copy exactly: CTR = clicks / impressions, CPC = cost / clicks,
CPL = cost / conversions, `Na` when conversions are 0, % Cost = row cost / total
cost.

Gotcha the report has to handle: age, gender and city are not reported for every
campaign type (Pmax reports no age or gender), so segment totals differ across
tabs: in the reference the Gender total is well below the Device total. The
dashboard must show each segment's coverage ("covers 69% of spend") so nobody
compares totals that are not comparable.

This repository is public: no client spend figures go in docs, fixtures or
tests. Tests use synthetic data; the reference sheet is only used locally to
cross-check the engine.

## Filters (two, as asked)

1. **Account**: searchable picker (same `PMPicker` as Pacing Desk), grouped by client.
2. **Date range**: presets (last 7 / 30 days, this month, last month) plus a custom range.

Campaign type is a toggle on the output (All / Brand / Non Brand / Pmax /
Demand Gen), not a third filter.

## Insights (the actionable part)

Computed, not guessed: every insight is a rule over the segment tables with
the numbers shown next to it, and a minimum-data floor (no "best hour" from 2
clicks). Planned rules:

- **Best / worst segment by CPL** per dimension, only among segments with enough spend.
- **Spend with no conversions**: segments over X% of spend and zero conversions
  (e.g. weekend days that spend with no leads) -> suggest ad schedule or bid adjustment.
- **Over-indexing**: segment with a share of conversions well above its share of spend -> raise bid adjustment.
- **Under-indexing**: share of spend well above share of conversions -> lower bid / exclude.
- **Hour windows**: group adjacent hours into a window (e.g. 7-9am) for a scheduling recommendation; flag spend outside business hours for accounts whose campaigns follow office timings.
- **Device mismatch**: a device with high CTR but worse CPL than the other (and vice versa).
- **Geographic long tail**: cities that burn spend with no conversions, and the best converting cities to isolate into their own campaign.
- **Network leakage**: spend on networks that do not convert (Gmail/Discover/cross-network).
- **Data caveats**: low-volume warning, unknown-demographic share, coverage gaps.

Each insight = headline, evidence (numbers), recommended action, and a
confidence tag (high / directional) based on volume. Optional second layer
(OPEN): a Claude-written summary of the rule output for the account manager.

## Data source (OPEN, the main decision)

The filters only work if the platform can query by account and date range.

- **A. Google Ads API from the platform** (recommended): GAQL query per segment, live,
  any account under the Position2 MCC, any date range. Needs the developer token,
  OAuth client id/secret and refresh token set as Railway env vars (new, platform has
  no Google Ads connection today). Project is on Explorer access (2,880 ops/day);
  a view is about 8 queries, so fine at team scale. Uses the existing MCC
  (9923720096) and account list.
- **B. Supermetrics -> Google Sheet, read by the service account** (matches how Pacing
  Desk reads spend today): no new credentials, but the sheet has to carry all accounts
  x 7 dimensions x daily rows, and age / gender / city are pulled undated in the
  reference, so a date-range filter could not apply to them.
- **C. Upload the exports** (like Search Query Analysis): no credentials, but then
  "account" and "date range" are just labels, not real filters.

## Phases

1. **Phase 0 (this doc)**: reference analysed, decisions agreed.
2. **Phase 1, the engine**: `tracker/paid_segments.py`: fetch (per chosen source),
   campaign-type mapping, metric math, per-segment tables, coverage. Pure functions,
   unit tested on synthetic data (cross-checked against the reference sheet locally).
3. **Phase 2, the insights**: `tracker/paid_segment_insights.py`: the rules above, tested.
4. **Phase 3, the page**: route + `segment_analysis.html` + `static/js/segment_analysis.js`,
   account and date filters, seven tables with the same columns as the sheet, campaign
   type toggle, insights panel on top, CSV/xlsx export in the sheet's layout.
5. **Phase 4, wiring**: hub card on `/p2/paid-marketing`, counts in `hub.html` and
   `test_hub_card_counts.py`, command palettes, Vimi context block in `app.py`, CSS
   accent, usage tracking.
6. **Phase 5, verify**: run the dev server, check the page against the McHale June
   reference, full paid + hub test pass, then review before any push.

## Phase 1 result (done)

`tracker/paid_gads.py` (read-only Google Ads REST: OAuth refresh, `googleAds:search`
with page tokens, account list under the MCC, place-name lookup) and
`tracker/paid_segments.py` (queries, campaign-type mapping, the sheet's metric
rules, per-campaign-type views, coverage, 15-minute cache). 37 tests, synthetic data.

Cross-checked live against the McHale June 2026 reference (local only):

- Gender and age: impressions, clicks, conversions and cost match the reference
  to the cent.
- Day, hour, device, network: the reference tables are *lower* than Google Ads.
  Cause: roughly 60% of the reference `Data` tab's Performance Max rows have a blank
  Campaign Type (the hand-kept lookup missed them), so a large share of Pmax spend
  never reached those tabs. The agent classifies by Google Ads' own channel type, so
  it cannot drop spend that way. Conclusions the reference drew (Thursday best,
  early morning best) still hold on the full numbers.
- Only about 41% of that account's spend is reported by age and gender (Pmax has
  neither), which is exactly what the per-table coverage figure is for.

Env vars needed on Railway before this works in production (none are needed to run
the tests): `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`,
`GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`; optional
`GOOGLE_ADS_LOGIN_CUSTOMER_ID` (defaults to the root MCC) and `GOOGLE_ADS_API_VERSION`.

## Phases 2 to 4 result (done)

- `tracker/paid_segment_insights.py`: the rules (best, waste grouped by weekend days /
  runs of hours / lists of cities, costly), campaign-type rules, caveats (coverage,
  low volume, short range, accounts that could not be read, mixed time zones), and the
  Claude summary, which is checked against the numbers it was shown (one retry, then
  the rules' own wording). 40+ tests.
- Page: `/p2/paid-marketing/segment-analysis` (`templates/segment_analysis.html`,
  `static/js/segment_analysis.js`, styles in `paid_marketing.css`): two filters, the
  seven tables with the sheet's columns, a campaign-type filter, insight cards that
  link to their table, CSV export. Routes: `api/segments/accounts`, `/report`,
  `/summary`.
- Wiring: hub card counts (4 and 4, band 31), launcher action on the Paid Marketing
  page, three command palettes, the assistant's knowledge block.

## MCC view (added at the PPC team's request)

Pick an MCC (OIA, Eventgroove, Tealium, Workato) to read all of its accounts together.
The picker lists "All of <MCC>" followed by that MCC's accounts. The report reads each
account, adds the raw rows together, and adds an **Account** table so the MCC's accounts
can be compared. An MCC with mixed currencies is split into one group per currency
(spend in different currencies cannot be added); a currency with a single account has
no group. If some accounts fail, the report names them and says the totals are short.
Mixed time zones are noted. Cost: about 7 API calls per account (a 13-account MCC is
about 90), so a cold view takes around 15 seconds and a repeat comes from the cache.
The Explorer allowance is 2,880 calls a day, which is roughly 30 cold views of a
13-account MCC.

Not built: picking a subset of an MCC's accounts.

Nothing is pushed or merged without an explicit go-ahead.
