"""Paid Marketing Agents: Call Transcript Analyzer, what to do about the calls that did not become appointments (no network, no call text).

The analysis counts why calls that were looking for care did not end in an appointment (no time free, insurance not accepted, a failed transfer, a callback left, a price question) and
how many calls were missed. This says what to do about each, in Google Ads, CallRail and at the front desk, and what it is worth: the calls in the range that ended that way (scaled up from
the sample that was read, said so) and the appointments that could mean if those callers booked at the rate the other callers who wanted care did.

The accounts are HIPAA accounts: nothing here reads or returns anything a caller said. The inputs are counts only.
"""
from __future__ import annotations

MIN_READ = 30                        # calls read before a share is scaled up to the range (the page already warns under this)
MISS_BAR = 0.10                      # share of missed calls before the missed-call plan is shown (a rule of thumb)
NOT_A_PATIENT_BAR = 0.10             # share of the calls read that are vendors, wrong numbers or job seekers before they get a plan

REASON_STEPS = {
    "no_availability": [
        "Tell the practice that callers wanting a new appointment found no time: ask for more new-patient slots (an extra hour a day, or slots held for new patients) in the scheduling system.",
        "While slots are short, stop paying for calls that cannot book: in Google Ads open Campaigns, then Audiences, keywords, and content, then Ad schedule, and run the ads only on the days and hours with slots. Or lower the daily budget of the campaigns for that location.",
        "Check the call asset's schedule too (Assets, then Call, then its schedule) so the number only shows when someone can book."],
    "insurance": [
        "Ask the practice which plans it takes and which it does not.",
        "Say the accepted plans where callers see them: add a callout asset in Google Ads (Assets, then Callouts) such as \"Most PPO plans accepted\", and the same line on the landing page.",
        "For plans it does not take, add them as phrase-match negative keywords (the campaign's Keywords, then Negative keywords) once the client has confirmed the list. A plan the practice takes under some terms is worth a question before it is blocked."],
    "transfer": [
        "Make test calls to each tracking number during opening hours and again after them, and note where the call is lost.",
        "In CallRail open the tracking number (Trackers) and check its call flow: the numbers it rings, the order, a fallback number and the hours. CallRail moves these menus, so look for Call flow in the number's settings.",
        "Ask the practice who answers when the first person is busy, and add them as a second number to ring."],
    "callback": [
        "Agree with the practice that every call from a paid source that ended in a voicemail or a promised call back is returned the same business day, by one named person.",
        "In CallRail filter the Calls list to calls from your paid sources that were not booked, and use it as the day's call-back list.",
        "If many of these land after hours, consider an answering service or a text-back for after-hours calls, and ask CallRail whether the number can send one."],
    "price": [
        "Give the front desk a short, agreed answer for price questions: a range, what affects it, and the offer of a consultation or exam to give an exact figure.",
        "Put what you can say publicly in the ad: a callout asset such as \"Financing available\" (Assets, then Callouts) and a price or financing line on the landing page, so fewer callers ring only to ask.",
        "For searches that are only about the cheapest price, review the Search terms report (Campaigns, then Insights and reports, then Search terms) and add negatives for words such as \"cheap\" and \"free\" if the practice does not offer them."],
    "other": [
        "Open the calls listed below in CallRail and listen to the first five, from the highest lead score down.",
        "Tag each one in CallRail with the real reason (for example Not Booked, plus a note), so the next read counts it instead of putting it under no clear reason.",
        "If the same reason keeps appearing, it belongs on this list: tell the person who maintains the analyzer."],
}
NOT_A_PATIENT_STEPS = [
    "In Google Ads open the search terms behind these calls (Campaigns, then Insights and reports, then Search terms) and look for job seekers, vendors and sales pitches.",
    "Add the words that bring them as phrase-match negative keywords (for example \"jobs\", \"careers\", \"hiring\"), after checking each against what the practice really offers.",
    "In CallRail mark the spam and wrong-number calls, so the lead counts are not inflated by them."]
MISSED_STEPS = [
    "Find which locations and sources miss the most: the tables on this page show each one's missed calls and miss rate.",
    "In CallRail open the tracking number for that location (Trackers) and check its call flow: a second number to ring, a fallback, the business hours and an after-hours voicemail or answering service, and turn on missed-call alerts. CallRail moves these menus, so look for Call flow in the number's settings.",
    "Have the front desk call back every missed call from a paid source the same business day (filter CallRail's Calls list to missed calls).",
    "If the misses cluster outside opening hours, set an ad schedule in Google Ads (Campaigns, then Audiences, keywords, and content, then Ad schedule) so ads do not pay for calls nobody can answer."]


def _scaled(n, a):
    """The calls in the range that look like `n` of the `read` ones, scaled up by how many calls match; None when too few were read."""
    read, matching = a.get("read") or 0, a.get("matching") or 0
    if read < MIN_READ or matching <= 0:
        return None
    return n / float(read) * matching


def _worth(n, a, what):
    est = _scaled(n, a)
    if est is None or est < 1:
        return None
    text = "In the sample, %d of %d calls read ended %s. Scaled up to the %s calls in this range that is about %s." % (n, a["read"], what, "{:,}".format(a["matching"]), "{:,.0f}".format(est))
    rate = a.get("bookRate")
    if rate:
        text += " If those callers booked at the rate other callers looking for care did (%d%%), that is about %s more appointments." % (round(rate * 100), "{:,.0f}".format(est * rate))
    return text + " It is an estimate: the sample is not every call and the reason is the likeliest one in the call, not a certain one."


WHAT = {"no_availability": "because no appointment time was free", "insurance": "because their insurance was not accepted", "transfer": "with a failed transfer",
        "callback": "with a promise to call back", "price": "with a question about price", "other": "with no clear reason in the call"}


def plans(a):
    """{"reasons": {key: {steps, worth}}, "notAPatient": {steps, worth} or None} for an analysis (paid_calls_topics.analyze plus `matching`). Only reasons that occurred get a plan."""
    out = {"reasons": {}, "notAPatient": None}
    for r in a.get("reasons") or []:
        steps = REASON_STEPS.get(r["key"])
        if steps:
            out["reasons"][r["key"]] = {"steps": list(steps), "worth": _worth(r["n"], a, WHAT[r["key"]])}
    read = a.get("read") or 0
    n = next((i["n"] for i in a.get("intents") or [] if i["key"] == "not_a_patient"), 0)
    if read >= MIN_READ and n / float(read) >= NOT_A_PATIENT_BAR:
        est = _scaled(n, a)
        worth = None
        if est is not None and est >= 1:
            worth = "In the sample, %d of %d calls read were vendors, wrong numbers or job seekers. Scaled up to the %s calls in this range that is about %s calls that were never patients: an estimate." % (
                n, read, "{:,}".format(a["matching"]), "{:,.0f}".format(est))
        out["notAPatient"] = {"steps": list(NOT_A_PATIENT_STEPS), "worth": worth}
    return out


def missed_plan(totals):
    """{steps, worth} for the range's missed calls (CallRail's own counts), or None when they are under the bar. `totals` is the overview's totals."""
    calls, missed = totals.get("calls") or 0, totals.get("missed") or 0
    if not calls or missed / float(calls) < MISS_BAR:
        return None
    answered, leads = totals.get("answered") or 0, totals.get("leads") or 0
    worth = "%s of %s calls (%d%%) were missed." % ("{:,}".format(missed), "{:,}".format(calls), round(missed / float(calls) * 100))
    if answered > 0 and leads > 0:
        rate = leads / float(answered)
        worth += " Answered calls became CallRail leads %d%% of the time, so if the missed ones had been answered that is about %s more leads: an estimate, since people who ring and get no answer often ring someone else." % (
            round(rate * 100), "{:,.0f}".format(missed * rate))
    return {"steps": list(MISSED_STEPS), "worth": worth}
