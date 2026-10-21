"""Seasonality Analysis: from a percentage to dollars and the steps (tracker/paid_season_plans.py).

The verdict says "raise budgets 30% before the month starts". These tests pin what that comes to in dollars (worked out by hand below), that a month where spend already follows
demand is not told to spend more, that a cut is worded as a cut, which kind of campaign each step is for, and when a holiday earns a Google seasonality adjustment and at what size.
"""
import datetime as dt

import pytest

from tests.test_seasonality import SEASON, TODAY, S, synth
from tracker import paid_season_plans as sp

EM, EN = chr(0x2014), chr(0x2013)
BASE = {"daily": 200.0, "cpa": 20.0, "months": 3}


def month(m=11, y=2026, budget=30, cost=100, cpa=100, verdict="peak", events=()):
    return {"y": y, "m": m, "name": sp.MONTH_NAMES[m], "budget": budget, "cost": cost, "cpa": cpa, "verdict": verdict, "events": list(events)}


def event(days=5, cvr=0.2, n=2, key="bfcm", start="2026-11-26"):
    a = dt.date.fromisoformat(start)
    return {"key": key, "name": "Thanksgiving to Cyber Monday", "start": start, "end": (a + dt.timedelta(days=days - 1)).isoformat(), "cvrChange": cvr, "n": n, "convLift": 0.5, "spendLift": 0.1, "cpaChange": -0.2}


def text(p):
    return " ".join(p["steps"])


# ── An average month ──────────────────────────────────────────────────────

def bucket(m, cost, conv, days=30, usable=True, y=2026):
    return {"y": y, "m": m, "days": days, "cost": cost, "conv": conv, "usable": usable, "full": True}


def idx(**by_month):
    return [{"m": i, "index": by_month.get("m%d" % i)} for i in range(1, 13)]


def test_an_average_months_spend_takes_each_months_own_season_out():
    buckets = [bucket(7, 3000.0, 150), bucket(8, 6000.0, 300), bucket(9, 6000.0, 400)]        # 100, 200 and 200 a day; $20, $20 and $15 a conversion
    cost = idx(m7=50, m8=150, m9=100)                                                         # July was half a normal month, August one and a half times
    cpa = idx(m7=100, m8=80, m9=75)                                                           # and a conversion cost 80% and 75% of usual in August and September
    base = sp.baseline(buckets, {"cost": cost, "cpa": cpa})
    assert base["daily"] == pytest.approx((200 + 200 / 1.5 + 200) / 3) and base["months"] == 3        # 100/.5, 200/1.5, 200/1.0
    assert base["cpa"] == pytest.approx((20 + 20 / 0.8 + 15 / 0.75) / 3)


def test_only_the_latest_three_usable_months_count():
    buckets = [bucket(1, 100.0, 5), bucket(2, 100.0, 5), bucket(3, 6000.0, 300), bucket(4, 900.0, 300, usable=False), bucket(5, 6000.0, 300), bucket(6, 6000.0, 300)]
    ix = {"cost": idx(**{"m%d" % i: 100 for i in range(1, 13)}), "cpa": idx(**{"m%d" % i: 100 for i in range(1, 13)})}
    base = sp.baseline(buckets, ix)
    assert base["daily"] == pytest.approx(200.0) and base["months"] == 3                            # March, May and June; April was paused, January and February are older


def test_a_month_with_no_index_is_skipped_and_nothing_usable_gives_none():
    ix = {"cost": idx(m5=100), "cpa": idx(m5=100)}
    assert sp.baseline([bucket(5, 6000.0, 300), bucket(6, 9999.0, 300)], ix)["months"] == 1
    assert sp.baseline([bucket(6, 9999.0, 300)], ix) is None and sp.baseline([], ix) is None
    assert sp.baseline([bucket(5, 6000.0, 0)], ix)["cpa"] is None                                  # no conversions: no cost per conversion


# ── A month ahead, in dollars ─────────────────────────────────────────────

def test_a_peak_month_says_how_many_dollars_a_day_and_over_the_month():
    p = sp.month_plan(month(), BASE, TODAY, ["google"])
    assert p["dollars"]["fromDaily"] == 200.0 and p["dollars"]["toDaily"] == 260.0 and p["dollars"]["gap"] == 1800.0 and p["dollars"]["left"] == 30
    assert p["worth"].startswith("About $260 a day in all, $1,800 more over the 30 days left in November, set against what the usual pattern would spend (about $200 a day)")
    assert "about $20.00 each) that is about 90 more" in p["worth"] and "converts a little worse" in p["worth"]
    assert "An average month here spends about $200 a day (worked out from the last 3 months" in p["worth"]
    assert "from about $200 to about $260 a day in all" in p["steps"][0] and "Limited by budget" in p["steps"][0]
    assert p["steps"][1].startswith("Do it a few days before November starts (about Oct 29), and put the budgets back on Dec 1")


def test_the_current_month_counts_only_the_days_left_and_starts_today():
    p = sp.month_plan(month(m=10, budget=20), BASE, dt.date(2026, 10, 16), ["google"])
    assert p["dollars"]["left"] == 16 and p["dollars"]["gap"] == pytest.approx(40.0 * 16)
    assert "$640 more over the 16 days left in October" in p["worth"] and p["steps"][1].startswith("Do it from Oct 16, and put the budgets back on Nov 1")


def test_a_month_where_spend_already_follows_demand_needs_no_change():
    p = sp.month_plan(month(cost=130), BASE, TODAY, ["google"])                # the usual pattern is 260 a day and the plan asks for 260
    assert p["steps"] == [] and p["dollars"] is None and p["worth"] is None
    assert p["note"] == "Spend usually follows demand in November already (about $260 a day on the usual pattern), so no change is needed beyond watching it."


def test_spend_that_already_runs_ahead_of_the_plan_is_not_told_to_rise_and_a_quiet_month_already_low_is_not_told_to_cut():
    p = sp.month_plan(month(cost=150), BASE, TODAY, ["google"])                # the usual pattern is 300 a day, above the 260 a peak month's plan asks for
    assert p["steps"] == [] and p["worth"] is None and p["note"] == "On the usual pattern spend in November runs at about $300 a day, already above the $260 the plan asks for, so no rise is needed."
    q = sp.month_plan(month(budget=-20, verdict="quiet", cost=60), BASE, TODAY, ["google"])        # the usual pattern is 120 a day, below the 160 the plan asks for
    assert q["steps"] == [] and q["note"] == "On the usual pattern spend in November runs at about $120 a day, already below the $160 the plan asks for, so no cut is needed."


def test_a_quiet_month_is_a_cut_and_the_cut_is_worded_as_one():
    p = sp.month_plan(month(budget=-20, verdict="quiet", cpa=100), BASE, TODAY, ["google"])
    assert p["dollars"]["toDaily"] == 160.0 and p["dollars"]["gap"] == -1200.0
    assert "$1,200 less over the 30 days left in November" in p["worth"] and "about 60 conversions would not come" in p["worth"]
    assert "lower the daily budgets of the dearest campaigns together from about $200 to about $160" in p["steps"][0] and "learning period" in p["steps"][1]


def test_a_dearer_month_raises_in_two_steps():
    costly = sp.month_plan(month(budget=15, verdict="peak_costly"), BASE, TODAY, ["google"])
    assert "half now and half a week later" in text(costly)
    assert "half now" not in text(sp.month_plan(month(budget=15, verdict="peak"), BASE, TODAY, ["google"]))


def test_the_cost_of_a_conversion_in_that_month_prices_the_extra_conversions():
    p = sp.month_plan(month(cpa=80), BASE, TODAY, ["google"])                  # 20 * 0.8 = $16: 1800 / 16 = 112.5, which rounds to 112
    assert "about $16.00 each) that is about 112 more" in p["worth"]
    none = sp.month_plan(month(), dict(BASE, cpa=None), TODAY, ["google"])
    assert "conversions at this month's expected cost" not in none["worth"] and none["worth"].startswith("About $260 a day")


def test_a_typical_month_with_nothing_to_change_has_no_plan():
    assert sp.month_plan(month(budget=0, verdict="typical"), BASE, TODAY, ["google"]) is None
    assert sp.month_plan(month(budget=0, verdict="typical", events=[event(days=10)]), BASE, TODAY, ["google"]) is None        # an event that cannot be adjusted is no reason for a plan


def test_without_an_average_month_the_change_stays_a_percentage_and_says_why():
    p = sp.month_plan(month(), None, TODAY, ["google"])
    assert p["dollars"] is None and p["steps"] == [] and "too thin to work an average month's spend out of" in p["note"]


# ── Which platform each step is for ───────────────────────────────────────

def test_meta_alone_gets_ads_manager_steps_in_small_moves_and_no_google():
    p = sp.month_plan(month(), BASE, TODAY, ["meta"])
    t = text(p)
    assert t.startswith("Open Ads Manager") and "no more than about 20% at a time" in t and "reaches about $260 a day" in t and "Google Ads" not in t and "Limited by budget" not in t


def test_both_platforms_get_both_sets_of_steps():
    t = text(sp.month_plan(month(), BASE, TODAY, ["google", "meta"]))
    assert t.startswith("In Google Ads open Campaigns") and "On Meta, open Ads Manager" in t
    cut = text(sp.month_plan(month(budget=-20, verdict="quiet"), BASE, TODAY, ["google", "meta"]))
    assert "On Meta, open Ads Manager and lower the budgets" in cut


# ── Holidays: the Google seasonality adjustment ───────────────────────────

def test_a_short_holiday_with_a_real_conversion_rate_change_gets_an_adjustment_of_that_size():
    a = sp.adjustment(event(days=5, cvr=0.2, n=2), ["google"])
    assert a["eligible"] and a["modifier"] == 20 and a["years"] == 2 and a["days"] == 5
    assert "about +20% (measured over 2 years)" in " ".join(a["steps"]) and "Nov 26 to Nov 30 (up to 7 days)" in " ".join(a["steps"])
    assert "Smart Bidding (Target CPA, Target ROAS" in a["steps"][1] and "manual bids do not read it" in a["steps"][3]
    assert "search the help for" in a["steps"][0]                                                    # the menu path is not claimed as exact


def test_one_year_of_evidence_halves_the_size_and_says_so():
    a = sp.adjustment(event(cvr=0.2, n=1), ["google"])
    assert a["modifier"] == 10 and "with only one year behind it, a smaller change such as +10% is the safer start" in a["steps"][2]
    drop = sp.adjustment(event(cvr=-0.15, n=3), ["google"])
    assert drop["modifier"] == -15 and "about -15% (measured over 3 years)" in drop["steps"][2]


def test_the_size_is_rounded_to_five_percent():
    assert sp.adjustment(event(cvr=0.237, n=2), ["google"])["modifier"] == 25 and sp.adjustment(event(cvr=0.226, n=2), ["google"])["modifier"] == 25
    assert sp.adjustment(event(cvr=0.222, n=2), ["google"])["modifier"] == 20


def test_a_holiday_that_cannot_have_an_adjustment_says_why():
    long_ = sp.adjustment(event(days=8), ["google"])
    assert not long_["eligible"] and long_["why"] == "It runs 8 days, and Google's seasonality adjustments are for up to 7: use the budget change for the month instead."
    assert sp.adjustment(event(days=7), ["google"])["eligible"]                                       # a week exactly is allowed
    small = sp.adjustment(event(cvr=0.09), ["google"])
    assert not small["eligible"] and small["why"] == "Conversion rate moved only +9% in past years, too little to be worth an adjustment."
    assert sp.adjustment(event(cvr=0.10), ["google"])["eligible"]
    flat = sp.adjustment(event(cvr=0.001), ["google"])
    assert flat["why"] == "Conversion rate barely moved in past years, too little to be worth an adjustment."
    none = sp.adjustment(event(cvr=None), ["google"])
    assert not none["eligible"] and "no history of how conversion rate moved" in none["why"]
    meta = sp.adjustment(event(), ["meta"])
    assert not meta["eligible"] and "Meta has no seasonality adjustment" in meta["why"]
    assert sp.adjustment(event(), ["google", "meta"])["eligible"]                                     # Google is there: the adjustment is for its campaigns


def test_a_months_plan_carries_its_eligible_adjustments_even_when_the_budget_needs_nothing():
    p = sp.month_plan(month(budget=0, verdict="typical", events=[event(), event(days=9, key="x")]), BASE, TODAY, ["google"])
    assert [e["eligible"] for e in p["events"]] == [True, False] and p["steps"] == []


# ── Through the whole analysis ────────────────────────────────────────────

def test_the_analysis_attaches_the_plans_and_the_average_month():
    out = S.analyze(synth(season=SEASON, growth=1.0), today=TODAY, platforms=["google"], currency="USD")
    assert out["base"]["months"] == 3 and out["base"]["daily"] == pytest.approx(200.0, rel=0.05)
    nov = next(m for m in out["upcoming"] if m["m"] == 11)
    assert nov["verdict"] == "peak" and nov["plan"]["dollars"]["toDaily"] == pytest.approx(out["base"]["daily"] * (1 + nov["budget"] / 100.0))
    assert nov["plan"]["steps"] and nov["plan"]["worth"]
    bfcm = next(e for e in nov["events"] if e["key"] == "bfcm")
    assert "cvrChange" in bfcm and "n" in bfcm                                                       # what the adjustment is sized from travels with the event


def test_a_short_history_has_no_base_and_no_plans():
    out = S.analyze(synth(start="2025-11-01", end="2026-10-14", season=SEASON), today=TODAY)
    assert out["base"] is None and out["upcoming"] == []


def test_the_report_passes_the_accounts_platforms_and_currency(monkeypatch):
    seen = {}
    real = S.analyze
    monkeypatch.setattr(S, "analyze", lambda daily, **kw: seen.update(kw) or real(daily, **kw))
    monkeypatch.setattr(S, "read", lambda *a, **k: (synth(season=SEASON), [], [], []))
    S.report(object(), [{"platform": "google", "id": "1", "currency": "EUR"}, {"platform": "meta", "id": "act_1", "currency": "EUR"}], 36, today=TODAY)
    assert seen["platforms"] == ["google", "meta"] and seen["currency"] == "EUR"


def test_no_plan_uses_an_em_or_en_dash_or_leaves_a_placeholder():
    cases = [sp.month_plan(month(), BASE, TODAY, ["google", "meta"]), sp.month_plan(month(budget=-20, verdict="quiet"), BASE, TODAY, ["meta"]),
             sp.month_plan(month(budget=15, verdict="peak_costly", events=[event(), event(days=9, key="x"), event(cvr=None, key="y")]), BASE, TODAY, ["google"]), sp.month_plan(month(), None, TODAY, ["google"])]
    for p in cases:
        blob = repr(p)
        assert EM not in blob and EN not in blob and "None" not in text(p) and "{" not in text(p) and "%s" not in text(p), blob
