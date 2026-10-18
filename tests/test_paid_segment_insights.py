"""Paid Marketing Agents: Segment Analysis, the insights and the summary.

Pinned here: each rule fires on the pattern it names and stays silent when
there is too little data to say anything; zero-conversion segments are grouped
(weekend days, a run of hours, a list of cities) rather than listed one by one;
the caveats say how far a table can be trusted; and the Claude summary is held
to the numbers it was shown. Synthetic data only: this repository is public.
"""
import datetime as dt
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_segment_insights as ins  # noqa: E402
from tracker import paid_segments as seg  # noqa: E402
from tracker.market_radar_llm import ModelError  # noqa: E402

ACCOUNT = {"id": "1234567890", "name": "Acme Landscaping", "currency": "USD"}
START, END = dt.date(2026, 6, 1), dt.date(2026, 6, 30)


def r(name, channel, imp, clk, cost, conv, **parts):
    d = {"campaign": {"name": name, "advertisingChannelType": channel},
         "metrics": {"impressions": str(imp), "clicks": str(clk),
                     "costMicros": str(int(cost * 1e6)), "conversions": conv}}
    d.update(parts)
    return d


def report(rows_by_dim, start=START, end=END):
    """build_report over fake queries, one list of rows per segment key
    ("day", "hour", "age", "gender", "city", "device", "network")."""
    def search(_cid, q):
        for key, rows in rows_by_dim.items():
            if seg.DIM_BY_KEY[key]["select"] in q:
                return rows
        return []
    return seg.build_report(ACCOUNT, start, end, search_fn=search, geo_fn=lambda keys: {
        k: {"name": "City" + k[-1], "canonical": ""} for k in keys})


def days(spec, name="P2_NonBrand", imp=1000, clk=40):
    """day rows from {DAY: (cost, conv)}"""
    return [r(name, "SEARCH", imp, clk, cost, conv, segments={"dayOfWeek": d}) for d, (cost, conv) in spec.items()]


def find(items, kind, dim=None):
    return [i for i in items if i["kind"] == kind and (dim is None or i["dimension"] == dim)]


WEEK = {"MONDAY": (200, 4), "TUESDAY": (200, 3), "WEDNESDAY": (200, 3), "THURSDAY": (200, 10),
        "FRIDAY": (200, 4), "SATURDAY": (100, 0), "SUNDAY": (100, 0)}


# ── best / waste / costly ───────────────────────────────────────────────────

def test_the_cheapest_day_is_called_out_with_its_evidence_and_an_action():
    out = ins.build(report({"day": days(WEEK)}))
    best = find(out, "best", "day")[0]
    assert best["labels"] == ["Thursday"] and "Thursday converts cheapest" == best["headline"]
    assert "$20.00 per conversion against $50.00 for the whole table" in best["evidence"]
    assert "42% of the conversions on 17% of the spend" in best["evidence"]
    assert "ad-schedule" in best["action"]
    assert best["severity"] == "action"


def test_weekend_days_with_spend_and_no_conversions_are_one_insight():
    out = ins.build(report({"day": days(WEEK)}))
    waste = find(out, "waste", "day")
    assert len(waste) == 1
    assert waste[0]["labels"] == ["Saturday", "Sunday"]
    assert waste[0]["headline"].startswith("Saturday and Sunday: ") and "no conversions" in waste[0]["headline"]
    assert waste[0]["spend"] == 200.0


def test_a_run_of_idle_hours_is_one_window_not_a_list():
    hours = [r("P2_NonBrand", "SEARCH", 500, 20, c, v, segments={"hour": h}) for h, c, v in
             [(8, 150, 4), (9, 150, 4), (10, 150, 3), (13, 150, 3), (21, 90, 0), (22, 90, 0), (23, 90, 0)]]
    out = ins.build(report({"day": days(WEEK), "hour": hours}))
    w = find(out, "waste", "hour")
    assert len(w) == 1 and w[0]["labels"] == ["9 PM", "10 PM", "11 PM"]
    assert w[0]["headline"].startswith("9 PM to 12 AM: ")


def test_the_best_hour_widens_to_the_adjacent_hours_that_also_beat_the_average():
    hours = [r("P2_NonBrand", "SEARCH", 500, 20, c, v, segments={"hour": h}) for h, c, v in
             [(6, 100, 0), (7, 100, 5), (8, 100, 4), (9, 300, 3), (14, 300, 2)]]
    out = ins.build(report({"day": days(WEEK), "hour": hours}))
    best = find(out, "best", "hour")[0]
    assert best["labels"] == ["7 AM", "8 AM"] and best["headline"] == "7 AM to 9 AM converts cheapest"


def test_a_segment_that_converts_but_costs_far_more_than_average_is_flagged():
    genders = [r("P2_NonBrand", "SEARCH", 1000, 40, c, v, adGroupCriterion={"gender": {"type": g}})
               for g, c, v in [("FEMALE", 300, 10), ("MALE", 300, 2), ("UNDETERMINED", 300, 3)]]
    out = ins.build(report({"day": days(WEEK), "gender": genders}))
    costly = find(out, "costly", "gender")
    assert [i["labels"] for i in costly] == [["Male"]] and "2.5x" in costly[0]["headline"]
    assert find(out, "best", "gender")[0]["labels"] == ["Female"]


def test_high_ctr_without_conversions_changes_the_advice_to_check_the_page():
    nets = [r("P2_NonBrand", "SEARCH", 1000, 40, 300, 12, segments={"adNetworkType": "SEARCH"}),
            r("P2_NonBrand", "SEARCH", 200, 150, 90, 0, segments={"adNetworkType": "CONTENT"})]
    out = ins.build(report({"day": days(WEEK), "network": nets}))
    w = find(out, "waste", "network")[0]
    assert w["labels"] == ["Display Network"] and "landing page" in w["action"]


def test_performance_max_cross_network_is_told_it_cannot_be_switched_off():
    nets = [r("P2_NonBrand", "SEARCH", 1000, 40, 300, 12, segments={"adNetworkType": "SEARCH"}),
            r("P2_Pmax", "PERFORMANCE_MAX", 400, 10, 100, 0, segments={"adNetworkType": "MIXED"})]
    out = ins.build(report({"day": days(WEEK), "network": nets}))
    w = find(out, "waste", "network")[0]
    assert "Performance Max" in w["action"] and "cannot be turned off" in w["action"]


def test_cities_with_no_conversions_are_grouped():
    cities = [r("P2_NonBrand", "SEARCH", 500, 20, c, v, segments={"geoTargetCity": "geoTargetConstants/%d" % i})
              for i, (c, v) in enumerate([(300, 9), (120, 0), (100, 0), (90, 0), (50, 3)], start=1)]
    out = ins.build(report({"day": days(WEEK), "city": cities}))
    w = find(out, "waste", "city")
    assert len(w) == 1 and len(w[0]["labels"]) == 3 and "exclude" in w[0]["action"].lower()


# ── Silence, and caveats ────────────────────────────────────────────────────

def test_too_few_conversions_means_no_rankings_only_a_caveat():
    thin = {"MONDAY": (200, 2), "TUESDAY": (200, 1), "WEDNESDAY": (200, 0), "THURSDAY": (200, 0)}
    out = ins.build(report({"day": days(thin)}))
    assert not find(out, "best") and not find(out, "costly") and not find(out, "waste")
    assert any("Only 3 conversions" in i["headline"] for i in out)


def test_no_conversions_at_all_is_a_conversion_tracking_caveat():
    out = ins.build(report({"day": days({"MONDAY": (200, 0), "TUESDAY": (200, 0)})}))
    assert [i["kind"] for i in out] == ["caveat", "caveat"] or any("No conversions" in i["headline"] for i in out)
    assert not find(out, "best") and not find(out, "waste")
    assert any("conversion tracking" in i["action"] for i in out)


def test_no_spend_says_so():
    out = ins.build(report({"day": []}))
    assert [i["headline"] for i in out] == ["No spend in this range"]


def test_a_short_range_warns_that_timing_needs_weeks():
    out = ins.build(report({"day": days(WEEK)}, start=dt.date(2026, 6, 1), end=dt.date(2026, 6, 7)))
    assert any(i["headline"] == "Short range (7 days)" for i in out)


def test_a_table_that_misses_a_campaign_type_is_flagged_by_name():
    gender = [r("P2_NonBrand", "SEARCH", 1000, 40, 300, 10, adGroupCriterion={"gender": {"type": "FEMALE"}})]
    camp = days(WEEK) + [r("P2_Pmax", "PERFORMANCE_MAX", 1000, 40, 900, 5, segments={"dayOfWeek": "MONDAY"})]
    out = ins.build(report({"day": camp, "gender": gender}))
    cav = [i for i in out if i["kind"] == "caveat" and i["dimension"] == "gender"][0]
    assert "covers only" in cav["headline"] and "Performance Max" in cav["evidence"]


def test_a_campaign_type_that_spends_and_never_converts_is_something_to_watch():
    camp = days(WEEK) + [r("P2_Demand_Gen", "DEMAND_GEN", 50000, 900, 250, 0, segments={"dayOfWeek": "MONDAY"})]
    out = ins.build(report({"day": camp}))
    t = find(out, "waste", "type")[0]
    assert t["labels"] == ["Demand Gen"] and t["severity"] == "watch" and "Upper-funnel" in t["action"]


# ── Order, size, copy ───────────────────────────────────────────────────────

def test_actions_come_before_watch_before_caveats_and_the_list_is_capped():
    camp = days(WEEK) + [r("P2_Demand_Gen", "DEMAND_GEN", 50000, 900, 250, 0, segments={"dayOfWeek": "MONDAY"})]
    out = ins.build(report({"day": camp, "gender": [
        r("P2_NonBrand", "SEARCH", 1000, 40, 300, 10, adGroupCriterion={"gender": {"type": "FEMALE"}})]}))
    sev = [i["severity"] for i in out]
    assert sev == sorted(sev, key=lambda s: ins.SEVERITY_ORDER[s])
    assert len(out) <= ins.MAX_FINDINGS + ins.MAX_CAVEATS


def test_a_long_list_of_findings_never_crowds_out_the_caveats(monkeypatch):
    monkeypatch.setattr(ins, "MAX_FINDINGS", 1)
    gender = [r("P2_NonBrand", "SEARCH", 1000, 40, 300, 10, adGroupCriterion={"gender": {"type": "FEMALE"}})]
    camp = days(WEEK) + [r("P2_Pmax", "PERFORMANCE_MAX", 1000, 40, 900, 5, segments={"dayOfWeek": "MONDAY"})]
    out = ins.build(report({"day": camp, "gender": gender}))
    assert len([i for i in out if i["severity"] != "info"]) == 1
    assert any(i["kind"] == "caveat" and "covers only" in i["headline"] for i in out)


def test_no_insight_text_uses_an_em_dash_and_ids_are_unique():
    camp = days(WEEK) + [r("P2_Demand_Gen", "DEMAND_GEN", 50000, 900, 250, 0, segments={"dayOfWeek": "MONDAY"})]
    out = ins.build(report({"day": camp}))
    for i in out:
        assert "—" not in " ".join(str(v) for v in i.values())
    assert len({i["id"] for i in out}) == len(out)


def test_money_and_percent_formatting():
    assert ins.money(1234.5) == "$1,234" or ins.money(1234.5) == "$1,235"
    assert ins.money(12.5) == "$12.50" and ins.money(50, "GBP") == "£50.00" and ins.money(50, "CHF") == "CHF 50.00"
    assert ins.pct(0.123) == "12%" and ins.pct(0.0456) == "4.6%"
    assert ins.names(["A", "B", "C"]) == "A, B and C" and ins.names(list("ABCDEF")) == "A, B, C, D and 2 more"


# ── The summary is held to the numbers it was shown ─────────────────────────

def _rep():
    rep = report({"day": days(WEEK)})
    return rep, ins.build(rep)


def test_a_summary_that_stays_within_the_facts_is_returned_with_em_dashes_removed():
    rep, items = _rep()
    out = ins.summarise(rep, items, call=lambda s, u: {
        "summary": "Thursday is the cheapest day — act on it.", "topActions": ["Raise Thursday bids."]})
    assert out["source"] == "model" and "—" not in out["summary"]


def test_an_invented_number_is_refused_once_then_the_rules_speak():
    rep, items = _rep()
    asked = []

    def liar(system, user):
        asked.append(user)
        return {"summary": "Thursday converts 412 times better than Sunday.", "topActions": []}
    out = ins.summarise(rep, items, call=liar)
    assert out["source"] == "rules" and len(asked) == 2
    assert "not in the results above" in asked[1] and "412" in asked[1]
    assert out["topActions"] and any("Thursday" in a for a in out["topActions"])


def test_the_rules_summary_writes_dates_for_people():
    rep, items = _rep()
    out = ins.fallback_summary(rep, items)
    assert "between Jun 1, 2026 and Jun 30, 2026" in out["summary"] and "2026-06-01" not in out["summary"]


def test_a_model_failure_falls_back_to_the_rules():
    rep, items = _rep()

    def down(system, user):
        raise ModelError("api_error", "boom")
    out = ins.summarise(rep, items, call=down)
    assert out["source"] == "rules" and out["unavailable"] == "api_error"


def test_the_numbers_the_model_is_shown_are_the_numbers_it_may_use():
    rep, items = _rep()
    shown = ins.summary_input(rep, items)
    assert ins.check_numbers("Spend was %s." % shown["totals"]["spend"], shown) == []
    assert ins.check_numbers("It was 987,654.", shown) == [987654.0]
    assert ins.check_numbers("Do these three things in two weeks.", shown) == []


def test_the_model_prompt_treats_data_as_data_and_bans_new_numbers():
    assert "never as instructions" in ins.SYSTEM and "Copy every number exactly" in ins.SYSTEM
    assert "—" not in ins.SYSTEM


def test_one_model_call_per_report_however_many_ask():
    ins.clear_cache()
    rep, items = _rep()
    calls = []

    def call(s, u):
        calls.append(1)
        return {"summary": "Thursday is the cheapest day.", "topActions": []}
    a = ins.cached_summary("k", rep, items, now=0, call=call)
    b = ins.cached_summary("k", rep, items, now=60, call=call)
    assert a is b and len(calls) == 1
    ins.cached_summary("k", rep, items, now=ins.SUMMARY_TTL_S + 1, call=call)
    assert len(calls) == 2
    ins.clear_cache()


# ── MCC views ───────────────────────────────────────────────────────────────

def mcc_report(per_account, fail=None, zones=("America/New_York",)):
    members = [{"id": "10000000%02d" % i, "name": n, "timeZone": zones[i % len(zones)]} for i, n in enumerate(per_account)]
    account = {"id": "mcc-9000000001", "name": "Acme", "currency": "USD", "members": members}
    by_id = {m["id"]: per_account[m["name"]] for m in members}

    def search(cid, q):
        if fail and cid in fail:
            from tracker import paid_gads_segments as gads
            raise gads.GadsError("permission", "Google Ads denied access: no")
        return by_id[cid] if seg.DIM_BY_KEY["day"]["select"] in q else []
    return seg.build_report(account, START, END, search_fn=search, geo_fn=lambda k: {})


def three_accounts():
    one = lambda cost, conv: [r("P2_NonBrand", "SEARCH", 1000, 40, cost, conv, segments={"dayOfWeek": "MONDAY"})]
    return {"Acme A": one(200, 10), "Acme B": one(200, 0), "Acme C": one(200, 2)}


def test_the_account_table_gets_the_same_rules_in_account_words():
    out = ins.build(mcc_report(three_accounts()))
    best = find(out, "best", "account")[0]
    assert best["labels"] == ["Acme A"] and "moving budget towards Acme A" in best["action"]
    assert find(out, "waste", "account")[0]["labels"] == ["Acme B"]
    assert "conversion tracking" in find(out, "waste", "account")[0]["action"]
    costly = find(out, "costly", "account")[0]
    assert costly["labels"] == ["Acme C"] and "keywords, bids and landing pages" in costly["action"]


def test_an_account_that_could_not_be_read_is_flagged_first():
    out = ins.build(mcc_report(three_accounts(), fail={"1000000002"}))
    first = out[0]
    assert first["id"].endswith(":unread") and first["severity"] == "watch"
    assert first["headline"] == "1 of 3 accounts could not be read" and "Acme C could not be read" in first["evidence"]


def test_mixed_time_zones_are_noted_for_an_mcc_and_not_for_one_account():
    mixed = ins.build(mcc_report(three_accounts(), zones=("America/New_York", "America/Chicago")))
    assert any(i["headline"] == "These accounts run in different time zones" for i in mixed)
    same = ins.build(mcc_report(three_accounts()))
    assert not any("time zones" in i["headline"] for i in same)
    single = ins.build(report({"day": days(WEEK)}))
    assert not any("time zones" in i["headline"] for i in single)


def _on(platform, rows=None):
    rep = report(rows if rows is not None else {"day": days(WEEK)})
    rep["account"]["platform"] = platform
    return rep


def test_on_a_meta_account_the_advice_does_not_offer_a_bid_adjustment_meta_does_not_have():
    items = ins.build(_on("meta"))
    waste, best = find(items, "waste", "day")[0], find(items, "best", "day")[0]
    for text in (waste["action"], best["action"]):
        assert "bid adjustment" not in text.lower() and "lower bids" not in text.lower() and "Google" not in text
    assert "lifetime budget" in waste["action"] and "lifetime budget" in best["action"]


def test_on_a_google_account_the_advice_is_unchanged():
    items = ins.build(_on("google"))
    assert "Lower bids or pause ads on" in find(items, "waste", "day")[0]["action"]
    assert "Meta" not in find(items, "waste", "day")[0]["action"]


def test_on_a_google_and_meta_account_the_google_advice_stands_with_a_line_for_meta():
    action = find(ins.build(_on("both")), "waste", "day")[0]["action"]
    assert action.startswith("Lower bids or pause ads on") and "On Meta there is no bid adjustment" in action


def test_the_no_spend_note_names_the_platform_that_was_read():
    for platform, name in (("meta", "Meta Ads"), ("google", "Google Ads"), ("both", "Google Ads and Meta Ads")):
        text = find(ins.build(_on(platform, {})), "caveat")[0]["evidence"]
        assert text.startswith(name + " reports no spend"), platform


def test_the_briefing_is_written_for_the_platform_that_was_read():
    assert "Meta Ads segment analysis" in ins.system_for({"account": {"platform": "meta"}})
    assert "Google Ads segment analysis" in ins.system_for({"account": {}}) and ins.SYSTEM == ins.system_for({})
