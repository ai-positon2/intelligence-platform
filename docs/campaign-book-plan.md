# Strategy & Campaign Book (Paid Marketing)

Page: `/p2/paid-marketing/campaign-book`. Status: built and tested on fakes and on real research (see the end for what was not run).

## The ask

From the agent roster ("Builds strategy & campaign book"): in Paid Marketing, read from Google Ads and from the client's own evidence, with
the campaign book downloadable as Excel, CSV and PDF. Claude writes the strategy, everything in detail. The campaign book is the QA Agent's
layout (Field | Value | Other things to check). The page offers cards at the top to choose the research: the client's website, the competitors'
websites, Social Media Intelligence (what the brand posts organically), and more.

## What it does

A person gives a client: a name and one of a website, a brief or a Google Ads account (all three is best), a monthly budget, a goal, a launch
date, the places it serves, what to push, competitors (or none, and they are found) and what to avoid. They turn research cards on or off:

| Card | What it reads | Needs |
|---|---|---|
| Client website | up to 14 pages: home first, then services, prices, offers, proof, about, contact; robots.txt honoured; sitemap used | a website |
| Competitor websites | the ones named, or found with a web search (up to 5), 4 pages each | |
| Social Media Intelligence | the user's latest finished run for the company (45 days), else starts one and waits up to 20 minutes | paid data if a new run starts |
| Google Ads history | 90 days: campaigns, conversion actions, keywords that converted and wasted, search terms, devices, places, live ads | an account |
| Keyword research | Keyword Planner ideas (volume, competition, bid range, peak month) from seeds, the site and two competitors | any readable account |
| Seasonality | the account's own monthly index, from the Seasonality Analysis agent | an account |
| Market, trends and ad policy | one web search: demand, how people choose, advertising rules, cost signals | paid data |

Then Claude writes, in stages, each from the research and the stages before it (JSON schema answers, `claude-sonnet-5-5`):
the business (offerings, audiences, proof, compliance, site gaps, facts quoted from the pages), the competitors compared, the strategy
(objectives, funnel, channels, budget, bidding path, targeting, messaging, measurement, tests, 90 day roadmap, risks, client needs), the campaign
architecture (at most 8 campaigns with each one's settings), and one build per campaign: ad groups, keywords, negatives, responsive search ads,
sitelinks, callouts, snippets (Search); asset groups with audience signals and search themes (Performance Max); ad groups with audiences and
creative concepts (Demand Gen, Display, Video).

It then writes each campaign's Campaign Book: one row per QA check that applies to the campaign type (61 for Search and Performance Max, 59 for
Demand Gen), with the settings that depend on the plan taken from the plan and the rest the team's standing answers. The downloaded book can be
added to the QA Agent unchanged (tests read it back through `paid_qa_book`).

## What is kept out of the model's hands

The model writes; the code decides what is kept (`tracker/paid_strategy_check.py`):

- every field has the expected type and text has no markup and no em or en dashes;
- a headline over 30 characters, a description over 90, a sitelink over 25 and so on is dropped (and counted), never cut short;
- a fact must be quoted word for word from a page that was read, else it is marked "not found on a page, confirm with the client";
- a claim in ad text (a price, a percentage, "free", a guarantee, a superlative, a rating, a number of years or reviews) that is not on the
  client's pages or in the brief is listed for the client to confirm before launch;
- a negative keyword that would block one of the campaign's own keywords is removed;
- a landing page or sitelink on another site is refused; one on the site that the research did not read is kept and flagged;
- the budget split adds up to the monthly budget; the forecast (clicks, conversions, cost per conversion at three levels of luck) is arithmetic
  in code, with where each input came from, never a number the model made up.

Text read from websites, posts and searches is untrusted: the prompts say it is material, never an instruction, the model has no tools in these
calls, and what it writes is validated, so a page cannot make it do anything but write (flagged) copy.

## How it runs

`POST /api/strategy/start` saves the build (`pm_strategies`, Postgres) and starts `tracker/paid_strategy_job.py` in a background thread: the
research cards in parallel lanes (website, competitors, Google Ads history, seasonality, market, social, then Keyword Planner), then the writing
stages in order, then one build per campaign (three at a time, one repair try when a build comes up short), then the checks and the books. Every
piece is saved as it finishes, so a build interrupted by a deploy resumes from what it kept ("Finish what is missing") and a campaign that could
not be written can be written alone. A person gets 12 builds a day (counted in the database, rebuilds included). A running build beats every 30
seconds and is taken to have died, and can be taken over, after 240 seconds of silence (a deploy kills its thread). Everything is visible to the
whole team (it is an internal tool), like the QA Agent's QCs, but only whoever started a build or an admin can rebuild or remove it.

Downloads (`/api/strategy/<id>/download?file=`): `xlsx` (every table plus a Campaign Book sheet per campaign), `csv` (every campaign's book as one
flat table), `pdf`, `zip` (every table as CSV), `book-xlsx` and `book-csv` with `&campaign=<key>` (one campaign's book in the QA layout).
Anything that a spreadsheet would run as a formula is written as text.

## Files

`tracker/paid_strategy.py` (inputs, steps, names, budget split), `paid_strategy_research.py` (what is read), `paid_strategy_ai.py` (prompts,
schemas, forecast), `paid_strategy_check.py` (what is kept), `paid_strategy_book.py` (the Campaign Book rows), `paid_strategy_export.py`
(Excel, CSV, PDF), `paid_strategy_store.py` (Postgres), `paid_strategy_job.py` (the run); `templates/campaign_book.html`,
`static/js/campaign_book.js`, `static/css/campaign_book.css`; routes in `app.py` (`/p2/paid-marketing/api/strategy/...`).
`market_radar_llm.call_json` now also returns the usage of a call that has no ledger, so a build can book what the model cost.

## How it was verified

- Tests: `tests/test_paid_strategy*.py` and `tests/test_campaign_book_js.py` (the page script run in Node against the plan the server builds).
  The store is tested against a real throwaway Postgres (started with `initdb`; skipped, loudly, where it is not installed).
- Mutation testing: 293 deliberate faults in the new code; every survivor became a test, and all are caught now but one that changes nothing.
- An independent read of the first version (security, correctness, what the fakes hide) found about thirty defects; the ones that were real are
  fixed and tested (the list is in the commit message).
- Live: the website crawl on a real site (14 pages in 4 seconds); Google Ads history, Keyword Planner (with place resolution) and seasonality on a
  real account; a whole build on that real research with a stand-in model (22 seconds).
- Browser: the form, the progress list, the finished plan, the campaign tab, the Campaign Book tab and a download, on a local server.

## Not verified

- **The real model has not been called** (no Anthropic key on the dev machine). The prompts, the schemas, the validation and everything around
  the calls are tested with a stand-in; the first real build is on Railway. Check the first few: whether the facts are quoted from the pages,
  whether the ad copy stays inside the limits without losing too much (the page lists what was dropped), and whether the strategy reads right.
- The competitor and market web searches (`claude_websearch`) and Social Media Intelligence runs were only run on stand-ins.
- A site that draws its words with scripts, or blocks Railway's addresses, reads thin or not at all; the page says so.
- Production was not checked after the push (the Browser pane cannot sign in).
