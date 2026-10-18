"""Paid Marketing Agents: Account Audit, the Google Ads checklist.

Each check reads the compact account read (tracker/paid_audit_google_read.py) and decides a result with the numbers that justify it; the
wording of why it matters and what to do is in the catalog (tracker/paid_audit_model.py). A check whose input was not read says
"unknown". Thresholds are named constants so the page and the tests can quote them. Nothing here asks a model anything.
"""
from __future__ import annotations

import logging

from . import paid_audit_model as model
from .paid_audit_model import names, pct, plural, share, verb  # noqa: F401  (the helpers every checklist uses)
from .paid_audit_google_read import LEGACY_ADS, SEARCH_LIST_MIN
from .paid_audit_pages import TIMED_OUT

log = logging.getLogger(__name__)
make, unknown, na = model.make, model.unknown, model.not_applicable

# Conversion action types counted from a tag, an import or an upload (judged for recording nothing, counting and windows). Google's own
# defaults are listed by GOOGLE_DEFAULT_TYPES further down and are never taken as the client's measurement.
WEB_TYPES = {"WEBPAGE", "WEBPAGE_CODELESS", "UPLOAD_CLICKS", "UPLOAD_CALLS", "GOOGLE_ANALYTICS_4_CUSTOM", "GOOGLE_ANALYTICS_4_PURCHASE",
             "UNIVERSAL_ANALYTICS_GOAL", "UNIVERSAL_ANALYTICS_TRANSACTION", "SALESFORCE"}
CALL_TYPES = {"AD_CALL", "CLICK_TO_CALL", "WEBSITE_CALL"}
TAG_TYPES = {"WEBPAGE", "WEBPAGE_CODELESS"}                  # a tag on the site, as opposed to an import
GA4_PREFIX = "GOOGLE_ANALYTICS_4"
LEAD_CATEGORIES = {"SUBMIT_LEAD_FORM", "REQUEST_QUOTE", "CONTACT", "SIGNUP", "BOOK_APPOINTMENT"}
VALUE_BIDDING = {"MAXIMIZE_CONVERSION_VALUE", "TARGET_ROAS"}
SMART_BIDDING = {"MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS"}
LAST_CLICK = {"GOOGLE_ADS_LAST_CLICK", "LAST_CLICK"}
EXTERNAL_ATTRIBUTION = "EXTERNAL"             # an imported action: its model is set in the tool it came from (GA4), not in Google Ads
ACTIVE_STATES = {"ENABLED"}
BAD_ACCOUNT = {"SUSPENDED", "CANCELED", "CLOSED"}
MAX_PRIMARY = 6                                              # primary actions on the site or in imports before bidding is spread thin
LOW_OPTIMIZATION = 50
LONG_ENOUGH_CLICK_WINDOW = 30

model.section("settings", "Account and settings", "google", 1)
model.section("conversions", "Conversion tracking", "google", 2)
model.section("links", "Links and audiences", "google", 3)
model.section("structure", "Structure and bidding", "google", 4)
model.section("keywords", "Keywords and search terms", "google", 5)
model.section("ads", "Ads and landing pages", "google", 6)

CHECKS = []                                                  # (check id, function), in the order they run


def check(cid, section, title, severity, why, fix):
    """Declares a check: registers its catalog entry and the function that decides it."""
    model.register([{"id": cid, "section": section, "title": title, "severity": severity, "why": why, "fix": fix}])

    def wrap(fn):
        CHECKS.append((cid, fn))
        return fn
    return wrap


def run(read, only=None):
    """Every check on one account's read, in order: a list of findings. A check that raises is reported as unknown (and logged) so
    one bug never takes the whole audit down. `only` limits the run to some check ids (for tests)."""
    ctx = Ctx(read)
    out = []
    for cid, fn in CHECKS:
        if only is not None and cid not in only:
            continue
        try:
            out.append(fn(ctx))
        except Exception as e:                      # a bug in one check
            log.exception("account audit: check %s failed", cid)
            out.append(unknown(cid, "the check itself failed (%s)" % type(e).__name__))
    return out


class Ctx:
    """What a check looks at: the read, and a few numbers every check needs. Reading a part that failed gives None."""

    def __init__(self, read):
        self.read = read
        self.customer = read.get("customer") or {}
        self.ok = read.get("ok") or {}
        self.actions = read.get("conversions")
        self.camps = (read.get("campaigns") or {}).get("campaigns") if read.get("campaigns") else None
        self.counts = (read.get("campaigns") or {}).get("counts") or {}
        totals = (read.get("campaigns") or {}).get("totals") if read.get("campaigns") else None
        self.spend = None if totals is None else totals["cost"]
        self.conversions = None if totals is None else totals["conversions"]
        self.currency = self.customer.get("currency") or ""
        self.min_spend = model.min_spend(self.currency)

    def money(self, x):
        return "%s %s" % (self.currency, format(round(x), ",")) if self.currency else format(round(x), ",")

    def search_camps(self):
        return [c for c in self.camps or [] if c["type"] == "SEARCH"]


# ── Account and settings ────────────────────────────────────────────────────

@check("G-SET-01", "settings", "The account is active", "critical",
       "A suspended, cancelled or closed account serves nothing, and a test account never reaches real people.",
       "Find out why the account is not active in Google Ads (billing, a policy suspension, a closed account) and fix that before anything else.")
def set_active(ctx):
    status = ctx.customer.get("status")
    if ctx.customer.get("test"):
        return make("G-SET-01", "fail", "This is a test account. Ads in a test account are never shown to real people.")
    if status in ACTIVE_STATES:
        return make("G-SET-01", "pass", "The account is enabled.")
    if status in BAD_ACCOUNT:
        return make("G-SET-01", "fail", "The account status is %s." % status.lower())
    return unknown("G-SET-01", "Google did not give the account's status (it said %s)" % (status or "nothing"))


@check("G-SET-02", "settings", "Auto-tagging is on", "high",
       "Auto-tagging adds the click id that links an ad click to a Google Analytics session and to an offline conversion. Without it, "
       "conversions imported from GA4 or a CRM cannot be matched to the click and reports lose the source.",
       "Turn on auto-tagging in the account settings. If a tracking template is used as well, keep the click id parameter in it.")
def set_autotag(ctx):
    if ctx.customer.get("autoTagging"):
        return make("G-SET-02", "pass", "Auto-tagging is on.")
    return make("G-SET-02", "fail", "Auto-tagging is off.")


@check("G-SET-03", "settings", "Conversion tracking has an owner", "critical",
       "Smart Bidding, reports and the audit's own numbers all depend on conversions being recorded. An account with no conversion tracking "
       "setting bids blind.",
       "Set up conversion tracking in the account (or link it to the manager account that owns it) before spending more.")
def set_tracking(ctx):
    status = ctx.customer.get("trackingStatus")
    owner = {"CONVERSION_TRACKING_MANAGED_BY_SELF": "this account",
             "CONVERSION_TRACKING_MANAGED_BY_THIS_MANAGER": "the manager account above it",
             "CONVERSION_TRACKING_MANAGED_BY_ANOTHER_MANAGER": "another manager account"}.get(status)
    if owner:
        return make("G-SET-03", "pass", "Conversion tracking is managed by %s." % owner)
    if status == "NOT_CONVERSION_TRACKED":
        return make("G-SET-03", "fail", "The account has no conversion tracking set up.")
    return unknown("G-SET-03", "Google did not say who manages conversion tracking")


@check("G-SET-04", "settings", "A Google tag is installed", "low",
       "The Google tag (AW id) on the site records conversions and builds remarketing lists. Without one the account can only learn from "
       "imports.",
       "Install the Google tag on every page of the site, directly or through Tag Manager, and confirm it with the tag diagnostics.")
def set_tag(ctx):
    if ctx.customer.get("trackingStatus") == "CONVERSION_TRACKING_MANAGED_BY_ANOTHER_MANAGER":
        return na("G-SET-04", "Conversion tracking belongs to another manager account, whose tag is the one that matters.")
    if ctx.customer.get("googleTagId"):
        return make("G-SET-04", "pass", "The account has a Google tag (%s)." % ctx.customer["googleTagId"])
    return make("G-SET-04", "warn", "No Google tag id (AW-...) is set on the account.")


@check("G-SET-05", "settings", "Enhanced conversions for leads are on and allowed", "medium",
       "Enhanced conversions match hashed form details to signed-in users, which recovers conversions a browser would otherwise lose. "
       "They only work after the customer data terms are accepted.",
       "Turn on enhanced conversions for leads and accept the customer data terms, if the client's privacy rules allow it. Healthcare and "
       "other regulated clients often cannot send form details, so confirm with the client first.")
def set_enhanced(ctx):
    on, terms = ctx.customer.get("enhancedLeads"), ctx.customer.get("dataTerms")
    if on and terms:
        return make("G-SET-05", "pass", "Enhanced conversions for leads are on and the data terms are accepted.")
    if on and not terms:
        return make("G-SET-05", "warn", "Enhanced conversions for leads are on, but the customer data terms are not accepted, so they cannot work.")
    return make("G-SET-05", "warn", "Enhanced conversions for leads are off.")


@check("G-SET-06", "settings", "Call reporting is on when calls are tracked", "medium",
       "Calls from ads and from the site only record conversions when call reporting is on for the account.",
       "Turn on call reporting and call conversion reporting in the account settings.")
def set_calls(ctx):
    if ctx.actions is None:
        return unknown("G-SET-06", "the conversion actions could not be read")
    calls = [a for a in ctx.actions if a["status"] in ACTIVE_STATES and a["type"] in CALL_TYPES]
    if not calls:
        return na("G-SET-06", "No call conversion action is enabled.")
    if ctx.customer.get("callReporting") and ctx.customer.get("callConversions"):
        return make("G-SET-06", "pass", "Call reporting and call conversion reporting are on (%s)." % plural(len(calls), "call conversion action"))
    return make("G-SET-06", "fail", "%s enabled but call reporting is %s." %
                (plural(len(calls), "call conversion action") + (" is" if len(calls) == 1 else " are"),
                 "off" if not ctx.customer.get("callReporting") else "on without call conversion reporting"))


@check("G-SET-07", "settings", "Google's optimization score is not low", "low",
       "The optimization score is Google's own measure of how many of its recommendations the account has applied. It is a prompt to look, "
       "not a target to chase, and many recommendations do not suit every client.",
       "Open the recommendations, apply the ones that fit the client's goals and dismiss the rest with a reason.")
def set_score(ctx):
    score = ctx.customer.get("optimizationScore")
    if score is None:
        return unknown("G-SET-07", "Google did not give a score")
    if score < LOW_OPTIMIZATION:
        return make("G-SET-07", "warn", "The optimization score is %d%%." % round(score))
    return make("G-SET-07", "pass", "The optimization score is %d%%." % round(score))


# ── Conversion tracking ─────────────────────────────────────────────────────



GOOGLE_DEFAULT_TYPES = {"ANDROID_INSTALLS_ALL_OTHER_APPS", "GOOGLE_HOSTED", "UNKNOWN", "UNSPECIFIED", ""}


def _real(a):
    """An enabled conversion action that is the client's own measurement (a tag, an import, a call, a lead form, an app or a store), not one of
    Google's defaults (the all-other-apps install, Google-hosted local actions, Smart campaign actions, YouTube follow-on views)."""
    return a["status"] in ACTIVE_STATES and a["type"] not in GOOGLE_DEFAULT_TYPES and not a["type"].startswith("SMART_CAMPAIGN")


def _primary(ctx):
    return [a for a in ctx.actions if _real(a) and a["primary"]]


def _is_ga4(a):
    return a["type"].startswith(GA4_PREFIX)


def _site_based(a):
    """Counted from a tag on the site or an import from GA4 (the actions whose counting and window settings are the client's choice)."""
    return a["type"] in TAG_TYPES or _is_ga4(a)


@check("G-CNV-01", "conversions", "There is an enabled primary conversion action", "critical",
       "Bidding optimises towards the primary conversion actions. With none, Smart Bidding has nothing to learn from and reports show "
       "no results.",
       "Create or import the conversion that matters to the client (a form, a call, a booking, a purchase) and mark it primary.")
def cnv_primary(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-01", "the conversion actions could not be read")
    p = _primary(ctx)
    if not p:
        return make("G-CNV-01", "fail", "No enabled primary conversion action was found (%s in the account)." % plural(len(ctx.actions), "action"))
    shown = ", ".join(a["name"] for a in p[:3]) + (" and %d more" % (len(p) - 3) if len(p) > 3 else "")
    return make("G-CNV-01", "pass", "%s: %s." % (plural(len(p), "enabled primary conversion action"), shown))


@check("G-CNV-02", "conversions", "Conversions were recorded in the last 30 days", "critical",
       "An account that spends and records nothing is either not converting or not measuring. Either way the bids are being set without "
       "feedback.",
       "Check the tag with Tag Assistant, submit a test conversion, and compare the platform's count with the client's own leads.")
def cnv_recorded(ctx):
    if ctx.spend is None:
        return unknown("G-CNV-02", "the campaigns' numbers could not be read")
    if ctx.spend < ctx.min_spend:
        return na("G-CNV-02", "Only %s was spent in the last 30 days, too little to expect conversions." % ctx.money(ctx.spend))
    if ctx.conversions > 0:
        return make("G-CNV-02", "pass", "%s conversions were recorded on %s of spend in the last 30 days." % (format(round(ctx.conversions), ","), ctx.money(ctx.spend)))
    return make("G-CNV-02", "fail", "%s was spent in the last 30 days and no conversion was recorded." % ctx.money(ctx.spend))


@check("G-CNV-03", "conversions", "Primary conversion actions that tag or import data are recording", "high",
       "A primary action that has recorded nothing in 30 days is usually a tag that fell off the page, a form that changed, or an import that "
       "stopped. If it is the only primary action, bidding is running without data.",
       "Open each action's tag status, test the form or page it counts, and fix or remove the action.")
def cnv_silent(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-03", "the conversion actions could not be read")
    if ctx.spend is not None and ctx.spend < ctx.min_spend:
        return na("G-CNV-03", "Only %s was spent in the last 30 days, too little to expect an action to record anything." % ctx.money(ctx.spend))
    cand = [a for a in _primary(ctx) if a["type"] in WEB_TYPES]
    if not cand:
        return na("G-CNV-03", "No primary action counts a tag, an import or an upload.")
    if any(a["allConversions"] is None for a in cand):
        return unknown("G-CNV-03", "the conversions of the last 30 days could not be read")
    silent = [a for a in cand if a["allConversions"] == 0]
    if not silent:
        return make("G-CNV-03", "pass", "Checked %s: all recorded conversions in the last 30 days." % plural(len(cand), "primary tag and import action"))
    found = "%d of %s recorded nothing in the last 30 days." % (len(silent), plural(len(cand), "primary tag and import action"))
    return make("G-CNV-03", "fail" if len(silent) == len(cand) else "warn", found, items=names(silent))


@check("G-CNV-04", "conversions", "Lead actions count one conversion per click", "medium",
       "A form counted every time it is submitted makes one person who fills it in twice look like two leads, which inflates conversions and "
       "teaches Smart Bidding to chase repeat submitters.",
       "Set the counting of lead actions to One per click (or per conversion) unless every submission is genuinely a separate lead.")
def cnv_lead_counting(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-04", "the conversion actions could not be read")
    lead = [a for a in _primary(ctx) if _site_based(a) and a["category"] in LEAD_CATEGORIES]
    if not lead:
        return na("G-CNV-04", "No primary lead action is counted from a tag or a GA4 import.")
    many = [a for a in lead if a["counting"] == "MANY_PER_CLICK"]
    if not many:
        return make("G-CNV-04", "pass", "Checked %s: none counts every submission." % plural(len(lead), "primary lead action"))
    return make("G-CNV-04", "warn", "%d of %s %s every submission." % (len(many), plural(len(lead), "primary lead action"), verb(len(many), "count")), items=names(many))


@check("G-CNV-05", "conversions", "Purchase actions count every purchase", "medium",
       "A purchase counted once per click misses repeat orders in the same visit, so revenue and return on ad spend read low.",
       "Set the counting of purchase actions to Every.")
def cnv_purchase_counting(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-05", "the conversion actions could not be read")
    buy = [a for a in _primary(ctx) if _site_based(a) and a["category"] == "PURCHASE"]
    if not buy:
        return na("G-CNV-05", "No primary purchase action is counted from a tag or a GA4 import.")
    once = [a for a in buy if a["counting"] == "ONE_PER_CLICK"]
    if not once:
        return make("G-CNV-05", "pass", "Checked %s: none counts one per click." % plural(len(buy), "primary purchase action"))
    return make("G-CNV-05", "warn", "%d of %s %s one per click." % (len(once), plural(len(buy), "primary purchase action"), verb(len(once), "count")), items=names(once))


@check("G-CNV-06", "conversions", "The same conversion is not counted by a tag and a GA4 import", "high",
       "When a Google Ads tag and a GA4 import both count the same kind of event as primary, one lead can be counted twice and bids "
       "are set on inflated numbers.",
       "Keep one source per conversion. Make one of the two secondary, or remove it.")
def cnv_double(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-06", "the conversion actions could not be read")
    p = [a for a in _primary(ctx) if a["included"]]
    tags = {a["category"] for a in p if a["type"] in TAG_TYPES}
    ga4 = {a["category"] for a in p if _is_ga4(a)}
    if not tags and not ga4:
        return na("G-CNV-06", "No primary action is counted from a tag or a GA4 import.")
    both = sorted(tags & ga4)
    if not both:
        return make("G-CNV-06", "pass", "No kind of event is counted by both a Google Ads tag and a GA4 import.")
    hit = [a for a in p if a["category"] in both and (a["type"] in TAG_TYPES or _is_ga4(a))]
    return make("G-CNV-06", "warn", "Counted by both a tag and a GA4 import: %s." % ", ".join(x.lower().replace("_", " ") for x in both), items=names(hit))


@check("G-CNV-07", "conversions", "Value bidding has conversion values to work with", "high",
       "Maximise conversion value and target ROAS optimise towards the value of each conversion. If every conversion has no value the strategy "
       "has nothing to optimise and performs like a guess.",
       "Give each primary action a value (a fixed value per lead is enough) or move the campaigns to a conversion-count strategy.")
def cnv_value(ctx):
    if ctx.camps is None or ctx.actions is None:
        return unknown("G-CNV-07", "the campaigns or the conversion actions could not be read")
    bidders = [x for x in ctx.camps if x["bidding"] in VALUE_BIDDING]
    if not bidders:
        return na("G-CNV-07", "No enabled campaign bids on conversion value.")
    if ctx.spend is not None and ctx.spend < ctx.min_spend:
        return na("G-CNV-07", "Only %s was spent in the last 30 days, too little to expect conversion values." % ctx.money(ctx.spend))
    p = _primary(ctx)
    if any(a["value"] is None for a in p):
        return unknown("G-CNV-07", "the conversion values of the last 30 days could not be read")
    total = sum(a["value"] for a in p)
    if total > 0:
        return make("G-CNV-07", "pass", "Campaigns bidding on conversion value: %d. The primary actions recorded %s of value." % (len(bidders), ctx.money(total)))
    return make("G-CNV-07", "fail", "Campaigns bidding on conversion value: %d, but the primary actions recorded no value in the last 30 days." % len(bidders), items=names(bidders))


@check("G-CNV-08", "conversions", "Primary actions use data-driven attribution", "low",
       "Last-click credits only the final ad clicked. Data-driven attribution spreads credit across the clicks that led to the conversion, "
       "which suits Smart Bidding better.",
       "Switch primary actions to data-driven attribution where Google offers it.")
def cnv_attribution(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-08", "the conversion actions could not be read")
    p = [a for a in _primary(ctx) if a["attribution"] and a["attribution"] != EXTERNAL_ATTRIBUTION]
    if not p:
        return na("G-CNV-08", "No primary action has an attribution model that is set in Google Ads.")
    last = [a for a in p if a["attribution"] in LAST_CLICK]
    if not last:
        return make("G-CNV-08", "pass", "No primary action uses last-click attribution.")
    return make("G-CNV-08", "warn", "%d of %s %s last-click attribution." % (len(last), plural(len(p), "primary action"), verb(len(last), "use")), items=names(last))


@check("G-CNV-09", "conversions", "Click lookback windows are long enough", "low",
       "A window shorter than a month drops conversions of people who take time to decide, which matters for higher-consideration services.",
       "Set the click-through window to 30 days or more for lead and purchase actions.")
def cnv_window(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-09", "the conversion actions could not be read")
    p = [a for a in _primary(ctx) if _site_based(a)]
    if not p:
        return na("G-CNV-09", "No primary action is counted from a tag or a GA4 import.")
    short = [a for a in p if 0 < a["clickWindow"] < LONG_ENOUGH_CLICK_WINDOW]
    if not short:
        return make("G-CNV-09", "pass", "Checked %s: all look back %d days or more." % (plural(len(p), "primary tag and import action"), LONG_ENOUGH_CLICK_WINDOW))
    return make("G-CNV-09", "warn", "Primary actions with a click window under %d days: %d." % (LONG_ENOUGH_CLICK_WINDOW, len(short)),
                items=["%s (%d days)" % (a["name"], a["clickWindow"]) for a in short])


@check("G-CNV-10", "conversions", "Not too many primary conversion actions", "low",
       "Every primary action is something bidding optimises towards. Many of them dilute the signal and make the conversion column hard to read.",
       "Keep the few actions that mark a real lead or sale as primary and make the supporting ones (page views, clicks to call) secondary.")
def cnv_many(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-10", "the conversion actions could not be read")
    p = [a for a in _primary(ctx) if a["type"] in WEB_TYPES]
    if len(p) <= MAX_PRIMARY:
        return make("G-CNV-10", "pass", "%s." % plural(len(p), "primary tag and import action"))
    return make("G-CNV-10", "warn", "%s, more than %d." % (plural(len(p), "primary tag and import action"), MAX_PRIMARY), items=names(p))


@check("G-CNV-11", "conversions", "Google Analytics 4 conversions are imported", "medium",
       "Linking GA4 lets the account use the site's own events as conversions and audiences. Google's API does not show the link itself, so "
       "this can only be seen through the conversions imported from it.",
       "In Google Ads open Linked accounts and check the GA4 link, then import the key events that matter as conversions.")
def cnv_ga4(ctx):
    if ctx.actions is None:
        return unknown("G-CNV-11", "the conversion actions could not be read")
    ga4 = [a for a in ctx.actions if _is_ga4(a) and a["status"] in ACTIVE_STATES]
    if ga4:
        return make("G-CNV-11", "pass", "Conversion actions imported from GA4: %d, so the link exists." % len(ga4))
    return unknown("G-CNV-11", "Google's API does not show the GA4 link, and no conversion action is imported from GA4")


# ── Links and audiences ─────────────────────────────────────────────────────

RETAIL_TYPES = {"SHOPPING"}
GEO_ELSEWHERE = {"LOCAL_SERVICES", "DEMAND_GEN"}   # locations are set per ad group (Demand Gen) or by Google (Local Services), not on the campaign


def _retail(ctx):
    return [x for x in ctx.camps if x["type"] in RETAIL_TYPES or (x["type"] == "PERFORMANCE_MAX" and x["merchantId"])]


@check("G-LNK-01", "links", "Shopping campaigns have their Merchant Center linked", "high",
       "A Shopping or retail Performance Max campaign serves from a Merchant Center product feed. Without the link, or with a link to a "
       "different Merchant Center than the one named in the campaign, it has no products to show.",
       "Link the Merchant Center account in Google Ads (Linked accounts) and check each shopping campaign points at it.")
def lnk_merchant(ctx):
    if ctx.camps is None:
        return unknown("G-LNK-01", "the campaigns could not be read")
    retail = _retail(ctx)
    if not retail:
        return na("G-LNK-01", "No enabled campaign serves products from a Merchant Center feed.")
    links = ctx.read.get("links")
    if links is None:
        return unknown("G-LNK-01", "the product links could not be read")
    if not links["counts"].get("MERCHANT_CENTER"):
        return make("G-LNK-01", "fail", "%s serve products but no Merchant Center account is linked." % plural(len(retail), "campaign"), items=names(retail))
    ids = links["merchantCenter"]
    if ids is not None:
        off = [x for x in retail if x["merchantId"] and x["merchantId"] not in ids]
        if off:
            return make("G-LNK-01", "fail", "%s point at a Merchant Center that is not linked (linked: %s)." % (plural(len(off), "campaign"), ", ".join(ids) or "none"),
                        items=["%s (Merchant Center %s)" % (x["name"], x["merchantId"]) for x in off])
    return make("G-LNK-01", "pass", "%s serve products and a Merchant Center account is linked." % plural(len(retail), "campaign"))


@check("G-LNK-02", "links", "Audience lists are collecting members", "low",
       "Remarketing and customer lists only help once they have people in them. Empty lists mean the tag or the upload is not feeding them.",
       "Check that the Google tag fires on the pages that build each list and that customer lists are uploaded. Some regulated sectors may not "
       "be allowed to build remarketing lists, so confirm the client's policy.")
def lnk_lists(ctx):
    lists = ctx.read.get("lists")
    if lists is None:
        return unknown("G-LNK-02", "the audience lists could not be read")
    if lists["total"] == 0:
        return make("G-LNK-02", "warn", "The account has no audience lists.")
    if lists["open"] == 0:
        return make("G-LNK-02", "warn", "No audience list is open to new members (%s, all closed)." % plural(lists["total"], "list"))
    if lists["withMembers"] == 0:
        return make("G-LNK-02", "warn", "None of the %s open audience lists has any members." % lists["open"], items=[e["name"] for e in lists["examples"] if e["kind"] == "empty"])
    return make("G-LNK-02", "pass", "%d of %s open audience lists have members." % (lists["withMembers"], lists["open"]))


@check("G-LNK-03", "links", "Audience lists are big enough to serve on Search", "low",
       "Google only serves a list on Search once it has 1,000 members. A smaller list is collected but does nothing for Search targeting or bids.",
       "Let the list grow, widen the rule that builds it, or use it for Display and YouTube, where the minimum is lower.")
def lnk_small(ctx):
    lists = ctx.read.get("lists")
    if lists is None:
        return unknown("G-LNK-03", "the audience lists could not be read")
    if lists["withMembers"] == 0:
        return na("G-LNK-03", "No audience list has members yet.")
    if lists["tooSmallForSearch"] == 0:
        return make("G-LNK-03", "pass", "No open list is under the %s-member Search minimum." % format(SEARCH_LIST_MIN, ","))
    return make("G-LNK-03", "warn", "%s under the Search minimum of %s members." % (plural(lists["tooSmallForSearch"], "open list") + (" is" if lists["tooSmallForSearch"] == 1 else " are"),
                                                                                      format(SEARCH_LIST_MIN, ",")),
                items=["%s (%s for Search)" % (e["name"], format(e["size"], ",")) for e in lists["examples"] if e["kind"] == "small"])



# ── Structure and bidding ───────────────────────────────────────────────────

BUDGET_LOST_PCT = 20                          # impression share lost to budget at which a campaign is held back
RANK_LOST_PCT = 40
RANK_LOST_MIN_SHARE = 5                       # a campaign must carry this share of spend for rank loss to matter
PARTNER_MIN_SHARE = 5
PARTNER_WORSE_BY = 1.5
NO_CONVERSION_CLICKS = 50
NO_CONVERSION_FAIL_SHARE = 25
PAUSED_CLUTTER = 50
CONVERSION_TYPES = {"SEARCH", "PERFORMANCE_MAX", "SHOPPING", "DEMAND_GEN"}
CLICK_BIDDING = {"MANUAL_CPC", "TARGET_SPEND", "MANUAL_CPM", "ENHANCED_CPC"}
MIN_CONVERSIONS = {"cpa": 30, "roas": 50, "smart": 15}
BAD_PRIMARY = {"MISCONFIGURED", "NOT_ELIGIBLE"}
ISSUE_REASONS = {"HAS_ADS_DISAPPROVED", "HAS_ADS_LIMITED_BY_POLICY", "HAS_ASSET_GROUPS_DISAPPROVED", "HAS_ASSET_GROUPS_LIMITED_BY_POLICY",
                 "NO_AD_GROUP_ADS", "NO_ACTIVE_AD_GROUPS", "MISSING_LEAD_FORM_EXTENSION", "MISSING_CALL_EXTENSION", "PENDING_REVIEW"}


def _say(reason):
    return str(reason).lower().replace("_", " ")


@check("G-STR-01", "structure", "Every campaign targets locations", "critical",
       "A campaign with no location target is eligible in every country and territory. The budget then spreads across places the client "
       "cannot serve.",
       "Add the locations (or a radius around each site) to each campaign, and exclude the places the client does not serve.")
def str_locations(ctx):
    if ctx.camps is None:
        return unknown("G-STR-01", "the campaigns could not be read")
    camps = [x for x in ctx.camps if x["type"] not in GEO_ELSEWHERE]
    if not camps:
        return na("G-STR-01", "No enabled campaign takes its locations from the campaign (Demand Gen sets them on each ad group).")
    if any(x["targeting"] is None for x in camps):
        return unknown("G-STR-01", "the campaigns' locations could not be read")
    open_ = [x for x in camps if not x["targeting"]["locations"] and not x["targeting"]["proximities"]]
    if not open_:
        return make("G-STR-01", "pass", "Checked %s: all target at least one location or radius." % plural(len(camps), "enabled campaign"))
    return make("G-STR-01", "fail", "Campaigns with no location target: %d of %s. They can show anywhere." % (len(open_), plural(len(camps), "enabled campaign")), items=names(open_))


@check("G-STR-02", "structure", "Search campaigns do not run on the Display Network", "high",
       "Search campaigns that also run on the Display Network spend on pages where people are not searching, at lower intent, and mix the "
       "numbers so the search results cannot be read cleanly.",
       "Turn off the Display Network in each Search campaign's network settings. Run Display as its own campaign if it is wanted.")
def str_display(ctx):
    if ctx.camps is None:
        return unknown("G-STR-02", "the campaigns could not be read")
    search = ctx.search_camps()
    if not search:
        return na("G-STR-02", "No enabled Search campaign.")
    on = [x for x in search if x["displayNetwork"]]
    if not on:
        return make("G-STR-02", "pass", "Checked %s: none opts into the Display Network." % plural(len(search), "Search campaign"))
    net = ctx.read.get("network")
    spend = ""
    if net and net["total"]:
        spend = " %d%% of Search spend in the last 30 days ran on the Display Network." % pct(net["cost"].get("CONTENT", 0), net["total"])
    return make("G-STR-02", "fail", "%d of %s opt into the Display Network.%s" % (len(on), plural(len(search), "Search campaign"), spend), items=names(on))


@check("G-STR-03", "structure", "Search Partners are not costing more per conversion", "low",
       "Search Partners are other sites that show Google search ads. Their traffic is often cheaper and weaker. If it costs clearly more per "
       "conversion it is worth turning off.",
       "Compare cost per conversion on Google Search and on Search Partners in the network report, and turn Search Partners off where it is worse.")
def str_partners(ctx):
    net = ctx.read.get("network")
    if net is None:
        return unknown("G-STR-03", "where Search spend ran could not be read")
    if not net["total"]:
        return na("G-STR-03", "No Search spend in the last 30 days.")
    cost_p, conv_p = net["cost"].get("SEARCH_PARTNERS", 0.0), net["conversions"].get("SEARCH_PARTNERS", 0.0)
    cost_s, conv_s = net["cost"].get("SEARCH", 0.0), net["conversions"].get("SEARCH", 0.0)
    taken = pct(cost_p, net["total"])
    if share(cost_p, net["total"]) < PARTNER_MIN_SHARE:
        return make("G-STR-03", "pass", "Search Partners took %d%% of Search spend, a small share." % taken)
    cpa_s = cost_s / conv_s if conv_s else None
    cpa_p = cost_p / conv_p if conv_p else None
    if conv_p == 0 or (cpa_s is not None and cpa_p is not None and cpa_p >= PARTNER_WORSE_BY * cpa_s):
        how = "recorded no conversions" if conv_p == 0 else "cost %s per conversion against %s on Google Search" % (ctx.money(cpa_p), ctx.money(cpa_s))
        return make("G-STR-03", "warn", "Search Partners took %d%% of Search spend (%s) and %s." % (taken, ctx.money(cost_p), how))
    return make("G-STR-03", "pass", "Search Partners took %d%% of Search spend at a cost per conversion in line with Google Search." % taken)


@check("G-STR-04", "structure", "Campaigns that convert are not held back by budget", "medium",
       "A campaign that runs out of budget while it is converting leaves conversions on the table, and its Smart Bidding learns from a "
       "partial day.",
       "Raise the budget of the campaigns that convert at an acceptable cost, funded from the ones that do not.")
def str_budget(ctx):
    if ctx.camps is None:
        return unknown("G-STR-04", "the campaigns could not be read")
    live = [x for x in ctx.camps if x["type"] in CONVERSION_TYPES]
    if not live:
        return na("G-STR-04", "No enabled Search, Shopping, Performance Max or Demand Gen campaign.")
    if any(x["metrics"] is None for x in live):
        return unknown("G-STR-04", "the campaigns' numbers could not be read")
    held = []
    for x in live:
        m = x["metrics"]
        lost = m["budgetLost"]
        limited = "BUDGET_CONSTRAINED" in x["reasons"] or (lost is not None and lost >= BUDGET_LOST_PCT)
        if limited and m["conversions"] > 0:
            held.append((x, lost))
    if not held:
        return make("G-STR-04", "pass", "No converting campaign is held back by budget.")
    held.sort(key=lambda h: (-h[0]["metrics"]["cost"], h[0]["name"]))
    return make("G-STR-04", "warn", "%s converting and held back by budget." % (plural(len(held), "campaign") + (" is" if len(held) == 1 else " are")),
                items=["%s%s" % (x["name"], " (%d%% of impressions lost to budget)" % round(lost) if lost is not None else " (budget limited)") for x, lost in held])


@check("G-STR-05", "structure", "No campaign is flagged by Google as misconfigured or limited by policy", "high",
       "Google shows a campaign status and the reasons behind it. A misconfigured campaign, a missing ad group or a policy limit means the "
       "campaign is not serving as built.",
       "Open each campaign's status in Google Ads, read the reason Google gives and fix it (an ad group without ads, a disapproved ad, a missing asset).")
def str_status(ctx):
    if ctx.camps is None:
        return unknown("G-STR-05", "the campaigns could not be read")
    bad = [x for x in ctx.camps if x["primaryStatus"] in BAD_PRIMARY]
    warn = [x for x in ctx.camps if x["primaryStatus"] not in BAD_PRIMARY and any(r in ISSUE_REASONS for r in x["reasons"])]
    if not bad and not warn:
        return make("G-STR-05", "pass", "No enabled campaign shows a misconfigured, not eligible or policy-limited status.")

    def line(x):
        why = [_say(r) for r in x["reasons"] if r in ISSUE_REASONS] or [_say(x["primaryStatus"])]
        return "%s (%s)" % (x["name"], ", ".join(why))
    if bad:
        return make("G-STR-05", "fail", "%d campaigns are %s." % (len(bad), " or ".join(sorted({_say(x["primaryStatus"]) for x in bad}))), items=[line(x) for x in bad + warn])
    return make("G-STR-05", "warn", "%s limited by policy or missing something Google needs." % (plural(len(warn), "campaign") + (" is" if len(warn) == 1 else " are")),
                items=[line(x) for x in warn], severity="medium")


@check("G-STR-06", "structure", "No campaign is still learning", "low",
       "A campaign in its learning period reads high and low until the strategy settles, and changes made then restart the learning.",
       "Leave campaigns that are learning alone for a couple of weeks, and avoid stacking bid, budget and conversion changes together.")
def str_learning(ctx):
    if ctx.camps is None:
        return unknown("G-STR-06", "the campaigns could not be read")
    learning = [x for x in ctx.camps if x["primaryStatus"] == "LEARNING" or any(r.endswith("_LEARNING") for r in x["reasons"])]
    if not learning:
        return make("G-STR-06", "pass", "No enabled campaign is in a learning period.")
    return make("G-STR-06", "warn", "%s in a learning period." % (plural(len(learning), "campaign") + (" is" if len(learning) == 1 else " are")), items=names(learning))


def _needs(x):
    """How many conversions in 30 days Google suggests a campaign's bidding strategy has, or None when it has no such need."""
    if x["targetKind"] == "cpa" or x["bidding"] == "TARGET_CPA":
        return MIN_CONVERSIONS["cpa"]
    if x["targetKind"] == "roas" or x["bidding"] == "TARGET_ROAS":
        return MIN_CONVERSIONS["roas"]
    if x["bidding"] in ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE"):
        return MIN_CONVERSIONS["smart"]
    return None


@check("G-STR-07", "structure", "Smart Bidding campaigns have enough conversions to learn from", "high",
       "Smart Bidding sets each bid from past conversions. Google suggests about 30 conversions a month for a target CPA, 50 for a target ROAS "
       "and 15 for maximise conversions. With fewer, bids swing and targets are missed.",
       "Merge thin campaigns so they share conversions, use a portfolio strategy, or count a nearer step such as a form start as a secondary signal.")
def str_smart_data(ctx):
    if ctx.camps is None:
        return unknown("G-STR-07", "the campaigns could not be read")
    smart = [x for x in ctx.camps if x["type"] in CONVERSION_TYPES and _needs(x) is not None]
    if not smart:
        return na("G-STR-07", "No enabled campaign uses a conversion-based bidding strategy.")
    if any(x["metrics"] is None for x in smart):
        return unknown("G-STR-07", "the campaigns' numbers could not be read")
    pool = {}                                                   # a portfolio strategy learns from all its campaigns together
    for x in smart:
        if x["portfolio"]:
            p = pool.setdefault(x["portfolio"], {"conversions": 0.0, "cost": 0.0, "campaigns": 0})
            p["conversions"] += x["metrics"]["conversions"]
            p["cost"] += x["metrics"]["cost"]
            p["campaigns"] += 1

    def seen(x):
        p = pool.get(x["portfolio"]) if x["portfolio"] else None
        return (p["conversions"], p["cost"]) if p else (x["metrics"]["conversions"], x["metrics"]["cost"])
    thin = [x for x in smart if seen(x)[1] > 0 and seen(x)[0] < _needs(x)]
    if not thin:
        return make("G-STR-07", "pass", "Checked %s: all have the conversions Google suggests." % plural(len(smart), "Smart Bidding campaign"))
    thin.sort(key=lambda x: (-x["metrics"]["cost"], x["name"]))

    def line(x):
        if x["portfolio"] and pool[x["portfolio"]]["campaigns"] > 1:
            p = pool[x["portfolio"]]
            return "%s (%s together with the other %s on its portfolio strategy, about %d suggested)" % (
                x["name"], plural(round(p["conversions"]), "conversion"), plural(p["campaigns"] - 1, "campaign"), _needs(x))
        return "%s (%s, about %d suggested)" % (x["name"], plural(round(x["metrics"]["conversions"]), "conversion"), _needs(x))
    return make("G-STR-07", "warn", "%d of %s had fewer conversions in 30 days than Google suggests." % (len(thin), plural(len(smart), "Smart Bidding campaign")),
                items=[line(x) for x in thin])


@check("G-STR-08", "structure", "Campaigns with plenty of conversions use Smart Bidding", "medium",
       "Manual and click-based bidding cannot use the conversion signal. A campaign that already records conversions usually does better "
       "when bids are set from them.",
       "Move the campaign to Maximise conversions (or a target CPA once it has run for a few weeks) and watch it for two weeks.")
def str_manual(ctx):
    if ctx.camps is None:
        return unknown("G-STR-08", "the campaigns could not be read")
    click = [x for x in ctx.camps if x["type"] == "SEARCH" and x["bidding"] in CLICK_BIDDING]
    if not click:
        return make("G-STR-08", "pass", "No Search campaign uses manual or click-based bidding.")
    if any(x["metrics"] is None for x in click):
        return unknown("G-STR-08", "the campaigns' numbers could not be read")
    ready = [x for x in click if x["metrics"]["conversions"] >= MIN_CONVERSIONS["smart"]]
    if not ready:
        return make("G-STR-08", "pass", "%s use manual or click-based bidding, none with enough conversions yet to bid on them." % plural(len(click), "Search campaign"))
    ready.sort(key=lambda x: (-x["metrics"]["conversions"], x["name"]))
    return make("G-STR-08", "warn", "%d of %s on manual or click-based bidding already record %d or more conversions a month." %
                (len(ready), plural(len(click), "Search campaign"), MIN_CONVERSIONS["smart"]),
                items=["%s (%s, %s)" % (x["name"], _say(x["bidding"]), plural(round(x["metrics"]["conversions"]), "conversion")) for x in ready])


@check("G-STR-09", "structure", "Campaigns that spend are converting", "high",
       "A campaign with a lot of clicks and no conversions in a month is either not converting or not measuring, and it keeps spending.",
       "Check the landing page and the conversion tracking for each, tighten the targeting, or pause the campaign and move its budget. If conversions "
       "are imported from a CRM or uploaded offline they arrive days or weeks after the click, so recent clicks may not have a conversion yet: look at "
       "the import's delay before pausing anything.")
def str_no_conversions(ctx):
    if ctx.camps is None or ctx.spend is None:
        return unknown("G-STR-09", "the campaigns or their numbers could not be read")
    live = [x for x in ctx.camps if x["type"] in CONVERSION_TYPES]
    if any(x["metrics"] is None for x in live):
        return unknown("G-STR-09", "the campaigns' numbers could not be read")
    dead = [x for x in live if x["metrics"]["clicks"] >= NO_CONVERSION_CLICKS and x["metrics"]["conversions"] == 0]
    if not dead:
        return make("G-STR-09", "pass", "Every campaign with %d or more clicks in 30 days recorded a conversion." % NO_CONVERSION_CLICKS)
    cost = sum(x["metrics"]["cost"] for x in dead)
    exact = share(cost, ctx.spend)
    dead.sort(key=lambda x: (-x["metrics"]["cost"], x["name"]))
    found = "%s %d or more clicks and no conversion in 30 days, %s (%d%% of spend)." % (plural(len(dead), "campaign") + (" has" if len(dead) == 1 else " have"),
                                                                                          NO_CONVERSION_CLICKS, ctx.money(cost), round(exact))
    return make("G-STR-09", "fail" if exact >= NO_CONVERSION_FAIL_SHARE else "warn", found,
                items=["%s (%s, %s clicks)" % (x["name"], ctx.money(x["metrics"]["cost"]), format(x["metrics"]["clicks"], ",")) for x in dead])


@check("G-STR-10", "structure", "Search campaigns are not losing much reach to rank", "medium",
       "Impression share lost to rank means the ad was not shown because its Ad Rank was too low: weak bids, Quality Score, or a weak ad or page.",
       "Improve ad relevance and landing page experience first, then raise the target or bid where the campaign earns it.")
def str_rank(ctx):
    if ctx.camps is None or ctx.spend is None:
        return unknown("G-STR-10", "the campaigns or their numbers could not be read")
    search = ctx.search_camps()
    if not search:
        return na("G-STR-10", "No enabled Search campaign.")
    if any(x["metrics"] is None for x in search):
        return unknown("G-STR-10", "the campaigns' numbers could not be read")
    big = [x for x in search if x["metrics"]["rankLost"] is not None and x["metrics"]["rankLost"] >= RANK_LOST_PCT
           and share(x["metrics"]["cost"], ctx.spend) >= RANK_LOST_MIN_SHARE]
    if not big:
        return make("G-STR-10", "pass", "No big Search campaign loses %d%% or more of its impressions to rank." % RANK_LOST_PCT)
    big.sort(key=lambda x: (-x["metrics"]["cost"], x["name"]))
    return make("G-STR-10", "warn", "%s losing %d%% or more of impressions to rank." % (plural(len(big), "big Search campaign") + (" is" if len(big) == 1 else " are"), RANK_LOST_PCT),
                items=["%s (%d%% lost to rank)" % (x["name"], round(x["metrics"]["rankLost"])) for x in big])


@check("G-STR-11", "structure", "The account is not buried in paused campaigns", "low",
       "Hundreds of paused campaigns make the account slow to read and easy to enable by mistake.",
       "Archive the campaigns that will not come back and keep their results in a report.")
def str_paused(ctx):
    if ctx.camps is None:
        return unknown("G-STR-11", "the campaigns could not be read")
    enabled, paused = ctx.counts["enabled"], ctx.counts["paused"]
    if paused >= PAUSED_CLUTTER and paused >= 2 * enabled:
        return make("G-STR-11", "warn", "%s against %d enabled." % (plural(paused, "paused campaign"), enabled))
    return make("G-STR-11", "pass", "%s and %d enabled." % (plural(paused, "paused campaign"), enabled))


@check("G-STR-12", "structure", "The account has enabled campaigns", "high",
       "An account with no enabled campaign is not advertising. Most of the other checks have nothing to look at, so its score only reflects its settings.",
       "Enable the campaigns that should be running, or audit again once the account is built.")
def str_any(ctx):
    if ctx.camps is None:
        return unknown("G-STR-12", "the campaigns could not be read")
    running = [x for x in ctx.camps if x["primaryStatus"] != "ENDED"]
    if not running:
        what = "No campaign is enabled (%s)" % plural(ctx.counts["paused"], "paused campaign") if not ctx.camps else \
            "No campaign is running (%s ended)" % plural(len(ctx.camps), "enabled campaign")
        return make("G-STR-12", "warn", what + ". The account is not advertising, so most checks do not apply and the score only reflects its settings.",
                    caps="the account is not advertising")
    return make("G-STR-12", "pass", "%s." % plural(len(running), "enabled campaign"))


# ── Keywords and search terms ───────────────────────────────────────────────

QS_LOW_FAIL = 30                              # share of impressions on keywords at Quality Score 4 or less
QS_LOW_WARN = 15
BELOW_AD_RELEVANCE = 35                       # share of scored impressions with a below-average component
BELOW_LANDING_PAGE = 35
BELOW_EXPECTED_CTR = 50
WASTED_KEYWORD_FAIL = 10                      # share of spend on keywords with 15+ clicks and no conversion
WASTED_KEYWORD_WARN = 3
WASTED_TERM_FAIL = 60                         # share of the biggest search terms' spend that never converted
WASTED_TERM_WARN = 35
RARELY_SERVED_SHARE = 10
ONE_AD_FAIL = 75
ONE_AD_WARN = 25
NO_KEYWORD_FAIL = 50


@check("G-KWD-01", "keywords", "Search campaigns use negative keywords", "high",
       "Negative keywords stop the ads showing for searches that cannot convert (jobs, free, other cities, competitors). Without them a "
       "share of the budget goes to the wrong searches every day.",
       "Add negative keyword lists for the account and for each theme from the search terms report. Account-level negatives cannot be read "
       "through Google's API, so check those in the account's settings before adding.")
def kwd_negatives(ctx):
    if ctx.camps is None:
        return unknown("G-KWD-01", "the campaigns could not be read")
    search = ctx.search_camps()
    if not search:
        return na("G-KWD-01", "No enabled Search campaign.")
    if any(x["negatives"] is None or x["lists"] is None for x in search):
        return unknown("G-KWD-01", "the campaigns' negative keywords could not be read")
    bare = [x for x in search if not x["negatives"] and not x["lists"]]
    if not bare:
        return make("G-KWD-01", "pass", "Checked %s: all have negative keywords of their own or a negative keyword list." % plural(len(search), "Search campaign"))
    if len(bare) == len(search):
        return make("G-KWD-01", "warn", "No Search campaign has a negative keyword or a negative keyword list. Negatives set for the whole account cannot be read, so they may exist.",
                    items=names(bare))
    return make("G-KWD-01", "warn", "%d of %s have no negative keyword and no negative keyword list." % (len(bare), plural(len(search), "Search campaign")), items=names(bare))


@check("G-KWD-02", "keywords", "Broad match keywords run under Smart Bidding", "high",
       "Broad match reaches a wide range of searches. Smart Bidding keeps it in check by bidding on the chance each search converts. With "
       "manual bids or click bidding, broad match spends on loosely related searches.",
       "Move the campaign to a conversion-based strategy, or change its broad match keywords to phrase or exact.")
def kwd_broad(ctx):
    kw = ctx.read.get("keywords")
    if kw is None or ctx.camps is None:
        return unknown("G-KWD-02", "the keywords or the campaigns could not be read")
    if not kw["byMatch"].get("BROAD"):
        return make("G-KWD-02", "pass", "No enabled broad match keyword.")
    by_id = {x["id"]: x for x in ctx.camps}
    bad = []
    for cid, n in kw["perCampaign"].items():
        x = by_id.get(cid)
        if x and n["broad"] and x["bidding"] not in SMART_BIDDING:
            bad.append((x, n["broad"]))
    if not bad:
        return make("G-KWD-02", "pass", "%s, all in campaigns that bid with a conversion-based strategy." % plural(kw["byMatch"]["BROAD"], "broad match keyword"))
    bad.sort(key=lambda b: (-b[1], b[0]["name"]))
    return make("G-KWD-02", "fail", "%s in %s without a conversion-based bidding strategy." % (plural(sum(n for _x, n in bad), "broad match keyword"), plural(len(bad), "campaign")),
                items=["%s (%d broad keywords, %s)" % (x["name"], n, _say(x["bidding"])) for x, n in bad])


@check("G-KWD-03", "keywords", "Quality Score is healthy where the ads show", "medium",
       "Quality Score of 4 or less means Google expects a poor click-through rate, a weak ad match or a weak landing page. Those keywords pay "
       "more per click and lose auctions to better ads.",
       "Start with the keywords that carry the most impressions: tighten the ad groups so the ads match the keywords, and improve the landing page.")
def kwd_quality(ctx):
    kw = ctx.read.get("keywords")
    if kw is None or kw["qs"]["impressions"] is None:
        return unknown("G-KWD-03", "the keywords or their impressions could not be read")
    imp = kw["qs"]["impressions"]
    scored = imp["low"] + imp["mid"] + imp["high"]
    if not scored:
        return na("G-KWD-03", "No keyword has a Quality Score with impressions yet.")
    low = share(imp["low"], scored)
    found = "%d%% of impressions on keywords with a Quality Score are on keywords at 4 or less (%s keywords)." % (round(low), format(kw["qs"]["keywords"]["low"], ","))
    examples = ["%s (Quality Score %d, %s impressions, %s)" % (e["keyword"], e["qs"], format(e["impressions"], ","), e["campaign"]) for e in kw["lowQualityExamples"]]
    if low >= QS_LOW_FAIL:
        return make("G-KWD-03", "fail", found, items=examples)
    if low >= QS_LOW_WARN:
        return make("G-KWD-03", "warn", found, items=examples)
    return make("G-KWD-03", "pass", found)


@check("G-KWD-04", "keywords", "Quality Score parts point to the ads and the landing pages", "medium",
       "Quality Score has three parts: expected click-through rate, ad relevance and landing page experience. A large share of impressions "
       "below average on one of them says which to fix.",
       "Ad relevance: write ads that use the keywords and split ad groups by theme. Landing page: match the page to the search and speed it up. "
       "Expected click-through rate: test stronger headlines and add assets.")
def kwd_components(ctx):
    kw = ctx.read.get("keywords")
    if kw is None or kw["qs"]["below"] is None:
        return unknown("G-KWD-04", "the keywords or their impressions could not be read")
    imp = kw["qs"]["impressions"]
    if not imp["low"] + imp["mid"] + imp["high"]:
        return na("G-KWD-04", "No keyword has a Quality Score with impressions yet.")
    b = kw["qs"]["below"]
    hits = []
    if b["adRelevance"] >= BELOW_AD_RELEVANCE:
        hits.append("ad relevance is below average on %d%% of impressions" % round(b["adRelevance"]))
    if b["landingPage"] >= BELOW_LANDING_PAGE:
        hits.append("landing page experience is below average on %d%%" % round(b["landingPage"]))
    if b["expectedCtr"] >= BELOW_EXPECTED_CTR:
        hits.append("expected click-through rate is below average on %d%%" % round(b["expectedCtr"]))
    if not hits:
        return make("G-KWD-04", "pass", "No Quality Score part is below average on a large share of impressions (ad relevance %d%%, landing page %d%%, expected click-through rate %d%%)." %
                    (round(b["adRelevance"]), round(b["landingPage"]), round(b["expectedCtr"])))
    return make("G-KWD-04", "warn", "On scored keywords, " + "; ".join(hits) + ".")


@check("G-KWD-05", "keywords", "The same keyword is not in several ad groups of one campaign", "low",
       "Two ad groups with the same keyword compete with each other in the same auction and split the data, so neither learns well.",
       "Keep the keyword in the ad group whose ads fit it best and pause or remove the other copies.")
def kwd_duplicates(ctx):
    kw = ctx.read.get("keywords")
    if kw is None:
        return unknown("G-KWD-05", "the keywords could not be read")
    d = kw["duplicates"]
    if not d["keywords"]:
        return make("G-KWD-05", "pass", "No keyword appears in more than one ad group of the same campaign.")
    return make("G-KWD-05", "warn", "Keywords in more than one ad group of the same campaign: %d (%s extra copies)." % (d["keywords"], format(d["copies"], ",")),
                items=["%s (%s, %d ad groups, %s)" % (e["keyword"], e["match"].lower(), e["adGroups"], e["campaign"]) for e in d["examples"]], count=d["keywords"])


@check("G-KWD-06", "keywords", "Few keywords spend without ever converting", "high",
       "A keyword with plenty of clicks and no conversion in a month is spending money the account cannot trace to a result.",
       "Check the search terms behind each one. Pause it, lower its bid, move it to exact match, or add the junk terms as negatives. If conversions are "
       "imported offline they arrive late, so recent clicks may not have a conversion yet: look at the import's delay first.")
def kwd_waste(ctx):
    kw = ctx.read.get("keywords")
    if kw is None or kw["wasted"] is None or ctx.spend is None:
        return unknown("G-KWD-06", "the keywords' numbers or the campaigns' spend could not be read")
    w = kw["wasted"]
    exact = share(w["cost"], ctx.spend)
    found = "%s on %s with %d or more clicks and no conversion in 30 days (%d%% of spend)." % (ctx.money(w["cost"]), plural(w["keywords"], "keyword"), w["minClicks"], round(exact))
    items = ["%s (%s, %s clicks, %s)" % (e["keyword"], ctx.money(e["cost"]), format(e["clicks"], ","), e["campaign"]) for e in w["examples"]]
    if exact >= WASTED_KEYWORD_FAIL:
        return make("G-KWD-06", "fail", found, items=items, count=w["keywords"])
    if exact >= WASTED_KEYWORD_WARN:
        return make("G-KWD-06", "warn", found, items=items, count=w["keywords"])
    return make("G-KWD-06", "pass", found)


@check("G-KWD-07", "keywords", "Most search term spend ends in a conversion", "medium",
       "The search terms report shows what people typed. Spend on terms that never converted is the clearest list of what to add as a negative "
       "keyword or to move to a more exact match.",
       "Run the Search Query Analysis agent for this account and add the negatives it recommends.")
def kwd_terms(ctx):
    t = ctx.read.get("terms")
    if t is None:
        return unknown("G-KWD-07", "the search terms could not be read")
    if not t["cost"]:
        return na("G-KWD-07", "No search term spend in the last 30 days.")
    exact = share(t["wastedCost"], t["cost"])
    where = "the %s most expensive search terms" % format(t["rows"], ",") if t["capped"] else "search terms"
    found = "%d%% of spend on %s (%s) went to terms that never converted." % (round(exact), where, ctx.money(t["wastedCost"]))
    items = ["%s (%s, %d clicks, %s)" % (e["term"], ctx.money(e["cost"]), e["clicks"], e["campaign"]) for e in t["examples"]]
    if exact >= WASTED_TERM_FAIL:
        return make("G-KWD-07", "fail", found, items=items)
    if exact >= WASTED_TERM_WARN:
        return make("G-KWD-07", "warn", found, items=items)
    return make("G-KWD-07", "pass", found)


@check("G-KWD-08", "keywords", "Keywords are approved and can serve", "low",
       "A disapproved keyword never shows. A keyword Google calls rarely served has too little search volume to trigger ads.",
       "Fix or replace disapproved keywords, and remove or broaden keywords that are rarely served.")
def kwd_serving(ctx):
    kw = ctx.read.get("keywords")
    if kw is None:
        return unknown("G-KWD-08", "the keywords could not be read")
    if not kw["enabled"]:
        return na("G-KWD-08", "No enabled keyword.")
    d, rare = kw["disapproved"]["count"], kw["rarelyServed"]
    rare_share = share(rare, kw["enabled"])
    if not d and rare_share < RARELY_SERVED_SHARE:
        return make("G-KWD-08", "pass", "No disapproved keyword (%s rarely served)." % plural(rare, "keyword"))
    parts = []
    if d:
        parts.append("%s disapproved" % plural(d, "keyword"))
    if rare_share >= RARELY_SERVED_SHARE:
        parts.append("%s (%d%%) rarely served" % (plural(rare, "keyword"), round(rare_share)))
    return make("G-KWD-08", "warn", " and ".join(parts).capitalize() + ".", items=["%s (%s, %s)" % (e["keyword"], e["match"].lower(), e["campaign"]) for e in kw["disapproved"]["examples"]], count=d)


@check("G-KWD-09", "keywords", "Ad groups hold a focused set of keywords", "low",
       "An ad group with dozens of keywords cannot have ads that speak to each one, which lowers ad relevance and Quality Score.",
       "Split the ad group by theme so each group has a handful of closely related keywords and its own ads.")
def kwd_many(ctx):
    s = ctx.read.get("structure")
    if s is None or "manyKeywords" not in s:
        return unknown("G-KWD-09", "the ad groups or the keywords could not be read")
    m = s["manyKeywords"]
    if not m["count"]:
        return make("G-KWD-09", "pass", "No Search ad group has more than 40 keywords.")
    return make("G-KWD-09", "warn", "Search ad groups with more than 40 keywords: %d." % m["count"], items=["%s (%s)" % (e["adGroup"], e["campaign"]) for e in m["examples"]], count=m["count"])


@check("G-KWD-10", "keywords", "Every Search ad group has keywords", "medium",
       "A Search ad group with no keyword cannot serve, and its ads are dead weight in the account.",
       "Add keywords to the ad group or pause it.")
def kwd_none(ctx):
    s = ctx.read.get("structure")
    if s is None or "noKeywords" not in s:
        return unknown("G-KWD-10", "the ad groups or the keywords could not be read")
    if not s["searchAdGroups"]:
        return na("G-KWD-10", "No enabled Search ad group.")
    n = s["noKeywords"]
    if not n["count"]:
        return make("G-KWD-10", "pass", "Every enabled Search ad group has an enabled keyword.")
    found = "%d of %s have no enabled keyword." % (n["count"], plural(s["searchAdGroups"], "Search ad group"))
    return make("G-KWD-10", "fail" if share(n["count"], s["searchAdGroups"]) >= NO_KEYWORD_FAIL else "warn", found,
                items=["%s (%s)" % (e["adGroup"], e["campaign"]) for e in n["examples"]], count=n["count"])


# ── Ads and landing pages ───────────────────────────────────────────────────

SLOW_PAGE_MS = 3000


@check("G-ADS-01", "ads", "Every Search ad group has a live ad", "critical",
       "An ad group with no live ad cannot show anything, whatever its keywords are worth.",
       "Write a responsive search ad for the ad group or pause the ad group.")
def ads_none(ctx):
    s = ctx.read.get("structure")
    if s is None or "noLiveAd" not in s:
        return unknown("G-ADS-01", "the ad groups or the ads could not be read")
    if not s["searchAdGroups"]:
        return na("G-ADS-01", "No enabled Search ad group.")
    n = s["noLiveAd"]
    if not n["count"]:
        return make("G-ADS-01", "pass", "Checked %s: all have a live ad." % plural(s["searchAdGroups"], "enabled Search ad group"))
    return make("G-ADS-01", "fail", "%d of %s have no live ad." % (n["count"], plural(s["searchAdGroups"], "enabled Search ad group")),
                items=["%s (%s)" % (e["adGroup"], e["campaign"]) for e in n["examples"]], count=n["count"])


@check("G-ADS-02", "ads", "Every Search ad group has a responsive search ad", "high",
       "Google no longer lets new expanded text ads be made, and responsive search ads are where Google's optimisation and reporting now live. "
       "An ad group with only older formats is not using them.",
       "Add a responsive search ad with up to 15 headlines and 4 descriptions to each of these ad groups.")
def ads_rsa(ctx):
    s = ctx.read.get("structure")
    if s is None or "noResponsiveAd" not in s:
        return unknown("G-ADS-02", "the ad groups or the ads could not be read")
    if not s["searchAdGroups"]:
        return na("G-ADS-02", "No enabled Search ad group.")
    dead = {(e["adGroup"], e["campaign"]) for e in s["noLiveAd"]["examples"]}
    only_old = s["noResponsiveAd"]["count"] - s["noLiveAd"]["count"]
    if only_old <= 0:
        return make("G-ADS-02", "pass", "Every Search ad group with a live ad has a responsive search ad.")
    items = ["%s (%s)" % (e["adGroup"], e["campaign"]) for e in s["noResponsiveAd"]["examples"] if (e["adGroup"], e["campaign"]) not in dead]
    return make("G-ADS-02", "warn", "Search ad groups with only older or call ad formats and no responsive search ad: %d." % only_old, items=items, count=only_old)


@check("G-ADS-03", "ads", "Ad groups have more than one ad to test", "medium",
       "With one ad there is nothing to compare. Two or three responsive search ads let Google and the team see which messages work.",
       "Add a second responsive search ad built around a different angle (price, speed, trust) to each ad group.")
def ads_testing(ctx):
    s = ctx.read.get("structure")
    if s is None or "oneAd" not in s:
        return unknown("G-ADS-03", "the ad groups or the ads could not be read")
    if not s["searchAdGroups"]:
        return na("G-ADS-03", "No enabled Search ad group.")
    n = s["oneAd"]["count"]
    exact = share(n, s["searchAdGroups"])
    if exact < ONE_AD_WARN:
        return make("G-ADS-03", "pass", "%d of %s have a single ad." % (n, plural(s["searchAdGroups"], "Search ad group")))
    return make("G-ADS-03", "fail" if exact >= ONE_AD_FAIL else "warn", "%d of %s (%d%%) have a single ad, so nothing is being tested." % (n, plural(s["searchAdGroups"], "Search ad group"), round(exact)),
                items=["%s (%s)" % (e["adGroup"], e["campaign"]) for e in s["oneAd"]["examples"]], count=n)


@check("G-ADS-04", "ads", "Responsive search ads are not rated Poor", "medium",
       "Google rates each responsive search ad's strength from the number, variety and relevance of its headlines and descriptions. A Poor "
       "ad is limited in how it can be shown.",
       "Open the Ad Strength Analysis agent for this account and apply the headline and description changes it recommends.")
def ads_strength(ctx):
    a = ctx.read.get("ads")
    if a is None:
        return unknown("G-ADS-04", "the ads could not be read")
    if not a["rsa"]:
        return na("G-ADS-04", "No live responsive search ad.")
    poor = a["strength"].get("POOR", 0)
    avg = a["strength"].get("AVERAGE", 0)
    found = "Rated Poor: %d of %s. Rated Average: %d." % (poor, plural(a["rsa"], "responsive search ad"), avg)
    if not poor:
        return make("G-ADS-04", "pass", found)
    return make("G-ADS-04", "fail" if share(poor, a["rsa"]) >= 25 else "warn", found)


@check("G-ADS-05", "ads", "No ad is disapproved", "high",
       "A disapproved ad does not run. If it was the only ad in its ad group, the ad group is dark.",
       "Open each disapproved ad's policy details, fix the text, landing page or claim that broke the policy, and appeal if it was wrongly flagged.")
def ads_disapproved(ctx):
    a = ctx.read.get("ads")
    if a is None:
        return unknown("G-ADS-05", "the ads could not be read")
    d, limited = a["disapproved"]["count"], a["limited"]
    if not d:
        extra = " %s approved with limits." % plural(limited, "ad") if limited else ""
        return make("G-ADS-05", "pass", "No live ad is disapproved." + extra)
    return make("G-ADS-05", "fail", "%s disapproved%s." % (plural(d, "live ad") + (" is" if d == 1 else " are"), ", and %d more approved with limits" % limited if limited else ""),
                items=["%s / %s (%s)" % (e["campaign"], e["adGroup"], ", ".join(e["topics"]).lower().replace("_", " ") or "no topic given") for e in a["disapproved"]["examples"]], count=d)


@check("G-ADS-06", "ads", "No ad uses a retired format", "low",
       "Expanded text ads and call-only ads can no longer be created. The ones left keep serving but get none of the newer features.",
       "Replace them with responsive search ads and keep the old ads paused for a short overlap.")
def ads_legacy(ctx):
    a = ctx.read.get("ads")
    if a is None:
        return unknown("G-ADS-06", "the ads could not be read")
    if not a["legacy"]:
        return make("G-ADS-06", "pass", "No live ad uses a retired format.")
    kinds = ", ".join("%d %s" % (n, t.lower().replace("_", " ")) for t, n in sorted(a["byType"].items(), key=lambda kv: -kv[1]) if t in LEGACY_ADS)
    return make("G-ADS-06", "warn", "%s a retired format (%s)." % (plural(a["legacy"], "live ad") + (" uses" if a["legacy"] == 1 else " use"), kinds))


@check("G-ADS-07", "ads", "Responsive search ads are not heavily pinned", "low",
       "Pinning headlines to positions limits the combinations Google can test, which lowers ad strength and often performance.",
       "Pin only what has to appear (a legal line, the brand) and let the rest rotate.")
def ads_pins(ctx):
    a = ctx.read.get("ads")
    if a is None:
        return unknown("G-ADS-07", "the ads could not be read")
    if not a["rsa"]:
        return na("G-ADS-07", "No live responsive search ad.")
    if not a["pinned"]["heavy"]:
        return make("G-ADS-07", "pass", "No responsive search ad pins three or more headlines (%d pin some)." % a["pinned"]["any"])
    return make("G-ADS-07", "warn", "%s pin three or more headlines." % (plural(a["pinned"]["heavy"], "responsive search ad")))


@check("G-ADS-08", "ads", "Ad groups do not carry too many responsive search ads", "low",
       "Google serves at most three responsive search ads per ad group. More only add clutter and slow the learning.",
       "Keep the best two or three and pause the rest.")
def ads_many(ctx):
    s = ctx.read.get("structure")
    if s is None or "manyAds" not in s:
        return unknown("G-ADS-08", "the ad groups or the ads could not be read")
    n = s["manyAds"]
    if not n["count"]:
        return make("G-ADS-08", "pass", "No Search ad group has more than three responsive search ads.")
    return make("G-ADS-08", "warn", "Search ad groups with more than three responsive search ads: %d." % n["count"],
                items=["%s (%s)" % (e["adGroup"], e["campaign"]) for e in n["examples"]], count=n["count"])


@check("G-ADS-09", "ads", "Landing pages load", "high",
       "An ad that sends people to a page that errors or never loads pays for clicks that cannot convert.",
       "Fix or replace the final URL in the ads that point at broken pages, and check the pages are fast on mobile.")
def ads_pages(ctx):
    p = ctx.read.get("pages")
    if p is None:
        return unknown("G-ADS-09", "the landing pages were not checked")
    if not p["results"]:
        return na("G-ADS-09", "No ad has a final URL to check.")
    blocked = [x for x in p["results"] if x.get("blocked")]
    if len(blocked) == len(p["results"]):
        return unknown("G-ADS-09", "every landing page refused the automated check (HTTP %s)" % "/".join(sorted({str(x["status"]) for x in blocked})))
    open_ = [x for x in p["results"] if not x.get("blocked")]
    broken = [x for x in open_ if (x["error"] and x["error"] != TIMED_OUT) or x["status"] >= 400]
    slow = [x for x in open_ if x not in broken and (x["error"] == TIMED_OUT or x["ms"] > SLOW_PAGE_MS)]
    scope = "%d of %d landing pages" % (len(p["results"]), p["of"]) if p["of"] > len(p["results"]) else plural(len(p["results"]), "landing page")
    note = " %s refused the automated check and could not be verified." % plural(len(blocked), "page") if blocked else ""
    if broken:
        return make("G-ADS-09", "fail", "%d of %s did not load.%s" % (len(broken), scope, note),
                    items=["%s (%s)" % (x["url"], x["error"] or "HTTP %d" % x["status"]) for x in broken])
    if slow:
        return make("G-ADS-09", "warn", "%d of %s were slow: they took more than %d seconds to answer.%s" % (len(slow), scope, SLOW_PAGE_MS // 1000, note),
                    items=["%s (%s)" % (x["url"], "no answer in %d seconds, twice" % (x["ms"] // 1000 or 1) if x["error"] == TIMED_OUT else "%.1f seconds" % (x["ms"] / 1000.0)) for x in slow])
    return make("G-ADS-09", "pass", "Checked %s: all loaded.%s" % (scope, note))
