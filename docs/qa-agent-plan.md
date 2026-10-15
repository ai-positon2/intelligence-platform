# QA Agent plan (Campaign Book QC)

Branch: `Divith`. Section: Paid Marketing Agents (`/p2/paid-marketing/qa-agent`).
Status: **v4 built. The AI does the QC against the PPC team's Campaign Book checks; the team can add the client's
campaign book. Checked end to end except the real model call and the real Postgres (see the end).**

## The ask

**Nobody sits and ticks checks. The AI does the QC and flags what it catches.** Pick an account and one of its
live campaigns; the AI goes through every Campaign Book check and flags what is wrong. People read the flags.
Then (v4): the checks are the ones in `Campaign Book.xlsx`, and the team can add the client's campaign book
(Excel or Google Sheet) to compare the campaign with.

History: v1 asked people to describe a launch so it could "build a checklist". v2 left about 90 of 155 checks for
a person to tick. v3 made the AI do all 155. v4 replaces that list with the team's own Campaign Book checks.

## The checks (from `Campaign Book.xlsx`: Field | Value | Other things to Check)

61 checks in 5 sections, all blocking (any flag is NOT READY):

1. **Campaign Setup (25):** name (`P2_<Search or PMax>_<launch date>`), type, networks (none), ad copies (at least 1;
   spelling, Start Case, brand), AI Max off, other assets, campaign goal (account default), bid strategy, optimisation
   goal, devices, genders, household income, audience exclusion (none), locations, language (English), age, no dynamic
   keyword insertion, negative keyword lists, EU political ads off, campaign URL (tracking, campaign or account level),
   location option (Presence), start and end date (no end), ad rotation (optimise), ad scheduling, final URL (working).
2. **Automation (2):** final URL expansion off, asset automation off.
3. **Automatically Created Assets (11):** apps, locations, promotions, business logos (on), business names (on),
   callouts, images, sitelinks, structured snippets, longer headlines, seller ratings.
4. **Auto-Apply Recommendations (21):** every auto-apply type off.
5. **Account Automation (2):** automatically generated assets off, auto migration and upgrades off.

The workbook's typos are fixed in the checklist ("Aaccount", "Shceduling", "Should a working"). The old 155-check
checklist is replaced (it is in git history).

## What Google Ads can and cannot answer

- **Exact rules (61, one per check)** settle what Google Ads gives exactly: name format, networks, AI Max, goals,
  language, negative lists, EU political ads, URL tracking, location option, end date, ad rotation, final URL loading,
  automation settings, auto-apply subscriptions. Their result stands; the AI cannot overturn it.
- **Depends on the campaign book** (campaign type, bid strategy, devices, genders, household income, locations, age, ad
  schedule): the rules print what is set (devices with bid adjustments, ages and genders at campaign and ad group level,
  locations by name, schedule). The bid strategy and campaign type are compared with the book when it names an
  unambiguous value; the rest go to the AI with the book.
- **Not shown to the API, so never ticked:** the account's "automatically created assets" switches (apps, locations,
  promotions, logos, names, callouts, images, sitelinks, snippets, longer headlines, seller ratings), the
  "automatically generated assets" switch, auto migration and upgrades. An automatically created asset found in the
  account PROVES its switch is on (a flag); finding none proves nothing. These stay under "Needs a human".
- **Auto-apply recommendations:** Google lists a subscription only for types that were subscribed. No subscription
  means every recommendation is off (pass). Some enabled means the matching checks fail and the rest are left for a person.
  This reading of "no subscription" has not been confirmed against an account that has auto-apply on: spot check it.

## The campaign book

Added per QC as an `.xlsx` or `.csv` file (read in the browser and sent as base64, up to 3 MB) or a Google Sheet link
shared as "anyone with the link can view" (fetched as an export from docs.google.com only, through the public-address
guard). Read into Field | Value | Other rows plus a text dump of every sheet, because a client's book will not always
keep the template layout. A placeholder ("Depends on the campaign book") is not an answer. Adding or replacing the book
starts a fresh review against it; removing it does not. The AI sees the rows and the text.

## The flow, and the rules the AI is held to

Same as v3: the review runs in a background thread with progress in Postgres; the page opens on Flagged, then Needs a
human, then Passed. The AI answers `pass`, `flag` (what is wrong, evidence, fix) or `cannot_verify`; a pass or flag must
quote evidence that is really in what it was shown or it becomes `cannot_verify`; a failed section leaves its checks for
a person; exact rules outrank the AI; a person's click outranks both; the verdict is computed on the server only.
Per campaign: about 16 Google Ads reads, up to 4 landing pages, a few small model calls (2 to 5 sections instead of 15).

## Verdict

NOT READY if any check is flagged; INCOMPLETE if any check could not be verified (a person confirming it clears it, or
adding the campaign book if that is why); else READY TO LAUNCH. N/A counts as done. Because the account switches cannot
be read, a campaign is READY only after a person confirms them.

## Migration

The tables from v1 to v3 may exist on the deployed database. `SCHEMA` brings them up to shape (adds the campaign,
review and book columns) and, once, clears the old per-check answers and reviews (they were for check ids that no longer
exist), guarded by a marker column. QCs themselves are kept and review again when opened.

## How it was verified

- **Tests** (about 290 for this agent): `test_paid_qa.py` (checklist data, verdict, AI/person combination, validation,
  SQL and migration, every route including the campaign book upload, a Google Sheet link, private sheet, bad files);
  `test_paid_qa_auto.py` (every rule, including book comparisons and the switches the API cannot show);
  `test_paid_qa_ai.py` (the guard rails; removing the grounding check fails tests); `test_paid_qa_book.py` (reading
  workbooks, CSV, uploads, links, size limits); `test_paid_qa_web.py`; `test_paid_qa_job.py`.
- **Live Google Ads:** every GAQL field was proven on a real account before it went in (a first guess at the account-level
  automation fields was rejected by the API, which is why those checks are "needs a human"). The rules ran on a real Search
  campaign: they flagged its name (not `P2_<type>_<date>`) and Search Partners being on, passed AI Max off, English, negative
  lists, Presence, goals equal to the account's, and read its devices, ages and household-income settings.
- **Real browser** with the team's real `Campaign Book.xlsx`: progress, the result opening on flags, the book panel (60 fields read).

## Not verified

- **The real model has not been called** (no Anthropic key on the dev machine). The prompt, schema, grounding and everything
  around the call are tested with a stand-in; the browser run used a stand-in marked DEMO. First real run is on Railway.
  Check a few flags and passes against Google Ads by hand and tell me what it gets wrong.
- **Not run against a real Postgres.** The tables are created on first use; the migration was parsed, not run.
- A real filled-in client campaign book has not been seen: only the blank template. Whether clients' books keep that layout
  is unknown; the AI also gets the raw text of every sheet for that reason.
- Not built: reading a screenshot of the account's automation settings (the one way to verify the switches the API hides),
  export of a finished QC, a Slack alert, account-level confirmation so the same switches need not be confirmed per campaign.
- The checklist is Position2's internal document and this repository is public.
