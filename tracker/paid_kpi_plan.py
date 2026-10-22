"""Paid Marketing Agents: KPI Monitoring, what to fix and in what order (no network).

One step per rule that found something: what stops a campaign from showing or converting first (not delivering, a misconfigured bid strategy, a campaign that stopped converting), then the
cost and return that are off target, then the campaigns held back (learning, budget), then the rates. Each step says how many campaigns it covers, says what to do about it and points at the
first campaigns to open. An account with no target saved gets a first step that says so: without a target only delivery, learning and the budget can be checked.
"""
from __future__ import annotations

WHERE = 3                         # campaigns named under a step
EXAMPLES = 3

TITLES = {
    "not_delivering": "Get %s showing again",
    "bidding": "Fix the bid strategy of %s",
    "no_conv": "Find out why %s stopped converting",
    "cpa": "Bring the cost per conversion of %s back to its target",
    "roas": "Bring the return on ad spend of %s back to its target",
    "learning": "Leave %s alone while it learns, and feed it conversions if it is limited by data",
    "budget": "Decide the budget of %s",
    "cvr": "Look at the conversion rate of %s",
    "ctr": "Look at the click-through rate of %s",
}


def _n(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def build(a):
    """{steps: [...]} for one analysed account (see tracker/paid_kpi_math.py); steps is empty when there is nothing to say."""
    steps = []
    if not a.get("targetsSet") and a.get("campaigns"):
        steps.append({"id": "targets", "title": "Save the targets of this account", "level": "info", "campaigns": a["campaigns"], "findings": 0,
                      "how": "Save the cost per conversion (or lead), the return on ad spend and the lowest conversion and click-through rates the client wants, in the form below. Until then only "
                             "delivery, learning, the budget and a campaign that stopped converting are checked.", "where": [], "examples": []})
    for b in a["byRule"]:
        rule = b["rule"]
        where, examples, items = [], [], 0
        for it in a["items"]:
            hit = [f for f in it["findings"] if f["rule"] == rule]
            if not hit:
                continue
            items += 1
            if len(where) < WHERE:
                where.append(it["name"])
            for f in hit:
                if len(examples) < EXAMPLES:
                    examples.append(f["detail"])
        steps.append({"id": rule, "title": TITLES[rule] % _n(items, "campaign"), "level": b["level"], "campaigns": items, "findings": b["findings"], "how": b["fix"], "where": where,
                      "examples": examples})
    if any(s["id"] != "targets" for s in steps):
        steps.append({"id": "recheck", "title": "Check again the next day", "level": "info", "campaigns": 0, "findings": 0,
                      "how": "Press Refresh to read the account again, or let the next daily run do it. Give a change a week before judging it: the figures are the last 7 days, and a bid strategy that just changed is still learning.",
                      "where": [], "examples": []})
    return {"steps": steps}
