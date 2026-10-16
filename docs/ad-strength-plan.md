# Ad Strength Analysis plan

Branch: `Divith`. Section: Paid Marketing Agents (`/p2/paid-marketing/ad-strength`).
Status: **Built (phases 1 to 7), in review on a PR from `Divith`.**

## What it is

Reviews the ad strength rating Google gives each live Responsive Search Ad (RSA) and each Performance Max asset group,
and says what to change in the headlines and descriptions to lift the weak ones to Good or Excellent. Pick an account (or an
MCC) and it lists every ad that is below Good, the worst-and-busiest first, with Google's own advice beside our reading of it.

## What the Google Ads API gives us (probed live on a real account, API v24)

| Need | Available | How |
|---|---|---|
| RSA ad strength (Poor / Average / Good / Excellent / Pending) | yes | `ad_group_ad.ad_strength` |
| The tips Google shows while you build an RSA ("Try including more keywords in your headlines", "Add 2 more sitelinks") | **yes** | `ad_group_ad.action_items` (a list of Google's own sentences) |
| Every headline and description of an RSA, with its pinned position | yes | `ad_group_ad.ad.responsive_search_ad.headlines / descriptions` |
| Per headline or description rating (Pending, Learning, Low, Good, Best) | yes | `assetPerformanceLabel` on each text, and `ad_group_ad_asset_view.performance_label` |
| Google's suggested new RSA text (headlines and descriptions it wrote) | yes, when Google has one | `recommendation` of type `RESPONSIVE_SEARCH_AD`, `RESPONSIVE_SEARCH_AD_ASSET`, `RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH` |
| The ad group's keywords (so suggestions can use them, which is Google's most common tip) | yes | `ad_group_criterion` |
| PMax asset group ad strength | yes | `asset_group.ad_strength` |
| PMax asset group tips | **no**: `action_items` does not exist on `asset_group` | we work them out from the asset counts |
| PMax per-asset rating | **no**: not exposed on `asset_group_asset` | counts and text only |
| PMax Google suggestion | partly: `IMPROVE_PERFORMANCE_MAX_AD_STRENGTH` names the asset group and the strength Google thinks it can reach, with no text | shown as "Google suggests improving this group" |

So Google's RSA recommendations can be pulled (the tips on every ad, and its suggested text where it has some). For
Performance Max the API does not carry Google's tips, so those are worked out from what the asset group holds against what
Google asks for.

## Decisions (defaults, tell me if you want any changed)

1. **Google only.** Meta has no ad strength rating, so there is no platform switch. The page says so.
2. **Same account drop as the other agents:** one account, or an MCC's accounts together.
3. **Live and enabled only:** enabled campaigns, ad groups and ads (paused ones are not worth improving).
4. **Two tabs:** Search ads (RSA) and Performance Max.
5. **The last 30 days** weights the list: an Average ad with 40,000 impressions comes before a Poor one nobody sees.
   No date picker; the window is stated on the page.
6. **The work list is everything below Good**, ordered by (how weak) x (impressions). Good and Excellent ads are counted, not listed
   (a toggle shows them).
7. **Per RSA:** the strength, Google's tips, the headlines and descriptions with their ratings and pins, and what the rules see
   (too few headlines or descriptions, near-duplicates, headlines that carry none of the ad group's keywords, everything pinned,
   a Low-rated text).
8. **Per PMax asset group:** the strength, and every asset type counted against what Google accepts (headlines 3 to 15, long
   headlines 1 to 5, descriptions 2 to 5, images, logos, videos), with the gaps named. Images, logos and video are named as
   gaps but never invented.
9. **Suggested changes are written by Claude on request** (a button on the ad, not for the whole account, so nothing is spent
   on ads nobody opens). It is given the ad's own text, its ratings, Google's tips, the ad group's keywords and Google's suggested
   text. It returns text to replace (which Low text goes, and what takes its place) and text to add. **The code, not the model,
   enforces Google's limits:** headline 30 characters, description 90, long headline 90, no more than 15 headlines and 4
   descriptions, no duplicates of existing text or of each other, no exclamation marks in headlines, no all caps, no em dashes.
   Anything that fails is dropped before the page sees it.
10. **Read only.** The agent recommends; it does not edit ads.

## Not in this version (said out loud)

- Applying a change in Google Ads. Copy the text into the ad.
- A promise of the rating. Google does not publish how it scores strength, so a suggestion is aimed at its tips and its labels, not
  guaranteed to reach Good or Excellent. The page says this.
- Landing page checks (the QA Agent does that) and image or video generation.
- Demand Gen, Display responsive ads and App ads.

## Phases

1. Plan (this file).
2. Data layer `tracker/paid_adstrength.py`: the queries, RSA and asset group reading, recommendation reading, MCC folding, cache.
3. Analysis `tracker/paid_adstrength_insights.py`: gaps, priority, what to change. Pure and tested.
4. Claude suggestions `tracker/paid_adstrength_ai.py`: the call, the limits enforced in code, the fallback from Google's own text.
5. Routes, the page and its script.
6. The hub card, palette and counts.
7. Tests, a real browser run, review, PR.

## What changed from the plan while building it

- **A full ad takes new text in place of old text.** The first real account had every RSA at 15 headlines and 4 descriptions and still
  rated Average (Google's tip was "include more keywords"). Adding text is impossible there, so each suggestion names the text it
  replaces and why: Google rates it Low, it nearly repeats another, or it carries none of the ad group's top keywords. Pinned text is
  never offered for replacement.
- **Performance Max with every slot full.** Two of the three Average asset groups on that account differ from the Good ones only in
  having one portrait image instead of two or three, so image counts (not only text) are checked and named.

## Results

- Read live on a real account (API v24): 12 RSAs (11 Average, 1 Good), 10 asset groups (3 Average, 7 Good), Google's tips on every
  RSA, the text Google itself recommended for ad groups where it had some, and the keywords of the weak ad groups. No per-asset
  rating exists on that account yet (all "Pending"), so the Low-first rule has only been exercised by tests.
- The Claude suggestions have never run against the real model (no Anthropic key on the build machine); the page was checked with
  a stand-in. The code that holds any answer to Google's limits and to the ad's own facts is unit tested.

## Verification

- 75 new tests (`test_paid_adstrength.py`, `test_paid_adstrength_ai.py`), and the hub and paid marketing count tests.
- Real browser (Chrome, local server with live Google Ads): KPIs, the Search and Performance Max tabs, rating filters, an ad opened
  with its tips, rules, texts and keyword chips, the suggestion flow with copy buttons, and a 390px-wide layout with no sideways scroll.

## Not verified

- An MCC view in the browser (it reads each account in turn, about 5 reads per account), the real model's wording, a rated asset
  with a Low label on a live account, and a phone (390px was checked in a frame, not on a device).
