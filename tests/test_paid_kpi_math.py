"""KPI Monitoring: the rules (tracker/paid_kpi_math.py): windows, enough data, targets and where they come from, each rule and its levels."""
import datetime as dt

from tracker import paid_kpi_math as m

TODAY = dt.date(2026, 10, 22)


def days(cost=100.0, conv=1.0, impr=2000, clicks=200, value=0.0, first=1, last=14):
    """One row a day from `first` to `last` days before TODAY."""
    return [{"date": (TODAY - dt.timedelta(days=i)).isoformat(), "impr": impr, "clicks": clicks, "cost": cost, "conv": conv, "value": value} for i in range(first, last + 1)]


def camp(cid="1", name="Search Core", rows=None, **kw):
    base = {"id": cid, "name": name, "channel": "SEARCH", "primaryStatus": "ELIGIBLE", "reasons": [], "systemStatus": "ENABLED", "strategy": "MANUAL_CPC", "startDate": "2026-01-01", "days": rows if rows is not None else days()}
    base.update(kw)
    return base


def run(campaigns, config=None, learning_days=None):
    return m.analyse({"campaigns": campaigns, "currency": "USD"}, config=config or {}, today=TODAY, learning_days=learning_days)


def finds(campaigns, config=None, rule=None, learning_days=None):
    out = [f for i in run(campaigns, config, learning_days)["items"] for f in i["findings"]]
    return [f for f in out if rule is None or f["rule"] == rule]


def two_weeks(recent, before):
    """Rows for the last 7 days from `recent` (a dict of per-day values) and the 7 before from `before`."""
    return days(first=1, last=7, **recent) + days(first=8, last=14, **before)


# ── windows and enough data ─────────────────────────────────────────────────

def test_windows_are_the_seven_days_before_today_and_the_seven_before_those():
    rows = days(cost=10, first=0, last=0) + days(cost=1, first=1, last=7) + days(cost=2, first=8, last=14) + days(cost=99, first=15, last=15)
    recent, prior = m.windows(rows, TODAY)
    assert (recent["cost"], recent["days"]) == (7.0, 7)                       # today (cost 10) is not in it
    assert (prior["cost"], prior["days"]) == (14.0, 7)                        # nor is day 15 (cost 99)


def test_a_figure_needs_enough_behind_it():
    w = {"impr": 999, "clicks": 99, "cost": 100.0, "conv": 4.9, "value": 500.0}
    assert m.value_of("cpa", w) == (None, "only 4.9 conversions in the week (at least 5 are needed)")
    assert m.value_of("roas", w)[0] is None
    assert m.value_of("cvr", w) == (None, "only 99 clicks in the week (at least 100 are needed)")
    assert m.value_of("ctr", w) == (None, "only 999 impressions in the week (at least 1000 are needed)")
    ok = {"impr": 1000, "clicks": 100, "cost": 100.0, "conv": 5.0, "value": 400.0}
    assert m.value_of("cpa", ok) == (20.0, "")
    assert m.value_of("roas", ok) == (4.0, "")
    assert m.value_of("cvr", ok) == (0.05, "")
    assert m.value_of("ctr", ok) == (0.1, "")


def test_a_roas_without_a_recorded_value_is_not_judged():
    w = {"impr": 5000, "clicks": 500, "cost": 100.0, "conv": 9.0, "value": 0.0}
    assert m.value_of("roas", w) == (None, "no conversion value was recorded")


def test_one_conversion_is_written_in_the_singular():
    assert m.value_of("cpa", {"impr": 0, "clicks": 0, "cost": 0, "conv": 1.0, "value": 0})[1] == "only 1 conversion in the week (at least 5 are needed)"


# ── targets ─────────────────────────────────────────────────────────────────

def test_a_target_comes_from_the_campaign_then_the_account_then_the_bid_strategy():
    c = camp(targetCpa=30.0, targetRoas=5.0)
    cfg = {"targets": {"cpa": 40, "roas": 4, "cvr": 5, "ctr": 2}, "campaigns": {"1": {"cpa": 20}}}
    t = m.targets_for(c, cfg)
    assert t["cpa"] == {"value": 20.0, "source": "campaign"}
    assert t["roas"] == {"value": 4.0, "source": "account"}
    assert t["cvr"] == {"value": 0.05, "source": "account"}                   # saved as a percent, used as a fraction
    assert t["ctr"] == {"value": 0.02, "source": "account"}
    only = m.targets_for(c, {})
    assert only == {"cpa": {"value": 30.0, "source": "strategy"}, "roas": {"value": 5.0, "source": "strategy"}}
    assert m.targets_for(camp(), {}) == {}
    assert m.targets_for(camp(), {"campaigns": {"2": {"cpa": 9}}}) == {}      # another campaign's own target is not this one's


def test_a_zero_target_is_no_target():
    assert m.targets_for(camp(), {"targets": {"cpa": 0, "roas": "", "cvr": None}}) == {}


# ── cost per conversion and return on ad spend ──────────────────────────────

def cpa_rows(cpa_now, cpa_before, conv=10.0):
    return two_weeks({"cost": cpa_now * conv / 7.0, "conv": conv / 7.0}, {"cost": cpa_before * conv / 7.0, "conv": conv / 7.0})


def test_a_cpa_inside_the_tolerance_is_on_target():
    got = run([camp(rows=cpa_rows(60.0, 50.0))], {"targets": {"cpa": 50}})                 # 60 is exactly 20 percent over: not over the tolerance
    assert got["items"][0]["findings"] == []
    assert got["items"][0]["metrics"][0]["state"] == "on"


def test_a_cpa_over_the_tolerance_is_a_medium_finding():
    (f,) = finds([camp(rows=cpa_rows(61.0, 50.0))], {"targets": {"cpa": 50}})
    assert (f["rule"], f["level"], f["key"]) == ("cpa", "medium", "cpa:1")
    assert "costs $61.00 a conversion against a target of $50.00" in f["title"]
    assert f["facts"]["source"] == "account"
    assert "The 7 days before were off target too." not in f["detail"]
    assert f["evidence"][0] == "Last 7 days: $61.00 (target $50.00, saved for the account)"
    assert f["evidence"][1] == "The 7 days before: $50.00"


def test_a_cpa_half_again_over_for_two_weeks_is_high_and_for_one_week_is_medium():
    (sustained,) = finds([camp(rows=cpa_rows(75.0, 75.0))], {"targets": {"cpa": 50}})
    assert sustained["level"] == "high" and "The 7 days before were off target too." in sustained["detail"]
    (fresh,) = finds([camp(rows=cpa_rows(75.0, 50.0))], {"targets": {"cpa": 50}})
    assert fresh["level"] == "medium"


def test_a_cpa_just_under_half_again_over_is_medium_even_when_it_was_off_before():
    (f,) = finds([camp(rows=cpa_rows(74.0, 74.0))], {"targets": {"cpa": 50}})                  # 1.48 times: off two weeks running, but not yet half again
    assert f["level"] == "medium" and "The 7 days before were off target too." in f["detail"]


def test_a_cpa_twice_the_target_is_high_on_its_own():
    (f,) = finds([camp(rows=cpa_rows(100.0, 50.0))], {"targets": {"cpa": 50}})
    assert f["level"] == "high"
    (g,) = finds([camp(rows=cpa_rows(99.0, 50.0))], {"targets": {"cpa": 50}})
    assert g["level"] == "medium"


def test_the_tolerance_can_be_saved():
    cfg = {"targets": {"cpa": 50}, "tolerance": 50}
    assert finds([camp(rows=cpa_rows(74.0, 50.0))], cfg) == []
    assert [f["rule"] for f in finds([camp(rows=cpa_rows(76.0, 50.0))], cfg)] == ["cpa"]


def test_a_cpa_with_too_few_conversions_is_not_judged_and_says_why():
    rows = cpa_rows(500.0, 50.0, conv=4.0)
    got = run([camp(rows=rows)], {"targets": {"cpa": 50}})
    assert got["items"][0]["findings"] == []
    cpa = got["items"][0]["metrics"][0]
    assert cpa["state"] == "unknown" and cpa["why"].startswith("only 4 conversions")
    assert got["judged"] == 0 and got["notJudged"] == 1


def test_the_strategys_own_target_is_judged_when_nothing_is_saved():
    (f,) = finds([camp(rows=cpa_rows(100.0, 100.0), targetCpa=40.0)])
    assert f["rule"] == "cpa" and f["facts"]["source"] == "strategy" and "the campaign's own bid strategy target" in f["evidence"][0]


def test_a_roas_under_the_target_is_a_finding_by_the_same_levels():
    rows = two_weeks({"cost": 100.0, "conv": 2.0, "value": 300.0}, {"cost": 100.0, "conv": 2.0, "value": 300.0})          # 3.0x in both weeks
    (f,) = finds([camp(rows=rows)], {"targets": {"roas": 4}})
    assert (f["rule"], f["level"]) == ("roas", "medium")                      # 4 / 3 = 1.33: beyond the tolerance, short of 1.5
    assert "returns 3.00x against a target of 4.00x" in f["title"]
    rows = two_weeks({"cost": 100.0, "conv": 2.0, "value": 200.0}, {"cost": 100.0, "conv": 2.0, "value": 200.0})          # 2.0x: half the target
    (g,) = finds([camp(rows=rows)], {"targets": {"roas": 4}})
    assert g["level"] == "high"
    assert finds([camp(rows=two_weeks({"cost": 100.0, "conv": 2.0, "value": 330.0}, {"cost": 100.0, "conv": 2.0, "value": 330.0}))], {"targets": {"roas": 4}}) == []     # 3.3x is inside the tolerance


def test_a_zero_value_makes_roas_unjudged_not_off():
    rows = two_weeks({"cost": 100.0, "conv": 2.0, "value": 0.0}, {"cost": 100.0, "conv": 2.0, "value": 0.0})
    got = run([camp(rows=rows)], {"targets": {"roas": 4}})
    assert got["items"][0]["findings"] == []
    assert got["items"][0]["metrics"][1]["why"] == "no conversion value was recorded"


# ── conversion rate and click-through rate ──────────────────────────────────

def test_a_cvr_under_the_minimum_is_low_and_far_under_is_medium():
    near = two_weeks({"cost": 100.0, "conv": 1.0, "clicks": 100}, {"cost": 100.0, "conv": 1.0, "clicks": 100})              # 1 percent against 1.5
    (f,) = finds([camp(rows=near)], {"targets": {"cvr": 1.5}})
    assert (f["rule"], f["level"]) == ("cvr", "low")
    assert "Conversion rate is at 1.0% over the last 7 days; the lowest wanted is 1.5%." == f["detail"]
    far = two_weeks({"cost": 100.0, "conv": 0.5, "clicks": 100}, {"cost": 100.0, "conv": 0.5, "clicks": 100})               # 0.5 percent against 1.5: a third
    (g,) = finds([camp(rows=far)], {"targets": {"cvr": 1.5}})
    assert g["level"] == "medium"
    edge = two_weeks({"cost": 100.0, "conv": 0.9, "clicks": 100}, {"cost": 100.0, "conv": 0.9, "clicks": 100})              # 0.9 percent against 1.5: exactly 60 percent
    (h,) = finds([camp(rows=edge)], {"targets": {"cvr": 1.5}})
    assert h["level"] == "medium"


def test_a_ctr_under_the_minimum_is_found_and_one_with_too_few_impressions_is_not():
    low = two_weeks({"impr": 1000, "clicks": 20}, {"impr": 1000, "clicks": 20})                                              # 2 percent against 3
    (f,) = finds([camp(rows=low)], {"targets": {"ctr": 3}})
    assert (f["rule"], f["level"]) == ("ctr", "low")
    few = two_weeks({"impr": 100, "clicks": 1}, {"impr": 100, "clicks": 1})
    assert finds([camp(rows=few)], {"targets": {"ctr": 3}}) == []


# ── stopped converting ──────────────────────────────────────────────────────

def test_a_week_of_spend_with_no_conversion_is_high_when_it_is_twice_the_target():
    rows = two_weeks({"cost": 15.0, "conv": 0.0}, {"cost": 15.0, "conv": 0.0})                                                # 105 in the week, target 50
    (f,) = finds([camp(rows=rows)], {"targets": {"cpa": 50}})
    assert (f["rule"], f["level"]) == ("no_conv", "high")
    assert "spent $105 in the last 7 days, 2.1 times its target cost per conversion ($50.00), and got no conversion" in f["detail"]
    assert finds([camp(rows=two_weeks({"cost": 14.0, "conv": 0.0}, {"cost": 14.0, "conv": 0.0}))], {"targets": {"cpa": 50}}) == []    # 98 is under twice the target


def test_the_edges_of_stopped_converting_are_inclusive():
    exactly_two = two_weeks({"cost": 10.0, "conv": 0.0}, {"cost": 10.0, "conv": 0.0})                                          # 70 in the week, twice the target of 35
    assert [f["rule"] for f in finds([camp(rows=exactly_two)], {"targets": {"cpa": 35}})] == ["no_conv"]
    five = two_weeks({"cost": 10.0, "conv": 0.0}, {"cost": 10.0, "conv": 5.0 / 7.0})                                           # exactly 5 conversions the week before
    assert [f["rule"] for f in finds([camp(rows=five)])] == ["no_conv"]
    half = two_weeks({"cost": 5.0, "conv": 0.0}, {"cost": 10.0, "conv": 1.0})                                                   # spend exactly half of last week's
    assert [f["rule"] for f in finds([camp(rows=half)])] == ["no_conv"]


def test_a_campaign_that_converted_and_stopped_is_found_without_any_target():
    rows = two_weeks({"cost": 10.0, "conv": 0.0}, {"cost": 10.0, "conv": 1.0})                                                # 7 conversions the week before
    (f,) = finds([camp(rows=rows)])
    assert f["rule"] == "no_conv" and f["title"] == "Search Core stopped converting"
    assert "converted 7 times the week before and none in the last 7 days" in f["detail"]
    low_spend = two_weeks({"cost": 4.0, "conv": 0.0}, {"cost": 10.0, "conv": 1.0})                                            # the spend fell to 40 percent: it is not "went on"
    assert finds([camp(rows=low_spend)]) == []
    few = two_weeks({"cost": 10.0, "conv": 0.0}, {"cost": 10.0, "conv": 0.5})                                                  # 3.5 conversions the week before is not enough to say it stopped
    assert finds([camp(rows=few)]) == []


def test_no_impressions_is_not_a_conversion_problem():
    rows = two_weeks({"cost": 0.0, "conv": 0.0, "impr": 0, "clicks": 0}, {"cost": 10.0, "conv": 1.0})
    assert [f["rule"] for f in finds([camp(rows=rows)])] == ["not_delivering"]


def test_stopped_converting_replaces_the_conversion_rate_finding():
    rows = two_weeks({"cost": 10.0, "conv": 0.0}, {"cost": 10.0, "conv": 1.0})
    got = [f["rule"] for f in finds([camp(rows=rows)], {"targets": {"cvr": 2}})]
    assert got == ["no_conv"]


# ── delivery ────────────────────────────────────────────────────────────────

def test_a_campaign_google_calls_not_eligible_is_high_with_its_reasons():
    (f,) = finds([camp(primaryStatus="NOT_ELIGIBLE", reasons=["NO_ACTIVE_AD_GROUP_ADS", "BIDDING_STRATEGY_PENDING"])])
    assert (f["rule"], f["level"]) == ("not_delivering", "high")
    assert f["detail"] == "Google Ads reports Search Core as not eligible to show: an ad group has no live ad; a bid strategy change is not in effect yet."
    assert f["evidence"] == ["Status: not eligible", "an ad group has no live ad", "a bid strategy change is not in effect yet"]


def test_misconfigured_and_ended_campaigns_are_high_too_and_pending_is_low():
    assert finds([camp(primaryStatus="MISCONFIGURED")])[0]["level"] == "high"
    assert "ended (its end date has passed)" in finds([camp(primaryStatus="ENDED")])[0]["detail"]
    (f,) = finds([camp(primaryStatus="PENDING")])
    assert (f["level"], f["title"]) == ("low", "Search Core has not started")


def test_a_campaign_not_delivering_hides_its_target_findings():
    rows = cpa_rows(500.0, 500.0)
    got = [f["rule"] for f in finds([camp(rows=rows, primaryStatus="NOT_ELIGIBLE")], {"targets": {"cpa": 50}})]
    assert got == ["not_delivering"]                                          # last week's numbers say nothing about a campaign that cannot show


def test_a_blocking_reason_on_an_eligible_campaign_is_medium():
    (f,) = finds([camp(reasons=["MOST_ADS_UNDER_REVIEW"])])
    assert (f["rule"], f["level"], f["detail"]) == ("not_delivering", "medium", "Google Ads says most of its ads are under review.")
    assert finds([camp(reasons=["MOST_ADS_UNDER_REVIEW"], primaryStatus="PAUSED")]) == []


def test_no_impressions_for_a_week_is_medium_unless_the_campaign_is_new_or_off():
    quiet = two_weeks({"impr": 0, "clicks": 0, "cost": 0.0, "conv": 0.0}, {"impr": 0, "clicks": 0, "cost": 0.0, "conv": 0.0})
    (f,) = finds([camp(rows=quiet)])
    assert (f["rule"], f["level"], f["evidence"]) == ("not_delivering", "medium", ["Impressions in the last 7 days: 0"])
    assert finds([camp(rows=quiet, startDate=(TODAY - dt.timedelta(days=2)).isoformat())]) == []                    # started two days ago
    assert len(finds([camp(rows=quiet, startDate=(TODAY - dt.timedelta(days=3)).isoformat())])) == 1                 # three days is old enough
    assert finds([camp(rows=quiet, primaryStatus="PAUSED")]) == []
    assert len(finds([camp(rows=quiet, startDate="")])) == 1                                                          # no start date read: judged as old


# ── bidding ─────────────────────────────────────────────────────────────────

def test_a_misconfigured_bid_strategy_is_high_and_a_cpc_limit_is_medium():
    (f,) = finds([camp(systemStatus="MISCONFIGURED_CONVERSION_TYPES")])
    assert (f["rule"], f["level"]) == ("bidding", "high")
    assert f["detail"] == "Google reports the bid strategy status misconfigured conversion types, so the strategy cannot bid properly."
    (g,) = finds([camp(systemStatus="LIMITED_BY_CPC_BID_CEILING")])
    assert (g["level"], g["detail"]) == ("medium", "Google reports that a maximum CPC limit is stopping the strategy from bidding what it needs.")
    (h,) = finds([camp(systemStatus="LIMITED_BY_CPC_BID_FLOOR")])
    assert "minimum CPC limit" in h["detail"]
    assert finds([camp(systemStatus="ENABLED")]) == []


# ── learning ────────────────────────────────────────────────────────────────

def test_a_learning_campaign_is_low_until_it_has_been_learning_for_two_weeks():
    (f,) = finds([camp(systemStatus="LEARNING_NEW")], learning_days={"1": 13})
    assert (f["rule"], f["level"], f["title"]) == ("learning", "low", "Search Core is still learning")
    (g,) = finds([camp(systemStatus="LEARNING_NEW")], learning_days={"1": 14})
    assert (g["level"], g["title"], g["detail"]) == ("medium", "Search Core is stuck in learning", "It has been learning for 14 days.")
    assert g["evidence"] == ["Bid strategy status: learning new", "Seen learning for 14 days"]
    assert finds([camp(primaryStatus="LEARNING")])[0]["rule"] == "learning"
    assert finds([camp(systemStatus="MULTIPLE_LEARNING")])[0]["rule"] == "learning"


def test_a_campaign_limited_by_data_is_medium():
    (f,) = finds([camp(systemStatus="LIMITED_BY_DATA")])
    assert (f["rule"], f["level"], f["title"]) == ("learning", "medium", "Search Core is limited by data")


# ── budget ──────────────────────────────────────────────────────────────────

def test_a_budget_limit_on_a_campaign_on_target_is_medium():
    (f,) = finds([camp(rows=cpa_rows(45.0, 45.0), reasons=["BUDGET_CONSTRAINED"], lostBudget=0.35)], {"targets": {"cpa": 50}})
    assert (f["rule"], f["level"]) == ("budget", "medium")
    assert f["detail"] == "Search Core is held back by its budget and is on its target, so more budget would buy more of what works."
    assert f["evidence"] == ["Google marks it limited by budget", "Impressions lost to the budget in the last 7 days: 35%"]


def test_a_budget_limit_on_a_campaign_off_target_is_low_and_says_so():
    got = finds([camp(rows=cpa_rows(70.0, 70.0), reasons=["BUDGET_CONSTRAINED"])], {"targets": {"cpa": 50}}, rule="budget")
    assert [(f["level"], f["detail"]) for f in got] == [("low", "Search Core is held back by its budget, but it is not on its target, so more budget would mostly buy more of the same.")]


def test_a_budget_limit_with_no_target_is_low():
    (f,) = finds([camp(reasons=["BUDGET_CONSTRAINED"])])
    assert (f["level"], f["detail"]) == ("low", "Search Core is held back by its budget.")


def test_lost_impressions_alone_make_a_budget_finding_from_a_fifth():
    assert finds([camp(lostBudget=0.19)]) == []
    (f,) = finds([camp(lostBudget=0.2)])
    assert f["rule"] == "budget" and f["evidence"] == ["Impressions lost to the budget in the last 7 days: 20%"]
    assert finds([camp(systemStatus="LIMITED_BY_BUDGET")])[0]["rule"] == "budget"


# ── muted campaigns, the account ────────────────────────────────────────────

def test_a_muted_campaign_has_no_findings_and_is_not_counted():
    got = run([camp(primaryStatus="NOT_ELIGIBLE"), camp("2", "Other")], {"muted": {"1": "a test"}})
    by = {i["id"]: i for i in got["items"]}
    assert by["1"]["findings"] == [] and by["1"]["muted"] is True and by["1"]["mutedWhy"] == "a test"
    assert got["muted"] == 1 and got["findingCount"] == 0 and got["status"] == "clean"


def test_the_account_answer_counts_what_it_found():
    campaigns = [camp("1", "Over", rows=cpa_rows(100.0, 100.0)), camp("2", "Fine", rows=cpa_rows(40.0, 40.0)), camp("3", "Learning", systemStatus="LEARNING_NEW", rows=cpa_rows(40.0, 40.0)),
                 camp("4", "Few", rows=cpa_rows(40.0, 40.0, conv=2.0))]
    got = run(campaigns, {"targets": {"cpa": 50}})
    assert (got["status"], got["campaigns"]) == ("fix", 4)
    assert got["counts"] == {"high": 1, "medium": 0, "low": 1}
    assert (got["judged"], got["offTarget"], got["onTarget"], got["notJudged"], got["withoutTarget"]) == (3, 1, 2, 1, 0)
    assert [i["name"] for i in got["items"]][:2] == ["Over", "Learning"]                   # the worst first
    assert [(b["rule"], b["findings"]) for b in got["byRule"]] == [("cpa", 1), ("learning", 1)]
    assert got["targetsSet"] is True


def test_with_no_target_at_all_the_account_says_so():
    got = run([camp()])
    assert got["targetsSet"] is False and got["withoutTarget"] == 1 and got["judged"] == 0
    assert got["status"] == "clean"


def test_an_account_with_only_low_findings_is_check():
    assert run([camp(systemStatus="LEARNING_NEW")])["status"] == "check"


def test_the_headline_uses_the_accounts_own_targets_only():
    campaigns = [camp("1", rows=cpa_rows(100.0, 100.0), targetCpa=10.0), camp("2", rows=cpa_rows(100.0, 100.0))]
    h = run(campaigns, {"targets": {"cpa": 50}})["headline"]
    cpa = h["metrics"][0]
    assert (cpa["state"], cpa["value"], cpa["target"], cpa["source"]) == ("off", 100.0, 50.0, "account")
    assert run(campaigns)["headline"]["metrics"][0]["state"] == "none"                     # a bid strategy's target is a campaign's, not the account's
    assert h["recent"]["conv"] == 20.0 and h["recent"]["cost"] == 2000.0


def test_findings_are_worst_first_then_in_rule_order():
    rows = two_weeks({"cost": 15.0, "conv": 0.0}, {"cost": 15.0, "conv": 0.0})
    c = camp(rows=rows, systemStatus="LIMITED_BY_CPC_BID_FLOOR", lostBudget=0.5)
    got = [(f["rule"], f["level"]) for f in finds([c], {"targets": {"cpa": 50}})]
    assert got == [("no_conv", "high"), ("bidding", "medium"), ("budget", "low")]


def test_a_campaign_with_no_days_does_not_break_anything():
    got = run([camp(rows=[])], {"targets": {"cpa": 50}})
    assert got["items"][0]["recent"]["cost"] == 0.0
    assert [f["rule"] for f in got["items"][0]["findings"]] == ["not_delivering"]           # enabled, old, and no impressions at all
