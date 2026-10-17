# Keyword Research (Paid Marketing)

Page: `/p2/paid-marketing/keyword-research`. Status: built and tested on stand-ins and on a real Google Ads account, a real site and real Keyword Planner (see the end for what was not run).

## The ask

From the agent roster ("Keyword Research", category Research & Audit, Google, cadence: at launch, then monthly; it absorbs "New Keywords Research"): find new
keyword opportunities from the business, search trends and competitor data, to expand reach and coverage.

## What it does

A person gives a client: a name and at least one of what it sells, a website or a Google Ads account, plus where it serves customers, competitors (up to three),
what to stay away from and the smallest monthly search volume worth looking at. They turn research cards on or off:

| Card | What it reads | Needs |
|---|---|---|
| Business Research of this client | the latest finished Business Research for the same client: what it sells, who buys, what they search, the voice and the ad rules | one to exist |
| Client website | the pages (the shared reader): headings and titles become seeds | a website |
| Account keywords and search terms | every keyword and negative keyword the account has (and its shared negative lists), 90 days of search terms | an account |
| Competitors | up to three competitor websites (named, or found by a web search): each one's address is a Keyword Planner seed | |

Keyword Planner is always asked (it is the point): a request per service the client sells (the phrase and its other number, because Planner sometimes answers
`insulin pump` with nothing and `insulin pumps` with hundreds of ideas), one for the site, one per competitor site, one for what already converts, and one
for the volumes of the converting terms. Planner returns monthly searches for the last twelve months, competition and top of page bids. Searches are counted in
the places the client serves, as Google finds them (no country is forced on a place name); if none is found, in the whole country of the account's currency, else
the United States, and the result says which.

Claude then does three things, each a JSON schema answer checked by code before it is kept:

1. **Themes**: groups the market into a few buyer needs, tied to the account's existing campaigns where one fits.
2. **Judge**: for every shortlisted keyword (about 480, in chunks of 120, each keyword given as an object): fit (yes, maybe, no), intent, theme, match type,
   priority and a short note. Keywords it leaves out of its answers are asked about once more.
3. **Plan**: the read in a few lines, quick wins, negatives to add, tests, questions for the client and risks.

## What is code, not the model

- **Every number** (searches, competition, bids, the 12 month shape, peak month, trend, coverage) is Keyword Planner's, copied or added up by code. The model is
  told never to write one; a number in its text that it was not given (small whole numbers and years excepted) is listed in the Checks for a person to look at.
- **Already in the account**: a keyword idea is "bidding" when the account has that keyword in any match type, "covered" when an account phrase or broad keyword
  would already match it, "new" otherwise. Matching uses the same word stems on both sides, so plurals do not hide a duplicate. The account's keywords are filed by
  their rarest word, so an account with twenty thousand keywords that all begin with "dental" is still quick to compare.
- **Blocked by the account's negatives**: an idea a negative keyword (in a campaign or a shared list) blocks in every campaign the account bids in is not
  offered. One blocked in only some campaigns is kept and says so; and it is not put in the Editor file for a campaign that blocks it.
- **Brand and competitor**: a keyword that holds the client's name or domain is brand, one that holds a named competitor's name or domain is competitor
  (stems, "and" for "&", words written together, accents and other scripts). A name or a domain that is only a word of the trade (dentist.com) marks nothing. Code
  sets this whatever the model said. Competitor keywords are listed but never put in the Ads Editor file (whether to bid on a rival is the client's decision).
- **Negatives**: a negative the model proposes is dropped when it would block any keyword recommended or the account bids on, or when an existing negative
  already blocks everything it would (an existing exact `[jobs]` covers nothing a new phrase `"jobs"` would).
- **Coverage**: the headline is the share of the monthly searches, over every kept idea that shares a word with what the client sells, that the account already bids
  on or covers. It is not taken from the judged sample (those are the biggest new ideas and a few the account has, so their split says nothing about the market).
  The share among the keywords judged a fit is shown by theme and labelled as such.
- **Seasonality**: each theme's twelve months added up, the peak month and how far above average it is, rising when the last three months are a quarter above the
  first three and falling when they are a fifth below. One year of history, so a lead and not a rule.
- **When the account could not be read**: every idea would be called new, so nothing is recommended for adding and no Editor file is made, and the build reads the
  account again when it is carried on.

## Downloads

Excel (a sheet per table), a zip of every table as CSV, PDF, and a CSV for Google Ads Editor (Campaign, Ad group, Keyword, Criterion type, Status "Paused") holding
only keywords judged a fit, new to the account (or a converting search term a broader keyword already matches), not a competitor's, and with a spelling Google Ads
takes. The Editor file is a draft to review: nothing is changed in Google Ads.

## How it runs

It is a Strategy & Campaign Book build with its own research and writing stages: `paid_keywords_job.KeywordBuild` subclasses `paid_strategy_job.Build`
(background thread, heartbeat, resume, cost booking) and its builds live in `pm_strategies`, told apart by `inputs.kind` ("keywords"). 12 builds a day per
person across the three agents. Only whoever started a build, or an admin, can build it again or remove it. The judge stage saves each chunk as it finishes (under
a lock, so two chunks finishing together cannot leave an older list as the last write), so a resume redoes only the chunks that were not done. The account is read
again on a resume if its keywords or negatives could not be read, and Keyword Planner if some request did not answer.

## Files

`tracker/paid_keywords.py` (inputs, cards, steps), `paid_keywords_data.py` (the arithmetic), `paid_keywords_research.py` (a finished Business Research, the account,
Keyword Planner), `paid_keywords_ai.py` (the questions), `paid_keywords_check.py` (what is kept), `paid_keywords_export.py` (the downloads),
`paid_keywords_job.py` (the run); `templates/keyword_research.html`, `static/js/keyword_research.js`, `static/css/keyword_research.css` (it shares `campaign_book.css`);
routes in `app.py` (`/p2/paid-marketing/api/keywords/...`). `paid_sqr_data.read` now also says, in flags, what could not be read and what was cut.

## Not verified

- **The real model has not been called** (no Anthropic key on the dev machine). The prompts, schemas, validation and everything around the calls are tested with a
  stand-in that judges by rules. How well Claude judges fit and intent on a real market is not known.
- Keyword Planner volumes are Google's estimates (rounded) for the last twelve months; an account with no ad spend may see broader figures.
- A site that draws its words with scripts, or blocks Railway's addresses, reads thin or not at all; the page says so.
- Shared with the other two agents and not changed here: the daily limit is checked before a run is counted, so several requests sent at the same moment can pass
  it; and removing a build that is running only hides it, the run finishes.
