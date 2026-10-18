"""Cross-Channel & Attribution Analysis: the arithmetic, each function against a worked example."""
import datetime as dt
import math

import pytest

from tracker import paid_xchannel_math as m


def shape(days, campaigns, rows):
    """A stored-days shape: campaigns [(id, type)], rows [(campaign index, day index, impr, clicks, cost, conv, value, allconv, allvalue, view)]."""
    return {"days": days, "campaigns": [{"id": c, "type": t, "name": c} for c, t in campaigns], "rows": [list(r) for r in rows]}


DAYS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]


def google_shape():
    return shape(DAYS, [("1", "SEARCH"), ("2", "PERFORMANCE_MAX")], [
        (0, 0, 1000, 100, 200.0, 10, 1000.0, 12, 1100.0, 3), (0, 1, 1000, 100, 200.0, 10, 1000.0, 12, 1100.0, 3),
        (1, 1, 500, 50, 100.0, 5, 400.0, 6, 450.0, 1), (0, 3, 1000, 100, 200.0, 10, 1000.0, 12, 1100.0, 0)])


# ── periods ─────────────────────────────────────────────────────────────────

def test_a_preset_ends_yesterday_and_the_previous_period_is_the_same_length_straight_before():
    p = m.periods("30", dt.date(2026, 10, 18))
    assert p["current"] == ("2026-09-18", "2026-10-17") and p["days"] == 30
    assert p["previous"] == ("2026-08-19", "2026-09-17")


def test_last_month_is_the_whole_calendar_month_and_the_previous_period_has_its_length():
    p = m.periods("last_month", dt.date(2026, 10, 18))
    assert p["current"] == ("2026-09-01", "2026-09-30") and p["days"] == 30
    assert p["previous"] == ("2026-08-02", "2026-08-31")


def test_month_to_date_ends_yesterday_and_refuses_the_first_of_the_month():
    p = m.periods("mtd", dt.date(2026, 10, 18))
    assert p["current"] == ("2026-10-01", "2026-10-17") and p["days"] == 17
    with pytest.raises(m.BadRequest):
        m.periods("mtd", dt.date(2026, 10, 1))


def test_an_unknown_range_is_refused_and_none_means_thirty_days():
    with pytest.raises(m.BadRequest):
        m.periods("365", dt.date(2026, 10, 18))
    assert m.periods(None, dt.date(2026, 10, 18))["days"] == 30


# ── totals ──────────────────────────────────────────────────────────────────

def test_totals_add_the_days_in_range_and_split_them_by_campaign_type():
    t = m.totals(google_shape(), "2026-09-01", "2026-09-02", "primary", "google")
    assert t["all"]["cost"] == 500.0 and t["all"]["conv"] == 25 and t["all"]["value"] == 2400.0 and t["all"]["clicks"] == 250
    assert t["types"]["SEARCH"]["cost"] == 400.0 and t["types"]["SEARCH"]["label"] == "Search" and t["types"]["SEARCH"]["role"] == "capture"
    assert t["types"]["PERFORMANCE_MAX"]["conv"] == 5 and t["types"]["PERFORMANCE_MAX"]["role"] == "mixed"


def test_a_day_outside_the_range_is_left_out():
    t = m.totals(google_shape(), "2026-09-04", "2026-09-04", "primary", "google")
    assert t["all"]["cost"] == 200.0 and set(t["types"]) == {"SEARCH"}


def test_the_all_measure_reads_the_other_conversion_columns():
    t = m.totals(google_shape(), "2026-09-01", "2026-09-04", "all", "google")
    assert t["all"]["conv"] == 12 + 12 + 6 + 12 and t["all"]["value"] == 1100 * 3 + 450


def test_view_through_conversions_are_kept_apart_from_conversions():
    t = m.totals(google_shape(), "2026-09-01", "2026-09-04", "primary", "google")
    assert t["all"]["view"] == 7 and t["all"]["conv"] == 35


def test_a_short_row_reads_the_missing_columns_as_zero():
    s = shape(DAYS, [("1", "SEARCH")], [(0, 0, 10, 1, 5.0, 1, 9.0)])
    t = m.totals(s, "2026-09-01", "2026-09-04", "all", "google")
    assert t["all"]["conv"] == 0 and t["all"]["cost"] == 5.0


def test_meta_types_are_named_by_objective_and_awareness_creates_demand():
    s = shape(DAYS, [("9", "META_AWARENESS"), ("8", "META_LEADS")], [(0, 0, 5000, 40, 100.0, 0, 0.0), (1, 0, 3000, 90, 150.0, 9, 0.0)])
    t = m.totals(s, "2026-09-01", "2026-09-01", "primary", "meta")
    assert t["types"]["META_AWARENESS"]["role"] == "create" and t["types"]["META_LEADS"]["label"] == "Leads" and t["types"]["META_LEADS"]["role"] == "mixed"


def test_an_unknown_type_still_gets_a_label_and_the_mixed_role():
    assert m.type_of("google", "SOMETHING_NEW") == ("SOMETHING_NEW", "Something New", "mixed")
    assert m.type_of("google", None)[0] == "OTHER"


def test_daily_adds_the_campaigns_of_each_day():
    d = m.daily(google_shape(), "primary")
    assert d["cost"] == [200.0, 300.0, 0.0, 200.0] and d["conv"] == [10, 15, 0, 10]


# ── figures, change, shares ─────────────────────────────────────────────────

def test_figures_are_ratios_of_totals_and_none_without_a_denominator():
    f = m.figures({"impr": 1000, "clicks": 50, "cost": 200.0, "conv": 10, "value": 800.0})
    assert f["ctr"] == pytest.approx(5.0) and f["cpc"] == pytest.approx(4.0) and f["cvr"] == pytest.approx(20.0)
    assert f["cpa"] == pytest.approx(20.0) and f["roas"] == pytest.approx(4.0)
    z = m.figures(m.blank())
    assert z["ctr"] is None and z["cpa"] is None and z["roas"] is None and z["cvr"] is None and z["cpc"] is None


def test_change_is_relative_and_none_without_a_base():
    assert m.change(120, 100) == pytest.approx(20.0) and m.change(80, 100) == pytest.approx(-20.0)
    assert m.change(5, 0) is None and m.change(None, 5) is None and m.change(5, None) is None


def test_shares_add_to_a_hundred_and_are_none_when_nothing_was_spent():
    s = m.shares({"google": {"cost": 600, "conv": 30, "value": 0}, "meta": {"cost": 400, "conv": 10, "value": 0}})
    assert s["google"]["cost"] == pytest.approx(60) and s["meta"]["cost"] == pytest.approx(40)
    assert s["google"]["conv"] == pytest.approx(75) and s["meta"]["conv"] == pytest.approx(25)
    assert s["google"]["value"] is None


# ── what moved the blended cost per conversion ──────────────────────────────

def parts(g_cost, g_conv, m_cost, m_conv):
    return {"google": dict(m.blank(), cost=g_cost, conv=g_conv), "meta": dict(m.blank(), cost=m_cost, conv=m_conv)}


def test_the_mix_and_the_rates_add_up_to_the_whole_change():
    prev, cur = parts(1000, 50, 1000, 20), parts(1000, 40, 1400, 35)
    d = m.decompose_cpa(cur, prev)
    assert d["ok"]
    assert d["from"] == pytest.approx(2000 / 70) and d["to"] == pytest.approx(2400 / 75)
    assert d["mix"] + d["rateTotal"] == pytest.approx(d["change"])
    assert d["change"] == pytest.approx(2400 / 75 - 2000 / 70)


def test_the_split_matches_a_hand_worked_case():
    # google cost per conversion 20 -> 20 (no rate change), meta 50 -> 50; conversions move from google to meta: pure mix
    prev, cur = parts(1000, 50, 1000, 20), parts(600, 30, 1500, 30)
    d = m.decompose_cpa(cur, prev)
    assert d["rateTotal"] == pytest.approx(0.0) and d["mix"] == pytest.approx(d["change"]) and d["mix"] > 0
    assert d["mixShare"]["meta"]["before"] == pytest.approx(20 / 70 * 100) and d["mixShare"]["meta"]["after"] == pytest.approx(50)


def test_a_pure_rate_change_has_no_mix():
    prev, cur = parts(1000, 50, 1000, 50), parts(1000, 50, 1500, 50)
    d = m.decompose_cpa(cur, prev)
    assert d["mix"] == pytest.approx(0.0) and d["rate"]["google"] == pytest.approx(0.0) and d["rate"]["meta"] == pytest.approx(0.5 * 10)


def test_a_platform_with_no_conversions_means_nothing_is_split():
    d = m.decompose_cpa(parts(1000, 50, 1000, 0), parts(1000, 50, 1000, 20))
    assert d["ok"] is False and "Meta Ads" in d["why"]


def test_one_platform_alone_has_no_mix():
    cur = {"google": dict(m.blank(), cost=100, conv=5)}
    assert m.decompose_cpa(cur, cur)["ok"] is False
    cur = parts(100, 5, 0, 0)
    assert m.decompose_cpa(cur, cur)["ok"] is False


# ── weeks ───────────────────────────────────────────────────────────────────

def week_days(first, n):
    d0 = dt.date.fromisoformat(first)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def test_only_whole_monday_to_sunday_weeks_inside_the_range_count():
    days = week_days("2026-09-02", 20)                                      # starts on a Wednesday
    d = {"days": days, "cost": [10.0] * 20, "conv": [1.0] * 20, "value": [5.0] * 20}
    w = m.weekly(d, days[0], days[-1])
    assert [x["start"] for x in w] == ["2026-09-07", "2026-09-14"]
    assert w[0]["cost"] == 70.0 and w[0]["conv"] == 7.0
    assert m.weekly(d, "2026-09-08", days[-1])[0]["start"] == "2026-09-14"   # a range that starts mid-week drops that week


def test_a_range_shorter_than_a_week_has_no_weeks():
    days = week_days("2026-09-07", 5)
    assert m.weekly({"days": days, "cost": [1.0] * 5, "conv": [1.0] * 5, "value": [0.0] * 5}, days[0], days[-1]) == []


# ── the link between platforms ──────────────────────────────────────────────

def test_pearson_matches_known_cases():
    assert m.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert m.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert m.pearson([1, 2, 3, 4], [5, 5, 5, 5]) is None and m.pearson([1, 2], [1, 2]) is None


def test_the_p_value_matches_the_textbook_and_grows_with_fewer_pairs():
    assert m.p_of(0.5, 30) == pytest.approx(0.0053, abs=0.001)
    assert m.p_of(0.0, 30) == pytest.approx(1.0)
    assert m.p_of(0.5, 12) > m.p_of(0.5, 30)
    assert m.p_of(0.5, 3) is None and m.p_of(None, 30) is None
    assert m.p_of(1.0, 30) < 1e-6


def weeks_of(cost, conv):
    return [{"start": "w%d" % i, "cost": c, "conv": v, "value": 0.0} for i, (c, v) in enumerate(zip(cost, conv))]


def test_it_will_not_look_for_a_link_in_too_few_weeks():
    w = {"google": weeks_of([10] * 8, [1] * 8), "meta": weeks_of([10] * 8, [1] * 8)}
    r = m.links(w)
    assert r["ok"] is False and r["weeks"] == 8 and "11 whole weeks" in r["why"] and "90 days" in r["why"]


def test_it_will_not_look_for_a_link_with_one_platform():
    assert m.links({"google": weeks_of([10] * 20, [1] * 20)})["ok"] is False


def test_a_planted_link_is_found_and_a_noise_series_is_not():
    # google conversions follow meta spend in the same week; meta conversions are unrelated to google spend
    meta_cost = [100, 140, 90, 160, 120, 180, 110, 170, 95, 150, 130, 175, 105, 165]
    google_conv = [50 * (1 + 0.0) for _ in range(14)]
    google_conv = [30 + 0.4 * c for c in meta_cost]
    google_cost = [200, 210, 205, 190, 215, 200, 195, 210, 205, 200, 190, 215, 205, 200]
    meta_conv = [20, 25, 19, 27, 21, 18, 26, 22, 24, 17, 23, 28, 20, 25]
    r = m.links({"google": weeks_of(google_cost, google_conv), "meta": weeks_of(meta_cost, meta_conv)})
    assert r["ok"] and r["weeks"] == 14
    by = {(t["spend"], t["conversions"], t["lag"]): t for t in r["tests"]}
    planted = by[("meta", "google", 0)]
    assert planted["reads"] == "together" and planted["r"] > 0.9 and planted["p"] < m.LINK_ALPHA and planted["n"] == 13
    assert by[("google", "meta", 0)]["reads"] == "none"


def test_a_negative_link_reads_opposite():
    meta_cost = [100, 140, 90, 160, 120, 180, 110, 170, 95, 150, 130, 175, 105, 165]
    google_conv = [200 - 0.5 * c for c in meta_cost]
    flat = [10.0] * 14
    r = m.links({"google": weeks_of(flat, google_conv), "meta": weeks_of(meta_cost, flat)})
    assert {(t["spend"], t["lag"]): t["reads"] for t in r["tests"]}[("meta", 0)] == "opposite"


def test_a_weak_correlation_is_never_reported_as_a_link():
    meta_cost = [100, 140, 90, 160, 120, 180, 110, 170, 95, 150, 130, 175]
    google_conv = [42, 40, 45, 39, 44, 41, 40, 46, 43, 39, 45, 41]
    r = m.links({"google": weeks_of([10.0] * 12, google_conv), "meta": weeks_of(meta_cost, [5.0] * 12)})
    assert all(t["reads"] == "none" for t in r["tests"])


def test_weeks_with_nothing_the_week_before_are_skipped_not_divided_by_zero():
    cost = [0, 0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    r = m.links({"google": weeks_of(cost, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]), "meta": weeks_of(cost, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])})
    assert r["ok"]
    assert all(t["n"] <= 10 for t in r["tests"])


# ── the person's own numbers ────────────────────────────────────────────────

def test_nothing_typed_means_no_reconciliation():
    assert m.reconcile(parts(1000, 50, 1000, 20)) is None
    assert m.reconcile(parts(1000, 50, 1000, 20), "", "") is None


def test_platforms_that_claim_more_than_happened_are_scaled_down_by_the_same_factor():
    r = m.reconcile(parts(1000, 50, 1000, 50), actual_conv=80)
    c = r["conversions"]
    assert c["over"] is True and c["claimed"] == 100 and c["excess"] == 20 and c["ratio"] == pytest.approx(1.25)
    assert c["scale"] == pytest.approx(0.8)
    assert c["perPlatform"]["google"]["scaled"] == pytest.approx(40) and c["perPlatform"]["google"]["cpa"] == pytest.approx(25.0)
    assert r["blendedCpa"] == pytest.approx(2000 / 80)


def test_platforms_that_claim_less_than_happened_are_not_scaled_and_overlap_is_not_asserted():
    r = m.reconcile(parts(1000, 30, 1000, 20), actual_conv=80)
    c = r["conversions"]
    assert c["over"] is False and c["scale"] == 1.0 and c["excess"] == 0.0
    assert c["perPlatform"]["meta"]["cpa"] == pytest.approx(50.0)
    assert r["blendedCpa"] == pytest.approx(25.0)


def test_revenue_is_reconciled_the_same_way():
    p = {"google": dict(m.blank(), cost=1000, conv=10, value=6000), "meta": dict(m.blank(), cost=1000, conv=10, value=4000)}
    r = m.reconcile(p, actual_value=8000)
    v = r["value"]
    assert v["over"] and v["scale"] == pytest.approx(0.8) and v["perPlatform"]["google"]["roas"] == pytest.approx(6000 * 0.8 / 1000)
    assert r["blendedRoas"] == pytest.approx(4.0)
    assert r["conversions"] is None


def test_zero_or_negative_actuals_are_ignored():
    r = m.reconcile(parts(1000, 50, 1000, 20), actual_conv=0, actual_value=-5)
    assert r["conversions"] is None and r["value"] is None


def test_zero_spend_gives_no_roas_rather_than_dividing():
    p = {"google": dict(m.blank(), cost=0, conv=0, value=0), "meta": dict(m.blank(), cost=0, conv=0, value=0)}
    r = m.reconcile(p, actual_conv=10, actual_value=100)
    assert r["blendedRoas"] is None and r["conversions"]["perPlatform"]["google"]["cpa"] is None


# ── the bars a link has to clear ────────────────────────────────────────────

def _lcg(seed, n):
    out, x = [], seed
    for _ in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        out.append(x / 2 ** 31)
    return out


def _meta_to_google(n_weeks, mix, seed):
    """Weeks where google conversions follow meta spend by `mix` and noise by the rest: deterministic."""
    spend = [50 + 100 * v for v in _lcg(seed, n_weeks)]
    noise = _lcg(seed + 1, n_weeks)
    conv = [20 + 60 * (mix * (s - 50) / 100 + (1 - mix) * z) for s, z in zip(spend, noise)]
    return {"google": weeks_of([10.0] * n_weeks, conv), "meta": weeks_of(spend, [5.0] * n_weeks)}


def _first(r, spend="meta", conv="google", lag=0):
    return next(t for t in r["tests"] if t["spend"] == spend and t["conversions"] == conv and t["lag"] == lag)


def test_a_link_that_is_significant_but_weak_in_size_is_not_reported():
    # a long history can make a small correlation significant; the size bar still holds it back
    for seed in range(1, 200):
        r = m.links(_meta_to_google(80, 0.35, seed))
        t = _first(r)
        if t["p"] is not None and t["p"] < m.LINK_ALPHA and abs(t["r"]) < m.LINK_R:
            assert t["reads"] == "none"
            return
    pytest.fail("no seed gave a significant but small correlation")


def test_a_link_that_is_large_but_not_significant_is_not_reported():
    # with few pairs even a correlation above the size bar can be chance
    for seed in range(1, 400):
        r = m.links(_meta_to_google(11, 0.6, seed))
        t = _first(r)
        if t["p"] is not None and t["p"] >= m.LINK_ALPHA and abs(t["r"]) >= m.LINK_R:
            assert t["reads"] == "none" and t["enough"] is True
            return
    pytest.fail("no seed gave a large but not significant correlation")


def test_exactly_ten_weeks_is_not_enough_and_eleven_is():
    ten = _meta_to_google(10, 0.9, 3)
    assert m.links(ten)["ok"] is False and "11 whole weeks" in m.links(ten)["why"]
    assert m.links(_meta_to_google(11, 0.9, 3))["ok"] is True


def test_weeks_that_end_after_the_range_are_not_counted():
    days = week_days("2026-09-07", 21)                                       # three Monday-first weeks
    d = {"days": days, "cost": [1.0] * 21, "conv": [1.0] * 21, "value": [0.0] * 21}
    assert len(m.weekly(d, days[0], days[-1])) == 3
    assert len(m.weekly(d, days[0], days[-2])) == 2                          # the range stops a day short of the third week
