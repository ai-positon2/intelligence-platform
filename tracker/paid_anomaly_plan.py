"""Paid Marketing Agents: Spend Anomaly Alert, what to check and in what order (no network).

One step for each finding above information, the worst first and, among equals, what is happening now before what happened yesterday: a surge, then a spike, a ceiling passed, more than
the budgets allow, and last a drop. A step names the campaigns that moved (when the account's campaigns could be read), says what to look at and what each answer means, and ends with the
way out when the spend was planned (muting it on the page, so nobody is alerted for a push everybody knew about). The agent only reads: nothing here changes an account.
"""
from __future__ import annotations

from .paid_brandspend_math import money

ORDER = ("surge", "spike", "ceiling", "budget", "drop")
MAX_STEPS = 6
MAX_WHERE = 3
_PLANNED = "If the spend was planned (a sale, a launch, a client's top-up), save it as a planned push for this account below, so it is not alerted."

ACTIONS = {
    "surge": [
        "Open the account in Google Ads or Meta now and sort the campaigns by cost for today: the campaign that is spending this fast is the one to look at.",
        "Check the change history for the last day: a budget raised, a new campaign, a bid strategy switched or a campaign turned on. Anyone who should not have made the change is the answer.",
        "If nobody can explain it, pause the campaign that is spending and call the account manager before turning it on again. If a person you do not know has access, remove them first.",
        _PLANNED,
    ],
    "spike": [
        "Sort the campaigns by cost for the day and find the ones above their usual; the cause shown above names the ones that moved the most.",
        "Compare their budgets and bids with last week, and read the change history for who changed what.",
        "Look at the search terms or placements those campaigns paid for, in case the spend is clicks nobody wanted (invalid clicks can be reported to Google for a refund).",
        _PLANNED,
    ],
    "ceiling": [
        "The team set a daily ceiling for this account and it has been reached. Check the budgets and what spent, as for a spike.",
        "Tell the account manager the same day. If the ceiling is out of date, change it below.",
    ],
    "budget": [
        "Add up the budgets of the campaigns that were on and compare them with what was spent: Google may spend up to twice a campaign's daily budget on one day, never more.",
        "More than that means a budget was raised or removed during the day, a shared budget spans more campaigns than expected, or a campaign was paused after it had spent. The change history says which.",
        "Fix the budget that is wrong and tell the account manager.",
    ],
    "drop": [
        "Check that the account can spend: billing and the payment method, the account's status, and any suspension notice.",
        "Look for ads that were disapproved, campaigns that were paused or removed, and budgets that ran out early.",
        "If tracking changed (a conversion action removed, a bid strategy stuck in learning), the bidding may have stopped itself. Compare with the change history.",
    ],
}
KIND_WORD = {"surge": "Spending right now", "spike": "Spending above usual", "ceiling": "Over the team's ceiling", "budget": "More than the budgets allow", "drop": "Spending stopped or dropped"}


def _movers(f, currency):
    c = f.get("cause") or {}
    return ["\"%s\": %s against a usual %s" % (x["name"], money(x["cost"], currency), money(x["usual"], currency)) for x in (c.get("movers") or [])[:MAX_WHERE]]


def build(a):
    """{steps: [...]} for one analysed account (see tracker/paid_anomaly_math.py); steps is empty when nothing was found."""
    found = [f for f in a["findings"] if f["level"] in ("high", "medium", "low")]
    found.sort(key=lambda f: ({"high": 0, "medium": 1, "low": 2}[f["level"]], ORDER.index(f["kind"]), f["day"] != "today", f["key"]))
    steps = []
    for f in found[:MAX_STEPS]:
        steps.append({"id": "%s:%s" % (f["kind"], f["key"]), "kind": f["kind"], "title": f["title"], "level": f["level"], "day": f["day"], "evidence": f["detail"], "actions": list(ACTIONS[f["kind"]]),
                      "where": [x["name"] for x in (f.get("cause") or {}).get("movers", [])[:MAX_WHERE]], "examples": _movers(f, a.get("currency") or "USD")})
    if steps:
        steps.append({"id": "recheck", "kind": "recheck", "title": "Check again later today", "level": "info", "day": "", "evidence": "",
                      "actions": ["Press Refresh to read the account again. The monitor also reads every account every two hours and alerts on what is new, what got worse and what was fixed."],
                      "where": [], "examples": []})
    return {"steps": steps}
