"""Paid Marketing Agents: Account Audit, the Google Ads checklist.

Each check reads the compact account read (tracker/paid_audit_google_read.py) and decides a result with the numbers that justify it; the
wording of why it matters and what to do is in the catalog (tracker/paid_audit_model.py). A check whose input was not read says
"unknown". Thresholds are named constants so the page and the tests can quote them. Nothing here asks a model anything.
"""
from __future__ import annotations

import logging

from . import paid_audit_model as model
from .paid_audit_google_read import SEARCH_LIST_MIN

log = logging.getLogger(__name__)
make, unknown, na = model.make, model.unknown, model.not_applicable

# Conversion action types that count as the client's own measurement: a tag on the site, an import from GA4, a CRM or an offline
# upload, or a call. The rest (app installs, Smart campaign and Google-hosted actions) are Google's defaults and say nothing about
# whether the client's tracking works.
WEB_TYPES = {"WEBPAGE", "WEBPAGE_CODELESS", "UPLOAD_CLICKS", "UPLOAD_CALLS", "GOOGLE_ANALYTICS_4_CUSTOM", "GOOGLE_ANALYTICS_4_PURCHASE",
             "UNIVERSAL_ANALYTICS_GOAL", "UNIVERSAL_ANALYTICS_TRANSACTION", "SALESFORCE"}
CALL_TYPES = {"AD_CALL", "CLICK_TO_CALL", "WEBSITE_CALL"}
TAG_TYPES = {"WEBPAGE", "WEBPAGE_CODELESS"}                  # a tag on the site, as opposed to an import
GA4_PREFIX = "GOOGLE_ANALYTICS_4"
LEAD_CATEGORIES = {"SUBMIT_LEAD_FORM", "REQUEST_QUOTE", "CONTACT", "SIGNUP", "BOOK_APPOINTMENT"}
VALUE_BIDDING = {"MAXIMIZE_CONVERSION_VALUE", "TARGET_ROAS"}
SMART_BIDDING = {"MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS"}
LAST_CLICK = {"GOOGLE_ADS_LAST_CLICK", "LAST_CLICK", "EXTERNAL"}
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

    def money(self, x):
        return "%s %s" % (self.currency, format(round(x), ",")) if self.currency else format(round(x), ",")

    def search_camps(self):
        return [c for c in self.camps or [] if c["type"] == "SEARCH"]


def share(part, whole):
    """The exact percentage, for deciding a threshold (a rounded 24.6% must not count as 25%)."""
    return 100.0 * part / whole if whole else 0.0


def pct(part, whole):
    """The whole-percent figure shown in a sentence."""
    return round(share(part, whole))


def plural(n, word):
    """"1 action", "2 actions"."""
    return "%d %s" % (n, word if n == 1 else word + "s")


def verb(n, word):
    """The verb that goes with a count of n ("1 of 4 counts", "2 of 4 count")."""
    return word + "s" if n == 1 else word


def names(items, key="name"):
    return [i[key] if isinstance(i, dict) else str(i) for i in items]


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
    calls = [a for a in ctx.actions if a["status"] in ACTIVE_STATES and (a["type"] in CALL_TYPES or a["category"] == "PHONE_CALL_LEAD")]
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
        return make("G-SET-07", "warn", "The optimization score is %d%%." % score)
    return make("G-SET-07", "pass", "The optimization score is %d%%." % score)


# ── Conversion tracking ─────────────────────────────────────────────────────

MIN_SPEND = 100                               # a month's spend under this (in the account's currency) is too little to expect conversions


def _real(a):
    """An enabled conversion action that is the client's own measurement, not one of Google's defaults."""
    return a["status"] in ACTIVE_STATES and (a["type"] in WEB_TYPES or a["type"] in CALL_TYPES or a["type"] == "LEAD_FORM_SUBMIT")


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
    if ctx.spend is None or ctx.conversions is None:
        return unknown("G-CNV-02", "the campaigns' numbers could not be read")
    if ctx.spend < MIN_SPEND:
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
    p = [a for a in _primary(ctx) if a["attribution"]]
    if not p:
        return na("G-CNV-08", "No primary action reports an attribution model.")
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
        return make("G-LNK-02", "warn", "None of the %s open audience lists has any members." % lists["open"], items=[e["name"] for e in lists["examples"] if e["size"] == 0])
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
                items=["%s (%s)" % (e["name"], format(e["size"], ",")) for e in lists["examples"] if e["size"]])



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
        return make("G-STR-01", "pass", "All %s target at least one location or radius." % plural(len(camps), "enabled campaign"))
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
        return make("G-STR-02", "pass", "None of the %s opts into the Display Network." % plural(len(search), "Search campaign"))
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
    held = []
    for x in live:
        m = x["metrics"] or {}
        lost = m.get("budgetLost")
        limited = "BUDGET_CONSTRAINED" in x["reasons"] or (lost is not None and lost >= BUDGET_LOST_PCT)
        if limited and (m.get("conversions") or 0) > 0:
            held.append((x, lost))
    if not held:
        return make("G-STR-04", "pass", "No converting campaign is held back by budget.")
    held.sort(key=lambda h: (-(h[0]["metrics"] or {}).get("cost", 0), h[0]["name"]))
    return make("G-STR-04", "warn", "%s converting and held back by budget." % (plural(len(held), "campaign") + (" is" if len(held) == 1 else " are")),
                items=["%s%s" % (x["name"], " (%d%% of impressions lost to budget)" % lost if lost is not None else " (budget limited)") for x, lost in held])


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
    thin = [x for x in smart if x["metrics"]["cost"] > 0 and x["metrics"]["conversions"] < _needs(x)]
    if not thin:
        return make("G-STR-07", "pass", "All %s have the conversions Google suggests." % plural(len(smart), "Smart Bidding campaign"))
    thin.sort(key=lambda x: (-x["metrics"]["cost"], x["name"]))
    return make("G-STR-07", "warn", "%d of %s had fewer conversions in 30 days than Google suggests." % (len(thin), plural(len(smart), "Smart Bidding campaign")),
                items=["%s (%d conversions, about %d suggested)" % (x["name"], round(x["metrics"]["conversions"]), _needs(x)) for x in thin])


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
                items=["%s (%s, %d conversions)" % (x["name"], _say(x["bidding"]), round(x["metrics"]["conversions"])) for x in ready])


@check("G-STR-09", "structure", "Campaigns that spend are converting", "high",
       "A campaign with a lot of clicks and no conversions in a month is either not converting or not measuring, and it keeps spending.",
       "Check the landing page and the conversion tracking for each, tighten the targeting, or pause the campaign and move its budget.")
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
                items=["%s (%d%% lost to rank)" % (x["name"], x["metrics"]["rankLost"]) for x in big])


@check("G-STR-11", "structure", "The account is not buried in paused campaigns", "low",
       "Hundreds of paused campaigns make the account slow to read and easy to enable by mistake.",
       "Archive the campaigns that will not come back and keep their results in a report.")
def str_paused(ctx):
    if ctx.camps is None:
        return unknown("G-STR-11", "the campaigns could not be read")
    enabled, paused = ctx.counts.get("enabled", 0), ctx.counts.get("paused", 0)
    if paused >= PAUSED_CLUTTER and paused >= 2 * enabled:
        return make("G-STR-11", "warn", "%s against %d enabled." % (plural(paused, "paused campaign"), enabled))
    return make("G-STR-11", "pass", "%s and %d enabled." % (plural(paused, "paused campaign"), enabled))
