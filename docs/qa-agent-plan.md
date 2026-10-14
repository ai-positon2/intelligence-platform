# QA Agent plan (Campaign Launch QC)

Branch: `Divith`. Section: Paid Marketing Agents (`/p2/paid-marketing/qa-agent`).
Status: **v2 built and checked against a live account. Not yet run on the real Postgres.**

## What it is

Pick a Google Ads **account**, then one of its **live campaigns**. The QC starts at once on the PPC
team's Campaign Launch QC Checklist (155 checks, 15 sections). Every check is a row:

- a box that is **right or wrong** (the cross needs a reason),
- **what the agent found**, printed, for the checks Google Ads can answer
  (check 1.7 prints the account and campaign tracking template, final URL suffix and custom parameters),
- a **comment box beside it**.

The verdict ("can this launch?") follows the workbook and is computed on the server only.

v1 asked you to describe a launch so that it could "build a checklist". That was not the ask, and it is gone.

## The workbook, as read

Columns: ID, Section, Check, Expected Setting / How to Verify, Campaign Types, Client Types, Severity,
Status (Pass / Fail / N/A), Notes / Fix Required, Checked By.

- **Severity:** Critical (launch blocker), Major (fix before launch or within 24h with a note), Minor (best practice).
- **Verdict (the workbook's rule):** NOT READY if any Critical check is Fail; else INCOMPLETE if any Critical check
  is still unchecked; else READY TO LAUNCH. A Critical N/A counts as done.
- **Notes are required for every Fail.**

## Decisions

1. **The agent runs the checks it can, and ticks only what is exact.** 65 checks have a rule. It ticks or crosses a
   box itself only when the answer is objective (Display Network off, auto-tagging on, Presence targeting, enough
   sitelinks). Where the right answer depends on the plan (budget, locations, bid target, naming) it prints the
   value and leaves the box to the reviewer. Where it saw only part of what the check asks (it reads ads and keywords,
   not every asset) it does not tick; it says what it saw. A false tick is worse than an empty box.
   The other 90 checks (landing pages, client sign-off, tag firing, policy, copy quality) are manual.
2. **A person always wins.** Their box and comment beat the agent's reading. Clicking their answer again removes it
   and the box returns to the agent's reading. The finding stays printed either way.
3. **Which rows show** follows the campaign's own type, read from Google Ads (Search, PMax, Shopping, Display,
   Demand Gen, Video, App). **Checks that exist only for a type of client** (Ecommerce, Lead Gen, Regulated...) wait
   until the client type is chosen on the page (optional chips) and are not counted until then, so a Lead Gen client
   is not held up by Ecommerce checks. The page says how many are waiting.
4. **One live QC per campaign.** Opening the campaign again resumes it (a database rule, not a check-then-insert).
5. **Re-run checks** reads the campaign again (about 18 Google Ads calls, 20 seconds apart at most). A new QC reads
   once. If Google Ads cannot be read (quota, sign-in) the QC still opens and every check can be done by hand.
6. The account list reuses the Performance Summarizer's `api/gads/accounts`; Google Ads is read through
   `tracker/paid_gads_segments.py`, so every read counts against the shared daily budget.

## What the agent reads

Per campaign, about 16 to 18 reads: account (auto-tagging, tracking template and suffix, currency, time zone, EU
political ads, enhanced conversions for leads), billing setup, auto-apply subscriptions, the campaign (status, serving
status, dates, budget, bid strategy, networks, location options, asset automation, AI Max, shopping setting),
campaign criteria (locations, languages, ad schedule, negatives), negative lists, labels, conversion actions, 30 days of
conversions, 7 days of spend, campaign and account assets. Search adds ad groups, ads and keywords. Performance Max
adds asset groups, their assets and signals. Every GAQL field was proven against the live API before it went in.

Not readable from the API, so printed as "not available" and never ticked: billing balance and credit cover, whether
a conversion action is actually recording, advertiser verification, user access, linked accounts, experiments.

## Phases

1. Plan. 2. Campaign list (`GET api/qa/campaigns`). 3. Auto-check engine `tracker/paid_qa_auto.py`.
4. Storage and routes (`pm_qc_launches` now holds account + campaign and the agent's findings; `pm_qc_checks` holds
people's answers). 5. Page. 6. Tests, browser run, review.

## How it was verified

- **Tests:** `tests/test_paid_qa.py` (55) for the checklist data, applicability, the verdict, how the agent's reading and a
  person's answer combine, validation, SQL, and every route on an in-memory store and a fake Google Ads (staff-only,
  cross-site refused, ids checked, store and Google outages handled); `tests/test_paid_qa_auto.py` (53) for every kind of
  rule on made-up rows, the "only exact answers tick" rule, which reads each campaign type makes, one refused read not sinking
  the rest, quota and sign-in errors stopping the run, and campaign ids being digits only.
- **Every SQL statement** is parsed by Postgres's own parser (`pglast`, dev-only; the test skips without it).
- **Live Google Ads:** the engine was run on real Search and Performance Max campaigns of a real account (read only).
- **Real browser:** pick account, pick campaign, the agent's 24 ticks appear with their findings, mark 1.7 wrong with a
  reason (verdict NOT READY, campaign dropdown shows it), reload (restored), pick a client type (7 more checks wait,
  then join), Re-run (the person's answer and comment kept).

## Not verified, and not built

- **Not run against a real Postgres.** None was available here; the SQL is valid and runs on a recording cursor. The tables
  are created on first use (`CREATE TABLE IF NOT EXISTS`). Please do one test QC after it deploys.
- Not built: export of a finished QC to xlsx or PDF, a Slack alert on a wrong Critical check, opening the landing page to
  see that it loads (6.3 only checks HTTPS), reading asset policy status.
- The checklist is Position2's internal QC document and this repository is public. Its wording is generic Google Ads practice,
  but say so if you would rather keep it out of the repo.
- A first read of a campaign costs about 18 of the shared daily Google Ads allowance (about 2,880).
