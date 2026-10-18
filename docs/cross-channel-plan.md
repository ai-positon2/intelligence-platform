# Cross-Channel & Attribution Analysis (Paid Marketing agent 9), first version: Google Ads and Meta Ads

The sheet asks for blended CPA and ROAS across Google, Meta, LinkedIn and programmatic, reconciled to GA4. The platform reads none of GA4, LinkedIn Ads or programmatic yet, so this version
covers the two platforms it can read, and a reconciliation the person types in (the conversions and revenue their own system recorded) stands in for GA4. It only reads. No model is asked: every
figure is arithmetic a test checks.

## What it answers

1. How do the two platforms compare, and what is the blended result? Spend, clicks, conversions, value, cost per conversion and return on ad spend per platform and blended, against the previous
   period of the same length, with each platform's share of the spend beside its share of the conversions and the value.
2. What moved the blended cost per conversion? The change is split exactly into a mix part (the spend moved between platforms) and a rate part (each platform got cheaper or dearer).
3. Which kinds of campaign carry the spend? Each campaign type (Google Search, Performance Max, Demand Gen, Display, Video, Shopping; Meta by objective) with a rule-of-thumb role: captures demand,
   creates demand, or mixed. A campaign that creates demand is not judged on last-click cost per conversion alone.
4. Do the two platforms move together? Week over week changes of one platform's spend against the other's conversions, this week and next, with a strict bar (see below).
5. Are the two platforms measured the same way? The conversion actions Google counts (primary ones, lookback windows, attribution model) against what Meta counts (the attribution window
   of each ad set, weighted by spend). Different windows and models make the two numbers incomparable before any analysis.
6. How much do the platforms claim between them against what really happened? The person types the conversions (and revenue) their own system recorded for the period; the page says whether the
   platforms claim more than happened (so they are counting the same people), and what each platform's cost per conversion and return become if the claim is scaled down to what happened.

## What it does not know

* GA4, LinkedIn Ads, programmatic, CRM and offline sales are not read. Conversions that neither platform claims cannot be seen. The reconciliation is the person's number, typed in.
* Platform numbers are each platform's own claim, over its own window and model, and both can claim the same sale. They are never added up as if they were separate people unless the page says so.
* An average cost per conversion is not a marginal one: the cheaper platform may not take more money at the same price. A suggested shift is a test, not a plan.
* The weekly link between platforms is a pattern in the numbers, never proof of cause.

## Rules (all in code)

* Period: last 30, 60 or 90 days, last calendar month or the month so far; the previous period is the same number of days straight before it. Both are read from the stored daily numbers
  (tracker/paid_gads.py and paid_meta.py), so a repeat read costs nothing.
* A platform with fewer than 30 conversions in the period is too thin to compare on cost per conversion (the page says so and does not rank it).
* A cost per conversion gap of 1.5 times or more between platforms with 30 or more conversions each is flagged; a share gap (share of spend minus share of conversions) of 15 points or more is flagged.
* The weekly link needs 11 whole weeks (10 week-over-week pairs). Four tests are run (each platform's spend against the other's conversions, this week and next), so the bar for each is p below 0.0125 and
  a correlation of at least 0.5 either way. Anything weaker is reported as no clear link.
* The conversion measure is the platform's primary one (Google: Conversions, Meta: results of the campaign objective) or all of them (Google: All conversions, Meta: every event). Google's
  All conversions can be refused by Google; then the page says so.
* Meta counts view-through conversions inside its window; Google's Conversions column counts clicks only (view-through is its own column). The page says so.
* Currency: a Google and a Meta account are read together only when they bill in the same currency.

## Downloads

Excel (a sheet per table), CSV (a zip), PDF. Cells that a spreadsheet would run as a formula are written as plain text.

## Slices

1. Arithmetic (tracker/paid_xchannel_math.py) and its tests.
2. Attribution settings and their comparison (tracker/paid_xchannel_settings.py).
3. Findings (tracker/paid_xchannel_findings.py).
4. The report: reading and assembling (tracker/paid_xchannel.py).
5. Downloads (tracker/paid_xchannel_export.py).
6. Routes in app.py and the assistant's knowledge.
7. The page.
8. Landing page, palette, hub, context file, rebuild prompt, memory.
