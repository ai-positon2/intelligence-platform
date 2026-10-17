# Business Research (Paid Marketing)

Page: `/p2/paid-marketing/business-research`. Status: built and tested on stand-ins and on a real site and a real Google Ads account (see the end for what was not run).

## The ask

From the agent roster ("Business Research", category Research & Audit, cadence: at onboarding, then quarterly): a clear picture of the client's business,
offerings, USPs and audience before any audit or strategy work. It is the first step of an engagement, so what it finds is what the Account Audit,
Keyword Research and Strategy & Campaign Book agents start from.

## What it does

A person gives a client: a name and one of a website, a brief or a Google Ads account, plus what the client wants from advertising, where it serves
customers, what it sells, competitors (up to three) and what to avoid. They turn research cards on or off:

| Card | What it reads | Needs |
|---|---|---|
| Client website | up to 14 pages (the Strategy & Campaign Book reader), plus what the pages carry for measuring and selling: analytics, tag manager, ad pixels, call tracking, CRM forms, booking, chat, consent banner, schema.org types, mobile viewport, https | a website |
| Reviews and reputation | one web search: ratings and counts on the review sites, what customers praise and complain about, what they ask, press, awards, who runs the business and how big it is | paid data |
| Social Media Intelligence | the user's latest finished run for the company (45 days), else starts one | paid data if a new run starts |
| Google Ads history | 90 days: what converted and what wasted, search terms, devices, places, conversion actions | an account |
| Competitors | up to three competitor websites, named or found by a web search | |
| Market and ad rules | one web search: demand, how people choose, advertising rules | paid data |

Claude then writes, in three stages, each from the evidence and the stage before:

1. **The business**: summary and snapshot, offerings (each with price on the page, its page, a priority for paid search and why), points of difference
   with the quote that proves each one and whether it is proven, claimed or generic, proof, voice, the rules that touch the ads, facts an ad may state, what the site gets wrong.
2. **Who buys and why**: audiences (situation, what they want, what worries them, what they search), the journey from search to customer, triggers, objections with the answer and the proof, how to write to them, places and seasons.
3. **What it means**: what to advertise first and what to hold back, angles and tests, the competitive read, the reputation read, the social read, what to check about tracking, the questions to put to the client grouped by area and urgency, risks, next steps.

It downloads as Excel (a sheet per table), PDF, a zip of every table as CSV, and the questions for the client as one CSV. The page can also copy the questions as plain text for an email.

## What is kept out of the model's hands

- Ratings, review counts, awards and press are kept as the search returned them (`paid_business_research.read_reputation`); the model reads them and may not change them.
- What a site carries is read from its tags by code (`paid_site_tech`) and every finding says "not seen", never "missing": a tag can load later, after a consent choice or from a tag manager.
- A point of difference is "proven" only when its quote is word for word on a page that was read or in what the reputation search found; otherwise it is shown as claimed.
- A fact an ad may state must be on the client's own pages (not just in a search result).
- Claims (a price, a percentage, "free", a rating, "best") in what could end up in an ad that are not on the client's pages, in the brief or in what the search found are listed for the client to confirm.
- A page the model names must be on the client's own site; research that was not done is never written about (no competitor that was not read, no reputation that was not searched, no social finding without a run).
- No em or en dashes anywhere.

## How it runs

It is a Strategy & Campaign Book build with other research and other writing stages: `paid_business_job.BusinessBuild` subclasses `paid_strategy_job.Build`
(same background thread, heartbeat, resume, cost booking), and its builds live in the same Postgres table (`pm_strategies`), told apart by `inputs.kind`
("business"). A person gets 12 builds a day across both agents. Only whoever started a build, or an admin, can build it again or remove it. Each agent's routes
treat the other agent's builds as not found.

## Files

`tracker/paid_business.py` (inputs, cards, steps), `paid_business_research.py` (reputation search, website with tracking), `paid_site_tech.py` (tags),
`paid_business_ai.py` (the three questions), `paid_business_check.py` (what is kept), `paid_business_export.py` (Excel, CSV, PDF), `paid_business_job.py` (the run);
`templates/business_research.html`, `static/js/business_research.js`, `static/css/business_research.css` (it shares `campaign_book.css`); routes in `app.py`
(`/p2/paid-marketing/api/business/...`).

## Not verified

- **The real model has not been called** (no Anthropic key on the dev machine). The prompts, schemas, validation and everything around the calls are tested with a stand-in.
- The reputation search and a new Social Media Intelligence run were only run on stand-ins.
- A site that draws its words with scripts, or blocks Railway's addresses, reads thin or not at all; the page says so. Tags that a consent banner loads later are not seen.
