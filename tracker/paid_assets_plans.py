"""Paid Marketing Agents: Asset Coverage Audit, what to do about each gap and each problem asset (no network).

The audit says "Sitelinks: 1 live, add 3" and "2 disapproved". This says how: where to click in Google Ads, the limits the new asset has to respect (the size of a
sitelink's text, the shape of an image) and, for a disapproved asset, what Google named as the reason and what usually fixes that reason.

Two honest limits. Google publishes no figure for what an asset adds to an ad, so a gap's "worth" is what it can state: how many impressions the campaign had in the last 30 days
without it. And the limits below are Google's at the time of writing; the asset editor shows the live ones, which is said in the steps.
"""
from __future__ import annotations

# What each kind of asset needs, in Google's terms. `what` is the line for the asset editor. Characters are Google's maximums.
CAMPAIGN_KINDS = {
    "SITELINK": ("Sitelink", "Write the link text (up to 25 characters), two description lines (up to 35 each) and the final URL of a different page for each sitelink. Add at least 4: Google shows a sitelink only when 2 are live."),
    "CALLOUT": ("Callout", "Write each callout in up to 25 characters (for example \"Free parking\" or \"Same-week appointments\"). Add at least 4; Google shows callouts only when 2 are live."),
    "STRUCTURED_SNIPPET": ("Structured snippet", "Pick a header from Google's list (for example Services, Types or Brands) and add 3 to 10 values of up to 25 characters that fit it."),
    "CALL": ("Call", "Choose the country and enter the number people should ring. Tick call reporting so the calls are counted."),
    "AD_IMAGE": ("Image", "Upload a square image (1:1, at least 300 x 300 pixels, 1200 x 1200 is best) and a landscape one (1.91:1, at least 600 x 314, 1200 x 628 is best). No text over the picture."),
    "BUSINESS_NAME": ("Business name", "Enter the business name in up to 25 characters, as customers know it."),
    "LOGO": ("Logo", "Upload a square logo (1:1, at least 128 x 128 pixels, 1200 x 1200 is best)."),
    "PROMOTION": ("Promotion", "Say what is on offer (the occasion, the discount or the amount off) and set the dates it runs. Only add one when there is a real offer."),
    "PRICE": ("Price", "Add at least 3 items, each with a header and a short description (up to 25 characters each), a price and the page it goes to."),
    "LEAD_FORM": ("Lead form", "Write the headline (up to 30 characters) and the questions to ask, and say where leads should go (the form is read in Google Ads or sent to your CRM)."),
    "LOCATION": ("Location", "Link the Business Profile (Assets, then Locations, then link the profile) so the address and distance can show."),
}

GROUP_KINDS = {
    "HEADLINE": "Add headlines of up to 30 characters, at least 3 and up to 15, each saying something different.",
    "LONG_HEADLINE": "Add long headlines of up to 90 characters (up to 5).",
    "DESCRIPTION": "Add descriptions of up to 90 characters (at least 2, and one of up to 60), up to 5 in all.",
    "MARKETING_IMAGE": "Add landscape images (1.91:1, at least 600 x 314 pixels, 1200 x 628 is best), up to 20 across all image sizes.",
    "SQUARE_MARKETING_IMAGE": "Add square images (1:1, at least 300 x 300 pixels, 1200 x 1200 is best).",
    "PORTRAIT_MARKETING_IMAGE": "Add portrait images (4:5, at least 480 x 600 pixels, 960 x 1200 is best).",
    "LOGO": "Add a square logo (1:1, at least 128 x 128 pixels, 1200 x 1200 is best).",
    "BUSINESS_NAME": "Enter the business name in up to 25 characters.",
    "YOUTUBE_VIDEO": "Add a YouTube video of at least 10 seconds. Without one Google builds a video from your images, which is rarely as good.",
}

REVIEW = "New assets are reviewed first, usually within a business day, and show in the Status column once approved. The editor shows Google's current limits if they have changed."


def _worth(gap, ctx):
    """What a gap can honestly be set against: the campaign's impressions in the last 30 days (only for the gaps that matter)."""
    impr = ctx.get("impressions") or 0
    if gap.get("severity") not in ("high", "medium"):
        return None
    if impr <= 0:
        return "The campaign had no impressions in the last 30 days, so settle why it is not showing before adding to it."
    share = ctx.get("share")
    bit = " (%d%% of what the audited campaigns had)" % round(share * 100) if share else ""
    return "The campaign had %s impressions in the last 30 days%s %s. Google does not publish what an asset adds, so there is no conversion estimate here." % (
        "{:,}".format(impr), bit, "with none live" if gap.get("have") == 0 else "with fewer than the target")


def level_of(scope):
    if scope == "Campaign":
        return "campaign"
    if scope.startswith("Asset group") or "asset group" in scope:
        return "group"
    return "ad"


def gap_plan(gap, ctx):
    """{steps, worth} for one gap ({scope, key, label, severity, have, target, add, message}); `ctx` is {name, type, impressions, share}."""
    level, key, name = level_of(gap["scope"]), gap["key"], ctx.get("name") or "the campaign"
    add = gap.get("add") or 0
    amount = "%d more" % add if gap.get("have") else "%d" % add
    if key == "ASSET_GROUPS":
        steps = ["Open Google Ads, then Campaigns, and click \"%s\"." % name,
                 "Choose Asset groups in the left menu, then the +, and build one: at least 3 headlines, 2 descriptions, 1 long headline, a landscape and a square image, a logo and the business name.",
                 "Point its final URL at the page the campaign is meant to send people to, and set the audience signal if you have one. " + REVIEW]
        return {"steps": steps, "worth": _worth(gap, ctx)}
    if level == "campaign":
        kind = CAMPAIGN_KINDS.get(key)
        if not kind:
            return None
        steps = ["Open Google Ads, then Campaigns, click \"%s\", and choose Assets in the left menu (under Ads & assets)." % name,
                 "Click the +, choose %s, and add %s. %s" % (kind[0], amount, kind[1]),
                 "When Google asks where to add it, choose this campaign; choose the account instead if every campaign should carry it. " + REVIEW]
        return {"steps": steps, "worth": _worth(gap, ctx)}
    if level == "group":
        how = GROUP_KINDS.get(key)
        if not how:
            return None
        steps = ["Open Google Ads, then Campaigns, click \"%s\", and choose Asset groups in the left menu." % name,
                 "Open the asset group named in the gap (the pencil to edit it), and find the %s section." % gap["label"].lower(),
                 "%s Add %s. %s" % (how, amount, REVIEW)]
        return {"steps": steps, "worth": _worth(gap, ctx)}
    how = GROUP_KINDS.get(key)
    if not how:
        return None
    steps = ["Open Google Ads, then Campaigns, click \"%s\", and choose Ads in the left menu." % name,
             "Open the ad named in the gap, click Edit, and find the %s section." % gap["label"].lower(),
             "%s Add %s, then save. %s" % (how, amount, REVIEW)]
    return {"steps": steps, "worth": _worth(gap, ctx)}


# ── Assets that are there but not working ───────────────────────────────────

# (words in Google's policy topic, what it usually means and what usually fixes it). The first that matches wins.
TOPIC_HINTS = (
    ("DESTINATION", "The page the asset sends people to does not work or does not match. Open the final URL in a browser: if it errors, fix the page; if it lands somewhere other than the asset says, change the URL."),
    ("TRADEMARK", "The text uses someone else's trademark. Take the brand name out unless you have the owner's permission, and resubmit."),
    ("PUNCTUATION", "The text has punctuation or symbols Google does not allow (extra exclamation marks, symbols or odd spacing). Rewrite it in plain words."),
    ("SYMBOL", "The text has punctuation or symbols Google does not allow. Rewrite it in plain words."),
    ("CAPITAL", "The text has capital letters in the wrong places (a whole word in capitals). Use normal capitals."),
    ("REPETIT", "The text repeats a word or phrase. Say it once."),
    ("GIMMICK", "The text uses a gimmick (repeated letters, odd spacing). Write it plainly."),
    ("PERSONALIZED", "The asset implies it knows something personal about the viewer (a health condition, for example). Say what you offer, not who the viewer is."),
    ("HEALTH", "Health claims and some health content are restricted and may need Google's approval for the business. Take out claims of cures or results, keep to what the practice does, and check the healthcare policy page."),
    ("MISLEAD", "The text could mislead (an offer without its terms, a claim that is not shown on the page). Make it match the page exactly."),
    ("UNCLEAR", "Google cannot tell what the asset is about. Make the text specific and match the page."),
    ("IMAGE", "The image breaks the image rules (text over it, a poor crop or low quality). Replace it with a clean, well-cropped one."),
    ("PHONE", "The phone number or contact detail is not allowed in this field or does not work. Use the call asset for numbers and check the number rings."),
    ("CONTACT", "A phone number or contact detail is in a field that does not allow one. Move it to a call asset."),
)
GENERIC_TOPIC = "Open the policy named and read what it asks for."


def topic_hint(topic):
    t = str(topic or "").upper()
    for word, hint in TOPIC_HINTS:
        if word in t:
            return hint
    return GENERIC_TOPIC


def topic_name(topic):
    return str(topic or "").replace("_", " ").strip().capitalize()


def _topics(bad):
    seen, out = set(), []
    for b in bad:
        for t in b.get("why") or []:
            if t["t"] not in seen:
                seen.add(t["t"])
                out.append(t["t"])
    return out


def problem_fix(slot):
    """{steps} for the problem assets of one slot (disapproved, expired, paused, under review), or None. The policy topics Google named are in slot["bad"][n]["why"]."""
    steps = []
    topics = _topics(slot.get("bad") or [])
    if slot.get("disapproved"):
        steps.append("Open Google Ads, then Tools, then Policy manager. It lists each disapproved asset with the policy that stopped it.")
        for t in topics[:3]:
            steps.append("%s: %s" % (topic_name(t), topic_hint(t)))
        if not topics:
            steps.append("Google did not name the policy for these in the read, so open the asset in Policy manager and read the reason beside it.")
        steps.append("Edit the asset text (or replace the asset) and save: an edited asset is reviewed again. If you believe it complies as it is, use Appeal in Policy manager instead.")
    if slot.get("expired"):
        steps.append("For the assets past their end date, open the asset in the campaign's Assets list and set a new end date (or remove the end date), or remove the asset if the offer is over.")
    if slot.get("paused"):
        steps.append("For the paused assets, tick them in the campaign's Assets list and choose Enable, or remove them if they should stay off.")
    if slot.get("pending"):
        steps.append("The assets under review are counted as live. If they are still pending after two business days, open Policy manager to see whether Google has asked for something.")
    return {"steps": steps} if steps else None
