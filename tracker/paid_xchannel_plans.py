"""Paid Marketing Agents: Cross-Channel & Attribution Analysis, how to act on a finding and what it is worth (no network).

The findings say what is different (the platforms count over different windows, Meta counts people who only saw the ad, one platform costs 3 times the other per conversion). This says
what to do about each, where to click, and the one thing that can be sized from the data: how much of Google's count lies beyond Meta's window, and what a 15 percent shift of budget
could buy at most. Anything that would change a live account's counting is said to be done one action at a time, because it changes what Smart Bidding learns from.

plan_for(finding, r) returns {"steps": [...], "worth": text or None, "note": text or None} or None. `r` is what paid_xchannel_findings.findings() was given.
"""
from __future__ import annotations

from . import paid_xchannel_findings as fd
from . import paid_xchannel_math as m

SHIFT = 0.15                         # the share of the dearer platform's budget the test moves (the findings say 10 to 20 percent)
META_MAX_CLICK = 7                   # the longest click window Meta offers, in days


def _both_ordered(r):
    ch = r["channels"]
    both = [n for n in ch if ch[n]["cur"]["conv"] >= m.MIN_CONV and ch[n]["cur"]["cpa"]]
    return sorted(both, key=lambda n: ch[n]["cur"]["cpa"]) if len(both) == 2 else []


def _budget_steps(plat, up, amount):
    if plat == "google":
        what = "In Google Ads open Campaigns, sort by Cost, %s the daily budgets of the campaigns with the %s cost per conversion by about %s in all." % (
            "raise" if up else "lower", "lowest" if up else "highest", amount)
    else:
        what = "In Ads Manager open the campaign's ad sets, sort by cost per result, and %s the budgets of the ad sets with the %s cost per result by about %s in all." % (
            "raise" if up else "lower", "lowest" if up else "highest", amount)
    return what


def _click_window(f, r):
    w = r.get("window") or {}
    gc, mc = w.get("google"), w.get("meta")
    if gc is None or mc is None:
        return None
    if gc > mc:
        steps = ["To see how much of Google's count the window explains, open Google Ads, then Goals, then Conversions, and segment by Days to conversion (or open Tools, then Attribution, then the Time lag report).",
                 "To make the two count the same way for good, open each conversion action (Goals, Conversions, Summary, the action, then Edit settings) and set the click-through conversion window to %d days. "
                 "That changes what Smart Bidding learns from and what the history shows from then on, so change one action at a time and write the date down." % mc]
        if mc <= META_MAX_CLICK:
            steps.append("Leave Meta's window alone: %d days is already at or near the longest it offers (%d), so Google's is the one that can be shortened." % (mc, META_MAX_CLICK))
        worth, note = None, None
        later = w.get("later")
        g = r["channels"].get("google")
        if later is not None and g and g["cur"]["conv"] > 0 and g["cur"]["cost"] > 0:
            kept = g["cur"]["conv"] * (1 - later)
            cur = r.get("currency") or ""
            if kept > 0:
                worth = ("About %s of Google's conversions came %d days or more after the click, which a %d-day window would not count. On that window Google's cost per conversion would be about %s instead of %s." % (
                    fd.pct(later * 100), mc, mc, fd.money(g["cur"]["cost"] / kept, cur), fd.money(g["cur"]["cost"] / g["cur"]["conv"], cur)))
        else:
            note = "How many of Google's conversions come later than %d days could not be read, so the effect of the window cannot be sized here; the Days to conversion segment shows it." % mc
        return {"steps": steps, "worth": worth, "note": note}
    steps = ["Meta can only be made to count over a shorter window: open each ad set (Ads Manager, the ad set, Edit, then Optimization and delivery) and set the attribution setting to the nearest "
             "click window Meta offers to Google's %d days (1 or 7)." % gc,
             "Do it one ad set at a time: changing the attribution setting restarts learning."]
    return {"steps": steps, "worth": None, "note": None}


def _view_through(f, r):
    return {"steps": ["In Ads Manager open Columns, then Compare attribution settings, and choose the 7-day click window on its own. That shows Meta's results without the people who only saw the ad.",
                      "Compare that figure, not the default one, with Google's Conversions. To make it the rule for an ad set, open the ad set, Edit, and set the attribution setting to 7-day click."],
            "worth": None, "note": "There is no figure for how many of Meta's conversions were view-through in this read; the Compare attribution settings view shows it."}


def _models(f, r):
    return {"steps": ["Type the conversions and revenue your own system recorded for the same dates in the boxes at the top of the page. That is the number both platforms are measured against.",
                      "Make sure your system can tell which platform sent each visit. Google: Admin, Account settings, then turn auto-tagging on. Meta: open the ad, Tracking, URL parameters, and add "
                      "utm_source=meta&utm_medium=paid&utm_campaign={{campaign.name}}&utm_content={{ad.name}}.",
                      "Count conversions by those tags in your CRM or analytics, and compare the count with each platform's claim."],
            "worth": None, "note": None}


def _goal_mismatch(f, r):
    return {"steps": ["In Google Ads open Goals, then Conversions, then Summary, and check which actions are Primary: those are the ones counted in Conversions and used for bidding.",
                      "In Ads Manager open each ad set (Edit, then Conversion) and check the conversion event it optimises for.",
                      "Pick one outcome (a booked appointment, a purchase) and make both platforms count it as their main conversion before you compare their costs."],
            "worth": None, "note": None}


def _secondary(f, r):
    return {"steps": ["In Google Ads open Goals, then Conversions, then Summary, and click the name of each action listed here.",
                      "In its settings choose whether it is a primary action (counted in Conversions and used for bidding) or a secondary one (reported only). Change a bidding action one at a time: "
                      "bidding relearns when the set of actions it optimises for changes."],
            "worth": None, "note": "The page's All conversions measure includes these actions if you want to see them beside the primary ones."}


def _cpa_gap(f, r):
    order = _both_ordered(r)
    if not order:
        return None
    cheap, dear = order
    ch, cur = r["channels"], r.get("currency") or ""
    shift = ch[dear]["cur"]["cost"] * SHIFT
    gain = shift / ch[cheap]["cur"]["cpa"] - shift / ch[dear]["cur"]["cpa"]
    steps = [_budget_steps(dear, False, "%d%%" % round(SHIFT * 100)),
             _budget_steps(cheap, True, "the same amount of money"),
             "Leave both changes in for two to four weeks and do not touch anything else in those campaigns.",
             "Judge it on the conversions your own system records for those weeks, not on what the platforms claim: type them in the boxes at the top of the page and compare with the four weeks before."]
    worth = None
    if gain >= 1:
        worth = ("Moving %d%% of %s's spend (%s) to %s at its average cost would buy about %s more conversions than it does now. It is an upper bound: cost per conversion rises as a platform takes more budget, "
                 "so the test is how much of it survives." % (round(SHIFT * 100), fd.name(dear), fd.money(shift, cur), fd.name(cheap), fd.num(gain)))
    return {"steps": steps, "worth": worth, "note": None}


def _overlap(f, r):
    return {"steps": ["Report the blended cost per recorded conversion (above) to the client as the real cost, with each platform's claimed cost beside it.",
                      "Set targets from the scaled costs: in Google Ads open the campaign's Settings, then Bidding, and keep the target cost per conversion no lower than the scaled cost; do the same with the cost "
                      "control on a Meta ad set. Move a target by no more than 10 to 15 percent at a time so bidding keeps learning.",
                      "Repeat the reconciliation each month: the share the platforms over-claim by moves with how much of the audience sees both."],
            "worth": None, "note": None}


BUILDERS = {"click-window": _click_window, "view-through": _view_through, "models": _models, "goal-mismatch": _goal_mismatch, "secondary": _secondary,
            "cpa-gap": _cpa_gap, "overlap": _overlap, "overlap-value": _overlap}


def plan_for(f, r):
    fid = f["id"]
    key = fid[5:] if fid.startswith("attr-") else fid
    build = BUILDERS.get(key)
    return build(f, r) if build else None
