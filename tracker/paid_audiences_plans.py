"""Paid Marketing Agents: Audience Segment Analysis, where to click and what each change is worth (no network).

paid_audiences_insights.action_for() says "set its bid adjustment to +25%" or "give it an ad group of its own". This says how, in the screens where the audience sits, using the
campaigns the audience is really in (and not guessing which kind of bidding they use: the table already knows), and what the change is worth. A figure about the future is
an estimate and says what it assumes. A list that cannot serve gets the steps for the reason it cannot.
"""
from __future__ import annotations

from . import paid_audiences_insights as ins

NAMES_SHOWN = 3
GROW_STEP = 0.20                       # Meta budget moves of at most this share at a time, so the ad set stays out of the learning phase


def money(v, cur="USD"):
    return ins.money(v, cur, 0 if abs(v) >= 100 else 2)


def _where(uses, pred=None):
    """The campaigns (or, for Meta, ad sets) an audience is in, the biggest spenders first, as a short list the steps can name."""
    seen, out = set(), []
    for u in sorted(uses, key=lambda u: -(u.get("m") or {}).get("cost", 0)):
        if pred and not pred(u):
            continue
        name = u.get("adGroup") if u.get("platform") == "meta" and u.get("adGroup") else u.get("campaign")
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    shown = out[:NAMES_SHOWN]
    return ('"%s"' % '", "'.join(shown) + (" and %d more" % (len(out) - NAMES_SHOWN) if len(out) > NAMES_SHOWN else "")) if shown else "the campaign"


def _signed(adj):
    return "%+d%%" % round(adj * 100)


def _exclude_steps(row, mode_note=""):
    if row["mode"] == "targeting":
        return ["Open Audiences, keywords, and content in %s, then Audiences. Tick \"%s\", choose Edit, then Remove, so it stops being the audience the ads are aimed at." % (_where(row["uses"]), row["label"]),
                "To keep it from ever being reached, open the Exclusions tab on the same page, click the +, and add \"%s\" as an exclusion at the campaign or ad group level." % row["label"]]
    return ["Open Audiences, keywords, and content in %s, then the Exclusions tab, click the +, and add \"%s\" at the campaign or ad group level. Then save.%s" % (_where(row["uses"]), row["label"], mode_note)]


def _bid_steps(row, adj):
    where = _where(row["uses"], ins.adjustable)
    cur = row.get("bid")
    now = "it is at %s now" % _signed(cur) if cur is not None else "it differs by campaign now" if row.get("bidVaries") else "it has none now"
    return ["Open Google Ads, then Campaigns, click %s, and choose Audiences, keywords, and content in the left menu, then Audiences." % where,
            "Find \"%s\" (%s) in the table, click its Bid adjustment cell (the pencil), and set it to %s (%s). Save." % (row["label"], row["kindLabel"].lower(), _signed(adj), now)]


def _own_campaign_steps(row, raise_):
    where = _where(row["uses"])
    strategies = {u.get("bidding") for u in row["uses"] if u.get("bidding")}
    why = "Smart Bidding sets the bids in %s, so an audience bid adjustment is ignored there" % where if strategies else "%s takes no audience bid adjustment" % where
    if any(u["ctype"] in ins.NO_BID_TYPES for u in row["uses"]):
        why = "%s takes no audience bid adjustment (Demand Gen and Performance Max set their own bids and treat an audience as a pointer, not a limit)" % where
    steps = ["%s, so the change has to be to the audience itself, not a bid." % why]
    if raise_:
        steps += ["Give it its own space: in Google Ads select the campaign, choose Edit, Copy, and paste it as a new campaign (or add a new ad group for it). In the copy open Audiences, keywords, and content, then Audiences, and keep only \"%s\", set to Targeting." % row["label"],
                  "Give the copy its own daily budget, and a target a little more generous than the original's (start about 10% looser). Leave the original running so the two can be compared after two weeks."]
    else:
        steps += ["Move it out of the way: open Audiences, keywords, and content in %s, then Audiences, and either exclude \"%s\" (the Exclusions tab, then the +) or, to keep it on a small budget, copy the campaign as above, keep only this audience in the copy and give the copy a small daily budget." % (where, row["label"])]
    return steps


def _meta_steps(row, kind):
    where = _where(row["uses"])
    if kind == "raise":
        return ["Open Ads Manager and find the ad sets %s." % where,
                "Duplicate the best one (select it, then Duplicate), and in the copy open Audience and keep only \"%s\" as the audience. Give the copy its own budget." % row["label"],
                "Raise a budget by no more than about %d%% at a time, so the ad set stays out of the learning phase, and look again after two weeks." % round(GROW_STEP * 100)]
    if kind == "lower":
        return ["Open Ads Manager and find the ad sets %s." % where,
                "Lower their budgets by no more than about %d%% at a time. If \"%s\" is still weak after a month, open the ad set, then Audience, and remove it from the audiences it includes." % (round(GROW_STEP * 100), row["label"])]
    return ["Open Ads Manager and find the ad sets %s." % where,
            "In each ad set open Audience and take \"%s\" out of the audiences it includes; to keep it from being reached by other ad sets too, add it under Exclude." % row["label"]]


def _worth(row, action, adj, cur):
    cost, conv = row.get("cost") or 0.0, row.get("conversions") or 0.0
    cpa = cost / conv if conv else None
    if action == "raise" and adj and cost > 0:
        extra = cost * adj
        text = "A %s change on the %s it spent would move about %s more through it" % (_signed(adj), money(cost, cur), money(extra, cur))
        if cpa:
            text += "; at its own cost per conversion (%s) that is about %d more conversions, though extra spend usually converts a little worse" % (money(cpa, cur), round(extra / cpa))
        return text + ": an estimate, since spend does not rise exactly with the bid."
    if action == "lower" and adj and cost > 0:
        saved = cost * -adj
        text = "Lowering it %s on the %s it spent would save up to about %s" % (_signed(adj), money(cost, cur), money(saved, cur))
        if conv:
            text += ", and cost up to about %d conversions" % round(conv * -adj) if round(conv * -adj) else ""
        return text + ": an estimate, since spend does not fall exactly with the bid. The conversions it would cost are the fewest, because this audience converts below average."
    if action == "exclude" and cost > 0:
        return "It spent %s without a conversion in this range. Excluding it stops that spend; the money goes to the audiences that do convert, where the next dollars usually earn less than the first: an estimate." % money(cost, cur)
    return None


def action_plan(row, action, adj, cur="USD"):
    """{steps, worth} for one audience's action (raise, lower, exclude), or None for hold and none. `row` is a judged row of paid_audiences_insights.analyse()."""
    kind = action.get("kind")
    if kind not in ("raise", "lower", "exclude") or not row.get("uses"):
        return None
    if row.get("platform") == "meta":
        return {"steps": _meta_steps(row, kind), "worth": _worth(row, kind, adj, cur)}
    can_bid = any(ins.adjustable(u) for u in row["uses"])
    observing = row["mode"] == "observation"
    if kind == "raise":
        steps = _bid_steps(row, adj) + (["It is set to Observation, so the adjustment only changes the bid: it does not narrow who sees the ads."] if observing else []) if can_bid else _own_campaign_steps(row, True)
    elif kind == "lower":
        if can_bid:
            steps = _bid_steps(row, adj) + (["If it is still weak after a month, exclude it:"] + _exclude_steps(row) if observing else ["If it is still weak after a month, remove it from the targeting:"] + _exclude_steps(row))
        else:
            steps = _own_campaign_steps(row, False)
    else:
        if can_bid and observing:
            steps = _bid_steps(row, -0.5) + ["Half the bid first: if it still does not convert after a month, exclude it:"] + _exclude_steps(row)
        else:
            steps = _exclude_steps(row)
    return {"steps": steps, "worth": _worth(row, kind, adj, cur)}


# ── Lists that cannot serve ─────────────────────────────────────────────────

def list_plan(l):
    """{steps} for what stops one audience list serving, or None when nothing does. `l` is a judged list (paid_audiences_insights.judge_lists)."""
    if not l.get("problems"):
        return None
    meta = l.get("platform") == "meta"
    steps = []
    joined = " ".join(l["problems"])
    if meta:
        if l.get("status") == "NOT_READY":
            steps.append("Open Ads Manager, then Audiences, click \"%s\" and read its Availability. A pixel or list audience takes time to fill; a lookalike cannot be made from a source of under 100 people." % l["label"])
        if "estimates" in joined:
            steps.append("Widen it: for a lookalike raise the audience size from 1% toward 3% or 5% (the range is 1 to 10%), for a custom audience lengthen the retention window or add another source such as a page's visitors.")
        return {"steps": steps}
    if "closed" in joined:
        steps.append("Open Google Ads, then Tools, then Audience manager, click \"%s\", and set it to Open so new people can join (a closed list stops growing)." % l["label"])
    if "Search" in joined or "Display" in joined:
        steps.append("In Audience manager click \"%s\" and lengthen the membership duration (most lists allow up to 540 days) so people stay in it longer." % l["label"])
        steps.append("Widen the rule: a list built from one narrow page is small, so base it on a broader set of pages or on all visitors, and check the list's tag status in Audience manager says it is receiving data.")
        steps.append("For Customer Match, upload more rows: Google needs about 1,000 matched members for Search and 100 for Display.")
        steps.append("Meanwhile do not count on it: use it on Display or YouTube only (it needs 100 there), or combine it with a larger list (Audience manager, then +, then Custom combination).")
    return {"steps": steps}


def unused_plan(names):
    return {"steps": ["Open Google Ads, then Campaigns, click the campaign the list fits, and choose Audiences, keywords, and content, then Audiences.",
                      "Click the +, choose Browse, then How they have interacted with your business, and pick %s. Set it to Observation so it only adds data and does not limit who sees the ads." % names,
                      "Look at it again after a month: the table above will judge it like any other audience."]}
