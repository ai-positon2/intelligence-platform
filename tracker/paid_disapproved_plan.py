"""Paid Marketing Agents: Disapproved Ads Tracker, what to do about the flagged ads (no network).

The plan is chosen from what was read: ad groups with no approved ad come first (they show nothing), then one step for each policy topic, the one flagging the most ads
first, then the check the next day. Each step says why it applies (the figures and what the policy usually means), how to do it in Google Ads and, where the weeks read
allow, what it is worth. The hints are the common fixes, not a ruling: Google's text in Policy manager is the ruling. The agent changes nothing.
"""
from __future__ import annotations

from . import paid_disapproved_math as m

MAX_TOPIC_STEPS = 4
POLICY_PATH = "In Google Ads open Tools, then Policy manager (the menu names move a little between versions): it lists every flagged ad with the policy and the reason."

TYPE_TEXT = {
    "PROHIBITED": "Google treats this as a prohibited use: an ad that breaks it is rarely approved on appeal, so change the ad (or the page) rather than appeal.",
    "FULLY_LIMITED": "Google limits this fully: the ad is allowed only where the policy permits, so check who the ad is allowed to show to.",
    "LIMITED": "Google limits this: the ad can run in some places or for some people, and some businesses can be approved for it.",
    "AREA_OF_INTEREST_ONLY": "The ad shows only to people whose area of interest is the place it is allowed.",
}


def _step(sid, title, why, how, worth=None, level="medium", campaigns=None):
    return {"n": 0, "id": sid, "title": title, "level": level, "why": why, "how": how, "worth": worth, "campaigns": campaigns or []}


def _worth(dead, cur, known):
    rows = [g["weight"] for g in dead if g["weight"]]
    if not known:
        return "How many clicks these ad groups used to bring could not be read, so it is not sized here."
    if not rows:
        return "None of these ad groups had a click in the weeks read, so there is nothing to size."
    usual, last = sum(r["usualClicks"] for r in rows), sum(r["lastClicks"] for r in rows)
    lost, cost = sum(r["lostClicks"] for r in rows), sum(r["usualCost"] for r in rows)
    text = "Before, these ad groups made about %s clicks a week and spent about %s; last week they made %s" % (format(usual, ","), m.money(cost, cur), format(last, ","))
    if lost:
        text += ", so about %s clicks a week are not coming. The money is not lost (the budget goes to other ad groups), but the searches these ad groups answered are not answered: an estimate from the last 5 whole weeks" % format(lost, ",")
    text += "."
    so_far = [g for g in dead if g.get("lostSoFar")]
    if so_far:
        text += " The oldest has been stopped for %s: about %s clicks lost since, at the usual pace." % (m.plural(max(g["ageDays"] for g in so_far), "day"), format(sum(g["lostSoFar"] for g in so_far), ","))
    return text


def _cover_step(a, cur):
    dead = a["deadGroups"]
    names = ", ".join('"%s"' % g["name"] for g in dead[:3]) + (" and %d more" % (len(dead) - 3) if len(dead) > 3 else "")
    return _step("cover", "Get the ad groups that cannot show an ad running again",
                 "%s (%s) %s no approved ad: every enabled ad in %s is disapproved, so none of its keywords can show." % (
                     m.plural(len(dead), "ad group"), names, "has" if len(dead) == 1 else "have", "it" if len(dead) == 1 else "them"),
                 ["In Google Ads open Ads & assets, then Ads, and filter Ad status to Disapproved (or open Tools, then Policy manager) to find them.",
                  "As a stopgap, add a plain new responsive search ad to each ad group: open the ad group, click +, choose Responsive search ad, give it the final URL of the page the old ad used, "
                  "and write headlines and descriptions in plain words without the text the policy points at (the steps below say what to avoid). A new ad is usually reviewed within a business day, and the ad group shows again once it is approved.",
                  "Then fix or remove the disapproved ads (the steps below), so an ad group does not depend on one ad again."],
                 _worth(dead, cur, a["weightKnown"]), "high", sorted({g["campaign"] for g in dead})[:3])


def _topic_step(t, a, cur):
    none = t["topic"] == m.NO_TOPIC
    why = t["hint"]
    if t["type"] in TYPE_TEXT:
        why += " " + TYPE_TEXT[t["type"]]
    if t["examples"]:
        why += " Google pointed at: %s." % "; ".join('"%s"' % e for e in t["examples"])
    why = "%s flagged for this%s. %s" % (m.plural(t["ads"], "ad is", "ads are"), " (in %s with no approved ad)" % m.plural(t["dead"], "ad group") if t["dead"] else "", why)
    if t.get("ageDays"):
        why += " It has been flagged for %s." % m.plural(t["ageDays"], "day")
    if t.get("reopened"):
        why += " It has come back %s after being fixed: look in Tools, then Change history for who or what puts it back (a template, a bulk upload, a rule)." % m.plural(t["reopened"], "time")
    how = [POLICY_PATH,
           "Open one flagged ad (Ads & assets, then Ads) and point at the status in the Status column: it names the policy and, when Google gives it, the text or the page it objects to.",
           "Edit that one ad to take the problem out and save it: a changed ad is reviewed again, usually within a business day. Check it comes back approved before you change the rest, so one wrong guess does not flag them all.",
           "If the ad already follows the policy as it is written, use Appeal in Policy manager instead of editing it (appeals are not offered for every policy, and Google answers in a few business days). An ad under appeal stays flagged until Google decides."]
    if t["topic"] != m.NO_TOPIC and "DESTINATION" in t["topic"].upper():
        how[2] = "This is about the page, not the words: open the final URL in a private window and on a phone. Fix what is wrong with it (it must load, match the ad and not send people elsewhere), then save the ad again so it is reviewed again."
    title = ("Fix the ads flagged for %s (%s)" % (t["label"], m.plural(t["ads"], "ad"))) if not none else "Find out why %s flagged" % m.plural(t["ads"], "ad is", "ads are")
    return _step("topic-" + t["topic"].lower(), title, why, how, None, "high" if t["dead"] else ("medium" if t["disapproved"] else "low"), t["campaigns"][:3])


def _common_step(a, cur):
    sus = a["suspects"]
    top = sus[0]
    lines = []
    for x in sus[:3]:
        what = {"headline": 'the headline "%s"' % x["text"], "evidence": 'the text "%s"' % x["text"], "url": "the page %s" % x["text"]}[x["kind"]]
        lines.append("%s is in %d of %d" % (what, x["ads"], x["of"]))
    how = ["In Google Ads open Ads & assets, then Ads, and filter the ads by the text (the Headline contains filter) so that only the ones that share it are shown.",
           "Edit the text once for all of them: select the ads, choose Edit, then Find and replace text in ads (in Google Ads Editor, Make multiple changes, then Find and replace). Replace it with wording that follows the policy, and save.",
           "Keep a copy of the old wording to see whether it comes back (a template, a bulk upload or a rule may put it there again)."]
    if top["kind"] == "url":
        how = ["Open the page %s in a private window and on a phone: it must load, match the ads and not send people elsewhere." % top["text"],
               "Fix the page once; every ad that points at it is reviewed again. If the page cannot be fixed, change the final URL of those ads together (Ads & assets, then Ads, select them, Edit, then Change final URLs)."]
    return _step("common", "Fix the shared cause once", "Among the flagged ads, %s. One shared cause is the likeliest reason so many are flagged together, so one change can clear many." % "; ".join(lines),
                 how, "Up to %s of the %s flagged ads could clear with this one change." % (top["ads"], top["of"]), "high" if a["deadGroups"] else "medium")


def _keywords_step(a, cur):
    kws = [i for i in a["items"] if i["state"] == "keyword"]
    topics = ", ".join("%s (%s)" % (t["label"].lower(), t["keywords"]) for t in a["keywordTopics"][:3])
    return _step("keywords", "Fix the disapproved keywords",
                 "%s disapproved%s. A disapproved keyword cannot trigger any ad, so the searches it was meant for are not answered." % (m.plural(len(kws), "keyword is", "keywords are"), (": " + topics) if topics else ""),
                 ["In Google Ads open Keywords (Campaigns, then Keywords), and filter the Status column to Disapproved. Point at the status of one: it names the policy.",
                  "Change the keyword to words that follow the policy (a keyword that names a medicine, a trademark or a restricted service often needs rewording), or remove it if the searches are not worth the effort; a change is reviewed again.",
                  "If the keyword is fine as written, use Appeal in Policy manager. Watch the Disapproved count here the next day."], None, "medium", sorted({i["campaign"] for i in kws})[:3])


def build(a, currency="USD"):
    """{steps, note} for one analysed account (the dict analyse() builds before the plan and the findings are added). No steps for a clear account."""
    if a["status"] == "clear":
        return {"steps": [], "note": None}
    steps = []
    if a["deadGroups"]:
        steps.append(_cover_step(a, currency))
    if a.get("suspects") and len([i for i in a["items"] if i["kind"] != "keyword"]) >= 3:
        steps.append(_common_step(a, currency))
    if a["counts"].get("keywords"):
        steps.append(_keywords_step(a, currency))
    for t in a["topics"][:MAX_TOPIC_STEPS]:
        steps.append(_topic_step(t, a, currency))
    steps.append(_step("again", "Check again the next business day",
                       "Google reviews an edited ad within about a business day, and an appeal takes a few. Nothing is fixed until the ad shows as approved.",
                       ["Press Refresh on this page: an ad that came back approved is gone from the list, and one still flagged shows its reason again.",
                        "An ad under appeal stays listed until Google decides. If it is refused, change the ad as the step for its policy says."], None, "low"))
    for i, s in enumerate(steps, 1):
        s["n"] = i
    more = len(a["topics"]) - MAX_TOPIC_STEPS
    note = "The reasons are Google's own policy names turned into plain words and the hints are the usual fixes, not a ruling: the text in Policy manager is the ruling."
    if more > 0:
        note += " %d more policy topic%s in the table above." % (more, " is" if more == 1 else "s are")
    if not a["coverKnown"]:
        note += " Whether another approved ad covers each flagged ad could not be read, so no ad group is called stopped."
    return {"steps": steps, "note": note}
