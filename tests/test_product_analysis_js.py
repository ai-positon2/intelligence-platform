"""Product Analysis: the number-crunching in static/js/pa_data.js, run in Node.

The page script only draws; which group a product falls in, the 80/20 curve,
the roll-ups, what moved, the filters behind each recommendation and the lines
that say what stands out are all worked out here, so this is where a wrong
conclusion about a client's money would come from. The file is executed, not
read: a typo that only breaks at run time fails here.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "static", "js", "pa_data.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

IMPR, CLK, COST, CONV, VAL = range(5)
NP = 11


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const A=require(%s);%sconst out=(%s);console.log(JSON.stringify(out));" % (json.dumps(SCRIPT), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def prod(pid, impr=1000, clicks=100, cost=100.0, conv=0.0, val=0.0, brand="", t1="", t2="", title=None):
    """A product in the shape the server sends: [id, title, brand, type1, type2, type3, <NP numbers>] with strings in dims."""
    return {"id": pid, "title": title or "Product " + pid, "brand": brand, "t1": t1, "t2": t2, "n": [impr, clicks, cost, conv, val] + [0] * (NP - 5)}


def wire(products, other=None):
    """Build server-shaped data from the dict form above."""
    dims = [""]
    def di(s):
        if s not in dims:
            dims.append(s)
        return dims.index(s)
    rows = [[p["id"], p["title"], di(p["brand"]), di(p["t1"]), di(p["t2"]), 0] + p["n"] for p in products]
    total = [sum(p["n"][i] for p in products) + (other["n"][i] if other else 0) for i in range(NP)]
    return {"dims": dims, "products": rows, "other": other or {"count": 0, "n": [0] * NP}, "total": total}


def classified(products, opts=None, other=None):
    data = wire(products, other)
    expr = "(()=>{const it=A.items(D);const c=A.classify(it,A.totals(D),O);return {seg:Object.fromEntries(it.filter(i=>!i.other).map(i=>[i.id,i.seg])),gem:Object.fromEntries(it.map(i=>[i.id,i.gem])),ctx:c.ctx,counts:Object.fromEntries(Object.keys(c.by).map(k=>[k,c.by[k].length]))}})()"
    return run(expr, D=data, O=opts or {})


# ── Reading what the server sent ──────────────────────────────────────────

def test_products_are_read_back_with_their_brand_and_type_and_the_folded_row_is_added():
    data = wire([prod("a", brand="Acme", t1="Shoes", t2="Running"), prod("b")], other={"count": 7, "n": [70, 7, 7.0, 1, 5] + [0] * 6})
    its = run("A.items(D)", D=data)
    assert [i["id"] for i in its] == ["a", "b", ""]
    assert its[0]["brand"] == "Acme" and its[0]["type"] == "Shoes > Running" and its[0]["t1"] == "Shoes" and its[1]["type"] == ""
    assert its[2]["other"] and its[2]["title"] == "All other products (7)" and its[2]["n"][2] == 7.0


def test_a_feed_with_nothing_folded_adds_no_extra_row_and_no_data_is_an_empty_list():
    assert [i["id"] for i in run("A.items(D)", D=wire([prod("a")]))] == ["a"]
    assert run("A.items(null)") == [] and run("A.totals(null)") == [0] * NP


def test_a_short_row_from_an_older_copy_is_padded_not_broken():
    data = wire([prod("a")])
    data["products"][0] = data["products"][0][:11]                          # only the five core numbers were sent
    n = run("A.items(D)[0].n", D=data)
    assert len(n) == NP and n[2] == 100.0 and n[5:] == [0] * 6


def test_the_script_and_the_server_agree_on_how_many_numbers_a_product_has():
    from tracker import paid_products as pp
    assert run("A.NP") == pp.NP
    names = ["IMPR", "CLK", "COST", "CONV", "VAL", "ALLC", "ALLV", "ORD", "UNITS", "COGS", "PROFIT"]
    assert run("[%s]" % ",".join("A." + n for n in names)) == list(range(pp.NP))
    assert [c for c in pp.COLS] == ["impressions", "clicks", "cost", "conversions", "value", "allConv", "allValue", "orders", "units", "cogs", "profit"]


# ── Which group each product belongs to ───────────────────────────────────

def account():
    """An account that earns 2.3x overall at $40 per conversion, with one product in each group."""
    return [
        prod("star", clicks=200, cost=100, conv=5, val=500),       # 5.0x
        prod("steady", clicks=200, cost=100, conv=3, val=280),     # 2.8x: above 2.3, but not 1.25x above
        prod("drag", clicks=200, cost=100, conv=2, val=150),       # 1.5x: sells, below target
        prod("waste", clicks=300, cost=100, conv=0, val=0),        # more than a sale costs, nothing sold
        prod("thin", clicks=5, cost=3, conv=0, val=0),             # too few clicks
    ]


def test_each_product_falls_in_exactly_one_group():
    # total: cost 403, value 930 => ROAS 2.31, CPA 403/10 = 40.3
    got = classified(account())
    assert got["seg"] == {"star": "star", "steady": "steady", "drag": "drag", "waste": "waste", "thin": "thin"}
    assert got["counts"] == {"star": 1, "steady": 1, "drag": 1, "waste": 1, "thin": 1}
    assert abs(got["ctx"]["acctRoas"] - 930 / 403) < 1e-9 and got["ctx"]["target"] == got["ctx"]["acctRoas"] and got["ctx"]["autoTarget"]


def test_a_product_at_exactly_the_boundaries_lands_on_the_kinder_side():
    # target 2.0x: 2.5x is a star (>= 1.25x), 2.0x is steady, just under 2.0x is below target
    ps = [prod("a", conv=1, val=250), prod("b", conv=1, val=200), prod("c", conv=1, val=199), prod("pad", conv=1, val=10_000, cost=1000)]
    seg = classified(ps, {"target": 2})["seg"]
    assert seg["a"] == "star" and seg["b"] == "steady" and seg["c"] == "drag"


def test_a_target_the_user_sets_replaces_the_accounts_own():
    seg = classified(account(), {"target": 10})["seg"]
    assert seg["star"] == "drag" and seg["steady"] == "drag"                # nothing reaches 10x
    assert classified(account(), {"target": 1})["seg"]["drag"] == "star"    # 1.5x is above 1.25x of a 1.0x target
    assert classified(account(), {"target": 0})["ctx"]["autoTarget"]        # zero or junk means automatic
    assert classified(account(), {"target": "x"})["ctx"]["autoTarget"]


def test_wasted_spend_needs_both_enough_clicks_and_enough_spend():
    ps = [prod("few", clicks=10, cost=500, conv=0), prod("cheap", clicks=300, cost=5, conv=0), prod("both", clicks=300, cost=500, conv=0),
          prod("sells", clicks=300, cost=100, conv=5, val=500)]
    seg = classified(ps)["seg"]                                             # default: 25 clicks, one typical sale's cost (100/5 = $20)
    assert seg["few"] == "thin" and seg["cheap"] == "thin" and seg["both"] == "waste"
    seg2 = classified(ps, {"minSpend": 1000})["seg"]
    assert seg2["both"] == "thin"                                           # the user raised the bar above what it spent
    seg3 = classified(ps, {"minClicks": 5, "minSpend": 0})["seg"]
    assert seg3["few"] == "waste" and seg3["cheap"] == "waste"
    exact = classified([prod("edge", clicks=300, cost=200, conv=0), prod("sells", clicks=300, cost=100, conv=5, val=500)], {"minSpend": 200})["seg"]
    assert exact["edge"] == "waste"                                         # spending exactly the threshold counts
    just = classified([prod("edge", clicks=300, cost=199.99, conv=0), prod("sells", clicks=300, cost=100, conv=5, val=500)], {"minSpend": 200})["seg"]
    assert just["edge"] == "thin"


def test_a_product_that_sold_is_never_called_thin_or_wasted_for_having_few_clicks():
    ps = [prod("lucky", clicks=3, cost=2, conv=1, val=60), prod("pad", clicks=500, cost=100, conv=5, val=100)]
    assert classified(ps)["seg"]["lucky"] == "star"


def test_without_conversion_value_products_are_judged_on_cost_per_conversion():
    ps = [prod("cheap", clicks=100, cost=10, conv=5), prod("mid", clicks=100, cost=85, conv=5), prod("dear", clicks=100, cost=200, conv=5)]
    got = classified(ps)                                                    # account cost per conversion = 295/15 = 19.67
    assert not got["ctx"]["hasValue"] and got["ctx"]["target"] is None and got["ctx"]["basis"] == "conv"
    assert got["seg"] == {"cheap": "star", "mid": "steady", "dear": "drag"}  # $2, $17 and $40 per conversion against 19.67
    assert classified([prod("a", conv=2, cost=10), prod("b", conv=2, cost=10)])["seg"]["a"] == "steady"      # equal to the account: on target


def test_an_account_with_no_conversions_at_all_gets_no_stars_and_no_wasted_label_it_cannot_justify():
    got = classified([prod("a", clicks=300, cost=50), prod("b", clicks=2, cost=1)])
    assert got["counts"]["star"] == 0 and got["counts"]["steady"] == 0 and got["counts"]["drag"] == 0
    assert got["seg"]["b"] == "thin"
    assert got["ctx"]["hasConv"] is False                                   # the page tells the user to check tracking


def test_an_underfunded_star_earns_at_least_twice_its_share_of_the_budget():
    ps = [prod("gem", clicks=100, cost=10, conv=10, val=900), prod("fair", clicks=100, cost=500, conv=10, val=1500),
          prod("pad", clicks=100, cost=490, conv=1, val=100)]
    gem = classified(ps)["gem"]
    assert gem["gem"] is True and gem["fair"] is False                      # gem: 9% of revenue... is more than twice 1% of spend; fair is not
    near = [prod("near", clicks=100, cost=100, conv=10, val=1000), prod("pad", clicks=100, cost=100, conv=10, val=500),
            prod("pad2", clicks=100, cost=100, conv=10, val=500)]                  # 50% of revenue on 33% of spend: a star, but not underfunded
    got = classified(near)
    assert got["seg"]["near"] == "star" and got["gem"]["near"] is False


def test_the_folded_row_is_not_graded_as_a_product():
    got = classified([prod("a", conv=2, val=100)], other={"count": 50, "n": [500, 50, 500.0, 0, 0] + [0] * 6})
    assert "" not in got["seg"] and sum(got["counts"].values()) == 1


def test_segment_totals_add_up_to_the_feed():
    data = wire(account())
    t = run("(()=>{const it=A.items(D);A.classify(it,A.totals(D),{});return A.segTotals(it)})()", D=data)
    assert sum(v["count"] for v in t.values()) == 5
    assert round(sum(v["n"][COST] for v in t.values()), 4) == round(sum(p["n"][COST] for p in account()), 4)
    assert t["waste"]["n"][COST] == 100


# ── The 80/20 curve ───────────────────────────────────────────────────────

def test_the_curve_says_how_few_products_make_eighty_percent():
    ps = [prod("p%d" % i, conv=1, val=v) for i, v in enumerate([600, 200, 100, 50, 30, 10, 5, 3, 1, 1])]
    pa = run("(()=>{const it=A.items(D);const c=A.classify(it,A.totals(D),{});return A.pareto(it,c.ctx)})()", D=wire(ps))
    assert pa["at80"] == 2 and pa["all"] == 10 and abs(pa["pct80"] - 0.2) < 1e-9       # 600 + 200 = 800 of 1000
    assert abs(pa["top5"] - 0.98) < 1e-9 and pa["basis"] == "value" and pa["points"][0] == {"k": 0, "share": 0}
    assert abs(pa["points"][-1]["share"] - 1.0) < 1e-9
    shares = [p["share"] for p in pa["points"]]
    assert shares == sorted(shares)                                         # a cumulative curve never goes down


def test_a_long_feed_gives_a_short_curve_that_still_ends_at_one_hundred_percent():
    ps = [prod("p%d" % i, conv=1, val=1000 - i) for i in range(901)]       # 901 does not divide evenly by the step the curve takes
    pa = run("(()=>{const it=A.items(D);return A.pareto(it,A.classify(it,A.totals(D),{}).ctx)})()", D=wire(ps))
    assert len(pa["points"]) <= 170 and abs(pa["points"][-1]["share"] - 1.0) < 1e-9 and pa["points"][-1]["k"] == 901


def test_the_curve_counts_conversions_when_there_is_no_value_and_nothing_when_nothing_sold():
    ps = [prod("a", conv=9), prod("b", conv=1)]
    pa = run("(()=>{const it=A.items(D);return A.pareto(it,A.classify(it,A.totals(D),{}).ctx)})()", D=wire(ps))
    assert pa["basis"] == "conv" and pa["at80"] == 1
    none = run("(()=>{const it=A.items(D);return A.pareto(it,A.classify(it,A.totals(D),{}).ctx)})()", D=wire([prod("a"), prod("b")]))
    assert none["count"] == 0 and none["points"] == [] and none["at80"] == 0 and none["top5"] is None


# ── Roll-ups ──────────────────────────────────────────────────────────────

def test_brands_are_totalled_biggest_spend_first_and_unset_ones_are_named():
    ps = [prod("a", cost=10, brand="Acme"), prod("b", cost=30, brand="Beta"), prod("c", cost=5, brand="Acme"), prod("d", cost=2, brand="")]
    out = run("A.rollup(A.items(D),'brand')", D=wire(ps))
    assert [(r["key"], r["n"][COST], r["count"]) for r in out] == [("Beta", 30, 1), ("Acme", 15, 2), ("(not set)", 2, 1)]
    assert out[2]["unset"] is True


def test_the_small_ones_are_folded_into_everything_else_and_nothing_is_lost():
    ps = [prod("p%d" % i, cost=100 - i, brand="B%d" % i) for i in range(15)]
    out = run("A.rollup(A.items(D),'brand',10)", D=wire(ps))
    assert len(out) == 11 and out[-1]["rest"] and out[-1]["count"] == 5 and out[-1]["key"] == "Everything else (5)"
    assert round(sum(r["n"][COST] for r in out), 6) == round(sum(p["n"][COST] for p in ps), 6)
    types = run("A.rollup(A.items(D),'type1')", D=wire([prod("a", t1="Shoes"), prod("b", t1="Shoes"), prod("c", t1="Hats")]))
    assert {r["key"]: r["count"] for r in types} == {"Shoes": 2, "Hats": 1}


# ── What moved ────────────────────────────────────────────────────────────

def test_gains_drops_new_and_vanished_products_are_found_against_the_comparison():
    cur = [prod("up", conv=2, val=300, cost=50), prod("down", conv=1, val=50, cost=50), prod("new", conv=1, val=80, cost=20), prod("same", conv=1, val=100)]
    prev = [prod("up", conv=1, val=100), prod("down", conv=3, val=250), prod("gone", conv=2, val=150, cost=30), prod("same", conv=1, val=100)]
    mv = run("(()=>{const c=A.items(C),p=A.items(P);const x=A.classify(c,A.totals(C),{});return A.movers(c,p,x.ctx,8)})()", C=wire(cur), P=wire(prev))
    assert [(r["id"], r["delta"]) for r in mv["up"]] == [("up", 200), ("new", 80)]
    assert [(r["id"], r["delta"]) for r in mv["down"]] == [("gone", -150), ("down", -200)][::-1] or \
        [(r["id"], r["delta"]) for r in mv["down"]] == [("down", -200), ("gone", -150)]
    assert mv["newCount"] == 1 and mv["goneCount"] == 1 and mv["basis"] == "value"
    assert next(r for r in mv["up"] if r["id"] == "new")["isNew"] is True


def test_there_is_nothing_to_say_about_movement_without_a_comparison():
    assert run("(()=>{const c=A.items(C);return A.movers(c,[],A.classify(c,A.totals(C),{}).ctx,8)})()", C=wire([prod("a")])) is None
    assert run("(()=>{const c=A.items(C);return A.movers(c,null,A.classify(c,A.totals(C),{}).ctx,8)})()", C=wire([prod("a")])) is None


def test_movers_are_capped_and_judged_on_conversions_when_there_is_no_value():
    cur = [prod("p%d" % i, conv=i + 5) for i in range(12)]
    prev = [prod("p%d" % i, conv=1) for i in range(12)]
    mv = run("(()=>{const c=A.items(C),p=A.items(P);return A.movers(c,p,A.classify(c,A.totals(C),{}).ctx,3)})()", C=wire(cur), P=wire(prev))
    assert len(mv["up"]) == 3 and mv["basis"] == "conv" and mv["up"][0]["id"] == "p11" and mv["down"] == []


# ── The filters behind the recommendations ────────────────────────────────

def filt(products, key, oos=None, opts=None):
    return run("(()=>{const it=A.items(D);const c=A.classify(it,A.totals(D),O);return A.filterItems(it,K,c.ctx,OOS?new Set(OOS):null).map(i=>i.id)})()",
               D=wire(products), O=opts or {}, K=key, OOS=oos)


def test_a_group_filter_returns_exactly_the_products_in_that_group():
    for key in ("star", "steady", "drag", "waste", "thin"):
        assert filt(account(), key) == [key]
    assert sorted(filt(account(), "all")) == sorted(["star", "steady", "drag", "waste", "thin"])
    assert filt(account(), "nonsense") == filt(account(), "all")            # an unknown filter shows everything, not nothing


def test_people_who_click_and_do_not_buy_are_found_but_not_the_no_sale_products():
    ps = [prod("pad", clicks=1000, conv=50, val=5000, cost=500),             # converts at 5%
          prod("leaky", clicks=400, conv=2, val=100, cost=100),              # 0.5%: under half of the account's 4.1%
          prod("ok", clicks=400, conv=20, val=1000, cost=100),
          prod("dead", clicks=400, conv=0, cost=300)]                        # no sales is the waste group, not this one
    assert filt(ps, "leak") == ["leaky"]
    few = ps + [prod("few", clicks=30, conv=0.3, val=20, cost=10)]           # a low rate over only 30 clicks (under twice the 25 needed) is noise
    assert filt(few, "leak") == ["leaky"]


def test_a_listing_that_is_shown_a_lot_and_rarely_clicked_is_found():
    ps = [prod("pad%d" % i, impr=1000, clicks=100, cost=10, conv=1, val=50) for i in range(8)] + [
        prod("dull", impr=50000, clicks=100, cost=10, conv=1, val=50), prod("tiny", impr=300, clicks=0, cost=0)]
    got = filt(ps, "window")
    assert got == ["dull"]                                                   # the 300-impression one is too small to judge
    small = [prod("pad%d" % i, impr=200, clicks=20, cost=5, conv=1, val=20) for i in range(8)] + [prod("low", impr=400, clicks=1, cost=1, conv=0)]
    assert filt(small, "window") == []                                       # nothing here was shown 500 times: too little to say it is ignored


def test_only_products_that_are_out_of_stock_and_still_spending_are_flagged():
    ps = [prod("a", cost=50), prod("b", cost=0, clicks=0), prod("c", cost=10)]
    assert filt(ps, "oos", ["a", "b"]) == ["a"]                              # b is out of stock but costs nothing
    assert filt(ps, "oos", None) == [] and filt(ps, "oos", []) == []        # unknown stock flags nothing


# ── Recommendations ───────────────────────────────────────────────────────

def acts(products, oos=None, opts=None):
    return run("(()=>{const it=A.items(D);const c=A.classify(it,A.totals(D),O);return A.actions(it,c,OOS)})()", D=wire(products), O=opts or {}, OOS=oos)


def test_each_recommendation_counts_the_same_products_its_filter_opens():
    ps = account() + [prod("oos1", cost=40, conv=1, val=100)]
    out = acts(ps, oos=["oos1"])
    for a in out:
        assert a["count"] == len(filt(ps, a["key"], ["oos1"])), a["key"]
        assert a["count"] > 0


def test_nothing_to_do_gives_no_recommendations_and_money_is_summed():
    assert acts([prod("a", clicks=100, cost=50, conv=5, val=250), prod("b", clicks=100, cost=50, conv=5, val=250)]) == []
    waste = next(a for a in acts(account()) if a["key"] == "waste")
    assert waste["count"] == 1 and waste["cost"] == 100 and waste["tone"] == "bad" and waste["amountKind"] == "cost"


def test_the_most_urgent_recommendations_come_first():
    ps = account() + [prod("oos1", cost=40, conv=1, val=100)]
    keys = [a["key"] for a in acts(ps, oos=["oos1"])]
    assert keys.index("waste") < keys.index("drag") and keys.index("oos") < keys.index("drag")      # losing money before trimming
    tones = [a["tone"] for a in acts(ps, oos=["oos1"])]
    assert tones == sorted(tones, key=["bad", "good", "warn", "info"].index)


# ── Headline numbers ──────────────────────────────────────────────────────

def test_the_headline_numbers_come_from_the_feed_totals_not_the_shown_rows():
    data = wire(account(), other={"count": 40, "n": [4000, 400, 400.0, 10, 600.0] + [0] * 6})
    k = run("(()=>{const it=A.items(D);const t=A.totals(D);return A.kpis(t,null,A.classify(it,t,{}))})()", D=data)
    assert k["spend"] == 803 and k["revenue"] == 1530 and k["conv"] == 20
    assert abs(k["roas"] - 1530 / 803) < 1e-9 and abs(k["cpa"] - 803 / 20) < 1e-9
    assert k["waste"] == 100 and abs(k["wastePct"] - 100 / 803) < 1e-9       # the folded products are not graded, so they are not counted wasted
    assert k["selling"] == 3 and k["products"] == 5


# ── Table helpers ─────────────────────────────────────────────────────────

def test_sorting_puts_nothing_last_whichever_way_and_breaks_ties_by_title():
    ps = [prod("a", cost=10, conv=0, title="Zed"), prod("b", cost=10, conv=1, val=50, title="Alpha"), prod("c", cost=20, conv=2, val=100, title="Mid")]
    ids = lambda key, d: run("A.sortItems(A.items(D),K,DIR).map(i=>i.id)", D=wire(ps), K=key, DIR=d)
    assert ids("cost", -1) == ["c", "b", "a"]                                # equal spend: Alpha before Zed
    assert ids("cost", 1) == ["b", "a", "c"]
    assert ids("roas", 1)[0] == "a" and ids("roas", -1)[-1] == "a"           # a spent and earned nothing: ROAS is a real zero
    assert ids("cpa", -1)[-1] == "a" and ids("cpa", 1)[-1] == "a"            # no conversions, no cost per conversion: last both ways
    assert ids("title", 1) == ["b", "c", "a"]
    many = [prod("n%d" % i, cost=5, conv=0, title="N%d" % i) for i in range(4)] + [prod("s%d" % i, cost=10 + i, conv=1, val=5, title="S%d" % i) for i in range(4)]
    order = lambda d: run("A.sortItems(A.items(D),'cpa',DIR).map(i=>i.id)", D=wire(many), DIR=d)
    for d in (-1, 1):
        assert all(x.startswith("n") for x in order(d)[-4:]), order(d)      # every product with no cost per conversion is last, either way
    many.reverse()                                                           # and whichever order they arrive in
    for d in (-1, 1):
        assert all(x.startswith("n") for x in order(d)[-4:]), order(d)


def test_search_needs_every_word_and_looks_in_title_id_brand_and_type():
    ps = [prod("sku-1", brand="Acme", t1="Shoes", title="Blue Runner"), prod("sku-2", brand="Beta", t1="Hats", title="Blue Cap")]
    s = lambda q: run("A.search(A.items(D),Q).map(i=>i.id)", D=wire(ps), Q=q)
    assert s("blue") == ["sku-1", "sku-2"] and s("blue runner") == ["sku-1"] and s("ACME") == ["sku-1"] and s("hats") == ["sku-2"]
    assert s("sku-2") == ["sku-2"] and s("") == ["sku-1", "sku-2"] and s("   ") == ["sku-1", "sku-2"] and s("blue zebra") == []


def test_the_csv_is_safe_to_open_in_a_spreadsheet_and_carries_every_group():
    ps = [prod("=cmd", title="=HYPERLINK(1)", cost=5, clicks=40, conv=0), prod("b", title='Has "quotes", and commas', conv=1, val=10, cost=2)]
    out = run("(()=>{const it=A.items(D);A.classify(it,A.totals(D),{});return A.csv(it,'USD',null)})()", D=wire(ps))
    lines = out.split("\r\n")
    assert lines[0].startswith("Item id,Product,Brand,Product type,Group,Spend,Revenue,ROAS")
    assert "'=cmd,'=HYPERLINK(1)" in lines[1]                                # a leading = would run as a formula
    assert '"Has ""quotes"", and commas"' in lines[2]


# ── Words ─────────────────────────────────────────────────────────────────

def lines(products, cur=None, oos=None, opts=None):
    expr = """(()=>{const it=A.items(D);const c=A.classify(it,A.totals(D),O);const pa=A.pareto(it,c.ctx);
      const mv=P?A.movers(it,A.items(P),c.ctx,8):null;return A.insights(it,c,pa,mv,OOS,'USD')})()"""
    return run(expr, D=wire(products), O=opts or {}, OOS=oos, P=wire(cur) if cur else None)


def test_the_lines_say_what_the_numbers_say_and_never_use_an_em_dash():
    ps = account() + [prod("oos1", cost=40, conv=1, val=100)]
    out = lines(ps, oos=["oos1"])
    text = " ".join(x["text"] for x in out)
    assert "1 product took $100 (" in text and "sold nothing" in text
    assert "1 out-of-stock product still got $40" in text
    assert "make 80% of your revenue" in text
    assert "—" not in text
    assert all(x["tone"] in ("good", "warn", "bad", "info", "mute", "flat") for x in out)


def test_a_feed_with_nothing_to_report_says_nothing_and_missing_tracking_is_called_out():
    assert lines([]) == []
    out = lines([prod("a", clicks=300, cost=50), prod("b", clicks=200, cost=40)])
    assert any("No conversions were recorded" in x["text"] for x in out)
    out2 = lines([prod("a", conv=5, cost=50), prod("b", conv=3, cost=40)])
    assert any("does not report conversion value" in x["text"] for x in out2)


def test_when_most_products_are_too_new_to_judge_the_line_says_so():
    ps = [prod("t%d" % i, clicks=2, cost=1) for i in range(8)] + [prod("a", clicks=200, cost=50, conv=5, val=250), prod("b", clicks=200, cost=50, conv=5, val=250)]
    out = lines(ps)
    assert any("too few to judge" in x["text"] and x["text"].startswith("80%") for x in out)


def test_biggest_gain_and_drop_are_named_against_the_comparison():
    cur = [prod("up", title="Big Winner", conv=5, val=500), prod("down", title="Sad Loser", conv=1, val=50)]
    prev = [prod("up", title="Big Winner", conv=1, val=100), prod("down", title="Sad Loser", conv=4, val=400)]
    text = " ".join(x["text"] for x in lines(cur, cur=prev))
    assert "Biggest gain: Big Winner, +$400" in text and "Biggest drop: Sad Loser, −$350" in text


# ── How each product is performing ────────────────────────────────────────

def board(products, cmp=None, key="value", n=20):
    expr = "(()=>{const it=A.items(D);A.classify(it,A.totals(D),{});const pv=P?A.items(P):null;return A.leaderboard(it,pv,K,N)})()"
    out = run(expr, D=wire(products), P=wire(cmp) if cmp else None, K=key, N=n)
    return out


def test_products_are_ranked_best_first_by_the_measure_chosen_with_their_share():
    ps = [prod("a", cost=50, conv=1, val=100), prod("b", cost=20, conv=5, val=400), prod("c", cost=30, conv=2, val=250), prod("zero", cost=5)]
    rev = board(ps, key="value")
    assert [r["it"]["id"] for r in rev["rows"]] == ["b", "c", "a"] and [r["rank"] for r in rev["rows"]] == [1, 2, 3]   # a product earning nothing is not ranked
    assert rev["additive"] is True and abs(rev["rows"][0]["share"] - 400 / 750) < 1e-9 and rev["total"] == 750
    spend = board(ps, key="cost")
    assert [r["it"]["id"] for r in spend["rows"]] == ["a", "c", "b", "zero"]
    roas = board(ps, key="roas")
    assert [r["it"]["id"] for r in roas["rows"]] == ["b", "c", "a", "zero"] and roas["additive"] is False and roas["rows"][0]["share"] is None
    assert abs(roas["rows"][0]["v"] - 20.0) < 1e-9


def test_the_board_is_capped_and_a_rate_needs_spend_to_mean_anything():
    ps = [prod("p%d" % i, cost=10 + i, conv=1, val=100 + i) for i in range(30)] + [prod("free", cost=0, clicks=5, conv=0)]
    assert len(board(ps, n=20)["rows"]) == 20 and board(ps, n=20)["rows"][0]["it"]["id"] == "p29"
    assert "free" not in [r["it"]["id"] for r in board(ps, key="roas", n=50)["rows"]]       # no spend: a ROAS of nothing
    assert board([], key="value")["rows"] == [] and board([prod("a")], key="value")["rows"] == []
    for rate in ("roas", "cpc", "ctr"):
        assert "free" not in [r["it"]["id"] for r in board(ps, key=rate, n=50)["rows"]], rate      # nothing was spent: a rate of it means nothing
    default = run("(()=>{const it=A.items(D);A.classify(it,A.totals(D),{});return A.leaderboard(it,null,'cost').rows.length})()", D=wire(ps))
    assert default == 20                                                    # twenty when no count is asked for


def test_each_product_carries_what_it_was_before_and_how_that_changed():
    cur = [prod("up", cost=10, conv=2, val=300), prod("new", cost=10, conv=1, val=50)]
    prev = [prod("up", cost=10, conv=1, val=100)]
    rows = {r["it"]["id"]: r for r in board(cur, prev, key="value")["rows"]}
    assert rows["up"]["prev"] == 100 and rows["up"]["change"]["text"] == "+200%" and rows["up"]["change"]["tone"] == "good"
    assert rows["new"]["prev"] is None and rows["new"]["change"] is None


def test_a_days_series_is_read_by_item_id_in_any_case_and_missing_means_not_read():
    ser = {"days": ["2026-10-01", "2026-10-02"], "ids": ["sku-1"], "rows": {"sku-1": [[100, 10, 5.0, 1.0, 50.0] + [0] * 6, [0] * 11]}}
    got = lambda key, pid="SKU-1": run("A.seriesValues(S,ID,K)", S=ser, ID=pid, K=key)
    assert got("cost") == [5.0, 0] and got("value") == [50.0, 0] and got("roas") == [10.0, None] and got("ctr") == [0.1, None]
    assert got("cost", "other") is None
    assert run("A.seriesValues(null,'a','cost')") is None and run("A.seriesValues({},'a','cost')") is None


def test_the_trend_chart_does_not_call_a_meta_sales_trend_all_products():
    """On Meta the trend is every Sales campaign by day, while the table beside it is only the catalog products: the subtitle must say so."""
    page = open(os.path.join(ROOT, "static", "js", "product_analysis.js"), encoding="utf-8").read()
    chart = page[page.index("Each day"):][:900]
    assert 'S.acct.platform==="meta"' in chart and "all Sales campaigns together" in chart and "all products together" in chart
    assert chart.index('S.acct.platform==="meta"') < chart.index("all products together")
