# Audience Segment Analysis plan

Branch: `Divith`. Section: Paid Marketing Agents (`/p2/paid-marketing/audience-segments`).
Status: **Built (phases 1 to 6), in review on a PR from `Divith`.**

## What it is

Compares how the audiences an account uses perform (in-market, affinity, remarketing, customer match, custom segments, similar
audiences) so the team can see which ones drive results and where to change a bid adjustment or add an exclusion. Pick an
account (or an MCC) and a date range; every audience is judged against the account's own average for the same kind of campaign,
with the numbers beside the advice.

## What the Google Ads API gives us (probed live on a real account, API v24)

| Need | Available | How |
|---|---|---|
| Performance of each audience attached to an ad group (impressions, clicks, cost, conversions, value) | yes | `ad_group_audience_view` |
| The same for audiences attached at campaign level (how Search campaigns usually carry them) | yes | `campaign_audience_view` (without `campaign_criterion.audience`, which is not a field) |
| What kind of audience it is | yes | the criterion `type` (`USER_INTEREST`, `USER_LIST`, `CUSTOM_AUDIENCE`, `CUSTOM_INTENT`, `AUDIENCE`...), and the lookup below |
| In-market versus affinity | yes | `user_interest.taxonomy_type` (`IN_MARKET`, `AFFINITY`...); the criterion's own `display_name` is only an id for these (`uservertical::80483`), so names come from `user_interest` |
| Remarketing versus customer match versus lookalike | yes | `user_list.type` (`REMARKETING`, `RULE_BASED`, `LOGICAL`, `CRM_BASED` = customer match, `SIMILAR`) |
| List size and whether it serves | yes | `user_list.size_for_search`, `size_for_display`, `membership_status` |
| Current bid adjustment | yes | `bid_modifier` on the criterion |
| Observation (bid only) or targeting (restricts who sees the ad) | yes | `targeting_setting.target_restrictions`, dimension `AUDIENCE`, `bid_only` (ad group overrides campaign) |
| Audiences excluded | yes | the criteria with `negative = TRUE`, at ad group and campaign level |
| Performance Max audience results | **no** | PMax audience signals carry no metrics in the API, so PMax is left out and the page says so |
| Meta audiences | not in this version | Meta's targeting lives on the ad set, a different API path |

## Decisions (defaults, tell me if you want any changed)

1. **Google only, same account drop and date presets** as Geo Performance and Segment Analysis (an account or an MCC's accounts together).
2. **Audiences are added across the ad groups and campaigns that use them** (one audience in five ad groups is one row), and each row opens to
   show where it is used: campaign, ad group, observation or targeting, current bid adjustment, and the numbers there.
3. **Kinds:** In-market, Affinity, Remarketing (website and app visitors, rule-based, combined), Customer match, Similar, Custom segments, Other.
   Filter chips by kind, and by campaign type (Brand, Non Brand, Demand Gen, Display, Video, Shopping...).
4. **Judged against the account's own average for the same campaign type**, the way Geo Performance judges places: an *efficiency index*
   (100 = average; ROAS against the average when the account tracks value, otherwise cost per conversion). Under 5 conversions an
   audience is "Too little data" and never judged. One that spent more than twice the average cost per conversion with real clicks and
   no conversions is "Leaking spend".
5. **What to do, by campaign type.** Search, Display, Shopping and Video take audience bid adjustments, so a strong audience gets a
   suggested adjustment (80% of the gap, 5% steps, +40% to -50%) shown beside the current one. Demand Gen and Performance Max take none, so the
   advice there is to give a strong audience its own ad group or budget, and to exclude a leaking one. A leaking audience in observation mode
   is told to lower the bid first; excluding it stops ads reaching those people.
6. **Kinds compared with each other:** one insight says which kind (in-market, remarketing, customer match...) is earning the most per
   dollar in this account, with the numbers.
7. **Lists that cannot serve:** a remarketing, customer match or similar list under what Google needs (about 1,000 active members for
   Search, 100 for Display) is named, because it cannot show ads however well it is set up. Lists the account has but no ad group or campaign
   uses are listed as unused.
8. **Exclusions are listed** (which audiences are excluded where), and an excluded audience is never also recommended.
9. **No Claude briefing in this version.** Every finding is computed from the table and carries its numbers; a written summary can be added
   the way Geo Performance has one.
10. **Read only.** The agent recommends; it changes nothing in Google Ads.

## Not in this version (said out loud)

- Performance Max audience signals, Meta ad set audiences, YouTube remarketing lists' video-view detail.
- Overlap between audiences (the API does not give it): the same click can count in several audiences, so shares do not add to 100%.
- Applying a bid adjustment or an exclusion.

## Phases

1. Plan (this file).
2. Data layer `tracker/paid_audiences.py`: the queries, names and kinds, observation or targeting, exclusions, lists, MCC folding, cache.
3. Analysis `tracker/paid_audiences_insights.py`: judging, suggested adjustments, the insights. Pure and tested.
4. Routes, the page and its script.
5. The hub card, palette and counts.
6. Tests, a real browser run, review, PR.

## What changed from the plan while building it

- **Smart Bidding matters more than the plan said.** Almost every campaign on the first real account bids with a Smart Bidding strategy,
  which ignores an audience bid adjustment. So the campaign's bidding strategy is read (`campaign.bidding_strategy_type`), a suggested
  adjustment is shown only where the strategy honours one (manual CPC, manual CPM, manual CPV, Maximize clicks, percent CPC), and
  elsewhere the advice is an ad group or a budget of its own, or an exclusion. A "Smart Bidding" insight says how many audiences this covers.
- **Google's own audience lists are not "unused".** Lists Google creates itself (`read_only`) are shown in the lists table but are not reported
  as lists the team forgot to use.

## Results

- Read live on a real account (API v24): 14 audiences in use across Brand, Non Brand and Demand Gen (in-market, affinity, a remarketing list and
  custom segments), observation versus targeting read correctly (the same audience observes in Search and targets in Demand Gen, shown as "Mixed"),
  8 audience lists with their sizes, and no exclusions. Every audience on that account had fewer than 5 conversions in 30 days, so none was
  judged: the strong, weak, leaking and bid-adjustment paths are exercised by tests only.

## Verification

- 46 new tests (`test_paid_audiences.py`) and the hub and paid marketing count tests.
- Real browser (Chrome, local server with live Google Ads): KPIs, the kinds strip, campaign type chips, insights, the audience table with a row
  opened to show where it is used, the lists table, and a 390px-wide layout with no sideways scroll.

## Not verified

- An audience judged strong, weak or leaking on a live account, an MCC view, an account with customer match and exclusions, and a phone (390px
  was checked in a frame, not on a device).
