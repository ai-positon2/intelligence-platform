"""Paid Marketing Agents: Geo Performance, what the numbers mean.

Pinned here: the efficiency index (ROAS when the account tracks value, CPA otherwise, 100 is the account's
average); that a place with too few conversions is never judged or adjusted; the suggested bid adjustment
(80% of the gap, steps of 5%, +40% to -50%, zero inside one step); what counts as leaking spend; that every
insight carries its evidence with numbers that are really in the rows; and the wording for a level.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_geo as g  # noqa: E402
from tracker import paid_geo_insights as ins  # noqa: E402


def row(key, cost, conv, clicks=100, value=0.0, imp=2000, tc=1000.0, tv=100.0):
    m = {"imp": imp, "clk": clicks, "cost": cost, "conv": conv, "val": value}
    return dict(g.derive(m, tc, tv), key=key, label=key, sub="")


def table(rows, total_cost=1000.0, total_conv=100.0, total_value=0.0, clicks=1000):
    total = g.derive({"imp": 20000, "clk": clicks, "cost": total_cost, "conv": total_conv, "val": total_value}, total_cost, total_conv)
    return {"rows": rows, "total": total, "coverage": 1.0}


def judged(rows, **kw):
    out = ins.analyse(table(rows, **kw), level="region")
    return {r["key"]: r for r in out["rows"]}, out


# ── The index ───────────────────────────────────────────────────────────────

def test_with_no_value_a_lower_cpa_than_the_account_is_better_and_100_is_the_average():
    # the account: $1000 for 100 conversions = $10 a conversion
    r, _ = judged([row("a", 100, 20), row("b", 100, 10), row("c", 100, 5)])
    assert (r["a"]["index"], r["b"]["index"], r["c"]["index"]) == (200, 100, 50)


def test_when_the_account_tracks_value_the_index_is_roas_against_the_accounts():
    # the account's ROAS is 2.0; a place at 3.0 is 150
    r, out = judged([row("a", 100, 10, value=300), row("b", 100, 10, value=100)], total_value=2000)
    assert out["basis"] == "roas" and r["a"]["index"] == 150 and r["b"]["index"] == 50


def test_the_basis_is_none_when_the_account_converted_nothing():
    r, out = judged([row("a", 100, 0)], total_conv=0)
    assert out["basis"] is None and r["a"]["verdict"] == "none" and r["a"]["adjustment"] is None
    assert [i["kind"] for i in out["insights"]] == ["nodata"] and "No conversions in this range" in out["insights"][0]["headline"]


# ── Enough data first ───────────────────────────────────────────────────────

def test_a_place_with_fewer_than_five_conversions_is_never_judged_or_adjusted_however_good_it_looks():
    r, _ = judged([row("lucky", 20, 4), row("one", 10, 1)])
    for k in ("lucky", "one"):
        assert r[k]["verdict"] == "thin" and r[k]["index"] is None and r[k]["adjustment"] is None and r[k]["confidence"] is None
    assert "too few to judge" in r["lucky"]["why"] and "1 conversion is" in r["one"]["why"]


def test_five_conversions_is_enough():
    r, _ = judged([row("a", 25, 5)])
    assert r["a"]["verdict"] == "star" and r["a"]["confidence"] == "low"


@pytest.mark.parametrize("conv,expect", [(5, "low"), (7, "low"), (8, "medium"), (14, "medium"), (15, "high"), (60, "high")])
def test_confidence_follows_how_many_conversions_stand_behind_the_index(conv, expect):
    assert ins.confidence_of(conv) == expect


def test_a_big_spender_that_got_nothing_is_leaking_only_with_real_traffic_and_real_money():
    # account CPA $10: leaking needs 30 clicks, at least $20 (2x CPA) and 1% of $1000
    r, _ = judged([row("leak", 60, 0, clicks=80), row("fewclicks", 60, 0, clicks=10), row("cheap", 15, 0, clicks=80)])
    assert r["leak"]["verdict"] == "leak" and r["leak"]["adjustment"] == -0.5 and "did not convert" in r["leak"]["why"]
    assert r["fewclicks"]["verdict"] == "thin" and r["cheap"]["verdict"] == "thin"
    assert "not enough spend or clicks" in r["cheap"]["why"]


# ── Verdicts and suggested adjustments ──────────────────────────────────────

@pytest.mark.parametrize("idx,verdict", [(2.0, "star"), (1.3, "star"), (1.29, "good"), (1.1, "good"), (1.09, "par"), (1.0, "par"),
                                         (0.9, "par"), (0.89, "below"), (0.76, "below"), (0.74, "weak"), (0.3, "weak")])
def test_the_verdict_bands(idx, verdict):
    # account $10 a conversion; a place with `conv` conversions at cost = conv * 10 / idx has that index
    conv = 10
    cost = conv * 10 / idx
    r, _ = judged([row("x", cost, conv)])
    assert r["x"]["verdict"] == verdict, (idx, r["x"]["index"])


@pytest.mark.parametrize("idx,adj", [(1.0, 0.0), (1.1, 0.10), (1.3, 0.25), (1.5, 0.40), (3.0, 0.40), (0.9, -0.10), (0.75, -0.20),
                                     (0.5, -0.40), (0.1, -0.50), (0.0, -0.50)])
def test_the_suggested_adjustment_is_most_of_the_gap_in_steps_of_five_and_capped(idx, adj):
    assert ins.adjustment_for(idx) == adj


def test_every_suggestion_is_a_multiple_of_five_percent_and_inside_the_caps():
    for i in range(0, 400):
        a = ins.adjustment_for(i / 100)
        assert -0.5 <= a <= 0.4 and abs(round(a / 0.05) * 0.05 - a) < 1e-9


def test_a_place_on_par_gets_zero_not_none():
    r, _ = judged([row("a", 100, 10)])
    assert r["a"]["verdict"] == "par" and r["a"]["adjustment"] == 0.0


def test_the_counts_say_how_many_places_got_each_verdict():
    _, out = judged([row("a", 25, 10), row("b", 100, 10), row("c", 5, 1)])
    assert out["counts"] == {"star": 1, "par": 1, "thin": 1}


# ── What to do ──────────────────────────────────────────────────────────────

def test_the_insights_name_where_to_push_trim_and_exclude_with_the_numbers_beside_them():
    rows = [row("Maryland", 200, 30), row("Ohio", 400, 12), row("Texas", 60, 0, clicks=90), row("Utah", 340, 58)]
    _, out = judged(rows, total_cost=1000, total_conv=100)
    kinds = [i["kind"] for i in out["insights"]]
    assert {"push", "trim", "exclude", "concentration"} <= set(kinds)
    push = next(i for i in out["insights"] if i["kind"] == "push")
    assert push["headline"] == "Raise bids in Utah by about 10%" or push["headline"].startswith("Raise bids in")
    assert push["severity"] == "action" and push["keys"] and "index" in push["evidence"]
    trim = next(i for i in out["insights"] if i["kind"] == "trim")
    assert "Ohio" in trim["headline"] and "CPA $33.33 against $10.00" in trim["evidence"] and "index 30" in trim["evidence"]
    leak = next(i for i in out["insights"] if i["kind"] == "exclude")
    assert "Texas spent $60 and did not convert" == leak["headline"] and "6.0 times" in leak["evidence"] and "$10.00" in leak["evidence"]


def test_actions_come_before_watches_and_infos():
    rows = [row("A", 100, 30), row("B", 300, 5), row("C", 400, 40), row("D", 200, 25)]
    _, out = judged(rows)
    ranks = [{"action": 0, "watch": 1, "info": 2}[i["severity"]] for i in out["insights"]]
    assert ranks == sorted(ranks) and ranks[0] == 0


def test_the_headline_and_evidence_numbers_are_in_the_rows():
    rows = [row("Utah", 100, 30), row("Ohio", 400, 12)]
    _, out = judged(rows)
    text = " ".join(i["headline"] + " " + i["evidence"] for i in out["insights"])
    assert "30 conversions" in text or "12 conversions" in text
    assert "46.9%" not in text and "10.0%" in text                              # shares are the rows' own pctCost


def test_a_thin_tail_is_said_once_with_what_to_do_about_it():
    rows = [row("big", 600, 80)] + [row("t%d" % i, 20, 1, clicks=10) for i in range(10)]
    _, out = judged(rows, total_cost=800, total_conv=90)
    thin = next(i for i in out["insights"] if i["kind"] == "thin")
    assert thin["headline"].startswith("10 of 11") and "Do not adjust these one by one" in thin["action"] and thin["severity"] == "watch"


def test_low_coverage_is_called_out_and_full_coverage_is_not():
    t = table([row("a", 100, 10), row("b", 100, 10)])
    assert not [i for i in ins.analyse(dict(t, coverage=0.95), level="city")["insights"] if i["kind"] == "coverage"]
    cov = [i for i in ins.analyse(dict(t, coverage=0.62), level="city")["insights"] if i["kind"] == "coverage"]
    assert cov and cov[0]["headline"] == "This table covers 62% of the spend"


def test_concentration_needs_at_least_four_places_and_reads_differently_when_spread():
    assert not [i for i in judged([row("a", 100, 10), row("b", 100, 10)])[1]["insights"] if i["kind"] == "concentration"]
    many = [row("p%d" % i, 50, 5, tc=500, tv=50) for i in range(10)]
    conc = next(i for i in judged(many, total_cost=500, total_conv=50)[1]["insights"] if i["kind"] == "concentration")
    assert "Spend is spread out" in conc["action"] and conc["headline"] == "Three regions hold 30% of the spend"


def test_the_noun_follows_the_level_and_plurals_are_right():
    assert ins.plural("city") == "cities" and ins.plural("county") == "counties" and ins.plural("region") == "regions"
    for level, word in (("city", "cities"), ("county", "counties"), ("postal_code", "postal codes"), ("dma", "metro areas"), ("country", "countries")):
        rows = [row("t%d" % i, 20, 1, clicks=10) for i in range(10)]
        out = ins.analyse(table(rows), level=level)
        text = " ".join(i["headline"] for i in out["insights"])
        assert word in text or level == "country", (level, text)


def test_no_text_the_agent_writes_uses_an_em_dash():
    rows = [row("Maryland", 200, 30), row("Ohio", 400, 12), row("Texas", 60, 0, clicks=90), row("Utah", 340, 58)]
    _, out = judged(rows)
    blob = " ".join(i["headline"] + i["evidence"] + i["action"] for i in out["insights"]) + " ".join(r["why"] for r in out["rows"])
    assert "—" not in blob and "–" not in blob


def test_money_is_written_in_the_accounts_currency():
    assert ins.money(1234.5) == "$1,234" or ins.money(1234.5) == "$1,235"
    assert ins.money(12.5, "GBP", 2) == "£12.50" and ins.money(7, "CHF") == "CHF 7" and ins.money(None) == "Na" and ins.money(-3, "USD") == "-$3"
    assert ins.pct(0.1234, 1) == "12.3%" and ins.pct(None) == "Na" and ins.num(3.0) == "3" and ins.num(3.4) == "3.4"


PLACES = lambda: [row("Maryland", 200, 30), row("Ohio", 400, 12), row("Texas", 60, 0, clicks=90), row("Utah", 340, 58)]


def _insights(platform):
    return ins.analyse(table(PLACES(), total_cost=1000, total_conv=100), level="region", platform=platform)["insights"]


def test_on_a_meta_account_the_advice_talks_of_budget_and_ad_sets_not_bid_adjustments():
    """Meta has no location bid adjustment: the same index says how much more or less budget a place deserves."""
    out = _insights("meta")
    said = " ".join(i["headline"] + " " + i["action"] for i in out if i["kind"] in ("push", "trim", "exclude", "concentration"))
    assert {i["kind"] for i in out} >= {"push", "trim", "exclude", "concentration"}
    assert "bid" not in said.lower() and "budget" in said
    push = next(i for i in out if i["kind"] == "push")
    assert push["headline"].startswith("Put more budget behind Utah") and "ad set of its own" in push["action"]
    assert next(i for i in out if i["kind"] == "trim")["headline"].startswith("Take budget out of Ohio")
    assert "halve" not in said and "cut its budget by half" in said


def test_on_google_and_on_both_the_advice_is_still_about_bids():
    for platform in ("google", "both"):
        said = " ".join(i["headline"] for i in _insights(platform))
        assert "Raise bids in" in said and "Lower bids in" in said, platform


def test_the_briefing_is_worded_for_the_platform_that_was_read():
    from tracker import paid_geo_summary as gs
    meta, google = gs.system_for({"account": {"platform": "meta"}}), gs.system_for({"account": {"platform": "google"}})
    assert "share of the budget" in meta and "Meta Ads terms" in meta and "bid adjustment" not in meta
    assert "higher or lower bid" in google and "Google Ads terms" in google and gs.SYSTEM == gs.system_for({})
    assert "Google Ads and Meta Ads terms" in gs.system_for({"account": {"platform": "both"}})
