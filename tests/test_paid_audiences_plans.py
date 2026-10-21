"""Audience Segment Analysis: where to click and what each change is worth (tracker/paid_audiences_plans.py).

What is pinned: that a bid change is only told where the campaign honours one (and the audience's own campaigns are named), that Smart Bidding, Demand Gen and Performance Max get a
campaign of their own instead, that an observed audience is lowered before it is excluded and a targeted one is removed and excluded, what Meta gets in place of all this, the
estimates against hand figures, and the steps for the reason a list cannot serve.
"""
import pytest

from tests.test_paid_audiences import (ACCT, AG, INTERESTS, LISTS, SETTINGS, TOTALS, a, ag_row, cache, list_row, report, row, searcher)  # noqa: F401  (cache: the fixture the report tests use)
from tracker import paid_audiences_insights as ins
from tracker import paid_audiences_plans as pl

EM, EN = chr(0x2014), chr(0x2013)


def text(p):
    return " ".join(p["steps"])


def uses(*rows):
    return [dict(campaign=c, adGroup=g, ctype=t, bidding=b, platform=p, m={"cost": cost}) for c, g, t, b, p, cost in rows]


def judged(label="Aud", kind="raise", mode="observation", bid=None, cost=400.0, conv=10.0, us=None, platform="google", bid_varies=False):
    return {"label": label, "kindLabel": "Remarketing", "mode": mode, "bid": bid, "bidVaries": bid_varies, "cost": cost, "conversions": conv, "platform": platform,
            "uses": us or uses(("P2_NonBrand", "Group", "Non Brand", "MANUAL_CPC", "google", cost))}


# ── Where a bid change works ──────────────────────────────────────────────

def test_a_bid_change_names_the_campaign_and_says_where_the_audience_is_now():
    p = pl.action_plan(judged(bid=0.1), {"kind": "raise"}, 0.25, "USD")
    assert p["steps"][0] == 'Open Google Ads, then Campaigns, click "P2_NonBrand", and choose Audiences, keywords, and content in the left menu, then Audiences.'
    assert p["steps"][1] == 'Find "Aud" (remarketing) in the table, click its Bid adjustment cell (the pencil), and set it to +25% (it is at +10% now). Save.'
    assert "Observation" in p["steps"][2] and "does not narrow who sees the ads" in p["steps"][2]
    assert "none now" in pl.action_plan(judged(bid=None), {"kind": "raise"}, 0.25)["steps"][1] and "differs by campaign now" in pl.action_plan(judged(bid_varies=True), {"kind": "raise"}, 0.25)["steps"][1]
    assert len(pl.action_plan(judged(mode="targeting"), {"kind": "raise"}, 0.25)["steps"]) == 2          # a targeted audience has no observation note


def test_the_biggest_spending_campaigns_that_honour_a_bid_are_named_and_the_rest_counted():
    us = uses(("Small", "G", "Non Brand", "MANUAL_CPC", "google", 10), ("Big", "G", "Non Brand", "TARGET_SPEND", "google", 500), ("Smart", "G", "Non Brand", "TARGET_CPA", "google", 900),
              ("B", "G", "Non Brand", "MANUAL_CPC", "google", 300), ("C", "G", "Non Brand", "MANUAL_CPC", "google", 200), ("D", "G", "Non Brand", "MANUAL_CPC", "google", 100))
    s = pl.action_plan(judged(us=us), {"kind": "raise"}, 0.2)["steps"][0]
    assert '"Big", "B", "C" and 2 more' in s and "Smart" not in s                                       # Smart Bidding's campaign is not one a bid change reaches


def test_smart_bidding_and_demand_gen_get_a_campaign_of_their_own_and_never_a_bid():
    smart = judged(us=uses(("Search_A", "G", "Non Brand", "TARGET_CPA", "google", 400)))
    p = pl.action_plan(smart, {"kind": "raise"}, 0.3)
    assert "Smart Bidding sets the bids in \"Search_A\", so an audience bid adjustment is ignored there" in p["steps"][0] and "Edit, Copy" in p["steps"][1] and "Targeting" in p["steps"][1]
    assert "own daily budget" in p["steps"][2] and "Bid adjustment cell" not in text(p)
    dg = judged(us=uses(("Gen", "G", "Demand Gen", "MAXIMIZE_CONVERSIONS", "google", 400)), mode="targeting")
    assert "Demand Gen and Performance Max set their own bids and treat an audience as a pointer, not a limit" in pl.action_plan(dg, {"kind": "raise"}, 0.3)["steps"][0]
    cut = pl.action_plan(smart, {"kind": "lower"}, -0.3)
    assert "exclude \"Aud\"" in cut["steps"][1] and "small daily budget" in cut["steps"][1] and "Bid adjustment cell" not in text(cut)


def test_a_weak_observed_audience_is_lowered_then_excluded_and_a_targeted_one_is_removed_then_excluded():
    obs = pl.action_plan(judged(bid=0.1), {"kind": "lower"}, -0.3)
    assert "set it to -30%" in obs["steps"][1] and obs["steps"][2] == "If it is still weak after a month, exclude it:" and "Exclusions tab" in obs["steps"][3]
    tgt = pl.action_plan(judged(mode="targeting"), {"kind": "lower"}, -0.3)
    assert tgt["steps"][2] == "If it is still weak after a month, remove it from the targeting:" and "choose Edit, then Remove" in tgt["steps"][3] and "Exclusions tab" in tgt["steps"][4]


def test_a_leak_is_halved_when_it_can_be_and_excluded_when_it_cannot():
    p = pl.action_plan(judged(bid=0.1), {"kind": "exclude"}, -0.5)
    assert "set it to -50%" in p["steps"][1] and "Half the bid first" in p["steps"][2] and "Exclusions tab" in p["steps"][3]
    smart = pl.action_plan(judged(us=uses(("S", "G", "Non Brand", "TARGET_CPA", "google", 400))), {"kind": "exclude"}, None)
    assert len(smart["steps"]) == 1 and smart["steps"][0].startswith("Open Audiences, keywords, and content in \"S\", then the Exclusions tab, click the +, and add \"Aud\"")


def test_hold_and_none_and_an_audience_with_no_use_have_no_plan():
    assert pl.action_plan(judged(), {"kind": "hold"}, None) is None and pl.action_plan(judged(), {"kind": "none"}, None) is None
    assert pl.action_plan(dict(judged(), uses=[]), {"kind": "raise"}, 0.2) is None


# ── Meta ──────────────────────────────────────────────────────────────────

def test_meta_gets_ad_set_steps_and_budget_moves_of_a_fifth_and_no_google_menus():
    us = uses(("Camp", "Ad set one", "Leads", "", "meta", 400))
    row_ = judged(us=us, platform="meta")
    raise_ = pl.action_plan(row_, {"kind": "raise"}, 0.3)
    assert raise_["steps"][0] == 'Open Ads Manager and find the ad sets "Ad set one".' and "Duplicate" in raise_["steps"][1] and "20% at a time" in raise_["steps"][2]
    lower = pl.action_plan(row_, {"kind": "lower"}, -0.3)
    assert "20% at a time" in lower["steps"][1] and "remove it from the audiences it includes" in lower["steps"][1]
    leak = pl.action_plan(row_, {"kind": "exclude"}, None)
    assert "add it under Exclude" in leak["steps"][1]
    for p in (raise_, lower, leak):
        assert "Google Ads" not in text(p) and "Bid adjustment" not in text(p)


# ── What it is worth ──────────────────────────────────────────────────────

def test_a_raise_is_worth_the_extra_spend_at_the_audiences_own_cost_per_conversion():
    # $400 and 10 conversions is $40 a conversion; +25% is $100 more spend, which at $40 is 2.5 conversions, rounded to 2
    w = pl.action_plan(judged(cost=400.0, conv=10.0), {"kind": "raise"}, 0.25)["worth"]
    assert w == "A +25% change on the $400 it spent would move about $100 more through it; at its own cost per conversion ($40.00) that is about 2 more conversions, though extra spend usually converts a little worse: an estimate, since spend does not rise exactly with the bid."
    assert "conversions" not in pl.action_plan(judged(cost=400.0, conv=0.0), {"kind": "raise"}, 0.25)["worth"].split(":")[0]


def test_a_lower_is_worth_the_spend_it_saves_and_the_conversions_it_would_cost():
    w = pl.action_plan(judged(cost=400.0, conv=10.0), {"kind": "lower"}, -0.3)["worth"]
    assert w.startswith("Lowering it -30% on the $400 it spent would save up to about $120, and cost up to about 3 conversions: an estimate") and "fewest" in w
    assert "conversions" not in pl.action_plan(judged(cost=400.0, conv=1.0), {"kind": "lower"}, -0.1)["worth"].split(":")[0]                  # a tenth of a conversion is not named


def test_a_leak_is_worth_what_it_spent_with_nothing_to_show():
    w = pl.action_plan(judged(cost=250.0, conv=0.0), {"kind": "exclude"}, None)["worth"]
    assert w.startswith("It spent $250 without a conversion in this range.") and "an estimate" in w


# ── Lists ─────────────────────────────────────────────────────────────────

def test_a_list_that_is_too_small_gets_steps_to_grow_it_and_a_closed_one_is_reopened():
    tiny = ins.judge_lists([dict(list_row("20", "Tiny", status="CLOSED", search=0, display=40), used=1, label="Tiny", platform="google", sizeSearch=0, sizeDisplay=40, status="CLOSED")])[0]
    t = text(tiny["plan"])
    assert "Audience manager" in t and "set it to Open" in t and "membership duration" in t and "1,000 matched members" in t and "Custom combination" in t
    big = ins.judge_lists([dict(label="Big", used=1, platform="google", sizeSearch=5000, sizeDisplay=5000, status="OPEN")])[0]
    assert "plan" not in big and big["serves"]


def test_a_meta_audience_that_is_not_ready_or_too_small_is_told_what_to_widen():
    l = ins.judge_lists([dict(label="Lookalike", used=1, platform="meta", status="NOT_READY", why="Too small", sizeSearch=300)])[0]
    t = text(l["plan"])
    assert "Availability" in t and "under 100 people" in t and "1% toward 3% or 5%" in t and "Google" not in t


def test_the_lists_and_unused_insights_carry_the_steps(cache):
    rep = report(searcher(ag=AG + [ag_row("USER_LIST", "20", cost=3, conv=0, clk=1)], settings=SETTINGS, lists=LISTS, interests=INTERESTS, totals=TOTALS))
    by = {i["kind"]: i for i in rep["views"]["All"]["insights"]}
    assert "membership duration" in text(by["lists"]["plan"]) and "Set it to Observation" in text(by["unused"]["plan"]) and '"Unused list"' in text(by["unused"]["plan"])


# ── Through the report ────────────────────────────────────────────────────

def test_every_audience_the_report_tells_to_act_on_carries_its_plan(cache):
    rep = report()
    crm = row(rep, "CRM list", "Non Brand")
    assert crm["action"]["kind"] == "raise" and "+40%" in crm["action"]["plan"]["steps"][1] and "customer match" in crm["action"]["plan"]["steps"][1]
    assert "worth" in crm["action"]["plan"] and crm["action"]["plan"]["worth"].startswith("A +40% change on the $")
    weddings = row(rep, "Weddings", "Non Brand")
    assert weddings["action"]["kind"] == "exclude" and "Exclusions tab" in text(weddings["action"]["plan"])
    gen = row(rep, "Local Landscapers", "Demand Gen")
    assert "Demand Gen and Performance Max" in gen["action"]["plan"]["steps"][0]
    assert "plan" not in row(rep, "Home Decor", "Non Brand")["action"]                                      # too few conversions: nothing to do yet


def test_no_plan_uses_an_em_or_en_dash_or_leaves_a_placeholder():
    cases = []
    for kind, adj in (("raise", 0.3), ("lower", -0.3), ("exclude", None)):
        for r in (judged(bid=0.1), judged(mode="targeting"), judged(us=uses(("S", "G", "Non Brand", "TARGET_CPA", "google", 400))), judged(us=uses(("D", "G", "Demand Gen", "X", "google", 400))),
                  judged(us=uses(("M", "Ad set", "Leads", "", "meta", 400)), platform="meta")):
            p = pl.action_plan(r, {"kind": kind}, adj)
            cases.append(text(p) + " " + (p["worth"] or ""))
    for b in cases:
        assert EM not in b and EN not in b and "None" not in b and "{" not in b and "%s" not in b, b[:160]
