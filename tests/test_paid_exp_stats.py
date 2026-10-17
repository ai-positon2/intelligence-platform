"""Test & Experiment Tracker: the arithmetic that says whether a test has said anything."""
import math

import pytest

from tracker import paid_exp_stats as st


def a(impressions=0, clicks=0, conversions=0, cost=0.0, value=0.0):
    return {"impressions": impressions, "clicks": clicks, "conversions": conversions, "cost": cost, "value": value}


# ── the z-test ──────────────────────────────────────────────────────────────

def test_z_test_matches_a_worked_example():
    t = st.z_test(100, 1000, 130, 1000)                  # 10% against 13%
    assert t["p1"] == pytest.approx(0.10) and t["p2"] == pytest.approx(0.13)
    assert t["z"] == pytest.approx(2.103, abs=0.002)
    assert t["p"] == pytest.approx(0.0354, abs=0.001)


def test_z_test_is_symmetric_in_p_and_signed_in_z():
    up, down = st.z_test(100, 1000, 130, 1000), st.z_test(130, 1000, 100, 1000)
    assert up["p"] == pytest.approx(down["p"])
    assert up["z"] == pytest.approx(-down["z"])


def test_z_test_needs_something_on_both_sides():
    assert st.z_test(1, 0, 1, 10) is None
    assert st.z_test(1, 10, 1, 0) is None


def test_z_test_with_no_spread_is_not_significant():
    assert st.z_test(0, 500, 0, 500) == {"p1": 0.0, "p2": 0.0, "z": 0.0, "p": 1.0}
    assert st.z_test(50, 50, 40, 40)["p"] == 1.0


def test_z_test_takes_fractional_conversions():
    assert 0 < st.z_test(10.5, 100, 20.25, 100)["p"] < 1


def test_needed_per_arm_matches_the_textbook():
    # 10% against 12% at 95% / 80%: about 3,840 each
    assert 3700 < st.needed_per_arm(0.10, 0.12) < 3950
    assert st.needed_per_arm(0.10, 0.10) is None
    assert st.needed_per_arm(None, 0.1) is None
    assert st.needed_per_arm(0.0, 0.0) is None and st.needed_per_arm(1.0, 1.0) is None     # no gap, no spread: nothing to size
    assert st.needed_per_arm(0.0, 1.0) is None                 # a perfect split has no spread to size
    assert st.needed_per_arm(-0.1, 0.2) is None and st.needed_per_arm(0.1, 1.5) is None


def test_needed_per_arm_grows_as_the_gap_shrinks():
    assert st.needed_per_arm(0.10, 0.11) > st.needed_per_arm(0.10, 0.12) > st.needed_per_arm(0.10, 0.15)


def test_detectable_gap_shrinks_with_more_data():
    assert st.detectable_gap(0.05, 400) > st.detectable_gap(0.05, 4000) > st.detectable_gap(0.05, 40000)
    assert st.detectable_gap(0.05, 4000) == pytest.approx(2.8 * math.sqrt(2 * 0.05 * 0.95 / 4000) / 0.05, rel=0.01)
    assert st.detectable_gap(None, 100) is None and st.detectable_gap(0.0, 100) is None and st.detectable_gap(1.0, 100) is None
    assert st.detectable_gap(0.05, 0) is None


# ── picking the metric ──────────────────────────────────────────────────────

def test_conversion_rate_needs_clicks_in_each_arm_and_conversions_together():
    assert st.pick_metric(a(5000, 100, 15), a(5000, 100, 15), 10) == "convRate"
    assert st.pick_metric(a(5000, 99, 15), a(5000, 100, 15), 10) is None
    assert st.pick_metric(a(5000, 100, 14), a(5000, 100, 15), 10) is None


def test_click_rate_is_used_when_there_are_no_conversions_at_all():
    assert st.pick_metric(a(1000, 30, 0), a(1000, 40, 0), 10) == "ctr"
    assert st.pick_metric(a(999, 30, 0), a(1000, 40, 0), 10) is None


def test_click_rate_is_not_used_early_when_conversions_exist_but_are_few():
    assert st.pick_metric(a(5000, 90, 5), a(5000, 90, 6), 10) is None
    assert st.pick_metric(a(5000, 90, 5), a(5000, 90, 6), 27) is None
    assert st.pick_metric(a(5000, 90, 5), a(5000, 90, 6), 28) == "ctr"


# ── compare ─────────────────────────────────────────────────────────────────

def test_no_impressions_is_no_data_and_says_which_arm():
    r = st.compare(a(0), a(1000, 50), 14)
    assert r["verdict"] == "no_data" and "control has" in r["note"]
    assert "trial has" in st.compare(a(1000, 50), a(0), 14)["note"]
    assert "Neither" in st.compare(a(), a(), 14)["note"]


def test_a_test_under_seven_days_is_too_early_however_big_the_gap():
    r = st.compare(a(50000, 2000, 400), a(50000, 3000, 900), 6)
    assert r["verdict"] == "too_early"
    assert r["wait"]["days"] == 1 and r["wait"]["eta"] == 1
    assert st.compare(a(50000, 2000, 400), a(50000, 3000, 900), 7)["verdict"] == "winner"


def test_a_clear_conversion_rate_gap_is_a_winner():
    r = st.compare(a(20000, 1000, 50, 900), a(20000, 1000, 90, 900), 21)
    assert r["verdict"] == "winner" and r["metric"] == "convRate"
    assert r["control"] == pytest.approx(5.0) and r["trial"] == pytest.approx(9.0)
    assert r["relative"] == pytest.approx(80.0)
    assert r["p"] < 0.05 and r["confidence"] > 95


def test_the_same_gap_the_other_way_is_behind():
    assert st.compare(a(20000, 1000, 90), a(20000, 1000, 50), 21)["verdict"] == "behind"


def test_a_small_gap_with_a_lot_of_data_is_no_difference_and_says_how_small_a_gap_it_could_see():
    r = st.compare(a(20000, 1000, 50), a(20000, 1000, 52), 21)
    assert r["verdict"] == "no_difference"
    assert r["detectable"] and r["detectable"] > 0
    assert r["needed"] and r["needed"] > 1000


def test_too_early_says_what_is_missing_and_how_long_it_will_take():
    r = st.compare(a(8000, 40, 3), a(8000, 50, 4), 10)
    assert r["verdict"] == "too_early" and r["metric"] == "convRate"
    assert r["wait"]["more"] == {"clicks": 60, "conversions": 23}
    assert r["wait"]["eta"] == 33                      # 23 conversions at 0.7 a day is 33 days, 60 clicks at 4 a day is 15 days: the longer one
    assert r["control"] == pytest.approx(7.5) and r["trial"] == pytest.approx(8.0)


def test_eta_is_unknown_when_an_arm_has_stopped_getting_what_is_needed():
    r = st.compare(a(8000, 0, 0), a(8000, 50, 5), 10)
    assert r["verdict"] == "too_early" and r["wait"]["eta"] is None
    assert r["wait"]["more"]["clicks"] == 100


def test_waiting_on_click_rate_asks_for_impressions_not_clicks():
    r = st.compare(a(400, 10, 0), a(600, 12, 0), 10)
    assert r["metric"] == "ctr" and r["wait"]["more"] == {"impressions": 600}


def test_a_click_rate_verdict_says_so_when_conversions_were_too_few():
    r = st.compare(a(20000, 400, 5), a(20000, 600, 6), 30)
    assert r["verdict"] == "winner" and r["metric"] == "ctr"
    assert "click rate" in r["note"]


def test_equal_rates_have_no_relative_change_and_no_division_by_zero():
    r = st.compare(a(20000, 1000, 0), a(20000, 1000, 0), 21)
    assert r["verdict"] == "no_difference" and r["metric"] == "ctr"
    assert st.compare(a(20000, 1000, 0), a(20000, 1000, 0), 21)["relative"] == 0.0
    r2 = st.compare(a(20000, 0, 0), a(20000, 0, 0), 21)
    assert r2["verdict"] in ("no_difference", "too_early")


def test_compare_ignores_junk_numbers():
    r = st.compare({"impressions": "x", "clicks": None}, a(100), 10)
    assert r["verdict"] == "no_data"
    assert st.arm(-5, -1, -2)["clicks"] == 0


def test_summarise_rounds_and_leaves_rates_empty_without_a_denominator():
    s = st.summarise(st.arm(1000, 50, 5, 100.456, 300))
    assert s == {"impressions": 1000, "clicks": 50, "conversions": 5.0, "cost": 100.46, "value": 300.0, "ctr": 5.0, "convRate": 10.0, "cpa": 20.09}
    e = st.summarise(st.arm())
    assert e["ctr"] is None and e["convRate"] is None and e["cpa"] is None
    assert st.summarise(st.arm(10, 5, 0.5, 10))["cpa"] is None        # under one conversion has no cost per conversion


# ── ads in an ad group ──────────────────────────────────────────────────────

def ads(*rows):
    return [dict(id=str(i + 1), **r) for i, r in enumerate(rows)]


def test_one_live_ad_is_not_a_test():
    r = st.rank_group(ads(a(5000, 300, 20), a(0)), 30)
    assert r["verdict"] == "too_early" and r["ads"]["2"]["verdict"] == "no_data"
    assert st.rank_group(ads(a(0), a(0)), 30)["verdict"] == "no_data"


def test_the_leader_that_beats_every_other_ad_is_the_winner():
    r = st.rank_group(ads(a(20000, 1000, 100), a(20000, 1000, 50), a(20000, 1000, 55)), 30)
    assert r["verdict"] == "winner" and r["leader"] == "1" and r["metric"] == "convRate"
    assert r["ads"]["1"]["verdict"] == "winner"
    assert r["ads"]["2"]["verdict"] == "behind" and r["ads"]["3"]["verdict"] == "behind"
    assert r["ads"]["2"]["relative"] == pytest.approx(-50.0)
    assert r["bar"] == pytest.approx(0.025)


def test_each_comparison_is_held_to_a_stricter_bar_when_there_are_more_ads():
    # 10% against 13% over 1,000 clicks each is p = 0.035: enough for two ads, not for three
    two = st.rank_group(ads(a(20000, 1000, 130), a(20000, 1000, 100)), 30)
    three = st.rank_group(ads(a(20000, 1000, 130), a(20000, 1000, 100), a(20000, 1000, 129)), 30)
    assert two["verdict"] == "winner" and two["ads"]["2"]["verdict"] == "behind"
    assert three["ads"]["2"]["verdict"] == "no_difference"
    assert three["verdict"] == "no_difference"


def test_a_leader_that_beats_only_some_is_a_partial_result():
    r = st.rank_group(ads(a(20000, 1000, 150), a(20000, 1000, 148), a(20000, 1000, 60)), 30)
    assert r["verdict"] == "partial" and r["ads"]["1"]["verdict"] == "partial"
    assert r["ads"]["3"]["verdict"] == "behind" and r["ads"]["2"]["verdict"] == "no_difference"
    assert r["behind"] == 1


def test_an_ad_without_enough_clicks_is_too_early_and_is_not_held_against_the_leader():
    r = st.rank_group(ads(a(20000, 1000, 100), a(20000, 1000, 50), a(900, 40, 5)), 30)
    assert r["eligible"] == 2 and r["ads"]["3"]["verdict"] == "too_early"
    assert r["verdict"] == "winner" and r["bar"] == pytest.approx(0.05)


def test_a_group_under_seven_days_is_too_early():
    r = st.rank_group(ads(a(20000, 1000, 100), a(20000, 1000, 50)), 6)
    assert r["verdict"] == "too_early" and r["leader"] is None and r["ads"]["1"]["rate"] == pytest.approx(10.0)


def test_ads_are_compared_on_click_rate_when_none_of_them_converts():
    r = st.rank_group(ads(a(10000, 500, 0), a(10000, 300, 0)), 30)
    assert r["metric"] == "ctr" and r["verdict"] == "winner" and r["leader"] == "1"


def test_ads_with_few_conversions_wait_until_the_long_window_and_then_use_click_rate():
    rows = ads(a(10000, 90, 4), a(10000, 60, 3))
    assert st.rank_group(rows, 14)["verdict"] == "too_early"
    late = st.rank_group(rows, 28)
    assert late["metric"] == "ctr" and late["verdict"] == "winner" and "click rate" in late["note"]


def test_a_tied_leader_is_picked_the_same_way_every_time():
    r = st.rank_group(ads(a(10000, 500, 0), a(10000, 500, 0)), 30)
    assert r["leader"] == "2" and r["verdict"] == "no_difference"


def test_a_single_conversion_is_enough_to_wait_on_conversion_rate():
    one = st.compare(a(8000, 40, 1), a(8000, 50, 0), 10)
    assert one["verdict"] == "too_early" and one["metric"] == "convRate"
    none = st.compare(a(800, 40, 0), a(800, 50, 0), 10)
    assert none["metric"] == "ctr"


def test_click_rate_without_conversions_carries_no_note():
    r = st.compare(a(20000, 1000, 0), a(20000, 1000, 0), 21)
    assert r["metric"] == "ctr" and r["note"] == ""
    g = st.rank_group(ads(a(10000, 500, 0), a(10000, 300, 0)), 30)
    assert g["note"] == ""


def test_the_smallest_visible_gap_is_worked_from_the_smaller_arm():
    r = st.compare(a(20000, 1000, 50), a(80000, 4000, 205), 21)
    assert r["verdict"] == "no_difference"
    assert r["detectable"] == pytest.approx(round(100 * st.detectable_gap(0.0513, 1000), 1), abs=0.6)


def test_one_live_ad_says_why_there_is_nothing_to_compare():
    assert "Fewer than two" in st.rank_group(ads(a(5000, 300, 20), a(0)), 30)["note"]
    assert "Fewer than two" in st.rank_group(ads(a(0), a(0)), 30)["note"]


def test_conversion_rate_is_judged_only_on_ads_that_have_the_clicks():
    rows = ads(a(5000, 100, 2), a(5000, 100, 2), a(300, 30, 28))
    g = st.rank_group(rows, 30)
    assert g["metric"] == "ctr"                         # the two ads with 100 clicks have 4 conversions between them; the third's do not count
    assert g["eligible"] == 2


def test_a_tie_goes_to_the_ad_with_more_to_show():
    g = st.rank_group(ads(a(20000, 1000, 0), a(10000, 500, 0)), 30)
    assert g["leader"] == "1"


def test_the_bar_is_shown_to_four_places():
    g = st.rank_group(ads(*[a(20000, 1000, 100 - i) for i in range(4)]), 30)
    assert g["eligible"] == 4 and g["bar"] == pytest.approx(0.0167, abs=0.00001)
    assert g["bar"] == round(g["bar"], 4)


def test_a_group_that_is_too_early_is_still_shown_on_conversion_rate_when_there_are_conversions():
    g = st.rank_group(ads(a(10000, 90, 4), a(10000, 60, 3)), 14)
    assert g["verdict"] == "too_early" and g["metric"] == "convRate"
    assert g["ads"]["1"]["rate"] == pytest.approx(4.444, abs=0.001)


def test_no_em_or_en_dash_in_any_experiment_tracker_code_or_the_plan():
    import glob
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = glob.glob(os.path.join(root, "tracker", "paid_exp*.py")) + [os.path.join(root, "docs", "experiment-tracker-plan.md")]
    files += [p for p in (os.path.join(root, "static", "js", "exp_data.js"), os.path.join(root, "static", "js", "experiment_tracker.js"),
                          os.path.join(root, "static", "css", "experiment_tracker.css"), os.path.join(root, "templates", "experiment_tracker.html")) if os.path.exists(p)]
    assert files
    for p in files:
        text = open(p, encoding="utf-8").read()
        assert "—" not in text and "–" not in text, p
