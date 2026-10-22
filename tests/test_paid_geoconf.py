"""Location Conflicts: reading Google and Meta, the report, the cache, what the team saved and the plan (tracker/paid_geoconf*.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_copycheck_settings as copy_st  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_geoconf as g  # noqa: E402
from tracker import paid_geoconf_plan as plan  # noqa: E402
from tracker import paid_geoconf_settings as st  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 22)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}
QUINCY, DOVER = "geoTargetConstants/1014221", "geoTargetConstants/1020000"
NAMES = {QUINCY: ("Quincy", "Quincy,Massachusetts,United States", "City"), DOVER: ("Dover", "Dover,New Hampshire,United States", "City")}


def campaign(i="5", name="Search_Quincy", channel="SEARCH", pos="PRESENCE", neg="PRESENCE_OR_INTEREST"):
    return {"campaign": {"id": i, "name": name, "advertisingChannelType": channel, "geoTargetTypeSetting": {"positiveGeoTargetType": pos, "negativeGeoTargetType": neg}}}


def loc_row(cid, geo, negative=False):
    return {"campaign": {"id": cid}, "campaignCriterion": {"criterionId": "1", "type": "LOCATION", "negative": negative, "location": {"geoTargetConstant": geo}}}


def prox_row(cid, radius=10, units="MILES", city="Quincy"):
    return {"campaign": {"id": cid}, "campaignCriterion": {"criterionId": "2", "type": "PROXIMITY", "negative": False, "proximity": {"radius": radius, "radiusUnits": units, "address": {"cityName": city}}}}


def name_row(rn):
    n, c, t = NAMES[rn]
    return {"geoTargetConstant": {"resourceName": rn, "name": n, "canonicalName": c, "targetType": t}}


def rsa(ad="1", group="10", gname="Quincy_Emergency", camp="5", heads=("Emergency dentist in Quincy",), descs=("Open late",)):
    return {"campaign": {"id": camp, "name": "Search_Quincy"}, "adGroup": {"id": group, "name": gname},
            "adGroupAd": {"ad": {"id": ad, "type": "RESPONSIVE_SEARCH_AD", "finalUrls": ["https://acme.example/a"],
                                 "responsiveSearchAd": {"headlines": [{"text": h} for h in heads], "descriptions": [{"text": d} for d in descs]}}}}


def searcher(camps=None, locs=None, names=None, extra=None, log=None, fail=(), fail_kinds=()):
    camps = camps if camps is not None else {ACCT["id"]: [campaign()]}
    locs = locs if locs is not None else {ACCT["id"]: [loc_row("5", QUINCY)]}
    extra = extra or {}

    def run(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        if "FROM campaign_criterion" in q and "LOCATION" in q:
            return locs.get(cid, [])
        if "FROM geo_target_constant" in q:
            if "names" in fail_kinds:
                raise gads.GadsError("api", "no names")
            return [name_row(rn) for rn in NAMES if "'%s'" % rn in q] if names is None else names
        if "FROM campaign_criterion" in q and "AD_SCHEDULE" in q:
            if "schedule" in fail_kinds:
                raise gads.GadsError("api", "no schedules")
            return extra.get("schedules", [])
        if "FROM campaign_criterion" in q and "DEVICE" in q:
            return extra.get("devices", [])
        if "FROM ad_group_ad" in q:
            if "ads" in fail_kinds:
                raise gads.GadsError("api", "no ads")
            return extra.get("ads", [])
        if "FROM ad_group_criterion" in q:
            return extra.get("keywords", [])
        if "FROM ad_group " in q:
            return extra.get("groups", [])
        if "FROM campaign " in q:
            return camps.get(cid, [])
        raise AssertionError("unexpected query: " + q[:80])
    return run


# ── Parsing Google's rows ───────────────────────────────────────────────────

def test_campaigns_with_their_options_and_without_an_id():
    rows = [campaign("5", "A", "PERFORMANCE_MAX", "PRESENCE_OR_INTEREST", "PRESENCE"), {"campaign": {"name": "no id"}}, {"campaign": {"id": "6"}}]
    assert g.read_campaigns(rows) == [{"id": "5", "name": "A", "channel": "PERFORMANCE_MAX", "positive": "PRESENCE_OR_INTEREST", "negative": "PRESENCE"},
                                      {"id": "6", "name": "", "channel": "", "positive": "", "negative": ""}]


def test_locations_and_radiuses():
    rows = [loc_row("5", QUINCY), loc_row("5", DOVER, negative=True), prox_row("5", 12.5, "KILOMETERS", "Braintree"), prox_row("6"), {"campaign": {"id": "7"}, "campaignCriterion": {"type": "LOCATION"}}]
    got = g.read_locations(rows)
    assert [(x["campaignId"], x["kind"], x["negative"], x["geoName"], x["name"], x["radiusText"] if x["kind"] == "proximity" else "") for x in got] == [
        ("5", "location", False, QUINCY, "", ""), ("5", "location", True, DOVER, "", ""), ("5", "proximity", False, "", "Braintree", "12.5 km"), ("6", "proximity", False, "", "Quincy", "10 miles")]


def test_names_are_applied_and_missing_ones_counted():
    locs = g.read_locations([loc_row("5", QUINCY), loc_row("5", DOVER), prox_row("5")])
    names = g.read_names([name_row(QUINCY), {"geoTargetConstant": {"name": "x"}}])
    assert names == {QUINCY: {"name": "Quincy", "canonical": "Quincy,Massachusetts,United States", "targetType": "City"}}
    out, missing = g.apply_names(locs, names)
    assert missing == 1 and out[0]["name"] == "Quincy" and out[0]["canonical"].startswith("Quincy,") and out[0]["targetType"] == "City" and out[1]["name"] == "" and out[2]["kind"] == "proximity"


def test_a_proximity_row_without_its_type_is_still_a_radius():
    row = {"campaign": {"id": "5"}, "campaignCriterion": {"proximity": {"radius": 5, "radiusUnits": "MILES", "address": {"cityName": "Quincy"}}}}
    assert [(x["kind"], x["radiusText"]) for x in g.read_locations([row])] == [("proximity", "5 miles")]


def test_the_place_names_are_asked_for_in_chunks_of_the_limit(cache, monkeypatch):
    calls = []
    locs = {ACCT["id"]: [loc_row("5", QUINCY), loc_row("5", DOVER), prox_row("5")]}
    report(searcher(locs=locs, log=calls))
    asked = [q for _c, q in calls if "FROM geo_target_constant" in q]
    assert len(asked) == 1 and "IN ('%s', '%s')" % (QUINCY, DOVER) in asked[0]
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.forget("")
    cache.clear()
    calls.clear()
    monkeypatch.setattr(g, "NAME_CHUNK", 1)
    rep = report(searcher(locs=locs, log=calls))
    asked = [q for _c, q in calls if "FROM geo_target_constant" in q]
    assert len(asked) == 2 and [("'%s'" % QUINCY in q, "'%s'" % DOVER in q) for q in asked] == [(True, False), (False, True)] and rep["accounts"][0]["namesRead"] is True


def test_schedules_devices_groups_and_keywords():
    sched = g.read_schedules([{"campaign": {"id": "5"}, "campaignCriterion": {"adSchedule": {"dayOfWeek": "MONDAY", "startHour": 8, "startMinute": "THIRTY", "endHour": 17, "endMinute": "ZERO"}}},
                              {"campaign": {"id": "5"}, "campaignCriterion": {}}])
    assert sched == [{"campaignId": "5", "day": "MONDAY", "start": 8.5, "end": 17.0}]
    dev = g.read_devices([{"campaign": {"id": "5"}, "campaignCriterion": {"device": {"type": "MOBILE"}, "bidModifier": 0}}, {"campaign": {"id": "5"}, "campaignCriterion": {"device": {"type": "TABLET"}}}])
    assert dev == [{"campaignId": "5", "device": "MOBILE", "modifier": 0.0}]
    assert g.read_groups([{"campaign": {"id": "5"}, "adGroup": {"id": "10", "name": "Q"}}, {"campaign": {"id": "5"}, "adGroup": {}}]) == [{"id": "10", "campaignId": "5", "name": "Q"}]
    assert g.read_keywords([{"campaign": {"id": "5"}, "adGroup": {"id": "10", "name": "Q"}, "adGroupCriterion": {"keyword": {"text": "dentist quincy"}}}, {"campaign": {"id": "5"}, "adGroupCriterion": {}}]) == [
        {"campaignId": "5", "adGroupId": "10", "adGroup": "Q", "text": "dentist quincy"}]


def test_meta_ad_sets_become_campaigns_with_locations_and_the_travel_option():
    rows = [{"id": "77", "name": "Quincy adset", "campaign": {"id": "9", "name": "Leads"},
             "targeting": {"geo_locations": {"countries": ["US"], "regions": [{"name": "Massachusetts", "country": "US"}], "cities": [{"name": "Quincy", "region": "Massachusetts", "country": "US", "radius": 10, "distance_unit": "mile"}],
                                             "zips": [{"name": "02169"}], "custom_locations": [{"name": "Clinic", "radius": 3}], "location_types": ["home", "recent", "travel_in"]},
                           "excluded_geo_locations": {"cities": [{"name": "Boston", "region": "Massachusetts", "country": "US"}]}}},
            {"id": "78", "name": "Plain", "targeting": {}}, {"name": "no id"}]
    camps, locs = g.read_meta_adsets(rows)
    assert camps == [{"id": "77", "name": "Quincy adset", "channel": "META", "positive": "TRAVEL_IN", "negative": "", "metaCampaign": "Leads", "metaCampaignId": "9"},
                     {"id": "78", "name": "Plain", "channel": "META", "positive": "PRESENCE", "negative": "", "metaCampaign": "", "metaCampaignId": ""}]
    byname = {(x["name"], x["negative"]): x for x in locs}
    assert byname[("Quincy", False)]["canonical"] == "Quincy,Massachusetts,United States" and byname[("Quincy", False)]["radiusText"] == "10 mile"
    assert byname[("Boston", True)]["targetType"] == "City" and byname[("Massachusetts", False)]["canonical"] == "Massachusetts,United States"
    assert byname[("United States", False)]["targetType"] == "Country" and byname[("02169", False)]["targetType"] == "Postal Code" and byname[("Clinic", False)]["kind"] == "proximity"


# ── The report and its cache ────────────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield saved
    paid_geo.clear_cache()
    base.forget("")


def report(search, **kw):
    return g.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, **kw)


MIXED = {"ads": [rsa(ad="1", gname="Dover_Emergency")], "groups": [{"campaign": {"id": "5"}, "adGroup": {"id": "10", "name": "Dover_Emergency"}}],
         "keywords": [{"campaign": {"id": "5"}, "adGroup": {"id": "10", "name": "Dover_Emergency"}, "adGroupCriterion": {"keyword": {"text": "dentist dover"}}}]}


def test_the_report_names_the_account_and_what_it_found(cache):
    s = searcher(locs={ACCT["id"]: [loc_row("5", QUINCY), loc_row("6", DOVER)]}, extra=MIXED)
    rep = report(s)
    a = rep["accounts"][0]
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and a["id"] == "1234567890" and a["platform"] == "google"
    rules = sorted(f["rule"] for it in a["items"] for f in it["findings"])
    assert rules == ["group_copy", "kw_place", "name_place"] and a["status"] == "fix" and a["namesRead"] is True
    assert a["counts"] == {"high": 1, "medium": 2, "low": 0} and a["plan"]["steps"][0]["id"] == "group_copy"
    assert rep["summary"] == {"counts": {"fix": 1, "check": 0, "clean": 0}, "accounts": 1, "campaigns": 1, "ads": 1, "high": 1, "medium": 2, "low": 0, "findings": 3}
    assert [r["rule"] for r in rep["rules"]] == g.math.ORDER and rep["readAt"] and rep["warnings"] == []


def test_a_second_ask_never_reads_google_again_and_refresh_does(cache):
    calls = []
    s = searcher(log=calls)
    report(s)
    n = len(calls)
    assert n == 8
    report(s)
    assert len(calls) == n
    report(s, force=True)
    assert len(calls) == 2 * n


def test_the_places_of_the_other_accounts_and_what_the_team_saved_apply_without_reading_again(cache):
    calls, mon = [], MemStore()
    s = searcher(extra={"ads": [rsa(ad="1", gname="General", heads=("Dentist in Weymouth",))]}, log=calls)
    assert report(s, mon=mon)["accounts"][0]["findingCount"] == 0
    n = len(calls)
    copy_st.save_account(mon, "9999999999", "Other Co", {"places": "Weymouth, MA"}, "me@x.co")
    rep = report(s, mon=mon)
    assert [f["rule"] for it in rep["accounts"][0]["items"] for f in it["findings"]] == ["copy_place"] and len(calls) == n
    st.save_account(mon, ACCT["id"], "Acme Dental", {"areas": "Weymouth"}, "me@x.co")
    rep = report(s, mon=mon)
    assert rep["accounts"][0]["findingCount"] == 0 and rep["accounts"][0]["saved"] == {"areas": 1, "ignore": 0, "acceptPresence": False}


def test_the_places_saved_for_this_account_in_the_ad_copy_checker_are_its_own_areas(cache):
    mon = MemStore()
    copy_st.save_account(mon, ACCT["id"], "Acme Dental", {"places": "Weymouth, MA"}, "me@x.co")
    s = searcher(extra={"ads": [rsa(ad="1", gname="General", heads=("Dentist in Weymouth",))]})
    rep = report(s, mon=mon)
    assert rep["accounts"][0]["findingCount"] == 0 and rep["accounts"][0]["saved"]["areas"] == 1
    st.save_account(mon, ACCT["id"], "Acme Dental", {"areas": "weymouth, ma"}, "me@x.co")
    assert report(s, mon=mon)["accounts"][0]["saved"]["areas"] == 1               # the same place written the same way twice is one


def test_a_saved_option_changes_the_setting_finding(cache):
    mon = MemStore()
    s = searcher(camps={ACCT["id"]: [campaign(pos="PRESENCE_OR_INTEREST")]})
    f = report(s, mon=mon)["accounts"][0]["items"][0]["findings"]
    assert [(x["rule"], x["level"]) for x in f] == [("setting", "medium")]
    st.save_account(mon, ACCT["id"], "Acme Dental", {"acceptPresence": True}, "me@x.co")
    f = report(s, mon=mon)["accounts"][0]["items"][0]["findings"]
    assert [(x["rule"], x["level"]) for x in f] == [("setting", "low")]


def test_a_check_that_cannot_be_read_is_said_not_passed_as_clean(cache):
    rep = report(searcher(fail_kinds=("schedule", "ads")))
    assert rep["warnings"] == ["The ad schedules could not be read (no schedules), so that check was skipped.", "The Search ads could not be read (no ads), so that check was skipped."]
    assert rep["accounts"][0]["status"] == "clean"


def test_names_that_could_not_be_read_are_said_and_the_report_is_not_called_clean(cache):
    rep = report(searcher(fail_kinds=("names",)))
    a = rep["accounts"][0]
    assert a["namesRead"] is False
    assert rep["warnings"] == ["The names of the places could not be read (no names), so that check was skipped.", "1 of the 1 targeted places have no name, so the place checks cover only the ones that do.",
                               "The names of the places could not be read, so no place was compared with a name."]


def test_an_mcc_report_puts_the_accounts_to_fix_first(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    s = searcher(camps={ACCT["id"]: [campaign()], ACCT2["id"]: [campaign(pos="PRESENCE_OR_INTEREST")]}, locs={ACCT["id"]: [loc_row("5", QUINCY)], ACCT2["id"]: [loc_row("5", QUINCY)]})
    rep = g.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=s, today=TODAY)
    assert [a["name"] for a in rep["accounts"]] == ["Beta Clinic", "Acme Dental"] and rep["account"]["isGroup"] is True and rep["account"]["accountCount"] == 2
    assert rep["summary"]["counts"] == {"fix": 1, "check": 0, "clean": 1}


def test_an_account_that_cannot_be_read_is_named_and_all_failing_is_raised(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    s = searcher(camps={ACCT["id"]: [campaign()]}, locs={ACCT["id"]: [loc_row("5", QUINCY)]}, fail=(ACCT2["id"],))
    rep = g.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=s, today=TODAY)
    assert [a["name"] for a in rep["accounts"]] == ["Acme Dental"] and rep["warnings"] == ["Beta Clinic could not be read: Account 2222222222 cannot be read."]
    other = dict(group, id="mcc-1111111111-USD")
    with pytest.raises(gads.GadsError):
        g.report_for(other["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [other], search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])), today=TODAY)


def test_a_report_for_a_meta_account_reads_the_ad_sets_and_their_ads(cache):
    adsets = [{"id": "77", "name": "Quincy adset", "campaign": {"id": "9", "name": "Dover Leads"}, "targeting": {"geo_locations": {"cities": [{"name": "Quincy", "region": "Massachusetts", "country": "US"}],
                                                                                                                                    "location_types": ["home", "recent", "travel_in"]}}},
              {"id": "78", "name": "Dover adset", "campaign": {"id": "10", "name": "Dover B"}, "targeting": {"geo_locations": {"cities": [{"name": "Dover", "region": "New Hampshire", "country": "US"}]}}}]
    ads = [{"id": "500", "campaign": {"id": "9", "name": "Dover Leads"}, "adset": {"id": "77", "name": "Quincy adset"}, "creative": {"body": "Dentist in Quincy", "title": "Book now"}}]

    def pages(path, params):
        return adsets if path.endswith("/adsets") else ads
    rep = g.report_for("act_1234567", meta_pages_fn=pages, meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR"}, today=TODAY, search_fn=searcher())
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and a["platform"] == "meta" and a["currency"] == "EUR"
    assert sorted(f["rule"] for it in a["items"] for f in it["findings"]) == ["group_copy", "setting"]
    assert [f for it in a["items"] for f in it["findings"] if f["rule"] == "group_copy"][0]["adGroup"] == "Dover Leads"


def test_meta_ads_that_cannot_be_read_are_said(cache):
    def pages(path, params):
        if path.endswith("/adsets"):
            return []
        raise RuntimeError("Meta says no")
    rep = g.report_for("act_1234567", meta_pages_fn=pages, meta_account_fn=lambda act: {"name": "M"}, today=TODAY, search_fn=searcher())
    assert rep["warnings"] == ["The Meta ads could not be read (Meta says no), so the ad text was not checked."]


def test_a_spent_quota_with_nothing_saved_is_raised_and_an_account_that_is_not_position2s_is_refused(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError):
        report(spent)
    with pytest.raises(g.BadRequest, match="not one of Position2"):
        g.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY)


def test_more_than_the_keyword_limit_is_said(cache):
    kws = [{"campaign": {"id": "5"}, "adGroup": {"id": "10", "name": "Q"}, "adGroupCriterion": {"keyword": {"text": "dentist %d" % i}}} for i in range(g.MAX_KEYWORDS)]
    rep = report(searcher(extra={"keywords": kws}))
    assert rep["warnings"] == ["Only the first %d keywords were read." % g.MAX_KEYWORDS]


def test_a_read_that_lost_a_part_is_shown_but_not_saved_for_hours(cache):
    rep = report(searcher(fail_kinds=("schedule", "ads")))
    assert len(rep["warnings"]) == 2                                                    # shown once, from the top of the read
    assert cache == {}                                                                  # the saved copy is for a whole read only: the next ask in a new process reads again
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.forget("")
    report(searcher())
    assert len(cache) == 1                                                              # a whole read is saved


def test_a_meta_read_that_lost_a_part_is_shown_but_not_saved_for_hours(cache):
    def pages(path, params):
        if path.endswith("/adsets"):
            return []
        raise RuntimeError("Meta says no")
    rep = g.report_for("act_1234567", meta_pages_fn=pages, meta_account_fn=lambda act: {"name": "M"}, today=TODAY, search_fn=searcher())
    assert rep["warnings"] == ["The Meta ads could not be read (Meta says no), so the ad text was not checked."]
    assert cache == {}


def test_a_group_names_the_account_a_warning_belongs_to_and_says_it_once(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    s = searcher(camps={ACCT["id"]: [campaign()], ACCT2["id"]: [campaign()]}, locs={ACCT["id"]: [loc_row("5", QUINCY)], ACCT2["id"]: [loc_row("5", QUINCY)]}, fail_kinds=("ads",))
    rep = g.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=s, today=TODAY)
    assert sorted(rep["warnings"]) == ["Acme Dental: The Search ads could not be read (no ads), so that check was skipped.", "Beta Clinic: The Search ads could not be read (no ads), so that check was skipped."]
    assert cache == {}


# ── What the team saved ─────────────────────────────────────────────────────

def test_settings_save_clean_and_log():
    mon = MemStore()
    now = dt.datetime(2026, 10, 22, 9, tzinfo=dt.timezone.utc)
    got = st.save_account(mon, "123", "Acme", {"areas": "Greater Boston;\nSouth Shore, MA", "ignore": ["Orange", "orange"], "acceptPresence": True}, "me@x.co", now)
    assert got == {"areas": ["Greater Boston", "South Shore, MA"], "ignore": ["Orange"], "acceptPresence": True, "name": "Acme"}
    s = st.load(mon)
    assert s["changes"] == [{"at": "2026-10-22T09:00:00+00:00", "by": "me@x.co", "what": "saved the places of account 123"}]
    assert st.view(mon)["accounts"]["123"]["areas"][0] == "Greater Boston"
    st.save_account(mon, "123", "Acme", {}, "me@x.co", now)
    assert st.load(mon)["accounts"] == {} and len(st.load(mon)["changes"]) == 2


def test_settings_refuse_bad_input():
    mon = MemStore()
    for bad in ({"areas": "x"}, {"areas": "12345"}, {"ignore": "<b>"}):
        with pytest.raises(st.BadRequest):
            st.save_account(mon, "123", "A", bad, "me")
    with pytest.raises(st.BadRequest, match="Pick one account"):
        st.save_account(mon, "mcc-1", "A", {}, "me")
    with pytest.raises(st.BadRequest, match="At most 50"):
        st.save_account(mon, "123", "A", {"areas": ["Place %s" % ("a" * i) for i in range(1, 52)]}, "me")
    assert st.save_account(mon, "act_1234567", "Meta", {"ignore": "Reading"}, "me")["ignore"] == ["Reading"]


def test_ignore_one_word_adds_without_duplicates():
    mon = MemStore()
    st.add_ignore(mon, "123", "Orange", "me")
    got = st.add_ignore(mon, "123", ["orange", "Reading"], "me")
    assert got["ignore"] == ["Orange", "Reading"]
    with pytest.raises(st.BadRequest, match="Give at least one word"):
        st.add_ignore(mon, "123", "", "me")


def test_settings_in_a_broken_store_default_to_empty():
    assert st.account_of(st.empty(), "1") == {"areas": [], "ignore": [], "acceptPresence": False, "name": ""}


# ── The plan ────────────────────────────────────────────────────────────────

def test_the_plan_has_a_step_per_rule_worst_first_and_a_recheck(cache):
    s = searcher(locs={ACCT["id"]: [loc_row("5", QUINCY), loc_row("6", DOVER)]}, extra=MIXED)
    steps = report(s)["accounts"][0]["plan"]["steps"]
    assert [x["id"] for x in steps] == ["group_copy", "name_place", "kw_place", "recheck"]
    first = steps[0]
    assert first["title"] == "Make the ads say the place of their ad group in 1 campaign" and first["level"] == "high" and first["where"] == ["Search_Quincy"] and first["campaigns"] == 1
    assert first["examples"] == ["The ad group is called Dover_Emergency and the ad names Quincy."] and steps[-1]["level"] == "info"


def test_a_step_names_three_campaigns_and_three_examples_at_most():
    def item(i):
        return {"name": "C%d" % i, "findings": [{"rule": "copy_place", "detail": "detail %d" % i}]}
    a = {"byRule": [{"rule": "copy_place", "level": "medium", "findings": 4, "fix": "Do this."}], "items": [item(i) for i in range(4)]}
    s = plan.build(a)["steps"][0]
    assert s["where"] == ["C0", "C1", "C2"] and s["examples"] == ["detail 0", "detail 1", "detail 2"] and s["campaigns"] == 4 and s["title"].endswith("in 4 campaigns") and s["how"] == "Do this."


def test_only_the_last_thirty_changes_are_kept():
    mon = MemStore()
    for i in range(32):
        st.save_account(mon, "123", "A", {"areas": "Place %s" % ("a" * (i + 1))}, "me")
    assert len(st.load(mon)["changes"]) == 30


def test_a_clean_account_has_no_steps():
    assert plan.build({"byRule": [], "items": []}) == {"steps": []}
