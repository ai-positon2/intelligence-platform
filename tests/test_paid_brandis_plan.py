"""Brand IS Monitor: the fix plan chosen and sized from what was read (tracker/paid_brandis_plan.py)."""
import datetime as dt

import pytest

from tracker import paid_brandis_math as m
from tracker import paid_brandis_plan as p

TODAY = dt.date(2026, 10, 20)
NOW, BEFORE = "2026-10-12", "2026-10-05"


def row(week=NOW, cid="1", name="P2_Brand", impr=1000, share=0.70, lb=0.25, lr=0.05, clicks=1000, cost=3500.0, budget=500.0, strategy="MAXIMIZE_CONVERSIONS"):
    return {"id": cid, "name": name, "week": week, "impressions": impr, "clicks": clicks, "cost": cost, "share": share, "lostBudget": lb, "lostRank": lr,
            "top": 0.5, "absTop": 0.3, "budget": budget, "strategy": strategy}


def account(rows, **kw):
    return m.analyse(rows, TODAY, **kw)


def ids(a):
    return [s["id"] for s in a["plan"]["steps"]]


def step(a, sid):
    return next(s for s in a["plan"]["steps"] if s["id"] == sid)


def text(s):
    return " ".join([s["title"], s["why"], s["effect"] or ""] + s["how"])


# ── money ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v,cur,want", [(12.5, "USD", "$12.50"), (1234.5, "USD", "$1,234"), (99.994, "USD", "$99.99"), (100, "USD", "$100"), (7, "EUR", "€7.00"),
                                        (7, "CHF", "7.00 CHF"), (None, "USD", "n/a"), (2500, "SEK", "2,500 SEK")])
def test_money_is_written_with_its_symbol_or_its_code_and_no_pence_from_100_up(v, cur, want):
    assert p.money(v, cur) == want


# ── Budget ──────────────────────────────────────────────────────────────────

def test_a_budget_loss_gets_a_budget_step_sized_from_last_weeks_spend_and_budget():
    a = account([row(share=0.70, lb=0.25, lr=0.0)])           # spent 3,500 in the week: 500 a day, against a 500 budget; 90% - 70% = 20 points to win
    s = step(a, "budget")
    assert s["title"] == "Raise the daily budget of the brand campaigns" and s["gain"] == pytest.approx(0.20) and s["level"] == "high"
    assert "25% of the brand searches were lost to budget" in s["why"] and "spent about $500 a day against daily budgets of $500 in total (100% of it)" in s["why"]
    assert "spending right up to the budget" in s["why"]
    extra = 500 * 0.20 / 0.70                                # $142.86 more a day wins back the 20 points at last week's cost per impression
    assert "$143" in text(s) and "about 20 points" in text(s) and "bring the brand to the 90% threshold" in text(s)
    assert "(25 points) would take about $179 a day" in text(s) and s["effect"] == "Worth up to 20 points of brand impression share." and s["campaigns"] == ["P2_Brand"]
    assert round(extra) == 143


def test_the_budget_step_stops_at_the_loss_when_the_loss_is_smaller_than_the_gap():
    a = account([row(share=0.70, lb=0.10, lr=0.20)])
    s = step(a, "budget")
    assert s["gain"] == pytest.approx(0.10) and "bring the brand to" not in text(s) and s["level"] == "high"


def test_spending_well_under_the_budget_is_said_and_a_missing_budget_falls_back_to_the_spend():
    under = step(account([row(cost=700.0, budget=500.0, lr=0.0)]), "budget")                 # 100 a day against 500
    assert "(20% of it)" in under["why"] and "runs out on some days and not others" in under["why"]
    none = step(account([row(budget=None, lr=0.0)]), "budget")
    assert "spent about $500 a day." in none["why"] and "against daily budgets" not in none["why"]
    mixed = step(account([row(lr=0.0), row(cid="2", name="P2_Brand_E", budget=None)]), "budget")
    assert "against daily budgets" not in mixed["why"]


def test_the_budget_step_names_the_campaigns_that_lost_the_most_and_there_is_none_under_5_points():
    rows = [row(cid="1", name="Brand A", impr=1000, share=0.9, lb=0.0, lr=0.0), row(cid="2", name="Brand B", impr=500, share=0.5, lb=0.5, lr=0.0)]
    a = account(rows)
    assert step(a, "budget")["campaigns"] == ["Brand B"] and "Brand B" in step(a, "budget")["how"][0]
    assert "budget" not in ids(account([row(share=0.80, lb=0.0499, lr=0.15)]))
    assert "budget" in ids(account([row(share=0.80, lb=0.05, lr=0.15)]))


def test_the_budget_step_offers_to_move_money_and_to_unshare_a_shared_budget():
    h = " ".join(step(account([row(lr=0.0)]), "budget")["how"])
    assert "take it from the campaigns that earn least" in h and "shared budget" in h and "next Monday" in h


# ── Bids ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("strategy,title,must", [
    ("TARGET_IMPRESSION_SHARE", "Raise the impression share target and the bid limit", ["Maximum CPC bid limit", "90% to 95%", "Absolute top of page"]),
    ("TARGET_CPA", "Stop the automated bidding from holding the brand back", ["raise it by about 30%", "Target impression share"]),
    ("TARGET_ROAS", "Stop the automated bidding from holding the brand back", ["lower it by about 30%"]),
    ("MAXIMIZE_CONVERSIONS", "Stop the automated bidding from holding the brand back", ["no target to loosen", "Target impression share"]),
    ("MAXIMIZE_CONVERSION_VALUE", "Stop the automated bidding from holding the brand back", ["no target to loosen"]),
    ("TARGET_SPEND", "Raise the maximum CPC limit, or move to Target impression share", ["Maximize clicks", "Maximum CPC bid limit"]),
    ("MANUAL_CPC", "Raise the bids on the brand keywords", ["by about 20%", "exact brand keywords first"]),
    ("", "Check the bidding of the brand campaigns", ["could not tell"]),
    ("PERCENT_CPC", "Check the bidding of the brand campaigns", ["could not tell"])])
def test_the_bid_step_follows_the_bidding_strategy_in_use(strategy, title, must):
    a = account([row(share=0.60, lb=0.0, lr=0.30, strategy=strategy)])
    s = step(a, "bids")
    assert s["title"] == title and s["level"] == "high" and s["gain"] == pytest.approx(0.30)
    for needle in must:
        assert needle in text(s), needle
    assert "30% of the brand searches were lost to rank" in s["why"] and "budget" not in ids(a)


def test_the_bid_step_starts_the_cpc_limit_at_twice_last_weeks_average_cpc():
    a = account([row(share=0.60, lb=0.0, lr=0.30, strategy="TARGET_IMPRESSION_SHARE", clicks=1000, cost=3500.0)])        # $3.50 a click
    assert "average CPC on the brand campaigns was $3.50" in text(step(a, "bids")) and "about $7.00" in text(step(a, "bids"))
    nocpc = account([row(share=0.60, lb=0.0, lr=0.30, strategy="TARGET_IMPRESSION_SHARE", clicks=0)])
    assert "average CPC on the brand campaigns was" not in text(step(nocpc, "bids")) and "twice last week" in text(step(nocpc, "bids"))


def test_the_strategy_the_most_searches_run_on_decides_the_step_and_the_campaigns_named_are_those_on_it():
    rows = [row(cid="1", name="Brand A", impr=900, share=0.6, lb=0.0, lr=0.3, strategy="MANUAL_CPC"), row(cid="2", name="Brand B", impr=100, share=0.6, lb=0.0, lr=0.3, strategy="TARGET_CPA")]
    s = step(account(rows), "bids")
    assert s["title"] == "Raise the bids on the brand keywords" and s["campaigns"] == ["Brand A"]


def test_the_bid_step_is_the_size_of_the_loss_up_to_the_gap_and_needs_5_points():
    assert step(account([row(share=0.85, lb=0.0, lr=0.15)]), "bids")["gain"] == pytest.approx(0.05)         # only 5 points to the threshold
    assert "bids" not in ids(account([row(share=0.70, lb=0.30, lr=0.0499)]))
    assert "bids" in ids(account([row(share=0.70, lb=0.25, lr=0.05)]))


# ── Relevance and competition ───────────────────────────────────────────────

def test_a_rank_loss_adds_the_quality_and_the_competition_steps_in_that_order_after_the_bids():
    a = account([row(share=0.60, lb=0.0, lr=0.30, strategy="MANUAL_CPC")])
    assert ids(a) == ["bids", "relevance", "competition"]
    rel, comp = step(a, "relevance"), step(a, "competition")
    assert rel["level"] == "medium" and "Exp. CTR" in text(rel) and "headline 1" in text(rel) and "Landing page exp." in text(rel)
    assert comp["level"] == "low" and "Auction insights" in text(comp) and "Outranking share" in text(comp) and "trademark complaint" in text(comp) and "does not carry Auction Insights" in comp["why"]
    small = account([row(share=0.80, lb=0.0, lr=0.10)])
    assert step(small, "relevance")["level"] == "low"


def test_budget_comes_before_the_bids_when_both_are_lost():
    a = account([row(share=0.50, lb=0.25, lr=0.25)])
    assert ids(a)[:2] == ["budget", "bids"] and a["cause"] == "both"


# ── What changed ────────────────────────────────────────────────────────────

def test_a_big_fall_puts_find_what_changed_first_and_a_small_one_second():
    big = account([row(share=0.70, lb=0.1, lr=0.2), row(BEFORE, share=0.95)])
    assert ids(big)[0] == "changed" and step(big, "changed")["level"] == "high" and "from 95% to 70%" in step(big, "changed")["why"]
    small = account([row(share=0.80, lb=0.1, lr=0.1), row(BEFORE, share=0.88)])           # 8 points: medium, after the first step
    assert ids(small)[0] == "budget" and ids(small).index("changed") == 1 and step(small, "changed")["level"] == "medium"
    h = " ".join(step(big, "changed")["how"])
    assert "Change history" in h and "Limited by budget" in h and "disapproved" in h and "Auction Insights" in h


def test_a_brand_above_the_threshold_that_fell_5_points_gets_only_the_find_what_changed_step():
    a = account([row(share=0.90, lb=0.0, lr=0.0), row(BEFORE, share=0.95, lb=0.0, lr=0.0)])
    assert a["status"] == "ok" and ids(a) == ["changed"]
    assert ids(account([row(share=0.91, lb=0.0, lr=0.0), row(BEFORE, share=0.95)])) == []


# ── The campaign behind it ──────────────────────────────────────────────────

def two(share_a, share_b):
    return [row(cid="1", name="Brand_E", impr=1000, share=share_a, lb=0.0, lr=0.1), row(cid="2", name="Brand_P", impr=1000, share=share_b, lb=0.4, lr=0.0)]


def test_a_campaign_far_behind_the_other_is_named_with_its_loss_and_the_overlap_advice():
    a = account(two(0.95, 0.60))
    s = step(a, "laggard")
    assert s["title"] == "Brand_P is the campaign holding the share down" and "Brand_P is at 60% while Brand_E is at 95%" in s["why"]
    assert "40% lost to budget and 0% lost to rank" in s["why"] and "overlaps another brand campaign" in " ".join(s["how"]) and "its budget (step above)" in s["how"][0]
    assert "laggard" in ids(account(two(0.80, 0.60)))                  # exactly 20 points apart
    assert "laggard" not in ids(account(two(0.79, 0.60))) and "laggard" not in ids(account(two(0.99, 0.95)))


def test_a_lone_campaign_or_one_too_small_to_judge_is_not_named():
    assert "laggard" not in ids(account([row()]))
    rows = [row(cid="1", name="Brand_E", impr=1000, share=0.95), row(cid="2", name="Brand_P", impr=10, share=0.20, lb=0.5, lr=0.3)]
    assert "laggard" not in ids(account(rows))


# ── The plan as a whole ─────────────────────────────────────────────────────

def test_an_account_that_cannot_be_judged_or_is_on_target_has_no_plan():
    for rows in ([row(impr=10)], [row(share=None, lb=None, lr=None)], [row(name="Core")], [row(share=0.97, lb=0.0, lr=0.0)]):
        a = account(rows)
        assert a["plan"] == {"note": None, "steps": []}, a["status"]


def test_the_steps_are_numbered_from_one_in_order():
    a = account([row(share=0.50, lb=0.25, lr=0.25, strategy="MANUAL_CPC"), row(BEFORE, share=0.95)])
    assert [s["n"] for s in a["plan"]["steps"]] == list(range(1, len(ids(a)) + 1)) and ids(a) == ["changed", "budget", "bids", "relevance", "competition"]


def test_a_brand_under_the_threshold_week_after_week_says_it_is_not_a_one_off():
    rows = [row(w, share=0.70) for w in ("2026-10-12", "2026-10-05", "2026-09-28")] + [row("2026-09-21", share=0.95)]
    a = account(rows)
    assert p.weeks_below(a) == 3 and a["plan"]["note"] == "The brand has been under 90% for 3 weeks in a row, so this is not a one-off."
    assert account([row(share=0.70), row(BEFORE, share=0.95)])["plan"]["note"] is None
    thin = [row(w, share=0.70) for w in ("2026-10-12", "2026-10-05")] + [row("2026-09-28", impr=10, share=0.70)]
    assert p.weeks_below(account(thin)) == 2                              # a week too thin to judge ends the run


def test_the_findings_point_at_the_plan_not_at_a_paragraph():
    a = account([row(share=0.70, lb=0.25, lr=0.0)])
    assert a["findings"][0]["nextStep"] == "Start with: Raise the daily budget of the brand campaigns. The fix plan below has every step, how to do it and what it is worth."
    dropped = account([row(share=0.70, lb=0.1, lr=0.2), row(BEFORE, share=0.95)])
    assert [f["nextStep"] for f in dropped["findings"] if f["kind"] == "dropped"][0].startswith("Start with: Find out what changed last week.")
    ok_drop = account([row(share=0.90, lb=0.0, lr=0.0), row(BEFORE, share=0.95, lb=0.0, lr=0.0)])
    assert ok_drop["findings"][0]["nextStep"].startswith("Start with: Find out what changed last week.")


def test_every_step_has_a_title_a_reason_how_to_and_no_dash_characters():
    for strategy in ("TARGET_IMPRESSION_SHARE", "TARGET_CPA", "TARGET_SPEND", "MANUAL_CPC", ""):
        a = account([row(share=0.50, lb=0.25, lr=0.25, strategy=strategy), row(BEFORE, share=0.95), row(cid="2", name="Brand_P", share=0.9, lb=0.0, lr=0.1)])
        for s in a["plan"]["steps"]:
            assert s["title"] and s["why"] and len(s["how"]) >= 3 and all(h.strip() for h in s["how"]) and s["level"] in ("high", "medium", "low")
            assert "\u2014" not in text(s) and "\u2013" not in text(s) and "%s" not in text(s) and "%%" not in text(s), s["id"]


def test_the_account_currency_is_used_for_the_money():
    a = m.analyse([row(share=0.70, lb=0.25, lr=0.0)], TODAY, currency="GBP")
    assert "£500 a day" in step(a, "budget")["why"]


def test_spending_nine_tenths_of_the_budget_counts_as_right_up_to_it_and_less_does_not():
    assert "spending right up to the budget" in step(account([row(cost=3150.0, budget=500.0, lr=0.0)]), "budget")["why"]       # 450 a day of 500
    assert "runs out on some days" in step(account([row(cost=3143.0, budget=500.0, lr=0.0)]), "budget")["why"]


def test_the_campaigns_that_lost_the_most_budget_are_named_first():
    rows = [row(cid="1", name="Brand A", impr=500, share=0.8, lb=0.10, lr=0.0), row(cid="2", name="Brand B", impr=500, share=0.5, lb=0.40, lr=0.0), row(cid="3", name="Brand C", impr=500, share=0.6, lb=0.20, lr=0.0)]
    assert step(account(rows), "budget")["campaigns"] == ["Brand B", "Brand C", "Brand A"] and "Brand B, Brand C, Brand A" in step(account(rows), "budget")["how"][0]
    many = [row(cid=str(i), name="Brand %d" % i, impr=500, share=0.5, lb=0.3, lr=0.0) for i in range(5)]
    assert "and 2 more" in step(account(many), "budget")["how"][0]


def test_a_target_roas_is_loosened_by_lowering_it_and_a_target_cpa_by_raising_it():
    assert "lower it by about 30%" in text(step(account([row(share=0.6, lb=0.0, lr=0.3, strategy="TARGET_ROAS")]), "bids"))
    assert "raise it by about 30%" in text(step(account([row(share=0.6, lb=0.0, lr=0.3, strategy="TARGET_CPA")]), "bids"))
    assert "no target to loosen" not in text(step(account([row(share=0.6, lb=0.0, lr=0.3, strategy="TARGET_ROAS")]), "bids"))


def test_the_strategy_most_of_the_eligible_searches_run_on_wins_even_when_it_is_listed_last():
    rows = [row(cid="1", name="Brand_a_small", impr=100, share=0.6, lb=0.0, lr=0.3, strategy="TARGET_CPA"), row(cid="2", name="Brand_b_big", impr=900, share=0.6, lb=0.0, lr=0.3, strategy="MANUAL_CPC")]
    assert step(account(rows), "bids")["title"] == "Raise the bids on the brand keywords"
