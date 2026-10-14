# QA Agent plan (Campaign Launch QC)

Branch: `Divith`. Section: Paid Marketing Agents (`/p2/paid-marketing/qa-agent`).
Status: **Phases 1 to 4 built. Phase 5 (review, push) waits for a go-ahead.**

## What it is

Pick a Google Ads account, and the agent builds the launch QC checklist for it. Each
check has a box to tick when it is done. If a check fails, a comment box beside it takes
the reason. The page keeps the verdict ("can this launch?") up to date as the boxes are
ticked, and the QC is saved, so it can be resumed and shared.

Source: the PPC team's "Campaign Launch QC Checklist" workbook (155 checks, 15 sections).

## The workbook, as read

Columns: ID, Section, Check, Expected Setting / How to Verify, Campaign Types, Client Types,
Severity, Status (Pass / Fail / N/A), Notes / Fix Required, Checked By.

- **Severity:** Critical (launch blocker), Major (fix before launch or within 24h with a
  note), Minor (best practice).
- **Applies to:** Campaign Types (All, Search, Search (DSA), PMax, Shopping, Display,
  Demand Gen, Video, App) and Client Types (All, Ecommerce, Lead Gen, B2B, B2C, Local, App,
  Regulated). A row marked All always applies; a row for other values applies when the
  launch has one of them.
- **Notes are required for every Fail** (exact campaign / ad group and the fix needed).
- **Verdict (the workbook's rule):** NOT READY if any Critical check is Fail; else
  INCOMPLETE if any Critical check is still unchecked; else READY TO LAUNCH. Critical
  checks that are N/A count as done.
- Summary tab also counts Pass / Fail / N/A / Not checked by severity and by section, and
  holds the launch details: client, Customer ID, campaigns launching, campaign types,
  client type, planned launch date, QC done by, reviewed / approved by.

## Decisions made (defaults, tell me if you want any changed)

1. **Manual checks first.** The reviewer ticks each check. Automatic checks from the Google
   Ads API (auto-apply, auto-tagging, conversion status, campaign settings) are a possible
   later phase, not built now: they would use the shared Google Ads quota.
2. **Launch details narrow the list**, as in the workbook: after choosing the account you
   set the client type and the campaign types launching, and only the rows that apply show.
   Leaving a choice empty shows every row for it.
3. **Saved per launch** in Postgres: who ticked what and when, with fail reasons.
4. **The tick is Pass.** Beside it: a red cross (error, opens the comment box, reason
   required) and N/A. The workbook's Pass / Fail / N/A, in the shape you described.
5. **The account list reuses** the Performance Summarizer's `api/gads/accounts` route and its
   quota guard; no second Google Ads path. If it cannot load, the account can be typed in.

## Phases

1. **Checklist data**: the 155 rows as `tracker/qa_checklist.json`, checked against the
   workbook (counts by section and severity).
2. **Engine and storage**: `tracker/paid_qa.py` (applicability, validation, progress,
   verdict) and `pm_qc_*` tables in `paid_store`; routes under `/p2/paid-marketing/api/qa/`.
   Tests: the verdict rule, applicability, validation (a Fail needs a reason), permissions.
3. **The page**: account picker, launch details, the checklist with tick / cross / N/A and
   comment box, the verdict banner, progress by section, filters.
4. **Wiring**: hub card and counts (7 agents, band 34), section card, command palettes,
   assistant context, tests of the counts.
5. **Review and push**: nothing is pushed or merged without a go-ahead.

Not built: automatic checks from Google Ads; exporting a finished QC to xlsx or PDF; Slack
notification of a failed Critical check. Each is a natural next step.

## Result

- **Checklist data** (`tracker/qa_checklist.json`): all 155 checks, 15 sections (45 Critical,
  78 Major, 32 Minor), converted from the workbook with a script that refuses any value it does
  not recognise. It found one: check 2.10 (Consent Mode v2) is "All (EEA/UK traffic)", so it is
  kept as All with a separate `condition`, shown on the row as "If EEA/UK traffic".
- **Engine and storage** (`tracker/paid_qa.py`, `tracker/paid_qa_store.py`): applicability, the
  workbook's verdict, validation (a Fail needs a reason of at least 3 characters), and two tables
  (`pm_qc_launches`, `pm_qc_checks`). The store has its own module so the shared `paid_store.py`
  is untouched.
- **Page** (`/p2/paid-marketing/qa-agent`): account dropdown (same picker as Segment Analysis),
  the launch's QCs as chips, a new-launch form with a live count of the checks it would build,
  and the checklist with a tick, an error cross (opens a comment box) and N/A on every row.
  Every change saves at once and the server's verdict is what the banner shows.
- **Account list** reuses the Performance Summarizer's route. If Google Ads cannot list accounts
  (not connected, or the daily quota is used up), the page says so and takes the account name and
  customer ID typed in, so the QC is never blocked on Google.
- Wiring: hub card 7/7 and band 34, section card, palettes, assistant context.

## How it was verified

- Tests (`tests/test_paid_qa.py`): the data, applicability, every verdict case, validation, all
  routes on an in-memory store (staff-only, cross-site refused, ids checked, a store outage is a 503).
- Every SQL statement the store runs is parsed by Postgres's own parser (`pglast`, a dev-only
  dependency; the test skips without it).
- The page was driven in a real browser against an in-memory store: build a launch, tick all
  Critical checks (READY), a Major error (still READY), a Critical error (NOT READY), clear it,
  reload (state restored). That run found and fixed two real bugs (the autosave stealing the
  caret from the comment box, and the comment box not opening on an already-passed check).

## Not verified, and not built

- **Not run against a real Postgres.** No Postgres was available here. The SQL is valid and the
  functions run against a recording cursor, but the first real run is on Railway: the tables are
  created on first use (`CREATE TABLE IF NOT EXISTS`).
- Not built: automatic checks from Google Ads, export to xlsx or PDF, a Slack alert on a failed
  Critical check, editing the checklist itself from the page.
- The checklist is Position2's internal QC document and this repository is public. Its wording
  is generic Google Ads practice, but say so if you would rather keep it out of the repo.

Nothing is pushed or merged without an explicit go-ahead.

