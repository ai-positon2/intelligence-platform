"""Paid Marketing Agents: Account Audit, the fix plans for the budget and bidding checks (no network).

A check says what is wrong with the account and gives one fixed paragraph of advice. For the checks where the account's own numbers can say more, the finding also
carries a plan: ordered steps, each with why it applies (the figures behind it), how to do it in Google Ads and what it is worth. A plan is chosen from what the audit read
(each campaign's budget, bidding strategy and target, the shares of impressions lost to budget and rank, the last 30 days' cost, clicks and conversions). A figure that is
an estimate says so; nothing is promised. `money` is the check context's own formatter.

Campaigns are the dicts the audit reads (tracker/paid_audit_google_read.py parse_campaigns): name, type, bidding, targetKind, target, budget, budgetShared, and metrics
(impressions, clicks, cost, conversions, budgetLost, rankLost, share: the shares as percentages, 0 to 100, or None).
"""
from __future__ import annotations

DAYS = 30                       # the window the audit reads
SMART = ("TARGET_CPA", "TARGET_ROAS", "MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE")
MAX_LINES = 8                   # campaigns named one by one in a step


def step(sid, title, why, how, effect=None, campaigns=None):
    return {"id": sid, "title": title, "why": why, "how": [h for h in how if h], "effect": effect, "campaigns": list(campaigns or [])}


def _names(items, key=lambda x: x["name"]):
    names = [key(x) for x in items]
    return ", ".join(names[:3]) + (" and %d more" % (len(names) - 3) if len(names) > 3 else "")


def _say(strategy):
    return (strategy or "unknown").replace("_", " ").lower()


def _per_day(x):
    return x["metrics"]["cost"] / float(DAYS)


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


# ── Held back by budget ─────────────────────────────────────────────────────

def extra_per_day(x):
    """What winning back the impressions lost to budget would cost a day at the campaign's own cost per impression, or None when its impression share was not read.
    The lost share over the share it won is the extra volume; spend rises with it."""
    m = x["metrics"]
    if m["budgetLost"] is None or not m["share"]:
        return None
    return _per_day(x) * m["budgetLost"] / m["share"]


def budget_plan(held, money, dead=None):
    """Steps for converting campaigns held back by budget. `held` is [(campaign, budget lost %)], `dead` the campaigns that spend and do not convert, [(campaign)]."""
    camps = [x for x, _lost in held]
    lines, total, conv_total, unsized = [], 0.0, 0.0, 0
    for x in camps[:MAX_LINES]:
        m = x["metrics"]
        extra = extra_per_day(x)
        now = x["budget"]
        if extra is None:
            unsized += 1
            lines.append("%s: raise the daily budget%s. Google gave no impression share for it, so the amount cannot be worked out: try about 20%% more and check again in a week." % (
                x["name"], " from %s" % money(now) if now else ""))
            continue
        total += extra
        gain = m["conversions"] * m["budgetLost"] / m["share"]
        conv_total += gain
        lines.append("%s: from %s to about %s a day (%s more), which should recover about %d%% of the impressions it lost and roughly %d more conversions in 30 days if the extra ones convert like the rest." % (
            x["name"], money(now) if now else "its current budget", money((now or 0) + extra) if now else "%s more than now" % money(extra), money(extra), round(m["budgetLost"]), round(gain)))
    why = "%s converted last month and still ran out of budget (%s)." % (
        _plural(len(camps), "campaign"), "; ".join("%s lost %d%% of its impressions to budget" % (x["name"], round(l)) for x, l in held[:3]) + ("; and %d more" % (len(held) - 3) if len(held) > 3 else ""))
    how = ["In Google Ads open Campaigns, and click the amount in the Budget column of each campaign named below (or open the campaign, then Settings, then Budget)."] + lines
    if len(camps) > MAX_LINES:
        how.append("%d more campaigns are capped as well; the Excel file lists them." % (len(camps) - MAX_LINES))
    shared = [x for x in camps if x.get("budgetShared")]
    if shared:
        how.append("%s use%s a shared budget: raising it lets every campaign on it spend more. If only the converting one should, give it a budget of its own (Budget, then Create a new budget)." % (
            _names(shared), "s" if len(shared) == 1 else ""))
    targeted = [x for x in camps if x.get("targetKind")]
    if targeted:
        how.append("%s bid%s to a target %s, so a bigger budget only spends if the target allows it: if the extra budget goes unused, loosen the target as well." % (
            _names(targeted), "s" if len(targeted) == 1 else "", "CPA" if targeted[0]["targetKind"] == "cpa" else "ROAS"))
    out = []
    effect = ("About %s more a day (%s over 30 days), for roughly %d more conversions: an estimate that assumes the extra impressions convert and cost like the rest." % (
        money(total), money(total * DAYS), round(conv_total))) if total else None
    out.append(step("budget", "Raise the budget of the converting campaigns that run out", why, how, effect, [x["name"] for x in camps]))
    if dead:
        dead_cost = sum(x["metrics"]["cost"] for x in dead)
        out.append(step("fund", "Pay for it from campaigns that spend and do not convert",
                        "%s spent %s in 30 days and recorded no conversion." % (_plural(len(dead), "campaign"), money(dead_cost)),
                        ["Check their conversion tracking and landing pages first (see the no-conversions check), because a campaign that converts but is not measured would be cut by mistake.",
                         "Then lower the budget of %s, or pause what still has no conversion, and put that money on the capped campaigns above." % _names(dead)],
                        "Moving all of it would free about %s a day." % money(dead_cost / float(DAYS)), [x["name"] for x in dead]))
    elif unsized == 0 and total:
        out.append(step("fund", "Fund it from the campaigns that earn least",
                        "No campaign here spends without converting, so the money has to come from the weakest converting campaigns or from a higher account budget.",
                        ["Compare cost per conversion across the campaigns (Campaigns, then add the Cost / conv. column) and lower the budget of the ones well above the account average."]))
    return out


# ── Lost to rank ────────────────────────────────────────────────────────────

def rank_plan(big, money):
    """Steps for big Search campaigns losing a lot of impressions to rank. `big` is the campaigns, biggest spend first."""
    lines = []
    for x in big[:MAX_LINES]:
        m = x["metrics"]
        cpc = m["cost"] / m["clicks"] if m["clicks"] else None
        extra = (m["cost"] * m["rankLost"] / m["share"]) if m["share"] else None
        lines.append("%s: %d%% lost to rank%s; bidding is %s%s." % (
            x["name"], round(m["rankLost"]), (", average CPC %s" % money(cpc)) if cpc else "", _say(x["bidding"]),
            (" (target %s %s)" % (x["targetKind"].upper(), ("%.2f" % x["target"]) if x["targetKind"] == "roas" else money(x["target"]))) if x.get("targetKind") and x.get("target") else ""))
        if extra:
            lines.append("Winning it back for %s would take at least %s more over 30 days at the same cost per impression (more once bids rise): an estimate." % (x["name"], money(extra)))
    strategies = {x["bidding"] for x in big}
    how = ["First fix quality, because it lowers the price of rank: open Keywords for the campaign, add the Quality Score, Exp. CTR, Ad relevance and Landing page exp. columns, and fix whatever is Below average (the brand or product name in headline 1, one tight group of keywords per ad, a landing page that matches the search)."]
    if strategies & {"TARGET_CPA", "TARGET_ROAS"}:
        how.append("Then loosen the target: for a target CPA raise it by about 20% to 30%, for a target ROAS lower it by about the same, and read the campaign again in two weeks. Do not change the target and the budget together.")
    if strategies & {"MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE"}:
        how.append("A strategy with no target bids what it expects each search to be worth: add the missing conversion signals (an imported lead status, a value) so it can bid higher where searches are worth more.")
    if strategies & {"TARGET_SPEND", "ENHANCED_CPC", "MANUAL_CPC"}:
        how.append("Raise the bids on the keywords that convert, by about 20% at a time (Keywords, sort by conversions), or the maximum CPC limit on maximize clicks, and check again after a week.")
    how.append("Open Auction Insights for the campaign (Insights and reports) to see which competitor outranks it: the Google Ads API does not carry that report, so it has to be read by hand.")
    return [step("rank", "Raise the rank of the Search campaigns that lose most to it",
                 "%s lost 40%% or more of their impressions to rank, which means the ads were eligible and lost the auction." % _plural(len(big), "big Search campaign"),
                 lines + how, None, [x["name"] for x in big])]


# ── Search partners ─────────────────────────────────────────────────────────

def partner_plan(cost_p, conv_p, cpa_p, cpa_s, money):
    saving = "Turning it off would save up to %s a month, and lose the %s it brought." % (money(cost_p), "no conversions" if not conv_p else _plural(round(conv_p), "conversion"))
    return [step("partners", "Turn off Search Partners where it costs more",
                 "Search Partners spent %s in 30 days and %s." % (money(cost_p), "recorded no conversions" if not conv_p else "cost %s per conversion against %s on Google Search" % (money(cpa_p), money(cpa_s))),
                 ["In Google Ads open each Search campaign, then Settings, then Networks, and untick Include Google search partners.",
                  "To see which campaigns it hurts most first, open Reports, then Predefined reports, then Basic, then Campaign, and segment by Network (with search partners).",
                  "Do it one campaign at a time and read the cost per conversion again after two weeks: partner traffic is sometimes the cheaper kind."],
                 saving)]


# ── Manual bidding with the conversions to use Smart Bidding ────────────────

def manual_plan(ready, money):
    lines = []
    for x in ready[:MAX_LINES]:
        m = x["metrics"]
        cpa = m["cost"] / m["conversions"] if m["conversions"] else None
        lines.append("%s: %d conversions in 30 days at %s each; bidding is %s." % (x["name"], round(m["conversions"]), money(cpa) if cpa else "n/a", _say(x["bidding"])))
    return [step("smart", "Move the converting Search campaigns to Maximize conversions",
                 "%s already record enough conversions to bid on, and still bid by clicks or by hand." % _plural(len(ready), "campaign"),
                 lines + ["Open the campaign, then Settings, then Bidding, then Change bid strategy, and choose Maximize conversions. Leave the target blank for the first two weeks.",
                          "Do one campaign at a time and do not change the budget or the ads in the same fortnight: the campaign goes through a learning period and its numbers swing until it ends.",
                          "After two weeks, if it is steady, add a target CPA at about what it costs now (the figures above), not lower: tightening the target later is easier than recovering from setting it too low."],
                 "No gain is promised: Google bids on every search's likelihood to convert, which usually does better than a fixed bid when there are enough conversions.", [x["name"] for x in ready])]


# ── Smart Bidding without the conversions to learn from ─────────────────────

def thin_plan(thin, need, money):
    lines = []
    for x in thin[:MAX_LINES]:
        m = x["metrics"]
        n = need(x)
        lines.append("%s: %d conversions in 30 days, about %d suggested, %d short." % (x["name"], round(m["conversions"]), n, max(0, n - round(m["conversions"]))))
    return [step("thin", "Give the Smart Bidding campaigns the conversions they need",
                 "%s bid on conversions without the volume Google suggests, so bids swing and targets are missed." % _plural(len(thin), "campaign"),
                 lines + ["Merge campaigns that target the same thing into one, so they share conversions (pause the small one after its ads and keywords are copied across).",
                          "Or put them on one portfolio strategy: Tools, then Bid strategies, then the + button, and add the campaigns to it, so they learn together.",
                          "Or count a nearer step as a second signal: in Goals, then Conversions, mark a form start or a call as a conversion for bidding, so the campaign has more to learn from."],
                 None, [x["name"] for x in thin])]


# ── Spending without converting ─────────────────────────────────────────────

def dead_plan(dead, spend, money):
    cost = sum(x["metrics"]["cost"] for x in dead)
    lines = ["%s: %s, %s clicks, no conversion." % (x["name"], money(x["metrics"]["cost"]), format(x["metrics"]["clicks"], ",")) for x in dead[:MAX_LINES]]
    return [step("dead", "Find out why the campaigns that spend are not converting",
                 "%s spent %s in 30 days (%d%% of the account) with no conversion." % (_plural(len(dead), "campaign"), money(cost), round(100 * cost / spend) if spend else 0),
                 lines + ["First check the tracking: open Goals, then Conversions, then Summary, and see whether the conversion actions show Recording conversions or No recent conversions. If the tag or import is broken, nothing below matters yet.",
                          "If conversions are imported from a CRM they arrive days or weeks after the click: look at the import delay before judging recent clicks.",
                          "Then look at what the clicks searched: open the campaign, then Insights and reports, then Search terms, and add negative keywords for terms that cannot convert.",
                          "Then check the landing page loads on a phone and matches the ad (the Landing Page Check agent does this for the whole account).",
                          "If the tracking is fine and the terms and page are right, pause the campaign or cut its budget and move the money to a converting one."],
                 "Pausing all of them would save about %s a month, so settle the tracking question first." % money(cost), [x["name"] for x in dead])]
