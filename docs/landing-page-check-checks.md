# Landing Page & Relevancy Check: every check

Generated from the catalog in `tracker/paid_lp_model.py` by `tracker/paid_lp_docs.py`; a test holds this file equal to it.
Every check is run on each landing page, except the ones marked for one platform, which run only for a page that platform's ads send people to. A check whose input could not be read says "could not check" and is never passed or failed. Severity weights: critical 8, high 4, medium 2, low 1.

39 checks in 6 sections.

## The page opens (7)

**reach_status** The page opens (critical)

Why it matters: An ad that sends people to a page that does not open pays for clicks that can never convert, and Google marks the ad's landing page experience down.

What to do: Open the address from the ad. If it fails, correct the final URL in the ad or restore the page.

**reach_redirect** The ad's address does not bounce (medium)

Why it matters: Each redirect adds a wait on a phone, and a redirect that drops people on the home page throws away the work the ad did to promise something specific.

What to do: Put the address people should land on straight into the ad's final URL, so there is no redirect.

**reach_params** The click id survives the redirect (high)

Runs for pages Google's ads reach only.

Why it matters: When a redirect drops the click id (gclid) and the UTM tags, Google Ads and Analytics lose track of which click became a customer.

What to do: Have the redirect keep the whole query string, or send the ad straight to the final address.

**reach_params_meta** Meta's click id survives the redirect (high)

Runs for pages Meta's ads reach only.

Why it matters: When a redirect drops the click id (fbclid) and the UTM tags, the Meta pixel cannot match the visit to the ad click, so Meta and Analytics lose track of which click became a customer.

What to do: Have the redirect keep the whole query string, or send the ad straight to the final address.

**reach_https** The page is served over https (high)

Why it matters: Browsers warn visitors away from pages that are not secure and Google may refuse the ad, especially where a form asks for contact details.

What to do: Serve the page over https and use the https address in the ad.

**reach_staging** The address is not a test site (critical)

Why it matters: A staging or test address in a live ad shows the client's budget to a page that is unfinished and often hidden from search engines.

What to do: Replace the final URL with the live site's address.

**reach_adsbot** Google's ad crawler may read the page (critical)

Runs for pages Google's ads reach only.

Why it matters: AdsBot judges the landing page. When robots.txt names it and keeps it out, the page cannot be checked and the ad can stop running.

What to do: Remove the rule that blocks AdsBot-Google from the page in robots.txt.

## Does it match the ad (11)

**match_meta_conversion** Meta rates the conversion rate average or better (high)

Runs for pages Meta's ads reach only.

Why it matters: Meta compares each ad's conversion rate with the ads competing for the same people. Below average means the page converts fewer of the people the ad brings than other advertisers' pages do, and Meta charges more to show the ad.

What to do: Fix what the other checks on this page find (match, speed, mobile, a short form) and Meta's rating follows in a few weeks.

**match_meta_quality** Meta rates the ad quality average or better (medium)

Runs for pages Meta's ads reach only.

Why it matters: Meta's quality ranking compares how people judge an ad (what they hide, report or ignore) with the ads competing for the same people, and a low one raises what each person costs.

What to do: Make the ad say what the page delivers, in plain words, with the same offer and the same picture of the service, and take out claims the page does not make.

**match_keywords_page** The keywords are on the page (high)

Runs for pages Google's ads reach only.

Why it matters: People searched for these words and clicked an ad written for them. A page that never uses them feels like the wrong place, and Google rates it down.

What to do: Add the missing keywords, in the words people use, to the page's headline and body, or send those ad groups to a page that already uses them.

**match_keywords_headline** The keywords are in the page's headline (medium)

Runs for pages Google's ads reach only.

Why it matters: The headline is what a visitor reads first. When it does not repeat what they searched, they stop to wonder if they are in the right place.

What to do: Rewrite the page's title and main headline so they say what the ad group's keywords say.

**match_terms** What people typed is on the page (high)

Runs for pages Google's ads reach only.

Why it matters: The search terms are the real wording of the people who clicked, and a page that does not use their words converts fewer of them.

What to do: Use the most common search terms in the page's text, and add negative keywords for the ones the page should not answer.

**match_headline** The page's headline echoes the ad (medium)

Why it matters: A visitor who clicked an ad expects its headline's words on the page they land on.

What to do: Write the page's main headline from the ad group's best ad headline, or write the ad from the page's.

**match_offers** The ad's offers are on the page (high)

Why it matters: An ad that promises a price, a discount or a free service and a page that does not mention it feels like bait, and visitors leave.

What to do: Show every offer the ads make on the page, near the top and in the same words, or take it out of the ads.

**match_h1** The page has one clear main headline (medium)

Why it matters: The main headline (the H1) tells visitors and search engines what the page is. None, or a dozen, leaves them guessing.

What to do: Give the page one H1 that says what is offered and where.

**match_lp_experience** Google rates the landing page experience average or better (high)

Runs for pages Google's ads reach only.

Why it matters: Landing page experience is one of the three parts of Quality Score. Below average raises the cost of every click and lowers where the ad shows.

What to do: Fix what the other checks on this page find (match, speed, mobile) and Google's rating follows in a few weeks.

**match_ad_relevance** Google rates the ad relevance average or better (medium)

Runs for pages Google's ads reach only.

Why it matters: Ad relevance is one of the three parts of Quality Score and measures how closely the ad speaks to the keyword.

What to do: Split the ad group so each holds keywords on one theme, and put the keyword's words in the ad headlines.

**match_content** The page has enough to read (low)

Why it matters: A page with a handful of words does not answer the questions a visitor has before they act.

What to do: Add what a visitor needs to decide: what is offered, who it is for, the price or how it is worked out, and why to choose this business.

## Can a visitor act (4)

**path_action** The page gives a way to act (critical)

Why it matters: A visitor who cannot call, book, buy or send a form leaves, however well the ad matched the page.

What to do: Add the one thing the ad is for: a short form, a tappable phone number, a booking tool or a buy button.

**path_cta_top** A call to action sits near the top (high)

Why it matters: Most visitors decide in the first screen. A page that asks for the action only far down loses the ones who never scroll.

What to do: Put a clear button or phone link, in words like Book now or Get a quote, in the first screen.

**path_form_length** The form is short (medium)

Why it matters: Every extra field lowers the share of visitors who finish a form, and most of the loss is on a phone.

What to do: Keep the fields the sales team needs to follow up (name, phone or email, one question) and ask for the rest later.

**path_focus** The page is not a maze of links (low)

Why it matters: Every other link on a landing page is a way to leave without acting.

What to do: Use a landing page template with a short header and no full site menu for ad traffic.

## Works on a phone (4)

**mobile_viewport** The page is built for a phone (high)

Why it matters: Most ad clicks come from phones. A page with no mobile viewport shows as a shrunken desktop page that people must pinch and drag.

What to do: Add <meta name="viewport" content="width=device-width, initial-scale=1"> and a responsive layout.

**mobile_zoom** Visitors can zoom (low)

Why it matters: Blocking pinch zoom makes small text unreadable for people who need it and fails accessibility rules.

What to do: Remove user-scalable=no and maximum-scale=1 from the viewport tag.

**mobile_tap_to_call** Phone numbers can be tapped (medium)

Why it matters: A phone number shown as plain text must be remembered and typed. A tel: link starts the call with one tap.

What to do: Wrap each phone number in a link that begins tel: followed by the number.

**mobile_conv_gap** Phones convert nearly as well as desktops (medium)

Why it matters: When phone visitors convert at far less than desktop visitors on the same page, the page is probably costing mobile clicks.

What to do: Open the page on a phone, find where it gets hard (the form, a menu, a slow image), and fix that before raising mobile bids.

## Loads fast (8)

**speed_google** Google's mobile speed score is good (medium)

Runs for pages Google's ads reach only.

Why it matters: Google scores each landing page's mobile speed from one to ten and slow pages lose clicks before they load.

What to do: Compress images, remove scripts the page does not need and ask the developer for the biggest delays in a speed test.

**speed_server** The server answers quickly (medium)

Why it matters: Nothing appears until the server starts to answer, so a slow server delays everything after it.

What to do: Turn on page caching or a content network, or move to faster hosting.

**speed_html** The page's HTML is light (low)

Why it matters: A very large page takes longer to download and to read, most of all on a mobile connection.

What to do: Remove inline scripts, styles and repeated blocks from the page, or load them from files that can be cached.

**speed_compression** The page is compressed (medium)

Why it matters: Without compression the same page is several times larger to download.

What to do: Turn on gzip or brotli compression for HTML on the web server.

**speed_blocking** Few scripts hold up the first paint (medium)

Why it matters: A script in the head that is not async or deferred stops the page from showing until it has downloaded and run.

What to do: Add async or defer to scripts in the head, or move them to the end of the page.

**speed_scripts** The page loads a sensible number of scripts (medium)

Why it matters: Each script is a download and some work for the phone; a page with dozens of them is slow to become usable.

What to do: Remove tags and widgets nobody uses, and load the rest through one tag manager.

**speed_images** Images do not shift or weigh the page down (low)

Why it matters: Images without a set size make the page jump as they load, and images far down the page need not load until someone scrolls there.

What to do: Give every image width and height, and add loading="lazy" to those below the first screen.

**speed_lab** The lab speed test is good (medium)

Why it matters: A lab test loads the page like a mid-range phone on a mobile connection and times what a visitor sees and when it becomes usable.

What to do: Work through the test's own list of biggest delays, starting with images and scripts.

## Tracking and trust (5)

**trust_tracking** A Google tag or tag manager is on the page (high)

Runs for pages Google's ads reach only.

Why it matters: Without a tag Google Ads and Analytics cannot count the visit or the sale that follows it.

What to do: Install the Google tag or Tag Manager on the page and check that the conversion fires on the thank-you step.

**trust_meta_pixel** The Meta pixel is on the page (high)

Runs for pages Meta's ads reach only.

Why it matters: Without the pixel Meta cannot count the visit or the lead or sale that follows it, so it cannot find more people like the ones who act and the ad set learns from clicks alone.

What to do: Install the Meta pixel on the page (directly or through Tag Manager) and check in Events Manager that the conversion fires on the thank-you step.

**trust_privacy** There is a privacy policy link (medium)

Why it matters: Google's rules and privacy laws expect a policy to be reachable wherever a page collects contact details.

What to do: Link the privacy policy in the page's footer and next to the form.

**trust_contact** The business can be contacted from the page (medium)

Why it matters: A page with no phone number, address or email looks like it could be anyone, and visitors who want to ask first have nowhere to go.

What to do: Show the phone number, the address and an email address in the header or footer.

**trust_proof** The page shows proof (low)

Why it matters: Reviews, ratings, awards and years in business answer the visitor's doubt before they fill in a form.

What to do: Add review ratings, a testimonial or two and any awards or credentials near the call to action.
