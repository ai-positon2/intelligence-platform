# Disapproved Ads Tracker plan

Sheet item #27 (roadmap: "Detects disapproved or limited ads across accounts and alerts the team so they can fix or appeal them quickly"). Section: Paid Marketing
Agents (`/p2/paid-marketing/disapproved-ads`). Owner on the sheet: Krishna. Built here: Krishna's request, 2026-10-21.

## What it is

For a Google Ads account, or every account of an MCC, it lists the **enabled ads that Google has disapproved or limited**, says which of them stop an ad group from
showing anything at all, groups them by the policy that flagged them (one fix often clears dozens), says what each policy usually means and what usually fixes it,
and sizes what the stopped ad groups used to bring.

## What is read (read only)

Per account, in three small reads:

1. The ads: every enabled ad, in an enabled ad group of an enabled campaign, whose policy approval status is disapproved, approved (limited) or area-of-interest only,
   with the policy topics Google named (and the type of each, and the text it points at when it gives one), the review status (under review, under appeal), the ad's
   type, its first headlines and its final URL.
2. The cover: how many approved ads each of those ad groups has. Without it a disapproved ad cannot be called harmless or serious. If it cannot be read, nothing is called
   serious and the page says so.
3. The weight: the clicks and cost of those ad groups by whole week for the last five, so an ad group that used to bring clicks and now brings none is sized.

Reads 2 and 3 are soft: a refusal leaves that part out and says so. Nothing is changed in Google Ads.

## What it says

- **Stopped**: an ad group whose every enabled ad is disapproved. Nothing in it can show, whatever its keywords. High.
- **Disapproved**: an ad disapproved while another approved ad in the ad group still serves (rotation is smaller, and one fix clears it). Medium. When the cover could not be
  read, a disapproved ad is medium and says it could not be checked.
- **Limited**: approved with limits (it shows, not everywhere or not to everyone). Low.
- Under appeal and in review are said as they are (info), never counted as fixed.
- **Why**: policy topics, grouped, with a plain-words hint (the Asset Coverage's library plus the ones ads meet), the type (prohibited cannot be appealed into approval,
  limited can) and the text Google points at when it names it.
- **Worth** (an estimate, it says so): the clicks a week a stopped ad group made before against last week, from the five weeks read. Money is not lost (the budget goes to other
  ad groups); the searches the ad group answered are.

## The fix plan

Stopped ad groups first: put an approved ad in so they serve while the flagged one is fixed (a plain responsive search ad). Then one step per policy topic, the most ads first:
what it usually means, how to see the reason in Google Ads (Ads, the status of the ad; Tools, Policy manager), how to fix it (edit the ad or the page; an edited ad is reviewed
again), and when to appeal instead (only when the ad complies as written). Then the check the next day.

## Built after the first version (2026-10-21)

- **Alerts, the daily run, new since yesterday**: the shared monitor layer (see `docs/brand-spend-plan.md`). A stopped ad group is high, a covered policy topic medium, a limited one
  low, a disapproved keyword medium. Ages ("stopped for 3 days"), "came back" counts and how long fixes took come from the saved issues.
- **Performance Max asset policy**: disapproved or limited assets of enabled asset groups; an asset-group field is stopped when approved assets fall below what Google needs
  (3 headlines, 1 long headline, 2 descriptions, 1 landscape and 1 square image, 1 business name).
- **Keyword disapprovals**: disapproved keywords of enabled ad groups (retried without the policy field if Google refuses it).
- **Meta**: rejected (DISAPPROVED) and flagged (WITH_ISSUES) ads, an ad set stopped when it has no active ad left; Meta accounts are in the picker.
- **Insight**: what the flagged ads have in common (a headline, a page, the text Google points at), policies that flag ads in several accounts, clicks lost since the stop.

## Still not built (said out loud)

- **Demand Gen, Shopping and app campaigns**: Merchant Center feed disapprovals are not read.
- **Meta sizing**: clicks lost per ad set are not estimated.
- **Live verification**: no new query (asset_group_asset policy, keyword policy_summary, the Meta ad fields) has been run against the real Google or Meta APIs from here.

## Phases

1. Plan and the pure logic (`tracker/paid_disapproved_math.py`). Tested and mutation-tested.
2. The fix plan (`tracker/paid_disapproved_plan.py`).
3. The read and the report (`tracker/paid_disapproved.py`), cached like the other Google agents.
4. Routes, the page, CSV.
5. Hub card, palette, counts, the assistant's text.
