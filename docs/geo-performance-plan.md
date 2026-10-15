# Geo Performance plan

Branch: `Divith`. Section: Paid Marketing Agents (`/p2/paid-marketing/geo-performance`).
Status: **Built (phases 1 to 7), in review on PR from `Divith`.**

## What it is

Breaks performance down by location to find the high and low performing regions and to guide geo bid adjustments and
targeting. Pick an account (a Google Ads account or MCC, a Meta ad account, or both, as in Segment Analysis and the
Performance Summarizer), a date range and a location level, and the agent shows where the money goes, which places
earn their spend, which leak it, and what bid adjustment each one deserves. Charts, with a choice of chart and metric.

## Decisions (defaults, tell me if you want any changed)

1. **Same controls as the agents already built:** the platform switch (Google | Meta | Both) with the account pickers
   (MCCs included), the same date presets and custom range, and a campaign type filter (Brand, Non Brand, Performance Max...).
2. **Location levels.** Google: Country, Region (state), Metro (DMA), City, County, Postal code. Meta: Country, Region, DMA.
   Both: Country and Region, merged by name (the two platforms' other levels do not line up). One level is read at a time,
   so a view costs a few Google Ads reads, not dozens.
3. **Which location report.** Google's "user location" report (where the person was, not only what was targeted), the same one
   Segment Analysis uses for cities. It carries conversions and conversion value, so ROAS is there when the account tracks value.
4. **Metrics:** impressions, clicks, cost, conversions, conversion value, then CTR, CPC, conversion rate, CPA (cost per
   conversion), ROAS (when there is value), share of spend and share of conversions.
5. **Performance is judged against the account's own average**, not a fixed target. A location's *efficiency index* is the
   account's CPA divided by the location's CPA (100 = average, above 100 is better); when the account tracks value it is
   the location's ROAS over the account's. A location with too little data is never judged.
6. **Suggested bid adjustment** per location: a dampened version of the index (80% of the gap, in steps of 5%, no more than
   +40% or less than -50%), only where there is enough data (at least 5 conversions). A location that spent a lot and
   converted nothing is flagged to exclude or cut hard. Every suggestion says how much data is behind it. These are
   starting points to apply gradually, not instructions.
7. **Graphs, with a picker:** ranked bars (top or bottom N by any metric), bubble (cost against results, size = clicks,
   colour = verdict), share of spend against share of conversions (who over or under delivers), doughnut (share of a
   metric), and efficiency against spend. Metric, top N and sort are choices; a chart can be saved as a PNG.
8. **A sortable table** of every location with a verdict pill and the suggested adjustment, filterable by text and verdict,
   with an export to CSV.
9. **What to do:** rule-based insights (push, trim, exclude, concentration, thin data) each with its numbers beside it, then
   a short summary written by Claude from those insights only, with every number checked against what it was shown
   (the same guard as Segment Analysis).

## Not in this version (said out loud)

- A map. No map library is bundled; the bubble and bar charts carry the picture. A US tile map is a natural next step.
- Comparison with a previous period (a column of change per location).
- Applying the bid adjustments in Google Ads. This agent only recommends.
- Locations the account *targets* versus where people were: the report is by where people were.

## Phases

1. Plan (this file).
2. Google Ads location data per level: queries proven on a live account, rows read into a common shape, place names.
3. Analysis engine `tracker/paid_geo_insights.py`: indexes, verdicts, suggested adjustments, insights. Pure and tested.
4. Meta (country, region, DMA) and the Google + Meta merge.
5. Routes (accounts reuse the existing ones), caching, the page and its charts.
6. The hub card, palette, counts; the Claude summary.
7. Tests, a real browser run, review, PR.

## Results

- Google reads proven live on a real account at region and city level (both returned rows, real verdicts and insights; the
  city level was all "too little data", which is the honest answer for a small account over 30 days).
- Meta and the Google + Meta merge are covered by unit tests with a fake fetch only. Meta is not connected on the machine this
  was built on, so **a real Meta read has not been run**.
- The Claude briefing has never run against the real model (no Anthropic key on the build machine); the page was checked with a
  stand-in. The number check and the rules fallback are unit tested.

## Verification

- 106 new tests (`test_paid_geo.py`, `test_paid_geo_insights.py`, `test_paid_geo_routes.py`), and the hub and paid marketing count tests.
- Full suite: only Windows-only failures remain (`test_job_change_*` temp file locks, `test_sci_audio` temp permissions,
  `test_product_analysis_js` command line too long), none touching this change.
- Real browser (Chrome, local server with live Google Ads): KPIs, campaign-type chips, insights, summary, all five charts, the
  verdict filter and column sort, CSV export, PNG export, the platform switch (Meta levels, the "not connected" message), the
  empty-data message on the efficiency chart, and a 390px-wide layout with no sideways scroll.

## Not verified

- A real Meta account, an MCC view in the browser, the real model's wording, and a phone (390px was checked in a frame, not on a device).
