# Test & Experiment Tracker (Paid Marketing)

Page: `/p2/paid-marketing/experiment-tracker`. The 19th Paid Marketing agent.

## The ask

From the agent roster ("Test & Experiment Tracker"): know what is being tested in an account, whether each test has said anything yet, and what to do
about it. Most accounts run tests nobody is watching: an experiment left running for months, an ad copy test where one ad has quietly lost, a test
called a winner after two days.

## What it does

Pick a Google Ads account. The page reads, read only, two kinds of test and says for each one whether it has a result:

1. **Google Ads experiments** (Campaigns, Experiments): every experiment, its status, dates and arms (the control campaign and the trial campaign),
   and what each arm did between the experiment's start and yesterday.
2. **Ad copy tests**: every ad group that has two or more live ads is a test whether or not anyone planned it. The ads are held against each other
   on the last 30 days.

Each test gets a verdict and a plain next step. Nothing is changed in Google Ads.

## What is code and what is the model

Every figure and every verdict is arithmetic (`tracker/paid_exp_stats.py`). No model is asked whether a test won. A comparison is a two-sided
two-proportion z-test on one metric, and the result says which metric and how sure.

## Honesty rules

- **Too early is a verdict.** A test needs at least 7 days, and each arm needs enough to count: 100 clicks to compare conversion rates (and 30
  conversions across both arms), or 1,000 impressions to compare click rates. Below that the verdict is "too early" with what is still needed and,
  at the current pace, how many days that is. Nothing is called a winner on a small number.
- **Confidence is 95%** (p under 0.05). With several ads in one ad group each is held against the leader at a stricter bar (0.05 divided by the
  number of comparisons), so picking the best of five is not read as a win.
- **No difference is not a tie.** When a test has enough data and shows no gap, the page says so and says the smallest gap this much data could have
  told apart from chance, so a team does not read "no difference" as "they are the same".
- **The metric is stated.** Conversion rate (conversions per click) when there are enough conversions, otherwise click rate. Cost per conversion and
  conversion value are shown beside it and are never the thing that decides a verdict.
- **Conversions are Google's**, which may be fractional (data driven attribution) and may be counted late. A recent window is read as it stands.
- **A split that is not even is not a problem.** Google rotates ads to favour the one it expects to do better, so a lopsided split between ads is
  expected. A trial arm with a 50/50 split that got very different traffic is shown as a warning because it can mean the arms were not comparable.
- **What Google's API does not show is said.** Ad variations experiments have no campaign arms in the API, so they are listed with their status
  and dates and the page says their results are only in the Google Ads screen. Experiments that Google has already removed are not read.
- **A test only measures what changed.** Experiments are read for the days they ran; the verdict is about that period. A holiday inside the window
  moves both arms together and is not a difference.

## The verdicts

| Verdict | Meaning |
|---|---|
| Winner | The trial is ahead by more than chance (or this ad beats every other ad in its group) |
| Behind | The trial (or this ad) is worse by more than chance |
| No difference | Enough data, no gap that chance cannot explain |
| Too early | Not enough days or not enough clicks, impressions or conversions yet |
| No data | An arm has no impressions |
| Not measured | The API does not report this test's arms |

## Next steps the page gives

- A running experiment that is ahead: apply it in Google Ads, or end it. One that is behind: end it, it will not recover by waiting. One with no
  difference after four weeks: end it and keep whichever is simpler. One past its end date but still running: end or extend it. One ending within
  7 days: decide now.
- An ad group where one ad is behind every comparison: pause or replace the behind ads. A leader with a conclusive result: write the next
  challenger against it.
- An ad group with more than 4 live ads that is "no difference": too many ads to learn from, keep the best two or three.

## Meta Ads

The account can be a Google Ads account, a Meta ad account (act_...) or one of each, named as Account Audit names it ("<google id>", "act_..." or "<google id>+<act_...>", see `tracker/paid_accounts.py`).

`tracker/paid_exp_meta_read.py` reads two kinds of test on its own. Meta's A/B tests (ad studies): the test, its dates, its cells and what each cell's campaigns or ad sets did on every day of the test. Meta splits the
audience between the cells at random, so two cells are a fair test, held to the same arithmetic as a Google experiment: the first cell is the control and the second the trial, and a test of three or more cells
is compared on its first two. A study that is not an A/B test (a conversion lift or brand lift study) is listed without a result. And every ad set that runs two or more live ads over the last 30 days. Meta moves
delivery towards the ad it expects to do best, so the ads of one ad set do not share the audience evenly: that test reads what happened, which is a lead and not a fair test, and the page says so.

A Meta test is worded in terms of its cells (the winner is "kept" and the budget goes behind it; there is no "apply" in Meta). What Meta counts as a conversion depends on the campaign's objective, so a campaign that
counts no result is judged on click rate. `tracker/paid_exp_merge.py` joins the two reads; the report marks each test with its platform. One platform that will not answer is a warning and the report is the other's
alone; a platform lost to its allowance leaves a whole saved read to be served with its date; a Google and a Meta account are read together only when they bill in the same currency.

The Meta read was written from Meta's documentation and tested against a copy of the Graph API's answers. It could not be tried against a live account from the development machine, and how Meta describes an ad
study's cells is the least certain part: if Meta will not describe them the test is listed as not measured with the reason (and the page says "could not be read" with Meta's own message when a request is refused),
while the ad tests stand on their own.

## Files

`tracker/paid_exp_stats.py` (the test), `paid_exp_read.py` (Google reads), `paid_exp.py` (the report, cached like the other reports'),
`paid_exp_export.py` (Excel, PDF, CSV zip). The page is `templates/experiment_tracker.html`, `static/js/exp_data.js` (pure functions, run in Node by
the tests), `static/js/experiment_tracker.js` and `static/css/experiment_tracker.css`.

## Setting it up

Nothing is needed: it uses the Google Ads access the other Paid Marketing agents use.
