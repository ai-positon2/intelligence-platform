# QA Agent plan (Campaign Launch QC)

Branch: `Divith`. Section: Paid Marketing Agents (`/p2/paid-marketing/qa-agent`).
Status: **v3 built. The AI does the QC. Checked end to end except the real model call and the real Postgres (see the end).**

## The ask

**Nobody sits and ticks checks. The AI does the QC and flags what it catches.** Pick an account and one of its
live campaigns; the AI goes through every check on the PPC team's Campaign Launch QC Checklist (155 checks,
15 sections) and flags what is wrong. People read the flags.

History: v1 asked people to describe a launch so it could "build a checklist". v2 had the agent tick only the
~65 checks that have an exact Google Ads rule and left about 90 for a person to tick. Both missed the point.

## The flow

1. Pick the **account**, then a **live campaign** (enabled campaigns only). The review starts at once, as a
   background job; the page shows its progress and opens on the result.
2. **Evidence is gathered, read only:**
   - Google Ads (about 25 reads): account settings, billing, auto-apply, the campaign and budget, locations,
     languages, schedule, device and demographic modifiers, audiences, negatives and lists, conversion actions
     and 30 days of conversions, ads (all headlines and descriptions), keywords, sitelinks and other assets with
     their text, Performance Max asset groups with their texts and signals, search terms, 28 days of change
     history, user access, audience lists, data exclusions and seasonality.
   - The campaign's **landing pages** (up to 4), fetched like a visitor: status, redirects, whether a test gclid
     and UTM survive, title, headings and visible text, forms, phone numbers, privacy link, consent banner, tags
     found, robots.txt for AdsBot. Only public addresses on ports 80 and 443 are ever fetched.
3. **Exact rules** (65) settle what Google Ads or the page answers exactly: auto-tagging, tracking parameters, Display
   Network and Search Partners, AI Max, location options, status, ad group coverage, sitelink counts, PMax asset
   minimums, page loads, parameters surviving redirects, and more. Their result stands; the AI cannot overturn it.
4. **Claude reviews the rest**, section by section (parallel), against the evidence. Per check it answers `pass`,
   `flag` (what is wrong, the evidence, the fix) or `cannot_verify` (what is missing).
5. The page leads with **Flagged**, then **Needs a human** (what the AI could not verify), then Passed.
   A person can confirm or overrule any check with one click and a comment; none of it is required.

## Rules the AI is held to (enforced in code, not just asked for)

- **Evidence only.** The prompt says: use only the evidence, never guess a pass, `cannot_verify` for anything it
  cannot see (the media plan, the client's sign-off, a test conversion, a balance).
- **A pass or flag must quote evidence, and the quote must really be in the evidence it was shown**
  (`paid_qa_ai.grounded`). An answer that cannot be tied to it is turned into `cannot_verify` and shown as such.
- A skipped check, an unknown id, a bad verdict or a reply that is not a list never becomes a pass.
- **Exact rules beat the AI.**
- A section that fails (model down, no key, refusal, truncation) leaves its checks for a person; the page says why.
  The verdict is never READY on rules alone.
- Each section gets only its own checks and the evidence relevant to it.

## The verdict (the workbook's rule, from these answers)

NOT READY if a Critical check is flagged; INCOMPLETE if a Critical check could not be verified (a person confirming
it clears it); else READY TO LAUNCH. A Critical N/A counts as done. Major and Minor flags do not block but are listed.
Checks that exist only for a client type (Ecommerce, Lead Gen, Regulated...) count once the client type is chosen on the
page (optional chips); the AI has already reviewed them, so choosing never needs another review.

## What it costs and limits

- About 25 Google Ads reads per review from the shared daily allowance (about 2,880).
- About 55,000 tokens of prompts across about 10 sections for a Search campaign (measured on a real campaign), on
  `claude-sonnet-5-5`, effort low. Roughly a few tenths of a dollar per review (estimate, not measured).
- A review takes about a minute. One review of a QC runs at a time; re-runs are at least a minute apart; 40 reviews a
  person a day.
- The AI does not know the media plan: it flags inconsistencies (budget far from spend, locations against the ad copy
  or page) but cannot check a value against a plan it was not given.

## Phases (all done)

1. Plan. 2. More Google Ads evidence, every field proven on a live account. 3. Landing page fetcher and its rules.
4. The AI reviewer. 5. Background job, storage, routes. 6. The page. 7. Tests, browser run, commit.

## How it was verified

- **Tests** (about 290 for this agent): `test_paid_qa.py` (the checklist data, applicability, the verdict, how a person's
  answer and the AI's combine, validation, SQL, every route on an in-memory store, a fake Google Ads, fake pages and a fake
  model); `test_paid_qa_auto.py` (every rule, which reads each campaign type makes, one refused read not sinking the rest);
  `test_paid_qa_ai.py` (the guard rails above, including that removing the grounding check fails tests);
  `test_paid_qa_web.py` (page parsing, parameter survival, robots, private addresses refused); `test_paid_qa_job.py`.
- **Every SQL statement and the migration** parse with Postgres's own parser (`pglast`, dev-only).
- **Live Google Ads and live landing pages**: the full evidence pipeline was run on a real Search and a real Performance Max
  campaign (read only). It found a real landing page marked noindex, which showed that noindex must be a note, not a failure.
- **Real browser**: pick an account and campaign, watch the progress, the page opens on the flags with the verdict, the
  evidence and fix per flag, a person confirming a flag, reload (restored).

## Not verified

- **The real model has not been called.** There is no Anthropic key on this machine. The prompt, the schema, the grounding
  and everything around the call are tested with a stand-in; the browser run used a stand-in that flagged a few checks so the
  page could be seen (its answers are marked DEMO). How good Claude's judgments are on real campaigns is not known yet. The
  first real run is on Railway, which has the key. Read the first few reviews critically, check a few flags and passes against
  Google Ads by hand, and tell me what it gets wrong.
- **Not run against a real Postgres.** The tables are created on first use; a v1 table is migrated.
- Not built: export of a finished QC to xlsx or PDF, a Slack alert on a flagged Critical check, giving the AI the media plan,
  asset policy status, Merchant Center health.
- The checklist is Position2's internal QC document and this repository is public.
