"""Paid Marketing Agents: QA Agent, how to fix a check that failed (no network, no model).

A rule that fails says what it found ("AI Max is on"); the person launching the campaign also needs to know where in Google Ads to change it. This holds the
steps for every check a rule can fail, as menu paths and the value to set, and attaches them to the failed checks of a review. The menu names follow the Google Ads
screen at the time of writing and can differ a little between accounts; every step says what the setting is called so it can be found by name.

Only a check that FAILED gets a fix, only where the person or the model has not already written one, and the checklist's own expected value is added when it
states one (an expected value that only says "depends on the campaign book" adds nothing).
"""
from __future__ import annotations

_ACCOUNT_AUTO = "Open Admin, then Account settings, then Automatically created assets, and set %s to %s."
_SETTINGS = "Open the campaign, then Settings"


def _auto(label, want):
    return _ACCOUNT_AUTO % ('"%s"' % label, want)


# check id -> the steps. A value may be a string or {channel type: string, "*": the rest}.
HOW = {
    "1.1": _SETTINGS + ", then click the pencil next to the campaign name and rename it to the P2 format (P2_ then the campaign type, Search or PMax, then what it is).",
    "1.2": "A campaign's type cannot be changed after it is created. Create a new campaign of the right type (Campaigns, then the + button, then New campaign), copy the settings across, and pause this one once the new one is live.",
    "1.3": _SETTINGS + ", then Networks. Untick Google search partners and the Display Network (and the Video network on a Search campaign) unless the campaign book asks for them.",
    "1.4": "Open Ads in the left menu, click the + button, choose Responsive search ad, and add an ad to every ad group (use 15 headlines and 4 descriptions) so each ad group has at least one live ad.",
    "1.5": _SETTINGS + ", then AI Max for Search campaigns, and turn the AI Max switch off.",
    "1.6": "Open the campaign, then Assets (under Ads and assets), then the + button, and add what is missing: sitelinks, callouts, a structured snippet, a call asset and images.",
    "1.7": _SETTINGS + ", then Goals, and choose the account's default goals instead of a goal chosen for this campaign alone.",
    "1.8": _SETTINGS + ", then Bidding, then Change bid strategy, and pick the strategy the campaign book names.",
    "1.9": _SETTINGS + ", then Goals, and under the conversion goals choose the account-level default goals (Conversions).",
    "1.10": _SETTINGS + ", then Devices, and set the bid adjustment of each device as the campaign book says (a bid adjustment of minus 100% stops ads on that device).",
    "1.11": "Open the campaign, then Audiences, then Demographics, then Gender, and exclude (or leave included) the genders the campaign book names.",
    "1.12": "Open the campaign, then Audiences, then Demographics, then Household income, and exclude the income bands the client does not want to reach.",
    "1.13": "Open the campaign, then Audiences, then Exclusions, and remove any audience that should not be excluded.",
    "1.14": _SETTINGS + ", then Locations, then Enter another location, and set the locations the campaign book names (and remove the others).",
    "1.15": _SETTINGS + ", then Languages, and choose English only.",
    "1.16": "Open the campaign, then Audiences, then Demographics, then Age, and exclude the age bands the campaign book rules out.",
    "1.17": "Open Ads, open each ad that has a headline or description containing {KeyWord: or {Keyword:, and replace that part with fixed text.",
    "1.18": "Open Tools, then Shared library, then Negative keyword lists, and apply the account's list to this campaign (or open the campaign, then Keywords, then Negative keywords, and add them).",
    "1.19": _SETTINGS + ", then EU political ads, and answer that the campaign does not have political ads.",
    "1.20": "Set a tracking template or Final URL suffix: " + _SETTINGS + ", then Additional settings, then Campaign URL options, or at account level, Admin, then Account settings, then Tracking.",
    "1.21": _SETTINGS + ", then Locations, then Location options, then Target, and choose Presence: People in or regularly in your included locations.",
    "1.22": _SETTINGS + ", then Start and end dates, and set the start date to the day the campaign goes live and the end date to None.",
    "1.23": _SETTINGS + ", then Additional settings, then Ad rotation, and choose Optimize: Prefer best performing ads.",
    "1.24": _SETTINGS + ", then Ad schedule, and add or edit the days and hours the campaign book names.",
    "1.25": "Open Ads, open the ad whose address fails, and correct its Final URL so the page loads (a 404, a redirect to the wrong page or a blocked page loses the click).",
    "2.1": {"PERFORMANCE_MAX": _SETTINGS + ", then Final URL expansion, and untick Use final URL expansion.",
            "*": _SETTINGS + ", then Final URL expansion (or Settings, then AI Max for Search campaigns), and turn final URL expansion off."},
    "2.2": _SETTINGS + ", then Asset automation (or Automatically created assets), and switch the automation off.",
    "3.1": _auto("Automated app assets", "Off"),
    "3.2": _auto("Automated location assets", "Off"),
    "3.3": _auto("Automated promotion assets", "Off"),
    "3.4": _auto("Dynamic business logos", "On"),
    "3.5": _auto("Dynamic business names", "On"),
    "3.6": _auto("Dynamic callouts", "Off"),
    "3.7": _auto("Dynamic images", "Off"),
    "3.8": _auto("Dynamic sitelinks", "Off"),
    "3.9": _auto("Dynamic structured snippets", "Off"),
    "3.10": _auto("Longer ad headlines", "Off"),
    "3.11": _auto("Seller ratings", "Off"),
    "5.1": "Open Admin, then Account settings, then Automatically created assets (or Generated assets), and turn the automatically generated text and image options off.",
    "5.2": "Google Ads does not show this setting to reporting. Open Admin, then Account settings, and look for the automatic upgrade and migration options, and switch them off; if the account shows no such setting, ask the Google rep.",
    "6.1": "Open the campaign, then Asset groups, open each asset group, and under Audience signal add the customer list, a custom segment or in-market audiences that fit the business.",
    "6.2": "Open the campaign, then Asset groups, open each asset group, and under Search themes add up to 25 phrases that match what the landing page sells.",
    "6.3": "Open the campaign, then Asset groups, open each rated Poor or Average, and add the assets Google lists under its ad strength tips (more headlines, descriptions, images, a video) until it reaches Good or Excellent.",
    "6.4": "Open the campaign, then Ad groups, open each ad group, then Audiences, and add the audiences that fit the business (a customer list for Demand Gen works best).",
}

# The auto-apply recommendations (section 4): every one is switched off in the same place.
AUTO_APPLY = {
    "4.1": "Use optimized ad rotation", "4.2": "Improve your search ads", "4.3": "Remove redundant keywords", "4.4": "Remove non-serving keywords",
    "4.5": "Remove conflicting negative keywords", "4.6": "Use optimized targeting", "4.7": "Upgrade your conversion tracking", "4.8": "Add new keywords",
    "4.9": "Add broad match keywords", "4.10": "Use display expansion", "4.11": "Bid more efficiently with Target impression share",
    "4.12": "Bid more efficiently with Maximize clicks", "4.13": "Bid more efficiently with Maximize conversions", "4.14": "Bid more efficiently with Maximize conversion value",
    "4.15": "Bid more efficiently with Maximize conversions using a target CPA", "4.16": "Bid more efficiently with Maximize conversion value using a target ROAS",
    "4.17": "Set a target CPA", "4.18": "Set a target ROAS", "4.19": "Adjust your CPA targets", "4.20": "Adjust your ROAS targets",
    "4.21": "Add store visits as an account default goal"}
for _id, _label in AUTO_APPLY.items():
    HOW[_id] = ('Open Recommendations in the left menu, then Auto-apply (the link at the top of the Recommendations page), find "%s" in the list, and turn it off. '
                "An auto-applied recommendation changes the account without anyone being asked.") % _label

# The Meta checklist's fixes, in Meta Ads Manager's words (a campaign is edited in Ads Manager: the campaign, the ad set, the ad).
_AM_CAMPAIGN = "In Ads Manager open the campaign and click Edit"
_AM_ADSET = "In Ads Manager open the ad set and click Edit"
_AM_AD = "In Ads Manager open the ad and click Edit"
HOW_META = {
    "1.1": _AM_CAMPAIGN + ", then rename it to the P2 format (P2_ then MetaLeads, MetaSales, MetaTraffic or MetaAwareness, the theme and the launch date as 8 digits).",
    "1.2": "A campaign's objective cannot be changed after it is created. Create a new campaign with the right objective (Create, then pick the objective), copy the ad sets and ads across with Duplicate, and turn this one off once the new one is live.",
    "1.3": _AM_CAMPAIGN + ", then Special ad categories: declare the category the ads are about (credit, jobs, housing, or social issues, elections or politics), or remove a category that does not apply. This cannot be changed on a live campaign: duplicate it.",
    "1.4": "The buying type cannot be changed after it is created. Create a new campaign with the Auction buying type and move the ads over.",
    "1.5": _AM_CAMPAIGN + ", then Budget and schedule, and set the budget type and amount the campaign book names. Change a budget in steps of about 20 percent so the ad set stays out of the learning phase.",
    "1.6": _AM_CAMPAIGN + " (or the ad set), then the bid strategy, and choose the one the campaign book names. A cost or bid cap needs a results history behind it.",
    "1.7": _AM_ADSET + ", then Budget and schedule, set the start date to the day the campaign goes live and remove the end date (a lifetime budget needs one: use a daily budget instead).",
    "1.8": "In Ads Manager add ads to any ad set with fewer than 3 active ads (Duplicate an ad and change the picture or the first line), and turn off the ones beyond 10.",
    "1.9": _AM_AD + ", then under Ad setup, Identity, choose the Facebook Page and the Instagram account the client uses.",
    "2.1": _AM_ADSET + ", then Conversion, and choose the conversion location the campaign book names.",
    "2.2": _AM_ADSET + ", then Conversion, then Performance goal, and choose the goal the campaign book names (for leads or sales a goal that counts conversions, not clicks or reach).",
    "2.3": _AM_ADSET + ", then Conversion, then Dataset, and pick the client's dataset (pixel) and the conversion event the campaign book names.",
    "2.4": _AM_ADSET + ", then Conversion, then Attribution setting, and choose the window the campaign book names (Meta's default is 7-day click and 1-day view).",
    "2.5": _AM_ADSET + ", then Audience, then Locations, and set the places the campaign book names (and remove the others).",
    "2.6": _AM_ADSET + ", then Audience, then Locations, then the location type, and choose People living in or recently in this location.",
    "2.7": _AM_ADSET + ", then Audience, then Age, and set the range the campaign book names. Meta only holds a limit when Advantage+ audience is off.",
    "2.8": _AM_ADSET + ", then Audience, then Gender, and set what the campaign book names.",
    "2.9": _AM_ADSET + ", then Audience, then Languages, and clear it unless the campaign book names a language.",
    "2.10": _AM_ADSET + ", then Audience, then Detailed targeting, and set the interests, custom audiences and lookalikes the campaign book names.",
    "2.11": _AM_ADSET + ", then Audience, then Custom audiences, and exclude the client's customers and current leads.",
    "2.12": _AM_ADSET + ", then Placements, and choose Advantage+ placements unless the campaign book names others.",
    "3.1": "In Ads Manager add ads in the formats the campaign book names (a video or a carousel next to the images).",
    "3.2": _AM_AD + ", then Primary text, and shorten it to 125 characters or fewer (Meta cuts it with See more after that), and fix any spelling or claim the finding names.",
    "3.3": _AM_AD + ", then Headline, and shorten it to 40 characters or fewer.",
    "3.4": _AM_AD + ", then Description, and shorten it to 30 characters or fewer, or clear it.",
    "3.5": _AM_AD + ", then Call to action, and choose the button that matches the objective (Learn more, Get quote, Book now, Contact us, Call now, Sign up or Apply now).",
    "3.6": _AM_AD + ", then Destination, and correct the website address so the page loads over https and matches the ad.",
    "3.7": _AM_AD + ", then Tracking, then URL parameters, and add utm_source=meta&utm_medium=paid_social&utm_campaign={{campaign.name}}&utm_content={{ad.name}} (or the parameters the account uses).",
    "3.8": _AM_AD + ", then Advantage+ creative, and turn off the enhancements the campaign book does not allow.",
    "3.9": "Open each ad that Ads Manager marks Disapproved or With issues, read the reason under the ad's status, fix what it names (text, image or destination) and ask for a review.",
    "5.1": "Rewrite the ad text so it talks about the product and the need, never about the person (not Are you struggling with X?), and remove any image that suggests a personal attribute.",
    "5.2": "Remove the claim from the ad, or get the client to confirm it and show it on the landing page.",
    "5.3": "Check the ad text against the industry's advertising rules (healthcare, finance, legal) and remove what breaks them.",
}

_VAGUE = ("depends", "from the campaign book", "check the", "none")


def how(check_id, channel=""):
    """The steps for a check, or "" when none are known. `channel` is the Google Ads channel type (SEARCH, PERFORMANCE_MAX...), or META_<objective> for a Meta campaign."""
    v = (HOW_META if str(channel or "").startswith("META_") else HOW).get(str(check_id))
    if isinstance(v, dict):
        v = v.get(channel) or v.get("*")
    return v or ""


def _expected(text):
    t = " ".join(str(text or "").split())
    return "" if not t or any(t.lower().startswith(v) for v in _VAGUE) else t


def attach(findings, checks, channel=""):
    """The review's findings with a fix on every failed check that has none: {check id: finding}. `checks` is the checklist rows reviewed (their `expected`)."""
    expected = {c["id"]: _expected(c.get("expected")) for c in checks}
    out = {}
    for cid, f in findings.items():
        f = dict(f)
        if f.get("result") == "fail" and not str(f.get("fix") or "").strip():
            steps = how(cid, channel)
            if steps:
                want = expected.get(cid)
                f["fix"] = steps + (" The checklist expects: %s." % want.rstrip(".") if want else "")
        out[cid] = f
    return out
