"""Geo Performance: the advice follows the bidding the spend is on (tracker/paid_geo.py, paid_geo_insights.py, paid_bidding.py)."""
import copy
import datetime as dt
import json
import os
import shutil
import subprocess

import pytest

from tests.test_paid_geo import DC, GOOGLE, MD, START, VA, END, geo_fn, grow, searcher
from tracker import paid_geo as g
from tracker import paid_geo_insights as gi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def prow(name, channel, strategy, key, imp, clk, cost, conv):
    row = grow(name, channel, key, imp, clk, cost, conv)
    if strategy:
        row["campaign"]["biddingStrategyType"] = strategy
    return row


def places(strategy, channel="SEARCH", name="P2_NonBrand"):
    return [prow(name, channel, strategy, MD, 5000, 400, 1000.0, 50), prow(name, channel, strategy, VA, 8000, 600, 1500.0, 10), prow(name, channel, strategy, DC, 3000, 100, 600.0, 0)]


def report(rows, end=END):
    raw = g.google_raw(GOOGLE, "region", START, end, search_fn=searcher(rows, []), geo_fn=geo_fn)
    return g.assemble(raw, GOOGLE, START, end)


def by_kind(rep, kind, view="All"):
    return [i for i in rep["views"][view]["insights"] if i["kind"] == kind]


def test_the_query_asks_for_the_bidding_strategy_and_every_table_says_what_kind_of_bidding_its_spend_is_on():
    assert "campaign.bidding_strategy_type" in g.gaql("region", START, END) and "campaign.bidding_strategy_type" in g.gaql("country", START, END)
    assert report(places("MANUAL_CPC"))["views"]["All"]["bidding"] == {"adjustable": 1.0, "smart": 0.0, "auto": 0.0, "other": 0.0}
    assert report(places("TARGET_CPA"))["views"]["Non Brand"]["bidding"]["smart"] == 1.0
    assert report(places("MAXIMIZE_CONVERSIONS", "PERFORMANCE_MAX", "PMax"))["views"]["All"]["bidding"]["auto"] == 1.0
    assert report(places(None))["views"]["All"]["bidding"] is None


def test_two_accounts_of_an_mcc_add_their_bidding_and_a_read_comes_out_of_the_cache_unchanged():
    a = {"places": {MD: {"all": g.zero(), "types": {}, "bid": {"smart": 10.0}, "bidTypes": {"Brand": {"smart": 10.0}}}}}
    b = {"places": {MD: {"all": g.zero(), "types": {}, "bid": {"smart": 5.0, "adjustable": 5.0}, "bidTypes": {"Brand": {"adjustable": 5.0}}}}}
    into = {}
    g._fold(into, a["places"])
    g._fold(into, b["places"])
    assert into[MD]["bid"] == {"smart": 15.0, "adjustable": 5.0} and into[MD]["bidTypes"] == {"Brand": {"smart": 10.0, "adjustable": 5.0}}
    copy_ = g._copy_place(into[MD])
    copy_["bid"]["smart"] = 0.0
    copy_["bidTypes"]["Brand"]["smart"] = 0.0
    assert into[MD]["bid"]["smart"] == 15.0 and into[MD]["bidTypes"]["Brand"]["smart"] == 10.0


def test_on_manual_bidding_the_old_advice_stands_with_the_steps_to_do_it_and_the_value():
    rep = report(places("MANUAL_CPC"))
    push, trim, leak = by_kind(rep, "push")[0], by_kind(rep, "trim")[0], by_kind(rep, "exclude")[0]
    assert push["action"].startswith("Add a +40% location bid adjustment for Maryland") and trim["action"].startswith("Add a -50% location bid adjustment for Virginia")
    assert push["how"][0].startswith("Open the campaign, then Locations, then the list of locations, and find Maryland, click the Bid adj. cell on its row and set +40%.") and "add it first with Enter another location" in push["how"][0]
    assert "set -50%" in trim["how"][0] and push["how"][1].startswith("Check the cost per conversion for Maryland after two weeks")
    assert leak["action"].startswith("Exclude District of Columbia, or cut its bid by half") and "Excluded locations" in leak["how"][0] and "-100%" in leak["how"][0]
    assert leak["worth"] == "$600 went to District of Columbia in 30 days with no conversion, about $600 a month at that rate."
    assert trim["worth"].startswith("$1,500 went to Virginia in 30 days, about $1,500 a month")


def test_on_smart_bidding_a_location_adjustment_is_replaced_by_a_campaign_of_its_own_or_an_exclusion():
    rep = report(places("TARGET_CPA"))
    push, trim, leak = by_kind(rep, "push")[0], by_kind(rep, "trim")[0], by_kind(rep, "exclude")[0]
    assert "Smart Bidding mostly ignores a location adjustment, so +40% for Maryland would do little" in push["action"] and any("campaign that targets only Maryland" in h for h in push["how"])
    assert any("original campaign" in h for h in push["how"]) and push["worth"] is None
    assert trim["action"] == "Smart Bidding mostly ignores a location adjustment, so exclude Virginia or move it to a campaign with a smaller budget." and any("Excluded locations" in h for h in trim["how"])
    assert leak["action"] == "Smart Bidding mostly ignores a location adjustment, so exclude District of Columbia." and leak["worth"].startswith("$600 went to")
    assert "bid adjustment" not in push["action"].replace("a location adjustment", "")


def test_on_performance_max_the_only_location_levers_are_exclusion_and_a_campaign_of_its_own():
    rep = report(places("MAXIMIZE_CONVERSIONS", "PERFORMANCE_MAX", "PMax"))
    push, trim = by_kind(rep, "push")[0], by_kind(rep, "trim")[0]
    assert push["action"] == "Performance Max and Demand Gen take no location adjustment. To give Maryland more money, put it in a campaign of its own with its own budget."
    assert trim["action"] == "Performance Max and Demand Gen take no location adjustment, so exclude Virginia in the campaign's location settings." and "Excluded locations" in trim["how"][0]
    assert "Raise bids" not in push["action"]


def test_a_mixed_table_keeps_the_advice_and_says_how_much_of_the_spend_it_will_not_reach():
    rows = places("MANUAL_CPC") + [prow("P2_Brand", "SEARCH", "TARGET_CPA", MD, 1000, 100, 1200.0, 25)]
    push = by_kind(report(rows), "push")[0]
    assert push["action"].startswith("Add a +") and any("of this table's spend is on Smart Bidding or Performance Max" in h for h in push["how"])
    assert not any("of this table's spend" in h for h in by_kind(report(places("MANUAL_CPC")), "push")[0]["how"])


def test_when_the_bidding_is_not_known_the_old_advice_stands_and_meta_advice_is_untouched():
    unknown = by_kind(report(places(None)), "push")[0]
    assert unknown["action"].startswith("Add a +40% location bid adjustment") and unknown["how"] and "Smart Bidding" not in " ".join(unknown["how"])
    rep = copy.deepcopy(report(places("TARGET_CPA")))
    view = rep["views"]["All"]
    meta = gi.analyse({k: v for k, v in view.items() if k not in ("rows", "insights", "counts", "basis")} | {"rows": [{k: r[k] for k in r if k not in ("verdict", "verdictLabel", "index", "adjustment", "confidence", "why")} for r in view["rows"]]},
                      level="region", currency="USD", platform="meta", days=30)
    assert all(i["how"] == [] and i["worth"] is None for i in meta["insights"]) and "Smart Bidding" not in json.dumps(meta["insights"])


def test_the_value_and_the_month_are_scaled_from_the_range():
    short = report(places("MANUAL_CPC"), end=dt.date(2026, 6, 15))
    assert by_kind(short, "exclude")[0]["worth"] == "$600 went to District of Columbia in 15 days with no conversion, about $1,200 a month at that rate."


def test_no_step_has_a_dash_or_a_stray_format_mark():
    for rows in (places("MANUAL_CPC"), places("TARGET_CPA"), places("MAXIMIZE_CONVERSIONS", "PERFORMANCE_MAX", "PMax"), places(None)):
        for i in report(rows)["views"]["All"]["insights"]:
            for text in i["how"] + [i["worth"] or "", i["action"]]:
                assert chr(0x2014) not in text and chr(0x2013) not in text and "%s" not in text and "%d" not in text and "%%" not in text, text


_JS = os.path.join(ROOT, "static", "js", "geo_performance.js")


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_page_draws_the_steps_and_what_it_is_worth_and_escapes_them():
    src = open(_JS, encoding="utf-8").read()
    a = src.index('var GROUPS=[["action","Do this"]')
    b = src.index('$("#insList").innerHTML=html;') + len('$("#insList").innerHTML=html;') + 2
    item = {"kind": "exclude", "severity": "action", "headline": "H", "evidence": "e", "action": "Do it", "confidence": "medium", "keys": ["k"], "how": ["Open <b>x</b>", "Then y"], "worth": "$5 <i>"}
    code = ("const out={}; const view=()=>({insights:%s}); const budgetWords=()=>false; const $=s=>({set innerHTML(v){out[s]=v;}, set textContent(v){out[s+'t']=v;}});"
            "const esc=s=>String(s==null?'':s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
            "%s;paintInsights();console.log(JSON.stringify(out));") % (json.dumps([item, dict(item, how=[], worth=None)]), src[a:b])
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    html = json.loads(out.stdout)["#insList"]
    assert '<ol class="how"><li>Open &lt;b&gt;x&lt;/b&gt;</li><li>Then y</li></ol>' in html and '<p class="worth"><b>Worth</b> $5 &lt;i&gt;</p>' in html
    assert html.count('class="how"') == 1 and html.count('class="worth"') == 1


def test_each_campaign_types_table_is_judged_on_its_own_bidding():
    rows = places("MANUAL_CPC") + [prow("P2_Brand", "SEARCH", "TARGET_CPA", MD, 1000, 100, 1200.0, 25)]
    rep = report(rows)
    assert rep["views"]["Non Brand"]["bidding"]["adjustable"] == 1.0 and rep["views"]["Brand"]["bidding"]["smart"] == 1.0
    assert 0.2 < rep["views"]["All"]["bidding"]["smart"] < 0.4
