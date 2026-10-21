"""Test & Experiment Tracker: how to act on a verdict and what acting on it is worth (tracker/paid_exp_plans.py).

The report says what to do; the plan says how, in the Google Ads or Meta screens, and puts a figure on it. The figures are the risk: a winner's worth is the
lead times the traffic the control had, with the low end of the 95 percent range beside it so the luckiest reading is never the only one, and a loser's is what
keeping it costs. They are pinned here to numbers worked out by hand, along with which steps each verdict, platform and state gets.
"""
import datetime as dt
import math

import pytest

from tests.test_paid_exp import (EARLY, FLAT, LOSE, NOW, TODAY, WIN, ad, build, group, made, read_with, tot)
from tracker import paid_exp as ex
from tracker import paid_exp_plans as pl

EM, EN = chr(0x2014), chr(0x2013)


def plan(**kw):
    return build(**kw)["action"].get("plan")


def text(p):
    return " ".join(p["steps"])


def meta(**kw):
    e = made(**kw)
    e["platform"] = "meta"
    e["arms"][0]["name"], e["arms"][1]["name"] = "Version A", "Version B"
    return ex.build_experiment(e, TODAY, "USD")["action"].get("plan")


# ── The figures ───────────────────────────────────────────────────────────

def test_the_gap_and_its_95_percent_range_are_the_unpooled_two_proportion_interval():
    c, t = {"clicks": 1000, "conversions": 50, "impressions": 20000}, {"clicks": 1000, "conversions": 90, "impressions": 20000}
    gap, low, high = pl.gap_range(c, t, "convRate")
    se = math.sqrt(0.05 * 0.95 / 1000 + 0.09 * 0.91 / 1000)
    assert gap == pytest.approx(0.04) and low == pytest.approx(0.04 - 1.959964 * se) and high == pytest.approx(0.04 + 1.959964 * se)
    g2 = pl.gap_range(c, t, "ctr")
    assert g2[0] == pytest.approx(0.05 - 0.05)                                                       # clicks per impression: the same 5% on both sides
    assert pl.gap_range({"clicks": 0, "conversions": 0, "impressions": 0}, t, "convRate") is None


def test_a_winners_worth_is_the_lead_times_the_whole_campaigns_traffic_with_the_low_end_beside_it():
    p = plan(**WIN)
    # 1000 clicks over the 28 days read for the control is 1071 a month; a 50/50 split means the whole campaign gets twice that: 2143 clicks a month
    assert "its lead of 4 points on conversion rate is worth about 86 more conversions a month" in p["worth"]
    assert "the whole campaign gets about 2,143 clicks a month" in p["worth"]
    assert "at least about 38 at the low end of the 95% range" in p["worth"] and "usually shrinks a little" in p["worth"]


def test_the_traffic_split_sets_how_much_of_the_campaign_the_control_was():
    lopsided = plan(split=(80, 20), **WIN)               # the control had 80% of the traffic: 1000 clicks is 1071 a month, so the whole campaign gets 1339 a month
    assert "the whole campaign gets about 1,339 clicks a month" in lopsided["worth"] and "worth about 54 more conversions" in lopsided["worth"]


def test_a_gap_whose_low_end_is_nothing_is_said_to_be_small_not_certain():
    c, t = {"clicks": 1000, "conversions": 50, "impressions": 20000}, {"clicks": 1000, "conversions": 90, "impressions": 20000}
    cmp_ = {"metric": "convRate", "metricLabel": "conversion rate"}
    thin = pl._winner_worth(c, t, cmp_, 3000, False, 0.5)                 # a tiny trickle of traffic: 20 clicks a month for the whole campaign
    assert "close to nothing" in thin and "at least about" not in thin and "worth about 1 more conversion a month" in thin
    assert "at least about" in pl._winner_worth(c, t, cmp_, 28, False, 0.5)


def test_a_losers_worth_is_what_keeping_it_costs():
    p = plan(**LOSE)
    # 1000 trial clicks in 28 days is 1071 a month; they convert 4 points under the control: about 43 fewer conversions. Cost 100 over 28 days is $107 a month.
    assert p["worth"].startswith("At this gap the trial gets about 43 fewer conversions a month than the original would on the same traffic; it spends about $107 a month. Ending it stops both.")
    assert "shrinks" not in p["worth"]


# ── The steps, by verdict ─────────────────────────────────────────────────

def test_applying_a_google_winner_says_where_and_which_option():
    p = plan(**WIN)
    t = text(p)
    assert p["steps"][0] == 'Open Google Ads, then Campaigns, then Experiments in the left menu, and open "Headline test".'
    assert "Click Apply" in t and "Update your original campaign" in t and "Convert the trial into a new campaign" in t and "judge it on the week" in t


def test_ending_a_google_loser_changes_nothing_on_the_original():
    t = text(plan(**LOSE))
    assert "Click End experiment" in t and "carries on exactly as it was" in t and "Apply" not in t


def test_a_meta_test_is_ended_in_experiments_and_the_budget_moves_in_small_steps():
    win, lose = text(meta(**WIN)), text(meta(**LOSE))
    assert "Experiments (under Business tools)" in win and "Turn off the ad set of Version A and put its budget on the ad set of Version B" in win
    assert "Turn off the ad set of Version B and put its budget on the ad set of Version A" in lose
    assert "no more than about 20% at a time" in win and "Google Ads" not in win
    assert meta(**WIN)["worth"].startswith("If all of the budget ran as the trial does")


def test_no_difference_that_is_called_says_keep_the_simpler_one_and_how_long_a_real_answer_would_take():
    p = plan(start="2026-08-01", **FLAT)
    assert "Click End experiment" in text(p) and "simpler to run" in text(p) and "apply it first" in text(p)
    assert p["worth"] is None and p["note"].startswith("To tell a gap as small as this one from chance each arm would need about 189,936 clicks")
    assert "more than six months" in p["note"]


def test_a_difference_that_could_be_seen_soon_gets_a_date_not_a_shrug():
    close = dict(control=tot(20000, 1000, 50), trial=tot(20000, 1000, 51))
    p = plan(**close)
    assert "more than six months" in p["note"]
    young = plan(start="2026-10-08", window=("2026-10-08", "2026-10-17"), control=tot(20000, 1000, 50), trial=tot(20000, 1000, 52), end="2026-12-30")
    assert young["steps"][0].startswith("Leave both arms alone") and young["steps"][1].startswith("Look again on Oct 25") and "more than six months" in young["note"]


def test_a_test_that_is_too_early_is_left_alone_and_given_the_day_to_look_again():
    p = plan(start="2026-10-08", window=("2026-10-08", "2026-10-17"), control=tot(8000, 40, 3), trial=tot(8000, 50, 4), end="2026-12-30")
    assert p["steps"][0].startswith("Leave both arms alone") and p["steps"][1].startswith("Look again on ") and p["worth"] is None
    assert "an edit mid-test starts the count again" in p["steps"][0]


def test_a_test_that_will_never_get_there_is_checked_arm_by_arm():
    arms = dict(control=tot(8000, 40, 3), trial=tot(8000, 0, 0))                 # one arm has had no clicks at all, so it will not get there on its own
    p = plan(start="2026-10-08", window=("2026-10-08", "2026-10-17"), end="2026-12-30", **arms)
    assert build(start="2026-10-08", window=("2026-10-08", "2026-10-17"), end="2026-12-30", **arms)["action"]["kind"] == "stuck"
    assert "Experiment type" in p["steps"][0] and "traffic split" in p["steps"][1] and "starts the count again" in p["steps"][2]
    m = meta(start="2026-10-08", window=("2026-10-08", "2026-10-17"), end="2026-12-30", **arms)
    assert "ad set of each cell" in m["steps"][0] and "Google Ads" not in text(m)


def test_a_test_past_its_end_date_offers_more_time_or_the_end():
    p = plan(start="2026-10-08", window=("2026-10-08", "2026-10-17"), control=tot(8000, 40, 3), trial=tot(8000, 50, 4), end="2026-10-10")
    assert "edit the end date" in text(p) and "End experiment" in text(p)


def test_a_kind_of_experiment_the_tracker_cannot_measure_is_sent_to_its_page():
    p = plan(measured=False, control=None, trial=None)
    assert p and "Read the result on its page" in text(p) and p["worth"] is None


def test_nothing_is_planned_for_a_test_that_is_over_or_not_started():
    assert plan(state="ended", **WIN) is None and plan(state="applied", **WIN) is None and plan(state="setting up", **WIN) is None


def test_the_plan_travels_with_the_action_and_into_the_needs_list():
    rep = ex.assemble(read_with([made(**WIN)], []), now=NOW, today=TODAY)
    e = rep["experiments"][0]
    assert e["action"]["plan"]["steps"] and rep["needs"][0]["plan"] == e["action"]["plan"]
    quiet = ex.assemble(read_with([made(start="2026-10-08", window=("2026-10-08", "2026-10-17"), **FLAT)], []), now=NOW, today=TODAY)
    assert quiet["experiments"][0]["action"]["kind"] == "wait" and "plan" in quiet["experiments"][0]["action"] and quiet["needs"] == []         # a plan is kept for a test that only waits, but it is not on the to-do list


def test_the_money_is_in_the_accounts_currency():
    assert pl.money(1234.5, "USD") == "$1,234" or pl.money(1234.5, "USD") == "$1,235"
    assert pl.money(12.5, "EUR") == "€12.50" and pl.money(12.5, "GBP") == "£12.50" and pl.money(12.5, "CHF") == "CHF 12.50" and pl.money(12.5, "") == "$12.50"
    e = made(**LOSE)
    e["arms"][1]["results"]["total"]["cost"] = 300.0
    assert "€321 a month" in ex.build_experiment(e, TODAY, "EUR")["action"]["plan"]["worth"]


# ── Ad tests ──────────────────────────────────────────────────────────────

def test_a_leader_is_kept_and_the_ads_behind_it_are_paused_by_name():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50), ad(3, 20000, 1000, 55)))
    p = t["action"]["plan"]
    assert p["steps"][0] == 'Open Google Ads, then Ads (under Ads & assets), and filter to the ad group "Implants" in "Search Implants".'
    assert 'Select "Headline 3 | Second | Third", "Headline 2 | Second | Third", then Edit, then Pause.' in p["steps"][1] or "then Edit, then Pause" in p["steps"][1]
    assert "Write one challenger" in p["steps"][2] and "change one thing" in p["steps"][2]


def test_an_ad_tests_worth_is_the_traffic_of_the_ads_behind_moved_to_the_leaders_rate():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50), ad(3, 20000, 1000, 55)))
    # the leader converts 10%; the two behind got 1000 clicks each at 5% and 5.5%: 1000*(0.10-0.05) + 1000*(0.10-0.055) = 95
    assert t["action"]["plan"]["worth"].startswith("Moving the traffic of the ads behind onto the leader would add about 95 conversions over 30 days")
    assert "keeps its rate with more traffic" in t["action"]["plan"]["worth"]


def test_a_meta_ad_set_says_the_result_is_a_lead_and_asks_for_a_fair_test():
    g = dict(group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50)), platform="meta")
    p = ex.build_ad_test(g)["action"]["plan"]
    assert p["steps"][0].startswith("Open Ads Manager, then the ad set") and "Switch off" in p["steps"][1] and "A/B test" in p["steps"][2]
    assert "a lead, not a fair test" in p["worth"] and "Google" not in text(p)


def test_too_many_ads_with_no_difference_keeps_the_three_best_by_rate():
    t = ex.build_ad_test(group(*[ad(i, 20000, 1000, 50 + i % 2 + (3 if i == 5 else 0)) for i in range(1, 6)]))
    p = t["action"]["plan"]
    assert "Keep the three with the best conversion rate" in p["steps"][1] and "pause the rest" in p["steps"][1] and p["worth"] is None


def test_few_ads_with_no_difference_replace_the_weakest_with_a_challenger():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 50), ad(2, 20000, 1000, 52)))
    p = t["action"]["plan"]
    assert "Replace the weakest" in p["steps"][1] and "Headline 1" in p["steps"][1] and "Write one challenger" in p["steps"][2]


def test_ads_that_are_too_early_or_alone_have_no_plan():
    assert "plan" not in ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 900, 40, 2)))["action"]
    assert "plan" not in ex.build_ad_test(group(ad(1, 0, 0, 0), ad(2, 0, 0, 0)))["action"]


# ── Wording ───────────────────────────────────────────────────────────────

def test_no_plan_uses_an_em_or_en_dash():
    cases = [plan(**WIN), plan(**LOSE), plan(start="2026-08-01", **FLAT), plan(**EARLY), meta(**WIN), meta(**LOSE),
             ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50)))["action"]["plan"],
             ex.build_ad_test(group(ad(1, 20000, 1000, 50), ad(2, 20000, 1000, 52)))["action"]["plan"]]
    for p in cases:
        blob = " ".join(p["steps"]) + " " + (p.get("worth") or "") + " " + (p.get("note") or "")
        assert EM not in blob and EN not in blob and "None" not in blob and "%s" not in blob and "{" not in blob, blob


# ── The edges of the figures ──────────────────────────────────────────────

def arm_(impressions=0, clicks=0, conversions=0, cost=0.0):
    return {"impressions": impressions, "clicks": clicks, "conversions": conversions, "cost": cost, "value": 0.0}


def test_how_long_a_real_answer_would_take_counts_only_what_is_still_missing():
    cmp_ = {"metric": "convRate", "needed": 1000}
    control, trial = arm_(clicks=400), arm_(clicks=500)                  # the slower arm has 400 clicks in 10 days: 40 a day, 600 still to find
    assert pl._more_days(cmp_, control, trial, 10) == "To tell a gap as small as this one from chance each arm would need about 1,000 clicks, which at the current pace is about 15 days more."
    far = pl._more_days(dict(cmp_, needed=3000), arm_(clicks=100), arm_(clicks=100), 10)              # 2900 still to find at 10 a day: 290 days
    assert "more than six months" in far
    assert "about 1 day more" in pl._more_days(dict(cmp_, needed=101), arm_(clicks=100), arm_(clicks=100), 10)
    assert pl._more_days(dict(cmp_, needed=None), control, trial, 10) is None and pl._more_days(cmp_, arm_(), arm_(), 10) is None
    assert pl._more_days({"metric": "ctr", "needed": 5000}, arm_(impressions=1000), arm_(impressions=1000), 10).count("impressions") == 1


def test_a_losers_costs_come_from_the_trials_own_traffic_and_say_nothing_when_there_is_nothing_to_stop():
    cmp_ = {"metric": "convRate", "metricLabel": "conversion rate"}
    control, trial = arm_(clicks=500, conversions=50, cost=10.0), arm_(clicks=1000, conversions=50, cost=300.0)       # 10% against 5%, and the trial has twice the clicks
    worth = pl._behind_worth(control, trial, cmp_, 30, "USD", False)
    assert worth.startswith("At this gap the trial gets about 50 fewer conversions a month than the original would on the same traffic; it spends about $300 a month.")
    quiet = pl._behind_worth(arm_(clicks=10, conversions=1), arm_(clicks=10, conversions=0, cost=500.0), cmp_, 3000, "USD", False)
    assert "fewer" not in quiet and "spends about $5.00 a month" in quiet
    assert pl._behind_worth(arm_(clicks=10, conversions=1), arm_(clicks=10, conversions=0), cmp_, 3000, "USD", False) is None


def test_a_winners_worth_needs_a_real_lead_and_some_traffic():
    cmp_ = {"metric": "convRate", "metricLabel": "conversion rate"}
    assert pl._winner_worth(arm_(clicks=1000, conversions=90), arm_(clicks=1000, conversions=50), cmp_, 28, False, 0.5) is None       # not a lead at all
    assert pl._winner_worth(arm_(clicks=0), arm_(clicks=0), cmp_, 28, False, 0.5) is None
    assert pl._winner_worth(arm_(clicks=1000, conversions=50), arm_(clicks=1000, conversions=90), cmp_, 0, False, 0.5) is None       # no days read, so no pace


def test_the_day_to_look_again_is_the_one_the_test_will_be_ready():
    kw = dict(start="2026-10-08", window=("2026-10-08", "2026-10-17"), control=tot(8000, 40, 3), trial=tot(8000, 50, 4), end="2026-12-30")
    r = build(**kw)
    eta = r["comparison"]["wait"]["eta"]
    assert eta and r["action"]["kind"] == "wait"
    assert r["action"]["plan"]["steps"][1] == "Look again on %s, when it should have enough to call." % pl._day(TODAY + dt.timedelta(days=eta))


def test_an_ad_group_judged_on_click_rate_is_worth_clicks_not_conversions():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 0), ad(2, 20000, 500, 0)))
    assert t["metric"] == "ctr" and t["verdict"] == "winner"
    # 20000 impressions at 2.5% against the leader's 5%: 500 clicks
    assert t["action"]["plan"]["worth"].startswith("Moving the traffic of the ads behind onto the leader would add about 500 clicks over 30 days (their impressions in the last 30 days, at the leader's click rate)")


def test_a_partial_result_pauses_only_the_ad_that_is_behind():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 150), ad(2, 20000, 1000, 148), ad(3, 20000, 1000, 60)))
    p = t["action"]["plan"]
    assert t["verdict"] == "partial" and "Headline 3" in p["steps"][1] and "Headline 2" not in p["steps"][1] and p["steps"][1].startswith("Select")


def test_an_ad_worth_ignores_ads_that_are_not_actually_behind_and_gains_too_small_to_name():
    leader = {"numbers": {"convRate": 10.0, "ctr": 5.0, "clicks": 1000, "impressions": 20000}}
    above = {"numbers": {"convRate": 12.0, "ctr": 5.0, "clicks": 1000, "impressions": 20000}}
    tiny = {"numbers": {"convRate": 9.99, "ctr": 5.0, "clicks": 100, "impressions": 20000}}
    assert pl._ad_worth(leader, [above], "convRate", False) is None                                  # an ad above the leader adds nothing, and does not subtract
    assert pl._ad_worth(leader, [above, {"numbers": {"convRate": 5.0, "ctr": 2.0, "clicks": 100, "impressions": 4000}}], "convRate", False).count("5 conversions") == 1
    assert pl._ad_worth(leader, [tiny], "convRate", False) is None                                   # a tenth of a conversion is not a gain worth stating
    assert pl._ad_worth(leader, [tiny], None, False) is None
