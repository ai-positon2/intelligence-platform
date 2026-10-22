# Location Conflicts plan

Sheet item #31 ("Location Conflicts", Monitoring, "Live - to be updated", P2, owner Divith; listed Live on the sheet but not built on this platform). The sheet's extension row says: add a
"presence or interest" setting check, ad copy location against ad group name, and schedule and device drift (from the idea "Geo & Settings Drift"). The notes on the sheet call the original a
"leakage QC report". Section: Paid Marketing Agents (`/p2/paid-marketing/location-conflicts`). Built here at Krishna's request, 2026-10-22 ("start working on the next agent").

## What it is

For a Google Ads account, every account of an MCC or a Meta ad account, it reads where each campaign is **set to show** and holds everything that names a place against it. A conflict is
a place that the settings, the names and the words do not agree on: an ad group called Dover whose ad says Quincy, a campaign for Quincy that targets only Dover, a keyword for a city
the campaign does not reach, a place that is both targeted and excluded.

It is not Geo Performance (where the clicks came from) and not the Ad Copy Checker (spelling and phone numbers): this reads the targeting itself.

## What is read (Google Ads)

| Read | Field names | Why |
|---|---|---|
| Campaign settings | `campaign.advertising_channel_type`, `campaign.geo_target_type_setting.positive_geo_target_type`, `negative_geo_target_type` | presence or interest |
| Location and proximity criteria | `campaign_criterion` type LOCATION and PROXIMITY: `negative`, `location.geo_target_constant`, `proximity.radius`, `radius_units`, `address.city_name` | where each campaign shows |
| Names of the places | `geo_target_constant.name`, `canonical_name`, `target_type` (by resource name, 200 at a time) | a place is a name, with its state and country |
| Schedule and device criteria | `campaign_criterion` type AD_SCHEDULE and DEVICE with `bid_modifier` | drift |
| Search ads | the Ad Copy Checker's responsive and expanded ad query | the place an ad names |
| Keywords | `ad_group_criterion.keyword.text` of enabled keywords, not negative | the place a keyword names |

Everything but the campaign settings and the location criteria is soft: a refusal leaves that check out and says so. Meta (soft as a whole): ad sets with `targeting.geo_locations`,
`excluded_geo_locations` and `location_types`, and the ad text the Ad Copy Checker reads.

## Rules

| Rule | Level | Meaning |
|---|---|---|
| `tgt_conflict` | high | a place is targeted and excluded in one campaign, or a targeted place lies inside an excluded one (the exclusion wins, so it never shows) |
| `name_place` | high or medium | a campaign or ad group name names a place the campaign does not target (high when another campaign targets it or this one excludes it) |
| `group_copy` | high | the ad group names one place and its ad names another (the sheet's "ad copy location vs ad group name") |
| `copy_place` | high or medium | an ad names a place the campaign does not target (high when excluded) |
| `kw_place` | medium | keywords of an ad group name a place the campaign does not target |
| `setting` | medium or low | the campaign shows to people interested in the place, not only people in it (the sheet's "presence or interest"); low when it targets a whole country or the team accepted it |
| `no_target` | medium | an enabled campaign has no location at all, so it targets every country |
| `overlap` | low | two enabled Search campaigns target the same place |
| `schedule` | medium or low | the campaign's schedule differs from what most of the account's campaigns of that type run |
| `device` | medium or low | the campaign's device adjustments differ from most of its peers (an excluded device is medium) |

A place is a name the account itself uses: the places its campaigns target or exclude (with their state and country), the places the team saved, the places the Ad Copy Checker's settings
hold for other accounts and the fifty states. A name that is also an ordinary word (Orange, Reading) can be told to be ignored on the page.

## What the team saves (`settings:geoconf`)

Per account: `areas` (places its copy may name though no campaign targets them), `ignore` (words that look like places and are not), and `acceptPresence` (the team chose to show to people
interested in the place, so the setting is not a finding). Each change is logged.

## The monitor

Daily on the shared monitor layer: one issue per high or medium finding, fingerprint `geoconf:<account>:<rule>:<scope>`. The first run is a baseline. Eight reads an account a day (campaigns, locations, place names, schedules, devices, ad groups, ads, keywords), in the daily run with the other monitors; if Google's shared read allowance runs short, move it to its own workflow like the Spend Anomaly Alert.

## Not built or not verified

* Meta: the ad set's `targeting.geo_locations`, `excluded_geo_locations` and `location_types` fields are Meta's documented ones, not seen live. There is no schedule or device check for Meta, and no keyword read.

* Places are matched by name against the places the account itself uses. A campaign named for a city that no campaign targets and nobody saved is not seen (Google's place search is not read).
* The keyword read is a quarter of an account's keyword volume at most (first 5000 enabled keywords).
* Language and audience location settings are not read.
* None of the new GAQL (the criteria, the place names, the keyword read) or the Meta targeting fields was run against the live APIs from here.
