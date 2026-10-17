# Account Audit (Paid Marketing)

Page: `/p2/paid-marketing/account-audit`. Status: built, tested, and run on real Google Ads accounts; the Meta half is tested on a double only (see the end).
Every check, with why it matters and what to do, is in `docs/account-audit-checks.md` (generated from the catalog and held equal to it by a test).

## The ask

From the agent roster ("Account Audit", category Research & Audit, Google + Meta, cadence: at onboarding, then quarterly; it merges the
existing Auditor Agents with the onboarding setup check): review the account's structure, settings, tracking, links and ads, and flag
issues and opportunities before a strategy is built. It also covers the roster's "get account access, link GA4 / GTM / CRM, verify
conversion tracking" and "audit existing accounts" steps.

## What it does

Pick a Google Ads account, a Meta ad account, or one of each (the same `<google id>+<act_...>` form Seasonality Analysis uses; an MCC is not
audited). The page reads the account live (read only, cached for an hour, with a Refresh that has a cooldown) and holds it against a written
checklist. Every check has an id, a severity (critical, high, medium, low), a result (pass, warning, fail, not applicable, could not check),
what was found (with the numbers), why it matters and what to do. Each section gets a score, and the account gets an overall score, a grade
and a list of the fixes to make first.

A check whose input could not be read says "could not check". It is never reported as a pass or as a failure, and it is left out of the
score and counted, so the page can say how much of the account it could not look at.

### Scoring

A pass earns a check's weight (critical 8, high 4, medium 2, low 1), a warning half of it, a failure nothing. The score is the share of the
weight that was earned over the checks that could be judged. The grade is held at C or below when a critical check failed, when 30 percent or more
of the checks could not be read (a high score over a small judged part is not a good account), or when a check says the account is not advertising
(most checks then pass because there is nothing to fault); the page and the downloads say which. The score decides nothing by itself: the fix-first
list is the worst failures and warnings by severity, then failures before warnings, then how many things they touch.

### Google Ads (52 checks, 6 sections)

Account and settings; conversion tracking; links and audience lists; structure and bidding; keywords and search terms; ads and landing pages.
17 read-only queries (4 at a time) read the account record, conversion actions and their 30 days of conversions, product links, audience
lists, campaigns with their settings, numbers, locations, negatives and network split, ad groups, keywords with Quality Score and their numbers,
ads, and the 1,000 most expensive search terms. The landing pages the live ads use are then fetched (25 at most, through the platform's
SSRF-guarded fetch, a timeout tried once more), and a page that refuses an automated request is reported as not verified, not as broken.

### Meta Ads (17 checks, 4 sections)

The ad account (status, disable reason, spend cap, pixel terms); the pixel (installed, last fired, conversion events and not only page views,
advanced matching, events arriving through the Conversions API); campaigns and ad sets (learning limited, issues Meta flags, spend with no
results, optimisation for clicks in lead and sales campaigns, frequency, whether any campaign is live); ads (disapproved, an ad set with no ad,
a single ad).

## What is code, not the model

Every check is arithmetic on what the platform returned. A finding quotes its own numbers. The model is not asked to find problems. An
optional "Write the briefing" step has Claude write a short read of the findings from the scores, the checks to fix first and what could not
be checked, never from the names of campaigns, keywords, search terms or pages (a sentence can name a conversion action, a pixel or an account, and
any identifier, such as a Google tag id or a Merchant Center id, is masked), and a number it writes that the audit did not give sends it back once and
then falls back to the findings' own words. One briefing is made per read, however many people ask.

## Saved audits

Nothing is saved unless a person asks. Save audit keeps an audit with a short note (Onboarding, Q1 review) in `pm_audit_history`; the page then
shows what changed since the last one: checks put right, checks that got better, new problems, checks that got worse, how many are still
failing, and the score change. Only a check judged in both audits is compared.

## Files

`tracker/paid_audit_model.py` (finding, scoring), `paid_audit_google_read.py`, `paid_audit_google_checks.py`, `paid_audit_meta_read.py`,
`paid_audit_meta_checks.py`, `paid_audit_pages.py`, `paid_audit.py` (the report), `paid_audit_history.py`, `paid_audit_store.py`,
`paid_audit_export.py`, `paid_audit_summary.py`; `templates/account_audit.html`, `static/js/au_data.js`, `static/js/account_audit.js`,
`static/css/account_audit.css`; routes in `app.py` between "Account Audit ---" and "Strategy & Campaign Book ---".

## What the platforms do not tell us

- Google's API does not show account-level negative keywords or the Google Analytics 4 link. The negative keyword check says so, and the GA4
  check is inferred from conversions imported from GA4 (and is "could not check" when there are none).
- Demand Gen sets its locations on each ad group, so the campaign location check does not judge Demand Gen.
- How Meta labels where an event came from (pixel or Conversions API) is read as Meta labels it; the check says what it saw.

## Not verified

- **Meta against a real account.** There is no Meta token on the development machine, so the Meta read is tested on a Graph API double shaped
  from Meta's documented fields. A wrong field name makes that part "could not read", never a wrong pass, but the first real read happens on Railway.
- **The real model.** There is no Anthropic key on the development machine, so the briefing is tested on a stand-in.
- Production while signed in (the browser pane cannot sign in).

## Independent review

A reviewer who had not seen the reasoning read the code and found 14 real defects, all fixed in their own commits with tests: a budget check that passed on
numbers it could not read; tracking checks that failed a paused account and app and store accounts; call reporting demanded for a website click-to-call
tag; an imported action's attribution called last click; rounded shares deciding thresholds; a briefing that called the model on every click; a partial
read cached for an hour and a saved-copy fallback that only worked within one day; an A for an account that was mostly unread or not advertising; a
stale audit filed as today's; portfolio strategies judged one campaign at a time; and four Meta checks (the ad-set check that contradicted the ads check,
campaigns Meta flags with issues left out, lead campaigns optimised for Schedule or Contact called dead, automatic events counted as conversions).
