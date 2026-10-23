"""The Google | Meta | Both switch's logic (static/js/pm_platform.js), run in Node.

What is pinned: which account on the other platform counts as "the same client" (a wrong match would add two clients'
money together, so an unclear one must be no match); how two answers (a Google account's and a Meta account's) become one
(campaigns side by side, a column missing only when neither has it, products matched on the item id with Google's title
kept, the oldest read time and the latest wait shown); and how a combined account's request is split in two, what
happens when only one half can be read, and that a single account's request passes through untouched. The file is
executed, not read.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "static", "js", "pm_platform.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

G, M = "1234567890", "act_1000000001"


def run(expr, pre=""):
    code = ("global.window=undefined;const P=require(%s);%s;(async()=>{const out=(%s);console.log(JSON.stringify(await out));})()"
            % (json.dumps(SCRIPT), pre, expr))
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def acct(name, currency="USD", id="x"):
    return {"id": id, "name": name, "currency": currency}


# ── Ids ─────────────────────────────────────────────────────────────────────

def test_ids_are_told_apart():
    assert run("[P.isMeta(%r),P.isMeta(%r),P.isMeta('act_'),P.isMeta('act_12ab'),P.isMeta(null)]" % (M, G)) == [True, False, False, False, False]
    assert run("[P.isCombo(%r),P.isCombo(%r),P.isCombo(%r),P.isCombo(%r)]" % (G + "+" + M, G, M + "+" + G, G + "+" + M + "+x")) == [True, False, False, False]
    assert run("[P.platformOfId(%r),P.platformOfId(%r),P.platformOfId(%r)]" % (G, M, G + "+" + M)) == ["google", "meta", "both"]
    assert run("P.partsOf(%r)" % (G + "+" + M)) == [G, M] and run("P.partsOf(%r)" % G) == [G]
    assert run("[P.singleId(%r),P.singleId(%r),P.singleId(%r),P.singleId(%r)]" % (G, M, G + "+" + M, "12345")) == [True, True, False, False]
    assert run("[P.validPlatform('google'),P.validPlatform('meta'),P.validPlatform('both'),P.validPlatform('tiktok'),P.validPlatform('')]") == [True, True, True, False, False]
    assert run("P.comboId(%r,%r)" % (G, M)) == G + "+" + M
    assert run("P.fmtId(%r)" % G) == "123-456-7890" and run("P.fmtId(%r)" % M) == M


# ── The same client on the other platform ───────────────────────────────────

def test_the_same_name_is_the_same_client():
    assert run("P.counterpart(a,[b,c])", "const a=%s,b=%s,c=%s" % (json.dumps(acct("Beta Bionics")), json.dumps(acct("Beta Bionics", id="m1")), json.dumps(acct("Other", id="m2"))))["id"] == "m1"
    assert run("P.counterpart(a,[b])", "const a=%s,b=%s" % (json.dumps(acct("Beta Bionics (US)")), json.dumps(acct("beta-bionics", id="m1"))))["id"] == "m1"


def test_a_name_that_holds_the_other_counts_only_when_exactly_one_does():
    a = acct("Eventgroove")
    one = [acct("US - Eventgroove Products", id="m1"), acct("Unrelated", id="m2")]
    two = [acct("US - Eventgroove Products", id="m1"), acct("Eventgroove Events", id="m2")]
    assert run("P.counterpart(a,o)", "const a=%s,o=%s" % (json.dumps(a), json.dumps(one)))["id"] == "m1"
    assert run("P.counterpart(a,o)", "const a=%s,o=%s" % (json.dumps(a), json.dumps(two))) is None          # unclear: no match, not a guess


def test_two_exact_matches_are_unclear_too_and_so_is_a_different_currency():
    a = acct("Acme")
    assert run("P.counterpart(a,o)", "const a=%s,o=%s" % (json.dumps(a), json.dumps([acct("Acme", id="m1"), acct("ACME", id="m2")]))) is None
    assert run("P.counterpart(a,o)", "const a=%s,o=%s" % (json.dumps(a), json.dumps([acct("Acme", "GBP", id="m1")]))) is None


def test_a_name_too_short_to_trust_is_never_matched():
    assert run("P.counterpart(a,o)", "const a=%s,o=%s" % (json.dumps(acct("AB")), json.dumps([acct("AB", id="m1")]))) is None
    assert run("P.counterpart(a,o)", "const a=%s,o=%s" % (json.dumps(acct("OIA")), json.dumps([acct("OIA Imaging Group", id="m1")]))) is None      # 3 letters: exact only


# ── Performance rows ────────────────────────────────────────────────────────

def perf(camps, rows, missing=(7, 9), shares=None, days=("2026-10-01", "2026-10-02")):
    return {"start": days[0], "end": days[-1], "days": list(days), "campaigns": [{"id": c, "name": c, "type": "SEARCH", "status": "ENABLED", "budget": 0} for c in camps],
            "rows": rows, "missing": list(missing), "sis": {"account": 0.5, "campaigns": {}}, "shares": shares or {"search_impression_share": 0.4}}


def test_two_ranges_become_one_with_the_second_ones_campaigns_after_the_first():
    a = perf(["g1", "g2"], [[0, 0, 100], [1, 1, 50]])
    b = perf(["m1"], [[0, 0, 7], [0, 1, 8]], shares={"reach": 900, "frequency": 1.5})
    out = run("P.mergePerf(a,b)", "const a=%s,b=%s" % (json.dumps(a), json.dumps(b)))
    assert [c["id"] for c in out["campaigns"]] == ["g1", "g2", "m1"]
    assert out["rows"] == [[0, 0, 100], [1, 1, 50], [2, 0, 7], [2, 1, 8]]                  # Meta's campaign index moves past Google's
    assert out["days"] == a["days"] and out["sis"]["account"] == 0.5
    assert out["shares"] == {"search_impression_share": 0.4, "reach": 900, "frequency": 1.5}
    assert a["rows"] == [[0, 0, 100], [1, 1, 50]]                                            # the inputs are not changed


def test_a_column_is_missing_only_when_neither_platform_has_it():
    a, b = perf(["g"], [], missing=(7, 9, 11)), perf(["m"], [], missing=(9, 11, 12))
    assert run("P.mergePerf(a,b).missing", "const a=%s,b=%s" % (json.dumps(a), json.dumps(b))) == [9, 11]


def test_one_side_alone_is_returned_as_it_is():
    a = perf(["g"], [[0, 0, 1]])
    assert run("P.mergePerf(a,null)", "const a=%s" % json.dumps(a)) == a
    assert run("P.mergePerf(null,a)", "const a=%s" % json.dumps(a)) == a


def test_the_merged_answer_says_the_oldest_read_and_the_longest_wait():
    acct_ = {"id": G + "+" + M, "name": "x"}
    g = {"current": perf(["g"], []), "compare": None, "stale": None, "fetchedAt": "2026-10-14T10:00:00+00:00", "blockedUntil": None}
    m = {"current": perf(["m"], []), "compare": perf(["m"], []), "stale": "2026-10-13T08:00:00+00:00", "fetchedAt": "2026-10-14T09:00:00+00:00", "blockedUntil": 5000}
    out = run("P.mergePerfResponse(acct,g,m)", "const acct=%s,g=%s,m=%s" % (json.dumps(acct_), json.dumps(g), json.dumps(m)))
    assert out["fetchedAt"] == "2026-10-14T09:00:00+00:00" and out["stale"] == "2026-10-13T08:00:00+00:00" and out["blockedUntil"] == 5000
    assert out["account"] == acct_ and out["compare"] is not None and len(out["current"]["campaigns"]) == 2


def test_a_time_with_a_different_offset_is_compared_as_a_time():
    assert run("P.oldest('2026-10-14T10:00:00+05:30','2026-10-14T05:00:00+00:00')") == "2026-10-14T10:00:00+05:30"     # 04:30 UTC is older than 05:00 UTC
    assert run("P.oldest(null,'2026-10-14T05:00:00+00:00')") == "2026-10-14T05:00:00+00:00"


def test_device_and_network_rows_add_by_key_and_the_hours_come_from_meta():
    g = {"device": [["MOBILE", 10, 1], ["DESKTOP", 5, 1]], "network": [["SEARCH", 7, 1]], "dowHour": [[0, 9, 3]], "missing": [7, 9], "stale": None, "fetchedAt": "2026-10-14T10:00:00+00:00"}
    m = {"device": [["MOBILE", 4, 2]], "network": [["FACEBOOK", 9, 9]], "dowHour": [], "hour": [[9, 1, 1]], "missing": [9, 11], "stale": None, "fetchedAt": "2026-10-14T09:00:00+00:00"}
    out = run("P.mergeExtras(g,m)", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m)))
    assert sorted(out["device"]) == [["DESKTOP", 5, 1], ["MOBILE", 14, 3]]
    assert sorted(out["network"]) == [["FACEBOOK", 9, 9], ["SEARCH", 7, 1]]
    assert out["dowHour"] == [[0, 9, 3]] and out["hour"] == [[9, 1, 1]] and out["missing"] == [9]
    assert out["fetchedAt"] == "2026-10-14T09:00:00+00:00"


# ── Products ────────────────────────────────────────────────────────────────

NP = 11


def nums(impr=0, clk=0, cost=0.0, conv=0.0, val=0.0):
    return [impr, clk, cost, conv, val] + [0] * (NP - 5)


def feed(products, missing=(), other=None, daily=None, oos=None, series=None):
    """Server-shaped products: products = [(id, title, brand, [t1,t2,t3], numbers)]."""
    dims = [""]

    def di(s):
        if s not in dims:
            dims.append(s)
        return dims.index(s)
    rows = [[i, t, di(b), di(ty[0]), di(ty[1]), di(ty[2])] + n for i, t, b, ty, n in products]
    total = [sum(r[6 + k] for r in rows) for k in range(NP)]
    return {"start": "2026-10-01", "end": "2026-10-02", "dims": dims, "products": rows,
            "other": other or {"count": 0, "n": [0] * NP}, "total": total, "missing": list(missing),
            "daily": daily, "outOfStock": oos, "series": series}


def test_products_are_matched_on_the_item_id_and_their_numbers_add():
    g = feed([("sku-1", "Trail Runner", "Northwind", ["Shoes", "Run", ""], nums(1000, 100, 50.0, 2, 200.0)),
              ("sku-2", "Lamp", "Ember", ["Home", "", ""], nums(500, 20, 10.0, 0, 0.0))])
    m = feed([("sku-1", "sku-1", "", ["", "", ""], nums(300, 30, 25.0, 1, 90.0)),
              ("sku-3", "sku-3", "", ["", "", ""], nums(100, 5, 99.0, 0, 0.0))], missing=(8, 9, 10))
    out = run("P.mergeProducts(g,m)", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m)))
    by = {p[0]: p for p in out["products"]}
    assert set(by) == {"sku-1", "sku-2", "sku-3"}
    assert by["sku-1"][6:11] == [1300, 130, 75.0, 3, 290.0]
    assert by["sku-1"][1] == "Trail Runner" and out["dims"][by["sku-1"][2]] == "Northwind" and out["dims"][by["sku-1"][3]] == "Shoes"
    assert by["sku-3"][1] == "sku-3"                                                     # Meta only: the id is the title


def test_products_come_out_biggest_spend_first_and_the_totals_add():
    g = feed([("a", "A", "", ["", "", ""], nums(0, 0, 10.0, 0, 0.0)), ("b", "B", "", ["", "", ""], nums(0, 0, 30.0, 0, 0.0))])
    m = feed([("a", "a", "", ["", "", ""], nums(0, 0, 25.0, 0, 0.0)), ("c", "c", "", ["", "", ""], nums(0, 0, 20.0, 0, 0.0))])
    out = run("P.mergeProducts(g,m)", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m)))
    assert [p[0] for p in out["products"]] == ["a", "b", "c"] and out["total"][2] == 85.0


def test_ties_in_spend_fall_back_to_value_then_clicks_then_id():
    g = feed([("z", "Z", "", ["", "", ""], nums(0, 5, 10.0, 0, 5.0)), ("y", "Y", "", ["", "", ""], nums(0, 9, 10.0, 0, 5.0)),
              ("x", "X", "", ["", "", ""], nums(0, 5, 10.0, 0, 50.0)), ("w", "W", "", ["", "", ""], nums(0, 5, 10.0, 0, 5.0))])
    merged = run("P.mergeProducts(g,e)", "const g=%s,e=%s" % (json.dumps(g), json.dumps(feed([("v", "V", "", ["", "", ""], nums(0, 0, 0.0, 0, 0.0))]))))
    assert [p[0] for p in merged["products"]] == ["x", "y", "w", "z", "v"]


def test_only_a_column_neither_feed_has_is_missing_and_the_days_and_stock_merge():
    days = lambda v: {"days": ["2026-10-01", "2026-10-02"], "rows": [[0, 0, v] + [0] * 8, [0, 0, v * 2] + [0] * 8]}
    g = feed([("a", "A", "", ["", "", ""], nums(1, 1, 1.0))], missing=(), daily=days(5), oos=["a"])
    m = feed([("a", "a", "", ["", "", ""], nums(1, 1, 1.0))], missing=(8, 9, 10), daily=days(7), oos=None)
    out = run("P.mergeProducts(g,m)", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m)))
    assert out["missing"] == [] and out["daily"]["rows"][0][2] == 12 and out["daily"]["rows"][1][2] == 24
    assert out["outOfStock"] == ["a"]
    both_unknown = run("P.mergeProducts(g,m).outOfStock", "const g=%s,m=%s" % (json.dumps(feed([], oos=None)), json.dumps(feed([], oos=None))))
    assert both_unknown is None


def test_each_products_days_are_added_where_both_platforms_have_the_product():
    mk = lambda ids, v: {"days": ["2026-10-01"], "ids": ids, "rows": {i: [[0, 0, v] + [0] * 8] for i in ids}}
    g = feed([], series=mk(["a", "b"], 3))
    m = feed([], series=mk(["b", "c"], 4))
    out = run("P.mergeProducts(g,m).series", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m)))
    assert out["ids"] == ["a", "b", "c"] and out["rows"]["b"][0][2] == 7 and out["rows"]["a"][0][2] == 3 and out["rows"]["c"][0][2] == 4
    only = run("P.mergeProducts(g,m).series", "const g=%s,m=%s" % (json.dumps(feed([], series=mk(['a'], 3))), json.dumps(feed([], series=None))))
    assert only["ids"] == ["a"]


def mixed(feed_, by, campaigns=(), top=None):
    feed_["mix"] = {"campaigns": [list(c) for c in campaigns], "by": by, "top": top or {}}
    return feed_


def test_the_campaign_mix_adds_across_platforms_and_keeps_googles_campaigns():
    g = mixed(feed([("a", "A", "", ["", "", ""], nums(1, 1, 5.0)), ("b", "B", "", ["", "", ""], nums(1, 1, 2.0))]),
              {"a": [3.0, 0, 2.0, 0, 0], "b": [2.0, 0, 0, 0, 0]}, [("Shop", "Shopping", "Manual CPC", "adjustable", 5.0), ("Max", "Performance Max", "Maximize conversion value", "auto", 2.0)], {"a": 0, "b": 0})
    m = mixed(feed([("a", "a", "", ["", "", ""], nums(1, 1, 4.0)), ("c", "c", "", ["", "", ""], nums(1, 1, 1.0))]), {"a": [0, 0, 0, 0, 4.0], "c": [0, 0, 0, 0, 1.0]})
    out = run("P.mergeProducts(g,m).mix", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m)))
    assert out["by"] == {"a": [3.0, 0, 2.0, 0, 4.0], "b": [2.0, 0, 0, 0, 0], "c": [0, 0, 0, 0, 1.0]}
    assert [c[0] for c in out["campaigns"]] == ["Shop", "Max"] and out["top"] == {"a": 0, "b": 0}


def test_a_second_feeds_campaign_indexes_are_shifted_past_the_first_feeds():
    camp = lambda n: (n, "Shopping", "Manual CPC", "adjustable", 1.0)
    g = mixed(feed([("a", "A", "", ["", "", ""], nums(1, 1, 1.0))]), {"a": [1.0, 0, 0, 0, 0]}, [camp("G1"), camp("G2")], {"a": 1})
    m = mixed(feed([("a", "a", "", ["", "", ""], nums(1, 1, 1.0)), ("z", "z", "", ["", "", ""], nums(1, 1, 1.0))]), {"a": [1.0, 0, 0, 0, 0], "z": [1.0, 0, 0, 0, 0]}, [camp("M1")], {"a": 0, "z": 0})
    out = run("P.mergeProducts(g,m).mix", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m)))
    assert [c[0] for c in out["campaigns"]] == ["G1", "G2", "M1"] and out["top"] == {"a": 1, "z": 2}          # a keeps the first feed's campaign, z points at the second feed's


def test_a_feed_whose_campaign_mix_could_not_be_read_makes_the_whole_mix_unknown():
    g = feed([("a", "A", "", ["", "", ""], nums(1, 1, 1.0))])
    g["mix"] = None
    m = mixed(feed([("a", "a", "", ["", "", ""], nums(1, 1, 1.0))]), {"a": [0, 0, 0, 0, 1.0]})
    assert run("P.mergeProducts(g,m).mix", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m))) is None
    empty_meta = mixed(feed([]), {})
    ok = mixed(feed([("a", "A", "", ["", "", ""], nums(1, 1, 1.0))]), {"a": [1.0, 0, 0, 0, 0]})
    out = run("P.mergeProducts(g,m).mix", "const g=%s,m=%s" % (json.dumps(ok), json.dumps(empty_meta)))
    assert out["by"] == {"a": [1.0, 0, 0, 0, 0]}                                                             # a feed with no products does not spoil it
    nothing = feed([])
    nothing["mix"] = None                                                                                    # ... even when its own read failed
    out = run("P.mergeProducts(g,m).mix", "const g=%s,m=%s" % (json.dumps(ok), json.dumps(nothing)))
    assert out["by"] == {"a": [1.0, 0, 0, 0, 0]}


def test_a_note_on_either_feed_reaches_the_merged_one():
    g = feed([("a", "A", "", ["", "", ""], nums(1, 1, 1.0))])
    m = dict(feed([("a", "a", "", ["", "", ""], nums(1, 1, 1.0))]), note="Meta credits purchases only in part")
    assert run("P.mergeProducts(g,m).note", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m))) == "Meta credits purchases only in part"
    assert run("(P.mergeProducts(g,g).note===undefined?null:1)", "const g=%s" % json.dumps(g)) is None


def test_the_folded_other_products_add_up():
    g = feed([], other={"count": 3, "n": nums(0, 0, 10.0)})
    m = feed([], other={"count": 2, "n": nums(0, 0, 5.0)})
    out = run("P.mergeProducts(g,m).other", "const g=%s,m=%s" % (json.dumps(g), json.dumps(m)))
    assert out["count"] == 5 and out["n"][2] == 15.0


# ── The request ─────────────────────────────────────────────────────────────

FETCH = """
const calls=[]; global.__calls=calls;
global.fetch=(url)=>{calls.push(url); return Promise.resolve(RESP(url));};
"""


def fetch_pre(body):
    return ("const mk=(status,j)=>({ok:status<400,status,json:()=>Promise.resolve(j)});" + FETCH.replace("RESP(url)", body))


def test_a_single_accounts_request_is_sent_once_as_it_is():
    pre = fetch_pre("mk(200,{account:{id:'x'}})")
    out = run("P.fetchJson('/api/p/performance?account=%s&start=a&end=b',false,{}).then(j=>({j,calls}))" % G, pre)
    assert out["calls"] == ["/api/p/performance?account=%s&start=a&end=b" % G]
    assert run("P.fetchJson('/x?account=%s',true,{}).then(()=>calls)" % M, pre) == ["/x?account=%s&refresh=1" % M]


def test_a_combined_request_is_two_requests_one_for_each_platform_and_merged():
    pre = fetch_pre("mk(200,{v:url.indexOf('act_')>0?'meta':'google'})")
    out = run("P.fetchJson('/x/performance?account=%s%%2B%s&start=a',false,{merge:(u,g,m)=>({g:g.v,m:m.v,u})}).then(j=>({j,calls}))" % (G, M), pre)
    assert sorted(out["calls"]) == sorted(["/x/performance?account=%s&start=a" % G, "/x/performance?account=%s&start=a" % M])
    assert out["j"]["g"] == "google" and out["j"]["m"] == "meta"
    refreshed = run("P.fetchJson('/x?account=%s%%2B%s',true,{merge:(u,g,m)=>({})}).then(()=>calls)" % (G, M), pre)
    assert all(u.endswith("&refresh=1") for u in refreshed) and len(refreshed) == 2


def test_when_one_platform_cannot_be_read_the_other_is_shown_with_a_note():
    pre = fetch_pre("url.indexOf('act_')>0?mk(502,{error:'Meta answered 500.'}):mk(200,{account:{id:'g'},current:1})")
    out = run("P.fetchJson('/x?account=%s%%2B%s',false,{acct:{id:'combo',name:'n'},merge:()=>({})})" % (G, M), pre)
    assert out["current"] == 1 and out["account"] == {"id": "combo", "name": "n"}
    assert out["partial"].startswith("Meta Ads could not be read") and "Meta answered 500." in out["partial"]
    pre2 = fetch_pre("url.indexOf('act_')>0?mk(200,{account:{id:'m'},current:2}):mk(429,{error:'slow down',cooldown:true})")
    out2 = run("P.fetchJson('/x?account=%s%%2B%s',false,{acct:{id:'c'},merge:()=>({})})" % (G, M), pre2)
    assert out2["current"] == 2 and out2["partial"].startswith("Google Ads could not be read")


def test_when_both_fail_the_first_error_is_thrown_with_its_status():
    pre = fetch_pre("mk(409,{error:'not connected',configProblem:'x'})")
    out = run("P.fetchJson('/x?account=%s%%2B%s',false,{merge:()=>({})}).then(()=>'no',e=>({m:e.message,s:e.status}))" % (G, M), pre)
    assert out == {"m": "not connected", "s": 409}


def test_when_both_fail_it_is_the_first_platforms_error_that_is_thrown():
    pre = fetch_pre("url.indexOf('act_')>0?mk(502,{error:'meta down'}):mk(429,{error:'google slow'})")
    out = run("P.fetchJson('/x?account=%s%%2B%s',false,{merge:()=>({})}).then(()=>'no',e=>e.message)" % (G, M), pre)
    assert out == "google slow"


def test_a_single_account_is_never_given_the_combined_accounts_name():
    pre = fetch_pre("mk(200,{account:{id:'x',name:'real'},current:1})")
    out = run("P.fetchJson('/x?account=%s',false,{acct:{id:'combo',name:'both'},merge:()=>({})}).then(j=>({j,n:calls.length}))" % G, pre)
    assert out["j"]["account"] == {"id": "x", "name": "real"} and out["n"] == 1 and "partial" not in out["j"]


def test_a_cooldown_answer_keeps_its_flag():
    pre = fetch_pre("mk(429,{error:'moments ago',cooldown:true,retryIn:90})")
    out = run("P.fetchJson('/x?account=%s',true,{}).then(()=>'no',e=>({c:e.cooldown,r:e.retryIn,s:e.status}))" % G, pre)
    assert out == {"c": True, "r": 90, "s": 429}


# ── googleOnly: the switch of an agent whose subject exists only on Google ───────────────────────────────────────────────────────────────

DOM = r"""
class El { constructor(){ this.children = []; this.hidden = false; this.innerHTML = ""; this.className = ""; this.handlers = {}; this.parentNode = null; }
  appendChild(c){ c.parentNode = this; this.children.push(c); } setAttribute(){} addEventListener(ev, fn){ this.handlers[ev] = fn; } querySelectorAll(){ return []; }
  insertBefore(n, ref){ n.parentNode = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(n); else this.children.splice(i, 0, n); } get nextSibling(){ const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; } }
const page = new El(), section = new El(), host = new El(), body = new El();
page.appendChild(section); section.appendChild(host); page.appendChild(body);
host.closest = q => (q === "section" ? section : null);
global.localStorage = { getItem: () => null, setItem(){} };
global.document = { createElement: () => new El(), querySelector: s => (s === "#gBody" ? body : null) };
const pick = (sw, p) => { const el = host.children[0]; el.handlers.click({ target: { closest: () => ({ dataset: { pf: p } }) } }); };
const OPTS = { name: "Brand IS Monitor", why: "Meta has no impression share.", closest: [{ label: "Spend Anomaly Alert", href: "/p2/paid-marketing/spend-anomaly" }], body: "#gBody" };
"""


def _go(steps):
    code = DOM + "const seen=[];const g=P.googleOnly(host,Object.assign({onChange:p=>seen.push(p)},OPTS));" + steps + ";console.log(JSON.stringify({seen,hidden:body.hidden,noteHidden:g.note.hidden,note:g.note.innerHTML,value:g.value(),where:page.children.indexOf(g.note)}))"
    r = subprocess.run(["node", "-e", "global.window=undefined;const P=require(%s);%s" % (json.dumps(SCRIPT), code)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_google_only_agent_starts_on_google_with_nothing_hidden_and_the_card_after_the_command_panel():
    out = _go("")
    assert out["value"] == "google" and out["hidden"] is False and out["noteHidden"] is True and out["note"] == "" and out["where"] == 1 and out["seen"] == ["google"]


def test_meta_hides_what_is_under_the_control_and_says_why_and_where_to_look():
    out = _go("pick(null,'meta')")
    assert out["value"] == "meta" and out["hidden"] is True and out["noteHidden"] is False and out["seen"] == ["google", "meta"]
    assert "Brand IS Monitor is a Google Ads agent." in out["note"] and "Meta has no impression share." in out["note"] and '<a href="/p2/paid-marketing/spend-anomaly">Spend Anomaly Alert</a>' in out["note"]
    assert "What follows is Google Ads only" not in out["note"]


def test_both_keeps_the_google_view_and_says_that_meta_was_not_read():
    out = _go("pick(null,'both')")
    assert out["hidden"] is False and out["noteHidden"] is False and "Brand IS Monitor: Meta is not read here." in out["note"] and "What follows is Google Ads only." in out["note"]


def test_going_back_to_google_shows_the_view_again_and_clears_the_card():
    out = _go("pick(null,'meta');pick(null,'google')")
    assert out["hidden"] is False and out["noteHidden"] is True and out["note"] == "" and out["seen"] == ["google", "meta", "google"]


def test_a_name_or_link_with_markup_is_text():
    code = "const c=P.googleOnlyCard('meta',{name:'<b>x</b>',why:'a<script>',closest:[{label:'<i>',href:'/a\"b'}]});console.log(JSON.stringify(c))"
    r = subprocess.run(["node", "-e", "global.window=undefined;const P=require(%s);%s" % (json.dumps(SCRIPT), code)], capture_output=True, text=True, timeout=30)
    html = json.loads(r.stdout)
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<script>" not in html and "&lt;i&gt;" in html and '/a"b' not in html


def test_the_switch_hands_back_its_element_so_a_page_can_leave_it_out_of_a_picker_that_has_its_own():
    code = DOM + "const sw=P.switcher(host,{});console.log(JSON.stringify({same: sw.el===host.children[0], cls: sw.el.className}))"
    r = subprocess.run(["node", "-e", "global.window=undefined;const P=require(%s);%s" % (json.dumps(SCRIPT), code)], capture_output=True, text=True, timeout=30)
    assert json.loads(r.stdout) == {"same": True, "cls": "pm-plat-sw"}
