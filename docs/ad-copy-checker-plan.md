# Ad Copy Checker plan

Sheet items #28 and #29 ("Ad Copy Checker Agent", the chat agent that writes a Google Sheet and emails the person who asked, and "Ad Copy Checker Claude", the automated bi-weekly agent that reads
every active account with Claude and Supermetrics). Purpose on the sheet: eliminate grammatical errors, misspellings and unwanted capitalisation in ad copy. The roadmap row says what to add:
phone number and location checks against the Client Master Config, and Meta ad copy. Section: Paid Marketing Agents (`/p2/paid-marketing/ad-copy-checker`). Owner on the sheet: Divith (Claude) and
Subbu (chat). Built here: Krishna's request, 2026-10-22.

## What it is

For a Google Ads account, every account of an MCC or a Meta ad account it reads the **text of the live ads** and points at the exact characters that are wrong. Plain rules, no model, so every
finding can be traced to the characters it marks. It also watches the accounts weekly and alerts the team to ads that go wrong after the first run.

This is not the Ad Copy & Creative Generator (`paid_adcopy*.py`), which writes copy and holds it to platform limits. The checker reads copy that is already live.

## What is read (read only)

Google Ads, per account: Search ads (responsive, and the old expanded kind, retried without the old fields if Google refuses them), then, soft, Display ads, the text assets of Performance Max asset
groups, and the sitelinks, callouts and structured snippets of live campaigns and of the account. Meta, per ad account: every active ad's primary text, headline and description, from the plain
creative, a link, video or photo post, a carousel card, or the lists of a flexible ad. Soft reads leave a kind out and say so. Nothing is changed in any account.

## The rules (tracker/paid_copycheck_rules.py)

| Rule | Level | What it finds |
| --- | --- | --- |
| placeholder | high | `{Brand}`, `[City]`, `<name>`, lorem ipsum, TBD (a real ad customizer or keyword insertion is read and left alone; its default text is checked) |
| phone | high | a US phone number that is not on the account's saved list (only when a list is saved) |
| garbled | medium | damaged characters (`â€™` for an apostrophe, a replacement character) |
| spelling | medium | a word the dictionary does not know that is one or two letters from one it does, with the correction |
| repeat | medium | the same word twice in a row |
| gap | medium | no space after a comma, colon or full stop, or a space before one |
| punct | medium | doubled punctuation (an ellipsis is allowed) |
| caps_line | medium | a whole headline or description in capitals |
| lower_start | medium | a line that starts with a small letter (unless the brand is written so) |
| place | medium | a place the team saved for another account |
| brand | medium or low | the brand written a way the team did not save (medium), or two ways in one account with nothing saved (low) |
| caps_word, odd_caps | low | one word in capitals, a capital inside a word |
| ending | low | descriptions of one ad that disagree on ending with a full stop, a headline that ends in a full stop or comma |
| style | low | headlines of one ad that mix Title Case and sentence case |
| vocab | low | an unknown word the account uses in three or more ads, spelt the same way: listed quietly, never alerted |

Words never doubted: the account's name and web address, its brand, the words the Brand Spend Alert saved as its brand, the team's allowed words (per account and for all), a short list of well known
brands. The dictionary is `pyspellchecker` (a soft dependency): without it the report says spelling was not checked and every other rule still runs.

## What the team saves (the stand-in for the Client Master Config)

Per account: phone numbers, places ("Quincy, MA"), the brand written the client's way, allowed words; for all accounts: allowed words. A "This is right" button on a finding adds a word. Kept in the
monitors' store (`settings:copycheck`) with who changed what and when. Phone and place checks only run against what is saved here, so they stay quiet until someone saves a list. When the Client Master
Config (sheet item #37) is built, this should read from it.

## The weekly run

On the shared monitor layer (`copycheck`, `paid_copycheck_monitor.py`). Mondays only (ad text changes slowly and the read is about five queries an account), with the other monitors' weekly summaries.
An ad with a high or medium finding is one issue (fingerprint `copycheck:<account>:<ad id>`); ads with only small things are counted in one quiet issue per account; the 200 worst ads of an account are
followed one by one and the rest counted in one issue. The first run is a baseline and sends nothing. An edited responsive ad is a new ad in Google, so the old one resolves and the new one is checked afresh.

## Not built / not verified

- The rules do not judge grammar or meaning. On request (`Read the grammar with Claude`, one account, `paid_copycheck_ai.py`) Claude reads the account's distinct lines of four words or more (240 at most, 80 to a call) for errors of grammar and wording; each error it names is kept only when its line number was one it was shown and its quoted excerpt is, character for character, in that line, and it is not part of the weekly run or the alerts. Capped at 30 reads a person a day. The real model has not been run from here.
- None of the new Google queries (ad text, Display, Performance Max text, campaign and account assets) nor the Meta creative fields was run against the live services from here.
- Locations are compared only with places the team saved for other accounts; the campaigns' geo targets are not read (the Location Conflicts agent, sheet item #31, is where that belongs).
- Shopping, Demand Gen, video and app ads are not read; Meta ad names and call-to-action buttons are not read.
- Reads Meta active ads only; paused ads are never checked.
