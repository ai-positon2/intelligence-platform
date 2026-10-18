# Ad Copy & Creative Generator (Paid Marketing Agents, agent 20)

Status: built 2026-10-18. Page `/p2/paid-marketing/ad-copy`. Kind `adcopy` in the shared build table (`pm_strategies`), like Business Research and Keyword Research.

## What it does

Gives a PPC manager ad copy that is ready to paste, for the formats they pick, and creative briefs for the formats that need an image or a video:

| Format | Text it writes | Brief |
|---|---|---|
| Google Search, Responsive Search Ad | 15 headlines (30), 4 descriptions (90), two paths (15) | no |
| Google Performance Max asset group | 15 headlines (30), 5 long headlines (90), 5 descriptions (90), one short description (60), call to action | no |
| Google Demand Gen | 5 headlines (40), 5 descriptions (90), call to action | yes |
| Google Responsive Display | 5 short headlines (30), 1 long headline (90), 5 descriptions (90), call to action | yes |
| Meta feed | primary texts (125), headlines (40), link descriptions (30), call to action | yes |
| Meta Stories and Reels | short primary texts (125), headlines (40), call to action | yes (a script, hook first) |

Counts are the most it asks for; the minimum it must keep is in `FORMATS` (`tracker/paid_adcopy.py`).

## Where the facts come from (nothing is invented)

1. The client's website, read through the platform's SSRF-guarded reader (`paid_strategy_research.read_website`).
2. The facts the person typed in "Facts you have confirmed with the client" (prices, offers, years in business, credentials). These count as verified.
3. Optionally, a Google Ads account: the keywords and the best existing responsive search ads, so new copy does not repeat what already runs and uses the words that already earn clicks (`paid_adstrength.report_for`).

A claim in the copy (a number, a price, "free", a guarantee, a superlative, "same day", a health claim) that is on none of those is **not dropped but marked "Confirm"**: it is listed with the exact claim, and the file for Google Ads Editor leaves it out until it is confirmed. Over-length text is dropped, never cut.

## Checks (all code, `tracker/paid_adcopy_check.py`; the model is never trusted for limits)

- length per field, deduplication (same words, or the same words reordered), Start Case for Google headlines (the team's standard), no em or en dashes;
- Google editorial rules: no emoji, no exclamation mark in a headline (one at most in a description), no ALL CAPS words, no repeated punctuation, no phone numbers or web addresses in the text;
- no competitor names, nothing on the avoid list;
- Meta policy hints: the copy must not tell the viewer something about their personal attributes ("Are you diabetic?"), and health or finance wording is flagged for a person to read;
- a format that ends with fewer items than its minimum asks the model once more with the exact shortfall, then says so;
- keyword coverage for Google formats: which of the keywords given appear in at least one headline.

## Downloads

Excel (one sheet per format, the briefs, the checks, the facts used), CSV (a zip of the same tables), PDF (to read and send), and a CSV for Google Ads Editor (responsive search ads only, Paused, "Confirm" items left out).

## What it does not do

It does not create or change anything in Google Ads or Meta, and it does not make images or video: a brief says what to make and lists the sizes. Meta lengths are Meta's recommended lengths as published (Meta truncates after them), held as constants in `FORMATS` so a change is one edit. Image and video sizes are the published ones as of this build and should be checked against the platform before a brief goes to a designer.

## Files

`tracker/paid_adcopy.py` (formats, inputs, steps), `paid_adcopy_check.py` (what is kept), `paid_adcopy_ai.py` (the prompts and the schema), `paid_adcopy_research.py` (the website and the account), `paid_adcopy_job.py` (the run), `paid_adcopy_export.py` (downloads); `templates/ad_copy.html`, `static/js/ad_copy.js`, `static/css/ad_copy.css`; routes in `app.py` under `/p2/paid-marketing/api/adcopy`.
