# Account Audit (Paid Marketing)

Page (planned): `/p2/paid-marketing/account-audit`. Status: being built, in small pushed slices.

## The ask

From the agent roster ("Account Audit", category Research & Audit, Google + Meta, cadence: at onboarding, then quarterly; it merges the
existing Auditor Agents with the onboarding setup check): review the account's structure, settings, tracking, links and ads, and flag
issues and opportunities before a strategy is built. It also covers the roster's "get account access, link GA4 / GTM / CRM, verify
conversion tracking" and "audit existing accounts" steps.

## What it does

Pick a Google Ads account, a Meta ad account, or one of each (the same `<google id>+<act_...>` form Seasonality Analysis uses). The
page reads the account live (read only, cached, with a Refresh that has a cooldown) and holds it against a written checklist. Every
check has an id, a severity (critical, high, medium, low), a result (pass, warning, fail, not applicable, unknown), what was found
(with the numbers), why it matters and what to do. Each section gets a score, and the account gets an overall score and a list of the
fixes to make first.

A check that could not be read says "unknown". It is never reported as a pass or as a failure.

### Google Ads sections

| Section | What is read | Examples of checks |
|---|---|---|
| Account and settings | the customer record | account enabled, auto-tagging on, a conversion tracking owner, enhanced conversions for leads and its data terms, call reporting, a tracking template |
| Conversion tracking | every conversion action, 30 days of conversions per action | at least one primary action, an enabled primary action that recorded nothing, leads counted every time, a purchase counted once, GA4 imports and website tags both primary, no values while bidding on value, attribution model, lookback window |
| Links and audiences | product links, remarketing and customer match lists | Merchant Center link when there are shopping or retail Performance Max campaigns, GA4 inferred from imported actions (said so), lists too small to serve |
| Structure and bidding | campaigns, budgets, network and location settings, bidding, status reasons | no location targeting, presence or interest targeting for a local business, Display on a Search campaign, budget limited, bidding that has too few conversions, spend with no conversions, learning, misconfigured |
| Keywords and search terms | keywords with Quality Score, negatives, 30 days of search terms | no negative keywords, broad match without Smart Bidding, share of impressions at Quality Score 4 or less, the same keyword in several ad groups of one campaign, keywords that spent and never converted, share of spend on search terms that never converted |
| Ads | every live ad, ad strength, policy approval | an ad group with no live responsive search ad, only one ad (nothing to test), disapproved or limited ads, legacy ad types, heavy pinning, a landing page that does not answer |
| Assets | a pointer to the Asset Coverage Audit | not repeated here |

### Meta Ads sections

| Section | What is read | Examples of checks |
|---|---|---|
| Account | status, disable reason, spend cap, terms accepted | account not active, a spend cap close to reached |
| Pixel and tracking | pixels, last fired, matching, event counts | no pixel, a pixel that has not fired in days, automatic matching off, no lead or purchase event in 7 days, Conversions API not detectable |
| Campaigns and ad sets | status, objective, learning stage, issues | learning limited, ad sets with issues, an objective that does not match the optimisation event |
| Ads | review feedback, delivery issues | disapproved ads, one ad per ad set |

## What is code, not the model

Every check is arithmetic on what the platform returned. A finding quotes its own numbers. The model is not asked to find problems.
An optional "Explain" step has Claude write a short read of the findings (what to fix first, what to ask the client), from the
computed findings only, held to the numbers it was given (any other number it writes is listed).

## Build order (each is its own pushed commit)

1. This plan.
2. The check model: a finding, severities, the scoring.
3. Reading the Google account, one part at a time, each with the parser tested on real-shaped rows.
4. The Google checks, one section at a time.
5. The Meta read and checks.
6. The report: cache, unknown handling, the sections and the fix-first list.
7. The routes and the downloads.
8. The page.
9. The optional Claude read.
10. The registry (hub, Paid Marketing card, knowledge text), docs, memory.

## Not verified (to be updated at the end)

- Meta has no credentials on the dev machine, so the Meta read is tested on fakes shaped from Meta's documented answers; the first
  real Meta read happens on Railway.
- The real model is not run locally (no Anthropic key on the dev machine).
