"""Brand Spend Alert: the arithmetic, the statuses, the cause, this week so far, the findings and the fix plan (tracker/paid_brandspend_math.py, paid_brandspend_plan.py). No network."""
import datetime as dt
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_brandspend_math as m  # noqa: E402
from tracker import paid_brandspend_plan as plan  # noqa: E402

TUE = dt.date(2026, 10, 20)               # the last whole week is Mon Oct 12 to Sun Oct 18; the oldest is Mon Aug 24
THU = dt.date(2026, 10, 22)               # three days into the week: Mon 19, Tue 20, Wed 21 have happened
USUAL = [203, 196, 210, 189, 203, 210, 196]       # seven usual weeks, each a multiple of 7 so a day is a whole number; the middle one is 203


def days(cid, name, weekly, clicks, *, today=TUE, status="ENABLED", strategy="MAXIMIZE_CLICKS", budget=40.0, this_week=None):
    """Rows for one campaign: eight weeks (oldest first, None is a week with nothing), the cost and the clicks spread evenly over the seven days of each;
    this_week is (cost per day, clicks per day) for the days of the current week that have happened."""
    out = []
    first = m.weeks(today)[0][0]
    for i, cost in enumerate(weekly):
        if cost is None:
            continue
        for d in range(7):
            day = first + dt.timedelta(days=7 * i + d)
            out.append({"id": cid, "name": name, "date": day.isoformat(), "status": status, "strategy": strategy, "budget": budget,
                        "impressions": 1000, "clicks": clicks[i] / 7.0, "cost": cost / 7.0})
    if this_week:
        monday = today - dt.timedelta(days=today.weekday())
        for d in range(today.weekday()):
            out.append({"id": cid, "name": name, "date": (monday + dt.timedelta(days=d)).isoformat(), "status": status, "strategy": strategy, "budget": budget,
                        "impressions": 1000, "clicks": this_week[1], "cost": this_week[0]})
    return out


def account(last, last_clicks=70, usual=USUAL, usual_clicks=70, **kw):
    return days("11", "P2_Brand", list(usual) + [last], [usual_clicks] * len(usual) + [last_clicks], **kw)


def go(rows, **kw):
    return m.analyse(rows, kw.pop("today", TUE), **kw)


# ── Weeks, totals and the usual week ────────────────────────────────────────

def test_the_read_runs_from_the_oldest_whole_week_to_yesterday():
    assert m.window(TUE) == (dt.date(2026, 8, 24), dt.date(2026, 10, 19))
    assert m.window(THU) == (dt.date(2026, 8, 24), dt.date(2026, 10, 21))


def test_a_week_is_put_together_from_its_days_and_a_dayless_week_has_no_cost_per_click():
    t = m.total(days("11", "B", [203], [70])[:7])
    assert t["cost"] == 203.0 and t["clicks"] == 70 and t["days"] == 7 and t["cpc"] == pytest.approx(2.9)
    z = m.total([])
    assert z["cost"] == 0 and z["cpc"] is None and z["days"] == 0


def test_the_usual_week_is_the_middle_of_the_weeks_with_spend_and_needs_four_of_them():
    wk = m.weeks(TUE)
    tr = m._trend(account(301), wk)
    b = m.baseline(tr)
    assert b["weeks"] == 7 and b["cost"] == 203.0 and b["clicks"] == 70 and b["cpc"] == pytest.approx(2.9)
    # a week with no spend in the middle is not a usual week of nothing: it is left out
    tr2 = m._trend(days("11", "B", [203, None, 196, 210, 189, 203, 210, 196], [70] * 8) + [], wk)
    assert m.baseline(tr2) is None or m.baseline(tr2)["weeks"] == 6
    tr3 = m._trend(days("11", "B", [None, None, None, None, 203, 196, 210, 189], [70] * 8), wk)
    assert m.baseline(tr3) is None                      # only three weeks before the last one had spend


def test_the_middle_of_an_even_number_of_weeks_is_the_mean_of_the_two_in_the_middle():
    assert m.median([1, 2, 3, 4]) == 2.5 and m.median([5]) == 5 and m.median([]) is None


# ── The band ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cost, state", [(263.9, "ok"), (264.0, "over"), (142.1, "ok"), (142.0, "under")])
def test_the_band_is_30_percent_either_way_and_the_edge_is_inside(cost, state):
    assert m.judge(cost, 203.0, 0.30)[0] == state


def test_a_swing_under_25_is_noise_even_when_it_is_a_big_share():
    assert m.judge(60, 40, 0.30)[0] == "ok"             # 50% above, but 20 in money
    assert m.judge(70, 40, 0.30)[0] == "over"           # 30 in money
    assert m.judge(64.9, 40, 0.30)[0] == "ok" and m.judge(65, 40, 0.30)[0] == "over"        # 25 in money is the smallest swing that counts


def test_no_usual_week_is_not_a_verdict():
    assert m.judge(500, None, 0.3) == ("ok", None, 0.0) and m.judge(500, 0, 0.3)[0] == "ok"


def test_high_is_twice_the_limit_away_and_a_stop_is_always_high():
    assert m.level_of("over", 1.59, 0.3) == "medium" and m.level_of("over", 1.6, 0.3) == "high"
    assert m.level_of("under", 0.41, 0.3) == "medium" and m.level_of("under", 0.4, 0.3) == "high"
    assert m.level_of("stopped", 0.0, 0.3) == "high"


# ── The cause ───────────────────────────────────────────────────────────────

NOW = {"clicks": 70, "cpc": 4.3}


@pytest.mark.parametrize("now, base, want", [
    ({"clicks": 70, "cpc": 4.3}, {"clicks": 70, "cpc": 2.9}, "cpc"),                    # same clicks, dearer
    ({"clicks": 105, "cpc": 2.9}, {"clicks": 70, "cpc": 2.9}, "clicks"),                # more clicks, same price
    ({"clicks": 98, "cpc": 3.9}, {"clicks": 70, "cpc": 2.9}, "both"),                   # 0.336 and 0.296 of the move: neither reaches 60%
    ({"clicks": 140, "cpc": 2.0}, {"clicks": 70, "cpc": 2.9}, "clicks"),                # more clicks and cheaper: still named for the bigger part
    ({"clicks": 70, "cpc": 2.9}, {"clicks": 70, "cpc": 2.9}, None),                     # nothing moved
])
def test_the_cause_is_the_larger_part_of_the_move_in_logs(now, base, want):
    assert m.cause(now, base) == want


def test_no_clicks_on_either_side_gives_no_cause():
    assert m.cause({"clicks": 0, "cpc": None}, {"clicks": 70, "cpc": 2.9}) is None and m.cause(NOW, None) is None
    assert m.cause(NOW, {"clicks": 0, "cpc": None}) is None


# ── An account ──────────────────────────────────────────────────────────────

def test_spend_above_the_band_is_over_with_the_figures_and_the_cause():
    a = go(account(301))                                  # 301 against 203: 48% up, 98 more; same clicks, dearer
    assert a["status"] == "over" and a["ratio"] == pytest.approx(1.4828, abs=1e-4) and a["diff"] == 98.0 and a["level"] == "medium"
    assert a["cause"] == "cpc" and a["brand"]["cost"] == 301.0 and a["brand"]["cpc"] == pytest.approx(4.3)
    assert a["expected"]["cost"] == 203.0 and a["expected"]["low"] == pytest.approx(142.1) and a["expected"]["high"] == pytest.approx(263.9) and a["expected"]["weeks"] == 7
    assert (a["weekFrom"], a["weekTo"]) == ("2026-10-12", "2026-10-18") and len(a["trend"]) == 8


def test_twice_the_limit_over_is_high():
    a = go(account(329))                                  # 329 / 203 = 1.62
    assert a["status"] == "over" and a["level"] == "high"


def test_spend_below_the_band_is_under_and_more_clicks_are_not_blamed_for_it():
    a = go(account(98, last_clicks=35))                   # 98 against 203: 52% down, clicks halved
    assert a["status"] == "under" and a["diff"] == -105.0 and a["cause"] == "clicks" and a["level"] == "medium"


def test_spend_inside_the_band_is_ok_with_no_cause_and_no_plan():
    a = go(account(217))                                  # 7% up
    assert a["status"] == "ok" and a["cause"] is None and a["level"] is None and a["plan"]["steps"] == [] and a["findings"] == []


def test_a_week_with_nothing_after_weeks_of_spend_is_stopped_and_high():
    a = go(days("11", "P2_Brand", USUAL + [None], [70] * 7 + [0]))
    assert a["status"] == "stopped" and a["level"] == "high" and a["ratio"] == 0.0 and a["diff"] == -203.0
    assert a["findings"][0]["kind"] == "stopped" and a["findings"][0]["level"] == "high"


def test_a_stop_is_named_even_with_less_than_four_weeks_of_history():
    a = go(days("11", "P2_Brand", [None] * 5 + [100, 100, None], [10] * 8))
    assert a["status"] == "stopped" and a["expected"] is None


def test_under_four_weeks_of_spend_is_new_and_never_a_pass():
    a = go(days("11", "P2_Brand", [None] * 4 + [100, 100, 100, 100], [10] * 8))
    assert a["status"] == "new" and a["expected"] is None and a["findings"][0]["kind"] == "new" and a["findings"][0]["level"] == "info"


def test_no_spend_in_the_whole_window_is_idle_and_no_brand_campaign_is_none():
    quiet = days("11", "P2_Brand", [0] * 8, [0] * 8)                  # impressions and no cost: the campaign is there, it spent nothing
    assert go(quiet)["status"] == "idle" and go(quiet)["findings"][0]["kind"] == "idle"
    assert go([])["status"] == "none"                                  # a campaign that never spent has no rows at all, so it cannot be seen
    a = go(days("22", "NonBrand Core", USUAL + [301], [70] * 8))
    assert a["status"] == "none" and a["findings"][0]["kind"] == "none" and a["otherCampaigns"][0]["name"] == "NonBrand Core"


def test_the_limit_changes_the_verdict():
    rows = account(264 + 7)                               # 271 / 203 = 1.335
    assert go(rows, limit=0.30)["status"] == "over" and go(rows, limit=0.40)["status"] == "ok" and go(rows, limit=0.40)["expected"]["high"] == pytest.approx(284.2)


def test_a_campaign_marked_as_brand_counts_and_one_marked_not_brand_does_not():
    rows = days("22", "Core Search", USUAL + [301], [70] * 8)
    assert go(rows)["status"] == "none"
    assert go(rows, overrides={"22": True})["status"] == "over"
    assert go(account(301), overrides={"11": False})["status"] == "none"


def test_several_brand_campaigns_are_put_together_and_the_one_that_moved_is_named():
    a = days("11", "P2_Brand Core", [98] * 7 + [196], [35] * 8) + days("12", "P2_Brand Exact", [105] * 8, [35] * 8)
    r = go(a)
    assert r["status"] == "over" and r["brand"]["cost"] == 301.0 and r["expected"]["cost"] == 203.0
    top = r["campaigns"][0]
    assert top["name"] == "P2_Brand Core" and top["diff"] == 98.0 and top["state"] == "over" and top["usual"] == 98.0
    assert r["campaigns"][1]["state"] == "steady" and r["campaigns"][1]["diff"] == 0.0
    f = [x for x in r["findings"] if x["kind"] == "campaign"][0]
    assert "P2_Brand Core accounts for most of the change" == f["title"] and "$196" in f["detail"] and "$98" in f["detail"]


def brands(*moves, usual=98):
    """One brand campaign per move: each spends `usual` a week for seven weeks and `usual + move` last week."""
    out = []
    for i, mv in enumerate(moves):
        out += days(str(10 + i), "Brand " + "ABCDEFGHIJ"[i] if i < 9 else "Brand Z", [usual] * 7 + [usual + mv], [35] * 8)
    return out


def test_the_biggest_mover_is_first_whatever_its_name_and_is_named_when_it_is_most_of_the_move():
    r = go(days("11", "Brand A", [98] * 7 + [105], [35] * 8) + days("12", "Brand B", [98] * 7 + [119], [35] * 8) + days("13", "Brand Zeta", [98] * 7 + [168], [35] * 8))
    assert [c["name"] for c in r["campaigns"]] == ["Brand Zeta", "Brand B", "Brand A"] and [c["diff"] for c in r["campaigns"]] == [70.0, 21.0, 7.0]
    assert r["status"] == "over" and r["diff"] == 98.0
    assert [f["title"] for f in r["findings"] if f["kind"] == "campaign"] == ["Brand Zeta accounts for most of the change"]      # 70 of 98


def test_a_move_spread_evenly_names_no_campaign():
    r = go(brands(35, 35, 35))                              # a third each of +105: under half, so none of them is "most of it"
    assert r["status"] == "over" and [f for f in r["findings"] if f["kind"] == "campaign"] == []


def test_a_campaign_that_moved_the_other_way_is_never_named_for_the_move():
    r = go(brands(-90, 60, 60), limit=0.10)                 # +30 on a usual 294 (10.2%, over a 10% band) and the biggest mover (A, 90) fell: it is half of the move in size, the wrong way
    assert r["status"] == "over" and r["diff"] == 30.0 and r["campaigns"][0]["name"] == "Brand A" and r["campaigns"][0]["diff"] == -90.0
    assert [f for f in r["findings"] if f["kind"] == "campaign"] == []


def test_the_where_step_names_the_campaigns_that_moved_biggest_first_three_at_most():
    r = go(brands(70, 70, 70, 35, 35))
    step = [s for s in r["plan"]["steps"] if s["id"] == "where"][0]
    assert step["campaigns"] == ["Brand A", "Brand B", "Brand C"] and "5 brand campaigns moved in all" in step["why"] and "and 2 more" in step["why"]


def test_a_cost_per_click_step_with_a_small_gain_says_no_figure():
    small = days("11", "P2_Brand", [70] * 7 + [100.8], [35] * 7 + [42])         # clicks up 20%, cost per click up 20%: both; 42 x $0.40 = $16.80 is under 25
    a = go(small)
    assert a["status"] == "over" and a["cause"] == "both"
    assert [s for s in a["plan"]["steps"] if s["id"] == "cpc"][0]["worth"] is None


def test_a_campaign_that_spent_only_this_week_makes_the_account_new_and_not_idle():
    a = go(days("11", "P2_Brand", [0] * 8, [0] * 8, today=THU, this_week=(10, 1)), today=THU)
    assert a["status"] == "new"


def test_a_brand_campaign_that_has_just_started_is_new_and_one_that_stopped_is_stopped():
    rows = days("11", "P2_Brand", USUAL + [203], [70] * 8) + days("12", "Brand Launch", [None] * 7 + [140], [10] * 8) + days("13", "Brand Old", [28] * 7 + [None], [5] * 8)
    r = go(rows)
    states = {c["name"]: (c["state"], c["diff"]) for c in r["campaigns"]}
    assert states["Brand Launch"] == ("new", 140.0) and states["Brand Old"] == ("stopped", -28.0) and states["P2_Brand"][0] == "steady"


def test_the_status_of_a_campaign_is_the_latest_one_read():
    r = go(days("11", "P2_Brand", USUAL + [None], [70] * 7 + [0], status="PAUSED"))
    assert r["campaigns"][0]["status"] == "PAUSED" and r["campaigns"][0]["state"] == "stopped"


# ── This week so far ────────────────────────────────────────────────────────

def test_the_week_so_far_is_held_to_the_band_pro_rata_from_the_third_day():
    base = account(203, today=THU, this_week=(87, 10))     # three days at 87 = 261 against 203 / 7 x 3 = 87: 3x the pace
    a = go(base, today=THU)
    p = a["partial"]
    assert a["status"] == "ok" and p["days"] == 3 and p["cost"] == 261.0 and p["expected"] == 87.0 and p["status"] == "over" and p["from"] == "2026-10-19"
    f = [x for x in a["findings"] if x["kind"] == "partial-over"][0]
    assert f["level"] == "medium" and "ahead of" in f["title"] and "3 days since Monday" in f["detail"] and "$261" in f["detail"]


def test_a_pace_that_matches_the_usual_week_is_not_flagged_and_the_second_day_is_the_first_judged():
    assert go(account(203, today=THU, this_week=(29, 10)), today=THU)["partial"]["status"] == "ok"
    assert go(account(203), today=TUE)["partial"] is None                          # one day of the week has happened
    wed = dt.date(2026, 10, 21)
    p = go(account(203, today=wed, this_week=(87, 10)), today=wed)["partial"]
    assert p["days"] == 2 and p["status"] == "over"
    assert go(account(203), today=dt.date(2026, 10, 19))["partial"] is None       # a Monday: nothing yet


def test_a_part_week_that_agrees_with_a_bad_week_is_low_and_one_alone_is_medium():
    a = go(account(301, today=THU, this_week=(87, 10)), today=THU)
    assert [f["kind"] for f in a["findings"]][:2] == ["over", "partial-over"] and a["findings"][1]["level"] == "low"


def test_a_part_week_that_is_slow_is_behind():
    a = go(account(203, today=THU, this_week=(5, 1)), today=THU)
    assert a["partial"]["status"] == "under" and [f["kind"] for f in a["findings"]] == ["partial-under"] and "behind" in a["findings"][0]["title"]
    assert [s["id"] for s in a["plan"]["steps"]] == ["changed"] and a["plan"]["steps"][0]["level"] == "low" and a["plan"]["note"] is None


# ── The findings ────────────────────────────────────────────────────────────

def test_the_over_finding_says_the_figures_the_band_and_the_cause():
    f = go(account(301))["findings"][0]
    assert f["kind"] == "over" and f["level"] == "medium" and f["title"] == "Brand spend is $301, above the usual $203"
    assert "48% above a usual week of $203" in f["detail"] and "the last 7 weeks" in f["detail"] and "the band is $142 to $264" in f["detail"]
    assert "dearer clicks (clicks level, cost per click up 48%)" in f["detail"] and f["nextStep"].startswith("Start with: ")


def test_the_under_finding_says_below():
    f = go(account(98, last_clicks=35))["findings"][0]
    assert f["title"] == "Brand spend is $98.00, below the usual $203" and "52% below a usual week of $203" in f["detail"] and ": fewer clicks (clicks down 50%, cost per click down 3%)" in f["detail"]


# ── The plan ────────────────────────────────────────────────────────────────

def ids(a):
    return [s["id"] for s in a["plan"]["steps"]]


def test_a_rise_in_the_price_of_a_click_gets_the_auction_the_change_history_and_a_ceiling():
    a = go(account(301))
    assert ids(a) == ["cpc", "where", "changed", "ceiling"] and [s["n"] for s in a["plan"]["steps"]] == [1, 2, 3, 4]
    cpc = a["plan"]["steps"][0]
    assert "$4.30 against a usual $2.90 (48% up)" in cpc["why"] and "about $98.00 less" in cpc["worth"]       # 70 clicks x ($4.30 - $2.90) = $98
    ceiling = a["plan"]["steps"][3]
    assert "$98.00 over a usual week" in ceiling["why"] and "about $29.00 a day" in " ".join(ceiling["how"])
    assert "$425 a month ($98.00 a week)" in ceiling["worth"]                                            # 98 x 52 / 12 = 424.67


def test_a_rise_in_clicks_starts_by_ruling_out_a_good_reason_and_asks_for_the_search_terms():
    a = go(days("11", "P2_Brand", [140, 140, 147, 140, 147, 140, 140, 210], [70, 70, 70, 70, 70, 70, 70, 105]))       # 105 clicks at the usual $2.00
    assert a["cause"] == "clicks" and ids(a) == ["clicks", "where", "changed", "ceiling"]
    s = a["plan"]["steps"][0]
    assert "Clicks were 50% up on a usual 70" in s["why"] and "Search terms" in " ".join(s["how"]) and "Invalid clicks" in " ".join(s["how"]) and "launch" in " ".join(s["how"])


def test_both_causes_get_both_steps():
    a = go(days("11", "P2_Brand", [140, 140, 147, 140, 147, 140, 140, 301], [70, 70, 70, 70, 70, 70, 70, 98]))
    assert a["cause"] == "both" and ids(a)[:2] == ["clicks", "cpc"]


def test_a_stop_gets_the_running_check_with_the_clicks_it_cost_and_the_change_history():
    a = go(days("11", "P2_Brand", USUAL + [None], [70] * 7 + [0]))
    assert ids(a) == ["running", "changed"]
    s = a["plan"]["steps"][0]
    assert s["level"] == "high" and "Nothing was spent from 2026-10-12 to 2026-10-18 although a usual week is $203" in s["why"] and "about 70 brand clicks for $203" in s["worth"]
    assert "Billing" in " ".join(s["how"]) and "Disapproved" in " ".join(s["how"])


def test_a_fall_with_fewer_clicks_points_to_the_brand_is_monitor_and_sizes_the_gap():
    a = go(account(98, last_clicks=35))
    assert ids(a) == ["running", "shown", "where", "changed"]
    shown = a["plan"]["steps"][1]
    assert "/p2/paid-marketing/brand-is" in " ".join(shown["how"]) and "About 35 fewer brand clicks than a usual week, worth $102 at the usual cost per click" in shown["worth"]       # 35 x $2.90 = 101.5


def test_a_fall_in_the_price_with_clicks_held_is_good_news_to_check_not_a_fault():
    a = go(days("11", "P2_Brand", [210, 210, 210, 210, 210, 210, 210, 119], [70] * 7 + [70]))      # 119 is 43% under; the clicks held, each cost 1.70 not 3.00
    assert a["status"] == "under" and a["cause"] == "cpc" and "cheaper" in ids(a) and a["plan"]["steps"][ids(a).index("cheaper")]["level"] == "low"


def test_every_step_has_a_number_a_title_a_reason_and_how_and_the_plan_says_where_the_limits_come_from():
    for a in (go(account(301)), go(account(98, last_clicks=35)), go(days("11", "P2_Brand", USUAL + [None], [70] * 7 + [0]))):
        for s in a["plan"]["steps"]:
            assert s["title"] and s["why"] and s["how"] and all(h for h in s["how"]) and s["level"] in ("high", "medium", "low")
        assert "account's own history" in a["plan"]["note"]


def test_a_currency_without_a_symbol_is_written_with_its_code():
    a = go(account(301), currency="INR")
    assert a["findings"][0]["title"] == "Brand spend is ₹301, above the usual ₹203"
    assert go(account(301), currency="CHF")["findings"][0]["title"] == "Brand spend is 301 CHF, above the usual 203 CHF"


def test_nothing_the_agent_writes_has_a_dash_or_an_unfilled_slot():
    seen = []
    for a in (go(account(301)), go(account(98, last_clicks=35)), go(days("11", "P2_Brand", USUAL + [None], [70] * 7 + [0])), go(account(301, today=THU, this_week=(87, 10)), today=THU),
              go(days("22", "Core", USUAL + [301], [70] * 8)), go(days("11", "P2_Brand", [None] * 8, [0] * 8))):
        seen += [f["title"] + f["detail"] + f["nextStep"] for f in a["findings"]]
        for s in a["plan"]["steps"]:
            seen += [s["title"], s["why"], s["worth"] or ""] + s["how"]
        seen.append(a["plan"]["note"] or "")
    text = "\n".join(seen)
    assert text.strip() and "—" not in text and "–" not in text
    assert not re.search(r"%[sd]|\{|None|nan", text)
