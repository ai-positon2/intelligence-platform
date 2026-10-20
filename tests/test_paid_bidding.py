"""The bidding a campaign has decides which levers work (tracker/paid_bidding.py) and how Segment Analysis words its advice for it."""
import copy

import pytest

from tests.test_paid_segment_insights import WEEK, find, r, report
from tracker import paid_bidding as bd
from tracker import paid_segment_insights as ins
from tracker import paid_segments as seg


# ── The classes ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("channel,strategy,want", [
    ("SEARCH", "MANUAL_CPC", bd.ADJUSTABLE), ("SEARCH", "TARGET_SPEND", bd.ADJUSTABLE), ("DISPLAY", "MANUAL_CPM", bd.ADJUSTABLE), ("VIDEO", "MANUAL_CPV", bd.ADJUSTABLE),
    ("SEARCH", "PERCENT_CPC", bd.ADJUSTABLE), ("SEARCH", "TARGET_CPA", bd.SMART), ("SEARCH", "TARGET_ROAS", bd.SMART), ("SEARCH", "MAXIMIZE_CONVERSIONS", bd.SMART),
    ("SHOPPING", "MAXIMIZE_CONVERSION_VALUE", bd.SMART), ("PERFORMANCE_MAX", "MAXIMIZE_CONVERSIONS", bd.AUTO), ("DEMAND_GEN", "TARGET_CPA", bd.AUTO), ("DISCOVERY", "", bd.AUTO),
    ("SEARCH", "TARGET_IMPRESSION_SHARE", bd.OTHER), ("SEARCH", "", bd.OTHER), (None, None, bd.OTHER)])
def test_a_campaign_is_adjustable_smart_auto_or_other(channel, strategy, want):
    assert bd.class_of(channel, strategy) == want


def test_shares_are_of_the_spend_and_none_when_nothing_is_known():
    assert bd.shares({"adjustable": 60.0, "smart": 40.0}) == {"adjustable": 0.6, "smart": 0.4, "auto": 0.0, "other": 0.0}
    assert bd.shares({"other": 10.0}) is None and bd.shares({}) is None and bd.shares(None) is None and bd.shares({"smart": 0.0}) is None
    assert bd.shares({"smart": 50.0, "other": 50.0})["other"] == 0.5


def test_a_table_is_of_a_kind_at_sixty_percent_of_its_spend_and_mixed_below():
    assert bd.mode({"adjustable": 0.6, "smart": 0.4, "auto": 0.0}) == "adjustable" and bd.mode({"adjustable": 0.59, "smart": 0.41, "auto": 0.0}) == "mixed"
    assert bd.mode({"smart": 0.6}) == "smart" and bd.mode({"auto": 0.7, "smart": 0.3}) == "auto" and bd.mode(None) is None and bd.mode({}) is None


def test_merge_adds_spend_by_class_and_ignores_missing_parts():
    assert bd.merge([{"smart": 1.0}, None, {"smart": 2.0, "auto": 3.0}]) == {"smart": 3.0, "auto": 3.0}


# ── Read from Google's answer ───────────────────────────────────────────────

def rs(name, channel, strategy, imp, clk, cost, conv, **parts):
    row = r(name, channel, imp, clk, cost, conv, **parts)
    if strategy:
        row["campaign"]["biddingStrategyType"] = strategy
    return row


def day_rows(strategy, channel="SEARCH", name="P2_NonBrand", spec=None):
    return [rs(name, channel, strategy, 1000, 40, cost, conv, segments={"dayOfWeek": d}) for d, (cost, conv) in (spec or WEEK).items()]


def test_the_query_asks_for_the_bidding_strategy_and_every_table_says_what_kind_of_bidding_its_spend_is_on():
    assert "campaign.bidding_strategy_type" in seg.gaql(seg.DIM_BY_KEY["day"], seg.dt.date(2026, 6, 1), seg.dt.date(2026, 6, 30))
    rep = report({"day": day_rows("MANUAL_CPC")})
    assert rep["segments"][0]["views"]["All"]["bidding"] == {"adjustable": 1.0, "smart": 0.0, "auto": 0.0, "other": 0.0}
    mixed = report({"day": day_rows("MANUAL_CPC", spec={"MONDAY": (100, 5)}) + day_rows("TARGET_CPA", name="P2_Brand", spec={"MONDAY": (300, 5)})})
    assert mixed["segments"][0]["views"]["All"]["bidding"] == {"adjustable": 0.25, "smart": 0.75, "auto": 0.0, "other": 0.0}
    assert mixed["segments"][0]["views"]["Brand"]["bidding"]["smart"] == 1.0 and mixed["segments"][0]["views"]["Non Brand"]["bidding"]["adjustable"] == 1.0
    pmax = report({"day": day_rows("MAXIMIZE_CONVERSIONS", channel="PERFORMANCE_MAX", name="PMax")})
    assert pmax["segments"][0]["views"]["All"]["bidding"]["auto"] == 1.0
    assert report({"day": day_rows(None)})["segments"][0]["views"]["All"]["bidding"] is None                # a strategy Google did not name: not known


# ── The suggested adjustment ────────────────────────────────────────────────

@pytest.mark.parametrize("avg,cpl,want", [(50.0, 50.0, 0.0), (50.0, 20.0, 0.40), (50.0, 40.0, 0.20), (50.0, 45.0, 0.10), (50.0, 48.0, 0.0), (50.0, 55.0, 0.0), (50.0, 100.0, -0.40),
                                          (50.0, 5.0, 0.40), (50.0, 500.0, -0.50), (50.0, 5000.0, -0.50), (None, 10.0, 0.0), (50.0, 0, 0.0)])
def test_the_suggested_adjustment_is_geos_rule_most_of_the_gap_in_steps_of_five_between_plus_forty_and_minus_fifty_and_none_on_par(avg, cpl, want):
    assert ins.suggested(avg, cpl) == pytest.approx(want)


def test_on_par_is_a_tenth_either_way_of_the_average():
    assert ins.suggested(100.0, 100.0 / 0.9) == 0.0 and ins.suggested(100.0, 100.0 / 1.1) == 0.0 and ins.suggested(100.0, 100.0 / 1.11) != 0.0


# ── The advice follows the bidding ──────────────────────────────────────────

def insights(strategy, channel="SEARCH", name="P2_NonBrand"):
    return ins.build(report({"day": day_rows(strategy, channel, name)}))


def test_on_manual_bidding_the_old_advice_stands_with_the_steps_and_a_value_and_what_it_is_worth():
    out = insights("MANUAL_CPC")
    best, waste = find(out, "best", "day")[0], find(out, "waste", "day")[0]
    assert "ad-schedule bid adjustment" in best["action"] and "Ad schedule" in best["how"][0] and any("about +40%" in h for h in best["how"]) and best["worth"] is None
    assert "ad schedule" in waste["action"] and any("about -50%" in h for h in waste["how"]) and waste["how"][0].startswith("Open the campaign, then Ad schedule")
    assert waste["worth"].startswith("$200 went on this with no conversion in 30 days, about $200 a month.")


def test_on_smart_bidding_the_adjustment_advice_is_replaced_by_what_works():
    out = insights("TARGET_CPA")
    best, waste = find(out, "best", "day")[0], find(out, "waste", "day")[0]
    assert "mostly ignores hand-set adjustments" in best["action"] and "ad-schedule bid adjustment" not in best["action"]
    assert any("limited by budget" in h for h in best["how"])
    assert "Smart Bidding ignores a day adjustment" in waste["action"] and any("ad schedule" in h.lower() for h in waste["how"]) and "Lower bids" not in waste["action"]


def test_on_performance_max_the_advice_says_what_it_takes_and_what_it_does_not():
    out = insights("MAXIMIZE_CONVERSIONS", "PERFORMANCE_MAX", "PMax")
    waste, best = find(out, "waste", "day")[0], find(out, "best", "day")[0]
    assert "Performance Max and Demand Gen set their own bids and take no adjustment, but an ad schedule works" in waste["action"]
    assert "take no adjustment for Thursday" in best["action"] and any("lost impression share" in h for h in best["how"])


def test_a_mixed_table_keeps_the_advice_and_says_how_much_of_the_spend_it_will_not_reach():
    rows = day_rows("MANUAL_CPC", spec={k: (v[0] * 0.5, v[1]) for k, v in WEEK.items()}) + day_rows("TARGET_CPA", name="P2_Brand", spec={k: (v[0] * 0.5, 0 if k not in ("THURSDAY",) else v[1]) for k, v in WEEK.items()})
    out = ins.build(report({"day": rows}))
    items = [i for i in out if i["dimension"] == "day" and i["kind"] in ("best", "waste", "costly")]
    assert items and all(any("of this table's spend is on Smart Bidding or Performance Max" in h for h in i["how"]) for i in items)
    assert all("mostly ignores" not in i["action"] for i in items)


def test_when_the_bidding_is_not_known_the_old_advice_stands_unchanged():
    out = insights(None)
    assert "ad-schedule" in find(out, "best", "day")[0]["action"] and "Smart Bidding" not in find(out, "waste", "day")[0]["action"]


def test_meta_advice_is_untouched():
    rep = report({"day": day_rows("TARGET_CPA")})
    rep = copy.deepcopy(rep)
    rep["account"]["platform"] = "meta"
    out = ins.build(rep)
    assert all(i["how"] == [] and i["worth"] is None for i in out if i["kind"] in ("best", "waste", "costly") and i["dimension"] == "day")
    assert "Smart Bidding" not in " ".join(i["action"] for i in out)


def test_a_costly_segment_on_smart_bidding_is_told_to_look_at_the_target_and_on_manual_gets_a_negative_adjustment_and_a_saving():
    spec = {"MONDAY": (100, 5), "TUESDAY": (100, 5), "WEDNESDAY": (100, 5), "THURSDAY": (100, 5), "FRIDAY": (100, 5), "SATURDAY": (300, 3), "SUNDAY": (100, 5)}
    smart = [i for i in ins.build(report({"day": day_rows("TARGET_CPA", spec=spec)})) if i["kind"] == "costly"][0]
    assert "will not help: the cost per conversion reflects the target" in smart["action"] and any("target CPA or ROAS" in h for h in smart["how"])
    manual = [i for i in ins.build(report({"day": day_rows("MANUAL_CPC", spec=spec)})) if i["kind"] == "costly"][0]
    assert any("about -" in h for h in manual["how"]) and manual["worth"].startswith("At the table's average cost per conversion the same 3 conversions would have cost about $")


def test_no_step_has_a_dash_or_a_stray_format_mark():
    for strategy, channel in (("MANUAL_CPC", "SEARCH"), ("TARGET_CPA", "SEARCH"), ("MAXIMIZE_CONVERSIONS", "PERFORMANCE_MAX"), (None, "SEARCH")):
        for i in insights(strategy, channel):
            for text in i["how"] + [i["worth"] or "", i["action"]]:
                assert chr(0x2014) not in text and chr(0x2013) not in text and "%s" not in text and "%d" not in text and "%%" not in text, (strategy, text)


def test_the_note_about_spend_the_advice_will_not_reach_appears_from_a_fifth_of_the_spend():
    def table(smart_share):
        manual = {k: (v[0] * (1 - smart_share), v[1]) for k, v in WEEK.items()}
        smart = {k: (v[0] * smart_share, 0) for k, v in WEEK.items()}
        rows = day_rows("MANUAL_CPC", spec=manual) + day_rows("TARGET_CPA", name="P2_Brand", spec=smart)
        return [i for i in ins.build(report({"day": rows})) if i["dimension"] == "day" and i["kind"] in ("best", "waste")]
    assert all(any("of this table's spend is on Smart Bidding" in h for h in i["how"]) for i in table(0.25))
    assert not any("of this table's spend is on Smart Bidding" in h for i in table(0.15) for h in i["how"])


def test_what_a_costly_segment_is_worth_is_the_difference_to_the_average_and_a_months_waste_is_scaled_from_the_range():
    spec = {"MONDAY": (100, 5), "TUESDAY": (100, 5), "WEDNESDAY": (100, 5), "THURSDAY": (100, 5), "FRIDAY": (100, 5), "SATURDAY": (300, 3), "SUNDAY": (100, 5)}
    costly = [i for i in ins.build(report({"day": day_rows("MANUAL_CPC", spec=spec)})) if i["kind"] == "costly"][0]
    assert costly["worth"] == "At the table's average cost per conversion the same 3 conversions would have cost about $218 less over 30 days: an estimate."
    import datetime as dt
    short = ins.build(report({"day": day_rows("MANUAL_CPC")}, end=dt.date(2026, 6, 15)))
    waste = find(short, "waste", "day")[0]
    assert "no conversion in 15 days, about $400 a month" in waste["worth"]


# ── The page draws the steps ────────────────────────────────────────────────

import json as _json  # noqa: E402
import os as _os  # noqa: E402
import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402

_JS = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "static", "js", "segment_analysis.js")


def _paint(insights_list):
    """Run the page's own paintInsights on a report: the function is cut out of the script and run with a stub page."""
    src = open(_JS, encoding="utf-8").read()
    a = src.index('var GROUPS=[["action","Do this"]')
    b = src.index('$("#insList").innerHTML=html;') + len('$("#insList").innerHTML=html;') + 2
    code = ("const out={}; const S={report:{insights:%s}}; const $=s=>({set innerHTML(v){out[s]=v;}, set textContent(v){out[s+'t']=v;}});"
            "const esc=s=>String(s==null?'':s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
            "const SEG_NAV={day:1};%s;paintInsights();console.log(JSON.stringify(out));") % (_json.dumps(insights_list), src[a:b])
    out = _subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return _json.loads(out.stdout)["#insList"]


@pytest.mark.skipif(not _shutil.which("node"), reason="node is not installed")
def test_the_page_lists_the_steps_and_what_it_is_worth_and_escapes_them():
    item = {"id": "day:waste:x", "dimension": "day", "dimensionLabel": "Day of the week", "kind": "waste", "severity": "action", "headline": "Sunday", "evidence": "e", "action": "Do it",
            "confidence": "high", "how": ["Open <b>the</b> campaign", "Then this"], "worth": "$200 a month <script>"}
    html = _paint([item])
    assert '<ol class="how"><li>Open &lt;b&gt;the&lt;/b&gt; campaign</li><li>Then this</li></ol>' in html and '<p class="worth"><b>Worth</b> $200 a month &lt;script&gt;</p>' in html
    bare = _paint([dict(item, how=[], worth=None)])
    assert 'class="how"' not in bare and 'class="worth"' not in bare and "Do it" in bare
    old = {k: v for k, v in item.items() if k not in ("how", "worth")}
    assert 'class="how"' not in _paint([old])                                       # a saved report from before the steps existed


def test_a_google_and_meta_report_keeps_the_google_halfs_bidding_so_the_google_advice_is_still_judged_on_it():
    from tracker import paid_meta_segments as ms
    g = report({"day": day_rows("TARGET_CPA")})
    view = g["segments"][0]["views"]["All"]
    merged = ms._merge_view(view, {"rows": []}, "natural", view["total"]["cost"])
    assert merged["bidding"] == view["bidding"] and merged["bidding"]["smart"] == 1.0
    assert ms._merge_view(None, {"rows": []}, "natural", 1.0)["bidding"] is None
