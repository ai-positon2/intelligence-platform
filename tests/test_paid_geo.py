"""Paid Marketing Agents: Geo Performance, the numbers.

Pinned here: the query for each location level; reading Google's rows (empty ones dropped, sums by place and by campaign
type, the blank place kept as what it is); naming places (two Springfields told apart, a place Google will not name shown as
what it is); an MCC's accounts added and one failing account said out loud; Meta's rows at country, region and DMA, with
country codes turned into names; a Google and a Meta read added by place name; the report assembled per campaign type with
its coverage and each table judged against its own average; and the cache: one read shared, Refresh limited, a spent
quota served from the last saved read with its date. Nothing here talks to Google or Meta.
"""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_geo as g  # noqa: E402
from tracker import paid_segments as seg  # noqa: E402
from tracker import paid_store  # noqa: E402

START, END = dt.date(2026, 6, 1), dt.date(2026, 6, 30)
GOOGLE = {"id": "1234567890", "name": "Acme Landscaping", "currency": "USD", "timeZone": "America/New_York"}
META = {"id": "act_5550001", "name": "Acme Landscaping", "currency": "USD", "tz": "America/New_York", "platform": "meta"}
MD, VA, DC = "geoTargetConstants/21000", "geoTargetConstants/21001", "geoTargetConstants/21002"


def grow(name, channel, key, imp, clk, cost, conv, value=0, level="region"):
    r = {"campaign": {"name": name, "advertisingChannelType": channel},
         "metrics": {"impressions": str(imp), "clicks": str(clk), "costMicros": str(int(cost * 1e6)), "conversions": conv, "conversionsValue": value}}
    if level == "country":
        r["userLocationView"] = {"countryCriterionId": key.split("/")[-1]}
    else:
        r["segments"] = {g.SEGMENT_FIELD[level]: key}
    return r


NAMES = {MD: {"name": "Maryland", "canonical": "Maryland,United States"}, VA: {"name": "Virginia", "canonical": "Virginia,United States"},
         DC: {"name": "District of Columbia", "canonical": "District of Columbia,United States"},
         "geoTargetConstants/2840": {"name": "United States", "canonical": "United States"}}


def geo_fn(keys):
    return {k: NAMES[k] for k in keys if k in NAMES}


def searcher(places, totals=None):
    def search(_cid, q):
        if "FROM user_location_view" in q:
            if isinstance(places, Exception):
                raise places
            return places
        if isinstance(totals, Exception):
            raise totals
        return totals if totals is not None else []
    return search


PLACES = [grow("Brand_Search", "SEARCH", MD, 5000, 500, 2000, 40, 8000), grow("NonBrand_Search", "SEARCH", MD, 3000, 200, 1500, 10, 1500),
          grow("Brand_Search", "SEARCH", VA, 4000, 300, 1200, 12, 2400), grow("NonBrand_Search", "SEARCH", DC, 2000, 150, 900, 2, 200),
          grow("PMax", "PERFORMANCE_MAX", DC, 1000, 90, 400, 1, 100)]
TOTALS = [{"campaign": {"name": "Brand_Search", "advertisingChannelType": "SEARCH"},
           "metrics": {"impressions": "9000", "clicks": "800", "costMicros": str(3200 * 10**6), "conversions": 52, "conversionsValue": 10400}},
          {"campaign": {"name": "NonBrand_Search", "advertisingChannelType": "SEARCH"},
           "metrics": {"impressions": "5000", "clicks": "350", "costMicros": str(2400 * 10**6), "conversions": 12, "conversionsValue": 1700}},
          {"campaign": {"name": "PMax", "advertisingChannelType": "PERFORMANCE_MAX"},
           "metrics": {"impressions": "1000", "clicks": "90", "costMicros": str(400 * 10**6), "conversions": 1, "conversionsValue": 100}}]


# ── The queries ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("level,field", [("region", "segments.geo_target_region"), ("metro", "segments.geo_target_metro"),
                                         ("city", "segments.geo_target_city"), ("county", "segments.geo_target_county"),
                                         ("postal_code", "segments.geo_target_postal_code")])
def test_each_level_below_a_country_reads_the_user_location_report_with_its_segment(level, field):
    q = g.gaql(level, START, END)
    assert q.startswith("SELECT campaign.name, campaign.advertising_channel_type, %s," % field)
    assert "FROM user_location_view" in q and "BETWEEN '2026-06-01' AND '2026-06-30'" in q
    for m in ("impressions", "clicks", "cost_micros", "conversions", "conversions_value"):
        assert "metrics." + m in q


def test_a_country_comes_from_the_country_criterion_not_a_segment():
    q = g.gaql("country", START, END)
    assert "user_location_view.country_criterion_id" in q and "segments.geo_target" not in q


def test_the_totals_query_is_the_campaign_level_spend_for_coverage():
    q = g.totals_gaql(START, END)
    assert "FROM campaign" in q and "segments.geo_target" not in q and "conversions_value" in q


def test_every_level_the_reader_knows_has_a_field_and_a_label():
    for key, label in g.GOOGLE_LEVELS:
        assert label and (key == "country" or key in g.SEGMENT_FIELD)
    assert [k for k, _ in g.levels_for("meta")] == ["country", "region", "dma"] and [k for k, _ in g.levels_for("both")] == ["country", "region"]


def test_a_level_the_platform_cannot_cut_by_is_refused_in_words():
    assert g.check_level("city", "google") == "city"
    for level, platform in (("city", "meta"), ("dma", "google"), ("county", "both"), ("galaxy", "google")):
        with pytest.raises(g.BadRequest, match="cannot be cut by"):
            g.check_level(level, platform)


# ── Reading Google's rows ───────────────────────────────────────────────────

def test_rows_are_summed_by_place_and_by_campaign_type_and_empty_rows_are_dropped():
    segs = g.read_google(PLACES + [grow("Brand_Search", "SEARCH", VA, 0, 0, 0, 0)], "region")
    assert set(segs) == {MD, VA, DC}
    md = segs[MD]
    assert md["all"] == {"imp": 8000.0, "clk": 700.0, "cost": 3500.0, "conv": 50.0, "val": 9500.0}
    assert set(md["types"]) == {"Brand", "Non Brand"} and md["types"]["Brand"]["cost"] == 2000.0
    assert set(segs[DC]["types"]) == {"Non Brand", "Performance Max"}


def test_a_country_row_is_keyed_by_its_geo_constant_and_a_blank_place_is_kept_as_blank():
    segs = g.read_google([grow("B", "SEARCH", "geoTargetConstants/2840", 10, 1, 5, 0, level="country")], "country")
    assert list(segs) == ["geoTargetConstants/2840"]
    assert list(g.read_google([{"campaign": {"name": "B", "advertisingChannelType": "SEARCH"}, "segments": {},
                               "metrics": {"impressions": "5", "clicks": "1", "costMicros": "1000000"}}], "city")) == [""]


def test_the_account_totals_are_by_campaign_type():
    t = g.read_google_totals(TOTALS)
    assert set(t) == {"Brand", "Non Brand", "Performance Max"} and t["Non Brand"]["cost"] == 2400.0 and t["Performance Max"]["val"] == 100.0


# ── Naming places ───────────────────────────────────────────────────────────

def test_places_get_their_names_and_their_parents():
    out = g.name_places({MD: {}, DC: {}}, "region", geo_fn)
    assert out[MD] == ("Maryland", "United States") and out[DC] == ("District of Columbia", "United States")


def test_two_places_that_share_a_name_are_told_apart_by_their_parent():
    names = {"geoTargetConstants/1": {"name": "Springfield", "canonical": "Springfield,Illinois,United States"},
             "geoTargetConstants/2": {"name": "Springfield", "canonical": "Springfield,Missouri,United States"},
             "geoTargetConstants/3": {"name": "Annapolis", "canonical": "Annapolis,Maryland,United States"}}
    out = g.name_places({k: {} for k in names}, "city", lambda keys: {k: names[k] for k in keys})
    assert out["geoTargetConstants/1"][0] == "Springfield, Illinois" and out["geoTargetConstants/2"][0] == "Springfield, Missouri"
    assert out["geoTargetConstants/3"] == ("Annapolis", "Maryland, United States")


def test_a_place_google_would_not_name_and_the_blank_one_are_shown_as_what_they_are():
    out = g.name_places({"geoTargetConstants/999": {}, "": {}}, "city", lambda keys: {})
    assert out["geoTargetConstants/999"][0] == "Place 999" and out[""][0] == "Not reported at this level"


def test_postal_codes_in_two_states_are_told_apart():
    names = {"geoTargetConstants/1": {"name": "10001", "canonical": "10001,New York,United States"},
             "geoTargetConstants/2": {"name": "10001", "canonical": "10001,Pennsylvania,United States"}}
    out = g.name_places({k: {} for k in names}, "postal_code", lambda keys: {k: names[k] for k in keys})
    assert {out[k][0] for k in names} == {"10001, New York", "10001, Pennsylvania"}


# ── One Google account, or an MCC's accounts ────────────────────────────────

def test_a_google_account_is_read_at_one_level_with_its_totals_and_names():
    raw = g.google_raw(GOOGLE, "region", START, END, search_fn=searcher(PLACES, TOTALS), geo_fn=geo_fn)
    assert raw["platform"] == "google" and raw["level"] == "region" and raw["warnings"] == [] and raw["members"] == []
    assert raw["places"][MD]["label"] == "Maryland" and raw["places"][MD]["sub"] == "United States"
    assert raw["totals"]["Brand"]["cost"] == 3200.0 and raw["timeZones"] == ["America/New_York"]


def test_an_mccs_accounts_are_added_and_one_that_fails_is_named():
    members = [{"id": "1111111111", "name": "One", "timeZone": "America/New_York"}, {"id": "2222222222", "name": "Two", "timeZone": "America/Chicago"}]

    def search(cid, q):
        if cid == "2222222222":
            raise gads.GadsError("permission", "No access to this account.")
        return PLACES if "user_location_view" in q else TOTALS
    raw = g.google_raw({"id": "mcc-1", "name": "Acme MCC", "members": members}, "region", START, END, search_fn=search, geo_fn=geo_fn)
    assert raw["members"] == ["One", "Two"] and raw["warnings"] == ["Two could not be read: No access to this account."]
    assert raw["places"][MD]["all"]["cost"] == 3500.0                                           # only account One's rows
    assert raw["timeZones"] == ["America/Chicago", "America/New_York"]


def test_if_every_account_fails_the_failure_is_raised_and_a_failing_totals_query_is_only_a_warning():
    with pytest.raises(gads.GadsError, match="daily limit"):
        g.google_raw(GOOGLE, "region", START, END, search_fn=searcher(gads.GadsError("quota", "The daily limit is used up.")), geo_fn=geo_fn)
    raw = g.google_raw(GOOGLE, "region", START, END, search_fn=searcher(PLACES, gads.GadsError("api", "totals failed")), geo_fn=geo_fn)
    assert raw["places"] and raw["totals"] == {} and any("totals failed" in w for w in raw["warnings"])


def test_place_names_failing_leaves_numbered_places_and_a_warning():
    def boom(keys):
        raise gads.GadsError("api", "names unavailable")
    raw = g.google_raw(GOOGLE, "region", START, END, search_fn=searcher(PLACES, TOTALS), geo_fn=boom)
    assert raw["places"][MD]["label"] == "Place 21000" and any("Place names could not be looked up" in w for w in raw["warnings"])


# ── Meta ────────────────────────────────────────────────────────────────────

def mrow(objective, spend, imp, clicks, purchases=0, value=0, **geo):
    r = {"objective": objective, "spend": str(spend), "impressions": str(imp), "inline_link_clicks": str(clicks), "actions": [], "action_values": []}
    if purchases:
        r["actions"] = [{"action_type": "purchase", "value": str(purchases)}]
        r["action_values"] = [{"action_type": "purchase", "value": str(value)}]
    r.update(geo)
    return r


def test_meta_rows_become_places_with_country_names_and_campaign_types():
    rows = [mrow("OUTCOME_SALES", 300, 9000, 400, purchases=12, value=2400, country="US"), mrow("OUTCOME_SALES", 50, 900, 40, country="CA"),
            mrow("OUTCOME_LEADS", 20, 800, 30, country="ZZ"), mrow("OUTCOME_SALES", 0, 0, 0, country="MX")]
    raw = g.meta_raw(META, "country", START, END, fetch=lambda act, level, params: rows)
    assert raw["platform"] == "meta" and {p["label"] for p in raw["places"].values()} == {"United States", "Canada", "ZZ"}
    us = raw["places"]["meta:United States"]
    assert us["all"] == {"imp": 9000.0, "clk": 400.0, "cost": 300.0, "conv": 12.0, "val": 2400.0} and set(us["types"]) == {"Meta Sales"}
    assert raw["totals"]["Meta Sales"]["cost"] == 350.0 and "Meta Leads" in raw["totals"]


def test_meta_regions_and_dmas_use_their_own_breakdown_and_the_query_is_what_meta_expects():
    q = g.meta_query("dma", START, END)
    assert q["breakdowns"] == "dma" and q["level"] == "campaign" and "2026-06-01" in q["time_range"] and "spend" in q["fields"]
    assert g.meta_query("region", START, END)["breakdowns"] == "region"
    raw = g.meta_raw(META, "region", START, END, fetch=lambda a, l, p: [mrow("OUTCOME_SALES", 10, 100, 5, region="Maryland")])
    assert list(raw["places"]) == ["meta:Maryland"]


def test_a_meta_failure_is_translated_for_the_page():
    def boom(act, level, params):
        raise base.AdsError("Meta answered 400: bad token")
    with pytest.raises(gads.GadsError):
        g.meta_raw(META, "region", START, END, fetch=boom)


# ── Google and Meta added ───────────────────────────────────────────────────

def test_a_place_with_the_same_name_on_both_platforms_is_one_row_with_each_platforms_share():
    graw = g.google_raw(GOOGLE, "region", START, END, search_fn=searcher(PLACES, TOTALS), geo_fn=geo_fn)
    mraw = g.meta_raw(META, "region", START, END, fetch=lambda a, l, p: [
        mrow("OUTCOME_SALES", 400, 9000, 300, purchases=8, value=1600, region="Maryland"),
        mrow("OUTCOME_SALES", 100, 2000, 80, purchases=1, value=100, region="State of Texas")])
    both = g.merge_raw(graw, mraw)
    md = both["places"][MD]
    assert md["all"]["cost"] == 3900.0 and md["plat"]["google"]["cost"] == 3500.0 and md["plat"]["meta"]["cost"] == 400.0
    assert {"Brand", "Non Brand", "Meta Sales"} <= set(md["types"])
    assert any(p["label"] == "State of Texas" and list(p["plat"]) == ["meta"] for p in both["places"].values())      # only Meta has it
    assert both["platform"] == "both" and both["totals"]["Meta Sales"]["cost"] == 500.0 and both["totals"]["Brand"]["cost"] == 3200.0


def test_adding_two_reads_changes_neither_of_them_because_both_are_shared_through_the_cache():
    import copy
    graw = g.google_raw(GOOGLE, "region", START, END, search_fn=searcher(PLACES, TOTALS), geo_fn=geo_fn)
    mraw = g.meta_raw(META, "region", START, END, fetch=lambda a, l, p: [mrow("OUTCOME_SALES", 400, 9000, 300, purchases=8, value=1600, region="Maryland"),
                                                                       mrow("OUTCOME_SALES", 90, 900, 30, region="Texas")])
    before = copy.deepcopy((graw, mraw))
    g.merge_raw(graw, mraw)
    assert (graw, mraw) == before


def test_place_names_match_across_platforms_ignoring_case_and_the_word_state():
    assert g._place_norm("State of Maryland") == g._place_norm("maryland") == g._place_norm("Maryland")
    assert g._place_norm("Washington") != g._place_norm("West Virginia")


# ── The report ──────────────────────────────────────────────────────────────

def raw_google():
    return g.google_raw(GOOGLE, "region", START, END, search_fn=searcher(PLACES, TOTALS), geo_fn=geo_fn)


def test_the_report_has_a_table_per_campaign_type_each_with_its_own_average_and_coverage():
    rep = g.assemble(raw_google(), GOOGLE, START, END)
    assert rep["types"] == ["Brand", "Non Brand", "Performance Max"] and list(rep["views"]) == ["All", "Brand", "Non Brand", "Performance Max"]
    assert rep["range"] == {"from": "2026-06-01", "to": "2026-06-30", "days": 30} and rep["level"] == "region"
    assert [l["key"] for l in rep["levels"]] == ["country", "region", "metro", "city", "county", "postal_code"]
    v = rep["views"]["All"]
    assert [r["label"] for r in v["rows"]] == ["Maryland", "District of Columbia", "Virginia"]          # biggest spender first
    assert v["total"]["cost"] == 6000.0 and v["account"]["cost"] == 6000.0 and v["coverage"] == pytest.approx(1.0)
    md = v["rows"][0]
    assert md["cost"] == 3500.0 and md["conversions"] == 50.0 and md["cpa"] == 70.0 and md["roas"] == 2.714 and md["pctCost"] == pytest.approx(3500 / 6000)
    assert md["cvr"] == pytest.approx(50 / 700) and md["ctr"] == pytest.approx(700 / 8000) and md["cpc"] == 5.0
    brand = rep["views"]["Brand"]
    assert [r["label"] for r in brand["rows"]] == ["Maryland", "Virginia"] and brand["total"]["cost"] == 3200.0


def test_each_row_is_judged_and_the_table_carries_insights_and_its_basis():
    v = g.assemble(raw_google(), GOOGLE, START, END)["views"]["All"]
    assert v["basis"] == "roas" and all("verdict" in r and "index" in r and "adjustment" in r for r in v["rows"])
    assert isinstance(v["insights"], list) and v["counts"]


def test_coverage_is_the_located_spend_over_the_accounts_and_is_short_when_places_miss_spend():
    raw = raw_google()
    raw["totals"]["Brand"]["cost"] += 4000.0                                                           # spend that reported no location
    v = g.assemble(raw, GOOGLE, START, END)["views"]["All"]
    assert v["coverage"] == pytest.approx(6000 / 10000) and any(i["kind"] == "coverage" for i in v["insights"])


def test_in_both_each_row_says_what_each_platform_contributed():
    graw = raw_google()
    mraw = g.meta_raw(META, "region", START, END, fetch=lambda a, l, p: [mrow("OUTCOME_SALES", 400, 9000, 300, purchases=8, value=1600, region="Maryland")])
    rep = g.assemble(g.merge_raw(graw, mraw), {"id": "1234567890+act_5550001", "name": "Acme + Acme", "currency": "USD"}, START, END)
    md = rep["views"]["All"]["rows"][0]
    assert md["label"] == "Maryland" and set(md["platforms"]) == {"google", "meta"} and md["platforms"]["meta"]["cost"] == 400.0
    assert "Meta Sales" in rep["types"] and [l["key"] for l in rep["levels"]] == ["country", "region"]


def test_the_account_block_names_the_platform_and_an_mccs_accounts():
    rep = g.assemble(dict(raw_google(), members=["One", "Two"]), GOOGLE, START, END)
    assert rep["account"]["platform"] == "google" and rep["account"]["isGroup"] and rep["account"]["accountCount"] == 2
    assert rep["account"]["accounts"] == ["One", "Two"] and rep["account"]["currency"] == "USD"


# ── The entry point and its cache ───────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    g.clear_cache()
    base.reset_state()
    base.forget("")
    yield saved
    g.clear_cache()
    base.forget("")


def report(search, account="1234567890", level="region", **kw):
    return g.report_for(account, level, "2026-06-01", "2026-06-30", accounts_fn=lambda: [GOOGLE], groups_fn=lambda: [], search_fn=search,
                        geo_fn=geo_fn, **kw)


def test_the_entry_point_builds_a_report_and_a_second_ask_does_not_read_google_again(cache):
    calls = []

    def search(cid, q):
        calls.append(q)
        return PLACES if "user_location_view" in q else TOTALS
    first = report(search)
    n = len(calls)
    second = report(search)
    assert first["views"]["All"]["rows"][0]["label"] == "Maryland" and len(calls) == n and second["views"] == first["views"]
    report(search, level="city")                                                                       # another level is another read
    assert len(calls) > n


def test_a_level_the_platform_cannot_do_is_refused_before_anything_is_read(cache):
    with pytest.raises(g.BadRequest, match="cannot be cut by"):
        report(lambda *a: pytest.fail("no read expected"), level="dma")


def test_a_bad_range_or_a_foreign_account_is_a_bad_request(cache):
    with pytest.raises(g.BadRequest):
        g.report_for("1234567890", "region", "2026-07-01", "2026-06-01", accounts_fn=lambda: [GOOGLE])
    with pytest.raises(g.BadRequest, match="not one of Position2"):
        report(lambda *a: [], account="9999999999")


def test_refresh_reads_again_and_a_second_refresh_straight_after_is_refused(cache):
    calls = []

    def search(cid, q):
        calls.append(q)
        return PLACES if "user_location_view" in q else TOTALS
    report(search)
    n = len(calls)
    cache_get = paid_store.get_gads_cache
    report(search, force=True)
    assert len(calls) > n
    paid_store.get_gads_entry = lambda key, ttl: (cache.get(key), "2026-06-30T00:00:00+00:00") if key in cache else None
    try:
        with pytest.raises(base.RefreshTooSoon):
            report(search, force=True)
    finally:
        del paid_store.get_gads_entry


def test_when_the_quota_is_spent_the_last_saved_read_is_served_with_its_date(cache, monkeypatch):
    def ok(cid, q):
        return PLACES if "user_location_view" in q else TOTALS
    first = report(ok, now=0)
    assert "stale" not in first

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: None)                              # nothing fresh
    monkeypatch.setattr(paid_store, "get_gads_cache_any", lambda key: (cache.get(key), "2026-06-30T10:00:00+00:00") if key in cache else None,
                        raising=False)
    g.clear_cache()
    base.forget("")
    old = report(spent, now=10 ** 9)
    assert old["stale"] == "2026-06-30T10:00:00+00:00" and old["views"]["All"]["rows"][0]["label"] == "Maryland"


def test_with_no_saved_read_a_spent_quota_is_raised_to_the_caller(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError, match="daily limit"):
        report(spent)


def test_a_combined_account_reads_each_half_and_adds_them(cache, monkeypatch):
    monkeypatch.setattr(seg, "_meta_target", lambda act, store: dict(META, id=act))
    rep = g.report_for("1234567890+act_5550001", "region", "2026-06-01", "2026-06-30", accounts_fn=lambda: [GOOGLE], groups_fn=lambda: [],
                       search_fn=searcher(PLACES, TOTALS), geo_fn=geo_fn,
                       meta_fetch=lambda a, l, p: [mrow("OUTCOME_SALES", 400, 9000, 300, purchases=8, value=1600, region="Maryland")])
    assert rep["account"]["platform"] == "both" and rep["account"]["name"] == "Acme Landscaping + Acme Landscaping"
    assert rep["views"]["All"]["rows"][0]["platforms"]["meta"]["cost"] == 400.0


def test_if_one_half_of_a_combined_account_fails_the_other_is_shown_with_a_warning(cache, monkeypatch):
    monkeypatch.setattr(seg, "_meta_target", lambda act, store: dict(META, id=act))

    def boom(act, level, params):
        raise base.AdsError("Meta answered 500: down")
    rep = g.report_for("1234567890+act_5550001", "region", "2026-06-01", "2026-06-30", accounts_fn=lambda: [GOOGLE], groups_fn=lambda: [],
                       search_fn=searcher(PLACES, TOTALS), geo_fn=geo_fn, meta_fetch=boom)
    assert rep["account"]["platform"] == "google" and any("Meta Ads could not be read" in w and "Showing the other platform only" in w for w in rep["warnings"])


def test_a_combined_account_must_be_one_google_and_one_meta_and_the_same_currency(cache, monkeypatch):
    with pytest.raises(g.BadRequest, match="one Google Ads account and one Meta"):
        g.report_for("1234567890+9999999999", "region", "2026-06-01", "2026-06-30")
    monkeypatch.setattr(seg, "_meta_target", lambda act, store: dict(META, id=act, currency="EUR"))
    with pytest.raises(g.BadRequest, match="different currencies"):
        g.report_for("1234567890+act_5550001", "region", "2026-06-01", "2026-06-30", accounts_fn=lambda: [GOOGLE], groups_fn=lambda: [],
                     search_fn=searcher(PLACES, TOTALS), geo_fn=geo_fn, meta_fetch=lambda a, l, p: [])
    with pytest.raises(g.BadRequest, match="cannot be cut by"):
        g.report_for("1234567890+act_5550001", "city", "2026-06-01", "2026-06-30")
