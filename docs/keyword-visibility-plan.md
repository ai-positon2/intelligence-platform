# Keyword Visibility Tracker plan

Sheet item #35 ("Keyword Visibility Tracker", Monitoring, "Not built", P2 suggested, cadence Weekly, owner TBD; Google only). The sheet says: "Tracks top-of-page impression share, lost IS and Quality Score on
the highest-spend keywords, and flags new competitors in auction insights." It absorbs "Top Spending Keywords Position Tracking; Quality Score Drops; Competitor & Brand Hijack", and notes that "Google no
longer reports true average position, so this uses impression share metrics." Section: Paid Marketing Agents (`/p2/paid-marketing/keyword-visibility`). Built here at Krishna's request, 2026-10-22.

It is not the Brand IS Monitor (brand impression share of whole campaigns against a threshold): this reads single keywords, and the competitors in the auctions.

## What it is

For a Google Ads account or every account of an MCC it takes the account's highest-spend Search keywords (the top 25 of the last 28 days, 5 to 100 as the team saves) and holds the **last whole week**
(Monday to Sunday) against the **four weeks before**: how often the keyword shows at the top of the page, how much it loses to rank, its Quality Score now and four weeks ago. Separately it reads
the auction insights of the live Search campaigns and flags a competitor that has newly appeared, or that overlaps far more than before, and on a brand campaign calls it a possible brand hijack.

## What is read (Google Ads)

| Read | Field names | Why |
|---|---|---|
| The top keywords | `keyword_view`: `campaign.id`, `ad_group.id`, `ad_group_criterion.criterion_id`, `keyword.text`, `keyword.match_type`, `quality_info.quality_score`, `metrics.cost_micros` for 28 days, enabled, not negative, by cost, `LIMIT` the number tracked | which keywords |
| Their weeks | `keyword_view` with `segments.week`: `metrics.impressions`, `clicks`, `cost_micros`, `conversions`, `search_impression_share`, `search_top_impression_share`, `search_absolute_top_impression_share`, `search_rank_lost_impression_share`, for the criterion ids above | visibility by week |
| Quality Score by week (soft) | `keyword_view` with `segments.week`: `metrics.historical_quality_score` | the drop |
| Auction insights (soft) | `campaign` with `segments.week` and `segments.auction_insight_domain`: `metrics.auction_insight_search_overlap_rate`, `auction_insight_search_impression_share`, `auction_insight_search_outranking_share`, `auction_insight_search_position_above_rate`, `auction_insight_search_top_impression_percentage` | competitors |

The top keywords and their weeks are the point (a failure there is raised); the Quality Score history and the auction insights are soft (a refusal leaves those checks out and says so). Lost impression share
to budget is not reported per keyword by Google, so only the share lost to rank is read.

## Rules

| Rule | Level | Meaning |
|---|---|---|
| `brand_hijack` | high or medium | on a brand campaign (the Brand IS Monitor's name rule) a competitor domain is new (high) or its overlap has risen (medium) |
| `comp_new` | medium | a competitor domain overlaps at least 10 percent in the last week and had under 2 percent in each of the four before |
| `comp_up` | low | a competitor's overlap is at least 20 percent and up at least 15 points on its average of the four weeks before |
| `is_drop` | medium or high | a keyword's top-of-page share fell 15 points (medium) or 30 (high) from its four-week average |
| `top_is_low` | medium or low | a keyword shows at the top of the page less than the target (50 percent unless saved): medium when 20 points or more under |
| `qs_drop` | medium or high | Quality Score fell by 2 or more over four weeks (high when it is now 4 or less) |
| `qs_low` | medium or low | Quality Score under the lowest wanted (5 unless saved): medium at 4 or less |
| `lost_rank` | medium or low | the keyword loses 40 percent (low) or 60 percent (medium) of the searches it was eligible for to its rank |

Enough data first: a keyword's share is judged only with 100 eligible searches (impressions divided by the share) in the week, and its four-week average only with 100 more. A keyword without that is
listed as "not judged" with what it had. Competitor domains the team ignores (a sister site, a partner) are left out.

## What the team saves (`settings:kvis`)

Per account: `topIs` (the lowest wanted top-of-page share, percent), `qsMin`, `topN` (how many keywords, 5 to 100) and `ignore` (competitor domains to leave out). Each change is logged.

## The monitor

Weekly (Mondays, `weekdays: (0,)`): one issue per high or medium finding, fingerprint `kvis:<account>:<key>`; low findings counted in one quiet issue; the first run is a baseline. Four reads an account a week.

## Not built or not verified

* **Auction insights are refused to this app (found live, 2026-10-22):** Google Ads answers 403 "The developer doesn't have access to metrics" for every `auction_insight_*` metric, so the competitor rules (`comp_new`, `comp_up`, `brand_hijack`) cannot run until Google grants the developer token that access. The read is kept (a standing limit, said on the page, not a failure), so it starts working the day access is granted.

* None of the new Google queries (keyword_view with `segments.week`, `historical_quality_score`, the auction insight metrics and segment) was run against the live API from here. In particular auction insights
  by `segments.auction_insight_domain` on `campaign` is the documented way, and the account's own row is left out when Google names it.
* Only Search keywords are read; Performance Max has no keywords.
* Keyword-level budget loss, true average position and the competitors' own impression share on a keyword are not available.
* The top keywords are the top of the last 28 days: a keyword that stopped spending is no longer tracked.
