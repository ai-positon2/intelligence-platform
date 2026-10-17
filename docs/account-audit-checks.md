# Account Audit: every check

Generated from the catalog in `tracker/paid_audit_model.py` by `tracker/paid_audit_docs.py`; a test holds this file equal to it.
A check whose input could not be read says "could not check" and is never passed or failed. Severity weights: critical 8, high 4, medium 2, low 1.

## Google Ads (52 checks)

### Account and settings (7)

**G-SET-01** The account is active (critical)

Why it matters: A suspended, cancelled or closed account serves nothing, and a test account never reaches real people.

What to do: Find out why the account is not active in Google Ads (billing, a policy suspension, a closed account) and fix that before anything else.

**G-SET-02** Auto-tagging is on (high)

Why it matters: Auto-tagging adds the click id that links an ad click to a Google Analytics session and to an offline conversion. Without it, conversions imported from GA4 or a CRM cannot be matched to the click and reports lose the source.

What to do: Turn on auto-tagging in the account settings. If a tracking template is used as well, keep the click id parameter in it.

**G-SET-03** Conversion tracking has an owner (critical)

Why it matters: Smart Bidding, reports and the audit's own numbers all depend on conversions being recorded. An account with no conversion tracking setting bids blind.

What to do: Set up conversion tracking in the account (or link it to the manager account that owns it) before spending more.

**G-SET-04** A Google tag is installed (low)

Why it matters: The Google tag (AW id) on the site records conversions and builds remarketing lists. Without one the account can only learn from imports.

What to do: Install the Google tag on every page of the site, directly or through Tag Manager, and confirm it with the tag diagnostics.

**G-SET-05** Enhanced conversions for leads are on and allowed (medium)

Why it matters: Enhanced conversions match hashed form details to signed-in users, which recovers conversions a browser would otherwise lose. They only work after the customer data terms are accepted.

What to do: Turn on enhanced conversions for leads and accept the customer data terms, if the client's privacy rules allow it. Healthcare and other regulated clients often cannot send form details, so confirm with the client first.

**G-SET-06** Call reporting is on when calls are tracked (medium)

Why it matters: Calls from ads and from the site only record conversions when call reporting is on for the account.

What to do: Turn on call reporting and call conversion reporting in the account settings.

**G-SET-07** Google's optimization score is not low (low)

Why it matters: The optimization score is Google's own measure of how many of its recommendations the account has applied. It is a prompt to look, not a target to chase, and many recommendations do not suit every client.

What to do: Open the recommendations, apply the ones that fit the client's goals and dismiss the rest with a reason.

### Conversion tracking (11)

**G-CNV-01** There is an enabled primary conversion action (critical)

Why it matters: Bidding optimises towards the primary conversion actions. With none, Smart Bidding has nothing to learn from and reports show no results.

What to do: Create or import the conversion that matters to the client (a form, a call, a booking, a purchase) and mark it primary.

**G-CNV-02** Conversions were recorded in the last 30 days (critical)

Why it matters: An account that spends and records nothing is either not converting or not measuring. Either way the bids are being set without feedback.

What to do: Check the tag with Tag Assistant, submit a test conversion, and compare the platform's count with the client's own leads.

**G-CNV-03** Primary conversion actions that tag or import data are recording (high)

Why it matters: A primary action that has recorded nothing in 30 days is usually a tag that fell off the page, a form that changed, or an import that stopped. If it is the only primary action, bidding is running without data.

What to do: Open each action's tag status, test the form or page it counts, and fix or remove the action.

**G-CNV-04** Lead actions count one conversion per click (medium)

Why it matters: A form counted every time it is submitted makes one person who fills it in twice look like two leads, which inflates conversions and teaches Smart Bidding to chase repeat submitters.

What to do: Set the counting of lead actions to One per click (or per conversion) unless every submission is genuinely a separate lead.

**G-CNV-05** Purchase actions count every purchase (medium)

Why it matters: A purchase counted once per click misses repeat orders in the same visit, so revenue and return on ad spend read low.

What to do: Set the counting of purchase actions to Every.

**G-CNV-06** The same conversion is not counted by a tag and a GA4 import (high)

Why it matters: When a Google Ads tag and a GA4 import both count the same kind of event as primary, one lead can be counted twice and bids are set on inflated numbers.

What to do: Keep one source per conversion. Make one of the two secondary, or remove it.

**G-CNV-07** Value bidding has conversion values to work with (high)

Why it matters: Maximise conversion value and target ROAS optimise towards the value of each conversion. If every conversion has no value the strategy has nothing to optimise and performs like a guess.

What to do: Give each primary action a value (a fixed value per lead is enough) or move the campaigns to a conversion-count strategy.

**G-CNV-08** Primary actions use data-driven attribution (low)

Why it matters: Last-click credits only the final ad clicked. Data-driven attribution spreads credit across the clicks that led to the conversion, which suits Smart Bidding better.

What to do: Switch primary actions to data-driven attribution where Google offers it.

**G-CNV-09** Click lookback windows are long enough (low)

Why it matters: A window shorter than a month drops conversions of people who take time to decide, which matters for higher-consideration services.

What to do: Set the click-through window to 30 days or more for lead and purchase actions.

**G-CNV-10** Not too many primary conversion actions (low)

Why it matters: Every primary action is something bidding optimises towards. Many of them dilute the signal and make the conversion column hard to read.

What to do: Keep the few actions that mark a real lead or sale as primary and make the supporting ones (page views, clicks to call) secondary.

**G-CNV-11** Google Analytics 4 conversions are imported (medium)

Why it matters: Linking GA4 lets the account use the site's own events as conversions and audiences. Google's API does not show the link itself, so this can only be seen through the conversions imported from it.

What to do: In Google Ads open Linked accounts and check the GA4 link, then import the key events that matter as conversions.

### Links and audiences (3)

**G-LNK-01** Shopping campaigns have their Merchant Center linked (high)

Why it matters: A Shopping or retail Performance Max campaign serves from a Merchant Center product feed. Without the link, or with a link to a different Merchant Center than the one named in the campaign, it has no products to show.

What to do: Link the Merchant Center account in Google Ads (Linked accounts) and check each shopping campaign points at it.

**G-LNK-02** Audience lists are collecting members (low)

Why it matters: Remarketing and customer lists only help once they have people in them. Empty lists mean the tag or the upload is not feeding them.

What to do: Check that the Google tag fires on the pages that build each list and that customer lists are uploaded. Some regulated sectors may not be allowed to build remarketing lists, so confirm the client's policy.

**G-LNK-03** Audience lists are big enough to serve on Search (low)

Why it matters: Google only serves a list on Search once it has 1,000 members. A smaller list is collected but does nothing for Search targeting or bids.

What to do: Let the list grow, widen the rule that builds it, or use it for Display and YouTube, where the minimum is lower.

### Structure and bidding (12)

**G-STR-01** Every campaign targets locations (critical)

Why it matters: A campaign with no location target is eligible in every country and territory. The budget then spreads across places the client cannot serve.

What to do: Add the locations (or a radius around each site) to each campaign, and exclude the places the client does not serve.

**G-STR-02** Search campaigns do not run on the Display Network (high)

Why it matters: Search campaigns that also run on the Display Network spend on pages where people are not searching, at lower intent, and mix the numbers so the search results cannot be read cleanly.

What to do: Turn off the Display Network in each Search campaign's network settings. Run Display as its own campaign if it is wanted.

**G-STR-03** Search Partners are not costing more per conversion (low)

Why it matters: Search Partners are other sites that show Google search ads. Their traffic is often cheaper and weaker. If it costs clearly more per conversion it is worth turning off.

What to do: Compare cost per conversion on Google Search and on Search Partners in the network report, and turn Search Partners off where it is worse.

**G-STR-04** Campaigns that convert are not held back by budget (medium)

Why it matters: A campaign that runs out of budget while it is converting leaves conversions on the table, and its Smart Bidding learns from a partial day.

What to do: Raise the budget of the campaigns that convert at an acceptable cost, funded from the ones that do not.

**G-STR-05** No campaign is flagged by Google as misconfigured or limited by policy (high)

Why it matters: Google shows a campaign status and the reasons behind it. A misconfigured campaign, a missing ad group or a policy limit means the campaign is not serving as built.

What to do: Open each campaign's status in Google Ads, read the reason Google gives and fix it (an ad group without ads, a disapproved ad, a missing asset).

**G-STR-06** No campaign is still learning (low)

Why it matters: A campaign in its learning period reads high and low until the strategy settles, and changes made then restart the learning.

What to do: Leave campaigns that are learning alone for a couple of weeks, and avoid stacking bid, budget and conversion changes together.

**G-STR-07** Smart Bidding campaigns have enough conversions to learn from (high)

Why it matters: Smart Bidding sets each bid from past conversions. Google suggests about 30 conversions a month for a target CPA, 50 for a target ROAS and 15 for maximise conversions. With fewer, bids swing and targets are missed.

What to do: Merge thin campaigns so they share conversions, use a portfolio strategy, or count a nearer step such as a form start as a secondary signal.

**G-STR-08** Campaigns with plenty of conversions use Smart Bidding (medium)

Why it matters: Manual and click-based bidding cannot use the conversion signal. A campaign that already records conversions usually does better when bids are set from them.

What to do: Move the campaign to Maximise conversions (or a target CPA once it has run for a few weeks) and watch it for two weeks.

**G-STR-09** Campaigns that spend are converting (high)

Why it matters: A campaign with a lot of clicks and no conversions in a month is either not converting or not measuring, and it keeps spending.

What to do: Check the landing page and the conversion tracking for each, tighten the targeting, or pause the campaign and move its budget. If conversions are imported from a CRM or uploaded offline they arrive days or weeks after the click, so recent clicks may not have a conversion yet: look at the import's delay before pausing anything.

**G-STR-10** Search campaigns are not losing much reach to rank (medium)

Why it matters: Impression share lost to rank means the ad was not shown because its Ad Rank was too low: weak bids, Quality Score, or a weak ad or page.

What to do: Improve ad relevance and landing page experience first, then raise the target or bid where the campaign earns it.

**G-STR-11** The account is not buried in paused campaigns (low)

Why it matters: Hundreds of paused campaigns make the account slow to read and easy to enable by mistake.

What to do: Archive the campaigns that will not come back and keep their results in a report.

**G-STR-12** The account has enabled campaigns (high)

Why it matters: An account with no enabled campaign is not advertising. Most of the other checks have nothing to look at, so its score only reflects its settings.

What to do: Enable the campaigns that should be running, or audit again once the account is built.

### Keywords and search terms (10)

**G-KWD-01** Search campaigns use negative keywords (high)

Why it matters: Negative keywords stop the ads showing for searches that cannot convert (jobs, free, other cities, competitors). Without them a share of the budget goes to the wrong searches every day.

What to do: Add negative keyword lists for the account and for each theme from the search terms report. Account-level negatives cannot be read through Google's API, so check those in the account's settings before adding.

**G-KWD-02** Broad match keywords run under Smart Bidding (high)

Why it matters: Broad match reaches a wide range of searches. Smart Bidding keeps it in check by bidding on the chance each search converts. With manual bids or click bidding, broad match spends on loosely related searches.

What to do: Move the campaign to a conversion-based strategy, or change its broad match keywords to phrase or exact.

**G-KWD-03** Quality Score is healthy where the ads show (medium)

Why it matters: Quality Score of 4 or less means Google expects a poor click-through rate, a weak ad match or a weak landing page. Those keywords pay more per click and lose auctions to better ads.

What to do: Start with the keywords that carry the most impressions: tighten the ad groups so the ads match the keywords, and improve the landing page.

**G-KWD-04** Quality Score parts point to the ads and the landing pages (medium)

Why it matters: Quality Score has three parts: expected click-through rate, ad relevance and landing page experience. A large share of impressions below average on one of them says which to fix.

What to do: Ad relevance: write ads that use the keywords and split ad groups by theme. Landing page: match the page to the search and speed it up. Expected click-through rate: test stronger headlines and add assets.

**G-KWD-05** The same keyword is not in several ad groups of one campaign (low)

Why it matters: Two ad groups with the same keyword compete with each other in the same auction and split the data, so neither learns well.

What to do: Keep the keyword in the ad group whose ads fit it best and pause or remove the other copies.

**G-KWD-06** Few keywords spend without ever converting (high)

Why it matters: A keyword with plenty of clicks and no conversion in a month is spending money the account cannot trace to a result.

What to do: Check the search terms behind each one. Pause it, lower its bid, move it to exact match, or add the junk terms as negatives. If conversions are imported offline they arrive late, so recent clicks may not have a conversion yet: look at the import's delay first.

**G-KWD-07** Most search term spend ends in a conversion (medium)

Why it matters: The search terms report shows what people typed. Spend on terms that never converted is the clearest list of what to add as a negative keyword or to move to a more exact match.

What to do: Run the Search Query Analysis agent for this account and add the negatives it recommends.

**G-KWD-08** Keywords are approved and can serve (low)

Why it matters: A disapproved keyword never shows. A keyword Google calls rarely served has too little search volume to trigger ads.

What to do: Fix or replace disapproved keywords, and remove or broaden keywords that are rarely served.

**G-KWD-09** Ad groups hold a focused set of keywords (low)

Why it matters: An ad group with dozens of keywords cannot have ads that speak to each one, which lowers ad relevance and Quality Score.

What to do: Split the ad group by theme so each group has a handful of closely related keywords and its own ads.

**G-KWD-10** Every Search ad group has keywords (medium)

Why it matters: A Search ad group with no keyword cannot serve, and its ads are dead weight in the account.

What to do: Add keywords to the ad group or pause it.

### Ads and landing pages (9)

**G-ADS-01** Every Search ad group has a live ad (critical)

Why it matters: An ad group with no live ad cannot show anything, whatever its keywords are worth.

What to do: Write a responsive search ad for the ad group or pause the ad group.

**G-ADS-02** Every Search ad group has a responsive search ad (high)

Why it matters: Google no longer lets new expanded text ads be made, and responsive search ads are where Google's optimisation and reporting now live. An ad group with only older formats is not using them.

What to do: Add a responsive search ad with up to 15 headlines and 4 descriptions to each of these ad groups.

**G-ADS-03** Ad groups have more than one ad to test (medium)

Why it matters: With one ad there is nothing to compare. Two or three responsive search ads let Google and the team see which messages work.

What to do: Add a second responsive search ad built around a different angle (price, speed, trust) to each ad group.

**G-ADS-04** Responsive search ads are not rated Poor (medium)

Why it matters: Google rates each responsive search ad's strength from the number, variety and relevance of its headlines and descriptions. A Poor ad is limited in how it can be shown.

What to do: Open the Ad Strength Analysis agent for this account and apply the headline and description changes it recommends.

**G-ADS-05** No ad is disapproved (high)

Why it matters: A disapproved ad does not run. If it was the only ad in its ad group, the ad group is dark.

What to do: Open each disapproved ad's policy details, fix the text, landing page or claim that broke the policy, and appeal if it was wrongly flagged.

**G-ADS-06** No ad uses a retired format (low)

Why it matters: Expanded text ads and call-only ads can no longer be created. The ones left keep serving but get none of the newer features.

What to do: Replace them with responsive search ads and keep the old ads paused for a short overlap.

**G-ADS-07** Responsive search ads are not heavily pinned (low)

Why it matters: Pinning headlines to positions limits the combinations Google can test, which lowers ad strength and often performance.

What to do: Pin only what has to appear (a legal line, the brand) and let the rest rotate.

**G-ADS-08** Ad groups do not carry too many responsive search ads (low)

Why it matters: Google serves at most three responsive search ads per ad group. More only add clutter and slow the learning.

What to do: Keep the best two or three and pause the rest.

**G-ADS-09** Landing pages load (high)

Why it matters: An ad that sends people to a page that errors or never loads pays for clicks that cannot convert.

What to do: Fix or replace the final URL in the ads that point at broken pages, and check the pages are fast on mobile.

## Meta Ads (17 checks)

### Meta ad account (3)

**M-ACC-01** The ad account is active (critical)

Why it matters: A disabled, unsettled or closed ad account shows no ads. An account in a grace period or under review is about to.

What to do: Open Account Quality and Billing in Meta Business Settings, find the reason Meta gives and fix it (a failed payment, a policy appeal).

**M-ACC-02** The spend cap is not about to be reached (medium)

Why it matters: When the account reaches its spend cap every campaign stops, whatever the budgets say.

What to do: Raise or remove the spend cap in Billing before the account reaches it, or agree the new limit with the client.

**M-ACC-03** The pixel terms are accepted (low)

Why it matters: Meta will not let the pixel build custom audiences until the terms of use for them are accepted on the ad account.

What to do: Accept the custom audience terms in Meta's Audiences section for this ad account.

### Pixel and tracking (5)

**M-TRK-01** A pixel is installed (high)

Why it matters: The Meta pixel is how the site tells Meta who visited and who converted. Without it, conversion campaigns have nothing to optimise on.

What to do: Create a pixel (or a dataset) in Events Manager, install it on the site and assign it to this ad account. A pixel owned by the business but not shared with the ad account is not visible here.

**M-TRK-02** The pixel has fired recently (critical)

Why it matters: A pixel that has not fired for days means the tag was removed or broke, and every conversion since is lost to Meta.

What to do: Open Events Manager, check the pixel's status and Test Events, and reinstall the tag if it is gone.

**M-TRK-03** The pixel receives conversion events, not only page views (high)

Why it matters: Lead and sales campaigns learn from conversion events. A pixel that only reports page views gives them nothing to optimise on.

What to do: Set up the Lead, Schedule, Purchase or custom events on the form, booking or checkout, and test them in Events Manager.

**M-TRK-04** Advanced matching is on (medium)

Why it matters: Advanced matching sends hashed contact details with pixel events so Meta can match more conversions to people.

What to do: Turn on automatic advanced matching in the pixel's settings, if the client's privacy rules allow it. Healthcare and other regulated clients often cannot send contact details.

**M-TRK-05** Events also arrive through the Conversions API (medium)

Why it matters: Browsers and ad blockers hide a share of pixel events. The Conversions API sends the same events from the server, which recovers them and improves matching.

What to do: Set up the Conversions API (through the site platform's integration or Tag Manager server-side) and deduplicate it against the pixel.

### Campaigns and ad sets (6)

**M-STR-01** Ad sets are not stuck in Learning limited (high)

Why it matters: Learning limited means the ad set is not getting enough conversions (about 50 a week) to leave the learning phase, so its delivery stays unstable and costs more.

What to do: Merge ad sets that split the same audience, broaden targeting, raise the budget, or optimise for an event that happens more often.

**M-STR-02** No campaign or ad set reports an issue (high)

Why it matters: Meta flags campaigns and ad sets it cannot deliver in full: a payment problem, a missing pixel event, an audience that is too narrow.

What to do: Open each flagged campaign or ad set in Ads Manager and read the message Meta gives.

**M-STR-03** Campaigns that spend are producing results (high)

Why it matters: A lead, sales or app campaign that spends for a month and records no result is either not converting or not measuring.

What to do: Check the pixel event the campaign optimises for, the landing page and the audience, or pause the campaign and move its budget.

**M-STR-04** Lead and sales ad sets optimise for a conversion (medium)

Why it matters: An ad set in a lead or sales campaign that optimises for link clicks, page views or reach finds people who click, not people who convert.

What to do: Change the ad set's optimisation event to Leads, Schedule, Purchase or another conversion once the pixel receives it.

**M-STR-05** Frequency is not high (medium)

Why it matters: A frequency of four or more in 30 days means the same people see the ads over and over, which tires the creative and raises the cost of a result.

What to do: Refresh the creative, widen the audience, or lower the budget of the campaign.

**M-STR-06** The ad account has live campaigns (high)

Why it matters: An ad account with no live campaign is not advertising. Most of the other checks have nothing to look at, so its score only reflects the account and its pixel.

What to do: Turn on the campaigns that should be running, or audit again once the account is built.

### Ads (3)

**M-ADS-01** No ad is disapproved (high)

Why it matters: A disapproved ad does not run, and repeated disapprovals put the whole account at risk.

What to do: Open each disapproved ad's review feedback, fix the creative, text or landing page, and request a review if it was wrongly flagged.

**M-ADS-02** Every live ad set has an ad that can deliver (critical)

Why it matters: An ad set whose ads are all disapproved, paused or deleted cannot deliver, however its audience and budget are set.

What to do: Add an ad to the ad set, fix the disapproved ones, or turn the ad set off.

**M-ADS-03** Ad sets have more than one ad to test (medium)

Why it matters: With one ad there is nothing to compare. Several ads let Meta's delivery find the creative that works and show the team what to make next.

What to do: Add two or three ads per ad set with different images or videos and different first lines.
