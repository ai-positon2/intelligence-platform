# Landing Page & Relevancy Check (Paid Marketing)

Page: `/p2/paid-marketing/landing-page-check`. The 18th Paid Marketing agent.

## The ask

From the agent roster ("Landing Page & Relevancy Check", Build & Launch): check that the keyword, the ad and the landing page say the same
thing, and look at page speed, mobile, forms and calls to action, to lift Quality Score and conversion rate.

## What it does

Pick a Google Ads account. The page reads, read only, the 30 days of what the account sent to each landing page and then fetches the 40 pages that
took the most money and holds each one against a written checklist.

From Google Ads, per landing page: spend, clicks, conversions, the mobile and desktop split, Google's own mobile speed score where it gives one; per ad
group that sends people there: its ads' headlines and descriptions, its keywords with their Quality Score parts (expected click rate, ad
relevance, landing page experience), and the search terms people really typed.

From the page itself (one plain GET from our servers, through the platform's guarded fetch): whether it opens, where it ends up, what the title and
headings say, the words on it, its forms and their fields, its calls to action, phone numbers and whether they can be tapped, whether it is
built for a phone, how fast the server answers, how heavy the HTML is, what slows the first paint, and which tracking tags it carries.

Every check has an id, a severity (critical, high, medium, low), a result (pass, warn, fail, not applicable, could not check), what was
found with its numbers, why it matters and what to do. A page gets a score and a grade, the account gets a spend-weighted score, and the report
lists the fixes to make first with the money that goes to each page.

## What is code and what is the model

Every check is arithmetic on what Google and the page returned. The model is not asked to find problems. An optional "Read it like a person"
step has Claude judge, for the biggest pages only, whether the page keeps the promise the ad makes, and suggest edits. It is shown public ad
copy, keywords and the page's own visible text, treats all of it as data, and its answer never changes a score.

## Honesty rules

- A page that refuses automated requests (401, 403, 429) is **not verified**, never called broken; its page checks say "could not check".
- A page whose words are put there by scripts (very little text in the HTML) is judged on what a plain read can see only where that is
  fair: the match checks say "could not check" rather than failing it.
- A tag that a consent banner or tag manager loads later is not seen, so a missing tag is a warning that says what was seen.
- The lab speed test (PageSpeed Insights) needs `PAGESPEED_API_KEY`; without it the page says the test is not connected and shows the
  measured server time, page weight and Google's own speed score instead. Nothing is invented.
- A page is its host and path, plus the few parameters that choose which page a site shows (a location, a product, a post; the list is `ROUTING` in
  `tracker/paid_lp_read.py`). Tracking tags (utm_, gclid, fbclid, ValueTrack) never make another page, and neither do parameters that only vary one page (variant,
  currency, sort, page number, search words). www, http against https, a default port, a trailing slash and a fragment do not make another page either.
- A {placeholder} in a query parameter is left out of the address that is fetched, never replaced by an invented value. A {placeholder} in the host or path
  (`{_domain}`, `{_page}`) is filled in by Google at click time, so the page people reach is not known: it is not fetched, its checks say "could not check" and its
  spend counts as not verified. It is never called broken.
- An ad address that uses plain http and is sent on to https is a warning (a redirect hop), and a move between http and https, the apex and www, or to another path
  counts as a redirect.
- "Looks like a test site" means a dev, test, qa, preview or stage subdomain, a staging, uat or sandbox name, a private address or localhost. A business whose own name
  holds one of those words (test-prep.com) is not one.
- A server error (HTTP 500 to 599) or a timeout is asked again once, half a second later, before it is held against the page. A bot check (Cloudflare's
  `cf-mitigated: challenge`) is "not verified", never a broken page.
- A page that answers 200 but whose title or main headline says it is gone ("Page not found", "404", "no longer available") is a warning, not a pass.
- Shopping and Performance Max send traffic with no keywords or ad text to compare, so the match checks are not applicable there; every other check still runs.

## The checklist (6 sections)

1. **The page opens**: answers with 200, does not bounce a deep link to the home page, keeps the click id and UTM through redirects, https, not a
   staging site, not closed to AdsBot.
2. **Does it match the ad**: the keywords (by spend) are on the page and in its headline; the ad's headline is echoed by the page's; the search terms
   people typed are on the page; offers and prices in the ads appear on the page; one clear H1; not one catch-all page for many ad groups;
   Google's landing page experience and ad relevance ratings.
3. **Can a visitor act**: a way to convert at all; a call to action near the top; forms not too long; a phone number that can be tapped.
4. **Works on a phone**: a mobile viewport, zoom not blocked, mobile against desktop conversion rate, Google's mobile speed score.
5. **Loads fast**: server response time, HTML weight, compression, render-blocking scripts, script count, images that shift the page, the lab score.
6. **Tracking and trust**: a Google tag or tag manager seen, a privacy link, contact details, proof (reviews, awards, ratings).

## Meta Ads

The account can be a Google Ads account, a Meta ad account (act_...) or one of each, named as Account Audit names it ("<google id>", "act_..." or "<google id>+<act_...>"). A page both
platforms send people to is one page: fetched once, its spend, clicks, results and device split added, and held to the checks of every platform that sends people to it.

`tracker/paid_lp_meta_read.py` reads each ad's creative (the address it sends people to and its headline, primary text and description), what each ad spent and took, the split by device,
Meta's own quality, engagement and conversion rate ratings and where an ad set sends people when its ads carry no web address. A Meta ad set stands where a Google ad group stands. Meta has no
keywords and no search terms, so a Meta page is held to what its ads say, to Meta's ratings and to what Meta's pixel needs. Spend that reaches no web page of the client's (an instant form,
a message, a call, an app, a post whose link the ad record does not carry, an ad no longer in the account) is counted and named, never guessed at. `tracker/paid_lp_merge.py` joins the two reads.

Four checks are Meta's own (Meta's click id fbclid survives a redirect, the Meta pixel is on the page, Meta rates the conversion rate and the ad quality average or better). Google's keyword,
Quality Score, tag, AdsBot and speed score checks run only for a page Google's ads reach. A Google page is held to 35 checks, a Meta page to 30 and a page both reach to all 39. The Meta
pixel is looked for in the page's HTML only: a pixel added later by a script or a tag manager, and the Conversions API, cannot be seen from a page, and the check says so.

One platform that will not answer is a warning and the report is the other platform's alone. A platform lost to its allowance leaves a whole saved read to be served with its date. A Google and
a Meta account are read together only when they bill in the same currency, because their spend is added. What Meta counts as a result depends on the campaign's objective, so a page that
only traffic or awareness campaigns reach shows no conversions. The Meta read was written from Meta's documentation and tested against a copy of the Graph API's answers; it has not been run
against a live account from the development machine, so a field Meta names differently shows up as a warning on the page ("could not be read") with Meta's own message, not as a gap.

## Scoring

Same arithmetic as the Account Audit: a pass earns a check's weight (critical 8, high 4, medium 2, low 1), a warning half, a failure
nothing; not applicable and could not check are left out and counted. The grade is held at C or below when a critical check failed or
30 percent or more of the checks could not be read. The account score weights each page's score by what the page spent.

## Files

`tracker/paid_lp_read.py` (Google reads), `paid_lp_meta_read.py` (Meta reads), `paid_lp_merge.py` (the two together), `paid_lp_page.py` (fetch and parse a page), `paid_lp_match.py` (words, keywords, offers),
`paid_lp_model.py` (a finding and a score), `paid_lp_checks.py` (the checklist), `paid_lp.py` (the report, cached like the audit's),
`paid_lp_export.py` (Excel, PDF, CSV zip), `paid_lp_ai.py` (the optional model read), `paid_lp_speed.py` (the optional lab test), `paid_lp_docs.py` (the checklist as a document,
`docs/landing-page-check-checks.md`, held equal to the catalog by a test). The page is `templates/landing_page_check.html`, `static/js/lp_data.js` (pure functions, run in Node by the tests),
`static/js/landing_page_check.js` and `static/css/landing_page_check.css`.

## Setting it up

Nothing is needed for the report itself: it uses the Google Ads access the other Paid Marketing agents use. Two optional steps need a key on the server:
`PAGESPEED_API_KEY` (a Google Cloud API key with the PageSpeed Insights API enabled) turns on the lab speed test, and `ANTHROPIC_API_KEY` (already set) is what Read it like a person uses.
