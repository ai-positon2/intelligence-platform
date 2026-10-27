# Competitor Analysis plan

Sheet item #39 ("Competitor Analysis", owner Divith, "New - not built"). The sheet says: "Look at competitor ads on platforms like Google Ads, Meta etc. Compare the same with the ads running
currently in our accounts. And give us ideas to improve the ads and creatives." Section: Paid Marketing Agents (`/p2/paid-marketing/competitor-analysis`). Built 2026-10-27 at Krishna's request,
the last of "the remaining 6".

## Where competitor ads come from

Neither platform lets an advertiser read other advertisers' ads through its own account, so the two public libraries are read:

| Platform | Library | How it is read |
|---|---|---|
| Meta | Meta Ad Library, by the competitor's Facebook page: its active ads in every country | Apify actor `apify/facebook-ads-scraper` (official, $0.005 an ad) |
| Google | Ads Transparency Center, by the competitor's website (every ad that sends people to it) or its advertiser id, for one country | Apify actor `silva95gustavo/google-ads-scraper` with `ocr: true` (about $0.0016 an ad) |

Meta's own Ad Library API was not used: for ads that are not political or about social issues it answers only for the EU, so it shows nothing for a US advertiser. Apify is already paid for and
used by the platform (`tracker/apify_transport.py`). Either actor can be replaced by an environment setting (`CA_APIFY_META_ACTOR_ID`, `CA_APIFY_GOOGLE_ACTOR_ID`) but the replacement must give the
same shape.

**Both actors were run for real on 2026-10-27 (three ads each, Tandem Diabetes) before any code was written**, and the tests hold the normalisers to that output. What the runs showed:

* Meta gives the full ad: headline, text, link, call to action, format, platforms, start and end days, whether it is active.
* Google gives a text ad as a picture. The headline is exact; the description exists only because OCR reads it from the picture, with mistakes ("el www.tandemdiabetes.com/" in front, stray fragments
  such as "il", "og"). The normaliser tidies what it can and the page says the rest is approximate. How long a creative has run (`numServedDays`) is exact.
* Without `ocr: true` Google's rows hold no text at all, so it is always on.

## What it does

1. **The competitors** are written down per account (`tracker/paid_compete_settings.py`, the monitors' store, `settings:compete`): a name, a website, a Facebook page and optionally a Google advertiser
   id. Up to eight. The Master Agent can hold the same list for a client (`shared.competitors`); an account's own saved list wins, whole.
2. **Refresh** reads each competitor on each platform that has something to look up (`tracker/paid_compete_fetch.py`): a background job, three reads at a time, 30 ads per read, progress kept in the store and
   followed by the page. It is never automatic because it costs money (about $1 for five competitors on both platforms, and the page says the most a refresh can cost before it is pressed). What was
   read is kept per competitor for seven days, shared by every account that names the same competitor, and a read newer than that is not paid for again. A failed read never replaces the last good one.
3. **Our own ads** are the Ad Copy Checker's saved read (`paid_copycheck.load_raw`): the text of the ads running now, so a report costs nothing in the ad account when that read is fresh. One Google account
   and/or one Meta account at a time; a group of accounts is refused because each client has its own competitors.
4. **The comparison** (`tracker/paid_compete_math.py`) is by line of copy, not by ad (one responsive search ad holds up to fifteen headlines, so by ad ours would always look richer), over thirteen messages
   found by plain rules: a price, free, urgency, proof, credentials, a guarantee, convenience, insurance, local, a question, something new, a figure, a comparison.
   * A message is a **gap** when the competitors use it at least 25 points more than we do, in at least 3 of their ads and, when two competitors were read, in two of them; it is high from 45 points when
     they use it in half their lines or more. Short of 8 lines of theirs or 6 of ours it says it could not judge.
   * What we say 25 points more than they do is shown as our **edge**.
   * The **offers** they state, the **longest-running** ads (the best public sign that an ad works) and the **newest** are listed.
   * The same ad of one competitor (Meta repeats it per placement) is counted once.
5. **Ideas** come from the gaps (each with its evidence, a thing to test and a real example line), the offers and the longest runners, worst first.

## What it says it could not measure

How the competitors' ads perform; our own formats and calls to action (only text is read); competitors' ads that have stopped; the keywords they bid on; LinkedIn, TikTok and Microsoft. A competitor with no
Facebook page, a read that failed, and a read that is old are named in the report, never counted as nothing.

## Not built, on purpose

* **A model's written creative brief.** The ideas are rules with evidence; a model could turn them into copy, but nothing here could verify its claims.
* **New competitor ad alerts** through the Alert Hub: possible later, since each read has the ads' first-shown days.
* **Spend and reach of competitors:** Meta publishes them for the EU only and Google for the EU only.
* **Our own ad formats and calls to action** in the comparison: the Ad Copy Checker's read has text only.
