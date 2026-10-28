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

1. **The competitors** are added by company name only, as in Social Media Intelligence (2026-10-27 rework). The page searches the name (`tracker/paid_compete_search.py`, the same Apollo company search),
   lists the companies with a similar name, and the team picks the ones it means, one at a time, up to eight. A website typed instead of a name is offered as a company of its own. A pick is saved at once for
   that ad account only (`tracker/paid_compete_settings.py` `change_account`, the monitors' store, `settings:compete`; a client with a Google and a Meta account gets the change on both, each on its own
   list). The website comes from the company record (Google is searched by it); the Facebook page (Meta is searched by it) comes from the record when it has one, else one Claude web search finds the
   company's official page and it is kept only when the answer is sure (high or medium); otherwise the card says so and the team pastes the page (typed pages are never searched over). The Master Agent can
   hold the same list for a client (`shared.competitors`); an account's own saved list wins, whole, and the first change starts from the master's list so nothing is lost.
2. **Refresh** reads each competitor on each platform that has something to look up (`tracker/paid_compete_fetch.py`): a background job, three reads at a time, up to 400 ads per Meta read and 150 per Google
   read (measured 2026-10-27: Tandem showed 94 active Meta ads (96 s) and 126 Google creatives (23 minutes, because the text of each is read from its picture); Insulet showed 307 Meta ads (193 s); the limit was
   30 before and hid most of them). A read capped at a lower limit than today's is read again at the next refresh. A read that reaches its limit is marked capped ("they run more than were read") and a run stopped at its time limit keeps the ads it saved as a partial
   read, read again at the next refresh and never replacing a fuller read. Progress is kept in the store and followed by the page for about 40 minutes. It is never automatic because it costs money (about $1 for five competitors on both platforms, and the page says the most a refresh can cost before it is pressed). What was
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

## The kinds of ads, and ideas written by Claude (2026-10-28)

Asked by Divith: "can we get better suggestions, from seeing the different types of ads they are running vs what we are running". Spec approved the same day.

* **Why Meta said "Only 0 lines"**: the words of a platform cannot be compared when no competitor ad with words was read there. The page now says why, competitor by competitor (`paid_compete_math.py`
  `thin_reason`): no Facebook page or website set, not read yet, a failed read, ads that are only pictures or videos, or no active ads.
* **Our own kinds of ads** (`tracker/paid_compete_own.py`), read in small reads of their own and kept for the day (`paid_geo._cached`, kind `compete-own`), because the Ad Copy Checker's read keeps
  text only. Google: each enabled ad's type (`ad_group_ad.ad.type`, text, image or video) and each enabled Performance Max asset group's pictures and videos (`asset_group_asset.field_type`; an
  asset group counts for every format it carries). Meta: each active ad's creative (format: dynamic, catalog, carousel, video or image; its button), its ad set's placements (none chosen means
  automatic placements, which count everywhere) and the day it was made. Both Google queries were run against two real accounts on 2026-10-28 (18 and 37 ads, 164 and 75 asset rows). The Meta
  fields were not run from here (no Meta token on this machine): they are in the admin live query check (`agents=compete`).
* **The comparison** (`tracker/paid_compete_mix.py`): format on both platforms, button and placement on Meta, over every ad read of theirs (pictures and videos with no words included). A gap is
  as strict as a gap in the words: their share 25 points above ours, in 3 of their ads, in two competitors when two were read, from 8 of their ads and 3 of ours. Also: which of their formats
  run longest (median days), and how fresh each side is (share started in the last 30 days; ours on Meta only, Google gives no start day). Gaps become ideas with a thing to try.
* **Ideas written by Claude** (`tracker/paid_compete_brief.py`, `/api/compete/brief`): on a button press (a few cents, never automatic), Claude Opus 5.5 is shown the gaps, a sample of our copy
  and their 10 longest-running ads (the 2 longest of each format first) with the pictures of up to 8, and writes 3 to 6 ideas (format, placement, hook, first line, why). Each must name the
  competitor ads it learned from; one that names none it was shown is dropped. It is told not to invent facts about the client (placeholders such as [your offer]). The answer is kept per
  account until the competitors or our ads are read again (then it is marked old). One ask at a time per account. A refusal is retried on a fallback model by the API.

## What it says it could not measure

How the competitors' ads perform; our own formats and calls to action (only text is read); competitors' ads that have stopped; the keywords they bid on; LinkedIn, TikTok and Microsoft. A competitor with no
Facebook page, a read that failed, and a read that is old are named in the report, never counted as nothing.

## Not built, on purpose

* **A model's written creative brief.** The ideas are rules with evidence; a model could turn them into copy, but nothing here could verify its claims.
* **New competitor ad alerts** through the Alert Hub: possible later, since each read has the ads' first-shown days.
* **Spend and reach of competitors:** Meta publishes them for the EU only and Google for the EU only.
* **How fresh our own Google ads are**: Google gives no start day for an ad.
