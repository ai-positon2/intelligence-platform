"""Brand IS Monitor: the weeks, the brand rule, putting shares together and what is said (tracker/paid_brandis_math.py)."""
import datetime as dt

import pytest

from tracker import paid_brandis_math as m

TODAY = dt.date(2026, 10, 20)            # a Tuesday: the last whole week is 12 to 18 October
NOW, BEFORE = "2026-10-12", "2026-10-05"


def row(week, cid="1", name="P2_Brand", impr=1000, share=0.95, lb=0.02, lr=0.03, clicks=100, cost=50.0, top=None, abs_top=None):
    return {"id": cid, "name": name, "week": week, "impressions": impr, "clicks": clicks, "cost": cost, "share": share,
            "lostBudget": lb, "lostRank": lr, "top": top, "absTop": abs_top}


# ── Weeks ───────────────────────────────────────────────────────────────────

def test_the_weeks_are_whole_monday_to_sunday_weeks_ending_before_today():
    w = m.weeks(TODAY)
    assert len(w) == 8 and w[-1] == (dt.date(2026, 10, 12), dt.date(2026, 10, 18)) and w[0][0] == dt.date(2026, 8, 24)
    assert all(a.weekday() == 0 and b.weekday() == 6 and (b - a).days == 6 for a, b in w)
    assert all(w[i][1] + dt.timedelta(days=1) == w[i + 1][0] for i in range(7))


@pytest.mark.parametrize("today,last", [(dt.date(2026, 10, 19), dt.date(2026, 10, 18)),      # Monday: yesterday was Sunday
                                        (dt.date(2026, 10, 18), dt.date(2026, 10, 11)),      # Sunday: this week is not whole yet
                                        (dt.date(2026, 10, 24), dt.date(2026, 10, 18))])
def test_the_last_week_never_includes_today_or_a_part_week(today, last):
    assert m.weeks(today)[-1][1] == last


# ── The brand rule ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,brand", [
    ("P2_Brand", True), ("Brand - Search", True), ("Branded Terms", True), ("SEARCH | brand | US", True), ("brand", True), ("Acme_Brand_Exact", True),
    ("P2_NonBrand_Core", False), ("Non-Brand Search", False), ("Non Brand", False), ("nonbrand", False), ("NB - Dentist", False), ("Generic Search", False),
    ("Competitor_Brand", False), ("Conquest brand", False), ("Brandon Dental", False), ("Search_Core", False), ("Unbranded", False), ("", False), (None, False)])
def test_the_brand_rule(name, brand):
    assert m.is_brand(name) is brand


def test_an_override_beats_the_name_either_way_and_is_marked():
    camps = [{"id": "1", "name": "Brand"}, {"id": "2", "name": "Core"}, {"id": 3, "name": "Brand Exact"}]
    brand, other = m.split_campaigns(camps, {"1": False, "2": True})
    assert [c["id"] for c in brand] == ["2", "3"] and [c["id"] for c in other] == ["1"]
    assert {c["id"]: c["byOverride"] for c in brand + other} == {"1": True, "2": True, "3": False}
    brand, other = m.split_campaigns(camps)
    assert [c["id"] for c in brand] == ["1", "3"] and not any(c["byOverride"] for c in brand + other)


# ── Putting shares together ─────────────────────────────────────────────────

def test_one_campaign_keeps_its_own_share():
    c = m.combine([row(NOW, impr=900, share=0.90, lb=0.04, lr=0.06)])
    assert c["share"] == 0.9 and c["eligible"] == 1000 and c["lostBudget"] == 0.04 and c["lostRank"] == 0.06 and c["measured"] == 900


def test_two_campaigns_are_weighted_by_the_searches_they_were_eligible_for_not_by_impressions():
    # 900 of 1000 eligible (90%) and 100 of 200 eligible (50%): 1000 / 1200, not the plain average of 90% and 50%
    c = m.combine([row(NOW, "1", impr=900, share=0.9, lb=0.0, lr=0.1), row(NOW, "2", impr=100, share=0.5, lb=0.5, lr=0.0)])
    assert c["eligible"] == 1200 and c["share"] == round(1000 / 1200, 4)
    assert c["lostBudget"] == round((1000 * 0.0 + 200 * 0.5) / 1200, 4) and c["lostRank"] == round((1000 * 0.1) / 1200, 4)


def test_a_row_without_a_share_is_unmeasured_and_not_a_zero():
    c = m.combine([row(NOW, "1", impr=900, share=0.9), row(NOW, "2", impr=500, share=None, lb=None, lr=None)])
    assert c["share"] == 0.9 and c["unmeasured"] == 500 and c["measured"] == 900 and c["impressions"] == 1400
    assert m.combine([row(NOW, impr=500, share=None)])["share"] is None
    assert m.combine([row(NOW, impr=500, share=0)])["share"] is None and m.combine([row(NOW, impr=0, share=0.9)])["unmeasured"] == 0
    assert m.combine([])["share"] is None


def test_a_missing_loss_is_left_out_of_that_average_only():
    c = m.combine([row(NOW, "1", impr=900, share=0.9, lb=None, lr=0.1), row(NOW, "2", impr=100, share=0.5, lb=0.5, lr=0.5)])
    assert c["lostBudget"] == 0.5 and c["lostRank"] == round((1000 * 0.1 + 200 * 0.5) / 1200, 4)


def test_string_numbers_from_the_api_are_read():
    assert m.combine([dict(row(NOW), impressions="1000", share="0.8", clicks="5", cost="1.5")])["share"] == 0.8


@pytest.mark.parametrize("lb,lr,want", [(0.06, 0.04, "budget"), (0.04, 0.06, "rank"), (0.05, 0.05, "both"), (0.059, 0.041, "both"), (0.0, 0.0, None), (None, None, None),
                                        (0.0, 0.1, "rank"), (0.1, None, "budget")])
def test_the_cause_needs_60_percent_of_the_loss_from_one_side(lb, lr, want):
    assert m.cause(lb, lr) == want


def test_pct_and_points():
    assert m.pct(0.9) == "90%" and m.pct(None) == "n/a" and m.pct(0.8234, 1) == "82.3%"
    assert m.points(-0.05) == "5 points" and m.points(0.01) == "1 point"


# ── One account ─────────────────────────────────────────────────────────────

def week_of(share, impr=1000, prev=None, **kw):
    rows = [row(NOW, impr=impr, share=share, **kw)]
    if prev is not None:
        rows.append(row(BEFORE, impr=impr, share=prev, **kw))
    return rows


def test_a_brand_above_the_threshold_is_ok_and_has_no_findings():
    a = m.analyse(week_of(0.95, prev=0.95), TODAY)
    assert a["status"] == "ok" and a["findings"] == [] and a["brand"]["share"] == 0.95 and a["delta"] == 0.0 and a["cause"] is None
    assert len(a["trend"]) == 8 and a["weekFrom"] == NOW and a["weekTo"] == "2026-10-18"


def test_exactly_the_threshold_is_not_below_and_one_hair_under_is():
    assert m.analyse(week_of(0.90), TODAY)["status"] == "ok"
    assert m.analyse(week_of(0.8999), TODAY)["status"] == "below"
    assert m.analyse(week_of(0.80), TODAY, threshold=0.75)["status"] == "ok"


def test_ten_points_under_is_high_and_a_hair_less_is_medium():
    high = m.analyse(week_of(0.80, lb=0.02, lr=0.18), TODAY)
    assert high["status"] == "below" and high["findings"][0]["level"] == "high" and high["cause"] == "rank"
    med = m.analyse(week_of(0.801, lb=0.02, lr=0.18), TODAY)
    assert med["findings"][0]["level"] == "medium"


def test_the_finding_names_the_figures_the_cause_and_one_next_step():
    f = m.analyse(week_of(0.70, lb=0.25, lr=0.05, prev=0.95), TODAY)["findings"]
    top = f[0]
    assert top["kind"] == "below" and "70%" in top["title"] and "90%" in top["title"] and "20 points under" in top["detail"]
    assert "25% lost to budget" in top["detail"] and "5% lost to rank" in top["detail"] and "budget" in top["nextStep"].lower()
    assert any(x["kind"] == "dropped" for x in f)


def test_a_week_with_fewer_than_100_eligible_searches_is_too_few_to_judge_at_exactly_100_it_is_judged():
    thin = m.analyse(week_of(0.50, impr=49), TODAY)                  # 49 / 0.5 = 98 eligible
    assert thin["status"] == "thin" and thin["findings"][0]["kind"] == "thin" and thin["findings"][0]["level"] == "info"
    assert m.analyse(week_of(0.50, impr=50), TODAY)["status"] == "below"    # exactly 100 eligible
    assert m.analyse(week_of(0.50, impr=51), TODAY)["status"] == "below"


def test_impressions_without_any_share_is_unmeasured_not_a_pass_or_a_fail():
    a = m.analyse(week_of(None, impr=800, lb=None, lr=None), TODAY)
    assert a["status"] == "unmeasured" and a["findings"][0]["kind"] == "unmeasured" and "800" in a["findings"][0]["detail"]


def test_no_brand_campaign_is_said_and_is_not_a_pass():
    a = m.analyse(week_of(0.99, name="Core Search"), TODAY)
    assert a["status"] == "none" and a["findings"][0]["kind"] == "none" and a["brandCampaigns"] == [] and a["otherCampaigns"][0]["name"] == "Core Search"
    forced = m.analyse(week_of(0.99, name="Core Search"), TODAY, overrides={"1": True})
    assert forced["status"] == "ok" and forced["brandCampaigns"][0]["byOverride"] is True


def test_non_brand_campaigns_do_not_count_toward_the_brand_share():
    rows = week_of(0.95) + [row(NOW, "2", "P2_NonBrand", impr=5000, share=0.30)]
    a = m.analyse(rows, TODAY)
    assert a["status"] == "ok" and a["brand"]["share"] == 0.95 and [c["id"] for c in a["otherCampaigns"]] == ["2"]


def test_the_change_on_the_week_before_and_the_drop_bar():
    exactly = m.analyse(week_of(0.90, prev=0.95), TODAY)             # 5 points: said even though still at the threshold
    assert exactly["delta"] == -0.05 and [f["kind"] for f in exactly["findings"]] == ["dropped"] and exactly["findings"][0]["level"] == "medium"
    just_under = m.analyse(week_of(0.91, prev=0.95), TODAY)
    assert just_under["delta"] == -0.04 and just_under["findings"] == []
    below_and_dropped = m.analyse(week_of(0.80, lb=0.1, lr=0.1, prev=0.95), TODAY)
    assert [f["kind"] for f in below_and_dropped["findings"]] == ["below", "dropped"] and below_and_dropped["findings"][1]["level"] == "low"


def test_no_change_is_claimed_when_the_week_before_cannot_be_judged():
    a = m.analyse(week_of(0.60, prev=0.95, impr=1000)[:1] + [row(BEFORE, impr=10, share=0.95)], TODAY)
    assert a["delta"] is None and not any(f["kind"] == "dropped" for f in a["findings"])
    assert m.analyse(week_of(0.60), TODAY)["delta"] is None


def test_a_single_brand_campaign_gets_no_second_finding_for_itself_but_several_do():
    one = m.analyse(week_of(0.70), TODAY)
    assert [f["kind"] for f in one["findings"]] == ["below"]
    rows = [row(NOW, "1", "Brand A", impr=1000, share=0.99), row(NOW, "2", "Brand B", impr=500, share=0.50, lb=0.5, lr=0.0)]
    two = m.analyse(rows, TODAY)
    kinds = [(f["kind"], f["level"]) for f in two["findings"]]
    assert ("campaign", "low") in kinds and two["brandCampaigns"][0]["name"] == "Brand B" and two["brandCampaigns"][0]["cause"] == "budget"
    assert any("Brand B is at 50%" == f["title"] for f in two["findings"])


def test_findings_are_ranked_high_first():
    rows = [row(NOW, "1", "Brand A", impr=1000, share=0.70, lb=0.1, lr=0.2), row(BEFORE, "1", "Brand A", impr=1000, share=0.99),
            row(NOW, "2", "Brand B", impr=500, share=None, lb=None, lr=None)]
    levels = [f["level"] for f in m.analyse(rows, TODAY)["findings"]]
    assert levels == sorted(levels, key=["high", "medium", "low", "info"].index) and levels[0] == "high"


def test_the_trend_has_a_point_for_each_of_eight_weeks_and_ignores_other_campaigns_rows_and_old_weeks():
    rows = week_of(0.9, prev=0.8) + [row("2026-08-24", impr=1000, share=0.7), row("2020-01-06", impr=1000, share=0.1)]
    t = m.analyse(rows, TODAY)["trend"]
    assert [p["share"] for p in t][0] == 0.7 and t[-1]["share"] == 0.9 and t[-2]["share"] == 0.8 and t[1]["share"] is None
