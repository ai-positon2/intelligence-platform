"""Paid Marketing Agents: Account Audit, the Google Ads checklist.

Each check reads the compact account read (tracker/paid_audit_google_read.py) and decides a result with the numbers that justify it; the
wording of why it matters and what to do is in the catalog (tracker/paid_audit_model.py). A check whose input was not read says
"unknown". Thresholds are named constants so the page and the tests can quote them. Nothing here asks a model anything.
"""
from __future__ import annotations

import logging

from . import paid_audit_model as model

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


def pct(part, whole):
    return round(100.0 * part / whole) if whole else 0


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
        return make("G-SET-06", "pass", "Call reporting and call conversion reporting are on (%d call conversion actions)." % len(calls))
    return make("G-SET-06", "fail", "%d call conversion actions are enabled but call reporting is %s." %
                (len(calls), "off" if not ctx.customer.get("callReporting") else "on without call conversion reporting"))


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
