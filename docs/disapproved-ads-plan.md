# Disapproved Ads Agent plan

Branch: `disapproved-ads` (from `main`). Section: Paid Marketing Agents (`/p2/paid-marketing/disapproved-ads`).
Status: **Built, awaiting review.** Results and what is not verified are at the end of this file.

## What it is

Once a day it reads every active ad account, finds the ads Google or Meta has **disapproved**, and alerts by **email and Slack**. It looks only at what is live:
an ad counts only if its **campaign is enabled, its ad group is enabled and the ad itself is enabled**. Paused or removed campaigns, ad groups and ads are never read for
disapprovals, because they would only be noise. Read only: it never pauses, edits or appeals anything.

## Can disapprovals be detected? Yes, on both platforms

- **Google Ads**: every ad carries `policy_summary` with an `approval_status` (APPROVED, APPROVED_LIMITED, DISAPPROVED, AREA_OF_INTEREST_ONLY), a `review_status`
  and the **policy topics** behind it (for example "Destination not working", "Misrepresentation") with the type (prohibited, limited) and the evidence
  (the offending text or website). The query filters on `approval_status = DISAPPROVED` together with the enabled-status filters, so one read per account is enough.
  Checked against a live account: the fields and the filter work. Two more things are covered because they are where a Performance Max campaign is disapproved:
  an asset group Google marks not eligible because it is disapproved (`asset_group.primary_status`), and the individual assets in an asset group
  (`asset_group_asset.policy_summary`), reported as lower severity because the asset group keeps serving.
- **Meta Ads**: an ad has an `effective_status` of `DISAPPROVED` and the reasons in `issues_info` and `ad_review_feedback`. Only ads that are active, in an active
  ad set in an active campaign are reported.
- **Not in v1**: ads that are "approved (limited)" (they serve, with limits), keywords and sitelinks (Google's API has no policy summary for a campaign-level sitelink in
  the query used), and a disapproval that is fixed and re-disapproved between two scans.

## What it keeps and says

- A finding is one disapproved ad (or asset group, or asset) in one account: severity (high: an ad or asset group; medium: an asset in a Performance Max asset group),
  the policy topics and why, the campaign, ad group, the ad's final URL, and when it was first seen. Statuses: **open**, **fixed** (approved now, paused or removed),
  **dismissed** (a person looked; stays quiet).
- **Alerts**: one email (with `disapproved-ads.xlsx` attached) and one Slack message per scan, about the findings not alerted before. Alerted once per finding; a send that
  fails or has nowhere to go is recorded and retried at the next scan. The first scan alerts everything disapproved today (no baseline: a disapproved live ad is
  wrong whoever launched it).
- **Who gets it**: its own recipients and Slack channel on the page; while those are empty it uses the ones saved for the Unknown Domains Alert.

## When it runs

Once a day by a GitHub Actions workflow (`disapproved-ads-scan.yml`, 06:07 UTC) calling a secret-guarded address, plus **Scan now** on the page. It uses the same
`DOMAIN_ALERT_SECRET` as the Unknown Domains Alert (set on Railway and as a GitHub secret already). About 3 Google Ads reads per account a day.

## Phases

1. Probe the API on a live account (done: see above).
2. Logic and reading (`tracker/paid_disapproved.py`), tested.
3. Storage (`pm_da_findings`) and the scan (`tracker/paid_disapproved_scan.py`).
4. Routes, the page, the workflow.
5. Hub card, palette, counts (23 agents), assistant text, context doc.
6. Tests, a real browser run, PR.
