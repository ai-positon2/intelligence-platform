"""Brand Spend Alert: the readings that explain a move (tracker/paid_brandspend_insight.py), with the figures worked by hand. No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_brandspend_insight as ins  # noqa: E402
from tracker import paid_brandspend_math as m  # noqa: E402

TODAY = dt.date(2026, 10, 21)


def wk(cost, conv=20.0):
    return {"cost": cost, "conversions": conv}


BASE = [wk(200.0) for _ in range(7)]


# ── Efficiency ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cost, conv, kind", [(300.0, 20.0, "same-for-more"), (300.0, 30.0, "followed"), (300.0, 24.0, "partly"), (300.0, 25.4, "partly"), (300.0, 23.0, "same-for-more"), (300.0, 26.0, "followed")])
def test_a_rise_is_the_same_result_for_more_money_or_followed_by_conversions(cost, conv, kind):
    e = ins.efficiency(wk(cost, conv), BASE, "over")
    assert e["kind"] == kind and e["usualConv"] == 20.0 and e["usualCpa"] == 10.0 and e["cpa"] == round(cost / conv, 2)


def test_a_fall_is_the_same_result_for_less_or_a_loss_of_conversions():
    assert ins.efficiency(wk(100.0, 20.0), BASE, "under")["kind"] == "same-for-less" and ins.efficiency(wk(100.0, 17.0), BASE, "under")["kind"] == "same-for-less"
    assert ins.efficiency(wk(100.0, 16.0), BASE, "under")["kind"] == "lost" and ins.efficiency(wk(0.0, 0.0), BASE, "stopped")["kind"] == "lost"


def test_an_account_on_its_usual_has_figures_but_no_verdict_and_without_conversions_there_is_nothing_to_compare():
    e = ins.efficiency(wk(210.0, 21.0), BASE, "ok")
    assert e["kind"] is None and e["ratioConv"] == 1.05 and e["conv"] == 21.0
    assert ins.efficiency(wk(300.0, 5.0), [wk(200.0, 0.0)] * 5, "over") is None and ins.efficiency(wk(300.0, 5.0), [], "over") is None
    assert ins.efficiency(wk(300.0, 5.0), [wk(0.0, 9.0)], "over") is None                            # weeks with no spend are not usual weeks
    assert ins.efficiency(wk(300.0, 0.0), BASE, "over")["cpa"] is None


# ── Impression share ────────────────────────────────────────────────────────

def sh(share, lost_rank, lost_budget, eligible=1000):
    return {"share": share, "lostRank": lost_rank, "lostBudget": lost_budget, "eligible": eligible}


PRIOR = [sh(0.95, 0.03, 0.02) for _ in range(7)]


def test_a_falling_share_with_more_lost_to_rank_names_a_competitor():
    v = ins.share_view(PRIOR + [sh(0.70, 0.25, 0.05)])
    assert v == {"now": 0.7, "usual": 0.95, "drop": 0.25, "lostRank": 0.25, "usualLostRank": 0.03, "lostBudget": 0.05, "usualLostBudget": 0.02, "signal": "competitor"}


def test_a_falling_share_with_more_lost_to_budget_names_the_budget():
    assert ins.share_view(PRIOR + [sh(0.70, 0.03, 0.27)])["signal"] == "budget"


def test_a_share_that_held_is_steady_and_a_small_fall_is_neither():
    assert ins.share_view(PRIOR + [sh(0.94, 0.03, 0.03)])["signal"] == "steady"
    assert ins.share_view(PRIOR + [sh(0.90, 0.07, 0.03)])["signal"] is None                          # a 5 point fall with only 4 points more lost to rank


def test_the_edges_of_the_competitor_rule_are_inside_it():
    assert ins.share_view(PRIOR + [sh(0.90, 0.13, 0.02)])["signal"] == "competitor"                  # exactly 5 points down and 10 points more lost to rank
    assert ins.share_view(PRIOR + [sh(0.9001, 0.13, 0.02)])["signal"] is None
    assert ins.share_view(PRIOR + [sh(0.9201, 0.03, 0.02)])["signal"] == "steady" and ins.share_view(PRIOR + [sh(0.9199, 0.03, 0.02)])["signal"] is None


def test_too_few_searches_or_weeks_is_no_reading():
    assert ins.share_view(PRIOR + [sh(0.5, 0.4, 0.0, eligible=99)]) is None
    assert ins.share_view(PRIOR[:2] + [sh(0.5, 0.4, 0.0)]) is None
    assert ins.share_view([sh(None, None, None)] * 3 + PRIOR[:3] + [sh(0.5, 0.4, 0.0)])["usual"] == 0.95
    assert ins.share_view([sh(0.9, 0.0, 0.0, eligible=50)] * 7 + [sh(0.5, 0.4, 0.0)]) is None


def test_the_weekly_share_puts_campaign_days_together_by_eligible_searches():
    rows = [{"date": "2026-10-12", "impressions": 900, "clicks": 90, "cost": 100, "share": 0.9, "lostRank": 0.1, "lostBudget": 0.0},
            {"date": "2026-10-13", "impressions": 100, "clicks": 10, "cost": 10, "share": 0.5, "lostRank": 0.5, "lostBudget": 0.0}]
    got = ins.weekly_share(rows, [(dt.date(2026, 10, 12), dt.date(2026, 10, 18)), (dt.date(2026, 10, 5), dt.date(2026, 10, 11))])
    assert got[0]["eligible"] == 1200 and got[0]["share"] == round(1000 / 1200, 4) and got[0]["lostRank"] == round((1000 * 0.1 + 200 * 0.5) / 1200, 4)
    assert got[1] == {"share": None, "lostRank": None, "lostBudget": None, "eligible": 0}


# ── Spikes ──────────────────────────────────────────────────────────────────

def days(overrides=None, n=56, cost=29.0, clicks=10, conv=1):
    first = TODAY - dt.timedelta(days=n)
    out = []
    for i in range(n):
        d = (first + dt.timedelta(days=i)).isoformat()
        c, k, v = (overrides or {}).get(d, (cost, clicks, conv))
        out.append({"date": d, "cost": c, "clicks": k, "conversions": v})
    return out


def test_a_day_far_above_the_usual_is_named_with_what_its_clicks_did():
    rows = days({"2026-10-14": (150.0, 40, 0), "2026-10-15": (100.0, 10, 1), "2026-10-16": (110.0, 30, 3)})
    got = ins.spikes(rows, TODAY)
    assert [(s["date"], s["kind"]) for s in got] == [("2026-10-14", "unconverted"), ("2026-10-16", "converting"), ("2026-10-15", "price")]
    assert [s["kind"] for s in ins.spikes(days({"2026-10-14": (100.0, 13, 1)}), TODAY)] == ["price"] and [s["kind"] for s in ins.spikes(days({"2026-10-14": (100.0, 15, 1)}), TODAY)] == ["converting"]
    first = got[0]
    assert first["weekday"] == "Wednesday" and first["cost"] == 150.0 and first["usual"] == 29.0 and first["clicks"] == 40 and first["usualClicks"] == 10 and first["conversions"] == 0.0 and first["usualConv"] == 1.0


def test_the_edge_of_a_spike_is_two_and_a_half_times_the_usual_day_and_a_swing_worth_noting():
    assert [s["date"] for s in ins.spikes(days({"2026-10-14": (72.5, 10, 1)}), TODAY)] == ["2026-10-14"]          # 2.5 x 29
    assert ins.spikes(days({"2026-10-14": (72.4, 10, 1)}), TODAY) == []
    small = days({"2026-10-14": (9.0, 3, 0)}, cost=3.0, clicks=1, conv=0)
    assert ins.spikes(small, TODAY) == []                                                              # 3 times the usual, but under 25 in money


def test_only_the_last_four_weeks_before_today_count_and_a_short_history_is_not_judged():
    today_row = days() + [{"date": "2026-10-21", "cost": 500.0, "clicks": 90, "conversions": 0}]                # today is not over, so it is not judged
    assert ins.spikes(days({"2026-09-10": (500.0, 90, 0)}), TODAY) == [] and ins.spikes(today_row, TODAY) == []
    assert ins.spikes(days({"2026-10-14": (500.0, 90, 0)}, n=13), TODAY) == []
    assert [s["date"] for s in ins.spikes(days({"2026-09-23": (500.0, 90, 0)}), TODAY)] == ["2026-09-23"]       # exactly 28 days back


def test_at_most_three_spikes_the_dearest_first():
    rows = days({"2026-10-%02d" % d: (100.0 + d, 40, 0) for d in range(10, 16)})
    assert [s["date"] for s in ins.spikes(rows, TODAY)] == ["2026-10-15", "2026-10-14", "2026-10-13"]


# ── The month and the limits ────────────────────────────────────────────────

def month_rows(per_day=30.0, upto=TODAY):
    return [{"date": (upto.replace(day=1) + dt.timedelta(days=i)).isoformat(), "cost": per_day} for i in range(upto.day + 3)]      # rows run past today: they must not count


@pytest.mark.parametrize("budget, status", [(900, "ok"), (800, "pace-over"), (500, "over"), (1300, "pace-under"), (None, None), (0, None), (886, "ok"), (885, "pace-over"), (600, "pace-over"), (599, "over"), (1162.5, "ok"), (1163, "pace-under")])
def test_the_month_is_held_against_the_saved_budget(budget, status):
    v = ins.month_view(month_rows(), TODAY, budget)
    assert v["spent"] == 600.0 and v["days"] == 20 and v["daysInMonth"] == 31 and v["perDay"] == 30.0 and v["projected"] == 930.0 and v["status"] == status


def test_on_pace_to_the_edge_of_five_percent_over_is_still_on_pace():
    feb = dt.date(2027, 2, 21)
    rows = [{"date": (dt.date(2027, 2, 1) + dt.timedelta(days=i)).isoformat(), "cost": 15.0} for i in range(20)]
    v = ins.month_view(rows, feb, 400)
    assert v["projected"] == 420.0 and v["daysInMonth"] == 28 and v["status"] == "ok" and ins.month_view(rows, feb, 399)["status"] == "pace-over"


def test_when_the_budget_runs_out_and_when_nothing_is_said_about_the_month_yet():
    assert ins.month_view(month_rows(), TODAY, 800)["runsOutOn"] == "2026-10-27" and ins.month_view(month_rows(), TODAY, 900)["runsOutOn"] is None
    assert ins.month_view(month_rows(upto=dt.date(2026, 10, 3)), dt.date(2026, 10, 3), 500) is None and ins.month_view(month_rows(upto=dt.date(2026, 10, 4)), dt.date(2026, 10, 4), 500)["days"] == 3
    assert ins.month_view([], TODAY, 500)["spent"] == 0.0 and ins.month_view([], TODAY, 500)["status"] == "pace-under"


def test_the_weekly_floor_and_ceiling():
    assert ins.limits_view(350.0, None, 300.0) == {"min": None, "max": 300.0, "cost": 350.0, "status": "over", "by": 50.0}
    assert ins.limits_view(150.0, 200.0, None)["status"] == "under" and ins.limits_view(150.0, 200.0, None)["by"] == 50.0
    assert ins.limits_view(250.0, 200.0, 300.0)["status"] == "ok" and ins.limits_view(300.0, 200.0, 300.0)["status"] == "ok" and ins.limits_view(200.0, 200.0, 300.0)["status"] == "ok"
    assert ins.limits_view(250.0, None, None) is None and ins.limits_view(250.0, 0, 0) is None


# ── Brand words and the searches that hold them ─────────────────────────────

def test_brand_words_are_cleaned_and_suggested_from_campaign_names():
    assert ins.terms_of("Northwind, northwind;  Acme Dental\n ab, " + "x" * 41) == ["northwind", "acme dental"] and ins.terms_of(None) == [] and ins.terms_of("") == []
    assert len(ins.terms_of(",".join("word%02d" % i for i in range(30)))) == 20
    assert ins.suggest_terms(["Northwind | Brand | Search", "P2_Brand_Core", "Acme-Brand Exact", "Northwind Brand"]) == ["northwind", "acme"]
    assert ins.suggest_terms(["Brand", "123 Brand", "Abc Brand"]) == [] and ins.suggest_terms([None]) == [] and ins.suggest_terms(["Abcd Brand"]) == ["abcd"]


LEAK = [{"campaignId": "12", "campaign": "NonBrand Implants", "term": "Northwind dental near me", "cost": 60.0, "clicks": 20, "conversions": 2},
        {"campaignId": "13", "campaign": "PMax All", "term": "northwind implants", "cost": 20.0, "clicks": 4, "conversions": 0},
        {"campaignId": "12", "campaign": "NonBrand Implants", "term": "dentist near me", "cost": 500.0, "clicks": 100, "conversions": 9},
        {"campaignId": "11", "campaign": "Northwind | Brand", "term": "northwind dental", "cost": 300.0, "clicks": 200, "conversions": 40},
        {"campaignId": "12", "campaign": "NonBrand Implants", "term": "northwind free", "cost": 0.0, "clicks": 0, "conversions": 0}]


def test_brand_searches_paid_for_by_other_campaigns_are_summed_and_priced_against_the_brand_campaign():
    v = ins.leak(LEAK, ["northwind"], {"11"}, 1.5, 400.0)
    assert v["cost"] == 80.0 and v["clicks"] == 24 and v["conversions"] == 2.0 and v["cpc"] == 3.33 and v["brandCpc"] == 1.5 and v["overpay"] == 44.0 and v["shareOfBrand"] == 0.2
    assert [(c["name"], c["cost"], c["clicks"], c["cpc"], c["top"]) for c in v["campaigns"]] == [("NonBrand Implants", 60.0, 20, 3.0, ["northwind dental near me"]), ("PMax All", 20.0, 4, 5.0, ["northwind implants"])]
    assert v["terms"] == ["northwind"] and v["days"] == 30 and v["campaignCount"] == 2


def test_no_brand_word_or_no_match_is_no_leak_and_an_unknown_brand_price_gives_no_overpay():
    assert ins.leak(LEAK, [], {"11"}, 1.5, 400.0) is None and ins.leak(LEAK, ["zzz"], {"11"}, 1.5, 400.0) is None and ins.leak([], ["northwind"], {"11"}, 1.5, 400.0) is None
    v = ins.leak(LEAK, ["northwind"], {"11"}, None, 0)
    assert v["overpay"] is None and v["shareOfBrand"] is None
    assert ins.leak(LEAK, ["northwind"], {"11"}, 9.0, 400.0)["overpay"] == 0.0                         # the brand campaign costs more per click: nothing is overpaid


def test_a_campaign_marked_brand_is_not_a_leak():
    assert ins.leak(LEAK, ["northwind"], {"11", "12", "13"}, 1.5, 400.0) is None


def test_only_five_campaigns_are_listed_but_all_are_counted():
    rows = [{"campaignId": str(i), "campaign": "C%d" % i, "term": "northwind x", "cost": 10.0 + i, "clicks": 5, "conversions": 0} for i in range(8)]
    v = ins.leak(rows, ["northwind"], set(), 1.0, 100.0)
    assert len(v["campaigns"]) == 5 and v["campaignCount"] == 8 and v["campaigns"][0]["name"] == "C7" and v["cost"] == round(sum(10.0 + i for i in range(8)), 2)


# ── Campaigns with no rows, and the history ─────────────────────────────────

def test_brand_campaigns_google_lists_with_no_spend_row_are_found_enabled_ones_first():
    camps = [{"id": 1, "name": "Alpha Paused Brand", "status": "PAUSED"}, {"id": 2, "name": "Zeta Brand", "status": "ENABLED"}, {"id": 3, "name": "Core", "status": "ENABLED"}, {"id": 4, "name": "Brand Spends", "status": "ENABLED"}]
    got = ins.silent_campaigns(camps, {"1", "2", "4"}, {"4"})
    assert got == [{"id": "2", "name": "Zeta Brand", "status": "ENABLED"}, {"id": "1", "name": "Alpha Paused Brand", "status": "PAUSED"}]
    assert ins.silent_campaigns([{"id": 9, "name": "Brand", "status": None}], {"9"}, set()) == [{"id": "9", "name": "Brand", "status": ""}] and ins.silent_campaigns([], {"1"}, set()) == []


def snap(day, status, cost=200):
    return {"day": day, "status": status, "cost": cost}


def test_the_history_counts_the_days_in_a_row_outside_the_band_ending_today():
    h = ins.history_view([snap("2026-10-17", "ok"), snap("2026-10-18", "over"), snap("2026-10-19", "ok"), snap("2026-10-20", "over"), snap("2026-10-21", "stopped")])
    assert h["streak"] == 2 and h["since"] == "2026-10-20" and h["outsideDays"] == 3 and h["days"] == 5 and h["first"] == "2026-10-17" and len(h["statuses"]) == 5
    assert ins.history_view([snap("2026-10-21", "ok")])["streak"] == 0 and ins.history_view([snap("2026-10-21", "ok")])["since"] is None
    assert ins.history_view([snap("2026-10-21", "new"), snap("2026-10-20", "under")])["streak"] == 0 and ins.history_view([]) is None and ins.history_view(None) is None


def test_the_history_is_read_oldest_first_whatever_order_it_came_in_and_keeps_sixty_days():
    h = ins.history_view([snap("2026-10-21", "over"), snap("2026-10-20", "over"), snap("2026-10-19", "ok")])
    assert h["streak"] == 2 and h["since"] == "2026-10-20"
    long = ins.history_view([snap((dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(), "ok") for i in range(100)])
    assert len(long["statuses"]) == 60 and long["days"] == 100 and long["statuses"][-1]["day"] == "2026-04-10"
