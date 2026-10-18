"""Paid Marketing Agents: Account Audit, the Meta Ads checklist.

The same method as the Google checklist (tracker/paid_audit_google_checks.py): each check reads the compact account read
(tracker/paid_audit_meta_read.py), decides a result with the numbers that justify it, and says unknown when its input was not read.
Meta's own rules differ from Google's (learning stages, a pixel and the Conversions API, objectives rather than bidding strategies),
so the checks are Meta's, not copies. Nothing here asks a model anything.
"""
from __future__ import annotations

import datetime as dt
import logging

from . import paid_audit_model as model
from .paid_audit_model import names, pct, plural, share, verb  # noqa: F401
from .paid_audit_meta_read import LEARNING_LIMITED, parse_time

log = logging.getLogger(__name__)
make, unknown, na = model.make, model.unknown, model.not_applicable

model.section("m_account", "Meta ad account", "meta", 1)
model.section("m_tracking", "Pixel and tracking", "meta", 2)
model.section("m_structure", "Campaigns and ad sets", "meta", 3)
model.section("m_ads", "Ads", "meta", 4)

CHECKS = []

FRESH_HOURS = 24
STALE_DAYS = 7
CAP_FAIL = 100                                # per cent of the spend cap already spent
CAP_WARN = 90
LEARNING_FAIL_SHARE = 50
NO_RESULT_FAIL_SHARE = 25
HIGH_FREQUENCY = 4.0
FREQUENCY_MIN_SHARE = 5
ONE_AD_FAIL = 75
ONE_AD_WARN = 25
RESULT_KINDS = {"SALES", "LEADS", "APP"}
WEAK_GOALS = {"LINK_CLICKS", "LANDING_PAGE_VIEWS", "IMPRESSIONS", "REACH", "POST_ENGAGEMENT"}
OUTCOME_OBJECTIVES = {"LEADS", "SALES"}
BROWSING_EVENTS = {"PageView", "ViewContent", "Search", "AddToCart", "AddToWishlist", "AddPaymentInfo",
                   "Microdata", "SubscribedButtonClick"}        # the last two are events Meta detects on its own, not ones the site set up
SERVER_WORDS = ("server", "capi", "conversions_api", "api")
DISABLE_REASONS = {1: "an ads integrity policy", 2: "an intellectual property review", 3: "a payment risk review", 4: "a gray account shutdown",
                   5: "an ads authenticity review", 6: "a business integrity review", 7: "a permanent close", 8: "an unused reseller account",
                   9: "an unused account", 11: "a business manager integrity policy", 12: "a misused ad account"}


def check(cid, section, title, severity, why, fix):
    model.register([{"id": cid, "section": section, "title": title, "severity": severity, "why": why, "fix": fix}])

    def wrap(fn):
        CHECKS.append((cid, fn))
        return fn
    return wrap


class Ctx:
    def __init__(self, read):
        self.read = read
        self.account = read.get("account") or {}
        self.currency = self.account.get("currency") or ""
        self.min_spend = model.min_spend(self.currency)
        self.camps = read.get("campaigns")
        self.sets = read.get("adSets")
        self.spend = None if self.camps is None or any(c["spend"] is None for c in self.camps) else sum(c["spend"] for c in self.camps)
        self.now = parse_time(read.get("readAt")) or dt.datetime.now(dt.timezone.utc)

    def money(self, x):
        return "%s %s" % (self.currency, format(round(x), ",")) if self.currency else format(round(x), ",")


def run(read, only=None):
    """Every Meta check on one account's read: a list of findings. A check that raises is reported unknown (and logged)."""
    ctx = Ctx(read)
    out = []
    for cid, fn in CHECKS:
        if only is not None and cid not in only:
            continue
        try:
            out.append(fn(ctx))
        except Exception as e:                      # a bug in one check
            log.exception("account audit: meta check %s failed", cid)
            out.append(unknown(cid, "the check itself failed (%s)" % type(e).__name__))
    return out


# ── The ad account ──────────────────────────────────────────────────────────

@check("M-ACC-01", "m_account", "The ad account is active", "critical",
       "A disabled, unsettled or closed ad account shows no ads. An account in a grace period or under review is about to.",
       "Open Account Quality and Billing in Meta Business Settings, find the reason Meta gives and fix it (a failed payment, a policy appeal).")
def acc_active(ctx):
    status = ctx.account.get("status")
    if status == "ACTIVE":
        return make("M-ACC-01", "pass", "The ad account is active.")
    why = DISABLE_REASONS.get(ctx.account.get("disableReason"))
    if status in ("DISABLED", "UNSETTLED", "PENDING_CLOSURE", "CLOSED"):
        label = status.lower().replace("_", " ")
        return make("M-ACC-01", "fail", "The ad account is %s%s." % (label, " (%s)" % why if status == "DISABLED" and why else ""))
    if status in ("IN_GRACE_PERIOD", "PENDING_REVIEW"):
        return make("M-ACC-01", "warn", "The ad account is %s." % status.lower().replace("_", " "))
    return unknown("M-ACC-01", "Meta did not give the account's status")


@check("M-ACC-02", "m_account", "The spend cap is not about to be reached", "medium",
       "When the account reaches its spend cap every campaign stops, whatever the budgets say.",
       "Raise or remove the spend cap in Billing before the account reaches it, or agree the new limit with the client.")
def acc_cap(ctx):
    cap, spent = ctx.account.get("spendCap") or 0.0, ctx.account.get("amountSpent") or 0.0
    if not cap:
        return make("M-ACC-02", "pass", "The ad account has no spend cap.")
    used = share(spent, cap)
    found = "%d%% of the %s spend cap is used (%s spent)." % (round(used), ctx.money(cap), ctx.money(spent))
    if used >= CAP_FAIL:
        return make("M-ACC-02", "fail", found)
    if used >= CAP_WARN:
        return make("M-ACC-02", "warn", found)
    return make("M-ACC-02", "pass", found)


@check("M-ACC-03", "m_account", "The pixel terms are accepted", "low",
       "Meta will not let the pixel build custom audiences until the terms of use for them are accepted on the ad account.",
       "Accept the custom audience terms in Meta's Audiences section for this ad account.")
def acc_terms(ctx):
    t = ctx.account.get("pixelTerms")
    if t is None:
        return unknown("M-ACC-03", "Meta did not give the account's pixel terms")
    if t:
        return make("M-ACC-03", "pass", "The pixel terms are accepted.")
    return make("M-ACC-03", "warn", "The pixel terms are not accepted on this ad account.")


# ── Pixel and tracking ──────────────────────────────────────────────────────

@check("M-TRK-01", "m_tracking", "A pixel is installed", "high",
       "The Meta pixel is how the site tells Meta who visited and who converted. Without it, conversion campaigns have nothing to optimise on.",
       "Create a pixel (or a dataset) in Events Manager, install it on the site and assign it to this ad account. A pixel owned by the business "
       "but not shared with the ad account is not visible here.")
def trk_pixel(ctx):
    px = ctx.read.get("pixels")
    if px is None:
        return unknown("M-TRK-01", "the pixels could not be read")
    if not px:
        return make("M-TRK-01", "warn", "No pixel is assigned to this ad account.")
    return make("M-TRK-01", "pass", "%s assigned to this ad account." % (plural(len(px), "pixel") + (" is" if len(px) == 1 else " are")))


@check("M-TRK-02", "m_tracking", "The pixel has fired recently", "critical",
       "A pixel that has not fired for days means the tag was removed or broke, and every conversion since is lost to Meta.",
       "Open Events Manager, check the pixel's status and Test Events, and reinstall the tag if it is gone.")
def trk_fired(ctx):
    px = ctx.read.get("pixels")
    if px is None:
        return unknown("M-TRK-02", "the pixels could not be read")
    if not px:
        return na("M-TRK-02", "No pixel is assigned to this ad account.")
    last = parse_time(px[0]["lastFired"])                     # the list is freshest first
    if last is None:
        return make("M-TRK-02", "fail", "No pixel has ever fired.")
    age = ctx.now - last
    hours = age.total_seconds() / 3600.0
    ago = "%d hours ago" % round(hours) if hours < 48 else "%d days ago" % int(hours // 24)
    if hours <= FRESH_HOURS:
        return make("M-TRK-02", "pass", "The pixel last fired %s (%s)." % (ago, px[0]["name"]))
    if age <= dt.timedelta(days=STALE_DAYS):
        return make("M-TRK-02", "warn", "The freshest pixel last fired %s (%s)." % (ago, px[0]["name"]))
    return make("M-TRK-02", "fail", "The freshest pixel last fired %s (%s)." % (ago, px[0]["name"]))


@check("M-TRK-03", "m_tracking", "The pixel receives conversion events, not only page views", "high",
       "Lead and sales campaigns learn from conversion events. A pixel that only reports page views gives them nothing to optimise on.",
       "Set up the Lead, Schedule, Purchase or custom events on the form, booking or checkout, and test them in Events Manager.")
def trk_events(ctx):
    st = ctx.read.get("stats")
    if st is None or st["events"] is None:
        return unknown("M-TRK-03", "the events the pixel received could not be read")
    ev = st["events"]
    if not ev:
        return make("M-TRK-03", "fail", "The pixel received no events in the last %d days." % st["days"])
    conv = {k: v for k, v in ev.items() if k not in BROWSING_EVENTS}
    if not conv:
        return make("M-TRK-03", "fail", "In the last %d days the pixel received only browsing events (%s)." % (st["days"], ", ".join("%s %s" % (k, format(v, ",")) for k, v in sorted(ev.items(), key=lambda kv: -kv[1])[:3])))
    top = ", ".join("%s %s" % (k, format(v, ",")) for k, v in sorted(conv.items(), key=lambda kv: (-kv[1], kv[0]))[:3])
    return make("M-TRK-03", "pass", "In the last %d days the pixel received conversion events: %s." % (st["days"], top))


@check("M-TRK-04", "m_tracking", "Advanced matching is on", "medium",
       "Advanced matching sends hashed contact details with pixel events so Meta can match more conversions to people.",
       "Turn on automatic advanced matching in the pixel's settings, if the client's privacy rules allow it. Healthcare and other regulated "
       "clients often cannot send contact details.")
def trk_matching(ctx):
    px = ctx.read.get("pixels")
    if px is None:
        return unknown("M-TRK-04", "the pixels could not be read")
    if not px:
        return na("M-TRK-04", "No pixel is assigned to this ad account.")
    on = px[0]["autoMatching"]
    if on is None:
        return unknown("M-TRK-04", "Meta did not give the pixel's matching settings")
    if on:
        return make("M-TRK-04", "pass", "Automatic advanced matching is on for %s." % px[0]["name"])
    return make("M-TRK-04", "warn", "Automatic advanced matching is off for %s." % px[0]["name"])


@check("M-TRK-05", "m_tracking", "Events also arrive through the Conversions API", "medium",
       "Browsers and ad blockers hide a share of pixel events. The Conversions API sends the same events from the server, which recovers them "
       "and improves matching.",
       "Set up the Conversions API (through the site platform's integration or Tag Manager server-side) and deduplicate it against the pixel.")
def trk_capi(ctx):
    st = ctx.read.get("stats")
    if st is None or st["methods"] is None:
        return unknown("M-TRK-05", "how the events arrived could not be read")
    methods = st["methods"]
    if not methods:
        return make("M-TRK-05", "warn", "No events were received in the last %d days, so no source can be seen." % st["days"])
    server = {k: v for k, v in methods.items() if any(w in k.lower() for w in SERVER_WORDS)}
    if server:
        return make("M-TRK-05", "pass", "Server events were received in the last %d days (%s)." % (st["days"], ", ".join("%s %s" % (k, format(v, ",")) for k, v in sorted(server.items()))))
    return make("M-TRK-05", "warn", "Meta reports the events as arriving only through %s; none looks like the Conversions API." % ", ".join(sorted(methods)))


# ── Campaigns and ad sets ───────────────────────────────────────────────────

@check("M-STR-01", "m_structure", "Ad sets are not stuck in Learning limited", "high",
       "Learning limited means the ad set is not getting enough conversions (about 50 a week) to leave the learning phase, so its delivery stays "
       "unstable and costs more.",
       "Merge ad sets that split the same audience, broaden targeting, raise the budget, or optimise for an event that happens more often.")
def str_learning(ctx):
    if ctx.sets is None:
        return unknown("M-STR-01", "the ad sets could not be read")
    if not ctx.sets:
        return na("M-STR-01", "No live ad set.")
    limited = [s for s in ctx.sets if s["learning"] == LEARNING_LIMITED]
    if not limited:
        return make("M-STR-01", "pass", "None of the %s is learning limited." % plural(len(ctx.sets), "live ad set"))
    found = "%d of %s are learning limited." % (len(limited), plural(len(ctx.sets), "live ad set"))
    return make("M-STR-01", "fail" if share(len(limited), len(ctx.sets)) >= LEARNING_FAIL_SHARE else "warn", found, items=names(limited), count=len(limited))


@check("M-STR-02", "m_structure", "No campaign or ad set reports an issue", "high",
       "Meta flags campaigns and ad sets it cannot deliver in full: a payment problem, a missing pixel event, an audience that is too narrow.",
       "Open each flagged campaign or ad set in Ads Manager and read the message Meta gives.")
def str_issues(ctx):
    if ctx.camps is None or ctx.sets is None:
        return unknown("M-STR-02", "the campaigns or the ad sets could not be read")
    flagged = [("%s (%s)" % (c["name"], c["issues"][0]), c) for c in ctx.camps if c["issues"]] + [("%s (%s)" % (s["name"], s["issues"][0]), s) for s in ctx.sets if s["issues"]]
    if not flagged:
        return make("M-STR-02", "pass", "No live campaign or ad set reports an issue.")
    return make("M-STR-02", "warn", "Live campaigns and ad sets with an issue reported by Meta: %d." % len(flagged), items=[f[0] for f in flagged])


@check("M-STR-03", "m_structure", "Campaigns that spend are producing results", "high",
       "A lead, sales or app campaign that spends for a month and records no result is either not converting or not measuring.",
       "Check the pixel event the campaign optimises for, the landing page and the audience, or pause the campaign and move its budget.")
def str_results(ctx):
    if ctx.camps is None or ctx.spend is None:
        return unknown("M-STR-03", "the campaigns or their numbers could not be read")
    live = [c for c in ctx.camps if c["kind"] in RESULT_KINDS]
    if not live:
        return na("M-STR-03", "No live lead, sales or app campaign.")
    dead = [c for c in live if c["spend"] >= ctx.min_spend and c["results"] == 0 and c["anyResults"] == 0]      # a lead campaign optimised for Schedule or Contact has results of its own
    if not dead:
        return make("M-STR-03", "pass", "Every lead, sales or app campaign that spent %s or more recorded a result." % ctx.money(ctx.min_spend))
    cost = sum(c["spend"] for c in dead)
    exact = share(cost, ctx.spend)
    found = "%s spent %s (%d%% of spend) and recorded no result in the last 30 days." % (plural(len(dead), "campaign"), ctx.money(cost), round(exact))
    return make("M-STR-03", "fail" if exact >= NO_RESULT_FAIL_SHARE else "warn", found, items=["%s (%s)" % (c["name"], ctx.money(c["spend"])) for c in dead])


@check("M-STR-04", "m_structure", "Lead and sales ad sets optimise for a conversion", "medium",
       "An ad set in a lead or sales campaign that optimises for link clicks, page views or reach finds people who click, not people who convert.",
       "Change the ad set's optimisation event to Leads, Schedule, Purchase or another conversion once the pixel receives it.")
def str_goal(ctx):
    if ctx.camps is None or ctx.sets is None:
        return unknown("M-STR-04", "the campaigns or the ad sets could not be read")
    kinds = {c["id"]: c for c in ctx.camps if c["kind"] in OUTCOME_OBJECTIVES}
    if not kinds:
        return na("M-STR-04", "No live lead or sales campaign.")
    weak = [s for s in ctx.sets if s["campaignId"] in kinds and s["optimization"] in WEAK_GOALS]
    if not weak:
        return make("M-STR-04", "pass", "No ad set in a lead or sales campaign optimises for clicks, page views or reach.")
    return make("M-STR-04", "warn", "%s in lead or sales campaigns optimise for clicks, page views or reach." % plural(len(weak), "ad set"),
                items=["%s (%s, %s)" % (s["name"], s["optimization"].lower().replace("_", " "), kinds[s["campaignId"]]["name"]) for s in weak], count=len(weak))


@check("M-STR-05", "m_structure", "Frequency is not high", "medium",
       "A frequency of four or more in 30 days means the same people see the ads over and over, which tires the creative and raises the cost of a result.",
       "Refresh the creative, widen the audience, or lower the budget of the campaign.")
def str_frequency(ctx):
    if ctx.camps is None or ctx.spend is None:
        return unknown("M-STR-05", "the campaigns or their numbers could not be read")
    if not ctx.camps:
        return na("M-STR-05", "No live campaign.")
    hot = [c for c in ctx.camps if c["frequency"] >= HIGH_FREQUENCY and share(c["spend"], ctx.spend) >= FREQUENCY_MIN_SHARE]
    if not hot:
        return make("M-STR-05", "pass", "No big campaign has a frequency of %g or more in the last 30 days." % HIGH_FREQUENCY)
    return make("M-STR-05", "warn", "%s a frequency of %g or more." % (plural(len(hot), "big campaign") + (" has" if len(hot) == 1 else " have"), HIGH_FREQUENCY),
                items=["%s (frequency %g)" % (c["name"], c["frequency"]) for c in hot])


@check("M-STR-06", "m_structure", "The ad account has live campaigns", "high",
       "An ad account with no live campaign is not advertising. Most of the other checks have nothing to look at, so its score only reflects the account and its pixel.",
       "Turn on the campaigns that should be running, or audit again once the account is built.")
def str_any(ctx):
    if ctx.camps is None:
        return unknown("M-STR-06", "the campaigns could not be read")
    if not ctx.camps:
        return make("M-STR-06", "warn", "No campaign is live. The ad account is not advertising, so most checks do not apply and the score only reflects the account and its pixel.",
                    caps="the ad account is not advertising")
    return make("M-STR-06", "pass", "%s." % plural(len(ctx.camps), "live campaign"))


# ── Ads ─────────────────────────────────────────────────────────────────────

@check("M-ADS-01", "m_ads", "No ad is disapproved", "high",
       "A disapproved ad does not run, and repeated disapprovals put the whole account at risk.",
       "Open each disapproved ad's review feedback, fix the creative, text or landing page, and request a review if it was wrongly flagged.")
def ads_disapproved(ctx):
    a = ctx.read.get("ads")
    if a is None:
        return unknown("M-ADS-01", "the ads could not be read")
    d, flagged = a["disapproved"]["count"], a["withIssues"]["count"]
    if not d and not flagged:
        return make("M-ADS-01", "pass", "No ad is disapproved or flagged with issues.")
    items = ["%s (%s)" % (e["ad"], ", ".join(e["why"]) or "no reason given") for e in a["disapproved"]["examples"]]
    found = "%s disapproved%s." % (plural(d, "ad") + (" is" if d == 1 else " are"), " and %s with issues" % plural(flagged, "more ad") if flagged else "")
    if d:
        return make("M-ADS-01", "fail", found, items=items, count=d)
    return make("M-ADS-01", "warn", "%s with issues." % plural(flagged, "ad"), items=["%s (%s)" % (e["ad"], ", ".join(e["why"]) or "no reason given") for e in a["withIssues"]["examples"]], count=flagged)


@check("M-ADS-02", "m_ads", "Every live ad set has an ad that can deliver", "critical",
       "An ad set whose ads are all disapproved, paused or deleted cannot deliver, however its audience and budget are set.",
       "Add an ad to the ad set, fix the disapproved ones, or turn the ad set off.")
def ads_none(ctx):
    per = ctx.read.get("adsPerSet")
    if ctx.sets is None or per is None:
        return unknown("M-ADS-02", "the ad sets or the ads could not be read")
    if not ctx.sets:
        return na("M-ADS-02", "No live ad set.")
    dark = [s for s in ctx.sets if not per.get(s["id"])]
    if not dark:
        return make("M-ADS-02", "pass", "Checked %s: all have an ad that can deliver." % plural(len(ctx.sets), "live ad set"))
    return make("M-ADS-02", "fail", "%d of %s have no ad that can deliver." % (len(dark), plural(len(ctx.sets), "live ad set")), items=names(dark), count=len(dark))


@check("M-ADS-03", "m_ads", "Ad sets have more than one ad to test", "medium",
       "With one ad there is nothing to compare. Several ads let Meta's delivery find the creative that works and show the team what to make next.",
       "Add two or three ads per ad set with different images or videos and different first lines.")
def ads_testing(ctx):
    per = ctx.read.get("adsPerSet")
    if ctx.sets is None or per is None:
        return unknown("M-ADS-03", "the ad sets or the ads could not be read")
    live = [s for s in ctx.sets if per.get(s["id"])]
    if not live:
        return na("M-ADS-03", "No live ad set has an ad that can deliver.")
    single = [s for s in live if per[s["id"]] == 1]
    exact = share(len(single), len(live))
    if exact < ONE_AD_WARN:
        return make("M-ADS-03", "pass", "%d of %s have a single ad." % (len(single), plural(len(live), "live ad set")))
    return make("M-ADS-03", "fail" if exact >= ONE_AD_FAIL else "warn", "%d of %s (%d%%) have a single ad, so nothing is being tested." % (len(single), plural(len(live), "live ad set"), round(exact)),
                items=names(single), count=len(single))
