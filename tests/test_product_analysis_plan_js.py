"""Product Analysis: what to do about each group of products, run in Node (static/js/pa_plan.js).

The advice has to be right for the campaign the money is in: a bid change reaches a product only on a Shopping campaign whose bids are set by hand or from
clicks. Smart Bidding sets its own and Performance Max has no bid for one product, so a plan that said "raise the bid" there would tell someone to do nothing.
These tests pin which steps each kind of spend gets, the figures in them and in the estimates, and that nothing is ever said where the spend is not known.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "static", "js", "pa_plan.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

NP = 11
CTX = {"hasValue": True, "hasConv": True, "target": 4.0, "acctRoas": 4.0, "acctCpa": 20.0, "acctCvr": 0.05, "acctCtr": 0.02, "minClicks": 25, "minSpend": 20.0}
EM, EN = chr(0x2014), chr(0x2013)


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const L=require(%s);%sconst out=(%s);console.log(JSON.stringify(out));" % (json.dumps(SCRIPT), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def prod(pid, impr=1000, clicks=100, cost=100.0, conv=0.0, val=0.0):
    return {"id": pid, "title": "Product " + pid, "other": False, "n": [impr, clicks, cost, conv, val] + [0] * (NP - 5)}


def mixof(by, campaigns=(), top=None):
    return {"campaigns": [list(c) for c in campaigns], "by": by, "top": top or {}}


def plan(key, rows, mix, ctx=None, currency="USD"):
    return run("L.build(K,R,C,M,{currency:CUR})", K=key, R=rows, C=ctx or CTX, M=mix, CUR=currency)


def text(p):
    return " ".join(p["steps"])


ADJ, SMART, AUTO, META = ([100.0, 0, 0, 0, 0], [0, 100.0, 0, 0, 0], [0, 0, 100.0, 0, 0], [0, 0, 0, 0, 100.0])


# ── Which kind of campaign the spend is on ────────────────────────────────

def test_a_kind_needs_sixty_percent_of_the_spend_and_otherwise_the_spend_is_mixed():
    rows = [prod("a")]
    kind = lambda v: run("L.kindOf(L.groupMix(R,M))", R=rows, M=mixof({"a": v}))
    assert kind(ADJ) == "adjustable" and kind(SMART) == "smart" and kind(AUTO) == "auto" and kind(META) == "meta"
    assert kind([60.0, 40.0, 0, 0, 0]) == "adjustable" and kind([59.0, 41.0, 0, 0, 0]) == "mixed"
    assert kind([0, 0, 0, 100.0, 0]) == "mixed"                                              # bidding Google did not name is not a kind
    assert run("L.kindOf(L.groupMix(R,null))", R=rows) == "unknown"
    assert run("L.kindOf(L.groupMix(R,M))", R=rows, M=mixof({})) == "unknown"                 # none of these products spent in a campaign that was read
    assert run("L.kindOf(L.groupMix(R,M))", R=rows, M=mixof({"a": [0, 0, 0, 0, 0]})) == "unknown"


def test_the_spend_is_added_across_the_products_of_a_group():
    rows = [prod("a"), prod("b"), prod("c")]
    g = run("L.groupMix(R,M)", R=rows, M=mixof({"a": [10.0, 0, 0, 0, 0], "b": [0, 30.0, 0, 0, 0]}))               # c has no spend recorded
    assert g["spend"] == [10.0, 30.0, 0, 0, 0] and g["total"] == 40.0 and g["share"]["smart"] == 0.75


def test_the_campaigns_named_are_the_biggest_of_that_kind_and_never_more_than_three():
    camps = [("Shop A", "Shopping", "Manual CPC", "adjustable", 50.0), ("Shop B", "Shopping", "Maximize clicks", "adjustable", 30.0),
             ("PMax", "Performance Max", "Maximize conversion value", "auto", 90.0), ("Shop C", "Shopping", "Manual CPC", "adjustable", 5.0),
             ("Shop D", "Shopping", "Manual CPC", "adjustable", 4.0)]
    rows = [prod(i) for i in "abcdef"]
    by = {"a": [10.0, 0, 0, 0, 0], "b": [40.0, 0, 0, 0, 0], "c": [20.0, 0, 0, 0, 0], "d": [5.0, 0, 0, 0, 0], "e": [0, 0, 90.0, 0, 0], "f": [1.0, 0, 0, 0, 0]}
    top = {"a": 0, "b": 1, "c": 2 + 1, "d": 4, "e": 2, "f": 0}                              # b and c are the biggest spenders; d and f are small
    mix = mixof(by, camps, top)
    assert run("L.campaignsOf(R,M,'adjustable')", R=rows, M=mix) == ["Shop B", "Shop C", "Shop A"]      # weighted by the spend of the products that spend most there
    assert run("L.campaignsOf(R,M,'auto')", R=rows, M=mix) == ["PMax"]
    assert run("L.campaignsOf(R,M,'smart')", R=rows, M=mix) == []
    assert run("L.campaignsOf(R,null,'auto')", R=rows) == []


# ── The bid rule is the one the other agents use ──────────────────────────

def test_the_suggested_bid_change_is_the_rule_geo_and_segment_analysis_use():
    adj = lambda idx: run("L.adjustmentFor(I)", I=idx)
    assert adj(1.25) == 0.2 and adj(1.375) == 0.3 and adj(2.0) == 0.4 and adj(9) == 0.4          # most of the gap, +40% at the most
    assert adj(0.75) == -0.2 and adj(0.375) == -0.5 and adj(0.1) == -0.5                          # -50% at the most
    assert adj(1.0) == 0 and adj(1.03) == 0 and adj(0.97) == 0                                    # on par: none
    assert adj(1.05) == 0.05 and adj(1.1) == 0.1 and adj(0.9) == -0.1                              # a small gap is a small step, not none


# ── Stop paying for products that do not sell ─────────────────────────────

def test_a_waste_plan_excludes_and_says_what_the_money_would_return():
    rows = [prod("a", cost=100.0), prod("b", cost=50.0)]
    p = plan("waste", rows, mixof({"a": ADJ, "b": ADJ}, [("Shop Main", "Shopping", "Manual CPC", "adjustable", 150.0)], {"a": 0, "b": 0}))
    assert p["kind"] == "adjustable" and p["lead"] == "2 products spent $150 without a sale, each more than the $20 a typical sale costs here."
    t = text(p)
    assert "Merchant Center" in p["steps"][0] and "Open Shop Main, then Ad groups" in t and "Item ID" in t and "Excluded" in t
    assert p["worth"].startswith("Excluding them frees $150 for the range; at the account's 4.00x that money would return about $600 of revenue")
    assert "an estimate" in p["worth"] and p["reach"] == ""                                       # an exclusion reaches every kind of spend


def test_a_waste_plan_names_two_campaigns_when_the_products_are_in_two():
    rows = [prod("a", cost=100.0), prod("b", cost=50.0)]
    camps = [("Shop Main", "Shopping", "Manual CPC", "adjustable", 100.0), ("Shop Sale", "Shopping", "Maximize clicks", "adjustable", 50.0)]
    p = plan("waste", rows, mixof({"a": ADJ, "b": ADJ}, camps, {"a": 0, "b": 1}))
    assert "Open Shop Main or Shop Sale, then Ad groups" in text(p)


def test_a_waste_plan_on_smart_bidding_and_performance_max_says_why_a_bid_will_not_do():
    rows = [prod("a")]
    smart = plan("waste", rows, mixof({"a": SMART}))
    assert "a lower bid does nothing under Smart Bidding" in text(smart) and "cut its bid" not in text(smart)
    auto = plan("waste", rows, mixof({"a": AUTO}, [("Max", "Performance Max", "Maximize conversion value", "auto", 100.0)], {"a": 0}))
    assert "Performance Max has no bid for a single product" in text(auto) and "Listing groups" in text(auto) and "open Max, then Asset groups" in text(auto)
    meta = plan("waste", rows, mixof({"a": META}))
    assert "Commerce Manager" in text(meta) and "Product groups" not in text(meta)


def test_many_products_are_pointed_at_a_custom_label_and_a_few_are_not():
    few = plan("waste", [prod("p%d" % i) for i in range(10)], mixof({}))
    many = plan("waste", [prod("p%d" % i) for i in range(11)], mixof({}))
    assert "custom label" not in text(few) and "custom label" in text(many)


def test_without_conversion_value_the_money_is_worth_conversions_at_the_accounts_cost():
    ctx = dict(CTX, hasValue=False, acctRoas=None, target=None)
    p = plan("waste", [prod("a", cost=100.0)], mixof({"a": ADJ}), ctx)
    assert "about 5 conversions" in p["worth"] and "the account's $20 a conversion" in p["worth"]


# ── Out of stock ──────────────────────────────────────────────────────────

def test_an_out_of_stock_plan_fixes_the_feed_first_and_pauses_only_what_is_truly_out():
    p = plan("oos", [prod("a", cost=200.0)], mixof({"a": ADJ}))
    assert p["lead"] == "1 product still got $200 of spend while Merchant Center said it was out of stock."
    assert "Diagnostics" in p["steps"][0] and "correct the availability" in p["steps"][1] and "pause it until it is back" in p["steps"][2] and "Excluded" in p["steps"][2]
    assert "at least daily" in p["steps"][3] and p["worth"].startswith("That $200 went to clicks that could not become sales")


# ── Give the best earners more room ───────────────────────────────────────

def gems():
    return [prod("a", cost=100.0, conv=10, val=500.0), prod("b", cost=100.0, conv=12, val=600.0)]          # 1.25x and 1.5x the 4x target


def test_a_gem_plan_raises_the_bid_by_the_median_suggestion_on_hand_set_bidding():
    p = plan("gem", gems(), mixof({"a": ADJ, "b": ADJ}, [("Shop Main", "Shopping", "Manual CPC", "adjustable", 200.0)], {"a": 0, "b": 0}))
    t = text(p)
    assert "raise each one's Max. CPC" in t and "about +30%" in t and "open Shop Main, Ad groups" in t and p["reach"] == ""
    assert p["worth"].startswith("These return $1,100 on $200 (5.5x). Raising their spend 20% ($40 more) at half that return would add about $110 of revenue: an estimate")


def test_a_gem_plan_never_raises_a_bid_where_bidding_is_automatic():
    smart = plan("gem", gems(), mixof({"a": SMART, "b": SMART}))
    assert "a higher bid does nothing" in text(smart) and "Target ROAS a little below" in text(smart) and "raise each one's Max. CPC" not in text(smart)
    auto = plan("gem", gems(), mixof({"a": AUTO, "b": AUTO}))
    assert "no bid for a single product" in text(auto) and "asset group" in text(auto) and "raise each one's Max. CPC" not in text(auto)
    meta = plan("gem", gems(), mixof({"a": META, "b": META}))
    assert "20% at a time" in text(meta) and "learning phase" in text(meta)


def test_a_gem_plan_with_no_target_to_compare_against_suggests_a_modest_rise():
    ctx = dict(CTX, hasValue=False, target=None, acctRoas=None, acctCpa=None)
    p = plan("gem", [prod("a", cost=100.0, conv=10)], mixof({"a": ADJ}), ctx)
    assert "about +20%" in text(p) and "10 conversions on $100" in p["worth"]


# ── Mixed and unknown spend ───────────────────────────────────────────────

def test_mixed_spend_gets_a_block_for_each_kind_of_at_least_a_tenth_and_says_how_much_a_bid_reaches():
    rows = gems()
    p = plan("gem", rows, mixof({"a": [60.0, 30.0, 6.0, 0, 0], "b": [0, 0, 0, 0, 0]}))
    assert p["kind"] == "adjustable"                                                                 # 60% on hand-set bids is enough to call it that
    mixed = plan("gem", rows, mixof({"a": [50.0, 45.0, 5.0, 0, 0]}))
    t = text(mixed)
    assert mixed["kind"] == "mixed" and "On campaigns whose bids you set" in t and "On Smart Bidding Shopping campaigns" in t
    assert "On Performance Max" not in t and "On Meta" not in t                                      # 5% is below a tenth: not worth a block
    assert mixed["reach"].startswith("Where their spend sits: 50% on bids you set, 45% on Smart Bidding, 5% on Performance Max.") and "reaches only the first" in mixed["reach"]


def test_bidding_google_did_not_name_still_gets_advice():
    p = plan("gem", gems(), mixof({"a": [0, 0, 0, 100.0, 0]}))
    assert p["kind"] == "mixed" and "On campaigns whose bids you set" in text(p) and "On Meta" in text(p)       # nothing is a tenth: every kind is said


def test_when_the_spend_is_not_known_every_kind_is_covered_and_the_lead_says_so():
    p = plan("drag", [prod("a", cost=100.0, conv=5, val=300.0)], None)
    t = text(p)
    assert p["kind"] == "unknown" and p["reach"].startswith("Which kind of campaign these products spend in could not be read")
    for label in ("On campaigns whose bids you set", "On Smart Bidding Shopping campaigns", "On Performance Max", "On Meta"):
        assert label in t, label
    assert "worth" not in p                                                                           # no saving is promised on spend that may not be reachable


# ── Fix the page or the price ─────────────────────────────────────────────

def test_a_leak_plan_is_about_the_page_and_the_price_and_cuts_bids_only_where_they_reach():
    rows = [prod("a", clicks=100, cost=100.0, conv=2.25, val=450.0)]                                  # converts at 0.0225: 45% of the account's 0.05
    p = plan("leak", rows, mixof({"a": ADJ}))
    assert "Price competitiveness" in p["steps"][1] and "lower the Max. CPC by about -45%" in text(p)
    assert p["worth"].startswith("If they converted at the account's rate they would add about 3 sales (about $600)") and "an estimate" in p["worth"]
    auto = plan("leak", rows, mixof({"a": AUTO}))
    assert "A bid cannot be lowered for one product here" in text(auto) and "lower the Max. CPC" not in text(auto)


def test_a_leak_with_no_gap_to_close_promises_nothing():
    rows = [prod("a", clicks=100, cost=100.0, conv=5.0, val=450.0)]
    assert "worth" not in plan("leak", rows, mixof({"a": ADJ}))


# ── Make the listing worth a click ────────────────────────────────────────

def test_a_window_plan_fixes_the_listing_not_the_bid():
    rows = [prod("a", impr=10000, clicks=50, cost=40.0)]
    p = plan("window", rows, mixof({"a": SMART}))
    assert "first 70 characters of the title" in p["steps"][0] and "main image" in p["steps"][1] and "Price competitiveness" in p["steps"][2]
    assert "bid" not in text(p).lower() and p["reach"] == ""
    assert p["worth"] == "At the account's click rate they would get about 150 more clicks, roughly 8 conversions at the account's rate over this range: an estimate, since a click rate that low may also reflect the price."


def test_a_window_plan_with_a_tiny_gap_promises_nothing():
    assert "worth" not in plan("window", [prod("a", impr=1000, clicks=19, cost=5.0)], mixof({"a": ADJ}))          # 20 expected, 19 got


# ── Trim what sells below target ──────────────────────────────────────────

def test_a_drag_plan_lowers_the_bid_by_the_rule_and_counts_the_saving_only_on_spend_a_bid_reaches():
    rows = [prod("a", cost=100.0, conv=10, val=300.0)]                                                # 3x against a 4x target: 0.75 of it
    p = plan("drag", rows, mixof({"a": ADJ}))
    assert "Lower their bids by about -20%" in text(p) and "price rise" in text(p)
    assert p["worth"].startswith("Trimming bids 20% on the $100 of their spend that bids reach would save up to about $20 for the range")
    half = plan("drag", rows, mixof({"a": [50.0, 50.0, 0, 0, 0]}))
    assert half["kind"] == "mixed" and half["worth"].startswith("Trimming bids 20% on the $50 of their spend that bids reach would save up to about $10")
    assert "worth" not in plan("drag", rows, mixof({"a": SMART}))                                     # nothing a bid change reaches


def test_a_drag_plan_for_a_product_barely_under_target_still_suggests_a_small_cut():
    p = plan("drag", [prod("a", cost=100.0, conv=10, val=396.0)], mixof({"a": ADJ}))               # 0.99 of the target: the rule gives none, so the cut is the default 10%
    assert "Lower their bids by about -10%" in text(p) and p["worth"].startswith("Trimming bids 10% on the $100")


def test_a_drag_plan_on_automatic_bidding_moves_the_products_instead():
    rows = [prod("a", cost=100.0, conv=10, val=300.0)]
    smart = plan("drag", rows, mixof({"a": SMART}))
    assert "a lower bid does nothing" in text(smart) and "higher Target ROAS" in text(smart) and "Lower their bids" not in text(smart)
    assert "higher target ROAS" in text(plan("drag", rows, mixof({"a": AUTO}))) and "smaller budget" in text(plan("drag", rows, mixof({"a": META})))


# ── Shape and wording ─────────────────────────────────────────────────────

def test_a_plan_is_made_only_for_a_real_group_and_ignores_the_folded_row():
    assert plan("waste", [], mixof({})) is None and plan("nonsense", [prod("a")], mixof({})) is None
    folded = dict(prod("x", cost=900.0), other=True)
    p = plan("waste", [prod("a", cost=50.0), folded], mixof({"a": ADJ}))
    assert p["lead"].startswith("1 product spent $50 without")                                         # the "all other products" row is many products, not one
    assert plan("waste", [folded], mixof({})) is None


def test_the_wording_agrees_with_the_number_of_products():
    one = plan("gem", [gems()[0]], mixof({"a": ADJ}))["lead"]
    two = plan("gem", gems(), mixof({"a": ADJ, "b": ADJ}))["lead"]
    assert one.startswith("1 product earns at least 1.25x the target and takes under half") and two.startswith("2 products earn at least 1.25x the target and take under half")
    assert plan("drag", [prod("a", cost=100.0, conv=10, val=300.0)], mixof({}))["lead"].startswith("1 product sells, but earns less")
    assert plan("window", [prod("a", impr=10000, clicks=50)], mixof({}))["lead"].startswith("1 product was shown often")


def test_the_money_is_written_in_the_accounts_currency():
    p = plan("waste", [prod("a", cost=100.0)], mixof({"a": ADJ}), currency="EUR")
    assert "€100" in p["lead"] and "$" not in p["lead"]


def test_no_plan_uses_an_em_or_en_dash_or_leaves_a_placeholder():
    gem_rows, loser = gems(), [prod("a", cost=100.0, conv=10, val=300.0)]
    mixes = [mixof({"a": v, "b": v}) for v in (ADJ, SMART, AUTO, META, [50.0, 40.0, 10.0, 0, 0])] + [None]
    for mix in mixes:
        for key, rows in (("waste", loser), ("oos", loser), ("gem", gem_rows), ("leak", [prod("a", clicks=100, cost=100.0, conv=2.0, val=400.0)]),
                          ("window", [prod("a", impr=10000, clicks=50)]), ("drag", loser)):
            p = plan(key, rows, mix)
            blob = json.dumps(p, ensure_ascii=False)
            assert EM not in blob and EN not in blob, (key, mix)
            assert "undefined" not in blob and "NaN" not in blob and "null" not in blob, (key, mix, blob)
